from __future__ import annotations

import os
import math
import logging
from typing import Optional

import pygame

from .constants import DEFAULT_MAP, LAND_MASK, LAND_MASK_FALLBACK

logger = logging.getLogger(__name__)

# ════════════════════ 内存优化开关(可运行时回退, 无需改代码)════════════════════
# 背景(实测 2026-10-02, 见 output/exploration/20_fix_map_decode.md):
#   map.png 与 land.png 各 10800×5400 RGBA = **233,280,000 B = 222.47 MB**,
#   启动期两次 `pygame.image.load` 合计解码 **444.95 MB**(确定值), 而窗口只要
#   2560×1540 = 15.8 MB。`is_land_at_geo` 首调还会先 `tobytes(整幅 RGBA)` 造一个
#   **222.47 MB 的临时字节串**再 `[3::4]` 切片取 alpha —— 这是启动峰值的来源。
#   本开关族只消除"取 alpha 时的整幅临时量", **不改任何像素结果**(见下面的长度守卫
#   与逐行回退: 两条回退路径与旧表达式逐字节等价)。
#
# 回退: 设置环境变量 `TSIM_MAP_ALPHA_NO_TRANSIENT=0` 即恢复旧行为(整幅 tobytes),
#      或 `cp output/exploration/_backup/map_mgr.py app/map_mgr.py` 整文件回退。


def _flag(name: str, default: bool) -> bool:
    """环境变量开关: 未设置用默认; '0/false/no/off' 视为关闭。"""
    v = os.environ.get('TSIM_' + name)
    if v is None:
        return default
    return v.strip().lower() not in ('0', 'false', 'no', 'off')


#: True = 取 alpha 时不制造整幅 RGBA 临时量(快路 get_view('3'), 回退逐行切片)
ALPHA_NO_TRANSIENT = _flag('MAP_ALPHA_NO_TRANSIENT', True)


def _surface_alpha_bytes(surface: 'pygame.Surface') -> bytes:
    """取 32 位 Surface 的 **alpha 通道**(1 字节/像素), 与 `tobytes(s,'RGBA')[3::4]` 等价。

    三级实现(结果逐字节相同, 只影响峰值内存):
      1. `Surface.get_view('3')` —— 直接给出第 3 通道(alpha)平面, 无整幅临时量。
         必须做长度守卫: 只有长度 == w*h 才采用, 否则视为不可用。
      2. 逐行 `subsurface → tobytes('RGBA') → [3::4]` —— 峰值仅 1 行(w*4 B)。
      3. 旧实现整幅 `tobytes('RGBA') → [3::4]` —— 峰值 w*h*4 B(10800×5400 时 222.47 MB)。
    任一级失败/异常都向下回退, 最终一定返回与旧实现相同的结果。
    """
    w, h = surface.get_size()
    n = w * h
    if ALPHA_NO_TRANSIENT:
        try:
            b = bytes(surface.get_view('3'))
            if len(b) == n:
                return b
        except Exception:
            pass
        try:
            out = bytearray(n)
            for y in range(h):
                row = pygame.image.tobytes(
                    surface.subsurface(pygame.Rect(0, y, w, 1)), 'RGBA')
                if len(row) != w * 4:
                    raise ValueError('row bytes %d != %d' % (len(row), w * 4))
                out[y * w:(y + 1) * w] = row[3::4]
            return bytes(out)
        except Exception:
            pass
    return pygame.image.tobytes(surface, 'RGBA')[3::4]


