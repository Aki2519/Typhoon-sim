# py/statistics/path_heatmap.py
"""台风 ACE 热力图对话框 — 径向累积 + ace-heat.png 色彩映射。"""
from __future__ import annotations
import math
import os
import pygame
from PIL import Image as PILImage
from typing import Optional

from ..constants import (f_s, f_m, rt, TXT, DIALOG_TITLE_BAR_HEIGHT, SUCAI_DIR,
                         SETTINGS_TEXT_DIM, SETTINGS_TEXT_LIGHT)
from ..dialog_base import DraggableDialog
from .chart_helpers import render_map_subset


# ── ace-heat.png 色表加载 ──
_heat_img = None
_heat_lut: list = []
_HEAT_H = 0


def _load_heat():
    global _heat_img, _HEAT_H, _heat_lut
    if _heat_img is not None:
        return
    path = os.path.join(SUCAI_DIR, 'SMCY', 'resource', 'ace-heat.png')
    if os.path.exists(path):
        img = PILImage.open(path)
        _heat_img = img.convert('RGBA')
        _HEAT_H = _heat_img.height - 1
        _heat_lut = [_heat_img.getpixel((0, yy))[:4] for yy in range(_HEAT_H + 1)]


def _ace_heat_color(value: float) -> tuple:
    """从 ace-heat.png 采样颜色，value ∈ [0, 8]."""
    _load_heat()
    if _heat_img is None or _HEAT_H <= 0:
        # 降级色阶: 0 透明,其余按比例从红到绿平滑过渡(避免跳变)
        if value <= 0:
            return (0, 0, 0, 0)
        t = min(1.0, value * 0.125)
        return (int((1 - t) * 255), int(t * 255), 0, 255)
    v = max(0.0, min(value, 8.0))
    y = int((1.0 - v * 0.125) * _HEAT_H)
    return _heat_lut[y]


