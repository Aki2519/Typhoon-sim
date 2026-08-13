# simulator/typhoons/gen.py
"""F7 台风生成层: 四种生成模式 + 环境允许性检查 + 生成记录。

输入: 阶段2 get_field/get_param、基准 xrq(2000-2025)。
输出: records.json [{id, basin, t0, la0, lo0, w0, p0, mode, 理由日志}]
"""
from __future__ import annotations
import json
import os
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import basin as B

GEN_DIR = os.path.dirname(os.path.abspath(__file__))

# 环境允许性(唯一硬门槛: SST≥MIN_SST; 纬度受全球网格 ±60 限制)
# 用户规则: TC 不只由 ITCZ 生成——季风槽/反季风槽/背风低压/EX-SS 转热/东风波/
# 高纬环流短暂聚合都可能成 TC, 故切变/GPI/ITCZ 不再作硬门槛(仅用于采样权重)。
MIN_ABS_LAT = 5.0
MAX_ABS_LAT = 60.0
MIN_SST = 20.0
EX_SST_THRESHOLD = 25.0     # 指定坐标 SST<25°C → 以 EX/SS 性质起始, 自然演化
MAX_SHEAR_KT = 20.0
ITCZ_TOLERANCE = 8.0      # ITCZ/季风槽带 ±8°


class GenerationError(Exception):
    """环境不允许(给界面提示原因,不静默跳过)。"""


def _bilinear_at(fld, la: float, lo: float) -> float:
    """双线性采样(与模拟层一致,B5)。E2: y 用 (h-1) 缩放避免 0.5° 北偏。"""
    h, w = fld.shape
    x = (lo % 360.0) / 360.0 * w
    y = (la + 60.0) / 120.0 * (h - 1)
    x0, y0 = int(x), int(y)
    x0 = min(max(x0, 0), w - 1)
    y0 = min(max(y0, 0), h - 2)
    fx, fy = x - x0, y - y0
    x1, y1 = (x0 + 1) % w, y0 + 1
    v = (fld[y0, x0] * (1 - fx) * (1 - fy) + fld[y0, x1] * fx * (1 - fy)
         + fld[y1, x0] * (1 - fx) * fy + fld[y1, x1] * fx * fy)
    return float(v)


def env_allowed(api, date, la: float, lo: float, gpi_thr: Optional[float] = None) -> Optional[str]:
    """环境允许性检查(唯一硬门槛: SST≥MIN_SST, 纬度网格限制)。
    通过返回 None;不通过返回原因(中文)。"""
    if abs(la) < MIN_ABS_LAT:
        return f"纬度 {la:.1f}° 太靠近赤道(|lat|<{MIN_ABS_LAT:.0f}°)"
    if abs(la) > MAX_ABS_LAT:
        return f"纬度 {la:.1f}° 超出 {MAX_ABS_LAT:.0f}°(全球网格限制)"
    sst = None
    try:
        sst = api.get_field('sst', date) if api else None
    except Exception:
        sst = None
    if sst is not None:
        v = _bilinear_at(sst, la, lo)
        if not np.isfinite(v) or v < MIN_SST:
            return f"SST {v:.1f}°C < {MIN_SST}°C"
    return None


