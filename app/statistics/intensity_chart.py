# py/statistics/intensity_chart.py
"""台风详情面板：强度折线图 + ACE 累计折线图（正常/编辑模式按 K 唤起）。"""
from __future__ import annotations
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from ..constants import (
    f_s, f_l, rt, TXT, BUTTON_BORDER, BUTTON_BG,
    SETTINGS_DARK_BG, SETTINGS_TEXT_LIGHT,
)
from ..dialog_base import DraggableDialog
from .chart_helpers import (DASH_COLOR, draw_dashed_h, draw_dashed_v,
                            draw_fill_bands, draw_tooltip, draw_vscrollbar,
                            _haversine_chain)
from .shared import _THRESHOLDS, _FILL_BANDS

# ── 固定深蓝色（模式 2） ──
DARK_BLUE = (20, 60, 140)

# ── ACE 曲线颜色 ──
ACE_CURVE_COLOR = (220, 130, 30)      # 橙金色
ACE_CURVE_LINE_W = 2

_CHART_COLORS = {
    'BG': (255, 255, 255, 235),
    'BORDER': TXT,
    'GRID_Y': (150, 150, 170, 140),
    'GRID_X': DASH_COLOR,
}

_CHART_COLORS_DARK = {
    'BG': SETTINGS_DARK_BG,
    'BORDER': (208, 216, 234),
    'GRID_Y': (255, 255, 255, 60),
    'GRID_X': (255, 255, 255, 55),
}


