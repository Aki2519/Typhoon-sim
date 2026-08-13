# simulator/clouds/render.py
"""F9 云图渲染: IR 增强 + 可见光,全球背景合成 + 单台风结构。

IR-BD 色阶术语: OW(开阔水面/无云)→ LG(浅灰,弱对流)→ MG/DG → 冷增强段(白/彩)。
输入: 阶段2 参数化(ITCZ/季风槽/副高) + 阶段4 台风逐 6h 状态。
输出: render_ir(global) / render_vis(global) / render_typhoon(单台细节)。
对接 F5 render_layer 接口(cloud_ir / cloud_vis / cloud_globe)。
"""
from __future__ import annotations
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from simulator.env.field_io import NLAT, NLON, LATS, LONS

# ════════════════════ IR-BD 色条(亮温 °C → RGB)════════════════════
# 顺序: 冷增强(极冷白/彩) → 深对流 → 中云 → 浅云 → 开阔水面
_IR_BD = [
    (-100.0, (255, 235, 255)),   # 极冷增强(白)
    (-80.0,  (235, 60, 200)),    # 冷增强(品红)
    (-70.0,  (220, 60, 90)),     # 强对流(红)
    (-60.0,  (235, 120, 60)),    # 强对流(橙)
    (-50.0,  (255, 210, 80)),    # DG(深灰增强,黄)
    (-40.0,  (160, 180, 220)),   # MG(中灰,蓝灰)
    (-30.0,  (120, 140, 190)),   # LG(浅灰,灰蓝)
    (-20.0,  (80, 95, 130)),     # 薄云
    (0.0,    (40, 45, 60)),      # 海面
    (40.0,   (20, 22, 30)),      # 海面(暖)
]


def irbd_color(tb_c: float) -> Tuple[int, int, int]:
    for (t0, c0), (t1, c1) in zip(_IR_BD, _IR_BD[1:]):
        if t0 <= tb_c <= t1:
            f = (tb_c - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))
    return _IR_BD[0][1] if tb_c < _IR_BD[0][0] else _IR_BD[-1][1]


# ════════════════════ 外部色阶(colormap_data.json)════════════════════
# 256 级 RGB, 索引 0=暖(50°C), 255=冷(-100°C)。来源:
#   G:\气象\工具\Typhoon8(绘图脚本)\colormap_data.json
_COLORMAP_PATHS = (
    r'G:\气象\工具\Typhoon8(绘图脚本)\colormap_data.json',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'assets', 'colormap_data.json'),
)
_COLORMAPS: Dict[str, np.ndarray] = {}
_COLORMAP_LOADED = False


def load_colormaps() -> Dict[str, np.ndarray]:
    """加载外部红外色阶(256 级 RGB uint8)。失败时回退内置。"""
    global _COLORMAPS, _COLORMAP_LOADED
    if _COLORMAP_LOADED:
        return _COLORMAPS
    _COLORMAP_LOADED = True
    import json
    for p in _COLORMAP_PATHS:
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
            _COLORMAPS = {k: np.asarray(v, dtype=np.uint8).reshape(-1, 3)
                          for k, v in d.items()
                          if isinstance(v, list) and len(v) == 256}
            if _COLORMAPS:
                break
        except Exception:
            continue
    return _COLORMAPS


def colormap_names() -> List[str]:
    return list(load_colormaps().keys()) or ['IR_BD']


def colormap_lut(name: str) -> Optional[np.ndarray]:
    """按名称取 256 级 LUT; 未知名称回退内置 IR_BD 表。"""
    cm = load_colormaps().get(name)
    if cm is not None:
        return cm
    # 内置 IR_BD 转 256 级 LUT(索引 0=50°C → 255=-100°C)
    lut = np.zeros((256, 3), dtype=np.float32)
    tb_axis = np.linspace(50.0, -100.0, 256)
    stops = np.array([t for t, _ in _IR_BD], dtype=np.float32)
    cols = np.array([c for _, c in _IR_BD], dtype=np.float32)
    idx = np.clip(np.searchsorted(stops, tb_axis, side='right') - 1, 0, len(stops) - 2)
    t0, t1 = stops[idx], stops[idx + 1]
    f = np.where(t1 > t0, (tb_axis - t0) / np.maximum(t1 - t0, 1e-6), 0.0)
    lut = cols[idx] * (1.0 - f)[:, None] + cols[idx + 1] * f[:, None]
    return np.clip(lut, 0, 255).astype(np.uint8)


def _gauss(lat: np.ndarray, lon: np.ndarray, clat: float, clon: float,
           a_lat: float, a_lon: float) -> np.ndarray:
    # 经度差归一化到 (-180, 180],西侧同样参与(单向 %360 会切掉西半侧)
    dlon = ((lon - clon + 180.0) % 360.0) - 180.0
    return np.exp(-((lat - clat) ** 2) / (2 * a_lat ** 2)
                  - dlon ** 2 / (2 * a_lon ** 2))


