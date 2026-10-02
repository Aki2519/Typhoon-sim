# -*- coding: utf-8 -*-
"""SimCore 山脉高度场 —— 真 DEM + 采样原语(轮次 158)。

━━ 这个文件是什么 ━━
同目录的 ``terrain_0.5.npz`` 里存着 0.5° 网格上的 **海拔高度场**。本模块只做三件事:

  1. **加载**(带模块级缓存, 只读一次盘);
  2. **按经纬度双线性采样**海拔(标量版 + 数组版);
  3. **沿途最高海拔**(供摩擦 / 登陆衰减用的路径积分原语)。

⚠⚠ **数据来源(2026-09-30 更正, verifier 文档审计)**: 现行 ``terrain_0.5.npz`` 是
**真实 DEM**, 由 ``tools/build_terrain_dem.py`` 从 ``map/heightmap.png`` 生成
(30 px/度、``elev = 像素 - 12000``、海洋格按 `landfrac` 掩码归 0)。
本节原先写的是"由 ``tools/build_terrain.py`` 按下方 ``FEATURES`` 数值表**程序化合成**
(不下载任何东西)" —— **那已经是历史**:
``build_terrain.py`` 的合成版被备份为 ``terrain_0.5_synth.npz``, 不再被本模块消费。
⇒ **不要再用"程序化合成"来判断这里的山有多真**; 台湾中央山脉 3025 m、
吕宋 2320 m、本州 2503 m 都是 ``heightmap.png`` 里量出来的。
(「陆地掩码」一节与 ``FEATURES`` 数值表仍留着, 供合成版回溯参考。)

━━ 为什么要有它 ━━
现在内核手里唯一的陆地信息是 ``landfrac``(陆地占比 0..1, 见 core.py:483
``cell_land_cache`` 与 simulator/env/model/land_cell.npz)。而登陆衰减
(core.py:2599-2602, ``dV -= vmax·land·0.012``)与路径阻尼(core.py:3241-3244,
``×(1-0.25·lf)``)**只看陆地比例、不看山有多高** —— 于是"穿过台湾中央山脉
(脊顶 3.9 km)"与"穿过一个平坦沙洲"对台风的损耗完全一样。本模块提供
"海拔"这一维度所需的全部取样原语; 具体系数怎么进 core.py 由组织者按补丁规格落地。

━━ 网格对齐(实读 core.py, 不是猜) ━━
  core.py:41   LON0/LON1 = 0.0/360.0,  LAT0/LAT1 = -90.0/90.0
  core.py:44   ENVD = 0.5
  core.py:50   ENVNX = 720
  core.py:51   ENVNY = 360
  core.py:587  env_lon(i) = 0 + (i+0.5)·0.5   → 0.25, 0.75, …, 359.75
  core.py:591  env_lat(j) = -90 + (j+0.5)·0.5 → -89.75, …, 89.75(**row 0 = 最南**)
本模块**故意不 import core**(要求: 纯 numpy, 不 import simulator.simcore.core) ——
常量在此重声明, 由 ``tests/test_simcore_terrain.py`` 直接与 core 对拍。``bilinear``
逐条照抄 ``core.env_bilinear``(core.py:595-617)的索引、clamp 与经度回绕约定,
所以与内核**同口径**(测试里用随机场逐点对拍验证)。

━━ 合成模型 ━━
每个"定向高斯脊"(kind='R')在**以脊心为原点的局部切平面**上算, 长度单位 km:

    dx = wrap180(lon - lon_c) · 111.32·cos(lat_c)     # 纬圈方向(km)
    dy = (lat - lat_c) · 110.57                       # 经圈方向(km)
    u  = dx·sin(az) + dy·cos(az)                      # 沿走向, az = 走向方位角(° , 正北=0, 顺时针)
    v  = dx·cos(az) - dy·sin(az)                      # 垂直走向
    e  = H · exp( -0.5·[ (u/σ_u)² + (v/σ_v)² ] )       # 峰高 H 落在脊轴中心

孤立"峰"(kind='P')是 az 无意义的各向同性特例(σ_u = σ_v)。极冠(kind='C')用余纬:

    e  = H · exp( -0.5·[ (d_pole_km/σ_u)² ] ),   d_pole_km = (90-|lat|)·110.57

多要素合成用**逐点取最大**(``np.maximum``), 不是相加 —— 山不会叠加长高。
σ 是**高斯尺度**, 不是半高宽: HWHM = 1.1774·σ, 3.5σ 处已衰减到 0.22%·H。
所有数值都是**近似**(量级对、位置对), 不追求与真实地形逐格吻合。

**注意"表里的 H"与"采样到的值"不是一回事**: 格内最大由 0.05° 细采样取到, 再经
双线性平滑, 因此**略低于** H。实测(轮次 158): 台湾中央山脉 H=3700 → 采样 3720 m
(脊+玉山峰叠加后)、玉山 H=3952 → 采样 3719 m、富士山 H=3776 → 采样 3257 m、
珠峰 H=8848 → 全局格内最大 8815 m。差值是离散采样与双线性平滑的必然结果, 不是 bug;
需要"真实峰高"时应直接查 ``elev_max`` 的格值, 不要指望双线性采样复现 H。

━━ 具体数值表(51 个要素, 与下方 ``FEATURES`` 逐行一一对应, 无省略) ━━
  列: 类=要素类型(R 定向脊 / P 峰 / C 极冠); σ_轴/σ_横 = 轴向/横向高斯尺度(km,
  C 类的 σ_轴 是到极点的公里尺度); 峰高 = 脊轴中心的 H(m)。
  每个要素的**有效范围取 3.5σ**(窗口外按 0 处理, 误差 ≤ 0.22%·H)。
  ─────────────────────────────────────────────────────────────────────────
  #   名称               类  中心纬度 中心经度  方位角  σ_轴   σ_横   峰高(m)
  ─────────────────────────────────────────────────────────────────────────
   1  台湾中央山脉        R    23.45   121.00    25.0   55.0   22.0    3700
   2  台湾玉山            P    23.47   120.96     0.0   12.0   12.0    3952
   3  海南五指山          R    19.05   109.70    40.0   45.0   22.0    1500
   4  吕宋科迪勒拉        R    17.00   120.90     5.0   60.0   25.0    2200
   5  吕宋马德雷山        R    16.20   122.05    10.0  170.0   20.0    1400
   6  米沙鄢脊            R    10.50   122.50    20.0   60.0   25.0    1200
   7  棉兰老脊            R     7.50   125.10     0.0   80.0   35.0    2100
   8  九州脊              R    32.80   131.00     7.0   90.0   40.0    1600
   9  本州西部脊          R    34.80   133.20    76.0  180.0   40.0    1200
  10  本州中部脊          R    36.00   137.80    20.0  130.0   45.0    2400
  11  富士山              P    35.36   138.73     0.0   15.0   15.0    3776
  12  本州东北脊          R    39.00   140.50    10.0  180.0   40.0    1500
  13  北海道脊            R    43.30   142.80    30.0  110.0   40.0    1700
  14  朝鲜太白脊          R    36.50   128.30   340.0  130.0   30.0    1300
  15  越南长山            R    16.50   107.30   340.0  200.0   35.0    1400
  16  堪察加脊            R    55.00   159.00    20.0  300.0   45.0    2000
  17  克柳切夫火山        P    56.06   160.64     0.0   18.0   18.0    4750
  18  婆罗洲中央脊        R     0.50   114.50    70.0  150.0   60.0    1800
  19  京那巴鲁            P     6.08   116.55     0.0   16.0   16.0    4095
  20  苏门答腊脊          R    -1.50   101.50   140.0  400.0   45.0    2000
  21  葛林芝火山          P    -1.70   101.26     0.0   15.0   15.0    3805
  22  爪哇脊              R    -7.60   110.00   100.0  260.0   30.0    2000
  23  新几内亚中央脊      R    -5.50   143.00   110.0  400.0   50.0    3000
  24  查亚峰              P    -4.08   137.18     0.0   18.0   18.0    4884
  25  落基山              R    44.00   246.00   340.0  450.0   80.0    2500
  26  内华达山            R    37.50   240.50   330.0  200.0   50.0    2500
  27  西马德雷山          R    26.00   253.00   330.0  300.0   50.0    2000
  28  东马德雷山          R    24.00   260.00   340.0  250.0   45.0    1800
  29  下加利福尼亚脊      R    27.00   247.00   330.0  350.0   35.0    1200
  30  南马德雷山          R    16.50   264.00   285.0  200.0   40.0    1900
  31  中美洲脊            R    14.50   269.00   290.0  120.0   35.0    2200
  32  伊斯帕尼奥拉脊      R    19.00   289.00   300.0   90.0   30.0    1500
  33  杜阿尔特峰          P    18.95   289.30     0.0   14.0   14.0    3098
  34  安第斯山            R   -20.00   291.00   165.0 1100.0   70.0    4000
  35  阿空加瓜            P   -32.65   290.00     0.0   20.0   20.0    6961
  36  西高止山            R    14.00    75.30   160.0  350.0   30.0    1600
  37  阿纳穆迪            P    10.17    77.06     0.0   15.0   15.0    2695
  38  喜马拉雅山          R    29.00    84.00   110.0  700.0   90.0    5500
  39  珠穆朗玛峰          P    27.99    86.93     0.0   20.0   20.0    8848
  40  天山                R    42.00    78.00   100.0  400.0   70.0    3500
  41  东非大裂谷脊        R    -1.00    36.50   350.0  350.0   60.0    2000
  42  乞力马扎罗          P    -3.07    37.35     0.0   18.0   18.0    5895
  43  马达加斯加脊        R   -19.50    46.50    15.0  280.0   50.0    1700
  44  大分水岭            R   -30.00   151.00    15.0  400.0   60.0    1100
  45  科修斯科山          P   -36.46   148.27     0.0   15.0   15.0    2228
  46  阿尔卑斯山          R    46.50    10.00    75.0  250.0   55.0    2500
  47  勃朗峰              P    45.83     6.86     0.0   15.0   15.0    4808
  48  高加索山            R    43.00    43.00   115.0  250.0   50.0    3000
  49  厄尔布鲁士峰        P    43.35    42.44     0.0   16.0   16.0    5642
  50  格陵兰冰盖          R    72.00   318.00     0.0  600.0  400.0    2400
  51  南极冰盖            C   -90.00     0.00     0.0 1500.0 1500.0    3200
  ─────────────────────────────────────────────────────────────────────────

━━ 面 / 峰两个场(都存, 不能只存一个) ━━
合成在 0.5° 格内做 sub×sub 细采样(默认 sub=10 → 0.05° ≈ 5.6 km), 每格存两个统计量:
  ``elev_mean``  格内均值 —— "面"效应(摩擦 / 引导权重折减 / 热力)
  ``elev_max``   格内最大 —— "峰"效应(沿途最高海拔 / 登陆衰减 / 背风破坏)
这是必须的: 0.5° 格宽 ≈ 55 km, 而台湾中央山脉横向尺度只有 ~50 km。只存均值会把
脊顶从 3.9 km 削成 ~1.5 km(真 DEM 实测, 玉山所在格 mean≈1.3 km vs max≈3.5 km,
见轮次 158 侦察报告 R5 §0.3), 摩擦与
登陆衰减立刻失效 —— 与轮次 158 侦察报告 §0.3 对真 DEM 的结论完全一致。

━━ 陆地掩码(重要) ━━
合成场在**纯海洋格上被强制置 0**(掩码取自 simulator/env/model/land_cell.npz 的
``landfrac``, 与内核同网格同对齐): ``elev_mean = raw_mean · landfrac``,
``elev_max = raw_max · [landfrac > 0]``。两个理由:
  ① 平滑高斯脊会在海洋上"漏"出假山。南极冰冠最严重 —— 不加掩码时南大洋 60S
     一圈会挂上千米假高程, 直接把引导摩擦与登陆衰减打掉;
  ② 由此得到一条**可验证的中性性质**: 纯海洋格海拔恒为 0 ⇒ 摩擦因子恒为 1、
     衰减因子恒为 1 ⇒ 海上行为逐位不变(补丁规格以这条性质作为"默认不改行为"的判据)。

━━ 用法(消费方) ━━
    from simulator.simcore import terrain as _TER
    e   = _TER.elev_at(lon, lat)                       # 平滑面海拔(m), 海洋=0
    pk  = _TER.peak_along(lon0, lat0, lon1, lat1)      # 这一段路上的最高海拔(m)
    f   = _TER.friction_scale(e)                       # ≤1, 摩擦折减(海拔越高越小)
    g   = _TER.decay_scale(pk)                         # ≥1, 登陆衰减倍率
``available()`` 为假(没生成过 npz)时, 调用方必须**退回原行为**, 不要抛异常。
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    'ENVNY', 'ENVNX', 'ENVD', 'LAT0', 'LON0', 'LAT1', 'LON1',
    'FEATURES', 'DEFAULT_SUB', 'K_SIGMA',
    'lat_of_row', 'lon_of_col', 'terrain_file', 'available', 'load',
    'elev_mean', 'elev_max', 'reset_cache',
    'bilinear', 'bilinear_arr', 'elev_at', 'elev_at_arr',
    'path_samples', 'elev_profile', 'peak_along', 'mean_along',
    'peak_along_series', 'peak_along_track',
    'synthesize', 'apply_land_mask', 'describe_features', 'stats',
    'friction_scale', 'friction_scale_arr', 'decay_scale',
    'ELEV_FRICTION_E0', 'ELEV_PEAK_P0',
]

# ═══════════════════════════ 1. 网格常量 ═══════════════════════════
# 与 core.py 逐条对齐(出处见模块 docstring)。本模块不 import core, 由测试对拍。
LON0, LON1 = 0.0, 360.0
LAT0, LAT1 = -90.0, 90.0
ENVD = 0.5
ENVNX = int(round((LON1 - LON0) / ENVD))          # 720  (core.py:50)
ENVNY = int(round((LAT1 - LAT0) / ENVD))          # 360  (core.py:51)

#: 单位换算(core.py:1868-1871 用的是同一组常数: 111.32 / 110.57)
KM_PER_DEG_LON = 111.32
KM_PER_DEG_LAT = 110.57


def lon_of_col(i: int) -> float:
    """列 i 的格心经度 —— 等价 core.py:587 ``env_lon``。"""
    return LON0 + (i + 0.5) * ENVD


def lat_of_row(j: int) -> float:
    """行 j 的格心纬度 —— 等价 core.py:591 ``env_lat``(row 0 = 最南)。"""
    return LAT0 + (j + 0.5) * ENVD


# ═══════════════════════════ 2. 合成参数表 ═══════════════════════════
#: 每格细采样数(合成用)。0.5°/10 = 0.05° ≈ 5.6 km(真 DEM 是 30 px/度 = 0.033°)。
#: 实测成本: sub=10 全场合成为 0.51 s(sub=5 → 0.17 s, sub=2 → 0.06 s)。
DEFAULT_SUB = 10
#: 要素有效范围(以高斯尺度计)。3.5σ → exp(-6.125) = 0.22% ⇒ 对 8848 m 也只有 19 m。
K_SIGMA = 3.5

#: 要素表 —— (名称, 类, 中心纬度, 中心经度, 方位角°, σ_轴 km, σ_横 km, 峰高 m)。
#: **数值与模块 docstring 里的表逐行一一对应**。经度是 0..360 东经为正。
FEATURES: Tuple[Tuple[str, str, float, float, float, float, float, float], ...] = (
    # ── 东亚 / 西太平洋(登陆衰减最相关) ──
    ('台湾中央山脉',   'R',  23.45, 121.00,  25.0,   55.0,  22.0, 3700.0),
    ('台湾玉山',       'P',  23.47, 120.96,   0.0,   12.0,  12.0, 3952.0),
    ('海南五指山',     'R',  19.05, 109.70,  40.0,   45.0,  22.0, 1500.0),
    ('吕宋科迪勒拉',   'R',  17.00, 120.90,   5.0,   60.0,  25.0, 2200.0),
    ('吕宋马德雷山',   'R',  16.20, 122.05,  10.0,  170.0,  20.0, 1400.0),
    ('米沙鄢脊',       'R',  10.50, 122.50,  20.0,   60.0,  25.0, 1200.0),
    ('棉兰老脊',       'R',   7.50, 125.10,   0.0,   80.0,  35.0, 2100.0),
    ('九州脊',         'R',  32.80, 131.00,   7.0,   90.0,  40.0, 1600.0),
    ('本州西部脊',     'R',  34.80, 133.20,  76.0,  180.0,  40.0, 1200.0),
    ('本州中部脊',     'R',  36.00, 137.80,  20.0,  130.0,  45.0, 2400.0),
    ('富士山',         'P',  35.36, 138.73,   0.0,   15.0,  15.0, 3776.0),
    ('本州东北脊',     'R',  39.00, 140.50,  10.0,  180.0,  40.0, 1500.0),
    ('北海道脊',       'R',  43.30, 142.80,  30.0,  110.0,  40.0, 1700.0),
    ('朝鲜太白脊',     'R',  36.50, 128.30, 340.0,  130.0,  30.0, 1300.0),
    ('越南长山',       'R',  16.50, 107.30, 340.0,  200.0,  35.0, 1400.0),
    ('堪察加脊',       'R',  55.00, 159.00,  20.0,  300.0,  45.0, 2000.0),
    ('克柳切夫火山',   'P',  56.06, 160.64,   0.0,   18.0,  18.0, 4750.0),
    # ── 海洋性大陆 / 东南亚群岛 ──
    ('婆罗洲中央脊',   'R',   0.50, 114.50,  70.0,  150.0,  60.0, 1800.0),
    ('京那巴鲁',       'P',   6.08, 116.55,   0.0,   16.0,  16.0, 4095.0),
    ('苏门答腊脊',     'R',  -1.50, 101.50, 140.0,  400.0,  45.0, 2000.0),
    ('葛林芝火山',     'P',  -1.70, 101.26,   0.0,   15.0,  15.0, 3805.0),
    ('爪哇脊',         'R',  -7.60, 110.00, 100.0,  260.0,  30.0, 2000.0),
    ('新几内亚中央脊', 'R',  -5.50, 143.00, 110.0,  400.0,  50.0, 3000.0),
    ('查亚峰',         'P',  -4.08, 137.18,   0.0,   18.0,  18.0, 4884.0),
    # ── 美洲(东太 / 大西洋登陆衰减) ──
    ('落基山',         'R',  44.00, 246.00, 340.0,  450.0,  80.0, 2500.0),
    ('内华达山',       'R',  37.50, 240.50, 330.0,  200.0,  50.0, 2500.0),
    ('西马德雷山',     'R',  26.00, 253.00, 330.0,  300.0,  50.0, 2000.0),
    ('东马德雷山',     'R',  24.00, 260.00, 340.0,  250.0,  45.0, 1800.0),
    ('下加利福尼亚脊', 'R',  27.00, 247.00, 330.0,  350.0,  35.0, 1200.0),
    ('南马德雷山',     'R',  16.50, 264.00, 285.0,  200.0,  40.0, 1900.0),
    ('中美洲脊',       'R',  14.50, 269.00, 290.0,  120.0,  35.0, 2200.0),
    ('伊斯帕尼奥拉脊', 'R',  19.00, 289.00, 300.0,   90.0,  30.0, 1500.0),
    ('杜阿尔特峰',     'P',  18.95, 289.30,   0.0,   14.0,  14.0, 3098.0),
    ('安第斯山',       'R', -20.00, 291.00, 165.0, 1100.0,  70.0, 4000.0),
    ('阿空加瓜',       'P', -32.65, 290.00,   0.0,   20.0,  20.0, 6961.0),
    # ── 南亚 / 非洲 / 澳洲 / 欧洲 / 冰盖 ──
    ('西高止山',       'R',  14.00,  75.30, 160.0,  350.0,  30.0, 1600.0),
    ('阿纳穆迪',       'P',  10.17,  77.06,   0.0,   15.0,  15.0, 2695.0),
    ('喜马拉雅山',     'R',  29.00,  84.00, 110.0,  700.0,  90.0, 5500.0),
    ('珠穆朗玛峰',     'P',  27.99,  86.93,   0.0,   20.0,  20.0, 8848.0),
    ('天山',           'R',  42.00,  78.00, 100.0,  400.0,  70.0, 3500.0),
    ('东非大裂谷脊',   'R',  -1.00,  36.50, 350.0,  350.0,  60.0, 2000.0),
    ('乞力马扎罗',     'P',  -3.07,  37.35,   0.0,   18.0,  18.0, 5895.0),
    ('马达加斯加脊',   'R', -19.50,  46.50,  15.0,  280.0,  50.0, 1700.0),
    ('大分水岭',       'R', -30.00, 151.00,  15.0,  400.0,  60.0, 1100.0),
    ('科修斯科山',     'P', -36.46, 148.27,   0.0,   15.0,  15.0, 2228.0),
    ('阿尔卑斯山',     'R',  46.50,  10.00,  75.0,  250.0,  55.0, 2500.0),
    ('勃朗峰',         'P',  45.83,   6.86,   0.0,   15.0,  15.0, 4808.0),
    ('高加索山',       'R',  43.00,  43.00, 115.0,  250.0,  50.0, 3000.0),
    ('厄尔布鲁士峰',   'P',  43.35,  42.44,   0.0,   16.0,  16.0, 5642.0),
    ('格陵兰冰盖',     'R',  72.00, 318.00,   0.0,  600.0, 400.0, 2400.0),
    ('南极冰盖',       'C', -90.00,   0.00,   0.0, 1500.0, 1500.0, 3200.0),
)

#: 要素表版本号。改了 FEATURES 就把它 +1 ⇒ npz 里的 meta 与代码不一致时能被发现。
FEATURES_VERSION = 1


# ═══════════════════════════ 3. 合成 ═══════════════════════════

def _wrap180(d):
    """把经度差折到 (-180, 180] —— 与 core.py:1549 的 wrap180 同义。"""
    return (np.asarray(d) + 180.0) % 360.0 - 180.0


def feature_window(f: Sequence) -> Tuple[float, float, float, float]:
    """要素的经纬度包围盒(°)。取 3.5σ, 且用**旋转后的轴对齐包围盒**而不是
    "±3.5(σ_轴+σ_横)" —— 后者对东西向长脊会白白多算 10 倍面积。

    反解 u/v: dx = u·sin(az) + v·cos(az), dy = u·cos(az) - v·sin(az) ⇒
        |dx| ≤ sqrt((3.5σ_u·sin)² + (3.5σ_v·cos)²)
        |dy| ≤ sqrt((3.5σ_u·cos)² + (3.5σ_v·sin)²)
    """
    _name, kind, lat_c, lon_c, az, su, sv, _H = f
    if kind == 'C':
        dlat = K_SIGMA * su / KM_PER_DEG_LAT
        if lat_c < 0.0:
            return (-90.0, -90.0 + dlat, LON0, LON1)
        return (90.0 - dlat, 90.0, LON0, LON1)
    a = math.radians(az)
    sa, ca = math.sin(a), math.cos(a)
    rx = math.hypot(K_SIGMA * su * sa, K_SIGMA * sv * ca)
    ry = math.hypot(K_SIGMA * su * ca, K_SIGMA * sv * sa)
    coslat = max(0.05, abs(math.cos(math.radians(lat_c))))
    dlat = ry / KM_PER_DEG_LAT
    dlon = min(180.0, rx / (KM_PER_DEG_LON * coslat))
    return (lat_c - dlat, lat_c + dlat, lon_c - dlon, lon_c + dlon)


def _evaluate(f: Sequence, lon_w: np.ndarray, lat_w: np.ndarray) -> np.ndarray:
    """在窗口坐标上估一个要素(广播, 不建 meshgrid)。返回 (nlat, nlon) float32。"""
    _name, kind, lat_c, lon_c, az, su, sv, H = f
    if kind == 'C':
        d_pole = (90.0 - np.abs(lat_w)) * KM_PER_DEG_LAT
        e = H * np.exp(-0.5 * (d_pole / su) ** 2)
        return np.broadcast_to(e[:, None], (lat_w.size, lon_w.size)).astype(np.float32)
    a = math.radians(az)
    sa, ca = math.sin(a), math.cos(a)
    dx = _wrap180(lon_w - lon_c)[None, :] * (KM_PER_DEG_LON
                                             * math.cos(math.radians(lat_c)))
    dy = (lat_w[:, None] - lat_c) * KM_PER_DEG_LAT
    u = dx * sa + dy * ca
    v = dx * ca - dy * sa
    e = H * np.exp(-0.5 * ((u / su) ** 2 + (v / sv) ** 2))
    return e.astype(np.float32)


def _accumulate(blk, lon_axis, lat_axis, j0, sub, f) -> None:
    """把一个要素的最大值并进块缓冲 blk(就地 np.maximum)。

    blk      : (nrow_coarse·sub, ENVNX·sub) float32
    lat_axis : blk 对应的细网格纬度轴(长度 nrow_coarse·sub)
    j0       : blk 第一行对应的**全局粗行号**
    """
    la_lo, la_hi, lo_lo, lo_hi = feature_window(f)
    nrow_coarse = lat_axis.size // sub
    jlo = max(j0, int(math.floor((la_lo - LAT0) / ENVD)) - 1)
    jhi = min(j0 + nrow_coarse, int(math.ceil((la_hi - LAT0) / ENVD)) + 1)
    if jlo >= jhi:
        return
    clo = int(math.floor((lo_lo - LON0) / ENVD)) - 1
    chi = int(math.ceil((lo_hi - LON0) / ENVD)) + 1
    # 跨 0/360 或整圈: 直接全经度算(_wrap180 会处理几何), 否则按窗口裁剪
    if clo < 0 or chi > ENVNX or (chi - clo) >= ENVNX:
        clo, chi = 0, ENVNX
    r0, r1 = (jlo - j0) * sub, (jhi - j0) * sub
    c0, c1 = clo * sub, chi * sub
    e = _evaluate(f, lon_axis[c0:c1], lat_axis[r0:r1])
    np.maximum(blk[r0:r1, c0:c1], e, out=blk[r0:r1, c0:c1])


def synthesize(sub: int = DEFAULT_SUB,
               features: Optional[Sequence] = None
               ) -> Tuple[np.ndarray, np.ndarray]:
    """按 FEATURES 合成海拔场。**未加陆地掩码**(裸合成场)。

    返回 ``(raw_mean, raw_max)``, 各 (ENVNY, ENVNX) float32:
        raw_mean 格内 (sub×sub) 均值;  raw_max 格内最大。
    纯函数、无随机数 ⇒ 同参数逐位可复现。掩码另走 :func:`apply_land_mask`。
    """
    feats = FEATURES if features is None else tuple(features)
    sub = max(1, int(sub))
    mean = np.zeros((ENVNY, ENVNX), dtype=np.float32)
    mx = np.zeros((ENVNY, ENVNX), dtype=np.float32)
    # 细采样在格内的相对偏移: sub=10 → -0.225, -0.175, …, +0.225 (度)
    off = (np.arange(sub, dtype=np.float64) + 0.5 - sub * 0.5) * (ENVD / sub)
    n_fine_lon = ENVNX * sub
    lon_fine = (LON0 + (np.arange(n_fine_lon) // sub + 0.5) * ENVD
                + off[np.arange(n_fine_lon) % sub])
    # 分块逐行处理: 峰值内存 = BLK·sub × ENVNX·sub 个 float32
    blk_rows = 24
    for j0 in range(0, ENVNY, blk_rows):
        j1 = min(ENVNY, j0 + blk_rows)
        rows = np.arange(j0 * sub, j1 * sub)
        lat_fine = LAT0 + (rows // sub + 0.5) * ENVD + off[rows % sub]
        blk = np.zeros(((j1 - j0) * sub, n_fine_lon), dtype=np.float32)
        for f in feats:
            _accumulate(blk, lon_fine, lat_fine, j0, sub, f)
        b = blk.reshape(j1 - j0, sub, ENVNX, sub)
        mean[j0:j1] = b.mean(axis=(1, 3)).astype(np.float32)
        mx[j0:j1] = b.max(axis=(1, 3)).astype(np.float32)
    return mean, mx


def apply_land_mask(raw_mean: np.ndarray, raw_max: np.ndarray,
                    landfrac: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """把海洋上的合成海拔抹平(见模块 docstring「陆地掩码」)。

    纯海洋格(landfrac≤0)两个场都严格 = 0.0; 部分陆地格 mean 按比例缩、max 保留。
    """
    lf = np.clip(np.asarray(landfrac, dtype=np.float32), 0.0, 1.0)
    mean = (np.asarray(raw_mean, dtype=np.float32) * lf).astype(np.float32)
    mx = np.where(lf > 0.0, np.asarray(raw_max, dtype=np.float32),
                  np.float32(0.0)).astype(np.float32)
    return mean, mx


def describe_features(features: Optional[Sequence] = None) -> str:
    """把 FEATURES 打成一张可读表(构建脚本打印用, 便于与注释里的表核对)。"""
    feats = FEATURES if features is None else tuple(features)
    lines = ['  #  名称                 类  中心纬度  中心经度  方位角   σ_轴    σ_横   峰高(m)',
             '  ' + '─' * 74]
    for k, (nm, kind, la, lo, az, su, sv, H) in enumerate(feats, 1):
        lines.append('  %2d  %-18s %s  %7.2f  %8.2f  %6.1f  %6.1f  %6.1f  %7.0f'
                     % (k, nm, kind, la, lo, az, su, sv, H))
    return '\n'.join(lines)


# ═══════════════════════════ 4. 加载(带缓存) ═══════════════════════════

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'terrain_0.5.npz')
_CACHE: Dict[str, object] = {'tried': False, 'data': None}


def terrain_file() -> str:
    """npz 路径(与 land_0.5.npz 同目录)。"""
    return _FILE


def reset_cache() -> None:
    """丢掉模块级缓存(测试/换文件后用)。"""
    _CACHE['tried'] = False
    _CACHE['data'] = None


def load(force: bool = False) -> Optional[dict]:
    """读 terrain_0.5.npz(带缓存)。文件不存在 → None; 形状不对 → ValueError。"""
    if _CACHE['data'] is not None and not force:
        return _CACHE['data']          # type: ignore[return-value]
    if not os.path.exists(_FILE):
        return None
    with np.load(_FILE) as z:
        keys = set(z.files)
        em = np.asarray(z['elev_mean'], dtype=np.float32)
        ex = np.asarray(z['elev_max'], dtype=np.float32)
        lf = (np.asarray(z['landfrac'], dtype=np.float32)
              if 'landfrac' in keys else None)
        meta = json.loads(str(z['meta'])) if 'meta' in keys else {}
    if em.shape != (ENVNY, ENVNX) or ex.shape != (ENVNY, ENVNX):
        raise ValueError('terrain_0.5.npz 形状 %s/%s 与网格 (%d,%d) 不一致 —— 需重建'
                         % (em.shape, ex.shape, ENVNY, ENVNX))
    d = {'elev_mean': em, 'elev_max': ex, 'landfrac': lf, 'meta': meta, 'path': _FILE}
    _CACHE['data'] = d
    return d


def available() -> bool:
    """npz 是否可用(存在 + 形状对)。任何异常都算"不可用", 消费方据此退回原行为。"""
    try:
        return load() is not None
    except Exception:
        return False


def elev_mean() -> np.ndarray:
    """(ENVNY, ENVNX) float32, 格内平均海拔(m)。缺文件时抛 FileNotFoundError。"""
    d = load()
    if d is None:
        raise FileNotFoundError(terrain_file())
    return d['elev_mean']          # type: ignore[return-value]


def elev_max() -> np.ndarray:
    """(ENVNY, ENVNX) float32, 格内最高海拔(m)。缺文件时抛 FileNotFoundError。"""
    d = load()
    if d is None:
        raise FileNotFoundError(terrain_file())
    return d['elev_max']           # type: ignore[return-value]


def stats() -> dict:
    """文件统计量(构建脚本打印 + 测试核对用)。"""
    d = load()
    if d is None:
        return {'exists': False, 'path': _FILE}
    em, ex = d['elev_mean'], d['elev_max']
    out = {
        'exists': True, 'path': _FILE,
        'shape': tuple(int(v) for v in ex.shape),
        'dtype': str(ex.dtype),
        'mean_min': float(em.min()), 'mean_max': float(em.max()),
        'mean_avg': float(em.mean()),
        'max_min': float(ex.min()), 'max_max': float(ex.max()),
        'max_avg': float(ex.mean()),
        'nan': int(np.isnan(em).sum() + np.isnan(ex).sum()),
        'n_nonzero_max': int((ex > 0).sum()),
        'n_land_cells': int((ex > 0).sum()),
    }
    if d.get('landfrac') is not None:
        lf = d['landfrac']
        out['ocean_max_elev'] = float(ex[lf <= 0.0].max()) if (lf <= 0).any() else 0.0
    out['meta'] = d.get('meta', {})
    out['size_bytes'] = os.path.getsize(_FILE)
    return out


# ═══════════════════════════ 5. 双线性采样 ═══════════════════════════

def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def bilinear(arr: np.ndarray, lon: float, lat: float) -> float:
    """双线性采样 —— **逐条照抄 core.py:595-617 ``env_bilinear``** 的约定:

      · 经度先回绕到 [LON0, LON0+360)(西经负数必须回绕, core.py:604);
      · fx = (lon-LON0)/ENVD - 0.5, fy = (lat-LAT0)/ENVD - 0.5(格心对齐);
      · 索引 clamp 到 [0, N-2], 权重 clamp 到 [0,1](极点/边界不外插);
      · 采样点正好落在格心时 **精确返回该格值**(tx=ty=0)。
    """
    lon = (lon - LON0) % 360.0 + LON0
    fx = (lon - LON0) / ENVD - 0.5
    fy = (lat - LAT0) / ENVD - 0.5
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = _clamp(x0, 0, ENVNX - 2)
    y1 = _clamp(y0, 0, ENVNY - 2)
    tx = _clamp(fx - x0, 0.0, 1.0)
    ty = _clamp(fy - y0, 0.0, 1.0)
    a = arr[y1, x1]
    b = arr[y1, x1 + 1]
    c = arr[y1 + 1, x1]
    d = arr[y1 + 1, x1 + 1]
    return float((a * (1.0 - tx) + b * tx) * (1.0 - ty) + (c * (1.0 - tx) + d * tx) * ty)


def bilinear_arr(arr: np.ndarray, lon, lat) -> np.ndarray:
    """bilinear 的数组版(逐点与标量版等价) —— 对应 core.py:624 ``env_bilinear_arr``。"""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    lon = (lon - LON0) % 360.0 + LON0
    fx = (lon - LON0) / ENVD - 0.5
    fy = (lat - LAT0) / ENVD - 0.5
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


def elev_at(lon: float, lat: float, use_max: bool = False) -> float:
    """单点海拔(m)。``use_max=False``(默认)= 平滑面海拔 ``elev_mean``(摩擦/引导用);
    ``use_max=True`` = 格内最高海拔 ``elev_max``(峰效应/登陆衰减用)。海洋格恒为 0。"""
    return bilinear(elev_max() if use_max else elev_mean(), lon, lat)


def elev_at_arr(lon, lat, use_max: bool = False) -> np.ndarray:
    """elev_at 的数组版(粒子 / 环带批量采样)。"""
    return bilinear_arr(elev_max() if use_max else elev_mean(), lon, lat)


# ═══════════════════════════ 6. 沿途海拔 ═══════════════════════════
#: 路径默认步长(km)。10 km 对 6h/300km 的步长约 30 个采样点, 成本 µs 级。
DEFAULT_STEP_KM = 10.0


def _seg_samples(lon_a: float, lat_a: float, lon_b: float, lat_b: float,
                 step_km: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """直线段(等经纬度插值)细化采样。返回 (lons, lats, s_km)。端点都含。"""
    dlon = float(_wrap180(lon_b - lon_a))
    dlat = float(lat_b - lat_a)
    lat_mid = 0.5 * (lat_a + lat_b)
    coslat = max(0.05, abs(math.cos(math.radians(lat_mid))))
    dx_km = dlon * KM_PER_DEG_LON * coslat
    dy_km = dlat * KM_PER_DEG_LAT
    L = math.hypot(dx_km, dy_km)
    n = max(1, int(math.ceil(L / max(1e-6, float(step_km)))))
    t = np.linspace(0.0, 1.0, n + 1)
    lons = (lon_a + dlon * t) % 360.0
    lats = lat_a + dlat * t
    return lons, lats, L * t


def path_samples(lons, lats, step_km: float = DEFAULT_STEP_KM
                 ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """折线路径细化采样。返回 (采样经度, 采样纬度, 累计里程 km)。"""
    lo = np.asarray(lons, dtype=np.float64).ravel()
    la = np.asarray(lats, dtype=np.float64).ravel()
    if lo.size == 0:
        return lo, la, np.zeros(0, dtype=np.float64)
    if lo.size == 1:
        return lo, la, np.zeros(1, dtype=np.float64)
    plo: List[np.ndarray] = []
    pla: List[np.ndarray] = []
    ps: List[np.ndarray] = []
    s0 = 0.0
    for k in range(lo.size - 1):
        s_lon, s_lat, s_km = _seg_samples(float(lo[k]), float(la[k]),
                                          float(lo[k + 1]), float(la[k + 1]), step_km)
        if k > 0:                       # 去掉与上一段重复的接缝点
            s_lon, s_lat, s_km = s_lon[1:], s_lat[1:], s_km[1:]
        plo.append(s_lon)
        pla.append(s_lat)
        ps.append(s_km + s0)
        s0 = float(ps[-1][-1])
    return (np.concatenate(plo), np.concatenate(pla), np.concatenate(ps))


def elev_profile(lon_a: float, lat_a: float, lon_b: float, lat_b: float,
                 step_km: float = DEFAULT_STEP_KM, use_max: bool = True
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """单段路径的海拔剖面 (s_km, elev_m)。"""
    lons, lats, s = _seg_samples(lon_a, lat_a, lon_b, lat_b, step_km)
    return s, bilinear_arr(elev_max() if use_max else elev_mean(), lons, lats)


def peak_along(lon_a: float, lat_a: float, lon_b: float, lat_b: float,
               step_km: float = DEFAULT_STEP_KM, use_max: bool = True) -> float:
    """**这一段路径上的最高海拔**(m) —— 登陆衰减的核心输入。

    默认在 ``elev_max`` 上取, 即"台风这条路上最高的那座山"。海洋段恒为 0。
    对调两端点结果相同(段内取最大与方向无关)。
    """
    _s, e = elev_profile(lon_a, lat_a, lon_b, lat_b, step_km, use_max)
    return float(np.max(e)) if e.size else 0.0


def mean_along(lon_a: float, lat_a: float, lon_b: float, lat_b: float,
               step_km: float = DEFAULT_STEP_KM, use_max: bool = False) -> float:
    """这一段路径上的**平均**海拔(m)(面效应; 默认在 ``elev_mean`` 上取)。"""
    _s, e = elev_profile(lon_a, lat_a, lon_b, lat_b, step_km, use_max)
    return float(np.mean(e)) if e.size else 0.0


def peak_along_series(lon_a: float, lat_a: float, lon_b: float, lat_b: float,
                      step_km: float = DEFAULT_STEP_KM, use_max: bool = True
                      ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(s_km, 逐点海拔, 逐点累计最大)。第三个数组**单调不减**(np.maximum.accumulate)。"""
    s, e = elev_profile(lon_a, lat_a, lon_b, lat_b, step_km, use_max)
    return s, e, np.maximum.accumulate(e) if e.size else e


