# py/paint_render.py
"""绘画模式渲染引擎: 复刻 Typhoon4(WikiProject Track Drawer)的路径图绘制。

输入一组已解析的 Typhoon(或任意构造的路径点列表), 按所选参数把路径/定点画到一张
off-screen Surface 上(可选地图图像或纯背景色), 支持:
  - 色阶方案: 应用现有色阶 / 经典 Wiki / 2023 新版
  - 路径宽度 / 定点大小 / 半宽半高留空 / 最小宽高 / 坐标单位(经纬度/像素)
  - 路径定点绘画顺序: 同时 / 按强度排序 / 定位点置顶
  - 忽略前期扰动 / 显示所有定位点 / 多文件路径重置
  - 显示经纬网 / 绘画风圈(不/全部/仅最后/仅热带)
  - 色阶图例位置(不/左上/左下/右上/右下/自动) + 图例条目
  - 普通(自动范围) / 区域(上,左,高,宽) / 全局 三种范围
  - 背景色 / 文字标注 / 输出尺寸 / 像素留空(内容区保持纵横比, 不拉伸地图)
坐标约定: 等距圆柱投影(与主地图一致, 0..360 x -90..90)。
像素留空模式下: 输出 Surface = 内容区 + 四周留边, 地图/路径只画在内容区,
避免地图被拉伸(内容区纵横比始终 = 经纬跨度比)。
"""
from __future__ import annotations

import math
import os
import sys
from typing import List, Optional, Tuple

import pygame

from .constants.colors import (
    WIKI_SCALE, COLOR_2023_SCALE,
)
#: 抗锯齿原语（共享底座，**只读使用**；见 app/paint_aa.py 与本报告 §A）。
#: pygame 基础图元(circle/polygon/rect/line)完全无抗锯齿 —— 用户点名的"定点太像素化"
#: 根因就在这里。统一改走超采样精灵。
from . import paint_aa
from .data_repo import DataRepository
from .utils import infer_strength_category

# 常用区域表(源自 Typhoon4 区域.txt): 洋区 -> (上, 左, 高, 宽) 单位 °
COMMON_REGIONS = {
    "北大西洋 NATL": (60, -110, 60, 110),
    "中东太平洋 EPAC": (50, 170, 50, 110),
    "西北太平洋 WPAC": (60, 90, 60, 100),
    "北印度洋 NIO": (40, 40, 40, 70),
    "西南印度洋 SWIO": (0, 20, 40, 80),
    "澳洲区 AUS": (0, 80, 40, 90),
    "西南太平洋 SPAC": (0, 150, 50, 70),
    "东南太平洋 SEPAC": (0, -150, 50, 90),
    "南大西洋 SATL": (0, -60, 40, 50),
    "地中海 MED": (50, -10, 30, 50),
    "高纬北大西洋 HATL": (80, -60, 40, 80),
    "东亚 EA": (45, 95, 40, 50),
    "中国 CNA": (55, 65, 55, 75),
    "浙江 ZJ": (26, 116, 6, 8),
}


def scheme_color(scheme: str, cat: str, wind: int = 0) -> Tuple[int, int, int]:
    """按色阶方案取类别颜色。scheme: 'app'/'wiki'/'2023'。"""
    if scheme == "wiki":
        return WIKI_SCALE.get(cat, (150, 150, 150))
    if scheme == "2023":
        return COLOR_2023_SCALE.get(cat, (180, 180, 180))
    # app: 复用主数据仓库的类别色映射(C5 需要按风级细分)
    if cat == "C5":
        from .constants import C5_M
        return C5_M
    return DataRepository._COLOR_MAP.get(cat, (150, 150, 150))


def _eff_category(pt, category_hint: Optional[str] = None) -> str:
    """取点类别: 优先按风级实时推断(与主应用同一套阈值)。"""
    wind = int(pt.get('w', 0) or 0)
    st = str(pt.get('st', ''))
    cat = infer_strength_category(wind, st)
    norm = {"C2-": "C2", "C3-": "C3", "C4-ST": "C4", "STS": "TS"}
    return norm.get(cat, cat)


def _skip_invest_point(pt) -> bool:
    st = str(pt.get('st', '')).upper()
    return st in ('DB', 'LO', 'WV', 'MD') or st == 'INV'


def _tropical(pt) -> bool:
    st = str(pt.get('st', '')).upper()
    return st not in ('EX', 'SS', 'SD', 'MD', 'WV', 'DB', 'LO')


