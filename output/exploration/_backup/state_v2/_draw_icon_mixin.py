# py/ty_sim_mixins/_draw_icon_mixin.py
"""台风图标 + 名称 + 信息框渲染 Mixin。"""
from __future__ import annotations
import functools
import logging
import math
import os
import numpy as np
import pygame
from ..typhoon import TrackPoint
from ..constants import (
    f_name, rt, TXT,
    C3, C4, C5_L, C5_M, C5_D, MD_COLOR, C2_MINUS, C3_MINUS, C4_ST, STS,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
    INFO_BOX_BG, INFO_BOX_BORDER,
    ICON_SET_SMCY, ICON_SET_DEFAULT,
)
# ⚠ W2(2026-10-02): 这里**不要**再写模块级 `from ..smcy_icon import ...`。
# `smcy_icon.py` 顶层 `import cv2` ⇒ 模块级导入会让每个用户启动时就加载 OpenCV
# (约 60-100 MB RSS + 数十 ms)。`get_smcy_manager` / `_TOTAL_FRAMES` /
# `_FRAME_INTERVAL_MS` 一律在**用到它的方法内部** import(与本文件既有写法一致,
# 见 `_preload_upcoming_icon`), `sys.modules` 有缓存, 重复 import 只是字典查找。
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..utils import get_tropical_points, max_wind_from_points, display_category
from ..utils import fmt_short_time, peak_point, movement_speed_kt
from ..ace_engine import _ace_eligible
from ..cache_store import SurfaceCache

logger = logging.getLogger(__name__)

# 名字/巅峰标注**不再**用固定字号字体渲染再缩放: 字号由 `_NAME_BASE_PX` /
# `_PEAK_BASE_PX` 按 factor 算、按字号取字体(`_sized_font`), 见 `_sized_glow_text`。
# (旧代码在这里放着 `_box_font`=28px / `_peak_font`=19px 两个固定字体, 已删除。)

#: 名字锚点沿"图标右上对角线"的**收缩系数**(1.0 = 原来的正对角, 离图标最远)。
#: 取 1/√2 + 2% 余量: 锚点 (a, -a) 到图标中心的距离 = a√2, 要求 > 图标内切圆半径
#: (`_icon_target_px/2`) ⇒ a > 0.7071·half; 0.72 保证**名字左上角恒在内切圆之外**,
#: 同时比原来近 ~28%·half。用户嫌"太右上角"(2026-10-01) ⇒ 收到这里。
#: 可在实例上覆盖 `sim.name_anchor_in` 做进一步微调; 方向微调走 `name_dx/name_dy`。
NAME_ANCHOR_IN = 0.72


def name_offset_px(icon_half, name_factor, name_dx, name_dy, icon_factor,
                   anchor_in=NAME_ANCHOR_IN):
    """名字左上角相对**图标中心**的偏移(真实 px)。

    绘制(`draw_typhoon_name`)与「名字位置」预览的**布局估算**共用这一个口径 ——
    预览要按真实 `icon_factor` 缩放并把画布算准, 两边绝不能各写一份公式。
    `name_dx/name_dy` 的单位是 `icon_factor==1` 时的 px(这里乘 `icon_factor`),
    `dy` 正 = 向下。
    """
    nm = int(8 + 4 * name_factor)          # 名字与图标之间的边距(随字号略增)
    a = icon_half * anchor_in
    return (int(a + nm + name_dx * icon_factor),
            int(-(a + nm) + name_dy * icon_factor))

# 法9/G2: 普通/编辑信息框"实时ACE"数字缓存(量化到 4 位小数,数值字面量跨帧极少变化)。
# 与 draw_info_boxes_mixin 的 _ace_digit_cache 独立,避免跨模块共享同一 key 空间。
_box_ace_digit_cache: dict = {}
_BOX_ACE_DIGIT_CACHE_MAX = 64


def _text_shadow(surf, color=(0, 0, 0), alpha=120):
    """由带 alpha 的文字 surface 生成同形阴影(去背景信息框的对比度保障)。"""
    try:
        mask = pygame.mask.from_surface(surf)
        sh = mask.to_surface(setcolor=color, unsetcolor=(0, 0, 0, 0))
        sh.set_alpha(120)
        return sh
    except Exception:
        return None


_STROKE_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_info_box_font_cache: dict = {}


def _info_box_font(size: int):
    """按字号缓存 SmartFont(信息框字体缩放用)。"""
    f = _info_box_font_cache.get(size)
    if f is None:
        f = SmartFont(_load_font(FONT_FILE, size, size), _load_font(FONT_FILE, size, size))
        if len(_info_box_font_cache) > 24:
            _info_box_font_cache.pop(next(iter(_info_box_font_cache)))
        _info_box_font_cache[size] = f
    return f


# ── 名字 / 巅峰标注: **按字号直接渲染**, 不再"位图缩放" ──────────────────────
# 旧实现: 固定 28px 字体渲染 → `pygame.transform.smoothscale(×name_factor)`
#   ⇒ name_size=100 时 factor=1.5, 150 时 2.25, 300 时 **4.5** —— 位图放大必然糊
#   (用户 2026-10-01: "你没发现这很糊吗")。巅峰标注 19px 同样问题。
# 新实现: `size = round(基准字号 × factor)` 的字号**直接渲染** ⇒ 边缘锐利;
#   只有描边/辉光的**半径**按 factor 等比铺开, 否则大字号下辉光细得像发丝。
_NAME_BASE_PX = 28        # 100% 档基准字号(旧实现固定 28px 再缩放)
_PEAK_BASE_PX = 19        # 100% 档基准字号(旧实现固定 19px 再缩放)
_GLOW_PAD_PX = 6          # 与 utils.render_glow_text 的 pad 一致(文字落在 (pad,pad))
_name_font_cache: dict = {}
_peak_font_cache: dict = {}
_OUTLINE8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))

#: 名字与标注、标注与标注之间**可见文字(实心像素)**的行间距(px)。**不随字号缩放** ——
#: 因为它是"两条文字之间留多少白", 字号变大时这个留白不该跟着暴涨(用户 2026-10-01:
#: "这个 peak3 为什么这么下面, 不应该在名字下一行吗")。
_LABEL_LINE_GAP_PX = 2
#: "实心文字"的 alpha 阈值(取包围盒用): 高于它才算看得见的笔画(含 8 向描边),
#: 从而把 `_glow_pad` 的透明边距与辉光晕圈排除在定位基准之外。
_TEXT_CORE_ALPHA = 200
_text_core_cache: dict = {}


def text_core_rect(surf, key, pad: int = 0):
    """表面里**实心文字**的包围盒(相对该表面; 按 key 缓存, 每帧都要用)。

    为什么要它: `_get_name_surf` / `_get_peak_label_surf` 返回的面带
    `_glow_pad(scale)` 的透明边距(随字号等比) ⇒ **表面底边 ≠ 文字底边**。
    定位必须用"看得见的那块", 否则字号一大, 那条看不见的边距就把标注推远。
    全透明(或极淡)时退回"文字框"口径, 保证不出现 0 尺寸导致的位置跳变。
    """
    r = _text_core_cache.get(key)
    if r is None:
        r = surf.get_bounding_rect(_TEXT_CORE_ALPHA)
        if r.w <= 0 or r.h <= 0:
            p = max(0, int(pad))
            r = pygame.Rect(p, p, max(1, surf.get_width() - 2 * p),
                            max(1, surf.get_height() - 2 * p))
        if len(_text_core_cache) > 512:
            _text_core_cache.pop(next(iter(_text_core_cache)))
        _text_core_cache[key] = r
    return r



def _sized_font(cache: dict, size: int):
    """按字号缓存 SmartFont(名字/巅峰标注用; 与 _info_box_font 同款写法)。"""
    f = cache.get(size)
    if f is None:
        f = SmartFont(_load_font(FONT_FILE, size, size), _load_font(FONT_FILE, size, size))
        if len(cache) > 24:
            cache.pop(next(iter(cache)))
        cache[size] = f
    return f


def _glow_pad(scale: float) -> int:
    """辉光的透明边距(px) —— 随字号等比; 与 `render_glow_text` 的 pad 同基准。"""
    return max(1, int(round(_GLOW_PAD_PX * max(0.05, float(scale)))))


@functools.lru_cache(maxsize=128)
def _ring_offsets(radius: int):
    """半径 radius 圆周上的采样偏移(radius<=1 时就是 8 邻居)。

    纯函数(只读模块常量 `_OUTLINE8` 与 `math`), 入参 `radius` 是 int
    (两个调用点都传 `round(...)` 的结果) ⇒ 可安全 `lru_cache`。
    每次调用都要跑 `n = round(2πr)` 次 cos/sin/round: 缩放档位下
    `_sized_glow_text` 每帧会为每个文字表面各调 5 次(r=3,2,1 的辉光 + r=1 描边),
    实测约 3,072 次/帧 = 57,968 次 cos + 57,960 次 sin。半径只有个位数个取值,
    缓存后稳态 0 次重算。

    ⚠ 返回**元组**(r==1 分支本来就是元组 `_OUTLINE8`, 此处统一), 以便安全共享
    缓存对象; 两个调用点都只做 `for dx, dy in ...` 迭代, 不做原地修改。
    """
    r = max(1, int(radius))
    if r == 1:
        return _OUTLINE8
    n = max(8, int(round(2.0 * math.pi * r)))
    out = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        dx = int(round(math.cos(a) * r))
        dy = int(round(math.sin(a) * r))
        if (dx, dy) not in out:
            out.append((dx, dy))
    return tuple(out)


def _sized_glow_text(font, text: str, color, scale: float):
    """按目标字号渲染**锐利**文字, 描边/辉光半径按 `scale` 等比。

    观感与 `utils.render_glow_text` 同一套(白字 + 降亮描边 + 3 层外辉光, 同参数:
    dim 0.62 / 辉光 alpha 26·46·96 / pad 6)。`scale≈1` 时**直接调用它** ——
    保证 100% 档与旧观感逐像素一致; 其它档位用这里的局部实现按比例铺开。
    (为什么局部实现: `utils.py` 不在本次写域, 且只有缩放档才需要"半径等比"。)
    返回表面中文字位于 `(_glow_pad(scale), _glow_pad(scale))`。
    """
    if abs(float(scale) - 1.0) < 1e-6:
        from ..utils import render_glow_text
        return render_glow_text(font, text, color)
    from ..utils import darken_color
    scale = max(0.05, float(scale))
    fg = font.render(text, True, (255, 255, 255))
    bk = font.render(text, True, darken_color(color, 0.62))
    w, h = fg.get_size()
    pad = _glow_pad(scale)
    surf = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
    for radius, a in ((3, 26), (2, 46), (1, 96)):
        layer = bk.copy()
        layer.set_alpha(int(a * 0.5))
        for dx, dy in _ring_offsets(round(radius * scale)):
            surf.blit(layer, (pad + dx, pad + dy))
    for dx, dy in _ring_offsets(round(scale)):          # 8 向描边等比
        surf.blit(bk, (pad + dx, pad + dy))
    surf.blit(fg, (pad, pad))
    return surf


def _outline_text(surf, outline_color=(0, 0, 0)):
    """给已渲染文字 surface 加 8 向描边(含换行), 返回 (w+2, h+2)。"""
    try:
        w, h = surf.get_size()
        out = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
        mask = pygame.mask.from_surface(surf)
        shade = mask.to_surface(setcolor=outline_color, unsetcolor=(0, 0, 0, 0))
        for dx, dy in _STROKE_OFFSETS:
            out.blit(shade, (dx + 1, dy + 1))
        out.blit(surf, (1, 1))
        return out
    except Exception:
        return surf


# ── 155+/170+ 紫色滤镜与辉光 ──

_PURPLE_TIERS = (
    # (最低风速, 滤镜强度)
    (170, 0.85),   # 170+: 更紫
    (155, 0.45),   # 155+: 偏紫
)


