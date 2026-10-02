# -*- coding: utf-8 -*-
"""扫 `core._STEER_DATA_W`(引导风里真实基态的权重)与浅层有效权重, 定标定解。

两个待定量的来源:
  · g = 真实基态权重: `u850 = g*DATA + (1-g)*解析地转`。原写死 0.55。
  · eff850 = 浅层有效引导权重(`steering_weights` 的等效值)。

判据: 在**观测路径所在位置**上, 模型分层引导风对"观测移速 − β"的 RMSE(kt)。
口径与 `tools/calib_steer.py` 完全一致(共用 `collect`, 同一采样通路)。

用法: python tools/calib_steer_sweep.py --max-tracks 40 --gs 0.55,0.7,0.85,1.0
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from calib_steer import BANDS, collect, rmse, eff      # noqa: E402

WS = np.round(np.arange(0.0, 1.0001, 0.05), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-tracks', type=int, default=40)
    ap.add_argument('--year-min', type=int, default=2010)
    ap.add_argument('--year-max', type=int, default=2024)
    ap.add_argument('--month-min', type=int, default=6)
    ap.add_argument('--month-max', type=int, default=11)
    ap.add_argument('--gs', default='0.55,0.70,0.85,1.00')
    a = ap.parse_args()
    gs = [float(x) for x in a.gs.split(',') if x.strip()]

    print('%-6s %7s | %-28s | %s' % ('g', '总RMSE', '总量最优 (ws,wm,wd) -> eff850',
                                     '分带 RMSE(0-10 / 10-20 / 20-30 / 30-40 / 40-60N)'))
    best_all = None
    for g in gs:
        rows = collect(a.max_tracks, a.year_min, a.year_max,
                       a.month_min, a.month_max, g)
        if len(rows) < 100:
            print('g=%.2f 样本不足' % g)
            continue
        # 总量最优(单一权重)
        b = (1e9, None)
        for ws in WS:
            for wm in WS:
                if ws + wm > 1.0001:
                    continue
                wd = round(1.0 - ws - wm, 4)
                r = rmse(rows, ws, wm, wd)
                if r < b[0]:
                    b = (r, (ws, wm, wd))
        # 现权重(g 变化不影响 weights, 只影响层风速) —— 记下 0.65 的 RMSE 做可比
        r065 = rmse(rows, 0.50, 0.25, 0.25)
        per = []
        for bb in BANDS:
            sub = [r for r in rows if bb[0] <= r[0] < bb[1]]
            per.append(np.nan if len(sub) < 20 else rmse(sub, *b[1]))
        print('%-6.2f %7.2f | %.2f/%.2f/%.2f -> eff850 %.2f | %s   (eff850=0.65 时总RMSE %.2f)'
              % (g, b[0], b[1][0], b[1][1], b[1][2], eff(b[1])[0],
                 ' / '.join('n.a.' if np.isnan(x) else '%.2f' % x for x in per), r065))
        if best_all is None or b[0] < best_all[0]:
            best_all = (b[0], g, b[1], rows)
    print()
    if best_all:
        print('最优: g=%.2f  权重 %.2f/%.2f/%.2f (eff850=%.2f)  总 RMSE %.2f'
              % (best_all[1], best_all[2][0], best_all[2][1], best_all[2][2],
                 eff(best_all[2])[0], best_all[0]))
        rows = best_all[3]
        print()
        print('该 g 下的分层均值(用于判断物理方向):')
        for bb in BANDS:
            sub = [r for r in rows if bb[0] <= r[0] < bb[1]]
            if len(sub) < 20:
                continue
            t = np.array([r[1] for r in sub])
            sh = np.array([r[2] for r in sub])
            dp = np.array([r[3] for r in sub])
            print('  %-8s n=%4d 目标 %+6.2f/%+6.2f | 850 %+6.2f/%+6.2f | 200 %+6.2f/%+6.2f'
                  % ('%d-%dN' % bb, len(sub), t[:, 0].mean(), t[:, 1].mean(),
                     sh[:, 0].mean(), sh[:, 1].mean(), dp[:, 0].mean(), dp[:, 1].mean()))
    print()
    print('注意: g 越大, 解析地转(含 sim[eddies] 瞬变涡)的占比越小。g=1.0 等于')
    print('      完全丢掉解析瞬变 —— 若最优解是 1.0, 必须**另外**补瞬变引导, 否则')
    print('      引导场变成纯气候态, 路径会过分平滑、缺少槽脊造成的转向。')


if __name__ == '__main__':
    main()
