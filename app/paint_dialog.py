# py/paint_dialog.py
"""绘画模式对话框: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘画窗口。

功能栏最右侧「绘画」按钮点击后弹出此窗口。窗口尺寸放大(接近风季统计界面),
控件间距/字号放大, 包含:
  - 管理文件列表(右列, 与 Ty4「管理文件列表」相同):
      顺序 / 文件名 / 所在路径 / 类型 / 文件尺寸 列;
      添加(打开文件) / 移除 / 去重 / 清空 / 刷新 / 全选 / 全不选 / 一键加入全部台风
      * 打开时默认不选择任何台风, 用「一键全部」或勾选加入
  - 导入台风文件(Ty4「打开文件」界面): FileBrowserDialog(purpose=typhoon)
  - 地图文件选择(Ty4「更改地形图」): FileBrowserDialog(purpose=map), 支持文件管理器选取
  - 范围: 普通(自动) / 区域(上,左,高,宽, 含常用区域预设) / 全局
  - 参数: 色阶(应用/经典Wiki/2023新版)/路径宽度/定点大小/半宽半高留空/最小宽高/
          坐标单位/绘画顺序/忽略前期扰动/所有定位点/经纬网/风圈/多文件重置/文字标注
  - 图例位置(不/左上/左下/右上/右下/自动) + 背景色/地图底图
  - 开始绘画 / 保存路径图 / 清除 / 关闭
  - 单击地图预览 -> 打开放大查看(比例 1:1 显示 + 滚轮平移)
  - 统计输出: 每台风 ACE / 路径长度(km) / 定位点数
"""
from __future__ import annotations

import os
import time
import pygame
from typing import List, Optional

from .constants import (
    f_s, f_m, f_l, rt, TXT, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_ACCENT, DIALOG_TITLE_BAR_HEIGHT,
)
from .dialog_base import DraggableDialog
from .input_field import InputField
from .paint_render import (
    TrackMapRenderer, COMMON_REGIONS, load_map_image,
    compute_track_stats,
)
from .paint_file_browser import FileBrowserDialog, EXT_MAP
from .constants.colors import (
    LEGEND_POS_NAMES, COLOR_SCALE_SCHEMES, WIKI_LEGEND_ENTRIES,
)

# 窗口尺寸(放大, 接近风季统计界面, 受屏幕约束)
DIALOG_W = 1720
DIALOG_H = 1180
PAD = 20
ROW_H = 30
ROW_GAP = 4
# 底部预留(预览大条 + 右侧统计列 + 动作按钮)
BOTTOM_BAR_H = 430
PREVIEW_H = 350
STATS_W = 420


def _fmt_region(region):
    top, left, height, width = region
    return f"{top:g},{left:g},{height:g},{width:g}"


def _light(dark: bool, bright: bool = False):
    if not dark:
        return (25, 32, 48) if not bright else (30, 80, 160)
    return SETTINGS_TEXT_LIGHT if bright else (190, 198, 212)


def _dark_bg(dark: bool, tone=0):
    if not dark:
        return ((210, 220, 234), (225, 233, 244), (235, 241, 250))[tone]
    return ((48, 54, 68), (40, 45, 58), (30, 34, 44))[tone]


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


_BG_COLOR_SEQ = [(200, 220, 240), (15, 20, 30), (235, 240, 245),
                 (30, 60, 110), (140, 180, 230), (255, 255, 255),
                 (90, 150, 90), (150, 90, 90)]


def _guess_file_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return "历史" if ext in (".dat", ".bde") else "预测"


def _fmt_size(path: str) -> str:
    try:
        n = os.path.getsize(path)
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.1f} MB"
        return f"{n / 1024:.0f} KB"
    except OSError:
        return "-"


class PaintDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title = rt(f_m, "绘画模式", TXT)
        self.title_dark = rt(f_m, "绘画模式", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

        # ── 参数状态 ──
        self.scheme = "wiki"
        self.path_width = 2
        self.point_size = 5
        self.margin_w = 0.0
        self.margin_h = 0.0
        self.min_w = 1920
        self.min_h = 1080
        self.draw_order = "simult"
        self.skip_invest = False
        self.all_points = True
        self.grid = False
        self.draw_rad = "none"
        self.multireset = "none"
        self.legend_pos = "auto"
        self.labels = False
        self.bg_color = None
        self.use_map = True
        self.coord_unit = "deg"
        self.mode = "normal"
        self.region = COMMON_REGIONS["西北太平洋 WPAC"]
        self.region_preset = "西北太平洋 WPAC"
        self.map_path = None

        # ── 文件列表(Ty4 管理文件列表模型) ──
        self.files: List[dict] = []     # {path, name, dir, type, size}
        self.sel_rows: List[bool] = []
        self._file_scroll = 0
        self._left_scroll = 0
        self._ext_tys = {}              # path -> Typhoon(解析缓存)

        # ── 输入框 ──
        self._fields: dict = {}
        self._field_rects: dict = {}

        # ── 渲染结果 / 统计 ──
        self._result: Optional[pygame.Surface] = None
        self._stats: List[dict] = []
        self._status = ""
        self._status_ts = 0.0
        self._zoom = False              # True: 放大查看模式
        self._zoom_scroll = 0

        self.x0 = self.y0 = 0
        self._browser: Optional[FileBrowserDialog] = None
        self._map_browse_rect: Optional[pygame.Rect] = None

    # ── 激活/关闭 ──

    def activate(self):
        # 打开时默认不选择任何台风(用户可选「一键加入全部」或自行勾选)
        self._build_layout()
        self._refresh_parser_cache()
        super().activate()

    def deactivate(self):
        for f in self._fields.values():
            f.deactivate()
        super().deactivate()

    def _build_layout(self):
        sw, sh = self.sim.screen_width, self.sim.screen_height
        w = min(DIALOG_W, sw - 40)
        h = min(DIALOG_H, sh - 60)
        self.bg_rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        self.x0 = self.bg_rect.x + PAD
        self.y0 = self.bg_rect.y + self.title_bar_height + 12
        self._rebuild_fields()
        if self._browser is not None:
            self._browser._build_layout()

    def _row_y(self, idx: int) -> int:
        return self.y0 + idx * (ROW_H + ROW_GAP)

    # ── 文件解析缓存 ──

    def _refresh_parser_cache(self):
        self._ext_tys.pop(None, None)
        # 预解析当前已选文件
        for d in self.files:
            self._get_ty_from_path(d['path'])
        self._refresh_stats_now()

    def _refresh_stats_now(self):
        """不依赖渲染, 直接对当前选中台风输出 ACE/路径长度。"""
        try:
            self._stats = compute_track_stats(self._selected_tys())
        except Exception:
            self._stats = []

    def _get_ty_from_path(self, path):
        if path in self._ext_tys:
            return self._ext_tys[path]
        try:
            from .data_repo import DataRepository
            ty = DataRepository(None, None).parse_typhoon_file(path, add_to_list=False)
        except Exception:
            ty = None
        if len(self._ext_tys) > 2000:
            self._ext_tys.clear()
        self._ext_tys[path] = ty
        return ty

    def _selected_paths(self):
        return [d['path'] for d, s in zip(self.files, self.sel_rows) if s]

    def _selected_tys(self):
        out = []
        for p in self._selected_paths():
            ty = self._get_ty_from_path(p)
            if ty is not None:
                out.append(ty)
        return out

    # ── 字段 ──

    def _rebuild_fields(self):
        for f in self._fields.values():
            f.deactivate()
        self._fields.clear()
        self._field_rects.clear()
        self._field_base_y: dict = {}
        colx = self.x0 + 270
        by = self._row_y
        defs = [
            ("region", by(2), _fmt_region(self.region), 24, None),
            ("path_width", by(6), f"{self.path_width}", 5, int),
            ("point_size", by(7), f"{self.point_size}", 5, int),
            ("margin_w", by(8), f"{self.margin_w:g}", 7, float),
            ("margin_h", by(9), f"{self.margin_h:g}", 7, float),
            ("min_w", by(10), f"{self.min_w}", 5, int),
            ("min_h", by(11), f"{self.min_h}", 5, int),
            ("map_path", by(20), self.map_path or "", 200, None),
        ]
        for key, y, val, mlen, conv in defs:
            rect = pygame.Rect(colx, y, 170, 26)
            if key == "region":
                rect = pygame.Rect(colx, y, 210, 26)
            if key == "map_path":
                rect = pygame.Rect(self.x0 + 90, y, 430, 26)
            fld = InputField(rect, max_length=mlen, dark=self.dark_mode)
            fld.set_text(val)
            fld.key = key
            self._fields[key] = fld
            self._field_rects[key] = rect
            self._field_base_y[key] = y
        self._apply_left_scroll()

    def _apply_left_scroll(self):
        for key, f in self._fields.items():
            f.rect.y = self._field_base_y[key] - self._left_scroll

    # ── 几何 ──

    def _ctrl_y(self, idx: int) -> int:
        """左列控件统一纵向坐标(含 scroll 偏移)。"""
        return self.y0 + idx * (ROW_H + ROW_GAP) - getattr(self, '_left_scroll', 0)

    def _preset_button_rect(self):
        return pygame.Rect(self.x0 + 270 + 230, self._ctrl_y(2), 150, 26)

    def _mode_btn_rect(self, mode):
        names = {"normal": 0, "region": 1, "global": 2}
        return pygame.Rect(self.x0 + names[mode] * 130, self._ctrl_y(1) - 26, 122, 30)

    def _left_panel_rect(self):
        return pygame.Rect(self.x0, self.y0, 720,
                           self.bg_rect.bottom - self.y0 - BOTTOM_BAR_H - 10)

    def _file_panel_rect(self):
        x = self.x0 + 740
        w = self.bg_rect.right - PAD - x
        h = self.bg_rect.bottom - self.y0 - BOTTOM_BAR_H - 10
        return pygame.Rect(x, self.y0, w, h)

    def _bottom_top(self):
        return self.bg_rect.bottom - BOTTOM_BAR_H

    def _preview_rect(self):
        bt = self._bottom_top()
        w = self.bg_rect.width - 2 * PAD - STATS_W - 14
        return pygame.Rect(self.x0, bt + 6, w, PREVIEW_H)

    def _stats_rect(self):
        bt = self._bottom_top()
        x = self.bg_rect.right - PAD - STATS_W
        return pygame.Rect(x, bt + 6, STATS_W, PREVIEW_H + 34)

    def _action_button_rects(self):
        bt = self._bottom_top()
        base_y = bt + PREVIEW_H + 12
        btns = {}
        x = self.bg_rect.right - PAD - 110
        for key, lab in (("close", "关闭"), ("clear", "清除"), ("save", "保存路径图"),
                         ("draw", "开始绘画")):
            w0 = 150 if key == "draw" else 110
            x -= w0
            btns[key] = pygame.Rect(x, base_y, w0, 34)
            x -= 10
        return btns

    def _cycle_controls(self):
        def Y(idx):
            return self._ctrl_y(idx)
        c = {}
        c["scheme"] = ("scheme", self._cycle_label_text("scheme"),
                       lambda: self.scheme, lambda v: setattr(self, "scheme", v),
                       lambda: pygame.Rect(self.x0, Y(13), 290, 30))
        c["order"] = ("order", self._cycle_label_text("order"),
                      lambda: self.draw_order, lambda v: setattr(self, "draw_order", v),
                      lambda: pygame.Rect(self.x0, Y(14), 290, 30))
        c["draw_rad"] = ("draw_rad", self._cycle_label_text("draw_rad"),
                         lambda: self.draw_rad, lambda v: setattr(self, "draw_rad", v),
                         lambda: pygame.Rect(self.x0, Y(15), 290, 30))
        c["multireset"] = ("multireset", self._cycle_label_text("multireset"),
                           lambda: self.multireset, lambda v: setattr(self, "multireset", v),
                           lambda: pygame.Rect(self.x0, Y(16), 290, 30))
        c["coord_unit"] = ("coord_unit", self._cycle_label_text("coord_unit"),
                           lambda: self.coord_unit, lambda v: setattr(self, "coord_unit", v),
                           lambda: pygame.Rect(self.x0 + 340, Y(13), 290, 30))
        c["legend_pos"] = ("legend_pos", self._cycle_label_text("legend_pos"),
                           lambda: self.legend_pos, lambda v: setattr(self, "legend_pos", v),
                           lambda: pygame.Rect(self.x0 + 340, Y(14), 290, 30))
        c["bg"] = ("bg", self._cycle_label_text("bg"),
                   lambda: self._bg_key(), lambda v: self._set_bg(v),
                   lambda: pygame.Rect(self.x0 + 340, Y(15), 290, 30))
        c["map"] = ("map", self._cycle_label_text("map"),
                    lambda: self.use_map, lambda v: setattr(self, "use_map", v),
                    lambda: pygame.Rect(self.x0 + 340, Y(16), 290, 30))
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
        if key == "map":
            return ""
        return ""

    def _cycle_label_text(self, key):
        if key == "map":
            return "地图底图: " + ("开" if self.use_map else "关")
        prefix = {"scheme": "色阶", "order": "绘画顺序", "draw_rad": "风圈",
                  "multireset": "多文件重置", "coord_unit": "坐标单位",
                  "legend_pos": "图例位置", "bg": "背景"}[key]
        return f"{prefix}: {self._cycle_text(key)}"

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
            cur = _BG_COLOR_SEQ.index(self.bg_color) if self.bg_color in _BG_COLOR_SEQ else 0
            self.bg_color = _BG_COLOR_SEQ[(cur + 1) % len(_BG_COLOR_SEQ)]
            self.use_map = False

    # ── 事件 ──

    def handle_event(self, e):
        if not self.active:
            return False
        # 二级浏览器优先
        if self._browser is not None and self._browser.active:
            return self._browser.handle_event(e)
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            if self._zoom:
                self._zoom = False
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_LEFT:
                self._zoom_scroll = max(0, self._zoom_scroll - 80)
                return True
            if e.key == pygame.K_RIGHT:
                self._zoom_scroll += 80
                return True
        for f in self._fields.values():
            if f.handle_event(e):
                return True
        if e.type == pygame.MOUSEBUTTONDOWN:
            if e.button in (4, 5):
                d = -1 if e.button == 4 else 1
                if self._zoom:
                    self._zoom_scroll = max(0, self._zoom_scroll + d * 90)
                elif self._left_panel_rect().collidepoint(e.pos):
                    self._left_scroll = max(0, self._left_scroll + d)
                else:
                    self._file_scroll = max(0, self._file_scroll + d)
                return True
            if e.button == 1:
                self._click(e.pos)
            return True
        return False

    def _click(self, pos):
        x, y = pos
        # 缩放模式: 点击返回
        if self._zoom:
            self._zoom = False
            return
        for key, rect in self._action_button_rects().items():
            if rect.collidepoint(x, y):
                self._on_action(key)
                return
        # 预览放大
        if self._preview_rect().collidepoint(x, y) and self._result is not None:
            self._zoom = True
            self._zoom_scroll = 0
            return
        # 模式组
        for mode in ("normal", "region", "global"):
            if self._mode_btn_rect(mode).collidepoint(x, y):
                self.mode = mode
                return
        if self._preset_button_rect().collidepoint(x, y):
            names = list(COMMON_REGIONS.keys())
            cur = names.index(self.region_preset) if self.region_preset in names else 0
            self.region_preset = names[(cur + 1) % len(names)]
            self.region = COMMON_REGIONS[self.region_preset]
            f = self._fields.get("region")
            if f:
                f.set_text(_fmt_region(self.region))
            return
        # 文件面板工具条(位于文件面板顶部)
        if self._file_toolbar_click(x, y):
            return
        # 地图文件「选择地图」按钮
        if self._map_browse_rect and self._map_browse_rect.collidepoint(x, y):
            self._open_browser("map", self._on_map_picked)
            return
        # 勾选项
        if self._checkbox_click(x, y):
            return
        # 循环按钮
        for key, (lab, text, getter, setter, rectfn) in self._cycle_controls().items():
            if rectfn().collidepoint(x, y):
                cur = getter()
                vals = _key_values(key)
                nxt = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0]
                setter(nxt)
                return
        # 文件列表行
        fr = self._file_panel_rect()
        if fr.collidepoint(x, y):
            row_h = 26
            idx = self._file_scroll + (y - (fr.y + 46)) // row_h
            if 0 <= idx < len(self.files):
                self.sel_rows[idx] = not self.sel_rows[idx]
            return

    def _file_toolbar_click(self, x, y):
        fr = self._file_panel_rect()
        if not (fr.y <= y <= fr.y + 34):
            return False
        for key, rect in self._file_toolbar_rects(fr).items():
            if rect.collidepoint(x, y):
                self._file_action(key)
                return True
        return False

    def _file_toolbar_rects(self, fr=None):
        fr = fr or self._file_panel_rect()
        labels = [("add", "导入台风"), ("oneall", "一键全部"), ("del", "移除"),
                  ("dedup", "去重"), ("clear", "清空"), ("refresh", "刷新"),
                  ("all", "全选"), ("unall", "全不选")]
        btns = {}
        x = fr.x
        for key, lab in labels:
            w0 = 92
            btns[key] = pygame.Rect(x, fr.y, w0, 30)
            x += w0 + 8
        return btns

    def _file_action(self, key):
        if key == "add":
            self._open_browser("typhoon", self._on_files_imported)
        elif key == "oneall":
            auto = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "typhoon")
            picked = []
            if os.path.isdir(auto):
                for root, _d, fs in os.walk(auto):
                    for fn in fs:
                        if fn.lower().endswith((".dat", ".txt", ".bde")):
                            picked.append(os.path.join(root, fn))
            before = len(self.files)
            self._add_files(picked)
            # 一键全部: 所有(含新增)文件标记为选中
            self.sel_rows = [True] * len(self.files)
            self._status = f"已加入全部台风: {len(self.files)} 个文件 (新增 {len(self.files) - before})"
            self._status_ts = time.time()
            self._refresh_stats_now()
        elif key == "del":
            keep = [d for d, s in zip(self.files, self.sel_rows) if not s]
            self._set_files(keep)
            self._refresh_stats_now()
        elif key == "dedup":
            seen, out = set(), []
            for d in self.files:
                k = (d['name'], d['size'])
                if k not in seen:
                    seen.add(k)
                    out.append(d)
            self._set_files(out)
            self._refresh_stats_now()
        elif key == "clear":
            self._set_files([])
            self._refresh_stats_now()
        elif key == "refresh":
            self._refresh_parser_cache()
            self._status = "已刷新"
            self._status_ts = time.time()
        elif key == "all":
            self.sel_rows = [True] * len(self.files)
            self._refresh_stats_now()
        elif key == "unall":
            self.sel_rows = [False] * len(self.files)
            self._refresh_stats_now()

    def _add_files(self, paths):
        cur = {d['path'] for d in self.files}
        added = 0
        for p in paths:
            if p in cur:
                continue
            self.files.append({
                'path': p,
                'name': os.path.basename(p),
                'dir': os.path.dirname(p),
                'type': _guess_file_type(p),
                'size': _fmt_size(p),
            })
            cur.add(p)
            added += 1
        self.sel_rows = [False] * len(self.files)
        self._status = f"已加入 {added} 个文件"
        self._status_ts = time.time()

    def _set_files(self, files):
        self.files = list(files)
        self.sel_rows = [False] * len(self.files)

    def _open_browser(self, purpose, on_ok):
        if self._browser is None:
            self._browser = FileBrowserDialog(self.sim, purpose=purpose)
        self._browser.purpose = purpose
        self._browser.exts = EXT_MAP[purpose]
        self._browser.activate(on_ok=on_ok)

    def _on_files_imported(self, paths):
        self._add_files(paths)

    def _on_map_picked(self, path):
        if path:
            self.map_path = path[0]
            f = self._fields.get("map_path")
            if f:
                f.set_text(path[0])

    # ── 勾选 ──

    def _checkbox_click(self, x, y):
        chk = [("skip_invest", self.skip_invest), ("all_points", self.all_points),
               ("grid", self.grid), ("labels", self.labels)]
        for i, (key, val) in enumerate(chk):
            box = pygame.Rect(self.x0 + 340, self._row_y(17 + i), 18, 18)
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
            self._stats = []
            self._status = "已清除当前显示"
            self._status_ts = time.time()
        elif key == "close":
            self.deactivate()

    def _collect_fields(self) -> dict:
        out = {}
        for key, f in self._fields.items():
            txt = f.get_text().strip()
            if key == "region":
                try:
                    nums = [float(v.strip()) for v in txt.replace("，", ",").split(",") if v.strip()]
                    if len(nums) == 4:
                        out["region"] = (nums[0], nums[1], nums[2], nums[3])
                except ValueError:
                    pass
                continue
            if key == "map_path":
                out["map_path"] = txt or None
                continue
            try:
                if key in ("path_width", "point_size", "min_w", "min_h"):
                    out[key] = int(float(txt)) if txt else None
                elif key in ("margin_w", "margin_h"):
                    out[key] = float(txt) if txt else None
            except ValueError:
                pass
        for k, d in (("path_width", self.path_width), ("point_size", self.point_size),
                     ("margin_w", self.margin_w), ("margin_h", self.margin_h),
                     ("min_w", self.min_w), ("min_h", self.min_h)):
            out.setdefault(k, d)
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
            self._result, self._stats = renderer.render(
                tys, mode=self.mode, region=region, map_surface=map_surf)
            n = len([t for t in tys if t.pts])
            self._status = (f"绘画完成: {n} 条路径, "
                            f"{self._result.get_width()}×{self._result.get_height()}"
                            f" · 统计 {len(self._stats)} 个台风")
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
        self._draw_file_panel(surface, dark)
        self._draw_preview(surface, dark)
        self._draw_stats(surface, dark)
        self._draw_action_buttons(surface, dark)
        self._draw_status(surface, dark)

        if self._browser is not None and self._browser.active:
            self._browser.draw(surface)

    def _label(self, surface, text, x, y, dark, bright=False, font=f_s):
        ts = rt(font, text, _light(dark, bright))
        surface.blit(ts, (x, y + (ROW_H - ts.get_height()) // 2))

    def _button(self, surface, rect, text, color, dark, tc=None, border=5):
        pygame.draw.rect(surface, color, rect, border_radius=border)
        ts = rt(f_s, text, tc or (255, 255, 255))
        surface.blit(ts, (rect.centerx - ts.get_width() // 2,
                          rect.centery - ts.get_height() // 2))

    def _draw_left(self, surface, dark):
        lp = self._left_panel_rect()
        pygame.draw.rect(surface, _dark_bg(dark, 1), lp, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128) if dark else (160, 175, 195),
                         lp, 1, border_radius=8)
        title = rt(f_m, "参数设置", _light(dark, True) if dark else (30, 60, 110))
        surface.blit(title, (lp.x + 8, lp.y - 34))

        scroll = self._left_scroll
        cy = self._ctrl_y

        # 范围
        self._label(surface, "范围", self.x0, cy(0) - 26, dark, bright=True, font=f_m)
        for mode, label in (("normal", "普通"), ("region", "区域"), ("global", "全局")):
            r = self._mode_btn_rect(mode)
            sel = self.mode == mode
            self._button(surface, r, label,
                         (60, 120, 190) if sel else _dark_bg(dark, 0), dark)
        # 区域
        self._label(surface, "区域(上,左,高,宽)", self.x0, cy(2), dark, bright=True)
        self._draw_field(surface, "region", dark)
        self._button(surface, self._preset_button_rect(), "预设: " + self.region_preset,
                     _dark_bg(dark, 0), dark)
        # 参数
        self._label(surface, "参数", self.x0, cy(4) - 6, dark, bright=True, font=f_m)
        rows = [("路径宽度(px)", "path_width"), ("定点大小(px)", "point_size"),
                ("半宽留空(°)", "margin_w"), ("半高留空(°)", "margin_h"),
                ("最小宽度(px)", "min_w"), ("最小高度(px)", "min_h")]
        for i, (lab, key) in enumerate(rows):
            self._label(surface, lab, self.x0, cy(5 + i), dark)
        for key in ("path_width", "point_size", "margin_w", "margin_h",
                    "min_w", "min_h"):
            self._draw_field(surface, key, dark)
        # 循环按钮: 文字自带前缀(如 "色阶: 经典Wiki 色阶"), 无独立标签避免重叠
        for key, (lab, text, get, set_, rectfn) in self._cycle_controls().items():
            rect = rectfn()
            self._button(surface, rect, text, _dark_bg(dark, 0), dark)
        # 勾选
        chk = [("skip_invest", "忽略前期扰动定位"), ("all_points", "显示所有定位点"),
               ("grid", "显示经纬网"), ("labels", "显示文字标注")]
        for i, (key, lab) in enumerate(chk):
            box = pygame.Rect(self.x0 + 340, cy(17 + i), 18, 18)
            self._checkbox(surface, box, lab, getattr(self, key), dark)
        # 地图文件
        self._label(surface, "地图文件", self.x0, cy(20), dark, bright=True)
        self._draw_field(surface, "map_path", dark)
        br = pygame.Rect(self.x0 + 530, cy(20), 90, 26)
        self._button(surface, br, "选择地图", (70, 120, 180), dark)
        self._map_browse_rect = br
        # 滚动条指示
        total_h = 24 * (ROW_H + ROW_GAP)
        if total_h > lp.height:
            vis = lp.height
            bar_h = max(16, int(vis * vis / total_h))
            bar_y = lp.y + int((vis - bar_h) * (scroll / max(1, total_h - vis)))
            pygame.draw.rect(surface, (120, 130, 150) if dark else (150, 165, 185),
                             (lp.right - 6, bar_y, 4, bar_h))

    def _draw_field(self, surface, key, dark):
        f = self._fields.get(key)
        if f:
            f.draw(surface)

    def _checkbox(self, surface, box, label, val, dark):
        pygame.draw.rect(surface, (60, 66, 82) if dark else (200, 210, 226), box, border_radius=3)
        if val:
            pygame.draw.line(surface, (90, 220, 130), (box.x + 3, box.centery),
                             (box.x + box.w // 2, box.bottom - 3), 3)
            pygame.draw.line(surface, (90, 220, 130), (box.x + box.w // 2, box.bottom - 3),
                             (box.right - 2, box.y + 3), 3)
        ts = rt(f_s, label, _light(dark))
        surface.blit(ts, (box.right + 8, box.y + (box.h - ts.get_height()) // 2))

    def _draw_file_panel(self, surface, dark):
        fr = self._file_panel_rect()
        pygame.draw.rect(surface, _dark_bg(dark, 1), fr, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128) if dark else (160, 175, 195), fr, 1, border_radius=8)
        t = rt(f_m, f"文件列表 ({len(self.files)} 个文件)",
               _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (fr.x + 8, fr.y - 34))
        # 工具条
        for key, rect in self._file_toolbar_rects(fr).items():
            color = {"add": (70, 150, 110), "oneall": (70, 120, 190), "del": (170, 100, 80),
                     "dedup": (120, 110, 90), "clear": (150, 90, 90), "refresh": (90, 120, 150),
                     "all": (80, 130, 170), "unall": (110, 95, 95)}[key]
            lab = {"add": "导入台风", "oneall": "一键全部", "del": "移除", "dedup": "去重",
                   "clear": "清空", "refresh": "刷新", "all": "全选", "unall": "全不选"}[key]
            self._button(surface, rect, lab, color, dark)
        # 表头
        hdr = fr.y + 44
        tcol = _light(dark, True)
        cols = [("顺序", int(fr.width * 0.07)), ("文件名", int(fr.width * 0.30)),
                ("所在路径", int(fr.width * 0.36)), ("类型", int(fr.width * 0.10)),
                ("文件尺寸", int(fr.width * 0.15))]
        x = fr.x
        for lab, w0 in cols:
            ts = rt(f_s, lab, tcol)
            surface.blit(ts, (x + 6, hdr + 2))
            x += w0
        total = len(self.sel_rows)
        vis = (fr.bottom - hdr - 6) // 26
        if total > vis and vis > 0:
            bar_h = max(16, int((fr.bottom - hdr - 6) * vis / total))
            bar_y = hdr + int((fr.bottom - hdr - 6 - bar_h) *
                              (self._file_scroll / max(1, total - vis)))
            pygame.draw.rect(surface, (120, 130, 150) if dark else (150, 165, 185),
                             (fr.right - 6, bar_y, 4, bar_h))
        y = hdr
        for i in range(self._file_scroll, min(total, self._file_scroll + vis)):
            yy = y + (i - self._file_scroll) * 26
            if yy + 26 > fr.bottom:
                break
            row = pygame.Rect(fr.x, yy, fr.width, 25)
            if self.sel_rows[i]:
                pygame.draw.rect(surface, (46, 78, 118) if dark else (185, 215, 240), row)
            d = self.files[i]
            v = [str(i + 1), d['name'], d['dir'], d['type'], d['size']]
            x = fr.x
            for j, (lab, w0) in enumerate(cols):
                ts = rt(f_s, ("☑ " if (j == 0 and self.sel_rows[i]) else "") + v[j],
                        _light(dark))
                surface.blit(ts, (x + 6, yy + 4))
                x += w0

    def _draw_preview(self, surface, dark):
        pr = self._preview_rect()
        pygame.draw.rect(surface, (24, 28, 38) if dark else (200, 210, 226), pr, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128) if dark else (150, 165, 185), pr, 1, border_radius=8)
        if self._result is None:
            ts = rt(f_m, "点击「开始绘画」生成路径图 (单击预览可放大)", _light(dark))
            surface.blit(ts, (pr.centerx - ts.get_width() // 2,
                              pr.centery - ts.get_height() // 2))
            return
        img = self._result
        if self._zoom:
            # 放大查看: 按预览区高度 1:1 显示(超高滚动)
            sc = pr.height / img.get_height()
            tw = max(1, int(img.get_width() * sc))
            th = pr.height
            vis_w = pr.width
            # 横向滚轮: _zoom_scroll 控制左偏移
            dx = -self._zoom_scroll
            prev = pygame.transform.smoothscale(img, (tw, th))
            if tw > vis_w:
                dx = min(0, max(vis_w - tw, -self._zoom_scroll))
            surface.blit(prev, (pr.x + dx, pr.y))
            tip = rt(f_s, "放大查看 · 滚轮平移 · 单击/ Esc 返回", _light(dark))
            surface.blit(tip, (pr.x + 8, pr.y + 8))
            return
        sc = min(pr.width / img.get_width(), pr.height / img.get_height())
        tw = max(1, int(img.get_width() * sc))
        th = max(1, int(img.get_height() * sc))
        prev = pygame.transform.smoothscale(img, (tw, th))
        surface.blit(prev, (pr.centerx - tw // 2, pr.centery - th // 2))
        tip = rt(f_s, "单击放大", (245, 245, 250))
        surface.blit(tip, (pr.right - tip.get_width() - 10, pr.y + 6))

    def _draw_stats(self, surface, dark):
        sr = self._stats_rect()
        pygame.draw.rect(surface, _dark_bg(dark, 1), sr, border_radius=8)
        pygame.draw.rect(surface, (90, 104, 128) if dark else (160, 175, 195),
                         sr, 1, border_radius=8)
        t = rt(f_m, "ACE / 路径统计", _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (sr.x + 10, sr.y + 6))
        if not self._stats:
            ts = rt(f_s, "绘画后可显示每个台风的 ACE 与路径长度", _light(dark))
            surface.blit(ts, (sr.x + 10, sr.y + 44))
            return
        hdrs = [("名称", 130), ("ACE", 76), ("km", 70), ("点", 44)]
        x = sr.x + 10
        for lab, w0 in hdrs:
            ts = rt(f_s, lab, _light(dark, True))
            surface.blit(ts, (x, sr.y + 44))
            x += w0
        y = sr.y + 70
        for s in self._stats:
            vals = [s['name'], f"{s['ace']:.2f}", f"{s['path_km']:.0f}", str(s['npts'])]
            x = sr.x + 10
            for j, (lab, w0) in enumerate(hdrs):
                ts = rt(f_s, vals[j], _light(dark))
                surface.blit(ts, (x, y))
                x += w0
            y += 22
            if y > sr.bottom - 10:
                ts = rt(f_s, f"... 共 {len(self._stats)} 个", _light(dark))
                surface.blit(ts, (sr.x + 10, sr.bottom - 24))
                break

    def _draw_action_buttons(self, surface, dark):
        for key, rect in self._action_button_rects().items():
            color = {"draw": (60, 150, 100), "save": (60, 110, 175),
                     "clear": (160, 110, 70), "close": (150, 80, 80)}[key]
            lab = {"draw": "开始绘画", "save": "保存路径图",
                   "clear": "清除", "close": "关闭"}[key]
            self._button(surface, rect, lab, color, dark)

    def _draw_status(self, surface, dark):
        if self._status and time.time() - self._status_ts < 8:
            pr = self._preview_rect()
            ts = rt(f_s, self._status, (240, 205, 110) if dark else (150, 100, 30))
            surface.blit(ts, (self.x0, pr.bottom - 26))
