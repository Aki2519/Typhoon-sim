# py/paint_dialog.py
"""绘画模式对话框: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘画窗口。

功能栏最右侧「绘画」按钮点击后弹出此窗口。窗口尺寸放大(接近风季统计界面),
布局为清晰的「左=参数设置 · 中=管理文件列表 · 底=预览+统计」三段式:

左列参数设置(固定行网格, 无重叠):
  - 范围: 普通 / 区域 / 全局(按钮组)
  - 区域(上,左,高,宽): 输入框 + 常用洋区预设(下拉)
  - 参数(每行 标签[定宽列] + 输入框): 路径宽度 / 定点大小 / 半宽半高留空 / 最小宽高
  - 下拉选择(点击按钮展开列表, 非点击循环): 色阶方案 / 坐标单位 / 绘画顺序 /
    风圈 / 多文件路径重置 / 图例位置
  - 勾选框: 地图底图 / 忽略前期扰动 / 显示所有定位点 / 显示经纬网 / 文字标注 / 背景色
  - 地图文件: 输入框 + 「选择地图」(文件浏览器)

中列管理文件列表(与 Ty4「管理文件列表」一致):
  - 工具条: 导入台风(打开文件浏览器) / 一键全部 / 移除 / 去重 / 清空 / 刷新 / 全选 / 全不选
  - 表头固定在面板顶部(不随滚动): 顺序 | 文件名 | 所在路径 | 文件尺寸
  - 数据行滚动(滚轮), 单击勾选; 打开时默认不选任何台风

底部区:
  - 大预览(左): 单击进入放大查看(滚轮平移 / 单击或 Esc 返回)
  - 统计面板(右): 每个选中台风的 名称 / ACE / 路径长度(km) / 定位点
  - 动作按钮: 开始绘画 / 保存路径图 / 清除 / 关闭

px 留空(像素单位)时内容区保持经纬纵横比(地图不被拉伸)。
"""
from __future__ import annotations

import os
import time
import pygame
from typing import Callable, Dict, List, Optional, Tuple

from .constants import (
    f_s, f_m, f_l, rt, TXT, SETTINGS_TEXT_LIGHT, DIALOG_TITLE_BAR_HEIGHT,
)
from .dialog_base import DraggableDialog
from .input_field import InputField
from .paint_render import (
    TrackMapRenderer, COMMON_REGIONS, load_map_image, compute_track_stats,
)
from .paint_file_browser import FileBrowserDialog, EXT_MAP
from .constants.colors import (
    LEGEND_POS_NAMES, COLOR_SCALE_SCHEMES, WIKI_LEGEND_ENTRIES,
)

# 窗口尺寸(放大, 接近风季统计界面, 受屏幕约束)
DIALOG_W = 1720
DIALOG_H = 1180
PAD = 20
# 行网格: 每行固定高, 标签定宽列 -> 输入/控件(对齐不错位)
ROW_H = 34
ROW_GAP = 2
LABEL_W = 150        # 左列标签列宽(输入框起点统一)
INPUT_W = 170        # 输入框宽度
LEFT_W = 600         # 左参数列总宽
# 底部预留(预览大条 + 右侧统计列 + 动作按钮)
BOTTOM_BAR_H = 430
PREVIEW_H = 350
STATS_W = 460


def _fmt_region(region):
    top, left, height, width = region
    return f"{top:g},{left:g},{height:g},{width:g}"


def _light(dark: bool, bright: bool = False):
    if not dark:
        return (25, 32, 48) if not bright else (30, 80, 160)
    return SETTINGS_TEXT_LIGHT if bright else (192, 200, 214)


def _panel_bg(dark: bool, tone=0):
    if not dark:
        return ((212, 222, 236), (228, 236, 246), (238, 244, 252))[tone]
    return ((52, 58, 72), (44, 50, 63), (31, 35, 46))[tone]


def _border(dark: bool):
    return (95, 108, 132) if dark else (160, 175, 195)


