# app/paint_aa.py
"""抗锯齿绘制原语 —— 供"绘画模式"所有渲染路径共用。

## 为什么存在

用户明确点名：绘画里**定点/原点太"像素"**，肉眼可见锯齿状圆。

根因（2026-10-02 实测，`app/paint_render.py`）：
  · `:457` `pygame.draw.circle(...)`  —— pygame 的基础图元**不开抗锯齿**
  · `:451` `pygame.draw.rect(...)`    —— 同上
  · `:455` `pygame.draw.polygon(...)` —— 同上
  · `:493` `pygame.draw.circle(..., 1)`（风圈）—— 同上
  · `:467` `r = max(2, int(round(...)))` —— 半径被取整，进一步放大锯齿

## 做法

**超采样精灵缓存**：把图元按 `SS` 倍分辨率画进一张临时面，再 `smoothscale` 回目标尺寸。
降采样的过程本身就是 SS×SS 的盒式平均 ⇒ 边缘得到真正的 alpha 渐变（而不是
`gfxdraw` 那种只补一圈 1px 的近似）。

每个 (形状, 颜色, 尺寸) 组合**只生成一次**并缓存 ⇒ 同一张图里成百上千个点不会重复付代价。

## 与既有代码的关系

`app/paint_render.py` 的 `_draw_shape` 与 `_draw_radii` 应改为调用本模块；
`app/paint_render_qq.py`（QQ 机器人样式）从第一行起就用本模块。
两者**只读**本文件，不要各自复制一份超采样逻辑。

## 缓存纪律

`_CACHE` 有**条目上限**且按字节估算淘汰（本项目已被"条数上限"坑过两次：
名文本缓存 256 条自噬、landfall 缓存只按条数 24 封顶导致最坏 1.37 GB）。
这里用 LRU + 字节上限双约束。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, Sequence, Tuple

import pygame

#: 超采样倍数。4 已足够平滑；再高收益递减而内存是平方增长。
SS = 4

#: 精灵缓存上限（字节估算）。按 RGBA 宽×高×4 计。
_CACHE_MAX_BYTES = 6 * 1024 * 1024
_CACHE: "OrderedDict[tuple, pygame.Surface]" = OrderedDict()
_CACHE_BYTES = 0

Color = Tuple[int, int, int]
ColorA = Tuple[int, int, int, int]


def _cache_get(key: tuple):
    surf = _CACHE.get(key)
    if surf is not None:
        _CACHE.move_to_end(key)
    return surf


def _cache_put(key: tuple, surf: pygame.Surface) -> None:
    global _CACHE_BYTES
    n = surf.get_width() * surf.get_height() * 4
    if n > _CACHE_MAX_BYTES:      # 单个就超预算 ⇒ 不进缓存（避免把整池冲掉）
        return
    if key in _CACHE:
        return
    _CACHE[key] = surf
    _CACHE_BYTES += n
    while _CACHE_BYTES > _CACHE_MAX_BYTES and len(_CACHE) > 1:
        _, ev = _CACHE.popitem(last=False)
        _CACHE_BYTES -= ev.get_width() * ev.get_height() * 4


def cache_stats() -> dict:
    """确定性指标：条目数与字节数（供验收/回归断言用，不影响绘制）。"""
    return {"items": len(_CACHE), "bytes": _CACHE_BYTES,
            "max_bytes": _CACHE_MAX_BYTES, "ss": SS}


def clear_cache() -> None:
    global _CACHE_BYTES
    _CACHE.clear()
    _CACHE_BYTES = 0


def _supersample(size: Tuple[int, int], paint) -> pygame.Surface:
    """在 SS 倍画布上执行 paint(surf, s)，再降采样回 size。"""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    big = pygame.Surface((w * SS, h * SS), pygame.SRCALPHA)
    paint(big, SS)
    return pygame.transform.smoothscale(big, (w, h))


def aa_disc(surf: pygame.Surface, color: ColorA, center: Sequence[float],
            radius: float) -> None:
    """抗锯齿实心圆（定点）。radius 可为浮点。"""
    r = float(radius)
    if r <= 0:
        return
    rp = max(1, int(round(r)))
    key = ("disc", rp, tuple(color))
    sprite = _cache_get(key)
    if sprite is None:
        d = rp * 2 + 2
        sprite = _supersample((d, d), lambda s, ss, rr=rp: pygame.draw.circle(
            s, color, (d * ss // 2, d * ss // 2), rr * ss))
        _cache_put(key, sprite)
    surf.blit(sprite, (int(round(center[0] - sprite.get_width() / 2.0)),
                       int(round(center[1] - sprite.get_height() / 2.0))))


def aa_ring(surf: pygame.Surface, color: ColorA, center: Sequence[float],
            radius: float, width: int = 1) -> None:
    """抗锯齿圆环（风圈）。"""
    r = max(1, int(round(float(radius))))
    wdt = max(1, int(width))
    #: 环宽在超采样画布上等比放大，降采样后才不会细到消失
    key = ("ring", r, wdt, tuple(color))
    sprite = _cache_get(key)
    if sprite is None:
        d = (r + wdt) * 2 + 2
        sprite = _supersample((d, d), lambda s, ss: pygame.draw.circle(
            s, color, (d * ss // 2, d * ss // 2), r * ss, wdt * ss))
        _cache_put(key, sprite)
    surf.blit(sprite, (int(round(center[0] - sprite.get_width() / 2.0)),
                       int(round(center[1] - sprite.get_height() / 2.0))))


def aa_poly(surf: pygame.Surface, color: Color, points: Iterable[Sequence[float]]) -> None:
    """抗锯齿实心多边形（三角/方块等）。points 为世界坐标。"""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = int(min(xs)) - 2, int(min(ys)) - 2
    w = int(max(xs)) - x0 + 4
    h = int(max(ys)) - y0 + 4
    local = [(x - x0, y - y0) for x, y in pts]
    sprite = _supersample((w, h), lambda s, ss: pygame.draw.polygon(
        s, color, [(x * ss, y * ss) for x, y in local]))
    surf.blit(sprite, (x0, y0))


def aa_rect(surf: pygame.Surface, color: Color, cx: float, cy: float, r: float) -> None:
    """抗锯齿实心方块（中心 + 半边长）。"""
    rr = max(1.0, float(r))
    aa_poly(surf, color, [(cx - rr, cy - rr), (cx + rr, cy - rr),
                          (cx + rr, cy + rr), (cx - rr, cy + rr)])


#: 粗线单次超采样精灵的**最长边上限**（像素）。见 `aa_line` 的分块说明。
#: 依据：精灵字节 = (w+2*width)*(h+2*width)*SS^2*4；SS=4 时
#:   96 px 段 ≈ 104*104*16*4 ≈ 0.69 MB
#:  1000 px 段 ≈ 1000*1000*16*4 ≈ **64 MB**  ← app-feat 在 P3 实测到并报警的坑
_AA_LINE_CHUNK_PX = 96


def aa_line(surf: pygame.Surface, color: Color, p1: Sequence[float],
            p2: Sequence[float], width: int = 1) -> None:
    """抗锯齿线段。width<=1 用 `aaline`（原生 AA）；更粗用超采样精灵。

    ⚠ **长段必须分块**（2026-10-02 修）：
    粗线走"包围盒超采样精灵"，字节数 ∝ 段长的平方 —— 一条 1000 px 的段会分配
    ~64 MB，在路径图/等值线这种"偶有长跳段"的场景里是**内存地雷**。
    这里按 `_AA_LINE_CHUNK_PX` 把长段切成等长小段分别绘制（相邻段各多画 1 px 消除接缝），
    单次分配因此有上界。短段（绝大多数）行为与以前完全一致。
    """
    w = max(1, int(width))
    pt = (float(p1[0]), float(p1[1]))
    qt = (float(p2[0]), float(p2[1]))
    if w <= 1:
        pygame.draw.aaline(surf, color, pt, qt)
        return
    dx, dy = qt[0] - pt[0], qt[1] - pt[1]
    length = max(abs(dx), abs(dy))
    if length > _AA_LINE_CHUNK_PX:
        #: 等长分块；每块向外延 1 px 覆盖接缝，避免 AA 边缘出现细缝
        n = int(length // _AA_LINE_CHUNK_PX) + 1
        for i in range(n):
            t0 = i / n
            t1 = (i + 1) / n
            a = (pt[0] + dx * t0, pt[1] + dy * t0)
            b = (pt[0] + dx * t1, pt[1] + dy * t1)
            ux = 0.0 if dx == 0 else dx / abs(dx)
            uy = 0.0 if dy == 0 else dy / abs(dy)
            a = (a[0] - ux, a[1] - uy)
            b = (b[0] + ux, b[1] + uy)
            _aa_line_short(surf, color, a, b, w)
        return
    _aa_line_short(surf, color, pt, qt, w)


def _aa_line_short(surf: pygame.Surface, color: Color, pt, qt, w: int) -> None:
    """单块粗线（长度已受 `_AA_LINE_CHUNK_PX` 约束）。"""
    xs = (pt[0], qt[0])
    ys = (pt[1], qt[1])
    x0, y0 = int(min(xs)) - w, int(min(ys)) - w
    ww = int(max(xs)) - x0 + 2 * w
    hh = int(max(ys)) - y0 + 2 * w
    local = [(pt[0] - x0, pt[1] - y0), (qt[0] - x0, qt[1] - y0)]
    sprite = _supersample((ww, hh), lambda s, ss: pygame.draw.line(
        s, color, (local[0][0] * ss, local[0][1] * ss),
        (local[1][0] * ss, local[1][1] * ss), w * ss))
    surf.blit(sprite, (x0, y0))
