# app/offscreen_dialog.py
"""「域外风暴」选择对话框（用户 2026-10-12 需求 1.5）。

功能栏右上角的「域外风暴」按钮打开它；列出当前所有**活动**风暴，逐个勾选是否
走"域外小窗"显示（写入 `ty.offscreen`，由 `app/offscreen_storm.py` 消费）。
以台风列表界面为基础：一行一条，左勾选框 + 名称 + 编号 + 状态。
"""
from __future__ import annotations

import pygame

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_TEXT_DIM,
)
from .dialog_base import DraggableDialog

_W, _H = 460, 430
_ROW_H = 30
_PAD = 12


class OffscreenDialog(DraggableDialog):
    def __init__(self, sim) -> None:
        super().__init__(sim)
        self.title_text = "域外风暴"
        self.title = rt(f_m, self.title_text, TXT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._rows = []
        self._scroll = 0
        self._close_rect = None
        self._all_rect = None
        self._none_rect = None
        self._layout_valid = False

    # ── 生命周期 ──
    def activate(self):
        super().activate()
        w, h = _W, _H
        dx = max(10, self.sim.screen_width - w - 40)
        dy = max(50, (self.sim.screen_height - h) // 2)
        self.dialog_rect = pygame.Rect(dx, dy, w, h)
        self.bg_rect = self.dialog_rect
        self._scroll = 0
        self._layout_valid = False
        self.refresh()

    def deactivate(self):
        super().deactivate()
        self.dragging = False

    def refresh(self):
        """重建行表（同时给**新出现的**风暴一个默认值：默认参与域外显示）。"""
        self._rows = []
        for ty in getattr(self.sim, 'tys', []):
            if not getattr(ty, 'act', False) and not getattr(ty, 'sf', False):
                continue
            if getattr(ty, 'offscreen', None) is None:
                ty.offscreen = True
            try:
                name = self.sim.get_display_name(ty) or ''
            except Exception:
                name = getattr(ty, 'sname', '') or ''
            code = '%s%s' % (getattr(ty, 'basin', '') or '', getattr(ty, 'n', '') or '')
            self._rows.append((ty, name, code))

    # ── 布局 ──
    def _row_rects(self):
        out = []
        y = self.dialog_rect.y + DIALOG_TITLE_BAR_HEIGHT + _PAD + _ROW_H  # 表头下方
        for i, row in enumerate(self._rows):
            if i < self._scroll:
                continue
            r = pygame.Rect(self.dialog_rect.x + _PAD, y, _W - 2 * _PAD, _ROW_H - 2)
            if r.bottom > self.dialog_rect.bottom - 52:
                break
            out.append((row, r))
            y += _ROW_H
        return out

    # ── 事件 ──
    def handle_event(self, event) -> bool:
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_o):
                self.deactivate()
                return True
            if event.key == pygame.K_DOWN:
                self._scroll = min(max(0, len(self._rows) - 1), self._scroll + 1)
                return True
            if event.key == pygame.K_UP:
                self._scroll = max(0, self._scroll - 1)
                return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:
                self._scroll = max(0, self._scroll - 1)
                return True
            if event.button == 5:
                self._scroll = min(max(0, len(self._rows) - 1), self._scroll + 1)
                return True
            if event.button == 1:
                mx, my = event.pos
                if self._close_rect and self._close_rect.collidepoint(mx, my):
                    self.deactivate()
                    return True
                if self._all_rect and self._all_rect.collidepoint(mx, my):
                    for ty, _n, _c in self._rows:
                        ty.offscreen = True
                    return True
                if self._none_rect and self._none_rect.collidepoint(mx, my):
                    for ty, _n, _c in self._rows:
                        ty.offscreen = False
                    return True
                for (ty, _n, _c), r in self._row_rects():
                    if r.collidepoint(mx, my):
                        ty.offscreen = not bool(getattr(ty, 'offscreen', False))
                        return True
        return super().handle_event(event)

    # ── 绘制 ──
    def draw(self, surface) -> None:
        if not self.active:
            return
        self.draw_background(surface, self.dialog_rect)
        self.draw_title(surface, rt(f_m, self.title_text, TXT), self.dialog_rect,
                        self.title_bar_height)
        dark = self.dark_mode
        tc = (215, 225, 245) if dark else TXT
        dim = SETTINGS_TEXT_DIM

        head = rt(f_s, "勾选的风暴会在屏幕边缘用白色方框显示（同时最多 5 个）", dim)
        surface.blit(head, (self.dialog_rect.x + _PAD,
                            self.dialog_rect.y + DIALOG_TITLE_BAR_HEIGHT + 8))

        if not self._rows:
            surface.blit(rt(f_s, "（当前没有活动的风暴）", dim),
                         (self.dialog_rect.x + _PAD,
                          self.dialog_rect.y + DIALOG_TITLE_BAR_HEIGHT + 42))
        for (ty, name, code), r in self._row_rects():
            on = bool(getattr(ty, 'offscreen', False))
            if dark:
                pygame.draw.rect(surface, (30, 38, 58), r, 0, 4)
            box = pygame.Rect(r.x + 6, r.y + 6, r.h - 12, r.h - 12)
            pygame.draw.rect(surface, (200, 210, 235) if on else (90, 100, 120), box, 2, 3)
            if on:
                pygame.draw.rect(surface, (90, 200, 130),
                                 box.inflate(-6, -6), 0, 2)
            label = rt(f_s, '%s  %s' % (code, name), tc)
            surface.blit(label, (r.x + 34, r.y + (r.h - label.get_height()) // 2))

        # 底部按钮
        by = self.dialog_rect.bottom - 42
        bw, bh = 96, 30
        self._all_rect = pygame.Rect(self.dialog_rect.x + _PAD, by, bw, bh)
        self._none_rect = pygame.Rect(self.dialog_rect.x + _PAD + bw + 10, by, bw, bh)
        self._close_rect = pygame.Rect(self.dialog_rect.right - _PAD - bw, by, bw, bh)
        for rect, text in ((self._all_rect, '全选'), (self._none_rect, '全不选'),
                           (self._close_rect, '关闭')):
            hover = rect.collidepoint(pygame.mouse.get_pos())
            self.draw_button(surface, rect, rt(f_s, text, (255, 255, 255)),
                             'primary' if text != '关闭' else 'light', hover=hover)
        # 滚动提示
        rects = self._row_rects()
        if rects and len(self._rows) * _ROW_H > (rects[-1][1].bottom -
                                                 (self.dialog_rect.y +
                                                  DIALOG_TITLE_BAR_HEIGHT + _PAD + _ROW_H)):
            surface.blit(rt(f_s, '↑↓ 滚动', dim),
                         (self.dialog_rect.right - 90, self.dialog_rect.bottom - 70))
