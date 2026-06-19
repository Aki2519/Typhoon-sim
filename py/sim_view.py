# py/sim_view.py
"""对话框依赖的 SimView 协议。"""
from typing import Optional, Tuple, Protocol
import pygame


class SimView(Protocol):

    screen_width: int
    screen_height: int
    map_height: int

    tys: list
    cti: int
    edit_typhoon: Optional[object]

    md: str
    pl: bool
    hemisphere: str
    name_display_mode: int
    fade_typhoon: bool
    fade_path: bool

    ace_display_mode: str
    ace_geo_limit_enabled: bool
    ace_limit_mode: str
    ace_limit_basin: str
    ace_min_lon: float
    ace_max_lon: float
    ace_min_lat: float
    ace_max_lat: float
    current_ace_year: int
    csa: float
    tsa: float
    yad: dict
    sy: int
    sty: int
    edy: int
    st: str
    ste: float

    mis: float
    mas: float
    sp: float
    ac: bool
    show_info_box_normal: bool
    show_info_box_season: bool
    main_rotation_speed: float
    level3_rotation_speed: float
    volume: float
    point_size: int
    icon_size: int
    disable_dpi_scaling: bool
    window_topmost: bool

    res_mgr: object
    map_mgr: object
    dialog_mgr: object
    ace_engine: object

    dialog_page_cache: dict

    def get_display_name(self, ty) -> str: ...
    def latlon_to_screen(self, la: float, lo: float) -> Tuple[int, int]: ...
    def screen_to_latlon(self, x: int, y: int) -> Tuple[float, float]: ...
    def get_strength_category(self, wind: int, stype: str) -> str: ...
    def get_point_color(self, wind: int, stype: str) -> Tuple[int, int, int]: ...
    def darken_color(self, c, factor: float = 0.6) -> tuple: ...
    def tint_image(self, img, color) -> pygame.Surface: ...
    def show_error(self, message: str) -> None: ...
    def save_config(self) -> None: ...
    def current_typhoon(self) -> Optional[object]: ...
    def get_ace_year(self, dt) -> int: ...
    def get_ace_year_start_end(self, year: int) -> tuple: ...
    def calc_accumulated_ace_up_to(self, y: int, m: int, d: int, h: int) -> float: ...
    def reload_typhoons(self) -> None: ...
    def recalc_all_ace(self) -> None: ...
    def update_all_screen_points(self) -> None: ...
    def _refresh_ace_data(self) -> None: ...
    def _point_in_ace_limit(self, la: float, lo: float) -> bool: ...
    def toggle_window_topmost(self) -> None: ...
    def get_next_time_for_typhoon(self, ty) -> str: ...