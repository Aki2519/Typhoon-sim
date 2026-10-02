# -*- coding: utf-8 -*-
"""**离线拟合副高向极修正的形状**(轮次 121, 纯离线, 秒级)。

为什么先离线: 100 天模拟扫一组要 ~7 分钟, 而候选形状有十几组。真正要满足的关系是
"**在观测风暴实际所在的位置/月份上**, 这一项要补多少经向引导"。把它写成离线最小二乘,
就能先把候选压到 3-4 个, 再花机时去验证**模式自己总体**的行为(两者不同: 见轮次 120 §13)。

目标向量来自轮次 120 的实测分解 —— 模型 mv = 总引导 + beta + fuji + 残差, 令 mv 等于
观测的总向极移速, 反解出的、**必须由 `sh_pole_steer` 提供**的分带贡献:

    10-20N  -0.1   (即基本不推)
    20-30N  +0.6
    30-40N  +4.2
    40-60N  +1.9   (n 少, 噪声大)

⇒ 形状是**带通**, 不是单调升。本工具用观测路径的位置/月份作权重, 对候选参数
(斜坡 `shPoleS/W/Max` 或高斯 `shPoleForm=gauss` + `Mu/Sig/Max`)算分带贡献, 报 RMS 排名。

用法:
    python tools/fit_pole_offline.py                 # 扫内置候选表
    python tools/fit_pole_offline.py --top 8
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

BANDS = [(10, 20), (20, 30), (30, 40), (40, 60)]
TARGET = {10: -0.1, 20: 0.6, 30: 4.2, 40: 1.9}
DEFAULT_OBS = r'G:\气象\数据\xrq\wp'

CAND = [
    ('base(斜坡 22.4/20/8)', {}),
    ('zero', {'shPoleMax': 0.0}),
    ('斜坡 s8w6m5', {'shPoleS': -8.0, 'shPoleW': 6.0, 'shPoleMax': 5.0}),
    ('斜坡 p8w8m6', {'shPoleS': 8.0, 'shPoleW': 8.0, 'shPoleMax': 6.0}),
    ('斜坡 p2w8m6', {'shPoleS': 2.0, 'shPoleW': 8.0, 'shPoleMax': 6.0}),
    ('斜坡 p4w6m6', {'shPoleS': 4.0, 'shPoleW': 6.0, 'shPoleMax': 6.0}),
    ('高斯 mu-2 sig4.5 m4.5', {'shPoleForm': 'gauss', 'shPoleMu': -2.0,
                               'shPoleSig': 4.5, 'shPoleMax': 4.5}),
    ('高斯 mu-4 sig6 m5', {'shPoleForm': 'gauss', 'shPoleMu': -4.0,
                           'shPoleSig': 6.0, 'shPoleMax': 5.0}),
    ('高斯 mu-2 sig6 m5', {'shPoleForm': 'gauss', 'shPoleMu': -2.0,
                           'shPoleSig': 6.0, 'shPoleMax': 5.0}),
    ('高斯 mu0 sig5 m4.5', {'shPoleForm': 'gauss', 'shPoleMu': 0.0,
                            'shPoleSig': 5.0, 'shPoleMax': 4.5}),
    ('高斯 mu-6 sig5 m5', {'shPoleForm': 'gauss', 'shPoleMu': -6.0,
                           'shPoleSig': 5.0, 'shPoleMax': 5.0}),
    ('高斯 mu-6 sig8 m5', {'shPoleForm': 'gauss', 'shPoleMu': -6.0,
                           'shPoleSig': 8.0, 'shPoleMax': 5.0}),
]


def band_of(lat):
    a = abs(lat)
    for lo, hi in BANDS:
        if lo <= a < hi:
            return lo
    return None


def obs_steps(obs_dir, min_peak=34.0):
    """观测步: [(month, lon, lat)] —— 只取北半球、peak>=34kt。"""
    from simulator.simcore import import_tc as IMP
    out = []
    for p in sorted(glob.glob(os.path.join(obs_dir, '**', '*.dat'), recursive=True)):
        try:
            rows = IMP.parse_bdeck(p)
        except Exception:
            continue
        if len(rows) < 4:
            continue
        if max(float(r['vmax'] or 0.0) for r in rows) < min_peak:
            continue
        if float(rows[0]['lat']) < 0:
            continue
        for r in rows:
            if band_of(r['lat']) is None:
                continue
            out.append((r['t'].month, r['lon'] % 360.0, float(r['lat'])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--obs-dir', default=DEFAULT_OBS)
    ap.add_argument('--top', type=int, default=12)
    a = ap.parse_args()

    from simulator.simcore import core as V
    st = obs_steps(a.obs_dir)
    print('观测步 n=%d(北半球, peak>=34kt)' % len(st))
    by_m = collections.Counter(x[0] for x in st)
    print('  逐月分布: ' + ' '.join('%d月 %d' % (m, by_m[m])
                                    for m in sorted(by_m)))
    sim = V.init({'start': '2025-06-15 00', 'seed': 7, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    base_params = dict(sim['params'])
    t0 = dt.datetime(2025, 6, 15)
    res = []
    for name, over in CAND:
        sim['params'] = dict(base_params)
        sim['params'].update(over)
        acc = collections.defaultdict(list)
        for m in sorted(by_m):
            sim['t'] = (dt.datetime(2025, m, 15) - t0).total_seconds() / 3600.0
            sim['month'] = m
            V.env_update(sim)
            for (mm, lo, la) in st:
                if mm != m:
                    continue
                b = band_of(la)
                acc[b].append(V.sh_pole_steer(sim, lo, la)[1])
        got = {b: float(np.mean(acc[b])) for b in acc}
        err = [got.get(b, 0.0) - TARGET[b] for b in TARGET]
        res.append((float(np.sqrt(np.mean(np.square(err)))), name, got))
    res.sort()
    print()
    print('%-26s | %7s %7s %7s %7s | %6s' %
          ('候选', '10-20', '20-30', '30-40', '40-60', 'RMS'))
    print('%-26s | %7.2f %7.2f %7.2f %7.2f | %6s' %
          ('**目标(必须补的量)**', TARGET[10], TARGET[20], TARGET[30],
           TARGET[40], '-'))
    for r, name, got in res[:a.top]:
        print('%-26s | %7.2f %7.2f %7.2f %7.2f | %6.2f' %
              (name, got.get(10, 0.0), got.get(20, 0.0), got.get(30, 0.0),
               got.get(40, 0.0), r))
    print()
    print('口径: 权重 = 观测风暴步的位置/月份分布(与模型总体不同, 因此这只是**预筛**;')
    print('      定案必须用 tools/sweep_pole.py 的模拟结果, 且两个半球季窗口趋势一致。')


if __name__ == '__main__':
    main()
