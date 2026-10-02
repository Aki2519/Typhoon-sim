# -*- coding: utf-8 -*-
"""模型 vs 观测的"增强速率曲线": 从风暴出生到首次达到 34/50/64/80/96/112 kt 各需多少小时。

为什么用这条曲线而不是只看 P50: P50 只反映峰值, 而"多久长到多强"才是强度方程的直接产物
(增长时间尺度 tau、fs/fd、RI 机制都会体现在这条曲线上)。观测侧已用
typhoon/2025wp 量过: 34kt 48h / 50kt 66h / 64kt 84h / 80kt 90h / 96kt 120h / 112kt 126h;
24h Δvmax 中位 +5.0 kt、P90 +22.5 kt。

用法: python tools/probe_rise_curve.py --start "2025-05-01 00" --days 245
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

OBS = {34: 48.0, 50: 66.0, 64: 84.0, 80: 90.0, 96: 120.0, 112: 126.0}


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
    for _ in range(int(a.days * 24 / step)):
        V.sim_step(sim, step)
    first = {T: [] for T in OBS}
    dv24 = []
    for t in sim.get('tcs', []):
        tr = t.get('track') or []
        if len(tr) < 4:
            continue
        v = np.asarray([float(q['vmax']) for q in tr], float)
        dv24.append(float(np.median(v[4:] - v[:-4])))
        for T in OBS:
            idx = np.nonzero(v >= T)[0]
            if idx.size:
                first[T].append(float(idx[0] * step))
    print('模型(seed %d, %s): 风暴 %d' % (a.seed, a.precision, len(dv24)))
    print('  24h Δvmax 中位 %.1f kt (观测 5.0)' % (np.median(dv24) if dv24 else 0.0))
    print('  %-8s %10s %10s %8s' % ('阈值', '模型中位(h)', '观测(h)', '倍数'))
    for T in sorted(OBS):
        if len(first[T]) >= 3:
            m = float(np.median(first[T]))
            print('  %-8s %10.0f %10.0f %8.2f' % ('%d kt' % T, m, OBS[T], m / OBS[T]))


if __name__ == '__main__':
    main()
