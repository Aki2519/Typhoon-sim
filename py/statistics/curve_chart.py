# py/statistics/curve_chart.py
"""ACE 累计曲线图。使用预渲染缓存。"""
import pygame
from datetime import datetime
from typing import List, Tuple, Optional, Dict

from ..constants import TXT, f_s, rt
from .draw_helpers_chart import DASH_COLOR, _get_dashed_h_surface

CURVE_BLUE = (30, 100, 220)

# ── 缓存 ──
_curve_cache: Dict[Tuple, dict] = {}
_MAX_CACHE = 16


def _make_curve_cache_key(
    ace_curve_points: List[Tuple[datetime, float]],
    rect_size: Tuple[int, int],
    year_total_ace: float,
) -> Tuple:
    """生成缓存键：用数据 id + 尺寸 + 总 ACE。"""
    return (id(ace_curve_points), rect_size[0], rect_size[1], round(year_total_ace, 1))


def draw_curve_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    ace_curve_points: List[Tuple[datetime, float]],
    year_range: Tuple[datetime, datetime, int],
    year_total_ace: float,
    draw_dashed_h,
) -> Optional[Tuple[str, Tuple[int, int]]]:
    if not ace_curve_points:
        return None

    start_dt, end_dt, total_hours = year_range
    key = _make_curve_cache_key(ace_curve_points, (rect.width, rect.height), year_total_ace)

    if key not in _curve_cache:
        # ── 构建缓存 ──
        cached = {}
        x_min, x_max = 0, total_hours
        max_cum = max(p[1] for p in ace_curve_points)
        yt = float(year_total_ace or max_cum)
        y_min_val = -0.05 * yt
        y_max_val = max_cum * 1.1
        if y_max_val - y_min_val < 1:
            y_max_val = y_min_val + 10.0

        # 预渲染整个曲线区域
        w, h = rect.width, rect.height
        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        # 边框
        pygame.draw.rect(chart_surf, TXT, (0, 0, w, h), 2)

        # 刻度标签 + 虚线
        y_step = 50.0
        y_range = y_max_val - y_min_val
        tick_labels = []
        if y_range > 0:
            val = 0.0
            while val <= y_max_val:
                if val >= y_min_val:
                    rel = (val - y_min_val) / y_range
                    y_px = h - rel * h
                    pygame.draw.line(chart_surf, TXT, (0, int(y_px)), (5, int(y_px)), 1)
                    lbl = rt(f_s, f"{val:.0f}", TXT)
                    tick_labels.append((lbl, int(y_px)))
                    if abs(val) > 0.001:
                        dash_surf = _get_dashed_h_surface(w, DASH_COLOR)
                        chart_surf.blit(dash_surf, (0, int(y_px)))
                val += y_step

        cached['tick_labels'] = tick_labels
        cached['chart_surf'] = chart_surf

        # 计算屏幕坐标点
        def mapper(xv, yv):
            rx = (xv - x_min) / (x_max - x_min) if x_max != x_min else 0
            ry = (yv - y_min_val) / y_range if y_range != 0 else 0
            return rect.x + rx * w, rect.y + (h - ry * h)

        pts = []
        for dt, ace in ace_curve_points:
            hours = max(0, min((dt - start_dt).total_seconds() / 3600, total_hours))
            x, y = mapper(hours, ace)
            pts.append((int(x), int(y)))

        cached['pts'] = pts

        # 预渲染曲线
        curve_overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        curve_overlay.fill((0, 0, 0, 0))
        if len(pts) > 1:
            # 转换为本地坐标
            local_pts = [(px - rect.x, py - rect.y) for px, py in pts]
            pygame.draw.lines(curve_overlay, CURVE_BLUE, False, local_pts, 2)
        cached['curve_overlay'] = curve_overlay

        # 限制缓存大小
        if len(_curve_cache) >= _MAX_CACHE:
            _curve_cache.pop(next(iter(_curve_cache)))
        _curve_cache[key] = cached
    else:
        cached = _curve_cache[key]

    # ── 绘制缓存内容 ──
    surface.blit(cached['chart_surf'], rect.topleft)

    # 刻度标签（相对于 surface）
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    # 曲线
    surface.blit(cached['curve_overlay'], rect.topleft)

    # ── 悬停检测 ──
    mx, my = pygame.mouse.get_pos()
    for i, (px, py) in enumerate(cached['pts']):
        if abs(mx - px) + abs(my - py) < 10:
            dt, ace = ace_curve_points[i]
            return f"{dt.month}/{dt.day} {dt.hour:02d}Z: {ace:.2f}", (mx + 15, my - 25)
    return None