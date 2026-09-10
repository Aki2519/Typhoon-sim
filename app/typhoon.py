# py/typhoon.py
"""台风数据类。南半球通过镜像+逆时针角度实现顺时针视觉。
方法已拆分到 typhoon_data / typhoon_sim / typhoon_render mixin。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
    import pygame
    from typing import List, Dict, Optional, Tuple, Any
    from .ty_sim import TySim

from .typhoon_data import TyphoonDataMixin
from .cache_store import SurfaceCache
from .typhoon_sim import TyphoonSimMixin
from .typhoon_render import TyphoonRenderMixin


@dataclass(slots=True)
class TrackPoint:
    t: str = ""
    la: float = 0.0
    lo: float = 0.0
    w: int = 0
    p: int = 0
    st: str = ""
    ace: float = 0.0
    pace: float = 0.0
    name: str = ""
    official: bool = True
    ace_year: int = 0
    color: Tuple[int, int, int] = (128, 128, 128)
    color_dim: Tuple[int, int, int] = (77, 77, 77)
    cat: str = "TD"

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def update(self, mapping: dict) -> None:
        for k, v in mapping.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


_VIEW_FIELDS = frozenset({
    "ra", "sa", "sa3", "sa4", "sa5",
    "ipos", "rot_dir", "mirror", "last_on_land",
    "icon_alpha", "path_alpha",
    "screen_points", "bbox", "_sp_ver",
    "map_points", "smooth_map_points",   # 地图空间(视图无关)的报点/平滑点
    "_sp_anchor",          # 屏幕点视图锚点(vx_int, vy_int, scale, bottom_align, sw, mh): 纯平移时按锚点整页位移
    "_img_cache",
    "_path_cache_full", "_path_cache_traversed", "_last_rendered_ci",
    "_path_cache_key", "_path_cache_blit",
    "_path_cache_drag_surf", "_path_cache_drag_key", "_path_cache_drag_pos",
    "_path_cache_drag_future", "_path_cache_drag_trav",
    "_path_cache_drag_trav_ci", "_path_cache_drag_trav_key",
    "_path_cache_drag_geom",
    "_path_cache_drag_local_line", "_path_cache_drag_local_marks",
    "smooth_screen_points", "_smooth_arc_lengths",
    "_smcy_frame", "_smcy_last_cat", "_smcy_last_ticks",
    "_last_ri_at", "_ri_armed",
    "_spawn_time",
})


class TyphoonView:
    __slots__ = tuple(_VIEW_FIELDS)

    def __init__(self) -> None:
        self.ra: float = 0.0
        self.sa: float = 0.0
        self.sa3: float = 0.0
        self.sa4: float = 0.0
        self.sa5: float = 0.0
        self.ipos: Optional[Dict[str, float]] = None
        self.rot_dir: int = 1
        self.mirror: bool = False
        self.last_on_land: bool = False
        self.icon_alpha: int = 255
        self.path_alpha: int = 255
        self.screen_points: List[Tuple[int, int]] = []
        self.map_points: List[Tuple[int, int]] = []
        self.smooth_map_points: List[Tuple[int, int]] = []
        self.bbox: Optional[pygame.Rect] = None
        self._sp_ver: int = -1
        self._sp_anchor = None
        # 旋转/镜像/着色结果缓存: 按字节封顶(旧的条数上限在图标放大后可达数百 MB)
        self._img_cache = SurfaceCache("rot", 12 * 1024 * 1024, 720)
        self._path_cache_full: Optional[pygame.Surface] = None
        self._path_cache_traversed: Optional[pygame.Surface] = None
        self._last_rendered_ci: int = -1
        self._path_cache_key: tuple = ()
        self._path_cache_drag_surf: Optional[pygame.Surface] = None
        self._path_cache_drag_key: tuple = ()
        self._path_cache_drag_pos: Tuple[int, int] = (0, 0)
        self._path_cache_drag_future: Optional[pygame.Surface] = None
        self._path_cache_drag_trav: Optional[pygame.Surface] = None
        self._path_cache_drag_trav_ci: int = -1
        self._path_cache_drag_trav_key: tuple = ()
        self._path_cache_drag_geom: Optional[tuple] = None
        self._path_cache_drag_local_line: list = []
        self._path_cache_drag_local_marks: list = []
        self.smooth_screen_points: List[Tuple[int, int]] = []
        self._smooth_arc_lengths: List[float] = []
        self._smcy_frame: int = 0
        self._smcy_last_cat: str = ""
        self._smcy_last_ticks: int = 0
        self._last_ri_at: float = -999.0
        self._ri_armed: bool = True
        self._spawn_time: int = 0


class Typhoon(TyphoonDataMixin, TyphoonSimMixin, TyphoonRenderMixin):
    __slots__ = (
        'b', 'n', 'name', 'cust', 'sname', 'basin',
        'pts', 'ci', 'act',
        'tace', 'cace', 'cumace', 'ist', 'idur', 'fin', 'ft',
        'ss', 'sf', 'at', 'lut',
        'sim', 'filepath', 'start_time',
        'last_ace_ci', 'points_time', 'points_dt',
        'format_type', 'original_jtwc_source',
        '_undo_stack', '_redo_stack', '_last_partial_csa', '_last_partial_ci',
        'finish_time',
        '_cached_max_wind_color', '_cached_name_colors', '_cached_peaks',
        '_cached_landfalls',
        '_in_filter_basin', '_filter_basin_checked',
        '_v',
    )

    def __init__(self, b: str, n: str) -> None:
        self.b: str = b
        self.n: str = n
        self.name: str = f"{b}{n}"
        self.cust: str = ""
        self.sname: str = ""
        self.basin: str = ""
        self.pts: List[TrackPoint] = []
        self.ci: int = 0
        self.act: bool = True
        self.tace: float = 0.0
        self.cace: float = 0.0
        self.cumace: float = 0.0
        self.ist: int = 0
        self.idur: float = 0.5
        self.fin: bool = False
        self.ft: float = 0
        self.ss: bool = False
        self.sf: bool = False
        self.at: float = 0.0
        self.lut: float = 0
        self.sim: Optional[TySim] = None
        self.filepath: Optional[str] = None
        self.start_time: Optional[str] = None
        self.last_ace_ci: int = -1
        self.points_time: List[float] = []
        self.points_dt: List[datetime.datetime] = []
        self.format_type: str = "simple_bdeck"
        self.original_jtwc_source: Optional[str] = None
        self._undo_stack: List[List[TrackPoint]] = []
        self._redo_stack: List[List[TrackPoint]] = []
        self._last_partial_csa: float = 0.0
        self._last_partial_ci: int = -1
        self.finish_time: float = 0
        self._v: TyphoonView = TyphoonView()

    def __getattr__(self, name: str):
        if name in _VIEW_FIELDS:
            try:
                return getattr(object.__getattribute__(self, '_v'), name)
            except AttributeError:
                pass
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        if name in _VIEW_FIELDS:
            try:
                v = object.__getattribute__(self, '_v')
            except AttributeError:
                raise AttributeError(
                    f"Cannot set view attribute '{name}' before _v is initialized"
                ) from None
            else:
                setattr(v, name, value)
                return
        object.__setattr__(self, name, value)

    @property
    def v(self) -> TyphoonView:
        return self._v

    def __repr__(self) -> str:
        return f"Typhoon(name={self.name}, pts={len(self.pts)})"


Typhoon.ap = Typhoon.add_point
Typhoon.cp = Typhoon.current_point
Typhoon.sm = Typhoon.start_move
Typhoon.um = Typhoon.update_move
Typhoon.cpos = Typhoon.current_position
Typhoon.us = Typhoon.update_rotation
Typhoon.rst = Typhoon.reset
