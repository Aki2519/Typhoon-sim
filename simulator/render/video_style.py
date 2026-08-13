# simulator/render/video_style.py
"""F10 模拟画面样式(视频同款): 白底画布 + 8 类画面。

通用规范: 白底、顶部标题、底部左图例、经纬网格+四边刻度、
陆地填充(取自 map/land.png 掩码,不再是逐行扫描线段)、时间戳。
数据不足的场以合成/占位渲染(样式正确优先),与模拟解耦。

R3 修正:
  - 字体路径错误(MapleMono.ttf/msyh.ttc 不存在)导致退回默认位图字体,
    °/⁻⁶ 等字形缺失显示黑框 → 改用 MapleMono-NF-CN-Medium.ttf。
  - 陆地改为掩码填充(land.png 派生),消除"横向条状/超出图框"。
  - 双域面板按经纬度纵横比留白(360°×120° → 3:1),地图不再被压扁。
  - 中心/单站坐标轴改为数据自适应(背景气压/最大风速),不再固定上限。
  - 经度 >180 标注 W,纬度标注 N/S;刻度文字不溢出画布。
"""
from __future__ import annotations
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pygame

from simulator.env.field_io import NLAT, NLON, LATS, LONS

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
YELLOW_LAND = (230, 200, 60)
GRID_C = (200, 200, 200)
MODEL_TAG = 'IFS/ERA5'
LAND_FILL = (232, 227, 205)
LAND_EDGE = (186, 170, 124)

VIDEO_LAYERS = ('video_olr', 'video_refl', 'video_sfc', 'video_mid',
                'video_high', 'video_station', 'video_center', 'video_track')

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'font')
_FT_FONTS = {}


def _font(size: int):
    import pygame.freetype as ft
    if size not in _FT_FONTS:
        for name in ('MapleMono-NF-CN-Medium.ttf', 'MapleMono-NF-CN-Light.ttf',
                     'msyhbd.ttc'):
            path = os.path.join(_FONT_DIR, name)
            if os.path.exists(path):
                _FT_FONTS[size] = ft.Font(path, size)
                break
        else:
            _FT_FONTS[size] = ft.Font(None, size)
    return _FT_FONTS[size]


def _render(surf, text, x, y, size=14, color=BLACK):
    _font(size).render_to(surf, (int(x), int(y)), text, color)


