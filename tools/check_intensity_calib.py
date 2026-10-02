# -*- coding: utf-8 -*-
"""**强度-寿命标定的唯一验收脚本**(2026-09-21 立)。

为什么要有它: 我(主代理)在半夜里因为**口径混用**连续误判过三次 ——
  ① 拿混季样本均值比单月分布;② 拿全部系统(含上限 30kt 的纯扰动)的中位比观测;
  ③ **把减弱速率的统计窗口写成"整条轨迹所有下降步"**, 而观测口径是"**峰值之后**"。
结果两次得出"崩了"的假象, 一次让一个正确的门控背了锅。
所以这里把口径**写死**, 任何物理改动都必须用它前后对比:

  口径 A: 只统计**达到过 >=34kt** 的系统(可比子集)。观测归档里 98% 是 >=34kt 的
          系统, 而模型 80% 是扰动 —— 混在一起比会得出完全相反的结论。
  口径 B: 减弱速率**只从该风暴的峰值时刻往后算**。
  口径 C: 每张表都打印 **n**。样本 <8 的档位标 `(n小)`。

观测基准(2025 WP 季, 见 output/sim/_reference/INTENSITY_LIFETIME.md):
  命名 27 个 | 峰值中位 70 kt | ACE/风暴 均值 7.37 / 中位 5.43
  前生期(首报→34kt) 中位 54 h | TD 报数中位 6.5
  峰值后减弱速率中位 kt/6h: >=100: -12.5 | 80-100: -10 | 64-80: -10 | 48-64: -5 | 34-48: -5

用法:
  python tools/check_intensity_calib.py --days 180 --seed 20250801     # 跑模拟再统计
  python tools/check_intensity_calib.py --model-dir sim_typhoon/2025/2025wp
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import time

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: 观测基准(2025 WP 季; 口径与上面一致)
OBS = {'n_named': 27, 'peak_med': 70.0, 'ace_mean': 7.37, 'ace_med': 5.43,
       'pre_h_med': 54.0, 'td_reports_med': 6.5,
       'decay': {'100+': -12.5, '80-100': -10.0, '64-80': -10.0,
                 '48-64': -5.0, '34-48': -5.0}}
_BINS = ((100.0, 1e9, '100+'), (80.0, 100.0, '80-100'), (64.0, 80.0, '64-80'),
         (48.0, 64.0, '48-64'), (34.0, 48.0, '34-48'), (0.0, 34.0, '<34'))
VALID = ('DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD')


def _latlon(s, kind):
    m = re.match(r'^(\d+)([NSEW])$', (s or '').strip())
    if not m:
        return None
    v = int(m.group(1)) / 10.0
    return -v if m.group(2) in ('S', 'W') else v


def read_bdeck(path):
    """→ (逐时报的 vmax 数组, 阶段码列表)。11/12/13 列变体都兼容; 同刻只留最后一条。"""
    rows = {}
    for ln in open(path, encoding='utf-8', errors='replace'):
        c = [x.strip() for x in ln.split(',')]
        if len(c) < 11:
            continue
        t = c[2]
        if len(t) != 10 or not t.isdigit():
            continue
        try:
            v = float(c[8])
        except ValueError:
            continue
        ph = c[10].upper() if c[10].upper() in VALID else None
        rows[t] = (v, ph)
    ks = sorted(rows)
    if len(ks) < 4:
        return None, None
    return np.array([rows[k][0] for k in ks], float), [rows[k][1] for k in ks]


def stats_from_tracks(tracks):
    """tracks: [(vmax 数组, 阶段码列表), ...] —— 按口径 A/B/C 统计。"""
    keep = [(v, p) for v, p in tracks if v is not None and v.max() >= 34.0]
    peaks, aces, pres, td, decay = [], [], [], [], collections.defaultdict(list)
    for v, ph in keep:
        peaks.append(float(v.max()))
        aces.append(float(np.sum(v[v >= 34.0] ** 2) / 1e4))
        k34 = np.argmax(v >= 34.0)
        pres.append(float(k34) * 6.0)
        seq = [x for x in (ph or []) if x]
        run = 0
        for x in seq + ['END']:
            if x == 'TD':
                run += 1
            else:
                if run:
                    td.append(run)
                run = 0
        ip = int(np.argmax(v))
        # **口径 B: 只从峰值之后算**
        for a, b in zip(v[ip:], v[ip + 1:]):
            if b < a:
                for lo, hi, nm in _BINS:
                    if lo <= a < hi:
                        decay[nm].append((b - a) / 6.0)
                        break
    out = {'n': len(keep),
           'peak_med': float(np.median(peaks)) if peaks else None,
           'ace_mean': float(np.mean(aces)) if aces else None,
           'ace_med': float(np.median(aces)) if aces else None,
           'pre_h_med': float(np.median(pres)) if pres else None,
           'td_reports_med': float(np.median(td)) if td else None,
           'decay': {k: [float(np.median(v)), len(v)] for k, v in decay.items() if v}}
    return out


def run_sim(days, seed, start='2025-01-01 00', quiet=True):
    from simulator.simcore import core as V
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    step = V.step_hours(sim)
    n = int(days * 24 / step)
    t0 = time.time()
    for i in range(n):
        V.sim_step(sim, step)
        if not quiet and (i + 1) % 40 == 0:
            print('  day %.0f  %.0fs' % ((i + 1) * step / 24, time.time() - t0), flush=True)
    return [(np.array([float(q['vmax']) for q in (t.get('track') or [])]),
             [q.get('phase') for q in (t.get('track') or [])]) for t in sim['tcs']]


def report(cur, label='模型', out_json=None):
    print('=== %s (口径: 仅 >=34kt 可比子集; 减弱速率只取峰值之后) ===' % label)
    def row(nm, key, obs_key=None, fmt='%.2f'):
        v = cur.get(key)
        o = OBS.get(obs_key or key)
        s = ('%-16s n=%-3d ' % (nm, cur['n'])) if key == 'peak_med' else '%-16s ' % nm
        if v is None:
            print(s + '无样本'); return
        if o:
            print(s + (fmt + '  (观测 ' + fmt + ' → %.2fx)') % (v, o, v / o))
        else:
            print(s + (fmt % v))
    row('峰值中位 kt', 'peak_med', 'peak_med', '%.0f')
    row('ACE/风暴 均值', 'ace_mean', 'ace_mean')
    row('ACE/风暴 中位', 'ace_med', 'ace_med')
    row('前生期中位 h', 'pre_h_med', 'pre_h_med', '%.0f')
    row('TD 报数中位', 'td_reports_med', 'td_reports_med', '%.1f')
    print('%-16s' % '峰值后减弱 kt/6h:')
    for nm in ('100+', '80-100', '64-80', '48-64', '34-48'):
        d = cur['decay'].get(nm)
        o = OBS['decay'].get(nm)
        if not d:
            print('   %-7s 无样本' % nm); continue
        med, n = d
        flag = ' (n小)' if n < 8 else ''
        print('   %-7s %6.1f (n=%d)%s  (观测 %5.1f → %.2fx)'
              % (nm, med, n, flag, o, med / o if o else float('nan')))
    if out_json:
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump({'label': label, 'stats': cur, 'obs': OBS}, f,
                      ensure_ascii=False, indent=1)
        print('-> ' + out_json)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=180.0)
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--start', default='2025-01-01 00')
    ap.add_argument('--model-dir', default=None, help='直接读 b-deck 目录(不跑模拟)')
    ap.add_argument('--json', default=os.path.join(ROOT, 'output', 'sim', '_reference',
                                                   'intensity_calib.json'))
    a = ap.parse_args(argv)
    if a.model_dir:
        tracks = []
        for f in sorted(glob.glob(os.path.join(a.model_dir, '**', '*.dat'), recursive=True)):
            tracks.append(read_bdeck(f))
        report(stats_from_tracks(tracks), '模型(%s)' % a.model_dir, a.json)
    else:
        t0 = time.time()
        tracks = run_sim(a.days, a.seed, a.start, quiet=False)
        print('跑 %.0f 天用时 %.0fs' % (a.days, time.time() - t0))
        report(stats_from_tracks(tracks), '模型(%d 天)' % a.days, a.json)
    return 0


if __name__ == '__main__':
    main()
