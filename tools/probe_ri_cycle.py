# -*- coding: utf-8 -*-
"""带仪表的强度回放: 统计每个风暴的 EMBC 形成/释放次数与 RI 过程数, 并给出强度分布。

用途: 判断"RI 过程数偏少"到底卡在**形成**(前置条件/触发率)还是**释放**。
用法: python tools/probe_ri_cycle.py --start "2025-05-01 00" --days 245 --seed 20250801
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from simulator.simcore import core as V      # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2025-05-01 00')
    ap.add_argument('--days', type=float, default=245.0)
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--precision', default='100ms')
    a = ap.parse_args(argv)
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': a.precision, 'dataMode': True}})
    step = V.step_hours(sim)
    n = int(a.days * 24 / step)
    prev = {}          # id -> was active
    form = {}          # id -> 形成次数
    rel = {}           # id -> 释放次数
    for i in range(n):
        V.sim_step(sim, step)
        for t in sim.get('tcs', []):
            k = id(t)
            now = bool(t.get('embc'))
            if prev.get(k) is False and now:
                form[k] = form.get(k, 0) + 1
            if prev.get(k) is True and not now:
                rel[k] = rel.get(k, 0) + 1
            prev[k] = now
    peaks, ris, has = [], [], 0
    for t in sim.get('tcs', []):
        tr = t.get('track') or []
        if len(tr) < 4:
            continue
        v = np.asarray([float(q['vmax']) for q in tr], float)
        peaks.append(float(v.max()))
        hit = (v[4:] - v[:-4]) >= 30.0
        ri = int(np.sum(hit & np.r_[True, ~hit[:-1]])) if hit.size else 0
        ris.append(ri)
        has += int(ri > 0)
    print('回放 %s 起 %.0f 天(seed %d, %s): 风暴 %d'
          % (a.start, a.days, a.seed, a.precision, len(peaks)))
    print('EMBC 形成次数: 合计 %d, 平均每风暴 %.2f' % (sum(form.values()), np.mean(list(form.values())) if form else 0.0))
    print('EMBC 释放次数: 合计 %d, 平均每风暴 %.2f' % (sum(rel.values()), np.mean(list(rel.values())) if rel else 0.0))
    print('RI 过程: 合计 %d, 平均每风暴 %.2f, 有 RI 的风暴 %d/%d'
          % (sum(ris), np.mean(ris) if ris else 0.0, has, len(peaks)))
    if peaks:
        print('峰值 P50 %.1f / P90 %.1f kt' % (np.percentile(peaks, 50), np.percentile(peaks, 90)))


if __name__ == '__main__':
    main()
