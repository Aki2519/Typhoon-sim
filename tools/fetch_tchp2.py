# -*- coding: utf-8 -*-
"""A: ERDDAP 端直接用 stride=4 降到 1°(载荷小 16 倍), 每月 15 日 12z。"""
import os, subprocess, time
import numpy as np, xarray as xr
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'tchp'); os.makedirs(TMP, exist_ok=True)
YEAR = 2025
def url(m):
    b = 'https://cwcgom.aoml.noaa.gov/erddap/griddap/aomlTCHP.nc?'
    win = ('[(%d-%02d-14T00:00:00Z):1:(%d-%02d-16T23:00:00Z)]' % (YEAR, m, YEAR, m)) * 1
    return (b + 'Tropical_Cyclone_Heat_Potential' + win + '[(-60.0):4:(60.0)][(0.0):4:(359.0)]'
            + ',D26' + win + '[(-60.0):4:(60.0)][(0.0):4:(359.0)]')
T = np.zeros((12, 120, 360), np.float32); D = np.zeros((12, 120, 360), np.float32)
ok = 0
for m in range(1, 13):
    fp = os.path.join(TMP, 's%02d.nc' % m)
    got = False
    for k in range(2):
        r = subprocess.run(['curl','-sL','-m','420','--noproxy','*','-o',fp,'-w','%{http_code}',
                            '-A','Mozilla/5.0', url(m)], capture_output=True)
        code = (r.stdout or b'').decode().strip()
        if code == '200' and os.path.exists(fp) and os.path.getsize(fp) > 5000:
            got = True; break
        print('  %02d 月 http=%s size=%s' % (m, code or '-', os.path.getsize(fp) if os.path.exists(fp) else 0), flush=True)
        time.sleep(3)
    if not got:
        continue
    try:
        d = xr.open_dataset(fp)
        t = np.asarray(d['Tropical_Cyclone_Heat_Potential'].values, np.float64)
        z = np.asarray(d['D26'].values, np.float64)
        la = np.asarray(d['latitude'].values); lo = np.asarray(d['longitude'].values)
        d.close()
    except Exception as ex:
        print('  %02d 读取失败 %s' % (m, str(ex)[:70])); continue
    while t.ndim > 2: t = t[0]; z = z[0]
    if t.shape == (lo.size, la.size): t = t.T; z = z.T
    if la[0] > la[-1]: t = t[::-1]; z = z[::-1]; la = la[::-1]
    if ok == 0:
        print('  源 %s lat %d(%0.2f~%0.2f) lon %d' % (t.shape, la.size, la.min(), la.max(), lo.size))
    if t.shape != (120, 360):
        print('  形状 %s 不符' % (t.shape,)); continue
    T[m - 1] = np.nan_to_num(t); D[m - 1] = np.nan_to_num(z); ok += 1
print('成功 %d/12 月' % ok)
if ok:
    OUT = os.path.join(ROOT, 'simulator', 'env', 'climate', 'tchp.npz')
    np.savez_compressed(OUT, tchp=T, d26=D, year=np.int32(YEAR))
    print('-> %s (%.1f MB)' % (os.path.basename(OUT), os.path.getsize(OUT) / 1e6))
    for m in (2, 9):
        s = T[m - 1]
        print('  %d月 TCHP min %.0f 均 %.0f max %.0f   D26 %.0f~%.0f m   为0的格点 %.2f'
              % (m, s.min(), s.mean(), s.max(), D[m-1].min(), D[m-1].max(), float((s <= 0.5).mean())))
