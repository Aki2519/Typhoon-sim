# py/config.py
"""应用配置 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Dict, Optional, Tuple
import json
import os
import logging

from .constants import HEMISPHERE_NORTH

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    mlo: float = 100.0
    Mlo: float = 180.0
    mla: float = 0.0
    Mla: float = 50.0
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
    hemisphere: str = HEMISPHERE_NORTH
    point_size: int = 100
    icon_size: int = 100
    fade_typhoon: bool = True
    fade_path: bool = True
    smooth_path: bool = False
    smooth_path_segments: int = 10
    ace_interpolated: bool = False
    show_fps: bool = False
    monthly_summary: bool = True

    disable_dpi_scaling: bool = True

    tn: Dict[str, str] = field(default_factory=dict)

    _FIELDS: Tuple[str, ...] = (
        "mlo", "Mlo", "mla", "Mla", "cmp",
        "ac", "md", "sp", "mis", "mas",
        "show_info_box_normal", "show_info_box_season",
        "screen_width", "screen_height", "window_topmost",
        "ace_display_mode",
        "ace_geo_limit_enabled", "ace_limit_mode", "ace_limit_basin",
        "ace_min_lon", "ace_max_lon", "ace_min_lat", "ace_max_lat",
        "land_min_lon", "land_max_lon", "land_min_lat", "land_max_lat",
        "main_rotation_speed", "level3_rotation_speed", "volume",
        "name_display_mode", "hemisphere",
        "point_size", "icon_size",
        "fade_typhoon", "fade_path",
        "smooth_path", "smooth_path_segments",
        "ace_interpolated", "show_fps",
        "monthly_summary",
        "disable_dpi_scaling", "tn",
    )

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
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({k: getattr(self, k) for k in self._FIELDS}, f, indent=2)

    def update_from(self, other: "AppConfig") -> None:
        for fld in fields(self):
            setattr(self, fld.name, getattr(other, fld.name))
