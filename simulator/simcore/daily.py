# -*- coding: utf-8 -*-
"""运行时**瞬时场**入口: ERA5 单日单时次 → 内核网格(240×720)。

用户 2026-09-20(重要): "风切及一切数据都要用瞬时, 除了一些特殊数据用长时间平均"。

为什么必须瞬时: |V200−V850| 用**月均**的风算出来的是"均值的矢量差", 比日尺度切变
系统性大 ~1.8 倍(实测全球均值 34.9kt / 最大 132kt, 而 CIRA 口径 ~17/65); SST 的
月气候态则把黑潮/锋面/中尺度涡全抹平。所以运行时的环境场一律优先取**该时次的瞬时
场**, 月气候态(base_sst 等)只当"没有单日数据时的兜底", 以及模态/指数这类本身就是
长时间平均的特殊产品。

缓存文件(由 tools/fetch_era5_day.py 生成):
  era5/day_<YYYYMMDD>_<HH>.nc    地面: sst(K), msl(Pa), u10/v10(m/s)
  era5/plev_<YYYYMMDD>_<HH>.nc   等压面: u/v(m/s), r(%), t(K) @ 200/300/500/850

返回(内核口径, 南在上 240×720):
  sst(°C) mslp(hPa) u850/v850/u200/v200(kt) rh(%) t200(K) shear(kt, 瞬时 |ΔV|)
"""
from __future__ import annotations

import logging
import os
import re

import numpy as np

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ERA5 = os.path.join(_ROOT, 'simulator', 'env', 'train_cache', 'era5')
CELL_NPZ = os.path.join(_ROOT, 'simulator', 'env', 'model', 'land_cell.npz')

# **网格与域从内核常量取**(2026-09-21 扩域 ±90): 原来写死 240×720/±60,
# 扩域后这里会把单日 ERA5/CMEMS 场重采样到错误的网格上。
from .core import ENVNY, ENVNX, C as _C          # noqa: E402
CMEMS = os.path.join(_ROOT, 'simulator', 'env', 'train_cache', 'cmems')
KT = 1.94384
#: 允许的时次偏差(小时): 找不到同小时就用最近的, 超过这个值视为没有单日数据
MAX_LAG_H = 6.0

#: 风切的 **TC 尺度平均**半径(度) —— SHIPS/CIRA 口径是 200-800 km 区域平均,
#: 高斯 sigma=1.75° 对应的有效平均半径约 2σ≈380 km ✓
TC_SHEAR_SCALE_DEG = 1.75

#: D26 的独立观测偏差修正(米, **加**) —— 与 tools/build_base_d26_validated.py 同一常数。
#: 依据: 与 AOML 12 个月逐格比 bias(AOML-CMEMS) = +14.6 m, 即 CMEMS 偏浅。
#: MPI 用 D26(米), 所以瞬时路径同样要补, 否则基态修正了、瞬时场没修正(不一致)。
D26_BIAS_M = 9.0

_CACHE = {}          # path -> (mtime, fields dict)
_LAND = None
_TRIED_LAND = False


def _cell_land():
    global _LAND, _TRIED_LAND
    if not _TRIED_LAND:
        _TRIED_LAND = True
        try:
            with np.load(CELL_NPZ) as z:
                _LAND = np.asarray(z['landfrac'], dtype=np.float32)
        except Exception:
            _LAND = None
    return _LAND


def _rel(p):
    """netCDF4 在 Windows 上按 locale 编码传路径, 中文仓库路径必须用相对路径。"""
    try:
        return os.path.relpath(p, _ROOT)
    except Exception:
        return p


def _nearest_hour(clock, hour: int):
    """在 era5/ 里找与 clock 最接近的已缓存时次(≤ MAX_LAG_H 小时)。"""
    if not os.path.isdir(ERA5):
        return None
    want = clock.replace(minute=0, second=0, microsecond=0)
    best = None
    pat = re.compile(r'^(day|plev)_(\d{8})_(\d{2})\.nc$')
    for f in os.listdir(ERA5):
        m = pat.match(f)
        if not m:
            continue
        y, mo, d = int(m.group(2)[:4]), int(m.group(2)[4:6]), int(m.group(2)[6:8])
        hh = int(m.group(3))
        try:
            from datetime import datetime as _dt
            t = _dt(y, mo, d, hh)
        except Exception:
            continue
        lag = abs((t - want).total_seconds()) / 3600.0
        if lag <= MAX_LAG_H and (best is None or lag < best[0]):
            best = (lag, t)
    return best[1] if best else None


