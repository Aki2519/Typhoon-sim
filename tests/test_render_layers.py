# tests/test_render_layers.py
"""渲染图层表: z 序与可见性(renderer.scene_layers)。

重构点: 原先 z 序靠 _draw_scene 里 if/elif 的书写顺序隐式维持, 加一层要在多处
插入; 现在顺序是一份数据。这个文件把"顺序 + 每种模式实际画了哪些层"钉死,
以后调整绘制顺序/可见性会直接失败并给出差异。
"""
import os
import types
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame

from app.renderer import Renderer

EXPECTED_ORDER = (
    # 轮次 156: `graticule` 由 `sim_field` **之后**移到**之前**。
    # 原因: 模拟模式的全部内容(环境色阶层/路径/图标/风圈)都由 `_sim_render`
    # 一次画完; 经纬网排在它后面会把网格线压在台风图标与路径之上。
    # 风季模式没有 sim_field 层, 顺序不受影响。
    "map", "graticule", "sim_field", "ocean_areas", "ocean_edit", "clock",
    "ace_bar", "typhoons", "track_label", "season_info_boxes",
    "typhoon_info", "effects",
)


class _Ms:
    def draw(self, surface):
        pass


class LayerSim:
    """最小 sim 桩: 每个图层入口记录调用名。"""

    MODE_NORMAL = "normal"
    MODE_SEASON = "season"
    MODE_EDIT = "edit"
    MODE_SIM = "sim"

    def __init__(self, md="normal"):
        self.md = md
        self.calls = []
        self.cfg = types.SimpleNamespace(
            show_graticule=False, show_ocean_areas=False,
            show_track_label=True, show_ace_bar=True)
        self.screen_width = 800
        self.map_height = 600
        self.effects = []
        self.ocean_edit = None
        self.show_ace_bar = True
        self.show_info_box_season = True
        self.dialog_mgr = types.SimpleNamespace(any_active=lambda: False)
        self._ms = _Ms()
        self.view = types.SimpleNamespace(latlon_to_screen=lambda la, lo: (0, 0))
        self.res_mgr = None
        self.tys = []
        self.edit_typhoon = None

    def _draw_map(self, surface):
        self.calls.append("map")

    def _sim_render(self, surface):
        self.calls.append("sim_field")

    def _draw_typhoons(self, surface):
        self.calls.append("typhoons")

    def draw_season_clock(self, surface, origin=None, time_tuple=None):
        # time_tuple: 正常/编辑模式现在显式传当前台风时刻(2026-09-19 修"时间轴
        # 坏了" —— 不传会落到风季控制器的陈旧字段)
        self.calls.append("clock")
        self.last_clock_tuple = time_tuple

    def draw_ace_display(self, surface):
        self.calls.append("ace_bar")

    def draw_season_info_boxes(self, surface):
        self.calls.append("season_info_boxes")

    def draw_typhoon_info(self, surface, ty):
        self.calls.append("typhoon_info")

    @staticmethod
    def tracking_label():
        return "镜头跟踪: 测试"

    @staticmethod
    def current_typhoon():
        return None


class _RecordingRenderer(Renderer):
    """记录每帧实际绘制的图层名与耗时(借助 _layer_drawn 钩子)。"""

    def __init__(self, sim):
        super().__init__(sim)
        self.drawn = []
        self.layer_ms = {}

    def _layer_drawn(self, name, elapsed_ms=0.0):
        self.drawn.append(name)
        self.layer_ms[name] = elapsed_ms


class TestLayerTable(unittest.TestCase):
    def setUp(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        self.surf = pygame.Surface((800, 600))

    def _render(self, sim, hidden=False):
        r = _RecordingRenderer(sim)
        r._draw_scene(self.surf, hidden)
        return r.drawn

    def test_z_order_is_documented(self):
        names = tuple(n for n, _v, _d in Renderer(LayerSim()).scene_layers(False))
        self.assertEqual(names, EXPECTED_ORDER)
        names_hidden = tuple(n for n, _v, _d in Renderer(LayerSim()).scene_layers(True))
        self.assertEqual(names_hidden, EXPECTED_ORDER)

    def test_normal_mode_default_flags(self):
        # typhoon_info / effects 层"可见"(被调用)但当前无台风/无特效 → 内部空转
        self.assertEqual(self._render(LayerSim("normal")),
                         ["map", "clock", "ace_bar", "typhoons", "track_label",
                          "typhoon_info", "effects"])

    def test_hidden_ui_keeps_only_base_layers(self):
        self.assertEqual(self._render(LayerSim("normal"), hidden=True),
                         ["map", "typhoons"])

    def test_sim_mode_only_map_and_field(self):
        # 2026-09-19: 模拟模式也用同一套 ACE 条(用户"ace 条像其它模式一样显示"),
        # 所以这里多了 'ace_bar'
        self.assertEqual(self._render(LayerSim("sim")),
                         ["map", "sim_field", "ace_bar"])

    def test_sim_mode_hidden_skips_field(self):
        self.assertEqual(self._render(LayerSim("sim"), hidden=True), ["map"])

    def test_season_info_boxes_after_paths(self):
        calls = self._render(LayerSim("season"))
        self.assertIn("season_info_boxes", calls)
        self.assertLess(calls.index("typhoons"), calls.index("season_info_boxes"))

    def test_optional_layers_follow_flags(self):
        sim = LayerSim("normal")
        sim.cfg.show_graticule = True
        self.assertIn("graticule", self._render(sim))

        sim2 = LayerSim("normal")
        sim2.show_ace_bar = False
        self.assertNotIn("ace_bar", self._render(sim2))

        sim3 = LayerSim("normal")
        sim3.cfg.show_track_label = False
        self.assertNotIn("track_label", self._render(sim3))

        sim4 = LayerSim("season")
        sim4.show_info_box_season = False
        self.assertNotIn("season_info_boxes", self._render(sim4))

    def test_no_fullscreen_snap_grid_layer(self):
        """2026-09-19 回退: 整屏 0.1° 吸附网格层被删掉了。

        用户报"编辑模式的格点出问题了" —— 之前这一版把吸附网格画成**整屏**细线
        网, 在 0.1° 步长下几乎糊满地图。旧版本是在**被拖动的那个点周围**画一小块
        半径 130px、边缘径向渐隐的网格(_draw_path_mixin._draw_snap_grid), 现在
        回到那个做法, 所以渲染层表里不再有 snap_grid。
        """
        for md in ("normal", "season", "edit", "sim"):
            self.assertNotIn("snap_grid", self._render(LayerSim(md)))

    def test_ocean_edit_layer_visibility(self):
        sim = LayerSim("normal")
        sim.ocean_edit = types.SimpleNamespace(active=True, draw=lambda s: None)
        self.assertEqual(self._render(sim)[:3], ["map", "ocean_areas", "ocean_edit"])


if __name__ == "__main__":
    unittest.main()
