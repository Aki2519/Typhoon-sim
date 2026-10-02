# py/paint_file_browser.py
"""Ty4 风格的「打开文件」浏览器对话框。

布局:
  - 顶部: 当前目录路径(可手动输入 + 上一级按钮)
  - 中部: 左侧「文件夹文件树」 + 右侧「文件列表」(可多选勾选)
  - 底部: 寻找范围(当前目录/所有子目录) + 已找到 N 个文件 · 全选/全不选/确定/关闭

用于绘画模式导入台风文件(*.dat/*.txt)与选择地图文件(*.png/*.jpg/...)。
完成后把所选文件绝对路径列表交给 on_ok 回调。
"""
from __future__ import annotations

import os
import time
import pygame
from typing import Callable, List, Optional, Tuple

from .constants import f_s, f_m, rt, SETTINGS_TEXT_LIGHT, DIALOG_TITLE_BAR_HEIGHT
from .dialog_base import DraggableDialog
from .input_field import InputField

EXT_MAP = {
    "typhoon": (".dat", ".txt", ".bde", ".bdeck"),
    "map": (".png", ".jpg", ".jpeg", ".jpe", ".bmp"),
}

_ITEM_H = 30
_HEADER_H = 30


def _light(dark):
    return SETTINGS_TEXT_LIGHT if dark else (25, 32, 48)


def _bg(dark, tone=0):
    if not dark:
        return ((232, 239, 250), (210, 220, 236), (238, 244, 252))[tone]
    return ((44, 50, 63), (52, 58, 72), (30, 34, 44))[tone]


def _border(dark):
    return (95, 108, 132) if dark else (160, 175, 195)


def _ellipsize(text: str, font, max_w: int) -> str:
    """把**单行**文字按像素宽截成 `prefix…`（不换行、不溢出、不压到相邻列）。

    P9（ui-experience 标准 1）：列表/树是固定行高的，长文件名/长路径必须截断。
    ⚠ **不能用 `rt(..., max_width=)`** —— 那是**多行自动换行**
    （见 `app/constants/fonts.py:241-254`），会把一行撑成两行并与下一行重叠，
    正是标准 1 要避免的。这里用 `Font.size()`（C 实现、不分配 Surface）二分求最长前缀。
    """
    if max_w <= 0:
        return ""
    try:
        if font.size(text)[0] <= max_w:
            return text
        ell_w = font.size("…")[0]
        budget = max_w - ell_w
        if budget <= 0:
            return "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size(text[:mid])[0] <= budget:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"
    except Exception:
        return text


