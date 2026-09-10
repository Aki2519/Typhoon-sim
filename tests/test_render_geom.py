# tests/test_render_geom.py
"""跨缝几何 (app/render_geom.py) 单元测试。

对应需求 1: 台风路径 / 洋区边界等长线跨越屏幕边界或 0° 经线时不能"乱飞"。
这里同时给出"反例控制": 不做归一化时同一组点确实会画出横跨全屏的伪线,
保证测试本身有牙齿。
"""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import numpy as np
import pygame

from app.render_geom import (clip_segment, clipped_segments, draw_polyline,
                             normalize_chain, segments_of, wrap_shift)


def longest_h_run(surface, color, tol=60):
    """最长水平连续同色像素数(伪线检测)。"""
    arr = pygame.surfarray.array3d(surface)
    mask = ((np.abs(arr[..., 0].astype(int) - color[0]) < tol)
            & (np.abs(arr[..., 1].astype(int) - color[1]) < tol)
            & (np.abs(arr[..., 2].astype(int) - color[2]) < tol))
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


class TestNormalizeChain(unittest.TestCase):
    def test_seam_gap_collapsed(self):
        wrap = 3600.0
        pts = [(3500.0, 10.0), (3560.0, 10.0), (100.0, 10.0), (220.0, 10.0)]
        out = normalize_chain(pts, wrap)
        for (x0, _), (x1, _) in zip(out, out[1:]):
            self.assertLessEqual(abs(x1 - x0), wrap / 2.0)

    def test_real_distance_preserved(self):
        # 相邻报点实际只差 5px: 归一化后仍是 5px, 而不是 wrap-5
        out = normalize_chain([(3599.0, 0.0), (4.0, 0.0)], 3600.0)
        self.assertAlmostEqual(out[1][0] - out[0][0], 5.0, places=6)

    def test_pan_only_shifts_whole_chain(self):
        wrap = 3600.0
        pts = [(120.0, 0.0), (400.0, 5.0), (3300.0, 9.0)]
        base = normalize_chain(pts, wrap)
        for d in (7.0, 123.0, 900.0):
            moved = normalize_chain([(x + d, y) for x, y in pts], wrap)
            delta = moved[0][0] - base[0][0]
            for (bx, _), (mx, _) in zip(base, moved):
                self.assertAlmostEqual(mx - bx, delta, places=6)

    def test_anchor_aligns_smooth_chain(self):
        wrap = 3600.0
        raw = normalize_chain([(3590.0, 0.0), (30.0, 0.0)], wrap)
        smooth = normalize_chain([(3595.0, 0.0), (2.0, 0.0), (20.0, 0.0)],
                                 wrap, anchor=raw[0][0])
        self.assertLess(abs(smooth[0][0] - raw[0][0]), wrap / 2.0)
        self.assertLess(abs(smooth[-1][0] - raw[-1][0]), wrap / 2.0)

    def test_zero_wrap_is_noop(self):
        pts = [(1.0, 2.0), (3.0, 4.0)]
        self.assertEqual(normalize_chain(pts, 0.0), pts)

    def test_wrap_shift_is_period_multiple(self):
        self.assertEqual(wrap_shift(10.0, 4000.0, 3600.0), 3600.0)
        self.assertEqual(wrap_shift(3590.0, 5.0, 3600.0), -3600.0)


class TestClipSegment(unittest.TestCase):
    def test_inside(self):
        seg = clip_segment((10, 10), (20, 20), 100, 100)
        self.assertEqual(seg, ((10.0, 10.0), (20.0, 20.0)))

    def test_outside_returns_none(self):
        self.assertIsNone(clip_segment((-10, -10), (-5, -5), 100, 100))
        self.assertIsNone(clip_segment((200, 10), (300, 10), 100, 100))

    def test_crossing_is_truncated(self):
        seg = clip_segment((-50, 50), (150, 50), 100, 100)
        self.assertIsNotNone(seg)
        self.assertAlmostEqual(seg[0][0], 0.0)
        self.assertAlmostEqual(seg[1][0], 100.0)

    def test_degenerate_point_inside(self):
        self.assertIsNotNone(clip_segment((50, 50), (50, 50), 100, 100))


class TestSegments(unittest.TestCase):
    def test_no_segment_dropped_at_seam(self):
        pts = [(3500.0, 0.0), (3560.0, 0.0), (100.0, 0.0), (160.0, 0.0)]
        segs = list(segments_of(pts, 3600.0, max_seg=200.0))
        self.assertEqual(len(segs), 3)          # 一段都不能少(旧实现会丢中间那段)
        for (x0, _), (x1, _) in segs:
            self.assertLessEqual(abs(x1 - x0), 200.0)

    def test_max_seg_breaks_real_jump(self):
        pts = [(0.0, 0.0), (5000.0, 0.0), (5000.0, 50.0)]
        segs = list(segments_of(pts, 0.0, max_seg=100.0))
        self.assertEqual(segs, [((5000.0, 0.0), (5000.0, 50.0))])

    def test_closed_polygon_last_edge_included(self):
        pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
        segs = list(segments_of(pts, 0.0, close=True))
        self.assertEqual(len(segs), 3)


class TestNoFlyingLine(unittest.TestCase):
    W, H, WRAP = 400, 200, 3600.0

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))

    def _raw_draw(self, pts):
        surf = pygame.Surface((self.W, self.H))
        surf.fill((0, 0, 0))
        for i in range(1, len(pts)):
            seg = clip_segment(pts[i - 1], pts[i], self.W, self.H)
            if seg is not None:
                pygame.draw.line(surf, (255, 0, 0),
                                 (int(seg[0][0]), int(seg[0][1])),
                                 (int(seg[1][0]), int(seg[1][1])), 2)
        return surf

    def test_normalized_has_no_full_width_line(self):
        pts = [(20.0, 100.0), (60.0, 100.0), (3600.0, 100.0), (3640.0, 100.0)]
        surf = pygame.Surface((self.W, self.H))
        surf.fill((0, 0, 0))
        drawn = draw_polyline(surf, (255, 0, 0), pts, self.WRAP, width=2)
        self.assertGreater(drawn, 0)
        self.assertLessEqual(longest_h_run(surf, (255, 0, 0)), 100)

    def test_control_unnormalized_does_fly(self):
        pts = [(20.0, 100.0), (60.0, 100.0), (3600.0, 100.0), (3640.0, 100.0)]
        self.assertGreaterEqual(longest_h_run(self._raw_draw(pts), (255, 0, 0)),
                                self.W - 60)

    def test_clipped_segments_all_inside_viewport(self):
        pts = [(3500.0, -20.0), (100.0, 260.0), (400.0, 100.0)]
        for (x0, y0), (x1, y1) in clipped_segments(pts, self.WRAP, self.W, self.H):
            for x, y in ((x0, y0), (x1, y1)):
                self.assertGreaterEqual(x, -1e-6)
                self.assertLessEqual(x, self.W + 1e-6)
                self.assertGreaterEqual(y, -1e-6)
                self.assertLessEqual(y, self.H + 1e-6)


if __name__ == "__main__":
    unittest.main()
