# -*- coding: utf-8 -*-
"""**变性(EX)闸门诊断**: 风暴已经能走到 30N 以上了, 为什么还是一个 EX 都没有。

轮次 104 实测(`check_phase_vs_obs.py --days 150`): 196 个系统里
    `最大|纬| 中位 29.7 / p90 41.4 / 最大 45.1`, 但 **EX = 0**、SS/SD/MD 全 0
⇒ 问题**不在纬度**, 在变性判据本身。`core.intensity_step` 的触发条件:
    |lat| >= 30  且  sst < 27.5  且  shear >= 22  且  vmax >= 35, **持续 ET_HOURS=24h**
累积满足后转 SD; SD 再降到 <32kt 或 SST<20 才成为 EX/SS。

本脚本把"够北"的每一步都记下来(纬/海温/切变/强度), 然后**事后**做全部统计 ——
这样一次运行就能扫多个阈值组合, 不必为每个候选阈值重跑。

用法:
    python tools/probe_et_gate.py --seed 20250801 --days 150 --start "2025-06-01 00"
"""
from __future__ import annotations

import argparse
import os
import sys

import collections

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def streak_max(mask):
    """按时间顺序的最长连续 True 段(步数)。"""
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=150.0)
    ap.add_argument('--start', default='2025-06-01 00')
    ap.add_argument('--lat-min', type=float, default=28.0)
    a = ap.parse_args()

    from simulator.simcore import core as V
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    trace = {}
    cuclass = collections.Counter()
    phase_cnt = collections.Counter()
    etmax = 0.0
    etn = 0
    tstep = 0
    for _ in range(int(a.days * 24 / 6)):
        V.sim_step(sim, 6.0)
        tstep += 1
        for t in sim['tcs']:
            # **直接读内核自己累积的 etHours** —— 这是最直接的闸门读数,
            # 不必靠外部的"连续段"重建(第一版按 |lat|>=28 过滤后再数连续段,
            # 被过滤掉的步会让两个不相邻的时刻看起来连续, 把段长虚报成 18h)。
            cc = str(t.get('cuClass') or '')
            if cc:
                cuclass[cc] += 1
            seg = str(t.get('phase') or '')
            if seg:
                phase_cnt[seg] += 1
            eh = float(t.get('etHours', 0.0) or 0.0)
            if eh > 0:
                etn += 1
            etmax = max(etmax, eh)
            if t['dead']:
                continue
            la = abs(float(t['lat']))
            if la < a.lat_min:
                continue
            env = t.get('env') or {}
            trace.setdefault(id(t), []).append(
                (tstep, la, float(env.get('sst', 30.0)), float(env.get('shear', 0.0)),
                 float(t['vmax'])))
    rows = [r for v in trace.values() for r in v]
    if not rows:
        print('够北的样本为 0')
        return
    A = np.array(rows, float)
    la, sst, sh, vm = A[:, 1], A[:, 2], A[:, 3], A[:, 4]
    print('=== %s 起 %d 天 seed=%s |lat|>=%.0f 的 TC-步 n=%d, 风暴 %d ==='
          % (a.start, a.days, a.seed, a.lat_min, len(A), len(trace)))
    print('**内核自己累积的 etHours**: 非零的步数 %d | 全场最大 %.0fh(门槛 ET_HOURS=%.0fh)'
          % (etn, etmax, V.ET_HOURS))
    print('**结构相态 cuClass 计数**(ET 物理分支只改这个): '
          + '  '.join('%s %d' % kv for kv in cuclass.most_common()))
    print('**权威阶段码 phase 计数**(phase.classify 决定): '
          + '  '.join('%s %d' % kv for kv in phase_cnt.most_common()))
    for nm, x in (('纬度', la), ('SST', sst), ('切变', sh), ('vmax', vm)):
        print('  %-4s p10 %6.1f  p25 %6.1f  p50 %6.1f  p75 %6.1f  p90 %6.1f'
              % (nm, np.percentile(x, 10), np.percentile(x, 25), np.percentile(x, 50),
                 np.percentile(x, 75), np.percentile(x, 90)))
    print()
    print('切变阈值达成率: ' + '  '.join('>=%d: %.0f%%' % (s, 100 * (sh >= s).mean())
                                        for s in (12, 15, 18, 20, 22, 25)))
    print()
    print('**四条件同时满足的步占比 / 单风暴最长连续段**(阈值组合扫描)')
    print('%-28s %10s %10s %12s' % ('(lat>=-30, sst<27.5, shear>=S, vmax>=35)', '步占比',
                                    '有段风暴', '最长段(h)'))
    for S in (12, 15, 18, 20, 22):
        for VMIN in (35.0,):
            mask_all = []
            nstep = 0
            nseg = 0
            longest = 0.0
            for k, v in trace.items():
                arr = np.array(v, float)
                m = ((arr[:, 1] >= 30.0) & (arr[:, 2] < 27.5) & (arr[:, 3] >= S)
                     & (arr[:, 4] >= VMIN))
                # **必须要求时间步相邻**(第一版漏了这条, 把被过滤掉中间步的两段接起来)
                gaps = np.diff(arr[:, 0]) != 1.0
                if gaps.any():
                    idx = np.where(gaps)[0] + 1
                    m = np.split(m, idx)
                    sl = max([streak_max(x) for x in m] or [0])
                else:
                    sl = streak_max(m)
                nstep += int(sum(x.sum() for x in (m if isinstance(m, list) else [m])))
                if sl > 0:
                    nseg += 1
                longest = max(longest, sl * 6.0)
                mask_all.append(m)
            print('%-28s %9.0f%% %10d %12.0f'
                  % ('lat>=30,sst<27.5,shear>=%d,vmax>=%.0f' % (S, VMIN),
                     100 * nstep / len(A), nseg, longest))
    print()
    print('  当前判据用的是 shear>=22 且 **持续 ET_HOURS=%.0fh**。' % V.ET_HOURS)
    print('  判读: 若把 S 降到 18-20 后"最长段"才够 24h, 说明卡在切变阈值;')
    print('        若最长段始终 <24h, 说明卡在**持续时间**, 该降 ET_HOURS;')
    print('        若 S 降到 15 仍不够, 说明模型副热带切变整体偏小, 要查切变场本身。')


if __name__ == '__main__':
    main()
