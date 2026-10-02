# -*- coding: utf-8 -*-
"""F2: 离线训练"基态气候场"(base state), 烘焙成运行时零外部数据可用的 npz。

数据源(全部免 key, 已验证可达):
  SST         NOAA OISST v2.1 0.25° 月气候态 1991-2020   (PSL THREDDS fileServer)
  D26 / TCHP  AOML 0.25° (ERDDAP, 每月 15 日 12z → 月气候态)
  风/湿度/气温 NCEP/NCAR Reanalysis 1 月均 2.5°           (PSL OPeNDAP, 服务端切片)
  长序列 SST  NOAA ERSSTv5 2° 1854-今(供 F3 模态/增暖用)

输出(全部 float16, 直接进 simulator/env/model/):
  base_sst.npz     sst    (12,240,720)   0.5°
  base_d26.npz     d26    (12,240,720)   0.5°   (26°C 等温线深度, m)
  base_shear.npz   shear  (12,240,720)   0.5°   (|V200-V850|, kt)
  base_wind.npz    u/v    (12,120,360)   1°     (850/500/300/200 hPa, kt)
  base_other.npz   rh700/mslp/t200/slp  (12,120,360) 1°

用法:
  python tools/train_env_base.py --stage fetch     # 只下载(慢, 可后台)
  python tools/train_env_base.py --stage build     # 只建库(需已下载)
  python tools/train_env_base.py --stage all
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
PSL_FILE = 'https://psl.noaa.gov/thredds/fileServer/'
PSL_DAP = 'https://psl.noaa.gov/thredds/dodsC/'
AOML = 'https://cwcgom.aoml.noaa.gov/erddap/griddap/aomlTCHP.nc?'

CLIM_YEARS = (1991, 2020)        # 基准期
# AOML 这个 ERDDAP 数据集(aomlTCHP)是 **~4.7 年滚动窗口**(实测 2026-09
# 时只有 2022-01-01 ~ 2026-09-09), 不是 1993 起的长序列 —— 想要长序列得走
# AOML 的按年归档或 ORAS5。这里用可得窗口建月气候态(每月 4~5 个样本,
# 偏噪但都是真值), 并记录覆盖区间供上层标注。
D26_YEARS = (2022, 2026)
LAT0, LAT1 = -60.0, 60.0         # 内核计算域


# ── 通用下载 ────────────────────────────────────────────────────

def _get(url, out, retries=3, timeout=600, min_size=1000):
    import urllib.request
    if os.path.exists(out) and os.path.getsize(out) >= min_size:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r, \
                    open(out + '.part', 'wb') as f:
                n = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    n += len(b)
            if n >= min_size:
                os.replace(out + '.part', out)
                print('  [get] %s  %.1f MB' % (os.path.basename(out), n / 1e6),
                      flush=True)
                return out
            print('  [get] 太小(%d), 重试' % n, flush=True)
        except Exception as e:
            print('  [get] 失败(%d/%d): %s' % (k + 1, retries, str(e)[:90]),
                  flush=True)
            time.sleep(3)
    return None


# ── fetch ──────────────────────────────────────────────────────

def fetch_dap_sst():
    """OISST LTM + ERSSTv5 用 OPeNDAP 服务端切片。

    fileServer 直下 46MB 实测 7 分钟才 8MB(PSL 限速); 走 dodsC 只取需要的
    纬度带, 40 秒级完成。
    """
    import netCDF4
    os.makedirs(CACHE, exist_ok=True)
    # ── OISST 0.25° 月气候态 1991-2020 ──
    fp = os.path.join(CACHE, 'oisst_ltm.npz')
    if not os.path.exists(fp):
        for attempt in range(3):
            try:
                d = netCDF4.Dataset(PSL_DAP +
                    'Datasets/noaa.oisst.v2.highres/sst.mon.ltm.1991-2020.nc')
                la = np.asarray(d.variables['lat'][:])
                lo = np.asarray(d.variables['lon'][:])
                j = np.nonzero((la >= LAT0) & (la <= LAT1))[0]
                a = np.asarray(d.variables['sst'][:, j[0]:j[-1] + 1, :], np.float32)
                d.close()
                np.savez_compressed(fp, sst=a, lat=la[j[0]:j[-1] + 1], lon=lo)
                print('  OISST LTM %s  %.1f~%.1f°C' % (a.shape, a.min(), a.max()),
                      flush=True)
                break
            except Exception as e:
                print('  OISST 失败(%d/3) %s' % (attempt + 1, str(e)[:90]), flush=True)
                time.sleep(5)

    # ── ERSSTv5 2° 月均 1854-今(供 F3 长序列) ──
    fp = os.path.join(CACHE, 'ersst5_mon.npz')
    if not os.path.exists(fp):
        for attempt in range(3):
            try:
                d = netCDF4.Dataset(PSL_DAP + 'Datasets/noaa.ersst.v5/sst.mnmean.nc')
                t = np.asarray(d.variables['time'][:])
                dt = netCDF4.num2date(t, d.variables['time'].units,
                                      only_use_cftime_datetimes=False)
                la = np.asarray(d.variables['lat'][:])
                lo = np.asarray(d.variables['lon'][:])
                j = np.nonzero((la >= LAT0) & (la <= LAT1))[0]
                a = np.asarray(d.variables['sst'][:, j[0]:j[-1] + 1, :], np.float32)
                d.close()
                yr = np.array([x.year for x in dt], np.int16)
                mo = np.array([x.month for x in dt], np.int8)
                np.savez_compressed(fp, sst=a, lat=la[j[0]:j[-1] + 1], lon=lo,
                                    year=yr, month=mo)
                print('  ERSSTv5 %s  %d-%d' % (a.shape, yr.min(), yr.max()),
                      flush=True)
                break
            except Exception as e:
                print('  ERSST 失败(%d/3) %s' % (attempt + 1, str(e)[:90]), flush=True)
                time.sleep(5)


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    print('== 1/4 OISST + ERSSTv5(OPeNDAP) ==', flush=True)
    fetch_dap_sst()

    print('== 3/4 AOML D26/TCHP 月气候态(ERDDAP 逐月子集) ==', flush=True)
    y0, y1 = D26_YEARS
    ok = 0
    for y in range(y0, y1 + 1):
        fp = os.path.join(CACHE, 'aoml_d26_%d.npz' % y)
        if os.path.exists(fp):
            ok += 1
            continue
        arrs = []
        bad = False
        for m in range(1, 13):
            ts = '%d-%02d-15T12:00:00Z' % (y, m)
            # 经度轴实测是 -180..180(不是 0..360) —— 按 0..358 请求会 404
            # ("Stop=358.0 is greater than the axis maximum=180.0")
            q = ('D26%%5B(%s):1:(%s)%%5D%%5B(%g):2:(%g)%%5D%%5B(-180.0):4:(180.0)%%5D,'
                 'Tropical_Cyclone_Heat_Potential%%5B(%s):1:(%s)%%5D'
                 '%%5B(%g):2:(%g)%%5D%%5B(-180.0):4:(180.0)%%5D'
                 % (ts, ts, LAT0, LAT1, ts, ts, LAT0, LAT1))
            tmp = os.path.join(CACHE, '_a.nc')
            if _get(AOML + q, tmp, retries=2, timeout=120, min_size=1000) is None:
                bad = True
                break
            try:
                import netCDF4
                d = netCDF4.Dataset(tmp)
                z = np.asarray(d.variables['D26'][:]).squeeze()
                t = np.asarray(d.variables['Tropical_Cyclone_Heat_Potential'][:]).squeeze()
                d.close()
            except Exception as e:
                print('   读 %d-%02d 失败 %s' % (y, m, str(e)[:60]), flush=True)
                bad = True
                break
            arrs.append(np.stack([z, t]))
        if bad or len(arrs) != 12:
            print('   年份 %d 不完整, 跳过' % y, flush=True)
            continue
        np.savez_compressed(fp, data=np.asarray(arrs, np.float32))
        ok += 1
        print('  aoml %d 完成 (%d/%d)' % (y, ok, y1 - y0 + 1), flush=True)
    print('  AOML 年份: %d' % ok, flush=True)

    print('== 4/4 NCEP R1 月均(OPeNDAP 服务端切片 1991-2020) ==', flush=True)
    fetch_ncep()


NCEP_FILES = {
    'uwnd': 'Datasets/ncep.reanalysis.derived/pressure/uwnd.mon.mean.nc',
    'vwnd': 'Datasets/ncep.reanalysis.derived/pressure/vwnd.mon.mean.nc',
    'rhum': 'Datasets/ncep.reanalysis.derived/pressure/rhum.mon.mean.nc',
    'slp': 'Datasets/ncep.reanalysis.derived/surface/slp.mon.mean.nc',
}
NCEP_LEVELS = (850, 500, 300, 200)


def fetch_ncep():
    import netCDF4
    for var, path in NCEP_FILES.items():
        fp = os.path.join(CACHE, 'ncep_%s.npz' % var)
        if os.path.exists(fp):
            print('  %s 已缓存' % var, flush=True)
            continue
        try:
            d = netCDF4.Dataset(PSL_DAP + path)
            t = np.asarray(d.variables['time'][:])
            dt = netCDF4.num2date(t, d.variables['time'].units,
                                  only_use_cftime_datetimes=False)
            yrs = np.array([x.year for x in dt])
            sel = np.nonzero((yrs >= CLIM_YEARS[0]) & (yrs <= CLIM_YEARS[1]))[0]
            lat = np.asarray(d.variables['lat'][:])
            lon = np.asarray(d.variables['lon'][:])
            # NCEP 的 lat 是**降序**(90→-90), 直接 lat[j0:j1+1] 会切出空数组
            # (j0 指向 -60 在下标大的一侧)。统一取 min/max。
            a_ = int(np.argmin(abs(lat - LAT0)))
            b_ = int(np.argmin(abs(lat - LAT1)))
            j0, j1 = min(a_, b_), max(a_, b_)
            out = {}
            if 'level' in d.variables:
                lev = np.asarray(d.variables['level'][:])
                for L in NCEP_LEVELS:
                    k = int(np.argmin(abs(lev - L)))
                    a = np.asarray(d.variables[var][sel, k, j0:j1 + 1, :],
                                   np.float32)
                    out['%s%d' % (var, L)] = a
            else:
                out[var] = np.asarray(d.variables[var][sel, j0:j1 + 1, :],
                                      np.float32)
            d.close()
            # 月份标签, 供气候态按月聚合
            mon = np.array([x.month for x in dt])[sel]
            out['_month'] = mon
            out['_lat'] = lat[j0:j1 + 1]
            out['_lon'] = lon
            np.savez_compressed(fp, **out)
            print('  %s -> %s  keys=%s' % (var, os.path.basename(fp),
                                           [k for k in out if not k.startswith('_')]),
                  flush=True)
        except Exception as e:
            print('  %s 失败: %s' % (var, str(e)[:120]), flush=True)


# ── build ──────────────────────────────────────────────────────

def _fill_nan(a, mask=None):
    """填 NaN(陆地/冰盖) —— **沿纬度插值会产生纬向假条带, 已废弃**。

    为什么原来那样写是错的: 源数据里陆地是 NaN, 原来对**每一列沿纬度**线性插值,
    于是岸线复杂的海区(东海/琉球、墨西哥湾、加勒比、日本海)会被填出一条条
    **水平条纹** —— 因为一列上跨过陆地缺口时的插值是沿经线方向拉直的, 与真实
    海洋结构无关。实测这正是"sst 场看着碎"和"琉球附近莫名低海温"的一个来源。

    改法: 用**各向同性**的最近邻填充(多轮膨胀, 每次只填紧邻有效值的格点),
    这样填充值来自四面八方的最近有效观测, 不会拉出纬向条纹。

    另外: 源数据的 _FillValue 往往是**有限值**(ERA5/CMEMS 都是 9.97e36),
    isfinite 拦不住 —— mask 参数用来在调用侧按物理范围先标出无效格点。
    """
    a = np.asarray(a, np.float32).copy()
    bad = ~np.isfinite(a)
    if mask is not None:
        bad |= np.asarray(mask, bool)
    if not bad.any():
        return a
    a[bad] = np.nan
    # 各向同性最近邻: 反复用 8 邻域均值扩散, 直到没有 NaN。
    # **只 pad 最后两维**(纬度/经度) —— pad 整个数组会把前导的月份轴也撑开,
    # 切片后形状对不上(实测 (12,480,1440) vs (12,480,1442) 广播失败)。
    pad = [(0, 0)] * (a.ndim - 2) + [(1, 1), (1, 1)]
    for _ in range(80):
        m = np.isnan(a)
        if not m.any():
            break
        p = np.pad(a, pad, mode='edge')
        nb = np.stack([p[..., 0:-2, 1:-1], p[..., 2:, 1:-1],
                       p[..., 1:-1, 0:-2], p[..., 1:-1, 2:],
                       p[..., 0:-2, 0:-2], p[..., 0:-2, 2:],
                       p[..., 2:, 0:-2], p[..., 2:, 2:]])
        with np.errstate(invalid='ignore'):
            avg = np.nanmean(nb, axis=0)
        a = np.where(m & np.isfinite(avg), avg, a)
    return np.where(np.isnan(a), 0.0, a)


def _sane_mask(a, valid):
    """按物理范围标出无效格点。

    源数据的 _FillValue 常常是**有限值**(ERA5/CMEMS/OISST 都是 9.97e36),
    isfinite 拦不住 —— 它会一路混进均值(实测把 base_sst 打成 -inf、
    OHC 打到 9.8e38)。所以每个场都要给一个物理上下界。
    """
    if valid is None:
        return None
    lo, hi = valid
    a = np.asarray(a)
    return ~np.isfinite(a) | (a < lo) | (a > hi)


def _to_half_deg(a, src_lat, src_lon, valid=None):
    """(…,nlat,nlon) → (…,240,720) 0.5°, 经度周期。"""
    import numpy as np
    a = _fill_nan(a, _sane_mask(a, valid))
    # **目标网格从内核常量取**(2026-09-21 扩域到 ±90): 原来写死 240/720/±60,
    # 扩域后这套重采样会把 ±60 的源数据"拉"到 ±90 的画布上(极区被拉伸伪造)。
    from simulator.simcore import core as _V
    dst_lat = _V.C['LAT0'] + (np.arange(_V.ENVNY) + 0.5) * _V.C['ENVD']
    dst_lon = _V.C['LON0'] + (np.arange(_V.ENVNX) + 0.5) * _V.C['ENVD']
    src_lat = np.asarray(src_lat, float)
    src_lon = np.asarray(src_lon, float)
    if src_lat[0] > src_lat[-1]:
        a = a[..., ::-1, :]
        src_lat = src_lat[::-1]
    j0 = np.clip(np.searchsorted(src_lat, dst_lat) - 1, 0, src_lat.size - 2)
    fj = (dst_lat - src_lat[j0]) / (src_lat[j0 + 1] - src_lat[j0])
    lon_wrapped = np.where(src_lon < 0, src_lon + 360.0, src_lon)
    order = np.argsort(lon_wrapped)
    lw = lon_wrapped[order]
    aw = a[..., :, order]
    lw_ext = np.concatenate([lw - 360.0, lw, lw + 360.0])
    aw_ext = np.concatenate([aw, aw, aw], axis=-1)
    i0 = np.clip(np.searchsorted(lw_ext, dst_lon) - 1, 0, lw_ext.size - 2)
    fi = (dst_lon - lw_ext[i0]) / (lw_ext[i0 + 1] - lw_ext[i0])
    tmp = aw_ext[..., :, i0] * (1 - fi) + aw_ext[..., :, i0 + 1] * fi
    out = tmp[..., j0, :] * (1 - fj)[:, None] + tmp[..., j0 + 1, :] * fj[:, None]
    return out.astype(np.float32)


def _to_one_deg(a, src_lat, src_lon, valid=None):
    a = _fill_nan(a, _sane_mask(a, valid))
    from simulator.simcore import core as _V
    dst_lat = _V.C['LAT0'] + (np.arange(_V.CLDNY) + 0.5) * _V.C['CLDD']
    dst_lon = _V.C['LON0'] + (np.arange(_V.CLDNX) + 0.5) * _V.C['CLDD']
    src_lat = np.asarray(src_lat, float)
    src_lon = np.asarray(src_lon, float)
    if src_lat[0] > src_lat[-1]:
        a = a[..., ::-1, :]
        src_lat = src_lat[::-1]
    j0 = np.clip(np.searchsorted(src_lat, dst_lat) - 1, 0, src_lat.size - 2)
    fj = (dst_lat - src_lat[j0]) / (src_lat[j0 + 1] - src_lat[j0])
    lw = np.where(src_lon < 0, src_lon + 360.0, src_lon)
    order = np.argsort(lw)
    lw = lw[order]
    aw = a[..., order]
    lw_ext = np.concatenate([lw - 360.0, lw, lw + 360.0])
    aw_ext = np.concatenate([aw, aw, aw], axis=-1)
    i0 = np.clip(np.searchsorted(lw_ext, dst_lon) - 1, 0, lw_ext.size - 2)
    fi = (dst_lon - lw_ext[i0]) / (lw_ext[i0 + 1] - lw_ext[i0])
    tmp = aw_ext[..., i0] * (1 - fi) + aw_ext[..., i0 + 1] * fi
    out = tmp[..., j0, :] * (1 - fj)[:, None] + tmp[..., j0 + 1, :] * fj[:, None]
    return out.astype(np.float32)


def _load_oisst_ltm():
    """读 OISST v2.1 的 1991-2020 月气候态 nc → (12,240,720) °C。

    这是 SST 基态的首选: 30 年气候态(不含某一年的 ENSO 状态) + 卫星/浮标
    融合(与用户用的 REMSS 同源), 而 ERA5 的 SST 在 EPAC 赤道冷舌有强冷偏差。
    """
    import netCDF4
    p = os.path.join(CACHE, 'oisst_ltm.nc')
    if not os.path.exists(p):
        return None, None, None
    try:
        d = netCDF4.Dataset(p)
        key = 'sst' if 'sst' in d.variables else list(d.variables)[-1]
        a = np.asarray(d.variables[key][:], np.float32)
        la = np.asarray(d.variables['lat'][:])
        lo = np.asarray(d.variables['lon'][:])
        d.close()
    except Exception as e:
        print('  读 OISST 失败 %s' % str(e)[:80], flush=True)
        return None, None, None
    if a.ndim == 4:
        a = a[:, 0]
    if la[0] > la[-1]:
        a = a[:, ::-1, :]
        la = la[::-1]
    # OISST 的缺测是 _FillValue(9.97e36, **是有限值**), 只查 isfinite 会漏,
    # 直接把均值打成 -inf。SST(°C)不可能超过 60, 用它当上界最稳。
    a = np.where(np.isfinite(a) & (a < 60.0) & (a > -5.0), a, np.nan)
    lat_s = (la >= LAT0) & (la <= LAT1)
    return _to_half_deg(a[:, lat_s, :], la[lat_s], lo, valid=(-3.0, 45.0)), la[lat_s], lo


def _load_era5_months(year=2024):
    """读 train_cache/era5/sstm_<year>_<mm>.nc(真 ERA5 0.25°) → (12,240,720)。"""
    import netCDF4
    d0 = os.path.join(CACHE, 'era5')
    files = [os.path.join(d0, 'sstm_%d_%02d.nc' % (year, m)) for m in range(1, 13)]
    if not all(os.path.exists(f) for f in files):
        return None, None, None
    out = []
    la = lo = None
    for f in files:
        try:
            d = netCDF4.Dataset(f)
            key = 'sst' if 'sst' in d.variables else None
            if key is None:
                return None, None, None
            a = np.asarray(d.variables[key][:], np.float32).squeeze()
            la = np.asarray(d.variables['latitude'][:])
            lo = np.asarray(d.variables['longitude'][:])
            d.close()
        except Exception as e:
            print('  读 %s 失败 %s' % (os.path.basename(f), str(e)[:60]), flush=True)
            return None, None, None
        if a.ndim == 3:
            a = a[0]
        if a.max() > 200.0:
            a = a - 273.15                      # K → °C
        if la[0] > la[-1]:
            a = a[::-1, :]
            la = la[::-1]
        out.append(a)
    return _to_half_deg(np.stack(out), la, lo, valid=(-3.0, 45.0)), la, lo


def _load_raw_era5_year(year=1979):
    """读 simulator/env/raw/sst_<year>_<mm>.nc(真 ERA5, 1°) → (12,240,720)。"""
    import netCDF4
    raw = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'simulator', 'env', 'raw')
    raw = os.path.normpath(raw)
    files = [os.path.join(raw, 'sst_%d_%02d.nc' % (year, m)) for m in range(1, 13)]
    if not all(os.path.exists(f) for f in files):
        return None, None, None
    out = []
    la = lo = None
    for f in files:
        try:
            d = netCDF4.Dataset(f)
            a = np.asarray(d.variables['sst'][:], np.float32).squeeze() - 273.15
            la = np.asarray(d.variables['latitude'][:])
            lo = np.asarray(d.variables['longitude'][:])
            d.close()
        except Exception as e:
            print('  读 %s 失败 %s' % (os.path.basename(f), str(e)[:60]), flush=True)
            return None, None, None
        if la[0] > la[-1]:
            a = a[::-1, :]
            la = la[::-1]
        out.append(a)
    return _to_half_deg(np.stack(out), la, lo, valid=(-3.0, 45.0)), la, lo


def _shift_ym(y, m, n):
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def deenso_sst(a, years, months):
    """把基态里的 ENSO 信号减掉, 得到"中性年"气候态。

    为什么必须做: 基态如果只取一年, 那一年的 ENSO 状态就被烤进了"气候态"。
    实测用 ERA5 2024(发展中拉尼娜)当基态, EPAC 赤道(0-5N)比 2026-09 的观测
    冷 **2.96°C**, 模态+增暖只贡献 -0.25 —— 也就是错在基态本身。

    做法: 用 modes_pat 里已标定好的 ONI 型(°C/σ)去减那一年的 ENSO 贡献。
    这是"去掉年际信号"的标准做法, 比直接换年份快得多; 多年平均到位后可以不用。
    """
    import numpy as np
    mp_path = os.path.join(MODEL, 'modes_pat.npz')
    mi_path = os.path.join(MODEL, 'modes_index.npz')
    if not (os.path.exists(mp_path) and os.path.exists(mi_path)):
        return a, 'no-patterns'
    mp = np.load(mp_path, allow_pickle=False)
    mi = np.load(mi_path, allow_pickle=False)
    if 'sst__oni' not in mp:
        return a, 'no-oni-pattern'
    names = [str(x) for x in mi['names']]
    k = names.index('oni')
    val, ok = mi['val'], mi['ok']
    y_lo = int(mi['y_lo'])
    T = val.shape[1]
    pats = np.asarray(mp['sst__oni'], np.float32)
    lags = np.asarray(mp['lag_oni'], np.int32)
    out = a.copy()
    hit = 0
    for i in range(12):
        y, m = years[i], months[i]
        acc = np.zeros(pats.shape[1:], np.float64)
        any_ok = False
        for j, L in enumerate(lags):
            yy, mm = _shift_ym(y, m, int(L))
            t = (yy - y_lo) * 12 + (mm - 1)
            if 0 <= t < T and ok[k, t]:
                acc += pats[j] * float(val[k, t])
                any_ok = True
        if not any_ok:
            continue
        # 2.5° → 0.5° 双线性
        out[i] -= _bilinear_to(acc.astype(np.float32), out.shape[1:])
        hit += 1
    return out, 'deenso(%d/12)' % hit


def _bilinear_to(a, shape):
    ny, nx = shape
    yy = np.clip((np.arange(ny) + 0.5) * a.shape[0] / ny - 0.5, 0, a.shape[0] - 1)
    xx = np.clip((np.arange(nx) + 0.5) * a.shape[1] / nx - 0.5, 0, a.shape[1] - 1)
    j0 = np.floor(yy).astype(np.int64); i0 = np.floor(xx).astype(np.int64)
    j1 = np.minimum(j0 + 1, a.shape[0] - 1); i1 = np.minimum(i0 + 1, a.shape[1] - 1)
    fj = (yy - j0).astype(np.float32)[:, None]; fi = (xx - i0).astype(np.float32)[None, :]
    t1 = a[j0][:, i0] * (1 - fi) + a[j0][:, i1] * fi
    t2 = a[j1][:, i0] * (1 - fi) + a[j1][:, i1] * fi
    return t1 * (1 - fj) + t2 * fj


def _cmems_daily_ready():
    """CMEMS 全球单日是否已就位(12 个月)。

    就位时**不要**用 OISST 气候态 / AOML 覆盖 base_sst/base_tchp/base_d26 ——
    CMEMS 单日是当前最优来源(单日不做时段平均 + 0.083° 原生, 能解析黑潮/湾流),
    而实测 --stage build 会把它们整个写回旧来源。要重建用
    tools/restore_cmems_base.py。
    """
    import glob as _g
    return len(_g.glob(os.path.join(CACHE, 'cmems', 'glob_*.npz'))) >= 12


def build():
    import netCDF4
    os.makedirs(MODEL, exist_ok=True)
    if _cmems_daily_ready():
        print('!! 检测到 CMEMS 全球单日(12 个月)已就位, **跳过** SST/OHC/D26 重建。')
        print('   要重建这三个基态请跑 tools/restore_cmems_base.py')
        print('   (CMEMS 单日是当前最优来源; 这条分支防止被 OISST/AOML 覆盖)')
        return

    # ── SST ──
    # 优先 OISST 0.25° 1991-2020 气候态; 拿不到时退回**仓库里现成的真 ERA5
    # 1979 月场**(simulator/env/raw/sst_1979_*.nc, 1°)—— 单年、1°, 比合成库
    # 好一个量级(合成库在湾流/马尾藻海偏冷 7°C), 但不是气候态, 明确标注。
    p = os.path.join(CACHE, 'oisst_ltm.npz')
    src = 'OISST 0.25° 1991-2020 气候态'
    if os.path.exists(p):
        z = np.load(p)
        a, la, lo = z['sst'], z['lat'], z['lon']
        a = _to_half_deg(a, la, lo, valid=(-3.0, 45.0))
    elif os.path.exists(os.path.join(CACHE, 'oisst_ltm.nc')):
        a, la, lo = _load_oisst_ltm()
        src = 'OISST v2.1 1991-2020 月气候态(0.25°, 卫星融合)'
    else:
        a, la, lo = _load_era5_months()
        src = 'ERA5 逐月(0.25°)'
        if a is None:
            a, la, lo = _load_raw_era5_year()
            src = 'ERA5 raw 1979 月场(1°, 过渡)'
        if a is None:
            print('缺 OISST / ERA5 逐月 / raw ERA5, 跳过 SST', flush=True)
    if a is not None:
        # 安全网: 任何来源拿到的是 K 就统一换成 °C(CDS 的 ERA5 月均是 K)
        if float(np.nanmean(a)) > 200.0:
            a = a - 273.15
            print('  (SST 单位 K → °C)', flush=True)
        # 基态去 ENSO: 只对**单年**基态有意义(30 年气候态本身已中性)
        if 'OISST' not in src:
            try:
                yrs = [int(src.split()[1])] * 12 if 'ERA5' in src else [2024] * 12
                a, tag = deenso_sst(a, yrs, list(range(1, 13)))
                print('  (基态去 ENSO: %s)' % tag, flush=True)
            except Exception as ex:
                print('  (去 ENSO 跳过: %s)' % str(ex)[:80], flush=True)
        np.savez_compressed(os.path.join(MODEL, 'base_sst.npz'),
                            sst=a.astype(np.float16))
        print('base_sst.npz', a.shape, '范围 %.1f~%.1f 均值 %.1f  [%s]'
              % (a.min(), a.max(), a.mean(), src), flush=True)

    # ── D26 / TCHP: AOML 月气候态 ──
    zs, ts = [], []
    for y in range(D26_YEARS[0], D26_YEARS[1] + 1):
        fp = os.path.join(CACHE, 'aoml_d26_%d.npz' % y)
        if os.path.exists(fp):
            zs.append(np.load(fp)['data'])
    if zs:
        allz = np.concatenate(zs, 0)                     # (ny*12, 2, nlat, nlon)
        n = allz.shape[0] // 12 * 12
        allz = allz[:n]
        ny_ = n // 12
        # 每个文件是一年的 12 个月 → 按 (年, 月) 重排后对**年**求均值,
        # 才得到 12 个月的月气候态。原来 .mean(1) 把月给平均掉了(只剩 4 个"月")。
        clim = allz.reshape(ny_, 12, 2, allz.shape[-2],
                            allz.shape[-1]).mean(0)      # (12, 2, nlat, nlon)
        d26 = np.clip(clim[:, 0], 0, 400)
        tchp = np.clip(clim[:, 1], 0, 400)
        # AOML 经度 0..358(步长 2), 纬度 -10..? 需按实际坐标插值
        # ERDDAP griddap 的 stride 是**下标**步长, 不是数值步长:
        # 0.25° 轴上 (-60):2:(60) → 0.5° 间距 241 点; (-180):4:(180) → 1° 间距 361 点。
        # 按 0.25° 算纬度轴会把整场压进南半球(于是北半球全是陆地的 0)。
        lat_a = np.arange(LAT0, LAT1 + 1e-6, 0.5)[:d26.shape[-2]]
        lon_a = np.arange(-180.0, 180.0 + 1e-6, 1.0)[:d26.shape[-1]]
        d26h = _to_half_deg(d26, lat_a, lon_a, valid=(0.0, 400.0))
        np.savez_compressed(os.path.join(MODEL, 'base_d26.npz'),
                            d26=d26h.astype(np.float16))
        print('base_d26.npz', d26h.shape, '范围 %.0f~%.0f 均值 %.0f m'
              % (d26h.min(), d26h.max(), d26h.mean()), flush=True)
        # TCHP(kJ/cm²)单独存: 物理侧用 D26(米, mpi 的 ohc_d26/75 才有意义),
        # 显示侧用 TCHP —— 同一个场两种口径必须分开, 不能互相冒充
        tch = _to_half_deg(tchp, lat_a, lon_a, valid=(0.0, 500.0))
        np.savez_compressed(os.path.join(MODEL, 'base_tchp.npz'),
                            tchp=tch.astype(np.float16))
        print('base_tchp.npz', tch.shape, '范围 %.0f~%.0f 均值 %.0f kJ/cm²'
              % (tch.min(), tch.max(), tch.mean()), flush=True)
    else:
        print('缺 AOML, 跳过 D26', flush=True)

    # ── 风场 / 湿度 / 气压:NCEP R1 ──
    def load_ncep(nm):
        fp = os.path.join(CACHE, 'ncep_%s.npz' % nm)
        if not os.path.exists(fp):
            return None
        z = np.load(fp)
        return {k: z[k] for k in z.files}

    # ── F6: 真实深支风切 ──
    # NCEP 有现成的 uwnd/vwnd.200_850 月均(= 200hPa 减 850hPa 的风矢量差),
    # 直接 hypot 就是深支切变, 不必分别下 17 层文件再相减(那两个文件的大范围
    # OPeNDAP 读会挂)。这是 F6 的核心输入。
    su, sv = load_ncep('shr_uwnd'), load_ncep('shr_vwnd')
    if su is not None and sv is not None:
        la, lo = su['lat'], su['lon']
        mon = su['month']
        uu = np.stack([su['data'][mon == m].mean(0) for m in range(1, 13)])
        vv = np.stack([sv['data'][mon == m].mean(0) for m in range(1, 13)])
        sh = np.hypot(uu, vv) * 1.94384               # m/s → kt
        shh = _to_half_deg(sh, la, lo, valid=(-50.0, 400.0))
        np.savez_compressed(os.path.join(MODEL, 'base_shear.npz'),
                            shear=shh.astype(np.float16))
        print('base_shear.npz', shh.shape, '范围 %.1f~%.1f 均值 %.1f kt  [NCEP 200-850 真值]'
              % (shh.min(), shh.max(), shh.mean()), flush=True)

    # ── F2 收尾: 分层风场(1°) + 湿度/气压/气温(1°) ──
    # 用 tools/fetch_ncep_plev.py 抓的连续分块结果(花式索引的大范围读会挂)
    uvl = load_ncep('uwnd_lv')
    vvl = load_ncep('vwnd_lv')
    if uvl is not None and vvl is not None:
        la, lo, mon = uvl['lat'], uvl['lon'], uvl['month']
        wind = {}
        for L in (850, 500, 300, 200):
            ku, kv = 'uwnd%d' % L, 'vwnd%d' % L
            if ku not in uvl or kv not in vvl:
                continue
            uu = np.stack([uvl[ku][mon == m].mean(0) for m in range(1, 13)])
            vv = np.stack([vvl[kv][mon == m].mean(0) for m in range(1, 13)])
            wind['u%d' % L] = _to_one_deg(uu, la, lo, valid=(-200.0, 200.0)) * 1.94384
            wind['v%d' % L] = _to_one_deg(vv, la, lo, valid=(-200.0, 200.0)) * 1.94384
        if len(wind) >= 8:
            np.savez_compressed(os.path.join(MODEL, 'base_wind.npz'),
                                **{k: v.astype(np.float16) for k, v in wind.items()})
            print('base_wind.npz', sorted(wind), flush=True)
            for L in (850, 200):
                if 'u%d' % L in wind:
                    print('   u%d 范围 %.1f~%.1f kt' % (L, wind['u%d' % L].min(),
                                                        wind['u%d' % L].max()), flush=True)

    other = {}
    rh = load_ncep('rhum_lv')
    if rh is not None and 'rhum700' in rh:
        mon = rh['month']
        other['rh700'] = _to_one_deg(
            np.stack([rh['rhum700'][mon == m].mean(0) for m in range(1, 13)]),
            rh['lat'], rh['lon'], valid=(0.0, 110.0))
    slp = load_ncep('slp')
    if slp is not None and 'slp' in slp:
        mon = slp['month']
        other['mslp'] = _to_one_deg(
            np.stack([slp['slp'][mon == m].mean(0) for m in range(1, 13)]),
            slp['lat'], slp['lon'], valid=(850.0, 1100.0))
    a2 = load_ncep('air200_lv')
    if a2 is not None and 'air200' in a2:
        mon = a2['month']
        other['t200'] = _to_one_deg(
            np.stack([a2['air200'][mon == m].mean(0) for m in range(1, 13)]),
            # NCEP 的 air 是**摄氏度**(200hPa ≈ -51°C), 不是开尔文
            a2['lat'], a2['lon'], valid=(-120.0, 60.0))
    if other:
        np.savez_compressed(os.path.join(MODEL, 'base_other.npz'),
                            **{k: v.astype(np.float16) for k, v in other.items()})
        print('base_other.npz', sorted(other), flush=True)

    uw, vw = load_ncep('uwnd'), load_ncep('vwnd')
    if uw is not None and vw is not None:
        mon = uw['_month']
        la, lo = uw['_lat'], uw['_lon']
        wind = {}
        for L in NCEP_LEVELS:
            ku, kv = 'uwnd%d' % L, 'vwnd%d' % L
            if ku not in uw or kv not in vw:
                continue
            uu = np.stack([uw[ku][mon == m].mean(0) for m in range(1, 13)])
            vv = np.stack([vw[kv][mon == m].mean(0) for m in range(1, 13)])
            wind['u%d' % L] = _to_one_deg(uu, la, lo, valid=(-200.0, 200.0)) * 1.94384
            wind['v%d' % L] = _to_one_deg(vv, la, lo, valid=(-200.0, 200.0)) * 1.94384
        if 'u850' in wind and 'u200' in wind:
            sh = np.hypot(wind['u200'] - wind['u850'],
                          wind['v200'] - wind['v850'])
            np.savez_compressed(os.path.join(MODEL, 'base_shear.npz'),
                                shear=sh.astype(np.float16))
            print('base_shear.npz', sh.shape, '范围 %.1f~%.1f 均值 %.1f kt'
                  % (sh.min(), sh.max(), sh.mean()), flush=True)
        np.savez_compressed(os.path.join(MODEL, 'base_wind.npz'),
                            **{k: v.astype(np.float16) for k, v in wind.items()})
        print('base_wind.npz', sorted(wind), flush=True)

    # (旧的 rhum/slp 处理已由上面的 base_other 块取代, 删掉避免重复/键名冲突)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='all',
                    choices=('fetch', 'build', 'all', 'rest', 'dap'))
    a = ap.parse_args()
    if a.stage in ('fetch', 'all'):
        fetch()
    if a.stage == 'dap':
        fetch_dap_sst()
    if a.stage == 'rest':
        fetch_rest()
    if a.stage in ('build', 'all'):
        build()
