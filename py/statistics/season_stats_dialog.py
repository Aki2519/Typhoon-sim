from __future__ import annotations

# py/statistics/season_stats_dialog.py
"""洋区统计数据独立对话框。"""
import pygame
from typing import Optional, Tuple, Dict

from ..constants import f_s, f_m, rt, TXT, BUTTON_BORDER, DIALOG_TITLE_BAR_HEIGHT
from ..dialog_base import DraggableDialog
from .season_stats import calculate_season_stats


class SeasonStatsDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._year: int = 0
        self._stats_data: Optional[Dict] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._hover_info: Optional[str] = None
        self._hover_pos: Optional[Tuple[int, int]] = None
        self._stats_hover_rects: list = []
        self._stats_hover_start: int = 0
        self._last_hover_rect = None

    def activate(self):
        super().activate()
        year = self.sim.current_ace_year
        self._year = year
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        basin = bc if lm == 'basin' else None
        self._stats_data = calculate_season_stats(self.sim, year, basin)
        self._stats_hover_rects = []

        w, h = min(920, self.sim.screen_width - 20), min(420, self.sim.screen_height - 60)
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width

        # 标题
        lm = getattr(self.sim, 'ace_limit_mode', 'none')
        bc = getattr(self.sim, 'ace_limit_basin', '')
        if lm == 'basin' and bc:
            a = self.sim.res_mgr.ocean_areas.get_by_code(bc)
            bname = a.name_full if a else bc
            title_str = f"统计数据 — {self._year} {bname}"
        else:
            title_str = f"统计数据 — {self._year} 全球"
        title = rt(f_m, title_str, TXT)
        surface.blit(title, (bx + 12, by + 8))

        # 关闭按钮
        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        if not self._stats_data:
            no_data = rt(f_m, "无可用统计数据", TXT)
            surface.blit(no_data, (bx + bw // 2 - no_data.get_width() // 2,
                                   by + 100))
            return

        stats = self._stats_data

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
        col_width = 290
        start_x = bx + 20
        y_base = by + 55

        self._stats_hover_rects = []
        for i, (label, value, tooltip) in enumerate(labels):
            col = i // rows_per_col
            row = i % rows_per_col
            x = start_x + col * col_width
            y = y_base + row * 23

            line = rt(f_s, f"{label}: {value}", TXT)
            surface.blit(line, (x, y))

            rect = pygame.Rect(x, y, line.get_width(), line.get_height())
            self._stats_hover_rects.append((rect, tooltip))

        # 悬停检测
        mx, my = pygame.mouse.get_pos()
        for rect, tip in self._stats_hover_rects:
            if rect and rect.collidepoint(mx, my):
                now = pygame.time.get_ticks()
                if self._last_hover_rect != rect:
                    self._stats_hover_start = now
                    self._last_hover_rect = rect
                elif now - self._stats_hover_start > 2000:
                    self._hover_info = tip
                    self._hover_pos = (mx + 15, my - 25)
                break
        else:
            self._last_hover_rect = None

        # 绘制悬停提示
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

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        # 先检查按钮点击（避免被 handle_drag_event 拦截）
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
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
        self._hover_info = None
        self._stats_hover_rects = []
