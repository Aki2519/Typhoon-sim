# -*- coding: utf-8 -*-
"""验证台风列表**页面循环切换**(用户 2026-10-01: "为什么台风列表界面不能页面循环切换?")。

原来 `_set_page` 一律夹紧(到头就停), 而 ↑/↓ 键走 `% len(...)`(会回绕) ⇒ 行为不一致。
现在 ←/→、滚轮/翻页按钮都走 `wrap=True`。

判据(全部用真实事件走 `handle_event`):
  1. 总页数 > 1(否则无从验起);
  2. 首页按 ← → 应到**末页**(回绕);
  3. 末页按 → → 应回**首页**;
  4. 滚轮在末页继续往前 → 应回首页;
  5. **输入页码跳转仍夹紧**: 输一个超范围页码不应回绕。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                                   # noqa: E402

pygame.init()
screen = pygame.display.set_mode((1200, 800))
from app.ty_sim import TySim                                    # noqa: E402

sim = TySim(screen)
sim.repo.load_typhoon_files()
dlg = sim.dialog_mgr.tl
dlg.activate()
total = dlg.get_total_pages()
print('台风列表: %d 条, 每页 %d 行 => 共 %d 页'
      % (len(sim.repo.tys), dlg.rows_per_page, total))

ok = []


def chk(name, cond, extra=''):
    ok.append(bool(cond))
    print('  [%s] %s %s' % ('OK ' if cond else 'FAIL', name, extra))


def key(k):
    return dlg.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k,
                                               mod=0, unicode=''))


if total <= 1:
    print('只有 %d 页, 无法验证回绕(把洋区过滤放宽或换一份数据)' % total)
    print('RESULT 跳过')
    sys.exit(0)

print('--- 1) 首页按 ← 应到末页 ---')
dlg._set_page(0)
chk('起点是首页', dlg.current_page == 0)
key(pygame.K_LEFT)
chk('← 回绕到末页', dlg.current_page == total - 1,
    '(page=%d, 末页=%d)' % (dlg.current_page, total - 1))

print('--- 2) 末页按 → 应回首页 ---')
key(pygame.K_RIGHT)
chk('→ 回绕到首页', dlg.current_page == 0, '(page=%d)' % dlg.current_page)

print('--- 3) 滚轮在末页继续往前应回首页 ---')
dlg._set_page(total - 1)
dlg._page_wheel_or_btn(1)
chk('滚轮回绕到首页', dlg.current_page == 0, '(page=%d)' % dlg.current_page)

print('--- 4) 输入页码跳转仍夹紧(不回绕) ---')
dlg._set_page(0)
dlg.jump_active = True
dlg.jump_input = str(total + 5)          # 超范围
dlg._do_jump()
chk('超范围页码不跳转(不回绕)', dlg.current_page == 0,
    '(page=%d)' % dlg.current_page)
dlg.jump_active = True
dlg.jump_input = str(total)
dlg._do_jump()
chk('合法页码正常跳转', dlg.current_page == total - 1,
    '(page=%d)' % dlg.current_page)

print()
print('RESULT %d/%d 通过' % (sum(ok), len(ok)))
