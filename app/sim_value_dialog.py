# app/sim_value_dialog.py
"""单值输入对话框(模拟模式用): 模拟种子 / 数据年份。

模拟设置面板里有些项不是开关也不是档位, 而是一个整数(种子)或"可留空的年份"。
为这一件事写一个通用小对话框, 比在面板里塞输入框干净 —— 面板是每帧重绘的,
输入焦点/光标闪烁那套状态放在独立对话框里更好管。

用法:
    dlg = sim.dialog_mgr.sim_value
    dlg.activate(title="模拟种子", label="种子", value="20250801",
                 hint="留空 = 默认", on_ok=sim._sim_set_seed, max_length=10)
"""
from __future__ import annotations

import pygame

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED,
    DIALOG_TITLE_BAR_HEIGHT, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
)
from .input_field import InputField
from .dialog_base import DraggableDialog

_W, _H = 400, 190


class SimValueDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_text = "输入"
        self.label_text = ""
        self.hint_text = ""
        self._on_ok = None
        self._field = None
        self.title = rt(f_m, "输入", TXT)
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))
        self._hint = rt(f_s, "", TXT)
        self._hint_dark = rt(f_s, "", SETTINGS_TEXT_DIM)
        self._ok_rect = None
        self._no_rect = None
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

    # ── 生命周期 ──
    def activate(self, title="输入", label="", value="", hint="",
                 on_ok=None, max_length=10):
        super().activate()
        self.title_text = str(title)
        self.label_text = str(label)
        self.hint_text = str(hint)
        self._on_ok = on_ok
        dx = (self.sim.screen_width - _W) // 2
        dy = max(60, (self.sim.screen_height - _H) // 2)
        self.dialog_rect = pygame.Rect(dx, dy, _W, _H)
        self.bg_rect = self.dialog_rect
        self._field = InputField(self._field_rect(), max_length=int(max_length),
                                 dark=self.dark_mode)
        self._field.set_text(str(value))
        self._field.activate()
        self._hint = rt(f_s, self.hint_text, TXT)
        self._hint_dark = rt(f_s, self.hint_text, SETTINGS_TEXT_DIM)
        self.dragging = False
        self._layout_valid = False

    def deactivate(self):
        if self._field is not None:
            self._field.deactivate()
        super().deactivate()
        self.dragging = False

    def _field_rect(self) -> pygame.Rect:
        r = self.dialog_rect
        return pygame.Rect(r.x + 120, r.y + 76, 240, 26)

    # ── 绘制 ──
    def draw(self, surface):
        if not self.active:
            return
        r = self.dialog_rect
        if self.dark_mode:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
            self.draw_dark_title(surface, self.title_text, r)
            label_color = SETTINGS_TEXT_LIGHT
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, rt(f_m, self.title_text, TXT), r, y_offset=20)
            label_color = TXT
        if self._field is not None:
            self._field.rect = self._field_rect()
            self._field.draw(surface)
        surface.blit(rt(f_s, self.label_text, label_color),
                     (r.x + 30, r.y + 80))
        hint = self._hint_dark if self.dark_mode else self._hint
        surface.blit(hint, (r.x + 30, r.y + _H - 46))
        ok = pygame.Rect(r.x + 110, r.y + _H - 52, 84, 30)
        no = pygame.Rect(r.x + 220, r.y + _H - 52, 84, 30)
        if self.dark_mode:
            self.draw_dark_button(surface, ok, "确认", accent=True)
            self.draw_dark_button(surface, no, "取消")
        else:
            self.draw_button(surface, (ok.x, ok.y, ok.w, ok.h),
                             self.confirm_text, BUTTON_BORDER)
            self.draw_button(surface, (no.x, no.y, no.w, no.h),
                             self.cancel_text, BUTTON_DISABLED)
        self._ok_rect, self._no_rect = ok, no

    # ── 事件 ──
    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            if self._field is not None:
                self._field.rect = self._field_rect()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if getattr(self, '_ok_rect', None) and self._ok_rect.collidepoint(x, y):
                if self._apply():
                    self.deactivate()
                return True
            if getattr(self, '_no_rect', None) and self._no_rect.collidepoint(x, y):
                self.deactivate()
                return True
            if self._field is not None and self._field.rect.collidepoint(e.pos):
                self._field.activate_at(e.pos[0])
                return True
            if self.dialog_rect.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self._apply():
                    self.deactivate()
                return True
        if self._field is not None and self._field.handle_event(e):
            return True
        return False

    def _apply(self) -> bool:
        if self._field is None:
            return False
        txt = self._field.get_text()
        if self._on_ok is not None:
            self._on_ok(txt)
        return True
