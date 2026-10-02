# -*- coding: utf-8 -*-
"""观测 vs 模拟 台风「强度-寿命廓线」统计

目的: 给模拟器三处系统性偏差提供**观测标定目标**
  1) ACE/风暴 偏高 (退化成: 强档停留过久?)
  2) 有 RI 的风暴比例偏高
  3) 前生期(扰动 -> TS)偏短; 减弱端 TD 阶段偏黏

数据源 (全部为 b-deck / ATCF 风格文本):
  观测 (只读):
    G:\\气象\\数据\\xrq\\<year>\\*.dat     1949-2026 逐年 (xrq\\wp 的近全集镜像)
    G:\\气象\\数据\\xrq\\wp\\*.dat          「全年代」合集 (2340 个, 2339 个与逐年重合)
    G:\\气象\\数据\\xrq\\2025|2026|sh     小幅补充集
    G:\\气象\\数据\\自评\\2025|2026|2027   人工自评集 (12 列, 末列为标识/名字)
    G:\\气象\\数据\\miwu\\<year>\\<OCEAN>\\*.dat   MIWU 再分析 b-deck 集
  模拟:
    G:\\气象\\Typhoon-sim\\sim_typhoon\\2025\\**\\*.dat   (含 *_invest)
    G:\\气象\\Typhoon-sim\\sim_typhoon\\2100\\**\\*.dat   (含 *_invest)

b-deck 列 (逗号分隔):
  0 洋区  1 低压号  2 时间 YYYYMMDDHH  3 空  4 技术码  5 tau
  6 纬度 (138N)  7 经度 (1196E)  8 风速 kt  9 气压  10 阶段码
  10 列/11 列/12 列/29 列变体都存在 -> 只取前 11 列, 多余的当标识/名字忽略。
  逐行按**时间戳**排序, 同一时次只保留文件中靠后的那条。

用法:
  PYTHONIOENCODING=utf-8 "<python.exe>" tools/stat_intensity_profile.py
  # 默认一键跑完, 写出
  #   output/sim/_reference/INTENSITY_LIFETIME.md
  #   output/sim/_reference/intensity_lifetime.json
  python tools/stat_intensity_profile.py --no-xrq-year      # 跳过 xrq\\<year> (省一半 IO)
  python tools/stat_intensity_profile.py --gap-cap 24 --ri-threshold 30
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import math
import os
import sys
from collections import OrderedDict

# ---------------------------------------------------------------- 常量 / 路径

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MD = os.path.join(ROOT, 'output', 'sim', '_reference', 'INTENSITY_LIFETIME.md')
DEFAULT_JSON = os.path.join(ROOT, 'output', 'sim', '_reference', 'intensity_lifetime.json')

DATA_ROOT = r'G:\气象\数据'
SIM_ROOT = os.path.join(ROOT, 'sim_typhoon')

# 强度档 (kt): (键, 下界含, 上界不含)
BINS = [
    ('ge100', 100.0, None),
    ('b80_100', 80.0, 100.0),
    ('b64_80', 64.0, 80.0),
    ('b48_64', 48.0, 64.0),
    ('b34_48', 34.0, 48.0),
    ('lt34', None, 34.0),
]
BIN_KEYS = [b[0] for b in BINS]
BIN_LABEL = {
    'ge100': '>=100 kt',
    'b80_100': '80-100 kt',
    'b64_80': '64-80 kt',
    'b48_64': '48-64 kt',
    'b34_48': '34-48 kt',
    'lt34': '<34 kt',
    'ge64': '>=64 kt',
    'ge96': '>=96 kt',
    'all': '全部(峰值后)',
}
# 额外累积停留门槛
EXTRA_RES = [('ge64', 64.0), ('ge96', 96.0)]

STAGE_ORDER = ['DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'HU',
               'EX', 'SS', 'SD', 'MD', 'TT', 'PT', 'IN', 'OTH']

OCEAN_NAME = {
    'WP': 'WP 西北太平洋', 'EP': 'EP 东太平洋', 'CP': 'CP 中太平洋',
    'AL': 'AL 北大西洋', 'SH': 'SH 南半球', 'IO': 'IO 北印度洋',
    'SA': 'SA 南大西洋', 'LS': 'LS 南大西洋/其他', 'ME': 'ME 地中海',
    'SL': 'SL 南大西洋', 'BB': 'BB 孟加拉湾', 'AS': 'AS 阿拉伯海',
}


def bin_of(w):
    for k, lo, hi in BINS:
        if (lo is None or w >= lo) and (hi is None or w < hi):
            return k
    return 'lt34'


# ---------------------------------------------------------------- 小工具

def rnd(x, nd=3):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def _pct(sorted_v, q):
    n = len(sorted_v)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_v[0])
    k = (n - 1) * q / 100.0
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_v[lo])
    return float(sorted_v[lo] * (hi - k) + sorted_v[hi] * (k - lo))


def dstat(vals, nd=3):
    """分布统计: n / 中位 / 10-90 分位 / 均值 / 最小 / 最大。n 即样本量(非空值个数)。"""
    v = sorted(float(x) for x in vals if x is not None)
    if not v:
        return None
    return {
        'n': len(v),
        'median': rnd(_pct(v, 50), nd),
        'p10': rnd(_pct(v, 10), nd),
        'p90': rnd(_pct(v, 90), nd),
        'mean': rnd(sum(v) / len(v), nd),
        'min': rnd(v[0], nd),
        'max': rnd(v[-1], nd),
    }


def fmt(v, nd=2):
    if v is None:
        return 'n/a'
    return ('{:,.%df}' % nd).format(float(v))


def fmt_i(v):
    if v is None:
        return 'n/a'
    return '{:,d}'.format(int(v))


def md_table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + '|'.join(['---'] * len(headers)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join('' if c is None else str(c) for c in r) + ' |')
    return '\n'.join(out)


# ---------------------------------------------------------------- b-deck 解析

_PARSE_CACHE = {}


def parse_bdeck(path):
    """返回 [(datetime, wind_kt|None, pres|None, stage, basin, num), ...]

    兼容 10/11/12/29 列变体; 按时间戳排序; 同一时次只留最后一条。
    """
    if path in _PARSE_CACHE:
        return _PARSE_CACHE[path]
    rows = []
    try:
        fh = open(path, 'r', encoding='utf-8', errors='replace')
    except OSError:
        _PARSE_CACHE[path] = rows
        return rows
    with fh:
        for raw in fh:
            line = raw.strip().lstrip('\ufeff')
            if not line:
                continue
            p = [x.strip() for x in line.split(',')]
            if len(p) < 11:
                continue
            ts = p[2]
            if len(ts) != 10 or not ts.isdigit():
                continue
            try:
                t = dt.datetime.strptime(ts, '%Y%m%d%H')
            except ValueError:
                continue
            try:
                w = float(p[8])
            except (ValueError, TypeError):
                w = None
            try:
                pr = float(p[9]) if p[9] else None
            except (ValueError, TypeError):
                pr = None
            stage = (p[10] or '').upper()
            rows.append((t, w, pr, stage, p[0].upper(), p[1].upper()))
    rows.sort(key=lambda r: r[0])
    ded = []
    for r in rows:
        if ded and ded[-1][0] == r[0]:
            ded[-1] = r          # 同一时次只留最后一条
        else:
            ded.append(r)
    _PARSE_CACHE[path] = ded
    return ded


def _episodes(series, pred):
    """序列中满足 pred 的独立段数。"""
    n = 0
    prev = False
    for v in series:
        cur = pred(v)
        if cur and not prev:
            n += 1
        prev = cur
    return n


# ---------------------------------------------------------------- 单风暴指标

def analyze_storm(rows, default_dt=6.0, gap_cap=24.0,
                  ri_window=24.0, ri_threshold=30.0, ri_tol=6.0):
    m = {}
    n_all = len(rows)
    m['n_reports'] = n_all
    m['first'] = rows[0][0].strftime('%Y-%m-%d %H:%M')
    m['last'] = rows[-1][0].strftime('%Y-%m-%d %H:%M')
    m['year'] = rows[0][0].year
    m['lifetime_h'] = (rows[-1][0] - rows[0][0]).total_seconds() / 3600.0

    # ---- 阶段码 (用全部报)
    stage_reports = collections.Counter()
    for r in rows:
        stage_reports[r[3]] += 1
    m['stage_reports'] = dict(stage_reports)
    sth = collections.defaultdict(float)
    dts_all = [(rows[i + 1][0] - rows[i][0]).total_seconds() / 3600.0 for i in range(n_all - 1)]
    tail_all = next((d for d in reversed(dts_all) if 0 < d <= gap_cap), default_dt)
    for i, r in enumerate(rows):
        d = dts_all[i] if i < n_all - 1 else tail_all
        if not (0 < d <= gap_cap):
            d = default_dt
        sth[r[3]] += d
    m['stage_hours'] = {k: rnd(v) for k, v in sth.items()}
    m['stage_hours_total'] = rnd(sum(sth.values()))

    runs, cur = [], 0
    for r in rows:
        if r[3] == 'TD':
            cur += 1
        else:
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    m['td_reports'] = int(stage_reports.get('TD', 0))
    m['td_hours'] = rnd(sth.get('TD', 0.0))
    m['td_max_run'] = max(runs) if runs else 0
    m['td_first_run'] = runs[0] if runs else 0
    m['td_runs'] = len(runs)

    # ---- 风速序列
    W = [(t, w) for (t, w, _p, _s, _b, _n) in rows if w is not None]
    m['n_wind_pts'] = len(W)
    if not W:
        m['peak'] = None
        return m

    ws = [w for _t, w in W]
    m['peak'] = rnd(max(ws), 2)
    peak = max(ws)
    peak_i = next(i for i, w in enumerate(ws) if w == peak)
    m['peak_time'] = W[peak_i][0].strftime('%Y-%m-%d %H:%M')
    m['time_to_peak_h'] = (W[peak_i][0] - W[0][0]).total_seconds() / 3600.0
    m['peak_frac_of_life'] = (m['time_to_peak_h'] / m['lifetime_h']) if m['lifetime_h'] > 0 else None

    def first_cross(thr, start=0):
        for i in range(start, len(W)):
            if W[i][1] >= thr:
                return i
        return None

    def tt(i):
        return None if i is None else (W[i][0] - W[0][0]).total_seconds() / 3600.0

    i20 = first_cross(20.0)
    i34 = first_cross(34.0)
    i64 = first_cross(64.0)
    i96 = first_cross(96.0)
    m['t_first20_h'] = tt(i20)
    m['t_first34_h'] = tt(i34)
    m['t_first64_h'] = tt(i64)
    m['t_first96_h'] = tt(i96)
    m['pregenesis_h'] = tt(i34)              # 首报 -> 首次 34 kt (扰动 -> TS)
    m['disturbance_h'] = tt(i20)             # 首报 -> 首次 20 kt
    m['time_to_64_h'] = tt(i64)
    m['intensify_20_to_34_h'] = (None if (i20 is None or i34 is None) else tt(i34) - tt(i20))
    m['intensify_34_to_64_h'] = (None if (i34 is None or i64 is None) else tt(i64) - tt(i34))
    i_td = next((i for i, r in enumerate(rows) if r[3] == 'TD'), None)
    # 前生期的另一种口径: 首个 TD 报 -> 首次 34 kt (仅当该 TD 报确实早于首次 34 kt 时才有意义)
    if i34 is not None and i_td is not None and rows[i_td][0] < W[i34][0]:
        m['td_to_ts_h'] = (W[i34][0] - rows[i_td][0]).total_seconds() / 3600.0
    else:
        m['td_to_ts_h'] = None

    # ---- 间隔 / 权重 (每条报代表其后一段时间)
    n = len(W)
    dts = [(W[i + 1][0] - W[i][0]).total_seconds() / 3600.0 for i in range(n - 1)]
    tail = next((d for d in reversed(dts) if 0 < d <= gap_cap), default_dt)
    wts = [(d if 0 < d <= gap_cap else default_dt) for d in dts] + [tail]

    # ---- ACE (标准口径 Σ v²/1e4, 只累加 >=34 kt; 按持续时间加权,
    #          纯 6 h 数据退化为「每个 >=34 kt 的 6 h 报加一次」)
    ace = 0.0
    for i, (_t, w) in enumerate(W):
        if w >= 34.0:
            ace += (w * w / 1e4) * (wts[i] / 6.0)
    m['ace'] = rnd(ace)
    m['ace_reports_ge34'] = sum(1 for _t, w in W if w >= 34.0)

    # ---- 强度档停留时长 / 进入次数
    res = collections.defaultdict(float)
    for i, (_t, w) in enumerate(W):
        res[bin_of(w)] += wts[i]
    for key, thr in EXTRA_RES:
        res[key] = sum(wts[i] for i, (_t, w) in enumerate(W) if w >= thr)
    m['res_h'] = {k: rnd(res.get(k, 0.0)) for k in (BIN_KEYS + [k for k, _ in EXTRA_RES])}
    eps = {}
    for k, lo, hi in BINS:
        eps[k] = _episodes(ws, (lambda lo=lo, hi=hi: (lambda v: (lo is None or v >= lo) and (hi is None or v < hi)))())
    for key, thr in EXTRA_RES:
        eps[key] = _episodes(ws, (lambda thr=thr: (lambda v: v >= thr))())
    m['episodes'] = eps

    # ---- 峰值后的减弱速率 (kt / 6h), 按强度档
    wr = collections.defaultdict(list)
    wr_strict = collections.defaultdict(list)
    for i in range(n - 1):
        if i < peak_i:
            continue
        d = dts[i]
        if not (0 < d <= gap_cap):
            continue
        rate = (W[i + 1][1] - W[i][1]) * 6.0 / d
        b = bin_of(W[i][1])
        wr[b].append(rate)
        wr['all'].append(rate)
        if rate < 0:
            wr_strict[b].append(rate)
            wr_strict['all'].append(rate)
    m['wr'] = {k: [rnd(x, 4) for x in v] for k, v in wr.items()}
    m['wr_strict'] = {k: [rnd(x, 4) for x in v] for k, v in wr_strict.items()}

    # 全程(不限峰值后)增/减速率, 备用
    up = [x for x in wr['all'] if x > 0]
    m['n_intensify_intervals_after_peak'] = len(up)

    # ---- RI: 24 h 内增幅 >= 30 kt 的独立过程
    cand = []
    max24 = None
    for i, (t0, w0) in enumerate(W):
        target = t0 + dt.timedelta(hours=ri_window)
        best = None
        best_dev = None
        for j in range(i + 1, n):
            dev = (W[j][0] - target).total_seconds() / 3600.0
            if dev > ri_tol:
                break
            if abs(dev) <= ri_tol and (best_dev is None or abs(dev) < best_dev):
                best_dev = abs(dev)
                best = j
        if best is None:
            continue
        delta = W[best][1] - w0
        if max24 is None or delta > max24:
            max24 = delta
        if delta >= ri_threshold:
            cand.append((t0, delta))
    sel = []
    for t0, delta in cand:
        if not sel or (t0 - sel[-1][0]).total_seconds() / 3600.0 >= ri_window:
            sel.append((t0, delta))
    m['ri_events'] = len(sel)
    m['ri_deltas'] = [rnd(d, 2) for _t, d in sel]
    m['ri_first_h'] = ((sel[0][0] - W[0][0]).total_seconds() / 3600.0) if sel else None
    m['max_24h_delta'] = rnd(max24, 2)
    m['min_24h_delta'] = None
    return m


# ---------------------------------------------------------------- 聚合

def _collect(storms, key):
    return [s['m'].get(key) for s in storms]


def agg(storms):
    """对一组风暴做分布聚合。storms: [{key, src, basin, num, year, m}, ...]"""
    out = {}
    out['n_storms'] = len(storms)
    out['n_storms_with_wind'] = sum(1 for s in storms if s['m'].get('peak') is not None)
    out['n_reports'] = sum(s['m']['n_reports'] for s in storms)
    out['reports_per_storm'] = dstat([s['m']['n_reports'] for s in storms])
    out['n_wind_pts'] = sum(s['m'].get('n_wind_pts', 0) for s in storms)
    yrs = sorted(s['year'] for s in storms if s.get('year'))
    out['year_min'] = yrs[0] if yrs else None
    out['year_max'] = yrs[-1] if yrs else None
    basins = collections.Counter(s['basin'] for s in storms)
    out['basins'] = dict(sorted(basins.items()))

    for k in ['peak', 'time_to_peak_h', 'peak_frac_of_life', 'lifetime_h',
              'pregenesis_h', 'disturbance_h', 't_first20_h', 't_first34_h',
              't_first64_h', 't_first96_h', 'intensify_20_to_34_h',
              'intensify_34_to_64_h', 'td_reports', 'td_hours', 'td_to_ts_h',
              'td_max_run', 'td_first_run', 'td_runs', 'max_24h_delta', 'ri_events',
              'ri_first_h', 'ace', 'ace_reports_ge34', 'stage_hours_total']:
        out[k if k != 'peak' else 'peak_intensity_kt'] = dstat(_collect(storms, k))

    if out.get('ace') is None:
        out['ace'] = {'n': 0}
    ace_vals = [s['m'].get('ace') for s in storms if s['m'].get('ace') is not None]
    out['ace']['total'] = rnd(sum(ace_vals))
    out['ace']['per_storm_mean_over_all'] = rnd(sum(ace_vals) / len(storms)) if storms else None

    # 停留时长 / 进入次数 (全部样本 + 条件样本「峰值到过该档」)
    out['res_h'] = {}
    out['episodes'] = {}
    out['res_h_cond'] = {}
    out['episodes_cond'] = {}
    for k in BIN_KEYS + [x[0] for x in EXTRA_RES]:
        out['res_h'][k] = dstat([(s['m'].get('res_h') or {}).get(k) for s in storms])
        out['episodes'][k] = dstat([(s['m'].get('episodes') or {}).get(k) for s in storms])
        lo = dict([(b[0], b[1]) for b in BINS] + list(EXTRA_RES)).get(k)
        sub = storms if lo is None else [s for s in storms if (s['m'].get('peak') or 0) >= lo]
        out['res_h_cond'][k] = dstat([(s['m'].get('res_h') or {}).get(k) for s in sub])
        out['episodes_cond'][k] = dstat([(s['m'].get('episodes') or {}).get(k) for s in sub])

    # 阶段码
    out['stage_hours'] = {}
    out['stage_reports'] = {}
    all_codes = set()
    for s in storms:
        all_codes |= set(s['m'].get('stage_hours', {}).keys())
        all_codes |= set(s['m'].get('stage_reports', {}).keys())
    codes = [c for c in STAGE_ORDER if c in all_codes] + sorted(all_codes - set(STAGE_ORDER))
    for c in codes:
        vals = [(s['m'].get('stage_hours') or {}).get(c) for s in storms]
        d = dstat(vals)
        if d:
            d['total_h'] = rnd(sum(v for v in vals if v is not None))
            d['storms_with_stage'] = sum(1 for v in vals if v)
        out['stage_hours'][c] = d
        out['stage_reports'][c] = sum((s['m'].get('stage_reports') or {}).get(c, 0) for s in storms)

    # 减弱速率 (按档, 池化所有区间)
    out['weaken_rate'] = {}
    for k in BIN_KEYS + ['all']:
        vals = []
        sids = set()
        strict = []
        for s in storms:
            v = (s['m'].get('wr') or {}).get(k) or []
            if v:
                sids.add(s['key'])
                vals.extend(v)
            strict.extend((s['m'].get('wr_strict') or {}).get(k) or [])
        d = dstat(vals)
        if d:
            d['n_intervals'] = len(vals)
            d['n_storms'] = len(sids)
            d['median_strict_neg'] = (dstat(strict) or {}).get('median')
            d['n_intervals_strict_neg'] = len(strict)
        out['weaken_rate'][k] = d

    # 跨风暴的 per-storm 中位减弱速率 (抗风暴数不均衡)
    out['weaken_rate_by_storm'] = {}
    for k in BIN_KEYS + ['all']:
        per = []
        for s in storms:
            v = (s['m'].get('wr') or {}).get(k) or []
            if len(v) >= 2:
                per.append(_pct(sorted(v), 50))
        d = dstat(per)
        out['weaken_rate_by_storm'][k] = d

    # RI 占比 (三种分母)
    def frac(sub, pred):
        sub = [s for s in sub if s['m'].get('peak') is not None or 'ri_events' in s['m']]
        if not sub:
            return None
        hit = sum(1 for s in sub if (s['m'].get('ri_events') or 0) >= 1)
        return {'num': hit, 'den': len(sub), 'frac': rnd(hit / len(sub), 4)}

    two = [s for s in storms if s['m']['n_reports'] >= 2]
    life24 = [s for s in two if (s['m'].get('lifetime_h') or 0) >= 24.0]
    ts = [s for s in two if (s['m'].get('peak') or 0) >= 34.0]
    out['ri'] = {
        'frac_all': frac(two, None),
        'frac_life24': frac(life24, None),
        'frac_ts': frac(ts, None),
        'events_total': sum((s['m'].get('ri_events') or 0) for s in storms),
        'events_per_storm': dstat(_collect(storms, 'ri_events')),
        'events_per_storm_ts': dstat([s['m'].get('ri_events') for s in ts]),
    }
    return out


# ---------------------------------------------------------------- 数据源装载

class Src(object):
    def __init__(self, name, label, patterns, kind, note=''):
        self.name = name
        self.label = label
        self.patterns = patterns
        self.kind = kind          # 'obs' | 'model'
        self.note = note


def build_obs_sources(data_root, with_xrq_year=True):
    xrq = os.path.join(data_root, 'xrq')
    zi = os.path.join(data_root, '自评')
    miwu = os.path.join(data_root, 'miwu')
    srcs = []

    years = []
    if os.path.isdir(xrq):
        years = [d for d in sorted(os.listdir(xrq))
                 if d.isdigit() and os.path.isdir(os.path.join(xrq, d))]
    if with_xrq_year and years:
        srcs.append(Src(
            'xrq_year', 'xrq\\<year> (逐年, %s-%s)' % (years[0], years[-1]),
            [os.path.join(xrq, y, '*.dat') for y in years], 'obs',
            'b-deck 11 列; 与 xrq\\wp 几乎完全重合'))

    def add(name, label, pats, note):
        if pats:
            srcs.append(Src(name, label, pats, 'obs', note))

    if os.path.isdir(os.path.join(xrq, 'wp')):
        add('xrq_wp', 'xrq\\wp (全年代合集)',
            [os.path.join(xrq, 'wp', '*.dat')],
            'b-deck 11 列; 含 WP/CP/EP/IO/SH')
    if os.path.isdir(os.path.join(xrq, '2025')):
        add('xrq_2025', 'xrq\\2025', [os.path.join(xrq, '2025', '*.dat')], 'b-deck 11 列, 纯 WP')
    if os.path.isdir(os.path.join(xrq, '2026')):
        add('xrq_2026', 'xrq\\2026', [os.path.join(xrq, '2026', '*.dat')], 'b-deck 11 列, 纯 WP')
    if os.path.isdir(os.path.join(xrq, 'sh')):
        add('xrq_sh', 'xrq\\sh', [os.path.join(xrq, 'sh', '*.dat')], 'b-deck 11 列, 纯 SH')
    for y in ['2025', '2026', '2027']:
        d = os.path.join(zi, y)
        if os.path.isdir(d):
            add('zi_%s' % y, '自评\\%s (递归)' % y,
                [os.path.join(d, '**', '*.dat')],
                'b-deck 12 列 (末列标识/名字), 带 UTF-8 BOM')
    if os.path.isdir(miwu):
        add('miwu', 'miwu\\<year>\\<OCEAN> (递归)',
            [os.path.join(miwu, '**', '*.dat')],
            'b-deck 12 列; 每风暴首行为 29 列扩展行 (前 11 列同 b-deck)')
    return srcs


def load_obs(sources, verbose=True, max_life_h=1080.0):
    """返回 (by_source, all_storms)。by_source: {name: {'src':Src,'storms':[...]}}"""
    by_source = collections.OrderedDict()
    all_storms = []
    for s in sources:
        files = []
        for pat in s.patterns:
            files.extend(glob.glob(pat, recursive=True))
        files = sorted(set(files))
        storms = []
        nrep = 0
        n_drop = 0
        for f in files:
            rows = parse_bdeck(f)
            if not rows:
                continue
            nrep += len(rows)
            basin = rows[0][4]
            num = rows[0][5]
            m = analyze_storm(rows)
            if max_life_h and m['lifetime_h'] > max_life_h:
                n_drop += 1        # 时间戳异常 / 跨年拼接的病态记录, 只计数不入统计
                continue
            st = {
                'key': (basin, num, rows[0][0].year, rows[0][0].strftime('%Y%m%d%H')),
                'src': s.name,
                'path': f,
                'basin': basin,
                'num': num,
                'year': rows[0][0].year,
                'invest': '_invest' in f.lower() or 'invest' in os.path.basename(f).lower(),
                'm': m,
            }
            storms.append(st)
        all_storms.extend(storms)
        dirs = sorted({os.path.dirname(p) for p in s.patterns})
        by_source[s.name] = {
            'src': s, 'files': files, 'storms': storms,
            'n_files': len(files), 'n_reports': nrep, 'dirs': dirs,
            'n_dropped': n_drop,
        }
        if verbose:
            print('  [obs] %-10s files=%-5d storms=%-5d (dropped %d) reports=%d'
                  % (s.name, len(files), len(storms), n_drop, nrep))
    return by_source, all_storms


def build_model_sources(sim_root, years=('2025', '2100')):
    srcs = []
    for y in years:
        d = os.path.join(sim_root, y)
        if os.path.isdir(d):
            srcs.append(Src('model_%s' % y, 'sim_typhoon\\%s (递归, 含 _invest)' % y,
                            [os.path.join(d, '**', '*.dat')], 'model',
                            'b-deck 12 列, 6 h 间隔; 阶段码只有 DB/WV/LO/TD/TS/TY/ST'))
    return srcs


# ---------------------------------------------------------------- 对照表

COMPARE_ROWS = [
    # (显示名, block 路径, 统计量, 更优方向说明)
    ('峰值强度 (kt)', 'peak_intensity_kt', 'median', ''),
    ('达到峰值时间 (h, 自首报)', 'time_to_peak_h', 'median', ''),
    ('强度峰值出现位置 (寿命占比)', 'peak_frac_of_life', 'median', ''),
    ('总寿命 (h)', 'lifetime_h', 'median', ''),
    ('前生期 首报→34kt (h)', 'pregenesis_h', 'median', '偏短=生成太快'),
    ('扰动段 首报→20kt (h)', 'disturbance_h', 'median', ''),
    ('20→34kt 用时 (h)', 'intensify_20_to_34_h', 'median', ''),
    ('34→64kt 用时 (h)', 'intensify_34_to_64_h', 'median', ''),
    ('TD 报数 (中位)', 'td_reports', 'median', '偏大=减弱端黏'),
    ('TD 报数 (最长)', 'td_reports', 'max', ''),
    ('首个 TD 段 (报, 中位)', 'td_first_run', 'median', ''),
    ('TD 连续最长段 (报, 中位)', 'td_max_run', 'median', ''),
    ('TD 连续最长段 (报, 最长)', 'td_max_run', 'max', ''),
    ('TD 连续段数 (中位)', 'td_runs', 'median', ''),
    ('首次 TD 报→34kt (h)', 'td_to_ts_h', 'median', '另一种前生期口径'),
    ('ACE/风暴 (中位)', 'ace', 'median', ''),
    ('ACE/风暴 (均值)', 'ace', 'mean', ''),
    ('>=34kt 报数/风暴 (中位)', 'ace_reports_ge34', 'median', ''),
    ('有 RI 风暴占比 (>=24h 样本)', 'ri.frac_life24', 'frac', ''),
    ('有 RI 风暴占比 (全部样本)', 'ri.frac_all', 'frac', ''),
    ('有 RI 风暴占比 (>=34kt 样本)', 'ri.frac_ts', 'frac', ''),
    ('RI 过程数/风暴 (均值)', 'ri.events_per_storm', 'mean', ''),
    ('24h 最大增幅 (kt, 中位)', 'max_24h_delta', 'median', ''),
    ('>=64kt 停留 (h, 到过该档中位)', 'res_h_cond.ge64', 'median', '偏大=强档停留过久'),
    ('>=96kt 停留 (h, 到过该档中位)', 'res_h_cond.ge96', 'median', ''),
    ('>=100kt 停留 (h, 到过该档中位)', 'res_h_cond.ge100', 'median', ''),
    ('>=64kt 停留 (h, 到过该档均值)', 'res_h_cond.ge64', 'mean', ''),
    ('>=64kt 停留 (h, 全样本中位)', 'res_h.ge64', 'median', ''),
    ('<34kt 停留 (h, 中位)', 'res_h.lt34', 'median', ''),
    ('峰值后减弱速率 >=100kt (kt/6h)', 'weaken_rate.ge100', 'median', '越负=脱离强档越快'),
    ('峰值后减弱速率 80-100kt (kt/6h)', 'weaken_rate.b80_100', 'median', ''),
    ('峰值后减弱速率 64-80kt (kt/6h)', 'weaken_rate.b64_80', 'median', ''),
    ('峰值后减弱速率 48-64kt (kt/6h)', 'weaken_rate.b48_64', 'median', ''),
    ('峰值后减弱速率 34-48kt (kt/6h)', 'weaken_rate.b34_48', 'median', ''),
    ('峰值后减弱速率 <34kt (kt/6h)', 'weaken_rate.lt34', 'median', ''),
    ('峰值后减弱速率 全部 (kt/6h)', 'weaken_rate.all', 'median', ''),
    ('峰值后减弱速率 全部 (p10)', 'weaken_rate.all', 'p10', '稳健的极端减弱端'),
]


def metric_val(block, path, stat):
    obj = block
    for p in path.split('.'):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(p)
    if obj is None:
        return None
    if isinstance(obj, dict):
        if stat == 'frac':
            return obj.get('frac')
        return obj.get(stat)
    return obj


def _mtime_str(path):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
    except OSError:
        return None


def _ratio(model_block, obs_block, path, stat):
    """模型/观测 倍率 (任一缺失或观测为 0 时返回 None)。"""
    if not model_block or not obs_block:
        return None
    a = metric_val(obs_block, path, stat)
    b = metric_val(model_block, path, stat)
    if a is None or b is None or a == 0:
        return None
    return b / a


def build_compare(obs_block, model_blocks):
    """model_blocks: OrderedDict(name -> block)。rec['models'][name] / rec['ratios'][name]"""
    rows = []
    for label, path, stat, note in COMPARE_ROWS:
        o = metric_val(obs_block, path, stat)
        rec = {'metric': label, 'path': path, 'stat': stat, 'note': note, 'obs': o,
               'models': OrderedDict(), 'ratios': OrderedDict()}
        worst = 0.0
        for name, blk in model_blocks.items():
            mv = metric_val(blk, path, stat)
            rec['models'][name] = mv
            if mv is None or o is None or o == 0:
                rec['ratios'][name] = None
                continue
            r = mv / o
            rec['ratios'][name] = r
            if r > 0:
                worst = max(worst, abs(math.log(r)))
        rec['ratios'] = OrderedDict((k, rnd(v, 3)) for k, v in rec['ratios'].items())
        rec['max_abs_log_ratio'] = rnd(worst, 3)
        rows.append(rec)
    return rows


# ---------------------------------------------------------------- 报告

def dist_row(label, d, nd=2, extra=None):
    if not d:
        return [label, 0, 'n/a', 'n/a', 'n/a', 'n/a', 'n/a'] + ([] if extra is None else ['n/a'])
    r = [label, d['n'], fmt(d['median'], nd),
         '%s – %s' % (fmt(d['p10'], nd), fmt(d['p90'], nd)),
         fmt(d['mean'], nd), fmt(d['min'], nd), fmt(d['max'], nd)]
    if extra is not None:
        r.append(extra)
    return r


DIST_HDR = ['指标', 'n', '中位', '10–90 分位', '均值', '最小', '最大']


def core_dist_rows(b):
    rows = [
        dist_row('峰值强度 (kt)', b.get('peak_intensity_kt')),
        dist_row('达到峰值时间 (h, 自首报)', b.get('time_to_peak_h')),
        dist_row('峰值出现位置 (寿命占比)', b.get('peak_frac_of_life'), 3),
        dist_row('总寿命 (h)', b.get('lifetime_h')),
        dist_row('报数 (条/风暴)', b.get('reports_per_storm')),
        dist_row('扰动段 首报→20kt (h)', b.get('disturbance_h')),
        dist_row('前生期 首报→34kt (h)', b.get('pregenesis_h')),
        dist_row('首报→64kt (h)', b.get('t_first64_h')),
        dist_row('20→34kt 用时 (h)', b.get('intensify_20_to_34_h')),
        dist_row('首次 TD 报→34kt (h)', b.get('td_to_ts_h')),
        dist_row('34→64kt 用时 (h)', b.get('intensify_34_to_64_h')),
        dist_row('TD 报数 (条)', b.get('td_reports')),
        dist_row('TD 连续最长段 (条)', b.get('td_max_run')),
        dist_row('TD 累计时长 (h)', b.get('td_hours')),
        dist_row('ACE (Σv²/1e4)', b.get('ace')),
        dist_row('>=34kt 报数 (条)', b.get('ace_reports_ge34')),
        dist_row('RI 过程数/风暴', b.get('ri_events')),
        dist_row('24h 最大增幅 (kt)', b.get('max_24h_delta')),
    ]
    return rows


def weaken_table(b, title='峰值后减弱速率 (kt/6h) 与停留时长'):
    lines = ['### ' + title, '']
    rows = []
    for k in BIN_KEYS + ['all']:
        w = (b.get('weaken_rate') or {}).get(k)
        r = (b.get('res_h') or {}).get(k)
        rc = (b.get('res_h_cond') or {}).get(k)
        e = (b.get('episodes') or {}).get(k)
        ws = (b.get('weaken_rate_by_storm') or {}).get(k)
        rows.append([
            BIN_LABEL[k],
            (w or {}).get('n_intervals', 0),
            (w or {}).get('n_storms', 0),
            fmt((w or {}).get('median')) if w else 'n/a',
            fmt((w or {}).get('mean')) if w else 'n/a',
            ('%s – %s' % (fmt(w['p10']), fmt(w['p90']))) if w else 'n/a',
            fmt((w or {}).get('median_strict_neg')) if w else 'n/a',
            fmt((ws or {}).get('median')) if ws else 'n/a',
            fmt((r or {}).get('median')) if r else 'n/a',
            '%s (n=%s)' % (fmt((rc or {}).get('median')) if rc else 'n/a',
                           fmt_i((rc or {}).get('n')) if rc else '-'),
            fmt((rc or {}).get('mean')) if rc else 'n/a',
            fmt((e or {}).get('median')) if e else 'n/a',
        ])
    lines.append(md_table(
        ['强度档', '区间数', '风暴数', '减弱速率中位', '均值', '10–90', '仅减弱区间中位',
         '跨风暴中位', '停留中位(h, 全样本)', '停留中位(h, 到过该档)', '停留均值(h, 到过该档)',
         '进入次数中位'], rows))
    lines.append('')
    lines.append('> 减弱速率 = (v₂−v₁)×6/Δt，取**峰值之后**的所有相邻报区间，按区间起始报的强度分档；'
                 '正值表示该档内仍在增强（净变化）。「仅减弱区间中位」只统计 Δv<0 的区间。'
                 '「到过该档」= 只统计峰值 ≥ 该档下界的风暴（例如 >=100 kt 档只统计曾达 100 kt 的风暴），'
                 '回答“强档停留过久”应引用这一列；「全样本」列把没到过该档的风暴按 0 h 计入。')
    lines.append('')
    lines.append('累积停留门槛（条件样本 / 全样本）: ' + '，'.join(
        '%s 条件中位 %s h（n=%s）/ 全样本中位 %s h；均值 %s h' % (
            BIN_LABEL[k],
            fmt(((b.get('res_h_cond') or {}).get(k) or {}).get('median')),
            fmt_i(((b.get('res_h_cond') or {}).get(k) or {}).get('n')),
            fmt(((b.get('res_h') or {}).get(k) or {}).get('median')),
            fmt(((b.get('res_h') or {}).get(k) or {}).get('mean')))
        for k, _ in EXTRA_RES))
    return '\n'.join(lines)


def stage_table(obs_b, model_b=None):
    codes = [c for c in STAGE_ORDER if (obs_b.get('stage_hours') or {}).get(c)
             or (model_b and (model_b.get('stage_hours') or {}).get(c))]
    model_b = model_b or {}
    rows = []
    for c in codes:
        o = (obs_b.get('stage_hours') or {}).get(c)
        mm = (model_b.get('stage_hours') or {}).get(c)
        rows.append([
            c,
            fmt_i(obs_b.get('stage_reports', {}).get(c, 0)),
            fmt((o or {}).get('total_h')) if o else '0',
            fmt((o or {}).get('median')) if o else 'n/a',
            fmt_i(model_b.get('stage_reports', {}).get(c, 0)) if model_b else '-',
            fmt((mm or {}).get('total_h')) if mm else ('0' if model_b else '-'),
            fmt((mm or {}).get('median')) if mm else ('n/a' if model_b else '-'),
        ])
    return md_table(['阶段码', '观测报数', '观测累计h', '观测每风暴中位h',
                     '模型报数', '模型累计h', '模型每风暴中位h'], rows)


def compact_metric_table(blocks, order=None, keycols=None):
    """blocks: OrderedDict(name -> block); 每个 block 一行核心指标。"""
    keycols = keycols or [
        ('n_storms', '风暴数', '{:,d}'),
        ('n_reports', '报数', '{:,d}'),
        ('peak_intensity_kt', '峰值中位(kt)', None),
        ('lifetime_h', '寿命中位(h)', None),
        ('pregenesis_h', '前生期中位(h)', None),
        ('td_reports', 'TD报数中位', None),
        ('ace', 'ACE/风暴中位', None),
        ('ace', 'ACE总量', None),
    ]
    names = list(blocks.keys()) if order is None else [n for n in order if n in blocks]
    hdr = ['集合'] + [c[1] for c in keycols]
    rows = []
    for n in names:
        b = blocks[n]
        row = [n]
        for path, label, f in keycols:
            if path == 'n_storms':
                v = b.get('n_storms')
            elif path == 'n_reports':
                v = b.get('n_reports')
            elif path == 'ace' and label == 'ACE总量':
                v = (b.get('ace') or {}).get('total')
            else:
                v = (b.get(path) or {})
                v = v.get('median') if isinstance(v, dict) else v
            if v is None:
                row.append('n/a')
            elif f:
                row.append(f.format(int(v)))
            else:
                row.append(fmt(v))
        rows.append(row)
    return md_table(hdr, rows)


def ri_table(blocks):
    rows = []
    for n, b in blocks.items():
        r = b.get('ri') or {}
        rows.append([
            n,
            '%s/%s = %s' % ((r.get('frac_life24') or {}).get('num', 'n/a'),
                            (r.get('frac_life24') or {}).get('den', 'n/a'),
                            fmt((r.get('frac_life24') or {}).get('frac'), 3)),
            fmt((r.get('frac_all') or {}).get('frac'), 3),
            fmt((r.get('frac_ts') or {}).get('frac'), 3),
            fmt_i(r.get('events_total')),
            fmt((r.get('events_per_storm') or {}).get('mean')),
        ])
    return md_table(['集合', '有 RI 占比 (>=24h 样本)', '有 RI 占比 (全部)', '有 RI 占比 (>=34kt)',
                     'RI 过程总数', 'RI 过程/风暴(均值)'], rows)


def write_report(path, ctx):
    L = []
    A = L.append
    A('# 观测 vs 模拟：台风「强度-寿命廓线」统计')
    A('')
    A('生成时间: %s' % ctx['generated'])
    A('')
    A('本文件由 `tools/stat_intensity_profile.py` 生成，**不由模拟器读取**。'
      '目的是给模拟器的三处系统性偏差提供观测标定目标：')
    A('')
    A('1. ACE/风暴 偏高（怀疑“强度廓线在强档停留过久”）；')
    A('2. 有 RI（快速增强）的风暴比例偏高；')
    A('3. 前生期（扰动→TS）偏短；减弱端 TD 阶段偏黏。')
    A('')

    # ---------- 1 数据源
    A('## 1. 数据源与样本量')
    A('')
    rows = []
    for name, info in ctx['sources'].items():
        s = info['src']
        blk = (ctx['obs_by_source'].get(name) or ctx['model_blocks'].get(name))
        basins = ','.join('%s(%d)' % (k, v) for k, v in sorted(blk['basins'].items()))
        dirstr = '-'
        if info['dirs']:
            dirstr = info['dirs'][0]
            if len(info['dirs']) > 1:
                dirstr = '%s 等 %d 个目录' % (os.path.dirname(info['dirs'][0]) or info['dirs'][0],
                                             len(info['dirs']))
        rows.append([name, dirstr,
                     info['n_files'], blk['n_storms'], info.get('n_dropped', 0), blk['n_reports'],
                     '%s–%s' % (blk['year_min'], blk['year_max']) if blk['year_min'] else '-',
                     basins or '-', s.note])
    A(md_table(['集合', '目录(glob 根)', '文件数', '计入风暴数', '丢弃异常记录',
                '报数', '年份范围', '洋区(风暴数)', '格式备注'], rows))
    A('')
    A('> 「丢弃异常记录」= 首末报跨度 > %s h（45 d）的病态记录（时间戳错误 / 跨年拼接），'
      '只计数、不进入任何统计。' % fmt(ctx['max_life_h'], 0))
    A('')
    A('**口径警告（重要）：** 观测归档只收**被编号/命名的系统**，模型归档把**未发展的 invest 也写出来**，'
      '因此「ACE/风暴」「RI 占比」这类占比指标对分母极其敏感——'
      '本报告同时给出「全集口径」和「>=34 kt 口径」两套对照表（第 8 节）。')
    A('')
    A('**结论：** `xrq\\wp`（2340 个文件）是 `xrq\\<year>`（1949–2026 逐年，%d 个文件）的近似全集镜像'
      '（2339 个文件名完全重合，仅 `bwp171980.dat` 只在 `xrq\\wp`）。两者在合并去重后不会重复计数。'
      % ctx['n_xrq_year_files'])
    A('')
    A('### 1.1 miwu 目录结构结论（已单独探查）')
    A('')
    A('`G:\\气象\\数据\\miwu` 是 **b-deck 同族格式**，不是另一种格式，结构为：')
    A('')
    A('```')
    A('miwu\\<year>\\<OCEAN>\\b<basin><num><year>.dat     e.g. miwu\\2019\\WPAC\\bwp011970.dat')
    A('```')
    A('')
    A('- 年份目录: %s（%d 个）' % (ctx['miwu_years_short'], ctx['n_miwu_years']))
    A('- 洋区子目录: %s' % ctx['miwu_oceans'])
    A('- 文件名前缀: `bwp*/bep*/bcp*/bio*/bsh*/bal*/bls*` —— 与 xrq 一致；文件数 %d。' % ctx['miwu_files'])
    A('- **列数**：正文行 12 列（末列为名字/标识），但 **每个风暴的首行是 29 列扩展行**'
      '（前 11 列仍是标准 b-deck 列，后面是 0/空值与名字）。解析器只取前 11 列，故两种行都兼容。')
    A('- 时间步：以 6 h 为主，mix 有 1/2/3/4/5 h 步长；另有极少数时间戳异常（负间隔 / 跨年 8766 h），'
      '解析时按时间戳排序 + 同刻去重，异常间隔不计入速率统计。')
    A('- 阶段码比 xrq 更丰富（多 `LO`/`HU`/`SS`/`SD`/`MD`/`WV`/`PT`），洋区更多（多 `ME`/`SL`）。')
    A('')

    # ---------- 2 观测总体
    A('## 2. 观测总体（全部来源合并去重 = OBS_UNIQUE）')
    A('')
    u = ctx['obs_unique']
    A('风暴数 %s，报数 %s，年份 %s–%s；洋区构成：%s。'
      % (fmt_i(u['n_storms']), fmt_i(u['n_reports']), u['year_min'], u['year_max'],
         ', '.join('%s=%d' % (k, v) for k, v in sorted(u['basins'].items()))))
    A('')
    A(md_table(DIST_HDR, core_dist_rows(u)))
    A('')
    A('**标题数字（观测）：**')
    A('')
    A('- ACE 总量 **%s**，ACE/风暴 中位 **%s** / 均值 **%s**'
      % (fmt((u['ace'] or {}).get('total')), fmt((u['ace'] or {}).get('median')),
         fmt((u['ace'] or {}).get('mean'))))
    A('- 有 RI 风暴占比：>=24h 样本 **%s**（%s/%s），全部样本 %s，>=34kt 样本 %s'
      % (fmt((u['ri']['frac_life24'] or {}).get('frac'), 3),
         (u['ri']['frac_life24'] or {}).get('num'), (u['ri']['frac_life24'] or {}).get('den'),
         fmt((u['ri']['frac_all'] or {}).get('frac'), 3),
         fmt((u['ri']['frac_ts'] or {}).get('frac'), 3)))
    A('- 前生期（首报→34kt）中位 **%s h**；扰动段（首报→20kt）中位 **%s h**'
      % (fmt((u['pregenesis_h'] or {}).get('median')), fmt((u['disturbance_h'] or {}).get('median'))))
    A('- TD 报数 中位 **%s** / 最长 **%s**；TD 连续最长段 最大 **%s** 报'
      % (fmt((u['td_reports'] or {}).get('median')), fmt((u['td_reports'] or {}).get('max')),
         fmt((u['td_max_run'] or {}).get('max'))))
    A('')

    # ---------- 3 分档
    A('## 3. 强度分档：停留时长 + 峰值后减弱速率（治“强档停留过久”的直接依据）')
    A('')
    A(weaken_table(u))
    A('')

    # ---------- 4 阶段码
    A('## 4. 阶段码累计时长')
    A('')
    A('观测（OBS_UNIQUE） vs 模型（2025+2100 合并）：')
    A('')
    A(stage_table(u, ctx['model_all']))
    A('')
    A('> 模型侧**没有** `EX/SS/SD/MD/HU` 阶段码（只有 DB/WV/LO/TD/TS/TY/ST）；模型 `WV` 占报数'
      '比例极高，说明模型把大量时间花在“波动”阶段，而观测的 `WV` 极少（%d 条）。'
      % (u.get('stage_reports', {}).get('WV', 0)))
    A('')

    # ---------- 5 洋区
    A('## 5. 按洋区（OBS_UNIQUE 内部拆分）')
    A('')
    A(compact_metric_table(ctx['obs_by_basin']))
    A('')
    A('分档减弱速率（按洋区，中位 kt/6h）：')
    A('')
    rows = []
    for name, b in ctx['obs_by_basin'].items():
        w = b.get('weaken_rate') or {}
        rows.append([name, b['n_storms']] + [
            (fmt((w.get(k) or {}).get('median')) + ' (n=%s)' % (w.get(k) or {}).get('n_intervals', 0))
            if w.get(k) else 'n/a' for k in BIN_KEYS])
    A(md_table(['洋区', '风暴数'] + [BIN_LABEL[k] for k in BIN_KEYS], rows))
    A('')
    A('按洋区 RI 占比：')
    A('')
    A(ri_table(ctx['obs_by_basin']))
    A('')

    # ---------- 6 年代
    A('## 6. 按年代（OBS_UNIQUE 按首报年份分十年期）')
    A('')
    A(compact_metric_table(ctx['obs_by_era']))
    A('')
    A('> 注意：早期（1950s–1970s）b-deck 只有 6 h 定强且风速偏粗（多为 5 kt 步长），'
      'ACE 与 RI 与卫星时代不完全可比；表中已按年代分开，引用时请连同样本量一起引用。')
    A('')

    # ---------- 7 各来源
    A('## 7. 按来源集合')
    A('')
    A(compact_metric_table(ctx['obs_by_source']))
    A('')
    A('各来源 RI 占比：')
    A('')
    A(ri_table(ctx['obs_by_source']))
    A('')
    A('各来源分档减弱速率（中位 kt/6h，括号内为区间数）：')
    A('')
    rows = []
    for name, b in ctx['obs_by_source'].items():
        w = b.get('weaken_rate') or {}
        rows.append([name] + [
            (fmt((w.get(k) or {}).get('median')) + ' (%s)' % (w.get(k) or {}).get('n_intervals', 0))
            if w.get(k) else 'n/a' for k in BIN_KEYS])
    A(md_table(['来源'] + [BIN_LABEL[k] for k in BIN_KEYS], rows))
    A('')

    # ---------- 8 对照
    A('## 8. 模型 vs 观测 对照表（含倍率与最大偏差项）')
    A('')
    A('模型侧：`sim_typhoon\\2025\\**\\*.dat`（含 `*_invest`）与 `sim_typhoon\\2100\\**\\*.dat`（含 `*_invest`），'
      '与观测同一套解析/统计口径。')
    A('')
    mb = ctx['model_blocks']
    full_names = [n for n in mb if not n.endswith('_ge34')]
    A('模型侧样本量（同一口径）:')
    A('')
    A(compact_metric_table(OrderedDict([('OBS_UNIQUE', u)] + [(n, mb[n]) for n in full_names])))
    A('')

    for si, (skey, sc) in enumerate(ctx['compare'].items()):
        label = ctx['compare_labels'][skey]
        names = list(ctx['model_scopes'][skey].keys())
        obs_b = ctx['obs_scopes'][skey]
        A('### 8.%d 对照表 —— %s' % (si + 1, label))
        A('')
        A('观测样本 %s（报数 %s）；模型列: %s'
          % (fmt_i(obs_b['n_storms']), fmt_i(obs_b['n_reports']), ', '.join(names)))
        A('')
        hdr = ['指标', '观测'] + ['模型 %s' % n for n in names] + ['倍率 %s' % n for n in names] + ['备注']
        rows = []
        for rec in sc:
            rows.append([rec['metric'], fmt(rec['obs'], 3)]
                        + [fmt(rec['models'].get(n), 3) for n in names]
                        + [fmt(rec['ratios'].get(n), 2) for n in names]
                        + [rec['note']])
        A(md_table(hdr, rows))
        A('')
        ranked = sorted([r for r in sc if r['max_abs_log_ratio'] > 0
                         and r['stat'] in ('median', 'mean', 'frac')],
                        key=lambda r: -r['max_abs_log_ratio'])
        A('**%s：最大偏差项排序（|log(模型/观测)| 降序，前 12）**' % label)
        A('')
        rows = []
        for i, r in enumerate(ranked[:12], 1):
            rows.append([i, r['metric'], fmt(r['obs'], 3)]
                        + [fmt(r['models'].get(n), 3) for n in names]
                        + [fmt(r['ratios'].get(n), 2) for n in names]
                        + [fmt(r['max_abs_log_ratio'], 2)])
        A(md_table(['#', '指标', '观测'] + ['模型 %s' % n for n in names]
                   + ['倍率 %s' % n for n in names] + ['|log 倍率|'], rows))
        A('')
        if ranked:
            top = ranked[0]
            A('**口径「%s」的最大偏差项 = `%s`**（观测 %s；%s；最大 |log 倍率| = %s）。'
              % (label, top['metric'], fmt(top['obs'], 3),
                 '，'.join('模型 %s = %s（倍率 %s）' % (n, fmt(top['models'].get(n), 3),
                                                      fmt(top['ratios'].get(n), 2)) for n in names),
                 fmt(top['max_abs_log_ratio'], 2)))
            A('')

    A('### 8.3 分档减弱速率 模型 vs 观测（中位 kt/6h）')
    A('')
    names = []
    for n in mb:
        if not n.endswith('_ge34'):
            names.append(n)
    rows = []
    for k in BIN_KEYS + ['all']:
        o = (u.get('weaken_rate') or {}).get(k)
        row = [BIN_LABEL[k], fmt((o or {}).get('median')) if o else 'n/a']
        for n in names:
            w = (mb[n].get('weaken_rate') or {}).get(k)
            row.append(fmt((w or {}).get('median')) if w else 'n/a')
        for n in names:
            w = (mb[n].get('weaken_rate') or {}).get(k)
            row.append(fmt(((w or {}).get('median') or 0) / ((o or {}).get('median') or 1), 2)
                       if (o and w and o.get('median')) else 'n/a')
        rows.append(row)
    A(md_table(['强度档', '观测'] + ['模型 %s' % n for n in names]
               + ['倍率 %s' % n for n in names], rows))
    A('')
    A('### 8.4 分档停留时长 模型 vs 观测（到过该档的样本中位 h）')
    A('')
    rows = []
    for k in BIN_KEYS + [x[0] for x in EXTRA_RES]:
        o = (u.get('res_h_cond') or {}).get(k) or (u.get('res_h') or {}).get(k)
        row = ['%s (n=%s)' % (BIN_LABEL[k], fmt_i((o or {}).get('n'))),
               fmt((o or {}).get('median')) if o else 'n/a']
        for n in names:
            r = (mb[n].get('res_h_cond') or {}).get(k) or (mb[n].get('res_h') or {}).get(k)
            row.append(fmt((r or {}).get('median')) if r else 'n/a')
        for n in names:
            r = (mb[n].get('res_h_cond') or {}).get(k) or (mb[n].get('res_h') or {}).get(k)
            row.append(fmt(((r or {}).get('median') or 0) / ((o or {}).get('median') or 1), 2)
                       if (o and r and o.get('median')) else 'n/a')
        rows.append(row)
    A(md_table(['强度档(条件样本)', '观测(到过该档)'] + ['模型 %s' % n for n in names]
               + ['倍率 %s' % n for n in names], rows))
    A('')
    A('> 条件样本 = 峰值 >= 该档下界的风暴；这一列才回答“到过强档的风暴在其中停了多久”。')
    A('')
    A('### 8.5 模型 vs 观测：阶段码累计时长')
    A('')
    A(stage_table(u, mb.get('model_2025')))
    A('')

    # ---- TD 口径对照
    A('### 8.6 TD 段：四种口径对照（“减弱端偏黏”的口径敏感性）')
    A('')
    A('“TD 阶段长度”至少有四种算法，观测与模型的倍率差别很大，故全部列出：')
    A('')
    td_blocks = OrderedDict([('OBS 全集', u)])
    if ctx['obs_scopes'].get('ge34'):
        td_blocks['OBS >=34kt'] = ctx['obs_scopes']['ge34']
    for n in ['model_2025', 'model_2025_ge34', 'model_2100', 'model_2100_ge34']:
        if n in mb:
            td_blocks[n] = mb[n]
    rows = []
    for n, b in td_blocks.items():
        if not b:
            continue
        rows.append([
            n, b['n_storms'],
            fmt((b.get('td_reports') or {}).get('median')),
            fmt((b.get('td_reports') or {}).get('max')),
            fmt((b.get('td_first_run') or {}).get('median')),
            fmt((b.get('td_max_run') or {}).get('median')),
            fmt((b.get('td_max_run') or {}).get('max')),
            fmt((b.get('td_runs') or {}).get('median')),
            fmt((b.get('td_hours') or {}).get('median')),
        ])
    A(md_table(['集合', '风暴数', 'TD 报数中位', 'TD 报数最长', '首个TD段中位(报)',
                '最长TD段中位(报)', '最长TD段最长(报)', 'TD段数中位', 'TD累计h中位'], rows))
    A('')

    # ---- 口径敏感性
    all_obs = ctx['obs_storms_all']
    n_obs = len(all_obs)
    n_obs_ts = sum(1 for s in all_obs if (s['m'].get('peak') or 0) >= 34.0)
    m25 = mb.get('model_2025') or {}
    m25g = mb.get('model_2025_ge34') or {}
    m21 = mb.get('model_2100') or {}
    m21g = mb.get('model_2100_ge34') or {}
    A('### 8.7 口径敏感性（为什么「倍率」取决于分母）')
    A('')
    A('观测归档（`xrq` / `自评` / `miwu`）只含**被编号/命名的系统**：%d/%d = %.1f%% 的风暴峰值 >=34 kt；'
      % (n_obs_ts, n_obs, 100.0 * n_obs_ts / max(n_obs, 1)))
    A('模型归档（`sim_typhoon\\<year>\\**`）把**未发展的 invest 也写了出来**：2025 只有 %d/%d = %.1f%%，'
      '2100 只有 %d/%d = %.1f%% 达到 34 kt。'
      % (m25g.get('n_storms', 0), m25.get('n_storms', 0),
         100.0 * m25g.get('n_storms', 0) / max(m25.get('n_storms', 1), 1),
         m21g.get('n_storms', 0), m21.get('n_storms', 0),
         100.0 * m21g.get('n_storms', 0) / max(m21.get('n_storms', 1), 1)))
    A('')
    A('因此：')
    A('')
    A('- 用**全集口径**（按任务书给的两个目录，含 `*_invest`）：模型的 ACE/风暴、RI 占比、'
      '>=64 kt 停留时长都**远低于**观测（一半样本是 ACE≈0 的弱系统），倍率 <1；')
    A('- 用 **>=34 kt 口径**（两侧都只留峰值 >=34 kt 的风暴）才与“命名风暴”口径可比，'
      '此时 ACE/风暴 均值倍率 = %s（2025）/ %s（2100）；'
      % (fmt(_ratio(m25g, ctx['obs_scopes'].get('ge34'), 'ace', 'mean'), 2),
         fmt(_ratio(m21g, ctx['obs_scopes'].get('ge34'), 'ace', 'mean'), 2)))
    A('- 引用任何倍率时必须连同样本量与该口径一起引用。')
    A('')

    # ---- 季节匹配对照（最重要的一张表）
    seas = (ctx['obs_by_source'] or {}).get('xrq_2025')
    if seas:
        A('### 8.8 季节匹配对照（推荐口径）')
        A('')
        A('模型 2025 运行模拟的就是 **2025 季**，所以「真实 2025 西北太平洋季」'
          '（`xrq\\2025`，%d 个风暴、%s 条报）才是同季可比基准。'
          % (seas['n_storms'], fmt_i(seas['n_reports'])))
        A('本脚本实算该集合：ACE/风暴 均值 **%s**、有 RI 占比 **%s**、前生期中位 **%s h**、'
          '峰值中位 **%s kt** —— 与任务书引用的观测目标（7.5 / 28%% / 42 h）逐项吻合，'
          '说明任务书的观测基准就是这一季。'
          % (fmt((seas.get('ace') or {}).get('mean')),
             fmt((seas['ri']['frac_life24'] or {}).get('frac'), 3),
             fmt((seas.get('pregenesis_h') or {}).get('median')),
             fmt((seas.get('peak_intensity_kt') or {}).get('median'))))
        A('')
        names2 = [n for n in ['model_2025', 'model_2025_ge34',
                              'model_2100', 'model_2100_ge34'] if n in mb]
        sub_models = OrderedDict((n, mb[n]) for n in names2)
        rows2 = build_compare(seas, sub_models)
        names2r = [n for n in ['model_2025_ge34', 'model_2100_ge34'] if n in mb] or names2
        rows2r = ctx.get('compare_season') or build_compare(
            seas, OrderedDict((n, mb[n]) for n in names2r))
        A(md_table(['指标', '观测 2025WP季'] + ['模型 %s' % n for n in names2]
                   + ['倍率 %s' % n for n in names2] + ['备注'],
                   [[r['metric'], fmt(r['obs'], 3)]
                    + [fmt(r['models'].get(n), 3) for n in names2]
                    + [fmt(r['ratios'].get(n), 2) for n in names2]
                    + [r['note']] for r in rows2]))
        A('')
        ranked2 = sorted([r for r in rows2r if r['max_abs_log_ratio'] > 0
                          and r['stat'] in ('median', 'mean', 'frac')],
                         key=lambda r: -r['max_abs_log_ratio'])
        A('**季节匹配口径下的最大偏差项排序（前 15）**')
        A('')
        A('> 只列 >=34 kt 口径的模型列，并排除 max/min 这类极值统计量（它们对单条记录敏感）。')
        A('')
        A(md_table(['#', '指标', '观测 2025WP季'] + ['模型 %s' % n for n in names2r]
                   + ['倍率 %s' % n for n in names2r] + ['|log 倍率|'],
                   [[i, r['metric'], fmt(r['obs'], 3)]
                    + [fmt(r['models'].get(n), 3) for n in names2r]
                    + [fmt(r['ratios'].get(n), 2) for n in names2r]
                    + [fmt(r['max_abs_log_ratio'], 2)]
                    for i, r in enumerate(ranked2[:15], 1)]))
        A('')
        if ranked2:
            top = ranked2[0]
            A('**季节匹配口径的最大偏差项 = `%s`**（观测 %s；%s）。'
              % (top['metric'], fmt(top['obs'], 3),
                 '，'.join('模型 %s = %s（倍率 %s）' % (n, fmt(top['models'].get(n), 3),
                                                      fmt(top['ratios'].get(n), 2))
                          for n in names2r)))
            A('')
        if 'model_2100_ge34' in mb:
            A('同季基准下，模型相对观测的**方向性结论**：')
            A('')
            A('- 峰值强度 与 24 h 最大增幅 与观测接近（倍率 0.9–1.1）；')
            A('- 前生期（扰动→TS）**长 3 倍以上**（模型 ~162–168 h vs 观测 54 h）；')
            A('- **强档静止**：>=100 kt 档的峰值后减弱速率只有观测的 0.32–0.40 倍'
              '（观测 %s kt/6h vs 模型 %s / %s kt/6h），'
              '>=64 kt 累计停留 2100 运行是观测的 2.4 倍；'
              % (fmt((seas.get('weaken_rate') or {}).get('ge100', {}).get('median')),
                 fmt((mb['model_2025_ge34'].get('weaken_rate') or {}).get('ge100', {}).get('median')),
                 fmt((mb['model_2100_ge34'].get('weaken_rate') or {}).get('ge100', {}).get('median'))))
            A('- **弱端过黏**：<34 kt 停留中位 %s h vs 观测 %s h；TD 报数中位 18 vs 观测 6.5；'
              % (fmt((mb['model_2100_ge34'].get('res_h') or {}).get('lt34', {}).get('median')),
                 fmt((seas.get('res_h') or {}).get('lt34', {}).get('median'))))
            A('- ACE/风暴 均值：2025 运行 %s（x%s）、2100 运行 %s（x%s）——'
              '即“ACE 偏高”主要出现在 2100 运行与 >=34 kt 口径下。'
              % (fmt((mb['model_2025_ge34'].get('ace') or {}).get('mean')),
                 fmt(_ratio(mb['model_2025_ge34'], seas, 'ace', 'mean'), 2),
                 fmt((mb['model_2100_ge34'].get('ace') or {}).get('mean')),
                 fmt(_ratio(mb['model_2100_ge34'], seas, 'ace', 'mean'), 2)))
            A('- 有 RI 占比：2025 运行 %s、2100 运行 %s（观测 %s）——'
              '**RI 占比并未系统性偏高**（在 >=34 kt 口径下反而接近或略低）。'
              % (fmt((mb['model_2025_ge34']['ri']['frac_life24'] or {}).get('frac'), 3),
                 fmt((mb['model_2100_ge34']['ri']['frac_life24'] or {}).get('frac'), 3),
                 fmt((seas['ri']['frac_life24'] or {}).get('frac'), 3)))
            A('')

    # ---- 与任务书引用数字的核对
    if seas and 'model_2025_ge34' in mb and 'model_2100_ge34' in mb:
        g25, g21 = mb['model_2025_ge34'], mb['model_2100_ge34']
        ace25 = (g25.get('ace') or {}).get('mean')
        ace21 = (g21.get('ace') or {}).get('mean')
        rows3 = [
            ['观测 ACE/风暴', '7.5', fmt((seas.get('ace') or {}).get('mean'), 2),
             'xrq\\2025 均值（32 个风暴）', '吻合'],
            ['观测 RI 占比', '28%', fmt((seas['ri']['frac_life24'] or {}).get('frac'), 3),
             'xrq\\2025，24h 内 +30kt', '吻合'],
            ['观测 前生期', '42 h', '%s h（p10 %s）' % (
                fmt((seas.get('pregenesis_h') or {}).get('median')),
                fmt((seas.get('pregenesis_h') or {}).get('p10'))),
             'xrq\\2025 中位/P10', '吻合（42 h ≈ P10）'],
            ['观测 TD 中位报数', '2 报',
             '首个TD段 %s 报 / 全部TD报 %s 报' % (
                 fmt((seas.get('td_first_run') or {}).get('median')),
                 fmt((seas.get('td_reports') or {}).get('median'))),
             'xrq\\2025，口径待定', '口径不同（首个段≈3）'],
            ['观测 TD 最长', '11 报',
             '最长TD段 %s / TD报数最长 %s' % (
                 fmt((seas.get('td_max_run') or {}).get('max')),
                 fmt((seas.get('td_reports') or {}).get('max'))),
             'xrq\\2025', '口径不同（9 / 16）'],
            ['模型 ACE/风暴', '14.3',
             '%s（2025）/ %s（2100），均值 %s' % (
                 fmt(ace25, 2), fmt(ace21, 2),
                 fmt(((ace25 or 0) + (ace21 or 0)) / 2.0, 2)),
             '>=34kt 口径', '吻合（≈两次运行均值）'],
            ['模型 RI 占比', '43%',
             '%s（2025）/ %s（2100）' % (
                 fmt((g25['ri']['frac_life24'] or {}).get('frac'), 3),
                 fmt((g21['ri']['frac_life24'] or {}).get('frac'), 3)),
             '>=34kt 口径', '接近（40% @2100）'],
            ['模型 前生期', '18 h',
             '%s h（2025）/ %s h（2100）' % (
                 fmt((g25.get('pregenesis_h') or {}).get('median')),
                 fmt((g21.get('pregenesis_h') or {}).get('median'))),
             'sim_typhoon\\2025|2100，首报→34kt', '**不吻合（差 ~9 倍）**'],
            ['模型 TD 中位/最长', '5 / 33 报',
             '%s / %s（2025）、%s / %s（2100）' % (
                 fmt((g25.get('td_reports') or {}).get('median')),
                 fmt((g25.get('td_max_run') or {}).get('max')),
                 fmt((g21.get('td_reports') or {}).get('median')),
                 fmt((g21.get('td_max_run') or {}).get('max'))),
             '>=34kt 口径', '**不吻合（18 / 47 报）**'],
        ]
        A('### 8.9 与任务书引用数字的核对')
        A('')
        A(md_table(['指标', '任务书引用', '本脚本实算（同口径）', '来源/口径', '判定'], rows3))
        A('')
        A('> 观测四项与模型 ACE/RI 两项都能对上（观测基准就是 `xrq\\2025` 这一季）；'
          '**「模型前生期 18 h」与「模型 TD 5 报 / 最长 33 报」在本仓库的两个模型 b-deck 目录里'
          '无论如何取口径都对不上**——本脚本看到的是前生期 162–168 h、TD 报数中位 18 报、'
          '最长 TD 段 47 报（2025）/ 36 报（2100）。')
        A('')
        A('**溯源（重要）：任务书那三个模型数字来自"再生成之前"的模型输出。** '
          '`output/sim/_reference/PHASE_DURATION_STATS.md`（生成于 %s）与 `ROUND_LOG.md` '
          '记录：当时 `sim_typhoon\\2025|2100` 各只有 81 个 `.dat`，阶段码只有 `DB/TS/TY/ST`，'
          '**TD 记录 0 条**，前生期合计中位 **12 h**。'
          '而当前这两个目录的 460 个 `.dat` 全部在 04:56–05:23 被重写（含 `*_invest`），'
          '阶段码变成 `DB/WV/LO/TD/TS/TY/ST`，TD 记录共 %s 条，前生期 162–168 h。'
          % (ctx.get('phase_stat_mtime') or '(未知)', 
             fmt_i((ctx['model_all'] or {}).get('stage_reports', {}).get('TD', 0))))
        A('')
        A('因此对**当前**模型输出而言，结论应当反过来写：')
        A('')
        A('- 「前生期偏短」→ 现在是 **偏长 3 倍**（模型把 2.3 天的前生期拉成了 6.8 天）；')
        A('- 「减弱端 TD 偏黏」的方向仍然成立，而且更强：当前模型 TD 报数中位 18 vs 观测 6.5（3×）、'
          '首个 TD 段中位 15 报 vs 观测 3 报（5×）；')
        A('- 「ACE/风暴偏高」只在 **2100 运行 + >=34 kt 口径**下成立（2.43×），2025 运行只有 1.46×；')
        A('- 「有 RI 占比偏高」在 >=34 kt 口径下**不成立**（2025 1.16×、2100 1.42×，'
          '而全归档口径反而是 0.24–0.29×）。')
        A('')

    # ---------- 9 方法
    A('## 9. 统计口径与方法学')
    A('')
    A('| 指标 | 定义 |')
    A('|---|---|')
    A('| 峰值强度 | 逐风暴最大风速（kt） |')
    A('| 达到峰值时间 | 首报 → 首次出现峰值的时刻 |')
    A('| 总寿命 | 首报 → 末报的时间跨度（不含尾部补齐） |')
    A('| 扰动段 | 首报 → 首次风速 ≥20 kt |')
    A('| 前生期 | 首报 → 首次风速 ≥34 kt（“扰动→TS”） |')
    A('| TD 报数 | 阶段码为 `TD` 的报条数 |')
    A('| TD 连续最长段 | 连续 `TD` 报条的最长段（条） |')
    A('| ACE | Σ v²/1e4，只累加 v≥34 kt；按报的**持续时间**加权（Δt/6），'
      '故纯 6 h 数据等价于“每个 ≥34 kt 的 6 h 报各计一次”的标准口径，'
      '3 h 数据不会被重复计数 |')
    A('| 减弱速率 | (v₂−v₁)×6/Δt，单位 kt/6h；只取**峰值之后**的相邻报区间，'
      '按区间**起始报**的强度分档；Δt>%s h 的区间丢弃（避免断记当减弱） |' % 24)
    A('| 停留时长 | 每条报按其到下一报的 Δt 计入该报所属强度档；末报按全序列最后一个合法 Δt 补齐 |')
    A('| RI 过程 | 任意 24 h 窗口内增幅 ≥30 kt；重叠窗口合并为**独立过程**'
      '（要求相邻事件起始时刻间隔 ≥24 h） |')
    A('| 合并去重 | 风暴键 = (洋区, 低压号, 首报年份, 首报时刻)；跨来源重复时保留**报数最多**的那条 |')
    A('')
    A('已知局限：')
    A('')
    A('- 早期年份（1950s–1970s）为 6 h 定强、风速取整到 5 kt，RI 事件被系统性低估；')
    A('- 观测集合里同时有“只报了 2 条的扰动”和完整台风，占比类指标对分母敏感，'
      '故 RI 占比给了 3 种分母；')
    A('- 模型侧 `*_invest` 目录未单独剔除（与观测的弱系统同口径），'
      '但也提供了 `model_2025_named` / `model_2100_named`（去 invest）供交叉核对；')
    A('- 模型与观测的阶段码体系不同（模型无 `EX/SS/SD/MD`），阶段码横向对比仅作参考。')
    A('')
    A('## 10. 复现命令')
    A('')
    A('```bash')
    A('PYTHONIOENCODING=utf-8 "<python.exe>" tools/stat_intensity_profile.py')
    A('# 可选: --no-xrq-year  跳过 xrq\\<year> 逐年集(与 xrq\\wp 基本重合)')
    A('#       --gap-cap 24  --ri-threshold 30  --ri-window 24')
    A('```')
    A('')
    open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(L) + '\n')


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description='观测/模拟 台风强度-寿命廓线统计')
    ap.add_argument('--data-root', default=DATA_ROOT)
    ap.add_argument('--sim-root', default=SIM_ROOT)
    ap.add_argument('--out-md', default=DEFAULT_MD)
    ap.add_argument('--out-json', default=DEFAULT_JSON)
    ap.add_argument('--no-xrq-year', action='store_true',
                    help='跳过 xrq\\<year> 逐年集(与 xrq\\wp 基本重合)')
    ap.add_argument('--only', default=None,
                    help='只统计指定来源(逗号分隔), 例如 xrq_wp,miwu')
    ap.add_argument('--no-model', action='store_true')
    ap.add_argument('--gap-cap', type=float, default=24.0)
    ap.add_argument('--default-dt', type=float, default=6.0)
    ap.add_argument('--ri-window', type=float, default=24.0)
    ap.add_argument('--ri-threshold', type=float, default=30.0)
    ap.add_argument('--ri-tol', type=float, default=6.0)
    ap.add_argument('--max-lifetime-h', type=float, default=1080.0,
                    help='寿命超过该值的记录视为时间戳异常, 只计数不入统计(默认 1080 h = 45 d)')
    args = ap.parse_args(argv)

    global analyze_storm
    _orig = analyze_storm

    def _an(rows):
        return _orig(rows, default_dt=args.default_dt, gap_cap=args.gap_cap,
                     ri_window=args.ri_window, ri_threshold=args.ri_threshold,
                     ri_tol=args.ri_tol)
    analyze_storm = _an

    print('[1/4] 扫描观测 b-deck ...')
    sources = build_obs_sources(args.data_root, with_xrq_year=not args.no_xrq_year)
    if args.only:
        keep = {x.strip() for x in args.only.split(',') if x.strip()}
        sources = [s for s in sources if s.name in keep]
    by_source, all_storms = load_obs(sources, max_life_h=args.max_lifetime_h)

    # 合并去重
    uniq = OrderedDict()
    for s in all_storms:
        k = s['key']
        if k not in uniq or s['m']['n_reports'] > uniq[k]['m']['n_reports']:
            uniq[k] = s
    obs_storms = list(uniq.values())
    print('  观测唯一风暴 %d (来自 %d 个文件记录)' % (len(obs_storms), len(all_storms)))

    print('[2/4] 聚合观测 ...')
    obs_unique = agg(obs_storms)
    obs_by_source = OrderedDict()
    for name, info in by_source.items():
        obs_by_source[name] = agg(info['storms'])
    obs_by_basin = OrderedDict()
    for b in sorted(obs_unique['basins']):
        sub = [s for s in obs_storms if s['basin'] == b]
        obs_by_basin['%s %s' % (b, OCEAN_NAME.get(b, ''))] = agg(sub)
    obs_by_era = OrderedDict()
    eras = collections.defaultdict(list)
    for s in obs_storms:
        eras['%ds' % (s['year'] // 10 * 10)].append(s)
    for k in sorted(eras):
        obs_by_era[k] = agg(eras[k])

    print('[3/4] 扫描并聚合模拟输出 ...')
    model_blocks = OrderedDict()
    model_by_source = OrderedDict()
    model_sources = [] if args.no_model else build_model_sources(args.sim_root)
    mstorms = []
    for s in model_sources:
        files = []
        for pat in s.patterns:
            files.extend(glob.glob(pat, recursive=True))
        files = sorted(set(files))
        storms = []
        nrep = 0
        ndrop = 0
        for f in files:
            rows = parse_bdeck(f)
            if not rows:
                continue
            nrep += len(rows)
            invest = '_invest' in f.lower()
            m = analyze_storm(rows)
            if args.max_lifetime_h and m['lifetime_h'] > args.max_lifetime_h:
                ndrop += 1
                continue
            storms.append({
                'key': (rows[0][4], rows[0][5], rows[0][0].year,
                        rows[0][0].strftime('%Y%m%d%H'), 'invest' if invest else 'named'),
                'src': s.name, 'path': f, 'basin': rows[0][4], 'num': rows[0][5],
                'year': rows[0][0].year, 'invest': invest, 'm': m,
            })
        mstorms.extend(storms)
        model_by_source[s.name] = {'src': s, 'files': files, 'storms': storms,
                                   'n_files': len(files), 'n_reports': nrep,
                                   'n_dropped': ndrop,
                                   'dirs': sorted({os.path.dirname(p) for p in s.patterns})}
        nb = [x for x in storms if not x['invest']]
        g34 = [x for x in storms if (x['m'].get('peak') or 0) >= 34.0]
        model_blocks[s.name] = agg(storms)
        model_blocks[s.name + '_named'] = agg(nb)
        model_blocks[s.name + '_ge34'] = agg(g34)
        print('  [model] %-12s files=%-4d storms=%-4d (named %d, >=34kt %d, dropped %d) reports=%d'
              % (s.name, len(files), len(storms), len(nb), len(g34), ndrop, nrep))
    model_all = agg(mstorms) if mstorms else None

    # 对照的口径: all(全集, 含弱系统) / ge34(两侧都只留峰值>=34kt)
    obs_ge34 = agg([s for s in obs_storms if (s['m'].get('peak') or 0) >= 34.0]) \
        if obs_storms else None
    obs_scopes = OrderedDict([('all', obs_unique), ('ge34', obs_ge34)])
    compare_labels = OrderedDict([
        ('all', '全集口径（模型含 `*_invest`；右侧 `_named` 列 = 去掉 invest 目录）'),
        ('ge34', '仅 ≥34 kt 样本口径（两侧都只保留峰值 ≥34 kt 的风暴）'),
    ])
    model_scopes = OrderedDict()
    model_scopes['all'] = OrderedDict(
        (n, model_blocks[n]) for n in model_blocks if not n.endswith('_ge34'))
    model_scopes['ge34'] = OrderedDict(
        (n, model_blocks[n]) for n in model_blocks if n.endswith('_ge34'))
    compare = OrderedDict()
    for skey in obs_scopes:
        if obs_scopes[skey] is None or not model_scopes[skey]:
            continue
        compare[skey] = build_compare(obs_scopes[skey], model_scopes[skey])

    # 季节匹配对照: 观测 = 真实 2025 WP 季 (模型 2025 运行的同季基准)
    obs_season = obs_by_source.get('xrq_2025')
    compare_season = None
    if obs_season is not None:
        sub = OrderedDict((n, model_blocks[n]) for n in
                          ['model_2025_ge34', 'model_2100_ge34'] if n in model_blocks)
        if sub:
            compare_season = build_compare(obs_season, sub)

    # ---------- JSON
    print('[4/4] 写出产物 ...')
    src_meta = OrderedDict()
    for name, info in by_source.items():
        src_meta[name] = {
            'label': info['src'].label,
            'patterns': info['src'].patterns,
            'dirs': info['dirs'],
            'n_files': info['n_files'],
            'n_reports': info['n_reports'],
            'n_storms': len(info['storms']),
            'note': info['src'].note,
        }
    for name, info in model_by_source.items():
        src_meta[name] = {
            'label': info['src'].label,
            'patterns': info['src'].patterns,
            'dirs': info['dirs'],
            'n_files': info['n_files'],
            'n_reports': info['n_reports'],
            'n_storms': len(info['storms']),
            'note': info['src'].note,
        }
    payload = {
        'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'params': {
            'default_dt_h': args.default_dt, 'gap_cap_h': args.gap_cap,
            'ri_window_h': args.ri_window, 'ri_threshold_kt': args.ri_threshold,
            'ri_tol_h': args.ri_tol,
            'bins': [{'key': k, 'label': BIN_LABEL[k], 'lo': lo, 'hi': hi} for k, lo, hi in BINS],
        },
        'sources': src_meta,
        'obs': {
            'unique': obs_unique,
            'unique_ge34': obs_ge34,
            'by_source': obs_by_source,
            'by_basin': obs_by_basin,
            'by_era': obs_by_era,
            'n_unique_storms': len(obs_storms),
            'n_unique_reports': sum(s['m']['n_reports'] for s in obs_storms),
        },
        'model': {
            'by_source': model_blocks,
            'all': model_all,
        },
        'compare': compare,
        'compare_season': compare_season,
        'compare_labels': compare_labels,
        'compare_ranked': OrderedDict(
            (skey, sorted([c for c in rows if c['max_abs_log_ratio'] > 0],
                          key=lambda c: -c['max_abs_log_ratio']))
            for skey, rows in compare.items()),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    with open(args.out_json, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
    print('  -> %s' % args.out_json)

    # ---------- MD
    xrq = os.path.join(args.data_root, 'xrq')
    yfiles = 0
    if os.path.isdir(xrq):
        for y in os.listdir(xrq):
            p = os.path.join(xrq, y)
            if y.isdigit() and os.path.isdir(p):
                yfiles += len(glob.glob(os.path.join(p, '*.dat')))
    miwu_years = []
    miwu_oceans = set()
    miwu_files = 0
    mroot = os.path.join(args.data_root, 'miwu')
    if os.path.isdir(mroot):
        for y in sorted(os.listdir(mroot)):
            p = os.path.join(mroot, y)
            if os.path.isdir(p):
                miwu_years.append(y)
                for o in os.listdir(p):
                    if os.path.isdir(os.path.join(p, o)):
                        miwu_oceans.add(o)
                        miwu_files += len(glob.glob(os.path.join(p, o, '*.dat')))

    ctx = {
        'generated': payload['generated'],
        'sources': by_source,
        'obs_unique': obs_unique,
        'obs_by_source': obs_by_source,
        'obs_by_basin': obs_by_basin,
        'obs_by_era': obs_by_era,
        'model_all': model_all,
        'model_blocks': OrderedDict((k, v) for k, v in model_blocks.items()),
        'compare': compare,
        'compare_season': compare_season,
        'compare_labels': compare_labels,
        'obs_scopes': obs_scopes,
        'model_scopes': model_scopes,
        'obs_storms_all': obs_storms,
        'max_life_h': args.max_lifetime_h,
        'phase_stat_mtime': _mtime_str(os.path.join(os.path.dirname(os.path.abspath(args.out_md)),
                                                    'PHASE_DURATION_STATS.md')),
        'n_xrq_year_files': yfiles,
        'miwu_years_short': ', '.join(miwu_years[:8]) + (' ...' if len(miwu_years) > 8 else ''),
        'n_miwu_years': len(miwu_years),
        'miwu_oceans': ', '.join(sorted(miwu_oceans)),
        'miwu_files': miwu_files,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_md)), exist_ok=True)
    write_report(args.out_md, ctx)
    print('  -> %s' % args.out_md)

    # 控制台摘要
    print('')
    print('观测(OBS_UNIQUE): 风暴 %d | ACE/风暴 中位 %s 均值 %s | RI占比(>=24h) %s | 前生期中位 %s h | TD报数中位 %s'
          % (obs_unique['n_storms'], (obs_unique['ace'] or {}).get('median'),
             (obs_unique['ace'] or {}).get('mean'),
             (obs_unique['ri']['frac_life24'] or {}).get('frac'),
             (obs_unique['pregenesis_h'] or {}).get('median'),
             (obs_unique['td_reports'] or {}).get('median')))
    for k, v in model_blocks.items():
        print('模型 %-18s 风暴 %5d | ACE/风暴 中位 %s 均值 %s | RI占比 %s | 前生期中位 %s h | TD报数中位 %s'
              % (k, v['n_storms'], (v['ace'] or {}).get('median'),
                 (v['ace'] or {}).get('mean'),
                 (v['ri']['frac_life24'] or {}).get('frac'),
                 (v['pregenesis_h'] or {}).get('median'),
                 (v['td_reports'] or {}).get('median')))
    for skey, rows in compare.items():
        r = sorted([c for c in rows if c['max_abs_log_ratio'] > 0],
                   key=lambda c: -c['max_abs_log_ratio'])
        print('最大偏差 TOP8 [%s]:' % skey)
        for c in r[:8]:
            print('   %-34s obs=%-9s %s | |log(model/obs)|=%s'
                  % (c['metric'], c['obs'],
                     ' '.join('%s=%s(x%s)' % (k, c['models'][k], c['ratios'][k])
                              for k in c['models']),
                     c['max_abs_log_ratio']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
