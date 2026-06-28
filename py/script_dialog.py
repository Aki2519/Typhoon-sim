# py/script_dialog.py
"""脚本选择与执行对话框。"""
from __future__ import annotations

import os
import pygame
from typing import List

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED,
    DIALOG_TITLE_BAR_HEIGHT,
)
from .dialog_base import DraggableDialog
from .script_engine import scan_scripts


SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'script')

# 对话框尺寸
DIALOG_W = 550
DIALOG_H = 450
ITEM_H = 36
ITEMS_PER_PAGE = 8


class ScriptDialog(DraggableDialog):
    """脚本列表对话框。"""

    def __init__(self, sim):
        super().__init__(sim)
        self.active = False
        self.dragging = False
        self.title_text = rt(f_m, "脚本列表", TXT)
        self.hint_text = rt(f_s, "点击脚本开始执行，ESC 或点击外部关闭", TXT)
        self.close_text = rt(f_s, "关闭", (255, 255, 255))
        self.stop_text = rt(f_s, "停止脚本", (255, 255, 255))

        self.scripts: List[dict] = []
        self._scroll_offset = 0
        self._hovered_idx = -1

        self._refresh_scripts()

    def _refresh_scripts(self):
        """刷新脚本列表。"""
        self.scripts = scan_scripts(SCRIPT_DIR)

    def activate(self):
        """打开对话框。"""
        super().activate()
        self._refresh_scripts()
        self._scroll_offset = 0
        self._hovered_idx = -1

        dw, dh = DIALOG_W, DIALOG_H
        dx = (self.sim.screen_width - dw) // 2
        dy = (self.sim.screen_height - dh) // 2
        self.bg_rect = pygame.Rect(dx, dy, dw, dh)

    def deactivate(self):
        """关闭对话框。"""
        super().deactivate()
        self._hovered_idx = -1

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return

        r = self.bg_rect
        self.draw_background(surface, r)
        self.draw_title(surface, self.title_text, r, y_offset=20)

        list_x = r.x + 20
        list_y = r.y + 65
        list_w = r.width - 40
        list_h = ITEMS_PER_PAGE * ITEM_H

        # 脚本列表背景
        list_bg = pygame.Rect(list_x, list_y, list_w, list_h)
        pygame.draw.rect(surface, (255, 255, 255, 200), list_bg, 0, 5)
        pygame.draw.rect(surface, BUTTON_BORDER, list_bg, 1, 5)

        # 可见脚本
        visible_scripts = self.scripts[self._scroll_offset:self._scroll_offset + ITEMS_PER_PAGE]
        mx, my = pygame.mouse.get_pos()
        self._hovered_idx = -1

        for i, script_info in enumerate(visible_scripts):
            actual_idx = self._scroll_offset + i
            item_y = list_y + i * ITEM_H
            item_rect = pygame.Rect(list_x, item_y, list_w, ITEM_H)

            # 悬停高亮
            hover = item_rect.collidepoint(mx, my)
            if hover:
                self._hovered_idx = actual_idx
                pygame.draw.rect(surface, (200, 220, 255, 180), item_rect, 0, 4)

            # 分割线
            if i > 0:
                pygame.draw.line(surface, (200, 210, 230),
                                 (list_x + 5, item_y), (list_x + list_w - 5, item_y), 1)

            # 文件名
            name_surf = rt(f_s, script_info['filename'], TXT)
            surface.blit(name_surf, (list_x + 10, item_y + 4))

            # 简介（小字）
            desc = script_info.get('description', '')
            if desc:
                max_desc_w = list_w - 180
                desc_surf = rt(f_s, desc, (120, 120, 150))
                if desc_surf.get_width() > max_desc_w:
                    while desc_surf.get_width() > max_desc_w and len(desc) > 3:
                        desc = desc[:-1]
                        desc_surf = rt(f_s, desc + "...", (120, 120, 150))
                    desc_surf = rt(f_s, desc + "...", (120, 120, 150))
                surface.blit(desc_surf, (list_x + 160, item_y + 6))

        # 滚动提示
        total = len(self.scripts)
        if total > ITEMS_PER_PAGE:
            scroll_info = f"{self._scroll_offset + 1}-{min(self._scroll_offset + ITEMS_PER_PAGE, total)} / {total}"
            scroll_surf = rt(f_s, scroll_info, TXT)
            surface.blit(scroll_surf, (list_x + list_w - scroll_surf.get_width() - 5, list_y + list_h + 8))

        # 提示文字
        hint_y = r.y + list_h + 80
        surface.blit(self.hint_text, (r.x + 20, hint_y))

        # 按钮
        btn_y = r.y + r.height - 40
        close_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
        self._draw_btn(surface, close_btn, self.close_text, BUTTON_BORDER)

        # 停止按钮（始终显示，未运行时灰色）
        engine_running = hasattr(self.sim, 'script_engine') and self.sim.script_engine.running
        stop_btn = pygame.Rect(r.x + r.width - 200, btn_y, 90, 28)
        stop_color = (200, 80, 80) if engine_running else BUTTON_DISABLED
        self._draw_btn(surface, stop_btn, self.stop_text, stop_color)

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if self.handle_drag_event(e):
            return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_UP:
                self._scroll_offset = max(0, self._scroll_offset - 1)
                return True
            if e.key == pygame.K_DOWN:
                max_offset = max(0, len(self.scripts) - ITEMS_PER_PAGE)
                self._scroll_offset = min(max_offset, self._scroll_offset + 1)
                return True

        if e.type == pygame.MOUSEWHEEL:
            max_offset = max(0, len(self.scripts) - ITEMS_PER_PAGE)
            self._scroll_offset = max(0, min(max_offset, self._scroll_offset - e.y))
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            r = self.bg_rect

            # 关闭按钮
            btn_y = r.y + r.height - 40
            close_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
            if close_btn.collidepoint(x, y):
                self.deactivate()
                return True

            # 停止按钮（始终可点击）
            if hasattr(self.sim, 'script_engine') and self.sim.script_engine.running:
                stop_btn = pygame.Rect(r.x + r.width - 200, btn_y, 90, 28)
                if stop_btn.collidepoint(x, y):
                    self.sim.script_engine.stop()
                    return True

            # 点击脚本项
            if self._hovered_idx >= 0 and self._hovered_idx < len(self.scripts):
                self._run_script(self._hovered_idx)
                return True

            # 点击对话框外部
            if not r.collidepoint(x, y):
                self.deactivate()
                return True

        return False

    def _run_script(self, idx: int):
        """执行选中的脚本。"""
        if idx < 0 or idx >= len(self.scripts):
            return

        script_info = self.scripts[idx]
        try:
            with open(script_info['path'], 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            self.sim.show_error(f"无法读取脚本: {e}")
            return

        engine = self.sim.script_engine
        if engine.load_script(text, script_info['filename']):
            engine.start()
            self.deactivate()

    def _draw_btn(self, surface: pygame.Surface, rect: pygame.Rect,
                  text_surf: pygame.Surface, color):
        """绘制按钮。"""
        mx, my = pygame.mouse.get_pos()
        hover = rect.collidepoint(mx, my)
        pressed = hover and pygame.mouse.get_pressed()[0]

        if pressed:
            from .constants import darken_color
            c = darken_color(color, 0.8)
        elif hover:
            from .constants import lighten_color
            c = lighten_color(color, 1.2)
        else:
            c = color

        pygame.draw.rect(surface, c, rect, 0, 5)
        surface.blit(text_surf, (
            rect.centerx - text_surf.get_width() // 2,
            rect.centery - text_surf.get_height() // 2,
        ))