class FileBrowserDialog(DraggableDialog):
    def __init__(self, sim, purpose="typhoon"):
        super().__init__(sim)
        self.title = rt(f_m, "打开文件", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self.purpose = purpose
        self.exts = EXT_MAP.get(purpose, EXT_MAP["typhoon"])

        self.cur_dir: str = ""
        self.recursive: bool = False
        self.candidates: List[str] = []
        self.checked: List[bool] = []
        self.tree: List[dict] = []           # 文件树节点: {name, path, depth}
        self._tree_scroll = 0
        self._file_scroll = 0
        self.on_ok: Optional[Callable[[List[str]], None]] = None
        self._path_field: Optional[InputField] = None
        self._msg = ""
        self._msg_ts = 0.0
        self.x0 = self.y0 = 0

    # ── 激活 ──

    def activate(self, start_dir: Optional[str] = None, on_ok=None):
        if start_dir and os.path.isdir(start_dir):
            self.cur_dir = os.path.normpath(start_dir)
        elif not self.cur_dir or not os.path.isdir(self.cur_dir):
            auto = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "typhoon")
            self.cur_dir = os.path.normpath(auto if os.path.isdir(auto) else os.path.expanduser("~"))
        self.on_ok = on_ok
        self._build_layout()
        self._refresh()
        super().activate()

    def deactivate(self):
        if self._path_field:
            self._path_field.deactivate()
        super().deactivate()

    def _build_layout(self):
        sw, sh = self.sim.screen_width, self.sim.screen_height
        w = min(1080, sw - 60)
        h = min(760, sh - 120)
        self.bg_rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        self.x0 = self.bg_rect.x + 16
        self.y0 = self.bg_rect.y + self.title_bar_height + 10
        pf = InputField(pygame.Rect(self.x0 + 60, self.y0 + 2,
                                    # P9(K3): 原来宽度 = bg.width-200, 而「↑ 上一级」按钮
                                    # 的左缘在 x0+60+W-230 ⇒ **恒定重叠 30px**, 且事件先给
                                    # 输入框, 按钮左侧 30px 点了没反应。现在按"按钮宽 110 +
                                    # 间隙 8"预留, 重叠归零。
                                    self.bg_rect.width - 200 - 118, 32),
                        max_length=500, dark=self.dark_mode)
        pf.set_text(self.cur_dir)
        self._path_field = pf

    # ── 几何 ──

    def _up_rect(self):
        # P9(K4): 高 26 → 32（桌面热区 ≥32px）；与路径框同 y 同高，仍不重叠
        return pygame.Rect(self.x0 + 60 + self.bg_rect.width - 120 - 110,
                           self.y0 + 2, 110, 32)

    def _range_rect(self):
        # P9(K4): 高 26 → 32（底边 y0+76 仍在上方树面板 y0+84 之前）
        return pygame.Rect(self.x0, self.y0 + 44, 300, 32)

    def _tree_rect(self):
        y = self.y0 + 84
        h = self.bg_rect.bottom - 64 - y
        return pygame.Rect(self.x0, y, 240, h)

    def _files_rect(self):
        y = self.y0 + 84
        h = self.bg_rect.bottom - 64 - y
        x = self.x0 + 260
        w = self.bg_rect.width - 260 - 16
        return pygame.Rect(x, y, w, h)

    def _bottom_btns(self):
        # P9(K4): 按钮高 30 → 32，基线由 bottom-52 上移到 bottom-56（保持 ~24px 下边距）
        y = self.bg_rect.bottom - 56
        x = self.bg_rect.right - 16
        out = {}
        for key, lab, c in (("ok", "确定", (70, 150, 110)), ("close", "关闭", (150, 90, 90)),
                            ("unall", "全不选", (120, 100, 100)), ("all", "全选", (80, 130, 170))):
            x -= 84
            out[key] = pygame.Rect(x, y, 84, 32)      # P9(K4): 30 → 32
            x -= 10
        return out

    # ── 目录/文件刷新 ──

    def _refresh(self):
        self.cur_dir = os.path.abspath(self.cur_dir)
        # 文件树: 显示当前目录的祖先链 + 各层子目录
        self.tree = []
        parts = self.cur_dir.split(os.sep)
        path = ""
        # 根
        root = parts[0] + os.sep if len(parts) > 1 and parts[0] else os.sep
        has_win = len(parts) > 1 and parts[1] != ""
        depth_start = 0 if not has_win else 1
        # Windows: parts = ['C:', '', 'Users', ...]
        acc = ""
        for i, p in enumerate(parts):
            if p == "":
                continue
            acc = p if not acc else os.path.join(acc, p)
            if i < len(parts) - 1:
                self.tree.append({"name": p + os.sep, "path": acc, "depth": 0})
        # 直接子目录
        try:
            entries = sorted(os.listdir(self.cur_dir))
        except OSError as e:
            self._msg = f"无法打开目录: {e}"
            self._msg_ts = time.time()
            return
        subdirs = [e for e in entries
                   if os.path.isdir(os.path.join(self.cur_dir, e)) and not e.startswith('.')]
        for e in subdirs:
            self.tree.append({"name": "▸ " + e, "path": os.path.join(self.cur_dir, e),
                              "depth": 1, "leaf": True})
        # 文件
        files = []
        if self.recursive:
            for root, _d, fs in os.walk(self.cur_dir):
                for fn in fs:
                    if fn.lower().endswith(self.exts) and not fn.startswith('.'):
                        files.append(os.path.join(root, fn))
            files.sort()
        else:
            for e in entries:
                p = os.path.join(self.cur_dir, e)
                if os.path.isfile(p) and e.lower().endswith(self.exts) and not e.startswith('.'):
                    files.append(p)
            files.sort()
        self.candidates = files
        self.checked = [False] * len(files)
        self._file_scroll = 0
        self._tree_scroll = 0
        if self._path_field:
            self._path_field.set_text(self.cur_dir)
        self._msg = f"已找到 {len(files)} 个文件。"
        self._msg_ts = time.time()

    # ── 事件 ──

    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if self._path_field and self._path_field.handle_event(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN:
            if e.button in (1,):
                self._click(e.pos)
            elif e.button == 4:
                self._scroll(-1, e.pos)
            elif e.button == 5:
                self._scroll(1, e.pos)
            return True
        return False

    def _scroll(self, d, pos):
        tr = self._tree_rect()
        fr = self._files_rect()
        if tr.collidepoint(pos):
            self._tree_scroll = max(0, self._tree_scroll + d)
        elif fr.collidepoint(pos):
            self._file_scroll = max(0, self._file_scroll + d)

    def _click(self, pos):
        x, y = pos
        for key, r in self._bottom_btns().items():
            if r.collidepoint(x, y):
                self._bottom_action(key)
                return
        if self._up_rect().collidepoint(x, y):
            self.cur_dir = os.path.dirname(self.cur_dir)
            self._refresh()
            return
        if self._range_rect().collidepoint(x, y):
            self.recursive = not self.recursive
            self._refresh()
            return
        # 文件树
        tr = self._tree_rect()
        if tr.collidepoint(x, y):
            ylocal = y - tr.y
            if ylocal >= _HEADER_H:
                idx = self._tree_scroll + (ylocal - _HEADER_H) // _ITEM_H
                if 0 <= idx < len(self.tree):
                    node = self.tree[idx]
                    if os.path.isdir(node['path']):
                        self.cur_dir = node['path']
                        self._refresh()
            return
        # 文件列表(勾选)
        fr = self._files_rect()
        if fr.collidepoint(x, y):
            ylocal = y - fr.y
            if ylocal >= _HEADER_H:
                idx = self._file_scroll + (ylocal - _HEADER_H) // _ITEM_H
                if 0 <= idx < len(self.candidates):
                    self.checked[idx] = not self.checked[idx]

    def _bottom_action(self, key):
        if key == "close":
            self.deactivate()
        elif key == "all":
            for i in range(len(self.checked)):
                self.checked[i] = True
        elif key == "unall":
            for i in range(len(self.checked)):
                self.checked[i] = False
        elif key == "ok":
            picked = [p for p, c in zip(self.candidates, self.checked) if c]
            cb = self.on_ok
            self.deactivate()
            if cb and picked:
                cb(picked)

    # ── 绘制 ──

    def draw(self, surface):
        if not self.active:
            return
        dark = self.dark_mode
        r = self.bg_rect
        if dark:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
            self.draw_title_bar(surface, r, "打开文件", SETTINGS_TEXT_LIGHT)
        else:
            self.draw_background(surface, r)
            self.draw_title_bar(surface, r, "打开文件", (20, 40, 80))
        tcol = _light(dark)
        # 路径 + 上一级
        surface.blit(rt(f_s, "路径", tcol), (self.x0, self.y0 + 6))
        if self._path_field:
            self._path_field.draw(surface)
        up = self._up_rect()
        pygame.draw.rect(surface, _bg(dark), up, border_radius=5)
        ts = rt(f_s, "↑ 上一级", tcol)
        surface.blit(ts, (up.centerx - ts.get_width() // 2, up.centery - ts.get_height() // 2))
        # 范围
        rg = self._range_rect()
        pygame.draw.rect(surface, _bg(dark), rg, border_radius=5)
        lab = "寻找范围: " + ("所有子目录" if self.recursive else "当前目录")
        ts = rt(f_s, lab, tcol)
        surface.blit(ts, (rg.x + 8, rg.centery - ts.get_height() // 2))
        # 文件树 + 文件列表
        self._draw_tree(surface, dark)
        self._draw_files(surface, dark)
        # 底部
        for key, br in self._bottom_btns().items():
            color = {"ok": (70, 150, 110), "close": (150, 90, 90),
                     "all": (80, 130, 170), "unall": (120, 100, 100)}[key]
            pygame.draw.rect(surface, color, br, border_radius=5)
            lab = {"ok": "确定", "close": "关闭", "all": "全选", "unall": "全不选"}[key]
            ts = rt(f_s, lab, (255, 255, 255))
            surface.blit(ts, (br.centerx - ts.get_width() // 2, br.centery - ts.get_height() // 2))
        if self._msg and time.time() - self._msg_ts < 8:
            ts = rt(f_s, self._msg, (240, 210, 120) if dark else (150, 100, 30))
            surface.blit(ts, (self.x0 + 320, self.bg_rect.bottom - 68))

    def _draw_panel_frame(self, surface, rect, dark, title):
        pygame.draw.rect(surface, _bg(dark, 2), rect, border_radius=6)
        pygame.draw.rect(surface, _border(dark), rect, 1, border_radius=6)
        ts = rt(f_s, title, _light(dark))
        surface.blit(ts, (rect.x + 8, rect.y + 6))

    def _draw_tree(self, surface, dark):
        tr = self._tree_rect()
        self._draw_panel_frame(surface, tr, dark, "文件夹")
        tcol = _light(dark)
        y = tr.y + _HEADER_H
        from math import ceil
        for i in range(self._tree_scroll, min(len(self.tree), self._tree_scroll + tr.height // _ITEM_H)):
            yy = y + (i - self._tree_scroll) * _ITEM_H
            if yy + _ITEM_H > tr.bottom:
                break
            node = self.tree[i]
            indent = node.get('depth', 0) * 14
            name = node['name']
            active = os.path.normpath(node['path']) == os.path.normpath(self.cur_dir)
            if active:
                pygame.draw.rect(surface, (60, 96, 140) if dark else (190, 215, 240),
                                 pygame.Rect(tr.x, yy, tr.width, _ITEM_H - 2), border_radius=4)
            ts = rt(f_s, _ellipsize(name, f_s, tr.width - 8 - indent - 6),
                    (240, 240, 245) if active else tcol)
            surface.blit(ts, (tr.x + 8 + indent, yy + 4))
        # 高对比: 文件树内文字颜色与背景对比足够

    def _file_columns(self):
        """文件列表的四列宽度（**保证加起来正好等于可用宽**）。

        P9(K2 子项): 表头原来用 `60 + 0.42w + 0.42w + 0.12w`，在 w=804 时合计
        830 > 804 ⇒ 「尺寸」表头被画到面板**外面** 34px；行内文字用的是另一套
        (`fr.x+40` + `0.42w`)，表头与行**不同源**。现在统一由本方法给出四列宽，
        表头与行都用它，且总和严格 = 面板内宽。
        """
        fr = self._files_rect()
        inner = max(120, fr.width - 16)
        c_sel = 40
        c_name = int((inner - c_sel) * 0.46)
        c_dir = int((inner - c_sel) * 0.42)
        c_size = inner - c_sel - c_name - c_dir
        return c_sel, c_name, c_dir, max(48, c_size)

    def _draw_files(self, surface, dark):
        fr = self._files_rect()
        self._draw_panel_frame(surface, fr, dark, "文件")
        tcol = _light(dark)
        c_sel, c_name, c_dir, c_size = self._file_columns()
        x0 = fr.x + 8
        # 表头（与行同源列宽；P9: 原表头合计超宽 34px）
        hx = x0
        for lab, w0 in (("选择", c_sel), ("文件名", c_name),
                        ("所在路径", c_dir), ("尺寸", c_size)):
            ts = rt(f_s, _ellipsize(lab, f_s, w0 - 4), _light(dark))
            surface.blit(ts, (hx, fr.y + 6))
            hx += w0
        y = fr.y + _HEADER_H
        for i in range(self._file_scroll, min(len(self.candidates), self._file_scroll + fr.height // _ITEM_H)):
            yy = y + (i - self._file_scroll) * _ITEM_H
            if yy + _ITEM_H > fr.bottom:
                break
            row = pygame.Rect(fr.x, yy, fr.width, _ITEM_H - 2)
            if self.checked[i]:
                pygame.draw.rect(surface, (50, 85, 120) if dark else (185, 215, 240), row, border_radius=4)
            p = self.candidates[i]
            fname = os.path.basename(p)
            fdir = os.path.dirname(p)
            try:
                size = f"{os.path.getsize(p) / 1024:.0f} KB"
            except OSError:
                size = "-"
            # 勾选框放大
            box = pygame.Rect(fr.x + 8, yy + 5, 20, 20)
            pygame.draw.rect(surface, (70, 78, 92) if dark else (200, 210, 228), box, border_radius=4)
            if self.checked[i]:
                pygame.draw.line(surface, (110, 230, 150), (box.x + 3, box.centery),
                                 (box.x + box.w // 2, box.bottom - 3), 3)
                pygame.draw.line(surface, (110, 230, 150), (box.x + box.w // 2, box.bottom - 3),
                                 (box.right - 2, box.y + 4), 3)
            surface.blit(rt(f_s, _ellipsize(fname, f_s, c_name - 8), tcol), (x0 + c_sel, yy + 4))
            ds = rt(f_s, _ellipsize(fdir, f_s, c_dir - 8),
                    (170, 175, 190) if dark else (90, 110, 140))
            surface.blit(ds, (x0 + c_sel + c_name, yy + 4))
            ss = rt(f_s, size, (170, 175, 190) if dark else (90, 110, 140))
            surface.blit(ss, (x0 + c_sel + c_name + c_dir + c_size - ss.get_width() - 2, yy + 4))
