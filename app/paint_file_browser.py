# py/paint_file_browser.py
"""Ty4 风格的「打开文件」浏览器对话框。

界面与 Typhoon4 的「打开文件」一致:
  - 顶部路径栏(可手动输入/跳转)
  - 快捷入口: 跳转到 .. / 运行目录 / 桌面 / 文档 / 收藏夹
  - 寻找范围: 当前目录 / 所有子目录
  - 文件列表: 文件名 + 所在目录 + 类型 + 尺寸, 可多选(勾选)
  - 底部: 已找到 N 个文件 · 全选 / 全不选 / 确定 / 关闭
用于绘画模式导入台风文件(*.dat/*.txt)与选择地图文件(*.png/*.jpg/...)。

完成后把所选文件绝对路径列表交给 on_ok 回调。
"""
from __future__ import annotations

import os
import time
import pygame
from typing import Callable, List, Optional

from .constants import f_s, f_m, rt, SETTINGS_TEXT_LIGHT, DIALOG_TITLE_BAR_HEIGHT
from .dialog_base import DraggableDialog
from .input_field import InputField

# 每种用途允许的扩展名
EXT_MAP = {
    "typhoon": (".dat", ".txt", ".bde", ".bdeck"),
    "map": (".png", ".jpg", ".jpeg", ".jpe", ".bmp"),
}

_FONT = f_s
_ITEM_H = 26
_HEADER_H = 28


