# py/ty_sim_mixins/track_mixin.py
"""镜头跟踪 Mixin：跟随台风平移地图，结束后自动总览完整路径。

风季(MODE_SEASON): 跟随指定/全部活跃台风，全部结束后总览其路径。
正常(MODE_NORMAL): 跟随"当前播放台风"([ ] 切换/自动连播后自动改跟新台风)，
                   该台风播完后自动完整显示其路径；不提供键盘快捷键(仅按钮)。
"""
from __future__ import annotations

import math
import pygame

# 平滑跟随系数(60fps 每帧向目标位置插值的比例; 实际按 dt 折算, 帧率无关)
_TRACK_SMOOTH_K = 0.08
# 单台风跟随: 目标始终为台风当前位置; 全部跟随: 所有活跃台风位置 bbox 中心
# 视差死区(px): 目标与视中心偏差小于该值时不再平移(避免台风静止/镜头到位后每帧无谓重投影)
_TRACK_PAN_DEADZONE = 0.25


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
    if n == 1:
        return 0.0     # 单点无跨度; 返回 360 会让总览缩到全球
    gaps = [(srt[(i + 1) % n] - srt[i]) % 360.0 for i in range(n)]
    return 360.0 - max(gaps)


class TySimTrackMixin:
    """镜头跟踪: tracking_mode = 'off' | 'single' | 'all'。"""

    def _init_tracking(self) -> None:
        self.tracking_mode: str = 'off'
        self.tracking_typhoon = None
        self._tracking_seen: set = set()
        self._tracking_overview_done: bool = False
        # 正常模式: 当前被跟踪台风(可能已结束, 供播完后总览完整路径)
        self._tracked_ty = None

    def set_tracking(self, mode: str, typhoon=None) -> None:
        """由跟踪选择对话框调用: mode = 'off' | 'single' | 'all'。"""
        self.tracking_mode = mode
        self.tracking_typhoon = typhoon
        self._tracking_seen.clear()
        self._tracking_overview_done = False
        self._tracked_ty = None

    def tracking_label(self) -> str:
        """用于界面指示的小标签; 未跟踪返回空串。"""
        if self.tracking_mode == 'off':
            return ""
        if self.md == self.MODE_NORMAL:
            # 正常模式: 跟随的是当前播放台风, 名称动态显示
            ty = self.current_typhoon()
            if ty is not None:
                return f"镜头跟踪: {self.get_display_name(ty)}"
            return ""
        if self.tracking_mode == 'all':
            return "镜头跟踪: 全部活跃"
        if self.tracking_typhoon is not None:
            return f"镜头跟踪: {self.get_display_name(self.tracking_typhoon)}"
        return ""

    def _track_active_typhoons(self) -> list:
        """当前应被跟踪的台风(未出现/已结束的不跟踪)。"""
        if self.md == self.MODE_NORMAL:
            # 正常模式: 始终跟踪当前播放台风([ ] 切换/自动连播后自动改跟新台风)
            if self.tracking_mode == 'off':
                return []
            ty = self.current_typhoon()
            if ty is None or not ty.pts:
                # 当前台风无报点(或已被过滤): 清掉旧目标, 否则播完后会总览上一个台风
                self._tracked_ty = None
                return []
            self._tracked_ty = ty
            if ty.fin:
                return []
            return [ty]
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

    def _update_tracking(self, ct: int, dt: float = 0.0) -> None:
        if self.md not in (self.MODE_SEASON, self.MODE_NORMAL) or self.tracking_mode == 'off':
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
                self._track_pan_to(mv, clon, clat, ct, dt)
            return

        # 全部被跟踪台风已完成 → 自动总览其路径(仅触发一次)
        if self._tracking_overview_done:
            return
        if self.md == self.MODE_NORMAL:
            # 正常模式: 总览"当前被跟踪台风"(刚播完的那个)的完整路径
            tys = [self._tracked_ty] if self._tracked_ty is not None else []
        else:
            tys = list(self._tracking_seen)
        if not tys:
            return
        self._tracking_overview_done = True
        lons, lats = [], []
        for ty in tys:
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
            # set_view_region 直接改了 scale/view: 屏幕点必须失效重投影,
            # 否则总览帧仍用旧坐标(路径/图标停在原处)
            self.invalidate_screen_points_lazy()

    def _track_pan_to(self, mv, clon: float, clat: float, ct: int = 0, dt: float = 0.0) -> None:
        """向目标中心平滑平移(带经度环绕感知; 逐帧、帧率无关, 小死区)。
        必须逐帧应用: 若按固定间隔节流, 相机与台风运动失去同步, 台风会在屏幕上抖动。"""
        px = (clon - mv.lon_min) * mv._scale_x
        py = (mv.lat_max - clat) * mv._scale_y
        tx = (px - mv.screen_width / (2.0 * mv.scale)) % mv.img_w
        ty_target = py - mv.screen_height / (2.0 * mv.scale)
        # 环绕感知的最短方向增量
        dx = (tx - mv.view_x + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        dy = ty_target - mv.view_y
        # 死区: 台风静止且镜头已对准时不产生任何视图改动(避免无谓的地图重绘/重投影)
        if abs(dx) < _TRACK_PAN_DEADZONE and abs(dy) < _TRACK_PAN_DEADZONE:
            return
        # 帧率无关平滑: 以 60fps 的 _TRACK_SMOOTH_K 为基准按 dt 折算时间常数
        if dt > 0:
            k = 1.0 - (1.0 - _TRACK_SMOOTH_K) ** (dt * 60.0)
        else:
            k = _TRACK_SMOOTH_K
        old_vx, old_vy = mv.view_x, mv.view_y
        mv.view_x = (mv.view_x + dx * k) % mv.img_w
        mv.view_y += dy * k
        mv._clamp_view_y()
        # 上下边界钳制后实际位移可能为 0(目标在屏幕外): 此时若仍标记视图脏,
        # 镜头贴边后会每帧无谓重投影。只在"实际一点都没动"时回退本帧,
        # 正常接近目标仍走上面的死区判定, 不会提前停住
        adx = (mv.view_x - old_vx + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        ady = mv.view_y - old_vy
        if abs(adx) < 1e-9 and abs(ady) < 1e-9:
            mv.view_x, mv.view_y = old_vx, old_vy
            return
        self._view_dirty = True
        # 视图变化 → 屏幕坐标过期,由绘制惰性重投影
        self.invalidate_screen_points_lazy()
