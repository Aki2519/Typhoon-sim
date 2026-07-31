"""粒子特效：RI（快速增强）、TS 升格提示。"""
from __future__ import annotations

import os
import logging
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import pygame
from PIL import Image as PILImage

from .constants import SUCAI_DIR, ICON_SET_SMCY, SOUND_DIR
from .utils import play_sound

logger = logging.getLogger(__name__)

_PARTICLE_DIR = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'particle')


# ── 缓存 ──
_eri_frames: Optional[List[pygame.Surface]] = None
_eri_sound: Optional[pygame.mixer.Sound] = None
_note_ts_frames: Optional[List[pygame.Surface]] = None
_note_ts_sound: Optional[pygame.mixer.Sound] = None


def preload_particles() -> None:
    _load_eri_gif()
    _load_note_ts_frames()
    _load_eri_sound()
    _load_note_ts_sound()


def _load_eri_sound():
    global _eri_sound
    if _eri_sound is None:
        path = os.path.join(SOUND_DIR, 'ERI-1.ogg')
        if os.path.exists(path):
            try:
                _eri_sound = pygame.mixer.Sound(path)
            except Exception as e:
                logger.debug(f"加载 ERI-1.ogg 失败: {e}")
    return _eri_sound


def _load_note_ts_sound():
    global _note_ts_sound
    if _note_ts_sound is None:
        path = os.path.join(SOUND_DIR, 'note_ts.ogg')
        if os.path.exists(path):
            try:
                _note_ts_sound = pygame.mixer.Sound(path)
            except Exception as e:
                logger.debug(f"加载 note_ts.ogg 失败: {e}")
    return _note_ts_sound


def _load_eri_gif() -> List[pygame.Surface]:
    global _eri_frames
    if _eri_frames is not None:
        return _eri_frames
    _eri_frames = []
    path = os.path.join(_PARTICLE_DIR, 'ERI.gif')
    if not os.path.exists(path):
        logger.warning(f"粒子特效: {path} 不存在")
        return _eri_frames
    gif = PILImage.open(path)
    for i in range(gif.n_frames):
        gif.seek(i)
        frame = gif.convert('RGBA')
        data = frame.tobytes()
        surf = pygame.image.fromstring(data, frame.size, 'RGBA')
        _eri_frames.append(surf)
    gif.close()
    logger.info(f"粒子特效: 加载 ERI.gif ({len(_eri_frames)} 帧)")
    return _eri_frames


def play_eri_sound(volume: float = 0.6) -> None:
    snd = _load_eri_sound()
    if snd:
        play_sound(snd, volume)


def play_note_ts_sound(volume: float = 0.6) -> None:
    snd = _load_note_ts_sound()
    if snd:
        play_sound(snd, volume)


class RIEffect:
    """快速增强（RI）粒子特效，跟随台风移动。
    按真实时间播放（约 0.9s），避免高倍速下瞬间结束而看不到。"""

    _DURATION_MS = 900
    _scaled_cache: Dict[Tuple[int, int, int], pygame.Surface] = {}
    _SCALED_CACHE_MAX = 128

    def __init__(self, typhoon, start_time: float,
                 latlon_to_screen_func, icon_factor: float = 1.0,
                 start_at: float = 0.0) -> None:
        self.typhoon = typhoon
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self.icon_factor = icon_factor
        self.frames = _load_eri_gif()
        self._frame_count = len(self.frames)
        self._cur_idx = 0

    def update(self, current_time: float) -> bool:
        if self._frame_count <= 0:
            return False
        elapsed = current_time - self.start_time
        if elapsed < 0:
            return False
        self._cur_idx = int(elapsed / self._DURATION_MS * self._frame_count)
        return self._cur_idx < self._frame_count

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        if not (0 <= self._cur_idx < self._frame_count):
            return
        pos = self.typhoon.cpos()
        if not pos:
            return
        x, y = self.latlon_to_screen(pos['la'], pos['lo'])
        frame = self.frames[self._cur_idx]
        size = max(40, int(100 * self.icon_factor))
        sw = max(1, int(frame.get_width() * size / 640))
        sh = max(1, int(frame.get_height() * size / 640))
        key = (self._cur_idx, sw, sh)
        cache = RIEffect._scaled_cache
        scaled = cache.get(key)
        if scaled is None:
            scaled = pygame.transform.smoothscale(frame, (sw, sh))
            if len(cache) >= self._SCALED_CACHE_MAX:
                cache.pop(next(iter(cache)))
            cache[key] = scaled
        # 75% 不透明度
        scaled = scaled.copy()
        scaled.set_alpha(191)   # 255 * 0.75
        r = scaled.get_rect(center=(x, y))
        surface.blit(scaled, r)


