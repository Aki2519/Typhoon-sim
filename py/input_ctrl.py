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
        if e.key == pygame.K_r and (e.mod & pygame.KMOD_CTRL):
            self.sim.reload_typhoons()
            return True
        if self.sim.md == self.sim.MODE_EDIT and self.sim.edit_typhoon:
            if e.key == pygame.K_z and (e.mod & pygame.KMOD_CTRL):
                self.sim._undo_edit()
                return True
            if e.key == pygame.K_y and (e.mod & pygame.KMOD_CTRL):
                self.sim._redo_edit()
                return True
        handler = self.commands.get(e.key)
        if handler:
            return handler()
        return False
