# app/script_target_box.py
"""地图上的 **target 显示范围黄框**（用户 2026-10-13 需求 3.5 / 3.5.1）。

规则（照用户原话）：
```
3.5   输入 target 后，左侧地图显示一个"显示范围黄框"；**只有左下和右上两个角点可拖**；
      框外用黑色半透明遮罩；**右键单击地图空白处**取消黄框。
3.5.1 角点步长：默认 1 度；按 Shift = 0.1 度；Ctrl = 10 度；Alt = 0.01 度。
```
与脚本的关系：黄框 = `target <lon> <lat> w=<deg>` 的**显示范围**，
所以框的宽度**双向**绑定到 `w=`（拖右上角同时改宽与高 ⇒ 保持正方形视野，
与脚本里 `w=` 是"一个数管两边"一致）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import pygame

C_BOX = (255, 214, 64)          # 黄框
C_HANDLE = (255, 240, 140)
MASK_A = 130                    # 框外黑色半透明遮罩的不透明度


def step_for_mods(mods: int) -> float:
    """3.5.1：默认 1 度；Shift 0.1；Ctrl 10；Alt 0.01。"""
    if mods & pygame.KMOD_ALT:
        return 0.01
    if mods & pygame.KMOD_SHIFT:
        return 0.1
    if mods & pygame.KMOD_CTRL:
        return 10.0
    return 1.0


def _current_mods() -> int:
    """当前修饰键状态。video 子系统没起来时不能抛（拖动会直接崩）。"""
    try:
        return pygame.key.get_mods()
    except Exception:
        return 0


class TargetBox:
    """黄框状态 + 绘制 + 两个角点的拖动。"""

    HANDLE_PX = 9

    def __init__(self, sim):
        self.sim = sim
        self.active = False
        self.lon0 = 110.0
        self.lat0 = 5.0
        self.lon1 = 150.0
        self.lat1 = 35.0
        self._drag: Optional[str] = None      # 'bl' | 'tr'
        self._grab = (0.0, 0.0)

    # ── 来自脚本 ──
    def set_from_target(self, lon: float, lat: float, width: float) -> None:
        """脚本里 `target lon lat w=W` ⇒ 以该点为中心、边长 W 的方框。"""
        half = max(0.5, float(width) / 2.0)
        self.lon0, self.lon1 = lon - half, lon + half
        self.lat0, self.lat1 = lat - half, lat + half
        self.active = True

    def to_target(self) -> Tuple[float, float, float]:
        """黄框 ⇒ (中心经度, 中心纬度, 宽度)。宽度取两轴里较大的那个。"""
        lon = (self.lon0 + self.lon1) / 2.0
        lat = (self.lat0 + self.lat1) / 2.0
        w = max(abs(self.lon1 - self.lon0), abs(self.lat1 - self.lat0))
        return lon, lat, w

    def cancel(self) -> None:
        self.active = False
        self._drag = None

    # ── 交互 ──
    def handle_event(self, e) -> bool:
        if e.type == pygame.MOUSEBUTTONUP and self._drag:
            self._drag = None
            return True
        if e.type == pygame.MOUSEMOTION and self._drag:
            self._apply(e.pos)
            return True
        if e.type != pygame.MOUSEBUTTONDOWN:
            return False
        if e.button == 3 and self.active and self._on_empty_map(e.pos):
            self.cancel()                      # 3.5 右键空白处取消
            return True
        if e.button != 1 or not self.active:
            return False
        hit = self._hit(e.pos)
        if hit:
            self._drag = hit
            return True
        return False

    def _on_empty_map(self, pos) -> bool:
        """点击处不在任何台风图标/路径点上 ⇒ 视为"地图空白"。"""
        sim = self.sim
        try:
            return not bool(sim.typhoon_at_screen(pos))
        except Exception:
            return True

    def _screen_rect(self) -> Optional[pygame.Rect]:
        sim = self.sim
        try:
            x0, y0 = sim.latlon_to_screen(self.lat1, self.lon0)   # 左上
            x1, y1 = sim.latlon_to_screen(self.lat0, self.lon1)   # 右下
        except Exception:
            return None
        return pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _hit(self, pos) -> Optional[str]:
        r = self._screen_rect()
        if r is None:
            return None
        bl = (r.left, r.bottom)
        tr = (r.right, r.top)
        for name, p in (('bl', bl), ('tr', tr)):
            if abs(pos[0] - p[0]) <= self.HANDLE_PX and abs(pos[1] - p[1]) <= self.HANDLE_PX:
                # 记下抓取时的手/点差值，拖动更跟手
                self._grab = (pos[0] - p[0], pos[1] - p[1])
                return name
        return None

    def _apply(self, pos) -> None:
        sim = self.sim
        try:
            la, lo = sim.screen_to_latlon((pos[0] - self._grab[0], pos[1] - self._grab[1]))
        except Exception:
            return
        step = step_for_mods(_current_mods())
        lo = round(lo / step) * step
        la = round(la / step) * step
        if self._drag == 'bl':
            self.lon0, self.lat0 = lo, la
        else:
            self.lon1, self.lat1 = lo, la
        # 不许翻转（左下必须真的在左下）
        if self.lon0 > self.lon1:
            self.lon0, self.lon1 = self.lon1, self.lon0
        if self.lat0 > self.lat1:
            self.lat0, self.lat1 = self.lat1, self.lat0

    # ── 绘制 ──
    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        r = self._screen_rect()
        if r is None or r.w < 2 or r.h < 2:
            return
        clip = pygame.Rect(0, 0, surface.get_width(), getattr(self.sim, 'map_height',
                                                               surface.get_height()))
        r = r.clip(clip)
        if r.w < 2 or r.h < 2:
            return
        # 框外黑色半透明遮罩（四块拼出来，避免整屏 SRCALPHA 分配）
        mask = pygame.Surface(clip.size, pygame.SRCALPHA)
        mask.fill((0, 0, 0, MASK_A))
        mask.fill((0, 0, 0, 0), pygame.Rect(r.x - clip.x, r.y - clip.y, r.w, r.h))
        surface.blit(mask, clip.topleft)
        pygame.draw.rect(surface, C_BOX, r, 2)
        for p in ((r.left, r.bottom), (r.right, r.top)):        # 只有两个角点可拖
            pygame.draw.rect(surface, C_HANDLE,
                             pygame.Rect(p[0] - 5, p[1] - 5, 10, 10), 0, 2)
            pygame.draw.rect(surface, (60, 50, 0),
                             pygame.Rect(p[0] - 5, p[1] - 5, 10, 10), 1, 2)