def _load_note_ts_frames() -> List[pygame.Surface]:
    global _note_ts_frames
    if _note_ts_frames is not None:
        return _note_ts_frames
    _note_ts_frames = []
    path = os.path.join(_PARTICLE_DIR, 'note_ts.mp4')
    if not os.path.exists(path):
        logger.warning(f"粒子特效: {path} 不存在")
        return _note_ts_frames
    from .smcy_icon import _frame_to_surface
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        logger.warning(f"粒子特效: 无法打开 {path}")
        return _note_ts_frames
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        _note_ts_frames.append(_frame_to_surface(frame_bgr))
    cap.release()
    logger.info(f"粒子特效: 加载 note_ts.mp4 ({len(_note_ts_frames)} 帧)")
    return _note_ts_frames


class TSNoteEffect:
    """TS 升格提示特效：台风从不可计算 ACE 的类型加强为 TS 时播放，跟随台风。

    初始整体旋转到与台风图标当前角度一致；创作偏差修正：逆时针 18°、放大 4/3。
    SMCY 图标集下帧进度与主图标视频帧同步，保持旋转锁定。"""

    _ROT_FIX_DEG = 18.0          # 创作偏差修正：逆时针 18°
    _SCALE_FIX = 4.0 / 3.0       # 创作偏差修正：放大 4/3（基准 = 缩放到图标同框）
    _SMCY_DEG_PER_FRAME = 1.2    # mainicon 视频每帧旋转角度 (1800 帧 = 6 圈)

    def __init__(self, typhoon, start_time: float,
                 latlon_to_screen_func,
                 icon_factor_func: Callable[[], float],
                 smcy: bool = True) -> None:
        self.typhoon = typhoon
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self._icon_factor_func = icon_factor_func
        self.frames = _load_note_ts_frames()
        self._frame_count = len(self.frames)
        self._smcy = smcy
        v = typhoon.v
        if smcy:
            self._start_icon_frame = v._smcy_frame
            base_angle = (v._smcy_frame * self._SMCY_DEG_PER_FRAME) % 360.0
        else:
            self._start_icon_frame = 0
            base_angle = (v.ra + v.sa) % 360.0
        self._angle = (base_angle + self._ROT_FIX_DEG) % 360.0
        self._mirror = v.mirror
        self._cur_idx = 0
        self._img_cache: Dict[Tuple[int, int], pygame.Surface] = {}

    def _frame_idx(self, current_time: float) -> int:
        if self._smcy:
            rel = self.typhoon.v._smcy_frame - self._start_icon_frame
            if rel < 0:
                return self._frame_count    # 越界值，触发 update→False
            return rel
        return int((current_time - self.start_time) / 1000.0 * 60.0)

    def update(self, current_time: float) -> bool:
        if self._frame_count <= 0:
            return False
        self._cur_idx = self._frame_idx(current_time)
        return 0 <= self._cur_idx < self._frame_count

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        idx = self._cur_idx
        if idx < 0 or idx >= self._frame_count:
            return
        pos = self.typhoon.cpos()
        if not pos:
            return
        x, y = self.latlon_to_screen(pos['la'], pos['lo'])
        icon_factor = self._icon_factor_func()
        icon_target = max(20, int(70 * icon_factor * 1.5))
        size = max(1, int(round(icon_target * self._SCALE_FIX)))
        key = (idx, size)
        img = self._img_cache.get(key)
        if img is None:
            img = pygame.transform.smoothscale(self.frames[idx], (size, size))
            if self._angle:
                img = pygame.transform.rotate(img, self._angle)
            if self._mirror:
                img = pygame.transform.flip(img, True, False)
            if len(self._img_cache) >= 128:
                self._img_cache.pop(next(iter(self._img_cache)))
            self._img_cache[key] = img
        r = img.get_rect(center=(x, y))
        surface.blit(img, r)
