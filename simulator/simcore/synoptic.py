# simulator/simcore/synoptic.py
"""西风槽 + 冷空气准周期爆发(轮次 158 新增)。

为什么单独建一个文件
--------------------
`core.py` 里已有的天气尺度过程是 `synoptic_steer`(core.py:1027-1061):用**随机
相位**的无辐散流函数合成异常 —— 每个时刻槽脊落在哪个经度是随机实现,它提供的是
"方差", 没有"槽在某个经度、以某个速度东移、轴倾斜多少"这几个**自由度**。转向
(槽前西南气流)与冷空气爆发需要的恰恰是这种**相干**结构, 所以这里另建一个解析
模块与随机场并列, 而不是去改那条随机谱(core.py 的 `synoptic_steer` 保持原样)。

与既有「季风槽」不是一回事
--------------------------
`monsoon_trough` 在 simulator/env/param_fields.py:100-116(`_extract_trough`,
0-20N 的纬向最低海压), 由 simulator/env/field_io.py:25 登记, 只被
simulator/typhoons/gen.py:169 与 simulator/clouds/render.py:154 读取, **不进
simcore**。本模块是 |lat| 30-60° 的**中纬度西风槽**, 作用在引导风上。

设计约束(每条都在 tests/test_simcore_synoptic.py 里断言)
--------------------------------------------------------
1. 纯 numpy + 标准库; **不 import pygame / TySim / simulator.simcore.core**。
   网格常量照抄 core.py:41-51 的实测值(ENVNY=360, ENVNX=720, ENVD=0.5,
   LAT0=-90, LON0=0), 由测试直接与 core 对照。
2. 默认全关: `wtOn=False` / `csOn=False` ⇒ 所有入口返回 None, `apply_to_env`
   一个数组运算都不做, env 里的数组连对象都不换 ⇒ 现有结果逐位不变。
   即使 core 的 params 字典里**还没有**这些键(未打补丁), 取默认值后同样是关。
3. 冷空气 = 准周期脉冲 × 包络(季节 × 簇) × 平滑:
   * 有界: 构造上 `0 <= field <= csAmp`, 靠三条保证 —— 脉冲窗**互不重叠**
     (半宽按事件最小间隔收紧, 见 `_cold_ctx`)、簇包络与季节包络都 ≤ 1、
     空间平滑是归一化核的凸组合(最大值不增)。**不做 clip**(clip 会把构造
     错误藏起来, 有界性必须由构造与测试共同证明)。
   * 空间光滑: 两个包络都是高斯型, 再叠一次可分离归一化高斯平滑。
   * 时间连续: 事件脉冲用 C∞ 紧支撑核 `exp(-x²/(1-x²))`, 与光滑的簇/季节包络
     相乘, 时间上处处可导(不是方波, 也不是白噪声)。
   * 可复现: 随机量只来自 `(sim['seed'], 事件序号)` 的**整数哈希**(splitmix64
     混合), 不用 numpy 的 RNG 流、不存跨步状态、没有模块级缓存 ⇒ 同种子同参数
     逐位复现; 不同种子抖动位置不同 ⇒ 发散。
4. 单位与 core 一致: 风 kt, 时间 h, 经纬度 °, 距离换算 110.57 km/°lat、
   111.32 km/°lon(与 core.py:1038-1039 同口径)。

不改动的共享文件: core.py / sim_mode_mixin.py / sim_settings_dialog.py /
keyboard_mixin.py / climate.py / utils_mixin.py / render.py。
接入 core 的精确补丁规格见 tests/test_simcore_synoptic.py 文件末尾的说明与本
文件 `apply_to_env` 的 docstring。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

# ── 环境网格常量(轮次 158: 照抄 core.py:41-51 的实测值, 刻意不 import core) ──
ENVD = 0.5                  # core.py:44
LAT0, LAT1 = -90.0, 90.0    # core.py:41
LON0, LON1 = 0.0, 360.0     # core.py:41
ENVNY, ENVNX = 360, 720     # core.py:50-51

_DEG = math.pi / 180.0
_KM_LAT = 110.57            # core.py:1038
_KM_LON = 111.32            # core.py:1039
_COS_MIN = 0.15             # core.py:1037
_YEAR_D = 365.25
_HEMI_W = 8.0               # 赤道两侧季节反相的平滑过渡尺度(°)

#: 新 params 键的默认值(轮次 158)。**默认全关**: wtOn/csOn = False。
DEFAULTS = {
    # ── 西风槽(中纬度, 可调 位置/强度/波长/移速/倾角) ──
    'wtOn': False,       # 总开关。False = 不改变现有结果
    'wtLon': 120.0,      # t=0 时槽轴经度(°E, 在槽带中心纬度上)
    'wtLat': 45.0,       # 槽带中心纬度(|lat|, °)
    'wtLatW': 13.0,      # 槽带纬度半宽(°, 高斯型)
    'wtAmp': 6.0,        # 引导风异常峰值(kt, hypot(du,dv) 的网格最大值)
    'wtWave': 5.0,       # 纬向波数(波长 = 360/wtWave °)
    'wtSpeed': 8.0,      # 槽东移速度(°/天, 正 = 向东)
    'wtTilt': 0.5,       # 槽轴倾角(°经度 / °纬度, 正 = 高纬槽轴偏东)
    'wtLonJit': 0.0,     # 初始经度的种子抖动半宽(°, 0 = 位置完全确定)
    # ── 冷空气准周期爆发 ──
    'csOn': False,       # 总开关。False = 不改变现有结果
    'csAmp': 8.0,        # 低层风异常峰值(kt, 沿现有风向加速)
    'csPeriod': 168.0,   # 准周期(h, 默认 7 天)
    'csDur': 54.0,       # 单次爆发总时长(h); 半宽会被「不重叠」规则收紧
    'csJit': 0.25,       # 爆发时刻的种子抖动(×周期, 内部上限 0.45)
    'csMod': 0.35,       # 簇包络调制深度(0 = 无簇结构, 纯等周期)
    'csCluster': 4.0,    # 簇周期 = csPeriod * csCluster(h)
    'csSeason': 0.6,     # 季节包络调制深度(0 = 无季节变化)
    'csPeakDay': 15.0,   # 北半球季节包络峰值(儒略日); 南半球反相
    'csLat': 40.0,       # 冷空气纬度带中心(|lat|, °)
    'csLatW': 18.0,      # 纬度带半宽(°)
    'csLon0': 105.0,     # t=0 时冷空气源区经度(°E)
    'csLonW': 65.0,      # 源区经度包络半宽(°)
    'csLonSpeed': 6.0,   # 源区东移速度(°/天)
    'csSmooth': 1.5,     # 空间高斯平滑 σ(格); 0 = 不平滑
}


def defaults() -> dict:
    """返回新 params 键的默认值(浅拷贝即可: 全是标量)。

    core.py 的 params 默认字典(锚点 core.py:810 之后)直接
    `params.update(synoptic.defaults())` 即可, 默认行为不变(全关)。
    """
    return dict(DEFAULTS)


def _pv(p: dict, key: str):
    """读一个 params 键, 缺失时退到 DEFAULTS(因此未打补丁的 core 也安全)。"""
    if not isinstance(p, dict):
        return DEFAULTS[key]
    return p.get(key, DEFAULTS[key])


# ── 网格与基础工具 ───────────────────────────────────────────────────────

def _lat_axis() -> np.ndarray:
    """纬度轴(deg), 与 core.py:592 的 env_lat 同式。"""
    return LAT0 + (np.arange(ENVNY, dtype=np.float64) + 0.5) * ENVD


def _lon_axis() -> np.ndarray:
    """经度轴(deg), 与 core.py:588 的 env_lon 同式。"""
    return LON0 + (np.arange(ENVNX, dtype=np.float64) + 0.5) * ENVD


def _wrap180(d):
    """把经度差映到 [-180, 180)(与 core 的 wrap180 同义)。"""
    return (np.asarray(d, dtype=np.float64) + 180.0) % 360.0 - 180.0


def _norm_gauss(x, c, w):
    """归一化高斯包络: 峰值**精确** 1(除以网格上的最大值)。

    归一化是为了让幅度参数就是峰值本身(`wtAmp`/`csAmp`), 测试才能断言
    "峰值 == 参数"; 否则 0.5° 网格取不到中心点(如 45.0 不在网格上), 峰值会
    差 1e-3 量级。
    """
    w = max(1e-6, float(w))
    g = np.exp(-(((np.asarray(x, dtype=np.float64) - float(c)) / w) ** 2))
    m = float(g.max())
    if m < 1e-12:
        return np.zeros_like(g)
    return g / m


def _hash_unit(seed: int, tag: int) -> float:
    """确定性整数哈希 → [0,1)。splitmix64 混合(mask 64 位)。

    刻意不用 numpy RNG 逐事件抽抖动: 每步都要为"邻近事件"重算抖动,
    `default_rng` 每次构造约 20 µs, 而哈希约 1 µs; 而且哈希不依赖 numpy 的
    流实现 ⇒ 逐位复现只受 IEEE-754 约束(轮次 158)。
    """
    x = (int(seed) * 0x9E3779B97F4A7C15 + (int(tag) + 0x51ED270B) * 0xBF58476D1CE4E5B9)
    x &= 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 31
    return (x >> 11) * (1.0 / 9007199254740992.0)


def _smooth2d(f: np.ndarray, sig: float) -> np.ndarray:
    """可分离归一化高斯平滑(经度环向 wrap, 纬度边界复制)。

    归一化核 + 正权重 ⇒ 每个输出都是输入的**凸组合** ⇒ 最大值不增、最小值不减,
    所以"有界"这条不会被平滑破坏; 同时把任何网格尺度的毛刺压掉。
    """
    if sig <= 0.0:
        return f
    sig = min(6.0, float(sig))
    r = int(math.ceil(3.0 * sig))
    x = np.arange(-r, r + 1, dtype=np.float64)
    ker = np.exp(-0.5 * (x / sig) ** 2)
    ker /= ker.sum()
    nlat, nlon = f.shape
    fp = np.pad(f, ((0, 0), (r, r)), mode='wrap')
    out = np.zeros_like(f)
    for i, w in enumerate(ker):
        out += np.float32(w) * fp[:, i:i + nlon]
    fp2 = np.pad(out, ((r, r), (0, 0)), mode='edge')
    out2 = np.zeros_like(f)
    for i, w in enumerate(ker):
        out2 += np.float32(w) * fp2[i:i + nlat, :]
    return out2


# ── ① 西风槽 ─────────────────────────────────────────────────────────────

def _trough_build(sim):
    """构造西风槽, 返回 (psi, du, dv); 关闭时返回 None。

    数学式(轮次 158):

        x0(t) = wtLon + wtSpeed * t / 24            # 槽轴 t=0 位置 + 东移
        θ(λ,φ) = k(λ - x0) - k·wtTilt·(|φ| - wtLat)   # k = 2π·wtWave/360
        ψ(λ,φ) = -band(|φ|) · cos θ                 # 槽轴 = ψ 极小 = 低压槽
        du = -∂ψ/∂y,  dv = +∂ψ/∂x                   # 无辐散流函数

    符号检查: 槽轴以东 cos θ 减小、∂ψ/∂x > 0 ⇒ v' > 0(南风), 即**槽前西南气流**;
    槽轴以西 v' < 0(北风), 即槽后西北气流 —— 与 core.py:1012-1015 记的同一条
    物理。`wtTilt > 0` 时高纬槽轴偏东(倾角), 由 θ 里的相位项实现。

    强度口径: 最后把 (du,dv) 整体缩放到 `hypot(du,dv)` 的网格最大值 == `wtAmp`(kt),
    因此 `wtAmp` 就是"风异常峰值"(与 core.py:1018-1024 的幅度包络同口径), 且与
    经向/纬向分量分开定标的做法不冲突(这里是单支相干槽, 不需要各自定标)。
    """
    p = sim.get('params') if isinstance(sim.get('params'), dict) else {}
    if not bool(_pv(p, 'wtOn')):
        return None
    t = float(sim.get('t', 0.0))
    amp = max(0.0, float(_pv(p, 'wtAmp')))
    wave = abs(float(_pv(p, 'wtWave')))
    lat_c = float(np.clip(float(_pv(p, 'wtLat')), 0.0, 89.0))
    lat_w = max(1e-3, float(_pv(p, 'wtLatW')))
    tilt = float(_pv(p, 'wtTilt'))
    speed = float(_pv(p, 'wtSpeed'))
    lon0 = float(_pv(p, 'wtLon'))
    jit = max(0.0, float(_pv(p, 'wtLonJit')))
    if jit > 0.0:
        seed = int(sim.get('seed', 0) or 0)
        lon0 += jit * (2.0 * _hash_unit(seed, 0x77) - 1.0)

    lat = _lat_axis()
    lon = _lon_axis()
    s = np.abs(lat)
    band = _norm_gauss(s, lat_c, lat_w)                 # (nlat,)
    k = 2.0 * math.pi * wave / 360.0                    # rad / °经度
    a = k * (lon - (lon0 + speed * t / 24.0))           # (nlon,)
    b = -k * tilt * (s - lat_c)                         # (nlat,)
    ca, sa = np.cos(a), np.sin(a)
    cb, sb = np.cos(b), np.sin(b)
    # cos(a+b) 展开成两组外积, 省掉 meshgrid 与两个大相位数组
    psi = -band[:, None] * (ca[None, :] * cb[:, None] - sa[None, :] * sb[:, None])
    # 经向: 一阶单侧(极区 band→0, 无假极值); 纬向: 环向中心差分(跨 0° 无缝)
    gy = np.gradient(psi, ENVD, axis=0)
    gx = (np.roll(psi, -1, axis=1) - np.roll(psi, 1, axis=1)) / (2.0 * ENVD)
    coslat = np.maximum(_COS_MIN, np.cos(lat * _DEG))[:, None]
    du = -gy / _KM_LAT
    dv = gx / (_KM_LON * coslat)
    mag = np.hypot(du, dv)
    mmax = float(mag.max())
    if not np.isfinite(mmax) or mmax < 1e-12:
        z = np.zeros((ENVNY, ENVNX), dtype=np.float32)
        return psi.astype(np.float32), z, z
    sc = amp / mmax
    return (psi.astype(np.float32), (du * sc).astype(np.float32),
            (dv * sc).astype(np.float32))


def trough_streamfunction(sim):
    """诊断用: 归一化流函数 ψ(无量纲, (ENVNY, ENVNX) float32); 关闭时 None。

    只用于检查槽轴位置/波长/倾角(槽轴 = 沿纬圈的 ψ 极小), 不参与任何物理计算。
    """
    r = _trough_build(sim)
    return None if r is None else r[0]


def westerly_trough(sim):
    """西风槽的引导风异常 (du, dv)(kt, 全网格 float32); 关闭时 (None, None)。

    与 core.py:1027-1061 的 `synoptic_steer` 同构(无辐散流函数), 但**相干、可调**:
    位置 wtLon / 强度 wtAmp / 波长 wtWave / 移速 wtSpeed / 倾角 wtTilt。
    """
    r = _trough_build(sim)
    if r is None:
        return None, None
    return r[1], r[2]


# ── ② 冷空气准周期爆发 ───────────────────────────────────────────────────

def _cold_ctx(sim):
    """读参数 + 做所有钳制, 返回上下文 dict; 关闭时 None。

    钳制(轮次 158)保证"有界"是无条件成立的, 与用户怎么填参数无关:
      * csJit ∈ [0, 0.45] ⇒ 相邻事件中心的最小间隔 = P(1-2·jit_h/P) ≥ 0.1P;
      * 脉冲半宽 half_w = min(csDur/2, 0.49·P(1-2·csJit)) ⇒ **任意两个事件的
        支撑区间不相交** ⇒ Σ 脉冲 ≤ 1(单核峰值恰为 1);
      * csMod / csSeason ∈ [0,1] ⇒ 簇包络、季节包络都 ≤ 1。
    """
    p = sim.get('params') if isinstance(sim.get('params'), dict) else {}
    if not bool(_pv(p, 'csOn')):
        return None
    period = max(1e-6, float(_pv(p, 'csPeriod')))
    dur = max(0.0, float(_pv(p, 'csDur')))
    jit = min(0.45, max(0.0, float(_pv(p, 'csJit'))))
    jit_h = jit * period
    gap = 0.5 * period * (1.0 - 2.0 * jit)
    half_w = min(0.5 * dur, 0.98 * gap)
    if half_w <= 1e-9:
        half_w = 1e-9                       # 退化: 脉冲恒为 0(不崩、不改行为)
    return {
        't': float(sim.get('t', 0.0)),
        'seed': int(sim.get('seed', 0) or 0),
        'period': period,
        'half_w': half_w,
        'jit_h': jit_h,
        'mod': min(1.0, max(0.0, float(_pv(p, 'csMod')))),
        'nclus': max(1e-6, float(_pv(p, 'csCluster'))),
        'amp': max(0.0, float(_pv(p, 'csAmp'))),
        'lat_c': float(np.clip(float(_pv(p, 'csLat')), 0.0, 89.0)),
        'lat_w': max(1e-3, float(_pv(p, 'csLatW'))),
        'lon0': float(_pv(p, 'csLon0')),
        'lon_w': max(1e-3, float(_pv(p, 'csLonW'))),
        'lon_sp': float(_pv(p, 'csLonSpeed')),
        'seas': min(1.0, max(0.0, float(_pv(p, 'csSeason')))),
        'peak_day': float(_pv(p, 'csPeakDay')),
        'sigma': min(6.0, max(0.0, float(_pv(p, 'csSmooth')))),
    }


def _cold_pulse(t: float, ctx: dict) -> float:
    """准周期脉冲串 Σ_k H((t-t_k)/W) ∈ [0,1]。

    H(x) = exp(-x²/(1-x²)), |x|<1(紧支撑 C∞ 核, 峰值 1, 边界值/各阶导数都 →0);
    t_k = k·P + jitter_k, jitter_k = (2·hash(seed,k)-1)·csJit·P(**有界**)。
    只枚举 t 附近的事件(k 范围由 W 决定), 与 t 的历史无关 ⇒ 无跨步状态。
    """
    period, half_w = ctx['period'], ctx['half_w']
    if half_w <= 1e-9:
        return 0.0
    jh, seed = ctx['jit_h'], ctx['seed']
    k0 = int(math.floor((t - half_w) / period)) - 1
    k1 = int(math.ceil((t + half_w) / period)) + 1
    s = 0.0
    for k in range(k0, k1 + 1):
        tk = k * period
        if jh > 0.0:
            tk += (2.0 * _hash_unit(seed, 0x1000 + k) - 1.0) * jh
        x = (t - tk) / half_w
        if -1.0 < x < 1.0:
            s += math.exp(-(x * x) / (1.0 - x * x))
    return s


def _cold_cluster(t: float, ctx: dict) -> float:
    """簇包络 ∈ [1-csMod, 1]: 周期的慢调制(默认 4×csPeriod = 28 天), 有界且光滑。

    相位由种子哈希给出 ⇒ 不同成员的事件强度组合不同(发散来源之一)。
    """
    mod, nclus = ctx['mod'], ctx['nclus']
    if mod <= 0.0:
        return 1.0
    pc = ctx['period'] * nclus
    ph = 2.0 * math.pi * _hash_unit(ctx['seed'], 0x2001)
    return (1.0 - mod) + mod * 0.5 * (1.0 + math.cos(2.0 * math.pi * t / pc + ph))


def _sim_doy(sim):
    """当前儒略日(1.0 = 1月1日00z); 拿不到日历则 None(季节包络退化为 1)。"""
    st = sim.get('start')
    t = float(sim.get('t', 0.0))
    if isinstance(st, datetime):
        c = st + timedelta(hours=t)
        return c.timetuple().tm_yday + (c.hour + c.minute / 60.0) / 24.0
    m = sim.get('month')
    if m is not None:
        return (float(m) - 0.5) * (_YEAR_D / 12.0)
    return None


def _season_factor(sim, lat1d: np.ndarray, ctx: dict) -> np.ndarray:
    """季节包络 ∈ [1-csSeason, 1](南北半球反相, 赤道用 tanh 平滑过渡)。

    `csSeason=0` 或拿不到日历时返回全 1(不影响"有界"与可复现)。
    """
    d = ctx['seas']
    if d <= 0.0:
        return np.ones_like(lat1d)
    doy = _sim_doy(sim)
    if doy is None:
        return np.ones_like(lat1d)
    ph = 2.0 * math.pi * (doy - ctx['peak_day']) / _YEAR_D
    nh = 0.5 * (1.0 + math.cos(ph))
    sh = 0.5 * (1.0 + math.cos(ph + math.pi))
    w = 0.5 * (1.0 + np.tanh(lat1d / _HEMI_W))
    seas = w * nh + (1.0 - w) * sh
    return (1.0 - d) + d * seas * seas


def cold_index(sim, lat_ref=None):
    """冷空气的时间标量 ∈ [0,1](脉冲 × 簇包络 × 季节包络); 关闭时 None。

    `lat_ref` 是取季节包络用的纬度(默认 +csLat, 即北半球)。诊断/测试用。
    """
    ctx = _cold_ctx(sim)
    if ctx is None:
        return None
    t = ctx['t']
    v = _cold_pulse(t, ctx) * _cold_cluster(t, ctx)
    if lat_ref is None:
        lat_ref = ctx['lat_c']
    lat1 = np.array([float(lat_ref)], dtype=np.float64)
    return float(v * _season_factor(sim, lat1, ctx)[0])


def cold_air(sim):
    """冷空气低层风异常的**强度场**(kt, (ENVNY, ENVNX) float32); 关闭时 None。

    field = csAmp · [脉冲(t)·簇(t)·季节] · band(|lat|) · lonenv(λ,t)
    再叠一次可分离高斯平滑。所有因子 ≤ 1 且脉冲支撑互不相交 ⇒ 0 ≤ field ≤ csAmp。
    方向不在本函数内定(冷空气是"沿现有低层风向加速"), 由 `apply_to_env` 做投影。
    """
    ctx = _cold_ctx(sim)
    if ctx is None:
        return None
    t = ctx['t']
    s = _cold_pulse(t, ctx) * _cold_cluster(t, ctx)
    lat = _lat_axis()
    lon = _lon_axis()
    band = _norm_gauss(np.abs(lat), ctx['lat_c'], ctx['lat_w'])[:, None]
    lon_c = ctx['lon0'] + ctx['lon_sp'] * t / 24.0
    lon_env = _norm_gauss(_wrap180(lon - lon_c), 0.0, ctx['lon_w'])[None, :]
    seas = _season_factor(sim, lat, ctx)[:, None]
    f = (ctx['amp'] * s) * band * lon_env * seas
    f = f.astype(np.float32)
    if ctx['sigma'] > 0.0:
        f = _smooth2d(f, ctx['sigma'])
    return f


# ── ③ 接入 core 的唯一入口 ───────────────────────────────────────────────

def apply_to_env(env: dict, sim: dict) -> dict:
    """把西风槽 + 冷空气叠加到 env 的风场上(就地 rebind); 关闭时是**纯 no-op**。

    只改 u850/v850/u200/v200 四个既有键 —— **不新增任何 env 键**。
    (轮次 158 侦察结论: core.py:1723-1737 已有一个 `env['coldSurge']`, 但
    `vortex_wind` 的三个调用点都没传 `env`(core.py:4345/4373), 是死代码; 若复用它
    并在数据模式下被激活, 会一次性打开一段从未运行过的风场代码 ⇒ 破坏"默认关 =
    逐位不变"。所以这里绝不碰它。)

    * 西风槽: 正压, 四层同加(core.py:1611-1613 的同一条约束) ⇒ u200-u850 不变。
    * 冷空气: 只加在低层(u850/v850), 沿**现有风向**加速 ⇒ |ΔV| = 强度场,
      方向不变; 切变反馈由调用点之后的通路自动生效(数据分支 core.py:1629-1654
      由 u200-u850 算切变; 解析分支 core.py:1688-1691 由 u850 合成 u200)。

    返回值(供调用点/测试检查, 也供可选补丁 P4 回算切变):
      {'wt': bool, 'cs': bool, 'du','dv': 西风槽异常(kt) 或 None,
       'cold_du','cold_dv': 冷空气沿风向增量(kt) 或 None}
    """
    out = {'wt': False, 'cs': False, 'du': None, 'dv': None,
           'cold_du': None, 'cold_dv': None}
    if not isinstance(env, dict):
        return out
    du, dv = westerly_trough(sim)
    if du is not None:
        for key, d in (('u850', du), ('v850', dv), ('u200', du), ('v200', dv)):
            a = env.get(key)
            if a is None or np.shape(a) != du.shape:
                continue
            env[key] = (np.asarray(a, dtype=np.float32) + d).astype(np.float32)
        out['wt'], out['du'], out['dv'] = True, du, dv
    cs = cold_air(sim)
    if cs is not None:
        out['cs'] = True
        if bool(np.any(cs > 0.0)):
            u = env.get('u850')
            v = env.get('v850')
            if u is not None and np.shape(u) == cs.shape:
                u = np.asarray(u, dtype=np.float32)
                v = np.asarray(v, dtype=np.float32)
                mag = np.hypot(u, v) + np.float32(1e-6)
                cdu = (cs * (u / mag)).astype(np.float32)
                cdv = (cs * (v / mag)).astype(np.float32)
                env['u850'] = (u + cdu).astype(np.float32)
                env['v850'] = (v + cdv).astype(np.float32)
                out['cold_du'], out['cold_dv'] = cdu, cdv
    return out