class PathHeatmapDialog(DraggableDialog):
    RANGE_AUTO = 0
    RANGE_SETTINGS = 1
    RANGE_CUSTOM = 2

    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._year: int = 0
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._range_mode = self.RANGE_AUTO
        self._custom_mlon = 0.0
        self._custom_Mlon = 360.0
        self._custom_mlat = -90.0
        self._custom_Mlat = 90.0
        self._custom_fields = []
        self._custom_field_active = -1
        self._legend_cache: dict = {}
        self._title_cache: Optional[tuple] = None

    def activate(self):
        super().activate()
        year = self.sim.current_ace_year
        self._year = year
        w, h = min(1800, self.sim.screen_width - 40), min(1100, self.sim.screen_height - 80)
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_surf = None
        self._custom_fields = []
        self._custom_field_active = -1

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
        if self._range_mode == self.RANGE_AUTO:
            margin = 5.0
            # 环形感知: 从最大经度缺口处切开,以缺口后首点为基准回卷,
            # 避免跨 0°/360° 时窗口被撕裂成近全球或坍缩(R2-4)
            srt = sorted(all_lons)
            n = len(srt)
            if n >= 2:
                gaps = [(srt[(i + 1) % n] - srt[i]) % 360.0 for i in range(n)]
                gi = max(range(n), key=lambda i: gaps[i])
                ref = srt[(gi + 1) % n]
            else:
                ref = srt[0]
            wrapped = [(lo - ref) % 360.0 for lo in all_lons]
            mlon = (ref + min(wrapped)) % 360.0 - margin
            Mlon = (ref + max(wrapped)) % 360.0 + margin
            mlat, Mlat = min(all_lats) - margin, max(all_lats) + margin
        elif self._range_mode == self.RANGE_SETTINGS:
            mlon, Mlon = self.sim.mlo, self.sim.Mlo
            mlat, Mlat = self.sim.mla, self.sim.Mla
        else:
            mlon, Mlon = self._custom_mlon, self._custom_Mlon
            mlat, Mlat = self._custom_mlat, self._custom_Mlat
        # K19: 经度归一到 0-360 制(负经度/跨 0 度线与底图切片一致)
        mlon = mlon % 360.0
        Mlon = Mlon % 360.0
        if Mlon <= mlon:
            Mlon += 360.0
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
        # 高度不超屏(避免关闭按钮被推出屏幕外)
        bh = min(bh, max(100, self.sim.screen_height - 160))

        new_h = bh + by + 80
        if new_h != self.bg_rect.height:
            self.bg_rect.height = new_h
            self.bg_rect.centery = self.sim.screen_height // 2

        w = self.bg_rect.width
        surf = pygame.Surface((w, new_h), pygame.SRCALPHA)

        # ── 底图 ──
        render_map_subset(surf, self.sim, mlon, Mlon, mlat, Mlat, bx, by, bw, bh)

        # ── Phase 3: 径向累积 ACE 热力(向量化) ──
        import numpy as np
        radius_px = 2.0 / (Mlat - mlat) * bh   # 2° 纬距 → 像素
        # K18: 核尺寸上限(π·r² 条元组),防止小纬度跨度+大 bh 时内存爆炸
        radius_px = min(radius_px, 120.0)
        radius_sq = radius_px * radius_px
        heat = np.zeros((bh, bw), dtype=np.float64)
        inv_lon = bw / (Mlon - mlon)
        inv_lat = bh / (Mlat - mlat)
        inv_r = 1.0 / radius_px

        # 法14+P3向量化: 径向核预计算为固定偏移/权重组,不再每像素 sqrt/边界算术;
        # 每点只需一次 numpy 切片累加(仅裁剪在窗口内的核元素),避免纯 Python 内层循环。
        r_int = int(radius_px) + 1
        k_ys = np.arange(-r_int, r_int + 1, dtype=np.int64)   # 显式整数偏移,避免 mgrid 负切片歧义
        k_xs = np.arange(-r_int, r_int + 1, dtype=np.int64)
        k_ys, k_xs = np.meshgrid(k_ys, k_xs, indexing='ij')
        k_ys0 = k_ys.ravel()
        k_xs0 = k_xs.ravel()
        rsq = k_xs0.astype(np.float64) ** 2 + k_ys0.astype(np.float64) ** 2
        kmask = rsq <= radius_sq                     # 圆形核掩码(与旧 `dx*dx+dy*dy <= radius_sq` 一致)
        k_ys = k_ys0[kmask]
        k_xs = k_xs0[kmask]
        k_w = 1.0 - inv_r * np.sqrt(rsq[kmask])      # 同旧 `1.0 - inv_r*sqrt(dx*dx+dy*dy)`

        for lon, lat, pace in ace_pts:
            # K19: 跨 0 度线窗口内数据点归一到窗口坐标系(取最近等价位置)
            d = (lon - mlon) % 360.0
            if d > (Mlon - mlon):
                d -= 360.0
            py = (Mlat - lat) * inv_lat
            # 转成整数目标坐标(与旧 `int(px)/int(py)` 结果一致;int 对负值截断向零,
            # 但核越界裁剪会丢弃,等价旧实现——保留 xi/yi 由 np.add.at 累加。)
            x0 = int(d * inv_lon)
            y0 = int(py)
            # 越界点完全在窗口外 → 核无贡献,直接跳过(仍与旧实现累加结果等价)
            xs_full = x0 + k_xs
            ys_full = y0 + k_ys
            inb = (xs_full >= 0) & (xs_full < bw) & (ys_full >= 0) & (ys_full < bh)
            if not inb.any():
                continue
            xv = xs_full[inb]
            yv = ys_full[inb]
            wv = k_w[inb] * pace
            np.add.at(heat, (yv, xv), wv)
        heat = heat.ravel()

        # ── Phase 4: 色彩映射到 Surface（numpy LUT 向量化，法14）──
        _load_heat()
        if _heat_img is None or _HEAT_H <= 0:
            heat_surf = pygame.Surface((bw, bh), pygame.SRCALPHA)
            px_array = pygame.PixelArray(heat_surf)
            for y in range(bh):
                row = y * bw
                for x in range(bw):
                    v = heat[row + x]
                    if v > 0:
                        c = pygame.Color(*_ace_heat_color(v))
                        px_array[x, y] = heat_surf.map_rgb(c)
            px_array.close()
        else:
            import numpy as np
            heat_arr = np.array(heat, dtype=np.float64).reshape(bh, bw)
            lut = np.array(_heat_lut, dtype=np.uint8)
            vq = np.clip(heat_arr * 0.125, 0.0, 1.0)
            idx = np.floor((1.0 - vq) * _HEAT_H).astype(np.int32)
            mask = heat_arr > 0
            idx = np.minimum(idx, _HEAT_H)
            rgba = np.zeros((bh, bw, 4), dtype=np.uint8)
            rgba[mask] = lut[idx[mask]]
            heat_surf = pygame.image.frombuffer(rgba.tobytes(), (bw, bh), 'RGBA')
        surf.blit(heat_surf, (bx, by))

        # ── 洋区矩形边框 ──
        rect_color = (100, 180, 255)
        pygame.draw.rect(surf, rect_color, (bx, by, bw, bh), 2)

        self._cached_surf = surf

    def _draw_legend(self, surface):
        """在左侧空白区域绘制颜色渐变图例（整体缓存为 Surface）。"""
        dark = self.dark_mode
        legend = self._legend_cache.get(dark)
        if legend is None:
            legend_w, legend_h = 24, 200
            tc = SETTINGS_TEXT_LIGHT if dark else TXT
            legend = pygame.Surface((legend_w + 40, legend_h + 30), pygame.SRCALPHA)
            title_lbl = rt(f_s, "ACE", tc)
            legend.blit(title_lbl, (0, 0))
            gy = 20
            for py in range(legend_h):
                t = 1.0 - py / legend_h
                rgba = _ace_heat_color(t * 8.0)
                pygame.draw.line(legend, pygame.Color(*rgba),
                                 (0, gy + py), (legend_w, gy + py))
            pygame.draw.rect(legend, tc, (0, gy, legend_w, legend_h), 1)
            for val_pct in (0, 0.25, 0.5, 0.75, 1.0):
                y = gy + int((1.0 - val_pct) * legend_h)
                lbl = rt(f_s, f"{val_pct * 8.0:.0f}", tc)
                legend.blit(lbl, (legend_w + 6, y - lbl.get_height() // 2))
            self._legend_cache[dark] = legend
        surface.blit(legend, (self.bg_rect.x + 20, self.bg_rect.y + 40))

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

        self._draw_legend(surface)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        title_key = (self._year, dark)
        if self._title_cache is None or self._title_cache[0] != title_key:
            self._title_cache = (title_key,
                                 rt(f_m, f"路径密度热力图 - {self._year}", tc))
        surface.blit(self._title_cache[1], (bx + 12, by + 8))

        # 右上角范围模式切换（记录按钮位置供 handle_event 检测）
        self._mode_btns = []
        mode_names = ["自动", "设置", "自定义"]
        for i, name in enumerate(mode_names):
            r = pygame.Rect(bx + bw - 360 + i * 85, by + 6, 75, 22)
            self._mode_btns.append(r)
            is_on = self._range_mode == i
            if not dark:
                bg = (100, 150, 200) if is_on else (180, 190, 210)
                btc = (255, 255, 255)
            elif is_on:
                bg = (70, 130, 180)
                btc = (20, 25, 35)
            else:
                bg = (50, 55, 70)
                btc = SETTINGS_TEXT_DIM
            pygame.draw.rect(surface, bg, r, border_radius=6)
            ts = rt(f_s, name, btc)
            surface.blit(ts, (r.x + (r.w - ts.get_width()) // 2, r.y + (r.h - ts.get_height()) // 2))

        # 关闭按钮
        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        if dark:
            self.draw_dark_button(surface, cb, "关闭")
        else:
            self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # 自定义范围输入
        if self._range_mode == self.RANGE_CUSTOM:
            if not self._custom_fields:
                self._ensure_custom_fields()
            cy = by + 34
            labels = ["西", "东", "南", "北"]
            for i, lbl in enumerate(labels):
                lx = bx + bw - 360 + i * 68
                surface.blit(rt(f_s, lbl, tc), (lx, cy))
            for f in self._custom_fields:
                f.draw(surface)

    def _ensure_custom_fields(self):
        from ..input_field import InputField
        self._custom_fields = []
        labels = ["西", "东", "南", "北"]
        vals = [self._custom_mlon, self._custom_Mlon, self._custom_mlat, self._custom_Mlat]
        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        for i, (lbl, val) in enumerate(zip(labels, vals)):
            lx = bx + bw - 360 + i * 68
            fr = pygame.Rect(lx + 16, by + 34, 46, 22)
            f = InputField(fr, max_length=8, dark=self.dark_mode)
            f.set_text(f"{val:.0f}")
            self._custom_fields.append(f)

    def _commit_custom_fields(self):
        try:
            west = float(self._custom_fields[0].get_text())
            east = float(self._custom_fields[1].get_text())
            south = float(self._custom_fields[2].get_text())
            north = float(self._custom_fields[3].get_text())
        except ValueError:
            self.sim.show_error("自定义范围必须为数字")
            for f in self._custom_fields:
                f.deactivate()
            self._ensure_custom_fields()
            return
        if not (-180 <= west <= 360 and -180 <= east <= 360):
            self.sim.show_error("经度范围 -180~360")
            for f in self._custom_fields:
                f.deactivate()
            self._ensure_custom_fields()
            return
        if not (-90 <= south <= 90 and -90 <= north <= 90):
            self.sim.show_error("纬度范围 -90~90")
            for f in self._custom_fields:
                f.deactivate()
            self._ensure_custom_fields()
            return
        self._custom_mlon, self._custom_Mlon = min(west, east), max(west, east)
        self._custom_mlat, self._custom_Mlat = min(south, north), max(south, north)
        for f in self._custom_fields:
            f.deactivate()
        self._cached_surf = None

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
            for i, r in enumerate(getattr(self, '_mode_btns', [])):
                if r.collidepoint(e.pos):
                    self._range_mode = i
                    self._cached_surf = None
                    if i != self.RANGE_CUSTOM:
                        for f in self._custom_fields:
                            f.deactivate()
                    return True
        if self._range_mode == self.RANGE_CUSTOM and self._custom_fields:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_RETURN:
                if any(f.active for f in self._custom_fields):
                    self._commit_custom_fields()
                    return True
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                # 点击字段: 交给字段激活(原实现先吞掉点击,字段永远无法激活)
                for f in self._custom_fields:
                    if f.rect.collidepoint(e.pos):
                        f.handle_event(e)
                        return True
                if any(f.active for f in self._custom_fields):
                    self._commit_custom_fields()
                    return True
            for f in self._custom_fields:
                if f.handle_event(e):
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
        for f in self._custom_fields:
            f.deactivate()      # 关闭时停用动态字段(IME 残留防护)
        self._custom_fields = []
