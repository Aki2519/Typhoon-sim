# py/ty_sim_mixins/draw_typhoon_mixin.py
"""台风渲染 Mixin：路径、图标、名称、信息框。"""
from __future__ import annotations
import math
import pygame
from collections import OrderedDict
from ..typhoon import TrackPoint
from ..constants import (
    f_s, f_m, f_15, f_19, f_name, rt, TXT, PATH, CUR_POS,
    DB, EX, TD, TS, STS, C1, C2, C3, C4, C5_L, C5_D, MD_COLOR, C2_LIGHT, C2_DARK, C2_MINUS, C3_MINUS, C4_ST, WV,
    SPEC,
    HEMISPHERE_NORTH,
    INFO_BOX_BG, INFO_BOX_BORDER,
    FUTURE_LINE_ALPHA,
)


class TySimDrawTyphoonMixin:
    """台风路径、图标、名称、信息框的绘制。"""

    @staticmethod
    def _draw_lines_safe(surface, color, points, width, max_seg_len):
        """Draw connected lines, breaking at segments that exceed max_seg_len.

        This prevents off-screen typhoons from producing lines that cross the
        entire screen when two consecutive screen points land on opposite sides
        of the viewport (e.g. map-projection wrapping artefacts).
        """
        if len(points) < 2:
            return
        seg_start = 0
        for i in range(1, len(points)):
            x1, y1 = points[i - 1]
            x2, y2 = points[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > max_seg_len * max_seg_len:
                if i - seg_start >= 2:
                    pygame.draw.lines(surface, color, False, points[seg_start:i], width)
                seg_start = i
        if len(points) - seg_start >= 2:
            pygame.draw.lines(surface, color, False, points[seg_start:], width)

    _circle_cache: OrderedDict = OrderedDict()
    _MAX_CIRCLE_CACHE = 512

    _info_box_cache_typhoon: dict = {}
    _info_box_last_data: dict = {}

    # ── 路径增量渲染缓存 ──
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
                if (ct - ty.finish_time) / 1000.0 < 30.0:
                    return True
            return False
        elif self.md == self.MODE_SEASON:
            if ty.act and ty.ss and not ty.sf:
                return True
            if self.fade_path and ty.sf and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if (ct - ty.finish_time) / 1000.0 < 30.0:
                    return True
            return False
        elif self.md == self.MODE_EDIT:
            if ty == self.edit_typhoon:
                return True
            if self.fade_path and ty == self.edit_typhoon and ty.finish_time > 0:
                ct = pygame.time.get_ticks()
                if (ct - ty.finish_time) / 1000.0 < 30.0:
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
        # 拖拽时路径 blit 会加上 drag_offset，
        # 把可见矩形向反方向偏移，等效于 bbox 加偏移
        if self._drag_offset_x or self._drag_offset_y:
            map_rect.x -= self._drag_offset_x
            map_rect.y -= self._drag_offset_y
        return map_rect.colliderect(bbox)

    def _draw_typhoons(self, surface):
        cty = self.current_typhoon()
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self.draw_typhoon(surface, self.edit_typhoon, highlight=True)
        else:
            for ty in self.tys:
                if self.should_draw_typhoon(ty):
                    if not self._is_typhoon_visible(ty):
                        continue
                    self.draw_typhoon(surface, ty, highlight=(ty == cty))

    # ── 增量路径渲染缓存（per-typhoon）──

    def _make_path_cache_key(self, ty, screen_points, path_alpha, highlight):
        """生成路径缓存的键。首尾屏幕坐标作为视图指纹。"""
        sp_first = screen_points[0] if screen_points else (0, 0)
        sp_last = screen_points[-1] if screen_points else (0, 0)
        return (path_alpha, highlight, self.point_size,
                self._path_render_view_version,
                sp_first, sp_last, len(screen_points),
                len(ty.pts), id(ty.pts),  # 点集身份指纹（undo 会生成新 list）
                self.md == self.MODE_EDIT and highlight)

    def _make_point_marker(self, pc, cat, p, r, point_radius_factor, opacity,
                           highlight, is_future):
        """创建一个点标记 Surface（圆/三角/矩形），返回 (surf, offset_x, offset_y)。"""
        if not p.get('official', True):
            size = max(2, int(2 * point_radius_factor))
            return self._get_rect_marker(size, pc), size // 2, size // 2
        elif cat == "EX":
            tri_h = int(3 * point_radius_factor)
            tri_w = int(3 * point_radius_factor)
            return self._get_tri_marker(tri_w, tri_h, pc), tri_w, tri_h
        else:
            if is_future and not (highlight and self.md == self.MODE_EDIT):
                alpha_pc = (*pc[:3], FUTURE_LINE_ALPHA) if len(pc) == 3 else pc
                return self._get_circle_marker(r, alpha_pc), r, r
            else:
                return self._get_circle_marker(r, pc), r, r

    def _render_path_to_surface(self, ty, screen_points, path_alpha, highlight):
        """增量渲染：cached_full（半透明全路径）+ cached_traversed（不透明已走段）。"""
        point_radius_factor = self.point_size / 100.0
        base_radius = 3
        r = int(base_radius * point_radius_factor)
        if r < 1:
            r = 1
        max_seg = max(self.screen_width, self.map_height)
        n = len(ty.pts)
        ci = ty.ci
        key = self._make_path_cache_key(ty, screen_points, path_alpha, highlight)

        # ── 缓存失效：重建 full + traversed ──
        if ty._path_cache_key != key:
            # full: 完整路径（半透明）
            ty._path_cache_full = pygame.Surface(
                (self.screen_width, self.map_height), pygame.SRCALPHA)
            full = ty._path_cache_full

            # 全路径线条（半透明 future alpha）
            f_alpha_for_full = FUTURE_LINE_ALPHA
            if path_alpha < 255:
                f_alpha_for_full = min(FUTURE_LINE_ALPHA, path_alpha)
            if highlight and self.md == self.MODE_EDIT:
                f_alpha_for_full = path_alpha if path_alpha < 255 else 255
            line_color = (*PATH, f_alpha_for_full)
            # 使用光滑路径点绘制路径线（如果可用）
            smooth_pts = (getattr(ty.v, 'smooth_screen_points', None)
                          if getattr(self, 'smooth_path', False) else None)
            draw_pts = smooth_pts if smooth_pts else screen_points
            self._draw_lines_safe(full, line_color, draw_pts, 2, max_seg)

            # 全点标记（半透明）
            for i, (p, (x, y)) in enumerate(zip(ty.pts, screen_points)):
                pc = p['color'] if highlight else p['color_dim']
                if path_alpha < 255 and len(pc) == 3:
                    pc = (*pc, path_alpha)
                cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                mk, ox, oy = self._make_point_marker(
                    pc, cat, p, r, point_radius_factor, 255,
                    highlight, is_future=True)
                full.blit(mk, (x - ox, y - oy))

            # traversed: 从头构建已走段（不透明）
            ty._path_cache_traversed = pygame.Surface(
                (self.screen_width, self.map_height), pygame.SRCALPHA)
            trav = ty._path_cache_traversed

            smooth_sp = (getattr(ty.v, 'smooth_screen_points', None)
                         if getattr(self, 'smooth_path', False) else None)
            if ci > 0:
                if smooth_sp:
                    segs = max(1, getattr(self, 'smooth_path_segments', 10))
                    end_idx = min(ci * segs, len(smooth_sp) - 1)
                    passed_line = smooth_sp[:end_idx + 1]
                else:
                    passed_line = screen_points[:ci + 1] if len(screen_points) > 1 else screen_points
                line_color = (*PATH, path_alpha) if path_alpha < 255 else PATH
                self._draw_lines_safe(trav, line_color, passed_line, 2, max_seg)

            for i in range(ci):  # 已走过的点 (0 .. ci-1)
                p = ty.pts[i]
                x, y = screen_points[i]
                pc = p['color'] if highlight else p['color_dim']
                if path_alpha < 255 and len(pc) == 3:
                    pc = (*pc, path_alpha)
                cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                mk, ox, oy = self._make_point_marker(
                    pc, cat, p, r, point_radius_factor, path_alpha,
                    highlight, is_future=False)
                trav.blit(mk, (x - ox, y - oy))

            ty._last_rendered_ci = ci
            ty._path_cache_key = key


        # ── 增量追加：ci 前进时在 traversed 上追加新线段 + 新点标记 ──
        elif ci > ty._last_rendered_ci:
            trav = ty._path_cache_traversed
            smooth_sp = (getattr(ty.v, 'smooth_screen_points', None)
                         if getattr(self, 'smooth_path', False) else None)
            segs = max(1, getattr(self, 'smooth_path_segments', 10))
            # 追加线段：从 last_rendered_ci 到 ci
            if ty._last_rendered_ci >= 0:
                if smooth_sp:
                    i0 = ty._last_rendered_ci * segs
                    i1 = min(ci * segs, len(smooth_sp) - 1)
                    seg = smooth_sp[i0:i1 + 1]
                else:
                    seg = screen_points[ty._last_rendered_ci:ci + 1]
                if len(seg) > 1:
                    line_color = (*PATH, path_alpha) if path_alpha < 255 else PATH
                    self._draw_lines_safe(trav, line_color, seg, 2, max_seg)
            elif ci > 0:
                if smooth_sp:
                    i1 = min(ci * segs, len(smooth_sp) - 1)
                    passed = smooth_sp[:i1 + 1]
                else:
                    passed = screen_points[:ci + 1]
                if len(passed) > 1:
                    line_color = (*PATH, path_alpha) if path_alpha < 255 else PATH
                    self._draw_lines_safe(trav, line_color, passed, 2, max_seg)

            # 追加点标记：last_rendered_ci .. ci-1
            for i in range(max(0, ty._last_rendered_ci), ci):
                p = ty.pts[i]
                x, y = screen_points[i]
                pc = p['color'] if highlight else p['color_dim']
                if path_alpha < 255 and len(pc) == 3:
                    pc = (*pc, path_alpha)
                cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                mk, ox, oy = self._make_point_marker(
                    pc, cat, p, r, point_radius_factor, path_alpha,
                    highlight, is_future=False)
                trav.blit(mk, (x - ox, y - oy))

            ty._last_rendered_ci = ci


        # ── ci 回退（编辑模式 undo）：重建 traversed ──
        elif ci < ty._last_rendered_ci:
            trav = ty._path_cache_traversed
            trav.fill((0, 0, 0, 0))
            smooth_sp = (getattr(ty.v, 'smooth_screen_points', None)
                         if getattr(self, 'smooth_path', False) else None)
            segs = max(1, getattr(self, 'smooth_path_segments', 10))
            if ci > 0:
                if smooth_sp:
                    end_idx = min(ci * segs, len(smooth_sp) - 1)
                    passed = smooth_sp[:end_idx + 1]
                else:
                    passed = screen_points[:ci + 1] if len(screen_points) > 1 else screen_points
                line_color = (*PATH, path_alpha) if path_alpha < 255 else PATH
                self._draw_lines_safe(trav, line_color, passed, 2, max_seg)
            for i in range(ci):
                p = ty.pts[i]
                x, y = screen_points[i]
                pc = p['color'] if highlight else p['color_dim']
                if path_alpha < 255 and len(pc) == 3:
                    pc = (*pc, path_alpha)
                cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                mk, ox, oy = self._make_point_marker(
                    pc, cat, p, r, point_radius_factor, path_alpha,
                    highlight, is_future=False)
                trav.blit(mk, (x - ox, y - oy))
            ty._last_rendered_ci = ci


        # ── 拖拽中：渲染到 bbox 尺寸 Surface，避免屏幕外路径被裁剪 ──
        dragging = self._drag_offset_x != 0 or self._drag_offset_y != 0
        if dragging:
            drag_key = (ci, highlight)
            if ty._path_cache_drag_key != drag_key:
                # 计算 screen_points 的包围盒
                if not screen_points:
                    min_x = min_y = max_x = max_y = 0
                else:
                    min_x = min(x for x, y in screen_points)
                    min_y = min(y for x, y in screen_points)
                    max_x = max(x for x, y in screen_points)
                    max_y = max(y for x, y in screen_points)
                pad = 30
                bw = max_x - min_x + pad * 2
                bh = max_y - min_y + pad * 2
                if bw < 4:
                    bw = 4
                if bh < 4:
                    bh = 4
                bbox_surf = pygame.Surface((bw, bh), pygame.SRCALPHA)

                # 局部坐标：screen_point → bbox 内偏移
                def _local(pt):
                    return (pt[0] - min_x + pad, pt[1] - min_y + pad)

                # 全路径半透明线
                f_alpha_for_full = FUTURE_LINE_ALPHA
                if path_alpha < 255:
                    f_alpha_for_full = min(FUTURE_LINE_ALPHA, path_alpha)
                if highlight and self.md == self.MODE_EDIT:
                    f_alpha_for_full = path_alpha if path_alpha < 255 else 255
                local_pts = [_local(sp) for sp in screen_points]
                self._draw_lines_safe(bbox_surf, (*PATH, f_alpha_for_full), local_pts, 2, max_seg)

                # 已走路径不透明线
                if ci > 0 and len(local_pts) > 1:
                    self._draw_lines_safe(bbox_surf, PATH, local_pts[:ci + 1], 2, max_seg)

                # 点标记
                for i, (p, lpt) in enumerate(zip(ty.pts, local_pts)):
                    lx, ly = lpt
                    pc = p['color'] if highlight else p['color_dim']
                    if path_alpha < 255 and len(pc) == 3:
                        pc = (*pc, path_alpha)
                    cat = p.get('cat', self.get_strength_category(p['w'], p['st']))
                    is_future = i > ci
                    mk, ox, oy = self._make_point_marker(
                        pc, cat, p, r, point_radius_factor,
                        path_alpha if i < ci else 255,
                        highlight, is_future=is_future)
                    bbox_surf.blit(mk, (lx - ox, ly - oy))

                # 当前位置高亮
                if 0 <= ci < n and highlight:
                    lx, ly = local_pts[ci]
                    highlight_r = r + int(2 * point_radius_factor)
                    outer_surf = self._get_circle_marker(highlight_r, CUR_POS)
                    pc_ci = ty.pts[ci]['color'] if highlight else ty.pts[ci]['color_dim']
                    if path_alpha < 255 and len(pc_ci) == 3:
                        pc_ci = (*pc_ci, path_alpha)
                    inner_surf = self._get_circle_marker(r, pc_ci)
                    bbox_surf.blit(outer_surf, (lx - highlight_r, ly - highlight_r))
                    bbox_surf.blit(inner_surf, (lx - r, ly - r))

                ty._path_cache_drag_surf = bbox_surf
                ty._path_cache_drag_key = drag_key
                ty._path_cache_drag_pos = (min_x - pad, min_y - pad)

            return ty._path_cache_drag_surf, ty._path_cache_drag_pos

        # ── 非拖拽：直接返回两个缓存的 Surface + 高亮信息 ──
        return ty._path_cache_full, ty._path_cache_traversed, (0, 0)

    def _blit_highlight(self, surface, x, y, r, highlight_r, pc, off_x, off_y):
        outer = self._get_circle_marker(highlight_r, CUR_POS)
        inner = self._get_circle_marker(r, pc)
        surface.blit(outer, (x - highlight_r + off_x, y - highlight_r + off_y))
        surface.blit(inner, (x - r + off_x, y - r + off_y))

    def _invalidate_path_cache_for_ty(self, ty):
        """使指定台风的增量路径缓存失效。"""
        ty._path_cache_full = None
        ty._path_cache_traversed = None
        ty._last_rendered_ci = -1
        ty._path_cache_key = ()
        ty._path_cache_drag_surf = None
        ty._path_cache_drag_key = ()

    def _invalidate_all_path_caches(self):
        """使所有台风路径缓存失效（视图变化时调用）。"""
        self._path_render_view_version += 1
        for ty in self.tys:
            ty._path_cache_full = None
            ty._path_cache_traversed = None
            ty._last_rendered_ci = -1
            ty._path_cache_key = ()
            ty._path_cache_drag_surf = None
            ty._path_cache_drag_key = ()

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

    _ring_scale_cache: dict = {}
    _center_scale_cache: dict = {}
    _l3_scale_cache: dict = {}

    # ── 路径绘制（使用缓存） ──
    def draw_typhoon(self, surface, ty, highlight):
        if not ty.pts:
            return

        screen_points = getattr(ty, 'screen_points', None)
        if not screen_points or len(screen_points) != len(ty.pts):
            screen_points = [self.latlon_to_screen(p['la'], p['lo']) for p in ty.pts]
            if hasattr(ty, 'update_screen_points'):
                ty.update_screen_points(self.latlon_to_screen)

        path_alpha = 255
        if self.fade_path and ty.finish_time > 0:
            ct = pygame.time.get_ticks()
            elapsed = (ct - ty.finish_time) / 1000.0
            if elapsed >= 30.0:
                path_alpha = 0
            else:
                path_alpha = max(0, int(255 * (1.0 - elapsed / 30.0)))

        if path_alpha <= 0 and self.fade_path:
            return

        # ── 从缓存获取或创建路径 Surface ──
        result = self._render_path_to_surface(
            ty, screen_points, path_alpha, highlight)
        if isinstance(result, tuple) and len(result) == 3:
            full_surf, trav_surf, (blit_x, blit_y) = result
            surface.blit(full_surf, (blit_x + self._drag_offset_x, blit_y + self._drag_offset_y))
            surface.blit(trav_surf, (blit_x + self._drag_offset_x, blit_y + self._drag_offset_y))
        else:
            path_surf, path_blit = result
            surface.blit(path_surf, (path_blit[0] + self._drag_offset_x, path_blit[1] + self._drag_offset_y))

        # 当前位置高亮（直接绘制，不分配复合 Surface）
        if highlight:
            n = len(ty.pts)
            ci = ty.ci
            if 0 <= ci < n:
                p = ty.pts[ci]
                x, y = screen_points[ci]
                pc = p['color'] if highlight else p['color_dim']
                if path_alpha < 255 and len(pc) == 3:
                    pc = (*pc, path_alpha)
                point_radius_factor = self.point_size / 100.0
                r = max(1, int(3 * point_radius_factor))
                highlight_r = r + int(2 * point_radius_factor)
                self._blit_highlight(surface, x, y, r, highlight_r, pc, self._drag_offset_x, self._drag_offset_y)

    # ── 图标 + 名称 + 信息框 ──
    def draw_typhoon_info(self, surface: pygame.Surface, ty) -> None:
        pos = ty.cpos()
        if not pos:
            return
        x, y = self.latlon_to_screen(pos['la'], pos['lo'])
        cp = ty.cp()
        if not cp:
            return

        show_icon = not (self.md == self.MODE_EDIT and not self.pl)
        icon_factor = self.icon_size / 100.0
        icon_alpha = 255

        if show_icon and self.fade_typhoon:
            if ty.ci >= len(ty.pts) - 2 and len(ty.pts) >= 2:
                if ty.ipos:
                    total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
                    progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
                else:
                    progress = 1.0 if ty.ci >= len(ty.pts) - 1 else 0.0
                icon_alpha = max(0, int(255 * (1.0 - progress)))

        if show_icon and icon_alpha > 0:
            cat = cp.get('cat', self.get_strength_category(cp['w'], cp['st']))
            ring_img = self.res_mgr.get_image(f"{cat}_ring")
            center_img = self.res_mgr.get_image(f"{cat}_center")
            if not ring_img:
                ring_img = self._create_fallback_ring()
            if not center_img:
                center_img = self._create_fallback_center()
            if ring_img and center_img:
                orig_w, orig_h = ring_img.get_size()
                target_size = max(20, 70 * icon_factor)
                scale = min(target_size / orig_w, target_size / orig_h)
                new_w, new_h = max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
                base_ring = self._get_scaled_image(ring_img, new_w, new_h, cat, self._ring_scale_cache)
                total_rotation = ty.ra + ty.sa
                rotated_ring = ty.get_rotated_ring(cat, base_ring, total_rotation, ty.mirror)

                if cat == "C5":
                    w = cp['w']
                    if w >= 170:
                        tint_color = C5_D
                    elif w >= 155:
                        ratio = (w - 155) / 15.0
                        r_ch = int(C5_L[0] + (C5_D[0] - C5_L[0]) * ratio)
                        g_ch = int(C5_L[1] + (C5_D[1] - C5_L[1]) * ratio)
                        b_ch = int(C5_L[2] + (C5_D[2] - C5_L[2]) * ratio)
                        tint_color = (r_ch, g_ch, b_ch)
                    else:
                        tint_color = C5_L
                    rotated_ring = self.tint_image(rotated_ring, tint_color)
                elif cat == "MD":
                    rotated_ring = self.tint_image(rotated_ring, MD_COLOR)
                elif cat == "STS":
                    rotated_ring = self.tint_image(rotated_ring, STS)

                if icon_alpha < 255:
                    rotated_ring.set_alpha(icon_alpha)

                rect = rotated_ring.get_rect(center=(x, y))
                surface.blit(rotated_ring, rect)

                target_center_size = max(10, 20 * icon_factor)
                csz = int(target_center_size)
                cent_img_scaled = self._get_scaled_image(center_img, csz, csz, cat, self._center_scale_cache)
                if icon_alpha < 255:
                    cent_img_scaled.set_alpha(icon_alpha)
                cent_rect = cent_img_scaled.get_rect(center=(x, y))
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
                        target_l3_size = max(65 * icon_factor, 20)
                        l3_scale = min(target_l3_size / l3_orig_w, target_l3_size / l3_orig_h)
                        l3_w, l3_h = int(l3_orig_w * l3_scale), int(l3_orig_h * l3_scale)
                        l3_base = self._get_scaled_image(l3_ring_img, l3_w, l3_h, level3_key, self._l3_scale_cache)
                        if cat in ("C3-", "C3"):
                            l3_angle = ty.sa3
                        elif cat in ("C4", "C4-ST"):
                            l3_angle = ty.sa4
                        else:
                            l3_angle = ty.sa5
                        l3_rotated = ty.get_rotated_level3_ring(level3_key, l3_base, l3_angle, ty.mirror)
                        l3_color = self.get_point_color(cp['w'], cp['st'])
                        if cat == "C5":
                            w = cp['w']
                            if w >= 170:
                                l3_color = C5_D
                            elif w >= 155:
                                ratio = (w - 155) / 15.0
                                r_ch = int(C5_L[0] + (C5_D[0] - C5_L[0]) * ratio)
                                g_ch = int(C5_L[1] + (C5_D[1] - C5_L[1]) * ratio)
                                b_ch = int(C5_L[2] + (C5_D[2] - C5_L[2]) * ratio)
                                l3_color = (r_ch, g_ch, b_ch)
                            else:
                                l3_color = C5_L
                        elif cat == "C3-":
                            l3_color = C3_MINUS
                        elif cat == "C3":
                            l3_color = C3
                        elif cat == "C4":
                            l3_color = C4
                        elif cat == "C4-ST":
                            l3_color = C4_ST
                        l3_rotated = self.tint_image(l3_rotated, l3_color)
                        if icon_alpha < 255:
                            l3_rotated.set_alpha(icon_alpha)
                        l3_rect = l3_rotated.get_rect(center=(x, y))
                        surface.blit(l3_rotated, l3_rect)

        show_name = not (self.md == self.MODE_EDIT and not self.pl) and icon_alpha > 100
        if show_name:
            self.draw_typhoon_name(surface, ty, x, y)

        if self.md == self.MODE_NORMAL and self.show_info_box_normal:
            self._draw_info_box(surface, ty, cp)
        elif self.md == self.MODE_EDIT:
            self._draw_info_box(surface, ty, cp)

    # ── fallback 图标 ──
    def _create_fallback_ring(self) -> pygame.Surface:
        s = pygame.Surface((80, 80), pygame.SRCALPHA)
        pygame.draw.circle(s, (200, 200, 200, 200), (40, 40), 35, 5)
        return s

    def _create_fallback_center(self) -> pygame.Surface:
        s = pygame.Surface((60, 60), pygame.SRCALPHA)
        pygame.draw.circle(s, (255, 255, 255, 240), (30, 30), 20)
        pygame.draw.circle(s, (50, 50, 50, 240), (30, 30), 4)
        return s

    # ── 名称 ──
    def _get_max_wind_color(self, ty):
        """获取台风名称颜色（基于最大风速），结果缓存于 typhoon 对象。"""
        cache_attr = '_cached_max_wind_color'
        cached = getattr(ty, cache_attr, None)
        if cached is not None:
            return cached
        valid_winds = [p for p in ty.pts if p['st'].upper() not in ['MD', 'SS', 'SD', 'EX', 'LO']]
        if valid_winds:
            mwp = max(valid_winds, key=lambda p: p['w'])
            color = mwp.get('color', self.get_point_color(mwp['w'], mwp['st']))
        else:
            color = TXT
        setattr(ty, cache_attr, color)
        return color

    def draw_typhoon_name(self, surface: pygame.Surface, ty, x: int, y: int) -> None:
        display_name = self.get_display_name(ty)
        name_color = self._get_max_wind_color(ty)

        cache_key = (display_name, name_color)
        if not hasattr(self, '_name_shadow_cache'):
            self._name_shadow_cache = {}
        if cache_key not in self._name_shadow_cache:
            name_text = f_name.render(display_name, True, name_color)
            black_text = f_name.render(display_name, True, (0, 0, 0))
            w, h = name_text.get_size()
            shadow_surf = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
            for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                shadow_surf.blit(black_text, (dx + 1, dy + 1))
            shadow_surf.blit(name_text, (1, 1))
            self._name_shadow_cache[cache_key] = shadow_surf

        shadow_surf = self._name_shadow_cache[cache_key]
        tx, ty_pos = x + 30, y - 20
        surface.blit(shadow_surf, (tx - 1, ty_pos - 1))

    # ── 信息框 ──
    def _draw_info_box(self, surface: pygame.Surface, ty, point: TrackPoint) -> None:
        key_data = (
            ty.b, ty.n, ty.cust, ty.sname, ty.start_time, point['t'],
            point['la'], point['lo'], point['w'], point['p'], point['st'],
            ty.tace, ty.cace, self.name_display_mode
        )
        if ty in self._info_box_cache_typhoon and self._info_box_last_data.get(ty) == key_data:
            box = self._info_box_cache_typhoon[ty]
        else:
            ifs, ifm = f_15, f_19

            bw, bh = 250, 260
            bg = pygame.Surface((bw, bh), pygame.SRCALPHA)
            pygame.draw.rect(bg, INFO_BOX_BG, (0, 0, bw, bh), 0, 10)
            pygame.draw.rect(bg, INFO_BOX_BORDER, (0, 0, bw, bh), 2, 10)

            y = 10
            max_w = bw - 20

            # ── 第1行：台风标签 ──
            label_surf = rt(ifs, "台风:", TXT, max_w)
            bg.blit(label_surf, (10, y))
            y += label_surf.get_height() + 2

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
            name_surf = rt(ifm, display_name, TXT, max_w)
            bg.blit(name_surf, (10, y))
            y += name_surf.get_height() + 4

            # ── 后续行 ──
            time_surf = rt(ifs, f"时间: {point['t']}", TXT, max_w)
            bg.blit(time_surf, (10, y)); y += time_surf.get_height() + 2

            la = point['la']
            lo = point['lo']
            lat_dir = 'N' if la >= 0 else 'S'
            lat_val = abs(la)
            if lo > 180.0:
                lon_val = 360.0 - lo
                lon_dir = 'W'
            else:
                lon_val = lo
                lon_dir = 'E'
            pos_surf = rt(ifs, f"位置: {lat_val:.1f}°{lat_dir}, {lon_val:.1f}°{lon_dir}", TXT, max_w)
            bg.blit(pos_surf, (10, y)); y += pos_surf.get_height() + 2

            st = point['st'].upper()
            if st in ('EX', 'MD', 'SS', 'SD', 'LO', 'DB'):
                wind_surf = rt(ifs, f"风速: {point['w']} kt  性质: {st}", TXT, max_w)
            else:
                wind_surf = rt(ifs, f"风速: {point['w']} kt", TXT, max_w)
            bg.blit(wind_surf, (10, y)); y += wind_surf.get_height() + 2

            pres_str = f"气压: {point['p']} hPa" if point['p'] != 0 else "气压: 未知"
            pres_surf = rt(ifs, pres_str, TXT, max_w)
            bg.blit(pres_surf, (10, y)); y += pres_surf.get_height() + 2

            cat = point.get('cat', self.get_strength_category(point['w'], point['st']))
            cat_surf = rt(ifs, f"等级: {cat}", TXT, max_w)
            bg.blit(cat_surf, (10, y)); y += cat_surf.get_height() + 2

            off_text = "正式报" if point.get('official', True) else "非正式报"
            off_color = (0, 150, 0) if point.get('official', True) else (150, 0, 0)
            off_surf = rt(ifs, f"报别: {off_text}", off_color, max_w)
            bg.blit(off_surf, (10, y)); y += off_surf.get_height() + 2

            ace_total = rt(ifs, f"总ACE: {ty.tace:.4f}", TXT, max_w)
            bg.blit(ace_total, (10, y)); y += ace_total.get_height() + 2
            ace_curr = rt(ifs, f"实时ACE: {ty.cace:.4f}", TXT, max_w)
            bg.blit(ace_curr, (10, y)); y += ace_curr.get_height() + 2

            valid_winds = [p['w'] for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
            max_wind = max(valid_winds) if valid_winds else 0
            peak_surf = rt(ifs, f"巅峰: {max_wind} kt", TXT, max_w)
            bg.blit(peak_surf, (10, y))

            self._info_box_cache_typhoon[ty] = bg
            self._info_box_last_data[ty] = key_data
            box = bg

        surface.blit(box, (15, 15))