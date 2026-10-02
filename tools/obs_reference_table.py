# -*- coding: utf-8 -*-
"""**观测基准表**: 把任务 D / E / A 需要的观测数字**在一处算清**, 并标注口径。

为什么要它: 本会话 17 次口径问题的根源都是"同一个名字底下有几个不同的数"
(自评含扰动、xrq/wp 只收命名; 全寿命 vs 到最后一个>=20kt; 量化到 5kt; 单年 vs 多年)。
把它们**用一个工具、一次算出来、并把口径写在旁边**, 就不会再拿错。

输出来源:
  自评 2025 / 自评 2026 —— 含独立扰动, 是**任务 D(阶段码)的唯一同口径来源**;
  xrq 2025             —— 只收命名台风, 与模型"发展后"的子集可比;
  xrq/wp(全部年份)    —— 样本最大, 用于**分布形状**(P90/P95)与最高纬度。

用法:
    python tools/obs_reference_table.py
    python tools/obs_reference_table.py --md output/sim/_reference/OBS_REFERENCE.md
"""
from __future__ import annotations

import argparse
import collections
import glob
import io
import os
import sys

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

OBS = {
    '自评2025': r'G:\气象\数据\自评\2025',
    '自评2026': r'G:\气象\数据\自评\2026',
    'xrq2025': r'G:\气象\数据\xrq\2025',
    'xrq/wp': r'G:\气象\数据\xrq\wp',
}
CODES = ('DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD')


def basin_of(lat, lon):
    """与 `tools/verify_year.py` **同一个海盆定义**(否则两侧口径不一致)。"""
    lo = lon % 360.0
    if lat < 0:
        return 'SH'
    if 100 <= lo < 180:
        return 'WP'
    if 180 <= lo < 260:
        return 'EP'
    if 260 <= lo < 360:
        return 'AL'
    return 'IO'


def read_tracks(root):
    """-> [ [ (lat, lon, vmax, phase), ... ], ... ](ATCF 纬度/经度是十分之几度)"""
    out = []
    for p in sorted(glob.glob(os.path.join(root, '**', '*.dat'), recursive=True)):
        rows = []
        try:
            with io.open(p, encoding='ascii', errors='replace') as f:
                for ln in f:
                    c = [x.strip() for x in ln.split(',')]
                    if len(c) < 11:
                        continue
                    try:
                        la = float(c[6][:-1]) / 10.0
                        if c[6][-1] in 'Ss':
                            la = -la
                        lo = float(c[7][:-1]) / 10.0
                        if c[7][-1] in 'Ww':
                            lo = -lo
                        v = float(c[8])
                    except Exception:
                        continue
                    rows.append((la, lo % 360.0, v, c[10].upper()))
        except OSError:
            continue
        if len(rows) >= 2:
            out.append(rows)
    return out


