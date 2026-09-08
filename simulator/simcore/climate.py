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

import os
import math
from collections import OrderedDict
from contextlib import contextmanager
from typing import Dict, Optional, Tuple

import numpy as np

# SimCore 内核坐标常量(与 core.C 对齐)
LON0, LON1 = 0.0, 360.0
LAT0, LAT1 = -60.0, 60.0
ENVNX, ENVNY = 360, 120
DEG = math.pi / 180.0

# 库网格(与 env/field_io 一致)
NLAT, NLON = 121, 360
LATS = np.linspace(-60, 60, NLAT)     # 格点(含两端)
LONS = np.arange(0, 360, 1.0)

# 库目录
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    '..', 'env', 'library')
_CLIM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '..', 'env', 'climate')

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


def _var_path(var: str, year: Optional[int], clim: bool):
    if clim:
        return os.path.join(_CLIM, f'{var}_clim.npz')
    return os.path.join(_LIB, f'{var}_{year}.npz')


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
    if year is None:
        p = _var_path(var, None, True)
        if not os.path.exists(p):
            p = _var_path(var, 2000, False)     # 无气候态: 用 2000 年库近似
    else:
        p = _var_path(var, year, False)
    if not os.path.exists(p):
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
    """库 121 格点(-60..60) → 内核 120 格心(-59.5..59.5) 线性重采样。
    处理 3D(360,121)与 4D(360,121,2)两种: 纬度轴固定为倒数第 2 维。"""
    n_lat = months.shape[axis_lat]
    if n_lat == ENVNY:
        return months
    # 目标格心纬度
    dst_lat = np.array([LAT0 + (j + 0.5) for j in range(ENVNY)], dtype=np.float64)
    # 源格点坐标 -> 插值位置(把格心值视为格点值, 线性插值到新位置)
    src = LATS.astype(np.float64)
    moved = np.moveaxis(months, axis_lat, 0)          # (lat, rest...)
    out = np.empty((ENVNY,) + moved.shape[1:], dtype=np.float32)
    for j in range(ENVNY):
        y = dst_lat[j]
        # 源格点下标(线性)
        i = (y - src[0]) / (src[1] - src[0])
        i0 = int(math.floor(i))
        i1 = min(i0 + 1, n_lat - 1)
        i0 = max(i0, 0)
        f = i - i0
        out[j] = moved[i0] * (1 - f) + moved[i1] * f
    return np.moveaxis(out, 0, axis_lat)


def interp_month(months: np.ndarray, t_h: float, month0: int,
                 day_cycle: float = 1.0, daily_amp: float = 0.0) -> np.ndarray:
    """按模拟时间 t_h(小时)对 12 月数据做闭式月间插值 + 日内谐波。
    - 月间: 线性插值(跨年循环, 每月按 30 天近似); 月份基准 = month0(模拟起始月),
      t_h 只负责推进(起始 t=0 即 month0, 而非 1 月)。
    - 日内: 当地太阳时驱动的日变化 —— 每一格点的"当地时间"由
      模拟绝对小时 + 经度时角决定, 峰值在当地正午(太阳加热)。
      daily_amp 为**加性**幅度(绝对单位, 如 sst=0.6°C / mslp=1.5hPa),
      避免乘法调制把海温/气压整体放大(物理错误)。
      (借鉴 KWP 的逐时气候态思想: 日内环境随当地太阳时演变。)
    返回与 months 同形(经度最后一维, 纬度已对齐)的场。"""
    n_m = months.shape[0]
    day = t_h / 24.0
    m_float = ((month0 - 1) + day / 30.0) % 12.0
    m0 = int(math.floor(m_float)) % 12
    m1 = (m0 + 1) % 12
    f = m_float - math.floor(m_float)
    base = months[m0] * (1 - f) + months[m1] * f
    if day_cycle <= 0 or daily_amp <= 0:
        return base
    # 当地太阳时(0-24): 模型 t 的绝对小时(UTC 近似) + 经度时角
    lon = (np.arange(base.shape[-1]) + 0.5)          # 0..359 经度格心
    utc_hour = (t_h % 24.0 + 24.0) % 24.0
    local_h = (utc_hour + lon / 15.0) % 24.0          # 每经度当地小时
    # 日变化: 峰值在 14 时(下午最暖), 低谷在 02 时; 幅度随季节(夏大冬小)
    daily = np.cos(2 * math.pi * (local_h - 14.0) / 24.0)
    seas = 0.5 + 0.5 * math.cos(2 * math.pi * (m_float - 6) / 12.0)
    return base + daily * day_cycle * seas * daily_amp


def build_env_fields(sim_params: dict, t_h: float, month0: int) -> Optional[dict]:
    """按 dataMode 参数构建内核 env 场覆盖层。
    返回 {sst, ohc, shear, rh, u850, v850, u200, v200, uMid, vMid} 或 None(无数据)。
    sst/ohc/shear/rh 单位与内核一致: °C / kJ / kt / %; 风: kt(内核全 kt)。"""
    dm = sim_params.get('dataMode', False)
    if not dm:
        return None
    year = sim_params.get('dataYear')
    clim = bool(sim_params.get('dataClim', False)) or year is None
    dc = float(sim_params.get('dataDayCycle', 1.0))
    amp = DAILY_AMP

    def get3(name, damp=0.0):
        m = load_months(name, year, clim)
        if m is None:
            return None
        return interp_month(resample_lat(m), t_h, month0, dc, damp)

    def get4(name, damp=0.0):
        m = load_months(name, year, clim)
        if m is None:
            return None
        # m: (12, 2, 121, 360) -> 分 u/v 各重采样
        u = interp_month(resample_lat(m[:, 0]), t_h, month0, dc, damp)
        v = interp_month(resample_lat(m[:, 1]), t_h, month0, dc, damp)
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
        # raw 全年文件走与库相同的处理: 纬度重采样 + 月间/日内插值
        sst = interp_month(resample_lat(sst), t_h, month0, dc, amp['sst'])
    if sst is not None:
        fields['sst'] = sst
    ohc = get3('ohc', amp['ohc'])
    if ohc is not None:
        fields['ohc'] = ohc
    shear = get3('shear', amp['shear'])
    if shear is not None:
        # 库 shear 单元: m/s(见 real_source)。转 kt
        fields['shear'] = np.maximum(SHEAR_MIN_KT, shear * KT).astype(np.float32)
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
    return fields or None


# 单位说明: 库各变量单位(与 env/field_io FIELD_DEFS 对齐)
# sst °C, ohc kJ/cm², shear m/s, rh700 %, uv_steer m/s, mslp hPa
UNITS = {'sst': 'degC', 'ohc': 'kJ', 'shear': 'kt', 'rh': '%', 'wind': 'kt'}
