# -*- coding: utf-8 -*-
"""typhoon4(WikiProject Track Drawer 1.2.7.2)路径图的等价渲染器(轮次 163)。

## 这个文件是什么
把 typhoon4 的路径图**按实测规格**复刻出来, 供 Typhoon-sim 使用。
规格来源(每条都标了是**实测**还是**从 Typhoon4Py 代码推的**):
  · 实测 = Lead 从参考图/底图上直接量出来的像素事实
  · 代码 = 从 `G:\\气象\\数据\\Typhoon4(绘图)\\Typhoon4Py\\t4_render.py` / `t4_track.py` 读到
详见 `output/sim/_reference/TY4_LEAD_NOTES.md` 与 `TY4_SPEC.md`。

## ⚠ 三条与 `Typhoon4Py` **不一致**的地方(以本文件为准)
1. **TD 配色**: 本文件用 **`(110,193,234)`**(实测); `Typhoon4Py` 写的是 `(94,186,255)`,
   而后者在 4 张参考图里**出现 0 次**。
2. **底图原点**: 本项目底图 `[GLOBAL].png` 是 **lon 0..360** 存的(实测: `120E` 标签在 x=3600),
   所以 `lon0 = 0`; `Typhoon4Py` 的 `MAP_BOUNDS` 写 `-180` ⇒ 会整体偏 180°。
3. **经典色阶**: `Typhoon4Py` 里 `PALETTE_CLASSIC = dict(PALETTE_2023)`(等于没实现)。
   本文件**只实现 2023 色阶**, `new_color=0` 时**显式报错**而不是假装有两套。

## 用法
    python tools/ty4_render.py --track <bdeck 或 json> --out out.png [--width 1920 ...]

或作为库:
    from tools.ty4_render import render_map, Fix
    img = render_map(fixes, basemap='G:/气象/typhoon3/map/[GLOBAL].png')
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

# ── 常量 ──────────────────────────────────────────────────────────────────
DEFAULT_MAP = r'G:\气象\typhoon3\map\[GLOBAL].png'
DEFAULT_PX_PER_DEG = 30.0        # 实测: [GLOBAL].png 是 10800x5400 = 360x180 度
BG = (15, 23, 31)
#: **预报段连线的颜色**(实测: 白色, 与底图混合后呈 133,133,153 / 194,194,204 /
#: 255,255,255 三档)。历史段用的是**起点等级色**。
WHITE = (255, 255, 255)

#: **2023 色阶**。除 TD 外与 `Typhoon4Py/t4_track.py:49` 的 `PALETTE_2023` 逐字节相同;
#: TD 用**实测值** —— 代码里那个 (94,186,255) 在 4 张参考图里出现 0 次(实测 6 万像素级)。
PALETTE_2023: Dict[str, Tuple[int, int, int]] = {
    'TD': (110, 193, 234),   # 浅蓝  实测
    'TS': (77, 255, 255),    # 青    (0x4D,0xFF,0xFF)
    'C1': (255, 255, 217),   # 淡黄  (0xFF,0xFF,0xD9)
    'C2': (255, 217, 140),   # 黄    (0xFF,0xD9,0x8C)
    'C3': (255, 158, 89),    # 橙    (0xFF,0x9E,0x59)
    'C4': (255, 115, 138),   # 红    (0xFF,0x73,0x8A)
    'C5': (141, 117, 230),   # 紫    (0x8D,0x75,0xE6)
    'SD': (204, 204, 204),   # 副热带(代码值, 参考图里没出现 ⇒ 推定)
    'SS': (204, 204, 204),
    'UN': (204, 204, 204),
}

LEGEND_EN = {'TD': 'Depression', 'TS': 'Storm', 'C1': 'Category 1',
             'C2': 'Category 2', 'C3': 'Category 3', 'C4': 'Category 4',
             'C5': 'Category 5', 'SD': 'Subtropical Depression',
             'SS': 'Subtropical Storm'}
LEGEND_ZH = {'TD': '热带低压', 'TS': '热带风暴', 'C1': '一级', 'C2': '二级',
             'C3': '三级', 'C4': '四级', 'C5': '五级',
             'SD': '副热带低压', 'SS': '副热带风暴'}

#: 强度分段。代码(`t4_track.py`)用 `>=` 逐档抬升 ⇒ **下界含、上界不含**。
#: ⚠⚠ **必须升序**: `category_of()` 里 `else: break` 假设"一旦不满足就停",
#: 若写成降序, 175 kt 会**一路通过并被反复覆盖**, 最后停在最低档 ⇒ 全图变 TD/TS。
#: 2026-09-30 踩过: 我写成降序 + 保留 break, `category_of(175)` 返回 `'TS'`,
#: 渲染出来 51/56 个点都是 TD。**而逐像素一致率仍有 99.188%** ——
#: 因为轨迹只占 0.85% 像素, **颜色全错在百分比上看不出来**。
#: 是靠"数两边的调色板像素"(参考图七档均衡 / 我的几乎全 TD)才发现的。
CATEGORY_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (34.0, 'TS'), (64.0, 'C1'), (83.0, 'C2'),
    (96.0, 'C3'), (113.0, 'C4'), (137.0, 'C5'),
)


def category_of(wind_kt: float) -> str:
    """kt -> 等级(下界含、上界不含)。用模块默认阈值。"""
    return _cat_of(wind_kt, CATEGORY_THRESHOLDS)


def _cat_of(wind_kt: float, thresholds) -> str:
    """kt -> 等级。**`thresholds` 必须升序**(见 `CATEGORY_THRESHOLDS` 的说明)。"""
    cat = 'TD'
    for lo, name in thresholds:
        if wind_kt >= lo:
            cat = name
        else:
            break
    return cat


# ── 数据 ──────────────────────────────────────────────────────────────────
@dataclass
class Fix:
    lon: float          # 度(可用 0..360 或 -180..180, 渲染前会 unwrap)
    lat: float
    wind: float = 0.0   # kt
    name: str = ''
    kind: str = 'history'      # history / forecast

    @property
    def cat(self) -> str:
        return category_of(self.wind)


# ── 投影 ──────────────────────────────────────────────────────────────────
@dataclass
class Projection:
    """等经纬度。`lon0/lat0` 是**画布左上角**的经纬度。

    `Typhoon4Py/Projection` 允许横纵比例分开(`px_per_deg` / `py_per_deg`)。
    """
    px_per_deg: float = DEFAULT_PX_PER_DEG
    py_per_deg: Optional[float] = None
    lon0: float = 0.0
    lat0: float = 90.0

    def __post_init__(self) -> None:
        if self.py_per_deg is None:
            self.py_per_deg = self.px_per_deg

    def to_px(self, lon: float, lat: float) -> Tuple[float, float]:
        return ((lon - self.lon0) * self.px_per_deg,
                (self.lat0 - lat) * self.py_per_deg)      # type: ignore[operator]


def unwrap_lon(seq: Sequence[float], center: Optional[float] = None) -> List[float]:
    """把**首点**锚定到离 `center` 最近的 360 度窗口, 再逐点消跳变。

    ⚠ 只锚定**第 0 点**(`Typhoon4Py/t4_render.py:151` 的实现就是这样; 其 README 说的
    "整条路径平移"**不精确** —— html-analyst 已在 `TY4_SPEC.md` 里指出)。
    与该锚点无关的其它点由后面的逐点消跳变拉过去。
    与我们旧版 `plot_tc_tracks.py` 的"在跨线处切断"**完全不同**。
    """
    out = list(seq)
    if center is not None and out:
        out[0] = center + ((out[0] - center + 180.0) % 360.0) - 180.0
    for i in range(1, len(out)):
        while out[i] - out[i - 1] > 180.0:
            out[i] -= 360.0
        while out[i] - out[i - 1] < -180.0:
            out[i] += 360.0
    return out


# ── 渲染参数 ──────────────────────────────────────────────────────────────
@dataclass
class RenderOptions:
    track: float = 0.05          # 路径宽度(度)
    #: 定点大小(度)。⚠ **实测拟合值是 0.36, 不是 `Settings.ini` 里的 0.40**。
    #: `px_point = point*px/2` => 0.36*30/2 = **5.4 px 半径**(直径 10.8, 与规格书实测
    #: 的 **⌀11** 吻合); 而 0.40 给 6.0 px 半径(直径 13) ⇒ **圆点明显偏大、相邻同色点
    #: 会互相重叠连成一条链**(与参考图目视一对比就看出来了)。
    #: 实测(三张图同时变好, 留出验证通过): 逐色总偏差 7163 -> 5017 (-30%);
    #: 逐像素相同 megi 99.371->99.383% / rita 99.516->99.523% / haiyan 99.474->99.486%。
    #: 为什么配置文件是 0.40 而实际是 0.36: **未查明**(可能这 4 张不是用当前 Settings.ini
    #: 画的, 或公式里除的不是 2)。留成参数以便手调。
    point: float = 0.36
    width: int = 1920            # 画布最小宽度(px)
    height: int = 1080           # 画布最小高度(px)
    width_space: int = 360       # 轨迹左右最小留白(px)
    height_space: int = 270      # 轨迹上下最小留白(px)
    priority: int = 2            # 0 不画路径 1 同时 2 定位点置顶 3 按强度排序
    draw_legends: int = 4        # 0 不画 1 左上 2 左下 3 右上 4 右下
    all_points: int = 1          # 是否每个定位点都画
    new_color: int = 1           # 1=2023 色阶; 0 **未实现**(见模块 docstring)
    legend_zh: bool = False
    font_path: Optional[str] = None
    #: 图例字号(px)。**推定 + 拟合**: 在 megi(H=1080) 上按"图例区平均绝对差"扫
    #: 15..26 ⇒ **15 最优(7.991)**, 越大越差。留成参数是为了可手调。
    #: ⚠⚠ 但**即使最优字号, 画上图例仍比不画差**(不画 = 7.576) ⇒ 参考图的文字是
    #: **原版 exe(易语言/GDI)** 光栅化的, 与 PIL 的字形抗锯齿**不可能逐位一致**。
    #: ⇒ **"完全一致"必须分区域定性**: 底图/轨迹/圆点可逐像素; **图例文字只能给容差**。
    legend_font_size: int = 15
    #: **线段取色规则**。规格书说"取线段**起点**的等级"(`t4_render._draw_track`)。
    #: 但实测参考图的 C3/C4 像素数明显多于"起点规则"渲染出来的 ⇒ 怀疑真实规则
    #: 可能是"取两端**较高**者"。做成可调以便实测判定, **默认仍是 start**。
    #:   'start' = 起点等级(规格书说法, 默认) / 'end' = 终点 / 'max' = 两端较高
    seg_color_rule: str = 'start'
    #: **超采样倍率(抗锯齿)**。PIL 的 `ImageDraw` **没有抗锯齿**, 而参考图是原版
    #: exe(GDI)画的、**有**抗锯齿 —— 实测参考图轨迹墨量里只有 **3.3%** 是精确调色板色,
    #: 我的是 **5.1%**, 说明它的笔画被"打散"成了混合色。
    #: 做法: 在**透明图层**上以 N 倍坐标画轨迹与图例, 用 LANCZOS 降采样后再合成到
    #: **1:1 的底图**上 —— **底图完全不受影响**(不做整图缩放)。
    supersample: int = 1
    #: **强度分段阈值(可手调入口)**。默认用规格书/国际标准分级。
    #: ⚠ 待定问题: C2/C3 边界取 **95** 而非 96 时, 三张图的**逐色偏差合计降 9%**
    #: (megi 1518->1242, haiyan 1751->1569, rita 不变, **没有一张变差**),
    #: 且独立吻合"参考图 C3 有约 5 个圆点、96 只分出 2 个"(95 正好把 3 个 95kt 移进 C3);
    #: 但**逐像素相同会降一点**(rita -0.003pp / haiyan -0.010pp)。
    #: 因 96 有标准背书、且逐像素是本项目的第一判据, **默认仍用 96**。
    #: 要试 95: `RenderOptions(category_thresholds=((34,'TS'),(64,'C1'),(83,'C2'),(95,'C3'),(113,'C4'),(137,'C5')))`
    category_thresholds: Optional[Tuple[Tuple[float, str], ...]] = None
    #: 图例尺寸**随画布高度缩放**(实测: megi H=1080 时 row=27/dot=11;
    #: sinlaku H=2312 时 row≈58 ⇒ 比例 = H/1080)。⚠ 不是 ∝ px_per_deg
    #: (`Typhoon4Py` 的 `dot = py_per_deg*0.8` 是错的)。
    legend_scale_with_height: bool = True

    def palette(self) -> Dict[str, Tuple[int, int, int]]:
        if not self.new_color:
            raise NotImplementedError(
                '经典色阶(NewColor=0)**未确证**: Typhoon4Py 里 '
                'PALETTE_CLASSIC = dict(PALETTE_2023)(等于没实现), '
                '而 4 张参考图全是 2023 色阶 ⇒ 我拿不到经典色阶的像素证据。'
                '要支持它, 必须先从 exe 或另一批 NewColor=0 的输出里取得 RGB。')
        return PALETTE_2023

    def px_track(self, proj: Projection) -> int:
        """路径线宽(px)。

        ⚠⚠ **用 `int()` 截断, 不是 `round()`** —— `Typhoon4Py` 写的是
        `int(round(track*px_per_deg))`, 实测**不符**: 默认 `Track=0.05`、`px=30`
        ⇒ `0.05*30 = 1.5`, `round` 给 **2**、截断给 **1**。
        实测判定(对 megi.png 用真 b-deck):
          · 线宽 2 => 圆点连通块 28 个, 逐色总偏差 2277, 像素相同 99.361%
          · 线宽 1 => 圆点连通块 **47** 个, 逐色总偏差 **2031**, 像素相同 **99.371%**
        参考图是 **52** 个分离的圆点 ⇒ **线宽 1 才对**(细线才不会把相邻同色圆点连成长条)。
        三项指标同时变好, 所以这不是噪声。
        """
        return max(1, int(self.track * proj.px_per_deg))

    def px_point(self, proj: Projection) -> float:
        """定点**半径**(px)。代码:`point * px_per_deg / 2`。"""
        return max(1.0, self.point * proj.px_per_deg / 2.0)


# ── 画布定位(实测推得) ────────────────────────────────────────────────────
def canvas_for(fixes: Sequence[Fix], opt: RenderOptions,
               px_per_deg: float = DEFAULT_PX_PER_DEG,
               clamp: bool = False
               ) -> Tuple[int, int, float, float, List[float]]:
    """算出 (W, H, x0, y0, unwrapped_lons)。

    规则(实测 4 张参考图尺寸全部吻合):
      · 先 unwrap 经度到 0..360 里**包含轨迹主体**的窗口;
      · `W = max(width,  lon_span_px + 2*width_space)`
        `H = max(height, lat_span_px + 2*height_space)`
      · 画布**以轨迹包围盒为中心**(x0 = round(cx - W/2), 同理 y0)。

    `clamp=True` 时**强制**用 `opt.width/opt.height`, 超出部分被裁掉 ——
    界面上的 `--clamp-size` 走这条路(typhoon4 原程序没有这个开关, 是本项目的
    **便利选项**, 因此默认关闭以保持规格一致)。
    """
    lons = [f.lon for f in fixes]
    lats = [f.lat for f in fixes]
    # 先取轨迹的经度中心, 把整段挪到它附近(避免跨 180 时散成两半)
    c0 = sum(lons) / len(lons)
    lons = unwrap_lon(lons, center=c0)
    xs = [lo * px_per_deg for lo in lons]
    ys = [(90.0 - la) * px_per_deg for la in lats]
    if clamp:
        W, H = int(opt.width), int(opt.height)
    else:
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        # ⚠ **用 `int()` 截断, 不是 `math.ceil`** —— 实测 `sinlaku.png` 是 2742x2312,
        #   而 ceil 给 2742x**2313**(差 1 像素): 真实纵向跨度 1772.x 像素, 截断得 1772,
        #   +2*270 = 2312 ✓。另外三张因为被 `Width`/`Height` 托底, 看不出这个差别。
        W = max(opt.width, int(span_x) + 2 * opt.width_space)
        H = max(opt.height, int(span_y) + 2 * opt.height_space)
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    x0 = cx - W / 2.0
    y0 = cy - H / 2.0
    return W, H, x0, y0, lons


# ── 主渲染 ────────────────────────────────────────────────────────────────
def render_map(fixes: Sequence[Fix], basemap: str = DEFAULT_MAP,
               opt: Optional[RenderOptions] = None,
               px_per_deg: float = DEFAULT_PX_PER_DEG,
               map_cache: Optional[dict] = None,
               origin: Optional[Tuple[float, float]] = None,
               clamp: bool = False) -> Image.Image:
    """渲染。`origin=(x0, y0)` 可**强制画布原点**(用于把"画布定位"与"画轨迹"
    两个误差源分开检验 —— 实测原点拿不准时特别有用)。
    `clamp=True` **强制**用 `opt.width/opt.height`(超出裁掉), 见 `canvas_for`。"""
    opt = opt or RenderOptions()
    pal = opt.palette()
    assert fixes, '没有定位点'
    W, H, x0, y0, lons = canvas_for(fixes, opt, px_per_deg, clamp=clamp)
    if origin is not None:
        x0, y0 = float(origin[0]), float(origin[1])

    # 底图按需裁剪 —— [GLOBAL].png 是 48MB/10800x5400, **绝不整张常驻**。
    cache = map_cache if map_cache is not None else {}
    key = (basemap, int(round(x0)), int(round(y0)), W, H)
    if key in cache:
        img = cache[key].copy()
    else:
        with Image.open(basemap) as full:
            img = full.convert('RGB').crop(
                (int(round(x0)), int(round(y0)),
                 int(round(x0)) + W, int(round(y0)) + H))
        cache.clear()
        cache[key] = img.copy()
    # ── 超采样(抗锯齿): 在**透明图层**上按 N 倍坐标画, 降采样后合成回 1:1 底图 ──
    _N = max(1, int(opt.supersample))
    _layer = None
    if _N > 1:
        _layer = Image.new('RGBA', (W * _N, H * _N), (0, 0, 0, 0))
        dr = ImageDraw.Draw(_layer)
    else:
        dr = ImageDraw.Draw(img)
    # ⚠⚠ `Projection.lon0/lat0` 是**经纬度**, 不是像素! 画布像素原点 (x0,y0) 要换算:
    #     x = (lon - lon0) * px  ,且 x0 = (lon0 - 0) * px  ⇒  lon0 = x0 / px
    #     y = (lat0 - lat) * py  ,且 y0 = (90 - lat0) * py ⇒  lat0 = 90 - y0 / px
    # (2026-09-30 踩过: 直接把 x0/y0 当 lon0/lat0 传进去 ⇒ 坐标变成 (lon-3005)*30,
    #  轨迹全跑到画外, 渲染出来是一张**没有轨迹的底图裁剪**。)
    proj = Projection(px_per_deg * _N, px_per_deg * _N,
                      x0 / px_per_deg, 90.0 - y0 / px_per_deg)

    pts_px = [proj.to_px(lo, f.lat) for lo, f in zip(lons, fixes)]
    _th = opt.category_thresholds or CATEGORY_THRESHOLDS
    cats = [_cat_of(f.wind, _th) for f in fixes]
    lw = opt.px_track(proj)
    r = opt.px_point(proj)

    def draw_lines() -> None:
        for i in range(len(pts_px) - 1):
            # 颜色取**线段起点**的等级(`t4_render._draw_track` 同义)。
            # ⚠ 例外: **预报段是白色** —— html-analyst 放大参考图两端确认
            # "各 3 个上三角 + 白色细线"(白色混合后是 133,133,153 / 194,194,204 /
            # 255,255,255 三档)。重写版把 forecast 当第二条轨道仍画等级色 ⇒ 不一致。
            if fixes[i].kind == 'forecast' and fixes[i + 1].kind == 'forecast':
                dr.line([pts_px[i], pts_px[i + 1]], fill=WHITE, width=lw)
            else:
                dr.line([pts_px[i], pts_px[i + 1]],
                        fill=pal[_seg_cat(opt, cats, i)], width=lw)

    def draw_pts() -> None:
        for (px, py), c, fx in zip(pts_px, cats, fixes):
            # ⚠ **预报定位点画上三角**(历史点是圆) —— 同上, 规格书标为
            # "最容易被忽略但一眼可见"的一处。
            if fx.kind == 'forecast':
                dr.polygon([(px, py - r * 1.25), (px - r * 1.15, py + r * 0.85),
                            (px + r * 1.15, py + r * 0.85)], fill=pal[c])
            else:
                dr.ellipse([px - r, py - r, px + r, py + r], fill=pal[c])

    if opt.priority == 0:
        pass
    elif opt.priority == 3:            # 按强度排序: 弱的先画、强的覆盖在上面
        draw_lines()
        if opt.all_points:
            order = sorted(range(len(pts_px)), key=lambda i: fixes[i].wind)
            for i in order:
                px, py = pts_px[i]
                fx = fixes[i]
                if fx.kind == 'forecast':
                    dr.polygon([(px, py - r * 1.25), (px - r * 1.15, py + r * 0.85),
                                (px + r * 1.15, py + r * 0.85)], fill=pal[cats[i]])
                else:
                    dr.ellipse([px - r, py - r, px + r, py + r], fill=pal[cats[i]])
    elif opt.priority == 2:            # 定位点置顶
        draw_lines()
        if opt.all_points:
            draw_pts()
    else:                              # 1 = 同时(逐段: 线后立刻画点)
        for i in range(len(pts_px) - 1):
            if fixes[i].kind == 'forecast' and fixes[i + 1].kind == 'forecast':
                dr.line([pts_px[i], pts_px[i + 1]], fill=WHITE, width=lw)
            else:
                dr.line([pts_px[i], pts_px[i + 1]], fill=pal[cats[i]], width=lw)
            if opt.all_points:
                _one_pt(dr, pts_px[i], cats[i], fixes[i], pal, r)
        if opt.all_points:
            _one_pt(dr, pts_px[-1], cats[-1], fixes[-1], pal, r)

    if opt.draw_legends:
        # 图例几何要跟着超采样倍率缩放(否则 N 倍画布上图例会缩到左上角一小块)
        _draw_legend(dr, (W * _N, H * _N), opt, pal, scale=float(_N))
    if _layer is not None:
        # 只在**轨迹/图例图层**上降采样, 再合成到 1:1 的底图 ⇒ **底图零改动**
        _small = _layer.resize((W, H), Image.LANCZOS)
        img = Image.alpha_composite(img.convert('RGBA'), _small).convert('RGB')
    return img


def _seg_cat(opt: 'RenderOptions', cats: Sequence[str], i: int) -> str:
    """线段取色: 按 `opt.seg_color_rule` 在起点/终点/两端较高者之间选。

    ⚠ `cats` 是按**强度档序**给的字符, 比较要用档序而不是字母序。
    """
    order = {c: n for n, c in enumerate(
        ('TD', 'TS', 'C1', 'C2', 'C3', 'C4', 'C5', 'SD', 'SS', 'UN'))}
    if opt.seg_color_rule == 'end':
        return cats[i + 1]
    if opt.seg_color_rule == 'max':
        a, b = cats[i], cats[i + 1]
        return a if order.get(a, 0) >= order.get(b, 0) else b
    return cats[i]


def _one_pt(dr, pt, cat, fx, pal, r) -> None:
    """画一个定位点: **预报点=上三角, 历史点=圆**(实测)。"""
    px, py = pt
    if fx.kind == 'forecast':
        dr.polygon([(px, py - r * 1.25), (px - r * 1.15, py + r * 0.85),
                    (px + r * 1.15, py + r * 0.85)], fill=pal[cat])
    else:
        dr.ellipse([px - r, py - r, px + r, py + r], fill=pal[cat])


def _legend_font(size: int, opt: RenderOptions) -> ImageFont.ImageFont:
    for p in ([opt.font_path] if opt.font_path else []) + [
            r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf',
            r'C:\Windows\Fonts\simhei.ttf', 'DejaVuSans.ttf']:
        if not p:
            continue
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_legend(dr: ImageDraw.ImageDraw, size: Tuple[int, int],
                 opt: RenderOptions,
                 pal: Dict[str, Tuple[int, str]],
                 scale: float = 1.0) -> None:
    """图例。

    **实测**(megi.png): 七行(不含副热带时)、**行距 ≈27 px**、圆点半径 ≈6 px、
    文字**按等级着色**、**右对齐**(圆点贴右边, 文字在其左)。
    `scale` 供超采样时等比放大(传 N 倍画布尺寸时必须同时传 N)。
    """
    labels = LEGEND_ZH if opt.legend_zh else LEGEND_EN
    cats = [c for c in ('TD', 'TS', 'C1', 'C2', 'C3', 'C4', 'C5')]
    W, H = size
    # 图例尺寸**随画布高度缩放**(实测 megi H=1080: row=27, dot ⌀11, pad=6;
    # sinlaku H=2312: row≈58)。⚠ 不是 ∝ px_per_deg。
    # ⚠ `size` 传进来的是**当前画布**尺寸(超采样时是 N 倍), 所以比例要按**逻辑高度**
    #   算(H/scale), 否则超采样会把图例放太大。
    k = ((H / scale) / 1080.0) * scale if opt.legend_scale_with_height else scale
    f = _legend_font(max(8, int(round(opt.legend_font_size * k))), opt)
    row = 26.5 * k
    r = 5.5 * k
    last_cy = H - 23.0 * k
    for i, c in enumerate(cats):
        cy = last_cy - (len(cats) - 1 - i) * row
        cx = W - 8.0 * k - r
        txt = labels[c]
        tw = dr.textlength(txt, font=f)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=pal[c])
        # 文字**按等级着色**、**右对齐**(贴着色块左侧)
        dr.text((cx - r - 6.0 * k - tw, cy - 11.0 * k), txt, font=f, fill=pal[c])


# ── 数据读取 ──────────────────────────────────────────────────────────────
def load_fixes(path: str) -> List[Fix]:
    """读 ATCF b-deck(BEST/OFCL 行)或本项目的 JSON 轨迹。

    b-deck 行格式(逗号分隔, 字段 0=basin,1=编号,2=时间,6=lat,7=lon,8=风):
        例: `WP, 08, 2014063000,   , XRQA,   0,  73N, 1597E,  15,    0, DB`
    纬度字段带 N/S 后缀、经度带 E/W 后缀(也有纯数字的写法)。
    """
    fixes: List[Fix] = []
    if path.lower().endswith('.json'):
        d = json.load(open(path, encoding='utf-8'))
        # 反解出来的轨迹是**按等级分组**的 ⇒ 每组给一个落在该档内部的代表风速,
        # 这样 `category_of()` 会还原出同一个等级(用于逐像素复现参考图)。
        rep = {'TD': 25.0, 'TS': 40.0, 'C1': 70.0, 'C2': 90.0, 'C3': 100.0,
               'C4': 120.0, 'C5': 140.0}
        for cat, arr in d['points'].items():
            for p in arr:
                fixes.append(Fix(p['lon'], p['lat'], rep.get(cat, 25.0)))
        fixes.sort(key=lambda f: f.lon)          # 按经度排(大致沿时间)
        return fixes
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            fl = [x.strip() for x in line.split(',')]
            if len(fl) < 9:
                continue
            try:
                lat = _deg(fl[6])
                lon = _deg(fl[7])
                wind = float(fl[8] or 0)
            except (ValueError, IndexError):
                continue
            if lat is None or lon is None:
                continue
            fixes.append(Fix(lon % 360.0 if lon >= 0 else lon, lat, wind,
                             name=fl[4] if len(fl) > 4 else ''))
    return fixes


def _deg(s: str) -> Optional[float]:
    """ATCF b-deck 的经纬度字段 -> 度。

    ⚠⚠ **b-deck 把经纬度按"十分之一度"存, 小数点被省略**:
    样例行 `WP, 08, ...,  73N, 1597E, ...` 里 **`73N` = 7.3N**、**`1597E` = 159.7E**
    (整整度写 `1200E` = 120.0)。
    2026-09-30 踩过: 直接 `float(s[:-1])` ⇒ **坐标放大 10 倍** ⇒ 画出来的图整个错位,
    而且 `_ty4_match_bdeck.py` 里因此**所有候选覆盖率归零**, 一度以为"数据里没有这张图"。
    """
    s = s.strip()
    if not s:
        return None
    suf = s[-1].upper()
    if suf in 'NSEW':
        body = s[:-1]
        v = float(body) if '.' in body else float(body) / 10.0
        return -v if suf in 'SW' else v
    if '.' in s:
        return float(s)
    v = float(s)
    return v / 10.0 if abs(v) >= 100 else v


def _interactive(fixes, map_path, opt, a, origin) -> None:
    """交互式预览窗口: 改参数立刻重画, s 保存, q 关闭。

    用 pygame(项目本来就依赖它)。底图裁剪有缓存 ⇒ 重画约 1 秒。
    """
    import pygame
    pygame.init()
    cache: dict = {}
    state = {'point': opt.point, 'track': opt.track, 'legend': opt.draw_legends}
    disp = None

    def redraw():
        o = RenderOptions(track=state['track'], point=state['point'],
                          width=opt.width, height=opt.height,
                          width_space=opt.width_space,
                          height_space=opt.height_space,
                          draw_legends=state['legend'], priority=opt.priority)
        im = render_map(fixes, map_path, o, origin=origin, map_cache=cache)
        k = a.show / float(max(im.size))
        if k < 1.0:
            im = im.resize((max(1, int(im.size[0] * k)),
                            max(1, int(im.size[1] * k))), Image.LANCZOS)
        surf = pygame.image.fromstring(im.tobytes(), im.size, 'RGB')
        font = pygame.font.Font(None, 20)      # ⚠ 用 Font(None) 而非 SysFont(None):
        hint = ('point=%.2f track=%.2f legend=%d  [s]保存 [q]关闭 '
                '[+/-]点径 [[/]]线宽 [g]图例' % (state['point'], state['track'],
                                              state['legend']))
        return surf, font.render(hint, True, (255, 240, 150)), im.size

    surf, hint, size = redraw()
    disp = pygame.display.set_mode((surf.get_width(),
                                    surf.get_height() + 26))
    pygame.display.set_caption('typhoon4 路径图预览')
    print('预览窗口已开: %dx%d 源图。s=保存到 %s, q=关闭' % (size[0], size[1], a.out))
    clock = pygame.time.Clock()
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                return
            if e.type != pygame.KEYDOWN:
                continue
            if e.key in (pygame.K_q, pygame.K_ESCAPE):
                pygame.quit()
                return
            if e.key == pygame.K_s:
                os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
                # 保存**全尺寸**结果(不是窗口里的缩略图)
                o = RenderOptions(track=state['track'], point=state['point'],
                                  width=opt.width, height=opt.height,
                                  width_space=opt.width_space,
                                  height_space=opt.height_space,
                                  draw_legends=state['legend'],
                                  priority=opt.priority)
                im = render_map(fixes, map_path, o, origin=origin,
                                map_cache=cache)
                im.save(a.out)
                print('已保存 -> %s (%dx%d)' % (a.out, im.size[0], im.size[1]))
            elif e.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                state['point'] = min(1.0, state['point'] + 0.02)
                surf, hint, size = redraw()
                disp = pygame.display.set_mode((surf.get_width(),
                                                surf.get_height() + 26))
            elif e.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                state['point'] = max(0.04, state['point'] - 0.02)
                surf, hint, size = redraw()
                disp = pygame.display.set_mode((surf.get_width(),
                                                surf.get_height() + 26))
            elif e.key == pygame.K_RIGHTBRACKET:
                state['track'] = min(0.5, state['track'] + 0.01)
                surf, hint, size = redraw()
                disp = pygame.display.set_mode((surf.get_width(),
                                                surf.get_height() + 26))
            elif e.key == pygame.K_LEFTBRACKET:
                state['track'] = max(0.01, state['track'] - 0.01)
                surf, hint, size = redraw()
                disp = pygame.display.set_mode((surf.get_width(),
                                                surf.get_height() + 26))
            elif e.key == pygame.K_g:
                state['legend'] = {4: 3, 3: 2, 2: 1, 1: 0, 0: 4}[state['legend']]
                surf, hint, size = redraw()
                disp = pygame.display.set_mode((surf.get_width(),
                                                surf.get_height() + 26))
        disp.fill((15, 23, 31))
        disp.blit(surf, (0, 0))
        disp.blit(hint, (6, surf.get_height() + 4))
        pygame.display.flip()
        clock.tick(30)


def main() -> None:
    ap = argparse.ArgumentParser(description='typhoon4 路径图渲染器')
    ap.add_argument('--track', required=True, help='b-deck 文件或轨迹 JSON')
    ap.add_argument('--out', required=True)
    ap.add_argument('--map', default=DEFAULT_MAP)
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--height', type=int, default=1080)
    ap.add_argument('--width-space', type=int, default=360)
    ap.add_argument('--height-space', type=int, default=270)
    ap.add_argument('--legend', type=int, default=4)
    ap.add_argument('--priority', type=int, default=2)
    ap.add_argument('--track-px', type=float, default=0.05)
    ap.add_argument('--point-px', type=float, default=0.4)
    ap.add_argument('--x0', type=float, default=None,
                    help='强制画布左上角像素 x(用于与实测窗口对齐)')
    ap.add_argument('--y0', type=float, default=None)
    ap.add_argument('--clamp-size', action='store_true',
                    help='强制输出恰好 width x height(超出裁掉); '
                         '默认按 typhoon4 规格自动放大')
    # ── 预览(轮次 163 补: 目标要求"交互/参数暴露/**预览**/导出" ──────────────
    #   这个工作量下"预览"最该解决的是: 底图 48MB、整图渲染慢、产物又大
    #   (实测 57 条风暴会自动放大到 8394x2058)。所以给两级:
    #     --dry-run  只算不画: 立刻告诉你输出会多大、有几个点、跨度多少
    #     --preview  出一张缩略图先看, 满意了再跑全尺寸
    ap.add_argument('--dry-run', action='store_true',
                    help='只计算并报告输出尺寸/点数/跨度, **不读底图也不渲染**(秒回)')
    ap.add_argument('--preview', type=int, default=0, metavar='MAXPX',
                    help='出缩略图(最长边 MAXPX, 如 900)写到 <out> 并加 _preview 后缀')
    # ── 交互式预览窗口(轮次 163 补: 目标 ⑤ 要求"预览") ────────────────────
    #   打开一个 pygame 窗口显示结果, 按键:
    #     s = 保存到 --out     q/Esc = 关闭(不保存)
    #     +/- = 点径 0.02 步进重画    [/] = 线宽(度) 0.01 步进重画
    #     g = 切换图例位置(0->4->3->2->1->0)   t = 切换 title
    #   改参数后**立即重画**(约 1s, 因为底图已缓存), 所以是真正的"所见即所得"。
    ap.add_argument('--show', type=int, default=0, metavar='MAXPX',
                    help='打开交互式预览窗口(最长边 MAXPX, 如 1200); s 保存, q 关闭')
    a = ap.parse_args()
    fixes = load_fixes(a.track)
    if not fixes:
        raise SystemExit('没读到定位点: %s' % a.track)
    opt = RenderOptions(track=a.track_px, point=a.point_px, width=a.width,
                        height=a.height, width_space=a.width_space,
                        height_space=a.height_space, draw_legends=a.legend,
                        priority=a.priority)
    if a.dry_run:
        W, H, x0, y0, lons = canvas_for(fixes, opt, clamp=bool(a.clamp_size))
        lats = [f.lat for f in fixes]
        cats = [f.cat for f in fixes]
        from collections import Counter
        print('轨迹    : %d 个定位点' % len(fixes))
        print('经度    : %.3f - %.3f  (跨度 %.2f 度)' % (min(lons), max(lons),
                                                        max(lons) - min(lons)))
        print('纬度    : %.3f - %.3f  (跨度 %.2f 度)' % (min(lats), max(lats),
                                                        max(lats) - min(lats)))
        print('输出尺寸: %d x %d  (请求 %d x %d%s)'
              % (W, H, a.width, a.height,
                 ', 已 clamp' if a.clamp_size else ', 按 typhoon4 规格自动放大'
                 if (W, H) != (a.width, a.height) else ''))
        print('画布原点: (%.1f, %.1f)  = lon %.3f, lat %.3f'
              % (x0, y0, x0 / DEFAULT_PX_PER_DEG,
                 90 - y0 / DEFAULT_PX_PER_DEG))
        print('等级分布: %s' % dict(Counter(cats).most_common()))
        print('线宽/点径: %d px / r=%.1f px' % (opt.px_track(Projection()),
                                               opt.px_point(Projection())))
        print('图例    : %s' % ('不画' if not a.legend else
                                {1: '左上', 2: '左下', 3: '右上', 4: '右下'}[a.legend]))
        print('(未读底图、未渲染 —— 加 --preview N 出缩略图, 或去掉 --dry-run 出全图)')
        return
    org = None if (a.x0 is None or a.y0 is None) else (a.x0, a.y0)
    if a.show:
        _interactive(fixes, a.map, opt, a, org)
        return
    img = render_map(fixes, a.map, opt, origin=org, clamp=bool(a.clamp_size))
    out = a.out
    if a.preview:
        k = a.preview / float(max(img.size))
        if k < 1.0:
            img = img.resize((max(1, int(img.size[0] * k)),
                              max(1, int(img.size[1] * k))), Image.LANCZOS)
            root, ext = os.path.splitext(a.out)
            out = '%s_preview%s' % (root, ext or '.png')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    img.save(out)
    print('%d 点 -> %s  (%dx%d)' % (len(fixes), out, img.size[0], img.size[1]))


if __name__ == '__main__':
    main()
