# py/statistics/chart_helpers.py
from __future__ import annotations
import logging
import math
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional

from ..constants import (
    f_s, rt, TXT, BUTTON_BORDER,
    HEMISPHERE_NORTH,
    ACE_CHART_DEFAULT_WIDTH, ACE_CHART_DEFAULT_HEIGHT,
    ACE_CHART_PADDING_LEFT, ACE_CHART_PADDING_RIGHT, ACE_CHART_PADDING_TOP,
)

logger = logging.getLogger(__name__)

DASH_COLOR = (180, 180, 200, 80)
WINDOW_WIDTH_SCALE = 1.2

# ════════════════════ 图表主题 ════════════════════

_chart_dark = {'on': False}


def set_chart_dark(on: bool) -> None:
    _chart_dark['on'] = bool(on)


def chart_dark() -> bool:
    return _chart_dark['on']


def chart_axis():
    """轴线/边框/刻度文字颜色。"""
    return (208, 216, 234) if _chart_dark['on'] else TXT


def chart_ink():
    """名称/描边等前景色。"""
    return (232, 236, 246) if _chart_dark['on'] else (0, 0, 0)


def chart_dash():
    """网格虚线颜色。"""
    return (255, 255, 255, 55) if _chart_dark['on'] else DASH_COLOR

# ════════════════════ 刻度计算 ════════════════════


def nice_step(max_val: float, target_ticks: int = 5) -> float:
    """返回 1/2/2.5/5 × 10^k 形式的美观刻度步长。"""
    if max_val <= 0 or target_ticks <= 0:
        return 1.0
    raw = max_val / target_ticks
    mag = 10.0 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = m * mag
        if raw <= step:
            return step
    return 10.0 * mag


def fmt_tick(val: float) -> str:
    """刻度标签格式化：整数不带小数，小数去除多余 0。"""
    return f"{val:g}"

# ════════════════════ 通用控件 ════════════════════


def draw_vscrollbar(surface: pygame.Surface, x: int, y: int, track_h: int,
                    total: float, visible: float, offset: float,
                    width: int = 6, dark: Optional[bool] = None,
                    show_track: bool = False, radius: int = 3) -> Optional[pygame.Rect]:
    """统一垂直滚动条。total/visible/offset 单位一致即可（像素或行数）。
    返回滑块 Rect（供拖拽命中检测），无需滚动时返回 None。"""
    if total <= visible or track_h <= 0:
        return None
    if dark is None:
        dark = chart_dark()
    if show_track:
        pygame.draw.rect(surface, (55, 62, 78) if dark else (200, 210, 220),
                         pygame.Rect(x, y, width, track_h), 0, radius)
    thumb_h = max(16, int(track_h * visible / total))
    avail = track_h - thumb_h
    ratio = offset / (total - visible)
    thumb = pygame.Rect(x, y + int(ratio * avail), width, thumb_h)
    pygame.draw.rect(surface, (110, 120, 140) if dark else (120, 150, 190),
                     thumb, 0, radius)
    return thumb


def draw_tooltip(surface: pygame.Surface, text: str, pos: Tuple[int, int],
                 screen_w: int, dark: Optional[bool] = None) -> None:
    """统一悬停提示框（显示在光标上方，越界自动翻转/收拢）。"""
    if dark is None:
        dark = chart_dark()
    tip = rt(f_s, text, (232, 236, 246) if dark else TXT)
    tw, th = tip.get_width() + 12, tip.get_height() + 10
    x = pos[0] + 15
    y = pos[1] - 25
    if y - th < 0:
        y = pos[1] + 15 + th
    if x + tw > screen_w:
        x = screen_w - tw - 5
    if x < 0:
        x = 5
    if y > surface.get_height() - th:
        y = surface.get_height() - th - 5
    tb = pygame.Surface((tw, th), pygame.SRCALPHA)
    if dark:
        tb.fill((25, 30, 44, 240))
        pygame.draw.rect(tb, (80, 110, 160), (0, 0, tw, th), 1, 4)
    else:
        tb.fill((255, 255, 255, 235))
        pygame.draw.rect(tb, BUTTON_BORDER, (0, 0, tw, th), 1, 4)
    tb.blit(tip, (6, 5))
    surface.blit(tb, (x, y - th))


