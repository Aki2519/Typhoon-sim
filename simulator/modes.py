# simulator/modes.py
"""F4 气候模态层: 模态定义表、实测数据归档(离线可用)、两段式序列生成、
组合合理性评分、输出与校验。

模态集合: ENSO(Nino3.4 ONI)、SOI、PDO、AMO、IOD(DMI);可选 NAO、QBO。
数据源: NOAA PSL/CPC 公开接口,抓取一次归档为本地 CSV;失败降级为内置占位。
"""
from __future__ import annotations
import json
import math
import os
import random
import re
import csv
import time
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'mode_data')     # 实测归档 CSV
OUT_DIR = os.path.join(BASE_DIR, 'output')         # 生成结果

# ════════════════════ 模态定义表(纯数据驱动,新增模态=新增一行)════════════════════

# 每项: 名称/单位/气候态/月std/AR系数(按月,None=用整体)/联合约束/优先级/数据源URL
# ar 为 (ar1, ar2) 或 None; spring_barrier: ENSO 春季可预测性障碍的月份集合(自相关衰减系数)
MODES: Dict[str, dict] = {
    'nino34': {
        'name': 'ENSO (Nino3.4)',
        'unit': '°C',
        'clim': 0.0, 'std': 0.8,
        'ar': (0.90, 0.0),
        'spring_barrier': {3, 4, 5},     # 春季自相关衰减
        'hard': 'nino_soi_opposite',
        'priority': 0,
        'spectrum': ((1 / 54, 0.9), (1 / 36, 1.0), (1 / 24, 0.6)),  # (频率1/年, 幅度)
        'url': 'https://psl.noaa.gov/data/correlation/nina34.anom.data',
    },
    'soi': {
        'name': 'SOI',
        'unit': 'hPa',
        'clim': 0.0, 'std': 7.0,
        'ar': (0.70, 0.0),
        'spring_barrier': None,
        'hard': 'nino_soi_opposite',
        'priority': 1,
        'spectrum': ((1 / 54, 6.0), (1 / 36, 7.0), (1 / 24, 4.0)),
        'url': 'https://www.cpc.ncep.noaa.gov/data/indices/soi',
    },
    'pdo': {
        'name': 'PDO',
        'unit': '°C',
        'clim': 0.0, 'std': 1.0,
        'ar': (0.92, 0.0),
        'spring_barrier': None,
        'hard': None,
        'priority': 2,
        'spectrum': ((1 / 240, 1.2), (1 / 120, 0.8), (1 / 60, 0.4)),
        'url': 'https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat',
    },
    'amo': {
        'name': 'AMO',
        'unit': '°C',
        'clim': 0.0, 'std': 0.22,
        'ar': (0.95, 0.0),
        'spring_barrier': None,
        'hard': None,
        'priority': 3,
        'spectrum': ((1 / 600, 0.25), (1 / 300, 0.15), (1 / 150, 0.08)),
        'url': 'https://psl.noaa.gov/data/correlation/amon.us.long.data',
    },
    'dmi': {
        'name': 'IOD (DMI)',
        'unit': '°C',
        'clim': 0.0, 'std': 0.4,
        'ar': (0.60, 0.0),
        'spring_barrier': None,
        'hard': 'dmi_seasonal',
        'priority': 4,
        'spectrum': ((1 / 36, 0.4), (1 / 24, 0.3)),
        'url': 'https://psl.noaa.gov/data/correlation/dmi.data',
    },
}

# 硬约束常量
HARD_MAX_SIGMA = 3.0          # 历史极值 ±3σ
DMI_ACTIVE_MONTHS = {5, 6, 7, 8, 9, 10, 11}   # IOD 发展/消亡季

# 生成段标记
SEG_REAL = 'real'             # 历史实测
SEG_CONT = 'continuation'     # 接续段(从最近实测外推)
SEG_SYN = 'synthetic'         # 架空段(随机合成)

