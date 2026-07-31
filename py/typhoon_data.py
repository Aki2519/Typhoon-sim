# py/typhoon_data.py
"""台风数据 Mixin：TrackPoint, TyphoonView, 数据操作方法。"""
from __future__ import annotations

import copy
import datetime
import logging
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .ty_sim import TySim

logger = logging.getLogger(__name__)


class TyphoonDataMixin:
    """数据操作：add_point, recalc_ace, recalc_simulated_times, undo/redo。"""

    def push_snapshot(self) -> None:
        self._undo_stack.append(copy.deepcopy(self.pts))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.v._img_cache.clear()

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(copy.deepcopy(self.pts))
        self.pts = self._undo_stack.pop()
        self._restore_after_history_change()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(copy.deepcopy(self.pts))
        self.pts = self._redo_stack.pop()
        self._restore_after_history_change()
        return True

    def _restore_after_history_change(self) -> None:
        if self.sim:
            self.recalc_ace()
            self.update_screen_points(self.sim.latlon_to_screen)
            self.recalc_simulated_times()
            if not self.sim.pl:
                self.rst()
                self.cace = self.pts[self.ci]['ace'] \
                    if self.pts and 0 <= self.ci < len(self.pts) else 0.0
        for attr in ('_cached_max_wind_color', '_cached_peaks', '_cached_name_colors'):
            if hasattr(self, attr):
                delattr(self, attr)

    def add_point(self, t: str, la: float, lo: float, w: int, p: int,
                  st: str, sn: str = "") -> None:
        from .typhoon import TrackPoint
        is_official = len(t) >= 10 and t[8:10] in ('00', '06', '12', '18')
        if not self.pts and la < 0:
            self.v.mirror = True
            self.v.rot_dir = -1

        geo_ok = True
        if self.sim and self.sim.ace_geo_limit_enabled:
            geo_ok = self.sim.ace_engine.point_in_limit(la, lo)

        pace = 0.0
        st_up = st.upper()
        if st_up in ('TS', 'TY', 'ST', 'HU', '') and isinstance(w, (int, float)) and w >= 35 and geo_ok and is_official:
            pace = round((w * w) / 10000.0, 4)

        ace_year = 0
        if self.sim and len(t) >= 10:
            try:
                ace_year = self.sim.get_ace_year(datetime.datetime.strptime(t[:10], "%Y%m%d%H"))
            except ValueError:
                pass

        self.tace += pace
        self.cumace = self.tace

        color = self.sim.get_point_color(w, st) if self.sim else (128, 128, 128)
        color_dim = self.sim.darken_color(color, 0.6) if self.sim else (77, 77, 77)
        cat = self.sim.get_strength_category(w, st) if self.sim else "TD"

        self.pts.append(TrackPoint(
            t=t, la=la, lo=lo, w=w, p=p, st=st,
            ace=self.tace, pace=pace, name=sn,
            official=is_official, ace_year=ace_year,
            color=color, color_dim=color_dim,
            cat=cat,
        ))
        self.points_time = []
        if hasattr(self, '_cached_max_wind_color'):
            delattr(self, '_cached_max_wind_color')
        if hasattr(self, '_cached_peaks'):
            delattr(self, '_cached_peaks')
        if hasattr(self, '_cached_name_colors'):
            delattr(self, '_cached_name_colors')

    def recalc_ace(self) -> None:
        total = 0.0
        geo_enabled = bool(self.sim and self.sim.ace_geo_limit_enabled)
        for pt in self.pts:
            st = pt['st'].upper()
            geo_ok = (not geo_enabled) or self.sim.ace_engine.point_in_limit(pt['la'], pt['lo'])
            if st in ('TS', 'TY', 'ST', 'HU', '') and pt.get('official', True) and pt['w'] >= 35 and geo_ok:
                pace = round((pt['w'] * pt['w']) / 10000.0, 4)
            else:
                pace = 0.0
            pt['pace'] = pace
            total += pace
            pt['ace'] = total
        self.tace = total
        self.cumace = total
        for attr in ('_cached_max_wind_color', '_cached_peaks', '_cached_name_colors'):
            if hasattr(self, attr):
                delattr(self, attr)

    def recalc_simulated_times(self) -> None:
        if not self.pts:
            self.points_time = []
            self.points_dt = []
            return

        self.points_dt = []
        for pt in self.pts:
            try:
                self.points_dt.append(datetime.datetime.strptime(pt['t'][:10], "%Y%m%d%H"))
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"台风 {getattr(self, 'n', '?')} 时间戳无效 '{pt.get('t', '')}'，"
                    f"回退为 2000-01-01，时间插值可能不准确: {e}")
                self.points_dt.append(datetime.datetime(2000, 1, 1, 0))

        officials = [i for i, pt in enumerate(self.pts) if pt.get('official', False)]
        if not officials:
            self.points_time = [i * 0.5 for i in range(len(self.pts))]
            return

        off_times = [0.0]
        for _ in officials[1:]:
            off_times.append(off_times[-1] + 0.5)

        self.points_time = [0.0] * len(self.pts)

        for idx in range(len(self.pts)):
            left = max((i for i in officials if i <= idx), default=None)
            right = next((i for i in officials if i > idx), None)
            self.points_time[idx] = self._interpolate_time(idx, left, right, officials, off_times)

        for i in range(1, len(self.points_time)):
            if self.points_time[i] < self.points_time[i - 1]:
                self.points_time[i] = self.points_time[i - 1] + 0.001

    def _interpolate_time(self, idx: int, left: Optional[int], right: Optional[int],
                          officials: List[int], off_times: List[float]) -> float:
        if left is None and right is not None:
            left_dt = self.points_dt[right] - datetime.timedelta(hours=6)
            right_dt = self.points_dt[right]
            ratio = (self.points_dt[idx] - left_dt).total_seconds() / (right_dt - left_dt).total_seconds() \
                if right_dt > left_dt else 0
            return -0.5 + ratio * 0.5
        if left is not None and right is None:
            li = officials.index(left)
            left_t = off_times[li]
            right_dt = self.points_dt[left] + datetime.timedelta(hours=6)
            ratio = (self.points_dt[idx] - self.points_dt[left]).total_seconds() / (right_dt - self.points_dt[left]).total_seconds() \
                if right_dt > self.points_dt[left] else 0
            return left_t + ratio * 0.5
        if left is not None and right is not None:
            li = officials.index(left)
            ri = officials.index(right)
            left_t = off_times[li]
            right_t = off_times[ri]
            left_dt = self.points_dt[left]
            right_dt = self.points_dt[right]
            ratio = (self.points_dt[idx] - left_dt).total_seconds() / (right_dt - left_dt).total_seconds() \
                if right_dt > left_dt else 0
            return left_t + ratio * (right_t - left_t)
        return idx * 0.5