def _nearest_path(kind: str, clock):
    """按**文件种类**找最近时次: 地面与等压面各自解析。

    2026-09-20 修: 原来用同一个 t 去拼 day/plev 两个路径, 只要两个文件的小时不同
    (很常见 —— 先抓地面、过一会儿才抓等压面), 就会有一个**静默缺失**: 实测模拟钟走到
    18Z 时 plev 拿不到, 风切整层回落月均(48.6 → 18.2 kt), 而 dataSrc 还显示"瞬时"。
    """
    if not os.path.isdir(ERA5):
        return None
    want = clock.replace(minute=0, second=0, microsecond=0)
    pat = re.compile(r'^%s_(\d{8})_(\d{2})\.nc$' % kind)
    best = None
    for f in os.listdir(ERA5):
        m = pat.match(f)
        if not m:
            continue
        try:
            from datetime import datetime as _dt
            t = _dt(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]),
                    int(m.group(2)))
        except Exception:
            continue
        lag = abs((t - want).total_seconds()) / 3600.0
        if lag <= MAX_LAG_H and (best is None or lag < best[0]):
            best = (lag, f)
    return os.path.join(ERA5, best[1]) if best else None


def _paths(t):
    hh = '%02d' % t.hour
    ymd = t.strftime('%Y%m%d')
    return (os.path.join(ERA5, 'day_%s_%s.nc' % (ymd, hh)),
            os.path.join(ERA5, 'plev_%s_%s.nc' % (ymd, hh)))


def _resample(src, src_lat, src_lon):
    """(nlat,nlon,北在上 or 南在上) → (240,720, 南在上), 双线性。"""
    from scipy import ndimage as ndi
    la = np.asarray(src_lat, dtype=np.float64)
    if la[0] > la[-1]:
        src = src[::-1]
        la = la[::-1]
    lo = np.asarray(src_lon, dtype=np.float64)
    # **跟随内核常量**(扩域到 ±90): 原写死 ±60/0.5°, 扩域后极区会被源数据拉伸伪造
    dst_lat = _C['LAT0'] + (np.arange(ENVNY) + 0.5) * _C['ENVD']
    dst_lon = _C['LON0'] + (np.arange(ENVNX) + 0.5) * _C['ENVD']
    fy = (dst_lat - la[0]) / (la[-1] - la[0]) * (src.shape[0] - 1)
    fx = (dst_lon - lo[0]) / (lo[-1] - lo[0]) * (src.shape[1] - 1)
    coords = np.array(np.meshgrid(fy, fx, indexing='ij'))
    return ndi.map_coordinates(np.asarray(src, dtype=np.float32), coords,
                               order=1, mode='nearest').astype(np.float32)


def _fill_land(a, warm=None, despike=False, zero_med=None, k=0.45, abs_min=20.0):
    """陆地/缺测格**平滑**补值(2026-09-21 改: 最近值会跨陆地/洋区边界跳变)。

    实测: 分洋区 OHC 的南边界正好在赤道, 最近值填充在赤道两侧留下 200 kJ 的硬接缝;
    平滑扩散填充把接缝抹平成渐变, 与用户看到的"东北太平洋 3-10 度附近极严重问题"对应。
    2026-09-21 二次修正: 那条接缝**不是**陆地填充造成的 —— 源文件 g2 里那一格本身就是
    14.1(邻格 187~223, 同格 SST 30.8°C), 是 CMEMS 的单格坏值。现在 despike=True 的场
    会把这种"比全部 8 个邻居都低/高一大截"的孤立尖峰一并当洞补掉。
    """
    from .fill import ocean_fill
    return ocean_fill(a, warm=warm, land=_cell_land(), despike=despike,
                      zero_med=zero_med, k=k, abs_min=abs_min)

