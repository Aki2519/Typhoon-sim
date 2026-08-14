# simulator/env/generator.py
"""F6 Analog 生成器(核心): 季节强约束候选池 + 模态马氏距离 +
轨迹记忆 + 片段复用 + 降级链 + 主/次月混合 + 逐日插值。

输出: 逐月场序列(合成/真实库 analog 选择)→ get_field 日插值。
"""
from __future__ import annotations
import json
import os
import numpy as np
from typing import Dict, List, Optional, Tuple

from . import field_io as F
from . import build_library as BL
from simulator import modes as M

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ENV_DIR, 'logs')

# 季节约束: 候选池 = 目标月 ± 窗口
SEASON_WIN = 1          # 初始 ±1 月
SEASON_WIN_2 = 2        # 降级 b
SEASON_WIN_3 = 3        # 降级 c(全季)
TRACK_MEMORY_WIN = 3    # 轨迹记忆: 上月所选月的时间邻居 ±3 月
MAIN_WEIGHT = 0.85      # 主月权重
MAX_DEGRADE = 4         # 降级链最大步数

# F10 渲染场集成: 生成序列除 VARS 外也携带 RENDER_VARS(逐月/逐日查询一致),
# 否则架空(虚构)台风年份的 D01/D02 等 F10 渲染层永远无 u10/v10/precip24/…。
# RENDER_VARS 不参与 analog 选源(仅 VARS), 但被选中主/次月的对应场按同权混合落盘。
GEN_VARS: Tuple[str, ...] = F.VARS + F.RENDER_VARS

# 模态匹配向量(标准化): 统一向量(敏感度分析后再拆)
MATCH_MODES = ('oni', 'soi', 'pdo', 'amo', 'dmi')
LAG = 3                 # 响应滞后: 目标月 m 用 [m, m-1, m-2]


def _mode_vec(ym: Tuple[int, int]) -> np.ndarray:
    """目标月模态向量(标准化,含滞后窗)。缺测用 0(标准化后中性)。
    C3: 滞后按实际差值回退((1,2)->(y-1,11),(1,3)->(y-1,10))。"""
    vec = []
    for lag in range(LAG):
        y, mo = ym
        mm = mo - lag
        if mm <= 0:
            mm += 12
            y -= 1
        d = _mode_values((y, mm))
        vec += [d.get(k, 0.0) for k in MATCH_MODES]
    return np.array(vec, dtype=float)


def _mode_values(ym: Tuple[int, int]) -> Dict[str, float]:
    out = {}
    for k, path in (('oni', 'nino34'), ('soi', 'soi'), ('pdo', 'pdo'),
                    ('amo', 'amo'), ('dmi', 'dmi')):
        rows = M.load_mode_series(path)
        d = {(y, mo): v for y, mo, v in rows}
        v = d.get(ym)
        if v is not None:
            out[k] = v / M.MODES[path]['std']
    return out


