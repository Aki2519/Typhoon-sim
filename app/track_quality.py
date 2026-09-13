# app/track_quality.py
"""路径质量度量: 逐段移速 / 跳变检测 / 平滑曲线偏离。

与渲染、回放解耦, 供调试面板(track stats)与测试复用。
单位约定: 经向 km 用 110.57 km/deg, 纬向 km 用 111.32*cos(lat) —— 与
app/statistics/chart_helpers.py 的统计口径一致(局地平面近似, 同一路径相邻点足够)。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON_EQ = 111.32
#: 单段(相邻报点之间)平均移速上限。55 kt 的快速移动台风 ≈ 102 km/h,
#: 所以 80 km/h 已经只会在数据错误/插值跳变时触发 —— 与参考模型的
#: "maxHourlyTrackKm < 80 ? continuous : review" 用同一阈值量级。
SEGMENT_SPEED_LIMIT_KMH = 80.0
#: 平滑曲线回放相对"报点直线插值"的最大允许偏离(km)。样条只为视觉平滑,
#: 不该把台风带离官方报点连线 —— 登陆判定/坐标读数/统计都跟随实时位置。
CURVE_DEV_LIMIT_KM = 25.0


def wrap_dlon(lo0: float, lo1: float) -> float:
    """经度差, 归一到 (-180, 180]; 跨 0°/180° 时不会算出半个地球的距离。"""
    d = (lo1 - lo0 + 180.0) % 360.0 - 180.0
    return d


def km_between(la1: float, lo1: float, la2: float, lo2: float) -> float:
    """两点间局地平面距离(km), 纬度取两端平均。"""
    dlat = (la2 - la1) * KM_PER_DEG_LAT
    dlon = wrap_dlon(lo1, lo2) * KM_PER_DEG_LON_EQ * math.cos(
        math.radians((la1 + la2) * 0.5))
    return math.hypot(dlat, dlon)


def km_per_deg_lon(lat: float) -> float:
    return KM_PER_DEG_LON_EQ * max(1e-6, math.cos(math.radians(lat)))


def segment_speeds(ty) -> List[Tuple[float, int]]:
    """[(平均移速 km/h, 段起点索引)]; 时间无效(非正)的段跳过。"""
    out: List[Tuple[float, int]] = []
    pts = getattr(ty, 'pts', None) or []
    times = getattr(ty, 'points_time', None) or []
    if len(times) != len(pts) or len(pts) < 2:
        return out
    for i in range(len(pts) - 1):
        dt_h = (times[i + 1] - times[i]) / 3600.0
        if dt_h <= 0:
            continue
        d = km_between(pts[i]['la'], pts[i]['lo'],
                       pts[i + 1]['la'], pts[i + 1]['lo'])
        out.append((d / dt_h, i))
    return out


def track_quality(ty, limit_kmh: float = SEGMENT_SPEED_LIMIT_KMH
                  ) -> Optional[Dict[str, Any]]:
    """路径质量报告; ty 为空或无有效段时间时返回 None。

    status: 'continuous' = 全部段≤阈值; 'review' = 存在超阈值段(数据/插值可疑)。
    """
    if ty is None:
        return None
    speeds = segment_speeds(ty)
    if not speeds:
        return None
    mx, idx = max(speeds, key=lambda kv: kv[0])
    over = [(s, i) for s, i in speeds if s > limit_kmh]
    mean = sum(s for s, _ in speeds) / len(speeds)
    return {
        'segments': len(speeds),
        'max_kmh': mx,
        'worst_index': idx,
        'mean_kmh': mean,
        'over_limit': len(over),
        'limit_kmh': limit_kmh,
        'status': 'review' if over else 'continuous',
    }


def deviation_report(ty) -> Optional[Dict[str, float]]:
    """平滑曲线相对'报点直线插值'的偏离记账(由回放时逐步记录)。"""
    if ty is None:
        return None
    v = getattr(ty, 'v', None)
    if v is None:
        return None
    n = getattr(v, '_dev_clamped', 0)
    last = getattr(v, '_dev_last_km', 0.0)
    mx = getattr(v, '_dev_max_km', 0.0)
    if last <= 0.0 and mx <= 0.0 and not n:
        return None
    return {'last_km': last, 'max_km': mx, 'clamped': int(n)}
