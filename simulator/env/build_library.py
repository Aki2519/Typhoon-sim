# simulator/env/build_library.py
"""F6 建库: 下载→解析→归档 npz + 模态对齐索引。

真实数据不可用时构建"合成场库"(确定性,标记 synthetic):
  - SST   = 季节气候态(纬度梯度+季节正弦) + ENSO 东太增暖 + PDO 距平 + 噪声
  - OHC   = 独立于 SST 生成(白海豚式: 冷水上翻信号可出现在高温区)
  - MSLP/风/湿度 = 气候态 + 模态响应
合成库满足: 场结构合理、模态-场回归可标定、OHC-SST 独立性可验证,
真实数据到位后以相同管道重建。
"""
from __future__ import annotations
import os
import numpy as np
from typing import Dict, List, Optional, Tuple

from . import field_io as F
from . import downloader

LIB_YEARS = range(1979, 2027)     # 1979-今
CLIM_BASE = range(1991, 2021)     # 1991-2020 基准

ENV_DIR = os.path.dirname(os.path.abspath(__file__))


# ════════════════════ 合成场库(占位降级)════════════════════

def _lat_weights():
    return F.LATS


def synth_sst_month(ym: Tuple[int, int], seed: int,
                    oni: float, pdo: float) -> np.ndarray:
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo) % (2**32))
    lat = F.LATS[:, None]
    # 气候态: 纬向温度廓线(赤道 30°C → 60° 约 4°C) + 最暖纬度季节迁移(±8°)
    # R2-7: 原 std=32° 高斯使两极与赤道同为 ~28-31°C(全图无冷区,SST 图层失真)
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    clat = 8.0 * season
    clim = 29.5 - 0.42 * np.abs(lat - clat) \
        + 1.5 * np.exp(-((lat - clat) ** 2) / (2 * 8 ** 2))
    clim = np.clip(clim, -1.0, 31.0)
    # ENSO 距平: 东太增暖(西半球经度 >180 更暖)
    lon = F.LONS[None, :]
    east = np.where(lon > 150, (lon - 150) / 210.0, 0.0)   # 东太权重
    enso_anom = oni * 1.2 * east * np.exp(-((lat - 0) ** 2) / (2 * 14 ** 2))
    pdo_anom = pdo * 0.6 * np.exp(-((lat - 35) ** 2) / (2 * 16 ** 2))
    # R3-2: 洋流结构(黑潮/湾流暖舌, 秘鲁/加利福尼亚/索马里冷水, 赤道冷舌)
    currents = (
        +1.6 * np.exp(-((lat - 25) ** 2) / (2 * 6 ** 2)) * np.exp(-((lon - 135) ** 2) / (2 * 45 ** 2))
        + 1.8 * np.exp(-((lat - 38) ** 2) / (2 * 5 ** 2)) * np.exp(-((lon - 305) ** 2) / (2 * 25 ** 2))
        - 1.8 * np.exp(-((lat + 10) ** 2) / (2 * 7 ** 2)) * np.exp(-((lon - 280) ** 2) / (2 * 12 ** 2))
        - 1.4 * np.exp(-((lat - 30) ** 2) / (2 * 6 ** 2)) * np.exp(-((lon - 240) ** 2) / (2 * 10 ** 2))
        - 1.5 * np.exp(-((lat - 8) ** 2) / (2 * 5 ** 2)) * np.exp(-((lon - 50) ** 2) / (2 * 10 ** 2))
        * max(0.0, season)
        - 0.9 * np.exp(-((lat - 0) ** 2) / (2 * 2.5 ** 2)) * np.exp(-((lon - 250) ** 2) / (2 * 35 ** 2))
    )
    noise = rng.normal(0, 0.18, clim.shape)
    return clim + enso_anom + pdo_anom + currents + noise


