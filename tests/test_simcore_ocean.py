# tests/test_simcore_ocean.py
"""模拟模式 P0/P2: 去云图后的海洋状态步 + 风切层 + 环流流线。

P0: 云场停算后, 冷尾流衰减不能再搭在 cloud_step 里(否则尾流永不恢复)。
P2: 尾流/OHC 消耗要随表层漂流平流(不然海温异常钉在原地),
    并新增"风切" "环流" 两个可显示图层。
"""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from simulator.simcore import core as V
from simulator.simcore import render as R

W, H = 1280, 740


def fx(lon):
    return lon / 360.0 * W


def fy(lat):
    return (60.0 - lat) / 120.0 * H


def make_sim(seed=5, n_tc=0):
    sim = V.init({"seed": seed})
    V.env_update(sim)
    for i in range(n_tc):
        tc = V.make_tc(sim, 130.0 + i * 6, 12.0 + i * 2, 45.0 + i * 5)
        tc["age"] = 40.0
        sim["tcs"].append(tc)
    return sim


def uniform_wind(sim, u_kt, v_kt):
    env = sim["env"]
    env["u850"] = np.full(env["u850"].shape, u_kt, dtype=np.float32)
    env["v850"] = np.full(env["v850"].shape, v_kt, dtype=np.float32)


def centroid(arr):
    w = np.asarray(arr, dtype=np.float64).reshape(V.ENVNY, V.ENVNX)
    tot = w.sum()
    if tot <= 1e-12:
        return None
    j, i = np.mgrid[0:V.ENVNY, 0:V.ENVNX]
    return float((i * w).sum() / tot), float((j * w).sum() / tot)


class TestOceanStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((64, 64))

    def test_wake_is_advected_downstream(self):
        """东向表层流应把冷尾流整体带向东(之前尾流钉在原地不动)。"""
        sim = make_sim()
        uniform_wind(sim, 40.0, 0.0)
        V.deposit_gauss(sim["wake"], 150.0, 20.0, 180.0, 0.4, 111.32, 110.57)
        c0 = centroid(sim["wake"])
        self.assertIsNotNone(c0)
        for _ in range(24):
            V.ocean_step(sim, 1.0)
        c1 = centroid(sim["wake"])
        self.assertGreater(c1[0], c0[0] + 0.25)          # 向东
        # 北半球表层漂流右偏: 东向风 -> 向南漂(j 向南为减小)
        self.assertLess(c1[1], c0[1] - 0.15)

    def test_wake_decays(self):
        sim = make_sim()
        uniform_wind(sim, 0.0, 0.0)
        V.deposit_gauss(sim["wake"], 150.0, 20.0, 180.0, 0.4, 111.32, 110.57)
        s0 = float(np.asarray(sim["wake"]).sum())
        for _ in range(24):
            V.ocean_step(sim, 1.0)
        s1 = float(np.asarray(sim["wake"]).sum())
        self.assertLess(s1, s0)
        self.assertGreater(s1, s0 * 0.80)                # 15 天尺度: 1 天只掉 ~6%

    def test_ohc_wake_slower_than_sst_wake(self):
        sim = make_sim()
        uniform_wind(sim, 0.0, 0.0)
        V.deposit_gauss(sim["wake"], 150.0, 20.0, 180.0, 0.4, 111.32, 110.57)
        V.deposit_gauss(sim["ohcWake"], 150.0, 20.0, 180.0, 0.4, 111.32, 110.57)
        w0 = float(np.asarray(sim["wake"]).sum())
        o0 = float(np.asarray(sim["ohcWake"]).sum())
        for _ in range(24):
            V.ocean_step(sim, 1.0)
        wr = float(np.asarray(sim["wake"]).sum()) / w0
        orr = float(np.asarray(sim["ohcWake"]).sum()) / o0
        self.assertGreater(orr, wr)                      # 45 天比 15 天恢复慢

    def test_wake_decays_even_without_cloud_step(self):
        """P0 回归: 不再调用 cloud_step, 尾流也必须照常衰减/平流。"""
        sim = make_sim()
        V.deposit_gauss(sim["wake"], 150.0, 20.0, 180.0, 0.4, 111.32, 110.57)
        s0 = float(np.asarray(sim["wake"]).sum())
        for _ in range(int(24 / V.C["DT"])):             # 1 个模拟日, 从不调 cloud_step
            V.sim_step(sim, V.C["DT"])
        s1 = float(np.asarray(sim["wake"]).sum())
        self.assertLess(s1, s0 * 0.98)


class TestShearLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((64, 64))
        cls.sim = make_sim(n_tc=3)

    def test_shape_and_land_mask(self):
        out = R.render_env_layer(self.sim, "shear")
        self.assertEqual(out.shape, (V.ENVNY, V.ENVNX, 4))
        self.assertEqual(out.dtype, np.uint8)
        land = self.sim["land"] >= 0.5
        self.assertTrue((out[..., 3][land] == 0).all())
        self.assertGreater(int((out[..., 3] > 0).sum()), 0)

    def test_color_grows_with_shear(self):
        sim = make_sim()
        env = sim["env"]
        env["shear"] = np.full(env["shear"].shape, 2.0, dtype=np.float32)
        low = R.render_env_layer(sim, "shear")
        env["shear"] = np.full(env["shear"].shape, 30.0, dtype=np.float32)
        high = R.render_env_layer(sim, "shear")
        self.assertTrue((low[..., :3] != high[..., :3]).any())


class TestStreamlines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((64, 64))
        cls.sim = make_sim(n_tc=2)

    def test_draws_and_caches(self):
        R._flow_cache.clear()
        R._flow_last[:] = [None, 0]
        surf = pygame.Surface((W, H))
        R.draw_streamlines(surf, self.sim, fx, fy)
        self.assertEqual(len(R._flow_cache), 1)
        first = R._flow_last[0]
        self.assertIsNotNone(first)
        self.assertIsNotNone(first.get_colorkey())
        # 同一 env 版本/视口再画一次: 命中的是同一张缓存
        R.draw_streamlines(surf, self.sim, fx, fy)
        self.assertIs(R._flow_last[0], first)
        self.assertGreater(int(pygame.surfarray.array3d(surf).sum()), 0)

    def test_seed_points_are_deterministic(self):
        a = R._stream_seeds(50)
        b = R._stream_seeds(50)
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()
