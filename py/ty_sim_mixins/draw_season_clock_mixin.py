# py/ty_sim_mixins/draw_season_clock_mixin.py
"""风季时钟 Mixin。"""
from __future__ import annotations
import math
import pygame
from ..constants import (
    f_s, rt, TXT,
    SEASON_CLOCK_BG, SEASON_CLOCK_BORDER, SEASON_CLOCK_QUARTER,
)


class TySimDrawSeasonClockMixin:
    """风季时钟绘制。"""

    def draw_season_clock(self, surface: pygame.Surface) -> None:
        tr = 40
        cx, cy = 60, 60
        pygame.draw.circle(surface, SEASON_CLOCK_BG, (cx, cy), tr)
        pygame.draw.circle(surface, SEASON_CLOCK_BORDER, (cx, cy), tr, 2)
        hour = int(self.st[4:6])
        quarter_colors = [SEASON_CLOCK_QUARTER] * 4
        if 6 <= hour <= 11:
            quarter_colors[3] = SEASON_CLOCK_BORDER
        elif 12 <= hour <= 17:
            quarter_colors[3] = quarter_colors[2] = SEASON_CLOCK_BORDER
        elif 18 <= hour <= 23:
            quarter_colors[3] = quarter_colors[2] = quarter_colors[1] = SEASON_CLOCK_BORDER
        for i in range(4):
            start_angle = 270 - i * 90
            end_angle = 270 - (i + 1) * 90
            points = [(cx, cy)]
            for a in range(int(start_angle), int(end_angle) - 1, -5):
                rad = math.radians(a)
                x = cx + tr * math.cos(rad)
                y = cy + tr * math.sin(rad)
                points.append((x, y))
            if len(points) > 2:
                pygame.draw.polygon(surface, quarter_colors[i], points)
        for i in range(4):
            a = math.radians(270 - i * 90)
            x1 = cx + tr * math.cos(a)
            y1 = cy + tr * math.sin(a)
            pygame.draw.line(surface, SEASON_CLOCK_BORDER, (cx, cy), (x1, y1), 2)
        pygame.draw.circle(surface, SEASON_CLOCK_BG, (cx, cy), tr - 15)
        year_text = rt(f_s, f"{self.sy}", TXT)
        month, day = self.st[0:2], self.st[2:4]
        md_text = rt(f_s, f"{int(month)}.{int(day)}", TXT)
        hour_text = rt(f_s, f"{hour:02d}", TXT)
        surface.blit(year_text, (cx - year_text.get_width() // 2, cy - 21))
        surface.blit(md_text, (cx - md_text.get_width() // 2, cy - 6))
        surface.blit(hour_text, (cx - hour_text.get_width() // 2, cy + 9))