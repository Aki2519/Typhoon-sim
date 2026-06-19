# py/statistics/path_length_viewer.py
"""台风路径长度查看器。支持按路径长度/台风顺序排序。"""
import math
import pygame
from typing import List, Tuple, Optional

from ..constants import f_s, f_m, rt, TXT, BUTTON_BORDER, DIALOG_TITLE_BAR_HEIGHT
from ..dialog_base import DraggableDialog


def _haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * math.asin(math.sqrt(a)) * 6371


def _ts_eligible(pt: dict) -> bool:
    return pt['st'].upper() in ('TS', 'TY', 'ST', 'HU', '') and pt.get('w', 0) >= 34


class PathLengthViewer(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._sort_mode = 0  # 0=按路径长度 1=按台风顺序（复刻台风列表排序）
        self._data: List[Tuple[str, float, int, object]] = []  # (name, path_km, year, ty)
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._btn_rects: dict = {}
        self._scroll_offset = 0
        self._row_h = 24
        self._visible_rows = 30
        self._scrollbar_dragging = False
        self._scrollbar_rect = pygame.Rect(0, 0, 0, 0)

    def activate(self):
        super().activate()
        self._scroll_offset = 0
        self._data = []
        year = self.sim.current_ace_year
        engine = self.sim.ace_engine
        for ty in self.sim.tys:
            if not ty.pts:
                continue
            ts_pts = [p for p in ty.pts
                      if _ts_eligible(p)
                      and p.get('ace_year', 0) == year
                      and engine.point_in_limit(p['la'], p['lo'])]
            path = 0.0
            for i in range(len(ts_pts) - 1):
                path += _haversine(
                    ts_pts[i]['la'], ts_pts[i]['lo'],
                    ts_pts[i + 1]['la'], ts_pts[i + 1]['lo'])
            if path > 0:
                self._data.append((self.sim.get_display_name(ty), path, year, ty))
        self._sort()
        w, h = 700, 820
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_surf = None

    def _build_basin_order(self) -> dict:
        """构建洋区排序字典，复刻台风列表的 basin_order。"""
        areas = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if areas and areas.areas:
            return {a.code: i for i, a in enumerate(areas.areas)}
        return {}

    def _sort(self):
        if self._sort_mode == 0:
            self._data.sort(key=lambda x: x[1], reverse=True)
        else:
            # 按台风顺序：复刻台风列表排序 (basin_idx, first_time, name.lower())
            basin_order = self._build_basin_order()
            def _sort_key(item):
                name, path, year, ty = item
                basin_idx = basin_order.get(ty.basin, 9999)
                first_time = ty.pts[0]['t'] if ty.pts else "99999999"
                return (basin_idx, first_time, name.lower())
            self._data.sort(key=_sort_key)

    def _render(self):
        if self._cached_surf is not None:
            return
        w, h = self.bg_rect.width, self.bg_rect.height
        surf = pygame.Surface((w, h), pygame.SRCALPHA)

        cols = [
            ("排名", 50), ("名称", 200), ("路径长度(km)", 140),
            ("年份", 60),
        ]
        y = 60
        cx = 20
        for label, width in cols:
            surf.blit(rt(f_m, label, TXT), (cx, y))
            cx += width + 5
        y += 28

        total = len(self._data)
        for i in range(self._scroll_offset,
                       min(self._scroll_offset + self._visible_rows, total)):
            name, path, year, _ = self._data[i]
            row_y = y + (i - self._scroll_offset) * self._row_h
            cx = 20
            vals = [str(i + 1), name, f"{path:.0f}", str(year)]
            for j, (val, (_, cw)) in enumerate(zip(vals, cols)):
                clr = TXT if j != 0 else (120, 120, 180)
                surf.blit(rt(f_s, val, clr), (cx, row_y))
                cx += cw + 5

        self._cached_surf = surf

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)
        self._render()
        if self._cached_surf:
            surface.blit(self._cached_surf, self.bg_rect.topleft)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        title = rt(f_m, "台风路径长度", TXT)
        surface.blit(title, (bx + 12, by + 8))

        # 排序按钮（仅两个）
        modes = [("按路径长度", 0), ("按台风顺序", 1)]
        btn_x = bx + 200
        self._btn_rects = {}
        for label, mode in modes:
            r = pygame.Rect(btn_x, by + 10, 110, 22)
            self.draw_button(surface, r, rt(f_s, label, (255, 255, 255)),
                             style='primary' if self._sort_mode == mode else 'light',
                             enabled=True)
            self._btn_rects[label] = r
            btn_x += 118

        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # 滚动条
        self._draw_scrollbar(surface)

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        # 先检查按钮点击（避免被 handle_drag_event 拦截）
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
            for label, r in self._btn_rects.items():
                if r.collidepoint(e.pos):
                    if label == "按路径长度":
                        self._sort_mode = 0
                    else:
                        self._sort_mode = 1
                    self._sort()
                    self._scroll_offset = 0
                    self._cached_surf = None
                    return True
            # 检查滚动条拖拽
            if hasattr(self, '_scrollbar_rect') and self._scrollbar_rect.collidepoint(e.pos):
                self._scrollbar_dragging = True
                return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if hasattr(self, '_scrollbar_dragging') and self._scrollbar_dragging:
                self._scrollbar_dragging = False
                return True
        if e.type == pygame.MOUSEMOTION:
            if hasattr(self, '_scrollbar_dragging') and self._scrollbar_dragging:
                self._scroll_to_mouse(e.pos[1])
                return True
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_DOWN:
                self._scroll_offset = min(
                    self._scroll_offset + 1,
                    max(0, len(self._data) - self._visible_rows))
                self._cached_surf = None
                return True
            if e.key == pygame.K_UP:
                self._scroll_offset = max(0, self._scroll_offset - 1)
                self._cached_surf = None
                return True
        if e.type == pygame.MOUSEWHEEL:
            self._scroll_offset = max(0, min(
                self._scroll_offset - e.y,
                max(0, len(self._data) - self._visible_rows)))
            self._cached_surf = None
            return True
        return False

    def _draw_scrollbar(self, surface):
        """绘制垂直滚动条"""
        total = len(self._data)
        if total <= self._visible_rows:
            self._scrollbar_rect = pygame.Rect(0, 0, 0, 0)
            return
        bw = self.bg_rect.width
        # 滚动条轨道
        track_x = self.bg_rect.x + bw - 16
        track_top = self.bg_rect.y + 90
        track_h = self._visible_rows * self._row_h
        track_rect = pygame.Rect(track_x, track_top, 10, track_h)
        pygame.draw.rect(surface, (200, 210, 220), track_rect, 0, 5)
        # 滑块
        thumb_h = max(20, track_h * self._visible_rows / total)
        avail = track_h - thumb_h
        ratio = self._scroll_offset / (total - self._visible_rows) if total > self._visible_rows else 0
        thumb_y = track_top + int(ratio * avail)
        thumb_rect = pygame.Rect(track_x, thumb_y, 10, thumb_h)
        pygame.draw.rect(surface, (100, 140, 180), thumb_rect, 0, 5)
        self._scrollbar_rect = thumb_rect

    def deactivate(self):
        super().deactivate()
        self._cached_surf = None
        self._scrollbar_dragging = False

    def _scroll_to_mouse(self, mouse_y: int):
        """根据鼠标Y坐标计算滚动偏移"""
        if not self._scrollbar_rect or self._scrollbar_rect.height <= 0:
            return
        total = len(self._data)
        if total <= self._visible_rows:
            return
        # 滚动条轨道
        track_top = self.bg_rect.y + 90
        track_h = self._visible_rows * self._row_h
        # 滑块高度
        thumb_h = max(20, track_h * self._visible_rows / total)
        # 可用滑动范围
        avail = track_h - thumb_h
        # 鼠标在轨道内的相对位置
        rel_y = max(0, min(avail, mouse_y - track_top - thumb_h // 2))
        ratio = rel_y / avail if avail > 0 else 0
        self._scroll_offset = int(ratio * (total - self._visible_rows))
        self._cached_surf = None
