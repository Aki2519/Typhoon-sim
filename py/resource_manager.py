# py/resource_manager.py
"""
统一资源管理器 + 地图管理器 + 洋区数据.
支持缓存洋区分界线 overlay 和渲染后的地图背景.
"""
from __future__ import annotations

import os
import math
import logging
from typing import Dict, Optional, Tuple, Any, List

import pygame

from .constants import (
    SUCAI_DIR, f_s, f_m, f_l, f_name,
    MD_COLOR, STS, C5_L, C5_D, DB, EX, TD, TS, C1, C2, C3, C4, C2_MINUS, C3_MINUS, C4_ST, WV,
    DEFAULT_MAP, LAND_MASK, BUTTON_BORDER, AREA_OCEAN_FILE, OCEAN_AREA_LINE,
    find_insensitive_path as fip
)

logger = logging.getLogger(__name__)


class OceanArea:
    def __init__(self, code, name_cn, name_full, hemisphere, avg_ace, vertices, is_merged=False):
        self.code = code
        self.name_cn = name_cn
        self.name_full = name_full
        self.hemisphere = hemisphere
        self.avg_ace = avg_ace
        self.vertices = vertices
        self.is_merged = is_merged
        self._proc_vertices: List[Tuple[float, float]] = []
        self._proc_lon_center: float = 0.0
        self._is_hemisphere = self._detect_hemisphere()
        if not self._is_hemisphere:
            self._preprocess()

    def _detect_hemisphere(self):
        """检测 2 顶点、同经度、赤道→极点的半球定义模式。"""
        if len(self.vertices) != 2:
            return False
        (lat0, lon0), (lat1, lon1) = self.vertices
        if abs(lon0 - lon1) > 0.001:
            return False
        if abs(lat0) <= 0.11 and lat1 >= 89.9:
            self._hemisphere_north = True
            return True
        if abs(lat0) <= 0.11 and lat1 <= -89.9:
            self._hemisphere_north = False
            return True
        return False

    def _preprocess(self):
        n = len(self.vertices)
        if n < 3:
            self._proc_vertices = list(self.vertices)
            return
        unwrapped = [[self.vertices[0][0], self.vertices[0][1]]]
        for i in range(1, n):
            prev_lon = unwrapped[-1][1]
            cur_lat, cur_lon = self.vertices[i]
            while cur_lon - prev_lon > 180: cur_lon -= 360
            while cur_lon - prev_lon < -180: cur_lon += 360
            unwrapped.append([cur_lat, cur_lon])
        lons = [v[1] for v in unwrapped]
        span = max(lons) - min(lons)
        if span > 180:
            center = (min(lons) + max(lons)) / 2.0
            shifted = []
            for lat, lon in unwrapped:
                while lon - center > 180: lon -= 360
                while lon - center < -180: lon += 360
                shifted.append((lat, lon))
            self._proc_vertices = shifted
        else:
            self._proc_vertices = [(v[0], v[1]) for v in unwrapped]
        lons2 = [v[1] for v in self._proc_vertices]
        self._proc_lon_center = (min(lons2) + max(lons2)) / 2.0

    def contains(self, lat, lon):
        if self._is_hemisphere:
            return lat >= 0 if self._hemisphere_north else lat < 0
        verts = self._proc_vertices
        if len(verts) < 3:
            return False
        test_lon = lon
        while test_lon - self._proc_lon_center > 180: test_lon -= 360
        while test_lon - self._proc_lon_center < -180: test_lon += 360
        inside = False
        j = len(verts) - 1
        for i in range(len(verts)):
            yi, xi = verts[i]
            yj, xj = verts[j]
            if (yi > lat) != (yj > lat):
                if test_lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
            j = i
        return inside


