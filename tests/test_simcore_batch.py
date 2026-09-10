# tests/test_simcore_batch.py
"""SimCore 批量采样/向量化的等价性测试。

背景(性能): 高低压标记原来是 120x360 双层 Python 循环(实测 0.13s/帧),
3000 个粒子的 step 逐点调用 wind_at(实测 0.063s/帧)。改成向量化后必须与
标量实现逐点等价, 否则就是"更快但画错"。
"""
import math
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import numpy as np
import pygame  # noqa: F401  (render 模块需要 pygame)

from simulator.simcore import core as V
from simulator.simcore import render as R


def make_sim(with_tc=True, seed=5):
    sim = V.init({
        "month": 8, "seed": seed,
        "params": {"shLat": 31.0, "shLon": 146.0, "shAmp": 10.0,
                   "random": 0.55, "genesis": 0.0, "dataMode": "off",
                   "dataYear": 2000, "dataClim": 1.0, "dataDayCycle": 1.0},
    })
    V.env_update(sim)
    if with_tc:
        vmax = 70.0
        sim["tcs"].append({
            "id": 1, "lon": 135.0, "lat": 18.0, "vmax": vmax,
            "pmin": V.pmin_from_v(vmax), "rmw": V.rmw_from_v(vmax),
            "b": 1.5, "mu": 8.0, "mv": 4.0, "dead": False,
            "erc": None, "track": [], "age": 24.0, "phase": "TC", "land": 0.0,
        })
    return sim


def sample_points(n, seed=11):
    rng = np.random.default_rng(seed)
    lon = rng.uniform(V.C["LON0"] - 5, V.C["LON1"] + 5, n)
    lat = rng.uniform(V.C["LAT0"] - 5, V.C["LAT1"] + 5, n)
    # 额外插入 TC 附近/内部/边界与网格外的点, 覆盖 clamp 与 r<1 分支
    extra = [(135.0, 18.0), (135.0001, 18.0), (135.5, 18.2),
             (143.0, 18.0), (160.0, 30.0), (0.0, 0.0), (359.0, 60.0)]
    lon = np.concatenate([lon, np.array([p[0] for p in extra])])
    lat = np.concatenate([lat, np.array([p[1] for p in extra])])
    return lon, lat


class TestBatchSampling(unittest.TestCase):
    def setUp(self):
        self.sim = make_sim()

    # 容许误差: 标量版在 float32 场上按 NumPy 弱提升求值, 批量版用 float64 权重,
    # 两者相差 ≤ float32 eps(~1e-7)。风速量级是几十 kt, 1e-5 远低于物理噪声。
    ATOL = 1e-5

    def test_env_bilinear_arr_matches_scalar(self):
        env = self.sim["env"]
        lon, lat = sample_points(400)
        for name in ("u850", "v850", "uMid", "sst"):
            arr = env[name]
            got = V.env_bilinear_arr(arr, lon, lat)
            want = np.array([V.env_bilinear(arr, float(a), float(b))
                             for a, b in zip(lon, lat)])
            np.testing.assert_allclose(got, want, atol=self.ATOL, rtol=0,
                                       err_msg=name)

    def test_land_at_arr_matches_scalar(self):
        lon, lat = sample_points(300)
        got = V.land_at_arr(self.sim, lon, lat)
        want = np.array([V.land_at(self.sim, float(a), float(b))
                         for a, b in zip(lon, lat)])
        np.testing.assert_allclose(got, want, atol=self.ATOL, rtol=0)

    def test_wind_at_arr_matches_scalar(self):
        lon, lat = sample_points(500)
        for level in (0.0, 1.0):
            gu, gv = V.wind_at_arr(self.sim, lon, lat, level)
            wu = np.array([V.wind_at(self.sim, float(a), float(b), level)[0]
                           for a, b in zip(lon, lat)])
            wv = np.array([V.wind_at(self.sim, float(a), float(b), level)[1]
                           for a, b in zip(lon, lat)])
            np.testing.assert_allclose(gu, wu, atol=self.ATOL, rtol=0,
                                       err_msg=f"u level={level}")
            np.testing.assert_allclose(gv, wv, atol=self.ATOL, rtol=0,
                                       err_msg=f"v level={level}")

    def test_wind_at_arr_without_tc(self):
        sim = make_sim(with_tc=False)
        lon, lat = sample_points(200)
        gu, gv = V.wind_at_arr(sim, lon, lat, 0.0)
        wu = np.array([V.wind_at(sim, float(a), float(b), 0.0)[0]
                       for a, b in zip(lon, lat)])
        wv = np.array([V.wind_at(sim, float(a), float(b), 0.0)[1]
                       for a, b in zip(lon, lat)])
        np.testing.assert_allclose(gu, wu, atol=self.ATOL, rtol=0)
        np.testing.assert_allclose(gv, wv, atol=self.ATOL, rtol=0)


