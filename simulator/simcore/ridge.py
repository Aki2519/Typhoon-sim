# simulator/simcore/ridge.py
"""副高**形状**参数化(轮次 158): 脊线倾角 / 西端陡峭 / 东西南北四向伸展。

本模块只提供**纯函数**, 不持有状态、不 import 任何仓库内模块(尤其不 import
`simulator.simcore.core`, 避免把 269KB 的重依赖拖进测试与工具), 只依赖 numpy。

────────────────────────────────────────────────────────────────────────────
不变量(最重要, 任何改动都必须先满足它)
────────────────────────────────────────────────────────────────────────────
**默认参数下, 本模块的输出与仓库里既有的公式逐位(bit-for-bit, 即
`np.array_equal` 为真、不是 `allclose`)一致。** 也就是说: 只要调用方不显式给出
新参数, 标定好的气候态结果**一个 bit 都不会变**。

证据锚点(全部实读):
  · 副高核  : `simulator/simcore/core.py:1546-1554`
  · 副高中心: `simulator/simcore/core.py:437-446`(`RIDGES`, 西太 = (31.0, 146.0, 10.0))
  · 脊线诊断: `simulator/simcore/core.py:1907-1961`(`sh_ridge_lat`, 返回**绝对纬度**)
  · 参数习惯: `shPoleS`/`shPoleW`/`shPoleMax` 一律「模块常量 + `P.get(key, 常量)`」,
    不写进 `core.init` 的 params 默认字典(见 core.py:2006-2008)

**逐位一致是怎么保证的**(不是"近似", 是 IEEE-754 上的恒等):

1. 四向伸展 `ridge_core()` 用的是把 `core.py:1551-1553` 的三个常数换成参数,
   并且**保留原来的算式形状**(除法留在 `np.minimum` / `np.maximum` 里面):
   · 默认 `shExtW=22.0` / `shExtE=12.0` ⇒ `np.minimum(0.0, d/22.0)` 与
     `np.minimum(0.0, d/shExtW)` 是**同一个操作**, 结果位级相同;
   · 默认 `shExtN=shExtS=5.5` ⇒ 南北拆成 `max(0,dy)/5.5` 与 `min(0,dy)/5.5`,
     两者中恒有一个恰好是 `0.0`, 而 `(-a) - 0.0 == -a`、`(-0.0) - a == -a` 在
     IEEE-754 下是精确恒等(含 `a` 为 `0.0` 的退化情形), 且**加法结合顺序与
     原式逐项对齐**(`-(dy_n²) - (dy_s²) - (dx_w²) - (dx_e²)`), 所以 `np.exp`
     拿到的输入位级相同, 输出必然位级相同;
   · 网格 dtype 是 float32(`core.py:942-947`), numpy 2.x(NEP50)下
     `float32 数组 / Python float` 仍是 float32 —— 本模块不做任何 `float64` 提升,
     保持调用方 dtype(见 `test_dtype_is_preserved`)。

2. 1D 脊线形状 `ridge_line_shape()` 在**形状参数全为默认(倾角 0、西端幅度 0)时
   直接短路返回输入的副本**, 一行数值运算都不做 —— 与仓库里
   `synoptic_steer` 的"默认关 = 场逐位不变"是同一个写法。

3. 即使把短路去掉, 默认下 `delta ≡ 0.0`, `r + 0.0` 对一切有限 `r` 也是精确恒等
   (唯一例外是 `r = -0.0`, 而脊线纬度是**绝对纬度**, 定义域内不存在 `-0.0`;
   短路分支仍然覆盖了它)。

单位约定(与 simcore 全局一致, 见 core.py:17):
  · 经/纬度      : 度(东经 0-360, 北纬为正)
  · 脊线纬度     : 度, **绝对纬度**(南北半球都是正数, 见 core.py:1915 / 1954-1955)
  · 四向伸展尺度 : 度(经/纬度上的高斯尺度, 与原式的 22.0/12.0/5.5 同量纲)
  · 倾角         : 度纬度 / 度经度
"""
from __future__ import annotations

from typing import Optional

import numpy as np

__all__ = [
    'DEFAULT_PARAMS',
    'wrap_lon180',
    'ridge_params',
    'basin_window',
    'ridge_line_delta',
    'ridge_line_shape',
    'ridge_core',
    'ridge_anomaly',
]

