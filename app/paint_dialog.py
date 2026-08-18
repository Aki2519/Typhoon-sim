# py/paint_dialog.py
"""绘画模式对话框: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘画窗口。

功能栏最右侧「绘画」按钮点击后弹出此窗口。包含:
  - 文件选择(勾选要绘画的台风)
  - 范围: 普通(自动) / 区域(上,左,高,宽, 含常用区域预设) / 全局
  - 参数: 色阶方案(应用/经典Wiki/2023新版)/路径宽度/定点大小/半宽半高留空/
          最小宽高/绘画顺序/忽略前期扰动/显示所有定位点/经纬网/风圈/多文件重置/文字标注
  - 图例位置(不/左上/左下/右上/右下/自动) + 背景色/地图底图
  - 开始绘画 / 保存路径图 / 清除当前显示 / 关闭
"""
from __future__ import annotations

import os
import time
import pygame
from typing import List, Optional

from .constants import (
    f_s, f_m, rt, TXT, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM, DIALOG_TITLE_BAR_HEIGHT,
)
from .dialog_base import DraggableDialog
from .input_field import InputField
from .paint_render import TrackMapRenderer, COMMON_REGIONS, load_map_image
from .constants.colors import (
    LEGEND_POS_NAMES, COLOR_SCALE_SCHEMES, WIKI_LEGEND_ENTRIES,
)

DIALOG_W = 1180
DIALOG_H = 790
PAD = 14
ROW_H = 24
ROW_GAP = 5


def _fmt_region(region):
    top, left, height, width = region
    return f"{top:g},{left:g},{height:g},{width:g}"


def _col_c(dark: bool, bright: bool = False):
    if not dark:
        return (10, 20, 40) if not bright else (30, 80, 160)
    return SETTINGS_TEXT_LIGHT if bright else SETTINGS_TEXT_DIM


def _key_values(key):
    if key == "scheme":
        return list(COLOR_SCALE_SCHEMES.keys())
    if key == "order":
        return ["simult", "sort", "points_top"]
    if key == "draw_rad":
        return ["none", "all", "last", "tropical"]
    if key == "multireset":
        return ["none", "auto", "last"]
    if key == "legend_pos":
        return list(LEGEND_POS_NAMES.keys())
    if key == "bg":
        return ["map", "def", "custom"]
    if key == "coord_unit":
        return ["deg", "px"]
    if key == "map":
        return [True, False]
    return []


_BG_COLOR_SEQ = [(0, 0, 0), (200, 220, 240), (255, 255, 255),
                 (30, 60, 110), (140, 180, 230), (25, 28, 35),
                 (90, 150, 90), (150, 90, 90)]


class PaintDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title = rt(f_m, "绘画模式", TXT)
        self.title_dark = rt(f_m, "绘画模式", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

        # ── 参数状态 ──
        self.scheme = "wiki"                    # app / wiki / 2023
        self.path_width = 2                     # px
        self.point_size = 5                     # px
        self.margin_w = 0.0                     # 半宽留空(°)
        self.margin_h = 0.0                     # 半高留空(°)
        self.min_w = 1920                       # 最小宽度 px
        self.min_h = 1080                       # 最小高度 px
        self.draw_order = "simult"              # simult/sort/points_top
        self.skip_invest = False
        self.all_points = True
        self.grid = False
        self.draw_rad = "none"                  # none/all/last/tropical
        self.multireset = "none"
        self.legend_pos = "auto"
        self.labels = False
        self.bg_color = None                    # None -> 地图/底色
        self.use_map = True                     # 地图底图开关
        self.coord_unit = "deg"                 # deg(经纬度)/px(像素)
        self.mode = "normal"                    # normal/region/global
        self.region = COMMON_REGIONS["西北太平洋 WPAC"]   # 上,左,高,宽
        self.region_preset = "西北太平洋 WPAC"
        self.map_path = None                    # 自定义地图(缺省全局地图)

        # ── 文件选择 ──
        self.selected: List[int] = []           # sim.tys 索引
        self._file_scroll = 0

        # ── 输入框 ──
        self._fields: dict = {}
        self._field_rects: dict = {}

        # ── 渲染结果 ──
        self._result: Optional[pygame.Surface] = None
        self._status = ""
        self._status_ts = 0.0

        self.x0 = 0
        self.y0 = 0

    # ── 激活/关闭 ──

    def activate(self):
        if not self.selected:
            self.selected = list(range(len(self.sim.tys)))
        self._build_layout()
        super().activate()

    def deactivate(self):
        for f in self._fields.values():
            f.deactivate()
        super().deactivate()

    def _build_layout(self):
        sw, sh = self.sim.screen_width, self.sim.screen_height
        w, h = min(DIALOG_W, sw - 20), min(DIALOG_H, sh - 20)
        self.bg_rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        self.x0 = self.bg_rect.x + PAD
        self.y0 = self.bg_rect.y + self.title_bar_height + 8
        self._rebuild_fields()

    def _rebuild_fields(self):
        for f in self._fields.values():
            f.deactivate()
        self._fields.clear()
        self._field_rects.clear()
        colx = self.x0 + 250
        rows_y = [
            ("path_width", self._row_y(4), f"{self.path_width}", 5, int),
            ("point_size", self._row_y(5), f"{self.point_size}", 5, int),
            ("margin_w", self._row_y(6), f"{self.margin_w:g}", 7, float),
            ("margin_h", self._row_y(7), f"{self.margin_h:g}", 7, float),
            ("min_w", self._row_y(8), f"{self.min_w}", 5, int),
            ("min_h", self._row_y(9), f"{self.min_h}", 5, int),
            ("region", self._row_y(1), _fmt_region(self.region), 24, None),
            ("map_path", self._row_y(19), self.map_path or "", 200, None),
        ]
        for key, y, val, mlen, conv in rows_y:
            rect = pygame.Rect(colx, y, 110, 22)
            if key == "map_path":
                rect = pygame.Rect(self.x0 + 70, y, 460, 22)
            fld = InputField(rect, max_length=mlen, dark=self.dark_mode)
            fld.set_text(val)
            fld.key = key
            self._fields[key] = fld
            self._field_rects[key] = rect

    def _row_y(self, idx: int) -> int:
        return self.y0 + 4 + idx * (ROW_H + ROW_GAP)

    # ── 几何助手 ──

    def _left_x(self, offset=0):
        return self.x0 + offset

    def _preset_button_rect(self):
        return pygame.Rect(self.x0 + 250 + 116, self._row_y(1), 164, 22)

    def _mode_btn_rect(self, mode):
        names = {"normal": 0, "region": 1, "global": 2}
        x = self.x0 + names[mode] * 96
        return pygame.Rect(x, self._row_y(0), 90, 24)

    def _file_list_rect(self):
        # 右侧文件列表: 自右列顶到预览区前
        x = self.x0 + 540
        y = self.y0
        w = self.bg_rect.right - PAD - x
        h = 300
        return pygame.Rect(x, y, w, h)

    def _sel_all_rect(self):
        fl = self._file_list_rect()
        return pygame.Rect(fl.x, fl.y - 24, 64, 20)

    def _unsel_all_rect(self):
        fl = self._file_list_rect()
        return pygame.Rect(fl.x + 70, fl.y - 24, 72, 20)

    def _preview_rect(self):
        fl = self._file_list_rect()
        x = fl.x
        y = fl.bottom + 8
        w = fl.width
        h = self.bg_rect.bottom - 56 - y
        return pygame.Rect(x, y, w, max(40, h))

    def _action_button_rects(self):
        base_y = self.bg_rect.bottom - 38
        btns = {}
        x = self.bg_rect.right - PAD - 70
        for key, label in (("close", "关闭"), ("clear", "清除"), ("save", "保存"), ("draw", "开始绘画")):
            w = 92 if key == "draw" else 70
            x -= w
            btns[key] = pygame.Rect(x, base_y, w, 26)
            x -= 8
        return btns

    # ── 循环控件定义 (label, text, getter, setter, rectfn) ──

    def _cycle_controls(self):
        by = self._row_y
        c = {}
        c["scheme"] = ("scheme", self._cycle_text("scheme"),
                       lambda: self.scheme,
                       lambda v: setattr(self, "scheme", v),
                       lambda: pygame.Rect(self.x0, by(12), 250, 24))
        c["order"] = ("order", self._cycle_text("order"),
                      lambda: self.draw_order,
                      lambda v: setattr(self, "draw_order", v),
                      lambda: pygame.Rect(self.x0, by(13), 250, 24))
        c["draw_rad"] = ("draw_rad", self._cycle_text("draw_rad"),
                         lambda: self.draw_rad,
                         lambda v: setattr(self, "draw_rad", v),
                         lambda: pygame.Rect(self.x0, by(14), 250, 24))
        c["multireset"] = ("multireset", self._cycle_text("multireset"),
                           lambda: self.multireset,
                           lambda v: setattr(self, "multireset", v),
                           lambda: pygame.Rect(self.x0, by(15), 250, 24))
        c["coord_unit"] = ("coord_unit", self._cycle_text("coord_unit"),
                           lambda: self.coord_unit,
                           lambda v: setattr(self, "coord_unit", v),
                           lambda: pygame.Rect(self.x0, by(16), 250, 24))
        c["legend_pos"] = ("legend_pos", self._cycle_text("legend_pos"),
                           lambda: self.legend_pos,
                           lambda v: setattr(self, "legend_pos", v),
                           lambda: pygame.Rect(self.x0 + 278, by(12), 236, 24))
        c["bg"] = ("bg", self._cycle_text("bg"),
                   lambda: self._bg_key(),
                   lambda v: self._set_bg(v),
                   lambda: pygame.Rect(self.x0 + 278, by(13), 236, 24))
        c["map"] = ("map", "地图底图: " + ("开" if self.use_map else "关"),
                    lambda: self.use_map,
                    lambda v: setattr(self, "use_map", v),
                    lambda: pygame.Rect(self.x0 + 278, by(14), 236, 24))
        return c

    def _cycle_text(self, key):
        if key == "scheme":
            return COLOR_SCALE_SCHEMES[self.scheme]
        if key == "order":
            return {"simult": "同时", "sort": "按强度排序", "points_top": "定位点置顶"}[self.draw_order]
        if key == "draw_rad":
            return {"none": "不绘画", "all": "全部绘画", "last": "仅最后定位",
                    "tropical": "仅热带部分"}[self.draw_rad]
        if key == "multireset":
            return {"none": "不重置", "auto": "自动重置", "last": "仅最后定位"}[self.multireset]
        if key == "legend_pos":
            return LEGEND_POS_NAMES[self.legend_pos]
        if key == "bg":
            return {"map": "地图底图", "def": "默认底色", "custom": "自定义背景"}[self._bg_key()]
        if key == "coord_unit":
            return "经纬度(°)" if self.coord_unit == "deg" else "像素(px)"
        return ""

    def _bg_key(self):
        if self.use_map and self.bg_color is None:
            return "map"
        if self.bg_color is None:
            return "def"
        return "custom"

    def _set_bg(self, v):
        if v == "map":
            self.bg_color = None
            self.use_map = True
        elif v == "def":
            self.bg_color = None
            self.use_map = False
        else:
            if self._bg_key() == "custom":
                cur = _BG_COLOR_SEQ.index(self.bg_color) if self.bg_color in _BG_COLOR_SEQ else 0
                self.bg_color = _BG_COLOR_SEQ[(cur + 1) % len(_BG_COLOR_SEQ)]
            else:
                self.bg_color = _BG_COLOR_SEQ[0]
                self.use_map = False

    # ── 事件 ──

    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True

        for f in self._fields.values():
            if f.handle_event(e):
                return True

        if e.type == pygame.MOUSEBUTTONDOWN:
            if e.button in (4, 5):
                self._file_scroll = max(0, self._file_scroll + (-1 if e.button == 4 else 1))
                return True
            if e.button == 1:
                self._click(e.pos)
            return True
        return False

    def _click(self, pos):
        x, y = pos
        # 动作按钮
        for key, rect in self._action_button_rects().items():
            if rect.collidepoint(x, y):
                self._on_action(key)
                return
        # 模式组
        for mode in ("normal", "region", "global"):
            if self._mode_btn_rect(mode).collidepoint(x, y):
                self.mode = mode
                return
        # 区域预设
        if self._preset_button_rect().collidepoint(x, y):
            names = list(COMMON_REGIONS.keys())
            cur = names.index(self.region_preset) if self.region_preset in names else 0
            self.region_preset = names[(cur + 1) % len(names)]
            self.region = COMMON_REGIONS[self.region_preset]
            f = self._fields.get("region")
            if f is not None:
                f.set_text(_fmt_region(self.region))
            return
        # 勾选项
        if self._checkbox_click(x, y):
            return
        # 循环按钮
        for key, (label, text, getter, setter, rectfn) in self._cycle_controls().items():
            if rectfn().collidepoint(x, y):
                cur = getter()
                vals = _key_values(key)
                nxt = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0]
                setter(nxt)
                return
        # 文件列表
        fl = self._file_list_rect()
        if fl.collidepoint(x, y):
            row_h = 20
            idx = self._file_scroll + (y - fl.y) // row_h
            if 0 <= idx < len(self.sim.tys):
                if idx in self.selected:
                    self.selected.remove(idx)
                else:
                    self.selected.append(idx)
            return
        # 全选/全不选
        if self._sel_all_rect().collidepoint(x, y):
            self.selected = list(range(len(self.sim.tys)))
            return
        if self._unsel_all_rect().collidepoint(x, y):
            self.selected = []
            return

    def _checkbox_click(self, x, y):
        chk = [
            ("skip_invest", self.skip_invest),
            ("all_points", self.all_points),
            ("grid", self.grid),
            ("labels", self.labels),
        ]
        for i, (key, val) in enumerate(chk):
            box = pygame.Rect(self.x0 + 278, self._row_y(15 + i), 16, 16)
            if box.collidepoint(x, y):
                setattr(self, key, not val)
                return True
        return False

    # ── 动作 ──

    def _on_action(self, key):
        if key == "draw":
            self._do_render()
        elif key == "save":
            self._do_save()
        elif key == "clear":
            self._result = None
            self._status = "已清除当前显示"
            self._status_ts = time.time()
        elif key == "close":
            self.deactivate()

    def _selected_tys(self):
        return [self.sim.tys[i] for i in self.selected if 0 <= i < len(self.sim.tys)]

    def _collect_fields(self) -> dict:
        out = {}
        conv = {"path_width": int, "point_size": int, "min_w": int, "min_h": int,
                "margin_w": float, "margin_h": float}
        for key, f in self._fields.items():
            txt = f.get_text().strip()
            if key == "region":
                try:
                    nums = [float(x.strip()) for x in txt.replace("，", ",").split(",") if x.strip()]
                    if len(nums) == 4:
                        out["region"] = (nums[0], nums[1], nums[2], nums[3])
                except ValueError:
                    pass
                continue
            if key == "map_path":
                out["map_path"] = txt or None
                continue
            cfn = conv.get(key)
            if cfn is None or not txt:
                continue
            try:
                out[key] = cfn(txt)
            except ValueError:
                pass
        for k, d in (("path_width", self.path_width), ("point_size", self.point_size),
                     ("margin_w", self.margin_w), ("margin_h", self.margin_h),
                     ("min_w", self.min_w), ("min_h", self.min_h)):
            if out.get(k) is None:
                out[k] = d
        return out

    def _do_render(self):
        tys = self._selected_tys()
        vals = self._collect_fields()
        region = vals.get("region") or self.region
        if self.mode == "region":
            self.region = region
        map_path = (vals.get("map_path") or self.map_path) if self.use_map else None
        try:
            renderer = TrackMapRenderer({
                "scheme": self.scheme,
                "path_width": max(1, vals.get("path_width", self.path_width)),
                "point_size": max(1, vals.get("point_size", self.point_size)),
                "margin_w": vals.get("margin_w", self.margin_w),
                "margin_h": vals.get("margin_h", self.margin_h),
                "coord_unit": self.coord_unit,
                "min_w": max(320, vals.get("min_w", self.min_w)),
                "min_h": max(200, vals.get("min_h", self.min_h)),
                "draw_order": self.draw_order,
                "skip_invest": self.skip_invest,
                "all_points": self.all_points,
                "grid": self.grid,
                "draw_rad": self.draw_rad,
                "multireset": self.multireset,
                "legend_pos": self.legend_pos,
                "legend_entries": WIKI_LEGEND_ENTRIES,
                "bg_color": None if self.use_map else self.bg_color,
                "map_path": map_path,
                "labels": self.labels,
                "font": f_s,
            })
            map_surf = load_map_image(map_path) if self.use_map else None
            self._result = renderer.render(tys, mode=self.mode, region=region,
                                           map_surface=map_surf)
            n = len([ty for ty in tys if ty.pts])
            self._status = f"绘画完成: {n} 条路径, {self._result.get_width()}×{self._result.get_height()}"
        except Exception as ex:
            self._status = f"绘画失败: {ex}"
        self._status_ts = time.time()

    def _do_save(self):
        if self._result is None:
            self._status = "请先生成路径图"
            self._status_ts = time.time()
            return
        try:
            pic_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "picture")
            os.makedirs(pic_dir, exist_ok=True)
            fn = os.path.join(pic_dir, time.strftime("trackmap_%Y%m%d_%H%M%S.png"))
            pygame.image.save(self._result, fn)
            self._status = f"已保存: {fn}"
            self.sim.show_error(f"路径图已保存: {fn}")
        except Exception as ex:
            self._status = f"保存失败: {ex}"
        self._status_ts = time.time()

    # ── 绘制 ──

    def draw(self, surface):
        if not self.active:
            return
        dark = self.dark_mode
        r = self.bg_rect
        if dark:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
            self.draw_title_bar(surface, r, "绘画模式", SETTINGS_TEXT_LIGHT)
        else:
            self.draw_background(surface, r)
            self.draw_title_bar(surface, r, "绘画模式", TXT)

        self._draw_left(surface, dark)
        self._draw_file_list(surface, dark)
        self._draw_preview(surface, dark)
        self._draw_action_buttons(surface, dark)
        self._draw_status(surface, dark)

    def _draw_field(self, surface, key, dark):
        f = self._fields.get(key)
        if f is not None:
            f.draw(surface, y_offset=0)

    def _draw_left(self, surface, dark):
        by = self._row_y
        # 范围模式组
        t = rt(f_s, "范围", _col_c(dark, True))
        surface.blit(t, (self.x0, by(0) - 22))
        for mode, label in (("normal", "普通"), ("region", "区域"), ("global", "全局")):
            r = self._mode_btn_rect(mode)
            selected = self.mode == mode
            bg = (70, 130, 200) if selected else ((50, 55, 68) if dark else (200, 210, 230))
            tc = (255, 255, 255) if selected or dark else TXT
            pygame.draw.rect(surface, bg, r, border_radius=5)
            ts = rt(f_s, label, tc)
            surface.blit(ts, (r.centerx - ts.get_width() // 2, r.centery - ts.get_height() // 2))
        # 区域 + 预设
        self._label(surface, "区域(上,左,高,宽)", self.x0, by(1), dark)
        pb = self._preset_button_rect()
        pygame.draw.rect(surface, (55, 60, 75) if dark else (210, 220, 240), pb, border_radius=5)
        pts = rt(f_s, "预设: " + self.region_preset, _col_c(dark))
        surface.blit(pts, (pb.x + 4, pb.centery - pts.get_height() // 2))
        # 参数标题
        self._label(surface, "参数", self.x0, by(3) - 22, dark)
        rows = [
            ("路径宽度(px)", "path_width"),
            ("定点大小(px)", "point_size"),
            ("半宽留空(°)", "margin_w"),
            ("半高留空(°)", "margin_h"),
            ("最小宽度(px)", "min_w"),
            ("最小高度(px)", "min_h"),
        ]
        for i, (lab, key) in enumerate(rows):
            self._label(surface, lab, self.x0, by(4 + i), dark)
        # 所有输入框统一绘制
        for f in self._fields.values():
            f.draw(surface, y_offset=0)
        # 循环按钮
        cyc = [
            ("scheme", self._cycle_text("scheme"), self._row_y(12)),
            ("order", self._cycle_text("order"), self._row_y(13)),
            ("draw_rad", self._cycle_text("draw_rad"), self._row_y(14)),
            ("multireset", self._cycle_text("multireset"), self._row_y(15)),
            ("coord_unit", self._cycle_text("coord_unit"), self._row_y(16)),
        ]
        for i, (key, text, yy) in enumerate(cyc):
            lab = {"scheme": "色阶", "order": "绘画顺序", "draw_rad": "风圈",
                   "multireset": "多文件重置", "coord_unit": "坐标单位"}[key]
            self._label(surface, lab, self.x0, yy - 20, dark)
            rect = pygame.Rect(self.x0, yy, 250, 24)
            pygame.draw.rect(surface, (55, 60, 75) if dark else (210, 220, 240), rect, border_radius=5)
            ts = rt(f_s, text, _col_c(dark))
            surface.blit(ts, (rect.x + 8, rect.centery - ts.get_height() // 2))
        # 右列循环按钮 (图例/背景/地图)
        cyc2 = [
            ("legend_pos", self._cycle_text("legend_pos"), self._row_y(12), "图例位置"),
            ("bg", self._cycle_text("bg"), self._row_y(13), "背景"),
            ("map", "地图底图: " + ("开" if self.use_map else "关"), self._row_y(14), "地图底图"),
        ]
        for key, text, yy, lab in cyc2:
            self._label(surface, lab, self.x0 + 278, yy - 20, dark)
            rect = pygame.Rect(self.x0 + 278, yy, 236, 24)
            pygame.draw.rect(surface, (55, 60, 75) if dark else (210, 220, 240), rect, border_radius=5)
            ts = rt(f_s, text, _col_c(dark))
            surface.blit(ts, (rect.x + 8, rect.centery - ts.get_height() // 2))
        # 勾选项
        chk = [
            ("skip_invest", "忽略前期扰动定位", self.skip_invest),
            ("all_points", "显示所有定位点", self.all_points),
            ("grid", "显示经纬网", self.grid),
            ("labels", "显示文字标注", self.labels),
        ]
        for i, (key, lab, val) in enumerate(chk):
            self._checkbox(surface, pygame.Rect(self.x0 + 278, self._row_y(15 + i), 16, 16),
                           lab, val, dark)
        # 自定义地图文件
        self._label(surface, "地图文件", self.x0, self._row_y(19), dark)

    def _checkbox(self, surface, box, label, val, dark):
        pygame.draw.rect(surface, (55, 60, 75) if dark else (210, 220, 240), box, border_radius=3)
        if val:
            pygame.draw.line(surface, (90, 220, 130), (box.x + 3, box.centery),
                             (box.x + box.w // 2, box.bottom - 3), 2)
            pygame.draw.line(surface, (90, 220, 130), (box.x + box.w // 2, box.bottom - 3),
                             (box.right - 2, box.y + 3), 2)
        ts = rt(f_s, label, _col_c(dark))
        surface.blit(ts, (box.right + 6, box.y + (box.h - ts.get_height()) // 2))

    def _draw_file_list(self, surface, dark):
        fl = self._file_list_rect()
        pygame.draw.rect(surface, (45, 50, 62) if dark else (230, 238, 248), fl, border_radius=6)
        pygame.draw.rect(surface, (70, 80, 100) if dark else (170, 185, 205), fl, 1, border_radius=6)
        t = rt(f_s, f"文件选择 ({len(self.selected)}/{len(self.sim.tys)})", _col_c(dark, True))
        surface.blit(t, (fl.x, fl.y - 20))
        for rect, lab, color in ((self._sel_all_rect(), "全选", (80, 160, 120)),
                                 (self._unsel_all_rect(), "全不选", (160, 120, 120))):
            pygame.draw.rect(surface, color, rect, border_radius=3)
            ts = rt(f_s, lab, (255, 255, 255))
            surface.blit(ts, (rect.centerx - ts.get_width() // 2, rect.centery - ts.get_height() // 2))
        row_h = 20
        y = fl.y + 1
        n = len(self.sim.tys)
        visible = max(1, fl.height // row_h)
        total = len(self.sim.tys)
        if total > visible:
            bar_h = max(18, int(fl.height * visible / total))
            bar_y = fl.y + int((fl.height - bar_h) *
                               (self._file_scroll / max(1, total - visible)))
            pygame.draw.rect(surface, (90, 100, 120) if dark else (150, 165, 185),
                             (fl.right - 6, bar_y, 4, bar_h))
        for idx in range(self._file_scroll, min(n, self._file_scroll + visible)):
            yy = y + (idx - self._file_scroll) * row_h
            if yy + row_h > fl.bottom:
                break
            sel = idx in self.selected
            if sel:
                pygame.draw.rect(surface, (60, 100, 150) if dark else (190, 215, 240),
                                 (fl.x, yy, fl.width, row_h - 1))
            mark = "☑ " if sel else "☐ "
            ty = self.sim.tys[idx]
            ts = rt(f_s, mark + self.sim.get_display_name(ty), _col_c(dark))
            surface.blit(ts, (fl.x + 6, yy + (row_h - ts.get_height()) // 2))

    def _draw_preview(self, surface, dark):
        pr = self._preview_rect()
        pygame.draw.rect(surface, (30, 34, 44) if dark else (210, 220, 235), pr, border_radius=6)
        pygame.draw.rect(surface, (80, 92, 115) if dark else (150, 165, 190), pr, 1, border_radius=6)
        if self._result is not None:
            img = self._result
            if pr.width > 8 and pr.height > 8:
                sc = min(pr.width / img.get_width(), pr.height / img.get_height())
                tw = max(1, int(img.get_width() * sc))
                th = max(1, int(img.get_height() * sc))
                prev = pygame.transform.smoothscale(img, (tw, th))
                surface.blit(prev, (pr.centerx - tw // 2, pr.centery - th // 2))
        else:
            ts = rt(f_s, "点击「开始绘画」生成路径图", _col_c(dark))
            surface.blit(ts, (pr.centerx - ts.get_width() // 2, pr.centery - ts.get_height() // 2))

    def _draw_action_buttons(self, surface, dark):
        colors = {"draw": (70, 160, 110), "save": (70, 120, 180),
                  "clear": (160, 120, 80), "close": (140, 90, 90)}
        labels = {"draw": "开始绘画", "save": "保存路径图", "clear": "清除", "close": "关闭"}
        for key, rect in self._action_button_rects().items():
            pygame.draw.rect(surface, colors.get(key, (90, 100, 120)), rect, border_radius=5)
            ts = rt(f_s, labels[key], (255, 255, 255))
            surface.blit(ts, (rect.centerx - ts.get_width() // 2, rect.centery - ts.get_height() // 2))

    def _draw_status(self, surface, dark):
        if self._status and time.time() - self._status_ts < 6:
            ts = rt(f_s, self._status, (230, 200, 90) if dark else (150, 100, 30))
            surface.blit(ts, (self.x0, self.bg_rect.bottom - 62))

    def _label(self, surface, text, x, y, dark):
        ts = rt(f_s, text, _col_c(dark))
        surface.blit(ts, (x, y + (ROW_H - ts.get_height()) // 2))
