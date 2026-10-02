"""按**来源**分别统计阶段码占比, 并修掉纬度解析(ATCF 是 1/10 度)。

用户提示: 自评的 WV/LO 占比要单独看 —— 因为 xrq/wp 是官方最佳路径(只收命名台风),
与自评(含扰动)混在一起比会失真。
用法: python tools/diag_phase_by_source.py
"""
import collections
import glob
import os
import sys

import numpy as np

sys.path.insert(0, '.')

SOURCES = [
    ('模型(2025 归档)', r'output\sim\2025\2025', True),
    ('自评 2025', r'G:\气象\数据\自评\2025', False),
    ('自评 2026', r'G:\气象\数据\自评\2026', False),
    ('xrq/2025', r'G:\气象\数据\xrq\2025', False),
    ('xrq/wp(官方)', r'G:\气象\数据\xrq\wp', False),
]
KEYS = ['DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD']


def parse(path):
    out = []
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for l in f:
                c = [x.strip() for x in l.split(',')]
                if len(c) < 11:
                    continue
                try:
                    # **ATCF 纬度/经度是 1/10 度**: 138N = 13.8N
                    la = float(c[6][:-1]) / 10.0 * (1 if c[6][-1] in 'Nn' else -1)
                    v = float(c[8])
                except Exception:
                    continue
                out.append((la, v, c[10].upper()))
    except OSError:
        pass
    return out


def main():
    print('%-18s %6s | %s' % ('来源', '系统', '阶段码记录占比'))
    for tag, d, ismodel in SOURCES:
        if ismodel:
            paths = glob.glob(os.path.join(d, '*', '*.dat'))
        else:
            paths = glob.glob(os.path.join(d, '**', '*.dat'), recursive=True)
        cnt = collections.Counter()
        n = 0
        latmax = []
        for p in paths:
            r = parse(p)
            if len(r) < 2:
                continue
            n += 1
            cnt.update(x[2] for x in r)
            latmax.append(max(abs(x[0]) for x in r))
        if n == 0:
            print('%-18s 无数据' % tag)
            continue
        tot = sum(cnt.values()) or 1
        shares = ' '.join('%s %4.1f%%' % (k, 100 * cnt.get(k, 0) / tot) for k in KEYS if cnt.get(k))
        print('%-18s %6d | %s' % (tag, n, shares))
        if latmax:
            a = np.array(latmax)
            print('%-18s        | 一生最高|纬| 中位 %.1f 度 | 到过 >=28 度的占 %.0f%%'
                  % ('', np.median(a), 100 * (a >= 28).mean()))
    print()
    print('注意: xrq/wp 是官方最佳路径(只收命名台风), 与自评(含扰动)不是同一口径,')
    print('      必须**分开**与模型比 —— 这是本会话第 13 次口径问题。')


if __name__ == '__main__':
    main()
