from __future__ import annotations

import os
import math
import logging
from typing import Optional

import pygame

from .constants import DEFAULT_MAP, LAND_MASK

logger = logging.getLogger(__name__)


class MapView:
    def __init__(self, img_path, lon_min, lon_max, lat_min, lat_max, screen_width, screen_height):
        self.original_img = pygame.image.load(img_path).convert()
        self.img_w, self.img_h = self.original_img.get_size()
        self.lon_min, self.lon_max = lon_min, lon_max
        self.lat_min, self.lat_max = lat_min, lat_max
        self.width_deg = lon_max - lon_min
        self.height_deg = lat_max - lat_min
        self._scale_x = self.img_w / self.width_deg
        self._scale_y = self.img_h / self.height_deg
        self._deg_per_px_x = self.width_deg / self.img_w
        self._deg_per_px_y = self.height_deg / self.img_h
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.view_x = self.view_y = 0.0
        self.scale = 1.0
        # cover 模式：最小缩放取纵横比较大者，地图始终铺满屏幕（上下/左右不露灰边）
        self.min_scale = max(screen_width / self.img_w, screen_height / self.img_h)
        self._cached_scale = -1.0
        self._cached_sw = -1
        self._cached_sh = -1
        self._cached_ba = False
        self._cached_offset = (0, 0)
        self._cached_extras = (0.0, 0.0)
        self._bottom_align = False

    @property
    def _max_view_y(self):
        return max(0.0, self.img_h - self.screen_height / self.scale)

    def _clamp_view_y(self):
        self.view_y = max(0.0, min(self.view_y, self._max_view_y))

    def _src_y(self, view_h):
        return max(0.0, min(self.view_y, self.img_h - view_h))

    def geo_to_screen(self, lon, lat):
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        ox, oy = self._draw_offset()
        # 两段式取整(与地图 blit 同源): int(坐标*scale) - int(视图*scale)。
        # 若直接取整整段差值 int((坐标-视图)*scale), 平移时与地图的舍入相位不同,
        # 路径/台风相对底图会有 ±1px 抖动式偏移。
        vx = int(self.view_x * self.scale)
        vy = int(self.view_y * self.scale)
        x = int(px * self.scale) - vx + ox
        y = int(py * self.scale) - vy + oy
        # 环绕: 距屏幕中心超过半个图宽则按图宽像素翻面(取离屏幕中心最近的副本)。
        # 阈值必须是半宽: 用整宽时跨 0°/360° 缝的点会停在翻面错误的一侧, 表现为路径点消失。
        wrap = max(1, int(self.img_w * self.scale))
        half = wrap / 2.0
        if x > self.screen_width / 2.0 + half:
            x -= wrap
        elif x < self.screen_width / 2.0 - half:
            x += wrap
        return x, y

    def screen_to_geo(self, sx, sy):
        ox, oy = self._draw_offset()
        sx, sy = sx - ox, sy - oy
        px = self.view_x + sx / self.scale
        py = self.view_y + sy / self.scale
        lon = self.lon_min + px * self._deg_per_px_x
        lon = ((lon - self.lon_min) % self.width_deg) + self.lon_min
        lat = max(self.lat_min, min(self.lat_max,
                                    self.lat_max - py * self._deg_per_px_y))
        return lon, lat

    def move_view(self, dx, dy):
        old_vx, old_vy = self.view_x, self.view_y
        new_vx = self.view_x - dx / self.scale
        new_vy = self.view_y - dy / self.scale
        self.view_x = new_vx % self.img_w
        self.view_y = new_vy
        self._clamp_view_y()
        # 返回未取模的真实位移: 屏幕内容确实位移 (new-old)*scale 像素;
        # 若对取模后的差再取"最短方向", 单次位移 ≥ 半个世界时符号会翻转(拖拽反向跳)
        return (new_vx - old_vx) * self.scale, (self.view_y - old_vy) * self.scale

    def zoom_at(self, factor, mx, my):
        ox, oy = self._draw_offset()
        mx, my = mx - ox, my - oy
        old = self.scale
        self.scale = max(self.min_scale, min(self.scale * factor, 8.0))
        self.view_x = (self.view_x + mx / old) - mx / self.scale
        self.view_y = (self.view_y + my / old) - my / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()

    def center_on_latlon(self, lat: float, lon: float) -> None:
        """把地图平移到指定经纬度居中(保持当前缩放,视图可横向环绕)。"""
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        self.view_x = (px - self.screen_width / (2.0 * self.scale)) % self.img_w
        self.view_y = py - self.screen_height / (2.0 * self.scale)
        self._clamp_view_y()
        self._bottom_align = False

    def set_view_region(self, lon_min, lon_max, lat_min, lat_max):
        if lon_max < lon_min: lon_max += 360
        clon = (lon_min + lon_max) / 2.0
        clat = (lat_min + lat_max) / 2.0
        lon_span = lon_max - lon_min
        lat_span = lat_max - lat_min
        # 防御: 零跨度(mlo==Mlo 或 mla==Mla,可来自外部脚本)会除零,保持当前缩放并居中
        if lon_span <= 0 or lat_span <= 0:
            self.view_x = ((clon - self.lon_min) * self._scale_x
                           - self.screen_width / (2.0 * self.scale)) % self.img_w
            self.view_y = (self.lat_max - clat) * self._scale_y \
                          - self.screen_height / (2.0 * self.scale)
            self._clamp_view_y()
            self._bottom_align = False
            return
        self.scale = max(self.min_scale, min(
            self.screen_width / (lon_span * self._scale_x),
            self.screen_height / (lat_span * self._scale_y), 8.0))
        self.view_x = ((clon - self.lon_min) * self._scale_x
                       - self.screen_width / (2.0 * self.scale)) % self.img_w
        self.view_y = (self.lat_max - clat) * self._scale_y \
                      - self.screen_height / (2.0 * self.scale)
        self._clamp_view_y()
        self._bottom_align = False

    def set_view_corner(self, lon_bl, lat_bl, span_lon):
        if span_lon <= 0:
            return
        scale_x = self.screen_width / (span_lon * self._scale_x)
        self.scale = max(self.min_scale, min(scale_x, 8.0))
        px_bl = (lon_bl - self.lon_min) * self._scale_x
        self.view_x = px_bl % self.img_w
        py_bl = (self.lat_max - lat_bl) * self._scale_y
        self.view_y = py_bl - self.screen_height / self.scale
        self._clamp_view_y()
        self._bottom_align = True

    def _draw_offset(self):
        if (self._cached_scale != self.scale or self._cached_sw != self.screen_width
                or self._cached_sh != self.screen_height or self._cached_ba != self._bottom_align):
            vw = min(self.screen_width / self.scale, self.img_w)
            vh = min(self.screen_height / self.scale, self.img_h)
            self._cached_offset = (
                int((self.screen_width - vw * self.scale) / 2),
                int(self.screen_height - vh * self.scale) if self._bottom_align
                else int((self.screen_height - vh * self.scale) / 2),
            )
            self._cached_extras = (self.screen_width / (2.0 * self.scale), self.img_w / 2.0)
            self._cached_scale = self.scale
            self._cached_sw = self.screen_width
            self._cached_sh = self.screen_height
            self._cached_ba = self._bottom_align
        return self._cached_offset

    def _pan_cache(self):
        """全图尺寸可控时缓存整图渲染；纯平移只按偏移 blit，scale 变化才重建。
        key 含精确 scale：脚本逐帧微缩放时避免旧缩放内容与新偏移错位（B14）。"""
        w = int(math.ceil(self.img_w * self.scale))
        h = int(math.ceil(self.img_h * self.scale))
        if w > self.screen_width * 2 or h > self.screen_height * 2:
            return None
        key = (self.scale, self.screen_width, self.screen_height)
        if getattr(self, '_pan_cache_key', None) != key:
            surf = pygame.Surface((w, h))
            old_vx, old_vy = self.view_x, self.view_y
            self.view_x = 0.0
            self.view_y = 0.0
            self._render_mip_window(surf, surf.get_rect(), 0, 0)
            self.view_x, self.view_y = old_vx, old_vy
            self._pan_cache_surf = surf
            self._pan_cache_key = key
        return self._pan_cache_surf

    _PAN_WINDOW_MARGIN = 512   # 窗口缓存四周留出的平移余量(屏幕 px)

    def _window_cache(self):
        """放大后整图缓存放不下时, 缓存"当前视野 + 四周余量"的窗口。

        跟踪/拖动时视图每帧都在变, 若每帧都从原图 transform.scale(实测 ~4ms/帧),
        60fps 下会吃掉四分之一帧预算; 窗口缓存把重建摊到每 ~512px 平移一次。
        跨 0°/360° 缝或余量不足的窗口直接放弃缓存(交回逐帧缩放), 避免拼接取整误差。"""
        sw, sh = self.screen_width, self.screen_height
        # 缓存按 ox=oy=0 渲染: 若地图未能铺满屏幕(ox/oy 非零)则放弃缓存,
        # 避免缓存与屏幕偏移叠加错位
        if self._draw_offset() != (0, 0):
            return None
        # 快速拖动(单帧位移 > 120px)时放弃缓存: 窗口重建跟不上, 每帧重建反而
        # 比逐帧缩放慢(实测 600px/帧: 30.6ms vs 11.3ms); 停下后自动恢复缓存
        prev = getattr(self, '_win_prev_view', None)
        self._win_prev_view = (self.view_x, self.view_y)
        if prev is not None:
            ddx = (self.view_x - prev[0] + self.img_w / 2.0) % self.img_w - self.img_w / 2.0
            ddy = self.view_y - prev[1]
            if abs(ddx) * self.scale > 120.0 or abs(ddy) * self.scale > 120.0:
                return None
        map_w = int(math.ceil(self.img_w * self.scale))
        map_h = int(math.ceil(self.img_h * self.scale))
        cw = min(sw + 2 * self._PAN_WINDOW_MARGIN, map_w)
        ch = min(sh + 2 * self._PAN_WINDOW_MARGIN, map_h)
        if cw < sw or ch < sh:
            return None
        vw = cw / self.scale
        vh = ch / self.scale
        if vw > self.img_w + 1e-6 or vh > self.img_h + 1e-6:
            return None
        sx = self.view_x % self.img_w
        sy = self._src_y(min(sh / self.scale, self.img_h))
        step = self._PAN_WINDOW_MARGIN / self.scale
        # 量化到整数源像素: 消除缩放相位漂移(非整数原点会让缓存与逐帧渲染
        # 相差不到 1px 的分数像素, 重建时出现微跳), 同时让 key 稳定、缓存可命中
        vx0 = float(math.floor(min(
            math.floor(max(0.0, sx - step) / step) * step,
            max(0.0, self.img_w - vw))))
        vy0 = float(math.floor(min(
            math.floor(max(0.0, sy - step) / step) * step,
            max(0.0, self.img_h - vh))))
        # 当前屏幕必须完整落在窗口内(窗口不跨缝), 否则本帧退回逐帧缩放
        if (sx < vx0 - 1e-9 or sx + sw / self.scale > vx0 + vw + 1e-6
                or sy < vy0 - 1e-9 or sy + sh / self.scale > vy0 + vh + 1e-6):
            return None
        key = (round(self.scale, 6), sw, sh, round(vx0, 3), round(vy0, 3),
               cw, ch, getattr(self, '_bottom_align', False))
        if getattr(self, '_win_cache_key', None) != key:
            surf = pygame.Surface((cw, ch))
            old_vx, old_vy = self.view_x, self.view_y
            self.view_x, self.view_y = vx0, vy0
            try:
                self._render_mip_window(surf, surf.get_rect(), 0, 0)
            finally:
                self.view_x, self.view_y = old_vx, old_vy
            self._win_cache_surf = surf
            self._win_cache_key = key
        return self._win_cache_surf, vx0, vy0

    def _render_mip_window(self, screen, dest_rect, ox=None, oy=None):
        vw = min(dest_rect.width / self.scale, self.img_w)
        vh = min(dest_rect.height / self.scale, self.img_h)
        sx, sy = self.view_x % self.img_w, self._src_y(vh)
        if ox is None or oy is None:
            ox, oy = self._draw_offset()
        img = self.original_img
        rw = self.img_w - sx

        x_off = 0
        for seg_x, seg_w in ([(sx, min(rw, vw))] if rw >= vw
                             else [(sx, rw), (0, vw - rw)]):
            if seg_w <= 0:
                continue
            rect = pygame.Rect(int(seg_x), int(sy),
                                int(math.ceil(seg_w)), int(math.ceil(vh)))
            rect = rect.clip(img.get_rect())
            if rect.width <= 0 or rect.height <= 0:
                continue
            try:
                scaled = pygame.transform.scale(img.subsurface(rect),
                    (max(1, int(math.ceil(seg_w * self.scale))),
                     max(1, int(math.ceil(vh * self.scale)))))
                screen.blit(scaled, (dest_rect.left + ox + x_off, dest_rect.top + oy))
                x_off += scaled.get_width()
            except ValueError:
                pass

    def draw(self, screen, dest_rect=None):
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)

        cache = self._pan_cache()
        if cache is not None:
            # 纯平移：缓存整图按滚动偏移 blit（含 wrap），不做任何缩放
            screen.fill((120, 120, 120), dest_rect)
            sx = self.view_x % self.img_w
            vh = min(dest_rect.height / self.scale, self.img_h)
            sy = self._src_y(vh)
            ox, oy = self._draw_offset()
            cw, ch = cache.get_size()
            dx = ox - int(sx * self.scale)
            dy = oy - int(sy * self.scale)
            screen.blit(cache, (dest_rect.left + dx, dest_rect.top + dy))
            if dx + cw < dest_rect.width:
                screen.blit(cache, (dest_rect.left + dx + cw, dest_rect.top + dy))
            return
        win = self._window_cache()
        if win is not None:
            # 放大态: 窗口缓存按偏移 blit, 重建只在平移出余量时发生。
            # 只贴可见子矩形(整窗 9M px 全贴比贴屏幕 3.8M px 慢一倍多),
            # 且缓存必然铺满屏幕, 可省掉每帧全屏 fill
            surf, vx0, vy0 = win
            sx = self.view_x % self.img_w
            sy = self._src_y(min(dest_rect.height / self.scale, self.img_h))
            dx = int(vx0 * self.scale) - int(sx * self.scale)
            dy = int(vy0 * self.scale) - int(sy * self.scale)
            area = pygame.Rect(-dx, -dy, dest_rect.width, dest_rect.height)
            area = area.clip(surf.get_rect())
            if area.width >= dest_rect.width and area.height >= dest_rect.height:
                screen.blit(surf, (dest_rect.left + dx + area.x,
                                   dest_rect.top + dy + area.y), area)
                return
        screen.fill((120, 120, 120), dest_rect)
        self._render_mip_window(screen, dest_rect)


