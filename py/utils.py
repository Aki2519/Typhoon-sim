# py/utils.py
"""工具函数。"""
from __future__ import annotations

import os
import json
from typing import Tuple, Optional

_DEFAULT_W, _DEFAULT_H = 1360, 885


def load_window_size() -> Tuple[int, int]:
    try:
        with open("config.json", 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        w = max(800, min(cfg.get("screen_width", _DEFAULT_W), 3840))
        h = max(600, min(cfg.get("screen_height", _DEFAULT_H), 2160))
        return int(w), int(h)
    except Exception:
        return _DEFAULT_W, _DEFAULT_H


def find_insensitive_path(base_path: str) -> Optional[str]:
    if os.path.exists(base_path):
        return base_path
    for variant in (base_path.lower(), base_path.upper()):
        if os.path.exists(variant):
            return variant
    directory, filename = os.path.dirname(base_path), os.path.basename(base_path).lower()
    if os.path.isdir(directory):
        for f in os.listdir(directory):
            if f.lower() == filename:
                return os.path.join(directory, f)
    return None


fip = find_insensitive_path


def infer_strength_category(wind: int, stype: str) -> str:
    st = stype.upper()
    if st in ('MD', 'SD', 'SS', 'LO', 'TD', 'DB', 'WV'):
        return st
    if st == 'EX':
        return "EX"
    if wind <= 28:
        return "DB"
    if wind < 34:
        return "TD"
    if wind < 49:
        return "TS"
    if wind < 64:
        return "STS"
    if wind < 83:
        return "C1"
    if wind < 86:
        return "C2-"
    if wind < 96:
        return "C2"
    if wind < 105:
        return "C3-"
    if wind < 113:
        return "C3"
    if wind < 130:
        return "C4"
    if wind < 137:
        return "C4-ST"
    return "C5"


def darken_color(c: Tuple[int, ...], factor: float = 0.6) -> Tuple[int, ...]:
    r, g, b = c[:3]
    a = c[3] if len(c) == 4 else 255
    return (int(r * factor), int(g * factor), int(b * factor), a) if len(c) == 4 else \
           (int(r * factor), int(g * factor), int(b * factor))


def lighten_color(c: Tuple[int, ...], factor: float = 1.2) -> Tuple[int, ...]:
    r, g, b = c[:3]
    a = c[3] if len(c) == 4 else 255
    return (min(255, int(r * factor)), min(255, int(g * factor)),
            min(255, int(b * factor)), a) if len(c) == 4 else \
           (min(255, int(r * factor)), min(255, int(g * factor)), min(255, int(b * factor)))


# ── 经纬度工具：NSEW 格式显示 / 解析 ──

def lon_to_display(val: float) -> str:
    if abs(val - 180.0) < 0.001:
        return "180.0"
    if abs(val) < 0.001:
        return "0.0"
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
        return 0.0
    if text.endswith('W'):
        return 360.0 - float(text[:-1])
    if text.endswith('E'):
        return float(text[:-1])
    return float(text)


def parse_lat(text: str) -> float:
    text = text.strip().upper()
    if not text:
        return 0.0
    if text.endswith('S'):
        return -float(text[:-1])
    if text.endswith('N'):
        return float(text[:-1])
    return float(text)