class TestPressureExtremes(unittest.TestCase):
    @staticmethod
    def _bruteforce(p, ny, nx):
        lows, highs = [], []
        for j in range(1, ny - 1):
            for i in range(1, nx - 1):
                v = p[j, i]
                nb = [p[j, i - 1], p[j, i + 1], p[j - 1, i], p[j + 1, i]]
                if v < min(nb) - 0.4:
                    lows.append((j, i))
                elif v > max(nb) + 0.4:
                    highs.append((j, i))
        return lows, highs

    def test_matches_bruteforce_on_random_field(self):
        rng = np.random.default_rng(7)
        p = (1010.0 + rng.normal(0.0, 6.0, (V.ENVNY, V.ENVNX))).astype(np.float64)
        # 埋一个明显低压与一个明显高压
        p[40, 100] = 960.0
        p[70, 250] = 1040.0
        lj, li, hj, hi = R._pressure_extremes(p, V.ENVNY, V.ENVNX)
        lows, highs = self._bruteforce(p, V.ENVNY, V.ENVNX)
        self.assertEqual(sorted(zip(lj.tolist(), li.tolist())), sorted(lows))
        self.assertEqual(sorted(zip(hj.tolist(), hi.tolist())), sorted(highs))
        self.assertIn((40, 100), lows)
        self.assertIn((70, 250), highs)

    def test_real_field_matches_bruteforce(self):
        sim = make_sim()
        p = sim["env"]["pTot"]
        lj, li, hj, hi = R._pressure_extremes(p, V.ENVNY, V.ENVNX)
        lows, highs = self._bruteforce(p, V.ENVNY, V.ENVNX)
        self.assertEqual(sorted(zip(lj.tolist(), li.tolist())), sorted(lows))
        self.assertEqual(sorted(zip(hj.tolist(), hi.tolist())), sorted(highs))