def draw_fill_bands(surf: pygame.Surface, bands,
                    chart_left: int, chart_top: int, ch_w: int, ch_h: int,
                    y_max: float, y_min: float = 0.0, alpha: int = 65) -> None:
    """强度分级半透明填充带。bands = [(y_lower, y_upper, color), ...]"""
    span = y_max - y_min
    if span <= 0 or ch_w <= 0 or ch_h <= 0:
        return
    inv_span = 1.0 / span
    for y_lower, y_upper, color in bands:
        if y_lower >= y_max:
            continue
        y_upper_c = min(y_upper, y_max)
        if y_upper_c <= y_lower:
            continue
        rel_top = max(0.0, min(1.0, (y_upper_c - y_min) * inv_span))
        rel_bottom = max(0.0, min(1.0, (y_lower - y_min) * inv_span))
        fy = chart_top + ch_h - int(rel_top * ch_h)
        fh = max(1, int((rel_top - rel_bottom) * ch_h))
        fill = pygame.Surface((ch_w, fh), pygame.SRCALPHA)
        fill.fill((color[0], color[1], color[2], alpha))
        surf.blit(fill, (chart_left, fy))


def render_map_subset(surf: pygame.Surface, sim,
                      mlon: float, Mlon: float, mlat: float, Mlat: float,
                      bx: int, by: int, bw: int, bh: int, alpha: int = 200) -> None:
    """把全球底图按经纬度范围裁剪缩放后贴到 surf 上。
    K19: 经度归一到 0-360 制,跨 0 度线(如 350°E-10°E)的窗口两段拼接。"""
    try:
        orig = sim.map_mgr.map_view.original_img
        iw, ih = orig.get_size()
        mlon = mlon % 360.0
        Mlon = Mlon % 360.0
        if Mlon <= mlon:
            Mlon += 360.0
        ix1 = int(mlon / 360 * iw)
        ix2 = int(Mlon / 360 * iw)
        iy1 = int((90 - Mlat) / 180 * ih)
        iy2 = int((90 - mlat) / 180 * ih)
        iy1, iy2 = max(0, min(iy1, ih)), max(0, min(iy2, ih))
        if ix2 <= ix1 or iy2 <= iy1:
            return
        if ix2 <= iw:
            sub = orig.subsurface(pygame.Rect(ix1, iy1, ix2 - ix1, iy2 - iy1))
            scaled = pygame.transform.smoothscale(sub, (bw, bh))
            scaled.set_alpha(alpha)
            surf.blit(scaled, (bx, by))
        else:
            # 跨 0 度线: 右段(ix1..iw) + 左段(0..ix2-iw) 拼接
            # K-回归: 拼接面板宽应为两段总宽 (iw-ix1)+(ix2-iw)=ix2-ix1,
            # 原先只用 seg1_w 做面板宽,导致左段(0..ix2-iw)被裁在面板外丢失。
            seg2_w = ix2 - iw        # 左段(跨 0 度后的 0..ix2-iw)宽
            sub1 = orig.subsurface(pygame.Rect(ix1, iy1, iw - ix1, iy2 - iy1))
            sub2 = orig.subsurface(pygame.Rect(0, iy1, seg2_w, iy2 - iy1))
            panel = pygame.Surface((ix2 - ix1, iy2 - iy1), pygame.SRCALPHA)
            panel.blit(sub1, (0, 0))
            panel.blit(sub2, (iw - ix1, 0))
            scaled = pygame.transform.smoothscale(panel, (bw, bh))
            scaled.set_alpha(alpha)
            surf.blit(scaled, (bx, by))
    except Exception as e:
        # 底图裁剪/缩放失败(尺寸异常等)时跳过该底图层, 但必须留痕:
        # 仅由 heatmap/path_comparison 的 _render 缓存路径调用, 不会每帧触发
        logger.warning("底图裁剪失败: %s", e)


def build_basin_order(sim) -> dict:
    """洋区代码 → 排序索引（复刻台风列表排序）。"""
    areas = getattr(getattr(sim, 'res_mgr', None), 'ocean_areas', None)
    if areas and areas.areas:
        return {a.code: i for i, a in enumerate(areas.areas)}
    return {}

# ════════════════════ 绘图辅助 ════════════════════


