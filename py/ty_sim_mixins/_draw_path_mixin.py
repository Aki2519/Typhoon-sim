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
from ..spline import build_spline, compute_arc_lengths

# ── 登陆点标记 png（按尺寸缓存） ──

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
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
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
    def should_draw_typhoon(self, ty) -> bool:
        if self.md == self.MODE_NORMAL:
            if ty == self.current_typhoon():
                return True
            if self.fade_path and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if (ct - ty.finish_time) / 1000.0 < FADE_DURATION:
                    return True
            return False
        elif self.md == self.MODE_SEASON:
            if ty.act and ty.ss and not ty.sf:
                return True
            if self.fade_path and ty.sf and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if (ct - ty.finish_time) / 1000.0 < FADE_DURATION:
                    return True
            return False
        elif self.md == self.MODE_EDIT:
            if ty == self.edit_typhoon:
                if self.fade_path and ty.finish_time > 0:
                    ct = pygame.time.get_ticks()
                    if (ct - ty.finish_time) / 1000.0 < FADE_DURATION:
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
        # 屏幕坐标过期（惰性刷新）：保守可见，draw_typhoon 刷新后下一帧再精确剔除
        if ty.v._sp_ver != getattr(self, '_sp_version', 0):
            return True
        margin = 20
        map_rect = pygame.Rect(-margin, -margin,
                               self.screen_width + margin * 2,
                               self.map_height + margin * 2)
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
        point_radius_factor = self.point_size / 100.0
        if self.fix_icon_point_size:
            mv = self.map_mgr.map_view
            if mv.min_scale > 0:
                point_radius_factor *= mv.scale / (mv.min_scale * 2.5)
        base_radius = 3
        radius = int(base_radius * point_radius_factor)
        if radius < 1:
            radius = 1
        max_seg = self._path_max_seg_len
        n_pts = len(ty.pts)
        cur_idx = ty.ci
        if not screen_points:
            return pygame.Surface((1, 1), pygame.SRCALPHA), (0, 0)

        # ── 拖拽中：渲染到独立 bbox Surface（不受屏幕裁剪）。
        #    必须在下方"裁剪到屏幕"的 bbox 计算之前处理，
        #    否则拖拽前在屏幕外的路径会因 bbox_w/h <= 0 提前返回而不渲染 ──
        if self._drag_offset_x or self._drag_offset_y:
            return self._render_drag_path_surface(
                ty, screen_points, highlight, radius, point_radius_factor,
                max_seg, cur_idx, n_pts)

        key = self._make_path_cache_key(ty, screen_points, highlight)

        # 计算 bbox（含点标记边距）
        margin = radius + 4
        xs = [p[0] for p in screen_points]
        ys = [p[1] for p in screen_points]
        bbox_x = max(0, min(xs) - margin)
        bbox_y = max(0, min(ys) - margin)
        bbox_w = min(self.screen_width - bbox_x, max(xs) - bbox_x + margin * 2)
        bbox_h = min(self.map_height - bbox_y, max(ys) - bbox_y + margin * 2)
        if bbox_w <= 0 or bbox_h <= 0:
            return pygame.Surface((1, 1), pygame.SRCALPHA), (0, 0)

        # ── 缓存失效：重建 full + traversed ──
        if ty._path_cache_key != key:
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

    def _render_drag_path_surface(self, ty, screen_points, highlight,
                                  radius, point_radius_factor, max_seg,
                                  cur_idx, n_pts):
        """拖拽期间的整路径渲染（旧视图坐标系，blit 时统一加 drag_offset）。
        Surface 覆盖整条路径 bbox（不裁剪到屏幕），平滑样条可用时优先使用。"""
        ox, oy = self._drag_offset_x, self._drag_offset_y
        segs = max(1, self.smooth_path_segments)
        smooth_sp = (ty.v.smooth_screen_points
                     if self.smooth_path and len(ty.v.smooth_screen_points) >= 2
                     else None)
        if self.smooth_path and smooth_sp is None:
            # 拖动中平滑点缺失 → 用 geo 坐标即时重建（保留在旧视图坐标系）
            mode = getattr(self.cfg, 'smooth_path_mode', 'monotone')
            geo_pts = [(p['lo'], p['la']) for p in ty.pts]
            smooth_geo = build_spline(geo_pts, segs, mode)
            f = self.latlon_to_screen
            smooth_sc = [(f(lat, lon)[0] - ox, f(lat, lon)[1] - oy) for lon, lat in smooth_geo]
            ty.v.smooth_screen_points = smooth_sc
            ty.v._smooth_arc_lengths = compute_arc_lengths(smooth_sc)
            smooth_sp = smooth_sc
        line_pts = smooth_sp if smooth_sp else screen_points
        line_segs = segs if smooth_sp else 1

        xs = [p[0] for p in screen_points]
        ys = [p[1] for p in screen_points]
        if line_pts is not screen_points:
            xs = xs + [p[0] for p in line_pts]
            ys = ys + [p[1] for p in line_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        pad = 30
        box_w = max(4, max_x - min_x + pad * 2)
        box_h = max(4, max_y - min_y + pad * 2)

        need_clip = box_w > self._DRAG_SURF_MAX or box_h > self._DRAG_SURF_MAX
        drag_extra = (self.point_size, segs,
                      getattr(self, 'path_mode', 'markers'),
                      getattr(self, 'show_future_path', True))
        if need_clip:
            # 超大路径：只渲染可视区附近，偏移每移动 512px 重建一次
            drag_key = (cur_idx, highlight, n_pts, ox // 512, oy // 512, *drag_extra)
        else:
            drag_key = (cur_idx, highlight, n_pts, *drag_extra)

        if ty._path_cache_drag_key == drag_key and ty._path_cache_drag_surf is not None:
            return ty._path_cache_drag_surf, ty._path_cache_drag_pos

        if need_clip:
            vis_pad = 600
            vis = pygame.Rect(-ox - vis_pad, -oy - vis_pad,
                              self.screen_width + vis_pad * 2,
                              self.map_height + vis_pad * 2)
            clipped = pygame.Rect(min_x - pad, min_y - pad, box_w, box_h).clip(vis)
            if clipped.width <= 0 or clipped.height <= 0:
                surf = pygame.Surface((1, 1), pygame.SRCALPHA)
                ty._path_cache_drag_surf = surf
                ty._path_cache_drag_key = drag_key
                ty._path_cache_drag_pos = (0, 0)
                return surf, (0, 0)
            origin_x, origin_y = clipped.x, clipped.y
            box_w, box_h = clipped.width, clipped.height
        else:
            origin_x, origin_y = min_x - pad, min_y - pad

        line_mode = self._line_mode()
        line_width = 4 if line_mode else 2
        bbox_surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)

        def _local(pt):
            return (pt[0] - origin_x, pt[1] - origin_y)

        local_line = [_local(p) for p in line_pts]
        local_marks = [_local(p) for p in screen_points]
        show_future = self._show_future(highlight)
        f_alpha_for_full = FUTURE_LINE_ALPHA
        if highlight and self.md == self.MODE_EDIT:
            f_alpha_for_full = 255
        if show_future:
            if line_mode:
                colors, types = self._build_line_colors(ty.pts, line_pts, dim=True, segs=line_segs)
                self._draw_lines_colored(bbox_surf, local_line, colors, line_width, max_seg, point_types=types)
            else:
                self._draw_lines_safe(bbox_surf, (*PATH, f_alpha_for_full), local_line, 2, max_seg)

        # 已走段（不透明）
        if cur_idx > 0 and len(local_line) > 1:
            end_idx = (min(cur_idx * line_segs, len(local_line) - 1)
                       if smooth_sp else min(cur_idx, len(local_line) - 1))
            passed_local = local_line[:end_idx + 1]
            passed_src = line_pts[:end_idx + 1]
            if len(passed_local) > 1:
                if line_mode:
                    colors, types = self._build_line_colors(ty.pts[:cur_idx + 1], passed_src, dim=False, segs=line_segs)
                    self._draw_lines_colored(bbox_surf, passed_local, colors, line_width, max_seg, point_types=types)
                else:
                    self._draw_lines_safe(bbox_surf, PATH, passed_local, 2, max_seg)

        if not line_mode:
            for i, (p, lpt) in enumerate(zip(ty.pts, local_marks)):
                is_future = i > cur_idx
                if is_future and not show_future:
                    continue
                lx, ly = lpt
                point_color = p['color'] if highlight else p['color_dim']
                cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                marker, offset_x, offset_y = self._make_point_marker(
                    point_color, cat, p, radius, point_radius_factor,
                    highlight, is_future=is_future)
                bbox_surf.blit(marker, (lx - offset_x, ly - offset_y))

        if 0 <= cur_idx < n_pts and highlight and not line_mode:
            lx, ly = local_marks[cur_idx]
            highlight_r = radius + int(2 * point_radius_factor)
            outer_surf = self._get_circle_marker(highlight_r, CUR_POS)
            pc_ci = ty.pts[cur_idx]['color'] if highlight else ty.pts[cur_idx]['color_dim']
            inner_surf = self._get_circle_marker(radius, pc_ci)
            bbox_surf.blit(outer_surf, (lx - highlight_r, ly - highlight_r))
            bbox_surf.blit(inner_surf, (lx - radius, ly - radius))

        ty._path_cache_drag_surf = bbox_surf
        ty._path_cache_drag_key = drag_key
        ty._path_cache_drag_pos = (origin_x, origin_y)
        return bbox_surf, (origin_x, origin_y)

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
        ty._path_cache_drag_surf = None
        ty._path_cache_drag_key = ()
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
                if sp_mismatch:
                    # 拖拽中出现新台风/新增报点：无法获得"旧视图"坐标，
                    # 用当前视图坐标减去拖拽偏移换算到旧坐标系，
                    # 使其与其他 stale+offset 内容在同一坐标系（blit 时统一加偏移）
                    ox, oy = self._drag_offset_x, self._drag_offset_y
                    f = self.latlon_to_screen

                    def _stale_equiv(la, lo):
                        x, y = f(la, lo)
                        return x - ox, y - oy

                    ty.update_screen_points(_stale_equiv)
                    screen_points = ty.screen_points
                    if not screen_points:
                        return
                # 仅版本过期：拖拽中沿用旧坐标系（拖拽结束统一强制刷新）
            else:
                # 惰性刷新：只对进入绘制的台风重算屏幕坐标；
                # 缩放突发期内跳过平滑样条（哨兵矩形永不与 bbox 相交；
                # 注意 Rect(0,0,0,0) 为假值会绕过 view_rect 判断，不能用）
                if pygame.time.get_ticks() < getattr(self, '_zoom_burst_until', 0):
                    if self.md == self.MODE_EDIT and ty is self.edit_typhoon:
                        smooth_rect = pygame.Rect(-50, -50, self.screen_width + 100,
                                                  self.map_height + 100)
                    else:
                        smooth_rect = pygame.Rect(-10 ** 6, -10 ** 6, 1, 1)
                else:
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
            elapsed = (ct - ty.finish_time) / 1000.0
            dur = FADE_DURATION_QUICK if mode == 'quick' else FADE_DURATION
            if elapsed >= dur:
                path_alpha = 0
            else:
                path_alpha = max(0, int(255 * (1.0 - elapsed / dur)))

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
                    point_radius_factor = self.point_size / 100.0
                    if self.fix_icon_point_size:
                        mv = self.map_mgr.map_view
                        if mv.min_scale > 0:
                            point_radius_factor *= mv.scale / (mv.min_scale * 2.5)
                    radius = max(1, int(3 * point_radius_factor))
                    highlight_r = radius + int(2 * point_radius_factor)
                    self._blit_highlight(surface, x, y, radius, highlight_r, point_color, self._drag_offset_x, self._drag_offset_y)

        # ── 编辑模式拖动指示：红色圆环 + 实时坐标 ──
        if (highlight and self.md == self.MODE_EDIT and self.dragging_point
                and ty is self.drag_typhoon
                and 0 <= self.drag_point_index < len(screen_points)):
            self._draw_drag_indicator(surface, ty, screen_points)

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
        if path_alpha < 255 and len(color) == 3:
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
        """在拖动点周围渲染一小块 0.1° 网格，向边缘渐变消失。"""
        R = self._GRID_RADIUS
        # 0.1° 对应的像素步长
        x0, y0 = self.latlon_to_screen(la0, lo0)
        x1, _ = self.latlon_to_screen(la0, lo0 + 0.1)
        _, y1 = self.latlon_to_screen(la0 + 0.1, lo0)
        step_x = abs(x1 - x0)
        step_y = abs(y1 - y0)
        if step_x < 6 or step_y < 6:
            return  # 网格过密不渲染
        grid = pygame.Surface((R * 2, R * 2), pygame.SRCALPHA)
        color = (255, 255, 255, 40)
        half_range_x = (R / step_x + 1) * 0.1
        half_range_y = (R / step_y + 1) * 0.1
        # 竖线：经度 0.1 的整数倍
        k0 = math.floor((lo0 - half_range_x) * 10)
        k1 = math.ceil((lo0 + half_range_x) * 10)
        for k in range(k0, k1 + 1):
            gx, _ = self.latlon_to_screen(la0, k / 10.0)
            lx = gx - px + R
            if 0 <= lx <= R * 2:
                pygame.draw.line(grid, color, (lx, 0), (lx, R * 2), 1)
        # 横线：纬度 0.1 的整数倍
        m0 = math.floor((la0 - half_range_y) * 10)
        m1 = math.ceil((la0 + half_range_y) * 10)
        for m in range(m0, m1 + 1):
            _, gy = self.latlon_to_screen(m / 10.0, lo0)
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

    def _draw_landfall_markers(self, surface, ty, path_alpha, highlight=True):
        """在每个登陆点绘制 landfall_X.png：未登陆半透明，登陆后不透明并随路径淡出。
        关闭"显示未经过的路径"时，未越过的登陆点不绘制。"""
        recs = self._get_precomputed_landfalls(ty)
        if not recs:
            return
        show_future = self._show_future(highlight)
        prf = self.point_size / 100.0
        if self.fix_icon_point_size:
            mv = self.map_mgr.map_view
            if mv.min_scale > 0:
                prf *= mv.scale / (mv.min_scale * 2.5)
        msize = max(5, int(10 * prf))

        # 当前段内进度（用于判断段中登陆点是否已越过）
        ci = ty.ci
        seg_t = 0.0
        pt_list = ty.points_time
        if ty.v.ipos and 0 <= ci < len(pt_list) - 1 and pt_list[ci + 1] > pt_list[ci]:
            seg_t = max(0.0, min(1.0, (ty.at - pt_list[ci]) / (pt_list[ci + 1] - pt_list[ci])))
        elif ci >= len(ty.pts) - 1:
            seg_t = 1.0

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
                img = img.copy()
                img.set_alpha(alpha)
            surface.blit(img, img.get_rect(center=(mx_, my_)))

    def _draw_drag_indicator(self, surface, ty, screen_points):
        px, py = screen_points[self.drag_point_index]
        p = ty.pts[self.drag_point_index]
        self._draw_snap_grid(surface, px, py, p['la'], p['lo'])
        point_radius_factor = self.point_size / 100.0
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
