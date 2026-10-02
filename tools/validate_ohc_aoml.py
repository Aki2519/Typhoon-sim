# -*- coding: utf-8 -*-
"""用 AOML TCHP(卫星+Argo 口径, 与 CMEMS 完全独立)逐月校验 base_tchp。

为什么走 tile: AOML 的 ERDDAP 大范围请求会失败, 但**小区域 tile 能下**
(实测 100-180E/0-35N 单日 ≈187 KB)。于是逐月取 12 个 tile, 与 base_tchp 的对应月
逐格比 —— 这是不依赖 CDS 的独立校验(2026-09-20 夜里 CDS 时好时坏)。

用法:
  python tools/validate_ohc_aoml.py --year 2024 --day 15
输出: 每月 (bias / MAD / 相关), 并把 tile 缓存到 C:\\cmems_tmp\\aoml_wpac_<日期>.nc
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = r'C:\cmems_tmp'
VAR = 'Tropical_Cyclone_Heat_Potential'
VAR_D26 = 'D26'          # 同一数据集里也有 D26(m), 顺带把 base_d26 也独立校验
LON0, LON1, LAT0, LAT1 = 100.0, 180.0, 0.0, 35.0
URL = ('https://cwcgom.aoml.noaa.gov/erddap/griddap/aomlTCHP.nc?'
       '%s%%5B(%sT12:00:00Z):1:(%sT12:00:00Z)%%5D'
       '%%5B(%.1f):1:(%.1f)%%5D%%5B(%.1f):1:(%.1f)%%5D')


def tile_path(ymd):
    return os.path.join(TMP, 'aoml_wpac_%s.nc' % ymd)


def tile_path_d26(ymd):
    return os.path.join(TMP, 'aoml_d26_%s.nc' % ymd)


def fetch_tile_d26(date, tries=3):
    """D26 必须**单独请求**(ERDDAP 对多变量+约束返回 500)。"""
    import requests
    ymd = date.replace('-', '')
    fp = tile_path_d26(ymd)
    if os.path.exists(fp) and os.path.getsize(fp) > 5000:
        return fp
    url = ('https://cwcgom.aoml.noaa.gov/erddap/griddap/aomlTCHP.nc?'
           'D26%%5B(%sT12:00:00Z):1:(%sT12:00:00Z)%%5D'
           '%%5B(%.1f):1:(%.1f)%%5D%%5B(%.1f):1:(%.1f)%%5D'
           % (date, date, LAT0, LAT1, LON0, LON1))
    for k in range(tries):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and len(r.content) > 5000:
                open(fp, 'wb').write(r.content)
                return fp
        except Exception:
            pass
    return None


def fetch_tile(date, tries=3):
    import requests
    ymd = date.replace('-', '')
    fp = tile_path(ymd)
    if os.path.exists(fp) and os.path.getsize(fp) > 5000:
        return fp
    # 注: ERDDAP 对"多变量 + 约束"的请求会返回 500, 所以只取 TCHP;
    # D26 的独立校验要用单独的请求(留给后续)。
    url = URL % (VAR, date, date, LAT0, LAT1, LON0, LON1)
    for k in range(tries):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and len(r.content) > 5000:
                open(fp, 'wb').write(r.content)
                return fp
            print('   [HTTP %s %d B] 重试' % (r.status_code, len(r.content)), flush=True)
        except Exception as e:                                   # noqa: BLE001
            print('   [ERR] %s' % str(e)[:90], flush=True)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2024)
    ap.add_argument('--day', type=int, default=15)
    a = ap.parse_args()
    import json
    import xarray as xr
    from scipy import ndimage as ndi
    from simulator.simcore.envmodel import store

    base = np.asarray(store.load('base_tchp')['tchp'], np.float32)
    base_d = np.asarray(store.load('base_d26')['d26'], np.float32)
    clat = -60 + (np.arange(240) + 0.5) * 0.5
    clon = (np.arange(720) + 0.5) * 0.5
    base_d = np.asarray(store.load('base_d26')['d26'], np.float32)
    rows = []
    d26rows = []
    for m in range(1, 13):
        date = '%04d-%02d-%02d' % (a.year, m, a.day)
        fp = fetch_tile(date)
        if fp is None:
            print('%s: tile 取不到, 跳过' % date, flush=True)
            rows.append((m, None, None, None, 0))
            continue
        try:
            d = xr.open_dataset(fp)
            A = np.asarray(d[VAR].values, float).squeeze()
            la = np.asarray(d['latitude'].values)
            lo = np.asarray(d['longitude'].values)
            d.close()
        except Exception as e:                                   # noqa: BLE001
            print('%s: 读失败 %s' % (date, str(e)[:80]), flush=True)
            rows.append((m, None, None, None, 0))
            continue
        fy = (la - clat[0]) / (clat[-1] - clat[0]) * (clat.size - 1)
        fx = (lo - clon[0]) / (clon[-1] - clon[0]) * (clon.size - 1)
        C = ndi.map_coordinates(base[m - 1], np.array(np.meshgrid(fy, fx, indexing='ij')),
                                order=1, mode='nearest')
        ok = np.isfinite(A) & np.isfinite(C) & (A > 1) & (C > 1)
        if ok.sum() < 100:
            rows.append((m, None, None, None, int(ok.sum())))
            continue
        dd = A[ok] - C[ok]
        rows.append((m, float(dd.mean()), float(np.abs(dd).mean()),
                     float(np.corrcoef(A[ok], C[ok])[0, 1]), int(ok.sum())))
        # D26 单独请求 → 顺带独立校验 base_d26
        dline = ''
        fpd = fetch_tile_d26(date)
        if fpd is not None:
            try:
                dd2 = xr.open_dataset(fpd)
                A2 = np.asarray(dd2['D26'].values, float).squeeze()
                dd2.close()
                C2 = ndi.map_coordinates(base_d[m - 1],
                                         np.array(np.meshgrid(fy, fx, indexing='ij')),
                                         order=1, mode='nearest')
                ok2 = np.isfinite(A2) & np.isfinite(C2) & (A2 > 1) & (C2 > 1)
                if ok2.sum() > 100:
                    d2 = A2[ok2] - C2[ok2]
                    d26rows.append((m, float(d2.mean()), float(np.abs(d2).mean()),
                                    float(np.corrcoef(A2[ok2], C2[ok2])[0, 1]),
                                    int(ok2.sum())))
                    dline = (' | D26 bias %+5.1f MAD %5.1f 相关 %.3f'
                             % (d2.mean(), np.abs(d2).mean(),
                                np.corrcoef(A2[ok2], C2[ok2])[0, 1]))
            except Exception:
                pass
        print('%2d 月: TCHP bias %+6.1f  MAD %5.1f  相关 %.3f  (n=%d)%s'
              % (m, dd.mean(), np.abs(dd).mean(),
                 np.corrcoef(A[ok], C[ok])[0, 1], ok.sum(), dline), flush=True)
    if d26rows:
        print('D26 汇总: %d/12 月 | 平均 bias %+.1f m | 平均 MAD %.1f | 平均相关 %.3f'
              % (len(d26rows), float(np.mean([r[1] for r in d26rows])),
                 float(np.mean([r[2] for r in d26rows])),
                 float(np.mean([r[3] for r in d26rows]))))
    good = [r for r in rows if r[1] is not None]
    if good:
        print('\n汇总: %d/%d 个月可校验 | 平均 bias %+.1f kJ/cm2 | 平均 MAD %.1f | 平均相关 %.3f'
              % (len(good), 12, float(np.mean([r[1] for r in good])),
                 float(np.mean([r[2] for r in good])),
                 float(np.mean([r[3] for r in good]))))
    out = os.path.join(ROOT, 'output', 'sim', '_reference', 'validate_ohc_aoml.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    _ok = [r for r in rows if len(r) == 5 and r[1] is not None]
    _sum = ({'n_months': len(_ok), 'bias': float(np.mean([r[1] for r in _ok])),
             'mad': float(np.mean([r[2] for r in _ok])),
             'corr': float(np.mean([r[3] for r in _ok]))} if _ok else {})
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'year': a.year, 'day': a.day, 'rows': rows, 'd26_rows': d26rows,
                   'summary': _sum,
                   'region': [LON0, LON1, LAT0, LAT1]}, f, ensure_ascii=False, indent=1)
    print('JSON 记录 %d 月, summary=%s' % (len(_ok), _sum))
    print('写出', out)


if __name__ == '__main__':
    main()
