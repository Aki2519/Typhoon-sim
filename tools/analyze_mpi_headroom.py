# -*- coding: utf-8 -*-
"""**强度上限诊断**: 强端偏低的病根是"MPI 天花板"还是"停留时间/机会"?

背景(任务 C): 8 种子实测 100-129 **12.6% vs 17%**、130+ **10.6% vs 18%**, 而峰值
P50/P90/P95 里只有 P90 显著偏低 —— 也就是说**不是"峰值不够高"**, 而是**上中段太薄**。
本轮(轮次 107)又发现: 模型在 10-30N 的向极速度是观测的 **2 倍**, 净位移却已对齐 =>
暖池停留时间只有一半。所以必须区分两种可能:

  (甲) **天花板型**: 风暴已经顶在 MPI 上 => `max(vpi) - peak` 很小。
       改法要动 SST/OHC/出流/`mpi()` 本身。
  (乙) **机会型**: 风暴离 MPI 还很远就衰减了 => `max(vpi) - peak` 很大。
       改法要动停留时间(寿命、环境汇、退出暖池的速度)。

本工具直接量这个差: 按峰值分档统计 `peak`、`max(vpi)`、`peak 时刻的 vpi`、
以及"vpi>vmax 的步占比"(= 处于可增长条件的比例)。

数据源: `tools/dump_intensity_samples.py` 产出的 npz(逐 6h 的
vmax/sst/shear/ohc/rh/vpi/rmw/weak/erc/org/tc/seed)。

用法:
    python tools/analyze_mpi_headroom.py --samples "output/sim/_reference/samples_r84/samples_*.npz"
"""
from __future__ import annotations

import argparse
import glob

import numpy as np

BANDS = ((34, 64, '34-63kt'), (64, 100, '64-99kt'),
         (100, 130, '100-129kt'), (130, 999, '>=130kt'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', default='output/sim/_reference/samples_r84/samples_*.npz')
    a = ap.parse_args()
    fs = sorted(glob.glob(a.samples))
    if not fs:
        print('没有样本文件: %s' % a.samples)
        return
    d = {k: [] for k in ('vmax', 'vpi', 'sst', 'ohc', 'shear', 'rh', 'seed', 'tc')}
    for f in fs:
        z = np.load(f)
        for k in d:
            if k in z.files:
                d[k].append(z[k])
    V = {k: np.concatenate(v) for k, v in d.items() if v}
    key = (V['seed'].astype(np.int64) * 100000 + V['tc'].astype(np.int64))
    n_storm = 0
    rows = []
    for k in np.unique(key):
        m = key == k
        vm = V['vmax'][m]
        vp = V['vpi'][m]
        if vm.size < 4:
            continue
        n_storm += 1
        pk = float(vm.max())
        rows.append((pk, float(vp.max()), float(vp[int(np.argmax(vm))]),
                     float((vp > vm).mean()), vm.size,
                     float(V['sst'][m].max()), float(V['ohc'][m].max()),
                     float(V['shear'][m].min())))
    R = np.array(rows, float)
    pk = R[:, 0]
    print('=== %d 个风暴(来自 %d 个样本文件) ===' % (n_storm, len(fs)))
    print('%-11s %6s | %-16s | %-16s | %-16s | %s'
          % ('峰值档', 'n', 'peak 中位', 'max(vpi) 中位', 'peak 步的 vpi',
             'vpi>vmax 步占比 | 寿命(步)'))
    for lo, hi, tag in BANDS:
        m = (pk >= lo) & (pk < hi)
        if m.sum() < 3:
            continue
        print('%-11s %6d | %6.1f          | %6.1f          | %6.1f          | %5.0f%%          | %5.1f'
              % (tag, int(m.sum()), np.median(R[m, 0]), np.median(R[m, 1]),
                 np.median(R[m, 2]), 100 * np.median(R[m, 3]), np.median(R[m, 4])))
    print()
    print('**关键量: max(vpi) - peak**(>0 = 离天花板还有多远; 越小越是"顶在 MPI 上")')
    for lo, hi, tag in BANDS:
        m = (pk >= lo) & (pk < hi)
        if m.sum() < 3:
            continue
        gap = R[m, 1] - R[m, 0]
        print('  %-11s n=%4d | 中位 %+6.1f kt | p25 %+6.1f | p75 %+6.1f | <=5kt 的占 %3.0f%%'
              % (tag, int(m.sum()), np.median(gap), np.percentile(gap, 25),
                 np.percentile(gap, 75), 100 * (gap <= 5.0).mean()))
    print()
    print('判读: 若 >=100kt 档的 `max(vpi)-peak` 中位**接近 0**(<=5kt 占多数)=> 天花板型,')
    print('      该动 MPI(SST/OHC/出流)或"顶部的刹车"; 若中位仍有 20-40kt => 机会型,')
    print('      该动**停留时间**(寿命/环境汇/退出暖池的速度), 而不是继续抬 MPI。')


if __name__ == '__main__':
    main()
