# py/ty_sim_mixins/utils_mixin.py
"""工具方法：坐标转换、编辑操作、错误提示。"""
from __future__ import annotations
import pygame
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TySimUtilsMixin:
    """工具方法：坐标转换委托、编辑操作、错误提示。"""

    def darken_color(self, c, factor=0.6):
        return self.repo.darken_color(c, factor)

    def latlon_to_screen(self, la, lo):
        return self.view.latlon_to_screen(la, lo)

    def screen_to_latlon(self, x, y):
        return self.view.screen_to_latlon(x, y)

    def get_strength_category(self, wind, stype):
        return self.repo.get_strength_category(wind, stype)

    gsc = get_strength_category

    def get_point_color(self, wind, stype):
        return self.repo.get_point_color(wind, stype)

    def tint_image(self, image, color):
        tinted = pygame.Surface(image.get_size(), pygame.SRCALPHA)
        tinted.fill((*color, 0))
        tinted.blit(image, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        return tinted

    def show_error(self, message: str) -> None:
        self.error_message = message
        self.error_time = pygame.time.get_ticks()

    def get_next_time_for_typhoon(self, ty) -> str:
        if not ty.start_time:
            if ty.pts:
                ty.start_time = ty.pts[0]['t']
            else:
                return "2000010100"
        try:
            base = datetime.strptime(ty.start_time, "%Y%m%d%H")
            new_time = base + timedelta(hours=6 * len(ty.pts))
            return new_time.strftime("%Y%m%d%H")
        except ValueError:
            return "2000010100"

    def add_point_to_edit_typhoon(self, vals: dict, current_name: str) -> None:
        ty = self.edit_typhoon
        if not ty:
            return
        try:
            la = float(vals['lat'])
            lo = float(vals['lon'])
            if la < -90 or la > 90:
                self.show_error("纬度必须在 -90 到 90 之间")
                return
            if lo < 0 or lo > 360:
                self.show_error("经度必须在 0 到 360 之间")
                return
        except ValueError:
            self.show_error("经纬度必须是有效数字")
            return

        try:
            w = int(vals['wind']) if vals['wind'] else 15
            p = int(vals['pressure']) if vals['pressure'] else 0
            st = vals['type'] if vals['type'] else self.dialog_mgr.point_list._infer_type(w, ty.basin)
            t = vals['time']

            cat = self.get_strength_category(w, st)
            color = self.get_point_color(w, st)
            color_dim = self.darken_color(color, 0.6)

            ace_year = 0
            if len(t) >= 10:
                try:
                    dt = datetime.strptime(t[:10], "%Y%m%d%H")
                    ace_year = self.get_ace_year(dt)
                except (ValueError, Exception):
                    ace_year = 0

            if len(ty.pts) == 0 and la < 0:
                ty.mirror = True
                ty.rot_dir = -1
            # 自动检测洋区
            if len(ty.pts) == 0 and self.res_mgr.ocean_areas.areas:
                area = self.res_mgr.ocean_areas.find_area(la, lo)
                if area:
                    ty.basin = area.code

            from ..typhoon import TrackPoint
            new_point = TrackPoint(
                t=t, la=la, lo=lo, w=w, p=p, st=st,
                cat=cat, color=color, color_dim=color_dim,
                name=current_name, official=True,
                ace=0, pace=0, ace_year=ace_year,
            )

            ty.push_snapshot()
            ty.pts.append(new_point)
            ty.recalc_ace()
            ty.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            if hasattr(self, '_start_cache'):
                self._start_cache.pop(ty, None)

        except ValueError as e:
            self.show_error("添加点失败：数值格式错误")

    def update_point_in_edit_typhoon(self, vals: dict, point_index: int) -> None:
        """编辑模式：更新台风已有报点。"""
        ty = self.edit_typhoon
        if not ty or point_index < 0 or point_index >= len(ty.pts):
            return
        try:
            la = float(vals['lat'])
            lo = float(vals['lon'])
            if la < -90 or la > 90:
                self.show_error("纬度必须在 -90 到 90 之间")
                return
            if lo < 0 or lo > 360:
                self.show_error("经度必须在 0 到 360 之间")
                return
        except ValueError:
            self.show_error("经纬度必须是有效数字")
            return

        try:
            w = int(vals['wind']) if vals['wind'] else 15
            p = int(vals['pressure']) if vals['pressure'] else 0
            st = vals['type'] if vals['type'] else ty.pts[point_index].get('st', '')
            t = vals['time']

            cat = self.get_strength_category(w, st)
            color = self.get_point_color(w, st)
            color_dim = self.darken_color(color, 0.6)

            ace_year = 0
            if len(t) >= 10:
                try:
                    dt = datetime.strptime(t[:10], "%Y%m%d%H")
                    ace_year = self.get_ace_year(dt)
                except (ValueError, Exception):
                    ace_year = 0

            pt = ty.pts[point_index]
            pt['t'] = t
            pt['la'] = la
            pt['lo'] = lo
            pt['w'] = w
            pt['p'] = p
            pt['st'] = st
            pt['cat'] = cat
            pt['color'] = color
            pt['color_dim'] = color_dim
            pt['ace_year'] = ace_year

            ty.recalc_ace()
            ty.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            if hasattr(self, '_start_cache'):
                self._start_cache.pop(ty, None)

        except ValueError as e:
            self.show_error("修改点失败：数值格式错误")
        except Exception as e:
            logger.debug(f"修改点失败: {e}", exc_info=True)
            self.show_error("修改点失败")