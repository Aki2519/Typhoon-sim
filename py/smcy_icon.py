"""SMCY 视频台风图标系统（流式按需加载 + 小窗口帧缓存）。"""
from __future__ import annotations

import os
import logging
from collections import OrderedDict
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import pygame

from .constants import (
    SUCAI_DIR, ICON_SET_SMCY,
    HEMISPHERE_NORTH, HEMISPHERE_SOUTH,
)

logger = logging.getLogger(__name__)

_TC_CATEGORIES = {
    'C1': 'TC-C1', 'C2': 'TC-C2', 'C2-': 'TC-C2-', 'C3': 'TC-C3',
    'C3-': 'TC-C3-', 'C4': 'TC-C4', 'C4-ST': 'TC-C4+', 'C5': 'TC-C5',
    'TS': 'TC-TS', 'STS': 'TC-TS+', 'TD': 'TD-D',
}

_EX_CATEGORIES = {
    'EX': 'EX-EX', 'SD': 'EX-SD', 'SS': 'EX-SS',
}

_DB_CATEGORIES = {
    'DB': 'DB-DB', 'LO': 'DB-LO', 'MD': 'DB-MD', 'WV': 'DB-WV',
}

_FPS = 60.0
_TOTAL_FRAMES = 1800
_FRAME_INTERVAL_MS = int(1000.0 / _FPS)

_VIDEO_EXT = '.mp4'
_CACHE_MAX = 240
_CACHE_BYTES_PER_STREAM = 48 * 1024 * 1024   # 单流帧缓存内存上限（大图标时自动降帧数）
_DECODE_BUDGET_PER_WIN = 3      # 每流每 ~16ms 窗口最多解码帧数（防多台风随机 seek 卡顿）
_MAX_CURSORS = 4                # 每流最多解码游标数（多台风共享同类别视频时避免来回 seek）
_SEQ_WINDOW = 45                # 游标允许 grab 快进的最大帧距


def _compose_bgra(frame_bgr: np.ndarray) -> np.ndarray:
    """BGR → BGRA，alpha = max(B,G,R)。全部走 cv2 C 实现（比 numpy axis 归约快一个量级）。"""
    b, g, r = cv2.split(frame_bgr)
    a = cv2.max(cv2.max(b, g), r)
    return cv2.merge((b, g, r, a))


def _frame_to_surface(frame_bgr: np.ndarray) -> pygame.Surface:
    """BGR 帧转 BGRA Surface，alpha = max(R,G,B)。"""
    frame_bgra = _compose_bgra(frame_bgr)
    return pygame.image.frombuffer(frame_bgra.tobytes(), frame_bgra.shape[1::-1], 'BGRA')


