# -*- coding: utf-8 -*-
"""按**洋区 × 年份**统计 ACE（给"实时 ACE 折线图"做背景数据）。

数据口径（用户 2026-10-19 指定）：
    西北太平洋  → `G:\\气象\\数据\\xrq\\<年>\\bwp*.dat`      （xrq 评）
    其它洋区    → `G:\\气象\\数据\\官评\\<年>\\b<洋区>*\\*.dat`（官评，排除 bwp）

ACE 定义：Σ(v²/10000)，只累计 v ≥ 35kt 的点（每 6 小时一个点）。

产出：`assets/ace_basin_years.json`
    {"meta": {...}, "basins": {"WP": {"2013": 268.4, ...}, "EP": {...}}}
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XRQ = r'G:\气象\数据\xrq'
OFFICIAL = r'G:\气象\数据\官评'
OUT = os.path.join(ROOT, 'assets', 'ace_basin_years.json')

#: b-deck 前缀 → 洋区代码/中文名
BASINS = [
    ('bwp', 'WP', '西北太平洋'),
    ('bep', 'EP', '东北太平洋'),
    ('bcp', 'CP', '中太平洋'),
    ('bal', 'NA', '北大西洋'),
    ('bio', 'NI', '北印度洋'),
    ('bsh', 'SH', '南半球'),
    ('bls', 'SA', '南大西洋'),
]


def storm_ace(path):
    """单个 b-deck 文件的 ACE。字段：[0]洋区 [1]编号 [2]时间 [6]纬度 [7]经度 [8]风速"""
    ace = 0.0
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split(',')
                if len(parts) < 9:
                    continue
                try:
                    v = float(parts[8].strip() or 0)
                except ValueError:
                    continue
                if v >= 35:                      # 热带风暴及以上才累计
                    ace += (v * v) / 10000.0
    except OSError:
        return 0.0
    return ace


def year_ace_for_prefix(year_dir, prefix):
    total = 0.0
    n = 0
    for path in glob.glob(os.path.join(year_dir, prefix + '*.dat')):
        a = storm_ace(path)
        if a > 0:
            total += a
            n += 1
    return total, n


def years_in(root):
    out = []
    for name in os.listdir(root):
        if len(name) == 4 and name.isdigit():
            out.append(int(name))
    return sorted(out)


def storm_daily(path):
    """单个风暴：{(月,日): 当日累计 ACE}"""
    out = {}
    try:
        for line in open(path, 'r', encoding='utf-8', errors='replace'):
            parts = line.split(',')
            if len(parts) < 9:
                continue
            ts = (parts[2] or '').strip()
            if len(ts) < 8 or not ts[:8].isdigit():
                continue
            try:
                v = float(parts[8].strip() or 0)
            except ValueError:
                continue
            if v < 35:
                continue
            key = int(ts[4:6]), int(ts[6:8])
            out[key] = out.get(key, 0.0) + (v * v) / 10000.0
    except OSError:
        return {}
    return out


def year_daily(year_dir, prefix):
    """该年该洋区的**按日累计** ACE：长度 367 的列表（索引 = 一年中的第几天）"""
    import datetime as _dt
    daily = [0.0] * 367
    for path in glob.glob(os.path.join(year_dir, prefix + '*.dat')):
        for (mo, dd), ace in storm_daily(path).items():
            try:
                idx = _dt.date(2001, mo, dd).timetuple().tm_yday   # 用平年换算
            except ValueError:
                continue
            if 1 <= idx <= 366:
                daily[idx] += ace
    cum, run = [], 0.0
    for v in daily:
        run += v
        cum.append(round(run, 2))
    return cum


def main():
    result = {code: {} for _p, code, _cn in BASINS}
    counts = {code: {} for _p, code, _cn in BASINS}

    # 西北太平洋：xrq
    for year in years_in(XRQ):
        d = os.path.join(XRQ, str(year))
        ace, n = year_ace_for_prefix(d, 'bwp')
        if n:
            result['WP'][str(year)] = round(ace, 2)
            counts['WP'][str(year)] = n
    # 其它洋区：官评（排除 bwp）
    for year in years_in(OFFICIAL):
        d = os.path.join(OFFICIAL, str(year))
        for prefix, code, _cn in BASINS:
            if code == 'WP':
                continue
            ace, n = year_ace_for_prefix(d, prefix)
            if n:
                result[code][str(year)] = round(result[code].get(str(year), 0.0) + ace, 2)
                counts[code][str(year)] = counts[code].get(str(year), 0) + n

    meta = {
        'source_wp': 'xrq (西太)',
        'source_other': '官评',
        'ace_rule': 'sum(v^2/10000) for v>=35kt',
        'basins': {code: cn for _p, code, cn in BASINS},
        'years': {code: [min(map(int, v)), max(map(int, v))] if v else [] for code, v in result.items()},
    }
    #: 按日累计曲线（折线图用）：{basin: {year: [367 个累计值]}}
    daily = {}
    for year in years_in(XRQ):
        d = os.path.join(XRQ, str(year))
        if glob.glob(os.path.join(d, 'bwp*.dat')):
            daily.setdefault('WP', {})[str(year)] = year_daily(d, 'bwp')
    for year in years_in(OFFICIAL):
        d = os.path.join(OFFICIAL, str(year))
        for prefix, code, _cn in BASINS:
            if code == 'WP':
                continue
            if glob.glob(os.path.join(d, prefix + '*.dat')):
                daily.setdefault(code, {})[str(year)] = year_daily(d, prefix)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'basins': result, 'daily': daily},
                  f, ensure_ascii=False, indent=0)

    print('写入', OUT, os.path.getsize(OUT), 'bytes')
    print('%-4s %6s %8s %8s' % ('洋区', '年数', '起', '止'))
    for _p, code, cn in BASINS:
        ys = sorted(int(y) for y in result[code])
        if not ys:
            print('%-4s %6d' % (code, 0))
            continue
        print('%-4s %6d %8d %8d' % (code, len(ys), ys[0], ys[-1]))
    for y in ('2013', '2015', '2024', '2025'):
        row = ['%s:%s' % (b, result[b].get(y, '-')) for b in ('WP', 'EP', 'NA', 'NI', 'SH')]
        print('%s  ' % y + '  '.join(row))


if __name__ == '__main__':
    sys.exit(main())
