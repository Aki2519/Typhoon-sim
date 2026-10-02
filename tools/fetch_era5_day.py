# -*- coding: utf-8 -*-
"""抓 ERA5 **单日单时次瞬时场**(不取任何时段平均) —— 风切/风场/气压/SST 的运行时来源。

用户 2026-09-20 明确要求: "风切及一切数据都要用瞬时(除了一些特殊数据用长时间平均)"。
月均已经把结构抹了一次(OISST 的 30 年月气候态连黑潮都抹平了), 而 |V200−V850| 用
"均值的矢量差"还算出了比日尺度大 ~1.8 倍的风切 —— 所以运行时必须以**瞬时场**为准,
月气候态只当"没有单日数据时的兜底"和模态/指数这类特殊产品的底。

产物(全部 netCDF, 覆盖 60S-60N / 0-360E):
  era5/day_<YYYYMMDD>_<HH>.nc    地面: sst, msl, u10, v10
  era5/plev_<YYYYMMDD>_<HH>.nc   等压面: u/v/r/t @ 850/500/300/200 hPa

用法:
  python tools/fetch_era5_day.py --pairs 1997-09-16,2026-02-28 --hour 12:00
  python tools/fetch_era5_day.py --list          # 看已缓存清单
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cds_client import CDS                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
AREA = [60, 0, -60, 360]
MIN_SIZE = 5e4


def surf_path(y, m, d, hh):
    return os.path.join(OUT, 'day_%s%s%s_%s.nc' % (y, m, d, hh))


def plev_path(y, m, d, hh):
    return os.path.join(OUT, 'plev_%s%s%s_%s.nc' % (y, m, d, hh))


def _valid(fp, need=()):
    """**完整性校验**: 只看文件大小不够 —— CDS 断流会留下 HDF5 截断文件
    (实测 897 KB 的 day 文件能过 size 检查但 netCDF4 打开报 HDF error)。"""
    if not os.path.exists(fp) or os.path.getsize(fp) < MIN_SIZE:
        return False
    try:
        import netCDF4
        os.chdir(ROOT)
        with netCDF4.Dataset(os.path.relpath(fp, ROOT)) as d:
            ks = set(d.variables.keys())
            return all(v in ks for v in need)
    except Exception:
        return False


def _fetch(c, dataset, req, fp, tries=3, need=()):
    if _valid(fp, need):
        print('已缓存', os.path.basename(fp), flush=True)
        return True
    if os.path.exists(fp):                    # 截断/损坏 → 删掉重下
        try:
            os.remove(fp)
            print('[损坏] 删除重下', os.path.basename(fp), flush=True)
        except Exception:
            pass
    for k in range(tries):
        try:
            c.retrieve(dataset, req, fp)
            if _valid(fp, need):
                print('[完成] %s %.2f MB' % (os.path.basename(fp),
                                            os.path.getsize(fp) / 1e6), flush=True)
                return True
            print('[不完整]', os.path.basename(fp),
                  os.path.getsize(fp) if os.path.exists(fp) else 0, flush=True)
        except Exception as e:
            print('[失败] %s %d %s' % (os.path.basename(fp), k + 1, str(e)[:100]), flush=True)
            time.sleep(10)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description='ERA5 单日单时次瞬时场')
    ap.add_argument('--pairs', default='', help='逗号分隔 YYYY-MM-DD')
    ap.add_argument('--hour', default='12:00', help='UTC 时次, 如 12:00')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--surf-only', action='store_true',
                    help='只抓地面场(校验 SST 用, 快得多)')
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    if a.list:
        fs = sorted(f for f in os.listdir(OUT) if f.startswith(('day_', 'plev_')))
        print('已缓存 %d 个: %s' % (len(fs), ', '.join(fs[:12])))
        return
    if not a.pairs:
        ap.error('--pairs 必填(或 --list)')
    hh = a.hour[:2]
    c = CDS()
    for p in [s.strip() for s in a.pairs.split(',') if s.strip()]:
        y, m, d = p.split('-')
        surf = {'product_type': ['reanalysis'],
                'variable': ['sea_surface_temperature', 'mean_sea_level_pressure',
                             '10m_u_component_of_wind', '10m_v_component_of_wind'],
                'year': [y], 'month': [m], 'day': [d], 'time': [a.hour],
                'data_format': 'netcdf', 'download_format': 'unarchived', 'area': AREA}
        plev = {'product_type': ['reanalysis'],
                'variable': ['u_component_of_wind', 'v_component_of_wind',
                             'relative_humidity', 'temperature'],
                # 700 也要: 内核的 env['rh'] 用的是 700 hPa 相对湿度,
                # 少了它瞬时路径就只能回落到月基态(2026-09-20 补)
                'pressure_level': ['200', '300', '500', '700', '850'],
                'year': [y], 'month': [m], 'day': [d], 'time': [a.hour],
                'data_format': 'netcdf', 'download_format': 'unarchived', 'area': AREA}
        _fetch(c, 'reanalysis-era5-single-levels', surf,
               surf_path(y, m, d, hh),
               need=('sst', 'msl', 'u10', 'v10'))
        if not a.surf_only:
            _fetch(c, 'reanalysis-era5-pressure-levels', plev,
                   plev_path(y, m, d, hh),
                   need=('u', 'v', 'r', 't'))
        time.sleep(5)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
