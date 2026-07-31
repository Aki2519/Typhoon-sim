# py/ty_sim_mixins/_draw_icon_mixin.py
"""台风图标 + 名称 + 信息框渲染 Mixin。"""
from __future__ import annotations
import numpy as np
import pygame
from ..typhoon import TrackPoint
from ..constants import (
    f_s, f_m, f_15, f_19, f_name, rt, TXT,
    C3, C4, C5_L, C5_M, C5_D, MD_COLOR, C2_MINUS, C3_MINUS, C4_ST, STS,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
    INFO_BOX_BG, INFO_BOX_BORDER,
    FUTURE_LINE_ALPHA,
    ICON_SET_SMCY,
)
from ..smcy_icon import get_smcy_manager, _FRAME_INTERVAL_MS, _TOTAL_FRAMES
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..utils import get_tropical_points, max_wind_from_points, display_category
from ..ace_engine import _ace_eligible

_box_font = SmartFont(_load_font(FONT_FILE, 28, 28), _load_font(FONT_FILE, 28, 28))
_name_font = SmartFont(_load_font(FONT_FILE, 28, 28), _load_font(FONT_FILE, 28, 28))
_peak_font = SmartFont(_load_font(FONT_FILE, 19, 19), _load_font(FONT_FILE, 19, 19))

_OUTLINE8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


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


def _apply_purple_filter(surf: pygame.Surface, strength: float) -> pygame.Surface:
    """按像素饱和度把彩色部分推向紫色；白/灰(低饱和)部分不受影响。

    C5 图标主色为品红 (255,0,255)，"更紫"意味着压低红色通道
    （C5_M=191,0,255 → C5_D=128,0,255），同时压绿提蓝。"""
    out = surf.copy()
    px = pygame.surfarray.pixels3d(out)
    arr = px.astype(np.float32)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    f = (mx - mn) / 255.0 * strength           # 白色 sat=0 → 不变
    arr[..., 0] *= (1.0 - 0.50 * f)                            # 压红 → 紫罗兰
    arr[..., 1] *= (1.0 - 0.55 * f)                            # 压绿
    arr[..., 2] += (255.0 - arr[..., 2]) * 0.50 * f            # 提蓝
    px[...] = np.clip(arr, 0, 255).astype(np.uint8)
    del px
    return out


