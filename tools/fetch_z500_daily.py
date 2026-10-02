# -*- coding: utf-8 -*-
"""环流型(regimes)数据:抓 NCEP/NCAR 逐日 500hPa 位势高度。

## 为什么用 NCSS 而不是 OPeNDAP

实测(2026-09-18):
  - OPeNDAP(`/thredds/dodsC/`)在本环境**读 5 天切片都超时**(服务端节流)
  - 直连 HTTP 下载整文件(`downloads.psl.noaa.gov`,320MB)**在 9-13MB 被截断**,
    curl/wget 带续传也一样
  - **NCSS 子集接口**(`/thredds/ncss/grid/`)可用:一年 500hPa+纬带的子集约
    10MB / 30s;但**单次请求仍在 ~9.9/10.3MB 处断**
  - → **按季度切分**,每次约 2.5MB,实测能完整返回

## 为什么要逐日

环流型是 10-60 天的低频现象。月均场会把这个频段抹掉,**而它正是强度尾部
缺的那块变率**(月平均环境下所有风暴看到的环境趋同,峰值分布太窄)。

产物: simulator/env/train_cache/z500_daily.npz
    hgt  (nday, nlat, nlon)  float16, 单位 gpm
    date (nday,)             int32 YYYYMMDD
    lat/lon
用法: python tools/fetch_z500_daily.py      (可后台)
"""
from __future__ import annotations

import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
DAP = 'https://psl.noaa.gov/thredds/ncss/grid/Datasets/ncep.reanalysis/Dailies/pressure/'
YR0, YR1 = 1979, 2026
# 实测: 6 个月块(约 5MB)最可靠, 14s 一块; 整年块(10MB)会断流。
# 48 年 x 2 块 = 96 次请求, 约 25 分钟。
CHUNK_MONTHS = 6


def _url(year, m0, m1):
    import calendar
    d0 = '%04d-%02d-01T00:00:00Z' % (year, m0)
    last = calendar.monthrange(year, m1)[1]
    d1 = '%04d-%02d-%02dT23:59:59Z' % (year, m1, last)
    return (DAP + 'hgt.%d.nc' % year +
            '?var=hgt&vertCoord=500&north=60&south=-60&west=0&east=357.5'
            '&horizStride=1&time_start=' + d0 + '&time_end=' + d1 +
            '&accept=netcdf')


def _get(url, path, tries=6):
    """带重试的下载。PSL 常在接近完成时断流, 所以每次重试都从头来(文件小)。

    **必须有 socket 超时**: 实测服务端会接受连接后不回数据, 没有超时的话
     会永久挂住(整个抓取卡死在第 5 块, 4 分钟无进展)。
    """
    import socket
    socket.setdefaulttimeout(90)
    for a in range(tries):
        try:
            urllib.request.urlretrieve(url, path)
            return os.path.getsize(path) > 100000
        except Exception as e:
            if a == tries - 1:
                print('    放弃: %s' % str(e)[:70], flush=True)
                return False
            time.sleep(2 + 2 * a)
    return False


