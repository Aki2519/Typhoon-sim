# py/statistics/intensity_chart.py
"""台风详情面板：强度折线图 + ACE 累计折线图（正常/编辑模式按 K 唤起）。"""
from __future__ import annotations
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict

from ..constants import (
    f_s, f_m, f_l, rt, TXT, BUTTON_BORDER, BUTTON_BG, DIALOG_TITLE_BAR_HEIGHT,
    DB, TD, TS, C1, C2, C3, C4, C5_L, C5_D, C2_MINUS, C3_MINUS, C4_ST, WV,
)
from ..dialog_base import DraggableDialog
from .draw_helpers_chart import DASH_COLOR, draw_dashed_h, draw_dashed_v

# ── 固定深蓝色（模式 2） ──
DARK_BLUE = (20, 60, 140)

# ── ACE 曲线颜色 ──
ACE_CURVE_COLOR = (220, 130, 30)      # 橙金色
ACE_CURVE_LINE_W = 2

# ── 强度阈值 + 颜色映射 ──
_THRESHOLDS: List[Tuple[int, Tuple[int, int, int]]] = [
    (29,  TD),
    (34,  TS),
    (64,  C1),
    (83,  C2_MINUS),
    (86,  C2),
    (96,  C3_MINUS),
    (105, C3),
    (113, C4),
    (130, C4_ST),
    (137, C5_L),
    (155, C5_L),
    (170, C5_D),
]

# 填充区域：(y_lower, y_upper, color)
_FILL_BANDS: List[Tuple[float, float, Tuple[int, int, int]]] = [
    (0,   29,  DB),
    (29,  34,  TD),
    (34,  64,  TS),
    (64,  83,  C1),
    (83,  86,  C2_MINUS),
    (86,  96,  C2),
    (96,  105, C3_MINUS),
    (105, 113, C3),
    (113, 130, C4),
    (130, 137, C4_ST),
    (137, 155, C5_L),
    (155, 170, (190, 96, 230)),
    (170, 999, C5_D),
]

_CHART_COLORS = {
    'BG': (255, 255, 255, 235),
    'BORDER': TXT,
    'GRID_Y': (150, 150, 170, 140),
    'GRID_X': DASH_COLOR,
}


