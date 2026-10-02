# -*- coding: utf-8 -*-
"""快速判据: **真正的 500hPa 场**能不能比"0.45*u850+0.55*u200 的合成中层"更好地解释观测移速。

背景(轮次 102):
  · `base_wind.npz` 里训练好的 u500/v500 从来没有进过内核 ——
    `climate.build_env_fields` 的拷贝白名单里没有它们, 而 `solver` 里
    uMid = 0.45*u850 + 0.55*u200 是**合成**的。
  · 500hPa 是台风路径的经典引导层, 用合成值替代真值属于数据保真缺口。
本脚本用**月平均基态**(够用于均值判据)在观测路径位置上取 850/500/200 三层,
与"观测移速 − β"做同样的单纯形拟合, 看加入真 500hPa 之后 RMSE 降不降。

用法: python tools/probe_mid500.py --max-tracks 40
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from probe_steer_obs import parse_track            # noqa: E402

OBS_DIR = r'G:\气象\数据\xrq\wp'
NPZ = os.path.join('simulator', 'env', 'model', 'base_wind.npz')
BANDS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 60)]
#: base_wind 的 u/v **已经是 kt**(u200 极值 148 = 急流核 148kt), 不能再乘 KT
KT = 1.0


def bilin(fld, lat, lon):
    nlat, nlon = fld.shape
    j = (lat + 90.0) / (180.0 / nlat) - 0.5
    i = (lon % 360.0) / (360.0 / nlon) - 0.5
    j0 = int(np.floor(j))
    i0 = int(np.floor(i))
    fj = j - j0
    fi = i - i0
    j0 = min(max(j0, 0), nlat - 2)
    i0 = min(max(i0, 0), nlon - 2)
    return ((1 - fj) * ((1 - fi) * fld[j0, i0] + fi * fld[j0, i0 + 1])
            + fj * ((1 - fi) * fld[j0 + 1, i0] + fi * fld[j0 + 1, i0 + 1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-tracks', type=int, default=40)
    ap.add_argument('--year-min', type=int, default=2010)
    ap.add_argument('--year-max', type=int, default=2024)
    ap.add_argument('--month-min', type=int, default=6)
    ap.add_argument('--month-max', type=int, default=11)
    a = ap.parse_args()

    from simulator.simcore import core as V
    z = np.load(NPZ)
    paths = sorted(glob.glob(os.path.join(OBS_DIR, '**', '*.dat'), recursive=True))
    rows = []
    ntr = 0
    for p in paths:
        tr = parse_track(p)
        if len(tr) < 8 or not (a.year_min <= tr[0][0].year <= a.year_max):
            continue
        if not (a.month_min <= tr[0][0].month <= a.month_max):
            continue
        if min(x[1] for x in tr) < 0:
            continue
        ntr += 1
        for i in range(len(tr) - 1):
            ta, la_a, lo_a, _va = tr[i]
            tb, la_b, lo_b, _vb = tr[i + 1]
            dt_h = (tb - ta).total_seconds() / 3600.0
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
            m = ta.month - 1
            bu, bv = V.beta_drift(la_a)
            rows.append((
                la_a, u_obs - bu, v_obs - bv,
                bilin(z['u850'][m], la_a, lo_a) * KT, bilin(z['v850'][m], la_a, lo_a) * KT,
                bilin(z['u500'][m], la_a, lo_a) * KT, bilin(z['v500'][m], la_a, lo_a) * KT,
                bilin(z['u200'][m], la_a, lo_a) * KT, bilin(z['v200'][m], la_a, lo_a) * KT))
        if ntr >= a.max_tracks:
            break
    R = np.array(rows, float)
    lat, tu, tv = R[:, 0], R[:, 1], R[:, 2]
    sh, md, dp = R[:, 3:5], R[:, 5:7], R[:, 7:9]
    print('样本 %d 步, %d 条路径(月平均基态, 观测位置)' % (len(R), ntr))
    print()
    print('%-9s %6s | %-17s | %-17s | %-17s | %-17s'
          % ('纬度带', 'n', '目标 obs-β u/v', '850 u/v', '500 u/v', '200 u/v'))
    for b in BANDS:
        s = (lat >= b[0]) & (lat < b[1])
        if s.sum() < 20:
            continue
        print('%-9s %6d | %+6.2f / %+6.2f  | %+6.2f / %+6.2f | %+6.2f / %+6.2f | %+6.2f / %+6.2f'
              % ('%d-%dN' % b, s.sum(), tu[s].mean(), tv[s].mean(),
                 sh[s, 0].mean(), sh[s, 1].mean(), md[s, 0].mean(), md[s, 1].mean(),
                 dp[s, 0].mean(), dp[s, 1].mean()))

    def fit2(A, B, tgt_u, tgt_v):
        best = (1e9, None)
        for i in range(21):
            w = i * 0.05
            ru = w * A[:, 0] + (1 - w) * B[:, 0] - tgt_u
            rv = w * A[:, 1] + (1 - w) * B[:, 1] - tgt_v
            r = float(np.sqrt(np.mean(ru ** 2 + rv ** 2)))
            if r < best[0]:
                best = (r, w)
        return best

    def fit3(A, B, C, tgt_u, tgt_v):
        best = (1e9, None)
        for i in range(21):
            for j in range(21 - i):
                k = 20 - i - j
                w = (i * 0.05, j * 0.05, k * 0.05)
                u = w[0] * A[:, 0] + w[1] * B[:, 0] + w[2] * C[:, 0]
                v = w[0] * A[:, 1] + w[1] * B[:, 1] + w[2] * C[:, 1]
                r = float(np.sqrt(np.mean((u - tgt_u) ** 2 + (v - tgt_v) ** 2)))
                if r < best[0]:
                    best = (r, w)
        return best

    print()
    print('%-9s %6s | %-26s | %-26s | %s'
          % ('纬度带', 'n', '两层 850/200: w850, RMSE', '三层 850/500/200: w, RMSE',
             '合成中层 vs 真500 的总 RMSE'))
    for b in BANDS:
        s = (lat >= b[0]) & (lat < b[1])
        if s.sum() < 20:
            continue
        t_u, t_v = tu[s], tv[s]
        r2 = fit2(sh[s], dp[s], t_u, t_v)
        r3 = fit3(sh[s], md[s], dp[s], t_u, t_v)
        # 四层: 850 + 合成中层(0.45*850+0.55*200) + 真500 + 200, 用单纯形
        syn = 0.45 * sh[s] + 0.55 * dp[s]
        best = (1e9, None)
        L = [sh[s], syn, md[s], dp[s]]
        for i in range(21):
            for j in range(21 - i):
                for k in range(21 - i - j):
                    q = 20 - i - j - k
                    w = np.array([i, j, k, q], float) * 0.05
                    u = sum(w[t] * L[t][:, 0] for t in range(4))
                    v = sum(w[t] * L[t][:, 1] for t in range(4))
                    rr = float(np.sqrt(np.mean((u - t_u) ** 2 + (v - t_v) ** 2)))
                    if rr < best[0]:
                        best = (rr, w)
        print('%-9s %6d | w850=%.2f RMSE=%5.2f        | %s RMSE=%5.2f   | 0.45/0.55合成+真500 最优 RMSE=%5.2f w=%s'
              % ('%d-%dN' % b, s.sum(), r2[1], r2[0],
                 '/'.join('%.2f' % x for x in r3[1]), r3[0], best[0],
                 '/'.join('%.2f' % x for x in best[1])))
    print()
    print('判读: 若"真 500hPa"那一条的 RMSE 明显低于合成中层 -> 该把训练好的 u500/v500')
    print('      接进内核(数据保真), 并让中层用真值; 若差不多 -> 合成中层不是问题所在。')


if __name__ == '__main__':
    main()
