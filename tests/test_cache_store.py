# tests/test_cache_store.py
"""渲染层表面缓存(app/cache_store.py)与已迁移缓存的预算/透明性测试。

迁移目标: 把各模块"按条数 + FIFO"的手写缓存换成"按字节 + LRU", 避免图标
放大后单帧数 MB 时内存失控, 同时保证缓存对渲染结果完全透明(命中与重算
必须逐字节一致)。
"""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

from app.cache_store import SurfaceCache, surface_bytes
from app.typhoon import Typhoon


def make_surf(w, h, color=(10, 20, 30, 255)):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    s.fill(color)
    return s


class TestSurfaceCacheUnit(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))

    def test_put_get_and_stats(self):
        c = SurfaceCache("t", 10 ** 6)
        s = make_surf(10, 10)                      # 400B
        self.assertTrue(c.put("a", s))
        self.assertIs(c.get("a"), s)
        self.assertIsNone(c.get("missing"))
        self.assertIn("a", c)
        self.assertEqual(len(c), 1)
        self.assertEqual(c.total_bytes(), surface_bytes(s))
        st = c.stats()
        self.assertEqual(st["hits"], 1)
        self.assertEqual(st["misses"], 1)
        self.assertEqual(st["items"], 1)

    def test_byte_budget_evicts_oldest(self):
        c = SurfaceCache("t", 900)                 # 只放得下 2 个 400B
        a, b, d = make_surf(10, 10), make_surf(10, 10), make_surf(10, 10)
        c.put("a", a)
        c.put("b", b)
        c.put("c", d)
        self.assertEqual(len(c), 2)
        self.assertNotIn("a", c)                   # 最旧的先走
        self.assertIn("b", c)
        self.assertIn("c", c)
        self.assertLessEqual(c.total_bytes(), 900)
        self.assertEqual(c.stats()["evictions"], 1)

    def test_get_refreshes_lru_order(self):
        c = SurfaceCache("t", 900)
        c.put("a", make_surf(10, 10))
        c.put("b", make_surf(10, 10))
        c.get("a")                                 # a 变最近使用
        c.put("c", make_surf(10, 10))
        self.assertIn("a", c)                      # 被保留
        self.assertNotIn("b", c)                   # 旧的 FIFO 会淘汰 a

    def test_oversized_item_is_not_cached(self):
        c = SurfaceCache("t", 1000)
        big = make_surf(50, 50)                    # 10KB > 预算
        self.assertFalse(c.put("big", big))
        self.assertEqual(len(c), 0)
        self.assertEqual(c.total_bytes(), 0)

    def test_max_items_cap(self):
        c = SurfaceCache("t", 10 ** 7, max_items=3)
        for i in range(6):
            c.put(i, make_surf(10, 10))
        self.assertEqual(len(c), 3)
        self.assertIn(5, c)
        self.assertNotIn(0, c)

    def test_pop_and_clear(self):
        c = SurfaceCache("t", 10 ** 6)
        s = make_surf(10, 10)
        c.put("a", s)
        self.assertIs(c.pop("a"), s)
        self.assertEqual(c.total_bytes(), 0)
        c.put("b", make_surf(10, 10))
        c.clear()
        self.assertEqual(len(c), 0)
        self.assertEqual(c.total_bytes(), 0)

    def test_replacement_updates_bytes(self):
        c = SurfaceCache("t", 10 ** 6)
        c.put("k", make_surf(10, 10))
        c.put("k", make_surf(20, 20))
        self.assertEqual(len(c), 1)
        self.assertEqual(c.total_bytes(), surface_bytes(make_surf(20, 20)))


class TestRotationCacheBudget(unittest.TestCase):
    """台风旋转/镜像/着色缓存: 有界且对渲染透明。"""

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.ty = Typhoon("WP", "01")
        # 非对称图案: 纯圆形翻转后逐字节相同, 无法验证 mirror 是否进 key
        self.img = make_surf(120, 90)
        pygame.draw.circle(self.img, (250, 60, 60, 255), (60, 45), 40)
        pygame.draw.rect(self.img, (60, 250, 60, 255), pygame.Rect(88, 10, 24, 14))

    def test_cache_is_bounded(self):
        cache = self.ty.v._img_cache
        for a in range(0, 360, 3):                 # 120 个角度桶
            self.ty._get_rotated("ring", self.img, float(a), False, None)
        self.assertLessEqual(cache.total_bytes(), cache.max_bytes)
        self.assertLessEqual(len(cache), 720)
        self.assertGreater(cache.hits + cache.misses, 0)

    def test_cache_is_transparent(self):
        s1 = self.ty._get_rotated("ring", self.img, 30.0, False, None)
        expect = pygame.transform.rotate(self.img, 30.0)
        self.assertEqual(pygame.image.tobytes(s1, "RGBA"),
                         pygame.image.tobytes(expect, "RGBA"))
        # 再取一次(命中缓存)必须逐字节一致
        s2 = self.ty._get_rotated("ring", self.img, 33.0, False, None)
        s3 = self.ty._get_rotated("ring", self.img, 33.0, False, None)
        self.assertIs(s2, s3)

    def test_mirror_and_tint_are_part_of_key(self):
        a = self.ty._get_rotated("ring", self.img, 0.0, False, None)
        b = self.ty._get_rotated("ring", self.img, 0.0, True, None)
        c = self.ty._get_rotated("ring", self.img, 0.0, False, (30, 0, 0))
        self.assertNotEqual(pygame.image.tobytes(a, "RGBA"),
                            pygame.image.tobytes(b, "RGBA"))
        self.assertNotEqual(pygame.image.tobytes(a, "RGBA"),
                            pygame.image.tobytes(c, "RGBA"))


class TestPurpleCacheBudget(unittest.TestCase):
    """C5 紫滤镜/TS 渐变结果缓存: 字节预算生效。"""

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        from app.ty_sim_mixins._draw_icon_mixin import TySimDrawIconMixin
        self.cls = TySimDrawIconMixin
        self.cache = TySimDrawIconMixin._purple_frame_cache

    def tearDown(self):
        self.cache.clear()

    def test_purple_cache_bounded_by_items_and_bytes(self):
        self.cache.clear()
        big = make_surf(300, 300)                  # 360KB
        for i in range(400):
            self.cls._cache_purple_frame(("k", i), big)
        self.assertLessEqual(len(self.cache), 240)
        self.assertLessEqual(self.cache.total_bytes(),
                             self.cache.max_bytes)
        self.assertGreater(self.cache.stats()["evictions"], 0)


if __name__ == "__main__":
    unittest.main()
