# simulator.simcore/core.py
"""SimCore 台风数值模拟器核心。

> 历史备注: 本项目**最初**打算逐行移植参考实现(typhoon-sim.html), 实测效果不好,
> 后来**重写**成现在这套; 原 docstring 里"从参考实现逐行移植"那句一直没删, 已于
> 轮次 157 删除 —— 现在的代码是独立实现, 不要拿它当"移植产物"去对照那份 HTML。
> (那份参考 HTML 本身不在本仓库内。)

科学依据:
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
import logging
import math
import time as _time
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

from simulator.simcore import phase as _PH
# 轮次 158: 副高形状参数化(脊线倾角 / 西端陡峭 / 东西南北四向伸展)。
# 纯函数、只依赖 numpy、不反向 import 本模块 ⇒ 无循环依赖。
# **默认参数下与原来的内联算式逐位一致**(见 ridge.py 的不变量 1)。
from simulator.simcore import ridge as _RG
# 轮次 158: 西风槽 + 冷空气准周期爆发。纯函数 + 整数哈希取随机量(不占 numpy RNG 流),
# **默认 wtOn/csOn 全关时是纯 no-op**(一个数组运算都不做、env 里的数组连对象都不换)。
from simulator.simcore import synoptic as _SY
# 轮次 158: 地形山脉高度(台湾中央山脉 / 菲律宾吕宋 / 日本本州脊)。
# 数据来自 map/heightmap.png 的真 DEM(见 tools/build_terrain_dem.py),
# **消费点全部由 `params['terrainOn']` 门控, 默认关 ⇒ 不改变现有结果**;
# 模块缺 npz 时 `available()` 为 False, 调用点自动跳过。
from simulator.simcore import terrain as _TG

logger = logging.getLogger(__name__)

C = {
    # **全球计算域 = ±90°**(用户 2026-09-21: "0.必须扩域!立刻!纬度判死不能有!极区必须参与模拟!
    # 我还要模拟 EX 的!")。原来是 ±60 —— 那是"只算 TC 相关海域"的取法, 后果是
    # ① 极区根本不参与模拟 ② 到 ±59.6° 的台风被硬判死(温带变性阶段被整段截掉)。
    # 基态场的数据源本来就是全球的(OISST 至 ±89.9、NCEP/NCAR 2.5° 至 ±90、
    # map/heightmap.png 至 ±90), 所以扩域不需要新下载, 只是重采样目标变大。
    'LON0': 0.0, 'LON1': 360.0, 'LAT0': -90.0, 'LAT1': 90.0,
    # F2: 环境场网格 0.5°(用户要求"SST 什么的 0.5°")。1° 分辨不出湾流/黑潮
    # (宽约 100km), 也分辨不出暖水核; 渲染侧自适应降采样, 屏幕像素成本不变。
    'ENVD': 0.5, 'CLDD': 1.0,                                   # 环境/云网格分辨率(°)
    'DT': 10.0 / 60.0,                                          # 积分步长(小时, 10分钟)
    'R_E': 6371.0, 'OMEGA': 7.292e-5, 'RHO': 1.15,
    'CP': 1005.0, 'LV': 2.5e6,
    'DEG': math.pi / 180.0,
}
ENVNX = int(round((C['LON1'] - C['LON0']) / C['ENVD']))     # 720
ENVNY = int(round((C['LAT1'] - C['LAT0']) / C['ENVD']))     # 360
CLDNX = int(round((C['LON1'] - C['LON0']) / C['CLDD']))     # 360
CLDNY = int(round((C['LAT1'] - C['LAT0']) / C['CLDD']))     # 180
#: 域边界的**模块级别名** —— 有些函数体里有局部变量叫 (如 gen_check 的生成表),
#: 直接用名字  会被遮蔽成 KeyError(扩域时踩过)。
DOM_LAT0, DOM_LAT1 = C['LAT0'], C['LAT1']
DOM_LON0, DOM_LON1 = C['LON0'], C['LON1']

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

def rmw_eq(v_kt: float) -> float:
    """给定强度下的**平衡**最大风速半径(nm)。

    **T5 标定(2026-09-17)**: 用官评的 [19] 字段(RMW)实测 25 万条记录,
    分强度组的中位数:

        vmax   20-34  34-45  45-55  55-65  65-80  80-100  100-140  140-200
        RMW     45     40     30     25     20     15       15       12

    拟合  在以上各点的误差都 ≤2nm。
    原来的  **整体偏大 1.3~3 倍**
    (v=100 给 42nm, 观测只有 15nm)—— 这会让风场过度铺开、
    radius_at_wind 的 R34 也跟着错。

    注意这只是**平衡值**: 观测里同强度下的 RMW 有真实离散
    (45-55kt 时 P25~P75 = 25~45nm, 宽 20nm), 所以 RMW 必须是**预报量**
    而不是 vmax 的纯函数 —— 见 intensity_step 里的 d(RMW)/dt。
    """
    # 2026-09-21: 地板 8 -> 2.5 nm。**中位拟合不动**(它用官评 25 万条标定过, 见上表),
    # 只放开极值端 —— 用户实例: 杜鹃主巅 RMW 仅 **2.5 海里**, 而 ERC 时间尺度按
    # T ∝ RMW^1.7 反解(2.5nm ⇒ 45 分钟), 所以极小眼必须能被表达, 否则那条律永远用不上。
    _base = 12.0 + 33.0 * math.exp(-(v_kt - 30.0) / 35.0)
    # **极强端额外收紧(2026-09-21, 目标 ④)**:
    # 原式中位拟合在 v=150 时给 **13.1nm**, 但目标 ④ 要求 ≥150kt 给 **3-5nm**
    # (依据: 杜鹃主巅 RMW 仅 2.5 海里; 且官评 RMW 字段在 ≥130kt 常记为常数 5,
    #  即强端本来就落在个位数 nm)。中位段(v<120)不动 —— 它由官评 25 万条标定过。
    if v_kt > 120.0:
        _base -= 9.0 * clamp((v_kt - 120.0) / 30.0, 0.0, 1.0)
    return clamp(_base, 2.5, 90.0)


def rmw_from_v(v_kt: float) -> float:
    """诊断用别名(历史调用点很多, 保留)。新代码请用 rmw_eq。"""
    return rmw_eq(v_kt)


def eta_rmw(rmw_nm: float) -> float:
    """外核惯性稳定度屏障: RMW 偏大时上限被压低。

    帖子的核心结论 —— 小 RMW 时低层入流能穿透到 RMW 以内, 非绝热加热集中在
    高效区, 对流破顶把平流层下层的高位温空气泵进上层暖心; 大 RMW 时入流被迫在
    RMW 处抬升, 加热分散、上限低。这解释了"季风低压/大槽出身的系统强度通常不高"。

    **形状要点: 只在 RMW 偏大时生效, RMW<=22nm 时 η=1**。因为模拟的强度尾部
    本来就偏低(P95 108 vs 观测 140), 若对强台风也打折会雪上加霜 ——
    物理上强台风的 RMW 本来就在 10-20nm 区间, 不该再受这个屏障限制。

        RMW  12   22   30   45   60+
        η   1.00 1.00 0.92 0.77 0.65
    """
    _r = max(2.0, float(rmw_nm))     # 目标 ④: 下限 6 -> 2nm(小眼必须能被表达)
    return clamp(1.0 - 0.30 * max(0.0, _r - 22.0) / 30.0, 0.65, 1.0)


def alpha_outer(v_kt: float) -> float:
    """Holland 廓线的外圈衰减指数 —— 控制 R34 随风速如何增长。

    **T5 标定**(2026-09-17): 用官评的 R34 四象限最大对标, 观测的 R34 随强度是
    **饱和**的(75nm@40kt → 150nm@185kt, 只涨 2 倍), 而原式 0.55→0.61 几乎不变,
    导致模型 R34 无界增长(59→339nm, 涨 5.7 倍)。强台风是**紧凑**的。
    由两个锚点解出 α(50kt)=0.729、α(185kt)=0.891 → 线性式如下。
    """
    return 0.70 + 0.0012 * min(max(v_kt - 40.0, 0.0), 145.0)


def pmin_from_v(v_kt: float) -> float:
    return 1010.0 - 0.59 * math.pow(v_kt * 0.514444, 1.13)


def holland_b(pmin: float) -> float:
    return clamp(1.5 + (980.0 - pmin) / 120.0, 1.0, 2.5)


def tangent_v(tc, r: float) -> float:
    """切向风(kt): 内核刚体旋转, 外区幂律衰减。"""
    # ⚠ 审查 R11-3(2026-10-15): 与数组版  的
    #    对齐 —— 标量版原来直接用 tc['rmw']，
    #   rmw 被写成 0（数据脏 / 未初始化）时 r/R 直接 ZeroDivisionError。
    R = max(1e-6, float(tc['rmw']))
    if r <= 0:
        return 0.0
    if r <= R:
        return tc['vmax'] * (r / R) ** 0.75
    return tc['vmax'] * (r / R) ** (-alpha_outer(tc['vmax']))


def radius_at_wind(tc, v0_kt: float) -> float:
    if v0_kt >= tc['vmax']:
        return 0.0
    # ⚠ 审查 R11-4(2026-10-15): v0_kt == 0 时 vmax/v0 除零（旧代码只在
    #   v0 >= vmax 时早退）。0kt 等值线没有有限半径 ⇒ 返回 inf 表示"无穷远"，
    #   调用方  用它算 rkm，inf 会被后续 clamp/比较自然吸收。
    if v0_kt <= 0:
        return float('inf')
    return max(1e-6, float(tc['rmw'])) * (tc['vmax'] / v0_kt) ** (
        1.0 / max(1e-6, alpha_outer(tc['vmax'])))


def tc_deficit(tc, r: float) -> float:
    """气压亏损 (hPa) at r (km): Holland 廓线 p(r)=pc+(pn-pc)·exp(-(R/r)^B),
    亏损 pn-p(r)=(pn-pc)·(1-exp(-(R/r)^B)), 远处趋于 0(物理正确)。"""
    if r < 2:
        r = 2
    return (1010.0 - tc['pmin']) * (1.0 - math.exp(-(tc['rmw'] / r) ** tc['b']))


def vortex_wind(tc, lon, lat, level: float, env=None):
    """TC 涡旋风 [u, v] (kt, 东/北)。level: 0=低层(入流), 1=高层(微辐散)。

    F9: 风场合成 = **对称涡旋 + 移速不对称 + 环境气压梯度风**。
    后两项原来都没有: 台风不是孤立涡旋, 它嵌在大尺度气压场里 —— 远离中心处
    环境风占主导(引导气流), 近中心处涡旋占主导。缺了环境项, 危险半圆/安全
    半圆的风速差就只有移速那一份, 冷空气参与的个例完全体现不出来。
    """
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
    # F9: **环境气压梯度风**。低层用真实 850hPa 环境风(u850/v850), 高层用 200hPa。
    # 过渡: 近中心 w→0(涡旋主导), 远处 w→1(环境主导), 尺度取 ~500km。
    if env is not None:
        # 注意: 环境气压梯度风**不在这里加** —— 调用点 (wind_at) 已经用
        # env_bilinear(env['u850']) 加过一次, 在这里再加会重复计入。
        # 这里只补**冷高压**的额外梯度: 冷空气入侵时冷高与台风之间的气压梯度
        # 极强, 危险半圆(北半球前进方向右侧)被进一步加速。
        # Megi(2010) 实测: 危险半圆 FL192 vs 安全半圆 FL154(常态差 ~25kt),
        # 冷空气参与时可达 35kt 以上 —— 那多出来的 10kt 就来自这一项。
        cs = float(env.get('coldSurge', 0.0) or 0.0)
        if cs > 0.0 and sp > 1.0:
            w = 1.0 - math.exp(-(r / 500.0) ** 2)
            # 危险半圆 = 北半球前进方向**右侧**。侧向单位矢量是 (mv, -mu)/sp
            # (例: 向东移动时右=南 = (0,-1))。
            # 原来写成 (-mv, mu)/sp 是**左侧**, 符号反了 —— 实测危险半圆反而
            # 比安全半圆弱 1.8 kt。
            side = (d_lon * tc['mv'] - d_lat * tc['mu']) / max(1.0, sp * r)
            # 增强必须**沿现有切向**(把风加速), 不能按固定的横向矢量叠加 ——
            # 后者与切向垂直, 只会把风"转"一下, 风速几乎不变(实测冷高压从
            # 0 加到 1.0, 危险/安全差只从 1.7 变到 1.7 kt, 完全没效果)。
            _spd = math.hypot(u, v)
            if _spd > 1.0:
                _add = 18.0 * cs * side * w
                u += (u / _spd) * _add
                v += (v / _spd) * _add
    return u, v


# ── MPI(最大潜在强度, Emanuel 气候学拟合, 输出 kt) ──

def mpi(sst: float, ohc_d26: float, outflow: float, lat: float,
        t200: float = None) -> float:
    """最大潜在强度(kt)。

    t200: 200hPa 出流层温度(K)。给了就按 Emanuel 的热力学因子
    **V² ∝ (Ts − To)/To** 修正: 对流层顶越高/出流层越冷 → 上下温差越大 →
    同样 SST 下 MPI 越强(用户 2026-09 提的"对流层顶影响")。
    参考态取 Ts=300K / To=210K; To 降 5K 约 +4% 风速, 与理论一致。

    ⚠⚠ **不要把"生成支持度"混进本函数**（Lead 2026-10-05）。本函数是
    **物理最大潜在强度**（Emanuel），**24°C 处仍有约 10.5 kt**（非 0，物理上本该如此）。
    沙盒的 `SEA_POTENTIAL`（`(24,0.00)…(31,1.00)`）是**归一化支持度**、24°C **定义为 0**，
    当 `sea_supported = 80·warmth·heat·org^0.75` 的**乘子**用 —— **两类量不该逐点相等**
    （实测归一化后在 24–27°C 差 19%–57%，**是概念不同，不是 bug**；见
    output/exploration/s12_sandbox_port.md ③-B）。
    ⇒ 将来要"24°C 以下不支持生成"，**做成独立的生成阈值/支持度乘子**，别改这里。
    """
    d = max(0.0, min(5.0, sst - 26.0))
    v = 70.0 + 26.73 * d - 0.729 * d * d
    if sst < 26.5:
        v *= max(0.15, (sst - 24.5) / 2.0)
    # F5: 原写法 clamp(d26/75, .15, 1.4) 在 d26 >= 105 m 全部触顶 —— 真实暖池
    # D26 常年 110-140 m, 于是 OHC 对 MPI 完全没有区分度(上一轮实测: 数据模式
    # 下 OHC 项恒为常数)。改成连续饱和型, 0-150 m 全程单调可分辨。
    ohc_term = 0.875 + 0.25 / (1.0 + math.exp(-(max(0.0, ohc_d26) - 55.0) / 28.0))
    v *= ohc_term
    v *= 0.92 + 0.08 * clamp(outflow, 0, 1)
    if t200 is not None:
        # NCEP 的 air 是**摄氏度**(实测 200hPa ≈ -51°C = 222 K), 这里统一到 K
        To = t200 + 273.15 if t200 < 150.0 else t200
        Ts = sst + 273.15
        frac = max(0.05, (Ts - To) / max(1.0, To))
        # 参考态必须取**热带实际平均**(Ts≈300K / To≈222K = -51℃), 不能用
        # 理想化的 210K —— 后者比真实热带冷 12K, 会把全球 MPI 系统性压低 8%
        # (实测 15N/140E 从 133 掉到 122)。改成相对调制后, 出流层每冷 5K 约 +4%,
        # 与 Emanuel 的 V²∝(Ts−To)/To 一致。
        frac_ref = (300.0 - T200_REF) / T200_REF
        v *= math.sqrt(max(0.2, min(2.5, frac / frac_ref)))
    # 纬度项取 |sin|: 南半球 sin 为负会把 MPI 压到近零(而生成区含南半球)
    v *= 0.72 + 0.28 * min(1.0, abs(math.sin(lat * C['DEG'])) / math.sin(18 * C['DEG']))
    return clamp(v, 0, 195)


# ── 气候学场(全球) ──
# 解析气候场的唯一公式源: sst_clim 用 numpy 逐元素实现, 标量与网格(LAT/LON 数组)通用;
# env_update 直接调用它做矢量建场, 点采样/测试同走此函数(避免双份拷贝漂移)。
# 切变/湿度等仅存在于 env_update 分支, 未在此保留并行副本。

_sst_clim_cache: dict = {}


def sst_clim_cached(lat, lon, month):
    """sst_clim 的月缓存: 只依赖 (网格, month), 而 month 每 30.4 模拟天才变一次,
    但 env_update 每 2 模拟小时就调一次(实测 11ms/次)。

    **2026-09-18 修:原实现用 ** —— 正是
     注释里警告过的坑: 调用方若传新建的临时数组, 临时数组被回收后
    id 会被复用, 于是命中**别人**的缓存。实测后果是"同一个模拟在同一个进程里
    跑两次结果不同"(wake 在 1250.843750 / 1250.843872 / 1250.791870 之间漂),
    而两个独立进程反而一致(因为每个进程只跑一次, 还没触发 id 复用)。

    改成**身份判断**: 只缓存  返回的规范网格(同一对象),
    其它输入直接算不缓存。既没有 id 复用风险, 也保留了对规范网格的加速。
    """
    g = _env_grids()
    if lat is not g[0] or lon is not g[1]:
        return sst_clim(lat, lon, month)
    key = int(month)
    v = _sst_clim_cache.get(key)
    if v is None:
        v = sst_clim(lat, lon, month)
        if len(_sst_clim_cache) > 4:
            _sst_clim_cache.clear()
        _sst_clim_cache[key] = v
    return v


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
    season = np.where(lat >= 0, month, (month + 5) % 12 + 1)
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
#: 由 tools/make_cell_land_mask.py 从 heightmap 栅格统计出来的格点陆地占比缓存
#: (每格 15×15 像素求均值, 比多边形 3×3 子采样精细得多), 附带"100% 陆地"格点掩码。
#: 用户 2026-09-19: "检查一次后存起来方便复用" + "陆地占比达到 100% 就不再模拟这个格子"。
_CELL_LAND_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'env', 'model', 'land_cell.npz')
_CELL_LAND = {'tried': False, 'frac': None, 'all_land': None}


def cell_land_cache():
    """读 land_cell.npz → (landfrac, all_land); 不存在则 (None, None)。"""
    if not _CELL_LAND['tried']:
        _CELL_LAND['tried'] = True
        try:
            with np.load(_CELL_LAND_FILE) as z:
                f = np.asarray(z['landfrac'], dtype=np.float32)
                a = np.asarray(z['all_land'])
                if f.shape == (ENVNY, ENVNX):
                    _CELL_LAND['frac'] = f
                    _CELL_LAND['all_land'] = a
        except Exception:
            logger.debug('land_cell.npz 读取失败(回退多边形掩码)', exc_info=True)
    return _CELL_LAND['frac'], _CELL_LAND['all_land']


def all_land_mask():
    """100% 陆地的格点掩码(无缓存则 None) —— 这些格点不再做海洋模拟/不再画。"""
    return cell_land_cache()[1]


def build_land() -> np.ndarray:
    """0.5° 陆地比例场(3×3 子采样)。结果持久化到 land_0.5.npz(首次 ~28s, 之后 <0.1s)。

    **经度约定必须两边都试**: ne_110m 的多边形经度是 -180..180, 而网格是 0..360。
    原来只拿网格经度去比 bbox, 于是  对西经永远不成立 ——
    **整个西半球(美洲、大西洋西岸)一个陆地格都没有**。实测 30N 纬圈只有 0-121.5E
    是陆地。后果很严重: 模拟的西太/印度洋风暴会登陆亚洲而死, 东太/大西洋风暴却
    永远不会登陆, 于是那两个洋区的寿命反而比观测长(267/308h vs 192/156h)。
    """
    global _LAND_POLYS, _LAND_CACHE
    if _LAND_CACHE is not None:
        return _LAND_CACHE
    # **优先用栅格统计缓存**(land_cell.npz): 15×15 像素/格, 比多边形 3×3 子采样准,
    # 而且一次生成后零成本复用(用户 2026-09-19)
    _cf, _ca = cell_land_cache()
    if _cf is not None:
        _LAND_CACHE = _cf
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
                    # 两个候选经度: 网格值(0..360) 与转成 -180..180 的值
                    los = (lo, lo - 360.0)
                    hit = False
                    for lt in los:
                        cand = np.nonzero(
                            (bboxes[:, 0] <= lt) & (bboxes[:, 1] >= lt)
                            & (bboxes[:, 2] <= la) & (bboxes[:, 3] >= la))[0]
                        for k in cand:
                            if _point_in_poly(lt, la, polys[k]):
                                hit = True
                                break
                        if hit:
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
    """arr: (ENVNY, ENVNX) 2D 场, 双线性采样。

    **经度必须先回绕到 [LON0, LON0+360)**: 观测资料(ATCF)里西经是负数,
    不回绕时 fx = (-50-0)/0.5-0.5 = -100.5, x0 取 floor 得 -101, 再被 clamp
    成 0 —— 于是大西洋/东太的所有采样都落到**格林尼治子午线**上去了。
    实测中点检查: 大西洋中部 30N/50W 与太平洋中部 20N/150W 都返回 land=1.00。
    模拟内部台风经度本来就在 0-360, 所以这个修复对模拟本身是中性的。
    """
    lon = (lon - C['LON0']) % 360.0 + C['LON0']
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


def env_bilinear_arr(arr: np.ndarray, lon, lat) -> np.ndarray:
    """env_bilinear 的数组版(粒子/风羽批量采样), 逐点与标量版等价。"""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    lon = (lon - C['LON0']) % 360.0 + C['LON0']      # 同上: 负经度必须回绕
    fx = (lon - C['LON0']) / C['ENVD'] - 0.5
    fy = (lat - C['LAT0']) / C['ENVD'] - 0.5
    x0 = np.floor(fx)
    y0 = np.floor(fy)
    x1 = np.clip(x0, 0, ENVNX - 2).astype(np.intp)
    y1 = np.clip(y0, 0, ENVNY - 2).astype(np.intp)
    tx = np.clip(fx - x0, 0.0, 1.0)
    ty = np.clip(fy - y0, 0.0, 1.0)
    a = arr[y1, x1]
    b = arr[y1, x1 + 1]
    c = arr[y1 + 1, x1]
    d = arr[y1 + 1, x1 + 1]
    return (a * (1.0 - tx) + b * tx) * (1.0 - ty) + (c * (1.0 - tx) + d * tx) * ty


def land_at_arr(sim, lon, lat) -> np.ndarray:
    """land_at 的数组版。"""
    return env_bilinear_arr(sim['land'], lon, lat)


def tangent_v_arr(tc, r: np.ndarray) -> np.ndarray:
    """tangent_v 的数组版(内核刚体旋转, 外区幂律衰减)。"""
    R = max(1e-6, float(tc['rmw']))
    rr = np.maximum(np.asarray(r, dtype=np.float64), 1e-6)
    out = tc['vmax'] * np.power(rr / R, 0.75)
    outer = rr > R
    if outer.any():
        out = np.where(outer,
                       tc['vmax'] * np.power(rr / R, -alpha_outer(tc['vmax'])),
                       out)
    return out


def vortex_wind_arr(tc, lon, lat, level: float):
    """vortex_wind 的数组版(含入流角与移动不对称), 返回 (u, v)。"""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    d_lon = (lon - tc['lon']) * 111.32 * np.cos(lat * C['DEG'])
    d_lat = (lat - tc['lat']) * 110.57
    r = np.hypot(d_lon, d_lat)
    V = tangent_v_arr(tc, r)
    phi = np.arctan2(d_lat, d_lon)
    inflow = -0.08 if level > 0.5 else 0.32
    c = math.cos(inflow)
    s = math.sin(inflow) * (-1.0 if level > 0.5 else 1.0)
    u_tan = -V * np.sin(phi)
    v_tan = V * np.cos(phi)
    u = u_tan * c - v_tan * s
    v = u_tan * s + v_tan * c
    sp = math.hypot(tc['mu'], tc['mv'])
    if sp > 1:
        f = 0.12 * sp * np.exp(-r / 400.0)
        u = u + (tc['mu'] / sp) * f
        v = v + (tc['mv'] / sp) * f
    near = r < 1.0
    if near.any():
        u = np.where(near, 0.0, u)
        v = np.where(near, 0.0, v)
    return u, v


# ── 模拟状态 ──

#: 环境场重建步长允许档位(小时)。模拟"精度"由此决定: 1/3/6/12/24h
#: 环境步长候选。**上限锁 6h**(用户 2026-09-17 定): 再大就会让路径曲率
#: 被一步直线抹平, 而 10 分钟那种细分步长并不带来登陆精度(登陆改用几何求交)。
ENV_STEPS = (1.0, 3.0, 6.0)

# ── 模拟精度档位(用户 2026-09-18 定) ──────────────────────────────
# 档位用"**每 6h 模拟时间的墙钟成本**"定义, 因为用户关心的就是"这一步跑多久"。
# 硬约束: 上限 1 s, 实际上限 800 ms; 超过就只能靠放慢模拟推进来达成
# (类似宇宙沙盘的"已达到当前模拟时间步长上限")。
#
# 三个内部旋钮:
#   inner    —— **把 6h 再细分几步**(每步都重建环境场)。这才是真正的"更高精度":
#               环境场在 6h 内是会变的, 细分后台风看到的是随时间演化的场,
#               而不是"一步之内冻结"。代价线性增长。
#   hold     —— 反过来, 低档位**减少**环境场重建频率(每 hold 步才重建一次),
#               中间的步只从已存的基态 + 尾流重算 SST/OHC(几十毫秒 → 几毫秒)。
#               大尺度场(风/切变/模态)变化慢, 这样近似是合理的。
#   quality  —— 1 = 含中尺度噪声/涡旋 + 低通(改善纹理与统计); 0 = 跳过。
#
# **参数是按本机实测标到目标上的**(env_update ≈ 230 ms):
#   inner 档: 每 6h ≈ inner × env_update
#   hold  档: 每 6h ≈ (env_update + (hold-1) × 补丁) / hold
# 机器不同→实测值会漂, 所以 UI 会**实时显示实测 ms/6h**(sim['stepCostMs']),
# 档位名只是"目标", 不是承诺。硬上限 1 s、实际上限 800 ms 由 UI 提示。
PRECISION_TIERS = (
    # 名称,   目标ms, inner, hold, quality
    ('50ms',   50,    1,    16,  0),
    ('100ms',  100,   1,    3,   0),
    ('400ms',  400,   1,    1,   1),
    ('800ms',  800,   4,    1,   1),
    ('2s',     2000,  12,   1,   1),
    ('5s',     5000,  31,   1,   1),
)
DEFAULT_PRECISION = '400ms'
PRECISION_CAP_MS = 800.0      # 实际上限: 超过它 UI 就提示"已达当前时间步长上限"
PRECISION_HARD_MS = 1000.0    # 硬上限


def precision_spec(name: str):
    """档位名 → (目标ms, inner, hold, quality)。未知档位回落到默认。"""
    for nm, ms, inner, hold, q in PRECISION_TIERS:
        if nm == str(name):
            return float(ms), int(inner), int(hold), int(q)
    for nm, ms, inner, hold, q in PRECISION_TIERS:
        if nm == DEFAULT_PRECISION:
            return float(ms), int(inner), int(hold), int(q)
    return 400.0, 1, 1, 1
DEFAULT_ENV_STEP = 6.0

#: 模拟可覆盖的年份范围(环境场外推对任意年份都成立,这里只是给出合理边界)
YEAR_MIN, YEAR_MAX = 1700, 2300


def parse_start(v) -> datetime:
    """把 'YYYY-MM-DD[ HH[:MM]]' / datetime / None 统一成 datetime。"""
    if v is None:
        return datetime(2000, 8, 1, 0)
    if isinstance(v, datetime):
        return v.replace(minute=0, second=0, microsecond=0)
    s = str(v).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H', '%Y-%m-%d',
                '%Y%m%d%H', '%Y%m%d'):
        try:
            return datetime.strptime(s, fmt).replace(minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    raise ValueError('无法解析模拟起始时间: %r' % (v,))


def snap_env_step(h) -> float:
    """把任意正值吸附到最近的允许档位(ENV_STEPS: 1/3/6h；2026-09-17 起上限 6h)。"""
    try:
        h = float(h)
    except (TypeError, ValueError):
        return DEFAULT_ENV_STEP
    if h <= 0:
        return DEFAULT_ENV_STEP
    return min(ENV_STEPS, key=lambda s: abs(s - h))


def clock(sim) -> datetime:
    """模拟当前时刻 = 起始时刻 + t 小时。"""
    return sim['start'] + timedelta(hours=float(sim['t']))


def doy(sim) -> float:
    """当前时刻的儒略日序号(1.0 = 1月1日00z),用于基态气候场的年内插值。"""
    c = clock(sim)
    return c.timetuple().tm_yday + (c.hour + c.minute / 60.0) / 24.0


def doy_of(dt: datetime) -> float:
    return dt.timetuple().tm_yday + (dt.hour + dt.minute / 60.0) / 24.0


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
        'dataMode': False,        # True = 气候库/模态模拟器, False = 解析公式
        'dataYear': None,         # 保留: 具体年份库(旧接口)
        'dataClim': True,         # True=用气候态(_clim), False=用具体年份库(_library)
        'dataDayCycle': 1.0,      # 日内太阳周期幅度(0=关)
        # F3: 模态锚定强度(0=纯气候态, 1=完全按真实模态)
        'modeAmp': 1.0,
        # F4: 增暖情景倍率(1=按内置曲线, 0=关掉增暖)
        'warmMul': 1.0,
        # F3: 模态手动覆盖 {模态名: σ值}。公开索引滞后约 1 个月, 模拟"今天"时
        # 只能靠 AR 外推(符号都可能反), 填上当前实测值即可立刻修正。
        'modeOverride': {},
        # 精度档位(用户 2026-09-18 定)。放在 params 里 —— 设置类参数都在这里,
        # 而且 UI 是直接改 sim['params'] 的。
        'precision': str(o.get('precision', DEFAULT_PRECISION)),
    }
    params.update(o.get('params') or {})
    # ── F1: 绝对时间。start 决定日历;month/month0 保留为派生量(旧代码兼容)。
    start = parse_start(o.get('start', o.get('start_time')))
    if 'month' in o and 'start' not in o and 'start_time' not in o:
        # 旧接口 {'month': 9}: 视为该年(默认 2000)该月 1 日 00z
        start = datetime(int(o.get('year', 2000)), int(o['month']), 1, 0)
    if not (YEAR_MIN <= start.year <= YEAR_MAX):
        raise ValueError('模拟起始年份需在 %d-%d 之间(得到 %d)'
                         % (YEAR_MIN, YEAR_MAX, start.year))
    env_step = snap_env_step(o.get('envStepH', o.get('env_step_h', DEFAULT_ENV_STEP)))
    sim = {
        't': 0.0,
        # 本年累计 ACE(台风消亡也保留; tc['ace'] 是逐台风的)
        'aceSum': 0.0,
        'start': start,                     # F1: 绝对 UTC 起始时刻
        'envStepH': env_step,               # F1: 环境场重建步长(小时)
        'month0': start.month,
        'month': start.month,
        'year0': start.year,
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
        # -1e9: 保证第一步 sim_step 就刷新环境(否则 t=1.0 前 env 全零, 台风秒死)
        'lastEnvH': -1e9, 'lastEnsH': -1.0, 'lastGenH': -1.0,
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

_grids_cache = {}


def _stable_seed(key: str) -> int:
    """稳定的字符串种子。

    不能用内置 hash(): CPython 的字符串 hash 每个进程都加了随机盐
    (PYTHONHASHSEED), 于是同一个 key 在两次启动里得到不同的随机场 ——
    同一个日期的模拟结果每次打开程序都不一样(实测 Niño 区点位值在
    30.7/30.4/28.8 之间漂)。crc32 是确定的。
    """
    import zlib
    return zlib.crc32(str(key).encode('utf-8')) & 0x7FFFFFFF


def _env_grids():
    g = _grids_cache.get('g')
    if g is None:
        lats = np.linspace(C['LAT0'] + 0.5 * C['ENVD'],
                           C['LAT1'] - 0.5 * C['ENVD'], ENVNY)
        lons = np.linspace(C['LON0'] + 0.5 * C['ENVD'],
                           C['LON1'] - 0.5 * C['ENVD'], ENVNX)
        g = np.meshgrid(lats, lons, indexing='ij')
        # 网格是常量, 缓存起来 —— env_update 一次要取三十几次, 每次 meshgrid
        # 都要新分配 2×1.4MB(0.5° 网格), 纯属浪费
        #
        # **dtype 用 float32**(2026-09-18 定): 环境场(env['sst']/shear/u850…)
        # 全是 float32, 而网格原来是 float64 —— 于是 env_update 里每一个
        #  参与的数组运算都被提升成 float64, 内存流量翻倍。
        # 实测切到 float32 后 env_update 174 → 131 ms(1.32×)。
        # 精度: 0.5° 网格需要 ~4 位有效数字, float32 有 7 位, 绰绰有余。
        _grids_cache['g'] = (g[0].astype(np.float32), g[1].astype(np.float32))
    return _grids_cache['g']


#: ── 轮次 158/159: **只依赖固定网格**的常量预计算 ──────────────────────────
#: 这些量只由 (LAT, LON) 决定, 与环境/时间/台风状态**全无关**, 但 `_env_update_impl`
#: 每步都在重算(每步还要算两遍: 主导 + 集合的 env 序列)。
#: 逐位一致的理由: **同一个表达式只算一次然后复用**, 运算顺序与 dtype 都没变;
#: `np.exp(-a/12.0)` 与原来的 `np.exp(-a/12)` 完全相同(12.0 == 12)。
#:
#: 轮次 159 追加(全部只依赖 LAT/LON; 每条的原式都逐字抄在下面的值注释里):
#: `cos_lat`/`f`/`f_eff`/`rho_f_eff`/`dx_m`/`dx_m3`/`dx_m2`/`pe_base`/`shear_base`/
#: `lon035`/`lon100_70`/`lon011`/`lat02`。其中 `cos_lat` 尤其值钱: 原来干空气块循环
#: (3 个)与涡旋循环(7 个)**每步各重算一次全网格 `np.cos(LAT*C['DEG'])`**。
#:
#: **有界性**: 键是网格身份 `id(g[0])`, 且命中/未命中都走「先 clear 再放一份」
#: ⇒ 条目数恒为 1, 不随调用次数或模拟时长增长(网格是模块级单例)。
#: **别名纪律(重要)**: 这些数组一律**只读**; 唯一要就地累加的 `pe`(下面 7 次 `+=`,
#: 再加季风槽与涡旋项)必须从 `_CG['pe_base'].copy()` 起步 —— 直接拿缓存对象去
#: `+=` 会把缓存改坏(第二次调用起数值全错)。
_CONST_GRID = {}


def _const_grids():
    """返回网格常量字典(首次调用时算, 之后直接复用)。"""
    g = _grids_cache.get('g')
    if g is None:
        g = _env_grids()
    key = id(g[0])
    c = _CONST_GRID.get(key)
    if c is None:
        LAT, LON = g
        a = np.abs(LAT)
        # 下面每条都是"原式", 不许合并/改写(浮点不满足结合律)
        cos_lat = np.cos(LAT * C['DEG'])
        f = 2 * C['OMEGA'] * np.sin(LAT * C['DEG'])
        f_eff = np.where(f >= 0, 1.0, -1.0) * np.maximum(np.abs(f), 3.2e-5)
        exp_lat_m3 = np.exp(-((LAT - 3.0) / 8.0) ** 2)
        exp_lat_p3 = np.exp(-((LAT + 3.0) / 8.0) ** 2)
        c = {
            'a': a,                                          # |LAT|
            'exp_a12': np.exp(-np.abs(LAT) / 12.0),          # 原 `np.exp(-a/12)`
            'exp_lat_m3': exp_lat_m3,                        # 原 `-((LAT-3)/8.0)**2`
            'exp_lat_p3': exp_lat_p3,                        # 原 `-((LAT+3)/8.0)**2`
            # ── 轮次 159 追加 ──
            'cos_lat': cos_lat,                              # 原 `np.cos(LAT*C['DEG'])`
            'f': f,                                          # 原 `2*C['OMEGA']*np.sin(LAT*C['DEG'])`
            'f_eff': f_eff,                                  # 原 `np.where(f>=0,1.0,-1.0)*np.maximum(np.abs(f),3.2e-5)`
            'rho_f_eff': C['RHO'] * f_eff,                   # 原 `C['RHO'] * f_eff`(每步用两次)
            'dx_m': C['ENVD'] * 111.32 * cos_lat,            # 原 `C['ENVD']*111.32*cos_lat`
            'dx_m3': 111.32e3 * cos_lat * C['ENVD'],         # 原 `111.32e3*cos_lat*C['ENVD']`
            'dx_m2': 111.32 * cos_lat * C['ENVD'],           # 原 `111.32*cos_lat*C['ENVD']`
            'pe_base': 1011.0 - 2.5 * exp_lat_m3 - 2.5 * exp_lat_p3,
            'shear_base': (4.0 + 0.28 * np.maximum(0.0, a - 10)
                           + 0.05 * np.maximum(0.0, a - 18) ** 1.5),
            'lon035': LON * 0.35,                            # 原 `LON * 0.35`
            'lon100_70': (LON - 100.0) / 70.0,               # 原 `(LON - 100.0) / 70.0`
            'lon011': LON * 0.11,                            # 原 `LON * 0.11`
            'lat02': LAT * 0.2,                              # 原 `LAT * 0.2`
            # ── 轮次 162 追加: 平流用的纬向每度公里数 ──
            # `_advect_env` 里原来每次算 `111.32 * np.cos(lat2 * C['DEG'])`, 而它只依赖
            # 纬度网格与 `C['DEG']` ⇒ **常量网格**; 但 `ocean_step` 是**每小时**一步
            # (6h 步长下 6 次), 每次两个场 ⇒ 实测 **12 次/步 × 0.896 ms ≈ 10.8 ms/步**。
            # `111.32 * cos_lat` 与 `111.32 * np.cos(LAT * C['DEG'])` 是**同一个表达式**
            # (cos_lat 就是它的值) ⇒ 逐位相同。
            'km_l': 111.32 * cos_lat,
            # `np.maximum(km_l, 1.0)` 同样只依赖纬度(只在 |lat|>89.48° 的极区行生效),
            # 原来每次调用都做一次全网格 maximum + 分配(约 0.3 ms × 12 次/步)。
            'km_l_c': np.maximum(111.32 * cos_lat, 1.0),
            # ── 轮次 160 追加: ENSO 空间型 ──
            # `enso_update` 里原来每步重算 `lo/dE/dW`(4 个全网格 `np.exp`)。
            # 三者只依赖 `_env_grids()` 与常量(不含 T/P/月/t/DATA) ⇒ 键=网格身份即可。
            # 消费点(`env['sst'] + T*1.8*dE - T*0.7*dW` 等)全是**出栈**运算 ⇒
            # 不会被就地改坏, 别名安全。
            'enso_lo': np.where(LON > 180.0, LON - 360.0, LON),   # 原 `np.where(lon>180, lon-360, lon)`
            'enso_dE': np.exp(-((np.where(LON > 180.0, LON - 360.0, LON) + 145.0) / 42.0) ** 2)
            * np.exp(-(LAT / 13.0) ** 2),                    # 原 `dE` 逐字复刻
            'enso_dW': np.exp(-((np.where(LON > 180.0, LON - 360.0, LON) - 130.0) / 40.0) ** 2)
            * np.exp(-(LAT / 13.0) ** 2),                    # 原 `dW` 逐字复刻
        }
        _CONST_GRID.clear()          # 网格是全局单例, 只留一份, 不泄漏
        _CONST_GRID[key] = c
    return c


#: ── 轮次 158/159: **按月**变化的纬度带基态 ────────────────────────────────
#: `month_a = month`(整数月) ⇒ `mt_n/mt_s/sh_n/sh_s` 只在**换月**时变, 但它们在
#: `_env_update_impl` 里每步都参与 4 个(湿度带) + 2 个(季风槽低压带)全网格 `np.exp`。
#: 按键缓存**逐位安全**: 键相同 ⇒ 四个标量与网格完全相同 ⇒ 同一个表达式给出同一个
#: 数组。表达式**逐字复刻**原式(含运算顺序) —— 浮点不满足结合律, 不许"顺手化简"。
#: 轮次 159 追加两项**也只随整数月变**的量:
#:   · `shear_seas` —— 解析切变的半球镜像季节项: 原式 `season_a = np.where(LAT >= 0,
#:     month, (month + 5) % 12 + 1)` 后 `3.0*np.abs(np.sin(np.pi*(season_a-8)/12.0))`,
#:     每步一个 `np.where` + `sin` + `abs` + 乘法(4 个全网格算子);
#:   · `seas` —— 冷高压季节钟形: 原式 `np.where(LAT>=0, np.cos(2*pi*(_mo-1)/12.0),
#:     np.cos(2*pi*(_mo-7)/12.0))` 再 `np.clip(..., 0, 1) ** 1.5`(`_mo = float(month)`)。
#: 两者都只依赖 (LAT, 整数月)。
#: ⚠ 轮次 160 起键是 `(id(LAT), mt_n, mt_s, float(month))` —— 不再是"只含
#: `int(month)`": ① `int` → `float` 与内容口径统一(防非整数月静默命中 9 月);
#: ② 补了网格身份(同形状换域会静默返回旧场)。本行原先写的"键含 `int(month)`"
#: 已过期(verifier 审计, DOC_AUDIT_R162.md)。
_BAND_CACHE = {}


def _month_bands(LAT, LON, mt_n, mt_s, month):
    """返回按月缓存的 {'rh', 'mt', 'shear_seas', 'seas'}。"""
    # ⚠ 轮次 159: 键与内容必须用**同一个** month 口径。原来键是 `int(month)` 而
    # 内容里用的是 `month` ⇒ 将来若有人传非整数月(如 9.5), 会命中 9 月的缓存并
    # **静默**返回 9 月的场。现在统一成 float(整数月转 float 是精确的, 逐位不变)。
    # (verifier 指出, VERIFY_R158.md §2.6 附带条)
    month = float(month)
    # ⚠ 轮次 159: 键**必须含网格身份** —— 缓存内容用了 LAT/LON, 而两个兄弟缓存
    # (`_CONST_GRID` 用 `id(g[0])`、`_RIDGE_GRID_CACHE` 用 `(id(LAT), tuple(RIDGES))`)
    # 都带了。今天不可触发(网格是永不失效的进程单例), 但同形状换域会**静默**返回旧场。
    # (perf-reviewer 指出, VERIFY_PERF_R159.md)
    _lg = _env_grids()
    key = (id(_lg[0]), mt_n, mt_s, month)
    c = _BAND_CACHE.get(key)
    if c is None:
        sh_n = 26 + 4 * math.sin(2 * math.pi * (month - 8) / 12)
        sh_s = -(26 + 4 * math.sin(2 * math.pi * (month - 2) / 12))
        rh = 70.0 + 13.0 * np.exp(-((LAT - mt_n) / 4.5) ** 2) \
             + 13.0 * np.exp(-((LAT - mt_s) / 4.5) ** 2)
        rh -= 9.0 * np.exp(-((LAT - sh_n) / 4.0) ** 2)
        rh -= 9.0 * np.exp(-((LAT - sh_s) / 4.0) ** 2)
        mt = []
        for _mlat in (mt_n, mt_s):
            dy_mt = (LAT - _mlat) / 5.5
            dx_mt = (LON - 128.0) / 30.0
            mt.append(6.5 * np.exp(-(dy_mt * dy_mt) - (dx_mt * dx_mt)))
        # 轮次 159: 下面两条是**原式的逐字复刻**(见上方注释), 不许改写形状
        season_a = np.where(LAT >= 0, month, (month + 5) % 12 + 1)
        shear_seas = 3.0 * np.abs(np.sin(np.pi * (season_a - 8) / 12.0))
        _mo = float(month)
        seas = np.where(LAT >= 0,
                        np.cos(2 * np.pi * (_mo - 1) / 12.0),
                        np.cos(2 * np.pi * (_mo - 7) / 12.0))
        seas = np.clip(seas, 0.0, 1.0) ** 1.5
        c = {'rh': rh, 'mt': mt, 'shear_seas': shear_seas, 'seas': seas}
        _BAND_CACHE.clear()          # 只有当前月有用, 不随月份数增长
        _BAND_CACHE[key] = c
    return c


#: ── 轮次 159: **只依赖副高常量表**的经纬网格 ──────────────────────────────
#: `_env_update_impl` 的副高循环(RIDGES 共 7 个中心)每步都在重算
#:   `d = LON - rlon;  d = (d + 180.0) % 360.0 - 180.0   # 经度周期化`
#: 以及 `LAT - rlat`。这两条**只依赖固定网格与 RIDGES 常量**, 与时间/环境无关。
#: 实测(360×720 float32, 本机): `np.mod(x, 360.0)` 是全函数里最贵的单算子
#: (≈7.4 ms; 对比 `LON*0.35` ≈0.06 ms) —— 7 个中心 × 每步 2 次(主导 + 集合)
#: 全花在重算同一条取模上。
#: **逐位安全**: 原式一字不改地只在缓存构建时算一次; 缓存值与原值 `np.array_equal`。
#: **有界性**: 键 = (网格身份, 当前 `tuple(RIDGES)`) ⇒ 只有一份; RIDGES 变了自动重建。
_RIDGE_GRID_CACHE = {}


def _ridge_grids():
    """返回 [(LAT - rlat, wrap_lon180(LON - rlon)), …], 顺序同 `RIDGES`。"""
    g = _grids_cache.get('g')
    if g is None:
        g = _env_grids()
    LAT, LON = g
    key = (id(LAT), tuple(RIDGES))
    c = _RIDGE_GRID_CACHE.get(key)
    if c is None:
        c = []
        for rlat, rlon, _ramp in RIDGES:
            d = LON - rlon
            d = (d + 180.0) % 360.0 - 180.0          # 经度周期化(原式)
            c.append((LAT - rlat, d))
        _RIDGE_GRID_CACHE.clear()      # 只留当前副高表那一份, 不泄漏
        _RIDGE_GRID_CACHE[key] = c
    return c


def _perf_drop_caches() -> None:
    """**仅供 A/B 测量**: 清空本模块的常量/月度缓存, 强制下一次调用逐次重算。

    数值**完全不变**(只是变慢) —— `tools/perf_ab_r158.py` 这类同进程交错 A/B
    用它在同一份代码里做出"有缓存 / 无缓存"两条路径。
    """
    _CONST_GRID.clear()
    _RIDGE_GRID_CACHE.clear()
    _BAND_CACHE.clear()


#: 随机扰动谱缓存(按 key 生成一次, 之后只按时相演化)
_NOISE_SPEC = {}


def _noise_build(key: str, n: int, kmin: int, kmax: int):
    """建一条空间随机谱: (Z, omega)。Z 已按 std=1 归一。"""
    rng = np.random.default_rng(_stable_seed(key))
    ky = rng.integers(-kmax, kmax + 1, n)
    kx = rng.integers(0, kmax + 1, n)          # 只留非负 x 频率(rfft2 半谱)
    m = np.hypot(kx, ky) >= kmin
    kx, ky = kx[m], ky[m]
    if kx.size == 0:
        kx, ky = np.array([kmin]), np.array([0])
    ph = rng.uniform(0.0, 2 * np.pi, kx.size)
    # 时间频率: 与原实现同量级(0.05~0.25 rad/模拟小时), 即"缓慢漂移"
    om = 0.05 * (1.0 + rng.integers(0, 5, kx.size))
    Z = np.zeros((ENVNY, ENVNX // 2 + 1), np.complex128)
    amp = 1.0 / np.maximum(np.hypot(kx, ky), 1.0)      # 随波数衰减(能量谱)
    iy = ky % ENVNY
    ix = kx
    Z[iy, ix] += amp * np.exp(1j * ph)
    # 归一化: 用 t=0 的实场定标, 之后相位旋转不改变方差
    f0 = np.fft.irfft2(Z, s=(ENVNY, ENVNX))
    s0 = float(f0.std())
    if s0 > 1e-12:
        Z = Z / s0
    return Z, om, iy, ix


def mesoscale_noise(sim, key: str, amp: float, n: int = 70,
                    kmin: int = 4, kmax: int = 10) -> np.ndarray:
    """空间 2~8 度、随时间缓慢漂移的随机扰动(模拟中尺度涡/锋面)。

    月气候态太平滑, 直接画出来就是一条纬向直线。这里用一条随机二维谱合成纹理,
    每个谱分量的相位按自己的频率旋转:
      * 同一时刻: 场里有涡状/条带状纹理, 不再是一条直线;
      * 不同时刻: 是**不同的实现**, 且时间上连续(不是把同一张图平移)。
    返回值已归一化到标准差 = amp, 调用方直接加到 SST/OHC 上。

    F2: 由"逐模态 sin 累加"改成 **FFT 合成**。原来是 70 次全网格 sin, 内核升到
    0.5°(240×720)后单次 194ms, 而每次 env_update 要调两遍(SST+OHC) → 占
    整个 env_update 的 77%。FFT 版为 O(N log N), 实测 <10ms, 时空谱也更规范。
    """
    spec = _NOISE_SPEC.get(key)
    if spec is None:
        spec = _noise_build(key, n, kmin, kmax)
        _NOISE_SPEC[key] = spec
    Z, om, iy, ix = spec
    t = float(sim.get('t', 0.0))
    # 只旋转非零分量(逐格乘会广播不上, 且全谱乘法是浪费)
    Zt = np.zeros_like(Z)
    Zt[iy, ix] = Z[iy, ix] * np.exp(1j * om * t)
    f = np.fft.irfft2(Zt, s=(ENVNY, ENVNX))
    return (f * amp).astype(np.float32)


#: ── 瞬变槽脊引导(轮次 102 新增) ─────────────────────────────────────────
#: 月平均气候态**产生不了转向所需的经向风**。用观测路径在模型分层引导上做最优拟合,
#: 残余仍系统性缺向极分量: **-2.4 / -3.9 / -7.9 kt**(20-30 / 30-40 / 40-60N),
#: 残余 std 5.6 / 7.8 / 8.7 kt。真实转向是"槽前西南气流"这种**瞬变过程**造成的,
#: 而月均场里槽脊被平滑掉了。
#: 做法: 用**无辐散随机流函数** ψ 合成异常 ——
#:     u' = -dψ/dy,  v' = +dψ/dx
#: 这样 u'/v' **自动**满足"槽前西南(+,+)、槽后西北(-,-)"的相关结构, 而不是把 u
#: 与 v 各自独立乱加(那得到各向同性涡, 没有转向意义)。
#: 空间尺度取天气尺度(纬向波数 3-6 ⇒ 波长 120~60 度), 幅度随 |lat| 增长
#: (深热带近零, 副热带/中纬 ~8kt)。
_SYN_STEER_V0 = 1.6           #: |lat| <= _SYN_STEER_LAT0 的**经向**天气尺度幅度(kt, std)
_SYN_STEER_VG = 3.9           #: 到 |lat| >= _SYN_STEER_LAT1 再增加的量(kt)
_SYN_STEER_LAT0 = 8.0
_SYN_STEER_LAT1 = 30.0
#: 纬向幅度 / 经向幅度。天气尺度槽脊是各向异性的(纬向风异常通常更大),
#: 但**转向靠的是经向分量**, 所以两者分别定标, 而不是让一个各向同性因子决定。
_SYN_STEER_URATIO = 1.4


def synoptic_steer(sim):
    """瞬变槽脊对引导的贡献 (u', v'), 单位 kt, 全网格。

    见上面常量处的说明。返回 (None, None) 表示不可用(网格缺失), 调用方跳过。
    """
    nlat, nlon = ENVNY, ENVNX
    psi = mesoscale_noise(sim, 'steer_psi', 1.0, n=48, kmin=3, kmax=6)
    if psi.shape != (nlat, nlon):
        return None, None
    LAT, _LON = _env_grids()
    coslat = np.maximum(0.15, np.cos(LAT * C['DEG']))
    dy_m = C['ENVD'] * 110.57
    dx_m = C['ENVD'] * 111.32 * coslat
    du = -np.gradient(psi, axis=0) / dy_m
    # dx_m 已经是 (nlat, nlon) 的逐列格距; **不能再写 dx_m[:, None]** ——
    # 二维数组的 [:, None] 会插成 (nlat, 1, nlon), 除法广播成 (nlat, nlat, nlon)
    # (实测 sv 变成 (360,360,720), 白吃 354MB 还在 env_update 里炸掉)。
    dv = np.gradient(psi, axis=1) / dx_m
    # **逐分量定标到指定的纬度幅度包络**(不能用"整体 RMS 归一再乘一个数":
    # 那样得到的场其纬度分布由 |grad psi| 自己决定, 与包络无关 —— 第一版就是这样,
    # 结果 0-10N 的 v std 反而比 20-30N 还大)。
    ramp = np.clip((np.abs(LAT) - _SYN_STEER_LAT0)
                   / (_SYN_STEER_LAT1 - _SYN_STEER_LAT0), 0.0, 1.0)
    amp_v = _SYN_STEER_V0 + _SYN_STEER_VG * ramp
    amp_u = _SYN_STEER_URATIO * amp_v
    # **逐纬行归一**再乘包络: dv 里有 1/cos(lat) 因子(纬向格距随纬度收缩),
    # 用**全局** std 归一会让低纬的 v 幅度被高纬的行距放大效应压掉
    # (实测 0-10N 的 v std 只有包络值的 1/3)。逐行归一是纬度的连续函数, 不会产生
    # 带边界跳变, 且使"每个纬行的局地 std 精确等于包络值"。
    ru = np.std(du, axis=1, keepdims=True)
    rv = np.std(dv, axis=1, keepdims=True)
    ru = np.where(ru < 1e-9, 1.0, ru)
    rv = np.where(rv < 1e-9, 1.0, rv)
    return ((du / ru * amp_u).astype(np.float32),
            (dv / rv * amp_v).astype(np.float32))


#: MJO: 逐日 RMM 索引。**这是环境场里唯一能提供季节内(30-60 天)变率的源** ——
#: 用月平均气候态时, 同一个月里所有风暴看到的环境几乎一样, 强度结果趋同,
#: 于是峰值分布太窄(实测 P95/P50 = 1.5, 观测 2.15)。真实超台的成因是"时间窗口":
#: 风暴恰好在 3-5 天里处于 MJO 活跃位相 + 有利槽脊配置。
#: 相位 → 对流中心经度用标准 Wheeler-Hendon 映射(PH1≈80E 印度洋, PH5≈160E 西太,
#: PH8≈260E 东太), 扰动取波 1 形式。
_MJO = {'ok': False, 'lut': None}


def _mjo_tables():
    if _MJO['ok']:
        return _MJO
    _MJO['ok'] = True
    try:
        from simulator.simcore.envmodel import store as _st
        z = _st.load('modes_index')
        if z is not None and 'rmm' in z:
            R = np.asarray(z['rmm'], np.float64)
            lut = {}
            for y, mo, d, a, b in R:
                lut[(int(y), int(mo), int(d))] = (float(a), float(b))
            _MJO['lut'] = lut
    except Exception:
        pass
    return _MJO


def mjo_field(sim, amp_kt: float = 5.0):
    """MJO 引起的切变扰动场(kt, (ENVNY, ENVNX))。取不到 RMM 时返回 None。

    振幅 5kt 的依据: MJO 活跃/抑制位相在热带 850hPa 的纬向风异常约 ±3m/s(≈6kt),
    切变异常同量级偏小。
    """
    T = _mjo_tables()
    if T['lut'] is None:
        return None
    clk = clock(sim)
    v = T['lut'].get((clk.year, clk.month, clk.day))
    if v is None:
        return None
    a, b = v
    m = math.hypot(a, b)
    if not math.isfinite(m) or m < 1e-6:
        return None
    # **缺测填充值必须挡住**(2026-09-20): 这份 RMM 表里有 290 天(1.6%)的幅度是
    # 1.41e36(缺测填充), 而 min(m,3) 会把它们全钳成"最强 MJO" ⇒ 那些日期会凭空多出
    # 一个满幅的 MJO 扰动。真实 RMM 幅度在 0-4 之间(中位 1.2, 90 分位 2.2), 所以
    # 超过 10 一律当缺测处理(没有 MJO 调制, 而不是假装最强)。
    if m > 10.0:
        return None
    ph = (math.degrees(math.atan2(b, a)) + 360.0) % 360.0      # 0-360, PH1≈0
    lon_c = 80.0 + ph / 45.0 * 22.5                            # 对流中心经度
    # 振幅包络: 热带 + 印度洋/西太最强(那里 MJO 信号最大)
    glon = C['LON0'] + (np.arange(ENVNX) + 0.5) * C['ENVD']
    glat = C['LAT0'] + (np.arange(ENVNY) + 0.5) * C['ENVD']
    env_lat = np.exp(-(glat[:, None] / 16.0) ** 2)
    env_lon = 0.5 + 0.5 * np.cos(np.radians((glon - 130.0) * 1.4))
    wave = np.cos(np.radians(glon - lon_c))[None, :]
    return (amp_kt * float(min(m, 3.0)) * env_lat
            * env_lon[None, :] * wave).astype(np.float32)


#: 随机项合成后的空间低通尺度(格)。
#: **标定过程**(北太平洋 20-40N/160E-160W 开阔洋, 拉普拉斯方差, 越小越平滑):
#:   真实 ERA5 单日 0.25°        0.01996  → 换算到 0.5° 网格 ≈ 0.00499
#:   σ=0.0(只靠降振幅)          0.00573
#:   σ=0.4                       0.0042   ← 取这个
#:   σ=1.5(最初拍的值)          0.00102   比真实平滑 5 倍, 过头了
#: 结论: 把噪声振幅从 0.9 降到 0.35 已经做了大部分工作, 低通只需要轻微收尾。
_LP_SIGMA = 0.4
#: 风切场的低通尺度。NCEP 原始网格 2.5°, 双线性升到 0.5° 后等值线会
#: 沿粗网格走、出现锯齿; 场先低通再把等值线放到 1° 网格上算(见 render._shear_segments)。
_SHEAR_LP = 1.0
#: 热带 200hPa 出流层温度的参考值(K): NCEP 气候态热带平均 ≈ -51℃ = 222K
T200_REF = 222.0


try:
    from scipy import ndimage as _SCI_NDI
except Exception:
    _SCI_NDI = None


#: 海洋中尺度涡(暖核/冷核环)缓存
_EDDY_CACHE: dict = {}


def _up2(a: np.ndarray) -> np.ndarray:
    """(ny,nx) → (2ny,2nx) 双线性升采样(给涡旋场用)。"""
    ny, nx = a.shape
    out = np.empty((ny * 2, nx * 2), np.float32)
    out[0::2, 0::2] = a
    out[0::2, 1::2] = (a + np.roll(a, -1, axis=1)) * 0.5
    out[1::2, 0::2] = (a + np.roll(a, -1, axis=0)) * 0.5
    out[1::2, 1::2] = (a + np.roll(a, -1, axis=0) + np.roll(a, -1, axis=1)
                       + np.roll(np.roll(a, -1, axis=0), -1, axis=1)) * 0.25
    return out


#: MJO: 逐日 RMM 索引。**这是环境场里唯一能提供季节内(30-60 天)变率的源** ——
#: 用月平均气候态时, 同一个月里所有风暴看到的环境几乎一样, 强度结果趋同,
#: 于是峰值分布太窄(实测 P95/P50 = 1.5, 观测 2.15)。真实超台的成因是"时间窗口":
#: 风暴恰好在 3-5 天里处于 MJO 活跃位相 + 有利槽脊配置。
#: 相位 → 对流中心经度用标准 Wheeler-Hendon 映射(PH1≈80E 印度洋, PH5≈160E 西太,
#: PH8≈260E 东太), 扰动取波 1 形式。
_MJO = {'ok': False, 'lut': None}


def _mjo_tables():
    if _MJO['ok']:
        return _MJO
    _MJO['ok'] = True
    try:
        from simulator.simcore.envmodel import store as _st
        z = _st.load('modes_index')
        if z is not None and 'rmm' in z:
            R = np.asarray(z['rmm'], np.float64)
            lut = {}
            for y, mo, d, a, b in R:
                lut[(int(y), int(mo), int(d))] = (float(a), float(b))
            _MJO['lut'] = lut
    except Exception:
        pass
    return _MJO


def _gauss_kernel(sigma: float):
    n = max(3, int(2.5 * sigma) * 2 + 1)
    if n % 2 == 0:
        n += 1
    x = np.arange(n) - n // 2
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


try:
    from scipy import ndimage as _SCI_NDI
except Exception:
    _SCI_NDI = None


def _lowpass(a: np.ndarray, sigma: float) -> np.ndarray:
    """可分离高斯低通(边界用 edge 填充)。

    为什么必须做: 随机谱合成的纹理**空间上不连续** —— 直接加到 SST 上就是
    一堆孤立斑点, 看起来"碎"。真实海洋的月均(甚至单日)SST 是连续的,
    中尺度信号的相关尺度有 100~300 km, 不会出现孤立的 ±1℃ 冷/暖坑。
    低通把随机项压成"大而弱"的相干结构, 而不是"小而强"的斑点。
    """
    if sigma <= 0.0:
        return a
    # **优先用 scipy 的 C 实现**: 手写的两趟 Python 循环每趟要做 kernel 大小次
    # (240,720) 的切片乘加(sigma=1 时 14 次), 实测 ~2 ms/次, 而 env_update
    # 每次要调 6 遍。scipy 的 gaussian_filter 是同一套可分离算法但全在 C 里。
    # 结果与手写版一致(mode='nearest' 对应原来的 edge 填充)。
    if _SCI_NDI is not None:
        return _SCI_NDI.gaussian_filter(a, sigma, mode='nearest')
    k = _gauss_kernel(sigma)
    n = k.size // 2
    ny, nx = a.shape
    p = np.pad(a, ((0, 0), (n, n)), mode='edge')
    t = np.zeros_like(a)
    for i, w in enumerate(k):
        t += w * p[:, i:i + nx]
    p = np.pad(t, ((n, n), (0, 0)), mode='edge')
    o = np.zeros_like(a)
    for i, w in enumerate(k):
        o += w * p[i:i + ny, :]
    return o


#: 海洋中尺度涡(暖核/冷核环)缓存
_EDDY_CACHE: dict = {}
_EDDY_FIELD: dict = {}


def mesoscale_eddies(sim, key: str, n: int, amp: float, r_km: float,
                     drift_kt: float = 0.25) -> np.ndarray:
    """中尺度涡(暖核/冷核环)场, 已归一化到 **峰值 = amp**(见函数内的说明)。

    F7: 月气候态 + 空间随机噪声都产生不了"闭合的暖核/冷核" —— 而那正是真实
    CMEMS 图上那些 180-200 kJ/cm² 的品红核、以及触发 RI 的暖涡。这里放 n 个
    带符号的高斯环, 按各自速度缓慢西传(真实中尺度涡以西传为主)。

    关键: SST 与 D26 用**不同的 key**(不同实现)—— 真实里暖涡可以是"深层暖、
    表层不明显", OHC 必须能有独立于 SST 的结构, 否则又是一个"SST 的影子"。

    性能: 逐环在全网格算 exp 太贵(24+20 环 × 240×720 → 单次 env_update 多
    400ms)。涡旋半径 130~400 km 本来就平滑, 所以在 1° 子网格上算、再双线性
    升采样, 并按 24 模拟小时量化缓存(涡西传很慢, 24h 内变化可忽略)。
    """
    tt = float(sim.get('t', 0.0))
    ck = (key, int(tt // 24))
    got = _EDDY_FIELD.get(ck)
    if got is not None:
        return got
    spec = _EDDY_CACHE.get(key)
    if spec is None:
        rng = np.random.default_rng(_stable_seed(key))
        lat0 = rng.uniform(-35.0, 35.0, n)
        lon0 = rng.uniform(0.0, 360.0, n)
        sgn = np.where(rng.random(n) < 0.5, -1.0, 1.0)
        rad = r_km * rng.uniform(0.7, 1.5, n)
        dr = drift_kt * rng.uniform(0.5, 1.5, n)
        spec = (lat0, lon0, sgn, rad, dr)
        _EDDY_CACHE[key] = spec
        _EDDY_CACHE['shape'] = (ENVNY, ENVNX)
    lat0, lon0, sgn, rad, dr = spec
    # 1° 子网格
    slat = np.linspace(C['LAT0'] + 1.0, C['LAT1'] - 1.0, ENVNY // 2)[:, None]
    slon = np.linspace(C['LON0'] + 1.0, C['LON1'] - 1.0, ENVNX // 2)[None, :]
    t = tt / 24.0                                  # 天
    f = np.zeros((ENVNY // 2, ENVNX // 2), np.float64)
    for i in range(len(lat0)):
        lo = (lon0[i] - dr[i] * 0.44 * t) % 360.0
        dx = (slon - lo + 180.0) % 360.0 - 180.0
        dx = dx * 111.32 * np.cos(slat * C['DEG'])
        dy = (slat - lat0[i]) * 110.57
        rr = np.hypot(dx, dy) / rad[i]
        f += sgn[i] * np.exp(-rr * rr)
    # **按峰值归一化, 不是按标准差**(2026-09-20 修): 这个场是"若干个彼此分开的高斯涡",
    # 尖峰很高而大片接近 0, 所以 std 归一化会把峰值放到 20-30 倍 amp —— 实测
    # amp=5.0 的 eddy_d26 输出 -159..+159(米!), 加到 D26(约 50 m)上直接把 OHC 打成
    # 负值(-122 m), 而 MPI 是拿 D26 当热含量输入的。改成"峰值 = amp"后:
    # OHC 涡 ±5 m、SST 涡 ±0.18 °C, 都是物理量级。
    pk = float(np.abs(f).max())
    if pk > 1e-9:
        f = f / pk * amp
    out = _up2(f.astype(np.float32))
    if len(_EDDY_FIELD) > 24:
        _EDDY_FIELD.clear()
    _EDDY_FIELD[ck] = out
    return out


#: ENSO 充放电振子参数(月为单位): dT/dt = aT - bh - cT^3,  dh/dt = -eT
# 线性化后 d²T/dt² - a·dT/dt + be·T = 0, 周期 2π/sqrt(be - a²/4);
# 取 a=0.05, b=0.35, e=0.05 -> be=0.0175, 周期 ≈ 47 个月(4 年), 振幅 ~2.3°C;
# e 不能太大, 否则 h 摆幅超过限幅(±1.5)会把周期压扁。
ENSO_A, ENSO_B, ENSO_C, ENSO_E = 0.05, 0.35, 0.008, 0.05


def enso_update(sim) -> None:
    """ENSO: 充放电振子(周期约 4 年, 振幅 ±2°C) + 对 SST/风切的遥相关。

    每个模拟步按 elapsed 时间推进振子, 然后把海温/切变异常加到场上:
      * 中东太平洋(Niño3.4, 约 145°W)加暖/冷异常;
      * 西太平洋加反向的小异常;
      * El Niño 年西太切变增强、中东太减弱。
    """
    e = sim.setdefault('enso', {'T': 0.35, 'h': 0.0, 't': 0.0})
    now = float(sim.get('t', 0.0))
    months = (now - float(e['t'])) / 730.0          # 30.4 天 = 730 小时
    if months > 1e-9:
        e['t'] = now
        T, h = float(e['T']), float(e['h'])
        n = max(1, int(math.ceil(months * 4)))
        dt = months / n
        for _ in range(n):
            # 符号很关键: dT/dt = aT + b·h - c·T^3。h 随 T 反向变化(充电/放电),
            # 写 +b·h 才会周期性反转; 写成 -b·h 会单向跑飞(被 clamp 卡住)。
            T += dt * (ENSO_A * T + ENSO_B * h - ENSO_C * T ** 3)
            h += dt * (-ENSO_E * T)
        e['T'] = max(-3.0, min(3.0, T))
        e['h'] = max(-1.5, min(1.5, h))
    # **实验控制: 钉住 ENSO 相位**(2026-09-21 轮次 45)。
    # 用户要"1 年超强厄尔尼诺 / 1 年中性 / 1 年超强拉尼娜"。本函数本来就有**空间型**
    # (中东太增暖 + 西太冷却 + 切变遥相关), 但振荡器周期约 4 年、T 在一年内会自己演化,
    # 给不出"整年都是超强"。传 params['ensoPin'] 即钉住 T, 三种相位用**同一套空间型**对照。
    # (注意: 之前几轮用 params['sstAnom'] 做的那三次"ENSO 年"是**均匀偏移**, 不是 ENSO, 作废。)
    _pin = (sim.get('params') or {}).get('ensoPin')
    if _pin is not None:
        e['T'] = float(_pin)
    T = float(e['T'])
    # 轮次 160: `lo/dE/dW` 只依赖网格(4 个全网格 `np.exp`), 已并入 `_const_grids()`。
    # 逐字复刻: `lo = np.where(lon>180, lon-360, lon)`, `dE = exp(-((lo+145)/42)^2)*exp(-(lat/13)^2)`,
    # `dW = exp(-((lo-130)/40)^2)*exp(-(lat/13)^2)`。消费点全是出栈运算 ⇒ 别名安全。
    _CG = _const_grids()
    lo = _CG['enso_lo']
    dE = _CG['enso_dE']
    dW = _CG['enso_dW']
    env = sim['env']
    env['sst'] = (env['sst'] + T * 1.8 * dE - T * 0.7 * dW).astype(np.float32)
    env['shear'] = np.maximum(
        0.0, env['shear'] + T * 1.8 * dW - T * 1.0 * dE).astype(np.float32)
    env['nino34'] = np.float32(T)


def _env_update_impl(sim) -> None:
    # 环境场版本号: SST/OHC/湿度/气压等一次性重建, 渲染图层据此复用已缩放表面
    sim['_envVer'] = sim.get('_envVer', 0) + 1
    # 安全网: 外部直接改 sim['t'](测试/脚本/回放)时把 sim['month'] 同步到真实日历,
    # 否则只有走 sim_step 才会更新, 直接改 t 会留下一个过期的月份
    sim['month'] = clock(sim).month
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
            # F1: 传绝对时刻 —— 基态气候场按"儒略日 + 绝对年份"取值,
            # 不再用 month0 + t/30 的近似月插值
            DATA = _CL.build_env_fields(P, sim['t'], sim['month0'],
                                        clock=clock(sim))
        except Exception as exc:
            DATA = None
            data_err = f'数据驱动环境场加载异常: {exc}'
    t = sim['t']
    _CG = _const_grids()             # 轮次 158: 常量网格(见 _const_grids)
    # 海洋柱状态(混合层): 首次在此建立; 数据模式用真实海温当初值
    _ocn = ocean_column_init(
        sim, DATA['sst'] if (DATA is not None and 'sst' in DATA) else None)
    if DATA is not None and 'sst' in DATA:
        # 真实海温(气候库) + 冷尾流 + 距平 + 微扰动
        sst = DATA['sst'].copy()
        # 微扰动幅度 0.06: 数据模式真实场已含细结构, 刻意小于解析模式的 0.25
        # 轮次 159: `LON*0.35` 是常量网格(原式见 _const_grids), 不再每步重乘
        env['sst'] = (sst + P['sstAnom']
                      + 0.06 * np.sin(t / 8 + _CG['lon035']) * _CG['exp_a12']
                      - sim['wake'].reshape(NY, NX)).astype(np.float32)
    else:
        # 海温基准 = 海洋混合层温度(含平流/松弛/夹卷的演化量), 不再是每月重建的
        # 解析场 —— 这样台风的冷尾流才会留下来、并随表层流走
        sst = _ocn['Tml']
        env['sst'] = (sst + P['sstAnom']
                      # 0.25: 解析平滑场所需的天气尺度纹理扰动(数据模式用 0.06)
                      + 0.25 * np.sin(t / 8 + _CG['lon035']) * _CG['exp_a12']
                      - sim['wake'].reshape(NY, NX)).astype(np.float32)

    # F5: 单位纪律 —— env['ohc'] 一律是 D26(米)。数据源必须用 _ohc_unit 标明;
    # 旧合成库给的是 kJ/cm², 直接当米用会让 mpi 的 ohc_d26/75 失去意义
    # (上一轮实测: 数据模式下 OHC 项因此恒为常数)。
    _ohc_is_m = (DATA is not None and 'ohc' in DATA
                 and DATA.get('_ohc_unit', 'm') == 'm')
    if _ohc_is_m:
        # 真实 D26(米) + OHC 消耗
        # 轮次 159: `LON*0.11` 与 `LAT*0.2` 都是常量网格(原式见 _const_grids);
        # 结合顺序仍是 `((t/20) + LON*0.11) + LAT*0.2` —— 一个括号都没动
        d26 = (DATA['ohc'].copy() * P['ohcMul']
               + 6 * np.sin(t / 20 + _CG['lon011'] + _CG['lat02']))
        env['ohc'] = np.maximum(18.0, d26 - sim['ohcWake'].reshape(NY, NX)).astype(np.float32)
    else:
        # D26 由海洋柱诊断(混合层深度 + 层结决定), 再减去台风过境的消耗
        d26, _ = d26_from_column(sim)
        env['ohc'] = np.maximum(18.0,
                                d26 * float(P['ohcMul'])
                                - sim['ohcWake'].reshape(NY, NX)).astype(np.float32)

    # F7: D26 的独立中尺度结构(SST 的影子之外, 另有暖核/冷核); 同样只加在数据模式
    if DATA is not None and 'ohc' in env:
        env['ohc'] = _lowpass(env['ohc'], _LP_SIGMA).astype(np.float32)
        if sim.get('_precQual', 1):
            env['ohc'] = (env['ohc'] + mesoscale_eddies(sim, 'eddy_d26', 8, 5.0,
                                                         180.0)).astype(np.float32)

    # 显示用热含量(kJ/cm²): 优先用数据源给的 TCHP(真 CMEMS/AOML 口径);
    # 没有 TCHP 时才退回"把 DATA['ohc'] 当 kJ"的旧行为(旧合成库确实是 kJ)。
    # F5: env['ohc'] 是 D26(米)、ocn['ohc_kj'] 是 kJ/cm² —— 两个口径不能互相冒充。
    if DATA is not None and 'ohc_kj' in DATA:
        _ocn['ohc_kj'] = np.asarray(DATA['ohc_kj'], dtype=np.float32)
    elif (DATA is not None and 'ohc' in DATA
          and DATA.get('_ohc_unit', 'm') == 'kJ'):
        _ocn['ohc_kj'] = np.asarray(DATA['ohc'], dtype=np.float32)
    else:
        _, _ohc_kj = d26_from_column(sim)
        _ocn['ohc_kj'] = _ohc_kj

    # 季风槽/副高纬度带(公共, 供气压场用)
    month_a = month
    mt_n = 10 + 3.5 * math.sin(2 * math.pi * (month_a - 5) / 12) + 2
    mt_s = -(10 + 3.5 * math.sin(2 * math.pi * (month_a - 11) / 12) + 2)
    # 轮次 159: 月度缓存**提到这里** —— 解析切变的季节项(下面 `_B['shear_seas']`)
    # 也要用它; 湿度块(原 1688 行)与气压块继续复用同一份, 不会重复算。
    _B = _month_bands(LAT, LON, mt_n, mt_s, month)

    # 风切变(kt): 数据模式用真实切变, 否则解析
    if DATA is not None and 'shear' in DATA:
        # 库里的 shear 单位是 m/s(climate.py 单位说明), 与解析分支一样换算成 kt;
        # 漏乘 KT 会让数据模式的风切整体小 1.94 倍、且与 SHEAR_MIN_KT 不同量纲
        # F6: 库/模型给的 shear 已经是 kt, 原来这里又乘一次 KT(1.944)是
        # 单位换算做重了 —— 现在是死代码(下面被 hypot 覆盖), 但一旦走到这条
        # 分支就会把风切放大 1.944 倍, 属于潜伏 bug。
        shear = DATA['shear'].copy()
        # 叠加天气尺度正/异常切变包(动态天气)
        # 轮次 159: `(LON-100.0)/70.0` 常量网格(原式见 _const_grids)
        shear = shear + 2.2 * np.sin(2 * np.pi * (t / 620.0 + _CG['lon100_70']))
        # **天气尺度切变扰动(2-10 天)**: 环境场是月平均的, 同一个月里所有风暴
        # 看到的风切完全一样 → 强度结果趋同, 分布太窄(实测 P95/P50 = 1.5,
        # 观测 2.15)。真实台风经历的是瞬变槽脊, 撞上有利窗口的才会爆发成超台。
        # mesoscale_noise 的时间频率 om=0.05~0.25 rad/h 正好是 25~126h 的天气尺度。
        _syn = mesoscale_noise(sim, 'shear_syn', 6.0)
        _mjo = mjo_field(sim, 5.0)
        _extra = _syn if _mjo is None else (_syn + _mjo)
        env['shear'] = _lowpass(np.maximum(SHEAR_MIN_KT, shear + P['shearOff']
                                           + _extra), _SHEAR_LP).astype(np.float32)
    else:
        # 风切变(kt, 半球镜像季节)
        # 轮次 159: 前一项只依赖 LAT(常量网格), 第二项只随整数月变 —— 两者都改成
        # "算一次复用"(原式逐字见 _const_grids / _month_bands)。
        # ⚠ 逐位安全的**唯一**写法: 必须以 `.copy()` 起步再**就地** `+=`, 不能写成
        #   `_CG['shear_base'] + _B['shear_seas']` —— `shear_seas` 是 float64
        #   (`season_a` 由 np.where 生成 int64 ⇒ sin 出来是 float64), 而原式的就地
        #   `+=` 会把右操作数降回 float32 再加; 出栈的 `+` 则整体提升成 float64,
        #   舍入位就变了(实测 dtype 会从 float32 变 float64)。
        shear = _CG['shear_base'].copy()
        shear += _B['shear_seas']
        shear += 2.5 * np.sin(2 * np.pi * (t / 620.0 + _CG['lon100_70']))
        env['shear'] = _lowpass(np.maximum(SHEAR_MIN_KT, (shear + P['shearOff']) * KT),
                                _SHEAR_LP).astype(np.float32)

    # 中层湿度: 数据模式用真实湿度, 否则解析; 干空气块叠加保留
    if DATA is not None and 'rh' in DATA:
        rh = DATA['rh'].copy() + P['rhOff']
        for db in sim['dryBlobs']:
            # 轮次 159: `np.cos(LAT*C['DEG'])` → 常量网格 `_CG['cos_lat']`
            #(逐位相同: 同一个 `np.cos` 的同一个输入, 只算一次)
            dx = (LON - db['lon']) * 111.32 * _CG['cos_lat']
            dy = (LAT - db['lat']) * 110.57
            rr = np.hypot(dx, dy) / db['R']
            rh -= db['str'] * np.exp(-rr * rr)
        env['rh'] = np.clip(rh, 25, 95).astype(np.float32)
        # F8: 出流层温度(200hPa)。MPI 的物理依据是 Emanuel 的 V²∝(Ts−To)/To,
        # 对流层顶越高/出流层越冷 → 上下温差越大 → 同样 SST 下 MPI 越强。
        if 't200' in DATA:
            env['t200'] = np.asarray(DATA['t200'], np.float32)
        # F9: 海平面气压也要带进来 —— 冷高压项靠它的**梯度**驱动, 之前 solver
        # 给了但 core 没接, 所以 coldSurge 一直拿不到输入。
        if 'mslp' in DATA:
            env['mslp'] = np.asarray(DATA['mslp'], np.float32)
    else:
        # 中层湿度(双季风槽湿区 + 副高干区 + 干空气块)
        # 轮次 158: 前四项只随**月份**变(见 _month_bands), 按键缓存; 干空气块是
        # 时变量, 仍在下面逐块叠加。`+ P['rhOff']` 另起一个数组 ⇒ 缓存不会被就地改。
        # 轮次 159: `_B` 已在上面(纬度带处)取好。
        rh = _B['rh'] + P['rhOff']
        for db in sim['dryBlobs']:
            # 轮次 159: 同数据分支 —— `np.cos(LAT*C['DEG'])` 用常量网格
            dx = (LON - db['lon']) * 111.32 * _CG['cos_lat']
            dy = (LAT - db['lat']) * 110.57
            rr = np.hypot(dx, dy) / db['R']
            rh -= db['str'] * np.exp(-rr * rr)
        env['rh'] = np.clip(rh, 25, 95).astype(np.float32)

    # 环境气压: 数据模式用真实 mslp 作基底, 否则解析(多副高 + 季风槽 + 涡旋)
    if DATA is not None and 'mslp' in DATA:
        pe = DATA['mslp'].copy()
        for ed in sim['eddies']:
            # 轮次 159: `np.cos(LAT*C['DEG'])` → 常量网格
            dx = (LON - ed['lon']) * 111.32 * _CG['cos_lat']
            dy = (LAT - ed['lat']) * 110.57
            rr = np.hypot(dx, dy) / ed['R']
            pe += ed['amp'] * np.exp(-rr * rr)
        env['pEnv'] = pe.astype(np.float32)
    else:
        # 轮次 158/159: 两项都只依赖 LAT(常量网格), 合起来就是常量基态 ——
        # **必须 `.copy()`**: 下面 7 次 `+=`(副高)、2 次 `-=`(季风槽)与涡旋项都是
        # 就地运算, 直接拿缓存对象会把它改坏(第二次调用起 pEnv 全错)。
        # 逐位安全: copy() 不做任何数值运算, 位模式与"现场重算"完全相同。
        pe = _CG['pe_base'].copy()
        _RGC = _ridge_grids()                # 轮次 159: 副高 (dy, d) 常量网格
        # S11：副高强度的**可调旋钮**（"距平口径"里唯一的手调量）。
        # ⚠ **默认 1.0 = 保持现行幅度、逐位不变** —— `ramp * 1.0 * k` 在 IEEE-754 下
        #   与 `ramp * k` 位级相同，所以 test_simcore_ridge 的
        #   `test_p_env_matches_rebuild_bit_for_bit`（逐位重建 pEnv）继续成立，
        #   也避免引入"标定用一套、模式用另一套"。
        # **沙盒的 0.85**（`s9_sandbox_v2.md:167` 的 `subtropical_high_strength = 0.85`，
        #   配 `RIDGE_HEIGHT_M = 45.0` ⇒ +38 m 脊距平）**作为一个可选设定**保留：
        #   想照抄沙盒就传 `ridgeAnom=0.85`（实测：峰值距平 8.48 hPa、面积 19409 万 km²；
        #   0.0 可整体关掉、1.5 可加强 ⇒ 强度连续可调，见 s11_ridge_anomaly.md 实验①）。
        _ridge_str = float(P.get('ridgeAnom', 1.0))
        for _i, (rlat, rlon, ramp) in enumerate(RIDGES):
            _dy, d = _RGC[_i]
            # 轮次 158: 内联算式 → `ridge.ridge_core`(四向伸展各自独立可调)。
            # **默认参数下逐位一致**: 原式是 `-(dy_r²)-(dx_shw²)-(dx_she²)`,
            # `ridge_core` 是 `-(dy_n²)-(dy_s²)-(dx_w²)-(dx_e²)`; 默认
            # shExtN=shExtS=5.5 时 dy_n/dy_s 恒有一个恰好 0.0(而 `a-0.0 == a` 在
            # IEEE-754 下精确), shExtW=22/shExtE=12 与原常数逐字相同, 且加法结合
            # 顺序逐项对齐 ⇒ 送进 np.exp 的输入位级相同。详见 ridge.py 的 docstring。
            # 参数沿用仓库习惯「`P.get(key, 常量)`」, **不进 init 的默认字典**。
            # 轮次 159: `LAT-rlat` 与 `d = (LON-rlon+180)%360-180` 改从 `_ridge_grids()`
            #   取常量网格(这是本函数里最贵的单算子: `np.mod` 实测 ≈7.4ms/次 ×7 中心);
            #   数组只读 —— 已用测试断言 `ridge_core` 不会就地改入参。
            # ⚠ 这里**试过**按键缓存整个核(剖析显示 ridge_core 占 14%), 但那次交错
            #   A/B 给出 0.99x(无收益) ⇒ 已撤回。
            #   ⚠⚠ **那次结论已作废, 不能当"缓存没收益"的依据**(verifier 审计
            #   DOC_AUDIT_R162.md 指出这是 doc→code→doc 循环引用): 当时的"旁路"是
            #   **假旁路**(只 `np.array.copy()` 复用缓存值, 根本没强制重算), 所以两臂
            #   其实都在用缓存; 而且机器 A/A 极差 72-76% ⇒ 与 1.0 不可分。
            #   **是否值得缓存这件事仍是开放的**, 要用**真旁路**复测(见 PERF_MEMO_PLAN 的 M3)。
            #   当前撤回到"算常量网格"这一层, 是因为它**可证明**只消除冗余、不依赖计时。
            pe += (ramp * _ridge_str) * _RG.ridge_core(_dy, d, P)
        # 轮次 158: 两项都只随**月份**变(经度项是常量), 取自 _month_bands 的缓存。
        # 写成两次就地 `-=` 是为了与原式的运算顺序逐位一致。
        pe -= _B['mt'][0]
        pe -= _B['mt'][1]
        for ed in sim['eddies']:
            # 轮次 159: `np.cos(LAT*C['DEG'])` → 常量网格
            dx = (LON - ed['lon']) * 111.32 * _CG['cos_lat']
            dy = (LAT - ed['lat']) * 110.57
            rr = np.hypot(dx, dy) / ed['R']
            pe += ed['amp'] * np.exp(-rr * rr)
        env['pEnv'] = pe.astype(np.float32)

    # 地转风 + 摩擦; 200hPa 近似; 辐合(有限差分)
    # 轮次 159: cos_lat / dx_m / f / f_eff 都只依赖 LAT(常量网格); 下面用的是
    # `rho_f_eff`(= 原式 `C['RHO'] * f_eff`, 每步原本算两次)。
    cos_lat = _CG['cos_lat']
    dx_m = _CG['dx_m']
    dy_m = C['ENVD'] * 110.57
    # 500hPa 位势高度：副高（5880 gpm）判定用。来自真实 ERA5 `hgt500`（气候态或 1990-92）。
    # 取不到就不建这个键 —— `draw_ridge` 会据此**不画**并标注原因，而不是硬编一个假阈值。
    if DATA is not None and 'z500' in DATA:
        env['z500'] = np.asarray(DATA['z500'], np.float32)
        # ── S11：副高的**物理量** = **高度距平**（用户 2026-10-05 裁定"并存"）──────
        # 物理用距平、显示画 5880。理由：5880 是**绝对阈值** —— 全球每升温 1°C，
        # 500hPa 面抬升几十米 ⇒ 底场一抬高"副高"就自己膨胀（未来情景 `sim_typhoon/2100`
        # 直接失真）；而"**比常年高 X 米**"与底场无关 ⇒ **可外推、强度可连续调**。
        # 基准 = 真实 500hPa 气候态。⚠ `reallib/hgt500_clim.npz` 目前 **n_years=1**
        #   ⇒ 它只是**一年的年循环**，不是 30 年气候态（见该文件 meta 的 warning）；
        #   回填更多年份后本判据自然变准，代码不用改。
        _zb = env.pop('z500Base', None)
        if _zb is not None:
            env['z500Anom'] = (env['z500'] - np.asarray(_zb, np.float32)).astype(np.float32)
    pe = env['pEnv']
    dpdx = np.empty_like(pe)
    dpdx[:, 1:-1] = (pe[:, 2:] - pe[:, :-2]) / (2.0 * dx_m[:, 1:-1])
    dpdx[:, 0] = (pe[:, 1] - pe[:, 0]) / dx_m[:, 0]
    dpdx[:, -1] = (pe[:, -1] - pe[:, -2]) / dx_m[:, -1]
    dpdy = np.empty_like(pe)
    dpdy[1:-1, :] = (pe[2:, :] - pe[:-2, :]) / (2.0 * dy_m)
    dpdy[0, :] = (pe[1, :] - pe[0, :]) / dy_m
    dpdy[-1, :] = (pe[-1, :] - pe[-2, :]) / dy_m
    # 轮次 159: `C['RHO'] * f_eff` 也是常量网格(原式在下面出现两次)
    ug = -0.1 * dpdy / _CG['rho_f_eff']
    vg = 0.1 * dpdx / _CG['rho_f_eff']
    sp = np.hypot(ug, vg)
    ang = 0.30
    c = math.cos(ang) * 0.88
    s = math.sin(ang) * 0.88
    uf = ug * c - vg * s
    vf = ug * s + vg * c
    # 数据模式: 真实引导(uv_steer) + 解析地转补充(完整经向结构/天气涡旋) + 扰动
    # F7: 海洋中尺度涡 —— SST 与 D26 各自独立实现(冷核/暖核, 西传)。
    # 只在数据模式叠加: 解析兜底路径的契约是"平滑气候态"(测试也据此校验
    # env[sst] 与 sst_clim 的一致性), 混进中尺度涡会破坏那条契约。
    if DATA is not None:
        if sim.get('_precQual', 1):
            env['sst'] = (env['sst'] + mesoscale_eddies(sim, 'eddy_sst', 10, 0.18,
                                                        280.0)).astype(np.float32)
    if DATA is not None and 'u850' in DATA:
        # 解析地转(含完整南北分量与副高/季风槽结构)
        ua = np.where(sp > 0.3, uf, 0.0) * KT
        va = np.where(sp > 0.3, vf, 0.0) * KT
        # 真实引导(uv_steer)保留大尺度方向; 解析补充补齐结构缺失分量
        g = _STEER_DATA_W                    # 真实引导权重(可用标定旋钮调, 见其定义处)
        u850 = g * DATA['u850'].copy() + (1 - g) * ua + P['steerU'] * KT * 0.5
        v850 = g * DATA['v850'].copy() + (1 - g) * va + P['steerV'] * KT * 0.5
        env['u850'] = u850.astype(np.float32)
        env['v850'] = v850.astype(np.float32)
        # 高空/中层改用气候库**真实**的风(原先由 850 风乘系数合成, 与切变互为因果),
        # 这样风切就能真正由高低空风场算出来, 而不是另立一条独立公式
        env['u200'] = np.asarray(DATA['u200'], dtype=np.float32)
        env['v200'] = np.asarray(DATA['v200'], dtype=np.float32)
        # ── **瞬变槽脊引导**(轮次 102 新增, 见 `synoptic_steer` 的说明) ──
        # 必须加在算 uMid 与切变**之前**: 异常是正压的(四层同加), 所以
        # u200-u850 不变 ⇒ 风切的方向/大小都不受影响, 只有引导被改变。
        _su, _sv = synoptic_steer(sim)
        if _su is None:
            _su = _sv = 0.0
        env['u850'] = (u850 + _su).astype(np.float32)
        env['v850'] = (v850 + _sv).astype(np.float32)
        env['u200'] = (env['u200'] + _su).astype(np.float32)
        env['v200'] = (env['v200'] + _sv).astype(np.float32)
        # 轮次 158: 西风槽 + 冷空气。**位置是关键**: 必须放在四层风场都建好之后、
        # uMid 与切变推导之前 —— 冷空气只加在低层, 靠 `u200-u850` 在下面自然反馈
        # 进切变(见 synoptic.apply_to_env 的 docstring)。关闭时纯 no-op。
        _SY.apply_to_env(env, sim)
        # step ⑧: **先**加天气尺度扰动，再推导 uMid/切变（默认关 ⇒ None ⇒ 逐位不变）
        _ss = steer_systems(sim, env['u850'].shape, t)
        if _ss is not None:
            env['u850'] = (env['u850'] + _ss[0]).astype(np.float32)
            env['v850'] = (env['v850'] + _ss[1]).astype(np.float32)
            env['u200'] = (env['u200'] + _ss[0]).astype(np.float32)
            env['v200'] = (env['v200'] + _ss[1]).astype(np.float32)
        _sp = synoptic_perturb(sim, env['u850'].shape, t)
        if _sp is not None:
            env['u850'] = (env['u850'] + _sp[0]).astype(np.float32)
            env['v850'] = (env['v850'] + _sp[1]).astype(np.float32)
            env['u200'] = (env['u200'] + _sp[0]).astype(np.float32)
            env['v200'] = (env['v200'] + _sp[1]).astype(np.float32)
        u850 = env['u850']
        v850 = env['v850']
        env['uMid'] = (0.45 * u850 + 0.55 * env['u200']).astype(np.float32)
        env['vMid'] = (0.45 * v850 + 0.55 * env['v200']).astype(np.float32)
        # 真实垂直风切变(kt) = |V200 - V850|
        # F8: 先低通**矢量分量**, 再由矢量导出的模作为 |shear|。
        # 不能各自独立: lowpass(|S|) ≠ |lowpass(S)|(Jensen), 实测最大差 7.5 kt,
        # 会让"大小"和"方向"两处用的不是同一个切变, 自相矛盾。
        su = _lowpass(env['u200'] - u850, _SHEAR_LP).astype(np.float32)
        sv = _lowpass(env['v200'] - v850, _SHEAR_LP).astype(np.float32)
        # **大小用 DATA['shear'], 方向用 u200-u850**(2026-09-19 修)。
        # DATA['shear'] 已经在 envmodel.solver 里按日尺度口径标定过(月均矢量差
        # 偏大 ~1.8 倍), 而这里的 hypot(u200-u850) 直接用未标定的基态风场算,
        # 实测把标定好的 18kt 均值又顶回 37.7kt、最大 125.9kt(西风带直接饱和,
        # 热带也被压死)。矢量方向仍取真实高低空风差, 保持 sh_u/sh_v 自洽。
        if 'shear' in DATA:
            _m0 = np.hypot(su, sv) + 1e-6
            _tgt = np.asarray(DATA['shear'], dtype=np.float32)
            if _tgt.shape == su.shape:
                su = (su / _m0 * _tgt).astype(np.float32)
                sv = (sv / _m0 * _tgt).astype(np.float32)
        # 天气尺度 + 季节内(MJO)切变扰动。**必须在数据分支加**: 模拟走的是
        # dataMode, 切变由 u200-u850 算出, 之前只加在解析分支所以完全没生效
        # (实测加 MJO 前后输出字节数一模一样)。
        # 沿**现有切变方向**叠加, 这样只改大小不改方向, 矢量保持自洽。
        _syn = mesoscale_noise(sim, 'shear_syn', 6.0)
        _mjo = mjo_field(sim, 5.0)
        _pert = _syn if _mjo is None else (_syn + _mjo)
        _mag = np.hypot(su, sv) + 1e-6
        su = su + (su / _mag) * _pert
        sv = sv + (sv / _mag) * _pert
        env['sh_u'], env['sh_v'] = su.astype(np.float32), sv.astype(np.float32)
        env['shear'] = np.maximum(SHEAR_MIN_KT,
                                  np.hypot(su, sv)).astype(np.float32)
        # 中尺度随机扰动(随时间演化): 月气候态 + 涡状扰动 = 某一天的 SST/OHC
        if sim.get('_precQual', 1):
            env['sst'] = (env['sst']
                          + mesoscale_noise(sim, 'sst', 0.35)).astype(np.float32)
        # 随机项合成后统一低通 σ=1.5 格(0.75°): 把斑点压成相干结构
        env['sst'] = _lowpass(env['sst'], _LP_SIGMA).astype(np.float32)
        if 'ohc_kj' in (sim.get('ocn') or {}):
            sim['ocn']['ohc_kj'] = (sim['ocn']['ohc_kj']
                                    + (mesoscale_noise(sim, 'ohc', 3.0)
                                       if sim.get('_precQual', 1) else 0.0)
                                    ).astype(np.float32)
        enso_update(sim)                     # ENSO 振子 + 遥相关
        # 副高脊线(逐经度, 南北半球各一份): 供 `sh_pole_steer` 的引导修正用。
        # 放在最后, 因为要用**已经加完瞬变槽脊**的 u850 —— 脊线诊断本身已做 +-20 度
        # 滑动平均, 瞬变不会把脊轴挪走, 但用最终场才与"模式看到的风"自洽。
        try:
            _rn, _rs = sh_ridge_lat(sim)
            env['shRidgeN'] = _rn.astype(np.float32)
            env['shRidgeS'] = _rs.astype(np.float32)
        except Exception:
            logger.debug('副高脊线诊断失败', exc_info=True)
    else:
        # 地转风 → kt(全程序风速统一 kt)
        u850 = np.where(sp > 0.3, uf, 0.0) * KT + P['steerU'] * 0.5 * KT
        v850 = np.where(sp > 0.3, vf, 0.0) * KT + P['steerV'] * 0.5 * KT
        env['u850'] = u850.astype(np.float32)
        env['v850'] = v850.astype(np.float32)
        # 轮次 158: 西风槽 + 冷空气(默认全关 = 纯 no-op)。放在 u200/uMid **合成之前**,
        # 这样它们由已叠加过的 u850 重新合成(否则改了个不被下游读到的数组)。
        # ⚠ 必须把本地 `u850/v850` **取回**: `apply_to_env` 是就地 rebind, 而下面
        # 1700+ 行的 du/dv 梯度用的是**本地变量**; 不取回会继续用旧数组(角动量/散度
        # 看不到叠加)。关闭时 rebind 不发生, 取回的是同一个对象 ⇒ 逐位不变。
        _SY.apply_to_env(env, sim)
        u850 = env['u850']
        v850 = env['v850']
        sh_u = env['shear'] * 0.9
        sh_v = env['shear'] * 0.28
        env['sh_u'] = sh_u.astype(np.float32)
        env['sh_v'] = sh_v.astype(np.float32)
        env['div200'] = np.full(sh_u.shape, 0.0, np.float32)
        env['coldSurge'] = np.zeros(sh_u.shape, np.float32)
        env['u200'] = (u850 * 1.45 + sh_u).astype(np.float32)
        env['v200'] = (v850 * 1.45 + sh_v).astype(np.float32)
        # step ⑧: 解析支同样叠加（默认关 ⇒ None ⇒ 逐位不变）
        _ss = steer_systems(sim, env['u850'].shape, t)
        if _ss is not None:
            env['u850'] = (env['u850'] + _ss[0]).astype(np.float32)
            env['v850'] = (env['v850'] + _ss[1]).astype(np.float32)
            env['u200'] = (env['u200'] + _ss[0]).astype(np.float32)
            env['v200'] = (env['v200'] + _ss[1]).astype(np.float32)
        _sp = synoptic_perturb(sim, env['u850'].shape, t)
        if _sp is not None:
            env['u850'] = (env['u850'] + _sp[0]).astype(np.float32)
            env['v850'] = (env['v850'] + _sp[1]).astype(np.float32)
            env['u200'] = (env['u200'] + _sp[0]).astype(np.float32)
            env['v200'] = (env['v200'] + _sp[1]).astype(np.float32)
        u850 = env['u850']
        v850 = env['v850']
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

    # F8: **出流场**。原来 outflow 是个标量参数(0.5), 但出流顺畅与否是空间的:
    # 高空辐散强的区域(副热带急流的反气旋侧、暖池上空的暖性高压)有利于把
    # 台风抽上来的质量排出去, 辐合区则把出流堵住。
    # 这里直接用真实的 200hPa 风算散度: div200 = ∂u/∂x + ∂v/∂y。
    if 'u200' in env and 'v200' in env:
        _u = np.asarray(env['u200'], np.float64)
        _v = np.asarray(env['v200'], np.float64)
        # 轮次 159: 原式 `111.32e3 * cos_lat * C['ENVD']` —— 常量网格(顺序未变)
        _dx = _CG['dx_m3']
        _dy = 110.57e3 * C['ENVD']
        _du = np.gradient(_u, axis=1) / _dx
        _dv = np.gradient(_v, axis=0) / _dy
        # 归一: 典型热带 200hPa 散度量级 1e-6 ~ 1e-5 s^-1
        env['div200'] = np.tanh((_du + _dv) / 4.0e-6).astype(np.float32)

    # F9: **冷高压(cold surge)**。冷空气入侵(大陆冷高压南下)时, 冷高与台风
    # 之间的气压梯度极强, 会把危险半圆的风进一步加速 —— Megi(2010) 危险半圆
    # FL192 vs 安全半圆 FL154(常态差 ~25kt), 冷空气参与时可达 35kt 以上。
    # 这里由**真实 mslp 场的气压梯度**驱动 + 冷空气条件(海温偏低)+ 冷季调制:
    #   1) 梯度越强, 冷高越"实"
    #   2) 海温低说明下面是冷空气垫(暖洋面上不会有冷平流)
    #   3) 北半球冷季(11-3 月)、南半球对称
    if 'mslp' in env and 'sst' in env:
        _P = np.asarray(env['mslp'], np.float64)
        # 轮次 159: 原式 `111.32 * cos_lat * C['ENVD']` —— 常量网格(顺序未变)
        _gx = np.gradient(_P, axis=1) / _CG['dx_m2']
        _gy = np.gradient(_P, axis=0) / (110.57 * C['ENVD'])
        _grad = np.hypot(_gx, _gy) * 100.0          # hPa / 100km
        # 注意: clamp() 是**标量**函数(用 Python 比较), 数组必须用 np.clip
        _cold = np.clip((27.0 - np.asarray(env['sst'], np.float64)) / 4.0, 0.0, 1.0)
        # 季节钟形: 北半球 1 月最强, 南半球 7 月最强
        # 轮次 159: 只随整数月变(原式 `_mo = float(sim['month'])` + np.where + clip**1.5
        # 见 _month_bands), 改从月度缓存取 —— 键含月份, 逐位相同。
        _seas = _B['seas']
        env['coldSurge'] = (np.clip((_grad - 0.8) / 2.5, 0.0, 1.0)
                            * _cold * _seas).astype(np.float32)

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
    # ── 显示用气压(2026-09-19 二次修正) ──
    # 用户反馈两轮: ①"抖动太大、不真实"(原 pEnv 只有 std 2.4hPa 的平坦异常场,
    # 等值线稍微一动就整片游走); ②"赤道附近有几条横贯地球的等压线、放大后
    # 歪歪扭扭" —— 那是我上一版**自造的纬向剖面**造成的(纬向剖面只有纬度依赖,
    # 等值线必然是绕地球一圈的直线)。
    # 现在: 直接用模型给的**真实二维** mslp 气候场(它是有槽脊结构的), 只做
    #   · 轻度空间平滑(抹掉 1-2 格的锯齿, 放大后不再"歪歪扭扭")
    #   · 时间平滑(SLP 记忆长, 6h 一步的模型噪声不该直接进画面)
    # **引导气流仍用 pEnv** —— 那条路径按实测路径回归标定过, 不在这里动。
    try:
        _base = np.asarray(env['pEnv'], dtype=np.float32)
        # 不再放大: 环境模型修好之后 pEnv 本身就是真实二维 mslp(min 983 最大
        # 1030、std 6.4hPa), 再乘系数只会造出 911/1070 这种假极值
        _anom = _base - float(np.mean(_base))
        try:
            from scipy.ndimage import gaussian_filter as _gf
            _anom = _gf(_anom, sigma=1.2, mode=('nearest', 'wrap')).astype(np.float32)
        except Exception:
            pass
        _prev = sim.get('_pDispAnom')
        if isinstance(_prev, np.ndarray) and _prev.shape == _anom.shape:
            _anom = _prev * 0.5 + _anom * 0.5          # 时间平滑(半衰期 ≈1.5 步)
        sim['_pDispAnom'] = _anom.astype(np.float32)
        env['pDisp'] = (1013.0 + _anom).astype(np.float32)
    except Exception:
        logger.debug('显示气压构造失败(退回 pEnv)', exc_info=True)
        env['pDisp'] = env['pEnv']
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
        # 模型场自己报的降级字段(合并时用旧库/解析补上的)也算 partial
        miss += [k for k in DATA.get('_degraded', []) if k not in miss]
        if miss:
            sim['dataState'] = 'partial'
            sim['dataWarn'] = '数据驱动: 缺少 ' + ', '.join(miss) + ', 已用解析场补齐'
        else:
            sim['dataState'] = 'loaded'
            sim['dataWarn'] = ''
    # **数据是瞬时还是月均兜底**(用户 2026-09-20: "一切数据要用瞬时"): 供 UI 徽标如实显示,
    # 免得"看着是数据环境、其实走的是月气候态"。
    try:
        if DATA is not None and DATA.get('_instant'):
            sim['dataSrc'] = '瞬时'
            sim['dataSrcDetail'] = str(DATA.get('_instant_src') or '')
        elif DATA is not None and DATA.get('_instant_partial'):
            # 只有部分层是瞬时(OHC 走 ±3 天窗口, 而 ERA5 单日只覆盖 ±6 小时)
            _g = DATA.get('_instant_groups') or {}
            _have = [k for k, v in _g.items() if v]
            sim['dataSrc'] = '瞬时·部分'
            sim['dataSrcDetail'] = ('瞬时层: ' + '+'.join(_have) +
                                    '；其余月均。' + str(DATA.get('_instant_src') or ''))
        elif DATA is not None:
            sim['dataSrc'] = '月均'
            sim['dataSrcDetail'] = ''
        else:
            sim['dataSrc'] = '-'
            sim['dataSrcDetail'] = ''
    except Exception:
        sim['dataSrc'] = '-'
    sim['lastEnvH'] = sim['t']


# ── 环境采样 / 引导气流 / β漂移 ──

def env_update(sim) -> None:
    """**公开入口**(= `_env_update_impl`); 模拟自身的环境刷新只走这里。

    **2026-09-23(轮次 107)把实现拆出来**的原因: 集合预报要用**未来时刻**的环境,
    必须重建环境场(`forecast_env_series`), 但那些重建**不是模拟自身的环境刷新**。
    而 `tests/test_sim_time_model.py::test_refresh_count_matches_step` 用
    monkeypatch 统计的正是后者 —— 不拆的话, 预报的内部重建会把"环境刷新率"
    这类契约测成假的(实测 48h/1h 档从应有的 ~48 次变成 **276 次**)。
    拆分后: 预报调 `_env_update_impl`, 其余一切照旧走 `env_update`。
    """
    _env_update_impl(sim)


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
    out = {'sst': sst, 'ohc': ohc, 'shear': shear, 'rh': rh,
           'land': clamp(lf, 0, 1), 'lat': lat}
    # **t200 必须带上**(2026-09-21): 阶段判定 phase.classify 要用 SST-200hPa 温差判暖心,
    # 缺了它暖心度按中性 0.5 处理 → 判成"弱暖心" → 每个新生系统第一步就被打成 SD/SS,
    # TD/TS/TY 整段阶梯被吃掉(实测序列 DB/20 → SD/20 → SD/25 …)。这是那串阶段问题的真根因。
    if 't200' in env:
        out['t200'] = env_bilinear(env['t200'], lon, lat)
    return out


#: 引导采样环带(km)与权重: 近环主导, 外环只做平滑
_STEER_RINGS = ((200.0, 1.00), (400.0, 0.80), (600.0, 0.58))
#: **数据模式下引导风里"真实基态"的权重**(轮次 102 提出来做可标定旋钮):
#:   u850 = W x DATA['u850'] + (1-W) x 解析地转(pEnv)
#: 原来写死 0.55。为什么要有这个旋钮: 数据模式的 DATA['u850'] 已经是 ERA5 训练
#: 产物(基态+模态+环流型), 而解析地转是它的**低质量重复估计**; 用 0.45 的权重把
#: 两者平均, 等价于把真实引导往一个粗糙副本上拉。`tools/probe_geo_blend.py` 实测
#: 35N 上被拉掉 2-3kt, `tools/calib_steer.py --g` 用观测路径定这个数。
_STEER_DATA_W = 0.55
#: 每个环带的方位采样数(8 = 45 度间隔)
_STEER_AZIMUTHS = 8
#: 24 个采样点相对台风中心的偏移(经度偏移需再除以 cos(lat)), 预计算避免每步三角函数
_STEER_OFF_LON = np.array([r * math.cos(k * (2.0 * math.pi / _STEER_AZIMUTHS) + 0.4) / 111.32
                           for r, _rw in _STEER_RINGS for k in range(_STEER_AZIMUTHS)])
_STEER_OFF_LAT = np.array([r * math.sin(k * (2.0 * math.pi / _STEER_AZIMUTHS) + 0.4) / 110.57
                           for r, _rw in _STEER_RINGS for k in range(_STEER_AZIMUTHS)])
_STEER_RING_W = np.array([rw for _r, rw in _STEER_RINGS
                          for _k in range(_STEER_AZIMUTHS)])


def steering_weights(vmax: float, lat: float, eff850=None):
    """三层引导权重 (浅层850, 中层, 深层200), 和为 1。

    **2026-09-22(轮次 102)重标定 —— 推翻 2026-09-18 的旧结论。**
    旧结论是 "|lat|>26° 时深层权重 = 1.0", 它的标定表里那一行的 **RMSE 是 94.08 kt**;
    台风实际移速只有 0~20kt, 94kt 的 RMSE 说明那一行的回归数据是坏的, 不该采信。
    用 `tools/probe_steer_obs.py` + `tools/calib_steer.py`(把观测路径喂给模型自己的
    分层引导采样, 1739 个 6h 步, 西太 6-11 月, 2010-2024):

        |lat|    目标(观测移速-β) u/v | 浅层850 u/v     | 深层200 u/v     | 现行RMSE | 最优eff850
        0-10N    -4.83 / +1.09      | -2.82 / +1.59  | -15.88 / -5.11 |    8.12  | 0.65
        10-20N   -4.14 / +0.44      | -0.18 / +2.17  |  -9.35 / -3.93 |    8.35  | 0.60
        20-30N   +2.15 / +2.28      | -2.15 / +2.62  |  -5.47 / -5.16 |   11.41  | 0.80
        30-40N  +13.14 / +4.56      | +0.95 / +3.34  | +13.94 / -4.23 |   19.09  | 0.55
        40-60N  +13.11 / +8.51      | +5.29 / +2.39  | +42.54 / -2.64 |   36.00  | 0.80

    最关键的一行是 30-40N: 观测里台风仍以 **+4.6 ~ +9.0 kt 向极**移动(典型转向),
    而 200hPa 的**气候平均经向风是 -4.2 kt(向赤道)** —— 那是副热带急流及其南侧
    东风带, **不是引导气流**。给它 1.0 的权重 ⇒ 台风一到 30N 就被按回去
    (实测模型在 30-40N 给出 +26.4kt 纯东向、向极 -0.1), 于是"一生最高纬度"中位
    只有 16-18 度、EX 一个都出不来、强端也起不来 —— 三件事是同一个机制。

    全局常数 eff850=0.65 的总 RMSE 是 **10.06**(现行 12.90); **逐带最优也只是 9.9**。
    既然纬度变式并不比常数好, 就取常数(平滑、无带边界跳变、少一个自由参数)。
    残余的向极亏损(-1.2 ~ -4.6 kt 随纬度增大)不是权重能补的, 那是**瞬变槽脊**
    的贡献 —— 月平均气候态本身产生不了转向所需的经向风。

    **S14 / task-70（2026-10-05）：把"强度 ⇒ 权重"这一维也标了 —— 结论同样是"不改"。**
    同一套 `tools/calib_steer.py`（`parse_track`/`rmse`/`grid`/`_ring_avg` 原样复用），
    只把 rows 换成**同时带回 vmax** 的版本，按强度分箱（本次样本 **2524 步**；
    ⚠ 与上面记的 1739 步**不同**，见下）。逐箱**箱内最优 vs 现行常数**：

        | 强度档 kt | n    | 现行常数 RMSE | 箱内最优 RMSE | 箱内最优 eff(850/200) |
        | 0-34      | 1016 | 12.569       | 12.524       | 0.70 / 0.30 |
        | 34-50     |  731 | 13.553       | 13.539       | 0.62 / 0.38 |
        | 50-65     |  308 | 14.910       | 14.901       | 0.63 / 0.37 |
        | 65-85     |  211 | 13.434       | 13.392       | 0.59 / 0.41 |
        | 85-105    |  136 | 12.367       | 12.054       | 0.49 / 0.51 |
        | 105+      |  122 | 11.164       | 11.155       | 0.62 / 0.38 |

    · **样本加权总 RMSE：常数 13.163 → 逐强度箱最优 13.121，改善 0.042（0.32%）**。
      比**纬度维**那次（10.06 → 9.90，改善 1.6%）还小 ⇒ **不值得多一个自由度** ⇒ **取常数**。
    · **没有单调趋势**：`eff850` = 0.70/0.62/0.63/0.59/**0.49**/0.62 —— 只有 85-105 那箱
      偏低（也正是"变强⇒偏高层"该有的方向），但**非单调、且下一箱又回去了** ⇒
      用户认可的"加强中的台风会转向"**在这批观测里没有得到支持**。
    · ⚠ **本函数的三权重是"退化"的**：`mid = 0.6·sh + 0.4·dp` 是另两层的**固定线性组合**
      ⇒ 真正起作用的只有 `eff850 = ws + 0.6·wm` 与 `eff200 = wd + 0.4·wm`
      （全局最优 `(0.2,0.75,0.05)` 的 eff = **(0.65, 0.35)**，与现行常数的 eff 相同、
      RMSE 同为 13.163）⇒ **"三层权重"实际只有 1 个自由度**。
      今后要动就动 `eff`，别去动三者的拆分。
    · ⚠ **绝对 RMSE 与上面记的 10.06 不可比**：本次收集到 **2524** 步而上面记 1739 步
      （口径/数据版本不同，未查明）。但**上面所有比较都是同一样本内的相对比较**，不受影响。
    · ⇒ **"强度 ⇒ 引导权重"这一维：有证据地不改**。`vmax`/`lat` 两个形参**保留但注明未使用**
      （删掉会改掉 `steering_at()` 的调用签名与既有测试）。
    """
    # eff850 = ws + 0.6*wm = 0.50 + 0.15 = 0.65;  eff200 = wd + 0.4*wm = 0.25 + 0.10 = 0.35
    #
    # ── step ⑦（Lead 裁定）：把 eff850 做成 `params['steerEff850']`（**默认 0.65 ⇒ 逐位不变**）──
    # 动机（用户【1】）：**"北半球所有风暴都是西北行，几乎没有其它路径"**。v5 实测（交付数据）：
    #   北半球低纬 W+NW 91.0% —— β split 落地后**基本没动**（v3 是 90.5%）⇒
    #   **"全是西北行"不是 β 驱动的，是 850hPa 信风东风驱动的** ⇒ 本步降 850 的有效权重，
    #   让 30° 以北**更早吃到东向高层风** ✓
    # `eff850 = ws + 0.6*wm`（唯一自由度）；固定 `wm = 0.25` ⇒ ws = eff850-0.15、wd = 1-ws-wm
    #   ⇒ eff850=0.65 复原现行的 (0.50, 0.25, 0.25) ✓
    # ── §46/§47：**默认由 0.65 改为 0.80（Lead 裁定采纳 B1）** ──────────────────────
    # 依据（同 seed 90 天、报点数口径、**每条轴都更好、无一项变差**）：
    #   方向  W+NW 95.8→90.1 | N+NE+E 3.9→8.9 | S+SW+SE 0.2→1.0   （全部朝目标方向）
    #   存活  ≥30° 36.8→**43.0**（+6.2）| ≥35° 26.4→**37.0**（+10.6）
    #   EX    21→**23**（中位持续报点数 14）
    # 机制：`steering_layers` 的 **200 层 |u| 是 850 的两倍**（10.15 vs 5.10）⇒ 它把 `v/|u|`
    #   压小 ⇒ 方向被钉进 W/NW；**提高 850 权重 = 降 200 权重** ⇒ 方向放开 ✓
    # ⚠ 只改**层权重** ⇒ 不改寿命/强度分布的口径 ⇒ 符合用户【1.1】的界 ✓
    # ⚠ 旋钮仍可回调：传 `steerEff850=0.65` 即完全复原历史行为 ✓
    # §60：**按纬度分带**（`params['steerEff850Lat']` = [[lat_max, eff850], ...]）
    # 默认为 None ⇒ **完全回落旧行为（常数 0.80）⇒ 逐位不变** ✓
    _band = None
    if isinstance(eff850, (list, tuple)) and eff850 and isinstance(eff850[0], (list, tuple)):
        _band = eff850
    if _band is not None:
        _al = abs(float(lat))
        _sel = float(_band[-1][1])
        for _lm, _lv in _band:
            if _al < float(_lm):
                _sel = float(_lv)
                break
        _e = _sel
    else:
        _e = 0.80 if eff850 is None else float(eff850)
    _e = min(0.95, max(0.0, _e))
    _wm = 0.25
    _ws = max(0.0, _e - 0.6 * _wm)
    _wd = max(0.0, 1.0 - _ws - _wm)
    _tot = _ws + _wm + _wd
    if _tot > 0 and abs(_tot - 1.0) > 1e-9:      # 归一（防极端输入）
        _ws, _wm, _wd = _ws / _tot, _wm / _tot, _wd / _tot
    if eff850 is not None:
        return _ws, _wm, _wd
    #
    # ⚠⚠ S12-① 结论（Lead 2026-10-05 要求写进注释，防止后人再踩）：
    #   · **不要**照抄沙盒 `s9_sandbox_v2.md` 的 `shallow=(0.349,0.527,0.124)` /
    #     `deep=(0.186,0.527,0.287)` 变权重 —— 它的等价物正是本函数 docstring 里
    #     **已被 1739 个观测步标定推翻**的那版（旧"|lat|>26 ⇒ 深层=1.0"，那行
    #     标定 RMSE 94.08 kt = 坏数据）。照抄 = 回退到坏标定。
    #   · **形参 `vmax` / `lat` 目前未使用** —— 本函数是**常数**。这不代表"强度影响引导"
    #     这个物理不存在，而是：**纬度变式已被证否，"强度变式"从未被标定**。
    #   · 实测（135E/22N 真实三层风）：按沙盒式强度插值，20→90kt 航向会变 **−7.55°**
    #     （20kt −65.50° → 90kt −73.05°）⇒ "加强中的台风会转向"**可观测但当前未接**。
    #     若要做，**必须先用 `tools/calib_steer.py` 那套观测步重新标定**，不能凭沙盒常数硬接
    #     （见 output/exploration/s12c_steer_ridge.md）。
    return _ws, _wm, _wd


def sh_ridge_lat(sim):
    """**副高脊线纬度**(逐经度, 度) —— 从模式自用的 850hPa 风场直接诊断。

    定义: 脊轴 = 低层**东风转西风**的零线(u850 由负转正)。这是最直接、最不依赖
    额外数据的脊线定义, 而且**随季节与 ENSO 自动移动**(不需要写死月份表)。
    沿经度做 +-20 度滑动平均, 把天气尺度涡滤掉只留副高脊。

    做法要点(避免又一处"标定用一套、模式用另一套"):
      · 同一函数对南半球镜像(用 -u850), 返回的纬度都是**该半球的绝对纬度**;
      · 找不到零线(该经度没有副高脊)时返回 45 度 ⇒ 修正项自然失效。
    """
    u = np.asarray(sim['env']['u850'], dtype=np.float64)
    ny, nx = u.shape
    k = max(1, int(round(30.0 / C['ENVD'])))
    up = np.concatenate([u[:, -k:], u, u[:, :k]], axis=1)
    cs = np.cumsum(up, axis=1)
    cs = np.concatenate([np.zeros((ny, 1)), cs], axis=1)
    us = (cs[:, 2 * k + 1:] - cs[:, :-2 * k - 1]) / (2.0 * k + 1.0)
    LAT, _LON = _env_grids()
    latcol = np.asarray(LAT[:, 0], dtype=np.float64)
    # **必须用有符号纬度分别取南北半球的连续行** —— 先前用 abs(LAT) 配 searchsorted
    # 是错的(abs 先减后增, 不是有序数组), 于是取到的行段毫无意义, 脊线全是兜底值 45。
    def _axis(mask, flip):
        idx = np.where(mask)[0]
        la = (-latcol[idx]) if flip else latcol[idx]
        o = np.argsort(la)
        return idx[o], la[o]
    idx_n, ln = _axis((latcol >= 3.0) & (latcol <= 50.0), False)
    idx_s, ls = _axis((latcol <= -3.0) & (latcol >= -50.0), True)
    cols = np.arange(nx)

    def _cross(idx, la, sgn):
        """每个经度找**最南的那个 东风->西风 零线**(向量化; Python 逐列循环实测
        把 env_update 从 ~0.1s 拖到 0.4s, 一年下来多 10 分钟)。"""
        m = sgn * us[idx, :]
        n = m.shape[0]
        neg = m < 0.0
        has = neg.any(axis=0)
        a = np.where(has, (n - 1) - np.argmax(neg[::-1, :], axis=0), 0)
        aa = np.minimum(a, n - 2)
        c0 = m[aa, cols]
        c1 = m[aa + 1, cols]
        ok = has & (c1 >= 0.0)
        den = np.where(np.abs(c1 - c0) < 1e-12, 1e-12, c1 - c0)
        f = np.where(ok, -c0 / den, 0.0)
        return np.where(ok, la[aa] + f * (la[aa + 1] - la[aa]), np.nan)

    out_n = _cross(idx_n, ln, 1.0)
    out_s = _cross(idx_s, ls, -1.0)
    # 找不到零线的经度(例如陆地上被地形/季风槽淹没)用**同半球已找到者的中位**补,
    # 而不是随手兜一个 45 —— 兜底值会把修正项整段关掉, 变成"有些经度突然没有副高"。
    for arr in (out_n, out_s):
        med = float(np.nanmedian(arr)) if np.isfinite(arr).any() else 45.0
        arr[~np.isfinite(arr)] = med
    # ⚠⚠ 轮次 158: 兜底填充**之后**才施加形状(倾角 / 西端陡峭) —— 顺序反了会把
    # nan 参与进倾斜计算, 兜底那一步就补不回干净的脊线了。
    # 默认(shTilt=0 且西端幅度=0)时 `ridge_line_shape` **直接短路返回输入副本**,
    # 一行数值运算都不做 ⇒ 逐位不变(见 ridge.py 的不变量 2)。lon=None 走
    # `_lon_axis(size) = 0.25 + 0.5*i`, 与环境网格经度轴逐位一致。
    _P = sim.get('params')
    out_n = _RG.ridge_line_shape(out_n, params=_P, hemi=1)
    out_s = _RG.ridge_line_shape(out_s, params=_P, hemi=-1)
    return out_n, out_s


#: ── 副高驱动的**净向极**引导修正(轮次 103) ───────────────────────────────
#: 证据(`tools/calib_steer.py`, 观测路径位置上的模型分层引导 vs "观测移速-β"):
#:   纬度带        10-20N   20-30N   30-40N   40-60N
#:   观测目标 v    +0.38    +2.26    +4.60   +10.78
#:   模型 v        -0.28    -3.02    -7.80    -7.50
#:   **缺口**      +0.66    +5.28   +12.40   +18.28
#: 缺口随纬度**线性**增长(0.5 kt/度), 从约 14 度开始 —— 这正是"副高脊线以北进入
#: 西风带后应有的向极引导": 真实流场那里有稳定的西南气流, 而 200hPa 的气候平均是
#: 副热带急流的**向赤道**一侧(贡献 -4.2kt), 轮次 102 只把它的权重压到 35%, 负贡献仍在。
#: 缺口在 35N 附近到顶后**不再增大**(观测 40-60N 的缺口 7.5kt 与 30-40N 的 7.8kt 几乎一样),
#: 所以形式是"从脊线起 22.4 度开始、20 度内线性升到 8kt 后封顶", 而不是无界线性。
#: 用**局地脊线纬度**做参照, 修正项就会随季节/ENSO 平移。
_SH_POLE_MAX = 8.0         #: 修正上限(kt)
_SH_POLE_S = 22.4          #: 从脊线往**南**多少度开始有修正
_SH_POLE_W = 20.0          #: 到满值所需的纬度跨度


def sh_pole_steer(sim, lon: float, lat: float):
    """副高驱动的**净向极**引导修正(kt, 单值, 直接加在引导的经向分量上)。

    返回 (du, dv): 目前 du 恒为 0 —— 纬向缺口(20-40N 约 +4~6kt)是**另一个**问题,
    一次只改一处, 留着单独标定。

    **轮次 120 起三个参数可由 `sim['params']` 覆盖**(`shPoleS` / `shPoleW` /
    `shPoleMax`), 默认值与历史一致。为什么要这样做: 这三个数决定的是**经向速度剖面**,
    而它同时牵动 A(最高纬/净向极)、C(寿命/峰值)两套目标 —— 必须能在**不改代码**的
    前提下扫参, 否则"扫参"就变成"一边跑一边改源码"(会污染正在跑的验收)。
    覆盖是走同一条通路的: `steering_layers` 与 `tools/calib_steer.py`、
    `tools/probe_v_profile.py` 都调这个函数, 所以口径自动一致(规则 14)。
    """
    env = sim['env']
    arr = env.get('shRidgeN') if lat >= 0 else env.get('shRidgeS')
    if arr is None:
        return 0.0, 0.0
    nx = arr.size
    x = (lon % 360.0) / (360.0 / nx) - 0.5
    i0 = int(math.floor(x))
    f = x - i0
    i0 %= nx
    i1 = (i0 + 1) % nx
    rl = float(arr[i0]) * (1.0 - f) + float(arr[i1]) * f
    P = sim['params']
    _s = float(P.get('shPoleS', _SH_POLE_S))
    _w = max(1e-6, float(P.get('shPoleW', _SH_POLE_W)))
    _mx = float(P.get('shPoleMax', _SH_POLE_MAX))
    d = abs(lat) - rl
    # ── 形式②: **带通(高斯)** —— 轮次 120 扫参证明需要的形状是"带通"而不是"单调升" ──
    # 实测(100 天北半球窗口, 逐风暴步加权, 观测/模型, kt):
    #     参数组            10-20N   20-30N   30-40N   40-60N
    #     观测               3.1      5.3      9.7      9.1
    #     base(22.4/20/8)    5.8      9.7     13.2     15.2   <- 低纬就早推了
    #     s8m5(-8/6/5)       3.2      5.2      6.3      8.2   <- 30-40N 又推不够
    #     p8m6(+8/8/6)       4.0      7.0     10.3     12.4   <- 20-30N 与 40-60N 都过头
    #   ⇒ 要的是 **20-30N 几乎不推、30-40N 推 ~4kt、40-60N 再收回** —— 单调斜坡做不到,
    #   高斯在 d(相对脊线的纬度差) 上可以, 而且**照样跟着脊线随季节/ENSO 平移**。
    #   形式① (线性斜坡) 仍是默认, 直到高斯型通过多种子验收(见 NEXT_PLAN 判据表)。
    if str(P.get('shPoleForm', 'ramp')) == 'gauss':
        _mu = float(P.get('shPoleMu', 12.0))
        _sg = max(1e-6, float(P.get('shPoleSig', 6.0)))
        mag = _mx * math.exp(-0.5 * ((d - _mu) / _sg) ** 2)
        return 0.0, (mag if lat >= 0 else -mag)
    t = (d + _s) / _w
    if t <= 0.0:
        return 0.0, 0.0
    mag = _mx * (1.0 if t >= 1.0 else t)
    return 0.0, (mag if lat >= 0 else -mag)


def steering_layers(sim, lon: float, lat: float):
    """**原始分层引导风**(kt): (浅层 850, 深层 200) 的三环 x 8 方位面积加权平均。

    环带平均是为了让路径不被单个小涡旋带偏: 台风正好压在一个中尺度涡上时,
    单点采样会让移向突变, 而 3 环 x 8 方位(共 24 点)加权后只剩大尺度信号。

    **2026-09-22(轮次 102)把它从 `steering_at` 里抽出来**: 引导权重
    (`steering_weights`) 必须用**观测路径所在位置的真实分层风**来标定, 否则
    "我标定的权重"与"模式实际用的权重"会走两套采样(第 14 次口径错误的经典形态)。
    抽出后 `steering_at` 与标定工具 `tools/calib_steer.py` 共用这一条通路。
    """
    env = sim['env']
    coslat = max(1e-6, math.cos(lat * C['DEG']))
    # 24 个采样点一次性批量采样: 逐点标量调用时每个台风每一步要 242 次
    # env_bilinear/land_at(实测 motion_step 的最大头), 批量化后是 5 次数组调用
    lo = lon + _STEER_OFF_LON / coslat
    la = lat + _STEER_OFF_LAT
    inside = ((lo >= C['LON0'] + 1) & (lo <= C['LON1'] - 1)
              & (la >= C['LAT0'] + 1) & (la <= C['LAT1'] - 1))
    wl = np.zeros(lo.shape, dtype=np.float64)
    if inside.any():
        wl[inside] = 1.0 - land_at_arr(sim, lo[inside], la[inside])
    # ⚠ 轮次 159: `keep` **只按陆地筛**(与地形无关)。原先的顺序是"先乘摩擦、
    # 再按 `wl >= 0.3` 筛", 于是 `terrainOn` 一开就可能把原本入选的环带点踢出
    # `keep`, 甚至触发下面的 `wsum < 0.5` 中心点兜底 —— 那是"地形开关改变采样
    # 集合"的耦合, 会让引导出现开关式跳变。
    # 实测(terrain-reviewer, 1° 全球扫描 43560 格位): 落点集合变化 856 处, 但
    # **兜底翻转只有 14 处(0.032%)**, 全在深内陆; 台湾/吕宋/日本穿越路径一次都
    # 不触发。所以它不是"一开就跳变", 但拆掉耦合只要两行, 没理由留着。
    keep = inside & (wl >= 0.3)
    # 地形摩擦: 只折减**已入选**点的权重(契约见 `terrain.py:652-659`, 用 elev_mean)。
    # 默认关 ⇒ 整段不执行 ⇒ 逐位不变。
    _tp = sim.get('params') or {}
    if _tp.get('terrainOn') and _TG.available() and keep.any():
        wl[keep] *= _TG.friction_scale_arr(_TG.elev_at_arr(lo[keep], la[keep]))
    wsum = float((_STEER_RING_W[keep] * wl[keep]).sum()) if keep.any() else 0.0
    if wsum < 0.5:
        # 环带几乎全落在陆地/域外: 退回中心点取样(不引入额外权重偏差)
        s_sh = [env_bilinear(env['u850'], lon, lat),
                env_bilinear(env['v850'], lon, lat)]
        s_dp = [env_bilinear(env['u200'], lon, lat),
                env_bilinear(env['v200'], lon, lat)]
    else:
        w = _STEER_RING_W[keep] * wl[keep]
        lo_k, la_k = lo[keep], la[keep]
        s_sh = [float((env_bilinear_arr(env['u850'], lo_k, la_k) * w).sum() / wsum),
                float((env_bilinear_arr(env['v850'], lo_k, la_k) * w).sum() / wsum)]
        s_dp = [float((env_bilinear_arr(env['u200'], lo_k, la_k) * w).sum() / wsum),
                float((env_bilinear_arr(env['v200'], lo_k, la_k) * w).sum() / wsum)]
    # **副高驱动的净向极修正**: 正压地加在两层上, 三层加权系数和为 1
    # ⇒ 引导里的修正量就是它本身(与标定工具 `tools/calib_steer.py` 看到的完全一致)。
    _du, _dv = sh_pole_steer(sim, lon, lat)
    if _du or _dv:
        s_sh = [s_sh[0] + _du, s_sh[1] + _dv]
        s_dp = [s_dp[0] + _du, s_dp[1] + _dv]
    return s_sh, s_dp


def steering_at(sim, lon: float, lat: float, vmax: float = 40.0):
    """三层(850/中层/200 hPa)引导气流(kt) —— 见 `steering_layers` 的采样说明。"""
    s_sh, s_dp = steering_layers(sim, lon, lat)
    _P60 = sim.get('params') or {}
    ws, wm, wd = steering_weights(
        vmax, lat, _P60.get('steerEff850Lat', _P60.get('steerEff850')))
    mid = [0.6 * s_sh[i] + 0.4 * s_dp[i] for i in (0, 1)]
    tot = ws + wm + wd
    su = (ws * s_sh[0] + wm * mid[0] + wd * s_dp[0]) / tot
    sv = (ws * s_sh[1] + wm * mid[1] + wd * s_dp[1]) / tot
    su += sim['params']['steerU'] * KT
    sv += sim['params']['steerV'] * KT
    return su, sv


#: S12-② β 漂移**纬度查表**（**照抄沙盒**）：(纬度, u, v)，单位 **m/s**。
#: 出处 output/exploration/s9_sandbox_v2.md。u 西向为负、v 向极为正。
#: 用户裁定："公式算的不如**每个纬度到底漂多少**准，纯查表、零成本。"
_BETA_TABLE = [(8.0, -2.8, 2.3), (14.0, -2.8, 2.0), (20.0, -1.3, 1.7),
               (26.0, -0.9, 2.3), (32.0, -0.75, 1.6)]
#: 默认算法：'formula'（原公式版，**当前默认**）| 'table'（沙盒查表，保留对照）
#: ─────────────────────────────────────────────────────────────────────────────
#: ⚠⚠ **2026-10-05 回退到 'formula'（路径线 step ①）**。
#: 这是一次**回退一个已定位的回归**，不是新调参 —— 用户报"北半球全是西北行"之后查因：
#: `task-68` ② 把查表版设为默认，而查表版在**低纬的西向分量是公式版的 1.6–2.8 倍**：
#:     8°N : 查表 **−5.443 kt** vs 公式 −1.936 kt（2.8×）
#:     14°N: 查表 **−5.443 kt** vs 公式 −3.365 kt（1.6×）
#: ⇒ **是我上一轮加重了"西北行"** ⇒ 本步把它退回去 ✓
#: （纬度考据另见 `beta_drift_formula` 的 docstring：查表版 ∝ 表值、随纬度**减弱**，与 β 理论同向；
#:  公式版 ∝ sinφ、随纬度**增强**，与理论反向 —— 但**绝对量级**上查表版在低纬**过强**，
#:  而用户的诉求是路径，故先回退量级更小的公式版 ✓）
#: 查表版（`beta_drift_table`）**保留**，设 `BETA_DRIFT_MODE='table'` 即可切回 ✓
BETA_DRIFT_MODE = 'split'


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def beta_drift_table(lat: float):
    """β漂移**查表版**(kt)：按 `_BETA_TABLE` 在纬度上线性插值，再乘沙盒的窗口
    `smoothstep(2,7,|lat|) × (1 − smoothstep(42,65,|lat|))`
    —— **赤道附近与高纬都逐渐减弱**（照抄沙盒）。半球镜像同公式版。

    ⚠ 单位：表是 **m/s**，本函数返回 **kt**（与 `beta_drift_formula` 一致，
    调用方 `core.py` 两处都按 kt 用）。转换用 `KT = 1.944`。
    """
    al = abs(lat)
    w = _smoothstep(2.0, 7.0, al) * (1.0 - _smoothstep(42.0, 65.0, al))
    if w <= 0.0:
        return 0.0, 0.0
    la = [p[0] for p in _BETA_TABLE]
    u = float(np.interp(al, la, [p[1] for p in _BETA_TABLE]))
    v = float(np.interp(al, la, [p[2] for p in _BETA_TABLE]))
    hemi = 1.0 if lat >= 0 else -1.0
    return u * KT * w, v * KT * w * hemi


def beta_drift_split2(lat: float):
    """β漂移 **u=公式、v=折中**（`BETA_DRIFT_MODE='split2'`）—— §44 新增。

    **机制**（见 g_path_sim.md §43）：南向逃逸要求 `v_steer + v_beta < 0`，
    而**查表版的 v 太大**（8°N **+4.47** kt）⇒ 把 `base_wind` 里那条 ~15% 的南向尾巴整体抬正
    （实测合成后南向只剩 0.9%，而真实 `<20°` 的 S+SW+SE 有 **18.5%**）✗
    **但整条回退 formula 会让 ≥30° 存活崩**（step① 实测 30.4% → 20.7%）✗
    ⇒ **对 v 取折中**：`v = 0.5·公式 + 0.5·查表`（8°N：0.5·1.56+0.5·4.47 = **+3.02** kt）✓
    """
    _fu, _fv = beta_drift_formula(lat)
    _tu, _tv = beta_drift_table(lat)
    return _fu, 0.5 * (_fv + _tv)


def beta_drift_split(lat: float):
    """β漂移(kt) **按分量拆版（当前默认）** —— 路径线 step ①b。

    **2 分量各取所需**（Lead 2026-10-05 裁定）：
      · **纬向 u（西向）用公式版**：直击用户"北半球全是西北行"里的**西向过强**
        （查表版 8°N 给 −5.443 kt，是公式版 −1.936 的 **2.8 倍** —— 那是我 task-68 ② 引入的回归）
      · **经向 v（向极）保留查表版**：实测查表版的向极分量也强 **2.9 倍**（8°N +4.471 vs +1.559 kt），
        而**正是它把风暴推到更高纬度** ⇒ 整条回退会把"≥30° 存活"从 **30.4% 打到 20.7%** ✗
        （step ① 的实测，见 output/exploration/g_path_sim.md §15.4）

    **按裁定 1**：改它的动机是**路径**（西向过强），**不是**为了对上寿命/强度分布 ✓
    """
    _fu, _fv = beta_drift_formula(lat)
    _tu, _tv = beta_drift_table(lat)
    return _fu, _tv


def beta_drift(lat: float):
    """β漂移(kt) —— **分发**：`split`(默认, 按分量拆) | `formula` | `table`。
    `BETA_DRIFT_MODE='formula'` 时回原公式版（保留对照, 见 `beta_drift_formula`）。

    ⚠⚠ 概念区分（Lead 2026-10-05 要求写进注释，防止后人踩）：
    本函数是**物理漂移**；**不要**把"24°C 以下不支持生成"这类**生成支持度**
    塞进来 —— 那是**另一个概念**（`SEA_POTENTIAL` 那种归一化乘子），
    要加就加在**生成阈值/支持度**上，别改物理量。同理**不要**去改 `mpi()`。
    """
    _m = str(BETA_DRIFT_MODE)
    if _m == 'formula':
        return beta_drift_formula(lat)
    if _m == 'table':
        return beta_drift_table(lat)
    if _m == 'split2':
        return beta_drift_split2(lat)
    return beta_drift_split(lat)


def beta_drift_formula(lat: float):
    """β漂移(kt) **原公式版（保留对照）**: 向西 + 向极(两半球镜像), 高纬(>32 度)随大尺度气流增强而衰减。

    南半球必须镜像: 直接用 sin(lat) 会得到负值再被 clamp 抬到下限, 既丢符号
    又把 |β| 压小 —— 域覆盖 -60~60 度, 南半球台风会朝赤道漂。
    """
    al = abs(lat)
    f = clamp(math.sin(al * C['DEG']) / math.sin(15 * C['DEG']), 0.25, 1.4)
    decay = 1.0 / (1.0 + ((al - 32.0) / 12.0) ** 2) if al > 32.0 else 1.0
    hemi = 1.0 if lat >= 0 else -1.0
    # T3 标定: 模拟路径的纬跨只有 5.2°, 观测 10.2°。查下来环境场的**经向风
    # 在热带基本为 0**(v850 在 15N 只有 +0.6kt —— 这是对的, 信风本就纬向),
    # 所以模拟里唯一往极向推的就是 β 漂移, 而它 2.24kt 偏小(理论 2-4 m/s,
    # 这里只有 2.1 m/s)。观测的 Δ纬 9.6° 需要平均 ~4.8kt 的经向速度。
    # 纬向也一起加大到经典 β 漂移量级(西向 ~3.6kt)。
    # 经向分量从 3.6 回调到 2.9: 上一轮为了补西太的纬跨(需要 9.6°)把它提到
    # 3.6, 但东太只需要 6°(168h), 结果东太台风多走 7 个纬度冲进墨西哥内陆,
    # 登陆率 80% vs 观测 13%。两个盆地需求相反, 一个全局常数无法同时满足;
    # 取 2.9 折中, 并接受西太纬跨略欠。
    #
    # ⚠⚠ S12-② 查因（Lead 2026-10-05 要求写进注释）：**本函数与查表版的纬度趋势是"反的"**。
    #   · 解析形式：`f = clamp(sin|lat|/sin15°, 0.25, 1.4)`，`u = −3.6·f·decay`、`v = +2.9·f·decay·hemi`
    #     ⇒ **纬度依赖就是 `sin φ`（= 科氏参数 f），在 0.25/1.4 处截断**（≥26° 饱和）；
    #   · 实测剖面：8° −1.936、14° −3.365、20° −4.757、26/32/40° −5.040 kt ⇒ **随纬度增强**；
    #   · 查表版：8° −5.443、14° −5.443、20° −2.527、26° −1.750、32° −1.458 kt ⇒ **随纬度减弱**；
    #   · **理论**：β = df/dy = 2Ωcosφ/a ⇒ **赤道最大、向极减小**（cos8°=0.9903 vs cos32°=0.8480，
    #     低纬 β 高 17%）⇒ **查表版与理论同向 ✓，本公式版与理论反向 ✗**。
    #   ⚠ **但这不是"除法/索引写反"的编码 bug** —— 代码算的就是它写的（一次 sin/sin，无反向运算）；
    #     它实质是上面 **T3 标定**产出的**经验漂移曲线**（按"观测纬跨 9.6°/6°"反推幅度），
    #     **不是 β 的解析式** ⇒ **差因是"定义不同"，不是实现错**（同 S12 的 ③-B）。
    #     **默认已改查表版（`BETA_DRIFT_MODE='table'`）；本函数留作对照，未重标定前不要改回默认。**
    return -3.6 * f * decay, 2.9 * f * decay * hemi


def local_hour(sim, lon: float) -> float:
    return ((sim['t'] + (lon - 120) / 15.0) % 24 + 24) % 24


# ── TC 创建 ──

def make_tc(sim, lon: float, lat: float, vmax_kt: float) -> dict:
    vmax_kt = clamp(vmax_kt, 20, 155)
    pmin = pmin_from_v(vmax_kt)
    rmw = rmw_from_v(vmax_kt)
    basin = basin_of(lon, lat)
    # **命名要等到达标**(用户 2026-09-19: "台风生成就是 TS, 没有 DB-TD-TS 过程")
    # 出生时只给一个临时编号(真实业务里就是 WP90/WP91 这类 invest 编号),
    # 达到所在洋区的命名门槛后才从真实命名表取名(见 maybe_name)。
    #   WP/EP/AL/CP/IO: >=34 kt(JTWC/NHC 口径)
    #   SH: AU 区(BOM)要四象限都有 >=35kt 大风; FJ/MU/ID 34kt
    name = None
    # **扰动(invest)编号**: 用户 2026-09-21 的规则 —— 每个洋区从 A0,A1,…,A9,B0,…,Z9
    # 依次分配(字母+数字), 对应的"90-99"号(真实 invest 编号)按 90 + 数字 取。
    # b-deck 里: 第 2 列写 A0W/B9W(带洋区后缀), 名字列写 90W/99W。
    _seq = sim.setdefault('investSeq', {})
    _n = int(_seq.get(basin, 0))
    _seq[basin] = _n + 1
    _letter = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[(_n // 10) % 26]
    _digit = _n % 10
    _invest = '%s%d' % (_letter, _digit)                  # 'A0' / 'B9' / ...
    _invest_num = 90 + _digit                             # 90..99
    _desig = '%s%02d' % (basin, _invest_num)              # 兼容旧字段: WP90
    tc = {
        'id': sim['nextId'],
        'name': _desig,
        'desig': _desig,
        'invest': _invest,                # 'A0' 这种扰动码(不带洋区后缀)
        'investNum': _invest_num,         # 90..99
        'cy': None,                       # 低压编号(达到 TD 才给; 由 maybe_number 填)
        'named': False,
        'basin': basin,
        'lon': lon, 'lat': lat, 'vmax': vmax_kt, 'pmin': pmin,
        'rmw': rmw, 'b': holland_b(pmin),
        'age': 0.0, 'dead': False, 'isUser': len(sim['tcs']) == 0,
        'mu': 0.0, 'mv': 0.0, 'noiseU': 0.0, 'noiseV': 0.0,
        # 阶段 5/6: 置换与核心组织度(riCD/ri 已删除 —— 那套"开窗加固定值"
        # 没有对应物理过程, 改由 core_org / EMBC / 屏障突破 / 集中加热承担)
        'erc': None, 'ercCD': 0.0,
        'core_org': None, 'embc': False, 'bandA': 0.0, 'barrierBreak': False,
        'weak': 0.0,
        'anvil': 0.0, 'phase': 'TC',
        # 生成时刻的记录: 保留(用户可能刚建完就导出 b-deck, 那时 track 不能是空的
        # —— tests/test_sim_mode.py::test_export 就是这个约束)。
        # 它与**主循环同一步**补的"步后"记录标同一个 t(实测 b-deck 头两行同标
        # 2025010806 却是 20kt/31kt), 因此 "同一时次只保留最后一条" 的职责放在
        # 写盘侧: tools/sim_year.bdeck_lines(保留步后状态, 那才是该时刻的真实状态)。
        # 首报就带阶段码: 新生的都是扰动(观测自评里 82% 首报就是 DB)
        'track': [{'t': sim['t'], 'lon': lon, 'lat': lat, 'vmax': vmax_kt,
                   'pmin': pmin, 'phase': 'DB', 'name': None, 'named': False}],
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


def _quadrant_gale_ok(tc, v_kt: float = 34.0) -> bool:
    """四象限是否都有 >=v_kt 的风(BOM 命名规则用)。

    本模型的切向风廓线是轴对称的, 四象限差异来自移速与环境流; 这里用 Holland
    廓线 + 移速不对称因子做判据: 最弱象限(左前 0.85)也要达到 v_kt。
    这样"四象限 >=35kt"等价于 vmax >= 35/0.85 ≈ 41kt 的强不对称情形, 弱不对称
    时约等于 vmax >= 35kt —— 与 BOM 的实际效果一致(比 JTWC 严一档)。
    """
    try:
        asym = 0.85                      # 左前象限(最弱)的不对称因子
        return bool(tc['vmax'] * asym >= v_kt)
    except Exception:
        return False


#: **命名概率**(用户 2026-09-21): "一个台风的命名在 35kt 以上不是必然事件, 应该改为风速
#: 越高越容易命名(每一报)". 逐点线性插值, 低于 25kt 不命名。
NAME_PROB = ((25.0, 0.01), (30.0, 0.10), (35.0, 0.75), (40.0, 0.80), (45.0, 0.85),
             (50.0, 0.90), (60.0, 0.95), (70.0, 0.98), (90.0, 1.00))


def name_probability(vmax_kt: float) -> float:
    v = float(vmax_kt)
    if v < NAME_PROB[0][0]:
        return 0.0
    if v >= NAME_PROB[-1][0]:
        return 1.0
    for (v0, p0), (v1, p1) in zip(NAME_PROB, NAME_PROB[1:]):
        if v0 <= v <= v1:
            f = (v - v0) / max(1e-6, v1 - v0)
            return p0 + (p1 - p0) * f
    return 0.0


#: "**没有低压编号、但被命名**的台风"占比(用户 2026-09-21: "也需要存在没有低压的台风")。
#: 现实里确实有从一出现就是 TS、没走过 TD 编号的系统。判据用**风暴 id 的确定性散列**
#: (不用 rng) —— 这样不会扰动随机数流, 除"是否给低压号"外整条模拟轨迹不变。
DIRECT_NAME_P = 0.15


def maybe_name(sim, tc) -> None:
    """达到所在洋区的命名门槛 → 从真实命名表取名(只做一次)。

    门槛(见 naming.name_threshold):
      · WP/EP/AL/CP/IO: vmax >= 34 kt
      · SH/AU(BOM 澳区): vmax*0.85 >= 35 kt(四象限大风)
      · SH/FJ、SH/MU、SH/ID: vmax >= 34 kt
    """
    if tc.get('dead'):
        return
    # 本风暴是否走"直接命名"通道(不给低压编号)。确定性散列, 与 rng 流无关。
    if 'directName' not in tc:
        _h = (int(tc.get('id', 0)) * 2654435761 + int(sim.get('seed', 0))) % 1000
        tc['directName'] = (_h < int(round(DIRECT_NAME_P * 1000)))
    # **低压编号(cy)**: 只有达到 TD 强度才给(用户 2026-09-21: 需要存在"没有低压"的台风,
    # 也就是可以命名但没有 JTWC 低压编号的系统)。WP 的 TD 口径取 25 kt。
    # 走 directName 的系统**永远不给编号** —— b-deck 第 2 列因此一直是扰动码(A0W 这种),
    # 末列却是名字(如 Dikeledi), 正是用户要的那种"有名字、没有低压号"。
    if (tc.get('cy') is None and not tc.get('directName')
            and float(tc.get('vmax', 0.0)) >= 25.0):
        _cnt = sim.setdefault('cySeq', {})
        _b = str(tc.get('basin') or 'WP')
        _cnt[_b] = int(_cnt.get(_b, 0)) + 1
        tc['cy'] = int(_cnt[_b])
        tc['numberedAt'] = float(sim.get('t', 0.0))
    if tc.get('named'):
        return
    try:
        from simulator.simcore import naming as _naming
        # 最低门槛(低于它连考虑都不考虑), 具体命中与否按**概率**(风速越高越容易)
        th = min(25.0, float(_naming.name_threshold(float(tc['lat']), float(tc['lon']))))
        sub = _naming.sub_basin(float(tc['lon']), float(tc['lat']))
        if sub == 'AU' and not _quadrant_gale_ok(tc, 35.0):
            return
        if float(tc['vmax']) < th:
            return
        if sim['rng']() >= name_probability(float(tc['vmax'])):
            return                                   # 这一报没命上, 下一报再抽
        _nm = sim.get('namer')
        if _nm is None:
            _nm = _naming.Namer(str(sim.get('start', '2000-01-01')))
            sim['namer'] = _nm
        # when=clock(sim): 让命名走**真实命名时刻表**(JMA best track), 而不是指针按日期比例推
        nm = _nm.next_name(str(tc.get('basin') or 'WP'),
                           lon=float(tc['lon']), lat=float(tc['lat']),
                           when=clock(sim))
        if nm and not str(nm).endswith('00'):
            tc['name'] = str(nm)
            tc['named'] = True
            # 记住命名时刻(小时, 从模拟起点算) —— b-deck 导出要用它区分"命名前填编号、命名后填名字"
            tc['namedAt'] = float(sim.get('t', 0.0))
    except Exception:
        logger.debug('命名失败', exc_info=True)


#: 切变响应系数 —— **I1 实测标定**(2026-09-17, 按观测切变分箱统计 RI 率):
#:   切变 0-5 / 5-10 / 10-15 / 15-20 / 20-25 / 25-30 / 30-40 / 40-50 / 50-60 kt
#:   含RI%  7.8   7.1    6.5     5.6     3.4     3.3     1.8     0.3     0.1
#: 即: **平均增长只受温和抑制**(S=50 → fs 0.53), 切变的主要作用是压制"尾部爆发"
#: (RI 门控 gate, S=20 → 0.44, S=40 → 0.037)。
SHEAR_FS_S0, SHEAR_FS_L = 6.0, 55.0
#: RI 门控的尺度。**由 I1 标定表反解**(2026-09-20): 把上表的 RI 率归一到第一档,
#: 令 gate = exp(-(S/L)^2) 逐档解 L, 得到 24.5 / 29.3 / 30.4 / 24.7 / 29.7 / 28.9 /
#: 24.9 / 26.4 —— 各档一致落在 25-30 之间, 取中值 **27**(原来写的是 22, 偏严:
#: 瞬时切变热带均值 27 kt 时 gate 只有 0.27, 而标定表对应的是 0.42)。
SHEAR_GATE_L = 27.0


def shear_response(shear_kt: float):
    """返回 (fs, gate): fs 乘在平均增长率上, gate 是 RI 机制的切变门控。

    2026-09-20: 抽出来是因为**两条路径曾经各写一份** —— 主强度模型
    (intensity_step) 用 55/6 的温和版, 而集合预报 (run_ensemble) 还留着旧的
    exp(-((S-5.8)/21.4)^2) 硬版(S=50 → 0.015)。结果同一时刻, 预报表盘里的成员
    强度被压得比主模型低一大截(实测同样切变下增长率差 30 倍)。
    """
    s = max(0.0, float(shear_kt))
    fs = math.exp(-(max(0.0, s - SHEAR_FS_S0) / SHEAR_FS_L) ** 2)
    gate = math.exp(-(s / SHEAR_GATE_L) ** 2)
    return fs, gate


#: 向 MPI 逼近的**时间尺度**(小时)。观测反推: V=40kt、MPI≈120kt 时 24h Δvmax
#: 只有 +3.2kt ⇒ tau_eff ≈ 600h; 早期写 38h 快了 16 倍("快涨快死")。增强慢、减弱更慢
#: (观测 V 80-110 时 24h 掉 -9.1kt), 所以分开取。
#:
#: **必须由主模型与集合预报共用**(2026-09-21): 集合积分里原来还留着 ,
#: 于是界面上"预计巅峰"比主模型自己的行为快 1.8~4 倍 —— 显示与实况自相矛盾。
#: 这两条路径的注释里都写着"必须与 intensity_step 同口径", 但数值是各自硬编码的,
#: 所以还是漂了; 现在只有一个来源。
TAU_INTENSIFY_H = 38.0
#: 2026-09-21(轮次 29)速率尺度重标: 原 68h 时, 逐格各档升均值一律只有观测的
#: 0.46-0.58 倍(48-64 +3.99 vs +7.26 / 80-100 +5.32 vs +11.04 / 100+ +5.81 vs +12.62),
#: 风暴级峰值中位 51kt vs 70、ACE 中位 2.43 vs 5.43, 峰值后减弱 -0.8~-1.4 vs -5~-12.5。
#: 六档同一比例 ⇒ 缺的是**整体速率尺度**, 不是某个分档的系数。
TAU_WEAKEN_H = 30.0

#: **组织期**(小时): 新生扰动要用这么久才把"潜在强度"利用到 100%。
#: 2026-09-21 二次标定: 54h 时实测前生期(首报→34kt)中位 **162h**, 而观测只有 **54h**(3× 过长);
#: 收到 12h 后配合下游增速上限, 目标是把前生期压回观测量级。
#: 观测前生期中位 60 h(扰动段 42 h), 模型原来只有 12 h(2 报) ⇒ 用户报的"扰动阶段太短/经常只有 2-3 报"。
#: 只压制增长端(见 intensity_step), 减弱端不动。

#: **温带变性(ET)判据的累积时长**(小时): 高纬 + 冷海 + 强切变 + 仍有一定强度, 持续这么久就变性。
#: 原来 ET 分支要求"age>168h 且 vmax<50 且 (sst<24.5 或 |lat|>32)" —— 我们的风暴在 30-36° 就
#: 因冷水 MPI 崩掉先死了, 所以用户"一个 wp 的变性台风都没看到"。
#: **2026-09-22(轮次 104)24h -> 12h**: `tools/probe_et_gate.py` 实测(120 天, 698 个"|lat|>=28"的
#: TC-步): 把四条件(纬>=30 / sst<27.5 / shear>=22 / vmax>=35)**同时满足**的最长连续段,
#: 全场最长只有 **18h** —— 也就是说 24h 的门槛**在模型里根本不可能达到**, EX 恒为 0 是
#: 设计上的不可能, 不是"变性过程没发生"。阈值扫描(每档的"最长连续段"):
#:     shear>=12 -> 36h | >=15 -> 36h | >=18 -> 36h | >=20 -> 30h | **>=22 -> 18h**
#: 先只动这一处(把门槛降到 12h, 让 18h 的段能过), 保留 shear>=22 这个斜压性门槛不动;
#: 若 EX 占比仍明显低于观测(自评 2.1-2.7% 的记录), 下一轮再降 shear 阈值。
ET_HOURS = 12.0


def intensity_step(sim, tc, dt: float) -> None:
    env = sample_env(sim, tc['lon'], tc['lat'])
    tc['env'] = env
    # F8: **出流场**。标量参数只给基准, 实际出流顺畅度由该点的 200hPa 辐散决定:
    # 高空辐散好 → 台风抽上来的质量排得出去 → 强度上限高; 辐合区则相反。
    _ov = sim['params']['outflow']
    _dv200 = env.get('div200')
    if _dv200 is not None:
        _ov = clamp(_ov + 0.35 * float(_dv200), 0.0, 1.0)
    vpi = mpi(env['sst'], env['ohc'], _ov, env['lat'],
              env.get('t200'))
    # 存进 env 供诊断(评估工具要算 V/MPI 这个最关键的强度利用率指标)
    env['vpi'] = vpi
    # **MPI 的时间记忆(24h 指数平滑)**。追踪发现 MPI 沿路径剧烈震荡
    # (实测一条风暴: 70→55→124→175, 甚至因经过小岛掉到 9), 而强度追不上 ——
    # 于是风暴在"锯齿"里来回, 永远到不了峰值(一条 540h 寿命的风暴峰值只有 99kt)。
    # 真实台风响应的是有记忆的有效 MPI, 不是瞬时值。
    _a = min(1.0, dt / 24.0)
    tc['vpi_s'] = vpi if 'vpi_s' not in tc else tc['vpi_s'] + (vpi - tc['vpi_s']) * _a
    vpi = tc['vpi_s']
    # 陆地抑制 MPI: 陆面上潜在强度大打折扣, 防止"陆上反而增强"
    if env['land'] > 0.05:
        vpi *= 1.0 - 0.85 * min(1.0, env['land'])
        # ⚠ 轮次 159 **撤回**这里曾经加的地形项。两个错:
        #   ① 位置: 契约(`terrain.py:652-659`)说 `friction_scale` 折减的是
        #      "**该点对台风引导的贡献**"(对应引导权重 `wl = 1-land`), 不是 MPI;
        #   ② 量级: 用格内**峰值**海拔时, 中央山脉格 `exp(-3700/1500)≈0.085`
        #      再叠 `1-0.85*land≈0.15` ⇒ 潜在强度只剩 ~**1.3%** —— 那不是
        #      "加点地形影响", 是把岛上台风的 MPI 直接归零。
        # 地形摩擦已按契约改到引导权重那处(`wl *= friction_scale(elev_mean)`)。
        # (由 verifier 复核发现, 见 VERIFY_R158.md §2.5)
    # I3 标定(**最关键的一处**): tau 是"向 MPI 逼近的时间尺度"。
    # 用观测反推 —— V=40kt、MPI≈120kt 时观测的 24h Δvmax 只有 **+3.2kt**,
    # 即 3.2/24/80 = 0.00167 /h → tau_eff ≈ 600h。原来写 38h **快了 16 倍**,
    # 于是"快涨快死": RI 率 6.4%(观测 1.5%)、生命史只有观测的 1/3。
    # 增强慢、减弱快是不对称的(观测 V 80-110 时 24h 掉 -9.1kt), 所以分开取。
    # ── 阶段 4: **RMW 改为预报量** ──
    # 原来每步  是纯函数 —— 同强度下 RMW 完全一样,
    # 而观测里 45-55kt 时的 P25~P75 是 25~45nm(**宽 20nm**)。这个离散度不只是
    # 噪声: 它就是"为什么有些风暴能冲高、有些不能"的载体(小 RMW 的更能冲)。
    # 现在让 RMW 自己朝平衡值松弛, 并受环境湍流扰动。
    _r_eq = rmw_eq(tc['vmax'])
    # ERC 期间**平衡半径**外扩(眼墙置换: 外眼在 2.2-3.0×RMW 处形成, 旧内眼被
    # 隔绝后新眼取外眼半径 → 眼径变大)。**必须作用在平衡值上而不是对预报值
    # 连乘** —— 原来  在末尾会被 rmw_from_v 覆盖
    # 所以看不出来; RMW 改成状态量后每步连乘会把半径指数推到 clamp 上限(实测 186nm)。
    _t_r = 30.0                                   # 平时的收缩/扩张时间尺度
    if tc.get('erc'):
        # 置换期间眼墙变化**快得多**: 外眼在 2.2-3.0×RMW 处形成 → MOAT →
        # 外眼收缩成新眼, 整个过程 2-3 天, 眼径大致翻倍。所以外扩系数取 0.9、
        # 时间尺度收到 8h。原来只乘 0.55 且用 30h 松弛, 会被"vmax 上升使平衡
        # 半径下降"抵消掉(实测 RMW 只从 15.7 涨到 17.4)。
        _x = min(1.0, tc['erc']['t'] / max(1e-6, tc['erc']['T']))
        _r_eq *= 1.0 + float(tc['erc'].get('rF', 0.9)) * math.sin(math.pi * _x)
        _t_r = 8.0
    tc['rmw'] += (_r_eq - tc['rmw']) * min(1.0, dt / _t_r)
    tc['rmw'] += sim['gauss']() * 0.55 * math.sqrt(max(dt, 1e-3))
    # 状态量地板 6 -> 2 nm(同上: 让极小眼可表示; 上限不动)
    tc['rmw'] = clamp(tc['rmw'], 2.0, 120.0)
    # 外核惯性稳定度屏障: RMW 偏大 → 加热分散 → 上限被压低(见 eta_rmw)
    vpi *= eta_rmw(tc['rmw'])
    env['vpi_eta'] = vpi

    # **2026-09-18 二次标定**: 三年回放(1997/99/2026)平均 ACE 13.1、P50 71.3,
    # 而观测是 9.5 / 65 —— 整体偏强约 25%。但寿命反而偏短(26/22/29 vs 观测 32),
    # 说明**风暴长得太快**而不是活得久。tau 从 55/70 提到 68/84(约 +24%)。
    tau = TAU_INTENSIFY_H if tc['vmax'] < vpi else TAU_WEAKEN_H
    # **高段松弛更快**(2026-09-21 轮次 84)。轮次 72/83 查明: 100+ 档的升均值只有
    # +8.47, 观测是 +12.66(0.67x); 而限制它的**不是上限**(轮次 82 实测抬上限无效),
    # 是松弛时间尺度 —— (vpi-V)/38h 在 V=110/vpi=150 时只有 6.3 kt/6h。
    # 只对 >=100kt 的增长分支缩短(不动低中档: 48-100 的幅度已精确吻合,
    # 轮次 43 全档一起动导致 100-129 过冲、130+ 反而降)。
    _tau_eff = tau
    if tc['vmax'] >= 100.0 and tau == TAU_INTENSIFY_H:
        # **高段松弛更快**(轮次 84 落地, 轮次 136 参数化)。
        # 轮次 136 的验收判读显示: 修好生成纬度后, C 的病根变成
        # "100-129 挤太多(28.5% vs 可比 5.3-11.1%)、130+ 与上尾反而薄
        #  (9.5% vs 12.3-13.9%, P90 126 vs 128-130, P95 141 vs 147-155)"
        # —— 即**分布形状过窄**, 而不是整体偏弱。要动的正是"已经到 100kt 以上的那批
        # 能不能继续往上走", 而轮次 82/84 已证**上限不是绑定约束、tau 才是**。
        # 所以把这个系数开放成参数(默认仍是 0.6 = 现状, 零副作用), 便于同窗口 A/B。
        _tau_eff = TAU_INTENSIFY_H * float(
            sim['params'].get('hiTauScale', 0.6))
    dV = (vpi - tc['vmax']) / _tau_eff
    # ── 增长支的**确定性上限**(2026-09-21, 判据 v2: INTENSITY_TARGETS_V2.md) ──
    # 逐格观测: 34-48kt 暖海格**均值 +1.43**(不是中位 0 —— 中位 0 是记录离散化),
    # 模型却是 +2.93。要做到"观测那种抖动为主、偶尔爆发"的形状, 必须压**持续性推力**。
    # 关键: 这一行在基础松弛**之后、所有 RI/EMBC/集中加热等突发项之前**,
    # 所以只削"每一报都在推"的那部分, 突发通道完整保留 ——
    # 前三次失败( /  / )都是在**整条增长通道**上削,
    # 于是 test_ri_event(24h 需 +25kt)与 test_tc_develops_and_moves(72h 需 +20kt)同时红。
    # 上限取 3.0 kt/6h: 低于观测的升均值(+6.3), 因为观测的升只占 35% ——
    # 持续性 +3.0 已经比"偶发 +6.3"更能抬高均值。
    # ── RI(快速增强)状态机: **真实通道**(2026-09-21) ──
    # 发现: 重写前 `tc["ri"]` 在模拟器里**从未被读取** —— tests/test_sim_mode_ref.py 注入的
    # ri 状态是死的, 它断言的 "24h +25kt" 完全由基础 MPI 松弛给出。也就是说那个测试一直在
    # **锁定"单调趋近 MPI"这一病灶**(逐格观测里 34-48kt 暖海档均值只有 +1.43)。
    # 物理上 +25kt/24h 确实存在 —— 但它是**少数风暴在极端有利环境下的爆发**(观测 RI 占比 28%),
    # 不是"每一报都在推"。所以把 RI 建成**有界乘子/独立通道**, 常规增长则按观测均值设上限。
    #   触发: 暖海(sst>=28.4) + 弱切变(shear<=16) + 无陆地 + V>=45, 每步小概率
    #   持续: 24h; 期间增长上限抬到 12 kt/6h(观测高档升均值量级)
    #   注入式(测试给的 {'t':...} 无 life)按 48h 生命处理, 保证 24h 窗口内有效
    _ri = tc.get('ri')
    if _ri:
        _ri['t'] = float(_ri.get('t', 0.0)) + dt
        if _ri['t'] > float(_ri.get('life', 48.0)):
            tc['ri'] = None
            tc['riCD'] = 12.0                      # 不应期 24 -> 12h(轮次 41): 让风暴一生
                                                       # 里能有**多次**爆发窗口, 强档个数才上得去
    else:
        tc['riCD'] = max(0.0, float(tc.get('riCD', 0.0)) - dt)
        if (float(env['sst']) >= 28.4
                and float(env['shear']) <= 16.0 and float(env['land']) < 0.05
                and tc['riCD'] <= 0.0 and tc['vmax'] < vpi):
            # **RI 不再有启动门槛**(用户 2026-09-21 明确要求): 原来要求 vmax >= 45kt 才能起爆,
            # 但快速增强的定义里本来就没有"必须先够强"这一条 —— 它可以从任何强度起爆。
            # 保留的是**环境有利度**这道门(sst/shear/land), 那才是 RI 的物理前提。
            # 低强度端的爆发幅度由下面的 _cap6 分档限制, 不让扰动一步跳几十节。
            # 触发频率实验(2026-09-21 轮次 41): 0.05 -> 0.08 试过, 实测**无益**
            # (130+ 占比 9% -> 5%), 真正的主导量是下面的**幅度上限** ⇒ 回到 0.05。
            if sim['rng']() < 0.05 * dt:           # 每 6h 约 30%
                tc['ri'] = {'t': 0.0, 'life': 36.0}   # 生命期 24 -> 36h(同上)
    # ── 增长支上限(按环境分档, 判据 INTENSITY_TARGETS_V2.md) ──
    # 常规暖海: 4.0 kt/6h(观测 34-48 档均值 +1.43, 而模型确定性项曾有 +2.93);
    # 极端有利尾(sst>=30 且 shear<=6): 8.0(观测该尾里确实出现 +25kt/24h);
    # RI 期间: 12.0(观测高档升均值 +11 ~ +12.6)。
    if dV > 0.0:
        # 4.0 -> 6.0(轮次 34, 可比口径重测后): 观测 34-48 档升均值 **+6.30**、
        # <34 档 **+5.30**, 而模型只有 +3.39 / +3.04(**0.6x**)且升步**过于频繁**
        # (升占比 51% vs 观测 35%)—— 正是"上限截掉升尾、只能用抖动凑小步"的特征。
        # 同时各档均值比观测低约 1 kt/6h, 抬升上限对这两处是同一个方向。
        # 按观测升均值分档的版本在轮次 43 **实测证伪并回退**: 130+ 占比 9% -> 7%(更差),
        # P95 166 -> 132, 虽然 P50 变好(72 -> 79)。按纪律(只在目标指标改善时保留)回退。
        # 保留固定 6.0: 它与"西太扰动 64(观测 64)"那一组读数一起给出过最好的综合表现。
        _cap6 = 6.0
        # 轮次 82 曾在此为 >=80kt 补上限(80->6.0, 100+->12.0), 依据是"100+ 档升均值
        # 8.47 vs 观测 12.66"。但 3 种子步级复测显示**无效**(100+ 升均值 8.47 -> 7.65):
        # **上限在那根本不是绑定约束** —— 松弛 (vpi-V)/tau 在 V=110/vpi=150 时只有
        # 40/38 = 6.3 kt/6h, 远低于 12。⇒ 限制 100+ 升步的是**松弛时间尺度 tau**, 不是上限。
        # 已回退。若要继续攻 100+, 该动的是"高段的有效 tau"(而不是全档 tau ——
        # 那会打乱已经吻合的 48-100 档)。
        _cap6 = 6.0
        # 与上面的"高段松弛更快"配套: 只对 >=100kt 放开上限(观测该档升均值 +12.66),
        # 不动 48-100(它们的幅度已经吻合)。
        if tc['vmax'] >= 100.0:
            _cap6 = 12.0
        if float(env['sst']) >= 30.0 and float(env['shear']) <= 6.0:
            _cap6 = max(_cap6, 8.0)
        if tc.get('ri'):
            # RI 现在可以从**任何强度**起爆 ⇒ 低强度端必须限制爆发幅度,
            # 否则一个 20kt 的扰动能一步跳十几节(观测里扰动段的步长只有几节)。
            # **幅度上限 + MPI 刹车**(2026-09-21 轮次 41):
            #   实测"降低幅度"会让超强变少(18->12 时 130+ 占比 9%->5%)⇒ 主导量是**上限**;
            #   但单纯提高上限会造出 200+ 的怪物(实测最大 211, 观测 185), 根因是
            #   **RI 起爆后再没有任何约束**, 风暴能冲过自己的 MPI。
            #   ⇒ 补物理刹车: 越接近 MPI, 爆发越收(与旧 EMBC 的 _head 同思路)。
            _head = max(0.0, 1.0 - tc['vmax'] / max(1.0, vpi))
            # ── 幅度是否随强度成形(轮次 121 预备, **默认仍走 legacy**) ──
            # 观测的**升步均值**(逐格, kt/6h): <34kt **5.3** / 34-48 **6.3** /
            # 100+ **12.7** ⇒ 低端只该有 5-6 kt/6h。而 legacy 式 `20*(0.35+0.65*_head)`
            # 在 vmax=25/vpi=195 时给 **18.4 kt/6h = 73 kt/天** —— 这正是
            # `tools/analyze_amode_intensity.py` 测到的"弱端 24h +49~+74kt"的通道,
            # 也是 `probe_lifecycle` 测到的"低端增强速率 19.4 vs 观测 8.8 kt/天"。
            # 代码自己的注释("低强度端必须限制爆发幅度")说明**意图本来就是限制低端**,
            # 只是实现反了(`_head` 在低强度时 **接近 1**, 反而把幅度放大)。
            # vshape: 幅度 = base + gain*clamp((v-v0)/(v1-v0), 0, 1), 再乘同一个 _head 刹车。
            # 注意 100+ 档不受影响: 那里 `_cap6` 已经是 12.0, 取 max 后仍是 12.0。
            if str(sim['params'].get('riAmpForm', 'legacy')) == 'vshape':
                _P = sim['params']
                _v0 = float(_P.get('riAmpV0', 65.0))
                _v1 = max(_v0 + 1e-6, float(_P.get('riAmpV1', 100.0)))
                _amp = (float(_P.get('riAmpBase', 6.0))
                        + float(_P.get('riAmpGain', 6.7))
                        * clamp((tc['vmax'] - _v0) / (_v1 - _v0), 0.0, 1.0))
            else:
                _amp = 20.0
            _cap6 = max(_cap6, _amp * (0.35 + 0.65 * _head))

        dV = min(dV, _cap6 / 6.0)
    # ── **发展阻滞**(用户要求: "阻滞扰动, 不利条件越强阻滞越明显") ──
    # 观测: 全球 ~200 个扰动/年, 只有 ~103 个发展成编号系统 ⇒ **发展率约 50%**。
    # 我们实测 87%(扰动几乎个个长大) —— 原因是当年压住扰动的 DISTURB_CAP 在轮次 33
    # 被当死代码删掉了。这里改用**有物理含义的阻滞**: 扰动(vmax<34kt)在不利环境里
    # 长不起来, 但它**依然存在**(只是停在 DB/WV/TD), 正是观测里的形态。
    if dV > 0.0 and tc['vmax'] < 34.0:
        dV *= unfav_block(env, tc['lat'],
                          k=float(sim['params'].get('unfavK', UNFAV_K)))
        # ── 轮次 125: **纯扰动的发展阻力**(任务 E 旋钮②, 默认 1.0 = 不生效) ──
        # 轮次 125 实测: 单靠 `unfavK`(环境不利度)**压不下发展率** ——
        # k=2.2/4/6 三档的发展率是 77%/80%/72%, 基本不动。原因很清楚:
        # **生成点是按观测密度/GPI 场抽样的**(`_gi_weight`), 所以扰动一出生就在
        # 有利环境里(`unfav_index≈0` ⇒ 阻滞因子≈1), 环境闸门在生成阶段就已经筛过一遍了。
        # ⇒ 要压发展率, 闸门必须挂在**系统自身的属性**上, 而不是环境:
        # 内核本来就有 `tc['disturb']`(75% 的生成是纯扰动, 但它只影响初始强度与阶段码标签),
        # 现在让它在 34kt 以下**额外承受一个增长阻力**。
        # 为什么不是"把所有人的增长速率一起降"(轮次 122 的 `riAmpForm` 教训):
        # 那次把峰值中位从 73/75 打到 57/62、130+ 从 4.5/3.4% 打到 1.4/2.9%。
        # 只打 `disturb` 的那 75% 则保留了"一出生就较有组织"的那批系统去冲高强度。
        _dk = float(sim['params'].get('disturbK', 1.0))
        if _dk != 1.0 and tc.get('disturb'):
            dV *= _dk
        # **僵胎类**(轮次 149): 出生就被标成"长不大的弱扰动"——在 34kt 以下给很重的增长阻力,
        # 于是它们基本停在 DB/WV, 而**发展那一半完全不受影响**(这是与 `disturbK` 的关键区别:
        # `disturb` 覆盖 87% 的出生、包含大量未来的强风暴, 拿它当闸门会把上尾一起砍掉)。
        if tc.get('stillborn'):
            dV *= float(sim['params'].get('stillbornK', STILLBORN_K))
    # ── 原"组织期(gestation)"块已按目标 ① 删除(轮次 33) ──
    # 它不是被"调参调到无效", 而是**已经死了**: `_gest` 恒为 1.0(`dV *= 1.0` 是空操作),
    # 且 `tc['gest']` 全仓**只写不读**。前生期的长度现在完全由增长支上限与基础松弛决定。
    # 对应常量 GEN_GEST_H 一并删除。

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
    # F8: 切变**方向**。cosφ = 切变矢量与移速矢量的夹角余弦。
    # φ=0(顺切变移动)最不利, φ=180(逆切变)最有利; 幅度 ±28%。
    _su, _sv = env.get('sh_u'), env.get('sh_v')
    if _su is not None and _sv is not None:
        _sp = math.hypot(tc['mu'], tc['mv'])
        if _sp > 0.5 and env['shear'] > 1e-3:
            _cos = (_su * tc['mu'] + _sv * tc['mv']) / (env['shear'] * _sp)
            shear_eff *= clamp(1.0 + 0.28 * _cos, 0.72, 1.28)
    # 其它台风的高空反气旋也会对本站制造风切(双台风靠近互相削弱的重要原因)。
    # **必须封顶**: 这个累加是对 1500km 内**每一个**台风做的。原来存活上限是 6,
    # 现在为了匹配观测生成率提到了 25 —— 25 个台风时有几个在 1500km 内就能把
    # shear_eff 累到 30-60kt, fs=exp(-((50-6)/21.4)^2)≈0.015 等于完全压制,
    # 于是风暴在低 MPI 区就到顶了(实测峰值时刻的 MPI 只有 76, 沿途最大 139)。
    _mut = 0.0
    for o in sim['tcs']:
        if o is tc or o['dead']:
            continue
        d = _tc_distance_km(tc, o)
        if d < 1500:
            f = 1.0 - d / 1500.0
            _mut += 3.0 * (0.3 + o['vmax'] / 155.0) * f
    shear_eff += min(_mut, 8.0)
    # **切变抑制的正确函数形式**(I1 实测标定, 2026-09-17):
    #   切变   0-5   5-10  10-15  15-20  20-25  25-30  30-40  40-50  50-60
    #   含RI%  7.8   7.1   6.5    5.6    3.4    3.3    1.8    0.3    0.1
    #   Δvmax  0.0   0.0   0.0    0.0   -5.0   -5.0   -5.0   -5.0    0.0
    # **中位数几乎不变(0~-5kt), 但 RI 率变了 78 倍** —— 也就是说切变压制的
    # 是"尾部爆发", 不是平均增长。原来的  是均匀乘在
    # 增长率上的, 在 S=50 时给 0.015 等于把中位数也砍没了, 函数形式根本不对。
    # 现在: 平均增长只受**温和**抑制(S=50 时 0.53), 切变的主要作用移到
    # 下面 RI 机制的门控上(见 gate_sh)。
    fs, gate_sh = shear_response(shear_eff)
    #: RI 机制的切变门控 —— 这一项才是复现"78 倍"的地方。
    #: S=5 时 0.95 / S=20 时 0.44 / S=40 时 0.037 / S=50 时 0.006
    # gate_sh 已由 shear_response 给出(见那里的标定说明)
    fd = math.exp(-((70.0 - env['rh']) / 42.0) ** 1.4) if env['rh'] < 70 else 1.0
    dV *= fs * fd
    # 陆地衰减。**I4 标定**: 实测观测(2013-2024, n=378)登陆后 24h 的 Δvmax
    # 中位数只有 **-15kt**, 即 -0.62 kt/h。原系数 0.12 在 vmax=60/land=0.5 时
    # 给 -3.6 kt/h、vmax=100 时 -6.0 kt/h —— **强 6-10 倍**, 风暴登陆后 17 小时
    # 就死, 于是寿命只有观测的 1/3(WP 74h vs 210h)、峰值上不去。
    # 反推: vmax=65/land=0.6 时两项合计应为 0.62 → k1=0.012, k2=0.002
    # 轮次 159: `_ds` = 地形对数衰减的放大因子, **必须先在这里无条件初始化** ——
    # 下面减弱支的封顶项也要用它, 而那个分支不一定经过 `env['land'] > 0.02`。
    _ds = 1.0
    # ── S12-④：**登陆后宽限期 / 渐变**（沙盒 `LAND_GRACE_HOURS=12` + `LAND_FADE_HOURS=24` 的结构）──
    # 我们原来的陆地衰减**只看 `env['land']` 与地形**，**没有"登陆多久"这一维** ⇒
    # 一沾地就开始掉强度（沙盒有 12 h 宽限、再用 24 h 渐入满衰减）。
    # ⚠ **默认 `landGraceH = 0.0` ⇒ `_lf_ramp = 1.0` ⇒ 与本改动前【逐位相同】**。
    #   理由：本文件 `0.012` 那个系数是拿观测"登陆后 24h 中位 −15 kt（n=378, 2013-2024）"
    #   **反推整条系数**的（见下方注释）。若直接给它加宽限期而**不重标定**，
    #   等于把标定口径改掉 —— 那正是 ① 里"照抄沙盒 = 回退到坏标定"的同一类错误。
    #   ⇒ 这里只**提供旋钮**（与 `terrainDecayK`/`terrainDecayMax` 同一模式），
    #     要用必须先用同一批观测（n=378）重标 `0.012` / `0.002` 两个系数。
    #   旋钮：`landGraceH`（小时，默认 0=关）、`landFadeH`（渐入时长，默认 24）。
    _lf_ramp = 1.0
    _tp_lf = sim.get('params') or {}
    _grace = float(_tp_lf.get('landGraceH', 0.0))
    if _grace > 0.0 and tc.get('landfall') is not None:
        _hrs = max(0.0, float(sim['t']) - float(tc['landfall'].get('t', sim['t'])))
        _fade = max(1e-6, float(_tp_lf.get('landFadeH', 24.0)))
        _lf_ramp = max(0.0, min(1.0, (_hrs - _grace) / _fade))
    if env['land'] > 0.02:
        # 轮次 159: **山脉高度加快登陆衰减**。
        # ⚠⚠ 这里一度是**裸倍率** `* _ds`(`decay_scale` 最高 8.49x), 被地形复核判定
        # 为**严重**问题(VERIFY_TERRAIN_FIX.md P1): 上面那个 `0.012` 是拿观测
        # "登陆后 24h 中位 -15kt" **反推出来的整条系数**, 再乘裸倍率会把衰减系统性
        # 放大 1.8-4x —— 台湾中央山脉格 vmax=100 时该项从 -1.19 变成 **-4.80 kt/h**
        # (6h 一步 -28.8kt, 而标定口径是 -7.2)。
        # 改成**有界增量**: 只放大"超出平原的那一部分"(`_ds - 1`), 再对总放大封顶。
        #   _ds_eff = min(cap, 1 + k*(_ds_raw - 1))
        # 默认 k=0.25 / cap=2.0 ⇒ 台湾(3025m, _ds_raw=4.03)≈**1.76x**、
        # 吕宋(2320m)≈1.58x、日本(2503m)≈1.63x, 任何地形最多 2x。
        # ⚠ 这是一阶**有界**取法, 不是观测标定 —— 真正的 k 应当用台湾/吕宋/日本
        #   的登陆观测反推。两个旋钮已做成 `sim['params']` 可覆盖
        #   (`terrainDecayK` / `terrainDecayMax`), 便于日后标定而不必改代码。
        # 用 **elev_max**(契约把"峰"给登陆衰减; "面"效应给摩擦, 见引导权重那处)。
        # 默认关 ⇒ `_ds_eff` 恒为 1.0 ⇒ 与原式逐位一致。
        _ds = 1.0
        _tp = sim.get('params') or {}
        if _tp.get('terrainOn') and _TG.available():
            _raw = _TG.decay_scale(
                float(_TG.elev_at(tc['lon'], tc['lat'], use_max=True)))
            _k = float(_tp.get('terrainDecayK', 0.25))
            _cap = float(_tp.get('terrainDecayMax', 2.0))
            _ds = min(max(1.0, _cap), 1.0 + _k * max(0.0, _raw - 1.0))
        dV -= tc['vmax'] * min(1.0, env['land']) * 0.012 * _ds * _lf_ramp
    if env['land'] > 0.3:
        dV -= tc['vmax'] * 0.002 * _lf_ramp
    if env['sst'] < 26.5:
        # I3 标定(观测 1980-2024, 24h Δvmax 分箱): SST 25.5-26.5 时观测只掉
        # -0.8 kt/24h。原系数 0.04 在 sst=26、vmax=50 时给 -24 kt/24h —— **强 30 倍**。
        # 0.0015 复现观测: 50×0.5×0.0015 = 0.0375 kt/h = -0.9 kt/24h ✓
        # 冷海衰减乘子 0.0015 -> 0.006(2026-09-21, 逐格诊断):
        #   48-64kt 档冷海(25-26.5C)格: 模型净中位 **-1.21** vs 观测 **-5.00**(0.24x)。
        #   即模型的"环境区分度"太弱 —— 有利格过度增长(另见 tau), 不利格衰减不足。
        dV -= tc['vmax'] * (26.5 - env['sst']) * 0.0060
    # I3 三轮标定: **强风暴衰减不足**。观测分强度组的 24h Δvmax:
    #   V 80-110 → -9.1 kt,  V 110-200 → -22.4 kt
    # 而模拟只有 -3.4 / -3.6 kt —— 强台风在模拟里"挂在顶上不下来",
    # 于是 ACE 冲到 15.7(观测 10.2)。这里补一个随强度平方增长的衰减项。
    # 系数由观测反推: V=130 时需要 -0.93 kt/h, (130-85)²×4e-4 = 0.81 ✓
    # 阈值从 85 提到 105: 85 介入会把**尾部也压掉**(实测 P95 从 120 掉到 105)。
    # 观测的特征是"少数风暴冲到很高、然后快速衰减", 所以衰减只在很高强度介入,
    # 尾部靠随机强迫形成 —— 这样 ACE 和 P95 才能同时对上。
    # 三轮扫描的取舍(峰值误差合计 / ACE 误差):
    #   无衰减 + noise1.20 → 峰值最准(合计 49) 但 ACE 15.7(超 5.5)
    #   阈值 85 + 4e-4     → 峰值 68, ACE 11.3
    #   阈值 105 + 6e-4    → 峰值 71, ACE 10.8
    # 取中间: 阈值 95 + 3e-4 + noise 1.35, 两头兼顾。
    # 阈值与系数都放松: 尾部要靠**环境方差**形成, 确定性刹车太紧会把 P95 压掉
    # (实测 95/3e-4 时 P95 只有 98, 观测 140)。ACE 由方差和寿命共同约束。
    if tc['vmax'] > 108.0:
        _d = tc['vmax'] - 108.0
        dV -= _d * _d * 1.6e-4
    # 高龄衰减(10 天后)
    if tc['age'] > 240:
        dV -= 0.45 * (tc['age'] - 240) / 260.0 * (0.5 + tc['vmax'] / 155.0)
    # 弱台风持续不利环境 → 结构退化累积(解决"永不消散")
    weak = tc.get('weak', 0.0)
    # I3 标定: 阈值原为 sst<26.5 / shear>23, 是按**旧合成场**定的(旧切变最大
    # 24.5kt、多数 <15)。真实切变均值 20.5kt,  太常见 → weak 一直涨 →
    # 风暴被过早削弱。观测显示切变 25-30kt 时 24h 平均仍是 +0.2kt、衰减率只
    # 18%(0-5kt 时是 14.6%), 影响要弱得多; SST 同理(25.5-26.5 只 -0.8kt/24h)。
    # ── 路径线 step ③：把 shear 门做成旋钮 `weakShear`（**默认 35.0 ⇒ 与本改动前逐位相同**）──
    # 背景（`output/exploration/g_path_sim.md` §19-20）：这个门当初由 23 **刻意提到 35**
    # 是为了压"weak 一直涨 ⇒ 风暴被过早削弱"。但它同时让 `weak > 20`（路径 1 的第三子句）
    # **几乎不可达**：实测 `weak` 峰值 **18.5**，离门 20 **只差 1.5** ⇒ 于是 SD/SS 恒 0。
    # ⇒ Lead 裁定：**不钝降**，做**分级实验**（35→33→30→28），且**必须同时报
    #   A=SS 触发数 与 B=过早削弱的代价**，否则无法判断该不该留 ✓
    _weak_sh = float((sim.get('params') or {}).get('weakShear', 35.0))
    if tc['vmax'] < 60 and (env['sst'] < 25.5 or env['shear'] > _weak_sh
                            or env['land'] > 0.25):
        weak += dt / 6.0          # 每 6h +1
        dV -= tc['vmax'] * 0.004 * weak
    else:
        weak = max(0.0, weak - dt / 24.0)
    tc['weak'] = weak
    # ── "低强度快速填塞"(`dV -= 0.08*(45-vmax)`)已按目标 ① 删除(轮次 28) ──
    # 逐格 v2 验收: 该式在 vmax=20kt 时给 **-12 kt/6h**, 而观测 <34kt 档的降均值只有
    # **-0.70**(模型实测 -6.33, 差 9 倍)。它当初用来修"TD 段太长", 但那个问题已由
    # 阶段分类(TS/TD→LO 结构路线)单独承担, 不该靠"把弱系统打穿"来解决。
    # ══════════ 减弱支: **显式速率 r(V) × 环境乘子**(目标 ① 的设计) ══════════
    # 2026-09-21(轮次 30)补齐。此处原来只剩一段注释 —— 代码在之前的改动里被删掉了,
    # 于是模型**根本没有强衰减项**, 衰减只剩基础松弛 (vpi-V)/TAU_WEAKEN_H。
    # 证据: 把 TAU_WEAKEN_H 从 84 砍到 30(**2.8 倍**), 峰值后减弱只从 -1.4 动到 **-0.9**
    # kt/6h —— 因为 (vpi-V) 本身很小(风暴没到 MPI 就被别的项拉住了), 衰减被"MPI 还在上面"屏蔽。
    # 观测(峰值后减弱, kt/6h 中位; INTENSITY_LIFETIME.md / stat_intensity_profile.py):
    #   34-48: -5 | 48-64: -5 | 64-80: -10 | 80-100: -10 | >=100: -12.5
    # 这是**由风暴自身动力(眼墙置换/冷尾流/干空气)决定的速率, 与 MPI 松弛无关**,
    # 所以必须写成显式 r(V), 不能用 (vpi-V)/tau。
    # 低强度端要温和(观测 <34kt 档降均值只有 -0.70 kt/6h), 故 r(V) 从 34kt 的 -2 线性长到
    # 100kt 的 -12.5; 环境乘子(相对暖海基线, 由观测分层反解): 陆地 ×3-5、冷海 ×2、强切变 ×2。
    if vpi < tc['vmax']:
        _v = float(tc['vmax'])
        # **减弱基线分两段**(轮次 73, 已验证): 34->80 缓升(-2 -> -5.5), 80->100 陡升(-5.5 -> -10)。
        # 依据: 3 种子 6782 步的可比口径显示 80-100 档降中位 -11.4 而观测只有 -5.0(2.3 倍偏深),
        # 而 100+ 档 -9.3 与观测 -10.0 吻合 ⇒ 形状是"到 80 只有 -5、100 以上才 -10"。
        # **年尺度已验证**: 3 中性种子 130+ = 11%/12%/11%(基线 9%/9%/10%)⇒ 真改善, 保留。
        # 注意: 轮次 75 曾在同档继续按观测降中位改形(48-100 平在 -5),
        # 但步级复测自相矛盾(64-80 反而从 -6.2 变 -8.0)⇒ **已回退**, 见 STATUS.md 第九节。
        _r6 = -(2.0 + 3.5 * clamp((_v - 34.0) / 46.0, 0.0, 1.0))
        if _v > 80.0:
            _r6 = -5.5 - 4.5 * clamp((_v - 80.0) / 20.0, 0.0, 1.0)
        # 环境汇: **有界叠加 + 总量封顶**(改自连乘, 2026-09-21 轮次 31)。
        # 连乘版(陆地 ×3-5 × 冷海 ×2 × 强切变 ×2)最高可达 **×20**, 实测把中档压穿:
        #   64-80 档降均值 **-11.64** vs 观测 **-5.89**、80-100 档 **-15.13** vs **-8.76**。
        # 观测的陆地档总量只有 -15~-25 kt/6h, 冷海 -10、强切变 -10 —— 是**各自有界的汇**,
        # 不是可乘的系数。改为相加并封顶 -25(观测陆地档上界)。
        _sink = 0.0
        if float(env['land']) > 0.3:
            # 轮次 159: 登陆汇按地形放大 —— **必须乘在封顶项里, 不能只乘在上面的
            # `dV` 上**。实测(test-hardener, 2026-09-29): 强台风登陆时
            # `vpi(陆地抑制后) < vmax` 恒成立 ⇒ 必走减弱支, 而末行
            # `dV = min(dV, _r6/6.0)` 会**整段替换** `dV` —— 于是中段那句
            # `dV -= vmax·land·0.012·_ds` 连同 0.002 与冷海项一起被丢弃,
            # 地形在**最该生效的场景(强台风撞山)**里完全无效(vmax0=65 时开关两臂
            # 差精确为 0)。放到 `_sink` 里就仍受下面的 `-18` 封顶约束,
            # 既生效又不会重蹈 P1"裸倍率放大 4 倍"的覆辙。
            _sink -= (6.0 + 7.0 * min(1.0, float(env['land']))) * _ds
        # 冷海门槛 26.5 -> 25.5、斜率 8.0 -> 5.0(轮次 37, 多种子 p10 实测):
        # 26.5°C 在西太 25-30°N 太常见, 于是 64kt 以上的风暴**几乎每一步**都被叠上冷海汇,
        # 实测 p10 到 −19.1/−23.4/−25.5, 而观测只有 −10/−15/−15。
        if float(env['sst']) < 25.5:
            _sink -= min(6.0, 5.0 * (25.5 - float(env['sst'])))    # 冷海: 最多 -6
        if float(env['shear']) >= 30.0:
            _sink -= 5.0                                           # 强切变: -5
        # 总量封顶 −25 -> −18: 观测降侧 p10 最深只有 −15, −25 允许了观测中不存在的极值。
        # "按 MPI 缺口平滑响应"在轮次 69 用 3 个中性种子重测过: 130+ = 8% / 8%,
        # 而同期基线是 9% / 9%(噪声 +-5 个百分点)⇒ **统计上中性**, 无改善。已回退。
        # (轮次 44 那次回退的理由"9%->7%"统计上不成立, 但结论碰巧是对的 ——
        #  这正是"回退也必须先看统计功效"这条规则的又一个例证。)
        _r6 = max(-18.0, _r6 + _sink)
        dV = min(dV, _r6 / 6.0)             # kt/h(唯一施加点在函数末, 随机项在其前)

    # 日变化(03 时对流峰值)
    lh = local_hour(sim, tc['lon'])
    dV *= 1 + 0.05 * math.cos(2 * math.pi * (lh - 3) / 24.0)
    # 随机强迫(kt)
    # I3 标定: 观测的 Δvmax 分布**高度右偏** —— 平均增长很小(V=30kt 时
    # +4.6kt/24h), 但峰值能到 145kt, 尾部完全由少数快速增强事件撑起。
    # 单一确定性 tau 拟合不了这种分布: tau 小则均值偏高+RI 过多, tau 大则尾部塌掉。
    # 所以 tau 取中值(拟合均值), 把**随机强迫加大**让尾部自己长出来。
    _jit = (sim['gauss']() * sim['params']['random'] * 1.90
            * (0.5 + tc['vmax'] / 155.0))
    # ── 幅度尺度标定(轮次 38): 系数 1.35 -> 1.90 ──
    # 依据 **剔除零步后** 的观测(tools/obs_zero_check.py, 与 rate_tables 同源同口径):
    #   零步比例随强度递减: <34 **60%** / 34-48 38% / 48-64 27% / 64-80 25% / 80-100 14% / 100+ 9%;
    #   剔除零步后 **升均值 ≈ −降均值**(34-48: +6.20 / −6.24; 64-80: +8.27 / −9.81;
    #   80-100: +10.89 / −11.00)⇒ **观测的步长分布没有偏度**。
    #   而模型各档幅度一致是观测的 **0.52-0.68x** ⇒ 这是**纯尺度**问题。
    # (轮次 35-37 去查"偏度"是被 rate_tables 的 up/down 划分稀释所致 —— down 含零步,
    #  所以那张表的"升占比 35% / 降均值 −2.63"不可直接与模型比。)
    dV += _jit
    # ══ 阶段 5/6: 核心组织度 + 雨带活动 + EMBC + 屏障突破 ══
    #
    # **删掉了原来的 **(Kaplan-DeMaria 判据 + 开一个 20-34h 的窗口加固定
    # 2.24 kt/h)。那套没有对应的物理过程 —— 它只是"为了造出右偏的强度分布"而拍
    # 的机关。真实的快速增强来自下面四条, 每一条都有触发条件和前置状态:
    #   ① 集中加热   —— RMW 小、加热效率高(2025 陈&吴: 平流层下层高位温空气被
    #      对流破顶泵进上层暖心)
    #   ② EMBC 释放  —— 底层眼已完整但高层无眼, 被"压缩"着; 风速积累够或风切退去
    #      就像弹簧释放(楼主原话)
    #   ③ 屏障突破   —— 半卷半塌(Zeb 1998): BF 途中惯性稳定度屏障被打破
    #   ④ 置换后重整 —— ERC 完成后内眼重新收紧
    #
    # **核心组织度 core_org ∈ [0,1]**: 帖子的结论是 BF(卷眼)/CF(塌眼) **不是
    # 二分而是连续谱**, 两者都是靠卷绕建眼墙、可以随时互相转化。所以不设状态机,
    # 用一个连续量表达"核心有多紧凑/对流有多集中"。
    _org = tc.get('core_org')
    if _org is None:
        # 初值由生成时的环境定: 干/切变大 → 组织度低(走 BF 的表现);
        # 湿/切变小 → 组织度高。东风波出身偏 CF、季风低压出身偏 BF 就是这个。
        _org = clamp(0.45 + 0.30 * clamp((env['rh'] - 55.0) / 25.0, -1, 1)
                     - 0.20 * clamp((env['shear'] - 20.0) / 25.0, -1, 1)
                     + 0.10 * sim['gauss'](), 0.10, 0.95)
        tc['core_org'] = _org

    # ── 雨带活动 A ──
    # 雨带是上限的敌人: BF → 非对称对流 → VRW → 内雨带 → 外雨带 → SBC → SEF → ERC,
    # 每一次置换眼径更大、上限更低("剥洋葱")。
    # **对风切/干空气是非单调的**(帖子反复强调"完美的环境未必是最完美的"):
    # 适当的风切/干空气**抑制**雨带(有利), 过强则把核心切散(不利)。
    _band_env = (clamp((env['rh'] - 50.0) / 30.0, 0.0, 1.4)
                 * clamp((env['sst'] - 26.0) / 2.5, 0.0, 1.4)
                 * clamp(tc['vmax'] / 70.0, 0.0, 1.5))
    _sh = env['shear']
    if _sh < 12.0:
        _band_sup = 0.75          # 太干净: 雨带疯长
    elif _sh < 28.0:
        _band_sup = 1.00 - 0.45 * (_sh - 12.0) / 16.0   # 适当风切抑制雨带
    else:
        _band_sup = max(0.35, 0.55 - 0.01 * (_sh - 28.0))  # 过强: 核心被切散
    _dry = clamp((65.0 - env['rh']) / 30.0, 0.0, 1.0)
    A = _band_env * _band_sup * (1.0 - 0.35 * _dry)
    tc['bandA'] = A

    # core_org 演化: 集中加热提升组织度, 雨带与风切散乱拉低
    _heat = clamp((tc['vmax'] / max(1.0, vpi)) * (22.0 / max(8.0, tc['rmw'])), 0.0, 2.0)
    tc['core_org'] = clamp(_org + (0.045 * _heat - 0.030 * A - 0.020 * max(0.0, _sh - 25.0) / 20.0)
                           * dt, 0.05, 1.0)
    # ── U-S12-5：**陆地组织度衰减**（与风速**分开**）—— 沙盒"结构先垮、风速后掉" ──────
    # 缺口（实测）：上面的 `core_org` 演化只由 集中加热 / 雨带 A / 风切 驱动，
    #   **`env['land']` 完全不出现** —— 登陆后组织度只通过 `vpi(陆地抑制)` 间接、缓慢地降，
    #   没有"撞山/上岸后结构先散"的独立通道。沙盒有 `LAND_ORGANIZATION=0.5` /
    #   `MOUNTAIN_ORGANIZATION=0.5` 两条**直接**衰减。
    # ⚠⚠ **默认 `landOrgDecay = 0.0` ⇒ 整段不执行 ⇒ 与本改动前【逐位相同】**。
    #   理由与 ④ 的 `landGraceH` 完全相同：本文件的强度项系数（`0.012`/`0.002`）是
    #   **反推自"登陆后 24h 中位 −15 kt（n=378）"** 的；单独加一条组织度汇会**改变
    #   那条反推的前提**（组织度→`_heat`→vmax 是耦合的）⇒ **启用前必须重标**
    #   （记 U-S12-6：至少 `0.012`/`0.002`/`0.045`/`0.030` 四个系数要一起重看）。
    #   本旋钮的含义：**组织度在陆地每小时直接衰减多少（1/h）**。
    #   ⚠⚠ **速率【不能】照抄沙盒的 `LAND_ORGANIZATION=0.5`** —— 实测（248 个真实登陆个例，
    #   见 output/exploration/s15_org_decay.md）：取 `landOrgDecay=0.5` 时
    #   **组织度半衰 = 0.91 h**，而同条件**风速半衰 = 303.6 h** ⇒ 组织度快 **约 330 倍**
    #   —— 那不是"结构先垮、风速后掉"，是"结构瞬间归零"。⇒ 沙盒那个 0.5 的**语义未取到**
    #   （不可能是 per-hour 线性减量；更像倍率/目标值），**不要拿它当速率**。
    #   本旋钮速率**必须标定**（见 U-S12-6），**当前没有可用默认值**。
    #   山地放大：`terrainOn` 时用与强度衰减同一个 `terrain.decay_scale(elev_max)`（有界 2x）。
    _tp_org = sim.get('params') or {}
    _org_land = float(_tp_org.get('landOrgDecay', 0.0))
    if _org_land > 0.0 and float(env['land']) > 0.02:
        _org_mul = min(1.0, float(env['land']))
        if _tp_org.get('terrainOn') and _TG.available():
            try:
                _org_mul *= min(2.0, float(_TG.decay_scale(
                    float(_TG.elev_at(tc['lon'], tc['lat'], use_max=True)))))
            except Exception:
                pass
        tc['core_org'] = clamp(tc['core_org'] - _org_land * _org_mul * dt, 0.05, 1.0)
    _org = tc['core_org']

    # ── EMBC(嵌匿中心)──
    # 底层眼已完整、高层无眼: 被深厚卷云/风切不对称压着。一旦风速积累够或风切退去,
    # 暖心聚集触发强正反馈 → RI 甚至 ERI。**这不是"开窗口加固定值"** ——
    # 它有前置状态(底层眼完整)、触发条件(风速或风切)、且有物理含义。
    if not tc.get('embc') and tc['vmax'] > 55.0 and env['land'] < 0.1             and _org > 0.55 and shear_eff > 9.0 and tc['rmw'] < 40.0:
        # ^ 2026-09-20: 原来要求 shear>14, 实测太严(195 个合格步只换来 15 次形成,
        #   0.45 次/风暴; 观测 1.05)。文献里嵌匿中心/中央冷云盖在 5-15 kt 中等切变下
        #   同样常见, 故放到 9 kt(保留"必须有切变不对称"这个物理前提)。
        # 高切变/深对流覆盖把高层眼压住
        # 形成概率受切变门控 —— 这正是"78 倍"的来源。
        # **2026-09-20 实测: 这个 0.016 太小了** —— 前置条件(风切>14 且 org>0.55 且
        # rmw<40 且 vmax>55)在风暴一生里只有 2-3 步能满足, 4%/步 ⇒ 一生触发概率 ~10%,
        # 于是"RI 机制实际上从没触发过"(6 个手工强台跑 6 天, EMBC 与屏障突破都是 False),
        # 而观测里 45-63% 的风暴都经历过 RI。按"一生一次"反推, 每步应 ~7-8%
        # (0.030*6h*gate, gate 0.42 → 7.6%)。屏障突破同比例调整。
        if sim['rng']() < min(0.6, 0.10 * dt * gate_sh):
            tc['embc'] = True
            tc['embc_sh'] = shear_eff          # 记住形成时的切变(供"切变松了"这个释放条件)
    if tc.get('embc'):
        # 释放条件: 风速积累到一定程度, 或风切退去。
        # **2026-09-20 实测的瓶颈**: 形成要 shear>14, 而原来的释放只认 shear<10 或
        # vmax>85 —— 两道门几乎互斥(实测 3130 个风暴·步里 EMBC 激活 129 步, 却几乎
        # 没有一次走到释放), 于是"RI 机制存在但从不爆发"。真实物理是: 切变相对**形成时**
        # 明显松弛(不必等到 <10)就是嵌匿中心抬升的时机。现在补上"切变松弛 ≥6 kt"这条。
        _sh0 = float(tc.get('embc_sh', shear_eff))
        if (tc['vmax'] > 80.0 or shear_eff < 12.0
                or shear_eff <= _sh0 - 6.0):
            tc['embc'] = False
            # 释放脉冲。实测(1997 全年, 接入环流型后 RI 风暴口径只有 ~14%,
            # 观测 44.9%): 环境变率是真的了, 但机制太弱抓不住有利窗口。
            # 6.0 → 11.0 → **8.2**(11.0 时 P95 冲到 171, 观测 140, 过冲了;
            # 8.2 对应一次释放跨两个 6h 步约 +15~28 kt)。
            _burst = 0.9 + 0.8 * _org           # kt/h
            # **爆发要随"离 MPI 还有多远"衰减**(2026-09-20 加)。
            # 原因: 放开前置(shear>9)与触发率(0.10)之后, RI 过程数确实对上了观测(1.08),
            # 但**不同种子之间开始失控** —— seed 20250801 的 P50 是 0.73 倍, seed 11 却是
            # 1.57 倍、ACE 4.0 倍(风暴被一串爆发推到 185 kt 硬上限)。物理上 RI 不可能把
            # 风暴推过它自己的 MPI, 所以这里按 (1 - vmax/MPI) 衰减: 接近 MPI 时爆发自然消失,
            # 既保留"有利窗口里能爆发", 又给出一个**由环境决定的刹车**而不是硬限幅。
            _mpi_now = max(1.0, float(vpi))
            _head = max(0.0, 1.0 - tc['vmax'] / _mpi_now)
            # **有界化(2026-09-21, 目标 ①)**: 原来这一项无标定(_burst*8.2 峰值 ~84 kt/步),
            # 钉到观测的高档升均值上界(80-100 档 +11.04、100+ 档 +12.62 kt/6h)⇒ 12 kt/6h。
            dV += min(_burst * 8.2 * _head, 12.0 / 6.0)
            tc['core_org'] = clamp(_org + 0.15, 0.05, 1.0)
        else:
            dV -= 0.25                          # 被压着: 强度暂缓

    # ── 屏障突破(半卷半塌)──
    # Zeb(1998)、Gay(1992)、Bavi(2026)那一类: 原本走 BF 路径, 中途低层入流突然获得
    # 突破外核惯性稳定度屏障的能力 → 加热集中到高效区 → 深对流填塞旧眼 + 强制转
    # 塌陷式。楼主原话"半卷半塌的增强速度会相当惊人"。
    if _org < 0.45 and tc['vmax'] > 60.0 and env['land'] < 0.1             and shear_eff < 8.0 and env['rh'] > 68.0:
        if sim['rng']() < 0.017 * dt * gate_sh:
            tc['core_org'] = clamp(_org + 0.45, 0.05, 1.0)
            tc['rmw'] = max(2.0, tc['rmw'] * 0.72)     # 屏障一破, 眼迅速收紧(下限 6->2)
            tc['barrierBreak'] = True

    # ── ERC 触发(骨架)──
    # 触发靠**雨带活动 A**, 而不是原来那个"接近 MPI + 随机数"。
    # 帖子的机制链: 外雨带组织化 → SBC → SEF → ERC。
    _erc_cd = tc.get('ercCD', 0.0) - dt
    tc['ercCD'] = max(0.0, _erc_cd)
    # **ERC 重构(2026-09-21, 用户给的机制)**:
    #   · 触发概率 ∝ (RMW_ref/RMW)^β —— **眼小极易**(杜鹃主巅 RMW 2.5nm, 置换仅 45 分钟);
    #     原来写成  才触发, 等于**把小眼全挡在门外** —— 与观测机制相反, 是 bug。
    #   · T_ERC = T_ref·(RMW/RMW_ref)^α, **α≈1.7**(由"2.5nm→0.75h / 20nm→24h"反解)。
    #   · 两型: **融合式**(小眼多半走这条)强度几乎不掉、快; **普通式**大掉、慢。
    #   · 上游用"暖海 × 接近潜在上限"的**通量/雨带代理**, 不再用凭空的 。
    # **通量代理改为基于强度**(2026-09-21 轮次 48)。原来乘的是
    # (1 - |vpi - vmax| / 40) —— 要求风暴在**自己 MPI 的 40kt 以内**。
    # 而 MPI 字段饱和在 195, 90-120kt 的风暴 |vpi - vmax| 常在 75-105
    # ⇒ 该项被钳到 0 ⇒ **ERC 几乎永不触发**(实测 150 天 / 217 风暴只有 4 次)。
    # ERC 的观测条件是**风暴本身够强**(通常 >=100kt), 不是"离 MPI 近",
    # 所以改用强度型通量代理(85kt 起, 110kt 饱和)。
    _flux = (max(0.0, min(1.0, (float(env['sst']) - 26.0) / 3.0))
             * max(0.0, min(1.0, (float(tc['vmax']) - 85.0) / 25.0)))
    if (not tc['erc'] and _erc_cd <= 0.0 and tc['vmax'] > 85.0
            and env['land'] < 0.15 and env['sst'] > 26.0):
        _rmw = max(2.0, float(tc['rmw']))
        _p = 0.010 * dt * _flux * (20.0 / _rmw) ** 1.5        # β = 1.5
        if sim['rng']() < _p:
            _merger = sim['rng']() < min(1.0, 8.0 / _rmw)     # 小眼 ⇒ 多半融合式
            _T = max(0.75, min(48.0, 24.0 * (_rmw / 20.0) ** 1.7))
            tc['erc'] = {'T': _T, 't': 0.0, 'merger': bool(_merger),
                         'rF': (0.35 + sim['rng']() * 0.25) if _merger
                               else (1.35 + sim['rng']() * 0.35),
                         'dip': 0.06 if _merger else (0.18 + sim['rng']() * 0.12)}
            tc['ercCD'] = _T + 24.0 + sim['rng']() * 60.0     # 不应期: 本次时长 + 1-3.5 天

    v_target = 1.0
    if tc['erc']:
        tc['erc']['t'] += dt
        x = tc['erc']['t'] / float(tc['erc']['T'])
        _shape = math.sin(math.pi * min(1.0, x))
        # **低谷改为强度上的真实亏损**(2026-09-21 轮次 49)。
        # 原来写的是 v_target = 1 - dip*sin(pi*x), 乘在 **dV** 上 —— 那只让增长**变慢**,
        # 强度在事件期间仍**净增**(实测 +22.9 / +27.0 kt), 而观测签名是**真的掉**
        # (普通式 145->120、160->130; 融合式 140->135、160->155)。
        # 现在按事件时长把**目标总亏损**分摊成 sin 形状的负速率:
        #   ∫₀^T (L/T)(π/2) sin(πt/T) dt = L  ⇒ 事件期间累计掉 L kt。
        # 融合式内眼被外眼取代但残余环流仍在, 掉得少(约 5kt); 普通式掉 15-30kt。
        # 关键: 事件期间**先关掉增强通道**。实测(轮次 49)只加亏损是不够的 ——
        # 背景增强会把亏损抵消(融合/普通仍是 +5.8/+2.2)。物理上内眼被隔绝时,
        # 增强本来就中止了, 所以正 dV 归零, 再由亏损决定净变化:
        #   净变化 = -_loss  ⇒ 融合 -5kt、普通 -25kt(观测 140->135 / 145->120)。
        if dV > 0.0:
            dV = 0.0
        _loss = 5.0 if tc['erc'].get('merger') else 25.0
        dV -= (_loss / max(1.0, float(tc['erc']['T']))) * _shape * (math.pi / 2.0)
        if x >= 1:
            tc['erc'] = None
            # 置换完成后外眼收缩、强度重新恢复。观测: 新峰值一般不超过旧峰值, 故只给小幅。
            # 2.0 -> 1.0(轮次 51): dV 的单位是 kt/**小时**, 2.0 意味着一整步 +12kt,
            # 会盖过融合式只有 5kt 的亏损(实测融合式净变化 +0.9, 目标 -5)。
            dV += 1.0
            # 置换完成后眼径更大 → 上限更低(eta_rmw 会自动体现)
    # ══ **必须在所有 dV 项之后** ══
    # 教训(犯过两次): 这两条规则原来插在 dV 组装的中途, 后面的随机强迫 
    # 会把它们覆盖掉 —— 实测"增速上限"与"峰值后减弱速率"都形同虚设(减弱只 -1.0 vs 观测 -12.5)。
    # 现在统一放在最终施加之前, 任何后续项都无法绕过。
    # ── 弱系统**每步增长上限**(必须放在**所有** dV 项之后: RI 突发/EMBC/集中加热
    # 是在上面才加到 dV 上的 —— 第一版插在中间, 于是上限被突发项绕过, 实测分布一模一样) ──
    # 观测前生期: 扰动 42 h + 低压 15 h = 57 h, 我们只有 30 h 且 TD 中位 0 h。
    #   vmax < 20kt: ≤1.5 kt/6h(扰动要慢慢组织);  20-34kt: ≤3.0 kt/6h(TD 档要走 ~4 报)
    # 只压增长、不碰减弱; ≥34kt 不限(RI 尾部分布不受影响)。
    # ── 本处原有的一组补丁已按目标 ① 删除(2026-09-21, 轮次 28) ──
    # 删掉的是: `disturb` 强度压制(DISTURB_CAP)、弱系统每步增速上限(_cap_step=2.0)、
    #           `peakV` 记账、以及 `>=100kt` 的减弱钉子(`dV = min(dV, -5/6)`)。
    # 删的依据不是"设计上不优雅", 而是**逐格 v2 验收直接测出它们把分布形状弄反了**:
    #   · 低档封顶 ⇒ <34kt 档升均值 **+0.54** kt/6h, 观测是 **+5.30**(差 10 倍);
    #   · >=100kt 钉子 ⇒ 100+ 档升占比 **0%**, 观测是 **33%**(均值 +12.62)——
    #     钉子强制每一步都衰减, 高位永远没有增强步, 于是"停留时间"和"顶部被削"两件事
    #     都被做错了(均值 -6.88 vs 观测 -3.41)。
    # 它们的职责现在由: 增长支的分环境上限(见上面的 RI 段)、基础松弛、以及减弱支承担。
    # 上限 185 -> 250(用户: 185 是硬上限就去掉)。实测 P99=184/最大=185 ⇒ 顶在墙上。
    tc['vmax'] = clamp(tc['vmax'] + dV * dt * v_target, 6, 250)
    tc['pmin'] = pmin_from_v(tc['vmax'])
    # RMW 不再每步重算 —— 它是状态量, 在函数开头演化。ERC 的外扩也已在那里
    # 通过"平衡半径 × 1.55sin"处理, 这里不再连乘(连乘会指数发散)。
    tc['b'] = holland_b(tc['pmin'])

    # ── 变性过程 TC → SD(副热带) → SS/EX(温带) ──
    # 结构退化到一定程度且环境不支持时转为副热带/温带, 不再按热带增强
    # **两套标签必须分开**(用户 2026-09-21 报的全部阶段问题根因):
    #   cuClass = 结构(热带/副热带/温带/残余) —— 决定物理(受不受 MPI 驱动), 这里算;
    #   phase   = **ATCF 阶段码**(DB/WV/LO/TD/TS/TY/ST/EX/SS/SD/MD) —— 台账/显示口径,
    #             由 simulator/simcore/phase.py 每步统一判定(含强度上限与逐级减弱)。
    # 原来只有一个 'TC' 标签 + 显示时按 vmax 现推码, 于是 TD 从不出现、DB 能有 93 kt。
    phase = tc.get('cuClass', 'TC')
    if phase == 'TC':
        # **副热带分类必须要求副热带纬度**(2026-09-21 修): 原来只判 weak/陆地/冷海,
        # 于是西太沿岸(116E 菲律宾一带, land>0.6)的**新生热带风暴**第一步就被判成 SD,
        # 之后一路 SD/SS —— 台账里 TD/TS/TY 全被吃掉(实测序列 DB/20 → SD/20 → SD/25 …)。
        # 副热带系统的定义本来就在 20-35°: 加这道纬度门之后, 低纬新生系统留在 TC,
        # 阶段码才会按 DB/WV → TD → TS → TY 正常爬。
        # 2026-09-21 第二次收紧: 20° 还不够 —— 实测生成点整体偏北(模型西太中位 18.5°N,
        # 观测 12.7°N), 20° 门形同虚设, 副热带判定照样吃掉大量正常热带系统。
        # 改成 25° 起 + **必须有副热带/变性证据**(冷海或强切变), 只有纬度/陆地不算。
        _sub_lat = abs(float(env['lat'])) >= 25.0
        _sub_ev = (float(env['sst']) < 25.5 or float(env['shear']) >= 20.0)
        if _sub_lat and _sub_ev and (
                weak > 20
                or (tc['age'] > 168 and tc['vmax'] < 50
                    and (env['sst'] < 24.5 or abs(env['lat']) > 32))
                or (env['land'] > 0.6 and tc['vmax'] < 45 and tc['age'] > 24.0)):
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
        # vmax 变了: Holland 廓线半径/形状要一起重算, 否则气压场与风场不自洽
        tc['rmw'] = rmw_from_v(tc['vmax'])
        tc['b'] = holland_b(tc['pmin'])
    # ── **温带变性(ET)**: 高纬 + 冷海 + 强切变 + 尚强, 累积到 ET_HOURS 就转 EX ──
    # 只有还在热带相态的系统会走这一步; 变性**不可逆**(EX 不回 TS), 与观测一致。
    if phase == 'TC':
        # 温带变性的经典配料: 够北(≥28°)、还在较强(≥35kt)、环境有斜压性(切变≥20)。
        # SST 门槛原来定 25.5 —— 夏秋副热带海温常在 26-27, 于是**一个 EX 都出不来**
        # (用户: "我怎么连一个 wp 的变性台风都没看到")。放宽到 26.5 并保留切变条件,
        # 免得把还在暖池里正常发展的系统误判成变性。
        # **按斜压性判, 不按固定 SST 门槛**: 夏秋副热带海温常在 26-29°C, 用 sst<26.5
        # 会把整个季节的变性过程挡在门外(实测一个 EX 都出不来)。真实 ET 的配料是
        # "够北 + 强切变 + 仍强 + 海温已不足以维持纯热带结构":
        #   |lat| >= 30 且 sst < 27.5 且 shear >= 22 且 vmax >= 35, 持续 ET_HOURS 即变性。
        # ── 诊断线（Lead 2026-10-05）：`vmax >= 35` 做成旋钮 `etVmax`（**默认 35.0 ⇒ 逐位不变**）──
        # 用户说真实情况里"未登陆风暴以 LO/DB 被切死或转 EX/SS ≈ 六四开"，而我们的 EX 只 ~10%，
        # 说明**短板在 EX（路径 2）而非 SS（路径 1）**；而路径 2 里 `|lat|>=30 时 vmax>=35`
        # 是关键卡点 ⇒ 用这个旋钮做 35/30/25 三档诊断，判"门太高"还是"到那儿已太弱" ✓
        _et_v = float((sim.get('params') or {}).get('etVmax', 35.0))
        _et_s = float((sim.get('params') or {}).get('etShear', 22.0))
        _et_ing = (abs(float(env['lat'])) >= 30.0 and float(env['sst']) < 27.5
                   and float(env['shear']) >= _et_s and tc['vmax'] >= _et_v)
        if _et_ing:
            tc['etHours'] = float(tc.get('etHours', 0.0)) + dt
        else:
            tc['etHours'] = max(0.0, float(tc.get('etHours', 0.0)) - dt * 0.5)
        if tc['etHours'] >= ET_HOURS:
            phase = 'SD'                     # 先走副热带过渡(下一次判定会进 SS/EX)
            tc['etOnset'] = float(sim['t'])

    # ── 残余低压(LO)与消散 ──
    # 用户 2026-09-19: "消散到 TD 时直接消失, 而不是到 LO(也可以是 DB)再消散"。
    # 设计要点(踩过的坑):
    #   · **DB/TD 是强度档, 不是相态** —— 用 grade_kt(vmax) 显示, phase 保持 'TC',
    #     这样弱系统仍在强度方程里(可以继续发展);
    #   · LO 只给"正在减弱"的系统: 已命名过、或增长率为负。否则一个刚生成的
    #     扰动偶发掉到 21kt 以下就被锁进 LO(旧写法实测: 20kt 生成 → 24h 掉到
    #     12kt 冻死, 再也不发展);
    #   · LO 期间**允许重新发展**(实测观测里残余低压重回暖池再增强并不罕见):
    #     环境支持时回到 'TC'。
    _dvg = (vpi - tc['vmax']) / max(1e-6, tau) * fs * fd
    # **结构 LO 的门槛放宽**(观测: 进入 LO 的前一码里有 TS 2 次、起始强度中位 20kt/96%<34kt):
    # 登陆(land>0.6)或入冷海(sst<24)或结构严重退化(weak>60)时, 风还有 30-34kt 也该降为 LO。
    _lost_now = (float(env['land']) > 0.6 or float(env['sst']) < 24.0 or weak > 60.0)
    if (phase == 'TC' and tc['age'] >= 18.0
            and (tc['vmax'] < 21.0 or (tc['vmax'] <= 33.0 and _lost_now))):
        # 年龄门: 新生扰动偶发掉到 21kt 以下不该立刻变"残余低压"(旧注释里踩过这个坑,
        # 但当时只挡了未命名的; 现在再加 18h 年龄门, 与观测"扰动段先持续一两天"一致)
        _named = bool(tc.get('named') or tc.get('wasNamed'))
        if _named or _dvg < 0.0:
            phase = 'LO'
            tc['loHour'] = sim['t']
    elif phase == 'LO':
        # 门槛原来 _dvg>0.05 且 vpi>45 —— 发展中扰动常常够不到, 于是被**锁在残余低压**里:
        # 物理上不再受 MPI 驱动 → 再也起不来, 阶段码也一路 LO(实测 TD 中位 0 h 的主因之一)。
        if _dvg > 0.02 and vpi > 38.0 and env['land'] < 0.4:
            phase = 'TC'                     # 回到有利环境 → 重新发展(去掉 LO 计时)
            tc.pop('loHour', None)
    if phase == 'LO':
        # 残余低压: 没有暖心, 不再受 MPI 驱动; 缓慢填塞, 冷海/深陆更快
        _dVlo = -0.05 - (0.30 if env['sst'] < 22.0 else 0.0)                 - (0.30 if env['land'] > 0.6 else 0.0)
        tc['vmax'] = clamp(tc['vmax'] + _dVlo * dt, 8, 999)
        tc['pmin'] = pmin_from_v(tc['vmax'])
        tc['rmw'] = rmw_from_v(tc['vmax'])
        tc['b'] = holland_b(tc['pmin'])
    if tc.get('named'):
        tc['wasNamed'] = True
    tc['cuClass'] = phase                     # 结构相态(物理用)
    # ── **权威阶段码(ATCF)**: DB/WV/LO/TD/TS/TY/ST/EX/SS/SD/MD ──
    # 由 simulator/simcore/phase.py 的纯函数统一判定: 含逐级减弱阶梯(TS→TD→LO)、
    # 非热带码的强度上限(DB/WV/LO 不允许 >=34kt)、EX 不可逆等规则。
    # 这一步直接治用户报的两个缺陷: "TD 一次都没出现" 与 "减弱从 TS 直跳 DB"。
    try:
        _t200 = (tc.get('env') or {}).get('t200')
        _dth = None
        if _t200 is not None:
            _t2 = float(_t200)
            _t2 = _t2 - 273.15 if _t2 > 150.0 else _t2
            _dth = float(env['sst']) - _t2
        else:
            # 兜底代理: 暖池里 SST≥26.5 的闭合系统按暖心处理(60K 以上 = 明确暖心)。
            # 宁可按暖心(不误判副热带), 因为误判会整段吃掉 TD/TS/TY。
            _dth = 40.0 + max(0.0, float(env['sst']) - 24.0) * 6.0
        _prev = tc.get('phase')
        if _prev not in _PH.PHASE_CODES:
            _prev = None                      # 'TC' 不是阶段码
        _res = _PH.classify(
            vmax=tc['vmax'], sst=float(env['sst']), lat=float(env['lat']),
            shear=float(env['shear']), land=float(env.get('land', 0.0)),
            age=tc['age'], dt_sst200=_dth, t200=_t200, previous=_prev,
            numbered=(tc.get('cy') is not None), named=bool(tc.get('named')),
            weak=float(weak), front=bool(phase in ('EX', 'SS')),
            disturb=bool(tc.get('disturb')),
            lon=float(tc['lon']), retropical=bool(phase == 'TC'),
            td_fresh=bool(_prev in ('TS', 'TY', 'ST') and tc['vmax'] < _PH.KT_TS),
            structure=phase)
        tc['phase'] = _res['phase']
        tc['phaseWhy'] = _res.get('confidence')
        if _res.get('capped_from'):
            tc['phaseCapped'] = _res['capped_from']
    except Exception:
        logger.debug('阶段判定失败', exc_info=True)
    _lo_h = (float(sim['t']) - float(tc['loHour'])) if tc.get('loHour') else 0.0
    # ── 轮次 124: **临终尾巴**的三个旋钮(默认 = 历史行为) ──
    # 依据 `tools/probe_death_cause.py --days 100`(179 个系统):
    #   死因: lo_fade 61% / fade(<8kt) 22% / lo_cap(72h) 8%
    #   寿命四段(到过 34kt 的 124 个): 生成->34kt 12-24h(观测 51-60h)
    #                                 峰值->最后34kt 12-48h(观测 36-60h)
    #                                 **最后34kt->结束 42-54h(观测 24h)**
    #   终态强度中位 9.9kt ⇒ 暖水上 10-19kt 的系统**可以无限期活着**。
    # 这条尾巴同时解释: LO 记录占比偏高(23% vs 观测 6.7-12.2%)、寿命数字虚胖、
    # 以及存活名额被占满(当年 _GEN_MAX_ALIVE 25->60 就是因为"每个名额被占 107 天")。
    _P = sim['params']
    _fade_kt = float(_P.get('fadeKt', 8.0))
    _lo_cap = float(_P.get('loCapH', 72.0))
    _weak_h = float(_P.get('weakLifeH', 0.0))       # >0 时启用"暖水弱系统寿命上限"
    _weak_v = float(_P.get('weakV', 20.0))
    # 维护"第一次掉到 _weak_v 以下"的时刻(只在 _weak_h>0 时起作用)
    if tc['vmax'] >= _weak_v:
        tc.pop('weakSince', None)
    elif 'weakSince' not in tc:
        tc['weakSince'] = float(sim['t'])
    # ── step ④（Lead 2026-10-05 裁定）：**EX/SS 用自己的消亡口径，不走热带那套地板** ──
    # 病灶（实测，见 output/exploration/g_path_sim.md §24.1）：**16/20 的 EX 台风死在
    # `phase=='EX' and vmax < 20`** —— 一个已转温带的系统本来就不该套"热带气旋最低强度地板"，
    # 真实里温带气旋可以很弱、且能维持很多天。交付口径实测 **EX 中位只有 2 个报点 ≈ 12 小时**。
    # 新口径（**温带气旋的消亡一般是"填塞/被合并"**，故用"弱 + 持续" + 年龄上限）：
    #   · `exFadeKt = 5.0`   —— 温带气旋的绝对地板（远低于热带的 20kt）
    #   · `exWeakH  = 72.0`  —— **持续** vmax<12kt 满 72h ⇒ 填塞消亡
    #   · `exAgeCapH = 720.0`—— 年龄上限 30 天（防"永远不死"；不是物理阈值，是兜底）
    # ⚠ **按裁定 1**：本步动机是**生命史类型**（用户【1.2】明确点名"没有 EX/SS/SD"），
    #   **不是**追寿命/强度指标 —— 但**会顺带改变寿命分布**（EX 活得久了总寿命自然涨），
    #   **用户本阶段不把寿命作为验收项，故接受** ✓
    # ⚠ 统计口径（Lead 定，写进报告当规矩）：**"存活/持续"一律按【报点数】算，
    #   **不**按 `sim['tcs']` 步数算 —— 后者含 **120 天死后残留**（core.py:5398-5410）** ✓
    if phase in ('EX', 'SS'):
        # ⚠⚠ **step ⑥ 修正（实测驱动）**：step⑤ 报出 **100% 的 EX 都死在 `ageCap`** ⇒
        #  查因：`core.py:3421` 的 `clamp(tc['vmax'] + dV2*dt, **12**, 999)` 把 EX 的 vmax
        #  **地板锁在 12** ⇒ step④ 那两条判据是**死代码**：
        #    · `vmax < 5.0`  ⇒ 12<5 恒假 ✗
        #    · `vmax < 12.0` ⇒ 12<12 恒假 ✗
        #  ⇒ **只有 `ageCap` 能触发** ⇒ "兜底"变成了"唯一主控" ✗
        # 修法（Lead 批准 (b)）：**弱计时改成 `<= 12.0`**（可达）⇒ 物理口径真正生效 ✓
        #  ⚠ **`exFadeKt` 分支已删除**：在地板恒 12 的前提下，它取 `<=12` 恒假（还是死代码）、
        #    取 `>12`（Lead 曾议 13.0）会**立刻**杀掉刚转温带的系统 ⇒ **变成新的主控** ✗
        #    ⇒ 任何取值都无意义，**删掉**比留一个旋钮更安全 ✓
        #  ⇒ 依据：温带气旋消亡 = **填塞/被合并** ⇒ "**持续弱满 `exWeakH` 小时**" ⇒ 消亡 ✓
        _ex_weak_h = float(_P.get('exWeakH', 72.0))
        _ex_cap = float(_P.get('exAgeCapH', 720.0))
        if tc['vmax'] <= 12.0:
            if 'exWeakSince' not in tc:
                tc['exWeakSince'] = float(sim['t'])
            elif float(sim['t']) - float(tc['exWeakSince']) > _ex_weak_h:
                tc['dead'] = True
                tc['exDeadBy'] = 'exWeakH'
        else:
            tc.pop('exWeakSince', None)
        if tc['age'] > _ex_cap:
            tc['dead'] = True
            tc['exDeadBy'] = 'exAgeCapH'
    elif tc['vmax'] < _fade_kt or weak > 120:
        tc['dead'] = True
    elif phase == 'LO' and (_lo_h > _lo_cap or tc['vmax'] < 10.0):
        tc['dead'] = True
    elif tc['vmax'] < _weak_v and (env['sst'] < 20.0 or env['land'] > 0.9):
        tc['dead'] = True
    elif (_weak_h > 0.0 and tc['vmax'] < _weak_v and env['land'] < 0.5
          and float(sim['t']) - float(tc['weakSince']) > _weak_h):
        tc['dead'] = True
    elif phase == 'EX' and tc['vmax'] < 20:
        tc['dead'] = True
    if tc['dead'] and 'deadHour' not in tc:
        tc['deadHour'] = float(sim['t'])
    tc['age'] += dt


# ── 移动 ──

def deposit_gauss(arr: np.ndarray, lon: float, lat: float, Rkm: float,
                  amp: float, km_per_lon: float, km_per_lat: float) -> None:
    """冷尾流 / OHC 消耗的高斯沉积(向量化)。

    原实现是 (2n+1)^2 的双重 Python 循环(每次 ~250 格), 实测 0.28ms/次 ×
    每步 24 次 = 6.6ms/步; 改成整窗一次算完。arr 是一维展开场, reshape 得到
    的是视图, 原地写回即可(与调用方 sim['wake'] / sim['ohcWake'] 共享内存)。
    """
    n = int(math.ceil((Rkm * 2.2) / (C['ENVD'] * 111.32))) + 1
    jc = int(math.floor((lat - C['LAT0']) / C['ENVD']))
    ic = int(math.floor((lon - C['LON0']) / C['ENVD']))
    j0, j1 = max(0, jc - n), min(ENVNY - 1, jc + n)
    i0, i1 = max(0, ic - n), min(ENVNX - 1, ic + n)
    if i1 < i0 or j1 < j0:
        return
    d_lon_km = (C['LON0'] + (np.arange(i0, i1 + 1) + 0.5) * C['ENVD'] - lon) * km_per_lon
    d_lat_km = (C['LAT0'] + (np.arange(j0, j1 + 1) + 0.5) * C['ENVD'] - lat) * km_per_lat
    g = np.exp(-(d_lat_km[:, None] ** 2 + d_lon_km[None, :] ** 2) / (Rkm * Rkm))
    win = arr.reshape(ENVNY, ENVNX)[j0:j1 + 1, i0:i1 + 1]
    np.minimum(win + amp * g, 6.0, out=win)


#: 移速对引导气流的响应时间常数(模拟小时)。一阶滞后保证台风不会瞬间拐弯,
#: 也让路径在引导场突变(副高断裂/槽逼近)时仍是圆滑的转向。
_MOTION_TAU_H = 3.0


def step_hours(sim) -> float:
    """当前模拟的步长(小时)。**积分步长 = 环境步长**, 一个旋钮。

    原来积分固定 10 分钟、环境 1/3/6/12/24h 是两套, 没有物理理由:
    环境冻结期内强度 ODE 是自治的, 10 分钟积 36 步与 6 小时积 1 步数学等价,
    纯属 36 倍冗余。而且 10 分钟**并不**带来登陆精度 —— 见 seg_land_cross。
    """
    try:
        return float(snap_env_step(sim.get('envStepH', DEFAULT_ENV_STEP)))
    except Exception:
        return DEFAULT_ENV_STEP


def seg_land_cross(sim, lon0: float, lat0: float,
                   lon1: float, lat1: float, n: int = 0):
    """线段 p0→p1 与陆地场(land > 0.3)的**第一个交点**。

    返回 (lon, lat, frac) 或 None; frac 是交点在线段上的比例。

    **为什么要几何求交而不是按步采样**: 6h 一步、10kt 速度下线段长 60nm,
    而 0.5° 格约 30nm —— 按步采样完全可能跨过一条窄岛/海峡而错过登陆。
    这个问题**与步长无关**(10 分钟一步同样会漏), 只有求交才能根治。
    """
    dlon = lon1 - lon0
    if dlon > 180.0:
        dlon -= 360.0
    elif dlon < -180.0:
        dlon += 360.0
    dlat = lat1 - lat0
    span = math.hypot(dlon * 111.32 * math.cos(lat0 * C['DEG']), dlat * 110.57)
    if n <= 0:
        n = int(max(8, math.ceil(span / max(8.0, 0.5 * C['ENVD'] * 111.32))))
        n = min(n, 256)
    prev = land_at(sim, lon0, lat0)
    for i in range(1, n + 1):
        f = i / n
        cur = land_at(sim, lon0 + dlon * f, lat0 + dlat * f)
        if prev <= 0.3 < cur:
            a, b = (i - 1) / n, f          # 二分细化到 ~1/1024 段
            for _ in range(10):
                m = 0.5 * (a + b)
                if land_at(sim, lon0 + dlon * m, lat0 + dlat * m) > 0.3:
                    b = m
                else:
                    a = m
            m = 0.5 * (a + b)
            return (lon0 + dlon * m, lat0 + dlat * m, m)
        prev = cur
    return None


def deposit_segment(sim, key: str, x0: float, y0: float, x1: float, y1: float,
                    Rkm: float, amp_total: float, km_per_lon: float,
                    km_per_lat: float) -> None:
    """沿线段沉积尾流(而不是只在终点画一个圆)。

    6h 一步、10kt 速度下线段长 60nm 而 RMW 才约 40nm —— 只在终点沉积会让
    尾流变成**一串分离的圆点**而不是连续的带。按线段长度自动分子步。
    """
    span = math.hypot((x1 - x0) * km_per_lon, (y1 - y0) * km_per_lat)
    k = int(max(1, min(12, math.ceil(span / max(20.0, Rkm * 0.5)))))
    arr = sim[key]
    for i in range(k):
        f = (i + 0.5) / k
        deposit_gauss(arr, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f,
                      Rkm, amp_total / k, km_per_lon, km_per_lat)


def motion_lag_alpha(dt: float) -> float:
    """一阶滞后系数 1-exp(-dt/tau), 恒在 [0,1] 内(不过冲, 与步长弱相关)。"""
    return 1.0 - math.exp(-max(0.0, dt) / _MOTION_TAU_H)


def fujiwhara_drift(lon, lat, vmax, others):
    """**藤原(二元互旋)漂移**(kt), 返回 (du, dv)。

    `others` 是邻居的 (lon, lat, vmax, rmw_nm) 序列。

    **轮次 106 抽成纯函数**: 预报原来**完全不做藤原**(主模型做到最多 4kt; 实测
    32.9% 的步 |fuji|>1kt、p90 3.8kt、均值 1.06kt), 这是"零扰动预报 vs 主模型
    自己的轨迹"残余误差的一个已知来源。抽出来两边共用同一份实现 —— 与引导、
    切变矢量一样的"同口径"要求。

    标度(沿用原注释): 幅度 ∝ (对方强度/50) x (1 - d/800) x (对方 RMW/30km)^1.5,
    方向 = 绕对方涡旋的切向(气旋式, 随半球反号), C=2.8 使 d=200km/70kt/30km 约 3kt。
    """
    fu = fv = 0.0
    hemi = 1.0 if lat >= 0 else -1.0
    for o_lon, o_lat, o_vmax, o_rmw in others:
        dx = (o_lon - lon) * 111.32 * math.cos(lat * C['DEG'])
        dy = (o_lat - lat) * 110.57
        d = math.hypot(dx, dy)
        if d < 1.0 or d > 800.0:
            continue
        lo_v = min(o_vmax, vmax)
        hi_v = max(o_vmax, vmax)
        if hi_v / max(1.0, lo_v) > 3.0:
            continue
        rmw_km = max(8.0, float(o_rmw)) * 1.852
        mag = min(4.0, 2.8 * (o_vmax / 50.0) * (1.0 - d / 800.0)
                  * (rmw_km / 30.0) ** 1.5)
        hx, hy = -dx / d, -dy / d
        tx, ty = -hy * hemi, hx * hemi
        fu += tx * mag
        fv += ty * mag
    return fu, fv


#: step ⑧ 天气尺度引导扰动（Lead 2026-10-05 派）。
#: 动机（用户【1】）：**深热带(<20°) W+NW 实测 83.4%，真实 62–72%** ⇒ 我们太偏西北。
#: 根因：`base_wind` 是 **(12,180,360) 月平均** ⇒ 空间平滑、时间只有 12 个点 ⇒
#: 让 <20° 出现 E/N/SW 的东西（季风涡旋、副高断裂、鞍型场、弱引导打转）**全被抹掉** ✓
#: ⇒ 本函数叠加**低波数 + AR(1) 演变**的扰动 ⇒ 偶发地让 |v| 超过 β 的向极分量(+3.9kt)
#:   或让 u 反号 ⇒ 方向不再被"东方 + β 向极"钉死 ✓
#: ⚠ **默认 `synAmp=0.0` ⇒ 直接 return None ⇒ 不消耗任何随机数 ⇒ 与改动前逐位相同** ✓
#: ⚠ 叠加在 **u850/v850/u200/v200**（**不是 uMid**）：`steering_layers` 采样的就是 850/200 两层 ✓
def synoptic_perturb(sim, shape, t_h):
    """返回 (du, dv) kt，或 None（关闭时）。空间低波数 + 时间 AR(1)。"""
    _P = sim.get('params') or {}
    amp = float(_P.get('synAmp', 0.0))
    if amp <= 0.0:
        return None
    tau = max(6.0, float(_P.get('synTauH', 120.0)))
    nw = max(1, min(6, int(_P.get('synWaves', 3))))
    st = sim.get('_synState')
    if st is None or len(st.get('a', ())) != nw * 2:
        g0 = np.random.Generator(np.random.PCG64(987654321))
        st = {'t': float(t_h), 'ph': g0.random(nw * 2) * 2.0 * np.pi,
              'a': g0.standard_normal(nw * 2)}
        sim['_synState'] = st
    d = float(t_h) - float(st['t'])
    if d > 1e-9:
        gg = np.random.Generator(np.random.PCG64(int(abs(t_h) * 1000) % (2 ** 31)))
        rho = math.exp(-d / tau)
        st['a'] = rho * st['a'] + math.sqrt(max(0.0, 1.0 - rho * rho)) * gg.standard_normal(len(st['a']))
        st['t'] = float(t_h)
    ny, nx = int(shape[0]), int(shape[1])
    lat = np.linspace(-89.5, 89.5, ny)[:, None]
    lon = np.linspace(0.5, 359.5, nx)[None, :]
    du = np.zeros((ny, nx), np.float64)
    dv = np.zeros((ny, nx), np.float64)
    for k in range(nw):
        wl = float(k + 1)
        au = amp * float(st['a'][k])
        av = amp * float(st['a'][nw + k])
        la = np.cos(np.radians(lat * 0.6))          # 赤道强、极区弱
        du += au * np.sin(np.radians(lon * wl) + float(st['ph'][k])) * la
        dv += av * np.cos(np.radians(lon * (wl * 0.7)) + float(st['ph'][nw + k])) * la
    return du.astype(np.float32), dv.astype(np.float32)


def steer_systems(sim, shape, t_h):
    """§63 **在模型里生成引导环流系统**（Lead 2026-10-05 派）—— 默认全 0 ⇒ None（no-op）。

    **定性**: 用真实数据离线统计出尺度/强度/周期 ⇒ **在模型里生成**；
    **不是**把真实逐日场读进来（那是回放）✓

    **为什么必须零均值**: `base_wind` 是真实再分析【月平均】⇒ **本来就含副高**
    （850 层 7 月 100-160E/10-30N 有清晰反气旋）⇒ 再叠一整个反气旋 = **把副高乘二** ✗
    ⇒ 本函数生成的是【相对气候态的异常】，并**减去 cos(lat) 加权面积平均** ⇒ 面积平均 ≈ 0 ✓

    ① 副高异常: 脊线随经度/时间漂移 + 缺口 ⇒ 台风在不同纬度/经度遇缺口 ⇒ 转向时刻分散
       **符号**（Lead 复核 ✓）: NH 反气旋 **北侧 = 西风(u>0)、南侧 = 信风(u<0)**
       ⇒ `Δu = +shAmp * sign(lat - ridge) * 包络`（**注意是 +**，我在 §62 设计稿里写成 −，已更正 ✓）
    ② 东移短波槽: 波数 k、**相速 twSpeedMs（m/s，10-15 往东）** ⇒ 槽扫过 ⇒ 急拐
    """
    _P = sim.get('params') or {}
    _A = float(_P.get('sysShAmp', 0.0))          # 副高异常幅度(kt), 0=关
    _A2 = float(_P.get('sysTwAmp', 0.0))         # 短波槽幅度(kt), 0=关
    if _A <= 0.0 and _A2 <= 0.0:
        return None
    # ── §69 **日历锁**：相位的时基（默认 0 = 现状：挂在【本次运行累计小时】上）──────
    # 为什么需要: 这里用的 `t_h` 是 `sim['t']`（`_env_update_impl` 的 `t = sim['t']`，
    #   `core.py:1583`）= **本次运行的 elapsed**，**不是日历时刻**。
    #   ⇒ 两个 run 只要 elapsed 相同、日历月份相同 ⇒ 环流异常场**逐点相同**
    #   ⇒ 在同一个位置生成的台风走同一条路。
    #   实测(§6.1 复现, `output/exploration/gen_diff_probe.py`): 8 个起报日期
    #   (8/01,8/04,…,8/22)、同一位置 14N/140E、槽开 + 相速 12 m/s
    #   ⇒ **最北纬极差只有 1.8°**（全关基线 0.6°）= 不分化 ✓
    # 打开 `sysClockLock` 后，相位改挂**绝对日历**（距 2000-01-01 的小时数）
    #   ⇒ "140E 上空的槽在 8/5 在哪" 由日历决定，与"哪天按下播放"无关 ✓
    # 默认 0 ⇒ `_t_h` 就是 `t_h` ⇒ 逐位不变 ✓（已用 free_hash 验证）
    _t_h = t_h
    if float(_P.get('sysClockLock', 0.0)) > 0.0:
        try:
            _t_h = (float(t_h)
                    + (sim['start'] - datetime(2000, 1, 1)).total_seconds() / 3600.0)
        except Exception:
            _t_h = t_h
    ny, nx = int(shape[0]), int(shape[1])
    lat = np.linspace(-89.5, 89.5, ny)[:, None]
    lon = np.linspace(0.5, 359.5, nx)[None, :]
    du = np.zeros((ny, nx), np.float64)
    dv = np.zeros((ny, nx), np.float64)
    # ── ① 副高异常（脊线漂移 + 缺口，均以 lon 上的余弦构造 ⇒ 经向积分为 0）──
    if _A > 0.0:
        wl = max(20.0, float(_P.get('sysShWLon', 90.0)))
        drift = float(_P.get('sysShDrift', 0.0)) * (float(_t_h) / 24.0)
        lat0 = float(_P.get('sysShLat0', 25.0))
        ampL = float(_P.get('sysShAmpLon', 8.0))
        ph = 2.0 * np.pi * (lon - drift) / wl
        ridge = lat0 + ampL * np.cos(ph)
        envY = np.exp(-0.5 * ((lat - ridge) / max(1.0, float(_P.get('sysShSigY', 8.0)))) ** 2)
        # 缺口: 对幅度做零均值调制（用余弦，不用 1-高斯 ⇒ 后者非零均值）
        gapw = float(_P.get('sysShGapAmp', 0.0))
        mod = 1.0 + gapw * np.cos(2.0 * np.pi * (lon - drift - float(_P.get('sysShGapLon', 180.0)))
                                  / max(5.0, float(_P.get('sysShGapW', 15.0)) * 4.0))
        du += _A * np.sign(lat - ridge) * envY * mod          # ★ 符号: + ✓
        dv += _A2 * 0.0 + float(_P.get('sysShAmpV', 0.0)) * np.cos(ph) * envY
    # ── ② 东移短波槽（相速 twSpeedMs ⇒ 相位随 t 推进）──
    if _A2 > 0.0:
        k = max(1, int(_P.get('sysTwWaves', 3)))
        c = float(_P.get('sysTwSpeedMs', 0.0))
        pk = float(_P.get('sysTwLatPeak', 40.0)); wd = max(2.0, float(_P.get('sysTwWidth', 12.0)))
        envL = np.exp(-0.5 * ((np.abs(lat) - pk) / wd) ** 2)
        for j in range(k):
            wl_j = 360.0 * (j + 1) / float(k)
            phase = 2.0 * np.pi * lon / wl_j
            # 相速换算: 度/天 = c[m/s]*86400/111320/cos(lat)
            omega = (c * 86400.0 / 111320.0) / np.maximum(0.2, np.cos(np.radians(lat)))
            phase = phase - omega * (float(_t_h) / 24.0) + float(_P.get('sysTwPhase0', 0.0)) + j
            # §68 **离散槽**: `sysTwDuty` = 槽占的比例（0 = 连续波 = 现状，逐位不变 ✓）
            # 门控只在波峰附近打开 ⇒ 槽【一个一个过境】，槽之间接近零场
            #   ⇒ 期望形态: "走一段、转一次"，而不是"一路在抖"（Lead 逐条看图后的判断 ✓）
            _duty = float(_P.get('sysTwDuty', 0.0))
            if _duty > 0.0:
                _thr = 1.0 - 2.0 * min(0.49, _duty)
                gate = np.clip((np.cos(phase) - _thr) / max(1e-6, 1.0 - _thr), 0.0, 1.0)
                du += _A2 * gate * np.cos(phase) * envL
                dv -= _A2 * gate * np.sin(phase) * envL
            else:
                du += _A2 * np.cos(phase) * envL
                dv -= _A2 * np.sin(phase) * envL
    # ── **零均值铁律**: 减去 cos(lat) 加权的面积平均 ⇒ 面积平均 ≈ 0（不加倍已有副高）──
    w = np.cos(np.radians(lat))
    wsum = float(w.sum()) * nx
    for arr in (du, dv):
        arr -= float((arr * w).sum()) / max(1e-9, wsum)
    return du.astype(np.float32), dv.astype(np.float32)


def _free_motion(sim, tc, dt):
    # 自由运动路径: 引导 + beta 漂移 + 随机扰动 + Fujiwhara -> 一阶滞后 -> 积分位移。
    # 轮次 120 从 motion_step 提取出来, 目的是让 **A 模式(路径锁定)绕开它却不绕开尾段**
    # (冷尾流沉积 / OHC 消耗 / Ekman 抽吸 / 登陆几何求交 / distKm 必须两种模式共用)。
    # 返回 (u, v, lon0_step, lat0_step, mu0, mv0)。
    steer = steering_at(sim, tc['lon'], tc['lat'], tc['vmax'])
    beta = beta_drift(tc['lat'])
    r = sim['params']['random']
    tc['noiseU'] += (sim['gauss']() * 2.1 * (0.4 + r) - tc['noiseU']) * 0.10
    tc['noiseV'] += (sim['gauss']() * 2.1 * (0.4 + r) - tc['noiseV']) * 0.10
    # 目标移速 = 引导 + β漂移 + 随机扰动, 再统一做一阶滞后
    tu = steer[0] + beta[0] + tc['noiseU'] * r * 1.6
    tv = steer[1] + beta[1] + tc['noiseV'] * r * 1.6
    # Fujiwhara 双台风互旋(弱台风影响按强度加权)
    # ── 阶段 8: Fujiwhara 双台风互旋(重写) ──
    # 旧实现把邻居涡旋在本点的**切向风**叠进引导(100kt 邻居在 400km 处仍有
    # ~30kt, 乘 0.5 就是 15kt), 再硬封顶到 3kt。三个毛病: 封的是"引导叠加量"
    # 而不是"互旋漂移"; 方向虽用切向但幅度不随距离衰减到位; 1200km 处本该近零。
    #
    # 现在直接给**位置漂移**, 用二元互旋的经验标度:
    #   幅度 ∝ (对方强度/50) · (1 − d/800) · (对方 RMW/30km)^1.5
    #   方向 = 绕对方涡旋的切向(气旋式, 随半球反号)
    #   门限 = d < 800km 且 强度比 < 3(差太多时大台风基本不理会小涡旋)
    # C=2.8 使 d=200km/70kt/30km 时约 3kt、d=700km 时约 0.5kt —— 与观测的
    # 二元互旋漂移量级(1-3kt)一致。
    _fu, _fv = fujiwhara_drift(
        tc['lon'], tc['lat'], tc['vmax'],
        [(o['lon'], o['lat'], o['vmax'], o['rmw']) for o in sim['tcs']
         if o is not tc and not o['dead']])
    tc['fuji'] = math.hypot(_fu, _fv)
    tu += _fu
    tv += _fv
    lf = land_at(sim, tc['lon'], tc['lat'])
    if lf > 0.1:
        tu *= 1 - 0.25 * min(1.0, lf)
        tv *= 1 - 0.25 * min(1.0, lf)
    if tc['mu'] == 0.0 and tc['mv'] == 0.0:
        # 生成首步: 直接用引导初值, 避免台风"原地起步"等滞后爬升
        tc['mu'], tc['mv'] = tu, tv
    else:
        a = motion_lag_alpha(dt)
        tc['mu'] += (tu - tc['mu']) * a
        tc['mv'] += (tv - tc['mv']) * a
    u, v = tc['mu'], tc['mv']
    # 位移: u/v 为 kt(海里/小时), 60 海里/度
    lon0_step, lat0_step = tc['lon'], tc['lat']
    mu0, mv0 = tc['mu'], tc['mv']
    return u, v, lon0_step, lat0_step, mu0, mv0


def motion_step(sim, tc, dt: float) -> None:
    # ── 阶段 11: 注入现有 TC 的 **A 模式(路径锁定)** ──
    # 位置由导入序列强制, 强度仍由 intensity_step 按环境演化。这是强度评估的
    # 标准做法 —— 只有路径一致, 模拟与观测的强度差才归因于强度方程本身。
    # ⚠ **A 模式必须与自由运动共用后半段**(轮次 120 修)。
    # 旧实现设完位置就 `return`, 于是**冷尾流沉积 / OHC 消耗 / Ekman 抽吸 / 登陆几何求交 /
    # distKm 累计全被跳过** —— 锁路径的台风从不冷却脚下的海面, 它的 MPI 永远停在气候态
    # (热带 ~195kt), 强度便一路爬到 150-190kt。实测(锁观测路径 72h, 观测初值 -> 模型末值):
    # 85->188 / 115->165 / 125->148 / 105->144, 系统性 **+40~+100kt 高偏**。
    # 这直接污染了所有走 A 模式的强度标定, 必须修。
    _locked = False
    if tc.get('track_lock'):
        try:
            from simulator.simcore import import_tc as _imp
            _p = _imp.track_at(tc, clock(sim))
            if _p is not None:
                _lo, _la, _vm = _p
                _lo_start, _la_start = tc['lon'], tc['lat']
                # 记下真实移速(强度方程要用), 再强制位置
                _d = math.hypot((_lo - tc['lon']) * 111.32
                                * math.cos(_la * C['DEG']), (_la - tc['lat']) * 110.57)
                if dt > 0:
                    _sp = _d / (1.852 * dt)
                    _ang = math.atan2(_la - tc['lat'], _lo - tc['lon'])
                    tc['mu'], tc['mv'] = _sp * math.cos(_ang), _sp * math.sin(_ang)
                tc['lon'], tc['lat'] = _lo, _la
                tc['importV'] = _vm
                _locked = True
        except Exception:
            tc['track_lock'] = None
    if _locked:
        # A 模式: mu/mv 已按**观测位移**写好(见上), 正好就是尾流沉积该用的移速;
        # 起点取强制位置**之前**的坐标(= obs(t−dt)), 这样尾流铺展与 seg_land_cross
        # 用的线段就是**真实走过的那一段**。
        #
        # ⚠ 这里**绝不能再积分一次位移**: `tc['lon']/tc['lat']` 已经是 obs(t), 若再叠加
        # u·dt 就等于把观测位移算了两遍 ⇒ 位置永远领先一步, 而且下一步算出的
        # `_d`(obs(t+dt) − 当前位置)只剩残差 ⇒ mu/mv≈0 ⇒ `spd>6` 的尾流门限失效,
        # 冷尾流根本不会沉积 —— 修 bug 反而把新逻辑架空。
        u, v = tc['mu'], tc['mv']
        lon0_step, lat0_step = _lo_start, _la_start
        mu0, mv0 = u, v
        tc['fuji'] = 0.0
    else:
        u, v, lon0_step, lat0_step, mu0, mv0 = _free_motion(sim, tc, dt)
        # 位移: u/v 为 kt(海里/小时), 60 海里/度
        tc['lon'] = (tc['lon'] + u * dt / (60.0 * max(0.05, math.cos(tc['lat'] * C['DEG'])))) % 360.0
        tc['lat'] += v * dt / 60.0
    # **纬度不判死**(用户 2026-09-21): 极区参与模拟, 温带变性(EX)阶段不许被边界截断。
    # 原来 lat 到 ±59.6° 就 dead=True —— 那是"域只有 ±60"时代的权宜之计。
    # 现在纬度只做**数值夹取**(避免跑到 ±90 之外把 cos(lat) 除爆), 死亡的判据回到物理:
    # 冷海/陆地/风切把 vmax 压到消散阈值(见 intensity_step 末尾的消亡判据)。
    tc['lat'] = float(clamp(tc['lat'], C['LAT0'] + 0.1, C['LAT1'] - 0.1))
    # **几何登陆求交**(任何步长都不漏, 见 seg_land_cross 的说明)
    _cr = seg_land_cross(sim, lon0_step, lat0_step, tc['lon'], tc['lat'])
    if _cr is not None:
        tc['landfall'] = {'lon': _cr[0], 'lat': _cr[1], 't': sim['t'],
                          'vmax': tc['vmax']}
        tc['nLandfall'] = tc.get('nLandfall', 0) + 1
    tc['distKm'] += math.hypot(u * 1.852 * dt, v * 1.852 * dt)
    # 冷尾流沉积(kt): 移动方向右侧 60km, 强度 ∝ V²; 同时消耗 OHC(恢复更慢)
    spd = math.hypot(u, v)
    env = tc.get('env') or sample_env(sim, tc['lon'], tc['lat'])
    if spd > 6 and env['sst'] > 25:
        cos_a = u / spd if spd > 0.5 else 0.0
        sin_a = v / spd if spd > 0.5 else 0.0
        _cl = max(1e-6, math.cos(tc['lat'] * C['DEG']))
        cx = tc['lon'] + (60 * cos_a - 60 * sin_a) / 111.32 / _cl
        cy = tc['lat'] + (60 * sin_a + 60 * cos_a) / 110.57
        # 步**起点**的尾流中心(用步前的方向), 用来把尾流沿线段铺开
        _sp0 = math.hypot(mu0, mv0)
        _ca0 = mu0 / _sp0 if _sp0 > 0.5 else cos_a
        _sa0 = mv0 / _sp0 if _sp0 > 0.5 else sin_a
        _cl0 = max(1e-6, math.cos(lat0_step * C['DEG']))
        cx0 = lon0_step + (60 * _ca0 - 60 * _sa0) / 111.32 / _cl0
        cy0 = lat0_step + (60 * _sa0 + 60 * _ca0) / 110.57
        Rkm = 150 + 40 * min(1.0, tc['vmax'] / 97.0)
        # 海洋柱(混合层深度/层结)决定同样的扰动会降多少温
        ocn = sim.get('ocn') or ocean_column_init(sim)
        jj_ = int(clamp((cy - C['LAT0']) / C['ENVD'], 0, ENVNY - 1))
        ii_ = int(clamp((cx - C['LON0']) / C['ENVD'], 0, ENVNX - 1))
        mld = float(ocn['h'][jj_, ii_])
        d_t = float(ocn['Tml'][jj_, ii_] - ocn['Tsub'][jj_, ii_])
        # 混合层越浅, 同样的热量亏损降温越大(以 60m 混合层归一)
        mld_gain = clamp(60.0 / max(20.0, mld), 0.4, 3.0)
        # Ekman 抽吸(参数化): w_e ~ curl(τ)/(ρ f) ~ C_D·V²/(ρ f L)
        f_cor = max(F_MIN, abs(2.0 * C['OMEGA'] * math.sin(tc['lat'] * C['DEG'])))
        we = (RHO_AIR * CD_WIND * (tc['vmax'] * 0.5144) ** 2
              / (1025.0 * f_cor * max(60.0e3, Rkm * 1.0e3)))
        pump = clamp(we * 8.0e4, 0.0, 3.0)
        ohc_f = 1 - 0.55 * min(1.0, env['ohc'] / 90.0)
        # F8: **移速-尾流反馈**。冷却强度取决于海洋的响应时间与台风在此处
        # 停留时间之比: 慢速台风有足够时间做 Ekman 抽吸并把混合层热量耗掉,
        # 快速台风则"掠过"—— 海洋来不及响应, 且把冷尾流甩在身后。
        # 用 Price(1981) 那类形式: ΔT ∝ 1/(1+U/U0), U0≈8kt, 快慢之间约 2 倍差。
        _sp_kt = math.hypot(tc['mu'], tc['mv'])
        speed_f = clamp(2.0 / (1.0 + _sp_kt / 8.0), 0.5, 1.8)
        # F8: **障碍层(barrier layer)**。降水 > 蒸发 的海区(西太暖池、ITCZ、
        # 大西洋淡水池、孟加拉湾)会在混合层底下压一层**暖而淡**的水; 台风搅动
        # 时卷上来的是这层温水而不是温跃层的冷水, 于是冷却弱、更容易增强。
        # 这里用气候学代理:
        #   - 暖池: SST 越高越可能有障碍层(29℃ 以上饱和)
        #   - ITCZ 纬度带: 深对流降水带, 季节南北摆动
        # 数据升级路径: 从 CMEMS 的 thetao+so 算「等温层深度 − 混合层深度」,
        # 需要重抓 12 个月全球场(原始 thetao 已丢弃, 只存了算好的 OHC/D26)。
        _bl_wp = clamp((env['sst'] - 28.5) / 1.5, 0.0, 1.0)
        # ITCZ 纬度: 8-10 月最北(~11N), 2 月最南(~1N)。
        # 必须用 **cos**(峰值在 8 月); 用 sin(2π(m-8)/12) 会把峰值挪到 11 月、
        # 谷值挪到 5 月, 整整错一个季度(实测 1 月算成 8.5N)。
        _itcz = 6.0 + 5.0 * math.cos(2 * math.pi * (sim['month'] - 8) / 12.0)
        _bl_itcz = math.exp(-((tc['lat'] - _itcz) / 7.0) ** 2)
        _bl = clamp(max(_bl_wp, 0.75 * _bl_itcz), 0.0, 1.0)
        bl_f = clamp(1.0 - 0.45 * _bl, 0.55, 1.0)
        k = (0.30 * (tc['vmax'] / 117.0) ** 2 * ohc_f * (0.7 + 0.3 * sim['rng']())
             * mld_gain * (d_t / 5.0) * speed_f * bl_f)
        _klon = 111.32 * math.cos(cy * C['DEG'])
        # 沿**线段**沉积而不是在终点画一个圆(见 deposit_segment 的说明)
        deposit_segment(sim, 'wake', cx0, cy0, cx, cy, Rkm, k * dt, _klon, 110.57)
        # Ekman 抽吸的冷却作用范围比核心大(辐散尺度), 强度随抽吸速度增长
        if pump > 0.05:
            deposit_segment(sim, 'wake', cx0, cy0, cx, cy, Rkm * 2.2,
                            k * dt * 0.6 * pump, _klon, 110.57)
        # OHC 消耗(量级为 D26 km 单位)
        deposit_segment(sim, 'ohcWake', cx0, cy0, cx, cy, Rkm + 60,
                        k * dt * 3.0, _klon, 110.57)
    # **不做经纬度判死**(用户 2026-09-21: "纬度判死不能有!")。
    # 原来这里 lat 一碰 ±59.6° 就 dead=True, 而且**经度也一起判** —— 可经度是周期的
    # (0°/360° 是连续海面), motion_step 当时又没做 % 360 环绕, 所以跨格林尼治子午线的
    # 台风会被误杀。现在: 经度在 motion_step 里环绕, 纬度只夹取; 消亡一律走物理判据
    # (冷海/陆地/切变把 vmax 压到阈值 → 见 intensity_step 末尾)。
    # ACE 累积
    # **同时累计到 sim 级**: tc['ace'] 是逐台风的, 而台风消亡后会从 sim['tcs']
    # 里移除 —— 只读存活台风的 ace 会漏掉已消散的那些, 于是"本年 ACE"越跑越小。
    _dace = tc['vmax'] * tc['vmax'] * 1e-4 * (dt / 6.0)
    tc['ace'] += _dace
    sim['aceSum'] = float(sim.get('aceSum', 0.0)) + _dace


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


# ── 扰动生成 ──
#
# T2(2026-09-17 重写): 原来是在 GEN_ZONES 里**均匀随机**撒点, 再用拍出来的
# GPI 因子 (SST-26.5)/2.5、(19.4-SHEAR)/15.6、(RH-55)/25 决定要不要生成。
# 换成真实环境场后这个 GPI 直接失效 —— 真实 NCEP 切变在 TC 海域均值就有
# 20.5kt(旧合成场最大才 24.5、多数 <15), f2 几乎恒为 0, 实测 90 天只生成
# 1 个台风(应有 ~21 个)。
#
# 现在的做法(与用户给的 T2 一致):
#   * **位置**: 从观测生成密度场(12,NY,NX, 由 6291 条最佳路径统计)抽样,
#     不再区域内均匀 —— 真实生成是高度聚集的(西太暖池东南侧、EPAC 10-15N 等)
#   * **环境调制**: 用病例-对照标定出来的经验权重曲线(见 tools/train_tc_genesis.py)
#     连乘, 决定该点这一次是否真的生成
#   * **绝对速率**: _GEN_RATE 标定到观测年均 72.3 个(1980-2024)
_GEN_CACHE = {'ok': False}


def _gen_tables():
    """载入观测生成密度与经验权重(缺文件时退回旧行为)。"""
    if _GEN_CACHE['ok']:
        return _GEN_CACHE
    _GEN_CACHE.update({'ok': True, 'dens': None, 'w': {}})
    try:
        from simulator.simcore.envmodel import store as _st
        z = _st.load('tc_clim')
        if z is not None and 'genesis' in z:
            g = np.asarray(z['genesis'], np.float32)
            # 轻微空间平滑: 生成密度是用**覆盖完整的年份**(2013-2024, 12 年)统计的,
            # 1° 网格下一个月只有几十个非零格, 直接用会让生成点聚成几个孤立点。
            # σ=1.5 格的高斯能保留洋区/季节分布, 同时给出合理的空间展布。
            g = np.stack([_lowpass(g[m], 1.5) for m in range(g.shape[0])])
            g = np.maximum(g, 0.0)
            n = g.sum(axis=(1, 2), keepdims=True)
            _GEN_CACHE['dens'] = g / np.maximum(n, 1e-9)
            # ── B(S14/t2050): **月间相对权重** `dens_w`（月均 = 1）──────────────────
            # 上面那行把**每个月各自**归一化到 1 ⇒ **月与月"总量"完全相同** ⇒
            # 抽样只决定**落点**、不决定**数量** ⇒ 数量只由 `gen_check` 里那个**与月份无关**
            # 的常数闸门定 ⇒ **生成数天然没有季节形状**（实测 7/8/9/10 = 23/20/22/27，平的）。
            # 而原始 `tc_clim['genesis']` 的月总量是
            #   [76,73,62,49,51,79,127,179,195,136,87,64]（峰 9 月 / 谷 4 月 = **3.98 倍**）
            # ⇒ **季节形状本来就在数据里，是被这行除掉的**。
            # `dens_w[m] = (月总量 / 年总量) * 12` ⇒ **12 个月之和 = 12、均值 = 1**
            # ⇒ 配合 `gen_check` 里的**无偏随机取整**，**年总量在期望上不变** ✓
            _nt = np.maximum(n, 1e-9)
            _GEN_CACHE['dens_w'] = ((_nt.ravel() / float(_nt.sum())) * 12.0
                                    ).astype(np.float64)
            # ⚠ 密度场**不带**纬轴成员（实测 `'dens_lat' in C` 为 **False**）⇒
            #   下面两个 `if` 在现网数据下**都不成立**，真正的纬度来自 `gen_check` 里的
            #   **以赤道为中心的 1° 兜底**（`lat = (jy + 0.5) - d.shape[0]/2` ⇒ ±59.5 ✓）。
            #   （原注释写"密度场自带纬轴(实测 tc_clim['lat'] = …)"**已过期且与
            #    `gen_check` 里的实测矛盾**，S14/t2050 顺手改对 —— U-t2050-3。）
            if 'lat' in z.files:
                _GEN_CACHE['dens_lat'] = np.asarray(z['lat'], np.float32)
            if 'lon' in z.files:
                _GEN_CACHE['dens_lon'] = np.asarray(z['lon'], np.float32)
    except Exception:
        pass
    try:
        from simulator.simcore.envmodel import store as _st2
        z2 = _st2.load('tc_genesis')
        if z2 is not None:
            for k in ('sst', 'shear', 'rh', 'lat'):
                ck, wk = k + '_ctr', k + '_w'
                if ck in z2 and wk in z2:
                    _GEN_CACHE['w'][k] = (np.asarray(z2[ck], np.float64),
                                          np.asarray(z2[wk], np.float64))
    except Exception:
        pass
    # T2: **生成密度的模态调制**(年际 + 空间位移)。tc_genesis_modes 里的 β 是
    # 5° 分箱的(45 年样本回归 1° 网格会过拟合), 这里上采样到 1° 再叠加:
    #   dens_eff(t) = clim + Σ_m β_m · idx_m(t)
    # 厄尔尼诺会让西太生成减少、中东太增多 —— 这是纯气候态密度给不出的。
    try:
        from simulator.simcore.envmodel import store as _st3
        z3 = _st3.load('tc_genesis_modes')
        if z3 is not None and 'clim' in z3:
            clim = np.asarray(z3['clim'], np.float64)          # (12, BNY, BNX)
            modes = [str(x) for x in z3['modes']] if 'modes' in z3 else []
            betas = {}
            for nm in modes:
                k = 'b_' + nm
                if k in z3:
                    betas[nm] = np.asarray(z3[k], np.float64)
            _GEN_CACHE['modes'] = (clim, betas)
    except Exception:
        pass
    return _GEN_CACHE


def _mode_idx(clock):
    """当前(年,月)的模态标准化索引 {模态名: 值}。"""
    out = {}
    try:
        from simulator.simcore.envmodel import solver as _sv
        from simulator.simcore.envmodel import modes as _md
        if not _md.available():
            return out
        ms = _md.ModeSeries()
        v = ms.at(clock.year, clock.month)
        for i, nm in enumerate(ms.names):
            out[nm] = float(v[i])
    except Exception:
        pass
    return out


#: 每次 6h 检查的尝试概率。**实测标定**(tools 里的 genesis-only 标定脚本):
#: _GEN_RATE=0.030 时一年生成 11 个; 观测年均 72.3 个(1980-2024) → 0.030×72.3/11
#: ≈ 0.197。环境权重的平均乘积只有 0.119(四个曲线连乘), 所以绝对速率比直觉大。
#: 生成尝试率(每步)。**2026-09-21 扩域调参**: 观测西太 ~116 个扰动/年(仅 13% 发展),
#: 而我们原来一年只有 ~30 个系统、晚期把早期增速压下去之后更少(实测 2025 全年只剩 12 个)。
#: 用户明确要"更多扰动", 所以把生成率从 0.177 提到 0.45 —— 多出来的大部分会因为环境不利
#: 停在扰动/低压阶段(正好是观测里的形态), 少数发展成台风。
#: **标定口径**: 保持"能发展成台风的系统数"不变(= 0.45 时的样子, 2025 西太命名 27 个
#: 正好对上观测), 把多出来的部分全部做成扰动: 0.45/(1-0.80) = 2.25。
#: **整年实测标定**(2025 全年, 623s): R=1.6/f=0.72 时西太命名 18(观测 27)、扰动 35(观测 116)、
#: 扰动/命名 1.6(观测 4.3)。要同时满足两个比值 ⇒ f' = 0.85、R'·(1-f') = 1.5×R(1-f) ⇒ R' ≈ 4.5。
#: 用户 2026-09-21: 观测(ATCF invest 台账)全球 **~200 个扰动/年**、其中 ~103 个发展;
#: 我们实测只有 78 个/年 ⇒ 生成率 ×2.5(8.40 -> 21.0)。阻滞函数会再压掉一部分,
#: 所以最终以"跑一年数出来"为准回调。
#: ⚠⚠ **轮次 130 又重标了一次(第三次), 起因是轮次 129 修掉了生成纬轴的 1.5 倍拉伸 bug**:
#: 那个 bug 让大量抽样点落到高纬冷海/陆地上被浪费, 掩盖了真实的生成率。修后同窗口生成量翻倍,
#: 于是上面那条"8.40 -> 21.0"整条作废(它是在坏抽样器上量的)。
#: 修后实测(100 天窗口 x 两个窗口, `_GEN_TRIES=2`; `rate = _GEN_RATE * genesisRate * (0.4+random)`,
#: random 默认 0.5 ⇒ 系数 0.9):
#:     `_GEN_RATE=21`(饱和, 等价 rate≈1): 7 月 98 / 1 月 ~98 -> 外推 ~358/年
#:     genesisRate=0.05(rate 0.945): 7 月 73 + 1 月 76 = 149 -> **272/年**
#:     genesisRate=0.03(rate 0.567): 7 月 41 + 1 月 50 =  91 -> **166/年**
#:   ⇒ 这条门在 rate<1 时**线性**(149/91 = 1.64 与 0.945/0.567 = 1.67 一致),
#:     所以直接按比例定点: 要 250/年 ⇒ rate = 0.869 ⇒ **`_GEN_RATE = 0.869/0.9 = 0.97`**
#:     (等价于"每步尝试概率 87%")。年量口径 = **1 月 + 7 月两窗口相加 x365/200**,
#:     单窗口外推不可信(单半球季)。
#: ⚠⚠ **轮次 150 又重标一次(第四次), 起因是轮次 149 引入的"僵胎类"改了种群构成**:
#:     0.97 是在**没有僵胎**的种群上定的 250; 加入 `STILLBORN_FRAC=0.60` 之后,
#:     15 种子 x 两窗口实测 **7 月 77.4 + 1 月 81.4 = 158.8/200 天 ⇒ 外推 290/年**
#:     (目标 250, 超 16%)。按比例定点 ⇒ **0.97 x 250/290 = 0.836 -> 0.84**。
#:     (定点前先证明过"没撞存活名额": `tools/probe_gen_cap.py` 实测 max_alive 7 /
#:      上限 60 ⇒ 这道门线性可用, 文档里"降生成率撞上限"那个坑不成立。)
#:     产物: `output/sim/_reference/multi_year_sb60{n,h}_15seed.txt`(定点前)
#:           `output/sim/_reference/multi_year_sb60g_*_15seed.txt`(定点后复验)。
_GEN_RATE = 0.84
#: 生成的系统里"纯扰动"的占比(观测: 西太 ~116 扰动/年、约 13% 发展成 TC)
#: 反解: 目标扰动/开发者 = 116/45 ≈ 2.6  ⇒ f/(1-f)=2.6 ⇒ f≈0.72;
#: 同时让开发者数回到"2025 西太命名 27 个"时的量级 ⇒ _GEN_RATE(1-f)≈0.45 ⇒ R≈1.6
DISTURB_FRAC = 0.87
#: ── 轮次 149: **僵胎类**(任务 E: 发展率 89% -> 目标 48%) ──────────────
#: 出生时按比例抽一小类标成 `stillborn`, 只给它们在 34kt 以下很重的增长阻力,
#: 于是它们基本停在 DB/WV; **发展那一半的物理完全不动**。
#: 为什么不用 `disturb` 标签当闸门: 它覆盖 **87% 的出生**、里面包含大量后来变强的风暴
#: (实测 `disturbK=0.10` 把发展率压到 60% 的同时, P95 从 142 掉到 129、匹配 >=28 从 45% 掉到 35%)。
#: 标定(7 月窗 2 种子, 同口径):
#:     0.00 -> 发展率 89% | 匹配>=28 45% | P95 142
#:     0.60 -> **52%**     | **48%**      | **140**   <- 甜点(采纳)
#:     0.65 -> 46%         | 40%          | 124      <- 过头: 强端被削
#: ⇒ 最后那几个百分点是**用上尾换来的**, 所以停在 0.60。
STILLBORN_FRAC = 0.60
STILLBORN_K = 0.06
#: ⚠ 轮次 148: 可由 `sim['params']['disturbFrac']` 覆盖(默认 = 上面的常量)。
#: 为什么必须能调: 任务 E 的目标是"250 生成 / 120 发展 / **48% 发展率**", 而
#: `disturbK`(纯扰动增长阻力)单独用**到不了 48%**(实测 1.0/0.6/0.35 -> 77%/71%/66%)。
#: 结构解是: **让纯扰动基本不发展 + 把"纯扰动占比"降到 ~0.52** ——
#: 这样"能发展的那部分"就正好是 48%, 而绝对发展数 = 0.48 x 年生成。
#: 附带好处: 多出一批长期存在的弱扰动 -> 阶段码里 DB/WV 的记录份额上升
#: (现在 DB 12.4% 比观测的 20-24% 偏低, 本来就缺"长不大的扰动")。
#: 纯扰动的增速上限(kt/步) —— 让它们大概率原地消散, 只有环境特别有利时才组织起来
#: 同时存活上限(旧值 6 太少 —— 全球实测常有 15-25 个同时活动)
#: **同时存活的系统上限**。这是**真正的限流阀**: gen_check 里  早已 >1 饱和
#: ( 恒不成立), 所以 _GEN_RATE 提到多少都不影响年产量 ——
#: 年产量 = 本上限 / 平均寿命。实测(365 天, seed 20250801): 上限 25 时只生成 85 个/年,
#: 反推每个名额平均被占 **107 天**(系统死得太慢, 观测里扰动只活几天)。
#: 用户要求对齐观测(ATCF invest 台账: 全球 ~200 个扰动/年)⇒ 25 -> 60。
#: 注: 若日后修好弱系统迟迟不死的问题, 这个上限要回调。
_GEN_MAX_ALIVE = 60
#: 每步生成尝试次数(见 gen_check 里的说明)。
#: 6 -> 14(轮次 41)实测给出 ~150-164 扰动/年, 仍低于 ATCF invest 台账的 ~200/年
#: (5 年多种子: 均值 150.6, 范围 [141,164])。轮次 52: 14 -> 19(约 x1.36)。
#: ── **轮次 123: 19 -> 5**(任务 E 旋钮①) ──
#: 依据(轮次 117 标定曲线, `tools/probe_gen_rate.py`, 100 天窗口 x 3 档, 不改源码):
#:     tries=19 -> 外推年生成 653 / 年发展 453 / 发展率 69%
#:     tries=10 -> 416 / 274 / 66%
#:     tries= 6 -> 285 / 204 / 72%
#:   ⇒ 每减少 1 次尝试约少 33 个生成; **tries=5 正好落在用户目标 250 上**。
#: 为什么以前一路往上加: 当年用"最近 120 天保留窗内的扰动总数"当指标, 而那个数在
#: **120 天处饱和**(饱和值 = 速率 x 120 天), 于是"把饱和值抬到 ~190"被误当成"对齐观测
#: 的 200/年", 真实年生成量被抬到了 ~640(3.2 倍)。第 19 次口径问题, 已定案。
#: ⚠ 总数下降 2.6 倍会改变海洋冷尾流的累积(自限), 所以 A/C/D 的比例指标必须复验。
#: ⚠ 轮次 123 复验: `tries=5` 在 **2025-01-01 起的 100 天窗口**里只给 53/57 个(外推 ~200/年),
#: 低于那条"每减 1 次尝试少 33 个"的线性预期 —— 说明低端**非线性变陡**。
#: 而且那个窗口是**南半球季**(WP=15 / SH=38 / EP=AL=IO=0), 单窗口外推本身有偏。
#: ⇒ 改取 **6**(轮次 117 曲线: 6 -> 285, 约为目标 250 的 1.14 倍),
#:   并用 **1 月 + 7 月两个窗口**重新标定年量(单窗口外推不可信, 见 §口径)。
#: ⚠⚠ **轮次 129 又把上面整条曲线作废了一次**: 生成纬轴 bug 修好之后
#: (见 `gen_check` 里那段"1.5 倍拉伸"的注释), 同窗口生成量**翻倍**
#: (7 月 100 天: 83 -> 165/181) —— 因为修前大量抽样点被搬到高纬冷海/陆地直接浪费掉了。
#: **修后实测(7 月 100 天, `probe_gen_rate.py`)**:
#:     tries=6 -> 165-181(外推 ~600-660/年, 发展率 78%)
#:     tries=3 -> 121(~442/年, 84%) | tries=2 -> 98(~358/年, 82%)
#:     tries=1 -> 48-50(~178/年, **94%**)
#: 两点结论:
#:   ① 关系**次线性**(1->2 近乎翻倍, 到 6 已饱和) —— `_GEN_MAX_ALIVE=60` 与
#:      "风暴互相争夺海洋热/互相制造切变"在起作用;
#:   ② **发展率随生成量升高而下降**(94% -> 78%) ⇒ "250 生成 + 120 发展(48%)"
#:      **不可能只靠这一个旋钮同时满足**; 48% 必须靠 `disturbK` 那条闸门
#:      (见 `intensity_step` 里 `disturbK` 的注释: 只打纯扰动, 不碰所有弱系统的速率)。
#: 取 **2**(~358/年): 再用**连续**旋钮 `genesisRate` 收到 250
#: (它缩的是每步的尝试概率; `_GEN_RATE` 饱和时把 rate 压到 1 以下就变成线性可调)。
_GEN_TRIES = 2


def unfav_index(env, lat):
    """**不利度指数**(0 = 有利; 越大越不利)。生成权重与扰动阻滞共用同一把尺子。

    四项相加, 各自在"开始不利"的阈值处为 0:
        SST 低于 27C / 切变高于 12kt / 相对湿度低于 55% / 纬度高于 22 度。
    """
    # 湿度项曾在轮次 42 放宽到 45/30, **实测证伪并回退**: 130+ 占比 9% -> 7%(更差),
    # 发展率 57% -> 74%(离观测的 50% 更远), P95 峰值 166 -> 135。
    # 放宽阻滞只是让**中等系统变多**, 超强反而更少 ⇒ 阻滞不是 130+ 不足的原因。
    # **纬度项已移除**(2026-09-21 轮次 53)。实测各海盆的纬度项平均贡献:
    #   AL 大西洋 **0.257**(生成点平均 20.3N) / WP 0.030 / SH 0.012 / EP 0.000 / IO 0.007
    # —— 大西洋被罚得最狠, 而它从密度场输入的 15.7% 被压到产出 5.9%。
    # "纬度本身"不是不利条件: 20-25N 的副热带正是大西洋主生成区,
    # 真正决定能否发展的是海温/切变/湿度这三项热力条件, 让它们自己说话。
    # **切变阈值 12 -> 20 kt**(2026-09-21 轮次 54)。逐海盆分解证明切变项在**所有海盆**
    # 都占绝对主导(SH 0.62 / AL 0.81 / IO 0.90 / WP 0.32 / EP 0.24), 把 SST 与湿度项
    # 完全压住; 而 12 kt 只相当于 **6 m/s** —— 观测里生成被显著抑制要到 10-15 m/s
    # (即 20-30 kt)。阈值太低导致高切变海盆(SH/AL/IO)的生成被系统性掐掉。
    return (max(0.0, 27.0 - float(env['sst'])) / 2.0
            + max(0.0, float(env['shear']) - 20.0) / 15.0
            + max(0.0, 55.0 - float(env.get('rh', 60.0))) / 20.0)


def unfav_block(env, lat, k=2.2):
    """**扰动阻滞函数**: 不利条件越强, 阻滞越明显。

    关键在**凸响应** _blk^2 —— 轻微不利几乎不挡(给系统机会), 明显不利迅速挡死:
        _blk = 0     -> 1.00
        _blk = 0.3   -> 0.82
        _blk = 0.5   -> 0.58
        _blk = 0.7   -> 0.34
        _blk = 1.0   -> 0.11
        _blk = 1.5   -> 0.007

    ── 轮次 123: `k` 可由 `sim['params']['unfavK']` 覆盖(默认见 `UNFAV_K`) ──
    任务 E 的旋钮②: 生成量降到 250 以后, **发展率仍须从 ~72% 降到 ~48%**,
    而实测发展率对 `_GEN_TRIES` 几乎不敏感(6/10/19 三档都是 66-72%)。
    选这里而不是动增长速率(`riAmpForm`)的理由: 这个阻滞**只在 vmax<34 且环境不利时**
    起作用(有利环境 `unfav_index~0` ⇒ 因子≈1), 所以它挡掉的是"本就不该发展的扰动",
    **不会**像轮次 122 的 `riAmpForm` 那样把所有弱系统的增长速率一起压低
    (那次把峰值中位从 73/75 打到 57/62、130+ 从 4.5/3.4% 打到 1.4/2.9%)。
    而且分母里少掉一批"本该长成 34-50kt"的系统, 对 100+/130+ 的**占比**可能是正的。
    """
    return math.exp(-k * unfav_index(env, lat) ** 2)


#: 扰动阻滞强度的默认值(见 `unfav_block`)。2.2 = 历史值; 任务 E 的旋钮② 在它上面加。
UNFAV_K = 2.2


def _gi_weight(C, env, lat, lon=None):
    """生成点的环境权重(四因子连乘, 各曲线已归一到最大 1)。

    ## 2026-09-18: 三种"改进"全部试过并**全部回退**, 结论记录在此

    生成位置已从观测密度场抽样(T2), 密度场本身就是经验 GPI, 而本函数在它
    之上又乘一次绝对门槛 —— 我怀疑这是"同一信息用两遍"并试图消除它。
    实测(1997 全年, 括号为观测 2013-24):

        用法                        总数   WP     EP     AL     IO     SH
        **四因子直接连乘(现行)**      66   43.9   22.7   13.6    4.5   15.2
        仿射压缩 0.30+0.70w          --   27.2   17.5   13.1    7.7   34.4
        w**0.18 软闸门               --   28.2   17.4   12.1    7.1   35.2
        硬否决(只挡物理不可能)       190   44.7   20.5   10.0    3.2   21.6
        盆地归一化 w/mean_basin(w)   176   50.0   23.3    5.7    4.0   17.0
        观测                         98   41.9   19.0   23.6   17.1   ~25

    - **压缩/软闸门**压平了西太的优势(WP 41→28), 因为西太的权重本来就最高
    - **硬否决**让总数翻三倍(190), 而 AL/IO 反而更差
    - **盆地归一化**把大西洋打到 5.7% —— 因为归一化用的是**盆地全域均值**,
      而大西洋的生成集中在其主发展区(那里的权重远高于盆地均值),
      用全域均值去除反而惩罚了它

    **所以现行版本保留**。AL/IO 偏低是真实存在的偏差(13.6/4.5 vs 23.6/17.1),
    但根因不在这个函数, 更可能在  的竞争效应或各盆地寿命差异
    (EP 寿命 49 点 vs 观测 32, 占着名额不放)。留待后续。
    """
    if not C['w']:
        return clamp((env['sst'] - 26.0) / 3.0, 0, 1)
    w = 1.0
    val = {'sst': env['sst'], 'shear': env['shear'],
           'rh': env.get('rh', 60.0), 'lat': abs(lat)}
    for k, (ctr, ww) in C['w'].items():
        w = w * np.interp(val[k], ctr, ww)
    # 注(2026-09-21): 这里**不再**乘阻滞。实测把阻滞加在生成权重上时, 候选点大量落在
    # 边缘海区(平均阻滞 ~0.3), 把生成率 2.5 倍的提升吃光还倒亏(扰动数 78 -> 52)。
    # 正确的分工: **生成总数**由 _GEN_RATE 单独控制; **阻滞只作用在发展上**
    # (见 intensity_step 里的发展阻滞) —— 扰动照常出现, 但在不利环境里长不起来。
    if np.ndim(w) == 0:
        return clamp(float(w), 0.0, 1.0)
    return np.clip(w, 0.0, 1.0)


# ── §69 **生成模式**（用户 2026-10 点名：6–9 月南海季风、8 月中纬/反向季风槽）──────
# 动机（交接文档 §6.2）: 原来生成【位置】只来自一张平滑的月平均密度场
#   (`tc_clim['genesis']`) ⇒ 空间上是一整片连续分布，**没有"几种有特色的生成模式"**；
#   而 `classify_genesis` 那 5 种是**按环境**分的，实测 180 天里 **88% 落到
#   `easterly_wv` 兜底支**（`output/exploration/mgs_base365.log`）⇒ "5 种"其实只有 1 种。
#
# 每个模式 = **区域盒 + 季节(逐月份额) + 自己的核形状**，三样都来自真实 b-deck 实测：
#   份额 s_k(m) = [该模式在该月的生成数] / [该洋区该月生成数]   （`mode_share_fit.py` 现算）
#   核参数     = [该模式盒 ∩ 活跃月] 内真实生成点的 均值/σ/倾角（`mode_kernel_fit.py`）
#   盒与活跃月 = `gen_modes.md` §1.4–1.8
#
# 混合方式（**按洋区质量守恒、逐月独立**）:
#     W = Σ_k s_k(m)（落在该洋区、该月活跃的模式）
#     d[洋区] ← (1 − f·W)·d[洋区] + Σ_k f·s_k(m)·(洋区质量)·K_k
#   ⇒ 模式的**总量份额就是实测份额**（修的是"区域份额对不上"，见下），
#     核形状取自实测分布；未列入的洋区（AL/IO/SI）与未活跃的月份**逐位不变** ✓
#
# ⚠ **必须做这一步的理由（实测）**: 现行那张 `tc_clim['genesis']` 密度场在 WP 内部
#   与 b-deck 实测份额**明显不符**（`gen_field_vs_real.txt`，同一 1° 栅格、同一月）：
#       8 月 低纬 130–180E/5–15N: 真实 38.4%  密度场 **15.8%**（差 2.4 倍）
#       9 月 低纬               : 真实 49.8%  密度场 **31.8%**
#       10 月 中纬 15–25N/130–170E: 真实 13.4%  密度场 **28.6%**（差 2.1 倍）
#   ⇒ 所以"生成位置不对"不只因为场是月平均的（§6.2），**场本身的区域份额也不对**。
#
# ⚠ 默认 `genModeFrac=0` ⇒ 本表**一个字节都不参与** ⇒ 逐位不变（free_hash 已验证）。
#
# ⚠ **"8 月反向季风槽"这个名字的口径边界**: 月平均生成场上量不出"NW–SE 槽轴倾斜"
#   （实测 lat~lon 斜率 8 月 = −0.079 °/°、r = −0.26，12 个月全是负的或近零），
#   反向季风槽是**月内天气尺度**现象。这里用的是**能取到的对应物**：
#   "8 月 15–25N/130–170E 占该月 31.4%、1–5 月 = 0.0%"（见 gen_modes.md §1.6）。
#: 各模式的"洋区盒"（(lon0, lon1, lat0, lat1)）—— 份额的分母就是这个盒
GEN_MODE_BASINS = {
    'WP': (100.0, 180.0, 0.0, 60.0),
    'SP': (135.0, 290.0, -60.0, 0.0),
}
GEN_MODES = [
    # 南海季风槽：5–11 月活跃；占 WP 该月 24.5/22.8/10.8/11.9/12.7/9.3/9.3%
    dict(name='scs_monsoon', kind='monsoon_dep', rmw0=44.0, org0=0.40,
         box=(105.0, 122.0, 5.0, 22.0), latc=15.2, lonc=115.9,
         sig_lat=3.6, sig_lon=3.6, tilt=0.123, basin='WP',
         share=[0.000, 0.000, 0.000, 0.000, 0.245, 0.228, 0.108, 0.119, 0.127,
                0.093, 0.093, 0.000]),
    # 8 月中纬（用户叫"反向季风槽"）/ WNP 15–25N：1–5 月为 0，8 月峰 31.4%
    dict(name='rmt_midlat', kind='itcz', rmw0=32.0, org0=0.58,
         box=(130.0, 170.0, 15.0, 25.0), latc=18.9, lonc=147.6,
         sig_lat=2.8, sig_lon=10.5, tilt=0.031, basin='WP',
         share=[0.000, 0.000, 0.000, 0.000, 0.000, 0.102, 0.281, 0.314, 0.246,
                0.155, 0.000, 0.000]),
    # 低纬 ITCZ / 东风波：全年，占 WP 该月 34.1…65.2%（密度场在这里**少了 2.4 倍**）
    dict(name='lowlat_itcz', kind='easterly_wv', rmw0=24.0, org0=0.72,
         box=(130.0, 180.0, 5.0, 15.0), latc=9.5, lonc=149.7,
         sig_lat=2.7, sig_lon=11.7, tilt=-0.039, basin='WP',
         share=[0.421, 0.478, 0.341, 0.417, 0.490, 0.509, 0.470, 0.373, 0.493,
                0.652, 0.644, 0.465]),
    # 副热带 / 冷涡：25–40N，6–10 月（8 月 9.7%）
    dict(name='subtrop_low', kind='cold_low', rmw0=38.0, org0=0.46,
         box=(100.0, 180.0, 25.0, 40.0), latc=27.6, lonc=149.5,
         sig_lat=2.4, sig_lon=17.8, tilt=0.008, basin='WP',
         share=[0.000, 0.000, 0.000, 0.000, 0.000, 0.006, 0.051, 0.097, 0.019,
                0.016, 0.000, 0.000]),
    # 南半球：珊瑚海 / 澳东北（12–4 月；SP 覆盖只 13 年，份额样本薄，按实测填）
    dict(name='sh_coral', kind='itcz', rmw0=32.0, org0=0.58,
         box=(145.0, 165.0, -25.0, -5.0), latc=-13.9, lonc=155.9,
         sig_lat=4.2, sig_lon=6.3, tilt=0.248, basin='SP',
         share=[0.480, 0.276, 0.167, 0.500, 0.000, 0.000, 0.000, 0.000, 0.000,
                0.000, 0.000, 0.214]),
    dict(name='sh_ausne', kind='itcz', rmw0=32.0, org0=0.58,
         box=(165.0, 180.0, -25.0, -5.0), latc=-12.9, lonc=171.4,
         sig_lat=4.6, sig_lon=5.0, tilt=0.453, basin='SP',
         share=[0.200, 0.276, 0.167, 0.375, 0.000, 0.000, 0.000, 0.000, 0.000,
                0.000, 0.000, 0.214]),
]


def _gen_mode_blend(sim, d: np.ndarray, f: float) -> np.ndarray:
    """把月抽样分布 d 的区域份额**按实测份额校正**（按洋区质量守恒、逐月独立）。默认不调用。

    为什么不是"加一个高斯核": 核会**叠加**在原有场上 ⇒ 残差自己的份额也还在
    （实测 2 月低纬：真实 47.8%，加核后 **84.6%**，比不改（70.1%）还远）。
    所以这里改成**按目标份额等比缩放该模式的区域盒**（迭代 3 遍、放大封顶 4×）:
        λ_k = (1 − f) + f·clip(t_k / p_k, 1/4, 4),   在区域盒内乘 λ_k，再把该洋区归一化回原质量
      （t_k = 该模式的**实测**占洋区该月份额；p_k = 当前场在盒内的份额）
    ⇒ f=0 时 λ≡1 ⇒ 逐位不变 ✓；f=1 时盒份额收敛到实测值 ✓
    ⇒ 未列入的洋区（AL/IO/SI）与"该月无活跃模式"的情形**逐位不变** ✓

    模式的**空间形状**就用密度场自己在该盒内的形状（它本来就是观测拟合的分布），
    模式提供的是**离散身份 = 区域 + 季节 + 份额**（用户要的"生成种类"）。
    """
    ny, nx = int(d.shape[0]), int(d.shape[1])
    if not GEN_MODES:
        return d
    m = max(1, min(12, int(sim['month']))) - 1
    tot = float(d.sum())
    if tot <= 0.0:
        return d
    out = np.array(d, np.float64, copy=True)
    lat = ((np.arange(ny) + 0.5) - ny / 2.0)[:, None]
    lon = ((np.arange(nx) + 0.5) * (360.0 / nx))[None, :]
    for bn, (bx0, bx1, by0, by1) in GEN_MODE_BASINS.items():
        bmask = (lat >= by0) & (lat <= by1) & (lon >= bx0) & (lon <= bx1)
        if not bmask.any():
            continue
        acts = []
        for md in GEN_MODES:
            if md.get('basin') != bn or float(md['share'][m]) <= 0.0:
                continue
            x0, x1, y0, y1 = md['box']
            bm = (lat >= y0) & (lat <= y1) & (lon >= x0) & (lon <= x1)
            if bm.any():
                acts.append((bm, float(md['share'][m])))
        if not acts:
            continue
        bmass0 = float(out[bmask].sum())
        if bmass0 <= 1e-15:
            continue
        for _ in range(3):                       # 迭代：盒互相邻近时也能收敛
            bmass = float(out[bmask].sum())
            if bmass <= 1e-15:
                break
            for bm, t in acts:
                p = float(out[bm].sum()) / bmass
                if p <= 1e-12:
                    continue
                lam = (1.0 - f) + f * min(4.0, max(0.25, t / p))
                if lam != 1.0:
                    out[bm] *= lam
            s = float(out[bmask].sum())
            if s > 1e-15:
                out[bmask] *= bmass0 / s         # 只在本洋区内重新分配，不加总量
    np.maximum(out, 0.0, out=out)
    s = float(out.sum())
    if s > 0.0 and abs(s - tot) > 1e-9 * tot:
        out *= tot / s                           # 保月总量（只在真需要时动，避免碰未改区域）
    return out


def _gen_mode_attr(sim, lon: float, lat: float):
    """给一个生成点定**模式身份**（取"该月份额 × 核密度"最大的那个；都不在核里则 None）。"""
    m = max(1, min(12, int(sim['month']))) - 1
    best, bestv = None, 1e-12
    for md in GEN_MODES:
        if float(md['share'][m]) <= 0.0:
            continue
        x0, x1, y0, y1 = md['box']
        if not (y0 <= lat <= y1 and (x0 <= lon <= x1 if x0 <= x1 else (lon >= x0 or lon <= x1))):
            continue
        dx = abs(((lon - md['lonc'] + 180.0) % 360.0) - 180.0) / max(1.0, float(md['sig_lon']))
        dy = ((lat - md['latc'] - md['tilt'] * (lon - md['lonc']))
              / max(1.0, float(md['sig_lat'])))
        v = float(md['share'][m]) * math.exp(-0.5 * (dx * dx + dy * dy))
        if v > bestv:
            best, bestv = md, v
    return best


def classify_genesis(sim, env, lon: float, lat: float) -> dict:
    """按生成时的环境把初生系统归类(用户 2026-09-17 需求 4)。

    真实的生成不止 ITCZ 一条路。陈&吴(2022, MWR)的结论: **CF(塌眼)常来自
    东风波、环境更干、初始尺寸更小; BF(卷眼)常来自季风低压、环境更湿、
    初始尺寸更大**。这个分类直接决定新台风的初始 RMW 与 core_org ——
    也就是"为什么有些系统天生就能冲高、有些不能"。

    判别只用生成点能采到的量, 不依赖任何人工标注:

      monsoon_dep  季风低压    低层**西风**(u850 > 0) + 湿(rh 高) + 低纬
      easterly_wv  东风波      低层**东风**(u850 < 0) + 干(rh 低)
      itcz         ITCZ/季风槽 低层气旋式切变 + 高 SST + 辐合带纬度
      cold_low     冷涡/切断低压 200hPa 温度偏低 + SST 偏低也能生成 + 副热带
      ex_ss        变性/副热带转热 中高纬(>25N) + SST 偏低
    """
    e = sim['env']
    u850 = env_bilinear(e['u850'], lon, lat) if 'u850' in e else 0.0
    t200 = env.get('t200')
    if t200 is None and 't200' in e:
        t200 = env_bilinear(e['t200'], lon, lat)
    sst, rh, shear = env['sst'], env['rh'], env['shear']
    alat = abs(lat)
    # ITCZ 纬度带(与 ocean_step 里的季节摆动同口径)
    _itcz = 6.0 + 5.0 * math.cos(2 * math.pi * (sim['month'] - 8) / 12.0)
    if alat > 25.0 and sst < 27.0:
        kind = 'ex_ss' if sst < 25.0 else 'cold_low'
    elif t200 is not None and (t200 - 273.15 if t200 > 150 else t200) > -48.0             and alat > 18.0:
        kind = 'cold_low'
    elif u850 > 1.5 and rh > 62.0 and alat < 20.0:
        kind = 'monsoon_dep'
    elif u850 < -1.5 and rh < 62.0:
        kind = 'easterly_wv'
    else:
        kind = 'itcz' if abs(alat - _itcz) < 9.0 else 'easterly_wv'
    # 初始结构: 小 RMW / 高组织度 → 更容易走 CF 路径(增强快、上限高)
    prof = {
        'easterly_wv': (24.0, 0.72),      # 小 RMW / 高组织度 → CF
        'itcz':        (32.0, 0.58),
        'monsoon_dep': (44.0, 0.40),      # 大 RMW / 低组织度 → BF
        'cold_low':    (38.0, 0.46),
        'ex_ss':       (48.0, 0.34),
    }
    rmw0, org0 = prof.get(kind, (32.0, 0.55))
    return {'kind': kind, 'rmw0': rmw0, 'org0': org0,
            'u850': float(u850), 'rh': float(rh)}


def gen_check(sim) -> None:
    if not sim['params']['genesis']:
        return
    if len([t for t in sim['tcs'] if not t['dead']]) >= _GEN_MAX_ALIVE:
        return
    rng = sim['rng']
    C = _gen_tables()
    rate = _GEN_RATE * sim['params']['genesisRate'] * (0.4 + sim['params']['random'])
    if rng() > rate:
        return
    # **每步的生成尝试次数** —— 这才是真正的限流点(2026-09-21 查明):
    # 全年 1460 步 x 6 次 = 8760 次尝试, 实测只生成 85 个/年 ⇒ 采样点上的平均接受率仅 0.0097。
    # (_GEN_MAX_ALIVE 实测不是瓶颈; `_GEN_RATE` 当年确实饱和过 —— 但那是在
    #  **0.97 且没有僵胎类**的旧配置下。轮次 150 把它重标到 **0.84** 之后
    #  `rate = 0.84*1.0*(0.4+random)` 在 random<0.5 时 <1, 会拒掉约 24% 的步 ——
    #  所以"早已饱和无效"这句话**已经过期**, 不要再据此判断 `_GEN_RATE` 无用。)
    # 用户目标: 观测全球 ~200 个扰动/年 ⇒ 6 -> 14(2.33 倍)。
    # ── B(S14/t2050): **按月的尝试次数**（`genMonthWeight`，**默认开**）──────────────
    # `dens_w[month]` 月均 = 1 ⇒ `_tries_eff = _GEN_TRIES * dens_w[month]` 的**期望年总量不变**；
    # 用**无偏随机取整**（小数部分按概率进位）把浮点次数变成整数次，避免系统偏差。
    # ⚠ **关闭时（`genMonthWeight=False`）`_frac <= 0` ⇒ 连 `rng()` 都不抽** ⇒ 与旧行为**逐位相同** ✓
    #
    # **2026-10-05 由 Lead 的整年 A/B 判定后改为默认开**（4 条 1460 步跑完）：
    #   · 总量不膨胀：A 均值 **196.1**、B 均值 **199.1** ⇒ 差 **1.5%（噪声内）** ✓
    #     （149 天窗曾看到 +35%，那是"7–11 月权重和 7.38 vs 平坦 5.00"的**窗口效应**；
    #      整年权重和 = 12 ⇒ 持平 ⇒ `_GEN_RATE` **不需要重标**）
    #   · 形状对了：B 臂合并两种子排序 **9 > 8 > 10 > 7 > …** 与 `dens_w` 前四名**完全一致**；
    #     A 臂排序 7 > 10 > 6 > 9（4 月权重最低却有 18–21 个）⇒ **平的、与权重无关** ✓
    #   · 南半球未被挤掉：A 31/182、37/142；B 25/168、22/183 ⇒ 都在 **13–26%**
    #   · **饱和关闭**：4 条全部 **撞上限 0/1460** 步 ⇒ 选项 2 永久关闭 ✓
    # ⚠ 方法论（Lead 2026-10-05）：**单种子、单个月度样本的"排序"测不出季节形状** ——
    #   每月只有 20–30 个样本、泊松噪声 ±20%，而权重跨度只有 0.5–2.0 ⇒
    #   **必须合并多个种子看均值**（一合并前四名立刻对齐）。
    _mw = bool((sim.get('params') or {}).get('genMonthWeight', True))
    # §69 **生成模式总旋钮**（默认 0 = 全关 ⇒ 抽样分布逐位不变、且不消耗任何随机数）
    _mf = float(sim['params'].get('genModeFrac', 0.0))
    _tries_eff = float(_GEN_TRIES)
    if _mw and C.get('dens_w') is not None:
        _tries_eff = float(_GEN_TRIES) * float(C['dens_w'][int(sim['month']) - 1])
    _n_try = int(_tries_eff)
    _frac = _tries_eff - _n_try
    if _frac > 0.0 and rng() < _frac:
        _n_try += 1
    for _ in range(_n_try):
        if C['dens'] is not None:
            d = np.array(C['dens'][int(sim['month']) - 1], np.float64)
            # 模态调制: 上采样 5° 的 β 到 1° 再叠加(只在有 β 时)
            if C.get('modes') is not None:
                _cl, _bs = C['modes']
                _mi = _mode_idx(clock(sim))
                # **相位钉住时, 让生成密度的模态调制跟着相位走**(2026-09-21 轮次 55)。
                # 生成密度本来就有 ENSO 模态调制(tc_genesis_modes 的 beta, 注释写着
                # "厄尔尼诺会让西太生成减少、中东太增多"), 但模态指数是按**日历时间**
                # `_mode_idx(clock(sim))` 取的 —— 完全不看 params['ensoPin']。
                # 所以之前三个相位跑出来生成位置几乎不动(35%/36%/46%), 不是缺机制,
                # 而是**调制所依赖的指数没跟着相位走**。这里在钉住时用相位值覆盖它。
                _pin = (sim.get('params') or {}).get('ensoPin')
                if _pin is not None and _mi:
                    for _nmk in list(_mi.keys()):
                        _u = _nmk.upper()
                        # 模态名实测为: nino34 / oni / tni 都属于 ENSO 家族
                        if 'ENSO' in _u or 'NINO' in _u or _u in ('ONI', 'TNI'):
                            _mi[_nmk] = float(_pin)
                _ny, _nx = d.shape
                _by, _bx = _cl.shape[1], _cl.shape[2]
                _sy, _sx = _ny // _by, _nx // _bx
                if _sy > 0 and _sx > 0 and _mi:
                    _m = _cl[int(sim['month']) - 1].copy()
                    for nm, b in _bs.items():
                        if nm in _mi:
                            _m += b[int(sim['month']) - 1] * _mi[nm]
                    _m = np.maximum(_m, 0.0)
                    # **量级对齐**(2026-09-21 轮次 64, 找到一个结构性 bug):
                    # d 是**归一化到和为 1** 的 1 度密度(平均 2.3e-05),
                    # 而 _m = clim + Σ b·idx 是 **5 度分箱**的量(平均 9.7e-03, 总和 16.8)。
                    # 原来写 `d = d + _m` ⇒ **_m 平均是 d 的 420 倍**, 精心拟合的 1 度密度场
                    # 被完全淹没(只占 0.24%), 实际抽样用的是 _m —— 这也解释了
                    # "beta 符号明明是对的、结果却几乎没反应"。
                    # 修法: 先把 _m 归一化成概率分布(与 d 同量级), 再等权合成。
                    _ms = float(_m.sum())
                    if _ms > 0:
                        _m = _m / _ms
                        _mw = np.repeat(np.repeat(_m, _sy, 0), _sx, 1)[:_ny, :_nx]
                        d = 0.5 * d + 0.5 * _mw
            d = np.maximum(d, 0.0)
            # ── §69 **生成模式混合**（默认关 ⇒ 逐位不变）──────────────────────────
            # 为什么: 原来生成位置只来自**一张平滑的月平均密度场** ⇒ 空间上是一整片
            #   连续分布、没有"几种有特色的模式"（用户 2026-10 原话点名"6–9 月南海季风、
            #   8 月反向季风槽"）。这里把每个月的抽样分布改成
            #   "**按实测份额校正各区盒**"（口径与效果见 `_gen_mode_blend` 的 docstring
            #   与 `gen_mode_check_out.txt`）；逐月独立、按洋区质量守恒。
            #   ⚠ 第一版写的是"加高斯核"，被自己的实测否掉（2 月低纬 47.8% → 加核 84.6%）。
            #   `_mf` 在函数开头定义（0 = 全关 ⇒ 逐位不变）。
            if _mf > 0.0:
                d = _gen_mode_blend(sim, d, _mf)
            if d.sum() <= 0:
                break
            flat = d.ravel()
            idx = int(np.searchsorted(np.cumsum(flat), rng() * flat.sum()))
            idx = min(idx, flat.size - 1)
            jy, ix = divmod(idx, d.shape[1])
            # 密度场坐标: 60S-60N / 0-360E, **1 度**网格(实测 dens 形状 (12,120,360))
            _dl = C.get('dens_lat')
            if _dl is not None and len(_dl) > 1:
                # 行 jy 对应密度场自己的纬度(线性映射到它自己的轴)
                _j = int(round(jy * (len(_dl) - 1) / max(1, d.shape[0] - 1)))
                lat = float(_dl[_j])
            else:
                # ⚠ **轮次 129 修掉一个实质性 bug**: 这里原来写
                #     lat = DOM_LAT0 + (jy + 0.5) * ((DOM_LAT1 - DOM_LAT0) / d.shape[0])
                # 而 `DOM_LAT0/LAT1` 是**环境域**(±90), `d.shape[0]` 是**密度场的行数**(120)
                # ⇒ 相当于把密度场的纬轴按 **1.5 倍拉伸**(赤道不动):
                #     真 10N -> 15.8 | 真 15N -> 21.8 | 真 20N -> 30.8
                # 后果就是"生成纬度系统性偏北": 轮次 128 实测模型比观测**偏北 4.4-6.8 度**
                # (逐月同海盆对比), 而这个拉伸正好给出同量级、同方向的偏移。
                # `tc_clim` 里**没有** `lat` 成员(实测 `'dens_lat' in C` 为 False),
                # 所以这条兜底一直在生效 —— 这也是当年那句注释"不许再写死 ±60/120"
                # 想避免、却因为兜底分支没跟上而留下的坑。
                # 密度场的真实纬轴是 **-59.5..+59.5**(1 度, 120 行, 赤道居中),
                # 所以正确兜底是"以赤道为中心的 1 度栅格":
                lat = (jy + 0.5) - d.shape[0] / 2.0
                if abs((DOM_LAT1 - DOM_LAT0) - float(d.shape[0])) > 0.5:
                    logger.debug('密度场行数 %d 与假设的 1 度栅格不一致', d.shape[0])
            _ll = C.get('dens_lon')
            lon = (float(_ll[int(round(ix * (len(_ll) - 1) / max(1, d.shape[1] - 1)))])
                   if _ll is not None and len(_ll) > 1
                   else (ix + 0.5) * (360.0 / d.shape[1]))
        else:
            zone = GEN_ZONES[int(rng() * len(GEN_ZONES))]
            _, lon0, lon1, lat0, lat1, _ = zone
            lon = lon0 + rng() * (lon1 - lon0)
            lat = lat0 + rng() * (lat1 - lat0)
        env = sample_env(sim, lon, lat)
        if env['land'] > 0.3:
            continue
        C['_month'] = int(sim['month'])
        if rng() < _gi_weight(C, env, lat, lon):
            # 从**扰动(DB)**起步: 用户要看到 DB→TD→TS 的过程
            # **生成时的初始强度 8-16kt**(原来 13-21): 观测里扰动从 10-15kt 起步、
            # 要 1-2 天才组织成 TD/TS(前生期中位 60h); 原来一起步就 20kt 上下, 于是
            # 一步就够到 TD 门槛(20kt), 扰动段被压成 12h。降下来之后, 前生期长度
            # **完全由增长支上限与基础松弛决定** —— 组织期乘子(GEN_GEST_H/_gest)已在
            # 轮次 33 作为死代码删除(它当时恒为 1.0, `tc['gest']` 全仓只写不读)。
            # 一起把前生期拉向观测的 42h(扰动)+15h(低压)。
            # **独立扰动群**(用户 2026-09-21: "一年就这么几个扰动?"):
            # 观测西太 ~116 个扰动/年, 其中只有 ~13% 发展成 TC。所以这里 75% 的生成
            # 是"纯扰动"(vmax 6-13kt), 它们会以 DB/WV
            # 身份进  目录、多数原地消散; 剩下 25% 才有机会发展成 TD/TS/TY。
            _dist = rng() < float(sim['params'].get('disturbFrac', DISTURB_FRAC))
            # ── 轮次 149: **僵胎类**(与 disturb 标签无关, 默认 0 = 不启用) ──
            # 轮次 148 实测: 拿 `disturb` 当"长不大"的闸门是错的 —— 它覆盖 **87% 的出生**,
            # 里面包含大量后来变强的风暴; 一闸下去发展率从 89% 降到 74%/60%,
            # 但 **P95 从 142 掉到 134/129、到过≥28 从 42% 掉到 36/35%、寿命 126h 掉到 94h**。
            # 正确做法: 出生时按比例把一部分标成"**僵胎**"(不长大的弱扰动, 记 DB/WV),
            # 剩下的照**现有物理**发展 ⇒ 发展率 = 1 - stillbornFrac 可调, 而上尾/最高纬/寿命
            # 由"发展那一半"决定、不受影响; 僵胎变多还会把 DB/WV 的记录份额抬上去(修 D)。
            _sbf = float(sim['params'].get('stillbornFrac', STILLBORN_FRAC))
            # ⚠ 必须短路: 只有真的要抽"僵胎"时才消耗随机数 —— 否则默认配置下
            # 每次生成都多抽一个 rng, 随机流改变 ⇒ 位同校验(`tools/free_hash.py`)会失败,
            # 也就无法证明"默认行为没变"。
            _sb = _sbf > 0.0 and rng() < _sbf
            # 开发者从 15-20kt 起步(观测自评首报中位就在 15-20kt); 纯扰动仍 6-13kt
            tc = make_tc(sim, lon, lat, (6.0 + rng() * 7.0) if _dist
                         else (15.0 + rng() * 5.0))
            if _dist:
                tc['disturb'] = True
            if _sb:
                tc['stillborn'] = True
            # **阶段 7: 按生成类型定初始结构**(RMW 与核心组织度)。
            # 这是把"生成分类"和"强度上限"接起来的那一环 —— 季风低压出身
            # 的大 RMW/低组织度系统天生就难冲高, 与观测一致。
            try:
                g = classify_genesis(sim, env, lon, lat)
                # §69: 记录**生成模式身份**（有区域/季节/份额的离散身份）。
                # ⚠ **默认只写 `genMode` 标签，不动结构与强度** —— 用户【1.1】的界是
                #   "本阶段只调路径、不管寿命/强度"，而 `genKind` 会经 `rmw0/org0`
                #   直接改强度上限。要让模式同时决定初始结构，另开 `genModeStruct=1`
                #   （默认 0 ⇒ 连这一段都不执行 ⇒ 逐位不变 ✓）。
                if _mf > 0.0:
                    _md = _gen_mode_attr(sim, lon, lat)
                    if _md is not None:
                        tc['genMode'] = _md['name']
                        if float(sim['params'].get('genModeStruct', 0.0)) > 0.0:
                            g = dict(g)
                            g['kind'] = _md['kind']
                            g['rmw0'] = _md['rmw0']
                            g['org0'] = _md['org0']
                tc['genKind'] = g['kind']
                tc['rmw'] = clamp(g['rmw0'] * (0.85 + 0.30 * rng()), 8.0, 90.0)
                tc['core_org'] = clamp(g['org0'] + 0.10 * (rng() - 0.5), 0.05, 1.0)
            except Exception:
                tc['genKind'] = 'itcz'
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
    # 云场版本号: 渲染侧据此判断云图能否复用(云场不变则整幅云图逐像素相同)
    sim['_cldVer'] = sim.get('_cldVer', 0) + 1
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
    # 系数按 kt/km 量级取 2.2e2 并收到 1.0: 原 2.2e5 让 ~30% 格点钉在上界、
    # ~70% 恰为 0, 辐合退化成符号函数
    conv = np.clip(-div * 2.2e2, 0.0, 1.0)
    noise = sim['cldNoise'].reshape(ny, nx)
    prod = (0.028 + 0.30 * conv + 0.055 * (noise * 0.5 + 0.5)) * moist
    lo, la = LONc.copy(), LATc.copy()
    for _ in range(sub):
        u = _sample_grid(sim['env']['uMid'], ENVNX, ENVNY, fx_env, fy_env)
        v = _sample_grid(sim['env']['vMid'], ENVNX, ENVNY, fx_env, fy_env)
        # TC 涡旋贡献(中层近似 0.5 加权已在 uMid; 这里补解析涡旋以出现眼墙/雨带结构)
        # 局部窗口: 900km 之外贡献恒为 0, 原实现逐台风在全幅 360x120 网格上做
        # ~15 次数组运算(实测 ~100ms/次), 改成只取包围盒切片后是小数组
        _dlat_w = 900.0 / 110.57
        grid2d = (CLDNY, CLDNX)
        for tc in sim['tcs']:
            if tc['dead']:
                continue
            _clon, _clat = tc['lon'], tc['lat']
            _dlon_w = 900.0 / max(1.0, 111.32 * math.cos(_clat * C['DEG']))
            _i0 = max(0, int((_clon - _dlon_w - C['LON0']) / C['CLDD']))
            _i1 = min(CLDNX, int((_clon + _dlon_w - C['LON0']) / C['CLDD']) + 2)
            _j0 = max(0, int((_clat - _dlat_w - C['LAT0']) / C['CLDD']))
            _j1 = min(CLDNY, int((_clat + _dlat_w - C['LAT0']) / C['CLDD']) + 2)
            if _i1 <= _i0 or _j1 <= _j0:
                continue
            # broadcast_to 是视图: 1D / (ny,1) / 2D 三种网格形状都能切
            lonw = np.broadcast_to(LONc, grid2d)[_j0:_j1, _i0:_i1]
            latw = np.broadcast_to(LATc, grid2d)[_j0:_j1, _i0:_i1]
            dx = (_clon - lonw) * 111.32 * np.cos(latw * C['DEG'])
            dy = (_clat - latw) * 110.57
            r2 = dx * dx + dy * dy
            mask = r2 <= 900.0 * 900.0
            r = np.sqrt(np.maximum(r2, 1.0))
            Vv = np.zeros_like(r)
            R = tc['rmw']
            inside = r <= R
            Vv[inside] = tc['vmax'] * (r[inside] / R) ** 0.75
            outside = ~inside
            Vv[outside] = tc['vmax'] * (r[outside] / R) ** (-alpha_outer(tc['vmax']))
            phi = np.arctan2(dy, dx)
            u[_j0:_j1, _i0:_i1] += np.where(mask, -Vv * np.sin(phi), 0.0)
            v[_j0:_j1, _i0:_i1] += np.where(mask, Vv * np.cos(phi), 0.0)
        # kt → km/h (×1.852) 半拉格朗日位移
        lo = lo - u * 1.852 * dt_s / km_l
        la = la - v * 1.852 * dt_s / km_t
    c = _sample_grid(cld.reshape(ny, nx), nx, ny, fx_cld, fy_cld)
    c = c * decay + prod * dt_h * (1.0 - c * 0.85)
    sim['cld'] = np.clip(c, 0, 1.15).astype(np.float32).reshape(-1)
    # 注意: 冷尾流衰减/平流不在这里 —— 见 ocean_step()。
    # 原先搭在本函数末尾, 一旦关掉云图(模拟模式已不要云图)尾流就永不恢复。


def _advect_env(field: np.ndarray, u_kt: np.ndarray, v_kt: np.ndarray,
                dt_h: float, scale: float = 1.0) -> np.ndarray:
    """0.5° env 网格上的半拉格朗日上游平流(经度方向环绕)。

    field/u/v 都是 (ENVNY, ENVNX)。u/v 单位 kt(海里/小时), scale 是把
    大气低层风换算成表层漂流的速度比例。
    """
    ny, nx = ENVNY, ENVNX
    # ── 早退 1: 场全零 ──
    # 绝大多数时刻洋面上根本没有尾流(只有活跃台风附近才有)。实测 ocean_step
    # 30 ms/次、其中 _advect_env 占 20 ms —— 而一年 6h 步长要调 8760 次。
    if not field.any():
        return field
    # 轮次 162: `km_l` 与 `np.maximum(km_l, 1.0)` 都只依赖纬度网格 ⇒ 从
    # `_const_grids()` 取(原式每步要各算 12 次)。
    _CGad = _const_grids()
    dx = -u_kt * 1.852 * dt_h * scale / _CGad['km_l_c'] / C['ENVD']
    dy = -v_kt * 1.852 * dt_h * scale / 110.57 / C['ENVD']
    # ── 早退 2: 位移可忽略 ──
    # 轮次 162: 这对 max **原来被算了两遍**(这里一次, 下面算 `_pad` 时又一次),
    # 而 `dx`/`dy` 在两者之间没有变化 ⇒ 提出来只算一次, **逐位相同**。
    # 实测 `np.abs(x).max()` 在全网格上约 2.9 ms, 12 次/步 ⇒ 省下的不是零头。
    _dmax = max(float(np.abs(dx).max()), float(np.abs(dy).max()))
    if _dmax < 1e-3:
        return field
    # ── 只在**非零区域的包围盒**上做 ──
    # 尾流半径约 200 km ≈ 4 格, 而整个场是 240x720 = 172800 格 —— 全网格做纯属
    # 浪费。包围盒外扩 max 位移(上游来的质量)+2 格余量后仍然精确。
    nzj, nzi = np.nonzero(field)
    # 轮次 162: 用上面已经算好的 `_dmax`(原来这里**又算了一遍**同一对 max)。
    _pad = int(np.ceil(_dmax)) + 2
    j0 = max(0, int(nzj.min()) - _pad)
    j1 = min(ny, int(nzj.max()) + _pad + 1)
    i0 = int(nzi.min()) - _pad
    i1 = int(nzi.max()) + _pad + 1
    # **经度一旦跨边界就退回全网格**: 切片不能环绕, 越界时会静默截短
    # (实测报 "operands could not be broadcast (1,357) (134,354)")。
    # 尾流大多不跨边界, 所以这个退路极少走到。
    if i0 < 0 or i1 > nx or (j1 - j0) >= ny:
        # **坐标斜坡必须是 float64**: dx/dy 由 us/vs(float32) 算出, 用 float32
        # 的斜坡相加会丢掉有效位。原实现就是 float64, 这里保持一致。
        fx = np.mod(np.arange(nx, dtype=np.float64)[None, :] + dx, nx)   # 环绕
        fy = np.clip(np.arange(ny, dtype=np.float64)[:, None] + dy, 0.0, ny - 1.0)
        return _sample_grid(field, nx, ny, fx, fy)
    _dxs = dx[j0:j1, i0:i1]
    _dys = dy[j0:j1, i0:i1]
    fx = np.mod(np.arange(i0, i1, dtype=np.float64)[None, :] + _dxs, nx)
    fy = np.clip(np.arange(j0, j1, dtype=np.float64)[:, None] + _dys,
                 0.0, ny - 1.0)
    sub = _sample_grid(field, nx, ny, fx, fy)
    out = field.copy()
    out[j0:j1, i0:i1] = sub
    return out


# ── 海洋混合层(2 层柱) ────────────────────────────────────────
# 混合层 = 被风和浮力对流充分搅拌、温度近似均匀的上层海水。它的**深度**决定了
# 台风能把它冷多快: 同样的热量亏损摊到 100m 深的水柱上只降一点, 摊到 30m 上
# 就降很多。暖池通常深 40~60m(抗冷却), 副热带/冷涡区 80~120m 且层结浅(易冷却)。
RHO_AIR = 1.22            # kg/m³
CD_WIND = 1.5e-3          # 海面拖曳系数(经典值)
F_MIN = 1.0e-5            # 科氏参数下限(约 4° 纬, 避免赤道除零)
THERMO_GRAD = 0.05        # 温跃层温度梯度 (°C/m)
RHO_C = 4.086e6           # ρ·c_p (J/(m³·K)), 用于热含量换算


_MLD_CACHE: dict = {}


def mixed_layer_clim(LAT, month):
    """混合层深度气候态(m): 暖池浅(35~50m), 副热带深(80~120m), 中高纬冬深夏浅。

    结果只依赖 (网格, month), 而 month 一年只变 12 次; 但  每 1 小时
    调一次(6h 步长下每步 6 次)。实测 30 天里它是 4.3 秒 —— 占 ocean_step 的 41%。
    所以按**规范网格身份 + 月份**缓存(不用 id(), 见 sst_clim_cached 的教训)。
    """
    g = _env_grids()
    if LAT is g[0]:
        k = int(month)
        v = _MLD_CACHE.get(k)
        if v is None:
            v = _mld_raw(LAT, month)
            _MLD_CACHE[k] = v
        return v
    return _mld_raw(LAT, month)


def _mld_raw(LAT, month):
    """混合层深度的实际计算(内部)。"""
    a = np.abs(LAT)
    base = (36.0 + 52.0 * np.exp(-((a - 26.0) / 13.0) ** 2)
            + 26.0 * np.exp(-((a - 45.0) / 16.0) ** 2))
    season = np.where(LAT >= 0, month, (month + 6 - 1) % 12 + 1)
    win = np.clip(np.cos(2.0 * np.pi * (season - 1) / 12.0), 0.0, 1.0)
    return base + (16.0 + 0.85 * a) * win * np.exp(-((a - 42.0) / 28.0) ** 2)


def ocean_column_init(sim, sst_ref=None):
    """建海洋柱状态: Tml(混合层温度≈SST), h(混合层深度), Tsub(温跃层顶温度)。

    初值由气候态给出: Tml=sst_clim(或 sst_ref, 数据模式传真实海温),
    h=mixed_layer_clim, 层结 dT 取 4~8.5 度(低纬层结强、高纬弱)。
    """
    ocn = sim.get('ocn')
    if ocn is not None:
        return ocn
    LAT, LON = _env_grids()
    tml = (np.asarray(sst_ref, dtype=np.float32).copy() if sst_ref is not None
           else np.asarray(sst_clim_cached(LAT, LON, sim['month']),
                           dtype=np.float32).copy())
    h = mixed_layer_clim(LAT, sim['month']).astype(np.float32)
    d_t = (4.0 + 4.5 * np.exp(-((np.abs(LAT) / 22.0) ** 2))).astype(np.float32)
    ocn = {'Tml': tml, 'h': h, 'Tsub': (tml - d_t).astype(np.float32),
           'ohc_kj': np.zeros_like(tml)}
    sim['ocn'] = ocn
    return ocn


def d26_from_column(sim):
    """由 2 层柱诊断 26°C 等温线深度(m) 与热含量(kJ/cm²)。

    剖面: [0,h] 内 T=Tml; h 以下按 THERMO_GRAD 递减。
    """
    ocn = sim['ocn']
    tml, h, tsub = ocn['Tml'], ocn['h'], ocn['Tsub']
    warm = np.maximum(0.0, tml - 26.0)
    d26 = np.where(tml > 26.0, h + np.maximum(0.0, tsub - 26.0) / THERMO_GRAD, 0.0)
    # ∫(T-26)dz = 混合层部分 + 温跃层三角部分
    integ = warm * h + 0.5 * np.maximum(0.0, tsub - 26.0) ** 2 / THERMO_GRAD
    return d26.astype(np.float32), (RHO_C * integ / 1.0e7).astype(np.float32)


#: 风生环流参数(经典值): 海水密度 / 侧向涡粘(决定西边界流宽度 (A_H/beta)^(1/3)) / 有效深度
RHO_OC = 1025.0
A_H = 2.0e4
H_EFF = 250.0


#: 真实表层流缓存(OSCAR 观测月场; False = 还没试过)
_OSCAR = False


def _oscar_currents():
    """读 OSCAR 观测表层流(simulator/env/climate/currents_2019.npz, 1° 南在上)。

    有就用真数据(黑潮/湾流/赤道流都是观测值), 没有就返回 None 走风生环流。
    F2: 内核网格已改 0.5°, 这里的 1° 月场必须重采样到 (ENVNY, ENVNX),
    否则与 env 场广播不上(而且静默用错分辨率比报错更糟)。
    """
    global _OSCAR
    if _OSCAR is False:
        try:
            import os
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'env', 'climate', 'currents_2019.npz')
            if os.path.exists(p):
                d = np.load(p)
                u = np.asarray(d['u'], np.float32)
                v = np.asarray(d['v'], np.float32)
                u = _resample_grid(u)
                v = _resample_grid(v)
                _OSCAR = (u, v)
            else:
                _OSCAR = None
        except Exception:
            _OSCAR = None
    return _OSCAR


def _resample_grid(arr: np.ndarray) -> np.ndarray:
    """把任意 (ny,nx) 的经纬网格场重采样到内核 (ENVNY, ENVNX)(最近邻)。

    用于 1° 的 OSCAR 月场 → 0.5° 内核网格。只做最近邻: 表层流本身是月平均,
    再插值只会制造虚假的羽状结构。
    """
    if arr.shape == (ENVNY, ENVNX):
        return arr.astype(np.float32)
    jj = np.clip((np.arange(ENVNY) + 0.5) * arr.shape[0] / ENVNY, 0,
                 arr.shape[0] - 1).astype(np.int32)
    ii = np.clip((np.arange(ENVNX) + 0.5) * arr.shape[1] / ENVNX, 0,
                 arr.shape[1] - 1).astype(np.int32)
    return arr[np.ix_(jj, ii)].astype(np.float32)


def wind_driven_currents(sim):
    """风生大洋环流: Sverdrup 内部解 + 斯汤姆尔西边界流(输出表层流 kt)。

    做法(经典风生环流理论, 不需要任何外部数据 —— 风场本身就是气候库里的真实风):
      τ = ρ_air·C_D·|U|·U           海面风应力
      curl τ                        风应力旋度(球坐标, 1° 中心差分)
      ψ(x) = ∫_东边界 (curl τ / ρ₀β) dx     Sverdrup 流函数(东边界取 0)
      ψ *= 1 - exp(-x_west/δ),  δ = (A_H/β)^(1/3)   西边界层: 回流被压到西侧
      u = -∂ψ/∂y / H,  v = ∂ψ/∂x / H
    西边界处 ∂ψ/∂x 极大 => 黑潮/湾流那种窄而快的流。

    若存在 OSCAR 观测月场(currents_2019.npz), 直接用观测值, 不再自算。
    """
    oc = _oscar_currents()
    if oc is not None:
        return oc
    env = sim['env']
    LAT, LON = _env_grids()
    dphi = C['ENVD'] * C['DEG']
    coslat = np.maximum(np.cos(LAT * C['DEG']), 1e-3)
    R = 6.371e6
    u = env['u850'] * 0.5144 * 0.8          # kt -> m/s, 850hPa 略强故折减
    v = env['v850'] * 0.5144 * 0.8
    spd = np.hypot(u, v)
    taux = RHO_AIR * CD_WIND * spd * u
    tauy = RHO_AIR * CD_WIND * spd * v
    curl = (np.gradient(tauy, axis=1) / (R * coslat * dphi)
            - np.gradient(taux, axis=0) / (R * dphi))
    beta = np.maximum(2.0 * C['OMEGA'] * np.cos(LAT * C['DEG']) / R, 1e-12)
    step = R * coslat * dphi
    psi = -np.cumsum((curl / (RHO_OC * beta))[:, ::-1], axis=1)[:, ::-1] * step
    land = np.asarray(sim['land']).reshape(ENVNY, ENVNX) >= 0.5
    # 到"西侧最近陆地"的向东距离(**环球**)。原来用 argmax 找每行第一个海洋格,
    # 行首若是陆地(如 25N 从撒哈拉起)整行都会算成 x_west<=0, 太平洋就拿不到西边界流。
    d = np.where(land, 0.0, 1.0e9)
    for i in range(1, 2 * ENVNX):
        d[:, i % ENVNX] = np.where(land[:, i % ENVNX], 0.0,
                                   d[:, (i - 1) % ENVNX] + 1.0)
    xw = d * step
    # 西边界层宽度 (A_H/β)^(1/3): 30°N 约 100km —— 1°(≈111km)网格根本分辨不了,
    # 用有限差分算 ∂ψ/∂x 会把黑潮/湾流算弱四五倍。所以西边界流用**解析梯度**:
    # v_wbc = ψ_sv/δ · exp(-x_west/δ)(指数衰减的导数)
    # δ 下限压到 60km: 1° 网格上西边界流越窄, ψ/δ 给的流速越大(原来 150km 太宽,
    # 黑潮被摊薄到只有 0.26kt; 真实黑潮宽 100km 量级)
    delta = np.maximum(np.power(A_H / beta, 1.0 / 3.0), 6.0e4)
    taper = 1.0 - np.exp(-np.maximum(xw, 0.0) / delta)
    psi_sv = psi
    psi = psi_sv * taper
    # 内部区与西边界流用不同的有效深度: 内区是温跃层尺度的斜压流(≈800m),
    # 西边界流是表层强化的窄流(≈250m)。用同一个深度会把内区算快 2~3 倍。
    u_c = -np.gradient(psi, axis=0) / (R * dphi) / 800.0
    v_c = (np.gradient(psi, axis=1) / (R * coslat * dphi) / 800.0
           + (psi_sv / delta) * np.exp(-np.maximum(xw, 0.0) / delta) / 250.0)
    # 纬向标定: 月气候态风应力偏弱, 副热带环流(黑潮/湾流)算出来只有观测的
    # 1/3; 同时赤道带在 Sverdrup 解里天然偏强。用一个纬向增益把两头拉回真实量级。
    gain = (1.0 + 1.6 * np.exp(-((np.abs(LAT) - 30.0) / 14.0) ** 2)
            - 0.35 * np.exp(-(LAT / 8.0) ** 2))
    u_c = u_c * gain
    v_c = v_c * gain
    return (np.clip(u_c * 1.944, -4.0, 4.0).astype(np.float32),
            np.clip(v_c * 1.944, -4.0, 4.0).astype(np.float32))


#: 表层流缓存(键 = (环境场版本号, 形状))。见 ocean_step。
_OCEAN_UV: dict = {}


def ocean_step(sim, dt_h: float) -> None:
    """海洋状态步: 冷尾流/OHC 消耗的**平流** + 衰减。

    之前尾流只会在原地指数衰减(15 天 / 45 天), 台风走后海温异常钉在原地。
    这里让它们随表层漂流(近似取低层风, 乘 0.03 的 Ekman 漂流系数并偏转 30 度)
    向下游移动 —— 这样才看得出"冷尾流被环流带向下游"。
    """
    env = sim['env']
    # ── 表层流缓存 ──
    # us/vs 只依赖环境场(u850/v850 + 风生环流), 而环境场每个 env 步才重建一次;
    # 但 ocean_step 是**每小时**一步(6h 步长下每步 6 次)。原来每次都重算
    # hemi/ang/ca/sa + wind_driven_currents + 6 个全网格运算 —— 实测 ocean_step
    # 15.7 ms 里这一半是白烧的。按 (环境场版本号, 网格形状) 缓存。
    _ck = ('usv', sim.get('_envVer', -1), env['u850'].shape)
    _c = _OCEAN_UV.get(_ck)
    if _c is None:
        u = env['u850']
        v = env['v850']
        # 表层漂流: 3% 风速 + 偏转 30 度(北半球右偏, 南半球左偏 = Ekman 漂流方向)
        hemi = np.where(_env_grids()[0] >= 0.0, 1.0, -1.0)
        ang = -math.radians(30.0) * hemi
        ca, sa = np.cos(ang), np.sin(ang)
        us_sv, vs_sv = wind_driven_currents(sim)      # 风生环流(含黑潮/湾流)
        us = (us_sv + (u * ca - v * sa) * 0.03).astype(np.float32)
        vs = (vs_sv + (u * sa + v * ca) * 0.03).astype(np.float32)
        if len(_OCEAN_UV) > 4:
            _OCEAN_UV.clear()
        _OCEAN_UV[_ck] = (us, vs)
        # 只保留当前版本的缓存(旧版本号不会再用)
        for _k in [k for k in _OCEAN_UV if k[1] != _ck[1]]:
            _OCEAN_UV.pop(_k, None)
        _c = (us, vs)
    us, vs = _c
    ocn = ocean_column_init(sim)
    ocn['u'] = us                           # 表层流(kt), 供"洋流"图层显示
    ocn['v'] = vs
    wake = sim['wake'].reshape(ENVNY, ENVNX)
    ohcw = sim['ohcWake'].reshape(ENVNY, ENVNX)
    # 100% 陆地的格点: 海洋状态无意义 → 整格冻结(用户 2026-09-19:
    # "如果陆地占比达到 100% 再不模拟这个格子")。掩码来自缓存, 读一次即可。
    _al = all_land_mask()
    _w0 = wake.copy() if _al is not None else None
    _o0 = ohcw.copy() if _al is not None else None
    np.copyto(wake, _advect_env(wake, us, vs, dt_h))
    np.copyto(ohcw, _advect_env(ohcw, us, vs, dt_h * 0.4))
    if _al is not None:
        np.copyto(wake, _w0, where=_al)
        np.copyto(ohcw, _o0, where=_al)
    wake *= math.exp(-dt_h / (15 * 24))       # SST 冷尾流: 15 天
    ohcw *= math.exp(-dt_h / (45 * 24))       # OHC 消耗: 45 天恢复
    # 混合层向气候态松弛(季节循环的来源) + 混合层深度恢复
    ocn = ocean_column_init(sim)
    clim = np.asarray(sst_clim_cached(_env_grids()[0], _env_grids()[1], sim['month']),
                      dtype=np.float32)
    _dT = (clim - ocn['Tml']) * min(1.0, dt_h / (40.0 * 24))
    _dh = (mixed_layer_clim(_env_grids()[0], sim['month']).astype(np.float32)
           - ocn['h']) * min(1.0, dt_h / (20.0 * 24))
    if _al is not None:                       # 全陆格点同样不松弛
        _dT = np.where(_al, 0.0, _dT)
        _dh = np.where(_al, 0.0, _dh)
    ocn['Tml'] += _dT
    ocn['h'] += _dh


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


def wind_at_arr(sim, lon, lat, level: float):
    """wind_at 的数组版: 一次采样全部点, 返回 (u, v) 数组。

    粒子(3000 个)原先逐点调用 wind_at 共约 63ms/帧; 这里把环境场做成
    批量双线性采样, TC 涡旋也按云场同一套向量公式叠加, 逐点与标量版等价。"""
    env = sim['env']
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    if level > 0.5:
        u = env_bilinear_arr(env['uMid'], lon, lat)
        v = env_bilinear_arr(env['vMid'], lon, lat)
    else:
        u = env_bilinear_arr(env['u850'], lon, lat)
        v = env_bilinear_arr(env['v850'], lon, lat)
    for tc in sim['tcs']:
        if tc['dead']:
            continue
        dx = (tc['lon'] - lon) * 111.32 * np.cos(lat * C['DEG'])
        dy = (tc['lat'] - lat) * 110.57
        mask = dx * dx + dy * dy <= 900.0 * 900.0
        if not mask.any():
            continue
        ux, vy = vortex_wind_arr(tc, lon, lat, level)
        u = u + np.where(mask, ux, 0.0)
        v = v + np.where(mask, vy, 0.0)
    return u, v


def cld_sample(sim, lon: float, lat: float) -> float:
    # ⚠ 审查 R11-1(2026-10-15): 经度必须**先归一 + 回绕**再采样。
    #   旧实现直接 ，负经度（ATCF/观测里的 30W、150W）会算出
    #   x0 为负数、被 clamp 到 0 ⇒ 所有西半球的云场采样都落到 0 经线那一列。
    #   同文件的  早就为此写过同样的归一（注释里记着这次事故）。
    lon = (float(lon) - C['LON0']) % 360.0 + C['LON0']
    fx = (lon - C['LON0']) / C['CLDD'] - 0.5
    fy = (lat - C['LAT0']) / C['CLDD'] - 0.5
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    # 经度方向**回绕**（360 与 0 是同一列），纬度方向夹取（极点外没有格点）
    x1 = x0 % CLDNX
    x1b = (x1 + 1) % CLDNX
    y1 = clamp(y0, 0, CLDNY - 2)
    tx = clamp(fx - x0, 0, 1)
    ty = clamp(fy - y0, 0, 1)
    a = sim['cld'][y1 * CLDNX + x1]
    b = sim['cld'][y1 * CLDNX + x1b]
    c = sim['cld'][(y1 + 1) * CLDNX + x1]
    d = sim['cld'][(y1 + 1) * CLDNX + x1b]
    return float((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)


# ── 集合预报(蒙特卡洛扰动, 每 6h 滚动 72h) ──

#: 预报要用到的环境字段(逐时次快照时只留这些, 免得把 13 个时刻的全部场都留在内存里)
_FC_ENV_KEYS = ('u850', 'v850', 'u200', 'v200', 'uMid', 'vMid', 'sst', 'ohc',
                'shear', 'rh', 't200', 'land', 'shRidgeN', 'shRidgeS', 'nino34')


def forecast_env_series(sim, hours: int = 72, dt_e: int = 6, env_int=None):
    """把**未来** `hours` 小时的环境逐时次算出来, 返回 `[env_dict, ...]`(len = hours/dt_e + 1)。

    为什么要它(轮次 105-106 的核心发现): 原来的集合预报把**签发时刻的环境冻结** 72h,
    而 `env_update` 重建的场里含**随时间演化的天气尺度扰动**(瞬变槽脊 `synoptic_steer`、
    MJO、ENSO 遥相关、切变扰动)。实测固定点引导相对签发时刻的偏差是
    **3.2 / 4.6 / 7.9 kt**(24/48/72h); 5kt×24h≈220km, 与
    "零扰动预报 vs 主模型自己的轨迹"实测的 **24h 192km** 同量级 ⇒ 冻结环境是主因。

    实现要点:
      · `env_update` 只依赖**时钟**(与风暴位置无关), 所以整条序列每签发只算一次,
        由全部集合成员共享;
      · 变量循环必须是"**时间在外、成员在内**", 这样同一时刻只需要一份环境在场;
      · 必须**保存/还原** sim 的状态: `env`(原地 clear+update, 不能只还原引用 ——
        `sim['env']` 始终是同一个 dict 对象)、`t` / `month` / `_envVer` / `enso` / `ocn`。
      · 数组用**引用快照**即可: `env_update` 对每个大场都是 `env[k] = <新数组>`,
        旧数组不会被原地改写。
    """
    env = sim['env']
    keep = dict(env)
    keep_t, keep_month = sim.get('t'), sim.get('month')
    keep_ver = sim.get('_envVer')
    keep_enso = dict(sim.get('enso') or {})
    keep_ocn = dict(sim.get('ocn') or {})
    # **env_update 内部会把 lastEnvH 设成当前 t**(见 core.py 里那行赋值),
    # 不还原它会让主模型在预报之后**整整 72 小时不再刷新环境** —— 这是致命的,
    # 因为它污染的是主模型本身, 而不是预报。_envBase 同理(hold>1 的中间步缓存)。
    keep_last = sim.get('lastEnvH')
    keep_base = sim.pop('_envBase', None)
    # **刷新节奏必须与主模型一致**(轮次 106 第二个口径坑):
    # 主模型的环境重建间隔是 `envStepH x hold`(precision 档位决定; '100ms' 档 hold=3
    # => **每 18h 才重建一次**, 中间步只按尾流改 SST/OHC)。第一版预报每 6h 重建一次,
    # 与主模型看到的环境序列不是同一个 —— 这正是"两套口径"的又一例。
    if env_int is None:
        try:
            _ms, _inner, _hold, _q = precision_spec(
                (sim.get('params') or {}).get('precision', DEFAULT_PRECISION))
        except Exception:
            _hold = 1
        env_int = float(sim.get('envStepH', DEFAULT_ENV_STEP)) * max(1, int(_hold))
    out = []
    try:
        out.append({k: env[k] for k in _FC_ENV_KEYS if k in env})
        for k in range(1, int(hours // dt_e) + 1):
            _h = k * dt_e
            if abs(_h % env_int) < 1e-6 or abs(_h % env_int - env_int) < 1e-6:
                sim['t'] = float(keep_t) + _h
                # 用实现函数: 预报内部的未来环境重建**不算**模拟的环境刷新
                _env_update_impl(sim)
                out.append({k2: env[k2] for k2 in _FC_ENV_KEYS if k2 in env})
            else:
                # 中间步: 沿用上一次重建的场(预报里没有尾流演化, 所以复用是精确的)
                out.append(out[-1])
    finally:
        env.clear()
        env.update(keep)
        sim['t'] = keep_t
        if keep_month is not None:
            sim['month'] = keep_month
        if keep_ver is not None:
            sim['_envVer'] = keep_ver
        sim['lastEnvH'] = keep_last
        if keep_base is not None:
            sim['_envBase'] = keep_base
        if keep_enso:
            sim['enso'] = keep_enso
        if keep_ocn:
            ocn = sim.get('ocn')
            if isinstance(ocn, dict):
                ocn.clear()
                ocn.update(keep_ocn)
            else:
                sim['ocn'] = keep_ocn
    return out


def forecast_members(sim, tc0, hours: int = 72, n_mem: int = 12, dt_e: int = 6,
                     rng=None, pert_scale: float = 1.0, envs=None):
    """从 `tc0` 的状态出发做 `hours` 小时的集合/确定性预报, 返回成员路径列表。

    每个成员是 ``{h: [lon, lat, vmax]}``(h 从 0 到 hours, 步长 dt_e)。

    **轮次 105 把它从 `run_ensemble` 里抽出来**, 有两个目的:
      1. 让"预报"与"主模型"共用**同一个引导**(`steering_at` ⇒ `steering_layers`
         + `steering_weights` + `sh_pole_steer`)。原来预报自己写了一套
         `0.75*u850 + 0.25*u200`, 既没有中层也没有副高向极修正 —— 这正是本会话
         反复踩到的"两套实现"陷阱。
      2. 让**预报检验**(`tools/verify_forecast.py`)能拿到一个 `n_mem=1,
         pert_scale=0` 的**确定性单成员**预报 —— 用来判定"预报与主模型是否自洽"
         (零扰动时预报轨迹必须与主模型自己的后续轨迹一致)。
    """
    # ── 初始化成员状态(时间在外、成员在内: 同一时刻只需一份环境在场) ──
    n_mem = max(1, int(n_mem))
    states, paths, perts, gens = [], [], [], []
    for _m in range(n_mem):
        if rng is None:
            perts.append({'su': 0.0, 'sv': 0.0, 'beta': 1.0, 'mpi': 1.0, 'sh': 0.0})
        else:
            perts.append({'su': rng() * 2.5 * pert_scale, 'sv': rng() * 2.5 * pert_scale,
                          'beta': 1 + rng() * 0.3 * pert_scale,
                          'mpi': 1 + rng() * 0.08 * pert_scale,
                          'sh': rng() * 3.9 * pert_scale})
        states.append({'lon': tc0['lon'], 'lat': tc0['lat'], 'vmax': tc0['vmax'],
                       'pmin': tc0['pmin'], 'rmw': tc0['rmw'], 'b': tc0['b'],
                       'mu': tc0['mu'], 'mv': tc0['mv'],
                       'noiseU': 0.0, 'noiseV': 0.0})
        paths.append({0: [tc0['lon'], tc0['lat'], tc0['vmax']]})
        gens.append(rng if rng is not None else (lambda: 0.0))
    # **环境序列由调用方算一次、全部风暴共享**(它只依赖时钟, 与风暴位置无关)。
    # 第一版在每个风暴内部各算一遍: 25 天 × 每天一次 × ~15 个风暴 × 13 次 env_update
    # = 近 5000 次 env_update, 直接把检验工具跑超时(>600s)。
    if envs is None:
        envs = forecast_env_series(sim, hours, dt_e)
    # 邻居列表(藤原用): 取**签发时刻**所有其它存活风暴的状态并保持不变
    others = [(o['lon'], o['lat'], o['vmax'], o['rmw'])
              for o in sim['tcs'] if o is not tc0 and not o['dead']]
    _sim_env = sim['env']
    try:
        for k in range(1, int(hours // dt_e) + 1):
            if envs is not None:
                # 把 sim 的环境**临时换成该时刻的快照**: steering_at / sample_env
                # 都是读 sim['env'], 所以不需要给它们传参就能同口径。
                sim['env'] = envs[k]
            for m in range(n_mem):
                tc, pert, g = states[m], perts[m], gens[m]
                lat = tc['lat']
                # ── **引导必须与主模型完全同口径**(轮次 105 修) ──
                # 原来这里是另写的一套: 0.75*u850 + 0.25*u200, 没有中层、
                # 没有副高向极修正(sh_pole_steer)、也没有 params['steerU/V']。
                # 后果是**预报表和主模型走两套物理** —— 本会话反复踩到的
                # "两套实现"陷阱(ERC 测量、引导标定、切变矢量都栽在同一个坑里)。
                steer_u, steer_v = steering_at(sim, tc['lon'], lat, tc['vmax'])
                # **藤原 + 陆地阻尼也要同口径**(轮次 106): 主模型 `motion_step`
                # 会叠加邻居的二元互旋漂移(实测 32.9% 的步 >1kt、p90 3.8kt)并做
                # 登陆阻尼; 预报原来两者都没有 —— 这是自洽性残差的已知来源。
                # 邻居位置按**签发时刻持续**(预报里不知道邻居将来怎么走, 这是
                # 标准做法), 所以 `others` 在整条预报里固定不变。
                _fu, _fv = fujiwhara_drift(tc['lon'], lat, tc['vmax'], others)
                _lf = land_at(sim, tc['lon'], lat)
                beta = beta_drift(lat)
                u = (steer_u + _fu + pert['su'] + beta[0] * pert['beta']
                     + tc['noiseU'])
                v = (steer_v + _fv + pert['sv'] + beta[1] * pert['beta']
                     + tc['noiseV'])
                if _lf > 0.1:
                    u *= 1 - 0.25 * min(1.0, _lf)
                    v *= 1 - 0.25 * min(1.0, _lf)
                tc['noiseU'] += (g() * 2.3 - tc['noiseU']) * 0.15
                tc['noiseV'] += (g() * 2.3 - tc['noiseV']) * 0.15
                # **一阶运动滞后也要同口径**(轮次 105): 主模型 motion_step 把目标
                # 移速经 motion_lag_alpha 滞后后才积分; 预报原来直接用瞬时目标值,
                # 于是引导场突变时预报会比主模型"拐得更快"。
                _a = motion_lag_alpha(dt_e)
                tc['mu'] += (u - tc['mu']) * _a
                tc['mv'] += (v - tc['mv']) * _a
                tc['lon'] = (tc['lon'] + tc['mu'] * dt_e
                             / (60.0 * math.cos(lat * C['DEG']))) % 360.0
                tc['lat'] = float(clamp(tc['lat'] + tc['mv'] * dt_e / 60.0,
                                        C['LAT0'] + 0.1, C['LAT1'] - 0.1))
                e = sample_env(sim, tc['lon'], tc['lat'])
                vpi = mpi(e['sst'], e['ohc'], sim['params']['outflow'], e['lat'],
                          e.get('t200')) * pert['mpi']
                shear = e['shear'] + pert['sh']
                # **必须与 intensity_step 同口径**(原来这里还留着旧的硬版函数)
                fs, _gate = shear_response(shear)
                fd = (math.exp(-((70 - e['rh']) / 42.0) ** 1.4)
                      if e['rh'] < 70 else 1.0)
                # tau 与主模型**共用常量**(原来这里是硬编码的 38/20, 快 1.8~4 倍)
                _tau = TAU_INTENSIFY_H if tc['vmax'] < vpi else TAU_WEAKEN_H
                d_v = (vpi - tc['vmax']) / _tau * fs * fd
                if e['land'] > 0.02:
                    d_v -= tc['vmax'] * min(1.0, e['land']) * 0.075
                if e['sst'] < 26.5:
                    d_v -= tc['vmax'] * (26.5 - e['sst']) * 0.04
                tc['vmax'] = clamp(tc['vmax'] + d_v * dt_e, 10, 250)
                tc['pmin'] = pmin_from_v(tc['vmax'])
                paths[m][k * dt_e] = [tc['lon'], tc['lat'], tc['vmax']]
    finally:
        sim['env'] = _sim_env
    return paths


def run_ensemble(sim) -> dict:
    if sim['ensemble'] and sim['t'] - sim['ensemble']['hour'] < 5.9:
        # 集合还新鲜: 但**强度统计可能还没算**(风暴是集合算完之后才生成的,
        # 或者上一次统计抛异常) —— 缺就补算, 否则信息框的"预报"行永远空白
        _need = next((t for t in sim['tcs']
                      if not t['dead'] and 'fcPeak' not in t), None)
        if _need is None:
            return sim['ensemble']
    n_mem, hours, dt_e = 12, 72, 6
    prim = next((t for t in sim['tcs'] if not t['dead']), None)
    if prim is None:
        sim['ensemble'] = {'hour': sim['t'], 'members': []}
        return sim['ensemble']
    g = make_gauss(make_rng(sim['seed'] * 31 + int(sim['t'] * 7)))
    _envs = forecast_env_series(sim, hours, dt_e)
    members = forecast_members(sim, prim, hours, n_mem, dt_e, rng=g, envs=_envs)
    sim['ensemble'] = {'hour': sim['t'], 'members': members, 'nMem': n_mem,
                       'hours': hours}
    # ── 集合强度统计(用户 2026-09-19: "巅峰及登陆强度要显示") ──
    # 用**成员自身的统计量**, 不用"平均轨迹上的最大值" —— 后者对减弱中的风暴会
    # 给出"未来还会更强"的假象(实测出现过 23kt@+72h 这种没意义的值)。
    #   · 预计巅峰 = 各成员自身最大强度的**均值**(P10/P90 给不确定度)
    #   · 预计巅峰时刻 = 各成员峰值时刻的均值(相对小时)
    #   · 预计登陆 = 各成员首次触陆的强度; 不足 40% 成员登陆就不给结论
    try:
        _nh = hours // 6
        for _tc in sim['tcs']:
            if _tc['dead']:
                continue
            if _tc is prim:
                # 陆地格点: 内核的**网格**在 sim['land'](env['land'] 是采样后的标量)
                _landg = sim.get('land')
                if _landg is None:
                    _landg = (sim.get('env') or {}).get('land')
                _peaks, _peak_h, _lands, _land_h = [], [], [], []
                for m in members:
                    _vs = [m[i * 6][2] for i in range(_nh + 1) if i * 6 in m]
                    if not _vs:
                        continue
                    _km = max(range(len(_vs)), key=lambda k: _vs[k])
                    _peaks.append(float(_vs[_km]))
                    _peak_h.append(float(_km * 6))
                    for i in range(1, _nh + 1):
                        _p = m.get(i * 6)
                        if _p is None:
                            continue
                        if _landg is None or env_bilinear(_landg, _p[0], _p[1]) > 0.05:
                            _lands.append(float(_p[2]))
                            _land_h.append(float(i * 6))
                            break
                if _peaks:
                    _ps = sorted(_peaks)
                    _lo = _ps[max(0, int(len(_ps) * 0.1))]
                    _hi = _ps[min(len(_ps) - 1, int(len(_ps) * 0.9))]
                    _tc['fcPeak'] = {'kt': sum(_peaks) / len(_peaks),
                                     'h': sum(_peak_h) / len(_peak_h),
                                     'kt_lo': _lo, 'kt_hi': _hi,
                                     'n': len(_peaks)}
                if _lands and len(_lands) >= max(3, int(0.4 * len(members))):
                    _tc['fcLand'] = {'kt': sum(_lands) / len(_lands),
                                     'h': sum(_land_h) / len(_land_h),
                                     'n': len(_lands)}
                else:
                    _tc.pop('fcLand', None)
            else:
                # 非主风暴: 只给"会不会更强"的粗估(用自己最近的趋势)
                _h = _tc.get('hist') or []
                if len(_h) >= 3:
                    _dv = (float(_h[-1]['vmax']) - float(_h[-3]['vmax'])) / 12.0
                    _tc['fcPeak'] = {'kt': clamp(float(_tc['vmax'])
                                                 + max(0.0, _dv) * 24.0, 10, 250),
                                     'h': 24.0, 'kt_lo': None, 'kt_hi': None,
                                     'n': 1}
                _tc.pop('fcLand', None)
            _tc['fcHour'] = float(sim['t'])
    except Exception:
        logger.debug('集合强度统计失败', exc_info=True)
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

def sim_step(sim, dt: float, max_inner: int = 0) -> bool:
    """推进一步。返回 True = 这个 dt 已经走完, False = 还有内步没跑(分帧续跑)。

    **模拟精度档位**在这里生效(用户 2026-09-18 定):
      高档位 —— 把 dt 内部再细分成 inner 步, 每步都重建环境场。
                这才是真正的"更高精度": 环境场在 6h 内是会变的, 细分之后
                台风看到的是随时间演化的场, 而不是"一步之内冻结"。
                代价线性增长, 所以 2s/5s 档只能靠放慢模拟推进来达成。
      低档位 —— inner=1 时**减少**环境场重建频率(每 hold 步一次), 中间的步
                只从已存的基态 + 尾流重算 SST/OHC(几毫秒)。大尺度场(风/切变/
                模态)变化慢, 这个近似是合理的, 也是 50ms 档唯一够得到的办法。

    max_inner(2026-09-19 加): >0 时本次最多跑 max_inner 个内步, 余下的记在
    sim['_pendStep'] 里等下次调用。这是"自动降速"的地基 —— 2s/5s 档一个 6h
    步要 2-5 s 墙钟, 一口气跑完会把主线程冻住(UI 完全不响应); 拆开后每帧只花
    100-200 ms。**模拟钟推进的总量与顺序完全不变**(内步是模型的原子步, 一个都
    不跳), 变的只是墙钟节奏 —— 所以"模拟时间对齐"不受影响。
    """
    _tgt, _inner, _hold, _qual = precision_spec(
        (sim.get('params') or {}).get('precision', DEFAULT_PRECISION))
    sim['_precQual'] = _qual
    pend = sim.get('_pendStep')
    if (not isinstance(pend, dict) or abs(float(pend.get('dt', -1.0)) - dt) > 1e-9
            or int(pend.get('inner', 0)) != int(_inner)):
        # 换档/换步长 → 丢掉半截的记账, 从新的细分重开(已推进的 t 不会回退:
        # 模拟钟只会往前, 少走的那点余量由下一段补上, 不产生"跳时")
        pend = {'dt': float(dt), 'inner': int(_inner), 'hold': int(_hold),
                'i': 0, 'el': 0.0}
        sim['_pendStep'] = pend
    _todo = int(pend['inner']) - int(pend['i'])
    _n = _todo if max_inner is None or max_inner <= 0 else min(_todo, int(max_inner))
    _t0 = _time.perf_counter()
    for _ in range(max(0, _n)):
        if int(pend['inner']) > 1:
            # **每内步都重建环境场** —— 这才是"更高精度"的实质: 环境场在 6h 内
            # 本来就会变, 细分后台风看到的是随时间演化的场。
            # 注意 env_update 内部会把 lastEnvH 设成当前 t, 所以不强制的话细分
            # 之后仍然只在最后一步重建一次(实测 inner=14 与 inner=6 成本几乎一样)。
            _sim_step_core(sim, dt / int(pend['inner']), int(pend['hold']),
                           force_env=True)
        else:
            _sim_step_core(sim, dt, int(pend['hold']), force_env=False)
        pend['i'] = int(pend['i']) + 1
    # 只累计**干活**的时间: 分帧跑时前后两帧之间隔着渲染/等待, 若用
    # t0→now 会把空闲时间算进"每 6h 成本", 档位实测值会被彻底污染
    pend['el'] = float(pend.get('el', 0.0)) + (_time.perf_counter() - _t0) * 1000.0
    if int(pend['i']) >= int(pend['inner']):
        # 实测成本(每 6h 模拟时间的墙钟 ms), 指数平滑供 UI 显示
        _per6 = float(pend['el']) * (6.0 / max(1e-9, dt))
        sim['stepCostMs'] = (_per6 if not sim.get('stepCostMs')
                             else sim['stepCostMs'] + (_per6 - sim['stepCostMs']) * 0.15)
        sim.pop('_pendStep', None)
        return True
    return False


def _sim_step_core(sim, dt: float, hold: int = 1,
                   force_env: bool = False) -> None:
    sim['t'] += dt
    # F1: 日历由绝对起始时刻 + t 推进(不再用 30.4 天的近似月长,
    # 因此 2 月/闰年/跨年的季节相位都正确)
    sim['month'] = clock(sim).month
    # 全球网格下环境场重建成本高: 按可配置步长刷新(1/3/6h, 默认 6h; 上限 6h)
    # eps: 10 分钟步长累加出的 t 会有浮点误差(6h 处实为 5.99999999), 不留 eps
    # 会让刷新点整体后移一步(6.167h), 长跑累积成可见的相位漂移
    # **hold 的正确语义: 把环境场重建间隔拉长成 envStepH × hold**。
    # 原来把计数挂在"每 envStepH 一次"的条件上, 而 env_update 内部又会把
    # lastEnvH 设成当前 t —— 于是 hold 实际只相当于每 hold×envStepH 才生效一次,
    # 完全不起作用(实测 hold=8 与 hold=1 成本一样)。
    _env_int = sim.get('envStepH', DEFAULT_ENV_STEP) * max(1, int(hold))
    if force_env or sim['t'] - sim['lastEnvH'] >= _env_int - 1e-6:
        env_update(sim)
        if hold > 1:
            # 存"不含尾流"的基态, 供中间的 hold 步重算
            _w = sim['wake'].reshape(ENVNY, ENVNX)
            _ow = sim['ohcWake'].reshape(ENVNY, ENVNX)
            _e = sim['env']
            sim['_envBase'] = (np.asarray(_e['sst']) + _w,
                               np.asarray(_e['ohc']) + _ow)
    elif hold > 1 and '_envBase' in sim:
        # 中间的步: 只更新被尾流影响的 SST/OHC(几毫秒), 大尺度场沿用上一次
        _w = sim['wake'].reshape(ENVNY, ENVNX)
        _ow = sim['ohcWake'].reshape(ENVNY, ENVNX)
        _bs, _bo = sim['_envBase']
        _e = sim['env']
        _e['sst'] = (_bs - _w).astype(np.float32)
        _e['ohc'] = np.maximum(18.0, _bo - _ow).astype(np.float32)
    # 海洋状态(尾流平流+衰减)。**必须按步长推进**: 原来固定 1.0h, 但步长
    # 统一后一次 sim_step 可能跨 6h —— 那样海洋只走了 1/6 的时间, 尾流衰减
    # 会被严重拖慢。改成按步长内部再分 1h 子步, 保证物理时间一致。
    _stp = step_hours(sim)
    # 子步数 = 档位给的固定值 × (步长/6h)。基准档位是按 6h 标的, 所以步长
    # 变 1h/3h 时按比例缩, 保证"每单位模拟时间的内部解析度"一致。
    _tgt_ms, _inner, _hold, _qual = precision_spec(
        (sim.get('params') or {}).get('precision', DEFAULT_PRECISION))
    sim['_precQual'] = _qual
    _n_oc = int(max(1, math.ceil(_stp / 1.0)))
    if sim['t'] - sim.get('lastOceanH', -1e9) >= _stp - 1e-6:
        sim['lastOceanH'] = sim['t']
        for _oi in range(_n_oc):
            ocean_step(sim, _stp / _n_oc)
    # 集合/生成: 与步长一致(原来是硬编码 6h, 步长变成 1/3h 时就会漏触发)
    # **集合预报可按参数关掉**(轮次 106): params['forecast'] = False。
    # 原因: forecast_env_series 每次签发要跑 13 次 env_update(约 4 秒),
    # 而年尺度验收(verify_year / multi_year_check)完全不需要集合 ——
    # 它是**只读诊断**, 不影响任何验收指标, 但会让全年跑慢好几倍。
    # GUI 的实时预报仍然默认开启。
    if ((sim.get('params') or {}).get('forecast', True)
            and sim['t'] - sim['lastEnsH'] >= _stp - 1e-6):
        run_ensemble(sim)
        sim['lastEnsH'] = sim['t']
    if sim['t'] - sim['lastGenH'] >= _stp - 1e-6:
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
                # 强度-寿命标定要用的状态量(2026-09-21 加): RMW 是 ERC 的核心变量,
                # weak 是结构退化, erc 是置换状态 —— 没有它们就无法离线分析置换/廓线。
                'rmw': tc['rmw'], 'weak': tc.get('weak', 0.0),
                'erc': 1.0 if tc.get('erc') else 0.0,
                'org': float(tc.get('core_org') or 0.0),
                'vpi': mpi(e['sst'], e['ohc'], sim['params']['outflow'], e['lat'],
                           e.get('t200'))})
            maybe_name(sim, tc)
            tc['track'].append({'t': sim['t'], 'lon': tc['lon'], 'lat': tc['lat'],
                                'vmax': tc['vmax'], 'pmin': tc['pmin'],
                                'name': tc['name'], 'named': bool(tc.get('named')),
                                'phase': tc.get('phase', 'DB')})
            if len(tc['hist']) > 720:
                tc['hist'] = tc['hist'][-720:]
            if len(tc['track']) > 2400:
                tc['track'] = tc['track'][-2400:]
    eddy_step(sim, dt)
    # **死亡台风不删除**(用户 2026-09-19: "台风没有过去的轨迹"/统计恒为 0)。
    # 原来 dead 就立刻从 sim['tcs'] 里剔掉, 于是路径、ACE 归属、统计一起消失。
    # 现在保留在列表里(dead=True), 只清理**很久以前**的(>120 天)防止内存无限涨;
    # 渲染/统计都按 dead 标记区分。
    if any(t['dead'] for t in sim['tcs']):
        _keep = []
        for _t in sim['tcs']:
            if (not _t['dead']) or (float(sim['t']) - float(_t.get('deadHour', sim['t']))
                                    <= 2880.0):
                _keep.append(_t)
        if len(_keep) != len(sim['tcs']):
            sim['tcs'] = _keep


def latest_real_start(step_h: float = 6.0):
    """**最后一个有实测环境/模态数据的时刻**(自动起始时间用)。

    用户 2026-09-19: "默认是 1850 年. 应该是当前时间(或者是最后一个能找到现实
    模态的时间)"。这里以逐日环流型表(regimes.npz 的 date 列)的末日为准 ——
    它是环境场模型里最新的一份实测; 读不到就退回系统当前时间。
    返回的时刻按 step_h 向下取整(与积分步长对齐)。
    """
    from datetime import datetime as _dt, timedelta as _td
    latest = None
    try:
        from .envmodel import store as _store
        d = _store.load('regimes')
        if d is not None and 'date' in d:
            import numpy as _np
            arr = _np.asarray(d['date']).ravel()
            if arr.size:
                v = int(arr[-1])
                latest = _dt(v // 10000, (v // 100) % 100, v % 100, 0)
    except Exception:
        latest = None
    # **还要看模态的可用窗口**(用户 2026-09-19: "模拟默认模拟最近的有完整真实
    # 数据及模态时间")。实况(regimes 逐日)能到 2026-03, 但 modes_index 的模态
    # 只到 2026-02 —— 取两者的**交集末端**, 否则默认时间会落在"有实况、没模态"
    # 的区间里, 模态异常整段为 0(等于偷偷退回纯气候态)。
    try:
        import numpy as _np
        from .envmodel import store as _store2
        mi = _store2.load('modes_index')
        if mi is not None and 'year' in mi:
            yr = _np.asarray(mi['year']).ravel()
            mo = _np.asarray(mi['month']).ravel()
            ok = _np.asarray(mi['ok']).ravel() if 'ok' in mi else _np.ones_like(yr)
            good = _np.nonzero(ok > 0)[0]
            if good.size:
                k = int(good[-1])
                _m_end = _dt(int(yr[k]), int(mo[k]), 1, 0)
                # 模态是月值 → 该月内都算可用, 取月末的最后一天 00Z 作为上限
                _m_end = _m_end + _td(days=27)
                if latest is None or _m_end < latest:
                    latest = _m_end
    except Exception:
        logger.debug('模态可用窗口读取失败(默认起始仍按实况末日)', exc_info=True)
    now = _dt.now()
    if latest is None or latest > now:
        latest = now
    # 与积分步长对齐(6h → 00/06/12/18Z)
    try:
        st = max(1.0, float(step_h))
        hours = int(latest.hour // st) * st
        return latest.replace(hour=int(hours), minute=0, second=0, microsecond=0)
    except Exception:
        return latest.replace(minute=0, second=0, microsecond=0)
