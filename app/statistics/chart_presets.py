# py/statistics/chart_presets.py
from __future__ import annotations
import math
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict

from ..constants import f_s, rt
from .chart_helpers import (_get_dashed_h_surface,
                            chart_axis, chart_ink, chart_dash, chart_dark,
                            nice_step, fmt_tick,
                            draw_dashed_v)


# ════════════════════ 每日 ACE 柱状图 ════════════════════

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

    key = (id(daily_ace_list), rect.width, rect.height, chart_dark())

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

        bw_line = 2
        pygame.draw.line(chart_surf, chart_axis(), (0, 0), (0, h), bw_line)
        pygame.draw.line(chart_surf, chart_axis(), (w - 1, 0), (w - 1, h), bw_line)
        pygame.draw.line(chart_surf, chart_axis(), (0, h - 1), (w, h - 1), bw_line)

        tick_labels = []
        y_tick_step = nice_step(y_max, 4)
        val = 0.0
        while val <= y_max:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, fmt_tick(val), chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
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

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))
    for al, lx, ly in cached.get('max_labels', []):
        surface.blit(al, (rect.x + lx, rect.y + ly))

    mx, my = pygame.mouse.get_pos()
    if rect.collidepoint(mx, my):
        day_i = int((mx - rect.x) / cached['bar_w'])
        if 0 <= day_i < cached['n_days']:
            _, ace = cached['daily_ace_list'][day_i]
            dt = cached['start_dt'] + timedelta(days=day_i)
            return f"{dt.month}/{dt.day}: {ace:.4f}", (mx + 15, my - 25)
    return None


# ════════════════════ 活跃台风数柱状图 ════════════════════

ACTIVITY_BAR_COLOR = (0, 200, 180)
ACTIVITY_BAR_MAX_COLOR = (220, 40, 40)

_activity_cache: Dict[Tuple, dict] = {}


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

    key = (id(activity_count_list), rect.width, rect.height, chart_dark())

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
            color = ACTIVITY_BAR_MAX_COLOR if (i == max_i and cnt > 0) else ACTIVITY_BAR_COLOR
            br = pygame.Rect(int(x_px), int(bar_y), max(1, int(math.ceil(bar_w))), int(bar_h_val))
            pygame.draw.rect(chart_surf, color, br)

        tick_labels = []
        y_tick_step = max(1.0, round(nice_step(y_max, 4)))
        val = 0.0
        while val <= y_max:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, f"{val:.0f}", chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
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

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    mx, my = pygame.mouse.get_pos()
    if rect.collidepoint(mx, my):
        day_i = int((mx - rect.x) / cached['bar_w'])
        n_days = cached['n_days']
        if 0 <= day_i < n_days:
            _, cnt = cached['activity_count_list'][day_i]
            dt = cached['start_dt'] + timedelta(days=day_i)
            return f"{dt.month}/{dt.day}: {cnt}个活跃台风", (mx + 15, my - 25)
    return None


# ════════════════════ 活跃周期图 ════════════════════

_active_periods_cache: Dict[Tuple, dict] = {}


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
    key = (id(active_periods), rect.width, rect.height, round(bw, 2), chart_dark())

    cached = _active_periods_cache.get(key)
    if cached is None or cached.get('src') is not active_periods:
        cached = {'src': active_periods}
        w, h = rect.width, rect.height
        n_periods = len(active_periods)
        n_mid = max(25, n_periods)
        pad_edge = 15
        zone_h = (h - 2 * pad_edge) / n_mid
        bar_h = max(2.0, min(14.0, zone_h - 2))
        bar_pad = 2

        start_dt, _, total_hours = year_range

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        bar_info = []
        name_entries = []

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

            pygame.draw.rect(chart_surf, chart_ink(), bar_rect, 1)

            nk = 'name_surf_d' if chart_dark() else 'name_surf'
            name_surf = period.get(nk)
            if name_surf is None:
                name_surf = rt(f_s, period['name_str'], chart_ink())
                period[nk] = name_surf
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

    surface.blit(cached['chart_surf'], rect.topleft)

    for name_surf, bar_left, bar_cy in cached['name_entries']:
        nx = rect.x + bar_left - name_surf.get_width() - 3
        ny = rect.y + bar_cy - name_surf.get_height() // 2
        surface.blit(name_surf, (nx, ny))

    mx, my = pygame.mouse.get_pos()
    hover_result = None
    click_targets = []
    for br_local, hover_str, period in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        click_targets.append((br_screen, period))
        if br_screen.collidepoint(mx, my):
            hover_result = (hover_str, (mx + 15, my - 25))
    return hover_result, click_targets


