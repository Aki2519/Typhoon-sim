# -*- coding: utf-8 -*-
"""把**观测台风路径**喂给模型自己的引导场, 看模型算出的引导气流与观测实际移速差多少。

A 任务的关键判据工具。为什么要它:
    "模型风暴不北上" 有两种完全不同的原因 ——
      (i) 引导场本身错了(副高位置/强度/经向风缺失);
      (ii) 引导场是对的, 但风暴活得不够久 / 生成太南 / 被陆地吃掉。
    只跑模式分不清这两者(两者都表现为最高纬度低)。本工具**用观测路径当地面真值**,
    在观测位置上取模型的 steering_at, 与观测的实际移速逐 6 小时对比:
      若引导场正确 -> (模型引导 - 观测移速) 在各纬度带都接近 0, 且**向极分量为正**;
      若引导场错误 -> 偏差随纬度系统变化(例如 20-30N 向极分量被压成 0 或负)。

口径: 观测移速用相邻两报的**中心差分**(kt); 模型只用 steering+β(不含噪声);
      xrq/wp 是命名台风(轨迹长、可信), 与模型的"全部扰动"不同口径 —— 这里只比**运动学**,
      不做强度/频数比较, 所以口径差异不影响结论。

用法:
    python tools/probe_steer_obs.py --max-tracks 40 --month-min 7 --month-max 10
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

OBS_DIR = r'G:\气象\数据\xrq\wp'
BANDS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 60)]


def parse_track(path):
    out = []
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for ln in f:
                c = [x.strip() for x in ln.split(',')]
                if len(c) < 9:
                    continue
                try:
                    t = datetime.strptime(c[2][:10], '%Y%m%d%H')
                except Exception:
                    continue
                try:
                    la = float(c[6][:-1]) / 10.0
                    if c[6][-1] in 'Ss':
                        la = -la
                    lo = float(c[7][:-1]) / 10.0
                    if c[7][-1] in 'Ww':
                        lo = -lo
                    v = float(c[8])
                except Exception:
                    continue
                out.append((t, la, lo % 360.0, v))
    except OSError:
        return []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-tracks', type=int, default=40)
    ap.add_argument('--month-min', type=int, default=7)
    ap.add_argument('--month-max', type=int, default=10)
    ap.add_argument('--year-min', type=int, default=2015)
    ap.add_argument('--year-max', type=int, default=2024)
    a = ap.parse_args()

    from simulator.simcore import core as V

    paths = sorted(glob.glob(os.path.join(OBS_DIR, '**', '*.dat'), recursive=True))
    tracks = []
    for p in paths:
        tr = parse_track(p)
        if len(tr) < 8:
            continue
        if not (a.year_min <= tr[0][0].year <= a.year_max):
            continue
        if not (a.month_min <= tr[0][0].month <= a.month_max):
            continue
        # 只取北半球西太
        if min(x[1] for x in tr) < 0:
            continue
        tracks.append(tr)
        if len(tracks) >= a.max_tracks:
            break
    print('用 %d 条观测路径(共 %d 个 6h 步)'
          % (len(tracks), sum(len(t) - 1 for t in tracks)))

    sim = V.init({'start': '2020-01-01 00', 'seed': 7, 'envStepH': 6.0,
                  'params': {'dataMode': True, 'precision': '1s'}})
    t0 = datetime.strptime('2020-01-01 00', '%Y-%m-%d %H')
    acc = {b: [] for b in BANDS}          # (du, dv)
    band_obs = {b: [] for b in BANDS}
    cur_month = None
    n = 0
    for tr in tracks:
        for i in range(len(tr) - 1):
            t_a, la_a, lo_a, v_a = tr[i]
            t_b, la_b, lo_b, v_b = tr[i + 1]
            dt_h = (t_b - t_a).total_seconds() / 3600.0
            if not (5.5 <= dt_h <= 6.5):
                continue
            dlo = lo_b - lo_a
            if dlo > 180.0:
                dlo -= 360.0
            if dlo < -180.0:
                dlo += 360.0
            u_obs = dlo * 60.0 * np.cos(np.deg2rad(la_a)) / dt_h
            v_obs = (la_b - la_a) * 60.0 / dt_h
            if abs(u_obs) > 90 or abs(v_obs) > 90:
                continue
            if t_a.month != cur_month:
                sim['t'] = (t_a - t0).total_seconds() / 3600.0
                V.env_update(sim)
                cur_month = t_a.month
            su, sv = V.steering_at(sim, lo_a, la_a, float(v_a))
            bu, bv = V.beta_drift(la_a)
            mu, mv = su + bu, sv + bv
            mid = 0.5 * (la_a + la_b)
            for b in BANDS:
                if b[0] <= mid < b[1]:
                    acc[b].append((mu - u_obs, mv - v_obs))
                    band_obs[b].append((u_obs, v_obs))
                    break
            n += 1
    print('有效对比步数 %d' % n)
    print()
    print('%-9s %6s | %-22s | %-22s | %s'
          % ('纬度带', 'n', '观测移速 u/v (kt)', '模型引导+β u/v (kt)', '偏差 Δu/Δv'))
    for b in BANDS:
        d = np.array(acc[b], float)
        o = np.array(band_obs[b], float)
        if len(d) < 20:
            print('%-9s %6d  样本不足' % ('%d-%dN' % b, len(d)))
            continue
        print('%-9s %6d | %+6.2f / %+6.2f (向极%+5.2f) | %+6.2f / %+6.2f (向极%+5.2f) | %+6.2f / %+6.2f'
              % ('%d-%dN' % b, len(d),
                 o[:, 0].mean(), o[:, 1].mean(), o[:, 1].mean(),
                 o[:, 0].mean() + d[:, 0].mean(), o[:, 1].mean() + d[:, 1].mean(),
                 o[:, 1].mean() + d[:, 1].mean(),
                 d[:, 0].mean(), d[:, 1].mean()))
    print()
    print('判读: 若"偏差 Δv"在 20-30N / 30-40N 明显为负 -> 引导场的**向极分量不足或反向**,')
    print('      修副高/RIDGES; 若 Δv 接近 0(整体只是偏西/偏东) -> 引导场没错, 该修寿命/生成。')


if __name__ == '__main__':
    main()
