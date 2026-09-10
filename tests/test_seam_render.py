# tests/test_seam_render.py
"""跨 0° 经线的实际渲染测试(路径面 / 洋区边界 / 平移复用)。

对应需求 1 与需求 3 的一部分: 长线跨越屏幕边界或 0° 经线时
  - 不能画出横跨全屏的伪线段("乱飞");
  - 也不能因为"跳变过长"被整段丢弃(路径出现缺口);
  - 平移后复用缓存必须与重新投影逐点一致(画面不跳)。

构造方式: 用一张"宽 400px 的地图放大到 1200px 屏宽"(scale=3 → wrap == 屏宽),
此时 geo_to_screen 的翻面阈值恰好落在屏幕左右边缘, 0° 经线附近的相邻报点
会落在两个地图副本上——这是最坏情况, 也是旧实现会丢段/画长线的位置。
"""
import math
import os
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import numpy as np
import pygame

from app.constants import DEFAULT_MAP, MODE_NORMAL
from app.typhoon import TrackPoint, Typhoon
from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin

SCREEN_W, SCREEN_H = 1200, 700
MAP_W, MAP_H = 400, 200
SCALE = 3.0
WRAP = int(MAP_W * SCALE)          # 1200 == 屏宽: 最坏情况


def longest_alpha_run(surface):
    """最长水平连续不透明像素(伪线/长段检测)。"""
    alpha = pygame.surfarray.array_alpha(surface)
    mask = alpha > 0
    best = 0
    for y in range(mask.shape[1]):
        col = mask[:, y]
        if not col.any():
            continue
        pad = np.concatenate(([0], col.astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(pad))
        if len(edges) >= 2:
            best = max(best, int((edges[1::2] - edges[0::2]).max()))
    return best


def make_full_map_view():
    """整图恰好铺满屏的地图视图(wrap == screen_width): 跨缝最坏情况。

    用 400x250 小图替代真实 10800x5400 地图(单实例 ~233MB → <1MB), 且
    min_scale = max(1200/400, 700/250) = 3.0 → wrap = 400*3 = 1200 = 屏宽。"""
    from app.map_mgr import MapView
    with mock.patch("pygame.image.load",
                    lambda _path: pygame.Surface((MAP_W, 250))):
        mv = MapView(DEFAULT_MAP, 0.0, 360.0, -90.0, 90.0, SCREEN_W, SCREEN_H)
    mv.scale = mv.min_scale
    mv.view_x = 0.0
    mv.view_y = 0.0
    mv._cached_scale = -1.0
    return mv


class FakeCfg:
    smooth_path = False
    smooth_path_mode = "catmull"
    smooth_path_segments = 8
    point_size = 100
    fix_icon_point_size = False
    fade_path_mode = "never"


class FakeDM:
    def any_active(self):
        return False


class FakeMgr:
    def __init__(self, mv):
        self.map_view = mv

    def _load_land_orig(self):
        return None


class FakeSim(TySimDrawPathMixin):
    MODE_NORMAL = MODE_NORMAL
    MODE_EDIT = "edit"

    def __init__(self, mv):
        self.md = MODE_NORMAL
        self.cfg = FakeCfg()
        self.screen_width = SCREEN_W
        self.map_height = SCREEN_H
        self.show_future_path = True
        self.path_mode = "line"
        self.smooth_path = False
        self.smooth_path_segments = 8
        self.smooth_path_mode = "catmull"
        self.point_size = 100
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self.right_button_dragging = False
        self.dragging_point = False
        self.drag_typhoon = None
        self.drag_point_index = -1
        self.dialog_mgr = FakeDM()
        self._sp_version = 0
        self.map_mgr = FakeMgr(mv)

    def _size_factors(self):
        return (1.0, 1.0)

    def latlon_to_screen(self, la, lo):
        return self.map_mgr.map_view.geo_to_screen(lo, la)

    def get_strength_category(self, w, st):
        return "TS"


def make_seam_ty(lons, lat0=10.0, dlat=0.6):
    ty = Typhoon("WP", "01")
    ty.pts = [TrackPoint(t=f"20000101{i:02d}00", la=lat0 + dlat * i, lo=lo,
                         w=45, p=1000, st="TS", name="T")
              for i, lo in enumerate(lons)]
    ty.rst()
    return ty


class TestPathAcrossSeam(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.mv = make_full_map_view()
        self.sim = FakeSim(self.mv)
        self.ty = make_seam_ty([356.0, 358.0, 0.0, 2.0, 4.0])
        self.ty.sim = self.sim

    def _raw_points(self):
        return [self.sim.latlon_to_screen(p["la"], p["lo"]) for p in self.ty.pts]

    def test_raw_points_have_seam_discontinuity(self):
        xs = [p[0] for p in self._raw_points()]
        gaps = [abs(b - a) for a, b in zip(xs, xs[1:])]
        self.assertGreater(max(gaps), WRAP * 0.5)   # 构造确实卡在最坏情况

    def test_update_screen_points_is_seam_stable(self):
        self.ty.update_screen_points(self.sim.latlon_to_screen)
        xs = [p[0] for p in self.ty.screen_points]
        gaps = [abs(b - a) for a, b in zip(xs, xs[1:])]
        self.assertLessEqual(max(gaps), WRAP / 2.0)
        # 报点宽度必须远小于一整圈地图(旧实现会跨两个副本)
        self.assertLess(max(xs) - min(xs), self.mv.wrap_px())

    @staticmethod
    def _gap_count(points, max_seg):
        n = 0
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if math.hypot(x1 - x0, y1 - y0) > max_seg:
                n += 1
        return n

    def test_no_segment_dropped_at_seam(self):
        max_seg = min(SCREEN_W, SCREEN_H) // 2      # = _path_max_seg_len
        raw = self._raw_points()
        self.ty.update_screen_points(self.sim.latlon_to_screen)
        self.assertGreaterEqual(self._gap_count(raw, max_seg), 1)   # 旧行为丢段
        self.assertEqual(self._gap_count(self.ty.screen_points, max_seg), 0)
        self.assertEqual(len(self.ty.screen_points), len(self.ty.pts))

    def test_path_surface_has_no_flying_line(self):
        self.ty.update_screen_points(self.sim.latlon_to_screen)
        full, _trav, _pos = self.sim._render_path_to_surface(
            self.ty, list(self.ty.screen_points), False)
        self.assertLessEqual(longest_alpha_run(full), SCREEN_W // 2)

    def test_control_raw_points_lose_a_segment(self):
        """反例: 未归一化的报点在绘制管线里会被 max_seg 判为跳变而丢段(路径缺口)。"""
        raw = self._raw_points()
        max_seg = min(SCREEN_W, SCREEN_H) // 2
        gaps = [math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(raw, raw[1:])]
        self.assertTrue(any(g > max_seg for g in gaps))

    def test_pan_reuse_matches_reprojection_at_seam(self):
        self.ty.update_screen_points(self.sim.latlon_to_screen,
                                     anchor=self.sim._view_anchor_tuple())
        self.sim._render_path_to_surface(self.ty, list(self.ty.screen_points), False)
        # 视图前移 9 个图像像素(等价屏幕位移 9*scale)
        self.mv.view_x = (self.mv.view_x + 3.0) % self.mv.img_w
        moved = self.sim._try_shift_screen_points(self.ty)
        if not moved:
            self.skipTest("视图位移超过复用窗口, 本帧按全量重建(合法行为)")
        expect = [self.sim.latlon_to_screen(p["la"], p["lo"]) for p in self.ty.pts]
        for (ax, ay), (ex, ey) in zip(self.ty.screen_points, expect):
            self.assertLessEqual(abs(ax - ex), 1)
            self.assertLessEqual(abs(ay - ey), 1)


class TestOceanAreasAcrossSeam(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.mv = make_full_map_view()

    def _stub_sim(self):
        from app.ocean_mgr import OceanArea

        class _Res:
            class ocean_areas:
                areas = [OceanArea("SA", "南大西洋", "South Atlantic", "S",
                                   10.0, [(10.0, 356.0), (14.0, 358.0),
                                          (18.0, 1.0), (13.0, 4.0)])]

        class _Stub:
            screen_width = SCREEN_W
            map_height = SCREEN_H
            res_mgr = _Res()

            def __init__(self, mv):
                self.map_mgr = FakeMgr(mv)

            def latlon_to_screen(self, la, lo):
                return self.map_mgr.map_view.geo_to_screen(lo, la)

        return _Stub(self.mv)

    def _blue_pixels(self, surf):
        arr = pygame.surfarray.array3d(surf)
        return int((((np.abs(arr[..., 0].astype(int) - 80) < 60)
                     & (np.abs(arr[..., 1].astype(int) - 140) < 60)
                     & (np.abs(arr[..., 2].astype(int) - 220) < 60))).sum())

    def test_overlay_draws_without_crossing_the_screen(self):
        from app.renderer import Renderer
        surf = pygame.Surface((SCREEN_W, SCREEN_H))
        surf.fill((0, 0, 0))
        Renderer(self._stub_sim())._draw_ocean_areas(surf)
        px = self._blue_pixels(surf)
        self.assertGreater(px, 0)                  # 确实画出来了
        # 多边形周长只有几十像素: 一旦出现横穿屏幕的伪线, 像素数会暴增
        self.assertLess(px, 600)

    def test_control_without_normalize_crosses_the_screen(self):
        from app.renderer import Renderer
        surf = pygame.Surface((SCREEN_W, SCREEN_H))
        surf.fill((0, 0, 0))
        with mock.patch("app.renderer.normalize_chain",
                        lambda pts, wrap: list(pts)):
            Renderer(self._stub_sim())._draw_ocean_areas(surf)
        # 反例: 未归一化时跨缝顶点之间会连出横穿屏幕的长线(像素数暴增)
        self.assertGreater(self._blue_pixels(surf), 1000)


if __name__ == "__main__":
    unittest.main()
