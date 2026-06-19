# py/ty_sim_mixins/draw_control_panel_mixin.py
"""控制面板绘制 Mixin。"""
import pygame
from ..constants import (
    f_s, rt, TXT, BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED,
    CONTROL_PANEL_ROW2_Y_OFFSET,
    CONTROL_PANEL_BG, CONTROL_PANEL_LINE,
    SPEED_BAR_BG, SPEED_BAR_FILL,
)
from ..utils import lighten_color, darken_color


class TySimDrawControlPanelMixin:
    """底部控制按钮、速度条、模式切换。"""

    BUTTON_W = 80
    BUTTON_H = 25
    SPEED_BAR_W = 112
    SPEED_BAR_H = 12

    def draw_button(self, surface: pygame.Surface, rect: pygame.Rect,
                    text_surf: pygame.Surface, color=BUTTON_BORDER,
                    disabled: bool = False):
        mx, my = pygame.mouse.get_pos()
        hover = rect.collidepoint(mx, my)
        pressed = hover and pygame.mouse.get_pressed()[0]

        if disabled:
            final_color = (150, 150, 150)
        elif pressed:
            final_color = darken_color(color, 0.8)
        elif hover:
            final_color = lighten_color(color, 1.2)
        else:
            final_color = color

        pygame.draw.rect(surface, final_color, rect, 0, 5)
        surface.blit(text_surf, (
            rect.centerx - text_surf.get_width() // 2,
            rect.centery - text_surf.get_height() // 2,
        ))

    def draw_control_panel(self, surface: pygame.Surface) -> None:
        py = self.map_height
        by = py + 15
        sby = py + CONTROL_PANEL_ROW2_Y_OFFSET
        pygame.draw.rect(surface, CONTROL_PANEL_BG, (0, py, self.screen_width, self.control_panel_height))
        pygame.draw.line(surface, CONTROL_PANEL_LINE, (0, py), (self.screen_width, py), 2)

        pc = (50, 150, 50) if not self.pl else (200, 100, 50)
        play_btn = pygame.Rect(15, by, self.BUTTON_W, self.BUTTON_H)
        self.draw_button(surface, play_btn, self.play_text, pc)

        reset_btn = pygame.Rect(105, by, self.BUTTON_W, self.BUTTON_H)
        self.draw_button(surface, reset_btn, self.reset_text, BUTTON_BORDER)

        if self.md == self.MODE_NORMAL:
            prev_btn = pygame.Rect(195, by, self.BUTTON_W, self.BUTTON_H)
            self.draw_button(surface, prev_btn, self.prev_text, (100, 100, 180))
            next_btn = pygame.Rect(285, by, self.BUTTON_W, self.BUTTON_H)
            self.draw_button(surface, next_btn, self.next_text, (100, 100, 180))
        else:
            new_btn = pygame.Rect(195, by, self.BUTTON_W, self.BUTTON_H)
            self.draw_button(surface, new_btn, self.new_text, (180, 100, 200))
            point_btn = pygame.Rect(285, by, self.BUTTON_W, self.BUTTON_H)
            self.draw_button(surface, point_btn, self.point_list_text, (150, 100, 200))

        mc = (180, 100, 200) if self.md == self.MODE_NORMAL else (200, 150, 50) if self.md == self.MODE_SEASON else (150, 100, 150)
        mode_btn = pygame.Rect(375, by, self.BUTTON_W, self.BUTTON_H)
        if self.md == self.MODE_NORMAL:
            mt = self.normal_mode_text
        elif self.md == self.MODE_SEASON:
            mt = self.season_mode_text
        else:
            mt = self.edit_mode_text
        self.draw_button(surface, mode_btn, mt, mc)

        speed_text = rt(f_s, f"速度: {self.sp:.1f}x", TXT)
        surface.blit(speed_text, (465, by - 3))
        speed_bar_rect = pygame.Rect(465, by + 15, self.SPEED_BAR_W, self.SPEED_BAR_H)
        pygame.draw.rect(surface, SPEED_BAR_BG, speed_bar_rect, 0, 6)
        sr = (self.sp - self.mis) / (self.mas - self.mis)
        fw = int(self.SPEED_BAR_W * sr)
        pygame.draw.rect(surface, SPEED_BAR_FILL, (465, by + 15, fw, self.SPEED_BAR_H), 0, 6)

        bw = 70
        bsp = 5
        sbx = 15

        ty_list_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, ty_list_btn, self.ty_list_text, (180, 150, 100))
        sbx += bw + bsp

        settings_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, settings_btn, self.settings_text, (120, 120, 180))
        sbx += bw + bsp

        tjc = BUTTON_BG if self.md == self.MODE_SEASON else BUTTON_DISABLED
        time_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, time_btn, self.time_jump_text, tjc,
                         disabled=(self.md != self.MODE_SEASON))
        sbx += bw + bsp

        ace_color = BUTTON_BG if self.md == self.MODE_SEASON else BUTTON_DISABLED
        ace_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, ace_btn, self.ace_chart_text, ace_color,
                         disabled=(self.md != self.MODE_SEASON))
        sbx += bw + bsp

        undo_enabled = (self.md == self.MODE_EDIT and self.edit_typhoon)
        undo_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, undo_btn, self.undo_text,
                         BUTTON_BG if undo_enabled else BUTTON_DISABLED,
                         disabled=not undo_enabled)
        sbx += bw + bsp

        redo_enabled = (self.md == self.MODE_EDIT and self.edit_typhoon)
        redo_btn = pygame.Rect(sbx, sby, bw, self.BUTTON_H)
        self.draw_button(surface, redo_btn, self.redo_text,
                         BUTTON_BG if redo_enabled else BUTTON_DISABLED,
                         disabled=not redo_enabled)

        if self.md == self.MODE_NORMAL:
            mode_desc = self.mode_desc_normal
        elif self.md == self.MODE_SEASON:
            mode_desc = self.mode_desc_season
        else:
            mode_desc = self.mode_desc_edit
        surface.blit(mode_desc, (self.screen_width // 2 - mode_desc.get_width() // 2, self.screen_height - 30))