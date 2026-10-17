# app/landfall_merge.py
"""**登陆汇总**（用户 2026-10-12 需求 2）。

需求原文："当登陆强度较弱时, 可以把一系列时间地点非常近且强度相同的登陆汇总成一次。
可以在设置中开关, 可以调一些参数, 比如使用时间模式还是地点模式, 强度允许多大的误差
(默认为 0), 时间最大差多久(使用模拟时,默认是被汇总的每一个登陆前后相差不超过3h),
地点最大差多远(默认是被汇总的每一个登陆前后不超过0.2经纬距)"

口径（写死、可复核）：
  · 只处理"同一个风暴"的登陆序列（不同风暴不互相合并）—— 一连串弱登陆通常是同一个
    系统在群岛/破碎海岸上反复上下陆。
  · **强度**：组内每个登陆与该组第一个的 |Δwind| ≤ `dw`（默认 0 = 必须相同）。
  · **判据分两种**（`mode`）：
      'time'  —— 与**组内第一个**的 |Δt| ≤ `dt_h`（默认 3h）
      'place' —— 与**组内第一个**的距离 ≤ `dist_deg` 经纬距（默认 0.2）
    `mode` 决定用哪一个；另一个参数在该模式下不参与（避免"两个都必须满足"让模式失去意义）。
  · 合并后取组内**最大强度**作为该次登陆的强度（弱序列里最强的那一下），
    经纬度/时间取组内第一个，并记 `merged` = 被并掉的条数。
"""
from __future__ import annotations

from datetime import datetime
from math import hypot


def _parse_t(t):
    for fmt in ('%Y%m%d%H', '%Y%m%d%H%M'):
        try:
            return datetime.strptime(str(t), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _dt_hours(a, b):
    """两条记录的时间差（小时）。优先用数值小时 `th`（地图标记那条路没有时间字符串），
    否则解析 `t`（b-deck 的 'YYYYMMDDHH'）。"""
    if a is not None and b is not None:
        try:
            return abs(float(a) - float(b))
        except (TypeError, ValueError):
            pass
    ta, tb = _parse_t(a), _parse_t(b)
    if ta is None or tb is None:
        return None
    return abs((ta - tb).total_seconds()) / 3600.0


def _dist_deg(a, b):
    try:
        return hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
    except (TypeError, ValueError):
        return None


def merge_landfalls(records, *, enabled=True, mode='time', dw=0,
                    dt_h=3.0, dist_deg=0.2):
    """把"非常近且强度相同"的连续登陆并成一次。返回新列表（不改原列表）。

    `records` 每条至少要有 `name/wind/la/lo`；`t`（'YYYYMMDDHH'）与 `year` 可选。
    `enabled=False` 时原样返回（**默认就是关的** ⇒ 现有统计一个字节不变）。
    """
    if not enabled:
        return list(records)

    def _sk(r):
        """排序键。⚠ 地图标记那条路的 `t` 是**段内比例(0~1)**，不是时间 ——
        必须优先用数值小时 `th`，否则会把同一条轨迹上的登陆点按"比例"打乱，
        相邻关系一断就一条都并不了（本轮实测踩到）。"""
        th = r.get('th')
        if th is not None:
            return (r.get('year', 0), 0, float(th))
        return (r.get('year', 0), 1, str(r.get('t', '') or ''))

    out = []
    i = 0
    ordered = sorted(records, key=_sk)
    n = len(ordered)
    while i < n:
        head = dict(ordered[i])
        group = [head]
        j = i + 1
        while j < n:
            r = ordered[j]
            prev = group[-1]
            # 不同风暴 / 不同年 一律断开（统计是按"同一风暴同年"做的）
            if (r.get('name') != head.get('name')
                    or r.get('year', 0) != head.get('year', 0)):
                break
            # 强度：与**组内第一个**比（"强度相同"是整组的性质）
            if abs(int(r.get('wind', 0) or 0) - int(head.get('wind', 0) or 0)) > int(dw):
                break
            # 距离/时间：与**紧邻的前一条**比（需求原话"被汇总的每一个登陆**前后**相差
            # 不超过 3h" ⇒ 相邻比较，不是"都跟第一条比" —— 否则一整串弱登陆只要
            # 中间断一次就全被拆散）
            if mode == 'place':
                d = _dist_deg((r.get('la'), r.get('lo')), (prev.get('la'), prev.get('lo')))
                if d is None or d > float(dist_deg):
                    break
            else:
                dh = _dt_hours(r.get('th'), prev.get('th'))
                if dh is None:
                    dh = _dt_hours(r.get('t'), prev.get('t'))
                if dh is None or dh > float(dt_h):
                    break
            group.append(r)
            j += 1
        if len(group) > 1:
            head['wind'] = max(int(g.get('wind', 0) or 0) for g in group)
            head['merged'] = len(group) - 1
        out.append(head)
        i = j
    return out


def landfall_label(idx: int, total: int, wind) -> str:
    """地图标注文本（用户 2026-10-12）。

    需求: "landfall 需要标上第几次, 也就是 landfall1 这样; landfall 的次数要跳过
    被汇总的（汇总后只算一次）"。
    编号口径与巅峰标注的 Peak / PeakN **完全一致**：只有多于一次时才编号；
    这里的 total 传的是**汇总后**的次数 ⇒ 被并掉的那些自然不占编号。
    """
    pref = ('Landfall%d' % (int(idx) + 1)) if int(total) > 1 else 'Landfall'
    return '%s %dkt' % (pref, int(wind))
