# -*- coding: utf-8 -*-
"""F2 补全: NCEP R1 分层风场 / 湿度 / 海平面气压 / 200hPa 温度(1991-2020 月气候态)。

为什么必须**连续切片 + 分块**: 早先用花式索引 `[sel, k, lat, :]`(sel 是跨度
938 的 360 个下标)向 OPeNDAP 发的是 gather 请求, 服务端直接挂住(三次 200s+
超时)。改成连续区间 `[i0:i0+60, k, lat, :]` 后单块 30-50 s, 稳定。

产物(全部 1° 网格, float16):
  ncep_uv.npz    u850/v850/u500/v500/u300/v300/u200/v200  (kt)
  ncep_other.npz rh700 (%) / mslp (hPa) / t200 (K)

用法: python tools/fetch_ncep_plev.py     (可后台, 约 40 分钟)
"""
from __future__ import annotations

import os
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
DAP = 'https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.derived/'
# **可从命令行覆盖**(扩域到 ±90 时用 --lat0 -90 --lat1 90 --suffix _g)
LAT0, LAT1 = -60.0, 60.0
YR0, YR1 = 1991, 2020
BLK = 60


def _fetch(ds, var, levels, out, year_lo=YR0, year_hi=YR1):
    import netCDF4
    if os.path.exists(out):
        print('已缓存 %s' % os.path.basename(out), flush=True)
        return
    d = netCDF4.Dataset(DAP + ds)
    t = np.asarray(d.variables['time'][:])
    dt = netCDF4.num2date(t, d.variables['time'].units,
                          only_use_cftime_datetimes=False)
    yrs = np.array([x.year for x in dt])
    idx = np.nonzero((yrs >= year_lo) & (yrs <= year_hi))[0]
    i0, i1 = int(idx[0]), int(idx[-1])           # 连续区间, 不做 gather
    lat = np.asarray(d.variables['lat'][:])
    a_ = int(np.argmin(abs(lat - LAT0)))
    b_ = int(np.argmin(abs(lat - LAT1)))
    j0, j1 = min(a_, b_), max(a_, b_)
    out_d = {}
    lev = np.asarray(d.variables['level'][:]) if 'level' in d.variables else None
    for L in (levels or [None]):
        k = None if L is None else int(np.argmin(abs(lev - L)))
        parts = []
        n = i1 - i0 + 1
        for s in range(0, n, BLK):
            e = min(s + BLK, n)
            t0 = time.time()
            if k is None:
                x = d.variables[var][i0 + s:i0 + e, j0:j1 + 1, :]
            else:
                x = d.variables[var][i0 + s:i0 + e, k, j0:j1 + 1, :]
            parts.append(np.asarray(x, np.float32))
            print('  %s%-4s 块 %d/%d  %.0fs'
                  % (var, '' if L is None else L, s // BLK + 1,
                     (n + BLK - 1) // BLK, time.time() - t0), flush=True)
        out_d[var if L is None else '%s%d' % (var, L)] = np.concatenate(parts, 0)
    out_d['month'] = np.array([x.month for x in dt])[idx]
    out_d['year'] = yrs[idx]
    out_d['lat'] = lat[j0:j1 + 1]
    out_d['lon'] = np.asarray(d.variables['lon'][:])
    d.close()
    np.savez_compressed(out, **out_d)
    print('-> %s  %s' % (os.path.basename(out),
                         [k for k in out_d if k not in ('month', 'year',
                                                        'lat', 'lon')]),
          flush=True)


def main(argv=None):
    global LAT0, LAT1
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--lat0', type=float, default=LAT0)
    ap.add_argument('--lat1', type=float, default=LAT1)
    ap.add_argument('--suffix', default='', help='输出文件名后缀, 如 _g')
    a = ap.parse_args(argv)
    LAT0, LAT1 = a.lat0, a.lat1
    sx = a.suffix
    print('范围 lat %.0f..%.0f, 输出后缀 %r' % (LAT0, LAT1, sx), flush=True)
    os.makedirs(CACHE, exist_ok=True)
    # 分层风(4 层 × u/v)
    for var in ('uwnd', 'vwnd'):
        _fetch('pressure/%s.mon.mean.nc' % var, var, [850, 500, 300, 200],
               os.path.join(CACHE, 'ncep_%s_lv%s.npz' % (var, sx)))
    # 700hPa 相对湿度 / 海平面气压 / 200hPa 气温
    _fetch('pressure/rhum.mon.mean.nc', 'rhum', [700],
           os.path.join(CACHE, 'ncep_rhum_lv%s.npz' % sx))
    _fetch('surface/slp.mon.mean.nc', 'slp', None,
           os.path.join(CACHE, 'ncep_slp%s.npz' % sx))
    _fetch('pressure/air.mon.mean.nc', 'air', [200],
           os.path.join(CACHE, 'ncep_air200_lv%s.npz' % sx))
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