def synth_ohc_month(ym: Tuple[int, int], seed: int, oni: float) -> np.ndarray:
    """OHC 独立生成: 与 SST 部分相关(共享气候态),但含独立的上翻冷信号
    (负 OHC 距平可出现在高温 SST 区,白海豚式)。"""
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo + 99991) % (2**32))
    lat = F.LATS[:, None]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    # R2-7: 收窄纬度高斯(std 26→12),高纬 OHC 回落,与 SST 冷区一致
    clim = 90.0 + 60.0 * np.exp(-((lat - 5 * season) ** 2) / (2 * 12 ** 2))
    # 深层暖水: 西太暖池(90-150E, 0-20N) + 黑潮延伸体暖舌
    lon = F.LONS[None, :]
    warm_pool = 35.0 * np.exp(-((lon - 120) ** 2) / (2 * 45 ** 2)) * \
        np.exp(-((lat - 10) ** 2) / (2 * 13 ** 2))
    kuro_o = 12.0 * np.exp(-((lat - 26) ** 2) / (2 * 7 ** 2)) * \
        np.exp(-((lon - 140) ** 2) / (2 * 55 ** 2))
    # D5: ENSO 对 OHC 的东西偶极(El Niño 时东太加深、西太暖池热含量下降
    # ——温跃层抬升),原实现全经度同号增暖与真实 ENSO-OHC 响应相反
    enso = oni * 8.0 * np.exp(-((lat - 0) ** 2) / (2 * 16 ** 2)) * \
        ((lon - 210.0) / 210.0)
    noise = rng.normal(0, 6.0, clim.shape)
    # 独立冷上翻: 热带海洋现象(采样 0-15N,可落在 SST 高温带,白海豚式);
    # R2-8: 幅度 -18 不足以把暖池 OHC(≈145)压到全局 p10(≈95)之下,
    # 真实上翻区冷异常 20-60 kJ/cm² 量级,取 -55 使信号真正可辨识
    upx = rng.randint(60, 300)
    upy = rng.randint(0, 15)
    upwell = -55.0 * np.exp(-((lon - upx) ** 2) / (2 * 8 ** 2)) * \
        np.exp(-((lat - upy) ** 2) / (2 * 6 ** 2))
    return np.clip(clim + warm_pool + kuro_o + enso + noise + upwell, 5, 180)


def synth_mslp_month(ym: Tuple[int, int], seed: int, oni: float, pdo: float) -> np.ndarray:
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo + 7777) % (2**32))
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    base = (1012.0 - 8.0 * season) + np.zeros((F.NLAT, F.NLON))
    # D4: 副高/ITCZ 随季节迁移(NH 夏 ITCZ 北移至 ~11°N, 冬 ~1°N),
    # 并补南半球副高(30°S 高压)——固定 30N/6S 使 NH 夏季 ITCZ 门无法生成
    ridge_lat = 30.0 + 4.0 * season
    trough_lat = 6.0 + 5.0 * season
    ridge = 4.0 * np.exp(-((lat - ridge_lat) ** 2) / (2 * 10 ** 2))
    trough = -3.0 * np.exp(-((lat - trough_lat) ** 2) / (2 * 8 ** 2))
    s_ridge = 2.5 * np.exp(-((lat + 30.0) ** 2) / (2 * 10 ** 2))
    # R2-2: 经向结构(副高东西延伸: 西太副高 120-160E 偏强)
    ridge_lon = 4.0 * np.exp(-((lon - 140.0) ** 2) / (2 * 60 ** 2))
    enso = oni * 1.5 * np.exp(-((lat - 10) ** 2) / (2 * 18 ** 2))
    noise = rng.normal(0, 0.5, (F.NLAT, F.NLON))
    return base + ridge + trough + s_ridge + ridge_lon + enso + noise


def synth_wind_month(ym: Tuple[int, int], seed: int) -> Tuple[np.ndarray, np.ndarray,
                                                              Tuple[np.ndarray, np.ndarray],
                                                              Tuple[np.ndarray, np.ndarray]]:
    """850/500/200hPa 三层 U/V(统一广播到 (NLAT, NLON));热带切变 ~10kt。
    500hPa 独立剖面,不复制 850(A1)。R3-2: 加入急流核(纬向带+经向波状),
    使切变/引导风有真实结构(原剖面只随纬度变化,整图一条水平带)。"""
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo + 5555) % (2**32))
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    u850 = 5.0 * np.tanh((lat - 20) / 10.0) + 0.5 * np.sin(lon * np.pi / 90.0)
    v850 = rng.normal(0, 0.3, lat.shape) + 0.2 * np.sin(lon * np.pi / 45.0)
    u500 = 7.5 * np.tanh((lat - 26) / 13.0) + 0.8 * np.sin(lon * np.pi / 75.0)
    v500 = rng.normal(0, 0.45, lat.shape) + 0.3 * np.sin(lon * np.pi / 50.0)
    u200 = 10.0 * np.tanh((lat - 30) / 16.0) + 1.2 * np.sin(lon * np.pi / 60.0)
    v200 = rng.normal(0, 0.6, lat.shape) + 0.4 * np.sin(lon * np.pi / 40.0)
    # 急流核(随季节南北迁移,经向波状增强)
    jet_lat = 32.0 - 6.0 * season
    jet = 5.0 * np.exp(-((lat - jet_lat) ** 2) / (2 * 5 ** 2)) * \
        (0.5 + 0.5 * np.sin(lon * np.pi / 35.0 + y))
    u200 = u200 + jet
    u500 = u500 + 0.4 * jet
    shape = (F.NLAT, F.NLON)
    return ((np.broadcast_to(u850, shape), np.broadcast_to(v850, shape)),
            (np.broadcast_to(u500, shape), np.broadcast_to(v500, shape)),
            (np.broadcast_to(u200, shape), np.broadcast_to(v200, shape)))


