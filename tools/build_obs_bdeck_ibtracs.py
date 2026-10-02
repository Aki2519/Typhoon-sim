# -*- coding: utf-8 -*-
"""用 IBTrACS 给**其它洋区**造观测 b-deck, 供强度验收用。

为什么需要: 本地只有西太 2025 的观测集(`G:\\气象\\数据\\xrq\\2025` 里 32 个 bwp*),
SH/EP/AL/IO 要对比就得有对应观测 —— 用户也明确要求"去官评目录/jtwc官网/nhc官网找其它洋区的"。

数据源: IBTrACS v04r01 `last3years`(NCEI 公开, 与 build_name_times_multi 同一个缓存文件)。
输出: `simulator/env/train_cache/ibtracs/bdeck/<年>/<洋区>/b<洋区><NN><年>.dat`
      —— **写我们自己的缓存目录, 不动用户的数据目录**。
格式: 与自评简化 b-deck 同构(12 列), 这样 tools/compare_staged_vs_obs.py 与
      train_tc_clim.parse_atcf 都能直接读(下标 [6]lat [7]lon [8]vmax 一致)。

风力优先 `USA_WIND`(JTWC/NHC 1 分钟风, 与主流 best track 一致), 缺则 `WMO_WIND`。

用法:
  python tools/build_obs_bdeck_ibtracs.py                 # 2023-2026 全部洋区
  python tools/build_obs_bdeck_ibtracs.py --years 2025
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CSV = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'ibtracs', 'last3years.csv')
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'ibtracs', 'bdeck')
BASIN_MAP = {'NA': 'AL', 'EP': 'EP', 'CP': 'EP', 'WP': 'WP',
             'SP': 'SH', 'SI': 'IO', 'NI': 'IO', 'SA': 'SA'}
SUFFIX = {'AL': 'L', 'EP': 'E', 'WP': 'W', 'SH': 'S', 'IO': 'A', 'SA': 'Q'}
#: 只收 6 小时整点(与模型 b-deck 同步长)
WANT_HOURS = ('00', '06', '12', '18')


def _f(s):
    try:
        v = float(s)
        return v if np.isfinite(v) else None
    except Exception:
        return None


def _lat(v):
    """IBTrACS 纬度(十进制度, 南为负) → 自评格式 138N / 75S(一位小数去掉点)"""
    s = 'N' if v >= 0 else 'S'
    d = abs(v) * 10.0
    return '%03d%s' % (int(round(d)), s)


def _lon(v):
    s = 'E' if v >= 0 else 'W'
    d = abs(v) * 10.0
    return '%04d%s' % (int(round(d)), s)


def build(years=None, out=OUT):
    if not os.path.exists(CSV):
        raise SystemExit('缺 IBTrACS 缓存: %s(先跑 tools/build_name_times_multi.py)' % CSV)
    storms = {}
    with io.open(CSV, encoding='utf-8', errors='replace', newline='') as f:
        rd = csv.reader(f)
        head = next(rd)
        next(rd)
        ix = {k.strip(): i for i, k in enumerate(head)}
        for row in rd:
            if len(row) < len(head):
                continue
            bas = row[ix['BASIN']].strip()
            if bas not in BASIN_MAP:
                continue
            b = BASIN_MAP[bas]
            sid = row[ix['SID']].strip()
            t = row[ix['ISO_TIME']].strip()
            if not sid or not t or t[11:13] not in WANT_HOURS:
                continue
            yr = t[:4]
            if years and yr not in years:
                continue
            v = _f(row[ix['USA_WIND']])
            if v is None:
                v = _f(row[ix['WMO_WIND']])
            if v is None:
                continue
            la, lo = _f(row[ix['USA_LAT']] or row[ix['LAT']]), _f(row[ix['USA_LON']] or row[ix['LON']])
            if la is None or lo is None:
                continue
            if lo > 180.0:
                lo -= 360.0
            p = _f(row[ix['USA_PRES']]) or _f(row[ix['WMO_PRES']]) or 0.0
            storms.setdefault((yr, b, sid), []).append((t, la, lo, v, p))
    n_files = 0
    for (yr, b, sid), rows in sorted(storms.items()):
        rows.sort()
        if len(rows) < 4:
            continue
        d = os.path.join(out, yr, b)
        os.makedirs(d, exist_ok=True)
        cy = len([f for f in os.listdir(d) if f.endswith('.dat')]) + 1
        fp = os.path.join(d, 'b%s%02d%s.dat' % (b.lower(), cy, yr))
        with io.open(fp, 'w', encoding='utf-8') as fh:
            for t, la, lo, v, p in rows:
                st = 'DB' if v < 34 else ('TS' if v < 64 else ('TY' if v < 96 else 'ST'))
                # IBTrACS 的 ISO_TIME 是 "2025-07-03 12:00:00"(**空格**分隔, 不是 T),
                # 所以必须先剥掉所有非数字再取前 10 位 —— 第一版只 replace('-',':','T'),
                # 得到 "20250703 1", parse_atcf 一看 len!=10 就把**每一行**都跳过了(实测 0 样本)。
                _ts = re.sub(r'\D', '', t)[:10]
                if len(_ts) != 10:
                    continue
                fh.write('%s, %02d, %s,   ,IBTrACS,   0,%5s,%6s,%4d,%5d,%3s,%02d%s\n'
                         % (b, cy, _ts,
                            _lat(la), _lon(lo), int(round(v)), int(round(p)) if p else 0,
                            st, cy, SUFFIX.get(b, 'X')))
        n_files += 1
    print('写出 %d 个观测 b-deck → %s' % (n_files, out))
    return n_files


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default=None, help='逗号分隔, 默认全部')
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args(argv)
    ys = [x.strip() for x in a.years.split(',')] if a.years else None
    build(ys, a.out)
    return 0


if __name__ == '__main__':
    main()