# 组合软评分权重
SCORE_WEIGHTS = {
    'enso_iod': 0.25,      # ENSO-IOD 联合
    'pdo_mod': 0.20,       # PDO 对 ENSO 频率调制
    'amo_mod': 0.10,       # AMO 长期调制
    'dist': 0.30,          # 五元组历史分布距离
    'persist': 0.15,       # 年际持续性
}
SCORE_MIN = 0.35           # 低于此分重抽;最多 MAX_RETRY 次
MAX_RETRY = 8

SEASONS = [(12, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)]
SEASON_NAMES = {0: '冬', 1: '春', 2: '夏', 3: '秋'}


# ════════════════════ 内置占位数据(断网降级,确定性)════════════════════

def _builtin_placeholder(months: List[Tuple[int, int]], mode: str) -> List[float]:
    """确定性合成"实测"序列(标记为占位)。用固定种子保证可复现。
    真实抓取归档存在时优先使用归档。"""
    rng = random.Random(20260807 + sum(ord(c) for c in mode))
    m = MODES[mode]
    n = len(months)
    vals = []
    phase = [rng.uniform(0, 2 * math.pi) for _ in m['spectrum']]
    # 低频漂移
    drift = 0.0
    for i in range(n):
        t = i / 12.0
        v = m['clim']
        for (freq, amp), ph in zip(m['spectrum'], phase):
            v += amp * math.sin(2 * math.pi * freq * t + ph)
        drift += rng.gauss(0, m['std'] * 0.05)
        drift *= 0.97
        v += drift + rng.gauss(0, m['std'] * 0.4)
        vals.append(v)
    return vals


# ════════════════════ 数据归档 ════════════════════

def _fetch_url(url: str, timeout: float = 8.0) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='ignore')
    except Exception:
        return None


def _parse_noaa_text(text: str, mode: str) -> List[Tuple[int, int, float]]:
    """解析 NOAA 常见格式: 前几行注释,之后每行 [年 月 值...](PSL 风格)
    或两列 [年月, 值](CPC 风格,含 -999.9 缺失标记)。
    M2/M3: 跳过首行头(如 ' 1948 2026'),缺失标记统一 |v|>=99 剔除,
    CPC 双段文件在 'STANDARDIZED' 标题处停止。"""
    out = []
    seen_data = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '%', ';', ':')):
            continue
        up = line.upper()
        if up.startswith('STANDARDIZED'):
            # M3: CPC soi 第二段(标准化数据)在已见数据后出现才截断;
            # 文件开头的主标题(如 'Standardized SOI (monthly)')不截断
            if seen_data:
                break
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            year = int(parts[0][:4]) if len(parts[0]) >= 4 else int(parts[0])
        except ValueError:
            continue
        # 尝试 CPC 风格 [年+月(6位), 值]
        if len(parts[0]) == 6 and parts[0].isdigit():
            year, mon = int(parts[0][:4]), int(parts[0][4:])
            if not 1 <= mon <= 12:
                continue
            try:
                v = float(parts[1])
            except ValueError:
                continue
            # M1: 缺失标记统一 |v|>=99(兼容 -999.9/-99.99/+99.99)
            if abs(v) < 99:
                out.append((year, mon, v))
                seen_data = True
            continue
        # PSL 风格: 行首年,后面 1-12 月各值
        # M2: 首行头(如 '1948 2026')只有 2 个 token, 后面不足 12 个数值 → 跳过
        # R2-5: 逐 token 容错(CPC soi 行末 "-4.0-999.9" 粘连), 失败仅丢该 token
        vals: List[float] = []
        for p in parts[1:13]:
            try:
                vals.append(float(p))
            except ValueError:
                # 粘连 token(如 '-4.0-999.9')尝试拆分
                m = re.match(r'^(-?\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$', p)
                if m:
                    vals.append(float(m.group(1)))
                    continue
                break
        if len(vals) < 12:
            continue
        for mon, v in enumerate(vals, 1):
            if abs(v) < 99:
                out.append((year, mon, v))
                seen_data = True
    return out