def _read_surface(path):
    import netCDF4
    out = {}
    with netCDF4.Dataset(_rel(path)) as d:
        lat = np.asarray(d.variables['latitude'][:]).ravel()
        lon = np.asarray(d.variables['longitude'][:]).ravel()
        for key, name, conv in (('sst', 'sst', lambda a: a - 273.15),
                                ('mslp', 'msl', lambda a: a / 100.0),
                                ('u10', 'u10', lambda a: a * KT),
                                ('v10', 'v10', lambda a: a * KT)):
            v = d.variables.get(name)
            if v is None:
                continue
            a = np.asarray(v[:]).squeeze().astype(np.float32)
            out[key] = _fill_land(_resample(conv(a), lat, lon))
    return out


def _read_plev(path):
    import netCDF4
    out = {}
    with netCDF4.Dataset(_rel(path)) as d:
        lat = np.asarray(d.variables['latitude'][:]).ravel()
        lon = np.asarray(d.variables['longitude'][:]).ravel()
        lv_name = 'pressure_level' if 'pressure_level' in d.variables else (
            'level' if 'level' in d.variables else None)
        if lv_name is None:
            return out
        lv = np.asarray(d.variables[lv_name][:]).ravel().astype(int)
        # **t 要转成 °C**: 月基态 base_other.t200 是 °C(≈ -53), 而 ERA5 给的是 K(≈ 220)。
        # 内核用  的启发式能兜住两种单位, 但
        # 混用会让"月均兜底路径"和"瞬时路径"在数值上走不同分支(实测偏差 -272 就是
        # 这个单位差), 所以这里统一到 °C。
        for key, name, conv in (('u', 'u', lambda a: a * KT),
                                ('v', 'v', lambda a: a * KT),
                                ('r', 'r', lambda a: a),
                                ('t', 't', lambda a: a - 273.15)):
            v = d.variables.get(name)
            if v is None:
                continue
            a = np.asarray(v[:]).squeeze()
            if a.ndim == 3:                     # (level, lat, lon)
                for L in (200, 300, 500, 700, 850):
                    k = int(np.argmin(np.abs(lv - L)))
                    if abs(int(lv[k]) - L) > 5:
                        continue
                    out['%s%d' % (key, L)] = _fill_land(
                        _resample(conv(a[k]).astype(np.float32), lat, lon))
    return out