def synth_rh700_month(ym: Tuple[int, int], seed: int, oni: float) -> np.ndarray:
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo + 3333) % (2**32))
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    base = (55.0 + 20.0 * np.exp(-((lat - 8 * season) ** 2) / (2 * 14 ** 2))
            + np.zeros((F.NLAT, F.NLON)))
    # R2-2: 经向湿度结构(西太湿润/东太干燥)
    lon_rh = -6.0 * np.exp(-((lon - 260.0) ** 2) / (2 * 50 ** 2))
    enso = oni * 6.0 * np.exp(-((lat - 5) ** 2) / (2 * 12 ** 2))
    noise = rng.normal(0, 2.0, (F.NLAT, F.NLON))
    return np.clip(base + lon_rh + enso + noise, 5, 100)


# ═════════ F10 渲染新字段(合成近似; 量级合理, 无 NaN/Inf)═════════
# 契约以 field_io.FIELD_DEFS / RENDER_VARS 为准(本文件不重复定义形状)。

def _cyclone_mask(vo: np.ndarray, ym: Tuple[int, int]) -> np.ndarray:
    """台风活跃掩膜 0..1(合成近似): 850hPa 气旋性涡度(NH 负相对涡度)归一化
    + 该月台风生成区气候纬带高斯, 保证"近台风大、环境小"的物理结构。"""
    y, mo = ym
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    cycl = np.maximum(-vo, 0.0)                  # NH 气旋性(负涡度)信号
    m = float(np.nanmax(cycl))
    tc_v = cycl / (m if m > 1e-9 else 1.0)
    # 台风生成区气候纬带(NW 太平洋, 夏季北移)
    tc_lat = 12.0 + 5.0 * season
    belt = np.exp(-((lat - tc_lat) ** 2) / (2 * 8 ** 2)) * \
        np.exp(-((lon - 140.0) ** 2) / (2 * 60 ** 2))
    return np.clip(0.5 * tc_v + 0.5 * belt, 0.0, 1.0)


def synth_surface_wind_month(ym: Tuple[int, int], seed: int,
                             uu: np.ndarray, vv: np.ndarray
                             ) -> Tuple[np.ndarray, np.ndarray]:
    """u10/v10(合成近似): 边界层摩擦对引导气流的减速(×~0.55)+ 小随机扰动。"""
    y, mo = ym
    rng = np.random.RandomState((seed + y * 12 + mo + 10001) % (2**32))
    u10 = 0.55 * uu + rng.normal(0, 0.8, uu.shape)
    v10 = 0.55 * vv + rng.normal(0, 0.8, vv.shape)
    return u10, v10


def synth_precip24_month(ym: Tuple[int, int],
                         mask: np.ndarray) -> np.ndarray:
    """precip24(合成近似): 热带辐合带降水基值 + 台风活跃区权重增强, 0-300mm/24h。"""
    mo = ym[1]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    clat = 8.0 * season
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    base = 20.0 + 90.0 * np.exp(-((lat - clat) ** 2) / (2 * 12 ** 2)) * \
        np.exp(-((lon - 150.0) ** 2) / (2 * 90 ** 2))
    return np.clip(base + 220.0 * mask, 0.0, 300.0)


