from __future__ import annotations

# py/ty_sim_mixins/draw_ace_mixin.py
"""风季 ACE 进度条 / 信息框。"""
import pygame
from ..constants import f_s, f_m, rt, HEMISPHERE_NORTH, INFO_BOX_BG, INFO_BOX_BORDER, TXT

ACE_TEXT = TXT


class TySimDrawACEMixin:

    def draw_ace_display(self, surface):
        if self.md == self.MODE_NORMAL:
            return

        ace_year = self.current_ace_year
        year_str = str(ace_year) if self.hemisphere == HEMISPHERE_NORTH else f"{ace_year}-{ace_year + 1}"

        lm = self.ace_limit_mode
        bc = self.ace_limit_basin
        if lm == 'basin' and bc:
            area = self.res_mgr.ocean_areas.get_by_code(bc)
            label = f"{year_str} {area.name_full if area else bc} ACE:"
        else:
            label = f"{year_str} ACE:"

        cya = self.yad.get(ace_year, 0.0)
        right = self.screen_width - 10

        if self.ace_display_mode == "original":
            self._draw_original(surface, label, cya, right)
        else:
            self._draw_progress(surface, label, cya, right)

    def _draw_original(self, surface, label, cya, right):
        w, h = 220, 80
        x = right - w
        bg = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(bg, INFO_BOX_BG, (0, 0, w, h), 0, 10)
        pygame.draw.rect(bg, INFO_BOX_BORDER, (0, 0, w, h), 2, 10)
        surface.blit(bg, (x, 30))

        for i, (font, text) in enumerate([
            (f_s, label), (f_m, f"{cya:.4f}"), (f_s, f"总ACE: {self.tsa:.4f}"),
        ]):
            s = rt(font, text, ACE_TEXT)
            surface.blit(s, (x + w - s.get_width() - 10, 40 + i * 22))

    def _draw_progress(self, surface, label, cya, right):
        w, h = 220, 20
        x = right - w

        pygame.draw.rect(surface, (255, 255, 255), (x, 30, w, h), 1)
        if cya > 0:
            fw = int(w * min(1.0, self.csa / cya))
            pygame.draw.rect(surface, (255, 200, 0), (x, 30, fw, h))

        val = f"{self.csa:.4f}"
        black = rt(f_m, val, (0, 0, 0))
        white = rt(f_m, val, (255, 255, 255))
        tx, ty = x + 5, 31 + (h - white.get_height()) // 2
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            surface.blit(black, (tx + dx, ty + dy))
        surface.blit(white, (tx, ty))

        ls = rt(f_s, label, (255, 255, 255))
        surface.blit(ls, (right - ls.get_width(), 55))
        ys = rt(f_s, f"{cya:.4f}", (255, 255, 255))
        surface.blit(ys, (right - ys.get_width(), 72))