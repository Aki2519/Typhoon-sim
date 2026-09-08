# app/sim_settings_dialog.py
"""模拟模式专属设置面板: 粒子密度/风羽/预报样式/四象限风圈/标签等。"""
from __future__ import annotations

import pygame

from .constants import f_s, rt
from .dialog_base import DraggableDialog

_PAD = 14
_ROW_H = 34
_LBL_W = 150


class SimSettingsDialog(DraggableDialog):
    """模拟设置(仅在模拟模式可用)。选项实时生效, 直接读写 sim 属性。"""

    def __init__(self, sim):
        super().__init__(sim)
        self.active = False
        self.bg_rect = pygame.Rect(0, 0, 440, 0)
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._rows = []          # [(key, label, kind, value)]
        self._slider_rects = {}
        self._toggle_rects = {}

    # ── 属性存取 ──
    def _sim(self):
        return getattr(self.sim, 'sim_v4', None)

    def _get(self, key, default):
        s = self._sim()
        if s is not None and key in s:
            return s[key]
        return default

    def _set(self, key, value):
        s = self._sim()
        if s is not None:
            s[key] = value
        # 同步到 sim 自身属性(部分读取方用 getattr)
        setattr(self.sim, key.lstrip('_'), value)

    def _particle_count(self) -> int:
        return max(500, min(6000, int(self._get('_particle_count', 3000))))

    def activate(self) -> None:
        # 必须走基类: 登记 _dialog_stack 并失效 dialog_mgr._active_count,
        # 否则 any_active() 恒为 False → 点击穿透到地图、面板无法被统一关闭
        super().activate()
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - 440) // 2, 150, 440, 320)
        self._layout_valid = False

    def deactivate(self) -> None:
        super().deactivate()

    # ── 绘制 ──
    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        dark = getattr(self.sim, 'dark_mode', True)
        r = self.bg_rect
        bg = (16, 22, 36, 235) if dark else (245, 245, 248, 240)
        border = (55, 85, 130) if dark else (120, 140, 170)
        tc = (215, 225, 245) if dark else (30, 40, 60)
        panel = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, bg, panel.get_rect(), 0, 10)
        pygame.draw.rect(panel, border, panel.get_rect(), 2, 10)
        title = rt(f_s, "模拟设置", tc)
        panel.blit(title, (16, 10))
        cb = pygame.Rect(r.width - 62, 8, 50, 24)
        if dark:
            pygame.draw.rect(panel, (40, 60, 90), cb, 0, 6)
            pygame.draw.rect(panel, border, cb, 1, 6)
        cl = rt(f_s, "关闭", (255, 255, 255))
        panel.blit(cl, (cb.centerx - cl.get_width() // 2,
                        cb.centery - cl.get_height() // 2))
        self._close_btn_rect = cb.move(r.x, r.y)
        self._slider_rects = {}
        self._toggle_rects = {}

        rows = self._rows_spec()
        y = 46
        for key, label, kind in rows:
            lbl = rt(f_s, label, tc)
            panel.blit(lbl, (16, y + 8))
            if kind == 'slider':
                val = self._particle_count()
                sr = pygame.Rect(_PAD + _LBL_W, y + 8, 200, 14)
                pygame.draw.rect(panel, (40, 52, 74) if dark else (200, 208, 220),
                                 sr, 0, 7)
                fw = int(sr.width * (val - 500) / (6000 - 500))
                pygame.draw.rect(panel, (90, 160, 230),
                                 (sr.x, sr.y, fw, sr.height), 0, 7)
                vs = rt(f_s, str(val), tc)
                panel.blit(vs, (sr.right + 10, y + 6))
                self._slider_rects[key] = sr.move(r.x, r.y)
            else:
                val = bool(self._get(key, True))
                tr = pygame.Rect(_PAD + _LBL_W, y + 6, 54, 22)
                if val:
                    pygame.draw.rect(panel, (60, 150, 90), tr, 0, 6)
                else:
                    pygame.draw.rect(panel, (80, 88, 104), tr, 0, 6)
                pygame.draw.rect(panel, border, tr, 1, 6)
                tl = rt(f_s, "开" if val else "关", (255, 255, 255))
                panel.blit(tl, (tr.centerx - tl.get_width() // 2,
                                tr.centery - tl.get_height() // 2))
                self._toggle_rects[key] = tr.move(r.x, r.y)
                if kind == 'style':
                    style = self._get('_fcst_style', 'JTWC')
                    sbtn = pygame.Rect(tr.right + 12, y + 5, 96, 24)
                    pygame.draw.rect(panel, (40, 60, 90) if dark else (210, 216, 228),
                                     sbtn, 0, 6)
                    sl = rt(f_s, f"样式: {style}", (255, 255, 255))
                    panel.blit(sl, (sbtn.centerx - sl.get_width() // 2,
                                    sbtn.centery - sl.get_height() // 2))
                    self._toggle_rects['_style_btn'] = sbtn.move(r.x, r.y)
            y += _ROW_H
        surface.blit(panel, r.topleft)

    def _rows_spec(self) -> list:
        return [
            ('_particle_count', '粒子密度', 'slider'),
            ('_show_barbs', '风羽', 'toggle'),
            ('_show_forecast', '预报路径', 'style'),
            ('_quad_rings', '四象限风圈', 'toggle'),
            ('_show_rings', '风圈显示', 'toggle'),
            ('_show_labels', '台风标签', 'toggle'),
            ('_genesis', '自然生成', 'toggle'),
        ]

    # ── 事件 ──
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self._close_btn_rect.collidepoint(x, y):
                self.deactivate()
                return True
            for key, sr in self._slider_rects.items():
                if sr.collidepoint(x, y):
                    ratio = max(0.0, min(1.0, (x - sr.x) / sr.width))
                    val = int(500 + ratio * (6000 - 500))
                    val = 500 * round(val / 500)
                    self._set(key, val)
                    return True
            for key, tr in self._toggle_rects.items():
                if tr.collidepoint(x, y):
                    if key == '_style_btn':
                        cur = self._get('_fcst_style', 'JTWC')
                        self._set('_fcst_style', 'JMA' if cur == 'JTWC' else 'JTWC')
                    elif key == '_particle_count':
                        continue
                    else:
                        self._set(key, not bool(self._get(key, True)))
                        if key == '_genesis':
                            if self._sim() is not None:
                                self._sim()['params']['genesis'] = bool(
                                    self._get(key, True))
                    return True
            if self.bg_rect.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        return False
