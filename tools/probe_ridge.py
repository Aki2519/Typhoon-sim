# -*- coding: utf-8 -*-
"""副高(西太副热带高压)在**模型自用的基态风场**里长什么样。

A 任务的第一步: 先证明"模型环境场里到底有没有副高、在哪、多强", 再谈改不改。
数据源就是模型运行时真正读的 `simulator/env/model/base_wind.npz`(ERA5 训练产物),
不是另找一套再分析 —— 否则"我改了副高"与"风暴看到的副高"不是同一个东西。

输出:
  ① u850/v850 的**纬向剖面**(西太 120-160E 平均)逐月 —— 看信风/西风与经向风;
  ② **500hPa 脊线纬度**逐月逐经度(脊线 = u500 由东转西的零线, 脊轴);
  ③ **西伸脊点**(脊线西端)逐月经度;
  ④ 200hPa 副热带急流位置(纬向风最大值)作对照。

用法: python tools/probe_ridge.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

NPZ = os.path.join('simulator', 'env', 'model', 'base_wind.npz')
MSLP_CANDIDATES = ['base_other.npz']


def latlon_axes(nlat, nlon):
    lat = -90.0 + (np.arange(nlat) + 0.5) * (180.0 / nlat)
    lon = (np.arange(nlon) + 0.5) * (360.0 / nlon)
    return lat, lon


def main():
    z = np.load(NPZ)
    nlat, nlon = z['u850'].shape[1], z['u850'].shape[2]
    lat, lon = latlon_axes(nlat, nlon)
    print('网格 %dx%d  纬度 %.1f..%.1f  经度 %.1f..%.1f'
          % (nlat, nlon, lat[0], lat[-1], lon[0], lon[-1]))

    # ① 纬向剖面(西太 120-160E)
    sel = (lon >= 120.0) & (lon <= 160.0)
    print()
    print('① 西太 120-160E 纬向剖面(m/s -> kt: x1.944)')
    print('   lat   ' + ' '.join('%7s' % ('%d月' % (m + 1)) for m in range(0, 12, 2)))
    for la in (5, 10, 15, 20, 25, 30, 35, 40, 45):
        j = int(np.argmin(np.abs(lat - la)))
        row = []
        for m in range(0, 12, 2):
            u = float(np.nanmean(z['u850'][m, j, sel])) * 1.944
            v = float(np.nanmean(z['v850'][m, j, sel])) * 1.944
            row.append('%+4.0f/%+3.0f' % (u, v))
        print('  %4dN  ' % la + ' '.join('%7s' % r for r in row))
    print('   (格式: u850/v850, kt)')

    # ② 500hPa 脊线 = u500 由西风(+)转东风(-)的零线, 取 120-180E 的经度加权
    print()
    print('② 500hPa 脊线纬度(逐月经度, 120-180E)')
    lons = np.arange(120.0, 180.1, 5.0)
    ridge = np.full((12, lons.size), np.nan)
    for m in range(12):
        u5 = z['u500'][m]
        for k, lo in enumerate(lons):
            i = int(np.argmin(np.abs(lon - lo)))
            prof = np.array([np.nanmean(u5[j, max(0, i - 1):i + 2])
                             for j in range(nlat)])
            jj = np.where(lat > 0)[0]
            found = np.nan
            for j in jj[:-1]:
                if prof[j] < 0 <= prof[j + 1]:          # 东风 -> 西风 = 脊轴
                    found = lat[j] + (lat[j + 1] - lat[j]) * (
                        -prof[j] / (prof[j + 1] - prof[j]))
                    break
            ridge[m, k] = found
    print('   月  ' + ' '.join('%5.0fE' % x for x in lons))
    for m in range(12):
        print('  %2d月 ' % (m + 1)
              + ' '.join('%5.1f ' % ridge[m, k] for k in range(lons.size)))

    # ③ 西伸脊点: 脊线纬度 < 40 度(脊还在副热带)的最西经度
    print()
    print('③ 西伸脊点(脊线存在且纬度 <40 的最西经度)')
    for m in (6, 7, 8, 9, 10):
        ok = ridge[m] < 40.0
        west = lons[ok].min() if ok.any() else float('nan')
        print('   %2d月: 西伸脊点 %.0fE | 脊线纬度(120-160E) 中位 %.1f'
              % (m + 1, west, np.nanmedian(ridge[m][:9])))

    # ④ 200hPa 急流
    print()
    print('④ 200hPa 纬向风最大值纬度(120-160E, 副热带急流)')
    for m in range(12):
        u2 = z['u200'][m][:, sel]
        prof = np.array([np.nanmean(u2[j]) for j in range(nlat)]) * 1.944
        jj = np.where((lat > 15) & (lat < 60))[0]
        jm = jj[int(np.argmax(prof[jj]))]
        print('   %2d月: 急流轴 %4.1fN  峰值 %5.1f kt' % (m + 1, lat[jm], prof[jm]))

    # ⑤ 有没有 mslp
    for nm in MSLP_CANDIDATES:
        p = os.path.join('simulator', 'env', 'model', nm)
        if os.path.exists(p):
            zz = np.load(p)
            print()
            print('⑤ %s 字段: %s' % (nm, ', '.join('%s%s' % (k, zz[k].shape)
                                                   for k in zz.files)))


if __name__ == '__main__':
    main()
