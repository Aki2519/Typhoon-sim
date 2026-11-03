# app/offscreen_storm.py
"""**域外风暴小窗**（用户 2026-10-12 需求 1 及 1.1–1.6）。

需求原文要点：
  1   在屏幕**边缘的空白处**，用**白色方框**显示指定的、**不在屏幕内**的风暴；框内是正常地图
  1.1 框内图标大小 = 正常画面里的图标大小（在"图标大小占屏幕比例不变"的设置为前提）；
      方框边长 = 刚好容纳"图标 + 名字"（图标居中）再加一点余量；**正方形**
  1.2 只能放在边缘
  1.3 被其它风暴的图标/名字或其它文字/东西（如月度总结框）覆盖时（本框优先级较低），
      从被覆盖起 **1s 内不透明度平滑降到 20%**；离开后 **0.5s 内回到 100%**
  1.4 同时最多 **5** 个；放在屏幕右侧（放得下几个由 `slots()` 实测决定）
  1.5 功能栏右上角"域外风暴"按钮选哪些风暴走这套显示（见 `offscreen_dialog.py`）
  1.6 图标任意一角进入屏幕 ⇒ 框在 **0.5s 内消失**；图标完全看不见 ⇒ 框在 **0.5s 内出现**；
      风暴若生成在屏幕内，框从一开始就不出现
"""
from __future__ import annotations

import logging

import pygame

from .constants import f_s, rt

logger = logging.getLogger(__name__)

MAX_BOXES = 5
DIM_ALPHA = 0.20          # 被覆盖时的目标不透明度（20%）
FADE_DOWN_S = 1.0         # 覆盖后降到 20% 用 1s
FADE_UP_S = 0.5           # 离开后升回 100% 用 0.5s
FADE_ONOFF_S = 0.5        # 进出屏幕的消失/出现用 0.5s
GAP = 8                   # 方框之间的间距
EDGE_MARGIN = 8           # 距屏幕边缘
BORDER_W = 2