class TySimDrawIconMixin:
    """台风图标、名称、信息框的绘制。"""

    _info_box_cache_typhoon: dict = {}
    _info_box_last_data: dict = {}

    _ring_scale_cache: dict = {}
    _center_scale_cache: dict = {}
    _l3_scale_cache: dict = {}

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
    def draw_typhoon_info(self, surface: pygame.Surface, ty) -> None:
        cp = ty.cp()
        if not cp:
            return
        # 拖拽期间使用与路径相同的坐标系：stale screen_points + drag_offset
        # 用 ty.cpos() 的进度在 screen_points[ci] 和 [ci+1] 之间插值，保证平滑运动
        if self.right_button_dragging and (self._drag_offset_x or self._drag_offset_y):
            screen_points = getattr(ty, 'screen_points', None)
            cur_idx = ty.ci
            if not screen_points or cur_idx < 0 or cur_idx >= len(screen_points):
                return
            if cur_idx < len(screen_points) - 1 and ty.ci < len(ty.pts) - 1:
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
        icon_factor = self.icon_size / 100.0
        if self.fix_icon_point_size:
            mv = self.map_mgr.map_view
            if mv.min_scale > 0:
                icon_factor *= mv.scale / (mv.min_scale * 2.5)
        icon_alpha = 255

        if show_icon and self.fade_typhoon:
            n_pts = len(ty.pts)
            if ty.ci >= n_pts - 2 and n_pts >= 2:
                if ty.ipos and ty.ci + 1 < n_pts:
                    total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
                    progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
                else:
                    progress = 1.0 if ty.ci >= n_pts - 1 else 0.0
                icon_alpha = max(0, int(255 * (1.0 - progress)))

        # 刚生成时的渐入效果（生成后 800ms 内 alpha 从 0 线性至 255）
        if ty.v._spawn_time:
            now = pygame.time.get_ticks()
            elapsed = now - ty.v._spawn_time
            if elapsed < 800:
                icon_alpha = icon_alpha * min(255, int(elapsed * 255 / 800)) // 255

        icon_alpha = icon_alpha * getattr(ty.v, 'icon_alpha', 255) // 255

        if show_icon and icon_alpha > 0:
            cat = cp.get('cat', self.get_strength_category(cp['w'], cp['st']))
            trans = self._get_transition(ty)

            if self.cfg.icon_set == ICON_SET_SMCY:
                if ty.v._spawn_time:
                    self._advance_smcy_frame(ty, cat, now, self.sp)
                else:
                    self._advance_smcy_frame(ty, cat, pygame.time.get_ticks(), self.sp)

                if trans:
                    old_cat, old_a, new_cat, new_a = trans
                    old_final = (icon_alpha * old_a) // 255
                    new_final = (icon_alpha * new_a) // 255
                    f = ty.v._smcy_frame
                    if old_final > 0:
                        self._draw_smcy_frame(surface, ty, old_cat, f, x, y, icon_factor, old_final, cp['w'])
                    if new_final > 0:
                        self._draw_smcy_frame(surface, ty, new_cat, f, x, y, icon_factor, new_final, cp['w'])
                else:
                    self._draw_smcy_frame(surface, ty, cat, ty.v._smcy_frame, x, y, icon_factor, icon_alpha, cp['w'])
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
        scale = min(target_size / orig_w, target_size / orig_h)
        new_w, new_h = max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        base_ring = self._get_scaled_image(ring_img, new_w, new_h, cat, self._ring_scale_cache)
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
                l3_scale = min(target_l3_size / l3_orig_w, target_l3_size / l3_orig_h)
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
    def _advance_smcy_frame(ty, cat, now, speed_factor=1.0):
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
                advance = elapsed // interval
                v._smcy_frame = (v._smcy_frame + advance) % _TOTAL_FRAMES
                v._smcy_last_ticks = now - (elapsed % interval)

    # ── 紫滤镜结果缓存：避免每帧对 C5 图标做 numpy 全图运算 ──
    _purple_frame_cache: dict = {}
    _PURPLE_FRAME_CACHE_MAX = 120

    def _draw_smcy_frame(self, surface, ty, cat, frame_idx, x, y, icon_factor, icon_alpha,
                         wind: int = 0):
        """纯绘制指定帧，按视频原始宽高比缩放。"""
        hemi = HEMISPHERE_SOUTH if ty.v.mirror else HEMISPHERE_NORTH
        mgr = get_smcy_manager()
        orig = mgr.get_size(cat, hemi) or (400, 400)
        size_mult = 3.0 if cat == 'EX' else 1.5   # EX 特殊处理：放大 1.5 倍
        target = max(20, int(70 * icon_factor * size_mult))
        scale = min(target / orig[0], target / orig[1])
        ts = (max(1, int(orig[0] * scale)), max(1, int(orig[1] * scale)))
        # 155+/170+ 紫色滤镜（不影响白色部分），结果按帧缓存
        tier = _purple_tier(wind) if cat == 'C5' else None
        if tier is not None:
            key = (cat, hemi, frame_idx, ts, tier[1])
            cache = TySimDrawIconMixin._purple_frame_cache
            frame = cache.get(key)
            if frame is None:
                raw = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
                if raw is None:
                    return
                frame = _apply_purple_filter(raw, tier[1])
                if len(cache) >= self._PURPLE_FRAME_CACHE_MAX:
                    cache.pop(next(iter(cache)))
                cache[key] = frame
        else:
            frame = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
            if frame is None:
                return
        frame.set_alpha(icon_alpha)
        rect = frame.get_rect(center=(x, y))
        surface.blit(frame, rect)
        frame.set_alpha(255)

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
            fg = _peak_font.render(label, True, (255, 255, 255))
            bk = _peak_font.render(label, True, color)
            w, h = fg.get_size()
            surf = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
            for dx, dy in _OUTLINE8:
                surf.blit(bk, (dx + 1, dy + 1))
            surf.blit(fg, (1, 1))
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
            fg = _name_font.render(name, True, (255, 255, 255))
            bk = _name_font.render(name, True, color)
            w, h = fg.get_size()
            surf = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
            for dx, dy in _OUTLINE8:
                surf.blit(bk, (dx + 1, dy + 1))
            surf.blit(fg, (1, 1))
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
            appear = max(0.0, min(1.0, (ty.at - pt[0]) / self._FADE_RAMP))
        switch_t = min(1.0, max(0.0, (ty.at - state['switch_at']) / self._FADE_RAMP))

        offset_x, offset_y = int(30 * icon_factor), int(-20 * icon_factor)
        text_x, ty_pos = x + offset_x, y + offset_y

        shadow_surf = self._get_name_surf(display_name, name_color, name_factor)
        name_alpha = int(alpha * appear * switch_t)
        self._blit_faded(surface, shadow_surf, (text_x - 1, ty_pos - 1), name_alpha)
        if switch_t < 1.0 and state['prev'] is not None:
            old_surf = self._get_name_surf(state['prev'][0], state['prev'][1], name_factor)
            old_alpha = int(alpha * appear * (1.0 - switch_t))
            self._blit_faded(surface, old_surf, (text_x - 1, ty_pos - 1), old_alpha)

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
                self._blit_faded(surface, surf_pk, (text_x - 1, py_pos), pk_alpha)
                py_pos += surf_pk.get_height() - 2

    # ── 信息框 ──
    def _draw_info_box(self, surface: pygame.Surface, ty, point: TrackPoint) -> None:
        dark = getattr(self, 'dark_mode', True)
        key_data = (
            ty.b, ty.n, ty.cust, ty.sname, ty.start_time, point['t'],
            point['la'], point['lo'], point['w'], point['p'], point['st'],
            ty.tace, self.name_display_mode, dark
        )
        if ty in self._info_box_cache_typhoon and self._info_box_last_data.get(ty) == key_data:
            box, ace_x, ace_y, ace_tc = self._info_box_cache_typhoon[ty]
        else:
            ifs, ifm = f_name, _box_font

            if dark:
                box_bg = (22, 28, 44, 220)
                box_border = (55, 85, 130)
                tc = (215, 225, 245)
                off_ok = (90, 210, 120)
                off_no = (235, 110, 110)
            else:
                box_bg = INFO_BOX_BG
                box_border = INFO_BOX_BORDER
                tc = TXT
                off_ok = (0, 150, 0)
                off_no = (150, 0, 0)

            box_w, box_h = 375, 390
            bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            pygame.draw.rect(bg, box_bg, (0, 0, box_w, box_h), 0, 15)
            pygame.draw.rect(bg, box_border, (0, 0, box_w, box_h), 2, 15)

            y = 15
            max_w = box_w - 30

            # ── 第1行：台风标签 ──
            label_surf = rt(ifs, "台风:", tc, max_w)
            bg.blit(label_surf, (15, y))
            y += label_surf.get_height() + 3

            # ── 第2行：台风名称（粗体，始终独立一行） ──
            if self.name_display_mode == 0:
                start_year = ty.pts[0]['t'][:4] if ty.pts else "????"
                base_name = f"{ty.basin}{ty.n}" if ty.basin else ty.n
                if ty.sname:
                    display_name = f"{start_year} {base_name} ({ty.sname})"
                elif ty.cust:
                    display_name = f"{start_year} {ty.cust}"
                else:
                    display_name = f"{start_year} {base_name}"
            else:
                display_name = self.get_display_name(ty)
            name_surf = rt(ifm, display_name, tc, max_w)
            bg.blit(name_surf, (15, y))
            y += name_surf.get_height() + 6

            # ── 后续行 ──
            time_surf = rt(ifs, f"时间: {point['t']}", tc, max_w)
            bg.blit(time_surf, (15, y)); y += time_surf.get_height() + 3

            la = point['la']
            lo = point['lo']
            lat_dir = 'N' if la >= 0 else 'S'
            lat_val = abs(la)
            if lo > 180.0:
                lon_disp = 360.0 - lo
                lon_dir = 'W'
            elif lo < -180.0:
                lon_disp = lo + 360.0
                lon_dir = 'W'
            elif lo < 0:
                lon_disp = -lo
                lon_dir = 'W'
            else:
                lon_disp = lo
                lon_dir = 'E'
            pos_surf = rt(ifs, f"位置: {lat_val:.1f}°{lat_dir}, {lon_disp:.1f}°{lon_dir}", tc, max_w)
            bg.blit(pos_surf, (15, y)); y += pos_surf.get_height() + 3

            st = point['st'].upper()
            if st in ('EX', 'MD', 'SS', 'SD', 'LO', 'DB'):
                wind_surf = rt(ifs, f"风速: {point['w']} kt  性质: {st}", tc, max_w)
            else:
                wind_surf = rt(ifs, f"风速: {point['w']} kt", tc, max_w)
            bg.blit(wind_surf, (15, y)); y += wind_surf.get_height() + 3

            pres_str = f"气压: {point['p']} hPa" if point['p'] != 0 else "气压: 未知"
            pres_surf = rt(ifs, pres_str, tc, max_w)
            bg.blit(pres_surf, (15, y)); y += pres_surf.get_height() + 3

            cat = point.get('cat', self.get_strength_category(point['w'], point['st']))
            cat_surf = rt(ifs, f"等级: {display_category(cat)}", tc, max_w)
            bg.blit(cat_surf, (15, y)); y += cat_surf.get_height() + 3

            off_text = "正式报" if point.get('official', True) else "非正式报"
            off_color = off_ok if point.get('official', True) else off_no
            off_surf = rt(ifs, f"报别: {off_text}", off_color, max_w)
            bg.blit(off_surf, (15, y)); y += off_surf.get_height() + 3

            ace_total = rt(ifs, f"总ACE: {ty.tace:.4f}", tc, max_w)
            bg.blit(ace_total, (15, y)); y += ace_total.get_height() + 3
            ace_prefix = rt(ifs, "实时ACE: ", tc, max_w)
            bg.blit(ace_prefix, (15, y))
            ace_x, ace_y, ace_tc = 15 + ace_prefix.get_width(), y, tc
            y += ace_prefix.get_height() + 3

            max_wind = max_wind_from_points(ty.pts)
            peak_surf = rt(ifs, f"巅峰: {max_wind} kt", tc, max_w)
            bg.blit(peak_surf, (15, y))

            self._info_box_cache_typhoon[ty] = (bg, ace_x, ace_y, ace_tc)
            self._info_box_last_data[ty] = key_data
            box = bg

        surface.blit(box, (22, 22))
        # 实时ACE数字每帧单独绘制（ASCII，底层字体绕过 SmartFont 缓存）
        digits = f_name.en_font.render(f"{ty.cace:.4f}", True, ace_tc)
        surface.blit(digits, (22 + ace_x, 22 + ace_y))
