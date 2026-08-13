# py/statistics/typhoon_ace_chart.py
"""台风 ACE 柱状图。预渲染缓存。"""
from __future__ import annotations
import pygame
from typing import List, Tuple, Optional, Dict

from ..constants import f_s, rt
from .chart_helpers import chart_axis, chart_dark, nice_step, fmt_tick

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


_name_trunc_cache: dict = {}


def _fit_name_line(name: str, max_w: int) -> str:
    """单行像素宽度截断(省略号),名称/页码区在按钮行上方不换行(R2-5/T3-3)。"""
    if max_w <= 0:
        return name
    key = (name, max_w)
    cached = _name_trunc_cache.get(key)
    if cached is not None:
        return cached
    if rt(f_s, name, chart_axis()).get_width() <= max_w:
        result = name
    else:
        lo, hi = 1, len(name)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if rt(f_s, name[:mid] + "…", chart_axis()).get_width() <= max_w:
                lo = mid
            else:
                hi = mid - 1
        result = name[:lo] + "…"
    if len(_name_trunc_cache) > 512:
        _name_trunc_cache.pop(next(iter(_name_trunc_cache)))
    _name_trunc_cache[key] = result
    return result


_fit_name_line_cached = _fit_name_line


def draw_typhoon_ace_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    typhoon_sort_data: List[Tuple[str, float, int, Tuple[int, int, int], str]],
    bar_page: int = 0,
    sort_mode: int = 0,
) -> Tuple[Optional[Tuple[str, Tuple[int, int]]], int, bool]:
    total = len(typhoon_sort_data)
    total_pages = max(1, (total + BAR_MAX_PER_PAGE - 1) // BAR_MAX_PER_PAGE)
    # 页码越界时夹紧,避免缓存持续失配导致稳定空白页
    bar_page = max(0, min(bar_page, total_pages - 1))

    key = (id(typhoon_sort_data), bar_page, sort_mode, rect.width, rect.height, chart_dark())

    cached = _typhoon_ace_cache.get(key)
    if (cached is None or cached.get('src') is not typhoon_sort_data
            or cached.get('total') != total):
        # 排序/分页仅在缓存失配时执行（法3：每帧命中直接取 page_data）
        if sort_mode == 0:
            sorted_data = sorted(typhoon_sort_data, key=lambda x: (-x[1], x[0]))
        elif sort_mode == 1:
            sorted_data = sorted(typhoon_sort_data, key=lambda x: (x[4], x[0]))
        else:
            sorted_data = sorted(typhoon_sort_data, key=lambda x: (-x[2], x[0]))
        start = bar_page * BAR_MAX_PER_PAGE
        page_data = sorted_data[start:start + BAR_MAX_PER_PAGE]

        if not page_data:
            return None, total_pages, total_pages > 1

        cached = {'src': typhoon_sort_data, 'total': total, 'page_data': page_data}
        w, h = rect.width, rect.height
        n = len(page_data)
        max_ace = max((d[1] for d in sorted_data), default=1.0)
        y_max_val = max_ace * 1.15
        if y_max_val <= 0:
            y_max_val = 10.0

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))
        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)

        tick_labels = []
        y_tick = nice_step(y_max_val, 5)
        val = 0.0
        while val <= y_max_val:
            rel = val / y_max_val
            y_px = h - rel * h
            lbl = rt(f_s, fmt_tick(val), chart_axis())
            tick_labels.append((lbl, int(y_px)))
            val += y_tick
        cached['tick_labels'] = tick_labels

        bar_w = min(40, w / n * 0.7)
        bar_info = []
        name_labels = []

        for i, (name, ace_val, peak_wind, color, _) in enumerate(page_data):
            x_data = i + 0.5
            rel_x = x_data / n
            rel_h = ace_val / y_max_val if y_max_val > 0 else 0
            x_px = rel_x * w
            bar_h_val = max(1, rel_h * h)
            y_px = h - bar_h_val
            br = pygame.Rect(int(x_px - bar_w // 2), int(y_px), int(bar_w), int(bar_h_val))
            pygame.draw.rect(chart_surf, color[:3], br)
            pygame.draw.rect(chart_surf, chart_axis(), br, 1)

            vs = rt(f_s, f"{ace_val:.4f}", chart_axis())
            vy = br.y - 18 if br.y - 18 > 0 else br.y + 12
            chart_surf.blit(vs, (br.centerx - vs.get_width() // 2, vy))

            name_lines = _wrap_name_cached(name, 10)
            name_labels.append((name_lines, br.centerx))

            bar_info.append((br, f"{name}: ACE {ace_val:.4f}  巅峰 {peak_wind}kt"))

        cached['chart_surf'] = chart_surf
        cached['bar_info'] = bar_info
        cached['bar_w'] = bar_w
        cached['name_labels'] = name_labels
        if len(_typhoon_ace_cache) >= _MAX_CACHE:
            _typhoon_ace_cache.pop(next(iter(_typhoon_ace_cache)))
        _typhoon_ace_cache[key] = cached

    # ── 绘制 ──
    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    # 名称标签(单行,按钮行上方;超宽按柱宽截断,R2-5/T3-3)
    name_y = rect.bottom + 4
    name_bottom = name_y
    for name_lines, center_x in cached['name_labels']:
        nm = _fit_name_line_cached(name_lines[0], max(12, cached['bar_w'] - 10))
        ls = rt(f_s, nm, chart_axis())
        surface.blit(ls, (rect.x + center_x - ls.get_width() // 2, name_y))
        name_bottom = max(name_bottom, name_y + ls.get_height())

    # ── 悬停（不能提前 return，因为需要返回 3 个值） ──
    hover_info = None
    mx, my = pygame.mouse.get_pos()
    for br_local, hover_str in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        if br_screen.collidepoint(mx, my):
            hover_info = (hover_str, (mx + 15, my - 25))
            break

    if total_pages > 1:
        # 页码紧随名称行(按钮行上方)
        ps = rt(f_s, f"台风 {bar_page + 1}/{total_pages}", chart_axis())
        surface.blit(ps, (rect.centerx - ps.get_width() // 2, name_bottom + 6))

    return hover_info, total_pages, total_pages > 1