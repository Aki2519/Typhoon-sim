# py/statistics/activity_count_chart.py
"""活跃台风数柱状图。预渲染缓存。"""
import math
import pygame
from datetime import timedelta
from typing import List, Tuple, Optional, Dict

from ..constants import TXT, f_s, rt
from .draw_helpers_chart import DASH_COLOR, _get_dashed_h_surface

DAILY_BAR_COLOR = (0, 200, 180)
DAILY_BAR_MAX_COLOR = (220, 40, 40)

_activity_cache: Dict[Tuple, dict] = {}
_MAX_CACHE = 16


def draw_activity_count_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    activity_count_list: List[Tuple[int, int]],
    year_range: Tuple,
    draw_dashed_h,
) -> Optional[Tuple[str, Tuple[int, int]]]:
    if not activity_count_list:
        return None

    n_days = len(activity_count_list)
    if n_days == 0:
        return None

    key = (id(activity_count_list), rect.width, rect.height)

    if key not in _activity_cache:
        cached = {}
        w, h = rect.width, rect.height

        max_count = max(c for _, c in activity_count_list)
        if max_count <= 0:
            max_count = 1
        y_max = float(max_count + 1)

        bar_w = w / n_days
        max_i = max(range(n_days), key=lambda i: activity_count_list[i][1])

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        start_dt, _, _ = year_range

        for i, (_, cnt) in enumerate(activity_count_list):
            x_px = i * bar_w
            rel_h = cnt / y_max if y_max > 0 else 0
            bar_h_val = max(1, rel_h * h)
            bar_y = h - bar_h_val
            color = DAILY_BAR_MAX_COLOR if (i == max_i and cnt > 0) else DAILY_BAR_COLOR
            br = pygame.Rect(int(x_px), int(bar_y), max(1, int(math.ceil(bar_w))), int(bar_h_val))
            pygame.draw.rect(chart_surf, color, br)

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
        cached['n_days'] = n_days
        cached['bar_w'] = bar_w
        cached['start_dt'] = start_dt
        cached['activity_count_list'] = activity_count_list

        if len(_activity_cache) >= _MAX_CACHE:
            _activity_cache.pop(next(iter(_activity_cache)))
        _activity_cache[key] = cached
    else:
        cached = _activity_cache[key]

    # ── 绘制 ──
    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    # ── 悬停 ──
    mx, my = pygame.mouse.get_pos()
    if rect.collidepoint(mx, my):
        day_i = int((mx - rect.x) / cached['bar_w'])
        n_days = cached['n_days']
        if 0 <= day_i < n_days:
            _, cnt = cached['activity_count_list'][day_i]
            dt = cached['start_dt'] + timedelta(days=day_i)
            return f"{dt.month}/{dt.day}: {cnt}个活跃台风", (mx + 15, my - 25)
    return None