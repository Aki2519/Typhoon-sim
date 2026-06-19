# py/ty_sim_mixins/utils_mixin.py
import pygame
from datetime import datetime, timedelta
from typing import Tuple
from ..constants import (
    f_s, f_m, f_name, f_l, rt, TXT, PATH, CUR_POS, BG, DB, EX, TD, TS, STS,
    C1, C2, C3, C4, C5_L, C5_D, MD_COLOR, C2_LIGHT, C2_DARK, SPEC, BUTTON_BORDER,
    ERROR_BG, ERROR_BORDER
)
from ..utils import infer_strength_category

class TySimUtilsMixin:
    """工具方法：坐标转换、颜色处理等"""

    @staticmethod
    def darken_color(c: Tuple[int, int, int], factor: float = 0.6) -> Tuple[int, int, int]:
        r, g, b = c
        return (int(r * factor), int(g * factor), int(b * factor))

    def latlon_to_screen(self, la: float, lo: float) -> Tuple[int, int]:
        if self.map_mgr.map_view is None:
            return 0, 0
        return self.map_mgr.map_view.geo_to_screen(lo, la)

    def screen_to_latlon(self, x: int, y: int) -> Tuple[float, float]:
        if self.map_mgr.map_view is None:
            return 0.0, 0.0
        lon, lat = self.map_mgr.map_view.screen_to_geo(x, y)
        return lat, lon

    def get_strength_category(self, wind: int, stype: str) -> str:
        return infer_strength_category(wind, stype)

    gsc = get_strength_category

    def get_point_color(self, wind: int, stype: str) -> Tuple[int, int, int]:
        cat = self.get_strength_category(wind, stype)
        if cat == "DB":
            return DB
        elif cat == "EX":
            return EX
        elif cat == "TD":
            return TD
        elif cat == "TS":
            return TS
        elif cat == "STS":
            return STS
        elif cat == "CAT1":
            return C1
        elif cat == "CAT2":
            return C2
        elif cat == "CAT3":
            return C3
        elif cat == "CAT4":
            return C4
        elif cat == "CAT5":
            if wind >= 170:
                return C5_D
            elif wind >= 155:
                ratio = (wind - 155) / 15.0
                r = int(C5_L[0] + (C5_D[0] - C5_L[0]) * ratio)
                g = int(C5_L[1] + (C5_D[1] - C5_L[1]) * ratio)
                b = int(C5_L[2] + (C5_D[2] - C5_L[2]) * ratio)
                return (r, g, b)
            else:
                return C5_L
        elif cat == "MD":
            return MD_COLOR
        elif cat == "SD":
            return (100, 150, 200)
        elif cat == "SS":
            return (200, 150, 100)
        elif cat == "LO":
            return (150, 200, 100)
        return TD

    def tint_image(self, image: pygame.Surface, color: Tuple[int, int, int]) -> pygame.Surface:
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
            st = vals['type'] if vals['type'] else self.dialog_mgr.point_list.infer_type(w)
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

            new_point = {
                't': t, 'la': la, 'lo': lo, 'w': w, 'p': p, 'st': st,
                'cat': cat,
                'color': color,
                'color_dim': color_dim,
                'name': current_name,
                'official': True,
                'ace': 0,
                'pace': 0,
                'ace_year': ace_year
            }

            ty.push_snapshot()
            ty.pts.append(new_point)
            ty.recalc_ace()
            ty.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            if hasattr(self, '_start_cache'):
                self._start_cache.pop(ty, None)

        except ValueError as e:
            self.show_error("添加点失败：数值格式错误")
        except Exception as e:
            self.show_error("添加点失败")