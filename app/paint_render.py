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

#: ⚠ 这里曾有一个调用侧上限 `_AA_LINE_MAX_PX = 96`（长段回落 aliased 直绘），
#: 2026-10-02 **已删除**：`paint_aa` 内部现在有 `_AA_LINE_CHUNK_PX` 等长分块 + 接缝补偿
#: （单次分配 64.7 MB → 665 KB），调用侧再留阈值只会让长段白白失去 AA。
#: **内存边界归底座，取景/几何归渲染器** —— 别在两个地方各放一个阈值。

#: Ty4 精确模式的 px/°：底图原生 10800×5400 / 360°×180° = **30 px/°**
#: （真源码：`Typhoon4Py/t4_render.py:7`（`[GLOBAL].png` 10800×5400 = 30 px/°）、`:49` 的 `MAP_BOUNDS`）。
_TY4_PX_PER_DEG = 30.0


# ── R3 维度层（`app/paint_style.py`）的取值解析 ────────────────────────────────

def _palette_to_scheme(palette: str) -> str:
    """`palette` 维度 → `scheme_color()` 认得的色阶名（缺模块时退回原串）。"""
    try:
        from . import paint_style
        return paint_style._palette_to_scheme(str(palette))
    except Exception:
        return str(palette)


#: Ty4 的等级判定表（**真源码**：`Typhoon4Py/t4_track.py:37-44` 的 `CATEGORY_THRESHOLDS`）
#: —— **闭下界、开上界**。用于 EX 族取色，见 `_ty4_category_of`。
_TY4_THRESHOLDS = ((0, "TD"), (34, "TS"), (64, "C1"), (83, "C2"),
                   (96, "C3"), (113, "C4"), (137, "C5"))


def _ty4_category_of(wind) -> str:
    """**只按风速**的 Ty4 等级（EX 族取色专用）。

    ⚠ 这里**不能**走 `_eff_category()`：`infer_strength_category(wind, 'EX')`
    会**先看状态**（`app/utils.py:175-176` 直接 `return "EX"`）⇒ 查表拿到
    `WIKI_SCALE["EX"] = (204,204,204)` **灰色** —— 这正是"注释写着按风级、渲出来却是灰"的根因
    （Lead 从观感、`evidence` 从 254 点逐点采样各自独立复现，一致率 0/24）。
    本函数绕开状态、只查风速表（等价 `Fix.category` 对非 SD/SS/DB… 分支的行为）。
    """
    try:
        w = float(wind or 0)
    except (TypeError, ValueError):
        w = 0.0
    cat = "TD"
    for lo, nm in _TY4_THRESHOLDS:
        if w >= lo:
            cat = nm
        else:
            break
    return cat


