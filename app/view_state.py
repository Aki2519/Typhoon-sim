# py/view_state.py
"""地图视图状态：屏幕尺寸、坐标转换、台风屏幕点更新。"""
from __future__ import annotations

import pygame
from typing import List, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .typhoon import Typhoon
    from .resource_manager import MapManager
    from .config import AppConfig


class ViewState:
    __slots__ = ('screen_width', 'screen_height', 'map_height',
                 '_mlo', '_Mlo', '_mla', '_Mla',
                 '_map_mgr', '_cfg')

    def __init__(self, screen_width: int, screen_height: int, map_height: int,
                 map_mgr: MapManager, cfg: AppConfig) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.map_height = map_height
        self._map_mgr = map_mgr
        self._cfg = cfg

    @property
    def mlo(self) -> float:
        return self._cfg.mlo

    @mlo.setter
    def mlo(self, v: float) -> None:
        self._cfg.mlo = v

    @property
    def Mlo(self) -> float:
        return self._cfg.Mlo

    @Mlo.setter
    def Mlo(self, v: float) -> None:
        self._cfg.Mlo = v

    @property
    def mla(self) -> float:
        return self._cfg.mla

    @mla.setter
    def mla(self, v: float) -> None:
        self._cfg.mla = v

    @property
    def Mla(self) -> float:
        return self._cfg.Mla

    @Mla.setter
    def Mla(self, v: float) -> None:
        self._cfg.Mla = v

    def latlon_to_screen(self, la: float, lo: float) -> Tuple[int, int]:
        if self._map_mgr.map_view is None:
            return 0, 0
        return self._map_mgr.map_view.geo_to_screen(lo, la)

    def screen_to_latlon(self, x: int, y: int) -> Tuple[float, float]:
        if self._map_mgr.map_view is None:
            return 0.0, 0.0
        lon, lat = self._map_mgr.map_view.screen_to_geo(x, y)
        return lat, lon

    def update_screen_points(self, tys: List[Typhoon],
                              edit_typhoon: Optional[Typhoon] = None) -> None:
        f = self.latlon_to_screen
        sw, sh = self.screen_width, self.map_height
        view_rect = pygame.Rect(-50, -50, sw + 100, sh + 100)
        for ty in tys:
            ty.update_screen_points(f, view_rect)
            for attr in ('_cached_max_wind', '_cached_peak_info', '_cached_max_wind_color', '_cached_peaks', '_cached_name_colors'):
                if hasattr(ty, attr):
                    delattr(ty, attr)
        if edit_typhoon:
            edit_typhoon.update_screen_points(f)
            for attr in ('_cached_max_wind', '_cached_peak_info', '_cached_max_wind_color', '_cached_peaks', '_cached_name_colors'):
                if hasattr(edit_typhoon, attr):
                    delattr(edit_typhoon, attr)

    def sync_land_state(self, tys: List[Typhoon]) -> None:
        """按地理坐标同步各台风的陆上状态（视图无关，与登陆判定一致）。"""
        map_mgr = self._map_mgr
        if map_mgr._load_land_orig() is None:
            return
        for ty in tys:
            pos = ty.cpos()
            if not pos:
                continue
            ty.v.last_on_land = map_mgr.is_land_at_geo(pos['la'], pos['lo'])
