# simulator.simcore/core.py
"""SimCore 台风数值模拟器核心 —— 从参考实现(typhoon-sim.html)逐行移植。

科学依据(原文):
- Emanuel (1986/1995) MPI 气候学拟合曲线
- Atkinson & Holliday (1977): Pmin = 1010 - 0.59·V^1.13
- Holland (1980) 气压廓线; Knaff-Zehr-Courtney (2007) RMW/B
- Fiorino & Elsberry (1989) β漂移; Kaplan & DeMaria (2003) RI 判据
- 冷尾流(移动右侧偏强)、干空气卷入、陆地摩擦、日变化、高龄衰减、眼墙替换 ERC

纯 Python/numpy 实现,无 pygame 依赖(便于测试与命令行运行)。
单位: 风速 m/s, 气压 hPa, 距离 km, 时间 h。
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional

import numpy as np

C = {
    'LON0': 0.0, 'LON1': 360.0, 'LAT0': -60.0, 'LAT1': 60.0,   # 全球计算域
    'ENVD': 1.0, 'CLDD': 1.0,                                   # 环境/云网格分辨率(°)
    'DT': 10.0 / 60.0,                                          # 积分步长(小时, 10分钟)
    'R_E': 6371.0, 'OMEGA': 7.292e-5, 'RHO': 1.15,
    'CP': 1005.0, 'LV': 2.5e6,
    'DEG': math.pi / 180.0,
}
ENVNX = int(round((C['LON1'] - C['LON0']) / C['ENVD']))     # 360
ENVNY = int(round((C['LAT1'] - C['LAT0']) / C['ENVD']))     # 120
CLDNX = int(round((C['LON1'] - C['LON0']) / C['CLDD']))     # 360
CLDNY = int(round((C['LAT1'] - C['LAT0']) / C['CLDD']))     # 120

KT = 1.944                      # 风速单位换算: m/s → kt
SHEAR_MIN_KT = 2.9              # 环境风切下限(kt), 物理上避免零切变抑制

_GEODATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'ne_110m_land.geojson')

# 台风命名: 各洋区英文名表(标准 WMO 名表子集)
BASIN_NAMES = {
    'WP': ['Damrey', 'Haikui', 'Kirogi', 'Yun-yeung', 'Koinu', 'Bolaven', 'Sanba',
           'Jelawat', 'Ewiniar', 'Maliksi', 'Gaemi', 'Prapiroon', 'Maria', 'Son-Tinh',
           'Ampil', 'Wukong', 'Jongdari', 'Shanshan', 'Yagi', 'Leepi', 'Bebinca',
           'Pulasan', 'Soulik', 'Cimaron', 'Jebi', 'Krathon', 'Barijat', 'Trami',
           'Kong-rey', 'Yinxing', 'Toraji', 'Man-yi', 'Usagi', 'Pabuk', 'Wutip',
           'Sepat', 'Mun', 'Danas', 'Nari', 'Wipha'],
    'EP': ['Aletta', 'Bud', 'Carlotta', 'Daniel', 'Emilia', 'Fabio', 'Gilma',
           'Hector', 'Ileana', 'John', 'Kristy', 'Lane', 'Miriam', 'Norman',
           'Olivia', 'Paul', 'Rosa', 'Sergio', 'Tara', 'Vicente', 'Willa',
           'Xavier', 'Yolanda', 'Zeke'],
    'AL': ['Alberto', 'Beryl', 'Chris', 'Debby', 'Ernesto', 'Francine', 'Gordon',
           'Helene', 'Isaac', 'Joyce', 'Kirk', 'Leslie', 'Milton', 'Nadine',
           'Oscar', 'Patty', 'Rafael', 'Sara', 'Tony', 'Valerie', 'William'],
    'IO': ['Arnab', 'Biparjoy', 'Chapala', 'Dana', 'Fengal', 'Gulab', 'Hikaa',
           'Idai', 'Jhar', 'Kyarr', 'Laila', 'Maha', 'Nisarga', 'Ockhi', 'Phet',
           'Roanu', 'Sagar', 'Titli', 'Vardah', 'Yaas'],
    'SI': ['Anggrek', 'Bakung', 'Cempaka', 'Dahlia', 'Flamboyan', 'Hibiscus',
           'Kenanga', 'Lili', 'Mangga', 'Seroja', 'Teratai', 'Waling-waling',
           'Sajiwa', 'Pelangi', 'Tropika', 'Kembara'],
    'SP': ['Alvin', 'Bune', 'Cyril', 'Dovi', 'Eva', 'Filipo', 'Garry', 'Haley',
           'Irene', 'Judy', 'Kevin', 'Lola', 'Mal', 'Nat', 'Osai', 'Pearl',
           'Ron', 'Susan', 'Tino', 'Uesi', 'Vicky', 'Wallace'],
    'SA': ['Araci', 'Bapo', 'Cari', 'Deni', 'Eçaí', 'Guará', 'Iba', 'Jaguar',
           'Kurumí', 'Mani', 'Oquira', 'Potira'],
    'GL': ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf',
           'Hotel', 'India', 'Juliet', 'Kilo', 'Lima', 'Mike', 'November'],
}


def basin_of(lon: float, lat: float) -> str:
    """按位置划分洋区(生成/命名用)。lon 0-360。"""
    if lat >= 0:
        if 100 <= lon < 180:
            return 'WP'
        if 180 <= lon < 260:
            return 'EP'
        if 30 <= lon < 100:
            return 'IO'
        return 'AL'
    if 30 <= lon < 115:
        return 'SI'
    if 115 <= lon < 250:
        return 'SP'
    return 'SA'


# 生成区: (洋区, 经度范围, 纬度范围(含符号), 相对权重)
GEN_ZONES = [
    ('WP', 105, 165, 5, 26, 1.0),
    ('EP', 185, 250, 8, 22, 0.7),
    ('AL', 260, 330, 8, 25, 0.7),
    ('IO', 50, 95, 5, 20, 0.6),
    ('SI', 55, 110, -20, -5, 0.5),
    ('SP', 130, 200, -22, -5, 0.6),
    ('SA', 280, 330, -25, -8, 0.15),
]

# CMA 等级(中国气象局标准, 2 分钟平均风速 m/s)
CMA = [
    {'name': '热带低压', 'en': 'TD', 'min': 10.8, 'color': (53, 196, 240)},
    {'name': '热带风暴', 'en': 'TS', 'min': 17.2, 'color': (53, 240, 122)},
    {'name': '强热带风暴', 'en': 'STS', 'min': 24.5, 'color': (240, 224, 53)},
    {'name': '台风', 'en': 'TY', 'min': 32.7, 'color': (240, 152, 53)},
    {'name': '强台风', 'en': 'STY', 'min': 41.5, 'color': (240, 53, 53)},
    {'name': '超强台风', 'en': 'SuperTY', 'min': 51.0, 'color': (224, 53, 240)},
]


def grade(v: float) -> dict:
    g = CMA[0]
    for c in CMA:
        if v >= c['min']:
            g = c
    return g


def clamp(x, a, b):
    return a if x < a else (b if x > b else x)


def smoothstep(a, b, x):
    t = clamp((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


# ── 随机数(mulberry32 / 高斯) ──

def make_rng(seed: int):
    a = int(seed) & 0xFFFFFFFF

    def rng():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = ((t + ((t ^ (t >> 7)) * (t | 61))) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rng


def make_gauss(rng):
    spare = [None]

    def gauss():
        if spare[0] is not None:
            v = spare[0]
            spare[0] = None
            return v
        u = 0.0
        while u == 0:
            u = rng()
        v = 0.0
        while v == 0:
            v = rng()
        m = math.sqrt(-2 * math.log(u))
        spare[0] = m * math.sin(2 * math.pi * v)
        return m * math.cos(2 * math.pi * v)

    return gauss


# ── 风廓线 / 气压廓线(Holland 1980 / KZC07), 全 kt ──

def rmw_from_v(v_kt: float) -> float:
    return clamp(64.3 - 0.44 * v_kt * 0.514444, 12, 85)


def alpha_outer(v_kt: float) -> float:
    return 0.55 + 0.06 * min(1.0, v_kt / 107.0)


def pmin_from_v(v_kt: float) -> float:
    return 1010.0 - 0.59 * math.pow(v_kt * 0.514444, 1.13)


def holland_b(pmin: float) -> float:
    return clamp(1.5 + (980.0 - pmin) / 120.0, 1.0, 2.5)


def tangent_v(tc, r: float) -> float:
    """切向风(kt): 内核刚体旋转, 外区幂律衰减。"""
    R = tc['rmw']
    if r <= 0:
        return 0.0
    if r <= R:
        return tc['vmax'] * (r / R) ** 0.75
    return tc['vmax'] * (r / R) ** (-alpha_outer(tc['vmax']))


def radius_at_wind(tc, v0_kt: float) -> float:
    if v0_kt >= tc['vmax']:
        return 0.0
    return tc['rmw'] * (tc['vmax'] / v0_kt) ** (1.0 / alpha_outer(tc['vmax']))


def tc_deficit(tc, r: float) -> float:
    """气压亏损 (hPa) at r (km): Holland 廓线 p(r)=pc+(pn-pc)·exp(-(R/r)^B),
    亏损 pn-p(r)=(pn-pc)·(1-exp(-(R/r)^B)), 远处趋于 0(物理正确)。"""
    if r < 2:
        r = 2
    return (1010.0 - tc['pmin']) * (1.0 - math.exp(-(tc['rmw'] / r) ** tc['b']))


def vortex_wind(tc, lon, lat, level: float):
    """TC 涡旋风 [u, v] (kt, 东/北)。level: 0=低层(入流), 1=高层(微辐散)。"""
    d_lon = (lon - tc['lon']) * 111.32 * math.cos(lat * C['DEG'])
    d_lat = (lat - tc['lat']) * 110.57
    r = math.hypot(d_lon, d_lat)
    if r < 1:
        return 0.0, 0.0
    V = tangent_v(tc, r)
    phi = math.atan2(d_lat, d_lon)
    inflow = -0.08 if level > 0.5 else 0.32
    c = math.cos(inflow)
    s = math.sin(inflow) * (-1 if level > 0.5 else 1)
    u_tan = -V * math.sin(phi)
    v_tan = V * math.cos(phi)
    u = u_tan * c - v_tan * s
    v = u_tan * s + v_tan * c
    # 运动不对称: 移动方向右前方风加强
    sp = math.hypot(tc['mu'], tc['mv'])
    if sp > 1:
        f = 0.12 * sp * math.exp(-r / 400.0)
        u += (tc['mu'] / sp) * f
        v += (tc['mv'] / sp) * f
    return u, v


# ── MPI(最大潜在强度, Emanuel 气候学拟合, 输出 kt) ──

def mpi(sst: float, ohc_d26: float, outflow: float, lat: float) -> float:
    d = max(0.0, min(5.0, sst - 26.0))
    v = 70.0 + 26.73 * d - 0.729 * d * d
    if sst < 26.5:
        v *= max(0.15, (sst - 24.5) / 2.0)
    ohc_f = clamp(ohc_d26 / 75.0, 0.15, 1.4) ** 0.35
    v *= 0.8 + 0.25 * min(1.0, ohc_f)
    v *= 0.92 + 0.08 * clamp(outflow, 0, 1)
    v *= 0.72 + 0.28 * min(1.0, math.sin(lat * C['DEG']) / math.sin(18 * C['DEG']))
    return clamp(v, 0, 195)


# ── 气候学场(全球) ──
# 解析气候场的唯一公式源: sst_clim 用 numpy 逐元素实现, 标量与网格(LAT/LON 数组)通用;
# env_update 直接调用它做矢量建场, 点采样/测试同走此函数(避免双份拷贝漂移)。
# 切变/湿度等仅存在于 env_update 分支, 未在此保留并行副本。

def sst_clim(lat, lon, month):
    """全球海温气候(°C): 纬度基础 + 东太冷舌/暖池/寒流调制 + 半球季节。
    lat/lon 可为标量或 numpy 数组(逐元素); month: 1-12。"""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    a = np.abs(lat)
    t = np.where(a < 5, 29.0 - 0.12 * (5 - a),
                 28.5 - 0.10 * (a - 5) - 0.0020 * (a - 5) * (a - 5))
    # 东太平洋冷舌(100°W 附近, 赤道)
    t -= 3.5 * np.exp(-((lon - 260) / 18.0) ** 2) * np.exp(-(lat / 8.0) ** 2)
    # 北印度洋暖池
    t += 0.5 * np.exp(-((lon - 82) / 10.0) ** 2) * np.exp(-((lat - 8) / 8.0) ** 2)
    # 南半球中纬东岸寒流(秘鲁/本格拉)
    t -= 1.4 * np.exp(-((lon - 285) / 12.0) ** 2) * np.exp(-((lat + 18) / 10.0) ** 2)
    t -= 1.2 * np.exp(-((lon - 10) / 12.0) ** 2) * np.exp(-((lat + 18) / 10.0) ** 2)
    # 季节: 南北半球镜像(南半球 2 月最暖)
    season = np.where(lat >= 0, month, (month + 6) % 12 + 1)
    amp = 0.8 + 1.6 * np.minimum(1.0, a / 30.0)
    t = t + amp * np.cos(2 * np.pi * (season - 8) / 12.0)
    return float(t) if np.ndim(t) == 0 else t


# 副高中心(全球多中心): (纬度, 经度, 振幅 hPa)
RIDGES = [
    (31.0, 146.0, 10.0),   # 北西太
    (32.0, 235.0, 9.0),    # 北东太
    (33.0, 320.0, 9.0),    # 北大西洋
    (28.0, 90.0, 7.0),     # 北印度洋
    (-28.0, 90.0, 7.0),    # 南印度洋
    (-30.0, 240.0, 8.0),   # 南太平洋
    (-30.0, 340.0, 6.0),   # 南大西洋
]


# ── 陆地掩码(ne_110m_land.geojson 多边形) ──

def _load_polys() -> List[list]:
    try:
        with open(_GEODATA, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []
    polys = []
    for feat in data.get('features', []):
        geom = feat.get('geometry', {})
        if geom.get('type') != 'Polygon':
            continue
        ring = geom.get('coordinates', [[]])[0]
        flat = []
        for lon, lat in ring:
            flat.append(float(lon))
            flat.append(float(lat))
        if len(flat) >= 6:
            polys.append(flat)
    return polys


_LAND_POLYS: Optional[List[list]] = None
_LAND_CACHE: Optional[np.ndarray] = None
_LAND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'land_0.5.npz')


def build_land() -> np.ndarray:
    """0.5° 陆地比例场(3×3 子采样)。结果持久化到 land_0.5.npz(首次 ~28s, 之后 <0.1s)。"""
    global _LAND_POLYS, _LAND_CACHE
    if _LAND_CACHE is not None:
        return _LAND_CACHE
    if os.path.exists(_LAND_FILE):
        try:
            with np.load(_LAND_FILE) as z:
                arr = z['land']
            if arr.shape == (ENVNY, ENVNX):
                _LAND_CACHE = arr
                return _LAND_CACHE
        except Exception:
            _LAND_CACHE = None
    if _LAND_POLYS is None:
        _LAND_POLYS = _load_polys()
    polys = _LAND_POLYS
    if not polys:
        return np.zeros((ENVNY, ENVNX), dtype=np.float32)
    # 预计算多边形 bbox(一次), 向量化预筛候选
    bboxes = np.array([(min(p[0::2]), max(p[0::2]),
                        min(p[1::2]), max(p[1::2])) for p in polys])
    land = np.zeros((ENVNY, ENVNX), dtype=np.float32)
    for j in range(ENVNY):
        lat = C['LAT0'] + (j + 0.5) * C['ENVD']
        for i in range(ENVNX):
            lon = C['LON0'] + (i + 0.5) * C['ENVD']
            cnt = 0
            for a in range(3):
                for b in range(3):
                    lo = lon + (a - 1) * C['ENVD'] / 3.2
                    la = lat + (b - 1) * C['ENVD'] / 3.2
                    cand = np.nonzero(
                        (bboxes[:, 0] <= lo) & (bboxes[:, 1] >= lo)
                        & (bboxes[:, 2] <= la) & (bboxes[:, 3] >= la))[0]
                    hit = False
                    for k in cand:
                        if _point_in_poly(lo, la, polys[k]):
                            hit = True
                            break
                    if hit:
                        cnt += 1
                land[j, i] = cnt / 9.0
    _LAND_CACHE = land
    try:
        np.savez(_LAND_FILE, land=land)
    except Exception:
        pass
    return land


def _point_in_poly(lon: float, lat: float, flat: list) -> bool:
    inside = False
    n = len(flat)
    for k in range(0, n - 2, 2):
        x1, y1 = flat[k], flat[k + 1]
        x2, y2 = flat[k + 2], flat[k + 3]
        if (y1 > lat) != (y2 > lat) and \
                lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# ── 网格辅助 ──

def env_lon(i: int) -> float:
    return C['LON0'] + (i + 0.5) * C['ENVD']


def env_lat(j: int) -> float:
    return C['LAT0'] + (j + 0.5) * C['ENVD']


def env_bilinear(arr: np.ndarray, lon: float, lat: float) -> float:
    """arr: (ENVNY, ENVNX) 2D 场, 双线性采样。"""
    fx = (lon - C['LON0']) / C['ENVD'] - 0.5
    fy = (lat - C['LAT0']) / C['ENVD'] - 0.5
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = clamp(x0, 0, ENVNX - 2)
    y1 = clamp(y0, 0, ENVNY - 2)
    tx = clamp(fx - x0, 0, 1)
    ty = clamp(fy - y0, 0, 1)
    a = arr[y1, x1]
    b = arr[y1, x1 + 1]
    c = arr[y1 + 1, x1]
    d = arr[y1 + 1, x1 + 1]
    return float((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)


def land_at(sim, lon: float, lat: float) -> float:
    return env_bilinear(sim['land'], lon, lat)


# ── 模拟状态 ──

def init(opts: Optional[dict] = None) -> dict:
    o = opts or {}
    seed = int(o.get('seed', 1)) & 0xFFFFFFFF
    rng = make_rng(seed)
    params = {
        'sstAnom': 0.0, 'ohcMul': 1.0, 'shearOff': 0.0, 'rhOff': 0.0,
        'shAmp': 10.0, 'shLon': 146.0, 'shLat': 31.0,
        'outflow': 0.5, 'steerU': 0.0, 'steerV': 0.0,
        'random': 0.5, 'genesis': True, 'genesisRate': 1.0,
        # 数据驱动模式(借鉴 KWP): 用真实气候库替代解析公式
        'dataMode': False,        # True = 真实气候数据, False = 解析公式
        'dataYear': None,         # 具体年份(如 2020); None = 用气候态
        'dataClim': True,         # True=用气候态(_clim), False=用具体年份库(_library)
        'dataDayCycle': 1.0,      # 日内太阳周期幅度(0=关)
    }
    params.update(o.get('params') or {})
    sim = {
        't': 0.0,
        'month0': int(o.get('month', 8)),
        'month': int(o.get('month', 8)),
        'seed': seed,
        'rng': rng,
        'gauss': make_gauss(rng),
        'params': params,
        'tcs': [],
        'nextId': 1,
        'usedNames': set(),
        'eddies': [],
        'dryBlobs': [],
        'wake': np.zeros(ENVNX * ENVNY, dtype=np.float32),
        'ohcWake': np.zeros(ENVNX * ENVNY, dtype=np.float32),
        'cld': np.zeros(CLDNX * CLDNY, dtype=np.float32),
        'cldNoise': np.zeros(CLDNX * CLDNY, dtype=np.float32),
        'env': {
            'sst': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'ohc': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'shear': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'rh': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'u850': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'v850': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'u200': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'v200': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'uMid': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'vMid': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'pEnv': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'pTot': np.zeros((ENVNY, ENVNX), dtype=np.float32),
            'div': np.zeros((ENVNY, ENVNX), dtype=np.float32),
        },
        'land': build_land(),
        'lastEnvH': -1.0, 'lastEnsH': -1.0, 'lastGenH': -1.0,
        'dataState': 'off', 'dataWarn': '',
        'ensemble': None,
    }
    # 静态云噪声(平滑随机)
    nr = make_rng(seed ^ 0x9E3779B9)
    small = np.array([nr() * 2 - 1 for _ in range(37 * 25)], dtype=np.float32)
    for j in range(CLDNY):
        for i in range(CLDNX):
            sx = i / CLDNX * 36.0
            sy = j / CLDNY * 24.0
            x0 = int(sx)
            y0 = int(sy)
            fx = sx - x0
            fy = sy - y0

            def g(x, y):
                return small[(y % 25) * 37 + (x % 37)]

            v = (g(x0, y0) * (1 - fx) + g(x0 + 1, y0) * fx) * (1 - fy) + \
                (g(x0, y0 + 1) * (1 - fx) + g(x0 + 1, y0 + 1) * fx) * fy
            sim['cldNoise'][j * CLDNX + i] = v
    # 初始扰动涡旋/干空气块
    for _ in range(7):
        sim['eddies'].append(make_eddy(sim, True))
    for _ in range(3):
        sim['dryBlobs'].append(make_dry_blob(sim))
    return sim


def make_eddy(sim, any_spot: bool = False) -> dict:
    rng = sim['rng']
    return {
        'lon': C['LON0'] + rng() * (C['LON1'] - C['LON0']),
        'lat': -25 + rng() * 50,
        'amp': (1 if rng() < 0.55 else -1) * (2 + rng() * 4),
        'R': 350 + rng() * 500,
        'age': 0.0, 'life': 60 + rng() * 120,
        'u': (rng() - 0.35) * 3, 'v': (rng() - 0.5) * 2,
    }


def make_dry_blob(sim) -> dict:
    rng = sim['rng']
    return {
        'lon': C['LON0'] + rng() * (C['LON1'] - C['LON0']),
        'lat': -22 + rng() * 44,
        'R': 350 + rng() * 500, 'str': 18 + rng() * 20,
        'age': 0.0, 'life': 60 + rng() * 100,
        'u': (rng() - 0.4) * 3.5, 'v': (rng() - 0.5) * 1.5,
    }


# ── 环境场重建(每模拟小时, numpy 向量化) ──

def _env_grids():
    lats = np.linspace(C['LAT0'] + 0.5 * C['ENVD'], C['LAT1'] - 0.5 * C['ENVD'], ENVNY)
    lons = np.linspace(C['LON0'] + 0.5 * C['ENVD'], C['LON1'] - 0.5 * C['ENVD'], ENVNX)
    return np.meshgrid(lats, lons, indexing='ij')


def env_update(sim) -> None:
    env = sim['env']
    P = sim['params']
    LAT, LON = _env_grids()
    NX, NY = ENVNX, ENVNY
    month = sim['month']
    # ── 数据驱动模式(KWP 思路): 真实气候库覆盖解析公式 ──
    # 加载失败/缺数据显示为降级(fallback/partial), 而非静默退回解析场——
    # 否则"数据环境"徽标会对真实状态撒谎且错误无法诊断。
    DATA = None
    data_err = ''
    if P.get('dataMode'):
        try:
            from . import climate as _CL
            DATA = _CL.build_env_fields(P, sim['t'], sim['month0'])
        except Exception as exc:
            DATA = None
            data_err = f'数据驱动环境场加载异常: {exc}'
    t = sim['t']
    a = np.abs(LAT)
    if DATA is not None and 'sst' in DATA:
        # 真实海温(气候库) + 冷尾流 + 距平 + 微扰动
        sst = DATA['sst'].copy()
        # 微扰动幅度 0.06: 数据模式真实场已含细结构, 刻意小于解析模式的 0.25
        env['sst'] = (sst + P['sstAnom']
                      + 0.06 * np.sin(t / 8 + LON * 0.35) * np.exp(-a / 12)
                      - sim['wake'].reshape(NY, NX)).astype(np.float32)
    else:
        # 海温(全球, 解析)—— 唯一公式源 sst_clim, 此处矢量应用到网格(避免双份漂移)
        sst = sst_clim(LAT, LON, month)
        env['sst'] = (sst + P['sstAnom']
                      # 0.25: 解析平滑场所需的天气尺度纹理扰动(数据模式用 0.06)
                      + 0.25 * np.sin(t / 8 + LON * 0.35) * np.exp(-a / 12)
                      - sim['wake'].reshape(NY, NX)).astype(np.float32)

    if DATA is not None and 'ohc' in DATA:
        # 真实热含量 + OHC 消耗
        d26 = DATA['ohc'].copy() * P['ohcMul'] + 6 * np.sin(t / 20 + LON * 0.11 + LAT * 0.2)
        env['ohc'] = np.maximum(18.0, d26 - sim['ohcWake'].reshape(NY, NX)).astype(np.float32)
    else:
        # D26(全球) + 台风过境 OHC 消耗(恢复慢)
        d26 = 90.0 + 20.0 * np.exp(-((a - 9) / 9.0) ** 2)
        d26 -= 28.0 * np.exp(-((LON - 156) / 9.0) ** 2) * np.exp(-((a - 6) / 5.0) ** 2)
        d26 -= 30.0 * np.exp(-((LON - 260) / 12.0) ** 2) * np.exp(-(a / 7.0) ** 2)
        d26 -= 12.0 * np.exp(-((LON - 30) / 14.0) ** 2) * np.exp(-((LAT + 20) / 9.0) ** 2)
        d26 = d26 * P['ohcMul'] + 12 * np.sin(t / 20 + LON * 0.11 + LAT * 0.2)
        env['ohc'] = np.maximum(18.0, d26 - sim['ohcWake'].reshape(NY, NX)).astype(np.float32)

    # 季风槽/副高纬度带(公共, 供气压场用)
    month_a = month
    mt_n = 10 + 3.5 * math.sin(2 * math.pi * (month_a - 5) / 12) + 2
    mt_s = -(10 + 3.5 * math.sin(2 * math.pi * (month_a - 11) / 12) + 2)

    # 风切变(kt): 数据模式用真实切变, 否则解析
    if DATA is not None and 'shear' in DATA:
        shear = DATA['shear'].copy()
        # 叠加天气尺度正/异常切变包(动态天气)
        shear = shear + 2.2 * np.sin(2 * np.pi * (t / 620.0 + (LON - 100.0) / 70.0))
        env['shear'] = np.maximum(SHEAR_MIN_KT, (shear + P['shearOff'])).astype(np.float32)
    else:
        # 风切变(kt, 半球镜像季节)
        shear = 4.0 + 0.28 * np.maximum(0.0, a - 10) + 0.05 * np.maximum(0.0, a - 18) ** 1.5
        season_a = np.where(LAT >= 0, month, (month + 6) % 12 + 1)
        shear += 3.0 * np.abs(np.sin(np.pi * (season_a - 8) / 12.0))
        shear += 2.5 * np.sin(2 * np.pi * (t / 620.0 + (LON - 100.0) / 70.0))
        env['shear'] = np.maximum(SHEAR_MIN_KT, (shear + P['shearOff']) * KT).astype(np.float32)

    # 中层湿度: 数据模式用真实湿度, 否则解析; 干空气块叠加保留
    if DATA is not None and 'rh' in DATA:
        rh = DATA['rh'].copy() + P['rhOff']
        for db in sim['dryBlobs']:
            dx = (LON - db['lon']) * 111.32 * np.cos(LAT * C['DEG'])
            dy = (LAT - db['lat']) * 110.57
            rr = np.hypot(dx, dy) / db['R']
            rh -= db['str'] * np.exp(-rr * rr)
        env['rh'] = np.clip(rh, 25, 95).astype(np.float32)
    else:
        # 中层湿度(双季风槽湿区 + 副高干区 + 干空气块)
        rh = 70.0 + 13.0 * np.exp(-((LAT - mt_n) / 4.5) ** 2) \
             + 13.0 * np.exp(-((LAT - mt_s) / 4.5) ** 2)
        sh_n = 26 + 4 * math.sin(2 * math.pi * (month - 8) / 12)
        sh_s = -(26 + 4 * math.sin(2 * math.pi * (month - 2) / 12))
        rh -= 9.0 * np.exp(-((LAT - sh_n) / 4.0) ** 2)
        rh -= 9.0 * np.exp(-((LAT - sh_s) / 4.0) ** 2)
        rh = rh + P['rhOff']
        for db in sim['dryBlobs']:
            dx = (LON - db['lon']) * 111.32 * np.cos(LAT * C['DEG'])
            dy = (LAT - db['lat']) * 110.57
            rr = np.hypot(dx, dy) / db['R']
            rh -= db['str'] * np.exp(-rr * rr)
        env['rh'] = np.clip(rh, 25, 95).astype(np.float32)

    # 环境气压: 数据模式用真实 mslp 作基底, 否则解析(多副高 + 季风槽 + 涡旋)
    if DATA is not None and 'mslp' in DATA:
        pe = DATA['mslp'].copy()
        for ed in sim['eddies']:
            dx = (LON - ed['lon']) * 111.32 * np.cos(LAT * C['DEG'])
            dy = (LAT - ed['lat']) * 110.57
            rr = np.hypot(dx, dy) / ed['R']
            pe += ed['amp'] * np.exp(-rr * rr)
        env['pEnv'] = pe.astype(np.float32)
    else:
        pe = 1011.0 - 2.5 * np.exp(-((LAT - 3) / 8.0) ** 2) \
             - 2.5 * np.exp(-((LAT + 3) / 8.0) ** 2)
        for rlat, rlon, ramp in RIDGES:
            d = LON - rlon
            d = (d + 180.0) % 360.0 - 180.0          # 经度周期化
            dy_r = (LAT - rlat) / 5.5
            dx_shw = np.minimum(0.0, d / 22.0)
            dx_she = np.maximum(0.0, d / 12.0)
            pe += ramp * np.exp(-(dy_r * dy_r) - (dx_shw * dx_shw) - (dx_she * dx_she))
        for mlat, mlon in ((mt_n, 128.0), (mt_s, 128.0)):
            dy_mt = (LAT - mlat) / 5.5
            dx_mt = (LON - mlon) / 30.0
            pe -= 6.5 * np.exp(-(dy_mt * dy_mt) - (dx_mt * dx_mt))
        for ed in sim['eddies']:
            dx = (LON - ed['lon']) * 111.32 * np.cos(LAT * C['DEG'])
            dy = (LAT - ed['lat']) * 110.57
            rr = np.hypot(dx, dy) / ed['R']
            pe += ed['amp'] * np.exp(-rr * rr)
        env['pEnv'] = pe.astype(np.float32)

    # 地转风 + 摩擦; 200hPa 近似; 辐合(有限差分)
    f = 2 * C['OMEGA'] * np.sin(LAT * C['DEG'])
    f_eff = np.where(f >= 0, 1.0, -1.0) * np.maximum(np.abs(f), 3.2e-5)
    cos_lat = np.cos(LAT * C['DEG'])
    dx_m = C['ENVD'] * 111.32 * cos_lat
    dy_m = C['ENVD'] * 110.57
    pe = env['pEnv']
    dpdx = np.empty_like(pe)
    dpdx[:, 1:-1] = (pe[:, 2:] - pe[:, :-2]) / (2.0 * dx_m[:, 1:-1])
    dpdx[:, 0] = (pe[:, 1] - pe[:, 0]) / dx_m[:, 0]
    dpdx[:, -1] = (pe[:, -1] - pe[:, -2]) / dx_m[:, -1]
    dpdy = np.empty_like(pe)
    dpdy[1:-1, :] = (pe[2:, :] - pe[:-2, :]) / (2.0 * dy_m)
    dpdy[0, :] = (pe[1, :] - pe[0, :]) / dy_m
    dpdy[-1, :] = (pe[-1, :] - pe[-2, :]) / dy_m
    ug = -0.1 * dpdy / (C['RHO'] * f_eff)
    vg = 0.1 * dpdx / (C['RHO'] * f_eff)
    sp = np.hypot(ug, vg)
    ang = 0.30
    c = math.cos(ang) * 0.88
    s = math.sin(ang) * 0.88
    uf = ug * c - vg * s
    vf = ug * s + vg * c
    # 数据模式: 真实引导(uv_steer) + 解析地转补充(完整经向结构/天气涡旋) + 扰动
    if DATA is not None and 'u850' in DATA:
        # 解析地转(含完整南北分量与副高/季风槽结构)
        ua = np.where(sp > 0.3, uf, 0.0) * KT
        va = np.where(sp > 0.3, vf, 0.0) * KT
        # 真实引导(uv_steer)保留大尺度方向; 解析补充补齐结构缺失分量
        g = 0.55                              # 真实引导权重
        u850 = g * DATA['u850'].copy() + (1 - g) * ua + P['steerU'] * KT * 0.5
        v850 = g * DATA['v850'].copy() + (1 - g) * va + P['steerV'] * KT * 0.5
        env['u850'] = u850.astype(np.float32)
        env['v850'] = v850.astype(np.float32)
        sh_u = env['shear'] * 0.9
        sh_v = env['shear'] * 0.28
        env['u200'] = (u850 * 1.45 + sh_u).astype(np.float32)
        env['v200'] = (v850 * 1.45 + sh_v).astype(np.float32)
        env['uMid'] = (u850 * 1.225 + sh_u * 0.5).astype(np.float32)
        env['vMid'] = (v850 * 1.225 + sh_v * 0.5).astype(np.float32)
    else:
        # 地转风 → kt(全程序风速统一 kt)
        u850 = np.where(sp > 0.3, uf, 0.0) * KT + P['steerU'] * 0.5 * KT
        v850 = np.where(sp > 0.3, vf, 0.0) * KT + P['steerV'] * 0.5 * KT
        env['u850'] = u850.astype(np.float32)
        env['v850'] = v850.astype(np.float32)
        sh_u = env['shear'] * 0.9
        sh_v = env['shear'] * 0.28
        env['u200'] = (u850 * 1.45 + sh_u).astype(np.float32)
        env['v200'] = (v850 * 1.45 + sh_v).astype(np.float32)
        env['uMid'] = (u850 * 1.225 + sh_u * 0.5).astype(np.float32)
        env['vMid'] = (v850 * 1.225 + sh_v * 0.5).astype(np.float32)
    du = np.empty_like(u850)
    du[:, 1:-1] = (u850[:, 2:] - u850[:, :-2]) / (2.0 * dx_m[:, 1:-1])
    du[:, 0] = (u850[:, 1] - u850[:, 0]) / dx_m[:, 0]
    du[:, -1] = (u850[:, -1] - u850[:, -2]) / dx_m[:, -1]
    dv = np.empty_like(v850)
    dv[1:-1, :] = (v850[2:, :] - v850[:-2, :]) / (2.0 * dy_m)
    dv[0, :] = (v850[1, :] - v850[0, :]) / dy_m
    dv[-1, :] = (v850[-1, :] - v850[-2, :]) / dy_m
    env['div'] = (du + dv).astype(np.float32)

    # 总气压 = 环境 + TC 亏损
    p_tot = env['pEnv'].copy()
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        dx = (LON - tc['lon']) * 111.32 * cos_lat
        dy = (LAT - tc['lat']) * 110.57
        r = np.hypot(dx, dy)
        r = np.maximum(r, 2.0)
        p_tot -= (1010.0 - tc['pmin']) * (1.0 - np.exp(-(tc['rmw'] / r) ** tc['b']))
    env['pTot'] = p_tot.astype(np.float32)
    # 数据驱动状态(供 UI 徽标/诊断): off / loaded / partial / fallback
    if not P.get('dataMode'):
        sim['dataState'] = 'off'
        sim['dataWarn'] = ''
    elif DATA is None:
        sim['dataState'] = 'fallback'
        sim['dataWarn'] = data_err or '数据驱动: 无可用气候库数据, 已退回解析场'
    else:
        need = ('sst', 'ohc', 'shear', 'rh', 'u850', 'v850')
        miss = [k for k in need if k not in DATA]
        if miss:
            sim['dataState'] = 'partial'
            sim['dataWarn'] = '数据驱动: 缺少 ' + ', '.join(miss) + ', 已用解析场补齐'
        else:
            sim['dataState'] = 'loaded'
            sim['dataWarn'] = ''
    sim['lastEnvH'] = sim['t']


# ── 环境采样 / 引导气流 / β漂移 ──

def sample_env(sim, lon: float, lat: float) -> dict:
    env = sim['env']
    sst = env_bilinear(env['sst'], lon, lat)
    ohc = env_bilinear(env['ohc'], lon, lat)
    shear = env_bilinear(env['shear'], lon, lat)
    rh = env_bilinear(env['rh'], lon, lat)
    land = land_at(sim, lon, lat)
    lf = land
    for k in range(4):
        a = k * math.pi / 2 + 0.7
        lf += land_at(sim, lon + 0.9 * math.cos(a), lat + 0.9 * math.sin(a))
    lf /= 5.0
    return {'sst': sst, 'ohc': ohc, 'shear': shear, 'rh': rh,
            'land': clamp(lf, 0, 1), 'lat': lat}


def steering_at(sim, lon: float, lat: float):
    """距中心 300-600km 环带上的深层平均环境风(850/200 加权, kt)。"""
    su = sv = w = 0.0
    env = sim['env']
    for k in range(6):
        a = k * math.pi / 3 + 0.5
        r = 340.0 + (k % 2) * 180.0
        lo = lon + r / 111.32 / max(1e-6, math.cos(lat * C['DEG'])) * math.cos(a)
        la = lat + r / 110.57 * math.sin(a)
        if lo < C['LON0'] + 1 or lo > C['LON1'] - 1 or la < C['LAT0'] + 1 or la > C['LAT1'] - 1:
            continue
        wl = 1 - land_at(sim, lo, la)
        if wl < 0.3:
            continue
        u8 = env_bilinear(env['u850'], lo, la)
        v8 = env_bilinear(env['v850'], lo, la)
        u2 = env_bilinear(env['u200'], lo, la)
        v2 = env_bilinear(env['v200'], lo, la)
        su += (0.75 * u8 + 0.25 * u2) * wl
        sv += (0.75 * v8 + 0.25 * v2) * wl
        w += wl
    if w < 0.5:
        su = env_bilinear(env['u850'], lon, lat)
        sv = env_bilinear(env['v850'], lon, lat)
    else:
        su /= w
        sv /= w
    su += sim['params']['steerU'] * KT
    sv += sim['params']['steerV'] * KT
    return su, sv


def beta_drift(lat: float):
    """β漂移(kt): 向赤道侧偏西 + 向极。"""
    f = clamp(math.sin(lat * C['DEG']) / math.sin(15 * C['DEG']), 0.25, 1.4)
    return -3.4 * f, 2.24 * f


def local_hour(sim, lon: float) -> float:
    return ((sim['t'] + (lon - 120) / 15.0) % 24 + 24) % 24


# ── TC 创建 ──

def make_tc(sim, lon: float, lat: float, vmax_kt: float) -> dict:
    vmax_kt = clamp(vmax_kt, 20, 155)
    pmin = pmin_from_v(vmax_kt)
    rmw = rmw_from_v(vmax_kt)
    basin = basin_of(lon, lat)
    names = BASIN_NAMES.get(basin, BASIN_NAMES['GL'])
    name = names[sim['nextId'] % len(names)]
    tc = {
        'id': sim['nextId'],
        'name': name,
        'basin': basin,
        'lon': lon, 'lat': lat, 'vmax': vmax_kt, 'pmin': pmin,
        'rmw': rmw, 'b': holland_b(pmin),
        'age': 0.0, 'dead': False, 'isUser': len(sim['tcs']) == 0,
        'mu': 0.0, 'mv': 0.0, 'noiseU': 0.0, 'noiseV': 0.0,
        'erc': None, 'ri': None, 'riCD': 0.0, 'weak': 0.0,
        'anvil': 0.0, 'phase': 'TC',
        'track': [{'t': sim['t'], 'lon': lon, 'lat': lat, 'vmax': vmax_kt, 'pmin': pmin}],
        'hist': [],
        'ace': 0.0, 'distKm': 0.0, 'lastLon': lon, 'lastLat': lat,
        'genHour': sim['t'],
    }
    sim['nextId'] += 1
    sim['tcs'].append(tc)
    return tc


# ── 强度演变(SHIPS 风格 + RI + ERC + 陆地衰减 + 消散 + 高空反气旋) ──

def _tc_distance_km(tc, o) -> float:
    dx = (o['lon'] - tc['lon']) * 111.32 * math.cos(tc['lat'] * C['DEG'])
    dy = (o['lat'] - tc['lat']) * 110.57
    return math.hypot(dx, dy)


def intensity_step(sim, tc, dt: float) -> None:
    env = sample_env(sim, tc['lon'], tc['lat'])
    tc['env'] = env
    vpi = mpi(env['sst'], env['ohc'], sim['params']['outflow'], env['lat'])
    # 陆地抑制 MPI: 陆面上潜在强度大打折扣, 防止"陆上反而增强"
    if env['land'] > 0.05:
        vpi *= 1.0 - 0.85 * min(1.0, env['land'])
    tau = 38.0 if tc['vmax'] < vpi else 20.0
    dV = (vpi - tc['vmax']) / tau

    # ── 高空反气旋(anvil): 台风头顶的暖性反气旋, 抵削环境风切 ──
    # 强度随台风强度与存活时间增长; 大风切持续破坏; 残余切变 5-10kt 不可消除
    anvil = tc.get('anvil', 0.0)
    anvil_target = min(1.0, (tc['vmax'] / 155.0) * (0.55 + 0.45 * min(1.0, tc['age'] / 240.0)))
    anvil += (anvil_target - anvil) * min(1.0, dt / 12.0)
    if env['shear'] > 29.0:                       # 强风切持续摧毁反气旋
        anvil -= dt / 30.0
    elif env['shear'] > 19.0:
        anvil -= dt / 90.0
    anvil = clamp(anvil, 0.0, 1.0)
    tc['anvil'] = anvil
    # 有效切变 = 环境切变被反气旋抵削, 但至少残留 6kt
    shear_eff = max(6.0, env['shear'] * (1.0 - 0.82 * anvil))
    # 其它台风的高空反气旋也会对本站制造风切(双台风靠近互相削弱的重要原因)
    for o in sim['tcs']:
        if o is tc or o['dead']:
            continue
        d = _tc_distance_km(tc, o)
        if d < 1500:
            f = 1.0 - d / 1500.0
            shear_eff += 3.0 * (0.3 + o['vmax'] / 155.0) * f
    fs = math.exp(-(max(0.0, shear_eff - 6.0) / 21.4) ** 2)
    fd = math.exp(-((70.0 - env['rh']) / 42.0) ** 1.4) if env['rh'] < 70 else 1.0
    dV *= fs * fd
    # 陆地衰减(增强): 按陆地比例强衰减 + 登陆快速削弱
    if env['land'] > 0.02:
        dV -= tc['vmax'] * min(1.0, env['land']) * 0.12
    if env['land'] > 0.3:
        dV -= tc['vmax'] * 0.02
    if env['sst'] < 26.5:
        dV -= tc['vmax'] * (26.5 - env['sst']) * 0.04
    # 高龄衰减(10 天后)
    if tc['age'] > 240:
        dV -= 0.45 * (tc['age'] - 240) / 260.0 * (0.5 + tc['vmax'] / 155.0)
    # 弱台风持续不利环境 → 结构退化累积(解决"永不消散")
    weak = tc.get('weak', 0.0)
    if tc['vmax'] < 60 and (env['sst'] < 26.5 or env['shear'] > 23.0 or env['land'] > 0.25):
        weak += dt / 6.0          # 每 6h +1
        dV -= tc['vmax'] * 0.012 * weak
    else:
        weak = max(0.0, weak - dt / 24.0)
    tc['weak'] = weak
    # 日变化(03 时对流峰值)
    lh = local_hour(sim, tc['lon'])
    dV *= 1 + 0.05 * math.cos(2 * math.pi * (lh - 3) / 24.0)
    # 随机强迫(kt)
    dV += sim['gauss']() * sim['params']['random'] * 0.27 * (0.5 + tc['vmax'] / 155.0)
    # RI(Kaplan-DeMaria 判据; 每 6h 约 10-20% 概率)
    tc['riCD'] -= dt
    if (not tc['ri'] and tc['riCD'] <= 0 and 27 < tc['vmax'] < 136
            and env['sst'] >= 26.5 and env['shear'] < 14.6 and env['rh'] > 62
            and env['land'] < 0.1 and vpi - tc['vmax'] > 23):
        if sim['rng']() < (0.10 * sim['params']['random'] + 0.03) * dt * 6 * 0.028:
            tc['ri'] = {'t': 20 + sim['rng']() * 14}
            tc['riCD'] = 60 + sim['rng']() * 100
    if tc['ri']:
        tc['ri']['t'] -= dt
        dV += 2.24
        if tc['ri']['t'] <= 0:
            tc['ri'] = None
    # ERC(成熟强台风接近 MPI 时; 概率较原版提高 ~3 倍, 每 2-6 天一次)
    if (not tc['erc'] and tc['vmax'] > 105 and env['sst'] > 26
            and env['land'] < 0.15 and vpi - tc['vmax'] < 27
            and sim['rng']() < 0.00035 * dt * 60):
        tc['erc'] = {'T': 16 + sim['rng']() * 20, 't': 0.0,
                     'rF': 1.45 + sim['rng']() * 0.35}
    v_target = 1.0
    if tc['erc']:
        tc['erc']['t'] += dt
        x = tc['erc']['t'] / tc['erc']['T']
        v_target = 1 - 0.30 * math.sin(math.pi * min(1.0, x))
        if x >= 1:
            tc['erc'] = None
            dV += 4.9
    tc['vmax'] = clamp(tc['vmax'] + dV * dt * v_target, 6, 185)
    tc['pmin'] = pmin_from_v(tc['vmax'])
    tc['rmw'] = rmw_from_v(tc['vmax'])
    if tc['erc']:
        tc['rmw'] *= 1 + 0.55 * math.sin(math.pi * min(1.0, tc['erc']['t'] / tc['erc']['T']))
    tc['b'] = holland_b(tc['pmin'])

    # ── 变性过程 TC → SD(副热带) → SS/EX(温带) ──
    # 结构退化到一定程度且环境不支持时转为副热带/温带, 不再按热带增强
    phase = tc.get('phase', 'TC')
    if phase == 'TC':
        if (weak > 20 or (tc['age'] > 168 and tc['vmax'] < 50
                          and (env['sst'] < 24.5 or abs(env['lat']) > 32))
                or (env['land'] > 0.6 and tc['vmax'] < 45)):
            phase = 'SD'
    elif phase == 'SD':
        if (tc['vmax'] < 32 or env['sst'] < 20 or weak > 45):
            phase = 'SS' if abs(env['lat']) < 30 else 'EX'
        elif tc['vmax'] > 70 and env['sst'] > 26.5 and env['land'] < 0.1:
            phase = 'TC'          # 重新热带化
    if phase != 'TC':
        # 变性后: 不再受 MPI 增强驱动, 仅缓慢衰减/维持; 移入冷海快速消亡
        dV2 = -0.08 * (tc['vmax'] / 60.0)
        if phase == 'EX' and env['sst'] < 18:
            dV2 -= 0.5
        tc['vmax'] = clamp(tc['vmax'] + dV2 * dt, 12, 999)
        tc['pmin'] = pmin_from_v(tc['vmax'])
    tc['phase'] = phase

    # 消散判定(加强): 弱到极限 / 结构退化累积超限 / 极弱+冷海或深陆
    if tc['vmax'] < 20 or weak > 60:
        tc['dead'] = True
    elif tc['vmax'] < 29 and (env['sst'] < 25.2 or env['land'] > 0.65):
        tc['dead'] = True
    elif phase == 'EX' and tc['vmax'] < 26:
        tc['dead'] = True
    tc['age'] += dt


# ── 移动 ──

def deposit_gauss(arr: np.ndarray, lon: float, lat: float, Rkm: float,
                  amp: float, km_per_lon: float, km_per_lat: float) -> None:
    i0 = int(math.floor((lon - C['LON0']) / C['ENVD']))
    j0 = int(math.floor((lat - C['LAT0']) / C['ENVD']))
    n = int(math.ceil((Rkm * 2.2) / (C['ENVD'] * 111.32))) + 1
    inv_r2 = 1.0 / (Rkm * Rkm)
    for dj in range(-n, n + 1):
        j = j0 + dj
        if j < 0 or j >= ENVNY:
            continue
        for di in range(-n, n + 1):
            i = i0 + di
            if i < 0 or i >= ENVNX:
                continue
            d_lon_km = (env_lon(i) - lon) * km_per_lon
            d_lat_km = (env_lat(j) - lat) * km_per_lat
            g = math.exp(-(d_lon_km * d_lon_km + d_lat_km * d_lat_km) * inv_r2)
            arr[j * ENVNX + i] = min(6.0, arr[j * ENVNX + i] + amp * g)


def motion_step(sim, tc, dt: float) -> None:
    steer = steering_at(sim, tc['lon'], tc['lat'])
    beta = beta_drift(tc['lat'])
    r = sim['params']['random']
    tc['noiseU'] += (sim['gauss']() * 2.1 * (0.4 + r) - tc['noiseU']) * 0.10
    tc['noiseV'] += (sim['gauss']() * 2.1 * (0.4 + r) - tc['noiseV']) * 0.10
    u = steer[0] + beta[0] + tc['noiseU'] * r * 1.6
    v = steer[1] + beta[1] + tc['noiseV'] * r * 1.6
    # Fujiwhara 双台风互旋(弱台风影响按强度加权)
    for o in sim['tcs']:
        if o is tc or o['dead']:
            continue
        dx = (o['lon'] - tc['lon']) * 111.32 * math.cos(tc['lat'] * C['DEG'])
        dy = (o['lat'] - tc['lat']) * 110.57
        rr = math.hypot(dx, dy)
        if rr > 1200:
            continue
        ux, vy = vortex_wind(o, tc['lon'], tc['lat'], 0)
        wgt = 0.5 * min(1.0, o['vmax'] / max(1.0, tc['vmax']))
        u += ux * wgt
        v += vy * wgt
    lf = land_at(sim, tc['lon'], tc['lat'])
    if lf > 0.1:
        u *= 1 - 0.25 * min(1.0, lf)
        v *= 1 - 0.25 * min(1.0, lf)
    tc['mu'] = u
    tc['mv'] = v
    # 位移: u/v 为 kt(海里/小时), 60 海里/度
    tc['lon'] += u * dt / (60.0 * math.cos(tc['lat'] * C['DEG']))
    tc['lat'] += v * dt / 60.0
    tc['distKm'] += math.hypot(u * 1.852 * dt, v * 1.852 * dt)
    # 冷尾流沉积(kt): 移动方向右侧 60km, 强度 ∝ V²; 同时消耗 OHC(恢复更慢)
    spd = math.hypot(u, v)
    env = tc.get('env') or sample_env(sim, tc['lon'], tc['lat'])
    if spd > 6 and env['sst'] > 25:
        cos_a = u / spd if spd > 0.5 else 0.0
        sin_a = v / spd if spd > 0.5 else 0.0
        cx = tc['lon'] + (60 * cos_a - 60 * sin_a) / 111.32 / max(1e-6, math.cos(tc['lat'] * C['DEG']))
        cy = tc['lat'] + (60 * sin_a + 60 * cos_a) / 110.57
        ohc_f = 1 - 0.55 * min(1.0, env['ohc'] / 90.0)
        k = 0.30 * (tc['vmax'] / 117.0) ** 2 * ohc_f * (0.7 + 0.3 * sim['rng']())
        Rkm = 150 + 40 * min(1.0, tc['vmax'] / 97.0)
        deposit_gauss(sim['wake'], cx, cy, Rkm, k * dt,
                      111.32 * math.cos(cy * C['DEG']), 110.57)
        # OHC 消耗(量级为 D26 km 单位)
        deposit_gauss(sim['ohcWake'], cx, cy, Rkm + 60, k * dt * 3.0,
                      111.32 * math.cos(cy * C['DEG']), 110.57)
    # 边界
    if (tc['lon'] < C['LON0'] + 0.4 or tc['lon'] > C['LON1'] - 0.4
            or tc['lat'] < C['LAT0'] + 0.4 or tc['lat'] > C['LAT1'] - 0.4):
        tc['dead'] = True
    # ACE 累积
    tc['ace'] += tc['vmax'] * tc['vmax'] * 1e-4 * (dt / 6.0)


# ── 涡旋 / 干块演化 ──

def eddy_step(sim, dt: float) -> None:
    for ed in sim['eddies']:
        ed['age'] += dt
        ed['lon'] += ed['u'] * 3.6 * dt / 111.32
        ed['lat'] += ed['v'] * 3.6 * dt / 110.57
        ed['amp'] *= math.exp(-dt / 90.0)
        ed['u'] += (sim['gauss']() * 0.4 - ed['u']) * 0.05
        ed['v'] += (sim['gauss']() * 0.3 - ed['v']) * 0.05
        if ed['age'] > ed['life'] or ed['amp'] * ed['amp'] < 0.5:
            ed['lon'] = C['LON0'] + sim['rng']() * (C['LON1'] - C['LON0'])
            ed['lat'] = -25 + sim['rng']() * 50
            ed['amp'] = (1 if sim['rng']() < 0.55 else -1) * (2 + sim['rng']() * 4)
            ed['R'] = 350 + sim['rng']() * 500
            ed['age'] = 0.0
            ed['life'] = 60 + sim['rng']() * 120
    for db in sim['dryBlobs']:
        db['age'] += dt
        db['lon'] += db['u'] * 3.6 * dt / 111.32
        db['lat'] += db['v'] * 3.6 * dt / 110.57
        if db['age'] > db['life']:
            new = make_dry_blob(sim)
            new['age'] = 0.0
            sim['dryBlobs'].remove(db)
            sim['dryBlobs'].append(new)


# ── 扰动生成(全球多洋区: 东风波/季风槽辐合) ──

def gen_check(sim) -> None:
    if not sim['params']['genesis']:
        return
    if len([t for t in sim['tcs'] if not t['dead']]) >= 6:
        return
    rng = sim['rng']
    # 按洋区权重选生成区
    weights = [z[5] for z in GEN_ZONES]
    total = sum(weights)
    r = rng() * total
    acc = 0.0
    zone = GEN_ZONES[0]
    for z, w in zip(GEN_ZONES, weights):
        acc += w
        if r < acc:
            zone = z
            break
    basin, lon0, lon1, lat0, lat1, _ = zone
    for _ in range(6):
        lon = lon0 + rng() * (lon1 - lon0)
        # 生成纬度随季节摆动(向暖半球偏移)
        drift = 3.0 * math.sin(2 * math.pi * (sim['month'] - 5) / 12)
        base = (lat0 + lat1) / 2
        lat = clamp(base + drift * (1 if lat0 >= 0 else -1)
                    + (rng() - 0.5) * (lat1 - lat0), lat0 - 3, lat1 + 3)
        env = sample_env(sim, lon, lat)
        if env['land'] > 0.3:
            continue
        f1 = clamp((env['sst'] - 26.5) / 2.5, 0, 1)
        f2 = clamp((19.4 - env['shear']) / 15.6, 0, 1)
        f3 = clamp((env['rh'] - 55) / 25, 0, 1)
        a = abs(lat)
        f4 = clamp((a - 4) / 8, 0, 1) * clamp((30 - a) / 12, 0, 1)
        p = 0.30 * f1 * f2 * f3 * f4 * sim['params']['genesisRate'] * (0.4 + sim['params']['random'])
        if rng() < p:
            make_tc(sim, lon, lat, 22 + rng() * 8)
            return


# ── 云场(半拉格朗日平流 + 物理源, numpy 向量化) ──

def _sample_grid(arr, w: int, h: int, fx, fy):
    """从 2D 数组 arr(h×w) 按源网格坐标 (fx, fy) 双线性采样(数组版)。"""
    x0 = np.floor(fx).astype(np.int64)
    y0 = np.floor(fy).astype(np.int64)
    x1 = np.clip(x0, 0, w - 2)
    y1 = np.clip(y0, 0, h - 2)
    tx = np.clip(fx - x0, 0.0, 1.0)
    ty = np.clip(fy - y0, 0.0, 1.0)
    a = arr[y1, x1]
    b = arr[y1, x1 + 1]
    c = arr[y1 + 1, x1]
    d = arr[y1 + 1, x1 + 1]
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def _env_to_cld(sim, name):
    """env 场(0.5°)双线性采样到云网格(0.25°), 返回 (CLDNY, CLDNX) 数组。"""
    LATc, LONc = _cld_grids()
    fx = (LONc - C['LON0']) / C['ENVD'] - 0.5
    fy = (LATc - C['LAT0']) / C['ENVD'] - 0.5
    return _sample_grid(sim['env'][name], ENVNX, ENVNY, fx, fy)


_cld_grids_cache = None


def _cld_grids():
    global _cld_grids_cache
    if _cld_grids_cache is None:
        lats = np.linspace(C['LAT0'] + 0.5 * C['CLDD'], C['LAT1'] - 0.5 * C['CLDD'], CLDNY)
        lons = np.linspace(C['LON0'] + 0.5 * C['CLDD'], C['LON1'] - 0.5 * C['CLDD'], CLDNX)
        _cld_grids_cache = np.meshgrid(lats, lons, indexing='ij')
    return _cld_grids_cache


def cloud_step(sim, dt_h: float) -> None:
    cld = sim['cld']
    nx, ny = CLDNX, CLDNY
    d = C['CLDD']
    decay = math.exp(-dt_h / 5.5)
    sub = max(1, min(3, int(math.ceil(dt_h / 0.35))))
    dt_s = dt_h / sub
    LATc, LONc = _cld_grids()
    km_l = 111.32 * np.cos(LATc * C['DEG'])
    km_t = 110.57
    fx_env = (LONc - C['LON0']) / C['ENVD'] - 0.5
    fy_env = (LATc - C['LAT0']) / C['ENVD'] - 0.5
    fx_cld = (LONc - C['LON0']) / C['CLDD'] - 0.5
    fy_cld = (LATc - C['LAT0']) / C['CLDD'] - 0.5
    sst = _sample_grid(sim['env']['sst'], ENVNX, ENVNY, fx_env, fy_env)
    rh = _sample_grid(sim['env']['rh'], ENVNX, ENVNY, fx_env, fy_env)
    div = _sample_grid(sim['env']['div'], ENVNX, ENVNY, fx_env, fy_env)
    moist = _smoothstep_arr(25.2, 29.2, sst) * _smoothstep_arr(45, 80, rh)
    conv = np.clip(-div * 2.2e5, 0, 2.2)
    noise = sim['cldNoise'].reshape(ny, nx)
    prod = (0.028 + 0.30 * conv + 0.055 * (noise * 0.5 + 0.5)) * moist
    lo, la = LONc.copy(), LATc.copy()
    for _ in range(sub):
        u = _sample_grid(sim['env']['uMid'], ENVNX, ENVNY, fx_env, fy_env)
        v = _sample_grid(sim['env']['vMid'], ENVNX, ENVNY, fx_env, fy_env)
        # TC 涡旋贡献(中层近似 0.5 加权已在 uMid; 这里补解析涡旋以出现眼墙/雨带结构)
        for tc in sim['tcs']:
            if tc['dead']:
                continue
            dx = (tc['lon'] - LONc) * 111.32 * np.cos(LATc * C['DEG'])
            dy = (tc['lat'] - LATc) * 110.57
            r2 = dx * dx + dy * dy
            mask = r2 <= 900 * 900
            r = np.sqrt(np.maximum(r2, 1.0))
            Vv = np.zeros_like(r)
            R = tc['rmw']
            inside = r <= R
            Vv[inside] = tc['vmax'] * (r[inside] / R) ** 0.75
            outside = ~inside
            Vv[outside] = tc['vmax'] * (r[outside] / R) ** (-alpha_outer(tc['vmax']))
            phi = np.arctan2(dy, dx)
            ux = -Vv * np.sin(phi)
            vy = Vv * np.cos(phi)
            u = u + np.where(mask, ux, 0.0)
            v = v + np.where(mask, vy, 0.0)
        # kt → km/h (×1.852) 半拉格朗日位移
        lo = lo - u * 1.852 * dt_s / km_l
        la = la - v * 1.852 * dt_s / km_t
    c = _sample_grid(cld.reshape(ny, nx), nx, ny, fx_cld, fy_cld)
    c = c * decay + prod * dt_h * (1.0 - c * 0.85)
    sim['cld'] = np.clip(c, 0, 1.15).astype(np.float32).reshape(-1)
    # 冷尾流衰减(SST 15 天) + OHC 消耗恢复(45 天)
    sim['wake'] *= math.exp(-dt_h / (15 * 24))
    sim['ohcWake'] *= math.exp(-dt_h / (45 * 24))


def _smoothstep_arr(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def wind_at(sim, lon: float, lat: float, level: float):
    """单点中层/低层风(环境 + TC 涡旋, kt), 供粒子/风羽/雷达使用。"""
    env = sim['env']
    if level > 0.5:
        u = env_bilinear(env['uMid'], lon, lat)
        v = env_bilinear(env['vMid'], lon, lat)
    else:
        u = env_bilinear(env['u850'], lon, lat)
        v = env_bilinear(env['v850'], lon, lat)
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        dx = (tc['lon'] - lon) * 111.32 * math.cos(lat * C['DEG'])
        dy = (tc['lat'] - lat) * 110.57
        if dx * dx + dy * dy > 900 * 900:
            continue
        ux, vy = vortex_wind(tc, lon, lat, level)
        u += ux
        v += vy
    return u, v


def cld_sample(sim, lon: float, lat: float) -> float:
    fx = (lon - C['LON0']) / C['CLDD'] - 0.5
    fy = (lat - C['LAT0']) / C['CLDD'] - 0.5
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = clamp(x0, 0, CLDNX - 2)
    y1 = clamp(y0, 0, CLDNY - 2)
    tx = clamp(fx - x0, 0, 1)
    ty = clamp(fy - y0, 0, 1)
    a = sim['cld'][y1 * CLDNX + x1]
    b = sim['cld'][y1 * CLDNX + x1 + 1]
    c = sim['cld'][(y1 + 1) * CLDNX + x1]
    d = sim['cld'][(y1 + 1) * CLDNX + x1 + 1]
    return float((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)


# ── 集合预报(蒙特卡洛扰动, 每 6h 滚动 72h) ──

def run_ensemble(sim) -> dict:
    if sim['ensemble'] and sim['t'] - sim['ensemble']['hour'] < 5.9:
        return sim['ensemble']
    n_mem, hours, dt_e = 12, 72, 6
    members = []
    prim = next((t for t in sim['tcs'] if not t['dead']), None)
    if prim is None:
        sim['ensemble'] = {'hour': sim['t'], 'members': []}
        return sim['ensemble']
    g = make_gauss(make_rng(sim['seed'] * 31 + int(sim['t'] * 7)))
    for m in range(n_mem):
        pert = {'su': g() * 2.5, 'sv': g() * 2.5, 'beta': 1 + g() * 0.3,
                'mpi': 1 + g() * 0.08, 'sh': g() * 3.9}
        tc = {'lon': prim['lon'], 'lat': prim['lat'], 'vmax': prim['vmax'],
              'pmin': prim['pmin'], 'rmw': prim['rmw'], 'b': prim['b'],
              'mu': prim['mu'], 'mv': prim['mv'],
              'noiseU': 0.0, 'noiseV': 0.0, 'erc': None, 'ri': None,
              'age': prim['age']}
        path = {}
        for h in range(0, hours + 1, dt_e):
            path[h] = [tc['lon'], tc['lat'], tc['vmax']]
            lat = tc['lat']
            env = sim['env']
            u8 = env_bilinear(env['u850'], tc['lon'], lat)
            v8 = env_bilinear(env['v850'], tc['lon'], lat)
            u2 = env_bilinear(env['u200'], tc['lon'], lat)
            v2 = env_bilinear(env['v200'], tc['lon'], lat)
            steer_u = 0.75 * u8 + 0.25 * u2
            steer_v = 0.75 * v8 + 0.25 * v2
            beta = beta_drift(lat)
            u = steer_u + pert['su'] + beta[0] * pert['beta'] + tc['noiseU']
            v = steer_v + pert['sv'] + beta[1] * pert['beta'] + tc['noiseV']
            tc['noiseU'] += (g() * 2.3 - tc['noiseU']) * 0.15
            tc['noiseV'] += (g() * 2.3 - tc['noiseV']) * 0.15
            tc['lon'] += u * 6.0 / (60.0 * math.cos(lat * C['DEG']))
            tc['lat'] += v * 6.0 / 60.0
            e = sample_env(sim, tc['lon'], tc['lat'])
            vpi = mpi(e['sst'], e['ohc'], sim['params']['outflow'], e['lat']) * pert['mpi']
            shear = e['shear'] + pert['sh']
            fs = math.exp(-(max(0.0, shear - 5.8) / 21.4) ** 2)
            fd = math.exp(-((70 - e['rh']) / 42.0) ** 1.4) if e['rh'] < 70 else 1.0
            d_v = (vpi - tc['vmax']) / (38 if tc['vmax'] < vpi else 20) * fs * fd
            if e['land'] > 0.02:
                d_v -= tc['vmax'] * min(1.0, e['land']) * 0.075
            if e['sst'] < 26.5:
                d_v -= tc['vmax'] * (26.5 - e['sst']) * 0.04
            tc['vmax'] = clamp(tc['vmax'] + d_v * dt_e, 10, 185)
            tc['pmin'] = pmin_from_v(tc['vmax'])
        members.append(path)
    sim['ensemble'] = {'hour': sim['t'], 'members': members, 'nMem': n_mem,
                       'hours': hours}
    return sim['ensemble']


# ── 卫星云顶叠加(TC 眼墙/螺旋雨带/眼, 解析) ──

def tc_cloud_top(sim, tc, lon: float, lat: float) -> float:
    dx = (lon - tc['lon']) * 111.32 * math.cos(lat * C['DEG'])
    dy = (lat - tc['lat']) * 110.57
    r = math.hypot(dx, dy)
    if r > 700:
        return 0.0
    R = tc['rmw']
    v = tc['vmax']          # kt
    H = 0.0
    ew = math.exp(-((r - R) / (0.42 * R + 4)) ** 2)
    H += 7.5 * ew * min(1.0, v / 78.0)
    if tc['erc']:
        er = R * (1 + 0.55 * math.sin(math.pi * min(1.0, tc['erc']['t'] / tc['erc']['T'])))
        H += 4.5 * math.exp(-((r - er) / (0.35 * er + 5)) ** 2) * min(1.0, v / 107.0)
    if R * 1.2 < r < R * 9:
        phi = math.atan2(dy, dx)
        sh = env_bilinear(sim['env']['shear'], tc['lon'], tc['lat']) + 5.8
        sh_dir = math.atan2(-0.28, -0.9)
        band0 = phi + 2.4 * math.log10(r / R) + (math.pi / 2) * math.sin(sim['t'] / 5)
        band = math.cos(band0 * 4) * 0.5 + 0.5
        spiral = math.pow(band, 3) * math.exp(-((r / R - 4) / 3.2) ** 2)
        asym = 1 + 0.5 * math.cos(phi - (sh_dir + math.pi))
        H += 5.2 * spiral * asym * min(1.0, v / 87.0) * smoothstep(0, 23.3, sh)
    if r < R * 0.45:
        H = -100.0
    return H


# ── 雷达反射率(dBZ) ──

def radar_refl(sim, tc, r: float, theta: float, h: float, cld_sample_v: float) -> float:
    dbz = -10.0
    if tc is not None:
        R = tc['rmw']
        v = tc['vmax']          # kt
        ew = math.exp(-((r - R) / (0.38 * R + 3)) ** 2)
        dbz = max(dbz, (41 + 0.082 * v) * ew)
        if tc['erc']:
            er = R * (1 + 0.55 * math.sin(math.pi * min(1.0, tc['erc']['t'] / tc['erc']['T'])))
            dbz = max(dbz, (36 + 0.062 * v) * math.exp(-((r - er) / (0.32 * er + 4)) ** 2))
        if r > R * 0.7:
            band0 = theta + 2.4 * math.log10(max(r, 1) / R)
            band = math.pow(math.cos(band0 * 3.2) * 0.5 + 0.5, 2.2)
            spiral = band * math.exp(-((r / R - 3.2) / 2.6) ** 2)
            dbz = max(dbz, (30 + 0.072 * v) * spiral + 4)
    dbz = max(dbz, -2 + 24 * math.pow(cld_sample_v, 2.2))
    if 3.4 < h < 5.4 and dbz > 5:
        dbz += 4.5
    if h > 13:
        dbz = -10.0
    return dbz


# ── 主积分步 ──

def sim_step(sim, dt: float) -> None:
    sim['t'] += dt
    # 季节推进: 月份随模拟时间变化(30.4 天/月), SST/切变/季风槽随之变化
    sim['month'] = ((sim['month0'] - 1 + int(sim['t'] / (24.0 * 30.4))) % 12) + 1
    # 全球网格下环境场重建成本高: 每 2 模拟小时刷新(10min 步下足够平滑)
    if sim['t'] - sim['lastEnvH'] >= 2:
        env_update(sim)
    if sim['t'] - sim['lastEnsH'] >= 6:
        run_ensemble(sim)
        sim['lastEnsH'] = sim['t']
    if sim['t'] - sim['lastGenH'] >= 6:
        gen_check(sim)
        sim['lastGenH'] = sim['t']
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        intensity_step(sim, tc, dt)
        if not tc['dead']:
            motion_step(sim, tc, dt)
        if sim['t'] - tc.get('lastHistT', -1) >= 1:
            tc['lastHistT'] = sim['t']
            e = tc.get('env') or sample_env(sim, tc['lon'], tc['lat'])
            tc['hist'].append({
                't': sim['t'], 'lon': tc['lon'], 'lat': tc['lat'],
                'vmax': tc['vmax'], 'pmin': tc['pmin'],
                'sst': e['sst'], 'shear': e['shear'], 'ohc': e['ohc'],
                'rh': e['rh'],
                'vpi': mpi(e['sst'], e['ohc'], sim['params']['outflow'], e['lat'])})
            tc['track'].append({'t': sim['t'], 'lon': tc['lon'], 'lat': tc['lat'],
                                'vmax': tc['vmax'], 'pmin': tc['pmin']})
            if len(tc['hist']) > 720:
                tc['hist'] = tc['hist'][-720:]
            if len(tc['track']) > 2400:
                tc['track'] = tc['track'][-2400:]
    eddy_step(sim, dt)
    if any(t['dead'] for t in sim['tcs']):
        sim['tcs'] = [t for t in sim['tcs'] if not t['dead']]
