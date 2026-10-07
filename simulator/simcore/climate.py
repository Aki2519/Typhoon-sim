# simulator/simcore/climate.py
"""真实气候数据驱动环境场(借鉴 KWP: 用 GCM/再分析格点数据替代解析公式)。

数据来源: simulator/env/library/*.npz(12 个月面, 60S-60N / 0-360E / 1°×1°)。
- 3D 变量(sst/ohc/shear/rh700/mslp/gpi...): months = (12, 121, 360)
- 4D 变量(uv_steer/uv200):               months = (12, 2, 121, 360)  [u/v 双分量]

与 SimCore 内核的对接:
- 内核网格: ENVNY=120 格心(纬度 -59.5..59.5 每 1°), ENVNX=360 格心(经度 0.5..359.5)
- 库网格:   NLAT=121 格点(纬度 -60..60), NLON=360 格点(经度 0..359)
- 本模块负责: 载入 → 重采样到内核格心网格 → 按模拟时间做月间+日内插值。

dataMode 语义(与 KWP 对照):
- 真实数据场替代解析公式(海温/热含量/切变/中层湿度/引导风)
- 保留并叠加内核自身的动态过程(台风冷尾流 wake/ohcWake、天气尺度涡旋 eddies、
  干空气块 dryBlobs、随机扰动), 使"真实气候背景 + 动态天气"并存。
"""
from __future__ import annotations

import logging
import os
import math
from collections import OrderedDict
from contextlib import contextmanager
from typing import Dict, Optional, Tuple

import numpy as np

# SimCore 内核坐标常量(与 core.C 对齐)
# F2: 不要在这里重新写死 360/120 —— 内核网格已改成 0.5°(720×240),
# 两份常量一旦漂移, resample_lat 会把场插到错误的纬度上(静默错位)。
LON0, LON1 = 0.0, 360.0
# **必须与 core.C 一致**(这里曾是 ±60 的第二份真源, 扩域时最容易漏掉的一处)
LAT0, LAT1 = -90.0, 90.0
DEG = math.pi / 180.0


def _env_dims():
    """(ENVNX, ENVNY, ENVD) —— 唯一真源在 core.C, 懒导入避免循环依赖。"""
    from . import core as _C
    return _C.ENVNX, _C.ENVNY, float(_C.C['ENVD'])


# ⚠ 「读库回落」必须留日志：这个项目在 `map/land.png` 上栽过一次**静默降级**
# （找不到就悄悄换成 0.5° 粗掩码，画出来还是"正常"的图 ⇒ 很久没人发现）。
# 数据源的同一条规矩：**只要没用首选源，就 `logger.warning`**。
logger = logging.getLogger(__name__)


ENVNX, ENVNY = _env_dims()[:2]
ENVD = _env_dims()[2]

# 库网格(与 env/field_io 一致)
NLAT, NLON = 121, 360
LATS = np.linspace(-60, 60, NLAT)     # 格点(含两端)
LONS = np.arange(0, 360, 1.0)

# 库目录
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    '..', 'env', 'library')
_CLIM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '..', 'env', 'climate')
#: 真实再分析库（S8/S8b 由 `simulator/env/build_reallib_cds.py` 从 CDS ERA5 生成）。
#: 与 `library/` **同 shape (12,121,360) / 同 keys ['months','meta'] / 同 dam 口径**，
#: 但 `meta.source='era5-cds'` 且是**真值**；`library/` 那 48 年是**合成**产物
#: （`meta.source='synthetic'`，且 `hgt500` 只是 `mslp` 的线性代理 `560+(1013-mslp)`，corr=-1.000000）。
#: ⚠ **绝不覆盖/改写 `library/` 与 `climate/`** —— 它们另有用途（可复现的合成基准）。
_REALLIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'env', 'reallib')

