# py/settings.py
"""设置对话框（全屏暗色面板 + 顶部 Tab 导航）。"""
from __future__ import annotations

import re
from datetime import datetime
import pygame
from .constants import (
    f_s, f_m, rt, TXT,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
    BUTTON_BORDER,
    SETTINGS_DARK_BG, SETTINGS_DARK_OVERLAY,
    settings_accent,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_TAB_BG, SETTINGS_TAB_ACTIVE, SETTINGS_TAB_HOVER,
    SETTINGS_ACCENT,
    SETTINGS_INPUT_BORDER,
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
    # ── 布局表驱动(网格化排版,F3)──
    LAYOUT_ROW_H = 30
    LAYOUT_LABEL_X = 30
    LAYOUT_LABEL_W = 210
    LAYOUT_CTRL_X = 250
    LAYOUT_CHECK_X = 280
    LAYOUT_BTN_X = 180
    LAYOUT_BTN_H = 22
    LAYOUT_FIELD_W = 80
    LAYOUT_FIELD_H = 24
    LAYOUT_SECTION_GAP = 18

    # 字段 key → 预渲染标签属性
    _FIELD_LABELS = {
        'mis': 'mis_label', 'mas': 'mas_label', 'volume': 'volume_label',
        'main_rot_speed': 'main_rot_label', 'level3_rot_speed': 'level3_rot_label',
        'point_size': 'point_size_text', 'icon_size': 'icon_size_text',
        'name_size': 'name_size_text', 'peak_label_size': 'peak_label_text',
        'info_box_scale': 'info_box_scale_text',
        'screen_height': 'sh_label', 'screen_width': 'sw_label',
        'mlo': 'mlo_label', 'Mlo': 'Mlo_label', 'mla': 'mla_label', 'Mla': 'Mla_label',
        'bl_lon': 'bl_lon_label', 'bl_lat': 'bl_lat_label', 'span': 'span_lon_label',
        'ace_min_lon': 'mlo_label', 'ace_max_lon': 'Mlo_label',
        'ace_min_lat': 'mla_label', 'ace_max_lat': 'Mla_label',
        'smooth_path_segments': 'smooth_segments_text',
    }
    # checkbox attr → 预渲染标签属性
    _CHECK_LABELS = {
        'disable_dpi_scaling': 'dpi_label', 'ac': 'auto_continue_text',
        'show_info_box_normal': 'normal_info_text', 'show_info_box_season': 'season_info_text',
        'fade_typhoon': 'fade_typhoon_text', 'smooth_path': 'smooth_path_text',
        'ace_interpolated': 'ace_interp_text', 'show_fps': 'fps_text',
        'monthly_summary': 'monthly_summary_text', 'fade_path': 'fade_path_text',
        'show_ri_effect': 'show_ri_text', 'show_future_path': 'future_path_text',
        'fix_icon_point_size': 'fix_icon_point_text',
        'show_ace_bar': 'ace_show_label', 'show_ace_total': 'ace_total_label',
        'point_name_mode': 'point_name_text', 'basin_filter_enabled': 'basin_filter_text',
        'show_edit_point_labels': 'edit_point_labels_text',
        'landfall_vol_scale': 'landfall_vol_text',
        'show_legend': 'legend_text', 'show_graticule': 'graticule_text',
        'show_ocean_areas': 'ocean_areas_text', 'show_coord_hud': 'coord_hud_text',
        'show_track_label': 'track_label_text',
    }

    def __init__(self, s):
        super().__init__(s)
        self._init_data()
        self.tab_index = 0
        self.fields: List[InputField] = []
        self.show_shortcuts = False
        self.show_info_box_editor = False
        self._ib_editor_rects = []      # [(rect, kind, key)] 信息框编辑器点击区
        self._needs_save = False
        self._field_offsets: List[Tuple[int, int, int, int]] = []
        self._pre_render_texts()
        self._ace_changed = False
        self._ace_recalc_dirty = False
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
        # 下拉列表项单独放: 它视觉上盖住下方按钮但注册顺序更晚, 必须优先派发,
        # 否则点击下拉项底部会误触「洋区编辑」按钮
        self._dropdown_targets: list = []
        self._shortcuts_scroll_y = 0
        self._shortcuts_scrollbar_dragging = False
        self._shortcuts_scrollbar_drag_start_y = 0
        self._shortcuts_scroll_start_y = 0
        self._shortcuts_max_scroll = 0
        self._shortcuts_cache_key = None
        self._shortcuts_cache = None
        self._error_fields = []
        self._content_scroll_y = 0
        self._content_scroll_max = 0
        self._close_confirm = False
        self._sb_track_rect = None
        self._sb_thumb_rect = None
        self._sb_dragging = False
        self._sb_grab_off = 0
        self._tab_static_cache = {}
        self._tab_rows_y_cache = {}
        self._restore_confirm = False

    # ── 布局表(F3)──

    def _tab_layout(self):
        """当前 (tab_index, 模式) 的行描述列表。
        行类型: section/field/checkbox/checkbox_now/toggle/note/dropdown。"""
        L = self.LAYOUT_ROW_H
        if self.tab_index == 0:  # 通用
            return [
                ('section', '速度'), ('field', 'mis'), ('field', 'mas'),
                ('section', '音量'), ('field', 'volume'),
                ('checkbox', 'landfall_vol_scale'),
                ('section', '旋转'), ('field', 'main_rot_speed'), ('field', 'level3_rot_speed'),
                ('section', '窗口'), ('field', 'screen_width'), ('field', 'screen_height'),
                ('note', 'window_note'),
            ]
        if self.tab_index == 1:  # 显示
            return [
                ('section', '大小'), ('field', 'point_size'), ('field', 'icon_size'),
                ('field', 'name_size'), ('field', 'peak_label_size'), ('field', 'info_box_scale'),
                ('section', '主题'),
                ('toggle', 'dark_mode', [('暗色', True), ('亮色', False)], 90, self._cb_dark_mode, 'dm_label'),
                ('toggle', 'color_scheme', [('高对比度', 1), ('旧版', 2)], 90, None, 'color_scheme_text'),
                ('checkbox', 'fix_icon_point_size'),
                ('checkbox', 'show_legend'),
                ('checkbox', 'show_graticule'),
                ('checkbox', 'show_coord_hud'),
                ('checkbox', 'show_track_label'),
                ('button', '信息框自定义'),
                ('note', 'theme_note'),
            ]
        if self.tab_index == 2:  # 地图
            if self._map_range_mode == 0:
                return [
                    ('section', '范围模式'),
                    ('toggle', '_map_range_mode', [(s, v) for s, v in
                                                   zip([x for x in self.map_range_modes], (0, 1))],
                     100, self._cb_map_range, None),
                    ('section', '经纬范围'),
                    ('field', 'mlo'), ('field', 'Mlo'), ('field', 'mla'), ('field', 'Mla'),
                    ('note', 'lonlat_note1'), ('note', 'lonlat_note2'),
                    ('section', '叠加'),
                    ('checkbox', 'show_ocean_areas'),
                ]
            return [
                ('section', '范围模式'),
                ('toggle', '_map_range_mode', [(s, v) for s, v in
                                               zip([x for x in self.map_range_modes], (0, 1))],
                 100, self._cb_map_range, None),
                ('section', '角点+大小'),
                ('field', 'bl_lon'), ('field', 'bl_lat'), ('field', 'span'),
                ('section', '叠加'),
                ('checkbox', 'show_ocean_areas'),
            ]
        if self.tab_index == 3:  # 播放
            return [
                ('section', '半球'),
                ('toggle', 'hemisphere', [(s, v) for s, v in zip(self.hemisphere_modes,
                                                                 (HEMISPHERE_NORTH, HEMISPHERE_SOUTH))],
                 90, self._cb_hemisphere, 'hemisphere_label'),
                ('section', '时间行为'),
                ('checkbox', 'ac'),
                ('toggle', 'normal_other_display',
                 [(rt(f_m, "半透明", (255, 255, 255)), "translucent"),
                  (rt(f_m, "不透明", (255, 255, 255)), "opaque"),
                  (rt(f_m, "不显示", (255, 255, 255)), "hidden")],
                 90, None, 'normal_other_text'),
                ('checkbox', 'show_info_box_normal'),
                ('checkbox', 'show_info_box_season'), ('checkbox', 'monthly_summary'),
                ('checkbox', 'ace_interpolated'), ('checkbox', 'disable_dpi_scaling'),
                ('section', '消失'),
                ('checkbox', 'fade_typhoon'), ('checkbox', 'fade_path'),
                ('toggle', 'fade_path_mode', [(s, v) for s, v in
                                              zip(self.fade_path_mode_modes, ("never", "fade", "quick"))],
                 90, None, 'fade_path_mode_text'),
                ('section', '平滑'),
                ('checkbox', 'smooth_path'),
                ('toggle', 'smooth_path_mode', [(s, v) for s, v in
                                                zip(self.smooth_path_mode_modes, ("monotone", "catmull"))],
                 90, None, 'smooth_path_mode_text'),
                ('field', 'smooth_path_segments'),
                ('section', '编辑'),
                ('toggle', 'edit_snap_step', [(s, v) for s, v in
                                              zip(self.edit_snap_step_modes, (0.0, 0.1, 0.5, 1.0))],
                 60, None, 'edit_snap_step_text'),
                ('checkbox', 'show_edit_point_labels'),
                ('section', '路径'),
                ('toggle', 'path_mode', [(s, v) for s, v in zip(self.path_mode_modes, ("markers", "line"))],
                 90, None, 'path_mode_text'),
                ('checkbox', 'show_future_path'),
                ('section', '效果'),
                ('checkbox', 'show_ri_effect'),
                ('section', '帧率'),
                ('checkbox', 'show_fps'),
                ('toggle', 'fps_cap', [(s, v) for s, v in zip(self.fps_cap_modes, (60, 120, 0))],
                 90, None, 'fps_cap_text'),
            ]
        if self.tab_index == 4:  # ACE
            rows = [
                ('section', '限制'),
                ('toggle', 'ace_limit_mode', [(s, v) for s, v in
                                              zip((self.ace_limit_none_text, self.ace_limit_latlon_text,
                                                   self.ace_limit_basin_text),
                                                  (ACE_LIMIT_NONE, ACE_LIMIT_LATLON, ACE_LIMIT_BASIN))],
                 100, self._cb_ace_limit, 'ace_limit_label'),
            ]
            if self.ace_limit_mode == ACE_LIMIT_LATLON:
                rows += [('section', '范围'),
                         ('field', 'ace_min_lon'), ('field', 'ace_max_lon'),
                         ('field', 'ace_min_lat'), ('field', 'ace_max_lat')]
            elif self.ace_limit_mode == ACE_LIMIT_BASIN:
                rows += [('checkbox_now', 'basin_filter_enabled'),
                         ('dropdown',)]
            rows += [('section', '显示'),
                     ('checkbox', 'show_ace_bar'), ('checkbox', 'show_ace_total'),
                     ('section', '编辑'),
                     ('btn_ocean_edit',)]
            return rows
        # 数据
        return [
            ('section', '图标'),
            ('toggle', 'icon_set', [(s, v) for s, v in
                                    zip(self.icon_set_modes, (ICON_SET_SIMPLE, ICON_SET_SMCY))],
             110, None, 'icon_set_text'),
            ('note', 'icon_set_warn'),
            ('section', '名称'),
            ('toggle', 'name_display_mode', [(s, v) for s, v in zip(self.name_modes, (0, 1, 2))],
             110, None, 'name_mode_text'),
            ('checkbox', 'point_name_mode'),
        ]

    def _tab_rows_y(self):
        """行 Y 偏移(相对 content_top)与内容总高。按 (tab, 模式变体) 缓存。"""
        layout = self._tab_layout()
        variant = self._tab_variant()
        key = (self.tab_index, variant)
        hit = self._tab_rows_y_cache.get(key)
        if hit is not None:
            return hit
        ys = []
        y = 5
        for row in layout:
            ys.append(y)
            y += self.LAYOUT_ROW_H
            if row[0] == 'section':
                y += self.LAYOUT_SECTION_GAP
        total_h = y + 5
        self._tab_rows_y_cache[key] = (ys, total_h)
        return ys, total_h

    def _tab_variant(self):
        if self.tab_index == 2:
            return self._map_range_mode
        if self.tab_index == 4:
            return self.ace_limit_mode
        return 0

    def _cb_map_range(self, m):
        if m == self._map_range_mode:
            return
        # 先按旧模式解析当前字段文本,再切换模式重建;
        # 否则 span 文本会被新模式当绝对经度解析,错误覆盖 Mlo
        self._flush_fields()
        self._map_range_mode = m
        self.rebuild_fields(flush=False)
        self._needs_save = True

    def _cb_hemisphere(self, new_hemi):
        changed = (new_hemi != self.sim.hemisphere)
        self.hemisphere = new_hemi
        if changed:
            self._ace_changed = True
            self._ace_recalc_dirty = True
            self._hemisphere_changed = True

    def _cb_ace_limit(self, m):
        if m == self.ace_limit_mode:
            return
        # 先把当前(经纬)字段的编辑写回 self,再切换模式;否则即时路径读到的是
        # 尚未落回 self 的旧句柄,且 LATLON 字段在切走后从 validated 循环消失。
        self._flush_fields()
        self.ace_limit_mode = m
        self._ace_changed = True
        # 切至不显示经纬字段的模式后,apply_settings 的 validated 循环再也看不到这些字段;
        # 若上面 flush 已把编辑后的界值写回 self 且与 sim 已应用值不同,则须置脏,
        # 让确认时按最终同步值真正重算一次(保证“编辑界值+切模式+确认”不漏重算)。
        if (self.ace_min_lon != self.sim.ace_min_lon
                or self.ace_max_lon != self.sim.ace_max_lon
                or self.ace_min_lat != self.sim.ace_min_lat
                or self.ace_max_lat != self.sim.ace_max_lat):
            self._ace_recalc_dirty = True
        self._apply_filter_now()
        self.rebuild_fields(flush=False)

    def _cb_dark_mode(self, d):
        self.sim.dark_mode = d
        self._refresh_texts()
        self._refresh_field_themes()
        self._invalidate_tab_static()

    def _invalidate_tab_static(self):
        self._tab_static_cache.clear()

    def _field_rect(self, dx, y):
        return pygame.Rect(dx + self.LAYOUT_CTRL_X, y, self.LAYOUT_FIELD_W, self.LAYOUT_FIELD_H)

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
        self.show_legend = False
        self.show_graticule = False
        self.show_ocean_areas = False
        self.show_coord_hud = True
        self.show_track_label = True
        self.normal_other_display = "translucent"
        self.screen_width = self.sim.screen_width
        self.screen_height = self.sim.screen_height
        self.ace_display_mode = "progress_bar"
        self.main_rot_speed = 1.0
        self.level3_rot_speed = 1.5
        self.volume = 0.6
        self.landfall_vol_scale = True
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
        self.info_box_scale = 1.2
        self.disable_dpi_scaling = False
        self.fade_typhoon = True
        self.fade_path = True
        self.fade_path_mode = "fade"
        self.smooth_path = False
        self.smooth_path_mode = "monotone"
        self.smooth_path_segments = 10
        self.edit_snap_step = 0.1
        self.show_edit_point_labels = False
        self.path_mode = "markers"
        self.ace_interpolated = False
        self.show_fps = False
        self.fps_cap = 60
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
        if not (0 <= v <= 360):
            return None
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
        if v > 90:
            return None
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
        self.landfall_vol_text = rt(f_m, "登陆音效随强度增强:", TX)
        self.main_rot_label = rt(f_m, "主旋转速度:", TX)
        self.level3_rot_label = rt(f_m, "3级旋转速度:", TX)
        self.point_size_text = rt(f_m, "台风路径点大小 (%):", TX)
        self.icon_size_text = rt(f_m, "台风图标大小 (%):", TX)
        self.name_size_text = rt(f_m, "台风名称大小 (%):", TX)
        self.peak_label_text = rt(f_m, "巅峰/登陆文字 (%):", TX)
        self.info_box_scale_text = rt(f_m, "信息框字号 (0.8~1.6):", TX)
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
        # R4: 角点+大小模式无北界(最北纬度)输入控件,北界沿用“经纬范围”模式的 Mla。
        # 说明此处,避免用户对该数值的约束来源困惑(否则“最南纬度必须小于最北纬度”报错无从下手)。
        self.corner_lat_note = rt(f_s, "北界(最北纬度)沿用“经纬范围”中设置,如报错请切回该模式调整。",
                                  TD, 400)
        self.map_range_modes = [rt(f_m, "经纬范围", (255, 255, 255)), rt(f_m, "角点+大小", (255, 255, 255))]

        self.hemisphere_label = rt(f_m, "半球:", TX)
        self.dpi_label = rt(f_m, "禁用DPI缩放 (需重启):", TX)
        self.auto_continue_text = rt(f_m, "正常模式台风播放完成后自动继续:", TX)
        self.normal_info_text = rt(f_m, "正常模式显示台风信息框:", TX)
        self.season_info_text = rt(f_m, "风季模式显示台风信息框:", TX)
        self.fade_typhoon_text = rt(f_m, "台风图标平滑消失:", TX)
        self.fade_path_mode_text = rt(f_m, "路径消失模式:", TX)
        self.fade_path_mode_modes = [rt(f_m, "不消失", (255, 255, 255)),
                                     rt(f_m, "淡出", (255, 255, 255)),
                                     rt(f_m, "快速", (255, 255, 255))]
        self.fade_path_text = rt(f_m, "路径结束后保留:", TX)
        self.smooth_segments_text = rt(f_m, "平滑路径段数:", TX)
        self.smooth_path_text = rt(f_m, "平滑路径:", TX)
        self.smooth_path_mode_text = rt(f_m, "插值模式:", TX)
        self.edit_snap_step_text = rt(f_m, "编辑吸附:", TX)
        self.edit_snap_step_modes = [rt(f_m, "关", (255, 255, 255)),
                                     rt(f_m, "0.1°", (255, 255, 255)),
                                     rt(f_m, "0.5°", (255, 255, 255)),
                                     rt(f_m, "1°", (255, 255, 255))]
        self.edit_point_labels_text = rt(f_m, "编辑模式显示点标签:", TX)
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
        self.legend_text = rt(f_m, "显示强度图例:", TX)
        self.graticule_text = rt(f_m, "显示经纬网格:", TX)
        self.ocean_areas_text = rt(f_m, "显示洋区边界:", TX)
        self.coord_hud_text = rt(f_m, "显示鼠标经纬读数:", TX)
        self.track_label_text = rt(f_m, "显示镜头跟踪指示:", TX)
        self.normal_other_text = rt(f_m, "同期其它台风:", TX)
        self.color_scheme_text = rt(f_m, "配色方案:", TX)
        self.icon_set_text = rt(f_m, "台风图标:", TX)
        self.icon_set_warn = rt(f_s, "SMCY图标影响性能较大,谨慎使用", (200, 80, 80), 400)
        self.icon_set_modes = [
            rt(f_m, ICON_SET_NAMES[ICON_SET_SIMPLE], (255, 255, 255)),
            rt(f_m, ICON_SET_NAMES[ICON_SET_SMCY], (255, 255, 255))
        ]
        self.name_mode_text = rt(f_m, "名称显示模式:", TX)
        self.name_modes = [
            rt(f_m, "完整", (255, 255, 255)),
            rt(f_m, "年份+风暴名", (255, 255, 255)),
            rt(f_m, "风暴名", (255, 255, 255))
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
        self.basin_filter_note = None
        # F3: 新增说明文字与分组标题缓存
        self.window_note = rt(f_s, "窗口尺寸修改后立即重排窗口", TD, 400)
        self.theme_note = rt(f_s, "主题配色即时生效；固定图标与路径点大小需重载数据生效", TD, 400)
        self.dm_label = rt(f_m, "主题配色:", TX)
        self._section_title_cache = {}

    def _refresh_texts(self):
        self._pre_render_texts()

    def activate(self):
        if self.active:
            return
        super().activate()
        # 快照即时生效项，丢弃路径回滚（B7）
        self._immediate_snapshot = (
            (self.sim.basin_filter_enabled, self.sim.ace_limit_mode,
             self.sim.ace_limit_basin, self.sim.dark_mode,
             self.sim.ace_geo_limit_enabled),
            self.sim._config_needs_save)
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
        self.show_legend = self.sim.show_legend
        self.show_graticule = self.sim.show_graticule
        self.show_ocean_areas = self.sim.show_ocean_areas
        self.show_coord_hud = self.sim.show_coord_hud
        self.show_track_label = getattr(self.sim, 'show_track_label', True)
        self.normal_other_display = getattr(self.sim, 'normal_other_display', "translucent")
        self.screen_width = self.sim.screen_width
        self.screen_height = self.sim.screen_height
        self.ace_display_mode = self.sim.ace_display_mode
        self.main_rot_speed = self.sim.main_rotation_speed
        self.level3_rot_speed = self.sim.level3_rotation_speed
        self.volume = self.sim.volume
        self.landfall_vol_scale = getattr(self.sim, 'landfall_vol_scale', True)
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
        self.info_box_scale = getattr(self.sim, 'info_box_scale', 1.2)
        self.fix_icon_point_size = self.sim.fix_icon_point_size
        self.disable_dpi_scaling = self.sim.disable_dpi_scaling
        self.fade_typhoon = self.sim.fade_typhoon
        self.fade_path = self.sim.fade_path
        self.fade_path_mode = getattr(self.sim, 'fade_path_mode', 'fade')
        self.smooth_path = self.sim.smooth_path
        self.smooth_path_mode = getattr(self.sim, 'smooth_path_mode', 'monotone')
        self.smooth_path_segments = getattr(self.sim, 'smooth_path_segments', 10)
        self.edit_snap_step = getattr(self.sim, 'edit_snap_step', 0.1)
        self.show_edit_point_labels = getattr(self.sim, 'show_edit_point_labels', False)
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
        self.ace_geo_limit_enabled = getattr(self.sim, 'ace_geo_limit_enabled', False)   # N3
        self.icon_set = getattr(self.sim, 'icon_set', ICON_SET_SIMPLE)
        self.color_scheme = getattr(self.sim, 'color_scheme', 1)
        if not hasattr(self, 'tab_index') or self.tab_index < 0:
            self.tab_index = 0
        self.show_shortcuts = False
        self.show_info_box_editor = False
        self._needs_save = False
        self._ace_changed = False
        self._ace_recalc_dirty = False
        self._hemisphere_changed = False
        self._basin_dropdown_open = False
        self._basin_scroll_offset = 0
        self._error_fields = []
        self._content_scroll_y = 0
        self._content_scroll_max = 0
        # 搜索框(P2-18): 跨 tab 匹配 section 名
        from .input_field import InputField
        self.search_field = InputField(
            pygame.Rect(self.bg_rect.x + 120, self.bg_rect.y + 11, 160, 24),
            max_length=30, dark=self.dark_mode)
        self.search_field.set_text("")
        self._close_confirm = False
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
        self._basin_items = {
            code: (rt(f_s, f"{code} {name_cn}", SETTINGS_TEXT_LIGHT),
                   rt(f_s, f"{code} {name_cn}", TXT))
            for code, name_cn in self._basin_list}

    def deactivate(self) -> bool:
        if self._needs_save:
            if not self.apply_settings():
                return False
        self._immediate_snapshot = None
        self._deactivate_fields()
        super().deactivate()
        self.dragging = False
        self._basin_dropdown_open = False
        self._error_fields = []
        self._close_confirm = False
        return True

    def _update_bg_rect(self):
        w = min(750, self.sim.screen_width - 40)
        h = min(680, self.sim.screen_height - 60)
        dialog_x = (self.sim.screen_width - w) // 2
        dialog_y = (self.sim.screen_height - h) // 2
        self.bg_rect = pygame.Rect(dialog_x, dialog_y, w, h)

    _light_overlay_cache = None
    _light_panel_cache: dict = {}
    _gray16_cache = None

    @classmethod
    def _gray16(cls):
        if cls._gray16_cache is None:
            g = pygame.Surface((16, 16), pygame.SRCALPHA)
            g.fill((128, 128, 128, 160))
            cls._gray16_cache = g
        return cls._gray16_cache

    def _draw_light_overlay(self, surface):
        cls = Settings
        ov = cls._light_overlay_cache
        if ov is None or ov.get_size() != surface.get_size():
            ov = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 60))
            cls._light_overlay_cache = ov
        surface.blit(ov, (0, 0))

    def _light_panel(self, size):
        cls = Settings
        panel = cls._light_panel_cache.get(size)
        if panel is None:
            panel = pygame.Surface(size, pygame.SRCALPHA)
            pygame.draw.rect(panel, (240, 245, 255, 235), (0, 0, size[0], size[1]),
                             border_radius=DIALOG_CORNER_RADIUS)
            if len(cls._light_panel_cache) > 4:
                cls._light_panel_cache.pop(next(iter(cls._light_panel_cache)))
            cls._light_panel_cache[size] = panel
        return panel

    def _sync_field_positions(self):
        if not self.fields or not self._field_offsets:
            return
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        for i, (off_x, off_y, _, _) in enumerate(self._field_offsets):
            if i < len(self.fields):
                self.fields[i].rect.x = dialog_x + off_x
                self.fields[i].rect.y = dialog_y + off_y

    def _flush_fields(self):
        """rebuild 前把当前字段文本尽力解析回写 self（R30）。"""
        for f in self.fields:
            key = f.key
            val = f.get_text().strip()
            if not val:
                continue
            try:
                if self._map_range_mode == 1:
                    # 角点+大小模式: bl_lon/bl_lat/span 映射到 mlo/mla/Mlo
                    # （这些 key 不是实例属性，必须先于 hasattr 守卫处理，否则切 tab/切模式丢编辑）
                    if key == 'bl_lon':
                        parsed = self._parse_lon(val)
                        if parsed is not None:
                            self.mlo = parsed
                        continue
                    if key == 'bl_lat':
                        parsed = self._parse_lat(val)
                        if parsed is not None:
                            self.mla = parsed
                        continue
                    if key == 'span':
                        span_v = float(val)
                        self.Mlo = self.mlo + span_v
                        continue
                if not hasattr(self, key):
                    continue
                if key == 'Mlo' and self._map_range_mode == 1:
                    self.Mlo = self.mlo + float(val)
                elif self._is_lon_key(key):
                    parsed = self._parse_lon(val)
                    if parsed is not None:
                        setattr(self, key, parsed)
                elif self._is_lat_key(key):
                    parsed = self._parse_lat(val)
                    if parsed is not None:
                        setattr(self, key, parsed)
                elif key in ('mis', 'mas', 'main_rot_speed', 'level3_rot_speed', 'info_box_scale'):
                    setattr(self, key, float(val))
                elif key == 'smooth_path_segments':
                    setattr(self, key, int(val))
                elif key == 'volume':
                    setattr(self, key, float(val) / 100.0)
                elif key in ('point_size', 'icon_size', 'name_size', 'peak_label_size',
                             'screen_width', 'screen_height'):
                    setattr(self, key, int(val))
            except ValueError:
                pass

    def rebuild_fields(self, flush: bool = True):
        if flush:
            self._flush_fields()
        for f in self.fields:
            f.deactivate()
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        self.fields.clear()
        self._field_offsets.clear()
        for key, val, rect, validator in self._get_fields_config():
            field = InputField(rect, max_length=10, dark=self.dark_mode)
            field.set_text(val)
            field.key = key
            field.on_change = self._field_edited
            self.fields.append(field)
            self._field_offsets.append((rect[0] - dialog_x, rect[1] - dialog_y, rect[2], rect[3]))
        self._field_orig = {f.key: f.get_text() for f in self.fields}

    def _field_edited(self):
        self._needs_save = True

    def _has_field_changes(self):
        return {f.key: f.get_text() for f in self.fields} != getattr(self, '_field_orig', {})

    def _deactivate_fields(self):
        for f in self.fields:
            f.deactivate()
        sf = getattr(self, 'search_field', None)
        if sf is not None:
            sf.deactivate()

    def _do_search(self, q: str) -> None:
        """跨 tab 匹配 section 名, 命中则切 tab 并滚动到顶部。"""
        q = q.strip().lower()
        if not q:
            return
        for ti in range(len(SETTINGS_TAB_NAMES)):
            old = self.tab_index
            self.tab_index = ti
            try:
                layout = self._tab_layout()
            finally:
                self.tab_index = old
            for row in layout:
                if row[0] == 'section' and q in str(row[1]).lower():
                    self.tab_index = ti
                    # 与点击 tab 一致: 不重建输入框会画在新 tab 布局上, 且确认时
                    # 校验/回写的是旧 tab 的字段键
                    self._restore_confirm = False
                    self.rebuild_fields()
                    self._invalidate_tab_static()
                    self._content_scroll_y = 0
                    self._basin_dropdown_open = False
                    return

    def _mark_error_field(self, *keys):
        for f in self.fields:
            if f.key in keys and f not in self._error_fields:
                self._error_fields.append(f)

    def _get_fields_config(self):
        """从布局表派生字段配置(F3): (key, 文本, rect, validator)。"""
        dialog_x, dialog_y = self.bg_rect.x, self.bg_rect.y
        layout = self._tab_layout()
        ys, _ = self._tab_rows_y()
        lon_val = self.validate_lon
        lat_val = self.validate_lat
        int_val = self.validate_int
        float_val = self.validate_float
        out = []
        for row, y in zip(layout, ys):
            if row[0] != 'field':
                continue
            key = row[1]
            base = dialog_y + 95 + y
            if key == 'mis':
                val, vd = f"{self.mis:.1f}", float_val
            elif key == 'mas':
                val, vd = f"{self.mas:.1f}", float_val
            elif key == 'volume':
                val, vd = f"{int(self.volume*100)}", int_val
            elif key == 'main_rot_speed':
                val, vd = f"{self.main_rot_speed:.2f}", float_val
            elif key == 'level3_rot_speed':
                val, vd = f"{self.level3_rot_speed:.2f}", float_val
            elif key == 'point_size':
                val, vd = f"{self.point_size}", int_val
            elif key == 'icon_size':
                val, vd = f"{self.icon_size}", int_val
            elif key == 'name_size':
                val, vd = f"{self.name_size}", int_val
            elif key == 'peak_label_size':
                val, vd = f"{self.peak_label_size}", int_val
            elif key == 'info_box_scale':
                val, vd = f"{self.info_box_scale:.1f}", float_val
            elif key == 'screen_height':
                val, vd = f"{self.screen_height}", int_val
            elif key == 'screen_width':
                val, vd = f"{self.screen_width}", int_val
            elif key == 'smooth_path_segments':
                val, vd = f"{self.smooth_path_segments}", int_val
            elif key in ('mlo', 'ace_min_lon'):
                val, vd = lon_to_display(self.mlo if key == 'mlo' else self.ace_min_lon), lon_val
            elif key in ('Mlo', 'ace_max_lon'):
                if key == 'Mlo' and self._map_range_mode == 1:
                    val, vd = f"{self.Mlo - self.mlo:.1f}", float_val
                else:
                    val, vd = lon_to_display(self.Mlo if key == 'Mlo' else self.ace_max_lon), lon_val
            elif key in ('mla', 'ace_min_lat'):
                val, vd = lat_to_display(self.mla if key == 'mla' else self.ace_min_lat), lat_val
            elif key in ('Mla', 'ace_max_lat'):
                val, vd = lat_to_display(self.Mla if key == 'Mla' else self.ace_max_lat), lat_val
            elif key == 'bl_lon':
                val, vd = lon_to_display(self.mlo), lon_val
            elif key == 'bl_lat':
                val, vd = lat_to_display(self.mla), lat_val
            elif key == 'span':
                val, vd = f"{self.Mlo - self.mlo:.1f}", float_val
            else:
                continue
            out.append((key, val, (dialog_x + self.LAYOUT_CTRL_X, base,
                                   self.LAYOUT_FIELD_W, self.LAYOUT_FIELD_H), vd))
        return out

    @staticmethod
    def validate_float(char: str) -> bool:
        return char == '-' or char.isdigit() or char == '.'

    @staticmethod
    def validate_int(char: str) -> bool:
        return char.isdigit() or char == '-'

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
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, self.bg_rect)
            text_color = SETTINGS_TEXT_LIGHT
        else:
            self._draw_light_overlay(surface)
            panel = self._light_panel(self.bg_rect.size)
            surface.blit(panel, (self.bg_rect.x, self.bg_rect.y))
            text_color = TXT

        dx, dy, dw, dh = self.bg_rect

        title = rt(f_m, "设置", text_color)
        surface.blit(title, (dx + 20, dy + 12))
        sf = getattr(self, 'search_field', None)
        if sf is not None:
            sf.rect.x = dx + 120
            sf.rect.y = dy + 11
            sf.draw(surface)

        mx, my = pygame.mouse.get_pos()
        btn_w, btn_h = 60, 26
        close_rect = pygame.Rect(dx + dw - btn_w - 12, dy + 8, btn_w, btn_h)
        self._add_target(close_rect, lambda: self._on_close())
        self._draw_modern_button(surface, close_rect, "关闭", hover=close_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        ok_rect = pygame.Rect(dx + dw - btn_w*2 - 20, dy + 8, btn_w, btn_h)
        self._add_target(ok_rect, lambda: self._on_ok())
        self._draw_modern_button(surface, ok_rect, "确认", hover=ok_rect.collidepoint(mx, my), accent=True, dark=self.dark_mode)

        # 右上角按钮(F3 修复:文字不超出按钮,按实际文字宽度重排)
        sc_rect = pygame.Rect(dx + dw - 222, dy + 8, 74, 22)
        self._add_target(sc_rect, lambda: setattr(self, 'show_shortcuts', not self.show_shortcuts))
        self._draw_modern_button(surface, sc_rect, "快捷键", hover=sc_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        df_rect = pygame.Rect(dx + dw - 430, dy + 8, 96, 22)
        self._add_target(df_rect, self._request_restore_defaults)
        self._draw_modern_button(surface, df_rect, "恢复默认", hover=df_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
        rl_rect = pygame.Rect(dx + dw - 326, dy + 8, 96, 22)
        self._add_target(rl_rect, self._on_reload)
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
        ind_w = tab_rects[self.tab_index].w - 8
        ind = pygame.Rect(int(tab_rects[self.tab_index].x + 4), tab_y + tab_area_h - 3, ind_w, 3)
        pygame.draw.rect(surface, self._accent, ind, border_radius=2)

        # 内容区域（可滚动）
        content_top = tab_y + tab_area_h + 8
        content_bottom = dy + dh - 40
        content_h = content_bottom - content_top
        scroll = self._content_scroll_y

        layout = self._tab_layout()
        ys, total_h = self._tab_rows_y()

        # F3: 滚动上限由布局总高一次算得(不再从 _targets 反推)
        self._content_scroll_max = max(0, total_h - content_h)
        self._content_scroll_y = min(self._content_scroll_y, self._content_scroll_max)

        old_clip = surface.get_clip()
        surface.set_clip(pygame.Rect(dx + 4, content_top, dw - 8, content_h))

        # 静态层(缓存) + 动态层
        # 静态层内部坐标以 dx 为基准,故 blit 到 (dx, ...),与动态层绝对坐标一致
        static = self._tab_static_surface(dx, dw, layout, ys, total_h)
        surface.blit(static, (dx, content_top - scroll))
        self._draw_tab_rows(surface, dx, dw, dy, content_top, scroll, mx, my)

        surface.set_clip(old_clip)

        # 字段随内容区滚动绘制（点击判定同步加回滚动偏移）
        for field in self.fields:
            field.draw(surface, y_offset=-scroll)
        for field in self._error_fields:
            er = field.rect.move(0, -scroll)
            pygame.draw.rect(surface, (230, 60, 60), er, 2, 3)

        # 滚动条（内容超出视口时显示，可拖动）
        self._sb_track_rect = None
        self._sb_thumb_rect = None
        if self._content_scroll_max > 0:
            self._sb_track_rect = pygame.Rect(dx + dw - 12, content_top, 6, content_h)
            pygame.draw.rect(surface, (70, 75, 90, 120), self._sb_track_rect, border_radius=3)
            thumb_h = max(24, int(content_h * content_h / (content_h + self._content_scroll_max)))
            thumb_h = min(thumb_h, content_h)
            ratio = self._content_scroll_y / self._content_scroll_max
            thumb_y = content_top + int(ratio * (content_h - thumb_h))
            self._sb_thumb_rect = pygame.Rect(dx + dw - 12, thumb_y, 6, thumb_h)
            pygame.draw.rect(surface, (175, 180, 195, 200), self._sb_thumb_rect, border_radius=3)

        # 未保存更改确认条
        if self._close_confirm:
            bar = pygame.Rect(dx + 20, dy + dh - 44, dw - 40, 34)
            if self.dark_mode:
                pygame.draw.rect(surface, (70, 50, 40), bar, 0, 4)
                pygame.draw.rect(surface, (200, 120, 60), bar, 1, 4)
                bt = SETTINGS_TEXT_LIGHT
            else:
                pygame.draw.rect(surface, (255, 236, 220), bar, 0, 4)
                pygame.draw.rect(surface, (200, 110, 40), bar, 1, 4)
                bt = (120, 60, 10)
            note = rt(f_s, "有未保存更改:", bt)
            surface.blit(note, (bar.x + 8, bar.y + (bar.h - note.get_height()) // 2))
            discard_rect = pygame.Rect(bar.right - 120, bar.y + 4, 54, 26)
            cancel_rect = pygame.Rect(bar.right - 62, bar.y + 4, 54, 26)
            self._add_target(discard_rect, self._on_close)
            self._add_target(cancel_rect, lambda: setattr(self, '_close_confirm', False))
            self._draw_modern_button(surface, discard_rect, "丢弃", hover=discard_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)
            self._draw_modern_button(surface, cancel_rect, "取消", hover=cancel_rect.collidepoint(mx, my), accent=False, dark=self.dark_mode)

        # 恢复默认二次确认条(F3)（置于 Tab 栏之下,避免遮挡 Tab 切换）
        if self._restore_confirm:
            bar = pygame.Rect(dx + 20, dy + 88, dw - 40, 34)
            if self.dark_mode:
                pygame.draw.rect(surface, (70, 50, 40), bar, 0, 4)
                pygame.draw.rect(surface, (200, 120, 60), bar, 1, 4)
                bt = SETTINGS_TEXT_LIGHT
            else:
                pygame.draw.rect(surface, (255, 236, 220), bar, 0, 4)
                pygame.draw.rect(surface, (200, 110, 40), bar, 1, 4)
                bt = (120, 60, 10)
            note = rt(f_s, "确认恢复默认设置?", bt)
            surface.blit(note, (bar.x + 8, bar.y + (bar.h - note.get_height()) // 2))
            ok_r = pygame.Rect(bar.right - 120, bar.y + 4, 54, 26)
            cc_r = pygame.Rect(bar.right - 62, bar.y + 4, 54, 26)
            self._add_target(ok_r, self._request_restore_defaults)
            self._add_target(cc_r, self._cancel_restore)
            self._draw_modern_button(surface, ok_r, "确认", hover=ok_r.collidepoint(mx, my), accent=True, dark=self.dark_mode)
            self._draw_modern_button(surface, cc_r, "取消", hover=cc_r.collidepoint(mx, my), accent=False, dark=self.dark_mode)
            # 拦截条内空白区域点击,避免误触下方内容控件
            self._add_target(bar, lambda: None)

        # 右下角确认按钮（确认条弹出时不绘制,避免覆盖条内"取消"按钮）
        if not self._close_confirm:
            confirm_w, confirm_h = 80, 28
            confirm_rect = pygame.Rect(dx + dw - confirm_w - 15, dy + dh - confirm_h - 12, confirm_w, confirm_h)
            self._add_target(confirm_rect, lambda: self._on_ok())
            hover_confirm = confirm_rect.collidepoint(mx, my)
            self._draw_modern_button(surface, confirm_rect, "确认", hover=hover_confirm, accent=True, dark=self.dark_mode)

        if self.show_shortcuts:
            self.draw_shortcuts_help(surface)
        elif self.show_info_box_editor:
            self.draw_info_box_editor(surface)

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
        if self.apply_settings():
            self._needs_save = False
            # 立即落盘并据此提示: 写盘失败时不能谎报"已保存"
            if self.sim.save_config(force=True):
                self.sim.show_toast("设置已保存", 'success')
            else:
                self.sim.show_error("设置保存失败(写入配置失败), 更改仅在本次运行有效")
            self._immediate_snapshot = None
            self._deactivate_fields()
            super().deactivate()
            self.dragging = False
            self._basin_dropdown_open = False
            self._error_fields = []
            self._close_confirm = False

    def _on_close(self):
        if self._needs_save and not self._close_confirm:
            self._close_confirm = True
            self.sim.show_error("有未保存更改 — 再次关闭将丢弃")
            return
        self._restore_immediate_applied()
        self._deactivate_fields()
        super().deactivate()
        self.dragging = False
        self._basin_dropdown_open = False
        self._error_fields = []
        self._close_confirm = False

    def _restore_immediate_applied(self):
        """丢弃路径：回滚即时生效项（盆域/主题）并恢复脏标志。"""
        snap = getattr(self, '_immediate_snapshot', None)
        if snap is None:
            return
        fields, dirty = snap
        sim = self.sim
        if (fields[0] == sim.basin_filter_enabled
                and fields[1] == sim.ace_limit_mode
                and fields[2] == sim.ace_limit_basin
                and fields[3] == sim.dark_mode
                and fields[4] == sim.ace_geo_limit_enabled):
            self._immediate_snapshot = None
            self._ace_changed = False
            self._ace_recalc_dirty = False
            self._hemisphere_changed = False
            return
        ace_changed = (fields[1] != sim.ace_limit_mode
                       or fields[2] != sim.ace_limit_basin)
        (sim.basin_filter_enabled, sim.ace_limit_mode, sim.ace_limit_basin,
         sim.dark_mode, sim.ace_geo_limit_enabled) = fields
        sim._config_needs_save = dirty
        sim._apply_basin_filter()
        sim.update_all_screen_points()
        if ace_changed:
            sim.recalc_all_ace()
        self._immediate_snapshot = None
        self._ace_changed = False
        self._ace_recalc_dirty = False
        self._hemisphere_changed = False

    def _apply_filter_now(self):
        """即时应用洋区过滤器。"""
        self.sim.basin_filter_enabled = self.basin_filter_enabled
        self.sim.ace_limit_mode = self.ace_limit_mode
        self.sim.ace_limit_basin = self.ace_limit_basin
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.ace_geo_limit_enabled = self.sim.ace_geo_limit_enabled   # N3: 本地保持同步
        self.sim._apply_basin_filter()
        self.sim.recalc_all_ace()
        self.sim.update_all_screen_points()

    def _on_reload(self):
        self._deactivate_fields()
        self._needs_save = True
        if self.deactivate():
            self.sim.reload_typhoons()

    def _sync(self, name: str, local: str = None) -> None:
        old = getattr(self.sim, name)
        new = getattr(self, local or name)
        if old != new:
            setattr(self.sim, name, new)

    def _refresh_field_themes(self):
        for f in self.fields:
            f.dark = self.dark_mode

    def _restore_defaults(self):
        from .config import AppConfig
        from dataclasses import fields as _dfields
        d = AppConfig()
        for fld in _dfields(AppConfig):
            name = fld.name
            if name in self.__dict__:
                setattr(self, name, getattr(d, name))
        self._map_range_mode = 0
        self.tab_index = 0
        self._needs_save = True
        self._close_confirm = False
        self._restore_confirm = False
        self.show_shortcuts = False
        self.show_info_box_editor = False
        self._basin_dropdown_open = False
        self._content_scroll_y = 0
        self._error_fields = []
        self._ace_changed = False
        self._ace_recalc_dirty = False
        self._hemisphere_changed = False
        # 恢复默认会整体重置 ACE 配置；hemisphere 亦影响 ACE 累计口径,经重置成北半球
        # 后须一并重算(此前漏检)+ 风季下重定位季节指针,避免 ACE 数据仍旧半球口径。
        if self.hemisphere != self.sim.hemisphere:
            self._hemisphere_changed = True
            self._ace_recalc_dirty = True
        # ace_limit_mode/basin 不经 validated 字段，与被恢复的 sim 已应用值不一致时须置脏，
        # 确保 apply_settings 正确重组/重算。
        if (self.ace_limit_mode != self.sim.ace_limit_mode
                or self.ace_limit_basin != self.sim.ace_limit_basin
                or self.ace_min_lon != self.sim.ace_min_lon
                or self.ace_max_lon != self.sim.ace_max_lon
                or self.ace_min_lat != self.sim.ace_min_lat
                or self.ace_max_lat != self.sim.ace_max_lat):
            self._ace_recalc_dirty = True
        self.rebuild_fields()
        self._update_bg_rect()

    def _request_restore_defaults(self):
        """F3: 恢复默认二次确认。"""
        if self._restore_confirm:
            self._restore_confirm = False
            self._restore_defaults()
        else:
            self._restore_confirm = True

    def _cancel_restore(self):
        self._restore_confirm = False

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

    # ── Tab 内容（布局表驱动,F3）──

    def _tab_static_surface(self, dx, dw, layout, ys, total_h):
        """静态层:标签/分组标题/说明/控件底(不含悬停与选中态)。"""
        dark = self.dark_mode
        # 尺寸进键: 改窗口大小后旧静态层不能复用(会画在错误位置)
        key = (self.tab_index, self._tab_variant(), dark, self.sim.color_scheme,
               self.sim.screen_width, self.sim.screen_height)
        cached = self._tab_static_cache.get(key)
        if cached is not None:
            return cached
        surf = pygame.Surface((dw - 8, total_h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        TX = SETTINGS_TEXT_LIGHT if dark else TXT
        TD = SETTINGS_TEXT_DIM if dark else (100, 110, 130)
        for row, y in zip(layout, ys):
            t = row[0]
            if t == 'section':
                title = row[1]
                st = self._section_title_cache.get((title, dark))
                if st is None:
                    st = rt(f_m, title, settings_accent(dark, self.sim.color_scheme))
                    self._section_title_cache[(title, dark)] = st
                    if len(self._section_title_cache) > 64:
                        self._section_title_cache.pop(next(iter(self._section_title_cache)))
                surf.blit(st, (self.LAYOUT_LABEL_X, y))
                pygame.draw.line(surf, (120, 130, 150, 90), (self.LAYOUT_LABEL_X, y + 20),
                                 (dw - 8 - 10, y + 20), 1)
            elif t == 'field':
                lab = getattr(self, self._FIELD_LABELS.get(row[1], ''), None)
                if lab is not None:
                    surf.blit(lab, (self.LAYOUT_LABEL_X, y + self._lh(lab, 24)))
            elif t == 'checkbox' or t == 'checkbox_now':
                lab = getattr(self, self._CHECK_LABELS.get(row[1], ''), None)
                if lab is not None:
                    surf.blit(lab, (self.LAYOUT_LABEL_X, y + self._lh(lab, 16)))
                b = pygame.Rect(dw - 50, y, 16, 16)   # checkbox 靠右列(相对 dx)
                pygame.draw.rect(surf, SETTINGS_CHECKBOX_BG, b, border_radius=3)
                pygame.draw.rect(surf, SETTINGS_INPUT_BORDER, b, 1, border_radius=3)
            elif t == 'toggle':
                _, attr, modes, btn_w, _cb, lab_attr = row
                if lab_attr:
                    lab = getattr(self, lab_attr, None)
                    if lab is not None:
                        surf.blit(lab, (self.LAYOUT_LABEL_X, y + self._lh(lab, 22)))
                for i in range(len(modes)):
                    rect = pygame.Rect(self.LAYOUT_BTN_X + i * (btn_w + 10), y,
                                       btn_w, self.LAYOUT_BTN_H)
                    bg = SETTINGS_TOGGLE_OFF if dark else (200, 205, 215)
                    pygame.draw.rect(surf, bg, rect, border_radius=6)
            elif t == 'note':
                note = getattr(self, row[1], None)
                if note is not None:
                    surf.blit(note, (self.LAYOUT_LABEL_X, y))
            elif t == 'dropdown':
                b = pygame.Rect(self.LAYOUT_BTN_X, y, 220, 24)
                bg = SETTINGS_TOGGLE_ON if dark else (200, 205, 215)
                pygame.draw.rect(surf, bg, b, border_radius=6)
        self._tab_static_cache[key] = surf
        if len(self._tab_static_cache) > 24:
            self._tab_static_cache.pop(next(iter(self._tab_static_cache)))
        return surf

    def _draw_tab_rows(self, surface, dx, dw, dy, content_top, scroll, mx, my):
        """动态层:开关 on/hover、checkbox 勾选、目标注册、洋区下拉。
        下拉列表最后绘制,避免被后续行的选中态遮挡(F3 修复)。"""
        layout = self._tab_layout()
        ys, total_h = self._tab_rows_y()
        dark = self.dark_mode
        _lh = self._lh
        dropdown_rect = None
        for row, y in zip(layout, ys):
            yy = content_top + y - scroll
            t = row[0]
            if t == 'checkbox' or t == 'checkbox_now':
                attr = row[1]
                cbx = dx + dw - 50
                r = pygame.Rect(cbx, yy, 16, 16)
                if attr == 'show_ace_total' and not self.show_ace_bar:
                    # 依赖置灰:show_ace_bar 关闭时不可点(保持原行为)
                    self._add_target(r, lambda: None)
                    surface.blit(self._gray16(), (cbx, yy))
                    continue
                if t == 'checkbox_now':
                    self._add_target(r, lambda a=attr: (
                        setattr(self, a, not getattr(self, a)),
                        setattr(self, '_needs_save', True),
                        self._apply_filter_now()))
                else:
                    self._add_target(r, lambda a=attr: (
                        setattr(self, a, not getattr(self, a)),
                        setattr(self, '_needs_save', True)))
                checked = getattr(self, attr)
                if attr == 'show_ace_total':
                    checked = checked and self.show_ace_bar
                if checked:
                    pygame.draw.rect(surface, SETTINGS_CHECKBOX_CHECK,
                                     pygame.Rect(cbx + 3, yy + 3, 10, 10), border_radius=2)
            elif t == 'toggle':
                _, attr, modes, btn_w, cb, _lab = row
                cur = getattr(self, attr)
                for i, (lbl, val) in enumerate(modes):
                    rect = pygame.Rect(dx + self.LAYOUT_BTN_X + i * (btn_w + 10), yy,
                                       btn_w, self.LAYOUT_BTN_H)
                    on = cur == val
                    hover = rect.collidepoint(mx, my) and not on
                    if on:
                        pygame.draw.rect(surface, self._accent, rect, border_radius=6)
                    elif hover:
                        pygame.draw.rect(surface, SETTINGS_TOGGLE_ON if dark else (160, 175, 200),
                                         rect, border_radius=6)
                    if cb is not None:
                        self._add_target(rect, (lambda v=val, c=cb: (
                            c(v), setattr(self, '_needs_save', True))))
                    else:
                        self._add_target(rect, (lambda v=val, a=attr: (
                            setattr(self, a, v), setattr(self, '_needs_save', True))))
                    if isinstance(lbl, str):
                        ts = rt(f_m, lbl, (25, 32, 45) if on else (255, 255, 255))
                    else:
                        ts = lbl
                    surface.blit(ts, (rect.x + (rect.w - ts.get_width()) // 2,
                                      rect.y + (rect.h - ts.get_height()) // 2 - 1))
            elif t == 'button':
                # 显示 tab 内可点击按钮(如「信息框自定义」)
                _, label = row[0], row[1]
                b = pygame.Rect(dx + self.LAYOUT_BTN_X, yy, 180, self.LAYOUT_BTN_H)
                self._add_target(b, self._open_info_box_editor)
                self._draw_modern_button(surface, b, label,
                                         hover=b.collidepoint(mx, my), accent=False, dark=dark)
            elif t == 'btn_ocean_edit':
                # ACE tab: 「洋区编辑」→ 进入主地图洋区编辑模式(仅此入口)
                b = pygame.Rect(dx + self.LAYOUT_BTN_X, yy, 180, self.LAYOUT_BTN_H)
                self._add_target(b, self._enter_ocean_edit)
                self._draw_modern_button(surface, b, rt(f_m, "洋区编辑", (255, 255, 255)),
                                         hover=b.collidepoint(mx, my), accent=True, dark=dark)
            elif t == 'dropdown':
                b = pygame.Rect(dx + self.LAYOUT_BTN_X, yy, 220, 24)
                current_basin = self.ace_limit_basin
                area = self.sim.res_mgr.ocean_areas.get_by_code(current_basin) if current_basin else None
                display = area.name_cn if area else (current_basin or "选择洋区")
                self._add_target(b, lambda: (setattr(self, '_basin_dropdown_open', True),
                                             setattr(self, '_basin_scroll_offset', 0)))
                text_color = SETTINGS_TEXT_LIGHT if dark else (60, 70, 90)
                text_surface = rt(f_s, display, text_color)
                surface.blit(text_surface, (b.x + 5, b.y + (b.h - text_surface.get_height()) // 2))
                dropdown_rect = b
        # 下拉列表最后绘制(不被后续行遮挡)
        if dropdown_rect is not None and self._basin_dropdown_open:
            self._dropdown_targets = []
            b = dropdown_rect
            ITEM_H = 24
            max_vis = 8
            list_h = min(len(self._basin_list), max_vis) * ITEM_H
            list_rect = pygame.Rect(b.x, b.bottom, b.width, list_h)
            bg_c = SETTINGS_TAB_ACTIVE if dark else (255, 255, 255)
            pygame.draw.rect(surface, bg_c, list_rect, 0, 3)
            pygame.draw.rect(surface, settings_accent(dark, self.sim.color_scheme) if dark else (70, 130, 180),
                             list_rect, 1, 3)
            total = len(self._basin_list)
            scroll_offset = max(0, min(self._basin_scroll_offset, total - max_vis))
            for i in range(scroll_offset, min(scroll_offset + max_vis, total)):
                code, name_cn = self._basin_list[i]
                iy = list_rect.y + (i - scroll_offset) * ITEM_H
                item_rect = pygame.Rect(list_rect.x, iy, list_rect.width, ITEM_H)
                if item_rect.collidepoint(mx, my):
                    hover_c = SETTINGS_ACCENT if dark else (180, 220, 255)
                    pygame.draw.rect(surface, hover_c, item_rect)
                self._dropdown_targets.append((item_rect, lambda cd=code: (
                    setattr(self, 'ace_limit_basin', cd),
                    setattr(self, '_ace_changed', True),
                    setattr(self, '_needs_save', True),
                    setattr(self, '_basin_dropdown_open', False),
                    self._apply_filter_now())))
                item_surfs = self._basin_items.get(code)
                it = item_surfs[0] if dark else item_surfs[1]
                surface.blit(it, (item_rect.x + 5, item_rect.y + 3))

    # ── 快捷键分类数据 ─────────────────────────────────────────────
    SHORTCUT_SECTIONS = [
        ("📖  基本使用", (90, 110, 150), [
            ("运行",       "pip install -r requirements.txt  然后  python main.py"),
            ("鼠标",       "右键拖拽=平移地图, 滚轮=缩放, 左键(编辑模式)=选点/拖点"),
            ("四种模式",   "H 循环 正常/风季/编辑; 模拟模式从功能栏进入"),
            ("正常模式",   "逐条回放单个台风, [ ] 切换, 播完自动连播下一个"),
            ("风季模式",   "整年所有台风同时推进, 含季节时钟/ACE/月度总结"),
            ("编辑模式",   "增删改报点, 保存自动完成(不弹提示), Ctrl+Z 撤销"),
            ("模拟模式",   "架空台风模拟: 生成台风 + 气压/风场/云图/SST 等图层"),
            ("完整说明",   "仓库根目录 使用说明.md (含统计图表/脚本/常见问题)"),
        ]),
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
            ("H",         "切换模式 (正常 ↔ 风季 ↔ 编辑)"),
            ("[",         "上一个台风"),
            ("]",         "下一个台风"),
            ("I",         "新建台风 (编辑模式)"),
            ("T",         "时间跳转 (风季模式)"),
        ]),
        ("📊  面板 / 工具", (140, 100, 200), [
            ("O",         "台风列表"),
            ("S",         "打开设置"),
            ("G",         "点列表 (编辑模式可编辑)"),
            ("K",         "台风详情 (正常/编辑) / ACE统计 (风季)"),
            ("D",         "打开绘画面板 (正常 / 风季 / 编辑)"),
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

    _IB_ROW_NAMES = {'name': '台风名', 'time': '时间', 'pos': '位置', 'wind': '风速',
                     'cat': '等级+气压', 'speed': '移速', 'peak': '巅峰',
                     'tace': '总ACE', 'cace': '当前ACE'}

    def _open_info_box_editor(self):
        self._deactivate_fields()
        self.show_shortcuts = False
        self.show_info_box_editor = True
        w, h = 560, 500
        self._ib_editor_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2, (self.sim.screen_height - h) // 2, w, h)

    def _enter_ocean_edit(self):
        """「洋区编辑」按钮: 关闭设置对话框并进入主地图洋区编辑模式。
        再次点击(需重开设置)或按 H 退出。"""
        self._deactivate_fields()
        self._on_close()
        if self.active:
            # _on_close 因"未保存更改"被拦下(需再点一次): 此时不能进编辑模式,
            # 否则 enter() 的 deactivate_all 会绕过确认直接关闭设置
            return
        self.sim.ocean_edit.enter()

    def _close_info_box_editor(self):
        self.show_info_box_editor = False

    def _ib_rows(self):
        return self.sim.info_box_rows or []

    def _ib_set_rows(self, rows):
        self.sim.info_box_rows = rows

    def draw_info_box_editor(self, surface):
        """信息框自定义二级面板: 逐行显隐/平滑/排序 + 背景/阴影 + 恢复默认。"""
        from .config import _default_info_box_rows
        r = self._ib_editor_rect
        dark = self.dark_mode
        # 遮罩 + 面板: 复用 Dialog._overlay_cache(按尺寸缓存), 否则每帧新建
        # 全屏 SRCALPHA 面(2560x1540 ≈ 15.7MB 分配)
        _okey = (self.sim.screen_width, self.sim.screen_height, (0, 0, 0, 120))
        overlay = self._overlay_cache.get(_okey)
        if overlay is None:
            overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 120))
            self._overlay_cache[_okey] = overlay
            if len(self._overlay_cache) > 4:
                self._overlay_cache.pop(next(iter(self._overlay_cache)))
        surface.blit(overlay, (0, 0))
        panel = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (22, 28, 44, 245) if dark else (245, 245, 248, 245),
                         panel.get_rect(), 0, 12)
        pygame.draw.rect(panel, (55, 85, 130) if dark else (120, 140, 170),
                         panel.get_rect(), 2, 12)
        tc = (215, 225, 245) if dark else (30, 40, 60)
        title = rt(f_m, "信息框自定义(正常/编辑模式)", tc)
        panel.blit(title, (16, 12))

        rows = self._ib_rows()
        self._ib_editor_rects = []
        y = 46
        for i, item in enumerate(rows):
            key = item['key']
            on = bool(item.get('on', True))
            sm = bool(item.get('smooth', True))
            lbl = rt(f_s, self._IB_ROW_NAMES.get(key, key), tc)
            panel.blit(lbl, (16, y + 4))
            x = 150
            # 显示开关
            tgl = pygame.Rect(x, y, 44, 22)
            self._draw_modern_button(panel, tgl, "显示" if on else "隐藏",
                                     accent=on, dark=dark)
            self._ib_editor_rects.append((tgl.move(r.x, r.y), 'on', key))
            x += 52
            # 平滑开关
            sgl = pygame.Rect(x, y, 44, 22)
            self._draw_modern_button(panel, sgl, "平滑" if sm else "锐利",
                                     accent=sm, dark=dark)
            self._ib_editor_rects.append((sgl.move(r.x, r.y), 'smooth', key))
            x += 52
            # 上移/下移
            up = pygame.Rect(x, y, 22, 22)
            self._draw_modern_button(panel, up, "↑", accent=False, dark=dark)
            self._ib_editor_rects.append((up.move(r.x, r.y), 'up', key))
            x += 26
            dn = pygame.Rect(x, y, 22, 22)
            self._draw_modern_button(panel, dn, "↓", accent=False, dark=dark)
            self._ib_editor_rects.append((dn.move(r.x, r.y), 'down', key))
            y += 30

        y += 6
        # 全局开关
        bg_on = bool(self.sim.info_box_bg)
        sh_on = bool(self.sim.info_box_text_shadow)
        bgl = rt(f_s, "背景+色条:", tc)
        panel.blit(bgl, (16, y + 4))
        bt = pygame.Rect(150, y, 52, 22)
        self._draw_modern_button(panel, bt, "开启" if bg_on else "透明", accent=bg_on, dark=dark)
        self._ib_editor_rects.append((bt.move(r.x, r.y), 'bg', ''))
        shl = rt(f_s, "文字阴影:", tc)
        panel.blit(shl, (220, y + 4))
        st = pygame.Rect(310, y, 52, 22)
        self._draw_modern_button(panel, st, "开启" if sh_on else "关闭", accent=sh_on, dark=dark)
        self._ib_editor_rects.append((st.move(r.x, r.y), 'shadow', ''))
        y += 34

        # 底部按钮
        df = pygame.Rect(r.width // 2 - 110, r.height - 40, 100, 26)
        self._draw_modern_button(panel, df, "恢复默认", accent=False, dark=dark)
        self._ib_editor_rects.append((df.move(r.x, r.y), 'reset', ''))
        cl = pygame.Rect(r.width // 2 + 10, r.height - 40, 100, 26)
        self._draw_modern_button(panel, cl, "关闭", accent=True, dark=dark)
        self._ib_editor_rects.append((cl.move(r.x, r.y), 'close', ''))
        surface.blit(panel, r.topleft)

    def _handle_info_box_editor_event(self, e):
        from .config import _default_info_box_rows
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self._close_info_box_editor()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not self._ib_editor_rect.collidepoint(e.pos):
                self._close_info_box_editor()
                return True
            for rect, kind, key in self._ib_editor_rects:
                if not rect.collidepoint(e.pos):
                    continue
                if kind == 'close':
                    self._close_info_box_editor()
                    return True
                if kind == 'reset':
                    self._ib_set_rows(_default_info_box_rows())
                    self.sim.info_box_bg = False
                    self.sim.info_box_text_shadow = True
                    return True
                if kind == 'bg':
                    self.sim.info_box_bg = not bool(self.sim.info_box_bg)
                    return True
                if kind == 'shadow':
                    self.sim.info_box_text_shadow = not bool(self.sim.info_box_text_shadow)
                    return True
                rows = self._ib_rows()
                if kind in ('on', 'smooth'):
                    for it in rows:
                        if it['key'] == key:
                            it[kind] = not bool(it.get(kind, True))
                    self._ib_set_rows([dict(x) for x in rows])
                    return True
                if kind in ('up', 'down'):
                    idx = next((i for i, x in enumerate(rows) if x['key'] == key), -1)
                    j = idx - 1 if kind == 'up' else idx + 1
                    if idx >= 0 and 0 <= j < len(rows):
                        rows[idx], rows[j] = rows[j], rows[idx]
                        self._ib_set_rows([dict(x) for x in rows])
                    return True
        return True

    def draw_shortcuts_help(self, surface):
        """绘制分类快捷键帮助面板（可滚动，内容按尺寸缓存）。"""
        hx, hy = self._shortcuts_rect.x, self._shortcuts_rect.y
        hw, hh = self._shortcuts_rect.width, self._shortcuts_rect.height
        TITLE_H = DIALOG_TITLE_BAR_HEIGHT
        FOOTER_H = 48
        viewport_y = TITLE_H
        viewport_h = hh - TITLE_H - FOOTER_H

        cache_key = (hw, hh, self.dark_mode)
        if self._shortcuts_cache_key != cache_key:
            self._build_shortcuts_cache(hw, hh)
            self._shortcuts_cache_key = cache_key
        content_surf, total_content_h, panel, fade, fade_h = self._shortcuts_cache

        max_scroll = max(0, total_content_h - viewport_h)
        self._shortcuts_scroll_y = max(0, min(self._shortcuts_scroll_y, max_scroll))
        self._shortcuts_max_scroll = max_scroll

        # 面板底座（标题/边框/底栏缓存）
        surface.blit(panel, (hx, hy))

        # 内容裁剪 blit（滚动在源矩形完成）
        clip_rect = pygame.Rect(0, self._shortcuts_scroll_y, hw, viewport_h)
        surface.blit(content_surf, (hx, hy + viewport_y), clip_rect)

        # 视口底部渐变遮罩
        if max_scroll > 0 and self._shortcuts_scroll_y < max_scroll - 4:
            surface.blit(fade, (hx, hy + viewport_y + viewport_h - fade_h))

        # 滚动条
        if max_scroll > 0:
            SB_W = 8
            SB_MARGIN = 4
            sb_x = hw - SB_W - SB_MARGIN
            sb_track_top = viewport_y + 4
            sb_track_h = viewport_h - 8
            pygame.draw.rect(surface, (210, 215, 225),
                             (hx + sb_x, hy + sb_track_top, SB_W, sb_track_h), 0, 4)
            thumb_h = max(28, sb_track_h * viewport_h / total_content_h)
            thumb_travel = sb_track_h - thumb_h
            thumb_y = sb_track_top + (thumb_travel * self._shortcuts_scroll_y / max_scroll)
            thumb_color = (150, 160, 180) if not self._shortcuts_scrollbar_dragging else (100, 110, 140)
            pygame.draw.rect(surface, thumb_color,
                             (hx + sb_x, hy + int(thumb_y), SB_W, int(thumb_h)), 0, 4)

        # 底栏关闭按钮
        footer_y = hh - FOOTER_H
        close_rect = pygame.Rect(hw // 2 - 50, footer_y + 10, 100, 28)
        close_txt = rt(f_s, "关  闭", (255, 255, 255))
        mx, my = pygame.mouse.get_pos()
        hover = close_rect.collidepoint(mx - hx, my - hy)
        self.draw_button(surface, close_rect.move(hx, hy), close_txt,
                         style='primary', hover=hover)

    def _build_shortcuts_cache(self, hw, hh):
        """一次性构建快捷键面板底座与内容长条（打开/尺寸变化时重建）。"""
        TITLE_H = DIALOG_TITLE_BAR_HEIGHT
        FOOTER_H = 48
        PAD_X = 18
        PAD_TOP = 8
        ROW_H = 28
        CAT_H = 30
        CAT_GAP = 6
        KEY_COL_W = 135
        DESC_X = KEY_COL_W + 14

        panel = pygame.Surface((hw, hh), pygame.SRCALPHA)
        panel.fill((248, 251, 255, 248))
        pygame.draw.rect(panel, BUTTON_BORDER, (0, 0, hw, hh), 2, 10)
        self.draw_title_bar(panel, pygame.Rect(0, 0, hw, TITLE_H),
                            "使用说明 · 快捷键  (Manual & Shortcuts)")
        footer_y = hh - FOOTER_H
        pygame.draw.line(panel, (200, 210, 225), (20, footer_y), (hw - 20, footer_y), 1)

        total_content_h = PAD_TOP
        for _n, _c, items in self.SHORTCUT_SECTIONS:
            total_content_h += CAT_H + CAT_GAP + len(items) * ROW_H
        total_content_h += 12

        content_surf = pygame.Surface((hw, total_content_h), pygame.SRCALPHA)
        y = PAD_TOP
        for cat_name, cat_color, items in self.SHORTCUT_SECTIONS:
            cat_rect = pygame.Rect(PAD_X, y, hw - PAD_X * 2, CAT_H)
            pygame.draw.rect(content_surf, (*cat_color, 140), cat_rect, 0, 5)
            cat_label = rt(f_m, cat_name, cat_color)
            content_surf.blit(cat_label,
                              (cat_rect.x + 10, cat_rect.y + (CAT_H - cat_label.get_height()) // 2))
            y += CAT_H + CAT_GAP
            for key_str, desc_str in items:
                row_y = y + (ROW_H - 22) // 2
                key_surf = rt(f_s, key_str, TXT)
                chip_w = key_surf.get_width() + 16
                chip_x = PAD_X + KEY_COL_W - chip_w - 4
                chip_rect = pygame.Rect(chip_x, row_y, chip_w, 22)
                pygame.draw.rect(content_surf, (*cat_color, 28), chip_rect, 0, 4)
                pygame.draw.rect(content_surf, (*cat_color, 160), chip_rect, 1, 4)
                content_surf.blit(key_surf, (
                    chip_rect.x + (chip_w - key_surf.get_width()) // 2,
                    chip_rect.y + (22 - key_surf.get_height()) // 2))
                desc_surf = rt(f_s, desc_str, TXT)
                content_surf.blit(desc_surf,
                                  (PAD_X + DESC_X, row_y + (22 - desc_surf.get_height()) // 2))
                y += ROW_H
        y += 8
        if y < total_content_h:
            content_surf = content_surf.subsurface((0, 0, hw, y))
            total_content_h = y

        fade_h = 20
        fade = pygame.Surface((hw, fade_h), pygame.SRCALPHA)
        for i in range(fade_h):
            fade.fill((248, 251, 255, int(180 * (i / fade_h))), (0, i, hw, 1))

        self._shortcuts_cache = (content_surf, total_content_h, panel, fade, fade_h)

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

        # ── 信息框自定义面板：拦截所有事件 ──
        if self.show_info_box_editor:
            return self._handle_info_box_editor_event(e)

        # 搜索框(P2-18): 回车跨 tab 搜索 section 名
        sf = getattr(self, 'search_field', None)
        if sf is not None:
            if e.type == pygame.KEYDOWN and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if sf.active:
                    self._do_search(sf.get_text())
                    sf.deactivate()
                    return True
            if sf.handle_event(e):
                return True

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
                self._deactivate_fields()
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
                self._on_reload()
                return True
            # 标题栏按钮（关闭/确认）优先于拖拽
            for rect, callback in self._targets:
                if rect.collidepoint(x, y):
                    for f in self.fields:
                        f.deactivate()
                    callback()
                    return True

        if self.handle_drag_event(e):
            self._sync_field_positions()
            return True

        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if pygame.Rect(self.bg_rect.x + 4, self.bg_rect.y + 90,
                           self.bg_rect.width - 8, self.bg_rect.height - 140).collidepoint(mx, my):
                self._content_scroll_y = max(0, min(
                    self._content_scroll_max,
                    self._content_scroll_y - e.y * 30))
            return True

        if e.type == pygame.MOUSEMOTION and self._sb_dragging:
            track = self._sb_track_rect
            thumb_h = self._sb_thumb_rect.h if self._sb_thumb_rect else 24
            if track is not None and track.h > thumb_h:
                ny = e.pos[1] - self._sb_grab_off
                ratio = (ny - track.y) / (track.h - thumb_h)
                self._content_scroll_y = int(ratio * self._content_scroll_max)
                self._content_scroll_y = max(0, min(self._content_scroll_max,
                                                    self._content_scroll_y))
            return True

        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._sb_dragging = False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            track = self._sb_track_rect
            if track is not None and track.collidepoint(e.pos):
                if self._sb_thumb_rect is not None and self._sb_thumb_rect.collidepoint(e.pos):
                    self._sb_grab_off = e.pos[1] - self._sb_thumb_rect.y
                else:
                    self._sb_grab_off = (self._sb_thumb_rect.h if self._sb_thumb_rect else 24) // 2
                    if track.h > self._sb_grab_off * 2:
                        ny = e.pos[1] - self._sb_grab_off
                        ratio = (ny - track.y) / (track.h - self._sb_grab_off * 2)
                        self._content_scroll_y = int(ratio * self._content_scroll_max)
                        self._content_scroll_y = max(0, min(self._content_scroll_max,
                                                            self._content_scroll_y))
                self._sb_dragging = True
                return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            hit = None
            for i, field in enumerate(self.fields):
                if field.rect.move(0, -self._content_scroll_y).collidepoint(e.pos):
                    hit = i
                    break
            if hit is not None:
                for f in self.fields:
                    f.deactivate()
                self.fields[hit].activate_at(e.pos[0])
                self.current_field = hit
                return True
        for i, field in enumerate(self.fields):
            if field.handle_event(e):
                return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._on_close()
                return True
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                # 有字段聚焦时跳转到下一个输入框；最后一个字段或无聚焦才确认并关闭
                active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                if active_idx != -1 and active_idx + 1 < len(self.fields):
                    self.fields[active_idx].deactivate()
                    self.fields[active_idx + 1].activate()
                    self.current_field = active_idx + 1
                    return True
                if self._has_field_changes():
                    self._needs_save = True
                self.deactivate()
                return True
            elif e.key == pygame.K_TAB:
                if self.fields:
                    active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                    shift = pygame.key.get_mods() & pygame.KMOD_SHIFT
                    nxt = (active_idx + (-1 if shift else 1)) % len(self.fields) if active_idx != -1 else 0
                    if active_idx != -1:
                        self.fields[active_idx].deactivate()
                    self.fields[nxt].activate()
                    self.current_field = nxt
                return True
            elif e.key in (pygame.K_UP, pygame.K_DOWN):
                # F3: ↑/↓ 在字段间移动焦点
                if self.fields:
                    active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                    if active_idx != -1:
                        nxt = active_idx + (1 if e.key == pygame.K_DOWN else -1)
                        if 0 <= nxt < len(self.fields):
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
                    self._content_scroll_y = 0
                    self.tab_index = i
                    # 切 tab 时关闭恢复默认确认条,避免残留遮挡新页(R2-24)
                    self._restore_confirm = False
                    self.rebuild_fields()
                    return True
                x_off += w
            # 下拉列表项优先派发(视觉上盖住下方按钮, 注册顺序却更晚)
            if self._basin_dropdown_open:
                for rect, callback in self._dropdown_targets:
                    if rect.collidepoint(x, y):
                        for f in self.fields:
                            f.deactivate()
                        callback()
                        return True
            # 统一派发其他点击目标
            for rect, callback in self._targets:
                if rect.collidepoint(x, y):
                    for f in self.fields:
                        f.deactivate()
                    callback()
                    return True
            # 洋区下拉框打开时，点击空白处关闭
            if self._basin_dropdown_open:
                self._basin_dropdown_open = False
                return True
        if self.handle_drag_event(e):
            return True
        return False

    def apply_settings(self) -> bool:
        # 先验证所有字段再批量应用，避免部分失败导致状态不一致
        self._error_fields = []
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
                        self._error_fields.append(field)
                        self.sim.show_error(f"无效经度: {val} (需加 E/W 后缀, 180 和 0 除外)")
                        return False
                    validated[key] = parsed
                elif self._is_lat_key(key):
                    parsed = self._parse_lat(val)
                    if parsed is None:
                        self._error_fields.append(field)
                        self.sim.show_error(f"无效纬度: {val} (需加 N/S 后缀, 0 除外)")
                        return False
                    validated[key] = parsed
                elif key == 'span':
                    # 角点+大小模式：地图宽度（°）; 上限 360 避免 Mlo=mlo+span 越界
                    span_v = float(val)
                    if not (0.1 <= span_v <= 360.0):
                        self._error_fields.append(field)
                        self.sim.show_error("跨度 span 需在 0.1~360 之间")
                        return False
                    validated[key] = span_v
                elif key in ('screen_width', 'screen_height'):
                    validated[key] = int(val)
                elif key in ('point_size', 'icon_size', 'name_size', 'peak_label_size'):
                    v = int(val)
                    if v < 1:
                        self._error_fields.append(field)
                        self.sim.show_error(f"{key} 必须为正数")
                        return False
                    validated[key] = v
                elif key in ('mis', 'mas', 'main_rot_speed', 'level3_rot_speed', 'info_box_scale'):
                    validated[key] = float(val)
                elif key == 'smooth_path_segments':
                    validated[key] = int(val)
                elif key == 'volume':
                    validated[key] = float(val) / 100.0
            except ValueError:
                self._error_fields.append(field)
                self.sim.show_error(f"无效数字: {val}")
                return False

        # 批量赋值到 self（settings 本地）
        for key, value in validated.items():
            setattr(self, key, value)
            if key.startswith('ace_'):
                self._ace_changed = True
                # 仅当值与 sim 当前已应用值不同才置位重算：
                # 即时路径(_apply_filter_now)已就地 recalc，若文本未实际改动
                # 则不再二次重算，消除“切模式+确认”路径的双 recalc。
                if value != getattr(self.sim, key, None):
                    self._ace_recalc_dirty = True

        # 角点+大小模式：左下角经/纬 + 跨度 映射回 mlo/mla/Mlo
        if self._map_range_mode == 1:
            if 'bl_lon' in validated:
                self.mlo = validated['bl_lon']
            if 'bl_lat' in validated:
                self.mla = validated['bl_lat']
            if 'span' in validated:
                self.Mlo = min(360.0, self.mlo + validated['span'])

        self.mis = max(0.1, self.mis)
        self.mas = min(20.0, self.mas)
        self.volume = max(0.0, min(1.0, self.volume))
        self.main_rot_speed = max(0.1, min(10.0, self.main_rot_speed))
        self.level3_rot_speed = max(0.1, min(10.0, self.level3_rot_speed))
        self.screen_width = max(800, min(int(self.screen_width), 3840))
        self.screen_height = max(600, min(int(self.screen_height), 2160))
        self.smooth_path_segments = max(1, min(int(self.smooth_path_segments), 200))

        if self.mis >= self.mas:
            self.sim.show_error("最大速度必须大于最小速度")
            return False
        self.mas = max(self.mas, self.mis + 0.1)

        # 地图范围交叉校验（角点模式校验跨度）
        if self._map_range_mode == 0:
            if not self.mlo < self.Mlo:
                self._mark_error_field('mlo', 'Mlo')
                self.sim.show_error("最西经度必须小于最东经度")
                return False
            if not self.mla < self.Mla:
                self._mark_error_field('mla', 'Mla')
                self.sim.show_error("最南纬度必须小于最北纬度")
                return False
        else:
            span = self.Mlo - self.mlo
            if span <= 0.1:
                self._mark_error_field('span')
                self.sim.show_error("地图宽度必须大于 0.1°")
                return False
            # 角点+大小模式无北界输入,但左下纬(mla)仍须小于现存北界(Mla),
            # 否则地图垂直范围失效(该分支此前漏检 mla>=Mla 的非法组合)。
            if not self.mla < self.Mla:
                self._mark_error_field('bl_lat')
                self.sim.show_error("最南纬度必须小于最北纬度")
                return False

        # ACE 经纬范围交叉校验
        if not self.ace_min_lon <= self.ace_max_lon:
            self._mark_error_field('ace_min_lon', 'ace_max_lon')
            self.sim.show_error("ACE 经度下限不能大于上限")
            return False
        if not self.ace_min_lat <= self.ace_max_lat:
            self._mark_error_field('ace_min_lat', 'ace_max_lat')
            self.sim.show_error("ACE 纬度下限不能大于上限")
            return False

        # 同步到 sim（先记录视图相关旧值，仅变化字段置脏）
        old_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                           self.sim.screen_width, self.sim.screen_height)
        old_smooth = self.sim.smooth_path
        old_smooth_mode = getattr(self.sim, 'smooth_path_mode', 'monotone')
        old_smooth_segs = self.sim.smooth_path_segments
        old_path_mode = getattr(self.sim, 'path_mode', 'markers')
        old_future = getattr(self.sim, 'show_future_path', True)
        old_icon_set = self.sim.icon_set
        old_icon_size = self.sim.icon_size
        for name in ('ac', 'mis', 'mas', 'mlo', 'Mlo', 'mla', 'Mla',
                     'show_info_box_normal', 'show_info_box_season',
                     'ace_display_mode', 'volume', 'name_display_mode', 'point_name_mode',
                     'ace_limit_mode', 'ace_limit_basin',
                     'ace_min_lon', 'ace_max_lon', 'ace_min_lat', 'ace_max_lat',
                     'hemisphere', 'point_size', 'icon_size', 'name_size',
                     'peak_label_size', 'info_box_scale', 'fix_icon_point_size', 'disable_dpi_scaling',
                     'fade_typhoon', 'fade_path', 'fade_path_mode', 'smooth_path',
                     'smooth_path_mode', 'smooth_path_segments', 'path_mode', 'ace_interpolated', 'show_fps',
                     'edit_snap_step', 'show_edit_point_labels',
                     'fps_cap', 'show_ri_effect', 'show_future_path', 'monthly_summary',
                     'icon_set', 'color_scheme', 'show_ace_bar', 'show_ace_total',
                     'basin_filter_enabled', 'screen_width', 'screen_height',
                     'landfall_vol_scale', 'show_legend', 'show_graticule',
                     'show_ocean_areas', 'show_coord_hud', 'show_track_label',
                     'normal_other_display'):
            self._sync(name)
        # N3: ace_geo_limit_enabled 仅由 _apply_filter_now 维护,不在 _sync 列表,
        # 防陈旧本地值覆盖 sim 值造成 ACE 口径分裂
        self.sim.ace_geo_limit_enabled = (self.ace_limit_mode != ACE_LIMIT_NONE)
        self.ace_geo_limit_enabled = self.sim.ace_geo_limit_enabled
        self._sync('main_rotation_speed', 'main_rot_speed')
        self._sync('level3_rotation_speed', 'level3_rot_speed')
        self.sim.map_corner_mode = (self._map_range_mode == 1)
        if (old_smooth != self.sim.smooth_path
                or old_smooth_mode != self.sim.smooth_path_mode
                or old_smooth_segs != self.sim.smooth_path_segments):
            self.sim.update_all_screen_points()
        if old_future != getattr(self.sim, 'show_future_path', True):
            self.sim._invalidate_all_path_caches()
        if old_path_mode != getattr(self.sim, 'path_mode', 'markers'):
            # N6: 路径模式变更立即失效路径缓存
            self.sim._invalidate_all_path_caches()
            self.sim.update_all_screen_points()
        if old_icon_set != self.sim.icon_set or old_icon_size != self.sim.icon_size:
            # K21: 图标集切换时释放 SMCY 视频流句柄/缓存(避免泄漏与旧图残留)
            try:
                from .smcy_icon import clear_smcy_cache
                clear_smcy_cache()
            except Exception:
                pass
            self.sim.show_toast("图标设置已保存 — 建议重载数据以应用图标效果", 'success')

        self.sim._apply_basin_filter()

        # ACE 去重：即时应用路径(_cb_ace_limit/盆域下拉→_apply_filter_now)
        # 已就地 recalc_all_ace,不置 _ace_recalc_dirty;仅其他 ACE 相关改动
        # (半球/ace_* 文本字段)置位并需要在此重算;最终数据保证只刷一次。
        if self._ace_recalc_dirty:
            self.sim.recalc_all_ace()
        self._ace_changed = False
        self._ace_recalc_dirty = False

        if self._hemisphere_changed:
            self._hemisphere_changed = False
            if self.sim.md == self.sim.MODE_SEASON:
                sty = self.sim.sty
                h = self.hemisphere
                dt = datetime(sty, 7, 1, 0) if h == HEMISPHERE_SOUTH else datetime(sty, 1, 1, 0)
                self.sim.season_ctrl.jump_to(dt)
                self.sim._sync_season_state()

        new_size = (self.sim.screen_width, self.sim.screen_height)
        if old_view_bounds[4:] != new_size:
            self.sim.handle_resize(*new_size)
            try:
                pygame.display.set_mode(new_size, pygame.RESIZABLE)
            except Exception:
                pass
        else:
            new_view_bounds = (self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla,
                               *new_size)
            if old_view_bounds != new_view_bounds:
                self.sim.map_mgr.update_view()

        self.sim.update_all_screen_points()
        self.sim._config_needs_save = True
        self.sim.save_config()
        return True
