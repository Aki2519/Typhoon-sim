# py/ty_sim_mixins/_draw_path_mixin.py
"""台风路径渲染 Mixin：安全连线、路径缓存、增量绘制。"""
from __future__ import annotations
import math
import os
import pygame
from collections import OrderedDict
from typing import Optional
from datetime import datetime
from ..utils import fmt_short_time
from ..constants import (
    PATH, CUR_POS,
    FUTURE_LINE_ALPHA, FADE_DURATION, FADE_DURATION_QUICK,
    f_s, rt,
    SUCAI_DIR, ICON_SET_SMCY,
)
from ..landfall_effect import landfall_marker_name
from ..spline import compute_arc_lengths
from ..track_quality import km_per_deg_lon
# P1(2026-10-02): 抗锯齿共享底座(超采样精灵缓存 + LRU 字节上限)。
# **只读使用**, 不要在本文件复制一份超采样逻辑。
from .. import paint_aa

# ── 登陆点标记 png（按尺寸缓存） ──

_FADE_MS = FADE_DURATION * 1000.0
_INV_FADE = 1.0 / FADE_DURATION
_INV_FADE_QUICK = 1.0 / FADE_DURATION_QUICK

_MARKER_DIR = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'landfall')
_marker_raw: dict = {}
_marker_scaled: dict = {}


def _get_landfall_marker(name: str, size: int):
    key = (name, size)
    surf = _marker_scaled.get(key)
    if surf is not None:
        return surf
    raw = _marker_raw.get(name)
    if raw is None and name not in _marker_raw:
        path = os.path.join(_MARKER_DIR, f'{name}.png')
        try:
            raw = pygame.image.load(path).convert_alpha() if os.path.exists(path) else None
        except Exception:
            raw = None
        _marker_raw[name] = raw
    if raw is None:
        return None
    surf = pygame.transform.smoothscale(raw, (size, size))
    if len(_marker_scaled) > 128:
        _marker_scaled.pop(next(iter(_marker_scaled)))
    _marker_scaled[key] = surf
    return surf


def preload_landfall_markers(marker_names: list, size: int) -> None:
    """预热登陆点标记 png：提前缩放并缓存到 _marker_scaled，避免路径绘制时卡顿。"""
    for name in marker_names:
        _get_landfall_marker(name, size)