# 单位/标定常量(单点表达, 避免魔法数散落)
KT = 1.944                       # m/s → kt
SHEAR_MIN_KT = 2.9               # 环境切变下限(kt)
# 日内加性日变化幅度(绝对单位): sst°C / ohc kJ / shear kt / rh % / mslp hPa / uv m/s
DAILY_AMP = {'sst': 0.6, 'ohc': 2.0, 'shear': 0.3, 'rh': 2.0, 'mslp': 1.5, 'uv': 0.4}

# 加载缓存: 小容量 LRU, 只缓存**成功**结果(失败不缓存, 便于补数据后即时生效)
_CACHE_MAX = 64
_cache: 'OrderedDict' = OrderedDict()


def _cache_set(key, val) -> None:
    _cache[key] = val
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


@contextmanager
def _chdir_ctx(path: str):
    """临时切换工作目录, 离开时恢复(即便异常)。
    针对 netCDF4 在 Windows 无法读取中文绝对路径的实测限制;
    副作用限定在上下文内、可重入。"""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _var_paths(var: str, year: Optional[int], clim: bool):
    """按**优先级**返回候选路径: `reallib/`（真实）优先, 其次 `climate/`(气候态) 或 `library/`。

    · clim=True  ⇒ [reallib/{var}_clim.npz, climate/{var}_clim.npz]
    · 否则       ⇒ [reallib/{var}_{year}.npz, library/{var}_{year}.npz]
    回落由 `load_months` 打 `logger.warning`（见那里的注释）。
    """
    if clim:
        return [os.path.join(_REALLIB, f'{var}_clim.npz'),
                os.path.join(_CLIM, f'{var}_clim.npz')]
    return [os.path.join(_REALLIB, f'{var}_{year}.npz'),
            os.path.join(_LIB, f'{var}_{year}.npz')]


def _var_path(var: str, year: Optional[int], clim: bool):
    """旧接口: 返回**首选**路径（向后兼容; 内部走 `_var_paths`）。"""
    return _var_paths(var, year, clim)[0]


def load_raw_sst_months(year: int) -> Optional[np.ndarray]:
    """加载真实 ERA5 SST 再分析(env/raw/sst_{yy}_{mm}.nc), 组装为 (12,121,360) K→°C。
    仅当该年 12 个月文件齐全时返回; 否则 None(不缓存缺失, 补数据后即时生效)。
    已处理: 纬度降序(60→-60)翻转为升序; 缺失(陆地/冰盖 NaN)沿纬度插值填充。"""
    key = ('raw_sst', year)
    if key in _cache:
        return _cache[key]
    raw = os.path.join(os.path.dirname(_LIB), 'raw')   # env/raw (与 library 同级)
    if not os.path.isdir(raw):
        return None
    files = [f'sst_{year}_{m:02d}.nc' for m in range(1, 13)]
    if not all(os.path.exists(os.path.join(raw, fn)) for fn in files):
        return None
    months = []
    for fn in files:
        s = _read_sst_nc(raw, fn)
        if s is None:
            return None                 # 任一文件读取失败 → 整年不采用
        months.append(s)
    out = np.stack(months, axis=0)
    _cache_set(key, out)
    return out


def _read_sst_nc(raw_dir: str, fn: str) -> Optional[np.ndarray]:
    """读取单月 ERA5 SST nc → (121, 360) °C。中文绝对路径限制用 _chdir_ctx 规避。"""
    import netCDF4
    with _chdir_ctx(raw_dir):
        try:
            ds = netCDF4.Dataset(fn)
            s = np.asarray(ds.variables['sst'][:], dtype=np.float32)
            lat = np.asarray(ds.variables['latitude'][:])
            ds.close()
        except Exception:
            return None
    if s.ndim == 3:
        s = s[0]
    s = s - 273.15                              # K → °C
    if lat.size > 1 and lat[1] < lat[0]:
        s = s[::-1, :]                          # 降序(60→-60) → 升序(-60→60)
    return _fill_nan_lat(s)