def _purple_tier(wind: int):
    for tier in _PURPLE_TIERS:
        if wind >= tier[0]:
            return tier
    return None


_PURPLE_LUTS: dict = {}


def _purple_luts(strength_q: int):
    """256 级逐像素饱和度差的整数 LUT(法8): 每级差 d=mx-mn 预计算乘数/加数。
    与原 float32 公式逐像素一致(取整差 ≤1)。strength_q = strength×100。"""
    luts = _PURPLE_LUTS.get(strength_q)
    if luts is None:
        d = np.arange(256, dtype=np.float32)
        k = (strength_q / 100.0) / 255.0
        f = d * k
        f05 = 0.5 * f
        r_perm = np.rint(np.clip(1.0 - f05, 0.0, 1.0) * 1000).astype(np.int16)
        g_perm = np.rint(np.clip(1.0 - 1.1 * f05, 0.0, 1.0) * 1000).astype(np.int16)
        b_perm = np.rint(np.clip(1.0 - f05, 0.0, 1.0) * 1000).astype(np.int16)
        b_add = np.rint(255.0 * f05 * 1000).astype(np.int32)
        luts = (r_perm, g_perm, b_perm, b_add)
        _PURPLE_LUTS[strength_q] = luts
        if len(_PURPLE_LUTS) > 8:
            _PURPLE_LUTS.pop(next(iter(_PURPLE_LUTS)))
    return luts


