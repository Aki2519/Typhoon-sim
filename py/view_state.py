# py/view_state.py
"""地图视图状态：屏幕尺寸、坐标转换、台风屏幕点更新。"""
from __future__ import annotations

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
        for ty in tys:
            ty.update_screen_points(f)
            if hasattr(ty, '_cached_max_wind_color'):
                delattr(ty, '_cached_max_wind_color')
        if edit_typhoon:
            edit_typhoon.update_screen_points(f)
            if hasattr(edit_typhoon, '_cached_max_wind_color'):
                delattr(edit_typhoon, '_cached_max_wind_color')

    def invalidate_all_path_caches(self, tys: List[Typhoon]) -> None:
        for ty in tys:
            ty.v._path_cache_full = None
            ty.v._path_cache_traversed = None
            ty.v._last_rendered_ci = -1
            ty.v._path_cache_key = ()
            ty.v._path_cache_drag_surf = None
            ty.v._path_cache_drag_key = ()

    def sync_land_state(self, tys: List[Typhoon]) -> None:
        map_mgr = self._map_mgr
        if map_mgr.land_img is None:
            map_mgr.update_land_mask()
        if map_mgr.land_img is None:
            return
        f = self.latlon_to_screen
        for ty in tys:
            pos = ty.cpos()
            if not pos:
                continue
            x, y = f(pos['la'], pos['lo'])
            if 0 <= x < self.screen_width and 0 <= y < self.map_height:
                ty.v.last_on_land = map_mgr.is_land_at_screen(x, y)
