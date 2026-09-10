# tests/test_motion_smoothness.py
"""移动与路径渲染的平滑性(含跨 0° 经线) + 镜头跟踪收敛。

对应需求 3: 台风移动/路径渲染在任何情况下都平滑——
  - 平滑样条弧长不因跨缝出现"整整一个地图周期"的假跳变(否则台风瞬移);
  - update_move 每帧屏幕位移有界(不瞬移);
  - 镜头跟踪逐帧收敛、静止后不再持续标记视图脏/重投影;
  - 平移复用与重新投影逐点一致(拖动时画面不跳)。
"""
import math
import os
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

from app.constants import DEFAULT_MAP, MODE_NORMAL, MODE_SEASON
from app.render_geom import normalize_chain
from app.spline import compute_arc_lengths, position_at_arc
from app.typhoon import TrackPoint, Typhoon
from app.ty_sim_mixins.track_mixin import TySimTrackMixin

SCREEN_W, SCREEN_H = 1200, 700
MAP_W, MAP_H, SCALE = 400, 200, 3.0
WRAP = int(MAP_W * SCALE)


def make_full_map_view():
    """整图恰好铺满屏的小地图视图(wrap == 屏宽): 跨缝最坏情况, 内存 <1MB。"""
    from app.map_mgr import MapView
    with mock.patch("pygame.image.load",
                    lambda _path: pygame.Surface((MAP_W, 250))):
        mv = MapView(DEFAULT_MAP, 0.0, 360.0, -90.0, 90.0, SCREEN_W, SCREEN_H)
    mv.scale = mv.min_scale
    mv.view_x = 0.0
    mv.view_y = 0.0
    mv._cached_scale = -1.0
    return mv


class MoveCfg:
    smooth_path = True
    smooth_path_segments = 8
    smooth_path_mode = "catmull"


class MoveSim:
    """update_move / 屏幕点管线所需的最小桩。"""

    def __init__(self, mv):
        self.cfg = MoveCfg()
        self.map_mgr = type("M", (), {"map_view": mv})()
        self._drag_offset_x = 0
        self._drag_offset_y = 0

    def latlon_to_screen(self, la, lo):
        return self.map_mgr.map_view.geo_to_screen(lo, la)

    def screen_to_latlon(self, x, y):
        # 与 TySim.screen_to_latlon 同约定: 返回 (lat, lon)
        lon, lat = self.map_mgr.map_view.screen_to_geo(x, y)
        return lat, lon


def make_seam_ty(lons, lat0=10.0):
    ty = Typhoon("WP", "01")
    ty.pts = [TrackPoint(t=f"20000101{i:02d}00", la=lat0 + 0.4 * i, lo=lo,
                         w=45, p=1000, st="TS", name="T")
              for i, lo in enumerate(lons)]
    ty.rst()
    return ty


def seam_gap(points):
    """链上最大的"环绕感知"相邻跳变(取最近副本后的 |dx| 与 |dy|)。"""
    worst = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx = abs(x1 - x0)
        dx = min(dx, abs(dx - WRAP), abs(dx + WRAP))
        worst = max(worst, math.hypot(dx, y1 - y0))
    return worst