def resolve_bounds(tys, mode: str, region, margin_w: float = 0.0,
                   margin_h: float = 0.0, include_invest: bool = True):
    """计算绘图经纬范围(度)。返回 (lon_min, lon_max, lat_min, lat_max)。"""
    if mode == 'global':
        return (0.0, 360.0, -90.0, 90.0)
    if mode == 'region' and region:
        top, left, height, width = region
        if width > 0 and height > 0:
            return (left, left + width, top - height, top)
    lons, lats = [], []
    for ty in tys:
        pts = ty.pts if hasattr(ty, 'pts') else ty
        for p in pts:
            if not include_invest and _skip_invest_point(p):
                continue
            lons.append(float(p.get('lo', 0) or 0))
            lats.append(float(p.get('la', 0) or 0))
    if not lons:
        return (90.0, 180.0, 0.0, 40.0)
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    if lon_max - lon_min < 0.5:
        lon_min -= 1.0
        lon_max += 1.0
    if lat_max - lat_min < 0.5:
        lat_min -= 1.0
        lat_max += 1.0
    if margin_w > 0 or margin_h > 0:
        lon_min -= margin_w
        lon_max += margin_w
        lat_min -= margin_h
        lat_max += margin_h
    return (lon_min, lon_max, lat_min, lat_max)


def haversine_km(la1, lo1, la2, lo2) -> float:
    """两点大圆距离(km)。"""
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def compute_track_stats(tys):
    """输出每个台风的统计: (名称, ACE, 路径长度km, 定位点数)。"""
    out = []
    for ty in tys:
        pts = ty.pts if hasattr(ty, 'pts') else ty
        if not pts:
            continue
        ace = 0.0
        path = 0.0
        prev = None
        for p in pts:
            w = int(p.get('w', 0) or 0)
            st = str(p.get('st', '')).upper()
            if st in ('TS', 'TY', 'ST', 'HU', '') and w >= 35:
                ace += (w * w) / 10000.0
            la = float(p.get('la', 0) or 0)
            lo = float(p.get('lo', 0) or 0)
            if prev is not None:
                path += haversine_km(prev[0], prev[1], la, lo)
            prev = (la, lo)
        name = (getattr(ty, 'sname', '') or getattr(ty, 'cust', '')
                or getattr(ty, 'name', '') or '?')
        out.append({'name': name, 'ace': ace, 'path_km': path, 'npts': len(pts),
                    'wmax': max(int(p.get('w', 0) or 0) for p in pts)})
    return out


#: 支持的投影名 —— 与 P4 的 `paint_projection.PROJECTIONS` / P5 的 `cfg["projection"]` 同名。
_PROJECTIONS = ("equirect", "conic")

#: 轨迹线段走 `paint_aa.aa_line` 的最大包围盒边长(px)。超过则沿用 aliased 直绘 ——
#: `aa_line`(width>1) 的超采样精灵尺寸 ∝ 段长，长跳段会造成大额临时分配。
#: （Lead 已在 `paint_aa` 内部加了 `_AA_LINE_CHUNK_PX` 分块守卫；这里是调用侧双保险。）
_AA_LINE_MAX_PX = 96

#: Ty4 精确模式的 px/°：底图原生 10800×5400 / 360°×180° = **30 px/°**（TY4_SPEC §0-1）。
_TY4_PX_PER_DEG = 30.0

#: P4 的 `app/paint_projection.py`。**契约（已冻结）**：
#:   `make_ctx(name, bounds, size, pad=0, **over) -> ctx`（自动装箱+居中；传 scale/ox/oy 可跳过）
#:   `project(name, lon, lat, ctx) -> (x, y)`（**画布内容区相对**像素 ⇒ 调用方要加 `inner.x/y`）
#: 每类 warning **只打一次**（避免一万个点打一万条日志）。
_proj_warned_keys: set = set()


def _proj_warn(key: str, msg: str) -> None:
    if key in _proj_warned_keys:
        return
    _proj_warned_keys.add(key)
    try:
        print(f"[paint_render] {msg}", file=sys.stderr)
    except Exception:
        pass


