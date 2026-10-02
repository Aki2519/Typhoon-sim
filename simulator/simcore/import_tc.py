# -*- coding: utf-8 -*-
"""注入现有 TC —— 把历史最佳路径喂进模拟。

两种模式(用户 2026-09-17 需求 4):

**A. 用完整路径 + 时间**(路径锁定, 强度模拟)
    位置由导入序列强制, 强度仍由 `intensity_step` 按环境演化。
    这是**强度评估的标准做法** —— 只有路径相同, 模拟与观测的强度差才归因于
    强度方程本身, 而不是"路径走偏了导致环境不同"。对应 `tc['track_lock']`。

**B. 只取生成点 + 时间**(种涡旋)
    在导入的生成位置/时刻放一个初生涡旋, 之后路径与强度全部自由演化。
    用来做"同一个起点会长成什么样"的集合式探索。

字段规范(JTWC b-deck, 用户提供的完整说明):
    [0]BASIN [1]CY [2]YYYYMMDDHH [3]TECHNUM [4]TECH [5]TAU
    [6]LatN/S [7]LonE/W [8]VMAX [9]MSLP [10]TY
    [11]RAD [12]WINDCODE [13-16]RAD1-4 [17]RADP [18]RRP [19]RMW
    [20]GUSTS [21]EYE [22]SUBREGION [23]MAXSEAS [24]INITIALS
    [25]DIR [26]SPEED [27]STORMNAME [28]DEPTH [29]SEAS [30]SEASCODE [31-34]SEAS1-4

**下标极易出错**(踩过: 把 [5] 当纬度, 那其实是 TAU, 于是静默收集到 0 条)。
"""
from __future__ import annotations

import datetime as _dt
import math
import os

#: 命名门槛(JTWC 口径: 34 kt 1 分钟持续风速)
NAME_KT = 34.0


def _lat(s: str):
    s = s.strip()
    if len(s) < 3 or s[-1] not in 'NS':
        return None
    try:
        v = float(s[:-1]) / 10.0
    except ValueError:
        return None
    return -v if s[-1] == 'S' else v


def _lon(s: str):
    s = s.strip()
    if len(s) < 4 or s[-1] not in 'EW':
        return None
    try:
        v = float(s[:-1]) / 10.0
    except ValueError:
        return None
    return v if s[-1] == 'E' else -v


def _f(x, d=None):
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return d


def parse_bdeck(path: str):
    """读 b-deck → [{'t':datetime,'lat','lon','vmax','pmin','rmw','name','status'}, ...]。

    按时间排序去重(同一时刻只保留最后一条)。
    """
    rows = {}
    name = ''
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for ln in f:
            p = [x.strip() for x in ln.split(',')]
            if len(p) < 10:
                continue
            ts = p[2]
            if len(ts) != 10 or not ts.isdigit():
                continue
            la, lo = _lat(p[6]), _lon(p[7])
            vm = _f(p[8])
            if la is None or lo is None or vm is None or vm <= 0:
                continue
            pm = _f(p[9])
            if pm is not None and pm > 1100:
                pm = None
            rmw = _f(p[19]) if len(p) > 19 else None      # [19] = RMW(nm)
            # 轮次 140: b-deck 有**两种方言** ——
            #   观测文件(完整 ATCF, ~28 列): 名字在 [27]([11] 是 RAD 半径, 纯数字);
            #   模型落盘文件(`stage_bdeck` 只写 12 列): 名字/扰动码在 [11], 没有 [27]。
            # 观测文件尾部空字段常被省略(实测 xrq 里多为 11 列), 所以只能按"列数够不够"
            # 判断方言。回落时排掉纯数字 —— 免得把观测方言的 RAD 半径当成名字(与
            # `tools/bdeck_summary.py` 的"是不是名字"同口径)。
            # 扰动码(`90W`)照旧**原样透传**, 这里不判"到底是不是名字"。
            # 轮次 153: 判据从"非纯数字"收紧为"**含字母**"。ATCF 的 RAD 缺测惯用值是
            # `-99`(以及 `34.0` 这类), `not isdigit()` 对它们为真 ⇒ 会把半径写成名字。
            # 名字一定含字母(`Mun`/`Nari`)或本身就是扰动码(`90W`, 含字母)。
            if len(p) > 27 and p[27]:
                name = p[27]                               # 观测方言
            elif len(p) <= 27 and len(p) > 11 and p[11] and any(
                    c.isalpha() for c in p[11]):
                name = p[11]                               # 模型方言(12 列)
            rows[ts] = {
                't': _dt.datetime(int(ts[:4]), int(ts[4:6]), int(ts[6:8]),
                                  int(ts[8:10])),
                'lat': la, 'lon': lo % 360.0, 'vmax': vm, 'pmin': pm,
                'rmw': (rmw if (rmw and 0 < rmw < 200) else None),
                'name': name,
                'status': (p[10] if len(p) > 10 else ''),
            }
    return [rows[k] for k in sorted(rows)]


