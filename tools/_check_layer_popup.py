# -*- coding: utf-8 -*-
"""验证图层组已收进**默认不可见**的弹层(用户 2026-10-01: "我要录视频! 不要把按钮放在界面里!")。

要证的四件事:
  1. 模拟模式的 `control_panel_height` == CPH(不再 +34);
  2. **默认(未展开)时, 屏幕上没有任何图层组按钮** —— 这是录视频的前提;
  3. 点 Row2 的「图层」(`sim_layer`) → 弹层展开、按钮出现、且都落在屏幕内;
  4. 弹层里点一个图层按钮 → **状态真的改变**(防"看得见点不动"的安慰剂);
     再点一次「图层」→ 收起, 屏幕重新干净。
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
from app.constants import CPH                                   # noqa: E402

sim = TySim(screen)
sim.md = TySim.MODE_SIM
sim._apply_panel_height_for_mode()
cp = sim.control_panel
cp.close_layer_popup()
cp.build()

ok = []


def chk(name, cond, extra=''):
    ok.append(bool(cond))
    print('  [%s] %s %s' % ('OK ' if cond else 'FAIL', name, extra))


print('--- 1) 功能栏高度 ---')
chk('control_panel_height == CPH', sim.control_panel_height == CPH,
    '(%s)' % sim.control_panel_height)

print('--- 2) 默认屏幕上没有图层按钮(录视频前提) ---')
chk('弹层默认关闭', not cp._layer_popup_open)
chk('_layer_popup_btns 为空', not cp._layer_popup_btns,
    '(%d 个)' % len(cp._layer_popup_btns))
allkeys = [b.key for b in cp._buttons]
leaked = [k for k in allkeys
          if k.startswith(('layer_', 'envlay_')) or k in ('wind_mode', 'shear_style')]
chk('图层键没有混进 self._buttons', not leaked, str(leaked))

print('--- 3) 点「图层」展开 ---')
b = cp._btn_map.get('sim_layer_menu')
chk('Row2 有「图层」按钮且可见', b is not None and b.visible)
ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=b.rect.center, button=1)
consumed = sim.handle_event(ev)
cp = sim.control_panel                      # 重新取(property 会 build)
chk('点击被消费', consumed)
chk('弹层已打开', cp._layer_popup_open)
chk('弹层里有按钮', len(cp._layer_popup_btns) > 0,
    '(%d 个)' % len(cp._layer_popup_btns))
r = cp.layer_popup_rect()
onscreen = all(0 <= bb.rect.left and bb.rect.right <= 1360
               and 0 <= bb.rect.top and bb.rect.bottom <= sim.map_height
               for bb in cp._layer_popup_btns)
chk('弹层按钮都在屏幕内、且不越进功能栏', onscreen, '弹层 rect=%s' % (tuple(r),))

print('--- 4) 弹层里点一个图层按钮 -> 状态真的变 ---')
target = None
for bb in cp._layer_popup_btns:
    if bb.key.startswith('layer_'):
        target = bb
        break
if target is None:
    target = cp._layer_popup_btns[0]
before = tuple(sorted(sim.sim_layers or ()))
ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=target.rect.center, button=1)
consumed = sim.handle_event(ev)
after = tuple(sorted(sim.sim_layers or ()))
chk('弹层按钮的点击被消费', consumed)
chk('状态真的改变了(%s)' % target.key, before != after,
    '%s -> %s' % (before, after))

print('--- 5) 再点「图层」收起 -> 屏幕重新干净 ---')
cp = sim.control_panel
b = cp._btn_map.get('sim_layer_menu')
ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=b.rect.center, button=1)
sim.handle_event(ev)
cp = sim.control_panel
chk('弹层已收起', not cp._layer_popup_open)

print()
print('RESULT %d/%d 通过' % (sum(ok), len(ok)))
