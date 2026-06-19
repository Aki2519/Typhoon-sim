# py/ty_sim_mixins/draw_typhoon_mixin.py
"""台风渲染 Mixin：路径、图标、名称、信息框。"""
import pygame
from collections import OrderedDict
from ..constants import (
    f_s, f_m, f_name, rt, TXT, PATH, CUR_POS,
    DB, EX, TD, TS, STS, C1, C2, C3, C4, C5_L, C5_D, MD_COLOR, C2_LIGHT, C2_DARK,
    SPEC,
    HEMISPHERE_NORTH,
    INFO_BOX_BG, INFO_BOX_BORDER,
    FUTURE_LINE_ALPHA,
)


class TySimDrawTyphoonMixin:
    """台风路径、图标、名称、信息框的绘制。"""

    _transparent_circle_cache: OrderedDict = OrderedDict()
    _opaque_circle_cache: OrderedDict = OrderedDict()
    _MAX_CIRCLE_CACHE = 256

    _info_box_cache_typhoon: dict = {}
    _info_box_last_data: dict = {}

    def _get_transparent_circle(self, radius, color):
        key = (radius, color)
        cache = self._transparent_circle_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, color, (radius, radius), radius)
        cache[key] = surf
        if len(cache) > self._MAX_CIRCLE_CACHE:
            cache.popitem(last=False)
        return surf

    def _get_opaque_circle(self, radius, color):
        key = (radius, color)
        cache = self._opaque_circle_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, color, (radius, radius), radius)
        cache[key] = surf
        if len(cache) > self._MAX_CIRCLE_CACHE:
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

    def _draw_typhoons(self, surface):
        cty = self.current_typhoon()
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self.draw_typhoon(surface, self.edit_typhoon, highlight=True)
        else:
            for ty in self.tys:
                if self.should_draw_typhoon(ty):
                    self.draw_typhoon(surface, ty, highlight=(ty == cty))

    # ── 路径 ──
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

        if ty.ci > 0:
            passed = screen_points[:ty.ci + 1]
            if len(passed) > 1:
                if path_alpha < 255:
                    path_surf = pygame.Surface((self.screen_width, self.map_height), pygame.SRCALPHA)
                    path_color = (*PATH, path_alpha)
                    pygame.draw.lines(path_surf, path_color, False, passed, 2)
                    surface.blit(path_surf, (0, 0))
                else:
                    pygame.draw.lines(surface, PATH, False, passed, 2)

        if ty.ci < len(ty.pts) - 1:
            future = screen_points[ty.ci:]
            if len(future) > 1:
                if highlight and self.md == self.MODE_EDIT:
                    if path_alpha < 255:
                        path_surf = pygame.Surface((self.screen_width, self.map_height), pygame.SRCALPHA)
                        path_color = (*PATH, path_alpha)
                        pygame.draw.lines(path_surf, path_color, False, future, 2)
                        surface.blit(path_surf, (0, 0))
                    else:
                        pygame.draw.lines(surface, PATH, False, future, 2)
                else:
                    if path_alpha < 255:
                        path_surf = pygame.Surface((self.screen_width, self.map_height), pygame.SRCALPHA)
                        path_color = (*PATH, min(FUTURE_LINE_ALPHA, path_alpha))
                        pygame.draw.lines(path_surf, path_color, False, future, 2)
                        surface.blit(path_surf, (0, 0))
                    else:
                        if not hasattr(self, '_future_line_surface') or \
                           self._future_line_surface is None or \
                           self._future_line_surface.get_size() != (self.screen_width, self.map_height):
                            self._future_line_surface = pygame.Surface((self.screen_width, self.map_height), pygame.SRCALPHA)
                        self._future_line_surface.fill((0, 0, 0, 0))
                        pygame.draw.lines(self._future_line_surface, (*PATH, FUTURE_LINE_ALPHA), False, future, 2)
                        surface.blit(self._future_line_surface, (0, 0))

        point_radius_factor = self.point_size / 100.0
        base_radius = 3
        r = int(base_radius * point_radius_factor)
        if r < 1:
            r = 1

        for i, (p, (x, y)) in enumerate(zip(ty.pts, screen_points)):
            pc = p['color'] if highlight else p['color_dim']
            cat = p.get('cat', self.get_strength_category(p['w'], p['st']))

            if path_alpha < 255:
                if len(pc) == 3:
                    pc = (*pc, path_alpha)

            if not p.get('official', True):
                size = max(2, int(2 * point_radius_factor))
                if path_alpha < 255:
                    rect_surf = pygame.Surface((size, size), pygame.SRCALPHA)
                    rect_surf.fill(pc)
                    surface.blit(rect_surf, (x - size//2, y - size//2))
                else:
                    pygame.draw.rect(surface, pc, (x - size//2, y - size//2, size, size))
            elif cat == "EX":
                tri_h = int(3 * point_radius_factor)
                tri_w = int(3 * point_radius_factor)
                tri = [(x, y - tri_h), (x - tri_w, y + tri_h), (x + tri_w, y + tri_h)]
                if path_alpha < 255:
                    tri_surf = pygame.Surface((tri_w * 2, tri_h * 2), pygame.SRCALPHA)
                    pygame.draw.polygon(tri_surf, pc, [(t[0] - x + tri_w, t[1] - y + tri_h) for t in tri])
                    surface.blit(tri_surf, (x - tri_w, y - tri_h))
                else:
                    pygame.draw.polygon(surface, pc, tri)
            else:
                if i > ty.ci:
                    if highlight and self.md == self.MODE_EDIT:
                        surf = self._get_opaque_circle(r, pc)
                        surface.blit(surf, (x - r, y - r))
                    else:
                        alpha_pc = (*pc[:3], FUTURE_LINE_ALPHA) if len(pc) == 3 else pc
                        surf = self._get_transparent_circle(r, alpha_pc)
                        surface.blit(surf, (x - r, y - r))
                elif i == ty.ci and highlight:
                    highlight_r = r + int(2 * point_radius_factor)
                    outer_surf = self._get_opaque_circle(highlight_r, CUR_POS)
                    inner_surf = self._get_opaque_circle(r, pc)
                    surface.blit(outer_surf, (x - highlight_r, y - highlight_r))
                    surface.blit(inner_surf, (x - r, y - r))
                else:
                    surf = self._get_opaque_circle(r, pc)
                    surface.blit(surf, (x - r, y - r))

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
                base_ring = pygame.transform.smoothscale(ring_img, (new_w, new_h))
                total_rotation = ty.ra + ty.sa
                rotated_ring = ty.get_rotated_ring(cat, base_ring, total_rotation, ty.mirror)

                if cat == "CAT5":
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
                elif cat == "CAT2":
                    w = cp['w']
                    if 83 <= w < 86:
                        rotated_ring = self.tint_image(rotated_ring, C2_LIGHT)
                    elif 91 <= w < 96:
                        rotated_ring = self.tint_image(rotated_ring, C2_DARK)

                if icon_alpha < 255:
                    rotated_ring.set_alpha(icon_alpha)

                rect = rotated_ring.get_rect(center=(x, y))
                surface.blit(rotated_ring, rect)

                target_center_size = max(10, 20 * icon_factor)
                cent_img_scaled = pygame.transform.smoothscale(
                    center_img, (int(target_center_size), int(target_center_size)))
                if icon_alpha < 255:
                    cent_img_scaled.set_alpha(icon_alpha)
                cent_rect = cent_img_scaled.get_rect(center=(x, y))
                surface.blit(cent_img_scaled, cent_rect)

                level3_key = None
                if cat == "CAT3":
                    level3_key = "CAT3_3"
                elif cat == "CAT4":
                    level3_key = "CAT4_3"
                elif cat == "CAT5":
                    level3_key = "CAT5_3"
                if level3_key:
                    l3_ring_img = self.res_mgr.get_image(f"{level3_key}_ring")
                    if l3_ring_img:
                        l3_orig_w, l3_orig_h = l3_ring_img.get_size()
                        target_l3_size = max(65 * icon_factor, 20)
                        l3_scale = min(target_l3_size / l3_orig_w, target_l3_size / l3_orig_h)
                        l3_base = pygame.transform.smoothscale(l3_ring_img,
                                                               (int(l3_orig_w * l3_scale), int(l3_orig_h * l3_scale)))
                        if cat == "CAT3":
                            l3_angle = ty.sa3
                        elif cat == "CAT4":
                            l3_angle = ty.sa4
                        else:
                            l3_angle = ty.sa5
                        l3_rotated = ty.get_rotated_level3_ring(level3_key, l3_base, l3_angle, ty.mirror)
                        l3_color = self.get_point_color(cp['w'], cp['st'])
                        if cat == "CAT5":
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
                        elif cat == "CAT3":
                            l3_color = C3
                        elif cat == "CAT4":
                            l3_color = C4
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
    def draw_typhoon_name(self, surface: pygame.Surface, ty, x: int, y: int) -> None:
        display_name = self.get_display_name(ty)
        valid_winds = [p for p in ty.pts if p['st'].upper() not in ['MD', 'SS', 'SD', 'EX', 'LO']]
        if valid_winds:
            max_wind_point = max(valid_winds, key=lambda p: p['w'])
            name_color = max_wind_point.get('color', self.get_point_color(max_wind_point['w'], max_wind_point['st']))
        else:
            name_color = TXT

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
    def _draw_info_box(self, surface: pygame.Surface, ty, point: dict) -> None:
        key_data = (
            ty.b, ty.n, ty.cust, ty.sname, ty.start_time, point['t'],
            point['la'], point['lo'], point['w'], point['p'], point['st'],
            ty.tace, ty.cace, self.name_display_mode
        )
        if ty in self._info_box_cache_typhoon and self._info_box_last_data.get(ty) == key_data:
            box = self._info_box_cache_typhoon[ty]
        else:
            bw, bh = 250, 240
            bg = pygame.Surface((bw, bh), pygame.SRCALPHA)
            pygame.draw.rect(bg, INFO_BOX_BG, (0, 0, bw, bh), 0, 10)
            pygame.draw.rect(bg, INFO_BOX_BORDER, (0, 0, bw, bh), 2, 10)

            # 名称：mode 0 显示年份+编号+名称，mode 1 仅显示 get_display_name
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
            name_surf = rt(f_m, f"台风: {display_name}", TXT, bw - 20)
            bg.blit(name_surf, (10, 10))
            time_surf = rt(f_s, f"时间: {point['t']}", TXT, bw - 20)
            bg.blit(time_surf, (10, 38))

            # 经纬度：根据符号动态显示 N/S/E/W
            la = point['la']
            lo = point['lo']
            lat_dir = 'N' if la >= 0 else 'S'
            lat_val = abs(la)
            # 经度 0–360 规范化显示
            if lo > 180.0:
                lon_val = 360.0 - lo
                lon_dir = 'W'
            else:
                lon_val = lo
                lon_dir = 'E'
            pos_surf = rt(f_s, f"位置: {lat_val:.1f}°{lat_dir}, {lon_val:.1f}°{lon_dir}", TXT, bw - 20)
            bg.blit(pos_surf, (10, 58))

            # 风速 + 天气类型
            st = point['st'].upper()
            if st in ('EX', 'MD', 'SS', 'SD', 'LO', 'DB'):
                wind_surf = rt(f_s, f"风速: {point['w']} kt  天气: {st}", TXT, bw - 20)
            else:
                wind_surf = rt(f_s, f"风速: {point['w']} kt", TXT, bw - 20)
            bg.blit(wind_surf, (10, 78))

            pres_str = f"气压: {point['p']} hPa" if point['p'] != 0 else "气压: 未知"
            pres_surf = rt(f_s, pres_str, TXT, bw - 20)
            bg.blit(pres_surf, (10, 98))

            cat = point.get('cat', self.get_strength_category(point['w'], point['st']))
            cat_surf = rt(f_s, f"等级: {cat}", TXT, bw - 20)
            bg.blit(cat_surf, (10, 118))

            off_text = "正式报" if point.get('official', True) else "非正式报"
            off_color = (0, 150, 0) if point.get('official', True) else (150, 0, 0)
            off_surf = rt(f_s, f"报别: {off_text}", off_color, bw - 20)
            bg.blit(off_surf, (10, 138))

            ace_total = rt(f_s, f"总ACE: {ty.tace:.4f}", TXT, bw - 20)
            bg.blit(ace_total, (10, 158))
            ace_curr = rt(f_s, f"实时ACE: {ty.cace:.4f}", TXT, bw - 20)
            bg.blit(ace_curr, (10, 178))
            # 最大风速
            valid_winds = [p['w'] for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
            max_wind = max(valid_winds) if valid_winds else 0
            peak_surf = rt(f_s, f"巅峰: {max_wind} kt", TXT, bw - 20)
            bg.blit(peak_surf, (10, 198))

            self._info_box_cache_typhoon[ty] = bg
            self._info_box_last_data[ty] = key_data
            box = bg

        surface.blit(box, (15, 15))