def _guess_file_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return "历史" if ext in (".dat", ".bde") else "预测"


def _fmt_size(path: str) -> str:
    try:
        n = os.path.getsize(path)
        if n >= 1048576:
            return f"{n / 1048576:.1f} MB"
        return f"{n / 1024:.0f} KB"
    except OSError:
        return "-"


class _Dropdown:
    """简单下拉框: 显示当前项, 点击展开列表选择。用于解决"点击循环切换"不便。"""

    def __init__(self, rect, options, current, on_select, dark=True):
        self.rect = pygame.Rect(rect)
        self.options = options          # [(value, label), ...]
        self.current = current
        self.on_select: Optional[Callable] = on_select
        self.open = False
        self._mouse_down = False

    def toggle(self, pos):
        if self.rect.collidepoint(pos):
            self.open = not self.open
            return True
        if self.open:
            for opt_rect, (val, lab) in self._option_rects():
                if opt_rect.collidepoint(pos):
                    self.current = val
                    self.open = False
                    if self.on_select:
                        self.on_select(val)
                    return True
            self.open = False
        return False

    def _option_rects(self):
        opt_h = 24
        out = []
        for i, (val, lab) in enumerate(self.options):
            out.append((pygame.Rect(self.rect.x, self.rect.bottom + 2 + i * opt_h,
                                    self.rect.width, opt_h), (val, lab)))
        return out

    def draw(self, surface, dark):
        bg = (60, 66, 82) if dark else (232, 239, 250)
        br = (100, 112, 136) if dark else (150, 165, 185)
        pygame.draw.rect(surface, bg, self.rect, border_radius=5)
        pygame.draw.rect(surface, br, self.rect, 1, border_radius=5)
        label = self._label_of(self.current)
        ts = rt(f_s, label, _light(dark))
        surface.blit(ts, (self.rect.x + 8, self.rect.centery - ts.get_height() // 2))
        # ▾
        if self.open:
            for opt_rect, (val, lab) in self._option_rects():
                pygame.draw.rect(surface, (52, 58, 72) if dark else (224, 233, 246),
                                 opt_rect, border_radius=4)
                hl = (60, 96, 140) if val == self.current else None
                if hl:
                    pygame.draw.rect(surface, hl, opt_rect, border_radius=4)
                ts = rt(f_s, lab, _light(dark))
                surface.blit(ts, (opt_rect.x + 8, opt_rect.centery - ts.get_height() // 2))

    def _label_of(self, val):
        for v, lab in self.options:
            if v == val:
                return lab
        return str(val)


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
        self.use_map = True
        self.bg_color = None
        self.coord_unit = "deg"
        self.mode = "normal"
        self.region = COMMON_REGIONS["西北太平洋 WPAC"]
        self.region_preset = "西北太平洋 WPAC"
        self.map_path = None

        # ── 文件列表(Ty4 管理文件列表模型) ──
        self.files: List[dict] = []      # {path,name,dir,type,size}
        self.sel_rows: List[bool] = []
        self._file_scroll = 0
        self._left_scroll = 0
        self._ext_tys: Dict[str, Optional[object]] = {}

        # ── 输入框 / 下拉 ──
        self._fields: Dict[str, InputField] = {}
        self._dropdowns: Dict[str, _Dropdown] = {}

        # ── 渲染结果/统计/放大 ──
        self._result: Optional[pygame.Surface] = None
        self._stats: List[dict] = []
        self._status = ""
        self._status_ts = 0.0
        self._zoom = False
        self._zoom_scroll = 0

        self.x0 = self.y0 = 0
        self._browser: Optional[FileBrowserDialog] = None
        self._map_browse_rect: Optional[pygame.Rect] = None

    # ── 激活/关闭 ──

    def activate(self):
        # 打开时默认不选择任何台风(提供「一键全部」)
        self._build_layout()
        super().activate()

    def deactivate(self):
        for f in self._fields.values():
            f.deactivate()
        if self._browser is not None and self._browser.active:
            self._browser.deactivate()
        super().deactivate()

    def _build_layout(self):
        sw, sh = self.sim.screen_width, self.sim.screen_height
        w = min(DIALOG_W, sw - 40)
        h = min(DIALOG_H, sh - 60)
        self.bg_rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        self.x0 = self.bg_rect.x + PAD
        self.y0 = self.bg_rect.y + self.title_bar_height + 10
        self._rebuild_fields()
        self._rebuild_dropdowns()
        if self._browser is not None:
            self._browser._build_layout()

    def _row_y(self, idx: int) -> int:
        return self.y0 + idx * (ROW_H + ROW_GAP)

    def _apply_left_scroll(self):
        """左列内容随 _left_scroll 平移: 更新字段/下拉/预设的 rect.y。"""
        orb = self.y0
        for key, f in self._fields.items():
            f.rect.y = self._field_base_y[key] - self._left_scroll
        for key, dd in self._dropdowns.items():
            dd.rect.y = self._dd_base_y[key] - self._left_scroll

    # ── 输入框 ──

    def _rebuild_fields(self):
        for f in self._fields.values():
            f.deactivate()
        self._fields.clear()
        self._field_base_y: Dict[str, int] = {}
        rows = [
            ("region", 1, _fmt_region(self.region), 24, None),
            ("path_width", 4, f"{self.path_width}", 4, int),
            ("point_size", 5, f"{self.point_size}", 4, int),
            ("margin_w", 6, f"{self.margin_w:g}", 6, float),
            ("margin_h", 7, f"{self.margin_h:g}", 6, float),
            ("min_w", 8, f"{self.min_w}", 5, int),
            ("min_h", 9, f"{self.min_h}", 5, int),
            ("map_path", 18, self.map_path or "", 160, None),
        ]
        inp_x = self.x0 + LABEL_W
        for key, idx, val, mlen, conv in rows:
            rect = pygame.Rect(inp_x, self._row_y(idx), INPUT_W, 26)
            if key == "region":
                rect = pygame.Rect(inp_x, self._row_y(1), 230, 26)
            if key == "map_path":
                rect = pygame.Rect(self.x0 + LABEL_W, self._row_y(18),
                                   LEFT_W - LABEL_W - 100, 26)
            fld = InputField(rect, max_length=mlen, dark=self.dark_mode)
            fld.set_text(val)
            fld.key = key
            self._fields[key] = fld
            self._field_base_y[key] = self._row_y(idx)
        self._apply_left_scroll()

    def _rebuild_dropdowns(self):
        dd = {}
        self._dd_base_y: Dict[str, int] = {}
        defs = [
            ("scheme", 11, [(k, v) for k, v in COLOR_SCALE_SCHEMES.items()], self.scheme),
            ("coord_unit", 12, [("deg", "经纬度(°)"), ("px", "像素(px)")], self.coord_unit),
            ("order", 13, [("simult", "同时"), ("sort", "按强度排序"),
                           ("points_top", "定位点置顶")], self.draw_order),
            ("draw_rad", 14, [("none", "不绘画"), ("all", "全部绘画"),
                              ("last", "仅最后定位"), ("tropical", "仅热带部分")], self.draw_rad),
            ("multireset", 15, [("none", "不重置"), ("auto", "自动重置"),
                                ("last", "仅最后定位")], self.multireset),
            ("legend_pos", 16, [(k, v) for k, v in LEGEND_POS_NAMES.items()], self.legend_pos),
        ]
        for key, idx, opts, cur in defs:
            x = self.x0 + LABEL_W
            if key == "legend_pos":
                x = self.x0 + LABEL_W + INPUT_W + 18
            dd[key] = _Dropdown(pygame.Rect(x, self._row_y(idx), 220, 26),
                                opts, cur, lambda v, k=key: setattr(self, k, v),
                                dark=self.dark_mode)
            self._dd_base_y[key] = self._row_y(idx)
        dd["preset"] = _Dropdown(
            pygame.Rect(self.x0 + LABEL_W + 240, self._row_y(1), 190, 26),
            [(k, k) for k in COMMON_REGIONS.keys()], self.region_preset,
            self._on_preset_select, dark=self.dark_mode)
        self._dd_base_y["preset"] = self._row_y(1)
        self._dropdowns = dd
        self._apply_left_scroll()

    def _on_preset_select(self, v):
        self.region_preset = v
        self.region = COMMON_REGIONS[v]
        f = self._fields.get("region")
        if f:
            f.set_text(_fmt_region(self.region))

    # ── 文件解析缓存 ──

    def _get_ty_from_path(self, path):
        if path in self._ext_tys:
            return self._ext_tys[path]
        try:
            from .data_repo import DataRepository
            ty = DataRepository(None, None).parse_typhoon_file(path, add_to_list=False)
        except Exception:
            ty = None
        if len(self._ext_tys) > 1500:
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

    def _refresh_stats_now(self):
        try:
            self._stats = compute_track_stats(self._selected_tys())
        except Exception:
            self._stats = []

    # ── 面板几何 ──

    def _left_panel_rect(self):
        return pygame.Rect(self.x0, self.y0, LEFT_W,
                           self.bg_rect.bottom - self.y0 - BOTTOM_BAR_H - 10)

    def _file_panel_rect(self):
        x = self.x0 + LEFT_W + 18
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
        return pygame.Rect(x, bt + 6, STATS_W, PREVIEW_H + 40)

    def _action_button_rects(self):
        bt = self._bottom_top()
        base_y = bt + PREVIEW_H + 12
        out = {}
        x = self.bg_rect.right - PAD - 110
        for key, lab in (("close", "关闭"), ("clear", "清除"), ("save", "保存路径图"),
                         ("draw", "开始绘画")):
            w0 = 150 if key == "draw" else 110
            x -= w0
            out[key] = pygame.Rect(x, base_y, w0, 34)
            x -= 10
        return out

    # ── 事件 ──

    def handle_event(self, e):
        if not self.active:
            return False
        if self._browser is not None and self._browser.active:
            return self._browser.handle_event(e)
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            if self._zoom:
                self._zoom = False
            else:
                self.deactivate()
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
                    self._apply_left_scroll()
                elif self._file_panel_rect().collidepoint(e.pos):
                    self._file_scroll = max(0, self._file_scroll + d)
                return True
            if e.button == 1:
                self._click(e.pos)
            return True
        return False

    def _click(self, pos):
        if self._zoom:
            self._zoom = False
            return
        x, y = pos
        # 动作按钮
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
        # 下拉框(选中即应用)
        for key, dd in self._dropdowns.items():
            if dd.toggle(pos):
                return
        # 勾选框
        if self._checkbox_click(x, y):
            return
        # 地图「选择地图」
        if self._map_browse_rect and self._map_browse_rect.collidepoint(x, y):
            self._open_browser("map", self._on_map_picked)
            return
        # 文件列表工具条
        if self._file_toolbar_click(x, y):
            return
        # 文件列表行
        fr = self._file_panel_rect()
        if fr.collidepoint(x, y) and y > fr.y + 72:
            idx = self._file_scroll + (y - (fr.y + 74)) // 26
            if 0 <= idx < len(self.files):
                self.sel_rows[idx] = not self.sel_rows[idx]
                self._refresh_stats_now()

    def _ctrl_y(self, idx: int) -> int:
        return self._row_y(idx) - getattr(self, '_left_scroll', 0)

    def _mode_btn_rect(self, mode):
        names = {"normal": 0, "region": 1, "global": 2}
        return pygame.Rect(self.x0 + names[mode] * 128, self._ctrl_y(0), 120, 28)

    def _file_toolbar_rects(self):
        fr = self._file_panel_rect()
        labels = [("add", "导入台风"), ("oneall", "一键全部"), ("del", "移除"),
                  ("dedup", "去重"), ("clear", "清空"), ("refresh", "刷新"),
                  ("all", "全选"), ("unall", "全不选")]
        out = {}
        x = fr.x
        for key, lab in labels:
            out[key] = pygame.Rect(x, fr.y, 96, 28)
            x += 104
        return out

    def _file_toolbar_click(self, x, y):
        for key, rect in self._file_toolbar_rects().items():
            if rect.collidepoint(x, y):
                self._file_action(key)
                return True
        return False

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
            self.sel_rows = [True] * len(self.files)
            self._refresh_stats_now()
            self._status = f"已加入全部台风: {len(self.files)} 个 (新增 {len(self.files) - before})"
            self._status_ts = time.time()
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
            self.files.append({'path': p, 'name': os.path.basename(p),
                               'dir': os.path.dirname(p),
                               'type': _guess_file_type(p), 'size': _fmt_size(p)})
            cur.add(p)
            added += 1
        self.sel_rows = [False] * len(self.files)
        if added:
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

    def _checkbox_click(self, x, y):
        chk = [("skip_invest", 19), ("all_points", 20), ("grid", 21), ("labels", 22),
               ("use_map", 23), ("bg_btn", 24)]
        for key, idx in chk:
            box = pygame.Rect(self.x0 + LABEL_W, self._ctrl_y(idx), 16, 16)
            if box.collidepoint(x, y):
                if key == "bg_btn":
                    self.bg_color = None if self.bg_color is not None else (200, 220, 240)
                    self.use_map = False
                else:
                    setattr(self, key, not getattr(self, key))
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
        # 垂直居中于输入框行(26px), 与输入框同中线, 避免错位感
        ts = rt(font, text, _light(dark, bright))
        surface.blit(ts, (x, y + (26 - ts.get_height()) // 2))

    def _button(self, surface, rect, text, bg, dark, fg=None, border=5):
        pygame.draw.rect(surface, bg, rect, border_radius=border)
        ts = rt(f_s, text, fg or (255, 255, 255))
        if ts.get_width() > rect.width - 10:
            scale = max(0.5, (rect.width - 10) / max(1, ts.get_width()))
            ts = pygame.transform.smoothscale(
                ts, (max(1, int(ts.get_width() * scale)),
                     max(1, int(ts.get_height() * scale))))
        surface.blit(ts, (rect.centerx - ts.get_width() // 2,
                          rect.centery - ts.get_height() // 2))

    def _checkbox(self, surface, box, label, val, dark):
        pygame.draw.rect(surface, (60, 66, 82) if dark else (200, 210, 226), box, border_radius=3)
        if val:
            pygame.draw.line(surface, (90, 220, 130), (box.x + 3, box.centery),
                             (box.x + box.w // 2, box.bottom - 3), 3)
            pygame.draw.line(surface, (90, 220, 130), (box.x + box.w // 2, box.bottom - 3),
                             (box.right - 2, box.y + 3), 3)
        ts = rt(f_s, label, _light(dark))
        surface.blit(ts, (box.right + 8, box.y + (box.h - ts.get_height()) // 2))

    def _draw_left(self, surface, dark):
        lp = self._left_panel_rect()
        pygame.draw.rect(surface, _panel_bg(dark, 1), lp, border_radius=8)
        pygame.draw.rect(surface, _border(dark), lp, 1, border_radius=8)
        t = rt(f_m, "参数设置", _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (lp.x + 8, lp.y - 32))
        cy = self._ctrl_y
        lx = self.x0
        inp_x = lx + LABEL_W

        # 范围
        self._label(surface, "范围", lx, cy(0) - 24, dark, bright=True, font=f_m)
        for mode, lab in (("normal", "普通"), ("region", "区域"), ("global", "全局")):
            r = self._mode_btn_rect(mode)
            sel = self.mode == mode
            self._button(surface, r, lab, (60, 120, 190) if sel else _panel_bg(dark, 0), dark)
        # 区域
        self._label(surface, "区域(上,左,高,宽)", lx, cy(1), dark)
        self._fields["region"].draw(surface)
        # 参数
        self._label(surface, "参数", lx, cy(3) - 6, dark, bright=True, font=f_m)
        rows = [("路径宽度(px)", "path_width"), ("定点大小(px)", "point_size"),
                ("半宽留空(°)", "margin_w"), ("半高留空(°)", "margin_h"),
                ("最小宽度(px)", "min_w"), ("最小高度(px)", "min_h")]
        for i, (lab, key) in enumerate(rows):
            self._label(surface, lab, lx, cy(4 + i), dark)
            self._fields[key].draw(surface)
        # 下拉选择行
        self._label(surface, "选择", lx, cy(10) - 6, dark, bright=True, font=f_m)
        dd_lab = [("scheme", "色阶方案"), ("coord_unit", "坐标单位"),
                  ("order", "绘画顺序"), ("draw_rad", "风圈"),
                  ("multireset", "多文件重置"), ("legend_pos", "图例位置")]
        for i, (key, lab) in enumerate(dd_lab):
            y = cy(11 + i)
            self._label(surface, lab, lx, y, dark)
            self._dropdowns[key].draw(surface, dark)
        # 勾选
        chk = [("skip_invest", "忽略前期扰动定位"), ("all_points", "显示所有定位点"),
               ("grid", "显示经纬网"), ("labels", "显示文字标注"),
               ("use_map", "地图底图")]
        for i, (key, lab) in enumerate(chk):
            box = pygame.Rect(lx + LABEL_W, cy(19 + i), 16, 16)
            self._checkbox(surface, box, lab, getattr(self, key), dark)
        # 背景
        box = pygame.Rect(lx + LABEL_W, cy(24), 16, 16)
        self._checkbox(surface, box, "自定义背景", self.bg_color is not None, dark)
        # 地图文件
        self._label(surface, "地图文件", lx, cy(18), dark, bright=True)
        self._fields["map_path"].draw(surface)
        br = pygame.Rect(inp_x + 310, cy(18), 100, 26)
        self._button(surface, br, "选择地图", (60, 110, 175), dark)
        self._map_browse_rect = br
        # 滚动条指示
        total_h = 26 * (ROW_H + ROW_GAP)
        if total_h > lp.height and lp.height > 0:
            bar_h = max(16, int(lp.height * lp.height / total_h))
            bar_y = lp.y + int((lp.height - bar_h) *
                               (self._left_scroll / max(1, total_h - lp.height)))
            pygame.draw.rect(surface, (120, 130, 150) if dark else (150, 165, 185),
                             (lp.right - 6, bar_y, 4, bar_h))

    def _draw_file_panel(self, surface, dark):
        fr = self._file_panel_rect()
        pygame.draw.rect(surface, _panel_bg(dark, 1), fr, border_radius=8)
        pygame.draw.rect(surface, _border(dark), fr, 1, border_radius=8)
        t = rt(f_m, f"文件列表 ({len(self.files)} 个文件)",
               _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (fr.x + 8, fr.y - 32))
        # 工具条
        for key, rect in self._file_toolbar_rects().items():
            color = {"add": (70, 150, 110), "oneall": (64, 118, 190), "del": (170, 100, 80),
                     "dedup": (120, 110, 90), "clear": (150, 90, 90), "refresh": (90, 120, 150),
                     "all": (80, 130, 170), "unall": (110, 95, 95)}[key]
            lab = {"add": "导入台风", "oneall": "一键全部", "del": "移除", "dedup": "去重",
                   "clear": "清空", "refresh": "刷新", "all": "全选", "unall": "全不选"}[key]
            self._button(surface, rect, lab, color, dark)
        # 表头固定在面板顶部(不随滚动)
        hdr = fr.y + 42
        tcol = _light(dark, True)
        cols = [("顺序", 56), ("文件名", int(fr.width * 0.28)), ("所在路径", int(fr.width * 0.46)),
                ("文件尺寸", int(fr.width * 0.16))]
        hx = fr.x
        for lab, w0 in cols:
            ts = rt(f_s, lab, tcol)
            surface.blit(ts, (hx + 4, hdr))
            hx += w0
        pygame.draw.line(surface, _border(dark), (fr.x, hdr + 20), (fr.right, hdr + 20), 1)
        # 数据区(从表头下开始, 滚动只滚数据)
        top = hdr + 24
        total = len(self.sel_rows)
        vis = max(1, (fr.bottom - top - 4) // 26)
        if total > vis:
            bar_h = max(16, int((fr.bottom - top) * vis / total))
            bar_y = top + int((fr.bottom - top - bar_h) *
                              (self._file_scroll / max(1, total - vis)))
            pygame.draw.rect(surface, (120, 130, 150) if dark else (150, 165, 185),
                             (fr.right - 6, bar_y, 4, bar_h))
        y = top
        for i in range(self._file_scroll, min(total, self._file_scroll + vis)):
            yy = y + (i - self._file_scroll) * 26
            if yy + 26 > fr.bottom:
                break
            row = pygame.Rect(fr.x, yy, fr.width, 25)
            if self.sel_rows[i]:
                pygame.draw.rect(surface, (46, 78, 118) if dark else (185, 215, 240), row)
            d = self.files[i]
            vals = [str(i + 1), d['name'], d['dir'], d['size']]
            hx = fr.x
            for j, (lab, w0) in enumerate(cols):
                ts = rt(f_s, vals[j], _light(dark))
                surface.blit(ts, (hx + 4, yy + 4))
                hx += w0

    def _draw_preview(self, surface, dark):
        pr = self._preview_rect()
        pygame.draw.rect(surface, (24, 28, 38) if dark else (202, 212, 228), pr, border_radius=8)
        pygame.draw.rect(surface, _border(dark), pr, 1, border_radius=8)
        if self._result is None:
            ts = rt(f_m, "点击「开始绘画」生成路径图 (单击预览可放大)", _light(dark))
            surface.blit(ts, (pr.centerx - ts.get_width() // 2,
                              pr.centery - ts.get_height() // 2))
            return
        img = self._result
        if self._zoom:
            sc = pr.height / img.get_height()
            tw = max(1, int(img.get_width() * sc))
            th = pr.height
            dx = min(0, max(pr.width - tw, -self._zoom_scroll))
            prev = pygame.transform.smoothscale(img, (tw, th))
            surface.blit(prev, (pr.x + dx, pr.y))
            tip = rt(f_s, "放大查看 · 滚轮平移 · 单击/Esc 返回", _light(dark))
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
        pygame.draw.rect(surface, _panel_bg(dark, 1), sr, border_radius=8)
        pygame.draw.rect(surface, _border(dark), sr, 1, border_radius=8)
        t = rt(f_m, "ACE / 路径统计", _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (sr.x + 10, sr.y + 8))
        if not self._stats:
            ts = rt(f_s, "绘画后可显示每个台风的 ACE 与路径长度", _light(dark))
            surface.blit(ts, (sr.x + 10, sr.y + 46))
            return
        hdrs = [("名称", 140), ("ACE", 78), ("km", 74), ("点", 46)]
        x = sr.x + 10
        for lab, w0 in hdrs:
            ts = rt(f_s, lab, _light(dark, True))
            surface.blit(ts, (x, sr.y + 46))
            x += w0
        y = sr.y + 72
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
                surface.blit(ts, (sr.x + 10, sr.bottom - 26))
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
            surface.blit(ts, (self.x0, pr.bottom - 30))
