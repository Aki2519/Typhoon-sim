# tests/test_track_quality.py
"""路径质量度量与回放偏离记账。

对应"路径值得参考"的四条落地:
  1. 运动矢量(移向/移速)采样与估计;
  2. 逐段移速与质量闸门(超阈值 -> review);
  3. 平滑曲线相对报点直线的偏离记账与上限拉回;
  4. 跨 0 度经线的距离/方向不出现"半个地球"的假跳变。
"""
import math
import os
import unittest
from types import SimpleNamespace

from app.track_quality import (CURVE_DEV_LIMIT_KM, km_between, km_per_deg_lon,
                               segment_speeds, track_quality, wrap_dlon)
from app.typhoon_sim import (TyphoonSimMixin, _MOTION_MAX_KMH,
                             _MOTION_SAMPLE_MAX, _MOTION_SAMPLE_STEP_H)


def pt(la, lo):
    return {'la': la, 'lo': lo}


def fake_ty(points, hours):
    """points: [(la, lo)], hours: 相邻点间隔小时数的列表(长度 len-1)。"""
    times = [0.0]
    for h in hours:
        times.append(times[-1] + h * 3600.0)
    return SimpleNamespace(pts=[pt(*p) for p in points], points_time=times)


def fake_view(**kw):
    base = dict(ipos=None, _motion_samples=[], _dev_last_km=0.0,
                _dev_max_km=0.0, _dev_clamped=0)
    base.update(kw)
    return SimpleNamespace(**base)


class TestKmBetween(unittest.TestCase):
    def test_wrap_dlon(self):
        self.assertAlmostEqual(wrap_dlon(359.0, 1.0), 2.0, places=9)
        self.assertAlmostEqual(wrap_dlon(1.0, 359.0), -2.0, places=9)
        self.assertAlmostEqual(abs(wrap_dlon(0.0, 180.0)), 180.0, places=9)
        self.assertAlmostEqual(abs(wrap_dlon(0.0, -180.0)), 180.0, places=9)

    def test_cross_seam_distance_is_local(self):
        """跨 0 度经线: 2 度经差在 10N 上约 219km, 不是半个地球。"""
        d = km_between(10.0, 359.0, 10.0, 1.0)
        self.assertAlmostEqual(d, 2.0 * km_per_deg_lon(10.0), places=6)
        self.assertLess(d, 300.0)

    def test_latitude_scaling(self):
        """同样 1 度经差, 高纬更短; 1 度纬差基本恒定。"""
        self.assertLess(km_between(60, 0, 60, 1), km_between(0, 0, 0, 1))
        self.assertAlmostEqual(km_between(0, 0, 1, 0), 110.57, places=6)


class TestTrackQuality(unittest.TestCase):
    def test_segment_speeds(self):
        ty = fake_ty([(10.0, 120.0), (10.0, 122.0)], [6.0])
        sp = segment_speeds(ty)
        self.assertEqual(len(sp), 1)
        self.assertAlmostEqual(sp[0][0], 2.0 * km_per_deg_lon(10.0) / 6.0, places=4)
        self.assertEqual(sp[0][1], 0)

    def test_invalid_times_skipped(self):
        ty = fake_ty([(10.0, 120.0), (10.0, 121.0), (10.0, 122.0)], [6.0, 0.0])
        self.assertEqual(len(segment_speeds(ty)), 1)

    def test_status_continuous_and_review(self):
        ok = track_quality(fake_ty([(10.0, 120.0), (12.0, 124.0)], [12.0]))
        self.assertEqual(ok['status'], 'continuous')
        self.assertEqual(ok['over_limit'], 0)
        # 11 度经差/1 小时 = 远超人可接受移速
        bad = track_quality(fake_ty([(10.0, 120.0), (40.0, 200.0)], [1.0]))
        self.assertEqual(bad['status'], 'review')
        self.assertEqual(bad['over_limit'], 1)
        self.assertEqual(bad['worst_index'], 0)

    def test_empty_inputs(self):
        self.assertIsNone(track_quality(None))
        self.assertIsNone(track_quality(fake_ty([(1.0, 1.0)], [])))