def _haversine_chain(lat2: float, lon2: float,
                     prev_lat: float, prev_lon: float, prev_state: tuple) -> tuple:
    """链式 haversine:复用上一端点的弧度/余弦,返回 (km, state)。

    经度差先归一化到 (-180, 180],避免跨 0° 经线/日期变更线时
    (如 358°E→2°E)按 356° 大圆弧绕远路;相邻报点间取短弧是预期行为。"""
    if prev_state is None:
        prev_state = (math.radians(prev_lat), math.radians(prev_lon),
                      math.cos(math.radians(prev_lat)))
    lat1, lon1, cos1 = prev_state
    lat2r = math.radians(lat2)
    lon2r = math.radians(lon2)
    dlon = lon2r - lon1
    while dlon > math.pi:
        dlon -= 2.0 * math.pi
    while dlon < -math.pi:
        dlon += 2.0 * math.pi
    sdlat = math.sin((lat2r - lat1) * 0.5)
    sdlon = math.sin(dlon * 0.5)
    a = sdlat * sdlat + cos1 * math.cos(lat2r) * sdlon * sdlon
    km = 12742.0 * math.asin(math.sqrt(min(1.0, a)))
    return km, (lat2r, lon2r, math.cos(lat2r))


def compute_path_km_in_window(ty_pts: list, year: int,
                              window: Optional[Tuple[datetime, datetime]] = None) -> float:
    """按时间窗口切分轨迹求路径长度(F1)。默认窗口 = 自然年 [year/01/01 00Z, (year+1)/01/01 00Z);
    南半球可传 ACE 年窗口 (7/1 Y, 7/1 Y+1)(N4)。

    - 台风跨窗口边界时,在边界时刻对边界两侧点做时间线性插值
      (lat/lon 按时间比例,经度跨 0 度线回卷归一),边界插值点与相邻真实点
      之间同样 haversine 累加,避免 1/1 边界两侧首尾段被丢弃。
    - 台风完全在窗口内(未跨边界)时退化为逐点全段累加,保证旧数据结果不变。
    - 返回公里数;是否只计 TS+ 段由调用方在传入前先过滤。
    """
    if len(ty_pts) < 2:
        return 0.0
    if window is not None:
        w0, w1 = window
    else:
        w0 = datetime(year, 1, 1, 0)
        w1 = datetime(year + 1, 1, 1, 0)

    pairs: List[Tuple[datetime, dict]] = []
    for p in ty_pts:
        try:
            # K17: 只解析前 10 位(与 ace_engine/season_stats 口径一致),
            # 失败仅跳过该点,不整条清零
            d = datetime.strptime(str(p['t'])[:10], "%Y%m%d%H")
            pairs.append((d, p))
        except (ValueError, KeyError, TypeError):
            continue
    if len(pairs) < 2:
        return 0.0
    dts = [d for d, _ in pairs]
    pts = [p for _, p in pairs]

    i0 = i1 = -1
    for i, d in enumerate(dts):
        if w0 <= d < w1:
            if i0 == -1:
                i0 = i
            i1 = i
    if i0 == -1:
        return 0.0

    def _interp(pa: dict, dt_a: datetime, pb: dict, dt_b: datetime,
                target: datetime) -> dict:
        span = (dt_b - dt_a).total_seconds()
        if span <= 0:
            return pa
        t = (target - dt_a).total_seconds() / span
        t = max(0.0, min(1.0, t))
        la = pa['la'] + (pb['la'] - pa['la']) * t
        dlo = pb['lo'] - pa['lo']
        while dlo > 180:
            dlo -= 360
        while dlo < -180:
            dlo += 360
        lo = pa['lo'] + dlo * t
        return {'la': la, 'lo': lo}

    seg_pts: List[dict] = []
    if i0 > 0 and dts[i0 - 1] < w0:
        seg_pts.append(_interp(pts[i0 - 1], dts[i0 - 1],
                               pts[i0], dts[i0], w0))
    seg_pts.append(pts[i0])
    for i in range(i0 + 1, i1 + 1):
        seg_pts.append(pts[i])
    if i1 < len(pts) - 1 and dts[i1 + 1] >= w1:
        seg_pts.append(_interp(pts[i1], dts[i1],
                               pts[i1 + 1], dts[i1 + 1], w1))

    km = 0.0
    st = None
    for i in range(1, len(seg_pts)):
        seg, st = _haversine_chain(seg_pts[i]['la'], seg_pts[i]['lo'],
                                   seg_pts[i - 1]['la'], seg_pts[i - 1]['lo'], st)
        km += seg
    return km


_dashed_h_cache: Dict[Tuple[int, int], pygame.Surface] = {}


def _get_dashed_h_surface(width: int, color: Tuple, dash: int = 6, gap: int = 4) -> pygame.Surface:
    key = (width, dash, gap, color)
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
    width = int(x2 - x1)
    if width <= 0 or y < 0:
        return
    dash_surf = _get_dashed_h_surface(width, color, dash, gap)
    surface.blit(dash_surf, (int(x1), int(y)))


