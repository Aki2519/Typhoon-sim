# py/track_dialog.py
"""台风季镜头跟踪选择对话框: 关闭 / 全部活跃 / 指定台风。"""
from __future__ import annotations

import pygame
from .constants import f_s, f_m, rt, TXT, BUTTON_BORDER, SETTINGS_TEXT_LIGHT
from .dialog_base import DraggableDialog


class TrackDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title = rt(f_m, "镜头跟踪", TXT)
        self.title_dark = rt(f_m, "镜头跟踪", SETTINGS_TEXT_LIGHT)
        self._rows: list = []          # (text_surf, callback)
        self.title_bar_height = 30

    def activate(self):
        super().activate()
        sim = self.sim
        w = 460
        # 选项行: 关闭 / 全部活跃 / 每个活跃台风
        options = []
        options.append(("关闭镜头跟踪", 'off', None))
        options.append(("跟踪全部活跃台风", 'all', None))
        active = [ty for ty in sim.tys if ty.act and not ty.sf and ty.pts]
        for ty in active:
            options.append((f"跟踪: {sim.get_display_name(ty)}", 'single', ty))

        row_h = 34
        h = 30 + 10 + len(options) * row_h + 12
        self.bg_rect = pygame.Rect(
            (sim.screen_width - w) // 2, (sim.screen_height - h) // 2, w, h)

        tc = SETTINGS_TEXT_LIGHT if self.dark_mode else TXT
        self._rows = []
        cur = (self.sim.tracking_mode,
               self.sim.tracking_typhoon if self.sim.tracking_mode == 'single' else None)
        for label, mode, ty in options:
            if mode == 'single' and ty is None:
                # 台风季没有活跃台风时仍可显示占位(当前无活跃)
                text = rt(f_s, label, tc)
            else:
                text = rt(f_s, label, tc)
            mark = "✓ " if (cur[0] == mode and cur[1] is ty) else ""
            if mark:
                text = rt(f_s, mark + label, (90, 210, 120))
            self._rows.append((text, mode, ty))
        self._update_bg_rect()

    def _update_bg_rect(self):
        pass

    def _row_rect(self, i):
        return pygame.Rect(self.bg_rect.x + 20, self.bg_rect.y + 42 + i * 34,
                           self.bg_rect.width - 40, 28)

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, (surf, mode, ty) in enumerate(self._rows):
                if self._row_rect(i).collidepoint(e.pos):
                    self.sim.set_tracking(mode, ty)
                    self.deactivate()
                    return True
        return False

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dark = self.dark_mode
        r = self.bg_rect
        if dark:
            self.draw_dark_panel(surface, r)
            self.draw_title(surface, self.title_dark, r, y_offset=10)
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=10)
        for i, (surf, mode, ty) in enumerate(self._rows):
            rr = self._row_rect(i)
            hover = rr.collidepoint(pygame.mouse.get_pos())
            if hover:
                if dark:
                    pygame.draw.rect(surface, (50, 60, 85), rr, border_radius=4)
                else:
                    pygame.draw.rect(surface, (210, 225, 245), rr, border_radius=4)
            surface.blit(surf, (rr.x + 8, rr.y + (rr.height - surf.get_height()) // 2))
