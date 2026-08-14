# simulator/typhoons/fields.py
"""A2 合成风场 + 结构诊断(内核 B 判定基础,第二轮修正)。

每 6h 构建台风涡旋场(修正 Rankine,由 vmax/r34 反演)叠加环境背景场
(台风位置 10°×10° 窗口环境风 + mslp 气压场),诊断:
  - 闭合环流中心(风场谷底+环流圈)检测
  - 暖心/冷心(中心 2° vs 周边 6-8° 温度距平;输入=环境温度代理+暖心增量)
  - 锋面与中心相接(真实 |∇T| 梯度带,季节迁移,非纬度硬编码)
  - 双中心风场合并(单槽化)判定
  - 环流可辨识度(并入背景判定)
"""
from __future__ import annotations
import math
from typing import Optional, Tuple

import numpy as np

DEG_KM = 111.0
R_CP = 0.286           # 静力近似 R/cp(干空气)
FRONT_GRAD = 0.025     # 锋面梯度阈值 °C/km(2.5°C/100km;高于台风自身凹陷梯度)


class VortexField:
    """局部合成风场(窗口 W°×W°,分辨率 res°)。"""

    def __init__(self, clat: float, clon: float, half_w: int = 12, res: float = 1.0):
        self.clat, self.clon = clat, clon % 360.0
        self.half_w = half_w
        self.res = res
        n = int(2 * half_w / res) + 1
        self.lat = np.linspace(clat - half_w, clat + half_w, n)
        self.lon = np.linspace(clon - half_w, clon + half_w, n)
        self.LAT, self.LON = np.meshgrid(self.lat, self.lon, indexing='ij')
        dlon = (self.LON - self.clon + 180.0) % 360.0 - 180.0
        self.D = np.hypot(self.LAT - self.clat, dlon) * DEG_KM   # km

    # ── 涡旋构建(修正 Rankine,任意中心)──

    def vortex_at(self, clat: float, clon: float, vmax_kt: float, r34_km: float,
                  rmax_km: float = 40.0) -> Tuple[np.ndarray, np.ndarray]:
        """修正 Rankine 风廓线(中心在 (clat, clon))→ (u, v) 分量。"""
        dlon = (self.LON - clon + 180.0) % 360.0 - 180.0
        r = np.hypot(self.LAT - clat, dlon) * DEG_KM
        r34 = max(50.0, r34_km)
        rmax = max(20.0, rmax_km)
        v = np.zeros_like(r)
        inner = r <= rmax
        v[inner] = vmax_kt * (r[inner] / rmax)
        outer = ~inner
        a = math.log(34.0 / vmax_kt) / math.log(rmax / r34) if vmax_kt > 34 else 0.55
        v[outer] = vmax_kt * (rmax / r[outer]) ** a
        # 方向用已回卷的 dlon(任意 clon 跨反经线 0°/360° 时,
        # 直接 LON-clon 会得到虚假大经度差 → 切向风分量方向错乱。
        # dlon 已统一为 [-180,180) 最短经度差, 与距离计算口径一致。)
        ang = np.arctan2(self.LAT - clat, dlon)
        u = -v * np.sin(ang)
        vv = v * np.cos(ang)
        return u, vv

    def vortex(self, vmax_kt: float, r34_km: float,
               rmax_km: float = 40.0) -> Tuple[np.ndarray, np.ndarray]:
        return self.vortex_at(self.clat, self.clon, vmax_kt, r34_km, rmax_km)

    def pressure_depression(self, vmax_kt: float, oci: float) -> np.ndarray:
        """梯度风平衡气压凹陷(简化): 中心 Δp≈0.35·Vmax,按风廓线平滑衰减。"""
        r = self.D
        vmax_ms = vmax_kt * 0.514444
        rmax = 40.0
        v = np.zeros_like(r)
        inner = r <= rmax
        v[inner] = vmax_ms * (r[inner] / rmax)
        a = 0.40
        outer = ~inner
        v[outer] = vmax_ms * (rmax / np.maximum(r[outer], 1.0)) ** a
        dp = 0.35 * vmax_kt * (v / max(vmax_ms, 1e-6)) ** 2 * np.exp(-r / (rmax * 6.0))
        return oci - dp

    # ── 温度代理(A2-3: 环境静力温度 + 暖心增量,非气压伪温度)──

    def temperature(self, mslp_env: Optional[float], month: int,
                    vmax_kt: float, tropical: bool) -> np.ndarray:
        """850hPa 温度代理(K): 静力近似 T ≈ T0(lat)·(1 - R/cp·ln(p/1000))
        + 季节迁移极地锋面带(30°N + 6·cos(季节)) + 暖心增量(热带且强时 ∝Vmax)。"""
        lat = self.LAT
        T0 = 302.0 - 0.35 * np.abs(lat - 8.0)
        oci = mslp_env if mslp_env else 1008.0
        p_local = self.pressure_depression(vmax_kt, oci)
        t = T0 * (1.0 - R_CP * np.log(np.maximum(p_local, 850.0) / 1000.0))
        # 极地锋面带(随季节迁移): 冬(2-3月)~30°N → 夏(8-9月)~42°N
        # E9: 南半球做镜像(南半球台风也可斜压化触发 EX)。
        #   仅取负号只镜像纬度,不镜像季节相位: 北半球冬(赤道侧 30°N)↔南半球夏(应极侧),
        #   直接取负会把 2 月(南半球夏)推到 -30°(仍偏赤道)、8 月(南半球冬)推到 -41°(偏极),
        #   方向恰恰相反。须先对季节相位移 6 个月(month+3)再取负:
        #   2 月(南半球夏)→ -41°(偏极), 8 月(南半球冬)→ -30°(偏赤道)。
        front_lat = 36.0 - 6.0 * math.cos(2 * math.pi * (month - 3) / 12.0)
        if self.clat < 0:
            front_lat = -(36.0 - 6.0 * math.cos(2 * math.pi * (month + 3) / 12.0))
        d_fr = (lat - front_lat) / 1.2
        t = t - 8.0 * np.exp(-(d_fr ** 2))
        # 暖心增量(热带性质且 vmax≥55kt,眼墙 200km 内 ∝ Vmax)
        if tropical and vmax_kt >= 55:
            t = t + 0.03 * vmax_kt * np.exp(-(self.D / 200.0) ** 2)
        return t

    # ── 结构诊断 ──

    def find_center(self, u: np.ndarray, v: np.ndarray,
                    p_field: Optional[np.ndarray] = None) -> Optional[dict]:
        """闭合环流中心检测(基于风场): 眼中心 = 风速谷底;
        closed = 中心 100-400km 环带存在强环流(峰值风速 ≥ 中心风速×3)。"""
        sp = np.hypot(u, v)
        iy, ix = np.unravel_index(np.argmin(sp), sp.shape)
        v_eye = float(sp[iy, ix])
        ring = (self.D > 100.0) & (self.D <= 400.0)
        v_ring = float(np.nanmax(sp[ring])) if ring.sum() > 0 else 0.0
        closed = v_ring >= max(15.0, v_eye * 3.0)
        out = {'la': float(self.lat[iy]), 'lo': float(self.lon[ix]),
               'v_eye': v_eye, 'v_ring': v_ring, 'closed': closed}
        if p_field is not None:
            out['pmin'] = float(np.nanmin(np.nan_to_num(p_field)))
        return out

    def warm_core(self, t_field: np.ndarray) -> float:
        """暖心/冷心(简化 Hart): 中心 2° 内温度距平 vs 周边 6-8° 带。"""
        core = self.D <= 222.0
        ring = (self.D > 666.0) & (self.D <= 888.0)
        if core.sum() == 0 or ring.sum() == 0:
            return 0.0
        tc = float(np.nanmean(np.nan_to_num(t_field[core])))
        tr = float(np.nanmean(np.nan_to_num(t_field[ring])))
        return tc - tr

    def front_contact(self, t_field: np.ndarray, radius_km: float = 200.0) -> bool:
        """锋面接入: 中心半径内是否存在真实温度梯度带(|∇T| > 2.5°C/100km)。
        梯度换算物理单位(像素 = res°)。"""
        g = np.gradient(np.nan_to_num(t_field), axis=(0, 1))
        grad = np.hypot(g[0], g[1]) / (DEG_KM * self.res)      # °C/km
        core = self.D <= radius_km
        if core.sum() == 0:
            return False
        return bool(float(np.nanmax(grad[core])) > FRONT_GRAD)

    def identifiable(self, vortex_max: float, env_max: float) -> bool:
        """环流可辨识度: 涡旋峰速显著高于背景(≥20kt 或 1.5×背景)才独立存在。"""
        if env_max <= 0:
            return vortex_max > 20.0
        return vortex_max > max(20.0, 1.5 * env_max)