def _read_ohc_basins(clock):
    """**瞬时** OHC / D26: CMEMS 单日分洋区文件(ohc_<YMD>_{wpac,epac,natl}.npz)。

    这三个文件是单日 0.05° 场(实测 1997-09-15 有 wpac/epac/natl), 比月气候态
    精细得多; 拼成全球网格后陆地最近海面填充。找不到就返回 {} (上层用月气候态兜底)。
    """
    import glob as _glob
    out = {}
    if not os.path.isdir(CMEMS):
        return out
    want = clock.date()
    cands = []
    for f in _glob.glob(os.path.join(CMEMS, 'ohc_*_*.npz')):
        m = re.search(r'ohc_(\d{8})_(\w+)\.npz$', os.path.basename(f))
        if not m:
            continue
        try:
            from datetime import datetime as _dt
            t = _dt(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
        except Exception:
            continue
        lag = abs((t.date() - want).days)
        if lag <= 3:
            cands.append((lag, m.group(2), f))
    if not cands:
        return out
    lag_min = min(c[0] for c in cands)
    grid_o = np.zeros((ENVNY, ENVNX), dtype=np.float32)
    grid_d = np.zeros((ENVNY, ENVNX), dtype=np.float32)
    cover = np.zeros((ENVNY, ENVNX), dtype=bool)
    got_o = got_d = False
    for lag, basin, f in cands:
        if lag != lag_min:
            continue
        try:
            with np.load(f) as z:
                o = np.asarray(z['ohc'], dtype=np.float32) if 'ohc' in z else None
                d26 = np.asarray(z['d26'], dtype=np.float32) if 'd26' in z else None
                la = np.asarray(z['lat'], dtype=np.float64).ravel()
                lo = np.asarray(z['lon'], dtype=np.float64).ravel()
            # **经度必须归一化到 0..360**(2026-09-20 修): epac/natl 两个洋区文件用的
            # 是 -180..180, 而内核网格是 0..360; 原来按 dst_lon <= lo.max()+0.5 选列时
            # lo.max() = -80 → 一列都选不中 ⇒ 这两个洋区**从来没被合并进来**, 网格其余
            # 部分全靠"最近有效值"填充(实测东太赤道 OHV 被填成 152 而文件里是 34.6)。
            if lo.size and lo.min() < 0.0:
                order = np.argsort(lo % 360.0)
                lo = (lo % 360.0)[order]
                if o is not None:
                    o = o[:, order]
                if d26 is not None:
                    d26 = d26[:, order]
        except Exception:
            continue
        if o is None or d26 is None:
            continue
        # 只取落到内核网格里的那部分(洋区文件本身就是子域)
        dst_lat = -60.0 + (np.arange(ENVNY) + 0.5) * 0.5
        dst_lon = (np.arange(ENVNX) + 0.5) * 0.5
        iy = np.nonzero((dst_lat >= la.min() - 0.5) & (dst_lat <= la.max() + 0.5))[0]
        ix = np.nonzero((dst_lon >= lo.min() - 0.5) & (dst_lon <= lo.max() + 0.5))[0]
        if iy.size == 0 or ix.size == 0:
            continue
        # 该子域内的双线性重采样: 先把全网格算出来再挑(iy, ix), 简单可靠
        o_full = _resample(np.where(o <= 0, np.nan, o), la, lo)
        d_full = _resample(np.where(d26 <= 0, np.nan, d26), la, lo)
        sel = np.ix_(iy, ix)
        ok = np.isfinite(o_full[sel])
        grid_o[sel] = np.where(ok, o_full[sel], grid_o[sel])
        grid_d[sel] = np.where(ok, d_full[sel], grid_d[sel])
        cover[sel] = cover[sel] | ok
        got_o = got_o or bool(ok.any())
        got_d = got_d or bool(ok.any())
    if got_o and cover.any():
        # 未覆盖处(其它洋区/陆地)先留 0, 让上层用月气候态补; 这里只填有覆盖的部分
        o_f = np.where(cover, grid_o, np.nan)
        d_f = np.where(cover, grid_d, np.nan)
        out['ohc'] = _fill_land(o_f, warm=out.get('sst'), despike=True, zero_med=40.0)
        out['tchp'] = out['ohc']                   # 显示用 OHC(kJ/cm²)
        out['d26'] = (_fill_land(d_f, warm=out.get('sst'), despike=True, zero_med=45.0)
                      + D26_BIAS_M).astype(np.float32)
        out['_ohc_instant'] = True
        out['_ohc_basins'] = sorted({b for lag, b, _ in cands if lag == lag_min})
    return out


def load_fields(clock, allow_partial=True):
    """该时次的**瞬时**环境场(内核口径); 没有单日数据返回 {} / None。

    allow_partial=True: 有地面文件就先用地面(如只有 sst), 缺的字段由上层用月气候态补。
    """
    if clock is None:
        return {}
    out = {}
    # ① 瞬时 OHC / D26(CMEMS 分洋区**单日**场): 与 day_/plev_ 时次文件无关,
    #    只要该日有文件就能用(实测 1997-09-15、2026-09-13 都有 wpac/epac/natl)
    try:
        out.update(_read_ohc_basins(clock))
    except Exception:
        logger.debug('瞬时 OHC 读取失败', exc_info=True)
    # ② 瞬时地面/等压面(ERA5 单日单时次)
    t = _nearest_hour(clock, getattr(clock, 'hour', 12))
    srcs = []
    _sp = _nearest_path('day', clock)
    _pp = _nearest_path('plev', clock)
    if _sp is None and t is not None:
        _sp = _paths(t)[0]
    if _pp is None and t is not None:
        _pp = _paths(t)[1]
    for path, reader in ((_sp, _read_surface), (_pp, _read_plev)):
        if path is None or not os.path.exists(path):
            continue
        try:
            st = os.stat(path)
            key = (path, st.st_mtime, st.st_size)
            hit = _CACHE.get(key)
            if hit is None:
                hit = reader(path)
                _CACHE[key] = hit
                if len(_CACHE) > 12:
                    _CACHE.pop(next(iter(_CACHE)))
            out.update(hit)
            srcs.append(os.path.basename(path))
        except Exception:
            logger.debug('瞬时场读取失败 %s', path, exc_info=True)
    # 键名对齐内核契约: env['rh'] 用的是 700 hPa, 而月基态那边叫 rh700
    # (旧版抓取没带 700 hPa, 所以老缓存文件里没有 r700 —— 那就回落到月基态, 不报错)
    if 'r700' in out and 'rh700' not in out:
        out['rh700'] = out['r700']
    # ③ 瞬时风切: |V200 − V850|, **不乘任何系数**(月均口径才需要 0.55 补偿)
    #    **按 TC 尺度平均**(2026-09-20): SHIPS/CIRA 口径的"风切"是台风周围
    #    200-800 km 的 200-850 hPa 矢量差, 不是单格点瞬时值 —— 单点瞬时在急流核
    #    能到 166 kt(实测), 拿去做强度方程会把一切都压死。这里保留原始点值
    #    (, 供显示/诊断), 另给 TC 尺度平均后的  供强度使用。
    if all(k in out for k in ('u200', 'v200', 'u850', 'v850')):
        du = (out['u200'] - out['u850']).astype(np.float32)
        dv = (out['v200'] - out['v850']).astype(np.float32)
        out['shear_raw'] = np.hypot(du, dv).astype(np.float32)
        try:
            from scipy import ndimage as _ndi
            _sg = float(TC_SHEAR_SCALE_DEG) / 0.5          # 网格 0.5° → 格数
            _su = _ndi.gaussian_filter(du, sigma=(_sg, _sg),
                                       mode=('nearest', 'wrap'))
            _sv = _ndi.gaussian_filter(dv, sigma=(_sg, _sg),
                                       mode=('nearest', 'wrap'))
            out['shear'] = np.hypot(_su, _sv).astype(np.float32)
            out['_shear_tc_scale_deg'] = float(TC_SHEAR_SCALE_DEG)
        except Exception:
            logger.debug('TC 尺度平滑失败(用点值)', exc_info=True)
            out['shear'] = out['shear_raw']
        out['_shear_instant'] = True
    if out:
        _bits = []
        if srcs:
            _bits.append('ERA5 %s 瞬时' % t.strftime('%Y-%m-%d %HZ'))
        if out.get('_ohc_instant'):
            _bits.append('CMEMS %s 单日 OHC(%s)'
                         % (clock.date(), ','.join(out.get('_ohc_basins') or [])))
        out['_source'] = ' + '.join(_bits) or '瞬时'
        out['_time'] = t or clock
    return out if out else {}


def coverage():
    """瞬时数据的可用窗口(供 UI 如实告知)。

    三类数据的窗口**天然不同**, 这点必须让用户看到:
      * ERA5 单日(day/plev): 只有个别时次, 模拟钟离开它 ±MAX_LAG_H 小时就回落月均;
      * CMEMS 单日洋区 OHC: ±3 天;
      * 基态/月气候态: 全年。
    返回 {'era5': (最早, 最晚, 个数), 'ohc': (...), 'era5_kinds': {...}}
    """
    import re as _re
    from datetime import datetime as _dt
    out = {'era5': None, 'ohc': None, 'era5_kinds': {}, 'dir': ERA5}
    pat = _re.compile(r'^(day|plev)_(\d{8})_(\d{2})\.nc$')
    times = {'day': [], 'plev': []}
    try:
        for f in os.listdir(ERA5):
            m = pat.match(f)
            if not m:
                continue
            try:
                times[m.group(1)].append(_dt(int(m.group(2)[:4]), int(m.group(2)[4:6]),
                                             int(m.group(2)[6:8]), int(m.group(3))))
            except Exception:
                continue
    except Exception:
        pass
    all_t = sorted(times['day'] + times['plev'])
    if all_t:
        out['era5'] = (all_t[0], all_t[-1], len(all_t))
    for k, v in times.items():
        if v:
            out['era5_kinds'][k] = (min(v), max(v), len(v))
    try:
        ohc = []
        for f in os.listdir(CMEMS):
            m = _re.match(r'^ohc_(\d{8})_', f)
            if m:
                ohc.append(_dt(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8])))
        if ohc:
            out['ohc'] = (min(ohc), max(ohc), len(ohc))
    except Exception:
        pass
    return out


def available_days():
    """已缓存的单日时次列表(诊断用)。"""
    if not os.path.isdir(ERA5):
        return []
    pat = re.compile(r'^(day|plev)_(\d{8})_(\d{2})\.nc$')
    ts = set()
    for f in os.listdir(ERA5):
        m = pat.match(f)
        if m:
            ts.add((m.group(2), m.group(3)))
    return sorted(ts)
