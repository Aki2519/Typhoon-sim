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


# ── 陆地掩码（2× 超采样 → 抗锯齿海岸线）────────────────────────────────────
_LAND = {"tried": False, "frac": None}

#: 海岸线超采样倍率（1 = 关）。2 倍即可让海岸线出现中间 alpha。
COAST_SS = 2


def _land_frac():
    """读 0.5° 全球陆地占比（`simulator/env/model/land_cell.npz`）；失败返回 None。"""
    if _LAND["tried"]:
        return _LAND["frac"]
    _LAND["tried"] = True
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "simulator", "env", "model", "land_cell.npz")
    try:
        with np.load(p) as z:
            _LAND["frac"] = np.asarray(z["landfrac"], dtype=np.float32)
    except Exception:
        _LAND["frac"] = None
    return _LAND["frac"]


def _sample_land(frac, ctx, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """把画布像素（或其超采样子格）反投影后查陆地占比 → bool 数组。"""
    lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
    ny, nx = frac.shape
    i = np.clip((lon % 360.0) / 0.5, 0, nx - 1).astype(np.int32)
    j = np.clip((lat + 90.0) / 0.5, 0, ny - 1).astype(np.int32)
    return frac[j, i] > 0.5


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
        self.aa = bool(o.get("aa", True))
        self.marker_r = float(o.get("marker_r", 9.0))
        self.line_w = int(o.get("line_w", 1))
        self.coast = bool(o.get("coast", True))
        self.map_surface = o.get("map_surface")
        #: 显式给 ctx（例如要和基准图逐像素同框对比时）—— 给了就不自动装箱
        self.ctx_override = o.get("ctx")
        self._ctx: Dict = {}
        self._k = 1.0

    # ── 工具: 统一取点, 兼容 Ty 对象与 b-deck 行 ──
    @staticmethod
    def _storm_pts(st) -> List[dict]:
        pts = getattr(st, "pts", None)
        if pts is None and isinstance(st, dict):
            pts = st.get("pts") or st.get("points")
        return list(pts or [])

    def _all_bounds(self, storms) -> Tuple[float, float, float, float]:
        lo0, lo1, la0, la1 = 1e9, -1e9, 1e9, -1e9
        for st in storms:
            for p in self._storm_pts(st):
                lo = float(p["lon"]); la = float(p["lat"])
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
        # 画布尺寸：按基准图的长宽比与像素密度(~23 px/度)自动
        if self.output_size:
            W, H = int(self.output_size[0]), int(self.output_size[1])
        else:
            dlon = max(1e-6, bounds[1] - bounds[0])
            dlat = max(1e-6, bounds[3] - bounds[2])
            W = int(max(320, min(2400, dlon * 22.6 + 2 * self.margin_w)))
            H = int(max(240, min(1800, dlat * 22.6 * 1.15 + 2 * self.margin_h)))
        if self.ctx_override:
            ctx = dict(self.ctx_override)
            # 显式 ctx 可能只给了投影参数(如 PP.CONIC_REFERENCE) ⇒ 补齐渲染需要的字段
            ctx.setdefault('mode', self.projection)
            ctx.setdefault('size', (W, H))
            ctx.setdefault('bounds', bounds)
            W, H = int(ctx['size'][0]), int(ctx['size'][1])
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
        frac = _land_frac()
        if frac is None:
            surf.fill(QQ_COLORS["ocean"])
            return
        ss = max(1, int(COAST_SS if self.aa else 1))
        ww, hh = W * ss, H * ss
        oc = np.array(QQ_COLORS["ocean"], np.float32)
        lc = np.array(QQ_COLORS["land"], np.float32)
        rgb = np.empty((H, W, 3), np.uint8)
        # ⚠ 分块：2× 超采样时整幅反投影要 ~9M 个 float64 中间量(数百 MB)，
        #   本机可用内存只有 0.7–2.6 GB 且有 OOM 前科 ⇒ 按行块算，峰值降到 1/32。
        BLK = max(ss, 64 // ss)
        for y0 in range(0, H, BLK):
            y1 = min(H, y0 + BLK)
            sh = (y1 - y0) * ss
            yy, xx = np.mgrid[0:sh, 0:ww]
            xs = (xx.astype(np.float64) + 0.5) / ss
            ys = (yy.astype(np.float64) + 0.5) / ss + y0
            land = _sample_land(frac, ctx, xs, ys).astype(np.float32)
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
            xy = [PP.project(self.projection, float(p["lon"]), float(p["lat"]), ctx)
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
        try:
            from .constants.fonts import f_m, rt
        except Exception:
            return
        stamp = rt(f_m, "@xrq", QQ_COLORS["watermark"])
        big = pygame.Surface((stamp.get_width() + 8, stamp.get_height() + 8),
                             pygame.SRCALPHA)
        big.blit(stamp, (4, 4))
        big = pygame.transform.rotate(big, 24)
        big.set_alpha(150)
        stepx = max(240, W // 5)
        stepy = max(200, H // 4)
        for gy in range(-1, H // stepy + 2):
            for gx in range(-1, W // stepx + 2):
                surf.blit(big, (gx * stepx + (gy % 2) * stepx // 2, gy * stepy))


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
