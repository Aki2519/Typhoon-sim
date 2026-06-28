# py/statistics/dialog_chart.py
"""ACE 统计图表对话框 + 统计面板 + 洋区框 + 辅助按钮。"""
from __future__ import annotations
import pygame
import os
from typing import List, Tuple, Optional, Dict

from ..constants import (
    f_s, f_m, f_l, rt, TXT, BUTTON_BORDER, LIST_BG,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
    ACE_CHART_DEFAULT_WIDTH, ACE_CHART_DEFAULT_HEIGHT,
    ACE_CHART_PADDING_LEFT, ACE_CHART_PADDING_RIGHT, ACE_CHART_PADDING_TOP,
    DIALOG_TITLE_BAR_HEIGHT,
)
from ..input_field import InputField
from ..dialog_base import DraggableDialog

from .data_builder_chart import build_chart_data, ChartData
from .draw_helpers_chart import draw_dashed_h, build_month_lines_surface
from .curve_chart import draw_curve_chart
from .daily_ace_chart import draw_daily_ace_chart
from .active_periods_chart import draw_active_periods_chart
from .activity_count_chart import draw_activity_count_chart
from .typhoon_ace_chart import draw_typhoon_ace_chart
from .season_stats import calculate_season_stats

WINDOW_WIDTH_SCALE = 1.2


class ACEChartDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.close_text = rt(f_s, "关闭", (255, 255, 255))
        self.save_text = rt(f_s, "保存图片", (255, 255, 255))
        self._stats_toggle_text = rt(f_s, "统计数据", (255, 255, 255))
        self._path_cmp_text = rt(f_s, "路径对比", (255, 255, 255))
        self._heatmap_text = rt(f_s, "热力图", (255, 255, 255))
        self._path_len_text = rt(f_s, "路径长度", (255, 255, 255))
        self._jump_btn_text = rt(f_s, "指定年份", (255, 255, 255))
        self._jump_prompt_text = rt(f_s, "输入年份:", TXT)
        self._no_data_text = rt(f_m, "无可用年份数据", TXT)

        self.cumulative_to_current = False
        max_w = max(800, min(int(ACE_CHART_DEFAULT_WIDTH * WINDOW_WIDTH_SCALE), self.sim.screen_width - 20))
        max_h = max(600, min(ACE_CHART_DEFAULT_HEIGHT, self.sim.screen_height - 80))
        self.window_width = max_w
        self.window_height = max_h
        self._max_window_height = max_h
        self.bg_rect = pygame.Rect(0, 0, self.window_width, self.window_height)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

        # 数据
        self._chart_data = ChartData()
        self._needs_update = True
        self._layout_valid = False
        self._show_stats = False
        self._stats_data: Optional[Dict] = None

        # 缓存
        self._cached_title_surf: Optional[pygame.Surface] = None
        self._cached_title_hash: Optional[int] = None
        self._cached_mode_text: Optional[pygame.Surface] = None
        self._cached_mode_hash: Optional[int] = None

        self.padding_left = ACE_CHART_PADDING_LEFT
        self.padding_right = ACE_CHART_PADDING_RIGHT
        self.padding_top = ACE_CHART_PADDING_TOP
        self.graph_width = self.window_width - self.padding_left - self.padding_right

        self.curve_rect = pygame.Rect(0, 0, 0, 0)
        self.daily_bar_rect = pygame.Rect(0, 0, 0, 0)
        self.chart2_rect = pygame.Rect(0, 0, 0, 0)
        self.chart3_rect = pygame.Rect(0, 0, 0, 0)
        self.bar_rect = pygame.Rect(0, 0, 0, 0)

        self.curve_height = 250
        self.daily_height = 180
        self.chart2_height = 360
        self.chart3_height = 120
        self.typhoon_height = 250
        self._typhoon_bar_width = 30

        self._available_years: List[int] = []
        self._selected_year_index = -1

        self._hover_info: Optional[str] = None
        self._hover_pos: Optional[Tuple[int, int]] = None
        self._active_period_click_targets: list = []

        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._mode_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._save_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._jump_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._arrow_left: Optional[pygame.Rect] = None
        self._arrow_right: Optional[pygame.Rect] = None

        self._bar_page = 0
        self._bar_arrow_left: Optional[pygame.Rect] = None
        self._bar_arrow_right: Optional[pygame.Rect] = None

        self._jump_active = False
        self._jump_field: Optional[InputField] = None

        self._month_lines_surface: Optional[pygame.Surface] = None
        self._month_line_xs: List[float] = []
        self._month_line_labels: List[str] = []
        self._month_line_top = 0
        self._month_line_bottom = 0
        self._month_label_y = 0

        self._month_label_surfs: Optional[List[pygame.Surface]] = None
        self._month_label_surf_xs: Optional[List[float]] = None

        # 底部按钮区域
        self._stats_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._path_cmp_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._heatmap_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._path_len_btn_rect = pygame.Rect(0, 0, 0, 0)

        # 洋区框绘制
        self._basin_box_rect = pygame.Rect(0, 0, 0, 0)

    # ═══════════════════════════════════════════════
    def activate(self):
        super().activate()
        self.dragging = False
        self._bar_page = 0
        self._jump_active = False
        self._jump_field = None
        self.cumulative_to_current = False
        self._layout_valid = False
        self._show_stats = False
        self._stats_data = None
        self._invalidate_caches()

        self._available_years = sorted([y for y, v in self.sim.yad.items() if v > 0])
        if not self._available_years:
            self._available_years = []
        target = self.sim.current_ace_year
        if target in self._available_years:
            idx = self._available_years.index(target)
        elif self._available_years:
            idx = next((i for i, y in enumerate(self._available_years) if y >= target), 0)
        else:
            idx = -1
        self._selected_year_index = idx
        self._needs_update = True

        if idx >= 0:
            self._rebuild()
            self._compute_layout()
            self._build_month_cache()
            self._layout_valid = True

        self._center_in_map()

    def deactivate(self):
        super().deactivate()
        self.dragging = False
        self._invalidate_caches()
        self._stats_data = None

    def _invalidate_caches(self):
        self._cached_title_surf = None
        self._cached_title_hash = None
        self._cached_mode_text = None
        self._cached_mode_hash = None
        self._month_label_surfs = None
        self._month_label_surf_xs = None

    # ═══════════════════════════════════════════════
    #  布局
    # ═══════════════════════════════════════════════
    def _center_in_map(self):
        map_cx = self.sim.screen_width // 2
        map_cy = self.sim.map_height // 2
        self.bg_rect.x = max(0, map_cx - self.window_width // 2)
        self.bg_rect.y = max(0, map_cy - self.window_height // 2)
        if self.bg_rect.right > self.sim.screen_width:
            self.bg_rect.x = self.sim.screen_width - self.window_width
        if self.bg_rect.bottom > self.sim.screen_height:
            self.bg_rect.y = self.sim.screen_height - self.window_height
        self._layout_valid = False

    def _rebuild(self):
        if self._selected_year_index < 0:
            self._chart_data = ChartData()
            return
        year = self._available_years[self._selected_year_index]
        self._chart_data = build_chart_data(
            self.sim, year, self.cumulative_to_current, self._available_years
        )
        # 计算统计数据（洋区限定）
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        basin = bc if lm == 'basin' else None
        self._stats_data = calculate_season_stats(self.sim, year, basin)
        self._invalidate_caches()
        self._layout_valid = False

    def _compute_layout(self):
        if self._layout_valid:
            return

        graph_width = self.window_width - self.padding_left - self.padding_right
        chart_data = self._chart_data

        n_ty = max(1, len(chart_data.typhoon_ace_list))
        tbw = min(40, graph_width / n_ty * 0.7)
        self._typhoon_bar_width = tbw

        chart2_h = 480
        chart3_h = max(40, int(2 * tbw))
        curve_h, daily_h, typhoon_h = 250, 180, 250

        top_area = self.padding_top + 40 + 30
        bottom_area = 70
        label_row = 18
        gap = 10

        needed = top_area + curve_h + daily_h + chart2_h + chart3_h + label_row + gap + typhoon_h + bottom_area
        max_h = 1440
        if needed > self.window_height and needed <= max_h:
            self.window_height = needed
        elif needed > max_h:
            self.window_height = max_h
            avail = max_h - top_area - label_row - gap - bottom_area
            total = curve_h + daily_h + chart2_h + chart3_h + typhoon_h
            if total > 0 and avail > 0:
                s = avail / total
                curve_h = max(100, int(curve_h * s))
                daily_h = max(60, int(daily_h * s))
                chart2_h = max(40, int(chart2_h * s))
                chart3_h = max(30, int(chart3_h * s))
                typhoon_h = max(100, int(typhoon_h * s))

        self.curve_height = curve_h
        self.daily_height = daily_h
        self.chart2_height = chart2_h
        self.chart3_height = chart3_h
        self.typhoon_height = typhoon_h
        self.graph_width = graph_width

        bx = self.bg_rect.x + self.padding_left
        top = self.bg_rect.y + self.padding_top + 32
        self.curve_rect = pygame.Rect(bx, top, graph_width, curve_h)
        self.daily_bar_rect = pygame.Rect(bx, self.curve_rect.bottom, graph_width, daily_h)
        self.chart2_rect = pygame.Rect(bx, self.daily_bar_rect.bottom, graph_width, chart2_h)
        self.chart3_rect = pygame.Rect(bx, self.chart2_rect.bottom, graph_width, chart3_h)
        self._month_label_y = self.chart3_rect.bottom + 7
        bt = self.chart3_rect.bottom + label_row + gap + 5
        self.bar_rect = pygame.Rect(bx, bt, graph_width, typhoon_h)
        self._month_line_top = self.curve_rect.top
        self._month_line_bottom = self.chart3_rect.bottom + 5
        total_h = self.bar_rect.bottom - self.bg_rect.y + 55
        self.bg_rect.height = min(self._max_window_height, max(self.window_height, total_h))
        self.window_height = self.bg_rect.height

        self._layout_valid = True

    def _build_month_cache(self):
        cd = self._chart_data
        sd, _, th = cd.year_range
        w = self.graph_width
        h = self._month_line_bottom - self._month_line_top
        surf, xs, labels = build_month_lines_surface(w, h, sd, th, self.sim.hemisphere)
        self._month_lines_surface = surf
        self._month_line_xs = xs
        self._month_line_labels = labels
        self._month_label_surfs = [rt(f_s, lbl, TXT) for lbl in labels]
        self._month_label_surf_xs = xs

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════
    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        if self._selected_year_index < 0 or not self._available_years:
            self.draw_background(surface, self.bg_rect)
            surface.blit(self._no_data_text, (
                self.bg_rect.centerx - self._no_data_text.get_width() // 2,
                self.bg_rect.centery - self._no_data_text.get_height() // 2))
            cb = pygame.Rect(self.bg_rect.x + self.window_width - 90, self.bg_rect.y + 8, 55, 25)
            self._close_btn_rect = cb
            self.draw_button(surface, cb, self.close_text)
            return

        if self._needs_update:
            self._rebuild()
            self._needs_update = False
        self._compute_layout()
        self.draw_background(surface, self.bg_rect)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width

        # 标题
        title_hash = self._title_hash()
        if self._cached_title_surf is None or self._cached_title_hash != title_hash:
            self._cached_title_surf = rt(f_l, self._build_title(), TXT)
            self._cached_title_hash = title_hash
        surface.blit(self._cached_title_surf, (bx + 12, by + 8))

        self._draw_top_buttons(surface, bx, by, bw)
        self._hover_info = None
        self._hover_pos = None

        cd = self._chart_data

        # 月份线
        if self._month_lines_surface is None:
            self._build_month_cache()
        if self._month_lines_surface is not None:
            surface.blit(self._month_lines_surface, (bx + self.padding_left, self._month_line_top))

        # 图表
        hint = draw_curve_chart(surface, self.curve_rect, cd.ace_curve_points,
                                cd.year_range, cd.year_total_ace, draw_dashed_h)
        self._apply_hint(hint)
        hint = draw_daily_ace_chart(surface, self.daily_bar_rect, cd.daily_ace_list,
                                    cd.year_range, draw_dashed_h)
        self._apply_hint(hint)
        # 构建洋区代码→中文名映射
        area_map = {}
        areas = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if areas and areas.areas:
            area_map = {a.code: a.name_cn for a in areas.areas}
        hint, click_targets = draw_active_periods_chart(surface, self.chart2_rect, cd.active_periods,
                                         cd.year_range, self._typhoon_bar_width, area_map)
        self._apply_hint(hint)
        self._active_period_click_targets = click_targets
        hint = draw_activity_count_chart(surface, self.chart3_rect, cd.activity_count_list,
                                         cd.year_range, draw_dashed_h)
        self._apply_hint(hint)

        # 月份标签
        if self._month_label_surfs and self._month_label_surf_xs:
            for x_px, ls in zip(self._month_label_surf_xs, self._month_label_surfs):
                surface.blit(ls, (bx + self.padding_left + x_px - ls.get_width() // 2, self._month_label_y))

        hint, total_pages, multi = draw_typhoon_ace_chart(
            surface, self.bar_rect, cd.typhoon_ace_list, self._bar_page
        )
        self._apply_hint(hint)

        if multi:
            by2 = self.bar_rect.bottom + 28
            bl = pygame.Rect(self.bar_rect.x + 5, by2, 16, 16)
            br2 = pygame.Rect(self.bar_rect.right - 21, by2, 16, 16)
            pygame.draw.polygon(surface, TXT,
                                [(bl.right, bl.top), (bl.right, bl.bottom), (bl.left + 4, bl.centery)])
            pygame.draw.polygon(surface, TXT,
                                [(br2.left, br2.top), (br2.left, br2.bottom), (br2.right - 4, br2.centery)])
            self._bar_arrow_left = bl
            self._bar_arrow_right = br2
        else:
            self._bar_arrow_left = None
            self._bar_arrow_right = None

        # 洋区框 — 已移除，不再在地图坐标绘制独立矩形

        # 统计面板 — 已改为独立对话框
        # if self._show_stats and self._stats_data:
        #     self._draw_stats_panel(surface, bx)

        self._draw_bottom_buttons(surface, bx)
        self._draw_hover(surface)

    def _draw_basin_box(self, surface):
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        if lm != 'basin' or not bc:
            return
        area = self.sim.res_mgr.ocean_areas.get_by_code(bc)
        if not area or not area.vertices:
            return

        # 计算边界 +5°
        lons = [v[1] for v in area.vertices]
        lats = [v[0] for v in area.vertices]
        min_lon, max_lon = min(lons) - 5, max(lons) + 5
        min_lat, max_lat = min(lats) - 5, max(lats) + 5

        # 转换为屏幕坐标
        top_left = self.sim.latlon_to_screen(max_lat, min_lon)
        bottom_right = self.sim.latlon_to_screen(min_lat, max_lon)

        # 绘制到地图上（不画在对话框内）
        map_rect = pygame.Rect(
            min(top_left[0], bottom_right[0]),
            min(top_left[1], bottom_right[1]),
            abs(bottom_right[0] - top_left[0]),
            abs(bottom_right[1] - top_left[1])
        )
        self._basin_box_rect = map_rect
        pygame.draw.rect(surface, (100, 180, 240), map_rect, 2)  # 浅蓝色实线

    def _draw_stats_panel(self, surface, bx):
        if not self._stats_data:
            return
        stats = self._stats_data
        # 对齐图表左侧 padding_left
        start_x = bx + self.padding_left
        y_base = self.bar_rect.bottom + 15

        # 竖列布局：3 列，每列 5 行（共15项自动补齐）
        labels = [
            ("总低压数", f"{stats['total_td']}", "达到TD及以上强度且性质达标"),
            ("总风暴数", f"{stats['total_ts']}", "达到TS及以上强度且性质达标"),
            ("总台风数", f"{stats['total_ty']}", "达到C1及以上强度且性质达标"),
            ("总MH数", f"{stats['total_mh']}", "达到C3及以上强度且性质达标"),
            ("总C5数", f"{stats['total_c5']}", "达到C5及以上强度且性质达标"),
            ("总系统数", f"{stats['total_systems']}", "统计所有系统,任何性质与强度"),
            ("风王", f"{stats['wind_king'][0]}: {stats['wind_king'][1]:.0f}kt" if stats['wind_king'] else "-", "强度最高TC"),
            ("ACE王", f"{stats['ace_king'][0]}: {stats['ace_king'][1]:.2f}" if stats['ace_king'] else "-", "ACE最高TC"),
            ("登陆王", f"{stats['landfall_king'][0]}: {stats['landfall_king'][1]:.0f}kt" if stats['landfall_king'] else "-", "登陆强度最高TC"),
            ("寿命王", f"{stats['lifetime_king'][0]}: {stats['lifetime_king'][1]:.0f}h" if stats['lifetime_king'] else "-", "TS及以上活跃时间最长的TC"),
            ("总ACE", f"{stats['total_ace']:.4f}", "该洋区ACE加总"),
            ("总活跃时间", f"{stats['total_active_hours']:.1f}h", "TS及以上活跃时间加总"),
            ("风暴天", f"{stats['storm_days']:.2f}d", "每个活跃正式报+0.25天"),
            ("登陆次数", f"{stats['landfall_count']}", "总登陆次数"),
            ("总路径长度", f"{stats['total_path_km']:.0f}km", "TS及以上路径长度加总"),
        ]

        num_cols = 3
        rows_per_col = 5
        col_width = 280

        for i, (label, value, tooltip) in enumerate(labels):
            col = i // rows_per_col
            row = i % rows_per_col
            x = start_x + col * col_width
            y = y_base + row * 22

            line = rt(f_s, f"{label}: {value}", TXT)
            surface.blit(line, (x, y))

            # 存储悬停区域
            if not hasattr(self, '_stats_hover_rects'):
                self._stats_hover_rects = []
            if len(self._stats_hover_rects) <= i:
                self._stats_hover_rects.append(None)
            self._stats_hover_rects[i] = (pygame.Rect(x, y, line.get_width(), line.get_height()), tooltip)

        # 悬停检测
        mx, my = pygame.mouse.get_pos()
        for rect, tip in self._stats_hover_rects:
            if rect and rect.collidepoint(mx, my):
                now = pygame.time.get_ticks()
                if not hasattr(self, '_stats_hover_start'):
                    self._stats_hover_start = now
                    self._last_hover_rect = rect
                elif self._last_hover_rect == rect and now - self._stats_hover_start > 2000:
                    self._hover_info = tip
                    self._hover_pos = (mx + 15, my - 25)
                elif self._last_hover_rect != rect:
                    self._stats_hover_start = now
                    self._last_hover_rect = rect
                break

    def _draw_bottom_buttons(self, surface, bx):
        y_bottom = self.bg_rect.y + self.bg_rect.height - 45
        btn_w, btn_h, gap = 90, 28, 8
        # 保存图片
        sb = pygame.Rect(bx + 15, y_bottom, 100, btn_h)
        self._save_btn_rect = sb
        self.draw_button(surface, sb, self.save_text)
        # 指定年份
        jb = pygame.Rect(sb.right + gap, y_bottom, 100, btn_h)
        self._jump_btn_rect = jb
        self.draw_button(surface, jb, self._jump_btn_text)
        # 统计数据切换
        stb = pygame.Rect(jb.right + gap, y_bottom, 100, btn_h)
        self._stats_btn_rect = stb
        self.draw_button(surface, stb, self._stats_toggle_text)
        # 路径对比
        pcb = pygame.Rect(stb.right + gap, y_bottom, 100, btn_h)
        self._path_cmp_btn_rect = pcb
        self.draw_button(surface, pcb, self._path_cmp_text)
        # 热力图
        hb = pygame.Rect(pcb.right + gap, y_bottom, 100, btn_h)
        self._heatmap_btn_rect = hb
        self.draw_button(surface, hb, self._heatmap_text)
        # 路径长度
        plb = pygame.Rect(hb.right + gap, y_bottom, 100, btn_h)
        self._path_len_btn_rect = plb
        self.draw_button(surface, plb, self._path_len_text)

        if self._jump_active and self._jump_field:
            self._jump_field.draw(surface)
            surface.blit(self._jump_prompt_text,
                         (self._jump_field.rect.x, self._jump_field.rect.y - 22))

    def _draw_hover(self, surface):
        if self._hover_info and self._hover_pos:
            tip = rt(f_s, self._hover_info, TXT)
            tw, th = tip.get_width() + 8, tip.get_height() + 8
            tx, ty = self._hover_pos
            if ty - th < 0:
                ty = self._hover_pos[1] + 15
            if tx + tw > self.sim.screen_width:
                tx = self.sim.screen_width - tw - 5
            tb = pygame.Surface((tw, th), pygame.SRCALPHA)
            tb.fill((255, 255, 255, 200))
            pygame.draw.rect(tb, BUTTON_BORDER, (0, 0, tw, th), 1)
            tb.blit(tip, (4, 4))
            surface.blit(tb, (tx, ty - th))

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if self._jump_active:
            return self._handle_jump_event(e)
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._handle_click(e):
                return True
        if self.handle_drag_event(e):
            self._layout_valid = False
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        return False

    def _handle_click(self, e):
        x, y = e.pos
        # 优先检测关闭按钮（禁止事件穿透）
        if self._close_btn_rect.collidepoint(x, y):
            self.deactivate()
            return True
        # 活跃时间图点击 → 跳转到台风生成时间
        for br_screen, period in self._active_period_click_targets:
            if br_screen.collidepoint(x, y):
                dt = period['start_dt']
                self._jump_sim_to(dt)
                return True
        # 统计数据切换 → 打开独立统计对话框
        if self._stats_btn_rect.collidepoint(x, y):
            dlg = self.sim.dialog_mgr.season_stats
            year = self._available_years[self._selected_year_index]
            self.sim.current_ace_year = year
            dlg.activate()
            return True
        # 路径对比
        if self._path_cmp_btn_rect.collidepoint(x, y):
            from .path_comparison import PathComparisonDialog
            dlg = self.sim.dialog_mgr.path_comparison
            year = self._available_years[self._selected_year_index]
            self.sim.current_ace_year = year
            dlg.activate()
            return True
        # 热力图
        if self._heatmap_btn_rect.collidepoint(x, y):
            from .heatmap import PathHeatmapDialog
            dlg = self.sim.dialog_mgr.heatmap
            year = self._available_years[self._selected_year_index]
            self.sim.current_ace_year = year
            dlg.activate()
            return True
        # 路径长度
        if self._path_len_btn_rect.collidepoint(x, y):
            from .path_length_viewer import PathLengthViewer
            dlg = self.sim.dialog_mgr.path_length_viewer
            year = self._available_years[self._selected_year_index]
            self.sim.current_ace_year = year
            dlg.activate()
            return True
        # 模式切换
        if self._mode_btn_rect.collidepoint(x, y):
            self.cumulative_to_current = not self.cumulative_to_current
            self._invalidate_caches()
            self._needs_update = True
            return True
        # 保存图片
        if self._save_btn_rect.collidepoint(x, y):
            self._save_chart_image()
            return True
        # 跳页
        if self._jump_btn_rect.collidepoint(x, y):
            self._start_jump()
            return True
        # 年份箭头
        if self._arrow_left and self._arrow_left.collidepoint(x, y):
            if self._selected_year_index > 0:
                self._selected_year_index -= 1
                self._invalidate_caches()
                self._needs_update = True
            return True
        if self._arrow_right and self._arrow_right.collidepoint(x, y):
            if self._selected_year_index < len(self._available_years) - 1:
                self._selected_year_index += 1
                self._invalidate_caches()
                self._needs_update = True
            return True
        # 柱状图翻页
        if self._bar_arrow_left and self._bar_arrow_left.collidepoint(x, y):
            if self._bar_page > 0:
                self._bar_page -= 1
            return True
        if self._bar_arrow_right and self._bar_arrow_right.collidepoint(x, y):
            tp = (len(self._chart_data.typhoon_ace_list) + 19) // 20
            if self._bar_page < tp - 1:
                self._bar_page += 1
            return True
        return False

    # ── 跳页 ──
    def _handle_jump_event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._jump_active = False
                self._jump_field = None
                return True
            if e.key == pygame.K_RETURN:
                self._do_jump()
                return True
        if self._jump_field and self._jump_field.handle_event(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._jump_field and not self._jump_field.rect.collidepoint(e.pos):
                self._do_jump()
                return True
        return True

    def _start_jump(self):
        self._jump_active = True
        r = pygame.Rect(self.bg_rect.x + self.bg_rect.width // 2 - 50, self.bg_rect.y + 35, 100, 24)
        self._jump_field = InputField(r, max_length=4, validator=lambda c: c.isdigit())
        self._jump_field.activate()

    def _do_jump(self):
        if self._jump_field:
            try:
                y = int(self._jump_field.get_text())
                if y in self._available_years:
                    self._selected_year_index = self._available_years.index(y)
                    self._invalidate_caches()
                    self._needs_update = True
            except ValueError:
                pass
        self._jump_active = False
        self._jump_field = None

    def _save_chart_image(self):
        out = "./picture"
        os.makedirs(out, exist_ok=True)
        yv = self._available_years[self._selected_year_index]
        yd = self._year_display(yv).replace(' ', '_')
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        if lm in ('none', '') and not bc:
            fn = f"{yd}_GLOBAL_ACE"
        elif lm == 'basin' and bc:
            a = self.sim.res_mgr.ocean_areas.get_by_code(bc)
            fn = f"{yd}_{a.name_full if a else bc}_ACE"
        else:
            fn = f"{yd}_ACE"

        ox, oy = self.bg_rect.x, self.bg_rect.y
        self.bg_rect.x = 0
        self.bg_rect.y = 0
        self._layout_valid = False
        self._compute_layout()
        tmp = pygame.Surface((self.window_width, self.window_height))
        self.draw(tmp)
        self.bg_rect.x, self.bg_rect.y = ox, oy
        self._layout_valid = False
        self._compute_layout()

        fp = os.path.join(out, f"{fn}.png")
        try:
            pygame.image.save(tmp, fp)
            self.sim.show_error(f"图表已保存为 {fp}")
        except Exception as e:
            self.sim.show_error(f"保存失败: {e}")

    # ── 标题 ──
    def _build_title(self) -> str:
        yv = self._available_years[self._selected_year_index]
        yd = self._year_display(yv)
        tace = self.sim.yad.get(yv, 0.0)
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        if lm in ('none', '') and not bc:
            return f"{yd} GLOBAL ACE: {tace:.4f}"
        if lm == 'basin' and bc:
            a = self.sim.res_mgr.ocean_areas.get_by_code(bc)
            bd = a.name_full if a else bc
            avg = a.avg_ace if a else 0.0
        else:
            bd = ""
            avg = getattr(self.sim.res_mgr.ocean_areas, 'total_avg_ace', 0)
        anom = tace - avg
        sign = '+' if anom >= 0 else ''
        if bd:
            return f"{yd} {bd} ACE: {tace:.4f} ({sign}{anom:.4f})"
        return f"{yd} ACE: {tace:.4f} ({sign}{anom:.4f})"

    def _year_display(self, year: int) -> str:
        if self.sim.hemisphere == HEMISPHERE_SOUTH:
            return f"{year} - {year + 1}"
        return str(year)

    def _title_hash(self) -> int:
        yv = self._available_years[self._selected_year_index]
        return hash((yv, self.cumulative_to_current, self.sim.current_ace_year))

    def _apply_hint(self, hint):
        if hint is not None:
            self._hover_info, self._hover_pos = hint

    def _jump_sim_to(self, dt):
        """跳转模拟时间到指定日期（委托给 SeasonController）。"""

        sim = self.sim
        if hasattr(sim, 'season_ctrl'):
            sim.season_ctrl.jump_to(dt)
            sim._sync_season_state()
        sim._view_dirty = True
        self._needs_update = True

    def _draw_top_buttons(self, surface, bx, by, bw):
        BAR_Y = by + 6
        BAR_H = 26
        GAP = 8
        a_sz = 16
        cbtn = pygame.Rect(bx + bw - 55 - 10, BAR_Y, 55, BAR_H)
        self.draw_button(surface, cbtn, self.close_text)
        self._close_btn_rect = cbtn
        acy = BAR_Y + BAR_H // 2
        rr = pygame.Rect(cbtn.left - GAP - a_sz, acy - a_sz // 2, a_sz, a_sz)
        pygame.draw.polygon(surface, TXT,
                            [(rr.left, rr.top), (rr.left, rr.bottom), (rr.right - 4, rr.centery)])
        self._arrow_right = rr
        lr = pygame.Rect(rr.left - GAP - a_sz, acy - a_sz // 2, a_sz, a_sz)
        pygame.draw.polygon(surface, TXT,
                            [(lr.right, lr.top), (lr.right, lr.bottom), (lr.left + 4, lr.centery)])
        self._arrow_left = lr
        mb = pygame.Rect(lr.left - GAP - 165, BAR_Y, 165, BAR_H)

        sy = self._available_years[self._selected_year_index]
        mode_hash = (self.cumulative_to_current, sy, self.sim.current_ace_year, self.sim.hemisphere)
        if self._cached_mode_text is None or self._cached_mode_hash != mode_hash:
            if self.cumulative_to_current and sy == self.sim.current_ace_year:
                ms = "当前：运行时间"
            elif self.cumulative_to_current:
                ms = "当前：非当前年份"
            else:
                ms = "当前：截止到12/31" if self.sim.hemisphere == HEMISPHERE_NORTH else "当前：截止到次年6/30"
            self._cached_mode_text = rt(f_s, ms, TXT)
            self._cached_mode_hash = mode_hash
        self.draw_button(surface, mb, self._cached_mode_text)
        self._mode_btn_rect = mb