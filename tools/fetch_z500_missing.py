# -*- coding: utf-8 -*-
"""补抓 Z500 的分片。

主抓取(6 个并行分片)会因服务端限流随机漏掉若干块。这个脚本扫描
`_z500_parts/` 找出缺失的 (年, 起始月), 只补那些块。
**幂等**: 已存在的块直接跳过, 可以反复跑。

用法: python tools/fetch_z500_missing.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    import numpy as np
    import netCDF4
    import fetch_z500_daily as F

    pdir = os.path.join(F.CACHE, '_z500_parts')
    os.makedirs(pdir, exist_ok=True)
    have = set(os.listdir(pdir))
    missing = []
    for y in range(F.YR0, F.YR1 + 1):
        for m in range(1, 13, F.CHUNK_MONTHS):
            if '%d_%02d.npz' % (y, m) not in have:
                missing.append((y, m))
    print('缺 %d 块' % len(missing), flush=True)
    if not missing:
        print('ALL DONE')
        return
    tmp = os.path.join(F.CACHE, '_z500_chunk.nc')
    lat = lon = None
    ok = 0
    for i, (y, m0) in enumerate(missing):
        m1 = min(12, m0 + F.CHUNK_MONTHS - 1)
        keep = os.path.join(pdir, '%d_%02d.npz' % (y, m0))
        if os.path.exists(keep):
            continue
        if not F._get(F._url(y, m0, m1), tmp, tries=4):
            print('  [%2d/%2d] %d_%02d 失败' % (i + 1, len(missing), y, m0), flush=True)
            continue
        try:
            d = netCDF4.Dataset(tmp)
            h = np.asarray(d.variables['hgt'][:], np.float32)
            if h.ndim == 4:
                h = h[:, 0]
            t = np.asarray(d.variables['time'][:])
            dt = netCDF4.num2date(t, d.variables['time'].units,
                                  only_use_cftime_datetimes=False)
            if lat is None:
                lat = np.asarray(d.variables['lat'][:], np.float32)
                lon = np.asarray(d.variables['lon'][:], np.float32)
            d.close()
        except Exception as e:
            print('  [%2d/%2d] %d_%02d 解析失败 %s'
                  % (i + 1, len(missing), y, m0, str(e)[:50]), flush=True)
            continue
        m = np.isfinite(h) & (h > 4000.0) & (h < 6500.0)
        h = np.where(m, h, np.nan).astype(np.float16)
        dd = np.array([x.year * 10000 + x.month * 100 + x.day for x in dt], np.int32)
        np.savez_compressed(keep, h=h, d=dd, lat=lat, lon=lon)
        ok += 1
        print('  [%2d/%2d] %d_%02d ✓' % (i + 1, len(missing), y, m0), flush=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    print('补了 %d 块。现在 %d/%d' % (
        ok, len(os.listdir(pdir)),
        len(range(F.YR0, F.YR1 + 1)) * len(range(1, 13, F.CHUNK_MONTHS))))


if __name__ == '__main__':
    main()