_month_lines_cache: Dict[Tuple, Tuple[pygame.Surface, List[float], List[str]]] = {}


def build_month_lines_surface(
    width: int, height: int,
    start_dt: datetime, total_hours: int, hemisphere: str,
) -> Tuple[pygame.Surface, List[float], List[str]]:
    # K-回归: 缓存键须含 start_dt 的月/日(仅 year 不足),否则同一年内不同
    # 起始日期(如 7/1 与 1/1 的 ACE 窗口)会互相错用缓存。
    cache_key = (width, height, start_dt.year, start_dt.month, start_dt.day,
                 total_hours, hemisphere, chart_dark())
    if cache_key in _month_lines_cache:
        return _month_lines_cache[cache_key]

    xs: List[float] = []
    labels: List[str] = []
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    if width <= 0 or height <= 0:
        _month_lines_cache[cache_key] = (surf, xs, labels)
        return surf, xs, labels

    window_end = start_dt + timedelta(hours=total_hours)
    # 先收集所有落在窗口内的候选月份起点,再按时间排序——南半球(7/1 起始)下
    # 直接按 m=1..12 循环会把次年 1-6 月排在当年 7-12 月之前,导致返回的
    # xs/labels 乱序,时间轴上网格线错位。
    cands: List[Tuple[float, str]] = []
    for m in range(1, 13):
        if hemisphere == HEMISPHERE_NORTH:
            ms = datetime(start_dt.year, m, 1, 0)
        else:
            yr = start_dt.year
            ms = datetime(yr, m, 1, 0) if m >= 7 else datetime(yr + 1, m, 1, 0)
        if ms < start_dt or ms > window_end:
            continue
        ho = (ms - start_dt).total_seconds() / 3600
        x_px = (ho / total_hours) * width if total_hours > 0 else 0.0
        cands.append((x_px, f"{ms.month:02d}/01"))
    cands.sort(key=lambda c: c[0])
    for x_px, label in cands:
        draw_dashed_v(surf, x_px, 0, height, chart_dash())
        xs.append(x_px)
        labels.append(label)

    if len(_month_lines_cache) > 32:
        _month_lines_cache.pop(next(iter(_month_lines_cache)))
    _month_lines_cache[cache_key] = (surf, xs, labels)
    return surf, xs, labels


# ════════════════════ 图表布局 Mixin ════════════════════


def _offset_rect(r: pygame.Rect, dy: int) -> pygame.Rect:
    return pygame.Rect(r.x, r.y + dy, r.w, r.h)


