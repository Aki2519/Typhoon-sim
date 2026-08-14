# py/config.py
"""应用配置 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, MISSING
from typing import Dict, Optional, Tuple
import json
import math
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
    edit_snap_step: float = 0.1
    show_edit_point_labels: bool = False
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
    dark_mode: bool = True

    icon_set: str = ICON_SET_DEFAULT
    color_scheme: int = 1
    show_summary: bool = True
    summary_transparent: bool = True
    basin_filter_enabled: bool = True

    tn: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def _serialize_fields(cls) -> Tuple[str, ...]:
        return tuple(f.name for f in fields(cls) if not f.name.startswith('_'))

    def update_from(self, other: "AppConfig") -> None:
        """用另一个配置实例覆盖当前实例的全部字段（py/→app/ 重命名期间曾丢失,已恢复）。"""
        for fld in fields(self):
            setattr(self, fld.name, getattr(other, fld.name))

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
        if not isinstance(raw, dict):
            logger.warning(f"配置顶层不是对象: {path}")
            return cls()
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for k, v in raw.items():
            fld = known.get(k)
            if fld is None:
                continue
            kwargs[k] = cls._coerce(fld, v)
        try:
            cfg = cls(**kwargs)
        except Exception as e:
            logger.warning(f"配置字段非法，使用默认值: {e}")
            return cls()
        # 手改坏值归一化：范围必须有效，否则启动时 (Mlo-mlo) 除零（B2 残留）
        if cfg.Mlo <= cfg.mlo or cfg.Mlo - cfg.mlo < 0.1:
            cfg.mlo, cfg.Mlo = min(cfg.mlo, cfg.Mlo), max(cfg.mlo, cfg.Mlo)
            if cfg.Mlo - cfg.mlo < 0.1:
                cfg.mlo, cfg.Mlo = 100.0, 180.0
        if cfg.Mla <= cfg.mla or cfg.Mla - cfg.mla < 0.1:
            cfg.mla, cfg.Mla = min(cfg.mla, cfg.Mla), max(cfg.mla, cfg.Mla)
            if cfg.Mla - cfg.mla < 0.1:
                # 与类声明默认值保持一致
                cfg.mla, cfg.Mla = 0.0, 50.0
        if cfg.ace_max_lon < cfg.ace_min_lon:
            cfg.ace_min_lon, cfg.ace_max_lon = cfg.ace_max_lon, cfg.ace_min_lon
        if cfg.ace_max_lat < cfg.ace_min_lat:
            cfg.ace_min_lat, cfg.ace_max_lat = cfg.ace_max_lat, cfg.ace_min_lat
        # 陆地范围同样需要保持 max≥min,避免未来下游 (max-min) 除零
        if cfg.land_max_lon < cfg.land_min_lon:
            cfg.land_min_lon, cfg.land_max_lon = cfg.land_max_lon, cfg.land_min_lon
        if cfg.land_max_lat < cfg.land_min_lat:
            cfg.land_min_lat, cfg.land_max_lat = cfg.land_max_lat, cfg.land_min_lat
        return cfg

    @staticmethod
    def _default_for(fld) -> object:
        """安全默认值:优先 default,其次 default_factory,最后类型兜底(N8)。"""
        if fld.default is not MISSING:
            return fld.default
        if fld.default_factory is not MISSING:
            return fld.default_factory()
        return None

    @staticmethod
    def _coerce(fld, v):
        if v is None:
            return AppConfig._default_for(fld)
        t = fld.type
        if t == 'bool':
            return v if isinstance(v, bool) else AppConfig._default_for(fld)
        if t == 'int':
            if isinstance(v, bool):
                return AppConfig._default_for(fld)
            # int() 遇到 nan/inf(JSON 字面量或 1e999)直接抛 OverflowError,
            # 先归一化再折算,避免 load() 崩溃(与下方 float NaN/Inf 守卫一致)
            try:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    fv = None
                if fv is not None and (math.isnan(fv) or math.isinf(fv)):
                    return AppConfig._default_for(fld)
                out = int(v)
            except (TypeError, ValueError, OverflowError):
                try:
                    out = int(float(v))
                except (TypeError, ValueError, OverflowError):
                    return AppConfig._default_for(fld)
            if fld.name in ('screen_width',):
                out = max(800, min(out, 3840))
            elif fld.name in ('screen_height',):
                out = max(600, min(out, 2160))
            elif fld.name in ('point_size', 'icon_size', 'name_size', 'peak_label_size'):
                out = max(1, out)
            elif fld.name == 'fps_cap':
                # 0 = 不限帧(settings UI 提供 60/120/0 三档)。首轮修复误用
                # max(1,·),导致手改/重启后 0 被钳成 1 FPS;必须放行 0。
                out = max(0, min(out, 500))
            elif fld.name == 'smooth_path_segments':
                # 与 settings.py 的 200 上限对齐:避免手改 config 注入
                # 每段 1000 点的昂贵样条(全站 ~8 处调用点)。<=0 仍钳 1。
                out = max(1, min(out, 200))
            return out
        if t == 'float':
            try:
                out = float(v)
            except (TypeError, ValueError):
                return AppConfig._default_for(fld)
            # NaN/Infinity 不会被比较捕获,显式拒绝避免下游除零/范围校验失效
            if math.isnan(out) or math.isinf(out):
                return AppConfig._default_for(fld)
            if fld.name in ('mla', 'Mla', 'ace_min_lat', 'ace_max_lat',
                            'land_min_lat', 'land_max_lat'):
                out = max(-90.0, min(out, 90.0))
            elif fld.name in ('mlo', 'Mlo', 'ace_min_lon', 'ace_max_lon',
                              'land_min_lon', 'land_max_lon'):
                out = max(0.0, min(out, 360.0))
            elif fld.name == 'mis':
                out = max(0.1, out)
            elif fld.name == 'mas':
                out = max(0.1, out)
            return out
        if t.startswith('Dict['):
            # N8: null/非 dict 用 default_factory 兜底,绝不返回 MISSING
            if isinstance(v, dict):
                return v
            return AppConfig._default_for(fld)
        if t == 'Optional[str]':
            return v if v is None or isinstance(v, str) else AppConfig._default_for(fld)
        if fld.name == 'hemisphere':
            # 下半球开关靠严格 == 'south' 判断；手改 config 写成 'North'/'S' 会
            # 静默让南半球季节、ACE 年界全部翻转,这里做小写 + 白名单归一化(防御)。
            if isinstance(v, str):
                v = v.strip().lower()
                return v if v in (HEMISPHERE_NORTH, 'south') else AppConfig._default_for(fld)
            return AppConfig._default_for(fld)
        return v if isinstance(v, str) else AppConfig._default_for(fld)

    def save(self, path: str) -> None:
        # N8: 保存失败仅记日志,不中断退出/模式切换
        try:
            tmp = path + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({k: getattr(self, k) for k in self._serialize_fields()}, f, indent=2)
            os.replace(tmp, path)
        except (TypeError, OSError, ValueError) as e:
            logger.warning(f"配置保存失败: {path}: {e}")
