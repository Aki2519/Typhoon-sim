# py/ace_engine.py
"""ACE 计算引擎。"""
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
from collections import Counter

from .constants import HEMISPHERE_NORTH


def _parse_dt(t: str) -> Optional[datetime]:
    """解析 '%Y%m%d%H' 格式的时间字符串，失败返回 None。"""
    try:
        return datetime.strptime(t[:10], "%Y%m%d%H")
    except (ValueError, IndexError):
        return None


def _ace_eligible(pt: dict, strict: bool = False) -> bool:
    """判断报点是否可计算 ACE。strict=True 时要求 official。"""
    st = pt['st'].upper()
    w = pt['w']
    if st not in ('TS', 'TY', 'ST', 'HU', ''):
        return False
    if not isinstance(w, (int, float)) or w < 35:
        return False
    return not strict or pt.get('official', True)


class ACEEngine:
    def __init__(self, sim):
        self._sim = sim

    # ── 属性 ──

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
    def _tys(self):
        return self._sim.tys

    def _name(self, ty) -> str:
        return self._sim.get_display_name(ty)

    # ── 年份 ──

    def ace_year(self, dt: datetime) -> int:
        if self.hemisphere == HEMISPHERE_NORTH:
            return dt.year
        return dt.year if dt.month >= 7 else dt.year - 1

    def ace_year_range(self, year: int) -> Tuple[datetime, datetime]:
        if self.hemisphere == HEMISPHERE_NORTH:
            return datetime(year, 1, 1, 0), datetime(year, 12, 31, 23, 59, 59)
        return datetime(year, 7, 1, 0), datetime(year + 1, 6, 30, 23, 59, 59)

    # ── 地理限制 ──

    def point_in_limit(self, lat: float, lon: float) -> bool:
        mode = self.limit_mode
        if mode == 'none':
            return True
        if mode == 'latlon':
            return (self._sim.ace_min_lon <= lon <= self._sim.ace_max_lon and
                    self._sim.ace_min_lat <= lat <= self._sim.ace_max_lat)
        if mode == 'basin' and self.limit_basin:
            area = self.ocean_areas.get_by_code(self.limit_basin)
            return area.contains(lat, lon) if area else True
        return True

    # ── 填充 ace_year ──

    def fill_point_ace_years(self, tys=None) -> None:
        for ty in (tys or self._tys):
            for p in ty.pts:
                dt = _parse_dt(p.get('t', ''))
                if dt:
                    p['ace_year'] = self.ace_year(dt)

    # ── 年度 ACE ──

    def yearly_ace(self, tys=None) -> Dict[int, float]:
        result: Dict[int, float] = {}
        for ty in (tys or self._tys):
            for p in ty.pts:
                ay = p.get('ace_year', 0)
                if ay and self.point_in_limit(p['la'], p['lo']):
                    result[ay] = result.get(ay, 0.0) + p.get('pace', 0.0)
        return result

    # ── 风季年份范围 ──

    def season_years(self, tys=None) -> Tuple[int, int]:
        if not (tys := tys or self._tys):
            return 2000, 2000
        earliest, latest = 3000, 0
        for ty in tys:
            if ty.pts:
                ft, lt = ty.pts[0]['t'], ty.pts[-1]['t']
                if len(ft) >= 4:
                    earliest = min(earliest, int(ft[:4]))
                if len(lt) >= 4:
                    latest = max(latest, int(lt[:4]))
        return (earliest if earliest != 3000 else 2000,
                latest if latest != 0 else 2000)

    # ── 累计 ACE ──

    def cumulative_ace_up_to(self, dt: datetime, tys=None) -> float:
        ace_yr = self.ace_year(dt)
        start, _ = self.ace_year_range(ace_yr)
        total = 0.0
        for ty in (tys or self._tys):
            for p in ty.pts:
                if p.get('ace_year', 0) != ace_yr:
                    continue
                if not self.point_in_limit(p['la'], p['lo']):
                    continue
                pt_dt = _parse_dt(p['t'])
                if pt_dt and start <= pt_dt <= dt:
                    total += p.get('pace', 0.0)
        return total

    # ── 每日 ACE ──

    def daily_ace(self, year: int, cutoff=None, tys=None) -> List[float]:
        start, end = self.ace_year_range(year)
        days = (end.date() - start.date()).days + 1
        if cutoff is None:
            cutoff = end
        daily = [0.0] * days
        for ty in (tys or self._tys):
            for pt in ty.pts:
                if pt.get('ace_year') != year:
                    continue
                if not self.point_in_limit(pt['la'], pt['lo']):
                    continue
                pt_dt = _parse_dt(pt['t'])
                if pt_dt and pt_dt <= cutoff:
                    di = (pt_dt.date() - start.date()).days
                    if 0 <= di < days:
                        daily[di] += pt.get('pace', 0.0)
        return daily

    # ── 每日活动台风数 ──

    def daily_activity_count(self, year: int, cutoff=None, tys=None) -> List[int]:
        start, end = self.ace_year_range(year)
        days = (end.date() - start.date()).days + 1
        if cutoff is None:
            cutoff = end
        day_sets: List[set] = [set() for _ in range(days)]
        for ty in (tys or self._tys):
            for pt in ty.pts:
                if pt.get('ace_year') != year:
                    continue
                if not self.point_in_limit(pt['la'], pt['lo']):
                    continue
                if not _ace_eligible(pt):
                    continue
                pt_dt = _parse_dt(pt['t'])
                if pt_dt and pt_dt <= cutoff:
                    di = (pt_dt.date() - start.date()).days
                    if 0 <= di < days:
                        day_sets[di].add(id(ty))
        return [len(s) for s in day_sets]

    # ── 台风 ACE 列表 ──

    def typhoon_ace_list(self, year: int, tys=None) -> List[Tuple[str, float]]:
        result = []
        for ty in (tys or self._tys):
            yace = sum(p.get('pace', 0.0) for p in ty.pts if p.get('ace_year') == year)
            if yace > 0:
                result.append((self._name(ty), yace))
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    # ── 活跃周期 ──

    def active_periods(self, year: int, tys=None) -> List[dict]:
        periods = []
        for ty in (tys or self._tys):
            pts = [(idx, pt) for idx, pt in enumerate(ty.pts)
                   if pt.get('ace_year') == year
                   and pt.get('official', True)
                   and self.point_in_limit(pt['la'], pt['lo'])]
            if not pts:
                continue

            non_sp = [(i, p) for i, p in pts if p['st'].upper() not in ('EX', 'SS', 'SD')]
            if not non_sp:
                continue

            first_idx, first_pt = non_sp[0]
            last_idx, last_pt = non_sp[-1]
            next_idx = last_idx + 1
            next_pt = ty.pts[next_idx] if next_idx < len(ty.pts) else last_pt

            t1, t2 = _parse_dt(first_pt['t']), _parse_dt(next_pt['t'])
            if not t1 or not t2:
                continue

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

            vw = [p['w'] for _, p in pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
            mx = max(vw) if vw else 0
            sc = self._sim.get_strength_category(mx, '')
            color = self._sim.get_point_color(mx, sc)

            periods.append({
                'name_str': self._name(ty),
                'name_surf': None,
                'color': color,
                'start_dt': t1,
                'end_dt': t2,
                'type2_times': t2_times,
            })
        periods.sort(key=lambda p: p['start_dt'])
        return periods

    # ── 累计曲线缓存 ──

    def build_timeline_cache(self, year: int, tys=None) -> List[Tuple[datetime, float]]:
        start, end = self.ace_year_range(year)
        events: List[Tuple[datetime, float]] = []
        for ty in (tys or self._tys):
            for p in ty.pts:
                if p.get('ace_year') != year or not self.point_in_limit(p['la'], p['lo']):
                    continue
                dt = _parse_dt(p['t'])
                if dt:
                    events.append((dt, p.get('pace', 0.0)))
        events.sort(key=lambda x: x[0])

        timeline, running, i = [], 0.0, 0
        cur = start
        while cur <= end:
            while i < len(events) and events[i][0] <= cur:
                running += events[i][1]
                i += 1
            timeline.append((cur, running))
            cur += timedelta(hours=6)
        return timeline

    # ── 全量刷新 ──

    def refresh_all(self) -> None:
        self.fill_point_ace_years()
        sim = self._sim
        sim.tsa = sum(ty.tace for ty in self._tys)
        sty, edy = self.season_years()
        sim.sty, sim.edy, sim.sy = sty, edy, sty
        sim.yad = self.yearly_ace()
        sim._ace_timeline_cache.clear()
        sim._ace_typhoon_cache.clear()
        for year, ace in sim.yad.items():
            if ace > 0:
                sim._ace_timeline_cache[year] = self.build_timeline_cache(year)
                sim._ace_typhoon_cache[year] = self.typhoon_ace_list(year)