# py/typhoon.py
"""台风数据类。南半球通过镜像+逆时针角度实现顺时针视觉。"""
import copy
import datetime
import pygame
from typing import List, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .ty_sim import TySim


class Typhoon:
    __slots__ = (
        'b', 'n', 'name', 'cust', 'sname', 'basin',
        'pts', 'ci', 'act', 'ra', 'sa', 'sa3', 'sa4', 'sa5',
        'tace', 'cace', 'cumace', 'ipos', 'ist', 'idur', 'fin', 'ft',
        'ss', 'sf', 'at', 'lut',
        'sim', 'last_on_land', 'filepath', 'start_time',
        'rot_dir', 'mirror', 'last_ace_ci',
        'points_time', 'points_dt', 'screen_points', 'bbox',
        '_img_cache', 'format_type', 'original_jtwc_source',
        '_undo_stack', '_redo_stack',
        'finish_time', 'icon_alpha', 'path_alpha',
    )

    def __init__(self, b: str, n: str):
        self.b = b
        self.n = n
        self.name = f"{b}{n}"
        self.cust = ""
        self.sname = ""
        self.basin = ""
        self.pts: List[Dict] = []
        self.ci = 0
        self.act = True
        self.ra = 0.0
        self.sa = 0.0
        self.sa3 = 0.0
        self.sa4 = 0.0
        self.sa5 = 0.0
        self.tace = 0.0
        self.cace = 0.0
        self.cumace = 0.0
        self.ipos: Optional[Dict[str, float]] = None
        self.ist = 0
        self.idur = 0.5
        self.fin = False
        self.ft = 0
        self.ss = False
        self.sf = False
        self.at = 0.0
        self.lut = 0
        self.sim: Optional['TySim'] = None
        self.last_on_land = False
        self.filepath: Optional[str] = None
        self.start_time: Optional[str] = None
        self.rot_dir = 1
        self.mirror = False
        self.last_ace_ci = -1

        self.points_time: List[float] = []
        self.points_dt: List[datetime.datetime] = []
        self.screen_points: List[Tuple[int, int]] = []
        self.bbox: Optional[pygame.Rect] = None

        self._img_cache: Dict[Tuple, pygame.Surface] = {}
        self.format_type = "simple_bdeck"
        self.original_jtwc_source: Optional[str] = None

        self._undo_stack: List[List[Dict]] = []
        self._redo_stack: List[List[Dict]] = []

        self.finish_time = 0
        self.icon_alpha = 255
        self.path_alpha = 255

    # ── 历史 ──

    def push_snapshot(self) -> None:
        self._undo_stack.append(copy.deepcopy(self.pts))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._img_cache.clear()

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

    # ── 报点 ──

    def add_point(self, t: str, la: float, lo: float, w: int, p: int, st: str, sn: str = "") -> None:
        is_official = len(t) >= 10 and t[8:10] in ('00', '06', '12', '18')
        if not self.pts and la < 0:
            self.mirror = True
            self.rot_dir = -1

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

        self.pts.append({
            't': t, 'la': la, 'lo': lo, 'w': w, 'p': p, 'st': st,
            'ace': self.tace, 'pace': pace, 'name': sn,
            'official': is_official, 'ace_year': ace_year,
            'color': color, 'color_dim': color_dim,
            'cat': cat,
        })

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

    # ── 播放移动 ──

    def start_move(self, current_time: int) -> None:
        if self.ci < 0 or self.ci >= len(self.pts) - 1:
            self.fin = True
            self.ft = current_time
            self.finish_time = current_time
            return
        if len(self.points_time) != len(self.pts):
            self.recalc_simulated_times()
        if self.ci + 1 >= len(self.points_time):
            self.fin = True
            self.ft = current_time
            self.finish_time = current_time
            return
        self.idur = max(self.points_time[self.ci + 1] - self.points_time[self.ci], 0.001)
        self.ist = self.lut = current_time
        cp = self.pts[self.ci]
        self.ipos = {'la': cp['la'], 'lo': cp['lo']}

    def update_move(self, current_time: int, speed_factor: float = 1.0, is_paused: bool = False) -> bool:
        if not self.ipos or self.ci >= len(self.pts) - 1:
            return False
        if is_paused:
            self.lut = current_time          # 冻结时钟，恢复时不累积
            return False
        if self.lut > 0:
            self.at += (current_time - self.lut) / 1000.0 * speed_factor
        self.lut = current_time

        target = self.points_time[self.ci + 1]
        if self.at < target:
            total = target - self.points_time[self.ci]
            progress = (self.at - self.points_time[self.ci]) / total if total > 0 else 0
            progress = min(1.0, max(0.0, progress))
            cp, np = self.pts[self.ci], self.pts[self.ci + 1]
            self.ipos['la'] = cp['la'] + (np['la'] - cp['la']) * progress
            self.ipos['lo'] = cp['lo'] + (np['lo'] - cp['lo']) * progress
            return False

        self.ci += 1
        self.ipos = None
        if self.ci >= len(self.pts) - 1:
            self.fin = True
            self.ft = current_time
            self.finish_time = current_time
        elif self.ci + 1 < len(self.points_time):
            self.start_move(current_time)
        return True

    def current_position(self) -> Optional[Dict[str, float]]:
        if self.ipos:
            return self.ipos
        p = self.current_point()
        return {'la': p['la'], 'lo': p['lo']} if p else None

    def current_point(self) -> Optional[Dict]:
        return self.pts[self.ci] if 0 <= self.ci < len(self.pts) else None

    def next_point(self) -> Optional[Dict]:
        return self.pts[self.ci + 1] if self.ci + 1 < len(self.pts) else None

    # ── 旋转 ──

    def update_rotation(self, dt: float) -> None:
        mf = self.sim.main_rotation_speed if self.sim else 1.0
        lf = self.sim.level3_rotation_speed if self.sim else 1.5
        self.sa = (self.sa + 360 * dt * mf) % 360
        self.sa3 = (self.sa3 + 360 * dt * lf) % 360
        self.sa4 = (self.sa4 + 360 * dt * lf) % 360
        self.sa5 = (self.sa5 + 360 * dt * lf) % 360

    # ── 重置 / 时间设置 ──

    def reset(self) -> None:
        self.ci = 0
        self.act = True
        self.ra = self.sa = self.sa3 = self.sa4 = self.sa5 = 0.0
        self.cace = self.at = 0.0
        self.ipos = None
        self.fin = self.ss = self.sf = False
        self.ft = self.lut = 0
        self.last_on_land = False
        self.last_ace_ci = -1
        self.finish_time = 0
        self.icon_alpha = self.path_alpha = 255
        self._img_cache.clear()
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
                    self.ipos = {'la': cp['la'] + (np['la'] - cp['la']) * ratio,
                                 'lo': cp['lo'] + (np['lo'] - cp['lo']) * ratio}
                else:
                    self.ipos = None
                self.lut = 0
                self.fin = self.last_on_land = False
                self.cace = self.pts[self.ci]['ace']
                return

        if target_dt <= self.points_dt[0]:
            self.at, self.ci = self.points_time[0], 0
        else:
            self.at, self.ci = self.points_time[-1], len(self.pts) - 1
        self.ipos = None
        self.lut = 0
        self.fin = False
        self.cace = self.pts[self.ci]['ace']

    # ── 屏幕坐标 ──

    def update_screen_points(self, latlon_to_screen_func) -> None:
        self.screen_points.clear()
        if not self.pts:
            self.bbox = None
            return
        xs, ys = [], []
        for pt in self.pts:
            x, y = latlon_to_screen_func(pt['la'], pt['lo'])
            self.screen_points.append((x, y))
            xs.append(x); ys.append(y)
        self.bbox = pygame.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # ── 图标旋转缓存 ──

    def _get_rotated(self, key_prefix: str, img: pygame.Surface, angle: float, mirror: bool) -> pygame.Surface:
        key = (key_prefix, int(angle) % 360, mirror)
        if key in self._img_cache:
            return self._img_cache[key]
        rotated = pygame.transform.rotate(img, angle)
        if mirror:
            rotated = pygame.transform.flip(rotated, True, False)
        self._img_cache[key] = rotated
        return rotated

    def get_rotated_ring(self, cat: str, base_ring: pygame.Surface, angle: float, mirror: bool) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror)

    def get_rotated_level3_ring(self, cat: str, base_ring: pygame.Surface, angle: float, mirror: bool) -> pygame.Surface:
        return self._get_rotated(cat, base_ring, angle, mirror)

    # ── 别名 ──

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