# ── 默认值(轮次 158) ─────────────────────────────────────────────────────────
#: 参数默认值。**四向伸展的四个数逐字等于 core.py:1551-1553 里的常数** ——
#: 这是"默认逐位一致"的物理来源, 不要为了让某组扫参好看而改这里。
DEFAULT_PARAMS = {
    # ── 一维脊线形状(作用在 `sh_ridge_lat` 的输出上) ──
    'shTilt': 0.0,          #: 脊线倾角(度纬度/度经度)。0 = 关闭(默认)
    'shTiltLon0': 146.0,    #: 倾角枢轴经度(= `RIDGES[0]` 的西太中心, core.py:439)
    'shWestAmp': 0.0,       #: 西端台阶幅度(度纬度, 有符号)。0 = 关闭(默认)
    'shWestOff': -30.0,     #: 台阶中心相对枢轴的经度偏移(度)
    'shWestW': 12.0,        #: 台阶宽度(度经度)。**越小西端越陡**
    'shWinHalf': 45.0,      #: 盆地窗平台半宽(度经度)
    'shWinShoulder': 25.0,  #: 盆地窗余弦肩宽(度经度)
    'shHemiMirror': True,   #: 南半球镜像(倾角/台阶取反), 保证南北形状对称
    # ── 二维副高数值场(作用在 `env['pEnv']` 的副高核上) ──
    'shExtN': 5.5,          #: 北伸尺度 == core.py:1551 的 5.5
    'shExtS': 5.5,          #: 南伸尺度 == core.py:1551 的 5.5
    'shExtW': 22.0,         #: 西伸尺度 == core.py:1552 的 22.0
    'shExtE': 12.0,         #: 东伸尺度 == core.py:1553 的 12.0
}

#: 经度轴默认参数: 与 `_env_grids()`(core.py:931-948)逐位一致 ——
#: `ENVD=0.5`(core.py:44), `LON0=0.0`(core.py:41) ⇒ 首个格心 = 0.25°E,
#: 即 `lon(i) = 0.25 + 0.5*i`, i = 0..719(`ENVNX=720`, core.py:50)。
_LON0_CENTER = 0.25
_LON_DELTA = 0.5

#: 尺度类参数的下限(与 `shPoleW` 的 `max(1e-6, ...)` 同风格, core.py:2007)。
_MIN_SCALE = 1e-6


def wrap_lon180(d):
    """把经度差折到 `[-180, 180)`。与 `core.py:1550` 的**同一个算式**。

    仓库里没有独立的 `wrap180` 帮助函数, 该算式是就地内联的(`core.py:1550`
    / `1318` / `types.py:206` 等十余处)。这里提出来只为可读性, 算子顺序
    `(d + 180.0) % 360.0 - 180.0` 与 core 完全一致, 位级相同。
    """
    return (np.asarray(d) + 180.0) % 360.0 - 180.0


def ridge_params(params: Optional[dict] = None) -> dict:
    """读出副高形状参数(带默认值), 并做下限保护。

    `params` 传 `sim['params']` 即可 —— 缺失的键自动落到 `DEFAULT_PARAMS`,
    这正是 `shPole*` 系列的做法(`core.py:2006-2008`): 新旋钮**不需要**写进
    `core.init` 的默认字典, 也就不可能影响任何既有测试对 `sim['params']` 的假设。

    返回的字典里尺度类(`shExt*` / `shWestW` / `shWinShoulder`)已保证
    `>= 1e-6`, 避免除零。默认值 22.0/12.0/5.5 经 `max(1e-6, ·)` 后不变,
    所以"默认逐位一致"不受这层保护影响。
    """
    P = dict(params or {})
    out: dict = {}
    for k, v in DEFAULT_PARAMS.items():
        out[k] = P.get(k, v)
    out['shHemiMirror'] = bool(out['shHemiMirror'])
    for k in ('shTilt', 'shTiltLon0', 'shWestAmp', 'shWestOff'):
        out[k] = float(out[k])
    for k in ('shWestW', 'shWinHalf', 'shWinShoulder',
              'shExtN', 'shExtS', 'shExtW', 'shExtE'):
        out[k] = max(_MIN_SCALE, float(out[k]))
    return out


