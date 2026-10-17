# py/renderer.py
"""统一渲染器：组合所有绘制逻辑。"""
from __future__ import annotations

import logging
import math
import time
from typing import Tuple, TYPE_CHECKING

import pygame

from .constants import (
    BG, ERROR_BG, ERROR_BORDER, f_m, f_s, rt,
    FPS_GREEN, FPS_YELLOW, FPS_RED, ERROR_TIMEOUT_MS,
    TD, TS, STS, C1, C2, C3, C4, C5_L,
)
from .render_geom import clip_segment, normalize_chain, draw_snap_grid
# P1(2026-10-02): 抗锯齿共享底座(只读使用; 见 output/exploration/p1_map_aa.md)
from . import paint_aa
from .track_quality import CURVE_DEV_LIMIT_KM  # noqa: F401  (面板已去掉; 常量留着备用)

if TYPE_CHECKING:
    from .ty_sim import TySim

#: P1 起 renderer 也要记一条降级日志(海洋区边界的 AA 失败时回落逐段绘制)
logger = logging.getLogger(__name__)


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
        self._stats_cache: dict = {}

    def draw(self, surface: pygame.Surface) -> None:
        sim = self.sim
        hidden = getattr(sim, '_ui_hidden', False)
        self._draw_scene(surface, hidden)
        # hidden 时 draw_control_panel 内部只画面板底色(与地图区分)
        sim.draw_control_panel(surface)
        # 需求 3.6：脚本时间轴**故意压住**右 1/4 功能栏 ⇒ 必须画在功能栏之后。
        # （面板本体在场景图层里，会正常被功能栏挡住；只有这条时间轴要"盖上去"。）
        _sp = getattr(sim, 'script_panel', None)
        if _sp is not None and not hidden:
            _sp.draw_overlay(surface)
        if not hidden:
            sim.dialog_mgr.draw(surface)

            if getattr(sim.cfg, 'show_fps', False):
                self._draw_fps(surface)
            # 用户 2026-10-01: 「路径质量面板」整项去掉(莫名其妙) —— 调用点与
            # `_draw_track_stats()` 实现一并删除, 设置里的开关也已移除。

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
                # 归一化顶点(单一代数): 相邻顶点统一到同一地图副本, 跨 0°/360°
                # 缝不再横穿屏幕; 绘制走 render_geom 的裁剪, 屏幕外部分自动截断
                try:
                    pts = [(int(x), int(y))
                           for x, y in (sim.latlon_to_screen(v[0], v[1])
                                        for v in verts)]
                except Exception:
                    continue
                wrap = 0.0
                mv = getattr(getattr(sim, 'map_mgr', None), 'map_view', None)
                if mv is not None:
                    try:
                        wrap = float(mv.wrap_px())
                    except Exception:
                        wrap = 0.0
                if wrap > 0 and len(pts) >= 2:
                    pts = [(int(x), int(y)) for x, y in normalize_chain(pts, wrap)]
                n = len(pts)
                # ⚠ P1(2026-10-02) 修订: 这里画的是**任意方向的 2px 边界线**
                #   (海洋区多边形的边), 属于"需要 AA"的类别。
                #   一度决定"不改"——因为 `paint_aa.aa_line` 按**单段包围盒**做 4×
                #   超采样, 一条横跨全屏的段会分配 ≈252 MB, 而这里是每帧 O(顶点数) 段。
                #   **现在改成复用 P1 的分块超采样**(`_draw_segments_aa`, 见
                #   `output/exploration/p1_map_aa.md` §3.1):
                #     · 峰值内存 = 单块(这里传 max_ss_pixels=1M ⇒ 4 MB), 与包围盒无关;
                #     · 每帧成本与总边长成正比、块数 = ceil(屏/tile), 全屏 ≈ 12 块;
                #     · 该层只在 `cfg.show_ocean_areas=True` 时绘制(默认 False),
                #       所以这笔开销不在默认路径上。
                #   复用而不是在本文件复制一份超采样逻辑(纪律要求: 共享底座只有一份)。
                try:
                    from .ty_sim_mixins._draw_path_mixin import TySimDrawPathMixin
                    segs = []
                    for i in range(n):
                        p0, p1 = pts[i], pts[(i + 1) % n]
                        seg = clip_segment(p0, p1, sw, sh)
                        if seg is None:
                            continue
                        segs.append(((int(seg[0][0]), int(seg[0][1])),
                                     (int(seg[1][0]), int(seg[1][1])), blue))
                    TySimDrawPathMixin._draw_segments_aa(
                        surface, segs, 2, max_ss_pixels=1_000_000)
                except Exception:
                    logger.debug('海洋区边界 AA 绘制失败, 回落逐段', exc_info=True)
                    for i in range(n):
                        p0, p1 = pts[i], pts[(i + 1) % n]
                        seg = clip_segment(p0, p1, sw, sh)
                        if seg is None:
                            continue
                        pygame.draw.line(surface, blue,
                                         (int(seg[0][0]), int(seg[0][1])),
                                         (int(seg[1][0]), int(seg[1][1])), 2)
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
                            # P1: 任意方向的多边形 ⇒ 需要 AA(原来是 pygame.draw.polygon)。
                            # 薄条填充的顶点已按视口过滤, 包围盒 = 该区在屏内的范围;
                            # `paint_aa.aa_poly` 按包围盒建一张超采样面, 每次一处,
                            # 且本函数只在 show_ocean_areas=True 时跑 ⇒ 代价可接受。
                            paint_aa.aa_poly(surface, (30, 95, 185), fill_pts)
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

    # 场景图层表: 顺序即 z 序。
    # 原先 z 序靠 _draw_scene 里 if/elif 的书写顺序隐式维持, 加一层要在多处插;
    # 现在顺序是一份可断言的数据(见 tests/test_render_layers.py), 各层自带可见性判定。
    def scene_layers(self, hidden: bool = False):
        sim = self.sim
        is_sim = getattr(sim, 'md', None) == getattr(sim, 'MODE_SIM', 'sim')
        md = getattr(sim, 'md', None)
        oe = getattr(sim, 'ocean_edit', None)

        def _clock(surface) -> None:
            # 时间轴(圆环钟): 风季在左上角, 正常/编辑在左下角(功能栏上方)
            if md == sim.MODE_SEASON:
                sim.draw_season_clock(surface)
                sim._ms.draw(surface)
            elif md in (sim.MODE_NORMAL, sim.MODE_EDIT):
                clock_y = max(0, sim.map_height - 250 - 8)
                # 必须显式传时间: 不传会落到风季控制器的 sy/st/ste(这两模式没人
                # 更新 → 钟恒停在初始日期, 用户报"时间轴坏了")。见 _active_clock_tuple。
                # 注意: 测试/工具用的轻量 sim 桩没有这个方法, 缺了就退回旧行为。
                _tt = None
                _mk = getattr(sim, '_active_clock_tuple', None)
                if callable(_mk):
                    _tt = _mk()
                sim.draw_season_clock(surface, origin=(0, clock_y),
                                      time_tuple=_tt)

        def _track_label(surface) -> None:
            label = getattr(sim, 'tracking_label', lambda: "")()
            if not label:
                return
            ts = rt(f_s, label, (255, 230, 120))
            tb = rt(f_s, label, (0, 0, 0))
            for ox, oy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
                           (1, -1), (1, 0), (1, 1)):
                surface.blit(tb, (12 + ox, 12 + oy))
            surface.blit(ts, (12, 12))

        def _typhoon_info(surface) -> None:
            # ⚠ 2026-10-12 用户: "打开设置/台风列表/脚本界面都会导致背景的台风图标消失,
            #   当路径等东西还在。台风图标不要消失"。
            #   根因: 这里原来把 `not dialog_open` 当成**整条共享路径**的开关, 而
            #   `draw_typhoon_info()` 同时画【图标】【名字】【信息框】⇒ 图标被顺带跳过;
            #   路径是上面 `typhoons` 层画的, 所以路径还在 ⇒ 现象正好是"只剩路径"。
            #   修法: 对话框打开时**只免掉信息框**(它会与对话框抢地方),
            #   图标与名字照画 ⇒ 现在传 `with_box=not dialog_open`。
            dialog_open = sim.dialog_mgr.any_active()
            with_box = not dialog_open
            if md == sim.MODE_NORMAL:
                ty = sim.current_typhoon()
                if ty and getattr(ty.v, 'icon_alpha', 255) > 0:
                    sim.draw_typhoon_info(surface, ty, with_box=with_box)
            elif md == sim.MODE_SEASON:
                for ty in sim.tys:
                    if ty.act and ty.ss and not ty.sf:
                        sim.draw_typhoon_info(surface, ty, with_box=with_box)
            elif md == sim.MODE_EDIT:
                ty = sim.edit_typhoon
                if ty and getattr(ty.v, 'icon_alpha', 255) > 0:
                    sim.draw_typhoon_info(surface, ty, with_box=with_box)

        def _effects(surface) -> None:
            ct = pygame.time.get_ticks()
            for eff in sim.effects:
                if hasattr(eff, '_map_height'):
                    eff._map_height = sim.map_height
                    eff._dark_mode = getattr(sim, 'dark_mode', True)
                eff.draw(surface, ct)

        return (
            ('map', lambda: True, lambda s: sim._draw_map(s)),
            # 经纬网: **模拟模式也画**(轮次 155 起)。原来这里是 `not is_sim`,
            # 于是模拟模式的 `show_graticule` 完全无效。
            # ⚠ 必须排在 `sim_field` **之前** —— 模拟模式的所有内容(环境色阶层/
            # 路径/图标/风圈)由 `_sim_render` 一次画完, 排在它后面会把网格线压在
            # 台风图标与路径之上。风季模式没有 sim_field 层, 顺序不受影响。
            ('graticule',
             lambda: getattr(sim.cfg, 'show_graticule', False),
             self._draw_graticule),
            # 模拟模式: 地图底图之上只叠 SimCore 图层(F1 隐藏时不画)
            ('sim_field', lambda: is_sim and not hidden,
             lambda s: sim._sim_render(s)),
            ('ocean_areas',
             lambda: not is_sim and (getattr(sim.cfg, 'show_ocean_areas', False)
                                     or bool(oe is not None and oe.active)),
             self._draw_ocean_areas),
            ('ocean_edit', lambda: not is_sim and bool(oe is not None and oe.active),
             lambda s: oe.draw(s)),
            ('clock', lambda: not is_sim and not hidden, _clock),
            ('ace_bar',
             # 模拟模式也用同一套 ACE 条(2026-09-19 用户: "ace 条像其它模式一样显示")
             lambda: not hidden and getattr(sim, 'show_ace_bar', True),
             lambda s: sim.draw_ace_display(s)),
            ('typhoons', lambda: not is_sim, lambda s: sim._draw_typhoons(s)),
            ('track_label',
             lambda: not is_sim and not hidden
             and getattr(sim.cfg, 'show_track_label', True),
             _track_label),
            # 季节信息框画在路径之后, 保证文字不被路径线遮挡
            ('season_info_boxes',
             lambda: (not is_sim and not hidden and md == sim.MODE_SEASON
                      and getattr(sim, 'show_info_box_season', True)),
             lambda s: sim.draw_season_info_boxes(s)),
            ('typhoon_info', lambda: not is_sim and not hidden, _typhoon_info),
            ('offscreen_boxes',
             # 用户 2026-10-12 需求 1：屏幕边缘的"域外风暴"小窗。
             # 排在 typhoon_info **之后** ⇒ 优先级低（它自己再按被覆盖情况降到 20%）。
             lambda: (not is_sim and not hidden
                      and getattr(sim.cfg, 'show_offscreen_boxes', True)
                      and getattr(sim, 'offscreen', None) is not None),
             lambda s: sim.offscreen.draw(s)),
            ('effects', lambda: not is_sim and not hidden, _effects),
            # 需求 3.1（用户 2026-10-13）：右侧脚本可视化编辑面板。
            # 排在**最后** ⇒ 盖住地图/图标/域外小窗；但它自己只画到
            # （功能栏上方），时间轴那条贴在窗口底、
            # **故意压住**右 1/4 功能栏（用户 3.6 原话"覆盖功能栏"）。
            # 需求 3.5：target 黄框 + 框外黑遮罩（在面板之下、台风图标之上）
            ('script_target_box',
             lambda: (not hidden and getattr(sim, 'script_target_box', None) is not None
                      and sim.script_target_box.active),
             lambda s: sim.script_target_box.draw(s)),
            ('script_panel',
             lambda: (not hidden and getattr(sim, 'script_panel', None) is not None
                      and sim.script_panel.active),
             lambda s: sim.script_panel.draw(s)),
        )

    def _layer_drawn(self, name: str, elapsed_ms: float = 0.0) -> None:
        """图层绘制完成钩子: 性能面板/测试可覆写(名字 + 单层耗时)。

        默认空实现; _draw_scene 每帧对每个可见层调用一次。"""
        return None

    def _draw_scene(self, surface: pygame.Surface, hidden: bool = False) -> None:
        surface.fill(BG)
        perf = time.perf_counter
        for name, visible, draw in self.scene_layers(hidden):
            if visible():
                t0 = perf()
                draw(surface)
                self._layer_drawn(name, (perf() - t0) * 1000.0)

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

    @staticmethod
    def _render_stats_panel(rows) -> pygame.Surface:
        surfs = [rt(f_s, text, color) for text, color in rows]
        w = max(s.get_width() for s in surfs) + 16
        h = sum(s.get_height() for s in surfs) + 12
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        pygame.draw.rect(panel, (150, 170, 200, 120), panel.get_rect(), 1,
                         border_radius=4)
        y = 6
        for s in surfs:
            panel.blit(s, (8, y))
            y += s.get_height()
        return panel