class TestParticlesVectorized(unittest.TestCase):
    def setUp(self):
        self.sim = make_sim()
        self.dt = 1e-6                      # 位移可忽略 → 不会触发重生

    def _ocean_particles(self, n=64):
        p = R.Particles(n)
        # 找一片安全洋面(不在陆地、离边界足够远)
        good = []
        lo, la = 150.0, 12.0
        while len(good) < n and la < 40.0:
            if V.land_at(self.sim, lo, la) <= 0.2:
                good.append((lo, la))
            lo += 0.3
            if lo > 175.0:
                lo = 150.0
                la += 0.3
        self.assertEqual(len(good), n, "找不到足够的洋面采样点")
        p.lon = np.array([g[0] for g in good], dtype=np.float64)
        p.lat = np.array([g[1] for g in good], dtype=np.float64)
        p.ol = p.lon.copy()
        p.oa = p.lat.copy()
        return p

    def test_step_matches_scalar_reference(self):
        vec = self._ocean_particles()
        ref = R.Particles(len(vec.lon))
        ref.lon = vec.lon.copy()
        ref.lat = vec.lat.copy()
        ref.ol = vec.ol.copy()
        ref.oa = vec.oa.copy()
        # 标量参考实现(改动前的逐点逻辑)
        for k in range(ref.n):
            u, v = V.wind_at(self.sim, ref.lon[k], ref.lat[k], 0)
            ref.ol[k] = ref.lon[k]
            ref.oa[k] = ref.lat[k]
            ref.u[k] = u
            ref.v[k] = v
            ref.lon[k] += u * 3.6 * self.dt / (111.32 * math.cos(ref.lat[k] * V.C['DEG']))
            ref.lat[k] += v * 3.6 * self.dt / 110.57
        vec.step(self.sim, self.dt)
        np.testing.assert_allclose(vec.u, ref.u, atol=1e-9)
        np.testing.assert_allclose(vec.v, ref.v, atol=1e-9)
        np.testing.assert_allclose(vec.lon, ref.lon, atol=1e-9)
        np.testing.assert_allclose(vec.lat, ref.lat, atol=1e-9)

    def test_step_respawns_out_of_domain(self):
        p = self._ocean_particles(16)
        p.lon[:] = V.C["LON0"] - 10.0        # 全部越界
        p.step(self.sim, self.dt)
        self.assertTrue(np.all(p.lon >= V.C["LON0"]))
        self.assertTrue(np.all(p.lon <= V.C["LON1"]))
        self.assertTrue(np.all(p.lat >= V.C["LAT0"]))
        self.assertTrue(np.all(p.lat <= V.C["LAT1"]))

    def test_step_respawns_on_land(self):
        sim = self.sim
        land = None
        lo, la = 100.0, 30.0
        while la < 45.0:
            if V.land_at(sim, lo, la) > 0.6:
                land = (lo, la)
                break
            lo += 0.5
            if lo > 130.0:
                lo = 100.0
                la += 0.5
        if land is None:
            self.skipTest("陆地掩码里找不到明显陆地格点")
        p = self._ocean_particles(4)
        p.lon[0], p.lat[0] = land
        p.step(self.sim, self.dt)
        # 契约: 陆地上的粒子会被重新投放(投放到哪是随机的, 可能又落在陆地,
        # 下一帧继续重投——与改动前的标量实现一致)
        self.assertNotAlmostEqual(float(p.lat[0]), land[1], places=6)
        self.assertTrue(V.C['LAT0'] <= p.lat[0] <= V.C['LAT1'])


class TestContourDrawing(unittest.TestCase):
    """气压等值线/高低压标记必须真的画出来。

    回归: 坐标是 np.float32 时 pygame.draw.line 会抛 TypeError, 而调用方用宽
    except 包着 → 整个气压层静默消失(只画到一半就中断)。
    """

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((320, 200))
        self.sim = make_sim()
        self.surf = pygame.Surface((320, 200))
        self.surf.fill((0, 0, 0))

    @staticmethod
    def _fx(lon):
        return int((lon % 360) * 0.8)

    @staticmethod
    def _fy(lat):
        return int((90.0 - lat) * 1.0)

    def test_draw_contours_draws_something(self):
        R.draw_contours(self.surf, self.sim, self._fx, self._fy)
        arr = pygame.surfarray.array3d(self.surf)
        self.assertGreater(int((arr.sum(axis=2) > 0).sum()), 0)

    def test_draw_contours_with_strong_gradient(self):
        # 造一个必然与所有等值线级别相交的场, 确保等值线分支被走到
        p = self.sim["env"]["pTot"]
        p[:, :] = 1000.0
        p[:, :p.shape[1] // 2] = 1040.0
        R.draw_contours(self.surf, self.sim, self._fx, self._fy)
        arr = pygame.surfarray.array3d(self.surf)
        self.assertGreater(int((arr.sum(axis=2) > 0).sum()), 0)


if __name__ == "__main__":
    unittest.main()
