# py/typhoon_sim.py
"""台风模拟 Mixin：运动、时间跳转、重置。"""
from __future__ import annotations

import datetime
import bisect
from typing import List, Optional, Dict, TYPE_CHECKING

import pygame

from .spline import position_at_arc

if TYPE_CHECKING:
    from .ty_sim import TySim
    from .typhoon import TrackPoint


class TyphoonSimMixin:
    """模拟方法：start_move, update_move, set_current_time, reset 等。"""

    def start_move(self, current_time: float) -> None:
        n_pts = len(self.pts)
        if self.ci < 0 or self.ci >= n_pts - 1:
            self._mark_finished(current_time)
            return
        if len(self.points_time) != n_pts:
            self.recalc_simulated_times()
        if self.ci + 1 >= len(self.points_time):
            self._mark_finished(current_time)
            return
        self.ist = self.lut = current_time
        cp = self.pts[self.ci]
        self.v.ipos = {'la': cp['la'], 'lo': cp['lo']}

    def _mark_finished(self, current_time: float) -> None:
        self.fin = True
        self.ft = current_time
        self.finish_time = current_time

    def update_move(self, current_time: float, speed_factor: float = 1.0,
                    is_paused: bool = False) -> bool:
        ipos = self.v.ipos
        last_idx = len(self.pts) - 1
        if not ipos or self.ci >= last_idx:
            return False
        if is_paused:
            self.lut = current_time
            return False
        # 确保 points_time 与 pts 长度一致,避免下方 points_time[ci+1] 越界(R4-1)
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        if self.ci + 1 >= len(self.points_time):
            return False
        if self.lut > 0:
            self.at += (current_time - self.lut) * 0.001 * speed_factor
        self.lut = current_time

        target = self.points_time[self.ci + 1]
        if self.at < target:
            pt0 = self.points_time[self.ci]
            total = target - pt0
            t = (self.at - pt0) / total if total > 0 else 0
            t = min(1.0, max(0.0, t))
            # 平滑样条与屏幕点始终同帧重建（含缩放突发期），可直接沿曲线走
            use_smooth = (self.sim and self.sim.cfg.smooth_path
                          and self.v.smooth_screen_points
                          and self.v._smooth_arc_lengths)
            if use_smooth:
                progress = self._smooth_progress(t)
                self._move_on_curve(ipos, progress)
            else:
                cp, np = self.pts[self.ci], self.pts[self.ci + 1]
                ipos['la'] = cp['la'] + (np['la'] - cp['la']) * t
                ipos['lo'] = cp['lo'] + (np['lo'] - cp['lo']) * t
            return False

        self.ci += 1
        self.v.ipos = None
        if self.ci >= last_idx:
            self._mark_finished(current_time)
        elif self.ci + 1 < len(self.points_time):
            self.start_move(current_time)
        return True

    def _move_on_curve(self, ipos: Dict[str, float], progress: float) -> None:
        arcs = self.v._smooth_arc_lengths
        segs = max(1, self.sim.cfg.smooth_path_segments)
        i0 = self.ci * segs
        i1 = min((self.ci + 1) * segs, len(arcs) - 1)
        if i1 == i0:
            i1 = i0 + 1 if i0 + 1 < len(arcs) else i0
        seg_start = arcs[i0]
        seg_total = arcs[i1] - seg_start
        if seg_total <= 0:
            seg_total = 1.0
        target = seg_start + seg_total * progress
        sc_x, sc_y = position_at_arc(
            self.v.smooth_screen_points, arcs, target)
        if self.sim:
            sc_x += self.sim._drag_offset_x
            sc_y += self.sim._drag_offset_y
            lat, lon = self.sim.screen_to_latlon(sc_x, sc_y)
            ipos['la'] = lat
            ipos['lo'] = lon

    def _smooth_progress(self, t: float) -> float:
        ci = self.ci
        n = len(self.pts)
        arcs = self.v._smooth_arc_lengths
        segs = max(1, self.sim.cfg.smooth_path_segments)
        cur_len = self._arc_span(arcs, ci, segs)
        prev_len = self._arc_span(arcs, ci - 1, segs) if ci > 0 else cur_len
        next_len = self._arc_span(arcs, ci + 1, segs) if ci + 1 < n - 1 else cur_len
        cur_dt = self.points_time[ci + 1] - self.points_time[ci]
        prev_dt = self.points_time[ci] - self.points_time[ci - 1] if ci > 0 else cur_dt
        next_dt = self.points_time[ci + 2] - self.points_time[ci + 1] if ci + 1 < n - 1 else cur_dt
        cur_rate = cur_len / cur_dt if cur_dt > 0 else 0.0
        s_prev = (prev_len / prev_dt) / cur_rate if prev_dt > 0 and cur_rate > 0 else 1.0
        s_next = (next_len / next_dt) / cur_rate if next_dt > 0 and cur_rate > 0 else 1.0
        v0 = max(0.1, min(3.0, (1.0 + s_prev) / 2.0))
        v1 = max(0.1, min(3.0, (1.0 + s_next) / 2.0))
        t2 = t * t
        t3 = t2 * t
        h01 = -2.0 * t3 + 3.0 * t2
        h11 = t3 - t2
        h10 = t3 - 2.0 * t2 + t
        return max(0.0, min(1.0, h01 + h10 * v0 + h11 * v1))

    @staticmethod
    def _arc_span(arcs: List[float], ci: int, segs: int) -> float:
        if not arcs or ci < 0 or ci * segs >= len(arcs):
            return 1.0
        i0 = ci * segs
        i1 = min((ci + 1) * segs, len(arcs) - 1)
        return arcs[i1] - arcs[i0] if i1 > i0 else 1.0

    def current_position(self) -> Optional[Dict[str, float]]:
        if self.v.ipos:
            return self.v.ipos
        p = self.current_point()
        return {'la': p['la'], 'lo': p['lo']} if p else None

    def current_point(self) -> Optional[TrackPoint]:
        return self.pts[self.ci] if 0 <= self.ci < len(self.pts) else None

    def interpolated_cace(self) -> float:
        if not self.pts or self.ci >= len(self.pts) - 1:
            return self.tace
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        pt0, pt1 = self.points_time[self.ci], self.points_time[self.ci + 1]
        if pt1 <= pt0:
            return self.pts[self.ci]['ace']
        a0 = self.pts[self.ci]['ace']
        t = (self.at - pt0) / (pt1 - pt0)
        t = max(0.0, min(1.0, t))
        return a0 + (self.pts[self.ci + 1]['ace'] - a0) * t

    def set_current_time(self, target_dt: datetime.datetime) -> None:
        if not self.pts:
            return
        # 确保 points_time/points_dt 与 pts 长度一致,避免下方 points_time[i+1] 越界(R4-1)
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        pd = self.points_dt
        pt = self.points_time
        if not pd or not pt:
            return
        n = len(self.pts)
        # 恰在末报点/晚于末报点: 台风已播完(R2-21), 不进入插值分支,收尾为 fin
        if target_dt >= pd[-1]:
            self.at, self.ci = pt[-1], len(self.pts) - 1
            self.fin = True
            self.v.ipos = None
            self.lut = 0
            self.v.last_on_land = False
            self.cace = self.pts[self.ci]['ace']
            return
        if len(pd) == n and len(pt) == n:
            # bisect 仅在 points_dt 有序时成立;编辑/插入导致乱序时回落线性扫描(R4-2)
            ordered = all(pd[i] <= pd[i + 1] for i in range(n - 1))
            if ordered:
                i = bisect.bisect_right(pd, target_dt) - 1
                if 0 <= i < n - 1 and target_dt <= pd[i + 1]:
                    dt1, dt2 = pd[i], pd[i + 1]
                    ratio = ((target_dt - dt1).total_seconds() / (dt2 - dt1).total_seconds()
                             if dt2 > dt1 else 0)
                    self.at = pt[i] + ratio * (pt[i + 1] - pt[i])
                    self.ci = i
                    if ratio > 0:
                        cp, np = self.pts[i], self.pts[i + 1]
                        self.v.ipos = {'la': cp['la'] + (np['la'] - cp['la']) * ratio,
                                       'lo': cp['lo'] + (np['lo'] - cp['lo']) * ratio}
                    else:
                        self.v.ipos = None
                    self.lut = 0
                    self.fin = self.v.last_on_land = False
                    self.cace = self.pts[self.ci]['ace']
                    return
        # 线性扫描(乱序 or 长度不一致): 首个满足 dt[i]<=t<=dt[i+1] 的段
        for i in range(len(pd) - 1):
            if i + 1 < len(pt) and pd[i] <= target_dt <= pd[i + 1]:
                dt1, dt2 = pd[i], pd[i + 1]
                ratio = ((target_dt - dt1).total_seconds() / (dt2 - dt1).total_seconds()
                         if dt2 > dt1 else 0)
                self.at = pt[i] + ratio * (pt[i + 1] - pt[i])
                self.ci = i
                if ratio > 0:
                    cp, np = self.pts[i], self.pts[i + 1]
                    self.v.ipos = {'la': cp['la'] + (np['la'] - cp['la']) * ratio,
                                   'lo': cp['lo'] + (np['lo'] - cp['lo']) * ratio}
                else:
                    self.v.ipos = None
                self.lut = 0
                self.fin = self.v.last_on_land = False
                self.cace = self.pts[self.ci]['ace']
                return

        if target_dt <= pd[0]:
            self.at, self.ci = pt[0], 0
            self.fin = False
        else:
            # 目标在数据之后(含恰在末报点): 台风已播完(R2-21)
            self.at, self.ci = pt[-1], len(self.pts) - 1
            self.fin = True
        self.v.ipos = None
        self.lut = 0
        self.v.last_on_land = False
        self.cace = self.pts[self.ci]['ace']

    def reset(self) -> None:
        self.ci = 0
        self.act = True
        self.cace = self.at = 0.0
        self.fin = self.ss = self.sf = False
        self.ft = self.lut = 0
        self.last_ace_ci = -1
        self._last_partial_csa = 0.0
        self._last_partial_ci = -1
        self.finish_time = 0
        # 单点台风完成守卫复位: 手动重新选中/复位后允许再次完成并自动推进
        self._single_fin_done = False
        v = self.v
        v.ra = v.sa = v.sa3 = v.sa4 = v.sa5 = 0.0
        v.ipos = None
        v.last_on_land = False
        v.icon_alpha = v.path_alpha = 255
        v._last_ri_at = -999.0
        v._ri_armed = True
        v._spawn_time = pygame.time.get_ticks()
        v._img_cache.clear()
        if self.pts:
            self.recalc_simulated_times()
