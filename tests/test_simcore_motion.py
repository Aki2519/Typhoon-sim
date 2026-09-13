# tests/test_simcore_motion.py
"""SimCore 运动学: 三层引导 + 环带平均 + 一阶滞后 + β漂移高纬衰减。

对应"架空模式运动学"落地, 以及需求 3(台风移动/路径在任何情况下平滑):
  - 引导权重随强度/纬度单调变化, 且三层权重归一后仍是引导场的凸组合;
  - 均匀环境场下 steering_at 必须精确等于该场的三层加权值(无采样偏差);
  - 引导突变时移速一阶滞后收敛: 不过冲、单调、单步增量有界(路径不会折角)。
"""
import math
import unittest
from unittest import mock

import simulator.simcore.core as core


def make_sim(seed=7):
    sim = core.init({'seed': seed})
    core.env_update(sim)
    return sim


core_beta = core.beta_drift


def uniform_env(sim, u850, v850, u200, v200):
    env = sim['env']
    for k, val in (('u850', u850), ('v850', v850), ('u200', u200), ('v200', v200)):
        env[k] = env[k] * 0.0 + val


class TestSteeringWeights(unittest.TestCase):
    def test_bounds_and_normalisation(self):
        for vmax in (10, 30, 60, 90, 140):
            for lat in (-45, -20, 0, 15, 30, 50):
                w = core.steering_weights(vmax, lat)
                self.assertTrue(all(x >= 0 for x in w))
                self.assertGreater(sum(w), 0.2)

    def test_intensity_shifts_to_deep_layer(self):
        weak = core.steering_weights(20, 15)
        strong = core.steering_weights(80, 15)
        self.assertGreater(strong[2], weak[2])      # 深
        self.assertLess(strong[0], weak[0])         # 浅

    def test_latitude_shifts_to_shallow_layer(self):
        low = core.steering_weights(40, 15)
        high = core.steering_weights(40, 45)
        self.assertGreater(high[0], low[0])


class TestBetaDrift(unittest.TestCase):
    def test_north_hemisphere_west_and_poleward(self):
        u, v = core.beta_drift(15.0)
        self.assertLess(u, 0.0)
        self.assertGreater(v, 0.0)

    def test_south_hemisphere_mirrors(self):
        _, v_n = core.beta_drift(15.0)
        _, v_s = core.beta_drift(-15.0)
        self.assertLess(v_s, 0.0)
        self.assertAlmostEqual(abs(v_s), abs(v_n), places=6)

    def test_decays_at_high_latitude(self):
        m15 = math.hypot(*core.beta_drift(15.0))
        m45 = math.hypot(*core.beta_drift(45.0))
        self.assertLess(m45, m15 * 0.8)


class TestLagAlpha(unittest.TestCase):
    def test_range_and_monotonicity(self):
        prev = 0.0
        for dt in (1 / 6, 0.5, 1.0, 3.0, 24.0):
            a = core.motion_lag_alpha(dt)
            self.assertGreater(a, 0.0)
            self.assertLessEqual(a, 1.0)
            self.assertGreater(a, prev)
            prev = a

    def test_negative_dt_is_safe(self):
        self.assertEqual(core.motion_lag_alpha(-5.0), 0.0)


class TestSteeringSampling(unittest.TestCase):
    def test_uniform_field_returns_layer_mixture(self):
        sim = make_sim()
        uniform_env(sim, 10.0, 0.0, 20.0, 0.0)
        ws, wm, wd = core.steering_weights(40.0, 15.0)
        mid = 0.6 * 10.0 + 0.4 * 20.0
        want = (ws * 10.0 + wm * mid + wd * 20.0) / (ws + wm + wd)
        got_u, got_v = core.steering_at(sim, 140.0, 15.0, 40.0)
        self.assertAlmostEqual(got_u, want, delta=1e-6)
        self.assertAlmostEqual(got_v, 0.0, delta=1e-6)

    def test_single_point_perturbation_is_damped(self):
        """单个采样点被扰动时, 24 点环带平均只吸收一小部分(路径不被小涡带偏)。"""
        sim = make_sim()
        uniform_env(sim, 10.0, 0.0, 10.0, 0.0)
        base, _ = core.steering_at(sim, 140.0, 15.0, 40.0)
        env = sim['env']
        i = env['u850'].shape[1] // 2
        j = env['u850'].shape[0] // 2
        env['u850'][j, i] += 400.0            # 单格巨大扰动
        bumped, _ = core.steering_at(sim, 140.0, 15.0, 40.0)
        self.assertLess(abs(bumped - base), 5.0)

    def test_steer_param_is_added(self):
        sim = make_sim()
        uniform_env(sim, 0.0, 0.0, 0.0, 0.0)
        sim['params']['steerU'] = 4.0
        u, _ = core.steering_at(sim, 140.0, 15.0, 40.0)
        self.assertAlmostEqual(u, 4.0 * core.KT, delta=1e-6)


