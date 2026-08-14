# py/ty_sim_mixins/_draw_path_mixin.py
"""台风路径渲染 Mixin：安全连线、路径缓存、增量绘制。"""
from __future__ import annotations
import math
import os
import pygame
from collections import OrderedDict
from ..constants import (
    PATH, CUR_POS,
    FUTURE_LINE_ALPHA, FADE_DURATION, FADE_DURATION_QUICK,
    f_s, rt,
    SUCAI_DIR, ICON_SET_SMCY,
)
from ..landfall_effect import landfall_marker_name
from ..spline import compute_arc_lengths

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
        while pos < length:
            seg_end = min(pos + dash, length)
            x1 = int(start[0] + ux * pos)
            y1 = int(start[1] + uy * pos)
            x2 = int(start[0] + ux * seg_end)
            y2 = int(start[1] + uy * seg_end)
            pygame.draw.line(surface, color, (x1, y1), (x2, y2), width)
            pos = seg_end + gap

    @staticmethod
    def _draw_lines_safe(surface, color, points, width, max_seg_len):
        """Draw connected lines, breaking at segments that exceed max_seg_len.
        防止两连续屏幕点落在地图投影折返两侧时产生横跨全屏的伪线。
        """
        if len(points) < 2:
            return
        max_seg_sq = max_seg_len * max_seg_len
        seg_start = 0
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > max_seg_sq:
                if i - seg_start >= 2:
                    pygame.draw.lines(surface, color, False, points[seg_start:i], width)
                seg_start = i
        if len(points) - seg_start >= 2:
            pygame.draw.lines(surface, color, False, points[seg_start:], width)

    @staticmethod
    def _draw_lines_colored(surface, points, point_colors, width, max_seg_len, point_types=None):
        """Draw colored line segments. point_types: list of storm type strings,
        EX/SS/SD segments are drawn dashed."""
        if len(points) < 2:
            return
        max_seg_sq = max_seg_len * max_seg_len
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy <= max_seg_sq:
                c = point_colors[i - 1]
                if point_types and point_types[i - 1] in ('EX', 'SS', 'SD'):
                    TySimDrawPathMixin._draw_dashed(surface, c, (x1, y1), (x2, y2), width)
                else:
                    pygame.draw.line(surface, c, (x1, y1), (x2, y2), width)

    _circle_cache: OrderedDict = OrderedDict()
    _MAX_CIRCLE_CACHE = 512

    _path_render_view_version = 0       # 视图版本号，视图变化时递增

    def _get_circle_marker(self, radius, color):
        key = (radius, color)
        cache = self._circle_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, color, (radius, radius), radius)
        cache[key] = surf
        if len(cache) > self._MAX_CIRCLE_CACHE:
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
        key = (tri_w, tri_h, color)
        cache = self._tri_marker_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((tri_w * 2, tri_h * 2), pygame.SRCALPHA)
        tri = [(tri_w, 0), (0, tri_h * 2), (tri_w * 2, tri_h * 2)]
        pygame.draw.polygon(surf, color, tri)
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

    def should_draw_typhoon(self, ty) -> bool:
        if self.md == self.MODE_NORMAL:
            if ty == self.current_typhoon():
                return True
            if self.fade_path and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if ct - ty.finish_time < _FADE_MS:
                    return True
            return False
        elif self.md == self.MODE_SEASON:
            if ty.act and ty.ss and not ty.sf:
                return True
            if self.fade_path and ty.sf and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if ct - ty.finish_time < _FADE_MS:
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

    def _draw_typhoons(self, surface):
        current_ty = self.current_typhoon()
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self.draw_typhoon(surface, self.edit_typhoon, highlight=True)
        else:
            for ty in self.tys:
                if self.should_draw_typhoon(ty):
                    if not self._is_typhoon_visible(ty):
                        continue
                    self.draw_typhoon(surface, ty, highlight=(ty == current_ty))

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
        """是否绘制未经过的路径（编辑模式始终显示）。"""
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

        key = self._make_path_cache_key(ty, screen_points, highlight)

        # ── 缓存失效：重建 full + traversed ──
        if ty._path_cache_key != key:
            # 计算 bbox（仅重建时；命中/追加分支直接取缓存的偏移，法1）
            margin = radius + 4
            xs = [p[0] for p in screen_points]
            ys = [p[1] for p in screen_points]
            if self.smooth_path and len(ty.v.smooth_screen_points) >= 2:
                xs = xs + [p[0] for p in ty.v.smooth_screen_points]
                ys = ys + [p[1] for p in ty.v.smooth_screen_points]
            bbox_x = max(0, min(xs) - margin)
            bbox_y = max(0, min(ys) - margin)
            bbox_w = min(self.screen_width - bbox_x, max(xs) - bbox_x + margin * 2)
            bbox_h = min(self.map_height - bbox_y, max(ys) - bbox_y + margin * 2)
            if bbox_w <= 0 or bbox_h <= 0:
                # K29: 记录空缓存键,避免屏幕外台风每帧重建 bbox。
                # 同时建立空 full/traversed 面,保证后续帧缓存命中走 3 元组返回,
                # 不落入 (None, None) 导致 draw_typhoon 里 set_alpha 崩溃。
                ty._path_cache_key = key
                ty._last_rendered_ci = cur_idx
                ty._path_cache_blit = (0, 0)
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

            smooth_pts = (ty.v.smooth_screen_points
                          if self.smooth_path else None)
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

            smooth_sp = (ty.v.smooth_screen_points
                         if self.smooth_path else None)
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


        # ── 增量追加：ci 前进时在 traversed 上追加新线段 ──
        elif cur_idx > ty._last_rendered_ci:
            bbox_x, bbox_y = ty._path_cache_blit
            line_mode = self._line_mode()
            line_width = 4 if line_mode else 2
            segs = max(1, self.smooth_path_segments)
            traversed = ty._path_cache_traversed
            smooth_sp = (ty.v.smooth_screen_points
                          if self.smooth_path else None)
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
            smooth_sp = (ty.v.smooth_screen_points
                          if self.smooth_path else None)
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

        blit_pos = getattr(ty, '_path_cache_blit', (0, 0))
        return ty._path_cache_full, ty._path_cache_traversed, blit_pos

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
        outer = self._get_circle_marker(highlight_r, CUR_POS)
        inner = self._get_circle_marker(radius, point_color)
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
    def draw_typhoon(self, surface, ty, highlight):
        if not ty.pts:
            return

        screen_points = getattr(ty, 'screen_points', None)
        sp_mismatch = not screen_points or len(screen_points) != len(ty.pts)
        sp_stale = sp_mismatch or ty.v._sp_ver != getattr(self, '_sp_version', 0)
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
                ty.v._sp_ver = getattr(self, '_sp_version', 0)
                self._clear_drag_cache(ty)
                screen_points = ty.screen_points
                if not screen_points:
                    return
            else:
                # 惰性刷新：只对进入绘制的台风重算屏幕坐标；
                # 缩放突发期内同样重建平滑样条,避免缩放时路径退化为折线
                smooth_rect = pygame.Rect(-50, -50, self.screen_width + 100,
                                          self.map_height + 100)
                ty.update_screen_points(self.latlon_to_screen, smooth_rect)
                ty.v._sp_ver = getattr(self, '_sp_version', 0)
                screen_points = ty.screen_points
                if not screen_points:
                    return

        path_alpha = 255
        mode = getattr(self.cfg, 'fade_path_mode', 'fade')
        if mode != 'never' and ty.finish_time > 0:
            ct = pygame.time.get_ticks()
            elapsed = (ct - ty.finish_time) * 0.001
            dur = FADE_DURATION_QUICK if mode == 'quick' else FADE_DURATION
            if elapsed >= dur:
                path_alpha = 0
            else:
                path_alpha = max(0, int(255 * (1.0 - elapsed * (_INV_FADE_QUICK if mode == 'quick' else _INV_FADE))))

        if path_alpha <= 0 and mode != 'never':
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
        else:
            path_surf, path_blit = result
            path_surf.set_alpha(path_alpha)
            surface.blit(path_surf, (path_blit[0] + self._drag_offset_x, path_blit[1] + self._drag_offset_y))

        # ── 实时位置（live segment + highlight dot 共用）──
        live_pos = ty.cpos()
        live_xy = self.latlon_to_screen(live_pos['la'], live_pos['lo']) if live_pos else None

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
                    pygame.draw.circle(surface, CUR_POS, live_xy, 2)
            else:
                cur_idx = ty.ci
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
        key = (id(ty.pts), len(ty.pts))
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
        pygame.draw.circle(surface, (255, 90, 90), (px, py), r, 2)
        pygame.draw.circle(surface, (255, 150, 150), (px, py), r + pulse, 1)
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
                # 选中: 白/黄圆环 + 时间标签
                pygame.draw.circle(surface, (255, 220, 120), (x, y), ring_r + 2, 2)
                pygame.draw.circle(surface, (255, 255, 255), (x, y), ring_r, 2)
                t = p.get('t', '')
                if len(t) >= 10:
                    from ..utils import fmt_short_time
                    txt = fmt_short_time(t)
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
                    from ..utils import fmt_short_time
                    txt = f"{i + 1} {fmt_short_time(t)}"
                    ts = rt(f_s, txt, (235, 235, 245))
                    tb = rt(f_s, txt, (0, 0, 0))
                    self._blit_outlined_text(
                        surface, ts, tb,
                        x - ts.get_width() // 2,
                        y + radius + 3)
