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


#: 单张超采样面的**像素数上限**。RGBA 下 16 M px = 64 MB。
#: 超过就**自适应降 SS**（4→3→2），而不是放弃 AA —— 见 `_pick_ss`。
_AA_MAX_SS_PIXELS = 16_000_000

#: 连 SS=2 都放不下时的行为：直接 aliased 绘制（不建临时面）。会**每进程告警一次**。
_poly_warned = False


def _pick_ss(w: int, h: int) -> int:
    """按目标面尺寸挑最大的可用超采样倍数（4→3→2→0）。

    0 = 连 SS=2 都超预算（bbox 面积 > 4 M px，即 >2000×2000）。
    `aa_disc`/`aa_ring` 遇 0 走 aliased 直绘（半径极大时）；
    `aa_poly` 遇 0 **改走分块**（见 `_poly_tiled`），**不掉 AA**。

    ⚠ 为什么必须自适应（2026-10-02 修）：
    原先一律 SS=4 是**内存地雷** —— `renderer.py` 用 `aa_poly` 画"海洋区填充"，
    一个全屏级多边形 1928×1088 在 SS=4 下要 `7712×4352×4 = **134 MB**` 一张临时面。
    同源的另一个坑在 `_draw_path_mixin.py`（组包围盒上限太小 ⇒ 全屏回落 ⇒ 完全无 AA）；
    这里反过来 —— **不设上限会爆内存，设糊涂上限会让最该 AA 的大图失去 AA**。
    正确做法是**降 SS 而不是放弃 AA**：全屏 2.1 M px 在 SS=2 下只要 33.5 MB，AA 仍成立。
    """
    area = max(1, int(w)) * max(1, int(h))
    for ss in (4, 3, 2):
        if area * ss * ss <= _AA_MAX_SS_PIXELS:
            return ss
    return 0


def _clip_poly_rect(pts, x0: float, y0: float, x1: float, y1: float):
    """Sutherland–Hodgman：把多边形裁到矩形 `[x0,x1]×[y0,y1]`。

    非凸多边形会被裁成多段，但本模块只用于**实心填充**（`pygame.draw.polygon`
    对自交/非凸多边形按 even-odd 填充）——裁剪后逐块画仍能拼回原形状。
    """
    def clip_edge(src, inside, inter):
        out = []
        n = len(src)
        for i in range(n):
            a, b = src[i], src[(i + 1) % n]
            ia, ib = inside(a), inside(b)
            if ia:
                out.append(a)
            if ia != ib:
                out.append(inter(a, b))
        return out
    p = list(pts)
    p = clip_edge(p, lambda q: q[0] >= x0,
                  lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0])))
    if not p:
        return []
    p = clip_edge(p, lambda q: q[0] <= x1,
                  lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0])))
    if not p:
        return []
    p = clip_edge(p, lambda q: q[1] >= y0,
                  lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0))
    if not p:
        return []
    p = clip_edge(p, lambda q: q[1] <= y1,
                  lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))
    return p


