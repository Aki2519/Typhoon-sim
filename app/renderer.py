# py/renderer.py
"""统一渲染器：组合所有绘制逻辑。"""
from __future__ import annotations

import pygame
from typing import Tuple, TYPE_CHECKING

from .constants import (
    BG, ERROR_BG, ERROR_BORDER, f_m, f_s, rt,
    FPS_GREEN, FPS_YELLOW, FPS_RED, ERROR_TIMEOUT_MS,
    TD, TS, STS, C1, C2, C3, C4, C5_L,
)

if TYPE_CHECKING:
    from .ty_sim import TySim


def _clip_seg_rect(surface, p0, p1, sw, sh, color, width) -> None:
    """把线段 p0→p1(可能越界/跨屏)按视口矩形截断后画出; 完全在视口外则跳过。"""
    x0, y0 = p0
    x1, y1 = p1
    # Liang-Barsky 裁剪
    dx = x1 - x0
    dy = y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - 0), (dx, sw - x0),
                 (-dy, y0 - 0), (dy, sh - y0)):
        if p == 0:
            if q < 0:
                return
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return
                if r < t1:
                    t1 = r
    if t0 > t1:
        return
    ax = int(x0 + t0 * dx)
    ay = int(y0 + t0 * dy)
    bx = int(x0 + t1 * dx)
    by = int(y0 + t1 * dy)
    pygame.draw.line(surface, color, (ax, ay), (bx, by), width)


