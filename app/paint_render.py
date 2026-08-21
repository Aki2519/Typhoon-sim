# py/paint_render.py
"""绘画模式渲染引擎: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘制。

输入一组已解析的 Typhoon(或任意构造的路径点列表), 按所选参数把路径/定点画到一张
off-screen Surface 上(可选地图图像或纯背景色), 支持:
  - 色阶方案: 应用现有色阶 / 经典 Wiki / 2023 新版
  - 路径宽度 / 定点大小 / 半宽半高留空 / 最小宽高 / 坐标单位(经纬度/像素)
  - 路径定点绘画顺序: 同时 / 按强度排序 / 定位点置顶
  - 忽略前期扰动 / 显示所有定位点 / 多文件路径重置
  - 显示经纬网 / 绘画风圈(不/全部/仅最后/仅热带)
  - 色阶图例位置(不/左上/左下/右上/右下/自动) + 图例条目
  - 普通(自动范围) / 区域(上,左,高,宽) / 全局 三种范围
  - 背景色 / 文字标注 / 输出尺寸 / 像素留空(内容区保持纵横比, 不拉伸地图)
坐标约定: 等距圆柱投影(与主地图一致, 0..360 x -90..90)。
像素留空模式下: 输出 Surface = 内容区 + 四周留边, 地图/路径只画在内容区,
避免地图被拉伸(内容区纵横比始终 = 经纬跨度比)。
"""
from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

import pygame

from .constants.colors import (
    WIKI_SCALE, COLOR_2023_SCALE,
)
from .data_repo import DataRepository
from .utils import infer_strength_category

# 常用区域表(源自 Typhoon4 区域.txt): 洋区 -> (上, 左, 高, 宽) 单位 °
COMMON_REGIONS = {
    "北大西洋 NATL": (60, -110, 60, 110),
    "中东太平洋 EPAC": (50, 170, 50, 110),
    "西北太平洋 WPAC": (60, 90, 60, 100),
    "北印度洋 NIO": (40, 40, 40, 70),
    "西南印度洋 SWIO": (0, 20, 40, 80),
    "澳洲区 AUS": (0, 80, 40, 90),
    "西南太平洋 SPAC": (0, 150, 50, 70),
    "东南太平洋 SEPAC": (0, -150, 50, 90),
    "南大西洋 SATL": (0, -60, 40, 50),
    "地中海 MED": (50, -10, 30, 50),
    "高纬北大西洋 HATL": (80, -60, 40, 80),
    "东亚 EA": (45, 95, 40, 50),
    "中国 CNA": (55, 65, 55, 75),
    "浙江 ZJ": (26, 116, 6, 8),
}


def scheme_color(scheme: str, cat: str, wind: int = 0) -> Tuple[int, int, int]:
    """按色阶方案取类别颜色。scheme: 'app'/'wiki'/'2023'。"""
    if scheme == "wiki":
        return WIKI_SCALE.get(cat, (150, 150, 150))
    if scheme == "2023":
        return COLOR_2023_SCALE.get(cat, (180, 180, 180))
    # app: 复用主数据仓库的类别色映射(C5 需要按风级细分)
    if cat == "C5":
        from .constants import C5_M
        return C5_M
    return DataRepository._COLOR_MAP.get(cat, (150, 150, 150))


def _eff_category(pt, category_hint: Optional[str] = None) -> str:
    """取点类别: 优先按风级实时推断(与主应用同一套阈值)。"""
    wind = int(pt.get('w', 0) or 0)
    st = str(pt.get('st', ''))
    cat = infer_strength_category(wind, st)
    norm = {"C2-": "C2", "C3-": "C3", "C4-ST": "C4", "STS": "TS"}
    return norm.get(cat, cat)


def _skip_invest_point(pt) -> bool:
    st = str(pt.get('st', '')).upper()
    return st in ('DB', 'LO', 'WV', 'MD') or st == 'INV'


def _tropical(pt) -> bool:
    st = str(pt.get('st', '')).upper()
    return st not in ('EX', 'SS', 'SD', 'MD', 'WV', 'DB', 'LO')


