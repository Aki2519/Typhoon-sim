# -*- coding: utf-8 -*-
"""**生成点抽样器的离线体检**(轮次 133, 不需要推进物理)。

为什么需要: 轮次 129 修掉了"生成纬轴被 1.5 倍拉伸"的 bug(见 `gen_check` 里的注释),
但那是一次**结构性**改动 —— 修完必须回答两件事:
  ① 抽出来的点, **纬度分布**对不对(与观测逐月比);
  ② 修完以后**海盆/经度**的构成有没有被带偏(修前大量点落在高纬冷海/陆地上被浪费,
     所以修后各海盆的**相对**份额会变)。

做法: 直接把 `_GEN_MAX_ALIVE` 放到无穷大、每抽一次就清空 `sim['tcs']`,
这样能干净地采到"抽样分布"本身(而不受存活上限与竞争的干扰), 每个月采 K 个。

观测对照(轮次 128 实测, 同月同海盆、首报纬度, 单位度):
    5月 7.7 | 6月 9.2 | 7月 13.1 | 8月 15.1 | 9月 12.9 | 10月 10.0 | 11月 7.0
(来源 `xrq/wp`, 只含西北太; 所以**海盆构成**只能作定性对照, 纬度可逐月对照。)

用法:
    python tools/diag_genesis_sampler.py --per-month 200
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

#: 观测逐月生成纬中位(度, xrq/wp, 首报; 轮次 128)
OBS_MED = {5: 7.7, 6: 9.2, 7: 13.1, 8: 15.1, 9: 12.9, 10: 10.0, 11: 7.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-month', type=int, default=200)
    ap.add_argument('--seed', default='20250801')
    a = ap.parse_args()

    from simulator.simcore import core as V
    sim = V.init({'start': '2025-01-15 00', 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True,
                             'genesis': True}})
    old_cap = V._GEN_MAX_ALIVE
    V._GEN_MAX_ALIVE = 10 ** 9            # 体检抽样器本身, 不受存活上限干扰
    try:
        print('=== 生成点抽样器体检: 每月 %d 个 ===' % a.per_month)
        print('%4s | %6s %6s %6s | %6s %6s %6s | %-24s | %s'
              % ('月', '全P25', '全中位', '全P75', 'WP25', 'WP中位', 'WP75',
                 '海盆构成', '观测中位'))
        for m in range(1, 13):
            sim['t'] = (m - 0.5) * 30.4 * 24.0
            sim['month'] = m
            sim['month0'] = m - 1
            V.env_update(sim)
            lats, lons = [], []
            for _ in range(a.per_month):
                sim['tcs'].clear()
                V.gen_check(sim)
                for tc in sim['tcs']:
                    lats.append(float(tc['lat']))
                    lons.append(float(tc['lon']))
            if not lats:
                print('%4d |   -' % m)
                continue
            L = np.array(lats, float)
            # **必须单独看 WP**: 观测那列来自 `xrq/wp`(只含西北太), 而模型是全海盆抽样;
            # 东太/大西洋的生成点偏北, 混在一起会把中位抬高好几度(第 28 次口径问题的形态)。
            w = np.array([lo % 360.0 for lo in lons], float)
            W = L[(w >= 100.0) & (w <= 180.0)]
            B = collections.Counter(V.basin_of(lo, la) for lo, la in zip(lons, lats))
            tot = max(1, sum(B.values()))
            mix = ' '.join('%s %.0f%%' % (k, 100.0 * v / tot)
                           for k, v in B.most_common(4))
            q = (np.percentile(L, [25, 50, 75]) if L.size else np.full(3, np.nan))
            qw = (np.percentile(W, [25, 50, 75]) if W.size else np.full(3, np.nan))
            print('%4d | %6.1f %6.1f %6.1f | %6.1f %6.1f %6.1f | %-24s | %s'
                  % (m, q[0], q[1], q[2], qw[0], qw[1], qw[2], mix,
                     ('%.1f' % OBS_MED[m]) if m in OBS_MED else '-'))
        print()
        print('读法: **只看 WP 那三列**与"观测中位"比(观测只含西北太); 全海盆那三列只作参考。')
    finally:
        V._GEN_MAX_ALIVE = old_cap
    return 0


if __name__ == '__main__':
    sys.exit(main())