def basin_window(lon, center: float = 146.0, half: float = 45.0,
                 shoulder: float = 25.0):
    """「平台 + 余弦肩」盆地窗(无量纲, 0..1)。

    · `|wrap_lon180(lon - center)| <= half`          → 1.0  (平台)
    · `half < |·| < half + shoulder`                 → `0.5*(1+cos(pi*t))`, `t=(|·|-half)/shoulder`
    · `|·| >= half + shoulder`                       → 0.0

    两端导数都为 0(`t=0` 与 `t=1` 处 `sin=0`), 所以窗是 C1 连续的 —— 叠加在
    脊线纬度上不会产生折点。作用: 把形状改动**局地化在一个洋盆内**, 否则
    倾角项绕地球一圈会在另一侧造成同样大的反向偏移。默认 `half=45`、
    `shoulder=25`: 西太盆地(中心 146°E)的影响范围是 76°E..216°E。
    """
    u = np.abs(wrap_lon180(np.asarray(lon, dtype=np.float64) - float(center)))
    half = max(0.0, float(half))
    shoulder = max(0.0, float(shoulder))
    if shoulder <= 0.0:
        return np.where(u <= half, 1.0, 0.0)
    t = (u - half) / shoulder
    w = 0.5 * (1.0 + np.cos(np.pi * np.clip(t, 0.0, 1.0)))
    return np.where(t <= 0.0, 1.0, np.where(t >= 1.0, 0.0, w))


def ridge_line_delta(lon, params: Optional[dict] = None, hemi: int = 1):
    """逐经度脊线纬度的**增量**(度)。默认参数下返回 `None`(= 完全关闭)。

    数学式(`λ` 为经度, `λ0 = shTiltLon0`, `u = wrap_lon180(λ - λ0)`, `σ` 为 logistic):

        Δ(λ) = s · win(λ) · [ shTilt·u  +  shWestAmp · (1 - σ((u - shWestOff)/shWestW)) ]

    其中 `s = -1` 当 `hemi < 0` 且 `shHemiMirror` 为真(南半球镜像), 否则 `s = +1`。
    返回 `None` 而不是全零数组, 是为了让调用方能**一行运算都不做**(见模块
    docstring 的不变量 2)。

    两项的物理含义(`win` 见 `basin_window`):
      · 倾角项 `shTilt·u`: 脊线轴沿经向线性倾斜。`shTilt > 0` = 脊轴**向东抬向
        极地**(西低东高); `shTilt < 0` = 西高东低。枢轴在 `shTiltLon0`, 那里
        增量恒为 0。
      · 西端台阶 `shWestAmp·(1-σ)`: 在 `λ = λ0 + shWestOff` 以西把脊线纬度整体
        抬/压 `shWestAmp` 度(有符号), 以东衰减到 0。`shWestW` 是过渡宽度 ——
        **越小, 西端越陡**。"有界"指 logistic 天然把台阶夹在 `[0, shWestAmp]`,
        不会像纯线性项那样在远西侧无限外推。
    """
    Q = ridge_params(params)
    tilt = Q['shTilt']
    wamp = Q['shWestAmp']
    if tilt == 0.0 and wamp == 0.0:
        return None
    x = np.asarray(lon, dtype=np.float64)
    lon0 = Q['shTiltLon0']
    u = wrap_lon180(x - lon0)
    win = basin_window(x, lon0, Q['shWinHalf'], Q['shWinShoulder'])
    dtilt = tilt * u
    # 有界 logistic 台阶。clip 到 ±700 是为了让 exp 不溢出(超出该范围时台阶已
    # 饱和到 0 或 1, 结果不受影响), 同时避免 numpy 的 overflow RuntimeWarning。
    _z = (u - Q['shWestOff']) / Q['shWestW']
    wstep = wamp * (1.0 - 1.0 / (1.0 + np.exp(-np.clip(_z, -700.0, 700.0))))
    sgn = -1.0 if (hemi < 0 and Q['shHemiMirror']) else 1.0
    return sgn * win * (dtilt + wstep)


def _lon_axis(n: int) -> np.ndarray:
    """`sh_ridge_lat` 输出数组(长度 `ENVNX`)对应的经度轴。"""
    return _LON0_CENTER + _LON_DELTA * np.arange(int(n), dtype=np.float64)