class IntensityChartDialog(DraggableDialog):
    """台风详情面板：强度折线图 + ACE累计折线图。"""

    def __init__(self, sim):
        super().__init__(sim)
        self._typhoon = None
        self._cached_chart: Optional[pygame.Surface] = None
        self._cached_key = None
        self._cached_rects: List[Tuple[pygame.Rect, str]] = []
        self._cached_points: List[Tuple[int, int]] = []
        self._cached_ace_rects: List[Tuple[pygame.Rect, str]] = []
        self._cached_tick_labels_y: List[Tuple[pygame.Surface, int, int]] = []
        self._cached_tick_labels_x: List[Tuple[pygame.Surface, int, int]] = []

        self._color_mode = 0  # 0=分色, 1=深蓝
        self._color_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._show_ace = True  # 是否显示ACE累计曲线
        self._ace_btn_rect = pygame.Rect(0, 0, 0, 0)

        self._margin_l = 75
        self._margin_r = 70
        self._margin_t = 50
        self._margin_b = 95

    # ═══════════════════════════════════════════════
    #  激活 / 关闭
    # ═══════════════════════════════════════════════

    def activate(self):
        super().activate()
        self.dragging = False

        md = self.sim.md
        if md == self.sim.MODE_EDIT:
            self._typhoon = self.sim.edit_typhoon
        elif md == self.sim.MODE_NORMAL:
            self._typhoon = self.sim.current_typhoon()
        else:
            self._typhoon = None

        if self._typhoon is None or not self._typhoon.pts:
            self.deactivate()
            return

        self._build()
        self._center()

    def deactivate(self):
        super().deactivate()
        self.dragging = False
        self._typhoon = None

    # ═══════════════════════════════════════════════
    #  构建
    # ═══════════════════════════════════════════════

    def _center(self):
        w, h = self.bg_rect.width, self.bg_rect.height
        self.bg_rect.x = max(0, (self.sim.screen_width - w) // 2)
        self.bg_rect.y = max(0, (self.sim.screen_height - h) // 2)

    def _point_color(self, w: int, st: str) -> Tuple[int, int, int]:
        """根据当前颜色模式返回点的颜色。"""
        if self._color_mode == 1:
            return DARK_BLUE
        return self.sim.get_point_color(w, st)

    def _build(self):
        ty = self._typhoon
        pts = ty.pts
        n = len(pts)

        # 保存旧位置，_build 可能在事件处理中被调用来重建缓存，
        # 此时 bg_rect 应保持用户拖拽后的位置不变
        old_x, old_y = self.bg_rect.x, self.bg_rect.y

        w = min(1500, self.sim.screen_width - 40)
        h = min(780, self.sim.screen_height - 80)
        self.bg_rect = pygame.Rect(old_x if old_x > 0 else 0,
                                   old_y if old_y > 0 else 0, w, h)

        ch_w = w - self._margin_l - self._margin_r
        ch_h = h - self._margin_t - self._margin_b
        chart_left = self._margin_l
        chart_top = self._margin_t

        # ── 时间轴 ──
        pts_dt: List[datetime] = []
        for p in pts:
            t = p['t']
            try:
                pts_dt.append(datetime.strptime(t[:10], "%Y%m%d%H"))
            except (ValueError, IndexError):
                pts_dt.append(datetime(2000, 1, 1, 0))

        t_min = pts_dt[0]
        t_max = pts_dt[-1]
        t_span = (t_max - t_min).total_seconds()
        if t_span <= 0:
            t_span = 3600

        # ── 风速轴 ──
        max_wind = max(p['w'] for p in pts)
        max_wind = max(max_wind, 40)
        y_max = ((max_wind // 20) + 1) * 20 + 20
        y_min = 0

        # ── ACE 累计轴（p['ace'] 已是累计值，直接使用）──
        ace_cum_list: List[float] = [p.get('ace', 0.0) for p in pts]
        max_ace = ace_cum_list[-1] if ace_cum_list else 1.0
        ace_max = max(max_ace * 1.1, 1.0)

        # ── 预渲染 Surface ──
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, _CHART_COLORS['BG'], (0, 0, w, h), 0, 10)
        pygame.draw.rect(surf, _CHART_COLORS['BORDER'], (0, 0, w, h), 2, 10)

        # 标题
        name = self.sim.get_display_name(ty)
        title = rt(f_l, f"{name} — 详情", TXT)
        surf.blit(title, ((w - title.get_width()) // 2, 12))

        # ── 颜色模式切换按钮 ──
        mode_label = "分色" if self._color_mode == 0 else "深蓝"
        btn_text = rt(f_s, f"配色: {mode_label}", (255, 255, 255))
        btn_pad = 10
        btn_w = btn_text.get_width() + btn_pad * 2
        btn_h = btn_text.get_height() + 6
        btn_x = w - self._margin_r - btn_w
        btn_y = 10
        self._color_btn_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
        pygame.draw.rect(surf, BUTTON_BG, self._color_btn_rect, 0, 4)
        pygame.draw.rect(surf, BUTTON_BORDER, self._color_btn_rect, 1, 4)
        surf.blit(btn_text, (btn_x + btn_pad, btn_y + 3))

        # ── ACE 曲线开关按钮 ──
        ace_label = "ACE: 开" if self._show_ace else "ACE: 关"
        ace_btn_text = rt(f_s, ace_label, (255, 255, 255))
        ace_btn_w = ace_btn_text.get_width() + btn_pad * 2
        ace_btn_x = btn_x - ace_btn_w - 8
        self._ace_btn_rect = pygame.Rect(ace_btn_x, btn_y, ace_btn_w, btn_h)
        pygame.draw.rect(surf, BUTTON_BG, self._ace_btn_rect, 0, 4)
        pygame.draw.rect(surf, BUTTON_BORDER, self._ace_btn_rect, 1, 4)
        surf.blit(ace_btn_text, (ace_btn_x + btn_pad, btn_y + 3))

        # ── 填充区域 ──
        for y_lower, y_upper, color in _FILL_BANDS:
            if y_lower >= y_max:
                continue
            y_upper_clamped = min(y_upper, y_max)
            if y_upper_clamped <= y_lower:
                continue
            rel_top = (y_upper_clamped - y_min) / (y_max - y_min)
            rel_bottom = (y_lower - y_min) / (y_max - y_min)
            fy = chart_top + ch_h - int(rel_top * ch_h)
            fh = max(1, int((rel_top - rel_bottom) * ch_h))
            alpha_color = (color[0], color[1], color[2], 65)
            fill_surf = pygame.Surface((ch_w, fh), pygame.SRCALPHA)
            fill_surf.fill(alpha_color)
            surf.blit(fill_surf, (chart_left, fy))

        # ── Y 轴虚线 + 标签（风速，左侧） ──
        self._cached_tick_labels_y.clear()
        for yt in range(0, int(y_max) + 1, 20):
            rel = (yt - y_min) / (y_max - y_min)
            y_px = chart_top + ch_h - int(rel * ch_h)
            draw_dashed_h(surf, chart_left, chart_left + ch_w, y_px, _CHART_COLORS['GRID_Y'])
            lbl = rt(f_s, f"{yt}", TXT)
            surf.blit(lbl, (chart_left - lbl.get_width() - 8, y_px - lbl.get_height() // 2))
            self._cached_tick_labels_y.append((lbl, chart_left - lbl.get_width() - 8, y_px - lbl.get_height() // 2))

        # ── 右 Y 轴 ACE 标签（自适应步长，至少显示0和上限）──
        if self._show_ace:
            if ace_max <= 1.0:
                ace_step = 0.2
            elif ace_max <= 5.0:
                ace_step = 1.0
            elif ace_max <= 25.0:
                ace_step = 5.0
            elif ace_max <= 50.0:
                ace_step = 10.0
            else:
                ace_step = 20.0
            ace_val = 0.0
            while ace_val <= ace_max + ace_step * 0.01:
                rel = ace_val / ace_max if ace_max > 0 else 0
                y_px = chart_top + ch_h - int(rel * ch_h)
                fmt = f"{ace_val:.1f}" if ace_step < 1.0 else f"{ace_val:.0f}"
                ace_lbl = rt(f_s, fmt, ACE_CURVE_COLOR)
                surf.blit(ace_lbl, (chart_left + ch_w + 6, y_px - ace_lbl.get_height() // 2))
                ace_val += ace_step
            # ACE 轴标签
            ace_axis_label = rt(f_s, "ACE", ACE_CURVE_COLOR)
            surf.blit(ace_axis_label, (chart_left + ch_w + 6, chart_top - 18))

        # ── 加粗阈值实线 ──
        for yt, color in _THRESHOLDS:
            if yt > y_max:
                continue
            rel = (yt - y_min) / (y_max - y_min)
            y_px = chart_top + ch_h - int(rel * ch_h)
            pygame.draw.line(surf, color, (chart_left, y_px), (chart_left + ch_w, y_px), 3)

        # ── X 轴：00Z 虚线 + 标签 ──
        self._cached_tick_labels_x.clear()
        d0 = t_min.replace(hour=0, minute=0, second=0, microsecond=0)
        if d0 < t_min:
            d0 += timedelta(days=1)
        cursor = d0
        while cursor <= t_max:
            rel = (cursor - t_min).total_seconds() / t_span
            x_px = chart_left + int(rel * ch_w)
            draw_dashed_v(surf, x_px, chart_top, chart_top + ch_h, _CHART_COLORS['GRID_X'])
            lbl = rt(f_s, f"{cursor.month}/{cursor.day}", TXT)
            surf.blit(lbl, (x_px - lbl.get_width() // 2, chart_top + ch_h + 5))
            self._cached_tick_labels_x.append((lbl, x_px - lbl.get_width() // 2, chart_top + ch_h + 5))
            cursor += timedelta(days=1)

        # 起始/结束时间
        start_lbl = rt(f_s, t_min.strftime("%m/%d %HZ"), TXT)
        surf.blit(start_lbl, (chart_left, chart_top + ch_h + 22))
        end_lbl = rt(f_s, t_max.strftime("%m/%d %HZ"), TXT)
        surf.blit(end_lbl, (chart_left + ch_w - end_lbl.get_width(), chart_top + ch_h + 22))

        # ── 强度点 + 线段 ──
        point_px: List[Tuple[int, int]] = []
        for i, pt in enumerate(pts):
            rel_t = (pts_dt[i] - t_min).total_seconds() / t_span
            rel_w = (pt['w'] - y_min) / (y_max - y_min)
            x_px = chart_left + int(rel_t * ch_w)
            y_px = chart_top + ch_h - int(rel_w * ch_h)
            point_px.append((x_px, y_px))

        self._cached_points = point_px
        self._cached_rects.clear()

        # 线段（与点颜色一致；插值算法仅用于常规强度类型）
        _INTERP_NATURES = {'DB', 'LO', 'WV', 'TD', 'TY', 'ST', 'HU'}
        for i in range(len(point_px) - 1):
            x1, y1 = point_px[i]
            x2, y2 = point_px[i + 1]
            w1 = pts[i]['w']
            w2 = pts[i + 1]['w']
            st_i = pts[i]['st']

            if self._color_mode == 1:
                # 深蓝模式：整段统一颜色
                pygame.draw.line(surf, DARK_BLUE, (x1, y1), (x2, y2), 3)
            elif st_i not in _INTERP_NATURES or w1 == w2:
                color = pts[i].get('color',
                                    self.sim.get_point_color(w1, pts[i]['st']))
                pygame.draw.line(surf, color, (x1, y1), (x2, y2), 3)
            else:
                lo, hi = (w1, w2) if w1 < w2 else (w2, w1)
                crossing = [yt for yt, _ in _THRESHOLDS if lo < yt < hi]
                crossing.sort()
                if w1 > w2:
                    crossing.reverse()

                segs = [(float(x1), float(y1), float(w1))]
                for yt in crossing:
                    ratio = (yt - w1) / (w2 - w1)
                    xt = x1 + ratio * (x2 - x1)
                    yt_px = y1 + ratio * (y2 - y1)
                    segs.append((xt, yt_px, float(yt)))
                segs.append((float(x2), float(y2), float(w2)))

                for j in range(len(segs) - 1):
                    sx, sy, sw = segs[j]
                    ex, ey, ew = segs[j + 1]
                    mid_w = int(round((sw + ew) / 2))
                    st = self.sim.get_strength_category(mid_w, '')
                    color = self.sim.get_point_color(mid_w, st)
                    pygame.draw.line(surf, color,
                                     (int(sx), int(sy)), (int(ex), int(ey)), 3)

        # 强度点
        for i, (x_px, y_px) in enumerate(point_px):
            color = self._point_color(pts[i]['w'], pts[i]['st'])
            pygame.draw.circle(surf, color, (x_px, y_px), 5)
            r = pygame.Rect(x_px - 6, y_px - 6, 12, 12)
            info = (
                f"{pts_dt[i].strftime('%m/%d %HZ')}  "
                f"{pts[i]['w']}kt  {pts[i]['st']}  {pts[i]['p']}hPa"
                f"  ACE={pts[i].get('ace', 0):.2f}"
            )
            self._cached_rects.append((r, info))

        # ── ACE 累计曲线 ──
        self._cached_ace_rects.clear()
        if self._show_ace and ace_cum_list:
            ace_points: List[Tuple[int, int]] = []
            for i, ace_val in enumerate(ace_cum_list):
                rel_t = (pts_dt[i] - t_min).total_seconds() / t_span
                rel_a = ace_val / ace_max
                x_px = chart_left + int(rel_t * ch_w)
                y_px = chart_top + ch_h - int(rel_a * ch_h)
                ace_points.append((x_px, y_px))

            # ACE 曲线线段
            if len(ace_points) > 1:
                pygame.draw.lines(surf, ACE_CURVE_COLOR, False, ace_points, ACE_CURVE_LINE_W)

            # ACE 点
            for i, (x_px, y_px) in enumerate(ace_points):
                pygame.draw.circle(surf, ACE_CURVE_COLOR, (x_px, y_px), 4)
                r = pygame.Rect(x_px - 5, y_px - 5, 10, 10)
                ace_info = f"累计 ACE: {ace_cum_list[i]:.4f}  ({pts_dt[i].strftime('%m/%d %HZ')})"
                self._cached_ace_rects.append((r, ace_info))

        # ── 图例 ──
        legend_y = chart_top + ch_h + 45
        legend_x = chart_left
        for yt, color in _THRESHOLDS:
            pygame.draw.line(surf, color, (legend_x, legend_y + 5), (legend_x + 18, legend_y + 5), 2)
            lbl = rt(f_s, f"{yt}", TXT)
            surf.blit(lbl, (legend_x + 22, legend_y - 2))
            legend_x += 55

        # ACE 图例
        ace_lgd_x = legend_x + 10
        pygame.draw.line(surf, ACE_CURVE_COLOR, (ace_lgd_x, legend_y + 5), (ace_lgd_x + 18, legend_y + 5), 2)
        ace_lgd_lbl = rt(f_s, "ACE累计", TXT)
        surf.blit(ace_lgd_lbl, (ace_lgd_x + 22, legend_y - 2))

        self._cached_chart = surf

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════

    def draw(self, surface: pygame.Surface):
        if not self.active or self._typhoon is None:
            return
        if self._cached_chart is None:
            self._build()

        surface.blit(self._cached_chart, (self.bg_rect.x, self.bg_rect.y))

        # ── 悬停信息 ──
        mx, my = pygame.mouse.get_pos()
        offset_x, offset_y = self.bg_rect.x, self.bg_rect.y

        # 优先 ACE 曲线悬停（点更小，更容易被强度点覆盖）
        for r_local, info in self._cached_ace_rects:
            r_global = r_local.move(offset_x, offset_y)
            if r_global.collidepoint(mx, my):
                self._draw_hover_tip(surface, info, mx, my)
                return

        for r_local, info in self._cached_rects:
            r_global = r_local.move(offset_x, offset_y)
            if r_global.collidepoint(mx, my):
                self._draw_hover_tip(surface, info, mx, my)
                break

    def _draw_hover_tip(self, surface, info, mx, my):
        tip = rt(f_s, info, TXT)
        tw, th = tip.get_width() + 10, tip.get_height() + 8
        tx, ty = mx + 15, my - 25
        if ty < 0:
            ty = my + 15
        if tx + tw > self.sim.screen_width:
            tx = self.sim.screen_width - tw - 5
        tb = pygame.Surface((tw, th), pygame.SRCALPHA)
        tb.fill((255, 255, 255, 210))
        pygame.draw.rect(tb, BUTTON_BORDER, (0, 0, tw, th), 1)
        tb.blit(tip, (5, 4))
        surface.blit(tb, (tx, ty))

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        # ── 颜色按钮（必须在 handle_drag_event 之前检查，
        #     因为按钮在标题栏区域内，否则会被拖拽抢先捕获）──
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            btn_global = self._color_btn_rect.move(self.bg_rect.x, self.bg_rect.y)
            if btn_global.collidepoint(e.pos):
                self._color_mode = 1 - self._color_mode
                self._build()          # _build 内已保留 bg_rect 位置
                return True
            ace_btn_global = self._ace_btn_rect.move(self.bg_rect.x, self.bg_rect.y)
            if ace_btn_global.collidepoint(e.pos):
                self._show_ace = not self._show_ace
                self._build()
                return True

        if self.handle_drag_event(e):
            return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_c:
                self._color_mode = 1 - self._color_mode
                self._build()
                return True
            if e.key == pygame.K_a:
                self._show_ace = not self._show_ace
                self._build()
                return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # 点击在对话框内但未被其他控件处理 → 拦截（防止穿透）
            if self.bg_rect.collidepoint(e.pos):
                return True
            # 点击在对话框外 → 关闭
            self.deactivate()
            return True

        # 对话框外的鼠标事件不拦截（让下层对话框/地图有机会处理）
        if e.type in (pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
            return False

        return False
