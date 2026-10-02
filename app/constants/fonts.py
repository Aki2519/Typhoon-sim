"""字体系统：SmartFont、字体实例、rt()渲染"""
from __future__ import annotations

import pygame
import os
from typing import Tuple, Dict
from functools import lru_cache
from collections import OrderedDict

from ..cache_store import SurfaceCache

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


# ── rt() 默认分支的文本表面缓存(2026-10-02) ────────────────────────────────
# **问题不是"没有缓存", 而是"条数上限太小且是 FIFO"**:
#   `rt(f, text, color)`(三参、无 max_width)先前直连 `f.render`。而 `f` 是 SmartFont,
#   它**自带**一个 128 条的 FIFO 缓存(见本文件 `SmartFont.__init__` 的 maxsize=128
#   与 `SmartFont.render` 的 pop(next(iter)))。实测 `PaintDialog` 稳态在 `f_s` 上就有
#   **139 个**不同 (text, color, antialias) 键 > 128 ⇒ 缓存每帧被自己冲掉, 实测每帧
#   **139–140 次真实字形光栅化**(其中 134–135 次经 rt() 调用, 5 次是 dialog_base /
#   input_field 直连 `f.render` 绕过了 rt, 不归本缓存管)。
#
# 因此这里在 rt() 的默认分支**前置**一层"字节预算 + 真 LRU"的缓存:
#   · **预算 8 MiB**: 实测 `PaintDialog` 稳态工作集只有 **0.64 MiB / 146 键**
#     (单张最大 21 KB) ⇒ 12 倍余量, 稳态 `evictions` 恒为 0。
#     不用条数封顶 —— 本批已两次踩到这个坑(`_name_shadow_cache` 256 条导致每帧自噬;
#     `_landfall_cache` 只按 24 条封顶导致最坏 1.37–5.5 GB)。
#   · 直接用 `app.cache_store.SurfaceCache`(自带 hits/misses/evictions/total_bytes),
#     不手写 OrderedDict。
#   · `_rt_font_refs` **钉住字体对象**: 键里含 `id(f)`, 而 `_draw_icon_mixin` 的
#     `_info_box_font` / `_sized_font` 会在**超过 24 个字号**时淘汰 SmartFont 实例
#     ⇒ 被淘汰对象被 GC 后其 `id` 可能被新建对象复用, 造成**串味**。持强引用后
#     `id` 在缓存有效期内不可能被复用(全 app 的 SmartFont 实例是常数级, 代价可忽略)。
#     ⚠ 既有的 max_width 缓存也用 `id(f)` 且**没有**这层保护 —— 那是既有问题,
#       本次按"只改默认分支"的约束不动它。
#   · `RT_TEXT_CACHE_BYTES <= 0` ⇒ 每次调用直接 `f.render`, 完全回到旧行为(回退开关;
#     运行时改这个模块级常量即刻生效, 不必重建缓存对象)。
RT_TEXT_CACHE_BYTES = 8 * 1024 * 1024

_rt_cache = SurfaceCache("font_rt_text", RT_TEXT_CACHE_BYTES) if RT_TEXT_CACHE_BYTES > 0 else None
#: id(f) → f 的强引用。唯一作用: 让缓存键里的 id() 不会被复用。
_rt_font_refs: dict = {}


def rt_cache_stats():
    """文本缓存统计(hits/misses/evictions/bytes + 钉住的字体数)。"""
    if _rt_cache is None:
        return {"name": "font_rt_text", "enabled": RT_TEXT_CACHE_BYTES > 0,
                "items": 0, "bytes": 0, "max_bytes": RT_TEXT_CACHE_BYTES,
                "hits": 0, "misses": 0, "evictions": 0, "fonts_pinned": 0}
    d = _rt_cache.stats()
    d["enabled"] = RT_TEXT_CACHE_BYTES > 0
    d["fonts_pinned"] = len(_rt_font_refs)
    return d


def rt_cache_clear():
    """清空文本缓存(改字号/换字体/测试隔离用)。"""
    if _rt_cache is not None:
        _rt_cache.clear()
    _rt_font_refs.clear()


def rt(f, text, color, max_width=None, smooth=True):
    if max_width is None or max_width <= 0:
        if _rt_cache is None or RT_TEXT_CACHE_BYTES <= 0:
            return f.render(text, smooth, color)
        try:
            key = (id(f), text, color, bool(smooth))
            hash(key)
        except TypeError:
            return f.render(text, smooth, color)      # color 不可哈希 → 不缓存
        surf = _rt_cache.get(key)
        if surf is not None:
            return surf
        surf = f.render(text, smooth, color)
        # 先钉住字体再写缓存: 保证键里的 id(f) 在存活期内唯一
        if id(f) not in _rt_font_refs:
            _rt_font_refs[id(f)] = f
        _rt_cache.put(key, surf)
        return surf
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
