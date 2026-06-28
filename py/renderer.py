# py/renderer.py
"""统一渲染器：组合所有绘制逻辑。"""
from __future__ import annotations

import pygame
from typing import TYPE_CHECKING

from .constants import BG, ERROR_BG, ERROR_BORDER, f_m, rt, TXT

if TYPE_CHECKING:
    from .ty_sim import TySim


class Renderer:
    """接受所有依赖，提供 draw() 入口。"""

    def __init__(self, sim: TySim) -> None:
        self.sim = sim

    def draw(self, surface: pygame.Surface) -> None:
        self._draw_scene(surface)
        sim = self.sim
        sim.draw_control_panel(surface)
        sim.dialog_mgr.draw(surface)

        if getattr(sim.cfg, 'show_fps', False):
            self._draw_fps(surface)

        if sim.error_message and pygame.time.get_ticks() - sim.error_time < 2000:
            self._draw_error(surface)

    def draw_view_only(self, surface: pygame.Surface) -> None:
        """绘制地图+台风+效果，不含控制面板和对话框。用于截图。"""
        self._draw_scene(surface)

    def _draw_scene(self, surface: pygame.Surface) -> None:
        sim = self.sim
        surface.fill(BG)
        sim._draw_map(surface)

        if sim.md == sim.MODE_SEASON:
            sim.draw_season_clock(surface)
            sim.draw_ace_display(surface)
            sim._ms.draw(surface)
            if sim.show_info_box_season:
                sim.draw_season_info_boxes(surface)

        sim._draw_typhoons(surface)

        ct = pygame.time.get_ticks()
        for eff in sim.effects:
            eff.draw(surface, ct)

        dialog_open = sim.dialog_mgr.any_active()
        if sim.md == sim.MODE_NORMAL:
            ty = sim.current_typhoon()
            if ty and not dialog_open and getattr(ty.v, 'icon_alpha', 255) > 0:
                sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_SEASON:
            for ty in sim.tys:
                if ty.act and ty.ss and not ty.sf:
                    sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_EDIT and sim.edit_typhoon:
            if not dialog_open:
                sim.draw_typhoon_info(surface, sim.edit_typhoon)

    def _draw_error(self, surface: pygame.Surface) -> None:
        sim = self.sim
        err = rt(f_m, sim.error_message, (255, 255, 255))
        p = 10
        w, h = err.get_width() + 2 * p, err.get_height() + 2 * p
        x = (sim.screen_width - w) // 2
        bg_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(bg_surf, ERROR_BG + (220,), (0, 0, w, h), 0, 5)
        pygame.draw.rect(bg_surf, ERROR_BORDER + (220,), (0, 0, w, h), 2, 5)
        surface.blit(bg_surf, (x, 10))
        surface.blit(err, (x + p, 10 + p))

    def _draw_fps(self, surface: pygame.Surface) -> None:
        fps = getattr(self.sim, '_fps', 60.0)
        if fps >= 60:
            color = (0, 180, 0)
        elif fps >= 30:
            color = (220, 180, 0)
        else:
            color = (220, 30, 30)
        from .constants import f_s, rt  # noqa: F811
        fps_text = rt(f_s, f"FPS: {fps:.0f}", color)
        surface.blit(fps_text, (8, self.sim.screen_height - 28))
