# py/statistics/path_comparison.py
"""多台风路径对比对话框。右侧选择栏，复选框管理台风可见性。"""
import pygame
from typing import List, Optional, Tuple, Set

from ..constants import f_s, f_m, rt, TXT, BUTTON_BORDER, DIALOG_TITLE_BAR_HEIGHT
from ..dialog_base import DraggableDialog


# ── 侧栏常量 ──
SIDEBAR_WIDTH = 240
SIDEBAR_GAP = 12
CHECKBOX_SIZE = 14
ROW_HEIGHT = 22
SIDEBAR_PAD_TOP = 50
SIDEBAR_PAD_BOTTOM = 20

# ── 12 种可区分颜色 ──
COLORS = [
    (220, 60, 60), (60, 160, 60), (60, 60, 220),
    (220, 160, 0), (160, 60, 160), (60, 180, 180),
    (220, 100, 50), (100, 200, 100), (100, 100, 255),
    (200, 200, 50), (200, 50, 200), (50, 200, 200),
]


class PathComparisonDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._tys: list = []
        self._year: int = 0
        self._selected: Set[int] = set()          # 选中台风在 _tys 中的索引
        self._cached_surf: Optional[pygame.Surface] = None
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)

        # 侧栏交互状态
        self._sidebar_scroll = 0
        self._sidebar_rows_visible = 0
        self._sidebar_rect = pygame.Rect(0, 0, 0, 0)
        self._sidebar_content_rect = pygame.Rect(0, 0, 0, 0)
        self._checkbox_rects: List[pygame.Rect] = []
        self._select_all_btn = pygame.Rect(0, 0, 0, 0)
        self._deselect_all_btn = pygame.Rect(0, 0, 0, 0)
        # 框选状态
        self._box_selecting = False
        self._box_start = (0, 0)
        self._box_current = (0, 0)

    # ═══════════════════════════════════════════════
    #  激活 / 数据构建
    # ═══════════════════════════════════════════════
    def activate(self):
        super().activate()
        self._build()
        self._sidebar_scroll = 0

    def _build(self):
        year = self.sim.current_ace_year
        engine = self.sim.ace_engine
        self._year = year
        # 按台风列表排序收集台风
        basin_order = self._build_basin_order()

        all_tys = []
        for ty in self.sim.tys:
            if any(p.get('ace_year', 0) == year
                   and engine.point_in_limit(p['la'], p['lo'])
                   for p in ty.pts):
                all_tys.append(ty)

        # 排序：复刻台风列表
        def _sort_key(ty):
            basin_idx = basin_order.get(ty.basin, 9999)
            first_time = ty.pts[0]['t'] if ty.pts else "99999999"
            return (basin_idx, first_time, self.sim.get_display_name(ty).lower())

        all_tys.sort(key=_sort_key)
        self._tys = all_tys
        self._selected = set(range(len(self._tys)))  # 默认全选

        # 初始化布局
        w, h = 1400, 900
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2,
            (self.sim.screen_height - h) // 2, w, h)
        self._cached_surf = None
        self._checkbox_rects = []

    def _build_basin_order(self) -> dict:
        areas = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if areas and areas.areas:
            return {a.code: i for i, a in enumerate(areas.areas)}
        return {}

    # ═══════════════════════════════════════════════
    #  渲染
    # ═══════════════════════════════════════════════
    def _render(self):
        if self._cached_surf is not None:
            return

        # ── Phase 1: 计算地理范围 ──
        engine = self.sim.ace_engine
        all_lons, all_lats = [], []
        for idx, ty in enumerate(self._tys):
            if idx not in self._selected:
                continue
            for p in ty.pts:
                if p.get('ace_year', 0) == self._year and engine.point_in_limit(p['la'], p['lo']):
                    all_lons.append(p['lo'])
                    all_lats.append(p['la'])
        if not all_lons:
            w, h = self.bg_rect.width, self.bg_rect.height
            self._cached_surf = pygame.Surface((w, h), pygame.SRCALPHA)
            return

        margin = 5.0
        mlon, Mlon = min(all_lons) - margin, max(all_lons) + margin
        mlat, Mlat = min(all_lats) - margin, max(all_lats) + margin
        if Mlon - mlon < 2:
            Mlon = mlon + 2
        if Mlat - mlat < 2:
            Mlat = mlat + 2

        # ── Phase 2: 动态调整高度 ──
        bx, by = 60, 50                     # 数据区左上角（相对 bg_rect）
        bw = self.bg_rect.width - bx - SIDEBAR_WIDTH - SIDEBAR_GAP - 20
        geo_ratio = (Mlon - mlon) / max(0.1, Mlat - mlat)
        bh = int(bw / geo_ratio)
        bh = max(100, min(2000, bh))

        new_h = bh + by + 80
        if new_h != self.bg_rect.height:
            self.bg_rect.height = new_h
            self.bg_rect.centery = self.sim.screen_height // 2

        w = self.bg_rect.width
        surf = pygame.Surface((w, new_h), pygame.SRCALPHA)

        # ── 底图 ──
        try:
            orig = self.sim.map_mgr.map_view.original_img
            iw, ih = orig.get_size()
            ix1 = int((mlon - 0) / 360 * iw)
            ix2 = int((Mlon - 0) / 360 * iw)
            iy1 = int((90 - Mlat) / 180 * ih)
            iy2 = int((90 - mlat) / 180 * ih)
            ix1, ix2 = max(0, min(ix1, iw)), max(0, min(ix2, iw))
            iy1, iy2 = max(0, min(iy1, ih)), max(0, min(iy2, ih))
            if ix2 > ix1 and iy2 > iy1:
                sub = orig.subsurface(pygame.Rect(ix1, iy1, ix2 - ix1, iy2 - iy1))
                scaled = pygame.transform.smoothscale(sub, (bw, bh))
                scaled.set_alpha(200)
                surf.blit(scaled, (bx, by))
        except Exception:
            pass

        # ── Phase 3: 坐标映射与路径绘制 ──
        def geo_to_local(lon, lat):
            x = bx + (lon - mlon) / (Mlon - mlon) * bw
            y = by + (Mlat - lat) / (Mlat - mlat) * bh
            return int(x), int(y)

        # 洋区边框
        rect_color = (100, 180, 255)
        lx1, ly1 = geo_to_local(mlon, Mlat)
        lx2, ly2 = geo_to_local(Mlon, mlat)
        pygame.draw.rect(surf, rect_color,
                         (lx1, ly1, lx2 - lx1, ly2 - ly1), 2)

        for idx, ty in enumerate(self._tys):
            if idx not in self._selected:
                continue
            color = COLORS[idx % len(COLORS)]
            pts = [p for p in ty.pts
                   if p.get('ace_year', 0) == self._year
                   and engine.point_in_limit(p['la'], p['lo'])]
            if len(pts) < 1:
                continue
            screen_pts = [geo_to_local(p['lo'], p['la']) for p in pts]
            if len(screen_pts) > 1:
                pygame.draw.lines(surf, color, False, screen_pts, 2)
            for sx, sy in screen_pts:
                pygame.draw.circle(surf, color, (sx, sy), 3)
            if screen_pts:
                name = self.sim.get_display_name(ty)
                ns = rt(f_s, name, color)
                surf.blit(ns, (screen_pts[0][0] + 5, screen_pts[0][1] - 8))

        self._cached_surf = surf

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════
    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)
        self._render()
        if self._cached_surf:
            surface.blit(self._cached_surf, self.bg_rect.topleft)

        bx, by = self.bg_rect.x, self.bg_rect.y
        bw = self.bg_rect.width
        bh = self.bg_rect.height

        # 标题
        title = rt(f_m, f"路径对比 — {self._year}", TXT)
        surface.blit(title, (bx + 12, by + 8))

        # 关闭按钮
        cb = pygame.Rect(bx + bw - 90, by + 8, 55, 25)
        self._close_btn_rect = cb
        self.draw_button(surface, cb, rt(f_s, "关闭", (255, 255, 255)))

        # ══ 右侧选择栏 ══
        self._draw_sidebar(surface, bx, by, bw, bh)

        # ══ 框选矩形 ══
        if self._box_selecting:
            rx = min(self._box_start[0], self._box_current[0])
            ry = min(self._box_start[1], self._box_current[1])
            rw = abs(self._box_current[0] - self._box_start[0])
            rh = abs(self._box_current[1] - self._box_start[1])
            box_surf = pygame.Surface((rw, rh), pygame.SRCALPHA)
            box_surf.fill((100, 180, 255, 60))
            pygame.draw.rect(box_surf, (100, 180, 255), (0, 0, rw, rh), 1)
            surface.blit(box_surf, (rx, ry))

    def _draw_sidebar(self, surface, bx, by, bw, bh):
        """绘制右侧选择栏"""
        sx = bx + bw - SIDEBAR_WIDTH - 10
        sy = by + 40
        sh = bh - 80
        self._sidebar_rect = pygame.Rect(sx, sy, SIDEBAR_WIDTH, sh)

        # 侧栏背景
        sidebar_bg = pygame.Surface((SIDEBAR_WIDTH, sh), pygame.SRCALPHA)
        sidebar_bg.fill((230, 235, 245, 180))
        pygame.draw.rect(sidebar_bg, BUTTON_BORDER, (0, 0, SIDEBAR_WIDTH, sh), 1, 6)
        surface.blit(sidebar_bg, (sx, sy))

        # 内容区域
        content_x = sx + 8
        content_w = SIDEBAR_WIDTH - 16
        content_top = sy + 8

        # 全选 / 全不选 按钮
        btn_w, btn_h = 55, 22
        self._select_all_btn = pygame.Rect(content_x, content_top, btn_w, btn_h)
        self._deselect_all_btn = pygame.Rect(content_x + btn_w + 8, content_top, btn_w, btn_h)
        self.draw_button(surface, self._select_all_btn, rt(f_s, "全选", (255, 255, 255)),
                         style='primary' if len(self._selected) == len(self._tys) else 'light')
        self.draw_button(surface, self._deselect_all_btn, rt(f_s, "全不选", (255, 255, 255)),
                         style='primary' if len(self._selected) == 0 else 'light')

        # 复选框列表
        list_top = content_top + btn_h + 10
        list_h = sh - (list_top - sy) - 8
        visible_rows = max(1, list_h // ROW_HEIGHT)
        self._sidebar_rows_visible = visible_rows
        self._sidebar_content_rect = pygame.Rect(content_x, list_top, content_w, list_h)

        total = len(self._tys)
        max_scroll = max(0, total - visible_rows)
        self._sidebar_scroll = max(0, min(self._sidebar_scroll, max_scroll))

        self._checkbox_rects = []
        for i in range(self._sidebar_scroll, min(self._sidebar_scroll + visible_rows, total)):
            row_y = list_top + (i - self._sidebar_scroll) * ROW_HEIGHT
            ty = self._tys[i]
            name = self.sim.get_display_name(ty)

            # 复选框
            cb_rect = pygame.Rect(content_x, row_y + 3, CHECKBOX_SIZE, CHECKBOX_SIZE)
            self._checkbox_rects.append((i, cb_rect))
            # 绘制复选框
            if i in self._selected:
                pygame.draw.rect(surface, (100, 180, 100), cb_rect, 0, 2)
                # 勾号
                check_color = (255, 255, 255)
                pygame.draw.line(surface, check_color,
                                 (cb_rect.x + 2, cb_rect.centery),
                                 (cb_rect.centerx, cb_rect.bottom - 2), 2)
                pygame.draw.line(surface, check_color,
                                 (cb_rect.centerx, cb_rect.bottom - 2),
                                 (cb_rect.right - 1, cb_rect.top + 3), 2)
            else:
                pygame.draw.rect(surface, (180, 190, 200), cb_rect, 0, 2)
                pygame.draw.rect(surface, BUTTON_BORDER, cb_rect, 1, 2)

            # 名称（用台风对应颜色）
            color_idx = i % len(COLORS)
            txt = rt(f_s, name, COLORS[color_idx])
            surface.blit(txt, (cb_rect.right + 6, row_y + 1))

        # 侧栏滚动条
        if total > visible_rows:
            self._draw_sidebar_scrollbar(surface, content_x + content_w - 8,
                                         list_top, list_h, total, visible_rows)

    def _draw_sidebar_scrollbar(self, surface, track_x, track_top, track_h,
                                 total, visible):
        track_rect = pygame.Rect(track_x, track_top, 6, track_h)
        pygame.draw.rect(surface, (200, 210, 220), track_rect, 0, 3)
        thumb_h = max(16, track_h * visible / total)
        avail = track_h - thumb_h
        ratio = self._sidebar_scroll / (total - visible) if total > visible else 0
        thumb_y = track_top + int(ratio * avail)
        pygame.draw.rect(surface, (120, 150, 190),
                         (track_x, thumb_y, 6, thumb_h), 0, 3)

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # 关闭按钮
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True

            # 全选 / 全不选
            if self._select_all_btn.collidepoint(e.pos):
                self._selected = set(range(len(self._tys)))
                self._cached_surf = None
                return True
            if self._deselect_all_btn.collidepoint(e.pos):
                self._selected.clear()
                self._cached_surf = None
                return True

            # 复选框点击
            for idx, cb_rect in self._checkbox_rects:
                if cb_rect.collidepoint(e.pos):
                    if idx in self._selected:
                        self._selected.discard(idx)
                    else:
                        self._selected.add(idx)
                    self._cached_surf = None
                    return True

            # 开始在侧栏内容区框选
            if self._sidebar_content_rect.collidepoint(e.pos):
                # 检查是否点击在空白区域（非复选框非按钮）
                hit_checkbox = any(cb.collidepoint(e.pos) for _, cb in self._checkbox_rects)
                if not hit_checkbox:
                    self._box_selecting = True
                    self._box_start = e.pos
                    self._box_current = e.pos
                    return True

        # 框选移动
        if e.type == pygame.MOUSEMOTION and self._box_selecting:
            self._box_current = e.pos
            return True

        # 框选结束
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self._box_selecting:
            self._box_selecting = False
            self._apply_box_select()
            return True

        # 侧栏滚轮
        if e.type == pygame.MOUSEWHEEL:
            if self._sidebar_rect.collidepoint(pygame.mouse.get_pos()):
                total = len(self._tys)
                self._sidebar_scroll = max(0, min(
                    self._sidebar_scroll - e.y,
                    max(0, total - self._sidebar_rows_visible)))
                return True

        # 拖拽
        if self.handle_drag_event(e):
            return True

        # ESC
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True

        return False

    def _apply_box_select(self):
        """根据框选矩形切换台风选中状态"""
        if not self._sidebar_content_rect:
            return
        rx = min(self._box_start[0], self._box_current[0])
        ry = min(self._box_start[1], self._box_current[1])
        rw = abs(self._box_current[0] - self._box_start[0])
        rh = abs(self._box_current[1] - self._box_start[1])
        box_rect = pygame.Rect(rx, ry, rw, rh)

        # 仅当框选有实际面积时
        if rw < 4 and rh < 4:
            return

        changed = False
        for idx, cb_rect in self._checkbox_rects:
            if box_rect.colliderect(cb_rect):
                if idx not in self._selected:
                    self._selected.add(idx)
                    changed = True
            else:
                # 框选之外的不变（保持原选中状态）
                pass

        if changed:
            self._cached_surf = None

    def deactivate(self):
        super().deactivate()
        self._cached_surf = None
        self._box_selecting = False
        self._checkbox_rects = []