class MapManager:
    def __init__(self, sim):
        self.sim = sim
        self.map_view: Optional[MapView] = None
        self.land_img: Optional[pygame.Surface] = None
        self._land_alpha_bytes: Optional[bytes] = None
        self._land_w: int = 0
        self.ocean_overlay: Optional[pygame.Surface] = None
        self.cmp: Optional[str] = None
        self._land_orig: Optional[pygame.Surface] = None
        self._cached_map_render: Optional[pygame.Surface] = None
        self._cached_render_hash = None
        self._land_pending: bool = False
        self._land_due: int = 0
        self._land_lazy: bool = False
        self._land_geo_alpha: Optional[bytes] = None
        self._land_geo_w: int = 0
        self._land_geo_h: int = 0

    def _view_hash(self):
        v = self.map_view
        if v is None:
            return None
        return (int(v.view_x * 100), int(v.view_y * 100),
                v.scale, self.sim.screen_width, self.sim.map_height,
                getattr(v, '_bottom_align', False))   # K30: 角落模式底部对齐变化时缓存失效

    def _init_map_view(self):
        path = self.cmp if self.cmp and os.path.exists(self.cmp) else DEFAULT_MAP
        self.map_view = MapView(path, 0.0, 360.0, -90.0, 90.0,
                                self.sim.screen_width, self.sim.map_height)
        self._fit_view()

    def _fit_view(self):
        if getattr(self.sim, 'map_corner_mode', False):
            span_lon = self.sim.Mlo - self.sim.mlo
            self.map_view.set_view_corner(self.sim.mlo, self.sim.mla, span_lon)
        else:
            self.map_view.set_view_region(self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla)

    def _load_land_orig(self):
        if self._land_orig is None and os.path.exists(LAND_MASK):
            try:
                self._land_orig = pygame.image.load(LAND_MASK).convert_alpha()
            except Exception as e:
                logger.error(f"加载陆地掩码失败: {e}")
        return self._land_orig

    def _rebuild_land_and_overlay(self):
        land = self._load_land_orig()
        if land is None or self.map_view is None:
            self.land_img = None
        else:
            view = self.map_view
            vw = min(self.sim.screen_width / view.scale, view.img_w)
            vh = min(self.sim.map_height / view.scale, view.img_h)
            sx, sy = view.view_x % view.img_w, max(0.0, min(view.view_y, view.img_h - vh))
            ox, oy = view._draw_offset()
            lw, lh = land.get_size()
            scx, scy = lw / view.img_w, lh / view.img_h

            self.land_img = pygame.Surface(
                (self.sim.screen_width, self.sim.map_height), pygame.SRCALPHA)

            for lsx, lw_seg in ([(sx, min(view.img_w - sx, vw))]
                                if view.img_w - sx >= vw
                                else [(sx, view.img_w - sx), (0, vw - (view.img_w - sx))]):
                if lw_seg <= 0: continue
                rect = pygame.Rect(int(lsx * scx), int(sy * scy),
                                   int(math.ceil(lw_seg * scx)), int(math.ceil(vh * scy)))
                rect = rect.clip(land.get_rect())
                if rect.width <= 0 or rect.height <= 0: continue
                scaled = pygame.transform.scale(
                    land.subsurface(rect),
                    (max(1, int(lw_seg * view.scale)), max(1, int(vh * view.scale))))
                dst_x = 0 if lsx == sx else int((view.img_w - sx) * view.scale)
                self.land_img.blit(scaled, (ox + dst_x, oy))

        self.ocean_overlay = None

        if self.land_img is not None:
            raw = pygame.image.tobytes(self.land_img, 'RGBA')
            self._land_alpha_bytes = raw[3::4]
            self._land_w = self.land_img.get_width()
        else:
            self._land_alpha_bytes = None
            self._land_w = 0

    _LAND_REBUILD_DELAY_MS = 180   # 视图连续变化(缩放/拖拽)期间推迟陆地掩码重建

    def update_land_mask(self):
        if self.map_view is None:
            return
        now = pygame.time.get_ticks()
        vh = self._view_hash()
        if vh == self._cached_render_hash:
            # 视图稳定后补做被推迟的陆地掩码重建（仅当掩码已构建过且被标记 pending）
            if self._land_pending and not self._land_lazy and now >= self._land_due:
                self._rebuild_land_and_overlay()
                self._land_pending = False
            return
        # 地图渲染必须立即更新（视觉）；
        # 陆地掩码非视觉（仅登陆检测用），懒加载——只有实际用到 is_land_at_*
        # 时才重建（首次调用触发），启动时不需要掩码，省下 land.png 加载。
        if self._land_orig is None and not os.path.exists(LAND_MASK):
            # 无掩码文件：整条路径跳过，避免每个移动事件都重建
            self._land_alpha_bytes = None
            self._land_pending = False
            self._land_lazy = False
        else:
            # 掩码懒加载：标记 lazy, 由 _ensure_land_mask 首次 is_land 时重建
            self._land_lazy = True
            self._land_pending = False
        w, h = self.sim.screen_width, self.sim.map_height
        if (self._cached_map_render is None
                or self._cached_map_render.get_size() != (w, h)):
            self._cached_map_render = pygame.Surface((w, h))
        self.map_view.draw(self._cached_map_render, pygame.Rect(0, 0, w, h))
        self._cached_render_hash = vh

    def _ensure_land_mask(self) -> None:
        """确保陆地掩码已构建（懒加载）。由 is_land_at_screen/geo 首次调用触发，
        仅一次；掩码加载失败或文件不存在时置空并跳过。"""
        if self._land_alpha_bytes is not None:
            return
        if self._land_orig is None and not os.path.exists(LAND_MASK):
            return
        if self._land_orig is None:
            self._land_orig = self._load_land_orig()
        if self._land_orig is None:
            self._land_alpha_bytes = None
            self._land_w = 0
            self._land_lazy = False
            self._land_pending = False
            return
        self._rebuild_land_and_overlay()
        self._land_lazy = False
        self._land_pending = False

    def is_land_at_screen(self, sx, sy):
        self._ensure_land_mask()
        ab = self._land_alpha_bytes
        if ab is None:
            return False
        w = self._land_w
        if 0 <= sx < w and 0 <= sy < len(ab) // w:
            return ab[sy * w + sx] > 0
        return False

    def is_land_at_geo(self, la, lo):
        """按经纬度直接采样原始陆地掩码（与视图无关）。
        首次调用时把原图 alpha 通道转为 bytes 查表，避免每次 get_at 锁表面。"""
        land = self._load_land_orig()
        if land is None:
            return False
        ab = self._land_geo_alpha
        if ab is None:
            ab = pygame.image.tobytes(land, 'RGBA')[3::4]
            self._land_geo_alpha = ab
            self._land_geo_w, self._land_geo_h = land.get_size()
            self._land_geo_sx = self._land_geo_w / 360.0
            self._land_geo_sy = self._land_geo_h / 180.0
        lw, lh = self._land_geo_w, self._land_geo_h
        x = int((lo % 360.0) * self._land_geo_sx) % lw
        y = min(lh - 1, max(0, int((90.0 - la) * self._land_geo_sy)))
        if 0 <= y < lh:
            return ab[y * lw + x] > 0
        return False

    def update_view(self):
        if self.map_view is None:
            self._init_map_view()
        else:
            self._fit_view()
        self.update_land_mask()

    update_map_image = update_view

    def draw_map(self, surface, dest_rect=None):
        if self.map_view is None:
            self._init_map_view()
        if self._cached_map_render is None:
            self.update_land_mask()
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)
        surface.blit(self._cached_map_render, dest_rect)

    def get_draw_rect(self):
        return pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)

    def load_custom_map(self, path):
        if os.path.exists(path):
            self.cmp = path
            self.sim.cmp = path
            self._init_map_view()
            self._cached_render_hash = None
            self._land_pending = False
            self._cached_map_render = None

    def reset_map(self):
        self.cmp = None
        self.sim.cmp = None
        self._init_map_view()
        self._cached_render_hash = None
        self._land_pending = False
        self._cached_map_render = None
