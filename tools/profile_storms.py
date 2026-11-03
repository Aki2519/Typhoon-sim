# -*- coding: utf-8 -*-
"""性能探针：**风暴多的时候**到底哪一步慢（用户 2026-10-19："tc 一多动画帧率就暴降"）。

不做任何修改，只测量：
    ① 每个帧里 update() 与 renderer.draw() 各占多少
    ② cProfile 找出 draw 内部的热点（按累计时间排序）
"""
import cProfile
import io
import os
import pstats
import sys
import time
from datetime import datetime

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, '.')

import pygame                                            # noqa: E402

from app.ty_sim import TySim                             # noqa: E402

W, H = 1600, 1000
DATE = datetime(2026, 8, 25, 0)
FRAMES = 30


def main():
    pygame.init()
    pygame.display.set_mode((W, H))
    sim = TySim(pygame.display.set_mode((W, H)))
    sim.md = sim.MODE_SEASON
    sim.dark_mode = True
    sim.season_ctrl.jump_to(DATE)
    sim._sync_season_state()
    sim.playback_ctrl.update_all(0.0, 0.05, False, sim.season_ctrl)
    sim.update(0.05)

    act = [t for t in sim.tys if getattr(t, 'act', False)]
    print('日期 %s | 活跃风暴 %d 个: %s' % (
        DATE.date(), len(act), ', '.join(t.name for t in act[:12])))

    surf = pygame.Surface((W, H))
    # 预热（建缓存/载素材）
    for _ in range(3):
        sim.update(1 / 60)
        sim.renderer.draw(surf)

    t_upd = t_draw = 0.0
    for _ in range(FRAMES):
        a = time.perf_counter()
        sim.playback_ctrl.update_all(0.0, 1 / 60, False, sim.season_ctrl)
        sim.update(1 / 60)
        b = time.perf_counter()
        sim.renderer.draw(surf)
        c = time.perf_counter()
        t_upd += b - a
        t_draw += c - b
    n = FRAMES
    print('平均 update %.2f ms | draw %.2f ms | 合计 %.2f ms  (60fps 预算 16.7 ms)'
          % (t_upd * 1000 / n, t_draw * 1000 / n, (t_upd + t_draw) * 1000 / n))

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(10):
        sim.update(1 / 60)
        sim.renderer.draw(surf)
    pr.disable()
    st = pstats.Stats(pr)
    st.sort_stats('cumulative')
    buf = io.StringIO()
    st.stream = buf
    st.print_stats(22)
    lines = buf.getvalue().splitlines()
    print('\n=== draw+update 热点（cumulative）===')
    for ln in lines:
        if 'ncalls' in ln or ('app' in ln and '.py' in ln) or 'pygame' in ln.lower():
            print(ln[:150])


if __name__ == '__main__':
    main()
