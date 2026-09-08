"""字体系统：SmartFont、字体实例、rt()渲染"""
from __future__ import annotations

import pygame
import os
from typing import Tuple, Dict
from functools import lru_cache
from collections import OrderedDict

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'assets', 'font')

FONT_FILE = 'MapleMono-NF-CN-Medium.ttf'


_font_cache: dict = {}


def _load_font(filename: str, size: int, fallback_size: int):
    """按 (文件, 字号) 缓存字体对象,避免同一字号被重复解析 TTF。

    无头环境(未先调用 pygame.init())下自动初始化 pygame.font,避免
    import 时因字体子系统未初始化而崩溃;若字体子系统仍无法初始化,
    则抛明确 ImportError 提示调用方先 pygame.init(),而不是返回会
    在渲染时才崩溃的 None 占位。
    """
    if not pygame.font.get_init():
        try:
            pygame.font.init()
        except Exception as e:
            raise ImportError(
                "pygame.font 初始化失败;请先调用 pygame.init() 或 "
                "pygame.font.init() 再导入 app.constants") from e
    key = (filename, size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    path = os.path.join(_FONT_DIR, filename)
    try:
        font = pygame.font.Font(path, size)
    except Exception:
        font = pygame.font.Font(None, fallback_size)
    if len(_font_cache) > 64:
        _font_cache.pop(next(iter(_font_cache)))
    _font_cache[key] = font
    return font


font_en_l = _load_font(FONT_FILE, 22, 22)
font_en_m = _load_font(FONT_FILE, 18, 18)
font_en_s = _load_font(FONT_FILE, 14, 14)
font_en_name = _load_font(FONT_FILE, 21, 21)

font_zh_l = _load_font(FONT_FILE, 22, 22)
font_zh_m = _load_font(FONT_FILE, 18, 18)
font_zh_s = _load_font(FONT_FILE, 14, 14)
font_zh_name = _load_font(FONT_FILE, 21, 21)


def _is_cjk_char(cp: int) -> bool:
    return ((0x2E80 <= cp <= 0x2FDF) or    # CJK Radicals
            (0x3000 <= cp <= 0x303F) or    # CJK Symbols
            (0x3040 <= cp <= 0x309F) or    # Hiragana
            (0x30A0 <= cp <= 0x30FF) or    # Katakana
            (0x3400 <= cp <= 0x4DBF) or    # CJK Extension A
            (0x4E00 <= cp <= 0x9FFF) or    # CJK Unified
            (0xF900 <= cp <= 0xFAFF) or    # CJK Compatibility
            (0xFF01 <= cp <= 0xFF60) or    # Fullwidth forms
            (0xAC00 <= cp <= 0xD7AF))      # Hangul


@lru_cache(maxsize=1024)
def _split_runs(text: str) -> tuple:
    """把文本切成 (is_cjk, 片段) 的连续段：汉字用中文字体，其余用英文字体。"""
    runs = []
    start = 0
    cur = None
    for i, ch in enumerate(text):
        is_cjk = _is_cjk_char(ord(ch))
        if cur is None:
            cur = is_cjk
        elif is_cjk != cur:
            runs.append((cur, text[start:i]))
            start = i
            cur = is_cjk
    if text:
        runs.append((cur, text[start:]))
    return tuple(runs)


class SmartFont:
    def __init__(self, en_font, zh_font, maxsize=128):
        self.en_font = en_font
        self.zh_font = zh_font
        self._cache: Dict[Tuple[str, Tuple[int, int, int]], pygame.Surface] = {}
        self.maxsize = maxsize

    def _font_for(self, is_cjk: bool):
        return self.zh_font if is_cjk else self.en_font

    def _render_mixed(self, runs, antialias, color) -> pygame.Surface:
        """分段渲染后按基线对齐拼接。"""
        parts = []
        max_ascent = 0
        for is_cjk, seg in runs:
            font = self._font_for(is_cjk)
            parts.append((font, font.render(seg, antialias, color)))
            max_ascent = max(max_ascent, font.get_ascent())
        width = sum(s.get_width() for _, s in parts)
        height = max(max_ascent - f.get_ascent() + s.get_height() for f, s in parts)
        canvas = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
        x = 0
        for f, s in parts:
            canvas.blit(s, (x, max_ascent - f.get_ascent()))
            x += s.get_width()
        return canvas

    def render(self, text, antialias, color):
        key = (text, color, antialias)
        if key in self._cache:
            return self._cache[key]
        if len(self._cache) >= self.maxsize:
            self._cache.pop(next(iter(self._cache)))
        runs = _split_runs(text)
        if len(runs) <= 1:
            font = self._font_for(runs[0][0]) if runs else self.en_font
            surf = font.render(text, antialias, color)
        else:
            surf = self._render_mixed(runs, antialias, color)
        self._cache[key] = surf
        return surf

    def size(self, text):
        runs = _split_runs(text)
        if len(runs) <= 1:
            font = self._font_for(runs[0][0]) if runs else self.en_font
            return font.size(text)
        w, h = 0, 0
        for is_cjk, seg in runs:
            sw, sh = self._font_for(is_cjk).size(seg)
            w += sw
            h = max(h, sh)
        return (w, h)

    def get_height(self):
        return max(self.en_font.get_height(), self.zh_font.get_height())


font_en_15 = _load_font(FONT_FILE, 15, 15)
font_en_19 = _load_font(FONT_FILE, 19, 19)
font_zh_15 = _load_font(FONT_FILE, 15, 15)
font_zh_19 = _load_font(FONT_FILE, 19, 19)

f_l = SmartFont(font_en_l, font_zh_l)
f_m = SmartFont(font_en_m, font_zh_m)
f_s = SmartFont(font_en_s, font_zh_s)
f_name = SmartFont(font_en_name, font_zh_name)
f_15 = SmartFont(font_en_15, font_zh_15)
f_19 = SmartFont(font_en_19, font_zh_19)


def rt(f, text, color, max_width=None, smooth=True):
    if max_width is None or max_width <= 0:
        return f.render(text, smooth, color)
    if not hasattr(rt, "_cache"):
        rt._cache = OrderedDict()
    cache = rt._cache
    max_cache_size = 1024
    key = (id(f), text, color, max_width, bool(smooth))
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    if len(cache) >= max_cache_size:
        cache.popitem(last=False)
    # G6: 空格断词对中文(无空格)会退化为整段一个"词"而无法换行。
    # 改为:先按空格分词积累;整词超宽(含中文整段)时再逐字符拆行。
    # 尾随空格统一去掉,避免行尾残留空格影响宽度/渲染。
    def _width(s: str) -> int:
        return f.size(s)[0]

    words = text.split(' ')
    lines, cur = [], ""
    for wd in words:
        cand = (cur + wd).rstrip(' ')
        if _width(cand + " ") <= max_width:
            cur = cand + " "
            continue
        # 当前行存下（若非空）
        if cur:
            lines.append(cur.rstrip(' '))
            cur = ""
        # 单个词(可能为无空格的中文整段/超长拉丁词)自身超宽 → 逐字符拆分
        run = ""
        for ch in wd:
            trial = run + ch
            if _width(trial) > max_width:
                if run:
                    lines.append(run)
                run = ch
            else:
                run = trial
        if run:
            cur = run + " "
    if cur:
        lines.append(cur.rstrip(' '))
    # 空行(整段仅空白)也保留一个空串占位,避免后续 max() 崩溃
    if not lines:
        lines = [""]
    surfaces = [f.render(ln, smooth, color) for ln in lines]
    h = sum(sf.get_height() for sf in surfaces)
    w = max(sf.get_width() for sf in surfaces)
    canvas = pygame.Surface((w, h), pygame.SRCALPHA)
    y = 0
    for sf in surfaces:
        canvas.blit(sf, (0, y))
        y += sf.get_height()
    cache[key] = canvas
    return canvas
