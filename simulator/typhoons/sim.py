# simulator/typhoons/sim.py
"""F8 台风模拟层: 逐 6h 状态机(路径/强度/气压/置换/生命周期),输出标准 .dat。

路径: 引导气流(uv_steer 双线性采样) + β漂移 + AR(1) 红噪声 + 陆地衰减(高程 tif)。
强度: PI(SST/OHC 驱动型,按环流尺度 S) → dV/dt 松弛 + 切变/干空气抑制。
气压: KZC 风压 + 雨带修正 + 置换期解耦 + 巅峰滞后。
置换: IDLE→FORMING→DEV→(CUTOFF|MERGE)→CONTRACT→IDLE;FAILED/REBUILD。
"""
from __future__ import annotations
import math
import os
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import basin as B
from . import kzc

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SIM_DIR, 'output')

# 高程 tif(60 弧秒,降采样 5 弧分 ≈ 9km);缺失时降级用回放程序陆地掩码
ELEV_TIF = r'C:\Users\Yukioto\Pictures\world_elevation_60arcsec_21600x10800.tif'
LAND_MASK = os.path.normpath(os.path.join(SIM_DIR, '..', '..', 'map', 'land.png'))
_ELEV = None          # (2160, 4320) int16 降采样
_ELEV_PATH = None


def load_elevation(force: bool = False) -> Optional[np.ndarray]:
    """一次性加载并降采样高程(5 弧分,4320×2160,int16 ≈ 19MB)。
    tif 缺失时用 map/land.png 陆地掩码(陆地取 300m 高程代理,海岸线/陆地衰减仍可用)。"""
    global _ELEV, _ELEV_PATH
    if _ELEV is not None and not force:
        return _ELEV
    src = ELEV_TIF if os.path.exists(ELEV_TIF) else LAND_MASK
    if not os.path.exists(src):
        return None
    try:
        from PIL import Image
        img = Image.open(src)
        if src == LAND_MASK:
            # 陆地掩码: alpha=255 陆地 → 300m 代理;降采样到 4320×2160
            small = img.resize((4320, 2160), Image.LANCZOS)
            a = np.asarray(small.convert('RGBA'), dtype=np.float32)[..., 3]
            _ELEV = np.where(a >= 128.0, 300.0, 0.0).astype(np.int16)
        else:
            if img.size != (21600, 10800):
                img = img.resize((21600, 10800), Image.LANCZOS)
            small = img.resize((4320, 2160), Image.LANCZOS)
            _ELEV = np.asarray(small, dtype=np.int16)
        _ELEV_PATH = src
    except Exception:
        _ELEV = None
    return _ELEV


def elevation_at(la: float, lo: float) -> float:
    """双线性采样海拔(m)。"""
    e = load_elevation()
    if e is None:
        return 0.0
    x = (lo % 360.0) / 360.0 * 4320.0
    y = (90.0 - la) / 180.0 * 2160.0
    x0, y0 = int(x), int(y)
    x0 = min(max(x0, 0), 4319)
    y0 = min(max(y0, 0), 2159)
    fx, fy = x - x0, y - y0
    x1, y1 = (x0 + 1) % 4320, min(y0 + 1, 2159)
    v = (e[y0, x0] * (1 - fx) * (1 - fy) + e[y0, x1] * fx * (1 - fy)
         + e[y1, x0] * (1 - fx) * fy + e[y1, x1] * fx * fy)
    return float(v)


def land_decay_factor(alt: float) -> float:
    """分段线性连续衰减系数(用户规则)。"""
    if alt <= 0:
        return 1.0
    if alt <= 200:
        return 1.00 - (1.00 - 0.82) * (alt / 200.0)
    if alt <= 800:
        return 0.82 - (0.82 - 0.55) * ((alt - 200) / 600.0)
    return 0.55 - (0.55 - 0.30) * min(1.0, (alt - 800) / 400.0)


# 置换状态
EW_IDLE, EW_FORMING, EW_DEV, EW_CUTOFF, EW_MERGE, EW_CONTRACT = range(6)
EW_FAILED, EW_REBUILD = 6, 7


