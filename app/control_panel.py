# py/control_panel.py
"""控制面板按钮布局系统。"""
from __future__ import annotations

import pygame
from dataclasses import dataclass
from typing import List, Tuple, Optional

from .constants import (
    f_s, rt,
    BUTTON_BORDER, BUTTON_DISABLED,
    CONTROL_PANEL_BG, CONTROL_PANEL_LINE,
    SPEED_BAR_BG, SPEED_BAR_FILL,
    SETTINGS_DARK_BG, SETTINGS_TAB_BG, settings_accent,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_TOGGLE_ON, SETTINGS_TOGGLE_OFF,
)
from .utils import lighten_color, darken_color
from .constants import CONTROL_PANEL_ROW2_Y_OFFSET
from .ty_sim_mixins.sim_mode_mixin import SIM_LAYERS


@dataclass
class PanelButton:
    key: str
    x: int = 0
    y: int = 0
    w: int = 80
    h: int = 25
    text_surf: Optional[pygame.Surface] = None
    color: Tuple[int, int, int] = BUTTON_BORDER
    disabled: bool = False
    visible: bool = True

    @property
    def rect(self) -> pygame.Rect:
        # R4: 每次按当前 x/y/w/h 构建(resize 后不返回陈旧缓存矩形)
        return pygame.Rect(self.x, self.y, self.w, self.h)


