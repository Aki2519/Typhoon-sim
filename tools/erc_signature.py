"""ERC 两型签名复验(目标 4)。

要证的三件事:
  1. **频率**: 有多少风暴经历过 ERC(观测口径见 ERC_CALIBRATION.md);
  2. **时长**: T 与 RMW 的关系 —— 20nm -> 24h, 2.5nm -> 45min(=0.75h), 即 T ∝ RMW^1.7;
  3. **低谷幅度**: **普通式**掉得多(观测实例 145->120、160->130, 即掉 15-30kt),
     **融合式**几乎不掉(140->135、160->155, 掉约 5kt)。

用法: python tools/erc_signature.py --seed 20250801 --days 120
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V            # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=120.0)
    ap.add_argument('--start', default='2025-07-01 00')
    ap.add_argument('--random', type=float, default=0.5,
                    help='抖动强度(0=关掉抖动)。用于验证低谷是不是被噪声淹没')
    a = ap.parse_args()

    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True,
                             'random': a.random}})

    live = {}          # id(tc) -> 事件起点信息
    prev = {}          # id(tc) -> **上一步**的强度(关键: 事件在 sim_step 内部就生效了,
                       #            若用检测到的那一步当起点, 会漏掉最大的一次亏损)
    events = []
    nstorm = 0
    for _ in range(int(a.days * 24 / 6)):
        V.sim_step(sim, 6.0)
        for t in sim['tcs']:
            key = id(t)
            e = t.get('erc')
            if e and key not in live:
                # v0 取**上一步**的值: 触发那一步的亏损已经在本次 sim_step 里施加了。
                # (轮次 51 查明: 用当前步当起点会系统性漏掉最大亏损步, 实测偏差约 +10kt,
                #  一度让我误判"机制没生效"。)
                live[key] = dict(T=float(e['T']), merger=bool(e.get('merger')),
                                 v0=float(prev.get(key, t['vmax'])), rmw0=float(t['rmw']),
                                 t0=float(sim['t']))
            elif not e and key in live:
                s = live.pop(key)
                events.append(dict(s, v1=float(t['vmax']),
                                   dur=float(sim['t']) - s['t0']))
        prev = {id(t): float(t['vmax']) for t in sim['tcs']}
    nstorm = len(sim['tcs'])
    n_erced = len({k for k in live}) + 0     # 仍在进行中的算上事件本身
    ev = events
    print('=== ERC 签名(seed %s, %d 天, 起于 %s) ===' % (a.seed, a.days, a.start))
    print('抖动 random=%.2f' % a.random)
    print('风暴总数 %d | 完成的 ERC 事件 %d 次 | 在跑未完成 %d 次'
          % (nstorm, len(ev), len(live)))
    if not ev:
        print('** 没有完整事件, 无法复验; 可能采样期太短。**')
        return
    mg = [x for x in ev if x['merger']]
    od = [x for x in ev if not x['merger']]
    print('两型比例: 融合式 %d / 普通式 %d' % (len(mg), len(od)))
    print()
    print('%-8s %5s %10s %12s %14s' % ('类型', 'n', '时长中位h', 'RMW中位nm', '强度变化中位kt'))
    for nm, grp in (('融合式', mg), ('普通式', od)):
        if not grp:
            continue
        print('%-8s %5d %10.1f %12.1f %14.1f'
              % (nm, len(grp), np.median([x['T'] for x in grp]),
                 np.median([x['rmw0'] for x in grp]),
                 np.median([x['v1'] - x['v0'] for x in grp])))
    print()
    print('T 与 RMW 的关系(应满足 T = 24*(RMW/20)^1.7):')
    for lo, hi in ((0, 5), (5, 12), (12, 20), (20, 40)):
        g = [x for x in ev if lo <= x['rmw0'] < hi]
        if g:
            print('   RMW %2d-%2dnm: n=%-3d T 中位 %6.2f h   (公式值 %6.2f h)'
                  % (lo, hi, len(g), np.median([x['T'] for x in g]),
                     24.0 * (max(0.5, np.median([x['rmw0'] for x in g])) / 20.0) ** 1.7))
    print()
    print('观测基准(ERC_CALIBRATION.md / 用户实例):')
    print('   普通式: 强度掉 15-30kt(145->120、160->130)')
    print('   融合式: 强度掉约 5kt(140->135、160->155)')
    print('   RMW 2.5nm -> T = 0.75h(45 分钟); RMW 20nm -> T = 24h')
    print('   小眼 => 融合式概率高')


if __name__ == '__main__':
    main()
