# py/input_handler.py
"""长按检测和输入事件处理。"""
from __future__ import annotations
import math
import pygame
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ty_sim import TySim


_LONG_PRESS_DELAY_MS = 200
_LONG_PRESS_DEAD_ZONE = 10


class InputHandler:
    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self._down = False
        self._down_time = 0
        self._down_pos = (0, 0)
        self._triggered = False

    def handle_event(self, e: pygame.event.Event) -> None:
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self._down = True
            self._down_time = pygame.time.get_ticks()
            self._down_pos = e.pos
            self._triggered = False
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._down = False
            self._triggered = False
        elif e.type in (pygame.WINDOWFOCUSLOST, pygame.ACTIVEEVENT):
            # R3-7: 失焦时 MOUSEBUTTONUP 可能丢失,复位长按状态
            # 防止切回窗口后误触发 on_long_press(误弹点编辑对话框)
            if e.type == pygame.WINDOWFOCUSLOST or not e.gain:
                self._down = False
                self._triggered = False

    def update(self, ct: int) -> None:
        if self.sim.dialog_mgr.any_active():
            # 对话框打开期间不进行长按判定。同时清掉按下状态:
            # 若点击打开了对话框,其 MOUSEBUTTONUP 会被对话框拦截而不到达本处理器,
            # 不清空会在对话框关闭后误触发 on_long_press。
            self._down = False
            self._triggered = False
            return
        if self._down and not self._triggered and ct - self._down_time >= _LONG_PRESS_DELAY_MS:
            mx, my = pygame.mouse.get_pos()
            if math.hypot(mx - self._down_pos[0], my - self._down_pos[1]) < _LONG_PRESS_DEAD_ZONE:
                # 使用按下时的坐标,避免按住期间鼠标在死区内漂移导致弹框错位
                self.sim.on_long_press(*self._down_pos)
            # 一旦判定(无论是否触发)即视为已处理,防止拖出死区又移回时重复触发
            self._triggered = True
