# tests/test_simcore_motion.py
"""SimCore 运动学: 三层引导 + 环带平均 + 一阶滞后 + β漂移高纬衰减。

对应"架空模式运动学"落地, 以及需求 3(台风移动/路径在任何情况下平滑):
  - 引导权重随强度/纬度单调变化, 且三层权重归一后仍是引导场的凸组合;
  - 均匀环境场下 steering_at 必须精确等于该场的三层加权值(无采样偏差);
  - 引导突变时移速一阶滞后收敛: 不过冲、单调、单步增量有界(路径不会折角)。
"""
import math
import unittest
from datetime import datetime, timedelta
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

    def test_deep_layer_never_dominates(self):
        """**深层(200hPa)权重永不超过约 0.45, 且与纬度无关**(轮次 102 重标定)。

        这里原来叫 `test_latitude_shifts_to_deep_layer`, 断言 "|lat|>26° 时深层权重
        >0.99" —— 那条断言把一个**被证伪的标定**锁死了(旧标定表那一行的 RMSE 是
        94.08 kt, 而台风移速只有 0~20kt, 数据本身就是坏的)。

        证据(工具 `tools/calib_steer.py`, 1739 个观测 6h 步): 30-40N 上 200hPa 的
        气候平均经向风是 **-4.2kt(向赤道)** —— 那是副热带急流, 不是引导气流;
        观测台风在那里仍以 +4.6 ~ +9.0kt **向极**移动。给深层 1.0 的权重会让台风
        一到 30N 就被按回赤道方向(实测 30-40N 给出 +26.4kt 纯东向、向极 -0.1),
        年尺度后果就是"最高纬度中位只有 16-18 度、EX 为 0、强端偏低"。
        """
        for lat in (-45, 0, 15, 25, 35, 50):
            ws, wm, wd = core.steering_weights(40, lat)
            eff_shallow = ws + 0.6 * wm            # 中层的 60% 来自浅层
            eff_deep = wd + 0.4 * wm
            self.assertAlmostEqual(eff_shallow + eff_deep, 1.0, places=6)
            self.assertGreater(eff_shallow, 0.5)
            self.assertLess(eff_deep, 0.45)

    def test_intensity_does_not_change_weights(self):
        """**权重由纬度决定, 不由强度决定** —— 数据里没找到强度的独立效应,
        而原来那条"越强越受深层引导"是拍的经验式(且方向错)。这里只锁住
        "同纬度下权重与强度无关"这个已被数据支持的约束。"""
        for lat in (10, 20, 30, 40):
            a = core.steering_weights(20, lat)
            b = core.steering_weights(140, lat)
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=6)


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


