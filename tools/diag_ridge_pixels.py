# -*- coding: utf-8 -*-
"""只量一件事: 副高图层到底画了多少像素?

团队报告称 `render_map` 漏传 `grid=` 导致副高填色退化成 **1 像素宽**(不可见)。
本轮已补上 `grid=grid`。这里对"开/关副高"做地图区帧间差分, 给出实际像素数。
"""
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402
from app.ty_sim import TySim  # noqa: E402
from simulator.simcore import render as R  # noqa: E402


def main():
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    app = TySim(screen)
    app.md = app.MODE_SIM
    app._sim_reset(msg=False)
    mh = int(getattr(app, 'map_height', screen.get_height()))

    def frame(layers):
        app.sim_layers = set(layers)
        screen.fill((0, 0, 0))
        app._sim_render(screen)
        return pygame.surfarray.array3d(screen).transpose(1, 0, 2)[:mh].astype(np.int16)

    off = frame(set())
    on = frame({'ridge'})
    d = (np.abs(on - off).sum(axis=2) > 8)
    n = int(d.sum())
    ys, xs = np.nonzero(d)
    w = (int(xs.max() - xs.min() + 1) if n else 0)
    h = (int(ys.max() - ys.min() + 1) if n else 0)
    print('地图区高 = %d px' % mh)
    print('副高开/关差异像素 = %d   包围盒 %d x %d' % (n, w, h))
    print('RIDGE_VISIBLE_PX = %s' % (getattr(R, 'RIDGE_VISIBLE_PX', ['N/A'])[0],))
    print()
    if n <= 0:
        print('判定: **副高完全没画出来**')
    elif w <= 3:
        print('判定: **退化成 %d 像素宽(不可见)** —— 团队的说法成立' % w)
    else:
        print('判定: 副高正常画出(宽 %d px), 团队"1 像素宽"的说法不成立' % w)
    print('说明: 副高主体可能不在默认视野内; 若差异像素为 0, 也要看视图, 不能直接判"坏"。')


if __name__ == '__main__':
    main()
