# py/renderer.py
"""统一渲染器：组合所有绘制逻辑。"""
from __future__ import annotations

import pygame
from typing import TYPE_CHECKING

from .constants import (
    BG, ERROR_BG, ERROR_BORDER, f_m, f_s, rt,
    FPS_GREEN, FPS_YELLOW, FPS_RED, ERROR_TIMEOUT_MS,
)

if TYPE_CHECKING:
    from .ty_sim import TySim


class Renderer:
    """接受所有依赖，提供 draw() 入口。"""

    def __init__(self, sim: TySim) -> None:
        self.sim = sim

    def draw(self, surface: pygame.Surface) -> None:
        sim = self.sim
        hidden = getattr(sim, '_ui_hidden', False)
        self._draw_scene(surface, hidden)
        if not hidden:
            sim.draw_control_panel(surface)
            sim.dialog_mgr.draw(surface)

            if getattr(sim.cfg, 'show_fps', False):
                self._draw_fps(surface)

            if sim.error_message and pygame.time.get_ticks() - sim.error_time < ERROR_TIMEOUT_MS:
                self._draw_error(surface)
        else:
            sim.draw_control_panel(surface)

    def _draw_scene(self, surface: pygame.Surface, hidden: bool = False) -> None:
        sim = self.sim
        surface.fill(BG)
        sim._draw_map(surface)

        if not hidden:
            if sim.md == sim.MODE_SEASON:
                sim.draw_season_clock(surface)
                sim._ms.draw(surface)

            if getattr(sim, 'show_ace_bar', True):
                sim.draw_ace_display(surface)

            if sim.md == sim.MODE_SEASON:
                if sim.show_info_box_season:
                    sim.draw_season_info_boxes(surface)

        sim._draw_typhoons(surface)

        if hidden:
            return

        ct = pygame.time.get_ticks()

        dialog_open = sim.dialog_mgr.any_active()
        if sim.md == sim.MODE_NORMAL:
            ty = sim.current_typhoon()
            if ty and not dialog_open and getattr(ty.v, 'icon_alpha', 255) > 0:
                sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_SEASON:
            for ty in sim.tys:
                if ty.act and ty.ss and not ty.sf:
                    sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_EDIT:
            ty = sim.edit_typhoon
            if ty and not dialog_open and getattr(ty.v, 'icon_alpha', 255) > 0:
                sim.draw_typhoon_info(surface, ty)

        for eff in sim.effects:
            if hasattr(eff, '_map_height'):
                eff._map_height = sim.map_height
                eff._dark_mode = getattr(sim, 'dark_mode', True)
            eff.draw(surface, ct)

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
            color = FPS_GREEN
        elif fps >= 30:
            color = FPS_YELLOW
        else:
            color = FPS_RED
        tu = getattr(self.sim, '_t_update_ms', 0)
        td = getattr(self.sim, '_t_draw_ms', 0)
        fps_text = rt(f_s, f"FPS: {fps:.0f}  U:{tu}ms D:{td}ms", color)
        x = self.sim.screen_width - fps_text.get_width() - 8
        surface.blit(fps_text, (x, 8))