class TestAmodeTrackLock(unittest.TestCase):
    """A 模式(路径锁定)的**结构契约**(轮次 120 新增)。

    为什么必须有这组测试: A 模式从阶段 11 引入后 `tests/` 里**零覆盖**
    (当时 `grep -rl track_lock tests/` 是空的), 于是 `motion_step` 里
    "设完位置就 `return`" 这个错误活了 19 轮 —— 锁路径的台风
    **不沉积冷尾流 / 不消耗 OHC / 不检测登陆 / 不累计 distKm 与 ACE**,
    脚下的 MPI 永远停在气候态(热带 ~195kt), 强度系统性高偏 +40~+100kt。
    这组测试把"两模式必须共用尾段"和"位置只能被赋一次"钉死。
    """

    def setUp(self):
        self.sim = make_sim(seed=11)
        self.t0 = datetime(2025, 8, 10, 0, 0)
        # 6h 一步、向西北移动: 1.35°E / 0.75°N per 6h ≈ 167km/6h ≈ 15.0kt
        #   —— 必须 > 6kt, 否则尾流门限 `spd>6` 不触发,
        #   测试就会因为"没沉积"而假通过/假失败。
        self.rows = [
            {'t': self.t0 + timedelta(hours=0), 'lon': 141.0, 'lat': 14.5,
             'vmax': 55, 'name': 'WP99'},
            {'t': self.t0 + timedelta(hours=6), 'lon': 142.35, 'lat': 15.25,
             'vmax': 60, 'name': 'WP99'},
            {'t': self.t0 + timedelta(hours=12), 'lon': 143.7, 'lat': 16.0,
             'vmax': 65, 'name': 'WP99'},
        ]
        from simulator.simcore import import_tc
        self.sim['start'] = self.t0
        self.sim['t'] = 0.0
        self.tc = import_tc.make_from_track(self.sim, self.rows, mode='A')

    def test_first_step_stays_at_obs_start(self):
        """第 1 步: 起报点必须**就是**观测起报点(位置只被赋一次)。"""
        self.assertIsNotNone(self.tc)
        core.motion_step(self.sim, self.tc, 6.0)
        self.assertAlmostEqual(self.tc['lon'], 141.0, places=6)
        self.assertAlmostEqual(self.tc['lat'], 14.5, places=6)

    def test_position_equals_observation_no_double_step(self):
        """第 2 步: 位置必须等于 obs(t), **不能**再叠加一次观测位移。

        这就是"双重位移"回归: 若把位移积分写在共用尾段里, 锁定的位置会变成
        obs(t)+Δ = obs(t+6h) 附近, 而且下一步 `_d`(obs(t+dt)−当前位置)只剩残差
        ⇒ mu/mv≈0 ⇒ 尾流门限失效(修 bug 反被架空)。
        """
        core.motion_step(self.sim, self.tc, 6.0)          # t=0
        self.sim['t'] = 6.0
        core.motion_step(self.sim, self.tc, 6.0)          # t=6h
        self.assertAlmostEqual(self.tc['lon'], 142.35, places=6)
        self.assertAlmostEqual(self.tc['lat'], 15.25, places=6)

    def test_observed_motion_vector_is_used(self):
        """mu/mv 必须还原**观测移速**(强度方程的移速项、尾流沉积都要用它)。"""
        core.motion_step(self.sim, self.tc, 6.0)
        self.sim['t'] = 6.0
        core.motion_step(self.sim, self.tc, 6.0)
        dlon = (142.35 - 141.0) * 111.32 * math.cos(15.25 * core.C['DEG'])
        dlat = (15.25 - 14.5) * 110.57
        spd = math.hypot(dlon, dlat) / (1.852 * 6.0)      # km -> kt
        self.assertGreater(spd, 6.0)
        self.assertAlmostEqual(math.hypot(self.tc['mu'], self.tc['mv']),
                               spd, delta=0.2)
        # 观测是**向西北**移动 ⇒ u>0(经度增加), v>0(纬度增加)
        self.assertGreater(self.tc['mu'], 0.0)
        self.assertGreater(self.tc['mv'], 0.0)

    def test_locked_mode_shares_wake_and_distance_tail(self):
        """A 模式不能跳过共用尾段: distKm / 冷尾流 / ACE 都必须照常累计。

        这三个量以前全被 `return` 跳过 —— distKm 让移动距离统计为 0,
        冷尾流让锁定路径的台风永远不冷却海面(强度高偏的根因)。
        """
        core.motion_step(self.sim, self.tc, 6.0)
        self.sim['t'] = 6.0
        d0 = self.tc['distKm']
        w0 = float(self.sim['wake'].sum())
        a0 = self.tc['ace']
        core.motion_step(self.sim, self.tc, 6.0)
        # 6h 走 hypot(1.35°×111.32×cos15.25, 0.75°×110.57) = 167.0km
        self.assertAlmostEqual(self.tc['distKm'] - d0, 167.0, delta=6.0)
        self.assertGreater(float(self.sim['wake'].sum()), w0)   # 冷尾流沉积
        self.assertGreater(self.tc['ace'], a0)                  # ACE 累计
        # 顺带: 冷尾流必须在**运动方向右侧**(北半球向西北移动 => 右前方),
        # 用质心经度与台风位置比较, 避免"沉积到任意位置也算过"。
        wk = self.sim['wake'].reshape(core.ENVNY, core.ENVNX)
        jj = int((self.tc['lat'] - core.C['LAT0']) / core.C['ENVD'])
        ii = int((self.tc['lon'] - core.C['LON0']) / core.C['ENVD'])
        box = wk[max(0, jj - 8):jj + 8, max(0, ii - 8):ii + 8]
        self.assertGreater(float(box.sum()), 0.0)