def resolve_bounds(tys, mode: str, region, margin_w: float = 0.0,
                   margin_h: float = 0.0, include_invest: bool = True):
    """计算绘图经纬范围(度)。返回 (lon_min, lon_max, lat_min, lat_max)。"""
    if mode == 'global':
        return (0.0, 360.0, -90.0, 90.0)
    if mode == 'region' and region:
        top, left, height, width = region
        if width > 0 and height > 0:
            return (left, left + width, top - height, top)
    lons, lats = [], []
    for ty in tys:
        pts = ty.pts if hasattr(ty, 'pts') else ty
        for p in pts:
            if not include_invest and _skip_invest_point(p):
                continue
            lons.append(float(p.get('lo', 0) or 0))
            lats.append(float(p.get('la', 0) or 0))
    if not lons:
        return (90.0, 180.0, 0.0, 40.0)
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    if lon_max - lon_min < 0.5:
        lon_min -= 1.0
        lon_max += 1.0
    if lat_max - lat_min < 0.5:
        lat_min -= 1.0
        lat_max += 1.0
    if margin_w > 0 or margin_h > 0:
        lon_min -= margin_w
        lon_max += margin_w
        lat_min -= margin_h
        lat_max += margin_h
    return (lon_min, lon_max, lat_min, lat_max)


def haversine_km(la1, lo1, la2, lo2) -> float:
    """两点大圆距离(km)。"""
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def compute_track_stats(tys):
    """输出每个台风的统计: (名称, ACE, 路径长度km, 定位点数)。"""
    out = []
    for ty in tys:
        pts = ty.pts if hasattr(ty, 'pts') else ty
        if not pts:
            continue
        ace = 0.0
        path = 0.0
        prev = None
        for p in pts:
            w = int(p.get('w', 0) or 0)
            st = str(p.get('st', '')).upper()
            if st in ('TS', 'TY', 'ST', 'HU', '') and w >= 35:
                ace += (w * w) / 10000.0
            la = float(p.get('la', 0) or 0)
            lo = float(p.get('lo', 0) or 0)
            if prev is not None:
                path += haversine_km(prev[0], prev[1], la, lo)
            prev = (la, lo)
        name = (getattr(ty, 'sname', '') or getattr(ty, 'cust', '')
                or getattr(ty, 'name', '') or '?')
        out.append({'name': name, 'ace': ace, 'path_km': path, 'npts': len(pts),
                    'wmax': max(int(p.get('w', 0) or 0) for p in pts)})
    return out