def synth_t500_month(mask: np.ndarray) -> np.ndarray:
    """t500(合成近似): 纬度带气候温度(240-265K)+ 台风暖心扰动。"""
    lat = F.LATS[:, None]
    clim = 258.0 - 0.25 * np.abs(lat)          # 赤道 ~258K → ±60° ~243K
    return np.clip(clim + 10.0 * mask, 150.0, 300.0)


def synth_qv850_month(rh: np.ndarray, sst: np.ndarray) -> np.ndarray:
    """qv850(合成近似): 由 700hPa 相对湿度与 SST 的饱和比湿近似推导(Clausius-Clapeyron)。
    qv ≈ qsat(T_sst) × rh/100, 850hPa 抬升饱和比湿近似。

    R3: sst 来自 synth_sst_month, 单位是 **°C**(合成库全链 °C, 下限 -1、上限 31),
    而 Magnus 公式要求 **Kelvin** 输入。原实现 `np.clip(sst, 260, 310)` 把 °C 值
    (约 -1..31) 当 K 用, 热带 ~28°C 整场被钉死为 260K, 使 qv850 恒约 1.3 g/kg
    而与 SST 无关(正确值 ~24 g/kg, 偏差 ~18×)。先用 °C→K 换算再算饱和比湿。
    与 real_source._rh_to_q(同样 +273.15 后再算 Magnus)口径一致。"""
    T = np.clip(sst + 273.15, 260.0, 310.0)    # °C → K, 再限幅
    esat = 6.112 * np.exp(17.67 * (T - 273.15) / (T - 29.65))   # hPa
    qsat = 622.0 * esat / np.maximum(850.0 - esat, 1.0)         # g/kg
    return np.clip(qsat * (rh / 100.0), 0.0, 25.0)


def synth_hgt100_month(mslp: np.ndarray) -> np.ndarray:
    """hgt100(合成近似): 由 MSLP 反推 100hPa 位势厚度, 单位 dam。
    hgt(m) ≈ 16650 + (1013 − MSLP)·8 → hgt(dam) = hgt(m)/10。"""
    hgt_m = 16650.0 + (1013.0 - mslp) * 8.0
    return hgt_m / 10.0


def synth_hgt500_month(mslp: np.ndarray) -> np.ndarray:
    """hgt500(合成近似): 由 MSLP 反推 500hPa 位势高度, 单位 dam。
    hgt(m) ≈ 5600 + (1013 − MSLP)·10 → hgt(dam) = hgt(m)/10(F10 render_mid 用)。"""
    hgt_m = 5600.0 + (1013.0 - mslp) * 10.0
    return hgt_m / 10.0


def synth_uv200_month(ym: Tuple[int, int], u200: np.ndarray, v200: np.ndarray,
                      vo: np.ndarray) -> np.ndarray:
    """uv200(合成近似): 气候高空风(200hPa, 已带急流核)+ 台风高空辐散外流。
    辐散以暖心区 ± 相对位点的小扰动量近似(不必精确无辐散)。"""
    mo = ym[1]
    season = np.cos(2 * np.pi * (mo - 8) / 12.0)
    clat = 8.0 * season
    lat = F.LATS[:, None]
    lon = F.LONS[None, :]
    mask = _cyclone_mask(vo, ym)
    du = 2.5 * mask * np.tanh((lon - 150.0) / 40.0)
    dv = 2.5 * mask * np.tanh((lat - clat) / 30.0)
    return np.stack([u200 + du, v200 + dv])



# ════════════════════ 派生场 ════════════════════

def shear_from_winds(u850, v850, u200, v200) -> np.ndarray:
    return np.hypot(u200 - u850, v200 - v850)


def steer_from_winds(uvs: List[Tuple[np.ndarray, np.ndarray]],
                     weights: List[float]) -> Tuple[np.ndarray, np.ndarray]:
    """850-300hPa 深度平均引导气流(质量加权)。uvs = [(u, v), ...] 对应层。"""
    tot = sum(weights)
    u = sum(w * uv[0] for uv, w in zip(uvs, weights)) / tot
    v = sum(w * uv[1] for uv, w in zip(uvs, weights)) / tot
    return u, v


