# py/statistics/intensity_comparison.py
"""多台风强度对比对话框：叠加多条强度曲线，x轴共用"距生成时间"。"""
from __future__ import annotations
import functools
import pygame
from datetime import datetime
from typing import List, Tuple, Optional

from ..constants import (
    f_s, f_l, rt, TXT,
    SETTINGS_DARK_BG, SETTINGS_TEXT_LIGHT,
)
from ..dialog_base import DraggableDialog
from .shared import COLORS, _THRESHOLDS, _FILL_BANDS
from .chart_helpers import (draw_dashed_h, draw_dashed_v,
                            draw_fill_bands, draw_tooltip, draw_vscrollbar)

_CHART_BG = (255, 255, 255, 235)
_CHART_BORDER = TXT
_GRID_COLOR = (150, 150, 170, 100)


@functools.lru_cache(maxsize=65536)
def _parse_pt_dt(t: str) -> Optional[datetime]:
    """点时间串 → datetime 的 lru 缓存包装(法22 同款)。

    解析结果与原有内联 `strptime(t[:10], \"%Y%m%d%H\")` 完全一致: 只取前 10 位
    到小时精度,分钟位(即使时间串为 12 位含 %M)不影响本对话的曲线时间轴与
    `%m/%d %HZ` 提示,故统一丢弃以保与现状相同。异常(含非 str)返回 None,
    由调用方按"沿用前点时间"策略处理。"""
    if not isinstance(t, str):
        return None
    try:
        return datetime.strptime(t[:10], "%Y%m%d%H")
    except (ValueError, IndexError, TypeError):
        return None


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
        self._scroll_y: int = 0
        self._content_h: int = 0

    # ═══════════════════════════════════════════════
    #  激活
    # ═══════════════════════════════════════════════
    def activate(self, year: int = 0, tys: list = None):
        super().activate()
        if not year:
            # 默认取当前 ACE 年,避免 year=0 时把所有缺失 ace_year 的历史点误纳入
            year = self.sim.current_ace_year
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
        dark = self.dark_mode
        self._built_dark = dark
        bg = SETTINGS_DARK_BG if dark else _CHART_BG
        border = (208, 216, 234) if dark else _CHART_BORDER
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        grid_y = (255, 255, 255, 60) if dark else _GRID_COLOR
        grid_x = (255, 255, 255, 55) if dark else (180, 190, 200, 120)

        w = min(1700, self.sim.screen_width - 20)
        content_h = min(820, self.sim.screen_height - 100)
        self._content_h = 820
        window_h = min(self._content_h, content_h)
        ox, oy = self.bg_rect.x, self.bg_rect.y
        # 保留用户拖拽后的位置(含部分拖出屏幕的负坐标),避免重建时跳回左上角
        self.bg_rect = pygame.Rect(ox, oy, w, window_h)
        max_scroll = max(0, self._content_h - window_h)
        self._scroll_y = max(0, min(self._scroll_y, max_scroll))

        ch_w = w - self._margin_l - self._margin_r
        ch_h = self._content_h - self._margin_t - self._margin_b
        chart_left = self._margin_l
        chart_top = self._margin_t

        surf = pygame.Surface((w, self._content_h), pygame.SRCALPHA)
        pygame.draw.rect(surf, bg, (0, 0, w, self._content_h), 0, 10)
        pygame.draw.rect(surf, border, (0, 0, w, self._content_h), 2, 10)

        # 标题
        title = rt(f_l, f"强度对比 — {self._year}", tc)
        surf.blit(title, ((w - title.get_width()) // 2, 12))

        # ── 收集全局 Y 范围 + 台风曲线数据(单趟过滤,法24)──
        engine = self.sim.ace_engine
        max_wind = 40
        global_max_hours = 1.0  # 所有台风中最长的持续时间
        typhoon_data = []
        for ty in self._tys:
            pts = [p for p in ty.pts
                   if p.get('ace_year', 0) == self._year
                   and engine.point_in_limit(p['la'], p['lo'])]
            if not pts:
                continue
            max_wind = max(max_wind, max(p['w'] for p in pts))
            pts_dt: List[datetime] = []
            for p in pts:
                t = p['t']
                parsed = _parse_pt_dt(t)
                if parsed is None:
                    # K32: 失败点沿用前点时间;首点失败则跳过该台风,
                    # 避免回退 2000 使 t_span 暴增挤压图表
                    if not pts_dt:
                        pts_dt = None
                        break
                    pts_dt.append(pts_dt[-1])
                else:
                    pts_dt.append(parsed)
            if pts_dt is None:
                continue
            t0 = pts_dt[0]
            hours = [(dt - t0).total_seconds() / 3600.0 for dt in pts_dt]
            if hours:
                global_max_hours = max(global_max_hours, hours[-1])
            typhoon_data.append((ty, pts, pts_dt, hours, t0))
        y_max = ((max_wind // 20) + 1) * 20 + 20

        # ── 强度填充带 ──
        draw_fill_bands(surf, _FILL_BANDS, chart_left, chart_top, ch_w, ch_h,
                        y_max, alpha=80 if dark else 65)

        # ── Y 轴虚线 + 标签 ──
        for yt in range(0, int(y_max) + 1, 20):
            rel = (yt - 0) / (y_max - 0)
            y_px = chart_top + ch_h - int(rel * ch_h)
            draw_dashed_h(surf, chart_left, chart_left + ch_w, y_px, grid_y, 4, 4)
            lbl = rt(f_s, f"{yt}", tc)
            surf.blit(lbl, (chart_left - lbl.get_width() - 8, y_px - lbl.get_height() // 2))

        # ── 阈值实线 ──
        for yt, color in _THRESHOLDS:
            if yt > y_max:
                continue
            rel = (yt - 0) / (y_max - 0)
            y_px = chart_top + ch_h - int(rel * ch_h)
            pygame.draw.line(surf, color, (chart_left, y_px), (chart_left + ch_w, y_px), 2)

        # ── 各台风曲线绘制 ──
        self._hover_rects = []

        # x 轴上限取整到最近的 24 小时(ceil 语义: 120h→120h,不再多出 24h 余量)
        global_max_hours = max(24, ((int(global_max_hours) + 23) // 24) * 24)
        x_label = "距生成时间"

        # ── X 轴虚线 + 标签（每 24 小时标注）──
        y_bottom = chart_top + ch_h
        for xh in range(0, int(global_max_hours) + 1, 24):
            rel = xh / global_max_hours
            x_px = chart_left + int(rel * ch_w)
            draw_dashed_v(surf, x_px, chart_top, chart_top + ch_h, grid_x, 4, 4)
            lbl = rt(f_s, f"{xh}h", tc)
            surf.blit(lbl, (x_px - lbl.get_width() // 2, y_bottom + 5))

        # X 轴标签
        x_axis_lbl = rt(f_s, x_label, tc)
        surf.blit(x_axis_lbl, (chart_left + ch_w // 2 - x_axis_lbl.get_width() // 2, y_bottom + 24))

        # ── 绘制曲线 ──
        for t_idx, (ty, pts, pts_dt, hours, t0) in enumerate(typhoon_data):
            color = COLORS[t_idx % len(COLORS)]
            name = self.sim.get_display_name(ty)

            point_px = []
            for i, pt in enumerate(pts):
                rel_t = hours[i] / global_max_hours
                rel_w = pt['w'] / (y_max - 0)
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
                        f"ACE={pts[i].get('ace', 0):.4f}")
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
            lbl = rt(f_s, name, tc)
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
        if not self.active:
            return
        if self._cached_surf is None or getattr(self, '_built_dark', None) != self.dark_mode:
            if not self._tys:
                return
            self._build()
        bx, by = self.bg_rect.x, self.bg_rect.y
        bw, bh = self.bg_rect.width, self.bg_rect.height
        src_rect = pygame.Rect(0, self._scroll_y, bw, bh)
        surface.blit(self._cached_surf, (bx, by), area=src_rect)

        # 滚动条
        draw_vscrollbar(surface, bx + bw - 8, by, bh,
                        self._content_h, bh, self._scroll_y, dark=self.dark_mode)

        # 关闭按钮
        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        if self.dark_mode:
            self.draw_dark_button(surface, cb, "关闭")
        else:
            self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # 悬停提示
        mx, my = pygame.mouse.get_pos()
        hover_rect = pygame.Rect(bx, by, bw, bh)
        if not hover_rect.collidepoint(mx, my):
            return
        ox, oy = bx, by - self._scroll_y
        for r_local, info in self._hover_rects:
            r_global = r_local.move(ox, oy)
            if r_global.collidepoint(mx, my):
                draw_tooltip(surface, info, (mx, my),
                             self.sim.screen_width, dark=self.dark_mode)
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
        if e.type == pygame.MOUSEMOTION:
            return False
        if e.type == pygame.MOUSEWHEEL:
            if self._content_h > self.bg_rect.height:
                max_scroll = self._content_h - self.bg_rect.height
                self._scroll_y = max(0, min(max_scroll, self._scroll_y - e.y * 30))
                return True
            return False
        return False
