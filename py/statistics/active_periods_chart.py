from __future__ import annotations

# py/statistics/active_periods_chart.py
"""活跃周期图。预渲染缓存，消除每帧 Surface 分配。"""
import pygame
from typing import List, Tuple, Optional, Dict

from ..constants import f_s, rt

_active_periods_cache: Dict[Tuple, dict] = {}
_MAX_CACHE = 16


def draw_active_periods_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    active_periods: List[dict],
    year_range: Tuple,
    typhoon_bar_width: float,
    area_map: Dict[str, str] = None,
) -> Tuple[Optional[Tuple[str, Tuple[int, int]]], list]:
    if not active_periods:
        return None, []

    bw = typhoon_bar_width
    key = (id(active_periods), rect.width, rect.height, round(bw, 2))

    if key not in _active_periods_cache:
        cached = {}
        w, h = rect.width, rect.height
        n_mid = 25
        zone_h = 18
        pad_edge = 15
        bar_h = 14
        bar_pad = 2

        start_dt, _, total_hours = year_range

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        bar_info = []      # (bar_rect_local, hover_str, period)
        name_entries = []  # (name_surf, center_x_local, center_y_local)

        for i, period in enumerate(active_periods):
            zone_idx = i % n_mid
            zone_top = pad_edge + zone_idx * zone_h
            bar_top = zone_top + bar_pad

            t1_hours = max(0, (period['start_dt'] - start_dt).total_seconds() / 3600)
            t2_hours = min(total_hours, (period['end_dt'] - start_dt).total_seconds() / 3600)
            x1 = (t1_hours / total_hours) * w
            x2 = (t2_hours / total_hours) * w
            bar_w_px = max(1, x2 - x1)

            bar_rect = pygame.Rect(int(x1), int(bar_top), int(bar_w_px), max(1, int(bar_h)))
            color = period['color']
            pygame.draw.rect(chart_surf, color, bar_rect)

            # type2_times 段 → 一次性渲染，不再逐段 new Surface
            type2_times = period.get('type2_times', [])
            for k in range(0, len(type2_times) - 1, 2):
                ta, tb = type2_times[k], type2_times[k + 1]
                if ta is None or tb is None:
                    continue
                ha = max(0, (ta - start_dt).total_seconds() / 3600)
                hb = min(total_hours, (tb - start_dt).total_seconds() / 3600)
                if hb <= ha:
                    continue
                sx1 = (ha / total_hours) * w
                sx2 = (hb / total_hours) * w
                sr = pygame.Rect(int(sx1), int(bar_top), max(1, int(sx2 - sx1)), max(1, int(bar_h)))
                alpha_color = (color[0], color[1], color[2], 128)
                pygame.draw.rect(chart_surf, (255, 255, 255), sr)
                overlay = pygame.Surface((sr.width, sr.height), pygame.SRCALPHA)
                overlay.fill(alpha_color)
                chart_surf.blit(overlay, sr)

            pygame.draw.rect(chart_surf, (0, 0, 0), bar_rect, 1)

            # 名称标签：不在预渲染 Surface 上画（会被裁剪），改为记录坐标在主 surface 绘制
            name_surf = period.get('name_surf')
            if name_surf is None:
                name_surf = rt(f_s, period['name_str'], (0, 0, 0))
                period['name_surf'] = name_surf
            name_entries.append((name_surf, bar_rect.left, bar_rect.centery))

            basin_name = (area_map.get(period.get('basin', ''), period.get('basin', '')) 
                          if area_map else '')
            hover_str = (
                f"{period['name_str']}: "
                f"{period['start_dt'].strftime('%m/%d %HZ')} - "
                f"{period['end_dt'].strftime('%m/%d %HZ')}"
                f"{'  ' + basin_name if basin_name else ''}"
            )
            bar_info.append((bar_rect, hover_str, period))

        cached['chart_surf'] = chart_surf
        cached['bar_info'] = bar_info
        cached['name_entries'] = name_entries

        if len(_active_periods_cache) >= _MAX_CACHE:
            _active_periods_cache.pop(next(iter(_active_periods_cache)))
        _active_periods_cache[key] = cached
    else:
        cached = _active_periods_cache[key]

    # ── 绘制 ──
    surface.blit(cached['chart_surf'], rect.topleft)

    # 名称标签（画在主 surface 上，避免预渲染 Surface 边界裁剪）
    for name_surf, bar_left, bar_cy in cached['name_entries']:
        nx = rect.x + bar_left - name_surf.get_width() - 3
        ny = rect.y + bar_cy - name_surf.get_height() // 2
        surface.blit(name_surf, (nx, ny))

    # ── 悬停 + 点击目标 ──
    mx, my = pygame.mouse.get_pos()
    hover_result = None
    click_targets = []
    for br_local, hover_str, period in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        click_targets.append((br_screen, period))
        if br_screen.collidepoint(mx, my):
            hover_result = (hover_str, (mx + 15, my - 25))
    return hover_result, click_targets