# -*- coding: utf-8 -*-
"""实测: 浮在地图区内的图层按钮**真的点得到**(用户 2026-10-01 的功能栏改动)。

风险点: 图层组原来在功能栏里, 现在搬到**地图区域内**。
       如果 `_handle_click` 之前有"只看功能栏范围"的守卫, 或地图的点击处理先吃掉事件,
       按钮就会变成**看得见点不着** —— 这正是本项目 UI 规范里禁止的"安慰剂"。

做法: 真建 TySim(模拟模式), 取每个图层按钮的**中心点**, 走真实事件入口
      (`handle_event` 的 MOUSEBUTTONDOWN), 断言状态真的变了。
"""
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
panel = sim.control_panel
panel.build()

print('控制面板高度 = %s (CPH 应为 100)' % sim.control_panel_height)

# 找一个可见的 layer_ 按钮和一个 envlay_ 按钮
def center(btn):
    return (btn.rect.centerx, btn.rect.centery)

def click(pos):
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1)
    return sim.handle_event(ev)

checked = 0
for key in ('layer_sst', 'layer_rain', 'envlay_ohc', 'wind_mode'):
    b = panel._btn_map.get(key)
    if b is None or not b.visible:
        print('  %-12s 不存在或不可见, 跳过' % key)
        continue
    pos = center(b)
    in_map = pos[1] < sim.map_height
    before = (tuple(sorted(sim.sim_layers or ())),
              getattr(sim, 'sim_layer', None),
              str(getattr(sim.cfg, 'sim_wind_mode', '')))
    consumed = click(pos)
    after = (tuple(sorted(sim.sim_layers or ())),
             getattr(sim, 'sim_layer', None),
             str(getattr(sim.cfg, 'sim_wind_mode', '')))
    changed = before != after
    print('  %-12s pos=%-12s 在地图区内=%-5s 事件被消费=%-5s 状态改变=%-5s'
          % (key, str(pos), in_map, consumed, changed))
    if changed:
        checked += 1

print()
print('RESULT 点击生效的按钮数 = %d' % checked)