class TestCurveDeviation(unittest.TestCase):
    """_limit_curve_deviation: 偏离记账 + 超限拉回。"""

    def _obj(self, ci=0):
        pts = [pt(10.0, 120.0), pt(10.0, 122.0)]
        return SimpleNamespace(pts=pts, ci=ci, v=fake_view())

    def test_records_without_clamping_when_small(self):
        o = self._obj()
        ipos = {'la': 10.0, 'lo': 121.0001}
        TyphoonSimMixin._limit_curve_deviation(o, ipos, 0.5)
        self.assertLess(o.v._dev_last_km, CURVE_DEV_LIMIT_KM)
        self.assertEqual(o.v._dev_clamped, 0)
        self.assertEqual(o.v._dev_max_km, o.v._dev_last_km)

    def test_clamps_and_accounts(self):
        o = self._obj()
        # 曲线位置偏出 3 度纬(约 330km), 远超 25km 上限
        ipos = {'la': 13.0, 'lo': 121.0}
        TyphoonSimMixin._limit_curve_deviation(o, ipos, 0.5)
        dev = km_between(ipos['la'], ipos['lo'], 10.0, 121.0)
        self.assertAlmostEqual(dev, CURVE_DEV_LIMIT_KM, delta=0.5)
        self.assertEqual(o.v._dev_clamped, 1)
        self.assertGreater(o.v._dev_max_km, CURVE_DEV_LIMIT_KM)
        # 方向保持: 仍在报点连线的"北方"
        self.assertGreater(ipos['la'], 10.0)

    def test_linear_reference_is_wrap_aware(self):
        """跨 0 度经线: 参考量走短路, 偏离约束不会把台风拖到地图另一边。"""
        o = SimpleNamespace(pts=[pt(10.0, 350.0), pt(10.0, 0.0)], ci=0,
                            v=fake_view())
        # 样条给出的位置 (短路: 355°), 与参考量只差 0.2°
        ipos = {'la': 10.0, 'lo': 355.0}
        TyphoonSimMixin._limit_curve_deviation(o, ipos, 0.5)
        self.assertAlmostEqual(ipos['lo'], 355.0, delta=0.5)
        self.assertEqual(o.v._dev_clamped, 0)
        self.assertLess(o.v._dev_last_km, 30.0)

    def test_max_is_monotone(self):
        o = self._obj()
        for la in (11.0, 12.0, 10.05):
            TyphoonSimMixin._limit_curve_deviation(o, {'la': la, 'lo': 121.0}, 0.5)
        self.assertGreaterEqual(o.v._dev_max_km, 100.0)


