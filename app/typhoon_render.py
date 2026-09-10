# py/typhoon_render.py
"""台风渲染 Mixin：坐标变换、旋转、屏幕点。"""
from __future__ import annotations

import pygame
from typing import TYPE_CHECKING
from .render_geom import normalize_chain
from .spline import build_spline, compute_arc_lengths

if TYPE_CHECKING:
    from .ty_sim import TySim

# ── geo 样条缓存（视图无关，点数据变化时失效） ──
_geo_spline_cache: dict = {}   # (id(pts), len, id(first_pt)) -> {(segs, mode): smooth_geo}


def _geo_spline_key(ty):
    if not ty.pts:
        return id(ty.pts)
    return (id(ty.pts), len(ty.pts), id(ty.pts[0]))


def _get_geo_spline(ty, segs: int, mode: str) -> list:
    """按 (pts 标识, segs, mode) 缓存视图无关的地理样条。"""
    key = _geo_spline_key(ty)
    c = _geo_spline_cache.get(key)
    if c is None:
        c = {}
        _geo_spline_cache[key] = c
        if len(_geo_spline_cache) > 64:
            _geo_spline_cache.pop(next(iter(_geo_spline_cache)))
    spl = c.get((segs, mode))
    if spl is None:
        # 经度先解包到连续轴: 否则跨 0°/360° 的报点会让样条沿"长的那一边"
        # 绕地球一圈——平滑路径画反, 台风也会沿错误曲线跨全图瞬移
        geo_pts = []
        prev_lo = None
        for p in ty.pts:
            lo, la = float(p['lo']), float(p['la'])
            if prev_lo is not None:
                lo = prev_lo + ((lo - prev_lo + 180.0) % 360.0) - 180.0
            geo_pts.append((lo, la))
            prev_lo = lo
        spl = build_spline(geo_pts, segs, mode)
        c[(segs, mode)] = spl
    return spl


def _clear_geo_spline_cache(ty) -> None:
    _geo_spline_cache.pop(_geo_spline_key(ty), None)


