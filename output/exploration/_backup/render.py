# simulator.simcore/render.py
"""SimCore 渲染移植(pygame): 卫星云图 IR/VIS、气压等值线、风羽/粒子、
海温/湿度叠加、集合预报锥、路径/风圈/眼墙/标签、副高脊线。

设计: 所有图层绘制到传入的地图 surface 上, 经纬度→像素由外部提供
(fx, fy 函数), 实现"叠加在现有地图上"而非全屏覆盖。
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Dict, List, Optional

import numpy as np
import pygame

from . import core as V

logger = logging.getLogger(__name__)

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
            # stops 可能按数值降序排列(如 LUT_IR 300→184): 用排序后的上下界判区间,
            # 否则区间永远不命中, 整条色标退化成首末色的灰度
            v0, v1 = stops[s][0], stops[s + 1][0]
            if (v0 <= v <= v1) or (v1 <= v <= v0):
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
    # 用户 2026-09-19: 生成过程要看得见 DB → TD → TS, 所以补上扰动(DB)档;
    # 21-33kt 才是热带低压(TD), 34kt 起才算命名风暴(TS)。
    ('热带扰动', 'DB', 0, (150, 200, 220)),
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


# ── 云图表面缓存 ────────────────────────────────────────────────
# render_sat 是整幅云场的逐格变换(含 exp/power/log10), 实测 ~95ms/次, 再加
# smoothscale 到屏幕尺寸 ~23ms。云场只在 cloud_step 时变化, 台风参数每帧只动
# 千分之几度 —— 量化后缓存整块"已缩放"表面; 另按墙钟限流, 避免 8x 加速时
# 仍每帧重建(那时模拟时间推进快于墙钟, 版本号每帧都变)。
_cloud_surf_cache: dict = {}
_CLOUD_SURF_CACHE_MAX = 6
_CLOUD_MIN_INTERVAL_MS = 300
_cloud_last: list = [None, 0, 0, 0]        # [surf, made_ms, w, h]

_layer_surf_cache: dict = {}
_LAYER_SURF_CACHE_MAX = 12


class GridMap:
    """环境格点 → 屏幕的**仿射**映射(不做经度环绕翻面)。

    为什么必须与点坐标用的 fx/fy 分开(app 的 geo_to_screen 会对每个点单独
    按"离屏幕中心最近的副本"翻面):

      · 整张 0.5° 格点场走 fx/fy 时, fx(0) 与 fx(360) 会解到同一个像素
        (lon % 360 + 环绕), 于是贴图宽度算成 **0** —— 实测 sst/ohc 整层
        永远画不出来(平均像素差 0.12/0.20), 风切/气压/流线只剩零头;
      · 逐点翻面还让阈值附近的格点每帧跳一个图宽 —— 风羽"闪烁且不固定"。

    这里换成: 先按**未环绕**的仿射坐标算全部格点, 再整体挑一个离屏幕最近的
    环绕副本(k 一次算好, 全场共用)。参数由 app 侧按当前地图视图给出。
    """

    __slots__ = ('lon0', 'lon1', 'lat0', 'lat1', 'x0', 'x1', 'y0', 'y1',
                 'wrap', 'clip')

    def __init__(self, lon0: float, lon1: float, lat0: float, lat1: float,
                 x0: float, x1: float, y0: float, y1: float,
                 wrap: float = 0.0, clip: tuple = (0, 0, 0, 0)) -> None:
        self.lon0, self.lon1 = float(lon0), float(lon1)
        self.lat0, self.lat1 = float(lat0), float(lat1)
        self.wrap = float(wrap or 0.0)
        self.clip = tuple(clip) if clip else (0, 0, 0, 0)
        if self.wrap > 1.0:
            cw = self.clip[2] if self.clip[2] > 0 else 1.0
            cx = self.clip[0] + cw / 2.0
            k = round((cx - (x0 + x1) / 2.0) / self.wrap)
            x0 += k * self.wrap
            x1 += k * self.wrap
        self.x0, self.x1 = float(x0), float(x1)
        self.y0, self.y1 = float(y0), float(y1)

    # ── 坐标变换 ──
    def x(self, lon: float) -> float:
        d = self.lon1 - self.lon0
        if d == 0:
            return self.x0
        return self.x0 + (float(lon) - self.lon0) * (self.x1 - self.x0) / d

    def y(self, lat: float) -> float:
        d = self.lat1 - self.lat0
        if d == 0:
            return self.y0
        return self.y1 + (float(lat) - self.lat0) * (self.y0 - self.y1) / d

    def xs(self, lons):
        """批量(保留给逐点循环, 语义同 x())。"""
        return [self.x(v) for v in lons]

    def px_per_deg_x(self) -> float:
        d = self.lon1 - self.lon0
        return abs((self.x1 - self.x0) / d) if d else 0.0

    def visible(self, size) -> Optional[tuple]:
        """屏幕可见矩形 (vx0, vy0, vx1, vy1); 完全不可见时 None。"""
        W, H = int(size[0]), int(size[1])
        if self.clip[2] > 0:
            W = min(W, self.clip[0] + self.clip[2])
            H = min(H, self.clip[1] + self.clip[3])
        vx0 = max(0, int(math.floor(min(self.x0, self.x1))))
        vx1 = min(W, int(math.ceil(max(self.x0, self.x1))))
        vy0 = max(0, int(math.floor(min(self.y0, self.y1))))
        vy1 = min(H, int(math.ceil(max(self.y0, self.y1))))
        if vx1 - vx0 < 2 or vy1 - vy0 < 2:
            return None
        return (vx0, vy0, vx1, vy1)

    def src_window(self, nx: int, ny: int, vis: tuple):
        """可见矩形对应的**源格窗口**与它在屏幕上的落点。

        返回 (c0, r0, c1, r1, dst_rect)。
        """
        vx0, vy0, vx1, vy1 = vis
        xa, xb = min(self.x0, self.x1), max(self.x0, self.x1)
        ya, yb = min(self.y0, self.y1), max(self.y0, self.y1)
        dx = max(1e-9, xb - xa)
        dy = max(1e-9, yb - ya)
        u0, u1 = (vx0 - xa) / dx, (vx1 - xa) / dx
        w0, w1 = (vy0 - ya) / dy, (vy1 - ya) / dy
        c0 = max(0, min(nx - 1, int(math.floor(u0 * nx))))
        c1 = max(c0 + 1, min(nx, int(math.ceil(u1 * nx))))
        r0 = max(0, min(ny - 1, int(math.floor(w0 * ny))))
        r1 = max(r0 + 1, min(ny, int(math.ceil(w1 * ny))))
        sx0 = xa + (c0 / nx) * dx
        sx1 = xa + (c1 / nx) * dx
        sy0 = ya + (r0 / ny) * dy
        sy1 = ya + (r1 / ny) * dy
        dst = pygame.Rect(int(round(sx0)), int(round(sy0)),
                          max(1, int(round(sx1 - sx0))),
                          max(1, int(round(sy1 - sy0))))
        return c0, r0, c1, r1, dst


def _tc_signature(sim):
    """台风参数签名(量化): 位置 0.1 度(≈11km, 1280px 全图约 0.35px)、强度 1kt。"""
    sig = []
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        erc = tc.get('erc')
        sig.append((round(float(tc['lon']), 1), round(float(tc['lat']), 1),
                    round(float(tc['vmax']), 1), round(float(tc['rmw']), 1),
                    round(float(erc['t']) / max(1e-6, float(erc['T'])), 2)
                    if erc else -1.0))
    return tuple(sig)


def _cloud_surface(sim, mode: str, w: int, h: int) -> pygame.Surface:
    """已缩放的云图表面(命中缓存时 ~0ms)。"""
    key = (mode, int(sim.get('_cldVer', -1)), round(float(sim['t']) * 4.0),
           _tc_signature(sim), w, h)
    surf = _cloud_surf_cache.get(key)
    if surf is not None:
        return surf
    now = pygame.time.get_ticks()
    last, made, lw, lh = _cloud_last
    if last is not None and lw == w and lh == h and now - made < _CLOUD_MIN_INTERVAL_MS:
        return last                       # 限流: 沿用上一幅(观感无差)
    sat = np.flipud(render_sat(sim, mode))      # 同上: 云网格也是南在上
    surf = pygame.surfarray.make_surface(np.transpose(sat[..., :3], (1, 0, 2)))
    surf = pygame.transform.smoothscale(surf, (max(1, w), max(1, h)))
    surf.set_alpha(235)
    if len(_cloud_surf_cache) >= _CLOUD_SURF_CACHE_MAX:
        _cloud_surf_cache.clear()
    _cloud_surf_cache[key] = surf
    _cloud_last[:] = [surf, now, w, h]
    return surf


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
            # 只对雨带项做掩膜: 原写法 add=np.where(band_mask,add,0) 会把已累加的
            # 眼墙/ERC 环(r<=1.2R)一并清零
            add += np.where(
                band_mask,
                5.2 * spiral * asym * min(1.0, v / 87.0) * V.smoothstep(0, 23.3, sh),
                0.0)
        H = np.where(r < R * 0.45, 0.9, H + add)
    bt = np.clip(300 - 6.4 * H, 184, 300)
    # LUT_IR[0]=300K(暖/暗) → [255]=184K(冷/白): 索引必须随 bt 下降而增大,
    # 原式方向反了(晴空变白、深对流变灰)
    idx = np.clip(((300.0 - bt) / (300.0 - 184.0) * 255.0).astype(int), 0, 255)
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


import os

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HI_LAND = None
_HI_LAND_TRIED = False


def _hi_land_mask():
    """高分辨率陆地掩码 (1080,2160): 直接读 map/heightmap.png 现算, 不另存 npy。

    heightmap 是"北在上"(与 map.png 一致), 而 env 网格 row0 = LAT0(南在上),
    所以读入后上下翻转一次; 再 [::5,::5] 降到 1080x2160 与图层升采样对齐。
    高程编码: 像素值 = 高程(米) + 12000, 故 陆地 <=> 值 > 12000。
    """
    global _HI_LAND, _HI_LAND_TRIED
    if not _HI_LAND_TRIED:
        _HI_LAND_TRIED = True
        try:
            from PIL import Image
            p = os.path.join(_ROOT_DIR, 'map', 'heightmap.png')
            if os.path.exists(p):
                # 只取 lat -60..60 那一段(heightmap 共 -90..90, 5400 行):
                # 计算域是 -60..60, 若整幅拿去当掩码, 1080 行会被压缩进 -60..60,
                # 陆地就跑到错误纬度上(南半球的轮廓出现在北半球)
                band = np.asarray(Image.open(p))[900:4500]      # 60N..60S
                _HI_LAND = np.flipud(band)[::5, ::5] > 12000    # (720, 2160)
        except Exception:
            _HI_LAND = None
    return _HI_LAND


#: 风切色标上限(kt)。用户 2026-09-20 要求风切用**瞬时**值后, 瞬时场 95 分位就有
#: ~95 kt、急流核能到 150+ —— 原来的 0-60 上限把半个地球画成同一片红(饱和)。
#: 现在 0-100, 结构与梯度都还看得见(超 100 才截顶)。
SHEAR_LUT_MAX_KT = 100.0
LUT_SHEAR = _build_lut([
    (0.00, (24, 44, 120)),      # 弱切变: 深蓝
    (0.20, (40, 150, 210)),     # 青
    (0.40, (90, 210, 140)),     # 绿
    (0.60, (240, 210, 90)),     # 黄
    (0.80, (235, 130, 60)),     # 橙
    (1.00, (225, 70, 60)),      # 强切变: 红
])


def _smooth_field(rgba: np.ndarray, sigma: float = 3.5) -> np.ndarray:
    """把最近邻放大的"方块感"磨平(只平滑 RGB, alpha 保持不动)。

    sigma 默认 3.5 对应 6 倍最近邻(每 6 像素一格); 内核切到 0.5° 后升采样倍率
    变 3, 调用方按倍率缩放 sigma, 否则会把真实结构一起糊掉。
    """
    try:
        from scipy.ndimage import gaussian_filter
        rgb = gaussian_filter(rgba[..., :3].astype(np.float32), sigma=(3.5, 3.5, 0))
        return np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), rgba[..., 3]])
    except Exception:
        return rgba


def _build_step_lut(colors):
    """离散分档 LUT: 256 索引 -> N 档等宽颜色(参考观测产品那种"阶梯"配色)。"""
    n = len(colors)
    idx = np.clip((np.arange(256) * n) // 256, 0, n - 1)
    return np.array([colors[i] for i in idx], dtype=np.uint8)


def _hex(c):
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


#: SST 分档(1°C 一档, 0..33°C), 颜色逐档取自用户给的 REMSS MW-IR 产品色标
SST_STEPS = [_hex(c) for c in (
    "#dfe3ef", "#cbcee7", "#c2c3e7", "#adaede", "#9d9ad4", "#8885cf",
    "#7573c5", "#5f5ebc", "#4d49b5", "#003b9d", "#0748ad", "#1153bf",
    "#175fce", "#1f6add", "#206ce2", "#2c8ad8", "#3aa8dd", "#6bd5e6",
    "#6dd5e6", "#3fc9c0", "#70eaa3", "#6eea9e", "#53d479", "#38c050",
    "#1bab28", "#019501", "#f2b000", "#e58000", "#da5201", "#c20305",
    "#d22b58", "#e454aa", "#e96bd4", "#ec5ede")]
#: OHC 分档(10 kJ/cm² 一档, 0..220), 取自 CMEMS OHC 产品色标
#: (110~200 段的色标被刻度文字遮挡, 按红->粉->品红插值)
OHC_STEPS = [_hex(c) for c in (
    "#01c7c8", "#01cfa8", "#00d18d", "#17d67a", "#5fdd4f", "#a3e52f",
    "#c8e02f", "#e6db2f", "#e7c530", "#e7ae30", "#e8952f", "#e87a2c",
    "#e85d28", "#e84025", "#e62222", "#dc1435", "#d2104a", "#c80e5f",
    "#bd0c72", "#b60a80", "#c00a86", "#d40c7f", "#e01a78")]
LUT_SST_STEPS = _build_step_lut(SST_STEPS)
LUT_OHC_STEPS = _build_step_lut(OHC_STEPS)


_env_arr_cache: dict = {}
_ENV_ARR_CACHE_MAX = 8

#: 图层显示域: 内核只算 ±60, 但地图能看到两极 —— 外推补齐, 否则极区是空白
LAYER_LAT0, LAYER_LAT1 = -90.0, 90.0


_ALL_LAND_CACHE = {'tried': False, 'mask': None}


def _all_land_hi(shape, ry: int, rx: int):
    """100% 陆地格点掩码升采样到高分辨率画布尺寸(缺失返回 None)。

    缓存在 simulator/env/model/land_cell.npz(tools/make_cell_land_mask.py 生成),
    与内核 all_land_mask() 同一份文件 —— 检查一次, 显示与模拟共用。
    """
    if not _ALL_LAND_CACHE['tried']:
        _ALL_LAND_CACHE['tried'] = True
        try:
            p = os.path.join(_ROOT_DIR, 'simulator', 'env', 'model', 'land_cell.npz')
            with np.load(p) as z:
                _ALL_LAND_CACHE['mask'] = np.asarray(z['all_land'])
        except Exception:
            logger.debug('land_cell.npz 缺失, 图层退回逐像素海岸线掩码', exc_info=True)
    m = _ALL_LAND_CACHE['mask']
    if m is None:
        return None
    big = np.repeat(np.repeat(m, ry, axis=0), rx, axis=1)
    if big.shape != tuple(shape):
        return None
    return big


def _pad_poles(arr: np.ndarray, lat0: float = None, lat1: float = None) -> np.ndarray:
    """纬度方向外推填充: 把**不足 ±90** 的图层补到 ±90, 让两极也有数据。

    用户 2026-09-19: "两极没有海温等数据"。这里用**边缘梯度线性外推**
    (不是常数填充 —— 常数会在边界处出现一条硬边), 再按物理范围夹紧。

    arr: (rows, cols, 4) 北在上, 行心均匀覆盖 `[lat0, lat1]`。
    lat0/lat1: 该数组**真实**覆盖的纬度范围。省略时按**内核域**取
    (`ENV_LAT0/ENV_LAT1`)。

    ⚠⚠ **轮次 151 修掉的严重缺陷**: 本函数原来把输入**写死当 ±60** 处理
    (`d_lat = 120.0/ny`), 而内核早已从 ±60 扩到 **±90**(`ENVNY=360`) ——
    于是一个 720 行、本来就覆盖 ±90 的场被当成 ±60, 每端又硬补 180 行,
    合计 1080 行; 消费端 (`_blit_layer`) 按"行数/列数"自动推出跨度 180°,
    结果**真实数据只占 120°** ⇒ 垂直方向被压成 **2/3**, 60N 画到约 40N
    (用户原话: "被压扁了, 纬度越高偏的越多")。
    接收进来的那两个形参(`lo=0.0, hi=1.0`)当时**根本没被使用** —— 根因就在这里。

    现在: 只有 `lat0/lat1` 真的窄于 ±90 才补, 且按它自己的分辨率补;
    已经覆盖 ±90 的场原样返回(幂等), 不会再被拉扁。
    """
    if arr.ndim != 3 or arr.shape[0] < 4:
        return arr
    if lat0 is None:
        lat0 = float(V.C['LAT0'])
    if lat1 is None:
        lat1 = float(V.C['LAT1'])
    if lat1 < lat0:
        lat0, lat1 = lat1, lat0
    _span = float(lat1 - lat0)
    if _span <= 0.0:
        return arr
    ny = arr.shape[0]
    d_lat = _span / ny
    # 两端各需要补多少行才能盖到 ±90
    n_south = int(round((lat0 - LAYER_LAT0) / d_lat))
    n_north = int(round((LAYER_LAT1 - lat1) / d_lat))
    n_south = max(0, n_south)
    n_north = max(0, n_north)
    if n_south == 0 and n_north == 0:
        return arr                      # 已经覆盖 ±90, 幂等
    # ⚠ 轮次 153: 必须在**浮点**上做外推。`arr` 是 uint8, `arr[0:1] - arr[1:2]`
    # 会按模 256 回绕(实测 [[10,20,30]]-[[200,200,200]] 得 +66 而非 -190)⇒ 边缘
    # 梯度方向整个反了。本分支当前**不可达**(两个调用点传入的都真实覆盖 ±90),
    # 但留着就是给以后扩域的人下套。
    _f = arr.astype(np.float32)
    parts = []
    if n_south:
        rg = np.arange(1, n_south + 1, dtype=np.float32).reshape(-1, 1, 1)
        south = _f[0:1] + (_f[0:1] - _f[1:2]) * rg
        parts.append(south[::-1])
    parts.append(_f)
    if n_north:
        rg = np.arange(1, n_north + 1, dtype=np.float32).reshape(-1, 1, 1)
        north = _f[-1:] + (_f[-1:] - _f[-2:-1]) * rg
        parts.append(north)
    out = np.concatenate(parts, axis=0)
    # 只对颜色做夹紧(alpha 保持), 避免外推把色标打出界
    out[..., :3] = np.clip(out[..., :3], 0, 255)
    return out.astype(np.uint8)


def render_env_layer(sim, name: str, alpha: int = 210) -> np.ndarray:
    """海温/热含量/湿度/风切叠加层 RGBA (ENVNY, ENVNX, 4), 半透明; 陆地以外显示。

    结果按 (层名, _envVer, alpha, 校验和) 缓存 —— 这张 RGBA 只依赖**环境场
    状态**, 与视图无关, 而拼包 + 高分辨率海岸线掩码实测约 190-220 ms/层。
    不缓存的话只要开着 sst 就每帧付一次(实测 sst 单层 1.7 s 冷启动 / 200 ms
    稳态), 等于把帧率钉死在 5 fps。

    校验和是必要的: 直接改 sim['env'][name](诊断脚本与测试会这么干)并不会更新
    _envVer, 只按版本号做键会拿回上一份结果 —— tests/test_simcore_ocean 的
    "风切色阶随数值增大"用例正是这样被卡住的。求一次和 ≈0.2 ms。
    """
    # OHC 显示用海洋柱诊断的热含量(kJ/cm², 与 CMEMS 产品同口径)
    if name == 'ohc' and 'ocn' in sim and 'ohc_kj' in sim['ocn']:
        _arr = sim['ocn']['ohc_kj']
    else:
        _arr = sim['env'][name]
    try:
        _sum = round(float(np.asarray(_arr).sum()), 3)
    except Exception:
        _sum = None
    _key = (str(name), int(sim.get('_envVer', -1)), int(alpha), _sum)
    _hit = _env_arr_cache.get(_key)
    if _hit is not None:
        return _hit
    arr = _arr
    out = np.zeros((V.ENVNY, V.ENVNX, 4), dtype=np.uint8)
    if name == 'sst':
        # 0..33°C, 1°C 一档(离散)
        t = np.clip(arr / 33.0, 0, 1) * 255.0
        out[..., :3] = LUT_SST_STEPS[t.astype(int)][..., :3]
        out[..., 3] = alpha
    elif name == 'ohc':
        # 0..220 kJ/cm², 10 一档(离散)
        t = np.clip(arr / 220.0, 0, 1) * 255.0
        out[..., :3] = LUT_OHC_STEPS[t.astype(int)][..., :3]
        out[..., 3] = alpha
    elif name == 'shear':
        # F6: 垂直风切变(kt) —— 原来是 (arr-4)/18, 22kt 以上全同一个红, 而真实
        # 风切能到 60-70kt(CIRA 图上红色等值线一直到 70), 一接真数据整层就是
        # 纯红块。改成 0-60kt 线性。
        t = np.clip(arr / SHEAR_LUT_MAX_KT, 0, 1) * 255.0
        out[..., :3] = LUT_SHEAR[t.astype(int)][..., :3]
        out[..., 3] = alpha
    else:  # rh
        a = np.clip((arr - 45.0) / 45.0, 0, 1)
        out[..., 0] = 40
        out[..., 1] = 220
        out[..., 2] = 120
        out[..., 3] = (a * 130).astype(np.uint8)
    # 注意: 这里**不再**先用 0.5° 的 sim['land'] 整格挖空 —— 那样海岸线会变成
    # 0.5°(约 55km)的方块阶梯。改成先把所有格子都填上颜色, 最后按高分辨率海岸线裁。
    land = sim['land']
    if _hi_land_mask() is None and land.shape == out.shape[:2]:
        out[..., 3] = (out[..., 3] * (land < 0.5)).astype(np.uint8)
    # 0.5° 的粗掩码会把颜色糊到小岛和海岸线上(用户反馈"sst/ohc 在陆地上也有"),
    # 这里升采样到 1080x2160 后按真实海岸线再掩一次
    hi = _hi_land_mask()
    if hi is not None:
        # 自适应整数倍升采样: 不管内核是 1°(120行) 还是 0.5°(240行),
        # 都放大到 hi 掩码的 (720,2160)。写死 6 会在 0.5° 下变成 1440x4320,
        # 既浪费又和掩码形状对不上(掩码会被整段跳过)。
        ry = max(1, int(round(hi.shape[0] / max(1, out.shape[0]))))
        rx = max(1, int(round(hi.shape[1] / max(1, out.shape[1]))))
        big = np.repeat(np.repeat(out, ry, axis=0), rx, axis=1)
        if big.shape[:2] == hi.shape:
            big = _smooth_field(big, sigma=max(1.2, 3.5 * ry / 6.0))
            # **只挖"100% 陆地"的格子**(用户 2026-09-19: "图中有因为小岛造成的空缺…
            # 如果陆地占比达到 100% 再不模拟这个格子")。原来按高分辨率海岸线逐像素挖,
            # 于是每个小岛都在图层上打一个洞; 现在按格点陆地占比缓存
            # (simulator/env/model/land_cell.npz, 见 tools/make_cell_land_mask.py)
            # 只挖全陆格, 3% 陆地那种小岛格照常着色 → 不再有岛状空缺。
            _al = _all_land_hi(big.shape[:2], ry, rx)
            if _al is not None:
                big[..., 3][_al] = 0
            else:
                big[..., 3][hi] = 0          # 缓存缺失 → 退回老行为
            # env 网格 row0 = 60°S(南在上), 而 _blit_layer 把它贴在 fy(LAT1)
            # (画面顶部) —— 不翻一次整层场就是上下镜像(2 月暖池会跑到北边)
            # 轮次 151: 不再传死参 0.0/1.0。内核域**就是**该数组的纬度范围
            # (§内核已扩到 ±90, ENVNY=360 —— 见 `LAT0/LAT1`), 交给 `_pad_poles`
            # 自己判断要不要补; 已经覆盖 ±90 时它是**幂等**的, 不会再被拉扁。
            _res = _pad_poles(np.flipud(big))
            if len(_env_arr_cache) >= _ENV_ARR_CACHE_MAX:
                _env_arr_cache.clear()
            _env_arr_cache[_key] = _res
            return _res
    _res = _pad_poles(np.flipud(out))
    if len(_env_arr_cache) >= _ENV_ARR_CACHE_MAX:
        _env_arr_cache.clear()
    _env_arr_cache[_key] = _res
    return _res


def _pressure_extremes(p: np.ndarray, ny: int, nx: int):
    """高/低压中心索引(向量化)。

    语义与原来 120×360 双重循环一致: 只统计内圈 (1..ny-2, 1..nx-2),
    格点值比四邻域最小值小 0.4 以上 = 低压中心; 比四邻域最大值大 0.4 以上
    = 高压中心。返回全局下标 (j_low, i_low, j_high, i_high)。"""
    inner = p[1:ny - 1, 1:nx - 1]
    up = p[0:ny - 2, 1:nx - 1]
    down = p[2:ny, 1:nx - 1]
    left = p[1:ny - 1, 0:nx - 2]
    right = p[1:ny - 1, 2:nx]
    nb_min = np.minimum(np.minimum(left, right), np.minimum(up, down))
    nb_max = np.maximum(np.maximum(left, right), np.maximum(up, down))
    lj, li = np.nonzero(inner < nb_min - 0.4)
    hj, hi = np.nonzero(inner > nb_max + 0.4)
    return lj + 1, li + 1, hj + 1, hi + 1


_contour_cache: dict = {}
_CONTOUR_CACHE_MAX = 4


def _contour_segments(p: np.ndarray, levels, key=None):
    """marching squares 线段(与视图无关的格坐标)。

    返回 (j0, i0, u0, v0, j1, i1, u1, v1): 两个端点的格下标 + 格内归一化位置
    (u 沿经度, v 沿纬度)。每个格子的两条穿越边配对成一段; 与旧实现一致,
    只有恰好 2 个交点的格子才画(鞍点 4 交点的格子保持不画)。
    """
    if key is not None:
        hit = _contour_cache.get(key)
        if hit is not None:
            return hit
    v00 = p[:-1, :-1]
    v10 = p[:-1, 1:]
    v01 = p[1:, :-1]
    v11 = p[1:, 1:]
    # 每条边 (起点场, 终点场): 边号 0=上 1=左 2=右 3=下
    edges = ((v00, v10), (v00, v01), (v10, v11), (v01, v11))
    levels = tuple(levels)
    acc = []
    for li, level in enumerate(levels):
        for ei, (a, b) in enumerate(edges):
            cross = (a <= level) != (b <= level)
            if not cross.any():
                continue
            jj, ii = np.nonzero(cross)
            t = (level - a[jj, ii]) / (b[jj, ii] - a[jj, ii] + 1e-9)
            acc.append((jj, ii, t, np.full(jj.shape, ei, dtype=np.int8),
                        np.full(jj.shape, li, dtype=np.int32)))
    if not acc:
        out = None
    else:
        jj = np.concatenate([a[0] for a in acc])
        ii = np.concatenate([a[1] for a in acc])
        tt = np.concatenate([a[2] for a in acc])
        ei = np.concatenate([a[3] for a in acc])
        ll = np.concatenate([a[4] for a in acc])
        # 交点按 (格, 层, 边序) 排序后, 同一格**同一层**内恰好两个的按顺序配成一段。
        # 必须带层号: 一个格子可能同时被多条等值线穿过(陡坡/台风中心附近),
        # 只按格子分组会把这些格子判成 4 个交点而整片丢掉。
        order = np.lexsort((ei, ll, ii, jj))
        jj, ii, tt, ei, ll = jj[order], ii[order], tt[order], ei[order], ll[order]
        cell = (jj.astype(np.int64) * (p.shape[1] + 1) + ii) * max(1, len(levels)) + ll
        uniq, inv, counts = np.unique(cell, return_inverse=True, return_counts=True)
        ok = counts[inv] == 2
        if not ok.any():
            out = None
        else:
            jj, ii, tt, ei, ll = jj[ok], ii[ok], tt[ok], ei[ok], ll[ok]
            cell = cell[ok]
            # 同一格子的两条: 偶数位为第一点, 奇数位为第二点
            first = np.zeros(jj.shape[0], dtype=bool)
            first[::2] = True
            # 重新按格子分组保证 (0,1) 成对(排序后同格相邻)
            # 边号 -> 格内归一化位置: 0 上(t,0) 1 左(0,t) 2 右(1,t) 3 下(t,1)
            uu = np.where((ei == 0) | (ei == 3), tt, np.where(ei == 2, 1.0, 0.0))
            vv = np.where((ei == 1) | (ei == 2), tt, np.where(ei == 3, 1.0, 0.0))
            out = (jj[first], ii[first], uu[first], vv[first],
                   jj[~first], ii[~first], uu[~first], vv[~first])
    if key is not None:
        if len(_contour_cache) >= _CONTOUR_CACHE_MAX:
            _contour_cache.clear()
        _contour_cache[key] = out
    return out


def draw_contours(surface, sim, fx: Callable, fy: Callable,
                  levels: Optional[List[float]] = None) -> None:
    """气压等值线(marching squares) + 高/低标记。"""
    if levels is None:
        levels = [976, 980, 984, 988, 992, 996, 1000, 1004, 1008, 1012, 1016, 1020, 1024, 1028]
    p = sim['env'].get('pDisp', sim['env']['pTot'])
    ny, nx = V.ENVNY, V.ENVNX
    # 逐格屏幕坐标只算一次: fx 只依赖经度 / fy 只依赖纬度, 原实现每层每格调
    # 4 次(fx,fy)(14 层 × 数千格 ≈ 数万次 Python 调用, 实测 ~37ms/帧)
    xs0 = [fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD']) for i in range(nx)]
    xs1 = [fx(V.C['LON0'] + (i + 1.5) * V.C['ENVD']) for i in range(nx)]
    ys0 = [fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD']) for j in range(ny)]
    ys1 = [fy(V.C['LAT0'] + (j + 1.5) * V.C['ENVD']) for j in range(ny)]
    # 几何在"格坐标"里算并与视图解耦(缓存键只含气压场版本), 每帧只做一次
    # fancy indexing 映射到屏幕; 原实现每格每层走 Python 循环, 实测 35.7ms/帧
    # 缓存键 = id(p) + env 版本 + 级别 + 形状 + 内容校验和:
    #   id(_envVer) 保证 env_update 后失效, sum 兜住"原地改场"(测试/外部代码
    #   直接对 pTot 赋值)的情况, 否则会画出上一帧的等值线。
    seg = _contour_segments(p, levels,
                            key=(id(p), sim.get('_envVer', -1), tuple(levels),
                                 p.shape, float(p.sum())))
    if seg is not None:
        xs0a = np.asarray(xs0, dtype=np.float64)
        xs1a = np.asarray(xs1, dtype=np.float64)
        ys0a = np.asarray(ys0, dtype=np.float64)
        ys1a = np.asarray(ys1, dtype=np.float64)
        j0, i0, u0, v0, j1, i1, u1, v1 = seg
        x0 = xs0a[i0] + u0 * (xs1a[i0] - xs0a[i0])
        y0 = ys0a[j0] + v0 * (ys1a[j0] - ys0a[j0])
        x1 = xs0a[i1] + u1 * (xs1a[i1] - xs0a[i1])
        y1 = ys0a[j1] + v1 * (ys1a[j1] - ys0a[j1])
        col = (200, 205, 215)
        # 一次性 tolist: 逐元素 float() 转换 4x2800 次反而更慢
        for ax, ay, bx, by in zip(x0.tolist(), y0.tolist(), x1.tolist(), y1.tolist()):
            pygame.draw.line(surface, col, (ax, ay), (bx, by), 1)
    # 高/低标记(向量化: 原 120×360 双层 Python 循环实测 0.13s/帧)
    lj, li, hj, hi = _pressure_extremes(p, ny, nx)
    for j, i in zip(lj, li):
        x = fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD'])
        y = fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD'])
        surface.blit(_label('低', (120, 200, 255)), (int(x), int(y)))
    for j, i in zip(hj, hi):
        x = fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD'])
        y = fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD'])
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
                # pygame 2.5.7 的 SysFont 在本机会崩(splitext 收到 int) -> 用仓库字体,
                # 否则等值线的数值标注会静默消失
                try:
                    _FALLBACK_FONT = pygame.font.Font(
                        os.path.join(_ROOT_DIR, 'assets', 'font',
                                     'MapleMono-NF-CN-Medium.ttf'), 18)
                except Exception:
                    _FALLBACK_FONT = pygame.font.Font(None, 18)
            s = _FALLBACK_FONT.render(text, True, color)
        if len(_label_cache) > 64:
            _label_cache.pop(next(iter(_label_cache)))
        _label_cache[key] = s
    return s


_BARB_COLOR = (220, 225, 235)


#: 风切等值线: 5 kt 一档(CIMSS 惯例); 5/10 绿, 15/20 黄, 25+ 红
SHEAR_LEVELS = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 120, 130)
_shear_cache: dict = {}
_SHEAR_CACHE_MAX = 2


def _shear_color(level: int):
    if level <= 10:
        return (40, 220, 60)
    if level <= 20:
        return (240, 225, 60)
    return (235, 60, 55)


#: 等值线抽取用的降采样因子(0.5° → 1°)。
#: 为什么降: NCEP 的原始网格是 2.5°, 双线性升到 0.5° 后信息量并没有增加, 在
#: 0.5° 网格上抽等值线只会得到一堆沿粗网格走的细碎线段(看着像贴赤道的"洋流")。
#: 在 1° 网格上抽, 线段更长更顺, 更接近 CIMSS 参考图的观感。
_SHEAR_DS = 2


def _shear_segments(sim):
    """按档取风切线段(整批缓存: 风切只在 env_update 时变)。

    返回 [(level, seg, ds)]: seg 的格下标是**降采样后**的网格(1°), 用 ds 换算。
    """
    sh = sim['env']['shear']
    key = (id(sh), int(sim.get('_envVer', -1)), float(sh.sum()))
    hit = _shear_cache.get(key)
    if hit is not None:
        return hit
    ny, nx = sh.shape
    ds = _SHEAR_DS
    if ny % ds == 0 and nx % ds == 0:                 # 2× 块平均
        lo = sh.reshape(ny // ds, ds, nx // ds, ds).mean(axis=(1, 3))
    else:
        lo, ds = sh, 1
    out = []
    for lv in SHEAR_LEVELS:
        seg = _contour_segments(lo, (lv,), key=None)
        if seg is not None:
            out.append((lv, seg, ds))
    if len(_shear_cache) >= _SHEAR_CACHE_MAX:
        _shear_cache.clear()
    _shear_cache[key] = out
    return out


def draw_shear_contours(surface, sim, fx: Callable, fy: Callable) -> None:
    """垂直风切变等值线(不填色, 直接叠在地图上)。"""
    segs = _shear_segments(sim)
    if not segs:
        return
    draw = pygame.draw.line
    for lv, (j0, i0, u0, v0, j1, i1, u1, v1), ds in segs:
        # 格下标在**降采样后**的网格上(ds=2 即 1°), 换算回经纬度时要乘 ds
        nx = V.ENVNX // ds
        xs0 = np.asarray([fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD'] * ds)
                          for i in range(nx)], dtype=np.float64)
        xs1 = np.asarray([fx(V.C['LON0'] + (i + 1.5) * V.C['ENVD'] * ds)
                          for i in range(nx)], dtype=np.float64)
        ny = V.ENVNY // ds
        ys0 = np.asarray([fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD'] * ds)
                          for j in range(ny)], dtype=np.float64)
        ys1 = np.asarray([fy(V.C['LAT0'] + (j + 1.5) * V.C['ENVD'] * ds)
                          for j in range(ny)], dtype=np.float64)
        i0 = np.clip(i0, 0, nx - 1)
        i1 = np.clip(i1, 0, nx - 1)
        j0 = np.clip(j0, 0, ny - 1)
        j1 = np.clip(j1, 0, ny - 1)
        col = _shear_color(lv)
        w = 2 if lv >= 25 else 1
        x0 = xs0[i0] + u0 * (xs1[i0] - xs0[i0])
        y0 = ys0[j0] + v0 * (ys1[j0] - ys0[j0])
        x1 = xs0[i1] + u1 * (xs1[i1] - xs0[i1])
        y1 = ys0[j1] + v1 * (ys1[j1] - ys0[j1])
        for ax, ay, bx, by in zip(x0.tolist(), y0.tolist(), x1.tolist(), y1.tolist()):
            draw(surface, col, (ax, ay), (bx, by), w)
        # 数值标注: 每档取若干段的中点在线上标数字(参考 CIMSS 产品)
        n = x0.shape[0]
        if n >= 8:
            for k in range(1, n, max(6, n // 5)):
                mx = 0.5 * (x0[k] + x1[k])
                my = 0.5 * (y0[k] + y1[k])
                if not (0 <= mx < surface.get_width() and 0 <= my < surface.get_height()):
                    continue
                lab = _label(str(lv), col)
                surface.blit(lab, (int(mx) + 3, int(my) - lab.get_height() // 2))


_STREAM_SEEDS: dict = {}


def _stream_seeds(n: int):
    """固定种子点(经纬度): 每帧一致, 否则流线会闪。"""
    g = _STREAM_SEEDS.get(n)
    if g is None:
        rng = np.random.default_rng(20260915)
        lon = rng.uniform(V.C['LON0'], V.C['LON1'], n)
        lat = rng.uniform(V.C['LAT0'] + 8.0, V.C['LAT1'] - 8.0, n)
        g = (lon, lat)
        _STREAM_SEEDS.clear()
        _STREAM_SEEDS[n] = g
    return g


_SEED_WINDOW_CACHE: dict = {}


def _stream_seeds_window(n: int, lon0: float, lon1: float,
                         lat0: float, lat1: float):
    """**可见窗口内**的固定种子点。

    原来种子撒在整个 0.5° 全球域(360°×120°), 而地图默认视图只框住 50°×55°
    (约 4% 面积) → 屏幕上只剩几条, 用户说"洋流/环流就几条, 完全不知道是什么"。
    现在按可见窗口撒种, 密度与屏幕面积挂钩; 窗口量化后缓存, 平移缩放不会让
    整片流线重新抖动。
    """
    key = (n, round(lon0 / 2.0), round(lon1 / 2.0),
           round(lat0 / 2.0), round(lat1 / 2.0))
    g = _SEED_WINDOW_CACHE.get(key)
    if g is None:
        rng = np.random.default_rng(abs(hash(key)) % (2 ** 31))
        pad_lon = (lon1 - lon0) * 0.06
        pad_lat = (lat1 - lat0) * 0.06
        lon = rng.uniform(lon0 - pad_lon, lon1 + pad_lon, n)
        lat = rng.uniform(max(V.C['LAT0'] + 6.0, lat0 - pad_lat),
                          min(V.C['LAT1'] - 6.0, lat1 + pad_lat), n)
        g = (lon, lat)
        if len(_SEED_WINDOW_CACHE) > 24:
            _SEED_WINDOW_CACHE.clear()
        _SEED_WINDOW_CACHE[key] = g
    return g


def _visible_window(grid, size):
    """可见区对应的经纬度窗口(用于种子/覆盖范围)。"""
    if grid is None:
        return (V.C['LON0'], V.C['LON1'], V.C['LAT0'], V.C['LAT1'])
    vis = grid.visible(size)
    if vis is None:
        return (V.C['LON0'], V.C['LON1'], V.C['LAT0'], V.C['LAT1'])
    vx0, vy0, vx1, vy1 = vis
    xa, xb = min(grid.x0, grid.x1), max(grid.x0, grid.x1)
    ya, yb = min(grid.y0, grid.y1), max(grid.y0, grid.y1)
    fx0 = (vx0 - xa) / max(1e-9, xb - xa)
    fx1 = (vx1 - xa) / max(1e-9, xb - xa)
    fy0 = (vy0 - ya) / max(1e-9, yb - ya)
    fy1 = (vy1 - ya) / max(1e-9, yb - ya)
    lon0 = grid.lon0 + fx0 * (grid.lon1 - grid.lon0)
    lon1 = grid.lon0 + fx1 * (grid.lon1 - grid.lon0)
    lat1 = grid.lat1 - fy0 * (grid.lat1 - grid.lat0)
    lat0 = grid.lat1 - fy1 * (grid.lat1 - grid.lat0)
    return (lon0, lon1, lat0, lat1)


_FLOW_KEY = (9, 8, 7)          # colorkey 魔法色(风羽色/地图色都命不中)
_flow_cache: dict = {}
_FLOW_CACHE_MAX = 4
#: 节流状态: **按图层名分开**。
#: ⚠⚠ 轮次 151 修掉的缺陷: 原先是**一个共享的 `[surf, made_ms]`**, 而
#: `draw_streamlines`(环流/风场)在 `draw_currents`(洋流)**之前**调用 ⇒ 洋流层在
#: 150ms 窗口内必然复用**风场**那张流线图, 画出来就是风场环流(单独开洋流是对的,
#: 两层同开才错, 且下一帧自愈)。实测: 同开时 `_build_streamlines` 调用记录里只有
#: `flow`、`_flow_cache` 无 `cur` 键、洋流在地图上**新增 0 像素**。
_flow_last: dict = {}


def _flow_throttle_get(name: str):
    """取该图层的节流缓存(150ms 内的上一张图), 没有则 None。"""
    st = _flow_last.get(name)
    if st is not None and st[0] is not None:
        now = pygame.time.get_ticks()
        if now - st[1] < 150:
            return st[0]
    return None


def _flow_throttle_put(name: str, surf) -> None:
    _flow_last[name] = [surf, pygame.time.get_ticks()]


def _build_streamlines(sim, fx: Callable, fy: Callable, w: int, h: int,
                       seeds: int, steps: int, step_h: float,
                       field=None, min_speed: float = 1.5,
                       window=None) -> pygame.Surface:
    """把流线画到一张 colorkey 背景的整屏 surface 上(blit 走 SDL 快速路径)。

    window=(lon0, lon1, lat0, lat1): 只在**可见窗口**里撒种子(见
    _stream_seeds_window) —— 全球撒种在放大视图下只剩几条线。
    """
    surf = pygame.Surface((w, h))
    surf.fill(_FLOW_KEY)
    if window is not None:
        lon, lat = _stream_seeds_window(seeds, *window)
    else:
        lon, lat = _stream_seeds(seeds)
    lon = np.asarray(lon).copy()
    lat = np.asarray(lat).copy()
    draw = pygame.draw.line
    def sample(lo, la):
        """默认用风场(含台风涡旋); field=(u,v) 时用外部流场(如表层洋流)。"""
        if field is None:
            return V.wind_at_arr(sim, lo, la, 0.0)
        return (V.env_bilinear_arr(field[0], lo, la),
                V.env_bilinear_arr(field[1], lo, la))

    for s in range(steps):
        u, v = sample(lon, lat)
        sp = np.hypot(u, v)
        km_lon = np.maximum(111.32 * np.cos(np.radians(lat)), 1.0)
        mlon = lon + u * 1.852 * step_h * 0.5 / km_lon       # RK2 中点
        mlat = np.clip(lat + v * 1.852 * step_h * 0.5 / 110.57, -89.0, 89.0)
        mu, mv = sample(mlon, mlat)
        nlon = lon + mu * 1.852 * step_h / km_lon
        nlat = np.clip(lat + mv * 1.852 * step_h / 110.57, -89.0, 89.0)
        fade = 1.0 - 0.62 * (s / max(1, steps - 1))
        xs0 = [fx(float(t)) for t in lon]
        ys0 = [fy(float(t)) for t in lat]
        xs1 = [fx(float(t)) for t in nlon]
        ys1 = [fy(float(t)) for t in nlat]
        for i in np.nonzero(sp > min_speed)[0]:
            x0, y0, x1, y1 = xs0[i], ys0[i], xs1[i], ys1[i]
            if abs(x1 - x0) > w * 0.5:
                continue                      # 跨 0 度经线: 本段不画
            base = _wind_color(float(sp[i]))
            draw(surf, (int(base[0] * fade), int(base[1] * fade), int(base[2] * fade)),
                 (x0, y0), (x1, y1), 1)
        lon, lat = nlon, nlat
    surf.set_colorkey(_FLOW_KEY)
    return surf


def draw_streamlines(surface: pygame.Surface, sim, fx: Callable, fy: Callable,
                     seeds: int = 260, steps: int = 5, step_h: float = 8.0,
                     grid=None) -> None:
    """低层环流细流线(参考视频里海面上那种淡流线, 不用云也不用粒子)。

    - 积分用 8 小时/步 x 5 步: 10kt 的风约走 900km(地图上 ~25px), 才看得见;
      之前按 10 分钟步长积分只有 0.1 像素, 等于没画。
    - grid 给出时: 种子撒在**可见窗口**内、数量按屏幕面积定(否则放大后只剩几条)。
    - 整层缓存: 键 = (env 版本, 视口, 尺寸)。env 每 2 模拟小时才变一次, 视图不变
      时每帧只付一次 colorkey blit(约 1ms)。拖动/缩放时键会变, 用 150ms 墙钟限流
      复用上一张, 避免拖动期间每帧重算。
    """
    w, h = surface.get_size()
    window = None
    if grid is not None:
        window = _visible_window(grid, (w, h))
        # 密度: 每 ~9000 像素一个种子, 60-700 之间(实测 1600x800 视图 → ~140)
        seeds = max(60, min(700, int((w * h) // 9000)))
    key = (int(sim.get('_envVer', -1)), w, h,
           int(fx(0.0)), int(fy(0.0)), int(fx(180.0)),
           None if window is None else tuple(round(v, 1) for v in window))
    s = _flow_cache.get(key)
    if s is None:
        s = _flow_throttle_get('flow')
        if s is None:
            s = _build_streamlines(sim, fx, fy, w, h, seeds, steps, step_h,
                                   window=window)
            if len(_flow_cache) >= _FLOW_CACHE_MAX:
                _flow_cache.clear()
            _flow_cache[key] = s
            # ⚠ 时间戳**只能在这里写**(真正重建之后)。
            # 轮次 153: 曾把它放到内层 if 之外 ⇒ "复用旧图"这条路径也刷新时间戳,
            # 而新 key 永远进不了 `_flow_cache` ⇒ 下一帧继续 miss、继续复用同一张旧图,
            # 只要帧间隔 <150ms 就**永久冻结**在旧视图上(拖动/缩放后不再更新)。
            _flow_throttle_put('flow', s)
    surface.blit(s, (0, 0))


def draw_currents(surface, sim, fx: Callable, fy: Callable,
                  seeds: int = 220, steps: int = 5, step_h: float = 240.0,
                  grid=None) -> None:
    """表层洋流流线(0.5° 逐格流场, 不含台风涡旋)。

    流场来自 ocean_step 存的 ocn['u']/['v'] —— Ekman 漂流(3% 风 + 半球偏转 30 度),
    24 小时/步 x 5 步约走 1000km, 所以热带西向流、副热带东向流都能看出来。
    整层缓存, 键 = env 版本 + 视口 + 尺寸。
    """
    ocn = sim.get('ocn') or {}
    if 'u' not in ocn or 'v' not in ocn:
        return
    w, h = surface.get_size()
    window = _visible_window(grid, (w, h)) if grid is not None else None
    if window is not None:
        seeds = max(60, min(600, int((w * h) // 11000)))
    key = ('cur', int(sim.get('_envVer', -1)), w, h,
           int(fx(0.0)), int(fy(0.0)), int(fx(180.0)),
           None if window is None else tuple(round(v, 1) for v in window))
    s = _flow_cache.get(key)
    if s is None:
        s = _flow_throttle_get('current')      # 与 'flow' 分开, 不能互相借图
        if s is None:
            # 洋流只有 0.2~2 kt(风场的 3%), 阈值和步长都要按洋流量级给:
            # 10 天/步 x 5 步, 0.3kt 也能走 6 个经度, 才看得见
            s = _build_streamlines(sim, fx, fy, w, h, seeds, steps, step_h,
                                   field=(ocn['u'], ocn['v']), min_speed=0.05,
                                   window=window)
            if len(_flow_cache) >= _FLOW_CACHE_MAX:
                _flow_cache.clear()
            _flow_cache[key] = s
            _flow_throttle_put('current', s)   # 同 'flow': 只在重建后写时间戳
    surface.blit(s, (0, 0))


def draw_barbs(surface, sim, fx: Callable, fy: Callable, step: float = 0.0,
               grid=None) -> None:
    """风羽(kt: 整羽 50kt/半羽 10kt/小羽 5kt)。

    2026-09-19 按用户反馈改三处:
      · **随缩放变化**: 风羽长度按"屏幕上每度多少像素"缩放(px_per_deg 3→30 时
        L 由 8 到 22 px), 不再固定 13px;
      · **间距随缩放**: step 默认按 ~48px 间距反推(放大后不再是 5° 一根的稀疏网,
        缩小时也不会糊成一片), 并吸附到 0.5° 网格上;
      · **不闪**: 位置一律走 grid(GridMap 的仿射映射, 不会逐点翻面), 采样格点
        固定为 0.5° 对齐的规则网格(旧实现按当前视图浮点起算 + 逐点环绕翻面,
        阈值附近每帧跳一个图宽)。
    采样一次性批量(wind_at_arr): 绘制仍逐点(风羽形状按速度分档, 无法批量画)。
    """
    if grid is not None:
        window = _visible_window(grid, surface.get_size())
        ppd = max(0.5, grid.px_per_deg_x())
        if step <= 0:
            step = max(0.5, min(10.0, round(48.0 / ppd * 2.0) / 2.0))
        lon0, lon1, lat0, lat1 = window
    else:
        if step <= 0:
            step = 5.0
        ppd = 6.0
        lon0, lon1, lat0, lat1 = 0.0, 360.0, -55.0, 55.0
    lon0 = max(0.0, min(357.5, math.floor(lon0 / step) * step))
    lon1 = min(360.0, math.ceil(lon1 / step) * step + step)
    lat0 = max(-55.0, min(55.0, math.floor(lat0 / step) * step))
    lat1 = min(55.0, math.ceil(lat1 / step) * step + step)
    lats = np.arange(lat0, lat1, step)
    lons = np.arange(lon0, lon1, step)
    if lats.size == 0 or lons.size == 0:
        return
    LO, LA = np.meshgrid(lons, lats)
    LO = LO.ravel()
    LA = LA.ravel()
    u, v = V.wind_at_arr(sim, LO, LA, 0.0)
    sp = np.hypot(u, v)
    keep = sp >= 2.5
    if not keep.any():
        return
    LO, LA, u, v, sp = LO[keep], LA[keep], u[keep], v[keep], sp[keep]
    # 风羽长度: 随缩放 8-22px(参考 48px 间距的一半以内, 避免互相压线)
    L = max(7.0, min(22.0, ppd * 1.1))
    sp_scale = L / 13.0                    # 羽片间隔同比例缩放
    color = _BARB_COLOR
    for i in range(LO.size):
        spi = float(sp[i])
        x, y = fx(float(LO[i])), fy(float(LA[i]))
        ang = math.atan2(-float(v[i]), -float(u[i]))
        ex, ey = math.cos(ang), math.sin(ang)
        px_ = -math.sin(ang)
        py_ = math.cos(ang)
        pygame.draw.line(surface, color, (x, y), (x + ex * L, y + ey * L), 1)
        rem = spi
        d = 0
        while rem >= 47.5 and d < 3:
            p0 = (x + ex * (L - d * 5 * sp_scale), y + ey * (L - d * 5 * sp_scale))
            p1 = (x + ex * (L - (d * 5 + 6) * sp_scale) + px_ * 6 * sp_scale,
                  y + ey * (L - (d * 5 + 6) * sp_scale) + py_ * 6 * sp_scale)
            pygame.draw.polygon(surface, color, [p0, p1, (x + ex * L, y + ey * L)], 1)
            d += 1
            rem -= 50
        while rem >= 7.5 and d < 8:
            p0 = (x + ex * (L - d * 5 * sp_scale), y + ey * (L - d * 5 * sp_scale))
            p1 = (x + ex * (L - (d * 5 + 6) * sp_scale) + px_ * 5 * sp_scale,
                  y + ey * (L - (d * 5 + 6) * sp_scale) + py_ * 5 * sp_scale)
            pygame.draw.line(surface, color, p0, p1, 1)
            d += 1
            rem -= 10
        if rem >= 2.5 and d < 8:
            p0 = (x + ex * (L - d * 5 * sp_scale), y + ey * (L - d * 5 * sp_scale))
            p1 = (x + ex * (L - (d * 5 + 4) * sp_scale) + px_ * 3 * sp_scale,
                  y + ey * (L - (d * 5 + 4) * sp_scale) + py_ * 3 * sp_scale)
            pygame.draw.line(surface, color, p0, p1, 1)


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
        """向量化推进: 整批采样风场(原实现 3000 次 Python 调用 ≈63ms/帧)。"""
        u, v = V.wind_at_arr(sim, self.lon, self.lat, 0.0)
        self.ol[:] = self.lon
        self.oa[:] = self.lat
        self.u[:] = u
        self.v[:] = v
        coslat = np.maximum(np.cos(self.lat * V.C['DEG']), 1e-6)
        self.lon += u * 3.6 * dt_h / (111.32 * coslat)
        self.lat += v * 3.6 * dt_h / 110.57
        bad = ((self.lon < V.C['LON0'] + 0.5) | (self.lon > V.C['LON1'] - 0.5)
               | (self.lat < V.C['LAT0'] + 0.5) | (self.lat > V.C['LAT1'] - 0.5)
               | (V.land_at_arr(sim, self.lon, self.lat) > 0.4))
        n_bad = int(np.count_nonzero(bad))
        if n_bad:
            self.lon[bad] = (V.C['LON0'] + np.random.random(n_bad)
                             * (V.C['LON1'] - V.C['LON0']))
            self.lat[bad] = (V.C['LAT0'] + np.random.random(n_bad)
                             * (V.C['LAT1'] - V.C['LAT0']))
            self.ol[bad] = self.lon[bad]
            self.oa[bad] = self.lat[bad]
            self.u[bad] = 0.0
            self.v[bad] = 0.0

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
        # ⚠ 轮次 155: 原来画在 `if show_rings:` **之外** ⇒ 关掉"风圈显示"之后
        # 34/50/64kt 圈没了、眼墙却还在, 与开关的语义不符。并入同一开关。
        if show_rings:
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


#: 上一帧副高图层在**当前视野内**真正画出来的像素数(轮次 151)。
#: 用途: 默认镜头(西太 107-157E / 10S-13N)下副高主体常在视野外, 打开图层
#: 地图上什么都不多 —— 用户会以为"这个按钮是假的"。`_sim_toggle_layer` 据此提示。
RIDGE_VISIBLE_PX = [0]


def draw_ridge(surface, sim, fx: Callable, fy: Callable, grid=None) -> None:
    """副高(西太副高)范围: 半透明橙色填充 + 边界描边 + 数值标注。

    2026-09-19 修: 原来写死 pEnv >= 1016 hPa 当"5880gpm 近似", 但本模式的
    海平面气压场实测只有 1000.7-1014.1 hPa —— 阈值永远命不中, **整层恒不可见**
    (实测平均像素差 0.13, 与什么都不画一样)。改成自适应阈值: 取场的高分位
    (下限 1012 hPa), 保证副高范围永远画得出来, 并把实际阈值写到标注里。
    贴图走 _blit_layer(与海温同一套可见区裁剪), 不再自己算域宽高。
    """
    try:
        p = np.asarray(sim['env'].get('pDisp', sim['env']['pEnv']))
        env_ver = int(sim.get('_envVer', -1))
        try:
            p90 = float(np.percentile(p, 88))
        except Exception:
            p90 = 1016.0
        th = max(1012.0, round(p90, 1))
        mask = np.flipud(p >= th)              # 网格南在上 -> 贴图前翻正
        rgba = np.zeros(mask.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = 150
        rgba[..., 2] = 40
        rgba[..., 3] = np.where(mask, 90, 0).astype(np.uint8)
        # 视野内到底有没有副高格点? (给 UI 反馈用 —— 见 RIDGE_VISIBLE_PX)
        try:
            if grid is not None:
                _vis = grid.visible(surface.get_size())
                if _vis is None:
                    RIDGE_VISIBLE_PX[0] = 0
                else:
                    _c0, _r0, _c1, _r1, _ = grid.src_window(mask.shape[1],
                                                            mask.shape[0], _vis)
                    RIDGE_VISIBLE_PX[0] = int(rgba[_r0:_r1, _c0:_c1, 3].sum())
            else:
                RIDGE_VISIBLE_PX[0] = int(rgba[..., 3].sum())
        except Exception:
            RIDGE_VISIBLE_PX[0] = 0
        _blit_layer(surface, rgba, fx, fy, cache_key=('ridge', env_ver, th),
                    grid=grid)
        # 边界描边(隔点画, 省一半圆)
        if mask.shape[0] > 3 and mask.shape[1] > 3:
            edge = np.zeros_like(mask)
            edge[1:-1, 1:-1] = mask[1:-1, 1:-1] & ~(
                mask[:-2, 1:-1] & mask[2:, 1:-1]
                & mask[1:-1, :-2] & mask[1:-1, 2:])
            ys, xs = np.nonzero(edge)
            for j, i in zip(ys[::2], xs[::2]):
                lon = V.C['LON0'] + (i + 0.5) * V.C['ENVD']
                lat = V.C['LAT0'] + ((mask.shape[0] - 1 - j) + 0.5) * V.C['ENVD']
                pygame.draw.circle(surface, (255, 150, 60),
                                   (int(fx(lon)), int(fy(lat))), 2)
        # 标注: 写实际用的阈值(副高范围), 位置跟着可见区走
        if grid is not None:
            win = _visible_window(grid, surface.get_size())
            lab = _label('副高 %.0f hPa' % th, (255, 170, 80))
            lx = int(grid.x(min(max(win[0] + 4.0, win[0]), win[1])))
            ly = int(grid.y(win[3] - 3.0))
            surface.blit(lab, (lx + 6, ly - lab.get_height() - 2))
        else:
            surface.blit(_label('副高 %.0f hPa' % th, (255, 170, 80)),
                         (int(fx(120)), int(fy(33))))
    except Exception:
        logger.debug('simcore 图层绘制失败', exc_info=True)


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
               icon_fn: Optional[Callable] = None,
               grid: "GridMap" = None, grid_pad: "GridMap" = None,
               hud_labels: bool = True) -> None:
    """把 SimCore 各图层叠加绘制到 surface(已有地图底图)上。

    fx/fy: 经纬度 → surface 像素(来自项目地图视图, 实现叠加而非覆盖)。
    grid:  环境格点类图层(海温/热含量/湿度/风切/气压/环流/洋流/风羽)专用的
           **仿射**映射(见 GridMap)。给 None 时退回 fx/fy —— 那样 sst/ohc 在
           项目视图下会整层画不出来(fx(0)==fx(360) → 宽度 0), 仅兼容旧调用。
    icon_fn: 可选, callable(surface, tc, x, y, sel) — 台风图标渲染(复用 app 图标集)。"""
    # 格点类图层统一用仿射映射; 点类(台风/预报圈/信息框)仍用 fx/fy(需要环绕翻面)
    gx = grid.x if grid is not None else fx
    gy = grid.y if grid is not None else fy
    # 云图(作为底图背景覆盖计算域, 半透明与地图融合; 仅图层开启时绘制)
    if 'cloud' in layers:
        w = int(fx(V.C['LON1']) - fx(V.C['LON0']))
        h = int(fy(V.C['LAT0']) - fy(V.C['LAT1']))
        if w > 0 and h > 0:
            # 云场/台风参数未明显变化时直接复用已缩放表面(实测 118ms -> ~0ms)
            surf = _cloud_surface(sim, mode, w, h)
            surface.blit(surf, (fx(V.C['LON0']), fy(V.C['LAT1'])))
    # 海温 / 热含量 / 湿度
    # 环境场只在 env_update 时变化: 缓存键带 _envVer, 图层表面整块复用
    _env_ver = int(sim.get('_envVer', -1))
    # 色阶类图层用 **±90 的填充版映射**(数组已被 _pad_poles 补到两极)
    _gpad = grid_pad if grid_pad is not None else grid
    if 'sst' in layers:
        arr = render_env_layer(sim, 'sst')
        _blit_layer(surface, arr, fx, fy, alpha=210,
                    cache_key=('sst', _env_ver), grid=_gpad)
    if 'ohc' in layers:
        arr = render_env_layer(sim, 'ohc')
        _blit_layer(surface, arr, fx, fy, alpha=190,
                    cache_key=('ohc', _env_ver), grid=_gpad)
    if 'rh' in layers:
        arr = render_env_layer(sim, 'rh')
        _blit_layer(surface, arr, fx, fy, cache_key=('rh', _env_ver), grid=_gpad)
    if 'shear' in layers:
        # 风切: **默认色阶**(用户 2026-09-19: 默认色阶而不是线); 切到线时用
        # CIMSS 风格等值线(带数值标注)。
        try:
            if sim.get('_shear_fill', True):
                arr = render_env_layer(sim, 'shear')
                _blit_layer(surface, arr, fx, fy, alpha=150,
                            cache_key=('shear', _env_ver), grid=_gpad)
            else:
                draw_shear_contours(surface, sim, gx, gy)
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
    # 气压
    if 'pressure' in layers:
        try:
            draw_contours(surface, sim, gx, gy)
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
    # 环流流线(细线, 参考视频那种"淡流线"表达; 不含云)
    if 'flow' in layers:
        try:
            draw_streamlines(surface, sim, gx, gy, grid=grid)
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
    # 洋流(表层流线)
    if 'current' in layers:
        try:
            draw_currents(surface, sim, gx, gy, grid=grid)
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
    # 副高 5880 线(近似: pEnv 等值线 + 内部橙色填充)
    if 'ridge' in layers:
        try:
            # 轮次 153: 必须传 `grid=grid` —— `draw_ridge` 的"视野内像素"计数
            # (`RIDGE_VISIBLE_PX`, 供"副高中心不在当前视野"提示)只在拿到 grid 时
            # 才走 `grid.visible()/src_window()` 那条分支; 漏传则走 else 分支数
            # **全球**掩码, 恒 > 0 ⇒ 提示永不触发、新功能等于没做。
            # 同文件 streamlines/currents 都传了 grid, 只有这里漏了。
            draw_ridge(surface, sim, gx, gy, grid=grid)
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
    # 风场
    if 'wind' in layers:
        _wmode = str(sim.get('_wind_mode', 'barbs'))
        if _wmode == 'stream':
            try:
                draw_streamlines(surface, sim, gx, gy, grid=grid)
            except Exception:
                logger.debug('simcore 图层绘制失败', exc_info=True)
        else:
            if _wmode == 'particles' and particles is not None:
                particles.draw(surface, gx, gy)
            try:
                if sim.get('_show_barbs', True):
                    draw_barbs(surface, sim, gx, gy, grid=grid)
            except Exception:
                logger.debug('simcore 图层绘制失败', exc_info=True)
    # 路径 + 预报(误差圈模式, 样式可选 JMA/JTWC)
    # 注意: 台风路径本体由模拟模式 mixin 用其它模式的点阵/渐变线管线绘制,
    # 此处仅画预报误差圈
    # ⚠⚠ 轮次 155: `draw_tc_hud`(风圈/四象限弧/眼墙/头顶文字)原来**与这个 if 同缩进**,
    # 也就是在门控**之外** ⇒ 用数字键切到非路径图层后, 路径/图标/名字/信息框都消失了,
    # 但 34/50/64kt 风圈与眼墙还留在屏幕上(与轮次 151 修掉的"幽灵图标"是同一类漏挂)。
    if 'track' in layers:
        try:
            if sim.get('_show_forecast', True):
                draw_forecast(surface, sim, sel_tc, fx, fy,
                              style=sim.get('_fcst_style', 'JTWC'))
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)
        try:
            draw_tc_hud(surface, sim, sel_tc['id'] if sel_tc else None, fx, fy,
                        icon_fn,
                        quadrant=bool(sim.get('_quad_rings', True)),
                        show_rings=bool(sim.get('_show_rings', True)),
                        # 模拟模式把"名字+强度+气压"统一挪到**头顶**(带字号/距离设置),
                        # 所以这里不再画图标右侧那一行(否则两处重复)
                        show_labels=bool(hud_labels))
        except Exception:
            logger.debug('simcore 图层绘制失败', exc_info=True)


def _blit_layer(surface, rgba: np.ndarray, fx: Callable, fy: Callable,
                alpha: int = 255, cache_key=None, grid=None) -> None:
    """把 (H, W, 4) RGBA 环境层贴到地图。

    必须保留逐格 alpha: 原来只取 RGB 再统一 set_alpha, 陆地/缺测格(alpha=0)
    会被整体不透明地画上去, 湿度层也退化成整屏纯色块。

    grid(GridMap): **推荐路径** —— 只把屏幕可见的那部分格点缩放后贴上,
      顺带解决"整张域贴图"在放大视图下宽高爆炸的问题(实测 sst 层 1871ms 且
      不可见)。grid=None 时退回旧的整体贴图(仅兼容旧调用/测试)。
    cache_key: (层名, 版本号 等) —— 命中时跳过 numpy 拼包 + smoothscale。
    """
    W, H = surface.get_size()
    if grid is not None:
        vis = grid.visible((W, H))
        if vis is None:
            return
        ny, nx = rgba.shape[:2]
        c0, r0, c1, r1, dst = grid.src_window(nx, ny, vis)
        sub = rgba[r0:r1, c0:c1]
        key = ((cache_key, 'g', c0, r0, c1, r1, dst.w, dst.h, int(alpha))
               if cache_key is not None else None)
        surf = _layer_surf_cache.get(key) if key is not None else None
        if surf is None:
            a = sub[..., 3].astype(np.uint16)
            if alpha < 255:
                a = (a * int(alpha)) // 255
            buf = np.ascontiguousarray(
                np.dstack([sub[..., :3], a.astype(np.uint8)]))
            surf = pygame.image.frombuffer(buf.tobytes(),
                                           (buf.shape[1], buf.shape[0]), 'RGBA')
            if (surf.get_width(), surf.get_height()) != (dst.w, dst.h):
                surf = pygame.transform.smoothscale(surf, (dst.w, dst.h))
            if key is not None:
                if len(_layer_surf_cache) >= _LAYER_SURF_CACHE_MAX:
                    _layer_surf_cache.clear()
                _layer_surf_cache[key] = surf
        surface.blit(surf, dst.topleft)
        return

    # **数组覆盖的纬度跨度必须自己推**(用户 2026-09-19: "被压扁了, 纬度越高偏的越多")。
    # 色阶类图层经 _pad_poles 补到 ±90(行数 ×1.5), 而这里原来写死按内核域 ±60
    # 算高度 —— 于是 ±90 的场被压进 ±60 的屏幕框, 垂直方向被压扁 2/3, 误差随
    # 纬度增大(赤道 0、极区最大), 正是用户看到的样子。
    # 列永远覆盖 360°, 所以 跨度 = 360 × 行数/列数: 240/720→120°(±60)、
    # 360/720→180°(±90)、1080/2160→180°、ridge 掩码 240/720→120° —— 全部自动正确。
    _span = 360.0 * float(rgba.shape[0]) / float(max(1, rgba.shape[1]))
    _lat_hi = _span / 2.0
    _lat_lo = -_lat_hi
    w = max(1, int(fx(V.C['LON1']) - fx(V.C['LON0'])))
    h = max(1, int(fy(_lat_lo) - fy(_lat_hi)))
    surf = None
    key = (cache_key, w, h, int(alpha)) if cache_key is not None else None
    if key is not None:
        surf = _layer_surf_cache.get(key)
    if surf is None:
        # frombuffer 的 size 是 (宽, 高), 而数组是 (行=高, 列=宽)。原实现把数组
        # transpose 成 (W,H,4) 又按 (W,H) 声明 —— 行列被转了两次, 整层场被**旋转 90 度**
        # (纬度的南北梯度跑到屏幕水平方向, 就是"sst/ohc 竖条纹")。
        # 正确做法: 不 transpose, 直接按 (列数, 行数) 声明。
        a = rgba[..., 3].astype(np.uint16)
        if alpha < 255:
            a = (a * int(alpha)) // 255
        buf = np.ascontiguousarray(np.dstack([rgba[..., :3], a.astype(np.uint8)]))
        surf = pygame.image.frombuffer(buf.tobytes(),
                                       (buf.shape[1], buf.shape[0]), 'RGBA')
        surf = pygame.transform.smoothscale(surf, (w, h))
        if key is not None:
            if len(_layer_surf_cache) >= _LAYER_SURF_CACHE_MAX:
                _layer_surf_cache.clear()
            _layer_surf_cache[key] = surf
    surface.blit(surf, (fx(V.C['LON0']), fy(_lat_hi)))