def main(years=None):
    """years: 只抓这些年份(用于多进程并行)。None = 全部。"""
    import numpy as np
    import netCDF4
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, 'z500_daily.npz')
    if os.path.exists(out):
        print('已缓存 %s' % out, flush=True)
        return
    tmp = os.path.join(CACHE, '_z500_chunk.nc')
    pdir = os.path.join(CACHE, '_z500_parts')
    os.makedirs(pdir, exist_ok=True)
    t0 = time.time()
    Hs, Ds = [], []
    lat = lon = None
    for year in (years if years else range(YR0, YR1 + 1)):
        for m0 in range(1, 13, CHUNK_MONTHS):
            m1 = min(12, m0 + CHUNK_MONTHS - 1)
            # 断点续传: 已抓好的块直接复用(网络断了不用从头来)
            keep = os.path.join(pdir, '%d_%02d.npz' % (year, m0))
            if os.path.exists(keep):
                z = np.load(keep)
                Hs.append(z['h']); Ds.append(z['d'])
                if lat is None and 'lat' in z:
                    lat, lon = z['lat'], z['lon']
                continue
            if not _get(_url(year, m0, m1), tmp):
                print('  %d %02d-%02d 跳过' % (year, m0, m1), flush=True)
                continue
            try:
                d = netCDF4.Dataset(tmp)
                h = np.asarray(d.variables['hgt'][:], np.float32)
                if h.ndim == 4:                 # (t, lev, lat, lon) → 取 500hPa
                    k = 0
                    if 'level' in d.variables:
                        lv = np.asarray(d.variables['level'][:], float)
                        k = int(np.argmin(np.abs(lv - 500.0)))
                    h = h[:, k]
                t = np.asarray(d.variables['time'][:])
                dt = netCDF4.num2date(t, d.variables['time'].units,
                                      only_use_cftime_datetimes=False)
                if lat is None:
                    lat = np.asarray(d.variables['lat'][:], np.float32)
                    lon = np.asarray(d.variables['lon'][:], np.float32)
                d.close()
            except Exception as e:
                print('  %d %02d-%02d 解析失败: %s' % (year, m0, m1, str(e)[:60]),
                      flush=True)
                continue
            # 量程防护: 500hPa 高度 4800-6000 gpm; 有限值 _FillValue(9.97e36)
            # 会被 isfinite 放行, 必须用物理量程挡
            m = np.isfinite(h) & (h > 4000.0) & (h < 6500.0)
            h = np.where(m, h, np.nan)
            _hh = h.astype(np.float16)
            _dd = np.array([x.year * 10000 + x.month * 100 + x.day
                            for x in dt], np.int32)
            Hs.append(_hh)
            Ds.append(_dd)
            np.savez_compressed(keep, h=_hh, d=_dd, lat=lat, lon=lon)
        n = sum(x.shape[0] for x in Hs)
        print('  %d 累计 %6d 天  %.0fs' % (year, n, time.time() - t0), flush=True)
    if years is not None:
        # 并行模式: 只负责自己的年份, 不写最终合并文件(由 --merge 做)
        print('分片完成 %d 年 -> %s' % (len(years), pdir), flush=True)
        return
    try:
        os.remove(tmp)
    except OSError:
        pass
    if not Hs:
        raise RuntimeError('一天都没抓到')
    hgt = np.concatenate(Hs, 0)
    date = np.concatenate(Ds, 0)
    np.savez_compressed(out, hgt=hgt, date=date, lat=lat, lon=lon)
    print('-> %s  shape=%s  %.0fs' % (out, hgt.shape, time.time() - t0), flush=True)


def merge():
    """把所有分片合并成 z500_daily.npz。"""
    import numpy as np
    pdir = os.path.join(CACHE, '_z500_parts')
    fs = sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []
    if not fs:
        print('没有分片'); return
    Hs, Ds, lat, lon = [], [], None, None
    for x in fs:
        z = np.load(os.path.join(pdir, x))
        Hs.append(z['h']); Ds.append(z['d'])
        if lat is None and 'lat' in z:
            lat, lon = z['lat'], z['lon']
    hgt = np.concatenate(Hs, 0); date = np.concatenate(Ds, 0)
    o = np.argsort(date); hgt, date = hgt[o], date[o]
    out = os.path.join(CACHE, 'z500_daily.npz')
    np.savez_compressed(out, hgt=hgt, date=date, lat=lat, lon=lon)
    print('-> %s  shape=%s  覆盖 %d-%d' % (
        out, hgt.shape, date[0] // 10000, date[-1] // 10000))


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--merge':
        merge()
    elif len(sys.argv) > 2 and sys.argv[1] == '--shard':
        i, n = int(sys.argv[2]), int(sys.argv[3])
        main(list(range(YR0 + i, YR1 + 1, n)))
    else:
        main()
