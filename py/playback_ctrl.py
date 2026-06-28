# py/playback_ctrl.py
"""播放控制器：台风更新、淡出、登陆检测。"""
from __future__ import annotations

import pygame
from typing import List, Optional, TYPE_CHECKING

from .typhoon import Typhoon
from .landfall_effect import LandfallEffect

if TYPE_CHECKING:
    from .data_repo import DataRepository
    from .view_state import ViewState
    from .config import AppConfig
    from .ace_engine import ACEEngine
    from .resource_manager import ResourceManager, MapManager
    from .season_ctrl import SeasonController

_MODE_NORMAL = "normal"
_MODE_SEASON = "season"
_MODE_EDIT = "edit"


class PlaybackController:
    """管理台风更新循环：移动、旋转、淡出、登陆检测。"""

    def __init__(self, cfg: AppConfig, repo: DataRepository,
                 view: ViewState, ace_engine: ACEEngine,
                 res_mgr: ResourceManager, map_mgr: MapManager) -> None:
        self.cfg = cfg
        self.repo = repo
        self.view = view
        self.ace_engine = ace_engine
        self.res_mgr = res_mgr
        self.map_mgr = map_mgr
        self.effects: List[LandfallEffect] = []
        self.landfall_records: list = []
        self._pl: bool = False

    def latlon_to_screen(self, la: float, lo: float) -> tuple:
        return self.view.latlon_to_screen(la, lo)

    def update_all(self, ct: float, dt: float, dialog_open: bool,
                   season_ctrl: Optional[SeasonController] = None) -> None:
        paused = not self._pl or dialog_open
        md = self.cfg.md
        current = self.repo.current_typhoon() if md == _MODE_NORMAL else None
        self.map_mgr.update_land_mask()
        for ty in self.repo.tys:
            if ty.act:
                ty.us(dt)
            self._fade_one(ty, ct)
            self._check_landfall(ty, ct, season_ctrl)
            if not ty.act:
                continue
            if md == _MODE_NORMAL:
                if ty == current:
                    self._update_normal(ty, ct, paused)
            elif md == _MODE_SEASON:
                if ty.ss and not ty.sf:
                    self._update_season(ty, ct, paused, season_ctrl)
            elif md == _MODE_EDIT:
                if self.repo.edit_typhoon == ty:
                    self._update_edit(ty, ct, paused)
        self.effects = [e for e in self.effects if e.update(ct)]
        if md == _MODE_SEASON and not paused and season_ctrl and self.cfg.ace_interpolated:
            season_ctrl.csa = self._compute_interpolated_csa(season_ctrl)

    def _fade_one(self, ty: Typhoon, ct: float) -> None:
        if ty.finish_time <= 0:
            return
        elapsed = (ct - ty.finish_time) / 1000.0
        v = ty.v
        if self.cfg.fade_typhoon:
            v.icon_alpha = max(0, int(255 * (1.0 - elapsed / 30.0)))
        else:
            v.icon_alpha = 255
        if self.cfg.fade_path:
            v.path_alpha = max(0, int(255 * (1.0 - elapsed / 30.0)))
        else:
            v.path_alpha = 0

    def _update_normal(self, ty: Typhoon, ct: float, paused: bool) -> None:
        pts = ty.pts
        if len(pts) == 1:
            ty._mark_finished(ct)
        elif ty.fin:
            if ct - ty.ft >= 500:
                ty.fin = False
                if self.cfg.ac and self.repo.tys:
                    self.repo.cti = (self.repo.cti + 1) % len(self.repo.tys)
                    self.repo.current_typhoon().rst()
        else:
            if not ty.v.ipos and ty.ci < len(pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.cfg.sp, paused)
        ty.cace = (ty.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[ty.ci]['ace'] if pts and ty.ci < len(pts) else 0.0))

    def _update_season(self, ty: Typhoon, ct: float, paused: bool,
                       season_ctrl: Optional[SeasonController] = None) -> None:
        pts = ty.pts
        if season_ctrl and ty.ci == 0 and ty.last_ace_ci == -1:
            pt = pts[0]
            if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == season_ctrl.current_ace_year:
                if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                    season_ctrl.csa += pt['pace']
            ty.last_ace_ci = 0
        if len(pts) == 1:
            ty._mark_finished(ct)
            ty.sf = True
        elif not ty.fin:
            if not ty.v.ipos and ty.ci < len(pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.cfg.sp, paused)
            if not paused and season_ctrl and ty.ci > ty.last_ace_ci:
                for i in range(ty.last_ace_ci + 1, ty.ci + 1):
                    pt = pts[i]
                    if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == season_ctrl.current_ace_year:
                        if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                            season_ctrl.csa += pt['pace']
                ty.last_ace_ci = ty.ci
            if ty.fin:
                ty.sf = True
                ty.act = False

        ty.cace = (ty.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[ty.ci]['ace'] if pts else 0.0))

    def _compute_interpolated_csa(self,
                                   season_ctrl: SeasonController) -> float:
        cur_year = season_ctrl.current_ace_year
        total = 0.0
        for ty in self.repo.tys:
            if not ty.pts:
                continue
            for i, pt in enumerate(ty.pts):
                if pt.ace_year != cur_year:
                    continue
                if not self.ace_engine.point_in_limit(pt.la, pt.lo):
                    continue
                if i <= ty.ci:
                    total += pt.pace
                elif i == ty.ci + 1 and ty.act and ty.ss and not ty.sf and not ty.fin:
                    pt0 = ty.points_time[ty.ci]
                    pt1 = ty.points_time[ty.ci + 1]
                    if pt1 > pt0 and pt.pace > 0:
                        t = (ty.at - pt0) / (pt1 - pt0)
                        t = max(0.0, min(1.0, t))
                        total += pt.pace * t
                    break
                else:
                    break
        return total

    def _update_edit(self, ty: Typhoon, ct: float, paused: bool) -> None:
        pts = ty.pts
        if len(pts) == 1:
            ty._mark_finished(ct)
        elif ty.fin:
            if ct - ty.ft >= 500:
                ty.fin = False
                self._pl = False
                ty.rst()
        else:
            if not ty.v.ipos and ty.ci < len(pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.cfg.sp, paused)
        ty.cace = (ty.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[ty.ci]['ace'] if pts and ty.ci < len(pts) else 0.0))

    def _check_landfall(self, ty: Typhoon, ct: float,
                        season_ctrl: Optional[SeasonController] = None) -> None:
        if not ty.act:
            return
        ace_limit_mode = self.cfg.ace_limit_mode
        if ace_limit_mode == "basin" and self.cfg.ace_limit_basin:
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None and not self.repo._ty_in_filter_basin(ty, area):
                pos = ty.cpos()
                if pos:
                    x, y = self.view.latlon_to_screen(pos['la'], pos['lo'])
                    w, h = self.view.screen_width, self.view.map_height
                    if 0 <= x < w and 0 <= y < h:
                        if self.map_mgr.land_img is not None:
                            ty.v.last_on_land = self.map_mgr.is_land_at_screen(x, y)
                return
        pos = ty.cpos()
        if not pos:
            return
        x, y = self.view.latlon_to_screen(pos['la'], pos['lo'])
        w, h = self.view.screen_width, self.view.map_height
        if not (0 <= x < w and 0 <= y < h):
            return
        if self.map_mgr.land_img is None:
            return
        is_land = self.map_mgr.is_land_at_screen(x, y)
        v = ty.v
        if is_land and not v.last_on_land:
            cp = ty.cp()
            if cp:
                adv_x, adv_y = self.view.latlon_to_screen(cp['la'], cp['lo'])
                adv_on_land = (0 <= adv_x < w and 0 <= adv_y < h
                               and self.map_mgr.is_land_at_screen(adv_x, adv_y))
                if adv_on_land and ty.ci > 0:
                    prev_pt = ty.pts[ty.ci - 1]
                    landfall_wind = prev_pt.get('w', cp.get('w', 0))
                    landfall_st = prev_pt.get('st', cp.get('st', ''))
                else:
                    landfall_wind = cp.get('w', 0)
                    landfall_st = cp.get('st', '')
                strength = self.repo.get_strength_category(landfall_wind, landfall_st)
                ace_year = season_ctrl.current_ace_year if season_ctrl else 2000
                self.landfall_records.append({
                    'name': self.repo.get_display_name(ty),
                    'wind': landfall_wind,
                    'year': ace_year,
                    'basin': ty.basin,
                    'la': pos['la'],
                    'lo': pos['lo'],
                })
                if ty.ci != 0:
                    img1, img2 = self.res_mgr.get_landfall_images(strength)
                    if img1 and img2:
                        self.effects.append(LandfallEffect(
                            strength, pos['lo'], pos['la'], img1, img2, ct,
                            self.view.latlon_to_screen))
                    sound = self.res_mgr.get_sound(strength)
                    if sound:
                        sound.set_volume(self.cfg.volume)
                        sound.play()
        v.last_on_land = is_land
