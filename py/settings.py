# py/settings.py
"""设置对话框（三页，经纬度方向后缀，ACE限制模式，洋区下拉框，洋区半球自动切换）。"""
from __future__ import annotations

import re
import pygame
from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED, BUTTON_BG,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH, LIST_BG, LIST_HL,
    SETTINGS_WIDTH, SETTINGS_HEIGHT,
    DIALOG_TITLE_BAR_HEIGHT
)
from .input_field import InputField
from .dialog_base import DraggableDialog
from .utils import lon_to_display, lat_to_display
from typing import List, Optional, Tuple

ACE_LIMIT_NONE = "none"
ACE_LIMIT_LATLON = "latlon"
ACE_LIMIT_BASIN = "basin"


class Settings(DraggableDialog):
    def __init__(self, s):
        super().__init__(s)
        self._init_data()
        self.current_page = 0
        self.fields: List[InputField] = []
        self.show_shortcuts = False
        self._needs_save = False
        self._field_offsets: List[Tuple[int, int, int, int]] = []
        self._pre_render_texts()
        self._ace_changed = False
        self._basin_dropdown_open = False
        self._basin_scroll_offset = 0
        self._basin_list: List[Tuple[str, str]] = []
        # 快捷键面板拖拽状态（与 DraggableDialog 保持相同的拖拽模式）
        self._shortcuts_dragging = False
        self._shortcuts_drag_offset_x = 0
        self._shortcuts_drag_offset_y = 0
        self._shortcuts_rect = pygame.Rect(0, 0, 0, 0)
        self._shortcuts_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._reload_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._shortcuts_scroll_y = 0
        self._shortcuts_scrollbar_dragging = False
        self._shortcuts_scrollbar_drag_start_y = 0
        self._shortcuts_scroll_start_y = 0
        self._shortcuts_max_scroll = 0

    def _init_data(self):
        self.ac = True
        self.mis = 0.1
        self.mas = 10.0
        self.mlo = 100
        self.Mlo = 180
        self.mla = 0
        self.Mla = 50
        self.show_info_box_normal = True
        self.show_info_box_season = True
        self.screen_width = self.sim.screen_width
        self.screen_height = self.sim.screen_height
        self.ace_display_mode = "original"
        self.main_rot_speed = 1.0
        self.level3_rot_speed = 1.5
        self.volume = 0.6
        self.name_display_mode = 0
        self.ace_geo_limit_enabled = False
        self.ace_limit_mode = ACE_LIMIT_NONE
        self.ace_limit_basin = ""
        self.ace_min_lon = 100
        self.ace_max_lon = 180
        self.ace_min_lat = 0
        self.ace_max_lat = 90
        self.hemisphere = HEMISPHERE_NORTH
        self.point_size = 150
        self.icon_size = 100
        self.disable_dpi_scaling = False
        self.fade_typhoon = True
        self.fade_path = True
        self.smooth_path = False
        self.ace_interpolated = False
        self.show_fps = False
        self.basin_filter_enabled = True

    @staticmethod
    def _parse_lon(text: str) -> Optional[float]:
        text = text.strip().upper()
        if not text:
            return None
        if re.fullmatch(r'\d+(\.\d+)?', text):
            v = float(text)
            if 0 <= v <= 360:
                return v
            return None
        m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([EW])', text)
        if not m:
            return None
        v = float(m.group(1))
        d = m.group(2)
        if d == 'W':
            if v == 0 or v == 180:
                return v
            return 360.0 - v
        else:
            return v

    @staticmethod
    def _parse_lat(text: str) -> Optional[float]:
        text = text.strip().upper()
        if not text:
            return None
        if re.fullmatch(r'\d+(\.\d+)?', text):
            v = float(text)
            if v == 0:
                return 0.0
            return None
        m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([NS])', text)
        if not m:
            return None
        v = float(m.group(1))
        d = m.group(2)
        if d == 'S':
            return -v
        return v

    @staticmethod
    def _is_lon_key(key: str) -> bool:
        return key in ('mlo', 'Mlo') or key.endswith('_lon')

    @staticmethod
    def _is_lat_key(key: str) -> bool:
        return key in ('mla', 'Mla') or key.endswith('_lat')

    def _pre_render_texts(self):
        self.title = rt(f_m, "设置", TXT)
        self.page_indicator_template = "第 {}/3 页"
        self.prev_text = rt(f_s, "上一页", (255, 255, 255))
        self.next_text = rt(f_s, "下一页", (255, 255, 255))
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))
        self.shortcuts_text = rt(f_s, "快捷键", (255, 255, 255))
        self.reload_text = rt(f_s, "重载数据", (255, 255, 255))
        self.close_shortcuts_text = rt(f_s, "关闭", (255, 255, 255))

        self.mis_label = rt(f_s, "最小速度:", TXT)
        self.mas_label = rt(f_s, "最大速度:", TXT)
        self.volume_label = rt(f_s, "音量 (%):", TXT)
        self.main_rot_label = rt(f_s, "主图标转速因子:", TXT)
        self.level3_rot_label = rt(f_s, "三级图标转速因子:", TXT)
        self.point_size_text = rt(f_s, "台风路径点大小 (%):", TXT)
        self.icon_size_text = rt(f_s, "台风图标大小 (%):", TXT)
        self.sh_label = rt(f_s, "窗口高度:", TXT)
        self.sw_label = rt(f_s, "窗口宽度:", TXT)
        self.mlo_label = rt(f_s, "最西经度:", TXT)
        self.Mlo_label = rt(f_s, "最东经度:", TXT)
        self.mla_label = rt(f_s, "最南纬度:", TXT)
        self.Mla_label = rt(f_s, "最北纬度:", TXT)
        self.lonlat_note1 = rt(f_s, "注: 经度须加 E/W 后缀 (如 140W), 180 和 0 除外;",
                               (100, 100, 100), 400)
        self.lonlat_note2 = rt(f_s, "纬度须加 N/S 后缀 (如 35N), 0 除外.", (100, 100, 100), 400)

        self.hemisphere_label = rt(f_s, "半球:", TXT)
        self.dpi_label = rt(f_s, "禁用DPI缩放 (需重启):", TXT)
        self.auto_continue_text = rt(f_s, "正常模式台风播放完成后自动继续:", TXT)
        self.normal_info_text = rt(f_s, "正常模式显示台风信息框:", TXT)
        self.season_info_text = rt(f_s, "台风季模式显示台风信息框:", TXT)
        self.fade_typhoon_text = rt(f_s, "台风图标平滑消失:", TXT)
        self.fade_path_text = rt(f_s, "台风路径平滑消失:", TXT)
        self.smooth_path_text = rt(f_s, "平滑路径 (Catmull-Rom):", TXT)
        self.ace_interp_text = rt(f_s, "连续 ACE:", TXT)
        self.fps_text = rt(f_s, "显示 FPS:", TXT)
        self.ace_mode_text = rt(f_s, "ACE显示模式:", TXT)
        self.orig_text = rt(f_s, "信息框样式", (255, 255, 255))
        self.prog_text = rt(f_s, "进度条样式", (255, 255, 255))
        self.name_mode_text = rt(f_s, "名称显示模式:", TXT)
        self.name_modes = [
            rt(f_s, "年份+名称", (255, 255, 255)),
            rt(f_s, "仅名称", (255, 255, 255)),
            rt(f_s, "原方式", (255, 255, 255))
        ]
        self.hemisphere_modes = [
            rt(f_s, "北半球", (255, 255, 255)),
            rt(f_s, "南半球", (255, 255, 255))
        ]
        self.ace_limit_label = rt(f_s, "ACE地理限制:", TXT)
        self.ace_limit_none_text = rt(f_s, "不启用", (255, 255, 255))
        self.ace_limit_latlon_text = rt(f_s, "按经纬度", (255, 255, 255))
        self.ace_limit_basin_text = rt(f_s, "按洋区", (255, 255, 255))
        self.ace_limit_note = rt(f_s, "注: ACE将只计算指定区域内的官方报.", (100, 100, 100), 400)
        self.basin_filter_text = rt(f_s, "启用洋区限制（仅加载/渲染进入过该洋区的台风）:", TXT)
        self.basin_filter_note = rt(f_s, "（洋区与上方ACE限制洋区相同；关闭则加载全部台风）", (100, 100, 100), 400)

    def activate(self):
        super().activate()
        self.ac = self.sim.ac
        self.mis = self.sim.mis
        self.mas = self.sim.mas
        self.mlo = self.sim.mlo
        self.Mlo = self.sim.Mlo
        self.mla = self.sim.mla
        self.Mla = self.sim.Mla
        self.show_info_box_normal = self.sim.show_info_box_normal
        self.show_info_box_season = self.sim.show_info_box_season
        self.screen_width = self.sim.screen_width
        self.screen_height = self.sim.screen_height
        self.ace_display_mode = self.sim.ace_display_mode
        self.main_rot_speed = self.sim.main_rotation_speed
        self.level3_rot_speed = self.sim.level3_rotation_speed
        self.volume = self.sim.volume
        self.name_display_mode = self.sim.name_display_mode
        self.ace_limit_mode = getattr(self.sim, 'ace_limit_mode', ACE_LIMIT_NONE)
        self.ace_limit_basin = getattr(self.sim, 'ace_limit_basin', "")
        self.ace_min_lon = self.sim.ace_min_lon
        self.ace_max_lon = self.sim.ace_max_lon
        self.ace_min_lat = self.sim.ace_min_lat
        self.ace_max_lat = self.sim.ace_max_lat
        self.hemisphere = self.sim.hemisphere
        self.point_size = self.sim.point_size
        self.icon_size = self.sim.icon_size
        self.disable_dpi_scaling = self.sim.disable_dpi_scaling
        self.fade_typhoon = self.sim.fade_typhoon
        self.fade_path = self.sim.fade_path
        self.smooth_path = self.sim.smooth_path
        self.ace_interpolated = self.sim.ace_interpolated
        self.show_fps = self.sim.show_fps
        self.basin_filter_enabled = getattr(self.sim, 'basin_filter_enabled', True)
        self.current_page = 0
        self.show_shortcuts = False
        self._needs_save = False
        self._ace_changed = False
        self._basin_dropdown_open = False
        self._basin_scroll_offset = 0
        self._build_basin_list()
        self._update_bg_rect()
        self.rebuild_fields()

    def _build_basin_list(self):
        areas = self.sim.res_mgr.ocean_areas.areas
        # 合并非合并洋区先，合并洋区（自动生成）在后
        manual = [(a.code, a.name_cn) for a in areas if not a.is_merged]
        merged = [(a.code, a.name_cn) for a in areas if a.is_merged]
        self._basin_list = manual + merged

    def deactivate(self):
        if self._needs_save:
            self.apply_settings()
        super().deactivate()
        self.dragging = False
        self._basin_dropdown_open = False

    def _update_bg_rect(self):
        dw, dh = SETTINGS_WIDTH, SETTINGS_HEIGHT
        dx = (self.sim.screen_width - dw) // 2
        dy = (self.sim.screen_height - dh) // 2
        self.bg_rect = pygame.Rect(dx, dy, dw, dh)

    def _sync_field_positions(self):
        if not self.fields or not self._field_offsets:
            return
        dx, dy = self.bg_rect.x, self.bg_rect.y
        for i, (off_x, off_y, _, _) in enumerate(self._field_offsets):
            if i < len(self.fields):
                self.fields[i].rect.x = dx + off_x
                self.fields[i].rect.y = dy + off_y

    def rebuild_fields(self):
        dx, dy = self.bg_rect.x, self.bg_rect.y
        self.fields.clear()
        self._field_offsets.clear()
        for key, val, rect, validator in self._get_fields_config():
            field = InputField(rect, max_length=10, validator=validator)
            field.set_text(val)
            field.key = key
            self.fields.append(field)
            self._field_offsets.append((rect[0] - dx, rect[1] - dy, rect[2], rect[3]))

    def _get_fields_config(self):
        dx, dy = self.bg_rect.x, self.bg_rect.y
        FIELD_W, FIELD_H = 80, 24
        COL_X = dx + 230
        lonlat_val = self.validate_lonlat
        if self.current_page == 0:
            return [
                ("mis", f"{self.mis:.1f}", (COL_X, dy + 70 - 2, FIELD_W, FIELD_H), self.validate_float),
                ("mas", f"{self.mas:.1f}", (COL_X, dy + 100 - 2, FIELD_W, FIELD_H), self.validate_float),
                ("volume", f"{int(self.volume*100)}", (COL_X, dy + 130 - 2, FIELD_W, FIELD_H), self.validate_int),
                ("main_rot_speed", f"{self.main_rot_speed:.2f}", (COL_X, dy + 160 - 2, FIELD_W, FIELD_H), self.validate_float),
                ("level3_rot_speed", f"{self.level3_rot_speed:.2f}", (COL_X, dy + 190 - 2, FIELD_W, FIELD_H), self.validate_float),
                ("point_size", f"{self.point_size}", (COL_X, dy + 220 - 2, FIELD_W, FIELD_H), self.validate_int),
                ("icon_size", f"{self.icon_size}", (COL_X, dy + 250 - 2, FIELD_W, FIELD_H), self.validate_int),
                ("screen_height", f"{self.screen_height}", (COL_X, dy + 290 - 2, FIELD_W, FIELD_H), self.validate_int),
                ("screen_width", f"{self.screen_width}", (COL_X, dy + 320 - 2, FIELD_W, FIELD_H), self.validate_int),
                ("mlo", lon_to_display(self.mlo), (COL_X, dy + 360 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("Mlo", lon_to_display(self.Mlo), (COL_X, dy + 390 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("mla", lat_to_display(self.mla), (COL_X, dy + 420 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("Mla", lat_to_display(self.Mla), (COL_X, dy + 450 - 2, FIELD_W, FIELD_H), lonlat_val),
            ]
        elif self.current_page == 1:
            return []
        else:
            config = []
            if self.ace_limit_mode == ACE_LIMIT_LATLON:
                config = [
                    ("ace_min_lon", lon_to_display(self.ace_min_lon), (COL_X, dy + 110 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lon", lon_to_display(self.ace_max_lon), (COL_X, dy + 140 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_min_lat", lat_to_display(self.ace_min_lat), (COL_X, dy + 170 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lat", lat_to_display(self.ace_max_lat), (COL_X, dy + 200 - 2, FIELD_W, FIELD_H), lonlat_val),
                ]
            return config

    @staticmethod
    def validate_float(char: str) -> bool:
        return char == '-' or char.isdigit() or char == '.'

    @staticmethod
    def validate_int(char: str) -> bool:
        return char.isdigit() or (char == '-' and len(char) == 1)

    @staticmethod
    def validate_lonlat(char: str) -> bool:
        return char.isdigit() or char in '.-' or char.upper() in 'EWNS'

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)
        self.draw_title(surface, self.title, self.bg_rect, y_offset=12)
        dx, dy, dw, dh = self.bg_rect
        page_text = self.page_indicator_template.format(self.current_page + 1)
        page_surf = rt(f_s, page_text, TXT)
        surface.blit(page_surf, (dx + dw // 2 - page_surf.get_width() // 2, dy + 50))

        if self.current_page == 0:
            self.draw_page1(surface, dx, dy)
        elif self.current_page == 1:
            self.draw_page2(surface, dx, dy)
        else:
            self.draw_page3(surface, dx, dy)

        for field in self.fields:
            field.draw(surface)

        btn_y = dy + dh - 55
        mx, my = pygame.mouse.get_pos()
        if self.current_page > 0:
            rect = pygame.Rect(dx + 80, btn_y, 80, 30)
            self.draw_button(surface, rect, rt(f_s, "上一页", (255, 255, 255)),
                           style='light', hover=rect.collidepoint(mx, my))
        if self.current_page < 2:
            rect = pygame.Rect(dx + 170, btn_y, 80, 30)
            self.draw_button(surface, rect, rt(f_s, "下一页", (255, 255, 255)),
                           style='light', hover=rect.collidepoint(mx, my))
        rect = pygame.Rect(dx + 260, btn_y, 80, 30)
        self.draw_button(surface, rect, rt(f_s, "确认", (255, 255, 255)),
                       style='primary', hover=rect.collidepoint(mx, my))
        rect = pygame.Rect(dx + 350, btn_y, 80, 30)
        self.draw_button(surface, rect, rt(f_s, "取消", (255, 255, 255)),
                       style='light', hover=rect.collidepoint(mx, my))

        self.draw_shortcuts_btn(surface, dx, dy, dw)
        if self.show_shortcuts:
            self.draw_shortcuts_help(surface)

    def draw_shortcuts_btn(self, surface, dx, dy, dw):
        btn_x = dx + dw - 200
        btn_y = dy + 10
        mx, my = pygame.mouse.get_pos()
        # 快捷键按钮 (light) — hover 时高亮
        sc_rect = pygame.Rect(btn_x, btn_y, 70, 22)
        self.draw_button(surface, sc_rect,
                         rt(f_s, "快捷键", (255, 255, 255)),
                         style='light', hover=sc_rect.collidepoint(mx, my))
        # 重载数据按钮 (primary) — hover 时高亮
        rl_rect = pygame.Rect(btn_x + 78, btn_y, 80, 22)
        self.draw_button(surface, rl_rect,
                         rt(f_s, "重载数据", (255, 255, 255)),
                         style='primary', hover=rl_rect.collidepoint(mx, my))
        self._shortcuts_btn_rect = sc_rect
        self._reload_btn_rect = rl_rect

    def draw_page1(self, surface, dx, dy):
        surface.blit(self.mis_label, (dx + 40, dy + 70))
        surface.blit(self.mas_label, (dx + 40, dy + 100))
        surface.blit(self.volume_label, (dx + 40, dy + 130))
        surface.blit(self.main_rot_label, (dx + 40, dy + 160))
        surface.blit(self.level3_rot_label, (dx + 40, dy + 190))
        surface.blit(self.point_size_text, (dx + 40, dy + 220))
        surface.blit(self.icon_size_text, (dx + 40, dy + 250))
        surface.blit(self.sh_label, (dx + 40, dy + 290))
        surface.blit(self.sw_label, (dx + 40, dy + 320))
        surface.blit(self.mlo_label, (dx + 40, dy + 360))
        surface.blit(self.Mlo_label, (dx + 40, dy + 390))
        surface.blit(self.mla_label, (dx + 40, dy + 420))
        surface.blit(self.Mla_label, (dx + 40, dy + 450))
        surface.blit(self.lonlat_note1, (dx + 40, dy + 480))
        surface.blit(self.lonlat_note2, (dx + 40, dy + 498))

    def draw_page2(self, surface, dx, dy):
        surface.blit(self.hemisphere_label, (dx + 40, dy + 70))
        for i, mode in enumerate(self.hemisphere_modes):
            rect = pygame.Rect(dx + 120 + i * 100, dy + 70, 80, 25)
            self._draw_toggle_button(surface, rect, mode,
                                     self.hemisphere == (HEMISPHERE_NORTH if i == 0 else HEMISPHERE_SOUTH))

        surface.blit(self.dpi_label, (dx + 40, dy + 100))
        self._draw_checkbox(surface, dx + 370, dy + 100, self.disable_dpi_scaling)

        surface.blit(self.auto_continue_text, (dx + 40, dy + 140))
        self._draw_checkbox(surface, dx + 370, dy + 140, self.ac)

        surface.blit(self.normal_info_text, (dx + 40, dy + 170))
        self._draw_checkbox(surface, dx + 370, dy + 170, self.show_info_box_normal)

        surface.blit(self.season_info_text, (dx + 40, dy + 200))
        self._draw_checkbox(surface, dx + 370, dy + 200, self.show_info_box_season)

        surface.blit(self.fade_typhoon_text, (dx + 40, dy + 230))
        self._draw_checkbox(surface, dx + 370, dy + 230, self.fade_typhoon)

        surface.blit(self.fade_path_text, (dx + 40, dy + 260))
        self._draw_checkbox(surface, dx + 370, dy + 260, self.fade_path)

        surface.blit(self.smooth_path_text, (dx + 40, dy + 290))
        self._draw_checkbox(surface, dx + 370, dy + 290, self.smooth_path)

        surface.blit(self.ace_interp_text, (dx + 40, dy + 320))
        self._draw_checkbox(surface, dx + 370, dy + 320, self.ace_interpolated)

        surface.blit(self.fps_text, (dx + 40, dy + 350))
        self._draw_checkbox(surface, dx + 370, dy + 350, self.show_fps)

        surface.blit(self.ace_mode_text, (dx + 40, dy + 390))
        ace_orig = pygame.Rect(dx + 150, dy + 385, 100, 25)
        ace_prog = pygame.Rect(dx + 260, dy + 385, 100, 25)
        self._draw_toggle_button(surface, ace_orig, self.orig_text, self.ace_display_mode == "original")
        self._draw_toggle_button(surface, ace_prog, self.prog_text, self.ace_display_mode == "progress_bar")

        surface.blit(self.name_mode_text, (dx + 40, dy + 420))
        for i, mode in enumerate(self.name_modes):
            rect = pygame.Rect(dx + 150 + i * 120, dy + 415, 100, 25)
            self._draw_toggle_button(surface, rect, mode, self.name_display_mode == i)

    def draw_page3(self, surface, dx, dy):
        surface.blit(self.ace_limit_label, (dx + 40, dy + 70))
        modes = [ACE_LIMIT_NONE, ACE_LIMIT_LATLON, ACE_LIMIT_BASIN]
        texts = [self.ace_limit_none_text, self.ace_limit_latlon_text, self.ace_limit_basin_text]
        btn_x = dx + 150
        for i, (mode, txt) in enumerate(zip(modes, texts)):
            rect = pygame.Rect(btn_x + i * 105, dy + 65, 95, 25)
            self._draw_toggle_button(surface, rect, txt, self.ace_limit_mode == mode)

        if self.ace_limit_mode == ACE_LIMIT_LATLON:
            surface.blit(rt(f_s, "最小经度:", TXT), (dx + 40, dy + 110))
            surface.blit(rt(f_s, "最大经度:", TXT), (dx + 40, dy + 140))
            surface.blit(rt(f_s, "最小纬度:", TXT), (dx + 40, dy + 170))
            surface.blit(rt(f_s, "最大纬度:", TXT), (dx + 40, dy + 200))
            surface.blit(self.ace_limit_note, (dx + 40, dy + 240))
        elif self.ace_limit_mode == ACE_LIMIT_BASIN:
            surface.blit(rt(f_s, "ACE限制洋区:", TXT), (dx + 40, dy + 110))
            # 洋区限制开关（复用ACE洋区，放在洋区选择器下方）
            basin_y = dy + 145
            surface.blit(self.basin_filter_text, (dx + 40, basin_y))
            self._draw_checkbox(surface, dx + 370, basin_y, self.basin_filter_enabled)
            surface.blit(self.basin_filter_note, (dx + 40, basin_y + 25))
            self._draw_basin_selector(surface, dx, dy)

    def _draw_basin_selector(self, surface, dx, dy):
        rect = pygame.Rect(dx + 150, dy + 105, 220, 26)
        area = self.sim.res_mgr.ocean_areas.get_by_code(self.ace_limit_basin)
        display = f"{area.code} {area.name_cn}" if area else "点击选择洋区"
        pygame.draw.rect(surface, (255, 255, 255), rect, 0, 3)
        pygame.draw.rect(surface, BUTTON_BORDER, rect, 1, 3)
        txt = rt(f_s, display, TXT)
        surface.blit(txt, (rect.x + 5, rect.y + 4))

        if self._basin_dropdown_open:
            ITEM_H = 24
            max_vis = 8
            list_h = min(len(self._basin_list), max_vis) * ITEM_H
            list_rect = pygame.Rect(rect.x, rect.bottom, rect.width, list_h)
            pygame.draw.rect(surface, (255, 255, 255), list_rect, 0, 3)
            pygame.draw.rect(surface, BUTTON_BORDER, list_rect, 1, 3)

            total = len(self._basin_list)
            visible_start = max(0, min(self._basin_scroll_offset, total - max_vis))
            for i in range(visible_start, min(visible_start + max_vis, total)):
                code, name_cn = self._basin_list[i]
                item_y = list_rect.y + (i - visible_start) * ITEM_H
                item_rect = pygame.Rect(list_rect.x, item_y, list_rect.width, ITEM_H)
                if item_rect.collidepoint(pygame.mouse.get_pos()):
                    pygame.draw.rect(surface, (180, 220, 255, 200), item_rect)
                item_txt = rt(f_s, f"{code} {name_cn}", TXT)
                surface.blit(item_txt, (item_rect.x + 5, item_rect.y + 3))

    # ── 快捷键分类数据 ─────────────────────────────────────────────
    SHORTCUT_SECTIONS = [
        ("▶  播放控制", (70, 130, 180), [
            ("Space",     "播放 / 暂停"),
            ("+ / =",     "增加播放速度 (+1)"),
            ("-",         "减小播放速度 (-1)"),
            ("左箭头",     "速度减半"),
            ("右箭头",     "速度加倍"),
            ("X",         "重置速度到 1×"),
        ]),
        ("🗔  视图导航", (60, 155, 100), [
            ("R",         "重置视图到配置文件"),
            ("右键拖拽",   "平移地图"),
            ("滚轮",       "缩放地图"),
            ("F12",       "切换窗口置顶状态"),
        ]),
        ("🌪  台风 / 模式", (210, 140, 50), [
            ("H",         "切换模式 (正常 ↔ 台风季 ↔ 编辑)"),
            ("[",         "上一个台风"),
            ("]",         "下一个台风"),
            ("I",         "新建台风 (编辑模式)"),
            ("T",         "时间跳转 (台风季模式)"),
        ]),
        ("📊  面板 / 工具", (140, 100, 200), [
            ("O",         "台风列表"),
            ("S",         "打开设置"),
            ("G",         "点列表 (编辑模式可编辑)"),
            ("K",         "台风详情 (正常/编辑) / ACE统计 (台风季)"),
        ]),
        ("✎  编辑操作", (200, 60, 60), [
            ("Ctrl + Z",  "撤销"),
            ("Ctrl + Y",  "重做"),
        ]),
        ("⚙  系统", (130, 130, 130), [
            ("P",         "截图 (保存到 screenshots 目录)"),
            ("Ctrl + R",  "重载台风数据"),
            ("Ctrl + L",  "加载编码台风"),
            ("ESC",       "退出当前对话框 / 菜单"),
        ]),
    ]

    def draw_shortcuts_help(self, surface):
        """绘制分类快捷键帮助面板（可滚动）。"""
        hx, hy = self._shortcuts_rect.x, self._shortcuts_rect.y
        hw, hh = self._shortcuts_rect.width, self._shortcuts_rect.height

        # ── 面板背景 ──
        panel = pygame.Surface((hw, hh), pygame.SRCALPHA)
        panel.fill((248, 251, 255, 248))
        pygame.draw.rect(panel, BUTTON_BORDER, (0, 0, hw, hh), 2, 10)

        # ── 标题栏 ──
        TITLE_H = DIALOG_TITLE_BAR_HEIGHT
        self.draw_title_bar(panel, pygame.Rect(0, 0, hw, TITLE_H),
                            "键盘快捷键 · Keyboard Shortcuts")

        # ── 视口参数 ──
        FOOTER_H = 48
        PAD_X = 18
        PAD_TOP = 8
        ROW_H = 28
        CAT_H = 30
        CAT_GAP = 6
        KEY_COL_W = 135          # 快捷键 chip 所在列宽
        DESC_X = KEY_COL_W + 14  # 描述文字起始 x

        viewport_y = TITLE_H
        viewport_h = hh - TITLE_H - FOOTER_H

        # ── 先计算内容总高度 ──
        total_content_h = PAD_TOP
        for _cat_name, _cat_color, items in self.SHORTCUT_SECTIONS:
            total_content_h += CAT_H + CAT_GAP
            total_content_h += len(items) * ROW_H
        total_content_h += 12  # bottom padding

        # ── 钳制滚动偏移 ──
        max_scroll = max(0, total_content_h - viewport_h)
        self._shortcuts_scroll_y = max(0, min(self._shortcuts_scroll_y, max_scroll))

        # ── 渲染内容到长条 surface ──
        content_surf = pygame.Surface((hw, total_content_h), pygame.SRCALPHA)
        y = PAD_TOP

        for cat_name, cat_color, items in self.SHORTCUT_SECTIONS:
            # 分类标题背景条
            cat_rect = pygame.Rect(PAD_X, y, hw - PAD_X * 2, CAT_H)
            cat_bg = pygame.Surface((cat_rect.width, cat_rect.height), pygame.SRCALPHA)
            cat_bg.fill((*cat_color, 35))
            pygame.draw.rect(cat_bg, (*cat_color, 140), (0, 0, cat_rect.width, cat_rect.height), 0, 5)
            content_surf.blit(cat_bg, (cat_rect.x, cat_rect.y))
            cat_label = rt(f_m, cat_name, cat_color)
            content_surf.blit(cat_label, (cat_rect.x + 10, cat_rect.y + (CAT_H - cat_label.get_height()) // 2))
            y += CAT_H + CAT_GAP

            for key_str, desc_str in items:
                row_y = y + (ROW_H - 22) // 2

                # ── 快捷键 chip ──
                key_surf = rt(f_s, key_str, TXT)
                chip_w = key_surf.get_width() + 16
                chip_h = 22
                chip_x = PAD_X + KEY_COL_W - chip_w - 4
                chip_rect = pygame.Rect(chip_x, row_y, chip_w, chip_h)
                chip_bg = pygame.Surface((chip_w, chip_h), pygame.SRCALPHA)
                chip_bg.fill((*cat_color, 28))
                pygame.draw.rect(chip_bg, (*cat_color, 160), (0, 0, chip_w, chip_h), 1, 4)
                content_surf.blit(chip_bg, (chip_rect.x, chip_rect.y))
                content_surf.blit(key_surf, (
                    chip_rect.x + (chip_w - key_surf.get_width()) // 2,
                    chip_rect.y + (chip_h - key_surf.get_height()) // 2))

                # ── 描述文字 ──
                desc_surf = rt(f_s, desc_str, TXT)
                content_surf.blit(desc_surf, (PAD_X + DESC_X, row_y + (chip_h - desc_surf.get_height()) // 2))
                y += ROW_H

        y += 8  # bottom padding
        # 确保 content_surf 高度与实际一致（裁剪多余空白）
        actual_h = y
        if actual_h < total_content_h:
            content_surf = content_surf.subsurface((0, 0, hw, actual_h))
            total_content_h = actual_h
            max_scroll = max(0, total_content_h - viewport_h)
            self._shortcuts_scroll_y = max(0, min(self._shortcuts_scroll_y, max_scroll))

        # ── 裁剪并 blit 到视口 ──
        self._shortcuts_max_scroll = max_scroll
        clip_rect = pygame.Rect(0, self._shortcuts_scroll_y, hw, viewport_h)
        panel.blit(content_surf, (0, viewport_y), clip_rect)

        # ── 视口底部渐变遮罩（内容可滚动时） ──
        if max_scroll > 0 and self._shortcuts_scroll_y < max_scroll - 4:
            fade_h = 20
            fade = pygame.Surface((hw, fade_h), pygame.SRCALPHA)
            for i in range(fade_h):
                alpha = int(180 * (i / fade_h))
                fade.fill((248, 251, 255, alpha), (0, i, hw, 1))
            panel.blit(fade, (0, viewport_y + viewport_h - fade_h))

        # ── 滚动条 ──
        if max_scroll > 0:
            SB_W = 8
            SB_MARGIN = 4
            sb_x = hw - SB_W - SB_MARGIN
            sb_track_top = viewport_y + 4
            sb_track_h = viewport_h - 8
            # 轨道
            pygame.draw.rect(panel, (210, 215, 225), (sb_x, sb_track_top, SB_W, sb_track_h), 0, 4)
            # 滑块
            thumb_h = max(28, sb_track_h * viewport_h / total_content_h)
            thumb_travel = sb_track_h - thumb_h
            thumb_y = sb_track_top + (thumb_travel * self._shortcuts_scroll_y / max_scroll) if max_scroll > 0 else sb_track_top
            thumb_color = (150, 160, 180) if not self._shortcuts_scrollbar_dragging else (100, 110, 140)
            pygame.draw.rect(panel, thumb_color, (sb_x, int(thumb_y), SB_W, int(thumb_h)), 0, 4)

        # ── 底栏 + 关闭按钮 ──
        footer_y = hh - FOOTER_H
        pygame.draw.line(panel, (200, 210, 225), (20, footer_y), (hw - 20, footer_y), 1)
        close_rect = pygame.Rect(hw // 2 - 50, footer_y + 10, 100, 28)
        close_txt = rt(f_s, "关  闭", (255, 255, 255))
        hover = close_rect.collidepoint(
            (pygame.mouse.get_pos()[0] - hx, pygame.mouse.get_pos()[1] - hy))
        self.draw_button(panel, close_rect, close_txt, style='primary', hover=hover)

        # ── 最终贴到屏幕 ──
        surface.blit(panel, (hx, hy))

    def _handle_shortcuts_event(self, e: pygame.event.Event) -> bool:
        """处理快捷键面板内的所有事件（滚动、拖拽、关闭等）。"""
        sr = self._shortcuts_rect
        FOOTER_H = 48
        TITLE_H = DIALOG_TITLE_BAR_HEIGHT
        SB_W = 8
        SB_MARGIN = 4
        viewport_h = sr.height - TITLE_H - FOOTER_H

        # ── 键盘 ──
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._close_shortcuts()
            return True  # 面板打开时吞噬所有按键

        # ── 滚轮 ──
        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if sr.collidepoint(mx, my) and self._shortcuts_max_scroll > 0:
                self._shortcuts_scroll_y = max(
                    0, min(self._shortcuts_scroll_y - e.y * 32,
                           self._shortcuts_max_scroll))
            return True

        # ── 鼠标按下 ──
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            mx, my = e.pos

            # 点击面板外 → 关闭
            if not sr.collidepoint(mx, my):
                self._close_shortcuts()
                return True

            lx, ly = mx - sr.x, my - sr.y  # 面板内局部坐标

            # 关闭按钮
            close_rect = pygame.Rect(sr.width // 2 - 50, sr.height - FOOTER_H + 10, 100, 28)
            if close_rect.collidepoint(lx, ly):
                self._close_shortcuts()
                return True

            # 标题栏拖拽（与 DraggableDialog.handle_drag_event 相同模式）
            if ly < TITLE_H:
                self._shortcuts_dragging = True
                self._shortcuts_drag_offset_x = lx
                self._shortcuts_drag_offset_y = ly
                # 提升到栈顶
                if hasattr(self.sim, '_dialog_stack') and self in self.sim._dialog_stack:
                    self.sim._dialog_stack.remove(self)
                    self.sim._dialog_stack.append(self)
                return True

            # 滚动条滑块按下
            if lx >= sr.width - SB_W - SB_MARGIN - 4 and self._shortcuts_max_scroll > 0:
                sb_x = sr.width - SB_W - SB_MARGIN
                sb_track_top = TITLE_H + 4
                sb_track_h = viewport_h - 8
                total_h = self._shortcuts_max_scroll + viewport_h
                thumb_h = max(28, sb_track_h * viewport_h / total_h)
                thumb_travel = sb_track_h - thumb_h
                thumb_y = sb_track_top + (thumb_travel * self._shortcuts_scroll_y / self._shortcuts_max_scroll)
                thumb_rect = pygame.Rect(sb_x, int(thumb_y), SB_W, int(thumb_h))
                if thumb_rect.collidepoint(lx, ly):
                    self._shortcuts_scrollbar_dragging = True
                    self._shortcuts_scrollbar_drag_start_y = ly
                    self._shortcuts_scroll_start_y = self._shortcuts_scroll_y
                    return True

            # 点击面板内其他位置 → 吞掉事件
            return True

        # ── 鼠标释放 ──
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._shortcuts_dragging = False
            self._shortcuts_scrollbar_dragging = False
            return True

        # ── 鼠标移动（拖拽标题栏 / 拖拽滚动条）──
        if e.type == pygame.MOUSEMOTION:
            if self._shortcuts_dragging:
                new_x = e.pos[0] - self._shortcuts_drag_offset_x
                new_y = e.pos[1] - self._shortcuts_drag_offset_y
                new_x = max(0, min(new_x, self.sim.screen_width - sr.width))
                new_y = max(0, min(new_y, self.sim.screen_height - sr.height))
                self._shortcuts_rect.x = new_x
                self._shortcuts_rect.y = new_y
                return True
            if self._shortcuts_scrollbar_dragging and self._shortcuts_max_scroll > 0:
                mx, my = e.pos
                ly = my - sr.y
                sb_track_top = TITLE_H + 4
                sb_track_h = viewport_h - 8
                total_h = self._shortcuts_max_scroll + viewport_h
                thumb_h = max(28, sb_track_h * viewport_h / total_h)
                thumb_travel = sb_track_h - thumb_h
                dy = ly - self._shortcuts_scrollbar_drag_start_y
                scroll_per_pixel = self._shortcuts_max_scroll / thumb_travel if thumb_travel > 0 else 0
                self._shortcuts_scroll_y = max(0, min(
                    self._shortcuts_scroll_start_y + dy * scroll_per_pixel,
                    self._shortcuts_max_scroll))
                return True

        return False

    def _close_shortcuts(self):
        """关闭快捷键面板并重置状态。"""
        self.show_shortcuts = False
        self._shortcuts_dragging = False
        self._shortcuts_scrollbar_dragging = False
        self._shortcuts_scroll_y = 0
        self._shortcuts_max_scroll = 0

    def _draw_checkbox(self, surface, x, y, checked):
        box = pygame.Rect(x, y, 20, 20)
        pygame.draw.rect(surface, (200, 200, 200), box, 0, 3)
        if checked:
            pygame.draw.rect(surface, BUTTON_BORDER, (x + 4, y + 4, 12, 12), 0, 2)

    def _draw_toggle_button(self, surface, rect, text_surf, active):
        bg = BUTTON_BG if active else BUTTON_DISABLED
        pygame.draw.rect(surface, bg, rect, 0, 5)
        surface.blit(text_surf, (rect.centerx - text_surf.get_width() // 2,
                                 rect.centery - text_surf.get_height() // 2))

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if self._basin_dropdown_open:
            return self._handle_basin_dropdown(e)

        # ── 快捷键面板：拦截所有事件 ──
        if self.show_shortcuts:
            return self._handle_shortcuts_event(e)

        # 标题栏按钮（快捷键/重载数据）优先于拖拽检测
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self._shortcuts_btn_rect.collidepoint(x, y):
                self.show_shortcuts = True
                self._shortcuts_scroll_y = 0
                self._shortcuts_scrollbar_dragging = False
                self._shortcuts_dragging = False
                hw, hh = 600, 540
                self._shortcuts_rect = pygame.Rect(
                    (self.sim.screen_width - hw) // 2,
                    (self.sim.screen_height - hh) // 2,
                    hw, hh)
                return True
            if self._reload_btn_rect.collidepoint(x, y):
                self._sync_ace_settings_to_sim()
                self.sim.reload_typhoons()
                self.deactivate()
                return True

        if self.handle_drag_event(e):
            self._sync_field_positions()
            return True

        if e.type == pygame.MOUSEWHEEL:
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, field in enumerate(self.fields):
                if field.rect.collidepoint(e.pos):
                    for f in self.fields:
                        f.deactivate()
                    field.activate()
                    self.current_field = i
                    return True
        for i, field in enumerate(self.fields):
            if field.handle_event(e):
                return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            elif e.key == pygame.K_RETURN:
                self._needs_save = True
                self.deactivate()
                return True
            elif e.key == pygame.K_TAB or e.key == pygame.K_KP_ENTER:
                if self.fields:
                    active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                    shift = pygame.key.get_mods() & pygame.KMOD_SHIFT
                    nxt = (active_idx + (-1 if shift else 1)) % len(self.fields) if active_idx != -1 else 0
                    if active_idx != -1:
                        self.fields[active_idx].deactivate()
                    self.fields[nxt].activate()
                    self.current_field = nxt
                return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            dx, dy, dw, dh = self.bg_rect
            x, y = e.pos
            btn_y = dy + dh - 55
            if self.current_page > 0 and pygame.Rect(dx + 80, btn_y, 80, 30).collidepoint(x, y):
                self.current_page -= 1
                self.rebuild_fields()
                return True
            if self.current_page < 2 and pygame.Rect(dx + 170, btn_y, 80, 30).collidepoint(x, y):
                self.current_page += 1
                self.rebuild_fields()
                return True
            if pygame.Rect(dx + 260, btn_y, 80, 30).collidepoint(x, y):
                self._needs_save = True
                self.deactivate()
                return True
            if pygame.Rect(dx + 350, btn_y, 80, 30).collidepoint(x, y):
                self.deactivate()
                return True

            if self.current_page == 1:
                for i in range(2):
                    if pygame.Rect(dx + 120 + i * 100, dy + 70, 80, 25).collidepoint(x, y):
                        self.hemisphere = HEMISPHERE_NORTH if i == 0 else HEMISPHERE_SOUTH
                        self._ace_changed = True
                        return True
                if pygame.Rect(dx + 370, dy + 100, 20, 20).collidepoint(x, y):
                    self.disable_dpi_scaling = not self.disable_dpi_scaling
                    return True
                if pygame.Rect(dx + 370, dy + 140, 20, 20).collidepoint(x, y):
                    self.ac = not self.ac
                    return True
                if pygame.Rect(dx + 370, dy + 170, 20, 20).collidepoint(x, y):
                    self.show_info_box_normal = not self.show_info_box_normal
                    return True
                if pygame.Rect(dx + 370, dy + 200, 20, 20).collidepoint(x, y):
                    self.show_info_box_season = not self.show_info_box_season
                    return True
                if pygame.Rect(dx + 370, dy + 230, 20, 20).collidepoint(x, y):
                    self.fade_typhoon = not self.fade_typhoon
                    return True
                if pygame.Rect(dx + 370, dy + 260, 20, 20).collidepoint(x, y):
                    self.fade_path = not self.fade_path
                    return True
                if pygame.Rect(dx + 370, dy + 290, 20, 20).collidepoint(x, y):
                    self.smooth_path = not self.smooth_path
                    return True
                if pygame.Rect(dx + 370, dy + 320, 20, 20).collidepoint(x, y):
                    self.ace_interpolated = not self.ace_interpolated
                    return True
                if pygame.Rect(dx + 370, dy + 350, 20, 20).collidepoint(x, y):
                    self.show_fps = not self.show_fps
                    return True
                if pygame.Rect(dx + 150, dy + 385, 100, 25).collidepoint(x, y):
                    self.ace_display_mode = "original"
                    return True
                if pygame.Rect(dx + 260, dy + 385, 100, 25).collidepoint(x, y):
                    self.ace_display_mode = "progress_bar"
                    return True
                for i in range(3):
                    if pygame.Rect(dx + 150 + i * 120, dy + 415, 100, 25).collidepoint(x, y):
                        self.name_display_mode = i
                        return True
            elif self.current_page == 2:
                modes = [ACE_LIMIT_NONE, ACE_LIMIT_LATLON, ACE_LIMIT_BASIN]
                for i in range(3):
                    if pygame.Rect(dx + 150 + i * 105, dy + 65, 95, 25).collidepoint(x, y):
                        self.ace_limit_mode = modes[i]
                        self._ace_changed = True
                        self.rebuild_fields()
                        return True
                if self.ace_limit_mode == ACE_LIMIT_BASIN:
                    basin_rect = self._get_basin_rect()
                    if basin_rect.collidepoint(x, y):
                        self._basin_dropdown_open = True
                        self._basin_scroll_offset = 0
                        return True
                    # 洋区限制开关（复用ACE洋区）
                    basin_filter_y = dy + 145
                    if pygame.Rect(dx + 370, basin_filter_y, 20, 20).collidepoint(x, y):
                        self.basin_filter_enabled = not self.basin_filter_enabled
                        self._ace_changed = True
                        return True
        return False

    def _get_basin_rect(self):
        dx, dy = self.bg_rect.x, self.bg_rect.y
        return pygame.Rect(dx + 150, dy + 105, 220, 26)

    def _handle_basin_dropdown(self, e) -> bool:
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._basin_dropdown_open = False
                return True
            return True
        if e.type == pygame.MOUSEWHEEL:
            self._basin_scroll_offset -= e.y
            self._basin_scroll_offset = max(0, min(self._basin_scroll_offset, len(self._basin_list) - 8))
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            dx, dy = self.bg_rect.x, self.bg_rect.y
            x, y = e.pos
            rect = self._get_basin_rect()
            list_h = min(len(self._basin_list), 8) * 24
            list_rect = pygame.Rect(rect.x, rect.bottom, rect.width, list_h)
            if list_rect.collidepoint(x, y):
                idx = int((y - list_rect.y) // 24) + self._basin_scroll_offset
                if 0 <= idx < len(self._basin_list):
                    self.ace_limit_basin = self._basin_list[idx][0]
                    self._ace_changed = True
                    self._basin_dropdown_open = False
                    return True
            self._basin_dropdown_open = False
            return True
        return True

    def _sync_ace_settings_to_sim(self):
        self.sim.ace_limit_mode = self.ace_limit_mode
        self.sim.ace_limit_basin = self.ace_limit_basin
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.sim.ace_min_lon = self.ace_min_lon
        self.sim.ace_max_lon = self.ace_max_lon
        self.sim.ace_min_lat = self.ace_min_lat
        self.sim.ace_max_lat = self.ace_max_lat
        self.sim.hemisphere = self.hemisphere

    def apply_settings(self):
        # 先验证所有字段再批量应用，避免部分失败导致状态不一致
        validated = {}
        for field in self.fields:
            key = field.key
            val = field.get_text().strip()
            try:
                if self._is_lon_key(key):
                    parsed = self._parse_lon(val)
                    if parsed is None:
                        self.sim.show_error(f"无效经度: {val} (需加 E/W 后缀, 180 和 0 除外)")
                        return
                    validated[key] = parsed
                elif self._is_lat_key(key):
                    parsed = self._parse_lat(val)
                    if parsed is None:
                        self.sim.show_error(f"无效纬度: {val} (需加 N/S 后缀, 0 除外)")
                        return
                    validated[key] = parsed
                elif key in ('screen_width', 'screen_height', 'point_size', 'icon_size'):
                    validated[key] = int(val)
                elif key in ('mis', 'mas', 'main_rot_speed', 'level3_rot_speed'):
                    validated[key] = float(val)
                elif key == 'volume':
                    validated[key] = int(val) / 100.0
            except ValueError:
                pass

        # 批量赋值到 self（settings 本地）
        for key, value in validated.items():
            setattr(self, key, value)

        self.mis = max(0.1, self.mis)
        self.mas = min(20.0, self.mas)
        self.volume = max(0.0, min(1.0, self.volume))

        # 检测洋区过滤相关设置是否变化（需重载台风数据）
        # 必须在 ace_limit_basin/ace_limit_mode 同步到 sim 之前获取旧值
        old_filter = (getattr(self.sim, 'basin_filter_enabled', True),
                      self.sim.ace_limit_basin,
                      self.sim.ace_limit_mode)
        new_filter = (self.basin_filter_enabled, self.ace_limit_basin, self.ace_limit_mode)

        # 同步到 sim（先记录视图相关旧值，仅变化时重建视图）
        old_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                           self.sim.screen_width, self.sim.screen_height)
        old_smooth = self.sim.smooth_path
        self.sim.ac = self.ac
        self.sim.mis = self.mis
        self.sim.mas = self.mas
        self.sim.mlo = self.mlo
        self.sim.Mlo = self.Mlo
        self.sim.mla = self.mla
        self.sim.Mla = self.Mla
        self.sim.show_info_box_normal = self.show_info_box_normal
        self.sim.show_info_box_season = self.show_info_box_season
        self.sim.screen_width = self.screen_width
        self.sim.screen_height = self.screen_height
        self.sim.ace_display_mode = self.ace_display_mode
        self.sim.main_rotation_speed = self.main_rot_speed
        self.sim.level3_rotation_speed = self.level3_rot_speed
        self.sim.volume = self.volume
        self.sim.name_display_mode = self.name_display_mode
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.sim.ace_limit_mode = self.ace_limit_mode
        self.sim.ace_limit_basin = self.ace_limit_basin
        self.sim.ace_min_lon = self.ace_min_lon
        self.sim.ace_max_lon = self.ace_max_lon
        self.sim.ace_min_lat = self.ace_min_lat
        self.sim.ace_max_lat = self.ace_max_lat
        self.sim.hemisphere = self.hemisphere
        self.sim.point_size = self.point_size
        self.sim.icon_size = self.icon_size
        self.sim.disable_dpi_scaling = self.disable_dpi_scaling
        self.sim.fade_typhoon = self.fade_typhoon
        self.sim.fade_path = self.fade_path
        self.sim.smooth_path = self.smooth_path
        self.sim.ace_interpolated = self.ace_interpolated
        self.sim.show_fps = self.show_fps
        if old_smooth != self.smooth_path:
            self.sim.update_all_screen_points()

        self.sim.basin_filter_enabled = self.basin_filter_enabled

        if self._ace_changed:
            self.sim.recalc_all_ace()
            self._ace_changed = False

        new_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                           self.sim.screen_width, self.sim.screen_height)
        if old_view_bounds != new_view_bounds:
            self.sim.map_mgr.update_view()

        if old_filter != new_filter:
            self.sim._apply_basin_filter()

        self.sim.update_all_screen_points()
        self.sim._config_needs_save = True
        self.sim.save_config()