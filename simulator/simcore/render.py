# simulator.simcore/render.py
"""SimCore 渲染移植(pygame): 卫星云图 IR/VIS、气压等值线、风羽/粒子、
海温/湿度叠加、集合预报锥、路径/风圈/眼墙/标签、副高脊线。

设计: 所有图层绘制到传入的地图 surface 上, 经纬度→像素由外部提供
(fx, fy 函数), 实现"叠加在现有地图上"而非全屏覆盖。
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

import numpy as np
import pygame

from . import core as V

# ── 色标(256 级 RGBA) ──


def _build_lut(stops: list) -> np.ndarray:
    lut = np.zeros((256, 4), dtype=np.uint8)
    lo = stops[0][0]
    hi = stops[-1][0]
    vs = lo + (hi - lo) * np.arange(256) / 255.0
    for k in range(256):
        v = vs[k]
        a = stops[0]
        b = stops[-1]
        for s in range(len(stops) - 1):
            if stops[s][0] <= v <= stops[s + 1][0]:
                a = stops[s]
                b = stops[s + 1]
                break
        t = 0.0 if b[0] == a[0] else (v - a[0]) / (b[0] - a[0])
        c = tuple(int(a[1][i] + (b[1][i] - a[1][i]) * t) for i in range(3))
        alpha = a[1][3] if len(a[1]) > 3 else 255
        lut[k] = (c[0], c[1], c[2], alpha)
    return lut


LUT_IR = _build_lut([
    [300, (6, 10, 24)], [288, (10, 16, 32)], [282, (34, 42, 56)], [274, (72, 84, 74)],
    [264, (52, 128, 84)], [254, (110, 168, 64)], [244, (176, 184, 52)], [234, (214, 152, 44)],
    [224, (240, 96, 38)], [214, (244, 44, 32)], [204, (250, 110, 110)], [194, (255, 200, 200)],
    [184, (255, 255, 255)],
])
LUT_SST = _build_lut([
    [18, (8, 22, 66)], [23, (12, 46, 118)], [25.5, (20, 96, 168)], [26.5, (44, 150, 190)],
    [27.5, (66, 190, 170)], [28.5, (120, 210, 120)], [29.5, (220, 220, 90)],
    [30.5, (240, 160, 50)], [31.5, (235, 90, 40)],
])
# OHC(D26, km) 色标: 低→高
LUT_OHC = _build_lut([
    [20, (20, 30, 80)], [40, (40, 90, 170)], [60, (60, 160, 200)],
    [80, (110, 210, 140)], [100, (230, 220, 90)], [120, (250, 150, 50)],
    [140, (235, 80, 40)],
])

# 风场颜色 0→68 kt
_WIND_STOPS = [(0, (90, 200, 255)), (16, (120, 255, 160)), (31, (255, 240, 90)),
               (49, (255, 140, 40)), (68, (255, 60, 60))]


def _wind_color(sp_kt: float) -> tuple:
    sp_kt = min(max(sp_kt, 0.0), 68.0)   # 越界夹紧, 避免外插颜色越界
    a = _WIND_STOPS[0]
    b = _WIND_STOPS[-1]
    for s in range(len(_WIND_STOPS) - 1):
        if _WIND_STOPS[s][0] <= sp_kt <= _WIND_STOPS[s + 1][0]:
            a = _WIND_STOPS[s]
            b = _WIND_STOPS[s + 1]
            break
    t = 0.0 if b[0] == a[0] else (sp_kt - a[0]) / (b[0] - a[0] + 1e-9)
    return tuple(int(a[1][i] + (b[1][i] - a[1][i]) * t) for i in range(3))


_IR_T0, _IR_T1 = 300.0, 184.0


# 强度分级(Saffir-Simpson, kt)
_CMA_KT = [
    ('热带低压', 'TD', 21, (53, 196, 240)),
    ('热带风暴', 'TS', 34, (53, 240, 122)),
    ('一级飓风', 'C1', 64, (240, 224, 53)),
    ('二级飓风', 'C2', 83, (240, 160, 40)),
    ('三级飓风', 'C3', 96, (240, 100, 40)),
    ('四级飓风', 'C4', 113, (240, 53, 53)),
    ('五级飓风', 'C5', 137, (224, 53, 240)),
]


def grade_kt(v_kt: float) -> tuple:
    """返回 (中文名, 英文, 颜色)。v_kt: kt, SSHS 分级。"""
    g = _CMA_KT[0]
    for c in _CMA_KT:
        if v_kt >= c[2]:
            g = c
    return g


def cat_of_kt(v_kt: float) -> str:
    return grade_kt(v_kt)[1]


def cat_zh_kt(v_kt: float) -> str:
    """中国等级(简单图标集用): TD/TS/STS/TY/STY/SuperTY。"""
    if v_kt >= 96:
        return 'SuperTY'
    if v_kt >= 80:
        return 'STY'
    if v_kt >= 64:
        return 'TY'
    if v_kt >= 48:
        return 'STS'
    if v_kt >= 34:
        return 'TS'
    return 'TD'


def render_sat(sim, mode: str = 'IR') -> np.ndarray:
    """卫星云图 RGBA (CLDNY, CLDNX, 4)。mode: 'IR' | 'VIS'。"""
    cld = sim['cld'].reshape(V.CLDNY, V.CLDNX)
    out = np.zeros((V.CLDNY, V.CLDNX, 4), dtype=np.uint8)
    if mode == 'VIS':
        alb = 0.045 + 0.34 * np.power(cld, 1.08)
        v = np.clip(10 + 225 * alb, 0, 255).astype(np.uint8)
        out[..., 0] = v
        out[..., 1] = np.clip(14 + 224 * alb, 0, 255).astype(np.uint8)
        out[..., 2] = np.clip(26 + 216 * alb, 0, 255).astype(np.uint8)
        out[..., 3] = 255
        return out
    H = 1.5 + 13.0 * cld * cld * np.sqrt(cld)
    # TC 云顶叠加(向量化)
    LATc, LONc = _cld_grids(sim)
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        dx = (LONc - tc['lon']) * 111.32 * np.cos(LATc * V.C['DEG'])
        dy = (LATc - tc['lat']) * 110.57
        r = np.hypot(dx, dy)
        R = tc['rmw']
        v = tc['vmax']
        add = np.zeros_like(r)
        ew = np.exp(-((r - R) / (0.42 * R + 4)) ** 2)
        add += 7.5 * ew * min(1.0, v / 78.0)
        if tc['erc']:
            er = R * (1 + 0.55 * math.sin(math.pi * min(1.0, tc['erc']['t'] / tc['erc']['T'])))
            add += 4.5 * np.exp(-((r - er) / (0.35 * er + 5)) ** 2) * min(1.0, v / 107.0)
        band_mask = (r > R * 1.2) & (r < R * 9)
        if band_mask.any():
            phi = np.arctan2(dy, dx)
            sh = V.env_bilinear(sim['env']['shear'], tc['lon'], tc['lat']) + 5.8
            sh_dir = math.atan2(-0.28, -0.9)
            band0 = phi + 2.4 * np.log10(np.maximum(r, 1.0) / R) + (math.pi / 2) * math.sin(sim['t'] / 5)
            band = np.cos(band0 * 4) * 0.5 + 0.5
            spiral = np.power(band, 3) * np.exp(-((r / R - 4) / 3.2) ** 2)
            asym = 1 + 0.5 * np.cos(phi - (sh_dir + math.pi))
            add += 5.2 * spiral * asym * min(1.0, v / 87.0) * V.smoothstep(0, 23.3, sh)
        add = np.where(band_mask, add, 0.0)
        H = np.where(r < R * 0.45, 0.9, H + add)
    bt = np.clip(300 - 6.4 * H, 184, 300)
    idx = np.clip(((bt - 184.0) / (300.0 - 184.0) * 255.0).astype(int), 0, 255)
    out[..., :3] = LUT_IR[idx][..., :3]
    out[..., 3] = 255
    return out


_cld_grids_cache: dict = {}


def _cld_grids(sim):
    key = id(sim)
    g = _cld_grids_cache.get(key)
    if g is None:
        lats = np.linspace(V.C['LAT0'] + 0.5 * V.C['CLDD'],
                           V.C['LAT1'] - 0.5 * V.C['CLDD'], V.CLDNY)
        lons = np.linspace(V.C['LON0'] + 0.5 * V.C['CLDD'],
                           V.C['LON1'] - 0.5 * V.C['CLDD'], V.CLDNX)
        g = np.meshgrid(lats, lons, indexing='ij')
        if len(_cld_grids_cache) > 4:
            _cld_grids_cache.pop(next(iter(_cld_grids_cache)))
        _cld_grids_cache[key] = g
    return g


def render_env_layer(sim, name: str, alpha: int = 210) -> np.ndarray:
    """海温/热含量/湿度叠加层 RGBA (ENVNY, ENVNX, 4), 半透明; 陆地以外显示。"""
    arr = sim['env'][name]
    out = np.zeros((V.ENVNY, V.ENVNX, 4), dtype=np.uint8)
    if name == 'sst':
        t = np.clip((arr - 18.0) / 13.5, 0, 1) * 255.0
        idx = t.astype(int)
        out[..., :3] = LUT_SST[idx][..., :3]
        out[..., 3] = alpha
    elif name == 'ohc':
        t = np.clip((arr - 20.0) / 120.0, 0, 1) * 255.0
        idx = t.astype(int)
        out[..., :3] = LUT_OHC[idx][..., :3]
        out[..., 3] = alpha
    else:  # rh
        a = np.clip((arr - 45.0) / 45.0, 0, 1)
        out[..., 0] = 40
        out[..., 1] = 220
        out[..., 2] = 120
        out[..., 3] = (a * 130).astype(np.uint8)
    # 陆地以外显示: 陆地像素透明度置 0
    land = sim['land']
    if land.shape == out.shape[:2]:
        out[..., 3] = (out[..., 3] * (land < 0.5)).astype(np.uint8)
    return out


def draw_contours(surface, sim, fx: Callable, fy: Callable,
                  levels: Optional[List[float]] = None) -> None:
    """气压等值线(marching squares) + 高/低标记。"""
    if levels is None:
        levels = [976, 980, 984, 988, 992, 996, 1000, 1004, 1008, 1012, 1016, 1020, 1024, 1028]
    p = sim['env']['pTot']
    ny, nx = V.ENVNY, V.ENVNX
    segs = []
    for level in levels:
        v00 = p[:-1, :-1]
        v10 = p[:-1, 1:]
        v01 = p[1:, :-1]
        v11 = p[1:, 1:]
        mn = np.minimum(np.minimum(v00, v10), np.minimum(v01, v11))
        mx = np.maximum(np.maximum(v00, v10), np.maximum(v01, v11))
        mask = (level >= mn) & (level <= mx)
        jj, ii = np.where(mask)
        for j, i in zip(jj, ii):
            a00, a10, a01, a11 = v00[j, i], v10[j, i], v01[j, i], v11[j, i]
            lon0 = V.C['LON0'] + (i + 0.5) * V.C['ENVD']
            lon1 = V.C['LON0'] + (i + 1.5) * V.C['ENVD']
            lat0 = V.C['LAT0'] + (j + 0.5) * V.C['ENVD']
            lat1 = V.C['LAT0'] + (j + 1.5) * V.C['ENVD']
            x0, y0 = fx(lon0), fy(lat0)
            x1, y1 = fx(lon1), fy(lat1)
            pts = []
            if (a00 <= level) != (a10 <= level):
                t = (level - a00) / (a10 - a00 + 1e-9)
                pts.append((x0 + t * (x1 - x0), y0))
            if (a00 <= level) != (a01 <= level):
                t = (level - a00) / (a01 - a00 + 1e-9)
                pts.append((x0, y0 + t * (y1 - y0)))
            if (a10 <= level) != (a11 <= level):
                t = (level - a10) / (a11 - a10 + 1e-9)
                pts.append((x1, y0 + t * (y1 - y0)))
            if (a01 <= level) != (a11 <= level):
                t = (level - a01) / (a11 - a01 + 1e-9)
                pts.append((x0 + t * (x1 - x0), y1))
            if len(pts) == 2:
                segs.append(pts)
    if segs:
        for p0, p1 in segs:
            pygame.draw.line(surface, (200, 205, 215), p0, p1, 1)
    # 高/低标记
    for j in range(1, ny - 1):
        for i in range(1, nx - 1):
            v = p[j, i]
            nb = [p[j, i - 1], p[j, i + 1], p[j - 1, i], p[j + 1, i]]
            if v < min(nb) - 0.4:
                x, y = fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD']), fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD'])
                surface.blit(_label('低', (120, 200, 255)), (int(x), int(y)))
            elif v > max(nb) + 0.4:
                x, y = fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD']), fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD'])
                surface.blit(_label('高', (255, 150, 80)), (int(x), int(y)))


_label_cache: dict = {}
_label_fn = None
_FALLBACK_FONT = None


def set_label_fn(fn) -> None:
    """注入文本渲染函数 label(text, color) -> Surface(通常为 app.constants 的 rt(f_s,...))。
    未注入时用内置 pygame 字体回退, 使 simcore 渲染不依赖 app 层。"""
    global _label_fn
    _label_fn = fn


def _label(text: str, color) -> pygame.Surface:
    global _FALLBACK_FONT
    fn = _label_fn
    key = (text, color, fn is not None)
    s = _label_cache.get(key)
    if s is None:
        if fn is not None:
            s = fn(text, color)
        else:
            if _FALLBACK_FONT is None:
                try:
                    _FALLBACK_FONT = pygame.font.SysFont('microsoftyahei', 18)
                except Exception:
                    _FALLBACK_FONT = pygame.font.Font(None, 18)   # 内建字体, 稳定兜底
            s = _FALLBACK_FONT.render(text, True, color)
        if len(_label_cache) > 64:
            _label_cache.pop(next(iter(_label_cache)))
        _label_cache[key] = s
    return s


def draw_barbs(surface, sim, fx: Callable, fy: Callable, step: float = 5.0) -> None:
    """风羽(每 step 度一个, kt: 整羽 50kt/半羽 10kt/小羽 5kt)。"""
    for lat in np.arange(-55, 55, step):
        for lon in np.arange(0, 360, step):
            u, v = V.wind_at(sim, lon, lat, 0)
            sp = math.hypot(u, v)
            if sp < 2.5:
                continue
            x, y = fx(lon), fy(lat)
            ang = math.atan2(-v, -u)
            ex, ey = math.cos(ang), math.sin(ang)
            px_ = -math.sin(ang)
            py_ = math.cos(ang)
            L = 13
            pygame.draw.line(surface, (220, 225, 235), (x, y), (x + ex * L, y + ey * L), 1)
            rem = sp
            d = 0
            while rem >= 47.5 and d < 3:
                p0 = (x + ex * (L - d * 5), y + ey * (L - d * 5))
                p1 = (x + ex * (L - d * 5 - 6) + px_ * 6, y + ey * (L - d * 5 - 6) + py_ * 6)
                pygame.draw.polygon(surface, (220, 225, 235), [p0, p1, (x + ex * L, y + ey * L)], 1)
                d += 1
                rem -= 50
            while rem >= 7.5 and d < 8:
                p0 = (x + ex * (L - d * 5), y + ey * (L - d * 5))
                p1 = (x + ex * (L - d * 5 - 6) + px_ * 5, y + ey * (L - d * 5 - 6) + py_ * 5)
                pygame.draw.line(surface, (220, 225, 235), p0, p1, 1)
                d += 1
                rem -= 10
            if rem >= 2.5 and d < 8:
                p0 = (x + ex * (L - d * 5), y + ey * (L - d * 5))
                p1 = (x + ex * (L - d * 5 - 4) + px_ * 3, y + ey * (L - d * 5 - 4) + py_ * 3)
                pygame.draw.line(surface, (220, 225, 235), p0, p1, 1)


class Particles:
    """风场粒子(流线)。"""

    def __init__(self, n: int = 1200):
        self.n = n
        self.lon = np.random.uniform(V.C['LON0'], V.C['LON1'], n)
        self.lat = np.random.uniform(V.C['LAT0'], V.C['LAT1'], n)
        self.ol = self.lon.copy()
        self.oa = self.lat.copy()
        self.u = np.zeros(n)
        self.v = np.zeros(n)

    def step(self, sim, dt_h: float) -> None:
        for k in range(self.n):
            u, v = V.wind_at(sim, self.lon[k], self.lat[k], 0)
            self.ol[k] = self.lon[k]
            self.oa[k] = self.lat[k]
            self.u[k] = u
            self.v[k] = v
            self.lon[k] += u * 3.6 * dt_h / (111.32 * math.cos(self.lat[k] * V.C['DEG']))
            self.lat[k] += v * 3.6 * dt_h / 110.57
            if (self.lon[k] < V.C['LON0'] + 0.5 or self.lon[k] > V.C['LON1'] - 0.5
                    or self.lat[k] < V.C['LAT0'] + 0.5 or self.lat[k] > V.C['LAT1'] - 0.5
                    or V.land_at(sim, self.lon[k], self.lat[k]) > 0.4):
                self.lon[k] = V.C['LON0'] + np.random.random() * (V.C['LON1'] - V.C['LON0'])
                self.lat[k] = V.C['LAT0'] + np.random.random() * (V.C['LAT1'] - V.C['LAT0'])
                self.ol[k] = self.lon[k]
                self.oa[k] = self.lat[k]
                self.u[k] = 0.0
                self.v[k] = 0.0

    def draw(self, surface, fx: Callable, fy: Callable) -> None:
        for k in range(self.n):
            sp = math.hypot(self.u[k], self.v[k])
            x0, y0 = fx(self.ol[k]), fy(self.oa[k])
            x1, y1 = fx(self.lon[k]), fy(self.lat[k])
            if abs(x1 - x0) < 60 and abs(y1 - y0) < 60 and sp >= 0.5:
                pygame.draw.line(surface, _wind_color(sp), (x0, y0), (x1, y1), 1)


def draw_forecast(surface, sim, sel_tc, fx: Callable, fy: Callable,
                  style: str = 'JTWC') -> None:
    """预报路径: 路径线 + 误差圈(集合成员散布), 样式可选 JMA/JTWC。
    JMA: 实线 + 24h 空心圆点; JTWC: 实线 + 24h 实心圆点。"""
    ens = sim.get('ensemble')
    if not ens or not ens.get('members') or sel_tc is None:
        return
    members = ens['members']
    hours = ens['hours']
    mean_pts = []
    radii = []
    for h in range(0, hours + 1, 6):
        xs = []
        ys = []
        for m in members:
            pt = m.get(h)
            if pt is None:
                continue
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        if len(xs) < 3:
            continue
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        rkm = 0.0
        for x, y in zip(xs, ys):
            dx = (x - cx) * 111.32 * math.cos(cy * V.C['DEG'])
            dy = (y - cy) * 110.57
            rkm += math.hypot(dx, dy)
        rkm /= len(xs)
        mean_pts.append((cx, cy, h))
        radii.append(rkm * 1.25)
    if len(mean_pts) < 2:
        return
    # 路径线
    pts = [(float(fx(m[0])), float(fy(m[1]))) for m in mean_pts]
    pygame.draw.lines(surface, (255, 255, 255), False, pts, 2)
    # 误差圈(细线, 半径随预报时长增大)
    for (cx, cy, h), rkm in zip(mean_pts, radii):
        cos_lat = math.cos(cy * V.C['DEG'])
        rxp = rkm / 111.32 / max(1e-6, cos_lat)
        ryp = rkm / 110.57
        rect = pygame.Rect(float(fx(cx)) - rxp, float(fy(cy)) - ryp,
                           2 * rxp, 2 * ryp)
        pygame.draw.ellipse(surface, (140, 195, 255), rect, 1)
    # 24h 标记
    hollow = (style == 'JMA')
    for (cx, cy, h) in mean_pts:
        if h % 24 != 0:
            continue
        x, y = int(fx(cx)), int(fy(cy))
        if hollow:
            pygame.draw.circle(surface, (255, 255, 255), (x, y), 6, 2)
        else:
            pygame.draw.circle(surface, (255, 255, 255), (x, y), 4)
        surface.blit(_label(f"{h}h", (255, 255, 255)), (x + 8, y - 8))


def draw_tracks(surface, sim, sel_id, fx: Callable, fy: Callable) -> None:
    for tc in sim['tcs']:
        if tc['dead'] or len(tc['track']) < 2:
            continue
        sel = tc['id'] == sel_id
        pts = [(fx(q['lon']), fy(q['lat'])) for q in tc['track']]
        if len(pts) > 1:
            pygame.draw.lines(surface, grade_kt(tc['vmax'])[2],
                              False, pts, 3 if sel else 2)


def draw_tc_hud(surface, sim, sel_id, fx: Callable, fy: Callable,
                icon_fn: Optional[Callable] = None,
                quadrant: bool = True, show_rings: bool = True,
                show_labels: bool = True) -> None:
    """台风 HUD: 图标(回调) + 名称/强度(kt)/气压标签 + 风圈(可四象限) + 眼墙。"""
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        x, y = fx(tc['lon']), fy(tc['lat'])
        sel = tc['id'] == sel_id
        cos_lat = math.cos(tc['lat'] * V.C['DEG'])
        v_kt = tc['vmax']          # 全 kt
        # 风圈 34/50/64 kt(可切换四象限模式)
        if show_rings:
            for vv_kt, col, dash in ((34, (120, 220, 255), (3, 3)),
                                     (50, (255, 210, 80), (4, 4)),
                                     (64, (255, 100, 80), (5, 5))):
                rkm = V.radius_at_wind(tc, vv_kt)
                if rkm < 5:
                    continue
                if quadrant:
                    _draw_quadrant_ring(surface, tc, x, y, rkm, cos_lat, col)
                else:
                    rxp = rkm / 111.32 / cos_lat
                    ryp = rkm / 110.57
                    _draw_ellipse_dashed(surface, (x, y), rxp, ryp, col, dash)
        # 眼墙
        pygame.draw.ellipse(surface, (255, 255, 255) if sel else (220, 220, 230),
                            pygame.Rect(x - tc['rmw'] / 111.32 / cos_lat,
                                        y - tc['rmw'] / 110.57,
                                        2 * tc['rmw'] / 111.32 / cos_lat,
                                        2 * tc['rmw'] / 110.57), 2)
        g = grade_kt(v_kt)
        if icon_fn is not None:
            # 由外部(模拟模式 mixin)按 app 图标集渲染台风图标
            icon_fn(surface, tc, x, y, sel)
        else:
            pygame.draw.circle(surface, g[2], (int(x), int(y)), 7 if sel else 5, 0)
            pygame.draw.circle(surface, (255, 255, 255), (int(x), int(y)),
                               7 if sel else 5, 1)
        if not show_labels:
            continue
        label = f"{tc['name']} {v_kt:.0f}kt {tc['pmin']:.0f}hPa"
        surface.blit(_label(label, (255, 255, 255)), (x + 10, y - 10))


def _draw_ellipse_dashed(surface, center, rx, ry, color, dash) -> None:
    if rx < 3 or ry < 3:
        return
    rect = pygame.Rect(center[0] - rx, center[1] - ry, rx * 2, ry * 2)
    steps = max(24, int(2 * math.pi * max(rx, ry) / 8))
    pts = []
    for k in range(steps):
        a = 2 * math.pi * k / steps
        pts.append((rect.centerx + rx * math.cos(a), rect.centery + ry * math.sin(a)))
    for k in range(0, len(pts) - 1, 2):
        pygame.draw.line(surface, color, pts[k], pts[k + 1], 1)


def _draw_quadrant_ring(surface, tc, x, y, rkm, cos_lat, color) -> None:
    """四象限风圈: 沿移动方向右前象限半径最大(1.15), 逆时针递减
    (右前1.15 / 右后1.05 / 左后0.95 / 左前0.85), 每象限 90° 弧。"""
    ang = math.atan2(tc['mv'], tc['mu'])
    factors = (1.15, 1.05, 0.95, 0.85)   # 从前象限起, 按运动方向旋转
    for qi in range(4):
        qr = rkm * factors[qi]
        rxp = qr / 111.32 / max(1e-6, cos_lat)
        ryp = qr / 110.57
        a0 = ang + (qi * 90 - 45) * V.C['DEG']
        pts = []
        for k in range(13):
            a = a0 + k * (90 * V.C['DEG'] / 12)
            pts.append((x + rxp * math.cos(a), y - ryp * math.sin(a)))
        pygame.draw.lines(surface, color, False, pts, 1)


def draw_ridge(surface, sim, fx: Callable, fy: Callable) -> None:
    """副高: 完整等值线(5880gpm 近似, 以 pEnv≥1016hPa 区域表示) + 内部半透明橙色填充。"""
    try:
        p = sim['env']['pEnv']
        ny, nx = p.shape
        th = 1016.0
        mask = p >= th
        if not mask.any():
            return
        # 内部填充(半透明橙色); 经度 0/360 同列, 宽度用 2×(fx(180)-fx(0)) 估算
        fill_surf = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        col0 = fx(V.C['LON0'])
        col1 = col0 + 2.0 * (fx(180.0) - fx(V.C['LON0']))
        row0 = fy(V.C['LAT1'])
        row1 = fy(V.C['LAT0'])
        if col1 - col0 < 4 or row1 - row0 < 4:
            return
        small = pygame.Surface((nx, ny), pygame.SRCALPHA)
        px = np.zeros((ny, nx, 4), dtype=np.uint8)
        px[..., 0] = 255
        px[..., 1] = 150
        px[..., 2] = 40
        px[..., 3] = np.where(mask, 90, 0).astype(np.uint8)
        try:
            pygame.surfarray.pixels_alpha(small)[:] = px[..., 3]
            pygame.surfarray.blit_array(small, np.transpose(px[..., :3], (1, 0, 2)))
        except Exception:
            small.set_alpha(90)
        fill_surf.blit(pygame.transform.smoothscale(
            small, (max(2, int(col1 - col0)), max(2, int(row1 - row0)))),
            (col0, row0))
        # 描边: 掩码边界像素画橙色线(粗 2px)
        edge = np.zeros_like(mask)
        edge[1:-1, 1:-1] = mask[1:-1, 1:-1] & ~(mask[:-2, 1:-1] & mask[2:, 1:-1]
                                                & mask[1:-1, :-2] & mask[1:-1, 2:])
        ys, xs = np.nonzero(edge)
        for j, i in zip(ys[::2], xs[::2]):
            lon = V.C['LON0'] + (i + 0.5) * V.C['ENVD']
            lat = V.C['LAT0'] + (j + 0.5) * V.C['ENVD']
            pygame.draw.circle(surface, (255, 150, 60), (int(fx(lon)), int(fy(lat))), 2)
        surface.blit(fill_surf, (0, 0))
        surface.blit(_label('副高 588', (255, 170, 80)),
                     (int(fx(120)), int(fy(33))))
    except Exception:
        pass


def draw_grid(surface, fx: Callable, fy: Callable) -> None:
    for lon in range(0, 360, 20):
        x = fx(lon)
        pygame.draw.line(surface, (255, 255, 255, 26), (x, fy(-60)), (x, fy(60)), 1)
    for lat in range(-60, 61, 20):
        y = fy(lat)
        pygame.draw.line(surface, (255, 255, 255, 26), (fx(0), y), (fx(360), y), 1)


def render_map(surface, sim, fx: Callable, fy: Callable,
               layers: set, sel_tc=None, mode: str = 'IR',
               particles: Optional[Particles] = None,
               icon_fn: Optional[Callable] = None) -> None:
    """把 SimCore 各图层叠加绘制到 surface(已有地图底图)上。

    fx/fy: 经纬度 → surface 像素(来自项目地图视图, 实现叠加而非覆盖)。
    icon_fn: 可选, callable(surface, tc, x, y, sel) — 台风图标渲染(复用 app 图标集)。"""
    # 云图(作为底图背景覆盖计算域, 半透明与地图融合; 仅图层开启时绘制)
    if 'cloud' in layers:
        sat = render_sat(sim, mode)
        surf = pygame.surfarray.make_surface(np.transpose(sat[..., :3], (1, 0, 2)))
        w = int(fx(V.C['LON1']) - fx(V.C['LON0']))
        h = int(fy(V.C['LAT0']) - fy(V.C['LAT1']))
        if w > 0 and h > 0:
            surf = pygame.transform.smoothscale(surf, (w, h))
            surf.set_alpha(235)
            surface.blit(surf, (fx(V.C['LON0']), fy(V.C['LAT1'])))
    # 海温 / 热含量 / 湿度
    if 'sst' in layers:
        arr = render_env_layer(sim, 'sst')
        _blit_layer(surface, arr, fx, fy, alpha=210)
    if 'ohc' in layers:
        arr = render_env_layer(sim, 'ohc')
        _blit_layer(surface, arr, fx, fy, alpha=190)
    if 'rh' in layers:
        arr = render_env_layer(sim, 'rh')
        _blit_layer(surface, arr, fx, fy)
    # 气压
    if 'pressure' in layers:
        try:
            draw_contours(surface, sim, fx, fy)
        except Exception:
            pass
    # 副高 5880 线(近似: pEnv 等值线 + 内部橙色填充)
    if 'ridge' in layers:
        try:
            draw_ridge(surface, sim, fx, fy)
        except Exception:
            pass
    # 风场
    if 'wind' in layers:
        if particles is not None:
            particles.draw(surface, fx, fy)
        try:
            if sim.get('_show_barbs', True):
                draw_barbs(surface, sim, fx, fy)
        except Exception:
            pass
    # 路径 + 预报(误差圈模式, 样式可选 JMA/JTWC)
    # 注意: 台风路径本体由模拟模式 mixin 用其它模式的点阵/渐变线管线绘制,
    # 此处仅画预报误差圈
    if 'track' in layers:
        try:
            if sim.get('_show_forecast', True):
                draw_forecast(surface, sim, sel_tc, fx, fy,
                              style=sim.get('_fcst_style', 'JTWC'))
        except Exception:
            pass
    try:
        draw_tc_hud(surface, sim, sel_tc['id'] if sel_tc else None, fx, fy,
                    icon_fn,
                    quadrant=bool(sim.get('_quad_rings', True)),
                    show_rings=bool(sim.get('_show_rings', True)),
                    show_labels=bool(sim.get('_show_labels', True)))
    except Exception:
        pass


def _blit_layer(surface, rgba: np.ndarray, fx: Callable, fy: Callable,
                alpha: int = 255) -> None:
    surf = pygame.surfarray.make_surface(np.transpose(rgba[..., :3], (1, 0, 2)))
    surf = pygame.transform.smoothscale(
        surf, (int(fx(V.C['LON1']) - fx(V.C['LON0'])),
               int(fy(V.C['LAT0']) - fy(V.C['LAT1']))))
    surf.set_alpha(alpha)
    surface.blit(surf, (fx(V.C['LON0']), fy(V.C['LAT1'])))