# ════════════════════ ACE 累计曲线图 ════════════════════

CURVE_BLUE = (30, 100, 220)

_curve_cache: Dict[Tuple, dict] = {}


def _make_curve_cache_key(
    ace_curve_points: List[Tuple[datetime, float]],
    rect_size: Tuple[int, int],
    year_total_ace: float,
) -> Tuple:
    return (id(ace_curve_points), rect_size[0], rect_size[1], round(year_total_ace, 1), chart_dark())


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

    cached = _curve_cache.get(key)
    if cached is None or cached.get('src') is not ace_curve_points:
        cached = {'src': ace_curve_points}
        x_min, x_max = 0, total_hours
        max_cum = max(p[1] for p in ace_curve_points)
        yt = float(year_total_ace or max_cum)
        y_min_val = -0.05 * yt
        y_max_val = max_cum * 1.1
        if y_max_val - y_min_val < 1:
            y_max_val = y_min_val + 10.0

        w, h = rect.width, rect.height
        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)

        y_step = nice_step(y_max_val, 5)
        y_range = y_max_val - y_min_val
        tick_labels = []
        if y_range > 0:
            val = 0.0
            while val <= y_max_val:
                if val >= y_min_val:
                    rel = (val - y_min_val) / y_range
                    y_px = h - rel * h
                    pygame.draw.line(chart_surf, chart_axis(), (0, int(y_px)), (5, int(y_px)), 1)
                    lbl = rt(f_s, fmt_tick(val), chart_axis())
                    tick_labels.append((lbl, int(y_px)))
                    if abs(val) > 0.001:
                        dash_surf = _get_dashed_h_surface(w, chart_dash())
                        chart_surf.blit(dash_surf, (0, int(y_px)))
                val += y_step

        cached['tick_labels'] = tick_labels
        cached['chart_surf'] = chart_surf

        def mapper(xv, yv):
            rx = (xv - x_min) / (x_max - x_min) if x_max != x_min else 0
            ry = (yv - y_min_val) / y_range if y_range != 0 else 0
            return rx * w, h - ry * h

        pts = []
        for dt, ace in ace_curve_points:
            hours = max(0, min((dt - start_dt).total_seconds() / 3600, total_hours))
            x, y = mapper(hours, ace)
            pts.append((int(x), int(y)))

        cached['pts'] = pts

        curve_overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        curve_overlay.fill((0, 0, 0, 0))
        if len(pts) > 1:
            pygame.draw.lines(curve_overlay, CURVE_BLUE, False, pts, 2)
        cached['curve_overlay'] = curve_overlay

        if len(_curve_cache) >= _MAX_CACHE:
            _curve_cache.pop(next(iter(_curve_cache)))
        _curve_cache[key] = cached

    surface.blit(cached['chart_surf'], rect.topleft)

    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))

    surface.blit(cached['curve_overlay'], rect.topleft)

    # 悬停数值显示由十字线（_draw_crosshair）统一提供，此处不再返回提示
    return None


# ════════════════════ 多年度 ACE 柱状图 ════════════════════

YEARLY_BAR_COLOR = (60, 140, 220)
YEARLY_BAR_CURRENT = (220, 140, 40)

_yearly_ace_cache: Dict[Tuple, dict] = {}
_MAX_YEARLY_CACHE = 8