class _VideoStream:
    """单个视频的流式读取 + OrderedDict 帧缓存（O(1) 驱逐）。

    多游标：同一类别视频被多个台风以不同帧号播放时，为每个前进序列
    维护独立 VideoCapture 游标，顺序 read（~0.4ms）替代随机 seek（~5ms）。"""

    def __init__(self, path: str, loop: bool = False) -> None:
        self._path = path
        self._loop = loop
        self._cap = cv2.VideoCapture(path)
        self._cursors: list = [{'cap': self._cap, 'pos': None, 'use': 0}]
        self._cache: OrderedDict[int, pygame.Surface] = OrderedDict()
        self._orig_w: int = 0
        self._orig_h: int = 0
        self._frame_count: int = _TOTAL_FRAMES
        self._scale_size: Optional[Tuple[int, int]] = None
        self._decode_win: int = -1
        self._decode_n: int = 0
        if self._cap.isOpened():
            self._orig_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._orig_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fc = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fc > 0:
                self._frame_count = fc

    @property
    def is_open(self) -> bool:
        return self._cap.isOpened()

    @property
    def orig_size(self) -> Tuple[int, int]:
        return (self._orig_w, self._orig_h)

    def set_scale_size(self, size: Tuple[int, int]) -> None:
        if self._scale_size != size:
            self._scale_size = size
            self._cache.clear()

    def _cache_cap(self) -> int:
        """按目标尺寸限制缓存帧数，控制单流内存占用。"""
        if not self._scale_size:
            return _CACHE_MAX
        px = self._scale_size[0] * self._scale_size[1] * 4
        return max(16, min(_CACHE_MAX, _CACHE_BYTES_PER_STREAM // max(1, px)))

    def _read_at(self, idx: int):
        """用最合适的游标读取指定帧：可快进则 grab+read，否则 seek（新开/复用游标）。"""
        now = pygame.time.get_ticks()
        best = None
        for c in self._cursors:
            p = c['pos']
            if p is not None and p < idx and idx - p <= _SEQ_WINDOW:
                if best is None or p > best['pos']:
                    best = c
        if best is not None:
            for _ in range(idx - best['pos'] - 1):
                best['cap'].grab()
            ret, frame_bgr = best['cap'].read()
        else:
            if len(self._cursors) < _MAX_CURSORS:
                cap = cv2.VideoCapture(self._path)
                if cap.isOpened():
                    best = {'cap': cap, 'pos': None, 'use': 0}
                    self._cursors.append(best)
            if best is None:
                best = min(self._cursors, key=lambda c: c['use'])
            best['cap'].set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame_bgr = best['cap'].read()
        best['pos'] = idx
        best['use'] = now
        return ret, frame_bgr

    def get_frame(self, idx: int, target_size: Optional[Tuple[int, int]] = None) -> Optional[pygame.Surface]:
        if self._loop:
            idx = idx % self._frame_count
        elif idx < 0 or idx >= self._frame_count:
            return None
        if target_size and target_size != self._scale_size:
            self._scale_size = target_size
            self._cache.clear()
        if idx in self._cache:
            self._cache.move_to_end(idx)
            return self._cache[idx]

        # 解码预算：超出预算时复用最近邻缓存帧，避免瞬时卡顿
        win = pygame.time.get_ticks() >> 4
        if win != self._decode_win:
            self._decode_win = win
            self._decode_n = 0
        if self._decode_n >= _DECODE_BUDGET_PER_WIN and self._cache:
            nearest = min(self._cache.keys(), key=lambda k: abs(k - idx))
            return self._cache[nearest]
        self._decode_n += 1

        ret, frame_bgr = self._read_at(idx)
        if not ret:
            return None
        # 原生分辨率合成 alpha（像素少），再一次性缩放 BGRA
        frame_bgra = _compose_bgra(frame_bgr)
        if self._scale_size:
            frame_bgra = cv2.resize(frame_bgra, self._scale_size, interpolation=cv2.INTER_LINEAR)
        surf = pygame.image.frombuffer(frame_bgra.tobytes(), frame_bgra.shape[1::-1], 'BGRA')
        self._cache[idx] = surf
        cap = self._cache_cap()
        while len(self._cache) > cap:
            self._cache.popitem(last=False)
        return surf

    def preload_window(self, start_idx: int, count: int = 30) -> None:
        """预加载 [start_idx, start_idx+count) 帧到缓存，避免后续 seek 卡顿。"""
        for i in range(start_idx, min(start_idx + count, self._frame_count)):
            self.get_frame(i)

    def release(self) -> None:
        for c in self._cursors:
            c['cap'].release()
        self._cursors = [{'cap': self._cap, 'pos': None, 'use': 0}]
        self._cache.clear()


_SMCY_SPEED = 1.0
_MAX_STREAMS = 20   # (类别×南北半球) 组合数上限；过小会导致 LRU 反复驱逐重开视频


def _is_ex_category(cat: str) -> bool:
    return cat == 'EX'


class SMCYIconManager:
    """管理 SMCY 视频台风图标的流式加载与帧播放。"""

    def __init__(self) -> None:
        self._base_dir = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'mainicon')
        self._streams: Dict[str, _VideoStream] = {}
        self._sizes: Dict[str, Tuple[int, int]] = {}
        self._icon_size_cache: int = 0
        self._ex_icon_size_cache: int = 0
        self._access_order: list = []

    # ── 公开接口 ──

    def get_frame(self, category: str, hemisphere: str, frame_idx: int,
                  target_size: Optional[Tuple[int, int]] = None) -> Optional[pygame.Surface]:
        key = self._make_key(category, hemisphere)
        stream = self._streams.get(key)
        if stream is None:
            stream = self._open(category, hemisphere)
        if stream is None:
            return None
        self._touch(key)
        return stream.get_frame(frame_idx, target_size)

    def get_size(self, category: str, hemisphere: str) -> Optional[Tuple[int, int]]:
        key = self._make_key(category, hemisphere)
        if key in self._sizes:
            return self._sizes[key]
        stream = self._streams.get(key)
        if stream is None:
            stream = self._open(category, hemisphere)
        if stream is None:
            return None
        return self._sizes.get(key)

    def preload_frame_window(self, category: str, hemisphere: str,
                              frame_idx: int, count: int = 30) -> None:
        key = self._make_key(category, hemisphere)
        stream = self._streams.get(key)
        if stream is None:
            stream = self._open(category, hemisphere)
        if stream is not None:
            self._touch(key)
            stream.preload_window(frame_idx, count)

    def unload_unused(self, active_keys: set) -> None:
        for key in list(self._streams.keys()):
            if key not in active_keys:
                self._streams[key].release()
                del self._streams[key]
                if key in self._access_order:
                    self._access_order.remove(key)

    def set_icon_size(self, size: int, ex_size: int = 0) -> None:
        if self._icon_size_cache == size and self._ex_icon_size_cache == ex_size:
            return
        self._icon_size_cache = size
        self._ex_icon_size_cache = ex_size
        for key, stream in self._streams.items():
            cat = key.split(':', 1)[1] if ':' in key else ''
            if _is_ex_category(cat) and ex_size:
                ow, oh = stream.orig_size
                if ow <= 0 or oh <= 0:
                    stream.set_scale_size((size, size))
                    continue
                scale = min(ex_size / ow, ex_size / oh)
                stream.set_scale_size((max(1, int(ow * scale)), max(1, int(oh * scale))))
            else:
                stream.set_scale_size((size, size))

    # ── 内部方法 ──

    @staticmethod
    def _make_key(category: str, hemisphere: str) -> str:
        hemi = 'N' if hemisphere == HEMISPHERE_NORTH else 'S'
        return f"{hemi}:{category}"

    def _touch(self, key: str) -> None:
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        while len(self._streams) > _MAX_STREAMS and self._access_order:
            oldest = self._access_order.pop(0)
            if oldest in self._streams:
                self._streams[oldest].release()
                del self._streams[oldest]
                logger.debug(f"SMCY: LRU 回收 {oldest}")

    def _get_file_name(self, category: str) -> Optional[str]:
        if category in _TC_CATEGORIES:
            return 'tc/' + _TC_CATEGORIES[category] + _VIDEO_EXT
        if category in _EX_CATEGORIES:
            return 'ex/' + _EX_CATEGORIES[category] + _VIDEO_EXT
        if category in _DB_CATEGORIES:
            return 'db/' + _DB_CATEGORIES[category] + _VIDEO_EXT
        return None

    def _open(self, category: str, hemisphere: str) -> Optional[_VideoStream]:
        file_name = self._get_file_name(category)
        if file_name is None:
            logger.warning(f"SMCY: 未找到类别映射 {category}")
            return None

        hemi_prefix = 'N' if hemisphere == HEMISPHERE_NORTH else 'S'
        parts = file_name.split('/')
        if len(parts) != 2:
            return None
        subdir, base = parts
        video_name = f"{hemi_prefix}-{base.replace(_VIDEO_EXT, '')}{_VIDEO_EXT}"
        video_path = os.path.join(self._base_dir, subdir, video_name)

        if not os.path.exists(video_path):
            logger.warning(f"SMCY: 视频文件不存在 {video_path}")
            return None

        stream = _VideoStream(video_path, loop=True)
        if not stream.is_open:
            logger.warning(f"SMCY: 无法打开视频 {video_path}")
            return None

        if self._icon_size_cache > 0:
            if _is_ex_category(category) and self._ex_icon_size_cache > 0:
                ow, oh = stream.orig_size
                if ow <= 0 or oh <= 0:
                    stream.set_scale_size((self._icon_size_cache, self._icon_size_cache))
                else:
                    scale = min(self._ex_icon_size_cache / ow, self._ex_icon_size_cache / oh)
                    stream.set_scale_size((max(1, int(ow * scale)), max(1, int(oh * scale))))
            else:
                stream.set_scale_size((self._icon_size_cache, self._icon_size_cache))

        key = self._make_key(category, hemisphere)
        self._streams[key] = stream
        self._sizes[key] = stream.orig_size
        self._evict_lru()

        logger.info(f"SMCY: 打开 {video_name} ({stream.orig_size[0]}x{stream.orig_size[1]})")
        return stream


# ── 登陆特效 ──

_LANDFALL_MAP = {
    'C1': '[Landing]C1', 'C2': '[Landing]C2', 'C2-': '[Landing]C2',
    'C3': '[Landing]C3', 'C3-': '[Landing]C3',
    'C4': '[Landing]C4', 'C4-ST': '[Landing]C4',
    'C5': '[Landing]C5',
    'TS': '[Landing]S', 'STS': '[Landing]S', 'SS': '[Landing]S',
    'TD': '[Landing]D', 'SD': '[Landing]D',
    'MD': '[Landing]MD',
}

_landfall_cache: Dict[Tuple[str, int, int], list] = {}
_LANDFALL_CACHE_MAX = 24        # 不同尺寸组合上限（缩放/图标尺寸变化时旧条目淘汰）


def get_landfall_frames(category: str, target_w: int = 0, target_h: int = 0) -> Optional[list]:
    """获取登陆特效的全部帧，加载时预缩放到目标尺寸，按尺寸缓存。"""
    name = _LANDFALL_MAP.get(category)
    if name is None:
        return None
    cache_key = (name, target_w, target_h)
    if cache_key in _landfall_cache:
        return _landfall_cache[cache_key]
    path = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'landfall', f'{name}.mp4')
    if not os.path.exists(path):
        return None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    frames = []
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        if target_w > 0 and target_h > 0:
            frame_bgr = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        frames.append(_frame_to_surface(frame_bgr))
    cap.release()
    if len(_landfall_cache) >= _LANDFALL_CACHE_MAX:
        _landfall_cache.pop(next(iter(_landfall_cache)))
    _landfall_cache[cache_key] = frames
    logger.info(f"SMCY 登陆特效: 加载 {name}.mp4 ({len(frames)} 帧, {target_w}x{target_h})")
    return frames