class TyphoonSim:
    """单台风 6h 模拟。"""

    def __init__(self, rec: dict, api, seed: int, basin_no: int):
        self.rec = rec
        self.api = api
        self.rng = np.random.RandomState(seed % (2**32))
        self.basin = rec['basin']
        self.no = basin_no
        self.uid = None              # H2: 全局唯一 id(simulate_records 赋值)

        self.t = datetime.strptime(rec['t0'], '%Y%m%d%H')
        self.la, self.lo = rec['la0'], rec['lo0']
        self.vmax = rec['w0']
        self.mslp = rec['p0']
        self.r34 = 45.0
        self.r50 = 25.0
        self.r64 = 12.0
        self.oci = 1008.0
        self.S = 0.3                       # 环流尺度因子(0-1)
        self.nb = 2                        # 雨带数
        self.stage = '生成'
        self.states: List[dict] = []
        self.ew_state = EW_IDLE
        self.ew_type = None                # 'normal' / 'merge'
        self.ew_since: Optional[datetime] = None
        self.ew_duration = 0
        self.ew_peak = 0.0
        self.ew_events: List[dict] = []
        self.ew_count = 0
        self.pmin_lag = timedelta(hours=float(self.rng.choice((3, 6, 12))))
        self._peak_t = self.t          # M3: 记录峰值时间戳(巅峰滞后用)
        self.vmax_peak = self.vmax
        self._weak_since = None
        self._red = 0.0
        self._prev_v = self.vmax
        self.dissip = None
        self.dissip_reason = None
        # 需求2: 支持以 EX/SS 性质起始(坐标 SST<25°C 时), 自然演化不强行转 TC
        self.nature = rec.get('nature', 'TD')
        self.ex_since: Optional[datetime] = None
        self._land_alt = 0.0
        self._diag = None                  # 结构诊断缓存(A2)
        self.mu = 0.0                      # 移速(kt, 东/北), 显示外推/尾流用
        self.mv = 0.0
        self._ri_cd = 0.0                  # RI 冷却期(h)
        self._ri_t = 0.0                   # RI 进行中剩余(h)
        self._ri_done = False

    # ── 环境采样 ──

    def _field(self, var: str):
        try:
            return self.api.get_field(var, (self.t.year, self.t.month, self.t.day))
        except Exception:
            return None

    def _sample(self, fld, la, lo):
        """统一双线性采样(B5)。"""
        return self._bilinear(fld, la, lo)

    def _sst_offset(self, la: float, lo: float) -> float:
        """冷尾流 SST 修正钩子(°C)。应用层(sim_mode_mixin)可覆盖为尾流网格采样。"""
        fn = getattr(self, 'sst_offset_fn', None)
        if fn is not None:
            try:
                v = fn(la, lo)
                return float(v) if v == v else 0.0
            except Exception:
                return 0.0
        return 0.0

    def _steer_ring(self, la, lo):
        """环带平均引导气流(参考 SimCore steeringAt): 距中心 300-600km 环带 6 点
        深层引导平均, 陆地权重剔除(陆地格点引导不可信), 陆地包围时退回单点。

        uv_steer 已是 850/200 加权深层引导场(库生成), 此处只做空间平均,
        避免单点采样被台风自身环流/小尺度噪声污染导致路径抖动。"""
        uv = self._field('uv_steer')
        if uv is None:
            return 0.0, 0.0
        su = sv = wsum = 0.0
        for k in range(6):
            a = k * math.pi / 3 + 0.5
            r = 340.0 + (k % 2) * 180.0          # 340/520 km 交替
            dlo = r / 111.32 / max(1e-6, math.cos(math.radians(la))) * math.cos(a)
            dla = r / 110.57 * math.sin(a)
            tla, tlo = la + dla, (lo + dlo) % 360.0
            if not (-60.0 <= tla <= 60.0):
                continue
            # 海面权重(高程 ≤300m 视为海面)
            alt = elevation_at(tla, tlo)
            wl = 1.0 - min(1.0, max(0.0, alt) / 300.0)
            if wl < 0.3:
                continue
            u = self._bilinear(uv[0], tla, tlo)
            v = self._bilinear(uv[1], tla, tlo)
            if u is None or v is None:
                continue
            su += u * 1.944 * wl                # m/s → kt
            sv += v * 1.944 * wl
            wsum += wl
        if wsum < 0.5:
            # 陆地包围: 退回近场单点
            u = self._bilinear(uv[0], la, lo)
            v = self._bilinear(uv[1], la, lo)
            return (u * 1.944 if u is not None else 0.0), \
                   (v * 1.944 if v is not None else 0.0)
        return su / wsum, sv / wsum

    def _bilinear(self, fld, la, lo):
        """双线性插值。E1: 行/列数取实际网格(默认 121×360),NaN 返回 None。
        E13: 经度维数取 fld.shape[1] 而非硬编码 360(与 basin._sample_bilinear
        口径一致),避免某字段并非标准 1°×360 列时越界/错位采样。"""
        if fld is None:
            return None
        h, w = fld.shape[0], fld.shape[1]
        x = (lo % 360.0) / 360.0 * w
        y = (la + 60.0) / 120.0 * (h - 1)
        x0, y0 = int(x), int(y)
        x0 = min(max(x0, 0), w - 1)
        y0 = min(max(y0, 0), h - 2)
        fx, fy = x - x0, y - y0
        x1, y1 = (x0 + 1) % w, y0 + 1
        v = (fld[y0, x0] * (1 - fx) * (1 - fy) + fld[y0, x1] * fx * (1 - fy)
             + fld[y1, x0] * (1 - fx) * fy + fld[y1, x1] * fx * fy)
        if v != v:      # NaN
            return None
        return float(v)

    # ── PI(强度上限, SST/OHC 驱动型)──

    def _pi(self) -> float:
        sst = self._sample(self._field('sst'), self.la, self.lo)
        if sst is None:
            sst = 28.5
        # 冷尾流修正(参考 SimCore: 台风自身翻涌的冷却水会抑制后续增强/维持)
        sst -= self._sst_offset(self.la, self.lo)
        sst_eff = sst
        self._teq = None
        if self.S > 0.55:
            # OHC 驱动型: 等效水温由 OHC 反算(可低于 SST → 白海豚式冷崩)
            ohc = self._sample(self._field('ohc'), self.la, self.lo)
            if ohc is not None:
                teq = 26.0 + (ohc - 60.0) / 18.0     # OHC 60-140 kJ/cm² → 26-30.4°C
                self._teq = teq
                sst_eff = (1 - self.S) * sst + self.S * teq
        # Emanuel 简化 PI: 40 + 24·(SST-26.5) kt
        # E6: 原 8kt/°C 使 PI 上限 ~96kt, 眼壁置换(≥100kt)/ST(≥130kt)永远不可达;
        # 24kt/°C 下 31°C 开水 → ~148kt, 28.5°C → ~88kt, 与实况量级一致
        pi = 40.0 + 24.0 * (sst_eff - 26.5)
        return max(30.0, pi)

    # ── 6h 步进 ──

    def step(self) -> None:
        # 1. 路径: 环带平均引导气流 + β漂移 + 双轴红噪声
        u, v = self._steer_ring(self.la, self.lo)
        # β漂移(向赤道侧偏西 + 向极)
        beta_lat = 2.5 + 1.5 * math.exp(-((abs(self.la) - 20) / 8.0) ** 2)
        # 东西分量: 南北半球均偏西(赤道侧偏西是固定地理方向,β项无半球号)
        u_beta = -beta_lat
        # 向极分量: 北半球向北(+)南半球向南(-)
        v_beta = (1.0 + 0.5 * math.exp(-((abs(self.la) - 20) / 8.0) ** 2)) \
            * (1 if self.la >= 0 else -1)
        # 红噪声扰动(AR(1), 东西/南北双轴; 参考 SimCore: 噪声应同时作用于两分量,
        # 且幅度小于引导项, 否则路径呈锯齿状抖动)
        self._red = getattr(self, '_red', 0.0)
        self._red_v = getattr(self, '_red_v', 0.0)
        self._red = 0.8 * self._red + self.rng.normal(0, 0.35)
        self._red_v = 0.8 * self._red_v + self.rng.normal(0, 0.35)
        # H1: u/v 单位为 kt(海里/小时); 6h 位移海里 → 度: 除以 60 海里/度
        dlon = (u + u_beta + self._red) * 6.0 / (60.0 * math.cos(math.radians(self.la)) + 1e-6)
        dlat = (v + v_beta + self._red_v) * 6.0 / 60.0
        self.mu, self.mv = u + u_beta + self._red, v + v_beta + self._red_v   # 移速(kt)
        self.lo = (self.lo + dlon) % 360.0
        self.la = max(-60.0, min(60.0, self.la + dlat))

        # 2. 强度演化
        pi = self._pi()
        shear = self._sample(self._field('shear'), self.la, self.lo)
        shear = shear * 1.944 if shear is not None else 5.0
        rh = self._sample(self._field('rh700'), self.la, self.lo)
        pi_eff = pi * (1.0 - 0.45 * min(1.0, max(0.0, shear - 8.0) / 12.0))
        if rh is not None and rh < 50:
            pi_eff -= 5.0 * (50.0 - rh) / 50.0
        # M2: 云图结构参数 — 切变方向(引导流方向近似,弧度) + 冷崩标记(OHC 亏空)
        self._shear_angle = math.atan2(v * 0.514444, u * 0.514444) if (u or v) else 0.0
        teq = getattr(self, '_teq', None)
        sst_cur = self._sample(self._field('sst'), self.la, self.lo) or 28.5
        self._cold_collapse = bool(self.S > 0.55 and teq is not None
                                   and teq < sst_cur - 2.5 and self.vmax >= 90)
        # 峰值后结构弱化: 从峰值回落 3kt 且当前 PI 已不足以维持强度时,
        # 累积衰退进程,增强效率持续下降,系统自然下沉直至并入背景
        # (无寿命上限,消散由模拟自然发生);环境恢复(PI 重新高于 vmax,
        # 暖水/入海/斜压)时结构重建,衰退进程快速消退 → 再增强不被累计衰减锁死。
        # 修复: 原实现仅以"回到峰值-3kt 以上"为唯一恢复条件,而衰减抑制了
        # pi_eff 使 vmax 永远回不到峰值-3 → 累计衰退无解,即便环境转好也
        # 永久塌到 5% 上限,真实再增强(先减弱后暖水增强)不可能发生。
        decay = getattr(self, '_decay', 0.0)
        below = self.vmax_peak >= 55 and self.vmax <= self.vmax_peak - 3
        if below and pi_eff <= self.vmax:
            # 峰值后且环境已不足以维持当前强度: 结构持续衰退(累积)
            decay += 0.5
            pi_eff *= max(0.05, 1.0 - 0.03 * decay)
        else:
            # 环境恢复(pi_eff > vmax): 结构重建, 衰退进程消退 → 允许再增强
            decay = max(0.0, decay - 0.5)
        self._decay = decay
        tau = 36.0 if pi_eff > self.vmax else 18.0
        dv = (pi_eff - self.vmax) / tau * 6.0
        # RI 快速增强(参考 SimCore, Kaplan-DeMaria 2003 判据):
        # SST≥26.5、切变<7.5kt、中层高湿、PI 余量>12kt 时, 每 6h 步 ~10% 概率
        # 触发 12-36h 增强事件(事件期每步额外 +2.2kt, 事件间冷却期 60-160h)。
        self._ri_cd = getattr(self, '_ri_cd', 0.0) - 6.0
        ri_t = getattr(self, '_ri_t', 0.0)
        if ri_t > 0:
            dv += 2.2
            ri_t -= 6.0
            self._ri_t = ri_t
        elif (not getattr(self, '_ri_done', False) and self._ri_cd <= 0
                and 35 <= self.vmax <= 110
                and pi_eff - self.vmax > 12.0
                and sst_cur >= 26.5 and shear <= 7.5
                and (rh is None or rh > 62)):
            if self.rng.random() < 0.10:
                self._ri_t = float(self.rng.randint(2, 6)) * 6.0   # 持续 12-36h
                self._ri_cd = float(self.rng.randint(60, 160))     # 冷却期
                self._ri_done = True
        # 日变化(参考 SimCore): 当地 03 时对流峰值, 强度变化 ±5%
        lh = (self.t.hour + self.lo / 15.0) % 24.0
        dv *= 1.0 + 0.05 * math.cos(2.0 * math.pi * (lh - 3.0) / 24.0)
        # 增强率分布约束(基准 6h 变化率 90 分位 ≈ 15kt)
        dv = max(-20.0, min(20.0, dv))
        self.vmax += dv

        # 巅峰后小幅波动(峰值维持概率低,自然回落由强度方程与结构演化主导)
        if (self.vmax >= 0.9 * self.vmax_peak
                and self.rng.random() < 0.15):
            self.vmax -= self.rng.uniform(1.0, 3.0)
        # M3: 峰值时刻追踪(新高峰时刷新)
        if self.vmax > self.vmax_peak:
            self._peak_t = self.t

        # 3. 陆地衰减(高程驱动) + 棕海效应(Luana 式: 陆上可短暂增强/开眼)
        # R3-1: 衰减确定性应用(原 12% 概率触发使跨菲律宾等陆地几乎不减弱)
        alt = elevation_at(self.la, self.lo)
        self._land_alt = alt
        if alt > 0:
            f = land_decay_factor(alt)
            if self.rng.random() < 0.15:
                # 棕海效应: 湿润陆面蒸发维持强度,短暂增强(幅度收窄)
                self.vmax = min(self.vmax_peak + 5, self.vmax + self.rng.uniform(0.5, 2.0))
            else:
                self.vmax = max(20.0, self.vmax * (1.0 - (1.0 - f) * 0.8))
            self.stage = '减弱(陆地)' if f < 0.9 else self.stage

        # 4. 结构参数
        self._update_structure(pi)

        # 5. 气压(EX/SS/SD 风压解耦;热带段 KZC + 雨带修正 + 置换解耦 + 滞后)
        self._update_pressure(pi)

        # 6. 置换状态机
        self._update_ew(pi)

        # 7. 结构诊断(合成风场) + 生命周期(用户规则: 无寿命上限,消散由模拟自然发生)
        self._diag = None
        self._diagnose()
        self.vmax_peak = max(self.vmax_peak, self.vmax)
        self._update_nature()
        self._check_dissipation()
        if self.dissip is not None:
            return

        # 8. 阶段更新
        self._update_stage()

        self.states.append(self._snapshot())
        self.t += timedelta(hours=6)

    def _update_structure(self, pi: float) -> None:
        self.S = max(0.0, min(1.0, 0.3 + 0.02 * (self.r34 - 40)))
        self.r34 = 35 + 0.55 * self.vmax + self.S * 30
        self.r50 = max(10, self.r34 * 0.6)
        self.r64 = max(5, self.r34 * 0.35)
        # 雨带数随强度
        want = 2 + int(self.vmax / 40)
        if self.ew_state in (EW_FORMING, EW_DEV, EW_CUTOFF):
            want = max(1, want - 1)
        self.nb = want

    def _update_pressure(self, pi: float) -> None:
        if self.nature in ('EX', 'SS', 'SD'):
            # EX/SS/SD 风压解耦: 从 miwu EX 段 (w,p) 约束域抽样,禁用热带公式
            lo, hi = B.ex_mslp_bounds(int(round(self.vmax)))
            if getattr(self, '_ex_p_low', None) is None:
                self._ex_p_low, self._ex_p_high = lo, hi
            target = self.rng.uniform(self._ex_p_low, self._ex_p_high)
            self.mslp += (target - self.mslp) * 0.3      # 平滑演化
            self.mslp = max(920.0, min(1016.0, self.mslp))
            return
        self.oci = 1008.0
        if self.ew_state in (EW_CUTOFF, EW_MERGE, EW_FAILED):
            # 置换期解耦: 气压持平或微升
            self.mslp += self.rng.uniform(-1.0, 3.0)
        else:
            p = kzc.min_pressure(self.vmax, self.r34, self.oci, self.la)
            # 雨带修正: 雨带多气压更低
            p -= 1.5 * (self.nb - 2)
            # 巅峰滞后: mslp 谷底滞后 vmax 峰值(从峰值时刻起计时,M3)
            if self.t - self._peak_t < self.pmin_lag and self.vmax >= self.vmax_peak - 2:
                p += 2.0
            self.mslp = p
        self.mslp = max(880.0, min(1012.0, self.mslp))

    def _update_nature(self) -> None:
        """性质转换(结构诊断驱动,A2): 热带性质按当前强度标定(TD→TS→TY→ST,
        双向,与 nature_code 口径一致);锋面接入中心 或 暖心转冷心(斜压化)→ EX;
        EX 后暖芯恢复 + 低纬可重新热带化。"""
        diag = self._diag or self._diagnose()
        w = int(round(self.vmax))
        if self.nature in ('EX', 'SS', 'SD'):
            # EX 后可重新热带化(暖芯恢复 + 环境转暖)
            if diag['warm'] > 0.8 and abs(self.la) < 25:
                self.nature = B.nature_code(self.basin, w)
                self.ex_since = None
            return
        # 热带性质: 按当前强度标定(增强升级,减弱降级;性质可多次切换)
        self.nature = B.nature_code(self.basin, w)
        # 锋面接入 或 暖心转为冷心(斜压化) → EX
        if diag['front'] or diag['warm'] < -0.3:
            self.nature = 'EX'
            self.ex_since = self.t
            # 重新标定 EX 风压约束带(若 EX→热带→EX 多次切换,旧带基于旧强度已失真)
            self._ex_p_low = self._ex_p_high = None

    def _check_dissipation(self) -> None:
        """自然消散判定(A2,结构诊断驱动):
        环流中心消失/不可辨识 → 并入背景;WV/LO 合法形态、暖心+风场完整不判消散。"""
        diag = self._diag or self._diagnose()
        center = diag['center']
        # a. 风场并入背景: 中心环流不可辨识(涡旋峰速 < 0.6×背景风)
        if not diag['ident'] and self._weak_since is None:
            self._weak_since = self.t
        elif diag['ident']:
            self._weak_since = None
        if self._weak_since and (self.t - self._weak_since) >= timedelta(hours=24):
            self.dissip = self.t
            self.dissip_reason = '并入背景风场'
            return
        # b. WV/LO 合法形态: 环流呈波状/槽状(中心未闭合)且可辨识 → 继续演化
        # c. 暖心 + 风场完整: 不判消散(由强度方程自然表现)
        # e. 陆地棕海效应: 不判消散(已在上方强度演化处理)

    def _diagnose(self) -> dict:
        """构建合成风场并运行结构诊断(每 6h 一次)。"""
        from . import fields as FF
        diag = FF.build_synthetic_field(self, self.api)
        self._diag = diag
        return diag

    def absorb(self, other: 'TyphoonSim') -> None:
        """吞并: 弱方被本系统吞并(记录原因)。"""
        self.vmax = min(self.vmax_peak + 15, self.vmax + 2.0)
        other.dissip = other.t
        other.dissip_reason = f'被 {self.rec["id"]} 吞并'
        # M6: 补最终快照(与自然消散一致), 弱方轨迹终点 = 合并时刻
        other.states.append(other._snapshot())

    def _update_ew(self, pi: float) -> None:
        """眼壁置换状态机(普通/融合/失败/重爆)。
        M7: 各阶段进入时重置 ew_duration,使阶段时长阈值(6/12/_ew_target/
        36)为"阶段内"时长,而非跨阶段累计计数(否则 MERGE/CONTRACT 仅 6h)"""
        if self.ew_state == EW_IDLE:
            since_last = 999.0
            if self.ew_events:
                end_dt = datetime.strptime(self.ew_events[-1]['t'], '%Y%m%d%H')
                since_last = (self.t - end_dt).total_seconds() / 3600.0
            if (self.vmax >= 100 and self.ew_count < 3
                    and (not self.ew_events or since_last >= 36)):
                self.ew_state = EW_FORMING
                self.ew_type = 'merge' if self.rng.random() < 0.4 else 'normal'
                self.ew_since = self.t
                self.ew_peak = self.vmax
                self.ew_duration = 0
                # M4: 进入状态时固定目标时长(避免每步重掷随机阈值)
                self._ew_target = int(self.rng.randint(9, 36))
        elif self.ew_state == EW_FORMING:
            self.ew_duration += 6
            if self.ew_duration >= 6:
                self.ew_state = EW_DEV
                self.ew_duration = 0
        elif self.ew_state == EW_DEV:
            self.ew_duration += 6
            if self.ew_duration >= 6:
                # M4: 进入 CUTOFF/MERGE 时固定目标时长
                self.ew_state = EW_MERGE if self.ew_type == 'merge' else EW_CUTOFF
                self._ew_target = int(self.rng.randint(6, 15)) \
                    if self.ew_type == 'merge' else int(self.rng.randint(9, 36))
                self.ew_duration = 0
        elif self.ew_state == EW_CUTOFF:
            self.ew_duration += 6
            # V 字曲线: 下降
            drop = 0.10 + 0.15 * self.rng.random()
            self.vmax = max(60.0, self.ew_peak * (1 - drop))
            if self.ew_duration >= self._ew_target:
                # 环境差 → 失败(9711 式)
                shear = self._sample(self._field('shear'), self.la, self.lo)
                if shear is not None and shear * 1.944 > 12:
                    self.ew_state = EW_FAILED
                    self.ew_duration = 0
                    self._ew_event('失败(9711式)')
                else:
                    self.ew_state = EW_CONTRACT
                    self.ew_duration = 0
        elif self.ew_state == EW_MERGE:
            self.ew_duration += 6
            self.vmax = max(60.0, self.ew_peak * 0.96)
            if self.ew_duration >= self._ew_target:
                self.ew_state = EW_CONTRACT
                self.ew_duration = 0
        elif self.ew_state == EW_CONTRACT:
            self.ew_duration += 6
            self.vmax = min(self.vmax + 4.0, self.ew_peak * 1.05)   # 回升
            if self.ew_duration >= 12:
                self._ew_event('完成')
                self.ew_state = EW_IDLE
                self.ew_count += 1
        elif self.ew_state == EW_FAILED:
            self.ew_duration += 6
            if self.ew_duration >= 36:
                shear = self._sample(self._field('shear'), self.la, self.lo)
                if shear is not None and shear * 1.944 <= 8:
                    self.ew_state = EW_REBUILD   # 重爆(杜苏芮式)
                    self.vmax = min(self.ew_peak, self.vmax + 12)
                    self.ew_duration = 0
                    self._ew_event('重爆(杜苏芮式)')
                else:
                    self.ew_state = EW_IDLE
                    self.ew_duration = 0
                    self._ew_event('失败后结束')
                self.ew_count += 1
        elif self.ew_state == EW_REBUILD:
            self.ew_duration += 6
            if self.ew_duration >= 12:
                self.ew_state = EW_IDLE
                self.ew_count += 1

    def _ew_event(self, note: str) -> None:
        self.ew_events.append({'t': self.t.strftime('%Y%m%d%H'), 'type': self.ew_type,
                               'note': note})

    def _update_stage(self) -> None:
        if self.stage == '消散':
            return
        # M1: 陆地衰减标记(减弱(陆地))在陆上持续期间不被强度比较覆盖
        if self.stage == '减弱(陆地)':
            if self._land_alt > 0:
                return
            self.stage = '减弱'
        if self.vmax >= 90:
            self.stage = '巅峰'
        elif self.vmax >= 64:
            # L6: 强度持平判"增强"(不倒退), 与"减弱"语义一致
            self.stage = '增强' if self.vmax >= (getattr(self, '_prev_v', 0)) else '减弱'
        elif self.vmax >= 34:
            self.stage = '增强' if self.vmax >= (getattr(self, '_prev_v', 0)) else '减弱'
        else:
            self.stage = '生成'
        self._prev_v = self.vmax

    def _snapshot(self) -> dict:
        return {'t': self.t.strftime('%Y%m%d%H'), 'la': round(self.la, 1),
                'lo': round(self.lo, 1), 'w': int(round(self.vmax)),
                'p': int(round(self.mslp)), 'st': self.nature,
                'nb': self.nb, 'r34': int(self.r34), 'ew': self.ew_state,
                'stage': self.stage,
                # M2: 云图渲染所需结构字段(切变方向/冷崩标记)
                'shear_angle': round(getattr(self, '_shear_angle', 0.0), 3),
                'cold_collapse': bool(getattr(self, '_cold_collapse', False))}

    def run(self, max_days: int = 0) -> List[dict]:
        """模拟至消散判定(B4: 无寿命上限,仅防死循环宽限 90 天并打警告)。"""
        limit = 90 * 4 if not max_days else max_days * 4
        warned = False
        for i in range(limit):
            try:
                self.step()
            except Exception:
                self.dissip = self.t
                self.dissip_reason = '异常终止'
                self.states.append(self._snapshot())
                break
            if self.dissip is not None:
                self.states.append(self._snapshot())
                break
            if not warned and i >= 89 * 4:
                warned = True
                print(f"[sim] 台风 {self.rec['id']} 超过 89 天仍未消散,继续按物理判据处理")
        return self.states

    # ── .dat 输出 ──

    def write_dat(self, out_dir: str = OUT_DIR) -> str:
        os.makedirs(out_dir, exist_ok=True)
        # 文件名年份取生成年(t0),跨年台风不取消散年(xrq 惯例为生成年)
        gen_year = self.rec.get('t0', '')[:4] or str(self.t.year)
        # dat_prefix 已含 'b' 前缀(如 'bwp'),勿再重复加 'b'(否则 bbwp01xxxx.dat 双 b)
        name = f"{B.dat_prefix(self.basin)}{self.no:02d}{gen_year}.dat"
        path = os.path.join(out_dir, name)
        lines = []
        for st in self.states:
            la_s = f"{int(abs(st['la']) * 10):3d}{'N' if st['la'] >= 0 else 'S'}"
            # C7: 与 xrq/回放程序惯例一致,经度 >180 写 W;
            # B: lo 已 %360,但 _snapshot 的 round 可使 359.9x 进位到 360.0,
            # 先 %360 归一避免 dateline 处写出 '0W'(实际为 0°E/日界线)
            lo = st['lo'] % 360.0
            if lo > 180:
                lo_s = f"{int((360 - lo) * 10):4d}W"
            else:
                lo_s = f"{int(lo * 10):4d}E"
            lines.append(f"{self.basin}, {self.no:02d}, {st['t']},   , XRQA,   0,"
                         f" {la_s}, {lo_s}, {st['w']:3d}, {st['p']:4d}, {st['st']}")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return path