def stats(tracks, nh_only=True):
    rec_cnt = collections.Counter()
    n = 0
    life, t20, t34, peak, mx, gen, bas = [], [], [], [], [], [], []
    dev = 0
    for r in tracks:
        la = np.array([x[0] for x in r], float)
        v = np.array([x[2] for x in r], float)
        if nh_only and la.mean() < 0:
            continue
        n += 1
        rec_cnt.update(x[3] for x in r)
        life.append(6.0 * (len(r) - 1))
        i20 = np.where(v >= 20.0)[0]
        i34 = np.where(v >= 34.0)[0]
        t20.append(6.0 * int(i20[-1]) if i20.size else 0.0)
        t34.append(6.0 * int(i34[-1]) if i34.size else 0.0)
        pk = float(v.max())
        peak.append(pk)
        if pk >= 34.0:
            dev += 1
        mx.append(float(la.max()))
        gen.append(float(la[0]))
        bas.append(basin_of(la[0], r[0][1]))
    tot = sum(rec_cnt.values()) or 1
    return {
        'n': n, 'codes': {k: 100.0 * rec_cnt.get(k, 0) / tot for k in CODES},
        'life': np.array(life), 't20': np.array(t20), 't34': np.array(t34),
        'peak': np.array(peak), 'maxlat': np.array(mx), 'genlat': np.array(gen),
        'dev': dev, 'basin': np.array(bas, dtype=object),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--md', default='')
    ap.add_argument('--nh-only', action='store_true', default=True)
    a = ap.parse_args()
    lines = []

    def P(s=''):
        print(s)
        lines.append(s)

    P('# 观测基准表(由 `tools/obs_reference_table.py` 生成)')
    P()
    P('**口径声明**(这些差别是本会话 17 次口径问题的来源, 不要混用):')
    P('- `自评 2025/2026` **含独立扰动** => 任务 D(阶段码)只能用它作同口径来源;')
    P('- `xrq 2025` / `xrq/wp` **只收命名台风**(WV/LO 恒为 0)=> 与自评混比会得到假象;')
    P('- 强度记录**量化到 5kt** => 逐档"降中位"不可比(见 INTENSITY_CALIBER_NOTES.md);')
    P('- 观测在 **20-25kt 就停止记录**(终态强度中位 20-25、<10kt 占 0%)=>')
    P('  "寿命"一律同时给**全长**与**到最后一个>=20kt** 两个口径;')
    P('- 只统计北半球(南半球分布另有口径)。')
    P()
    P('## 1. 阶段码分布(% 记录; 任务 D 的验收基准)')
    P()
    P('| 来源 | 系统 | ' + ' | '.join(CODES) + ' |')
    P('|---|---|' + '---|' * len(CODES))
    res = {}
    for nm, root in OBS.items():
        tr = read_tracks(root)
        res[nm] = stats(tr, a.nh_only)
        s = res[nm]
        P('| %s | %d | ' % (nm, s['n'])
          + ' | '.join('%.1f' % s['codes'][k] for k in CODES) + ' |')
    P()
    P('## 2. 寿命 / 强度 / 纬度(任务 A / C / E 的基准)')
    P()
    P('| 来源 | 寿命中位(全长) | 到>=20kt 中位 | 到>=34kt 中位 | 峰值 P50/P90/P95 |'
      ' 最高纬中位 | 到过>=28 | 生成纬中位 | 发展率 |')
    P('|---|---|---|---|---|---|---|---|---|')
    for nm in OBS:
        s = res[nm]
        P('| %s | %.0fh | %.0fh | %.0fh | %.0f / %.0f / %.0f | %.1f | %.0f%% | %.1f | %.0f%% |'
          % (nm, np.median(s['life']), np.median(s['t20']), np.median(s['t34']),
             np.percentile(s['peak'], 50), np.percentile(s['peak'], 90),
             np.percentile(s['peak'], 95), np.median(s['maxlat']),
             100.0 * (s['maxlat'] >= 28).mean(), np.median(s['genlat']),
             100.0 * s['dev'] / max(1, s['n'])))
    P()

    def sub(nm, lo):
        s = res[nm]
        m = s['peak'] >= lo
        if m.sum() < 3:
            return None
        return (int(m.sum()), 100.0 * (s['maxlat'][m] >= 28).mean(),
                float(np.median(s['maxlat'][m])), float(np.median(s['life'][m])),
                float(np.percentile(s['peak'][m], 90)))

    P('## 3. **匹配子总体**(峰值>=34kt; 轮次 104 的口径修正: 最高纬度必须按这个比)')
    P()
    P('| 来源 | n | 到过>=28 | 最高纬中位 | 寿命中位 | 峰值 P90 |')
    P('|---|---|---|---|---|---|')
    for nm in OBS:
        r = sub(nm, 34.0)
        if r:
            P('| %s | %d | %.1f%% | %.1f | %.0fh | %.0f |' % ((nm,) + r))
    P()
    P('## 3b. **任务 C 的验收基准**(发展后子总体 peak>=34kt 里的档位占比)')
    P()
    P('为什么要单列: 目标里的 "100-129 **17%** / 130+ **18%**" 必须写清是**哪个口径** ——')
    P('单年集合(自评/xrq2025)与多年合并的 xrq/wp 给出的数并不一样, 混用会误判缺口大小。')
    P()
    P('| 来源 | 发展数 n | 100-129 | 130+ | 峰值 P90 | 峰值 P95 |')
    P('|---|---|---|---|---|---|')
    for nm in OBS:
        s2 = res[nm]
        pk = s2['peak'][s2['peak'] >= 34.0]
        if pk.size < 5:
            continue
        b1 = 100.0 * ((pk >= 100) & (pk < 130)).mean()
        b2 = 100.0 * (pk >= 130).mean()
        P('| %s | %d | %.1f%% | %.1f%% | %.0f | %.0f |'
          % (nm, pk.size, b1, b2, np.percentile(pk, 90), np.percentile(pk, 95)))
    P()
    P('## 3c. **分海盆**的档位占比(第 18 次口径问题的修正)')
    P()
    P('发现: 目标里的 "17% / 18%" 来自 **xrq/wp(只含西北太、跨几十年、2340 个)**;')
    P('而 自评/xrq2025 这类**单年、多海盆**集合给出的是 5-11% / 12-14%。')
    P('模型跑的是**全球单年** => 直接对 WP-only 多年池比会**系统性高估缺口**')
    P('(各海盆的强度分布本来就不同)。所以这里把海盆拆开报。')
    P()
    P('| 来源 | 海盆 | 发展数 n | 100-129 | 130+ | 峰值 P90 |')
    P('|---|---|---|---|---|---|')
    for nm in OBS:
        s3 = res[nm]
        for b in ('WP', 'EP', 'AL', 'IO', 'SH'):
            m = (s3['basin'] == b) & (s3['peak'] >= 34.0)
            if int(m.sum()) < 8:
                continue
            pk = s3['peak'][m]
            P('| %s | %s | %d | %.1f%% | %.1f%% | %.0f |'
              % (nm, b, pk.size, 100.0 * ((pk >= 100) & (pk < 130)).mean(),
                 100.0 * (pk >= 130).mean(), np.percentile(pk, 90)))
    P()
    P('## 4. 任务 E 的目标(生成量)')
    P()
    P('用户口径: **扰动 250/年**(JTWC 常漏扰动 => 200 的台账是被低估的),'
      ' **发展 ~120/年** => 发展率约 **48%**。')
    P('对照: 自评 2025 的发展率是 %.0f%%、自评 2026 是 %.0f%%、xrq2025 是 %.0f%%'
      % (100.0 * res['自评2025']['dev'] / max(1, res['自评2025']['n']),
         100.0 * res['自评2026']['dev'] / max(1, res['自评2026']['n']),
         100.0 * res['xrq2025']['dev'] / max(1, res['xrq2025']['n'])))
    P('(注意: 这些是**被记录到**的系统, 不是真实扰动总数 —— 用户认为后者被低估。)')
    if a.md:
        io.open(a.md, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        print('-> %s' % a.md)


if __name__ == '__main__':
    main()
