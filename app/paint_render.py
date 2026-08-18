# py/paint_render.py
"""绘画模式渲染引擎: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘制。

输入一组已解析的 Typhoon(或任意构造的路径点列表), 按所选参数把路径/定点画到一张
off-screen Surface 上(可选地图图像或纯背景色), 支持:
  - 色阶方案: 应用现有色阶 / 经典 Wiki / 2023 新版
  - 路径宽度 / 定点大小 / 半宽半高留空 / 最小宽高
  - 路径定点绘画顺序: 同时 / 按强度排序 / 定位点置顶
  - 忽略前期扰动 / 显示所有定位点 / 多文件路径重置
  - 显示经纬网 / 绘画风圈(不/全部/仅最后/仅热带)
  - 色阶图例位置(不/左上/左下/右上/右下/自动) + 图例条目
  - 普通(自动范围) / 区域(上,左,高,宽) / 全局 三种范围
  - 背景色 / 文字标注 / 输出尺寸
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
    """取点类别: 优先按风级实时推断(与主应用同一套阈值), 其次用节内缓存。"""
    wind = int(pt.get('w', 0) or 0)
    st = str(pt.get('st', ''))
    cat = infer_strength_category(wind, st)
    # 归一化到 Wiki 7 档: C2-/C3-/C4-ST 折叠到基础档, STS 计入 Storm
    norm = {
        "C2-": "C2", "C3-": "C3", "C4-ST": "C4", "STS": "TS",
    }
    return norm.get(cat, cat)


def _skip_invest_point(pt) -> bool:
    """忽略前期扰动定位: DB/扰动(文件号 9xW/9xE 前身)与生成前阶段。"""
    st = str(pt.get('st', '')).upper()
    return st in ('DB', 'LO', 'WV', 'MD') or st == 'INV'


def _tropical(pt) -> bool:
    st = str(pt.get('st', '')).upper()
    return st not in ('EX', 'SS', 'SD', 'MD', 'WV', 'DB', 'LO')


def resolve_bounds(tys, mode: str, region, margin_w: float = 0.0,
                   margin_h: float = 0.0, include_invest: bool = True):
    """计算绘图经纬范围(度)。返回 (lon_min, lon_max, lat_min, lat_max)。
    mode: 'global' / 'region' / 'normal'(自动套用所有有效点范围)
    region: (top, left, height, width) 上,左,高,宽 (Ty4 语义)
    """
    if mode == 'global':
        return (0.0, 360.0, -90.0, 90.0)

    if mode == 'region' and region:
        top, left, height, width = region
        if width > 0 and height > 0:
            lon_min, lon_max = left, left + width
            lat_min, lat_max = top - height, top
            return (lon_min, lon_max, lat_min, lat_max)

    # normal / 无有效区域: 全部路过点自动范围
    lons, lats = [], []
    for ty in tys:
        pts = ty.pts if hasattr(ty, 'pts') else ty
        for p in pts:
            if not include_invest and _skip_invest_point(p):
                continue
            la = float(p.get('la', 0) or 0)
            lo = float(p.get('lo', 0) or 0)
            lons.append(lo)
            lats.append(la)
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
        self.multireset = o.get("multireset", "none")     # none/last(auto path color 重置)
        self.legend_pos = o.get("legend_pos", "auto")
        self.legend_entries = o.get("legend_entries", None)  # [(label, cat), ...]
        self.bg_color = o.get("bg_color", None)
        self.map_path = o.get("map_path", None)
        self.labels = bool(o.get("labels", False))
        self.font = o.get("font", None)
        self.output_size = o.get("output_size", None)     # (w, h) 覆盖 min_w/min_h

    # ── 投影: 等距圆柱(与主地图一致) ──

    def _proj(self, lo: float, la: float, bounds, w: int, h: int):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = lon_max - lon_min
        span_lat = lat_max - lat_min
        if span_lon <= 0 or span_lat <= 0:
            return None
        x = (lo - lon_min) / span_lon * w
        y = (lat_max - la) / span_lat * h
        return (int(x), int(y))

    # ── 主入口 ──

    def render(self, tys, mode: str = "normal", region=None,
               map_surface: Optional[pygame.Surface] = None) -> pygame.Surface:
        """tys: list[Typhoon] 或 list[list[TrackPoint]](缺省取 pts)。
        返回已绘制的 Surface(尺寸 = output_size 或按 bounds 纵横比贴合 min_w/min_h)。
        """
        bounds = resolve_bounds(tys, mode, region, self.margin_w, self.margin_h,
                                include_invest=not self.skip_invest)
        # 输出尺寸
        if self.output_size:
            out_w, out_h = self.output_size
        else:
            out_w, out_h = self._auto_output_size(bounds)
            if self.coord_unit == "px" and (self.margin_w or self.margin_h):
                out_w = max(320, int(out_w + 2 * self.margin_w))
                out_h = max(240, int(out_h + 2 * self.margin_h))
        out_w, out_h = max(320, out_w), max(240, out_h)

        # 统计各象限路径点密度(供 auto 图例定位)
        self._point_density = self._compute_point_density(tys, bounds, out_w, out_h)

        surf = pygame.Surface((out_w, out_h), pygame.SRCALPHA)
        # 背景
        self._draw_background(surf, bounds, map_surface)
        if self.grid:
            self._draw_grid(surf, bounds)
        # 路径
        self._draw_tracks(surf, tys, bounds)
        # 色阶图例
        if self.legend_pos != "none":
            self._draw_legend(surf)
        if self.labels:
            self._draw_labels(surf, tys, bounds)
        return surf

    def _compute_point_density(self, tys, bounds, w, h):
        """统计四个象限内路径点数量 [tl, tr, bl, br]。"""
        q = [0, 0, 0, 0]
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            for p in pts:
                lo = float(p.get('lo', 0))
                la = float(p.get('la', 0))
                c = self._proj(lo, la, bounds, w, h)
                if c is None:
                    continue
                x, y = c
                if x < w / 2 and y < h / 2:
                    q[0] += 1
                elif x >= w / 2 and y < h / 2:
                    q[1] += 1
                elif x < w / 2:
                    q[2] += 1
                else:
                    q[3] += 1
        return q

    def _last_point_density(self, w, h):
        return getattr(self, '_point_density', [0, 0, 0, 0])

    def _auto_output_size(self, bounds):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        aspect = span_lon / span_lat          # 宽/高(度)
        tw = self.min_w
        th = max(int(tw / aspect), 1)
        if th < self.min_h:
            th = self.min_h
            tw = max(int(th * aspect), 1)
        return (tw, th)

    def _draw_background(self, surf, bounds, map_surface):
        """地图底图开关: 有地图图源(传入或 map_path)才铺地图, 否则纯背景色。"""
        _map_img = map_surface
        if _map_img is None and self.map_path and os.path.exists(self.map_path):
            try:
                _map_img = pygame.image.load(self.map_path).convert()
            except Exception:
                _map_img = None
        if _map_img is not None:
            self._draw_map_crop(surf, _map_img, bounds)
            return
        bg = self.bg_color or (200, 220, 240)
        surf.fill(bg)

    def _draw_map_crop(self, surf, map_img, bounds):
        lon_min, lon_max, lat_min, lat_max = bounds
        mw, mh = map_img.get_size()
        # 地图按全局 0..360, -90..90 假设(与 map/map.png 一致)
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        sx = max(0, int((lon_min % 360.0) / 360.0 * mw))
        sw = max(1, int(span_lon / 360.0 * mw))
        # 经度可环绕(区域跨过 0°/180° 时拆两段画)
        if sx + sw > mw:
            # 分两段绘制, 先画右侧再到左侧
            seg1_w = mw - sx
            seg2_w = sw - seg1_w
            crop1 = map_img.subsurface((sx, 0, seg1_w, mh))
            surf1 = pygame.transform.smoothscale(
                crop1, (max(1, int(seg1_w / mw * surf.get_width())),
                        max(1, surf.get_height())))
            surf.blit(surf1, (0, 0))
            if seg2_w > 0:
                crop2 = map_img.subsurface((0, 0, seg2_w, mh))
                surf2 = pygame.transform.smoothscale(
                    crop2, (max(1, int(seg2_w / mw * surf.get_width())),
                            max(1, surf.get_height())))
                surf.blit(surf2, (surf1.get_width(), 0))
        else:
            crop = map_img.subsurface((sx, 0, sw, mh))
            scaled = pygame.transform.smoothscale(crop, (surf.get_width(), surf.get_height()))
            surf.blit(scaled, (0, 0))

    def _draw_grid(self, surf, bounds):
        lon_min, lon_max, lat_min, lat_max = bounds
        w, h = surf.get_size()
        step = 10.0
        color = (0, 0, 0, 60)
        sx = surf.get_width()
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        # 经线
        lon0 = math.floor(lon_min / step) * step
        lon = lon0
        while lon <= lon_max:
            x = int((lon - lon_min) / (lon_max - lon_min) * w)
            pygame.draw.line(line_surf, color, (x, 0), (x, h), 1)
            lon += step
        # 纬线
        lat0 = math.ceil(lat_min / step) * step
        lat = lat0
        while lat <= lat_max:
            y = int((lat_max - lat) / (lat_max - lat_min) * h)
            pygame.draw.line(line_surf, color, (0, y), (w, y), 1)
            lat += step
        surf.blit(line_surf, (0, 0))

    def _draw_tracks(self, surf, tys, bounds):
        # 排序: sort -> 强度(最大风)升序(后画强台风置顶); points_top -> 路径先画, 点最后统一画
        w, h = surf.get_size()
        points_top = self.draw_order == "points_top"
        if self.draw_order == "sort":
            def _peak(ty):
                pts = ty.pts if hasattr(ty, 'pts') else ty
                return max([int(p.get('w', 0) or 0) for p in pts] or [0])
            tys = sorted(tys, key=_peak)

        # 逐条路径: 线段 + 定点
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
            self._draw_ty_path(surf, draw_pts, bounds)
            if not points_top:
                self._draw_ty_points(surf, draw_pts, bounds)

        if points_top:
            # 定位点置顶: 所有路径的定点最后统一绘制
            for ty in tys:
                pts = ty.pts if hasattr(ty, 'pts') else ty
                draw_pts = [p for p in pts] if pts else []
                if self.skip_invest:
                    draw_pts = [p for p in draw_pts if not _skip_invest_point(p)]
                if not draw_pts:
                    continue
                self._draw_ty_points(surf, draw_pts, bounds)

    def _draw_ty_path(self, surf, pts, bounds):
        if len(pts) < 2:
            return
        w, h = surf.get_size()
        coords = [self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, w, h)
                  for p in pts]
        coords = [c for c in coords if c is not None]
        if len(coords) < 2:
            return
        for i in range(1, len(coords)):
            x1, y1 = coords[i - 1]
            x2, y2 = coords[i]
            dx, dy = x2 - x1, y2 - y1
            if dx * dx + dy * dy > (w // 2) ** 2:
                continue  # 跳过跨图伪线(经度环绕)
            cat = _eff_category(pts[min(i, len(pts) - 1)])
            color = scheme_color(self.scheme, cat)
            pygame.draw.line(surf, color, (x1, y1), (x2, y2), self.path_width)

    def _draw_ty_points(self, surf, pts, bounds):
        w, h = surf.get_size()
        r = self.point_size
        if self.draw_rad != "none":
            self._draw_radii(surf, pts, bounds)
        # 仅最后定位
        pool = pts if self.all_points else pts[-1:]
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, w, h)
            if c is None:
                continue
            cat = _eff_category(p)
            color = scheme_color(self.scheme, cat)
            pygame.draw.circle(surf, color, c, r)

    def _draw_radii(self, surf, pts, bounds):
        """绘画风圈(简化): 以风速近似圆环半径(有真实半径字段则用之)。"""
        w, h = surf.get_size()
        pool = pts
        if self.draw_rad == "last":
            pool = pts[-1:]
        elif self.draw_rad == "tropical":
            pool = [p for p in pts if _tropical(p)]
        ring_color = (0, 0, 0, 120)
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, w, h)
            if c is None:
                continue
            wind = int(p.get('w', 0) or 0)
            # 简单风圈: 半径与风速成正比(1kt -> 0.5px + 基线); 有半径字段 r50/r64 时优先
            rad34 = int(p.get('r34', 0) or max(0, int(wind * 0.5)))
            pygame.draw.circle(line_surf, ring_color, c, max(4, rad34 + self.point_size), 1)
        surf.blit(line_surf, (0, 0))

    def _draw_legend(self, surf):
        entries = self.legend_entries or _default_entries()
        w, h = surf.get_size()
        item_h = 22
        pad = 10
        label_w = 0
        if self.font is not None:
            label_w = max((self.font.size(lb)[0] for lb, _ in entries), default=0) + 26
        lw = max(150, label_w + 30)
        lh = len(entries) * item_h + pad * 2
        pos = self.legend_pos
        if pos == "auto":
            pos = _auto_legend_corner(entries, lw, lh, w, h,
                                      self._last_point_density(w, h))
        if pos == "tl":
            x0, y0 = pad, pad
        elif pos == "tr":
            x0, y0 = w - lw - pad, pad
        elif pos == "br":
            x0, y0 = w - lw - pad, h - lh - pad
        else:  # bl
            x0, y0 = pad, h - lh - pad
        panel = pygame.Surface((lw, lh), pygame.SRCALPHA)
        pygame.draw.rect(panel, (255, 255, 255, 210), panel.get_rect(), border_radius=6)
        pygame.draw.rect(panel, (0, 0, 0, 90), panel.get_rect(), 1, border_radius=6)
        y = pad
        for label, cat in entries:
            color = scheme_color(self.scheme, cat)
            pygame.draw.circle(panel, color, (pad + 7, y + 11), 7)
            if self.font is not None:
                ts = self.font.render(label, True, (20, 30, 50))
                panel.blit(ts, (pad + 20, y + (item_h - ts.get_height()) // 2 + 1))
            y += item_h
        surf.blit(panel, (x0, y0))

    def _draw_labels(self, surf, tys, bounds):
        passage = []
        w, h = surf.get_size()
        if self.font is None:
            return
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            if not pts:
                continue
            p = pts[-1]
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, w, h)
            if c is None:
                continue
            name = getattr(ty, 'sname', '') or getattr(ty, 'cust', '') or getattr(ty, 'name', '')
            if not name:
                continue
            passage.append((name, c))
        for name, c in passage:
            ts = self.font.render(name, True, (20, 20, 40))
            surf.blit(ts, (c[0] + 6, c[1] - ts.get_height() // 2))


def _default_entries():
    from .constants.colors import WIKI_LEGEND_ENTRIES
    return WIKI_LEGEND_ENTRIES


def _auto_legend_corner(entries, lw, lh, w, h, density=None):
    """自动选择路径点最少的角落放置图例, 避免盖住密集路径。
    density: [tl, tr, bl, br] 各象限点数(空则退回左上)。"""
    if density is None or not density:
        return "tl"
    corners = [("tl", 0), ("tr", 1), ("bl", 2), ("br", 3)]
    # 无明显差异(全平)时左上
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
