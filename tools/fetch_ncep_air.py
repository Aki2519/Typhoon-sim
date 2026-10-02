# -*- coding: utf-8 -*-
"""抓 NCEP R1 的 sigma0.995 气温月均(1948-2026, 2.5°)—— F3b 回归的 SST 代理。

为什么要代理: 模态空间型的回归需要**长序列**格点场(至少 200-300 个月才稳)。
CDS 的 ERA5 SST 单月要 35 s 且大文件会断流, 30 年 = 360 个请求, 不现实;
NCEP 这个文件一次覆盖 77 年, 而**海上气温异常与 SST 异常高度相关**,
做"异常型的形状"是够用的(回归掉月气候态之后, 系统性偏差会抵消)。

陆地在回归时会用 simcore 的陆地掩码置零(气温在陆地上振幅远大于 SST)。

用法: python tools/fetch_ncep_air.py
输出: simulator/env/train_cache/ncep_air.npz
"""
from __future__ import annotations

import os
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
URL = ('https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.derived/'
       'surface/air.sig995.mon.mean.nc')


def main():
    import netCDF4
    fp = os.path.join(CACHE, 'ncep_air.npz')
    if os.path.exists(fp):
        print('已缓存', fp)
        return
    t0 = time.time()
    d = netCDF4.Dataset(URL)
    t = np.asarray(d.variables['time'][:])
    dt = netCDF4.num2date(t, d.variables['time'].units,
                          only_use_cftime_datetimes=False)
    la = np.asarray(d.variables['lat'][:])
    lo = np.asarray(d.variables['lon'][:])
    n = t.size
    out = []
    for k in range(0, n, 60):                    # 逐块读, 大请求会挂
        blk = slice(k, min(k + 60, n))
        out.append(np.asarray(d.variables['air'][blk, :, :], np.float32))
        print('  块 %d/%d (%.0fs)' % (k // 60 + 1, (n + 59) // 60,
                                      time.time() - t0), flush=True)
    A = np.concatenate(out, 0)
    d.close()
    np.savez_compressed(fp, air=A, year=np.array([x.year for x in dt], np.int16),
                        month=np.array([x.month for x in dt], np.int8),
                        lat=la, lon=lo)
    print('-> ncep_air.npz %s  %.1f~%.1f K  (%.0fs)'
          % (A.shape, np.nanmin(A), np.nanmax(A), time.time() - t0))


if __name__ == '__main__':
    main()
