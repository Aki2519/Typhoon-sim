# py/ace_engine.py
"""ACE 计算引擎。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING
from collections import Counter
import bisect
import functools

from .constants import HEMISPHERE_NORTH

_TIMELINE_STEP_HOURS = 6

if TYPE_CHECKING:
    from .ty_sim import TySim
    from .typhoon import Typhoon


@functools.lru_cache(maxsize=65536)
def _parse_dt(t: str) -> Optional[datetime]:
    """法22: strptime 结果按串缓存(点时间串重复出现时免解析)。"""
    try:
        return datetime.strptime(t[:10], "%Y%m%d%H")
    except (ValueError, IndexError):
        return None


def _ace_eligible(pt: dict, strict: bool = False) -> bool:
    """ACE 资格判定: 热带性质(TS/TY/ST/HU/空)且风速 ≥ 35kt 才计
    (与 typhoon_data.recalc_ace 口径一致;B1 阈值回归修复)。"""
    st = pt['st'].upper()
    if st not in ('TS', 'TY', 'ST', 'HU', ''):
        return False
    w = pt['w']
    if not isinstance(w, (int, float)) or w < 35:
        return False
    return not strict or pt.get('official', True)


class ACEEngine:
    def __init__(self, sim: TySim) -> None:
        self._sim = sim
        self._limit_cache_key = None
        self._limit_cache_area = None

    @property
    def hemisphere(self) -> str:
        return self._sim.hemisphere

    @property
    def limit_mode(self) -> str:
        return self._sim.ace_limit_mode

    @property
    def limit_basin(self) -> str:
        return self._sim.ace_limit_basin

    @property
    def ocean_areas(self):
        return self._sim.res_mgr.ocean_areas

    @property
    def _tys(self) -> list:
        return self._sim.tys

    def _name(self, ty: Typhoon) -> str:
        return self._sim.get_display_name(ty)

    def ace_year(self, dt: datetime) -> int:
        if self.hemisphere == HEMISPHERE_NORTH:
            return dt.year
        return dt.year if dt.month >= 7 else dt.year - 1

    def ace_year_range(self, year: int) -> Tuple[datetime, datetime]:
        if self.hemisphere == HEMISPHERE_NORTH:
            return datetime(year, 1, 1, 0), datetime(year, 12, 31, 23, 59, 59)
        return datetime(year, 7, 1, 0), datetime(year + 1, 6, 30, 23, 59, 59)

    def point_in_limit(self, lat: float, lon: float) -> bool:
        sim = self._sim
        mode = sim.ace_limit_mode
        if mode == 'none':
            return True
        if mode == 'latlon':
            return (sim.ace_min_lon <= lon <= sim.ace_max_lon and
                    sim.ace_min_lat <= lat <= sim.ace_max_lat)
        if mode == 'basin' and sim.ace_limit_basin:
            key = (mode, sim.ace_limit_basin)
            if key != self._limit_cache_key:
                self._limit_cache_key = key
                self._limit_cache_area = sim.res_mgr.ocean_areas.get_by_code(sim.ace_limit_basin)
            area = self._limit_cache_area
            return area.contains(lat, lon) if area else True
        return True

    def fill_point_ace_years(self, tys: Optional[List[Typhoon]] = None) -> None:
        for ty in (tys if tys is not None else self._tys):
            for p in ty.pts:
                dt = _parse_dt(p.get('t', ''))
                if dt:
                    p['ace_year'] = self.ace_year(dt)

    def yearly_ace(self, tys: Optional[List[Typhoon]] = None) -> Dict[int, float]:
        result: Dict[int, float] = {}
        for ty in (tys if tys is not None else self._tys):
            for p in ty.pts:
                ay = p.get('ace_year', 0)
                if ay and self.point_in_limit(p['la'], p['lo']):
                    result[ay] = result.get(ay, 0.0) + p.get('pace', 0.0)
        return result

    def season_years(self, tys: Optional[List[Typhoon]] = None) -> Tuple[int, int]:
        if not (tys := tys if tys is not None else self._tys):
            return 2000, 2000
        earliest, latest = 3000, 0
        for ty in tys:
            if ty.pts:
                ft, lt = ty.pts[0]['t'], ty.pts[-1]['t']
                try:
                    if len(ft) >= 4:
                        earliest = min(earliest, int(ft[:4]))
                except ValueError:
                    pass
                try:
                    if len(lt) >= 4:
                        latest = max(latest, int(lt[:4]))
                except ValueError:
                    pass
        return (earliest if earliest != 3000 else 2000,
                latest if latest != 0 else 2000)

    def _year_events(self, ty: Typhoon, year: int):
        """该台风指定 ACE 年的过滤事件(时间排序)+前缀和缓存(法5)。
        返回 (events, pref): events=[(dt, pace)...] 排序, pref[i]=events[0..i] 的累计和。
        缓存键含 pts id/len,点编辑后自动失效。"""
        key = (id(ty.pts), len(ty.pts), year)
        cached = getattr(ty, '_ace_year_events', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        events: List[Tuple[datetime, float]] = []
        for p in ty.pts:
            if p.get('ace_year', 0) != year:
                continue
            if not self.point_in_limit(p['la'], p['lo']):
                continue
            pt_dt = _parse_dt(p['t'])
            if pt_dt:
                events.append((pt_dt, p.get('pace', 0.0)))
        events.sort(key=lambda x: x[0])
        pref: List[Tuple[datetime, float]] = []
        s = 0.0
        for dt, v in events:
            s += v
            pref.append((dt, s))
        ty._ace_year_events = (key, (events, pref))
        return events, pref

    def cumulative_ace_up_to(self, dt: datetime,
                             tys: Optional[List[Typhoon]] = None) -> float:
        ace_yr = self.ace_year(dt)
        total = 0.0
        for ty in (tys if tys is not None else self._tys):
            events, pref = self._year_events(ty, ace_yr)
            if not events:
                continue
            # 二分定位最后一个时间 <= dt 的事件(法5: O(P) → O(log n))
            pos = bisect.bisect_right(events, (dt, float('inf')))
            if pos > 0:
                total += pref[pos - 1][1]
        return total

    def daily_ace(self, year: int, cutoff: Optional[datetime] = None,
                  tys: Optional[List[Typhoon]] = None) -> List[float]:
        start, end = self.ace_year_range(year)
        start_date = start.date()
        days = (end.date() - start_date).days + 1
        if cutoff is None:
            cutoff = end
        daily = [0.0] * days
        for ty in (tys if tys is not None else self._tys):
            for pt in ty.pts:
                if pt.get('ace_year') != year:
                    continue
                if not self.point_in_limit(pt['la'], pt['lo']):
                    continue
                pt_dt = _parse_dt(pt['t'])
                if pt_dt and pt_dt <= cutoff:
                    di = (pt_dt.date() - start_date).days
                    if 0 <= di < days:
                        daily[di] += pt.get('pace', 0.0)
        return daily

    def daily_activity_count(self, year: int, cutoff: Optional[datetime] = None,
                              tys: Optional[List[Typhoon]] = None) -> List[int]:
        start, end = self.ace_year_range(year)
        start_date = start.date()
        days = (end.date() - start_date).days + 1
        if cutoff is None:
            cutoff = end
        day_sets: List[set] = [set() for _ in range(days)]
        for ty in (tys if tys is not None else self._tys):
            for pt in ty.pts:
                if pt.get('ace_year') != year:
                    continue
                if not self.point_in_limit(pt['la'], pt['lo']):
                    continue
                if not _ace_eligible(pt):
                    continue
                pt_dt = _parse_dt(pt['t'])
                if pt_dt and pt_dt <= cutoff:
                    di = (pt_dt.date() - start_date).days
                    if 0 <= di < days:
                        day_sets[di].add(id(ty))
        return [len(s) for s in day_sets]

    def typhoon_ace_list(self, year: int,
                         tys: Optional[List[Typhoon]] = None) -> List[Tuple[str, float]]:
        result: List[Tuple[str, float]] = []
        for ty in (tys if tys is not None else self._tys):
            yace = sum(p.get('pace', 0.0) for p in ty.pts
                       if p.get('ace_year') == year
                       and self.point_in_limit(p['la'], p['lo']))
            if yace > 0:
                result.append((self._name(ty), yace))
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def active_periods(self, year: int,
                       tys: Optional[List[Typhoon]] = None) -> List[dict]:
        periods: List[dict] = []
        for ty in (tys if tys is not None else self._tys):
            pts = [(idx, pt) for idx, pt in enumerate(ty.pts)
                   if pt.get('ace_year') == year
                   and pt.get('official', True)
                   and self.point_in_limit(pt['la'], pt['lo'])]
            if not pts:
                continue

            non_sp = [(i, p) for i, p in pts
                      if p['st'].upper() not in ('EX', 'SS', 'SD')]
            if not non_sp:
                continue

            first_idx, first_pt = non_sp[0]
            last_idx, last_pt = non_sp[-1]
            next_idx = last_idx + 1
            next_pt = ty.pts[next_idx] if next_idx < len(ty.pts) else last_pt

            t1, t2 = _parse_dt(first_pt['t']), _parse_dt(next_pt['t'])
            if not t1 or not t2:
                continue
            t2 = max(t1, t2)

            cand: List[Tuple[int, datetime]] = []
            if first_idx != next_idx:
                cand += [(first_idx, t1), (next_idx, t2)]
            for k in range(len(pts) - 1):
                pa, pb = pts[k][1], pts[k + 1][1]
                if pa['st'].upper() in ('EX', 'SS', 'SD') and pb['st'].upper() in ('EX', 'SS', 'SD'):
                    continue
                if _ace_eligible(pa, strict=True) != _ace_eligible(pb, strict=True):
                    dt_b = _parse_dt(pb['t'])
                    if dt_b:
                        cand.append((pts[k + 1][0], dt_b))

            cand.sort(key=lambda x: x[1])
            cnt = Counter(idx for idx, _ in cand)
            t2_times = [dt for idx, dt in cand if cnt[idx] == 1]

            vp = [p for _, p in pts
                  if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
            if vp:
                mwp = max(vp, key=lambda p: p['w'])
                mx = mwp['w']
                sc = self._sim.get_strength_category(mx, mwp.get('st', ''))
                color = self._sim.get_point_color(mx, mwp.get('st', ''))
            else:
                mx = 0
                sc = self._sim.get_strength_category(0, '')
                color = self._sim.get_point_color(0, '')

            periods.append({
                'name_str': self._name(ty),
                'name_surf': None,
                'color': color,
                'start_dt': t1,
                'end_dt': t2,
                'type2_times': t2_times,
                'basin': ty.basin,
            })
        periods.sort(key=lambda p: p['start_dt'])
        return periods

    def build_timeline_cache(self, year: int,
                              tys: Optional[List[Typhoon]] = None) -> List[Tuple[datetime, float]]:
        start, end = self.ace_year_range(year)
        events: List[Tuple[datetime, float]] = []
        for ty in (tys if tys is not None else self._tys):
            for p in ty.pts:
                if p.get('ace_year') != year or not self.point_in_limit(p['la'], p['lo']):
                    continue
                dt = _parse_dt(p['t'])
                if dt:
                    events.append((dt, p.get('pace', 0.0)))
        events.sort(key=lambda x: x[0])

        timeline: List[Tuple[datetime, float]] = []
        running = 0.0
        i = 0
        cur = start
        while cur <= end:
            while i < len(events) and events[i][0] <= cur:
                running += events[i][1]
                i += 1
            timeline.append((cur, running))
            cur += timedelta(hours=_TIMELINE_STEP_HOURS)
        return timeline

    def refresh_all(self, affected: Optional[List[Typhoon]] = None) -> None:
        """刷新 ACE 相关缓存。affected 指定(点编辑)时只重算受影响台风,
        其余台风走每台风年 ACE 缓存、其余年份 timeline/列表缓存保留(法4)。"""
        affected = affected or self._tys
        affected_set = set(id(ty) for ty in affected)
        # K9: _ace_year_events 缓存键不含 limit 因子,配置变化时必须随刷新失效
        for ty in affected:
            ty._ace_year_events = None
        self.fill_point_ace_years(affected)
        sim = self._sim
        sim._ace_data_revision = getattr(sim, '_ace_data_revision', 0) + 1
        sim.tsa = sum(ty.tace for ty in self._tys)
        sty, edy = self.season_years()
        sim.sty, sim.edy = sty, edy
        # 仅在 sim.sy 未初始化或超出有效区间时才用 sty 初始化，
        # 避免切换 ACE 设置时把当前模拟年份重置为最早台风年份
        if sim.sy is None or not (sty <= sim.sy <= edy):
            sim.sy = sty
        mode_key = (sim.ace_limit_mode, sim.ace_limit_basin)
        yad: Dict[int, float] = {}
        changed_years: set = set()
        full = affected is self._tys
        for ty in self._tys:
            key = (id(ty.pts), len(ty.pts), mode_key)
            cached = getattr(ty, '_ace_ty_yearly', None)
            if cached is not None and cached[0] == key and id(ty) not in affected_set:
                acc = cached[1]
            else:
                acc = {}
                for p in ty.pts:
                    ay = p.get('ace_year', 0)
                    if ay and self.point_in_limit(p['la'], p['lo']):
                        acc[ay] = acc.get(ay, 0.0) + p.get('pace', 0.0)
                ty._ace_ty_yearly = (key, acc)
                for ay in acc:
                    changed_years.add(ay)
            for ay, v in acc.items():
                yad[ay] = yad.get(ay, 0.0) + v
        if full:
            changed_years = set(yad)
        sim.yad = yad
        # 清理已不存在的年份条目
        for year in list(sim._ace_timeline_cache):
            if year not in yad:
                sim._ace_timeline_cache.pop(year, None)
                sim._ace_typhoon_cache.pop(year, None)
        # 只重建受影响年份的图表数据
        for year, ace in yad.items():
            if ace > 0 and (full or year in changed_years
                            or year not in sim._ace_timeline_cache):
                sim._ace_timeline_cache[year] = self.build_timeline_cache(year)
                sim._ace_typhoon_cache[year] = self.typhoon_ace_list(year)
        if hasattr(sim, '_sync_to_season_ctrl'):
            sim._sync_to_season_ctrl()
