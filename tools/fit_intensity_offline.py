# -*- coding: utf-8 -*-
"""离线比对: **模型的实际步级速率 vs 观测的分层速率表**。

为什么是这样(INTENSITY_REFACTOR_PLAN.md 错误 2/3 的对策):
  * 不再比"峰值中位/ACE/前生期"这类**风暴级聚合量**(样本只有每年十几个系统, 一直在噪声里);
  * 改成比**步级条件速率**: 同一套分箱(强度档 × SST × 切变 × 陆海), 模型与观测逐格对照;
  * 数据来源: 模型侧 = tools/dump_intensity_samples.py 落盘的每步状态;
              观测侧 = tools/stat_rate_table.py 生成的 rate_tables.json。
  两边都用"相邻两步的 Δvmax / 实际时间间隔"作为速率, 单位统一 kt/6h。

用法:
  python tools/fit_intensity_offline.py                     # 用最新样本 + 默认观测表
  python tools/fit_intensity_offline.py --samples <glob> --obs <json>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

V_BINS = ((0, 34, '<34'), (34, 48, '34-48'), (48, 64, '48-64'), (64, 80, '64-80'),
          (80, 100, '80-100'), (100, 1e9, '100+'))
SST_BINS = ((-9, 25, 'sst<25'), (25, 26.5, '25-26.5'), (26.5, 28.5, '26.5-28.5'),
            (28.5, 99, '>=28.5'))
SHR_BINS = ((0, 10, 'shr<10'), (10, 20, '10-20'), (20, 30, '20-30'), (30, 1e9, '>=30'))


def _b(x, bins):
    for lo, hi, nm in bins:
        if lo <= x < hi:
            return nm
    return None


def load_model(samples_glob, max_gap_h=12.0):
    """→ {(V档, 环境键): [速率...]} + {(V档): [停留时长]}"""
    by = {}
    tv = {}
    n = 0
    for fp in sorted(glob.glob(samples_glob)):
        z = np.load(fp, allow_pickle=False)
        t, tc, v = z['t'], z['tc'], z['vmax']
        sst, shr, org = z['sst'], z.get('shear'), z.get('org')
        seed = z['seed']
        order = np.lexsort((t, tc, seed))
        for k in range(1, order.size):
            i0, i1 = order[k - 1], order[k]
            if tc[i0] != tc[i1] or seed[i0] != seed[i1]:
                continue
            gap = float(t[i1] - t[i0])
            if not (0 < gap <= max_gap_h):
                continue
            rate = float(v[i1] - v[i0]) / gap * 6.0
            bv = _b(float(v[i0]), V_BINS)
            if bv is None:
                continue
            n += 1
            by.setdefault((bv, 'all'), []).append(rate)
            tv.setdefault(bv, []).append(gap)
            for key, bins, val in (('sst', SST_BINS, float(sst[i0])),
                                   ('shr', SHR_BINS, float(shr[i0]) if shr is not None else np.nan)):
                if np.isfinite(val):
                    kb = _b(val, bins)
                    if kb:
                        by.setdefault((bv, kb), []).append(rate)
    return by, tv, n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', default=os.path.join(ROOT, 'output', 'sim', '_reference',
                                                      'samples', 'samples_*.npz'))
    ap.add_argument('--obs', default=os.path.join(ROOT, 'output', 'sim', '_reference',
                                                  'rate_tables.json'))
    ap.add_argument('--min-n', type=int, default=60)
    a = ap.parse_args(argv)
    by, tv, n = load_model(a.samples)
    obs = json.load(open(a.obs, encoding='utf-8')) if os.path.exists(a.obs) else {}
    print('模型步级样本 %d 条(来自 %s)' % (n, os.path.basename(a.samples)))
    print()
    print('%-8s %-11s %7s %9s %9s %8s' % ('强度档', '环境', 'n模型', '模型中位', '观测中位', '倍率'))
    rows = 0
    for _, _, bv in V_BINS:
        o = (obs.get('by_env') or {}).get(bv, {})
        mine = {k[1]: v for k, v in by.items() if k[0] == bv and k[1] != 'all'}
        allv = np.asarray(by.get((bv, 'all'), []), float)
        if allv.size == 0:
            continue
        print('%-8s %-11s %7d %9.2f %9s %8s' % (bv, '(总计·仅供参考)', allv.size,
                                                np.median(allv), '-', '-'))
        for k, xs in sorted(mine.items()):
            if len(xs) < a.min_n:
                continue
            om = (o.get(k) or {}).get('med')
            m = float(np.median(xs))
            r = ('%6.2fx' % (m / om)) if om not in (None, 0) else '   -'
            print('%-8s %-11s %7d %9.2f %9.2f %8s' % ('', k, len(xs), m, om if om is not None else np.nan, r))
            rows += 1
        if tv.get(bv):
            print('%-8s %-11s %7s 停留中位 %.1f h / 样本 %d' % ('', '(停留)', '', float(np.median(tv[bv])), len(tv[bv])))
    print()
    print('说明: 观测中位来自 tools/stat_rate_table.py(环境量是**月气候态代理**);')
    print('      倍率 >1 表示模型比观测更"增强/更少减弱"; <1 相反。逐格对齐是本轮重构的验收尺子。')
    return 0


if __name__ == '__main__':
    main()
