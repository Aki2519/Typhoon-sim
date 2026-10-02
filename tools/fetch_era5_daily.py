# -*- coding: utf-8 -*-
"""抓 ERA5 单日 SST(0.25°)—— 每小时一个场, 不做任何时段平均。

为什么必须单日: 月均已经把结构抹了一次(OISST 的 30 年月气候态连黑潮都抹平了),
   而验证/建基态都要保留锋面。用法:
     python tools/fetch_era5_daily.py --pairs 2024-09-15,2024-01-15,... [--hour 12]
输出: simulator/env/train_cache/era5/day_<YYYYMMDD>_<HH>.nc
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cds_client import CDS
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
AREA = [60, 0, -60, 360]

ap = argparse.ArgumentParser()
ap.add_argument('--pairs', required=True, help='逗号分隔 YYYY-MM-DD')
ap.add_argument('--hour', default='12:00')
a = ap.parse_args()
c = CDS()
os.makedirs(OUT, exist_ok=True)
for p in a.pairs.split(','):
    y, m, d = p.split('-')
    fp = os.path.join(OUT, 'day_%s%s%s_%s.nc' % (y, m, d, a.hour[:2]))
    if os.path.exists(fp) and os.path.getsize(fp) > 1e5:
        print('已缓存', os.path.basename(fp), flush=True); continue
    req = {'product_type': ['reanalysis'],
           'variable': ['sea_surface_temperature'],
           'year': [y], 'month': [m], 'day': [d], 'time': [a.hour],
           'data_format': 'netcdf', 'download_format': 'unarchived',
           'area': AREA}
    for k in range(3):
        try:
            pid = c.submit('reanalysis-era5-single-levels', req)
            c.wait(pid, poll=15, verbose=False)
            c.download(pid, fp)
            print('[完成]', os.path.basename(fp), '%.2f MB' % (os.path.getsize(fp)/1e6), flush=True)
            break
        except Exception as e:
            print('[失败]', p, k+1, str(e)[:90], flush=True); time.sleep(10)
    time.sleep(15)
print('DONE', flush=True)