def _fill_nan_lat(a: np.ndarray) -> np.ndarray:
    """沿纬度轴(axis 0)对每经度列线性插值填充 NaN; 端点用最近有效值。
    用于 ERA5 陆地/冰盖缺失点(不改变海洋有效值)。"""
    if not np.isnan(a).any():
        return a
    b = a.copy()
    n_lat = b.shape[0]
    lat_i = np.arange(n_lat)
    for col in range(b.shape[-1]):
        v = b[:, col]
        nan = np.isnan(v)
        if not nan.any():
            continue
        valid = ~nan
        if not valid.any():
            continue                        # 整列无效(异常文件)保持 NaN, 由上层回退
        v[nan] = np.interp(lat_i[nan], lat_i[valid], v[valid])
    return b


#: 回落警告去重（U-S8b-5）：reallib 目前只有 mslp/hgt500，其余变量**每次**调用都会回落
#: ⇒ 逐帧刷屏。**刷屏的日志等于没日志**，所以每个 (var, year, clim) 只警告一次。
_WARNED_FALLBACK: set = set()


def load_months(var: str, year: Optional[int] = None,
                clim: bool = False) -> Optional[np.ndarray]:
    """加载变量月数据, 返回 (12, ...) 数组或 None。
    year None 且非 clim: 用气候态(_clim)。
    显式 year 缺文件 → None(不再静默用 2000 年近似, 避免"指定 1900 得到 2000"的误导);
    气候态缺 _clim 文件时回退到 2000 年库作近似。
    失败/缺失一律不写缓存(便于补齐数据后无需重启即生效)。"""
    if clim:
        year = None
    key = (var, year)
    if key in _cache:
        return _cache[key]
    _cands = _var_paths(var, year, True) if year is None else _var_paths(var, year, False)
    p = None
    for _i, _c in enumerate(_cands):
        if os.path.exists(_c):
            p = _c
            if _i > 0:
                # **回落必须留日志**（不静默降级）: 首选 reallib 缺失 ⇒ 用了 library/climate。
                # ⚠ 但**每个 (var, year, clim) 只警告一次**（U-S8b-5）：否则逐帧刷屏。
                _wk = (var, year, (year is None))
                if _wk not in _WARNED_FALLBACK:
                    _WARNED_FALLBACK.add(_wk)
                    logger.warning(
                        '读库回落: var=%s year=%s clim=%s —— 首选 %s 不存在, 改用 %s'
                        '（本组合后续不再重复告警）',
                        var, year, (year is None), os.path.basename(_cands[0]), _c)
            break
    if p is None and year is None:
        # 既有行为(保留): 气候态缺失时退回 **2000 年库近似**。
        # ⚠ 原来这一步**一声不响**就换了数据源 ⇒ 现在补日志。
        _fb = _var_paths(var, 2000, False)[-1]
        if os.path.exists(_fb):
            logger.warning(
                '读库回落: var=%s 气候态缺失(%s) —— **改用 2000 年库近似** %s',
                var, [os.path.basename(c) for c in _cands], _fb)
            p = _fb
    if p is None:
        return None
    try:
        # with 关闭 NpzFile 的 zip 句柄(否则滞留到 GC)
        with np.load(p, allow_pickle=True) as z:
            # 库文件用 'months'; 气候态文件用 'clim'
            if 'months' in z:
                months = np.asarray(z['months'], dtype=np.float32)
            elif 'clim' in z:
                months = np.asarray(z['clim'], dtype=np.float32)
            else:
                return None
        _cache_set(key, months)
        return months
    except Exception:
        return None


