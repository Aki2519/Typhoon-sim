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
    SMALL_BUTTON_W = 70
    SPEED_BAR_W = 112
    SPEED_BAR_H = 12

    def __init__(self, sim):
        self.sim = sim
        self._buttons: List[PanelButton] = []
        self._btn_map: dict = {}       # key → PanelButton
        self._row2_btns: List[PanelButton] = []  # buttons in second row, left to right
        self._build_key: tuple = ()
        self._sp_text_cache: dict = {}
        self._md_text_cache: dict = {}
        self._run_text_cache: dict = {}
        self._mode_texts = {"normal": "模式: 正常",
                            "season": "模式: 台风季",
                            "edit": "模式: 编辑"}

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
        key = (sim.md, sim.pl, id(sim.play_text),
               bool(sim.md == "edit" and sim.edit_typhoon),
               sim.map_height, sim.screen_width)
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
        else:
            self._add(PanelButton("new_typhoon", self._x(2), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.new_text, (180, 100, 200)))
            self._add(PanelButton("point_list", self._x(3), by, self.BUTTON_W, self.BUTTON_H,
                                   self.sim.point_list_text, (150, 100, 200)))

        mode_color = (
            (180, 100, 200) if md == "normal"
            else (200, 150, 50) if md == "season"
            else (150, 100, 150))
        mode_texts = {"normal": self.sim.normal_mode_text,
                      "season": self.sim.season_mode_text,
                      "edit": self.sim.edit_mode_text}
        self._add(PanelButton("mode", self._x(4), by, self.BUTTON_W, self.BUTTON_H,
                               mode_texts.get(md), mode_color))

        # ── Row 2 ──
        row2_btns = [
            PanelButton("ty_list", self._x(0, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.ty_list_text, (180, 150, 100)),
            PanelButton("settings", self._x(1, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.settings_text, (120, 120, 180)),
            PanelButton("time_jump", self._x(2, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.time_jump_text,
                        BUTTON_BORDER if md == "season" else BUTTON_DISABLED,
                        disabled=(md != "season")),
            PanelButton("ace_chart", self._x(3, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.ace_chart_text,
                        BUTTON_BORDER if md == "season" else BUTTON_DISABLED,
                        disabled=(md != "season")),
            PanelButton("track", self._x(4, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.track_text,
                        BUTTON_BORDER if md == "season" else BUTTON_DISABLED,
                        disabled=(md != "season")),
        ]
        undo_enabled = (md == "edit" and self.sim.edit_typhoon)
        row2_btns.append(
            PanelButton("undo", self._x(5, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.undo_text,
                        BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                        disabled=not undo_enabled))
        row2_btns.append(
            PanelButton("redo", self._x(6, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.redo_text,
                        BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                        disabled=not undo_enabled))

        # script 按钮: 与左侧 row2 按钮保持最小间距,避免窄窗重叠
        script_x = self.sim.screen_width - self.SMALL_BUTTON_W - 15
        if row2_btns:
            left_edge = row2_btns[-1].x + row2_btns[-1].w + 10
            script_x = max(script_x, left_edge)
        row2_btns.append(
            PanelButton("script", script_x, sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.script_text, BUTTON_BORDER))

        for b in row2_btns:
            self._add(b)
            self._row2_btns.append(b)

    def _add(self, btn: PanelButton) -> None:
        self._buttons.append(btn)
        self._btn_map[btn.key] = btn

    # ── 点击检测 ──

    def hit_test(self, pos: Tuple[int, int]) -> Optional[str]:
        """返回被点击按钮的 key，或 None。"""
        x, y = pos
        for btn in self._buttons:
            if not btn.visible or btn.disabled:
                continue
            r = btn.rect
            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                return btn.key
        return None

    def hit_test_speed_bar(self, pos: Tuple[int, int]) -> Optional[float]:
        """如果在速度条上点击，返回表示比例 (0.0-1.0) 或 None。"""
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
        for btn in self._buttons:
            self._draw_button(surface, btn, dark, mouse_pos, mouse_down)

        # 速度条(文字按 sp 量化缓存,避免每帧 rt)
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
        mode_label = self._mode_texts.get(sim.md, "")
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
        surface.blit(btn.text_surf, (
            r.centerx - btn.text_surf.get_width() // 2,
            r.centery - btn.text_surf.get_height() // 2,
        ))