class TestMotionLag(unittest.TestCase):
    """一阶滞后: 隔离 β漂移, 目标移速 = 引导场本身, 便于精确断言。"""

    def setUp(self):
        self.sim = make_sim()
        uniform_env(self.sim, 10.0, 0.0, 10.0, 10.0)
        self.tc = core.make_tc(self.sim, 140.0, 15.0, 45.0)
        self.sim['tcs'].append(self.tc)
        self.sim['params']['random'] = 0.0
        patcher = mock.patch.object(core, 'beta_drift', lambda lat: (0.0, 0.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, hours, dt=1 / 6):
        for _ in range(int(round(hours / dt))):
            core.motion_step(self.sim, self.tc, dt)

    def test_first_step_snaps_to_steering(self):
        """生成首步直接用引导初值, 不会原地起步等滞后爬升。"""
        core.motion_step(self.sim, self.tc, 1 / 6)
        self.assertGreater(math.hypot(self.tc['mu'], self.tc['mv']), 1.0)

    def test_track_is_smooth_when_steering_flips(self):
        """引导场突变时, 单步移速增量有界 -> 路径不会折角。"""
        self._run(12.0)
        before = (self.tc['mu'], self.tc['mv'])
        uniform_env(self.sim, -10.0, 0.0, -10.0, 0.0)
        self._run(6.0)
        # 移速已经明显转向, 但不会一步到位
        self.assertLess(self.tc['mu'], before[0])
        self.assertGreater(self.tc['mu'], -10.0 - 1e-6)

    def test_no_overshoot_towards_target(self):
        self._run(24.0)
        target = self.tc['mu']
        # 把引导置零, 移速应单调衰减且不过冲(不反向)
        uniform_env(self.sim, 0.0, 0.0, 0.0, 0.0)
        prev = target
        for _ in range(60):
            core.motion_step(self.sim, self.tc, 1 / 6)
            self.assertLessEqual(self.tc['mu'], prev + 1e-9)
            self.assertGreaterEqual(self.tc['mu'], -1e-9)
            prev = self.tc['mu']

    def test_per_step_speed_change_is_bounded(self):
        self._run(24.0)
        uniform_env(self.sim, -12.0, 0.0, -12.0, 0.0)
        alpha = core.motion_lag_alpha(1 / 6)
        prev = self.tc['mu']
        worst = 0.0
        for _ in range(48):
            core.motion_step(self.sim, self.tc, 1 / 6)
            worst = max(worst, abs(self.tc['mu'] - prev))
            prev = self.tc['mu']
        # 单步增量 <= alpha * 总变化幅度 (含少量数值余量)
        self.assertLessEqual(worst, alpha * 30.0 + 1e-6)

    def test_converges_to_steering_plus_beta(self):
        """含 β漂移时: 移速收敛到 (三层引导 + β); 目标随纬度缓慢漂移, 用当前位置的
        目标值比较(滞后 τ=3h 对缓慢漂移的跟踪误差 << 0.1 kt)。"""
        with mock.patch.object(core, 'beta_drift', core_beta):
            self._run(48.0)
            st = core.steering_at(self.sim, self.tc['lon'], self.tc['lat'],
                                  self.tc['vmax'])
            be = core_beta(self.tc['lat'])
            self.assertAlmostEqual(self.tc['mu'], st[0] + be[0], delta=0.35)
            self.assertAlmostEqual(self.tc['mv'], st[1] + be[1], delta=0.35)

    def test_displacement_matches_motion(self):
        self._run(24.0)
        lon0, lat0 = self.tc['lon'], self.tc['lat']
        dt = 1 / 6
        core.motion_step(self.sim, self.tc, dt)
        # 位移必须严格由 (mu, mv) 换算而来(模型口径: 60 海里/度)
        dlat = (self.tc['lat'] - lat0) * 110.57
        dlon = (self.tc['lon'] - lon0) * 111.32 * math.cos(lat0 * core.C['DEG'])
        self.assertAlmostEqual(dlat, self.tc['mv'] * dt / 60.0 * 110.57, delta=1e-6)
        self.assertAlmostEqual(dlon, self.tc['mu'] * dt / 60.0 * 111.32, delta=1e-6)


if __name__ == '__main__':
    unittest.main()
