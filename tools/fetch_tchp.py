# -*- coding: utf-8 -*-
"""A: 下 AOML TCHP + D26(真 OHC, 0.25°) 每月 15 日 12z -> 1° 月场 npz。"""
import os, subprocess, time
import numpy as np, xarray as xr
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'tchp')
os.makedirs(TMP, exist_ok=True)
BASE = 'https://cwcgom.aoml.noaa.gov/erddap/griddap/aomlTCHP.nc?'
YEAR = 2025

def dl(url, fp, t=300):
    for _ in range(3):
        r = subprocess.run(['curl', '-sL', '-m', str(t), '--noproxy', '*',
                            '-o', fp, '-w', '%{http_code}',
                            '-A', 'Mozilla/5.0', url], capture_output=True)
        if (r.stdout or b'').decode().strip() == '200' and os.path.getsize(fp) > 10000:
            return True
        time.sleep(2)
    return False

tchp = np.zeros((12, 120, 360), np.float32)
d26 = np.zeros((12, 120, 360), np.float32)
lat = lon = None
for m in range(1, 13):
    fp = os.path.join(TMP, '%d%02d.nc' % (YEAR, m))
    if not (os.path.exists(fp) and os.path.getsize(fp) > 10000):
        q = ('Tropical_Cyclone_Heat_Potential[(%d-%02d-14T12:00:00Z):1:(%d-%02d-16T12:00:00Z)]'
             '[(-60.0):1:(60.0)][(0.0):1:(360.0)],'
             'D26[(%d-%02d-14T12:00:00Z):1:(%d-%02d-16T12:00:00Z)]'
             '[(-60.0):1:(60.0)][(0.0):1:(360.0)]'
             % (YEAR, m, YEAR, m, YEAR, m, YEAR, m))
        if not dl(BASE + q.replace(',', '%2C').replace('[', '%5B').replace(']', '%5D').replace(':', '%3A'), fp):
            print('  %02d 月下载失败' % m); continue
    try:
        d = xr.open_dataset(fp)
        t = np.asarray(d['Tropical_Cyclone_Heat_Potential'].values, np.float64)
        z = np.asarray(d['D26'].values, np.float64)
        la = np.asarray(d['latitude'].values); lo = np.asarray(d['longitude'].values)
        d.close()
    except Exception as ex:
        print('  %02d 月读取失败 %s' % (m, str(ex)[:70])); continue
    if t.ndim == 4: t = t[:, 0]; z = z[:, 0]
    if t.ndim == 3: t = t[0]; z = z[0]
    if t.shape == (lo.size, la.size): t = t.T; z = z.T
    if lat is None:
        lat, lon = la, lo
        print('源 %s lat %.2f~%.2f lon %.2f~%.2f' % (t.shape, la.min(), la.max(), lo.min(), lo.max()))
    if la[0] > la[-1]: t = t[::-1]; z = z[::-1]; la = la[::-1]
    j0 = int(np.argmin(np.abs(la + 60.0))); i0 = int(np.argmin(np.abs(lo - 0.0)))
    st = np.nan_to_num(t[i0:i0 + 1440, j0:j0 + 480], nan=0.0)
    sz = np.nan_to_num(z[i0:i0 + 1440, j0:j0 + 480], nan=0.0)
    tchp[m - 1] = np.nanmean(st.reshape(360, 4, 120, 4), axis=(1, 3)).T
    d26[m - 1] = np.nanmean(sz.reshape(360, 4, 120, 4), axis=(1, 3)).T
OUT = os.path.join(ROOT, 'simulator', 'env', 'climate', 'tchp.npz')
np.savez_compressed(OUT, tchp=tchp, d26=d26, year=np.int32(YEAR))
print('-> %s (%.1f MB)' % (os.path.basename(OUT), os.path.getsize(OUT) / 1e6))
for m in (2, 9):
    s = tchp[m - 1]
    print('  %d月 TCHP: min %.0f 均 %.0f max %.0f kJ/cm2   D26: %.0f~%.0f m'
          % (m, s.min(), s.mean(), s.max(), d26[m - 1].min(), d26[m - 1].max()))
    cold = tchp[m - 1][np.isfinite(tchp[m - 1])]
    print('     为 0 的格点比例 %.2f (SST<26.5 处应为 0)' % float((s <= 0.5).mean()))