def vort850_from_winds(u850, v850) -> np.ndarray:
    """850hPa 相对涡度(s^-1): 像素梯度换算为物理单位。
    D1: np.gradient 的格距为 1 个格点(≈1°), 每格点 ≈ 111.32 km,
    直接除以 111320.0(旧值多乘 π/180 使涡度放大 57.3 倍, GPI 失真)。
    R: ζ = ∂v/∂lon(axis=1) - ∂u/∂lat(axis=0), 原实现 v 接 axis1/u 接 axis0(
    即 gradient(v,axis=0)-gradient(u,axis=1)=∂v/∂lat-∂u/∂lon)仍为交叉, 已改为:
    gradient(v,axis=1)-gradient(u,axis=0)。"""
    DEG_M = 111320.0
    return (np.gradient(v850, axis=1) - np.gradient(u850, axis=0)) / DEG_M


def gpi_from(sst: np.ndarray, shear: np.ndarray, rh: np.ndarray,
             vort: np.ndarray) -> np.ndarray:
    """标准 GPI 公式(相对涡度 10^-5 s^-1 单位)。"""
    v850 = np.abs(vort) * 1e5
    gp = (1 + 0.1 * v850) ** 3 * (rh / 50.0) ** 3 * \
        (sst - 26.0) ** 4 * (1 + 0.1 * shear) ** -1
    return np.where(sst > 26.0, np.maximum(gp, 0), 0.0)


# ════════════════════ 建库主流程 ════════════════════

def _modes_for(ym: Tuple[int, int]) -> Dict[str, float]:
    """目标月模态向量(标准化): 由 F4 模态序列或内置占位提供。"""
    from simulator import modes as M
    rows = M.load_mode_series('nino34')
    d = {(y, mo): v for y, mo, v in rows}
    oni = d.get(ym, 0.0) / 0.8
    d2 = {(y, mo): v for y, mo, v in M.load_mode_series('pdo')}
    pdo = d2.get(ym, 0.0) / 1.0
    d3 = {(y, mo): v for y, mo, v in M.load_mode_series('dmi')}
    dmi = d3.get(ym, 0.0) / 0.4
    return {'oni': oni, 'pdo': pdo, 'dmi': dmi}


