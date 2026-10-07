# tests/test_simcore_ocean.py
"""模拟模式 P0/P2: 去云图后的海洋状态步 + 风切层 + 环流流线。

P0: 云场停算后, 冷尾流衰减不能再搭在 cloud_step 里(否则尾流永不恢复)。
P2: 尾流/OHC 消耗要随表层漂流平流(不然海温异常钉在原地),
    并新增"风切" "环流" 两个可显示图层。
"""
import os
import time
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
        """陆地像素必须被掩成透明。

        有 map/heightmap.png 时函数会升采样到 (720,2160) 并按真实海岸线裁切
        (6x6 重复); 没有时退回 0.5° 粗掩码 (120,360)。
        """
        out = R.render_env_layer(self.sim, "shear")
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(out.shape[2], 4)
        # 2026-09-19: 图层现在会**外推到两极**(±90), 所以行数比内核域(±60)多;
        # 列数不变。核心带(±60)仍按高分辨率海岸线裁切。
        hi = R._hi_land_mask()
        self.assertGreater(out.shape[1], 0)
        self.assertGreaterEqual(out.shape[0], V.ENVNY)
        ny = out.shape[0]
        core = int(round(ny * 120.0 / 180.0))          # ±60 占 180° 里的 120°
        c0 = (ny - core) // 2
        band = out[c0:c0 + core]
        # 2026-09-19: 掩码口径改了 —— **只挖"100% 陆地"的格点**
        # (用户: "图中有因为小岛造成的空缺…如果陆地占比达到 100% 再不模拟这个格子"),
        # 所以"高分辨率海岸线里是陆地"不再等于"图层透明"(部分陆地格照常着色,
        # 小岛才不会打洞)。这里改断言: 全陆格点必须透明。
        from simulator.simcore import core as _C
        _al = _C.all_land_mask()
        if _al is not None:
            ny2 = band.shape[0]
            ry = max(1, int(round(ny2 / _al.shape[0])))
            rx = max(1, int(round(band.shape[1] / _al.shape[1])))
            # land_cell.npz 是**南在上**(与内核 row0 一致), 而图层输出是北在上,
            # 比对前必须翻一次(原来漏了这一步, 于是 3.4 万像素的"错位差")
            al_hi = np.repeat(np.repeat(np.flipud(_al), ry, axis=0), rx, axis=1)
            if al_hi.shape == band.shape[:2]:
                self.assertEqual(int((band[..., 3][al_hi] > 0).sum()), 0,
                                 "100% 陆地的格子必须完全透明")
        elif hi is not None and band.shape[:2] == hi.shape:
            # 缓存缺失时的旧口径: 高分辨率海岸线全透明
            self.assertEqual(int((band[..., 3][np.flipud(hi)] > 0).sum()), 0)
        else:
            land = np.asarray(self.sim["land"]).reshape(V.ENVNY, V.ENVNX) >= 0.5
            import pygame as _pg
            small = _pg.transform.smoothscale(
                _pg.image.frombuffer(np.ascontiguousarray(band[..., 3]).tobytes(),
                                     (band.shape[1], band.shape[0]), 'A'),
                (V.ENVNX, V.ENVNY))
            am = _pg.surfarray.array_alpha(small).T
            self.assertEqual(int((am[land] > 0).sum()), 0)
        self.assertGreater(int((out[..., 3] > 0).sum()), 0)
        # 两极带必须有数据(用户 2026-09-19: "两极没有海温")
        if ny > core:
            pole_rows = np.concatenate([np.arange(0, c0), np.arange(c0 + core, ny)])
            self.assertGreater(int((out[pole_rows, :, 3] > 0).sum()), 0,
                               "两极没有填充数据")

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
        # 轮次 151: 节流状态由 `[surf, made_ms]` 改成**按图层名分开的 dict**
        # (原先 flow 与 current 共用一个, 洋流层会复用风场那张图)。
        R._flow_last.clear()
        surf = pygame.Surface((W, H))
        R.draw_streamlines(surf, self.sim, fx, fy)
        self.assertEqual(len(R._flow_cache), 1)
        first = R._flow_last['flow'][0]
        self.assertIsNotNone(first)
        self.assertIsNotNone(first.get_colorkey())
        # 同一 env 版本/视口再画一次: 命中的是同一张缓存
        R.draw_streamlines(surf, self.sim, fx, fy)
        self.assertIs(R._flow_last['flow'][0], first)
        self.assertGreater(int(pygame.surfarray.array3d(surf).sum()), 0)

    def test_flow_and_current_do_not_share_throttle(self):
        """轮次 151 回归: 环流与洋流的节流缓存必须**互相独立**。

        缺陷原貌: 两者共用一个 `[surf, made_ms]`, 而 `draw_streamlines` 先调用,
        于是 150ms 窗口内 `draw_currents` 直接复用风场那张流线图 —— 用户同时打开
        "环流"和"洋流"时, 洋流层画的是风场(实测地图新增 0 像素)。
        """
        R._flow_cache.clear()
        R._flow_last.clear()
        surf = pygame.Surface((W, H))
        # `draw_currents` 在 ocn 缺 u/v 时会提前返回(不进节流) —— 补一份洋流场
        self.sim.setdefault('ocn', {})
        self.sim['ocn']['u'] = np.full((V.ENVNY, V.ENVNX), 0.4, np.float32)
        self.sim['ocn']['v'] = np.full((V.ENVNY, V.ENVNX), 0.1, np.float32)
        R.draw_streamlines(surf, self.sim, fx, fy)     # 先建档 flow 的节流
        self.assertIn('flow', R._flow_last)
        self.assertNotIn('current', R._flow_last)
        R.draw_currents(surf, self.sim, fx, fy)
        self.assertIn('current', R._flow_last)
        # 两者是不同的 surface 对象, 没有互相借图
        self.assertIsNot(R._flow_last['flow'][0], R._flow_last['current'][0])

    def test_seed_points_are_deterministic(self):
        a = R._stream_seeds(50)
        b = R._stream_seeds(50)
        self.assertIs(a, b)

    def test_flow_throttle_rebuilds_after_window(self):
        """轮次 153 回归: 视图(key)变化后, 流线层**必须**在节流窗口过后重建。

        缺陷原貌: `_flow_throttle_put` 被放在"复用上一张节流图"那条路径上 ⇒
        每帧都刷新时间戳, 而**新 key 从未写进 `_flow_cache`** ⇒ 下一帧继续 miss、
        继续复用同一张旧图。只要帧间隔 <150ms, 环流/洋流层就**永久冻结**在旧视图
        (拖动/缩放地图后不再更新)。原实现只在真正重建后写时间戳, 所以 150ms 后
        必然重建一次、自愈。

        ⚠ 测法要点: 必须用**连续快帧**(每帧间隔 <150ms)跨过 150ms 窗口。
        早先版本用单次 `sleep(0.2)` 直接跨过窗口 —— 那样坏写法也会因为等到了
        超时而重建, **测试恒过、抓不到 bug**(已实测确认)。
        """
        R._flow_cache.clear()
        R._flow_last.clear()
        calls = []
        orig = R._build_streamlines

        def _spy(*a, **kw):
            calls.append(1)
            return orig(*a, **kw)

        R._build_streamlines = _spy
        try:
            surf = pygame.Surface((W, H))
            R.draw_streamlines(surf, self.sim, fx, fy)
            self.assertEqual(len(calls), 1, '首次应当重建一次')

            # 换 key(改 env 版本) —— 新 key 不在缓存里, 首帧允许复用节流图
            self.sim['_envVer'] = int(self.sim.get('_envVer', -1)) + 1
            R.draw_streamlines(surf, self.sim, fx, fy)

            # 连续快帧: 每个间隔都短于 150ms 节流窗口, 但累计远超 150ms。
            # 修好的版本会在窗口过期后重建并缓存新 key; 坏版本每帧刷新时间戳,
            # 永远等不到过期 ⇒ 一次都不重建(图层永久冻结)。
            n_before = len(calls)
            for _ in range(8):
                time.sleep(0.04)
                R.draw_streamlines(surf, self.sim, fx, fy)
            self.assertGreater(
                len(calls), n_before,
                '连续快帧下流线层从未重建(节流时间戳被复用路径永久刷新 ⇒ 图层冻结)')
        finally:
            R._build_streamlines = orig


if __name__ == "__main__":
    unittest.main()
