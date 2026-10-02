# -*- coding: utf-8 -*-
"""验证 `ty4_render.py --show` 的交互式预览代码路径能完整跑通。

做法: 先用 SDL dummy 驱动初始化, 往事件队列塞一个 QUIT, 再走 main()。
这样会**完整执行**一遍: 渲染 -> 缩放到 --show 尺寸 -> 建窗 -> 取事件 ->
遇到 QUIT 退出。不进入真正的等待循环, 所以能在无头环境验证。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'tools'))

sys.argv = ['ty4_render.py',
            '--track', r'G:\气象\数据\xrq\wp\bwp152010.dat',
            '--out', r'G:\tmp\ty4out\show.png',
            '--show', '900']

import ty4_render as P                                          # noqa: E402
import pygame                                                   # noqa: E402

pygame.init()
# 把要验的**所有**事件一次性塞进队列, 然后只走一遍 main():
#   + / [ / ] / g 这些按键各触发一次"改参数 -> 重画 -> 重建窗口",
#   最后 QUIT 退出。这样一次就能验完交互路径。
for ev in (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PLUS),
           pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFTBRACKET),
           pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHTBRACKET),
           pygame.event.Event(pygame.KEYDOWN, key=pygame.K_g),
           pygame.event.Event(pygame.VIDEOEXPOSE)):
    pygame.event.post(ev)
pygame.event.post(pygame.event.Event(pygame.QUIT))
try:
    P.main()
    print('RESULT preview_ok=True  (渲染->缩放->建窗->按键重画->事件循环退出, 未抛异常)')
except SystemExit as e:
    print('RESULT preview_ok=False SystemExit code=%r' % (e.code,))
    sys.exit(1)
except Exception as e:                                          # noqa: BLE001
    print('RESULT preview_ok=False %s: %s' % (type(e).__name__, e))
    sys.exit(1)