def build_synthetic_library(years: range = LIB_YEARS, force: bool = False,
                            seed_base: int = 20260802) -> Dict[str, List[int]]:
    """构建合成场库(确定性种子)。真实数据可用时改为 downloader+解析。
    force=False 且某年 9 变量齐全时跳过重建(避免每次运行全量重写 ~1GB)。"""
    built = {'sst': [], 'ohc': [], 'mslp': [], 'shear': [],
             'rh700': [], 'uv_steer': [], 'vort850': [], 'gpi': [], 'ssta': [],
             'u10': [], 'v10': [], 'precip24': [], 't500': [],
             'qv850': [], 'hgt500': [], 'hgt100': [], 'uv200': []}
    if not force and all(
            all(F.load_monthly_field(v, y) is not None for v in built)
            for y in years):
        # 库已完整:直接复用,避免每次运行全量重写 ~1GB 并重算气候态。
        # R4-完整性: fast-path 只检查场文件,可能在"库文件在、气候态缺失"的
        # 半迁移环境静默返回, 使 get_anomaly 全链返回 None。此处补:若任一
        # 非 ssta 气候态缺失则补建(ssta 本身已是距平场, 不入气候态)。
        clim_vars = tuple(v for v in built if v != 'ssta')
        if any(F.load_climatology(v) is None for v in clim_vars):
            print('[library] 场库完整但气候态缺失,补建气候态')
            F.build_climatology(clim_vars, CLIM_BASE)
        for y in years:
            for var in built:
                built[var].append(y)
        return built
    for y in years:
        sst_m, ohc_m, mslp_m, sh_m, rh_m, vo_m, gp_m, sa_m = \
            [np.full((12, F.NLAT, F.NLON), np.nan) for _ in range(8)]
        st_m = np.full((12, 2, F.NLAT, F.NLON), np.nan)   # uv_steer 双分量
        # F10 新增场: 7 个 3D + uv200(4D 双分量, 与 uv_steer 同构)
        u10_m, v10_m, pp_m, t5_m, qv_m, hg5_m, hg_m = \
            [np.full((12, F.NLAT, F.NLON), np.nan) for _ in range(7)]
        u2_m = np.full((12, 2, F.NLAT, F.NLON), np.nan)   # uv200
        for mo in range(1, 13):
            mod = _modes_for((y, mo))
            sst = synth_sst_month((y, mo), seed_base, mod['oni'], mod['pdo'])
            ohc = synth_ohc_month((y, mo), seed_base, mod['oni'])
            mslp = synth_mslp_month((y, mo), seed_base, mod['oni'], mod['pdo'])
            (u850, v850), (u500, v500), (u200, v200) = synth_wind_month((y, mo), seed_base)
            rh = synth_rh700_month((y, mo), seed_base, mod['oni'])
            sh = shear_from_winds(u850, v850, u200, v200)
            uu, vv = steer_from_winds([(u850, v850), (u500, v500), (u200, v200)],
                                      [0.4, 0.35, 0.25])
            vo = vort850_from_winds(u850, v850)
            gp = gpi_from(sst, sh, rh, vo)
            # ── F10 新增场(合成近似)──
            mask = _cyclone_mask(vo, (y, mo))
            u10, v10 = synth_surface_wind_month((y, mo), seed_base, uu, vv)
            pp = synth_precip24_month((y, mo), mask)
            t5 = synth_t500_month(mask)
            qv = synth_qv850_month(rh, sst)
            hg5 = synth_hgt500_month(mslp)
            hg = synth_hgt100_month(mslp)
            u2 = synth_uv200_month((y, mo), u200, v200, vo)
            i = mo - 1
            sst_m[i], ohc_m[i], mslp_m[i], sh_m[i] = sst, ohc, mslp, sh
            rh_m[i], st_m[i], vo_m[i], gp_m[i] = rh, np.stack([uu, vv]), vo, gp
            u10_m[i], v10_m[i], pp_m[i], t5_m[i], qv_m[i], hg5_m[i], hg_m[i] = u10, v10, pp, t5, qv, hg5, hg
            u2_m[i] = u2
        F.save_monthly_field('sst', y, sst_m, meta={'source': 'synthetic',
                                                    'seed_base': seed_base})
        F.save_monthly_field('ohc', y, ohc_m, meta={'source': 'synthetic'})
        F.save_monthly_field('mslp', y, mslp_m, meta={'source': 'synthetic'})
        F.save_monthly_field('shear', y, sh_m, meta={'source': 'synthetic'})
        F.save_monthly_field('rh700', y, rh_m, meta={'source': 'synthetic'})
        F.save_monthly_field('uv_steer', y, st_m, meta={'source': 'synthetic'})
        F.save_monthly_field('vort850', y, vo_m, meta={'source': 'synthetic'})
        F.save_monthly_field('gpi', y, gp_m, meta={'source': 'synthetic'})
        F.save_monthly_field('ssta', y, sst_m, meta={'source': 'synthetic'})
        # F10 新增场落盘(命名即契约, 见 field_io.FIELD_DEFS/RENDER_VARS)
        F.save_monthly_field('u10', y, u10_m, meta={'source': 'synthetic'})
        F.save_monthly_field('v10', y, v10_m, meta={'source': 'synthetic'})
        F.save_monthly_field('precip24', y, pp_m, meta={'source': 'synthetic'})
        F.save_monthly_field('t500', y, t5_m, meta={'source': 'synthetic'})
        F.save_monthly_field('qv850', y, qv_m, meta={'source': 'synthetic'})
        F.save_monthly_field('hgt500', y, hg5_m, meta={'source': 'synthetic'})
        F.save_monthly_field('hgt100', y, hg_m, meta={'source': 'synthetic'})
        F.save_monthly_field('uv200', y, u2_m, meta={'source': 'synthetic'})
        for var in built:
            built[var].append(y)
    # 气候态(1991-2020 基准)
    # D10: ssta 已是距平场, 不参与气候态构建(否则 ssta_clim 存的是原始 SST 气候态,
    # get_anomaly('ssta') 会得 sst-2·clim)
    F.build_climatology(tuple(v for v in built if v != 'ssta'), CLIM_BASE)
    # BUG-8: 删除历史遗留的 ssta_clim.npz(旧版误建), 否则 get_anomaly('ssta')
    # 静默返回错误距平
    try:
        for name in ('ssta_clim.npz', 'ssta_clim.json'):
            p = os.path.normpath(os.path.join(F.CLIM_DIR, name))
            if os.path.exists(p):
                os.remove(p)
                print(f'[library] 已删除过期 {name}')
    except Exception:
        pass
    # ssta = sst - 气候态(重算)
    for y in years:
        sst = F.load_monthly_field('sst', y)
        if sst is None:
            continue
        cl = F.load_climatology('sst')
        if cl is None or cl['clim'] is None:
            break
        F.save_monthly_field('ssta', y, sst - cl['clim'], meta={'source': 'synthetic'})
    return built


