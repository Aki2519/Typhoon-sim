# app/paint_projection.py
"""绘画投影：等距圆柱 / **圆锥（Lambert 正形）** —— 从 `xrq-dolphin.png` 反推。

本模块是 P4 从 QQ 机器人样式基准图里**反推**出来的投影层，供 P3（Ty4 保真）与
P5（界面开关）接线用。**纯标准库 + math**，不依赖 pygame，可单独测试。

────────────────────────────────────────────────────────────────────────
一、结论（**实测拟合**，不是猜的）

基准图 `xrq-dolphin.png`（1837×1224）里的经纬网是**虚线**，颜色恰好是
`(102,204,153)`（与底图海洋 `(0,34,68)` / 陆地 `(48,112,80)` / 水印
`(62,88,114)`、`(99,147,123)` 都不冲突）⇒ 可以**逐像素无损抠出网格**（3418 px）。

抠出来之后，两条**互相独立**的几何事实各自给出同一个答案：

1. **经线是过同一点的直线束**。用 RANSAC 找出 8 条经线（内点 163–217、延伸
   1185–1261 px），两两求交 **28 对，交点全部落在 (918.0, −1768.5)**，
   `x` 标准差 **0.0 px**、`y` 标准差 **0.3 px**。这是**圆锥投影的定义性质**
   （经线是自圆锥顶点出发的射线）；等距圆柱投影的经线是**平行**的，交点会在无穷远。
2. **纬线是同心圆弧**。以该顶点为圆心，6 条纬线的半径标准差只有
   **0.10–0.29 px**。

由 (1) 得圆锥常数 `n`，由 (2) 得 `ρ(φ)` 的族。**两个来源的 n 相对差 2.7e-5**：

| 来源 | 拟合式 | 结果 |
|---|---|---|
| 经线角 | `σ = n·(λ − λ0)` | **n = 0.448550**，λ0 = **152.3983°E**，角残差 RMS **0.00066°**（≈0.065 px） |
| 纬线半径 | `ρ = A·tan(π/4+φ/2)^(−n)` | **n = 0.448562**，A = 2955.116 px，残差 RMS **0.0030 px**、max 0.0042 px |

**三个候选族的对比**（同样 6 条纬线，自由参数最小二乘）：

| 族 | RMS 残差 | 最大残差 |
|---|---|---|
| **Lambert 正形圆锥** | **0.003 px** | **0.004 px** |
| 等距圆锥 (equidistant conic) | 3.816 px | 4.890 px |
| Albers 等积 | 7.551 px | 9.736 px |

⇒ **是 Lambert 正形圆锥**，差 3 个数量级，没有歧义。

**独立验证**：把本模块的投影**反解**回经纬度，用仓库里的全球陆地掩码
（`simulator/env/model/land_cell.npz`，0.5°）去对图像里的绿陆地区域，
**IoU = 0.8833**；把经度方向翻过来只有 **0.0001** ⇒ 方向与参数都对。

────────────────────────────────────────────────────────────────────────
二、公式（可直接复现）

球面 Lambert 正形圆锥（中央经线 λ0，圆锥常数 n）：

    t(φ)   = tan(π/4 + φ/2)
    ρ(φ)   = ρ_k · ( t(φ) / t(φ_k) )^(−n)          # ρ_k = 参考纬度 φ_k 处的半径(px)
    σ(λ)   = n · (λ − λ0)                          # 弧度; 东侧 σ>0
    X      = ρ·sin(σ)
    Y      = ρ·cos(σ)                              # Y 向下为正(自圆锥顶点起算)

`n` 与标准纬线的关系（若要按标准纬线表达）：
    n = ln(cos φ1 / cos φ2) / ln( tan(π/4+φ2/2) / tan(π/4+φ1/2) )
本图拟合出的 n = 0.44855（等价于单条切纬线 φ ≈ 26.65°，或对称标准纬线 16.7°/36.7°）。
**注意：本图不是"整度"标准纬线**（20/40 → n=0.5026、30/60 → n=0.7157，都不符），
所以这里以 n 与 ρ_k 为第一性参数，不去倒推标准纬线。

────────────────────────────────────────────────────────────────────────
三、`ctx` 契约（P5/P3 接线用；字段名在此**冻结**）

`make_ctx(name, bounds, size, pad, **over)` 产出，`project()` 消费：

| 字段 | 类型 | 含义 |
|---|---|---|
| `mode` | str | `"equirect"` / `"conic"`（= 传入的 name） |
| `size` | (w, h) | 画布像素尺寸 |
| `bounds` | (lon0, lon1, lat0, lat1) | 请求覆盖的经纬度框（度；lon 0–360） |
| `lon_c` | float | 中央经线（度） |
| `lat_c` | float | 参考纬度（度，默认取 bounds 中值） |
| `scale` | float | 投影单位 → 像素 的等比缩放 |
| `ox`, `oy` | float | 平移（像素）：`p = (ox + scale·X, oy + scale·Y)` |
| `n` | float | 圆锥常数（仅 conic 有意义；equirect 恒 0） |
| `rho_k` | float | 参考纬度处的 ρ（px 单位，仅 conic） |
| `unit` | str | 投影单位说明：`"px@reference"`（沿用基准图的像素尺度） |

等距圆柱的解析式（`equirect`）：

    x = ox + scale·cos(lat_c)·(λ − lon_c)
    y = oy − scale·(φ − lat_c)

（`cos(lat_c)` 使经度方向按中纬等比，避免高纬横向拉伸；`lat_c=0` 时等价于
"每度等宽"的普通等距圆柱。）

圆锥的解析式（`conic`）：见上面 §二，`project()` 内部完成。

**基准图的原始取景**（若想逐像素复现 `xrq-dolphin.png` 的画幅）：
`n=0.44855, lon_c=152.3983, rho_k=2955.116, lat_c=0, scale=1.0,
ox=918.0, oy=-1768.5`（此时 `project` 直接返回基准图像素坐标）。
`CONIC_REFERENCE` 常量就是这套值。
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, Sequence, Tuple

__all__ = [
    'PROJECTIONS', 'project', 'project_many', 'inverse', 'inverse_arrays',
    'make_ctx', 'CONIC_REFERENCE', 'sample_graticule',
]

#: 支持的投影名（与 P5 的 `cfg["projection"]` 同名）
PROJECTIONS = ("equirect", "conic")

#: 从 `xrq-dolphin.png` 拟合出的 Lambert 正形圆锥参数（**基准图原生像素尺度**）
CONIC_REFERENCE: Dict[str, float] = {
    'n': 0.448550,          # 圆锥常数（经线角 0.448550 / 纬线半径 0.448562）
    'lon_c': 152.3983,      # 中央经线（度 E）
    'lat_c': 0.0,           # 参考纬度（度）
    'rho_k': 2955.116,      # ρ(lat_c)（px）
    'scale': 1.0,           # 投影单位 -> 像素
    'ox': 918.0,            # 圆锥顶点 x（基准图像素）
    'oy': -1768.5,          # 圆锥顶点 y（基准图像素；负 = 在画面上方）
}

_TWO_PI = 2.0 * math.pi


def _t(lat_deg: float) -> float:
    """Lambert 的 t(φ) = tan(π/4 + φ/2)；极点附近夹取避免 overflow。"""
    phi = math.radians(max(-89.99, min(89.99, float(lat_deg))))
    return math.tan(math.pi / 4.0 + phi / 2.0)


def _raw_conic(lon: float, lat: float, ctx: Dict) -> Tuple[float, float]:
    """圆锥原始投影（未做 scale/平移）：X 东向、Y 向南（自顶点起算）。"""
    n = ctx['n']
    dlon = (float(lon) - ctx['lon_c'] + 180.0) % 360.0 - 180.0
    sig = n * math.radians(dlon)
    rho = ctx['rho_k'] * (_t(lat) / _t(ctx['lat_c'])) ** (-n)
    return rho * math.sin(sig), rho * math.cos(sig)


def _raw_equirect(lon: float, lat: float, ctx: Dict) -> Tuple[float, float]:
    """等距圆柱原始投影（未做 scale/平移）：X 东向、Y 向北。"""
    dlon = (float(lon) - ctx['lon_c'] + 180.0) % 360.0 - 180.0
    return (math.cos(math.radians(ctx['lat_c'])) * dlon,
            float(lat) - ctx['lat_c'])


def project(name: str, lon_deg: float, lat_deg: float, ctx: Dict) -> Tuple[float, float]:
    """把经纬度投影成画布像素 `(x, y)`。**主接口**（P5 接线用）。

    `name` 与 `ctx['mode']` 二选一为准：显式传 `name` 时以 `name` 为准
    （这样调用方可以只改 `cfg["projection"]` 而不重建 ctx）。
    """
    mode = str(name or ctx.get('mode') or 'equirect')
    if mode == 'conic':
        X, Y = _raw_conic(lon_deg, lat_deg, ctx)
    elif mode == 'equirect':
        X, Y = _raw_equirect(lon_deg, lat_deg, ctx)
        Y = -Y                                  # 屏幕 y 向下 ⇒ 北在上
    else:
        raise ValueError('未知投影 %r（可选 %s）' % (mode, PROJECTIONS))
    s = ctx.get('scale', 1.0)
    return ctx.get('ox', 0.0) + s * X, ctx.get('oy', 0.0) + s * Y


def project_many(name: str, lons: Iterable[float], lats: Iterable[float],
                 ctx: Dict) -> list:
    """`project` 的批量版（渲染密集折线时省函数调用）。"""
    mode = str(name or ctx.get('mode') or 'equirect')
    s = ctx.get('scale', 1.0)
    ox, oy = ctx.get('ox', 0.0), ctx.get('oy', 0.0)
    out = []
    for lo, la in zip(lons, lats):
        if mode == 'conic':
            X, Y = _raw_conic(lo, la, ctx)
        else:
            X, Y = _raw_equirect(lo, la, ctx)
            Y = -Y
        out.append((ox + s * X, oy + s * Y))
    return out


def inverse(name: str, x: float, y: float, ctx: Dict) -> Tuple[float, float]:
    """画布像素 → 经纬度（`inverse(project(p)) == p` 到浮点精度）。

    用途：把图上的点/轨迹**反解**成经纬度（本模块就是靠它做陆地掩码 IoU 验证的），
    以及做命中测试。**逐点（标量）版**；整画布级反投影请用 `inverse_arrays`。
    """
    mode = str(name or ctx.get('mode') or 'equirect')
    s = ctx.get('scale', 1.0) or 1.0
    X = (float(x) - ctx.get('ox', 0.0)) / s
    Y = (float(y) - ctx.get('oy', 0.0)) / s
    if mode == 'equirect':
        lon = ctx['lon_c'] + X / max(1e-9, math.cos(math.radians(ctx['lat_c'])))
        lat = ctx['lat_c'] - Y
        return (lon + 360.0) % 360.0, lat
    if mode != 'conic':
        raise ValueError('未知投影 %r' % (mode,))
    rho = math.hypot(X, Y)
    sig = math.atan2(X, Y)
    lon = ctx['lon_c'] + math.degrees(sig) / ctx['n']
    rho = max(rho, 1e-9)
    ratio = ctx['rho_k'] / rho
    lat = math.degrees(2.0 * (math.atan(ratio ** (1.0 / ctx['n'])) - math.pi / 4.0))
    return (lon + 360.0) % 360.0, lat


def inverse_arrays(name: str, xs, ys, ctx: Dict):
    """**矢量版**反投影（需要 numpy）—— 返回 `(lon, lat)` 两个 `ndarray`。

    为什么单独一个函数：`inverse` 要保持"纯 math、不依赖 numpy"以便单独测试；
    而渲染器要一次反投影**整幅画布**（百万级像素），逐点调太慢。
    两者共用同一套公式（本函数的圆锥分支与 `inverse` 逐项对应）。
    """
    import numpy as np
    mode = str(name or ctx.get('mode') or 'equirect')
    s = ctx.get('scale', 1.0) or 1.0
    X = (np.asarray(xs, dtype=np.float64) - float(ctx.get('ox', 0.0))) / s
    Y = (np.asarray(ys, dtype=np.float64) - float(ctx.get('oy', 0.0))) / s
    if mode == 'equirect':
        lon = ctx['lon_c'] + X / max(1e-9, math.cos(math.radians(ctx['lat_c'])))
        lat = ctx['lat_c'] - Y
        return (lon + 360.0) % 360.0, lat
    if mode != 'conic':
        raise ValueError('未知投影 %r' % (mode,))
    rho = np.maximum(np.hypot(X, Y), 1e-9)
    sig = np.arctan2(X, Y)
    lon = ctx['lon_c'] + np.degrees(sig) / ctx['n']
    ratio = ctx['rho_k'] / rho
    lat = np.degrees(2.0 * (np.arctan(ratio ** (1.0 / ctx['n'])) - math.pi / 4.0))
    return (lon + 360.0) % 360.0, lat


def _boundary_samples(lon0, lon1, lat0, lat1, k: int = 41):
    """经纬度框的边界采样点（含四角与边中点），用于求投影后的包围盒。"""
    pts = []
    for i in range(k):
        f = i / (k - 1.0)
        pts.append((lon0 + (lon1 - lon0) * f, lat0))
        pts.append((lon0 + (lon1 - lon0) * f, lat1))
        pts.append((lon0, lat0 + (lat1 - lat0) * f))
        pts.append((lon1, lat0 + (lat1 - lat0) * f))
    return pts


def make_ctx(name: str, bounds: Sequence[float], size: Sequence[int],
             pad: float = 0.0, **over) -> Dict:
    """按"把经纬度框装进画布"算出 `ctx`。

    参数
    ----
    name   : `"equirect"` / `"conic"`
    bounds : `(lon0, lon1, lat0, lat1)`，度；lon 用 0–360
    size   : `(w, h)` 画布像素
    pad    : 四边留白（像素），默认 0
    over   : 覆盖任何 ctx 字段（如 `lon_c=` / `scale=` / `ox=` / `oy=`）；
             conic 的 `n`/`rho_k` 默认取 `CONIC_REFERENCE`

    做法：**先把投影参数填好，再采样边界求投影包围盒，最后等比缩放+居中**。
    圆锥投影下经纬度框是"扇形"，所以只对边界采样求包围盒（比对角两点稳）。
    """
    lon0, lon1, lat0, lat1 = (float(v) for v in bounds)
    W, H = int(size[0]), int(size[1])
    ctx: Dict = {
        'mode': str(name),
        'size': (W, H),
        'bounds': (lon0, lon1, lat0, lat1),
        'lon_c': (lon0 + lon1) * 0.5,
        'lat_c': (lat0 + lat1) * 0.5,
        'scale': 1.0,
        'ox': 0.0,
        'oy': 0.0,
    }
    if str(name) == 'conic':
        ctx.update({'n': CONIC_REFERENCE['n'], 'rho_k': CONIC_REFERENCE['rho_k'],
                    'lat_c': CONIC_REFERENCE['lat_c']})
    elif str(name) != 'equirect':
        raise ValueError('未知投影 %r（可选 %s）' % (name, PROJECTIONS))
    ctx.update(over)
    # 调用方显式给了取景(scale/ox/oy)就照用, 不再自动装箱
    auto = not any(k in over for k in ('scale', 'ox', 'oy'))
    if auto:
        xs, ys = [], []
        for lo, la in _boundary_samples(lon0, lon1, lat0, lat1):
            x, y = project(name, lo, la, {'mode': ctx['mode'], 'lon_c': ctx['lon_c'],
                                          'lat_c': ctx['lat_c'], 'n': ctx.get('n'),
                                          'rho_k': ctx.get('rho_k'),
                                          'scale': 1.0, 'ox': 0.0, 'oy': 0.0})
            xs.append(x); ys.append(y)
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        bw = max(1e-9, x1 - x0)
        bh = max(1e-9, y1 - y0)
        s = min((W - 2.0 * pad) / bw, (H - 2.0 * pad) / bh)
        ctx['scale'] = s
        # 缩放后的内容包围盒在画布(含 pad)里居中
        ctx['ox'] = (W - s * bw) * 0.5 - s * x0
        ctx['oy'] = (H - s * bh) * 0.5 - s * y0
    return ctx


def sample_graticule(ctx: Dict, lons: Sequence[float] = tuple(range(0, 360, 10)),
                     lats: Sequence[float] = tuple(range(-80, 81, 10)),
                     step: float = 1.0, clip=None):
    """生成经纬网的折线段（供渲染器画虚线用）。

    返回 `[(kind, value, [(x, y), ...]), ...]`；`kind` 为 `'lon'` / `'lat'`。
    `clip` 给 `(x0, y0, x1, y1)` 时可只保留与该框相交的段。
    """
    name = ctx['mode']
    lon0, lon1, lat0, lat1 = ctx.get('bounds', (0.0, 360.0, -90.0, 90.0))
    out = []
    nstep = max(2, int(round((lat1 - lat0) / step)) + 1)
    for lo in lons:
        if lo < lon0 - 1e-9 or lo > lon1 + 1e-9:
            continue
        pts = [project(name, lo, lat0 + (lat1 - lat0) * i / (nstep - 1.0), ctx)
               for i in range(nstep)]
        out.append(('lon', lo, pts))
    mstep = max(2, int(round((lon1 - lon0) / step)) + 1)
    for la in lats:
        if la < lat0 - 1e-9 or la > lat1 + 1e-9:
            continue
        pts = [project(name, lon0 + (lon1 - lon0) * i / (mstep - 1.0), la, ctx)
               for i in range(mstep)]
        out.append(('lat', la, pts))
    if clip is not None:
        x0, y0, x1, y1 = clip
        kept = []
        for kind, val, pts in out:
            if any(x0 <= px <= x1 and y0 <= py <= y1 for px, py in pts):
                kept.append((kind, val, pts))
        return kept
    return out