class MapView:
    def __init__(self, img_path, lon_min, lon_max, lat_min, lat_max, screen_width, screen_height):
        self.original_img = pygame.image.load(img_path).convert()
        self.img_w, self.img_h = self.original_img.get_size()
        self.lon_min, self.lon_max = lon_min, lon_max
        self.lat_min, self.lat_max = lat_min, lat_max
        self.width_deg = lon_max - lon_min
        self.height_deg = lat_max - lat_min
        self._scale_x = self.img_w / self.width_deg
        self._scale_y = self.img_h / self.height_deg
        self._deg_per_px_x = self.width_deg / self.img_w
        self._deg_per_px_y = self.height_deg / self.img_h
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.view_x = self.view_y = 0.0
        self.scale = 1.0
        # cover 模式：最小缩放取纵横比较大者，地图始终铺满屏幕（上下/左右不露灰边）
        self.min_scale = max(screen_width / self.img_w, screen_height / self.img_h)
        self._cached_scale = -1.0
        self._cached_sw = -1
        self._cached_sh = -1
        self._cached_ba = False
        self._cached_offset = (0, 0)
        self._cached_extras = (0.0, 0.0)
        self._bottom_align = False

    @property
    def _max_view_y(self):
        return max(0.0, self.img_h - self.screen_height / self.scale)

    def _clamp_view_y(self):
        self.view_y = max(0.0, min(self.view_y, self._max_view_y))

    def _src_y(self, view_h):
        return max(0.0, min(self.view_y, self.img_h - view_h))

    def resize(self, screen_width: int, screen_height: int) -> None:
        """窗口尺寸变化: 更新尺寸/缩放极限, 并保证地图仍铺满屏幕(cover)。

        B4 只同步了尺寸而没重夹 scale: 放大窗口或进全屏后 min_scale 变大,
        旧 scale 小于新 min_scale → 缩放后的地图比屏幕窄, 左右两边露出灰底,
        看上去就是"地图渲染不全, 两边要补"。这里把 scale 顶到 min_scale。
        """
        # **保持视野中心**(用户 2026-09-19: "切换到模拟不要自作主张地改变其它模式的
        # 视角, 继承视角")。各模式的底栏高度不同(模拟模式多一行图层开关), 但那只是
        # 地图可视高度变化, 不该让镜头跳到别处: 这里先记住原中心(图像像素坐标),
        # 尺寸/夹紧处理完再把它放回去。
        _old_cy = None
        try:
            _old_cy = self.view_y + self.screen_height / max(1e-6, self.scale) / 2.0
        except Exception:
            _old_cy = None
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.min_scale = max(self.screen_width / self.img_w,
                             self.screen_height / self.img_h)
        if self.scale < self.min_scale:
            self.scale = self.min_scale
        self._clamp_view_y()
        if _old_cy is not None:
            self.view_y = _old_cy - self.screen_height / max(1e-6, self.scale) / 2.0
            self._clamp_view_y()
        # 复位渲染缓存(整图缓存/窗口缓存/偏移缓存)
        self._cached_scale = -1.0
        self._cached_sw = -1
        self._cached_sh = -1
        self._pan_cache_key = None
        self._win_cache_key = None

    def geo_to_map(self, lon, lat):
        """视图无关的"地图空间"坐标(缩放后像素): screen = map - view_offset()。

        与 geo_to_screen 同一套两段式取整, 因此视图平移只改变 view_offset,
        地图空间内容不变 —— 路径缓存据此跨平移复用, 不必重建。"""
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        return int(px * self.scale), int(py * self.scale)

    def view_offset(self):
        """当前视图在地图空间中的原点(screen = map - view_offset)。"""
        ox, oy = self._draw_offset()
        return int(self.view_x * self.scale) - ox, int(self.view_y * self.scale) - oy

    def wrap_px(self) -> int:
        """地图横向环绕周期(屏幕像素)。

        环绕阈值、屏幕点归一化、洋区/路径跨缝处理都必须用这一个来源,
        否则各处取整相位不一致会表现为路径与底图错位或伪长线。"""
        return max(1, int(self.img_w * self.scale))

    def geo_to_screen(self, lon, lat):
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        ox, oy = self._draw_offset()
        # 两段式取整(与地图 blit 同源): int(坐标*scale) - int(视图*scale)。
        # 若直接取整整段差值 int((坐标-视图)*scale), 平移时与地图的舍入相位不同,
        # 路径/台风相对底图会有 ±1px 抖动式偏移。
        vx = int(self.view_x * self.scale)
        vy = int(self.view_y * self.scale)
        x = int(px * self.scale) - vx + ox
        y = int(py * self.scale) - vy + oy
        # 环绕: 距屏幕中心超过半个图宽则按图宽像素翻面(取离屏幕中心最近的副本)。
        # 阈值必须是半宽: 用整宽时跨 0°/360° 缝的点会停在翻面错误的一侧, 表现为路径点消失。
        wrap = self.wrap_px()
        half = wrap / 2.0
        if x > self.screen_width / 2.0 + half:
            x -= wrap
        elif x < self.screen_width / 2.0 - half:
            x += wrap
        return x, y

    def geo_to_screen_round(self, lon, lat):
        """**连续运动物体**的屏幕坐标: 对连续差值**一次取整**(±0.5 舍入)。

        为什么必须与 `geo_to_screen` 并存(2026-10-01 实测):
        `geo_to_screen` 是"两段式取整" `int(px*scale) - int(view*scale)` —— 它的
        好处是**与底图 blit 同相**, 并让"地图空间 + 整数视图偏移"不变式成立
        (路径缓存的整页平移复用全靠它, **不要动**)。但对**连续运动**的物体,
        两个取整会各自独立地跨整数边界: 只要物体不是相机目标, 两者跨界的时刻就
        错开, 差值(屏幕坐标)在 m / m±1 之间来回跳 = 肉眼可见的 ±1px 抖动。
        实测(dummy SDL 探针 `G:/tmp/probe_general_jitter.py`: 相机追 bbox 中心、
        两个台风都不在中心): 399 帧里 x 方向翻转 **146** 次(0.33 px/帧)。
        本方法把连续差值**一次**取整 ⇒ 单调台阶, 同一场景翻转 **0** 次。

        代价: 相对底图可能有 <1px 的静态偏移(底图本身只能整像素 blit)。这条偏移
        比"每帧 ±1px 来回跳"轻微得多 ⇒ **移动的图标**用它; **静态/缓存路径点**
        继续用 `geo_to_screen`(整页平移复用需要整数均匀位移)。两者不可互换。
        """
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        ox, oy = self._draw_offset()
        s = self.scale
        x = int(math.floor(px * s - self.view_x * s + 0.5)) + ox
        y = int(math.floor(py * s - self.view_y * s + 0.5)) + oy
        # 环绕: 与 geo_to_screen 同一约定(取离屏幕中心最近的副本)
        wrap = self.wrap_px()
        half = wrap / 2.0
        if x > self.screen_width / 2.0 + half:
            x -= wrap
        elif x < self.screen_width / 2.0 - half:
            x += wrap
        return x, y

    def target_view_for_region(self, lon_min, lon_max, lat_min, lat_max):
        """算出"恰好装下该经纬范围"的目标视野 `(scale, view_x, view_y)`, **不改当前视图**。

        与 `set_view_region` **同一套公式**(取两轴较紧的那个 fit, 夹在
        `min_scale .. 8.0`; 零/负跨度时保持当前 scale), 但只**算**不写 ——
        供收尾做**平滑推镜**(逐帧把位置与 scale 插值过去)使用。
        `set_view_region` 本身一个字没改(路径缓存/地图空间的口径不受影响)。
        """
        if lon_max < lon_min:
            lon_max += 360
        clon = (lon_min + lon_max) / 2.0
        clat = (lat_min + lat_max) / 2.0
        lon_span = lon_max - lon_min
        lat_span = lat_max - lat_min
        if lon_span > 0 and lat_span > 0:
            scale = max(self.min_scale, min(
                self.screen_width / (lon_span * self._scale_x),
                self.screen_height / (lat_span * self._scale_y), 8.0))
        else:
            scale = self.scale
        view_x = ((clon - self.lon_min) * self._scale_x
                  - self.screen_width / (2.0 * scale)) % self.img_w
        view_y = (self.lat_max - clat) * self._scale_y \
            - self.screen_height / (2.0 * scale)
        return scale, view_x, view_y

    def screen_to_geo(self, sx, sy):
        ox, oy = self._draw_offset()
        sx, sy = sx - ox, sy - oy
        px = self.view_x + sx / self.scale
        py = self.view_y + sy / self.scale
        lon = self.lon_min + px * self._deg_per_px_x
        lon = ((lon - self.lon_min) % self.width_deg) + self.lon_min
        lat = max(self.lat_min, min(self.lat_max,
                                    self.lat_max - py * self._deg_per_px_y))
        return lon, lat

    def move_view(self, dx, dy):
        old_vx, old_vy = self.view_x, self.view_y
        new_vx = self.view_x - dx / self.scale
        new_vy = self.view_y - dy / self.scale
        self.view_x = new_vx % self.img_w
        self.view_y = new_vy
        self._clamp_view_y()
        # 返回未取模的真实位移: 屏幕内容确实位移 (new-old)*scale 像素;
        # 若对取模后的差再取"最短方向", 单次位移 ≥ 半个世界时符号会翻转(拖拽反向跳)
        return (new_vx - old_vx) * self.scale, (self.view_y - old_vy) * self.scale

    def zoom_at(self, factor, mx, my):
        ox, oy = self._draw_offset()
        mx, my = mx - ox, my - oy
        old = self.scale
        self.scale = max(self.min_scale, min(self.scale * factor, 8.0))
        self.view_x = (self.view_x + mx / old) - mx / self.scale
        self.view_y = (self.view_y + my / old) - my / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()

    def center_on_latlon(self, lat: float, lon: float) -> None:
        """把地图平移到指定经纬度居中(保持当前缩放,视图可横向环绕)。"""
        px = (lon - self.lon_min) * self._scale_x
        py = (self.lat_max - lat) * self._scale_y
        self.view_x = (px - self.screen_width / (2.0 * self.scale)) % self.img_w
        self.view_y = py - self.screen_height / (2.0 * self.scale)
        self._clamp_view_y()
        self._bottom_align = False

    def set_view_region(self, lon_min, lon_max, lat_min, lat_max):
        if lon_max < lon_min: lon_max += 360
        clon = (lon_min + lon_max) / 2.0
        clat = (lat_min + lat_max) / 2.0
        lon_span = lon_max - lon_min
        lat_span = lat_max - lat_min
        # 防御: 零跨度(mlo==Mlo 或 mla==Mla,可来自外部脚本)会除零,保持当前缩放并居中
        if lon_span <= 0 or lat_span <= 0:
            self.view_x = ((clon - self.lon_min) * self._scale_x
                           - self.screen_width / (2.0 * self.scale)) % self.img_w
            self.view_y = (self.lat_max - clat) * self._scale_y \
                          - self.screen_height / (2.0 * self.scale)
            self._clamp_view_y()
            self._bottom_align = False
            return
        self.scale = max(self.min_scale, min(
            self.screen_width / (lon_span * self._scale_x),
            self.screen_height / (lat_span * self._scale_y), 8.0))
        self.view_x = ((clon - self.lon_min) * self._scale_x
                       - self.screen_width / (2.0 * self.scale)) % self.img_w
        self.view_y = (self.lat_max - clat) * self._scale_y \
                      - self.screen_height / (2.0 * self.scale)
        self._clamp_view_y()
        self._bottom_align = False

    def set_view_corner(self, lon_bl, lat_bl, span_lon):
        if span_lon <= 0:
            return
        scale_x = self.screen_width / (span_lon * self._scale_x)
        self.scale = max(self.min_scale, min(scale_x, 8.0))
        px_bl = (lon_bl - self.lon_min) * self._scale_x
        self.view_x = px_bl % self.img_w
        py_bl = (self.lat_max - lat_bl) * self._scale_y
        self.view_y = py_bl - self.screen_height / self.scale
        self._clamp_view_y()
        self._bottom_align = True

    def _draw_offset(self):
        if (self._cached_scale != self.scale or self._cached_sw != self.screen_width
                or self._cached_sh != self.screen_height or self._cached_ba != self._bottom_align):
            vw = min(self.screen_width / self.scale, self.img_w)
            vh = min(self.screen_height / self.scale, self.img_h)
            self._cached_offset = (
                int((self.screen_width - vw * self.scale) / 2),
                int(self.screen_height - vh * self.scale) if self._bottom_align
                else int((self.screen_height - vh * self.scale) / 2),
            )
            self._cached_extras = (self.screen_width / (2.0 * self.scale), self.img_w / 2.0)
            self._cached_scale = self.scale
            self._cached_sw = self.screen_width
            self._cached_sh = self.screen_height
            self._cached_ba = self._bottom_align
        return self._cached_offset

    def _pan_cache(self):
        """全图尺寸可控时缓存整图渲染；纯平移只按偏移 blit，scale 变化才重建。
        key 含精确 scale：脚本逐帧微缩放时避免旧缩放内容与新偏移错位（B14）。"""
        w = int(math.ceil(self.img_w * self.scale))
        h = int(math.ceil(self.img_h * self.scale))
        if w > self.screen_width * 2 or h > self.screen_height * 2:
            return None
        key = (self.scale, self.screen_width, self.screen_height)
        if getattr(self, '_pan_cache_key', None) != key:
            surf = pygame.Surface((w, h))
            old_vx, old_vy = self.view_x, self.view_y
            self.view_x = 0.0
            self.view_y = 0.0
            self._render_mip_window(surf, surf.get_rect(), 0, 0)
            self.view_x, self.view_y = old_vx, old_vy
            self._pan_cache_surf = surf
            self._pan_cache_key = key
        return self._pan_cache_surf

    _PAN_WINDOW_MARGIN = 512   # 窗口缓存四周留出的平移余量(屏幕 px)

    def _window_cache(self):
        """放大后整图缓存放不下时, 缓存"当前视野 + 四周余量"的窗口。

        跟踪/拖动时视图每帧都在变, 若每帧都从原图 transform.scale(实测 ~4ms/帧),
        60fps 下会吃掉四分之一帧预算; 窗口缓存把重建摊到每 ~512px 平移一次。
        跨 0°/360° 缝或余量不足的窗口直接放弃缓存(交回逐帧缩放), 避免拼接取整误差。"""
        sw, sh = self.screen_width, self.screen_height
        # 缓存按 ox=oy=0 渲染: 若地图未能铺满屏幕(ox/oy 非零)则放弃缓存,
        # 避免缓存与屏幕偏移叠加错位
        if self._draw_offset() != (0, 0):
            return None
        # 快速拖动(单帧位移 > 120px)时放弃缓存: 窗口重建跟不上, 每帧重建反而
        # 比逐帧缩放慢(实测 600px/帧: 30.6ms vs 11.3ms); 停下后自动恢复缓存
        prev = getattr(self, '_win_prev_view', None)
        self._win_prev_view = (self.view_x, self.view_y)
        if prev is not None:
            ddx = (self.view_x - prev[0] + self.img_w / 2.0) % self.img_w - self.img_w / 2.0
            ddy = self.view_y - prev[1]
            if abs(ddx) * self.scale > 120.0 or abs(ddy) * self.scale > 120.0:
                return None
        map_w = int(math.ceil(self.img_w * self.scale))
        map_h = int(math.ceil(self.img_h * self.scale))
        cw = min(sw + 2 * self._PAN_WINDOW_MARGIN, map_w)
        ch = min(sh + 2 * self._PAN_WINDOW_MARGIN, map_h)
        if cw < sw or ch < sh:
            return None
        vw = cw / self.scale
        vh = ch / self.scale
        if vw > self.img_w + 1e-6 or vh > self.img_h + 1e-6:
            return None
        sx = self.view_x % self.img_w
        sy = self._src_y(min(sh / self.scale, self.img_h))
        step = self._PAN_WINDOW_MARGIN / self.scale
        # 量化到整数源像素: 消除缩放相位漂移(非整数原点会让缓存与逐帧渲染
        # 相差不到 1px 的分数像素, 重建时出现微跳), 同时让 key 稳定、缓存可命中
        vx0 = float(math.floor(min(
            math.floor(max(0.0, sx - step) / step) * step,
            max(0.0, self.img_w - vw))))
        vy0 = float(math.floor(min(
            math.floor(max(0.0, sy - step) / step) * step,
            max(0.0, self.img_h - vh))))
        # 当前屏幕必须完整落在窗口内(窗口不跨缝), 否则本帧退回逐帧缩放
        if (sx < vx0 - 1e-9 or sx + sw / self.scale > vx0 + vw + 1e-6
                or sy < vy0 - 1e-9 or sy + sh / self.scale > vy0 + vh + 1e-6):
            return None
        key = (round(self.scale, 6), sw, sh, round(vx0, 3), round(vy0, 3),
               cw, ch, getattr(self, '_bottom_align', False))
        if getattr(self, '_win_cache_key', None) != key:
            surf = pygame.Surface((cw, ch))
            old_vx, old_vy = self.view_x, self.view_y
            self.view_x, self.view_y = vx0, vy0
            try:
                self._render_mip_window(surf, surf.get_rect(), 0, 0)
            finally:
                self.view_x, self.view_y = old_vx, old_vy
            self._win_cache_surf = surf
            self._win_cache_key = key
        return self._win_cache_surf, vx0, vy0

    def _render_mip_window(self, screen, dest_rect, ox=None, oy=None):
        vw = min(dest_rect.width / self.scale, self.img_w)
        vh = min(dest_rect.height / self.scale, self.img_h)
        sx, sy = self.view_x % self.img_w, self._src_y(vh)
        if ox is None or oy is None:
            ox, oy = self._draw_offset()
        img = self.original_img
        rw = self.img_w - sx

        x_off = 0
        for seg_x, seg_w in ([(sx, min(rw, vw))] if rw >= vw
                             else [(sx, rw), (0, vw - rw)]):
            if seg_w <= 0:
                continue
            rect = pygame.Rect(int(seg_x), int(sy),
                                int(math.ceil(seg_w)), int(math.ceil(vh)))
            rect = rect.clip(img.get_rect())
            if rect.width <= 0 or rect.height <= 0:
                continue
            try:
                scaled = pygame.transform.scale(img.subsurface(rect),
                    (max(1, int(math.ceil(seg_w * self.scale))),
                     max(1, int(math.ceil(vh * self.scale)))))
                screen.blit(scaled, (dest_rect.left + ox + x_off, dest_rect.top + oy))
                x_off += scaled.get_width()
            except ValueError:
                pass

    def draw(self, screen, dest_rect=None):
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)

        cache = self._pan_cache()
        if cache is not None:
            # 纯平移：缓存整图按滚动偏移 blit（含 wrap），不做任何缩放
            screen.fill((120, 120, 120), dest_rect)
            sx = self.view_x % self.img_w
            vh = min(dest_rect.height / self.scale, self.img_h)
            sy = self._src_y(vh)
            ox, oy = self._draw_offset()
            cw, ch = cache.get_size()
            dx = ox - int(sx * self.scale)
            dy = oy - int(sy * self.scale)
            screen.blit(cache, (dest_rect.left + dx, dest_rect.top + dy))
            if dx + cw < dest_rect.width:
                screen.blit(cache, (dest_rect.left + dx + cw, dest_rect.top + dy))
            return
        win = self._window_cache()
        if win is not None:
            # 放大态: 窗口缓存按偏移 blit, 重建只在平移出余量时发生。
            # 只贴可见子矩形(整窗 9M px 全贴比贴屏幕 3.8M px 慢一倍多),
            # 且缓存必然铺满屏幕, 可省掉每帧全屏 fill
            surf, vx0, vy0 = win
            sx = self.view_x % self.img_w
            sy = self._src_y(min(dest_rect.height / self.scale, self.img_h))
            dx = int(vx0 * self.scale) - int(sx * self.scale)
            dy = int(vy0 * self.scale) - int(sy * self.scale)
            area = pygame.Rect(-dx, -dy, dest_rect.width, dest_rect.height)
            area = area.clip(surf.get_rect())
            if area.width >= dest_rect.width and area.height >= dest_rect.height:
                screen.blit(surf, (dest_rect.left + dx + area.x,
                                   dest_rect.top + dy + area.y), area)
                return
        screen.fill((120, 120, 120), dest_rect)
        self._render_mip_window(screen, dest_rect)