# ── Landed 落地标记动画（流式，一次性播放） ──

_LANDED_MAP = {
    'C1': 'Landed-C1', 'C2': 'Landed-C2', 'C2-': 'Landed-C2',
    'C3': 'Landed-C3', 'C3-': 'Landed-C3',
    'C4': 'Landed-C4', 'C4-ST': 'Landed-C4',
    'C5': 'Landed-C5',
    'TS': 'Landed-S', 'STS': 'Landed-S', 'SS': 'Landed-S',
    'TD': 'Landed-D', 'SD': 'Landed-D',
}

_landed_streams: Dict[str, _VideoStream] = {}
_MAX_LANDED_STREAMS = 8


def landed_frame_count(category: str) -> int:
    """Landed 视频总帧数；无对应视频返回 0。"""
    stream = _open_landed(category)
    return stream._frame_count if stream else 0


def _open_landed(category: str) -> Optional[_VideoStream]:
    name = _LANDED_MAP.get(category)
    if name is None:
        return None
    stream = _landed_streams.get(name)
    if stream is None:
        path = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'landfall', f'{name}.mp4')
        if not os.path.exists(path):
            return None
        stream = _VideoStream(path)
        if not stream.is_open:
            return None
        _landed_streams[name] = stream
        while len(_landed_streams) > _MAX_LANDED_STREAMS:
            oldest = next(iter(_landed_streams))
            _landed_streams[oldest].release()
            del _landed_streams[oldest]
    return stream


