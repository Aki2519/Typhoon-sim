# py/constants.py
"""
全局常量和字体工具.
"""
from __future__ import annotations

import pygame
from typing import Tuple, Dict
from functools import lru_cache
import os
from collections import OrderedDict

DEFAULT_SW = 1360
DEFAULT_SH = 885
SW = DEFAULT_SW
SH = DEFAULT_SH
CPH = 100
MH = SH - CPH

HEMISPHERE_NORTH = "north"
HEMISPHERE_SOUTH = "south"

BG = (230, 245, 255)
GRID = (200, 220, 240)
TXT = (20, 40, 80)
PATH = (70, 130, 180)
CUR_POS = (220, 60, 60)
EDIT = (255, 0, 0, 180)
SPEC = (180, 20, 20)
LIST_BG = (240, 248, 255, 220)
LIST_HL = (180, 220, 255, 200)
DB = (128, 128, 128)
EX = (173, 216, 230)
TD = (70, 130, 180)
TS = (0, 180, 0)
STS = (173, 255, 47)
C1 = (255, 255, 153)
C2 = (210, 180, 140)
C3 = (255, 165, 0)
C4 = (255, 0, 0)
C5_L = (221, 160, 221)
C5_D = (160, 32, 240)
MD_COLOR = (0, 255, 0)
C2_LIGHT = (255, 200, 100)
C2_DARK = (255, 140, 0)
C2_MINUS = C2_LIGHT
C3_MINUS = (255, 180, 60)
C4_ST = (200, 0, 30)
WV = (0, 191, 255)

BUTTON_BORDER = (70, 130, 180)
BUTTON_BG = (100, 150, 200)
BUTTON_DISABLED = (150, 150, 150)
BUTTON_HIGHLIGHT = (180, 220, 255)
BUTTON_HOVER_LIGHTEN = (120, 180, 230)
BUTTON_PRESS_DARKEN = (50, 100, 150)

ERROR_BG = (255, 100, 100)
ERROR_BORDER = (200, 0, 0)

INFO_BOX_BG = (255, 255, 255, 200)
INFO_BOX_BORDER = BUTTON_BORDER

SEASON_CLOCK_BG = (240, 248, 255)
SEASON_CLOCK_BORDER = BUTTON_BORDER
SEASON_CLOCK_QUARTER = (200, 200, 200)

CONTROL_PANEL_BG = (240, 248, 255)
CONTROL_PANEL_LINE = (180, 200, 220)
SPEED_BAR_BG = (200, 200, 210)
SPEED_BAR_FILL = BUTTON_BORDER

FUTURE_LINE_ALPHA = 128

OCEAN_AREA_LINE = (255, 255, 100, 60)

SUCAI_DIR = "./assets/"
SOUND_DIR = "./sound/"
MAP_DIR = "./map/"
TYPHOON_DIR = "./typhoon/"
CONFIG_FILE = "config.json"
DEFAULT_MAP = "./map/map.png"
CUSTOM_MAP = "./map/custom_map.png"
LAND_MASK = "./map/land.png"
AREA_OCEAN_FILE = "./assets/Area_ocean.json"

DIALOG_TITLE_BAR_HEIGHT = 45
DIALOG_CORNER_RADIUS = 10
DIALOG_BG_ALPHA = 230
DIALOG_BORDER_WIDTH = 2

CONTROL_BUTTON_W = 80
CONTROL_BUTTON_H = 25
CONTROL_BUTTON_SPACING = 5
SPEED_BAR_W = 112
SPEED_BAR_H = 12
CONTROL_PANEL_ROW1_Y_OFFSET = 15
CONTROL_PANEL_ROW2_Y_OFFSET = 55

ACE_CHART_DEFAULT_WIDTH = 1800
ACE_CHART_DEFAULT_HEIGHT = 1200
ACE_CHART_PADDING_LEFT = 70
ACE_CHART_PADDING_RIGHT = 20
ACE_CHART_PADDING_TOP = 60
ACE_CHART_GRAPH_HEIGHT = 300
ACE_CHART_CURVE_COLOR = (255, 100, 100)
ACE_CHART_MAX_POINT_COLOR = (0, 200, 0)

TY_LIST_ROWS_PER_PAGE = 6
TY_LIST_ITEM_HEIGHT = 70
TY_LIST_WIDTH = 650
TY_LIST_TOP_OFFSET = 60

POINT_LIST_ROWS_PER_PAGE = 15
POINT_LIST_WIDTH = 900
POINT_LIST_ROW_HEIGHT = 30
POINT_LIST_HEADER_Y = 45

INFO_BOX_WIDTH = 250
INFO_BOX_HEIGHT = 220
INFO_BOX_POS_X = 15
INFO_BOX_POS_Y = 15
SEASON_INFO_BOX_WIDTH = 280
SEASON_INFO_BOX_HEIGHT = 90
SEASON_INFO_BOX_START_X = 180
SEASON_INFO_BOX_START_Y = 10
SEASON_INFO_BOXES_PER_ROW = 3
SEASON_INFO_BOX_SPACING_X = 10
SEASON_INFO_BOX_SPACING_Y = 10

