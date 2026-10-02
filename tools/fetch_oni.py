# -*- coding: utf-8 -*-
"""抓 NOAA CPC 的 ONI(观测 Niño3.4 指数, 1950-今) -> oni.npz。
真值比调参数的自振子靠谱得多。"""
import os, re, subprocess
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def curl(url):
    r = subprocess.run(['curl','-sL','-m','60','--noproxy','*','-A','Mozilla/5.0',url],
                       capture_output=True)
    return (r.stdout or b'').decode('utf-8','ignore')
txt = curl('https://psl.noaa.gov/data/correlation/oni.data')
print('ONI 文本长度 %d' % len(txt))
rows = []
for line in txt.splitlines():
    m = re.match(r'\s*(\d{4})\s+(.*)$', line)
    if not m:
        continue
    yr = int(m.group(1))
    if yr < 1950:
        continue
    vals = [float(v) for v in m.group(2).split()[:12]]
    if len(vals) == 12:
        rows.append((yr, vals))
print('年份 %d 个: %s .. %s' % (len(rows), rows[0][0] if rows else '-', rows[-1][0] if rows else '-'))
if rows:
    y0 = rows[0][0]
    arr = np.full((rows[-1][0] - y0 + 1, 12), np.nan, np.float32)
    for yr, vals in rows:
        arr[yr - y0] = vals
    print('ONI 范围 %.2f ~ %.2f  非缺 %.2f%%' % (np.nanmin(arr), np.nanmax(arr),
          100 * np.isfinite(arr).mean()))
    OUT = os.path.join(ROOT, 'simulator', 'env', 'climate', 'oni.npz')
    np.savez_compressed(OUT, oni=arr, year0=np.int32(y0))
    print('-> %s  年份起点 %d' % (os.path.basename(OUT), y0))
else:
    print(txt[:300])