class TestMotionVector(unittest.TestCase):
    def _obj(self, samples, at=0.0):
        return SimpleNamespace(at=at, v=fake_view(_motion_samples=list(samples)))

    def test_requires_history(self):
        o = self._obj([(0.0, 10.0, 120.0), (1.0, 10.0, 121.0)])
        self.assertIsNone(TyphoonSimMixin.motion_vector(o))

    def test_eastward_and_northward(self):
        # 2 小时向东 1 度, 向北 1 度(≈55 km/h, 低于限幅阈值)
        o = self._obj([(0.0, 10.0, 120.0), (2.0, 11.0, 121.0)], at=2.0)
        u, v = TyphoonSimMixin.motion_vector(o)
        self.assertAlmostEqual(u, km_per_deg_lon(10.5) / 2.0, delta=0.5)
        self.assertAlmostEqual(v, 110.57 / 2.0, delta=0.5)

    def test_clamp_keeps_direction(self):
        """超限时按比例缩放: 方向不变, 模长受限。"""
        o = self._obj([(0.0, 0.0, 0.0), (2.0, 3.0, 3.0)], at=2.0)
        u, v = TyphoonSimMixin.motion_vector(o)
        raw_u, raw_v = 3.0 * km_per_deg_lon(1.5) / 2.0, 3.0 * 110.57 / 2.0
        self.assertAlmostEqual(math.hypot(u, v), _MOTION_MAX_KMH, delta=1e-6)
        self.assertAlmostEqual(v / u, raw_v / raw_u, places=9)

    def test_cross_seam_uses_short_way(self):
        o = self._obj([(0.0, 10.0, 359.0), (2.0, 10.0, 1.0)], at=2.0)
        u, v = TyphoonSimMixin.motion_vector(o)
        self.assertGreater(u, 0.0)            # 向东跨缝, 不是向西绕地球
        self.assertLess(abs(u), 300.0)

    def test_speed_clamped(self):
        o = self._obj([(0.0, 0.0, 0.0), (2.0, 0.0, 120.0)], at=2.0)
        u, v = TyphoonSimMixin.motion_vector(o)
        self.assertLessEqual(math.hypot(u, v), _MOTION_MAX_KMH + 1e-6)

    def test_sampling_interval_and_cap(self):
        o = self._obj([], at=0.0)
        o.v.ipos = {'la': 10.0, 'lo': 120.0}
        # 同一时刻重复调用只留一个样本
        for _ in range(5):
            TyphoonSimMixin._record_motion_sample(o)
        self.assertEqual(len(o.v._motion_samples), 1)
        # 逐步推进 -> 间隔生效
        for i in range(1, 40):
            o.at = i * _MOTION_SAMPLE_STEP_H * 0.5
            TyphoonSimMixin._record_motion_sample(o)
        self.assertLessEqual(len(o.v._motion_samples), _MOTION_SAMPLE_MAX)


class TestMotionArrow(unittest.TestCase):
    """_draw_motion_vector 的可执行性与跨经线护栏(观感靠人工确认)。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        pygame.init()
        cls.pygame = pygame

    def _sim(self, shift_x=0):
        class S:
            cfg = SimpleNamespace(show_motion_vector=True)
            screen_width = 200

            @staticmethod
            def latlon_to_screen(la, lo):
                return (int(lo - 120.0) + shift_x, int(-la))
        return S()

    def _blank(self):
        return self.pygame.Surface((200, 200))

    def _ink(self, surf):
        import pygame.surfarray as sa
        arr = sa.array3d(surf)
        return int(arr.sum())

    def test_draws_arrow(self):
        from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin
        ty = SimpleNamespace(motion_vector=lambda: (25.0, 12.0))
        surf = self._blank()
        TySimDrawPathMixin._draw_motion_vector(
            self._sim(), surf, ty, (50, 50), {'la': 10.0, 'lo': 120.0}, 255)
        self.assertGreater(self._ink(surf), 0)

    def test_disabled_flag_draws_nothing(self):
        from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin
        sim = self._sim()
        sim.cfg = SimpleNamespace(show_motion_vector=False)
        ty = SimpleNamespace(motion_vector=lambda: (25.0, 12.0))
        surf = self._blank()
        TySimDrawPathMixin._draw_motion_vector(
            sim, surf, ty, (50, 50), {'la': 10.0, 'lo': 120.0}, 255)
        self.assertEqual(self._ink(surf), 0)

    def test_seam_guard_draws_nothing(self):
        """投影把箭尖甩到屏幕另一端(跨 0 度经线)时不画, 避免横穿全屏的假箭头。"""
        from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin
        ty = SimpleNamespace(motion_vector=lambda: (25.0, 12.0))
        surf = self._blank()
        TySimDrawPathMixin._draw_motion_vector(
            self._sim(shift_x=180), surf, ty, (50, 50), {'la': 10.0, 'lo': 120.0}, 255)
        self.assertEqual(self._ink(surf), 0)

    def test_no_samples_draws_nothing(self):
        from app.ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin
        ty = SimpleNamespace(motion_vector=lambda: None)
        surf = self._blank()
        TySimDrawPathMixin._draw_motion_vector(
            self._sim(), surf, ty, (50, 50), {'la': 10.0, 'lo': 120.0}, 255)
        self.assertEqual(self._ink(surf), 0)


if __name__ == '__main__':
    unittest.main()
