# py/control_panel.py
"""控制面板按钮布局系统。"""
from __future__ import annotations

import pygame
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Callable

from .constants import (
    BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED,
    CONTROL_PANEL_BG, CONTROL_PANEL_LINE,
    SPEED_BAR_BG, SPEED_BAR_FILL,
)
from .utils import lighten_color, darken_color


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

    def _x(self, n: int, w: int = None) -> int:
        """第 n 个按钮的 x 坐标（n 从 0 开始，间距 90）。"""
        bw = w if w is not None else self.BUTTON_W
        gap = 10 if bw == self.BUTTON_W else 5
        return 15 + n * (bw + gap)

    def _by(self) -> int:
        return self.sim.map_height + 15

    def _sby(self) -> int:
        from .constants import CONTROL_PANEL_ROW2_Y_OFFSET
        return self.sim.map_height + CONTROL_PANEL_ROW2_Y_OFFSET

    @property
    def _speed_bar_x(self) -> int:
        return self._x(4) + self.BUTTON_W + 15

    def build(self) -> None:
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
        ]
        undo_enabled = (md == "edit" and self.sim.edit_typhoon)
        row2_btns.append(
            PanelButton("undo", self._x(4, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.undo_text,
                        BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                        disabled=not undo_enabled))
        row2_btns.append(
            PanelButton("redo", self._x(5, self.SMALL_BUTTON_W), sby,
                        self.SMALL_BUTTON_W, self.BUTTON_H,
                        self.sim.redo_text,
                        BUTTON_BORDER if undo_enabled else BUTTON_DISABLED,
                        disabled=not undo_enabled))

        script_x = self.sim.screen_width - self.SMALL_BUTTON_W - 15
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
        cp_h = self.sim.control_panel_height
        py = self.sim.map_height

        pygame.draw.rect(surface, CONTROL_PANEL_BG,
                         (0, py, self.sim.screen_width, cp_h))
        pygame.draw.line(surface, CONTROL_PANEL_LINE,
                         (0, py), (self.sim.screen_width, py), 2)

        for btn in self._buttons:
            self._draw_button(surface, btn)

        # 速度条
        from .constants import f_s, rt, TXT
        by = self._by()
        sbx = self._speed_bar_x
        speed_text = rt(f_s, f"\u901f\u5ea6: {self.sim.sp:.1f}x", TXT)
        surface.blit(speed_text, (sbx, by - 3))
        speed_bar_rect = pygame.Rect(sbx, by + 15, self.SPEED_BAR_W, self.SPEED_BAR_H)
        pygame.draw.rect(surface, SPEED_BAR_BG, speed_bar_rect, 0, 6)
        sr = (self.sim.sp - self.sim.mis) / (self.sim.mas - self.sim.mis)
        fw = int(self.SPEED_BAR_W * sr)
        pygame.draw.rect(surface, SPEED_BAR_FILL,
                         (sbx, by + 15, fw, self.SPEED_BAR_H), 0, 6)

        # 模式描述
        mode_texts = {"normal": self.sim.mode_desc_normal,
                      "season": self.sim.mode_desc_season,
                      "edit": self.sim.mode_desc_edit}
        mode_desc = mode_texts.get(self.sim.md)
        if mode_desc:
            surface.blit(mode_desc, (
                self.sim.screen_width // 2 - mode_desc.get_width() // 2,
                self.sim.screen_height - 30))

        # 脚本运行指示
        if (hasattr(self.sim, 'script_engine') and self.sim.script_engine
                and self.sim.script_engine.running):
            from .constants import f_s as _f_s, rt as _rt
            running_surf = _rt(_f_s, "\u00b7\u811a\u672c\u8fd0\u884c\u4e2d", (220, 50, 50))
            script_btn = self._btn_map.get("script")
            if script_btn:
                surface.blit(running_surf, (
                    script_btn.x - running_surf.get_width() - 10,
                    script_btn.y + 4))

    @staticmethod
    def _draw_button(surface: pygame.Surface, btn: PanelButton) -> None:
        if not btn.visible or btn.text_surf is None:
            return
        r = btn.rect
        mx, my = pygame.mouse.get_pos()
        hover = r.collidepoint(mx, my)
        pressed = hover and pygame.mouse.get_pressed()[0]

        if btn.disabled:
            final_color = (150, 150, 150)
        elif pressed:
            final_color = darken_color(btn.color, 0.8)
        elif hover:
            final_color = lighten_color(btn.color, 1.2)
        else:
            final_color = btn.color

        pygame.draw.rect(surface, final_color, r, 0, 5)
        surface.blit(btn.text_surf, (
            r.centerx - btn.text_surf.get_width() // 2,
            r.centery - btn.text_surf.get_height() // 2,
        ))
