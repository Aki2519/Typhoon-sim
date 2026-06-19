# py/utils.py
"""工具函数。"""
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
    if st in ('MD', 'SD', 'SS', 'LO', 'TD', 'DB'):
        return st
    if st == 'EX':
        return "EX"
    if wind <= 28:   return "DB"
    if wind < 34:    return "TD"
    if wind < 49:    return "TS"
    if wind < 64:    return "STS"
    if wind < 83:    return "CAT1"
    if wind < 96:    return "CAT2"
    if wind < 113:   return "CAT3"
    if wind < 137:   return "CAT4"
    return "CAT5"


def darken_color(c: Tuple, factor: float = 0.6) -> Tuple:
    r, g, b = c[:3]
    a = c[3] if len(c) == 4 else 255
    return (int(r * factor), int(g * factor), int(b * factor), a) if len(c) == 4 else \
           (int(r * factor), int(g * factor), int(b * factor))


def lighten_color(c: Tuple, factor: float = 1.2) -> Tuple:
    r, g, b = c[:3]
    a = c[3] if len(c) == 4 else 255
    return (min(255, int(r * factor)), min(255, int(g * factor)), min(255, int(b * factor)), a) if len(c) == 4 else \
           (min(255, int(r * factor)), min(255, int(g * factor)), min(255, int(b * factor)))