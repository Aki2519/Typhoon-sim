# -*- coding: utf-8 -*-
"""从 **JMA 官方 RSMC best track**(bstYYYY.txt)抽取每个台风的**真实命名时刻**, 存成 JSON。

用户 2026-09-20: "你获取每个tc的真实命名时间. 存起来. 而不是靠指针按日期比例推进"
→ 所以模拟里给台风命名时, 应该按**真实命名时刻**去取名字/编号, 而不是用季节比例推指针。

JMA best track 文本格式(实测):
    头部: 66666 2501  022 0001 2501 0 6                WUTIP              20250912
    记录: 25061012 002 2 150 1142  998     000
          YYMMDDHH  ?  等级 纬度 经度 气压  最大风速(kt)
    等级: 2=TD, 3=TS, 4=STS, 5=TY(命名发生在等级首次 >= 3 的那条)

产物: simulator/env/model/tc_name_times.json
    {"WP": {"2025": [{"cy": 1, "name": "WUTIP", "named": "2025-06-11 12:00"}, ...]}}

用法: python tools/build_name_times.py --years 2024,2025,2026
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'model', 'tc_name_times.json')
TMP = r'G:\tmp\jma'
BASE = 'https://www.jma.go.jp/jma/jma-eng/jma-center/rsmc-hp-pub-eg/Besttracks/bst%d.txt'


def fetch(year):
    os.makedirs(TMP, exist_ok=True)
    p = os.path.join(TMP, 'bst%d.txt' % year)
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return p
    try:
        r = requests.get(BASE % year, timeout=60)
        if r.status_code == 200 and len(r.content) > 1000:
            open(p, 'wb').write(r.content)
            return p
    except Exception as exc:
        print('  下载失败 %d: %s' % (year, str(exc)[:60]))
    return None


def parse(path, year):
    """→ [{cy, name, named, first}]"""
    out = []
    cur = None
    for line in open(path, encoding='ascii', errors='replace'):
        line = line.rstrip('\n')
        if line.startswith('66666'):
            m = re.match(r'^66666\s+(\d{4})\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(.*?)\s*(\d{8})?\s*$', line)
            if not m:
                cur = None
                continue
            cy = int(m.group(1)[2:])                 # 2501 → 1
            name = (m.group(2) or '').strip()
            cur = {'cy': cy, 'name': name or ('WP%02d' % cy),
                   'named': None, 'first': None}
            out.append(cur)
            continue
        if cur is None or len(line) < 24:
            continue
        ts = line[0:8]
        try:
            grade = int(line[12:14].strip())      # 等级在 [12:14](实测: ' 2'/' 3')
        except Exception:
            continue
        iso = '20%s-%s-%s %s:00' % (ts[0:2], ts[2:4], ts[4:6], ts[6:8])
        if cur['first'] is None:
            cur['first'] = iso
        if cur['named'] is None and grade >= 3:       # 首次成为 TS → JMA 命名
            cur['named'] = iso
    # 没到 TS 的(纯扰动)不参与命名表, 但保留 first 供参考
    return [c for c in out if c['named']]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2024,2025,2026')
    a = ap.parse_args(argv)
    table = {}
    for y in [int(x) for x in a.years.split(',') if x.strip()]:
        p = fetch(y)
        if not p:
            print('%d: 拿不到文件' % y)
            continue
        rows = parse(p, y)
        table.setdefault('WP', {})[str(y)] = rows
        print('%d: %d 个命名台风' % (y, len(rows)))
        for r in rows[:6]:
            print('   %02d %-14s 命名于 %s' % (r['cy'], r['name'], r['named']))
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            old = {}
    for b, yrs in table.items():
        old.setdefault(b, {}).update(yrs)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)
    print('写出 %s' % OUT)


if __name__ == '__main__':
    main()
