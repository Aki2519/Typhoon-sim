from __future__ import annotations

# py/statistics/data_builder_chart.py
"""数据计算：委托给 ACEEngine。"""
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from ..constants import HEMISPHERE_NORTH, HEMISPHERE_SOUTH


class ChartData:
    __slots__ = (
        'ace_curve_points', 'typhoon_ace_list', 'daily_ace_list',
        'activity_count_list', 'active_periods', 'year_total_ace',
        'year_range',
    )

    def __init__(self):
        self.ace_curve_points: List[Tuple[datetime, float]] = []
        self.typhoon_ace_list: List[Tuple[str, float]] = []
        self.daily_ace_list: List[Tuple[int, float]] = []
        self.activity_count_list: List[Tuple[int, int]] = []
        self.active_periods: List[dict] = []
        self.year_total_ace: float = 0.0
        self.year_range: Tuple[datetime, datetime, int] = (
            datetime(2000, 1, 1), datetime(2000, 12, 31, 23, 59, 59), 8760
        )


def build_chart_data(sim, year: int, cumulative_to_current: bool,
                     available_years: List[int]) -> ChartData:
    data = ChartData()
    if year < 0 or not available_years:
        return data

    engine = sim.ace_engine
    start_dt, end_dt = engine.ace_year_range(year)
    total_hours = int((end_dt - start_dt).total_seconds() / 3600)
    data.year_range = (start_dt, end_dt, total_hours)

    # 累计曲线
    timeline_cache = getattr(sim, '_ace_timeline_cache', {})
    cached = timeline_cache.get(year)
    if cached is not None:
        if cumulative_to_current and year == sim.current_ace_year:
            now = _current_sim_dt(sim)
            if now is not None and now >= start_dt:
                data.ace_curve_points = [(t, v) for t, v in cached if t <= now]
            else:
                data.ace_curve_points = cached
        else:
            data.ace_curve_points = cached

    # 台风 ACE 列表
    typhoon_cache = getattr(sim, '_ace_typhoon_cache', {})
    data.typhoon_ace_list = typhoon_cache.get(year, engine.typhoon_ace_list(year))

    data.year_total_ace = sim.yad.get(year, 0.0)

    # 每日 ACE + 活动数
    cutoff = _cutoff(sim, start_dt, end_dt, year, cumulative_to_current)
    daily = engine.daily_ace(year, cutoff)
    activity = engine.daily_activity_count(year, cutoff)
    data.daily_ace_list = [(i, a) for i, a in enumerate(daily)]
    data.activity_count_list = [(i, c) for i, c in enumerate(activity)]

    # 活跃周期
    data.active_periods = engine.active_periods(year)

    return data


def _current_sim_dt(sim) -> Optional[datetime]:
    try:
        m, d, h = int(sim.st[0:2]), int(sim.st[2:4]), int(sim.st[4:6])
        return datetime(sim.sy, m, d, h)
    except Exception:
        return None


def _cutoff(sim, start_dt, end_dt, year, cumulative_to_current):
    if not cumulative_to_current or year != sim.current_ace_year:
        return end_dt
    now = _current_sim_dt(sim)
    if now is None:
        return end_dt
    if now < start_dt:
        return start_dt
    if now > end_dt:
        return end_dt
    return now