# py/spline.py
"""Monotone Cubic (Fritsch-Carlson) 样条 + 弧长参数化。"""
from __future__ import annotations

import bisect
import math
from typing import List, Tuple


# ── Centripetal Catmull-Rom（无抽动） ─────────────────────

def _lerp(p, q, alpha):
    return (p[0] + (q[0] - p[0]) * alpha, p[1] + (q[1] - p[1]) * alpha)


def _centripetal_catmull_rom(p0: Tuple[float, float], p1: Tuple[float, float],
                              p2: Tuple[float, float], p3: Tuple[float, float],
                              t0: float, t1: float, t2: float, t3: float,
                              u: float) -> Tuple[float, float]:
    """Centripetal Catmull-Rom：p1→p2 段。u∈[0,1] 映射到 [t1,t2]。
    用 sqrt(chord) 参数化避免匀参导致的 overshoot / 抽动。"""
    t = t1 + u * (t2 - t1)
    # 防御性钳制：u 越界或重复点导致 t 超出 [t0,t3] 时,避免外推产生发散。
    t = max(t0, min(t3, t))

    a1 = _lerp(p0, p1, (t - t0) / (t1 - t0)) if t1 > t0 else p1
    a2 = _lerp(p1, p2, (t - t1) / (t2 - t1)) if t2 > t1 else p2
    a3 = _lerp(p2, p3, (t - t2) / (t3 - t2)) if t3 > t2 else p3

    b1 = _lerp(a1, a2, (t - t0) / (t2 - t0)) if t2 > t0 else a2
    b2 = _lerp(a2, a3, (t - t1) / (t3 - t1)) if t3 > t1 else a3

    return _lerp(b1, b2, (t - t1) / (t2 - t1)) if t2 > t1 else b2


def catmull_rom(p0: Tuple[float, float], p1: Tuple[float, float],
                p2: Tuple[float, float], p3: Tuple[float, float],
                t: float) -> Tuple[float, float]:
    """Uniform Catmull-Rom（保留兼容,新代码请用 centripetal 版本）。

    t=0 返回 p1、t=1 返回 p2。对等距/直线输入在 t=0.5 处取中点,
    端点重合时曲线收缩为该点（通过 tests/test_spline.py）。
    """
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


# ── Monotone Cubic (Fritsch-Carlson) ─────────────────────

def _fritsch_carlson_tangents(pts: List[Tuple[float, float]],
                               cum_len: List[float]) -> Tuple[List[float], List[float]]:
    """对 x、y 分别计算 Fritsch-Carlson 单调导数。

    以弦长累计值 t(s) 为参数，分别对 x(t)、y(t) 拟合单调三次样条。
    返回 (tx_list, ty_list)，每个元素为该点的 dx/dt、dy/dt。
    """
    n = len(pts)
    if n < 2:
        return ([0.0] * n, [0.0] * n)

    def _slopes(values, lens):
        s = []
        for i in range(len(values) - 1):
            h = lens[i + 1] - lens[i]
            s.append((values[i + 1] - values[i]) / h if h > 0 else 0.0)
        return s

    sx = _slopes([p[0] for p in pts], cum_len)
    sy = _slopes([p[1] for p in pts], cum_len)

    def _fcd(slopes):
        """给定 n 个点之间的 n-1 条割线斜率，返回 n 个点的单调导数。"""
        m = len(slopes) + 1
        d = [0.0] * m

        for i in range(1, m - 1):
            sl = slopes[i - 1]
            sr = slopes[i]
            if sl * sr <= 0.0:
                d[i] = 0.0
            else:
                d[i] = (sl + sr) / 2.0
                limit = 3.0 * min(abs(sl), abs(sr))
                if abs(d[i]) > limit:
                    d[i] = limit if d[i] > 0 else -limit

        # 端点导数：用相邻割线 + 镜像外推
        if m >= 2:
            if m >= 3 and d[1] != 0.0:
                d[0] = 2.0 * slopes[0] - d[1]
                if slopes[0] * d[0] <= 0.0:
                    d[0] = 0.0
                elif abs(d[0]) > 3.0 * abs(slopes[0]):
                    d[0] = 3.0 * slopes[0]
            else:
                d[0] = slopes[0]

            if m >= 3 and d[m - 2] != 0.0:
                d[m - 1] = 2.0 * slopes[-1] - d[m - 2]
                if slopes[-1] * d[m - 1] <= 0.0:
                    d[m - 1] = 0.0
                elif abs(d[m - 1]) > 3.0 * abs(slopes[-1]):
                    d[m - 1] = 3.0 * slopes[-1]
            else:
                d[m - 1] = slopes[-1]

        return d

    return _fcd(sx), _fcd(sy)


def _cubic_hermite(p0: Tuple[float, float], p1: Tuple[float, float],
                   m0x: float, m0y: float, m1x: float, m1y: float,
                   h: float, u: float) -> Tuple[float, float]:
    """三次 Hermite 插值：p0→p1 段，(m0x,m0y)=p0 处导数，(m1x,m1y)=p1 处导数，
    h=段长（参数差），u∈[0,1)。
    """
    u2 = u * u
    u3 = u2 * u
    H00 = 2.0 * u3 - 3.0 * u2 + 1.0
    H10 = h * (u3 - 2.0 * u2 + u)
    H01 = -2.0 * u3 + 3.0 * u2
    H11 = h * (u3 - u2)
    x = H00 * p0[0] + H10 * m0x + H01 * p1[0] + H11 * m1x
    y = H00 * p0[1] + H10 * m0y + H01 * p1[1] + H11 * m1y
    return (x, y)