class TestArcLengthSmoothness(unittest.TestCase):
    RAW = [(1150.0, 100.0), (1180.0, 104.0), (10.0, 108.0), (40.0, 112.0)]

    def test_arc_length_has_no_phantom_period(self):
        arcs_raw = compute_arc_lengths(self.RAW)
        step_raw = max(b - a for a, b in zip(arcs_raw, arcs_raw[1:]))
        norm = normalize_chain(self.RAW, float(WRAP))
        arcs = compute_arc_lengths(norm)
        step_norm = max(b - a for a, b in zip(arcs, arcs[1:]))
        self.assertGreater(step_raw, WRAP * 0.9)      # 旧链确实含一整圈假弧长
        self.assertLess(step_norm, 100.0)

    def test_position_at_arc_is_continuous(self):
        norm = normalize_chain(self.RAW, float(WRAP))
        arcs = compute_arc_lengths(norm)
        total = arcs[-1]
        prev = None
        worst = 0.0
        for k in range(101):
            x, y = position_at_arc(norm, arcs, total * k / 100.0)
            if prev is not None:
                worst = max(worst, math.hypot(x - prev[0], y - prev[1]))
            prev = (x, y)
        self.assertLess(worst, total / 100.0 * 1.5 + 1e-6)

    def test_unnormalized_curve_goes_the_long_way(self):
        """反例: 未归一化时曲线沿"长的那一边"绕过半个地图, 弧长也多出整整一圈。"""
        arcs_raw = compute_arc_lengths(self.RAW)
        total_raw = arcs_raw[-1]
        xs_raw = [position_at_arc(self.RAW, arcs_raw,
                                  total_raw * k / 100.0)[0] for k in range(101)]
        self.assertLess(min(xs_raw), 600.0)
        norm = normalize_chain(self.RAW, float(WRAP))
        arcs = compute_arc_lengths(norm)
        xs = [position_at_arc(norm, arcs, arcs[-1] * k / 100.0)[0]
              for k in range(101)]
        self.assertGreater(min(xs), 1100.0)        # 始终在同一副本内
        self.assertGreater(arcs_raw[-1], arcs[-1] + WRAP * 0.9)


