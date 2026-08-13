# py/landfall_effect.py
"""登陆效果类（简单图标 PNG + SMCY 视频）。"""
from __future__ import annotations

import pygame
from typing import Callable, List, Tuple

from .constants.fonts import _load_font, SmartFont, FONT_FILE

_label_font = SmartFont(_load_font(FONT_FILE, 20, 20), _load_font(FONT_FILE, 20, 20))
_OUTLINE = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

_LABEL_DURATION = 2.0
_LABEL_FADE = 0.3
_INV_LABEL_FADE = 1.0 / _LABEL_FADE

_label_cache: dict = {}


def _get_label_surf(label: str, color, scale: float = 1.0) -> pygame.Surface:
    key = (label, color, round(scale, 2))
    surf = _label_cache.get(key)
    if surf is None:
        # 白字 + 强度色描边
        fg = _label_font.render(label, True, (255, 255, 255))
        bk = _label_font.render(label, True, color)
        surf = pygame.Surface((fg.get_width() + 2, fg.get_height() + 2), pygame.SRCALPHA)
        for dx, dy in _OUTLINE:
            surf.blit(bk, (dx + 1, dy + 1))
        surf.blit(fg, (1, 1))
        if scale != 1.0:
            nw = max(1, int(surf.get_width() * scale))
            nh = max(1, int(surf.get_height() * scale))
            surf = pygame.transform.smoothscale(surf, (nw, nh))
        if len(_label_cache) > 64:
            _label_cache.pop(next(iter(_label_cache)))
        _label_cache[key] = surf
    return surf


def _draw_strength_label(surface, label: str, color, x: int, y_top: int,
                         elapsed: float, scale: float = 1.0) -> None:
    """登陆图标下方的强度标注：显示 2 秒，淡入淡出。"""
    if not label or elapsed < 0 or elapsed >= _LABEL_DURATION:
        return
    a = min(1.0, elapsed * _INV_LABEL_FADE, (_LABEL_DURATION - elapsed) * _INV_LABEL_FADE)
    alpha = int(255 * max(0.0, a))
    if alpha <= 0:
        return
    surf = _get_label_surf(label, color, scale)
    if alpha < 255:
        surf = surf.copy()
        surf.set_alpha(alpha)
    r = surf.get_rect(midtop=(x, y_top))
    surface.blit(surf, r)


# ── 登陆点标记 ──

_MARKER_MAP = {
    'TD': 'landfall_TD', 'SD': 'landfall_TD',
    'TS': 'landfall_TS',
    'STS': 'landfall_STS', 'SS': 'landfall_STS',
    'C1': 'landfall_C1',
    'C2': 'landfall_C2', 'C2-': 'landfall_C2',
    'C3': 'landfall_C3', 'C3-': 'landfall_C3',
    'C4': 'landfall_C4', 'C4-ST': 'landfall_C4',
    'C5': 'landfall_C5',
}


def landfall_marker_name(wind: int, cat: str):
    """登陆点标记 png 名（155+/170+ 专属标记仅限热带 C5），无对应资源返回 None。"""
    if cat == 'C5':
        if wind >= 170:
            return 'landfall_170'
        if wind >= 155:
            return 'landfall_155'
    return _MARKER_MAP.get(cat)


class LandedEffect:
    """Landed-X 落地标记动画：在登陆点一次性播放（96×96, 60fps）。"""

    def __init__(self, cat: str, lon: float, lat: float, start_time: float,
                 latlon_to_screen_func: Callable[[float, float], tuple],
                 size: int) -> None:
        from .smcy_icon import landed_frame_count
        self.cat = cat
        self.lon = lon
        self.lat = lat
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self.size = max(8, size)
        self._count = min(landed_frame_count(cat), 60)

    def update(self, current_time: float) -> bool:
        if self._count <= 0:
            return False
        elapsed = (current_time - self.start_time) / 1000.0
        return int(elapsed * 60) < self._count

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        from .smcy_icon import get_landed_frame
        elapsed = (current_time - self.start_time) / 1000.0
        idx = int(elapsed * 60)
        if idx >= self._count:
            return
        frame = get_landed_frame(self.cat, idx, (self.size, self.size))
        if frame is None:
            return
        x, y = self.latlon_to_screen(self.lat, self.lon)
        surface.blit(frame, frame.get_rect(center=(x, y)))