def build_real_library(years: range = LIB_YEARS) -> Dict[str, List[int]]:
    """真实数据建库(A1): 逐月下载 ERA5/ORAS5 → 解析 NetCDF → 1° 网格 npz。
    任一变量任一缺失月 → 抛错列出,绝不静默降级合成。"""
    built = {v: [] for v in F.VARS}
    missing = []
    if not downloader.check_credentials():
        raise RuntimeError(
            "真实数据不可用: 未配置 CDS API 凭证(cdsapi)。"
            "A1 语义: 真实库必须完整,缺失即报错,不降级合成。\n"
            "  合成库请显式使用 build_library(use_real=False)。")
    # 风场三层 + 派生(切变/引导/涡度/GPI 由插值后风场计算,不另做诊断)
    # A1 补充: sst/mslp/rh700 同样必须抓取(缺失则真实库不完整)
    for y in years:
        for mo in range(1, 13):
            u850 = _fetch_era5_component('u850', y, mo, missing)
            v850 = _fetch_era5_component('v850', y, mo, missing)
            u500 = _fetch_era5_component('u500', y, mo, missing)
            v500 = _fetch_era5_component('v500', y, mo, missing)
            u300 = _fetch_era5_component('u300', y, mo, missing, optional=True)
            v300 = _fetch_era5_component('v300', y, mo, missing, optional=True)
            u200 = _fetch_era5_component('u200', y, mo, missing)
            v200 = _fetch_era5_component('v200', y, mo, missing)
            sst = _fetch_era5_component('sst', y, mo, missing)
            mslp = _fetch_era5_component('mslp', y, mo, missing)
            rh = _fetch_era5_component('rh700', y, mo, missing)
            if None in (u850, v850, u200, v200, u500, v500, sst, mslp, rh):
                continue
            sh = np.hypot(u200 - u850, v200 - v850)
            uu, vv = steer_from_winds([(u850, v850), (u500, v500),
                                       (u300, v300) if u300 is not None and v300 is not None
                                       else (u500, v500),
                                       (u200, v200)],
                                      [0.40, 0.30, 0.20, 0.10])
            vo = vort850_from_winds(u850, v850)
            gp = gpi_from(sst, sh, rh, vo)
            _store_month('shear', y, mo, sh, built)
            _store_month('uv_steer', y, mo, np.stack([uu, vv]), built)
            _store_month('vort850', y, mo, vo, built)
            _store_month('gpi', y, mo, gp, built)
            _store_month('sst', y, mo, sst, built)
            _store_month('mslp', y, mo, mslp, built)
            _store_month('rh700', y, mo, rh, built)
    # OHC(ORAS5 真实深度积分)
    for y in years:
        for mo in range(1, 13):
            p = downloader.fetch_oras5_month(y, mo)
            if not p:
                missing.append(f"ohc {y}-{mo:02d}")
                continue
            t3d = _parse_oras5_nc(p)
            if t3d is None:
                missing.append(f"ohc {y}-{mo:02d}(解析失败)")
                continue
            _store_month('ohc', y, mo, downloader.ohc_from_temperature(t3d), built)
    # GPI 与 ssta(气候态建后; ssta = sst - 气候态,与合成路径一致)
    F.build_climatology(tuple(v for v in built if v != 'ssta'), CLIM_BASE)
    if missing:
        raise RuntimeError(
            "真实库不完整,以下变量缺失(不降级合成):\n  " + '\n  '.join(missing[:50]))
    for y in years:
        sst = F.load_monthly_field('sst', y)
        if sst is None:
            continue
        cl = F.load_climatology('sst')
        if cl is None or cl['clim'] is None:
            break
        F.save_monthly_field('ssta', y, sst - cl['clim'],
                             meta={'source': 'era5'})
    return built


def _fetch_era5_component(var: str, y: int, mo: int, missing: list,
                          optional: bool = False):
    p = downloader.fetch_era5_month(var, y, mo)
    if not p:
        if not optional:
            missing.append(f"{var} {y}-{mo:02d}")
        return None
    arr = _parse_era5_nc(p, var)
    if arr is None:
        if not optional:
            missing.append(f"{var} {y}-{mo:02d}(解析失败)")
    return arr