def build_monotone_spline(points: List[Tuple[float, float]],
                          segments: int = 10) -> List[Tuple[float, float]]:
    """用 Fritsch-Carlson 单调三次样条加密点列。需要至少 2 个点。"""
    n = len(points)
    if n < 2:
        return list(points)
    segments = max(1, int(segments))

    # 弦长参数化
    cum = [0.0]
    for i in range(1, n):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        cum.append(cum[-1] + math.sqrt(dx * dx + dy * dy))

    tx, ty = _fritsch_carlson_tangents(points, cum)

    result: List[Tuple[float, float]] = []
    inv_segments = 1.0 / segments
    for i in range(n - 1):
        p0, p1 = points[i], points[i + 1]
        h = cum[i + 1] - cum[i]
        for s in range(segments):
            u = s * inv_segments
            result.append(_cubic_hermite(
                p0, p1, tx[i], ty[i], tx[i + 1], ty[i + 1], h, u))
    result.append(points[-1])
    return result


# ── 默认构建（Monotone Cubic） ────────────────────────────

def build_spline(points: List[Tuple[float, float]],
                 segments: int = 10,
                 mode: str = "monotone") -> List[Tuple[float, float]]:
    """构建平滑样条曲线。mode: "monotone" (Fritsch-Carlson), "catmull" (Catmull-Rom)。

    monotone 模式为 Fritsch-Carlson 单调三次样条:严格穿过每个原始点,
    每段在 x/y 方向单调,不会 overshoot。
    """
    if mode == "catmull":
        return _build_catmull_rom_spline(points, segments)
    return build_monotone_spline(points, segments)


def _build_catmull_rom_spline(points: List[Tuple[float, float]],
                              segments: int = 10) -> List[Tuple[float, float]]:
    """用 Centripetal Catmull-Rom 样条加密点列。需要至少 4 个点，否则回退单调样条。"""
    n = len(points)
    if n < 4:
        return build_monotone_spline(points, segments)
    segments = max(1, int(segments))

    # 计算弦长作为 centripetal 参数 (alpha=0.5)
    chords = [0.0]
    for i in range(1, n):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        chords.append(chords[-1] + math.sqrt(math.sqrt(dx * dx + dy * dy)))

    inv_segments = 1.0 / segments
    # 第一段:p0→p1。前导幻影点复制 p0(且 t0=t1),使首段也能获得平滑混合,
    # 否则首条控制边是 p0→p1 的直线跳变,与中间/末段的平滑曲线不连续。
    # result 以第一段 u=0 处的 p1(=points[0])为起点,避免首点重复。
    result: List[Tuple[float, float]] = []
    p0, p1, p2, p3 = points[0], points[0], points[1], points[2]
    t0, t1, t2, t3 = chords[0], chords[0], chords[1], chords[2]
    for s in range(segments):
        result.append(_centripetal_catmull_rom(p0, p1, p2, p3,
                                               t0, t1, t2, t3, s * inv_segments))
    # 中间段
    for i in range(1, n - 2):
        p0, p1, p2, p3 = points[i - 1], points[i], points[i + 1], points[i + 2]
        t0, t1, t2, t3 = chords[i - 1], chords[i], chords[i + 1], chords[i + 2]
        for s in range(segments):
            result.append(_centripetal_catmull_rom(p0, p1, p2, p3,
                                                   t0, t1, t2, t3, s * inv_segments))
    # 最后一段:末点作为幻影点复制(p2=p3),t3 外推避免末段参数退化。
    p0, p1, p2, p3 = points[n - 3], points[n - 2], points[n - 1], points[n - 1]
    t0, t1, t2 = chords[n - 3], chords[n - 2], chords[n - 1]
    t3 = t2 + (t2 - t1)
    for s in range(segments):
        result.append(_centripetal_catmull_rom(p0, p1, p2, p3,
                                               t0, t1, t2, t3, s * inv_segments))
    result.append(points[-1])
    return result


# ── 弧长 ──

def compute_arc_lengths(pts: List[Tuple[float, float]]) -> List[float]:
    """计算点列的累计弧长。"""
    arcs = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        arcs.append(arcs[-1] + math.sqrt(dx * dx + dy * dy))
    return arcs


def position_at_arc(pts: List[Tuple[float, float]],
                    arcs: List[float],
                    target: float) -> Tuple[float, float]:
    """给定弧长距离，求曲线上对应位置（线性插值于弧长段之间）。"""
    if not pts:
        return (0.0, 0.0)
    if target <= arcs[0]:
        return pts[0]
    if target >= arcs[-1]:
        return pts[-1]
    i = bisect.bisect_left(arcs, target, 1)
    if i >= len(arcs):
        return pts[-1]
    seg_len = arcs[i] - arcs[i - 1]
    t = (target - arcs[i - 1]) / seg_len if seg_len > 0 else 0
    x = pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t
    y = pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t
    return (x, y)
