# -*- coding: utf-8 -*-
"""对照实验: **瞬时环境场 vs 月均兜底** 对强度统计的影响(为 fs 重标定提供依据)。

背景: 用户 2026-09-20 要求"风切及一切数据都要用瞬时"。改成瞬时候热带切变均值从
13.4 kt 升到 27-29 kt, 而强度方程的抑制因子 fs = exp(-((S-6)/55)^2) 的系数是在
月均口径下标定的 —— 必须量化"换瞬时之后强度统计变了多少", 才能决定怎么重标。

做法: 同一个种子、同一区间跑两遍回放, 只切换环境场来源:
  A 瞬时: 有单日 ERA5/CMEMS 就用(现状)
  B 月均: 把 daily.load_fields 打桩成空 → 走月气候态 + 模态兜底(旧行为)
统计: 生成风暴数 / 峰值中位与分布 / RI(24h 增 30kt)次数 / ACE 合计 / 最大 vmax。

用法: python tools/exp_shear_calib.py --start "2024-09-15 12" --days 30
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

from simulator.simcore import core as V                     # noqa: E402
from simulator.simcore import daily as D                    # noqa: E402
from simulator.simcore.envmodel import solver as SL         # noqa: E402


def _stats(sim):
    peaks, ri, ace, nmax = [], 0, 0.0, 0.0
    for tc in sim.get('tcs', []):
        tr = tc.get('track') or []
        if not tr:
            continue
        v = np.asarray([float(q['vmax']) for q in tr], float)
        peaks.append(float(v.max()))
        nmax = max(nmax, float(v.max()))
        ace += float(tc.get('ace', 0.0))
        # RI: 任意 24h 窗口内 vmax 增幅 ≥ 30 kt(track 是 6h 一条 → 4 条 = 24h)
        if v.size > 4:
            dv = v[4:] - v[:-4]
            ri += int((dv >= 30.0).sum())
    return {'n': len(peaks), 'peaks': peaks, 'ri': ri, 'ace': ace, 'vmax': nmax}


def replay(start, days, seed=20250801, instant=True):
    if not instant:                            # 月均兜底: 把瞬时通道打桩成空
        D.load_fields = lambda *a, **k: {}
    else:
        D.load_fields = _ORIG_LOAD
    SL._DAY_MEMO['key'] = None
    SL._DAY_MEMO['fields'] = None
    D._CACHE.clear()
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': '400ms', 'dataMode': True}})
    n = int(days * 24 / max(1.0, V.step_hours(sim)))
    for _ in range(n):
        V.sim_step(sim, V.step_hours(sim))
    st = _stats(sim)
    st['shear_mean'] = float(np.asarray(sim['env']['shear']).mean())
    try:
        st['src'] = (sim.get('env') or {}).get('_dataSrc') or sim.get('dataState')
    except Exception:
        st['src'] = sim.get('dataState')
    return st


_ORIG_LOAD = D.load_fields


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2024-09-15 12')
    ap.add_argument('--days', type=float, default=30.0)
    ap.add_argument('--seed', type=int, default=20250801)
    a = ap.parse_args(argv)
    out = {}
    for tag, inst in (('瞬时', True), ('月均兜底', False)):
        st = replay(a.start, a.days, a.seed, instant=inst)
        out[tag] = st
        pk = st['peaks']
        print('%-8s 切变均值 %5.1f kt | 风暴 %2d | 峰值 中位 %5.1f 均值 %5.1f 最大 %5.1f | '
              'RI≥30kt %2d | ACE 合计 %6.1f'
              % (tag, st['shear_mean'], st['n'],
                 float(np.median(pk)) if pk else 0.0,
                 float(np.mean(pk)) if pk else 0.0, st['vmax'], st['ri'], st['ace']),
              flush=True)
    A, B = out['瞬时'], out['月均兜底']
    if A['n'] or B['n']:
        print('\n差值(瞬时-月均): 峰值中位 %+.1f kt  最大 %+.1f kt  RI %+d  ACE %+.1f'
              % ((float(np.median(A['peaks'])) if A['peaks'] else 0.0)
                 - (float(np.median(B['peaks'])) if B['peaks'] else 0.0),
                 A['vmax'] - B['vmax'], A['ri'] - B['ri'], A['ace'] - B['ace']))
    D.load_fields = _ORIG_LOAD


if __name__ == '__main__':
    main()