def build_synthetic_field(sim, api) -> dict:
    """从台风模拟状态构建合成风场与诊断(供判定接线)。
    A2-4: 环境背景风取台风中心 10°×10° 窗口(排除中心 2° 涡旋区)均值。"""
    vf = VortexField(sim.la, sim.lo)
    u_v, v_v = vf.vortex(sim.vmax, sim.r34 * 1.852)
    # 环境背景风: 中心窗口均值(排除中心 2° 涡旋区)
    uv = api.get_field('uv_steer', (sim.t.year, sim.t.month, sim.t.day)) if api else None
    env_u = env_v = 0.0
    if uv is not None:
        la_i = int(np.clip(sim.la + 60.0, 0, 120))
        lo_i = int(sim.lo % 360)
        uu = np.roll(uv[0], -lo_i, axis=1)
        vv = np.roll(uv[1], -lo_i, axis=1)
        la0, la1 = max(0, la_i - 5), min(120, la_i + 6)
        # E5(+)E12: 经度取环绕中心 ±5°,与纬度窗口(la_i±5, 共 11 行)对称。
        #   回卷后 lo_i 位于列 0: 取循环列 -5..+5 → 左半[-5:](西侧 5 列)+ 右半[:6](中心+东侧 5 列)。
        #   旧实现 [-11:]+[:11] 为 22 列(~21°)且西 11/东 11 不对称, 与注释"±5°/10°×10°"不符。
        win_u = np.concatenate([uu[la0:la1, -5:], uu[la0:la1, :6]], axis=1).copy()
        win_v = np.concatenate([vv[la0:la1, -5:], vv[la0:la1, :6]], axis=1).copy()
        # 排除中心 2°(la_i-1..la_i+1, lo 中心±2),边缘时钳制避免负索引
        c_la = la_i - la0
        r0, r1 = max(0, c_la - 1), min(win_u.shape[0], c_la + 2)
        # 拼接后本地列 5 为中心(lo_i); 排除本地列 3..7(中心±2 附近)
        win_u[r0:r1, 3:8] = np.nan
        win_v[r0:r1, 3:8] = np.nan
        # 窗口全为 NaN(如 uv 缺失/边缘)时保持 env 为 0(与 uv 缺失时一致)
        if np.isfinite(win_u).any():
            env_u = float(np.nanmean(win_u)) * 1.944
        if np.isfinite(win_v).any():
            env_v = float(np.nanmean(win_v)) * 1.944
    u = u_v + env_u
    v = v_v + env_v
    # 气压场与温度代理
    mslp = api.get_field('mslp', (sim.t.year, sim.t.month, sim.t.day)) if api else None
    oci = 1008.0
    if mslp is not None:
        la_i = int(np.clip((sim.la + 60.0), 0, 120))
        lo_i = int(sim.lo % 360)
        oci = float(mslp[la_i, lo_i]) if not np.isnan(mslp[la_i, lo_i]) else 1008.0
    p = vf.pressure_depression(sim.vmax, oci)
    tropical = sim.nature not in ('EX', 'SS', 'SD')
    t = vf.temperature(oci, sim.t.month, sim.vmax, tropical)
    center = vf.find_center(u, v, p)
    warm = vf.warm_core(t)
    front = vf.front_contact(t)
    env_max = math.hypot(env_u, env_v)
    ident = vf.identifiable(sim.vmax, env_max)
    return {'u': u, 'v': v, 'p': p, 't': t, 'center': center,
            'warm': warm, 'front': front, 'ident': ident, 'env_max': env_max,
            'env_u': env_u, 'env_v': env_v}


