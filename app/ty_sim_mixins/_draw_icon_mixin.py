# py/ty_sim_mixins/_draw_icon_mixin.py
"""台风图标 + 名称 + 信息框渲染 Mixin。"""
from __future__ import annotations
import functools
import logging
import numpy as np
import pygame
from ..typhoon import TrackPoint
from ..constants import (
    f_name, rt, TXT,
    C3, C4, C5_L, C5_M, C5_D, MD_COLOR, C2_MINUS, C3_MINUS, C4_ST, STS,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
    INFO_BOX_BG, INFO_BOX_BORDER,
    ICON_SET_SMCY,
)
from ..smcy_icon import get_smcy_manager, _FRAME_INTERVAL_MS, _TOTAL_FRAMES
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..utils import get_tropical_points, max_wind_from_points, display_category
from ..utils import fmt_short_time, peak_point, movement_speed_kt
from ..ace_engine import _ace_eligible

logger = logging.getLogger(__name__)

_box_font = SmartFont(_load_font(FONT_FILE, 28, 28), _load_font(FONT_FILE, 28, 28))
_peak_font = SmartFont(_load_font(FONT_FILE, 19, 19), _load_font(FONT_FILE, 19, 19))

# 法9/G2: 普通/编辑信息框"实时ACE"数字缓存(量化到 4 位小数,数值字面量跨帧极少变化)。
# 与 draw_info_boxes_mixin 的 _ace_digit_cache 独立,避免跨模块共享同一 key 空间。
_box_ace_digit_cache: dict = {}
_BOX_ACE_DIGIT_CACHE_MAX = 64


def _text_shadow(surf, color=(0, 0, 0), alpha=120):
    """由带 alpha 的文字 surface 生成同形阴影(去背景信息框的对比度保障)。"""
    try:
        mask = pygame.mask.from_surface(surf)
        sh = mask.to_surface(setcolor=color, unsetcolor=(0, 0, 0, 0))
        sh.set_alpha(120)
        return sh
    except Exception:
        return None


_STROKE_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_info_box_font_cache: dict = {}


def _info_box_font(size: int):
    """按字号缓存 SmartFont(信息框字体缩放用)。"""
    f = _info_box_font_cache.get(size)
    if f is None:
        f = SmartFont(_load_font(FONT_FILE, size, size), _load_font(FONT_FILE, size, size))
        if len(_info_box_font_cache) > 24:
            _info_box_font_cache.pop(next(iter(_info_box_font_cache)))
        _info_box_font_cache[size] = f
    return f


def _outline_text(surf, outline_color=(0, 0, 0)):
    """给已渲染文字 surface 加 8 向描边(含换行), 返回 (w+2, h+2)。"""
    try:
        w, h = surf.get_size()
        out = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
        mask = pygame.mask.from_surface(surf)
        shade = mask.to_surface(setcolor=outline_color, unsetcolor=(0, 0, 0, 0))
        for dx, dy in _STROKE_OFFSETS:
            out.blit(shade, (dx + 1, dy + 1))
        out.blit(surf, (1, 1))
        return out
    except Exception:
        return surf


# ── 155+/170+ 紫色滤镜与辉光 ──

_PURPLE_TIERS = (
    # (最低风速, 滤镜强度)
    (170, 0.85),   # 170+: 更紫
    (155, 0.45),   # 155+: 偏紫
)


def _purple_tier(wind: int):
    for tier in _PURPLE_TIERS:
        if wind >= tier[0]:
            return tier
    return None


_PURPLE_LUTS: dict = {}


def _purple_luts(strength_q: int):
    """256 级逐像素饱和度差的整数 LUT(法8): 每级差 d=mx-mn 预计算乘数/加数。
    与原 float32 公式逐像素一致(取整差 ≤1)。strength_q = strength×100。"""
    luts = _PURPLE_LUTS.get(strength_q)
    if luts is None:
        d = np.arange(256, dtype=np.float32)
        k = (strength_q / 100.0) / 255.0
        f = d * k
        f05 = 0.5 * f
        r_perm = np.rint(np.clip(1.0 - f05, 0.0, 1.0) * 1000).astype(np.int16)
        g_perm = np.rint(np.clip(1.0 - 1.1 * f05, 0.0, 1.0) * 1000).astype(np.int16)
        b_perm = np.rint(np.clip(1.0 - f05, 0.0, 1.0) * 1000).astype(np.int16)
        b_add = np.rint(255.0 * f05 * 1000).astype(np.int32)
        luts = (r_perm, g_perm, b_perm, b_add)
        _PURPLE_LUTS[strength_q] = luts
        if len(_PURPLE_LUTS) > 8:
            _PURPLE_LUTS.pop(next(iter(_PURPLE_LUTS)))
    return luts