def _poly_tiled(surf: pygame.Surface, color: Color, pts, w: int, h: int,
                x0: int, y0: int) -> None:
    """超大多边形走**分块超采样**：按 ≤SS=2 预算的网格切块，每块裁剪后各自 AA 再 blit。

    内存有硬上界（单块 ≤ `_AA_MAX_SS_PIXELS` 像素），**AA 全尺寸保留** ——
    这是与 `_draw_path_mixin._draw_segments_aa` 同一思路（P1 的网格分块）。
    相邻块各外扩 1 px 覆盖接缝。
    """
    tile = max(32, int(math.isqrt(_AA_MAX_SS_PIXELS // 4)))   # SS=2 ⇒ 4 倍像素
    for ty in range(y0, y0 + h, tile):
        for tx in range(x0, x0 + w, tile):
            tw = min(tile, x0 + w - tx)
            th = min(tile, y0 + h - ty)
            if tw <= 0 or th <= 0:
                continue
            sub = _clip_poly_rect(pts, tx - 1, ty - 1, tx + tw + 1, ty + th + 1)
            if len(sub) < 3:
                continue
            sw, sh = tw + 2, th + 2
            local = [(px - (tx - 1), py - (ty - 1)) for px, py in sub]
            sprite = _supersample((sw, sh), lambda s, k: pygame.draw.polygon(
                s, color, [(px * k, py * k) for px, py in local]), 2, fill=(color[0], color[1], color[2], 0))
            surf.blit(sprite, (tx - 1, ty - 1))


def _supersample(size: Tuple[int, int], paint, ss: int = SS,
                 fill=None) -> pygame.Surface:
    """在 ss 倍画布上执行 paint(surf, s)，再降采样回 size。"""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    big = pygame.Surface((w * ss, h * ss), pygame.SRCALPHA)
    #: ⚠ 关键: SRCALPHA 面的**透明区 RGB = (0,0,0)**。不先铺色的话,
    #: `smoothscale` 会把这块黑**平均进边缘像素的 RGB** —— 边缘于是拿到
    #: `color x 覆盖率` 的 RGB, 而 alpha 是 `255 x 覆盖率`; 再按**非预乘** blit,
    #: 实际合成变成 `dst*(1-a) + color*a^2` ⇒ **边缘被二次变暗**(视觉上是黑边/暗环)。
    #: 用形状色 + alpha=0 铺底 ⇒ 透明区也带形状色, 降采样后 RGB 处处等于形状色,
    #: 只有 alpha 变化 ⇒ 与非预乘 blit 的语义一致。抗锯齿强度不变。
    if fill is not None:
        big.fill(fill)
    paint(big, ss)
    return pygame.transform.smoothscale(big, (w, h))


def aa_disc(surf: pygame.Surface, color: ColorA, center: Sequence[float],
            radius: float) -> None:
    """抗锯齿实心圆（定点）。radius 可为浮点。

    ⚠ 大半径也走 `_pick_ss` 降 SS（原先无条件 SS=4，半径极大时会无界分配 `4r×4r`）。
    """
    r = float(radius)
    if r <= 0:
        return
    rp = max(1, int(round(r)))
    d = rp * 2 + 2
    ss = _pick_ss(d, d)
    if ss <= 0:                      # 半径大到连 SS=2 都放不下 ⇒ 直绘（无 AA），不爆内存
        _warn_once("disc", f"半径 {rp}px 超出超采样预算 ⇒ 该圆点退回 aliased 直绘。")
        pygame.draw.circle(surf, color, (int(round(center[0])), int(round(center[1]))), rp)
        return
    key = ("disc", rp, ss, tuple(color))
    sprite = _cache_get(key)
    if sprite is None:
        sprite = _supersample((d, d), lambda s, k: pygame.draw.circle(
            s, color, (d * k // 2, d * k // 2), rp * k), ss, fill=(color[0], color[1], color[2], 0))
        _cache_put(key, sprite)
    surf.blit(sprite, (int(round(center[0] - sprite.get_width() / 2.0)),
                       int(round(center[1] - sprite.get_height() / 2.0))))


def aa_ring(surf: pygame.Surface, color: ColorA, center: Sequence[float],
            radius: float, width: int = 1) -> None:
    """抗锯齿圆环（风圈）。同样按 `_pick_ss` 降 SS。"""
    r = max(1, int(round(float(radius))))
    wdt = max(1, int(width))
    d = (r + wdt) * 2 + 2
    ss = _pick_ss(d, d)
    if ss <= 0:
        _warn_once("ring", f"半径 {r}px 超出超采样预算 ⇒ 该圆环退回 aliased 直绘。")
        pygame.draw.circle(surf, color, (int(round(center[0])), int(round(center[1]))), r, wdt)
        return
    #: 环宽在超采样画布上等比放大，降采样后才不会细到消失
    key = ("ring", r, wdt, ss, tuple(color))
    sprite = _cache_get(key)
    if sprite is None:
        sprite = _supersample((d, d), lambda s, k: pygame.draw.circle(
            s, color, (d * k // 2, d * k // 2), r * k, wdt * k), ss, fill=(color[0], color[1], color[2], 0))
        _cache_put(key, sprite)
    surf.blit(sprite, (int(round(center[0] - sprite.get_width() / 2.0)),
                       int(round(center[1] - sprite.get_height() / 2.0))))


def aa_poly(surf: pygame.Surface, color: Color, points: Iterable[Sequence[float]]) -> None:
    """抗锯齿实心多边形（三角/方块/海洋区填充等）。points 为世界坐标。

    ⚠ **形状大时自适应降 SS**（2026-10-02 修，见 `_pick_ss` 的说明）：
    全屏级多边形原先在 SS=4 下要 134 MB 一张临时面。现在按面积把 SS 从 4 降到 3/2，
    **保 AA 而不是保 SS**；只有连 SS=2 都放不下（面积 > 4 M px）才退回 aliased 直绘。
    """
    global _poly_warned
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = int(min(xs)) - 2, int(min(ys)) - 2
    w = int(max(xs)) - x0 + 4
    h = int(max(ys)) - y0 + 4
    ss = _pick_ss(w, h)
    if ss <= 0:
        #: 超大多边形（bbox > 4 M px，如 4K 全屏）⇒ **分块**，不放弃 AA。
        _poly_tiled(surf, color, pts, w, h, x0, y0)
        return
    local = [(x - x0, y - y0) for x, y in pts]
    sprite = _supersample((w, h), lambda s, k: pygame.draw.polygon(
        s, color, [(x * k, y * k) for x, y in local]), ss, fill=(color[0], color[1], color[2], 0))
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
    """抗锯齿线段（任意宽度，含 1px）。

    ⚠ **1px 用 `pygame.draw.aaline`（它确实抗锯齿）**，2026-10-02 更正：
    app-sim 在 P1 曾报"`aaline` 在本机完全没有抗锯齿（中间值像素 = 0）"，**Lead 复测认为该结论错误**：

    | Surface  | `draw.line(w=1)` | `draw.aaline` |
    |---|---|---|
    | SRCALPHA | mid=0, **2 种颜色** | mid=240, **5 种颜色** ✅ |
    | 不透明    | mid=0, 2 种颜色 | mid=0, **5 种颜色** ✅ |

    ⇒ `aaline` 在两处**都抗锯齿**（颜色数 5 vs 2）。在**不透明**面上 `get_at()[3]` 恒为 255，
    于是"中间 alpha 像素数"必然是 0 —— **那是度量口径的陷阱，不是没有 AA**。
    （Lead 自己早先在 `prove_aa.py` 里踩过同一个坑。）
    **正确判据**：数**不同颜色数**，或只在 SRCALPHA 面上数 `0<a<255`。

    所以 1px 走 `aaline`（原生、廉价）；`>1px` 走超采样精灵 + 分块。
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
        (local[1][0] * ss, local[1][1] * ss), w * ss), fill=(color[0], color[1], color[2], 0))
    surf.blit(sprite, (x0, y0))