class OffscreenStormBoxes:
    """管一堆"域外风暴"小窗的状态与绘制。由 `TySim` 持有（`sim.offscreen`）。"""

    def __init__(self, sim) -> None:
        self.sim = sim
        self._alpha: dict = {}      # ty -> float 0..1（当前不透明度系数）
        self._covered: dict = {}    # ty -> bool（上一帧是否被压住）
        self._content: dict = {}    # ty -> (key, Surface) 内容缓存
        self._layout: list = []     # 上一帧实际摆放的 [(ty, rect)]

    # ── 开关 ──────────────────────────────────────────────────────────
    def enabled(self) -> bool:
        return bool(getattr(self.sim.cfg, 'show_offscreen_boxes', True))

    def selected(self, ty) -> bool:
        """该风暴是否被用户指定走"域外显示"（1.5 的对话框写入 `ty.offscreen`）。

        `gen_born_onscreen` 为真（生成时就在屏幕里，1.6 末句）⇒ **永不显示**。
        """
        if getattr(ty, 'offscreen', None) is None:
            return False
        return bool(getattr(ty, 'offscreen')) and not getattr(ty, 'born_onscreen', False)

    @staticmethod
    def in_area_limit(sim, ty) -> bool:
        """这个风暴是否**已经落在当前洋区限制内**（用户 2026-10-19）。

        在限制内的风暴本来就该出现在正常视野里，不该再进"域外小窗"
        —— 对话框不列它，小窗也不画它。

        ⚠ 限制模式是  时  恒为 True，若照用会把
        **所有**风暴都判成"域内"（对话框直接空掉）。所以 none 时一律返回
        False（没有限制 ⇒ 不做这个过滤）。
        """
        try:
            eng = getattr(sim, 'ace_engine', None)
            mode = getattr(sim, 'ace_limit_mode', 'none')
            if eng is None or mode == 'none':
                return False
            pos = ty.cpos() or {}
            la, lo = pos.get('la'), pos.get('lo')
            if la is None or lo is None:
                p = ty.cp() if hasattr(ty, 'cp') else None
                if not p:
                    return False
                la, lo = p.get('la'), p.get('lo')
            if la is None or lo is None:
                return False
            return bool(eng.point_in_limit(la, lo))
        except Exception:
            return False

    def candidates(self) -> list:
        return [ty for ty in getattr(self.sim, 'tys', [])
                if getattr(ty, 'act', False) and self.selected(ty)
                and not self.in_area_limit(self.sim, ty)]

    # ── 几何 ──────────────────────────────────────────────────────────
    def _cp(self, ty):
        try:
            return ty.cp()
        except Exception:
            return None

    def _screen_xy(self, ty):
        p = self._cp(ty)
        if not p:
            return None
        try:
            return self.sim.latlon_to_screen(p['la'], p['lo'])
        except Exception:
            return None

    def icon_size(self, ty) -> int:
        """与正常画面**同一个口径**的图标边长（px）。"""
        p = self._cp(ty) or {}
        cat = 'TC'
        try:
            cat = self.sim.get_strength_category(p.get('w', 0), p.get('st', ''))
        except Exception:
            pass
        try:
            icon_factor = self.sim._size_factors()[1]
            return max(8, int(round(self.sim._icon_target_px(cat, icon_factor))))
        except Exception:
            return 48

    def _name_pair(self, ty):
        """(白字, 黑描边) 同一字体同一字号 —— 供小窗居中绘制。按 (名字, 字号) 缓存。"""
        text = self._name_text(ty)
        if not text:
            return None, None
        try:
            size = max(12, int(round(12 * (getattr(self.sim, 'name_size', 100) / 100.0) * 1.5)))
        except Exception:
            size = 18
        key = (text, size)
        hit = _NAME_CACHE.get(key)
        if hit is None:
            from .constants.fonts import _load_font, FONT_FILE
            font = _load_font(FONT_FILE, size, size)
            hit = (rt(font, text, (255, 255, 255)), rt(font, text, (0, 0, 0)))
            if len(_NAME_CACHE) > 128:
                _NAME_CACHE.pop(next(iter(_NAME_CACHE)))
            _NAME_CACHE[key] = hit
        return hit

    def name_surface(self, ty):
        return self._name_pair(ty)[0]

    def box_side(self, ty) -> int:
        """1.1: 刚好容纳「图标 + 名字」（图标居中）+ 余量；正方形。"""
        icon = self.icon_size(ty)
        nm = self.name_surface(ty)
        nh = nm.get_height() if nm is not None else 0
        return int(max(icon + 2 * EDGE_MARGIN, icon + nh + 2 * EDGE_MARGIN))

    def _icon_rect(self, ty):
        """图标在**屏幕坐标**下的矩形（1.6 的"任意一角进入屏幕"用它判）。"""
        xy = self._screen_xy(ty)
        if xy is None:
            return None
        s = self.icon_size(ty)
        return pygame.Rect(int(xy[0] - s / 2), int(xy[1] - s / 2), s, s)

    def _on_screen(self, ty) -> bool:
        r = self._icon_rect(ty)
        if r is None:
            return False
        view = pygame.Rect(0, 0, int(getattr(self.sim, 'screen_width', 0)),
                           int(getattr(self.sim, 'map_height', 0)))
        return r.colliderect(view)

    def slots(self) -> list:
        """右侧一列可用的矩形（1.4：最多 5 个，放得下几个由高度决定）。"""
        sim = self.sim
        out = []
        side = None
        for ty in self.candidates():
            side = self.box_side(ty)
            break
        if side is None:
            return out
        avail_h = int(getattr(sim, 'map_height', 0)) - 2 * EDGE_MARGIN
        n = min(MAX_BOXES, max(0, (avail_h + GAP) // (side + GAP)))
        x = int(getattr(sim, 'screen_width', 0)) - EDGE_MARGIN - side
        y = EDGE_MARGIN
        for _ in range(n):
            out.append(pygame.Rect(x, y, side, side))
            y += side + GAP
        return out

    # ── 覆盖判定（1.3）───────────────────────────────────────────────
    def obstacles(self, exclude_ty=None) -> list:
        """会"压住"小窗的东西：其它风暴的图标/名字、信息框、月度总结框。"""
        sim = self.sim
        out = []
        for ty in getattr(sim, 'tys', []):
            if ty is exclude_ty or not getattr(ty, 'act', False):
                continue
            r = self._icon_rect(ty)
            if r is not None:
                out.append(r)
            xy = self._screen_xy(ty)
            nm = self.name_surface(ty)
            if xy is not None and nm is not None:
                # 名字在地图上锚在图标右上角（名字位置可在设置里调）——这里用同一
                # 语义的近似矩形：右上角起点 + 名字尺寸。
                out.append(pygame.Rect(int(xy[0]), int(xy[1] - nm.get_height()),
                                       nm.get_width(), nm.get_height()))
        hit = getattr(sim, '_info_box_hit', None)
        if isinstance(hit, dict):
            for rect in hit.values():
                if isinstance(rect, pygame.Rect):
                    out.append(rect)
        ms = getattr(sim, '_ms', None)
        if ms is not None and getattr(ms, 'active', False):
            try:
                rect = pygame.Rect(0, 0, 0, 0)
                from . import monthly_summary as _M
                # 与 monthly_summary.draw 用同一组常量，避免两处漂移
                w = int(getattr(_M, 'BOX_W', 420))
                h = int(getattr(_M, 'BOX_H', 285))
                y = int(getattr(_M, 'BOX_Y', 240))
                rect = pygame.Rect(int(sim.screen_width) - 10 - w, y, w, h)
                out.append(rect)
            except Exception:
                pass
        return out

    # ── 状态推进 ──────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        sim = self.sim
        if not self.enabled():
            self._alpha.clear()
            self._covered.clear()
            self._layout = []
            return
        placed = []
        slots = self.slots()
        obs = self.obstacles()
        cands = self.candidates()
        for i, ty in enumerate(cands[:MAX_BOXES]):
            if i >= len(slots):
                break
            rect = slots[i]
            on_screen = self._on_screen(ty)
            covered = any(rect.colliderect(o) for o in obs if o is not None)
            cur = self._alpha.get(ty, 0.0)
            if on_screen:
                target, rate = 0.0, FADE_ONOFF_S          # 1.6 进入屏幕 ⇒ 0.5s 消失
            elif covered:
                target, rate = DIM_ALPHA, FADE_DOWN_S     # 1.3 被覆盖 ⇒ 1s 降到 20%
            else:
                if cur < DIM_ALPHA:
                    target, rate = 1.0, FADE_ONOFF_S      # 1.6 完全看不见 ⇒ 0.5s 出现
                else:
                    target, rate = 1.0, FADE_UP_S         # 1.3 离开覆盖 ⇒ 0.5s 回到 100%
            step = (dt / max(1e-6, rate))
            if cur < target:
                cur = min(target, cur + step)
            elif cur > target:
                cur = max(target, cur - step)
            self._alpha[ty] = cur
            self._covered[ty] = covered
            if cur > 0.01:
                placed.append((ty, rect))
        self._layout = placed

    # ── 绘制 ──────────────────────────────────────────────────────────
    def draw(self, surface) -> None:
        if not self.enabled():
            return
        for ty, rect in self._layout:
            a = self._alpha.get(ty, 0.0)
            if a <= 0.01:
                continue
            self._draw_one(surface, ty, rect, a)

    def _draw_one(self, surface, ty, rect, alpha: float) -> None:
        sim = self.sim
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        # 1. 框内是正常地图：从全局底图裁一块以风暴为中心的窗口（原生 30px/°）
        try:
            img = getattr(sim.map_mgr, 'original_img', None)
            p = self._cp(ty)
            if img is not None and p is not None:
                W, H = img.get_size()
                cx = int((p['lo'] % 360.0) / 360.0 * W)
                cy = int((90.0 - p['la']) / 180.0 * H)
                half = rect.w // 2
                crop = pygame.Rect(cx - half, cy - half, rect.w, rect.h)
                # 跨 0°/360° 或越界时夹回图内（不重采样，直接平移裁切）
                crop.x = max(0, min(W - rect.w, crop.x))
                crop.y = max(0, min(H - rect.h, crop.y))
                surf.blit(img.subsurface(crop), (0, 0))
        except Exception:
            surf.fill((16, 24, 40))
            logger.debug('域外小窗: 底图裁切失败', exc_info=True)
        # 2. 图标（与正常画面同一像素尺寸；居中）
        try:
            p = self._cp(ty) or {}
            cat = sim.get_strength_category(p.get('w', 0), p.get('st', ''))
            icon_factor = sim._size_factors()[1]
            col = {'cat': cat, 'w': p.get('w', 0), 'st': p.get('st', '')}
            cx, cy = rect.w // 2, rect.h // 2
            if getattr(sim, 'icon_set', '') == 'smcy':
                sim._draw_smcy_frame(surf, ty, cat, getattr(ty.v, '_smcy_frame', 0),
                                     cx, cy, icon_factor, 255, p.get('w', 0))
            else:
                sim._draw_simple_icon(surf, ty, cat, col, cx, cy, icon_factor, 255)
        except Exception:
            logger.debug('域外小窗: 图标绘制失败', exc_info=True)
        # 3. 名字（图标下方居中）
        nm, shadow = self._name_pair(ty)
        if nm is not None:
            icon = self.icon_size(ty)
            y = (rect.h + icon) // 2
            y = min(y, rect.h - nm.get_height())
            x = (rect.w - nm.get_width()) // 2
            for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                surf.blit(shadow, (x + dx, y + dy))
            surf.blit(nm, (x, y))
        # 4. 白框
        pygame.draw.rect(surf, (255, 255, 255, 255), (0, 0, rect.w, rect.h), BORDER_W)
        surf.set_alpha(int(255 * max(0.0, min(1.0, alpha))))
        surface.blit(surf, rect.topleft)

    def _name_text(self, ty) -> str:
        try:
            return self.sim.get_display_name(ty) or ''
        except Exception:
            return getattr(ty, 'sname', '') or ''

    # 供测试/调试
    def layout(self):
        return list(self._layout)

    def alpha_of(self, ty) -> float:
        return float(self._alpha.get(ty, 0.0))


def _name_font_path() -> str:
    from .constants.fonts import FONT_FILE
    return FONT_FILE


_NAME_CACHE: dict = {}