ACE_DISPLAY_BOX_WIDTH = 180
ACE_DISPLAY_BOX_HEIGHT = 80
ACE_DISPLAY_PROGRESS_HEIGHT = 20

TIME_JUMP_WIDTH = 400
TIME_JUMP_HEIGHT = 380

SETTINGS_WIDTH = 500
SETTINGS_HEIGHT = 620

DEFAULT_POINT_RADIUS = 3
PATH_LINE_WIDTH = 2
MAX_CIRCLE_CACHE = 256
INFO_BOX_CORNER_RADIUS = 10
SEASON_CLOCK_RADIUS = 40

FILE_FORMAT_SIMPLE_BDECK = "simple_bdeck"
FILE_FORMAT_JTWC = "jtwc"
FILE_FORMAT_AUTO = "auto"

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'font')

def _load_font(filename: str, size: int, fallback_size: int):
    path = os.path.join(_FONT_DIR, filename)
    try:
        return pygame.font.Font(path, size)
    except:
        return pygame.font.Font(None, fallback_size)

font_en_l = _load_font('bahnschrift.ttf', 22, 22)
font_en_m = _load_font('bahnschrift.ttf', 18, 18)
font_en_s = _load_font('bahnschrift.ttf', 14, 14)
font_en_name = _load_font('bahnschrift.ttf', 21, 21)

font_zh_l = _load_font('unifont_smooth.ttf', 22, 22)
font_zh_m = _load_font('unifont_smooth.ttf', 18, 18)
font_zh_s = _load_font('unifont_smooth.ttf', 14, 14)
font_zh_name = _load_font('unifont_smooth.ttf', 21, 21)


class SmartFont:
    def __init__(self, en_font, zh_font, maxsize=128):
        self.en_font = en_font
        self.zh_font = zh_font
        self._cache: Dict[Tuple[str, Tuple[int,int,int]], pygame.Surface] = {}
        self.maxsize = maxsize

    @lru_cache(maxsize=256)
    def _needs_cjk_font(self, text: str) -> bool:
        for c in text:
            cp = ord(c)
            if ((0x2E80 <= cp <= 0x2FDF) or    # CJK Radicals
                (0x3000 <= cp <= 0x303F) or    # CJK Symbols
                (0x3040 <= cp <= 0x309F) or    # Hiragana
                (0x30A0 <= cp <= 0x30FF) or    # Katakana
                (0x3400 <= cp <= 0x4DBF) or    # CJK Extension A
                (0x4E00 <= cp <= 0x9FFF) or    # CJK Unified
                (0xF900 <= cp <= 0xFAFF) or    # CJK Compatibility
                (0xFF01 <= cp <= 0xFF60) or    # Fullwidth forms
                (0xAC00 <= cp <= 0xD7AF)):     # Hangul
                return True
        return False

    def render(self, text, antialias, color):
        key = (text, color)
        if key in self._cache: return self._cache[key]
        if len(self._cache) >= self.maxsize: self._cache.pop(next(iter(self._cache)))
        font = self.zh_font if self._needs_cjk_font(text) else self.en_font
        surf = font.render(text, antialias, color)
        self._cache[key] = surf
        return surf

    def size(self, text):
        font = self.zh_font if self._needs_cjk_font(text) else self.en_font
        return font.size(text)

    def get_height(self):
        return max(self.en_font.get_height(), self.zh_font.get_height())


font_en_15 = _load_font('bahnschrift.ttf', 15, 15)
font_en_19 = _load_font('bahnschrift.ttf', 19, 19)
font_zh_15 = _load_font('unifont_smooth.ttf', 15, 15)
font_zh_19 = _load_font('unifont_smooth.ttf', 19, 19)

f_l = SmartFont(font_en_l, font_zh_l)
f_m = SmartFont(font_en_m, font_zh_m)
f_s = SmartFont(font_en_s, font_zh_s)
f_name = SmartFont(font_en_name, font_zh_name)
f_15 = SmartFont(font_en_15, font_zh_15)
f_19 = SmartFont(font_en_19, font_zh_19)


def rt(f, text, color, max_width=None):
    if max_width is None: return f.render(text, True, color)
    if not hasattr(rt, "_cache"): rt._cache = OrderedDict()
    cache = rt._cache
    max_cache_size = 1024
    key = (id(f), text, color, max_width)
    if key in cache: cache.move_to_end(key); return cache[key]
    if len(cache) >= max_cache_size: cache.popitem(last=False)
    words = text.split(' ')
    lines, cur = [], ""
    for wd in words:
        test = cur + wd + " "
        if f.size(test)[0] <= max_width: cur = test
        else:
            if cur: lines.append(cur)
            cur = wd + " "
    if cur: lines.append(cur)
    surfaces = [f.render(ln, True, color) for ln in lines]
    h = sum(sf.get_height() for sf in surfaces)
    w = max(sf.get_width() for sf in surfaces)
    canvas = pygame.Surface((w, h), pygame.SRCALPHA)
    y = 0
    for sf in surfaces: canvas.blit(sf, (0, y)); y += sf.get_height()
    cache[key] = canvas
    return canvas


from .utils import darken_color, lighten_color, find_insensitive_path, fip  # noqa: F401