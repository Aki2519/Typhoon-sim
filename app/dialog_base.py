# py/dialog_base.py
"""对话框基类。"""
from __future__ import annotations

import pygame
from typing import Tuple, TYPE_CHECKING

from .constants import (
    LIST_BG, TXT, BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED, DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_DARK_BG, SETTINGS_DARK_OVERLAY, settings_accent,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_TOGGLE_ON, SETTINGS_TOGGLE_OFF,
    DIALOG_CORNER_RADIUS, DIALOG_BORDER_WIDTH,
    f_s, f_m
)

if TYPE_CHECKING:
    from .ty_sim import TySim

TITLE_FONT = f_m
CONTENT_FONT = f_s

DIALOG_PADDING = 10
BTN_PADDING_V = 5
BTN_PADDING_H = 8
BTN_RADIUS = 5

BTN_PRIMARY_NORMAL = BUTTON_BORDER
BTN_PRIMARY_HOVER = (90, 150, 210)
BTN_DISABLED = BUTTON_DISABLED
BTN_LIGHT_NORMAL = BUTTON_BG
BTN_LIGHT_HOVER = (130, 170, 220)

DIALOG_BG = LIST_BG
DIALOG_BORDER = BUTTON_BORDER


def _invalidate_dialog_count(dlg):
    mgr = getattr(getattr(dlg, 'sim', None), 'dialog_mgr', None)
    if mgr is not None:
        mgr._active_count = None