class TestUpdateMoveNoTeleport(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.mv = make_full_map_view()
        self.sim = MoveSim(self.mv)
        self.ty = make_seam_ty([350.0, 0.0, 10.0])
        self.ty.sim = self.sim

    def _worst_step(self):
        ty = self.ty
        ty.update_screen_points(self.sim.latlon_to_screen)
        ty.points_time = [float(i) for i in range(len(ty.pts))]
        ty.ci = 0
        ty.at = 0.0
        ty.lut = 0.0
        ty.start_move(0.0)
        prev = None
        worst = 0.0
        t = 0.0
        for _ in range(400):
            t += 16.67
            ty.update_move(t, 1.0)
            pos = ty.cpos()
            if not pos:
                break
            xy = self.sim.latlon_to_screen(pos["la"], pos["lo"])
            if prev is not None:
                dx = abs(xy[0] - prev[0])
                dx = min(dx, abs(dx - WRAP))
                worst = max(worst, math.hypot(dx, xy[1] - prev[1]))
            prev = xy
        return worst

    def test_movement_step_bounded(self):
        self.assertLess(self._worst_step(), 40.0)

    def test_control_spline_without_unwrap_goes_the_long_way(self):
        """反例: 样条按原始经度(不解包)建 → 曲线绕过半个地图(路径画反)。"""
        from app.spline import build_spline as _build

        def old_spline(ty, segs, mode):
            return _build([(p["lo"], p["la"]) for p in ty.pts], segs, mode)

        with mock.patch("app.typhoon_render._get_geo_spline", old_spline):
            self.ty.update_screen_points(self.sim.latlon_to_screen)
            xs_bad = [p[0] for p in self.ty.v.smooth_screen_points]
        self.assertLess(min(xs_bad), 600.0)         # 跨过半个屏幕
        self.ty.update_screen_points(self.sim.latlon_to_screen)
        xs_ok = [p[0] for p in self.ty.v.smooth_screen_points]
        self.assertGreater(min(xs_ok), 1100.0)      # 修好后始终在同一副本

    def test_smooth_chain_stays_in_one_copy(self):
        self.ty.update_screen_points(self.sim.latlon_to_screen)
        self.assertLess(seam_gap(self.ty.screen_points), WRAP / 2.0)
        self.assertLess(seam_gap(self.ty.v.smooth_screen_points), WRAP / 2.0)


# ── 镜头跟踪 ──

class FakeMapView:
    lon_min = 0.0
    lat_max = 90.0
    _scale_x = 10.0
    _scale_y = 10.0
    screen_width = 1360
    screen_height = 785
    img_w = 3600
    scale = 2.0

    def __init__(self):
        self.view_x = 0.0
        self.view_y = 0.0

    def _clamp_view_y(self):
        pass


class ClampMapView(FakeMapView):
    def __init__(self, max_y):
        super().__init__()
        self.max_y = max_y

    def _clamp_view_y(self):
        self.view_y = max(0.0, min(self.view_y, self.max_y))


class TrackSim(TySimTrackMixin):
    MODE_NORMAL = MODE_NORMAL
    MODE_SEASON = MODE_SEASON

    def __init__(self, mv):
        self.md = MODE_NORMAL
        self.map_mgr = type("M", (), {"map_view": mv})()
        self._view_dirty = False
        self._init_tracking()

    def current_typhoon(self):
        return None

    def invalidate_screen_points_lazy(self):
        pass


def remaining(mv, clon, clat):
    px = (clon - mv.lon_min) * mv._scale_x
    py = (mv.lat_max - clat) * mv._scale_y
    tx = (px - mv.screen_width / (2.0 * mv.scale)) % mv.img_w
    ty = py - mv.screen_height / (2.0 * mv.scale)
    dx = (tx - mv.view_x + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
    return dx, ty - mv.view_y


class TestTrackCameraConvergence(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def test_converges_and_then_stops(self):
        mv = FakeMapView()
        sim = TrackSim(mv)
        clon, clat = 44.0, 20.0
        moves, dirty = [], []
        prev_x, prev_y = mv.view_x, mv.view_y
        for _ in range(300):
            sim._view_dirty = False
            sim._track_pan_to(mv, clon, clat, 0, 1.0 / 60.0)
            moves.append(math.hypot(mv.view_x - prev_x, mv.view_y - prev_y))
            dirty.append(sim._view_dirty)
            prev_x, prev_y = mv.view_x, mv.view_y
        dx, dy = remaining(mv, clon, clat)
        self.assertLess(abs(dx), 0.3)
        self.assertLess(abs(dy), 0.3)
        self.assertEqual(dirty[-50:].count(True), 0)      # 到位后不再重投影
        self.assertGreater(moves[0], moves[-1])           # 逐帧减速, 无振荡
        self.assertLess(max(moves), 200.0)                # 单帧不跳

    def test_clamped_axis_does_not_keep_invalidating(self):
        # 目标正好在屏幕中心线上、垂直方向被地图下边界钳制:
        # 视图实际一点都动不了 → 不允许每帧标记脏/重投影
        mv = ClampMapView(max_y=100.0)
        mv.view_x, mv.view_y = 100.0, 100.0
        sim = TrackSim(mv)
        for _ in range(5):
            sim._view_dirty = False
            sim._track_pan_to(mv, 44.0, 10.0, 0, 1.0 / 60.0)
            self.assertFalse(sim._view_dirty)
        self.assertEqual((mv.view_x, mv.view_y), (100.0, 100.0))

    def test_clamped_axis_still_settles(self):
        mv = ClampMapView(max_y=100.0)
        mv.view_x, mv.view_y = 100.0, 100.0
        sim = TrackSim(mv)
        for _ in range(120):
            sim._view_dirty = False
            sim._track_pan_to(mv, 44.1, 10.0, 0, 1.0 / 60.0)
        dx, _dy = remaining(mv, 44.1, 10.0)
        self.assertLess(abs(dx), 0.3)
        self.assertEqual(mv.view_y, 100.0)                # 垂直方向始终贴边不动
        sim._view_dirty = False
        sim._track_pan_to(mv, 44.1, 10.0, 0, 1.0 / 60.0)
        self.assertFalse(sim._view_dirty)


if __name__ == "__main__":
    unittest.main()
