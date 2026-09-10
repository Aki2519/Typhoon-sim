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
