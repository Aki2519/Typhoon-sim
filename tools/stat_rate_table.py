# -*- coding: utf-8 -*-
"""观测侧的**分层速率表**: 把"6h 内强度变化"按 (强度档 × 环境) 分箱统计。

为什么需要(INTENSITY_REFACTOR_PLAN.md 错误 2/3):
  我之前拿"风暴级聚合量"(峰值中位/ACE/前生期)当标定目标, 样本只有每年十几个系统 ⇒ 一直在噪声里。
  必须换成**步级条件统计**: 例如"所有 6h 步里, 当前 80-100 kt 且暖海面上的减弱速率" = 数千条样本。

输出三张表(都给中位与样本量):
  1. `rate_by_v`:      按当前强度档分箱的 dV/6h(增强/减弱分开)
  2. `rate_by_env`:    在固定强度档内, 按 (SST 档 × 陆海 × 切变档) 分箱 ⇒ 反解环境乘子 m_sst/m_land/m_shear
  3. `time_by_v`:      每个强度档的**停留时长**(小时) —— 直接对标 ">=100kt 停留 27-42h"

环境量从**模型用的月气候态**查表得到(观测 b-deck 本身没有 SST/切变):
  SST ← simulator/env/model/base_sst.npz(OISST 1991-2020 月气候态)
  切变 ← base_shear.npz(NCEP 月均)      ← **只是代理**, 报告里必须标注
  陆海 ← land_cell.npz(landfrac >= 0.999 视为陆地)
时间分辨率按**时间戳**算, 不用固定 6h(数据里有 1-12h 混步长)。

用法:
  python tools/stat_rate_table.py                        # 默认 xrq 全量 + 自评 + miwu
  python tools/stat_rate_table.py --only xrq_2025,xrq_wp --out output/sim/_reference/rate_tables.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import datetime as dt

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
sys.path.insert(0, ROOT)

V_BINS = ((0, 34, '<34'), (34, 48, '34-48'), (48, 64, '48-64'), (64, 80, '64-80'),
          (80, 100, '80-100'), (100, 1e9, '100+'))
SST_BINS = ((-9, 25, 'sst<25'), (25, 26.5, '25-26.5'), (26.5, 28.5, '26.5-28.5'),
            (28.5, 99, '>=28.5'))
SHR_BINS = ((0, 10, 'shr<10'), (10, 20, '10-20'), (20, 30, '20-30'), (30, 1e9, '>=30'))


def _lat(s):
    m = re.match(r'^(\d+)([NS])$', (s or '').strip())
    return None if not m else (int(m.group(1)) / 10.0) * (1 if m.group(2) == 'N' else -1)


def _lon(s):
    m = re.match(r'^(\d+)([EW])$', (s or '').strip())
    return None if not m else (int(m.group(1)) / 10.0) * (1 if m.group(2) == 'E' else -1)


def _t(s):
    s = (s or '').strip()
    if len(s) == 10 and s.isdigit():
        return dt.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]))
    return None


def read_storm(path):
    """→ [(t, lat, lon, vmax)] 按时间去重(同刻留最后)。"""
    rows = {}
    for ln in open(path, encoding='utf-8', errors='replace'):
        c = [x.strip() for x in ln.split(',')]
        if len(c) < 11:
            continue
        t, la, lo = _t(c[2]), _lat(c[6]), _lon(c[7])
        if t is None or la is None or lo is None:
            continue
        try:
            v = float(c[8])
        except ValueError:
            continue
        if v > 300:
            continue
        rows[t] = (la, lo, v)
    return [(t, *rows[t]) for t in sorted(rows)]


class Env:
    """月气候态查表(仅作环境的**代理**; 观测 b-deck 本身无 SST/切变)。"""

    def __init__(self):
        self.ok = False
        try:
            with np.load(os.path.join(MODEL, 'base_sst.npz')) as z:
                self.sst = np.asarray(z['sst'], np.float32)
            with np.load(os.path.join(MODEL, 'base_shear.npz')) as z:
                self.shr = np.asarray(z['shear'], np.float32)
            with np.load(os.path.join(MODEL, 'land_cell.npz')) as z:
                self.land = np.asarray(z['landfrac'], np.float32)
            self.ok = True
        except Exception as exc:
            print('  (环境表不可用: %s —— 只出 rate_by_v)' % exc)

    def at(self, t, la, lo):
        if not self.ok:
            return np.nan, np.nan, np.nan
        j = int(np.clip(round((la + 89.75) / 0.5), 0, self.land.shape[0] - 1))
        i = int(np.clip(round((lo % 360.0) / 0.5 - 0.5), 0, self.land.shape[1] - 1))
        m = t.month - 1
        sst = float(self.sst[m, j, i])
        shr = float(self.shr[m, j, i])
        if self.shr.shape[0] != self.sst.shape[0]:      # 切变是 0.5°, SST 也是; 若不同则粗采样
            shr = float(self.shr[m, min(j // max(1, self.sst.shape[0] // self.shr.shape[0]),
                                        self.shr.shape[0] - 1), i])
        return sst, shr, float(self.land[j, i])


def _bin(x, bins):
    for lo, hi, nm in bins:
        if lo <= x < hi:
            return nm
    return None


def collect(paths, env, max_gap_h=12.0):
    by_v = collections.defaultdict(lambda: collections.defaultdict(list))
    by_env = collections.defaultdict(lambda: collections.defaultdict(list))
    time_by_v = collections.defaultdict(list)
    n_rec = 0
    for f in paths:
        rec = read_storm(f)
        if len(rec) < 3:
            continue
        for (t0, la0, lo0, v0), (t1, la1, lo1, v1) in zip(rec, rec[1:]):
            gap = (t1 - t0).total_seconds() / 3600.0
            if not (0 < gap <= max_gap_h):
                continue
            rate = (v1 - v0) / gap * 6.0            # kt / 6h
            bv = _bin(v0, V_BINS)
            if bv is None:
                continue
            n_rec += 1
            by_v[bv]['all'].append(rate)
            (by_v[bv]['up'] if rate > 0 else by_v[bv]['down']).append(rate)
            time_by_v[bv].append(gap)
            if env.ok and v0 >= 34:
                sst, shr, lf = env.at(t0, la0, lo0)
                if np.isfinite(sst):
                    k1, k2, k3 = _bin(sst, SST_BINS), _bin(shr, SHR_BINS), \
                        ('land' if lf >= 0.999 else 'sea')
                    for k in (k1, k2, k3):
                        if k:
                            by_env[bv][k].append(rate)
    return by_v, by_env, time_by_v, n_rec


def _stat(xs):
    a = np.asarray(xs, float)
    if a.size == 0:
        return None
    return {'n': int(a.size), 'med': float(np.median(a)),
            'mean': float(a.mean()), 'p10': float(np.percentile(a, 10)),
            'p90': float(np.percentile(a, 90))}


def report(by_v, by_env, time_by_v, n_rec, out):
    print('=== 步级速率表(观测; 环境量来自月气候态代理)===  样本 %d 步' % n_rec)
    print('%-8s %6s %9s %9s %9s' % ('强度档', 'n', '增强中位', '减弱中位', '净中位'))
    res = {'by_v': {}, 'by_env': {}, 'time_by_v': {}, 'n': n_rec}
    for _, _, nm in V_BINS:
        d = by_v.get(nm)
        if not d:
            continue
        up, dn, al = _stat(d.get('up', [])), _stat(d.get('down', [])), _stat(d['all'])
        print('%-8s %6d %9s %9s %9.2f' % (nm, al['n'],
                                          ('%.1f' % up['med']) if up else '-',
                                          ('%.1f' % dn['med']) if dn else '-', al['med']))
        res['by_v'][nm] = {'up': up, 'down': dn, 'all': al}
        res['time_by_v'][nm] = {'gap_med_h': float(np.median(time_by_v[nm])),
                                'n': len(time_by_v[nm])}
    print()
    print('在 64-80 / 80-100 / 100+ kt 档内按环境分层(只算 >=34kt 的步):')
    for _, _, nm in V_BINS:
        d = by_env.get(nm)
        if not d:
            continue
        for k in sorted(d):
            st = _stat(d[k])
            if st and st['n'] >= 30:
                print('   %-8s %-10s n=%-5d 中位 %+6.2f kt/6h' % (nm, k, st['n'], st['med']))
                res['by_env'].setdefault(nm, {})[k] = st
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('-> ' + out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default=None, help='xrq_2025,xrq_wp,zi,miwu 之一或逗号分隔')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output', 'sim', '_reference',
                                                  'rate_tables.json'))
    ap.add_argument('--max-gap', type=float, default=12.0)
    a = ap.parse_args(argv)
    groups = {
        'xrq_2025': sorted(glob.glob(r'G:\气象\数据\xrq\2025\*.dat')),
        'xrq_2026': sorted(glob.glob(r'G:\气象\数据\xrq\2026\*.dat')),
        'xrq_wp': sorted(glob.glob(r'G:\气象\数据\xrq\wp\*.dat')),
        'zi': sum([sorted(glob.glob(os.path.join(d, '**', '*.dat'), recursive=True))
                   for d in (r'G:\气象\数据\自评\2025', r'G:\气象\数据\自评\2026')], []),
        'miwu': sorted(glob.glob(os.path.join(r'G:\气象\数据\miwu', '**', '*.dat'),
                                 recursive=True)),
    }
    keys = [x.strip() for x in a.only.split(',')] if a.only else list(groups)
    paths = [f for k in keys for f in groups.get(k, [])]
    print('扫描 %d 个文件(%s)' % (len(paths), ','.join(keys)))
    by_v, by_env, tv, n = collect(paths, Env(), a.max_gap)
    report(by_v, by_env, tv, n, a.out)
    return 0


if __name__ == '__main__':
    main()
