# app/paint_render_qq.py
"""QQ 机器人样式渲染器（台风吧官群风格）—— 独立于 `paint_render.py`。

反推自基准图 `xrq-dolphin.png`（1837×1224，26 Dolphin）。**不改 `paint_render.py`**，
只复用它的调用约定（`render(tys, mode, region, map_surface) -> (Surface, stats)`），
所以 P5 可以用一个开关在两者之间切。

────────────────────────────────────────────────────────────────────────
一、配色 —— **全部从基准图采样**（不是凭感觉）

`xrq-dolphin.png` 只有 3022 种颜色、前 5 色覆盖 95.7%，所以色表可以**精确取到**：

| 角色 | RGB | 原图占比 | 取法 |
|---|---|---|---|
| 海洋 | `(0, 34, 68)` | 78.08 % | 出现最多的颜色 |
| 陆地 | `(48, 112, 80)` | 15.73 % | 第二多 |
| 水印(叠海洋) | `(62, 88, 114)` | 1.151 % | 第三多 |
| 水印(叠陆地) | `(99, 147, 123)` | 0.434 % | |
| **经纬网** | `(102, 204, 153)` | 0.111 % | 见下 |
| 连线 | `(255, 255, 255)` | 0.035 % | |
| 标注文字 | `(202, 218, 210)` | 0.034 % | 左/下边缘的文本色 |
| 强度色 × 9 | 见 `QQ_SCALE` | 0.890 % 合计 | 按亮度/色相排序后与风压档对应 |

**经纬网色怎么定下来的**：不是猜的。把 `(102,204,153)` 单独做成掩码后，它是
一张**干净到没有一条杂线的虚线经纬网**（3418 px，无陆地/标记/文字污染）——
正是靠它才把投影拟合到 0.003 px。反过来这也证明该色就是网格色。

二、强度色序（9 档，弱→强）

    (0,250,244) 青 → (94,186,255) 浅蓝 → (102,204,153) 青绿 → (255,255,204) 近白黄
    → (255,231,117) 淡黄 → (255,193,64) 琥珀 → (255,143,32) 橙
    → (255,96,96) 红 → (255,135,135) 浅红

分档阈值由 `calibrate_thresholds()` 用**分位数匹配**标定：让渲染出的 9 色计数
尽量贴近基准图实测计数（同一批 26 Dolphin 的 99 个报点）。默认值见 `QQ_BINS`。

三、`opts` 字段

| 键 | 默认 | 含义 |
|---|---|---|
| `projection` | `"conic"` | `"equirect"` / `"conic"`（与 `paint_projection.PROJECTIONS` 同名） |
| `output_size` | `None` | `(w, h)`；`None` = 按 bounds 自动 |
| `grid` | `True` | 画虚线经纬网 |
| `grid_step` | `10` | 网格间隔（度） |
| `labels` | `True` | 画经纬度标注 |
| `watermark` | `True` | `@xrq` 斜向重复水印 |
| `margin_w` / `margin_h` | `0` | 同 `paint_render`（内容留白） |
| `aa` | `True` | 关闭则退回 pygame 基础图元（**仅供对照**，会锯齿） |
| `marker_r` | `9.0` | 标记半径（px） |
| `line_w` | `1` | 连线宽 |

四、抗锯齿

**从第一行起就用 `app.paint_aa`**：圆/三角/方块/连线/风圈全部走它的超采样精灵；
海岸线走 **2× 陆地掩码超采样降采样**。因此边缘一定存在中间 alpha 像素
（见报告 §5 的确定性计数）。
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pygame

from . import paint_aa
from . import paint_projection as PP

__all__ = ["QQ_COLORS", "QQ_SCALE", "QQ_BINS", "QQTrackRenderer",
           "color_for_wind", "calibrate_thresholds", "shape_for_code"]

# ── 实测采样自 xrq-dolphin.png 的配色 ──────────────────────────────────────
QQ_COLORS: Dict[str, Tuple[int, int, int]] = {
    "ocean": (0, 34, 68),
    "land": (48, 112, 80),
    "watermark": (62, 88, 114),
    "watermark_land": (99, 147, 123),
    "grid": (102, 204, 153),
    "line": (255, 255, 255),
    "label": (202, 218, 210),
}

#: 强度色（弱 → 强），实测取自原图的标记像素
QQ_SCALE: Tuple[Tuple[int, int, int], ...] = (
    (0, 250, 244), (94, 186, 255), (102, 204, 153), (255, 255, 204),
    (255, 231, 117), (255, 193, 64), (255, 143, 32), (255, 96, 96),
    (255, 135, 135),
)

#: 亮度/色相序下的分档上界（kt）。由 `calibrate_thresholds` 分位数匹配标定：
#: 让 26 Dolphin 的 99 个报点落进 9 个色桶后的**计数分布**尽量贴近基准图实测计数。
QQ_BINS: Tuple[float, ...] = (20.0, 25.0, 30.0, 38.0, 48.0, 62.0, 80.0, 105.0)

#: 基准图里 9 个强度色的**实测像素计数**（用于直方图距离与分档标定）
QQ_REF_COUNTS: Tuple[int, ...] = (1650, 6915, 2507, 1732, 3811, 5206, 2582, 1203, 423)


def color_for_wind(w_kt: float) -> Tuple[int, int, int]:
    """风速(kt) → 强度色。弱→强按 `QQ_BINS` 分档。"""
    for i, hi in enumerate(QQ_BINS):
        if w_kt <= hi:
            return QQ_SCALE[i]
    return QQ_SCALE[-1]


def calibrate_thresholds(winds: Sequence[float],
                         ref_counts: Sequence[int] = QQ_REF_COUNTS,
                         n_iter: int = 400) -> Tuple[float, ...]:
    """用**分位数匹配**把 `QQ_BINS` 标定到基准图的 9 色计数上。

    思路：把参考图的 9 个色桶计数归一化成累计分布，再取风序列的对应分位数作为
    档位边界 ⇒ 渲染后的色计数分布与基准图尽量一致。返回新的分档上界。
    这是**可复现的定量标定**，不是手调。
    """
    w = np.sort(np.asarray(list(winds), dtype=float))
    if w.size == 0:
        return tuple(QQ_BINS)
    c = np.asarray(list(ref_counts), dtype=float)
    c = c / c.sum()
    c = c[:-1]                     # 前 8 个累计边界（第 9 桶是余量）
    cum = np.cumsum(c)
    bins = [float(np.quantile(w, min(0.999, max(0.001, q)))) for q in cum]
    # 单调化，避免并列风值造成的逆序
    out = []
    for b in bins:
        while out and b < out[-1] + 0.5:
            b = out[-1] + 0.5
        out.append(b)
    return tuple(out)


def shape_for_code(code: str) -> str:
    """报点类型码 → 标记形状。

    实测基准图里三种形状都在用（`fill` 面积占比：圆≈0.79 / 三角≈0.5 / 方块≈1.0）：
      · 后期/变性(`EX`/`SS`/`SD`) → **方块**（原图末端那段就是方块）
      · 预报/前身弱报点(`WV`/`DB`/`LO`) → **三角**（原图两端各有一段三角）
      · 其余（`TD`/`TS`/`TY`/`ST`/`MD`）→ **圆**
    """
    c = (code or "").strip().upper()
    if c in ("EX", "SS", "SD"):
        return "square"
    if c in ("WV", "DB", "LO", "MD"):
        return "triangle"
    return "circle"


# ── 陆地掩码：(10800x5400, 30 px/°) **按输出画布裁剪窗口**后采样 ──
#: 高分辨率底图（alpha=255 陆 / alpha=0 海）。repo 内只读使用。
LAND_PNG = os.path.join("map", "land.png")
#: 源图分辨率（px/°）—— 10800/360 = 30
LAND_PPD = 30.0
#: 裁剪窗口在经纬度上多留的余量（度），防边界采样溢出
LAND_MARGIN_DEG = 1.0
#: 相对**源图**的采样倍率：1.0 = 直接用 30 px/° 的原始分辨率（默认）。
#: 调小可省内存/时间但会让海岸线变钝；调大无意义（源图没有更多信息）。
LAND_RES = 1.0
#: 若 >0: 把裁剪窗口**降采样**到该 px/度 再采样(仅供 A/B 对比块状程度 / 省内存)。
LAND_PPD_MAX = 0.0

#: 海岸线超采样倍率(1 = 关)。配合源图双线性采样一起给出平滑边缘。
COAST_SS = 2
_LAND_IMG = {"tried": False, "im": None}
#: 全局兜底掩码（0.5° land_cell.npz）。**注意行序与 land.png 相反**：
#: npz 的 row 0 = 90°S；land.png 的 row 0 = 90°N。这个差异曾导致陆地上下翻转。
_LAND_FALLBACK = {"tried": False, "frac": None, "dlon": 0.5, "dlat": 0.5}


def _open_land_png():
    """懒打开 （**不** load 全量到内存）。"""
    if _LAND_IMG["tried"]:
        return _LAND_IMG["im"]
    _LAND_IMG["tried"] = True
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _LAND_IMG["im"] = Image.open(os.path.join(root, LAND_PNG))
    except Exception:
        _LAND_IMG["im"] = None
    return _LAND_IMG["im"]


def _fallback_frac():
    if _LAND_FALLBACK["tried"]:
        return _LAND_FALLBACK
    _LAND_FALLBACK["tried"] = True
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with np.load(os.path.join(root, "simulator", "env", "model", "land_cell.npz")) as z:
            a = np.asarray(z["landfrac"], dtype=np.float32)
        _LAND_FALLBACK.update({"frac": a, "dlon": 360.0 / a.shape[1],
                               "dlat": 180.0 / a.shape[0]})
    except Exception:
        _LAND_FALLBACK["frac"] = None
    return _LAND_FALLBACK


def _land_window(lon0, lon1, lat0, lat1):
    """裁出源图上覆盖该经纬度窗口的一块，返回 。

     是 float32 的**陆地占比 0..1**（源图是二值 alpha，裁剪 + 双线性插值后
    天然给出 0..1 的边缘覆盖率 ⇒ 海岸线抗锯齿）。**arr 的 row 0 = 北(纬度大)**。
    内存只与窗口有关（例如 75°x48° 窗口 ≈ 3.2 M px ≈ 13 MB float32），
    **不会**把 10800x5400（233 MB）常驻。
    """
    im = _open_land_png()
    if im is None:
        return None
    m = LAND_MARGIN_DEG
    lon0 = max(0.0, lon0 - m); lon1 = min(360.0, lon1 + m)
    lat0 = max(-90.0, lat0 - m); lat1 = min(90.0, lat1 + m)
    if lon1 - lon0 < 1e-6 or lat1 - lat0 < 1e-6:
        return None
    x0 = int(max(0, np.floor(lon0 * LAND_PPD)))
    x1 = int(min(im.size[0], np.ceil(lon1 * LAND_PPD)))
    # land.png: y = (90 - lat) * ppd  ⇒ 纬度越大 y 越小
    y0 = int(max(0, np.floor((90.0 - lat1) * LAND_PPD)))
    y1 = int(min(im.size[1], np.ceil((90.0 - lat0) * LAND_PPD)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if LAND_PPD_MAX > 0.0 and LAND_PPD > LAND_PPD_MAX:
        scale = LAND_PPD_MAX / LAND_PPD
        w = max(2, int((x1 - x0) * scale)); h = max(2, int((y1 - y0) * scale))
        tile = im.crop((x0, y0, x1, y1)).getchannel("A").resize((w, h), Image.BOX)
        ppd = (x1 - x0) * scale / max(1e-9, (x1 - x0)) * LAND_PPD
        ppd = LAND_PPD_MAX
        return np.asarray(tile, dtype=np.float32) / 255.0, float(x0), float(y0), float(ppd)
    if LAND_RES != 1.0:
        w = max(2, int((x1 - x0) * LAND_RES)); h = max(2, int((y1 - y0) * LAND_RES))
        tile = im.crop((x0, y0, x1, y1)).getchannel("A").resize((w, h))
        ppd = LAND_PPD * (w / float(x1 - x0))
        arr = np.asarray(tile, dtype=np.float32) / 255.0
        return arr, float(x0), float(y0), ppd
    tile = im.crop((x0, y0, x1, y1)).getchannel("A")
    arr = np.asarray(tile, dtype=np.float32) / 255.0
    return arr, float(x0), float(y0), LAND_PPD


def _bilinear(arr, fx, fy):
    """双线性采样（fx/fy 是**像素坐标**，可浮点）。越界夹取。"""
    ny, nx = arr.shape
    fx = np.clip(fx, 0.0, nx - 1.0001)
    fy = np.clip(fy, 0.0, ny - 1.0001)
    ix = fx.astype(np.int32); iy = fy.astype(np.int32)
    dx = (fx - ix).astype(np.float32); dy = (fy - iy).astype(np.float32)
    a = arr[iy, ix]; b = arr[iy, ix + 1]
    c = arr[iy + 1, ix]; d = arr[iy + 1, ix + 1]
    return (a * (1.0 - dx) * (1.0 - dy) + b * dx * (1.0 - dy)
            + c * (1.0 - dx) * dy + d * dx * dy)


def _canvas_window(ctx, W, H, pad_px=2.0):
    """画布覆盖的经纬度包围盒（采样四条边 + 四角后取极值）。"""
    xs = np.concatenate([np.linspace(-pad_px, W + pad_px, 48)] * 2
                        + [np.full(48, -pad_px), np.full(48, W + pad_px)])
    ys = np.concatenate([np.full(48, -pad_px), np.full(48, H + pad_px)]
                        + [np.linspace(-pad_px, H + pad_px, 48)] * 2)
    lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
    # ⚠ **不要**把 lon 归一化到 -180..180: land.png 是 0..360, 混用会让裁剪窗口
    #   退化成整圈 [0,360] 并把西侧全夹到 lon=0(实测表现为右半张图多出一大块假陆地)。
    span = float(lon.max() - lon.min())
    if span > 300.0:                       # 跨 0° 经线: 交给兜底掩码, 不做窗口裁剪
        return None
    return (float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max()))


def _sample_land_window(win, ctx, xs, ys):
    """画布像素 → 陆地占比 0..1（走**投影**，不是拉伸图片）。"""
    arr, wx0, wy0, ppd = win
    lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
    fx = lon * ppd - wx0
    fy = (90.0 - lat) * ppd - wy0        # land.png 的 row 0 是 90°N
    return _bilinear(arr, fx, fy)


class QQTrackRenderer:
    """QQ 机器人样式的路径图渲染器。接口与 `paint_render.TrackMapRenderer` 同形。"""

    def __init__(self, opts: Optional[dict] = None) -> None:
        o = dict(opts or {})
        self.projection = str(o.get("projection", "conic"))
        self.output_size = o.get("output_size")
        self.grid = bool(o.get("grid", True))
        self.grid_step = int(o.get("grid_step", 10))
        self.labels = bool(o.get("labels", True))
        self.watermark = bool(o.get("watermark", True))
        self.margin_w = float(o.get("margin_w", 0.0))
        self.margin_h = float(o.get("margin_h", 0.0))
        # ── 画布下限：照 Typhoon4 的 Width / Height ─────────────────────────
        # Ty4 的公式(规格书 §2.2, 语言包键 57-60 原文确证 "最小宽度"/"最小高度"):
        #     W = max(Width,  track_w + 2*WidthSpace)
        #     H = max(Height, track_h + 2*HeightSpace)
        # 默认取 Settings.ini 的 Width=1920 / Height=1080。
        # 原先这里下限只有 320x240 ⇒ 小台风会渲出 320x259 这种"缩略图"。
        # ⚠ 关键差别: Ty4 的 px/° **恒定**, 所以画布变大是"**看到更多地图**",
        #   不是"把轨迹放大"。本渲染器照此办理(见 render 里"同一比例重新居中")。
        self.min_w = int(o.get("min_w", 1920))
        self.min_h = int(o.get("min_h", 1080))
        #: 内存护栏(我们加的, Ty4 没有): 超大包围盒不无限涨
        self.max_w = int(o.get("max_w", 4096))
        self.max_h = int(o.get("max_h", 3072))
        #: 内容 px/°(Ty4 是 30; 本样式按基准图观感取 ~22.6)
        self.px_per_deg = float(o.get("px_per_deg", 22.6))
        self.aa = bool(o.get("aa", True))
        self.marker_r = float(o.get("marker_r", 9.0))
        self.line_w = int(o.get("line_w", 1))
        self.coast = bool(o.get("coast", True))
        self.map_surface = o.get("map_surface")
        #: 显式给 ctx（例如要和基准图逐像素同框对比时）—— 给了就不自动装箱
        self.ctx_override = o.get("ctx")
        self._ctx: Dict = {}
        self._k = 1.0
        self._land_src = None

    # ── 工具: 统一取点, 兼容 Ty 对象与 b-deck 行 ──
    @staticmethod
    def _storm_pts(st) -> List[dict]:
        pts = getattr(st, "pts", None)
        if pts is None and isinstance(st, dict):
            pts = st.get("pts") or st.get("points")
        return list(pts or [])

    @staticmethod
    def _lo_la(p) -> Tuple[float, float]:
        """取 (经度, 纬度)。**兼容三种输入**（2026-10-02 修）。

        修的是什么: 原先两处写死 `p["lon"]` / `p["lat"]`, 那只对**本模块探针用的 dict**
        有效; app 真实的 `TrackPoint`(app/typhoon.py:23) 用的是 `lo`/`la` 字段名
        (`paint_render.py:103` 就是 `p.get('lo')`)。于是**在 app 里选 QQ 样式会直接抛**
        `AttributeError: 'TrackPoint' object has no attribute 'lon'` ——
        也就是说 QQ 样式**此前只在离线探针里跑过, 从未接过 app 的真实数据**。

        支持:
          · app 的 `TrackPoint`: `.get('lo')` / `.get('la')`, 或属性 `.lo` / `.la`
          · 本模块探针/离线 dict: `{'lon': .., 'lat': ..}`
          · b-deck 行 dict: `{'lo': .., 'la': ..}`
        """
        if isinstance(p, dict):
            if "lon" in p or "lat" in p:
                return (float(p.get("lon", 0.0) or 0.0),
                        float(p.get("lat", 0.0) or 0.0))
            return (float(p.get("lo", 0.0) or 0.0),
                    float(p.get("la", 0.0) or 0.0))
        try:                                   # TrackPoint 有 __getitem__/get
            return (float(p.get("lo", 0.0) or 0.0),
                    float(p.get("la", 0.0) or 0.0))
        except Exception:
            return (float(getattr(p, "lo", 0.0) or 0.0),
                    float(getattr(p, "la", 0.0) or 0.0))

    def _all_bounds(self, storms) -> Tuple[float, float, float, float]:
        lo0, lo1, la0, la1 = 1e9, -1e9, 1e9, -1e9
        for st in storms:
            for p in self._storm_pts(st):
                lo, la = self._lo_la(p)
                lo0 = min(lo0, lo); lo1 = max(lo1, lo)
                la0 = min(la0, la); la1 = max(la1, la)
        if lo0 > lo1:
            return (100.0, 180.0, 0.0, 40.0)
        return (lo0, lo1, la0, la1)

    def _shapes(self, surf, kind, color, center):
        """一个标记 —— 全部走 `paint_aa`。"""
        cx, cy = center
        if not self.aa:
            r = max(2, int(round(self.marker_r)))
            if kind == "circle":
                pygame.draw.circle(surf, color, (int(cx), int(cy)), r)
            elif kind == "square":
                pygame.draw.rect(surf, color, pygame.Rect(int(cx) - r, int(cy) - r, 2*r, 2*r))
            else:
                h = r * 1.15
                pygame.draw.polygon(surf, color, [(cx, cy - h), (cx - h, cy + h * 0.8),
                                                  (cx + h, cy + h * 0.8)])
            return
        if kind == "circle":
            paint_aa.aa_disc(surf, color, (cx, cy), self.marker_r)
        elif kind == "square":
            paint_aa.aa_rect(surf, color, cx, cy, self.marker_r * 0.88)
        else:
            h = self.marker_r * 1.25
            paint_aa.aa_poly(surf, color, [(cx, cy - h), (cx - h * 0.92, cy + h * 0.8),
                                           (cx + h * 0.92, cy + h * 0.8)])

    def _dashed(self, surf, color, pts, dash=9.0, gap=6.0, width=1):
        """沿折线按弧长切虚线后逐段画（用 `paint_aa.aa_line`，width=1 走 aaline）。"""
        on = True
        remain = dash
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg <= 1e-9:
                continue
            ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
            t = 0.0
            while t < seg:
                step = min(remain, seg - t)
                if on:
                    a = (x0 + ux * t, y0 + uy * t)
                    b = (x0 + ux * (t + step), y0 + uy * (t + step))
                    paint_aa.aa_line(surf, color, a, b, width)
                t += step
                remain -= step
                if remain <= 1e-9:
                    on = not on
                    remain = dash if on else gap

    def render(self, tys, mode: str = "normal", region=None, map_surface=None):
        """返回 `(Surface, stats)`；`stats` 与 `paint_render` 同形（尽量）。"""
        storms = list(tys or [])
        lo0, lo1, la0, la1 = self._all_bounds(storms)
        pad_deg = 4.0
        bounds = (lo0 - pad_deg, lo1 + pad_deg, max(-80.0, la0 - pad_deg),
                  min(84.0, la1 + pad_deg))
        # 画布尺寸：照 Ty4 的 `W = max(最小宽度, 内容 + 2*留空)`
        if self.output_size:
            W, H = int(self.output_size[0]), int(self.output_size[1])
        else:
            dlon = max(1e-6, bounds[1] - bounds[0])
            dlat = max(1e-6, bounds[3] - bounds[2])
            #: 内容尺寸(未设下限)
            _wc = dlon * self.px_per_deg + 2 * self.margin_w
            _hc = dlat * self.px_per_deg * 1.15 + 2 * self.margin_h
            Wc = int(min(self.max_w, max(160, _wc)))
            Hc = int(min(self.max_h, max(120, _hc)))
            #: 套下限(照 Ty4 的 Width/Height)。取整到 2 的倍数无害, 便于等比缩放。
            W = int(min(self.max_w, max(self.min_w, Wc)))
            H = int(min(self.max_h, max(self.min_h, Hc)))
        if self.ctx_override:
            ctx = dict(self.ctx_override)
            # 显式 ctx 可能只给了投影参数(如 PP.CONIC_REFERENCE) ⇒ 补齐渲染需要的字段
            ctx.setdefault('mode', self.projection)
            ctx.setdefault('size', (W, H))
            ctx.setdefault('bounds', bounds)
            W, H = int(ctx['size'][0]), int(ctx['size'][1])
        elif not self.output_size and (W, H) != (Wc, Hc):
            # 命中下限 ⇒ **保持内容比例不变, 只把内容居中到更大的画布**
            # (= 往四周多露地图, 而不是把轨迹放大; 这正是 Ty4 px/° 恒定的效果)
            # 做法: 先用内容尺寸装箱拿到 ctx, 再把偏移各加半个尺寸差。
            ctx = PP.make_ctx(self.projection, bounds, (Wc, Hc), pad=6.0)
            ctx = dict(ctx)
            ctx['size'] = (W, H)
            ctx['ox'] = float(ctx.get('ox', 0.0)) + (W - Wc) / 2.0
            ctx['oy'] = float(ctx.get('oy', 0.0)) + (H - Hc) / 2.0
        else:
            ctx = PP.make_ctx(self.projection, bounds, (W, H), pad=6.0)
        self._ctx = ctx
        self._k = ctx["scale"]

        surf = pygame.Surface((W, H), pygame.SRCALPHA)
        # ① 底图：平涂海洋 + 抗锯齿海岸线陆地
        self._draw_flat_basemap(surf, ctx, W, H)
        # ② 虚线经纬网 + 标注
        if self.grid:
            self._draw_grid(surf, ctx, W, H)
        # ③ 轨迹
        self._draw_tracks(surf, storms, ctx, W, H)
        # ④ 水印
        if self.watermark:
            self._draw_watermark(surf, W, H)
        return surf, {"bounds": bounds, "size": (W, H), "projection": self.projection,
                      "ctx": dict(ctx)}

    # ── ① 底图 ──
    def _draw_flat_basemap(self, surf, ctx, W, H):
        """平涂海洋 + 陆地。

        海岸线做法：把  **按画布覆盖的经纬度窗口裁剪**下来（源图
        30 px/°，与输出 ~26 px/° 同量级），再用**双线性插值**按投影逐像素采样。
        二值掩码经双线性后自然得到 0..1 的边缘覆盖率 ⇒ 海岸线平滑、无块状。
        """
        win = None
        if self.coast:
            try:
                cw = _canvas_window(ctx, W, H)
                win = _land_window(*cw) if cw else None
            except Exception:
                win = None
        fb = _fallback_frac()
        if win is None and fb["frac"] is None:
            surf.fill(QQ_COLORS["ocean"])
            return
        ss = max(1, int(COAST_SS if self.aa else 1))
        ww = W * ss
        oc = np.array(QQ_COLORS["ocean"], np.float32)
        lc = np.array(QQ_COLORS["land"], np.float32)
        rgb = np.empty((H, W, 3), np.uint8)
        # ⚠ 分块：整幅反投影在 2x 超采样下是 ~9M 个 float64 中间量(数百 MB)，
        #   本机可用内存 0.7–2.6 GB 且有 OOM 前科 ⇒ 按行块算，峰值降到 ~1/BLK。
        BLK = max(ss, 64 // ss)
        for y0 in range(0, H, BLK):
            y1 = min(H, y0 + BLK)
            sh = (y1 - y0) * ss
            yy, xx = np.mgrid[0:sh, 0:ww]
            xs = (xx.astype(np.float64) + 0.5) / ss
            ys = (yy.astype(np.float64) + 0.5) / ss + y0
            if win is not None:
                land = _sample_land_window(win, ctx, xs, ys)
            else:
                lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
                ny, nx = fb["frac"].shape
                i = np.clip((lon % 360.0) / fb["dlon"], 0, nx - 1).astype(np.int32)
                j = np.clip((lat + 90.0) / fb["dlat"], 0, ny - 1).astype(np.int32)
                land = fb["frac"][j, i]          # npz: row0 = 90S
            del xs, ys, yy, xx
            if ss > 1:
                land = land.reshape(y1 - y0, ss, W, ss).mean(axis=(1, 3))
            rgb[y0:y1] = (oc[None, None, :] * (1.0 - land[:, :, None])
                          + lc[None, None, :] * land[:, :, None]).astype(np.uint8)
            del land
        try:
            base = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        except Exception:
            surf.fill(QQ_COLORS["ocean"])
            return
        surf.blit(base, (0, 0))
        del rgb, base
        self._land_src = ("window" if win is not None else "npz-fallback")


    # ── ② 虚线经纬网 ──
    def _draw_grid(self, surf, ctx, W, H):
        lon0, lon1, lat0, lat1 = ctx["bounds"]
        lons = [v for v in range(-180, 361, self.grid_step)
                if lon0 - 1e-6 <= v <= lon1 + 1e-6]
        lats = [v for v in range(-80, 85, self.grid_step)
                if lat0 - 1e-6 <= v <= lat1 + 1e-6]
        pts = PP.sample_graticule(ctx, lons=lons, lats=lats, step=0.5,
                                  clip=(-40, -40, W + 40, H + 40))
        for kind, val, poly in pts:
            self._dashed(surf, QQ_COLORS["grid"], poly, dash=9.0, gap=6.0, width=1)
        if not self.labels:
            return
        try:
            from .constants.fonts import f_s, rt
        except Exception:
            return
        # 纬度标注贴左边缘、经度标注贴下边缘（与基准图一致）
        for kind, val, poly in pts:
            if kind == "lat":
                cand = [p for p in poly if p[0] <= 0]
                if not cand:
                    cand = sorted(poly, key=lambda p: p[0])[:1]
                x, y = cand[0]
                s = "%d°%s" % (abs(int(val)), "N" if val >= 0 else "S")
                img = rt(f_s, s, QQ_COLORS["label"])
                surf.blit(img, (4, y - img.get_height() / 2))
            else:
                cand = [p for p in poly if p[1] >= H]
                if not cand:
                    cand = sorted(poly, key=lambda p: -p[1])[:1]
                x, y = cand[0]
                lon = int(val)
                if lon > 180:
                    s = "%d°" % lon if lon == 180 else "%d°W" % (360 - lon)
                elif lon == 180:
                    s = "180°"
                else:
                    s = "%d°E" % lon
                img = rt(f_s, s, QQ_COLORS["label"])
                surf.blit(img, (x - img.get_width() / 2,
                                min(H - img.get_height() - 3, y - img.get_height() - 2)))

    # ── ③ 轨迹 ──
    def _draw_tracks(self, surf, storms, ctx, W, H):
        for st in storms:
            pts = self._storm_pts(st)
            if not pts:
                continue
            xy = [PP.project(self.projection, *self._lo_la(p), ctx)
                  for p in pts]
            # 细白连线
            for i in range(len(xy) - 1):
                paint_aa.aa_line(surf, QQ_COLORS["line"], xy[i], xy[i + 1], self.line_w)
            # 标记（分阶段形状 + 强度色）
            for p, (x, y) in zip(pts, xy):
                code = str(p.get("code") or p.get("type") or "TY")
                w = float(p.get("w", p.get("wind", 0)) or 0)
                self._shapes(surf, shape_for_code(code), color_for_wind(w), (x, y))

    # ── ④ 水印 ──
    def _draw_watermark(self, surf, W, H):
        """ 斜向重复水印。

        **用白色 + alpha 62**：这不是猜的 —— 基准图里水印叠在海洋上得到
        、叠在陆地上得到 ，两者都由
         精确复现：
          海 0.243*255+0.757*0  = 62 ✓  0.243*255+0.757*34  = 88 ✓  0.243*255+0.757*68  = 114 ✓
          陆 0.243*255+0.757*48 = 98.3≈99 ✓  0.243*255+0.757*112 = 146.8≈147 ✓  …+0.757*80 = 122.6≈123 ✓
        """
        try:
            from .constants.fonts import f_m, rt
        except Exception:
            return
        stamp = rt(f_m, "@xrq", QQ_COLORS["line"])
        if stamp.get_width() < 4:
            return
        # 缩到约 0.16 倍画布宽（基准图里每个 "@xrq" 约 300 px / 1837 px）
        k = max(1.0, (W * 0.16) / stamp.get_width())
        stamp = pygame.transform.smoothscale(
            stamp, (max(2, int(stamp.get_width() * k)),
                    max(2, int(stamp.get_height() * k))))
        big = pygame.Surface((stamp.get_width() + 12, stamp.get_height() + 24),
                             pygame.SRCALPHA)
        big.blit(stamp, (6, 12))
        big = pygame.transform.rotate(big, 25)
        big.set_alpha(62)                     # ← 实测反解出的 alpha
        stepx = max(240, int(W * 0.33))
        stepy = max(200, int(H * 0.46))
        for gy in range(-1, H // stepy + 2):
            for gx in range(-1, W // stepx + 2):
                surf.blit(big, (gx * stepx + (gy % 2) * stepx // 2,
                                gy * stepy - big.get_height() // 3))


def resolve_bounds_like_paint_render(tys, margin_w=0.0, margin_h=0.0):
    """薄包装：尽量复用 `paint_render.resolve_bounds`（只读），失败则自带兜底。"""
    try:
        from .paint_render import resolve_bounds
        return resolve_bounds(tys, "normal", None, margin_w, margin_h,
                              include_invest=False)
    except Exception:
        r = QQTrackRenderer()
        b = r._all_bounds(tys)
        return (b[0] - 4, b[1] + 4, max(-80.0, b[2] - 4), min(84.0, b[3] + 4))
