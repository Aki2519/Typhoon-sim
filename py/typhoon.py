# py/typhoon.py
"""台风数据类。南半球通过镜像+逆时针角度实现顺时针视觉。"""
from __future__ import annotations

import copy
import datetime
import pygame
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .ty_sim import TySim


@dataclass
class TrackPoint:
    t: str = ""
    la: float = 0.0
    lo: float = 0.0
    w: int = 0
    p: int = 0
    st: str = ""
    ace: float = 0.0
    pace: float = 0.0
    name: str = ""
    official: bool = True
    ace_year: int = 0
    color: Tuple[int, int, int] = (128, 128, 128)
    color_dim: Tuple[int, int, int] = (77, 77, 77)
    cat: str = "TD"

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def update(self, mapping: dict) -> None:
        for k, v in mapping.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


_VIEW_FIELDS = frozenset({
    "ra", "sa", "sa3", "sa4", "sa5",
    "ipos", "rot_dir", "mirror", "last_on_land",
    "icon_alpha", "path_alpha",
    "screen_points", "bbox",
    "_img_cache",
    "_path_cache_full", "_path_cache_traversed", "_last_rendered_ci",
    "_path_cache_key",
    "_path_cache_drag_surf", "_path_cache_drag_key", "_path_cache_drag_pos",
    "smooth_screen_points", "_smooth_arc_lengths",
})


class TyphoonView:
    __slots__ = tuple(_VIEW_FIELDS)

    def __init__(self) -> None:
        self.ra: float = 0.0
        self.sa: float = 0.0
        self.sa3: float = 0.0
        self.sa4: float = 0.0
        self.sa5: float = 0.0
        self.ipos: Optional[Dict[str, float]] = None
        self.rot_dir: int = 1
        self.mirror: bool = False
        self.last_on_land: bool = False
        self.icon_alpha: int = 255
        self.path_alpha: int = 255
        self.screen_points: List[Tuple[int, int]] = []
        self.bbox: Optional[pygame.Rect] = None
        self._img_cache: Dict[Tuple, pygame.Surface] = {}
        self._path_cache_full: Optional[pygame.Surface] = None
        self._path_cache_traversed: Optional[pygame.Surface] = None
        self._last_rendered_ci: int = -1
        self._path_cache_key: tuple = ()
        self._path_cache_drag_surf: Optional[pygame.Surface] = None
        self._path_cache_drag_key: tuple = ()
        self._path_cache_drag_pos: Tuple[int, int] = (0, 0)
        self.smooth_screen_points: List[Tuple[int, int]] = []
        self._smooth_arc_lengths: List[float] = []