class TySimDrawPathMixin:
    """台风路径绘制：点标记、缓存、增量更新。"""

    @property
    def _path_max_seg_len(self):
        return min(self.screen_width, self.map_height) // 2

    @staticmethod
    def _draw_dashed(surface, color, start, end, width):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1:
            return
        inv = 1.0 / length
        ux, uy = dx * inv, dy * inv
        pos = 0.0
        dash, gap = 6, 4
        segs = []
        while pos < length:
            seg_end = min(pos + dash, length)
            x1 = int(start[0] + ux * pos)
            y1 = int(start[1] + uy * pos)
            x2 = int(start[0] + ux * seg_end)
            y2 = int(start[1] + uy * seg_end)
            segs.append(((x1, y1), (x2, y2), color))
            pos = seg_end + gap
        # P1: 破折线段是**斜线** ⇒ 需要 AA; 一段 dash 只有 6px 长,
        # 走区域超采样的包围盒很小(≈(6+|dx|)×...), 代价可忽略。
        TySimDrawPathMixin._draw_segments_aa(surface, segs, width)

    #: ── P1: 折线的**区域超采样**上限 ─────────────────────────────────────
    #: 一次超采样要分配 (w*SS)×(h*SS) 的 RGBA 面。以 SS=2 计:
    #:   0.5 Mpx 包围盒 → 2.0 Mpx 临时面 =  8 MB
    #: ── P1: 折线的**分块**区域超采样 ─────────────────────────────────────
    #: 单块超采样面的上限(单位 = **SS 像素**, 即 (tile*ss)² )。4 M SS px @ss=2
    #: ⇒ 单块 ≤ 16 MB 临时面; 块边长 = isqrt(4M/4) = 1000 输出 px。
    #: ⚠ 2026-10-02 修(Lead 实测报缺): 原先这里是**整组一张面 + 超限就回落**,
    #:   等价于"包围盒 > 1 M px(≈1000×1000)就没有 AA" —— 而**横跨全屏的台风路径**
    #:   包围盒轻松 1912×1072 = 2.05 M px ⇒ 最常见的情形反而没 AA
    #:   (实测 mid_alpha = 0)。现在改为**分块**: 超限时按 tile 网格切块, 每块
    #:   各自超采样 → smoothscale → blit ⇒ **内存峰值恒 ≤ 单块**、AA 全覆盖,
    #:   代价与总面积成正比(与不分块等价)。块原点都是整数、ss 为整数 ⇒
    #:   输出像素的 SS 采样块完整落在同一块内 ⇒ **无接缝、无重复绘制**。
    _AA_MAX_SS_PIXELS = 4_000_000
    #: 超采样倍数。与 paint_aa.SS 一致(4)会把临时面放大 16 倍 ⇒ 这里取 2,
    #: 4 个样本/像素已能显著削锯齿(实测见报告 §验收)。
    _AA_SS = 2

    @classmethod
    def _draw_segments_aa(cls, surface, segs, width, max_ss_pixels=None):
        """把**一组同宽线段**做区域超采样后画出(真 AA), 内存有硬上界。

        为什么不用 `paint_aa.aa_line`: 后者按**单段包围盒**建精灵, 对
        "几百段、每段可能几百像素长"的台风路径会生成成百上千张临时面
        (O(段数) × O(段长²)) —— 正是任务里要报出来的那种爆炸。

        本实现: 整组线段算**一个**包围盒 → 按 `tile` 网格**分块** → 每块一张
        超采样面(≤ `max_ss_pixels` SS 像素) → 画进它 → `smoothscale` → blit。
        * 代价与"组包围盒面积"成正比, **与段数无关**(每块只画与该块相交的段);
        * **内存峰值 = 单块**(全屏 1912×1072 @cap 4 M 只切成 2×2=4 块,
          峰值 16 MB, 而不是整张 32.7 MB);
        * 小包围盒(≤ 1 M px)时只有 1 块 ⇒ 与"单张面"实现**逐像素一致**。

        segs: 可迭代的 `((x1,y1), (x2,y2), color)`; color 可逐段不同。
        max_ss_pixels: 覆盖默认单块上限(renderer 的每帧海洋区边界用更小的值,
        把峰值压到 ~1 MB)。
        """
        segs = list(segs)
        if not segs:
            return
        w = max(1, int(width))
        ss = cls._AA_SS
        cap = cls._AA_MAX_SS_PIXELS if max_ss_pixels is None else int(max_ss_pixels)
        cap = max(4096, cap)
        xs = []
        ys = []
        for (x1, y1), (x2, y2), _c in segs:
            xs.append(x1)
            xs.append(x2)
            ys.append(y1)
            ys.append(y2)
        pad = w + 2
        x0 = int(math.floor(min(xs))) - pad
        y0 = int(math.floor(min(ys))) - pad
        ww = int(math.ceil(max(xs))) - x0 + pad
        hh = int(math.ceil(max(ys))) - y0 + pad
        if ww <= 0 or hh <= 0:
            return
        # 单块输出边长: 保证 (tile*ss)² ≤ cap
        tile = max(32, int(math.isqrt(max(1, cap // (ss * ss)))))
        lw = w * ss
        half = max(1, lw // 2)
        for oy in range(y0, y0 + hh, tile):
            th = min(tile, y0 + hh - oy)
            for ox in range(x0, x0 + ww, tile):
                tw = min(tile, x0 + ww - ox)
                big = pygame.Surface((tw * ss, th * ss), pygame.SRCALPHA)
                for (x1, y1), (x2, y2), c in segs:
                    # 快速剔除: 该段(含线宽余量)与本块不相交就跳过(省 draw call)
                    if (max(x1, x2) < ox - w or min(x1, x2) > ox + tw + w
                            or max(y1, y2) < oy - w or min(y1, y2) > oy + th + w):
                        continue
                    a = (int((x1 - ox) * ss), int((y1 - oy) * ss))
                    b = (int((x2 - ox) * ss), int((y2 - oy) * ss))
                    pygame.draw.line(big, c, a, b, lw)
                    # 接头处补圆点: pygame.draw.lines 的相邻段在钝角处会有缺口,
                    # 逐段画时必须自己补(否则粗线拐点出现"刻痕")。
                    # ⚠ 只在 lw>=6(输出宽度>=3) 时补: 2px 线的缺口肉眼不可见,
                    #   而逐顶点补一圈会让二值 ink 变胖(实测 IoU 从 0.62 提到 0.93+)。
                    if lw >= 6:
                        pygame.draw.circle(big, c, b, half)
                surface.blit(pygame.transform.smoothscale(big, (tw, th)), (ox, oy))

    @staticmethod
    def _draw_lines_safe(surface, color, points, width, max_seg_len):
        """Draw connected lines, breaking at segments that exceed max_seg_len.
        防止两连续屏幕点落在地图投影折返两侧时产生横跨全屏的伪线。
        """
        if len(points) < 2:
            return
        max_seg_sq = max_seg_len * max_seg_len
        seg_start = 0

        def _emit(lo, hi):
            if hi - lo < 2:
                return
            TySimDrawPathMixin._draw_segments_aa(
                surface,
                [(points[i - 1], points[i], color) for i in range(lo + 1, hi)],
                width)

        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > max_seg_sq:
                _emit(seg_start, i)
                seg_start = i
        _emit(seg_start, len(points))

    @staticmethod
    def _draw_lines_colored(surface, points, point_colors, width, max_seg_len, point_types=None):
        """Draw colored line segments. point_types: list of storm type strings,
        EX/SS/SD segments are drawn dashed."""
        if len(points) < 2:
            return
        max_seg_sq = max_seg_len * max_seg_len
        segs = []                      # ((x1,y1),(x2,y2),color) —— 不连续处整组重画
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > max_seg_sq:
                # 跨折返: 先把已积累的整组超采样画掉(保持"一组一张面"的代价模型)
                TySimDrawPathMixin._draw_segments_aa(surface, segs, width)
                segs = []
                continue
            c = point_colors[i - 1]
            if point_types and point_types[i - 1] in ('EX', 'SS', 'SD'):
                # 虚线: 展开成小段, 与相邻实线一起进同一张超采样面(段短、成本小)
                dx2, dy2 = x2 - x1, y2 - y1
                length = math.sqrt(dx2 * dx2 + dy2 * dy2)
                if length >= 1:
                    inv = 1.0 / length
                    ux, uy = dx2 * inv, dy2 * inv
                    pos = 0.0
                    while pos < length:
                        seg_end = min(pos + 6, length)
                        segs.append(((int(x1 + ux * pos), int(y1 + uy * pos)),
                                     (int(x1 + ux * seg_end), int(y1 + uy * seg_end)), c))
                        pos = seg_end + 4
            else:
                segs.append(((x1, y1), (x2, y2), c))
        TySimDrawPathMixin._draw_segments_aa(surface, segs, width)

    _circle_cache: OrderedDict = OrderedDict()
    _MAX_CIRCLE_CACHE = 512

    _path_render_view_version = 0       # 视图版本号，视图变化时递增

    def _get_circle_marker(self, radius, color):
        """报点圆点精灵(缓存)。

        P1(2026-10-02): 原来用 `pygame.draw.circle` —— **pygame 基础图元无抗锯齿**
        (实测边缘中间 alpha 像素 = 0, 见 `output/exploration/p1_map_aa.md`)。
        改走共享底座 `paint_aa.aa_disc`(4× 超采样 + smoothscale + 字节上限 LRU)。
        精灵尺寸/中心**保持不变**(2r×2r, 中心 (r,r)) —— 调用方的 blit 偏移契约
        (`_make_point_marker` 返回 `radius, radius`; `_blit_highlight` 用
        `x - radius`) 因此完全不用改。
        """
        key = (radius, color)
        cache = self._circle_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        paint_aa.aa_disc(surf, color, (radius, radius), radius)
        cache[key] = surf
        if len(cache) > self._MAX_CIRCLE_CACHE:
            cache.popitem(last=False)
        return surf

    #: 唯一的**非 AA** 圆盘缓存(只给当前位置高亮的红盘用; 理由见 `_blit_highlight`)。
    #: 与 `_circle_cache` **分开存**, 否则同一个 (radius,color) 键会互相污染。
    _circle_cache_aliased: OrderedDict = OrderedDict()
    _MAX_CIRCLE_CACHE_ALIASED = 32

    def _get_circle_marker_aliased(self, radius, color):
        """无抗锯齿的圆盘精灵(仅 `_blit_highlight` 的红盘使用)。

        ⚠ 这是 P1 里**刻意保留**的一处 aliased 绘制: 它在画面上只剩 2px 细环,
        AA 收益亚像素级, 但会抹掉饱和 (220,60,60) 像素而弄坏
        `tests/test_pan_reuse.py` 的定位契约。详见 `_blit_highlight` 的 docstring。
        """
        key = (radius, color)
        cache = self._circle_cache_aliased
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, color, (radius, radius), radius)
        cache[key] = surf
        if len(cache) > self._MAX_CIRCLE_CACHE_ALIASED:
            cache.popitem(last=False)
        return surf

    # ── 非圆形标记缓存（矩形/三角形） ──
    _rect_marker_cache: OrderedDict = OrderedDict()
    _tri_marker_cache: OrderedDict = OrderedDict()
    _MAX_SHAPE_CACHE = 128

    def _get_rect_marker(self, size, color):
        key = (size, color)
        cache = self._rect_marker_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        surf.fill(color)
        cache[key] = surf
        if len(cache) > self._MAX_SHAPE_CACHE:
            cache.popitem(last=False)
        return surf

    def _get_tri_marker(self, tri_w, tri_h, color):
        """EX 三角标记精灵(缓存)。P1: 多边形同样无抗锯齿 ⇒ 改走 `paint_aa.aa_poly`。
        尺寸/顶点保持不变, 调用方的 `tri_w, tri_h` 偏移契约不变。"""
        key = (tri_w, tri_h, color)
        cache = self._tri_marker_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((tri_w * 2, tri_h * 2), pygame.SRCALPHA)
        tri = [(tri_w, 0), (0, tri_h * 2), (tri_w * 2, tri_h * 2)]
        paint_aa.aa_poly(surf, color, tri)
        cache[key] = surf
        if len(cache) > self._MAX_SHAPE_CACHE:
            cache.popitem(last=False)
        return surf

    # ── 台风筛选 ──
    def _size_factors(self):
        mv = self.map_mgr.map_view
        key = (self.point_size, self.icon_size, self.fix_icon_point_size,
               mv.scale if mv else None, mv.min_scale if mv else None)
        if getattr(self, '_size_factor_key', None) != key:
            prf = self.point_size / 100.0
            irf = self.icon_size / 100.0
            if self.fix_icon_point_size and mv and mv.min_scale > 0:
                k = mv.scale / (mv.min_scale * 2.5)
                prf *= k
                irf *= k
            self._size_factor_key = key
            self._size_factors_cached = (prf, irf)
        return self._size_factors_cached

    #: 高亮视觉参数: 提亮副本 = 原路径面 RGB 整体 +GAIN; 峰值时按 PEAK_ALPHA/255
    #: 叠在上面 ⇒ "整体提亮"(不是闪烁, 也不改线宽 —— 改线宽要重建面, 代价高)。
    _HL_GAIN = 130
    _HL_PEAK_ALPHA = 235

    def _blit_path_highlight(self, surface, ty, src, level: float, pos) -> None:
        """把路径**整体提亮**一层, 强度 = `level` ∈ (0,1](渐入/渐出用它做单次斜坡)。

        实现: 提亮副本**每次高亮只建一次**(缓存在本对象上, key=(面身份,尺寸)),
        之后每帧只是普通 alpha 混合 blit 一次 —— **没有每帧重建**。
        ⚠ 为什么不用 `BLEND_RGB_ADD`: 实测 pygame 的加法混合**不吃 `set_alpha`**
        (a=64/128/255 的增量完全相同), 无法做渐入渐出。
        ⚠ 透明背景像素 alpha=0, 加完 RGB 仍不可见 ⇒ 只提亮笔画本身。
        ⚠ 缓存放本对象(不是 typhoon/v, 两者都有 `__slots__`); 只在**当前高亮**用到
        的那 1-2 张面上保留, key 变了就整表替换 ⇒ 不会增长。
        """
        a = int(self._HL_PEAK_ALPHA * max(0.0, min(1.0, float(level))))
        if a <= 0:
            return
        key = (id(src), src.get_size())
        cache = getattr(self, '_hl_glow_cache', None)
        if cache is None:
            cache = {}
            self._hl_glow_cache = cache
        glow = cache.get(key)
        if glow is None:
            # 用**普通 Surface** 复制(不要 src.copy(): 若 src 是 Surface 子类,
            # copy() 会返回子类实例且不跑 __init__, 子类覆盖的方法可能炸)
            glow = pygame.Surface(src.get_size(), pygame.SRCALPHA)
            glow.blit(src, (0, 0))
            g = int(self._HL_GAIN)
            glow.fill((g, g, g, 0), special_flags=pygame.BLEND_RGB_ADD)
            cache.clear()
            cache[key] = glow
        glow.set_alpha(a)
        surface.blit(glow, pos)

    def _path_visible_deadline(self, ty) -> float:
        """该台风路径必须保持**可见(可绘制)**的截止时刻(ms)。

        正常情况 = `finish_time + _FADE_MS`(原来的口径)。
        收尾(高亮 2.4s + 淡出)期间必须延长到"高亮渐出完 + 淡完": alpha 是从高亮
        结束才起算淡出的, 而可见性窗口从 `finish_time` 起算 ⇒ fade 档会在 alpha≈17
        时被裁掉, quick 档更糟(1.5s 的窗口比高亮还短)。
        截止时间由 `TrackMixin._track_path_deadline` 提供(只有收尾台风 >0)。
        """
        dl = (float(ty.finish_time) + _FADE_MS) if ty.finish_time > 0 else 0.0
        fn = getattr(self, '_track_path_deadline', None)
        if fn is not None:
            try:
                dl = max(dl, float(fn(ty)))
            except Exception:
                pass
        return dl

    def should_draw_typhoon(self, ty) -> bool:
        if self.md == self.MODE_NORMAL:
            if ty == self.current_typhoon():
                return True
            if self.fade_path and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if ct < self._path_visible_deadline(ty):
                    return True
            return False
        elif self.md == self.MODE_SEASON:
            if ty.act and ty.ss and not ty.sf:
                return True
            if self.fade_path and ty.sf and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if ct < self._path_visible_deadline(ty):
                    return True
            return False
        elif self.md == self.MODE_EDIT:
            if ty == self.edit_typhoon:
                if self.fade_path and ty.finish_time > 0:
                    ct = pygame.time.get_ticks()
                    if ct - ty.finish_time < _FADE_MS:
                        return True
                return True
            return False
        return False

    def _is_typhoon_visible(self, ty) -> bool:
        """使用 bbox 快速判断台风路径是否与可视区域有交集。
        拖拽期间，将可见区域向反方向偏移以补偿路径的整体平移。"""
        bbox = getattr(ty, 'bbox', None)
        if bbox is None:
            return True  # 无 bbox 时保守绘制
        margin = 20
        map_rect = pygame.Rect(-margin, -margin,
                               self.screen_width + margin * 2,
                               self.map_height + margin * 2)
        # 屏幕坐标过期（惰性刷新）：用地理多点采样粗投影做裁剪，
        # 避免缩放后首帧屏幕外台风也全量重建（拖21）；U 形/环形路径两端同侧时
        # 首末点投影会误裁，采样全部点（最多 16 个）取 min/max（B11）
        if ty.v._sp_ver != getattr(self, '_sp_version', 0):
            if not ty.pts:
                return True
            fs = self.latlon_to_screen
            step = max(1, len(ty.pts) // 16)
            min_x = min_y = 1e18
            max_x = max_y = -1e18
            for p in ty.pts[::step]:
                x, y = fs(p['la'], p['lo'])
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
            min_x -= 400; max_x += 400
            min_y -= 400; max_y += 400
            return (min_x <= self.screen_width and max_x >= 0
                    and min_y <= self.map_height and max_y >= 0)
        # 拖拽时路径 blit 会加上 drag_offset：把可见矩形向反方向偏移做精确判定，
        # 不走 cpos 兜底（兜底会反复失效缓存，导致拖拽中每帧重建）
        if self._drag_offset_x or self._drag_offset_y:
            map_rect.x -= self._drag_offset_x
            map_rect.y -= self._drag_offset_y
            return map_rect.colliderect(bbox)
        if map_rect.colliderect(bbox):
            return True
        # bbox 可能因视图平移变得过时，用当前位置的地理坐标做二次确认
        pos = ty.cpos()
        if pos:
            x, y = self.latlon_to_screen(pos['la'], pos['lo'])
            if -margin <= x <= self.screen_width + margin and -margin <= y <= self.map_height + margin:
                # 使路径缓存失效，确保 draw_typhoon 用当前视图坐标重绘
                self._invalidate_path_cache_for_ty(ty)
                return True
        return False

    # ── 纯平移锚点复用 ──
    _PAN_REUSE_PAD = 48   # 路径面四周留白(px): 平移复用窗口内不露边
    _PAN_REUSE_MAX_SHIFT = 40   # 累计位移超过该值→重建(<= 留白, 无露边)

    def _view_anchor_tuple(self):
        """当前视图锚点(与 geo_to_screen 两段式取整同源)。"""
        mv = self.map_mgr.map_view if self.map_mgr is not None else None
        if mv is None:
            return None
        return (int(mv.view_x * mv.scale), int(mv.view_y * mv.scale),
                mv.scale, getattr(mv, '_bottom_align', False),
                self.screen_width, self.map_height)

    def _view_offset(self):
        """当前视图在地图空间中的原点; 无地图视图时 (0,0)。"""
        mv = self.map_mgr.map_view if self.map_mgr is not None else None
        if mv is None:
            return (0, 0)
        try:
            return mv.view_offset()
        except Exception:
            return (0, 0)

    def _map_space_points(self, ty, screen_points, voff):
        """地图空间报点: 优先用 update_screen_points 时算好的 v.map_points。"""
        mp = getattr(ty.v, 'map_points', None)
        if mp and len(mp) == len(screen_points):
            return [(int(x), int(y)) for x, y in mp]
        return [(x + voff[0], y + voff[1]) for x, y in screen_points]

    def _map_space_smooth(self, ty, voff):
        """地图空间平滑点(与报点同一坐标系)。"""
        if not self.smooth_path:
            return None
        mp = getattr(ty.v, 'smooth_map_points', None)
        if mp:
            return [(int(x), int(y)) for x, y in mp]
        return [(x + voff[0], y + voff[1]) for x, y in ty.v.smooth_screen_points]

    def _try_pan_reuse_path_cache(self, ty, screen_points, key) -> bool:
        """纯平移复用路径缓存面: 仅当(除首尾坐标外)缓存指纹一致且首尾点位移相同
        且累计位移在留白窗口内时, 直接把面按位移整体 blit, 免每帧重建。
        缩放/环绕缝/其它变化位移非统一 → 返回 False 走全量重建(现行为)。"""
        full = ty._path_cache_full
        trav = ty._path_cache_traversed
        if full is None or trav is None or not ty._path_cache_key:
            return False
        if getattr(ty, '_path_cache_partial', False):
            return False   # 裁窗面: 平移复用会露出屏幕外条带 → 每帧重建(无条带)
        if key[:3] + key[5:] != ty._path_cache_key[:3] + ty._path_cache_key[5:]:
            return False
        sp0 = getattr(ty, '_path_cache_sp0', None)
        spL = getattr(ty, '_path_cache_spL', None)
        origin = getattr(ty, '_path_cache_origin', None)
        if sp0 is None or spL is None or origin is None or len(screen_points) < 2:
            return False
        d0 = (screen_points[0][0] - sp0[0], screen_points[0][1] - sp0[1])
        dL = (screen_points[-1][0] - spL[0], screen_points[-1][1] - spL[1])
        if d0 != dL:
            return False   # 非均匀位移(缩放/环绕/其它) → 全量重建
        # 面内容固定在建面时坐标系; blit = 原bbox原点 + 当前总位移(绝对, 非累加)
        nb = (origin[0] + d0[0], origin[1] + d0[1])
        # 累计位移超过留白窗口 → 重建重置锚点, 避免屏幕外入境的条带露边
        if abs(d0[0]) > self._PAN_REUSE_MAX_SHIFT or abs(d0[1]) > self._PAN_REUSE_MAX_SHIFT:
            return False
        ty._path_cache_blit = nb
        ty._path_cache_key = key
        return True

    def _try_shift_screen_points(self, ty) -> bool:
        """纯平移时按锚点整页平移已有屏幕点(含平滑点/bbox), 返回是否成功。
        与 geo_to_screen 的两段式取整一致: 只有 scale/尺寸/底对齐不变时位移才是
        整数均匀的; 任何缩放/环绕翻面/尺寸变化都返回 False 走全量重算。"""
        v = ty.v
        anchor = getattr(v, '_sp_anchor', None)
        now = self._view_anchor_tuple()
        if anchor is None or now is None or anchor[2:] != now[2:]:
            return False
        # 屏幕坐标 = 基准 - 视图偏移: 视图前移 → 坐标减小
        dx = anchor[0] - now[0]
        dy = anchor[1] - now[1]
        # ⚠⚠ 用户 2026-10-01: "平滑路径有可能不会应用, 需要开关一次" —— **就是这里**。
        # 纯平移优化只"平移已有数组", 不重建样条。可如果**平滑数组本身是空的**
        # (而 smooth_path 是开的), 平移过后依然是空的 ⇒ 画面永远不平滑,
        # 且  被标成最新 ⇒ 后面也不会再判过期 ⇒ **卡死在"不平滑"状态**,
        # 只有把平滑开关来回拨一次(触发值变化 -> update_all_screen_points)才恢复。
        # 修法: 开关开着却没有平滑数据时**拒绝走平移捷径**, 落到下面全量重投影 + 重建样条。
        if getattr(self, 'smooth_path', False) and len(getattr(v, 'screen_points', ()) or ()) >= 2                 and not v.smooth_map_points and not v.smooth_screen_points:
            return False
        if dx == 0 and dy == 0:
            v._sp_ver = getattr(self, '_sp_version', 0)
            return True
        if abs(dx) > 3000 or abs(dy) > 3000:
            return False
        pts = v.screen_points
        if not pts:
            return False
        mv = self.map_mgr.map_view
        if mv is None:
            return False
        # 环绕重归一化: 防止跨 0°/360° 缝时点停在翻面错误的一侧
        # (阈值取半宽: 用整宽时跨缝点会停在翻面错误的一侧)
        wrap = mv.wrap_px()
        sw2 = self.screen_width / 2.0
        half = wrap / 2.0
        wrapped = False

        def _renorm(points):
            nonlocal wrapped
            out = []
            for x, y in points:
                nx, ny = x + dx, y + dy
                if nx > sw2 + half:
                    nx -= wrap
                    wrapped = True
                elif nx < sw2 - half:
                    nx += wrap
                    wrapped = True
                out.append((nx, ny))
            return out

        v.screen_points = _renorm(pts)
        v.smooth_screen_points = _renorm(v.smooth_screen_points)
        if v.bbox is not None:
            if wrapped:
                # 有顶点被翻面到另一侧: 旧 bbox 已失效, 必须按新点重建,
                # 否则可见性裁剪会漏掉翻面后的整段路径
                xs = [p[0] for p in v.screen_points] or [0.0]
                ys = [p[1] for p in v.screen_points] or [0.0]
                pad = 8
                v.bbox = pygame.Rect(int(min(xs)) - pad, int(min(ys)) - pad,
                                     int(max(xs) - min(xs)) + 2 * pad,
                                     int(max(ys) - min(ys)) + 2 * pad)
            else:
                v.bbox.move_ip(dx, dy)
        v._sp_anchor = now
        if wrapped:
            # 跨缝帧: 中点位于翻面另一侧, 平移复用不再安全 → 下一帧路径面重建
            ty._path_cache_key = ()
        return True

    def _normal_others_dim(self) -> Optional[float]:
        """正常模式其它台风的显示系数: 1.0=不透明, (0,1)=半透明; None=不显示。"""
        if self.md != self.MODE_NORMAL:
            return 1.0
        mode = getattr(self.cfg, 'normal_other_display', 'translucent')
        if mode == 'hidden':
            return None
        if mode == 'opaque':
            return 1.0
        return 0.45

    def _draw_typhoons(self, surface):
        current_ty = self.current_typhoon()
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            # 轮次 160(F5): 编辑模式原先**不过可见性门**, 屏幕外也调 draw_typhoon。
            # 而 `update_screen_points` 在 bbox 与 view_rect 不相交时是
            # 「**先清空 smooth_* 再早退**」(typhoon_render.py:93-95) ⇒ 样条恒空 ⇒
            # draw_typhoon 里那条"开关开着但样条为空"的过期判定每帧命中 ⇒
            # 每帧全量重投影(973 的平移捷径被同一条件否掉)。与风季循环(下面 425)
            # 对齐: 不可见就不画 —— 屏幕外本来也不该产生任何重投影成本。
            if self._is_typhoon_visible(self.edit_typhoon):
                self.draw_typhoon(surface, self.edit_typhoon, highlight=True)
        else:
            others_dim = self._normal_others_dim()
            for ty in self.tys:
                if self.should_draw_typhoon(ty):
                    if not self._is_typhoon_visible(ty):
                        continue
                    if ty is current_ty:
                        self.draw_typhoon(surface, ty, highlight=True)
                    elif others_dim is not None:
                        self.draw_typhoon(surface, ty, highlight=False, dim=others_dim)

    # ── 增量路径渲染缓存（per-typhoon）──

    def _make_path_cache_key(self, ty, screen_points, highlight):
        """生成路径缓存的键。首尾屏幕坐标作为视图指纹。"""
        sp_first = screen_points[0] if screen_points else (0, 0)
        sp_last = screen_points[-1] if screen_points else (0, 0)
        return (highlight, self.point_size,
                self._path_render_view_version,
                sp_first, sp_last, len(screen_points),
                len(ty.pts), id(ty.pts),
                getattr(self, 'show_future_path', True),
                getattr(self, 'smooth_path', False),      # R3: 平滑开关/模式进 key
                getattr(self, 'smooth_path_mode', 'monotone'),
                getattr(self, 'smooth_path_segments', 10),
                getattr(self, 'path_mode', 'markers'),   # N6: 路径模式进 key
                # K26: 固定点/图标大小模式进 key(影响标记 radius 与 size_factors)
                getattr(self, 'fix_icon_point_size', False),
                self.md == self.MODE_EDIT and highlight)

    def _show_future(self, highlight) -> bool:
        """是否绘制未经过的路径（编辑模式始终显示）。

        ⚠ 轮次 155: **模拟模式一律不画**。模拟台风的 `track` 是内核逐 6h 算出来的,
        "物理领先显示钟一步"的那几个点属于**内部超前**, 不是"未来预报" ——
        用户 2026-09-19 明确反馈过"为什么会渲染下一步的路径"。
        原先这件事是靠 `_sim_render` **每帧把 `ty.pts` 截断到 ci+1** 实现的, 代价是
        同时打断了四个共享函数的前置条件(活线段 / 类别过渡前半段 / 图标预载 /
        信息框起止时间), 还让路径缓存键(含 `id(ty.pts)`)每帧失效。
        改成在这里返回 False 之后, 截断可以整个删掉, 四个前置条件一起恢复。
        """
        # ⚠ 必须用 getattr 取 MODE_SIM: 测试/工具里的轻量桩(FakeSim)没有这个类属性,
        # 硬取会 AttributeError(实测 test_pan_reuse / test_seam_render 多条全挂)。
        # 仓库里其它同类判断也都写成 getattr(..., 'MODE_SIM', 'sim')。
        if getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim'):
            return False
        if self.md == self.MODE_EDIT and highlight:
            return True
        return getattr(self, 'show_future_path', True)

    def _line_mode(self) -> bool:
        """当前是否为渐变线模式（编辑模式强制点阵，不改动配置）。"""
        if self.md == self.MODE_EDIT:
            return False
        return getattr(self, 'path_mode', 'markers') == 'line'

    def _make_point_marker(self, point_color, cat, p, radius, point_radius_factor,
                           highlight, is_future):
        """创建一个点标记 Surface（圆/三角/矩形），返回 (surf, offset_x, offset_y)。"""
        if not p.get('official', True):
            size = max(2, int(2 * point_radius_factor))
            return self._get_rect_marker(size, point_color), size // 2, size // 2
        elif cat == "EX":
            tri_h = int(3 * point_radius_factor)
            tri_w = int(3 * point_radius_factor)
            return self._get_tri_marker(tri_w, tri_h, point_color), tri_w, tri_h
        else:
            if is_future and not (highlight and self.md == self.MODE_EDIT):
                if len(point_color) == 3:
                    alpha_pc = (*point_color, FUTURE_LINE_ALPHA)
                else:
                    alpha_pc = (*point_color[:3], min(point_color[3], FUTURE_LINE_ALPHA))
                return self._get_circle_marker(radius, alpha_pc), radius, radius
            else:
                return self._get_circle_marker(radius, point_color), radius, radius

    @staticmethod
    def _build_line_colors(pts, draw_points, dim, segs):
        """为 draw_points 构建逐段颜色列表（每段用终点原始点颜色）。返回 (colors, types)."""
        n_orig = len(pts)
        colors, types = [], []
        for idx in range(1, len(draw_points)):
            orig_idx = min((idx + segs - 1) // segs, n_orig - 1)
            colors.append(pts[orig_idx]['color_dim' if dim else 'color'])
            types.append(pts[orig_idx].get('st', ''))
        return colors, types

    def _render_path_to_surface(self, ty, screen_points, highlight):
        """增量渲染：cached_full（半透明全路径）+ cached_traversed（不透明已走段）。
        使用 bbox 尺寸 Surface，避免全屏分配。"""
        point_radius_factor = self._size_factors()[0]
        base_radius = 3
        radius = int(base_radius * point_radius_factor)
        if radius < 1:
            radius = 1
        max_seg = self._path_max_seg_len
        n_pts = len(ty.pts)
        cur_idx = ty.ci
        if not screen_points:
            # R4: 与文件内其它返回同契约——统一 3 元组 (full, trav, blit_pos)，
            # 避免 draw_typhoon 在极端情况下落入 2 元组单面 else 分支。
            return pygame.Surface((1, 1), pygame.SRCALPHA), \
                pygame.Surface((1, 1), pygame.SRCALPHA), (0, 0)

        # ── 拖拽中：渲染到独立 bbox Surface（不受屏幕裁剪）。
        #    必须在下方"裁剪到屏幕"的 bbox 计算之前处理，
        #    否则拖拽前在屏幕外的路径会因 bbox_w/h <= 0 提前返回而不渲染 ──
        if self._drag_offset_x or self._drag_offset_y:
            return self._render_drag_path_surface(
                ty, screen_points, highlight, radius, point_radius_factor,
                max_seg, cur_idx, n_pts)

        # ── 切到"地图空间"(视图无关): 平移只改 blit 位置, 缓存面不重建 ──
        voff = self._view_offset()
        screen_points = self._map_space_points(ty, screen_points, voff)
        smooth_map = self._map_space_smooth(ty, voff)
        key = self._make_path_cache_key(ty, screen_points, highlight)

        # ── bbox/裁窗判定(每帧都要, 与是否重建无关) ──
        # 路径包围盒完整建面(不裁窗): 平移永无「屏幕外入境条带」; 只有面积超大
        # (超出留白屏幕面积 1.25 倍)才裁到"视口 + pad"的窗口, 此时窗口依赖视图,
        # 键里并入量化后的视图偏移: 视图静止仍命中, 移动超过一个量化步才重建
        pad = self._PAN_REUSE_PAD
        margin = radius + 4
        xs = [p[0] for p in screen_points]
        ys = [p[1] for p in screen_points]
        if self.smooth_path and len(smooth_map) >= 2:
            xs = xs + [p[0] for p in smooth_map]
            ys = ys + [p[1] for p in smooth_map]
        path_w = (max(xs) - min(xs)) + margin * 2
        path_h = (max(ys) - min(ys)) + margin * 2
        fit_area = (self.screen_width + 2 * pad) * (self.map_height + 2 * pad) * 1.25
        huge = not (path_w > 2 and path_h > 2 and path_w * path_h <= fit_area)
        if huge:
            key = key + ((voff[0] // 32, voff[1] // 32),)

        # ── 缓存失效：重建 full + traversed ──
        if ty._path_cache_key != key:
            if not huge:
                bbox_x = min(xs) - margin
                bbox_y = min(ys) - margin
                bbox_w = path_w
                bbox_h = path_h
                ty._path_cache_partial = False
            else:
                bbox_x = max(voff[0] - pad, min(xs) - margin)
                bbox_y = max(voff[1] - pad, min(ys) - margin)
                bbox_w = min(voff[0] + self.screen_width + pad - bbox_x, path_w)
                bbox_h = min(voff[1] + self.map_height + pad - bbox_y, path_h)
                ty._path_cache_partial = True
            if bbox_w <= 0 or bbox_h <= 0:
                # K29: 记录空缓存键,避免屏幕外台风每帧重建 bbox。
                # 同时建立空 full/traversed 面,保证后续帧缓存命中走 3 元组返回,
                # 不落入 (None, None) 导致 draw_typhoon 里 set_alpha 崩溃。
                ty._path_cache_key = key
                ty._last_rendered_ci = cur_idx
                ty._path_cache_blit = (0, 0)
                ty._path_cache_origin = (0, 0)
                ty._path_cache_full = pygame.Surface((1, 1), pygame.SRCALPHA)
                ty._path_cache_traversed = pygame.Surface((1, 1), pygame.SRCALPHA)
                return ty._path_cache_full, ty._path_cache_traversed, (0, 0)

            line_mode = self._line_mode()
            line_width = 4 if line_mode else 2
            segs = max(1, self.smooth_path_segments)
            show_future = self._show_future(highlight)
            # full: 完整路径（半透明）
            ty._path_cache_full = pygame.Surface((bbox_w, bbox_h), pygame.SRCALPHA)
            full = ty._path_cache_full

            smooth_pts = (smooth_map if self.smooth_path else None)
            draw_points = smooth_pts if smooth_pts else screen_points
            rel_points = [(px - bbox_x, py - bbox_y) for px, py in draw_points]

            if show_future:
                if line_mode:
                    colors, types = self._build_line_colors(ty.pts, draw_points, dim=True, segs=segs if smooth_pts else 1)
                    self._draw_lines_colored(full, rel_points, colors, line_width, max_seg, point_types=types)
                else:
                    line_color = (*PATH, FUTURE_LINE_ALPHA)
                    if highlight and self.md == self.MODE_EDIT:
                        line_color = (*PATH, 255)
                    self._draw_lines_safe(full, line_color, rel_points, 2, max_seg)

                if not line_mode:
                    for i, (p, (x, y)) in enumerate(zip(ty.pts, screen_points)):
                        point_color = p['color'] if highlight else p['color_dim']
                        cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                        marker, offset_x, offset_y = self._make_point_marker(
                            point_color, cat, p, radius, point_radius_factor,
                            highlight, is_future=True)
                        full.blit(marker, (x - bbox_x - offset_x, y - bbox_y - offset_y))

            # traversed: 从头构建已走段（不透明）
            ty._path_cache_traversed = pygame.Surface((bbox_w, bbox_h), pygame.SRCALPHA)
            traversed = ty._path_cache_traversed

            smooth_sp = (smooth_map if self.smooth_path else None)
            if cur_idx > 0:
                if smooth_sp:
                    end_idx = min(cur_idx * segs, len(smooth_sp) - 1)
                    passed_line = smooth_sp[:end_idx + 1]
                else:
                    passed_line = screen_points[:cur_idx + 1] if len(screen_points) > 1 else screen_points
                rel_line = [(px - bbox_x, py - bbox_y) for px, py in passed_line]
                if line_mode:
                    colors, types = self._build_line_colors(ty.pts[:cur_idx + 1], passed_line, dim=False, segs=segs if smooth_sp else 1)
                    self._draw_lines_colored(traversed, rel_line, colors, line_width, max_seg, point_types=types)
                else:
                    self._draw_lines_safe(traversed, PATH, rel_line, 2, max_seg)

            if not line_mode:
                for i in range(cur_idx):
                    p = ty.pts[i]
                    x, y = screen_points[i]
                    point_color = p['color'] if highlight else p['color_dim']
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    marker, offset_x, offset_y = self._make_point_marker(
                        point_color, cat, p, radius, point_radius_factor,
                        highlight, is_future=False)
                    traversed.blit(marker, (x - bbox_x - offset_x, y - bbox_y - offset_y))

            ty._last_rendered_ci = cur_idx
            ty._path_cache_key = key
            ty._path_cache_blit = (bbox_x, bbox_y)
            ty._path_cache_origin = (bbox_x, bbox_y)



        # ── 增量追加：ci 前进时在 traversed 上追加新线段 ──
        elif cur_idx > ty._last_rendered_ci:
            bbox_x, bbox_y = ty._path_cache_blit
            line_mode = self._line_mode()
            line_width = 4 if line_mode else 2
            segs = max(1, self.smooth_path_segments)
            traversed = ty._path_cache_traversed
            smooth_sp = (smooth_map if self.smooth_path else None)
            if ty._last_rendered_ci >= 0:
                if smooth_sp:
                    i0 = ty._last_rendered_ci * segs
                    i1 = min(cur_idx * segs, len(smooth_sp) - 1)
                    seg = smooth_sp[i0:i1 + 1]
                else:
                    seg = screen_points[ty._last_rendered_ci:cur_idx + 1]
                rel_seg = [(px - bbox_x, py - bbox_y) for px, py in seg]
                if len(rel_seg) > 1:
                    if line_mode:
                        colors, types = self._build_line_colors(ty.pts[ty._last_rendered_ci:cur_idx + 1], seg, dim=False, segs=segs if smooth_sp else 1)
                        self._draw_lines_colored(traversed, rel_seg, colors, line_width, max_seg, point_types=types)
                    else:
                        self._draw_lines_safe(traversed, PATH, rel_seg, 2, max_seg)
            elif cur_idx > 0:
                if smooth_sp:
                    i1 = min(cur_idx * segs, len(smooth_sp) - 1)
                    passed = smooth_sp[:i1 + 1]
                else:
                    passed = screen_points[:cur_idx + 1]
                rel_pass = [(px - bbox_x, py - bbox_y) for px, py in passed]
                if len(rel_pass) > 1:
                    if line_mode:
                        colors, types = self._build_line_colors(ty.pts[:cur_idx + 1], passed, dim=False, segs=segs if smooth_sp else 1)
                        self._draw_lines_colored(traversed, rel_pass, colors, line_width, max_seg, point_types=types)
                    else:
                        self._draw_lines_safe(traversed, PATH, rel_pass, 2, max_seg)

            if not line_mode:
                for i in range(max(0, ty._last_rendered_ci), cur_idx):
                    p = ty.pts[i]
                    x, y = screen_points[i]
                    point_color = p['color'] if highlight else p['color_dim']
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    marker, offset_x, offset_y = self._make_point_marker(
                        point_color, cat, p, radius, point_radius_factor,
                        highlight, is_future=False)
                    traversed.blit(marker, (x - bbox_x - offset_x, y - bbox_y - offset_y))

            ty._last_rendered_ci = cur_idx


        # ── ci 回退：重建 traversed ──
        elif cur_idx < ty._last_rendered_ci:
            bbox_x, bbox_y = ty._path_cache_blit
            line_mode = self._line_mode()
            line_width = 4 if line_mode else 2
            segs = max(1, self.smooth_path_segments)
            traversed = ty._path_cache_traversed
            traversed.fill((0, 0, 0, 0))
            smooth_sp = (smooth_map if self.smooth_path else None)
            if cur_idx > 0:
                if smooth_sp:
                    end_idx = min(cur_idx * segs, len(smooth_sp) - 1)
                    passed = smooth_sp[:end_idx + 1]
                else:
                    passed = screen_points[:cur_idx + 1] if len(screen_points) > 1 else screen_points
                rel_pass = [(px - bbox_x, py - bbox_y) for px, py in passed]
                if line_mode:
                    colors, types = self._build_line_colors(ty.pts[:cur_idx + 1], passed, dim=False, segs=segs if smooth_sp else 1)
                    self._draw_lines_colored(traversed, rel_pass, colors, line_width, max_seg, point_types=types)
                else:
                    self._draw_lines_safe(traversed, PATH, rel_pass, 2, max_seg)
            if not line_mode:
                for i in range(cur_idx):
                    p = ty.pts[i]
                    x, y = screen_points[i]
                    point_color = p['color'] if highlight else p['color_dim']
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    marker, offset_x, offset_y = self._make_point_marker(
                        point_color, cat, p, radius, point_radius_factor,
                        highlight, is_future=False)
                    traversed.blit(marker, (x - bbox_x - offset_x, y - bbox_y - offset_y))
            ty._last_rendered_ci = cur_idx

        bx, by = getattr(ty, '_path_cache_blit', (0, 0))
        return (ty._path_cache_full, ty._path_cache_traversed,
                (bx - voff[0], by - voff[1]))

    _DRAG_SURF_MAX = 8192   # 拖拽 bbox Surface 单边上限，超出则裁剪到可视区附近

    def _clear_drag_cache(self, ty):
        """清空单台风全部拖拽面缓存（future/traversed/几何）。"""
        ty._path_cache_drag_surf = None
        ty._path_cache_drag_key = ()
        ty._path_cache_drag_future = None
        ty._path_cache_drag_trav = None
        ty._path_cache_drag_trav_ci = -1
        ty._path_cache_drag_trav_key = ()
        ty._path_cache_drag_geom = None

    def _render_drag_path_surface(self, ty, screen_points, highlight,
                                  radius, point_radius_factor, max_seg,
                                  cur_idx, n_pts):
        """拖拽期间的整路径渲染（旧视图坐标系，blit 时统一加 drag_offset）。
        future 层（未来路径+暗标记，与播放无关）与 traversed 层（已走段+实标记）
        分离：ci 前进只增量追加 traversed，避免整条路径面重建。"""
        ox, oy = self._drag_offset_x, self._drag_offset_y
        segs = max(1, self.smooth_path_segments)
        smooth_sp = (ty.v.smooth_screen_points
                     if self.smooth_path and len(ty.v.smooth_screen_points) >= 2
                     else None)
        if self.smooth_path and smooth_sp is None:
            # 拖动中平滑点缺失 → 用缓存的 geo 样条即时重建（保留在旧视图坐标系）
            mode = getattr(self.cfg, 'smooth_path_mode', 'monotone')
            from ..typhoon_render import _get_geo_spline
            smooth_geo = _get_geo_spline(ty, segs, mode)
            f = self.latlon_to_screen
            smooth_sc = [(f(lat, lon)[0] - ox, f(lat, lon)[1] - oy) for lon, lat in smooth_geo]
            ty.v.smooth_screen_points = smooth_sc
            ty.v._smooth_arc_lengths = compute_arc_lengths(smooth_sc)
            smooth_sp = smooth_sc
        line_pts = smooth_sp if smooth_sp else screen_points
        line_segs = segs if smooth_sp else 1

        # bbox 几何只在首次构建时计算（拖5）
        geom = getattr(ty, '_path_cache_drag_geom', None)
        if geom is None:
            xs = [p[0] for p in screen_points]
            ys = [p[1] for p in screen_points]
            if line_pts is not screen_points:
                xs = xs + [p[0] for p in line_pts]
                ys = ys + [p[1] for p in line_pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            geom = (min_x, max_x, min_y, max_y,
                    max(4, max_x - min_x + 60), max(4, max_y - min_y + 60))
            ty._path_cache_drag_geom = geom
        min_x, max_x, min_y, max_y, box_w, box_h = geom

        need_clip = box_w > self._DRAG_SURF_MAX or box_h > self._DRAG_SURF_MAX
        drag_extra = (self.point_size, segs,
                      getattr(self, 'path_mode', 'markers'),
                      getattr(self, 'show_future_path', True),
                      # R3: 平滑开关/模式/段数影响 future 层的 line_pts 与线型
                      getattr(self, 'smooth_path', False),
                      getattr(self, 'smooth_path_mode', 'monotone'))
        bucket = (ox // 512, oy // 512) if need_clip else (0, 0)
        future_key = (highlight, n_pts, bucket, ty.v._sp_ver,
                      getattr(self, '_path_render_view_version', 0), *drag_extra)
        trav_key = (cur_idx,)

        if (ty._path_cache_drag_key == future_key
                and ty._path_cache_drag_trav_key == trav_key
                and ty._path_cache_drag_future is not None):
            return (ty._path_cache_drag_future, ty._path_cache_drag_trav,
                    ty._path_cache_drag_pos)

        if need_clip:
            vis_pad = 600
            vis = pygame.Rect(-ox - vis_pad, -oy - vis_pad,
                              self.screen_width + vis_pad * 2,
                              self.map_height + vis_pad * 2)
            clipped = pygame.Rect(min_x - 30, min_y - 30, box_w, box_h).clip(vis)
            if clipped.width <= 0 or clipped.height <= 0:
                # 拖拽路径完全被裁剪掉：返回一致的 3 元组 (future, traversed, pos)，
                # 与下方所有返回路径同契约，避免 draw_typhoon 落入 2 元组单面 else 分支。
                surf = pygame.Surface((1, 1), pygame.SRCALPHA)
                ty._path_cache_drag_future = surf
                ty._path_cache_drag_trav = surf
                ty._path_cache_drag_key = future_key
                ty._path_cache_drag_trav_key = trav_key
                ty._path_cache_drag_pos = (0, 0)
                return surf, surf, (0, 0)
            origin_x, origin_y = clipped.x, clipped.y
            box_w, box_h = clipped.width, clipped.height
        else:
            origin_x, origin_y = min_x - 30, min_y - 30

        line_mode = self._line_mode()
        line_width = 4 if line_mode else 2
        show_future = self._show_future(highlight)
        f_alpha_for_full = FUTURE_LINE_ALPHA
        if highlight and self.md == self.MODE_EDIT:
            f_alpha_for_full = 255

        def _local(pt):
            return (pt[0] - origin_x, pt[1] - origin_y)

        # ── future 层（与 cur_idx 无关）──
        if ty._path_cache_drag_key != future_key or ty._path_cache_drag_future is None:
            future = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            local_line = [_local(p) for p in line_pts]
            local_marks = [_local(p) for p in screen_points]
            if show_future:
                if line_mode:
                    colors, types = self._build_line_colors(ty.pts, line_pts, dim=True, segs=line_segs)
                    self._draw_lines_colored(future, local_line, colors, line_width, max_seg, point_types=types)
                else:
                    self._draw_lines_safe(future, (*PATH, f_alpha_for_full), local_line, 2, max_seg)
            if not line_mode and show_future:
                for i, (p, lpt) in enumerate(zip(ty.pts, local_marks)):
                    lx, ly = lpt
                    point_color = p['color'] if highlight else p['color_dim']
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    marker, offset_x, offset_y = self._make_point_marker(
                        point_color, cat, p, radius, point_radius_factor,
                        highlight, is_future=True)
                    future.blit(marker, (lx - offset_x, ly - offset_y))
            ty._path_cache_drag_future = future
            ty._path_cache_drag_local_line = local_line
            ty._path_cache_drag_local_marks = local_marks
            ty._path_cache_drag_key = future_key
            # 坐标空间(origin)可能变化 → traversed 整体重建
            ty._path_cache_drag_trav = None
            ty._path_cache_drag_trav_ci = -1

        # ── traversed 层（ci 前进只增量追加）──
        if ty._path_cache_drag_trav_key != trav_key or ty._path_cache_drag_trav is None:
            trav = ty._path_cache_drag_trav
            if trav is None:
                trav = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            last_ci = ty._path_cache_drag_trav_ci
            if cur_idx < last_ci:
                # ci 回退：清空重画
                trav.fill((0, 0, 0, 0))
                last_ci = -1
            local_line = ty._path_cache_drag_local_line
            local_marks = ty._path_cache_drag_local_marks
            for ci in range(last_ci + 1, cur_idx + 1):
                if ci > 0 and len(local_line) > 1:
                    if smooth_sp:
                        i0 = max(0, (ci - 1) * line_segs)
                        i1 = min(ci * line_segs, len(local_line) - 1)
                        seg = local_line[i0:i1 + 1]
                    else:
                        seg = local_line[max(0, ci - 1):ci + 1]
                    if len(seg) > 1:
                        if line_mode:
                            colors, types = self._build_line_colors(
                                ty.pts[max(0, ci - 1):ci + 1], seg, dim=False,
                                segs=line_segs if smooth_sp else 1)
                            self._draw_lines_colored(trav, seg, colors, line_width,
                                                     max_seg, point_types=types)
                        else:
                            self._draw_lines_safe(trav, PATH, seg, 2, max_seg)
                if not line_mode and ci < len(local_marks):
                    p = ty.pts[ci]
                    lx, ly = local_marks[ci]
                    point_color = p['color'] if highlight else p['color_dim']
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    marker, offset_x, offset_y = self._make_point_marker(
                        point_color, cat, p, radius, point_radius_factor,
                        highlight, is_future=False)
                    trav.blit(marker, (lx - offset_x, ly - offset_y))
            ty._path_cache_drag_trav = trav
            ty._path_cache_drag_trav_ci = cur_idx
            ty._path_cache_drag_trav_key = trav_key

        ty._path_cache_drag_pos = (origin_x, origin_y)
        return ty._path_cache_drag_future, ty._path_cache_drag_trav, (origin_x, origin_y)

    def _blit_highlight(self, surface, x, y, radius, highlight_r, point_color, off_x, off_y):
        """当前位置高亮: 红盘(CUR_POS) + 中心点色盘。

        ⚠ P1 审计结论 —— **外圈红盘刻意保持无 AA**(本任务唯一的"有证据地不改"):
          · 可见部分只有 `[radius, highlight_r]` 那 **2px 细环**, 而且其后
            `_draw_edit_selection` 还会在白/黄环位置上再压一层; 对 2px 环做 AA 的
            收益是亚像素级的。
          · 一旦 AA, 环内**不再有饱和 (220,60,60) 像素**(实测: 旧 4 个 -> 新 0 个,
            见 `G:/tmp/p1_diag.py` 输出), 于是
            `tests/test_pan_reuse.py::TestEditSelectionHighlight::test_highlight_follows_selection`
            直接失败 —— 它靠"画面上存不存在 (220,60,60)±容差 的像素"来定位选中点。
            那是**可观察的产品行为契约**, 改它要动 `tests/`(不在本任务写作用域)。
          · 中心点色盘仍是 AA(`_get_circle_marker`), 所以"定点边缘变平滑"这个用户
            诉求不受影响。
        替代方案(留待 Lead 裁决): 红环也 AA, 同时把那条测试改成按 alpha>0 统计环位置。
        """
        # 内盘同样保持无 AA: 它 AA 之后软边会压在红环上, 把 (220,60,60) 混成
        # (142,62,62)(实测), 仍会弄坏同一条测试。两盘一起保持旧行为 ⇒ 这一处
        # 完全等价于改动前; **报点圆点标记**(`_make_point_marker` → `_get_circle_marker`)
        # 仍是 AA 的, 所以"定点边缘像素化"的用户诉求不受影响。
        outer = self._get_circle_marker_aliased(highlight_r, CUR_POS)
        inner = self._get_circle_marker_aliased(radius, point_color)
        surface.blit(outer, (x - highlight_r + off_x, y - highlight_r + off_y))
        surface.blit(inner, (x - radius + off_x, y - radius + off_y))

    def _invalidate_path_cache_for_ty(self, ty):
        """使指定台风的增量路径缓存失效。"""
        ty._path_cache_full = None
        ty._path_cache_traversed = None
        ty._last_rendered_ci = -1
        ty._path_cache_key = ()
        self._clear_drag_cache(ty)
        from ..typhoon_render import _clear_geo_spline_cache
        _clear_geo_spline_cache(ty)
        ty._cached_landfalls = None

    def _invalidate_all_path_caches(self):
        self._path_render_view_version += 1

    # ── 路径绘制（使用缓存） ──
    def draw_typhoon(self, surface, ty, highlight, dim: float = 1.0):
        """绘制台风路径。dim: 整体透明度系数(1.0=不透明, <1=半透明, 用于正常模式其它台风)。"""
        if not ty.pts:
            return

        screen_points = getattr(ty, 'screen_points', None)
        sp_mismatch = not screen_points or len(screen_points) != len(ty.pts)
        # ⚠⚠ 用户 2026-10-01: "平滑路径有可能不会应用在路径上, 需要开关一次后才确保应用"。
        # 根因: 平滑样条可能在 smooth_path 为 True 时**依然是空的** ——
        #    有一条早退分支(view_rect 与 bbox 不相交时),
        #   它**先清空 smooth_* 就 return**; 而  只在
        #   "smooth_path 的值**发生变化**"时才调 。
        #   => 出现"开关是开的、样条是空的"状态, 画面就是不平滑;
        #     只有把开关来回拨一次(值发生变化)才重建。
        # 修法(治本): 把"开关开着但样条/地图点为空"也算过期 => 下一帧自动重建。
        #   早退分支与任何"样条丢了"的路径都一并修好, 不必在设置那边补条件。
        # ⚠ 独立验证员建议: 守卫原本要求**两个数组都空**, 那"只有一个空"的部分状态
        #   就不会被重建(它用产品自身路径造不出该状态 —— update_screen_points 开头
        #   总把两者一起清 —— 所以这是合成状态, 不构成现网 bug)。但只要有别的路径
        #   只清其中一个, 旧写法就会漏。放宽成"**任一为空**"更稳, 代价只是多判一次。
        if (getattr(self, 'smooth_path', False) and len(ty.pts) >= 2
                and (not getattr(ty.v, 'smooth_map_points', None)
                     or not getattr(ty.v, 'smooth_screen_points', None))):
            sp_stale = True
        else:
            sp_stale = (sp_mismatch
                        or ty.v._sp_ver != getattr(self, '_sp_version', 0))
        if sp_stale:
            if self.right_button_dragging:
                # 拖拽中任何坐标过期（拖拽偏移/缩放/重投影）：当前视图下重算
                # （减拖拽偏移保持旧坐标系），并清该台风拖拽面，下一帧按新坐标重建
                ox, oy = self._drag_offset_x, self._drag_offset_y
                f = self.latlon_to_screen

                def _stale_equiv(la, lo):
                    x, y = f(la, lo)
                    return x - ox, y - oy

                # 缩放/拖动期间同样重建平滑样条,保证路径始终平滑不错位
                ty.update_screen_points(_stale_equiv, None)
                ty.v._sp_anchor = None   # 拖拽坐标是旧视图系, 锚点失效
                ty.v._sp_ver = getattr(self, '_sp_version', 0)
                self._clear_drag_cache(ty)
                screen_points = ty.screen_points
                if not screen_points:
                    return
            elif self._try_shift_screen_points(ty):
                # 纯平移: 屏幕点(含平滑点/bbox)按锚点整页平移, 免重投影+免样条重建
                screen_points = ty.screen_points
                ty.v._sp_ver = getattr(self, '_sp_version', 0)
                if not screen_points:
                    return
            else:
                # 惰性刷新：只对进入绘制的台风重算屏幕坐标；
                # 缩放突发期内同样重建平滑样条,避免缩放时路径退化为折线
                smooth_rect = pygame.Rect(-50, -50, self.screen_width + 100,
                                          self.map_height + 100)
                ty.update_screen_points(self.latlon_to_screen, smooth_rect,
                                        anchor=self._view_anchor_tuple())
                ty.v._sp_ver = getattr(self, '_sp_version', 0)
                screen_points = ty.screen_points
                if not screen_points:
                    return

        path_alpha = 255
        hl_level = 0.0
        mode = getattr(self.cfg, 'fade_path_mode', 'fade')
        if mode != 'never' and ty.finish_time > 0:
            ct = pygame.time.get_ticks()
            _is_hl = getattr(self, '_track_is_highlight', None)
            if _is_hl is not None and _is_hl(ty):
                # ── 收尾时序由 TrackMixin 的状态机给出(用户 2026-10-01 精确规格) ──
                #   高亮 = 0.2s 渐入 → 2.0s 保持 → 0.2s 渐出 (**单次, 不是闪烁**);
                #   高亮期间路径满不透明, 高亮**渐出完**才开始按 cfg 淡出。
                path_alpha = int(self._track_finale_path_alpha(ct))
                hl_level = float(self._track_highlight_level(ct))
            else:
                # 非收尾台风: 老口径(从 finish_time 起算 + 闪/淡出)保持原样
                hl_ms = int(getattr(self, '_track_hl_s', 2.0) * 1000)
                fade_start = ty.finish_time
                hl_active = False
                _hl_set = getattr(self, '_track_hl_tys', None) or ()
                _hl_this = (ty is getattr(self, '_track_hl_ty', None)
                            or any(t is ty for t in _hl_set))
                if _hl_this:
                    hl_from = getattr(self, '_track_hl_from', 0)
                    if hl_from:
                        fade_start = max(fade_start, hl_from + hl_ms)
                        if ct < hl_from + hl_ms:
                            hl_active = True
                elapsed = (ct - fade_start) * 0.001
                dur = FADE_DURATION_QUICK if mode == 'quick' else FADE_DURATION
                if hl_active:
                    path_alpha = 255 if (ct - hl_from) % 600 < 300 else 90
                elif elapsed < 0:
                    path_alpha = 255
                elif elapsed >= dur:
                    path_alpha = 0
                else:
                    path_alpha = max(0, int(255 * (1.0 - elapsed * (
                        _INV_FADE_QUICK if mode == 'quick' else _INV_FADE))))

        if dim < 1.0:
            path_alpha = max(0, int(path_alpha * dim))
        if path_alpha <= 0 and mode != 'never' and hl_level <= 0.0:
            return

        # ── 从缓存获取或创建路径 Surface ──
        result = self._render_path_to_surface(
            ty, screen_points, highlight)
        if isinstance(result, tuple) and len(result) == 3:
            full_surf, trav_surf, (blit_x, blit_y) = result
            full_surf.set_alpha(path_alpha)
            trav_surf.set_alpha(path_alpha)
            surface.blit(full_surf, (blit_x + self._drag_offset_x, blit_y + self._drag_offset_y))
            surface.blit(trav_surf, (blit_x + self._drag_offset_x, blit_y + self._drag_offset_y))
            if hl_level > 0.0:
                # 高亮: 同一位置的"提亮副本", 按 level 渐入/渐出(单次峰值)
                for _s in (full_surf, trav_surf):
                    self._blit_path_highlight(surface, ty, _s, hl_level,
                                              (blit_x + self._drag_offset_x,
                                               blit_y + self._drag_offset_y))
        else:
            path_surf, path_blit = result
            path_surf.set_alpha(path_alpha)
            surface.blit(path_surf, (path_blit[0] + self._drag_offset_x, path_blit[1] + self._drag_offset_y))
            if hl_level > 0.0:
                self._blit_path_highlight(surface, ty, path_surf, hl_level,
                                          (path_blit[0] + self._drag_offset_x,
                                           path_blit[1] + self._drag_offset_y))

        # ── 实时位置（live segment + highlight dot 共用）──
        # ⚠ 2026-10-01: 必须与**图标**用同一条投影(`_icon_live_screen` →
        #   `MapView.geo_to_screen_round`, 连续差值一次取整)。两者原先都走两段式
        #   取整 `int(px*s)-int(view*s)`, 相机连续前推时在"图标不是相机目标"的场景
        #   (风季/追踪全部/多台风)会各自 ±1px 来回跳; 只修图标不修这里, 图标与活线段
        #   端点就会相差 ≤1px —— 看上去就是图标在抖。实测见
        #   `G:/tmp/probe_general_jitter.py`(399 帧 x 方向翻转 146 → 0)。
        #   注意: 这只改**每帧现算**的实时点; `screen_points`/路径缓存面仍走
        #   `geo_to_screen` + 整页平移复用, 一个字节没动。
        live_pos = ty.cpos()
        _live_fn = getattr(self, '_icon_live_screen', None)
        if live_pos and _live_fn is not None:
            live_xy = _live_fn(live_pos['la'], live_pos['lo'])
        else:
            live_xy = (self.latlon_to_screen(live_pos['la'], live_pos['lo'])
                       if live_pos else None)

        # ── 实时段：已走路径从当前报点连续延伸到台风实时位置 ──
        n_pts = len(ty.pts)
        if (ty.act and not ty.sf
                and live_xy and 0 <= ty.ci < n_pts - 1
                and len(screen_points) == n_pts):
            self._draw_live_segment(surface, ty, screen_points, path_alpha, live_xy)

        # ── 登陆点标记 ──
        self._draw_landfall_markers(surface, ty, path_alpha, highlight)

        # 当前位置高亮（直接绘制，不分配复合 Surface）
        if highlight and not ty.sf:
            if self._line_mode():
                if live_pos:
                    # P1: 圆点 -> 抗锯齿(paint_aa 精灵缓存)
                    paint_aa.aa_disc(surface, CUR_POS, live_xy, 2)
            else:
                cur_idx = ty.ci
                # 编辑模式(未播放): 高亮圆点跟随选中报点(与信息框/圆环/列表联动一致)
                if self.md == self.MODE_EDIT and not self.pl:
                    sel = getattr(self, '_edit_selected_point', None)
                    if sel is not None and 0 <= sel < n_pts:
                        cur_idx = sel
                if 0 <= cur_idx < n_pts:
                    p = ty.pts[cur_idx]
                    x, y = screen_points[cur_idx]
                    point_color = p['color'] if highlight else p['color_dim']
                    if path_alpha < 255 and len(point_color) == 3:
                        point_color = (*point_color, path_alpha)
                    point_radius_factor = self._size_factors()[0]
                    radius = max(1, int(3 * point_radius_factor))
                    highlight_r = radius + int(2 * point_radius_factor)
                    self._blit_highlight(surface, x, y, radius, highlight_r, point_color, self._drag_offset_x, self._drag_offset_y)

        # 用户 2026-10-01: 「运动矢量箭头」(红色方向箭头)整项去掉 ——
        # 调用点与  实现一并删除, 设置里的开关也已移除。

        # ── 编辑模式拖动指示：红色圆环 + 实时坐标 ──
        if (highlight and self.md == self.MODE_EDIT and self.dragging_point
                and ty is self.drag_typhoon
                and 0 <= self.drag_point_index < len(screen_points)):
            self._draw_drag_indicator(surface, ty, screen_points)
        elif (highlight and self.md == self.MODE_EDIT and not self.right_button_dragging):
            # A1/B7: 选中点高亮 + 悬停提示 + 点标签
            self._draw_edit_selection(surface, ty, screen_points)

    def _draw_live_segment(self, surface, ty, screen_points, path_alpha, live_xy):
        """已走路径的实时延伸段：从 pts[ci]（或样条中间点）画到台风当前插值位置。"""
        cx, cy = live_xy
        line_mode = self._line_mode()
        width = 4 if line_mode else 2
        color = ty.pts[min(ty.ci + 1, len(ty.pts) - 1)]['color'] if line_mode else PATH
        max_seg = self._path_max_seg_len

        pts_draw = []
        smooth_sp = ty.v.smooth_screen_points if self.smooth_path else None
        if smooth_sp:
            segs = max(1, self.smooth_path_segments)
            pt_list = ty.points_time
            t = 0.0
            if ty.ci + 1 < len(pt_list):
                t0, t1 = pt_list[ty.ci], pt_list[ty.ci + 1]
                if t1 > t0:
                    t = max(0.0, min(1.0, (ty.at - t0) / (t1 - t0)))
            i0 = min(ty.ci * segs, len(smooth_sp) - 1)
            i1 = min(i0 + int(t * segs), len(smooth_sp) - 1)
            pts_draw = list(smooth_sp[i0:i1 + 1])
        else:
            pts_draw = [screen_points[ty.ci]]
        pts_draw.append((cx, cy))
        if len(pts_draw) < 2:
            return
        ox, oy = self._drag_offset_x, self._drag_offset_y
        # 前段 pts_draw[:-1] 为旧视图坐标（需加偏移），尾点 live_xy 已是当前视图坐标
        pts_off = [(px + ox, py + oy) for px, py in pts_draw[:-1]]
        pts_off.append(pts_draw[-1])
        if path_alpha < 255:
            if len(color) == 4:
                color = (color[0], color[1], color[2], min(color[3], path_alpha))
            elif len(color) == 3:
                color = (*color, path_alpha)
        self._draw_lines_safe(surface, color, pts_off, width, max_seg)

    # ── 编辑拖动：局部 0.1° 网格（径向渐隐）──
    _grid_mask_cache: dict = {}
    _GRID_RADIUS = 130

    @classmethod
    def _get_radial_mask(cls, size: int) -> pygame.Surface:
        mask = cls._grid_mask_cache.get(size)
        if mask is None:
            small = pygame.Surface((32, 32), pygame.SRCALPHA)
            pygame.draw.circle(small, (255, 255, 255, 255), (16, 16), 13)
            mask = pygame.transform.smoothscale(small, (size, size))
            cls._grid_mask_cache[size] = mask
        return mask

    def _draw_snap_grid(self, surface, px, py, la0, lo0):
        """在拖动点周围渲染一小块吸附网格(步长跟随配置),向边缘渐变消失。"""
        step = getattr(self.cfg, 'edit_snap_step', 0.1) or 0.1
        if step <= 0:
            return
        R = self._GRID_RADIUS
        # 步长对应的像素间距
        x0, y0 = self.latlon_to_screen(la0, lo0)
        x1, _ = self.latlon_to_screen(la0, lo0 + step)
        _, y1 = self.latlon_to_screen(la0 + step, lo0)
        step_x = abs(x1 - x0)
        step_y = abs(y1 - y0)
        if step_x < 6 or step_y < 6:
            return  # 网格过密不渲染
        grid = pygame.Surface((R * 2, R * 2), pygame.SRCALPHA)
        color = (255, 255, 255, 40)
        half_range_x = (R / step_x + 1) * step
        half_range_y = (R / step_y + 1) * step
        # 竖线：经度 step 的整数倍
        k0 = math.floor((lo0 - half_range_x) / step)
        k1 = math.ceil((lo0 + half_range_x) / step)
        for k in range(k0, k1 + 1):
            gx, _ = self.latlon_to_screen(la0, k * step)
            lx = gx - px + R
            if 0 <= lx <= R * 2:
                pygame.draw.line(grid, color, (lx, 0), (lx, R * 2), 1)
        # 横线：纬度 step 的整数倍
        m0 = math.floor((la0 - half_range_y) / step)
        m1 = math.ceil((la0 + half_range_y) / step)
        for m in range(m0, m1 + 1):
            _, gy = self.latlon_to_screen(m * step, lo0)
            ly = gy - py + R
            if 0 <= ly <= R * 2:
                pygame.draw.line(grid, color, (0, ly), (R * 2, ly), 1)
        grid.blit(self._get_radial_mask(R * 2), (0, 0),
                  special_flags=pygame.BLEND_RGBA_MULT)
        surface.blit(grid, (px - R, py - R))

    def _get_precomputed_landfalls(self, ty) -> list:
        """预计算整条路径的登陆点（陆地掩码地理采样，视图无关）。"""
        pts = ty.pts
        if not pts:
            return []
        # 键含首末报点内容: 只含 id(pts) 时列表被替换后 id 复用会命中陈旧记录
        key = (len(pts), pts[0].get('t'), pts[0].get('la'), pts[0].get('lo'),
               pts[-1].get('t'), pts[-1].get('la'), pts[-1].get('lo'))
        cached = getattr(ty, '_cached_landfalls', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        recs = []
        mm = getattr(self, 'map_mgr', None)
        if mm is None or mm._load_land_orig() is None:
            return recs
        prev_land = None
        steps = 16
        step_inv = 1.0 / steps
        for i in range(len(ty.pts) - 1):
            p0, p1 = ty.pts[i], ty.pts[i + 1]
            cat = p0.get('cat', self.get_strength_category(p0['w'], p0['st']))
            name = landfall_marker_name(p0['w'], cat)
            dla = p1['la'] - p0['la']
            dlo = p1['lo'] - p0['lo']
            while dlo > 180: dlo -= 360
            while dlo < -180: dlo += 360
            for s in range(steps + 1):
                t = s * step_inv
                la = p0['la'] + dla * t
                lo = p0['lo'] + dlo * t
                land = mm.is_land_at_geo(la, lo)
                if prev_land is False and land and name:
                    recs.append({'la': la, 'lo': lo, 'png': name, 'seg': i, 't': t})
                prev_land = land
        ty._cached_landfalls = (key, recs)
        return recs

    _MARKER_FUTURE_ALPHA = 110   # 未登陆时的半透明度
    _marker_fade_cache: dict = {}
    _MARKER_FADE_MAX = 256

    def _draw_landfall_markers(self, surface, ty, path_alpha, highlight=True):
        """在每个登陆点绘制 landfall_X.png：未登陆半透明，登陆后不透明并随路径淡出。
        关闭"显示未经过的路径"时，未越过的登陆点不绘制。"""
        recs = self._get_precomputed_landfalls(ty)
        if not recs:
            return
        show_future = self._show_future(highlight)
        prf = self._size_factors()[0]
        msize = max(5, int(10 * prf))

        # 当前段内进度（用于判断段中登陆点是否已越过）
        ci = ty.ci
        seg_t = 0.0
        pt_list = ty.points_time
        if ty.v.ipos and 0 <= ci < len(pt_list) - 1 and pt_list[ci + 1] > pt_list[ci]:
            seg_t = max(0.0, min(1.0, (ty.at - pt_list[ci]) / (pt_list[ci + 1] - pt_list[ci])))
        elif ci >= len(ty.pts) - 1:
            seg_t = 1.0

        fade_cache = TySimDrawPathMixin._marker_fade_cache
        for rec in recs:
            img = _get_landfall_marker(rec['png'], msize)
            if img is None:
                continue
            passed = ci > rec['seg'] or (ci == rec['seg'] and seg_t >= rec['t'])
            if not passed and not show_future:
                continue  # 不渲染未经过路径时也不渲染未来登陆点
            alpha = path_alpha if passed else self._MARKER_FUTURE_ALPHA * path_alpha // 255
            if alpha <= 0:
                continue
            # latlon_to_screen 已反映拖拽中的最新视图，叠加拖拽偏移会导致双重位移
            mx_, my_ = self.latlon_to_screen(rec['la'], rec['lo'])
            if alpha < 255:
                key = (id(img), alpha)
                entry = fade_cache.get(key)
                if entry is None or entry[0] is not img:
                    faded = img.copy()
                    faded.set_alpha(alpha)
                    entry = (img, faded)
                    if len(fade_cache) >= self._MARKER_FADE_MAX:
                        fade_cache.pop(next(iter(fade_cache)))
                    fade_cache[key] = entry
                img = entry[1]
            surface.blit(img, img.get_rect(center=(mx_, my_)))

    def _draw_drag_indicator(self, surface, ty, screen_points):
        px, py = screen_points[self.drag_point_index]
        p = ty.pts[self.drag_point_index]
        self._draw_snap_grid(surface, px, py, p['la'], p['lo'])
        point_radius_factor = self._size_factors()[0]
        r = max(6, int(5 * point_radius_factor)) + 5
        pulse = 1 + (pygame.time.get_ticks() // 300) % 2
        # P1: 拖动指示环 -> 抗锯齿圆环(原来是 pygame.draw.circle 的 width 描边)
        # 注意半径语义: pygame 的 circle(r, width) 描边占 [r-w, r];
        # paint_aa.aa_ring(R, width) 占 [R-w/2, R+w/2] ⇒ 传 R = r - w//2 才等价。
        paint_aa.aa_ring(surface, (255, 90, 90), (px, py), r - 1, 2)
        paint_aa.aa_ring(surface, (255, 150, 150), (px, py), r + pulse, 1)
        # 十字准星
        for ax, ay, bx, by in ((px - r - 5, py, px - r + 1, py), (px + r - 1, py, px + r + 5, py),
                               (px, py - r - 5, px, py - r + 1), (px, py + r - 1, px, py + r + 5)):
            pygame.draw.line(surface, (255, 90, 90), (ax, ay), (bx, by), 1)

        p = ty.pts[self.drag_point_index]
        la, lo = p['la'], p['lo']
        lat_dir = 'N' if la >= 0 else 'S'
        if lo > 180.0:
            lon_disp, lon_dir = 360.0 - lo, 'W'
        elif lo < 0:
            lon_disp, lon_dir = -lo, 'W'
        else:
            lon_disp, lon_dir = lo, 'E'
        txt = f"{abs(la):.1f}°{lat_dir} {lon_disp:.1f}°{lon_dir}"
        ts = rt(f_s, txt, (255, 255, 255))
        tb = rt(f_s, txt, (0, 0, 0))
        tx = px + r + 8
        ty_pos = py - ts.get_height() - r - 2
        for ox, oy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            surface.blit(tb, (tx + ox, ty_pos + oy))
        surface.blit(ts, (tx, ty_pos))

    @staticmethod
    def _blit_outlined_text(surface, ts, tb, x, y):
        for ox, oy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            surface.blit(tb, (x + ox, y + oy))
        surface.blit(ts, (x, y))

    @staticmethod
    def _fmt_day_label(t) -> str:
        """报点时间 → 整天数标签 '第N天'。t = YYYYMMDDHH[MM]。
        以报点所在年为第1天(月日相对历年首日的天数), 与季节跨度一致。"""
        try:
            dt = datetime(int(t[0:4]), int(t[4:6]), int(t[6:8]))
            y0 = datetime(dt.year, 1, 1)
            day = (dt - y0).days + 1
            return f"第{day}天"
        except (ValueError, TypeError, IndexError):
            return t

    def _draw_edit_selection(self, surface, ty, screen_points):
        """A1/B7: 编辑模式下绘制选中点高亮圆环+时间标签、悬停提示环、
        以及(开关开启时)全部报点的索引+时间标签。"""
        n = len(screen_points)
        if n == 0:
            return
        prf = self._size_factors()[0]
        radius = max(1, int(3 * prf))
        ring_r = radius + max(2, int(2 * prf))
        show_labels = bool(getattr(self, 'show_edit_point_labels', False))
        sel = getattr(self, '_edit_selected_point', None)

        # 悬停检测(鼠标贴近报点 8px)
        hover_idx = None
        mx, my = pygame.mouse.get_pos()
        if my < self.map_height and not self.dialog_mgr.any_active():
            best_d = 8
            for i, (sx, sy) in enumerate(screen_points):
                d = abs(mx - sx) + abs(my - sy)
                if d < best_d:
                    best_d = d
                    hover_idx = i

        for i, (x, y) in enumerate(screen_points):
            p = ty.pts[i]
            is_sel = (sel == i)
            if is_sel:
                # 选中: 白/黄圆环 + 具体时间标签(MM-DD HH:MM)
                # ⚠ P1 审计结论 —— 这三处 2px 细环**刻意保持无 AA**(与
                # `_blit_highlight` 同一条规则, 见那里的 docstring):
                #   它们与"当前位置红色高亮盘"在**同一半径区间**上逐层覆盖
                #   (白环 span [ring_r-2, ring_r] 正好压住红盘 [radius, highlight_r]),
                #   一旦两侧都变成软边, 红环最后那 4 个饱和 (220,60,60) 像素就没了,
                #   `tests/test_pan_reuse.py::test_highlight_follows_selection` 随即失败
                #   (实测: 8 passed -> 1 failed)。2px 环的 AA 收益亚像素级 ⇒ 不改。
                pygame.draw.circle(surface, (255, 220, 120), (x, y), ring_r + 2, 2)
                pygame.draw.circle(surface, (255, 255, 255), (x, y), ring_r, 2)
                t = p.get('t', '')
                if t:
                    txt = fmt_short_time(str(t))
                    ts = rt(f_s, txt, (255, 220, 120))
                    tb = rt(f_s, txt, (0, 0, 0))
                    self._blit_outlined_text(
                        surface, ts, tb,
                        x - ts.get_width() // 2,
                        y - ring_r - ts.get_height() - 4)
            elif i == hover_idx:
                pygame.draw.circle(surface, (200, 200, 220), (x, y), ring_r, 1)
            if show_labels:
                t = p.get('t', '')
                if len(t) >= 10:
                    # 全部报点标签: 序号 + 整天数(不显示完整时间)
                    txt = f"{i + 1} {self._fmt_day_label(t)}"
                    ts = rt(f_s, txt, (235, 235, 245))
                    tb = rt(f_s, txt, (0, 0, 0))
                    self._blit_outlined_text(
                        surface, ts, tb,
                        x - ts.get_width() // 2,
                        y + radius + 3)