def get_landed_frame(category: str, idx: int,
                     target_size: Optional[Tuple[int, int]] = None) -> Optional[pygame.Surface]:
    """获取 Landed 动画的指定帧（一次性播放：越界返回 None）。"""
    stream = _open_landed(category)
    if stream is None or idx < 0 or idx >= stream._frame_count:
        return None
    return stream.get_frame(idx, target_size)


def preload_landfall_effects(lf_size: int, landed_size: int = 0) -> None:
    """预加载全部登陆特效帧与 Landed 流，避免登陆瞬间的解码卡顿。"""
    seen = set()
    for cat, name in _LANDFALL_MAP.items():
        if name in seen:
            continue
        seen.add(name)
        get_landfall_frames(cat, lf_size, lf_size)
    seen.clear()
    for cat, name in _LANDED_MAP.items():
        if name in seen:
            continue
        seen.add(name)
        stream = _open_landed(cat)
        if stream is not None and landed_size > 0:
            stream.get_frame(0, (landed_size, landed_size))
    logger.info(f"登陆特效预加载完成 (lf={lf_size}, landed={landed_size})")


def preload_icon_streams(data: list, target_size: int = 0) -> int:
    """按台风数据中出现的 (类别, 半球) 预打开 SMCY 图标视频流，
    解码首帧到缓存（避免播放时 OpenCV seek 卡顿）。
    返回实际打开的流数（上限受 _MAX_STREAMS 约束）。"""
    mgr = get_smcy_manager()
    categories = set()
    for ty in data:
        cat = None
        try:
            cat = ty.pts[0].get('cat', '')
        except (IndexError, AttributeError):
            pass
        if cat in _TC_CATEGORIES:
            categories.add(cat)
        elif cat in _EX_CATEGORIES:
            categories.add(cat)
    # 保证最常用强度级别始终预打开
    for cat in ('C4', 'C3', 'C2', 'C1', 'TS', 'TD'):
        if cat not in categories:
            categories.add(cat)
    opened = 0
    available = _MAX_STREAMS - len(mgr._streams)
    for cat in sorted(categories):
        for hemi in (HEMISPHERE_NORTH, HEMISPHERE_SOUTH):
            if available <= 0:
                return opened
            key = mgr._make_key(cat, hemi)
            if key in mgr._streams:
                continue
            mgr._open(cat, hemi)
            if key in mgr._streams:
                available -= 1
                if target_size > 0:
                    mgr._streams[key].get_frame(0, (target_size, target_size))
                opened += 1
    return opened


