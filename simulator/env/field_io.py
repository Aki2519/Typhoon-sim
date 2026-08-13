# simulator/env/field_io.py
"""F6 场存储与查询: npz 读写、月气候态/月 std、逐日线性插值、
get_field(var, date) / get_param(name, date) 查询接口。

存储布局: {变量}_{年份}.npz,每文件 12 个月面 (lat, lon)。
网格: 60°S-60°N / 0-360°E / 1°×1°。
"""
from __future__ import annotations
import os
import numpy as np
from typing import Dict, Optional, Tuple

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(ENV_DIR, 'library')      # 场库 npz
CLIM_DIR = os.path.join(ENV_DIR, 'climate')     # 气候态 npz
PARAM_DIR = os.path.join(ENV_DIR, 'params')     # 参数化序列 npz

NLAT = 121          # 60°S..60°N
NLON = 360
LATS = np.linspace(-60, 60, NLAT)
LONS = np.arange(0, 360, 1.0)

VARS = ('sst', 'ohc', 'shear', 'rh700', 'mslp', 'uv_steer',
        'vort850', 'gpi', 'ssta')
PARAMS = ('ridge', 'itcz', 'monsoon_trough')

# F10 渲染新字段契约(合成库侧默认生成,真实库/wrfout 侧以本块为准对齐)。
# 这些字段**不加入 VARS**(保持既有 API/analog 迭代遍历兼容),但可由
# save_monthly_field / load_monthly_field / get_field 直接存取(命名即契约)。
RENDER_VARS = ('u10', 'v10', 'precip24', 't500', 'qv850', 'hgt100', 'uv200')

# 字段定义: shape=单月面形状, unit=F10 图层单位, ndim=月面 vstack 维数
# (3D=(12,NLAT,NLON); 4D=(12,*,NLAT,NLON), 与 uv_steer 同构)。
FIELD_DEFS = {
    'u10':      {'shape': (NLAT, NLON),        'unit': 'm/s',     'ndim': 3},
    'v10':      {'shape': (NLAT, NLON),        'unit': 'm/s',     'ndim': 3},
    'precip24': {'shape': (NLAT, NLON),        'unit': 'mm/24h',  'ndim': 3},
    't500':     {'shape': (NLAT, NLON),        'unit': 'K',       'ndim': 3},
    'qv850':    {'shape': (NLAT, NLON),        'unit': 'g/kg',    'ndim': 3},
    'hgt100':   {'shape': (NLAT, NLON),        'unit': 'dam',     'ndim': 3},
    'uv200':    {'shape': (2, NLAT, NLON),     'unit': 'm/s',     'ndim': 4},
}

# 4D(双分量)变量: 与 uv_steer 同构 (12, 2, NLAT, NLON)。uv200 必须纳入。
_4D_VARS = frozenset({'uv_steer', 'uv200'})

# SST 色标(风迷惯例,界面显示/校验用)
SST_BINS = [(26.5, (90, 190, 235)), (28.5, (120, 220, 120)),
            (30.5, (250, 200, 90)), (40.0, (240, 90, 90))]
SST_LABELS = [(26.5, '冷水 <26.5°C'), (28.5, '温水 27-28.5°C'),
              (30.5, '热水 29-30.5°C'), (40.0, '开水 ≥31°C')]


def field_path(var: str, year: int, lib_dir: str = LIB_DIR) -> str:
    return os.path.join(lib_dir, f"{var}_{year}.npz")


def save_monthly_field(var: str, year: int, months: np.ndarray, lib_dir: str = LIB_DIR,
                       meta: Optional[dict] = None) -> str:
    """months: (12, NLAT, NLON)。缺失月用 np.nan 面,不静默填 0。"""
    os.makedirs(lib_dir, exist_ok=True)
    path = field_path(var, year, lib_dir)
    np.savez(path, months=months.astype(np.float32), meta=meta or {})
    return path


def load_monthly_field(var: str, year: int, lib_dir: str = LIB_DIR) -> Optional[np.ndarray]:
    path = field_path(var, year, lib_dir)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            m = z['months']
            # BUG-9: 损坏/旧格式文件(形状不对或含全 NaN)判为缺失, 不静默复用
            # uv_steer/uv200 为双分量 (12, 2, NLAT, NLON) 4D; 其余变量 3D
            expected_ndim = 4 if var in _4D_VARS else 3
            if m.shape[0] < 12 or len(m.shape) != expected_ndim:
                return None
            return m
    except Exception:
        return None


def load_all_years(var: str, years: range, lib_dir: str = LIB_DIR) -> Dict[int, np.ndarray]:
    out = {}
    for y in years:
        m = load_monthly_field(var, y, lib_dir)
        if m is not None:
            out[y] = m
    return out


# ════════════════════ 气候态 ════════════════════