def peak_along_track(lons, lats, step_km: float = DEFAULT_STEP_KM,
                     use_max: bool = True) -> np.ndarray:
    """折线路径上**每个顶点的累计最高海拔**(m), 长度与输入顶点数相同。

    定义: ``out[k] = max(顶点 0..k 之间的所有采样点海拔)`` ⇒ **按构造单调不减**
    (这正是"沿途最高海拔"这个量该有的性质: 路走得越长, 遇到的最高峰只可能更高)。
    典型用法: 把一条观测/预报路径整条喂进来, 得到"到该点为止翻过的最高山"。
    """
    lo = np.asarray(lons, dtype=np.float64).ravel()
    la = np.asarray(lats, dtype=np.float64).ravel()
    if lo.size == 0:
        return np.zeros(0, dtype=np.float64)
    arr = elev_max() if use_max else elev_mean()
    out = np.empty(lo.size, dtype=np.float64)
    run = float(bilinear(arr, float(lo[0]), float(la[0])))
    out[0] = run
    for k in range(1, lo.size):
        s_lon, s_lat, _s = _seg_samples(float(lo[k - 1]), float(la[k - 1]),
                                        float(lo[k]), float(la[k]), step_km)
        run = max(run, float(np.max(bilinear_arr(arr, s_lon, s_lat))))
        out[k] = run
    return out


