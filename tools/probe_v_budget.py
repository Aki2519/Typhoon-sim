# -*- coding: utf-8 -*-
"""追踪单条模拟路径的**经向速度预算**: 引导 + β + 噪声 + 藤原 = mv, 到底哪一项把向极运动吃掉了。

轮次 102 的异常: 模型自己的 `vlat_profile`(由 track 记录算 dlat/dt)在
  0-10N = **-0.51 kt**、10-20N = **+0.42 kt**(观测 +2.17 / +3.07),
而同一位置用 `steering_at + beta_drift` 算出来的应该是 +1.2 / +3.9 kt。
两者差 2-3.5 kt ⇒ 一定有别的环节在动经向速度, 或者 track 记录的时间间隔不是 6h。
本脚本把每一步的 (lat, mv, steer_v, beta_v, noise_v, fuji_v) 逐行打出来定案。

用法: python tools/probe_v_budget.py --days 20 --seed 20250801
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=20.0)
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--start', default='2025-07-01 00')
    ap.add_argument('--top', type=int, default=4)
    a = ap.parse_args()

    from simulator.simcore import core as V
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    print('params.random = %s' % sim['params'].get('random'))
    trace = {}
    n = int(a.days * 24 / 6)
    for step in range(n):
        V.sim_step(sim, 6.0)
        for t in sim['tcs']:
            if t['dead']:
                continue
            if len(trace) >= a.top and id(t) not in trace:
                continue
            rec = trace.setdefault(id(t), [])
            st = V.steering_at(sim, t['lon'], t['lat'], t['vmax'])
            bv = V.beta_drift(t['lat'])
            rec.append((sim['t'], t['lat'], t['lon'], t['vmax'], t['mv'],
                        st[1], bv[1], t.get('fuji', 0.0)))
    # 顺便核对 track 记录的时间间隔
    for t in sim['tcs']:
        tr = t.get('track') or []
        if len(tr) >= 3:
            print('track 记录: n=%d, 相邻记录 t 差 = %s'
                  % (len(tr), [round(tr[i + 1]['t'] - tr[i]['t'], 3)
                               for i in range(min(4, len(tr) - 1))]))
            break
    print()
    for k, rec in list(trace.items())[:a.top]:
        print('=== 路径 %d 步 ===' % len(rec))
        print('%7s %7s %8s %6s | %7s | %7s %7s %7s | %7s'
              % ('t(h)', 'lat', 'lon', 'vmax', 'mv(实际)', 'steer_v', 'beta_v', 'fuji', '和'))
        for r in rec[::max(1, len(rec) // 14)]:
            print('%7.0f %7.2f %8.1f %6.0f | %+7.2f | %+7.2f %+7.2f %+7.2f | %+7.2f'
                  % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                     r[5] + r[6] + r[7]))


if __name__ == '__main__':
    main()
