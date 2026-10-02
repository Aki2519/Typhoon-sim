# app/render_geom.py
"""跨缝几何: 屏幕坐标的环绕归一化与视口裁剪。

渲染层里所有折线/多边形都必须先经过这里。地图在横向上是周期性的
(周期 = 地图宽度 × scale 像素), 单个投影点可以落在任意一个副本上;
如果不把相邻点统一到同一个副本, 跨 0°/360° 缝的相邻两点就会相差
整整一个周期, 画出横跨全屏的伪线段——现象就是"路径/洋区边界乱飞"。

设计约束(改动前请读完):
  - 平移量必须是 wrap 的整数倍: 与 geo_to_screen 的两段式取整
    (int(px*scale) - int(view*scale)) 相位完全一致, 不引入亚像素抖动;
  - 归一化以"第一个点"为锚: 纯视图平移时各点相对首点的差值不变,
    因此路径缓存的"整页平移复用"仍然成立; 只有当首点自身翻面时整条
    链会统一平移 ±wrap, 此时复用逻辑因位移超限而自动退回全量重建;
  - 归一化只改变副本选择, 不改变几何: 归一化前后的点在屏幕上是同一
    地理位置的不同副本。
"""
from __future__ import annotations

import math
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

import pygame

Point = Tuple[float, float]
Segment = Tuple[Point, Point]


def wrap_shift(x: float, ref: float, wrap: float) -> float:
    """把 x 平移到离 ref 最近的等价副本所需的平移量(整数倍 wrap)。"""
    if wrap <= 0:
        return 0.0
    return round((ref - x) / wrap) * wrap


def normalize_chain(points: Sequence[Point], wrap: float,
                    anchor: Optional[float] = None) -> List[Point]:
    """逐点按 ±wrap 归一化, 使相邻点落在同一地图副本内。

    anchor 默认取首点自身(y 不变), 这样纯平移下结果与视图无关;
    传入 anchor(如首点的归一化 x)可让另一条链(平滑点)与前一条对齐。
    返回新列表, 不修改输入。
    """
    pts = list(points)
    if not pts:
        return []
    if wrap <= 0 or len(pts) < 2:
        return pts
    if anchor is None:
        anchor = pts[0][0]
    out: List[Point] = []
    prev_x = pts[0][0] + wrap_shift(pts[0][0], anchor, wrap)
    out.append((float(prev_x), float(pts[0][1])))
    for x, y in pts[1:]:
        nx = float(x) + wrap_shift(float(x), prev_x, wrap)
        out.append((nx, float(y)))
        prev_x = nx
    return out


