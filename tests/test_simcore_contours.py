# tests/test_simcore_contours.py
"""等压线提取的向量化改动必须与旧实现逐像素等价。

背景: 原 draw_contours 逐格逐层走 Python 循环(14 层 x 数万格, 实测 40.9ms/帧),
改成向量化 + 与视图解耦的缓存后 2.35ms/帧(17x)。这类"纯提速"改动最容易
悄悄改变几何(边号 -> 格内位置的映射写错会让整层等值线错位), 因此这里把
改动前的算法原样保留为参照, 用栅格化位图做等价性断言。
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
LEVELS = [976, 980, 984, 988, 992, 996, 1000, 1004, 1008, 1012,
          1016, 1020, 1024, 1028]


def _screen(lon):
    return lon / 360.0 * W


def _screen_lat(lat):
    return (60.0 - lat) / 120.0 * H


def make_sim(n=4):
    sim = V.init({"seed": 1})
    V.env_update(sim)
    for i in range(n):
        tc = V.make_tc(sim, 130.0 + i * 7, 12.0 + i * 3, 45.0 + i * 6)
        tc["age"] = 40.0
        sim["tcs"].append(tc)
    return sim


def reference_segments(p, xs0, xs1, ys0, ys1):
    """改动前的 draw_contours 几何部分(逐字保留, 作为等价性参照)。"""
    segs = []
    for level in LEVELS:
        v00, v10, v01, v11 = p[:-1, :-1], p[:-1, 1:], p[1:, :-1], p[1:, 1:]
        mn = np.minimum(np.minimum(v00, v10), np.minimum(v01, v11))
        mx = np.maximum(np.maximum(v00, v10), np.maximum(v01, v11))
        mask = (level >= mn) & (level <= mx)
        jj, ii = np.where(mask)
        for j, i in zip(jj, ii):
            a00, a10, a01, a11 = v00[j, i], v10[j, i], v01[j, i], v11[j, i]
            x0, y0 = xs0[i], ys0[j]
            x1, y1 = xs1[i], ys1[j]
            pts = []
            if (a00 <= level) != (a10 <= level):
                t = (level - a00) / (a10 - a00 + 1e-9)
                pts.append((x0 + t * (x1 - x0), y0))
            if (a00 <= level) != (a01 <= level):
                t = (level - a00) / (a01 - a00 + 1e-9)
                pts.append((x0, y0 + t * (y1 - y0)))
            if (a10 <= level) != (a11 <= level):
                t = (level - a10) / (a11 - a10 + 1e-9)
                pts.append((x1, y0 + t * (y1 - y0)))
            if (a01 <= level) != (a11 <= level):
                t = (level - a01) / (a11 - a01 + 1e-9)
                pts.append((x0 + t * (x1 - x0), y1))
            if len(pts) == 2:
                segs.append(pts)
    return segs


class TestContourEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((64, 64))
        cls.sim = make_sim()
        cls.p = cls.sim["env"]["pTot"]
        ny, nx = V.ENVNY, V.ENVNX
        cls.xs0 = [_screen(V.C["LON0"] + (i + 0.5) * V.C["ENVD"]) for i in range(nx)]
        cls.xs1 = [_screen(V.C["LON0"] + (i + 1.5) * V.C["ENVD"]) for i in range(nx)]
        cls.ys0 = [_screen_lat(V.C["LAT0"] + (j + 0.5) * V.C["ENVD"]) for j in range(ny)]
        cls.ys1 = [_screen_lat(V.C["LAT0"] + (j + 1.5) * V.C["ENVD"]) for j in range(ny)]

    def _rasterize(self, segs):
        s = pygame.Surface((W, H))
        s.fill((0, 0, 0))
        for p0, p1 in segs:
            pygame.draw.line(s, (200, 205, 215),
                             (float(p0[0]), float(p0[1])),
                             (float(p1[0]), float(p1[1])), 1)
        return s

    def _new_segments_screen(self, p=None):
        p = self.p if p is None else p
        seg = R._contour_segments(p, LEVELS,
                                  key=("test", tuple(LEVELS), self.p.shape)
                                  if p is self.p else None)
        self.assertIsNotNone(seg)
        j0, i0, u0, v0, j1, i1, u1, v1 = seg
        xs0 = np.asarray(self.xs0)
        xs1 = np.asarray(self.xs1)
        ys0 = np.asarray(self.ys0)
        ys1 = np.asarray(self.ys1)
        x0 = xs0[i0] + u0 * (xs1[i0] - xs0[i0])
        y0 = ys0[j0] + v0 * (ys1[j0] - ys0[j0])
        x1 = xs0[i1] + u1 * (xs1[i1] - xs0[i1])
        y1 = ys0[j1] + v1 * (ys1[j1] - ys0[j1])
        return list(zip(zip(x0.tolist(), y0.tolist()), zip(x1.tolist(), y1.tolist())))

    def test_segment_count_matches(self):
        ref = reference_segments(self.p, self.xs0, self.xs1, self.ys0, self.ys1)
        self.assertEqual(len(ref), len(self._new_segments_screen()))

    def test_rasterization_matches(self):
        """逐像素对比: 允许极少数端点取整差异(<0.05% 像素), 非黑像素数必须相同。"""
        ref = self._rasterize(reference_segments(self.p, self.xs0, self.xs1,
                                                 self.ys0, self.ys1))
        new = self._rasterize(self._new_segments_screen())
        a = pygame.surfarray.array3d(ref)
        b = pygame.surfarray.array3d(new)
        diff = int((a != b).any(axis=2).sum())
        self.assertLess(diff, W * H * 0.0005, f"等值线位图差异 {diff} 像素")
        self.assertEqual(int((a.sum(axis=2) > 0).sum()),
                         int((b.sum(axis=2) > 0).sum()))

    def test_steep_field_multiple_levels_per_cell(self):
        """一个格子同时被多条等值线穿过时不能整片丢掉。

        回归: 初版向量化把"层号"漏在配对键外, 陡坡格子(8 条线都穿同一列)被当成
        4 交点的鞍点全部丢弃 —— 真实场里台风中心附近最陡, 等值线会整片消失。
        """
        p = np.full_like(self.p, 1000.0)
        p[:, :p.shape[1] // 2] = 1040.0
        ref = reference_segments(p, self.xs0, self.xs1, self.ys0, self.ys1)
        seg = R._contour_segments(p, LEVELS, key=None)
        self.assertIsNotNone(seg)
        self.assertEqual(len(ref), len(seg[0]))
        old = self._rasterize(ref)
        new = self._rasterize(self._new_segments_screen(p))
        a = pygame.surfarray.array3d(old)
        b = pygame.surfarray.array3d(new)
        diff = int((a != b).any(axis=2).sum())
        self.assertLess(diff, W * H * 0.0005, f"阶跃场位图差异 {diff} 像素")
        self.assertGreater(int((b.sum(axis=2) > 0).sum()), 0)

    def test_cache_reuses_result(self):
        key = ("cachetest", tuple(LEVELS), self.p.shape)
        R._contour_cache.pop(key, None)
        first = R._contour_segments(self.p, LEVELS, key=key)
        second = R._contour_segments(self.p, LEVELS, key=key)
        self.assertIs(first, second)

    def test_draw_contours_runs_on_cached_path(self):
        surf = pygame.Surface((W, H))
        R.draw_contours(surf, self.sim, _screen, _screen_lat)
        R.draw_contours(surf, self.sim, _screen, _screen_lat)
        self.assertGreater(int(pygame.surfarray.array3d(surf).sum()), 0)


if __name__ == "__main__":
    unittest.main()