def _title_size(w: int) -> int:
    return max(18, min(44, w // 48))


def _axis_size(w: int) -> int:
    return max(10, min(18, w // 180))


# ════════════════════════════════════════════════ 通用画布 ════════════════════════════════════════════════

def new_canvas(w: int, h: int) -> pygame.Surface:
    s = pygame.Surface((w, h))
    s.fill(WHITE)
    return s


def draw_title(s: pygame.Surface, text: str, y: int = 10) -> None:
    size = _title_size(s.get_width())
    f = _font(size)
    tw = f.get_rect(text).width
    if tw > s.get_width() - 24:
        size = max(14, int(size * (s.get_width() - 24) / tw))
        f = _font(size)
        tw = f.get_rect(text).width
    _render(s, text, (s.get_width() - tw) // 2, y, size=size)


def draw_timestamp(s: pygame.Surface, dt, x: int, y: int,
                   fmt: str = '%d/%m/%y %H:%M:%S') -> None:
    _render(s, dt.strftime(fmt), x, y, size=_axis_size(s.get_width()))


def draw_legend(s: pygame.Surface, text: str, x: int, y: int) -> None:
    _render(s, text, x, y, size=_axis_size(s.get_width()))


# ════════════════════════════════════════════════ 陆地(掩码填充 + map.png)════════════════════════════════════════════════

_LAND_ELEV: Optional[np.ndarray] = None
_MAP_PNG: Optional[np.ndarray] = None


def _land_elev() -> Optional[np.ndarray]:
    global _LAND_ELEV
    if _LAND_ELEV is None:
        from simulator.typhoons.sim import load_elevation
        _LAND_ELEV = load_elevation()
    return _LAND_ELEV


def _map_png() -> Optional[np.ndarray]:
    """加载 ./map/map.png 为 RGB 数组((5400,10800,3), -90..90°N)。"""
    global _MAP_PNG
    if _MAP_PNG is None:
        from PIL import Image
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        p = os.path.join(root, 'map', 'map.png')
        if os.path.exists(p):
            _MAP_PNG = np.asarray(Image.open(p).convert('RGB'))
    return _MAP_PNG


def _map_land_px(w: int, h: int, lon0: float, lon1: float,
                 lat0: float, lat1: float) -> np.ndarray:
    """从 map.png 裁出窗口区域的陆地像素 (h, w, 3)。"""
    m = _map_png()
    if m is None:
        return np.zeros((h, w, 3), dtype=np.uint8)
    span_lon = ((lon1 - lon0) % 360.0) or 360.0
    xs = (np.linspace(lon0, lon0 + span_lon, w) % 360.0) / 360.0 * m.shape[1]
    ys = (90.0 - np.linspace(max(lat0, lat1), min(lat0, lat1), h)) \
        / 180.0 * m.shape[0]
    px = np.clip(xs.astype(int), 0, m.shape[1] - 1)
    py = np.clip(ys.astype(int), 0, m.shape[0] - 1)
    return m[np.ix_(py, px)]


def land_mask_for(w: int, h: int, lon0: float, lon1: float,
                  lat0: float, lat1: float) -> np.ndarray:
    """返回 (h, w) bool 陆地掩码,严格限制在给定窗口内(不再越界)。"""
    e = _land_elev()
    if e is None:
        return np.zeros((h, w), dtype=bool)
    span_lon = ((lon1 - lon0) % 360.0) or 360.0
    xs = (np.linspace(lon0, lon0 + span_lon, w) % 360.0) / 360.0 * e.shape[1]
    # 高程数组: 行 0 = 90°N,纬度 L → (90-L)/180*2160
    ys = (90.0 - np.linspace(max(lat0, lat1), min(lat0, lat1), h)) \
        / 180.0 * e.shape[0]
    px = np.clip(xs.astype(int), 0, e.shape[1] - 1)
    py = np.clip(ys.astype(int), 0, e.shape[0] - 1)
    return e[np.ix_(py, px)] > 0


def draw_map(s: pygame.Surface, rect, lon0: float = 0.0, lon1: float = 360.0,
             lat0: float = -60.0, lat1: float = 60.0,
             fill: Optional[Tuple[int, int, int]] = None,
             edge: Optional[Tuple[int, int, int]] = None) -> None:
    """陆地掩码填充(map/map.png 陆地像素);海洋保持透明露出画布。"""
    rw, rh = max(1, rect.w), max(1, rect.h)
    m = land_mask_for(rw, rh, lon0, lon1, lat0, lat1)
    if not m.any():
        return
    rgba = np.zeros((rh, rw, 4), dtype=np.uint8)
    if fill is not None:
        rgba[m, :3] = fill
        rgba[m, 3] = 255
    else:
        land = _map_land_px(rw, rh, lon0, lon1, lat0, lat1)
        rgba[m, :3] = land[m]
        rgba[m, 3] = 255
    if edge is not None:
        er = np.zeros_like(m)
        er[1:, :] |= m[:-1, :]
        er[:-1, :] |= m[1:, :]
        er[:, 1:] |= m[:, :-1]
        er[:, :-1] |= m[:, 1:]
        rgba[m & er, :3] = edge      # 陆地边缘像素 → 轮廓色(参考图黄线样式)
    surf = pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                   (rw, rh), 'RGBA')
    s.blit(surf, rect.topleft)


# ════════════════════════════════════════════════ 网格与刻度════════════════════════════════════════════════

def draw_grid(s: pygame.Surface, rect, lat_step: float = 10.0,
              lon_step: float = 20.0, lat0: float = -60.0,
              lon0: float = 0.0, lat1: float = 60.0, lon1: float = 360.0,
              labels: bool = True) -> None:
    """经纬网格 + 可选四边刻度(使用 map.png 底图时无需标注经纬度)。"""
    span_lat = max(1e-9, lat1 - lat0)
    span_lon = ((lon1 - lon0) % 360.0) or 360.0
    asz = _axis_size(rect.w)
    for la in np.arange(min(lat0, lat1), max(lat0, lat1) + 0.01, lat_step):
        y = rect.y + int((lat1 - la) / span_lat * rect.h)
        pygame.draw.line(s, GRID_C, (rect.x, y), (rect.right, y), 1)
        if labels:
            tag = f"{abs(la):.0f}°{'N' if la >= 0 else 'S'}"
            _render(s, tag, rect.x - 24, y - asz // 2, size=asz)
    for lo0v in np.arange(lon0, lon0 + span_lon + 0.01, lon_step):
        lo = lo0v % 360
        x = rect.x + int(((lo0v - lon0) / span_lon) * rect.w)
        pygame.draw.line(s, GRID_C, (x, rect.y), (x, rect.bottom), 1)
        if labels:
            if lo > 180:
                tag = f"{360 - lo:.0f}°W"
            else:
                tag = f"{lo:.0f}°E"
            _render(s, tag, x - 14, rect.bottom + 3, size=asz)


def _map_inner(panel: pygame.Rect, lon_span: float, lat_span: float) -> pygame.Rect:
    """按经纬纵横比留白(360×120 → 3:1;80×40 → 2:1),地图不变形。"""
    aspect = lon_span / max(1e-9, abs(lat_span))
    hh = min(panel.h, panel.w / aspect)
    ww = hh * aspect
    return pygame.Rect(int(panel.centerx - ww / 2), int(panel.centery - hh / 2),
                       int(ww), int(hh))


# ════════════════════════════════════════════════ 风羽 ════════════════════════════════════════════════

def draw_barb(s: pygame.Surface, x: int, y: int, u: float, v: float,
              scale: float = 2.2) -> None:
    """风羽: 半羽=2.5 m/s、整羽=5、旗=25;黑色,实心圆=静风。"""
    spd = math.hypot(u, v)
    if math.isnan(spd):
        return
    ang = math.atan2(u, v)          # 风向(气象学来向)
    if spd < 0.5:
        pygame.draw.circle(s, BLACK, (x, y), 3)
        return
    ln = 14.0 * scale
    ex = x + ln * math.sin(ang)
    ey = y - ln * math.cos(ang)
    pygame.draw.line(s, BLACK, (x, y), (ex, ey), 1)
    n_flag = int(spd // 25)
    rem = spd - n_flag * 25
    n_full = int(rem // 5)
    n_half = 1 if (rem - n_full * 5) >= 2.5 else 0
    px, py = ex, ey
    of = 7.0 * scale
    for _ in range(n_flag):
        sx = px - of * math.cos(ang)
        sy = py + of * math.sin(ang)
        pygame.draw.line(s, BLACK, (px, py), (sx, sy), 1)
        px, py = sx, sy
    for _ in range(n_full):
        sx = px - 6.0 * scale * math.cos(ang)
        sy = py + 6.0 * scale * math.sin(ang)
        pygame.draw.line(s, BLACK, (px, py), (sx, sy), 1)
        px, py = sx, sy
    if n_half:
        sx = px - 3.5 * scale * math.cos(ang)
        sy = py + 3.5 * scale * math.sin(ang)
        pygame.draw.line(s, BLACK, (px, py), (sx, sy), 1)


# ════════════════════════════════════════════════ 色标 ════════════════════════════════════════════════

REFLECTIVITY_COLORS = [   # 5→75 dBZ
    (0, 0, 255), (0, 128, 255), (0, 200, 255), (0, 200, 0), (0, 255, 0),
    (255, 255, 0), (255, 200, 0), (255, 140, 0), (255, 0, 0),
    (255, 0, 128), (255, 0, 255),
]


def reflectivity_color(dbz: float) -> Tuple[int, int, int]:
    i = max(0, min(len(REFLECTIVITY_COLORS) - 1, int((dbz - 5) / 70.0
                                                     * (len(REFLECTIVITY_COLORS) - 1))))
    return REFLECTIVITY_COLORS[i]


def olr_gray(olr: float) -> int:
    """100→300 W/m²: 白=100(深对流)→黑=300。"""
    return int(max(0, min(255, 255 - (olr - 100) / 200.0 * 255)))


def precip_color(mm: float) -> Tuple[int, int, int]:
    if mm <= 0:
        return WHITE
    if mm < 1:
        return (255, 255, 255)
    if mm < 25:
        return (0, 200, 0)
    if mm < 100:
        return (0, 0, 255)
    return (200, 0, 200)


def wind_jet_color(spd_ms: float, thresh: float) -> Optional[Tuple[int, int, int]]:
    if spd_ms >= thresh:
        return (200, 120, 255)
    return None


# ════════════════════════════════════════════════ F10 新增色标/工具 ════════════════════════════════════════════════

def _valid(fld) -> bool:
    """场缺失或全 NaN 时返回 False(该层跳过,不崩溃)。"""
    if fld is None:
        return False
    a = np.asarray(fld)
    if a.size == 0:
        return False
    return not bool(np.isnan(a).all())


_WARNED: set = set()


def _warn_layer(layer: str, why: str) -> None:
    """字段缺失告警(每画面类型仅打印一次)。"""
    key = f"{layer}:{why}"
    if key not in _WARNED:
        _WARNED.add(key)
        print(f"[video_style] {layer} layer: {why} — layer content skipped")


# 24h 累计降水: 分段线性插值 白0 → 绿1 → 蓝25 → 紫/红800
_PRECIP_STOPS = [(0.0, WHITE), (1.0, (0, 200, 0)), (25.0, (0, 0, 255)),
                 (800.0, (210, 0, 210))]


def precip_color24(mm: float) -> Tuple[int, int, int]:
    if not (mm > 0.0):
        return WHITE
    mm = min(max(mm, 0.0), 800.0)
    for i in range(len(_PRECIP_STOPS) - 1):
        x0, c0 = _PRECIP_STOPS[i]
        x1, c1 = _PRECIP_STOPS[i + 1]
        if x0 <= mm <= x1 or i == len(_PRECIP_STOPS) - 2:
            t = (mm - x0) / max(1e-9, (x1 - x0))
            return tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return _PRECIP_STOPS[-1][1]


def _precip_lut(values: np.ndarray, lo: float = 0.0, hi: float = 800.0,
                n: int = 256) -> np.ndarray:
    """对 (m,) 标量数组逐点取色 → (m, 3) uint8,替换逐像素 Python 循环。"""
    vs = np.nan_to_num(values, nan=0.0)
    idx = np.clip((vs - lo) / max(1e-9, (hi - lo)) * (n - 1), 0, n - 1).astype(int)
    lut = np.empty((n, 3), dtype=np.uint8)
    xs = np.linspace(lo, hi, n)
    for k in range(n):
        lut[k] = precip_color24(float(xs[k]))
    return lut[idx]


def wind_vec_color(ms: float) -> Tuple[int, int, int]:
    """10m 风矢量色标: 5→25 m/s 冷(蓝)到暖(红)。"""
    t = max(0.0, min(1.0, (ms - 5.0) / 20.0))
    if t < 0.5:
        f = t / 0.5
        return (int(20 * f), int(120 + 120 * f), 255)
    f = (t - 0.5) / 0.5
    return (int(20 + 235 * f), int(240 - 200 * f), int(255 - 215 * f))


def draw_arrow(s: pygame.Surface, x: int, y: int, u: float, v: float,
               scale: float = 3.2) -> None:
    """风矢量箭头: 长度按风速,方向按 (u,v),借鉴 draw_barb 的静风圆风格。"""
    if math.isnan(u) or math.isnan(v):
        return
    spd = math.hypot(u, v)
    if spd < 0.3:
        pygame.draw.circle(s, (60, 60, 60), (x, y), 2)
        return
    col = wind_vec_color(spd)
    ln = min(30.0, 2.0 + spd * scale)          # 长度按风速,封顶防溢出
    ang = math.atan2(v, u)                      # 数学方向(东=0,北=90)
    ex = x + ln * math.cos(ang)
    ey = y - ln * math.sin(ang)
    pygame.draw.line(s, col, (x, y), (int(ex), int(ey)), 2)
    # 箭头头部
    hl = 5.0
    for da in (0.6, -0.6):
        tx = ex - hl * math.cos(ang + da)
        ty = ey + hl * math.sin(ang + da)
        pygame.draw.line(s, col, (int(ex), int(ey)), (int(tx), int(ty)), 1)


def _contour(s: pygame.Surface, rect: pygame.Rect, sub: np.ndarray,
             levels: Tuple[float, ...], color=(90, 90, 100),
             dashed: bool = False, nan_fill: float = 1013.0) -> None:
    """在 _blit_field 采样的面板子场上画等值线(sub 对齐 rect 像素)。
    dashed=True 时画短划线(用于温度红色虚线)。"""
    if sub is None or sub.shape[0] < 2 or sub.shape[1] < 2:
        return
    a = np.nan_to_num(sub, nan=nan_fill)
    for lev in levels:
        sign = np.sign(a - lev)
        cross = np.abs(np.diff(sign, axis=1)) > 1.0     # (h, w-1)
        hs, ws = np.where(cross)
        if not len(hs):
            continue
        px = rect.x + ws + 0.5
        py = rect.y + hs + 0.5
        if dashed:
            for k in range(len(hs)):
                pygame.draw.line(s, color, (int(px[k]), int(py[k])),
                                 (int(px[k]) + 5, int(py[k])), 1)
            continue
        order = np.lexsort((px, py))                    # 行优先,同行相连
        segs = np.split(np.arange(hs[order].size),
                        np.flatnonzero(np.diff(hs[order])))
        for seg in segs:
            if len(seg) < 2:
                continue
            pts = [(int(px[order[i]]), int(py[order[i]])) for i in seg]
            pygame.draw.lines(s, color, False, pts, 1)


def _divergence(uv: np.ndarray) -> Optional[np.ndarray]:
    """从 (2, NLAT, NLON) m/s 风用 np.gradient 算散度 → 10⁻⁶ s⁻¹。
    场无效时返回 None。"""
    if uv is None or not _valid(uv) or not _valid(uv[0]):
        return None
    u = np.nan_to_num(uv[0])
    ve = np.nan_to_num(uv[1])
    lat_rad = np.deg2rad(np.clip(LATS, -60, 60))
    dx = 111.2e3 * np.cos(lat_rad)        # 每 1° 经度对应米数(逐纬)
    dy = 111.2e3                          # 每 1° 纬度对应米数
    dudx = np.gradient(u, axis=1, edge_order=1) / dx[:, None] * 1e6
    dvdy = np.gradient(ve, axis=0, edge_order=1) / dy * 1e6
    return (dudx + dvdy).astype(np.float64)


def _mark_hlt(s: pygame.Surface, rect: pygame.Rect, sub: np.ndarray,
              typhoons, span, h_thresh: float = 1018.0,
              l_thresh: float = 1006.0) -> None:
    """在 SLP 子场上窗口扫描局部极值标红 H / 蓝 L;台风中心标红 T。"""
    if sub is None or sub.shape[0] <= 8 or sub.shape[1] <= 8:
        return
    a = np.nan_to_num(sub, nan=1013.0)
    h, w = sub.shape
    r = 3                                  # 邻域半径(像素)
    step = 6
    for i in range(r, h - r, step):
        for j in range(r, w - r, step):
            patch = a[i - r:i + r + 1, j - r:j + r + 1]
            c = a[i, j]
            if c >= h_thresh and c == patch.max():
                pygame.draw.circle(s, (220, 40, 40),
                                   (rect.x + j, rect.y + i), 6)
                _render(s, 'H', rect.x + j - 5, rect.y + i - 18,
                        size=13, color=(220, 40, 40))
            elif c <= l_thresh and c == patch.min():
                pygame.draw.circle(s, (30, 60, 220),
                                   (rect.x + j, rect.y + i), 6)
                _render(s, 'L', rect.x + j - 5, rect.y + i - 18,
                        size=13, color=(30, 60, 220))
    if typhoons:
        ty = typhoons[0]
        c0, c1, la0, la1 = span
        x, y = _map_rect(rect, c0, c1, la0, la1, ty['lo'], ty['la'])
        if rect.collidepoint(x, y):
            pygame.draw.circle(s, (220, 40, 40), (x, y), 5)
            _render(s, 'T', x - 5, y - 18, size=13, color=(220, 40, 40))


def track_pressure_color(p: float) -> Tuple[int, int, int]:
    """990→1030 hPa: 红→黄→绿→青→蓝。"""
    t = max(0.0, min(1.0, (p - 990.0) / 40.0))
    if t < 0.25:
        f = t / 0.25
        return (255, int(255 * f), 0)
    if t < 0.5:
        f = (t - 0.25) / 0.25
        return (int(255 * (1 - f)), 255, 0)
    if t < 0.75:
        f = (t - 0.5) / 0.25
        return (0, 255, int(255 * f))
    f = (t - 0.75) / 0.25
    return (0, int(255 * (1 - f)), 255)


# ════════════════════════════════════════════════ 双域 ════════════════════════════════════════════════

def _map_rect(rect, lon0, lon1, lat0, lat1, lon, lat):
    """经纬 → 像素(等距圆柱)。"""
    x = rect.x + int((lon - lon0) / (lon1 - lon0) * rect.w)
    y = rect.y + int((lat1 - lat) / (lat1 - lat0) * rect.h)
    return x, y


def draw_dual(s: pygame.Surface, w: int, h: int, title: str, dt,
              draw_panel, center: Optional[Tuple[float, float]] = None,
              lon_span: float = 80.0) -> None:
    """D01(大域)+ D02(放大域,以台风中心为焦点)左右并排,共享色标。
    面板内按纵横比留白,地图不变形。"""
    draw_title(s, title)
    draw_timestamp(s, dt, 10, 36)
    gap = 10
    margin = 48
    pw = (w - margin * 2 - gap * 3) // 2
    ph = h - 130
    y0 = 60
    d01 = pygame.Rect(margin, y0, pw, ph)
    d02 = pygame.Rect(w - margin - pw, y0, pw, ph)
    for rect, prefix, (c0, c1, la0, la1) in (
            (d01, 'D01', (0.0, 360.0, 60.0, -60.0)),
            (d02, 'D02', _d02_span(center, lon_span))):
        inner = _map_inner(rect, ((c1 - c0) % 360.0) or 360.0, abs(la1 - la0))
        draw_panel(s, rect, inner, prefix, (c0, c1, la0, la1))
        draw_grid(s, inner, lat0=min(la0, la1), lat1=max(la0, la1),
                  lon0=c0, lon1=c1, labels=False)
        draw_map(s, inner, lat0=min(la0, la1), lat1=max(la0, la1),
                 lon0=c0, lon1=c1, edge=(230, 210, 60))
    draw_legend(s, title, margin, h - 22)


def _d02_span(center, lon_span):
    if center is None:
        return (100.0, 180.0, 30.0, 0.0)
    lo = center[1] % 360
    return ((lo - lon_span / 4) % 360, (lo + lon_span / 4) % 360,
            center[0] + lon_span / 4, center[0] - lon_span / 4)


def _blit_field(s: pygame.Surface, dst: pygame.Rect, fld: np.ndarray,
                span: Tuple[float, float, float, float]) -> None:
    """(NLAT, NLON) 场 → dst 矩形(等距圆柱,向量化采样)。"""
    c0, c1, la0, la1 = span
    span_lon = ((c1 - c0) % 360.0) or 360.0
    ys = la0 - (np.arange(dst.h) + 0.5) / dst.h * (la0 - la1)
    xs = c0 + (np.arange(dst.w) + 0.5) / dst.w * span_lon
    la_i = np.clip(np.round(ys + 60).astype(int), 0, NLAT - 1)
    lo_i = np.clip((xs % 360).astype(int), 0, NLON - 1)
    sub = fld[np.ix_(la_i, lo_i)]
    return sub


# ════════════════════════════════════════════════ 8 类画面════════════════════════════════════════════════

def render_olr(api, dt, typhoons=None, w=1280, h=760) -> pygame.Surface:
    """OLR 灰度(100→300 W/m²),D01/D02 双域(纵横比校正)。"""
    s = new_canvas(w, h)

    def panel(surf, rect, inner, prefix, span):
        olr = api.get_field('olr', dt) if api else None
        if olr is None:
            olr = _synth_olr(api, dt, typhoons)
        sub = _blit_field(surf, inner, olr, span)
        g = np.clip(255 - ((np.nan_to_num(sub, nan=300.0) - 100) / 200.0 * 255),
                    0, 255).astype(np.uint8)
        rgb = np.stack([g, g, g], axis=-1)
        blit_surf = pygame.image.frombuffer(rgb.tobytes(), (inner.w, inner.h), 'RGB')
        surf.blit(blit_surf, inner.topleft)
        ft_title(surf, rect, prefix)
    draw_dual(s, w, h, "Outgoing Longwave Radiation (W/m²)", dt, panel,
              center=(typhoons[0]['la'], typhoons[0]['lo']) if typhoons else None)
    return s


def render_refl(api, dt, typhoons=None, w=1280, h=760) -> pygame.Surface:
    """雷达组合反射率(5→75 dBZ),双域。"""
    s = new_canvas(w, h)

    def panel(surf, rect, inner, prefix, span):
        refl = _synth_refl(typhoons, span)
        sub = _blit_field(surf, inner, refl, span)
        idx = np.clip(((sub - 5) / 70.0 * (len(REFLECTIVITY_COLORS) - 1)).astype(int),
                      0, len(REFLECTIVITY_COLORS) - 1)
        lut = np.array(REFLECTIVITY_COLORS, dtype=np.uint8)
        rgb = lut[idx]
        blit_surf = pygame.image.frombuffer(rgb.tobytes(), (inner.w, inner.h), 'RGB')
        surf.blit(blit_surf, inner.topleft)
        ft_title(surf, rect, prefix)
    draw_dual(s, w, h, "Radar Reflectivity (DBZ)", dt, panel,
              center=(typhoons[0]['la'], typhoons[0]['lo']) if typhoons else None)
    return s


def _draw_panel_base(s, rect) -> pygame.Rect:
    """整域面板底座: 经纬留白内图 + 网格 + 陆地,返回 inner。"""
    inner = _map_inner(rect, 360.0, 120.0)
    draw_grid(s, inner, labels=False)
    draw_map(s, inner, edge=(230, 210, 60))
    return inner


def render_sfc(api, dt, typhoons=None, w=1280, h=760) -> pygame.Surface:
    """地面形势(F10): D01 左 = SLP 等值线 + 24h 降水填色 + H/L/T 标记;
    D02 右 = 10m 风矢量 + SLP 等值线(以台风中心为焦点)。"""
    s = new_canvas(w, h)
    draw_title(s, "D01 SLP (hPa) & 24h Precip. (mm)  |  D02 SLP (hPa) & 10m Wind (m/s)")
    draw_timestamp(s, dt, 10, 36)
    gap, margin, y0, ph = 10, 48, 60, h - 130
    pw = (w - margin * 2 - gap * 3) // 2
    left = pygame.Rect(margin, y0, pw, ph)
    right = pygame.Rect(w - margin - pw, y0, pw, ph)
    center = (typhoons[0]['la'], typhoons[0]['lo']) if typhoons else None

    mslp = api.get_field('mslp', dt) if api else None
    precip = api.get_field('precip24', dt) if api else None
    u10 = api.get_field('u10', dt) if api else None
    v10 = api.get_field('v10', dt) if api else None
    if not (_valid(mslp) or _valid(precip) or (_valid(u10) or _valid(v10))):
        _warn_layer('sfc', 'no mslp/precip24/u10/v10')

    # ── D01 左面板: SLP 等值线 + 24h 降水填色 + H/L/T ──
    span_d01 = (0.0, 360.0, 60.0, -60.0)
    inner_l = _draw_panel_base(s, left)
    ft_title(s, left, 'D01 SLP (hPa) & 24h Precip. (mm)')
    if _valid(precip):
        sub = _blit_field(s, inner_l, precip, span_d01)
        rgb = _precip_lut(sub.ravel()).reshape(sub.shape + (3,))
        blit_surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(),
                                            (inner_l.w, inner_l.h), 'RGB')
        s.blit(blit_surf, inner_l.topleft)
    if _valid(mslp):
        sub_m = _blit_field(s, inner_l, mslp, span_d01)
        _contour(s, inner_l, sub_m, (1000.0, 1008.0, 1016.0, 1024.0),
                 color=(40, 40, 50), nan_fill=1013.0)
        _mark_hlt(s, inner_l, sub_m, typhoons, span_d01)
    # 重画陆地轮廓在图上(透明海洋露出下方填充)
    draw_map(s, inner_l, edge=(230, 210, 60))

    # ── D02 右面板: 10m 风矢量 + SLP 等值线(台风聚焦)──
    span_d02 = _d02_span(center, 80.0)
    inner_r = _map_inner(right, ((span_d02[1] - span_d02[0]) % 360.0) or 360.0,
                         abs(span_d02[3] - span_d02[2]))
    draw_grid(s, inner_r, lat0=min(span_d02[2], span_d02[3]),
              lat1=max(span_d02[2], span_d02[3]),
              lon0=span_d02[0], lon1=span_d02[1], labels=False)
    draw_map(s, inner_r, lat0=min(span_d02[2], span_d02[3]),
             lat1=max(span_d02[2], span_d02[3]),
             lon0=span_d02[0], lon1=span_d02[1], edge=(230, 210, 60))
    ft_title(s, right, 'D02 SLP (hPa) & 10m Wind (m/s)')
    if _valid(u10) and _valid(v10):
        us = _blit_field(s, inner_r, u10, span_d02)
        vs = _blit_field(s, inner_r, v10, span_d02)
        # 风矢量箭头(等距取样,draw_arrow 按风速冷→暖着色)
        nj = max(2, inner_r.h // 24)
        ni = max(2, inner_r.w // 44)
        for i in range(nj):
            for j in range(ni):
                by = min(i * (inner_r.h // nj) + (inner_r.h // nj) // 2,
                         inner_r.h - 1)
                bx = min(j * (inner_r.w // ni) + (inner_r.w // ni) // 2,
                         inner_r.w - 1)
                draw_arrow(s, inner_r.x + j * (inner_r.w // ni),
                           inner_r.y + i * (inner_r.h // nj),
                           float(us[by, bx]), float(vs[by, bx]), scale=2.6)
    if _valid(mslp):
        sub_m2 = _blit_field(s, inner_r, mslp, span_d02)
        _contour(s, inner_r, sub_m2, (1004.0, 1008.0, 1012.0, 1016.0),
                 color=(40, 40, 50), nan_fill=1013.0)
    return s


def _isobars(s: pygame.Surface, rect, fld: np.ndarray,
             levels: Tuple[float, ...], color=(90, 90, 100)) -> None:
    """等压线: 逐行跨层检测 → 连续细线(原 2px 点阵在放缩后像噪点)。"""
    a = np.nan_to_num(fld, nan=1013.0)
    for lev in levels:
        sign = np.sign(a - lev)
        cross = np.abs(np.diff(sign, axis=1)) > 1.0   # (121, 359)
        ys, xs = np.where(cross)
        if not len(ys):
            continue
        px = rect.x + ((xs + 0.5) / NLON * rect.w)
        # 场行索引: 行 0=-60°S、行 NLAT-1=+60°N,而地图顶部=60°N,故翻转 ys
        py = rect.y + ((NLAT - 1 - ys) / float(NLAT - 1) * rect.h)
        order = np.lexsort((px, py))
        # 同行相连 →水平折线段近似等压线
        rows = np.split(order, np.flatnonzero(np.diff(ys[order])))
        for seg in rows:
            if len(seg) < 2:
                continue
            pts = [(int(px[i]), int(py[i])) for i in seg]
            pygame.draw.lines(s, color, False, pts, 1)


# 中低层/高空共用: 整域面板
_SPAN_GLOBAL = (0.0, 360.0, 60.0, -60.0)


def _whole_panel(s, w, h) -> Tuple[pygame.Rect, pygame.Rect]:
    rect = pygame.Rect(48, 60, w - 96, h - 130)
    inner = _map_inner(rect, 360.0, 120.0)
    draw_grid(s, inner, labels=False)
    draw_map(s, inner, edge=(230, 210, 60))
    return rect, inner


def _jet_fill(s, surf, inner, uv, q=70) -> bool:
    """急流紫填色(以分位风速为阈值,沿用原风格);uv 无效返回 False。"""
    if not (_valid(uv) and _valid(uv[0])):
        return False
    spd = np.hypot(np.nan_to_num(uv[0]), np.nan_to_num(uv[1]))
    thr = float(np.nanpercentile(spd, q))
    sub = _blit_field(surf, inner, spd, _SPAN_GLOBAL)
    jet = sub >= thr
    rgba = np.zeros((inner.h, inner.w, 4), dtype=np.uint8)
    rgba[jet] = (220, 150, 255, 200)
    s.blit(pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                   (inner.w, inner.h), 'RGBA'), inner.topleft)
    return True


def _draw_barbs(s, inner, uv, step_la=3.0, step_lo=4.0) -> None:
    """黑色风羽(参考 draw_barb 现有实现,原样保留)。"""
    if not (_valid(uv) and _valid(uv[0])):
        return
    for la in np.arange(0, 56, step_la):
        for lo in np.arange(0, 360, step_lo):
            la_i = int(np.clip((la + 60), 0, NLAT - 1))
            lo_i = int(lo) % NLON
            u, v = float(uv[0][la_i, lo_i]), float(uv[1][la_i, lo_i])
            x, y = _map_rect(inner, 0, 360, -60, 60, lo, la)
            if inner.collidepoint(x, y):
                draw_barb(s, x, y, u, v, scale=1.6)


def render_mid(api, dt, typhoons=None, w=1280, h=760) -> pygame.Surface:
    """中低层(F10): 500hPa 高度(蓝 dam 等值线)+500hPa 温度(红虚 °C)+
    850hPa 风羽(黑)+低空急流紫填色+850hPa 比湿≥12 g/kg 绿阴影。"""
    s = new_canvas(w, h)
    draw_title(s, "500 hPa Height (dam) and Temp (°C), 850 hPa Wind (barb)")
    draw_timestamp(s, dt, 10, 36)
    rect, inner = _whole_panel(s, w, h)
    g = _SPAN_GLOBAL

    # 850hPa 风(引导气流代理)
    uv850 = api.get_field('uv_steer', dt) if api else None
    # 850hPa 比湿≥12 g/kg 绿色阴影
    qv = api.get_field('qv850', dt) if api else None
    if _valid(qv):
        sub = _blit_field(s, inner, qv, g)
        shade = (np.nan_to_num(sub, nan=0.0) >= 12.0)
        rgba = np.zeros((inner.h, inner.w, 4), dtype=np.uint8)
        rgba[shade] = (60, 200, 90, 120)
        s.blit(pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                       (inner.w, inner.h), 'RGBA'), inner.topleft)
    # 低空急流紫填色(850hPa)
    _jet_fill(s, s, inner, uv850, q=70)
    # 500hPa 高度蓝等值线(dam);无 hgt500 时回退 mslp 代理(保留视觉)
    hgt500 = api.get_field('hgt500', dt) if api else None
    if _valid(hgt500):
        sub = _blit_field(s, inner, hgt500, g)
        _contour(s, inner, sub, (570.0, 576.0, 582.0, 588.0, 594.0),
                 color=(30, 90, 220), nan_fill=576.0)
    else:
        mslp = api.get_field('mslp', dt) if api else None
        if _valid(mslp):
            sub = _blit_field(s, inner, mslp, g)
            _contour(s, inner, sub, (1005.0, 1010.0, 1015.0, 1020.0),
                     color=(30, 90, 220), nan_fill=1013.0)
    # 500hPa 温度红色虚线(K→°C)
    t500 = api.get_field('t500', dt) if api else None
    if _valid(t500):
        sub = _blit_field(s, inner, t500 - 273.15, g)
        _contour(s, inner, sub, (-30.0, -20.0, -10.0, 0.0, 10.0),
                 color=(200, 30, 30), dashed=True, nan_fill=-40.0)
    # 850hPa 风羽(黑)
    _draw_barbs(s, inner, uv850)
    return s


def render_high(api, dt, typhoons=None, w=1280, h=760) -> pygame.Surface:
    """高空(F10): 100hPa 高度(dam 等值线)+200hPa 风羽(黑)+散度填色+
    高空急流紫填色。"""
    s = new_canvas(w, h)
    draw_title(s, "100 hPa Height (dam), 200 hPa Wind (knots) and Divergence (10⁻⁶ s⁻¹)")
    draw_timestamp(s, dt, 10, 36)
    rect, inner = _whole_panel(s, w, h)
    g = _SPAN_GLOBAL

    # 200hPa 风(uv200,缺省回退 uv_steer 代理)
    uv200 = api.get_field('uv200', dt) if api else None
    uv = uv200 if (_valid(uv200) and _valid(uv200[0])) else \
        (api.get_field('uv_steer', dt) if api else None)
    # 散度填色(正散度>5e-6 紫,负散度=辐合标蓝)
    div = _divergence(uv200) if (_valid(uv200) and _valid(uv200[0])) else None
    if div is not None:
        sub = _blit_field(s, inner, div, g)
        pos = sub > 5.0
        neg = sub < -5.0
        rgba = np.zeros((inner.h, inner.w, 4), dtype=np.uint8)
        if pos.any():
            rgba[pos] = (170, 80, 255, 160)
        if neg.any():
            rgba[neg] = (60, 160, 255, 120)
        s.blit(pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                       (inner.w, inner.h), 'RGBA'), inner.topleft)
    # 高空急流紫填色(200hPa)
    _jet_fill(s, s, inner, uv, q=75)
    # 100hPa 高度等值线(dam)
    hgt100 = api.get_field('hgt100', dt) if api else None
    if _valid(hgt100):
        sub = _blit_field(s, inner, hgt100, g)
        _contour(s, inner, sub, (1600.0, 1630.0, 1660.0, 1690.0),
                 color=(90, 90, 100), nan_fill=1650.0)
    # 200hPa 风羽(黑)
    _draw_barbs(s, inner, uv)
    return s


def render_station(api, dt, station=None, w=800, h=520) -> pygame.Surface:
    """单站时间序列: 气压 + 10m 风双面板(自适应坐标轴)。
    station: {'name','la','lo','pts':[{'p','w'},...]}。"""
    s = new_canvas(w, h)
    name = station.get('name', 'Station') if station else 'Station'
    pts = station.get('pts') if station else None
    if not pts:
        pts = [{'p': 1013.0, 'w': 0.0}]
    ps = [p['p'] for p in pts]
    ws = [p.get('w', 0) for p in pts]
    pmin, pmax = min(ps), max(ps)
    wmax = max(ws) or 1.0
    plo, phi = max(900.0, pmin - 6), pmax + 6          # 背景气压自适应
    wlo, whi = 0.0, wmax * 1.15 + 2
    t_last = pts[-1].get('t', '')
    t_tag = f"{t_last[:4]}-{t_last[4:6]}-{t_last[6:8]} {t_last[8:10]}Z" if t_last else ''
    draw_title(s, f"{name} ({MODEL_TAG}) | {dt.strftime('%Y-%m-%d %HZ')} – {t_tag}")
    asz = _axis_size(w)
    # 上 气压
    r1 = pygame.Rect(90, 60, w - 130, h // 2 - 70)
    _render(s, "hPa", 12, r1.y - 4, size=asz)
    for i in range(5):
        p = plo + (phi - plo) * i / 4.0
        y = r1.y + int((phi - p) / (phi - plo) * r1.h)
        pygame.draw.line(s, GRID_C, (r1.x, y), (r1.right, y), 1)
        _render(s, f"{p:.0f}", 48, y - asz // 2, size=asz)
    xs = np.linspace(r1.x, r1.right, len(pts))
    ys = [r1.y + int((phi - min(phi, max(plo, p))) / (phi - plo) * r1.h) for p in ps]
    pygame.draw.lines(s, BLACK, False, list(zip(xs, ys)), 2)
    # 下 10m 风m/s)
    r2 = pygame.Rect(90, h // 2 + 24, w - 130, h // 2 - 76)
    _render(s, "m/s", 12, r2.y - 4, size=asz)
    for i in range(5):
        v = wlo + (whi - wlo) * i / 4.0
        y = r2.y + int((whi - v) / (whi - wlo) * r2.h)
        pygame.draw.line(s, GRID_C, (r2.x, y), (r2.right, y), 1)
        _render(s, f"{v:.0f}", 48, y - asz // 2, size=asz)
    xs = np.linspace(r2.x, r2.right, len(pts))
    ys2 = [r2.y + int((whi - min(whi, max(wlo, w))) / (whi - wlo) * r2.h) for w in ws]
    pygame.draw.lines(s, BLACK, False, list(zip(xs, ys2)), 2)
    return s


def render_center(api, dt, typhoon=None, ref=None, w=800, h=560) -> pygame.Surface:
    """中心气压与最大风速演化(自适应坐标轴;无实况数据时不画 JMA)。"""
    from simulator.typhoons import gen as _g
    s = new_canvas(w, h)
    states = (typhoon.get('states') if typhoon else None) or []
    draw_title(s, "Typhoon Intensity | Center Pressure (hPa) & Max Wind (kt)")
    asz = _axis_size(w)
    if ref:
        _render(s, "JMA", w - 60, 30, size=12, color=(220, 40, 40))
    _render(s, MODEL_TAG, w - 120, 30, size=12)
    if not states:
        return s
    ps = [st['p'] for st in states]
    ws = [st['w'] for st in states]
    pmin, pmax = min(ps), max(ps)
    wmax = max(ws) or 1.0
    # 气压轴上限= 背景气压(台风位置处环境mslp 逐时刻变化,随环境时间动态浮动
    env_hi = pmax
    if api is not None:
        for st in states:
            t = st['t']
            mslp = api.get_field('mslp', (int(t[:4]), int(t[4:6]), int(t[6:8])))
            if mslp is not None:
                o = _g._bilinear_at(mslp, st['la'], st['lo'])
                if o == o and o > env_hi:
                    env_hi = o
    plo, phi = max(900.0, min(pmin, env_hi) - 8), env_hi + 8
    wlo, whi = 0.0, wmax * 1.15 + 4
    # 上 中心气压
    r1 = pygame.Rect(90, 60, w - 130, h // 2 - 70)
    _render(s, "hPa", 12, r1.y - 4, size=asz)
    for i in range(5):
        p = plo + (phi - plo) * i / 4.0
        y = r1.y + int((phi - p) / (phi - plo) * r1.h)
        pygame.draw.line(s, GRID_C, (r1.x, y), (r1.right, y), 1)
        _render(s, f"{p:.0f}", 48, y - asz // 2, size=asz)
    xs = np.linspace(r1.x, r1.right, len(ps))
    ys = [r1.y + int((phi - min(phi, max(plo, p))) / (phi - plo) * r1.h) for p in ps]
    pygame.draw.lines(s, BLACK, False, list(zip(xs, ys)), 2)
    # 下 最大风速kt)
    r2 = pygame.Rect(90, h // 2 + 24, w - 130, h // 2 - 76)
    _render(s, "kt", 12, r2.y - 4, size=asz)
    for i in range(5):
        v = wlo + (whi - wlo) * i / 4.0
        y = r2.y + int((whi - v) / (whi - wlo) * r2.h)
        pygame.draw.line(s, GRID_C, (r2.x, y), (r2.right, y), 1)
        _render(s, f"{v:.0f}", 48, y - asz // 2, size=asz)
    xs = np.linspace(r2.x, r2.right, len(ws))
    ys2 = [r2.y + int((whi - min(whi, max(wlo, w))) / (whi - wlo) * r2.h) for w in ws]
    pygame.draw.lines(s, BLACK, False, list(zip(xs, ys2)), 2)
    # 实况(红点,可空)
    if ref:
        rx = np.linspace(r1.x, r1.right, len(ref))
        ry = [r1.y + int((phi - min(phi, max(plo, p[1]))) / (phi - plo) * r1.h) for p in ref]
        for x, y in zip(rx, ry):
            pygame.draw.circle(s, (220, 40, 40), (int(x), int(y)), 3)
    return s


def render_track(api, dt, typhoon=None, best=None, w=800, h=560) -> pygame.Surface:
    """路径对比: 模拟按中心气压着色,时间标注稀疏化。"""
    s = new_canvas(w, h)
    draw_title(s, f"Typhoon Track ({MODEL_TAG}, colored by central pressure)")
    if best:
        _render(s, "Best Track", 10, 30, size=12, color=(220, 40, 40))
    _render(s, MODEL_TAG, 10, 46, size=12, color=(30, 80, 220))
    rect = pygame.Rect(60, 60, w - 120, h - 120)
    inner = _map_inner(rect, 80.0, 50.0)
    draw_grid(s, inner, lat_step=5, lon_step=15, lat0=0, lon0=100, lat1=50, lon1=180, labels=False)
    draw_map(s, inner, lon0=100, lon1=180, lat0=0, lat1=50, edge=(230, 210, 60))
    if typhoon and typhoon.get('states'):
        states = typhoon['states']
        for i, st in enumerate(states):
            px, py = _map_rect(inner, 100, 180, 0, 50, st['lo'], st['la'])
            col = track_pressure_color(st['p'])
            pygame.draw.circle(s, col, (px, py), 4)
            if i % 12 == 0 or i == len(states) - 1:      # 每 3 天标一次
                _render(s, f"{st['t'][4:6]}/{st['t'][6:8]}", px + 6, py - 6, size=12)
        pts = [_map_rect(inner, 100, 180, 0, 50, st['lo'], st['la']) for st in states]
        if len(pts) > 1:
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                pygame.draw.line(s, (30, 80, 220), (x0, y0), (x1, y1), 1)
    if best:
        for la, lo in best:
            px, py = _map_rect(inner, 100, 180, 0, 50, lo, la)
            pygame.draw.circle(s, (220, 40, 40), (px, py), 3)
    cb = pygame.Rect(w - 150, h - 30, 120, 14)
    for i in range(cb.w):
        p = 990 + i / cb.w * 40
        pygame.draw.rect(s, track_pressure_color(p), (cb.x + i, cb.y, 1, cb.h))
    _render(s, "990 hPa", cb.x, cb.y - 14, size=12)
    _render(s, "1030 hPa", cb.right - 40, cb.y - 14, size=12)
    return s


# ════════════════════════════════════════════════ 合成占位图════════════════════════════════════════════════

def _synth_olr(api, dt, typhoons=None) -> np.ndarray:
    """OLR 合成: 高湿/台风区低 OLR(深对流)。"""
    olr = np.full((NLAT, NLON), 300.0)
    rh = api.get_field('rh700', dt) if api else None
    if rh is not None:
        olr = 300.0 - 1.8 * (np.nan_to_num(rh) - 30.0)
    if not typhoons:
        return olr
    ty = typhoons[0]
    la, lo, w = ty['la'], ty['lo'], ty['w']
    dlon = (LONS - lo + 180) % 360 - 180
    d = np.hypot(LATS[:, None] - la, dlon[None, :])
    r_cdo = max(1.0, (60.0 + 0.45 * w) / 111.0)
    eye_r = max(0.2, (25.0 - 0.05 * w) / 111.0)
    core = 120.0 + 60.0 * (1.0 - w / 200.0)
    out = np.where(d <= eye_r, 260.0, olr)
    out = np.where((d > eye_r) & (d <= r_cdo), core, out)
    return out


def _synth_refl(typhoons, span) -> np.ndarray:
    """雷达合成: 台风眼/眼墙/雨带 → dBZ。"""
    refl = np.zeros((NLAT, NLON))
    if not typhoons:
        return refl
    ty = typhoons[0]
    la, lo, w = ty['la'], ty['lo'], ty['w']
    eye_r = max(0.3, 1.5 - w / 200.0)
    wall_r = max(0.8, eye_r + 1.2)
    dlon = (LONS - lo + 180) % 360 - 180
    d = np.hypot(LATS[:, None] - la, dlon[None, :])
    refl = np.where(d <= eye_r, 15.0, refl)
    refl = np.where((d > eye_r) & (d <= wall_r), 65.0, refl)
    refl = np.where((d > wall_r) & (d <= wall_r + 4.0),
                    45.0 - 8.0 * (d - wall_r), refl)
    return refl


# ════════════════════════════════════════════════ 单站序列(自定义站点════════════════════════════════════════════════

def station_series_at(states, api, la: float, lo: float) -> List[dict]:
    """指定站点 (la, lo) 的气压10m 风时间序列
    台风涡旋(修正 Rankine,由逐6h 状态vmax/r34 反演)叠加环境背景风
    与 mslp 气压凹陷 → 站点处逐时刻观测。"""
    from simulator.typhoons import gen as _g
    out = []
    for st in states:
        t = st['t']
        date = (int(t[:4]), int(t[4:6]), int(t[6:8]))
        dlon = (lo - st['lo'] + 180.0) % 360.0 - 180.0
        r = math.hypot(la - st['la'], dlon) * 111.0
        vmax = float(st['w'])
        r34 = max(50.0, float(st.get('r34', 60)) * 1.852)
        rmax = 40.0
        if r <= rmax:
            v = vmax * (r / max(rmax, 1e-6))
        else:
            a = math.log(34.0 / vmax) / math.log(rmax / r34) if vmax > 34 else 0.55
            v = vmax * (rmax / max(r, 1.0)) ** a
        v_ms = v * 0.514444
        env_ms = 0.0
        oci = 1008.0
        if api is not None:
            uv = api.get_field('uv_steer', date)
            if uv is not None:
                u = _g._bilinear_at(uv[0], la, lo)
                vv = _g._bilinear_at(uv[1], la, lo)
                env_ms = math.hypot(0.0 if u != u else u,
                                    0.0 if vv != vv else vv)
            mslp = api.get_field('mslp', date)
            if mslp is not None:
                o = _g._bilinear_at(mslp, la, lo)
                oci = o if o == o else 1008.0
        # 气压凹陷: 相对 vmax(kt)的比值须单位一致;v 恒 ≤ vmax,故 min(v, vmax)/vmax∈[0,1]
        ratio = min(v, vmax) / max(vmax, 1e-6)
        dp = 0.35 * vmax * ratio ** 2 * math.exp(-r / (rmax * 6.0))
        out.append({'t': t, 'p': float(oci - dp), 'w': float(v_ms + env_ms)})
    return out


# ════════════════════════════════════════════════ 工具 ════════════════════════════════════════════════

def ft_title(surf, rect, text) -> None:
    _render(surf, text, rect.x, rect.y + 2, size=16)


def ft_render(surf, text, x, y, color=BLACK) -> None:
    _render(surf, text, x, y, size=14, color=color)