class Dialog:
    _overlay_cache: dict = {}
    _panel_cache: dict = {}

    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self.active: bool = False
        self.current_field: int = 0

    @property
    def dark_mode(self) -> bool:
        return getattr(self.sim, 'dark_mode', True)

    def activate(self, *args, **kwargs) -> None:
        self.active = True
        if hasattr(self.sim, '_dialog_stack') and self not in self.sim._dialog_stack:
            self.sim._dialog_stack.append(self)
        _invalidate_dialog_count(self)

    def deactivate(self) -> None:
        self.active = False
        if hasattr(self.sim, '_dialog_stack') and self in self.sim._dialog_stack:
            self.sim._dialog_stack.remove(self)
        _invalidate_dialog_count(self)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
        return False

    def draw(self, surface: pygame.Surface) -> None:
        pass

    def draw_dark_overlay(self, surface: pygame.Surface) -> None:
        """全屏暗色半透明遮罩。"""
        key = (self.sim.screen_width, self.sim.screen_height, SETTINGS_DARK_OVERLAY)
        ov = self._overlay_cache.get(key)
        if ov is None:
            ov = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            ov.fill(SETTINGS_DARK_OVERLAY)
            self._overlay_cache[key] = ov
            if len(self._overlay_cache) > 4:
                self._overlay_cache.pop(next(iter(self._overlay_cache)))
        surface.blit(ov, (0, 0))

    def draw_dark_panel(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """暗色圆角面板背景。"""
        key = (rect.w, rect.h, DIALOG_CORNER_RADIUS)
        panel = self._panel_cache.get(key)
        if panel is None:
            panel = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(panel, SETTINGS_DARK_BG, (0, 0, rect.w, rect.h), border_radius=DIALOG_CORNER_RADIUS)
            self._panel_cache[key] = panel
            if len(self._panel_cache) > 16:
                self._panel_cache.pop(next(iter(self._panel_cache)))
        surface.blit(panel, rect)

    def draw_dark_button(self, surface, rect, text, hover=False, accent=False):
        """暗色主题按钮。"""
        if accent:
            bg = settings_accent(True, self.sim.color_scheme)
            tc = (20, 25, 35)
        else:
            bg = SETTINGS_TOGGLE_ON if hover else SETTINGS_TOGGLE_OFF
            tc = SETTINGS_TEXT_LIGHT if hover else SETTINGS_TEXT_DIM
        pygame.draw.rect(surface, bg, rect, border_radius=6)
        if isinstance(text, str):
            ts = CONTENT_FONT.render(text, True, tc)
        else:
            ts = text
        surface.blit(ts, (rect.x + (rect.w - ts.get_width()) // 2, rect.y + (rect.h - ts.get_height()) // 2))

    def draw_dark_title(self, surface, text: str, rect: pygame.Rect, y_offset: int = 12):
        """暗色标题居中。"""
        ts = TITLE_FONT.render(text, True, SETTINGS_TEXT_LIGHT)
        surface.blit(ts, (rect.x + 20, rect.y + y_offset))

    _bg_cache: dict = {}

    def draw_background(self, surface: pygame.Surface, rect: pygame.Rect,
                        color: Tuple = DIALOG_BG,
                        border_color: Tuple = DIALOG_BORDER,
                        alpha: bool = True,
                        radius: int = DIALOG_CORNER_RADIUS) -> None:
        key = (rect.width, rect.height, color, border_color, radius)
        bg = self._bg_cache.get(key)
        if bg is None:
            bg = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, color, (0, 0, rect.width, rect.height), 0, radius)
            pygame.draw.rect(bg, border_color, (0, 0, rect.width, rect.height),
                             DIALOG_BORDER_WIDTH, radius)
            self._bg_cache[key] = bg
            if len(self._bg_cache) > 32:
                self._bg_cache.pop(next(iter(self._bg_cache)))
        surface.blit(bg, rect)

    def draw_title(self, surface: pygame.Surface, title: pygame.Surface,
                   rect: pygame.Rect, y_offset: int = 15) -> None:
        x = rect.centerx - title.get_width() // 2
        y = rect.y + y_offset
        surface.blit(title, (x, y))

    def draw_button(self, surface: pygame.Surface, rect, text_surf: pygame.Surface,
                    style='primary', enabled: bool = True,
                    hover: bool = False) -> None:
        """style 可为 'primary'/'light' 风格名，或直接传入颜色元组。"""
        if not isinstance(rect, pygame.Rect):
            rect = pygame.Rect(rect)
        if not enabled:
            color = BTN_DISABLED
        elif isinstance(style, tuple):
            color = style
        elif style == 'primary':
            color = BTN_PRIMARY_HOVER if hover else BTN_PRIMARY_NORMAL
        else:
            color = BTN_LIGHT_HOVER if hover else BTN_LIGHT_NORMAL
        pygame.draw.rect(surface, color, rect, 0, BTN_RADIUS)
        surface.blit(text_surf, (
            rect.centerx - text_surf.get_width() // 2,
            rect.centery - text_surf.get_height() // 2))

    _title_bar_cache: dict = {}

    def draw_title_bar(self, surface: pygame.Surface, rect: pygame.Rect,
                       title_text: str, title_color: Tuple = TXT,
                       title_font=TITLE_FONT) -> pygame.Rect:
        if not isinstance(rect, pygame.Rect):
            rect = pygame.Rect(rect)
        bar_rect = pygame.Rect(rect.x, rect.y, rect.width, DIALOG_TITLE_BAR_HEIGHT)
        key = (rect.width, title_text, title_color, id(title_font))
        bar_surf = self._title_bar_cache.get(key)
        if bar_surf is None:
            bar_surf = pygame.Surface((bar_rect.width, bar_rect.height), pygame.SRCALPHA)
            bar_surf.fill((0, 0, 0, 30))
            title = title_font.render(title_text, True, title_color)
            bar_surf.blit(title, (
                bar_surf.get_width() // 2 - title.get_width() // 2,
                bar_surf.get_height() // 2 - title.get_height() // 2))
            self._title_bar_cache[key] = bar_surf
            if len(self._title_bar_cache) > 32:
                self._title_bar_cache.pop(next(iter(self._title_bar_cache)))
        surface.blit(bar_surf, (bar_rect.x, bar_rect.y))
        return bar_rect


class DraggableDialog(Dialog):
    def __init__(self, sim: TySim) -> None:
        super().__init__(sim)
        self.dragging: bool = False
        self.drag_offset_x: int = 0
        self.drag_offset_y: int = 0
        self.bg_rect: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self.title_bar_height: int = DIALOG_TITLE_BAR_HEIGHT

    def _is_title_bar(self, pos: Tuple[int, int]) -> bool:
        return self.bg_rect.collidepoint(pos) and pos[1] - self.bg_rect.y < self.title_bar_height

    def handle_drag_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._is_title_bar(e.pos):
                self.dragging = True
                self.drag_offset_x = e.pos[0] - self.bg_rect.x
                self.drag_offset_y = e.pos[1] - self.bg_rect.y
                if hasattr(self.sim, '_dialog_stack') and self in self.sim._dialog_stack:
                    self.sim._dialog_stack.remove(self)
                    self.sim._dialog_stack.append(self)
                return True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.dragging:
                self.dragging = False
                return True
        elif e.type == pygame.MOUSEMOTION and self.dragging:
            new_x = e.pos[0] - self.drag_offset_x
            new_y = e.pos[1] - self.drag_offset_y
            new_x = max(0, min(new_x, self.sim.screen_width - self.bg_rect.width))
            new_y = max(0, min(new_y, self.sim.screen_height - self.bg_rect.height))
            self.bg_rect.x = new_x
            self.bg_rect.y = new_y
            return True
        return False
