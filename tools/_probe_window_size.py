# -*- coding: utf-8 -*-
"""按**应用的真实方式**开窗, 量系统实际给的客户区 + 窗口位置。

与应用 main.py 一致: 先 SetProcessDpiAwareness(1), 再 set_mode。
同时报出窗口在屏幕上的位置, 以判断"窗口是否存在但跑到屏幕外/被裁"。
全部用**物理像素**(GetSystemMetrics 在 DPI aware 进程下就是物理值)。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

os.environ.pop('SDL_VIDEODRIVER', None)
os.environ.pop('SDL_AUDIODRIVER', None)

# ── 1. 与应用一致: 开 DPI 感知 ──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    dpi_mode = 'SetProcessDpiAwareness(1)'
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        dpi_mode = 'SetProcessDPIAware()'
    except Exception:
        dpi_mode = '失败'

import pygame  # noqa: E402

TITLE = '__app_like_probe__'


def screen_hw():
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def work_area():
    r = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def window_state():
    hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
    if not hwnd:
        return None
    cr, wr = wt.RECT(), wt.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(cr))
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(wr))
    return {'client': (cr.right, cr.bottom),
            'win_rect': (wr.left, wr.top, wr.right, wr.bottom),
            'outer': (wr.right - wr.left, wr.bottom - wr.top),
            'onscreen': wr.left >= 0 and wr.top >= 0 and wr.right <= screen_hw()[0]
                        and wr.bottom <= screen_hw()[1]}


def main():
    pygame.init()
    print('DPI 模式: %s' % dpi_mode)
    print('屏幕(物理): %dx%d' % screen_hw())
    print('工作区(物理): 左上%s 尺寸%dx%d' % (work_area()[:2], work_area()[2], work_area()[3]))
    print()
    pygame.display.set_mode((640, 480), pygame.RESIZABLE, vsync=0)
    pygame.display.set_caption(TITLE)
    pygame.event.pump()
    print('%-14s %-14s %-14s %-26s %-8s %s' % (
        '请求客户区', '实得客户区', '实得外框', '窗口屏幕矩形(l,t,r,b)', 'pysurface', '完全在屏内'))
    for w, h in ((2560, 1540), (2560, 1440), (1600, 900), (1200, 700)):
        pygame.display.set_mode((w, h), pygame.RESIZABLE, vsync=0)
        for _ in range(8):
            pygame.event.pump()
            time.sleep(0.04)
        st = window_state()
        surf = pygame.display.get_surface().get_size()
        if st is None:
            print('%-14s 拿不到句柄' % ('%dx%d' % (w, h)))
            continue
        print('%-14s %-14s %-14s %-26s %-8s %s' % (
            '%dx%d' % (w, h),
            '%dx%d' % st['client'], '%dx%d' % st['outer'],
            str(st['win_rect']), '%dx%d' % surf,
            '是' if st['onscreen'] else '**否(超出屏幕)**'))
    pygame.quit()


if __name__ == '__main__':
    main()
