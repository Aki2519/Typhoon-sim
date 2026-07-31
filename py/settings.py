# py/settings.py
"""设置对话框（全屏暗色面板 + 顶部 Tab 导航）。"""
from __future__ import annotations

import re
from datetime import datetime
import pygame
from .constants import (
    f_s, f_m, rt, TXT,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH, LIST_BG, LIST_HL,
    BUTTON_BORDER,
    SETTINGS_DARK_BG, SETTINGS_DARK_OVERLAY,
    settings_accent,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_TAB_BG, SETTINGS_TAB_ACTIVE, SETTINGS_TAB_HOVER,
    SETTINGS_ACCENT,
    SETTINGS_INPUT_BG, SETTINGS_INPUT_BORDER,
    SETTINGS_CHECKBOX_BG, SETTINGS_CHECKBOX_CHECK,
    SETTINGS_TOGGLE_ON, SETTINGS_TOGGLE_OFF,
    SETTINGS_TAB_NAMES,
    DIALOG_CORNER_RADIUS, DIALOG_TITLE_BAR_HEIGHT,
    ICON_SET_SIMPLE, ICON_SET_SMCY, ICON_SET_NAMES
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
        self.tab_index = 0
        self._tab_indicator_x: float = 0.0
        self._tab_indicator_target: float = 0.0
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
        self._targets: list = []  # [(rect, callback), ...]
        self._shortcuts_scroll_y = 0
        self._shortcuts_scrollbar_dragging = False
        self._shortcuts_scrollbar_drag_start_y = 0
        self._shortcuts_scroll_start_y = 0
        self._shortcuts_max_scroll = 0

    def _is_title_bar(self, pos):
        return (self.bg_rect.collidepoint(pos)
                and pos[1] - self.bg_rect.y < 40)

    @property
    def _accent(self):
        return settings_accent(self.dark_mode, self.sim.color_scheme)

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
        self.ace_display_mode = "progress_bar"
        self.main_rot_speed = 1.0
        self.level3_rot_speed = 1.5
        self.volume = 0.6
        self.name_display_mode = 0
        self.point_name_mode = False
        self.ace_geo_limit_enabled = False
        self.ace_limit_mode = ACE_LIMIT_NONE
        self.ace_limit_basin = ""
        self.ace_min_lon = 100
        self.ace_max_lon = 180
        self.ace_min_lat = 0
        self.ace_max_lat = 90
        self.hemisphere = HEMISPHERE_NORTH
        self._map_range_mode = 1 if getattr(self.sim, 'map_corner_mode', False) else 0
        self.point_size = 150
        self.icon_size = 100
        self.name_size = 100
        self.peak_label_size = 100
        self.disable_dpi_scaling = False
        self.fade_typhoon = True
        self.fade_path = True
        self.fade_path_mode = "fade"
        self.smooth_path = False
        self.smooth_path_mode = "monotone"
        self.path_mode = "markers"
        self.ace_interpolated = False
        self.show_fps = False
        self.fps_cap = 120
        self.show_ri_effect = True
        self.show_future_path = True
        self.show_ace_bar = True
        self.show_ace_total = True
        self.monthly_summary = True
        self.basin_filter_enabled = True
        self.icon_set = ICON_SET_SIMPLE
        self.color_scheme = 1

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

    def _lh(self, surf, ctrl_h):
        """label surface 与控件垂直居中的 Y 偏移"""
        return (ctrl_h - surf.get_height()) // 2

    def _pre_render_texts(self):
        TX = SETTINGS_TEXT_LIGHT if self.dark_mode else TXT
        TD = SETTINGS_TEXT_DIM if self.dark_mode else (100, 110, 130)

        self.mis_label = rt(f_m, "最小速度:", TX)
        self.mas_label = rt(f_m, "最大速度:", TX)
        self.volume_label = rt(f_m, "音量 (%):", TX)
        self.main_rot_label = rt(f_m, "主旋转速度:", TX)
        self.level3_rot_label = rt(f_m, "3级旋转速度:", TX)
        self.point_size_text = rt(f_m, "台风路径点大小 (%):", TX)
        self.icon_size_text = rt(f_m, "台风图标大小 (%):", TX)
        self.name_size_text = rt(f_m, "台风名称大小 (%):", TX)
        self.peak_label_text = rt(f_m, "巅峰/登陆文字 (%):", TX)
        self.sh_label = rt(f_m, "窗口高度:", TX)
        self.sw_label = rt(f_m, "窗口宽度:", TX)
        self.mlo_label = rt(f_m, "最西经度:", TX)
        self.Mlo_label = rt(f_m, "最东经度:", TX)
        self.mla_label = rt(f_m, "最南纬度:", TX)
        self.Mla_label = rt(f_m, "最北纬度:", TX)
        self.lonlat_note1 = rt(f_s, "注: 经度须加 E/W 后缀 (如 140W), 180 和 0 除外;",
                               TD, 400)
        self.lonlat_note2 = rt(f_s, "纬度须加 N/S 后缀 (如 35N), 0 除外.", TD, 400)

        self.bl_lon_label = rt(f_m, "左下角经度:", TX)
        self.bl_lat_label = rt(f_m, "左下角纬度:", TX)
        self.span_lon_label = rt(f_m, "地图宽度 (°):", TX)
        self.map_range_modes = [rt(f_m, "经纬范围", (255, 255, 255)), rt(f_m, "角点+大小", (255, 255, 255))]

        self.hemisphere_label = rt(f_m, "半球:", TX)
        self.dpi_label = rt(f_m, "禁用DPI缩放 (需重启):", TX)
        self.auto_continue_text = rt(f_m, "正常模式台风播放完成后自动继续:", TX)
        self.normal_info_text = rt(f_m, "正常模式显示台风信息框:", TX)
        self.season_info_text = rt(f_m, "台风季模式显示台风信息框:", TX)
        self.fade_typhoon_text = rt(f_m, "台风图标平滑消失:", TX)
        self.fade_path_mode_text = rt(f_m, "路径消失模式:", TX)
        self.fade_path_mode_modes = [rt(f_m, "不消失", (255, 255, 255)),
                                     rt(f_m, "平滑", (255, 255, 255)),
                                     rt(f_m, "快速", (255, 255, 255))]
        self.smooth_path_text = rt(f_m, "平滑路径:", TX)
        self.smooth_path_mode_text = rt(f_m, "插值模式:", TX)
        self.smooth_path_mode_modes = [rt(f_m, "单调三次", (255, 255, 255)),
                                       rt(f_m, "Catmull", (255, 255, 255))]
        self.path_mode_text = rt(f_m, "路径模式:", TX)
        self.path_mode_modes = [rt(f_m, "点阵", (255, 255, 255)), rt(f_m, "渐变线", (255, 255, 255))]
        self.ace_interp_text = rt(f_m, "连续 ACE:", TX)
        self.fps_text = rt(f_m, "显示 FPS:", TX)
        self.fps_cap_text = rt(f_m, "帧率上限:", TX)
        self.fps_cap_modes = [rt(f_m, "60", (255, 255, 255)),
                              rt(f_m, "120", (255, 255, 255)),
                              rt(f_m, "无限制", (255, 255, 255))]
        self.show_ri_text = rt(f_m, "显示 ERI 动画:", TX)
        self.future_path_text = rt(f_m, "显示未经过的路径:", TX)
        self.monthly_summary_text = rt(f_m, "月度 ACE 总结弹窗:", TX)
        self.fix_icon_point_text = rt(f_m, "固定图标与路径点大小:", TX)
        self.color_scheme_text = rt(f_m, "配色方案:", TX)
        self.icon_set_text = rt(f_m, "台风图标:", TX)
        self.icon_set_warn = rt(f_s, "SMCY图标影响性能较大,谨慎使用", (200, 80, 80), 400)
        self.icon_set_modes = [
            rt(f_m, ICON_SET_NAMES[ICON_SET_SIMPLE], (255, 255, 255)),
            rt(f_m, ICON_SET_NAMES[ICON_SET_SMCY], (255, 255, 255))
        ]
        self.name_mode_text = rt(f_m, "名称显示模式:", TX)
        self.name_modes = [
            rt(f_m, "年份+名称", (255, 255, 255)),
            rt(f_m, "仅名称", (255, 255, 255)),
            rt(f_m, "原方式", (255, 255, 255))
        ]
        self.point_name_text = rt(f_m, "逐点名称:", TX)
        self.hemisphere_modes = [
            rt(f_m, "北半球", (255, 255, 255)),
            rt(f_m, "南半球", (255, 255, 255))
        ]
        self.ace_show_label = rt(f_m, "显示ACE进度条:", TX)
        self.ace_total_label = rt(f_m, "显示ACE总数值:", TX)
        self.ace_limit_label = rt(f_m, "ACE地理限制:", TX)
        self.ace_limit_none_text = rt(f_m, "不启用", (255, 255, 255))
        self.ace_limit_latlon_text = rt(f_m, "按经纬度", (255, 255, 255))
        self.ace_limit_basin_text = rt(f_m, "按洋区", (255, 255, 255))
        self.ace_limit_note = rt(f_s, "注: ACE将只计算指定区域内的官方报.", TD, 400)
        self.basin_filter_text = rt(f_s, "启用洋区限制（仅加载/渲染进入过该洋区的台风）:", TX)
        self.basin_filter_note = rt(f_s, "（洋区与上方ACE限制洋区相同；关闭则加载全部台风）", TD, 400)

    def _refresh_texts(self):
        self._pre_render_texts()

    def activate(self):
        super().activate()
        self.ac = self.sim.ac
        self._map_range_mode = 1 if getattr(self.sim, 'map_corner_mode', False) else 0
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
        self.point_name_mode = getattr(self.sim, 'point_name_mode', False)
        self.ace_limit_mode = getattr(self.sim, 'ace_limit_mode', ACE_LIMIT_NONE)
        self.ace_limit_basin = getattr(self.sim, 'ace_limit_basin', "")
        self.ace_min_lon = self.sim.ace_min_lon
        self.ace_max_lon = self.sim.ace_max_lon
        self.ace_min_lat = self.sim.ace_min_lat
        self.ace_max_lat = self.sim.ace_max_lat
        self.hemisphere = self.sim.hemisphere
        self.point_size = self.sim.point_size
        self.icon_size = self.sim.icon_size
        self.name_size = getattr(self.sim, 'name_size', 100)
        self.peak_label_size = getattr(self.sim, 'peak_label_size', 100)
        self.fix_icon_point_size = self.sim.fix_icon_point_size
        self.disable_dpi_scaling = self.sim.disable_dpi_scaling
        self.fade_typhoon = self.sim.fade_typhoon
        self.fade_path = self.sim.fade_path
        self.fade_path_mode = getattr(self.sim, 'fade_path_mode', 'fade')
        self.smooth_path = self.sim.smooth_path
        self.smooth_path_mode = getattr(self.sim, 'smooth_path_mode', 'monotone')
        self.path_mode = getattr(self.sim, 'path_mode', 'markers')
        self.ace_interpolated = self.sim.ace_interpolated
        self.show_fps = self.sim.show_fps
        self.fps_cap = getattr(self.sim, 'fps_cap', 120)
        self.show_ri_effect = getattr(self.sim, 'show_ri_effect', True)
        self.show_future_path = getattr(self.sim, 'show_future_path', True)
        self.monthly_summary = getattr(self.sim, 'monthly_summary', True)
        self.show_ace_bar = getattr(self.sim, 'show_ace_bar', True)
        self.show_ace_total = getattr(self.sim, 'show_ace_total', True)
        self.basin_filter_enabled = getattr(self.sim, 'basin_filter_enabled', True)
        self.icon_set = getattr(self.sim, 'icon_set', ICON_SET_SIMPLE)
        self.color_scheme = getattr(self.sim, 'color_scheme', 1)
        if not hasattr(self, 'tab_index') or self.tab_index < 0:
            self.tab_index = 0
        self._tab_indicator_x = 0.0
        self._tab_indicator_target = 0.0
        self.show_shortcuts = False
        self._needs_save = False
        self._ace_changed = False
        self._hemisphere_changed = False
        self._basin_dropdown_open = False
        self._basin_scroll_offset = 0
        self._build_basin_list()
        self._update_bg_rect()
        self._refresh_texts()
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
        w = min(750, self.sim.screen_width - 40)
        h = min(680, self.sim.screen_height - 60)
        dialog_x = (self.sim.screen_width - w) // 2
        dialog_y = (self.sim.screen_height - h) // 2
        self.bg_rect = pygame.Rect(dialog_x, dialog_y, w, h)

    def _sync_field_positions(self):
        if not self.fields or not self._field_offsets:
            return
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        for i, (off_x, off_y, _, _) in enumerate(self._field_offsets):
            if i < len(self.fields):
                self.fields[i].rect.x = dialog_x + off_x
                self.fields[i].rect.y = dialog_y + off_y

    def rebuild_fields(self):
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        self.fields.clear()
        self._field_offsets.clear()
        for key, val, rect, validator in self._get_fields_config():
            field = InputField(rect, max_length=10, dark=self.dark_mode)
            field.set_text(val)
            field.key = key
            self.fields.append(field)
            self._field_offsets.append((rect[0] - dialog_x, rect[1] - dialog_y, rect[2], rect[3]))

    def _get_fields_config(self):
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        FIELD_W, FIELD_H = 80, 24
        COL_X = dialog_x + 250
        lonlat_val = self.validate_lonlat
        lon_val = self.validate_lon
        lat_val = self.validate_lat
        # 标签起始 Y ≈ dialog_y + 95（与 _draw_tab_* 中的 y = content_top + 5 一致）
        base_y = dialog_y + 95
        y0 = base_y + 0
        y1 = base_y + 30
        y2 = base_y + 60
        y3 = base_y + 90
        y4 = base_y + 120
        y5 = base_y + 150
        if self.tab_index == 0:  # 通用
            return [
                ("mis", f"{self.mis:.1f}", (COL_X, y0, FIELD_W, FIELD_H), self.validate_float),
                ("mas", f"{self.mas:.1f}", (COL_X, y1, FIELD_W, FIELD_H), self.validate_float),
                ("volume", f"{int(self.volume*100)}", (COL_X, y2, FIELD_W, FIELD_H), self.validate_int),
                ("main_rot_speed", f"{self.main_rot_speed:.2f}", (COL_X, y3, FIELD_W, FIELD_H), self.validate_float),
                ("level3_rot_speed", f"{self.level3_rot_speed:.2f}", (COL_X, y4, FIELD_W, FIELD_H), self.validate_float),
            ]
        elif self.tab_index == 1:  # 显示（5 个字段：point/icon/name + screen_h/w）
            return [
                ("point_size", f"{self.point_size}", (COL_X, y0, FIELD_W, FIELD_H), self.validate_int),
                ("icon_size", f"{self.icon_size}", (COL_X, y1, FIELD_W, FIELD_H), self.validate_int),
                ("name_size", f"{self.name_size}", (COL_X, y2, FIELD_W, FIELD_H), self.validate_int),
                ("peak_label_size", f"{self.peak_label_size}", (COL_X, y3, FIELD_W, FIELD_H), self.validate_int),
                ("screen_height", f"{self.screen_height}", (COL_X, y4, FIELD_W, FIELD_H), self.validate_int),
                ("screen_width", f"{self.screen_width}", (COL_X, y5, FIELD_W, FIELD_H), self.validate_int),
            ]
        elif self.tab_index == 2:  # 地图（标签向下偏移 26px 因模式切换）
            y0m = base_y + 26
            y1m = y0m + 30; y2m = y1m + 30; y3m = y2m + 30
            if self._map_range_mode == 0:
                return [
                    ("mlo", lon_to_display(self.mlo), (COL_X, y0m, FIELD_W, FIELD_H), lon_val),
                    ("Mlo", lon_to_display(self.Mlo), (COL_X, y1m, FIELD_W, FIELD_H), lon_val),
                    ("mla", lat_to_display(self.mla), (COL_X, y2m, FIELD_W, FIELD_H), lat_val),
                    ("Mla", lat_to_display(self.Mla), (COL_X, y3m, FIELD_W, FIELD_H), lat_val),
                ]
            else:
                return [
                    ("mlo", lon_to_display(self.mlo), (COL_X, y0m, FIELD_W, FIELD_H), lon_val),
                    ("Mlo", f"{self.Mlo - self.mlo:.1f}", (COL_X, y1m, FIELD_W, FIELD_H), self.validate_float),
                    ("mla", lat_to_display(self.mla), (COL_X, y2m, FIELD_W, FIELD_H), lat_val),
                ]
        elif self.tab_index == 4:  # ACE
            config = []
            if self.ace_limit_mode == ACE_LIMIT_LATLON:
                config = [
                    ("ace_min_lon", lon_to_display(self.ace_min_lon), (COL_X, y1 + 5, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lon", lon_to_display(self.ace_max_lon), (COL_X, y2 + 5, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_min_lat", lat_to_display(self.ace_min_lat), (COL_X, y3 + 5, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lat", lat_to_display(self.ace_max_lat), (COL_X, y4 + 5, FIELD_W, FIELD_H), lonlat_val),
                ]
            return config
        else:
            return []

    @staticmethod
    def validate_float(char: str) -> bool:
        return char == '-' or char.isdigit() or char == '.'

    @staticmethod
    def validate_int(char: str) -> bool:
        return char.isdigit() or char == '-'

    @staticmethod
    def validate_lonlat(char: str) -> bool:
        return char.isdigit() or char in '.-' or char.upper() in 'EWNS'

    @staticmethod
    def validate_lon(char: str) -> bool:
        return char.isdigit() or char in '.-' or char.upper() in 'EW'

    @staticmethod
    def validate_lat(char: str) -> bool:
        return char.isdigit() or char in '.-' or char.upper() in 'NS'

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self._targets.clear()
        if self.dark_mode:
            ov = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            ov.fill(SETTINGS_DARK_OVERLAY)
            surface.blit(ov, (0, 0))
            panel_bg = SETTINGS_DARK_BG
            text_color = SETTINGS_TEXT_LIGHT
        else:
            # 亮色模式
            bg_rect_surf = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            bg_rect_surf.fill((0, 0, 0, 60))
            surface.blit(bg_rect_surf, (0, 0))
            panel_bg = (240, 245, 255, 235)
            text_color = TXT

        dx, dy, dw, dh = self.bg_rect
        panel = pygame.Surface((dw, dh), pygame.SRCALPHA)
        pygame.draw.rect(panel, panel_bg, (0, 0, dw, dh), border_radius=DIALOG_CORNER_RADIUS)
        surface.blit(panel, (dx, dy))

        title = rt(f_m, "设置", text_color)
        surface.blit(title, (dx + 20, dy + 12))

        mx, my = pygame.mouse.get_pos()
        btn_w, btn_h = 60, 26
        close_rect = pygame.Rect(dx + dw - btn_w - 12, dy + 8, btn_w, btn_h)
        self._add_target(close_rect, lambda: self._on_close())
        self._draw_modern_button(surface, close_rect, "关闭", hover=close_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        ok_rect = pygame.Rect(dx + dw - btn_w*2 - 20, dy + 8, btn_w, btn_h)
        self._add_target(ok_rect, lambda: self._on_ok())
        self._draw_modern_button(surface, ok_rect, "确认", hover=ok_rect.collidepoint(mx, my), accent=True, dark=self.dark_mode)

        sc_rect = pygame.Rect(dx + dw - 210, dy + 8, 56, 22)
        self._add_target(sc_rect, lambda: setattr(self, 'show_shortcuts', not self.show_shortcuts))
        self._draw_modern_button(surface, sc_rect, "快捷键", hover=sc_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        rl_rect = pygame.Rect(dx + dw - 148, dy + 8, 70, 22)
        self._add_target(rl_rect, lambda: (self._apply_filter_now(), self.sim.reload_typhoons(), self.sim.save_config()))
        self._draw_modern_button(surface, rl_rect, "重载数据", hover=rl_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        self._shortcuts_btn_rect = sc_rect
        self._reload_btn_rect = rl_rect

        # Tab 导航栏
        tab_y = dy + 40
        tab_area_h = 42
        if self.dark_mode:
            pygame.draw.rect(surface, SETTINGS_TAB_BG, (dx, tab_y, dw, tab_area_h))
        else:
            pygame.draw.rect(surface, (220, 225, 235), (dx, tab_y, dw, tab_area_h))
        tabs = SETTINGS_TAB_NAMES
        n = len(tabs)
        tab_w = (dw - 10) // n
        extra = dw - 10 - tab_w * n
        tab_rects = []
        x_offset = dx + 5
        for i, name in enumerate(tabs):
            w = tab_w + (1 if i < extra else 0)
            tab_rect = pygame.Rect(x_offset, tab_y + 1, w - 2, tab_area_h - 2)
            tab_rects.append(tab_rect)
            active = i == self.tab_index
            color = self._accent if active else (SETTINGS_TEXT_DIM if self.dark_mode else (100, 110, 130))
            if tab_rect.collidepoint(mx, my) and not active:
                color = text_color
            lb = rt(f_m, name, color)
            surface.blit(lb, (tab_rect.x + (tab_rect.w - lb.get_width()) // 2, tab_rect.y + (tab_rect.h - f_m.get_height()) // 2))
            x_offset += w
        self._tab_indicator_x = float(tab_rects[self.tab_index].x + 4)
        ind_w = tab_rects[self.tab_index].w - 8
        ind = pygame.Rect(int(self._tab_indicator_x), tab_y + tab_area_h - 3, ind_w, 3)
        pygame.draw.rect(surface, self._accent, ind, border_radius=2)

        # 内容区域
        content_top = tab_y + tab_area_h + 8
        content_h = dy + dh - content_top - 40
        old_clip = surface.get_clip()
        surface.set_clip(pygame.Rect(dx + 4, content_top, dw - 8, content_h))

        if self.tab_index == 0:
            self._draw_tab_general(surface, dx, dy, content_top, mx, my)
        elif self.tab_index == 1:
            self._draw_tab_display(surface, dx, dy, content_top, mx, my)
        elif self.tab_index == 2:
            self._draw_tab_map(surface, dx, dy, content_top, mx, my)
        elif self.tab_index == 3:
            self._draw_tab_playback(surface, dx, dy, content_top, mx, my)
        elif self.tab_index == 4:
            self._draw_tab_ace(surface, dx, dy, content_top, mx, my)
        else:
            self._draw_tab_data(surface, dx, dy, content_top, mx, my)

        surface.set_clip(old_clip)

        for field in self.fields:
            field.draw(surface)

        # 右下角确认按钮
        confirm_w, confirm_h = 80, 28
        confirm_rect = pygame.Rect(dx + dw - confirm_w - 15, dy + dh - confirm_h - 12, confirm_w, confirm_h)
        self._add_target(confirm_rect, lambda: self._on_ok())
        hover_confirm = confirm_rect.collidepoint(mx, my)
        self._draw_modern_button(surface, confirm_rect, "确认", hover=hover_confirm, accent=True, dark=self.dark_mode)

        if self.show_shortcuts:
            self.draw_shortcuts_help(surface)

    def _draw_modern_button(self, surface, rect, text, hover=False, accent=False, dark=True):
        if accent:
            bg = settings_accent(dark, self.sim.color_scheme)
        elif dark:
            bg = SETTINGS_TOGGLE_ON if hover else SETTINGS_TOGGLE_OFF
        else:
            bg = (100, 150, 200) if hover else (180, 190, 210)
        pygame.draw.rect(surface, bg, rect, border_radius=6)
        if isinstance(text, str):
            text_surface = rt(f_m, text, (255, 255, 255))
        else:
            text_surface = text
        surface.blit(text_surface, (rect.x + (rect.w - text_surface.get_width()) // 2, rect.y + (rect.h - text_surface.get_height()) // 2 - 1))

    def _add_target(self, rect, callback):
        self._targets.append((rect, callback))

    def _on_ok(self):
        self.apply_settings()
        self._needs_save = False
        super().deactivate()
        self.dragging = False
        self._basin_dropdown_open = False

    def _on_close(self):
        super().deactivate()
        self.dragging = False
        self._basin_dropdown_open = False

    def _apply_filter_now(self):
        """即时应用洋区过滤器。"""
        self.sim.basin_filter_enabled = self.basin_filter_enabled
        self.sim.ace_limit_mode = self.ace_limit_mode
        self.sim.ace_limit_basin = self.ace_limit_basin
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.sim._apply_basin_filter()
        self.sim.update_all_screen_points()
        self.sim.save_config(force=True)

    def _cb(self, surface, x, y, checked, attr):
        """绘制复选框并自动记录点击目标。"""
        self._add_target(pygame.Rect(x, y, 16, 16), lambda a=attr: (
            setattr(self, a, not getattr(self, a)),
            setattr(self, '_needs_save', True)
        ))
        self._draw_cb(surface, x, y, checked)

    def _tg(self, surface, rect, label, on, callback):
        """绘制切换按钮并自动记录点击目标。"""
        def _wrap():
            callback()
            self._needs_save = True
        self._add_target(rect, _wrap)
        dark = self.dark_mode
        mouse_x, mouse_y = pygame.mouse.get_pos()
        hover = rect.collidepoint(mouse_x, mouse_y) and not on
        if on:
            bg = self._accent
        elif dark:
            bg = SETTINGS_TOGGLE_ON if hover else SETTINGS_TOGGLE_OFF
        else:
            bg = (160, 175, 200) if hover else (200, 205, 215)
        pygame.draw.rect(surface, bg, rect, border_radius=6)
        if isinstance(label, str):
            text_surface = rt(f_m, label, (255, 255, 255))
        else:
            text_surface = label
        surface.blit(text_surface, (rect.x + (rect.w - text_surface.get_width()) // 2, rect.y + (rect.h - text_surface.get_height()) // 2 - 1))

    def _draw_cb(self, surface, x, y, checked):
        b = pygame.Rect(x, y, 16, 16)
        pygame.draw.rect(surface, SETTINGS_CHECKBOX_BG, b, border_radius=3)
        pygame.draw.rect(surface, SETTINGS_INPUT_BORDER, b, 1, border_radius=3)
        if checked:
            inner = pygame.Rect(x + 3, y + 3, 10, 10)
            pygame.draw.rect(surface, SETTINGS_CHECKBOX_CHECK, inner, border_radius=2)

    def _draw_toggle_btns(self, surface, x, y, labels, active_idx):
        btn_w = 90
        for i, label in enumerate(labels):
            rx = x + i * (btn_w + 4)
            b = pygame.Rect(rx, y, btn_w, 22)
            on = i == active_idx
            bg = self._accent if on else SETTINGS_TOGGLE_OFF
            pygame.draw.rect(surface, bg, b, border_radius=6)
            lb = rt(f_m, label, (255, 255, 255))
            surface.blit(lb, (b.x + (b.w - lb.get_width()) // 2, b.y + (b.h - lb.get_height()) // 2 - 1))
            yield b

    # ── Tab 内容 ──

    def _draw_tab_general(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        surface.blit(self.mis_label, (dx + 30, y + self._lh(self.mis_label, 24)))
        surface.blit(self.mas_label, (dx + 30, y + 30 + self._lh(self.mas_label, 24)))
        surface.blit(self.volume_label, (dx + 30, y + 60 + self._lh(self.volume_label, 24)))
        surface.blit(self.main_rot_label, (dx + 30, y + 90 + self._lh(self.main_rot_label, 24)))
        surface.blit(self.level3_rot_label, (dx + 30, y + 120 + self._lh(self.level3_rot_label, 24)))

    def _draw_tab_display(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        surface.blit(self.point_size_text, (dx + 30, y + self._lh(self.point_size_text, 24)))
        surface.blit(self.icon_size_text, (dx + 30, y + 30 + self._lh(self.icon_size_text, 24)))
        surface.blit(self.name_size_text, (dx + 30, y + 60 + self._lh(self.name_size_text, 24)))
        surface.blit(self.peak_label_text, (dx + 30, y + 90 + self._lh(self.peak_label_text, 24)))
        surface.blit(self.sh_label, (dx + 30, y + 120 + self._lh(self.sh_label, 24)))
        surface.blit(self.sw_label, (dx + 30, y + 150 + self._lh(self.sw_label, 24)))
        surface.blit(self.fix_icon_point_text, (dx + 30, y + 190 + self._lh(self.fix_icon_point_text, 16)))
        self._cb(surface, dx + 280, y + 190, self.fix_icon_point_size, 'fix_icon_point_size')
        dm_text = rt(f_m, "主题配色:", SETTINGS_TEXT_LIGHT if self.dark_mode else TXT)
        surface.blit(dm_text, (dx + 30, y + 217 + self._lh(dm_text, 22)))
        for i, (lbl, is_dark) in enumerate([("暗色", True), ("亮色", False)]):
            rect = pygame.Rect(dx + 180 + i * 80, y + 217, 72, 22)
            self._tg(surface, rect, rt(f_m, lbl, (255, 255, 255)), self.dark_mode == is_dark,
                     lambda d=is_dark: (setattr(self.sim, 'dark_mode', d), self._refresh_texts()))
        surface.blit(self.color_scheme_text, (dx + 30, y + 247 + self._lh(self.color_scheme_text, 22)))
        for i, lbl in enumerate([rt(f_m, "高对比度", (255, 255, 255)), rt(f_m, "旧版", (255, 255, 255))]):
            rect = pygame.Rect(dx + 180 + i * 108, y + 247, 100, 22)
            self._tg(surface, rect, lbl, self.color_scheme == i + 1, lambda s=i+1: setattr(self, 'color_scheme', s))

    def _draw_tab_map(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        # 范围模式切换
        for i, lbl in enumerate(self.map_range_modes):
            rect = pygame.Rect(dx + 30 + i * 110, y - 1, 100, 22)
            self._tg(surface, rect, lbl, self._map_range_mode == i, lambda m=i: (
                setattr(self, '_map_range_mode', m),
                self.rebuild_fields(),
                setattr(self, '_needs_save', True)))
        y += 26
        if self._map_range_mode == 0:
            surface.blit(self.mlo_label, (dx + 30, y + self._lh(self.mlo_label, 24)))
            surface.blit(self.Mlo_label, (dx + 30, y + 30 + self._lh(self.Mlo_label, 24)))
            surface.blit(self.mla_label, (dx + 30, y + 60 + self._lh(self.mla_label, 24)))
            surface.blit(self.Mla_label, (dx + 30, y + 90 + self._lh(self.Mla_label, 24)))
            surface.blit(self.lonlat_note1, (dx + 30, y + 127))
            surface.blit(self.lonlat_note2, (dx + 30, y + 145))
        else:
            surface.blit(self.bl_lon_label, (dx + 30, y + self._lh(self.bl_lon_label, 24)))
            surface.blit(self.bl_lat_label, (dx + 30, y + 30 + self._lh(self.bl_lat_label, 24)))
            surface.blit(self.span_lon_label, (dx + 30, y + 60 + self._lh(self.span_lon_label, 24)))

    def _draw_tab_playback(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        gap = 30
        surface.blit(self.hemisphere_label, (dx + 30, y - 1 + self._lh(self.hemisphere_label, 22)))
        for i, mode in enumerate(self.hemisphere_modes):
            rect = pygame.Rect(dx + 180 + i * 100, y - 1, 80, 22)
            h_val = HEMISPHERE_NORTH if i == 0 else HEMISPHERE_SOUTH
            def _mk_cb(new_hemi):
                def _cb():
                    changed = (new_hemi != self.sim.hemisphere)
                    setattr(self, 'hemisphere', new_hemi)
                    if changed:
                        setattr(self, '_ace_changed', True)
                        setattr(self, '_hemisphere_changed', True)
                return _cb
            self._tg(surface, rect, mode, self.hemisphere == h_val, _mk_cb(h_val))

        for idx, (attr, y_off) in enumerate([
            ('disable_dpi_scaling', gap), ('ac', gap*2),
            ('show_info_box_normal', gap*3), ('show_info_box_season', gap*4),
            ('fade_typhoon', gap*5),
        ]):
            surface.blit(getattr(self, {'disable_dpi_scaling': 'dpi_label', 'ac': 'auto_continue_text',
                'show_info_box_normal': 'normal_info_text', 'show_info_box_season': 'season_info_text',
                'fade_typhoon': 'fade_typhoon_text'}[attr]), (dx + 30, y + y_off))
            self._cb(surface, dx + self.bg_rect.width - 50, y + y_off, getattr(self, attr), attr)
        for attr, y_off in [('smooth_path', gap*7+10), ('ace_interpolated', gap*8+10+22), ('show_fps', gap*9+10+22)]:
            surface.blit(getattr(self, {'smooth_path':'smooth_path_text','ace_interpolated':'ace_interp_text','show_fps':'fps_text'}[attr]), (dx + 30, y + y_off))
            self._cb(surface, dx + self.bg_rect.width - 50, y + y_off, getattr(self, attr), attr)

        # 路径消失模式
        fpm_y = y + gap * 6
        surface.blit(self.fade_path_mode_text, (dx + 30, fpm_y - 1 + self._lh(self.fade_path_mode_text, 22)))
        for i, (lbl, v) in enumerate(zip(self.fade_path_mode_modes, ("never", "fade", "quick"))):
            rect = pygame.Rect(dx + 180 + i * 60, fpm_y - 1, 52, 22)
            self._tg(surface, rect, lbl, self.fade_path_mode == v, lambda m=v: setattr(self, 'fade_path_mode', m))

        # 月度总结开关
        ms_y = y + gap * 6 + 28
        surface.blit(self.monthly_summary_text, (dx + 30, ms_y))
        self._cb(surface, dx + self.bg_rect.width - 50, ms_y, self.monthly_summary, 'monthly_summary')

        # 插值模式选择
        spm_y = y + gap * 7 + 32
        surface.blit(self.smooth_path_mode_text, (dx + 30, spm_y - 1 + self._lh(self.smooth_path_mode_text, 22)))
        for i, lbl in enumerate(self.smooth_path_mode_modes):
            rect = pygame.Rect(dx + 180 + i * 90, spm_y - 1, 80, 22)
            v = "monotone" if i == 0 else "catmull"
            self._tg(surface, rect, lbl, self.smooth_path_mode == v, lambda m=v: setattr(self, 'smooth_path_mode', m))

        # 路径模式切换
        pm_y = y + gap * 10 + 15 + 22
        surface.blit(self.path_mode_text, (dx + 30, pm_y - 1 + self._lh(self.path_mode_text, 22)))
        for i, lbl in enumerate(self.path_mode_modes):
            rect = pygame.Rect(dx + 180 + i * 90, pm_y - 1, 80, 22)
            v = "markers" if i == 0 else "line"
            self._tg(surface, rect, lbl, self.path_mode == v, lambda m=v: setattr(self, 'path_mode', m))

        # ERI 设置
        eri_y = y + gap * 11 + 20 + 22
        surface.blit(self.show_ri_text, (dx + 30, eri_y))
        self._cb(surface, dx + self.bg_rect.width - 50, eri_y, self.show_ri_effect, 'show_ri_effect')

        # 未经过路径
        fp_y = y + gap * 12 + 25 + 22
        surface.blit(self.future_path_text, (dx + 30, fp_y))
        self._cb(surface, dx + self.bg_rect.width - 50, fp_y, self.show_future_path, 'show_future_path')

        # 帧率上限
        fc_y = y + gap * 13 + 30 + 22
        surface.blit(self.fps_cap_text, (dx + 30, fc_y - 1 + self._lh(self.fps_cap_text, 22)))
        for i, (lbl, v) in enumerate(zip(self.fps_cap_modes, (60, 120, 0))):
            rect = pygame.Rect(dx + 180 + i * 90, fc_y - 1, 80, 22)
            self._tg(surface, rect, lbl, self.fps_cap == v, lambda m=v: setattr(self, 'fps_cap', m))

    def _draw_tab_ace(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        surface.blit(self.ace_limit_label, (dx + 30, y - 1 + self._lh(self.ace_limit_label, 22)))
        modes = [ACE_LIMIT_NONE, ACE_LIMIT_LATLON, ACE_LIMIT_BASIN]
        texts = [self.ace_limit_none_text, self.ace_limit_latlon_text, self.ace_limit_basin_text]
        for i, (mode, txt) in enumerate(zip(modes, texts)):
            rect = pygame.Rect(dx + 180 + i * 105, y - 1, 95, 22)
            self._tg(surface, rect, txt, mode == self.ace_limit_mode, lambda m=mode: (
                setattr(self, 'ace_limit_mode', m),
                setattr(self, '_ace_changed', True),
                self._apply_filter_now(),
                self.rebuild_fields()))
        if self.ace_limit_mode == ACE_LIMIT_LATLON:
            # 经纬度输入标签
            for i, lbl in enumerate([self.mlo_label, self.Mlo_label, self.mla_label, self.Mla_label]):
                surface.blit(lbl, (dx + 30, y + 35 + i * 30 + self._lh(lbl, 24)))
        if self.ace_limit_mode == ACE_LIMIT_BASIN:
            surface.blit(self.basin_filter_text, (dx + 30, y + 30))
            # 盆地过滤 checkbox：立即生效
            cbx = dx + self.bg_rect.width - 50
            self._add_target(pygame.Rect(cbx, y + 30, 16, 16), lambda: (
                setattr(self, 'basin_filter_enabled', not self.basin_filter_enabled),
                setattr(self, '_needs_save', True),
                self._apply_filter_now()
            ))
            self._draw_cb(surface, cbx, y + 30, self.basin_filter_enabled)
            surface.blit(self.basin_filter_note, (dx + 30, y + 50))
            basin_rect = pygame.Rect(dx + 180, y + 75, 220, 24)
            current_basin = self.ace_limit_basin
            area = self.sim.res_mgr.ocean_areas.get_by_code(current_basin) if current_basin else None
            display = area.name_cn if area else (current_basin or "选择洋区")
            self._add_target(basin_rect, lambda: (setattr(self, '_basin_dropdown_open', True), setattr(self, '_basin_scroll_offset', 0)))
            dark = self.dark_mode
            bg = SETTINGS_TOGGLE_ON if dark else (200, 205, 215)
            text_color = SETTINGS_TEXT_LIGHT if dark else (60, 70, 90)
            pygame.draw.rect(surface, bg, basin_rect, border_radius=6)
            text_surface = rt(f_s, display, text_color)
            surface.blit(text_surface, (basin_rect.x + 5, basin_rect.y + (basin_rect.h - text_surface.get_height()) // 2))
            # 下拉列表
            if self._basin_dropdown_open:
                ITEM_H = 24
                max_vis = 8
                list_h = min(len(self._basin_list), max_vis) * ITEM_H
                list_rect = pygame.Rect(basin_rect.x, basin_rect.bottom, basin_rect.width, list_h)
                bg_c = SETTINGS_TAB_ACTIVE if dark else (255, 255, 255)
                pygame.draw.rect(surface, bg_c, list_rect, 0, 3)
                pygame.draw.rect(surface, settings_accent(dark, self.sim.color_scheme) if dark else (70, 130, 180), list_rect, 1, 3)
                total = len(self._basin_list)
                scroll_offset = max(0, min(self._basin_scroll_offset, total - max_vis))
                for i in range(scroll_offset, min(scroll_offset + max_vis, total)):
                    code, name_cn = self._basin_list[i]
                    iy = list_rect.y + (i - scroll_offset) * ITEM_H
                    item_rect = pygame.Rect(list_rect.x, iy, list_rect.width, ITEM_H)
                    if item_rect.collidepoint(mx, my):
                        hover_c = SETTINGS_ACCENT if dark else (180, 220, 255)
                        pygame.draw.rect(surface, hover_c, item_rect)
                    self._add_target(item_rect, lambda cd=code: (
                        setattr(self, 'ace_limit_basin', cd),
                        setattr(self, '_ace_changed', True),
                        setattr(self, '_needs_save', True),
                        setattr(self, '_basin_dropdown_open', False),
                        self._apply_filter_now()))
                    it = rt(f_s, f"{code} {name_cn}", SETTINGS_TEXT_LIGHT if dark else TXT)
                    surface.blit(it, (item_rect.x + 5, item_rect.y + 3))

        surface.blit(self.ace_show_label, (dx + 30, y + 130 + self._lh(self.ace_show_label, 16)))
        self._cb(surface, dx + 280, y + 130, self.show_ace_bar, 'show_ace_bar')
        ace_total_enabled = self.show_ace_bar
        surface.blit(self.ace_total_label, (dx + 30, y + 155 + self._lh(self.ace_total_label, 16)))
        self._add_target(pygame.Rect(dx + 280, y + 155, 16, 16), lambda: (
            setattr(self, 'show_ace_total', not self.show_ace_total),
            setattr(self, '_needs_save', True)
        ) if ace_total_enabled else None)
        self._draw_cb(surface, dx + 280, y + 155, self.show_ace_total and ace_total_enabled)
        if not ace_total_enabled:
            gray = pygame.Surface((16, 16), pygame.SRCALPHA)
            gray.fill((128, 128, 128, 160))
            surface.blit(gray, (dx + 280, y + 155))

    def _draw_tab_data(self, surface, dx, dy, top_y, mx, my):
        y = top_y + 5
        surface.blit(self.icon_set_text, (dx + 30, y - 1 + self._lh(self.icon_set_text, 22)))
        for i, mode in enumerate(self.icon_set_modes):
            rect = pygame.Rect(dx + 180 + i * 120, y - 1, 110, 22)
            s = ICON_SET_SIMPLE if i == 0 else ICON_SET_SMCY
            self._tg(surface, rect, mode, self.icon_set == s, lambda s2=s: setattr(self, 'icon_set', s2))
        surface.blit(self.icon_set_warn, (dx + 30, y + 27))

        surface.blit(self.name_mode_text, (dx + 30, y + 57 + self._lh(self.name_mode_text, 22)))
        for i, mode in enumerate(self.name_modes):
            rect = pygame.Rect(dx + 180 + i * 120, y + 57, 110, 22)
            self._tg(surface, rect, mode, self.name_display_mode == i, lambda idx=i: setattr(self, 'name_display_mode', idx))

        surface.blit(self.point_name_text, (dx + 30, y + 87 + self._lh(self.point_name_text, 16)))
        self._cb(surface, dx + 280, y + 87, self.point_name_mode, 'point_name_mode')

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
            mouse_x, mouse_y = pygame.mouse.get_pos()
            if sr.collidepoint(mouse_x, mouse_y) and self._shortcuts_max_scroll > 0:
                self._shortcuts_scroll_y = max(
                    0, min(self._shortcuts_scroll_y - e.y * 32,
                           self._shortcuts_max_scroll))
            return True

        # ── 鼠标按下 ──
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            mouse_x, mouse_y = e.pos

            # 点击面板外 → 关闭
            if not sr.collidepoint(mouse_x, mouse_y):
                self._close_shortcuts()
                return True

            lx, ly = mouse_x - sr.x, mouse_y - sr.y  # 面板内局部坐标

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
                mouse_x, mouse_y = e.pos
                ly = mouse_y - sr.y
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

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        # ── 快捷键面板：拦截所有事件 ──
        if self.show_shortcuts:
            return self._handle_shortcuts_event(e)

        # 洋区下拉框: ESC / 滚轮 单独处理
        if self._basin_dropdown_open:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                self._basin_dropdown_open = False
                return True
            if e.type == pygame.MOUSEWHEEL:
                self._basin_scroll_offset -= e.y
                self._basin_scroll_offset = max(0, min(self._basin_scroll_offset, len(self._basin_list) - 8))
                return True

        # 标题栏按钮（快捷键/重载数据）优先于拖拽检测
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self._shortcuts_btn_rect.collidepoint(x, y):
                self._basin_dropdown_open = False
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
                self._needs_save = True
                self.deactivate()
                self.sim.reload_typhoons()
                return True
            # 标题栏按钮（关闭/确认）优先于拖拽
            for rect, callback in self._targets:
                if rect.collidepoint(x, y):
                    callback()
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
                    field.activate_at(e.pos[0])
                    self.current_field = i
                    return True
        for i, field in enumerate(self.fields):
            if field.handle_event(e):
                return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._on_close()
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
            x, y = e.pos
            # Tab 栏点击（保留独立处理，需要 rebuild_fields）
            dialog_x, dialog_y, dialog_w, dialog_h = self.bg_rect
            tab_y = dialog_y + 40
            n_tabs = len(SETTINGS_TAB_NAMES)
            tab_w = (dialog_w - 10) // n_tabs
            extra_tabs = dialog_w - 10 - tab_w * n_tabs
            x_off = dialog_x + 5
            for i in range(n_tabs):
                w = tab_w + (1 if i < extra_tabs else 0)
                tab_rect = pygame.Rect(x_off, tab_y, w - 2, 40)
                if tab_rect.collidepoint(x, y):
                    self._basin_dropdown_open = False
                    self.tab_index = i
                    self._tab_indicator_target = tab_rect.x + 4
                    self.rebuild_fields()
                    return True
                x_off += w
            # 统一派发其他点击目标
            for rect, callback in self._targets:
                if rect.collidepoint(x, y):
                    callback()
                    return True
            # 洋区下拉框打开时，点击空白处关闭
            if self._basin_dropdown_open:
                self._basin_dropdown_open = False
                return True
        if self.handle_drag_event(e):
            return True
        return False

    def _sync_ace_settings_to_sim(self):
        self.sim.ace_limit_mode = self.ace_limit_mode
        self.sim.ace_limit_basin = self.ace_limit_basin
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.sim.ace_min_lon = self.ace_min_lon
        self.sim.ace_max_lon = self.ace_max_lon
        self.sim.ace_min_lat = self.ace_min_lat
        self.sim.ace_max_lat = self.ace_max_lat
        self.sim.hemisphere = self.hemisphere
        self.sim.basin_filter_enabled = self.basin_filter_enabled

    def apply_settings(self):
        # 先验证所有字段再批量应用，避免部分失败导致状态不一致
        validated = {}
        for field in self.fields:
            key = field.key
            val = field.get_text().strip()
            try:
                if key == 'Mlo' and self._map_range_mode == 1:
                    validated[key] = float(val)
                elif self._is_lon_key(key):
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
                elif key in ('screen_width', 'screen_height', 'point_size', 'icon_size',
                             'name_size', 'peak_label_size'):
                    validated[key] = int(val)
                elif key in ('mis', 'mas', 'main_rot_speed', 'level3_rot_speed'):
                    validated[key] = float(val)
                elif key == 'volume':
                    validated[key] = int(val) / 100.0
            except ValueError:
                self.sim.show_error(f"无效数字: {val}")
                return

        # 批量赋值到 self（settings 本地）
        for key, value in validated.items():
            setattr(self, key, value)

        # 角点+大小模式：跨度转换为最大经度
        if self._map_range_mode == 1 and 'Mlo' in validated:
            self.Mlo = self.mlo + validated['Mlo']

        self.mis = max(0.1, self.mis)
        self.mas = min(20.0, self.mas)
        self.volume = max(0.0, min(1.0, self.volume))

        if self.mis > self.mas:
            self.sim.show_error("最小速度不能大于最大速度")
            return
        self.mas = max(self.mas, self.mis + 0.1)

        # 同步到 sim（先记录视图相关旧值，仅变化时重建视图）
        old_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                           self.sim.screen_width, self.sim.screen_height)
        old_smooth = self.sim.smooth_path
        old_smooth_mode = getattr(self.sim, 'smooth_path_mode', 'monotone')
        self.sim.ac = self.ac
        self.sim.mis = self.mis
        self.sim.mas = self.mas
        self.sim.mlo = self.mlo
        self.sim.Mlo = self.Mlo
        self.sim.mla = self.mla
        self.sim.Mla = self.Mla
        self.sim.map_corner_mode = (self._map_range_mode == 1)
        self.sim.show_info_box_normal = self.show_info_box_normal
        self.sim.show_info_box_season = self.show_info_box_season
        self.sim.screen_width = self.screen_width
        self.sim.screen_height = self.screen_height
        self.sim.ace_display_mode = self.ace_display_mode
        self.sim.main_rotation_speed = self.main_rot_speed
        self.sim.level3_rotation_speed = self.level3_rot_speed
        self.sim.volume = self.volume
        self.sim.name_display_mode = self.name_display_mode
        self.sim.point_name_mode = self.point_name_mode
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
        self.sim.name_size = self.name_size
        self.sim.peak_label_size = self.peak_label_size
        self.sim.fix_icon_point_size = self.fix_icon_point_size
        self.sim.disable_dpi_scaling = self.disable_dpi_scaling
        self.sim.fade_typhoon = self.fade_typhoon
        self.sim.fade_path = self.fade_path
        self.sim.fade_path_mode = self.fade_path_mode
        self.sim.smooth_path = self.smooth_path
        self.sim.smooth_path_mode = self.smooth_path_mode
        self.sim.path_mode = self.path_mode
        self.sim.ace_interpolated = self.ace_interpolated
        self.sim.show_fps = self.show_fps
        self.sim.fps_cap = self.fps_cap
        self.sim.show_ri_effect = self.show_ri_effect
        if getattr(self.sim, 'show_future_path', True) != self.show_future_path:
            self.sim.show_future_path = self.show_future_path
            self.sim._invalidate_all_path_caches()
        else:
            self.sim.show_future_path = self.show_future_path
        self.sim.monthly_summary = self.monthly_summary
        self.sim.icon_set = self.icon_set
        self.sim.color_scheme = self.color_scheme
        self.sim.show_ace_bar = self.show_ace_bar
        self.sim.show_ace_total = self.show_ace_total
        if old_smooth != self.smooth_path or old_smooth_mode != self.smooth_path_mode:
            self.sim.update_all_screen_points()

        self.sim.basin_filter_enabled = self.basin_filter_enabled
        self.sim._apply_basin_filter()

        if self._ace_changed:
            self.sim.recalc_all_ace()
            self._ace_changed = False

        if self._hemisphere_changed:
            self._hemisphere_changed = False
            if self.sim.md == self.sim.MODE_SEASON:
                sty = self.sim.sty
                h = self.hemisphere
                dt = datetime(sty, 7, 1, 0) if h == HEMISPHERE_SOUTH else datetime(sty, 1, 1, 0)
                self.sim.season_ctrl.jump_to(dt)
                self.sim._sync_season_state()

        new_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                           self.sim.screen_width, self.sim.screen_height)
        if old_view_bounds != new_view_bounds:
            self.sim.map_mgr.update_view()

        self.sim.update_all_screen_points()
        self.sim._config_needs_save = True
        self.sim.save_config()
