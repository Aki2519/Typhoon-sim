# py/statistics/intensity_comparison.py
"""多台风强度对比对话框：叠加多条强度曲线，x轴共用"距生成时间"。"""
from __future__ import annotations
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from ..constants import (
    f_s, f_m, f_l, rt, TXT, BUTTON_BORDER, BUTTON_BG, DIALOG_TITLE_BAR_HEIGHT,
    DB, TD, TS, C1, C2, C3, C4, C5_L, C5_D, C2_MINUS, C3_MINUS, C4_ST, WV,
)
from ..dialog_base import DraggableDialog

# ── 12 种可区分颜色 ──
COLORS = [
    (220, 60, 60), (60, 160, 60), (60, 60, 220),
    (220, 160, 0), (160, 60, 160), (60, 180, 180),
    (220, 100, 50), (100, 200, 100), (100, 100, 255),
    (200, 200, 50), (200, 50, 200), (50, 200, 200),
]

# ── 强度阈值 ──
_THRESHOLDS: List[Tuple[int, Tuple[int, int, int]]] = [
    (34,  TS),
    (64,  C1),
    (83,  C2_MINUS),
    (86,  C2),
    (96,  C3_MINUS),
    (105, C3),
    (113, C4),
    (130, C4_ST),
    (137, C5_L),
]

_CHART_BG = (255, 255, 255, 235)
_CHART_BORDER = TXT
_GRID_COLOR = (150, 150, 170, 100)


