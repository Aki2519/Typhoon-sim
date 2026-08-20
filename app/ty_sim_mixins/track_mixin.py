# py/ty_sim_mixins/track_mixin.py
"""风季镜头跟踪 Mixin：跟随指定/全部活跃台风平移地图，结束后总览路径。"""
from __future__ import annotations

import math
import pygame

# 平滑跟随系数(每帧向目标位置插值的比例)
_TRACK_SMOOTH_K = 0.08
# 单台风跟随: 目标始终为台风当前位置; 全部跟随: 所有活跃台风位置 bbox 中心
_PAN_FRAME_MS = 33   # 平移导致的重投影按帧进行,无需节流


def _circular_lon_center(lons: list) -> float:
    """经度环形中心(跨 0°/360° 时取正确圆心)。"""
    if not lons:
        return 180.0
    cx = sum(math.cos(math.radians(lo)) for lo in lons) / len(lons)
    cy = sum(math.sin(math.radians(lo)) for lo in lons) / len(lons)
    return math.degrees(math.atan2(cy, cx)) % 360.0


def _lon_span_wrap(lons: list) -> float:
    """经度覆盖跨度(0-360 环形,取最大缺口反面的跨度)。"""
    if not lons:
        return 360.0
    srt = sorted(lons)
    n = len(srt)
    gaps = [(srt[(i + 1) % n] - srt[i]) % 360.0 for i in range(n)]
    return 360.0 - max(gaps)


class TySimTrackMixin:
    """镜头跟踪: tracking_mode = 'off' | 'single' | 'all'。"""

    def _init_tracking(self) -> None:
        self.tracking_mode: str = 'off'
        self.tracking_typhoon = None
        self._tracking_seen: set = set()
        self._tracking_overview_done: bool = False

    def set_tracking(self, mode: str, typhoon=None) -> None:
        """由跟踪选择对话框调用: mode = 'off' | 'single' | 'all'。"""
        self.tracking_mode = mode
        self.tracking_typhoon = typhoon
        self._tracking_seen.clear()
        self._tracking_overview_done = False

    def tracking_label(self) -> str:
        """用于界面指示的小标签; 未跟踪返回空串。"""
        if self.tracking_mode == 'off':
            return ""
        if self.tracking_mode == 'all':
            return "镜头跟踪: 全部活跃"
        if self.tracking_typhoon is not None:
            return f"镜头跟踪: {self.get_display_name(self.tracking_typhoon)}"
        return ""

    def _track_active_typhoons(self) -> list:
        """当前应被跟踪的台风(未出现/已结束的不跟踪)。"""
        if self.tracking_mode == 'single':
            ty = self.tracking_typhoon
            if ty is not None and ty.act and not ty.sf and ty.pts:
                self._tracking_seen.add(ty)
                return [ty]
            return []
        out = []
        for ty in self.tys:
            if ty.act and not ty.sf and ty.pts:
                self._tracking_seen.add(ty)
                out.append(ty)
        return out

    def _update_tracking(self, ct: int) -> None:
        if self.md != self.MODE_SEASON or self.tracking_mode == 'off':
            return
        mv = self.map_mgr.map_view
        if mv is None:
            return

        active = self._track_active_typhoons()
        if active:
            self._tracking_overview_done = False
            lons, lats = [], []
            for ty in active:
                pos = ty.cpos()
                if pos:
                    lons.append(pos['lo'])
                    lats.append(pos['la'])
            if lons:
                clon = _circular_lon_center(lons)
                clat = sum(lats) / len(lats)
                self._track_pan_to(mv, clon, clat)
            return

        # 全部被跟踪台风已完成 → 总览其路径(仅触发一次)
        if not self._tracking_overview_done and self._tracking_seen:
            self._tracking_overview_done = True
            lons, lats = [], []
            for ty in self._tracking_seen:
                if ty.pts:
                    for p in ty.pts:
                        lons.append(p['lo'])
                        lats.append(p['la'])
            if lons:
                clon = _circular_lon_center(lons)
                span = max(_lon_span_wrap(lons), 1e-3)
                half = span / 2.0
                lat0, lat1 = min(lats), max(lats)
                # 防 set_view_region(范围外)对 0 跨度做除零;给最小可见尺度
                if lat1 - lat0 < 1e-3:
                    lat0 = max(-90.0, lat0 - 1e-3)
                    lat1 = min(90.0, lat0 + 1e-3)
                mv.set_view_region((clon - half) % 360.0, clon + half,
                                   lat0, lat1)
                self._view_dirty = True

    def _track_pan_to(self, mv, clon: float, clat: float) -> None:
        """向目标中心平滑平移(每帧一次,带经度环绕感知)。"""
        px = (clon - mv.lon_min) * mv._scale_x
        py = (mv.lat_max - clat) * mv._scale_y
        tx = (px - mv.screen_width / (2.0 * mv.scale)) % mv.img_w
        ty_target = py - mv.screen_height / (2.0 * mv.scale)
        # 环绕感知的最短方向增量
        dx = (tx - mv.view_x + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        dy = ty_target - mv.view_y
        mv.view_x = (mv.view_x + dx * _TRACK_SMOOTH_K) % mv.img_w
        mv.view_y += dy * _TRACK_SMOOTH_K
        mv._clamp_view_y()
        self._view_dirty = True
        # 视图变化 → 屏幕坐标过期,由绘制惰性重投影
        self.invalidate_screen_points_lazy()
