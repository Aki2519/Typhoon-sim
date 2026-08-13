# py/input_ctrl.py
"""输入控制器：键盘快捷键命令表路由。"""
from __future__ import annotations

import pygame
from typing import Callable, Dict


class InputController:
    """管理键盘输入，通过命令表路由到 TySim 方法。"""

    def __init__(self, sim):
        self.sim = sim
        self.commands: Dict[int, Callable] = {}
        self._build_commands()

    def _build_commands(self) -> None:
        sim = self.sim
        self.commands = {
            pygame.K_h:             sim.switch_mode,
            pygame.K_g:             sim._key_g,
            pygame.K_o:             sim._key_o,
            pygame.K_s:             sim._key_s,
            pygame.K_t:             sim._key_t,
            pygame.K_x:             sim._key_x,
            pygame.K_LEFT:          sim._key_left,
            pygame.K_RIGHT:         sim._key_right,
            pygame.K_PLUS:          sim._key_plus,
            pygame.K_EQUALS:        sim._key_plus,
            pygame.K_MINUS:         sim._key_minus,
            pygame.K_SPACE:         sim._key_space,
            pygame.K_F1:            sim._key_f1,
            pygame.K_F12:           sim._key_f12,
            pygame.K_i:             sim._key_i,
            pygame.K_LEFTBRACKET:   sim._key_left_bracket,
            pygame.K_RIGHTBRACKET:  sim._key_right_bracket,
            pygame.K_k:             sim._key_k,
            pygame.K_r:             sim._key_r,
            pygame.K_p:             sim._key_p,
            pygame.K_j:             sim._key_j,
        }

    def handle_keydown(self, e: pygame.event.Event) -> bool:
        if e.key == pygame.K_r and (e.mod & pygame.KMOD_CTRL) \
                and not (e.mod & (pygame.KMOD_ALT | pygame.KMOD_SHIFT | pygame.KMOD_META)):
            self.sim.reload_typhoons()
            return True
        if self.sim.md == self.sim.MODE_EDIT and self.sim.edit_typhoon:
            # 修饰键精确匹配:排除 Alt/Shift/Meta,避免 Ctrl+Alt+Z 等组合误触发
            ctrl = (e.mod & pygame.KMOD_CTRL) and not (e.mod & (pygame.KMOD_ALT | pygame.KMOD_SHIFT | pygame.KMOD_META))
            alt = (e.mod & pygame.KMOD_ALT) and not (e.mod & (pygame.KMOD_CTRL | pygame.KMOD_SHIFT | pygame.KMOD_META))
            if ctrl and e.key == pygame.K_z:
                self.sim._undo_edit()
                return True
            if ctrl and e.key == pygame.K_y:
                self.sim._redo_edit()
                return True
            # 切换选中报点(Alt+左右)
            if alt:
                if e.key == pygame.K_LEFT:
                    self.sim._select_edit_point(-1)
                    return True
                if e.key == pygame.K_RIGHT:
                    self.sim._select_edit_point(1)
                    return True
            # 按吸附步长微调选中报点(Ctrl+方向键)
            if ctrl:
                dx = dy = 0
                if e.key == pygame.K_LEFT:
                    dx = -1
                elif e.key == pygame.K_RIGHT:
                    dx = 1
                elif e.key == pygame.K_UP:
                    dy = 1
                elif e.key == pygame.K_DOWN:
                    dy = -1
                if dx or dy:
                    self.sim._nudge_edit_point(dx, dy)
                    return True
            # 删除选中报点(Del/退格);无选中时不消费按键
            if e.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
                sel = self.sim._edit_selected_point
                if sel is not None:
                    self.sim._delete_edit_point(sel)
                    return True
        if e.key == pygame.K_F3:
            self.sim.show_edit_point_labels = not self.sim.show_edit_point_labels
            return True
        handler = self.commands.get(e.key)
        if handler:
            return handler()
        return False