class CloudRenderer:
    """全球背景 + 台风结构 → IR/VIS 亮温场。"""

    def __init__(self, params: Optional[dict] = None, typhoons: Optional[list] = None,
                 res: int = 2, window: Optional[Tuple[float, float, float, float]] = None,
                 colormap: Optional[str] = None):
        """res: 分辨率(度,默认 2° 快速模式;1° 全分辨率;0.1 窗口细网格)。
        window=(clat, clon, hw_la, hw_lo): 局部窗口模式(台风放大用,
        细网格下眼/眼壁结构可分辨——1° 全局网格放不下 0.4° 的眼)。
        colormap: 红外色阶名(见 colormap_data.json, 如 'CA'/'BD'/'BW'/'OTT'/
        'CC'/'RAMMB'/'RBTOP'; 默认 None = 内置 IR-BD)。"""
        self.res = res
        self.params = params or {}
        self.typhoons = typhoons or []
        self._lut = colormap_lut(colormap) if colormap else None
        if window is not None:
            clat, clon, hw_la, hw_lo = window
            self.lat = np.linspace(clat - hw_la, clat + hw_la,
                                   int(2 * hw_la / res) + 1)[:, None]
            self.lon = (np.arange(clon - hw_lo, clon + hw_lo + res, res)
                        % 360.0)[None, :]
            self.nl, self.no = self.lat.shape[0], self.lon.shape[1]
            return
        self.nl = int(120 // res) + 1
        self.no = int(360 // res)
        self.lat = np.linspace(-60, 60, self.nl)[:, None]
        self.lon = np.arange(0, 360, res)[None, :]

    # ── 全球背景 ──

    def _background(self) -> np.ndarray:
        """IR 亮温场: ITCZ 带 + 季风槽云团 + 副高边缘锋面 + 洋面低云。"""
        tb = np.full((self.nl, self.no), 25.0)     # 海面(亮温 25°C)
        rng = np.random.RandomState(7)
        # 洋面低云(8-25% 覆盖,弱信号——原 25%/-8°C 在全图呈噪点)
        low = rng.random((self.nl, self.no))
        tb = np.where(low > 0.85, tb - 3.0, tb)
        p = self.params or {}
        # ITCZ 云带(参数序列字段: lat / strength / lon_range=半宽)
        itcz = p.get('itcz') or {}
        clat = itcz.get('lat', 0)
        hw = itcz.get('lon_range', 6)
        act = itcz.get('strength', 1.0)
        band = np.exp(-((self.lat - clat) ** 2) / (2 * hw ** 2))
        # C5: 强度温和化(min(1.5,act)), 原 min(3,·) 使整带 -80°C 一片血红
        itcz_tb = -35.0 - 12.0 * min(1.5, max(0.0, act))
        tb = np.minimum(tb, itcz_tb * band + 25.0 * (1.0 - band))
        # 季风槽云团(字段: lon_range=经度中心)
        mt = p.get('monsoon_trough') or {}
        tlat = mt.get('lat', 10)
        tlon = mt.get('lon_range', 130)
        for _ in range(5):
            cl = tlat + rng.uniform(-4, 4)
            cln = (tlon + rng.uniform(-15, 15)) % 360
            blob = _gauss(self.lat, self.lon, cl, cln, 3.5, 5.0)
            tb_c = -42.0 - 10.0 * rng.uniform(0.5, 1.0)
            tb = np.minimum(tb, tb_c * blob + 25.0 * (1.0 - blob))
        # 中纬度锋面云带(副高边缘)
        ridge = p.get('ridge') or {}
        rlat = ridge.get('lat', 30)
        front = np.exp(-((self.lat - (rlat - 8)) ** 2) / (2 * 4.5 ** 2))
        front = front * (0.5 + 0.5 * np.sin(self.lon * np.pi / 40.0))
        tb = np.minimum(tb, 25.0 - 40.0 * front)
        return tb

    # ── 单台风结构 ──

    def _typhoon_field(self, ty: dict) -> np.ndarray:
        """单台风 IR 亮温: 眼/眼壁/CDO/螺旋雨带/置换结构/冷崩/切变拉扯。
        C6: 最小视觉尺寸放大(CDO≥2.2°、眼≥0.4°),全球图上可见结构。"""
        tb = np.full((self.nl, self.no), 25.0)
        la, lo, w = ty['la'], ty['lo'], ty['w']
        res = self.res
        # 修正 Rankine 剖面 → CDO 半径(随强度,下限放大)
        cdo_r = max(2.2, (60.0 + 0.45 * w) / 111.0)
        eye_r = max(0.4, (25.0 - 0.05 * w) / 111.0)
        rng = np.random.RandomState(int(ty.get('id', 0)))
        # 位置(经度回卷)
        dlon = (self.lon - lo + 180.0) % 360.0 - 180.0
        d = np.hypot((self.lat - la), dlon)
        # CDO(随强度变冷)
        cdo = _gauss(self.lat, self.lon, la, lo, cdo_r, cdo_r * 1.1)
        tb_cdo = -60.0 - 25.0 * min(1.0, w / 140.0)
        tb = np.minimum(tb, tb_cdo * cdo + 25.0 * (1 - cdo))
        # 眼(暖)
        eye = d <= eye_r
        tb = np.where(eye, -15.0, tb)
        # 眼壁环(极冷)
        wall = (d > eye_r) & (d <= eye_r + 20.0 / 111.0)
        tb = np.where(wall, -85.0, tb)
        # 螺旋雨带(对数螺旋,nb 条;仅作用于眼外——高斯宽度 1.6-2.4° 的
        # 带尾会扫回中心,原 np.minimum 把眼(-15°C)覆盖成 -43°C)
        nb = ty.get('nb', 3)
        shear_ang = ty.get('shear_angle', 0.0)
        outside = d > eye_r + 0.25
        for k in range(nb):
            base = k * 2 * math.pi / max(1, nb)
            for r_deg in np.arange(1.0, 6.0, 0.5):
                ang = base + 2.6 * math.log(1 + r_deg * 2)
                # 切变拉扯: 雨带向切变方向延伸
                cla = la + r_deg * math.cos(ang) * 0.9 + 2.0 * math.cos(shear_ang)
                clo = (lo + r_deg * math.sin(ang) * 0.9 + 2.0 * math.sin(shear_ang)) % 360
                band_r = _gauss(self.lat, self.lon, cla, clo, 1.6, 2.4)
                fade = max(0.0, 1.0 - r_deg / 6.0)
                band_val = (-40.0 - 15.0 * fade) * band_r + 25.0 * (1 - band_r)
                tb = np.where(outside, np.minimum(tb, band_val), tb)
        # 置换结构
        ew = ty.get('ew', 0)
        if ew in (3, 6):        # CUTOFF / FAILED: 双眼墙
            outer = (d > eye_r + 25.0 / 111.0) & (d <= eye_r + 50.0 / 111.0)
            tb = np.where(outer, -78.0, tb)
            tb = np.where(eye, 0.0, tb)     # 内眼回暖(消亡期)
        elif ew == 4:            # MERGE: 单环紧贴
            tight = (d > eye_r * 0.6) & (d <= eye_r * 1.6)
            tb = np.where(tight, -82.0, tb)
        elif ew == 7:            # REBUILD: 明亮小眼重回中心
            tb = np.where(eye, -70.0, tb)
        # 冷崩(白海豚式): 半圈 LG 半圈 OW
        if ty.get('cold_collapse'):
            sector = (dlon * 0 + self.lat * 0) + np.sign(dlon * math.cos(0.5) + (self.lat - la) * math.sin(0.5))
            tb = np.where((d < cdo_r * 1.2) & (sector > 0), -28.0, tb)   # LG 半圈
            tb = np.where((d < cdo_r * 1.2) & (sector <= 0), 25.0, tb)   # OW 半圈
        return tb

    # ── 合成 ──

    def _compose(self, bg: np.ndarray, tys: List[dict]) -> np.ndarray:
        out = bg.copy()
        for ty in tys:
            f = self._typhoon_field(ty)
            out = np.minimum(out, f)
        return out

    def tb_field(self, typhoons: Optional[List[dict]] = None) -> np.ndarray:
        """合成亮温场(背景 + 台风结构)。"""
        tys = typhoons if typhoons is not None else self.typhoons
        return self._compose(self._background(), tys)

    def rgb_from_tb(self, tb: np.ndarray) -> np.ndarray:
        """亮温场 → RGB(uint8,向量化)。用外部色阶 LUT(0=50°C → 255=-100°C)
        或内置 IR-BD 分段表。"""
        a = tb.astype(np.float32)
        if self._lut is not None:
            idx = np.clip(((50.0 - a) / 150.0 * 255.0).astype(int), 0, 255)
            return self._lut[idx]
        stops = np.array([t for t, _ in _IR_BD], dtype=np.float32)
        cols = np.array([c for _, c in _IR_BD], dtype=np.float32)
        idx = np.clip(np.searchsorted(stops, a, side='right') - 1, 0, len(stops) - 2)
        t0, t1 = stops[idx], stops[idx + 1]
        f = np.where(t1 > t0, (a - t0) / np.maximum(t1 - t0, 1e-6), 0.0)
        rgb = cols[idx] * (1.0 - f)[..., None] + cols[idx + 1] * f[..., None]
        return np.clip(rgb, 0, 255).astype(np.uint8)

    def render_ir(self, typhoons: Optional[List[dict]] = None) -> np.ndarray:
        """返回 (NL, NO, 3) RGB(IR-BD)。"""
        return self.rgb_from_tb(self.tb_field(typhoons))

    def render_vis(self, typhoons: Optional[List[dict]] = None) -> np.ndarray:
        """可见光: 云顶反照率(按亮温/云厚)。"""
        tb = self.tb_field(typhoons)
        albedo = np.clip((25.0 - tb) / 60.0, 0.0, 1.0)
        v = (albedo * 255).astype(np.uint8)
        return np.stack([v, v, v], axis=-1)


def to_surface(rgb: np.ndarray) -> 'pygame.Surface':
    """RGB 数组 → pygame Surface(供 F5 render_layer 对接)。"""
    import pygame
    return pygame.image.frombuffer(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), 'RGB')
