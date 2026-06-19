# py/settings.py
"""设置对话框（三页，经纬度方向后缀，ACE限制模式，洋区下拉框，洋区半球自动切换）。"""
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

    @staticmethod
    def _lon_to_display(val: float) -> str:
        if abs(val - 180.0) < 0.001 or abs(val) < 0.001:
            return str(int(round(val)))
        if val > 180.0:
            return f"{int(round(360.0 - val))}W"
        return f"{int(round(val))}E"

    @staticmethod
    def _lat_to_display(val: float) -> str:
        if abs(val) < 0.001:
            return "0"
        if val > 0:
            return f"{int(round(val))}N"
        return f"{int(round(-val))}S"

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
        self.ace_mode_text = rt(f_s, "ACE显示模式:", TXT)
        self.orig_text = rt(f_s, "原始样式", (255, 255, 255))
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
        self._basin_list = [(a.code, a.name_cn) for a in areas]

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
                ("mlo", self._lon_to_display(self.mlo), (COL_X, dy + 360 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("Mlo", self._lon_to_display(self.Mlo), (COL_X, dy + 390 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("mla", self._lat_to_display(self.mla), (COL_X, dy + 420 - 2, FIELD_W, FIELD_H), lonlat_val),
                ("Mla", self._lat_to_display(self.Mla), (COL_X, dy + 450 - 2, FIELD_W, FIELD_H), lonlat_val),
            ]
        elif self.current_page == 1:
            return []
        else:
            config = []
            if self.ace_limit_mode == ACE_LIMIT_LATLON:
                config = [
                    ("ace_min_lon", self._lon_to_display(self.ace_min_lon), (COL_X, dy + 110 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lon", self._lon_to_display(self.ace_max_lon), (COL_X, dy + 140 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_min_lat", self._lat_to_display(self.ace_min_lat), (COL_X, dy + 170 - 2, FIELD_W, FIELD_H), lonlat_val),
                    ("ace_max_lat", self._lat_to_display(self.ace_max_lat), (COL_X, dy + 200 - 2, FIELD_W, FIELD_H), lonlat_val),
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
        if self.current_page > 0:
            self.draw_text_button(surface, (dx + 80, btn_y, 80, 30), f_s, "上一页", (255, 255, 255), 'light')
        if self.current_page < 2:
            self.draw_text_button(surface, (dx + 170, btn_y, 80, 30), f_s, "下一页", (255, 255, 255), 'light')
        self.draw_text_button(surface, (dx + 260, btn_y, 80, 30), f_s, "确认", (255, 255, 255), 'primary')
        self.draw_text_button(surface, (dx + 350, btn_y, 80, 30), f_s, "取消", (255, 255, 255), 'light')

        self.draw_shortcuts_btn(surface, dx, dy, dw)
        if self.show_shortcuts:
            self.draw_shortcuts_help(surface)

    def draw_shortcuts_btn(self, surface, dx, dy, dw):
        btn_x = dx + dw - 200
        btn_y = dy + 10
        self.draw_text_button(surface, (btn_x, btn_y, 70, 22), f_s, "快捷键", (255, 255, 255), 'light')
        self.draw_text_button(surface, (btn_x + 78, btn_y, 80, 22), f_s, "重载数据", (255, 255, 255), 'primary')
        self._shortcuts_btn_rect = pygame.Rect(btn_x, btn_y, 70, 22)
        self._reload_btn_rect = pygame.Rect(btn_x + 78, btn_y, 80, 22)

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

        surface.blit(self.ace_mode_text, (dx + 40, dy + 300))
        ace_orig = pygame.Rect(dx + 150, dy + 295, 100, 25)
        ace_prog = pygame.Rect(dx + 260, dy + 295, 100, 25)
        self._draw_toggle_button(surface, ace_orig, self.orig_text, self.ace_display_mode == "original")
        self._draw_toggle_button(surface, ace_prog, self.prog_text, self.ace_display_mode == "progress_bar")

        surface.blit(self.name_mode_text, (dx + 40, dy + 330))
        for i, mode in enumerate(self.name_modes):
            rect = pygame.Rect(dx + 150 + i * 120, dy + 325, 100, 25)
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

    def draw_shortcuts_help(self, surface):
        hw, hh = 700, 660
        hx, hy = (self.sim.screen_width - hw) // 2, (self.sim.screen_height - hh) // 2
        help_surf = pygame.Surface((hw, hh), pygame.SRCALPHA)
        help_surf.fill((255, 255, 255, 230))
        pygame.draw.rect(help_surf, BUTTON_BORDER, (0, 0, hw, hh), 2, 10)

        title = rt(f_m, "键盘快捷键帮助", TXT)
        help_surf.blit(title, (hw // 2 - title.get_width() // 2, 20))
        shortcuts = [
            ("空格键", "播放/暂停"), ("R Ctrl", "重置地图"), ("R", "重置视图到配置"),
            ("O", "打开台风列表"), ("S", "打开设置"), ("H", "切换编辑模式"),
            ("G", "编辑当前台风点列表 (编辑模式)"), ("T / J", "时间跳跃 (台风季模式)"),
            ("X", "重置速度到1x"), ("+ / =", "增加播放速度"), ("-", "减小播放速度"),
            ("左箭头", "速度减半"), ("右箭头", "速度加倍"),
            ("[", "上一个台风 (正常模式)"), ("]", "下一个台风 (正常模式)"),
            ("I", "新建台风 (编辑模式)"), ("F12", "切换窗口置顶状态"),
            ("Ctrl+Z", "撤销 (编辑模式)"), ("Ctrl+Y", "重做 (编辑模式)"),
            ("Ctrl+R", "重载台风数据"), ("ESC", "退出当前对话框/菜单")
        ]
        y_offset = 60
        for key, desc in shortcuts:
            key_surf = rt(f_s, key, BUTTON_BORDER)
            desc_surf = rt(f_s, desc, TXT)
            help_surf.blit(key_surf, (150 - key_surf.get_width() - 10, y_offset))
            help_surf.blit(desc_surf, (150, y_offset))
            y_offset += 25
        close_btn = pygame.Rect(hw // 2 - 60, hh - 50, 120, 30)
        btn_surf = pygame.Surface((120, 30), pygame.SRCALPHA)
        btn_surf.fill(BUTTON_BORDER)
        close_txt = rt(f_s, "关闭", (255, 255, 255))
        btn_surf.blit(close_txt, (60 - close_txt.get_width() // 2, 15 - close_txt.get_height() // 2))
        help_surf.blit(btn_surf, close_btn)
        surface.blit(help_surf, (hx, hy))

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

        dragged = self.handle_drag_event(e)
        if self.show_shortcuts:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                hw, hh = 700, 660
                hx, hy = (self.sim.screen_width - hw) // 2, (self.sim.screen_height - hh) // 2
                close_rect = pygame.Rect(hx + hw//2 - 60, hy + hh - 50, 120, 30)
                if close_rect.collidepoint(e.pos):
                    self.show_shortcuts = False
                    return True
            return False
        if dragged:
            self._sync_field_positions()

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

            if hasattr(self, '_shortcuts_btn_rect') and self._shortcuts_btn_rect.collidepoint(x, y):
                self.show_shortcuts = True
                return True
            if hasattr(self, '_reload_btn_rect') and self._reload_btn_rect.collidepoint(x, y):
                self._sync_ace_settings_to_sim()
                self.sim.reload_typhoons()
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
                if pygame.Rect(dx + 150, dy + 295, 100, 25).collidepoint(x, y):
                    self.ace_display_mode = "original"
                    return True
                if pygame.Rect(dx + 260, dy + 295, 100, 25).collidepoint(x, y):
                    self.ace_display_mode = "progress_bar"
                    return True
                for i in range(3):
                    if pygame.Rect(dx + 150 + i * 120, dy + 325, 100, 25).collidepoint(x, y):
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
                    area = self.sim.res_mgr.ocean_areas.get_by_code(self.ace_limit_basin)
                    if area:
                        new_hemi = HEMISPHERE_NORTH if area.hemisphere == 'N' else HEMISPHERE_SOUTH
                        if self.hemisphere != new_hemi:
                            self.hemisphere = new_hemi
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

        # 同步到 sim
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

        if self._ace_changed:
            self.sim.recalc_all_ace()
            self._ace_changed = False

        self.sim.map_mgr.update_view()
        self.sim.update_all_screen_points()
        self.sim._config_needs_save = True
        self.sim.save_config()