def archive_mode(mode: str, force: bool = False) -> Optional[str]:
    """抓取一次模态实测数据并归档为 CSV(带时间戳)。返回归档路径或 None。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{mode}.csv")
    if os.path.exists(path) and not force:
        return path
    m = MODES[mode]
    text = _fetch_url(m['url'])
    if not text:
        # M7: 数据源不可达时显式告警(不再静默降级为占位)
        print(f"[modes] 警告: 模态 {mode} 数据源不可达({m['url']}),将使用内置占位序列")
        return None
    rows = _parse_noaa_text(text, mode)
    if not rows:
        print(f"[modes] 警告: 模态 {mode} 数据解析为空({m['url']}),将使用内置占位序列")
        return None
    rows.sort()
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['year', 'month', 'value', 'fetched_at'])
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for y, mo, v in rows:
            w.writerow([y, mo, f"{v:.3f}", ts])
    return path


def archive_all(force: bool = False) -> Dict[str, Optional[str]]:
    result = {}
    for mode in MODES:
        result[mode] = archive_mode(mode, force)
    return result


def load_mode_series(mode: str) -> List[Tuple[int, int, float]]:
    """加载模态实测序列(归档优先,缺失降级为内置占位)。
    返回 [(year, month, value), ...] 升序。"""
    path = os.path.join(DATA_DIR, f"{mode}.csv")
    rows = []
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                rd = csv.reader(f)
                next(rd, None)
                for r in rd:
                    if len(r) >= 3:
                        try:
                            rows.append((int(r[0]), int(r[1]), float(r[2])))
                        except ValueError:
                            continue
        except (OSError, ValueError):
            rows = []
    if not rows:
        months = [(y, m) for y in range(1950, 2026) for m in range(1, 13)]
        vals = _builtin_placeholder(months, mode)
        rows = [(y, m, v) for (y, m), v in zip(months, vals)]
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows


def _fill_months(rows: List[Tuple[int, int, float]]) -> Dict[Tuple[int, int], float]:
    """转 (年,月)->值 字典,并对单月缺失做月内线性插值(不静默填 0)。"""
    d = {}
    for y, mo, v in rows:
        d[(y, mo)] = v
    if not d:
        return d
    keys = sorted(d)
    # 补齐首尾内的连续缺失(线性插值)
    for i in range(len(keys) - 1):
        y0, m0 = keys[i]
        y1, m1 = keys[i + 1]
        gap = (y1 * 12 + m1) - (y0 * 12 + m0)
        if gap > 1 and gap <= 12:
            v0, v1 = d[keys[i]], d[keys[i + 1]]
            for k in range(1, gap):
                t = k / gap
                ym = y0 * 12 + m0 + k
                yy, mm = divmod(ym - 1, 12)
                d[(yy, mm + 1)] = v0 + (v1 - v0) * t
    return d


def monthly_stats(rows: List[Tuple[int, int, float]]) -> Dict[str, Dict[int, float]]:
    """月气候态/月std/一阶自相关(逐月)。"""
    by_month = {m: [] for m in range(1, 13)}
    for y, mo, v in rows:
        by_month[mo].append(v)
    clim = {m: sum(vs) / len(vs) for m, vs in by_month.items() if vs}
    std = {}
    for m, vs in by_month.items():
        if len(vs) < 2:
            std[m] = 1.0
            continue
        c = clim.get(m, 0.0)
        std[m] = math.sqrt(sum((v - c) ** 2 for v in vs) / (len(vs) - 1)) or 1.0
    # 逐月一阶自相关(同一月份相邻年)
    ar = {}
    for m in range(1, 13):
        pairs = sorted((y, v) for y, mo, v in rows if mo == m)
        if len(pairs) < 4:
            ar[m] = 0.5
            continue
        xs = [v for _, v in pairs]
        mx = sum(xs) / len(xs)
        num = sum((xs[i] - mx) * (xs[i + 1] - mx) for i in range(len(xs) - 1))
        den = sum((x - mx) ** 2 for x in xs)
        ar[m] = num / den if den > 0 else 0.5
    return {'clim': clim, 'std': std, 'ar': ar}


# ════════════════════ 序列生成 ════════════════════

class ModeGenerator:
    """两段式模态生成器: 接续段(AR 外推) + 架空段(谱合成+位相转移)。"""

    def __init__(self, seed: Optional[int] = None, cont_years: int = 2):
        self.seed = seed if seed is not None else random.randint(0, 2 ** 31)
        self.rng = random.Random(self.seed)
        self.cont_years = cont_years          # 接续段年数(2-3)
        self.sub_seeds = {}
        self.retry_counts = {}
        self.low_confidence = []

    def _sub_rng(self, mode: str) -> random.Random:
        s = self.rng.randint(0, 2 ** 31)
        self.sub_seeds[mode] = s
        return random.Random(s)

    # ── 历史统计准备 ──

    def _prepare(self):
        self.stats = {}
        self.history = {}
        self.hist_bounds = {}
        for mode in MODES:
            rows = load_mode_series(mode)
            self.history[mode] = rows
            self.stats[mode] = monthly_stats(rows)
            vals = [v for _, _, v in rows]
            if vals:
                pad = HARD_MAX_SIGMA * MODES[mode]['std']
                self.hist_bounds[mode] = (min(vals) - pad, max(vals) + pad)
            else:
                self.hist_bounds[mode] = (-9e9, 9e9)

    def _last_observed(self, mode: str) -> Tuple[Tuple[int, int], float]:
        rows = self.history[mode]
        return (rows[-1][0], rows[-1][1]), rows[-1][2]

    # ── 接续段 ──

    def _extend_continuation(self, mode: str, start_ym: Tuple[int, int],
                             n_months: int, ref: Optional[Dict[Tuple[int, int], float]] = None
                             ) -> List[Tuple[int, int, float]]:
        """按月 AR(1)/AR(2) 外推,残差方差保持;ENSO 春季障碍衰减自相关。
        ref: SOI 模式下传 nino34 同月值(反号联动)。"""
        m = MODES[mode]
        st = self.stats[mode]
        rng = self._sub_rng(mode + ':cont')
        ar1, ar2 = m['ar'] or (0.7, 0.0)
        out = []
        y, mo = start_ym
        prev1 = self._last_observed(mode)[1]
        prev2 = prev1
        for _ in range(n_months):
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            std = st['std'].get(mo, m['std'])
            # 春季障碍: 3-5 月自相关衰减至 ~0.55×
            a1 = ar1 * (0.55 if mo in (m['spring_barrier'] or set()) else 1.0)
            pred = a1 * (prev1 - m['clim']) + ar2 * (prev2 - m['clim']) + m['clim']
            if mode == 'soi' and ref is not None:
                rv = ref.get((y, mo))
                if rv is not None:
                    # SOI 与 Nino3.4 反号联动(按 std 换算,残差噪声取条件方差)
                    pred += -0.7 * rv * (std / 0.8)
            resid_std = std * (0.35 if mode == 'soi' and ref is not None
                               else math.sqrt(max(0.0, 1.0 - a1 * a1 - ar2 * ar2)))
            v = pred + rng.gauss(0, resid_std)
            if mode == 'dmi' and mo not in DMI_ACTIVE_MONTHS:
                v *= 0.25             # IOD 季节锁相: 冬季强制收缩
            out.append((y, mo, v))
            prev2, prev1 = prev1, v
        return out

    # ── 架空段(谱合成 + 位相转移)──

    def _synthesize(self, mode: str, start_ym: Tuple[int, int],
                    n_months: int, ref: Optional[Dict[Tuple[int, int], float]] = None
                    ) -> List[Tuple[int, int, float]]:
        """谱合成 + 马尔可夫位相转移。ref: SOI 反号联动 nino34。"""
        m = MODES[mode]
        st = self.stats[mode]
        rng = self._sub_rng(mode + ':syn')
        phase = [rng.uniform(0, 2 * math.pi) for _ in m['spectrum']]
        # M6: 位相态初值 ±1(初值 0 时 state=-state 恒 0, 位相项失效)
        state = 1 if rng.random() < 0.5 else -1
        out = []
        y, mo = start_ym
        prev = m['clim']
        for i in range(n_months):
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            t = i / 12.0
            base = m['clim']
            for (freq, amp), ph in zip(m['spectrum'], phase):
                base += amp * 0.75 * math.sin(2 * math.pi * freq * t + ph)
            std = st['std'].get(mo, m['std'])
            noise = rng.gauss(0, std * 0.5)
            # 位相转移: 马尔可夫(转移概率 ~0.04/月,即平均 2 年一次)
            if rng.random() < 0.04:
                state = -state
            v = base + state * std * 0.30 + noise
            if mode == 'soi' and ref is not None:
                rv = ref.get((y, mo))
                if rv is not None:
                    # SOI 与 Nino3.4 反号联动(按 std 换算,残差噪声取条件方差)
                    v = base + state * std * 0.30 + noise - 0.7 * rv * (std / 0.8)
            if mode == 'dmi' and mo not in DMI_ACTIVE_MONTHS:
                v *= 0.25             # IOD 季节锁相: 冬季强制收缩
            v = 0.85 * prev + 0.15 * v   # 均值回归平滑
            out.append((y, mo, v))
            prev = v
        return out

    # ── 硬约束 ──

    def _hard_ok(self, series: Dict[str, List[Tuple[int, int, float]]]) -> bool:
        """硬约束: 超出历史 ±3σ / Nino3.4-SOI 反号 / IOD 非季。"""
        for mode, rows in series.items():
            lo, hi = self.hist_bounds[mode]
            for y, mo, v, _seg in rows:
                if v < lo or v > hi:      # 超出历史极值 ± 3σ 范围
                    return False
        nino = {(y, mo): v for y, mo, v, _g in series['nino34']}
        soi = {(y, mo): v for y, mo, v, _g in series['soi']}
        for ym, v in nino.items():
            if abs(v) <= 0.8:
                continue                  # 仅显著 ENSO 期检查反号
            s = soi.get(ym)
            if s is not None and v * s > 0:
                return False              # 同号即违反(以 Nino3.4 符号为准,SOI 反号)
        dmi = {(y, mo): v for y, mo, v, _g in series['dmi']}
        # IOD 季节锁相: 非季(12-4 月)显著非零且持续 2 月以上才拒绝
        off_streak = 0
        for (y, mo), v in sorted(dmi.items()):
            if mo not in DMI_ACTIVE_MONTHS and abs(v) > 0.5:
                off_streak += 1
                if off_streak >= 2:
                    return False
            else:
                off_streak = 0
        return True

    # ── 软评分 ──

    def _score(self, series: Dict[str, List[Tuple[int, int, float]]]) -> float:
        """加权软评分(0-1),越高越好。"""
        by = {mode: {(y, mo): v for y, mo, v, _g in rows} for mode, rows in series.items()}
        nino = by['nino34']
        dmi = by['dmi']
        soi = by['soi']
        pdo = by['pdo']
        # a) ENSO-IOD 联合: 暖事件时正 IOD 概率高 → 偏差扣分
        enso_iod = 0.0
        n = 0
        for ym, v in nino.items():
            d = dmi.get(ym)
            if d is None or abs(v) < 0.3:
                continue
            n += 1
            want = 0.35 if v > 0 else -0.25
            enso_iod += max(0.0, 1.0 - abs(d - want) / 1.2)
        enso_iod = enso_iod / n if n else 0.5
        # b) PDO 暖位相时 El Niño 占比升高
        pdo_val = sum(pdo.values()) / max(1, len(pdo))
        nino_pos = sum(1 for v in nino.values() if v > 0.3) / max(1, len(nino))
        want_frac = 0.55 if pdo_val > 0.15 else (0.35 if pdo_val < -0.15 else 0.45)
        pdo_mod = max(0.0, 1.0 - abs(nino_pos - want_frac) * 4)
        # c) AMO 长期调制(弱)
        amo = by['amo']
        amo_val = sum(amo.values()) / max(1, len(amo))
        amo_mod = 0.5 + 0.3 * math.tanh(amo_val / 0.15)
        # d) 五元组历史分布距离(马氏近似: 标准化后欧氏距离)
        dist_score = self._hist_dist(by)
        # e) 年际持续性: 同模态相邻年变化量 > 历史 95 分位扣分
        persist = self._persist_score(by)
        return (SCORE_WEIGHTS['enso_iod'] * enso_iod
                + SCORE_WEIGHTS['pdo_mod'] * pdo_mod
                + SCORE_WEIGHTS['amo_mod'] * amo_mod
                + SCORE_WEIGHTS['dist'] * dist_score
                + SCORE_WEIGHTS['persist'] * persist)

    def _hist_dist(self, by: Dict[str, Dict[Tuple[int, int], float]]) -> float:
        """五元组标准化欧氏距离 → 相似度分数。"""
        # 使用历史各模态全序列做参照(简化)
        ref = []
        for mode in MODES:
            vals = [v for _, _, v in load_mode_series(mode)]
            ref.append(vals)
        score = 0.0
        for i, (ym, v) in enumerate(sorted(by['nino34'].items())):
            d2 = 0.0
            for j, mode in enumerate(MODES):
                rv = by[mode].get(ym)
                if rv is None:
                    continue
                vals = ref[j]
                mu = sum(vals) / len(vals)
                sd = math.sqrt(sum((x - mu) ** 2 for x in vals) / len(vals)) or 1.0
                d2 += ((rv - mu) / sd) ** 2
            score += math.exp(-d2 / 20.0)
        return score / max(1, len(by['nino34']))

    def _persist_score(self, by: Dict[str, Dict[Tuple[int, int], float]]) -> float:
        # M4: 年际持续性 = 同一月份相邻年变化量(原实现对连续月比较恒不命中,恒 0.5)
        n = 0
        total = 0.0
        for mode in MODES:
            items = sorted(by[mode].items())
            for i in range(12, len(items)):
                (ym0, v0), (ym1, v1) = items[i - 12], items[i]
                if ym1[1] == ym0[1] and ym1[0] == ym0[0] + 1:
                    dv = abs(v1 - v0)
                    std = MODES[mode]['std']
                    total += max(0.0, 1.0 - dv / (2.5 * std))
                    n += 1
        return total / n if n else 0.5

    # ── 主入口 ──

    def _advance_month(self, ym: Tuple[int, int]) -> Tuple[int, int]:
        y, mo = ym
        return (y + 1, 1) if mo == 12 else (y, mo + 1)

    def _mode_window(self, mode: str, ws: int, we: int, n_cont: int,
                     ref: Optional[Dict[Tuple[int, int], float]] = None
                     ) -> List[Tuple[int, int, float, str]]:
        """生成单模态覆盖 [ws, we) 月序窗口的完整序列。
        各模态从自身最后观测月锚点外推: 前 n_cont 个月标接续, 其余标架空;
        早于窗口起点的月份直接跳过, 保证所有模态窗口完全对齐。"""
        anchor = self._last_observed(mode)[0]
        a_idx = anchor[0] * 12 + anchor[1]
        need = we - a_idx                    # 锚点之后需要的总月数
        n_c = min(n_cont, need)              # 接续段月数
        out = []
        last = anchor
        if n_c > 0:
            for y, mo, v in self._extend_continuation(mode, anchor, n_c, ref=ref):
                if ws <= y * 12 + mo < we:
                    out.append((y, mo, v, SEG_CONT))
                last = (y, mo)
        n_s = need - n_c
        if n_s > 0:
            syn = self._synthesize(mode, last, n_s, ref=ref)
            for y, mo, v in syn:
                if ws <= y * 12 + mo < we:
                    out.append((y, mo, v, SEG_SYN))
        return out

    def generate(self, start_year: Optional[int] = None,
                 total_years: int = 5) -> Dict[str, list]:
        """生成模态序列。返回 {mode: [(year, month, value, seg), ...]}。
        所有模态覆盖同一窗口 [最新实测月+1, +total_years), 逐月对齐。"""
        self._prepare()
        if start_year is not None:
            ws = start_year * 12 + 1             # 显式起点(年份首月)
        else:
            ws = max(self._last_observed(m)[0][0] * 12 + self._last_observed(m)[0][1]
                     for m in MODES) + 1         # 最新实测月之后
        we = ws + total_years * 12
        n_cont = self.cont_years * 12
        # M5 修正: 各模态以"自身最后观测月"为锚点逐月外推,
        # 避免归档末月非 12 月时 AR 跨长间隙且序列出现缺口(如止 3 月却从 12 月锚点起)
        best = None
        best_score = -1.0
        for attempt in range(MAX_RETRY):
            series = {}
            # 先生成 nino34(SOI 需反号联动)
            nino_rows = self._mode_window('nino34', ws, we, n_cont)
            nino_map = {(y, mo): v for y, mo, v, _s in nino_rows}
            series['nino34'] = nino_rows
            for mode in MODES:
                if mode == 'nino34':
                    continue
                series[mode] = self._mode_window(
                    mode, ws, we, n_cont,
                    ref=nino_map if mode == 'soi' else None)
            if not self._hard_ok(series):
                self.retry_counts[attempt] = 'hard'
                continue
            s = self._score(series)
            self.retry_counts[attempt] = f"{s:.3f}"
            if s > best_score:
                best_score = s
                best = series
            if s >= SCORE_MIN:
                break
        if best is None:
            best = series
            self.low_confidence.append('all attempts failed hard constraints')
        elif best_score < SCORE_MIN:
            self.low_confidence.append(f"best score {best_score:.3f} < {SCORE_MIN}")
        self.best_score = best_score
        return best


# ════════════════════ 输出 ════════════════════

def summarize(series: Dict[str, list]) -> List[dict]:
    """每年每季模态状态摘要(中文短句)。"""
    years = sorted({y for rows in series.values() for y, _, _, _ in rows})
    out = []
    for y in years:
        for si, months in enumerate(SEASONS):
            ym_list = [((y - 1 if si == 0 and m >= 12 else y), m)
                       for m in months]
            parts = []
            for mode in ('nino34', 'dmi', 'pdo'):
                by = {(yy, mm): v for yy, mm, v, _ in series[mode]}
                vals = [by.get(ym) for ym in ym_list if by.get(ym) is not None]
                if not vals:
                    continue
                v = sum(vals) / len(vals)
                if mode == 'nino34':
                    if v > 0.5:
                        parts.append('强 El Niño')
                    elif v > 0.3:
                        parts.append('中等 El Niño')
                    elif v < -0.5:
                        parts.append('强 La Niña')
                    elif v < -0.3:
                        parts.append('中等 La Niña')
                elif mode == 'dmi':
                    if v > 0.2:
                        parts.append('正 IOD')
                    elif v < -0.2:
                        parts.append('负 IOD')
                elif mode == 'pdo':
                    if v > 0.15:
                        parts.append('PDO 暖位相')
                    elif v < -0.15:
                        parts.append('PDO 冷位相')
            label = f"{y} {SEASON_NAMES[si]}: "
            if parts:
                # 冬季跨年: 归入下一年标签
                out.append({'year': y if si != 0 else y,
                            'season': SEASON_NAMES[si],
                            'text': label + ' + '.join(parts)})
            else:
                out.append({'year': y, 'season': SEASON_NAMES[si],
                            'text': label + '中性'})
    return out


def save_output(series: Dict[str, list], seed: int, sub_seeds: dict,
                retry_counts: dict, low_confidence: list,
                out_dir: Optional[str] = None) -> Tuple[str, str]:
    os.makedirs(out_dir or OUT_DIR, exist_ok=True)
    base = os.path.join(out_dir or OUT_DIR, 'modes_series.json')
    doc = {
        'seed': seed,
        'sub_seeds': sub_seeds,
        'retry_counts': retry_counts,
        'low_confidence': low_confidence,
        'modes': {
            mode: [[y, m, round(v, 3), seg] for y, m, v, seg in rows]
            for mode, rows in series.items()
        },
    }
    with open(base, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    summ = summarize(series)
    spath = os.path.join(out_dir or OUT_DIR, 'modes_summary.json')
    with open(spath, 'w', encoding='utf-8') as f:
        json.dump({'seed': seed, 'summary': summ}, f, ensure_ascii=False, indent=1)
    return base, spath


# ════════════════════ 校验 ════════════════════

def validate(series: Dict[str, list], history: Optional[Dict[str, list]] = None) -> Dict[str, dict]:
    """生成序列与历史统计对比: 逐月分布(KS 近似)、一阶自相关、ENSO 谱、位相转移。"""
    history = history or {m: load_mode_series(m) for m in MODES}
    report = {}
    for mode in MODES:
        gen = [v for _, _, v, _ in series[mode]]
        if not gen:
            continue
        hist = [v for _, _, v in history[mode]]
        # KS 近似: 最大 CDF 差(经验分布)
        ks = _ks_stat(gen, hist)
        # 一阶自相关
        ar_gen = _autocorr(gen, 1)
        ar_hist = _autocorr(hist, 1)
        # ENSO 准周期谱: 自相关在滞后 24-48 月的峰值(粗略)
        spec = _period_strength(gen)
        ok = ks < 0.40 and abs(ar_gen - ar_hist) < 0.35
        report[mode] = {
            'ks': round(ks, 3),
            'ar_gen': round(ar_gen, 3),
            'ar_hist': round(ar_hist, 3),
            'period_strength': round(spec, 3),
            'ok': bool(ok),
        }
    return report


def _ks_stat(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 1.0
    merged = sorted(set(a + b))
    if not merged:
        return 1.0
    n1, n2 = len(a), len(b)
    best = 0.0
    for x in merged[::max(1, len(merged) // 200)]:
        c1 = sum(1 for v in a if v <= x) / n1
        c2 = sum(1 for v in b if v <= x) / n2
        best = max(best, abs(c1 - c2))
    return best


def _autocorr(vals: List[float], lag: int) -> float:
    if len(vals) <= lag + 2:
        return 0.0
    mu = sum(vals) / len(vals)
    num = sum((vals[i] - mu) * (vals[i + lag] - mu) for i in range(len(vals) - lag))
    den = sum((x - mu) ** 2 for x in vals)
    return num / den if den > 0 else 0.0


def _period_strength(vals: List[float], lo: int = 24, hi: int = 60) -> float:
    """ENSO 准周期强度: 滞后 24-60 月自相关均值。"""
    best = 0.0
    for lag in range(lo, min(hi, len(vals) // 2)):
        best = max(best, abs(_autocorr(vals, lag)))
    return best


# ════════════════════ 命令行 ════════════════════

def main(argv: Optional[List[str]] = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description='F4 气候模态序列生成器')
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--years', type=int, default=5, help='总生成年数(含接续段)')
    ap.add_argument('--cont', type=int, default=2, help='接续段年数')
    ap.add_argument('--fetch', action='store_true', help='抓取实测数据归档')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    if args.fetch:
        r = archive_all()
        for k, v in r.items():
            print(f"  {k}: {'ok' if v else 'FAILED(用内置占位)'}")
    gen = ModeGenerator(seed=args.seed, cont_years=args.cont)
    series = gen.generate(total_years=args.years)
    p1, p2 = save_output(series, gen.seed, gen.sub_seeds, gen.retry_counts,
                         gen.low_confidence, args.out)
    print(f"seed={gen.seed} score={gen.best_score:.3f} retries={len(gen.retry_counts)}")
    print(f"低置信: {gen.low_confidence or '无'}")
    rep = validate(series)
    for mode, r in rep.items():
        print(f"  {mode}: ks={r['ks']} ar={r['ar_gen']}/{r['ar_hist']} {'OK' if r['ok'] else 'WARN'}")
    print(f"输出: {p1}\n     {p2}")


if __name__ == '__main__':
    main()
