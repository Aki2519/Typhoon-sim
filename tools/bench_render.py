# -*- coding: utf-8 -*-
"""渲染基准（用户 2026-10-19："先建基准"）。

同一进程里跑多组配置，每组：预热若干帧 → 跑 R 轮、每轮 N 帧 → 取**中位数**。
不用"跑一次看看"的数字：实测同一配置两次能差 20 ms（缓存冷热/系统抖动）。

用法：
    python tools/bench_render.py                 # 默认 2026-08-25（10 个风暴）
    python tools/bench_render.py --date 2026-08-20 --frames 40 --rounds 5
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from datetime import datetime

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame                                        # noqa: E402

from app.ty_sim import TySim                         # noqa: E402


def one_run(date, frames, rounds, warmup, size=(1600, 1000), hide=(), label=''):
    pygame.display.set_mode(size)
    sim = TySim(pygame.display.set_mode(size))
    sim.md = sim.MODE_SEASON
    sim.dark_mode = True
    for h in hide:
        try:
            sim.renderer.hidden_layers.add(h)
        except Exception:
            pass
    sim.season_ctrl.jump_to(date)
    sim._sync_season_state()
    sim.playback_ctrl.update_all(0.0, 0.05, False, sim.season_ctrl)
    sim.update(0.05)
    act = len([t for t in sim.tys if getattr(t, 'act', False)])
    surf = pygame.Surface(size)
    for _ in range(warmup):                       # 预热：建缓存/载流/解码首帧
        sim.update(1 / 60)
        sim.renderer.draw(surf)
    per_round = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        for _ in range(frames):
            sim.update(1 / 60)
            sim.renderer.draw(surf)
        per_round.append((time.perf_counter() - t0) / frames * 1000.0)
    med = statistics.median(per_round)
    print('%-26s 风暴 %2d  中位 %6.1f ms/帧 → %4.1f fps   [%s]' % (
        label or 'default', act, med, 1000.0 / med,
        ' '.join('%.0f' % v for v in per_round)))
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='2026-08-25')
    ap.add_argument('--frames', type=int, default=30)
    ap.add_argument('--rounds', type=int, default=5)
    ap.add_argument('--warmup', type=int, default=20)
    args = ap.parse_args()
    date = datetime.strptime(args.date, '%Y-%m-%d')
    pygame.init()
    print('基准：%s  %d 帧/轮 × %d 轮，取中位' % (date.date(), args.frames, args.rounds))
    one_run(date, args.frames, args.rounds, args.warmup, label='默认（全部图层）')
    one_run(date, args.frames, args.rounds, args.warmup,
            hide=('typhoon_info',), label='隐藏图标/名字/信息框')
    one_run(date, args.frames, args.rounds, args.warmup,
            hide=('typhoon_info', 'typhoons', 'track_label'),
            label='只留地图+钟+ACE')


if __name__ == '__main__':
    main()