def resample_lat(months: np.ndarray, axis_lat: int = -2) -> np.ndarray:
    """库 121 格点(-60..60) → 内核格心 线性重采样。

    F2: 改成全向量化。原实现对每一行纬度做一次 Python 循环 + 花式索引,
    内核升到 0.5°(240 行)后单次就要几十毫秒, 而 env_update 每步要重采样
    8 个变量, 直接成帧率杀手(实测 dataMode 稳态 616ms/次)。向量化后 <5ms。
    """
    n_lat = months.shape[axis_lat]
    if n_lat == ENVNY:
        return months
    # 目标格心纬度(用内核自己的 ENVD, 不写死 1°)
    dst = LAT0 + (np.arange(ENVNY, dtype=np.float64) + 0.5) * ENVD
    step = float(LATS[1] - LATS[0])
    i = (dst - float(LATS[0])) / step
    i0 = np.clip(np.floor(i).astype(np.int64), 0, n_lat - 1)
    i1 = np.clip(i0 + 1, 0, n_lat - 1)
    # **夹取**: 库只有 ±60, 而内核域已扩到 ±90 —— 不夹的话目标纬度落在源范围外会
    # 线性外推(实测把 shear 外推成负值)。夹到 [0,1] 后极区=取边界行(临时口径)。
    f = np.clip(i - i0, 0.0, 1.0).astype(np.float32)
    moved = np.moveaxis(months, axis_lat, 0)                 # (lat, ...)
    shape = (ENVNY,) + (1,) * (moved.ndim - 1)
    out = (moved[i0] * (1.0 - f).reshape(shape)
           + moved[i1] * f.reshape(shape))
    return np.moveaxis(out, 0, axis_lat)


def _days_in_month(y: int, m: int) -> int:
    import calendar
    return calendar.monthrange(y, m)[1]


def month_float_of(dt, month0: int = 1, t_h: float = 0.0) -> float:
    """绝对时刻 → 连续月序(0=1月)。

    F1 修正: 库里的值是**月平均**,应视为"月中"有效。旧实现把它当月**初**,
    于是 9 月 14 日插出 45% 的 10 月(季节相位提前半个月, 约 0.5°C 系统冷偏差)。
    正确做法: 月平均锚在月中, `f = (day - 15)/该月天数`。
    例: 9/14 → 8.03(97% 九月 + 3% 十月); 9/1 → 7.53(47% 八月 + 53% 九月)。
    """
    if dt is not None:
        y, m, d = dt.year, dt.month, dt.day
        dim = _days_in_month(y, m)
        return ((m - 1) + (d - 15.0) / dim) % 12.0
    # 旧接口回落(t_h 小时 + month0 起算, 30 天近似)
    day = t_h / 24.0
    return ((month0 - 1) + day / 30.0) % 12.0


def resample_lon(months: np.ndarray, axis_lon: int = -1) -> np.ndarray:
    """库 360 格点(整数度) → 内核 ENVNX 格心, 经度周期插值。

    F2: 内核网格从 1° 改到 0.5° 后, 经度也必须从 360 升到 720 —— 只重采样纬度
    会让场停在 (240,360), 与 wake/land 的 (240,720) 广播不上, 直接抛异常
    (比静默错位好, 但功能是坏的)。
    """
    n = months.shape[axis_lon]
    if n == ENVNX:
        return months
    dst = (np.arange(ENVNX) + 0.5) * ENVD          # 目标格心经度(源坐标 = 经度)
    i0 = np.floor(dst).astype(np.int64) % n
    i1 = (i0 + 1) % n
    f = (dst - np.floor(dst)).astype(np.float32)
    moved = np.moveaxis(months, axis_lon, -1)
    out = moved[..., i0] * (1.0 - f) + moved[..., i1] * f
    return np.moveaxis(out, -1, axis_lon)


def resample_env(months: np.ndarray) -> np.ndarray:
    """库网格(…,121,360) → 内核网格(…,ENVNY,ENVNX), 纬度+经度都重采样。"""
    return resample_lon(resample_lat(months))


#: 已重采样到内核网格的月场缓存。env_update 每步都要这套场, 而重采样是纯几何
#: 变换、与时间无关 —— 不缓存的话每次都要把 8 个变量重插一遍(实测 0.5° 下
#: dataMode 稳态 616ms/次, 缓存后 ~10ms)。
_ENV_CACHE_MAX = 64
_env_cache: 'OrderedDict' = OrderedDict()


