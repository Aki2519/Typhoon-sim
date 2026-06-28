# py/statistics/draw_helpers_chart.py
"""绘制辅助：虚线、月份线。使用预渲染 Surface 缓存。"""
from __future__ import annotations
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Dict

from ..constants import HEMISPHERE_NORTH

DASH_COLOR = (180, 180, 200, 80)

# ── 虚线水平线缓存 ──
_dashed_h_cache: Dict[Tuple[int, int], pygame.Surface] = {}


def _get_dashed_h_surface(width: int, color: Tuple, dash: int = 6, gap: int = 4) -> pygame.Surface:
    """返回一条预渲染的虚线水平线 Surface（宽 width，高 1px）。"""

    key = (width, dash, gap)
    if key in _dashed_h_cache:
        return _dashed_h_cache[key]
    surf = pygame.Surface((width, 1), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    step = dash + gap
    for i in range(max(1, width // step + 1)):
        sx = i * step
        ex = min(sx + dash, width)
        if sx < width:
            surf.fill(color, (int(sx), 0, int(ex - sx), 1))
    _dashed_h_cache[key] = surf
    # 限制缓存大小
    if len(_dashed_h_cache) > 64:
        _dashed_h_cache.pop(next(iter(_dashed_h_cache)))
    return surf


def draw_dashed_v(surface: pygame.Surface, x: float, y1: float, y2: float,
                  color: Tuple, dash: int = 6, gap: int = 4):
    total = y2 - y1
    if total <= 0:
        return
    step = dash + gap
    for i in range(max(1, int(total / step))):
        sy = y1 + i * step
        ey = min(sy + dash, y2)
        pygame.draw.line(surface, color, (int(x), int(sy)), (int(x), int(ey)), 1)


def draw_dashed_h(surface: pygame.Surface, x1: float, x2: float, y: float,
                  color: Tuple, dash: int = 6, gap: int = 4):
    """使用预渲染虚线 Surface 快速绘制水平虚线。"""
    width = int(x2 - x1)
    if width <= 0 or y < 0:
        return
    dash_surf = _get_dashed_h_surface(width, color, dash, gap)
    surface.blit(dash_surf, (int(x1), int(y)))


# ── 月份线缓存 ──
_month_lines_cache: Dict[Tuple, Tuple[pygame.Surface, List[float], List[str]]] = {}


def build_month_lines_surface(
    width: int, height: int,
    start_dt: datetime, total_hours: int, hemisphere: str,
) -> Tuple[pygame.Surface, List[float], List[str]]:
    """构建月份分隔线 Surface（带缓存）。"""
    # 用年份+半球作为缓存键的一部分（月份位置与具体年份无关，只与半球和 total_hours 有关）
    cache_key = (width, height, start_dt.year, total_hours, hemisphere)
    if cache_key in _month_lines_cache:
        return _month_lines_cache[cache_key]

    xs: List[float] = []
    labels: List[str] = []
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    if width <= 0 or height <= 0:
        _month_lines_cache[cache_key] = (surf, xs, labels)
        return surf, xs, labels

    for m in range(1, 13):
        if hemisphere == HEMISPHERE_NORTH:
            ms = datetime(start_dt.year, m, 1, 0)
        else:
            yr = start_dt.year
            ms = datetime(yr, m, 1, 0) if m >= 7 else datetime(yr + 1, m, 1, 0)
        if ms < start_dt:
            continue
        if ms > (start_dt + timedelta(hours=total_hours)):
            continue
        ho = (ms - start_dt).total_seconds() / 3600
        x_px = (ho / total_hours) * width
        draw_dashed_v(surf, x_px, 0, height, DASH_COLOR)
        xs.append(x_px)
        labels.append(f"{m:02d}/01")

    # 限制缓存大小
    if len(_month_lines_cache) > 32:
        _month_lines_cache.pop(next(iter(_month_lines_cache)))
    _month_lines_cache[cache_key] = (surf, xs, labels)
    return surf, xs, labels