class Generator:
    """四种生成模式(用户指定)。"""

    def __init__(self, api=None, seed: int = 1, gpi_thr: Optional[float] = None):
        self.api = api
        self._seed = seed
        self.rng = np.random.RandomState(seed % (2**32))
        self.records: List[dict] = []
        self.rejections: List[dict] = []
        # B2: GPI 阈值 = xrq 历史生成点 GPI 40 分位(api 可用时)
        if gpi_thr is not None:
            self.gpi_thr = gpi_thr
        elif api is not None:
            # E3: 初始阈值用当前模拟年 8 月场;逐月生成时按生成月动态取
            gpi = None
            try:
                gpi = api.get_field('gpi', (2026, 8, 1))
            except Exception:
                gpi = None
            self.gpi_thr = B.genesis_gpi_threshold(gpi) if gpi is not None else 15.0
            self._gpi_thr_cache: Dict[Tuple[int, int], float] = {}
        else:
            self.gpi_thr = 15.0

    def _gpi_thr_for(self, year: int, month: int) -> float:
        """E3: 按生成月的 GPI 场取 40 分位阈值(带缓存)。"""
        cache = getattr(self, '_gpi_thr_cache', None)
        if cache is None:
            return self.gpi_thr
        key = (year, month)
        thr = cache.get(key)
        if thr is None:
            gpi = None
            try:
                gpi = self.api.get_field('gpi', (year, month, 1))
            except Exception:
                gpi = None
            thr = B.genesis_gpi_threshold(gpi) if gpi is not None else self.gpi_thr
            if len(cache) > 96:
                cache.pop(next(iter(cache)))
            cache[key] = thr
        return thr

    # ── 生成数(逐盆地逐月经验分布抽样)──

    def sample_monthly_counts(self, year: int, month: int,
                              counts: Optional[Dict[str, np.ndarray]] = None) -> Dict[str, int]:
        counts = counts or B.basin_monthly_counts()
        n_years = len(B.BASE_YEARS)      # 累计计数 → 年均
        out = {}
        for basin, arr in counts.items():
            mu = float(arr[month - 1]) / max(1, n_years)
            if mu <= 0:
                out[basin] = 0
                continue
            # 泊松抽样(经验分布年均)
            out[basin] = int(self.rng.poisson(mu))
        return out

    # ── 时间/空间抽样 ──

    @staticmethod
    def _month_len(y: int, m: int) -> int:
        if m == 2:
            return 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28
        return 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30

    def _sample_time(self, year: int, month: int) -> datetime:
        # L1: 整月日期分布(原 randint(1,28) 使 29-31 日永不出现)
        day = self.rng.randint(1, self._month_len(year, month) + 1)
        hour = self.rng.choice((0, 6, 12, 18))
        return datetime(year, month, day, hour)

    def _sample_position(self, date, la_band: Tuple[float, float],
                         lon_band: Tuple[float, float]) -> Tuple[float, float]:
        """空间抽样: 纬向均匀 + GPI 权重拒绝(api 可用时);带内权重 ×2。"""
        # E3: 阈值按生成月动态取(避免恒用 8 月场阈值)
        thr = self._gpi_thr_for(date.year, date.month)
        gpi = None
        try:
            gpi = self.api.get_field('gpi', date) if self.api else None
        except Exception:
            gpi = None
        # C5: 带权目标纬度——北半球用 ITCZ,南半球用季风槽带对称(季风槽纬度取负)。
        # 一次性获取(不再每轮循环内重复调用,避免对 API 的重复访问)。
        band_lat = None
        try:
            if la_band[0] < 0:      # 南半球 → 季风槽对称带
                ref = self.api.get_param('monsoon_trough', date) if self.api else None
                lat = ref.get('lat') if ref else None
                band_lat = -float(lat) if lat is not None else None
            else:                   # 北半球 → ITCZ 带
                ref = self.api.get_param('itcz', date) if self.api else None
                lat = ref.get('lat') if ref else None
                band_lat = float(lat) if lat is not None else None
        except Exception:
            band_lat = None
        for _ in range(200):
            la = self.rng.uniform(la_band[0], la_band[1])
            lo = self.rng.uniform(lon_band[0], lon_band[1])
            if gpi is None:
                return la, lo
            v = _bilinear_at(gpi, la, lo)      # B5: 双线性采样
            if np.isnan(v) or v <= 0:
                continue
            in_band = band_lat is not None and abs(la - band_lat) <= ITCZ_TOLERANCE
            weight = 2.0 if in_band else 1.0
            if thr <= 0 or self.rng.random() < min(1.0, v * weight / (thr * 4)):
                return la, lo
        # 兜底: 带内随机
        la = self.rng.uniform(la_band[0], la_band[1])
        return la, self.rng.uniform(lon_band[0], lon_band[1])

    # ── 四种模式 ──

    def generate(self, mode: str = 'D', *, year: Optional[int] = None,
                 month: Optional[int] = None, coord: Optional[Tuple[float, float]] = None,
                 t0: Optional[datetime] = None, basin: Optional[str] = None,
                 counts: Optional[Dict[str, np.ndarray]] = None) -> List[dict]:
        """mode: A 决定生成点 / B 决定生成时间 / C 两者给定 / D 完全随机。"""
        if mode == 'C':
            if coord is None or t0 is None:
                raise ValueError('模式 C 需给定坐标与时间')
            self._try_gen(t0, coord[0], coord[1], 'C')
        elif mode == 'A':
            if coord is None:
                raise ValueError('模式 A 需给定坐标')
            la0, lo0 = coord
            b = basin_of_guess(la0, lo0)
            for _ in range(24):
                t = self._sample_time(year or 2026, month or 8)
                la = self.rng.uniform(la0 - 3, la0 + 3)
                lo = (self.rng.uniform(lo0 - 3, lo0 + 3)) % 360
                if self._try_gen(t, la, lo, 'A'):
                    break
        elif mode == 'B':
            if t0 is None:
                raise ValueError('模式 B 需给定时间')
            b = basin or 'WP'
            la_band, lon_band = _basin_band(b)
            for _ in range(24):
                t = t0 + timedelta(hours=float(self.rng.randint(-8, 9) * 6))   # ±48h(6h 对齐,对称)
                la, lo = self._sample_position(t, la_band, lon_band)
                if self._try_gen(t, la, lo, 'B'):
                    break
        else:   # D 完全随机(整季无人值守)
            y = year or 2026
            # D8: 月份窗口从 month(接续段起始月)对齐到 12 月,不再固定 1 月起
            # (run.py GUI/headless 均按接续段起始月传参,避免环境场窗口错位)
            for mo in range(month or 1, 13):
                cnt = self.sample_monthly_counts(y, mo, counts)
                for b, n in cnt.items():
                    la_band, lon_band = _basin_band(b)
                    for _ in range(n):
                        t = self._sample_time(y, mo)
                        la, lo = self._sample_position(t, la_band, lon_band)
                        self._try_gen(t, la, lo, 'D')
        self._sort_records()
        # 需求1: 每次生成自动写日志(文件 + 直接输出到 cmd 窗口)
        print(f"[gen] 生成完成: {len(self.records)} 个系统, 拒绝 {len(self.rejections)} 次 "
              f"(seed={self._seed})")
        for r in self.records:
            print(f"[gen]   #{r['id']:02d} {r['t0']} {r['la0']:+.1f}° {r['lo0']:6.1f}° "
                  f"w={r['w0']:.0f}kt p={r['p0']:.0f}hPa 性质={r['nature']} "
                  f"盆地={r['basin']} 模式={r['mode']} | {r['reason']}")
        for r in self.rejections[:20]:
            print(f"[gen]   [拒] {r['t']} {r['la']:+.1f}° {r['lo']:6.1f}° | {r['reason']}")
        try:
            self.save_log()
        except Exception:
            pass
        return self.records

    def _try_gen(self, t: datetime, la: float, lo: float, mode: str) -> bool:
        reason = env_allowed(self.api, (t.year, t.month, t.day), la, lo)
        if reason:
            self.rejections.append({'t': t.isoformat(), 'la': la, 'lo': lo,
                                    'reason': reason})
            return False
        b = basin_of_guess(la, lo)
        # 用户规则: 坐标 SST<25°C → 以 EX/SS 性质起始(副热带/温带系统),
        # 自然模拟其路径/强度/消散, 不强行转为 TC; EX/SS 转 TC 也不再看 SST。
        sst = None
        if self.api is not None:
            s = None
            try:
                s = self.api.get_field('sst', (t.year, t.month, t.day))
            except Exception:
                s = None
            if s is not None:
                v = _bilinear_at(s, la, lo)
                if not np.isnan(v):
                    sst = v
        if sst is not None and sst < EX_SST_THRESHOLD:
            nature = 'EX' if self.rng.random() < 0.5 else 'SS'
            w0 = float(self.rng.randint(25, 45))
            p0 = float(self.rng.randint(990, 1008))
            reason = f"SST {sst:.1f}°C < 25°C → 以{nature}性质起始"
        else:
            nature = 'TD'
            w0 = float(self.rng.randint(15, 25))
            p0 = float(self.rng.randint(1005, 1010))
            reason = '环境允许性通过'
        tid = len(self.records) + 1
        self.records.append({
            'id': tid,
            'basin': b,
            't0': t.strftime('%Y%m%d%H'),
            'la0': round(la, 2),
            'lo0': round(lo, 2),
            'w0': w0,
            'p0': p0,
            'nature': nature,
            'mode': mode,
            'reason': reason,
        })
        return True

    def _sort_records(self):
        self.records.sort(key=lambda r: r['t0'])

    def save(self, path: Optional[str] = None) -> str:
        path = path or os.path.join(GEN_DIR, 'output', 'records.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'records': self.records, 'rejections': self.rejections},
                      f, ensure_ascii=False, indent=1)
        return path

    def save_log(self, path: Optional[str] = None) -> str:
        """需求1: 生成阶段日志(模式/参数/每条记录与拒绝原因)。"""
        if path is None:
            log_dir = os.path.normpath(os.path.join(GEN_DIR, '..', 'logs'))
            path = os.path.join(log_dir, f'gen_{self._seed}.log')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            f"=== 生成日志 (seed={getattr(self, '_seed', '?')}) ===",
            f"记录 {len(self.records)} 个, 拒绝 {len(self.rejections)} 次",
        ]
        for r in self.records:
            lines.append(
                f"  [{r['id']:02d}] {r['t0']} {r['la0']:+.1f}° {r['lo0']:6.1f}° "
                f"w={r['w0']:.0f}kt p={r['p0']:.0f}hPa 性质={r['nature']} "
                f"盆地={r['basin']} 模式={r['mode']} | {r['reason']}")
        for r in self.rejections[:50]:
            lines.append(f"  [拒] {r['t']} {r['la']:+.1f}° {r['lo']:6.1f}° | {r['reason']}")
        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")
        return path


def basin_of_guess(la: float, lo: float) -> str:
    b = B.basin_of(la, lo)
    return b


def _basin_band(basin: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    bands = {
        'NI': ((5, 25), (50, 100)),
        'WP': ((5, 30), (100, 180)),
        # E11: SH 覆盖西南印度洋(30-90°E 真实生成区); MD 上界对齐 MAX_ABS_LAT=40
        'SH': ((-30, -5), (30, 290)),
        'CP': ((5, 25), (180, 220)),
        'EP': ((5, 25), (220, 280)),
        'AL': ((5, 30), (280, 360)),
        'MD': ((30, 40), (350, 360)),
    }
    return bands.get(basin, ((5, 25), (100, 180)))


if __name__ == '__main__':
    g = Generator(seed=42)
    recs = g.generate('D', year=2027)
    print('generated:', len(recs), 'rejected:', len(g.rejections))
    p = g.save()
    print('saved:', p)
