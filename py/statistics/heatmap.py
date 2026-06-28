# py/statistics/path_heatmap.py
"""台风 ACE 热力图对话框 — 径向累积 + 线性色彩渐变。"""
from __future__ import annotations
import math
import pygame
from typing import Optional

from ..constants import f_s, f_m, rt, TXT, DIALOG_TITLE_BAR_HEIGHT
from ..dialog_base import DraggableDialog


# ── 色彩渐变停靠点 (value, (R, G, B, A)) ──
_COLOR_STOPS = [
    (0.0,  (0,   0,   0,   0)),
    (0.75, (255, 255, 255, 255)),
    (2.0,  (255, 255, 0,   255)),
    (5.0,  (255, 0,   0,   255)),
    (8.0,  (128, 0,   128, 255)),
    (20.0, (0,   0,   0,   255)),
]


def _value_to_rgba(value: float) -> tuple:
    """将 ACE 热力值映射为 (R, G, B, A)。"""

    if value <= 0:
        return (0, 0, 0, 0)
    if value >= 20.0:
        return (0, 0, 0, 255)
    for i in range(len(_COLOR_STOPS) - 1):
        v0, c0 = _COLOR_STOPS[i]
        v1, c1 = _COLOR_STOPS[i + 1]
        if v0 <= value <= v1:
            t = (value - v0) / (v1 - v0)
            return tuple(int(c0[j] + (c1[j] - c0[j]) * t) for j in range(4))
    return (0, 0, 0, 0)


class PathHeatmapDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._year: int = 0
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)

    def activate(self):
        super().activate()
        year = self.sim.current_ace_year
        self._year = year
        w, h = min(1800, self.sim.screen_width - 40), min(1100, self.sim.screen_height - 80)
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_surf = None

    def _render(self):
        if self._cached_surf is not None:
            return

        # ── Phase 1: 收集 ACE 报点 (lon, lat, pace) ──
        engine = self.sim.ace_engine
        ace_pts = []          # [(lon, lat, pace), ...]
        all_lons, all_lats = [], []
        for ty in self.sim.tys:
            for p in ty.pts:
                if p.get('ace_year', 0) != self._year:
                    continue
                if not engine.point_in_limit(p['la'], p['lo']):
                    continue
                pace = p.get('pace', 0.0)
                if pace <= 0:
                    continue
                ace_pts.append((p['lo'], p['la'], pace))
                all_lons.append(p['lo'])
                all_lats.append(p['la'])

        if not all_lons:
            w, h = self.bg_rect.width, self.bg_rect.height
            self._cached_surf = pygame.Surface((w, h), pygame.SRCALPHA)
            return

        # ── 地理边界 ──
        margin = 5.0
        mlon, Mlon = min(all_lons) - margin, max(all_lons) + margin
        mlat, Mlat = min(all_lats) - margin, max(all_lats) + margin
        if Mlon - mlon < 2:
            Mlon = mlon + 2
        if Mlat - mlat < 2:
            Mlat = mlat + 2

        # ── Phase 2: 按地理宽高比动态调整高度 ──
        bx, by = 80, 50
        bw = self.bg_rect.width - 160
        geo_ratio = (Mlon - mlon) / max(0.1, Mlat - mlat)
        bh = int(bw / geo_ratio)
        bh = max(100, min(2000, bh))

        new_h = bh + by + 80
        if new_h != self.bg_rect.height:
            self.bg_rect.height = new_h
            self.bg_rect.centery = self.sim.screen_height // 2

        w = self.bg_rect.width
        surf = pygame.Surface((w, new_h), pygame.SRCALPHA)

        # ── 底图 ──
        try:
            orig = self.sim.map_mgr.map_view.original_img
            iw, ih = orig.get_size()
            ix1 = int((mlon - 0) / 360 * iw)
            ix2 = int((Mlon - 0) / 360 * iw)
            iy1 = int((90 - Mlat) / 180 * ih)
            iy2 = int((90 - mlat) / 180 * ih)
            ix1, ix2 = max(0, min(ix1, iw)), max(0, min(ix2, iw))
            iy1, iy2 = max(0, min(iy1, ih)), max(0, min(iy2, ih))
            if ix2 > ix1 and iy2 > iy1:
                sub = orig.subsurface(pygame.Rect(ix1, iy1, ix2 - ix1, iy2 - iy1))
                scaled = pygame.transform.smoothscale(sub, (bw, bh))
                scaled.set_alpha(200)
                surf.blit(scaled, (bx, by))
        except Exception:
            pass

        # ── Phase 3: 径向累积 ACE 热力 ──
        radius_px = 2.0 / (Mlat - mlat) * bh   # 2° 纬距 → 像素
        heat = [0.0] * (bw * bh)

        for lon, lat, pace in ace_pts:
            px = (lon - mlon) / (Mlon - mlon) * bw
            py = (Mlat - lat) / (Mlat - mlat) * bh
            r_int = int(radius_px) + 1
            x0 = max(0, int(px - r_int))
            y0 = max(0, int(py - r_int))
            x1 = min(bw, int(px + r_int) + 1)
            y1 = min(bh, int(py + r_int) + 1)
            for y in range(y0, y1):
                dy = y - py
                row_off = y * bw
                for x in range(x0, x1):
                    dx = x - px
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist <= radius_px:
                        heat[row_off + x] += pace * (1.0 - dist / radius_px)

        # ── Phase 4: 色彩映射到 Surface ──
        heat_surf = pygame.Surface((bw, bh), pygame.SRCALPHA)
        px_array = pygame.PixelArray(heat_surf)
        for y in range(bh):
            row = y * bw
            for x in range(bw):
                v = heat[row + x]
                if v > 0:
                    c = pygame.Color(*_value_to_rgba(v))
                    px_array[x, y] = heat_surf.map_rgb(c)
        px_array.close()
        surf.blit(heat_surf, (bx, by))

        # ── 洋区矩形边框 ──
        rect_color = (100, 180, 255)
        pygame.draw.rect(surf, rect_color, (bx, by, bw, bh), 2)

        self._cached_surf = surf

    def _draw_legend(self, surface):
        """在左侧空白区域绘制颜色渐变图例"""
        bx, by = self.bg_rect.x, self.bg_rect.y
        # 图例放在左侧边距区
        legend_x = bx + 20
        legend_y = by + 60
        legend_w = 24
        legend_h = 200

        # 渐变条
        for py in range(legend_h):
            t = 1.0 - py / legend_h  # 顶部=高值, 底部=低值
            value = t * 20.0  # 0..20
            rgba = _value_to_rgba(value)
            color = pygame.Color(*rgba)
            pygame.draw.line(surface, color,
                           (legend_x, legend_y + py),
                           (legend_x + legend_w, legend_y + py))

        pygame.draw.rect(surface, TXT,
                        (legend_x, legend_y, legend_w, legend_h), 1)
        # 标签
        for val_pct in [0, 0.25, 0.5, 0.75, 1.0]:
            y = legend_y + int((1.0 - val_pct) * legend_h)
            value = val_pct * 20.0
            lbl = rt(f_s, f"{value:.0f}", TXT)
            surface.blit(lbl, (legend_x + legend_w + 6, y - lbl.get_height() // 2))
        # 标题
        title_lbl = rt(f_s, "ACE", TXT)
        surface.blit(title_lbl, (legend_x, legend_y - 20))

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)
        self._render()
        if self._cached_surf:
            surface.blit(self._cached_surf, self.bg_rect.topleft)

        self._draw_legend(surface)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        title = rt(f_m, f"路径密度热力图 - {self._year}", TXT)
        surface.blit(title, (bx + 12, by + 8))

        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        return False

    def deactivate(self):
        super().deactivate()
        self._cached_surf = None
