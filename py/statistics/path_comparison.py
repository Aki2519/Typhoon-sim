# py/statistics/path_comparison.py
"""多台风路径对比对话框。右侧选择栏，复选框管理台风可见性。含强度对比图。"""
from __future__ import annotations
import pygame
from typing import List, Optional, Tuple, Set

from ..constants import (f_s, f_m, rt, TXT, BUTTON_BORDER,
                          DIALOG_TITLE_BAR_HEIGHT, SETTINGS_TEXT_LIGHT)
from ..dialog_base import DraggableDialog
from .shared import COLORS
from .chart_helpers import build_basin_order, render_map_subset, draw_vscrollbar


# ── 侧栏常量 ──
SIDEBAR_WIDTH = 240
SIDEBAR_GAP = 12
CHECKBOX_SIZE = 14
ROW_HEIGHT = 22
SIDEBAR_PAD_TOP = 50
SIDEBAR_PAD_BOTTOM = 20


class PathComparisonDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._tys: list = []
        self._year: int = 0
        self._selected: Set[int] = set()          # 选中台风在 _tys 中的索引
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)

        # 侧栏交互状态
        self._sidebar_scroll = 0
        self._sidebar_rows_visible = 0
        self._sidebar_rect = pygame.Rect(0, 0, 0, 0)
        self._sidebar_content_rect = pygame.Rect(0, 0, 0, 0)
        self._checkbox_rects: List[pygame.Rect] = []
        self._select_all_btn = pygame.Rect(0, 0, 0, 0)
        self._deselect_all_btn = pygame.Rect(0, 0, 0, 0)
        self._intensity_cmp_btn = pygame.Rect(0, 0, 0, 0)
        # 框选状态
        self._box_selecting = False
        self._box_start = (0, 0)
        self._box_current = (0, 0)
        # 绘制缓存
        self._name_surfs: List[pygame.Surface] = []
        self._title_cache: Optional[tuple] = None
        self._sidebar_bg_cache: Optional[tuple] = None
        self._box_surf: Optional[pygame.Surface] = None
        self._close_text = rt(f_s, "关闭", (255, 255, 255))
        self._select_all_text = rt(f_s, "全选", (255, 255, 255))
        self._deselect_all_text = rt(f_s, "全不选", (255, 255, 255))
        self._intensity_cmp_text = rt(f_s, "强度对比", (255, 255, 255))

    def activate(self):
        super().activate()
        self._build()
        self._sidebar_scroll = 0

    def _build(self):
        year = self.sim.current_ace_year
        engine = self.sim.ace_engine
        self._year = year
        # 按台风列表排序收集台风
        basin_order = self._build_basin_order()

        all_tys = []
        for ty in self.sim.tys:
            if any(p.get('ace_year', 0) == year
                   and engine.point_in_limit(p['la'], p['lo'])
                   for p in ty.pts):
                all_tys.append(ty)

        # 排序：复刻台风列表
        def _sort_key(ty):
            basin_idx = basin_order.get(ty.basin, 9999)
            first_time = ty.pts[0]['t'] if ty.pts else "99999999"
            return (basin_idx, first_time, self.sim.get_display_name(ty).lower())

        all_tys.sort(key=_sort_key)
        self._tys = all_tys
        self._selected = set(range(len(self._tys)))  # 默认全选
        self._name_surfs = [
            rt(f_s, self.sim.get_display_name(ty), COLORS[i % len(COLORS)])
            for i, ty in enumerate(self._tys)
        ]

        # 初始化布局（尽可能利用屏幕空间）
        w = min(1800, max(1200, self.sim.screen_width - 40))
        h = min(1100, max(800, self.sim.screen_height - 60))
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_surf = None
        self._checkbox_rects = []

    def _build_basin_order(self) -> dict:
        return build_basin_order(self.sim)

    def _render(self):
        if self._cached_surf is not None:
            return

        # ── Phase 1: 计算地理范围 ──
        engine = self.sim.ace_engine
        all_lons, all_lats = [], []
        for idx, ty in enumerate(self._tys):
            if idx not in self._selected:
                continue
            for p in ty.pts:
                if p.get('ace_year', 0) == self._year and engine.point_in_limit(p['la'], p['lo']):
                    all_lons.append(p['lo'])
                    all_lats.append(p['la'])
        if not all_lons:
            w, h = self.bg_rect.width, self.bg_rect.height
            self._cached_surf = pygame.Surface((w, h), pygame.SRCALPHA)
            return

        margin = 5.0
        mlon, Mlon = min(all_lons) - margin, max(all_lons) + margin
        mlat, Mlat = min(all_lats) - margin, max(all_lats) + margin
        if Mlon - mlon < 2:
            Mlon = mlon + 2
        if Mlat - mlat < 2:
            Mlat = mlat + 2

        # ── Phase 2: 动态调整高度 ──
        box_x, box_y = 60, 50                     # 数据区左上角（相对 bg_rect）
        box_w = self.bg_rect.width - box_x - SIDEBAR_WIDTH - SIDEBAR_GAP - 20
        geo_ratio = (Mlon - mlon) / max(0.1, Mlat - mlat)
        box_h = int(box_w / geo_ratio)
        box_h = max(100, min(2000, box_h))

        new_h = box_h + box_y + 80
        if new_h != self.bg_rect.height:
            self.bg_rect.height = new_h
            self.bg_rect.centery = self.sim.screen_height // 2

        w = self.bg_rect.width
        surf = pygame.Surface((w, new_h), pygame.SRCALPHA)

        # ── 底图 ──
        render_map_subset(surf, self.sim, mlon, Mlon, mlat, Mlat,
                          box_x, box_y, box_w, box_h)

        # ── Phase 3: 坐标映射与路径绘制 ──
        def geo_to_local(lon, lat):
            x = box_x + (lon - mlon) / (Mlon - mlon) * box_w
            y = box_y + (Mlat - lat) / (Mlat - mlat) * box_h
            return int(x), int(y)

        # 洋区边框
        rect_color = (100, 180, 255)
        lx1, ly1 = geo_to_local(mlon, Mlat)
        lx2, ly2 = geo_to_local(Mlon, mlat)
        pygame.draw.rect(surf, rect_color,
                         (lx1, ly1, lx2 - lx1, ly2 - ly1), 2)

        for idx, ty in enumerate(self._tys):
            if idx not in self._selected:
                continue
            pts = [p for p in ty.pts
                   if p.get('ace_year', 0) == self._year
                   and engine.point_in_limit(p['la'], p['lo'])]
            if len(pts) < 1:
                continue
            screen_pts = [geo_to_local(p['lo'], p['la']) for p in pts]
            # 渐变线段模式：每段用终点颜色
            for i in range(1, len(screen_pts)):
                x1, y1 = screen_pts[i - 1]
                x2, y2 = screen_pts[i]
                c = pts[i].get('color', COLORS[idx % len(COLORS)])
                pygame.draw.line(surf, c, (x1, y1), (x2, y2), 3)
            if screen_pts:
                name = self.sim.get_display_name(ty)
                ns = rt(f_s, name, pts[-1].get('color', COLORS[idx % len(COLORS)]))
                surf.blit(ns, (screen_pts[0][0] + 5, screen_pts[0][1] - 8))

        self._cached_surf = surf

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dark = self.dark_mode
        if dark:
            self.draw_dark_panel(surface, self.bg_rect)
        else:
            self.draw_background(surface, self.bg_rect)
        self._render()
        if self._cached_surf:
            surface.blit(self._cached_surf, self.bg_rect.topleft)

        box_x, box_y = self.bg_rect.x, self.bg_rect.y
        box_w = self.bg_rect.width
        box_h = self.bg_rect.height

        # 标题（缓存）
        title_key = (self._year, dark)
        if self._title_cache is None or self._title_cache[0] != title_key:
            tc = SETTINGS_TEXT_LIGHT if dark else TXT
            self._title_cache = (title_key, rt(f_m, f"路径对比 — {self._year}", tc))
        surface.blit(self._title_cache[1], (box_x + 12, box_y + 8))

        # 关闭按钮
        cb = pygame.Rect(box_x + box_w - 90, box_y + 8, 55, 25)
        self._close_btn_rect = cb
        if dark:
            self.draw_dark_button(surface, cb, "关闭")
        else:
            self.draw_button(surface, cb, self._close_text)

        # ══ 右侧选择栏 ══
        self._draw_sidebar(surface, box_x, box_y, box_w, box_h)

        # ══ 框选矩形 ══
        if self._box_selecting:
            rx = min(self._box_start[0], self._box_current[0])
            ry = min(self._box_start[1], self._box_current[1])
            rw = abs(self._box_current[0] - self._box_start[0])
            rh = abs(self._box_current[1] - self._box_start[1])
            if rw > 0 and rh > 0:
                if self._box_surf is None or self._box_surf.get_size() != (rw, rh):
                    self._box_surf = pygame.Surface((rw, rh), pygame.SRCALPHA)
                    self._box_surf.fill((100, 180, 255, 60))
                    pygame.draw.rect(self._box_surf, (100, 180, 255), (0, 0, rw, rh), 1)
                surface.blit(self._box_surf, (rx, ry))

    def _draw_sidebar(self, surface, box_x, box_y, box_w, box_h):
        """绘制右侧选择栏"""
        dark = self.dark_mode

        sx = box_x + box_w - SIDEBAR_WIDTH - 10
        sy = box_y + 40
        sh = box_h - 80
        self._sidebar_rect = pygame.Rect(sx, sy, SIDEBAR_WIDTH, sh)

        # 侧栏背景（缓存）
        bg_key = (sh, dark)
        if self._sidebar_bg_cache is None or self._sidebar_bg_cache[0] != bg_key:
            sidebar_bg = pygame.Surface((SIDEBAR_WIDTH, sh), pygame.SRCALPHA)
            if dark:
                sidebar_bg.fill((35, 40, 52, 220))
                pygame.draw.rect(sidebar_bg, (70, 80, 100), (0, 0, SIDEBAR_WIDTH, sh), 1, 6)
            else:
                sidebar_bg.fill((230, 235, 245, 180))
                pygame.draw.rect(sidebar_bg, BUTTON_BORDER, (0, 0, SIDEBAR_WIDTH, sh), 1, 6)
            self._sidebar_bg_cache = (bg_key, sidebar_bg)
        surface.blit(self._sidebar_bg_cache[1], (sx, sy))

        # 内容区域
        content_x = sx + 8
        content_w = SIDEBAR_WIDTH - 16
        content_top = sy + 8

        # 全选 / 全不选 按钮
        btn_w, btn_h = 55, 22
        self._select_all_btn = pygame.Rect(content_x, content_top, btn_w, btn_h)
        self._deselect_all_btn = pygame.Rect(content_x + btn_w + 8, content_top, btn_w, btn_h)
        if dark:
            self.draw_dark_button(surface, self._select_all_btn, "全选",
                                  hover=len(self._selected) == len(self._tys))
            self.draw_dark_button(surface, self._deselect_all_btn, "全不选",
                                  hover=len(self._selected) == 0)
        else:
            self.draw_button(surface, self._select_all_btn, self._select_all_text,
                             style='primary' if len(self._selected) == len(self._tys) else 'light')
            self.draw_button(surface, self._deselect_all_btn, self._deselect_all_text,
                             style='primary' if len(self._selected) == 0 else 'light')

        # 复选框列表
        list_top = content_top + btn_h + 10
        # 留出底部按钮空间
        list_h = sh - (list_top - sy) - 38
        visible_rows = max(1, list_h // ROW_HEIGHT)
        self._sidebar_rows_visible = visible_rows
        self._sidebar_content_rect = pygame.Rect(content_x, list_top, content_w, list_h)

        total = len(self._tys)
        max_scroll = max(0, total - visible_rows)
        self._sidebar_scroll = max(0, min(self._sidebar_scroll, max_scroll))

        cb_unsel_bg = (55, 60, 75) if dark else (180, 190, 200)
        cb_unsel_border = (90, 100, 120) if dark else BUTTON_BORDER

        self._checkbox_rects = []
        for i in range(self._sidebar_scroll, min(self._sidebar_scroll + visible_rows, total)):
            row_y = list_top + (i - self._sidebar_scroll) * ROW_HEIGHT

            # 复选框
            cb_rect = pygame.Rect(content_x, row_y + 3, CHECKBOX_SIZE, CHECKBOX_SIZE)
            self._checkbox_rects.append((i, cb_rect))
            # 绘制复选框
            if i in self._selected:
                pygame.draw.rect(surface, (100, 180, 100), cb_rect, 0, 2)
                # 勾号
                check_color = (255, 255, 255)
                pygame.draw.line(surface, check_color,
                                 (cb_rect.x + 2, cb_rect.centery),
                                 (cb_rect.centerx, cb_rect.bottom - 2), 2)
                pygame.draw.line(surface, check_color,
                                 (cb_rect.centerx, cb_rect.bottom - 2),
                                 (cb_rect.right - 1, cb_rect.top + 3), 2)
            else:
                pygame.draw.rect(surface, cb_unsel_bg, cb_rect, 0, 2)
                pygame.draw.rect(surface, cb_unsel_border, cb_rect, 1, 2)

            # 名称（用台风对应颜色，预渲染缓存）
            if i < len(self._name_surfs):
                surface.blit(self._name_surfs[i], (cb_rect.right + 6, row_y + 1))

        # 侧栏滚动条
        draw_vscrollbar(surface, content_x + content_w - 8, list_top, list_h,
                        total, visible_rows, self._sidebar_scroll,
                        dark=dark, show_track=True)

        # ── 强度对比按钮 ──
        cmp_btn_y = sy + sh - 30
        self._intensity_cmp_btn = pygame.Rect(content_x, cmp_btn_y, content_w, 24)
        if dark:
            self.draw_dark_button(surface, self._intensity_cmp_btn, "强度对比",
                                  hover=len(self._selected) >= 1)
        else:
            self.draw_button(surface, self._intensity_cmp_btn,
                             self._intensity_cmp_text,
                             style='primary' if len(self._selected) >= 1 else 'light')

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # 关闭按钮
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True

            # 全选 / 全不选
            if self._select_all_btn.collidepoint(e.pos):
                self._selected = set(range(len(self._tys)))
                self._cached_surf = None
                return True
            if self._deselect_all_btn.collidepoint(e.pos):
                self._selected.clear()
                self._cached_surf = None
                return True

            # 强度对比
            if self._intensity_cmp_btn.collidepoint(e.pos) and len(self._selected) >= 1:
                sel_tys = [self._tys[i] for i in sorted(self._selected)]
                dlg = self.sim.dialog_mgr.intensity_comparison
                dlg.activate(self._year, sel_tys)
                return True

            # 复选框点击
            for idx, cb_rect in self._checkbox_rects:
                if cb_rect.collidepoint(e.pos):
                    if idx in self._selected:
                        self._selected.discard(idx)
                    else:
                        self._selected.add(idx)
                    self._cached_surf = None
                    return True

            # 开始在侧栏内容区框选
            if self._sidebar_content_rect.collidepoint(e.pos):
                # 检查是否点击在空白区域（非复选框非按钮）
                hit_checkbox = any(cb.collidepoint(e.pos) for _, cb in self._checkbox_rects)
                if not hit_checkbox:
                    self._box_selecting = True
                    self._box_start = e.pos
                    self._box_current = e.pos
                    return True

        # 框选移动
        if e.type == pygame.MOUSEMOTION and self._box_selecting:
            self._box_current = e.pos
            return True

        # 框选结束
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self._box_selecting:
            self._box_selecting = False
            self._apply_box_select()
            return True

        # 侧栏滚轮
        if e.type == pygame.MOUSEWHEEL:
            if self._sidebar_rect.collidepoint(pygame.mouse.get_pos()):
                total = len(self._tys)
                self._sidebar_scroll = max(0, min(
                    self._sidebar_scroll - e.y,
                    max(0, total - self._sidebar_rows_visible)))
                return True

        # 拖拽
        if self.handle_drag_event(e):
            return True

        # ESC
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True

        return False

    def _apply_box_select(self):
        """根据框选矩形切换台风选中状态"""
        if self._sidebar_content_rect is None or self._sidebar_content_rect.width <= 0:
            return
        rx = min(self._box_start[0], self._box_current[0])
        ry = min(self._box_start[1], self._box_current[1])
        rw = abs(self._box_current[0] - self._box_start[0])
        rh = abs(self._box_current[1] - self._box_start[1])
        box_rect = pygame.Rect(rx, ry, rw, rh)

        # 仅当框选有实际面积时
        if rw < 4 and rh < 4:
            return

        changed = False
        ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL
        for idx, cb_rect in self._checkbox_rects:
            if box_rect.colliderect(cb_rect):
                if ctrl:
                    if idx not in self._selected:
                        self._selected.add(idx)
                        changed = True
                elif idx in self._selected:
                    self._selected.discard(idx)
                    changed = True
                else:
                    self._selected.add(idx)
                    changed = True

        if changed:
            self._cached_surf = None

    def deactivate(self):
        super().deactivate()
        self._cached_surf = None
        self._box_selecting = False
        self._checkbox_rects = []