class MapManager:
    def __init__(self, sim):
        self.sim = sim
        self.map_view: Optional[MapView] = None
        self.land_img: Optional[pygame.Surface] = None
        self._land_alpha_bytes: Optional[bytes] = None
        self._land_w: int = 0
        self.ocean_overlay: Optional[pygame.Surface] = None
        self.cmp: Optional[str] = None
        self._land_orig: Optional[pygame.Surface] = None
        self._cached_map_render: Optional[pygame.Surface] = None
        self._cached_render_hash = None
        self._land_pending: bool = False
        self._land_due: int = 0
        self._land_lazy: bool = False
        self._land_geo_alpha: Optional[bytes] = None
        self._land_geo_w: int = 0
        self._land_geo_h: int = 0

    def _view_hash(self):
        v = self.map_view
        if v is None:
            return None
        return (int(v.view_x * 100), int(v.view_y * 100),
                v.scale, self.sim.screen_width, self.sim.map_height,
                getattr(v, '_bottom_align', False))   # K30: 角落模式底部对齐变化时缓存失效

    def _init_map_view(self):
        path = self.cmp if self.cmp and os.path.exists(self.cmp) else DEFAULT_MAP
        self.map_view = MapView(path, 0.0, 360.0, -90.0, 90.0,
                                self.sim.screen_width, self.sim.map_height)
        self._fit_view()

    def _fit_view(self):
        if getattr(self.sim, 'map_corner_mode', False):
            span_lon = self.sim.Mlo - self.sim.mlo
            self.map_view.set_view_corner(self.sim.mlo, self.sim.mla, span_lon)
        else:
            self.map_view.set_view_region(self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla)

    def _load_land_orig(self):
        if self._land_orig is None:
            # land.png 不存在时退化为 land2.png(两个文件任一存在即可用)
            for path in (LAND_MASK, LAND_MASK_FALLBACK):
                if not os.path.exists(path):
                    continue
                try:
                    self._land_orig = pygame.image.load(path).convert_alpha()
                    if path != LAND_MASK:
                        logger.info(f"LAND_MASK {LAND_MASK} 缺失, 已退化为 {path}")
                except Exception as e:
                    logger.error(f"加载陆地掩码失败({path}): {e}")
                    self._land_orig = None
                if self._land_orig is not None:
                    break
        return self._land_orig

    def _rebuild_land_and_overlay(self):
        land = self._load_land_orig()
        if land is None or self.map_view is None:
            self.land_img = None
        else:
            view = self.map_view
            vw = min(self.sim.screen_width / view.scale, view.img_w)
            vh = min(self.sim.map_height / view.scale, view.img_h)
            sx, sy = view.view_x % view.img_w, max(0.0, min(view.view_y, view.img_h - vh))
            ox, oy = view._draw_offset()
            lw, lh = land.get_size()
            scx, scy = lw / view.img_w, lh / view.img_h

            self.land_img = pygame.Surface(
                (self.sim.screen_width, self.sim.map_height), pygame.SRCALPHA)

            for lsx, lw_seg in ([(sx, min(view.img_w - sx, vw))]
                                if view.img_w - sx >= vw
                                else [(sx, view.img_w - sx), (0, vw - (view.img_w - sx))]):
                if lw_seg <= 0: continue
                rect = pygame.Rect(int(lsx * scx), int(sy * scy),
                                   int(math.ceil(lw_seg * scx)), int(math.ceil(vh * scy)))
                rect = rect.clip(land.get_rect())
                if rect.width <= 0 or rect.height <= 0: continue
                scaled = pygame.transform.scale(
                    land.subsurface(rect),
                    (max(1, int(lw_seg * view.scale)), max(1, int(vh * view.scale))))
                dst_x = 0 if lsx == sx else int((view.img_w - sx) * view.scale)
                self.land_img.blit(scaled, (ox + dst_x, oy))

        self.ocean_overlay = None

        if self.land_img is not None:
            # 只取 alpha 通道: 走 _surface_alpha_bytes 以避免整幅 RGBA 临时量
            # (2560×1540 时旧写法多一次 15.78 MB 的 tobytes; 结果逐字节相同)
            self._land_alpha_bytes = _surface_alpha_bytes(self.land_img)
            self._land_w = self.land_img.get_width()
        else:
            self._land_alpha_bytes = None
            self._land_w = 0

    _LAND_REBUILD_DELAY_MS = 180   # 视图连续变化(缩放/拖拽)期间推迟陆地掩码重建

    def update_land_mask(self):
        if self.map_view is None:
            return
        now = pygame.time.get_ticks()
        vh = self._view_hash()
        if vh == self._cached_render_hash:
            # 视图稳定后补做被推迟的陆地掩码重建（仅当掩码已构建过且被标记 pending）
            if self._land_pending and not self._land_lazy and now >= self._land_due:
                self._rebuild_land_and_overlay()
                self._land_pending = False
            return
        # 地图渲染必须立即更新（视觉）；
        # 陆地掩码非视觉（仅登陆检测用），懒加载——只有实际用到 is_land_at_*
        # 时才重建（首次调用触发），启动时不需要掩码，省下 land.png 加载。
        if self._land_orig is None and not (os.path.exists(LAND_MASK)
                                            or os.path.exists(LAND_MASK_FALLBACK)):
            # 无掩码文件：整条路径跳过，避免每个移动事件都重建
            self._land_alpha_bytes = None
            self._land_pending = False
            self._land_lazy = False
        else:
            # 掩码懒加载：标记 lazy, 由 _ensure_land_mask 首次 is_land 时重建
            self._land_lazy = True
            self._land_pending = False
        w, h = self.sim.screen_width, self.sim.map_height
        if (self._cached_map_render is None
                or self._cached_map_render.get_size() != (w, h)):
            self._cached_map_render = pygame.Surface((w, h))
        self.map_view.draw(self._cached_map_render, pygame.Rect(0, 0, w, h))
        self._cached_render_hash = vh

    def _ensure_land_mask(self) -> None:
        """确保陆地掩码已构建（懒加载）。由 is_land_at_screen/geo 首次调用触发，
        仅一次；掩码加载失败或文件不存在时置空并跳过。"""
        if self._land_alpha_bytes is not None:
            return
        if self._land_orig is None and not (os.path.exists(LAND_MASK)
                                            or os.path.exists(LAND_MASK_FALLBACK)):
            return
        if self._land_orig is None:
            self._land_orig = self._load_land_orig()
        if self._land_orig is None:
            self._land_alpha_bytes = None
            self._land_w = 0
            self._land_lazy = False
            self._land_pending = False
            return
        self._rebuild_land_and_overlay()
        self._land_lazy = False
        self._land_pending = False

    def is_land_at_screen(self, sx, sy):
        self._ensure_land_mask()
        ab = self._land_alpha_bytes
        if ab is None:
            return False
        w = self._land_w
        if 0 <= sx < w and 0 <= sy < len(ab) // w:
            return ab[sy * w + sx] > 0
        return False

    def is_land_at_geo(self, la, lo):
        """按经纬度直接采样原始陆地掩码（与视图无关）。
        首次调用时把原图 alpha 通道转为 bytes 查表，避免每次 get_at 锁表面。"""
        land = self._load_land_orig()
        if land is None:
            return False
        ab = self._land_geo_alpha
        if ab is None:
            # 旧写法 `pygame.image.tobytes(land, 'RGBA')[3::4]` 会先造一个
            # **222.47 MB** 的整幅临时字节串(land.png 10800×5400×4 = 233,280,000 B),
            # 这是启动峰值的来源之一。_surface_alpha_bytes 结果逐字节相同,
            # 但峰值只剩 55.6 MB(或逐行回退时的 1 行)。
            ab = _surface_alpha_bytes(land)
            self._land_geo_alpha = ab
            self._land_geo_w, self._land_geo_h = land.get_size()
            self._land_geo_sx = self._land_geo_w / 360.0
            self._land_geo_sy = self._land_geo_h / 180.0
        lw, lh = self._land_geo_w, self._land_geo_h
        x = int((lo % 360.0) * self._land_geo_sx) % lw
        y = min(lh - 1, max(0, int((90.0 - la) * self._land_geo_sy)))
        if 0 <= y < lh:
            return ab[y * lw + x] > 0
        return False

    def update_view(self):
        if self.map_view is None:
            self._init_map_view()
        else:
            self._fit_view()
        self.update_land_mask()

    update_map_image = update_view

    def draw_map(self, surface, dest_rect=None):
        if self.map_view is None:
            self._init_map_view()
        if self._cached_map_render is None:
            self.update_land_mask()
        if dest_rect is None:
            dest_rect = pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)
        surface.blit(self._cached_map_render, dest_rect)

    def get_draw_rect(self):
        return pygame.Rect(0, 0, self.sim.screen_width, self.sim.map_height)

    def load_custom_map(self, path):
        if os.path.exists(path):
            self.cmp = path
            self.sim.cmp = path
            self._init_map_view()
            self._cached_render_hash = None
            self._land_pending = False
            self._cached_map_render = None

    def reset_map(self):
        self.cmp = None
        self.sim.cmp = None
        self._init_map_view()
        self._cached_render_hash = None
        self._land_pending = False
        self._cached_map_render = None
