# -*- coding: utf-8 -*-
"""量化"中尺度涡幅度归一化"修正对强度分布的影响(同种子、同区间, 只换归一化口径)。

背景: `mesoscale_eddies()` 原来按 **std = amp** 归一化, 而场是若干个分开的高斯涡 →
峰值被放到 ~30 倍 amp(amp=5 的 D26 涡输出 ±159 米), 把 `env['ohc']`(D26, 米, 供 MPI)
打成负值; 2026-09-20 改成 **峰值 = amp**。

这里跑两遍回放(新口径 vs 旧口径), 比风暴数与强度分布 —— 用来判断"这个修正对强度统计
到底有多大影响", 也作为后续强度标定的基线。

用法: python tools/exp_eddy_impact.py --start "1997-09-16 12" --days 30 --seed 20250801
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

_ORIG = V.mesoscale_eddies
_OLD_MODE = {'on': False}


def _old_eddy(sim, key, n, amp, r_km, drift_kt=0.25):
    """旧口径: 按 std 归一化(复现修正前的行为)。"""
    r = _ORIG(sim, key, n, amp, r_km, drift_kt)
    if not _OLD_MODE['on']:
        return r
    pk = float(np.abs(r).max())
    if pk < 1e-9:
        return r
    # 新场峰值 = amp; 旧场峰值 ≈ 30×amp → 放大回去
    return (np.asarray(r, np.float64) * 30.0).astype(np.float32)


def stats(sim):
    peaks, ri, ace, nmin = [], 0, 0.0, 1e9
    for tc in sim.get('tcs', []):
        tr = tc.get('track') or []
        if not tr:
            continue
        v = np.asarray([float(q['vmax']) for q in tr], float)
        peaks.append(float(v.max()))
        nmin = min(nmin, float(v.min()))
        ace += float(tc.get('ace', 0.0))
        if v.size > 4:
            ri += int(((v[4:] - v[:-4]) >= 30.0).sum())
    ohc = np.asarray(sim['env']['ohc'], np.float64)
    return {'n': len(peaks), 'peaks': peaks, 'ri': ri, 'ace': ace,
            'ohc_min': float(ohc.min()), 'ohc_neg': int((ohc < 0).sum())}


def replay(start, days, seed, old):
    _OLD_MODE['on'] = bool(old)
    V.mesoscale_eddies = _old_eddy
    D._CACHE.clear()
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': '400ms', 'dataMode': True}})
    n = int(days * 24 / max(1.0, V.step_hours(sim)))
    for _ in range(n):
        V.sim_step(sim, V.step_hours(sim))
    st = stats(sim)
    _OLD_MODE['on'] = False
    V.mesoscale_eddies = _ORIG
    return st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='1997-09-16 12')
    ap.add_argument('--days', type=float, default=30.0)
    ap.add_argument('--seed', type=int, default=20250801)
    a = ap.parse_args(argv)
    out = {}
    for tag, old in (('修正后(峰值口径)', False), ('修正前(std 口径)', True)):
        st = replay(a.start, a.days, a.seed, old)
        out[tag] = st
        pk = st['peaks']
        print('%-16s 风暴 %2d | 峰值 中位 %5.1f 均值 %5.1f 最大 %5.1f | RI≥30kt %2d | '
              'ACE %6.1f | env[ohc] 最小 %7.1f 负值格 %d'
              % (tag, st['n'], float(np.median(pk)) if pk else 0.0,
                 float(np.mean(pk)) if pk else 0.0,
                 max(pk) if pk else 0.0, st['ri'], st['ace'],
                 st['ohc_min'], st['ohc_neg']), flush=True)
    A, B = out['修正后(峰值口径)'], out['修正前(std 口径)']
    print('\n差值(修正后 - 修正前): 风暴 %+d | 峰值中位 %+.1f kt | RI %+d | ACE %+.1f'
          % (A['n'] - B['n'],
             (float(np.median(A['peaks'])) if A['peaks'] else 0.0)
             - (float(np.median(B['peaks'])) if B['peaks'] else 0.0),
             A['ri'] - B['ri'], A['ace'] - B['ace']))


if __name__ == '__main__':
    main()
