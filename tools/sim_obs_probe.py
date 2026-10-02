# -*- coding: utf-8 -*-
"""模拟模式环境场取样: 在指定模拟时刻导出 SST / OHC / 风切 网格(供与观测对比)。

用法(Windows Python, 因为要跟主程序同环境):
  python tools/sim_obs_probe.py --month 9 --t 318 --data 1 --tag 20260914_dm1
  python tools/sim_obs_probe.py --month 9 --t 318 --data 0 --tag 20260914_dm0

输出: output/sim_<tag>.npz  (sst, ohc, shear, u200, v200, u850, v850, lat, lon)
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', type=int, default=9)
    ap.add_argument('--t', type=float, default=13 * 24 + 6.0,
                    help='模拟小时数; 13*24+6 = 该月 14 日 06z')
    ap.add_argument('--outdir', default=None,
                    help='产物目录; 省略 = output/sim/<起始年份>/')
    ap.add_argument('--start', default=None,
                    help='绝对起始时刻(YYYY-MM-DD HH); 给了就忽略 --month/--t')
    ap.add_argument('--data', type=int, default=1, help='1=数据环境, 0=解析公式')
    ap.add_argument('--year', type=int, default=None,
                    help='指定年份(用 env/library 与 env/raw 具体年份场); 省略=气候态')
    ap.add_argument('--daycycle', type=float, default=1.0)
    ap.add_argument('--tag', default='probe')
    ap.add_argument('--seed', type=int, default=20250801)
    args = ap.parse_args()

    from simulator.simcore import core as V

    init_kw = ({'start': args.start} if args.start
               else {'month': args.month, 't': args.t})
    sim = V.init({'seed': args.seed, **init_kw,
                  'params': {'dataMode': bool(args.data),
                             'dataClim': args.year is None,
                             'dataYear': args.year,
                             'dataDayCycle': args.daycycle}})
    sim['t'] = 0.0 if args.start else float(args.t)
    V.env_update(sim)
    env = sim['env']
    ocn = sim.get('ocn') or {}
    out = {
        'sst': np.asarray(env['sst'], np.float32),
        'ohc': np.asarray(ocn.get('ohc_kj', np.zeros_like(env['sst'])), np.float32),
        'shear': np.asarray(env['shear'], np.float32),
        'u850': np.asarray(env['u850'], np.float32),
        'v850': np.asarray(env['v850'], np.float32),
        'u200': np.asarray(env['u200'], np.float32),
        'v200': np.asarray(env['v200'], np.float32),
        'land': np.asarray(sim['land'], np.float32).reshape(V.ENVNY, V.ENVNX),
        # 格心经纬度
        'lat': (np.linspace(V.C['LAT0'] + 0.5, V.C['LAT1'] - 0.5, V.ENVNY)
                .astype(np.float32)),
        'lon': (np.linspace(V.C['LON0'] + 0.5, V.C['LON1'] - 0.5, V.ENVNX)
                .astype(np.float32)),
        'meta': np.array(['start=%s t=%.1f data=%d state=%s' %
                          (args.start or ('month %d' % args.month), args.t,
                           args.data, sim.get('dataState', '?'))]),
    }
    # 产物按**起始年份**归档: output/sim/<年>/(用户 2026-09-17 要求)
    if args.start:
        _y = int(str(args.start)[:4])
    else:
        _y = 1997 + (int(args.month) - 1) // 12      # 旧接口的 month 是"第几个月"
    outdir = os.path.join(ROOT, 'output', 'sim', str(_y))
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, 'sim_%s.npz' % args.tag)
    np.savez_compressed(p, **out)
    print('dataState=%s warn=%r' % (sim.get('dataState'), sim.get('dataWarn')))
    print('HEADLESS ' + str({
        'file': p,
        'sst': [round(float(out['sst'].min()), 2), round(float(out['sst'].max()), 2),
                round(float(out['sst'].mean()), 2)],
        'ohc': [round(float(out['ohc'].min()), 1), round(float(out['ohc'].max()), 1),
                round(float(out['ohc'].mean()), 1)],
        'shear': [round(float(out['shear'].min()), 1), round(float(out['shear'].max()), 1),
                  round(float(out['shear'].mean()), 1)],
    }))


if __name__ == '__main__':
    main()