class TrackMapRenderer:
    """把一组路径点渲染为 Track Map Surface。"""

    def __init__(self, opts: Optional[dict] = None) -> None:
        o = opts or {}
        self.scheme = o.get("scheme", "app")
        self.path_width = max(1, int(o.get("path_width", 2)))
        self.point_size = max(1, int(o.get("point_size", 4)))
        self.margin_w = float(o.get("margin_w", 0.0))     # 半宽留空
        self.margin_h = float(o.get("margin_h", 0.0))     # 半高留空
        self.coord_unit = o.get("coord_unit", "deg")      # deg(经纬度)/px(像素)
        self.min_w = max(320, int(o.get("min_w", 1920)))  # 最小宽度(px)
        self.min_h = max(200, int(o.get("min_h", 1080)))  # 最小高度(px)
        self.draw_order = o.get("draw_order", "simult")   # simult/sort/points_top
        self.skip_invest = bool(o.get("skip_invest", False))
        self.all_points = bool(o.get("all_points", True))
        self.grid = bool(o.get("grid", False))
        self.draw_rad = o.get("draw_rad", "none")         # none/all/last/tropical
        self.multireset = o.get("multireset", "none")     # 保留(兼容)
        self.legend_pos = o.get("legend_pos", "auto")
        self.legend_entries = o.get("legend_entries", None)
        self.legend_bg = bool(o.get("legend_bg", False))  # 图例半透明底框, 默认不画
        self.bg_color = o.get("bg_color", None)
        self.map_path = o.get("map_path", None)
        self.labels = bool(o.get("labels", False))
        self.font = o.get("font", None)
        self.output_size = o.get("output_size", None)

    # ── 投影: 相对内容区(内容区 = 去掉留边后的矩形) ──

    def _proj(self, lo: float, la: float, bounds, inner: pygame.Rect):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = lon_max - lon_min
        span_lat = lat_max - lat_min
        if span_lon <= 0 or span_lat <= 0 or inner.w <= 0 or inner.h <= 0:
            return None
        x = inner.x + (lo - lon_min) / span_lon * inner.w
        y = inner.y + (lat_max - la) / span_lat * inner.h
        return (int(x), int(y))

    # ── 主入口 ──

    def render(self, tys, mode: str = "normal", region=None,
               map_surface: Optional[pygame.Surface] = None):
        """返回 (Surface, stats)。stats = compute_track_stats 结果。"""
        bounds = resolve_bounds(tys, mode, region, self.margin_w, self.margin_h,
                                include_invest=not self.skip_invest)
        # 内容区尺寸(保持经纬纵横比)
        content_w, content_h = self._auto_output_size(bounds)
        # 输出尺寸 + 内容区偏移
        if self.output_size:
            out_w, out_h = self.output_size
            inner = pygame.Rect(0, 0, out_w, out_h)
        elif self.coord_unit == "px" and (self.margin_w or self.margin_h):
            out_w = int(content_w + 2 * self.margin_w)
            out_h = int(content_h + 2 * self.margin_h)
            inner = pygame.Rect(int(self.margin_w), int(self.margin_h),
                                content_w, content_h)
        else:
            out_w, out_h = content_w, content_h
            inner = pygame.Rect(0, 0, out_w, out_h)
        out_w, out_h = max(320, out_w), max(240, out_h)

        self._point_density = self._compute_point_density(tys, bounds, inner)

        surf = pygame.Surface((out_w, out_h), pygame.SRCALPHA)
        self._draw_background(surf, bounds, inner, map_surface, out_w, out_h)
        if self.grid:
            self._draw_grid(surf, bounds, inner)
        self._draw_tracks(surf, tys, bounds, inner)
        if self.legend_pos != "none":
            self._draw_legend(surf)
        if self.labels:
            self._draw_labels(surf, tys, bounds, inner)
        stats = compute_track_stats(tys)
        return surf, stats

    def _compute_point_density(self, tys, bounds, inner):
        q = [0, 0, 0, 0]
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            for p in pts:
                c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)),
                               bounds, inner)
                if c is None:
                    continue
                x, y = c
                if x < inner.centerx and y < inner.centery:
                    q[0] += 1
                elif x >= inner.centerx and y < inner.centery:
                    q[1] += 1
                elif x < inner.centerx:
                    q[2] += 1
                else:
                    q[3] += 1
        return q

    def _auto_output_size(self, bounds):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        aspect = span_lon / span_lat
        tw = self.min_w
        th = max(int(tw / aspect), 1)
        if th < self.min_h:
            th = self.min_h
            tw = max(int(th * aspect), 1)
        return (tw, th)

    def _draw_background(self, surf, bounds, inner, map_surface, out_w, out_h):
        """先铺全幅背景, 再把地图精确裁剪到内容区(比例不被拉伸)。"""
        bg = self.bg_color or (200, 220, 240)
        surf.fill(bg)
        _map_img = map_surface
        if _map_img is None and self.map_path and os.path.exists(self.map_path):
            try:
                _map_img = pygame.image.load(self.map_path).convert()
            except Exception:
                _map_img = None
        if _map_img is not None:
            self._draw_map_crop_into(surf, _map_img, bounds, inner)

    def _draw_map_crop_into(self, surf, map_img, bounds, inner):
        """把地图裁剪到内容区 inner(等距圆柱): 按经纬范围裁剪, 保持源图比例不拉伸。

        地图图源 (map/map.png) 为 0..360 x -90..90 等距圆柱, 需同时按经度与纬度
        范围裁剪后再平滑缩放, 否则(只按经度裁剪/用全高)会把纬向拉伸(地图明显拉长)。
        """
        lon_min, lon_max, lat_min, lat_max = bounds
        mw, mh = map_img.get_size()
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        # 源图经纬 -> 像素: 0..360 -> 0..mw, 90..-90 -> 0..mh
        sx = int(((lon_min % 360.0) % 360.0) / 360.0 * mw)
        sw = max(1, int(span_lon / 360.0 * mw))
        sy = int((90.0 - lat_max) / 180.0 * mh)
        sh = max(1, int(span_lat / 180.0 * mh))
        sy = max(0, min(sy, mh - 1))
        sh = max(1, min(sh, mh - sy))
        # 经度环绕拆两段(目标宽度按源宽比例分配)
        if sx >= mw:
            sx = sx % mw
        if sx + sw > mw:
            seg1_w = mw - sx
            seg2_w = sw - seg1_w
            d1_w = max(1, int(seg1_w / mw * inner.w))
            d2_w = max(1, inner.w - d1_w)
            c1 = map_img.subsurface((sx, sy, max(1, mw - sx), sh))
            t1 = pygame.transform.smoothscale(c1, (d1_w, inner.h))
            surf.blit(t1, (inner.x, inner.y))
            if seg2_w > 0 and sy + sh <= mh:
                c2 = map_img.subsurface((0, sy, min(seg2_w, mw), sh))
                t2 = pygame.transform.smoothscale(c2, (d2_w, inner.h))
                surf.blit(t2, (inner.x + d1_w, inner.y))
        else:
            crop_rect = (sx, sy, max(1, min(sw, mw - sx)), sh)
            crop = map_img.subsurface(crop_rect)
            scaled = pygame.transform.smoothscale(crop, (inner.w, inner.h))
            surf.blit(scaled, (inner.x, inner.y))

    def _draw_grid(self, surf, bounds, inner):
        step = 10.0
        color = (0, 0, 0, 60)
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        lon_min, lon_max, lat_min, lat_max = bounds
        lon0 = math.floor(lon_min / step) * step
        lon = lon0
        while lon <= lon_max:
            x = inner.x + int((lon - lon_min) / (lon_max - lon_min) * inner.w)
            pygame.draw.line(line_surf, color, (x, inner.top), (x, inner.bottom), 1)
            lon += step
        lat0 = math.ceil(lat_min / step) * step
        lat = lat0
        while lat <= lat_max:
            y = inner.y + int((lat_max - lat) / (lat_max - lat_min) * inner.h)
            pygame.draw.line(line_surf, color, (inner.left, y), (inner.right, y), 1)
            lat += step
        surf.blit(line_surf, (0, 0))

    def _draw_tracks(self, surf, tys, bounds, inner):
        points_top = self.draw_order == "points_top"
        if self.draw_order == "sort":
            def _peak(ty):
                pts = ty.pts if hasattr(ty, 'pts') else ty
                return max([int(p.get('w', 0) or 0) for p in pts] or [0])
            tys = sorted(tys, key=_peak)
        for idx, ty in enumerate(tys):
            pts = ty.pts if hasattr(ty, 'pts') else ty
            if not pts:
                continue
            draw_pts = []
            for p in pts:
                if self.skip_invest and _skip_invest_point(p):
                    continue
                draw_pts.append(p)
            if not draw_pts:
                continue
            self._draw_ty_path(surf, draw_pts, bounds, inner)
            if not points_top:
                self._draw_ty_points(surf, draw_pts, bounds, inner)
        if points_top:
            for ty in tys:
                pts = ty.pts if hasattr(ty, 'pts') else ty
                draw_pts = [p for p in pts] if pts else []
                if self.skip_invest:
                    draw_pts = [p for p in draw_pts if not _skip_invest_point(p)]
                if not draw_pts:
                    continue
                self._draw_ty_points(surf, draw_pts, bounds, inner)

    def _draw_ty_path(self, surf, pts, bounds, inner):
        if len(pts) < 2:
            return
        coords = []
        for p in pts:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is not None:
                coords.append((c, p))
        if len(coords) < 2:
            return
        for i in range(1, len(coords)):
            (x1, y1), _ = coords[i - 1]
            (x2, y2), p2 = coords[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > (inner.w // 2) ** 2:
                continue
            cat = _eff_category(p2)
            pygame.draw.line(surf, scheme_color(self.scheme, cat),
                             (x1, y1), (x2, y2), self.path_width)

    def _draw_ty_points(self, surf, pts, bounds, inner):
        r = self.point_size
        if self.draw_rad != "none":
            self._draw_radii(surf, pts, bounds, inner)
        pool = pts if self.all_points else pts[-1:]
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            cat = _eff_category(p)
            pygame.draw.circle(surf, scheme_color(self.scheme, cat), c, r)

    def _draw_radii(self, surf, pts, bounds, inner):
        pool = pts
        if self.draw_rad == "last":
            pool = pts[-1:]
        elif self.draw_rad == "tropical":
            pool = [p for p in pts if _tropical(p)]
        ring_color = (0, 0, 0, 120)
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            wind = int(p.get('w', 0) or 0)
            rad34 = int(p.get('r34', 0) or max(0, int(wind * 0.5)))
            pygame.draw.circle(line_surf, ring_color, c, max(4, rad34 + self.point_size), 1)
        surf.blit(line_surf, (0, 0))

    def _draw_legend(self, surf):
        entries = self.legend_entries or _default_entries()
        w, h = surf.get_size()
        item_h = 26
        pad = 12
        label_w = 0
        if self.font is not None:
            label_w = max((self.font.size(lb)[0] for lb, _ in entries), default=0) + 30
        lw = max(170, label_w + 32)
        lh = len(entries) * item_h + pad * 2
        pos = self.legend_pos
        if pos == "auto":
            pos = _auto_legend_corner(entries, lw, lh, w, h, self._point_density)
        if pos == "tl":
            x0, y0 = pad, pad
        elif pos == "tr":
            x0, y0 = w - lw - pad, pad
        elif pos == "br":
            x0, y0 = w - lw - pad, h - lh - pad
        else:
            x0, y0 = pad, h - lh - pad
        panel = pygame.Surface((lw, lh), pygame.SRCALPHA)
        if self.legend_bg:
            # 半透明深色底框(可选, 默认不画)
            pygame.draw.rect(panel, (15, 18, 25, 235), panel.get_rect(), border_radius=8)
            pygame.draw.rect(panel, (200, 210, 230, 90), panel.get_rect(), 1, border_radius=8)
        y = pad
        for label, cat in entries:
            color = scheme_color(self.scheme, cat)
            pygame.draw.circle(panel, color, (pad + 8, y + 13), 8)
            if self.font is not None:
                ts = self.font.render(label, True, (240, 243, 250))
                # 无底框时给文字加描边保证可读性
                if not self.legend_bg:
                    sh = self.font.render(label, True, (10, 12, 18))
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        panel.blit(sh, (pad + 26 + ox, y + (item_h - ts.get_height()) // 2 + 1 + oy))
                panel.blit(ts, (pad + 26, y + (item_h - ts.get_height()) // 2 + 1))
            y += item_h
        surf.blit(panel, (x0, y0))

    def _draw_labels(self, surf, tys, bounds, inner):
        if self.font is None:
            return
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            if not pts:
                continue
            p = pts[-1]
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            name = getattr(ty, 'sname', '') or getattr(ty, 'cust', '') or getattr(ty, 'name', '')
            if not name:
                continue
            ts = self.font.render(name, True, (235, 240, 250))
            # 带描边提高可读性
            shadow = self.font.render(name, True, (10, 12, 18))
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                surf.blit(shadow, (c[0] + 6 + ox, c[1] - ts.get_height() // 2 + oy))
            surf.blit(ts, (c[0] + 6, c[1] - ts.get_height() // 2))


def _default_entries():
    from .constants.colors import WIKI_LEGEND_ENTRIES
    return WIKI_LEGEND_ENTRIES


def _auto_legend_corner(entries, lw, lh, w, h, density=None):
    if not density or not density:
        return "tl"
    corners = [("tl", 0), ("tr", 1), ("bl", 2), ("br", 3)]
    if max(density) - min(density) < max(1, w * h // 4000):
        return "tl"
    best = min(corners, key=lambda kv: density[kv[1]])
    return best[0]


def load_map_image(map_path: Optional[str] = None) -> Optional[pygame.Surface]:
    """载入地图图像(缺省用应用全局地图)。失败返回 None。"""
    if not map_path:
        from .constants import DEFAULT_MAP
        map_path = DEFAULT_MAP
    if os.path.exists(map_path):
        try:
            img = pygame.image.load(map_path)
            try:
                return img.convert()
            except Exception:
                try:
                    return img.convert_alpha()
                except Exception:
                    return img
        except Exception:
            return None
    return None