class TrackMapRenderer:
    """把一组路径点渲染为 Track Map Surface。"""

    def __init__(self, opts: Optional[dict] = None) -> None:
        o = opts or {}
        self.scheme = o.get("scheme", "app")
        # Ty4 原版: 路径宽度/定点大小为角度制 (Settings.ini: Track=0.05°, Point=0.4°)
        # 渲染时按“每度像素 k”换算; path_width/point_size 为像素兜底
        self.track_deg = float(o.get("track_deg", 0.05))
        self.point_deg = float(o.get("point_deg", 0.4))
        self.path_width = max(1, int(o.get("path_width", 2)))
        self.point_size = max(1, int(o.get("point_size", 4)))
        self.margin_w = float(o.get("margin_w", 0.0))     # 半宽留空
        self.margin_h = float(o.get("margin_h", 0.0))     # 半高留空
        self.coord_unit = o.get("coord_unit", "deg")      # deg(经纬度)/px(像素)
        self.min_w = max(320, int(o.get("min_w", 1920)))  # 最小宽度(px)
        self.min_h = max(200, int(o.get("min_h", 1080)))  # 最小高度(px)
        self.draw_order = o.get("draw_order", "simult")   # simult/sort/points_top
        self.skip_invest = bool(o.get("skip_invest", False))
        self.all_points = bool(o.get("all_points", True))
        self.grid = bool(o.get("grid", False))
        self.draw_rad = o.get("draw_rad", "none")         # none/all/last/tropical
        # `multireset`（多文件重置）**死参数已删除**：Ty4 的 `MultipleFile` 只实现了 `==2`
        # （§11.2，`1` 无任何代码），而我们本就"逐台风独立画路径"、不存在跨文件连线需要
        # 重置 ⇒ 该选项在语义上是空的。旧调用方仍可传该键（`o.get` 不报错），只是不再读取。
        self.legend_pos = o.get("legend_pos", "auto")
        self.legend_entries = o.get("legend_entries", None)
        self.legend_bg = bool(o.get("legend_bg", False))  # 图例半透明底框, 默认不画
        self.bg_color = o.get("bg_color", None)
        self.map_path = o.get("map_path", None)
        self.labels = bool(o.get("labels", False))
        self.font = o.get("font", None)
        self.output_size = o.get("output_size", None)
        #: 投影名。与 P4/P5 约定同名（`cfg["projection"]`，字符串）：
        #: `"equirect"`（等距圆柱，默认/缺省/非法值一律回落它）/ `"conic"`（圆锥，P4 交付）。
        #: ⚠ P4 的 `app/paint_projection.py` 尚不存在 ⇒ 现在 `"conic"` 会**回落等距圆柱**并
        #: 打一条 warning（每进程一次），等 P4 落地后自动接线，P3 不需要再改。
        _pj = o.get("projection") or "equirect"
        self.projection = _pj if _pj in _PROJECTIONS else "equirect"
        #: 画布模式。**默认 `"fit"` = 现状**（按跨度纵横比 + min_w/min_h 反推 px/°），
        #: 保证老调用方/老测试零改动；`"ty4"` = **Typhoon4 精确模式**（TY4_SPEC §0-2/§14）：
        #: px/° 恒为 `ty4_px_per_deg`(=30，即底图原生 10800×5400/360×180)、
        #: `W = max(min_w, ceil(bbox_w_px) + 2×WidthSpace_px)`、底图 **1:1 裁剪不缩放**、
        #: 轨迹严格居中，且 `margin_w/margin_h` 按**像素**解释（不是 deg 模式里的"度"）。
        _cm = o.get("canvas_mode") or "fit"
        self.canvas_mode = "ty4" if _cm == "ty4" else "fit"
        #: ty4 模式的 px/°。默认 30；若换底图可按 `底图宽/360` 传进来。
        self.ty4_px_per_deg = float(o.get("px_per_deg", _TY4_PX_PER_DEG))
        #: 本次渲染实际生效的投影（ty4 模式下会被钉成 `"equirect"`，见 `render()`）
        self._pj_active = self.projection
        #: P4 `ctx` 的缓存槽 `(key, ctx)` —— 见 `_projection_ctx()`
        self._pj_ctx_cache = None

    # ── 投影: 相对内容区(内容区 = 去掉留边后的矩形) ──

    def _proj(self, lo: float, la: float, bounds, inner: pygame.Rect):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = lon_max - lon_min
        span_lat = lat_max - lat_min
        if span_lon <= 0 or span_lat <= 0 or inner.w <= 0 or inner.h <= 0:
            return None
        if self._pj_active != "equirect":
            pt = self._proj_via_p4(lo, la, bounds, inner)
            if pt is not None:
                return pt
            # P4 不可用 ⇒ 回落（warning 已打过一次），继续走下面的等距圆柱
        x = inner.x + (lo - lon_min) / span_lon * inner.w
        y = inner.y + (lat_max - la) / span_lat * inner.h
        return (int(x), int(y))

    def _projection_ctx(self, bounds, inner: pygame.Rect):
        """构造并**缓存** P4 的 `ctx`。

        `make_ctx` 要采样一圈边界求投影包围盒（不便宜）⇒ **一帧只建一次**；
        以 `(投影名, bounds, inner 宽高)` 为键失效（同一渲染器换台风/换窗口才重建）。
        """
        key = (self._pj_active, tuple(float(v) for v in bounds), inner.w, inner.h)
        if self._pj_ctx_cache is not None and self._pj_ctx_cache[0] == key:
            return self._pj_ctx_cache[1]
        ctx = None
        try:
            from . import paint_projection as PJ
            ctx = PJ.make_ctx(self._pj_active, bounds, (inner.w, inner.h))
        except Exception as e:
            _proj_warn("fail:" + str(self._pj_active),
                       f"投影 '{self._pj_active}' 不可用({e})：本次回落等距圆柱。")
        self._pj_ctx_cache = (key, ctx)
        return ctx

    def _proj_via_p4(self, lo: float, la: float, bounds, inner: pygame.Rect):
        """走 P4：`ctx`（含自动装箱/居中）→ `project` → **补 `inner` 偏移**。"""
        ctx = self._projection_ctx(bounds, inner)
        if ctx is None:
            return None
        try:
            from . import paint_projection as PJ
            x, y = PJ.project(self._pj_active, lo, la, ctx)
            # ⚠ P4 返回的是**内容区相对**坐标（原点在内容区左上角）⇒ 必须加 inner 偏移
            return (int(x) + inner.x, int(y) + inner.y)
        except Exception as e:
            _proj_warn("fail:" + str(self._pj_active),
                       f"投影 '{self._pj_active}' 调用失败({e})：本次回落等距圆柱。")
            return None

    # ── Ty4 线宽 / 点径（TY4_SPEC §7.1 的两分支） ──

    def _px_track(self) -> int:
        """线宽(px)。`px` 单位 ⇒ `round(Track)`；`deg` 单位 ⇒ `round(track_deg×k)`。"""
        if self.coord_unit == "px":
            return max(1, int(round(self.path_width)))
        return max(1, int(round(self.track_deg * self._k)))

    def _px_point_radius(self) -> float:
        """**点半径**(px，浮点，不取整)。

        Ty4: `px_point = max(1.0, Point×px_per_deg/2)`（§7.1「除 2 = 半径」）；
        `px` 单位下 `Point` 直接当像素用 ⇒ 半径 = `point_size/2`。
        """
        if self.coord_unit == "px":
            return max(1.0, self.point_size / 2.0)
        return max(2.0, self.point_deg * self._k * 0.5)

    # ── Ty4 精确模式画布（TY4_SPEC §0-2 / §14） ──

    def _ty4_canvas(self, tys):
        """按 Typhoon4 公式算 `(bounds, out_w, out_h)`。

        ```
        tw = (lon1-lon0)*30 ; th = (lat1-lat0)*30
        W  = max(min_w, ceil(tw) + 2*WidthSpace_px)
        H  = max(min_h, ceil(th) + 2*HeightSpace_px)
        x0 = round(cx_px - W/2) ; y0 = round(cy_px - H/2)      # 轨迹严格居中
        bounds = (x0/30, (x0+W)/30, 90-(y0+H)/30, 90-y0/30)    # 1:1 裁剪窗口
        ```
        `bounds` 是"以画布中心为轨迹 bbox 中心"的经纬窗口 ⇒ 调用方按 `k=30` 投影即得
        **1:1 裁剪**。`margin_w/margin_h` 在这里按**像素**用（与 deg 模式的"度"区分）。
        """
        ppd = self.ty4_px_per_deg
        lons, lats = [], []
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            for p in pts:
                if self.skip_invest and _skip_invest_point(p):
                    continue
                try:
                    lons.append(float(p.get('lo', 0) or 0))
                    lats.append(float(p.get('la', 0) or 0))
                except (TypeError, ValueError):
                    continue
        if not lons or not lats:
            lons, lats = [90.0, 180.0], [0.0, 40.0]
        lon0, lon1 = min(lons), max(lons)
        lat0, lat1 = min(lats), max(lats)
        # ⚠ 这里**故意不 `ceil`**：规格书 §14 是 `W = max(W_MIN, tw + 2*HALF_W)`（`tw` 为浮点），
        # 随后交给 PIL `crop` 取整；用 `ceil` 会让"跨度 ×30 恰好是整数、但浮点略大一点"
        # 的情况多出 1 px（实测 sinlaku: 67.4000001° ⇒ ceil 给 2743，基准图是 2742）。
        # 截断与原来"用浮点再裁剪"的语义一致。
        out_w = int(max(self.min_w, (lon1 - lon0) * ppd + 2 * int(self.margin_w)))
        out_h = int(max(self.min_h, (lat1 - lat0) * ppd + 2 * int(self.margin_h)))
        # 轨迹严格居中（取整到整像素 ⇒ 中心偏差 ≤0.5 px，规格书容差 ≤1 px）
        cx_px = (lon0 + lon1) / 2.0 * ppd
        cy_px = (90.0 - (lat0 + lat1) / 2.0) * ppd
        x0 = round(cx_px - out_w / 2.0)
        y0 = round(cy_px - out_h / 2.0)
        bounds = (x0 / ppd, (x0 + out_w) / ppd,
                  90.0 - (y0 + out_h) / ppd, 90.0 - y0 / ppd)
        return bounds, out_w, out_h

    # ── 主入口 ──

    def render(self, tys, mode: str = "normal", region=None,
               map_surface: Optional[pygame.Surface] = None):
        """返回 (Surface, stats)。stats = compute_track_stats 结果。"""
        # Ty4 精确模式：画布与窗口由 §0-2 公式直接给出（`output_size` 仍可由调用方强压）。
        _ty4 = (self.canvas_mode == "ty4" and mode == "normal" and not self.output_size)
        if _ty4:
            bounds, out_w, out_h = self._ty4_canvas(tys)
            inner = pygame.Rect(0, 0, out_w, out_h)
        else:
            bounds = resolve_bounds(tys, mode, region, self.margin_w, self.margin_h,
                                    include_invest=not self.skip_invest)
            # 内容区尺寸(保持经纬纵横比)
            content_w, content_h = self._auto_output_size(bounds)
            # 输出尺寸 + 内容区偏移
            if self.output_size:
                out_w, out_h = self.output_size
                inner = pygame.Rect(0, 0, out_w, out_h)
            elif self.coord_unit == "px" and (self.margin_w or self.margin_h):
                out_w = int(content_w + 2 * self.margin_w)
                out_h = int(content_h + 2 * self.margin_h)
                inner = pygame.Rect(int(self.margin_w), int(self.margin_h),
                                    content_w, content_h)
            else:
                out_w, out_h = content_w, content_h
                inner = pygame.Rect(0, 0, out_w, out_h)
        out_w, out_h = max(320, out_w), max(240, out_h)

        self._point_density = self._compute_point_density(tys, bounds, inner)

        if _ty4:
            # px/° **恒为 30**（不由画布反推）⇒ 点径/线宽与台风 bbox 无关，与 Ty4 一致。
            self._k = self.ty4_px_per_deg
        else:
            # 角度制尺寸换算: 每度像素 k = 内容区宽/经度跨度(与纬度等比例, 保持正形)
            span_lon = max(1e-6, bounds[1] - bounds[0])
            self._k = inner.w / span_lon

        # 投影开关 × 画布模式的相容性（Lead 提问的那条，实测见报告 §3.4）：
        # `make_ctx` 的自动装箱会把 scale 设成 `min(W/bw, H/bh)`（实测 conic=3.006、
        # equirect=66.258），**不是** Ty4 的 30 px/°，且会重算 ox/oy 居中 ⇒ 会把
        # "px/° 恒 30 + 1:1 裁剪"整条破坏。更根本的是：ty4 模式下垫在下面的底图就是
        # 等距圆柱栅格（网格/标签都烘焙在其中），在其上画圆锥轨迹本身就不自洽。
        # ⇒ ty4 模式**钉死 equirect**（要 conic 请用 canvas_mode="fit"），并明确告警。
        self._pj_active = self.projection
        if _ty4 and self.projection != "equirect":
            self._pj_active = "equirect"
            _proj_warn("ty4-pin",
                       f"canvas_mode='ty4' 固定等距圆柱（底图是 1:1 裁剪的等距圆柱栅格，"
                       f"网格/标签已烘焙在其中）；投影 '{self.projection}' 需要 "
                       f"canvas_mode='fit'。本次按 equirect 渲染。")

        surf = pygame.Surface((out_w, out_h), pygame.SRCALPHA)
        self._draw_background(surf, bounds, inner, map_surface, out_w, out_h)
        if self.grid:
            self._draw_grid(surf, bounds, inner)
        self._draw_tracks(surf, tys, bounds, inner)
        if self.legend_pos != "none":
            self._draw_legend(surf)
        if self.labels:
            self._draw_labels(surf, tys, bounds, inner)
        stats = compute_track_stats(tys)
        return surf, stats

    def _compute_point_density(self, tys, bounds, inner):
        q = [0, 0, 0, 0]
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            for p in pts:
                c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)),
                               bounds, inner)
                if c is None:
                    continue
                x, y = c
                if x < inner.centerx and y < inner.centery:
                    q[0] += 1
                elif x >= inner.centerx and y < inner.centery:
                    q[1] += 1
                elif x < inner.centerx:
                    q[2] += 1
                else:
                    q[3] += 1
        return q

    def _auto_output_size(self, bounds):
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        aspect = span_lon / span_lat
        tw = self.min_w
        th = max(int(tw / aspect), 1)
        if th < self.min_h:
            th = self.min_h
            tw = max(int(th * aspect), 1)
        return (tw, th)

    def _draw_background(self, surf, bounds, inner, map_surface, out_w, out_h):
        """先铺全幅背景, 再把地图精确裁剪到内容区(比例不被拉伸)。"""
        bg = self.bg_color or (200, 220, 240)
        surf.fill(bg)
        _map_img = map_surface
        if _map_img is None and self.map_path and os.path.exists(self.map_path):
            try:
                _map_img = pygame.image.load(self.map_path).convert()
            except Exception:
                _map_img = None
        if _map_img is not None:
            self._draw_map_crop_into(surf, _map_img, bounds, inner)

    def _draw_map_crop_into(self, surf, map_img, bounds, inner):
        """把地图裁剪到内容区 inner(等距圆柱): 按经纬范围裁剪, 保持源图比例不拉伸。

        地图图源 (map/map.png) 为 0..360 x -90..90 等距圆柱, 需同时按经度与纬度
        范围裁剪后再平滑缩放, 否则(只按经度裁剪/用全高)会把纬向拉伸(地图明显拉长)。
        """
        lon_min, lon_max, lat_min, lat_max = bounds
        mw, mh = map_img.get_size()
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        # 源图经纬 -> 像素: 0..360 -> 0..mw, 90..-90 -> 0..mh
        sx = int(((lon_min % 360.0) % 360.0) / 360.0 * mw)
        sw = max(1, int(span_lon / 360.0 * mw))
        sy = int((90.0 - lat_max) / 180.0 * mh)
        sh = max(1, int(span_lat / 180.0 * mh))
        sy = max(0, min(sy, mh - 1))
        sh = max(1, min(sh, mh - sy))
        # 经度环绕拆两段(目标宽度按源宽比例分配)
        if sx >= mw:
            sx = sx % mw
        if sx + sw > mw:
            seg1_w = mw - sx
            seg2_w = sw - seg1_w
            d1_w = max(1, int(seg1_w / mw * inner.w))
            d2_w = max(1, inner.w - d1_w)
            c1 = map_img.subsurface((sx, sy, max(1, mw - sx), sh))
            t1 = pygame.transform.smoothscale(c1, (d1_w, inner.h))
            surf.blit(t1, (inner.x, inner.y))
            if seg2_w > 0 and sy + sh <= mh:
                c2 = map_img.subsurface((0, sy, min(seg2_w, mw), sh))
                t2 = pygame.transform.smoothscale(c2, (d2_w, inner.h))
                surf.blit(t2, (inner.x + d1_w, inner.y))
        else:
            cw = max(1, min(sw, mw - sx))
            # **1:1 像素直搬**（Ty4 精确模式的"底图不缩放"靠这里）：
            # bounds 由 k=30 的整数窗口算出 ⇒ 裁剪尺寸恰好等于内容区尺寸；此时再
            # smoothscale 只是白付一次重采样（并引入亚像素柔化）。允许 ±1 px 的
            # 浮点/取整误差，超出才回落到缩放（`fit` 模式尺寸差很大，永远走缩放）。
            if (abs(cw - inner.w) <= 1 and abs(sh - inner.h) <= 1
                    and sx + inner.w <= mw and sy + inner.h <= mh):
                crop = map_img.subsurface((sx, sy, inner.w, inner.h))
                surf.blit(crop, (inner.x, inner.y))
            else:
                crop = map_img.subsurface((sx, sy, cw, sh))
                scaled = pygame.transform.smoothscale(crop, (inner.w, inner.h))
                surf.blit(scaled, (inner.x, inner.y))

    def _draw_grid(self, surf, bounds, inner):
        step = 10.0
        color = (0, 0, 0, 60)
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        lon_min, lon_max, lat_min, lat_max = bounds
        lon0 = math.floor(lon_min / step) * step
        lon = lon0
        while lon <= lon_max:
            x = inner.x + int((lon - lon_min) / (lon_max - lon_min) * inner.w)
            pygame.draw.line(line_surf, color, (x, inner.top), (x, inner.bottom), 1)
            lon += step
        lat0 = math.ceil(lat_min / step) * step
        lat = lat0
        while lat <= lat_max:
            y = inner.y + int((lat_max - lat) / (lat_max - lat_min) * inner.h)
            pygame.draw.line(line_surf, color, (inner.left, y), (inner.right, y), 1)
            lat += step
        surf.blit(line_surf, (0, 0))

    def _draw_tracks(self, surf, tys, bounds, inner):
        points_top = self.draw_order == "points_top"
        if self.draw_order == "sort":
            def _peak(ty):
                pts = ty.pts if hasattr(ty, 'pts') else ty
                return max([int(p.get('w', 0) or 0) for p in pts] or [0])
            tys = sorted(tys, key=_peak)
        for idx, ty in enumerate(tys):
            pts = ty.pts if hasattr(ty, 'pts') else ty
            if not pts:
                continue
            draw_pts = []
            for p in pts:
                if self.skip_invest and _skip_invest_point(p):
                    continue
                draw_pts.append(p)
            if not draw_pts:
                continue
            self._draw_ty_path(surf, draw_pts, bounds, inner)
            if not points_top:
                self._draw_ty_points(surf, draw_pts, bounds, inner)
        if points_top:
            for ty in tys:
                pts = ty.pts if hasattr(ty, 'pts') else ty
                draw_pts = [p for p in pts] if pts else []
                if self.skip_invest:
                    draw_pts = [p for p in draw_pts if not _skip_invest_point(p)]
                if not draw_pts:
                    continue
                self._draw_ty_points(surf, draw_pts, bounds, inner)

    def _draw_ty_path(self, surf, pts, bounds, inner):
        """路径线：Ty4 原版为白色细线（被定点遮挡），非强度色。宽度 = track_deg*每度像素。

        相邻点按“球面最近距离”连线：经度取环面最短差((lon2-lon1+180)%360-180, 恒≤180)，
        因此跨 0°/180° 子午线洋区(如南大西洋 SA)内的路径段会被正确连接,
        不会横穿整幅地图；纬度差 |dlat|>90 视为异常跳段跳过。
        """
        if len(pts) < 2:
            return
        line_w = self._px_track()
        prev = None
        for p in pts:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                prev = None
                continue
            if prev is not None:
                (x1, y1), p1 = prev
                x2, y2 = c
                lo1 = float(p1.get('lo', 0))
                lo2 = float(p.get('lo', 0))
                dlon = ((lo2 - lo1 + 180.0) % 360.0) - 180.0   # 环面最短经度差
                dlat = float(p.get('la', 0)) - float(p1.get('la', 0))
                if abs(dlat) > 90.0:
                    prev = (c, p)
                    continue
                # 解包目标点经度到与上一束同轴的连续值, 使线段沿最短方向绘制
                c2 = self._proj(lo1 + dlon, float(p.get('la', 0)), bounds, inner)
                if c2 is not None:
                    # 抗锯齿：**只在短段上**走 aa_line。
                    # 为什么要设上限：`paint_aa.aa_line` 对 width>1 走"包围盒超采样精灵"，
                    # 精灵尺寸 ∝ 线段长度 —— 一条长跳段(数据缺口/跨洋)会退化成数千像素的
                    # 4× 画布(例如 1000px 段 → 4000×4000×4 = 64 MB)。上限把它钉在
                    # ≤96px 段(精灵 ≤ ~(96+4)×4≈400² ≈ 0.6 MB)，长段沿用 aliased 直绘。
                    # 计量：见报告 §A「长段回退次数」。Ty4 原程序的线来自 PIL，同样无 AA。
                    if max(abs(c2[0] - x1), abs(c2[1] - y1)) <= _AA_LINE_MAX_PX:
                        paint_aa.aa_line(surf, (255, 255, 255), (x1, y1), c2, line_w)
                    else:
                        pygame.draw.line(surf, (255, 255, 255), (x1, y1), c2, line_w)
            prev = (c, p)

    # ── Ty4 原版定点点型规则 ──
    #   普通:   圆形(强度色); 正式报 full 大小, 非正式报小点
    #   SS:     TS 色正方形;  SD: TD 色正方形
    #   EX 系:  三角形 — ED/ES/LO 用 TS 色, E1..E5 用 C1..C5 色
    #   EX 气压分级: ED 1005-1001 / ES 1000-986 / E1 985-971 / E2 970-961 /
    #               E3 960-951 / E4 950-940 / E5 <940 / LO >1005
    @staticmethod
    def ex_pressure_category(pres) -> str:
        """气压 → EX 细化类别 (ED/ES/E1..E5/LO)。气压缺失视为 ES(居中)。"""
        try:
            pres = float(pres or 0)
        except (TypeError, ValueError):
            pres = 0.0
        if pres <= 0:
            return "ES"
        if pres > 1005:
            return "LO"
        if pres >= 1001:
            return "ED"
        if pres >= 986:
            return "ES"
        if pres >= 971:
            return "E1"
        if pres >= 961:
            return "E2"
        if pres >= 951:
            return "E3"
        if pres >= 940:
            return "E4"
        return "E5"

    def _point_style(self, p) -> tuple:
        """返回 (形状, 颜色)。形状: 'circle'/'square'/'triangle'。"""
        st = str(p.get('st', '')).upper()
        if st == "SS":
            return ("square", scheme_color(self.scheme, "TS"))
        if st == "SD":
            return ("square", scheme_color(self.scheme, "TD"))
        if st in ("EX", "ED", "ES", "E1", "E2", "E3", "E4", "E5", "LO"):
            ex = self.ex_pressure_category(p.get('p', p.get('pres', 0)))
            color = scheme_color(self.scheme, "TS") if ex in ("ED", "ES", "LO") else scheme_color(self.scheme, ex.replace("E", "C"))
            return ("triangle", color)
        return ("circle", scheme_color(self.scheme, _eff_category(p)))

    def _draw_shape(self, surf, shape, color, cx, cy, r):
        """画定点/原点。**全部走 `paint_aa`**（pygame 基础图元无抗锯齿 —— 用户点名的
        "定点太像素化"根因）。`r` 允许浮点；`aa_disc/aa_rect` 收浮点半径。
        """
        if shape == "square":
            paint_aa.aa_rect(surf, color, cx, cy, r)
        elif shape == "triangle":
            h = r * 1.6
            pts3 = [(cx, cy - h * 0.62), (cx - r, cy + h * 0.38), (cx + r, cy + h * 0.38)]
            paint_aa.aa_poly(surf, color, pts3)
        else:
            paint_aa.aa_disc(surf, color, (cx, cy), r)

    @staticmethod
    def _is_official(p) -> bool:
        """正式报判定: 地球 UTC 时间为 00/06/12/18 时（t 字段形如 'YYYYMMDDHH'）。"""
        t = str(p.get('t', '') or '')
        hh = t[8:10] if len(t) >= 10 else t[-2:]
        return bool(hh) and hh in ("00", "06", "12", "18")

    def _draw_ty_points(self, surf, pts, bounds, inner):
        # 半径**不取整**（原来 `int(round(...))` 会放大锯齿）；`px` 单位走 Ty4 的
        # `Point/2` 分支（见 `_px_point_radius`）。
        r = self._px_point_radius()
        if self.draw_rad != "none":
            self._draw_radii(surf, pts, bounds, inner)
        pool = pts if self.all_points else pts[-1:]
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            shape, color = self._point_style(p)
            rr = r if self._is_official(p) else max(1.0, r * 0.5)
            self._draw_shape(surf, shape, color, c[0], c[1], rr)

    def _draw_radii(self, surf, pts, bounds, inner):
        pool = pts
        if self.draw_rad == "last":
            pool = pts[-1:]
        elif self.draw_rad == "tropical":
            pool = [p for p in pts if _tropical(p)]
        ring_color = (0, 0, 0, 120)
        line_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            wind = int(p.get('w', 0) or 0)
            rad34 = int(p.get('r34', 0) or max(0, int(wind * 0.5)))
            # 风圈同样无抗锯齿 ⇒ 改走 aa_ring（超采样圆环）
            paint_aa.aa_ring(line_surf, ring_color, c, max(4, rad34 + self.point_size), 1)
        surf.blit(line_surf, (0, 0))

    def _draw_legend(self, surf):
        entries = self.legend_entries or _default_entries()
        w, h = surf.get_size()
        item_h = 26
        pad = 12
        label_w = 0
        if self.font is not None:
            label_w = max((self.font.size(lb)[0] for lb, _ in entries), default=0) + 30
        lw = max(170, label_w + 32)
        lh = len(entries) * item_h + pad * 2
        pos = self.legend_pos
        if pos == "auto":
            pos = _auto_legend_corner(entries, lw, lh, w, h, self._point_density)
        if pos == "tl":
            x0, y0 = pad, pad
        elif pos == "tr":
            x0, y0 = w - lw - pad, pad
        elif pos == "br":
            x0, y0 = w - lw - pad, h - lh - pad
        else:
            x0, y0 = pad, h - lh - pad
        panel = pygame.Surface((lw, lh), pygame.SRCALPHA)
        if self.legend_bg:
            # 半透明深色底框(可选, 默认不画)
            pygame.draw.rect(panel, (15, 18, 25, 235), panel.get_rect(), border_radius=8)
            pygame.draw.rect(panel, (200, 210, 230, 90), panel.get_rect(), 1, border_radius=8)
        y = pad
        for label, cat in entries:
            color = scheme_color(self.scheme, cat)
            # 图例色点是**圆** ⇒ 必须抗锯齿（原来 pygame.draw.circle 是硬边八向阶梯）
            paint_aa.aa_disc(panel, color, (pad + 8, y + 13), 8)
            if self.font is not None:
                ts = self.font.render(label, True, (240, 243, 250))
                # 无底框时给文字加描边保证可读性
                if not self.legend_bg:
                    sh = self.font.render(label, True, (10, 12, 18))
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        panel.blit(sh, (pad + 26 + ox, y + (item_h - ts.get_height()) // 2 + 1 + oy))
                panel.blit(ts, (pad + 26, y + (item_h - ts.get_height()) // 2 + 1))
            y += item_h
        surf.blit(panel, (x0, y0))

    def _draw_labels(self, surf, tys, bounds, inner):
        if self.font is None:
            return
        for ty in tys:
            pts = ty.pts if hasattr(ty, 'pts') else ty
            if not pts:
                continue
            p = pts[-1]
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            name = getattr(ty, 'sname', '') or getattr(ty, 'cust', '') or getattr(ty, 'name', '')
            if not name:
                continue
            ts = self.font.render(name, True, (235, 240, 250))
            # 带描边提高可读性
            shadow = self.font.render(name, True, (10, 12, 18))
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                surf.blit(shadow, (c[0] + 6 + ox, c[1] - ts.get_height() // 2 + oy))
            surf.blit(ts, (c[0] + 6, c[1] - ts.get_height() // 2))


def _default_entries():
    from .constants.colors import WIKI_LEGEND_ENTRIES
    return WIKI_LEGEND_ENTRIES


def _auto_legend_corner(entries, lw, lh, w, h, density=None):
    if not density or not density:
        return "tl"
    corners = [("tl", 0), ("tr", 1), ("bl", 2), ("br", 3)]
    if max(density) - min(density) < max(1, w * h // 4000):
        return "tl"
    best = min(corners, key=lambda kv: density[kv[1]])
    return best[0]


def load_map_image(map_path: Optional[str] = None) -> Optional[pygame.Surface]:
    """载入地图图像(缺省用应用全局地图)。失败返回 None。"""
    if not map_path:
        from .constants import DEFAULT_MAP
        map_path = DEFAULT_MAP
    if os.path.exists(map_path):
        try:
            img = pygame.image.load(map_path)
            try:
                return img.convert()
            except Exception:
                try:
                    return img.convert_alpha()
                except Exception:
                    return img
        except Exception:
            return None
    return None
