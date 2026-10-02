# py/paint_dialog.py
"""绘画模式对话框: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘画窗口。

布局「左=参数设置 · 中=管理文件列表 · 底=预览+统计」, 关键规则:
  - 左列面板内边距统一, 文字不超出面板左界
  - 范围选「区域」时才显示 区域(上,左,高,宽) 输入框 (普通/全局隐藏)
  - 下拉框展开层置顶绘制, 不被下方控件盖住
  - 统计面板固定显示在底部右侧, 恒含「总计」行
  - 功能按钮在统计面板之下, 不重叠
  - 管理文件列表: 顺序 / 文件名 / 所在路径 / 文件尺寸, 表头固定顶部
"""
from __future__ import annotations

import os
import time
import pygame
from typing import Callable, Dict, List, Optional

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

DIALOG_W = 1760
DIALOG_H = 1240
PAD = 22
ROW_H = 26
ROW_GAP = 2
LABEL_W = 170
INPUT_W = 180
LEFT_W = 640
BOTTOM_BAR_H = 470
PREVIEW_H = 350
STATS_W = 480
TITLE_BAR_H = 64          # 标题栏加高,「绘画模式」放大


def _fmt_region(region):
    top, left, height, width = region
    return f"{top:g},{left:g},{height:g},{width:g}"


def _light(dark, bright=False):
    if not dark:
        return (25, 32, 48) if not bright else (30, 80, 160)
    return SETTINGS_TEXT_LIGHT if bright else (196, 204, 218)


def _pb(dark, tone=0):
    if not dark:
        return ((212, 222, 236), (228, 236, 246), (238, 244, 252))[tone]
    return ((52, 58, 72), (44, 50, 63), (31, 35, 46))[tone]


def _bd(dark):
    return (98, 111, 135) if dark else (160, 175, 195)


def _fmt_size(path):
    try:
        n = os.path.getsize(path)
        return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"
    except OSError:
        return "-"


#: P5: 色阶方案的 **UI 显示文案**(仅显示层)。值仍来自 `COLOR_SCALE_SCHEMES`, 不新建颜色。
#: 为什么这样标: Ty4 自己 `t4_track.py:59-60` 就是 `PALETTE_CLASSIC = dict(PALETTE_2023)`,
#: 即原程序的 NewColor 开关同样切不出视觉差异 ⇒ 本项目"两套相同"是**忠实复刻**,
#: 既不该编造一组 RGB 去"修好它", 也不该删掉选项掩盖"与 Ty4 同构"这个事实。
_SCHEME_UI_LABEL = {
    "wiki": "经典 Wiki 色阶（同 2023）",
    "2023": "2023 新版色阶（同经典）",
}


