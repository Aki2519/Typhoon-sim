# py/time_jump.py
"""时间跳跃对话框（风季模式）。"""
from __future__ import annotations

import pygame
from datetime import datetime
from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED,
    TIME_JUMP_WIDTH, TIME_JUMP_HEIGHT, DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
)
from .input_field import InputField
from .dialog_base import DraggableDialog


class TimeJump(DraggableDialog):
    def __init__(self, s):
        super().__init__(s)
        self.fields: list[InputField] = []
        self.title = rt(f_m, "时间跳跃 (风季模式)", TXT)
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))
        self._hint_light = rt(f_s, "使用Tab切换字段，Enter确认，ESC取消", TXT)
        self._hint_dark = rt(f_s, "使用Tab切换字段，Enter确认，ESC取消", SETTINGS_TEXT_DIM)
        self._quick_rects = {}
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
            f = InputField(r, max_length=4, dark=self.dark_mode)
            f.set_text(dv)
            self.fields.append(f)
        self.fields[0].activate()
        self.dragging = False

    def deactivate(self):
        for f in self.fields:
            f.deactivate()
        self.fields.clear()
        super().deactivate()
        self.dragging = False

    def draw(self, surface):
        if not self.active:
            return
        if self.dark_mode:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, self.dialog_rect)
            self.draw_dark_title(surface, "时间跳跃 (风季模式)", self.dialog_rect)
            hint_color = SETTINGS_TEXT_DIM
            label_color = SETTINGS_TEXT_LIGHT
        else:
            self.draw_background(surface, self.dialog_rect)
            self.draw_title(surface, self.title, self.dialog_rect, y_offset=20)
            hint_color = TXT
            label_color = TXT
        for f in self.fields:
            f.draw(surface)
        surface.blit(self._hint_dark if self.dark_mode else self._hint_light,
                     (self.dialog_rect.x + 50, self.dialog_rect.y + 310))
        # 标签
        for i, label in enumerate(['年份:', '月份:', '日期:', '小时:']):
            lb = rt(f_s, label, label_color)
            surface.blit(lb, (self.dialog_rect.x + 30, self.dialog_rect.y + 80 + i * 45 + 2))
        # 快捷预填按钮(P1-12): 季节开始/结束(只预填, 不直接跳转)
        quick = [('季节开始', 0), ('季节结束', 1)]
        for name, k in quick:
            r = pygame.Rect(self.dialog_rect.x + 30 + k * 110, self.dialog_rect.y + 250, 100, 26)
            if self.dark_mode:
                self.draw_dark_button(surface, r, name)
            else:
                self.draw_button(surface, (r.x, r.y, r.w, r.h),
                                 rt(f_s, name, TXT), BUTTON_BORDER)
            self._quick_rects[k] = r
        if self.dark_mode:
            self.draw_dark_button(surface, pygame.Rect(self.dialog_rect.x + 100, self.dialog_rect.y + 340, 80, 30), "确认", accent=True)
            self.draw_dark_button(surface, pygame.Rect(self.dialog_rect.x + 220, self.dialog_rect.y + 340, 80, 30), "取消")
        else:
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
            # 快捷预填按钮
            for k, r in getattr(self, '_quick_rects', {}).items():
                if r.collidepoint(x, y):
                    self._fill_quick(k)
                    return True
            if pygame.Rect(self.dialog_rect.x + 100, self.dialog_rect.y + 340, 80, 30).collidepoint(x, y):
                if self._jump():
                    self.deactivate()
                return True
            if pygame.Rect(self.dialog_rect.x + 220, self.dialog_rect.y + 340, 80, 30).collidepoint(x, y):
                self.deactivate()
                return True
            for f in self.fields:
                if f.rect.collidepoint(e.pos):
                    for g in self.fields:
                        g.deactivate()
                    f.activate_at(e.pos[0])
                    return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_RETURN:
                if self._jump():
                    self.deactivate()
                return True
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

    def _fill_fields(self, y: int, m: int, d: int, h: int) -> None:
        for f, v in zip(self.fields, (str(y), f"{m:02d}", f"{d:02d}", f"{h:02d}")):
            f.set_text(v)
            f.deactivate()

    def _fill_quick(self, k: int) -> None:
        south = getattr(self.sim, 'hemisphere', 'north') == 'south'
        sty = int(getattr(self.sim, 'sty', self.sim.sy) or self.sim.sy)
        edy = int(getattr(self.sim, 'edy', self.sim.sy) or self.sim.sy)
        if k == 0:      # 季节开始
            self._fill_fields(sty, 7 if south else 1, 1, 0)
        elif k == 1:    # 季节结束
            self._fill_fields(edy, 6 if south else 12, 30 if south else 31, 23)

    def _jump(self):
        try:
            y, m, d, h = (int(f.get_text()) for f in self.fields)
        except ValueError:
            self.sim.show_error("请输入有效的数字")
            return False
        if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= h <= 23):
            self.sim.show_error("日期或时间超出范围")
            return False
        # 年份钳制: 只允许在风季数据年代区间(绝对兜底 1900-2099,按数据扩展)
        # 内跳转,避免跳到无数据的极远年代导致时钟/ACE 累计状态失真(R4)
        lo, hi = 1900, 2099
        sty = getattr(self.sim, 'sty', None)
        edy = getattr(self.sim, 'edy', None)
        if isinstance(sty, int) and isinstance(edy, int):
            # 仅在数据年代区间 [sty, edy] 内跳转(与注释一致), 1900/2099 仅作无数据时的兜底
            lo = max(lo, sty)
            hi = min(hi, edy)
        if y < lo:
            y = lo
        elif y > hi:
            y = hi
        try:
            target = datetime(y, m, d, h)
        except ValueError:
            self.sim.show_error("无效的日期")
            return False

        if hasattr(self.sim, 'season_ctrl'):
            self.sim.season_ctrl.jump_to(target)
            self.sim._sync_season_state()

        chart = getattr(getattr(self.sim, 'dialog_mgr', None), 'ace_chart', None)
        if chart and chart.active:
            chart.needs_update = True
        return True