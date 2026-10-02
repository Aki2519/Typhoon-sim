# -*- coding: utf-8 -*-
"""重标定**引导权重**(副高/引导气流的可验收形式)。

为什么必须重做(轮次 102 的证据):
    `tools/probe_steer_obs.py` 把观测路径喂给模型引导场, 逐 6 小时对比:

      纬度带    n    | 观测移速 u/v      | 模型引导+β u/v     | 偏差 Δu/Δv
      0-10N    73    |  -5.98 / +2.55   |  -9.01 / +1.17    |  -3.03 / -1.38
      10-20N  539    |  -8.31 / +3.17   |  -7.99 / +3.88    |  +0.32 / +0.71
      20-30N  334    |  -4.62 / +4.86   |  -5.43 / +3.62    |  -0.81 / -1.24
      30-40N  129    |  +0.28 / +9.02   | **+26.39 / -0.10**| **+26.11 / -9.12**
      40-60N   64    | +10.06 / +11.31  | **+34.59 / -0.39**| **+24.53 / -11.70**

    30N 以上是**灾难性的**: 观测里台风在 30-40N 仍以 **+9.0kt 向极**移动(典型转向),
    模型却给出 **+26.4kt 纯东向、向极分量 -0.1**。
    物理原因很清楚: `steering_weights` 在 |lat|>30 给 **深(200hPa)权重 1.0**,
    而 200hPa 在高纬是**急流**(8 月急流轴 44.5N、峰值 ~99kt), 于是台风被按急流方向
    向东甩走 —— 既拿不到向极分量, 也飞快离开暖水, 这正是"最高纬度只有 18.5 度、
    EX 一个都没有、强端偏弱"的共同机制。

    原标定表里 ">26° 最优 RMSE **94.08 kt**" 本身就是这个 bug 的指纹:
    台风实际移速只有 0-20kt, 94kt 的 RMSE 说明那一行的回归数据是坏的, 不该采信。

做法(与旧标定同形式但口径正确):
    目标向量 t = 观测移速 - β漂移(模型自己的 β 函数);
    特征 = 模型**同一条采样通路** (`core.steering_layers`) 在**观测位置**上的
    浅层(850)与深层(200); 中层按代码定义 mid = 0.6*浅 + 0.4*深。
    在单纯形 (ws, wm, wd) 上网格搜索, 逐纬度带最小化 |模型-t| 的 RMSE。

用法:
    python tools/calib_steer.py --max-tracks 60
    python tools/calib_steer.py --max-tracks 60 --dump output/sim/_reference/steer_samples.npz
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from probe_steer_obs import parse_track          # noqa: E402  (同一条解析口径)

OBS_DIR = r'G:\气象\数据\xrq\wp'
BANDS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 60)]
STEP = 0.05


def _ring_avg(sim, lon, lat, arr):
    """按**模型自己的**三环 x 8 方位权重做面积加权平均(与 core.steering_layers 同一条路)。

    为什么不能点采样: 瞬变槽脊场的经向尺度可以小到 30 度(波数 6 于 180 度跨度),
    ±600km(±5.4 度)的环带平均实测把 v' 削掉 **17%**(+1.668 -> +1.380)。
    点采样会让标定看到的瞬变比模式实际用到的更强, 属于口径不一致。
    """
    coslat = max(1e-6, math.cos(math.radians(lat)))
    from simulator.simcore import core as V
    lo = lon + V._STEER_OFF_LON / coslat
    la = lat + V._STEER_OFF_LAT
    inside = ((lo >= V.C['LON0'] + 1) & (lo <= V.C['LON1'] - 1)
              & (la >= V.C['LAT0'] + 1) & (la <= V.C['LAT1'] - 1))
    wl = np.zeros(lo.shape, dtype=np.float64)
    if inside.any():
        wl[inside] = 1.0 - V.land_at_arr(sim, lo[inside], la[inside])
    keep = inside & (wl >= 0.3)
    wsum = float((V._STEER_RING_W[keep] * wl[keep]).sum()) if keep.any() else 0.0
    if wsum < 0.5:
        return float(V.env_bilinear(arr, lon, lat))
    w = V._STEER_RING_W[keep] * wl[keep]
    return float((V.env_bilinear_arr(arr, lo[keep], la[keep]) * w).sum() / wsum)


def collect(max_tracks, y0, y1, m0, m1, g=None, syn_scale=None):
    """-> list of (lat, t_steer(u,v), s_sh(u,v), s_dp(u,v))"""
    from simulator.simcore import core as V
    if g is not None:
        V._STEER_DATA_W = float(g)        # 引导风里真实基态的权重(见 core 里的定义)
    if syn_scale is not None:             # 瞬变槽脊幅度倍率(0 = 关掉, 用来量基准 std)
        V._SYN_STEER_V0 = 2.0 * float(syn_scale)
        V._SYN_STEER_VG = 4.8 * float(syn_scale)
    paths = sorted(glob.glob(os.path.join(OBS_DIR, '**', '*.dat'), recursive=True))
    tracks = []
    for p in paths:
        tr = parse_track(p)
        if len(tr) < 8 or not (y0 <= tr[0][0].year <= y1):
            continue
        if not (m0 <= tr[0][0].month <= m1):
            continue
        if min(x[1] for x in tr) < 0:
            continue
        tracks.append(tr)
        if len(tracks) >= max_tracks:
            break
    sim = V.init({'start': '2020-01-01 00', 'seed': 7, 'envStepH': 6.0,
                  'params': {'dataMode': True, 'precision': '1s'}})
    t0 = datetime.strptime('2020-01-01 00', '%Y-%m-%d %H')
    rows = []
    cur = None
    for tr in tracks:
        for i in range(len(tr) - 1):
            ta, la_a, lo_a, v_a = tr[i]
            tb, la_b, lo_b, _v_b = tr[i + 1]
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
            if ta.month != cur:
                sim['t'] = (ta - t0).total_seconds() / 3600.0
                V.env_update(sim)
                cur = ta.month
            s_sh, s_dp = V.steering_layers(sim, lo_a, la_a)
            # **瞬变槽脊引导按观测时刻单独采样**: 它逐时次变化, 而上面 env_update
            # 只在换月时调用(月内冻结)。口径等价性: 模型把同一个 Δ 正压地加在
            # 850/200 两层上, 而 `steering_at` 的三层加权系数和为 1 ⇒ 引导里的 Δ
            # 就是 Δ 本身。环带平均(±600km)对 60-120 度波长衰减 <1%, 故直接点采样。
            sim['t'] = (ta - t0).total_seconds() / 3600.0
            tu, tv = V.synoptic_steer(sim)
            if tu is not None:
                # 走**模型同一条环带平均通路**(点采样会把瞬变高估 ~17%)
                _du = _ring_avg(sim, lo_a, la_a, tu)
                _dv = _ring_avg(sim, lo_a, la_a, tv)
                s_sh = [s_sh[0] + _du, s_sh[1] + _dv]
                s_dp = [s_dp[0] + _du, s_dp[1] + _dv]
            bu, bv = V.beta_drift(la_a)
            rows.append((la_a, (u_obs - bu, v_obs - bv), tuple(s_sh), tuple(s_dp)))
    return rows


def rmse(rows, ws, wm, wd):
    e = []
    tot = ws + wm + wd
    for _la, t, sh, dp in rows:
        mid = (0.6 * sh[0] + 0.4 * dp[0], 0.6 * sh[1] + 0.4 * dp[1])
        mu = (ws * sh[0] + wm * mid[0] + wd * dp[0]) / tot
        mv = (ws * sh[1] + wm * mid[1] + wd * dp[1]) / tot
        e.append((mu - t[0]) ** 2 + (mv - t[1]) ** 2)
    return float(np.sqrt(np.mean(e)))


def grid(rows):
    best = (1e9, None)
    n = int(round(1.0 / STEP))
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            r = rmse(rows, i * STEP, j * STEP, k * STEP)
            if r < best[0]:
                best = (r, (i * STEP, j * STEP, k * STEP))
    return best


def eff(w):
    ws, wm, wd = w
    return ws + 0.6 * wm, wd + 0.4 * wm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-tracks', type=int, default=60)
    ap.add_argument('--year-min', type=int, default=2010)
    ap.add_argument('--year-max', type=int, default=2024)
    ap.add_argument('--month-min', type=int, default=6)
    ap.add_argument('--month-max', type=int, default=11)
    ap.add_argument('--dump', default='')
    ap.add_argument('--g', type=float, default=None,
                    help='引导风里真实基态的权重(默认沿用 core 的 _STEER_DATA_W=0.55)')
    ap.add_argument('--syn-scale', type=float, default=None,
                    help='瞬变槽脊幅度倍率(0 = 关掉, 用来量"月平均基准"的 std)')
    a = ap.parse_args()

    rows = collect(a.max_tracks, a.year_min, a.year_max, a.month_min, a.month_max,
                   a.g, a.syn_scale)
    print('样本 %d 步(观测路径所在位置的模型分层风), 真实基态权重 g=%s, 瞬变倍率 %s'
          % (len(rows), '默认' if a.g is None else a.g,
             '默认' if a.syn_scale is None else a.syn_scale))
    if a.dump:
        arr = np.array([(r[0], r[1][0], r[1][1], r[2][0], r[2][1], r[3][0], r[3][1])
                        for r in rows], float)
        np.savez_compressed(a.dump, s=arr)
        print('-> %s' % a.dump)
    print()
    from simulator.simcore import core as V
    print('%-9s %6s | %-22s | %-20s | %s'
          % ('纬度带', 'n', '目标(观测-β) u/v', '浅层850 u/v', '深层200 u/v'))
    for b in BANDS:
        sub = [r for r in rows if b[0] <= r[0] < b[1]]
        if len(sub) < 20:
            print('%-9s %6d  样本不足' % ('%d-%dN' % b, len(sub)))
            continue
        t = np.array([r[1] for r in sub])
        sh = np.array([r[2] for r in sub])
        dp = np.array([r[3] for r in sub])
        print('%-9s %6d | %+6.2f / %+6.2f      | %+6.2f / %+6.2f    | %+6.2f / %+6.2f'
              % ('%d-%dN' % b, len(sub), t[:, 0].mean(), t[:, 1].mean(),
                 sh[:, 0].mean(), sh[:, 1].mean(), dp[:, 0].mean(), dp[:, 1].mean()))
    print()
    print('%-9s %6s | %-24s | %-24s | %s'
          % ('纬度带', 'n', '现行权重 (850/mid/200) RMSE', '最优权重 (850/mid/200) RMSE',
             '最优 -> 等效(850/200)'))
    for b in BANDS:
        sub = [r for r in rows if b[0] <= r[0] < b[1]]
        if len(sub) < 20:
            continue
        la = 0.5 * (b[0] + b[1])
        cur = V.steering_weights(40.0, la)
        r_cur = rmse(sub, *cur)
        r_best, w_best = grid(sub)
        e = eff(w_best)
        print('%-9s %6d | %.2f / %.2f / %.2f -> %5.2f | %.2f / %.2f / %.2f -> %5.2f | %.2f / %.2f'
              % ('%d-%dN' % b, len(sub), cur[0], cur[1], cur[2], r_cur,
                 w_best[0], w_best[1], w_best[2], r_best, e[0], e[1]))
    print()
    print('**分布对比**(含瞬变槽脊引导, 用现行权重): 均值要接近 0 偏差, 标准差要量级相当')
    print('%-9s %6s | %-24s | %-24s | %s'
          % ('纬度带', 'n', '目标(观测-β) u/v 均值±std', '模型引导 u/v 均值±std', '偏差 Δu/Δv'))
    for b in BANDS:
        sub = [r for r in rows if b[0] <= r[0] < b[1]]
        if len(sub) < 20:
            continue
        t = np.array([r[1] for r in sub])
        sh = np.array([r[2] for r in sub])
        dp = np.array([r[3] for r in sub])
        ws, wm, wd = V.steering_weights(40.0, 0.5 * (b[0] + b[1]))
        mid = 0.6 * sh + 0.4 * dp
        mu = ws * sh[:, 0] + wm * mid[:, 0] + wd * dp[:, 0]
        mv = ws * sh[:, 1] + wm * mid[:, 1] + wd * dp[:, 1]
        print('%-9s %6d | %+6.2f±%5.2f / %+6.2f±%5.2f | %+6.2f±%5.2f / %+6.2f±%5.2f | %+6.2f / %+6.2f'
              % ('%d-%dN' % b, len(sub), t[:, 0].mean(), t[:, 0].std(),
                 t[:, 1].mean(), t[:, 1].std(), mu.mean(), mu.std(), mv.mean(), mv.std(),
                 mu.mean() - t[:, 0].mean(), mv.mean() - t[:, 1].mean()))
    print()
    print('判读: ① 若最优解在 20-30N / 30-40N 把权重压向**浅层**, 说明现行"高纬抓 200hPa"')
    print('      是错的(那是急流, 不是引导); ② 等效权重随纬度应**平滑**, 不要逐带硬编码')
    print('      (逐带硬编码会在带边界产生不连续, 台风的移向会莫名跳变)。')


if __name__ == '__main__':
    main()
