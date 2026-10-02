# -*- coding: utf-8 -*-
"""**配对**实验: 同一批台风, 环境场走"瞬时"vs"月均兜底", 看强度演化差多少。

为什么要配对: 用自然生成时, 两边生成的风暴根本不是同一批(实测瞬时 1 个 vs 月均 2 个),
比出来的差只是"抽样差"。所以这里:
  * 关掉自然生成(`params.genesis = False`);
  * 两边用**同一个种子**、在**同一批固定位置**放同样数量的台风;
  * 逐风暴比较峰值, 给出配对差与平均值。
环境覆盖多少会如实打印(瞬时数据只有个别日期, ±6 小时窗口)。

用法:
  python tools/exp_instant_vs_monthly.py --start "1997-09-13 12" --days 4 --n 8
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
from simulator.simcore import daily as D     # noqa: E402

_ORIG = D.load_fields
#: 固定出生点(西太多发区, 纬度/经度)
SPAWNS = ((135.0, 12.0), (140.0, 15.0), (128.0, 18.0), (150.0, 20.0),
          (120.0, 14.0), (145.0, 10.0), (132.0, 22.0), (158.0, 16.0))


def run(start, days, seed, instant, n, precision='100ms'):
    D.load_fields = _ORIG if instant else (lambda *a, **k: {})
    D._CACHE.clear()
    try:
        from simulator.simcore.envmodel import solver as SL
        SL._DAY_MEMO['key'] = None
        SL._DAY_MEMO['fields'] = None
    except Exception:
        SL = None
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': precision, 'dataMode': True,
                             'genesis': False}})
    tcs = [V.make_tc(sim, SPAWNS[i][0], SPAWNS[i][1], 35.0) for i in range(min(n, len(SPAWNS)))]
    step = V.step_hours(sim)
    for _ in range(int(days * 24 / step)):
        V.sim_step(sim, step)
    peaks = [float(t['vmax']) for t in tcs if not t.get('dead')] + \
            [float(max((q['vmax'] for q in (t.get('track') or [])), default=0.0)) for t in tcs if t.get('dead')]
    sh = float(np.mean(sim['env']['shear']))
    D.load_fields = _ORIG
    return {'peaks': peaks, 'shear': sh, 'n_alive': sum(1 for t in tcs if not t.get('dead'))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='1997-09-13 12')
    ap.add_argument('--days', type=float, default=4.0)
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--n', type=int, default=8)
    ap.add_argument('--precision', default='100ms')
    a = ap.parse_args(argv)
    cov = D.coverage()
    print('瞬时覆盖: ERA5 %s .. %s(%d 个文件, 窗口 ±6 小时)'
          % (cov['era5'][0], cov['era5'][1], cov['era5'][2]) if cov.get('era5')
          else '瞬时覆盖: 无')
    out = {}
    for tag, inst in (('瞬时', True), ('月均', False)):
        st = run(a.start, a.days, a.seed, inst, a.n, a.precision)
        out[tag] = st
        pk = st['peaks']
        print('%-4s 风切均值 %5.1f kt | 峰值 中位 %6.1f 均值 %6.1f 最大 %6.1f | 存活 %d/%d'
              % (tag, st['shear'], np.median(pk), np.mean(pk), max(pk), st['n_alive'], len(pk)))
    A, B = out['瞬时']['peaks'], out['月均']['peaks']
    m = min(len(A), len(B))
    if m:
        d = np.asarray(A[:m]) - np.asarray(B[:m])
        print('\n**配对差(瞬时-月均)**: 平均 %+.1f kt, 中位 %+.1f, 最大 %+.1f, 最小 %+.1f'
              % (d.mean(), np.median(d), d.max(), d.min()))
        print('逐风暴: %s' % ' '.join('%+.0f' % x for x in d))


if __name__ == '__main__':
    main()