class LandfallEffect:
    _faded_cache: dict = {}   # 法29: 16 级 alpha 淡化拷贝缓存
    _ring_cache: dict = {}    # 法29: 4° 桶旋转缓存

    def __init__(self, strength: str, lon: float, lat: float,
                 img1: pygame.Surface, img2: pygame.Surface,
                 start_time: float,
                 latlon_to_screen_func: Callable[[float, float], tuple],
                 label: str = "",
                 label_color: Tuple[int, int, int] = (255, 255, 255),
                 label_scale: float = 1.0) -> None:
        self.strength = strength
        self.lon = lon
        self.lat = lat
        self.img1 = img1
        self.img2 = img2
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self.label = label
        self.label_color = label_color
        self.label_scale = label_scale
        self._flash_alpha: int = 255
        self._ring_alpha: int = 255

    def update(self, current_time: float) -> bool:
        elapsed = (current_time - self.start_time) / 1000.0
        if elapsed > _LABEL_DURATION:
            return False
        self._flash_alpha = max(0, int(255 * (1.0 - elapsed)))
        self._ring_alpha = max(0, int(255 * (1.0 - elapsed * 0.5)))
        return True

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        x, y = self.latlon_to_screen(self.lat, self.lon)
        elapsed = (current_time - self.start_time) / 1000.0
        if self._flash_alpha > 0 and self.img2:
            # 法29: 按 16 级 alpha 桶缓存淡化拷贝
            dk = (id(self.img2), self._flash_alpha // 16)
            flash = LandfallEffect._faded_cache.get(dk)
            if flash is None:
                flash = self.img2.copy()
                flash.set_alpha((dk[1]) * 16 + 15)
                if len(LandfallEffect._faded_cache) >= 64:
                    LandfallEffect._faded_cache.pop(next(iter(LandfallEffect._faded_cache)))
                LandfallEffect._faded_cache[dk] = flash
            r = flash.get_rect(center=(x, y))
            surface.blit(flash, r)
        if self._ring_alpha > 0 and self.img1:
            # 法29: 旋转按 4° 桶缓存
            aq = int(elapsed * 360 % 360) // 4 * 4
            rk = (id(self.img1), aq)
            rotated = LandfallEffect._ring_cache.get(rk)
            if rotated is None:
                rotated = pygame.transform.rotate(self.img1, aq)
                if len(LandfallEffect._ring_cache) >= 128:
                    LandfallEffect._ring_cache.pop(next(iter(LandfallEffect._ring_cache)))
                LandfallEffect._ring_cache[rk] = rotated
            rotated.set_alpha(self._ring_alpha)
            r = rotated.get_rect(center=(x, y))
            surface.blit(rotated, r)
        _draw_strength_label(surface, self.label, self.label_color, x, y + 14,
                             elapsed, self.label_scale)


class LandfallEffectSMCY:
    """SMCY 视频登陆特效：播放 60 帧 1 秒动画。"""

    def __init__(self, strength: str, lon: float, lat: float,
                 frames: List[pygame.Surface],
                 start_time: float,
                 latlon_to_screen_func: Callable[[float, float], tuple],
                 label: str = "",
                 label_color: Tuple[int, int, int] = (255, 255, 255),
                 label_scale: float = 1.0) -> None:
        self.strength = strength
        self.lon = lon
        self.lat = lat
        self.frames = frames
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self.label = label
        self.label_color = label_color
        self.label_scale = label_scale
        self._frame_count = len(frames)

    def update(self, current_time: float) -> bool:
        elapsed = (current_time - self.start_time) / 1000.0
        return elapsed < max(self._frame_count / 60.0, _LABEL_DURATION)

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        elapsed = (current_time - self.start_time) / 1000.0
        x, y = self.latlon_to_screen(self.lat, self.lon)
        idx = int(elapsed * 60)
        if 0 <= idx < self._frame_count:
            frame = self.frames[idx]
            r = frame.get_rect(center=(x, y))
            surface.blit(frame, r)
        _draw_strength_label(surface, self.label, self.label_color,
                             x, y + 14, elapsed, self.label_scale)
