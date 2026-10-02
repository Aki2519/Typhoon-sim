# -*- coding: utf-8 -*-
"""**生成速率标定曲线**: `_GEN_TRIES -> 真年生成量 / 真年发展量`。

为什么需要它(轮次 115-116 的发现): `_sim_step_core` 会剔掉死亡超过 120 天的台风,
所以"一年跑完时的 `len(sim['tcs'])`"**在 120 天处饱和**, 不是年生成量。
把 **`make_tc` 包一层计数器**才拿得到真值(实测 135 天: 生成 238、发展 168)。
于是当年为了把**饱和量**抬到 ~190 而把 `_GEN_TRIES` 从 6 提到 19,
实际上是把**真年生成量**抬到了 ~640(观测台账 ~200, 用户目标 250)。

本工具**不改源码**: 导入后直接覆盖模块常量 `V._GEN_TRIES`, 跑一段模拟, 报真值。
这样可以在不改变仓库物理状态的前提下, 一次扫出"旋钮 -> 真值"的响应曲线,
供任务 E(166-180 轮)按正确口径执行。

用法:
    python tools/probe_gen_rate.py --tries 19 --days 135 --seed 20250801
    python tools/probe_gen_rate.py --tries 19,14,10,7 --days 135
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

OBS = {'dist': 200, 'dev': 103, 'user_dist': 250, 'user_dev': 120}


def run_one(tries, days, seed, start='2025-01-01 00'):
    from simulator.simcore import core as V
    V._GEN_TRIES = int(tries)
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True,
                             'forecast': False}})
    gen = [0]
    peaks = {}
    orig = V.make_tc

    def _mk(s, *a, **k):
        gen[0] += 1
        return orig(s, *a, **k)

    V.make_tc = _mk
    t0 = time.time()
    try:
        for _ in range(int(days * 24 / 6)):
            V.sim_step(sim, 6.0)
            for t in sim['tcs']:
                if t['dead']:
                    continue
                k = id(t)
                v = float(t['vmax'])
                if v > peaks.get(k, 0.0):
                    peaks[k] = v
    finally:
        V.make_tc = orig
    pk = np.array(list(peaks.values()), float)
    dev = int((pk >= 34.0).sum())
    return {'tries': tries, 'days': days, 'gen': gen[0], 'dev': dev,
            'rate': gen[0] / days, 'drate': 100.0 * dev / max(1, gen[0]),
            'p50': float(np.percentile(pk, 50)) if pk.size else 0.0,
            'p90': float(np.percentile(pk, 90)) if pk.size else 0.0,
            'sec': time.time() - t0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tries', default='19')
    ap.add_argument('--days', type=float, default=135.0)
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--start', default='2025-01-01 00')
    a = ap.parse_args()
    rows = []
    for ts in [x for x in a.tries.split(',') if x.strip()]:
        r = run_one(float(ts), a.days, a.seed, a.start)
        rows.append(r)
        print('tries=%-5s | %d 天: 生成 %4d(%.2f/天) 发展 %4d(%.0f%%) | '
              '峰值 P50 %.0f P90 %.0f | %.0fs'
              % (ts, a.days, r['gen'], r['rate'], r['dev'], r['drate'],
                 r['p50'], r['p90'], r['sec']), flush=True)
    k = 365.0 / a.days
    print()
    print('=== 外推到年(按 %.0f 天窗口 x %.2f) ===' % (a.days, k))
    print('%-8s %-14s %-14s %-12s %s' % ('tries', '年生成', '年发展', '年发展率', '与目标比'))
    for r in rows:
        print('%-8s %-14.0f %-14.0f %-12.0f%% 生成 %.2fx / 发展 %.2fx'
              % (r['tries'], r['gen'] * k, r['dev'] * k, r['drate'],
                 r['gen'] * k / OBS['user_dist'], r['dev'] * k / OBS['user_dev']))
    print()
    print('目标(用户口径): 生成 **250**/年, 发展 **~120**/年(发展率 ~48%%)。')
    print('观测台账: 生成 ~200/年, 发展 ~103/年(发展率 51.5%%)。')
    print('注: 本工具**不改源码**(只在内存里覆盖 `V._GEN_TRIES`), 跑完仓库状态不变。')


if __name__ == '__main__':
    main()