class Renderer:
    """接受所有依赖，提供 draw() 入口。"""

    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self._fps_cache: dict = {}

    def draw(self, surface: pygame.Surface) -> None:
        sim = self.sim
        hidden = getattr(sim, '_ui_hidden', False)
        self._draw_scene(surface, hidden)
        # hidden 时 draw_control_panel 内部只画面板底色(与地图区分)
        sim.draw_control_panel(surface)
        if not hidden:
            sim.dialog_mgr.draw(surface)

            if getattr(sim.cfg, 'show_fps', False):
                self._draw_fps(surface)

            self._draw_toasts(surface)
            if getattr(sim.cfg, 'show_coord_hud', True):
                self._draw_coord_hud(surface)
            if getattr(sim.cfg, 'show_legend', False):
                self._draw_legend(surface)
            self._draw_script_status(surface)
        # hidden 模式下控制面板仍绘制底色,与地图区分(F1 截图模式)

    def _draw_ocean_areas(self, surface: pygame.Surface) -> None:
        """洋区边界 + 盆域标签(可选 overlay)。

        相邻顶点按“球面最近距离”连线：经度取环面最短差((lo2-lo1+180)%360-180)，
        目标点经度解包到与上一点同轴，避免跨 0°/180° 洋区(如 SA)横穿屏幕；
        纬度差 >90 视为异常跳段跳过。分界线加粗。"""
        sim = self.sim
        try:
            oa = getattr(getattr(sim, 'res_mgr', None), 'ocean_areas', None)
            if oa is None:
                return
            sw, sh = sim.screen_width, getattr(sim, 'map_height', 0)
            blue = (80, 140, 220)
            for area in getattr(oa, 'areas', None) or []:
                verts = getattr(area, 'vertices', None) or []
                if len(verts) < 3:
                    continue
                # 生成“解包后”的边界顶点（环面最短展开，跨缝连续）
                unwrapped = []
                prev_lo = None
                ok = True
                for v in verts:
                    la, lo = v[0], v[1]
                    if prev_lo is None:
                        unwrapped.append((la, lo))
                    else:
                        dlon = ((lo - prev_lo + 180.0) % 360.0) - 180.0
                        if abs(la - unwrapped[-1][0]) > 90.0:
                            ok = False
                            break
                        unwrapped.append((la, prev_lo + dlon))
                    prev_lo = unwrapped[-1][1]
                if not ok or not unwrapped:
                    continue
                # 投影所有解包顶点（经度可能超出 0..360，直映可正常取模）
                pts = []
                for la, lo in unwrapped:
                    try:
                        x, y = sim.latlon_to_screen(la, lo)
                        pts.append((int(x), int(y)))
                    except Exception:
                        pts.append(None)
                n = len(pts)
                # 边界: 逐段画线, 跳过解包后仍跨屏跳动的片段(正常不应出现)
                for i in range(n):
                    p0, p1 = pts[i], pts[(i + 1) % n]
                    if p0 is None or p1 is None:
                        continue
                    if abs(p1[0] - p0[0]) > sw * 0.7 or abs(p1[1] - p0[1]) > sh:
                        continue
                    pygame.draw.line(surface, blue, p0, p1, 2)
                # 薄区域填充: 非相邻边界距离 ≤ √2·0.1°(≈0.1414°) → 蓝色实心
                try:
                    import math as _math
                    thin = False
                    for i in range(n):
                        if pts[i] is None:
                            continue
                        vi = verts[i]
                        for j in range(i + 2, n):
                            if pts[j] is None:
                                continue
                            vj = verts[j]
                            if _math.hypot(vi[1] - vj[1], vi[0] - vj[0]) <= 0.1414:
                                thin = True
                                break
                        if thin:
                            break
                    if thin:
                        fill_pts = [p for p in pts if p is not None
                                    and 0 <= p[0] <= sw and 0 <= p[1] <= sh]
                        if len(fill_pts) >= 3:
                            pygame.draw.polygon(surface, (30, 95, 185), fill_pts, 0)
                except Exception:
                    pass
                # 标签(质心)
                if verts:
                    la = sum(v[0] for v in verts) / len(verts)
                    lo = sum(v[1] for v in verts) / len(verts)
                    try:
                        x, y = sim.latlon_to_screen(la, lo)
                        if 0 <= x <= sw and 0 <= y <= sh:
                            ts = rt(f_s, area.code, (120, 175, 225))
                            surface.blit(ts, (x, y))
                    except Exception:
                        pass
        except Exception:
            pass

    def _draw_graticule(self, surface: pygame.Surface) -> None:
        sim = self.sim
        try:
            mh = getattr(sim, 'map_height', 0)
            col = (190, 205, 225)
            for lon in range(0, 360, 10):
                x = sim.view.latlon_to_screen(0, lon)[0]
                if 0 <= x <= sim.screen_width:
                    pygame.draw.line(surface, col, (x, 0), (x, mh), 1)
            for lat in range(-90, 91, 10):
                y = sim.view.latlon_to_screen(lat, 180)[1]
                if 0 <= y <= mh:
                    pygame.draw.line(surface, col, (0, y), (sim.screen_width, y), 1)
        except Exception:
            pass

    def _draw_legend(self, surface: pygame.Surface) -> None:
        sim = self.sim
        entries = [('TD', TD), ('TS', TS), ('STS', STS), ('C1', C1),
                   ('C2', C2), ('C3', C3), ('C4', C4), ('C5', C5_L)]
        w = 90
        h = len(entries) * 18 + 16
        x = 12
        y = max(0, getattr(sim, 'map_height', 0) - h - 12)
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(box, (20, 26, 42, 190), (0, 0, w, h), 0, 6)
        pygame.draw.rect(box, (70, 90, 120), (0, 0, w, h), 1, 6)
        for i, (name, col) in enumerate(entries):
            yy = 8 + i * 18
            pygame.draw.rect(box, col, (8, yy, 14, 12), 0, 2)
            ts = rt(f_s, name, (230, 232, 240))
            box.blit(ts, (28, yy - 1))
        surface.blit(box, (x, y))

    def _draw_scene(self, surface: pygame.Surface, hidden: bool = False) -> None:
        sim = self.sim
        surface.fill(BG)
        # 模拟模式: 先画地图底图, 再叠加 SimCore 图层(不再全屏覆盖地图)
        if getattr(sim, 'md', None) == getattr(sim, 'MODE_SIM', 'sim'):
            sim._draw_map(surface)
            if not hidden:
                sim._sim_render(surface)
            return
        sim._draw_map(surface)

        # 经纬网格(可选, 地图之上、台风之下)
        if getattr(sim.cfg, 'show_graticule', False):
            self._draw_graticule(surface)
        if getattr(sim.cfg, 'show_ocean_areas', False) or getattr(sim, 'ocean_edit', None) and sim.ocean_edit.active:
            self._draw_ocean_areas(surface)

        # 洋区编辑模式: 顶点高亮 + 提示(仅该模式渲染)
        oe = getattr(sim, 'ocean_edit', None)
        if oe is not None and oe.active:
            oe.draw(surface)

        if not hidden:
            # 时间轴(圆环钟): 风季/模拟在左上角, 正常/编辑在左下角(功能栏上方)
            if sim.md == sim.MODE_SEASON:
                sim.draw_season_clock(surface)
                sim._ms.draw(surface)
            elif sim.md in (sim.MODE_NORMAL, sim.MODE_EDIT):
                clock_y = max(0, sim.map_height - 250 - 8)
                sim.draw_season_clock(surface, origin=(0, clock_y))

            if getattr(sim, 'show_ace_bar', True):
                sim.draw_ace_display(surface)

        sim._draw_typhoons(surface)

        # 镜头跟踪指示(左上角, F1 隐藏界面或设置中关闭开关后不显示)
        if not hidden and getattr(sim.cfg, 'show_track_label', True):
            track_label = getattr(sim, 'tracking_label', lambda: "")()
            if track_label:
                ts = rt(f_s, track_label, (255, 230, 120))
                tb = rt(f_s, track_label, (0, 0, 0))
                for ox, oy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                    surface.blit(tb, (12 + ox, 12 + oy))
                surface.blit(ts, (12, 12))

        if not hidden:
            # 季节信息框画在台风路径之后,保证信息框文字不被路径线遮挡
            # (与正常模式信息框 z 序一致)
            if sim.md == sim.MODE_SEASON:
                if sim.show_info_box_season:
                    sim.draw_season_info_boxes(surface)

        if hidden:
            return

        ct = pygame.time.get_ticks()

        dialog_open = sim.dialog_mgr.any_active()
        if sim.md == sim.MODE_NORMAL:
            ty = sim.current_typhoon()
            if ty and not dialog_open and getattr(ty.v, 'icon_alpha', 255) > 0:
                sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_SEASON:
            for ty in sim.tys:
                if ty.act and ty.ss and not ty.sf and not dialog_open:
                    sim.draw_typhoon_info(surface, ty)
        elif sim.md == sim.MODE_EDIT:
            ty = sim.edit_typhoon
            if ty and not dialog_open and getattr(ty.v, 'icon_alpha', 255) > 0:
                sim.draw_typhoon_info(surface, ty)

        for eff in sim.effects:
            if hasattr(eff, '_map_height'):
                eff._map_height = sim.map_height
                eff._dark_mode = getattr(sim, 'dark_mode', True)
            eff.draw(surface, ct)

    def _draw_error(self, surface: pygame.Surface) -> None:
        sim = self.sim
        err = rt(f_m, sim.error_message, (255, 255, 255))
        p = 10
        w, h = err.get_width() + 2 * p, err.get_height() + 2 * p
        x = (sim.screen_width - w) // 2
        bg_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(bg_surf, ERROR_BG + (220,), (0, 0, w, h), 0, 5)
        pygame.draw.rect(bg_surf, ERROR_BORDER + (220,), (0, 0, w, h), 2, 5)
        surface.blit(bg_surf, (x, 10))
        surface.blit(err, (x + p, 10 + p))

    _TOAST_COLORS = {
        'info': (70, 130, 220), 'success': (60, 190, 100),
        'warning': (235, 190, 60), 'error': (225, 90, 90),
    }

    def _draw_toasts(self, surface: pygame.Surface) -> None:
        sim = self.sim
        now = pygame.time.get_ticks()
        toasts = getattr(sim, '_toasts', None)
        if not toasts:
            return
        live = [t for t in toasts if t[2] > now]
        if len(live) != len(toasts):
            sim._toasts = live
        if not live:
            return
        y = 10
        for text, level, _ in live:
            col = self._TOAST_COLORS.get(level, self._TOAST_COLORS['info'])
            surf = rt(f_s, text, (255, 255, 255))
            p = 8
            w, h = surf.get_width() + 2 * p, surf.get_height() + 2 * p
            x = (sim.screen_width - w) // 2
            box = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(box, (22, 28, 44, 230), (0, 0, w, h), 0, 5)
            pygame.draw.rect(box, (*col, 255), (0, 0, w, h), 2, 5)
            surface.blit(box, (x, y))
            surface.blit(surf, (x + p, y + p))
            y += h + 4

    def _draw_coord_hud(self, surface: pygame.Surface) -> None:
        """鼠标经纬度读数(右下角)。地图区内且未隐藏 UI 时显示。"""
        sim = self.sim
        try:
            mx, my = pygame.mouse.get_pos()
            mh = getattr(sim, 'map_height', 0)
            if not (0 <= mx <= sim.screen_width and 0 <= my <= mh):
                return
            lat, lon = sim.view.screen_to_latlon(mx, my)
            # 经度: >180 → 360-x+W; <0 → -x+W; 其余 E
            if lon > 180.0:
                lon_s = f"{360.0 - lon:.1f}°W"
            elif lon < 0:
                lon_s = f"{-lon:.1f}°W"
            else:
                lon_s = f"{lon:.1f}°E"
            lat_s = f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}"
            # 0.1° 量化缓存,减少每帧重建
            q = (round(lat, 1), round(lon, 1), getattr(sim, 'dark_mode', True))
            cached = getattr(sim, '_coord_hud_cache', None)
            if cached is None or cached[0] != q:
                txt = f"{lon_s} {lat_s}"
                surf = rt(f_s, txt, (235, 235, 245))
                sim._coord_hud_cache = (q, surf)
            else:
                surf = cached[1]
            surface.blit(surf, (sim.screen_width - surf.get_width() - 12,
                                mh - surf.get_height() - 10))
        except Exception:
            pass

    def _draw_script_status(self, surface: pygame.Surface) -> None:
        """脚本运行状态条(左下角)。"""
        sim = self.sim
        se = getattr(sim, 'script_engine', None)
        if se is None:
            return
        try:
            txt = se.status_text()
        except Exception:
            return
        if not txt:
            return
        ts = rt(f_s, txt, (150, 220, 255))
        surface.blit(ts, (12, getattr(sim, 'map_height', 0) - 34))

    def _draw_fps(self, surface: pygame.Surface) -> None:
        fps = getattr(self.sim, '_fps', 60.0)
        if fps >= 60:
            color = FPS_GREEN
        elif fps >= 30:
            color = FPS_YELLOW
        else:
            color = FPS_RED
        tu = getattr(self.sim, '_t_update_ms', 0)
        td = getattr(self.sim, '_t_draw_ms', 0)
        # 按量化值缓存,避免每帧 rt() 重建
        key = (int(fps // 2), tu // 10, td // 10, color)
        fps_surf = self._fps_cache.get(key)
        if fps_surf is None:
            fps_surf = rt(f_s, f"FPS: {fps:.0f}  U:{tu}ms D:{td}ms", color)
            if len(self._fps_cache) > 32:
                self._fps_cache.pop(next(iter(self._fps_cache)))
            self._fps_cache[key] = fps_surf
        x = self.sim.screen_width - fps_surf.get_width() - 8
        surface.blit(fps_surf, (x, 8))