def build_climatology(vars_: Tuple[str, ...], years: range,
                      lib_dir: str = LIB_DIR, clim_dir: str = CLIM_DIR) -> Dict[str, dict]:
    """按 (1991-2020 基准) 计算逐月气候态与月 std。返回 {var: {'clim','std'}}。"""
    os.makedirs(clim_dir, exist_ok=True)
    out = {}
    for var in vars_:
        months = [m for y in years for m in [load_monthly_field(var, y, lib_dir)]
                  if m is not None]
        if not months:
            out[var] = {'clim': None, 'std': None}
            continue
        stack = np.stack([m for m in months])       # (N, 12, NLAT, NLON)
        valid = ~np.isnan(stack)
        count = valid.sum(axis=0)
        clim = np.where(count > 0, np.nansum(stack, axis=0) / np.maximum(1, count), np.nan)
        sq = np.nansum(np.where(valid, (stack - clim) ** 2, 0.0), axis=0)
        std = np.sqrt(sq / np.maximum(1, count - 1))
        std[count < 2] = np.nan
        np.savez(os.path.join(clim_dir, f"{var}_clim.npz"), clim=clim, std=std)
        out[var] = {'clim': clim, 'std': std}
    return out


def load_climatology(var: str, clim_dir: str = CLIM_DIR) -> Optional[dict]:
    p = os.path.join(clim_dir, f"{var}_clim.npz")
    if not os.path.exists(p):
        return None
    with np.load(p, allow_pickle=False) as z:
        return {'clim': z['clim'], 'std': z['std']}


# ════════════════════ 日插值 ════════════════════

def _month_len(y: int, m: int) -> int:
    if m == 2:
        return 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28
    return 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30


def interpolate_daily(months: np.ndarray, year: int, month: int, day: int,
                      next_jan: Optional[np.ndarray] = None) -> np.ndarray:
    """逐月场 → 逐日(月内线性插值: 该月面与次月面的日线性混合)。
    12 月时次月为次年 1 月(next_jan 传入;缺失时退化为当月面)。"""
    mi = month - 1
    n = _month_len(year, month)
    a = months[mi]
    if month == 12:
        # R2-03: 12 月时次月为次年 1 月;缺失时退化为当月面(不回卷到同年 1 月)
        nxt = next_jan if next_jan is not None else a
    elif months.shape[0] == 12:
        nxt = months[(mi + 1) % 12]
    else:
        nxt = a
    # D11: t=day/n 使月末(day=n)收敛到次月面,消除月末→次月首日跳变
    t = day / n
    out = a + (nxt - a) * t
    return out


# ════════════════════ 查询接口(阶段3/4/5 只依赖此接口)════════════════════

class FieldAPI:
    """get_field(var, date) / get_param(name, date)。
    date: (year, month, day) 或 datetime。库缺失时返回 None。
    查询顺序: 生成场目录(generated_dir) → 历史场库(lib_dir)。"""

    def __init__(self, lib_dir: str = LIB_DIR, clim_dir: str = CLIM_DIR,
                 param_dir: str = PARAM_DIR, years: Optional[range] = None,
                 generated_dir: Optional[str] = None):
        self.lib_dir = lib_dir
        self.clim_dir = clim_dir
        self.param_dir = param_dir
        self.generated_dir = generated_dir
        self.years = years if years is not None else range(1979, 2027)
        self._cache: Dict[Tuple[str, int], np.ndarray] = {}

    def _months(self, var: str, year: int) -> Optional[np.ndarray]:
        key = (var, year)
        m = self._cache.get(key)
        if m is None:
            m = load_monthly_field(var, year, self.generated_dir or self.lib_dir)
            if m is None and self.generated_dir:
                m = load_monthly_field(var, year, self.lib_dir)
            if m is not None:
                if len(self._cache) > 64:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = m
        return m

    def get_field(self, var: str, date) -> Optional[np.ndarray]:
        y, mo, d = _split_date(date)
        months = self._months(var, y)
        if months is None:
            return None
        next_jan = None
        if mo == 12 and d > 1:
            nj = self._months(var, y + 1)
            if nj is not None:
                next_jan = nj[0]    # 次年 1 月面(单面)
        return interpolate_daily(months, y, mo, d, next_jan)

    def get_anomaly(self, var: str, date) -> Optional[np.ndarray]:
        """相对 1991-2020 气候态的距平。"""
        f = self.get_field(var, date)
        if f is None:
            return None
        cl = load_climatology(var, self.clim_dir)
        if cl is None or cl['clim'] is None:
            return None
        y, mo, _ = _split_date(date)
        return f - cl['clim'][mo - 1]

    def get_param(self, name: str, date) -> Optional[dict]:
        y, mo, _ = _split_date(date)
        p = os.path.join(self.param_dir, f"{name}.npz")
        if not os.path.exists(p):
            return None
        with np.load(p, allow_pickle=True) as z:
            years = z['years']
            rows = z['rows']
        idx = np.searchsorted(years, y * 12 + mo)
        if idx >= len(years) or years[idx] != y * 12 + mo:
            return None
        row = rows[idx]
        return {k: row[k] for k in ('lat', 'strength', 'lon_range') if k in row.dtype.names}


def _split_date(date):
    if hasattr(date, 'year'):
        y, mo, d = date.year, date.month, date.day
    else:
        y, mo, d = int(date[0]), int(date[1]), int(date[2])
    # BUG-11: 输入校验(字符串"2026-08-12"等非法输入不再产生垃圾索引)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return 1, 1, 1
    return y, mo, d