class _Dropdown:
    """下拉框: 点击展开, 选项列表由对话框统一置顶绘制(见 draw)。"""

    def __init__(self, rect, options, current, on_select, dark=True):
        self.rect = pygame.Rect(rect)
        self.options = options            # [(value, label), ...]
        self.current = current
        self.on_select: Optional[Callable] = on_select
        self.open = False

    def toggle(self, pos):
        if self.rect.collidepoint(pos):
            self.open = True
            return True
        if self.open:
            for r, (val, _) in self._option_rects():
                if r.collidepoint(pos):
                    self.current = val
                    self.open = False
                    if self.on_select:
                        self.on_select(val)
                    return True
            self.open = False
            return True   # 点击展开层外 -> 关闭
        return False

    def _option_rects(self):
        opt_h = 28
        out = []
        for i, (val, lab) in enumerate(self.options):
            out.append((pygame.Rect(self.rect.x, self.rect.bottom + 2 + i * opt_h,
                                    self.rect.width, opt_h), (val, lab)))
        return out

    def _label_of(self, val):
        for v, lab in self.options:
            if v == val:
                return lab
        return str(val)

    def draw_base(self, surface, dark):
        pygame.draw.rect(surface, (60, 66, 82) if dark else (236, 242, 250),
                         self.rect, border_radius=5)
        pygame.draw.rect(surface, _bd(dark), self.rect, 1, border_radius=5)
        ts = rt(f_s, self._label_of(self.current), _light(dark))
        surface.blit(ts, (self.rect.x + 8, self.rect.centery - ts.get_height() // 2))
        tri = rt(f_s, "▾", _light(dark))
        surface.blit(tri, (self.rect.right - tri.get_width() - 6,
                           self.rect.centery - tri.get_height() // 2))

    def draw_open(self, surface, dark):
        """展开层: 在主对话框最后调用, 确保不被其它控件盖住。"""
        for r, (val, lab) in self._option_rects():
            pygame.draw.rect(surface, (46, 52, 66) if dark else (236, 242, 250),
                             r, border_radius=4)
            if val == self.current:
                pygame.draw.rect(surface, (70, 110, 165) if dark else (150, 190, 235),
                                 r, border_radius=4)
            ts = rt(f_s, lab, _light(dark))
            surface.blit(ts, (r.x + 8, r.centery - ts.get_height() // 2))


class PaintDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title = rt(f_m, "绘画模式", TXT)
        self.title_dark = rt(f_m, "绘画模式", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = TITLE_BAR_H

        self.scheme = "wiki"
        self.path_width = 4
        self.point_size = 6
        self.margin_w = 0.0
        self.margin_h = 0.0
        self.min_w = 1280
        self.min_h = 720
        self.draw_order = "simult"
        self.skip_invest = False
        self.all_points = True
        self.grid = False
        self.draw_rad = "none"
        self.multireset = "none"
        #: P5: 投影开关。值与 P4 的 `paint_projection.PROJECTIONS` / P3 的
        #: `cfg["projection"]` 同名(paint_render.py:160/232-233)。
        #: "equirect" = 等距圆柱(默认, 与历史行为一致); "conic" = 圆锥(Lambert 正形)。
        self.projection = "equirect"
        self.legend_pos = "auto"
        self.legend_bg = False
        self.labels = False
        self.use_map = True
        self.bg_color = None
        self.coord_unit = "deg"
        self.mode = "normal"
        self.region = COMMON_REGIONS["西北太平洋 WPAC"]
        self.region_preset = "西北太平洋 WPAC"
        self.map_path = None

        self.files: List[dict] = []
        self.sel_rows: List[bool] = []
        self._file_scroll = 0
        self._ext_tys: Dict[str, Optional[object]] = {}

        self._fields: Dict[str, InputField] = {}
        self._dropdowns: Dict[str, _Dropdown] = {}
        self._field_base_y: Dict[str, int] = {}
        self._dd_base_y: Dict[str, int] = {}

        self._result: Optional[pygame.Surface] = None
        self._stats: List[dict] = []
        self._status = ""
        self._status_ts = 0.0
        #: P5: 校验类提示不随时间消失(否则用户在 8s 后看不到自己填错了什么)
        self._status_sticky = False
        # 放大查看: 单击预览进入, 图片占满接近整个地图区域(留空隙)
        self._zoom = False
        self._zoom_level = 1.0          # 滚轮缩放倍率
        self._zoom_img_rect: Optional[pygame.Rect] = None   # 放大模式下图片实际绘制区
        self._ctx_menu = False          # 右键菜单(另存为)
        self._ctx_rect: Optional[pygame.Rect] = None

        self.x0 = self.y0 = 0
        self._browser: Optional[FileBrowserDialog] = None
        self._map_browse_rect: Optional[pygame.Rect] = None

    # ── 激活/布局 ──

    def activate(self):
        self._build_layout()
        # 已有文件时立即刷新统计(避免打开时统计面板空白)
        if self.files:
            self._refresh_stats_now()
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
        self.y0 = self.bg_rect.y + self.title_bar_height + 12
        self._rebuild_fields()
        self._rebuild_dropdowns()
        if self._browser is not None:
            self._browser._build_layout()

    def _row_y(self, idx):
        # 左列内容从面板内顶部(留出「绘制范围」标题)开始
        return self.y0 + 24 + idx * (ROW_H + ROW_GAP)

    def _rebuild_fields(self):
        for f in self._fields.values():
            f.deactivate()
        self._fields.clear()
        self._field_base_y.clear()
        rows = [
            # 区域输入仅当 mode==region 显示
            ("region", 2, _fmt_region(self.region), 24, None),
            ("path_width", 4, f"{self.path_width}", 4, int),
            ("point_size", 5, f"{self.point_size}", 4, int),
            ("margin_w", 6, f"{self.margin_w:g}", 6, float),
            ("margin_h", 7, f"{self.margin_h:g}", 6, float),
            ("min_w", 8, f"{self.min_w}", 5, int),
            ("min_h", 9, f"{self.min_h}", 5, int),
            ("map_path", 22, self.map_path or "", 160, None),
        ]
        inp_x = self.x0 + LABEL_W
        for key, idx, val, mlen, _conv in rows:
            rect = pygame.Rect(inp_x, self._row_y(idx), INPUT_W, 26)
            if key == "map_path":
                rect = pygame.Rect(inp_x, self._row_y(idx), LEFT_W - LABEL_W - 110, 26)
            fld = InputField(rect, max_length=mlen, dark=self.dark_mode)
            fld.set_text(val)
            fld.key = key
            self._fields[key] = fld
            self._field_base_y[key] = self._row_y(idx)

    def _rebuild_dropdowns(self):
        self._dropdowns.clear()
        self._dd_base_y.clear()
        defs = [
            # P5: 色阶只改**显示文案**, 不改 colors.py 的值 —— "经典/2023 相同"是对原版
            # Ty4 的忠实复刻(t4_track.py:59-60 PALETTE_CLASSIC = dict(PALETTE_2023)),
            # 不许为了"让它们不同"而编造 RGB, 也不删选项(那会丢掉"与 Ty4 同构"的事实)。
            ("scheme", 11, [(k, _SCHEME_UI_LABEL.get(k, v))
                            for k, v in COLOR_SCALE_SCHEMES.items()], self.scheme),
            ("coord_unit", 12, [("deg", "经纬度(°)"),
                                ("px", "像素(px)·启用宽度/大小")], self.coord_unit),
            ("order", 13, [("simult", "同时"), ("sort", "按强度排序"),
                           ("points_top", "定位点置顶")], self.draw_order),
            ("draw_rad", 14, [("none", "不绘画"), ("all", "全部绘画"),
                              ("last", "仅最后定位"), ("tropical", "仅热带部分")], self.draw_rad),
            # P5: 「多文件重置」已删(P3 定案: Ty4 的 MultipleFile 只实现 ==2, 选项语义为空;
            # 本渲染每台风独立画路径, 不存在跨文件连线需要重置)。该行让给「投影」开关。
            ("projection", 15, [("equirect", "等距圆柱"), ("conic", "圆锥(Lambert 正形)")],
             self.projection),
            ("legend_pos", 16, [(k, v) for k, v in LEGEND_POS_NAMES.items()], self.legend_pos),
        ]
        # 统一按第一列 x0+LABEL_W 排列(图例位置不再偏右)
        inp_x = self.x0 + LABEL_W
        for key, idx, opts, cur in defs:
            dd = _Dropdown(pygame.Rect(inp_x, self._row_y(idx), 240, 26),
                           opts, cur, lambda v, k=key: self._on_dd_select(k, v),
                           dark=self.dark_mode)
            self._dropdowns[key] = dd
            self._dd_base_y[key] = self._row_y(idx)
        dd = _Dropdown(pygame.Rect(inp_x + 250, self._row_y(2), 200, 26),
                       [(k, k) for k in COMMON_REGIONS.keys()], self.region_preset,
                       self._on_preset_select, dark=self.dark_mode)
        self._dropdowns["preset"] = dd
        self._dd_base_y["preset"] = self._row_y(2)

    def _on_dd_select(self, key, v):
        """下拉选中回调: 写属性 + 对"容易被误解/看不出效果"的选项给一条解释性提示。

        P5: 这些提示是**说明性**的, 不改变任何渲染参数 —— 例如"经典/2023 相同"是原版
        Ty4 的行为, 说清楚比"偷偷让它们不一样"正确。
        """
        setattr(self, key, v)
        msg = None
        sticky = False
        if key == "coord_unit":
            if v == "px":
                msg = ("坐标单位=像素：路径宽度 / 定点大小 生效"
                       "（线宽=路径宽度，点半径=定点大小/2）")
            else:
                msg = ("坐标单位=经纬度：线宽/点径走角度制，"
                       "「路径宽度 / 定点大小」这两个框不生效")
        elif key == "scheme" and v in ("wiki", "2023"):
            msg = ("说明：经典 Wiki 与 2023 两套色阶在本项目**与原版 Ty4 中**都相同"
                   "（Ty4 的 NewColor 开关同样无视觉差异），不是本程序的缺陷")
            sticky = True
        elif key == "projection":
            msg = ("投影=" + ("圆锥(Lambert 正形)" if v == "conic" else "等距圆柱")
                   + ("。若图纸外观暂未变化，说明渲染侧仍在接线中。"
                      if v == "conic" else "。"))
            sticky = (v == "conic")
        if msg:
            self._status = msg
            self._status_ts = time.time()
            self._status_sticky = sticky

    def _on_preset_select(self, v):
        self.region_preset = v
        self.region = COMMON_REGIONS[v]
        f = self._fields.get("region")
        if f:
            f.set_text(_fmt_region(self.region))

    # ── P5: 「区域」输入的即时校验 ──

    def _region_error(self) -> Optional[str]:
        """校验「区域」输入框, 返回可直接显示的中文错误文案, 合法则返回 None。

        语义与 README / `paint_render.resolve_bounds`(paint_render.py:88-96) 严格一致:
        4 个数, 顺序 `上,左,高,宽` ⇒ 经度 `[左, 左+宽]`、纬度 `[上-高, 上]`。

        ⚠ 为什么必须校验"高/宽 > 0": `resolve_bounds` 只在 `width>0 and height>0` 时才
        采纳 region, 否则**静默回落到轨迹 bbox 分支**(paint_render.py:95-96) —— 用户会
        以为"设了范围", 实际拿到的是自动范围, 这正是第一轮记录的"填错不报错只画出怪图"。
        """
        f = self._fields.get("region")
        if f is None:
            return None
        txt = f.get_text().strip()
        if not txt:
            return "区域不能为空（格式：上,左,高,宽）"
        raw = [v.strip() for v in txt.replace("，", ",").split(",") if v.strip()]
        if len(raw) != 4:
            return f"需要 4 个数（上,左,高,宽），当前 {len(raw)} 个"
        try:
            nums = [float(v) for v in raw]
        except ValueError:
            return "只能填数字（格式：上,左,高,宽）"
        top, left, height, width = nums
        if height <= 0 or width <= 0:
            return f"高/宽必须 > 0（当前 高={height:g}, 宽={width:g}）——否则范围会被忽略"
        if top > 90.0 or top - height < -90.0:
            return (f"纬度越界：需要 -90 ≤ 上-高 且 上 ≤ 90"
                    f"（当前 上={top:g}, 上-高={top - height:g}）")
        if left < 0.0 or left > 360.0:
            return f"左边界需在 0..360（当前 {left:g}）"
        if left + width > 360.0 + 1e-6:
            return (f"右边界 {left + width:g} 超出 360°（本渲染不支持跨 0° 缝的区域，"
                    f"请改用 全局 或缩小宽）")
        return None

    def _region_ok(self) -> bool:
        return self._region_error() is None

    def _set_mode(self, mode: str) -> None:
        """切换绘制范围。P5: 离开「区域」时**失活隐藏的区域输入框**。

        否则该输入框虽然在普通/全局模式下不可见(_draw_left 不画), 却仍会抢走点击与
        键盘输入(input_field.py:304-307 左键点在 rect 内即 activate_at) —— 用户会在
        一片空白处把光标"丢"进一个看不见的框里。
        """
        self.mode = mode
        if mode != "region":
            f = self._fields.get("region")
            if f is not None:
                f.deactivate()
            if self._status_sticky:
                self._status = ""
                self._status_sticky = False

    def _selected_paths(self):
        return [d['path'] for d, s in zip(self.files, self.sel_rows) if s]

    def _selected_tys(self):
        out = []
        for p in self._selected_paths():
            ty = self._get_ty_from_path(p)
            if ty is not None:
                out.append(ty)
        return out

    def _get_ty_from_path(self, path):
        if path in self._ext_tys:
            return self._ext_tys[path]
        try:
            from .data_repo import DataRepository
            ty = DataRepository(None, None).parse_typhoon_file(path, add_to_list=False)
        except Exception:
            ty = None
        self._ext_tys[path] = ty
        return ty

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
        x = self.x0 + LEFT_W + 20
        w = self.bg_rect.right - PAD - x
        h = self.bg_rect.bottom - self.y0 - BOTTOM_BAR_H - 10
        return pygame.Rect(x, self.y0, w, h)

    def _bottom_top(self):
        return self.bg_rect.bottom - BOTTOM_BAR_H

    def _preview_rect(self):
        bt = self._bottom_top()
        w = self.bg_rect.width - 2 * PAD - STATS_W - 14
        return pygame.Rect(self.x0, bt + 8, w, PREVIEW_H)

    def _stats_rect(self):
        bt = self._bottom_top()
        x = self.bg_rect.right - PAD - STATS_W
        # 统计面板固定在底部, 高度延伸至动作按钮之上(动作按钮在预览下方)
        h = PREVIEW_H + 30
        return pygame.Rect(x, bt + 8, STATS_W, h)

    def _action_button_rects(self):
        bt = self._bottom_top()
        base_y = bt + PREVIEW_H + 48
        out = {}
        x = self.bg_rect.right - PAD - 110
        for key, lab in (("close", "关闭"), ("clear", "清除"), ("save", "保存路径图"),
                         ("draw", "开始绘画")):
            w0 = 150 if key == "draw" else 110
            x -= w0
            out[key] = pygame.Rect(x, base_y, w0, 34)
            x -= 10
        return out

    def _mode_btn_rect(self, mode):
        names = {"normal": 0, "region": 1, "global": 2}
        return pygame.Rect(self.x0 + names[mode] * 132, self._row_y(0), 124, 30)

    def _file_toolbar_rects(self):
        fr = self._file_panel_rect()
        labels = [("add", "导入台风"), ("oneall", "一键全部"), ("del", "移除"),
                  ("dedup", "去重"), ("clear", "清空"), ("refresh", "刷新"),
                  ("all", "全选"), ("unall", "全不选")]
        out = {}
        # 工具条整体右下偏移: 起于文件面板右下角以上, 靠右
        x = fr.right
        y = fr.y + 6
        for key, lab in reversed(labels):
            w0 = 96
            x -= w0 + 6
            out[key] = pygame.Rect(x, y, w0, 28)
        return out

    def _checkbox_rect(self, idx):
        # 两列布局: 左列 idx 0-2, 右列 idx 3-5; idx6(bg) 也放左列第4? 用3+4排布
        # 6 个勾选 + 背景: 左列 3 (0-2), 右列 3 (3-5), 背景放右列第4 (6->row21)
        if idx == 6:
            return pygame.Rect(self.x0 + LABEL_W + 320, self._row_y(21), 18, 18)
        col = 0 if idx < 3 else 1
        r = idx % 3
        x = self.x0 + LABEL_W + col * 320
        return pygame.Rect(x, self._row_y(18 + r), 18, 18)

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
            elif self._ctx_menu:
                self._ctx_menu = False
            else:
                for dd in self._dropdowns.values():
                    if dd.open:
                        dd.open = False
                        return True
                self.deactivate()
            return True
        # P5: 「区域」输入框只在 region 模式下可见(_draw_left 仅在该模式绘制它),
        # 因此事件也必须按可见性路由 —— 否则普通/全局模式下会有一个**看不见的输入框**
        # 抢走点击与键盘输入(input_field.py:304-307 左键点在 rect 内就 activate_at)。
        for key, f in self._fields.items():
            if key == "region" and self.mode != "region":
                continue
            if f.handle_event(e):
                return True
        if e.type == pygame.MOUSEBUTTONDOWN:
            # 右键: 弹「另存为」菜单(放大模式与普通模式均可)
            if e.button == 3:
                self._ctx_menu = True
                self._ctx_rect = pygame.Rect(e.pos[0], e.pos[1], 110, 30)
                return True
            if e.button in (4, 5):
                d = -1 if e.button == 4 else 1
                if self._zoom:
                    # 滚轮: 缩放倍率
                    self._zoom_level = max(1.0, min(8.0, self._zoom_level * (1.15 ** d)))
                elif self._file_panel_rect().collidepoint(e.pos):
                    self._file_scroll = max(0, self._file_scroll + d)
                return True
            if e.button == 1:
                self._click(e.pos)
            return True
        return False

    def _click(self, pos):
        # 右键菜单: 点「另存为」或空白处关闭
        if self._ctx_menu:
            if self._ctx_rect and self._ctx_rect.collidepoint(pos):
                self._ctx_menu = False
                self._do_save()
            else:
                self._ctx_menu = False
            return
        if self._zoom:
            # 单击图片内 -> 缩回; 点图片外 -> 退出放大
            if self._zoom_img_rect and self._zoom_img_rect.collidepoint(pos):
                self._zoom = False
            else:
                self._zoom = False
            return
        x, y = pos
        # 下拉展开层优先(避免被下方控件吃掉点击)
        any_open = any(dd.open for dd in self._dropdowns.values())
        for dd in self._dropdowns.values():
            if dd.open:
                if dd.toggle(pos):
                    return
        if any_open:
            return
        for key, rect in self._action_button_rects().items():
            if rect.collidepoint(x, y):
                self._on_action(key)
                return
        if self._preview_rect().collidepoint(x, y) and self._result is not None:
            self._zoom = True
            self._zoom_level = 1.0
            self._zoom_img_rect = self._compute_zoom_rect()
            return
        for mode in ("normal", "region", "global"):
            if self._mode_btn_rect(mode).collidepoint(x, y):
                self._set_mode(mode)
                return
        for key, dd in self._dropdowns.items():
            if dd.toggle(pos):
                return
        if self._checkbox_click(x, y):
            return
        if self._map_browse_rect and self._map_browse_rect.collidepoint(x, y):
            self._open_browser("map", self._on_map_picked)
            return
        if self._file_toolbar_click(x, y):
            return
        fr = self._file_panel_rect()
        if fr.collidepoint(x, y) and y > fr.y + 72:
            idx = self._file_scroll + (y - (fr.y + 74)) // 30
            if 0 <= idx < len(self.files):
                self.sel_rows[idx] = not self.sel_rows[idx]
                self._refresh_stats_now()

    def _checkbox_click(self, x, y):
        chk = [("skip_invest", 0), ("all_points", 1), ("grid", 2),
               ("labels", 3), ("use_map", 4), ("legend_bg", 5), ("bg_btn", 6)]
        for key, idx in chk:
            box = self._checkbox_rect(idx)
            if box.collidepoint(x, y):
                if key == "bg_btn":
                    self.bg_color = None if self.bg_color is not None else (200, 220, 240)
                    self.use_map = False
                else:
                    setattr(self, key, not getattr(self, key))
                return True
        return False

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
            # P5: 「刷新」原来只改状态文字(不重扫), 用户以为列表被重读 —— 现在真刷新尺寸,
            # 并如实报告"文件已不存在"的条目数(不静默删行, 由用户决定是否"移除")。
            gone = 0
            for d in self.files:
                p = d.get('path')
                if p and os.path.isfile(p):
                    d['size'] = _fmt_size(p)
                else:
                    gone += 1
            self._status = (f"已刷新尺寸（{len(self.files)} 个文件"
                            + (f"，其中 {gone} 个已不存在" if gone else "") + "）")
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
                               'dir': os.path.dirname(p), 'size': _fmt_size(p)})
            cur.add(p)
            added += 1
        # 新导入的文件默认勾选(与 Ty4 交互一致: 导入即可用, 不必先"全选")
        self.sel_rows = [True] * len(self.files)
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
        self._refresh_stats_now()

    def _on_map_picked(self, path):
        if path:
            self.map_path = path[0]
            f = self._fields.get("map_path")
            if f:
                f.set_text(path[0])

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

    # ── 渲染/保存 ──

    def _collect_fields(self):
        out = {}
        for key, f in self._fields.items():
            txt = f.get_text().strip()
            if key == "region":
                # P5: 非法输入**不写入** out["region"]（解析失败的旧行为不变），
                # 但错误文案由 _region_error() 统一给出, 供 UI 常驻显示与阻止渲染。
                if self._region_error() is None:
                    try:
                        nums = [float(v.strip()) for v in
                                txt.replace("，", ",").split(",") if v.strip()]
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
        # P5: 区域模式下输入非法 ⇒ **不再静默用旧值画一张怪图**, 而是明确报错并停下。
        # 依据: resolve_bounds 只在 width>0 and height>0 时采纳 region, 否则静默回落
        # 轨迹 bbox(paint_render.py:95-96) —— 用户会以为"设了范围"。
        if self.mode == "region":
            _err = self._region_error()
            if _err is not None:
                self._status = f"区域输入有误：{_err}"
                self._status_ts = time.time()
                self._status_sticky = True
                return
        region = vals.get("region") or self.region
        if self.mode == "region":
            self.region = region
            self._status_sticky = False
        else:
            self._status_sticky = False
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
                # P5: 投影透传。字段名与 P3/P4 冻结约定一致(paint_render.py:232-233
                # 读 `opts.get("projection")`, 非法/缺省 ⇒ "equirect")。
                "projection": self.projection,
                # P5: 「多文件重置」(multireset) 已按 P3 定案移除 —— 不再透传。
                "legend_pos": self.legend_pos,
                "legend_entries": WIKI_LEGEND_ENTRIES,
                "legend_bg": self.legend_bg,
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
            # 截图统一进 output/(用户 2026-09-19 精简主目录)
            pic_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "output", "picture")
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
            self.draw_title_bar(surface, r, "绘画模式", SETTINGS_TEXT_LIGHT, title_font=f_l)
        else:
            self.draw_background(surface, r)
            self.draw_title_bar(surface, r, "绘画模式", TXT, title_font=f_l)

        if self._zoom:
            # 放大查看模式: 只画放大图(盖住其它面板)
            self._draw_preview(surface, dark)
            if self._ctx_menu:
                self._draw_ctx_menu(surface, dark)
            return
        self._draw_left(surface, dark)
        self._draw_file_panel(surface, dark)
        self._draw_preview(surface, dark)
        self._draw_stats(surface, dark)
        self._draw_action_buttons(surface, dark)
        self._draw_status(surface, dark)
        # 下拉展开层最后绘制(置顶, 不被盖住)
        for dd in self._dropdowns.values():
            if dd.open:
                dd.draw_open(surface, dark)
        if self._ctx_menu:
            self._draw_ctx_menu(surface, dark)
        if self._browser is not None and self._browser.active:
            self._browser.draw(surface)

    def _draw_ctx_menu(self, surface, dark):
        """右键菜单: 仅「另存为」。"""
        if self._ctx_rect is None:
            return
        r = self._ctx_rect
        menu = pygame.Rect(r.x, r.y, 110, 30)
        pygame.draw.rect(surface, (46, 52, 66) if dark else (240, 244, 252), menu,
                         border_radius=5)
        pygame.draw.rect(surface, _bd(dark), menu, 1, border_radius=5)
        ts = rt(f_s, "另存为…", _light(dark))
        surface.blit(ts, (menu.x + 10, menu.y + (menu.h - ts.get_height()) // 2))
        self._ctx_rect = menu

    def _button(self, surface, rect, text, bg, dark, fg=None, border=5):
        pygame.draw.rect(surface, bg, rect, border_radius=border)
        ts = rt(f_s, text, fg or (255, 255, 255))
        if ts.get_width() > rect.width - 10:
            sc = max(0.5, (rect.width - 10) / max(1, ts.get_width()))
            ts = pygame.transform.smoothscale(
                ts, (max(1, int(ts.get_width() * sc)), max(1, int(ts.get_height() * sc))))
        surface.blit(ts, (rect.centerx - ts.get_width() // 2,
                          rect.centery - ts.get_height() // 2))

    def _label(self, surface, text, x, y, dark, bright=False, font=f_s):
        ts = rt(font, text, _light(dark, bright))
        surface.blit(ts, (x, y + (26 - ts.get_height()) // 2))

    def _checkbox(self, surface, box, label, val, dark):
        pygame.draw.rect(surface, (60, 66, 82) if dark else (200, 210, 226), box, border_radius=3)
        if val:
            pygame.draw.line(surface, (110, 230, 150), (box.x + 3, box.centery),
                             (box.x + box.w // 2, box.bottom - 3), 3)
            pygame.draw.line(surface, (110, 230, 150), (box.x + box.w // 2, box.bottom - 3),
                             (box.right - 2, box.y + 3), 3)
        ts = rt(f_s, label, _light(dark))
        surface.blit(ts, (box.right + 8, box.y + (box.h - ts.get_height()) // 2))

    def _draw_left(self, surface, dark):
        lp = self._left_panel_rect()
        pygame.draw.rect(surface, _pb(dark, 1), lp, border_radius=8)
        pygame.draw.rect(surface, _bd(dark), lp, 1, border_radius=8)
        # 面板内左边距 = lp.x + 12, 文字不超左界
        lx = lp.x + 12
        inp_x = lx + LABEL_W
        row = self._row_y

        # 绘制范围(模式选择)
        self._label(surface, "绘制范围", lx, row(0) - 26, dark, bright=True, font=f_m)
        for mode, lab in (("normal", "普通"), ("region", "区域"), ("global", "全局")):
            r = self._mode_btn_rect(mode)
            r = pygame.Rect(r.x + 12, r.y, r.w, r.h)
            self._button(surface, r, lab,
                         (60, 120, 190) if self.mode == mode else _pb(dark, 0), dark)
        # 区域输入框: 仅在区域模式显示
        if self.mode == "region":
            self._label(surface, "区域(上,左,高,宽)", lx, row(2), dark)
            self._fields["region"].draw(surface)
            # P5: 非法输入时给输入框加**常驻红框**(不依赖 8 秒就消失的状态栏)
            if self._region_error() is not None:
                pygame.draw.rect(surface, (235, 90, 90),
                                 self._fields["region"].rect.inflate(6, 6), 2,
                                 border_radius=5)
            self._dropdowns["preset"].draw_base(surface, dark)
        # 参数
        # 参数（P5: 标题放在 row(3) 这个**预留空行**里。原来写 row(3)-26 = row(2)+2,
        # 与上一行内容同 y 完全叠字 —— 区域模式下就是"参数上,左,高,宽)"那种糊字。
        # 实测 probe_paint_ui_overlap.py: row(3)/row(10)/row(17) 三行本来就没有任何元素。）
        self._label(surface, "参数", lx, row(3) + 1, dark, bright=True, font=f_m)
        rows = [("路径宽度(px)", "path_width"), ("定点大小(px)", "point_size"),
                ("半宽留空(°)", "margin_w"), ("半高留空(°)", "margin_h"),
                ("最小宽度(px)", "min_w"), ("最小高度(px)", "min_h")]
        for i, (lab, key) in enumerate(rows):
            self._label(surface, lab, lx, row(4 + i), dark)
            self._fields[key].draw(surface)
        # 下拉
        # P5: 同上, 标题放 row(10) 预留空行(原来 row(10)-26 与"最小高度(px)"完全叠字)
        self._label(surface, "设置", lx, row(10) + 1, dark, bright=True, font=f_m)
        dd_lab = [("scheme", "色阶方案"), ("coord_unit", "坐标单位"),
                  ("order", "绘画顺序"), ("draw_rad", "风圈"),
                  ("projection", "投影"), ("legend_pos", "图例位置")]
        for i, (key, lab) in enumerate(dd_lab):
            self._label(surface, lab, lx, row(11 + i), dark)
            self._dropdowns[key].draw_base(surface, dark)
        # 勾选(两列 4+3)
        # P5: 同上, 标题放 row(17) 预留空行(原来 row(17)-26 与"图例位置"完全叠字)
        self._label(surface, "显示", lx, row(17) + 1, dark, bright=True, font=f_m)
        chk = [("skip_invest", "忽略前期扰动定位"), ("all_points", "显示所有定位点"),
               ("grid", "显示经纬网"), ("labels", "显示文字标注"),
               ("use_map", "地图底图"), ("legend_bg", "图例底框"),
               ("bg_btn", "自定义背景")]
        for i, (key, lab) in enumerate(chk):
            if key == "bg_btn":
                val = self.bg_color is not None
            else:
                val = getattr(self, key)
            self._checkbox(surface, self._checkbox_rect(i), lab, val, dark)
        # 地图文件
        self._label(surface, "地图文件", lx, row(22), dark, bright=True)
        self._fields["map_path"].draw(surface)
        br = pygame.Rect(inp_x + 360, row(22), 100, 26)
        self._button(surface, br, "选择地图", (60, 110, 175), dark)
        self._map_browse_rect = br

    def _draw_file_panel(self, surface, dark):
        fr = self._file_panel_rect()
        pygame.draw.rect(surface, _pb(dark, 1), fr, border_radius=8)
        pygame.draw.rect(surface, _bd(dark), fr, 1, border_radius=8)
        t = rt(f_m, f"文件列表 ({len(self.files)} 个文件)",
               _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (fr.x + 8, fr.y - 34))
        # 工具条(右下偏移: 靠右行)
        for key, rect in self._file_toolbar_rects().items():
            color = {"add": (70, 150, 110), "oneall": (64, 118, 190), "del": (170, 100, 80),
                     "dedup": (120, 110, 90), "clear": (150, 90, 90), "refresh": (90, 120, 150),
                     "all": (80, 130, 170), "unall": (110, 95, 95)}[key]
            lab = {"add": "导入台风", "oneall": "一键全部", "del": "移除", "dedup": "去重",
                   "clear": "清空", "refresh": "刷新", "all": "全选", "unall": "全不选"}[key]
            self._button(surface, rect, lab, color, dark)
        # 表头固定顶部
        hdr = fr.y + 44
        tcol = _light(dark, True)
        cols = [("顺序", 56), ("文件名", int(fr.width * 0.28)), ("所在路径", int(fr.width * 0.46)),
                ("文件尺寸", int(fr.width * 0.16))]
        hx = fr.x
        for lab, w0 in cols:
            ts = rt(f_s, lab, tcol)
            surface.blit(ts, (hx + 6, hdr))
            hx += w0
        pygame.draw.line(surface, _bd(dark), (fr.x, hdr + 22), (fr.right, hdr + 22), 1)
        top = hdr + 26
        total = len(self.sel_rows)
        vis = max(1, (fr.bottom - top - 6) // 30)
        if total > vis:
            bar_h = max(16, int((fr.bottom - top) * vis / total))
            bar_y = top + int((fr.bottom - top - bar_h) *
                              (self._file_scroll / max(1, total - vis)))
            pygame.draw.rect(surface, (120, 130, 150) if dark else (150, 165, 185),
                             (fr.right - 6, bar_y, 4, bar_h))
        y = top
        for i in range(self._file_scroll, min(total, self._file_scroll + vis)):
            yy = y + (i - self._file_scroll) * 30
            if yy + 30 > fr.bottom:
                break
            row = pygame.Rect(fr.x, yy, fr.width, 29)
            if self.sel_rows[i]:
                pygame.draw.rect(surface, (46, 78, 118) if dark else (185, 215, 240), row)
            d = self.files[i]
            vals = [str(i + 1), d['name'], d['dir'], d['size']]
            hx = fr.x
            for j, (lab, w0) in enumerate(cols):
                ts = rt(f_s, vals[j], _light(dark))
                surface.blit(ts, (hx + 6, yy + 5))
                hx += w0

    def _zoom_view_rect(self):
        """放大查看区域: 整个对话框内容区(留边缘空隙)。"""
        return pygame.Rect(self.bg_rect.x + 60, self.bg_rect.y + self.title_bar_height + 40,
                           self.bg_rect.width - 120, self.bg_rect.height - self.title_bar_height - 100)

    def _compute_zoom_rect(self):
        """计算放大模式下图片实际绘制矩形(随滚轮缩放)。"""
        if self._result is None:
            return None
        img = self._result
        vr = self._zoom_view_rect()
        sc = min(vr.width / img.get_width(), vr.height / img.get_height()) * self._zoom_level
        tw = max(1, int(img.get_width() * sc))
        th = max(1, int(img.get_height() * sc))
        return pygame.Rect(vr.centerx - tw // 2, vr.centery - th // 2, tw, th)

    def _draw_preview(self, surface, dark):
        pr = self._preview_rect()
        pygame.draw.rect(surface, (24, 28, 38) if dark else (200, 210, 226), pr, border_radius=8)
        pygame.draw.rect(surface, _bd(dark), pr, 1, border_radius=8)
        if self._result is None:
            ts = rt(f_m, "点击「开始绘画」生成路径图 (单击预览可放大)", _light(dark))
            surface.blit(ts, (pr.centerx - ts.get_width() // 2,
                              pr.centery - ts.get_height() // 2))
            return
        img = self._result
        if self._zoom:
            # 放大查看: 占满接近整个地图区域(留空隙), 滚轮缩放
            self._zoom_img_rect = self._compute_zoom_rect()
            vr = self._zoom_view_rect()
            tw = self._zoom_img_rect.width
            th = self._zoom_img_rect.height
            prev = pygame.transform.smoothscale(img, (tw, th))
            surface.blit(prev, (self._zoom_img_rect.x, self._zoom_img_rect.y))
            tip = rt(f_s, f"放大 ×{self._zoom_level:.1f} · 滚轮缩放 · 单击图片缩回 · 点外部退出 · 右键另存为",
                     (245, 245, 250))
            surface.blit(tip, (vr.x + 8, vr.y + 6))
            return
        self._zoom_img_rect = None
        sc = min(pr.width / img.get_width(), pr.height / img.get_height())
        tw = max(1, int(img.get_width() * sc))
        th = max(1, int(img.get_height() * sc))
        prev = pygame.transform.smoothscale(img, (tw, th))
        surface.blit(prev, (pr.centerx - tw // 2, pr.centery - th // 2))
        tip = rt(f_s, "单击放大 · 右键另存为", (245, 245, 250))
        surface.blit(tip, (pr.right - tip.get_width() - 10, pr.y + 6))

    def _draw_stats(self, surface, dark):
        sr = self._stats_rect()
        pygame.draw.rect(surface, _pb(dark, 1), sr, border_radius=8)
        pygame.draw.rect(surface, _bd(dark), sr, 1, border_radius=8)
        t = rt(f_m, "ACE / 路径统计", _light(dark, True) if dark else (30, 60, 110))
        surface.blit(t, (sr.x + 10, sr.y + 10))
        # 表头
        hdrs = [("名称", 150), ("ACE", 80), ("km", 76), ("点", 48)]
        x = sr.x + 10
        for lab, w0 in hdrs:
            ts = rt(f_s, lab, _light(dark, True))
            surface.blit(ts, (x, sr.y + 48))
            x += w0
        y = sr.y + 74
        sum_ace = 0.0
        sum_km = 0.0
        sum_n = 0
        for s in self._stats:
            vals = [s['name'], f"{s['ace']:.2f}", f"{s['path_km']:.0f}", str(s['npts'])]
            sum_ace += s['ace']
            sum_km += s['path_km']
            sum_n += s['npts']
            x = sr.x + 10
            for j, (lab, w0) in enumerate(hdrs):
                ts = rt(f_s, vals[j], _light(dark))
                surface.blit(ts, (x, y))
                x += w0
            y += 22
            if y > sr.bottom - 34:
                ts = rt(f_s, f"... 共 {len(self._stats)} 个", _light(dark))
                surface.blit(ts, (sr.x + 10, sr.bottom - 52))
                break
        # 总计: 固定面板底部
        pygame.draw.line(surface, _bd(dark), (sr.x + 8, sr.bottom - 30),
                         (sr.right - 8, sr.bottom - 30), 1)
        tot = [f"总计 {len(self._stats)}", f"{sum_ace:.2f}", f"{sum_km:.0f}", str(sum_n)]
        y = sr.bottom - 26
        x = sr.x + 10
        for j, (lab, w0) in enumerate(hdrs):
            ts = rt(f_s, tot[j], (120, 230, 160) if dark else (40, 130, 60))
            surface.blit(ts, (x, y))
            x += w0

    def _draw_action_buttons(self, surface, dark):
        for key, rect in self._action_button_rects().items():
            color = {"draw": (60, 150, 100), "save": (60, 110, 175),
                     "clear": (160, 110, 70), "close": (150, 80, 80)}[key]
            lab = {"draw": "开始绘画", "save": "保存路径图",
                   "clear": "清除", "close": "关闭"}[key]
            self._button(surface, rect, lab, color, dark)

    def _draw_status(self, surface, dark):
        # P5: 校验类错误(status_sticky)常驻, 其余状态仍 8 秒后淡出
        if self._status and (self._status_sticky or time.time() - self._status_ts < 8):
            pr = self._preview_rect()
            ts = rt(f_s, self._status, (240, 205, 110) if dark else (150, 100, 30))
            surface.blit(ts, (self.x0, pr.bottom - 32))
