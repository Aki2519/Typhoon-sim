# -*- coding: utf-8 -*-
"""出两张图供目视: 模拟模式**弹层关闭**(录视频时的样子) 与 **弹层展开**。"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                                   # noqa: E402

pygame.init()
screen = pygame.display.set_mode((1360, 885))
from app.ty_sim import TySim                                    # noqa: E402

sim = TySim(screen)
sim.md = TySim.MODE_SIM
sim._apply_panel_height_for_mode()
sim.update(0.0)

for tag, openit in (('closed', False), ('open', True)):
    cp = sim.control_panel
    if openit:
        cp.toggle_layer_popup()
    else:
        cp.close_layer_popup()
    sim.draw(screen)
    out = 'G:/tmp/ty4out/popup_%s.png' % tag
    pygame.image.save(screen, out)
    print('-> %s  (弹层 %s, 控制面板高 %s)'
          % (out, '展开' if openit else '关闭', sim.control_panel_height))