def _marker_geom(marker: str) -> dict:
    """`marker` 维度 → 逐形状几何系数。

    · `by_status` → `MARKER_GEOM_TY4`（严格口径实测：方块≈0.82、三角≈0.81×0.72，见 paint_style 注释）
    · `uniform`   → 全 1.0（**老行为，逐位兼容**）
    """
    try:
        from . import paint_style
        return (paint_style.MARKER_GEOM_TY4 if marker == "by_status"
                else paint_style.MARKER_GEOM_UNIFORM)
    except Exception:
        return {"circle": {"r_scale": 1.0}, "square": {"half_scale": 1.0},
                "triangle": {"base_scale": 1.0, "height_scale": 1.0}}


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
        # ── R3 维度驱动（`app/paint_style.py`）────────────────────────────────
        # 维度键优先；**旧键全部保留**（`scheme`/`legend_pos`/`labels`(bool)…）⇒ 老调用方零改动。
        # `palette` 维度 → `scheme`（`resolve()` 会把 palette 翻成 scheme 一并给出）。
        self.palette = o.get("palette", None)
        self.scheme = o.get("scheme", None) or _palette_to_scheme(self.palette or "app")
        # `marker` 维度：by_status(默认, 按状态定形状) / uniform(老行为: 三形状同尺度)
        self.marker = o.get("marker", None) or "uniform"
        self.marker_geom = o.get("marker_geom", None) or _marker_geom(self.marker)
        # `basemap` 维度：map_png(卫星底图 1:1 裁剪) / land_flat(平涂陆海) / none
        _bm = o.get("basemap", None)
        if _bm is None:
            _bm = "map_png" if (o.get("map_path") or o.get("map_surface") is not None) else "none"
        self.basemap = _bm
        # `labels` 维度：none / edge / inside（旧键 labels(bool) 仍可用）
        self.labels_mode = o.get("_labels_mode", None) or (
            "edge" if o.get("labels") else "none")
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
        # 用户要求"**默认不要图例**"（2026-10-02）⇒ 默认 `"none"`。
        # `"auto"` / `"tl" / "tr" / "br" / "bl"` 全部**保留**，调用方（P5 的界面）显式要才画。
        # ⚠ 这会改变默认输出（不再自动选角画图例）—— 已按纪律告知 Lead 并给出改前/改后 sha。
        self.legend_pos = o.get("legend_pos", "none")
        self.legend_entries = o.get("legend_entries", None)
        self.legend_bg = bool(o.get("legend_bg", False))  # 图例半透明底框, 默认不画
        #: **渐变线**（用户要求"配置还要加上渐变线"）：轨迹连线按**两端强度插值**着色。
        #: 默认 **False** ⇒ 不动既有输出（既有两条路：历史段=起点等级色 / 预报段=白色）。
        #: 开启后是**第三条路**：每段按 `subdiv` 细分成小段、逐小段取插值色，避免"一段一色"的台阶。
        self.gradient_line = bool(o.get("gradient_line", False))
        #: 渐变线的**每段细分数**（越大越平滑、代价线性增长）。见报告 §3「细分段数 vs 观感/代价」。
        self.gradient_subdiv = max(1, int(o.get("gradient_subdiv", 8)))
        #: 轨迹线着色方式：`"white"`（**默认，老行为**，逐位兼容）/ `"category"`（Ty4 §4.4：
        #: 历史段取**起点等级色**，见 `_draw_ty_path` 里 T3 的说明）。`TY4_PRESET` 用 `category`。
        self.track_line = o.get("track_line") or "white"
        #: 非正式报（报时不在 00/06/12/18）的**半径比例**。默认 **1.0 = 与正式报同径**
        #: （老行为，逐位兼容）；`TY4_PRESET` 用基准图实测比值 **0.64**（正式报中位 ⌀11 vs
        #: 非正式报中位 ⌀7，n=6 —— 脆弱性见 `_draw_ty_points` 注释与报告 §R4-c）。
        self.nonofficial_scale = float(o.get("nonofficial_scale", 1.0))
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
        #: 保证老调用方/老测试零改动；`"ty4"` = **Typhoon4 精确模式**
        #: （画布公式来自**基准图像素实测**：`t4_render.py:252-308` 只做整图 resize、与参考图不符，
        #:   公式与验算见报告 `output/exploration/r4_ty4_fix.md`）：
        #: px/° 恒为 `ty4_px_per_deg`(=30，即底图原生 10800×5400/360×180)、
        #: `W = max(min_w, ceil(bbox_w_px) + 2×WidthSpace_px)`、底图 **1:1 裁剪不缩放**、
        #: 轨迹严格居中，且 `margin_w/margin_h` 按**像素**解释（不是 deg 模式里的"度"）。
        _cm = o.get("canvas_mode") or "fit"
        self.canvas_mode = "ty4" if _cm == "ty4" else "fit"
        #: ty4 模式的 px/°。默认 30；若换底图可按 `底图宽/360` 传进来。
        # ⚠ 显式传 `None` 也必须安全（R10：`px_per_deg=None` 就是"未覆盖"的语义）
        _ppd = o.get("px_per_deg")
        self.ty4_px_per_deg = (float(_ppd) if _ppd is not None
                               else float(_TY4_PX_PER_DEG))
        #: **R10：`px_per_deg` 显式覆盖**（`None`/未给 ⇒ 行为与 R9 逐位相同）。
        #: 给了值 ⇒ **用它当 k**（画布仍按 `canvas_w/canvas_h`）⇒ 把"**画布多大**"与
        #: "**地图多清晰 / 看多大范围**"**解耦**：
        #:   · `px_per_deg=30` ⇒ 底图**原生清晰**（看到更大范围）；
        #:   · `px_per_deg=40` = 30×(2560/1920) ⇒ **Ty4 构图比例不变**（纯等比放大 1.333×）；
        #:   · 不给 ⇒ 现状（`k` 由 contain 公式从画布+`pad_ratio` 推出）。
        self.px_per_deg_given = ("px_per_deg" in o and o.get("px_per_deg") is not None)
        # ── R9 画布参数化（用户 2026-10-03 五条）──
        #: **画布尺寸是参数**：默认 2560×1440（见 `TY4_PRESET`），任意更大尺寸可用。
        #: `None` ⇒ 回到"老 Ty4 公式"（`max(min_w, bbox×30 + 2×WidthSpace)`）——
        #: **保留它正是为了仍能按基准图尺寸出图**（rita 3129×1080 / haiyan 2541×1080 /
        #: megi 1920×1080 / sinlaku 2742×2312）以便与基准图叠加比对。
        self.canvas_w = o.get("canvas_w")
        self.canvas_h = o.get("canvas_h")
        #: **留白比例**（每侧）= `pad_ratio × 轨迹 bbox 跨度`。用户裁定 **0.1**。
        #: **不是**固定像素、**也不是**固定角度 —— 它乘在 bbox（=画布尺度）上，**画布放大时同步放大**。
        #: 与 Ty4 原版**并不冲突**（更正 2026-10-03）：原版 `WidthSpace=360 / HeightSpace=270` 是在
        #: **固定 1920×1080 画布**上的固定像素 ⇒ 本身就是固定比例 **360/1920 = 0.1875**（横）、
        #: **270/1080 = 0.25**（纵）；Ty4 把画布固定住才让"像素"看起来像"常数"。
        #: 现按**比例参数化**：用户最新给的 `pad_ratio = 0.1` 为准，原版的 0.1875/0.25 并列记在
        #: `TY4_PRESET` 注释里备查；两者都随画布等比放大，**没有例外**。
        self.pad_ratio = o.get("pad_ratio")
        #: 画布模式算出的 px/°（**等比缩放的载体**）；`None` ⇒ 用固定 `ty4_px_per_deg`(30)。
        self._ty4_k = None
        #: 等比基准宽度：`scale = canvas_w / 1920`（标记/线宽/字号都定义在 1920×1080 尺度）。
        self.scale_base_w = float(o.get("scale_base_w", 1920.0) or 1920.0)
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

    # ── Ty4 线宽 / 点径（真源码：`Typhoon4Py/t4_render.py:205-213` 的 `px_track`/`px_point`）──
    #:
    #: 二进制静态证据（RE4，`Typhoon4.exe!0x44AB89-0x44ABC6`）：**线宽是查表，不是公式** ——
    #: 读样式记录字段 `[ebx+0x4c]`，映射 `0→5, 1→6, 2→1, 3→2, 4→3, else→4`，随后
    #: `0x44AC1D push esi` 作 `CreatePen` 的宽度参数 ⇒ Ty4 线宽是 **6 档离散值**。
    #: ⚠ **与本实现冲突**：下面 `_px_track()` 用 `round(track_deg×k)`（k=30 ⇒ 0.05×30=2）。
    #:   `[ebx+0x4c]` 索引的来源、以及它如何对应 `Settings.ini` 的 `Track=0.05`，**未取到**
    #:   ⇒ 只记录冲突，**不据此改数**。
    #: ⚠ 点径/尺寸数字（⌀11 / 9.0×9.0 / 8.9×7.9 等）**像素反推，未由二进制验证**
    #:   （x32dbg 动态取参未打通；Ghidra 也未给出尺寸公式 —— 顶点是**数据数组**，
    #:   见下方 `_point_style` 前的"方法论边界"；环境坑见
    #:   `output/exploration/re4_environment_pitfalls.md`）。
    #: ⚠ 上面这张线宽查表位于**控件画笔** `FUN_0044aae0` 的代码里（不是地图标记画笔）
    #:   ⇒ 它**作为二进制事实成立**，但**是否适用于地图标记同样未证**。
    #: 详见 `output/exploration/re4b_ghidra.md` §8/§9（含 §3.6 被推翻的更正）。

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

    # ── Ty4 精确模式画布（**基准图像素实测**；`t4_render.py:252-308` 的整图 resize 与参考图不符）──

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
        # ── R9：**参数化画布 + 比例留白 + 等比缩放**（用户 2026-10-03）──
        # `canvas_w/canvas_h` 给出时 ⇒ 画布尺寸**由调用方定**，px/° 反过来由"装得下 bbox+留白"推出：
        #   p   = pad_ratio（每侧，占 bbox 跨度的比例；用户裁定 0.1）
        #   k_w = canvas_w / (dl × (1+2p))      k_h = canvas_h / (db × (1+2p))
        #   k   = min(k_w, k_h)                 # contain：**约束方向**恰好占 1/(1+2p) = 83.3%
        # ⇒ 长宽比**自由**（非 16:9 也行，不会变形：经纬同 k）；画布变大 ⇒ k 变大同 ⇒
        #   点径/线宽/字号（都按 k 算）**同步等比放大**；`canvas_w/1920` 即等比倍数。
        # **留白与 Ty4 原版不冲突（更正）**：原版 `WidthSpace=360/HeightSpace=270` 是**固定 1920×1080
        #   画布上的固定像素** ⇒ 本身就是固定比例 `360/1920=0.1875`（横）/`270/1080=0.25`（纵）。
        #   现按比例参数化（用户最新 `pad_ratio=0.1` 为准；原版 0.1875/0.25 并列备查），
        #   **画布缩放时留白同步等比放大**（pad_px = p × bbox × k，k ∝ 画布宽 ⇒ 比值 1.3333）。
        #   legacy 分支（`canvas_w=None`）仍可复现原版固定像素留白，用于基准尺寸出图。
        _cw, _ch = self.canvas_w, self.canvas_h
        if _cw and _ch and (self.pad_ratio is not None or self.px_per_deg_given):
            dl = max(1e-6, lon1 - lon0)
            db = max(1e-6, lat1 - lat0)
            if self.px_per_deg_given:
                # **R10：显式 k**（画布尺寸与"看多大范围/多清晰"解耦）
                #   · k=30 ⇒ 底图原生 1:1（清晰、范围大）；k=40 ⇒ 纯等比 1.333×（Ty4 构图比例不变）
                # `pad_ratio` 在此**不参与**（窗口 = 画布/k；轨迹仍**居中**、四周余量相等）。
                k = float(self.ty4_px_per_deg)
            else:
                p = float(self.pad_ratio)
                k = min(float(_cw) / (dl * (1.0 + 2.0 * p)),
                        float(_ch) / (db * (1.0 + 2.0 * p)))
                # ⚠ **窗口不可能超过一整圈经度**（2026-10-05 用户实测：全球图底图错位/被整体缩放）。
                #   留白是"每侧占 bbox 跨度 p" ⇒ 窗口 = 跨度 × (1+2p)。单台风跨度小，没问题；
                #   但**全球/多洋区图**的跨度本来就接近 360°，×1.2 会得到 376° > 360°
                #   ⇒ 地图必须绕回去画（窗口内出现 16° 重复）⇒ 底图与轨迹对不上、大西洋被劈开。
                #   这里把"想要的经度窗口"夹到 360°，并把 k **抬高**到 `_cw/360`（只可能变大
                #   ⇒ 内容仍装得下，只是留白变小）。纬度方向不受影响（最多 180°）。
                if dl * (1.0 + 2.0 * p) > 360.0:
                    k = max(k, float(_cw) / 360.0)
            out_w, out_h = int(_cw), int(_ch)
            self._ty4_k = k
            cx_deg = (lon0 + lon1) / 2.0
            cy_deg = (lat0 + lat1) / 2.0
            half_lon = out_w / k / 2.0
            half_lat = out_h / k / 2.0
            # 窗口中心 = 轨迹 bbox 中心（**Ty4 语义：轨迹居中、四周余量相等**），窗口尺寸 = 画布 / k
            bounds = (cx_deg - half_lon, cx_deg + half_lon,
                      cy_deg - half_lat, cy_deg + half_lat)
            return bounds, out_w, out_h
        self._ty4_k = None
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
            # R9：但如果画布是**参数化**的（`canvas_w/canvas_h/pad_ratio` 生效），
            # 就用 `_ty4_canvas` 推出的 `k` ⇒ **点径/线宽/字号随画布等比放大**（`k = 30 × canvas_w/1920`）。
            self._k = self._ty4_k if self._ty4_k else self.ty4_px_per_deg
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
        """先铺全幅背景, 再按 `basemap` 维度铺底图。

        `basemap`：`map_png`（卫星底图 1:1 裁剪，现有行为）/ `land_flat`（平涂陆海）/
        `none`（只留背景色）。缺省时按"有没有给地图"推断，**老调用方行为不变**。
        """
        bg = self.bg_color or (200, 220, 240)
        surf.fill(bg)
        if self.basemap == "none":
            return
        if self.basemap == "land_flat":
            self._draw_land_flat(surf, bounds, inner)
            return
        _map_img = map_surface
        if _map_img is None and self.map_path and os.path.exists(self.map_path):
            try:
                _map_img = pygame.image.load(self.map_path).convert()
            except Exception:
                _map_img = None
        if _map_img is not None:
            self._draw_map_crop_into(surf, _map_img, bounds, inner)

    def _draw_land_flat(self, surf, bounds, inner):
        """`basemap="land_flat"`：平涂陆海（QQ 那种）。

        掩膜用 `map/land.png`（10800×5400 的 **alpha 掩膜**：陆地 alpha=255、海洋 0），
        与底图同一套经纬→像素规则裁剪，再用**两次 blit**上色（不做逐像素 Python 循环）：
        `(255,255,255,0) --BLEND_RGBA_MAX--> 掩膜` 归一化，再 `--BLEND_RGBA_MULT--> 陆色`，
        最后贴到海色上。全程 6 次 Surface 操作。
        """
        from . import paint_style
        mask_path = self.map_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "map", "land.png")
        if not os.path.exists(mask_path):
            _proj_warn("land-flat-missing",
                       f"basemap='land_flat' 需要陆海掩膜，但找不到 {mask_path} ⇒ 本次只铺背景色。")
            return
        try:
            img = pygame.image.load(mask_path).convert_alpha()
        except Exception as e:
            _proj_warn("land-flat-load", f"陆海掩膜加载失败({e}) ⇒ 本次只铺背景色。")
            return
        mw, mh = img.get_size()
        lon_min, lon_max, lat_min, lat_max = bounds
        span_lon = max(1e-6, lon_max - lon_min)
        span_lat = max(1e-6, lat_max - lat_min)
        sx = int(((lon_min % 360.0) % 360.0) / 360.0 * mw)
        sw = max(1, int(span_lon / 360.0 * mw))
        sy = max(0, min(int((90.0 - lat_max) / 180.0 * mh), mh - 1))
        sh = max(1, min(int(span_lat / 180.0 * mh), mh - sy))
        if sx + sw > mw:                      # 跨 180°：只取到右缘（平涂底图可以接受）
            sw = mw - sx
        try:
            # ⚠ 必须 `smoothscale`（**不是** `scale`）：`map/land.png` 的 alpha 是"陆地占比"，
            # 平滑缩放后海岸线得到**分数 alpha** ⇒ 下面两色混合才产生中间色（抗锯齿海岸线）。
            # 用最近邻会把海岸线变硬，而基准图的海岸线**是抗锯齿的**（sim-core 实测：
            # 内部 100% 纯色、边界 126 277 px 全是过渡色）。
            crop = pygame.transform.smoothscale(img.subsurface((sx, sy, sw, sh)),
                                                (inner.w, inner.h))
        except Exception:
            crop = pygame.transform.smoothscale(img, (inner.w, inner.h))
        # 线性混合（不是硬阈值上色）：`sea×(1−a) + land×a`，a = 陆地占比 ✓
        norm = pygame.Surface((inner.w, inner.h), pygame.SRCALPHA)
        norm.fill((255, 255, 255, 0))
        norm.blit(crop, (0, 0), special_flags=pygame.BLEND_RGBA_MAX)   # 归一化 alpha，RGB→白
        land = norm.copy()
        land.fill(tuple(paint_style.BASEMAP_COLORS["land"]) + (255,),
                  special_flags=pygame.BLEND_RGBA_MULT)                # 上陆色，alpha 保留占比
        sea = pygame.Surface((inner.w, inner.h))
        sea.fill(paint_style.BASEMAP_COLORS["sea"])
        sea.blit(land, (0, 0))                                          # 普通 alpha 混合 ⇒ 中间色
        surf.blit(sea, (inner.x, inner.y))

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
                    # 抗锯齿：**所有段都走 `paint_aa.aa_line`**。
                    # ⚠ 历史：这里曾有一个调用侧上限 `_AA_LINE_MAX_PX = 96`，超过就 aliased 直绘
                    # （当时的理由：aa_line 的精灵尺寸 ∝ 段长，长跳段会造成大额临时分配）。
                    # 2026-10-02 Lead 把守卫做进了 `paint_aa` 内部（`_AA_LINE_CHUNK_PX=96`
                    # 等长分块 + 接缝补偿；实测单次分配 64.7 MB → 665 KB / 97×，沿线
                    # 201 点采样 `zero_alpha_gaps: 0`）⇒ 这个上限**从保护变成了有害**：
                    # 它让 >96px 的长段（全屏级路径/长跳段）**彻底失去 AA**。
                    # 分块后内存边界由模块内负责 ⇒ 调用侧不该再留第二个阈值
                    # （两个真相源会随底座改动悄悄失配）。故**删除**该上限。
                    if not self.gradient_line:
                        if self.track_line == "category":
                            # **T3 修（2026-10-03）**：历史段连线取**起点等级色**（Ty4 §12.1 / §4.4）。
                            # 起因（像素证据）：用户看到"点周围一圈浅描边、相邻点无法融合"——
                            # 剖面显示那圈浅色是 **(255,255,255) 的白线**（不是标记的描边）：
                            # 在前期打转区，密集的白线绕着每个标记 ⇒ 看起来像一圈浅边。
                            # Ty4 的历史段线是**等级色**（与标记同色系）⇒ 线与点融合。
                            # ⚠ 预报段在 Ty4 里是白色（§12.1），但我们的数据**没有 forecast/tau 字段**
                            # （解析器未保留 tau）⇒ 无法逐点区分；这里统一按起点等级色，
                            # 偏差只影响"预报段"（通常只有末尾几点）。「历史段按起点等级色」是
                            # Ty4 §4.4 的 [确证·代码] 行为，方向与参考图一致。
                            col = scheme_color(self.scheme, _eff_category(p1))
                        else:
                            col = (255, 255, 255)      # 老行为（默认，逐位兼容）
                        paint_aa.aa_line(surf, col, (x1, y1), c2, line_w)
                    else:
                        # 渐变线（第三条路，仅开关打开时生效）：本段按两端强度插值着色，
                        # 并细分成 `gradient_subdiv` 个小段，避免"一段一色"的台阶。
                        self._draw_gradient_segment(surf, p1, p, (x1, y1), c2, line_w)
            prev = (c, p)

    def _draw_gradient_segment(self, surf, p1, p, a, b, line_w):
        """把一段轨迹画成**强度渐变**（两端等级色线性插值 + 细分）。

        颜色取**等级色**（`scheme_color(scheme, _eff_category(pt))`），与定点同源；
        在 RGB 空间线性插值 ⇒ 中间色不在调色板里，这正是"渐变"的代价与观感。
        细分段数 = `self.gradient_subdiv`（默认 8；见报告 §3「段数 vs 观感/代价」）。
        """
        col0 = scheme_color(self.scheme, _eff_category(p1))
        col1 = scheme_color(self.scheme, _eff_category(p))
        n = self.gradient_subdiv
        dx, dy = b[0] - a[0], b[1] - a[1]
        for k in range(n):
            t0 = k / n
            t1 = (k + 1) / n
            tm = (t0 + t1) * 0.5
            c = (int(round(col0[0] + (col1[0] - col0[0]) * tm)),
                 int(round(col0[1] + (col1[1] - col0[1]) * tm)),
                 int(round(col0[2] + (col1[2] - col0[2]) * tm)))
            paint_aa.aa_line(surf, c, (a[0] + dx * t0, a[1] + dy * t0),
                             (a[0] + dx * t1, a[1] + dy * t1), line_w)

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

    # ── 形状选择 ──────────────────────────────────────────────────────────────
    #: ⚠ **二进制对"地图标记的形状/空心"没有结论** ⇒ 本方法的形状规则与尺寸数字一律是
    #:   **像素反推，未由二进制验证**（更正与依据：`output/exploration/re4b_ghidra.md` §9；
    #:   环境坑：`output/exploration/re4_environment_pitfalls.md`）。
    #:
    #: 曾写过"形状跳表 `0x0044AFAC`（`[ebx+0x44]` 成对索引 (0,1)/(2,3)/(4,5)）⇒ 从二进制侧
    #: 支持'两档（实心/空心）'"—— **该表述已被推翻**：那张跳表属于**控件画笔**
    #: `FUN_0044aae0`（`CPaintDC` + `PatBlt` + `GetClientRect(控件自身 HWND)`，地址在易语言
    #: 控件注册表 `.rdata:0x0055B364`），**不是地图标记画笔**；控件的两档 ≠ 地图标记的两档。
    #:
    #: **仍然成立的二进制事实（已核实）**：
    #:   · `Polygon` 全程序**仅 1 处**：`Typhoon4.exe!0x004432DD`（`FUN_00442c00` 的 `case 0xc`）
    #:   · `Ellipse` 仅 2 处：`0x00442EB5`（`FUN_00442c00` mode 4）、`0x0044AE2C`（控件画笔）
    #:   · ⇒ `FUN_00442c00` 是**唯一能画 Polygon（三角）的函数** ⇒ **最可能**的轨迹点实现；
    #:     但**"最可能"≠"已证明"**：它的两条 thunk 落在函数指针表 `0x54B6C0–0x54B7FF`，而
    #:     **全程序没有任何指令引用该表区间**（立即数扫描 0 处）⇒ 调用点**静态不可见**，
    #:     由易语言运行时按索引走
    #:   · 程序**链了 MFC**：Ghidra FID 把 `0x0053B048` 匹配成 `CDC::LineTo`（VS2003/2005）
    #:
    #: ⚠ **方法论边界（值得留痕）**：**三角顶点公式不在二进制里，在数据里** —— `case 0xc`
    #:   只读调用方预算好的点数组（`[p2+0xc]`，`n = 元素数/2`），**不读 `[+0x24]/[+0x30]`、
    #:   不调用 `DPtoLP`**，已反编译范围内**不存在 ×1.414 / ×0.866 之类系数**
    #:   ⇒ 对这类目标，**量基准图像素反推尺寸是正确方法**，不是"没去找源码"。
    #:   详见 `re4b_ghidra.md` §8（顶点）与 §9（归属判定）。

    def _point_style(self, p) -> tuple:
        """返回 (形状, 颜色)。形状: 'circle'/'square'/'triangle'。

        · `marker="by_status"`（R3 维度默认）—— **形状规则的证据分两档，别混**：
          **① 像素实测（有样本）**：`{DB, EX}` → **三角**（DB 28/28、EX 24/24 实测全为三角，且**非三角点里
             一个 DB/EX 都没有**；DB 集中在轨迹开头 i=0..8 ⇒ 状态驱动）、`{SS, SD}` → **方块**（SS 2/2）。
             4 张 Ty4 基准图严格口径实测（见报告 §5.3）。
          **② 用户裁定 + 与 QQ 侧一致（基准图无样本 ⇒ 未由像素验证）**：
             `{WV, LO, MD, INV, INVEST}` → **三角**。依据 = 用户 2026-10-03 明确裁定
             （"lo 和 wv 还有其他的肯定是三角"）+ **QQ 侧 `paint_render_qq.py:281-285` 的
             `shape_for_code` 本来就把 `DB/WV/LO/MD` 全归三角**（"非热带阶段"）⇒ 两侧统一。
             ⚠ **这 5 个状态在 4 张 Ty4 基准图里零样本**，所以**不是实测**，**不许写成实测**。
          `{TD/TS/TY/ST/HU…}`（热带阶段）→ **圆**。
        · `marker="uniform"` = **老行为，逐位兼容**（SS/SD→方块、EX 族→三角、其余→圆）。
        """
        st = str(p.get('st', '')).upper()
        if self.marker == "by_status":
            # **R7 形状规则（2026-10-03）**：三角家族 = 非热带阶段
            #   · 像素实测：DB、EX（见 docstring ①）
            #   · 用户裁定 + QQ 侧一致：WV、LO、MD、INV、INVEST（见 docstring ②；**基准图无样本**）
            #   ⚠ 只改**形状**；取色仍走 `_ty4_category_of(wind)`（T6 已定：按风速）。
            #     旁证：`Typhoon4Py/t4_track.py:88-97` 的 `Fix.category` 恰好点名
            #     **DB/LO/WV/INVEST 且 wind<34 → TD，其余一律 `category_of(wind)`** ⇒
            #     对 WV/LO 用风级色**有源码依据**（MD/INV 未被该行点名，按同一规则处理）。
            # ══ R14（用户 2026-10-04 直接裁定；与 QQ 侧同源，两边必须一致）══
            # **形状**：三角 = DB/EX/WV/LO/MD/INV/INVEST；方块 = SD/SS；圆 = 其余全部
            # **颜色**：
            #   · 随风速变 = TS/TY/ST/HU + **EX/SS/LO/WV**（SS 有下限特例）
            #   · 固定不变 = TD/SD/DB/MD/INV/INVEST
            #       TD/DB/MD/INV/INVEST → 色阶**最弱那一档**（Ty4 = TD 的 (110,193,234)）
            #       SD → 副热带低压色 (130,190,255)
            #   · SS 特例 = 变色，但**下限夹到 TS 档**（不含最弱那档的蓝）
            #   · **任何标记都不许是灰**（用户："不要让我看见灰色图标"）
            # 用户原话："**不要完全参考 ty4 了**" ⇒ 以下按裁定实现，不再反推 Ty4。
            # （历史：T6/R7 曾按 Ty4 反推出"三角家族一律按风速"⇒ DB/MD/INV/INVEST 也变了色，本版拆开。）
            if st in ("DB", "EX", "WV", "LO", "MD", "INV", "INVEST"):
                if st in ("EX", "WV", "LO"):
                    # 这三个**随风速变**（只查风速表 ⇒ 永不落灰）
                    col = scheme_color(self.scheme, _ty4_category_of(p.get('w', 0)))
                else:
                    # DB / MD / INV / INVEST：**固定 = 最弱那一档**（TD 蓝）
                    col = scheme_color(self.scheme, "TD")
                return ("triangle", col)
            if st in ("SS", "SD"):
                if st == "SD":
                    # SD：**固定** = 副热带低压色 (130,190,255)（用户："SD 也不要变色"）
                    return ("square", scheme_color(self.scheme, "SD"))
                # SS：**变色但下限夹到 TS 档**（用户："SS 要变色但没有 td 的蓝色"）
                c = _ty4_category_of(p.get('w', 0))
                if c == "TD":                 # 最弱那档 ⇒ 抬到 TS
                    c = "TS"
                return ("square", scheme_color(self.scheme, c))
            # 圆 = 其余全部（TD/TS/TY/ST/HU/…）
            if st == "TD":
                # TD：**固定** = 最弱那一档（用户："TD 为什么要变色?"）
                return ("circle", scheme_color(self.scheme, "TD"))
            # 其余热带/未知状态：**随风速变**，但**绝不落灰** ——
            # ⚠ 原来这里用 `_eff_category()`：低风速它会返回 `"DB"` ⇒ `WIKI_SCALE["DB"]=(150,150,150)` **灰**，
            #   这正是用户看到的"灰色图标"。改用只查风速的 7 档表（最弱 = TD 蓝）。
            return ("circle", scheme_color(self.scheme, _ty4_category_of(p.get('w', 0))))
        # ── 以下 = 老行为（uniform）──
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

        逐形状尺寸由 `self.marker_geom` 决定（R3：`marker` 维度）：
        `uniform` = 全 1.0（老行为）；`by_status` = Ty4 基准图严格口径实测的比例
        （方块≈0.82×、三角≈0.81×宽 0.72×高，相对圆的半径）。
        """
        g = self.marker_geom or {}
        if shape == "square":
            rr = r * float((g.get("square") or {}).get("half_scale", 1.0))
            paint_aa.aa_rect(surf, color, cx, cy, rr)
        elif shape == "triangle":
            bs = float((g.get("triangle") or {}).get("base_scale", 1.0))
            hs = float((g.get("triangle") or {}).get("height_scale", 1.0))
            h = r * 1.6 * hs
            bw = r * bs
            pts3 = [(cx, cy - h * 0.62), (cx - bw, cy + h * 0.38), (cx + bw, cy + h * 0.38)]
            paint_aa.aa_poly(surf, color, pts3)
        else:
            rs = float((g.get("circle") or {}).get("r_scale", 1.0))
            paint_aa.aa_disc(surf, color, (cx, cy), r * rs)

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
        # §11.2 `AllPoints`: **0 ⇒ 只画线不画点**（Ty4 `t4_render.py:365`）。
        # ⚠ 原实现是 `pts[-1:]`（只画最后一个定位点）—— 那**不是** Ty4 的语义，
        # 且与 UI 上"是否显示所有定位点"的开关含义不符 ⇒ 改为空池（一个点都不画）。
        # 默认 `all_points=True` ⇒ 默认输出不变。
        pool = pts if self.all_points else []
        for p in pool:
            c = self._proj(float(p.get('lo', 0)), float(p.get('la', 0)), bounds, inner)
            if c is None:
                continue
            shape, color = self._point_style(p)
            # ⚠ **T2① 修订（2026-10-03，用户二次质疑后）**：起初我依据"源码无 official 概念"
            # （`grep -c official` = 0/0；`t4_render.py:383-388` 对所有点用一个 `r`）删掉了减半。
            # 用户追问"为什么非正式报和正式报一个大小了" ⇒ 回**基准图**按 official 分组实测
            # （腐蚀+2 口径，4 图 250 点）：正式报 n=244 中位 **11.0**；非正式报 n=**6** 中位 **7.0**
            # ⇒ **基准图里非正式报确实更小**（≈0.64×），故按实测比值恢复尺寸差（不再是拍脑袋的 0.5）。
            # ⚠ 脆弱性：样本仅 6 个、"更小"的**机制未证实**（可能真是一条尺寸规则，也可能是原程序把
            # 它们画成半透明/混色、被我的容差核量小）；源码对此沉默 ⇒ 属**像素推断**，见报告 §R4-c。
            rr = r if self._is_official(p) else max(1.0, r * self.nonofficial_scale)
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
            # ⚠ 2026-10-02（Lead 裁定）：**不再用 `wind×0.5` 编造半径**。
            # Ty4 的风圈来自 ATCF 的 `RAD/BP/TK` 行（§8：r34/r50/r64，单位海里，`/60` 转度，
            # 画 45° 灰色扇形）。本机数据里**没有** RAD 行 ⇒ 原来那个 `wind×0.5` 是**假数据**，
            # 用它画得再像 Ty4 也是"画了一个不存在的气旋尺度"，属用保真外衣掩盖数据造假。
            # ⇒ **无真实 r34 就不画**（并在 UI/报告里说明）。真实数据到位后按 §8 实现。
            rad34 = int(p.get('r34', 0) or 0)
            if rad34 <= 0:
                _proj_warn("rad-no-data",
                           f"风圈：本机数据无 RAD 行（该点无真实 r34，wind={wind}kt）⇒ 不画风圈。"
                           f"（旧实现用 wind×0.5 编造半径；§8 的 r34/r50/r64 扇形需真实 RAD 数据）")
                continue
            # 风圈同样无抗锯齿 ⇒ 改走 aa_ring（超采样圆环）
            paint_aa.aa_ring(line_surf, ring_color, c, max(4, rad34 + self.point_size), 1)
        surf.blit(line_surf, (0, 0))

    def _draw_legend(self, surf):
        """图例。几何来自**基准图像素实测**（megi 1920×1080 与 sinlaku 2312 两点标定）
        —— 随**画布高度**缩放；`t4_render.py:486-494` 的"∝ px/°"模型与实测**不符**。

        megi 1920×1080 实测(§5.1)：7 行、**行距 27 px**、**色点 ⌀11**、色点右缘距画布右边
        **6 px**、内容下缘距底 **8 px**、**文字与圆点同色 + 右对齐 + 无底框无描边**。
        sinlaku H=2312 实测行距 58.0 ⇒ 58/27 = 2.148 ≈ 2312/1080 = 2.141
        ⇒ **尺寸 ∝ 画布高**（`row = 27·H/1080`、`dot = 11·H/1080`、`pad ≈ dot/2`）。
        ⚠ `Typhoon4Py` 的模型是"∝ px/°"（`dot = round(py_per_deg*0.8)`）⇒ 在 30 px/° 下
        给出 dot 24 / row 59，是实测值的两倍 —— 那个模型是错的（§5.3），不要照它。
        """
        entries = self.legend_entries or _default_entries()
        w, h = surf.get_size()
        s = h / 1080.0
        row = 27.0 * s                    # 行距
        dot = 11.0 * s                    # 色点**直径**
        pad = dot / 2.0                   # §5.2: pad ≈ dot/2
        n = max(1, len(entries))
        block_h = row * n
        label_w = 0
        if self.font is not None:
            label_w = max((self.font.size(lb)[0] for lb, _ in entries), default=0)
        box_w = dot + 6.0 + label_w
        pos = self.legend_pos
        if pos == "auto":
            pos = _auto_legend_corner(entries, int(box_w), int(block_h + pad), w, h,
                                      self._point_density)
        right = pos in ("tr", "br")
        top = pos in ("tl", "tr")
        # 圆点列：右侧锚点 ⇒ 圆点**右缘**距画布边 pad；左侧锚点做镜像。
        # （Ty4 源码对左侧锚点用 `x_dot = pad` + `anchor="rm"` 会把文字推到画布外，
        #  属其自身缺陷；我们镜像成"点在左、字在右"，见报告 §1 说明。）
        x_dot_left = (w - pad - dot) if right else pad
        y0 = pad if top else (h - block_h - pad)
        if self.legend_bg:
            # 可选半透明底框（**我们的**功能，默认关；Ty4 参考图无底框）
            bw, bh = int(box_w + pad * 2), int(block_h + pad * 2)
            bx = (w - bw) if right else 0
            by = 0 if top else (h - bh)
            panel = pygame.Surface((bw, bh), pygame.SRCALPHA)
            pygame.draw.rect(panel, (15, 18, 25, 235), panel.get_rect(), border_radius=8)
            pygame.draw.rect(panel, (200, 210, 230, 90), panel.get_rect(), 1, border_radius=8)
            surf.blit(panel, (bx, by))
            x_dot_left += (bw - int(box_w)) if right else 0
        cx = x_dot_left + dot / 2.0
        for i, (label, cat) in enumerate(entries):
            cy = y0 + i * row + row / 2.0
            color = scheme_color(self.scheme, cat)
            # 色点是**圆** ⇒ 走抗锯齿底座
            paint_aa.aa_disc(surf, color, (cx, cy), dot / 2.0)
            if self.font is not None:
                # §5.1: 文字**与圆点同色**、**右对齐**、**无描边**（原实现是白字+4 向描边）
                ts = self.font.render(label, True, tuple(color[:3]))
                ty = int(round(cy - ts.get_height() / 2.0))
                if right:
                    surf.blit(ts, (int(round(x_dot_left - 6 - ts.get_width())), ty))
                else:
                    surf.blit(ts, (int(round(x_dot_left + dot + 6)), ty))

    def _draw_labels(self, surf, tys, bounds, inner):
        if self.font is None or self.labels_mode == "none":
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