def clear_caches() -> None:
    """清空两级缓存(原始月场 / 已重采样月场)。

    测试里要 monkeypatch load_months 来模拟"某变量缺失"时, 必须先清缓存 ——
    否则命中缓存就绕过了 patch, 断言的降级路径根本没被走到。
    """
    _cache.clear()
    _env_cache.clear()


def load_months_env(var: str, year: Optional[int] = None,
                    clim: bool = False) -> Optional[np.ndarray]:
    """load_months + 重采样到内核网格, 结果按 (var, year) 缓存。"""
    key = (var, year if not clim else None)
    v = _env_cache.get(key)
    if v is not None:
        _env_cache.move_to_end(key)
        return v
    m = load_months(var, year, clim)
    if m is None:
        return None
    v = np.ascontiguousarray(resample_env(m), dtype=np.float32)
    _env_cache[key] = v
    _env_cache.move_to_end(key)
    while len(_env_cache) > _ENV_CACHE_MAX:
        _env_cache.popitem(last=False)
    return v


def interp_month(months: np.ndarray, t_h: float, month0: int,
                 day_cycle: float = 1.0, daily_amp: float = 0.0,
                 clock=None) -> np.ndarray:
    """按模拟时刻对 12 月数据做闭式月间插值 + 日内谐波。

    - 月间: 线性插值(月平均锚在月中, 见 month_float_of); 跨年循环。
    - 日内: 当地太阳时驱动的日变化 —— 每一格点的"当地时间"由
      模拟绝对小时 + 经度时角决定, 峰值在当地正午(太阳加热)。
      daily_amp 为**加性**幅度(绝对单位, 如 sst=0.6°C / mslp=1.5hPa),
      避免乘法调制把海温/气压整体放大(物理错误)。
    返回与 months 同形(经度最后一维, 纬度已对齐)的场。
    """
    n_m = months.shape[0]
    m_float = month_float_of(clock, month0, t_h)
    m0 = int(math.floor(m_float)) % 12
    m1 = (m0 + 1) % 12
    f = m_float - math.floor(m_float)
    base = months[m0] * (1 - f) + months[m1] * f
    if day_cycle <= 0 or daily_amp <= 0:
        return base
    # 当地太阳时(0-24): 用模拟时刻的绝对 UTC 小时(有 clock 时用它,
    # 否则回落到 t_h 取模 —— 起始时次非 00z 时旧写法会偏移)
    utc_hour = ((clock.hour + clock.minute / 60.0) if clock is not None
                else (t_h % 24.0 + 24.0) % 24.0)
    lon = (np.arange(base.shape[-1]) + 0.5)          # 0..359 经度格心
    local_h = (utc_hour + lon / 15.0) % 24.0          # 每经度当地小时
    # 日变化: 峰值在 14 时(下午最暖), 低谷在 02 时; 幅度随季节(夏大冬小)
    daily = np.cos(2 * math.pi * (local_h - 14.0) / 24.0)
    seas = 0.5 + 0.5 * math.cos(2 * math.pi * (m_float - 6) / 12.0)
    return base + daily * day_cycle * seas * daily_amp


def day_cycle_term(shape, t_h: float, month0: int, day_cycle: float,
                   daily_amp: float, clock=None):
    """只要**日内谐波**那一项(供模型路径复用)。

    为什么需要它: 日内变化原来只在 (旧库路径)里加, 而环境场模型
    可用时字段会被  整块覆盖 ⇒ 用户面板里的"日变化"设置对模型路径**完全无效**
    (静默 no-op)。这里把同一套公式抽出来, 让模型路径也能按设置加日变化;
    瞬时场不加(实测数据本身已含日内信号, 加了就是重复计入)。
    """
    if day_cycle <= 0 or daily_amp <= 0:
        return None
    m_float = month_float_of(clock, month0, t_h)
    utc_hour = ((clock.hour + clock.minute / 60.0) if clock is not None
                else (t_h % 24.0 + 24.0) % 24.0)
    lon = (np.arange(shape[-1]) + 0.5)
    local_h = (utc_hour + lon / 15.0) % 24.0
    daily = np.cos(2 * math.pi * (local_h - 14.0) / 24.0)
    seas = 0.5 + 0.5 * math.cos(2 * math.pi * (m_float - 6) / 12.0)
    return (daily * day_cycle * seas * daily_amp).astype(np.float32)


