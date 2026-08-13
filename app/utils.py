# py/utils.py
"""工具函数。"""
from __future__ import annotations

import functools
import math
import os
import json
import re
import logging
from typing import Tuple, Optional

try:
    import pygame
except ImportError:  # 无 pygame 环境(如纯数据脚本)下仍可导入本模块
    pygame = None

logger = logging.getLogger(__name__)
_DEFAULT_W, _DEFAULT_H = 1360, 885


def load_window_size() -> Tuple[int, int]:
    try:
        with open("config.json", 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        w = max(800, min(cfg.get("screen_width", _DEFAULT_W), 3840))
        h = max(600, min(cfg.get("screen_height", _DEFAULT_H), 2160))
        return int(w), int(h)
    except Exception as e:
        logger.warning(f"无法加载窗口尺寸配置，使用默认值 {_DEFAULT_W}x{_DEFAULT_H}: {e}")
        return _DEFAULT_W, _DEFAULT_H


_fip_cache: dict = {}


def find_insensitive_path(base_path: str) -> Optional[str]:
    """大小写不敏感路径查找。命中结果缓存;None 结果不缓存,
    避免资源文件之后才出现时永久返回 None。"""
    cached = _fip_cache.get(base_path)
    if cached is not None:
        return cached
    if os.path.exists(base_path):
        result = base_path
    else:
        result = None
        for variant in (base_path.lower(), base_path.upper()):
            if os.path.exists(variant):
                result = variant
                break
        if result is None:
            directory, filename = os.path.dirname(base_path), os.path.basename(base_path).lower()
            if os.path.isdir(directory):
                for f in os.listdir(directory):
                    if f.lower() == filename:
                        result = os.path.join(directory, f)
                        break
    if result is not None:
        if len(_fip_cache) > 128:
            _fip_cache.pop(next(iter(_fip_cache)))
        _fip_cache[base_path] = result
    return result

fip = find_insensitive_path


def play_sound(sound, volume=None) -> None:
    """可靠地播放音效：声道全忙时强制抢占最久的声道，避免静默失败。
    音量通过声道(Channel)设置,不改写共享 Sound 对象的全局音量。"""
    if sound is None:
        return
    try:
        if volume is not None:
            ch = sound.play()
            if ch is not None:
                ch.set_volume(volume)
            else:
                ch = pygame.mixer.find_channel(True)
                if ch is not None:
                    ch.play(sound)
                    ch.set_volume(volume)
        else:
            ch = sound.play()
            if ch is None:
                ch = pygame.mixer.find_channel(True)
                if ch is not None:
                    ch.play(sound)
    except Exception:
        pass


_NON_TROPICAL_TYPES = frozenset({'MD', 'SS', 'SD', 'EX', 'LO'})
_EXCLUDED_TYPES = frozenset({'MD', 'SS', 'SD', 'EX', 'LO', 'DB'})

# Saffir-Simpson 风力等级阈值 (kt)
_WIND_TD_MAX = 28
_WIND_TS_MIN = 34
_WIND_STS_MIN = 49
_WIND_C1_MIN = 64
_WIND_C2_MINUS_MIN = 83
_WIND_C2_MIN = 86
_WIND_C3_MINUS_MIN = 96
_WIND_C3_MIN = 105
_WIND_C4_MIN = 113
_WIND_C4_ST_MIN = 130
_WIND_C5_MIN = 137


def get_valid_winds(pts, exclude_extra: bool = False) -> list:
    excluded = _EXCLUDED_TYPES if exclude_extra else _NON_TROPICAL_TYPES
    return [p['w'] for p in pts if p['st'].upper() not in excluded]


def get_tropical_points(pts) -> list:
    return [p for p in pts if p['st'].upper() not in _NON_TROPICAL_TYPES]


def _tropical_pool(pts) -> list:
    """热带性质报点池(排除 MD/SS/SD/EX/LO);全为非热带时退回全部点。"""
    vp = [p for p in pts if p['st'].upper() not in _NON_TROPICAL_TYPES]
    return vp or list(pts)


def max_wind_from_points(pts, exclude_extra: bool = False) -> int:
    winds = get_valid_winds(pts, exclude_extra)
    return max(winds) if winds else 0


def fmt_short_time(t, with_year: bool = False) -> str:
    """YYYYMMDDHH[MM] → 'MM-DD HH:MM'(可带年份 'YYYY-MM-DD HH:MM')。
    含分钟(12位)时显示真实分钟,否则 ':00'。"""
    if len(t) >= 12:
        body = f"{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}"
    elif len(t) >= 10:
        body = f"{t[4:6]}-{t[6:8]} {t[8:10]}:00"
    else:
        return t
    if with_year:
        return f"{t[:4]}-{body}"
    return body


def peak_point(pts):
    """路径中的巅峰报点(排除 MD/SS/SD/EX/LO 等非热带性质)。"""
    pool = _tropical_pool(pts)
    return max(pool, key=lambda p: p['w']) if pool else None


def movement_speed_kt(pts, points_time, ci: int = -1) -> Optional[float]:
    """相邻报点间移动速度(节)。ci 为当前报点索引(-1/越界取最后两点)。
    points_time 单位: 0.5 = 6 模拟小时,故 1.0 = 12 小时。"""
    n = len(pts)
    if n < 2 or len(points_time) != n:
        return None
    if ci is None or ci < 0 or ci >= n:
        ci = n - 1
    if ci > 0:
        a, b, ia, ib = pts[ci - 1], pts[ci], ci - 1, ci
    else:
        a, b, ia, ib = pts[0], pts[1], 0, 1
    # 1° 纬度 = 60 海里;经度按平均纬度余弦折算
    dlat = (b['la'] - a['la']) * 60.0
    dlon = (b['lo'] - a['lo']) * 60.0 * math.cos(math.radians((a['la'] + b['la']) / 2))
    dt_h = (points_time[ib] - points_time[ia]) * 12.0
    if dt_h <= 0:
        return None
    return math.hypot(dlat, dlon) / dt_h


def infer_strength_category(wind: int, stype: str) -> str:
    st = stype.upper()
    if st in ('MD', 'SD', 'SS', 'LO', 'TD', 'DB', 'WV'):
        return st
    if st == 'EX':
        return "EX"
    if wind <= _WIND_TD_MAX:
        return "DB"
    if wind < _WIND_TS_MIN:
        return "TD"
    if wind < _WIND_STS_MIN:
        return "TS"
    if wind < _WIND_C1_MIN:
        return "STS"
    if wind < _WIND_C2_MINUS_MIN:
        return "C1"
    if wind < _WIND_C2_MIN:
        return "C2-"
    if wind < _WIND_C3_MINUS_MIN:
        return "C2"
    if wind < _WIND_C3_MIN:
        return "C3-"
    if wind < _WIND_C4_MIN:
        return "C3"
    if wind < _WIND_C4_ST_MIN:
        return "C4"
    if wind < _WIND_C5_MIN:
        return "C4-ST"
    return "C5"


_DISPLAY_CAT = {'C2-': 'C2', 'C3-': 'C3'}


def display_category(cat: str) -> str:
    """显示用等级名：C2-/C3- 显示为 C2/C3。"""
    return _DISPLAY_CAT.get(cat, cat)


@functools.lru_cache(maxsize=256)
def darken_color(c: Tuple[int, ...], factor: float = 0.6) -> Tuple[int, ...]:
    r, g, b = c[:3]
    out = (int(r * factor), int(g * factor), int(b * factor))
    return (*out, c[3]) if len(c) == 4 else out


@functools.lru_cache(maxsize=256)
def lighten_color(c: Tuple[int, ...], factor: float = 1.2) -> Tuple[int, ...]:
    r, g, b = c[:3]
    out = (min(255, int(r * factor)), min(255, int(g * factor)),
           min(255, int(b * factor)))
    return (*out, c[3]) if len(c) == 4 else out


# ── 经纬度工具：NSEW 格式显示 / 解析 ──

def lon_to_display(val: float) -> str:
    if abs(val - 180.0) < 0.001:
        return "180.0"
    if abs(val) < 0.001:
        return "0.0"
    if val < 0:
        # 内部偶现负经度(越界中间值),统一按西经显示
        return f"{-val:.1f}W"
    if val > 180.0:
        return f"{360.0 - val:.1f}W"
    return f"{val:.1f}E"


def lat_to_display(val: float) -> str:
    if abs(val) < 0.001:
        return "0.0"
    if val > 0:
        return f"{val:.1f}N"
    return f"{-val:.1f}S"


def parse_lon(text: str) -> float:
    text = text.strip().upper()
    if not text:
        raise ValueError("空经度")
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([EW])', text)
    if m:
        v = float(m.group(1))
        if not (0 <= v <= 360):
            raise ValueError("经度超出范围")
        if m.group(2) == 'W':
            if v == 0 or v == 180:
                return v
            return 360.0 - v
        return v
    if re.fullmatch(r'\d+(\.\d+)?', text):
        v = float(text)
        if 0 <= v <= 360:
            return v
        raise ValueError("经度超出范围")
    raise ValueError("经度格式不正确")


def parse_lat(text: str) -> float:
    text = text.strip().upper()
    if not text:
        raise ValueError("空纬度")
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([NS])', text)
    if m:
        v = float(m.group(1))
        if v > 90:
            raise ValueError("纬度超出范围")
        return -v if m.group(2) == 'S' else v
    if re.fullmatch(r'\d+(\.\d+)?', text):
        v = float(text)
        if v == 0:
            return 0.0
        raise ValueError("纬度需加 N/S 后缀")
    raise ValueError("纬度格式不正确")