class ControlPanel:
    BUTTON_W = 80
    BUTTON_H = 25
    SPEED_BAR_W = 112
    SPEED_BAR_H = 12

    def __init__(self, sim):
        self.sim = sim
        self._buttons: List[PanelButton] = []
        self._btn_map: dict = {}       # key → PanelButton
        self._row2_btns: List[PanelButton] = []  # buttons in second row, left to right
        #: 图层组的**弹层**按钮(模拟模式)。⚠ 与 `self._buttons` **分开存** ——
        #: 用户 2026-10-01: "我要录视频! 不要把按钮放在界面里!"
        #: ⇒ 这些按钮**默认完全不出现**, 只有点「图层」才展开, 用完就收。
        #: 既不加高功能栏, 也不占地图画面。
        self._layer_popup_btns: List[PanelButton] = []
        self._layer_popup_open: bool = False
        self._build_key: tuple = ()
        self._sp_text_cache: dict = {}
        self._sp_rate_cache: dict = {}
        self._md_text_cache: dict = {}
        self._run_text_cache: dict = {}
        self._mode_texts = {"normal": "模式: 正常",
                            "season": "模式: 风季",
                            "edit": "模式: 编辑",
                            "sim": "模式: 模拟"}

    def _x(self, n: int, w: int = None) -> int:
        """第 n 个按钮的 x 坐标（n 从 0 开始，间距 90）。"""
        box_w = w if w is not None else self.BUTTON_W
        gap = 10 if box_w == self.BUTTON_W else 5
        return 15 + n * (box_w + gap)

    def _by(self) -> int:
        return self.sim.map_height + 15

    def _sby(self) -> int:
        return self.sim.map_height + CONTROL_PANEL_ROW2_Y_OFFSET

    @property
    def _speed_bar_x(self) -> int:
        return self._x(4) + self.BUTTON_W + 15

    def build(self) -> None:
        # 状态未变化时跳过重建（每帧调用，但布局只依赖以下状态）
        sim = self.sim
        sim_state = ()
        if sim.md == "sim":
            # 第三行的图层开关/样式按钮的**外观**依赖这些状态, 必须进缓存键,
            # 否则点了图层按钮后按钮不变色(看着像没生效)
            sim_state = (getattr(sim, 'sim_layer', None),
                         tuple(sorted(getattr(sim, 'sim_layers', None) or ())),
                         getattr(sim, 'sim_natural_gen', None),
                         getattr(sim, 'sim_playing', None),
                         str(getattr(sim.cfg, 'sim_wind_mode', 'barbs')),
                         bool(getattr(sim.cfg, 'sim_shear_fill', True)),
                         round(getattr(sim, 'sp', 1.0), 1))
        key = (sim.md, sim.pl, id(sim.play_text),
               bool(sim.md == "edit" and sim.edit_typhoon),
               sim.map_height, sim.screen_width) + sim_state
        if key == self._build_key and self._buttons:
            return
        self._build_key = key

        self._buttons.clear()
        self._btn_map.clear()
        self._row2_btns.clear()

        by = self._by()
        sby = self._sby()
        md = self.sim.md

        # ── Row 1 ──
        play_color = (50, 150, 50) if not self.sim.pl else (200, 100, 50)
        self._add(PanelButton("play", self._x(0), by, self.BUTTON_W, self.BUTTON_H,
                               self.sim.play_text, play_color))

        self._add(PanelButton("reset", self._x(1), by, self.BUTTON_W, self.BUTTON_H,
                               self.sim.reset_text, BUTTON_BORDER))

        if md == "normal":
            self._add(PanelButton("prev", self._x(2), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.prev_text, (100, 100, 180)))
            self._add(PanelButton("next", self._x(3), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.next_text, (100, 100, 180)))
        elif md == "sim":
            # 模拟模式: Row1 放 1 个专属按钮(play/reset 之后), mode 与其它模式同为 _x(4)。
            # 「速度××」按钮 2026-09-19 按用户要求去掉 —— 速度只用快捷键与速度条调
            # (+/-、←/→、X; 见 keyboard_mixin)。
            sim = self.sim
            names = [
                ("sim_gen", "生成台风", (180, 120, 80)),
                # 把 ./sim_typhoon 里的实时 b-deck 记录搬进 typhoon/sim<年>/
                # (用户 2026-09-20: 实时记录不写 typhoon, 由这个按钮手动搬)
                ("sim_move_bdeck", "移入台风库", (120, 150, 110)),
            ]
            for i, (key, label, color) in enumerate(names):
                self._add(PanelButton(key, self._x(2 + i), by, self.BUTTON_W, self.BUTTON_H,
                                      rt(f_s, label, (240, 240, 245)), color))
        else:
            self._add(PanelButton("new_typhoon", self._x(2), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.new_text, (180, 100, 200)))
            self._add(PanelButton("point_list", self._x(3), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.point_list_text, (150, 100, 200)))

        mode_color = (
            (180, 100, 200) if md == "normal"
            else (200, 150, 50) if md == "season"
            else (150, 100, 150) if md == "edit"
            else (60, 180, 200) if md == "sim"
            else (60, 160, 120))  # ocean_edit
        mode_texts = {"normal": self.sim.normal_mode_text,
                      "season": self.sim.season_mode_text,
                      "edit": self.sim.edit_mode_text,
                      "sim": rt(f_s, "模拟", (240, 240, 245)),
                      "ocean_edit": rt(f_s, "洋区编辑", (240, 240, 245))}
        # 所有模式 mode 按钮位置一致(_x(4)), 速度条在其右侧
        self._add(PanelButton("mode", self._x(4),
                               by, self.BUTTON_W, self.BUTTON_H,
                               mode_texts.get(md), mode_color))

        # 窄窗保护: sim 模式 Row1 按钮超宽时隐藏超出屏幕的按钮
        if md == "sim":
            right_limit = self.sim.screen_width - 15
            for btn in self._buttons:
                if btn.rect.right > right_limit:
                    btn.visible = False

        # ── Row 2 ──
        row2_btns = [
            PanelButton("ty_list", self._x(0, self.BUTTON_W), sby,
                        self.BUTTON_W, self.BUTTON_H,
                        self.sim.ty_list_text, (180, 150, 100)),
            PanelButton("settings", self._x(1, self.BUTTON_W), sby,
                        self.BUTTON_W, self.BUTTON_H,
                        self.sim.settings_text, (120, 120, 180)),
        ]
        if md == "season":
            row2_btns.extend([
                PanelButton("time_jump", self._x(2, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.time_jump_text, BUTTON_BORDER),
                PanelButton("ace_chart", self._x(3, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.ace_chart_text, BUTTON_BORDER),
                PanelButton("track", self._x(4, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.track_text, BUTTON_BORDER),
            ])
        if md in ("normal", "edit"):
            undo_enabled = (md == "edit" and self.sim.edit_typhoon)
            row2_btns.append(
                PanelButton("undo", self._x(2, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.undo_text,
                            BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                            disabled=not undo_enabled))
            row2_btns.append(
                PanelButton("redo", self._x(3, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.redo_text,
                            BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                            disabled=not undo_enabled))
            if md == "normal":
                # 正常模式: 镜头跟踪按钮(与风季模式同位置; 仅按钮入口, 无快捷键)
                row2_btns.append(
                    PanelButton("track", self._x(4, self.BUTTON_W), sby,
                                self.BUTTON_W, self.BUTTON_H,
                                self.sim.track_text, BUTTON_BORDER))
        if md == "sim":
            # 模拟模式 Row2: 自然生成开关 + 图层切换 + 模拟设置
            # 轮次 158 追加: 单步(15min) / 骰子种子, 放列 5/6。
            # ⚠ 列 0/1 是"台风列表/设置"(全局), 2/3/4 已被上面三个占, **没有空位**;
            #   必须 `row2_btns.extend` 追加 —— 后面 script/paint 的右对齐布局会读
            #   `row2_btns[-1]`(`left_edge`)自动让位, 直接 `_add` 会让它们重叠。
            # ⚠ 标签刻意短: BUTTON_W 只有 80px, 长了会被裁(UI 规范第 1 条)。
            # 点击路由沿用既有 `_btn_<key>` 约定(keyboard_mixin.py:534) ⇒ 处理器由
            # SimStepControlMixin 提供。
            sim = self.sim
            row2_btns.extend([
                PanelButton("sim_nat", self._x(2, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "自然:" + ("开" if sim.sim_natural_gen else "关"),
                               (240, 240, 245)), (60, 180, 120)),
                PanelButton("sim_layer", self._x(3, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "图层:" + sim._sim_layer_short(), (240, 240, 245)),
                            (60, 160, 200)),
                PanelButton("sim_sett", self._x(4, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "模拟设置", (240, 240, 245)),
                            (120, 120, 200)),
                # 图层弹层入口(用户 2026-10-01): 那些图层/环境层/样式按钮**不能占界面**
                # (他要录视频), 又不能加高功能栏 ⇒ 收进**默认不可见**的弹层, 由这个按钮开。
                # ⚠ 与 `sim_layer`(循环独占层, 契约见 tests/test_sim_mode.py::test_layer_cycle)
                #   **职责分开**: 这个只负责开关弹层, 不改层。
                PanelButton("sim_layer_menu", self._x(5, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "图层设置", (240, 240, 245)),
                            (86, 128, 176)),
                PanelButton("sim_step", self._x(6, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "单步15m", (240, 240, 245)),
                            (110, 110, 160)),
                PanelButton("sim_dice", self._x(7, self.BUTTON_W), sby,
                            self.BUTTON_W, self.BUTTON_H,
                            rt(f_s, "骰子种子", (240, 240, 245)),
                            (110, 140, 120)),
            ])
            # 轮次 158: 记下"轮次158 这两个次要按钮"的起点。右侧 script/paint 的
            # 让位计算**只按核心按钮**算, 空间不足时先隐藏这两个 —— 否则在 800px
            # 一类的窄窗下它们会把「绘画」按钮挤掉(实测 tests/test_paint.py 抓到)。
            _row2_core_n = len(row2_btns) - 2

        # 功能栏最右侧: 绘画按钮(右缘), 其左侧为 script; 与左侧 row2 按钮保持最小间距
        paint_x = self.sim.screen_width - self.BUTTON_W - 15
        script_x = paint_x - self.BUTTON_W - 10
        # 轮次 158: 让位只按**核心**按钮算(排除 sim_step/sim_dice 这两个次要按钮),
        # 否则它们会把 script/paint 一路推到屏幕外, 于是「绘画」在窄窗下消失。
        _core_n = locals().get('_row2_core_n') or 0
        _edge_btns = row2_btns[:_core_n] if _core_n else row2_btns
        if _edge_btns:
            left_edge = _edge_btns[-1].x + _edge_btns[-1].w + 10
            script_x = max(script_x, left_edge)
        # 窄窗钳制: 右缘不超出屏幕; 若与左侧按钮重叠则隐藏
        max_x = max(15, self.sim.screen_width - self.BUTTON_W - 15)
        script_x = min(script_x, max_x)
        max_paint_x = max(15, self.sim.screen_width - self.BUTTON_W - 15)
        if script_x >= max_paint_x - self.BUTTON_W:
            # 空间不足: paint 移出(隐藏), script 回到最右
            script_x = max(15, self.sim.screen_width - self.BUTTON_W - 15)
        # 轮次 158: 把「单步/骰子」这两个**次要**按钮让掉 —— 它们若会压到 script
        # 就自己隐藏(键盘 `.` / `;` 仍然可用)。**必须在 script/paint 定稿之后判**,
        # 且**不能**参与上面的 left_edge 计算, 否则窄窗下会把「绘画」挤没。
        if _core_n and _core_n < len(row2_btns):
            for _b in row2_btns[_core_n:]:
                if _b.x + _b.w + 10 > script_x:
                    _b.visible = False
        # script 按钮
        if row2_btns:
            # ⚠ 判"重不重叠"只能跟**可见的**左侧按钮比。上面刚把 sim_step/sim_dice
            #   判成隐藏(`visible=False`), 它们就不该再挡住 script ——
            #   实测 800px: 那两个已隐藏, script 放在 615..695 明明不碰任何可见按钮
            #   (核心组止于 545), 却因为 `row2_btns[-1]`(sim_dice, 645..725)而被判
            #   "重叠"一起消失 ⇒ 与上面"重叠才隐藏"的注释矛盾, 且 1024px 下它又是
            #   可见的(说明本意就是"放得下就显示")。
            _vis = [b for b in row2_btns if b.visible]
            last_btn = _vis[-1] if _vis else None
            _hit_left = (last_btn is not None
                         and script_x < last_btn.x + last_btn.w + 10)
            # 空间真不够时 script_x 会被钳到 paint 的位置(见上面 265-271), 那种情况
            # 必须按 paint 判重 —— 原来的 `row2_btns[-1]` 判法只是**碰巧**盖住它。
            _hit_paint = script_x + self.BUTTON_W + 10 > paint_x
            if _hit_left or _hit_paint:
                script_btn = PanelButton("script", script_x, sby,
                                         self.BUTTON_W, self.BUTTON_H,
                                         self.sim.script_text, BUTTON_BORDER)
                script_btn.visible = False
                row2_btns.append(script_btn)
            else:
                row2_btns.append(
                    PanelButton("script", script_x, sby,
                                self.BUTTON_W, self.BUTTON_H,
                                self.sim.script_text, BUTTON_BORDER))
        else:
            row2_btns.append(
                PanelButton("script", script_x, sby,
                            self.BUTTON_W, self.BUTTON_H,
                            self.sim.script_text, BUTTON_BORDER))
        # 绘画按钮: 功能栏最右缘
        paint_btn = PanelButton("paint", paint_x, sby,
                                self.BUTTON_W, self.BUTTON_H,
                                self.sim.paint_text, (200, 120, 80))
        if paint_x < 15 or paint_x + self.BUTTON_W > self.sim.screen_width - 5:
            paint_btn.visible = False
        row2_btns.append(paint_btn)

        for b in row2_btns:
            self._add(b)
            self._row2_btns.append(b)

        # ── 图层组(仅模拟模式): 收进**默认不可见的弹层** ────────────────────
        # 史: 原来它是功能栏第三行 ⇒ 模拟模式被迫 +34px(用户 2026-10-01:
        #     "功能栏不能抬高高度, 你需要找别的方法放下那些按钮");
        # 我第一版搬到地图上 ⇒ 用户 2026-10-01: "我要录视频! 不要把按钮放在界面里!"
        # 最终: `_layer_popup_open=False` 时**屏幕上什么都没有**; 点 Row2 的「图层」
        #      (`_btn_sim_layer`) 才展开。功能栏高度不受影响。
        if md == "sim":
            self._layer_popup_btns = []
            if self._layer_popup_open:
                self._build_sim_layer_row(self.layer_popup_rect().top + 10)
        else:
            # ⚠ 非模拟模式必须**收起**弹层。原来没有这个 else ⇒ 在模拟模式打开弹层
            #   再切到正常/编辑模式时: `_layer_popup_open` 还是 True、13 个按钮还在
            #   列表里 ⇒ ① `draw()` 仍然把它画在地图上(录制/截图里多一块);
            #   ② `hit_test` 仍然优先命中它(`_in_layer_popup` 只看 open 标志)
            #   ⇒ 正常模式点地图会误触 `layer_*`。实测(1360px): 切模式 + build() 后
            #   `open=True` / `n_btns=13` / `hit_test(弹层按钮)= 'layer_pressure'`。
            self._layer_popup_open = False
            self._layer_popup_btns = []

    def _layer_row_y(self) -> int:
        """图层组的 y: **浮在地图区域内、贴着功能栏上沿**。

        这样功能栏保持两行(100px)不变高, 而按钮照常可见可点。
        留 6px 与功能栏的分隔线错开, 避免视觉上糊在一起。
        """
        return max(0, self.sim.map_height - self.BUTTON_H - 6)

    def _build_sim_layer_row(self, y: int) -> None:
        """模拟模式的**图层弹层**内容: 图层开关(可叠加) + 环境层三选一 + 两个样式按钮。

        ⚠ 用户 2026-10-01 两次纠偏:
          1. "功能栏不能抬高高度, 你需要找别的方法放下那些按钮" —— 原来这组是功能栏
             第三行, 逼得模拟模式 +34px;
          2. "我要录视频! 不要把按钮放在界面里!" —— 搬到地图上**也不行**, 它们会一直
             占着画面。
        ⇒ 最终形态: **默认完全不可见**的弹层, 点 Row2 的「图层」才展开。
          按钮存进 `self._layer_popup_btns`(**不进 `self._buttons`**),
          `draw()`/`hit_test()` 只在 `_layer_popup_open` 时处理它们。
        """
        sim = self.sim
        self._layer_popup_btns = []
        pad = 12
        x = pad
        small = 62
        layers = set(getattr(sim, 'sim_layers', None) or set())
        for lid, nm, _key in SIM_LAYERS:
            if lid == 'track':
                continue                       # 路径层常开, 不给开关
            on = lid in layers
            # 选中态要**一眼看得出来**(用户 2026-09-19: "图层没有高亮显示"):
            # 亮蓝底 + 纯白字, 未选中压暗到近背景色
            self._layer_popup_btns.append(PanelButton(
                'layer_' + lid, x, y, small, self.BUTTON_H,
                rt(f_s, nm, (255, 255, 255) if on else (170, 178, 192)),
                (86, 152, 236) if on else (44, 50, 64)))
            x += small + 4
        # 环境层三选一框(海温/热含量/风切): 单独一组, 用蓝色系区分
        x += 10
        for lid, nm in (('sst', '海温'), ('ohc', '热含量'), ('shear', '风切')):
            on = lid in layers
            self._layer_popup_btns.append(PanelButton(
                'envlay_' + lid, x, y, small + 8, self.BUTTON_H,
                rt(f_s, nm, (255, 255, 255) if on else (166, 186, 210)),
                (64, 178, 220) if on else (32, 48, 66)))
            x += small + 12
        # 样式按钮: 风场样式 / 风切样式
        x += 10
        wmode = {'barbs': '风羽', 'stream': '流线',
                 'particles': '粒子'}.get(
                     str(getattr(sim.cfg, 'sim_wind_mode', 'barbs')), '风羽')
        self._layer_popup_btns.append(PanelButton(
            'wind_mode', x, y, small + 18, self.BUTTON_H,
            rt(f_s, '风场:' + wmode, (240, 240, 245)), (120, 110, 190)))
        x += small + 22
        sfill = bool(getattr(sim.cfg, 'sim_shear_fill', True))
        self._layer_popup_btns.append(PanelButton(
            'shear_style', x, y, small + 18, self.BUTTON_H,
            rt(f_s, '风切:' + ('色阶' if sfill else '等值线'),
               (240, 240, 245)), (120, 110, 190)))
        self._layer_popup_w = x + pad

    def layer_popup_rect(self) -> pygame.Rect:
        """图层弹层的矩形(锚在功能栏上沿之上, 左对齐)。

        **默认不显示** —— 只有 `toggle_layer_popup()` 打开后才绘制。
        """
        h = self.BUTTON_H + 20
        y = max(0, self.sim.map_height - h - 6)
        return pygame.Rect(0, y, int(getattr(self, '_layer_popup_w', 640)),
                           h + 6)

    def toggle_layer_popup(self) -> None:
        self._layer_popup_open = not self._layer_popup_open
        if self._layer_popup_open:
            # 打开时才按当前状态重建(选中态才准)
            r = self.layer_popup_rect()
            self._build_sim_layer_row(r.top + 10)

    def close_layer_popup(self) -> None:
        """收起图层弹层: **标志与按钮列表一起清**。

        ⚠ 只置 `_layer_popup_open=False` 会留下 13 个陈旧按钮(实测)。消费者
        (`_in_layer_popup`/`draw`)都带 open 守卫, 所以不是现行 bug; 但列表留着
        会让人误以为"关掉后还能读", 也白占引用 —— 顺手清干净。
        """
        self._layer_popup_open = False
        self._layer_popup_btns = []

    def _in_layer_popup(self, pos) -> Optional[str]:
        """若弹层打开且 pos 命中弹层按钮, 返回其 key。"""
        if not self._layer_popup_open:
            return None
        x, y = pos
        for btn in self._layer_popup_btns:
            r = btn.rect
            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                return btn.key
        return None

    def _add(self, btn: PanelButton) -> None:
        self._buttons.append(btn)
        self._btn_map[btn.key] = btn

    # ── 点击检测 ──

    def hit_test(self, pos: Tuple[int, int]) -> Optional[str]:
        """返回被点击按钮的 key，或 None。

        ⚠ 图层弹层**打开时优先** —— 它画在功能栏之上, 命中判定也必须先于栏内按钮,
        否则点弹层里靠下的按钮会误触它底下的功能栏按钮。
        """
        k = self._in_layer_popup(pos)
        if k is not None:
            return k
        x, y = pos
        for btn in self._buttons:
            if not btn.visible or btn.disabled:
                continue
            r = btn.rect
            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                return btn.key
        return None

    def hit_test_speed_bar(self, pos: Tuple[int, int]) -> Optional[float]:
        """如果在速度条上点击，返回表示比例 (0.0-1.0) 或 None。所有模式同一速度条。"""
        x, y = pos
        sbx = self._speed_bar_x
        sbr = pygame.Rect(sbx, self._by() + 15, self.SPEED_BAR_W, self.SPEED_BAR_H)
        if sbr.collidepoint(x, y):
            ratio = (x - sbx) / self.SPEED_BAR_W
            return max(0.0, min(1.0, ratio))
        return None

    # ── 绘制 ──

    def draw(self, surface: pygame.Surface) -> None:
        sim = self.sim
        cp_h = sim.control_panel_height
        py = sim.map_height
        dark = getattr(sim, 'dark_mode', True)
        sw = sim.screen_width

        if getattr(sim, '_ui_hidden', False):
            if dark:
                pygame.draw.rect(surface, SETTINGS_DARK_BG[:3], (0, py, sw, cp_h))
            else:
                pygame.draw.rect(surface, CONTROL_PANEL_BG, (0, py, sw, cp_h))
            return

        if dark:
            pygame.draw.rect(surface, SETTINGS_DARK_BG[:3], (0, py, sw, cp_h))
            pygame.draw.line(surface, SETTINGS_TAB_BG,
                             (0, py), (sw, py), 2)
        else:
            pygame.draw.rect(surface, CONTROL_PANEL_BG,
                             (0, py, sw, cp_h))
            pygame.draw.line(surface, CONTROL_PANEL_LINE,
                             (0, py), (sw, py), 2)

        mouse_pos = pygame.mouse.get_pos()
        mouse_down = pygame.mouse.get_pressed()[0]
        # 图层弹层: **只有打开时才画** —— 默认屏幕上什么都没有, 便于录视频。
        # 画在功能栏按钮**之前**, 但它在功能栏上方, 不重叠。
        if self._layer_popup_open and self._layer_popup_btns:
            r = self.layer_popup_rect()
            panel_bg = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
            panel_bg.fill((14, 18, 28, 242) if dark else (236, 239, 245, 246))
            surface.blit(panel_bg, r.topleft)
            pygame.draw.rect(surface, (96, 132, 176) if dark else (150, 160, 180),
                             r, 1, 8)
            for btn in self._layer_popup_btns:
                self._draw_button(surface, btn, dark, mouse_pos, mouse_down)
        for btn in self._buttons:
            self._draw_button(surface, btn, dark, mouse_pos, mouse_down)

        # 速度条(所有模式统一, 文字按 sp 量化缓存,避免每帧 rt)
        by = self._by()
        sbx = self._speed_bar_x
        text_color = SETTINGS_TEXT_LIGHT if dark else (20, 40, 80)
        sp_key = (round(sim.sp, 1), dark)
        speed_text = self._sp_text_cache.get(sp_key)
        if speed_text is None:
            speed_text = rt(f_s, f"\u901f\u5ea6: {sim.sp:.1f}x", text_color)
            if len(self._sp_text_cache) > 32:
                self._sp_text_cache.pop(next(iter(self._sp_text_cache)))
            self._sp_text_cache[sp_key] = speed_text
        surface.blit(speed_text, (sbx, by - 3))
        # 有效速度(红字, 速度条上方): 请求的倍速机器吃不下时如实显示,
        # 例 "8× → 有效 0.58×"。用户 2026-09-19: 就去掉速度按钮, 用这个红字 +
        # 快捷键/速度条承担调速反馈。
        try:
            _eff = sim._sim_effective_x() if hasattr(sim, '_sim_effective_x') else 0.0
            if _eff > 0.0 and abs(_eff - sim.sp) > max(0.05, 0.08 * sim.sp):
                _txt = '%g\u00d7 \u2192 \u6709\u6548 %.2f\u00d7' % (sim.sp, _eff)
                rk = (round(sim.sp, 2), round(_eff, 2), dark)
                rate_surf = self._sp_rate_cache.get(rk)
                if rate_surf is None:
                    rate_surf = rt(f_s, _txt, (255, 60, 60))
                    if len(self._sp_rate_cache) > 32:
                        self._sp_rate_cache.pop(next(iter(self._sp_rate_cache)))
                    self._sp_rate_cache[rk] = rate_surf
                surface.blit(rate_surf,
                             (sbx + speed_text.get_width() + 10, by - 3))
        except Exception:
            pass
        speed_bar_rect = pygame.Rect(sbx, by + 15, self.SPEED_BAR_W, self.SPEED_BAR_H)
        sb_bg = SETTINGS_TOGGLE_OFF if dark else SPEED_BAR_BG
        sb_fill = settings_accent(dark, sim.color_scheme) if dark else SPEED_BAR_FILL
        pygame.draw.rect(surface, sb_bg, speed_bar_rect, 0, 6)
        denom = sim.mas - sim.mis
        if denom <= 0:
            sr = 0.0
        else:
            sr = max(0.0, min(1.0, (sim.sp - sim.mis) / denom))
        fw = int(self.SPEED_BAR_W * sr)
        pygame.draw.rect(surface, sb_fill,
                         (sbx, by + 15, fw, self.SPEED_BAR_H), 0, 6)

        # 模式描述(按模式缓存)
        # 模拟模式面板是三行(第三行铺满宽度), 屏幕底部中央已被按钮占据 ——
        # 这里不再画"模式: 模拟", 否则文字被按钮压住(2026-09-19 离屏渲染发现)
        mode_label = "" if sim.md == "sim" else self._mode_texts.get(sim.md, "")
        if mode_label:
            text_color = SETTINGS_TEXT_LIGHT if dark else (20, 40, 80)
            md_key = (sim.md, dark)
            md_surf = self._md_text_cache.get(md_key)
            if md_surf is None:
                md_surf = rt(f_s, mode_label, text_color)
                self._md_text_cache[md_key] = md_surf
            surface.blit(md_surf, (
                sw // 2 - md_surf.get_width() // 2,
                sim.screen_height - 30))

        # 脚本运行指示(状态翻转时缓存)
        running = bool(hasattr(sim, 'script_engine') and sim.script_engine
                       and sim.script_engine.running)
        if running:
            run_key = (running, dark)
            running_surf = self._run_text_cache.get(run_key)
            if running_surf is None:
                running_surf = rt(f_s, "\u00b7\u811a\u672c\u8fd0\u884c\u4e2d", (220, 50, 50))
                self._run_text_cache[run_key] = running_surf
            script_btn = self._btn_map.get("script")
            if script_btn:
                surface.blit(running_surf, (
                    script_btn.x - running_surf.get_width() - 10,
                    script_btn.y + 4))

    @staticmethod
    def _draw_button(surface: pygame.Surface, btn: PanelButton, dark: bool = False,
                     mouse_pos: Tuple[int, int] = (-1, -1), mouse_down: bool = False) -> None:
        if not btn.visible or btn.text_surf is None:
            return
        r = btn.rect
        hover = r.collidepoint(mouse_pos)
        pressed = hover and mouse_down

        if btn.disabled:
            final_color = (100, 100, 110) if dark else (150, 150, 150)
        elif pressed:
            final_color = darken_color(btn.color, 0.8)
        elif hover:
            final_color = SETTINGS_TOGGLE_ON if dark else lighten_color(btn.color, 1.2)
        else:
            final_color = SETTINGS_TOGGLE_OFF if dark else btn.color

        pygame.draw.rect(surface, final_color, r, 0, 5)
        ts = btn.text_surf
        # 文字自适应: 超出按钮时等比缩小(中文长文本如"模拟设置")
        if ts.get_width() > r.w - 8:
            scale = max(0.5, (r.w - 8) / ts.get_width())
            ts = pygame.transform.smoothscale(
                ts, (max(1, int(ts.get_width() * scale)),
                     max(1, int(ts.get_height() * scale))))
        surface.blit(ts, (
            r.centerx - ts.get_width() // 2,
            r.centery - ts.get_height() // 2,
        ))