class ChartGridMixin:
    """图表布局、栅格计算、滚动条、月份缓存管理。"""

    padding_left: int
    padding_right: int
    padding_top: int
    window_width: int
    window_height: int
    graph_width: int

    curve_rect: pygame.Rect
    daily_bar_rect: pygame.Rect
    chart2_rect: pygame.Rect
    chart3_rect: pygame.Rect
    bar_rect: pygame.Rect

    curve_height: int
    daily_height: int
    chart2_height: int
    chart3_height: int
    typhoon_height: int
    _typhoon_bar_width: float

    _scroll_y: int
    _content_height: int
    _layout_valid: bool
    _chart_data: object
    _month_lines_surface: Optional[pygame.Surface]
    _month_line_xs: List[float]
    _month_line_labels: List[str]
    _month_line_top: int
    _month_line_bottom: int
    _month_label_y: int
    _month_label_surfs: Optional[List[pygame.Surface]]
    _month_label_surf_xs: Optional[List[float]]

    def _init_grid_attributes(self) -> None:
        max_w = max(800, min(int(ACE_CHART_DEFAULT_WIDTH * WINDOW_WIDTH_SCALE),
                             self.sim.screen_width - 20))
        max_h = max(600, min(ACE_CHART_DEFAULT_HEIGHT,
                             self.sim.screen_height - 80))
        self.window_width = max_w
        self.window_height = max_h
        self._layout_valid = False

        self.padding_left = ACE_CHART_PADDING_LEFT
        self.padding_right = ACE_CHART_PADDING_RIGHT
        self.padding_top = ACE_CHART_PADDING_TOP
        self.graph_width = self.window_width - self.padding_left - self.padding_right

        self.curve_rect = pygame.Rect(0, 0, 0, 0)
        self.daily_bar_rect = pygame.Rect(0, 0, 0, 0)
        self.chart2_rect = pygame.Rect(0, 0, 0, 0)
        self.chart3_rect = pygame.Rect(0, 0, 0, 0)
        self.bar_rect = pygame.Rect(0, 0, 0, 0)

        self.curve_height = 250
        self.daily_height = 180
        self.chart2_height = 360
        self.chart3_height = 120
        self.typhoon_height = 250

        self._scroll_y = 0
        self._content_height = 0

        self._month_lines_surface = None
        self._month_line_xs = []
        self._month_line_labels = []
        self._month_line_top = 0
        self._month_line_bottom = 0
        self._month_label_y = 0
        self._month_label_surfs = None
        self._month_label_surf_xs = None

    def _center_in_map(self):
        map_cx = self.sim.screen_width // 2
        map_cy = self.sim.map_height // 2
        self.bg_rect.x = max(0, map_cx - self.window_width // 2)
        self.bg_rect.y = max(0, map_cy - self.window_height // 2)
        if self.bg_rect.right > self.sim.screen_width:
            self.bg_rect.x = self.sim.screen_width - self.window_width
        if self.bg_rect.bottom > self.sim.screen_height:
            self.bg_rect.y = self.sim.screen_height - self.window_height
        self._layout_valid = False

    def _compute_layout(self):
        if self._layout_valid:
            return

        graph_width = self.window_width - self.padding_left - self.padding_right
        chart_data = self._chart_data

        n_ty = max(1, len(chart_data.typhoon_ace_list))
        tbw = min(40, graph_width / n_ty * 0.7)
        self._typhoon_bar_width = tbw

        chart2_h = 480          # 风暴活跃周期图(原布局)
        chart3_h = max(40, int(2 * tbw))
        curve_h, daily_h, typhoon_h = 250, 180, 250

        top_area = self.padding_top + 40 + 30
        bottom_area = 70
        label_row = 18
        gap = 10

        needed = top_area + curve_h + daily_h + chart2_h + chart3_h + label_row + gap + typhoon_h + bottom_area
        max_h = min(1440, self.sim.screen_height - 100)
        if needed > max_h:
            self.window_height = max_h
            self._content_height = needed
        else:
            self.window_height = needed
            self._content_height = needed
            self._scroll_y = 0

        self.curve_height = curve_h
        self.daily_height = daily_h
        self.chart2_height = chart2_h
        self.chart3_height = chart3_h
        self.typhoon_height = typhoon_h
        self.graph_width = graph_width

        bx = self.bg_rect.x + self.padding_left
        top = self.bg_rect.y + self.padding_top + 32
        self.curve_rect = pygame.Rect(bx, top, graph_width, curve_h)
        self.daily_bar_rect = pygame.Rect(bx, self.curve_rect.bottom, graph_width, daily_h)
        self.chart2_rect = pygame.Rect(bx, self.daily_bar_rect.bottom, graph_width, chart2_h)
        self.chart3_rect = pygame.Rect(bx, self.chart2_rect.bottom, graph_width, chart3_h)
        self._month_label_y = self.chart3_rect.bottom + 7
        bt = self.chart3_rect.bottom + label_row + gap + 5
        self.bar_rect = pygame.Rect(bx, bt, graph_width, typhoon_h)
        self._month_line_top = self.curve_rect.top
        self._month_line_bottom = self.chart3_rect.bottom + 5
        total_h = self.bar_rect.bottom - self.bg_rect.y + 95   # 底部: 按钮行28 + 名称/页码区(R2-5)
        self._content_height = max(self._content_height, total_h)
        max_h = min(1440, self.sim.screen_height - 100)
        self.bg_rect.height = min(max_h, max(self.window_height, total_h))
        self.window_height = self.bg_rect.height
        max_scroll = max(0, self._content_height - self.window_height)
        self._scroll_y = max(0, min(self._scroll_y, max_scroll))

        self._layout_valid = True

    def _build_month_cache(self):
        cd = self._chart_data
        sd, _, th = cd.year_range
        w = self.graph_width
        h = self._month_line_bottom - self._month_line_top
        surf, xs, labels = build_month_lines_surface(w, h, sd, th, self.sim.hemisphere)
        self._month_lines_surface = surf
        self._month_line_xs = xs
        self._month_line_labels = labels
        self._month_label_surfs = [rt(f_s, lbl, chart_axis()) for lbl in labels]
        self._month_label_surf_xs = xs

    def _invalidate_grid_caches(self):
        self._month_label_surfs = None
        self._month_label_surf_xs = None
        self._month_lines_surface = None

    def _draw_scrollbar(self, surface, bx, by, bw):
        draw_vscrollbar(surface, bx + bw - 8, by, self.window_height,
                        self._content_height, self.window_height, self._scroll_y,
                        dark=chart_dark())
