# app/offscreen_dialog.py
"""「域外风暴」选择对话框（用户 2026-10-12 需求 1.5；2026-10-19 改版）。

功能栏右上角的「域外风暴」按钮打开它；列出当前所有**活动**风暴，逐个勾选是否
走"域外小窗"显示（写入 `ty.offscreen`，由 `app/offscreen_storm.py` 消费）。

⚠ 用户 2026-10-19 两条意见，本文件据此重写：
   1. "域外风暴仍然是白色背景" —— 原来无条件调 `draw_background`（浅色），
      暗色主题下整块发白。现在跟台风列表一样**按主题切**
      （暗色 `draw_dark_panel` + `draw_title(title_dark)` + `draw_dark_button`）。
   2. "使用与台风列表一样的 UI" —— 尺寸/行高/顶偏移/高亮色/分页/搜索框
      全部复用 `app/ty_list.py` 的那一套常量与摆法。
"""
from __future__ import annotations

import pygame

from .constants import (
    f_s, f_m, rt, TXT, LIST_HL, BUTTON_BG, DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    TY_LIST_WIDTH, TY_LIST_ITEM_HEIGHT, TY_LIST_TOP_OFFSET, TY_LIST_ROWS_PER_PAGE,
)
from .dialog_base import DraggableDialog
from .input_field import InputField

_PAD = 20
_BTN_W, _BTN_H = 96, 26


