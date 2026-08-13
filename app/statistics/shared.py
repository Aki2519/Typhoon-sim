# py/statistics/shared.py
"""统计模块共享常量：色表、强度阈值、强度填充带。"""
from typing import List, Tuple

from ..constants import DB, TD, TS, C1, C2, C3, C4, C5_L, C5_M, C5_D, C2_MINUS, C3_MINUS, C4_ST

# ── 12 种可区分颜色 ──
COLORS = [
    (220, 60, 60), (60, 160, 60), (60, 60, 220),
    (220, 160, 0), (160, 60, 160), (60, 180, 180),
    (220, 100, 50), (100, 200, 100), (100, 100, 255),
    (200, 200, 50), (200, 50, 200), (50, 200, 200),
]

# ── 强度阈值 + 颜色映射 ──
_THRESHOLDS: List[Tuple[int, Tuple[int, int, int]]] = [
    (29,  TD),
    (34,  TS),
    (64,  C1),
    (83,  C2_MINUS),
    (86,  C2),
    (96,  C3_MINUS),
    (105, C3),
    (113, C4),
    (130, C4_ST),
    (137, C5_L),
    (155, C5_M),
    (170, C5_D),
]

# ── 强度填充带：(y_lower, y_upper, color) ──
_FILL_BANDS: List[Tuple[float, float, Tuple[int, int, int]]] = [
    (0,   29,  DB),
    (29,  34,  TD),
    (34,  64,  TS),
    (64,  83,  C1),
    (83,  86,  C2_MINUS),
    (86,  96,  C2),
    (96,  105, C3_MINUS),
    (105, 113, C3),
    (113, 130, C4),
    (130, 137, C4_ST),
    (137, 155, C5_L),
    (155, 170, C5_M),
    (170, 999, C5_D),
]
