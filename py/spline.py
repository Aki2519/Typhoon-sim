# py/spline.py
"""Catmull-Rom 样条曲线 + 弧长参数化。"""
from __future__ import annotations

import math
from typing import List, Tuple


def catmull_rom(p0: Tuple[float, float], p1: Tuple[float, float],
                p2: Tuple[float, float], p3: Tuple[float, float],
                t: float) -> Tuple[float, float]:
    """Catmull-Rom 插值：p1→p2 段，t∈[0,1]。"""
    t2 = t * t
    t3 = t2 * t
    x = 0.5 * ((2.0 * p1[0]) +
               (-p0[0] + p2[0]) * t +
               (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2 +
               (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3)
    y = 0.5 * ((2.0 * p1[1]) +
               (-p0[1] + p2[1]) * t +
               (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2 +
               (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3)
    return (x, y)


def build_spline(points: List[Tuple[float, float]],
                 segments: int = 10) -> List[Tuple[float, float]]:
    """对点列构建完整 Catmull-Rom 样条曲线。需要至少 2 个点。"""
    n = len(points)
    if n < 2:
        return list(points)
    result: List[Tuple[float, float]] = []
    for i in range(n - 1):
        p0 = points[max(i - 1, 0)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(i + 2, n - 1)]
        for s in range(segments):
            t = s / segments
            result.append(catmull_rom(p0, p1, p2, p3, t))
    result.append(points[-1])
    return result


def compute_arc_lengths(pts: List[Tuple[float, float]]) -> List[float]:
    """计算点列的累计弧长。"""
    arcs = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        arcs.append(arcs[-1] + math.hypot(dx, dy))
    return arcs


def position_at_arc(pts: List[Tuple[float, float]],
                    arcs: List[float],
                    target: float) -> Tuple[float, float]:
    """给定弧长距离，求曲线上对应位置（线性插值于弧长段之间）。"""
    if target <= arcs[0]:
        return pts[0]
    if target >= arcs[-1]:
        return pts[-1]
    for i in range(1, len(arcs)):
        if arcs[i] >= target:
            seg_len = arcs[i] - arcs[i - 1]
            t = (target - arcs[i - 1]) / seg_len if seg_len > 0 else 0
            x = pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t
            y = pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t
            return (x, y)
    return pts[-1]