def _store_month(var: str, y: int, mo: int, arr, built) -> None:
    # D2: load 返回多元素数组不能直接 or; 显式 None 判断
    months = F.load_monthly_field(var, y)
    if months is None:
        months = np.full(
            (12, 2, F.NLAT, F.NLON) if var == 'uv_steer' else (12, F.NLAT, F.NLON), np.nan)
    if arr is not None:
        months[mo - 1] = arr
    F.save_monthly_field(var, y, months, meta={'source': 'era5' if var != 'ohc' else 'oras5'})
    if y not in built[var]:
        built[var].append(y)


def _parse_era5_nc(path: str, var: str) -> Optional[np.ndarray]:
    """解析 ERA5 NetCDF 月平均 → (NLAT, NLON) 1° 网格。需 netCDF4/xarray。
    D9: 纬度序与 F.LATS(-60→60 升序)对齐(CDS 按 area=[60,0,-60,360] 返回降序)。
    R4: 变量名映射 —— CDS 请求按 `mean_sea_level_pressure` 抓取, 返回 NetCDF 的
    data var 短名为 `msl`(非内部名 `mslp`)。原实现 `ds['mslp']` 不存在时静默
    退回"第一个 data var", 只在 msl 是唯一变量时碰巧选对; 改用显式别名映射
    确定性选变量(仍保留"首个 data var"兜底, 兼容单变量文件)。"""
    # CDS 返回短名 → 本模块请求名; 缺失时回退首个 data var(单变量文件)。
    _ERA5_ALIAS = {'mslp': 'msl'}
    try:
        import xarray as xr
        with xr.open_dataset(path) as ds:
            name = var if var in ds else _ERA5_ALIAS.get(var)
            if name is not None and name in ds:
                da = ds[name]
            else:
                da = list(ds.data_vars.values())[0]
            arr = da.mean(dim='time', skipna=True).values
            arr = np.squeeze(arr)
            # 纬度降序(60→-60)时翻转
            if 'lat' in ds.coords:
                lat0 = float(ds.lat.values[0])
                lat1 = float(ds.lat.values[-1])
                if lat0 > lat1:
                    arr = arr[::-1]
            # 重采样到 121×360(1°)
            from scipy.ndimage import zoom
            target = (F.NLAT, F.NLON)
            arr = zoom(arr, (target[0] / arr.shape[0], target[1] / arr.shape[1]))
            # 单位归一: ERA5 CDS 的 msl 为 Pa, 项目其余路径(合成库/Open-Meteo)
            # 与渲染诊断均按 hPa; 转成 hPa 以免真实库口径错位(A6 报告)。
            if var == 'mslp':
                arr = arr / 100.0
            return arr.astype(np.float32)
    except Exception:
        return None


def _parse_oras5_nc(path: str) -> Optional[np.ndarray]:
    """解析 ORAS5 3D 温度 NetCDF → (nz, 121, 360)。纬度序同 ERA5 处理。"""
    try:
        import xarray as xr
        with xr.open_dataset(path) as ds:
            da = list(ds.data_vars.values())[0]
            arr = da.mean(dim='time', skipna=True).values
            arr = np.squeeze(arr)
            if 'lat' in ds.coords:
                lat0 = float(ds.lat.values[0])
                lat1 = float(ds.lat.values[-1])
                if lat0 > lat1:
                    arr = arr[::-1]
            if arr.ndim == 3:
                from scipy.ndimage import zoom
                nz = arr.shape[0]
                out = np.stack([zoom(arr[i], (121 / arr.shape[1], 360 / arr.shape[2]))
                                for i in range(nz)]).astype(np.float32)
                return out
    except Exception:
        return None
    return None


def build_library(use_real: bool = False, force: bool = False) -> Dict[str, List[int]]:
    """建库入口(A1 语义): use_real=True 时真实库必须完整,缺失即抛错;
    合成库仅作为 use_real=False 的显式选项(元数据 source='synthetic')。
    建库后重标定副高/ITCZ/季风槽参数序列(测试残留的窄范围 npz
    会让 get_param 对架空年月返回 None,云图/ITCZ 图层缺失)。"""
    if use_real:
        result = build_real_library()
    else:
        result = build_synthetic_library(force=force)
    from . import param_fields as PF
    PF.calibrate()
    return result


if __name__ == '__main__':
    built = build_library()
    print('library built:', {k: len(v) for k, v in built.items()})