class IntensityComparisonDialog(DraggableDialog):
    """多台风强度对比（无ACE曲线）。x轴 = 距生成的小时数，所有台风共用。"""

    def __init__(self, sim):
        super().__init__(sim)
        self._year: int = 0
        self._tys: list = []
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._hover_rects: List[Tuple[pygame.Rect, str]] = []
        # 整个对话框可拖动（标题栏高度设为极大，handle_event 中已先检查关闭按钮）
        self.title_bar_height = 100000

        self._margin_l = 75
        self._margin_r = 50
        self._margin_t = 50
        self._margin_b = 95

    # ═══════════════════════════════════════════════
    #  激活
    # ═══════════════════════════════════════════════
    def activate(self, year: int = 0, tys: list = None):
        super().activate()
        self._year = year
        self._tys = list(tys) if tys else []
        self._cached_surf = None
        self._hover_rects = []
        if not self._tys:
            self.deactivate()
            return
        self._build()
        self._center()

    def deactivate(self):
        super().deactivate()
        self._cached_surf = None
        self._hover_rects = []

    # ═══════════════════════════════════════════════
    #  构建
    # ═══════════════════════════════════════════════
    def _center(self):
        w, h = self.bg_rect.width, self.bg_rect.height
        self.bg_rect.x = max(0, (self.sim.screen_width - w) // 2)
        self.bg_rect.y = max(0, (self.sim.screen_height - h) // 2)

    def _build(self):
        w = min(1700, self.sim.screen_width - 20)
        h = min(820, self.sim.screen_height - 60)
        ox, oy = self.bg_rect.x, self.bg_rect.y
        self.bg_rect = pygame.Rect(ox if ox > 0 else 0, oy if oy > 0 else 0, w, h)

        ch_w = w - self._margin_l - self._margin_r
        ch_h = h - self._margin_t - self._margin_b
        chart_left = self._margin_l
        chart_top = self._margin_t

        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, _CHART_BG, (0, 0, w, h), 0, 10)
        pygame.draw.rect(surf, _CHART_BORDER, (0, 0, w, h), 2, 10)

        # 标题
        title = rt(f_l, f"强度对比 — {self._year}", TXT)
        surf.blit(title, ((w - title.get_width()) // 2, 12))

        # ── 收集全局 Y 范围 ──
        engine = self.sim.ace_engine
        max_wind = 40
        for ty in self._tys:
            pts = [p for p in ty.pts
                   if p.get('ace_year', 0) == self._year
                   and engine.point_in_limit(p['la'], p['lo'])]
            if pts:
                max_wind = max(max_wind, max(p['w'] for p in pts))
        y_max = ((max_wind // 20) + 1) * 20 + 20

        # ── 强度填充带 ──
        fill_bands = [
            (0, 29, DB), (29, 34, TD), (34, 64, TS), (64, 83, C1),
            (83, 86, C2_MINUS), (86, 96, C2), (96, 105, C3_MINUS), (105, 113, C3),
            (113, 130, C4), (130, 137, C4_ST),
            (137, 155, C5_L), (155, 170, (190, 96, 230)), (170, 999, C5_D),
        ]
        for y_lower, y_upper, color in fill_bands:
            if y_lower >= y_max:
                continue
            y_upper_clamped = min(y_upper, y_max)
            if y_upper_clamped <= y_lower:
                continue
            rel_top = (y_upper_clamped - 0) / (y_max - 0)
            rel_bottom = (y_lower - 0) / (y_max - 0)
            fy = chart_top + ch_h - int(rel_top * ch_h)
            fh = max(1, int((rel_top - rel_bottom) * ch_h))
            alpha_color = (color[0], color[1], color[2], 65)
            fill_surf = pygame.Surface((ch_w, fh), pygame.SRCALPHA)
            fill_surf.fill(alpha_color)
            surf.blit(fill_surf, (chart_left, fy))

        # ── Y 轴虚线 + 标签 ──
        for yt in range(0, int(y_max) + 1, 20):
            rel = (yt - 0) / (y_max - 0)
            y_px = chart_top + ch_h - int(rel * ch_h)
            for dx in range(0, ch_w, 8):
                x1 = chart_left + dx
                x2 = min(chart_left + dx + 4, chart_left + ch_w)
                pygame.draw.line(surf, _GRID_COLOR, (x1, y_px), (x2, y_px), 1)
            lbl = rt(f_s, f"{yt}", TXT)
            surf.blit(lbl, (chart_left - lbl.get_width() - 8, y_px - lbl.get_height() // 2))

        # ── 阈值实线 ──
        for yt, color in _THRESHOLDS:
            if yt > y_max:
                continue
            rel = (yt - 0) / (y_max - 0)
            y_px = chart_top + ch_h - int(rel * ch_h)
            pygame.draw.line(surf, color, (chart_left, y_px), (chart_left + ch_w, y_px), 2)

        # ── 各台风曲线 + 全局时间轴计算 ──
        self._hover_rects = []
        global_max_hours = 1.0  # 所有台风中最长的持续时间

        # 第一遍：计算每个台风的数据，找到全局最大小时数
        typhoon_data = []
        for ty in self._tys:
            pts = [p for p in ty.pts
                   if p.get('ace_year', 0) == self._year
                   and engine.point_in_limit(p['la'], p['lo'])]
            if len(pts) < 1:
                continue

            pts_dt: List[datetime] = []
            for p in pts:
                t = p['t']
                try:
                    pts_dt.append(datetime.strptime(t[:10], "%Y%m%d%H"))
                except (ValueError, IndexError):
                    pts_dt.append(datetime(2000, 1, 1, 0))

            t0 = pts_dt[0]
            hours = [(dt - t0).total_seconds() / 3600.0 for dt in pts_dt]
            if hours:
                global_max_hours = max(global_max_hours, hours[-1])

            typhoon_data.append((ty, pts, pts_dt, hours, t0))

        # x 轴上限取整到最近的 24 小时
        global_max_hours = ((int(global_max_hours) // 24) + 1) * 24 + 24
        x_label = "距生成时间"

        # ── X 轴虚线 + 标签（每 24 小时标注）──
        y_bottom = chart_top + ch_h
        for xh in range(0, int(global_max_hours) + 1, 24):
            rel = xh / global_max_hours
            x_px = chart_left + int(rel * ch_w)
            for dy in range(0, ch_h, 8):
                y1 = chart_top + dy
                y2 = min(chart_top + dy + 4, chart_top + ch_h)
                pygame.draw.line(surf, (180, 190, 200, 120), (x_px, y1), (x_px, y2), 1)
            lbl = rt(f_s, f"{xh}h", TXT)
            surf.blit(lbl, (x_px - lbl.get_width() // 2, y_bottom + 5))

        # X 轴标签
        x_axis_lbl = rt(f_s, x_label, TXT)
        surf.blit(x_axis_lbl, (chart_left + ch_w // 2 - x_axis_lbl.get_width() // 2, y_bottom + 24))

        # ── 第二遍：绘制曲线 ──
        for t_idx, (ty, pts, pts_dt, hours, t0) in enumerate(typhoon_data):
            color = COLORS[t_idx % len(COLORS)]
            name = self.sim.get_display_name(ty)

            point_px = []
            for i, pt in enumerate(pts):
                rel_t = hours[i] / global_max_hours
                rel_w = (pt['w'] - 0) / (y_max - 0)
                x_px = chart_left + int(rel_t * ch_w)
                y_px = chart_top + ch_h - int(rel_w * ch_h)
                point_px.append((x_px, y_px))

            # 强度线段
            if len(point_px) > 1:
                pygame.draw.lines(surf, color, False, point_px, 3)
            # 强度点
            for i, (x_px, y_px) in enumerate(point_px):
                pygame.draw.circle(surf, color, (x_px, y_px), 4)
                r = pygame.Rect(x_px - 5, y_px - 5, 10, 10)
                info = (f"{name}  {pts_dt[i].strftime('%m/%d %HZ')}  "
                        f"{pts[i]['w']}kt  {pts[i]['st']}  "
                        f"+{hours[i]:.0f}h  "
                        f"ACE={pts[i].get('ace', 0):.2f}")
                self._hover_rects.append((r, info))

            # 名称标签（标在曲线起点旁）
            if point_px:
                name_surf = rt(f_s, name, color)
                surf.blit(name_surf, (point_px[0][0] + 6, point_px[0][1] - 10))

        # ── 图例 ──
        legend_y = chart_top + ch_h + 60
        legend_x = chart_left
        for t_idx, (ty, _, _, _, _) in enumerate(typhoon_data):
            color = COLORS[t_idx % len(COLORS)]
            name = self.sim.get_display_name(ty)
            pygame.draw.line(surf, color, (legend_x, legend_y + 5), (legend_x + 20, legend_y + 5), 3)
            lbl = rt(f_s, name, TXT)
            surf.blit(lbl, (legend_x + 24, legend_y - 1))
            legend_x += lbl.get_width() + 40
            if legend_x > chart_left + ch_w - 100:
                legend_y += 18
                legend_x = chart_left

        self._cached_surf = surf

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════
    def draw(self, surface: pygame.Surface):
        if not self.active or self._cached_surf is None:
            return
        surface.blit(self._cached_surf, (self.bg_rect.x, self.bg_rect.y))

        # 关闭按钮
        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # 悬停提示
        mx, my = pygame.mouse.get_pos()
        ox, oy = self.bg_rect.x, self.bg_rect.y
        for r_local, info in self._hover_rects:
            r_global = r_local.move(ox, oy)
            if r_global.collidepoint(mx, my):
                tip = rt(f_s, info, TXT)
                tw, th = tip.get_width() + 10, tip.get_height() + 8
                tx, ty_ = mx + 15, my - 25
                if ty_ < 0:
                    ty_ = my + 15
                if tx + tw > self.sim.screen_width:
                    tx = self.sim.screen_width - tw - 5
                tb = pygame.Surface((tw, th), pygame.SRCALPHA)
                tb.fill((255, 255, 255, 210))
                pygame.draw.rect(tb, BUTTON_BORDER, (0, 0, tw, th), 1)
                tb.blit(tip, (5, 4))
                surface.blit(tb, (tx, ty_))
                break

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # 关闭按钮（优先于拖拽，防止被拖拽抢先）
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
            # 点击对话框外 → 关闭
            if not self.bg_rect.collidepoint(e.pos):
                self.deactivate()
                return True

        # 拖拽（整个对话框可拖）
        if self.handle_drag_event(e):
            return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True

        # 不拦截鼠标移动/滚轮（让底层可以处理）
        if e.type in (pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
            return False
        return False
