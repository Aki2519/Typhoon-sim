"""台风结束摘要：左侧显示巅峰强度素材 + 名称 + 数据（渐入渐出，流式读取）。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pygame

from .constants import rt
from .constants.colors import DB, TD, TS, STS, C1, C2, C3, C4, C5_L, C2_MINUS, C3_MINUS, C4_ST

_CAT_COLOR = {'DB': DB, 'TD': TD, 'TS': TS, 'STS': STS, 'C1': C1,
              'C2': C2, 'C2-': C2_MINUS, 'C3': C3, 'C3-': C3_MINUS,
              'C4': C4, 'C4-ST': C4_ST, 'C5': C5_L}

from .constants.fonts import _load_font, SmartFont, FONT_FILE
from .smcy_icon import get_summary_frame, has_summary_video
from .statistics.chart_helpers import _haversine_chain
from .utils import infer_strength_category, display_category, get_tropical_points
from .ty_sim_mixins._draw_icon_mixin import _apply_purple_filter, _purple_tier

if TYPE_CHECKING:
    from .typhoon import Typhoon

logger = logging.getLogger(__name__)

_slot_registry: dict = {}
_wait_queue: list = []
_font_bar_small = SmartFont(_load_font(FONT_FILE, 20, 20), _load_font(FONT_FILE, 20, 20))
_font_bar_name = SmartFont(_load_font(FONT_FILE, 26, 26), _load_font(FONT_FILE, 26, 26))
_font_bar_main = SmartFont(_load_font(FONT_FILE, 30, 30), _load_font(FONT_FILE, 30, 30))
_font_bar_big = SmartFont(_load_font(FONT_FILE, 32, 32), _load_font(FONT_FILE, 32, 32))

_OUTLINE = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_WHITE = (255, 255, 255)
_border_cache: dict = {}
_outlined_cache: dict = {}

# 法8: C5 摘要条紫色滤镜结果按 (cat,hemi,frame_idx,size) 缓存,免每帧 copy+numpy 全图
_purple_summary_cache: dict = {}
_PURPLE_SUMMARY_CACHE_MAX = 32


_trunc_cache: dict = {}


def _truncate_bar(font, text: str, max_w: int) -> pygame.Surface:
    """超宽文本二分截断为单行（省略号结尾），避免与右侧数据重叠。结果缓存。"""
    if max_w <= 0:
        # 无可用空间: 不绘制左列(避免与右侧数据叠字,R2-22)
        return rt(font, "", _WHITE)
    key = (id(font), text, max_w)
    cached = _trunc_cache.get(key)
    if cached is not None:
        return cached
    full = rt(font, text, _WHITE)
    if full.get_width() <= max_w:
        result = full
    else:
        lo, hi = 1, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if rt(font, text[:mid] + "…", _WHITE).get_width() <= max_w:
                lo = mid
            else:
                hi = mid - 1
        result = rt(font, text[:lo] + "…", _WHITE)
    if len(_trunc_cache) > 256:
        _trunc_cache.pop(next(iter(_trunc_cache)))
    _trunc_cache[key] = result
    return result


def clear_caches() -> None:
    """清空摘要静态缓存(紫滤镜/边框/描边/截断),配合资源重置调用。

    紫滤镜结果以 tier[1] 入键:图标设置切换后旧强度桶不再有对应资源,清空防窜用。"""
    _purple_summary_cache.clear()
    _border_cache.clear()
    _outlined_cache.clear()
    _trunc_cache.clear()


class TyphoonSummary:

    BAR_H = 64
    GAP = 10
    MAX_VISIBLE = 3
    TOP_Y = 10
    RIGHT_MARGIN = 520    # ACE 进度条左侧

    def __init__(self, ty: Typhoon, start_time: float) -> None:
        self.ty = ty
        self.start_time = start_time
        self._total = 10.0
        self._fade = 0.5
        self._phase1 = 5.0

        peak_cat = self._find_peak(ty)
        self._hemi = 'S' if ty.v.mirror else 'N'
        self._cat = peak_cat
        self._frame_idx = 0

        _pool = get_tropical_points(ty.pts) or ty.pts
        self._max_wind = max((p['w'] for p in _pool), default=0) if _pool else 0
        peak_pres = [p['p'] for p in _pool if p['w'] == self._max_wind and p['p']]
        self._peak_pres = min(peak_pres) if peak_pres else 0
        # 巅峰日期（首个最大风速点）
        self._peak_date = ""
        for p in _pool:
            if p['w'] == self._max_wind and len(p.get('t', '')) >= 8:
                self._peak_date = f"{p['t'][4:6]}/{p['t'][6:8]}"
                break
        self._total_ace = ty.tace
        self._active_days = 0.0
        self._path_length = 0.0
        if ty.pts:
            start = ty.pts[0].get('t', '')
            end = ty.pts[-1].get('t', '')
            if len(start) >= 10 and len(end) >= 10:
                try:
                    s = datetime(int(start[:4]), int(start[4:6]), int(start[6:8]), int(start[8:10]))
                    e = datetime(int(end[:4]), int(end[4:6]), int(end[6:8]), int(end[8:10]))
                    self._active_days = (e - s).total_seconds() / 3600.0
                except Exception:
                    pass
            st = None
            for i in range(len(ty.pts) - 1):
                p0, p1 = ty.pts[i], ty.pts[i + 1]
                seg, st = _haversine_chain(p1['la'], p1['lo'], p0['la'], p0['lo'], st)
                self._path_length += seg
        # 平均移速 (km/h)
        self._avg_speed = (self._path_length / self._active_days
                           if self._active_days > 0 else 0.0)

        self._slot: int = -1
        self._started = False
        self._try_start(start_time)

    def _expired(self, now: float) -> bool:
        return (now - self.start_time) / 1000.0 >= self._total

    def _try_start(self, now: float) -> bool:
        """取空闲槽位（后来的插空排放）；全满时进入队列依次等待。"""
        for s in range(self.MAX_VISIBLE):
            occ = _slot_registry.get(s)
            if occ is not None and occ._started and occ._expired(now):
                _slot_registry.pop(s, None)
        if _wait_queue and _wait_queue[0] is not self:
            return False
        for s in range(self.MAX_VISIBLE):
            if s not in _slot_registry:
                _slot_registry[s] = self
                self._slot = s
                self._started = True
                self.start_time = now
                if self in _wait_queue:
                    _wait_queue.remove(self)
                return True
        if self not in _wait_queue:
            _wait_queue.append(self)
        return False

    @staticmethod
    def _find_peak(ty: Typhoon) -> str:
        """巅峰等级：优先取热带性质报点的最大风速点（连同其性质推断等级）。"""
        pool = get_tropical_points(ty.pts) or ty.pts
        if not pool:
            return "TD"
        mwp = max(pool, key=lambda p: p['w'])
        return infer_strength_category(mwp['w'], mwp.get('st', ''))

    @classmethod
    def available_for(cls, ty: Typhoon) -> bool:
        """是否存在对应的摘要视频；没有的等级（如 SD）不做总结。"""
        hemi = 'S' if ty.v.mirror else 'N'
        return has_summary_video(cls._find_peak(ty), hemi)

    def update(self, current_time: float) -> bool:
        if not self._started:
            self._try_start(current_time)
            return True
        # 帧进度按真实经过时间推导(int(elapsed*60))而非渲染调用次数累加:
        # 高帧率(120FPS/不封顶)下若逐帧 +1 会跑超视频总长导致后半段空白,
        # 低帧率下则慢放——与淡入/位移(均按 elapsed)脱节。
        self._frame_idx = int((current_time - self.start_time) / 1000.0 * 60.0)
        alive = (current_time - self.start_time) / 1000.0 < self._total
        if not alive and _slot_registry.get(self._slot) is self:
            _slot_registry.pop(self._slot, None)
        return alive

    @staticmethod
    def _blit_outlined(surface, font, text, color, x, y, alpha):
        # 合成面按 (字体, 文本, 颜色, alpha桶) 缓存,避免每帧渲染黑/白两份并 8 向 blit
        bucket = alpha // 16
        key = (id(font), text, color, bucket)
        composed = _outlined_cache.get(key)
        if composed is None:
            black = rt(font, text, (0, 0, 0))
            fg = rt(font, text, color)
            if alpha < 255:
                black = black.copy()
                black.set_alpha(bucket * 16 + 15)
                fg = fg.copy()
                fg.set_alpha(bucket * 16 + 15)
            composed = pygame.Surface((fg.get_width() + 2, fg.get_height() + 2),
                                      pygame.SRCALPHA)
            for dx, dy in _OUTLINE:
                composed.blit(black, (dx + 1, dy + 1))
            composed.blit(fg, (1, 1))
            if len(_outlined_cache) >= 128:
                _outlined_cache.pop(next(iter(_outlined_cache)))
            _outlined_cache[key] = composed
        surface.blit(composed, (x - 1, y - 1))

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        if not self._started:
            return
        elapsed = (current_time - self.start_time) / 1000.0
        if elapsed >= self._total:
            return

        # 缓动动画：弹入 / 弹出
        if elapsed < self._fade:
            t = elapsed / self._fade
            eased = t ** 3
        elif elapsed > self._total - self._fade:
            t = (self._total - elapsed) / self._fade
            eased = t ** 3
        else:
            eased = 1.0

        alpha = int(255 * eased)
        cat_color = _CAT_COLOR.get(self._cat, (220, 220, 240))

        # 保持视频原始比例（1920:96），高度不变，向右靠齐 ACE 进度条左侧
        max_w = surface.get_width() - self.RIGHT_MARGIN - 20
        if max_w <= 0:   # N17: 窗口过窄时跳过绘制
            return
        bar_w = max(64, min(self.BAR_H * 20, max_w))
        # 右缘与 ACE 进度条左缘对齐; 与左上季节时钟冲突时右移并收窄,
        # 绝不越过 ACE 条左缘(R2-3: 原实现钳制 210 后右缘越过 ACE 条造成遮挡)
        clock_avoid_x = 210
        bar_x = max(clock_avoid_x, surface.get_width() - self.RIGHT_MARGIN - bar_w)
        bar_w = min(bar_w, surface.get_width() - self.RIGHT_MARGIN - bar_x)
        bar_w = max(4, bar_w)   # 极窄窗口下保持最小宽度,不强制回扩 64(T3-9)
        target_size = (bar_w, self.BAR_H)
        frame = get_summary_frame(self._cat, self._hemi, self._frame_idx, target_size)
        # 155+/170+ 紫色滤镜（仅热带 C5 巅峰）; 结果按帧缓存,避免每帧 copy+numpy(法8)
        tier = _purple_tier(self._max_wind) if self._cat == 'C5' else None
        if frame is not None and tier is not None:
            cache = _purple_summary_cache
            # 缓存键含 tier[1](滤镜强度):不同巅峰风速的 C5 摘要若共用同帧同尺寸,
            # 缺强度会造成跨摘要滤镜像率串扰(与 _draw_smcy_frame 的键一致)。
            pk = (self._cat, self._hemi, self._frame_idx, target_size, tier[1])
            filtered = cache.get(pk)
            if filtered is None:
                filtered = _apply_purple_filter(frame, tier[1])
                if len(cache) >= _PURPLE_SUMMARY_CACHE_MAX:
                    cache.pop(next(iter(cache)))
                cache[pk] = filtered
            frame = filtered

        target_y = self.TOP_Y + self._slot * (self.BAR_H + self.GAP)
        # 弹入/弹出：从上方滑入 + 淡入
        bar_y = int(target_y - 20 * (1.0 - eased))

        bar_rect = pygame.Rect(bar_x, bar_y, bar_w, self.BAR_H)

        # 背景底(视频黑色区为透明, 浮在地图上; 帧右移后左侧露底需垫色)
        bg_key = (bar_rect.w, bar_rect.h, alpha // 16)
        bg_surf = _border_cache.get(('bg',) + bg_key)
        if bg_surf is None:
            bg_surf = pygame.Surface((bar_rect.w, bar_rect.h), pygame.SRCALPHA)
            bg_surf.fill((14, 17, 26, min(255, (alpha // 16) * 16 + 15)))
            if len(_border_cache) >= 48:
                _border_cache.pop(next(iter(_border_cache)))
            _border_cache[('bg',) + bg_key] = bg_surf
        surface.blit(bg_surf, (bar_rect.x, bar_rect.y))

        # 帧水平右移: 视频素材中台风图标位于帧最左 ~12%, 右移后不再贴左缘
        # (参考 SimCore 总结条: 图标区 + 右侧信息横排)
        frame_shift = max(0, int(bar_rect.w * 0.12))
        if frame is not None:
            frame.set_alpha(alpha)
            surface.blit(frame, (bar_rect.x + frame_shift, bar_rect.y))
            frame.set_alpha(255)

        # 台风等级颜色描边（按 (尺寸,颜色,alpha量化) 缓存，避免每帧新建 Surface）
        border_key = (bar_rect.w, bar_rect.h, cat_color[:3], alpha // 16)
        border = _border_cache.get(border_key)
        if border is None:
            border = pygame.Surface((bar_rect.w, bar_rect.h), pygame.SRCALPHA)
            pygame.draw.rect(border, (*cat_color[:3], min(255, (alpha // 16) * 16 + 15)),
                             (0, 0, bar_rect.w, bar_rect.h), 2)
            if len(_border_cache) >= 32:
                _border_cache.pop(next(iter(_border_cache)))
            _border_cache[border_key] = border
        surface.blit(border, (bar_rect.x, bar_rect.y))

        # ── 左侧两行：编号 + 等级（小字号）/ 台风名（中小字号） ──
        display_name = self.ty.cust or self.ty.sname or self.ty.name
        code = f"{self.ty.basin}{self.ty.n}" if self.ty.basin else f"{self.ty.b}{self.ty.n}"
        line1 = f"{code} {display_category(self._cat)}"
        s1 = rt(_font_bar_small, line1, _WHITE)
        s2 = rt(_font_bar_name, display_name, _WHITE)

        # ── 右侧单行(大字号, 横排): 前 2s 强度/ACE, 后 2s 活跃/路径/均速 ──
        if elapsed < self._fade + self._phase1:
            intensity = f"{self._max_wind}kt"
            if self._peak_pres:
                intensity += f" {self._peak_pres}mb"
            if self._peak_date:
                intensity += f" ({self._peak_date})"
            r_text = f"强度: {intensity}    ACE: {self._total_ace:.4f}"
        else:
            total_h = self._active_days
            d = int(total_h // 24)
            h = int(total_h % 24)
            r_text = (f"活跃: {d}d {h}h    路径: {self._path_length:.0f} km"
                      f"    均速: {self._avg_speed:.0f} km/h")
        # 窄条自适应降档字号, 避免单行横排溢出条宽
        r_font = _font_bar_big
        rs = rt(r_font, r_text, _WHITE)
        if rs.get_width() > bar_rect.w * 0.78:
            r_font = _font_bar_main
            rs = rt(r_font, r_text, _WHITE)
        if rs.get_width() > bar_rect.w * 0.78:
            r_font = _font_bar_name
            rs = rt(r_font, r_text, _WHITE)
        rx = bar_rect.right - 15 - rs.get_width()
        r_top = bar_rect.y + (bar_rect.h - rs.get_height()) // 2
        self._blit_outlined(surface, r_font, r_text, _WHITE, rx, r_top, alpha)

        # ── 左侧：从条左缘起，可用宽度止于右侧文本，超宽截断为单行 ──
        left_x = bar_rect.x + frame_shift + 12
        max_left_w = max(0, rx - left_x - 12)
        top = bar_rect.y + (bar_rect.h - s1.get_height() - s2.get_height()) // 2
        # 截断返回 Surface,直接 blit(不再走 _blit_outlined 的字符串渲染,防崩溃)
        t1 = _truncate_bar(_font_bar_small, line1, max_left_w)
        t2 = _truncate_bar(_font_bar_name, display_name, max_left_w)
        if alpha < 255:
            t1 = t1.copy()
            t1.set_alpha(alpha)
            t2 = t2.copy()
            t2.set_alpha(alpha)
        surface.blit(t1, (left_x, top))
        surface.blit(t2, (left_x, top + s1.get_height()))