class TyphoonRenderMixin:
    """渲染方法：update_screen_points, 旋转, 坐标。"""

    def update_screen_points(self, latlon_to_screen_func, view_rect=None,
                              anchor=None) -> None:
        v = self.v
        v.screen_points.clear()
        v.smooth_screen_points.clear()
        v.map_points.clear()
        v.smooth_map_points.clear()
        v._smooth_arc_lengths.clear()
        # None = 无锚点: 必须清掉旧锚点, 否则后续纯平移会按旧视图位移 → 路径错位
        v._sp_anchor = anchor
        if not self.pts:
            v.bbox = None
            return
        xs, ys = [], []
        for pt in self.pts:
            x, y = latlon_to_screen_func(pt['la'], pt['lo'])
            v.screen_points.append((x, y))
            xs.append(x)
            ys.append(y)
        # 环绕归一化(唯一真相): 相邻报点统一到同一地图副本。不做这一步时,
        # 跨 0°/360° 缝的相邻点会落在两个副本上, 路径面会画出横跨全屏的伪线,
        # 平滑样条弧长也会被算成整整一个周期(台风"瞬移")。
        wrap = self._map_wrap_px()
        if wrap > 0 and len(v.screen_points) >= 2:
            v.screen_points[:] = normalize_chain(v.screen_points, wrap)
            xs = [p[0] for p in v.screen_points]
            ys = [p[1] for p in v.screen_points]
        # 地图空间点(视图无关): 路径缓存用它, 平移时只需改 blit 位置
        mv = self._map_view()
        geo_to_map = getattr(mv, 'geo_to_map', None) if mv is not None else None
        if geo_to_map is not None:
            v.map_points[:] = [geo_to_map(p['lo'], p['la']) for p in self.pts]
        else:
            v.map_points[:] = list(v.screen_points)   # 无地图变换: 退化为屏幕空间
        x0, y0 = min(xs), min(ys)
        x1, y1 = max(xs), max(ys)
        v.bbox = pygame.Rect(x0, y0, x1 - x0, y1 - y0)
        if view_rect and not v.bbox.colliderect(view_rect):
            # 早退也必须清 smooth：burst 内第 2+ 档滚轮沿用旧 zoom 样条会错位（R5）
            return
        if self.sim and self.sim.cfg.smooth_path:
            segs = max(1, int(self.sim.cfg.smooth_path_segments))
            mode = getattr(self.sim.cfg, 'smooth_path_mode', 'monotone')
            smooth_geo = _get_geo_spline(self, segs, mode)
            f = latlon_to_screen_func
            smooth_sc = [f(lat, lon) for lon, lat in smooth_geo]
            if wrap > 0 and len(smooth_sc) >= 2:
                # 与报点链对齐(同一副本), 否则平滑线与报点线会差一个周期
                smooth_sc = normalize_chain(smooth_sc, wrap,
                                            anchor=v.screen_points[0][0])
            v.smooth_screen_points = smooth_sc
            if geo_to_map is not None:
                v.smooth_map_points[:] = [geo_to_map(lon, lat)
                                          for lon, lat in smooth_geo]
            else:
                v.smooth_map_points[:] = list(smooth_sc)
            v._smooth_arc_lengths = compute_arc_lengths(smooth_sc)

    def _map_view(self):
        """当前地图视图(MapView); 无地图时返回 None。"""
        mgr = getattr(self, 'sim', None)
        mgr = getattr(mgr, 'map_mgr', None) if mgr is not None else None
        return getattr(mgr, 'map_view', None) if mgr is not None else None

    def _map_wrap_px(self) -> float:
        """地图横向环绕周期(屏幕像素); 无地图视图时返回 0 = 跳过归一化。"""
        mgr = getattr(self, 'sim', None)
        mgr = getattr(mgr, 'map_mgr', None) if mgr is not None else None
        mv = getattr(mgr, 'map_view', None) if mgr is not None else None
        if mv is None:
            return 0.0
        try:
            return float(mv.wrap_px())
        except Exception:
            return 0.0

    def _get_rotated(self, key_prefix: str, img: pygame.Surface, angle: float,
                     mirror: bool, tint=None) -> pygame.Surface:
        # 法31: 6° 桶量化缓存 key(3°/帧主环命中率 ~50%,4.5°/帧 3 级环 ~33%);
        # 只量化 key,不改 v.sa/v.sa3 累加与旋转推进公式,动画平滑度不变
        key = (key_prefix, id(img), (int(angle) // 6) * 6 % 360, mirror, tint)
        cache = self.v._img_cache
        hit = cache.get(key)
        if hit is not None:
            return hit
        rotated = pygame.transform.rotate(img, angle)
        if mirror:
            rotated = pygame.transform.flip(rotated, True, False)
        if tint is not None:
            tinted = pygame.Surface(rotated.get_size(), pygame.SRCALPHA)
            tinted.fill((*tint, 0))
            tinted.blit(rotated, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
            rotated = tinted
        cache.put(key, rotated)
        return rotated

    def get_rotated_ring(self, cat: str, base_ring: pygame.Surface, angle: float,
                          mirror: bool, tint=None) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror, tint)

    def get_rotated_level3_ring(self, cat: str, base_ring: pygame.Surface, angle: float,
                                 mirror: bool, tint=None) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror, tint)

    def update_rotation(self, dt: float) -> None:
        mf = self.sim.main_rotation_speed if self.sim else 1.0
        lf = self.sim.level3_rotation_speed if self.sim else 1.5
        v = self.v
        step_main = 180.0 * dt * mf
        step_level3 = 180.0 * dt * lf
        v.sa = (v.sa + step_main) % 360
        v.sa3 = (v.sa3 + step_level3) % 360
        v.sa4 = (v.sa4 + step_level3) % 360
        v.sa5 = (v.sa5 + step_level3) % 360