class TestShPoleOverride(unittest.TestCase):
    """`sh_pole_steer` 的三个参数必须真的能被 `sim['params']` 覆盖(轮次 120)。

    为什么值得单独测: 轮次 121+ 的扫参台(`tools/sweep_pole.py`)就是靠这个覆盖来
    "不改源码扫参"的。如果覆盖**静默失效**, 扫参会把同一组参数跑 5 遍、报出 5 行
    一模一样的数 —— 那是"看起来在做实验、其实什么都没做"的典型(比报错更危险)。

    ⚠ 另一个陷阱(本次现场踩到): `sh_pole_steer` 依赖 `env['shRidgeN']`, 而后者只在
    **`dataMode=True`**(气候库)那条分支里计算(`core.py` 的 `if DATA is not None and
    'u850' in DATA`)。默认 `dataMode=False` 的解析场路径**根本没有副高脊线**,
    于是 `sh_pole_steer` 恒返回 0 —— 也就是说这个"向极推速"项在解析模式下**不存在**。
    验收/扫参一律走 `dataMode=True`, 所以本测试也必须用同一套 params。
    """

    def test_override_is_honoured(self):
        sim = core.init({'seed': 3,
                         'params': {'precision': '100ms', 'dataMode': True,
                                    'forecast': False}})
        core.env_update(sim)
        if 'shRidgeN' not in sim['env']:
            self.skipTest('env 里没有副高脊线场(需要 dataMode=True)')
        lon, lat = 140.0, 40.0
        base = core.sh_pole_steer(sim, lon, lat)[1]
        self.assertGreater(base, 0.0)           # 默认参数下该点有向极修正
        # ① 上限按比例缩放: 未饱和时 mag ∝ shPoleMax
        sim['params']['shPoleMax'] = 2.0
        self.assertAlmostEqual(core.sh_pole_steer(sim, lon, lat)[1],
                               base * 2.0 / core._SH_POLE_MAX, places=6)
        # ② 上限置零 = 完全关闭
        sim['params']['shPoleMax'] = 0.0
        self.assertEqual(core.sh_pole_steer(sim, lon, lat)[1], 0.0)
        # ③ 启动纬度抬到很北(脊线以北 60 度才生效) -> 该点不再有修正
        sim['params']['shPoleMax'] = core._SH_POLE_MAX
        sim['params']['shPoleS'] = -60.0
        self.assertEqual(core.sh_pole_steer(sim, lon, lat)[1], 0.0)
        # ④ 南半球必须镜像(用 shRidgeS, 符号相反)
        sim['params'].pop('shPoleS', None)
        self.assertLess(core.sh_pole_steer(sim, lon, -lat)[1], 0.0)


