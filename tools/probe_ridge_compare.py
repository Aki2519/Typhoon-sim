# -*- coding: utf-8 -*-
"""**脊线诊断的一致性检查**: 850hPa 零线 vs 500hPa 零线差多少, 差在哪。

背景(轮次 103 留的待办): 副高向极修正(`core.sh_pole_steer`)以
`core.sh_ridge_lat` 诊断的脊线纬度作参照, 而那个诊断用的是 **850hPa 东风转西风的零线**;
`tools/probe_ridge.py` 用 **500hPa 零线**得到的是另一组数(8 月西太 27.8-32.1N),
两者实测差 5-9 度。到底该用哪一个当"驱动引导的副高", 需要先把差异**量清楚**。

本工具完全离线(只读 `simulator/env/model/base_wind.npz`), 不需要跑模拟:
对 850 / 500 / 300 / 200 hPa 四个层次用**完全相同的判据**(沿经度 +-30 度滑动平均后,
取最低的"东风->西风"零线纬度)算脊线, 再逐月对比。

用法: python tools/probe_ridge_compare.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

NPZ = os.path.join('simulator', 'env', 'model', 'base_wind.npz')
#: 关注的经度窗口(西太副高)
LON0, LON1 = 120.0, 180.0
LAT0, LAT1 = 3.0, 50.0


def ridge_of(u, lat, lon, lat0=LAT0, lat1=LAT1, smooth_deg=30.0):
    """逐经度取最南的"东风->西风"零线纬度(与 core.sh_ridge_lat 同判据)。"""
    ny, nx = u.shape
    k = max(1, int(round(smooth_deg / (360.0 / nx))))
    up = np.concatenate([u[:, -k:], u, u[:, :k]], axis=1)
    cs = np.cumsum(up, axis=1)
    cs = np.concatenate([np.zeros((ny, 1)), cs], axis=1)
    us = (cs[:, 2 * k + 1:] - cs[:, :-2 * k - 1]) / (2.0 * k + 1.0)
    m = (lat >= lat0) & (lat <= lat1)
    idx = np.where(m)[0]
    la = lat[idx]
    o = np.argsort(la)
    idx, la = idx[o], la[o]
    sub = us[idx, :]
    n = sub.shape[0]
    neg = sub < 0.0
    has = neg.any(axis=0)
    a = np.where(has, (n - 1) - np.argmax(neg[::-1, :], axis=0), 0)
    aa = np.minimum(a, n - 2)
    cols = np.arange(sub.shape[1])
    c0, c1 = sub[aa, cols], sub[aa + 1, cols]
    ok = has & (c1 >= 0.0)
    den = np.where(np.abs(c1 - c0) < 1e-12, 1e-12, c1 - c0)
    f = np.where(ok, -c0 / den, 0.0)
    return np.where(ok, la[aa] + f * (la[aa + 1] - la[aa]), np.nan)


def main():
    z = np.load(NPZ)
    ny, nx = z['u850'].shape[1], z['u850'].shape[2]
    lat = -90.0 + (np.arange(ny) + 0.5) * (180.0 / ny)
    lon = (np.arange(nx) + 0.5) * (360.0 / nx)
    sel = (lon >= LON0) & (lon <= LON1)
    print('西太 %.0f-%.0fE 的脊线纬度(逐月经度中位), 判据: 最南的"东风->西风"零线'
          % (LON0, LON1))
    print('%-5s | %-8s | %-8s | %-8s | %-8s | %s'
          % ('月', '850hPa', '500hPa', '300hPa', '200hPa', '850-500'))
    rows = []
    for m in range(12):
        r = {}
        for lv in ('u850', 'u500', 'u300', 'u200'):
            u = np.asarray(z[lv][m], dtype=np.float64)
            r[lv] = float(np.nanmedian(ridge_of(u, lat, lon)[sel]))
        rows.append((m + 1, r))
        print('%-5d | %8.1f | %8.1f | %8.1f | %8.1f | %+8.1f'
              % (m + 1, r['u850'], r['u500'], r['u300'], r['u200'],
                 r['u850'] - r['u500']))
    a = np.array([[r['u850'], r['u500'], r['u300'], r['u200']] for _m, r in rows])
    print()
    print('全年中位: 850 %.1f | 500 %.1f | 300 %.1f | 200 %.1f  => **850 比 500 高 %.1f 度**'
          % (np.median(a[:, 0]), np.median(a[:, 1]), np.median(a[:, 2]),
             np.median(a[:, 3]), np.median(a[:, 0] - a[:, 1])))
    print('夏季(6-9 月)中位: 850 %.1f | 500 %.1f  => 差 %.1f 度'
          % (np.median(a[5:9, 0]), np.median(a[5:9, 1]),
             np.median(a[5:9, 0] - a[5:9, 1])))
    print()
    print('判读:')
    print('  · 台风路径的经典引导层是 **500hPa**(槽脊所在); 850hPa 的零线受季风槽/大陆热低压')
    print('    影响会被推到更北, 所以"用 850 零线当副高脊"本身就有系统性偏移。')
    print('  · 若 850 比 500 稳定高 5-9 度, 那么 `sh_pole_steer` 的参照纬度就整体偏北,')
    print('    等效于把向极修正**推迟**了几度 —— 校准时应把这个偏移算进去(或直接改用')
    print('    500hPa 判据, 需要先把 u500/v500 接进内核)。')


if __name__ == '__main__':
    main()
