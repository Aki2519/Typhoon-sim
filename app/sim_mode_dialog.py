# app/sim_mode_dialog.py
"""F3: 模态手动覆盖对话框。

公开的模态索引(PSL)滞后约 1 个月 —— 模拟"今天"时只能靠 AR 外推, 符号都可能
反(实测 2026-09 被外推成偏拉尼娜, EPAC 赤道因此比实况冷 5°C)。这个面板让你
直接填当前实测的 ONI/PDO/…, 立刻修正。

值按**标准化 σ**理解(与模态表同口径): ONI +2.5 = 强厄尔尼诺, -2.0 = 强拉尼娜。
留空 = 该项走查表/AR。

用法: 模拟设置面板 → 「模态覆盖」行
"""
from __future__ import annotations

import pygame

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED,
    DIALOG_TITLE_BAR_HEIGHT, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
)
from .input_field import InputField
from .dialog_base import DraggableDialog

_W, _H = 480, 520
#: (键名, 中文说明) —— 与 modes_index.npz 的 names 对齐
MODES = (
    ('oni', 'ENSO(ONI)'), ('nino34', 'Niño3.4'), ('pdo', 'PDO'),
    ('amo', 'AMO'), ('nao', 'NAO'), ('dmi', 'IOD(DMI)'),
    ('atl3', '大西洋Niño'), ('pmm', 'PMM'), ('npgo', 'NPGO'),
    ('aao', 'SAM/AAO'), ('qbo', 'QBO'), ('tni', 'TNI'),
)
_COLS = 3
_ROW_H = 42


class SimModeDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.fields = {}
        self.title = rt(f_m, "模态手动覆盖", TXT)
        self._hint = rt(f_s, "留空 = 用查表/AR;  Enter 确认  ESC 取消", TXT)
        self._hint_dark = rt(f_s, "留空 = 用查表/AR;  Enter 确认  ESC 取消",
                             SETTINGS_TEXT_DIM)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

    def activate(self):
        super().activate()
        dx = (self.sim.screen_width - _W) // 2
        dy = (self.sim.screen_height - _H) // 2
        self.dialog_rect = pygame.Rect(dx, dy, _W, _H)
        self.bg_rect = self.dialog_rect
        cur = getattr(self.sim, 'sim_mode_override', {}) or {}
        self.fields = {}
        for i, (k, _lab) in enumerate(MODES):
            r = self._field_rect(i)
            fld = InputField(r, max_length=7, dark=self.dark_mode)
            if k in cur:
                fld.set_text('%+.2f' % float(cur[k]))
            self.fields[k] = fld
        self.dragging = False

    def deactivate(self):
        for f in self.fields.values():
            f.deactivate()
        self.fields = {}
        super().deactivate()
        self.dragging = False

    def _field_rect(self, i):
        col, row = i % _COLS, i // _COLS
        return pygame.Rect(self.dialog_rect.x + 24 + col * 150,
                           self.dialog_rect.y + 58 + row * _ROW_H, 78, 24)

    def draw(self, surface):
        if not self.active:
            return
        r = self.dialog_rect
        if self.dark_mode:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
            self.draw_dark_title(surface, "模态手动覆盖", r)
            label_color = SETTINGS_TEXT_LIGHT
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=20)
            label_color = TXT
        for i, (k, lab) in enumerate(MODES):
            fld = self.fields.get(k)
            if fld is None:
                continue
            fld.rect = self._field_rect(i)
            fld.draw(surface)
            lb = rt(f_s, lab, label_color)
            surface.blit(lb, (self._field_rect(i).x, self._field_rect(i).y - 17))
        surface.blit(self._hint_dark if self.dark_mode else self._hint,
                     (r.x + 24, r.y + _H - 108))
        # 快捷: 清除 / 恢复
        self._q = {}
        for k, name in ((0, '全部清除'), (1, '恢复查表')):
            br = pygame.Rect(r.x + 24 + k * 130, r.y + _H - 82, 118, 26)
            if self.dark_mode:
                self.draw_dark_button(surface, br, name)
            else:
                self.draw_button(surface, (br.x, br.y, br.w, br.h),
                                 rt(f_s, name, TXT), BUTTON_BORDER)
            self._q[k] = br
        ok = pygame.Rect(r.x + 130, r.y + _H - 44, 84, 30)
        no = pygame.Rect(r.x + 260, r.y + _H - 44, 84, 30)
        if self.dark_mode:
            self.draw_dark_button(surface, ok, "确认", accent=True)
            self.draw_dark_button(surface, no, "取消")
        else:
            self.draw_button(surface, (ok.x, ok.y, ok.w, ok.h),
                             rt(f_s, "确认", (255, 255, 255)), BUTTON_BORDER)
            self.draw_button(surface, (no.x, no.y, no.w, no.h),
                             rt(f_s, "取消", (255, 255, 255)), BUTTON_DISABLED)
        self._ok, self._no = ok, no

    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        r = self.dialog_rect
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            for k, br in getattr(self, '_q', {}).items():
                if br.collidepoint(x, y):
                    for fld in self.fields.values():
                        fld.set_text('')
                    if k == 0:
                        self.sim._sim_set_mode_override({})
                    return True
            if self._ok.collidepoint(x, y):
                self._apply()
                self.deactivate()
                return True
            if self._no.collidepoint(x, y):
                self.deactivate()
                return True
            for k, fld in self.fields.items():
                if fld.rect.collidepoint(e.pos):
                    for g in self.fields.values():
                        g.deactivate()
                    fld.activate_at(e.pos[0])
                    return True
            if r.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_RETURN:
                self._apply()
                self.deactivate()
                return True
            if e.key == pygame.K_TAB:
                keys = list(self.fields)
                idx = next((i for i, k in enumerate(keys)
                            if self.fields[k].active), -1)
                d = -1 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                nxt = (idx + d) % len(keys) if idx != -1 else 0
                if idx != -1:
                    self.fields[keys[idx]].deactivate()
                self.fields[keys[nxt]].activate()
                return True
        for fld in self.fields.values():
            if fld.handle_event(e):
                return True
        return False

    def _apply(self):
        d = {}
        for k, fld in self.fields.items():
            t = (fld.get_text() or '').strip()
            if not t:
                continue
            try:
                d[k] = float(t)
            except ValueError:
                continue
        self.sim._sim_set_mode_override(d)
