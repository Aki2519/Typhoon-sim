from __future__ import annotations

# py/statistics/season_stats_dialog.py
"""洋区统计数据独立对话框。"""
import pygame
from typing import Optional, Dict

from ..constants import (f_s, f_m, rt, TXT, DIALOG_TITLE_BAR_HEIGHT,
                         SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM, settings_accent)
from ..dialog_base import DraggableDialog
from .season_stats import calculate_season_stats
from .chart_helpers import draw_tooltip

_HOVER_DELAY_MS = 600


class SeasonStatsDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._year: int = 0
        self._stats_data: Optional[Dict] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._summary_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._stats_hover_rects: list = []
        self._stats_hover_start: int = 0
        self._last_hover_rect = None
        self._content_surf: Optional[pygame.Surface] = None
        self._content_key = None
        self._title_surf: Optional[pygame.Surface] = None
        self._title_key = None
        self._hl_cache: dict = {}

    def activate(self):
        super().activate()
        year = self.sim.current_ace_year
        self._year = year
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        basin = bc if lm == 'basin' else None
        self._stats_data = calculate_season_stats(self.sim, year, basin)
        self._stats_hover_rects = []
        self._last_hover_rect = None
        self._content_surf = None
        self._content_key = None
        self._title_surf = None
        self._title_key = None

        w, h = min(920, self.sim.screen_width - 20), min(430, self.sim.screen_height - 60)
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)

    # ── 预渲染内容 ──

    def _build_groups(self):
        stats = self._stats_data

        def king(v, fmt):
            return fmt.format(v[0], v[1]) if v else "-"

        return [
            ("数量统计", [
                ("总系统数", f"{stats['total_systems']}", "统计所有系统,任何性质与强度"),
                ("总低压数", f"{stats['total_td']}", "达到TD及以上强度且性质达标"),
                ("总风暴数", f"{stats['total_ts']}", "达到TS及以上强度且性质达标"),
                ("总台风数", f"{stats['total_ty']}", "达到C1及以上强度且性质达标"),
                ("总MH数", f"{stats['total_mh']}", "达到C3及以上强度且性质达标"),
                ("总C5数", f"{stats['total_c5']}", "达到C5及以上强度且性质达标"),
            ]),
            ("极值记录", [
                ("风王", king(stats['wind_king'], "{0}: {1:.0f}kt"), "强度最高TC"),
                ("ACE王", king(stats['ace_king'], "{0}: {1:.4f}"), "ACE最高TC"),
                ("登陆王", king(stats['landfall_king'], "{0}: {1:.0f}kt"), "登陆强度最高TC"),
                ("寿命王", king(stats['lifetime_king'], "{0}: {1:.0f}h"), "TS及以上活跃时间最长的TC"),
            ]),
            ("累计数据", [
                ("总ACE", f"{stats['total_ace']:.4f}", "该洋区ACE加总"),
                ("总活跃时间", f"{stats['total_active_hours']:.1f}h", "TS及以上活跃时间加总"),
                ("风暴天", f"{stats['storm_days']:.2f}d", "每个活跃正式报+0.25天"),
                ("登陆次数", f"{stats['landfall_count']}", "总登陆次数"),
                ("总路径长度", f"{stats['total_path_km']:.0f}km", "TS及以上路径长度加总"),
            ]),
        ]

    def _build_content(self, dark, accent, tc, td, hairline):
        """把分组标题/标签/值/分隔线一次性渲染到缓存 Surface。"""
        box_w, box_h = self.bg_rect.width, self.bg_rect.height
        surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        col_w = (box_w - 40) // 3
        y_base = 58
        hover_rects = []

        for gi, (gname, items) in enumerate(self._build_groups()):
            gx = 20 + gi * col_w
            gh = rt(f_s, gname, accent)
            surf.blit(gh, (gx, y_base))
            line = pygame.Surface((col_w - 30, 1), pygame.SRCALPHA)
            line.fill(hairline)
            surf.blit(line, (gx, y_base + gh.get_height() + 3))

            y = y_base + gh.get_height() + 12
            for label, value, tooltip in items:
                label_surf = rt(f_s, f"{label}", td)
                value_surf = rt(f_s, value, tc, col_w - 110)
                row_rect = pygame.Rect(gx - 4, y - 2, col_w - 24,
                                       max(label_surf.get_height(), value_surf.get_height()) + 4)
                surf.blit(label_surf, (gx, y))
                surf.blit(value_surf, (gx + 96, y))
                hover_rects.append((row_rect, tooltip))
                y += max(26, label_surf.get_height() + 8)

        self._content_surf = surf
        self._stats_hover_rects = hover_rects

    # ── 绘制 ──

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dark = self.dark_mode
        accent = settings_accent(dark, getattr(self.sim, 'color_scheme', 1))
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        td = SETTINGS_TEXT_DIM if dark else (110, 120, 140)
        hairline = (255, 255, 255, 30) if dark else (20, 40, 80, 40)

        if dark:
            self.draw_dark_panel(surface, self.bg_rect)
        else:
            self.draw_background(surface, self.bg_rect)

        box_x, box_y = self.bg_rect.x, self.bg_rect.y
        box_w = self.bg_rect.width

        # 标题（缓存）
        title_key = (self._year, dark)
        if self._title_surf is None or self._title_key != title_key:
            lm = getattr(self.sim, 'ace_limit_mode', 'none')
            bc = getattr(self.sim, 'ace_limit_basin', '')
            if lm == 'basin' and bc:
                a = self.sim.res_mgr.ocean_areas.get_by_code(bc)
                bname = a.name_full if a else bc
                title_str = f"统计数据 — {self._year} {bname}"
            else:
                title_str = f"统计数据 — {self._year} 全球"
            self._title_surf = rt(f_m, title_str, tc)
            self._title_key = title_key
        surface.blit(self._title_surf, (box_x + 16, box_y + 10))
        th = self._title_surf.get_height()
        pygame.draw.line(surface, accent, (box_x + 16, box_y + 10 + th + 3),
                         (box_x + 16 + 28, box_y + 10 + th + 3), 2)

        # 按钮
        cb = pygame.Rect(box_x + box_w - 90, box_y + 8, 55, 25)
        self._close_btn_rect = cb
        sb = pygame.Rect(box_x + box_w - 210, box_y + 8, 110, 25)
        self._summary_btn_rect = sb
        if dark:
            self.draw_dark_button(surface, cb, "关闭")
            self.draw_dark_button(surface, sb, "总结条列表", accent=True)
        else:
            self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))
            self.draw_button(surface, sb, rt(f_s, "总结条列表", (255, 255, 255)))

        if not self._stats_data:
            no_data = rt(f_m, "无可用统计数据", tc)
            surface.blit(no_data, (box_x + box_w // 2 - no_data.get_width() // 2,
                                   box_y + 100))
            return

        # 内容（缓存）
        content_key = (dark, accent, box_w)
        if self._content_surf is None or self._content_key != content_key:
            self._build_content(dark, accent, tc, td, hairline)
            self._content_key = content_key
        surface.blit(self._content_surf, (box_x, box_y))

        # ── 悬停高亮 + 提示（移开即消失）──
        mouse_x, mouse_y = pygame.mouse.get_pos()
        hover_info = None
        hover_rect = None
        for rect_rel, tip in self._stats_hover_rects:
            rect = rect_rel.move(box_x, box_y)
            if rect.collidepoint(mouse_x, mouse_y):
                hover_rect = rect_rel
                hl_key = (rect.size, dark)
                hl = self._hl_cache.get(hl_key)
                if hl is None:
                    hl = pygame.Surface(rect.size, pygame.SRCALPHA)
                    hl.fill((255, 255, 255, 18) if dark else (70, 130, 180, 26))
                    self._hl_cache[hl_key] = hl
                    if len(self._hl_cache) > 8:
                        self._hl_cache.pop(next(iter(self._hl_cache)))
                surface.blit(hl, rect.topleft)
                now = pygame.time.get_ticks()
                if self._last_hover_rect != rect_rel:
                    self._stats_hover_start = now
                    self._last_hover_rect = rect_rel
                elif now - self._stats_hover_start > _HOVER_DELAY_MS:
                    hover_info = tip
                break
        if hover_rect is None:
            self._last_hover_rect = None

        if hover_info:
            draw_tooltip(surface, hover_info, (mouse_x, mouse_y),
                         self.sim.screen_width, dark=dark)

    # ── 事件 ──

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        # 先检查按钮点击（避免被 handle_drag_event 拦截）
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
            if self._summary_btn_rect.collidepoint(e.pos):
                self.sim.dialog_mgr.summary_list.activate()
                return True
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.bg_rect.collidepoint(e.pos):
                return True
        return False

    def deactivate(self):
        super().deactivate()
        self._stats_data = None
        self._stats_hover_rects = []
        self._last_hover_rect = None