# ═══════════════════ 7. 摩擦 / 登陆衰减的无量纲因子 ═══════════════════
# 这里只给**形状**, 不给标定值 —— 系数最终进 core.py 的 params(见补丁规格)。
# 两个因子的共同设计目标: **海拔为 0 时严格等于 1** ⇒ 不改变现有海上行为。

#: 摩擦折减的 e 折尺度(m): 1500 m → 0.368, 3000 m → 0.135, 500 m → 0.717
ELEV_FRICTION_E0 = 1500.0
#: 登陆衰减的参考峰高(m): 沿途峰高每 +1×该值, 衰减倍率 +1
ELEV_PEAK_P0 = 1000.0


def friction_scale(elev_m: float, e0: Optional[float] = None) -> float:
    """海拔 → 摩擦/引导权重折减因子 ∈ (0, 1]。``exp(-max(0,elev)/e0)``。

    物理含义: 山脉越高, 低层气流越被地形阻塞 ⇒ 该点对台风引导的贡献越小
    (对应 core.py:2053 的 ``wl = 1-land`` 那一套权重)。``elev_m = 0`` 时恒为 1.0。
    """
    _e0 = float(ELEV_FRICTION_E0 if e0 is None else e0)
    return float(math.exp(-max(0.0, float(elev_m)) / max(1e-6, _e0)))


def friction_scale_arr(elev_m, e0: Optional[float] = None) -> np.ndarray:
    """friction_scale 的数组版。"""
    _e0 = float(ELEV_FRICTION_E0 if e0 is None else e0)
    return np.exp(-np.maximum(0.0, np.asarray(elev_m, dtype=np.float64))
                  / max(1e-6, _e0))


def decay_scale(peak_m: float, p0: Optional[float] = None) -> float:
    """沿途最高海拔 → 登陆衰减倍率 ≥ 1。``1 + max(0,peak)/p0``。

    ``peak_m = 0``(纯海洋段)时恒为 1.0 ⇒ 海上逐位不变; 台湾中央山脉一条
    (峰高 ≈3.9 km, p0=1000)给 ≈4.9 倍, 吕宋科迪勒拉(≈2.2 km)给 ≈3.2 倍。
    """
    _p0 = float(ELEV_PEAK_P0 if p0 is None else p0)
    return float(1.0 + max(0.0, float(peak_m)) / max(1e-6, _p0))