def fields_merged(api, a, b) -> bool:
    """A2-2: 两系统风场是否已合并为一个环流中心(即使槽状)。
    判据: 中点 1° 邻域均风速 < 两中心峰值风速的 35%(单槽形态)。
    E4: 中点经度跨反经线回卷(0°/360° 两侧)。"""
    d = (b.lo - a.lo + 180.0) % 360.0 - 180.0     # 最短经度差 [-180,180)
    mid_lo = (a.lo + d / 2.0) % 360.0
    mid_la = (a.la + b.la) / 2
    vf = VortexField(mid_la, mid_lo, half_w=8, res=0.5)
    ua, va = vf.vortex_at(a.la, a.lo, a.vmax, a.r34 * 1.852)
    ub, vb = vf.vortex_at(b.la, b.lo, b.vmax, b.r34 * 1.852)
    u = ua + ub
    v = va + vb
    sp = np.hypot(u, v)
    v_peak = float(np.nanmax(sp))
    if v_peak < 20.0:
        return False
    dlon = (vf.LON - mid_lo + 180.0) % 360.0 - 180.0
    mid = np.hypot(vf.LAT - mid_la, dlon) <= 111.0   # 中点 1°
    v_mid = float(np.nanmean(sp[mid])) if mid.sum() > 0 else v_peak
    return v_mid < 0.35 * v_peak
