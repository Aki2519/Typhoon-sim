# -*- coding: utf-8 -*-
"""快速核对: 阶段结构与纬度的"模型 vs 观测"(不落盘, 直接读 sim['tcs'])。

观测基准(见 output/sim/_reference/PHASE_DURATION_STATS.md, 自评 139 系统):
  扰动 DB/WV/LO 中位 42.0 h · 低压 TD 中位 15.0 h · 前生期合计 60.0 h
  首报阶段码: DB 82% / LO 9% / WV 4% / MD 5% / TD 1% / EX 1%
  TS→DB 直跳(中间无 TD): 0 例

用法:
  python tools/check_phase_vs_obs.py --days 120 --seed 20250801
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import time

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TROP = ('TD', 'TS', 'TY', 'ST')
DIST = ('DB', 'WV', 'LO')


def run(days=120, seed=20250801, start='2025-08-01 00', quiet=True):
    from simulator.simcore import core as V
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  # forecast=False: 关掉内置集合预报(只读诊断, 不影响阶段码;
                  # 但它每次签发要跑若干次 env_update, 本机内存紧张时会直接 OOM)
                  'params': {'precision': '100ms', 'dataMode': True,
                             'forecast': False}})
    step = V.step_hours(sim)
    n = int(days * 24 / step)
    t0 = time.time()
    for i in range(n):
        V.sim_step(sim, step)
        if not quiet and (i + 1) % (24 * 10 // int(step)) == 0:
            print('  day %.0f, tcs %d, %.0fs' % ((i + 1) * step / 24, len(sim['tcs']),
                                                 time.time() - t0), flush=True)
    return sim, time.time() - t0


def stats(sim):
    dist_h, td_h, pre_h, life_h, maxlat = [], [], [], [], []
    firsts = collections.Counter()
    codes = collections.Counter()
    jumps = 0
    ex_sys = lo_sys = 0
    for tc in sim['tcs']:
        tr = tc.get('track') or []
        if len(tr) < 4:
            continue
        seq = [str(q.get('phase') or 'DB') for q in tr]
        for c in seq:
            codes[c] += 1
        if seq:
            firsts[seq[0]] += 1
        # 前生期 = 首次 TS+ 之前的记录
        k = next((i for i, c in enumerate(seq) if c in TROP), None)
        pre = seq if k is None else seq[:k]
        dist_h.append(6.0 * sum(1 for c in pre if c in DIST))
        td_h.append(6.0 * sum(1 for c in pre if c == 'TD'))
        pre_h.append(6.0 * len(pre))
        life_h.append(6.0 * len(seq))
        maxlat.append(max(abs(float(q['lat'])) for q in tr))
        # TS→DB 直跳(中间没有 TD)
        for a, b in zip(seq, seq[1:]):
            if a in TROP and b == 'DB':
                jumps += 1
        if 'EX' in seq:
            ex_sys += 1
        if 'LO' in seq:
            lo_sys += 1
    med = lambda x: float(np.median(x)) if x else 0.0
    return {'n': len(life_h), 'dist_h': med(dist_h), 'td_h': med(td_h), 'pre_h': med(pre_h),
            'life_h': med(life_h), 'maxlat_med': med(maxlat),
            'maxlat_p90': float(np.percentile(maxlat, 90)) if maxlat else 0.0,
            'maxlat_max': float(np.max(maxlat)) if maxlat else 0.0,
            'firsts': dict(firsts), 'codes': dict(codes), 'jumps': jumps,
            'ex_sys': ex_sys, 'lo_sys': lo_sys}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=120.0)
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--start', default='2025-08-01 00')
    a = ap.parse_args(argv)
    sim, el = run(a.days, a.seed, a.start, quiet=False)
    s = stats(sim)
    print('跑 %.0f 天用时 %.0fs' % (a.days, el))
    print('系统 %d 个 | 阶段码记录分布 %s' % (s['n'], s['codes']))
    print('首报码 %s' % s['firsts'])
    print('中位: 扰动 %.0f h(观测 42) · TD %.0f h(观测 15) · 前生期 %.0f h(观测 60) · 寿命 %.0f h'
          % (s['dist_h'], s['td_h'], s['pre_h'], s['life_h']))
    print('最大|纬度| 中位 %.1f / p90 %.1f / 最大 %.1f  (扩域前模型最大 36.0)'
          % (s['maxlat_med'], s['maxlat_p90'], s['maxlat_max']))
    print('TS→DB 直跳 %d 次(观测 0) | 出现 EX 的系统 %d | 出现 LO 的系统 %d'
          % (s['jumps'], s['ex_sys'], s['lo_sys']))
    return s


if __name__ == '__main__':
    main()
