"""多种子验收(目标 ③)—— 拒绝用单种子下结论。

用法:
    python tools/acceptance_multi_seed.py --seeds 20250801,11,23,37,53 --days 150
    python tools/acceptance_multi_seed.py --seeds 1,2,3 --days 150 --json out.json

为什么要这个工具:
  观测的目标值(峰值中位 / ACE P50·P90 / >=100kt 停留 / 前生期)都是**气候统计量**,
  单种子跑出来的数字方差极大 —— 本会话里已经因为"看单种子"误判过多次(峰值中位在
  不同种子间从 26 到 71 都出现过)。所以这里:
    · 每个种子独立跑, 汇总 **均值 +/- 范围**;
    · 报告**成功种子数**, 少于 3 个就明确写"不足以判定", 不给结论;
    · 内存不足等异常按种子跳过并记录, 不会静默丢样本。
"""
import argparse
import io
import json
import statistics as st
import sys

import numpy as np

sys.path.insert(0, '.')
from tools.check_intensity_calib import run_sim, stats_from_tracks  # noqa: E402

# 观测目标(rate_tables.json / INTENSITY_LIFETIME.md; 自评+xrq 分层口径)
OBS = {
    'peak_med': 70.0,        # 峰值中位 kt
    'ace_mean': 7.37,        # ACE/风暴 均值
    'ace_p50': 5.43,         # ACE/风暴 中位
    'pre_h_med': 54.0,       # 前生期(首报->34kt)中位 h
    'td_med': 6.5,           # TD 报数中位
    'decay': {'100+': -12.5, '80-100': -10.0, '64-80': -10.0,
              '48-64': -5.0, '34-48': -5.0},
}


def residence_100(tracks):
    """>=100kt 停留时长(小时/风暴, 中位)。跟踪间隔按 t 差分, 未知则按 6h。"""
    out = []
    for v, _ph in tracks:
        v = np.asarray(v, float)
        if v.size and (v >= 100.0).any():
            out.append(6.0 * int((v >= 100.0).sum()))
    return float(np.median(out)) if out else float('nan'), len(out)


def ace_p90(tracks):
    a = [float(np.sum(np.asarray(v, float)[np.asarray(v, float) >= 34] ** 2) / 1e4)
         for v, _ in tracks if np.asarray(v, float).size]
    a = [x for x in a if x > 0]
    return float(np.percentile(a, 90)) if a else float('nan')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', required=True, help='逗号分隔')
    ap.add_argument('--days', type=float, default=150.0)
    ap.add_argument('--start', default='2025-01-01 00')
    ap.add_argument('--json', default=None)
    a = ap.parse_args()

    seeds = [s.strip() for s in a.seeds.split(',') if s.strip()]
    rows, failed = [], []
    for sd in seeds:
        try:
            tracks = run_sim(a.days, sd, a.start, quiet=True)
            cur = stats_from_tracks(tracks)
        except Exception as e:                      # 含 MemoryError
            failed.append((sd, type(e).__name__))
            print('  seed %-10s 跳过: %s' % (sd, type(e).__name__))
            continue
        res, n100 = residence_100(tracks)
        rows.append(dict(seed=sd, peak_med=cur.get('peak_med'),
                         ace_mean=cur.get('ace_mean'), ace_p50=cur.get('ace_med'),
                         ace_p90=ace_p90(tracks), pre_h_med=cur.get('pre_h_med'),
                         td_med=cur.get('td_med'), res100=res, n100=n100,
                         decay=cur.get('decay') or {}))
        print('  seed %-10s 完成: 峰值中位 %s  ACE P50 %s  >=100kt 停留 %s h'
              % (sd, cur.get('peak_med'), cur.get('ace_med'), res))

    print()
    print('=' * 100)
    print('成功 %d / %d 种子%s' % (len(rows), len(seeds),
          ('   失败: ' + ', '.join('%s(%s)' % x for x in failed)) if failed else ''))
    if len(rows) < 3:
        print('** 成功种子 < 3 => 不足以判定, 不给结论(单种子结论已被本会话反复证伪)。**')
        return

    def agg(key):
        v = [r[key] for r in rows if r.get(key) is not None
             and not (isinstance(r[key], float) and np.isnan(r[key]))]
        return (float(np.mean(v)), float(np.min(v)), float(np.max(v)), len(v)) if v else None

    print('%-22s %10s %10s %10s   %s' % ('指标', '均值', '最小', '最大', '观测/比值'))
    for key, nm, fmt in (('peak_med', '峰值中位 kt', '%.0f'),
                         ('ace_mean', 'ACE/风暴 均值', '%.2f'),
                         ('ace_p50', 'ACE/风暴 P50', '%.2f'),
                         ('ace_p90', 'ACE/风暴 P90', '%.2f'),
                         ('pre_h_med', '前生期中位 h', '%.0f'),
                         ('td_med', 'TD 报数中位', '%.1f'),
                         ('res100', '>=100kt 停留 h', '%.0f')):
        g = agg(key)
        if not g:
            continue
        m, lo, hi, n = g
        ob = OBS.get(key)
        tail = ('观测 %s -> **%.2fx**' % (fmt % ob, m / ob)) if (ob and m and m == m) else '—'
        print('%-22s %10s %10s %10s   %s' % (nm, fmt % m, fmt % lo, fmt % hi, tail))

    print()
    print('峰值后减弱速率(kt/6h, 多种子中位的中位):')
    print('  注: stats_from_tracks 返回的 decay[b] 是 **[中位数, 样本数]** 两元素列表,')
    print('      取值必须用 [0]。此前误当标量取中位 => 算出 (中位+n)/2 的**正值**')
    print('      (34-48: (−5+88)/2 = 41.5 与实测 +39.5 吻合), 已修正。')
    for b in ('100+', '80-100', '64-80', '48-64', '34-48'):
        vals = []
        for r in rows:
            e = r['decay'].get(b)
            if e is None:
                continue
            vals.append(float(e[0]) if isinstance(e, (list, tuple)) else float(e))
        if not vals:
            continue
        ob = OBS['decay'][b]
        print('   %-7s 模型 %6.1f (种子数 %d)  观测 %6.1f  -> %.2fx'
              % (b, float(np.median(vals)), len(vals), ob, float(np.median(vals)) / ob))

    if a.json:
        io.open(a.json, 'w', encoding='utf-8').write(
            json.dumps({'rows': rows, 'failed': failed, 'days': a.days},
                       ensure_ascii=False, indent=2))
        print('\n-> %s' % a.json)


if __name__ == '__main__':
    main()