def build_env_fields(sim_params: dict, t_h: float, month0: int,
                     clock=None) -> Optional[dict]:
    """按 dataMode 参数构建内核 env 场覆盖层。
    返回 {sst, ohc, shear, rh, u850, v850, u200, v200, uMid, vMid} 或 None(无数据)。
    sst/ohc/shear/rh 单位与内核一致: °C / kJ / kt / %; 风: kt(内核全 kt)。

    clock: F1 起传入的绝对时刻;决定基态气候场的年内插值与模态索引取值。
    """
    dm = sim_params.get('dataMode', False)
    if not dm:
        return None
    # ── F2/F3: 优先用离线训练出来的环境场模型(基态 + 模态 + 增暖) ──
    # 它是"消化过的真实数据"(OISST/ERA5/AOML 训练产物), 运行时零联网;
    # 产物不存在时静默回落旧合成库/ERA5 raw —— 保证没有训练产物也能跑。
    _model = None
    try:
        from . import envmodel as _EM
        _model = _EM.solve(sim_params, clock=clock)
    except Exception:
        _model = None
    year = sim_params.get('dataYear')
    clim = bool(sim_params.get('dataClim', False)) or year is None
    dc = float(sim_params.get('dataDayCycle', 1.0))
    amp = DAILY_AMP

    def get3(name, damp=0.0):
        m = load_months_env(name, year, clim)      # 已重采样 + 缓存
        if m is None:
            return None
        return interp_month(m, t_h, month0, dc, damp, clock=clock)

    def get4(name, damp=0.0):
        m = load_months_env(name, year, clim)      # 已重采样 + 缓存
        if m is None:
            return None
        # m: (12, 2, 121, 360) -> 分 u/v 各重采样
        u = interp_month(np.ascontiguousarray(m[:, 0]), t_h, month0, dc, damp,
                         clock=clock)
        v = interp_month(np.ascontiguousarray(m[:, 1]), t_h, month0, dc, damp,
                         clock=clock)
        return u, v

    fields = {}
    # 真实 ERA5 SST 优先(env/raw, dataYear 有完整 12 月时); 否则合成库
    sst = None
    if year is not None:
        try:
            sst = load_raw_sst_months(int(year))
        except Exception:
            sst = None
    if sst is None:
        sst = get3('sst', amp['sst'])
    else:
        # raw 全年文件走与库相同的处理: 重采样到内核网格 + 月间/日内插值
        # (同样要缓存 —— 这是每年 12 个月面的几何变换, 与时间无关)
        key = ('raw_sst_env', int(year))
        cached = _env_cache.get(key)
        if cached is None:
            cached = np.ascontiguousarray(resample_env(sst), dtype=np.float32)
            _env_cache[key] = cached
            _env_cache.move_to_end(key)
            while len(_env_cache) > _ENV_CACHE_MAX:
                _env_cache.popitem(last=False)
        sst = interp_month(cached, t_h, month0, dc, amp['sst'])
    if sst is not None:
        fields['sst'] = sst
    ohc = get3('ohc', amp['ohc'])
    if ohc is not None:
        fields['ohc'] = ohc
        # 旧合成库的 ohc 是 kJ/cm²(见 climate.UNITS), 必须显式标注 ——
        # core 只在标注为 'm' 时才把它当 D26 用
        fields['_ohc_unit'] = 'kJ'
    # ── 500hPa 位势高度（R18/S3）：副高 5880 线的**真实**数据来源 ──
    # 仓库里 `env/climate/hgt500_clim.npz` + `env/library/hgt500_{1990,1991,1992}.npz`
    # （只有这 3 个年份 + 气候态）。单位 gpm（ERA5 500hPa 位势高度）。
    # `get3(..., 0.0)`：不加日变化（位势高度的日变化可忽略），与其它标量同一套
    # 重采样 + 月间插值路径 ⇒ 直接落在内核网格上。
    _z500 = get3('hgt500', 0.0)
    if _z500 is not None:
        fields['z500'] = _z500.astype(np.float32)
        # S11：**距平基准** —— 真实 500hPa 气候态（常走 reallib/hgt500_clim.npz）。
        # ⚠ `n_years=1` 时它只是**一年的年循环**，不是 30 年气候态（该文件 meta 有 warning）。
        # 单独取 clim 分支：`get3` 跟随调用方的 year/clim，而基准必须**始终是气候态**。
        # （U-S11-4 实验：已用临时开关证伪"本键扰动物理"的假说，见 s12_sandbox_port.md §2；
        #   开关已移除，恢复为无条件执行。）
        try:
            _mc = load_months_env('hgt500', None, True)
            if _mc is not None:
                fields['z500Base'] = interp_month(_mc, t_h, month0, dc, 0.0,
                                                  clock=clock).astype(np.float32)
        except Exception:
            pass
    shear = get3('shear', amp['shear'])
    if shear is not None:
        # 库 shear 单元: m/s(见 real_source)。转 kt
        # ×0.55: 与 envmodel.solver 同一口径(月均矢量差 → 日尺度切变), 详见那里
        fields['shear'] = np.maximum(SHEAR_MIN_KT, shear * KT * 0.55).astype(np.float32)
    rh = get3('rh700', amp['rh'])
    if rh is not None:
        fields['rh'] = np.clip(rh, 25, 95).astype(np.float32)
    mslp = get3('mslp', amp['mslp'])
    if mslp is not None:
        fields['mslp'] = mslp.astype(np.float32)
    suv = get4('uv_steer', amp['uv'])
    if suv is not None:
        u, v = suv
        # 库 uv_steer: m/s -> kt
        fields['u850'] = (u * KT).astype(np.float32)
        fields['v850'] = (v * KT).astype(np.float32)
        # 中层/高层近似(u200 → 1.45×850 与内核公式一致, 但用真实方向)
        fields['u200'] = (u * KT * 1.45).astype(np.float32)
        fields['v200'] = (v * KT * 1.45).astype(np.float32)
        fields['uMid'] = (u * KT * 1.225).astype(np.float32)
        fields['vMid'] = (v * KT * 1.225).astype(np.float32)
    # ── F2/F3: 模型场优先, 缺口用旧库补齐, 并把"降级字段"显式报给 core ──
    # 不能整块 return 模型场: core 的 dataState 判定依赖字段完整性, 而模型
    # 产物可能只烘焙了一部分(base_wind 还没下到时只有 SST/D26)。合并之后
    # 契约(必有 sst/ohc/shear/rh/u850/...)仍成立, 由 _degraded 说明哪些是
    # 用旧库/解析补的 —— 状态徽标因此不会对用户撒谎。
    if _model:
        degraded = []
        # 自洽性: 模型给了真风切(shear)时, **不能**再从旧库补 u850/u200 ——
        # core 的 data 分支一旦看到 u850 就会用 hypot(u200-u850) 覆盖 shear,
        # 而旧库的 u200 是 1.45×u850 合成的假风, 等于把真风切又冲掉
        # (实测: 真值 1.3~89 kt 被冲成 0~30 kt)。缺风场就让 core 走解析地转,
        # 这样"风切"和"风场"至少各自自洽。
        _skip = {'u850', 'v850', 'u200', 'v200', 'uMid', 'vMid'}             if 'shear' in _model else set()
        for k in ('shear', 'rh', 'mslp', 'u850', 'v850', 'u200', 'v200',
                  'ohc', 'sst'):
            if k not in _model:
                degraded.append(k)
        for k in _skip:
            fields.pop(k, None)
        for k in ('sst', 'ohc', 'shear', 'rh', 'mslp', 'u850', 'v850',
                  'u200', 'v200', 'uMid', 'vMid', 'ohc_kj', 't200'):
            if k in _model:
                fields[k] = _model[k]
        # 单位标签必须跟着实际来源走: 旧库分支会写 _ohc_unit='kJ', 若模型给了
        # 真正的 D26(米) 却不覆盖这个标签, core 会误判成 kJ 而改走解析柱
        # (实测: 基态 137 m 被算成 63 m)。
        if 'ohc' in _model:
            fields['_ohc_unit'] = 'm'
        if degraded:
            fields['_degraded'] = degraded
        # **诊断标记必须一起传下去**(2026-09-20): 合并循环只搬固定字段名, 于是
        # solve() 里打的 _instant / _shear_instant / _ohc_instant 全被丢掉 ——
        # 后果 ① UI 徽标永远显示"月均"(即使走的是瞬时场); ② 任何依赖这些标记的
        # 下游逻辑(例如瞬时风切不再乘 0.55)在跨过这一层之后失效。
        for _k in ('_instant', '_instant_partial', '_instant_groups', '_instant_src',
                   '_shear_instant', '_ohc_instant', '_ohc_basins',
                   '_shear_tc_scale_deg'):
            if _k in _model:
                fields[_k] = _model[_k]
        # **日变化**: 模型路径原来完全没有(见 day_cycle_term 的说明)。瞬时场跳过,
        # 因为它本身已经包含日内信号, 再叠加就是重复计入。
        # **逐字段判**: 只要该字段本身是瞬时的就不加日变化。原来只看整体 _instant
        # (要求 sst+shear 都瞬时), 于是"只有瞬时 SST"的日子会给瞬时海温再叠一层
        # 合成日变化 —— 实测 2026-02-28 00Z 暖池 SST 因此偏高 0.96 °C(自查时发现)。
        _grp = _model.get('_instant_groups') or {}
        _inst_grp = {g for g, v in _grp.items() if v}
        if dc > 0:
            for _k, _amp_key in (('sst', 'sst'), ('ohc', 'ohc'), ('shear', 'shear'),
                                 ('rh', 'rh'), ('mslp', 'mslp'),
                                 ('u850', 'uv'), ('v850', 'uv'),
                                 ('u200', 'uv'), ('v200', 'uv')):
                # **只给"确实来自模型"的字段加**: 模型缺某个字段时 fields 里留的是
                # 旧库的值, 而旧库那条路已经在 interp_month 里加过日内项了 ——
                # 再给降级字段补一次就是双份日变化(自查时发现)。
                if _k not in fields or _k not in _model:
                    continue
                # 该字段所属的瞬时分组已就绪 → 数据里本来就含日内信号, 不再合成
                _g_of = {'sst': 'sst', 'mslp': 'sst', 'ohc': 'ohc'}.get(_k, 'shear')
                if _g_of in _inst_grp:
                    continue
                _t = day_cycle_term(np.asarray(fields[_k]).shape, t_h, month0, dc,
                                    float(amp.get(_amp_key, 0.0)), clock=clock)
                if _t is not None:
                    fields[_k] = (np.asarray(fields[_k], np.float32) + _t
                                  ).astype(np.float32)
    # 物理守卫: 模态/日变化叠加后 shear 可能被推到微负(实测 -0.068 kt)。
    # 切变是**矢量差的模**, 不可能为负 —— 出口统一夹一次。
    if fields and 'shear' in fields:
        fields['shear'] = np.maximum(np.asarray(fields['shear'], np.float32),
                                     0.0).astype(np.float32)
    return fields or None


# 单位说明: 库各变量单位(与 env/field_io FIELD_DEFS 对齐)
# sst °C, ohc kJ/cm², shear m/s, rh700 %, uv_steer m/s, mslp hPa
UNITS = {'sst': 'degC', 'ohc': 'kJ', 'shear': 'kt', 'rh': '%', 'wind': 'kt'}
