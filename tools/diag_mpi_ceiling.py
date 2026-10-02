"""诊断: 130kt+ 占比上不去的天花板在哪里?

思路: 对每个风暴找出它**达到峰值强度那一步**的 MPI(vpi), 看两者的关系。
     · 若风暴峰值 < 130 而那些风暴的 vpi 本身就 < 140 ⇒ 天花板在 **MPI(环境场/MPI 公式)**,
       调 RI 是白费力气;
     · 若 vpi 明显高于峰值(比如 vpi 160 而峰值只有 110)⇒ 天花板在**强度路径**,
       继续调增长支/RI 才有意义。

用法: python tools/diag_mpi_ceiling.py --samples "output/sim/_reference/samples_diag/samples_*.npz"
"""
import argparse
import glob
import sys

import numpy as np

BINS = [(-1, 34), (34, 64), (64, 100), (100, 130), (130, 160), (160, 999)]
NAMES = ['<34', '34-64', '64-100', '100-130', '130-160', '160+']


def load_storms(pattern):
    out = []
    for p in sorted(glob.glob(pattern)):
        d = np.load(p, allow_pickle=True)
        tc = d['tc']
        v = d['vmax'].astype(np.float64)
        vpi = d['vpi'].astype(np.float64)
        for sid in np.unique(tc):
            m = tc == sid
            if m.sum() < 2:
                continue
            vv, pp = v[m], vpi[m]
            k = int(np.argmax(vv))
            out.append((float(vv.max()), float(pp[k]), float(np.median(pp))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', required=True)
    a = ap.parse_args()
    st = load_storms(a.samples)
    if not st:
        print('没有样本'); return
    pk = np.array([x[0] for x in st])
    vpi_pk = np.array([x[1] for x in st])
    vpi_med = np.array([x[2] for x in st])
    print('风暴数 %d' % len(st))
    print()
    print('按**峰值强度**分组, 看该组"峰值那一步的 MPI":')
    print('%-9s %5s %12s %12s %12s' % ('峰值档', 'n', '峰值中位', '峰值步MPI中位', '全程MPI中位'))
    for (lo, hi), nm in zip(BINS, NAMES):
        m = (pk > lo) & (pk <= hi)
        if m.sum() == 0:
            continue
        print('%-9s %5d %12.0f %12.0f %12.0f'
              % (nm, int(m.sum()), np.median(pk[m]), np.median(vpi_pk[m]), np.median(vpi_med[m])))
    print()
    print('MPI 本身的分布(峰值那一步): P50 %.0f  P75 %.0f  P90 %.0f  P95 %.0f  最大 %.0f'
          % (np.percentile(vpi_pk, 50), np.percentile(vpi_pk, 75),
             np.percentile(vpi_pk, 90), np.percentile(vpi_pk, 95), vpi_pk.max()))
    print('峰值本身的分布:              P50 %.0f  P75 %.0f  P90 %.0f  P95 %.0f  最大 %.0f'
          % (np.percentile(pk, 50), np.percentile(pk, 75),
             np.percentile(pk, 90), np.percentile(pk, 95), pk.max()))
    print()
    # 关键指标: 有多少风暴"本可以更强"(MPI 明显高于峰值)
    gap = vpi_pk - pk
    print('**未用尽的强度余量**(峰值步 MPI - 峰值强度):')
    for thr in (0, 20, 40, 60):
        print('   余量 > %3d kt 的风暴占 %.0f%%' % (thr, 100.0 * (gap > thr).mean()))
    print()
    print('判读: 若"峰值 100-130 档"的 MPI 中位也只有 ~130-140, 天花板在 MPI;')
    print('      若它们的 MPI 中位在 160+, 天花板在强度路径(可继续调 RI/增长支)。')


if __name__ == '__main__':
    main()
