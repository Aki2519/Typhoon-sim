# -*- coding: utf-8 -*-
"""TC 无头模拟 runner: 从某时刻起跑 N 天, 导出所有台风的路径与强度。

为什么必须要它: 没有它就没法把"模拟的风暴"和观测最佳路径放在一起比 ——
而这正是强度/路径/生成所有验收的前提。项目里原来没有这个工具。

要点:
  * 台风死亡后会从 sim['tcs'] 里被移除, 所以**边跑边收集**, 不能跑完再取
  * env_update 每 6h 一次(step1 的 F1 设计), 逐 10 分钟步进的开销主要在
    intensity/motion, 可以接受
  * 产物归档 output/sim/<起始年份>/

用法:
  python tools/run_tc_sim.py --start "1997-01-01 00" --days 365
  python tools/run_tc_sim.py --start "1997-01-01 00" --days 30 --dt 0.1667
输出: output/sim/<年>/tcsim_<start>_<days>d.npz  (每个台风的 t/lon/lat/vmax/pmin)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from simulator.simcore import core as V                      # noqa: E402


def run(start, days, dt=10.0 / 60.0, data_mode=True, seed=20260917,
        genesis=True, quiet=False):
    sim = V.init({'start': start, 'seed': seed,
                  'params': {'dataMode': bool(data_mode),
                             'genesis': bool(genesis)}})
    V.env_update(sim)
    # **循环次数必须按"实际步长"算**。步长统一后 sim_step 每次推进 envStepH
    # (默认 6h), 而这里原来固定按 10 分钟算 → 循环 52560 次 × 每次 6h =
    # **实际跑了 360 年**(实测 99% CPU 跑了 32 分钟还没完)。改成用步长算。
    nstep = int(round(days * 24.0 / V.step_hours(sim)))
    tracks = {}                    # id(tc) → {'t':[], 'lon':[], ...}
    # 几何登陆事件(seg_land_cross 产出)。**必须在 _reg 里捕获**: sim['tcs']
    # 到结束时只剩存活的台风, 早已消亡的那些登陆记录会丢。
    landfalls = []
    _seen_lf = set()
    # 稳定 id: 给每个台风打标记
    counter = [0]

    def _reg():
        for tc in sim['tcs']:
            if 'rid' not in tc:
                tc['rid'] = counter[0]
                counter[0] += 1
                tracks[tc['rid']] = {'t': [], 'lon': [], 'lat': [],
                                     'vmax': [], 'pmin': [],
                                     # 诊断用: 环境与潜在强度, 用来算 V/MPI
                                     'vpi': [], 'sst': [], 'ohc': [],
                                     'shear': []}
            lf = tc.get('landfall')
            if lf is not None:
                key = (tc['rid'], round(lf['t'], 3))
                if key not in _seen_lf:
                    _seen_lf.add(key)
                    landfalls.append(lf)

    _reg()
    t0 = time.time()
    for i in range(nstep):
        V.sim_step(sim, V.step_hours(sim))
        _reg()
        for tc in sim['tcs']:
            tr = tracks.get(tc.get('rid'))
            if tr is None:
                continue
            tr['t'].append(sim['t'])
            tr['lon'].append(tc['lon'])
            tr['lat'].append(tc['lat'])
            tr['vmax'].append(tc['vmax'])
            tr['pmin'].append(tc['pmin'])
            e = tc.get('env') or {}
            tr['vpi'].append(e.get('vpi', np.nan))
            tr['sst'].append(e.get('sst', np.nan))
            tr['ohc'].append(e.get('ohc', np.nan))
            tr['shear'].append(e.get('shear', np.nan))
        if not quiet and (i + 1) % max(1, nstep // 10) == 0:
            print('  %5.1f%%  t=%.0fh  存活 %d  累计 %d  (%.0fs)'
                  % ((i + 1) * 100.0 / nstep, sim['t'],
                     len(sim['tcs']), len(tracks), time.time() - t0), flush=True)
    out = {}
    # 几何登陆记录(seg_land_cross 的结果)。T6 用它, 而不是在 6h 采样点上
    # 找"land 跨越 0.3" —— 后者可能跨过一条窄岛/海峡。
    out['n_landfall'] = np.int32(len(landfalls))
    if landfalls:
        out['lf_t'] = np.asarray([r['t'] for r in landfalls], np.float32)
        out['lf_lon'] = np.asarray([r['lon'] for r in landfalls], np.float32)
        out['lf_lat'] = np.asarray([r['lat'] for r in landfalls], np.float32)
        out['lf_vmax'] = np.asarray([r['vmax'] for r in landfalls], np.float32)
    for rid, tr in tracks.items():
        if len(tr['t']) < 4:
            continue
        for k, v in tr.items():
            out['%s_%d' % (k, rid)] = np.asarray(v, np.float32)
    out['n_tc'] = np.int32(len([1 for tr in tracks.values() if len(tr['t']) >= 4]))
    out['start'] = np.array([start])
    out['days'] = np.float32(days)
    return out, sim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='1997-01-01 00')
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--dt', type=float, default=10.0 / 60.0)
    ap.add_argument('--data', type=int, default=1)
    ap.add_argument('--tag', default='')
    ap.add_argument('--outdir', default=None)
    a = ap.parse_args()
    y = int(str(a.start)[:4])
    od = a.outdir or os.path.join(ROOT, 'output', 'sim', str(y))
    os.makedirs(od, exist_ok=True)
    tag = a.tag or ('%s_%dd' % (a.start.replace(' ', '_').replace(':', ''),
                                int(a.days)))
    out, sim = run(a.start, a.days, a.dt, bool(a.data))
    p = os.path.join(od, 'tcsim_%s.npz' % tag)
    np.savez_compressed(p, **out)
    print('-> %s   台风数 %d' % (p, int(out['n_tc'])))


if __name__ == '__main__':
    main()