class TestRiAmpShape(unittest.TestCase):
    """RI 幅度必须能**随强度成形**(`riAmpForm=vshape`, 轮次 121 预备, 默认 legacy)。

    观测的**升步均值**(逐格, kt/6h): <34kt **5.3** / 34-48 **6.3** / 100+ **12.7**。
    legacy 式 `20*(0.35+0.65*_head)` 在 vmax=25/vpi≈190 时给 **~18 kt/6h = 73 kt/天**
    —— 这正是 `tools/analyze_amode_intensity.py` 测到的"弱端 24h +49~+74kt"的通道,
    也是 `probe_lifecycle` 测到的"低端增强速率 19.4 vs 观测 8.8 kt/天"。
    代码自己的注释("低强度端必须限制爆发幅度")说明**意图本来就是限制低端**,
    反了的原因是 `_head` 在低强度时接近 1, 反而把幅度放大。

    这个测试用两个**同 seed、同流程**的模拟做 A/B(因此随机数序列完全一致,
    差异只可能来自那个上限本身)。
    """

    def _one_step_gain(self, over):
        sim = core.init({'seed': 5, 'params': {'precision': '100ms',
                                               'dataMode': True,
                                               'forecast': False}})
        core.env_update(sim)
        sim['params'].update(over)
        tc = core.make_tc(sim, 145.0, 16.0, 25.0)
        tc['ri'] = {'t': 0.0, 'life': 36.0}      # 直接给一个 RI 窗口
        v0 = float(tc['vmax'])
        core.intensity_step(sim, tc, 6.0)
        return float(tc['vmax']) - v0, float(tc['env'].get('vpi') or 0.0)

    def test_vshape_lowers_the_low_end_burst(self):
        d_leg, vpi = self._one_step_gain({})
        self.assertGreater(vpi, 100.0)            # 该点环境确实有利(否则测不到上限)
        d_new, _ = self._one_step_gain({'riAmpForm': 'vshape'})
        self.assertLess(d_new, 0.5 * d_leg,
                        'vshape 应把 25kt 处的爆发幅度砍到一半以下 '
                        '(legacy %.1f kt/6h vs vshape %.1f)' % (d_leg, d_new))
        self.assertLessEqual(d_new, 8.0)          # 观测低档升步均值只有 ~5-6 kt/6h


class TestHiTauScale(unittest.TestCase):
    """高段松弛系数 `hiTauScale` 必须真的起作用(B2 上尾加厚的前置验证, 轮次 142)。

    为什么先验这个: 轮次 136 把它从写死的 0.6 参数化, 而**第一次测试反而显示"没效果"**
    —— 原因是我把 MPI 抬到 195(不现实), 于是**每步上限 `_cap6` 变成绑定约束**,
    无论 tau 多小都被截到同一个值。换成现实 MPI(≈135)才看得出差别:
        默认 0.6 -> +3.54 kt/6h | 0.35 -> +6.50 | 0.15 -> +6.50(已被上限截住)
    ⇒ 这条测试同时锁住"参数有效"与"上限会在 ~0.35 附近接管"这两件事。
    """

    def _step(self, scale, sst=26.5, v0=105.0):
        sim = core.init({'seed': 7, 'params': {'precision': '100ms',
                                               'dataMode': True,
                                               'forecast': False}})
        core.env_update(sim)
        if scale is not None:
            sim['params']['hiTauScale'] = scale
        for k, val in (('sst', sst), ('shear', 8.0)):
            sim['env'][k] = sim['env'][k] * 0.0 + val
        tc = core.make_tc(sim, 150.0, 22.0, v0)
        before = float(tc['vmax'])
        core.intensity_step(sim, tc, 6.0)
        return float(tc['vmax']) - before, float(tc['env'].get('vpi') or 0.0)

    def test_hi_tau_scale_speeds_up_the_top_band(self):
        d0, vpi = self._step(None)
        self.assertLess(vpi, 160.0,
                        '该用例要求现实 MPI(上限一旦绑定就看不出 tau 的效果)')
        d1, _ = self._step(0.35)
        self.assertGreater(d1, d0 + 1.0,
                           'hiTauScale 必须让 100kt 以上的增长更快 '
                           '(默认 %.2f vs 0.35 %.2f)' % (d0, d1))
        d2, _ = self._step(0.15)
        self.assertLessEqual(d2, d1 + 0.5, '再降 tau 应被每步上限截住(不该继续线性变快)')


if __name__ == '__main__':
    unittest.main()
