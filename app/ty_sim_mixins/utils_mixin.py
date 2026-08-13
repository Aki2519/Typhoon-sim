# py/ty_sim_mixins/utils_mixin.py
"""工具方法：坐标转换、编辑操作、错误提示。"""
from __future__ import annotations
import pygame
import logging
from datetime import datetime, timedelta
from ..typhoon import TrackPoint
from ..typhoon_render import _clear_geo_spline_cache

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
            if ty.pts and ty.pts[0].get('t'):
                ty.start_time = ty.pts[0]['t']
            else:
                return "2000010100"
        try:
            if len(ty.start_time) >= 12:
                base = datetime.strptime(ty.start_time[:12], "%Y%m%d%H%M")
                fmt = "%Y%m%d%H%M"
            else:
                base = datetime.strptime(ty.start_time[:10], "%Y%m%d%H")
                fmt = "%Y%m%d%H"
            new_time = base + timedelta(hours=6 * len(ty.pts))
            return new_time.strftime(fmt)
        except ValueError:
            return "2000010100"

    def _reposition_typhoon_on_path(self, ty) -> None:
        """点数据变化后，把台风当前显示位置(ipos)重定位到新路径上。
        否则旧 ipos 残留会与移动后的首点之间连出伪线（live segment）。"""
        n = len(ty.pts)
        if not n or ty.ci < 0 or ty.ci >= n:
            return
        if ty.v.ipos is None:
            return
        pt_list = ty.points_time
        if len(pt_list) != n or ty.ci + 1 >= n:
            ty.v.ipos = None
            return
        pt0, pt1 = pt_list[ty.ci], pt_list[ty.ci + 1]
        total = pt1 - pt0
        t = (ty.at - pt0) / total if total > 0 else 0.0
        t = min(1.0, max(0.0, t))
        if t <= 0:
            # at 未离开段起点：台风应停在新 pts[ci] 上
            ty.v.ipos = None
            return
        use_smooth = (self.cfg.smooth_path and ty.v.smooth_screen_points
                      and ty.v._smooth_arc_lengths)
        if use_smooth:
            from ..spline import position_at_arc
            segs = max(1, self.cfg.smooth_path_segments)
            arcs = ty.v._smooth_arc_lengths
            i0 = ty.ci * segs
            i1 = min((ty.ci + 1) * segs, len(arcs) - 1)
            if i1 == i0:
                i1 = i0 + 1 if i0 + 1 < len(arcs) else i0
            seg_start = arcs[i0]
            seg_total = arcs[i1] - seg_start
            if seg_total <= 0:
                seg_total = 1.0
            sc_x, sc_y = position_at_arc(
                ty.v.smooth_screen_points, arcs, seg_start + seg_total * t)
            la, lo = self.screen_to_latlon(sc_x, sc_y)
            ty.v.ipos['la'] = la
            ty.v.ipos['lo'] = lo
        else:
            cp, np = ty.pts[ty.ci], ty.pts[ty.ci + 1]
            ty.v.ipos['la'] = cp['la'] + (np['la'] - cp['la']) * t
            ty.v.ipos['lo'] = cp['lo'] + (np['lo'] - cp['lo']) * t

    def refresh_typhoon_after_point_change(self, ty) -> None:
        self._invalidate_path_cache_for_ty(ty)
        ty._cached_landfalls = None
        ty._ace_year_events = None
        ty._ace_ty_yearly = None
        ty._officials = None   # 微优化1: RI 官方报索引缓存失效
        ty.recalc_simulated_times()
        self._reposition_typhoon_on_path(ty)

    def _drop_season_start_cache(self, ty) -> None:
        if hasattr(self, 'season_ctrl'):
            self.season_ctrl._start_cache.pop(ty, None)

    def _delete_edit_point(self, idx, ty=None) -> bool:
        """编辑模式删除报点(带撤销)。同步地图选中与报点列表状态。"""
        ty = ty or self.edit_typhoon
        if not ty or not ty.pts or idx is None or not (0 <= idx < len(ty.pts)):
            return False
        ty.push_snapshot()
        del ty.pts[idx]
        n = len(ty.pts)
        new_idx = min(idx, n - 1) if n else None
        self._edit_selected_point = new_idx
        self._last_edited_point = new_idx
        ty.recalc_ace()
        _clear_geo_spline_cache(ty)
        ty.update_screen_points(self.latlon_to_screen)
        self.refresh_typhoon_after_point_change(ty)
        self._refresh_ace_data(ty)
        self._season_info_box_cache.pop(ty, None)
        self._season_info_box_last_data.pop(ty, None)
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl._clear_row_cache()
            pl._needs_save = True
            pl.current_page = min(pl.current_page, max(0, pl.get_total_pages() - 1))
            pl.selected_index = new_idx if new_idx is not None else -1
        else:
            pl.save_typhoon_to_file(ty)
        self._drop_season_start_cache(ty)
        return True

    def _select_edit_point(self, delta: int) -> bool:
        """编辑模式: 切换选中报点(循环)。"""
        ty = self.edit_typhoon
        if not ty or not ty.pts:
            return False
        n = len(ty.pts)
        sel = getattr(self, '_edit_selected_point', None)
        if sel is None or not (0 <= sel < n):
            sel = 0
        else:
            sel = (sel + delta) % n
        self._edit_selected_point = sel
        self._last_edited_point = sel
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl.selected_index = sel
            pl.current_page = sel // pl.rows_per_page
            pl._clear_row_cache()
        return True

    def _nudge_edit_point(self, dx: int, dy: int) -> bool:
        """编辑模式: 按吸附步长微调选中报点(Ctrl+方向键)。
        dx>0 东移, dy>0 北移。"""
        ty = self.edit_typhoon
        sel = getattr(self, '_edit_selected_point', None)
        if not ty or sel is None or not (0 <= sel < len(ty.pts)):
            return False
        step = getattr(self.cfg, 'edit_snap_step', 0.1)
        if not step or step <= 0:
            step = 0.1
        pt = ty.pts[sel]
        new_la = max(-90.0, min(90.0, pt['la'] + dy * step))
        new_lo = max(0.0, min(360.0, pt['lo'] + dx * step))
        if new_la == pt['la'] and new_lo == pt['lo']:
            return False
        pt['la'] = new_la
        pt['lo'] = new_lo
        from ..typhoon_render import _clear_geo_spline_cache
        _clear_geo_spline_cache(ty)
        ty.update_screen_points(self.latlon_to_screen)
        self._reposition_typhoon_on_path(ty)
        self._invalidate_path_cache_for_ty(ty)
        self.dialog_mgr.point_list.save_typhoon_to_file(ty)
        return True

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
            # 先解析校验,全部通过后再快照,避免解析失败残留无效果撤销步
            w = int(vals['wind']) if vals['wind'] else 15
            p = int(vals['pressure']) if vals['pressure'] else 0
            if w < 0 or p < 0:
                self.show_error("风速/气压不能为负数")
                return
            st = (vals['type'] or '').strip() or self.dialog_mgr.point_list._infer_type(w, ty.basin)
            t = vals['time']

            ty.push_snapshot()
            cat = self.get_strength_category(w, st)
            color = self.get_point_color(w, st)
            color_dim = self.darken_color(color, 0.6)

            ace_year = 0
            if len(t) >= 10:
                try:
                    dt = datetime.strptime(t[:10], "%Y%m%d%H")
                    ace_year = self.get_ace_year(dt)
                except Exception:
                    ace_year = 0

            if len(ty.pts) == 0 and la < 0:
                ty.mirror = True
                ty.rot_dir = -1
            # 自动检测洋区
            if len(ty.pts) == 0 and self.res_mgr.ocean_areas.areas:
                area = self.res_mgr.ocean_areas.find_area(la, lo)
                if area:
                    ty.basin = area.code

            new_point = TrackPoint(
                t=t, la=la, lo=lo, w=w, p=p, st=st,
                cat=cat, color=color, color_dim=color_dim,
                name=current_name, official=True,
                ace=0, pace=0, ace_year=ace_year,
            )

            # 按时间顺序插入,保证路径按时间连接(无论输入时间在何时)
            new_idx = ty.insert_point_by_time(new_point)
            ty.recalc_ace()
            # N7: 先清 geo 样条缓存,再重投影(就地修改 pts 时旧样条不得复用)
            _clear_geo_spline_cache(ty)
            ty.update_screen_points(self.latlon_to_screen)
            self.refresh_typhoon_after_point_change(ty)
            self._refresh_ace_data(ty)
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            self._drop_season_start_cache(ty)

            self._last_edited_point = new_idx

        except ValueError:
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
            if w < 0 or p < 0:
                self.show_error("风速/气压不能为负数")
                return
            st = (vals['type'] or '').strip() or ty.pts[point_index].get('st', '')
            t = vals['time']
            ty.push_snapshot()

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

            # 时间被修改后按时间重排,保证路径按时间连接
            point_index = ty.resort_point_by_time(point_index)
            ty.recalc_ace()
            # N7: 先清 geo 样条缓存,再重投影
            _clear_geo_spline_cache(ty)
            ty.update_screen_points(self.latlon_to_screen)
            self.refresh_typhoon_after_point_change(ty)
            self._refresh_ace_data(ty)
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            self._drop_season_start_cache(ty)

            self._last_edited_point = point_index
        except ValueError:
            self.show_error("修改点失败：数值格式错误")
        except Exception as e:
            logger.debug(f"修改点失败: {e}", exc_info=True)
            self.show_error("修改点失败")