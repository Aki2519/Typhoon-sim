# py/statistics/season_stats.py
"""洋区统计数据计算模块。"""
from __future__ import annotations
import functools
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .chart_helpers import compute_path_km_in_window


def _ace_eligible(pt: dict) -> bool:
    """判断报点是否可计算 ACE：热带性质(TS/TY/ST/HU)+风速≥35kt+正式报
    (与 ace_engine._ace_eligible / typhoon_data.recalc_ace 同口径, R25)。
    单趟化重构时曾漏掉 w>=35 下限,已恢复。"""

    st = (pt.get('st') or '').upper()
    return (st in ('TS', 'TY', 'ST', 'HU', '')
            and isinstance(pt.get('w'), (int, float))
            and pt['w'] >= 35
            and pt.get('official', True))


# ── 洋区统计主函数 ──
def calculate_season_stats(
    sim,
    year: int,
    basin_code: Optional[str] = None
) -> Dict:
    """计算指定洋区/全局的年度统计数据。"""
    engine = sim.ace_engine
    start_dt, end_dt = engine.ace_year_range(year)
    ocean_areas = sim.res_mgr.ocean_areas

    # 获取指定洋区的边界（用于距离过滤）
    basin_area = None
    if basin_code:
        basin_area = ocean_areas.get_by_code(basin_code)

    # ── 汇总数据结构 ──
    stats = {
        # 计数类
        'total_systems': 0,         # 所有系统
        'total_td': 0,              # TD+
        'total_ts': 0,              # TS+
        'total_ty': 0,              # C1+
        'total_mh': 0,              # C3+
        'total_c5': 0,              # C5+
        # 极值
        'wind_king': None,          # (typhoon display_name, max_wind)
        'ace_king': None,           # (typhoon display_name, ace)
        'landfall_king': None,      # (typhoon display_name, landfall_wind)
        'lifetime_king': None,      # (typhoon display_name, lifetime_hours)
        # 累计
        'total_ace': 0.0,
        'total_active_hours': 0.0,   # TS+ 活跃时间（去重叠）
        'storm_days': 0.0,          # 正式报中活跃的报点数 ×0.25
        'landfall_count': 0,
        'total_path_km': 0.0,      # TS+ 路径总长度
    }

    # 临时数据
    ts_intervals_all = []
    storm_day_set = set()

    for ty in sim.tys:
        # 筛选当年报点(R4: 同时套用 ACE 生效地理范围 point_in_limit,与 yad/热力图/
        # monthly_summary 同口径——此前在 latlon 限制下会漏掉该过滤,导致统计值偏离
        # ACE 限制视图;mode='none' 时恒 True,不影响默认全局统计)。
        year_pts = [p for p in ty.pts
                    if p.get('ace_year') == year
                    and engine.point_in_limit(p['la'], p['lo'])]
        if not year_pts:
            continue

        # 筛选洋区内报点（只算在洋区内的数据）
        if basin_area:
            basin_pts = [p for p in year_pts if basin_area.contains(p['la'], p['lo'])]
            if not basin_pts:
                continue
        else:
            basin_pts = year_pts

        # 系统整体数据（仅洋区内报点，单趟合并峰值/ACE/风暴数/ts_pts/风暴日，法21）
        max_wind = 0
        ace = 0.0
        has_td = has_ts = has_ty = has_mh = has_c5 = False
        ts_pts = []
        for p in basin_pts:
            w = p.get('w', 0)
            st_u = (p.get('st') or '').upper()
            if st_u in ('MD', 'SS', 'SD', 'EX', 'LO'):
                continue
            # K16: 风王只统计热带性质报点(与总结条口径一致)
            if w > max_wind:
                max_wind = w
            ace += p.get('pace', 0.0)
            if w >= 137: has_c5 = True
            if w >= 96: has_mh = True       # MH = C3+(>=96kt), 与 monthly_summary 同口径
            if w >= 64: has_ty = True
            if w >= 35: has_ts = True
            if w >= 29: has_td = True
            if _ace_eligible(p):
                ts_pts.append(p)
                if p.get('official', True):
                    dt = _parse_time(p['t'])
                    if dt:
                        storm_day_set.add(dt.strftime('%Y%m%d%H'))
        name = sim.get_display_name(ty)

        stats['total_systems'] += 1
        if has_td: stats['total_td'] += 1
        if has_ts: stats['total_ts'] += 1
        if has_ty: stats['total_ty'] += 1
        if has_mh: stats['total_mh'] += 1
        if has_c5: stats['total_c5'] += 1

        # 风王
        if stats['wind_king'] is None or max_wind > stats['wind_king'][1]:
            stats['wind_king'] = (name, max_wind)
        # 累加总 ACE
        stats['total_ace'] += ace
        # ACE 王
        if stats['ace_king'] is None or ace > stats['ace_king'][1]:
            stats['ace_king'] = (name, ace)

        # 路径长度（仅洋区内 TS+ 报点间，按 ACE 年窗口切分跨年台风，F1/N4）
        # 复用上方单趟循环已收集的 ts_pts（法21：不再全量二次重扫 basin_pts）
        if sim.hemisphere == 'south':
            win = (datetime(year, 7, 1, 0), datetime(year + 1, 7, 1, 0))
            path_km = compute_path_km_in_window(ts_pts, year, win)
        else:
            path_km = compute_path_km_in_window(ts_pts, year)
        stats['total_path_km'] += path_km

        # TS+ 活跃时间段（去重叠，仅洋区内）
        if ts_pts:
            # 按时间排序后取首尾差(乱序数据下避免负值/重复计入)
            ts_sorted = sorted(ts_pts, key=lambda p: _parse_time(p['t']) or datetime(2000, 1, 1))
            first_t = _parse_time(ts_sorted[0]['t'])
            last_t = _parse_time(ts_sorted[-1]['t'])
            if first_t and last_t:
                duration = (last_t - first_t).total_seconds() / 3600.0
                ts_intervals_all.append((first_t, last_t))
                if stats['lifetime_king'] is None or duration > stats['lifetime_king'][1]:
                    stats['lifetime_king'] = (name, duration)

    # 风暴天
    stats['storm_days'] = len(storm_day_set) * 0.25

    # 登陆次数（从 sim.landfall_records 统计 + 从台风数据直接检测）
    lf_records = getattr(sim, 'landfall_records', [])
    for lf in lf_records:
        if lf.get('year') == year and (basin_code is None or lf.get('basin') == basin_code):
            stats['landfall_count'] += 1
            if stats['landfall_king'] is None or lf.get('wind', 0) > stats['landfall_king'][1]:
                stats['landfall_king'] = (lf.get('name', ''), lf.get('wind', 0))

    # 补充：从台风数据直接检测登陆（不依赖播放时累积的记录）
    if stats['landfall_count'] == 0:
        _compute_landfalls_from_data(sim, year, basin_code, stats)

    # 总活跃时间（去掉重叠）
    stats['total_active_hours'] = _merge_intervals(ts_intervals_all)

    return stats


