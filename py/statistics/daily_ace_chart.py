# py/statistics/daily_ace_chart.py
"""每日 ACE 柱状图。预渲染缓存。"""
import math
import pygame
from datetime import timedelta
from typing import List, Tuple, Optional, Dict

from ..constants import TXT, f_s, rt
from .draw_helpers_chart import DASH_COLOR, _get_dashed_h_surface

DAILY_BAR_COLOR = (0, 200, 180)
DAILY_BAR_MAX_COLOR = (220, 40, 40)

_daily_ace_cache: Dict[Tuple, dict] = {}
_MAX_CACHE = 16


def draw_daily_ace_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    daily_ace_list: List[Tuple[int, float]],
    year_range: Tuple,
    draw_dashed_h,
) -> Optional[Tuple[str, Tuple[int, int]]]:
    if not daily_ace_list:
        return None

    n_days = len(daily_ace_list)
    if n_days == 0:
        return None

    key = (id(daily_ace_list), rect.width, rect.height)

    if key not in _daily_ace_cache:
        cached = {}
        w, h = rect.width, rect.height

        max_daily = max(ace for _, ace in daily_ace_list)
        if max_daily <= 0:
            max_daily = 1.0
        y_max = max_daily * 1.2

        bar_w = w / n_days
        max_i = max(range(n_days), key=lambda i: daily_ace_list[i][1])

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        start_dt, _, _ = year_range
        max_labels = []

        for i, (_, ace) in enumerate(daily_ace_list):
            x_px = i * bar_w
            rel_h = ace / y_max if y_max > 0 else 0
            bar_h_val = max(1, rel_h * h)
            bar_y = h - bar_h_val
            color = DAILY_BAR_MAX_COLOR if (i == max_i and ace > 0) else DAILY_BAR_COLOR
            br = pygame.Rect(int(x_px), int(bar_y), max(1, int(math.ceil(bar_w))), int(bar_h_val))
            pygame.draw.rect(chart_surf, color, br)

            if i == max_i and ace > 0:
                al = rt(f_s, f"{ace:.4f}", DAILY_BAR_MAX_COLOR)
                lx = br.centerx - al.get_width() // 2
                ly = br.top - al.get_height() - 2
                if ly < 0:
                    ly = br.bottom + 2
                max_labels.append((al, lx, ly))

        # 边框
        bw_line = 2
        pygame.draw.line(chart_surf, TXT, (0, 0), (0, h), bw_line)
        pygame.draw.line(chart_surf, TXT, (w - 1, 0), (w - 1, h), bw_line)
        pygame.draw.line(chart_surf, TXT, (0, h - 1), (w, h - 1), bw_line)

        # 刻度标签 + 虚线
        tick_labels = []
        y_tick_step = 2.0
        val = 0.0
        while val <= y_max:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, f"{val:.0f}", TXT)
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, DASH_COLOR)
                chart_surf.blit(dash_surf, (0, int(y_px)))
            val += y_tick_step

        cached['chart_surf'] = chart_surf
        cached['tick_labels'] = tick_labels
        cached['max_labels'] = max_labels
        cached['n_days'] = n_days
        cached['bar_w'] = bar_w
        cached['start_dt'] = start_dt
        cached['daily_ace_list'] = daily_ace_list

        if len(_daily_ace_cache) >= _MAX_CACHE:
            _daily_ace_cache.pop(next(iter(_daily_ace_cache)))
        _daily_ace_cache[key] = cached
    else:
        cached = _daily_ace_cache[key]

    # ── 绘制 ──
    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))
    for al, lx, ly in cached.get('max_labels', []):
        surface.blit(al, (rect.x + lx, rect.y + ly))

    # ── 悬停 ──
    mx, my = pygame.mouse.get_pos()
    if rect.collidepoint(mx, my):
        day_i = int((mx - rect.x) / cached['bar_w'])
        if 0 <= day_i < cached['n_days']:
            _, ace = cached['daily_ace_list'][day_i]
            dt = cached['start_dt'] + timedelta(days=day_i)
            return f"{dt.month}/{dt.day}: {ace:.4f}", (mx + 15, my - 25)
    return None