# ── 摘要视频 ──

_SUMMARY_DIR = os.path.join(SUCAI_DIR, ICON_SET_SMCY, 'summary')

_SUMMARY_CATS = {
    'TD': 'TD', 'TS': 'TS', 'STS': 'TS+', 'C1': 'C1', 'C2': 'C2',
    'C2-': 'C2-', 'C3': 'C3', 'C3-': 'C3-', 'C4': 'C4', 'C4-ST': 'C4+',
    'C5': 'C5', 'SS': 'SS',
}

_summary_streams: Dict[str, _VideoStream] = {}
_summary_access: list = []
_MAX_SUMMARY_STREAMS = 10


def _summary_video_path(cat: str, hemi: str) -> Optional[str]:
    name = _SUMMARY_CATS.get(cat)
    if name is None:
        return None
    path = os.path.join(_SUMMARY_DIR, f"Summary-{hemi}-TC-{name}.mp4")
    if os.path.exists(path):
        return path
    if cat == 'SS':
        path = os.path.join(_SUMMARY_DIR, f"Summary-{hemi}-EX-{name}.mp4")
        if os.path.exists(path):
            return path
    return None


def has_summary_video(cat: str, hemi: str) -> bool:
    """该等级/半球是否存在对应的摘要视频。"""
    return _summary_video_path(cat, hemi) is not None


