# tests/test_landfall_view_invariance.py
"""登陆判定与视图无关(缩放/拖动/跟踪都不影响结果)。

对应需求 2: 台风登陆在任何情况下(缩放、跟踪、拖动)都必须正确。
登陆必须完全按经纬度采样陆地掩码, 一旦有人把屏幕坐标混进这条链路,
本文件的断言就会失败。
"""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

from app.constants import DEFAULT_MAP, MODE_NORMAL
from app.typhoon import TrackPoint, Typhoon
from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin

LAND_LON = 130.0


class FakeLandMgr:
    """确定性陆地: 经度 >= LAND_LON 视为陆地; 记录每次采样坐标。"""

    def __init__(self, mv):
        self.map_view = mv
        self.probes = []

    def _load_land_orig(self):
        return object()          # 非 None 即视为掩码可用

    def is_land_at_geo(self, la, lo):
        self.probes.append((round(float(la), 6), round(float(lo), 6)))
        return float(lo) >= LAND_LON

    def clear_probes(self):
        self.probes = []


class FakeCfg:
    smooth_path = False
    smooth_path_mode = "catmull"
    smooth_path_segments = 8
    point_size = 100
    fix_icon_point_size = False
    fade_path_mode = "never"


class FakeSim(TySimDrawPathMixin):
    MODE_NORMAL = MODE_NORMAL
    MODE_EDIT = "edit"

    def __init__(self, mv):
        self.md = MODE_NORMAL
        self.cfg = FakeCfg()
        self.screen_width = 1200
        self.map_height = 700
        self.map_mgr = FakeLandMgr(mv)
        self.smooth_path = False

    def _size_factors(self):
        return (1.0, 1.0)

    def latlon_to_screen(self, la, lo):
        return self.map_mgr.map_view.geo_to_screen(lo, la)

    def get_strength_category(self, w, st):
        return "TS"


def make_mv():
    from app.map_mgr import MapView
    return MapView(DEFAULT_MAP, 0.0, 360.0, -90.0, 90.0, 1200, 700)


def make_ty(lons, lat0=15.0):
    ty = Typhoon("WP", "01")
    ty.pts = [TrackPoint(t=f"20000101{i:02d}00", la=lat0 + 0.2 * i, lo=lo,
                         w=45, p=1000, st="TS", name="T")
              for i, lo in enumerate(lons)]
    ty.rst()
    return ty


class TestLandfallViewInvariance(unittest.TestCase):
    VIEWS = ((1.0, 0.0, 0.0), (2.5, 800.0, 300.0), (0.5, 4000.0, 1000.0))

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.mv = make_mv()
        self.sim = FakeSim(self.mv)
        self.ty = make_ty([125.0, 128.0, 130.5, 133.0, 136.0])
        self.ty.sim = self.sim

    def _set_view(self, scale, vx, vy):
        self.mv.scale = scale
        self.mv.view_x = vx % self.mv.img_w
        self.mv.view_y = vy
        self.mv._clamp_view_y()
        self.mv._cached_scale = -1.0

    def _records(self):
        self.ty._cached_landfalls = None
        return self.sim._get_precomputed_landfalls(self.ty)

    def test_records_identical_across_zoom_and_pan(self):
        ref = None
        for scale, vx, vy in self.VIEWS:
            self._set_view(scale, vx, vy)
            # 跟踪/拖动会重投影屏幕点: 登陆结果不允许随之变化
            self.ty.update_screen_points(self.sim.latlon_to_screen)
            recs = self._records()
            if ref is None:
                ref = recs
                self.assertTrue(recs, "构造数据应当至少有一次登陆")
            else:
                self.assertEqual(recs, ref)

    def test_probe_sequence_identical_across_views(self):
        ref = None
        for scale, vx, vy in self.VIEWS:
            self._set_view(scale, vx, vy)
            self.ty.update_screen_points(self.sim.latlon_to_screen)
            self.ty._cached_landfalls = None
            self.sim.map_mgr.clear_probes()
            self.sim._get_precomputed_landfalls(self.ty)
            probes = list(self.sim.map_mgr.probes)
            self.assertTrue(probes)
            if ref is None:
                ref = probes
            else:
                self.assertEqual(probes, ref)

    def test_one_record_per_crossing(self):
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["seg"], 1)
        self.assertTrue(recs[0]["png"])
        self.assertLessEqual(recs[0]["lo"], 133.0)

    def test_crossing_back_and_forth(self):
        ty = make_ty([125.0, 136.0, 126.0, 138.0])
        ty.sim = self.sim
        recs = self.sim._get_precomputed_landfalls(ty)
        self.assertEqual(len(recs), 2)

    def test_no_false_landfall_when_staying_at_sea(self):
        ty = make_ty([120.0, 124.0, 128.0])
        ty.sim = self.sim
        self.assertEqual(self.sim._get_precomputed_landfalls(ty), [])

    def test_cache_key_follows_point_data(self):
        self._records()
        key0 = self.ty._cached_landfalls[0]
        self.ty.pts = self.ty.pts[:3]          # 报点数据变了
        self._records()
        self.assertNotEqual(self.ty._cached_landfalls[0], key0)


class TestLandfallWithRealLandMask(unittest.TestCase):
    """真实 MapManager.is_land_at_geo 采样 + 合成小掩码(36x18, 10°/像素)。

    换视图(缩放/平移)后登陆记录必须逐字段一致。用 36x18 掩码而不构造完整
    TySim: 既走真实采样代码, 又把单测内存从几百 MB 降到 <1MB。"""

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.mv = make_mv()
        self.sim = FakeSim(self.mv)
        self.sim.map_mgr = self._make_mgr()

    @staticmethod
    def _make_mgr():
        from app.map_mgr import MapManager
        # 36x18 = 10°/px: x=[12,15) → lon[120,150), y=[6,9) → lat(0,30] 为陆地
        surf = pygame.Surface((36, 18), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        pygame.draw.rect(surf, (255, 255, 255, 255), pygame.Rect(12, 6, 3, 3))
        mgr = object.__new__(MapManager)
        mgr.sim = None
        mgr._land_orig = surf
        mgr._land_geo_alpha = None
        mgr._land_geo_w = 0
        mgr._land_geo_h = 0
        return mgr

    def _ty(self):
        ty = make_ty([118.0, 125.0, 128.0], lat0=15.0)
        ty.sim = self.sim
        return ty

    def test_real_mask_samples_by_geo(self):
        recs = self.sim._get_precomputed_landfalls(self._ty())
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["seg"], 0)
        self.assertGreaterEqual(recs[0]["lo"], 120.0)

    def test_real_mask_landfall_view_invariant(self):
        ty = self._ty()
        ref = self.sim._get_precomputed_landfalls(ty)
        self.assertTrue(ref)
        for scale, vx in ((2.0, 500.0), (0.6, 3000.0)):
            self.mv.scale = scale
            self.mv.view_x = vx % self.mv.img_w
            self.mv._clamp_view_y()
            ty._cached_landfalls = None
            self.assertEqual(self.sim._get_precomputed_landfalls(ty), ref)


if __name__ == "__main__":
    unittest.main()