def clip_segment(p0: Point, p1: Point, width: float, height: float) -> Optional[Segment]:
    """Liang-Barsky 裁剪到 [0,width]x[0,height]; 完全在视口外返回 None。"""
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - 0.0), (dx, float(width) - x0),
                 (-dy, y0 - 0.0), (dy, float(height) - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return None
            if r < t1:
                t1 = r
    if t0 > t1:
        return None
    return ((x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy))


def segments_of(points: Sequence[Point], wrap: float, close: bool = False,
                max_seg: Optional[float] = None) -> Iterator[Segment]:
    """产出折线/多边形的边: 已归一化; max_seg 用于断开真实数据跳变(非跨缝)。"""
    pts = normalize_chain(points, wrap)
    if close and len(pts) >= 3:
        pts = pts + [pts[0]]
    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        if max_seg is not None:
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            if dx * dx + dy * dy > max_seg * max_seg:
                continue
        yield p0, p1


def clipped_segments(points: Sequence[Point], wrap: float, width: float,
                     height: float, close: bool = False,
                     max_seg: Optional[float] = None) -> Iterator[Segment]:
    """归一化 + 视口裁剪后的可见线段(供 pygame.draw.line 直接使用)。"""
    for p0, p1 in segments_of(points, wrap, close=close, max_seg=max_seg):
        seg = clip_segment(p0, p1, width, height)
        if seg is not None:
            yield seg


def draw_polyline(surface: pygame.Surface, color, points: Sequence[Point],
                  wrap: float, width: int = 1, close: bool = False,
                  max_seg: Optional[float] = None,
                  viewport: Optional[Tuple[float, float]] = None) -> int:
    """归一化 + 裁剪后绘制折线, 返回实际画出的段数(测试可直接断言)。"""
    if viewport is None:
        viewport = (surface.get_width(), surface.get_height())
    drawn = 0
    for (ax, ay), (bx, by) in clipped_segments(points, wrap, viewport[0],
                                               viewport[1], close=close,
                                               max_seg=max_seg):
        pygame.draw.line(surface, color, (int(ax), int(ay)),
                         (int(bx), int(by)), width)
        drawn += 1
    return drawn

_GRID_LAYER_CACHE = {}


def _grid_layer(sw: int, sh: int):
    """复用整屏网格图层。每帧新建 Surface 会产生 ~5MB/帧的分配抖动(编辑模式下
    每帧都画), 复用后只在尺寸变化时新建。"""
    key = (sw, sh)
    surf = _GRID_LAYER_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface(key, pygame.SRCALPHA)
        _GRID_LAYER_CACHE[key] = surf
    else:
        surf.fill((0, 0, 0, 0))
    return surf


def draw_snap_grid(surface, sim, step: float, sw: int = None, sh: int = None) -> None:
    """按 step(默认 0.1°)在整屏画经纬吸附网格: step 细线 + 每 1° 一条粗线。

    洋区顶点编辑(ocean_edit)与报点编辑(renderer)共用同一套观感与实现:
      - 投影走调用方的等距圆柱投影 sim.latlon_to_screen, 经度逐条环绕,
        跨 0°/360° 缝不会漏画;
      - 半透明线画在单独图层上, 不遮挡底图;
      - 间距过密(step 在屏幕上不足 1.5px)时只画 1° 粗线, 更密则整块跳过 ——
        否则整球视野下每帧要画上万条线, 直接卡死。
    """
    sw = surface.get_width() if sw is None else int(sw)
    sh = surface.get_height() if sh is None else int(sh)
    try:
        step = float(step)
    except (TypeError, ValueError):
        return
    if not (step > 0.0) or step < 0.01:
        # 0 = 设置里"关"; 负值会让下面的 while 永不终止; 过小会画几十万条线
        return
    proj = getattr(sim, 'latlon_to_screen', None)
    if proj is None:
        proj = sim.view.latlon_to_screen
    lo_min, lo_max = float(sim.mlo), float(sim.Mlo)
    la_min, la_max = float(sim.mla), float(sim.Mla)
    if lo_max <= lo_min:
        lo_max += 360.0
    # 步长在当前缩放下占多少像素(等距圆柱: 经线间距与纬度无关)
    x0, _ = proj(la_min, lo_min)
    x1, _ = proj(la_min, lo_min + step)
    _, y0 = proj(la_min, lo_min)
    _, y1 = proj(la_min + step, lo_min)
    step_x, step_y = abs(x1 - x0), abs(y1 - y0)
    draw_fine = max(step_x, step_y) >= 1.5            # 亚像素细线只会变成糊屏
    draw_major = max(step_x, step_y) / step >= 1.5     # 1° 的像素间距
    if not (draw_fine or draw_major):
        return
    dark = bool(getattr(sim, 'dark_mode', False))
    fine = (60, 70, 90, 60) if dark else (90, 110, 140, 55)
    major = (255, 255, 255, 90) if dark else (0, 0, 0, 70)
    layer = _grid_layer(sw, sh)
    lon = math.floor(lo_min / step) * step
    while lon <= lo_max + 1e-9:
        is_major = abs(lon - round(lon)) < 1e-9
        if (is_major and draw_major) or (not is_major and draw_fine):
            nl = ((lon % 360.0) + 360.0) % 360.0
            try:
                x, _y = proj(la_max, nl)
            except Exception:
                lon += step
                continue
            if 0 <= x <= sw:
                pygame.draw.line(layer, major if is_major else fine,
                                 (int(x), 0), (int(x), sh), 2 if is_major else 1)
        lon += step
    lat = math.floor(la_min / step) * step
    while lat <= la_max + 1e-9:
        is_major = abs(lat - round(lat)) < 1e-9
        if (is_major and draw_major) or (not is_major and draw_fine):
            try:
                _x, y = proj(lat, lo_min)
            except Exception:
                lat += step
                continue
            if 0 <= y <= sh:
                pygame.draw.line(layer, major if is_major else fine,
                                 (0, int(y)), (sw, int(y)), 2 if is_major else 1)
        lat += step
    surface.blit(layer, (0, 0))