def get_summary_frame(cat: str, hemi: str, idx: int,
                      target_size: Optional[Tuple[int, int]] = None) -> Optional[pygame.Surface]:
    """获取摘要视频的指定帧（流式读取 + 缓存，按宽高比缩放）。"""
    global _summary_streams, _summary_access
    path = _summary_video_path(cat, hemi)
    if path is None:
        return None
    key = f"{hemi}:{cat}"
    if key not in _summary_streams:
        stream = _VideoStream(path)
        if not stream.is_open:
            return None
        _summary_streams[key] = stream
        while len(_summary_streams) > _MAX_SUMMARY_STREAMS and _summary_access:
            oldest = _summary_access.pop(0)
            if oldest in _summary_streams:
                _summary_streams[oldest].release()
                del _summary_streams[oldest]
    if key in _summary_access:
        _summary_access.remove(key)
    _summary_access.append(key)
    return _summary_streams[key].get_frame(idx, target_size)


def set_summary_scale(w: int, h: int) -> None:
    """设置摘要视频的预缩放尺寸（2/5 原大小）。"""
    size = (w, h)
    for stream in _summary_streams.values():
        stream.set_scale_size(size)


def preload_summary_streams(data: list, bar_h: int = 64) -> int:
    """按台风数据中出现的 (类别, 半球) 预打开摘要视频流并解码首帧。
    返回实际打开的流数。"""
    from .summary_effect import TyphoonSummary
    cats = set()
    for ty in data:
        cat = TyphoonSummary._find_peak(ty)
        if cat and cat in _SUMMARY_CATS:
            cats.add(cat)
    for cat in ('C5', 'C4', 'C3', 'C2', 'C1', 'TS', 'TD'):
        if cat not in cats:
            cats.add(cat)
    target = None
    if bar_h > 0:
        target = (bar_h * 1920 // 96, bar_h)
    opened = 0
    for cat in sorted(cats):
        for hemi in ('N', 'S'):
            if has_summary_video(cat, hemi):
                key = f"{hemi}:{cat}"
                if key not in _summary_streams:
                    stream = _VideoStream(_summary_video_path(cat, hemi))
                    if stream.is_open:
                        _summary_streams[key] = stream
                        while len(_summary_streams) > _MAX_SUMMARY_STREAMS and _summary_access:
                            oldest = _summary_access.pop(0)
                            if oldest in _summary_streams:
                                _summary_streams[oldest].release()
                                del _summary_streams[oldest]
                        if target:
                            stream.get_frame(0, target)
                        opened += 1
    return opened


_smcy_manager: Optional[SMCYIconManager] = None


def get_smcy_manager() -> SMCYIconManager:
    global _smcy_manager
    if _smcy_manager is None:
        _smcy_manager = SMCYIconManager()
    return _smcy_manager


def clear_smcy_cache() -> None:
    global _smcy_manager, _summary_streams, _summary_access, _landed_streams, _landfall_cache
    if _smcy_manager is not None:
        for stream in _smcy_manager._streams.values():
            stream.release()
    _smcy_manager = None
    for stream in _summary_streams.values():
        stream.release()
    _summary_streams.clear()
    _summary_access.clear()
    for stream in _landed_streams.values():
        stream.release()
    _landed_streams.clear()
    _landfall_cache.clear()