def _apply_purple_filter(surf: pygame.Surface, strength: float) -> pygame.Surface:
    """按像素饱和度把彩色部分推向紫色；白/灰(低饱和)部分不受影响。

    C5 图标主色为品红 (255,0,255)，"更紫"意味着压低红色通道
    （C5_M=191,0,255 → C5_D=128,0,255），同时压绿提蓝。
    LUT 整数化实现(法8): 免每帧 float32 全图转换。"""
    strength_q = int(round(strength * 100))
    r_perm, g_perm, b_perm, b_add = _purple_luts(strength_q)
    out = surf.copy()
    px = pygame.surfarray.pixels3d(out)
    r = px[..., 0].astype(np.uint16)
    g = px[..., 1].astype(np.uint16)
    b = px[..., 2].astype(np.uint16)
    d = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    px[..., 0] = np.clip(r * r_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 1] = np.clip(g * g_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 2] = np.clip((b * b_perm[d] + b_add[d]) // 1000, 0, 255).astype(np.uint8)
    del px
    return out


# ── TS 强度渐变滤镜: 34kt 原图绿 → 49kt 黄绿(与 STS 图标衔接) ──

_TS_GRAD_MIN_WIND = 34
_TS_GRAD_MAX_WIND = 49


def _ts_gradient_t(wind) -> float:
    """TS 图标滤镜渐变进度: 34kt→0(TS 原图绿, 与 TD 蓝边界清晰), 49kt→1(黄绿=STS 色系)。
    49kt 为渐变端点(逐 1kt, 缓解 48→49 转 STS 的跳变)。"""
    if wind <= _TS_GRAD_MIN_WIND:
        return 0.0
    if wind >= _TS_GRAD_MAX_WIND:
        return 1.0
    return (wind - _TS_GRAD_MIN_WIND) / (_TS_GRAD_MAX_WIND - _TS_GRAD_MIN_WIND)


def _ts_wind_bucket(wind) -> int:
    """TS 渐变风速量化(3kt 一档)。

    逐 1kt 做缓存键会让滤镜几乎每帧失效(实测约 0.55 次/帧、每次 ~4ms 的
    numpy 全图运算); 3kt 一档在绿→黄绿渐变上肉眼无差别, 重算降到约 1/3。"""
    try:
        return int(round(float(wind) / 3.0)) * 3
    except (TypeError, ValueError):
        return _TS_GRAD_MIN_WIND


_TS_GRAD_LUTS: dict = {}


def _ts_grad_luts(t_q: int):
    """TS 渐变滤镜 LUT(与 _purple_luts 同构): 每档进度 t 预计算通道系数。

    绿端(34kt): 保持 TS 原图绿色(SMCY TS 视频/简单 TS 图标均为绿);
    黄绿端(49kt): 提红+少量附蓝、压净蓝 → 与 STS 图标色系(SMCY TS+ #FFFF34 /
                    简单 STS #B8FF00)衔接, 类别切换无跳变;
    中间按 t 线性插值, 通道系数连续, 无瞬变。"""
    luts = _TS_GRAD_LUTS.get(t_q)
    if luts is None:
        t = t_q / 100.0
        r_c = 0.0                                   # 红通道不缩放(保留原纹理)
        g_c = 0.0                                   # 绿通道不缩放
        b_c = 0.0 * (1 - t) - 1.00 * t              # 黄绿端压净蓝
        r_a = 0.80 * t                              # 黄绿端提红 → 与 STS 黄色系衔接
        b_a = 0.20 * t                              # 黄绿端带少量蓝(SMCY TS+ #FFFF34)
        d = np.arange(256, dtype=np.float32)
        f = d / 255.0
        r_perm = np.rint(np.clip(1.0 + r_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        g_perm = np.rint(np.clip(1.0 + g_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        b_perm = np.rint(np.clip(1.0 + b_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        r_add = np.rint(255.0 * r_a * f * 1000).astype(np.int32)
        b_add = np.rint(255.0 * b_a * f * 1000).astype(np.int32)
        luts = (r_perm, g_perm, b_perm, r_add, b_add)
        _TS_GRAD_LUTS[t_q] = luts
        if len(_TS_GRAD_LUTS) > 20:
            _TS_GRAD_LUTS.pop(next(iter(_TS_GRAD_LUTS)))
    return luts


def _apply_ts_gradient_filter(surf: pygame.Surface, wind) -> pygame.Surface:
    """按风速把彩色部分在 绿(34kt, TS 原图)↔黄绿(49kt, STS 色系) 间渐变着色, 白/灰不受影响。"""
    t = _ts_gradient_t(wind)
    t_q = int(round(t * 100))
    r_perm, g_perm, b_perm, r_add, b_add = _ts_grad_luts(t_q)
    out = surf.copy()
    px = pygame.surfarray.pixels3d(out)
    r = px[..., 0].astype(np.int32)
    g = px[..., 1].astype(np.int32)
    b = px[..., 2].astype(np.int32)
    d = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    px[..., 0] = np.clip((r * r_perm[d] + r_add[d]) // 1000, 0, 255).astype(np.uint8)
    px[..., 1] = np.clip(g * g_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 2] = np.clip((b * b_perm[d] + b_add[d]) // 1000, 0, 255).astype(np.uint8)
    del px
    return out


class TySimDrawIconMixin:
    """台风图标、名称、信息框的绘制。"""

    _info_box_cache_typhoon: dict = {}
    _info_box_last_data: dict = {}

    _ring_scale_cache: dict = {}
    _center_scale_cache: dict = {}
    _l3_scale_cache: dict = {}
    _ts_grad_cache: dict = {}   # TS 渐变滤镜结果缓存 (w, h, 风速kt)

    @classmethod
    def _clear_icon_scale_caches(cls):
        cls._ring_scale_cache.clear()
        cls._center_scale_cache.clear()
        cls._l3_scale_cache.clear()
        cls._purple_frame_cache.clear()
        cls._purple_frame_cache_bytes = 0
        cls._ts_grad_cache.clear()

    @classmethod
    def _get_scaled_image(cls, img, new_w, new_h, cat, cache_dict):
        key = (cat, new_w, new_h)
        if key in cache_dict:
            return cache_dict[key]
        scaled = pygame.transform.smoothscale(img, (new_w, new_h))
        if len(cache_dict) > 64:
            cache_dict.pop(next(iter(cache_dict)))
        cache_dict[key] = scaled
        return scaled

    # ── 图标 + 名称 + 信息框 ──
    # 注: _size_factors 由 TySimDrawPathMixin 提供(MRO 在前); 此处不再重复定义,
    # 避免两份实现日后只改一份导致图标/路径尺寸不一致

    def draw_typhoon_info(self, surface: pygame.Surface, ty) -> None:
        cp = ty.cp()
        if not cp:
            return
        # 提前渐进预载下一类别图标(SMCY 惰性视频: 首次切换会一次性解码卡顿)
        self._preload_upcoming_icon(ty)
        # 接近海岸时预解码登陆特效视频, 避免登陆瞬间首次加载卡顿导致特效缺失
        self._preload_landfall_effect(ty)
        # 拖拽期间使用与路径相同的坐标系：stale screen_points + drag_offset
        # 平滑路径下沿曲线采样，与活线段/路径一致（B12）
        if self.right_button_dragging and (self._drag_offset_x or self._drag_offset_y):
            screen_points = getattr(ty, 'screen_points', None)
            cur_idx = ty.ci
            if not screen_points or cur_idx < 0 or cur_idx >= len(screen_points):
                return
            smooth_sp = ty.v.smooth_screen_points if self.smooth_path else None
            if (smooth_sp and cur_idx < len(ty.pts) - 1
                    and len(ty.points_time) > cur_idx + 1):
                segs = max(1, self.smooth_path_segments)
                total = ty.points_time[cur_idx + 1] - ty.points_time[cur_idx]
                progress = (ty.at - ty.points_time[cur_idx]) / total if total > 0 else 0.0
                progress = max(0.0, min(1.0, progress))
                i0 = cur_idx * segs
                raw = progress * segs
                i1 = min(i0 + int(raw), len(smooth_sp) - 1)
                if i1 < i0:
                    i1 = i0
                p0 = smooth_sp[i1]
                if i1 + 1 < len(smooth_sp):
                    frac = raw - int(raw)
                    p1 = smooth_sp[i1 + 1]
                    x = p0[0] + (p1[0] - p0[0]) * frac
                    y = p0[1] + (p1[1] - p0[1]) * frac
                else:
                    x, y = p0
                x += self._drag_offset_x
                y += self._drag_offset_y
            elif cur_idx < len(screen_points) - 1 and ty.ci < len(ty.pts) - 1:
                total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
                progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
                progress = max(0.0, min(1.0, progress))
                pt1 = screen_points[cur_idx]
                pt2 = screen_points[cur_idx + 1]
                x = int(pt1[0] + (pt2[0] - pt1[0]) * progress) + self._drag_offset_x
                y = int(pt1[1] + (pt2[1] - pt1[1]) * progress) + self._drag_offset_y
            else:
                x = screen_points[cur_idx][0] + self._drag_offset_x
                y = screen_points[cur_idx][1] + self._drag_offset_y
        else:
            pos = ty.cpos()
            if not pos:
                return
            x, y = self.latlon_to_screen(pos['la'], pos['lo'])

        show_icon = not (self.md == self.MODE_EDIT and not self.pl)
        icon_factor = self._size_factors()[1]
        icon_alpha = 255
        v = ty.v

        if show_icon and self.fade_typhoon:
            n_pts = len(ty.pts)
            if ty.ci >= n_pts - 2 and n_pts >= 2:
                if v.ipos and ty.ci + 1 < n_pts:
                    total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
                    progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
                else:
                    progress = 1.0 if ty.ci >= n_pts - 1 else 0.0
                icon_alpha = max(0, int(255 * (1.0 - progress)))

        # 刚生成时的渐入效果（生成后 800ms 内 alpha 从 0 线性至 255）
        if v._spawn_time:
            now = pygame.time.get_ticks()
            elapsed = now - v._spawn_time
            if elapsed < 800:
                icon_alpha = icon_alpha * min(255, int(elapsed * 0.31875)) // 255

        icon_alpha = icon_alpha * getattr(v, 'icon_alpha', 255) // 255

        if show_icon and icon_alpha > 0:
            cat = cp.get('cat', self.get_strength_category(cp['w'], cp['st']))
            trans = self._get_transition(ty)

            if self.cfg.icon_set == ICON_SET_SMCY:
                if v._spawn_time:
                    self._advance_smcy_frame(ty, cat, now, self.sp)
                else:
                    self._advance_smcy_frame(ty, cat, pygame.time.get_ticks(), self.sp)

                if trans:
                    old_cat, old_a, new_cat, new_a = trans
                    old_final = (icon_alpha * old_a) // 255
                    new_final = (icon_alpha * new_a) // 255
                    f = v._smcy_frame
                    if old_final > 0:
                        self._draw_smcy_frame(surface, ty, old_cat, f, x, y, icon_factor, old_final, cp['w'])
                    if new_final > 0:
                        self._draw_smcy_frame(surface, ty, new_cat, f, x, y, icon_factor, new_final, cp['w'])
                else:
                    self._draw_smcy_frame(surface, ty, cat, v._smcy_frame, x, y, icon_factor, icon_alpha, cp['w'])
            else:
                if trans:
                    old_cat, old_a, new_cat, new_a = trans
                    old_final = (icon_alpha * old_a) // 255
                    new_final = (icon_alpha * new_a) // 255
                    if old_final > 0:
                        self._draw_simple_icon(surface, ty, old_cat, cp, x, y, icon_factor, old_final)
                    if new_final > 0:
                        self._draw_simple_icon(surface, ty, new_cat, cp, x, y, icon_factor, new_final)
                else:
                    self._draw_simple_icon(surface, ty, cat, cp, x, y, icon_factor, icon_alpha)

        show_name = not (self.md == self.MODE_EDIT and not self.pl) and icon_alpha > 0
        if show_name:
            self.draw_typhoon_name(surface, ty, x, y, icon_factor, icon_alpha)

        if self.md == self.MODE_NORMAL and self.show_info_box_normal:
            self._draw_info_box(surface, ty, cp)
        elif self.md == self.MODE_EDIT:
            display_pt = cp
            if not self.pl:
                sel = getattr(self, '_edit_selected_point', None)
                if sel is not None and 0 <= sel < len(ty.pts):
                    display_pt = ty.pts[sel]
                elif 0 <= ty.ci < len(ty.pts):
                    display_pt = ty.pts[ty.ci]
            self._draw_info_box(surface, ty, display_pt)

    # ── 类别渐变过渡 ──
    def _get_transition(self, ty):
        """返回 (old_cat, old_alpha, new_cat, new_alpha) 或 None。
        ty.at / points_time 使用相同时间单位：0.5 单位 = 6 模拟小时。
        新图标: 0%→100% (T-3h→T+3h, 线性 6h)
        旧图标: 100%→75% (T-3h→T+2h) 再 75%→0% (T+2h→T+3h)
        """
        if not ty.pts or len(ty.points_time) != len(ty.pts):
            return None
        cur_idx = ty.ci
        now = ty.at

        def _calc(c, n, t):
            elapsed = (now - t) * 12.0
            if elapsed < -3.0 or elapsed > 3.0:
                return None
            # 新图标: 线性 0→1 over 6h
            new_pct = (elapsed + 3.0) / 6.0
            new_a = int(255.0 * max(0.0, min(1.0, new_pct)))
            # 旧图标: 100%→75% over 5h, 再 75%→0% over 1h
            if elapsed <= 2.0:
                old_pct = 1.0 - 0.05 * (elapsed + 3.0)
            else:
                old_pct = 0.75 * (3.0 - elapsed)
            old_a = int(255.0 * max(0.0, min(1.0, old_pct)))
            return (c, old_a, n, new_a)

        if cur_idx + 1 < len(ty.pts):
            cur_cat = ty.pts[cur_idx].get('cat', self.get_strength_category(ty.pts[cur_idx]['w'], ty.pts[cur_idx]['st']))
            next_cat = ty.pts[cur_idx + 1].get('cat', self.get_strength_category(ty.pts[cur_idx + 1]['w'], ty.pts[cur_idx + 1]['st']))
            if cur_cat != next_cat:
                r = _calc(cur_cat, next_cat, ty.points_time[cur_idx + 1])
                if r: return r
        if cur_idx > 0:
            prev_cat = ty.pts[cur_idx - 1].get('cat', self.get_strength_category(ty.pts[cur_idx - 1]['w'], ty.pts[cur_idx - 1]['st']))
            cur_cat = ty.pts[cur_idx].get('cat', self.get_strength_category(ty.pts[cur_idx]['w'], ty.pts[cur_idx]['st']))
            if prev_cat != cur_cat:
                r = _calc(prev_cat, cur_cat, ty.points_time[cur_idx])
                if r: return r
        return None

    # ── 类别切换预载 ──
    def _preload_upcoming_icon(self, ty) -> None:
        """下一报点类别与当前不同时, 提前渐进预载新类别 SMCY 视频帧。
        首次切换类别时视频是惰性打开+解码(几十~上百 ms)→ 画面卡一下,
        并从进入当前段起每帧解码少量帧, 把成本摊平到切换前的十几帧里。"""
        try:
            if self.cfg.icon_set != ICON_SET_SMCY:
                return
            pts = ty.pts
            ci = ty.ci
            if (not pts or ci < 0 or ci + 1 >= len(pts)
                    or len(ty.points_time) != len(pts)):
                return
            cur_cat = pts[ci].get('cat') or self.get_strength_category(pts[ci]['w'], pts[ci]['st'])
            nxt_cat = pts[ci + 1].get('cat') or self.get_strength_category(pts[ci + 1]['w'], pts[ci + 1]['st'])
            if nxt_cat == cur_cat:
                return
            st = getattr(ty, '_icon_preload_state', None)
            key = (ci, nxt_cat)
            if st is not None and st.get('key') == key and st.get('remain', 1) <= 0:
                return
            from ..smcy_icon import get_smcy_manager
            if st is None or st.get('key') != key:
                st = {'key': key, 'hemi': 'S' if ty.v.mirror else 'N',
                      'ts': None, 'remain': 30}
                ty._icon_preload_state = st
            mgr = get_smcy_manager()
            ts = self._smcy_ts_for(ty, nxt_cat, self._size_factors()[1])
            if st.get('ts') != ts:
                st['ts'] = ts
                st['remain'] = 30
            st['remain'] = mgr.preload_window_step(nxt_cat, st['hemi'], 0, 30, 2, ts)
        except Exception as ex:
            ty._icon_preload_state = None
            logger.debug(f"SMCY 图标预载失败: {ex}", exc_info=True)

    # ── 登陆特效预解码 ──
    def _preload_landfall_effect(self, ty) -> None:
        """台风接近(下一报点内)预计算登陆点时, 提前解码 SMCY 登陆特效视频。
        登陆触发瞬间 `get_landfall_frames` 首次解码约 100-160ms, 会卡一帧且
        可能让用户看到登陆特效缺失; 提前解码后触发即播。"""
        try:
            if self.cfg.icon_set != ICON_SET_SMCY:
                return
            pts = ty.pts
            if not pts:
                return
            recs = self._get_precomputed_landfalls(ty)
            if not recs:
                return
            ci = ty.ci
            # 未经过的最近登陆点, 且距当前报点 <= 1 段(即将登陆)
            warmed = getattr(ty, '_landfall_warm', None)
            for rec in recs:
                if rec['seg'] > ci + 1:
                    continue
                if ci > rec['seg']:
                    continue
                p = pts[rec['seg']]
                cat = self.get_strength_category(p.get('w', 0), p.get('st', ''))
                if warmed is None:
                    warmed = set()
                    ty._landfall_warm = warmed
                if cat in warmed:
                    continue
                warmed.add(cat)
                from ..smcy_icon import get_landfall_frames, get_landed_frame
                icon_factor = self._size_factors()[1]
                size = max(20, 4 * round(70 * icon_factor * 1.5 / 4))
                get_landfall_frames(cat, size, size)
                get_landed_frame(cat, 0, (size, size))
                break
        except Exception:
            pass

    # ── 简单图标绘制 ──
    def _draw_simple_icon(self, surface, ty, cat, cp, x, y, icon_factor, icon_alpha):
        ring_img = self.res_mgr.get_image(f"{cat}_ring")
        center_img = self.res_mgr.get_image(f"{cat}_center")
        if not ring_img:
            ring_img = self._create_fallback_ring()
        if not center_img:
            center_img = self._create_fallback_center()
        if not (ring_img and center_img):
            return

        orig_w, orig_h = ring_img.get_size()
        target_size = max(20, int(70 * icon_factor * (1.5 if cat == 'EX' else 1.0)))
        scale = target_size / max(orig_w, orig_h)
        new_w, new_h = max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        base_ring = self._get_scaled_image(ring_img, new_w, new_h, cat, self._ring_scale_cache)
        # TS 强度渐变滤镜: 34kt 绿(TS 原图) → 49kt 黄绿(与 STS 衔接), 结果按 (尺寸,风速) 缓存
        if cat == 'TS':
            wq = _ts_wind_bucket(cp['w'])
            ckey = (new_w, new_h, wq)
            cring = self._ts_grad_cache.get(ckey)
            if cring is None:
                cring = _apply_ts_gradient_filter(base_ring, wq)
                if len(self._ts_grad_cache) > 40:
                    self._ts_grad_cache.pop(next(iter(self._ts_grad_cache)))
                self._ts_grad_cache[ckey] = cring
            base_ring = cring
        total_rotation = ty.ra + ty.sa

        # tint 并入旋转缓存（避免每帧 tint_image 新建 Surface）
        tint_color = None
        if cat == "C5":
            tint_color = self._get_c5_color(cp['w'])
        elif cat in ("C2-", "C3-", "C4-ST"):
            tint_color = self._get_sub_gradient_color(cp['w'], cat)
        elif cat == "MD":
            tint_color = MD_COLOR
        elif cat == "STS":
            tint_color = STS
        rotated_ring = ty.get_rotated_ring(
            (cat, new_w, new_h), base_ring, total_rotation, ty.mirror,
            tuple(tint_color[:3]) if tint_color else None)

        if icon_alpha < 255:
            rotated_ring = rotated_ring.copy()
            rotated_ring.set_alpha(icon_alpha)

        rect = rotated_ring.get_rect(center=(x, y))

        target_center_size = max(10, int(20 * icon_factor))
        csz = int(target_center_size)
        cent_img_scaled = self._get_scaled_image(center_img, csz, csz, cat, self._center_scale_cache)
        if icon_alpha < 255:
            cent_img_scaled = cent_img_scaled.copy()
            cent_img_scaled.set_alpha(icon_alpha)
        cent_rect = cent_img_scaled.get_rect(center=(x, y))

        if cat == "LO":
            surface.blit(cent_img_scaled, cent_rect)
            surface.blit(rotated_ring, rect)
        else:
            surface.blit(rotated_ring, rect)
            surface.blit(cent_img_scaled, cent_rect)

        level3_key = None
        if cat in ("C3-", "C3"):
            level3_key = "C3_3"
        elif cat in ("C4", "C4-ST"):
            level3_key = "C4_3"
        elif cat == "C5":
            level3_key = "C5_3"
        if level3_key:
            l3_ring_img = self.res_mgr.get_image(f"{level3_key}_ring")
            if l3_ring_img:
                l3_orig_w, l3_orig_h = l3_ring_img.get_size()
                target_l3_size = max(int(65 * icon_factor), 20)
                l3_scale = target_l3_size / max(l3_orig_w, l3_orig_h)
                l3_w, l3_h = int(l3_orig_w * l3_scale), int(l3_orig_h * l3_scale)
                l3_base = self._get_scaled_image(l3_ring_img, l3_w, l3_h, level3_key, self._l3_scale_cache)
                if cat in ("C3-", "C3"):
                    l3_angle = ty.sa3
                elif cat in ("C4", "C4-ST"):
                    l3_angle = ty.sa4
                else:
                    l3_angle = ty.sa5
                if cat == "C5":
                    l3_color = self._get_c5_color(cp['w'])
                elif cat in ("C3-", "C4-ST"):
                    l3_color = self._get_sub_gradient_color(cp['w'], cat) or self.get_point_color(cp['w'], cp['st'])
                elif cat == "C3":
                    l3_color = C3
                elif cat == "C4":
                    l3_color = C4
                else:
                    l3_color = self.get_point_color(cp['w'], cp['st'])
                l3_rotated = ty.get_rotated_level3_ring(
                    (level3_key, l3_w, l3_h), l3_base, l3_angle, ty.mirror,
                    tuple(l3_color[:3]))
                if icon_alpha < 255:
                    l3_rotated = l3_rotated.copy()
                    l3_rotated.set_alpha(icon_alpha)
                l3_rect = l3_rotated.get_rect(center=(x, y))
                surface.blit(l3_rotated, l3_rect)

    # ── SMCY 视频图标绘制 ──
    @staticmethod
    def _smcy_ts_for(ty, cat, icon_factor: float):
        """计算 SMCY 帧绘制目标尺寸(与 _draw_smcy_frame 使用同一 icon_factor)。"""
        try:
            mgr = get_smcy_manager()
            orig = mgr.get_size(cat, 'S' if ty.v.mirror else 'N')
            if not orig or orig[0] <= 0 or orig[1] <= 0:
                orig = (400, 400)
            size_mult = 3.0 if cat == 'EX' else 1.5
            target = max(20, 4 * round(70 * icon_factor * size_mult / 4))
            scale = target / max(orig[0], orig[1])
            return (max(1, int(orig[0] * scale)), max(1, int(orig[1] * scale)))
        except Exception:
            return None

    def _advance_smcy_frame(self, ty, cat, now, speed_factor=1.0):
        """只推进 SMCY 帧索引，不绘制。"""
        v = ty.v
        hemi = 'S' if v.mirror else 'N'
        cat_key = f"{hemi}:{cat}"
        if v._smcy_last_cat != cat_key:
            if v._smcy_last_cat:
                v._smcy_frame = (v._smcy_frame + 1) % _TOTAL_FRAMES
            v._smcy_last_cat = cat_key
            v._smcy_last_ticks = now
        else:
            elapsed = now - v._smcy_last_ticks
            interval = max(1, int(_FRAME_INTERVAL_MS / max(speed_factor, 0.1)))
            if elapsed >= interval:
                advance, rem = divmod(elapsed, interval)
                if advance > 8:
                    # N19: 长时间未绘制时限制单次跳帧,并重置计时避免持续追赶
                    advance = 8
                    rem = 0
                v._smcy_frame = (v._smcy_frame + advance) % _TOTAL_FRAMES
                v._smcy_last_ticks = now - rem
                # 法32: 运行时预载下一段帧窗口(与解码预算共用,不抢占)
                # K21: 传与绘制一致的尺寸,避免 get_frame 因尺寸变化清空缓存
                # N: 渐进预载(每帧最多 3 帧), 避免一次同步解码 30 帧造成 10-30ms 卡顿
                try:
                    mgr = get_smcy_manager()
                    nxt = (v._smcy_frame + 30) % _TOTAL_FRAMES
                    if not mgr.has_frame_window(cat, hemi, nxt, 30):
                        ts = TySimDrawIconMixin._smcy_ts_for(ty, cat, self._size_factors()[1])
                        mgr.preload_window_step(cat, hemi, nxt, 30, budget=3, target_size=ts)
                except Exception:
                    pass

    # ── 紫滤镜结果缓存：避免每帧对 C5 图标做 numpy 全图运算 ──
    _purple_frame_cache: dict = {}
    _purple_frame_cache_bytes: int = 0
    _PURPLE_FRAME_CACHE_MAX = 120
    # 只按条数封顶时, 放大图标尺寸后单帧可达数 MB, 120 条能吃到数百 MB
    _PURPLE_FRAME_CACHE_BYTES = 96 * 1024 * 1024

    @classmethod
    def _cache_purple_frame(cls, key, frame) -> None:
        """按条数 + 总字节双上限缓存紫滤镜/TS 渐变结果。"""
        cache = cls._purple_frame_cache
        old = cache.pop(key, None)
        if old is not None:
            cls._purple_frame_cache_bytes -= old.get_width() * old.get_height() * 4
        cache[key] = frame
        cls._purple_frame_cache_bytes += frame.get_width() * frame.get_height() * 4
        while cache and (len(cache) > cls._PURPLE_FRAME_CACHE_MAX
                         or cls._purple_frame_cache_bytes > cls._PURPLE_FRAME_CACHE_BYTES):
            k = next(iter(cache))
            v = cache.pop(k)
            cls._purple_frame_cache_bytes -= v.get_width() * v.get_height() * 4

    def _draw_smcy_frame(self, surface, ty, cat, frame_idx, x, y, icon_factor, icon_alpha,
                         wind: int = 0):
        """纯绘制指定帧，按视频原始宽高比缩放。"""
        hemi = HEMISPHERE_SOUTH if ty.v.mirror else HEMISPHERE_NORTH
        mgr = get_smcy_manager()
        orig = mgr.get_size(cat, hemi)
        # N10: (0,0) 是非空 tuple,须显式防护,否则 scale 除零
        if not orig or orig[0] <= 0 or orig[1] <= 0:
            orig = (400, 400)
        size_mult = 3.0 if cat == 'EX' else 1.5   # EX 特殊处理：放大 1.5 倍
        # 4px 网格量化：缩放档内 ts 恒定，解码缓存存活（拖14）
        target = max(20, 4 * round(70 * icon_factor * size_mult / 4))
        scale = target / max(orig[0], orig[1])
        ts = (max(1, int(orig[0] * scale)), max(1, int(orig[1] * scale)))
        # 155+/170+ 紫色滤镜（不影响白色部分），结果按帧缓存
        tier = _purple_tier(wind) if cat == 'C5' else None
        is_ts = cat == 'TS'
        if tier is not None:
            key = (cat, hemi, frame_idx, ts, tier[1])
            cache = TySimDrawIconMixin._purple_frame_cache
            frame = cache.get(key)
            if frame is None:
                raw = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
                if raw is None:
                    return
                frame = _apply_purple_filter(raw, tier[1])
                self._cache_purple_frame(key, frame)
        elif is_ts:
            # TS 强度渐变滤镜（34kt 绿 → 49kt 黄绿, 与 STS 衔接），结果按帧缓存
            wq = _ts_wind_bucket(wind)
            key = ('tsg', cat, hemi, frame_idx, ts, wq)
            cache = TySimDrawIconMixin._purple_frame_cache
            frame = cache.get(key)
            if frame is None:
                raw = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
                if raw is None:
                    return
                frame = _apply_ts_gradient_filter(raw, wq)
                self._cache_purple_frame(key, frame)
        else:
            frame = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
            if frame is None:
                return
        # R4: 不可在原处 mutate 共享帧面。frame 可能来自 _purple_frame_cache 共享缓存，
        # 或来自 smcy _VideoStream._cache（多台风共用同一 (类别,半球,帧号,尺寸) 帧）。
        # set_alpha 会永久改写共享面的表面 alpha——若此处 set_alpha<255 后 blit，
        # 其它台风/后续帧再取到同一面时 alpha 已被污染。与 _draw_simple_icon 一致：
        # 仅当需要淡出(<255)时 copy，正常 255 直绘不改共享面。
        if icon_alpha < 255:
            frame = frame.copy()
            frame.set_alpha(icon_alpha)
        rect = frame.get_rect(center=(x, y))
        surface.blit(frame, rect)

    # ── fallback 图标 ──
    _fallback_ring_cache = None
    _fallback_center_cache = None

    def _create_fallback_ring(self) -> pygame.Surface:
        cls = TySimDrawIconMixin
        if cls._fallback_ring_cache is None:
            s = pygame.Surface((80, 80), pygame.SRCALPHA)
            pygame.draw.circle(s, (200, 200, 200, 200), (40, 40), 35, 5)
            cls._fallback_ring_cache = s
        return cls._fallback_ring_cache

    def _create_fallback_center(self) -> pygame.Surface:
        cls = TySimDrawIconMixin
        if cls._fallback_center_cache is None:
            s = pygame.Surface((60, 60), pygame.SRCALPHA)
            pygame.draw.circle(s, (255, 255, 255, 240), (30, 30), 20)
            pygame.draw.circle(s, (50, 50, 50, 240), (30, 30), 4)
            cls._fallback_center_cache = s
        return cls._fallback_center_cache

    @staticmethod
    def _get_c5_color(wind: int):
        if wind >= 170:
            return C5_D
        if wind >= 155:
            return TySimDrawIconMixin._get_gradient_color(wind, C5_M, C5_D, 155, 170)
        return C5_L

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _get_gradient_color(wind, low_color, high_color, low_wind, high_wind):
        if wind >= high_wind:
            return high_color
        if wind >= low_wind:
            ratio = (wind - low_wind) / (high_wind - low_wind)
            return (
                int(low_color[0] + (high_color[0] - low_color[0]) * ratio),
                int(low_color[1] + (high_color[1] - low_color[1]) * ratio),
                int(low_color[2] + (high_color[2] - low_color[2]) * ratio),
            )
        return low_color

    @staticmethod
    def _get_sub_gradient_color(wind, cat):
        _GRADIENTS = {
            "C2-": (C2_MINUS, C2_MINUS, 83, 86),
            "C3-": ((255, 207, 69), C3_MINUS, 96, 105),
            "C4-ST": ((255, 0, 39), C4_ST, 130, 137),
        }
        if cat in _GRADIENTS:
            lo, hi, lo_w, hi_w = _GRADIENTS[cat]
            return TySimDrawIconMixin._get_gradient_color(wind, lo, hi, lo_w, hi_w)
        return None

    # ── 名称 ──
    def _get_max_wind_color(self, ty):
        """获取台风名称颜色（基于最大风速），结果缓存于 typhoon 对象。"""
        cache_attr = '_cached_max_wind_color'
        cached = getattr(ty, cache_attr, None)
        if cached is not None:
            return cached
        tropical = get_tropical_points(ty.pts)
        if tropical:
            mwp = max(tropical, key=lambda p: p['w'])
            color = mwp.get('color', self.get_point_color(mwp['w'], mwp['st']))
        else:
            color = TXT
        setattr(ty, cache_attr, color)
        return color

    def _get_point_name_color(self, ty, pname: str):
        """逐点名称模式：对每个名字按其对应点的最大风速单独计算颜色。"""
        cache = getattr(ty, '_cached_name_colors', None)
        if cache is None:
            cache = {}
            ty._cached_name_colors = cache
        if pname in cache:
            return cache[pname]
        pts = [p for p in ty.pts if (p.get('name') or '').strip() == pname]
        pool = get_tropical_points(pts) or pts
        if pool:
            mwp = max(pool, key=lambda p: p['w'])
            color = mwp.get('color', self.get_point_color(mwp['w'], mwp['st']))
        else:
            color = TXT
        cache[pname] = color
        return color

    @staticmethod
    def _pt_stronger(a, b) -> bool:
        """a 是否强于 b：先比风速，风速相同比气压（气压未知不算更强）。"""
        if a['w'] != b['w']:
            return a['w'] > b['w']
        pa, pb = a['p'], b['p']
        return bool(pa and pb and pa < pb)

    @classmethod
    def _pt_tied(cls, a, b) -> bool:
        """两报强度持平（互相都不严格更强）。"""
        return not cls._pt_stronger(a, b) and not cls._pt_stronger(b, a)

    def _get_peaks(self, ty) -> list:
        """巅峰列表：风速高于前后两个合格报（可计算 ACE 的报 + 性质与风速合格的非正式报）。
        平顶（连续持平报）取第一报：向后跳过持平报后再与首个不同强度的报比较。
        合格报之间如有非合格报（风速回落），仍视为独立巅峰。"""
        cached = getattr(ty, '_cached_peaks', None)
        if cached is not None:
            return cached
        qual = [(i, p) for i, p in enumerate(ty.pts) if _ace_eligible(p)]
        peaks = []
        for k, (i, p) in enumerate(qual):
            if k > 0 and not self._pt_stronger(p, qual[k - 1][1]):
                continue
            # 跳过与当前持平的后续报（平顶），与平顶后的首个不同强度报比较
            m = k + 1
            while m < len(qual) and self._pt_tied(p, qual[m][1]):
                m += 1
            if m < len(qual) and not self._pt_stronger(p, qual[m][1]):
                # 下一个合格报更强 — 检查两者间是否存在非合格低谷（风速回落）
                valley = False
                for j in range(i + 1, qual[m][0]):
                    if ty.pts[j]['w'] < p['w']:
                        valley = True
                        break
                if not valley:
                    continue
            color = p.get('color', None) or self.get_point_color(p['w'], p['st'])
            peaks.append({'idx': i, 'w': p['w'], 'p': p['p'] or 0,
                          'color': tuple(color), 'strongest': False})
        if peaks:
            best = max(peaks, key=lambda pk: (pk['w'], -pk['p'] if pk['p'] else -100000))
            best['strongest'] = True
        ty._cached_peaks = peaks
        return peaks

    def _get_peak_label_surf(self, label: str, color, name_factor: float):
        key = ('peak', label, color, name_factor)
        surf = self._name_shadow_cache.get(key)
        if surf is None:
            from ..utils import render_glow_text
            surf = render_glow_text(_peak_font, label, color)
            if name_factor != 1.0:
                nw = max(1, int(surf.get_width() * name_factor))
                nh = max(1, int(surf.get_height() * name_factor))
                surf = pygame.transform.smoothscale(surf, (nw, nh))
            self._name_shadow_cache[key] = surf
            if len(self._name_shadow_cache) > 256:
                self._name_shadow_cache.pop(next(iter(self._name_shadow_cache)))
        return surf

    _FADE_RAMP = 0.25       # 名称淡入/切换时长（points_time 单位，0.25 = 3 模拟小时）
    _PEAK_PRE_S = 0.5       # 巅峰前显示 0.5 秒（真实秒）
    _PEAK_POST_S = 0.5      # 巅峰后显示 0.5 秒
    _PEAK_POST_BEST_S = 1.0  # 最强巅峰后显示 1 秒
    _PEAK_FADE_S = 0.25     # 巅峰淡入淡出时长（真实秒）

    @staticmethod
    def _blit_faded(surface, surf, pos, alpha):
        if alpha >= 255:
            surface.blit(surf, pos)
        elif alpha > 0:
            tmp = surf.copy()
            tmp.set_alpha(alpha)
            surface.blit(tmp, pos)

    def _get_name_surf(self, name: str, color, name_factor: float):
        key = (name, color, name_factor)
        surf = self._name_shadow_cache.get(key)
        if surf is None:
            from ..utils import render_glow_text
            surf = render_glow_text(_box_font, name, color)
            if name_factor != 1.0:
                nw = max(1, int(surf.get_width() * name_factor))
                nh = max(1, int(surf.get_height() * name_factor))
                surf = pygame.transform.smoothscale(surf, (nw, nh))
            self._name_shadow_cache[key] = surf
            if len(self._name_shadow_cache) > 256:
                self._name_shadow_cache.pop(next(iter(self._name_shadow_cache)))
        return surf

    def draw_typhoon_name(self, surface: pygame.Surface, ty, x: int, y: int,
                           icon_factor: float = 1.0, alpha: int = 255) -> None:
        display_name = None
        name_color = None
        if getattr(self, 'point_name_mode', False):
            cp = ty.cp()
            pname = (cp.get('name') or '').strip() if cp else ''
            if pname:
                display_name = pname
                name_color = self._get_point_name_color(ty, pname)
        if display_name is None:
            display_name = self.get_display_name(ty)
            name_color = self._get_max_wind_color(ty)
        name_factor = getattr(self, 'name_size', 100) / 100.0
        if not hasattr(self, '_name_shadow_cache'):
            self._name_shadow_cache = {}

        # ── 名称切换检测（交叉淡入淡出）──
        if not hasattr(self, '_name_anim'):
            self._name_anim = {}
        state = self._name_anim.get(ty)
        if state is None or state['switch_at'] > ty.at:
            state = {'name': display_name, 'color': name_color,
                     'prev': None, 'switch_at': -1e9}
            self._name_anim[ty] = state
        elif state['name'] != display_name:
            state['prev'] = (state['name'], state['color'])
            state['switch_at'] = ty.at
            state['name'] = display_name
            state['color'] = name_color

        # 淡入：出场后 3 模拟小时内渐显
        pt = ty.points_time
        appear = 1.0
        if pt:
            appear = max(0.0, min(1.0, (ty.at - pt[0]) * 4.0))
        switch_t = min(1.0, max(0.0, (ty.at - state['switch_at']) * 4.0))

        offset_x, offset_y = int(30 * icon_factor), int(-20 * icon_factor)
        text_x, ty_pos = x + offset_x, y + offset_y
        # 辉光版表面含 4px 透明边距(缩放后为 4*name_factor),平移回原文字位置
        name_off = int(6 * name_factor)

        shadow_surf = self._get_name_surf(display_name, name_color, name_factor)
        name_alpha = int(alpha * appear * switch_t)
        self._blit_faded(surface, shadow_surf, (text_x - name_off, ty_pos - name_off), name_alpha)
        if switch_t < 1.0 and state['prev'] is not None:
            old_surf = self._get_name_surf(state['prev'][0], state['prev'][1], name_factor)
            old_alpha = int(alpha * appear * (1.0 - switch_t))
            self._blit_faded(surface, old_surf, (text_x - name_off, ty_pos - name_off), old_alpha)

        # ── 巅峰标注（名称下方，巅峰前后短暂显示，淡入淡出）──
        peaks = self._get_peaks(ty)
        if peaks and pt:
            sp = max(0.1, getattr(self, 'sp', 1.0))
            pk_factor = getattr(self, 'peak_label_size', 100) / 100.0
            total = len(peaks)
            py_pos = ty_pos + shadow_surf.get_height() - 1
            for n, pk in enumerate(peaks, 1):
                if pk['idx'] >= len(pt):
                    continue
                t_pk = pt[pk['idx']]
                pre = self._PEAK_PRE_S * sp
                post = (self._PEAK_POST_BEST_S if pk['strongest'] else self._PEAK_POST_S) * sp
                if not (t_pk - pre <= ty.at <= t_pk + post):
                    continue
                fade = max(1e-6, self._PEAK_FADE_S * sp)
                a = min(1.0, (ty.at - (t_pk - pre)) / fade, ((t_pk + post) - ty.at) / fade)
                pk_alpha = int(alpha * max(0.0, a))
                if pk_alpha <= 0:
                    continue
                prefix = f"Peak{n}" if total > 1 else "Peak"
                label = f"{prefix} {pk['w']}kt"
                if pk['p']:
                    label += f" {pk['p']}mb"
                surf_pk = self._get_peak_label_surf(label, pk['color'], pk_factor)
                pk_off = int(6 * pk_factor)
                self._blit_faded(surface, surf_pk, (text_x - pk_off, py_pos), pk_alpha)
                py_pos += surf_pk.get_height() - 2

    # ── 信息框 ──
    def _draw_info_box(self, surface: pygame.Surface, ty, point: TrackPoint) -> None:
        dark = getattr(self, 'dark_mode', True)
        # 法17: 峰值风速缓存(失效点与 _cached_max_wind_color 相同)
        mw = getattr(ty, '_cached_max_wind', None)
        if mw is None:
            mw = max_wind_from_points(ty.pts)
            ty._cached_max_wind = mw
        # 显示点索引(编辑模式可能是选中点,非播放位置)
        idx = next((i for i, p in enumerate(ty.pts) if p is point), ty.ci)
        prev_pt = ty.pts[idx - 1] if 0 < idx < len(ty.pts) else None
        peak = peak_point(ty.pts)
        peak_t = peak['t'] if peak else ''
        # 用显示点索引(idx)而非播放位置(ty.ci): 编辑模式回看选中点时移速才对应
        mv = movement_speed_kt(ty.pts, ty.points_time, idx)
        # ── S0 行配置(顺序 + 显隐 + 平滑) + 背景/阴影开关 ──
        rows_cfg = self.info_box_rows or []
        bg_on = bool(getattr(self, 'info_box_bg', False))
        shadow_on = bool(getattr(self, 'info_box_text_shadow', True))
        scale = float(getattr(self, 'info_box_scale', 1.2) or 1.0)
        cfg_fp = (tuple((r['key'], bool(r.get('on', True)), bool(r.get('smooth', True)))
                        for r in rows_cfg), bg_on, shadow_on, scale)
        key_data = (
            ty.b, ty.n, ty.cust, ty.sname, ty.start_time, point['t'],
            point['la'], point['lo'], point['w'], point['p'], point['st'],
            ty.tace, self.name_display_mode, dark,
            mw,
            ty.pts[0]['t'] if ty.pts else '',
            ty.basin,
            point.get('cat', ''),
            self.screen_width,      # 宽度随窗口缩放(避免 resize 后宽度陈旧)
            len(ty.pts), idx, round(mv or 0, 2), peak_t,
            cfg_fp,
        )
        if ty in self._info_box_cache_typhoon and self._info_box_last_data.get(ty) == key_data:
            box, ace_x, ace_y, ace_tc, ace_visible = self._info_box_cache_typhoon[ty]
        else:
            # 信息框字体: 按 info_box_scale 缩放(默认 1.2, 比旧版略大)
            scale = float(getattr(self, 'info_box_scale', 1.2) or 1.0)
            ifs = _info_box_font(max(12, int(round(21 * scale))))
            ifm = _info_box_font(max(12, int(round(28 * scale))))

            if dark:
                box_bg = (22, 28, 44, 220)
                box_border = (55, 85, 130)
                tc = (215, 225, 245)
                trend_up = (255, 150, 70)
                trend_dn = (110, 170, 250)
                shadow_col = (0, 0, 0)
            else:
                box_bg = INFO_BOX_BG
                box_border = INFO_BOX_BORDER
                tc = TXT
                trend_up = (200, 90, 0)
                trend_dn = (0, 90, 200)
                shadow_col = (255, 255, 255)

            # 小窗口下信息框宽度受 ACE 进度条左缘限制,避免遮挡
            ace_left = max(0, self.screen_width - 10 - 495)

            def build_row(key, smooth, max_w):
                """按行 key 生成 (surface, gap, is_ace); 无内容返回 None。
                所有文字带黑描边(加粗可读); 风速行与名称一致使用黑描边,
                文字色保留趋势色(↑橙/↓蓝)。"""
                def rt_o(font, text, color, outline=(0, 0, 0)):
                    return _outline_text(rt(font, text, color, max_w, smooth), outline)

                if key == 'name':
                    name = self.get_display_name(ty, self.name_display_mode)
                    return (rt_o(ifm, name, tc), 6, False)
                if key == 'time':
                    t = point['t']
                    with_year = bool(ty.pts and t[:4] != ty.pts[0]['t'][:4])
                    return (rt_o(ifs, f"时间: {fmt_short_time(t, with_year)}", tc), 3, False)
                if key == 'pos':
                    la = point['la']
                    lo = point['lo']
                    lat_dir = 'N' if la >= 0 else 'S'
                    lat_val = abs(la)
                    if lo > 180.0:
                        lon_disp, lon_dir = 360.0 - lo, 'W'
                    elif lo < -180.0:
                        lon_disp, lon_dir = lo + 360.0, 'W'
                    elif lo < 0:
                        lon_disp, lon_dir = -lo, 'W'
                    else:
                        lon_disp, lon_dir = lo, 'E'
                    return (rt_o(ifs, f"位置: {lat_val:.1f}°{lat_dir}, {lon_disp:.1f}°{lon_dir}", tc), 3, False)
                if key == 'wind':
                    wind_line = f"风速: {point['w']} kt"
                    if prev_pt is not None and point['w'] != prev_pt['w']:
                        d = point['w'] - prev_pt['w']
                        wind_line += f"  {'↑' if d > 0 else '↓'}{abs(d)}"
                    # 与台风名称样式一致: 白字 + 强度色描边(无趋势色)
                    outline = self._get_max_wind_color(ty)
                    return (rt_o(ifs, wind_line, (255, 255, 255), outline), 3, False)
                if key == 'cat':
                    cat = point.get('cat', self.get_strength_category(point['w'], point['st']))
                    pres_str = f"{point['p']} hPa" if point['p'] != 0 else "未知"
                    return (rt_o(ifs, f"等级: {display_category(cat)}   气压: {pres_str}", tc), 3, False)
                if key == 'speed':
                    if not mv:
                        return None
                    return (rt_o(ifs, f"移速: {mv:.0f} kt", tc), 3, False)
                if key == 'peak':
                    if not peak:
                        return None
                    return (rt_o(ifs, f"巅峰: {peak['w']} kt ({fmt_short_time(peak_t)})", tc), 3, False)
                if key == 'tace':
                    return (rt_o(ifs, f"总ACE: {ty.tace:.4f}", tc), 3, False)
                if key == 'cace':
                    return (rt_o(ifs, "实时ACE: ", tc), 3, True)
                return None

            # 第一遍: 无换行取自然宽度 → 自适应 box_w
            nat_rows = []
            for r in rows_cfg:
                if not r.get('on', True):
                    continue
                row = build_row(r['key'], bool(r.get('smooth', True)), None)
                if row is not None:
                    nat_rows.append(row)
            content_w = max((s.get_width() for s, _, _ in nat_rows), default=0)
            box_w = int(min(375, max(160, content_w + 30)))
            box_w = min(box_w, max(160, ace_left - 30))

            # 第二遍: 按 box_w 换行(仅当内容超宽时)
            max_w = box_w - 30
            need_wrap = any(s.get_width() > max_w for s, _, _ in nat_rows)
            rows = []
            row_keys = []
            for r in rows_cfg:
                if not r.get('on', True):
                    continue
                row = build_row(r['key'], bool(r.get('smooth', True)),
                                max_w if need_wrap else None)
                if row is not None:
                    rows.append(row)
                    row_keys.append(r['key'])

            # ── 动态高度 + 绘制 ──
            box_h = 14 + sum(s.get_height() + g for s, g, _ in rows) + 6
            bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            if bg_on:
                pygame.draw.rect(bg, box_bg, (0, 0, box_w, box_h), 0, 15)
                if 'color' in point:
                    bar_c = point['color']
                    if len(bar_c) == 3:
                        bar_c = (*bar_c, 220)
                    pygame.draw.rect(bg, bar_c, (0, 0, 8, box_h), 0, 14, 0, 0, 14)
                pygame.draw.rect(bg, box_border, (0, 0, box_w, box_h), 2, 15)

            y = 10
            ace_x = ace_y = 0
            ace_visible = False
            name_rect = None
            for i, (surf_ln, gap, is_ace) in enumerate(rows):
                if shadow_on:
                    sh = _text_shadow(surf_ln, shadow_col)
                    if sh is not None:
                        bg.blit(sh, (16, y + 1))
                bg.blit(surf_ln, (15, y))
                if row_keys[i] == 'name':
                    # 名称行屏幕矩形(供双击编辑 B4)
                    name_rect = pygame.Rect(22 + 15, 22 + y,
                                            surf_ln.get_width(), surf_ln.get_height())
                if is_ace:
                    ace_x = 15 + surf_ln.get_width()
                    ace_y = y
                    ace_visible = True
                y += surf_ln.get_height() + gap
            self._info_box_name_rect = name_rect

            self._info_box_cache_typhoon[ty] = (bg, ace_x, ace_y, tc, ace_visible)
            self._info_box_last_data[ty] = key_data
            box = bg
            ace_tc = tc

        surface.blit(box, (22, 22))
        if ace_visible:
            # 实时ACE数字每帧单独绘制（ASCII，底层字体绕过 SmartFont 缓存）。
            digit_key = (round(ty.cace, 4), ace_tc)
            digits = _box_ace_digit_cache.get(digit_key)
            if digits is None:
                digits = f_name.en_font.render(f"{ty.cace:.4f}", True, ace_tc)
                if len(_box_ace_digit_cache) >= _BOX_ACE_DIGIT_CACHE_MAX:
                    _box_ace_digit_cache.pop(next(iter(_box_ace_digit_cache)))
                _box_ace_digit_cache[digit_key] = digits
            surface.blit(digits, (22 + ace_x, 22 + ace_y))
        # 名称编辑态: 在名称行处叠加输入框
        if getattr(self, '_name_edit_ty', None) is ty:
            self._draw_name_edit(surface)

    # ── 名称行双击编辑(S0 B4) ──
    def _start_name_edit(self, ty, rect) -> None:
        from ..input_field import InputField
        self._name_edit_ty = ty
        f = InputField(rect, max_length=40, dark=getattr(self, 'dark_mode', True))
        f.set_text(ty.cust or ty.sname or '')
        f.activate()
        self._name_edit_field = f

    def _commit_name_edit(self) -> None:
        ty = getattr(self, '_name_edit_ty', None)
        f = getattr(self, '_name_edit_field', None)
        if ty is not None and f is not None:
            text = f.get_text().strip()
            if text:
                ty.cust = text
                try:
                    key = f"{ty.b}{ty.n}"
                    tn = self.cfg.tn or {}
                    tn[key] = text
                    self.cfg.tn = tn
                    self.save_config()
                except Exception:
                    pass
            self._info_box_cache_typhoon.pop(ty, None)
            self._info_box_last_data.pop(ty, None)
        self._name_edit_ty = None
        self._name_edit_field = None

    def _draw_name_edit(self, surface) -> None:
        f = getattr(self, '_name_edit_field', None)
        if f is not None:
            f.draw(surface)

    def _handle_name_edit_event(self, e) -> bool:
        f = getattr(self, '_name_edit_field', None)
        if f is None:
            return False
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_RETURN,
                                                  pygame.K_KP_ENTER):
            self._commit_name_edit()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not f.rect.collidepoint(e.pos):
                self._commit_name_edit()
                return False      # 点击输入框外 → 提交并放行给其它逻辑
        if f.handle_event(e):
            return True
        return True