class OffscreenDialog(DraggableDialog):
    def __init__(self, sim) -> None:
        super().__init__(sim)
        self.title_text = "域外风暴"
        self.title = rt(f_m, self.title_text, TXT)
        self.title_dark = rt(f_m, self.title_text, SETTINGS_TEXT_LIGHT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._rows = []          # [(ty, name, code)]
        self._filtered = []      # 过滤后的行（与台风列表一致的"搜索"语义）
        self.page = 0
        self.rows_per_page = TY_LIST_ROWS_PER_PAGE
        self.search_text = ''
        # 搜索框（用户 2026-10-19："搜索框做"）—— 与台风列表用同一个 InputField
        self.search_field = InputField(pygame.Rect(0, 0, 180, 24), max_length=40,
                                       font=f_s, dark=self.dark_mode)
        self._all_rect = None
        self._none_rect = None
        self._close_rect = None
        self._page_l = None
        self._page_r = None

    # ── 生命周期 ──
    def activate(self):
        super().activate()
        h = TY_LIST_TOP_OFFSET + self.rows_per_page * TY_LIST_ITEM_HEIGHT + 96
        self.dialog_rect = pygame.Rect(
            max(10, (self.sim.screen_width - TY_LIST_WIDTH) // 2),
            max(20, (self.sim.screen_height - h) // 2 - 20),
            TY_LIST_WIDTH, min(h, self.sim.screen_height - 40))
        self.bg_rect = self.dialog_rect
        self.page = 0
        self.search_text = ''
        self.refresh()

    def deactivate(self):
        super().deactivate()
        self.dragging = False

    def refresh(self):
        """重建行表（新出现的风暴默认参与域外显示）。"""
        self._rows = []
        for ty in getattr(self.sim, 'tys', []):
            if not getattr(ty, 'act', False) and not getattr(ty, 'sf', False):
                continue
            # 用户 2026-10-19：已在当前洋区限制内的风暴不列出来（它本来就看得见）
            try:
                _off = getattr(self.sim, 'offscreen', None)
                if _off is not None and _off.in_area_limit(self.sim, ty):
                    continue
            except Exception:
                pass
            if getattr(ty, 'offscreen', None) is None:
                # 用户 2026-10-19："默认全不选"
                ty.offscreen = False
            try:
                name = self.sim.get_display_name(ty) or ''
            except Exception:
                name = getattr(ty, 'sname', '') or ''
            code = '%s%s' % (getattr(ty, 'basin', '') or '', getattr(ty, 'n', '') or '')
            self._rows.append((ty, name, code))
        self._apply_filter()

    def _apply_filter(self, reset_page=True):
        key = (self.search_text or '').strip().lower()
        if key:
            self._filtered = [r for r in self._rows
                              if key in r[1].lower() or key in r[2].lower()]
        else:
            self._filtered = list(self._rows)
        if reset_page:
            self.page = 0

    # ── 布局 ──
    def total_pages(self) -> int:
        return max(1, (len(self._filtered) + self.rows_per_page - 1) // self.rows_per_page)

    def _search_rect(self):
        return pygame.Rect(self.dialog_rect.x + _PAD,
                           self.dialog_rect.y + DIALOG_TITLE_BAR_HEIGHT + 6,
                           200, 24)

    def _row_rects(self):
        out = []
        start = self.page * self.rows_per_page
        for i in range(start, min(start + self.rows_per_page, len(self._filtered))):
            row = i - start
            y = (self.dialog_rect.y + TY_LIST_TOP_OFFSET
                 + row * TY_LIST_ITEM_HEIGHT + 8)
            r = pygame.Rect(self.dialog_rect.x + _PAD, y,
                            self.dialog_rect.w - 2 * _PAD, TY_LIST_ITEM_HEIGHT - 8)
            out.append((self._filtered[i], r))
        return out

    def _btn_rects(self):
        by = self.dialog_rect.bottom - 42
        x = self.dialog_rect.x + _PAD
        self._all_rect = pygame.Rect(x, by, _BTN_W, _BTN_H)
        self._none_rect = pygame.Rect(x + _BTN_W + 10, by, _BTN_W, _BTN_H)
        self._close_rect = pygame.Rect(self.dialog_rect.right - _PAD - _BTN_W, by,
                                       _BTN_W, _BTN_H)
        cy = by + _BTN_H // 2
        self._page_l = pygame.Rect(self.dialog_rect.right - _PAD - _BTN_W - 110,
                                   cy - 12, 24, 24)
        self._page_r = pygame.Rect(self.dialog_rect.right - _PAD - _BTN_W - 34,
                                   cy - 12, 24, 24)

    # ── 事件 ──
    def handle_event(self, event) -> bool:
        if not self.active:
            return False
        self.search_field.dark = self.dark_mode
        self.search_field.rect = self._search_rect()   # 命中测试要靠它（别只在 draw 里设）
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if self.search_field.handle_event(event):
            self.search_text = self.search_field.text
            self._apply_filter()
            return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_o):
                self.deactivate()
                return True
            if event.key in (pygame.K_LEFT, pygame.K_PAGEUP):
                self.page = max(0, self.page - 1)
                return True
            if event.key in (pygame.K_RIGHT, pygame.K_PAGEDOWN):
                self.page = min(self.total_pages() - 1, self.page + 1)
                return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button not in (1, 4, 5):
                return super().handle_event(event)
            mx, my = event.pos
            if event.button == 4:
                self.page = max(0, self.page - 1)
                return True
            if event.button == 5:
                self.page = min(self.total_pages() - 1, self.page + 1)
                return True
            self._btn_rects()
            if self._close_rect.collidepoint(mx, my):
                self.deactivate()
                return True
            if self._all_rect.collidepoint(mx, my):
                for ty, _n, _c in self._filtered:
                    ty.offscreen = True
                return True
            if self._none_rect.collidepoint(mx, my):
                for ty, _n, _c in self._filtered:
                    ty.offscreen = False
                return True
            if self._page_l.collidepoint(mx, my):
                self.page = max(0, self.page - 1)
                return True
            if self._page_r.collidepoint(mx, my):
                self.page = min(self.total_pages() - 1, self.page + 1)
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
        r = self.dialog_rect
        dark = self.dark_mode
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        dim = (150, 158, 176) if dark else SETTINGS_TEXT_DIM

        # ⚠ 主题感知：暗色走暗面板，浅色走浅面板（原来写死浅色 ⇒ 暗色下发白）
        if dark:
            self.draw_dark_panel(surface, r)
            self.draw_title(surface, self.title_dark, r, y_offset=15)
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=15)

        # 搜索框 + 右侧提示
        self.search_field.rect = self._search_rect()
        if not self.search_field.text and not self.search_field.active:
            surface.blit(rt(f_s, '搜索风暴…', dim),
                         (self.search_field.rect.x + 6, self.search_field.rect.y + 4))
        self.search_field.draw(surface)
        hint = rt(f_s, "勾选的会在屏幕边缘用小窗显示（最多 5 个）", dim)
        surface.blit(hint, (self.search_field.rect.right + 10,
                            self.search_field.rect.y + 4))

        mx, my = pygame.mouse.get_pos()
        for (ty, name, code), rr in self._row_rects():
            on = bool(getattr(ty, 'offscreen', False))
            hover = rr.collidepoint(mx, my)
            if hover:
                hl = pygame.Surface(rr.size, pygame.SRCALPHA)
                hl.fill((70, 105, 165, 130) if dark else LIST_HL)
                surface.blit(hl, rr.topleft)
            # 勾选框（与台风列表的行首对齐）
            box = pygame.Rect(rr.x + 8, rr.y + 18, 26, 26)
            pygame.draw.rect(surface, (30, 34, 44) if dark else (245, 247, 250), box, 0, 4)
            pygame.draw.rect(surface, (110, 130, 165) if dark else (150, 158, 172),
                             box, 1, 4)
            if on:
                pygame.draw.line(surface, (90, 205, 120), (box.x + 5, box.centery),
                                 (box.centerx - 1, box.bottom - 6), 3)
                pygame.draw.line(surface, (90, 205, 120), (box.centerx - 1, box.bottom - 6),
                                 (box.right - 5, box.y + 6), 3)
            # 两段式：编号+名字（= 台风名）/ 状态（= 台风消息）
            surface.blit(rt(f_s, '%s  %s' % (code, name), tc if on else dim),
                         (box.right + 12, rr.y + 5))
            info = '域外: %s' % ('开' if on else '关')
            try:
                # ⚠ 两个坑：
                #   1) 位置读 cpos()（返回 {'la','lo'}）；风读报点字典的 'w'
                #      键（键名见 app/ace_engine.py:35 的 w = pt['w']）。
                #   2) 还没开始的风暴 cpos() 是空的，要退回第一个报点，
                #      否则会显示成 0.0°N 0.0°E（第一版就是这个毛病）。
                pos = ty.cpos() or None
                p0 = (ty.cp() if hasattr(ty, 'cp') else None) or (
                    ty.pts[0] if getattr(ty, 'pts', None) else None)
                if pos is None and p0:
                    pos = {'la': p0.get('la'), 'lo': p0.get('lo')}
                wind = (p0 or {}).get('w', 0)
                if pos and pos.get('la') is not None:
                    # 经度要绕回 ±180（cpos 可能给 237.8 这种跨 180 的展开值）
                    la = float(pos['la'])
                    lo = float(pos['lo'])
                    lo = ((lo + 180.0) % 360.0) - 180.0
                    info = '%.1f°%s  %.1f°%s   %s kt   域外: %s' % (
                        abs(la), 'N' if la >= 0 else 'S',
                        abs(lo), 'E' if lo >= 0 else 'W',
                        wind, '开' if on else '关')
                else:
                    info = '未开始   域外: %s' % ('开' if on else '关')
            except Exception:
                pass
            surface.blit(rt(f_s, info, dim), (box.right + 12, rr.y + 30))

        if not self._filtered:
            surface.blit(rt(f_s, "（没有可显示的风暴）", dim),
                         (r.x + _PAD, r.y + TY_LIST_TOP_OFFSET + 10))

        # 分页信息 + 箭头（与台风列表一致）
        self._btn_rects()
        page_info = '第 %d/%d 页  共 %d 条' % (self.page + 1, self.total_pages(),
                                            len(self._filtered))
        surface.blit(rt(f_s, page_info, tc),
                     (r.x + _PAD, r.bottom - 66))
        for rect, left in ((self._page_l, True), (self._page_r, False)):
            if rect.w and rect.x > r.x:
                pygame.draw.polygon(
                    surface, tc,
                    ([(rect.right, rect.top), (rect.right, rect.bottom),
                      (rect.left + 4, rect.centery)] if left else
                     [(rect.left, rect.top), (rect.left, rect.bottom),
                      (rect.right - 4, rect.centery)]))

        for rect, text in ((self._all_rect, '全选'), (self._none_rect, '全不选'),
                           (self._close_rect, '关闭')):
            t = rt(f_s, text, (255, 255, 255))
            if dark:
                self.draw_dark_button(surface, rect, t)
            else:
                self.draw_button(surface, rect, t, BUTTON_BG)
