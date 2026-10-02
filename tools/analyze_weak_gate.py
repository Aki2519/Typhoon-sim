# -*- coding: utf-8 -*-
"""**内核副热带门槛(SD/SS)的可达性**: `weak > 20` 到底够不够得到?

轮次 110 把**分类器**那一侧查清了(MD 完全不可达;SS 只在 `front=True` 时可达;
而 `front` 只由内核的 ET 分支给出)。那么**内核**这一侧呢?
`core.intensity_step` 把 `cuClass` 从 TC 转 SD 的条件是:

    |lat| >= 25  且  (sst < 25.5 或 shear >= 20)  且
        ( weak > 20
          或 (age > 168 且 vmax < 50 且 (sst < 24.5 或 |lat| > 32))
          或 (land > 0.6 且 vmax < 45 且 age > 24) )

其中 `weak` 的动力学是: `vmax<60 且 (sst<25.5 或 shear>35 或 land>0.25)` 时每 6h **+1**,
否则每 6h **-0.25**。=> 要 `weak>20` 需要**约 20 个不利步(120h)**, 而"有利步"只扣 0.25
(几乎是一路单向累加)。这是 SS/SD 现在一个都出不来的**最可疑的拦路虎**。

本工具从逐 6h 样本(`tools/dump_intensity_samples.py` 的 npz)直接量:
  · 每个风暴一生 `weak` 的**最大值**分布;
  · 同时满足 `|lat|>=25 且 (sst<25.5 或 shear>=20)` 的步占比;
  · **SD 门槛真正被满足**的风暴比例(逐个条件拆开报, 定位到底卡哪一条)。

用法:
    python tools/analyze_weak_gate.py --samples "output/sim/_reference/samples_r84/samples_*.npz"
"""
from __future__ import annotations

import argparse
import glob

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples',
                    default='output/sim/_reference/samples_r84/samples_*.npz')
    a = ap.parse_args()
    fs = sorted(glob.glob(a.samples))
    if not fs:
        print('没有样本文件: %s' % a.samples)
        return
    keys = ('vmax', 'sst', 'shear', 'lat', 'weak', 'seed', 'tc')
    acc = {k: [] for k in keys}
    for f in fs:
        z = np.load(f)
        for k in keys:
            if k in z.files:
                acc[k].append(np.asarray(z[k], dtype=np.float64))
    V = {k: np.concatenate(v) for k, v in acc.items() if v}
    key = V['seed'].astype(np.int64) * 100000 + V['tc'].astype(np.int64)

    n = 0
    wmax, sub_steps, hit = [], [], 0
    hit_w, hit_alt, hit_lat_ev = 0, 0, 0
    for k in np.unique(key):
        m = key == k
        if m.sum() < 4:
            continue
        n += 1
        lat = np.abs(V['lat'][m])
        sst = V['sst'][m]
        sh = V['shear'][m]
        vm = V['vmax'][m]
        wk = V['weak'][m]
        age = 6.0 * np.arange(m.sum())          # 近似: 每步 6h, 从 0 起
        wmax.append(float(wk.max()))
        base = (lat >= 25.0) & ((sst < 25.5) | (sh >= 20.0))
        sub_steps.append(float(base.mean()))
        c_lat_ev = bool(base.any())
        c_weak = bool((base & (wk > 20.0)).any())
        c_alt = bool(((lat >= 25.0) & (age > 168) & (vm < 50)
                      & ((sst < 24.5) | (lat > 32))).any())
        if c_lat_ev:
            hit_lat_ev += 1
        if c_weak:
            hit_w += 1
        if c_alt:
            hit_alt += 1
        if c_weak or c_alt:
            hit += 1
    W = np.array(wmax, float)
    print('=== %d 个风暴(来自 %d 个样本文件) ===' % (n, len(fs)))
    print('一生 `weak` 最大值: 中位 %.1f | p75 %.1f | p90 %.1f | 最大 %.1f'
          % (np.median(W), np.percentile(W, 75), np.percentile(W, 90), W.max()))
    print('  weak>20 的风暴 %d (%.0f%%) | >10 的 %d (%.0f%%) | >5 的 %d (%.0f%%)'
          % (int((W > 20).sum()), 100 * (W > 20).mean(),
             int((W > 10).sum()), 100 * (W > 10).mean(),
             int((W > 5).sum()), 100 * (W > 5).mean()))
    print()
    print('SD 门槛的三个分支各自被满足的风暴:')
    print('  A) |lat|>=25 且 (sst<25.5 或 shear>=20) 出现过          : %4d (%.0f%%)'
          % (hit_lat_ev, 100.0 * hit_lat_ev / max(1, n)))
    print('  B) A 且 **weak>20**(主分支)                            : %4d (%.0f%%)'
          % (hit_w, 100.0 * hit_w / max(1, n)))
    print('  C) 年龄>168h 且 vmax<50 且 (sst<24.5 或 |lat|>32)        : %4d (%.0f%%)'
          % (hit_alt, 100.0 * hit_alt / max(1, n)))
    print('  **B 或 C(即内核真的会转 SD)**                            : %4d (%.0f%%)'
          % (hit, 100.0 * hit / max(1, n)))
    print()
    print('判读:')
    print('  · 若 B 的比例很低而 A 很高 => 卡在 **`weak>20`**: 该门槛需要约 20 个不利步,')
    print('    而模型的副热带风暴往往还没积够就衰减/登陆了。')
    print('  · 若 A 本身很低 => 卡在**副热带环境判据**(sst/shear)或纬度, 与 weak 无关。')
    print('  · 注意口径: 本工具的 `age` 用"样本序号 x 6h"近似(样本逐 6h 记录),')
    print('    而内核的 `age` 从生成时刻起算 —— 两者在长寿命风暴上会有偏差,')
    print('    所以 C 分支的数字只作量级参考, **B 分支不受影响**。')


if __name__ == '__main__':
    main()