class Typhoon:
    __slots__ = (
        'b', 'n', 'name', 'cust', 'sname', 'basin',
        'pts', 'ci', 'act',
        'tace', 'cace', 'cumace', 'ist', 'idur', 'fin', 'ft',
        'ss', 'sf', 'at', 'lut',
        'sim', 'filepath', 'start_time',
        'last_ace_ci', 'points_time', 'points_dt',
        'format_type', 'original_jtwc_source',
        '_undo_stack', '_redo_stack', '_last_partial_csa', '_last_partial_ci',
        'finish_time',
        '_cached_max_wind_color', '_in_filter_basin', '_filter_basin_checked',
        '_v',
    )

    def __init__(self, b: str, n: str) -> None:
        self.b: str = b
        self.n: str = n
        self.name: str = f"{b}{n}"
        self.cust: str = ""
        self.sname: str = ""
        self.basin: str = ""
        self.pts: List[TrackPoint] = []
        self.ci: int = 0
        self.act: bool = True
        self.tace: float = 0.0
        self.cace: float = 0.0
        self.cumace: float = 0.0
        self.ist: int = 0
        self.idur: float = 0.5
        self.fin: bool = False
        self.ft: float = 0
        self.ss: bool = False
        self.sf: bool = False
        self.at: float = 0.0
        self.lut: float = 0
        self.sim: Optional[TySim] = None
        self.filepath: Optional[str] = None
        self.start_time: Optional[str] = None
        self.last_ace_ci: int = -1
        self.points_time: List[float] = []
        self.points_dt: List[datetime.datetime] = []
        self.format_type: str = "simple_bdeck"
        self.original_jtwc_source: Optional[str] = None
        self._undo_stack: List[List[TrackPoint]] = []
        self._redo_stack: List[List[TrackPoint]] = []
        self._last_partial_csa: float = 0.0
        self._last_partial_ci: int = -1
        self.finish_time: float = 0
        self._v: TyphoonView = TyphoonView()

    def __getattr__(self, name: str):
        if name in _VIEW_FIELDS:
            try:
                return getattr(object.__getattribute__(self, '_v'), name)
            except AttributeError:
                pass
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        if name in _VIEW_FIELDS:
            try:
                v = object.__getattribute__(self, '_v')
            except AttributeError:
                pass
            else:
                setattr(v, name, value)
                return
        object.__setattr__(self, name, value)

    @property
    def v(self) -> TyphoonView:
        return self._v

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
                self.cace = self.pts[self.ci]['ace'] if self.pts and self.ci < len(self.pts) else 0.0
        if hasattr(self, '_cached_max_wind_color'):
            delattr(self, '_cached_max_wind_color')

    def add_point(self, t: str, la: float, lo: float, w: int, p: int,
                  st: str, sn: str = "") -> None:
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
        if hasattr(self, '_cached_max_wind_color'):
            delattr(self, '_cached_max_wind_color')

    def recalc_ace(self) -> None:
        total = 0.0
        for pt in self.pts:
            st = pt['st'].upper()
            if st in ('TS', 'TY', 'ST', 'HU', '') and pt.get('official', True) and pt['w'] >= 35:
                pace = round((pt['w'] * pt['w']) / 10000.0, 4)
            else:
                pace = 0.0
            pt['pace'] = pace
            total += pace
            pt['ace'] = total
        self.tace = total
        self.cumace = total
        if hasattr(self, '_cached_max_wind_color'):
            delattr(self, '_cached_max_wind_color')

    def recalc_simulated_times(self) -> None:
        if not self.pts:
            self.points_time = []
            self.points_dt = []
            return

        self.points_dt = []
        for pt in self.pts:
            try:
                self.points_dt.append(datetime.datetime.strptime(pt['t'][:10], "%Y%m%d%H"))
            except Exception:
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
            return 0.0 + ratio * (off_times[0] - 0.0)
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

    def start_move(self, current_time: float) -> None:
        if self.ci < 0 or self.ci >= len(self.pts) - 1:
            self._mark_finished(current_time)
            return
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        if len(self.points_time) != len(self.pts):
            self._mark_finished(current_time)
            return
        if self.ci + 1 >= len(self.points_time):
            self._mark_finished(current_time)
            return
        self.idur = max(self.points_time[self.ci + 1] - self.points_time[self.ci], 0.001)
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
        if not ipos or self.ci >= len(self.pts) - 1:
            return False
        if is_paused:
            self.lut = current_time
            return False
        if self.lut > 0:
            self.at += (current_time - self.lut) / 1000.0 * speed_factor
        self.lut = current_time

        target = self.points_time[self.ci + 1]
        if self.at < target:
            total = target - self.points_time[self.ci]
            t = (self.at - self.points_time[self.ci]) / total if total > 0 else 0
            t = min(1.0, max(0.0, t))
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
        if self.ci >= len(self.pts) - 1:
            self.fin = True
            self.ft = current_time
            self.finish_time = current_time
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
        from .spline import position_at_arc
        sc_x, sc_y = position_at_arc(
            self.v.smooth_screen_points, arcs, target)
        if self.sim:
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
        s_prev = (prev_len / prev_dt) / (cur_len / cur_dt) if cur_dt > 0 and prev_dt > 0 else 1.0
        s_next = (next_len / next_dt) / (cur_len / cur_dt) if cur_dt > 0 and next_dt > 0 else 1.0
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

    def next_point(self) -> Optional[TrackPoint]:
        return self.pts[self.ci + 1] if self.ci + 1 < len(self.pts) else None

    def interpolated_cace(self) -> float:
        if not self.pts or self.ci >= len(self.pts) - 1:
            return self.tace
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        pt0, pt1 = self.points_time[self.ci], self.points_time[self.ci + 1]
        if pt1 <= pt0:
            return self.pts[self.ci]['ace']
        t = (self.at - pt0) / (pt1 - pt0)
        t = max(0.0, min(1.0, t))
        return self.pts[self.ci]['ace'] + (self.pts[self.ci + 1]['ace'] - self.pts[self.ci]['ace']) * t

    def update_rotation(self, dt: float) -> None:
        mf = self.sim.main_rotation_speed if self.sim else 1.0
        lf = self.sim.level3_rotation_speed if self.sim else 1.5
        v = self.v
        v.sa = (v.sa + 360 * dt * mf) % 360
        v.sa3 = (v.sa3 + 360 * dt * lf) % 360
        v.sa4 = (v.sa4 + 360 * dt * lf) % 360
        v.sa5 = (v.sa5 + 360 * dt * lf) % 360

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
        v = self.v
        v.ra = v.sa = v.sa3 = v.sa4 = v.sa5 = 0.0
        v.ipos = None
        v.last_on_land = False
        v.icon_alpha = v.path_alpha = 255
        v._img_cache.clear()
        if self.pts:
            self.recalc_simulated_times()

    def set_current_time(self, target_dt: datetime.datetime) -> None:
        if not self.pts or not self.points_dt or not self.points_time:
            return
        for i in range(len(self.points_dt) - 1):
            if self.points_dt[i] <= target_dt <= self.points_dt[i + 1]:
                dt1, dt2 = self.points_dt[i], self.points_dt[i + 1]
                ratio = (target_dt - dt1).total_seconds() / (dt2 - dt1).total_seconds() if dt2 > dt1 else 0
                self.at = self.points_time[i] + ratio * (self.points_time[i + 1] - self.points_time[i])
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

        if target_dt <= self.points_dt[0]:
            self.at, self.ci = self.points_time[0], 0
        else:
            self.at, self.ci = self.points_time[-1], len(self.pts) - 1
        self.v.ipos = None
        self.lut = 0
        self.fin = False
        self.cace = self.pts[self.ci]['ace']

    def update_screen_points(self, latlon_to_screen_func: Callable[[float, float], Tuple[int, int]]) -> None:
        v = self.v
        v.screen_points.clear()
        v.smooth_screen_points.clear()
        v._smooth_arc_lengths.clear()
        if not self.pts:
            v.bbox = None
            return
        xs, ys = [], []
        for pt in self.pts:
            x, y = latlon_to_screen_func(pt['la'], pt['lo'])
            v.screen_points.append((x, y))
            xs.append(x)
            ys.append(y)
        v.bbox = pygame.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        if self.sim and self.sim.cfg.smooth_path:
            from .spline import build_spline, compute_arc_lengths
            segs = self.sim.cfg.smooth_path_segments
            geo_pts = [(p['lo'], p['la']) for p in self.pts]
            smooth_geo = build_spline(geo_pts, segs)
            f = latlon_to_screen_func
            smooth_sc = [f(lat, lon) for lon, lat in smooth_geo]
            v.smooth_screen_points = smooth_sc
            v._smooth_arc_lengths = compute_arc_lengths(smooth_sc)

    def _get_rotated(self, key_prefix: str, img: pygame.Surface, angle: float,
                     mirror: bool) -> pygame.Surface:
        key = (key_prefix, int(angle) % 360, mirror)
        cache = self.v._img_cache
        if key in cache:
            return cache[key]
        rotated = pygame.transform.rotate(img, angle)
        if mirror:
            rotated = pygame.transform.flip(rotated, True, False)
        cache[key] = rotated
        return rotated

    def get_rotated_ring(self, cat: str, base_ring: pygame.Surface, angle: float,
                          mirror: bool) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror)

    def get_rotated_level3_ring(self, cat: str, base_ring: pygame.Surface, angle: float,
                                 mirror: bool) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror)

    ap = add_point
    cp = current_point
    np = next_point
    sm = start_move
    um = update_move
    cpos = current_position
    us = update_rotation
    rst = reset

    def __repr__(self) -> str:
        return f"Typhoon(name={self.name}, pts={len(self.pts)})"
