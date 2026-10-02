# -*- coding: utf-8 -*-
"""决定性检查: 把 pDisp 设成"处处都是副高", 副高填色到底画不画得出来?

背景: 轮次 153 给 `render_map` 的 `draw_ridge` 补了 `grid=grid`(为了修"视野内像素"
计数)。这会把贴图从 `_blit_layer(grid=None)` 整域路径切到 grid 路径 —— 必须确认
填色没被切坏。默认视野里副高本来就不在(实测差异只有文字标注), 所以这里**直接构造
一个处处超阈值的气压场**再调 `draw_ridge`, 与"不画副高"的基线做差分。
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
    sim = app.sim_v4
    mh = int(getattr(app, 'map_height', screen.get_height()))

    surf = pygame.Surface(screen.get_size())
    fx = app.latlon_to_screen
    fy = app.latlon_to_screen
    grid = app._sim_grid_map(surf)

    ny, nx = int(R.V.ENVNY), int(R.V.ENVNX)
    sim['env']['pDisp'] = np.full((ny, nx), 1020.0, np.float32)   # 全部超阈值
    sim['_envVer'] = int(sim.get('_envVer', 0)) + 1
    R._layer_surf_cache.clear()

    # 基线: 什么都不画
    surf.fill((0, 0, 0))
    base = pygame.surfarray.array3d(surf).transpose(1, 0, 2)[:mh].astype(np.int16)

    # 画副高(带 grid)
    R.RIDGE_VISIBLE_PX[0] = -1
    R.draw_ridge(surf, sim, fx, fy, grid=grid)
    on = pygame.surfarray.array3d(surf).transpose(1, 0, 2)[:mh].astype(np.int16)
    d = (np.abs(on - base).sum(axis=2) > 8)
    n = int(d.sum())
    ys, xs = np.nonzero(d)
    w = int(xs.max() - xs.min() + 1) if n else 0
    h = int(ys.max() - ys.min() + 1) if n else 0

    print('气压场设成处处 1020 hPa(全部超阈值)')
    print('带 grid 调用 draw_ridge 后: 差异像素 = %d, 包围盒 %d x %d' % (n, w, h))
    print('RIDGE_VISIBLE_PX = %s' % R.RIDGE_VISIBLE_PX[0])
    print()
    if n < 1000:
        print('判定: **填色没画出来**(只有标注) => 补 grid 引入了回归, 必须回退或另修')
    elif w < 20:
        print('判定: **退化成 %d 像素宽** => 填色坏了' % w)
    else:
        print('判定: 填色正常画出(%d px, %d x %d) => 补 grid 没有破坏渲染' % (n, w, h))
    print('注: 这里用处处超阈值的场, 所以计数与"是否在视野内"无关, 只看填色本身。')


if __name__ == '__main__':
    main()
