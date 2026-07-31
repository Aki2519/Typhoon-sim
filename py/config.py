# py/config.py
"""应用配置 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Dict, Optional, Tuple
import json
import os
import logging

from .constants import HEMISPHERE_NORTH, ICON_SET_DEFAULT

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    mlo: float = 100.0
    Mlo: float = 180.0
    mla: float = 0.0
    Mla: float = 50.0
    map_corner_mode: bool = False
    cmp: Optional[str] = None

    ac: bool = True
    md: str = "normal"
    sp: float = 1.0
    mis: float = 0.1
    mas: float = 10.0

    show_info_box_normal: bool = True
    show_info_box_season: bool = True
    screen_width: int = 1360
    screen_height: int = 885
    window_topmost: bool = False

    ace_display_mode: str = "progress_bar"
    ace_geo_limit_enabled: bool = False
    ace_limit_mode: str = "none"
    ace_limit_basin: str = ""
    ace_min_lon: float = 100.0
    ace_max_lon: float = 180.0
    ace_min_lat: float = 0.0
    ace_max_lat: float = 90.0

    land_min_lon: float = 90.0
    land_max_lon: float = 190.0
    land_min_lat: float = -10.0
    land_max_lat: float = 80.0

    main_rotation_speed: float = 1.0
    level3_rotation_speed: float = 1.5
    volume: float = 0.6
    name_display_mode: int = 0
    point_name_mode: bool = False
    hemisphere: str = HEMISPHERE_NORTH
    point_size: int = 100
    icon_size: int = 100
    name_size: int = 100
    peak_label_size: int = 100
    fix_icon_point_size: bool = False
    fade_typhoon: bool = True
    fade_path: bool = True
    fade_path_mode: str = "fade"
    smooth_path: bool = False
    smooth_path_segments: int = 10
    smooth_path_mode: str = "monotone"
    path_mode: str = "markers"
    show_future_path: bool = True
    ace_interpolated: bool = False
    show_fps: bool = False
    fps_cap: int = 120
    monthly_summary: bool = True

    show_ri_effect: bool = True
    show_ace_bar: bool = True
    show_ace_total: bool = True

    disable_dpi_scaling: bool = True

    icon_set: str = ICON_SET_DEFAULT
    color_scheme: int = 1
    show_summary: bool = True
    summary_transparent: bool = True
    basin_filter_enabled: bool = True

    tn: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def _serialize_fields(cls) -> Tuple[str, ...]:
        return tuple(f.name for f in fields(cls) if not f.name.startswith('_'))

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning(f"配置加载失败: {path}: {e}")
            return cls()
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for k, v in raw.items():
            fld = known.get(k)
            if fld is None:
                continue
            kwargs[k] = cls._coerce(fld, v)
        try:
            return cls(**kwargs)
        except Exception as e:
            logger.warning(f"配置字段非法，使用默认值: {e}")
            return cls()

    @staticmethod
    def _coerce(fld, v):
        if v is None:
            return fld.default
        t = fld.type
        if t == 'bool':
            return v if isinstance(v, bool) else fld.default
        if t == 'int':
            if isinstance(v, bool):
                return fld.default
            try:
                return int(v)
            except (TypeError, ValueError):
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return fld.default
        if t == 'float':
            try:
                return float(v)
            except (TypeError, ValueError):
                return fld.default
        if t.startswith('Dict['):
            return v if isinstance(v, dict) else fld.default
        if t == 'Optional[str]':
            return v if v is None or isinstance(v, str) else fld.default
        return v if isinstance(v, str) else fld.default

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({k: getattr(self, k) for k in self._serialize_fields()}, f, indent=2)

    def update_from(self, other: "AppConfig") -> None:
        for fld in fields(self):
            setattr(self, fld.name, getattr(other, fld.name))
