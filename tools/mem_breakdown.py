# -*- coding: utf-8 -*-
"""内存分项测量（用户 2026-10-19："先做内存分项测量，再决定砍谁"）。

10 个风暴时整机 RSS 会到 ~1.9 GB、帧时间在 66~158 ms 之间抖 —— 先看清 1.4 GB
是谁吃掉的，再动手。

用法：
    python tools/mem_breakdown.py --date 2026-08-25 --warm 20
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
from datetime import datetime

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame                                       # noqa: E402

from app.ty_sim import TySim                        # noqa: E402


def surf_bytes(s) -> int:
    try:
        w, h = s.get_size()
        return w * h * 4
    except Exception:
        return 0


def mb(n: int) -> str:
    return '%8.1f MB' % (n / 1e6)


def rss() -> int:
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
        return 0


def smcy_breakdown() -> list:
    from app.smcy_icon import get_smcy_manager
    mgr = get_smcy_manager()
    rows = []
    for key, st in list(getattr(mgr, '_streams', {}).items()):
        sc = sum(surf_bytes(s) for s in getattr(st, '_cache', {}).values())
        nc = sum(getattr(a, 'nbytes', 0) for a in getattr(st, '_ncache', {}).values())
        rows.append(('smcy %-10s' % str(key), sc + nc,
                     '%d 表面 + %d 原生帧' % (len(getattr(st, '_cache', {})),
                                             len(getattr(st, '_ncache', {})))))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='2026-08-25')
    ap.add_argument('--warm', type=int, default=20)
    ap.add_argument('--size', default='1600x1000')
    args = ap.parse_args()
    w, h = (int(v) for v in args.size.lower().split('x'))
    pygame.init()
    pygame.display.set_mode((w, h))
    print('RSS 起始 %s' % mb(rss()))

    sim = TySim(pygame.display.set_mode((w, h)))
    sim.md = sim.MODE_SEASON
    sim.dark_mode = True
    print('RSS 建完 TySim %s' % mb(rss()))

    # 地图
    mv = getattr(sim, 'map_view', None) or getattr(getattr(sim, 'view', None), 'map', None)
    map_rows = []
    pyr = getattr(mv, '_pyramid', None) if mv is not None else None
    if pyr:
        tot = sum(surf_bytes(s) for s in pyr)
        map_rows.append(('map 金字塔 %d 层' % len(pyr), tot,
                         ' '.join('%dx%d' % s.get_size() for s in pyr)))
    if getattr(sim, 'map_img', None) is not None:
        map_rows.append(('map map_img', surf_bytes(sim.map_img), ''))

    sim.season_ctrl.jump_to(datetime.strptime(args.date, '%Y-%m-%d'))
    sim._sync_season_state()
    sim.playback_ctrl.update_all(0.0, 0.05, False, sim.season_ctrl)
    sim.update(0.05)
    act = len([t for t in sim.tys if getattr(t, 'act', False)])
    surf = pygame.Surface((w, h))
    for _ in range(args.warm):
        sim.update(1 / 60)
        sim.renderer.draw(surf)
    gc.collect()
    print('RSS 预热 %d 帧后 %s   （活跃风暴 %d）' % (args.warm, mb(rss()), act))

    rows = map_rows + smcy_breakdown()
    # 其它 SurfaceCache
    try:
        from app.ty_sim_mixins._draw_icon_mixin import TySimDrawIconMixin
        c = TySimDrawIconMixin._purple_frame_cache
        rows.append(('purple_frame_cache', getattr(c, '_bytes', 0),
                     '%d 条 / 上限 %.0f MB' % (len(getattr(c, '_d', {})),
                                               getattr(c, 'max_bytes', 0) / 1e6)))
    except Exception:
        pass
    rows.sort(key=lambda r: -r[1])
    print('\n%-22s %10s  %s' % ('组件', '占用', '明细'))
    tot = 0
    for name, n, note in rows:
        tot += n
        print('%-22s %s  %s' % (name, mb(n), note))
    print('%-22s %s' % ('以上合计', mb(tot)))


if __name__ == '__main__':
    main()
