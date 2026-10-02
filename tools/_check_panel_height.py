# -*- coding: utf-8 -*-
"""验证"模拟模式叠加层不再拉高功能栏"(用户 2026-10-01 要求)。

检查三件事并出图:
  1. 模拟模式的 `control_panel_height` 是否等于 CPH(不再 +34);
  2. 图层组的按钮是否仍在(一个都没少)、且它们的 y 落在**地图区域内**;
  3. 出两张 PNG(正常模式 / 模拟模式)供**目视**确认按钮没被挡、文字没糊。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                                   # noqa: E402

pygame.init()
from app.ty_sim import TySim                                    # noqa: E402
from app.constants import CPH                                   # noqa: E402

W, H = 1360, 885
screen = pygame.display.set_mode((W, H))
sim = TySim(screen)

for mode, tag in ((TySim.MODE_NORMAL, 'normal'), (TySim.MODE_SIM, 'sim')):
    sim.md = mode
    sim._apply_panel_height_for_mode()
    cp = getattr(sim, 'control_panel_height', None)
    panel = sim._panel_for_draw() if hasattr(sim, '_panel_for_draw') else None
    print('--- %s ---' % tag)
    print('  控制面板高度 = %s   (CPH=%s, 模拟模式**不应**再 +34)' % (cp, CPH))
    if cp != CPH:
        print('  !! 仍然被拉高了: %s != %s' % (cp, CPH))
    # 直接用 ControlPanel 建一次, 看图层按钮
    from app.control_panel import ControlPanel
    p = ControlPanel(sim)
    p.build()
    layer = [b for b in p._buttons if b.key.startswith(('layer_', 'envlay_'))
             or b.key in ('wind_mode', 'shear_style')]
    vis = [b for b in layer if b.visible]
    if layer:
        ys = sorted({b.y for b in layer})
        print('  图层组按钮 %d 个(可见 %d), y=%s' % (len(layer), len(vis), ys))
        print('  地图区高度 = %d; 按钮底边 = %d  => %s' % (
            sim.map_height, max(b.rect.bottom for b in layer),
            '在地图区内 OK' if max(b.rect.bottom for b in layer) <= sim.map_height
            else '**越到功能栏里了**'))
        print('  按钮 x 右缘最大 = %d (屏宽 %d)' % (
            max(b.rect.right for b in layer), W))
    else:
        print('  (该模式没有图层组按钮 —— 正常)')
    # 出图
    try:
        sim.draw(screen)
    except Exception as e:                                       # noqa: BLE001
        print('  render 跳过: %s: %s' % (type(e).__name__, e))
    out = 'G:/tmp/ty4out/panel_%s.png' % tag
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pygame.image.save(screen, out)
    print('  -> %s' % out)

print()
print('RESULT done')