def ridge_line_shape(rlat, lon=None, params: Optional[dict] = None,
                     hemi: int = 1):
    """对**逐经度脊线纬度数组**施加「倾角 + 西端陡峭」。

    参数
    ----
    rlat : (ENVNX,) 数组
        `sh_ridge_lat(sim)`(core.py:1907-1961)返回的那条曲线, 单位是**绝对纬度**。
        长度 `ENVNX=720`(core.py:50)。
    lon : (ENVNX,) 数组 或 None
        每个格心的经度。`None` 时按 `0.25 + 0.5*i` 生成 —— 与
        `_env_grids()`(core.py:931-948)的经度轴逐位一致。
    params : dict 或 None
        传 `sim['params']`。
    hemi : +1 / -1
        `+1` = 北半球(`env['shRidgeN']`), `-1` = 南半球(`env['shRidgeS']`)。
        `shHemiMirror=True`(默认)时南半球增量取反, 保证两侧形状镜像对称。

    返回
    ----
    同 shape、同 dtype 的新数组(绝对纬度, 已被夹到 `[0, 90]`)。

    **默认参数(倾角 0、西端幅度 0)下返回输入副本, 逐位等于输入** —— 见模块
    docstring 的不变量 2/3。
    """
    r = np.asarray(rlat)
    delta = ridge_line_delta(_lon_axis(r.size) if lon is None else lon,
                             params, hemi)
    if delta is None:
        return r.copy()
    return np.clip(r.astype(np.float64) + delta, 0.0, 90.0).astype(r.dtype)


def ridge_core(dy, d, params: Optional[dict] = None):
    """单个副高中心的**各向异性高斯核**(无量纲, 0..1), 四向伸展各自独立。

        dy = 纬度 - 中心纬度        (`rlat`)
        d  = wrap_lon180(经度 - 中心经度)   (`core.py:1549-1550`)

    核的形式与 `core.py:1551-1554` 完全一致(`exp(-(·/s)²)`, 不是
    `exp(-(·/s)²/2)`; 半高在 `|·| = s·sqrt(ln2) ≈ 0.8326·s`):

        core = exp( -(dy_n²) - (dy_s²) - (dx_w²) - (dx_e²) )
        dy_n = max(0, dy/shExtN)      # 北伸
        dy_s = min(0, dy/shExtS)      # 南伸
        dx_w = min(0,  d/shExtW)      # 西伸
        dx_e = max(0,  d/shExtE)      # 东伸

    **默认参数下与 `core.py:1551-1554` 的算式逐位一致**(dtype 随入参, 见模块
    docstring 的不变量 1)。四个方向严格正交: 改 `shExtN` 只影响 `dy > 0` 一侧,
    南/东/西三侧的取值连一个 bit 都不动(其余同理, 参见测试 ④)。
    """
    Q = ridge_params(params)
    dy = np.asarray(dy)
    d = np.asarray(d)
    # 注意: 除法**留在 minimum/maximum 里面**, 与原式 core.py:1551-1553 同形,
    # 这样默认尺度下是同一个操作、位级相同(见模块 docstring)。
    dy_n = np.maximum(0.0, dy / Q['shExtN'])
    dy_s = np.minimum(0.0, dy / Q['shExtS'])
    dx_w = np.minimum(0.0, d / Q['shExtW'])
    dx_e = np.maximum(0.0, d / Q['shExtE'])
    return np.exp(-(dy_n * dy_n) - (dy_s * dy_s)
                  - (dx_w * dx_w) - (dx_e * dx_e))


def ridge_anomaly(dy, d, ramp, params: Optional[dict] = None):
    """单个副高中心对副高**数值场**(`env['pEnv']`, hPa)的贡献。

        anomaly = ramp * ridge_core(dy, d, params)

    `ramp` 是该中心的振幅(hPa), 取自 `RIDGES`(core.py:437-446)。
    算式的算子顺序与原式 `ramp * np.exp(...)`(core.py:1554)一致 ⇒ 默认参数下
    逐位一致, 可以直接替换 `core.py:1548-1554` 循环体内的那一行:

        for rlat, rlon, ramp in RIDGES:
            d = LON - rlon
            d = (d + 180.0) % 360.0 - 180.0
            pe += ridge.ridge_anomaly(LAT - rlat, d, ramp, P)
    """
    return ramp * ridge_core(dy, d, params)