def draw_yearly_ace_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    year_ace_pairs: List[Tuple[int, float]],
    current_year: int = 0,
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """年度 ACE 柱状图。横轴年份，纵轴 ACE 总量。"""
    if not year_ace_pairs:
        return None

    key = (id(year_ace_pairs), rect.width, rect.height, current_year, chart_dark())
    cached = _yearly_ace_cache.get(key)
    if cached is None or cached.get('src') is not year_ace_pairs:
        w, h = rect.width, rect.height
        max_ace = max(a for _, a in year_ace_pairs) if year_ace_pairs else 1.0
        y_max = max_ace * 1.15
        step = nice_step(y_max, 5)

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))
        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)

        tick_labels = []
        val = 0.0
        while val <= y_max + step * 0.01:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, f"{val:.0f}" if val < 100 else fmt_tick(val), chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
                chart_surf.blit(dash_surf, (0, int(y_px)))
            val += step

        n_years = len(year_ace_pairs)
        bar_gap = max(6, w // (n_years * 4))
        bar_w = max(1.0, (w - bar_gap * (n_years + 1)) / n_years)   # N23: 年数过多时柱宽不为负
        bar_info = []

        for i, (year, ace) in enumerate(year_ace_pairs):
            x_data = bar_gap + i * (bar_w + bar_gap)
            rel_h = ace / y_max if y_max > 0 else 0
            bar_h_val = max(1, rel_h * h)
            y_px = h - bar_h_val
            color = YEARLY_BAR_CURRENT if year == current_year else YEARLY_BAR_COLOR
            br = pygame.Rect(int(x_data), int(y_px), max(1, int(bar_w)), int(bar_h_val))
            pygame.draw.rect(chart_surf, color, br)
            pygame.draw.rect(chart_surf, chart_axis(), br, 1)
            bar_info.append((br, f"{year}: {ace:.4f}"))

            # 年份标签（柱下）
            yl = rt(f_s, str(year), chart_axis())
            lx = int(x_data + bar_w / 2 - yl.get_width() / 2)
            ly = h - yl.get_height() - 2
            if ly > 0:
                chart_surf.blit(yl, (lx, max(4, ly)))

        for lbl, y_px in tick_labels:
            pygame.draw.line(chart_surf, chart_axis(), (0, y_px), (4, y_px), 1)

        cached = {
            'src': year_ace_pairs, 'chart_surf': chart_surf, 'tick_labels': tick_labels,
            'bar_info': bar_info, 'bar_w': bar_w, 'bar_gap': bar_gap,
        }
        if len(_yearly_ace_cache) >= _MAX_YEARLY_CACHE:
            _yearly_ace_cache.pop(next(iter(_yearly_ace_cache)))
        _yearly_ace_cache[key] = cached

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 8, rect.y + y_px - lbl.get_height() // 2))

    mx, my = pygame.mouse.get_pos()
    for br_local, hover_str in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        if br_screen.collidepoint(mx, my):
            return hover_str, (mx + 15, my - 25)
    return None


# ════════════════════ 多年累积 ACE 曲线叠加 ════════════════════

_MULTI_CURVE_COLORS = [
    (30, 100, 220), (220, 60, 60), (0, 160, 80),
    (220, 140, 0), (140, 60, 180), (0, 180, 180),
    (220, 100, 50), (50, 200, 50), (0, 0, 200),
    (200, 200, 20), (200, 20, 200), (20, 200, 200),
    (100, 100, 255), (255, 100, 100), (100, 255, 100),
]

_multi_curve_cache: Dict[Tuple, dict] = {}


def draw_multi_year_curve_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    curves: List[Tuple[int, List[Tuple[datetime, float]], float, Tuple[datetime, datetime, int]]],
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """多条年度累积 ACE 曲线叠加。
    curves = [(year, [(dt, ace), ...], total_ace, (start_dt, end_dt, total_hours)), ...]"""
    if not curves:
        return None

    w, h = rect.width, rect.height
    key = (id(curves), w, h, chart_dark())
    cached = _multi_curve_cache.get(key)
    if cached is None or cached.get('src') is not curves:
        cached = {'src': curves}

        all_max = max((max(p[1] for p in cv[1]) for cv in curves if cv[1]), default=0.0)
        if all_max <= 0:
            cached['empty'] = True
            if len(_multi_curve_cache) >= _MAX_CACHE:
                _multi_curve_cache.pop(next(iter(_multi_curve_cache)))
            _multi_curve_cache[key] = cached
            return None
        y_max_val = all_max * 1.1
        if y_max_val < 10:
            y_max_val = 10.0

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))
        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)

        y_step = nice_step(y_max_val, 5)
        tick_labels = []
        val = 0.0
        while val <= y_max_val:
            rel = val / y_max_val
            y_px = h - rel * h
            pygame.draw.line(chart_surf, chart_axis(), (0, int(y_px)), (5, int(y_px)), 1)
            lbl = rt(f_s, f"{val:.0f}", chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
                chart_surf.blit(dash_surf, (0, int(y_px)))
            val += y_step

        # 找到最长的时间跨度作为 x 轴基准
        max_total_hours = max(cv[3][2] for cv in curves)
        # 按日历对齐：所有曲线按统一日历来映射 x 坐标
        global_start = min(cv[3][0] for cv in curves)

        curve_overlays = []
        legend_info = []

        for ci, (year, pts, total_ace, (start_dt, end_dt, total_hours)) in enumerate(curves):
            if not pts:
                continue
            color = _MULTI_CURVE_COLORS[ci % len(_MULTI_CURVE_COLORS)]

            # 按日历对齐映射
            local_pts = []
            inv_ymax = 1.0 / y_max_val if y_max_val > 0 else 0.0
            inv_max_th = 1.0 / max_total_hours if max_total_hours > 0 else 0.0
            for dt, ace in pts:
                ho = (dt - global_start).total_seconds() / 3600
                x_px = ho * inv_max_th * w
                rel_y = ace * inv_ymax
                y_px = h - rel_y * h
                local_pts.append((int(x_px), int(y_px)))

            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 0))
            if len(local_pts) > 1:
                pygame.draw.lines(overlay, color, False, local_pts, 2)
            curve_overlays.append(overlay)

            # 图例项
            display_fmt = f"{total_ace:.0f}" if total_ace < 1000 else fmt_tick(total_ace)
            legend_info.append((color, f"{year}: {display_fmt}"))

        cached['chart_surf'] = chart_surf
        cached['tick_labels'] = tick_labels
        cached['curve_overlays'] = curve_overlays
        cached['legend_info'] = legend_info
        cached['curves'] = curves
        cached['global_start'] = global_start
        cached['max_total_hours'] = max_total_hours

        # 法11: 月份线+标签烘培进 overlay(相对坐标),每帧只 blit
        month_overlay = pygame.Surface((w, h + 22), pygame.SRCALPHA)
        month_overlay.fill((0, 0, 0, 0))
        inv_max_th = 1.0 / max_total_hours if max_total_hours > 0 else 0.0
        first = global_start.year * 12 + (global_start.month - 1)
        last = first + int(max_total_hours / (24 * 28)) + 3
        for k in range(first, last + 1):
            yr, mo = divmod(k, 12)
            mo += 1
            ms = datetime(yr, mo, 1, 0)
            if ms < global_start:
                continue
            ho = (ms - global_start).total_seconds() / 3600
            if ho > max_total_hours:
                break
            x_px = ho * inv_max_th * w
            draw_dashed_v(month_overlay, x_px, 0, h, chart_dash(), 4, 4)
            ml = rt(f_s, f"{mo:02d}/01", chart_axis())
            month_overlay.blit(ml, (x_px - ml.get_width() // 2, h + 2))
        cached['month_overlay'] = month_overlay

        if len(_multi_curve_cache) >= _MAX_CACHE:
            _multi_curve_cache.pop(next(iter(_multi_curve_cache)))
        _multi_curve_cache[key] = cached

    if cached.get('empty'):
        return None

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 10, rect.y + y_px - lbl.get_height() // 2))
    for overlay in cached['curve_overlays']:
        surface.blit(overlay, rect.topleft)

    # 图例
    ly = rect.bottom + 5
    lx = rect.x + 8
    for color, text in cached.get('legend_info', []):
        ls = rt(f_s, text, chart_axis())
        line_x = lx
        pygame.draw.line(surface, color, (line_x, ly + ls.get_height() // 2),
                         (line_x + 12, ly + ls.get_height() // 2), 2)
        surface.blit(ls, (line_x + 16, ly))
        lx += 16 + ls.get_width() + 20
        if lx > rect.right - 60:
            lx = rect.x + 8
            ly += ls.get_height() + 4

    # 月份线(烘培缓存,法11)
    if 'month_overlay' in cached:
        surface.blit(cached['month_overlay'], rect.topleft)

    mx, my = pygame.mouse.get_pos()
    if rect.collidepoint(mx, my):
        return f"鼠标悬停查看各年 ACE 曲线", (mx + 15, my - 25)
    return None


# ════════════════════ 月度统计柱状图 ════════════════════

MONTH_NAMES = ['1月', '2月', '3月', '4月', '5月', '6月',
               '7月', '8月', '9月', '10月', '11月', '12月']

_monthly_cache: Dict[Tuple, dict] = {}


def draw_monthly_bars_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    data: List[Tuple[int, float]],
    bar_color: Tuple[int, int, int] = (70, 130, 220),
    y_label: str = "",
    month_labels: Optional[List[str]] = None,
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """12 个月份的柱状图。data = [(month_index, value), ...]，1-12 索引。
    month_labels 非空时(南半球)用它替换第 i 根柱的标签(N21)。"""
    filled = {m: v for m, v in data}
    pairs = [(m, filled.get(m, 0)) for m in range(1, 13)]

    w, h = rect.width, rect.height
    key = (id(data), w, h, bar_color, chart_dark())
    cached = _monthly_cache.get(key)
    if cached is None or cached.get('src') is not data:
        cached = {'src': data}
        max_val = max(v for _, v in pairs) if pairs else 1.0
        if max_val <= 0:
            max_val = 1.0
        y_max = max_val * 1.15
        step = nice_step(y_max, 4)

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        tick_labels = []
        val = 0.0
        while val <= y_max + step * 0.01:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, f"{val:.0f}", chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
                chart_surf.blit(dash_surf, (0, int(y_px)))
                pygame.draw.line(chart_surf, chart_axis(), (0, int(y_px)), (4, int(y_px)), 1)
            val += step

        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)
        pygame.draw.line(chart_surf, chart_axis(), (0, h), (w, h), 1)

        n = 12
        bar_gap = max(6, w // (n * 4))
        bar_w = (w - bar_gap * (n + 1)) / n
        bar_info = []

        for i, (m, val) in enumerate(pairs):
            x_data = bar_gap + i * (bar_w + bar_gap)
            rel_h = val / y_max if y_max > 0 else 0
            bar_h_val = max(1, rel_h * h)
            y_px = h - bar_h_val
            br = pygame.Rect(int(x_data), int(y_px), max(1, int(bar_w)), int(bar_h_val))
            pygame.draw.rect(chart_surf, bar_color, br)
            pygame.draw.rect(chart_surf, chart_axis(), br, 1)
            bar_info.append((br, f"{(month_labels[i] if month_labels else MONTH_NAMES[i])}: {val:.0f}"))
            ml = rt(f_s, month_labels[i] if month_labels else str(m), chart_axis())
            lx = int(x_data + bar_w / 2 - ml.get_width() / 2)
            ly = h - ml.get_height() - 2
            if ly > 0:
                chart_surf.blit(ml, (lx, max(2, ly)))

        cached['chart_surf'] = chart_surf
        cached['tick_labels'] = tick_labels
        cached['bar_info'] = bar_info
        if len(_monthly_cache) >= _MAX_CACHE:
            _monthly_cache.pop(next(iter(_monthly_cache)))
        _monthly_cache[key] = cached

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 8, rect.y + y_px - lbl.get_height() // 2))

    # 单位标签
    if y_label:
        yl = rt(f_s, y_label, chart_axis())
        surface.blit(yl, (rect.x + 5, rect.y + 2))

    mx, my = pygame.mouse.get_pos()
    for br_local, hover_str in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        if br_screen.collidepoint(mx, my):
            return hover_str, (mx + 15, my - 25)
    return None


# ════════════════════ 洋区统计对比图 ════════════════════

_basin_cache: Dict[Tuple, dict] = {}


def draw_basin_comparison_chart(
    surface: pygame.Surface,
    rect: pygame.Rect,
    basin_data: List[Tuple[str, float, Tuple[int, int, int]]],
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """洋区 ACE 对比柱状图。basin_data = [(name, value, color), ...]"""
    if not basin_data:
        return None

    w, h = rect.width, rect.height
    key = (id(basin_data), w, h, chart_dark())
    cached = _basin_cache.get(key)
    if cached is None or cached.get('src') is not basin_data:
        cached = {'src': basin_data}
        max_val = max(v for _, v, _ in basin_data)
        if max_val <= 0:
            max_val = 1.0
        y_max = max_val * 1.15
        step = nice_step(y_max, 5)

        chart_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        chart_surf.fill((0, 0, 0, 0))

        tick_labels = []
        val = 0.0
        while val <= y_max + step * 0.01:
            rel = val / y_max
            y_px = h - rel * h
            lbl = rt(f_s, fmt_tick(val), chart_axis())
            tick_labels.append((lbl, int(y_px)))
            if val > 0.001:
                dash_surf = _get_dashed_h_surface(w, chart_dash())
                chart_surf.blit(dash_surf, (0, int(y_px)))
            val += step

        pygame.draw.rect(chart_surf, chart_axis(), (0, 0, w, h), 2)

        n = len(basin_data)
        bar_gap = max(10, w // (n * 4))
        bar_w = (w - bar_gap * (n + 1)) / n
        bar_info = []

        for i, (name, val, color) in enumerate(basin_data):
            x_data = bar_gap + i * (bar_w + bar_gap)
            rel_h = val / y_max if y_max > 0 else 0
            bar_h_val = max(1, rel_h * h)
            y_px = h - bar_h_val
            br = pygame.Rect(int(x_data), int(y_px), max(1, int(bar_w)), int(bar_h_val))
            pygame.draw.rect(chart_surf, color, br)
            pygame.draw.rect(chart_surf, chart_axis(), br, 1)
            bar_info.append((br, f"{name}: {val:.4f}"))

            # 名称标签（柱下，竖排）
            nl = rt(f_s, name, chart_axis())
            nx = int(x_data + bar_w / 2 - nl.get_width() / 2)
            ny = h - nl.get_height() - 2
            if ny > 0:
                chart_surf.blit(nl, (nx, max(2, ny)))

        for lbl, y_px in tick_labels:
            pygame.draw.line(chart_surf, chart_axis(), (0, y_px), (4, y_px), 1)

        cached['chart_surf'] = chart_surf
        cached['tick_labels'] = tick_labels
        cached['bar_info'] = bar_info
        if len(_basin_cache) >= _MAX_CACHE:
            _basin_cache.pop(next(iter(_basin_cache)))
        _basin_cache[key] = cached

    surface.blit(cached['chart_surf'], rect.topleft)
    for lbl, y_px in cached['tick_labels']:
        surface.blit(lbl, (rect.x - lbl.get_width() - 8, rect.y + y_px - lbl.get_height() // 2))

    mx, my = pygame.mouse.get_pos()
    for br_local, hover_str in cached['bar_info']:
        br_screen = br_local.move(rect.x, rect.y)
        if br_screen.collidepoint(mx, my):
            return hover_str, (mx + 15, my - 25)
    return None