class OceanAreaManager:
    def __init__(self):
        self.areas: List[OceanArea] = []
        self._load()

    @property
    def total_avg_ace(self):
        """所有非合并洋区的平均ACE之和（合并洋区不参与统计）。"""
        return sum(a.avg_ace for a in self.areas if not a.is_merged)

    def _load(self):
        geojson_path = os.path.join(SUCAI_DIR, "Area_ocean.geojson")
        if os.path.exists(geojson_path):
            self._load_geojson(geojson_path)
            if self.areas:
                return
        path = fip(AREA_OCEAN_FILE)
        if not path:
            logger.warning(f"洋区文件不存在: {AREA_OCEAN_FILE}")
            return
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    self.areas.append(self._parse(line))

    def _load_geojson(self, path: str):
        import json
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"GeoJSON 加载失败: {e}")
            return
        features = data.get("features", [])
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            if geom.get("type") != "Polygon":
                continue
            coords = geom.get("coordinates", [[]])[0]
            verts = [(lat, lon) for lon, lat in coords]  # GeoJSON [lon,lat] → (lat,lon)
            area = OceanArea(
                code=props.get("code", ""),
                name_cn=props.get("name_cn", ""),
                name_full=props.get("name_full", ""),
                hemisphere=props.get("hemisphere", "N"),
                avg_ace=float(props.get("avg_ace", 0)),
                vertices=verts,
                is_merged=bool(props.get("is_merged", False)),
            )
            self.areas.append(area)

    def _parse(self, line):
        # 合并洋区以 (...) 包裹，去除外层括号
        is_merged = line.startswith('(') and line.endswith(')')
        if is_merged:
            line = line[1:-1]
        parts = line.rsplit('/', 4)
        header, name_cn = parts[0], parts[1] if len(parts) > 1 else ""
        name_full = parts[2] if len(parts) > 2 else name_cn
        hemi = parts[3].strip().upper() if len(parts) > 3 else "N"
        # avg_ace 可能以 ) 结尾（合并洋区），需要清理
        avg_ace_raw = parts[4].strip().rstrip(')') if len(parts) > 4 else "0"
        avg_ace = float(avg_ace_raw) if avg_ace_raw else 0.0
        code = header.split(';')[0].strip()
        verts = []
        for s in header.split(';')[1:]:
            toks = s.strip().split()
            if len(toks) >= 2:
                verts.append((self._plat(toks[0]), self._plon(toks[1])))
        return OceanArea(code, name_cn.strip(), name_full.strip(), hemi, avg_ace, verts, is_merged)

    @staticmethod
    def _plat(s):
        s = s.strip().upper()
        if s.endswith('S'):  return -float(s[:-1])
        if s.endswith('N'):  return float(s[:-1])
        return float(s)

    @staticmethod
    def _plon(s):
        s = s.strip().upper()
        v = float(s[:-1]) if s.endswith(('E', 'W')) else float(s)
        if s.endswith('W') and abs(v - 180) > 0.001:
            return 360.0 - v
        return v

    def find_area(self, lat, lon):
        for area in self.areas:
            if area.contains(lat, lon):
                return area
        return None

    def get_by_code(self, code):
        for a in self.areas:
            if a.code == code:
                return a
        return None


# ── 资源管理器 ──

class ResourceManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.images: Dict[str, pygame.Surface] = {}
        self.sounds: Dict[str, pygame.mixer.Sound] = {}
        self.fonts = {'f_s': f_s, 'f_m': f_m, 'f_l': f_l, 'f_name': f_name}
        self.ocean_areas = OceanAreaManager()
        self._load_all()

    def _load_all(self):
        categories = ['DB', 'EX', 'TD', 'TS', 'STS', 'C1', 'C2-', 'C2', 'C3-', 'C3', 'C4', 'C4-ST', 'C5',
                      'MD', 'SD', 'SS', 'LO', 'WV', 'C3_3', 'C4_3', 'C5_3']
        for cat in categories:
            ring_path = fip(os.path.join(SUCAI_DIR, f"{cat}_1.png"))
            center_path = fip(os.path.join(SUCAI_DIR, f"{cat}_2.png"))
            ring_img, center_img = None, None
            if ring_path:
                try: ring_img = pygame.image.load(ring_path).convert_alpha()
                except Exception as e: logger.warning(f"加载图标失败 {cat}_1: {e}")
            if center_path:
                try: center_img = pygame.image.load(center_path).convert_alpha()
                except Exception as e: logger.warning(f"加载图标失败 {cat}_2: {e}")
            self.images[f"{cat}_ring"] = ring_img or self._create_ring_icon(cat)
            self.images[f"{cat}_center"] = center_img or self._create_center_icon(cat)

        for sub, base, color, mult in [('C2-', 'C2', C2_MINUS, False), ('C4-ST', 'C4', C4_ST, True)]:
            ring_img = self.images.get(f"{base}_ring")
            if ring_img:
                self.images[f"{sub}_ring"] = self._recolor_icon(ring_img, color, mult)

        lf_map = {'C1': 'C1', 'C2-': 'C2', 'C2': 'C2', 'C3-': 'C3', 'C3': 'C3',
                  'C4': 'C4', 'C4-ST': 'C4', 'C5': 'C5',
                  'STS': 'STS', 'TS': 'TS', 'TD': 'TD', 'SD': 'SD', 'SS': 'SS',
                  'EX': 'EX', 'MD': 'MD', 'DB': 'DB', 'WV': 'WV'}
        for key, prefix in lf_map.items():
            for sfx in ('', '_2'):
                path = fip(os.path.join(SUCAI_DIR, f"landfall_{prefix}{sfx}.png"))
                if path:
                    try:
                        self.images[f"landfall_{key}{'_2' if sfx else '_1'}"] = \
                            pygame.image.load(path).convert_alpha()
                    except Exception as e: logger.warning(f"加载登陆图片失败 {key}{sfx}: {e}")

        for strength, suffix in {'C1': 'C1', 'C2': 'C2', 'C3': 'C3', 'C4': 'C4', 'C5': 'C5',
                                  'TS': 'TS', 'STS': 'TS', 'SS': 'SS', 'TD': 'TD', 'EX': 'EX', 'MD': 'MD'}.items():
            path = os.path.join(SUCAI_DIR, f"sound.landfall.{suffix}.ogg")
            if os.path.exists(path):
                try: self.sounds[strength] = pygame.mixer.Sound(path)
                except Exception:
                    logger.debug(f"加载音效失败: {path}", exc_info=True)

    def _create_ring_icon(self, cat):
        s = pygame.Surface((80, 80), pygame.SRCALPHA)
        colors = {'DB': (150, 150, 150), 'EX': (150, 200, 255), 'MD': MD_COLOR,
                  'LO': (150, 200, 100), 'STS': STS, 'C5': C5_L,
                  'C2-': C2_MINUS, 'C3-': C3_MINUS, 'C4-ST': C4_ST, 'WV': WV}
        c = colors.get(cat, (100, 150, 255))
        pygame.draw.circle(s, (*c, 200), (40, 40), 35, 5)
        if cat in ('C3_3', 'C4_3', 'C5_3'):
            return s
        lc = {'MD': (0, 200, 0, 220), 'LO': (100, 200, 100, 220),
              'STS': (*STS, 220)}.get(cat,
              (50, 100, 200, 220) if cat != 'C5' else (*C5_L, 220))
        for i in range(0, 360, 45):
            a = math.radians(i)
            x1, y1 = 40 + 30 * math.cos(a), 40 + 30 * math.sin(a)
            x2, y2 = 40 + 25 * math.cos(a), 40 + 25 * math.sin(a)
            pygame.draw.line(s, lc, (x1, y1), (x2, y2), 2)
        return s

    @staticmethod
    def _create_center_icon(cat):
        s = pygame.Surface((60, 60), pygame.SRCALPHA)
        pygame.draw.circle(s, (255, 255, 255, 240), (30, 30), 20)
        pygame.draw.circle(s, (50, 50, 50, 240), (30, 30), 4)
        return s

    @staticmethod
    def _recolor_icon(surface, target_color, mult=False):
        if mult:
            fill_color = tuple(255 if c == 0 else c for c in target_color)
            tinted = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            tinted.fill((*fill_color, 255))
            tinted.blit(surface, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            return tinted
        tinted = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        tinted.fill((*target_color, 0))
        tinted.blit(surface, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        return tinted

    def get_image(self, name): return self.images.get(name)
    def get_sound(self, name): return self.sounds.get(name)
    def get_font(self, name): return self.fonts.get(name, f_s)
    def get_landfall_images(self, s):
        return self.images.get(f"landfall_{s}_1"), self.images.get(f"landfall_{s}_2")


# ── MapView ──

class MapView:
    def __init__(self, img_path, lon_min, lon_max, lat_min, lat_max, screen_width, screen_height):
        self.original_img = pygame.image.load(img_path).convert()
        self.img_w, self.img_h = self.original_img.get_size()
        self.lon_min, self.lon_max = lon_min, lon_max
        self.lat_min, self.lat_max = lat_min, lat_max
        self.width_deg = lon_max - lon_min
        self.height_deg = lat_max - lat_min
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.view_x = self.view_y = 0.0
        self.scale = 1.0
        self.min_scale = min(screen_width / self.img_w, screen_height / self.img_h)
        self._cached_scale = -1.0
        self._cached_offset = (0, 0)

    @property
    def _max_view_y(self):
        return max(0.0, self.img_h - self.screen_height / self.scale)

    def _clamp_view_y(self):
        self.view_y = max(0.0, min(self.view_y, self._max_view_y))

    def _src_y(self, view_h):
        return max(0.0, min(self.view_y, self.img_h - view_h))

    def geo_to_screen(self, lon, lat):
        px = (lon - self.lon_min) / self.width_deg * self.img_w
        py = (self.lat_max - lat) / self.height_deg * self.img_h
        cx = self.view_x + self.screen_width / (2.0 * self.scale)
        dx = px - cx
        if dx > self.img_w / 2.0: dx -= self.img_w
        elif dx < -self.img_w / 2.0: dx += self.img_w
        ox, oy = self._draw_offset()
        return int(dx * self.scale + self.screen_width / 2.0) + ox, \
               int((py - self.view_y) * self.scale) + oy

    def screen_to_geo(self, sx, sy):
        ox, oy = self._draw_offset()
        sx, sy = sx - ox, sy - oy
        cx = self.view_x + self.screen_width / (2.0 * self.scale)
        px = cx + (sx - self.screen_width / 2.0) / self.scale
        py = self.view_y + sy / self.scale
        lon = self.lon_min + (px / self.img_w) * self.width_deg
        lon = ((lon - self.lon_min) % self.width_deg) + self.lon_min
        lat = max(self.lat_min, min(self.lat_max,
                                    self.lat_max - (py / self.img_h) * self.height_deg))
        return lon, lat

    def move_view(self, dx, dy):
        old_vx, old_vy = self.view_x, self.view_y
        self.view_x -= dx / self.scale
        self.view_y -= dy / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()
        return (self.view_x - old_vx) * self.scale, (self.view_y - old_vy) * self.scale

    def zoom_at(self, factor, mx, my):
        ox, oy = self._draw_offset()
        mx, my = mx - ox, my - oy
        old = self.scale
        self.scale = max(self.min_scale, min(self.scale * factor, 8.0))
        self.view_x = (self.view_x + mx / old) - mx / self.scale
        self.view_y = (self.view_y + my / old) - my / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()

    def set_view_region(self, lon_min, lon_max, lat_min, lat_max):
        if lon_max < lon_min: lon_max += 360
        clon = (lon_min + lon_max) / 2.0
        clat = (lat_min + lat_max) / 2.0
        lon_span = lon_max - lon_min
        lat_span = lat_max - lat_min
        self.scale = max(self.min_scale, min(
            self.screen_width / (lon_span / self.width_deg * self.img_w),
            self.screen_height / (lat_span / self.height_deg * self.img_h), 8.0))
        self.view_x = ((clon - self.lon_min) / self.width_deg * self.img_w
                       - self.screen_width / (2.0 * self.scale)) % self.img_w
        self.view_y = (self.lat_max - clat) / self.height_deg * self.img_h \
                      - self.screen_height / (2.0 * self.scale)
        self._clamp_view_y()

    def _draw_offset(self):
        if self._cached_scale != self.scale:
            vw = min(self.screen_width / self.scale, self.img_w)
            vh = min(self.screen_height / self.scale, self.img_h)
            self._cached_offset = (
                int((self.screen_width - vw * self.scale) / 2),
                int((self.screen_height - vh * self.scale) / 2),
            )
            self._cached_scale = self.scale
        return self._cached_offset

    def draw(self, screen, dest_rect=None):
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
        screen.fill((120, 120, 120), dest_rect)

        vw = min(dest_rect.width / self.scale, self.img_w)
        vh = min(dest_rect.height / self.scale, self.img_h)
        sx, sy = self.view_x % self.img_w, self._src_y(vh)
        ox, oy = self._draw_offset()
        rw = self.img_w - sx

        x_off = 0
        for seg_x, seg_w in ([(sx, min(rw, vw))] if rw >= vw
                             else [(sx, rw), (0, vw - rw)]):
            rect = pygame.Rect(int(seg_x), int(sy),
                               int(math.ceil(seg_w)), int(math.ceil(vh)))
            try:
                part = self.original_img.subsurface(rect)
                scaled = pygame.transform.scale(part,
                    (max(1, int(seg_w * self.scale)), max(1, int(vh * self.scale))))
                screen.blit(scaled, (dest_rect.left + ox + x_off, dest_rect.top + oy))
                x_off += scaled.get_width()
            except ValueError:
                pass


# ── MapManager ──

class MapManager:
    def __init__(self, sim):
        self.sim = sim
        self.map_view: Optional[MapView] = None
        self.land_img: Optional[pygame.Surface] = None
        self.ocean_overlay: Optional[pygame.Surface] = None
        self.cmp: Optional[str] = None
        self._orig_map_cache: Dict[str, pygame.Surface] = {}
        self._land_orig: Optional[pygame.Surface] = None
        # 缓存
        self._cached_map_render: Optional[pygame.Surface] = None
        self._cached_render_hash = None

    def _view_hash(self):
        v = self.map_view
        if v is None:
            return None
        return (int(v.view_x * 100), int(v.view_y * 100),
                v.scale, self.sim.screen_width, self.sim.map_height)

    def _init_map_view(self):
        path = self.cmp if self.cmp and os.path.exists(self.cmp) else DEFAULT_MAP
        if path not in self._orig_map_cache:
            try:
                self._orig_map_cache[path] = pygame.image.load(path).convert() \
                    if os.path.exists(path) else self._dummy()
            except Exception:
                self._orig_map_cache[path] = self._dummy()
        self.map_view = MapView(path, 0.0, 360.0, -90.0, 90.0,
                                self.sim.screen_width, self.sim.map_height)
        self.map_view.set_view_region(self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla)

    @staticmethod
    def _dummy():
        s = pygame.Surface((100, 50)); s.fill((128, 128, 128)); return s

    def _load_land_orig(self):
        if self._land_orig is None and os.path.exists(LAND_MASK):
            try:
                self._land_orig = pygame.image.load(LAND_MASK).convert_alpha()
            except Exception as e:
                logger.error(f"加载陆地掩码失败: {e}")
        return self._land_orig

    def _rebuild_land_and_overlay(self):
        """重建陆地掩码和洋区分界线（仅在视图变化时调用）。"""
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

        # ── 洋区 overlay：已移除 ──
        self.ocean_overlay = None

    def _rebuild_map_render(self):
        """将地图渲染到一张缓存 Surface 上。"""
        w, h = self.sim.screen_width, self.sim.map_height
        self._cached_map_render = pygame.Surface((w, h))
        self.map_view.draw(self._cached_map_render, pygame.Rect(0, 0, w, h))
        self._cached_render_hash = self._view_hash()

    def update_land_mask(self):
        """外部调用：按需重建陆地掩码和地图缓存。"""
        if self.map_view is None:
            return
        vh = self._view_hash()
        if vh is not None and vh == self._cached_render_hash:
            return  # 完全命中，无需任何重建

        self._rebuild_land_and_overlay()
        self._rebuild_map_render()
        self._cached_render_hash = vh

    def is_land_at_screen(self, sx, sy):
        return (self.land_img is not None
                and 0 <= sx < self.land_img.get_width()
                and 0 <= sy < self.land_img.get_height()
                and self.land_img.get_at((sx, sy)).a > 0)

    def update_ocean_overlay(self):
        """外部调用：已合并到 update_land_mask 的 _rebuild_land_and_overlay 中。"""
        pass

    @staticmethod
    def _dashed(surface, x1, y1, x2, y2, color, dash=8, gap=6):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1: return
        dx, dy = dx / length, dy / length
        step = dash + gap
        for k in range(int(length / step)):
            sx, sy = x1 + k * step * dx, y1 + k * step * dy
            if k == int(length / step) - 1:
                ex, ey = x2, y2
            else:
                ex, ey = sx + dash * dx, sy + dash * dy
            pygame.draw.line(surface, color, (int(sx), int(sy)), (int(ex), int(ey)), 1)

    def update_view(self):
        if self.map_view is None:
            self._init_map_view()
        else:
            self.map_view.set_view_region(self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla)
        self.update_land_mask()

    update_map_image = update_view

    def draw_map(self, surface, dest_rect=None):
        if self.map_view is None:
            self._init_map_view()
        if self._cached_map_render is None:
            self.update_land_mask()
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)

        if self._cached_map_render is not None:
            surface.blit(self._cached_map_render, dest_rect)
        else:
            self.map_view.draw(surface, dest_rect)

    def get_draw_rect(self):
        return pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)

    def load_custom_map(self, path):
        if os.path.exists(path):
            self.cmp = path; self._orig_map_cache.pop(path, None)
            self._init_map_view()
            self.sim._config_needs_save = True

    def reset_map(self):
        self.cmp = None
        self._init_map_view()
        self.sim._config_needs_save = True