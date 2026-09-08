"""洋区编辑模式：在主地图上编辑洋区多边形。

进入方式：设置 → ACE tab → 「洋区编辑」按钮（仅此入口）。
进入后 sim.md = MODE_OCEAN_EDIT，记录进入前的模式；退出时还原。

交互（主地图上）：
  - 左键 命中顶点 → 选中并开始拖动（按住左键拖动）
  - 左键 未命中顶点 → 在洋区边界附近插入新顶点（两顶点连线与点击处最近处）
  - 右键（未拖动）→ 删除选中/命中的顶点（不足 3 顶点拒删）
  - Ctrl+左键 空白 → 取消选中
  - 按 H 或再次点击「洋区编辑」按钮 → 退出到进入时的模式

保存：退出时若发生过编辑，写回 assets/Area_ocean.json（紧凑格式，
条目含 code/name_cn/name_full/h/ace/c（[lon, lat] 对，原格式保留）。
洋区顶点改动仅修改对应条目的 c，其余字段原样保留。
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import List, Optional, Tuple

import pygame

from .constants import (SUCAI_DIR, AREA_OCEAN_FILE, f_s, f_m, rt, TXT,
                        SETTINGS_TEXT_LIGHT)

logger = logging.getLogger(__name__)


class OceanEditMode:
    """洋区编辑模式控制器。由 TySim 持有(sim.ocean_edit)。

    状态:
      self.active        — 是否处于洋区编辑模式
      self._prev_md      — 进入前的 md(退出还原)
      self.selected_area — 当前编辑的 OceanArea
      self.drag_idx      — 正在拖动的顶点下标(-1 无)
      self._dirty        — 有未保存改动
      self._status       — 提示文本
    """

    HIT_RADIUS = 10          # 顶点点击命中半径(px)
    EDGE_HIT_FACTOR = 12     # 边界插入命中: 距边 ≤ 12px
    MAX_VERTICES = 200

    def __init__(self, sim) -> None:
        self.sim = sim
        self.active = False
        self._prev_md = None
        self.selected_area = None
        self.drag_idx = -1
        self._dirty = False
        self._status = ""
        self._hover_area = None

    # ── 进出模式 ──

    def enter(self) -> None:
        """进入洋区编辑模式(仅此入口)。重复进入无副作用。

        进入后自动选中第一个可编辑洋区(非半球/非合并), 并把地图视野
        聚焦到该洋区包围盒(带边距), 保证顶点可见可编辑。
        """
        sim = self.sim
        if self.active:
            return
        if sim.dialog_mgr.any_active():
            # 设置对话框还开着: 先关闭, 避免编辑模式与对话框同时捕获事件
            sim.dialog_mgr.deactivate_all()
        self._prev_md = sim.md
        sim.md = sim.MODE_OCEAN_EDIT
        self.active = True
        self.selected_area = None
        self.drag_idx = -1
        self._dirty = False
        self._status = ""
        # 记录进入前的地图视野(编辑模式会缩放到洋区 bbox, 退出时恢复)
        self._saved_view = None
        try:
            _mv = sim.map_mgr.map_view
            if _mv is not None:
                self._saved_view = (_mv.view_x, _mv.view_y, _mv.scale,
                                    getattr(_mv, '_bottom_align', False))
        except Exception:
            self._saved_view = None
        # 默认选中第一个可编辑洋区并聚焦视野
        self._select_first_area()
        self._set_status("洋区编辑模式: 点击顶点拖动 / 边界插入点 / 右键删除 / H 退出")

    def _editable_areas(self):
        oa = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if oa is None:
            return []
        return [a for a in oa.areas
                if not a._is_hemisphere and not getattr(a, 'is_merged', False)
                and len(a.vertices) >= 3]

    def _select_first_area(self) -> None:
        areas = self._editable_areas()
        if not areas:
            self._set_status("无可用洋区(数据缺失)")
            return
        self.selected_area = areas[0]
        self._focus_area(areas[0])
        self._set_status(f"已选中 {areas[0].code} {areas[0].name_cn} · H 退出")

    def _focus_area(self, area) -> None:
        """把地图视野调整到洋区包围盒, 保证全部顶点落在屏内。

        无论角落/范围模式配置, 直接调用 MapView.set_view_region 以范围方式
        缩放(经度/纬度都带边距), 保证洋区全部顶点可见; 不改变全局配置,
        退出编辑模式后由主程序按原模式恢复视图。
        经度按环面最小跨度(跨 0/180° 洋区正确)。
        """
        sim = self.sim
        try:
            lats = [v[0] for v in area.vertices]
            lons = [v[1] for v in area.vertices]
            la_min, la_max = min(lats), max(lats)
            # 经度环面最小跨度(跨 0/180° 洋区正确)
            sorted_lons = sorted(lons)
            n = len(sorted_lons)
            gaps = [(sorted_lons[(i + 1) % n] - sorted_lons[i]) % 360.0
                    for i in range(n)]
            gi = gaps.index(max(gaps))
            lon_min = sorted_lons[(gi + 1) % n]
            lon_span = 360.0 - max(gaps)
            m_lon = max(5.0, lon_span * 0.08)
            m_lat = max(5.0, (la_max - la_min) * 0.12)
            lo0 = lon_min - m_lon
            lo1 = lon_min + lon_span + m_lon
            la0 = max(-90.0, la_min - m_lat)
            la1 = min(90.0, la_max + m_lat)
            mv = sim.map_mgr.map_view
            if mv is not None:
                mv.set_view_region(lo0, lo1, la0, la1)
                mv._bottom_align = False
            sim.invalidate_screen_points_lazy()
        except Exception:
            pass

    def exit(self) -> None:
        """退出洋区编辑模式, 还原进入时的模式。

        保存失败时留在编辑模式(避免改动被静默丢弃)。"""
        if not self.active:
            return
        if not self._save_if_dirty():
            return
        sim = self.sim
        sim.md = self._prev_md if self._prev_md else sim.MODE_NORMAL
        self.active = False
        self.selected_area = None
        self.drag_idx = -1
        self._prev_md = None
        self._restore_view()
        sim.invalidate_screen_points_lazy()  # 恢复普通渲染

    def _restore_view(self) -> None:
        """恢复进入编辑模式前的地图视野(编辑模式把视野缩到了洋区 bbox)。"""
        sv = getattr(self, '_saved_view', None)
        self._saved_view = None
        if not sv:
            return
        mv = getattr(self.sim.map_mgr, 'map_view', None)
        if mv is None:
            return
        mv.view_x, mv.view_y, mv.scale, ba = sv
        # 编辑期间窗口可能改过大小: 恢复的 scale 必须仍满足 cover(min_scale),
        # 否则地图不再铺满屏幕, 且 geo_to_screen 的环绕阈值会因 draw_offset≠0 偏移
        mv.scale = max(mv.min_scale, min(mv.scale, 8.0))
        mv.view_x %= mv.img_w
        mv._clamp_view_y()
        mv._bottom_align = ba

    def toggle(self) -> bool:
        """切换模式。返回 True 表示已处理(设置按钮/H 键共用)。"""
        if self.active:
            self.exit()
        else:
            self.enter()
        return True

    # ── 状态/提示 ──

    def _set_status(self, msg: str) -> None:
        self._status = msg

    # ── 坐标辅助 ──

    def _geo_to_screen(self, la: float, lo: float) -> Tuple[int, int]:
        try:
            x, y = self.sim.latlon_to_screen(la, lo)
            return int(x), int(y)
        except Exception:
            return 0, 0

    def _screen_to_geo(self, x: int, y: int) -> Tuple[float, float]:
        try:
            la, lo = self.sim.screen_to_latlon(x, y)
            return float(la), float(lo)
        except Exception:
            return 0.0, 0.0

    def _clamp_lon(self, lon: float) -> float:
        """经度归一到 [0, 360)。"""
        return lon % 360.0

    # ── 顶点/面积操作 ──

    def _area_vertex_screen_pts(self, area) -> List[Optional[Tuple[int, int]]]:
        pts = []
        for la, lo in area.vertices:
            pts.append(self._geo_to_screen(la, lo))
        return pts

    def _hit_vertex(self, area, mx: int, my: int) -> int:
        """命中顶点: 返回下标(-1 无)。命中半径 HIT_RADIUS。"""
        pts = self._area_vertex_screen_pts(area)
        best, best_d = -1, self.HIT_RADIUS
        for i, p in enumerate(pts):
            if p is None:
                continue
            d = math.hypot(p[0] - mx, p[1] - my)
            if d <= best_d:
                best, best_d = i, d
        return best

    def _hit_edge(self, area, mx: int, my: int) -> int:
        """命中边界: 返回插入顶点应处的下标(点将插在该下标位置), -1 无。

        遍历每条边(闭合), 求点击点在线段上的投影距离;
        距离 ≤ EDGE_HIT_FACTOR 且投影在线段内 → 命中, 返回边的末端下标。
        跨屏大幅跳变的边(反经线)跳过。
        """
        pts = self._area_vertex_screen_pts(area)
        n = len(pts)
        sw, sh = self.sim.screen_width, getattr(self.sim, 'map_height', 0)
        best, best_d = -1, self.EDGE_HIT_FACTOR
        for i in range(n):
            p0, p1 = pts[i], pts[(i + 1) % n]
            if p0 is None or p1 is None:
                continue
            if abs(p1[0] - p0[0]) > sw * 0.7 or abs(p1[1] - p0[1]) > sh:
                continue  # 跨屏边跳过
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            ln2 = dx * dx + dy * dy
            if ln2 <= 0:
                continue
            t = ((mx - p0[0]) * dx + (my - p0[1]) * dy) / ln2
            t = max(0.0, min(1.0, t))
            px_, py_ = p0[0] + t * dx, p0[1] + t * dy
            d = math.hypot(px_ - mx, py_ - my)
            if d <= best_d:
                best, best_d = (i + 1) % n, d
        return best

    def _pick_area(self, mx: int, my: int):
        """命中顶点/边所在洋区(遍历全部)。返回 (area, mode, idx)。"""
        oa = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if oa is None:
            return None, None, -1
        for area in oa.areas:
            if area._is_hemisphere or len(area.vertices) < 3:
                continue
            vi = self._hit_vertex(area, mx, my)
            if vi >= 0:
                return area, 'vertex', vi
        for area in oa.areas:
            if area._is_hemisphere or len(area.vertices) < 3:
                continue
            ei = self._hit_edge(area, mx, my)
            if ei >= 0:
                return area, 'edge', ei
        return None, None, -1

    # ── 事件处理(主地图区域) ──

    def handle_event(self, e: pygame.event.Event) -> bool:
        """洋区编辑模式的事件入口。返回 True 表示已消费。"""
        if not self.active:
            return False
        sim = self.sim
        if e.type == pygame.KEYDOWN:
            # H 键退出(与设置按钮等效)
            if e.key == pygame.K_h:
                self.exit()
                return True
            return False
        if e.type == pygame.MOUSEBUTTONDOWN:
            mx, my = e.pos
            if my >= getattr(sim, 'map_height', 0):
                return False  # 面板区不管
            if e.button == 1:
                return self._mouse_down(mx, my)
            if e.button == 3:
                return self._mouse_right(mx, my)
            return False
        if e.type == pygame.MOUSEMOTION and self.drag_idx >= 0:
            if self.selected_area is not None:
                la, lo = self._screen_to_geo(*e.pos)
                la = max(-90.0, min(90.0, la))
                lo = self._clamp_lon(lo)
                # 与台风报点一致: 按 edit_snap_step(默认0.1°)吸附网格, Shift 临时取消
                try:
                    # 不能用 or 0.1: 0.0 是设置里"关闭吸附"的合法哨兵值
                    _raw = getattr(self.sim.cfg, 'edit_snap_step', 0.1)
                    step = 0.1 if _raw is None else float(_raw)
                    ctrl = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
                except Exception:
                    step, ctrl = 0.1, False
                if step > 0 and not ctrl:
                    la = round(la / step) * step
                    lo = round(lo / step) * step
                self.selected_area.vertices[self.drag_idx] = (la, lo)
                self.selected_area._preprocess()
                self._dirty = True
                sim.invalidate_screen_points_lazy()
                return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.drag_idx >= 0:
                self.drag_idx = -1
                return True
        return False

    def _mouse_down(self, mx: int, my: int) -> bool:
        """左键: 命中顶点选中拖动 / 命中边插入 / 空白取消选中。"""
        try:
            ctrl = bool(pygame.key.get_mods() & pygame.KMOD_CTRL)
        except pygame.error:
            ctrl = False
        if self.selected_area is not None:
            vi = self._hit_vertex(self.selected_area, mx, my)
            if vi >= 0:
                self.drag_idx = vi
                self._set_status(f"{self.selected_area.code} 顶点{vi} 拖动中")
                return True
            if not ctrl:
                ei = self._hit_edge(self.selected_area, mx, my)
                if ei >= 0:
                    return self._insert_vertex(self.selected_area, ei, mx, my)
        area, mode, idx = self._pick_area(mx, my)
        if area is not None:
            self.selected_area = area
            if mode == 'vertex':
                self.drag_idx = idx
                self._set_status(f"{area.code} 顶点{idx} 拖动中")
            else:
                return self._insert_vertex(area, idx, mx, my)
            return True
        if ctrl or self.selected_area is not None:
            self.selected_area = None
            self.drag_idx = -1
            self._set_status("")
        return True

    def _insert_vertex(self, area, idx: int, mx: int, my: int) -> bool:
        if len(area.vertices) >= self.MAX_VERTICES:
            self._set_status("顶点数已达上限")
            return True
        la, lo = self._screen_to_geo(mx, my)
        # 与台风报点一致: 按 edit_snap_step(默认0.1°)吸附网格
        try:
            _raw = getattr(self.sim.cfg, 'edit_snap_step', 0.1)
            step = 0.1 if _raw is None else float(_raw)   # 保留 0.0(关闭吸附)
        except Exception:
            step = 0.1
        if step > 0:
            la = round(la / step) * step
            lo = round(lo / step) * step
        # 新点经度与相邻点连续(插在边中段, 取与两侧最相近的角)
        prev_lon = area.vertices[(idx - 1) % len(area.vertices)][1]
        lon_shift = self._nearest_shift(lo, prev_lon)
        area.vertices.insert(idx, (max(-90.0, min(90.0, la)),
                                   self._clamp_lon(lo + lon_shift)))
        area._preprocess()
        self._dirty = True
        self._set_status(f"{area.code} 插入顶点{idx}")
        self.sim.invalidate_screen_points_lazy()
        return True

    @staticmethod
    def _nearest_shift(lon: float, ref: float) -> float:
        """把 lon 平移到离 ref 最近的等价经度(+360*k)。"""
        s = lon
        while s - ref > 180:
            s -= 360
        while s - ref < -180:
            s += 360
        return s - lon

    def _mouse_right(self, mx: int, my: int) -> bool:
        """右键(未拖动): 删除命中的顶点。少于 3 顶点拒删。"""
        if self.drag_idx >= 0:
            return False  # 拖动中交给拖拽逻辑
        area = self.selected_area
        if area is None:
            return True
        vi = self._hit_vertex(area, mx, my)
        if vi < 0:
            return True
        if len(area.vertices) <= 3:
            self._set_status("至少保留 3 个顶点")
            return True
        area.vertices.pop(vi)
        area._preprocess()
        self._dirty = True
        self._set_status(f"{area.code} 删除顶点{vi}")
        self.sim.invalidate_screen_points_lazy()
        return True

    # ── 保存 ──

    def _save_if_dirty(self) -> bool:
        """有改动则落盘。返回是否已保存成功(无改动视为成功)。"""
        if not self._dirty:
            return True
        try:
            if not self._save_to_json():
                self._set_status("洋区保存失败: 未匹配到任何洋区条目")
                return False
        except Exception as ex:
            self._set_status(f"洋区保存失败: {ex}")
            return False
        self._dirty = False
        self._set_status("洋区已保存")
        return True

    def _save_to_json(self) -> bool:
        """写回紧 JSON。返回是否至少更新了一条洋区(False = 未匹配到任何条目)。

        路径 = assets/Area_ocean.json(不存在则用 SUCAI_DIR)。

        说明:
          - 不在旁边生成 .bak(与台风数据目录一致, 避免垃圾文件); 防写坏由
            原子写(临时文件+替换)兜底
          - merged 洋区(自动生成)与原字段不动, 只更新主洋区的 c
        """
        path = AREA_OCEAN_FILE
        if not os.path.exists(path):
            alt = os.path.join(SUCAI_DIR, "Area_ocean.json")
            if os.path.exists(alt):
                path = alt
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        oa = self.sim.res_mgr.ocean_areas
        n_updated = 0
        for obj in data:
            code = obj.get('code')
            if obj.get('merged'):
                continue  # 自动合并洋区不写多边形
            # 用主洋区字典: get_by_code 也会返回子洋区(OceanSubArea 无 vertices)
            area = getattr(oa, '_by_code', {}).get(code) if code else None
            if area is None or getattr(area, 'is_merged', False):
                continue
            if not area.vertices or len(area.vertices) < 3:
                continue  # 拒绝非法多边形
            # 写回该条目的 c([lon, lat] 对), 其余字段原样
            obj['c'] = [[lo, la] for (la, lo) in area.vertices]
            n_updated += 1
        if n_updated == 0:
            return False  # 未匹配到任何条目: 上层据此保留脏标记并报错
        # 原子写: 先写临时文件再覆盖, 写坏也不会丢原文件内容
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception:
            # 失败时清掉临时文件, 不留下 .tmp 垃圾
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        # 刷新 ACE 限制缓存(可能按洋区)
        try:
            oa._rebuild_by_code()
        except Exception:
            pass
        return True

    # ── 绘制(主地图层, 由渲染器调用) ──

    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        sim = self.sim
        dark = getattr(sim, 'dark_mode', False)
        oa = getattr(getattr(sim, 'res_mgr', None), 'ocean_areas', None)
        if oa is None:
            return
        sw, sh = sim.screen_width, getattr(sim, 'map_height', 0)
        # 可视化吸附网格: 按 edit_snap_step(默认0.1°) 步长, 1°粗线/步长细线
        try:
            _raw = getattr(sim.cfg, 'edit_snap_step', 0.1)
            step = 0.1 if _raw is None else float(_raw)   # 保留 0.0(关闭吸附)
        except Exception:
            step = 0.1
        try:
            self._draw_snap_grid(surface, step, sw, sh)
        except Exception:
            pass
        # 编辑中的洋区: 顶点高亮; 其他洋区淡蓝
        for area in oa.areas:
            if area._is_hemisphere or len(area.vertices) < 3:
                continue
            selected = (area is self.selected_area)
            color = (90, 160, 90) if selected else (80, 140, 220)
            # 环面最短展开边界点(跨 0°/180° 连续), 再投影避免横穿屏幕
            unwrapped = []
            prev_lo = None
            ok = True
            for v in area.vertices:
                la, lo = float(v[0]), float(v[1])
                if prev_lo is None:
                    unwrapped.append((la, lo))
                else:
                    dlon = ((lo - prev_lo + 180.0) % 360.0) - 180.0
                    # 原来误用纬度差做跳变阈值(纬度跨度 >90° 的洋区会被整条跳过);
                    # 跨屏边的过滤已由下方屏幕坐标守卫(> sw*0.7)处理
                    unwrapped.append((la, prev_lo + dlon))
                prev_lo = unwrapped[-1][1]
            if not ok or not unwrapped:
                continue
            pts = []
            for la, lo in unwrapped:
                try:
                    x, y = sim.latlon_to_screen(la, lo)
                    pts.append((int(x), int(y)))
                except Exception:
                    pts.append(None)
            n = len(pts)
            for i in range(n):
                p0, p1 = pts[i], pts[(i + 1) % n]
                if p0 is None or p1 is None:
                    continue
                if abs(p1[0] - p0[0]) > sw * 0.7 or abs(p1[1] - p0[1]) > sh:
                    continue
                pygame.draw.line(surface, color, p0, p1, 3 if selected else 2)
            # 顶点圆点
            for i, p in enumerate(pts):
                if p is None:
                    continue
                r = 6 if selected else 3
                if selected and i == self.drag_idx:
                    r = 8
                pygame.draw.circle(surface, (250, 220, 90) if selected else (220, 235, 250),
                                   p, r, 0 if selected else 1)
        # 顶部提示
        if self._status:
            tc = (230, 235, 245) if dark else (30, 40, 60)
            ts = rt(f_m, self._status, tc)
            x = (sim.screen_width - ts.get_width()) // 2
            y = 8
            bg = pygame.Surface((ts.get_width() + 16, ts.get_height() + 8), pygame.SRCALPHA)
            if dark:
                bg.fill((20, 24, 34, 200))
            else:
                bg.fill((250, 250, 252, 210))
            surface.blit(bg, (x - 8, y - 4))
            surface.blit(ts, (x, y))

    def hover_status(self) -> str:
        return self._status

    def _draw_snap_grid(self, surface, step: float, sw: int, sh: int) -> None:
        """可视化吸附网格: 按 step(默认0.1°)画细线, 每 1° 画一条稍粗线。

        线画在洋区边界之下(先画), 半透明深色, 淡色图上也可辨。
        经纬网格按屏幕经纬度逐条投影(等距圆柱), 跨缝经度自动环绕。
        """
        if not (step > 0.0) or step < 0.01:
            # 0 = 设置里"关"; 负值会让下面的 while 永不终止;
            # 过小(手改 config.json, 配置仅钳 0~10)会让每帧画几十万条线 → 卡死
            return
        sim = self.sim
        try:
            line_surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
            dark = getattr(sim, 'dark_mode', False)
            fine = (60, 70, 90, 60) if dark else (90, 110, 140, 55)
            major = (255, 255, 255, 90) if dark else (0, 0, 0, 70)
            # 视野经纬范围
            lo_min = float(sim.mlo)
            lo_max = float(sim.Mlo)
            la_min = float(sim.mla)
            la_max = float(sim.Mla)
            if lo_max <= lo_min:
                lo_max += 360.0
            # 经线
            lon0 = math.floor(lo_min / step) * step
            lon = lon0
            while lon <= lo_max + 1e-9:
                nl = ((lon % 360.0) + 360.0) % 360.0
                try:
                    x, _y = sim.latlon_to_screen(la_max, nl)
                except Exception:
                    lon += step
                    continue
                if 0 <= x <= sw:
                    is_major = abs((lon % 1.0)) < 1e-9
                    pygame.draw.line(line_surf, major if is_major else fine,
                                     (int(x), 0), (int(x), sh), 2 if is_major else 1)
                lon += step
            # 纬线
            lat0 = math.floor(la_min / step) * step
            lat = lat0
            while lat <= la_max + 1e-9:
                is_major = abs((lat % 1.0)) < 1e-9
                try:
                    _x, y = sim.latlon_to_screen(lat, lo_min)
                except Exception:
                    lat += step
                    continue
                if 0 <= y <= sh:
                    pygame.draw.line(line_surf, major if is_major else fine,
                                     (0, int(y)), (sw, int(y)), 2 if is_major else 1)
                lat += step
            surface.blit(line_surf, (0, 0))
        except Exception as ex:
            logger.warning(f"洋区编辑网格绘制失败: {ex}")