class FileBrowserDialog(DraggableDialog):
    def __init__(self, sim, purpose="typhoon"):
        super().__init__(sim)
        self.title = rt(f_m, "打开文件", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self.purpose = purpose
        self.exts = EXT_MAP.get(purpose, EXT_MAP["typhoon"])

        self.cur_dir: str = ""
        self.recursive: bool = False       # 寻找范围: 当前目录 / 所有子目录
        self.candidates: List[str] = []     # 找到的可选文件
        self.checked: List[bool] = []
        self._scroll = 0
        self._dir_scroll = 0
        self._dirs: List[str] = []          # 子目录
        self._dir_checked: List[bool] = []
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
            self.cur_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "typhoon"))
            if not os.path.isdir(self.cur_dir):
                self.cur_dir = os.path.expanduser("~")
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
        w = min(980, sw - 60)
        h = min(720, sh - 120)
        self.bg_rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        self.x0 = self.bg_rect.x + 16
        self.y0 = self.bg_rect.y + self.title_bar_height + 10
        pf = InputField(pygame.Rect(self.x0 + 70, self.y0 + 4, self.bg_rect.width - 250, 24),
                        max_length=400, dark=self.dark_mode)
        pf.set_text(self.cur_dir)
        self._path_field = pf

    # ── 刷新目录 ──

    def _refresh(self, keep_checked=None):
        self.cur_dir = os.path.abspath(self.cur_dir)
        try:
            entries = sorted(os.listdir(self.cur_dir))
        except OSError as e:
            self._msg = f"无法打开目录: {e}"
            self._msg_ts = time.time()
            return
        dirs = []
        for e in entries:
            p = os.path.join(self.cur_dir, e)
            if os.path.isdir(p) and not e.startswith('.'):
                dirs.append(e)
        self._dirs = dirs
        self._dir_checked = [False] * len(dirs)

        # 收集文件候选
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
        self._scroll = 0
        self._dir_scroll = 0
        if self._path_field:
            self._path_field.set_text(self.cur_dir)
        self._msg = f"已找到 {len(files)} 个文件。"
        self._msg_ts = time.time()

    # ── 几何 ──

    def _path_rect(self):
        return pygame.Rect(self.x0, self.y0 + 2, 76, 26)

    def _go_rect(self):
        return pygame.Rect(self.x0 + 76, self.y0 + 2, 58, 26)

    def _quick_y(self):
        return self.y0 + 44

    def _dirs_rect(self):
        x = self.x0
        y = self._quick_y() + 40
        return pygame.Rect(x, y, 210, self.bg_rect.bottom - 80 - y)

    def _files_rect(self):
        x = self.x0 + 226
        y = self._quick_y() + 40
        w = self.bg_rect.width - 68 - 226
        return pygame.Rect(x, y, w, self.bg_rect.bottom - 80 - y)

    def _bottom_btns(self):
        y = self.bg_rect.bottom - 52
        x = self.bg_rect.right - 16
        out = {}
        for key, lab, w0, color in (("ok", "确定", 76, (70, 150, 110)),
                                    ("close", "关闭", 76, (150, 90, 90)),
                                    ("unall", "全不选", 76, (120, 100, 100)),
                                    ("all", "全选", 76, (80, 130, 170))):
            x -= w0
            out[key] = pygame.Rect(x, y, w0, 30)
            x -= 10
        return out

    def _quick_rect(self, i):
        w = (self.bg_rect.width - 116) // 5
        return pygame.Rect(self.x0 + 54 + i * (w + 6), self._quick_y(), w - 6, 26)

    def _toggle_rect(self):
        return pygame.Rect(self.x0 + 228, self.y0 + 4, 46, 26)  # unused, 范围放 quick 行右侧

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
            if e.button in (4, 5):
                dr = -(1 if e.button == 4 else -1)
                self._scroll = max(0, self._scroll + dr)
                self._dir_scroll = max(0, self._dir_scroll + dr)
                return True
            if e.button == 1:
                self._click(e.pos)
            return True
        return False

    def _click(self, pos):
        x, y = pos
        # 底部按钮
        for key, r in self._bottom_btns().items():
            if r.collidepoint(x, y):
                self._bottom_action(key)
                return
        # 快捷路径
        for i, q in enumerate(("运行目录", "桌面", "文档", "收藏夹", "上一级")):
            if self._quick_rect(i).collidepoint(x, y):
                self._quick_action(q)
                return
        # 寻找范围: 当前目录 / 所有子目录
        fr = pygame.Rect(self.x0 + 54, self._quick_y(), 250, 26)
        if fr.collidepoint(x, y):
            self.recursive = not self.recursive
            self._refresh()
            return
        # 子目录列表
        drect = self._dirs_rect()
        if drect.collidepoint(x, y) and x < drect.right:
            if x >= drect.right:
                return
            idx = self._dir_scroll + (y - drect.y) // _ITEM_H
            if 0 <= idx < len(self._dirs):
                p = os.path.join(self.cur_dir, self._dirs[idx])
                if y < drect.y + _ITEM_H:
                    self.cur_dir = p
                    self._refresh()
            return
        # 文件列表
        frect = self._files_rect()
        if frect.collidepoint(x, y):
            idx = self._scroll + (y - frect.y) // _ITEM_H
            if 0 <= idx < len(self.candidates):
                self.checked[idx] = not self.checked[idx]
            return
        # 双击目录进入
        if x < self._dirs_rect().right and y > self._dirs_rect().y:
            idx = self._dir_scroll + (y - self._dirs_rect().y) // _ITEM_H
            if 0 <= idx < len(self._dirs) and os.path.isdir(os.path.join(self.cur_dir, self._dirs[idx])):
                self.cur_dir = os.path.join(self.cur_dir, self._dirs[idx])
                self._refresh()

    def _quick_action(self, q):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if q == "运行目录":
            tgt = os.path.join(base, "typhoon")
            self.cur_dir = tgt if os.path.isdir(tgt) else base
        elif q == "桌面":
            self.cur_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        elif q == "文档":
            self.cur_dir = os.path.join(os.path.expanduser("~"), "Documents")
        elif q == "收藏夹":
            self.cur_dir = os.path.join(os.path.expanduser("~"), "Favorites")
        elif q == "上一级":
            self.cur_dir = os.path.dirname(self.cur_dir)
        if not os.path.isdir(self.cur_dir):
            self.cur_dir = os.path.expanduser("~")
        self._refresh()

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

        tcol = SETTINGS_TEXT_LIGHT if dark else (20, 40, 80)
        lbl = rt(f_s, "路径", tcol)
        surface.blit(lbl, (self.x0, self.y0 + 6))
        if self._path_field:
            self._path_field.draw(surface)
        # 快捷入口
        for i, (q, lab) in enumerate((("运行目录", "运行目录"), ("桌面", "桌面"), ("文档", "文档"),
                                      ("收藏夹", "收藏夹"), ("上一级", "上一级"))):
            qr = self._quick_rect(i)
            pygame.draw.rect(surface, (55, 62, 78) if dark else (210, 220, 238), qr, border_radius=5)
            ts = rt(f_s, q, tcol)
            surface.blit(ts, (qr.centerx - ts.get_width() // 2,
                              qr.centery - ts.get_height() // 2))
        # 范围
        fr = pygame.Rect(self.x0 + 54, self._quick_y(), 250, 26)
        lab = "寻找范围: " + ("所有子目录" if self.recursive else "当前目录")
        pygame.draw.rect(surface, (55, 62, 78) if dark else (210, 220, 238), fr, border_radius=5)
        ts = rt(f_s, lab, tcol)
        surface.blit(ts, (fr.x + 8, fr.centery - ts.get_height() // 2))
        # 子目录
        self._draw_dir_panel(surface, dark)
        # 文件列表
        self._draw_file_panel(surface, dark)
        # 底部按钮
        for key, br in self._bottom_btns().items():
            color = {"ok": (70, 150, 110), "close": (150, 90, 90),
                     "all": (80, 130, 170), "unall": (120, 100, 100)}[key]
            pygame.draw.rect(surface, color, br, border_radius=5)
            lab = {"ok": "确定", "close": "关闭", "all": "全选", "unall": "全不选"}[key]
            ts = rt(f_s, lab, (255, 255, 255))
            surface.blit(ts, (br.centerx - ts.get_width() // 2,
                              br.centery - ts.get_height() // 2))
        if self._msg and time.time() - self._msg_ts < 8:
            ts = rt(f_s, self._msg, (240, 210, 120) if dark else (150, 100, 30))
            surface.blit(ts, (self.x0 + 250, self.bg_rect.bottom - 42))

    def _panel_frame(self, surface, rect, dark, title):
        pygame.draw.rect(surface, (40, 46, 60) if dark else (235, 242, 250), rect, border_radius=6)
        pygame.draw.rect(surface, (80, 92, 115) if dark else (170, 185, 205), rect, 1, border_radius=6)
        ts = rt(f_s, title, SETTINGS_TEXT_LIGHT if dark else (40, 70, 120))
        surface.blit(ts, (rect.x + 6, rect.y + 6))

    def _draw_dir_panel(self, surface, dark):
        r = self._dirs_rect()
        self._panel_frame(surface, r, dark, "文件夹")
        tcol = SETTINGS_TEXT_LIGHT if dark else (20, 40, 80)
        y = r.y + _HEADER_H
        for i in range(self._dir_scroll, min(len(self._dirs), self._dir_scroll + r.height // _ITEM_H)):
            yy = y + (i - self._dir_scroll) * _ITEM_H
            if yy + _ITEM_H > r.bottom:
                break
            name = self._dirs[i]
            ts = rt(f_s, "📁 " + name, tcol)
            surface.blit(ts, (r.x + 8, yy + 4))
            if pygame.Rect(r.x, yy, r.width, _ITEM_H).collidepoint(pygame.mouse.get_pos()):
                pygame.draw.rect(surface, (60, 90, 130) if dark else (190, 215, 240),
                                 pygame.Rect(r.x, yy, r.width, _ITEM_H - 1), border_radius=4)

    def _draw_file_panel(self, surface, dark):
        r = self._files_rect()
        self._panel_frame(surface, r, dark, "文件")
        tcol = SETTINGS_TEXT_LIGHT if dark else (20, 40, 80)
        y = r.y + _HEADER_H
        for i in range(self._scroll, min(len(self.candidates), self._scroll + r.height // _ITEM_H)):
            yy = y + (i - self._scroll) * _ITEM_H
            if yy + _ITEM_H > r.bottom:
                break
            row = pygame.Rect(r.x, yy, r.width, _ITEM_H - 1)
            if self.checked[i]:
                pygame.draw.rect(surface, (50, 85, 120) if dark else (185, 215, 240), row, border_radius=4)
            mark = "☑ " if self.checked[i] else "☐ "
            path = self.candidates[i]
            fname = os.path.basename(path)
            fdir = os.path.dirname(path)
            try:
                fsize = os.path.getsize(path)
                size_s = f"{fsize/1024:.0f} KB"
            except OSError:
                size_s = ""
            ts = rt(f_s, mark + fname, tcol)
            surface.blit(ts, (r.x + 6, yy + 4))
            ds = rt(f_s, fdir, (170, 175, 190) if dark else (90, 110, 140))
            surface.blit(ds, (r.x + r.width // 2, yy + 4))
            ss = rt(f_s, size_s, (170, 175, 190) if dark else (90, 110, 140))
            surface.blit(ss, (r.right - ss.get_width() - 10, yy + 4))
