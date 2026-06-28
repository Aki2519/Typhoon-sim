# py/statistics/typhoon_ace_chart.py
"""台风 ACE 柱状图。预渲染缓存。"""
from __future__ import annotations
import pygame
from typing import List, Tuple, Optional, Dict

from ..constants import TXT, f_s, rt

BAR_MAX_PER_PAGE = 20

_typhoon_ace_cache: Dict[Tuple, dict] = {}
_MAX_CACHE = 32


def _wrap_name_cached(name: str, max_chars: int = 10) -> List[str]:
    """换行名称（结果缓存）。"""

    if not hasattr(_wrap_name_cached, '_cache'):
        _wrap_name_cached._cache = {}
    key = (name, max_chars)
    if key in _wrap_name_cached._cache:
        return _wrap_name_cached._cache[key]
    if len(name) <= max_chars:
        result = [name]
    else:
        result = [name[i:i + max_chars] for i in range(0, len(name), max_chars)]
    if len(_wrap_name_cached._cache) > 512:
        _wrap_name_cached._cache.pop(next(iter(_wrap_name_cached._cache)))
    _wrap_name_cached._cache[key] = result
    return result


def draw_typhoon_ace_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    typhoon_ace_list: List[Tuple[str, float]],
    bar_page: int = 0,
) -> Tuple[Optional[Tuple[str, Tuple[int, int]]], int, bool]:
    total = len(typhoon_ace_list)
    total_pages = max(1, (total + BAR_MAX_PER_PAGE - 1) // BAR_MAX_PER_PAGE)
    start = bar_page * BAR_MAX_PER_PAGE
    page_data = typhoon_ace_list[start:start + BAR_MAX_PER_PAGE]

    if not page_data:
        return None, total_pages, total_pages > 1

    key = (id(typhoon_ace_list), bar_page, rect.width, rect.height)

    if key not in _typhoon_ace_cache:
        cached = {}
        w, h = rect.width, rect.height
        n = len(page_data)
        max_ace = max(ace for _, ace in typhoon_ace_list) if typhoon_ace_list else 1.0
        y_max_val = max_ace * 1.1
        if y_max_val <= 0:
            y_max_val = 10.0

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        # 边框
        pygame.draw.rect(chart_surf, TXT, (0, 0, w, h), 2)

        # 刻度标签
        tick_labels = []
        y_tick = 10.0
        val = 0.0
        while val <= y_max_val:
            rel = val / y_max_val
            y_px = h - rel * h
            lbl = rt(f_s, f"{val:.0f}", TXT)
            tick_labels.append((lbl, int(y_px)))
            val += y_tick

        cached['tick_labels'] = tick_labels

        # 柱状图
        bar_w = min(40, w / n * 0.7) if n > 0 else 0
        bar_info = []
        name_labels = []

        for i, (name, ace) in enumerate(page_data):
            x_data = i + 0.5
            rel_x = x_data / n
            rel_h = ace / y_max_val if y_max_val > 0 else 0
            x_px = rel_x * w
            bar_h_val = max(1, rel_h * h)
            y_px = h - bar_h_val
            br = pygame.Rect(int(x_px - bar_w // 2), int(y_px), int(bar_w), int(bar_h_val))
            pygame.draw.rect(chart_surf, (100, 150, 255), br)
            pygame.draw.rect(chart_surf, TXT, br, 1)

            # 数值标签
            vs = rt(f_s, f"{ace:.2f}", TXT)
            vy = br.y - 18 if br.y - 18 > 0 else br.y + 12
            chart_surf.blit(vs, (br.centerx - vs.get_width() // 2, vy))

            # 名称标签
            name_lines = _wrap_name_cached(name, 10)
            name_labels.append((name_lines, br.centerx))

            bar_info.append((br, f"{name}: ACE {ace:.4f}"))

        cached['chart_surf'] = chart_surf
        cached['bar_info'] = bar_info
        cached['bar_w'] = bar_w
        cached['name_labels'] = name_labels

        if len(_typhoon_ace_cache) >= _MAX_CACHE:
            _typhoon_ace_cache.pop(next(iter(_typhoon_ace_cache)))
        _typhoon_ace_cache[key] = cached
    else:
        cached = _typhoon_ace_cache[key]

    # ── 绘制 ──
    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    # 名称标签（在 rect 下方）
    for name_lines, center_x in cached['name_labels']:
        ny = rect.bottom + 5
        for line in name_lines:
            ls = rt(f_s, line, TXT)
            surface.blit(ls, (rect.x + center_x - ls.get_width() // 2, ny))
            ny += ls.get_height()

    # ── 悬停（不能提前 return，因为需要返回 3 个值） ──
    hover_info = None
    mx, my = pygame.mouse.get_pos()
    for br_local, hover_str in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        if br_screen.collidepoint(mx, my):
            hover_info = (hover_str, (mx + 15, my - 25))
            break

    if total_pages > 1:
        ps = rt(f_s, f"台风 {bar_page + 1}/{total_pages}", TXT)
        surface.blit(ps, (rect.centerx - ps.get_width() // 2, rect.bottom + 30))

    return hover_info, total_pages, total_pages > 1