# -*- coding: utf-8 -*-
"""把模拟的**每一步状态**落盘, 供离线标定使用(强度-寿命 / ERC)。

为什么要它(见 INTENSITY_REFACTOR_PLAN.md 第一节):
  我之前的工作方式是"改一处 → 跑一年(11 分钟) → 看 5 个聚合数", 信息量极低, 而且
  风暴级聚合量的样本只有每年十几个系统 ⇒ 一直在噪声里。正确的做法是:
  **跑一次, 把每步完整状态落盘**, 之后所有拟合/对比都在**离线**做(秒级迭代)。

落盘内容: 每条记录 = 一个风暴的一个时次(6h):
  seed, tc, t, lon, lat, vmax, pmin, sst, shear, ohc, rh, vpi(有效MPI),
  rmw, weak(结构退化), erc(是否在置换中), org(核心组织度), age, phase, disturb(是否纯扰动)
产物: <out>/samples_<seed>.npz(压缩), 以及一个合并的 summary。

用法:
  python tools/dump_intensity_samples.py --seeds 20250801,11 --days 365
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KEYS = ('t', 'lon', 'lat', 'vmax', 'pmin', 'sst', 'shear', 'ohc', 'rh', 'vpi',
        'rmw', 'weak', 'erc', 'org')


def run_one(seed, days, start, out, quiet=True):
    from simulator.simcore import core as V
    sim = V.init({'start': start, 'seed': int(seed), 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    step = V.step_hours(sim)
    n = int(days * 24 / step)
    t0 = time.time()
    for i in range(n):
        V.sim_step(sim, step)
        if not quiet and (i + 1) % 240 == 0:
            print('  seed %s day %.0f  %.0fs' % (seed, (i + 1) * step / 24,
                                                 time.time() - t0), flush=True)
    cols = {k: [] for k in KEYS}
    meta = {'tc': [], 'age': [], 'phase': [], 'disturb': []}
    for j, tc in enumerate(sim['tcs']):
        h = tc.get('hist') or []
        if len(h) < 3:
            continue
        for q in h:
            for k in KEYS:
                cols[k].append(q.get(k, np.nan))
            meta['tc'].append(j)
            meta['age'].append(tc.get('age', 0.0))
            meta['phase'].append(q.get('phase') or '')
            meta['disturb'].append(1.0 if tc.get('disturb') else 0.0)
    arr = {k: np.asarray(v, np.float32) for k, v in cols.items()}
    arr['tc'] = np.asarray(meta['tc'], np.int32)
    arr['seed'] = np.full(arr['t'].shape, int(seed), np.int32)
    arr['phase'] = np.asarray(meta['phase'])
    arr['disturb'] = np.asarray(meta['disturb'], np.float32)
    os.makedirs(out, exist_ok=True)
    fp = os.path.join(out, 'samples_%s.npz' % seed)
    np.savez_compressed(fp, **arr)
    print('seed %s: %d 风暴 / %d 步  → %s (%.1f s, %.1f MB)'
          % (seed, len(set(arr['tc'].tolist())), arr['t'].size, fp,
             time.time() - t0, os.path.getsize(fp) / 1e6), flush=True)
    return fp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', default='20250801')
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--start', default='2025-01-01 00')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output', 'sim', '_reference', 'samples'))
    a = ap.parse_args(argv)
    for s in [x.strip() for x in a.seeds.split(',') if x.strip()]:
        run_one(s, a.days, a.start, a.out, quiet=False)
    return 0


if __name__ == '__main__':
    main()
