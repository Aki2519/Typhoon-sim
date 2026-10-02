# -*- coding: utf-8 -*-
"""查询**正在运行的**应用窗口的真实状态(不启动、不打扰任何东西)。

回答的问题: 应用以为自己多大 vs 系统实际给了多大 vs 窗口在屏幕上的哪儿。
用法: 先把应用开着, 再跑这个脚本。
"""
import ctypes
import ctypes.wintypes as wt
import os

os.environ.pop('SDL_VIDEODRIVER', None)
os.environ.pop('SDL_AUDIODRIVER', None)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

TITLE = '台风路径模拟系统'
u = ctypes.windll.user32


def main():
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    r = wt.RECT()
    u.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    work = (r.left, r.top, r.right - r.left, r.bottom - r.top)
    print('屏幕(物理)      : %dx%d' % (sw, sh))
    print('工作区(物理)    : 左上(%d,%d) 尺寸%dx%d' % work)
    print()

    # 按标题枚举(标题可能有后缀, 用前缀匹配)
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if TITLE in buf.value and u.IsWindowVisible(hwnd):
            found.append((hwnd, buf.value))
        return True

    u.EnumWindows(_cb, 0)
    if not found:
        print('没找到标题含 %r 的可见窗口 —— 应用没在运行?' % TITLE)
        return
    for hwnd, title in found:
        cr, wr = wt.RECT(), wt.RECT()
        u.GetClientRect(hwnd, ctypes.byref(cr))
        u.GetWindowRect(hwnd, ctypes.byref(wr))
        cw, ch = cr.right, cr.bottom
        ow, oh = wr.right - wr.left, wr.bottom - wr.top
        onscreen = (wr.left >= 0 and wr.top >= 0
                    and wr.right <= sw and wr.bottom <= sh)
        fits_work = (ow <= work[2] and oh <= work[3])
        print('窗口: %r' % title)
        print('  客户区(系统实际给) : %dx%d' % (cw, ch))
        print('  外框尺寸          : %dx%d' % (ow, oh))
        print('  屏幕位置          : 左上(%d,%d) 右下(%d,%d)'
              % (wr.left, wr.top, wr.right, wr.bottom))
        print('  完全在屏幕内      : %s' % ('是' if onscreen else '**否 —— 有部分在屏幕外**'))
        print('  外框放得进工作区  : %s (%dx%d vs %dx%d)'
              % ('是' if fits_work else '**否**', ow, oh, work[2], work[3]))
        print('  => 地图区高度     : %d  (客户区高 - 功能栏100)' % (ch - 100))
        if not onscreen:
            print('     ⚠ 窗口伸出屏幕: 右边超出 %d px, 下边超出 %d px'
                  % (max(0, wr.right - sw), max(0, wr.bottom - sh)))
        print()


if __name__ == '__main__':
    main()
