# -*- coding: utf-8 -*-
"""把**其它洋区**(AL/EP/IO/SH)的真实命名时间补进 tc_name_times.json。

用户 2026-09-21: "去官评目录/jtwc官网/nhc官网找其它洋区的真实命名时间"。

数据源与取舍
------------
* **WP 已经用 JMA bst 官方命名时刻**(`tools/build_name_times.py`, grade>=TS) —— 比任何
  再分析都准, 本工具**不覆盖** WP。
* 官评目录 `G:\\气象\\数据\\xrq\\{wp,sh}` 与 `自评/2025/2025other` 是**官方 b-deck 的
  去名版**(第 5 列是 `XRQA` 这种占位技术码, 没有 NAME 列), 只有经纬/强度 ——
  可以用来校强度, 取不到命名时间。
* NHC/JTWC 的 ATCF b-deck 归档里, AL/EP/CP 有 NAME 列, 但 SH/IO 不公开批量。
* 所以统一走 **IBTrACS v04r01**(NCEI, 公开 CSV, 汇集各 RSMC 的官方报文):
  `NAME` 给官方名字, `WMO_WIND`(RSMC 10 分钟风)/`USA_WIND`(JTWC/NHC 1 分钟风)≥34 kt
  的**首个时次**作为命名时刻(与 naming.NAME_KT=34 同口径)。

产出(合并写入, 保留已有条目)
  simulator/env/model/tc_name_times.json   {洋区: {"年": [{cy,name,named,first,src}...]}}
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'model', 'tc_name_times.json')
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'ibtracs')
URL = ('https://www.ncei.noaa.gov/data/international-best-track-archive-for-'
       'climate-stewardship-ibtracs/v04r01/access/csv/'
       'ibtracs.last3years.list.v04r01.csv')

#: IBTrACS 洋区 → 本工程洋区码(与 naming.basin_of / b-deck 后缀一致)
BASIN_MAP = {'NA': 'AL', 'EP': 'EP', 'CP': 'EP', 'WP': 'WP',
             'SP': 'SH', 'SI': 'IO', 'NI': 'IO', 'SA': 'SA'}
#: 我们只补这几个(WP 走 JMA, 不被覆盖)
#: AU/FJ/MU/ID 是南半球的 WMO 子洋区 —— naming.next_name 会把 SH/SI/SP/SA
#: 先解析成子洋区再查表, 所以**必须**按经度拆出来, 只给 SH 是查不到的。
_TARGET = ('AL', 'EP', 'IO', 'SH', 'SA', 'AU', 'FJ', 'MU', 'ID')
KT = 34.0


def fetch(path=URL, force=False):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, 'last3years.csv')
    if os.path.exists(fp) and not force and os.path.getsize(fp) > 100000:
        return fp
    print('下载 %s' % path, flush=True)
    with urllib.request.urlopen(path, timeout=120) as r, open(fp, 'wb') as f:
        f.write(r.read())
    print('  -> %s (%.1f MB)' % (fp, os.path.getsize(fp) / 1e6), flush=True)
    return fp


def _num(s):
    try:
        return float(s)
    except Exception:
        return None


def parse(fp):
    """→ {洋区: {年: {SID: {'name':..,'named':..,'first':..,'wmo':..,'usa':..}}}}"""
    out = {}
    with io.open(fp, encoding='utf-8', errors='replace', newline='') as f:
        rd = csv.reader(f)
        head = next(rd)
        next(rd)                                  # 第二行是单位
        idx = {k.strip(): i for i, k in enumerate(head)}
        for col in ('SID', 'SEASON', 'BASIN', 'NAME', 'ISO_TIME', 'WMO_WIND',
                    'USA_WIND', 'LON', 'LAT'):
            if col not in idx:
                raise RuntimeError('IBTrACS 缺列 %s (实际: %s)' % (col, head[:12]))
        for row in rd:
            if len(row) < len(head):
                continue
            sid = row[idx['SID']]
            bas = row[idx['BASIN']].strip()
            if not sid or bas not in BASIN_MAP:
                continue
            name = (row[idx['NAME']] or '').strip()
            t = (row[idx['ISO_TIME']] or '').strip()
            if not t or name in ('', 'NOT_NAMED', 'UNNAMED'):
                continue
            bk = BASIN_MAP[bas]
            season = row[idx['SEASON']].strip()
            if not season.isdigit():
                continue
            rec = out.setdefault(bk, {}).setdefault(season, {}).setdefault(
                sid, {'name': name.upper(), 'named': None, 'first': None,
                      'wmo': None, 'usa': None, 'lon': None, 'lat': None})
            if rec['first'] is None or t < rec['first']:
                rec['first'] = t
            if rec['lon'] is None:
                rec['lon'] = _num(row[idx['LON']])
                rec['lat'] = _num(row[idx['LAT']])
            w, u = _num(row[idx['WMO_WIND']]), _num(row[idx['USA_WIND']])
            hit = None
            if w is not None and w >= KT:
                hit = 'WMO'
            if u is not None and u >= KT and hit is None:
                hit = 'USA'
            if hit and (rec['named'] is None or t < rec['named']):
                rec['named'] = t
            if hit == 'WMO' and (rec['wmo'] is None or t < rec['wmo']):
                rec['wmo'] = t
            if hit == 'USA' and (rec['usa'] is None or t < rec['usa']):
                rec['usa'] = t
    return out


def merge(new, path=OUT, only=_TARGET):
    cur = {}
    if os.path.exists(path):
        with io.open(path, encoding='utf-8') as f:
            cur = json.load(f)
    # 南半球: 按**生成点经度**拆到 WMO 子洋区(AU/FJ/MU/ID), 否则 next_name 查不到
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from simulator.simcore.naming import sub_basin
    for bk in ('SH', 'IO', 'SA'):
        for yr, storms in (new.get(bk) or {}).items():
            for sid, r in storms.items():
                if r.get('lat') is None or r['lat'] >= 0 or r.get('lon') is None:
                    continue
                sub = sub_basin(r['lon'], r['lat'])
                if sub:
                    new.setdefault(sub, {}).setdefault(yr, {})[sid] = r

    added = replaced = 0
    for bk, yrs in new.items():
        if bk not in only:
            continue
        tgt = cur.setdefault(bk, {})
        for yr, storms in yrs.items():
            rows = []
            for sid, r in sorted(storms.items(), key=lambda kv: kv[1]['named'] or kv[1]['first']):
                if r['named'] is None:
                    continue                       # 没达到命名强度(纯 TD)不算命名风暴
                rows.append({'name': r['name'],
                             'named': r['named'][:16].replace('T', ' '),
                             'first': (r['first'] or r['named'])[:16].replace('T', ' '),
                             'src': 'IBTrACS' + ('/WMO' if r['wmo'] else '/USA')})
            rows.sort(key=lambda x: x['named'])
            for i, x in enumerate(rows, 1):
                x['cy'] = i
                x['name'] = x.pop('name')
            if not rows:
                continue
            if yr in tgt:
                replaced += 1
            else:
                added += 1
            tgt[yr] = [{'cy': x['cy'], 'name': x['name'], 'named': x['named'],
                        'first': x['first'], 'src': x['src']} for x in rows]
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    return cur, added, replaced


def main(argv=None):
    force = '--force' in (argv or sys.argv[1:])
    fp = fetch(force=force)
    new = parse(fp)
    for bk in sorted(new):
        yrs = {y: len(v) for y, v in sorted(new[bk].items())}
        print('  %-3s %s' % (bk, yrs))
    cur, added, replaced = merge(new)
    print('写入 %s: 新增 %d 个年-洋区, 覆盖 %d 个' % (OUT, added, replaced))
    for bk in sorted(cur):
        ys = sorted(cur[bk])
        print('  %-3s 年份 %s' % (bk, ', '.join('%s(%d)' % (y, len(cur[bk][y])) for y in ys)))
    return cur


if __name__ == '__main__':
    main()
