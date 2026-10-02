"""观测步长的**零步比例**与"剔除零步后"的真实分布(目标 ②,轮次 38)。

为什么需要它:
    rate_tables.json 里 `up = (rate>0)`、`down = (rate<=0)` —— 零步被并进 down,
    所以那张表的"升占比 35%"与"降均值 −2.63"都是**被零步稀释过的**。
    拿它们跟模型比会得出错误的"偏度"结论(模型几乎没有精确零步)。
    本脚本用与 stat_rate_table.py **逐行相同的口径**重算,并显式给出零步比例。

用法: python tools/obs_zero_check.py
"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
from stat_rate_table import read_storm, _bin, V_BINS          # noqa: E402

GROUPS = {
    'xrq_2025': sorted(glob.glob(r'G:\气象\数据\xrq\2025\*.dat')),
    'xrq_2026': sorted(glob.glob(r'G:\气象\数据\xrq\2026\*.dat')),
    'xrq_wp': sorted(glob.glob(r'G:\气象\数据\xrq\wp\*.dat')),
    'zi': sum([sorted(glob.glob(os.path.join(d, '**', '*.dat'), recursive=True))
               for d in (r'G:\气象\数据\自评\2025', r'G:\气象\数据\自评\2026')], []),
    'miwu': sorted(glob.glob(os.path.join(r'G:\气象\数据\miwu', '**', '*.dat'),
                             recursive=True)),
}
ZERO_TOL = 0.5          # |kt/6h| < 0.5 视为"零步"(记录未变的量级)


def main():
    paths = [f for k in GROUPS for f in GROUPS[k]]
    per = {}
    nf = 0
    for f in paths:
        rec = read_storm(f)
        if len(rec) < 3:
            continue
        nf += 1
        for (t0, la0, lo0, v0), (t1, la1, lo1, v1) in zip(rec, rec[1:]):
            gap = (t1 - t0).total_seconds() / 3600.0
            if not (0 < gap <= 12.0):
                continue
            rate = (v1 - v0) / gap * 6.0
            b = _bin(v0, V_BINS)
            if b is None:
                continue
            per.setdefault(b, []).append(rate)

    print('扫描 %d 个文件(与 rate_tables.json 同源、同口径)' % nf)
    print('%-8s %7s %7s %7s | %s' % ('档', 'n', '零步%', '真实升占比', '剔除零步后的升/降均值'))
    tot = 0
    for _lo, _hi, b in V_BINS:
        a = np.asarray(per.get(b) or [], float)
        if a.size == 0:
            continue
        tot += a.size
        z = np.abs(a) < ZERO_TOL
        nz = a[~z]
        up, dn = nz[nz > 0], nz[nz < 0]
        print('%-8s %7d %6.0f%% %6.0f%%    升 n=%-6d 均 %+6.2f | 降 n=%-6d 均 %+6.2f'
              % (b, a.size, 100 * z.mean(),
                 100 * up.size / nz.size if nz.size else float('nan'),
                 up.size, up.mean() if up.size else float('nan'),
                 dn.size, dn.mean() if dn.size else float('nan')))
        print('%-8s   p10 %+6.2f  p25 %+6.2f  p50 %+6.2f  p75 %+6.2f  p90 %+6.2f   mean %+6.2f'
              % ('', np.percentile(a, 10), np.percentile(a, 25), np.percentile(a, 50),
                 np.percentile(a, 75), np.percentile(a, 90), a.mean()))
    print('合计 %d 步' % tot)


if __name__ == '__main__':
    main()
