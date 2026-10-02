"""风暴"死在哪": 用归档 b-deck 统计每个系统的终态(纬度 / 强度 / 阶段码)。

目的: 验证"EX 一条都没有"是否因为**风暴在够到变性条件(纬>=28 且 SST<26.5 且切变>=20
且 vmax>=35)之前就衰减成 LO**。
用法: python tools/diag_death_place.py
"""
import collections
import glob
import os
import sys

import numpy as np

sys.path.insert(0, '.')

MODEL = r'output\sim\2025\2025'
OBS = [r'G:\气象\数据\自评\2025', r'G:\气象\数据\自评\2026',
       r'G:\气象\数据\xrq\2025', r'G:\气象\数据\xrq\wp']


def parse(path):
    out = []
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for l in f:
                c = [x.strip() for x in l.split(',')]
                if len(c) < 11:
                    continue
                try:
                    la = float(c[6][:-1]) * (1 if c[6][-1] in 'Nn' else -1)
                    v = float(c[8])
                except Exception:
                    continue
                out.append((la, v, c[10].upper()))
    except OSError:
        pass
    return out


def report(tag, paths):
    ends = []
    lat_max = []
    for p in paths:
        r = parse(p)
        if len(r) < 2:
            continue
        ends.append(r[-1])
        lat_max.append(max(abs(x[0]) for x in r))
    if not ends:
        print('--- %s: 无数据' % tag)
        return
    v = np.array([e[1] for e in ends], float)
    la = np.array([e[0] for e in ends], float)
    lm = np.array(lat_max, float)
    ph = collections.Counter(e[2] for e in ends)
    print('--- %s(%d 个系统)---' % (tag, len(ends)))
    print('   终态强度中位 %.0f kt | 终态|纬|中位 %.0f 度 | **一生最高|纬| 中位 %.0f 度**'
          % (np.median(v), np.median(np.abs(la)), np.median(lm)))
    print('   终态强度分布: <20kt %.0f%% | 20-34 %.0f%% | >=34 %.0f%%'
          % (100 * (v < 20).mean(), 100 * ((v >= 20) & (v < 34)).mean(), 100 * (v >= 34).mean()))
    print('   **到达过 |纬|>=28 的系统: %.0f%%**(变性判据的纬度门槛)' % (100 * (lm >= 28).mean()))
    print('   终态阶段码: ' + '  '.join('%s %d(%.0f%%)' % (k, c, 100 * c / len(ends))
                                        for k, c in ph.most_common(6)))


def main():
    print('=== 模型: %s ===' % MODEL)
    report('模型', glob.glob(os.path.join(MODEL, '*', '*.dat')))
    print()
    print('=== 观测 ===')
    op = []
    for d in OBS:
        op += glob.glob(os.path.join(d, '**', '*.dat'), recursive=True)
    report('观测', op[:2500])
    print()
    print('判读: 若模型的"到达 |纬|>=28 的比例"远低于观测, 则 EX 缺失的原因是'
          '**风暴根本走不到变性纬度**; 若比例接近而仍无 EX, 则问题在变性判据本身。')


if __name__ == '__main__':
    main()
