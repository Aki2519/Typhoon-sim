# simulator/ui/theme.py
"""视觉主题: 暗色面板、圆角控件、MapleMono 字体(复用回放程序字体工具)。"""
from __future__ import annotations
import os
import pygame
from app.constants import f_s, f_m, f_l, rt
from app.constants.fonts import _load_font, SmartFont, FONT_FILE

BG = (18, 22, 32)
PANEL = (26, 31, 44)
PANEL_2 = (33, 39, 54)
BORDER = (58, 68, 88)
TEXT = (226, 232, 244)
TEXT_DIM = (150, 160, 178)
ACCENT = (70, 130, 220)
ACCENT_HOT = (235, 160, 60)     # 生成中
ACCENT_BAD = (220, 70, 70)      # 校验异常
OK = (90, 190, 120)
TOGGLE_ON = (52, 88, 140)
TOGGLE_OFF = (58, 66, 86)

GRID = (42, 50, 70)

LAYER_COLORS = [
    (200, 120, 60),   # L1 IR 云图
    (190, 195, 210),  # L2 可见光
    (150, 170, 200),  # L3 全球云图
    (60, 150, 220),   # L4 SST
    (180, 90, 190),   # L5 OHC
    (90, 200, 160),   # L6 风切变
    (220, 200, 90),   # L7 海压
    (120, 200, 230),  # L8 ITCZ
    (235, 235, 235),  # L9 台风
    (200, 120, 120),  # V1 OLR
    (80, 140, 220),   # V2 雷达
    (140, 200, 90),   # V3 地面
    (90, 160, 220),   # V4 中低层
    (160, 100, 200),  # V5 高空
    (200, 160, 60),   # V6 单站
    (220, 90, 90),    # V7 中心
    (80, 180, 140),   # V8 路径
]

_FONT_CACHE: dict = {}
_FONT_CACHE_MAX = 16


def font(size: int) -> SmartFont:
    """K23/G2: 复用缓存字体实例,避免每次新建 Font 导致 rt 缓存(id(f) 作 key)全失效。"""
    sf = _FONT_CACHE.get(size)
    if sf is None:
        sf = SmartFont(_load_font(FONT_FILE, size, size),
                       _load_font(FONT_FILE, size, size))
        if len(_FONT_CACHE) >= _FONT_CACHE_MAX:
            _FONT_CACHE.pop(next(iter(_FONT_CACHE)))
        _FONT_CACHE[size] = sf
    return sf


def text(size: int, s: str, color=TEXT, max_w=None):
    # G1: max_w 默认 None(不换行直接 render);显式传像素宽度才走 wrap
    return rt(font(size), s, color, max_w)


def ellipsis_text(size: int, s: str, color=TEXT, max_w: int = 200) -> pygame.Surface:
    """超宽文本省略号截断(提示词: 文字不得溢出/被遮挡)。"""
    f = font(size)
    if f.size(s)[0] <= max_w:
        return f.render(s, True, color)
    if max_w < 16:
        return f.render("…", True, color)
    out = ""
    for ch in s:
        if f.size(out + ch)[0] > max_w - f.size("…")[0]:
            break
        out += ch
    return f.render(out + "…", True, color)


def _hover(rect) -> bool:
    try:
        return rect.collidepoint(pygame.mouse.get_pos())
    except Exception:
        return False


def draw_panel(surface, rect, radius=6):
    pygame.draw.rect(surface, PANEL, rect, border_radius=radius)
    pygame.draw.rect(surface, BORDER, rect, 1, border_radius=radius)


def draw_button(surface, rect, label, *, hover=False, accent=False,
                enabled=True, size=18):
    # UI 提示词: 交互反馈——按钮 hover/pressed 三态(自动检测鼠标位置)
    if not enabled:
        bg = (46, 52, 66)
        bd = (70, 76, 96)
        tc = TEXT_DIM
    else:
        hov = hover or _hover(rect)
        pressed = hov and pygame.mouse.get_pressed()[0]
        if accent:
            bg = (121, 217, 255) if pressed else (ACCENT if hov else (52, 88, 150))
        else:
            bg = (44, 52, 74) if pressed else (TOGGLE_ON if hov else TOGGLE_OFF)
        bd = (150, 170, 210) if hov else BORDER
        tc = (255, 255, 255) if (hov or pressed) else TEXT
    pygame.draw.rect(surface, bg, rect, border_radius=6)
    pygame.draw.rect(surface, bd, rect, 1, border_radius=6)
    # M8: 标签超宽时省略号截断, 不溢出按钮边界
    ts = ellipsis_text(size, label, tc, max_w=max(8, rect.w - 10))
    surface.blit(ts, (rect.x + (rect.w - ts.get_width()) // 2,
                      rect.y + (rect.h - ts.get_height()) // 2 - 1))


def draw_toggle(surface, rect, label, on, *, hover=False):
    hov = hover or _hover(rect)
    bg = ACCENT if on else (TOGGLE_ON if hov else TOGGLE_OFF)
    pygame.draw.rect(surface, bg, rect, border_radius=6)
    pygame.draw.rect(surface, BORDER if not hov else (150, 170, 210), rect, 1, border_radius=6)
    ts = text(18, label, TEXT)
    surface.blit(ts, (rect.x + (rect.w - ts.get_width()) // 2,
                      rect.y + (rect.h - ts.get_height()) // 2 - 1))


def draw_checkbox(surface, x, y, checked, enabled=True):
    b = pygame.Rect(x, y, 15, 15)
    hov = _hover(b)
    bg = (44, 52, 74) if hov else TOGGLE_OFF
    pygame.draw.rect(surface, bg, b, border_radius=3)
    pygame.draw.rect(surface, (150, 170, 210) if hov else BORDER, b, 1, border_radius=3)
    if checked:
        pygame.draw.rect(surface, ACCENT, (x + 3, y + 3, 9, 9), border_radius=2)


def draw_slider(surface, rect, ratio: float):
    hov = _hover(rect)
    pygame.draw.rect(surface, TOGGLE_OFF, rect, border_radius=3)
    w = max(6, int(rect.w * ratio))
    pygame.draw.rect(surface, ACCENT, (rect.x, rect.y, w, rect.h), border_radius=3)
    if hov:
        pygame.draw.rect(surface, (150, 170, 210), rect, 1, border_radius=3)