def _compute_landfalls_from_data(sim, year, basin_code, stats):
    """从台风数据点检测登陆事件（用于没有播放记录时的统计）。
    登陆强度使用登陆前一报的数据（不要插值）。
    采样基于地理坐标（视图无关，与播放时登陆判定一致）。"""
    mm = sim.map_mgr
    if mm._load_land_orig() is None:
        return
    for ty in sim.tys:
        year_pts = [p for p in ty.pts if p.get('ace_year') == year]
        if not year_pts:
            continue
        if basin_code:
            area = sim.res_mgr.ocean_areas.get_by_code(basin_code)
            if area is None:
                continue
            if not any(area.contains(p['la'], p['lo']) for p in year_pts):
                continue
        name = sim.get_display_name(ty)
        prev_on_land = None
        prev_p = None
        for p in year_pts:
            cur_on_land = mm.is_land_at_geo(p['la'], p['lo'])
            if prev_on_land is False and cur_on_land is True:
                stats['landfall_count'] += 1
                # 使用登陆前一报的强度（prev_p），不要插值后的当前报
                if prev_p is not None:
                    w = prev_p.get('w', p.get('w', 0))
                else:
                    w = p.get('w', 0)
                if stats['landfall_king'] is None or w > stats['landfall_king'][1]:
                    stats['landfall_king'] = (name, w)
            prev_on_land = cur_on_land
            prev_p = p


@functools.lru_cache(maxsize=65536)
def _parse_time(t: str) -> Optional[datetime]:
    try:
        if len(t) >= 10:
            return datetime.strptime(t[:10], "%Y%m%d%H")
    except (ValueError, TypeError):
        pass
    return None


def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0.0
    intervals.sort(key=lambda x: x[0])
    merged = []
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    total = sum((end - start).total_seconds() / 3600.0 for start, end in merged)
    return total