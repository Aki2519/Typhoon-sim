# -*- coding: utf-8 -*-
"""环境汇**触发频率**诊断: 为什么 34-63kt 的弱台风一碰环境就崩塌。

轮次 103 的寿命四段分解(与观测同口径)显示:
    峰值 -> 最后 34kt   模型 **9-42h**  vs 观测 **36-60h**
而基线衰减 `_r6` 在 34-48kt 只有 **-2.0 kt/6h**(14kt 的落差本应走 ~42h)。
=> 崩塌不可能来自基线, 只能来自**环境汇**(登陆 -6~-13 / 冷海 -6 / 强切变 -5,
相加后封顶 -18 kt/6h)。本脚本逐强度档统计三个汇的**触发频率**, 并对照观测口径的
"减弱步"比例, 判断是不是门槛(land>0.3 / sst<25.5 / shear>=30)对弱台风**过度触发**。

用法: python tools/probe_sink_trigger.py --seed 20250801 --days 90
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

BANDS = [(0, 34), (34, 48), (48, 64), (64, 80), (80, 100), (100, 999)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=90.0)
    ap.add_argument('--start', default='2025-06-01 00')
    a = ap.parse_args()

    from simulator.simcore import core as V
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    acc = {b: collections.Counter() for b in BANDS}
    n_steps = {b: 0 for b in BANDS}
    dec = {b: [] for b in BANDS}
    for _ in range(int(a.days * 24 / 6)):
        V.sim_step(sim, 6.0)
        for t in sim['tcs']:
            if t['dead']:
                continue
            env = t.get('env')
            if not env:
                continue
            v = float(t['vmax'])
            b = next((bb for bb in BANDS if bb[0] <= v < bb[1]), None)
            if b is None:
                continue
            n_steps[b] += 1
            if float(env.get('land', 0.0)) > 0.3:
                acc[b]['land>0.3'] += 1
                acc[b]['land_kt'] += min(13.0, 6.0 + 7.0 * min(1.0, float(env['land'])))
            if float(env.get('sst', 30.0)) < 25.5:
                acc[b]['sst<25.5'] += 1
                acc[b]['sst_kt'] += min(6.0, 5.0 * (25.5 - float(env['sst'])))
            if float(env.get('shear', 0.0)) >= 30.0:
                acc[b]['shear>=30'] += 1
                acc[b]['shear_kt'] += 5.0
            acc[b]['vmax_sum'] += v
    print('=== %s 起 %d 天, seed=%s ===' % (a.start, a.days, a.seed))
    print('%-10s %8s | %-30s | %-30s | %s'
          % ('强度档', '步数', '汇触发频率(land/sst/shear)', '平均汇深度(kt/6h, 三项和)', '平均强度'))
    for b in BANDS:
        n = n_steps[b]
        if n < 50:
            print('%-10s %8d  样本不足' % ('%d-%d' % (b[0], min(b[1], 999)), n))
            continue
        f_land = acc[b]['land>0.3'] / n
        f_sst = acc[b]['sst<25.5'] / n
        f_sh = acc[b]['shear>=30'] / n
        # 平均汇深度只对**触发**的步求(否则被零步稀释), 再乘频率得到"每步期望汇"
        exp = ((acc[b]['land_kt'] + acc[b]['sst_kt'] + acc[b]['shear_kt']) / n)
        print('%-10s %8d | land %4.0f%%  sst %4.0f%%  shear %4.0f%% | 期望 %5.2f kt/6h | %6.1f'
              % ('%d-%d' % (b[0], min(b[1], 999)), n,
                 100 * f_land, 100 * f_sst, 100 * f_sh, exp, acc[b]['vmax_sum'] / n))
    print()
    print('判读: 若 34-64kt 档的**期望汇深度**已经接近甚至超过基线 `_r6`(-2.0~-3.5 kt/6h),')
    print('      说明弱台风的崩塌是环境汇造成的, 而观测里同档还能活 3-5 天 =>')
    print('      该做的是**按强度分档削弱环境汇**(弱系统对冷海/切变的敏感性本就低于强系统),')
    print('      而不是动基线衰减(基线已被观测的峰值后降幅标定过)。')


if __name__ == '__main__':
    main()