def summarize(rows) -> dict:
    """给 UI 用的摘要。"""
    if not rows:
        return {}
    v = [r['vmax'] for r in rows]
    return {
        'n': len(rows),
        'name': rows[0].get('name') or '?',
        't0': rows[0]['t'], 't1': rows[-1]['t'],
        'lat0': rows[0]['lat'], 'lon0': rows[0]['lon'],
        'vmax_max': max(v),
        'hours': (rows[-1]['t'] - rows[0]['t']).total_seconds() / 3600.0,
    }


def make_from_track(sim, rows, mode: str = 'A'):
    """把导入的路径注入模拟, 返回新建的 TC(失败返回 None)。

    mode='A': 路径锁定(位置强制), 强度模拟 → `tc['track_lock']` 是一个按时间
              推进的游标, `motion_step` 检测到就跳过自由运动。
    mode='B': 只在生成点种一个涡旋。
    """
    if not rows:
        return None
    from simulator.simcore import core as V
    r0 = rows[0]
    v0 = max(20.0, min(60.0, float(r0['vmax'])))       # 初生涡旋不宜直接给巅峰强度
    tc = V.make_tc(sim, r0['lon'], r0['lat'], v0)
    if r0.get('name'):
        tc['name'] = str(r0['name'])
    if r0.get('rmw'):
        tc['rmw'] = float(r0['rmw'])
    if mode == 'A':
        tc['track_lock'] = [dict(r) for r in rows]
        tc['track_i'] = 0
        tc['importMode'] = 'A'
        tc['importT0'] = r0['t']
    else:
        tc['importMode'] = 'B'
        tc['importT0'] = r0['t']
    return tc


def track_at(tc, clock_dt):
    """mode A: 按真实时刻插值出该时刻的位置。返回 (lon, lat) 或 None。"""
    tr = tc.get('track_lock')
    if not tr:
        return None
    t0 = tc.get('importT0')
    if t0 is None:
        return None
    h = (clock_dt - t0).total_seconds() / 3600.0
    # 线性插值(路径本来是 6h 间隔, 步长也是 6h, 多数时候直接命中)
    for i in range(len(tr) - 1):
        h0 = (tr[i]['t'] - t0).total_seconds() / 3600.0
        h1 = (tr[i + 1]['t'] - t0).total_seconds() / 3600.0
        if h0 <= h <= h1:
            f = 0.0 if h1 <= h0 else (h - h0) / (h1 - h0)
            lo = tr[i]['lon'] + (tr[i + 1]['lon'] - tr[i]['lon']) * f
            la = tr[i]['lat'] + (tr[i + 1]['lat'] - tr[i]['lat']) * f
            return lo, la, tr[i]['vmax']
    last = tr[-1]
    return last['lon'], last['lat'], last['vmax']


def list_candidates(root: str, limit: int = 40):
    """列出数据目录里可导入的 b-deck 文件(给选择对话框用)。"""
    out = []
    for dp, _dn, fn in os.walk(root):
        for x in fn:
            if x.lower().endswith('.dat') and len(x) > 8:
                out.append(os.path.join(dp, x))
                if len(out) >= limit:
                    return out
    return out
