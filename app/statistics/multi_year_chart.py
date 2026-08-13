# py/statistics/multi_year_chart.py
"""多年度 ACE 统计图表对话框：年度柱状图 + 多年曲线叠加。"""
from __future__ import annotations
import pygame
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from ..constants import (f_s, f_m, rt, TXT, DIALOG_TITLE_BAR_HEIGHT,
                         SETTINGS_TEXT_LIGHT, HEMISPHERE_NORTH)
from ..dialog_base import DraggableDialog
from .chart_helpers import (chart_axis, chart_dark, set_chart_dark,
                            draw_vscrollbar, draw_tooltip, build_basin_order)
from .chart_presets import (draw_yearly_ace_chart, draw_multi_year_curve_chart,
                            draw_monthly_bars_chart, draw_basin_comparison_chart)
from .shared import COLORS


class MultiYearDialog(DraggableDialog):
    """多年度 ACE 分析对话框：年度柱状图 + 多年曲线叠加。"""

    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._tab = 0  # 0=柱状图, 1=曲线叠加
        self._cached_data = None
        self._hover_info = None
        self._hover_pos = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._tab_rects = {}

    def activate(self):
        super().activate()
        w, h = min(1300, self.sim.screen_width - 40), min(800, self.sim.screen_height - 60)
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_data = self._build_data()
        self._tab = 0
        self._selected_year = self.sim.current_ace_year
        self._monthly_data = None

    def deactivate(self):
        super().deactivate()
        self._cached_data = None

    def _build_data(self):
        """收集全部可用年份的 ACE 数据 + 多年曲线 + 月度 + 洋区。"""
        yad = dict(self.sim.yad)
        available = sorted(k for k, v in yad.items() if v > 0)
        if not available:
            return {'years_ace': [], 'curves': [], 'monthly_ace': [], 'basin_stats': []}

        current = self.sim.current_ace_year
        pairs = [(y, yad[y]) for y in available]
        engine = self.sim.ace_engine

        curves = []
        for y in available[-8:]:  # 最近 8 年
            cached = getattr(self.sim, '_ace_timeline_cache', {}).get(y)
            if cached:
                sd, ed = engine.ace_year_range(y)
                th = int((ed - sd).total_seconds() / 3600)
                curves.append((y, cached, yad[y], (sd, ed, th)))

        # 当前年份的月度 ACE 数据（按自然月分组，从 ACE 年起始日算起；当年含当前时间 cutoff）
        monthly_ace = []
        if current in available:
            sd, _ = engine.ace_year_range(current)
            cutoff = None
            if current == getattr(self.sim, 'current_ace_year', None):
                try:
                    cutoff = datetime(int(self.sim.sy), int(self.sim.st[0:2]),
                                      int(self.sim.st[2:4]), int(self.sim.st[4:6]))
                except (ValueError, IndexError):
                    cutoff = None
            dc = engine.daily_ace(current, cutoff)
            buckets = {m: 0.0 for m in range(1, 13)}
            for di, val in enumerate(dc):
                day = sd + timedelta(days=di)
                m = day.month
                if m in buckets:
                    buckets[m] += val
            for m in range(1, 13):
                if self.sim.hemisphere == HEMISPHERE_NORTH:
                    cal = m
                else:
                    cal = m + 6 if m <= 6 else m - 6
                monthly_ace.append((cal, buckets[m]))

        # 当前年份的洋区 ACE（法23: 单趟按点归属分桶,免 A× 全扫）
        # A4: 与 yad/daily_ace 同口径——应用 point_in_limit(设置里的 ACE 地理限制),
        # 使各洋区之和与年度总 ACE 一致(限制开启时不再超额)。
        basin_stats = []
        areas = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if areas and areas.areas:
            ace_by_area: dict = {}
            for ty in self.sim.tys:
                for p in ty.pts:
                    if p.get('ace_year') != current:
                        continue
                    if not engine.point_in_limit(p['la'], p['lo']):
                        continue
                    v = p.get('pace', 0.0)
                    if v <= 0:
                        continue
                    area = areas.find_area(p['la'], p['lo'])
                    if area:
                        ace_by_area[area.code] = ace_by_area.get(area.code, 0.0) + v
            ci = 0
            for a in areas.areas:
                ace = ace_by_area.get(a.code, 0.0)
                if ace > 0:
                    basin_stats.append((a.name_cn, ace, COLORS[ci % len(COLORS)]))
                    ci += 1

        return {'years_ace': pairs, 'curves': curves, 'monthly_ace': monthly_ace,
                'basin_stats': basin_stats, 'current': current}

    def _chart_rect(self) -> pygame.Rect:
        r = self.bg_rect
        return pygame.Rect(r.x + 70, r.y + 70, r.width - 140, r.height - 180)

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        data = self._cached_data
        if not data or not data['years_ace']:
            self._draw_empty(surface)
            return

        dark = self.dark_mode
        set_chart_dark(dark)
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        r = self.bg_rect

        if dark:
            self.draw_dark_panel(surface, r)
        else:
            self.draw_background(surface, r)

        # 标题
        title = rt(f_m, "多年度 ACE 统计", tc)
        surface.blit(title, (r.x + 16, r.y + 10))

        # 关闭按钮
        cb = pygame.Rect(r.x + r.width - 90, r.y + 8, 55, 25)
        self._close_btn_rect = cb
        if dark:
            self.draw_dark_button(surface, cb, "关闭")
        else:
            self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # Tab 切换
        self._tab_rects = {}
        tab_y = r.y + 38
        tab_names = ["年度 ACE 柱状图", "多年累积曲线", "月度 ACE", "洋区对比"]
        for i, name in enumerate(tab_names):
            br = pygame.Rect(r.x + 20 + i * 140, tab_y, 128, 24)
            self._tab_rects[name] = br
            if dark:
                self.draw_dark_button(surface, br, name, hover=self._tab == i,
                                      accent=self._tab == i)
            else:
                style = 'primary' if self._tab == i else 'light'
                self.draw_button(surface, br, rt(f_s, name, (255, 255, 255)), style=style)

        chart_rect = self._chart_rect()
        self._hover_info = None
        self._hover_pos = None

        if self._tab == 0:
            hint = draw_yearly_ace_chart(surface, chart_rect,
                                         data['years_ace'], data['current'])
        elif self._tab == 1:
            hint = draw_multi_year_curve_chart(surface, chart_rect, data['curves'])
        elif self._tab == 2:
            # N21: 南半球 cal 序(1=7月)时显示真实自然月标签
            labels = None
            if self.sim.hemisphere == 'south':
                labels = [str((cal + 5) % 12 + 1) for cal in range(1, 13)]
            hint = draw_monthly_bars_chart(surface, chart_rect,
                                           data.get('monthly_ace', []),
                                           bar_color=(60, 180, 140),
                                           y_label="月度累计 ACE",
                                           month_labels=labels)
        else:
            hint = draw_basin_comparison_chart(surface, chart_rect,
                                               data.get('basin_stats', []))

        if hint:
            self._hover_info, self._hover_pos = hint

        if self._hover_info and self._hover_pos:
            draw_tooltip(surface, self._hover_info,
                         self._hover_pos, self.sim.screen_width, dark=dark)

    def _draw_empty(self, surface):
        r = self.bg_rect
        dark = self.dark_mode
        if dark:
            self.draw_dark_panel(surface, r)
        else:
            self.draw_background(surface, r)
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        no_data = rt(f_m, "无可用年份数据", tc)
        surface.blit(no_data, (r.centerx - no_data.get_width() // 2, r.centery - 20))
        cb = pygame.Rect(r.x + r.width - 90, r.y + 8, 55, 25)
        self._close_btn_rect = cb
        if dark:
            self.draw_dark_button(surface, cb, "关闭")
        else:
            self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self._close_btn_rect.collidepoint(x, y):
                self.deactivate()
                return True
            for name, rect in self._tab_rects.items():
                if rect.collidepoint(x, y):
                    if name.startswith("年度 ACE"):
                        self._tab = 0
                    elif name.startswith("多年累积"):
                        self._tab = 1
                    elif name.startswith("月度"):
                        self._tab = 2
                    else:
                        self._tab = 3
                    return True
            if self.bg_rect.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.bg_rect.collidepoint(mx, my):
                self._tab = (self._tab - e.y) % 4
                return True
        return False
