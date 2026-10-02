# -*- coding: utf-8 -*-
"""窗口尺寸/位置与**操作系统实际状态**对账。

为什么需要这个:
  用户在设置里把窗口改成 2560x1540, 窗口"一点没动"。实测根因有两条, 都不是 DPI:

  1) `pygame.display.set_mode(size)` **只改大小, 不改位置**。
     窗口左上角固定在原地, 于是往**右下**长:
        客户区 2560x900  ->  外框 2584x956   位置 (-12, 306)   底边 1262  可见
        客户区 2560x1540 ->  外框 2584x1596  位置 (-12, 306)   底边 1902  超出屏幕 1600
     => 底部约 300px 长到屏幕外, **用户看不到增长**, 于是以为"没生效"。
     而尺寸其实**已经生效**了(客户区 1540 = 地图区 1440)。

  2) `set_mode` 对"放不进工作区"的尺寸**不报错**, 应用于是**相信**自己就是请求值,
     屏幕上却只有系统实际给的那一块 => 内容被静默切掉。

  => 每次改完尺寸都要: ① 读系统实际给的客户区; ② 把窗口挪回屏内。

⚠ 轮次 2026-10-01 复核加固(rev-ui):
  · `FindWindowW` 按**标题**查窗口是**跨进程**的 —— 多开两个实例(标题相同)会命中
    任意一个。现在用 `_own_window()`(GetWindowThreadProcessId 比对本进程 pid)
    把"不是我的窗口"一律当成查不到 ⇒ 返回 None(no-op), 既不会误读别人的客户区,
    更不会**去移动别人的窗口**(`keep_window_on_screen(title=None)` 走的是
    GetForegroundWindow, 前台窗口完全可能是别的程序)。
  · ctypes **原型声明**: 不声明时 `FindWindowW` 的 restype 默认是 `c_long`(32 位,
    实测), 而 HWND 在 64 位下是**指针** —— 句柄可能被截断/变负。现在用**私有**
    `WinDLL('user32')` 显式声明 argtypes/restype(私有实例 ⇒ 不改全局
    `ctypes.windll.user32` 的原型, 不干扰 main.py 里 `_maximize_window` 的用法)。
  · 已知限制(未改): `work_area_size()` 取的是**主显示器**的工作区
    (SPI_GETWORKAREA), 所以把窗口放在第二块显示器上时, 本函数会把它夹回主屏范围。
    正确的做法是 `MonitorFromWindow` + `GetMonitorInfoW`(按窗口所在显示器取工作区);
    这里没有多显示器环境可验证, 不冒险改。
"""
from __future__ import annotations

import os
import sys

__all__ = ['actual_client_size', 'work_area_size', 'keep_window_on_screen']

#: 私有 user32 实例(带原型声明); 避免改到全局 `ctypes.windll.user32`。
_USER32 = [None]


def _user32():
    """取 user32 并**声明函数原型**(仅 Windows 可用; 失败抛异常由调用方兜)。"""
    if _USER32[0] is None:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u.FindWindowW.restype = wt.HWND
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = wt.HWND
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetClientRect.restype = wt.BOOL
        u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetWindowRect.restype = wt.BOOL
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = wt.BOOL
        u.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p,
                                            wt.UINT]
        u.SystemParametersInfoW.restype = wt.BOOL
        _USER32[0] = u
    return _USER32[0]


def _own_window(hwnd) -> bool:
    """hwnd 是不是**本进程**的窗口。

    `FindWindowW` 只按标题找, 跨进程 —— 多实例时可能拿到另一个实例的窗口;
    `GetForegroundWindow` 更可能拿到别的程序。两种都要挡掉。
    """
    if not hwnd:
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt
        pid = wt.DWORD(0)
        _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def actual_client_size(title: str):
    """按窗口标题查到窗口, 返回**操作系统实际给的客户区尺寸** (w, h)。

    非 Windows / 查不到窗口 / 不是本进程的窗口 / ctypes 不可用时返回 None,
    调用方应把它当作"无法对账"而不是"尺寸为 0"。
    """
    if not sys.platform.startswith('win'):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = _user32()
        hwnd = u.FindWindowW(None, title)
        if not _own_window(hwnd):
            return None
        rect = wt.RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        w, h = int(rect.right), int(rect.bottom)
        if w > 0 and h > 0:
            return (w, h)
    except Exception:
        return None
    return None


def work_area_size():
    """可放置区域的尺寸 (w, h)。

    Windows: 任务栏之外的**工作区**(SPI_GETWORKAREA); 取不到则退回桌面尺寸。
    其它平台: pygame 报的桌面尺寸。

    ⚠ 这是**主显示器**的工作区 —— 多显示器下不等同于"窗口所在显示器"的工作区。
    """
    if sys.platform.startswith('win'):
        try:
            import ctypes
            import ctypes.wintypes as wt
            r = wt.RECT()
            if _user32().SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                w, h = int(r.right - r.left), int(r.bottom - r.top)
                if w > 0 and h > 0:
                    return (w, h)
        except Exception:
            pass
    try:
        import pygame
        sizes = pygame.display.get_desktop_sizes()
        if sizes:
            return (int(sizes[0][0]), int(sizes[0][1]))
    except Exception:
        pass
    return None


def keep_window_on_screen(title: str = None) -> tuple | None:
    """把窗口挪回屏内(装不下时贴左上角), 返回新的**外框**左上角 (x, y) 或 None。

     保留窗口左上角不动 —— 窗口变大时右下角会跑到屏幕外,
    用户看不到变化(实测: 客户区 1540 的窗口底边到 1902, 屏幕只有 1600)。

    ⚠ 必须用 `SetWindowPos` 按**外框**移动, 不能用 pygame 的
      `pygame.display.set_window_position`: 后者是**客户区**坐标, 与 `SetWindowPos`
      差一个边框偏移 —— 实测传 (0,0) 会落到外框 (-12,-44), 把**标题栏推出屏幕**。

    `title=None` 时退到 `GetForegroundWindow()`; 但**只动本进程自己的窗口**
    (见 `_own_window`), 否则会把当前前台的其它程序窗口搬走。
    """
    if not sys.platform.startswith('win'):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = _user32()
        hwnd = u.FindWindowW(None, title) if title else u.GetForegroundWindow()
        if not _own_window(hwnd):
            return None
        wr = wt.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(wr)):
            return None
        ow, oh = wr.right - wr.left, wr.bottom - wr.top
        area = work_area_size()
        if not area:
            return None
        aw, ah = area
        # 装不下就贴 0(此时必然要盖住任务栏, 属物理限制); 装得下就夹进工作区
        nx = max(0, min(wr.left, max(0, aw - ow)))
        ny = max(0, min(wr.top, max(0, ah - oh)))
        if (nx, ny) != (wr.left, wr.top):
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            u.SetWindowPos(hwnd, 0, nx, ny, 0, 0,
                           SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            return (nx, ny)
    except Exception:
        return None
    return None
