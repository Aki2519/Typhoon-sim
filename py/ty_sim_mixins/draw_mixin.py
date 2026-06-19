# py/ty_sim_mixins/draw_mixin.py
"""绘图协调 Mixin：聚合所有子绘图 Mixin。"""
import pygame
from ..constants import (
    f_m, rt, TXT, BG,
    ERROR_BG, ERROR_BORDER,
)
from .draw_typhoon_mixin import TySimDrawTyphoonMixin
from .draw_info_boxes_mixin import TySimDrawInfoBoxesMixin
from .draw_ace_mixin import TySimDrawACEMixin
from .draw_season_clock_mixin import TySimDrawSeasonClockMixin
from .draw_control_panel_mixin import TySimDrawControlPanelMixin


class TySimDrawMixin(
    TySimDrawTyphoonMixin,
    TySimDrawInfoBoxesMixin,
    TySimDrawACEMixin,
    TySimDrawSeasonClockMixin,
    TySimDrawControlPanelMixin,
):

    def _draw_map(self, surface):
        self.map_mgr.draw_map(surface)

    def draw(self, surface):
        surface.fill(BG)
        self._draw_map(surface)

        if self.md == self.MODE_SEASON:
            self.draw_season_clock(surface)
            self.draw_ace_display(surface)
            if self.show_info_box_season:
                self.draw_season_info_boxes(surface)

        self._draw_typhoons(surface)

        ct = pygame.time.get_ticks()
        for eff in self.effects:
            eff.draw(surface, ct)

        # 绘制台风信息框
        if self.md == self.MODE_NORMAL:
            ty = self.current_typhoon()
            if ty and not self.dialog_mgr.any_active() and ty.icon_alpha > 0:
                self.draw_typhoon_info(surface, ty)
        elif self.md == self.MODE_SEASON:
            for ty in self.tys:
                if ty.act and ty.ss and not ty.sf:
                    self.draw_typhoon_info(surface, ty)
        elif self.md == self.MODE_EDIT and self.edit_typhoon:
            if not self.dialog_mgr.any_active():
                self.draw_typhoon_info(surface, self.edit_typhoon)

        self.draw_control_panel(surface)
        self.dialog_mgr.draw(surface)

        if self.error_message and pygame.time.get_ticks() - self.error_time < 2000:
            self._draw_error(surface)

    def _draw_error(self, surface):
        err = rt(f_m, self.error_message, (255, 255, 255))
        p = 10
        w, h = err.get_width() + 2 * p, err.get_height() + 2 * p
        x = (self.screen_width - w) // 2
        bg = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(bg, ERROR_BG + (220,), (0, 0, w, h), 0, 5)
        pygame.draw.rect(bg, ERROR_BORDER + (220,), (0, 0, w, h), 2, 5)
        surface.blit(bg, (x, 10))
        surface.blit(err, (x + p, 10 + p))