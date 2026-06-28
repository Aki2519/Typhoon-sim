# py/time_jump.py
"""时间跳跃对话框（台风季模式）。"""
from __future__ import annotations

import pygame
from datetime import datetime
from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED,
    TIME_JUMP_WIDTH, TIME_JUMP_HEIGHT, DIALOG_TITLE_BAR_HEIGHT,
)
from .input_field import InputField
from .dialog_base import DraggableDialog


class TimeJump(DraggableDialog):
    def __init__(self, s):
        super().__init__(s)
        self.fields: list[InputField] = []
        self.title = rt(f_m, "时间跳跃 (台风季模式)", TXT)
        self.hint = rt(f_s, "使用Tab切换字段，Enter确认，ESC取消", TXT)
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

    def activate(self):
        super().activate()
        dw, dh = TIME_JUMP_WIDTH, TIME_JUMP_HEIGHT
        dx = (self.sim.screen_width - dw) // 2
        dy = (self.sim.screen_height - dh) // 2
        self.dialog_rect = pygame.Rect(dx, dy, dw, dh)
        self.bg_rect = self.dialog_rect

        labels = ['年份:', '月份:', '日期:', '小时:']
        defaults = [str(self.sim.sy), self.sim.st[0:2], self.sim.st[2:4], self.sim.st[4:6]]
        self.fields = []
        for i, (label, dv) in enumerate(zip(labels, defaults)):
            r = (dx + 120, dy + 80 + i * 45, 100, 24)
            f = InputField(r, label=label, max_length=4, validator=str.isdigit)
            f.set_text(dv)
            self.fields.append(f)
        self.fields[0].activate()
        self.dragging = False

    def deactivate(self):
        super().deactivate()
        self.fields.clear()
        self.dragging = False

    def draw(self, surface):
        if not self.active:
            return
        # 不再绘制黑色遮罩
        self.draw_background(surface, self.dialog_rect)
        self.draw_title(surface, self.title, self.dialog_rect, y_offset=20)
        for f in self.fields:
            f.draw(surface)
        surface.blit(self.hint, (self.dialog_rect.x + 50, self.dialog_rect.y + 310))
        self.draw_button(surface, (self.dialog_rect.x + 100, self.dialog_rect.y + 340, 80, 30),
                         self.confirm_text, BUTTON_BORDER)
        self.draw_button(surface, (self.dialog_rect.x + 220, self.dialog_rect.y + 340, 80, 30),
                         self.cancel_text, BUTTON_DISABLED)

    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            for i, f in enumerate(self.fields):
                f.rect.x = self.dialog_rect.x + 120
                f.rect.y = self.dialog_rect.y + 80 + i * 45
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if pygame.Rect(self.dialog_rect.x + 100, self.dialog_rect.y + 340, 80, 30).collidepoint(x, y):
                self._jump()
                return self.deactivate()
            if pygame.Rect(self.dialog_rect.x + 220, self.dialog_rect.y + 340, 80, 30).collidepoint(x, y):
                return self.deactivate()
            for f in self.fields:
                if f.rect.collidepoint(e.pos):
                    for g in self.fields:
                        g.deactivate()
                    f.activate()
                    return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                return self.deactivate()
            if e.key == pygame.K_RETURN:
                self._jump()
                return self.deactivate()
            if e.key in (pygame.K_TAB, pygame.K_KP_ENTER):
                idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                delta = -1 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                nxt = (idx + delta) % len(self.fields) if idx != -1 else 0
                if idx != -1:
                    self.fields[idx].deactivate()
                self.fields[nxt].activate()
                return True

        for f in self.fields:
            if f.handle_event(e):
                return True
        return False

    def _jump(self):
        try:
            y, m, d, h = (int(f.get_text()) for f in self.fields)
        except ValueError:
            return self.sim.show_error("请输入有效的数字")
        if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= h <= 23):
            return self.sim.show_error("日期或时间超出范围")
        try:
            target = datetime(y, m, d, h)
        except ValueError:
            return self.sim.show_error("无效的日期")

        if hasattr(self.sim, 'season_ctrl'):
            self.sim.season_ctrl.jump_to(target)
            self.sim._sync_season_state()

        chart = getattr(getattr(self.sim, 'dialog_mgr', None), 'ace_chart', None)
        if chart and chart.active:
            chart._needs_update = True