def simulate_records(records: List[dict], api, seed: int = 1,
                     out_dir: str = OUT_DIR,
                     on_progress: Optional[Callable[[int, int], None]] = None
                     ) -> List[TyphoonSim]:
    sims = []
    # F4-14: 逐盆地独立编号(xrq 惯例: WP01, WP02, ... 各盆地从 1 起),
    # 界面显式指定 no 时优先
    basin_count: Dict[str, int] = {}
    for i, rec in enumerate(records):
        # 界面"低压编号"优先(rec['no']),否则按盆地顺序编号;
        # "20xx/19xx" 前缀按 年+号 习惯取后 2 位,其余按整值(F4-14)
        no = rec.get('no')
        if no is None:
            b = rec.get('basin', 'WP')
            basin_count[b] = basin_count.get(b, 0) + 1
            no = basin_count[b]
        else:
            s = str(no).strip()
            if s.isdigit() and len(s) == 4 and s[:2] in ('19', '20'):
                no = int(s[-2:])
            elif s.isdigit():
                no = int(s)
            else:
                no = i + 1
        s = TyphoonSim(rec, api, seed + i * 1000, no)
        s.uid = i + 1                # H2: 全局唯一 id(跨盆地 no 可能重复)
        sims.append(s)
    # 同步推进(支持吞并交互);无寿命上限,90 天防死循环宽限
    active = [s for s in sims]
    for _ in range(90 * 4):
        if not active:
            break
        for s in list(active):
            if s.dissip is not None:
                continue
            try:
                s.step()
            except Exception:
                # 单个台风步进异常: 跳过该台风, 不中止整个批次
                s.dissip = s.t
                s.dissip_reason = '异常终止'
                s.states.append(s._snapshot())
                active.remove(s)
                continue
            if s.dissip is not None:
                s.states.append(s._snapshot())
                active.remove(s)
        # 吞并(A2-2): 两系统风场合并为一个环流中心(即使槽状)才判弱方被吞并
        remove_set = set()
        for i in range(len(active)):
            if active[i] in remove_set:
                continue
            for j in range(i + 1, len(active)):
                if active[j] in remove_set:
                    continue
                a, b = active[i], active[j]
                if a.dissip is not None or b.dissip is not None:
                    continue
                d = math.hypot((a.la - b.la) * 111.0,
                               ((a.lo - b.lo + 180) % 360 - 180) * 111.0
                               * math.cos(math.radians((a.la + b.la) / 2)))
                if d >= 800:
                    continue          # 性能裁剪(判定以 merge 检测为准)
                from . import fields as FF
                if FF.fields_merged(api, a, b):
                    if a.vmax >= b.vmax:
                        a.absorb(b)
                        remove_set.add(b)
                    else:
                        b.absorb(a)
                        remove_set.add(a)
        if remove_set:
            active = [s for s in active if s not in remove_set]
        if on_progress is not None:
            done = len(sims) - len([s for s in active if s.dissip is None])
            on_progress(done, len(sims))
    for s in sims:
        if s.states:
            s.write_dat(out_dir)
    if on_progress is not None:
        on_progress(len(sims), len(sims))
    # 需求1: 模拟日志(文件 + 直接输出到 cmd 窗口)
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'logs')
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, f'sim_{seed}.log'), 'w', encoding='utf-8') as f:
            f.write(f"=== 模拟日志 (seed={seed}) ===\n")
            for s in sims:
                n_st = len(s.states)
                natures = sorted(set(st['st'] for st in s.states))
                line = (f"  #{s.no:02d} {s.basin} {s.rec.get('t0', '')} "
                        f"({s.rec.get('la0', 0):+.1f}°,{s.rec.get('lo0', 0):6.1f}°) "
                        f"性质={s.rec.get('nature', 'TD')}→{natures if natures else '-'} "
                        f"报点={n_st} 峰值={s.vmax_peak:.0f}kt "
                        f"消散={'否' if s.dissip is None else s.dissip.strftime('%Y%m%d%H')} "
                        f"({s.dissip_reason or '-'})")
                f.write(line + "\n")
                print(f"[sim] #{s.no:02d} {s.basin} {s.rec.get('t0', '')} "
                      f"({s.rec.get('la0', 0):+.1f}°,{s.rec.get('lo0', 0):6.1f}°) "
                      f"性质={s.rec.get('nature', 'TD')}→{natures if natures else '-'} "
                      f"报点={n_st} 峰值={s.vmax_peak:.0f}kt "
                      f"消散={s.dissip_reason or '进行中'}")
                for ev in getattr(s, 'ew_events', []):
                    f.write(f"    [置换] #{s.no:02d} {ev.get('t')} {ev.get('note', '')}\n")
                    print(f"[sim]   [置换] #{s.no:02d} {ev.get('t')} {ev.get('note', '')}")
    except Exception:
        pass
    return sims