class IntensityChartDialog(DraggableDialog):
    """台风详情面板：强度折线图 + ACE累计折线图。"""

    def __init__(self, sim):
        super().__init__(sim)
        self._typhoon = None
        self._cached_chart: Optional[pygame.Surface] = None
        self._cached_rects: List[Tuple[pygame.Rect, str]] = []
        self._cached_points: List[Tuple[int, int]] = []
        self._cached_ace_rects: List[Tuple[pygame.Rect, str]] = []

        self._color_mode = 0  # 0=分色, 1=深蓝
        self._color_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._show_ace = True  # 是否显示ACE累计曲线
        self._ace_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._scroll_y: int = 0
        self._content_h: int = 0

        self._margin_l = 75
        self._margin_r = 70
        self._margin_t = 50
        self._margin_b = 95

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
        count = len(pts)

        dark = self.dark_mode
        self._built_dark = dark
        C = _CHART_COLORS_DARK if dark else _CHART_COLORS
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        btn_bg = (50, 55, 70) if dark else BUTTON_BG
        btn_border = (80, 110, 160) if dark else BUTTON_BORDER

        # 保存旧位置，_build 可能在事件处理中被调用来重建缓存，
        # 此时 bg_rect 应保持用户拖拽后的位置不变
        old_x, old_y = self.bg_rect.x, self.bg_rect.y

        w = min(1500, self.sim.screen_width - 40)
        content_h = min(780, self.sim.screen_height - 100)
        self._content_h = 780  # 固有内容高度
        window_h = min(self._content_h, content_h)
        # 保留用户拖拽后的位置(含部分拖出屏幕的负坐标),避免重建时跳回左上角
        self.bg_rect = pygame.Rect(old_x, old_y, w, window_h)
        max_scroll = max(0, self._content_h - window_h)
        self._scroll_y = max(0, min(self._scroll_y, max_scroll))

        ch_w = w - self._margin_l - self._margin_r
        ch_h = self._content_h - self._margin_t - self._margin_b
        chart_left = self._margin_l
        chart_top = self._margin_t

        # ── 时间轴 ──
        pts_dt: List[datetime] = []
        for p in pts:
            t = p['t']
            try:
                pts_dt.append(datetime.strptime(t[:10], "%Y%m%d%H"))
            except (ValueError, IndexError, TypeError):
                # K32: 失败点沿用前点时间,避免回退 2000 使 t_span 暴增挤压图表
                pts_dt.append(pts_dt[-1] if pts_dt else datetime(2000, 1, 1, 0))

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

        # ── 预渲染 Surface（使用内容高度而非窗口高度）──
        ch = self._content_h
        surf = pygame.Surface((w, ch), pygame.SRCALPHA)
        pygame.draw.rect(surf, C['BG'], (0, 0, w, ch), 0, 10)
        pygame.draw.rect(surf, C['BORDER'], (0, 0, w, ch), 2, 10)

        # 标题
        name = self.sim.get_display_name(ty)
        title = rt(f_l, f"{name} — 详情", tc)
        surf.blit(title, ((w - title.get_width()) // 2, 12))

        # ── 路径总长度（整条路径，不分年、不做 TS 过滤，F2）──
        path_km = 0.0
        if len(pts) >= 2:
            st = None
            for i in range(1, len(pts)):
                seg, st = _haversine_chain(pts[i]['la'], pts[i]['lo'],
                                           pts[i - 1]['la'], pts[i - 1]['lo'], st)
                path_km += seg
        km_label = rt(f_s, f"路径长度: {path_km:.0f} km", tc)
        surf.blit(km_label, ((w - km_label.get_width()) // 2, 36))

        # ── 颜色模式切换按钮 ──
        mode_label = "分色" if self._color_mode == 0 else "深蓝"
        btn_text = rt(f_s, f"配色: {mode_label}", (255, 255, 255))
        btn_pad = 10
        btn_w = btn_text.get_width() + btn_pad * 2
        btn_h = btn_text.get_height() + 6
        btn_x = w - self._margin_r - btn_w
        btn_y = 10
        self._color_btn_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
        pygame.draw.rect(surf, btn_bg, self._color_btn_rect, 0, 4)
        pygame.draw.rect(surf, btn_border, self._color_btn_rect, 1, 4)
        surf.blit(btn_text, (btn_x + btn_pad, btn_y + 3))

        # ── ACE 曲线开关按钮 ──
        ace_label = "ACE: 开" if self._show_ace else "ACE: 关"
        ace_btn_text = rt(f_s, ace_label, (255, 255, 255))
        ace_btn_w = ace_btn_text.get_width() + btn_pad * 2
        ace_btn_x = btn_x - ace_btn_w - 8
        self._ace_btn_rect = pygame.Rect(ace_btn_x, btn_y, ace_btn_w, btn_h)
        pygame.draw.rect(surf, btn_bg, self._ace_btn_rect, 0, 4)
        pygame.draw.rect(surf, btn_border, self._ace_btn_rect, 1, 4)
        surf.blit(ace_btn_text, (ace_btn_x + btn_pad, btn_y + 3))

        # ── 填充区域 ──
        draw_fill_bands(surf, _FILL_BANDS, chart_left, chart_top, ch_w, ch_h,
                        y_max, y_min, alpha=80 if dark else 65)

        # ── Y 轴虚线 + 标签（风速，左侧） ──
        for yt in range(0, int(y_max) + 1, 20):
            rel = (yt - y_min) / (y_max - y_min)
            y_px = chart_top + ch_h - int(rel * ch_h)
            draw_dashed_h(surf, chart_left, chart_left + ch_w, y_px, C['GRID_Y'])
            lbl = rt(f_s, f"{yt}", tc)
            surf.blit(lbl, (chart_left - lbl.get_width() - 8, y_px - lbl.get_height() // 2))

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
                rel = ace_val / ace_max
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
        d0 = t_min.replace(hour=0, minute=0, second=0, microsecond=0)
        if d0 < t_min:
            d0 += timedelta(days=1)
        cursor = d0
        while cursor <= t_max:
            rel = (cursor - t_min).total_seconds() / t_span
            x_px = chart_left + int(rel * ch_w)
            draw_dashed_v(surf, x_px, chart_top, chart_top + ch_h, C['GRID_X'])
            lbl = rt(f_s, f"{cursor.month}/{cursor.day}", tc)
            surf.blit(lbl, (x_px - lbl.get_width() // 2, chart_top + ch_h + 5))
            cursor += timedelta(days=1)

        # 起始/结束时间
        start_lbl = rt(f_s, t_min.strftime("%m/%d %HZ"), tc)
        surf.blit(start_lbl, (chart_left, chart_top + ch_h + 22))
        end_lbl = rt(f_s, t_max.strftime("%m/%d %HZ"), tc)
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
            rect = pygame.Rect(x_px - 6, y_px - 6, 12, 12)
            info = (
                f"{pts_dt[i].strftime('%m/%d %HZ')}  "
                f"{pts[i]['w']}kt  {pts[i]['st']}  {pts[i]['p']}hPa"
                f"  ACE={pts[i].get('ace', 0.0):.4f}"
            )
            self._cached_rects.append((rect, info))

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
                rect = pygame.Rect(x_px - 5, y_px - 5, 10, 10)
                ace_info = f"累计 ACE: {ace_cum_list[i]:.4f}  ({pts_dt[i].strftime('%m/%d %HZ')})"
                self._cached_ace_rects.append((rect, ace_info))

        # ── 图例 ──
        legend_y = chart_top + ch_h + 45
        legend_x = chart_left
        for yt, color in _THRESHOLDS:
            pygame.draw.line(surf, color, (legend_x, legend_y + 5), (legend_x + 18, legend_y + 5), 2)
            lbl = rt(f_s, f"{yt}", tc)
            surf.blit(lbl, (legend_x + 22, legend_y - 2))
            legend_x += 55

        # ACE 图例
        ace_lgd_x = legend_x + 10
        pygame.draw.line(surf, ACE_CURVE_COLOR, (ace_lgd_x, legend_y + 5), (ace_lgd_x + 18, legend_y + 5), 2)
        ace_lgd_lbl = rt(f_s, "ACE累计", tc)
        surf.blit(ace_lgd_lbl, (ace_lgd_x + 22, legend_y - 2))

        self._cached_chart = surf

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════

    def draw(self, surface: pygame.Surface):
        if not self.active or self._typhoon is None:
            return
        if self._cached_chart is None or getattr(self, '_built_dark', None) != self.dark_mode:
            self._build()

        box_x, box_y = self.bg_rect.x, self.bg_rect.y
        box_w, box_h = self.bg_rect.width, self.bg_rect.height
        # 裁剪并滚动
        src_rect = pygame.Rect(0, self._scroll_y, box_w, box_h)
        surface.blit(self._cached_chart, (box_x, box_y), area=src_rect)

        # 滚动条
        draw_vscrollbar(surface, box_x + box_w - 8, box_y, box_h,
                        self._content_h, box_h, self._scroll_y, dark=self.dark_mode)

        # ── 悬停信息 ──
        mouse_x, mouse_y = pygame.mouse.get_pos()
        hover_rect = pygame.Rect(box_x, box_y, box_w, box_h)
        if not hover_rect.collidepoint(mouse_x, mouse_y):
            return
        offset_x, offset_y = box_x, box_y - self._scroll_y

        # 优先 ACE 曲线悬停（点更小，更容易被强度点覆盖）
        for rect_local, info in self._cached_ace_rects:
            r_global = rect_local.move(offset_x, offset_y)
            if r_global.collidepoint(mouse_x, mouse_y):
                self._draw_hover_tip(surface, info, mouse_x, mouse_y)
                return

        for rect_local, info in self._cached_rects:
            r_global = rect_local.move(offset_x, offset_y)
            if r_global.collidepoint(mouse_x, mouse_y):
                self._draw_hover_tip(surface, info, mouse_x, mouse_y)
                break

    def _draw_hover_tip(self, surface, info, mouse_x, mouse_y):
        draw_tooltip(surface, info, (mouse_x, mouse_y),
                     self.sim.screen_width, dark=self.dark_mode)

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        # 滚轮滚动
        if e.type == pygame.MOUSEWHEEL:
            if self._content_h > self.bg_rect.height:
                max_scroll = self._content_h - self.bg_rect.height
                self._scroll_y = max(0, min(max_scroll, self._scroll_y - e.y * 30))
                return True
            return False

        # ── 颜色按钮（必须在 handle_drag_event 之前检查，
        #     因为按钮在标题栏区域内，否则会被拖拽抢先捕获）──
        # 按钮位于滚动内容顶部: 命中判定需减去滚动偏移,否则滚动后
        # 按钮已滚出可视区却仍可被"隔空"点击
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            btn_global = self._color_btn_rect.move(self.bg_rect.x, self.bg_rect.y - self._scroll_y)
            if btn_global.collidepoint(e.pos):
                self._color_mode = 1 - self._color_mode
                self._build()          # _build 内已保留 bg_rect 位置
                return True
            ace_btn_global = self._ace_btn_rect.move(self.bg_rect.x, self.bg_rect.y - self._scroll_y)
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