class AnalogGenerator:
    def __init__(self, lib_dir: str = F.LIB_DIR, years: range = BL.LIB_YEARS,
                 seed: int = 1, generated_dir: Optional[str] = None):
        self.lib_dir = lib_dir
        self.years = years
        self.generated_dir = generated_dir or os.path.join(ENV_DIR, 'generated')
        self.rng = np.random.RandomState(seed % (2**32))
        self.log: List[dict] = []
        # 预计算每模态向量(库内月)
        self.pool: List[Tuple[int, int]] = []
        self.pool_vec: Dict[Tuple[int, int], np.ndarray] = {}
        self._index()

    def _index(self):
        """建候选池: 库内存在 sst 场的年月。"""
        for y in self.years:
            if F.load_monthly_field('sst', y, self.lib_dir) is None:
                continue
            for mo in range(1, 13):
                self.pool.append((y, mo))
                self.pool_vec[(y, mo)] = _mode_vec((y, mo))
        if not self.pool:
            raise RuntimeError(
                '环境场库为空: 请先运行 build_library 生成场库'
                f'(lib_dir={self.lib_dir})')
        vecs = np.stack([self.pool_vec[k] for k in self.pool])
        self._pool_mean = vecs.mean(axis=0)
        cov = np.cov(vecs.T)
        # G8: 岭按特征值尺度(0.01×λmax)而非固定 1e-6, 防止近奇异矩阵求逆
        # 被噪声方向主导;特征值极小时用伪逆
        lam = np.linalg.eigvalsh(cov)
        lam_max = float(lam[-1]) if len(lam) else 1.0
        self._pool_cov = cov + np.eye(cov.shape[0]) * max(1e-9, 0.01 * lam_max)
        try:
            self._pool_cov_inv = np.linalg.inv(self._pool_cov)
        except np.linalg.LinAlgError:
            self._pool_cov_inv = np.linalg.pinv(self._pool_cov)

    def _season_candidates(self, ym: Tuple[int, int], win: int) -> List[Tuple[int, int]]:
        y, mo = ym
        out = []
        for (py, pm) in self.pool:
            # G2: 环形月份距离 d = min(|pm-mo|, 12-|pm-mo|) <= win
            d = abs(pm - mo) % 12
            d = min(d, 12 - d)
            if d <= win:
                out.append((py, pm))
        return out

    def _mahalanobis(self, vec: np.ndarray, cand: Tuple[int, int]) -> float:
        d = vec - self.pool_vec[cand]
        return float(np.sqrt(d @ self._pool_cov_inv @ d))

    def _pick_analog(self, ym: Tuple[int, int], exclude: Optional[set] = None,
                     prefer: Optional[List[Tuple[int, int]]] = None) -> Optional[Tuple[int, int]]:
        """在季节候选池内按模态距离选主月(可优先轨迹记忆邻居)。"""
        candidates = self._season_candidates(ym, SEASON_WIN)
        if exclude:
            candidates = [c for c in candidates if c not in exclude]
        vec = _mode_vec(ym)
        if prefer:
            pre = [c for c in prefer if c in set(candidates) and
                   (exclude is None or c not in exclude)]
            if pre:
                best = min(pre, key=lambda c: self._mahalanobis(vec, c))
                return best
        if not candidates:
            return None
        return min(candidates, key=lambda c: self._mahalanobis(vec, c))

    def _degrade_chain(self, ym: Tuple[int, int],
                       exclude: Optional[set]) -> Optional[Tuple[int, int]]:
        """降级链: 同月±1 → ±2 → 全季 → 全局 top-K。"""
        vec = _mode_vec(ym)
        for win, tag in ((SEASON_WIN, 'a±1'), (SEASON_WIN_2, 'b±2'),
                         (SEASON_WIN_3, 'c全季')):
            cands = self._season_candidates(ym, win)
            if exclude:
                cands = [c for c in cands if c not in exclude]
            if cands:
                best = min(cands, key=lambda c: self._mahalanobis(vec, c))
                self._log_match(ym, best, len(cands), f"降级{tag}")
                return best
        # d. 全局 top-K
        if self.pool:
            cands = [c for c in self.pool if exclude is None or c not in exclude]
            if cands:
                best = min(cands, key=lambda c: self._mahalanobis(vec, c))
                self._log_match(ym, best, len(cands), '降级d全局')
                return best
        return None

    def _log_match(self, ym, picked, pool_size, note=''):
        self.log.append({'target': ym, 'picked': picked,
                         'pool': pool_size, 'note': note})

    def _fragment_search(self, target_ym: Tuple[int, int], future_months: int) -> Optional[list]:
        """片段复用: 历史模态序列滑窗搜索最相似 3-12 月轨迹。
        C2: 候选段首月必须与目标段首月同月 ±1(段内逐月季节对齐)。"""
        if future_months < 3:
            return None
        # G3: hist 只取候选池内月份(有 sst 场),避免选中无场月产生 NaN
        hist = []
        for (y, mo) in self.pool:
            v = _mode_vec((y, mo))     # 15 维(5 模态×3 滞后),与目标一致
            hist.append(((y, mo), v))
        if len(hist) < 12:
            return None
        target = [_mode_vec(((target_ym[0] + (target_ym[1] + i - 1) // 12),
                             (target_ym[1] + i - 1) % 12 + 1))
                  for i in range(future_months)]
        best_seg = None
        best_d = float('inf')
        t_months = [(target_ym[1] + i - 1) % 12 + 1 for i in range(future_months)]
        # G4: range 上界 +1, 使最后一段(段首 len(hist)-future_months)也参与匹配
        for start in range(len(hist) - future_months + 1):
            seg_months = [m[1] for m, _ in hist[start:start + future_months]]
            # 季节约束: 段内各月与目标月逐一满足 ±2
            if any(abs((a - b + 12) % 12) > 2 and abs((b - a + 12) % 12) > 2
                   for a, b in zip(seg_months, t_months)):
                continue
            seg = [v for _, v in hist[start:start + future_months]]
            d = sum(float(np.linalg.norm(a - b)) for a, b in zip(target, seg))
            if d < best_d:
                best_d = d
                best_seg = [m for m, _ in hist[start:start + future_months]]
        if best_seg and best_d / future_months < 2.5:
            return best_seg
        return None

    # ── 主生成 ──

    def generate_monthly(self, start_ym: Tuple[int, int],
                         n_months: int, use_fragment: bool = True,
                         save: bool = True
                         ) -> Dict[str, Dict[int, np.ndarray]]:
        """生成逐月场序列。返回 {var: {月份序号: 场}}。月份序号从 0 起。
        G5: save=False 时不落盘(验证/平滑度检查用,避免污染 generated_dir)。"""
        out_vars = {v: {} for v in GEN_VARS}
        month_ym = []
        y, mo = start_ym
        for i in range(n_months):
            month_ym.append((y, mo))
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        if save:
            # G6 修正: 清理窗口外年份的旧月份残留(不同种子/窗口的 npz 会混入)
            win_years = {ym[0] for ym in month_ym}
            if os.path.isdir(self.generated_dir):
                for fn in os.listdir(self.generated_dir):
                    if not fn.endswith('.npz'):
                        continue
                    try:
                        # 变量名可能含下划线(uv_steer),年份取最后一段
                        fyear = int(fn.rsplit('_', 1)[1].split('.')[0])
                    except (IndexError, ValueError):
                        continue
                    if fyear not in win_years:
                        try:
                            os.remove(os.path.join(self.generated_dir, fn))
                        except OSError:
                            pass

        picked_seq: List[Optional[Tuple[int, int]]] = []
        excluded = set()

        # 片段复用优先
        if use_fragment:
            seg = self._fragment_search(start_ym, n_months)
            if seg:
                picked_seq = seg
                self._log_match(start_ym, seg[0], 0, f'片段复用 {len(seg)} 月')
                for k, ym in enumerate(month_ym):
                    self._blend_month(out_vars, ym, seg[k], k)
                if save:
                    self._save(month_ym, out_vars)
                return out_vars

        # 轨迹记忆逐月
        prev_picked = None
        for ym in month_ym:
            prefer = None
            if prev_picked is not None:
                py, pm = prev_picked
                prefer = [(yy, mm) for (yy, mm) in self.pool
                          if abs(mm - pm) <= TRACK_MEMORY_WIN and
                          abs(mm - ym[1]) <= SEASON_WIN]
            picked = self._pick_analog(ym, exclude=excluded, prefer=prefer)
            if picked is None:
                picked = self._degrade_chain(ym, excluded)
            if picked is None:
                # e. 气候态+合成扰动
                picked = None
                self._log_match(ym, None, 0, '降级e气候态')
                self._blend_clim(ym, out_vars, month_ym.index(ym))
                prev_picked = None
                picked_seq.append(None)
                continue
            self._log_match(ym, picked, len(self._season_candidates(ym, SEASON_WIN)))
            self._blend_month(out_vars, ym, picked, month_ym.index(ym))
            # R4-02: 已选中的历史月加入排除集,避免逐月连续复用同一源月
            excluded.add(picked)
            prev_picked = picked
            picked_seq.append(picked)
        if save:
            self._save(month_ym, out_vars)
        return out_vars

    def _blend_month(self, out_vars, ym, picked, idx: int):
        """主月 85% + 次相似月 15% 加权混合(全部变量,C6)。"""
        vec = _mode_vec(ym)
        cands = [c for c in self._season_candidates(ym, SEASON_WIN) if c != picked]
        sec = min(cands, key=lambda c: self._mahalanobis(vec, c)) if cands else None
        for var in GEN_VARS:
            a = F.load_monthly_field(var, picked[0], self.lib_dir)
            if a is None:
                continue
            field = a[picked[1] - 1]
            if sec is not None:
                b = F.load_monthly_field(var, sec[0], self.lib_dir)
                if b is not None:
                    field = MAIN_WEIGHT * field + (1 - MAIN_WEIGHT) * b[sec[1] - 1]
            out_vars[var][idx] = field

    def _blend_clim(self, ym, out_vars, idx):
        """降级 e: 气候态 + 合成扰动。"""
        for var in GEN_VARS:
            cl = F.load_climatology(var)
            if cl is None or cl['clim'] is None:
                continue
            clim = cl['clim'][ym[1] - 1]
            std = cl['std'][ym[1] - 1] if cl['std'] is not None else 0.0
            noise = self.rng.normal(0, 1.0, clim.shape) * np.nan_to_num(std, nan=0.0)
            out_vars[var][idx] = clim + noise

    def _save(self, month_ym, out_vars):
        """生成逐月场落盘到 generated_dir(按月序号→(年,月)归档为 12 月 npz)。
        G6: 部分月份生成时保留同文件既有月份(不 NaN 覆盖)。"""
        by_year: Dict[int, Dict[int, int]] = {}
        for idx, ym in enumerate(month_ym):
            by_year.setdefault(ym[0], {})[ym[1] - 1] = idx
        os.makedirs(self.generated_dir, exist_ok=True)
        for y, months in by_year.items():
            for var in GEN_VARS:
                if var not in out_vars:
                    continue
                existing = F.load_monthly_field(var, y, self.generated_dir)
                if var in F._4D_VARS:
                    # 双分量(uv_steer/uv200): (12, 2, NLAT, NLON)
                    if existing is not None:
                        arr4 = existing.copy()
                    else:
                        arr4 = np.full((12, 2, F.NLAT, F.NLON), np.nan, dtype=np.float32)
                    for mi, idx in months.items():
                        f = out_vars[var].get(idx)
                        if f is not None:
                            arr4[mi] = f
                    F.save_monthly_field(var, y, arr4, lib_dir=self.generated_dir)
                    continue
                if existing is not None:
                    arr = existing.copy()
                else:
                    arr = np.full((12, F.NLAT, F.NLON), np.nan, dtype=np.float32)
                for mi, idx in months.items():
                    f = out_vars[var].get(idx)
                    if f is not None:
                        arr[mi] = f
                F.save_monthly_field(var, y, arr, lib_dir=self.generated_dir)

    def save_log(self, path: Optional[str] = None) -> str:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = path or os.path.join(LOG_DIR, 'analog_log.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.log, f, ensure_ascii=False, indent=1)
        return path


def build_daily_field(api: F.FieldAPI, var: str, date, months: np.ndarray) -> Optional[np.ndarray]:
    """由逐月场序列插值得到逐日场(月内线性)。months: (12, lat, lon)。"""
    y, mo, d = F._split_date(date)
    return F.interpolate_daily(months, y, mo, d)