def _apply_purple_filter(surf: pygame.Surface, strength: float) -> pygame.Surface:
    """按像素饱和度把彩色部分推向紫色；白/灰(低饱和)部分不受影响。

    C5 图标主色为品红 (255,0,255)，"更紫"意味着压低红色通道
    （C5_M=191,0,255 → C5_D=128,0,255），同时压绿提蓝。
    LUT 整数化实现(法8): 免每帧 float32 全图转换。"""
    strength_q = int(round(strength * 100))
    r_perm, g_perm, b_perm, b_add = _purple_luts(strength_q)
    out = surf.copy()
    px = pygame.surfarray.pixels3d(out)
    r = px[..., 0].astype(np.uint16)
    g = px[..., 1].astype(np.uint16)
    b = px[..., 2].astype(np.uint16)
    d = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    px[..., 0] = np.clip(r * r_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 1] = np.clip(g * g_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 2] = np.clip((b * b_perm[d] + b_add[d]) // 1000, 0, 255).astype(np.uint8)
    del px
    return out


# ── TS 强度渐变滤镜: 34kt 原图绿 → 49kt 黄绿(与 STS 图标衔接) ──

_TS_GRAD_MIN_WIND = 34
_TS_GRAD_MAX_WIND = 49


def _ts_gradient_t(wind) -> float:
    """TS 图标滤镜渐变进度: 34kt→0(TS 原图绿, 与 TD 蓝边界清晰), 49kt→1(黄绿=STS 色系)。
    49kt 为渐变端点(逐 1kt, 缓解 48→49 转 STS 的跳变)。"""
    if wind <= _TS_GRAD_MIN_WIND:
        return 0.0
    if wind >= _TS_GRAD_MAX_WIND:
        return 1.0
    return (wind - _TS_GRAD_MIN_WIND) / (_TS_GRAD_MAX_WIND - _TS_GRAD_MIN_WIND)


def _ts_wind_bucket(wind) -> int:
    """TS 渐变风速量化(3kt 一档)。

    逐 1kt 做缓存键会让滤镜几乎每帧失效(实测约 0.55 次/帧、每次 ~4ms 的
    numpy 全图运算); 3kt 一档在绿→黄绿渐变上肉眼无差别, 重算降到约 1/3。"""
    try:
        return int(round(float(wind) / 3.0)) * 3
    except (TypeError, ValueError):
        return _TS_GRAD_MIN_WIND


_TS_GRAD_LUTS: dict = {}


def _ts_grad_luts(t_q: int):
    """TS 渐变滤镜 LUT(与 _purple_luts 同构): 每档进度 t 预计算通道系数。

    绿端(34kt): 保持 TS 原图绿色(SMCY TS 视频/简单 TS 图标均为绿);
    黄绿端(49kt): 提红+少量附蓝、压净蓝 → 与 STS 图标色系(SMCY TS+ #FFFF34 /
                    简单 STS #B8FF00)衔接, 类别切换无跳变;
    中间按 t 线性插值, 通道系数连续, 无瞬变。"""
    luts = _TS_GRAD_LUTS.get(t_q)
    if luts is None:
        t = t_q / 100.0
        r_c = 0.0                                   # 红通道不缩放(保留原纹理)
        g_c = 0.0                                   # 绿通道不缩放
        b_c = 0.0 * (1 - t) - 1.00 * t              # 黄绿端压净蓝
        r_a = 0.80 * t                              # 黄绿端提红 → 与 STS 黄色系衔接
        b_a = 0.20 * t                              # 黄绿端带少量蓝(SMCY TS+ #FFFF34)
        d = np.arange(256, dtype=np.float32)
        f = d / 255.0
        r_perm = np.rint(np.clip(1.0 + r_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        g_perm = np.rint(np.clip(1.0 + g_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        b_perm = np.rint(np.clip(1.0 + b_c * f, 0.0, 1.4) * 1000).astype(np.int16)
        r_add = np.rint(255.0 * r_a * f * 1000).astype(np.int32)
        b_add = np.rint(255.0 * b_a * f * 1000).astype(np.int32)
        luts = (r_perm, g_perm, b_perm, r_add, b_add)
        _TS_GRAD_LUTS[t_q] = luts
        if len(_TS_GRAD_LUTS) > 20:
            _TS_GRAD_LUTS.pop(next(iter(_TS_GRAD_LUTS)))
    return luts


def _apply_ts_gradient_filter(surf: pygame.Surface, wind) -> pygame.Surface:
    """按风速把彩色部分在 绿(34kt, TS 原图)↔黄绿(49kt, STS 色系) 间渐变着色, 白/灰不受影响。"""
    t = _ts_gradient_t(wind)
    t_q = int(round(t * 100))
    r_perm, g_perm, b_perm, r_add, b_add = _ts_grad_luts(t_q)
    out = surf.copy()
    px = pygame.surfarray.pixels3d(out)
    r = px[..., 0].astype(np.int32)
    g = px[..., 1].astype(np.int32)
    b = px[..., 2].astype(np.int32)
    d = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    px[..., 0] = np.clip((r * r_perm[d] + r_add[d]) // 1000, 0, 255).astype(np.uint8)
    px[..., 1] = np.clip(g * g_perm[d] // 1000, 0, 255).astype(np.uint8)
    px[..., 2] = np.clip((b * b_perm[d] + b_add[d]) // 1000, 0, 255).astype(np.uint8)
    del px
    return out


class TySimDrawIconMixin:
    """台风图标、名称、信息框的绘制。"""

    #: 名字 / 巅峰标注的**文字表面**缓存: 键 = ('name'|'peak', 文本, 颜色, 字号)。
    #: 用 `SurfaceCache` 的**字节预算**而不是"条数 256": 字号大时单条面积按平方增长
    #: (28px→126px 面积 ×20), 只按条数封顶时 256 条大字表面能吃到几百 MB —— 与紫
    #: 滤镜缓存(`_purple_frame_cache`)同一个教训。`keyboard_mixin` 里对本属性的
    #: `.clear()` 仍然有效(SurfaceCache 提供 clear)。
    #:
    #: 条目上限 256→1024(2026-10-02 W2 修复 2): 256 条会造成"缩放悬崖"——
    #: 207 个台风同时可见时帧内不同键 = 708 > 256, LRU 每帧把刚放进来的条目又
    #: 逐出。确定性计数实测(`G:\tmp\perf\probe_name_cache.py`):
    #:   cap=256 : 1416 次查找 → 1416 misses + 1416 evictions/帧, 命中率 0%,
    #:             字节只用 2.28/32 MiB(卡条数, 字节预算完全没被利用);
    #:   cap=1024: 帧2 起 0 misses + 0 evictions, 命中率 84%, 字节 7.96/32 MiB;
    #:   cap=2048: 与 1024 逐项相同(帧内键 708 < 1024 ⇒ 无额外收益)。
    #: 12 个台风存活的日常路径在 256 与 1024 下逐项相同 ⇒ 无回归。
    #: 选 1024(而非 2048): 覆盖实测最坏值 708 且留 45% 余量, 字节占用仍远低于
    #: 32 MiB 预算; 将来键数继续增长时由字节预算兜底(那才是本缓存的真闸门)。
    _name_shadow_cache = SurfaceCache("name_text", 32 * 1024 * 1024, 1024)

    _info_box_cache_typhoon: dict = {}
    _info_box_last_data: dict = {}

    _ring_scale_cache: dict = {}
    _center_scale_cache: dict = {}
    _l3_scale_cache: dict = {}
    _ts_grad_cache: dict = {}   # TS 渐变滤镜结果缓存 (w, h, 风速kt)

    @classmethod
    def _clear_icon_scale_caches(cls):
        cls._ring_scale_cache.clear()
        cls._center_scale_cache.clear()
        cls._l3_scale_cache.clear()
        cls._purple_frame_cache.clear()
        cls._ts_grad_cache.clear()

    @classmethod
    def _get_scaled_image(cls, img, new_w, new_h, cat, cache_dict):
        key = (cat, new_w, new_h)
        if key in cache_dict:
            return cache_dict[key]
        scaled = pygame.transform.smoothscale(img, (new_w, new_h))
        if len(cache_dict) > 64:
            cache_dict.pop(next(iter(cache_dict)))
        cache_dict[key] = scaled
        return scaled

    # ── 图标 + 名称 + 信息框 ──
    # 注: _size_factors 由 TySimDrawPathMixin 提供(MRO 在前); 此处不再重复定义,
    # 避免两份实现日后只改一份导致图标/路径尺寸不一致

    def _end_fade_alpha(self, ty):
        """图标"走到末报点"时的淡出系数(0-255); 不适用时返回 None。

        ⚠⚠ **轮次 152 修掉的缺陷**: 判"是否已到末点"必须用**全程点数**
        (`ty.track_total_n`), 不能用 `len(ty.pts)`。

        模拟模式为了"只画已播过的部分"会在画路径前把 `ty.pts` 截断到 `ci+1`
        (见 `sim_mode_mixin._sim_render`)。而本函数原来拿 `len(ty.pts)` 当末点依据,
        截断后 `len(ty.pts) == ci+1` ⇒ `ci >= n_pts-2` 与 `ci >= n_pts-1`
        **恒真** ⇒ progress 恒为 1.0 ⇒ **icon_alpha 恒为 0**。
        表现: "扰动出现一段时间后图标消失, 只剩下一个淡色定位点在动"
        (扰动档 `DB` 的点色是 (150,200,220), 看着就是个白点)。
        实测: 修前 53 条采样里 52 条 alpha=0(只有 ci=0 那条正常);
        修后**存活系统 0 条不可见**, 剩下的 alpha=0 全是已死亡台风按本设置正常淡出。

        `track_total_n` 由 `_sim_sync_path_ty` 写入; 其它模式没有该属性,
        自动退回 `len(ty.pts)` 的旧行为。
        """
        # ⚠ 轮次 153: 必须取 `max(track_total_n, len(ty.pts))`。
        # 内核会把 `tc['track']` 裁到最近 2400 点(`core.py` 的
        # `if len(tc['track']) > 2400`), 而 `ty.pts` 只增不减 ⇒ 超长寿命台风会出现
        # `track_total_n=2400 < len(ty.pts)=2401`, 于是 `ci >= n_total-2` 恒真
        # ⇒ 图标**永久淡出**(正是轮次 152 修掉的那个症状的另一个入口)。
        # 取两者的较大值可同时挡住两种情况: 常规截断时 len(ty.pts)=ci+1 很小 ⇒ 取
        # track_total_n; 内核裁窗时 len(ty.pts) 更大 ⇒ 取它。
        n_total = max(int(getattr(ty, 'track_total_n', 0) or 0), len(ty.pts))
        if n_total < 2 or ty.ci < n_total - 2:
            return None
        # `ty.points_time` 同样被截断过, 插值前必须判下标有效
        if (ty.v.ipos and ty.ci + 1 < len(ty.pts)
                and ty.ci + 1 < len(ty.points_time)):
            total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
            progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
        else:
            # 轮次 153: 点集与时间轴长度失配时**不淡出**(宁可多画图标也不要静默消失),
            # 并留一条日志, 否则这种不一致完全无声。
            if ty.ci >= n_total - 1 and len(ty.points_time) <= ty.ci:
                logger.debug('图标淡出: points_time(%d) 与 pts(%d) 失配, 本帧不淡出',
                             len(ty.points_time), len(ty.pts))
                return None
            progress = 1.0 if ty.ci >= n_total - 1 else 0.0
        return max(0, int(255 * (1.0 - progress)))

    def _icon_live_screen(self, la, lo):
        """**连续运动图标**的屏幕坐标: 对连续差值一次取整(消除 ±1px 抖动)。

        为什么不能直接用 `latlon_to_screen`/`geo_to_screen`(2026-10-01 实测):
        后者是"两段式取整" `int(px*scale) - int(view*scale)`(与地图 blit 同相,
        路径缓存整页平移复用依赖它)。相机逐帧连续前推时, 台风自己的取整与相机的
        取整在**不同帧**跨整数边界; 当台风**不是相机目标**(风季/追踪全部/多台风,
        或任何偏离中心的风暴)时两者时刻必然错开 ⇒ 屏幕坐标在 m/m±1 之间来回跳。
        探针 `G:/tmp/probe_general_jitter.py`: 这种场景下 399 帧里 x 方向翻转
        **146** 次; 改用 `geo_to_screen_round` 后翻转 **0** 次。
        只改**这条每帧现算的图标投影**: 路径点仍走 `geo_to_screen` + 整页平移
        复用, 路径缓存/锚点/`_try_shift_screen_points` 一个字节都没动。
        """
        mv = None
        try:
            mv = self.map_mgr.map_view if self.map_mgr is not None else None
        except Exception:
            mv = None
        if mv is None or not hasattr(mv, 'geo_to_screen_round'):
            return self.latlon_to_screen(la, lo)
        return mv.geo_to_screen_round(lo, la)

    def draw_typhoon_info(self, surface: pygame.Surface, ty,
                          with_name: bool = True) -> None:
        cp = ty.cp()
        if not cp:
            return
        # 提前渐进预载下一类别图标(SMCY 惰性视频: 首次切换会一次性解码卡顿)
        self._preload_upcoming_icon(ty)
        # 接近海岸时预解码登陆特效视频, 避免登陆瞬间首次加载卡顿导致特效缺失
        self._preload_landfall_effect(ty)
        # 拖拽期间使用与路径相同的坐标系：stale screen_points + drag_offset
        # 平滑路径下沿曲线采样，与活线段/路径一致（B12）
        if self.right_button_dragging and (self._drag_offset_x or self._drag_offset_y):
            screen_points = getattr(ty, 'screen_points', None)
            cur_idx = ty.ci
            if not screen_points or cur_idx < 0 or cur_idx >= len(screen_points):
                return
            smooth_sp = ty.v.smooth_screen_points if self.smooth_path else None
            if (smooth_sp and cur_idx < len(ty.pts) - 1
                    and len(ty.points_time) > cur_idx + 1):
                segs = max(1, self.smooth_path_segments)
                total = ty.points_time[cur_idx + 1] - ty.points_time[cur_idx]
                progress = (ty.at - ty.points_time[cur_idx]) / total if total > 0 else 0.0
                progress = max(0.0, min(1.0, progress))
                i0 = cur_idx * segs
                raw = progress * segs
                i1 = min(i0 + int(raw), len(smooth_sp) - 1)
                if i1 < i0:
                    i1 = i0
                p0 = smooth_sp[i1]
                if i1 + 1 < len(smooth_sp):
                    frac = raw - int(raw)
                    p1 = smooth_sp[i1 + 1]
                    x = p0[0] + (p1[0] - p0[0]) * frac
                    y = p0[1] + (p1[1] - p0[1]) * frac
                else:
                    x, y = p0
                x += self._drag_offset_x
                y += self._drag_offset_y
            elif cur_idx < len(screen_points) - 1 and ty.ci < len(ty.pts) - 1:
                total = ty.points_time[ty.ci + 1] - ty.points_time[ty.ci]
                progress = (ty.at - ty.points_time[ty.ci]) / total if total > 0 else 0.0
                progress = max(0.0, min(1.0, progress))
                pt1 = screen_points[cur_idx]
                pt2 = screen_points[cur_idx + 1]
                x = int(pt1[0] + (pt2[0] - pt1[0]) * progress) + self._drag_offset_x
                y = int(pt1[1] + (pt2[1] - pt1[1]) * progress) + self._drag_offset_y
            else:
                x = screen_points[cur_idx][0] + self._drag_offset_x
                y = screen_points[cur_idx][1] + self._drag_offset_y
        else:
            pos = ty.cpos()
            if not pos:
                return
            x, y = self._icon_live_screen(pos['la'], pos['lo'])

        show_icon = not (self.md == self.MODE_EDIT and not self.pl)
        icon_factor = self._size_factors()[1]
        icon_alpha = 255
        v = ty.v

        if show_icon and self.fade_typhoon:
            _f = self._end_fade_alpha(ty)
            if _f is not None:
                icon_alpha = _f

        # 刚生成时的渐入效果（生成后 800ms 内 alpha 从 0 线性至 255）
        if v._spawn_time:
            now = pygame.time.get_ticks()
            elapsed = now - v._spawn_time
            if elapsed < 800:
                icon_alpha = icon_alpha * min(255, int(elapsed * 0.31875)) // 255

        icon_alpha = icon_alpha * getattr(v, 'icon_alpha', 255) // 255

        if show_icon and icon_alpha > 0:
            cat = cp.get('cat', self.get_strength_category(cp['w'], cp['st']))
            trans = self._get_transition(ty)

            if self.cfg.icon_set == ICON_SET_SMCY:
                if v._spawn_time:
                    self._advance_smcy_frame(ty, cat, now, self.sp)
                else:
                    self._advance_smcy_frame(ty, cat, pygame.time.get_ticks(), self.sp)

                if trans:
                    old_cat, old_a, new_cat, new_a = trans
                    old_final = (icon_alpha * old_a) // 255
                    new_final = (icon_alpha * new_a) // 255
                    f = v._smcy_frame
                    if old_final > 0:
                        self._draw_smcy_frame(surface, ty, old_cat, f, x, y, icon_factor, old_final, cp['w'])
                    if new_final > 0:
                        self._draw_smcy_frame(surface, ty, new_cat, f, x, y, icon_factor, new_final, cp['w'])
                else:
                    self._draw_smcy_frame(surface, ty, cat, v._smcy_frame, x, y, icon_factor, icon_alpha, cp['w'])
            else:
                if trans:
                    old_cat, old_a, new_cat, new_a = trans
                    old_final = (icon_alpha * old_a) // 255
                    new_final = (icon_alpha * new_a) // 255
                    if old_final > 0:
                        self._draw_simple_icon(surface, ty, old_cat, cp, x, y, icon_factor, old_final)
                    if new_final > 0:
                        self._draw_simple_icon(surface, ty, new_cat, cp, x, y, icon_factor, new_final)
                else:
                    self._draw_simple_icon(surface, ty, cat, cp, x, y, icon_factor, icon_alpha)

        # with_name: 模拟模式把"台风标签"开关接到这里(原来这个开关在模拟模式下
        # 完全没有作用 —— 名字是这条共享路径画的, 见 sim_mode_mixin 的调用点)
        show_name = (with_name and not (self.md == self.MODE_EDIT and not self.pl)
                     and icon_alpha > 0)
        if show_name:
            self.draw_typhoon_name(surface, ty, x, y, icon_factor, icon_alpha)

        if self.md == self.MODE_NORMAL and self.show_info_box_normal:
            self._draw_info_box(surface, ty, cp)
        elif self.md == self.MODE_EDIT:
            display_pt = cp
            if not self.pl:
                sel = getattr(self, '_edit_selected_point', None)
                if sel is not None and 0 <= sel < len(ty.pts):
                    display_pt = ty.pts[sel]
                elif 0 <= ty.ci < len(ty.pts):
                    display_pt = ty.pts[ty.ci]
            self._draw_info_box(surface, ty, display_pt)

    # ── 类别渐变过渡 ──
    def _get_transition(self, ty):
        """返回 (old_cat, old_alpha, new_cat, new_alpha) 或 None。
        ty.at / points_time 使用相同时间单位：0.5 单位 = 6 模拟小时。
        新图标: 0%→100% (T-3h→T+3h, 线性 6h)
        旧图标: 100%→75% (T-3h→T+2h) 再 75%→0% (T+2h→T+3h)
        """
        if not ty.pts or len(ty.points_time) != len(ty.pts):
            return None
        cur_idx = ty.ci
        now = ty.at

        def _calc(c, n, t):
            elapsed = (now - t) * 12.0
            if elapsed < -3.0 or elapsed > 3.0:
                return None
            # 新图标: 线性 0→1 over 6h
            new_pct = (elapsed + 3.0) / 6.0
            new_a = int(255.0 * max(0.0, min(1.0, new_pct)))
            # 旧图标: 100%→75% over 5h, 再 75%→0% over 1h
            if elapsed <= 2.0:
                old_pct = 1.0 - 0.05 * (elapsed + 3.0)
            else:
                old_pct = 0.75 * (3.0 - elapsed)
            old_a = int(255.0 * max(0.0, min(1.0, old_pct)))
            return (c, old_a, n, new_a)

        if cur_idx + 1 < len(ty.pts):
            cur_cat = ty.pts[cur_idx].get('cat', self.get_strength_category(ty.pts[cur_idx]['w'], ty.pts[cur_idx]['st']))
            next_cat = ty.pts[cur_idx + 1].get('cat', self.get_strength_category(ty.pts[cur_idx + 1]['w'], ty.pts[cur_idx + 1]['st']))
            if cur_cat != next_cat:
                r = _calc(cur_cat, next_cat, ty.points_time[cur_idx + 1])
                if r: return r
        if cur_idx > 0:
            prev_cat = ty.pts[cur_idx - 1].get('cat', self.get_strength_category(ty.pts[cur_idx - 1]['w'], ty.pts[cur_idx - 1]['st']))
            cur_cat = ty.pts[cur_idx].get('cat', self.get_strength_category(ty.pts[cur_idx]['w'], ty.pts[cur_idx]['st']))
            if prev_cat != cur_cat:
                r = _calc(prev_cat, cur_cat, ty.points_time[cur_idx])
                if r: return r
        return None

    # ── 类别切换预载 ──
    def _preload_upcoming_icon(self, ty) -> None:
        """下一报点类别与当前不同时, 提前渐进预载新类别 SMCY 视频帧。
        首次切换类别时视频是惰性打开+解码(几十~上百 ms)→ 画面卡一下,
        并从进入当前段起每帧解码少量帧, 把成本摊平到切换前的十几帧里。"""
        try:
            if self.cfg.icon_set != ICON_SET_SMCY:
                return
            pts = ty.pts
            ci = ty.ci
            if (not pts or ci < 0 or ci + 1 >= len(pts)
                    or len(ty.points_time) != len(pts)):
                return
            cur_cat = pts[ci].get('cat') or self.get_strength_category(pts[ci]['w'], pts[ci]['st'])
            nxt_cat = pts[ci + 1].get('cat') or self.get_strength_category(pts[ci + 1]['w'], pts[ci + 1]['st'])
            if nxt_cat == cur_cat:
                return
            st = getattr(ty, '_icon_preload_state', None)
            key = (ci, nxt_cat)
            if st is not None and st.get('key') == key and st.get('remain', 1) <= 0:
                return
            from ..smcy_icon import get_smcy_manager
            if st is None or st.get('key') != key:
                st = {'key': key, 'hemi': 'S' if ty.v.mirror else 'N',
                      'ts': None, 'remain': 30}
                ty._icon_preload_state = st
            mgr = get_smcy_manager()
            ts = self._smcy_ts_for(ty, nxt_cat, self._size_factors()[1])
            if st.get('ts') != ts:
                st['ts'] = ts
                st['remain'] = 30
            st['remain'] = mgr.preload_window_step(nxt_cat, st['hemi'], 0, 30, 2, ts)
        except Exception as ex:
            ty._icon_preload_state = None
            logger.debug(f"SMCY 图标预载失败: {ex}", exc_info=True)

    # ── 登陆特效预解码 ──
    def _preload_landfall_effect(self, ty) -> None:
        """台风接近(下一报点内)预计算登陆点时, 提前解码 SMCY 登陆特效视频。
        登陆触发瞬间 `get_landfall_frames` 首次解码约 100-160ms, 会卡一帧且
        可能让用户看到登陆特效缺失; 提前解码后触发即播。"""
        try:
            if self.cfg.icon_set != ICON_SET_SMCY:
                return
            pts = ty.pts
            if not pts:
                return
            recs = self._get_precomputed_landfalls(ty)
            if not recs:
                return
            ci = ty.ci
            # 未经过的最近登陆点, 且距当前报点 <= 1 段(即将登陆)
            warmed = getattr(ty, '_landfall_warm', None)
            for rec in recs:
                if rec['seg'] > ci + 1:
                    continue
                if ci > rec['seg']:
                    continue
                p = pts[rec['seg']]
                cat = self.get_strength_category(p.get('w', 0), p.get('st', ''))
                if warmed is None:
                    warmed = set()
                    ty._landfall_warm = warmed
                if cat in warmed:
                    continue
                warmed.add(cat)
                from ..smcy_icon import get_landfall_frames, get_landed_frame
                icon_factor = self._size_factors()[1]
                size = max(20, 4 * round(70 * icon_factor * 1.5 / 4))
                get_landfall_frames(cat, size, size)
                get_landed_frame(cat, 0, (size, size))
                break
        except Exception:
            pass

    # ── 简单图标绘制 ──
    def _draw_simple_icon(self, surface, ty, cat, cp, x, y, icon_factor, icon_alpha):
        ring_img = self.res_mgr.get_image(f"{cat}_ring")
        center_img = self.res_mgr.get_image(f"{cat}_center")
        if not ring_img:
            ring_img = self._create_fallback_ring()
        if not center_img:
            center_img = self._create_fallback_center()
        if not (ring_img and center_img):
            return

        orig_w, orig_h = ring_img.get_size()
        target_size = max(20, int(self._icon_target_px(cat, icon_factor)))
        scale = target_size / max(orig_w, orig_h)
        new_w, new_h = max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        base_ring = self._get_scaled_image(ring_img, new_w, new_h, cat, self._ring_scale_cache)
        # TS 强度渐变滤镜: 34kt 绿(TS 原图) → 49kt 黄绿(与 STS 衔接), 结果按 (尺寸,风速) 缓存
        if cat == 'TS':
            wq = _ts_wind_bucket(cp['w'])
            ckey = (new_w, new_h, wq)
            cring = self._ts_grad_cache.get(ckey)
            if cring is None:
                cring = _apply_ts_gradient_filter(base_ring, wq)
                if len(self._ts_grad_cache) > 40:
                    self._ts_grad_cache.pop(next(iter(self._ts_grad_cache)))
                self._ts_grad_cache[ckey] = cring
            base_ring = cring
        total_rotation = ty.ra + ty.sa

        # tint 并入旋转缓存（避免每帧 tint_image 新建 Surface）
        tint_color = None
        if cat == "C5":
            tint_color = self._get_c5_color(cp['w'])
        elif cat in ("C2-", "C3-", "C4-ST"):
            tint_color = self._get_sub_gradient_color(cp['w'], cat)
        elif cat == "MD":
            tint_color = MD_COLOR
        elif cat == "STS":
            tint_color = STS
        rotated_ring = ty.get_rotated_ring(
            (cat, new_w, new_h), base_ring, total_rotation, ty.mirror,
            tuple(tint_color[:3]) if tint_color else None)

        if icon_alpha < 255:
            rotated_ring = rotated_ring.copy()
            rotated_ring.set_alpha(icon_alpha)

        rect = rotated_ring.get_rect(center=(x, y))

        target_center_size = max(10, int(20 * icon_factor))
        csz = int(target_center_size)
        cent_img_scaled = self._get_scaled_image(center_img, csz, csz, cat, self._center_scale_cache)
        if icon_alpha < 255:
            cent_img_scaled = cent_img_scaled.copy()
            cent_img_scaled.set_alpha(icon_alpha)
        cent_rect = cent_img_scaled.get_rect(center=(x, y))

        if cat == "LO":
            surface.blit(cent_img_scaled, cent_rect)
            surface.blit(rotated_ring, rect)
        else:
            surface.blit(rotated_ring, rect)
            surface.blit(cent_img_scaled, cent_rect)

        level3_key = None
        if cat in ("C3-", "C3"):
            level3_key = "C3_3"
        elif cat in ("C4", "C4-ST"):
            level3_key = "C4_3"
        elif cat == "C5":
            level3_key = "C5_3"
        if level3_key:
            l3_ring_img = self.res_mgr.get_image(f"{level3_key}_ring")
            if l3_ring_img:
                l3_orig_w, l3_orig_h = l3_ring_img.get_size()
                target_l3_size = max(int(65 * icon_factor), 20)
                l3_scale = target_l3_size / max(l3_orig_w, l3_orig_h)
                l3_w, l3_h = int(l3_orig_w * l3_scale), int(l3_orig_h * l3_scale)
                l3_base = self._get_scaled_image(l3_ring_img, l3_w, l3_h, level3_key, self._l3_scale_cache)
                if cat in ("C3-", "C3"):
                    l3_angle = ty.sa3
                elif cat in ("C4", "C4-ST"):
                    l3_angle = ty.sa4
                else:
                    l3_angle = ty.sa5
                if cat == "C5":
                    l3_color = self._get_c5_color(cp['w'])
                elif cat in ("C3-", "C4-ST"):
                    l3_color = self._get_sub_gradient_color(cp['w'], cat) or self.get_point_color(cp['w'], cp['st'])
                elif cat == "C3":
                    l3_color = C3
                elif cat == "C4":
                    l3_color = C4
                else:
                    l3_color = self.get_point_color(cp['w'], cp['st'])
                l3_rotated = ty.get_rotated_level3_ring(
                    (level3_key, l3_w, l3_h), l3_base, l3_angle, ty.mirror,
                    tuple(l3_color[:3]))
                if icon_alpha < 255:
                    l3_rotated = l3_rotated.copy()
                    l3_rotated.set_alpha(icon_alpha)
                l3_rect = l3_rotated.get_rect(center=(x, y))
                surface.blit(l3_rotated, l3_rect)

    # ── SMCY 视频图标绘制 ──
    @staticmethod
    def _smcy_ts_for(ty, cat, icon_factor: float):
        """计算 SMCY 帧绘制目标尺寸(与 _draw_smcy_frame 使用同一 icon_factor)。"""
        from ..smcy_icon import get_smcy_manager      # 函数内 import: 避免启动期拖入 cv2
        try:
            mgr = get_smcy_manager()
            orig = mgr.get_size(cat, 'S' if ty.v.mirror else 'N')
            if not orig or orig[0] <= 0 or orig[1] <= 0:
                orig = (400, 400)
            target = max(20, 4 * round(self._icon_target_px(cat, icon_factor) / 4))
            scale = target / max(orig[0], orig[1])
            return (max(1, int(orig[0] * scale)), max(1, int(orig[1] * scale)))
        except Exception:
            return None

    def _advance_smcy_frame(self, ty, cat, now, speed_factor=1.0):
        """只推进 SMCY 帧索引，不绘制。"""
        # 函数内 import: 避免模块级导入把 cv2 拖进启动路径(`_TOTAL_FRAMES` /
        # `_FRAME_INTERVAL_MS` 是纯 int 常量, 每次重复 import 只是字典查找)
        from ..smcy_icon import get_smcy_manager, _FRAME_INTERVAL_MS, _TOTAL_FRAMES
        v = ty.v
        hemi = 'S' if v.mirror else 'N'
        cat_key = f"{hemi}:{cat}"
        if v._smcy_last_cat != cat_key:
            if v._smcy_last_cat:
                v._smcy_frame = (v._smcy_frame + 1) % _TOTAL_FRAMES
            v._smcy_last_cat = cat_key
            v._smcy_last_ticks = now
        else:
            elapsed = now - v._smcy_last_ticks
            interval = max(1, int(_FRAME_INTERVAL_MS / max(speed_factor, 0.1)))
            if elapsed >= interval:
                advance, rem = divmod(elapsed, interval)
                if advance > 8:
                    # N19: 长时间未绘制时限制单次跳帧,并重置计时避免持续追赶
                    advance = 8
                    rem = 0
                v._smcy_frame = (v._smcy_frame + advance) % _TOTAL_FRAMES
                v._smcy_last_ticks = now - rem
                # 法32: 运行时预载下一段帧窗口(与解码预算共用,不抢占)
                # K21: 传与绘制一致的尺寸,避免 get_frame 因尺寸变化清空缓存
                # N: 渐进预载(每帧最多 3 帧), 避免一次同步解码 30 帧造成 10-30ms 卡顿
                try:
                    mgr = get_smcy_manager()
                    nxt = (v._smcy_frame + 30) % _TOTAL_FRAMES
                    if not mgr.has_frame_window(cat, hemi, nxt, 30):
                        ts = TySimDrawIconMixin._smcy_ts_for(ty, cat, self._size_factors()[1])
                        mgr.preload_window_step(cat, hemi, nxt, 30, budget=3, target_size=ts)
                except Exception:
                    pass

    # ── 紫滤镜结果缓存：避免每帧对 C5 图标做 numpy 全图运算 ──
    # 字节预算 + LRU(见 cache_store): 只按条数封顶时, 图标放大后 120 条能吃到数百 MB
    _purple_frame_cache = SurfaceCache("purple_frame", 96 * 1024 * 1024, 240)

    @classmethod
    def _cache_purple_frame(cls, key, frame) -> None:
        """缓存紫滤镜/TS 渐变结果(按字节预算 + LRU 驱逐)。"""
        cls._purple_frame_cache.put(key, frame)

    def _draw_smcy_frame(self, surface, ty, cat, frame_idx, x, y, icon_factor, icon_alpha,
                         wind: int = 0):
        """纯绘制指定帧，按视频原始宽高比缩放。"""
        from ..smcy_icon import get_smcy_manager      # 函数内 import: 避免启动期拖入 cv2
        hemi = HEMISPHERE_SOUTH if ty.v.mirror else HEMISPHERE_NORTH
        mgr = get_smcy_manager()
        orig = mgr.get_size(cat, hemi)
        # N10: (0,0) 是非空 tuple,须显式防护,否则 scale 除零
        if not orig or orig[0] <= 0 or orig[1] <= 0:
            orig = (400, 400)
        # 4px 网格量化：缩放档内 ts 恒定，解码缓存存活（拖14）
        # 尺寸口径统一走 _icon_target_px（EX/SMCY 的倍率在那里，别再本地写死）
        target = max(20, 4 * round(self._icon_target_px(cat, icon_factor) / 4))
        scale = target / max(orig[0], orig[1])
        ts = (max(1, int(orig[0] * scale)), max(1, int(orig[1] * scale)))
        # 155+/170+ 紫色滤镜（不影响白色部分），结果按帧缓存
        tier = _purple_tier(wind) if cat == 'C5' else None
        is_ts = cat == 'TS'
        if tier is not None:
            key = (cat, hemi, frame_idx, ts, tier[1])
            cache = TySimDrawIconMixin._purple_frame_cache
            frame = cache.get(key)
            if frame is None:
                raw = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
                if raw is None:
                    return
                frame = _apply_purple_filter(raw, tier[1])
                self._cache_purple_frame(key, frame)
        elif is_ts:
            # TS 强度渐变滤镜（34kt 绿 → 49kt 黄绿, 与 STS 衔接），结果按帧缓存
            wq = _ts_wind_bucket(wind)
            key = ('tsg', cat, hemi, frame_idx, ts, wq)
            cache = TySimDrawIconMixin._purple_frame_cache
            frame = cache.get(key)
            if frame is None:
                raw = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
                if raw is None:
                    return
                frame = _apply_ts_gradient_filter(raw, wq)
                self._cache_purple_frame(key, frame)
        else:
            frame = get_smcy_manager().get_frame(cat, hemi, frame_idx, ts)
            if frame is None:
                return
        # R4: 不可在原处 mutate 共享帧面。frame 可能来自 _purple_frame_cache 共享缓存，
        # 或来自 smcy _VideoStream._cache（多台风共用同一 (类别,半球,帧号,尺寸) 帧）。
        # set_alpha 会永久改写共享面的表面 alpha——若此处 set_alpha<255 后 blit，
        # 其它台风/后续帧再取到同一面时 alpha 已被污染。与 _draw_simple_icon 一致：
        # 仅当需要淡出(<255)时 copy，正常 255 直绘不改共享面。
        if icon_alpha < 255:
            frame = frame.copy()
            frame.set_alpha(icon_alpha)
        rect = frame.get_rect(center=(x, y))
        surface.blit(frame, rect)

    # ── fallback 图标 ──
    _fallback_ring_cache = None
    _fallback_center_cache = None

    def _create_fallback_ring(self) -> pygame.Surface:
        cls = TySimDrawIconMixin
        if cls._fallback_ring_cache is None:
            s = pygame.Surface((80, 80), pygame.SRCALPHA)
            pygame.draw.circle(s, (200, 200, 200, 200), (40, 40), 35, 5)
            cls._fallback_ring_cache = s
        return cls._fallback_ring_cache

    def _create_fallback_center(self) -> pygame.Surface:
        cls = TySimDrawIconMixin
        if cls._fallback_center_cache is None:
            s = pygame.Surface((60, 60), pygame.SRCALPHA)
            pygame.draw.circle(s, (255, 255, 255, 240), (30, 30), 20)
            pygame.draw.circle(s, (50, 50, 50, 240), (30, 30), 4)
            cls._fallback_center_cache = s
        return cls._fallback_center_cache

    @staticmethod
    def _get_c5_color(wind: int):
        if wind >= 170:
            return C5_D
        if wind >= 155:
            return TySimDrawIconMixin._get_gradient_color(wind, C5_M, C5_D, 155, 170)
        return C5_L

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _get_gradient_color(wind, low_color, high_color, low_wind, high_wind):
        if wind >= high_wind:
            return high_color
        if wind >= low_wind:
            ratio = (wind - low_wind) / (high_wind - low_wind)
            return (
                int(low_color[0] + (high_color[0] - low_color[0]) * ratio),
                int(low_color[1] + (high_color[1] - low_color[1]) * ratio),
                int(low_color[2] + (high_color[2] - low_color[2]) * ratio),
            )
        return low_color

    @staticmethod
    def _get_sub_gradient_color(wind, cat):
        _GRADIENTS = {
            "C2-": (C2_MINUS, C2_MINUS, 83, 86),
            "C3-": ((255, 207, 69), C3_MINUS, 96, 105),
            "C4-ST": ((255, 0, 39), C4_ST, 130, 137),
        }
        if cat in _GRADIENTS:
            lo, hi, lo_w, hi_w = _GRADIENTS[cat]
            return TySimDrawIconMixin._get_gradient_color(wind, lo, hi, lo_w, hi_w)
        return None

    # ── 名称 ──
    def _get_max_wind_color(self, ty):
        """获取台风名称颜色（基于最大风速），结果缓存于 typhoon 对象。"""
        cache_attr = '_cached_max_wind_color'
        cached = getattr(ty, cache_attr, None)
        if cached is not None:
            return cached
        tropical = get_tropical_points(ty.pts)
        if tropical:
            mwp = max(tropical, key=lambda p: p['w'])
            color = mwp.get('color', self.get_point_color(mwp['w'], mwp['st']))
        else:
            color = TXT
        setattr(ty, cache_attr, color)
        return color

    def _get_point_name_color(self, ty, pname: str):
        """逐点名称模式：对每个名字按其对应点的最大风速单独计算颜色。"""
        cache = getattr(ty, '_cached_name_colors', None)
        if cache is None:
            cache = {}
            ty._cached_name_colors = cache
        if pname in cache:
            return cache[pname]
        pts = [p for p in ty.pts if (p.get('name') or '').strip() == pname]
        pool = get_tropical_points(pts) or pts
        if pool:
            mwp = max(pool, key=lambda p: p['w'])
            color = mwp.get('color', self.get_point_color(mwp['w'], mwp['st']))
        else:
            color = TXT
        cache[pname] = color
        return color

    @staticmethod
    def _pt_stronger(a, b) -> bool:
        """a 是否强于 b：先比风速，风速相同比气压（气压未知不算更强）。"""
        if a['w'] != b['w']:
            return a['w'] > b['w']
        pa, pb = a['p'], b['p']
        return bool(pa and pb and pa < pb)

    @classmethod
    def _pt_tied(cls, a, b) -> bool:
        """两报强度持平（互相都不严格更强）。"""
        return not cls._pt_stronger(a, b) and not cls._pt_stronger(b, a)

    def _get_peaks(self, ty) -> list:
        """巅峰列表：风速高于前后两个合格报（可计算 ACE 的报 + 性质与风速合格的非正式报）。
        平顶（连续持平报）取第一报：向后跳过持平报后再与首个不同强度的报比较。
        合格报之间如有非合格报（风速回落），仍视为独立巅峰。"""
        cached = getattr(ty, '_cached_peaks', None)
        if cached is not None:
            return cached
        qual = [(i, p) for i, p in enumerate(ty.pts) if _ace_eligible(p)]
        peaks = []
        for k, (i, p) in enumerate(qual):
            if k > 0 and not self._pt_stronger(p, qual[k - 1][1]):
                continue
            # 跳过与当前持平的后续报（平顶），与平顶后的首个不同强度报比较
            m = k + 1
            while m < len(qual) and self._pt_tied(p, qual[m][1]):
                m += 1
            if m < len(qual) and not self._pt_stronger(p, qual[m][1]):
                # 下一个合格报更强 — 检查两者间是否存在非合格低谷（风速回落）
                valley = False
                for j in range(i + 1, qual[m][0]):
                    if ty.pts[j]['w'] < p['w']:
                        valley = True
                        break
                if not valley:
                    continue
            color = p.get('color', None) or self.get_point_color(p['w'], p['st'])
            peaks.append({'idx': i, 'w': p['w'], 'p': p['p'] or 0,
                          'color': tuple(color), 'strongest': False})
        if peaks:
            best = max(peaks, key=lambda pk: (pk['w'], -pk['p'] if pk['p'] else -100000))
            best['strongest'] = True
        ty._cached_peaks = peaks
        return peaks

    def _get_peak_label_surf(self, label: str, color, name_factor: float):
        """巅峰/登陆标注文字表面 —— **按字号直接渲染**(见 `_sized_glow_text`)。"""
        size = max(8, int(round(_PEAK_BASE_PX * name_factor)))
        key = ('peak', label, tuple(color) if color else None, size)
        surf = self._name_shadow_cache.get(key)
        if surf is None:
            surf = _sized_glow_text(_sized_font(_peak_font_cache, size), label, color,
                                    name_factor)
            self._name_shadow_cache.put(key, surf)
        return surf

    _FADE_RAMP = 0.25       # 名称淡入/切换时长（points_time 单位，0.25 = 3 模拟小时）
    _PEAK_PRE_S = 0.5       # 巅峰前显示 0.5 秒（真实秒）
    _PEAK_POST_S = 0.5      # 巅峰后显示 0.5 秒
    _PEAK_POST_BEST_S = 1.0  # 最强巅峰后显示 1 秒
    _PEAK_FADE_S = 0.25     # 巅峰淡入淡出时长（真实秒）

    @staticmethod
    def _blit_faded(surface, surf, pos, alpha):
        if alpha >= 255:
            surface.blit(surf, pos)
        elif alpha > 0:
            tmp = surf.copy()
            tmp.set_alpha(alpha)
            surface.blit(tmp, pos)

    def _get_name_surf(self, name: str, color, name_factor: float):
        """台风名字文字表面 —— **按字号直接渲染**(见 `_sized_glow_text`)。

        键从 `(name, color, name_factor)` 改成 `(name, color, size)`: 字号是真正的
        渲染参数(不同 factor 可能落到同一字号, 那次就该命中同一张表面)。
        """
        size = max(8, int(round(_NAME_BASE_PX * name_factor)))
        key = ('name', name, tuple(color) if color else None, size)
        surf = self._name_shadow_cache.get(key)
        if surf is None:
            surf = _sized_glow_text(_sized_font(_name_font_cache, size), name, color,
                                    name_factor)
            self._name_shadow_cache.put(key, surf)
        return surf

    def _name_core(self, name: str, color, name_factor: float):
        """名字**实心文字**的包围盒(相对名字表面) —— 定位标注的基准。"""
        size = max(8, int(round(_NAME_BASE_PX * name_factor)))
        key = ('cname', name, tuple(color) if color else None, size)
        return text_core_rect(self._get_name_surf(name, color, name_factor), key,
                              _glow_pad(name_factor))

    def _label_core(self, label: str, color, pk_factor: float):
        """标注**实心文字**的包围盒(相对标注表面)。"""
        size = max(8, int(round(_PEAK_BASE_PX * pk_factor)))
        key = ('cpeak', label, tuple(color) if color else None, size)
        return text_core_rect(self._get_peak_label_surf(label, color, pk_factor), key,
                              _glow_pad(pk_factor))

    def _label_layout(self, cands, pk_factor: float, base_bottom: int):
        """给所有**可能出现的**标注排**固定槽位**, 返回 {key: (blit_y, text, color)}。

        用户 2026-10-01: "巅峰文字显示的位置不要变动 / 显示一半跳到上一行" —— 旧的
        "游标式堆叠"只对**当帧真正画出来的**标注推进游标, 所以任何一条淡入/淡出都会把
        它**后面所有标注**整体挪一行。这里改成(方案 C 的**确定性**版本, 不用"上一帧
        可见集合"那种带状态的写法):

          槽位 = "**时间窗**与更早候选重叠的个数"(只由候选自己算, 与当前可见集合无关)
            · 时间上不会同时出现的标注 ⇒ 都在第 1 行 ⇒ 单独出现时永远紧贴名字, **不留空行**;
            · 只有真的会同时出现的才往下排一行;
            · 每条标注的 y 在它整个可见期间**恒定**, 别人显隐**不影响**它。
          行内对齐"实心文字(ink)顶边", 行高取该行所有候选 ink 高的最大值。

        `cands`: [(key, text, color, win0, win1)]; `win=None` 表示"恒可见"(预览示例),
        视为与所有候选重叠(逐行堆叠)。
        """
        n = len(cands)
        if not n:
            return {}
        rows_of = {}
        solved = []                                  # [(win0, win1)]
        order = sorted(range(n), key=lambda i: (-1e18 if cands[i][3] is None
                                                else cands[i][3], i))
        for i in order:
            _key, _txt, _col, w0, w1 = cands[i]
            r = 0
            for (o0, o1) in solved:
                if w0 is None or o0 is None:
                    r += 1                           # 恒可见 ⇒ 与所有重叠
                elif o0 < w1 - 1e-9 and w0 < o1 - 1e-9:
                    r += 1                           # 时间窗真重叠
            solved.append((w0, w1))
            rows_of[_key] = r
        row_h = {}
        cores = {}
        for (key, txt, col, _w0, _w1) in cands:
            core = self._label_core(txt, col, pk_factor)
            cores[key] = core
            row_h[rows_of[key]] = max(row_h.get(rows_of[key], 0), core.h)
        plan = {}
        ink_top = int(base_bottom) + _LABEL_LINE_GAP_PX   # 第 1 行的 ink 顶边
        for r in sorted(row_h):
            for (key, txt, col, _w0, _w1) in cands:
                if rows_of[key] == r:
                    plan[key] = (ink_top - cores[key].top, txt, col)
            ink_top += row_h[r] + _LABEL_LINE_GAP_PX
        return plan

    def _icon_target_px(self, cat, icon_factor: float) -> float:
        """该类别在当前图标集下的图标**目标边长**(px) —— 全文件唯一的尺寸口径。

        ⚠⚠ 2026-10-01 审查抓到的严重问题: 原来三处各写各的算式, 而名字偏移自己
        硬编了  —— 那只等于"简单图标 + 非 EX"的半宽(70*f/2)。
        实际存在三种更大的口径:
            简单·非EX = 70*f*1.0  => 半宽 35*f
            简单·**EX** = 70*f*1.5  => 半宽 52.5*f
            SMCY·非EX = 70*f*1.5  => 半宽 52.5*f
            SMCY·**EX** = 70*f*3.0  => 半宽 **105*f**
        于是切到 SMCY 图标集、或出现 EX 时, **名字压到图标上** —— 与我当时
        写的"保证任何大小都在右上角之外"正相反。
        统一到这里之后, 四处(简单图标/SMCY ts/SMCY 绘制/名字偏移)不可能再分叉。
        """
        if getattr(self, 'icon_set', ICON_SET_DEFAULT) == ICON_SET_SMCY:
            mult = 3.0 if cat == 'EX' else 1.5
        else:
            mult = 1.5 if cat == 'EX' else 1.0
        return 70.0 * icon_factor * mult

    def _icon_source_ok(self, cat) -> tuple:
        """**预览自检**: 当前图标集下 `cat` 有没有**真实图标**(而不是程序生成的兜底环)。

        背景(2026-10-01 用户报"名字位置调整时使用了兜底转轮"): 应用跑在
        `icon_set == 'smcy'` 时, `resource_mgr._load_all` 在 SMCY 目录里找不到
        `<cat>_1.png`, 于是 `resource_mgr.py:73` 用 `_create_ring_icon(cat)` **生成**
        一个圆环顶上(`get_image` 因此**不是 None**) ⇒ 谁走 `_draw_simple_icon`
        谁就会画出那个兜底环, 而且**静默无提示**。
        ⇒ 预览必须先问这里: 拿不到真实图标就**明着报错**, 不许兜底。

        返回 `(ok, 来源描述)`。
        """
        from ..smcy_icon import get_smcy_manager      # 函数内 import: 避免启动期拖入 cv2
        try:
            if getattr(self, 'icon_set', ICON_SET_DEFAULT) == ICON_SET_SMCY:
                mgr = get_smcy_manager()
                for hemi in (HEMISPHERE_NORTH, HEMISPHERE_SOUTH):
                    sz = mgr.get_size(cat, hemi)
                    if sz and sz[0] > 0 and sz[1] > 0:
                        return True, 'smcy %s/%s %sx%s' % (cat, hemi, sz[0], sz[1])
                return False, 'smcy 无 %s 帧' % cat
            # 简单图标集: 用加载器**同一个目录**判断资源文件是否存在
            d = self.res_mgr._icon_dir()
            p = os.path.join(d, '%s_1.png' % cat)
            if not os.path.exists(p):
                return False, 'simple 缺 %s_1.png' % cat
            img = self.res_mgr.get_image('%s_ring' % cat)
            return bool(img), 'simple %s' % os.path.basename(p)
        except Exception as ex:                      # 自检失败也当"不可用", 不兜底
            return False, '自检异常 %r' % (ex,)

    def draw_typhoon_name(self, surface: pygame.Surface, ty, x: int, y: int,
                           icon_factor: float = 1.0, alpha: int = 255,
                           force_full: bool = False, name_override=None,
                           color_override=None, cat_override=None,
                           label_override=None, zoom: float = 1.0) -> None:
        """画台风名字 + 巅峰/登陆标注(锚在图标右上角, 可 `name_dx/dy`、`peak_dx/dy` 微调)。

        `force_full=True` 时**跳过出场/切换淡入**(appear=switch_t=1) —— 只给
        「名字位置」预览用: 用户在对话框里调位置时, 不该因为当前台风正好在
        淡入(或该点名字为空)而**看不到名字**(那样预览就成了安慰剂), 也避免
        名字/颜色轮换时被 `_name_anim` 的交叉淡入搞成半透明闪烁。
        预览专用参数(地图路径**一个都不传** ⇒ 零行为变化):
          · `name_override`/`color_override`/`cat_override` —— 预览的名字/颜色/类别;
          · `label_override` —— [(文本, 颜色), ...]: 用这些**示例**标注替掉真实巅峰/登陆
            (预览里当前时刻可能没有标注 ⇒ 用户没得调), 堆叠位置与真实标注**同一段代码**;
          · `zoom` —— 把**整个场景**(图标口径/字号/边距/所有偏移)等比缩 `zoom` 倍,
            供预览"放不下就按真实比例缩小"。只缩长度、不用位图缩放 ⇒ 依然锐利。
        """
        zoom = max(0.05, float(zoom))
        display_name = None
        name_color = None
        _skip_name = False       # 逐点名字模式下命中空行时为 True: 只不画名字
        if getattr(self, 'point_name_mode', False):
            cp = ty.cp()
            pname = (cp.get('name') or '').strip() if cp else ''
            if pname:
                display_name = pname
                name_color = self._get_point_name_color(ty, pname)
            else:
                # ⚠⚠ 用户 2026-10-01: "台风名部分行为空、部分行有内容的台风,
                # 当播放名字的空行时, **不显示名字**"。
                # 原先空名会掉进下面的  分支,
                # **回退成台风默认名**(如 "2025 90W") —— 于是空行反而显示了一个名字。
                # ⚠ 审查指出: 我第一版是直接 , 把**巅峰/登陆标注一起吞掉**了
                #   —— 用户只要求"不显示名字"。改成只标记跳过名字本身, 标注照常画。
                _skip_name = True
        if display_name is None:
            display_name = self.get_display_name(ty)
            name_color = self._get_max_wind_color(ty)
        # 预览的名字轮换: 只覆盖**文本与颜色**(不碰 dx/dy); 顺序放在 `_name_anim`
        # 之前, 这样状态机看到的也是轮换后的名字(配合 force_full 不会有半透明帧)。
        if name_override is not None:
            display_name = str(name_override)
            _skip_name = False           # 轮换的名字恒可见
        if color_override is not None:
            name_color = tuple(color_override)
        # ⚠ 用户 2026-10-01: "台风名字默认大小(设置为100%时的大小)为现在的1.5倍"
        # ⇒ 100% 这一档整体放大 1.5 倍。把系数乘在这里(而不是改字体基准),
        #   这样滑条 0~N% 的**相对**关系不变, 只是 100% 对应到更大的字号。
        # `zoom` 再把字号(以及下面的图标口径)一起等比缩 ⇒ 预览能按真实比例缩小。
        name_factor = getattr(self, 'name_size', 100) / 100.0 * 1.5 * zoom

        # ── 名称切换检测（交叉淡入淡出）──
        # ⚠⚠ 用户 2026-10-01 报"对话框里轮换名字, 地图上真台风的名字也跟着变":
        #   这个状态机以 **ty 对象本身**为键(`self._name_anim.get(ty)`), 而「名字位置」
        #   预览当时传的是**真台风对象** + `name_override` ⇒ 预览里轮换的
        #   Rita/Haiyan/... 被写进了**真台风的渲染状态**; 地图下一帧判到
        #   `state['name'] != display_name` 就进入交叉淡入 —— 若 `ty.at` 不动
        #   (暂停/未播放), `switch_t` 恒为 0 ⇒ 地图**长期**显示预览名。
        #   审查复核: 成立(代码路径与症状都对得上)。
        #   修法(用户倾向的方案 b): `name_override` / 预览专用的 `force_full` 走
        #   **完全隔离**分支 —— **不读也不写 `_name_anim`**。渲染状态不该被"预览
        #   参数"影响; 隔离后预览轮换多少次都不会碰真台风的状态。
        _isolated = (name_override is not None) or force_full
        state = None
        if not _isolated:
            if not hasattr(self, '_name_anim'):
                self._name_anim = {}
            state = self._name_anim.get(ty)
            if state is None or state['switch_at'] > ty.at:
                state = {'name': display_name, 'color': name_color,
                         'prev': None, 'switch_at': -1e9}
                self._name_anim[ty] = state
            elif state['name'] != display_name:
                state['prev'] = (state['name'], state['color'])
                state['switch_at'] = ty.at
                state['name'] = display_name
                state['color'] = name_color

        # 淡入：出场后 3 模拟小时内渐显
        pt = ty.points_time
        appear = 1.0
        if pt:
            appear = max(0.0, min(1.0, (ty.at - pt[0]) * 4.0))
        switch_t = (min(1.0, max(0.0, (ty.at - state['switch_at']) * 4.0))
                    if state is not None else 1.0)
        if force_full:                       # 预览: 跳过淡入(见 docstring)
            appear = 1.0
            switch_t = 1.0

        # ⚠⚠ 用户 2026-10-01: "台风名字往上调一点, 保证任何大小都在台风右上角"。
        # 原来写死 (+30, -20) x icon_factor ⇒ 半宽 35 而偏移 30/20 都更小
        # ⇒ 名字锚点**落在图标包围盒内部**, 清不出去。
        # 现在锚到图标的**右上角**并留边距, 名字整体落在右上角之外。
        #
        # ⚠⚠ 审查指出(我第一版写错了): 我原来硬编  并声称
        #   "任何大小", 但那只等于"简单图标 + 非 EX"的半宽。实际还有三种更大的:
        #     简单·EX / SMCY·非EX = 半宽 52.5*f,  SMCY·EX = 半宽 **105*f**
        #   于是切到 SMCY 图标集、或出现 EX 时名字照样压住图标。
        #   现在统一走 (全文件唯一口径, 与三个绘制点同源)。
        _cp = ty.cp()
        _cat = (_cp.get('cat') or self.get_strength_category(_cp['w'], _cp['st'])
                if _cp else 'TD')
        if cat_override is not None:              # 预览: 锚点尺寸跟着**预览图标**走
            _cat = cat_override
        _eff_factor = icon_factor * zoom          # 预览缩放只缩长度, 不做位图缩放
        _icon_half = self._icon_target_px(_cat, _eff_factor) / 2.0
        # ── 名字锚点(用户 2026-10-01: "名字太右上角了") ─────────────────────
        # 锚点沿图标右上对角线、按 `NAME_ANCHOR_IN` 收缩(几何依据见常量注释);
        # 再叠 `name_dx/name_dy`(用户可视化微调, 单位 = icon_factor==1 时的 px)。
        # ⚠ 公式抽到模块级 `name_offset_px()` —— 「名字位置」预览要用**同一口径**
        #   估算画布与缩放比, 两边绝不能各写一份。
        offset_x, offset_y = name_offset_px(
            _icon_half, name_factor, getattr(self, 'name_dx', 0),
            getattr(self, 'name_dy', 0), _eff_factor,
            float(getattr(self, 'name_anchor_in', NAME_ANCHOR_IN)))
        text_x, ty_pos = x + offset_x, y + offset_y
        # 辉光版表面含 `_glow_pad(name_factor)` px 透明边距(与 render_glow_text 的
        # pad=6 同基准, 按字号等比), 平移回原文字位置。
        name_off = _glow_pad(name_factor)

        # ⚠ 空名(_skip_name)时仍要算出**同名高度**的表面: 下面的巅峰/登陆标注
        #   靠它定位, 若这里换成空串会让标注跳到图标上。所以用"本该显示的名字"
        #   来量高度, 只是**不画**它。
        _size_name = display_name or self.get_display_name(ty)
        _size_color = name_color or self._get_max_wind_color(ty)
        shadow_surf = self._get_name_surf(_size_name, _size_color, name_factor)
        if not _skip_name:
            name_alpha = int(alpha * appear * switch_t)
            self._blit_faded(surface, shadow_surf,
                             (text_x - name_off, ty_pos - name_off), name_alpha)
            # 交叉淡入只在**未隔离**(地图路径)时才有 prev: 预览的 state is None
            if state is not None and switch_t < 1.0 and state['prev'] is not None:
                old_surf = self._get_name_surf(state['prev'][0], state['prev'][1],
                                               name_factor)
                old_alpha = int(alpha * appear * (1.0 - switch_t))
                self._blit_faded(surface, old_surf,
                                 (text_x - name_off, ty_pos - name_off), old_alpha)

        # ── 巅峰 / 登陆标注（名称**下方**逐行堆叠 ⇒ 同时出现多个时自动换行）──
        # 用户 2026-10-01: "台风巅峰/登陆放在名字下一行(需要同时显示多个自动换行)就行"。
        # 巅峰原来就在这里; **登陆原先只有地图上的 png 标记、没有文字** ⇒ 本轮补上,
        # 与巅峰共用同一个 `py_pos` 游标, 所以两者同时出现时会自然往下排。
        # 用户 2026-10-01(第二轮): "**而且不能调整巅峰/登陆!**" ⇒ 加 `peak_dx/peak_dy`
        #   (与 name_dx/dy **同一套语义**: 单位 = icon_factor==1 时的 px, 渲染时乘
        #    factor; dy 正=向下)。基准点是**名字**的左上角 —— 标注本来就长在名字下方,
        #    相对名字才能"跟着名字走"(设 name_dx/dy 时标注一起动), 见对话框说明。
        peaks = self._get_peaks(ty)
        pk_factor = getattr(self, 'peak_label_size', 100) / 100.0 * zoom
        _pk_dx = getattr(self, 'peak_dx', 0) * _eff_factor
        _pk_dy = getattr(self, 'peak_dy', 0) * _eff_factor
        _lbl_x = text_x - _glow_pad(pk_factor) + int(_pk_dx)
        if pt or label_override is not None:
            sp = max(0.1, getattr(self, 'sp', 1.0))
            # ── 标注 y 定位: 以**可见文字(实心像素)**为基准, 不用"表面高度" ──
            # 旧式 `py_pos = ty_pos + shadow_surf.get_height() - 1` 用的是**表面**高,
            # 而表面 = 文字 + 2×`_glow_pad(name_factor)`, pad 又随字号等比 ⇒ 字号越大,
            # 那条**看不见的边距**把标注推得越远(实测墨迹间距 100%→40px, 300%→93px;
            # 用户截图原话: "这个 peak3 为什么这么下面, 不应该在名字下一行吗")。
            # 现在:
            #   名字可见文字底边(画布 y) = (ty_pos - name_off) + name_core.bottom
            #   每条标注的 y 由 `_label_layout()` 的**固定槽位**给出(见那里):
            #     第 1 行 ink 顶边 = 名字底边 + peak_dy + GAP; 行间 = 上一行 ink 底边 + GAP
            # GAP 是**固定 px**(`_LABEL_LINE_GAP_PX`), 不随字号缩放 —— 它表示"两条
            # 文字之间留多少白", 字号大时留白不该跟着暴涨。
            #
            # ⚠⚠ 用户 2026-10-01: "巅峰文字显示的位置不要变动 / 显示一半跳到上一行"。
            # 旧的"游标式堆叠"(_blit_stacked + cursor) 只对**当帧真正画出来**的标注推进
            # 游标, 而巅峰/登陆循环里"不在时间窗 / idx 越界 / alpha<=0"的标注直接
            # `continue` ⇒ **任何一条标注淡入淡出都会把它后面所有标注整体挪一行**。
            # 现在改成: 先按**时间窗**(与当前可见集合无关)给所有候选排死槽位, 绘制时
            # 只取自己那一行的 y ⇒ 自身可见期间 y 恒定, 别人显隐不影响它(见 `_label_layout`)。
            _name_bottom = ((ty_pos - name_off)
                            + self._name_core(_size_name, _size_color,
                                              name_factor).bottom)
            _base_bottom = _name_bottom + int(_pk_dy)
            try:
                _lfs = self._get_precomputed_landfalls(ty)
            except Exception:
                _lfs = []

            # ⓪ 预览示例标注(label_override): **不读真实峰值**(当前时刻可能压根没有标注,
            #    用户就没得调), 走同一套布局 + 同一段绘制; 也**不碰任何真台风状态**。
            if label_override is not None:
                _cands = [(('ov', k), str(t), tuple(c), None, None)
                          for k, (t, c) in enumerate(label_override)]
                _plan = self._label_layout(_cands, pk_factor, _base_bottom)
                for k, (_t, _c) in enumerate(label_override):
                    item = _plan.get(('ov', k))
                    if item is None:
                        continue
                    _y, _txt, _col = item
                    self._blit_faded(surface,
                                     self._get_peak_label_surf(_txt, _col, pk_factor),
                                     (_lbl_x, _y), int(alpha))
                return

            # 候选清单(**所有**可能出现的标注): 巅峰 + 登陆, 带各自时间窗
            total = len(peaks)
            _cands = []
            for n, pk in enumerate(peaks, 1):
                if pk['idx'] >= len(pt):
                    continue
                t_pk = pt[pk['idx']]
                pre = self._PEAK_PRE_S * sp
                post = (self._PEAK_POST_BEST_S if pk['strongest']
                        else self._PEAK_POST_S) * sp
                prefix = f"Peak{n}" if total > 1 else "Peak"
                txt = f"{prefix} {pk['w']}kt"
                if pk['p']:
                    txt += f" {pk['p']}mb"
                _cands.append((('peak', n), txt, pk['color'], t_pk - pre, t_pk + post))
            for i, lf in enumerate(_lfs):
                seg = lf['seg']
                if seg + 1 >= len(pt):
                    continue
                t_lf = pt[seg] + (pt[seg + 1] - pt[seg]) * lf['t']
                pre = self._PEAK_PRE_S * sp
                post = self._PEAK_POST_S * sp
                txt = "Landfall %dkt" % ty.pts[seg]['w']
                _cands.append((('lf', i), txt, self._get_max_wind_color(ty),
                               t_lf - pre, t_lf + post))
            _plan = self._label_layout(_cands, pk_factor, _base_bottom)

            # ① 巅峰(按原有前后窗口 + 淡入淡出; y 只从 `_plan` 取, 不再推进游标)
            for n, pk in enumerate(peaks, 1):
                key = ('peak', n)
                item = _plan.get(key)
                if item is None:                     # idx 越界(与候选清单同判据)
                    continue
                t_pk = pt[pk['idx']]
                pre = self._PEAK_PRE_S * sp
                post = (self._PEAK_POST_BEST_S if pk['strongest']
                        else self._PEAK_POST_S) * sp
                if not (t_pk - pre <= ty.at <= t_pk + post):
                    continue
                fade = max(1e-6, self._PEAK_FADE_S * sp)
                a = min(1.0, (ty.at - (t_pk - pre)) / fade,
                        ((t_pk + post) - ty.at) / fade)
                if a <= 0:
                    continue
                _y, _txt, _col = item
                self._blit_faded(surface,
                                 self._get_peak_label_surf(_txt, _col, pk_factor),
                                 (_lbl_x, _y), int(alpha * max(0.0, a)))

            # ② 登陆(与巅峰同一套固定槽位 ⇒ 自动换行, 但谁显谁隐都不动别人的位置)
            for i, lf in enumerate(_lfs):
                key = ('lf', i)
                item = _plan.get(key)
                if item is None:
                    continue
                seg = lf['seg']
                t_lf = pt[seg] + (pt[seg + 1] - pt[seg]) * lf['t']
                pre = self._PEAK_PRE_S * sp
                post = self._PEAK_POST_S * sp
                if not (t_lf - pre <= ty.at <= t_lf + post):
                    continue
                fade = max(1e-6, self._PEAK_FADE_S * sp)
                a = min(1.0, (ty.at - (t_lf - pre)) / fade,
                        ((t_lf + post) - ty.at) / fade)
                if a <= 0:
                    continue
                _y, _txt, _col = item
                self._blit_faded(surface,
                                 self._get_peak_label_surf(_txt, _col, pk_factor),
                                 (_lbl_x, _y), int(alpha * max(0.0, a)))

    # ── 信息框 ──
    def _draw_info_box(self, surface: pygame.Surface, ty, point: TrackPoint) -> None:
        dark = getattr(self, 'dark_mode', True)
        # 法17: 峰值风速缓存(失效点与 _cached_max_wind_color 相同)
        mw = getattr(ty, '_cached_max_wind', None)
        if mw is None:
            mw = max_wind_from_points(ty.pts)
            ty._cached_max_wind = mw
        # 显示点索引(编辑模式可能是选中点,非播放位置)
        idx = next((i for i, p in enumerate(ty.pts) if p is point), ty.ci)
        prev_pt = ty.pts[idx - 1] if 0 < idx < len(ty.pts) else None
        peak = peak_point(ty.pts)
        peak_t = peak['t'] if peak else ''
        # 用显示点索引(idx)而非播放位置(ty.ci): 编辑模式回看选中点时移速才对应
        mv = movement_speed_kt(ty.pts, ty.points_time, idx)
        # ── S0 行配置(顺序 + 显隐 + 平滑) + 背景/阴影开关 ──
        # ⚠ 用户 2026-10-01: 旧配置没有 `pres`(气压)行 —— 这里再兜一次迁移
        # (config 加载时已迁过; 运行时被手工赋值/老会话残留时也能自愈), 幂等且零拷贝。
        from ..config import migrate_info_box_rows
        rows_cfg = migrate_info_box_rows(self.info_box_rows or [])
        bg_on = bool(getattr(self, 'info_box_bg', False))
        shadow_on = bool(getattr(self, 'info_box_text_shadow', True))
        scale = float(getattr(self, 'info_box_scale', 1.2) or 1.0)
        cfg_fp = (tuple((r['key'], bool(r.get('on', True)), bool(r.get('smooth', True)))
                        for r in rows_cfg), bg_on, shadow_on, scale)
        key_data = (
            ty.b, ty.n, ty.cust, ty.sname, ty.start_time, point['t'],
            point['la'], point['lo'], point['w'], point['p'], point['st'],
            ty.tace, self.name_display_mode, dark,
            mw,
            ty.pts[0]['t'] if ty.pts else '',
            ty.basin,
            point.get('cat', ''),
            self.screen_width,      # 宽度随窗口缩放(避免 resize 后宽度陈旧)
            len(ty.pts), idx, round(mv or 0, 2), peak_t,
            cfg_fp,
        )
        if ty in self._info_box_cache_typhoon and self._info_box_last_data.get(ty) == key_data:
            box, ace_x, ace_y, ace_tc, ace_visible = self._info_box_cache_typhoon[ty]
        else:
            # 信息框字体: 按 info_box_scale 缩放(默认 1.2, 比旧版略大)
            scale = float(getattr(self, 'info_box_scale', 1.2) or 1.0)
            ifs = _info_box_font(max(12, int(round(21 * scale))))
            ifm = _info_box_font(max(12, int(round(28 * scale))))

            if dark:
                box_bg = (22, 28, 44, 220)
                box_border = (55, 85, 130)
                tc = (215, 225, 245)
                trend_up = (255, 150, 70)
                trend_dn = (110, 170, 250)
                shadow_col = (0, 0, 0)
            else:
                box_bg = INFO_BOX_BG
                box_border = INFO_BOX_BORDER
                tc = TXT
                trend_up = (200, 90, 0)
                trend_dn = (0, 90, 200)
                shadow_col = (255, 255, 255)

            # ⚠ 用户 2026-10-01: 这里原先算过 `ace_left`(ACE 进度条左缘)并用它封顶
            # box_w —— 于是长行会被折行。现在"不许换行"优先: 宽度只受屏幕右缘约束,
            # 必要时允许信息框压到 ACE 条上(ACE 条是独立图层, 见 draw_ace_display)。

            def build_row(key, smooth, max_w):
                """按行 key 生成 (surface, gap, is_ace); 无内容返回 None。
                所有文字带黑描边(加粗可读); 风速行与名称一致使用黑描边,
                文字色保留趋势色(↑橙/↓蓝)。"""
                def rt_o(font, text, color, outline=(0, 0, 0)):
                    return _outline_text(rt(font, text, color, max_w, smooth), outline)

                if key == 'name':
                    name = self.get_display_name(ty, self.name_display_mode)
                    return (rt_o(ifm, name, tc), 6, False)
                if key == 'time':
                    t = point['t']
                    with_year = bool(ty.pts and t[:4] != ty.pts[0]['t'][:4])
                    return (rt_o(ifs, f"时间: {fmt_short_time(t, with_year)}", tc), 3, False)
                if key == 'pos':
                    la = point['la']
                    lo = point['lo']
                    lat_dir = 'N' if la >= 0 else 'S'
                    lat_val = abs(la)
                    if lo > 180.0:
                        lon_disp, lon_dir = 360.0 - lo, 'W'
                    elif lo < -180.0:
                        lon_disp, lon_dir = lo + 360.0, 'W'
                    elif lo < 0:
                        lon_disp, lon_dir = -lo, 'W'
                    else:
                        lon_disp, lon_dir = lo, 'E'
                    return (rt_o(ifs, f"位置: {lat_val:.1f}°{lat_dir}, {lon_disp:.1f}°{lon_dir}", tc), 3, False)
                if key == 'wind':
                    wind_line = f"风速: {point['w']} kt"
                    if prev_pt is not None and point['w'] != prev_pt['w']:
                        d = point['w'] - prev_pt['w']
                        wind_line += f"  {'↑' if d > 0 else '↓'}{abs(d)}"
                    # ⚠⚠ 用户 2026-10-01: 描边改用**当前点**的强度色。
                    # 原来这里是 `self._get_max_wind_color(ty)` —— **整条路径峰值**的颜色,
                    # 对同一个台风**整场恒定**(还缓存在 `ty._cached_max_wind_color`),
                    # 于是"当前只有 15kt 时描边仍是峰值的红/紫", 与"这一行显示当前风速"
                    # 自相矛盾(实测 WP05: 当前 15kt, 描边却是峰值 130kt 的 (255,0,191))。
                    # 现在与同框的「等级」行语义一致: 弱就灰、强才红。
                    # (台风**名字**仍用峰值色 —— 名字代表"这个台风最强到什么级别", 合理。)
                    outline = point.get('color') or self.get_point_color(
                        point['w'], point['st'])
                    return (rt_o(ifs, wind_line, (255, 255, 255), outline), 3, False)
                if key == 'pres':
                    # ⚠ 用户 2026-10-01: "气压不和等级放在一起, 默认放在风速下面"
                    # ⇒ 气压从 `cat` 行拆成**独立行**(key='pres'), 默认行序里紧跟
                    # `wind` 之后(见 app/config.py 的 INFO_BOX_ROW_KEYS / 迁移函数)。
                    pres_str = f"{point['p']} hPa" if point['p'] != 0 else "未知"
                    return (rt_o(ifs, f"气压: {pres_str}", tc), 3, False)
                if key == 'cat':
                    # 只渲染等级; 气压已拆到上面的 `pres` 行(旧配置由迁移补行)
                    cat = point.get('cat', self.get_strength_category(point['w'], point['st']))
                    return (rt_o(ifs, f"等级: {display_category(cat)}", tc), 3, False)
                if key == 'speed':
                    if not mv:
                        return None
                    return (rt_o(ifs, f"移速: {mv:.0f} kt", tc), 3, False)
                if key == 'peak':
                    if not peak:
                        return None
                    return (rt_o(ifs, f"巅峰: {peak['w']} kt ({fmt_short_time(peak_t)})", tc), 3, False)
                if key == 'tace':
                    return (rt_o(ifs, f"总ACE: {ty.tace:.4f}", tc), 3, False)
                if key == 'cace':
                    return (rt_o(ifs, "实时ACE: ", tc), 3, True)
                return None

            # 第一遍: 无换行取自然宽度 → 自适应 box_w
            nat_rows = []
            for r in rows_cfg:
                if not r.get('on', True):
                    continue
                row = build_row(r['key'], bool(r.get('smooth', True)), None)
                if row is not None:
                    nat_rows.append(row)
            content_w = max((s.get_width() for s, _, _ in nat_rows), default=0)
            # ⚠⚠ 用户 2026-10-01: **任何一条信息都不许换行**。
            # 原实现把宽度硬顶在 375 px(再受 ACE 条左缘 `ace_left-30` 限制) ⇒ 超宽就走
            # 第二遍的 `rt(..., max_w)` 折行, 于是 `等级: SD   气压: 1005 hPa` 被拆成
            # 两行、`hPa` 掉到下一行(用户截图)。这与"自适应"的注释自相矛盾: 上限一旦
            # 生效就不是自适应了。
            # 现在改为**按最长一行反推所需宽度**(左右各 15px 内边距, 与下面
            # `bg.blit(surf_ln, (15, y))` 对齐), 只受**屏幕右缘**约束:
            #   · 有上限就必然存在"内容比上限宽"的输入 ⇒ 那种输入只能折行或截断,
            #     两者都被用户禁止 ⇒ 所以这里不留硬编码宽度;
            #   · 宽度不够时宁可向右长(可能压住右上角的 ACE 条)也不折行 —— 用户对
            #     "不许换行"的要求优先于"不遮 ACE"(见下面的 logger.debug 记录)。
            _pad_lr = 15 * 2
            need_w = int(max(160, content_w + _pad_lr))
            _cap_screen = max(160, int(self.screen_width) - 22 - 12)
            box_w = min(need_w, _cap_screen)
            if box_w < need_w:
                # 只有"内容比整个屏幕还宽"才会走到这里; 此时仍不折行(接受右侧溢出)
                logger.debug('信息框: 最长行需要 %dpx, 屏幕只允许 %dpx, 本帧不折行',
                             need_w, _cap_screen)

            # 第二遍: **永不折行**(max_w 传 None ⇒ rt 走单行分支);
            # 保留两遍结构是为了让"测量"与"成型"分开, 数值上两遍等价(rt 自带缓存)
            rows = []
            row_keys = []
            for r in rows_cfg:
                if not r.get('on', True):
                    continue
                row = build_row(r['key'], bool(r.get('smooth', True)), None)
                if row is not None:
                    rows.append(row)
                    row_keys.append(r['key'])

            # ── 动态高度 + 绘制 ──
            box_h = 14 + sum(s.get_height() + g for s, g, _ in rows) + 6
            bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            if bg_on:
                pygame.draw.rect(bg, box_bg, (0, 0, box_w, box_h), 0, 15)
                if 'color' in point:
                    bar_c = point['color']
                    if len(bar_c) == 3:
                        bar_c = (*bar_c, 220)
                    pygame.draw.rect(bg, bar_c, (0, 0, 8, box_h), 0, 14, 0, 0, 14)
                pygame.draw.rect(bg, box_border, (0, 0, box_w, box_h), 2, 15)

            y = 10
            ace_x = ace_y = 0
            ace_visible = False
            name_rect = None
            for i, (surf_ln, gap, is_ace) in enumerate(rows):
                if shadow_on:
                    sh = _text_shadow(surf_ln, shadow_col)
                    if sh is not None:
                        bg.blit(sh, (16, y + 1))
                bg.blit(surf_ln, (15, y))
                if row_keys[i] == 'name':
                    # 名称行屏幕矩形(供双击编辑 B4)
                    name_rect = pygame.Rect(22 + 15, 22 + y,
                                            surf_ln.get_width(), surf_ln.get_height())
                if is_ace:
                    ace_x = 15 + surf_ln.get_width()
                    ace_y = y
                    ace_visible = True
                y += surf_ln.get_height() + gap
            self._info_box_name_rect = name_rect

            self._info_box_cache_typhoon[ty] = (bg, ace_x, ace_y, tc, ace_visible)
            self._info_box_last_data[ty] = key_data
            box = bg
            ace_tc = tc

        surface.blit(box, (22, 22))
        if ace_visible:
            # 实时ACE数字每帧单独绘制（ASCII，底层字体绕过 SmartFont 缓存）。
            digit_key = (round(ty.cace, 4), ace_tc)
            digits = _box_ace_digit_cache.get(digit_key)
            if digits is None:
                digits = f_name.en_font.render(f"{ty.cace:.4f}", True, ace_tc)
                if len(_box_ace_digit_cache) >= _BOX_ACE_DIGIT_CACHE_MAX:
                    _box_ace_digit_cache.pop(next(iter(_box_ace_digit_cache)))
                _box_ace_digit_cache[digit_key] = digits
            surface.blit(digits, (22 + ace_x, 22 + ace_y))
        # 名称编辑态: 在名称行处叠加输入框
        if getattr(self, '_name_edit_ty', None) is ty:
            self._draw_name_edit(surface)

    # ── 名称行双击编辑(S0 B4) ──
    def _start_name_edit(self, ty, rect) -> None:
        from ..input_field import InputField
        self._name_edit_ty = ty
        f = InputField(rect, max_length=40, dark=getattr(self, 'dark_mode', True))
        f.set_text(ty.cust or ty.sname or '')
        f.activate()
        self._name_edit_field = f

    def _commit_name_edit(self) -> None:
        ty = getattr(self, '_name_edit_ty', None)
        f = getattr(self, '_name_edit_field', None)
        if ty is not None and f is not None:
            text = f.get_text().strip()
            if text:
                ty.cust = text
                try:
                    key = f"{ty.b}{ty.n}"
                    tn = self.cfg.tn or {}
                    tn[key] = text
                    self.cfg.tn = tn
                    self.save_config()
                except Exception:
                    pass
            self._info_box_cache_typhoon.pop(ty, None)
            self._info_box_last_data.pop(ty, None)
        self._name_edit_ty = None
        self._name_edit_field = None

    def _draw_name_edit(self, surface) -> None:
        f = getattr(self, '_name_edit_field', None)
        if f is not None:
            f.draw(surface)

    def _handle_name_edit_event(self, e) -> bool:
        f = getattr(self, '_name_edit_field', None)
        if f is None:
            return False
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_RETURN,
                                                  pygame.K_KP_ENTER):
            self._commit_name_edit()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not f.rect.collidepoint(e.pos):
                self._commit_name_edit()
                return False      # 点击输入框外 → 提交并放行给其它逻辑
        if f.handle_event(e):
            return True
        return True
