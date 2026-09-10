# tests/test_video_style_i18n.py
"""模拟器科研图的中文渲染与字体定位。

回归背景: 字体从 font/ 迁到 assets/font/ 之后, video_style._font 仍指向旧目录,
找不到文件就静默退化成 FreeType 默认西文字体 —— 中文标题全变成方块。
这个文件同时锁住"字体能找到 CJK 字形"和"图表标题是中文"两条。
"""
import inspect
import os
import re
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import numpy as np
import pygame

from simulator.render import video_style as VS

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class TestFigureFont(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))

    def test_assets_font_dir_first(self):
        self.assertTrue(any(os.path.isdir(d) for d in VS._FONT_DIRS))
        self.assertEqual(os.path.basename(VS._FONT_DIRS[0]), "font")
        self.assertEqual(os.path.basename(os.path.dirname(VS._FONT_DIRS[0])), "assets")

    def test_cjk_glyph_available(self):
        metrics = VS._font(24).get_metrics("台")
        self.assertTrue(metrics and metrics[0] is not None,
                        "FreeType 字体缺少中文字形 → 图表中文标题会渲染成方块")

    def test_chinese_title_renders_ink(self):
        surf = VS.new_canvas(640, 120)
        VS.draw_title(surf, "台风强度 | 中心气压 (hPa) 与最大风速 (kt)")
        arr = pygame.surfarray.array3d(surf)
        ink = int((arr.sum(axis=2) < 3 * 250).sum())
        self.assertGreater(ink, 200)

    def test_draw_title_literals_are_chinese(self):
        """图表标题必须是中文(单位/缩写可以保留英文)。"""
        src = inspect.getsource(VS)
        titles = re.findall(r'draw_title\([^,]+,\s*f?"([^"]+)"', src)
        self.assertTrue(titles, "没有找到 draw_title 标题字面量")
        for t in titles:
            # 纯动态标题(名次/日期插值)不要求中文; 其余固定文案必须是中文
            plain = re.sub(r"\{[^}]*\}", "", t).strip()
            # 只看含英文字母的固定文案(纯插值/纯标点的动态标题跳过)
            if re.search(r"[A-Za-z]", plain):
                self.assertTrue(CJK_RE.search(t),
                                f"图表标题缺少中文: {t!r}")


if __name__ == "__main__":
    unittest.main()
