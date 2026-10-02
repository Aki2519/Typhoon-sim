# -*- coding: utf-8 -*-
"""模型里的台风**为什么死**、活多久 —— 任务 A 的第二杠杆(寿命)。

轮次 102 的证据: 观测(自评 2025/2026、xrq 2025)的**寿命中位是 180-222h**,
而模型只有 **108-120h**(20 天冒烟 108h、归档产物 120h)。
因为"一生最高纬度 = 生成纬度 + 向极速度 x 寿命", 寿命短 1.7 倍就直接砍掉近一半
的向极位移 —— 这与用户早先抱怨的"扰动/TD 太短"是同一件事。

本脚本把死亡判据(hard-code 在 `intensity_step` 末尾)逐条归因:
  · vmax < 8                       -> 'fade'      (强度衰减到消散)
  · LO 且 loHour > 72h (或 <10kt)  -> 'lo_cap'    (**残余低压 72 小时上限**)
  · vmax < 20 且 (sst<20 或 land>.9)-> 'cold_land'
  · EX 且 vmax < 20                -> 'ex_fade'
并对"从未达到 34kt"的扰动与"曾命名"的系统分开统计(两者寿命机制不同)。

用法:
    python tools/probe_death_cause.py --seed 20250801 --days 120
    python tools/probe_death_cause.py --seed 11 --days 120 --start 2025-07-01
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=120.0)
    ap.add_argument('--start', default='2025-01-01 00')
    ap.add_argument('--param', default='',
                    help='覆盖 sim["params"], 形如 loCapH=24,fadeKt=12,weakLifeH=48')
    a = ap.parse_args()

    from simulator.simcore import core as V
    _pr = {'precision': '100ms', 'dataMode': True}
    for kv in [x for x in a.param.split(',') if x.strip()]:
        k, _, v = kv.partition('=')
        try:
            _pr[k.strip()] = float(v)
        except ValueError:
            _pr[k.strip()] = v.strip()
    print('== probe_death_cause seed=%s start=%s days=%.0f param=%s'
          % (a.seed, a.start, a.days, a.param or '(默认)'), flush=True)
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': _pr})
    for _ in range(int(a.days * 24 / 6)):
        V.sim_step(sim, 6.0)

    rows = []
    for t in sim['tcs']:
        tr = t.get('track') or []
        if len(tr) < 2:
            continue
        pk = max(float(q['vmax']) for q in tr)
        last = tr[-1]
        lo = float(last.get('lon', t['lon'])) % 360.0
        hemi = 1.0 if float(last['lat']) >= 0 else -1.0
        # 死亡判据复算(用**最后一报**的环境)
        env = t.get('env') or {}
        v0 = float(last['vmax'])
        phase = str(last.get('phase') or '')
        lo_h = (float(t.get('deadHour', sim['t'])) - float(t.get('loHour', 0.0))
                if t.get('loHour') else 0.0)
        if not t['dead']:
            cause = 'alive'
        elif v0 < 8.0:
            cause = 'fade(<8kt)'
        elif phase == 'LO':
            cause = 'lo_cap(72h)' if lo_h > 72.0 else 'lo_fade'
        elif v0 < 20.0 and (float(env.get('sst', 30)) < 20.0
                            or float(env.get('land', 0.0)) > 0.9):
            cause = 'cold_land'
        elif phase == 'EX':
            cause = 'ex_fade'
        else:
            cause = 'other(' + phase + ')'
        if pk < 34.0:
            lo_b, hi_b = 0.0, 34.0
        elif pk < 64.0:
            lo_b, hi_b = 34.0, 64.0
        elif pk < 100.0:
            lo_b, hi_b = 64.0, 100.0
        else:
            lo_b, hi_b = 100.0, 1e9
        rows.append({'pk': pk, 'life': 6.0 * (len(tr) - 1), 'cause': cause,
                     'v0': v0, 'lat': float(last['lat']) * hemi,
                     'lon': lo, 'named': bool(t.get('wasNamed')),
                     'hband': 6.0 * sum(1 for q in tr
                                        if lo_b <= float(q['vmax']) < hi_b),
                     'h34': 6.0 * sum(1 for q in tr if float(q['vmax']) >= 34.0),
                     'h64': 6.0 * sum(1 for q in tr if float(q['vmax']) >= 64.0),
                     # **可比口径**: 观测(自评/官方 b-deck)在 20-25kt 就停止记录
                     # (实测终态强度中位 20-25kt、<10kt 占 0%), 而模型会一路衰减到
                     # <8kt 才消失 ⇒ 全长不可比。这两个量才是同一口径。
                     't20': 6.0 * max([i for i, q in enumerate(tr)
                                       if float(q['vmax']) >= 20.0] or [0]),
                     't34': 6.0 * max([i for i, q in enumerate(tr)
                                       if float(q['vmax']) >= 34.0] or [0]),
                     # 寿命四段分解(与观测同口径; 观测: 生成->34kt 51-60h |
                     # 34kt->峰值 36-66h | 峰值->最后34kt 36-60h | 最后34kt->结束 24h)
                     'pre': 6.0 * int(np.argmax([float(q['vmax']) >= 34.0
                                                 for q in tr]))
                     if any(float(q['vmax']) >= 34.0 for q in tr) else float('nan'),
                     'fall': 6.0 * (max([i for i, q in enumerate(tr)
                                         if float(q['vmax']) >= 34.0] or [0])
                                    - int(np.argmax([float(q['vmax']) for q in tr]))),
                     'rise': 6.0 * (int(np.argmax([float(q['vmax']) for q in tr]))
                                    - int(np.argmax([float(q['vmax']) >= 34.0
                                                     for q in tr])))
                     if any(float(q['vmax']) >= 34.0 for q in tr) else float('nan')})
    if not rows:
        print('无样本')
        return
    print('=== seed=%s %s %d 天: %d 个系统 ===' % (a.seed, a.start, a.days, len(rows)))
    print('**按峰值分档**(观测同口径: <34kt 全长 72-87h | 34-63kt 132-177h | >=64kt 240-279h)')
    print('%-14s %5s | %-20s | %-24s | %s'
          % ('峰值档', 'n', '全长 中位/P25/P75', '**到最后一个>=20kt**', '到最后一个>=34kt'))
    for lo, hi, tag in ((0, 34, '<34kt(扰动)'), (34, 64, '34-63kt'),
                        (64, 100, '64-99kt'), (100, 999, '>=100kt')):
        sub = [r for r in rows if lo <= r['pk'] < hi]
        if not sub:
            continue
        lf = np.array([r['life'] for r in sub], float)
        t20 = np.array([r['t20'] for r in sub], float)
        t34 = np.array([r['t34'] for r in sub], float)
        print('%-14s %5d | %5.0f / %5.0f / %5.0f | %5.0f / %5.0f / %5.0f       | %5.0f'
              % (tag, len(sub), np.median(lf), np.percentile(lf, 25), np.percentile(lf, 75),
                 np.median(t20), np.percentile(t20, 25), np.percentile(t20, 75),
                 np.median(t34)))
    print('  (观测: 全长中位 180-207h; 到最后一个>=20kt 180-219h; 到最后一个>=34kt 144-195h;')
    print('   终态强度中位 **20-25kt**、<10kt 占 **0%** —— 观测在 20-25kt 就停止记录)')
    print()
    print('**寿命四段分解**(只统计达到过 34kt 的系统; 观测中位见下表)')
    print('%-14s %5s | %-10s | %-12s | %-14s | %s'
          % ('峰值档', 'n', '生成->34kt', '34kt->峰值', '峰值->最后34kt', '最后34kt->结束'))
    for lo, hi, tag in ((34, 64, '34-63kt'), (64, 100, '64-99kt'), (100, 999, '>=100kt')):
        sub = [r for r in rows if lo <= r['pk'] < hi and r['pre'] == r['pre']]
        if len(sub) < 3:
            continue
        pre = np.array([r['pre'] for r in sub], float)
        rise = np.array([r['rise'] for r in sub], float)
        fall = np.array([r['fall'] for r in sub], float)
        post = np.array([max(0.0, r['life'] - r['t34']) for r in sub], float)
        print('%-14s %5d | %5.0f h     | %5.0f h      | %5.0f h        | %5.0f h'
              % (tag, len(sub), np.median(pre), np.median(rise),
                 np.median(fall), np.median(post)))
    print('  (观测: 生成->34kt **51-60h** | 34kt->峰值 **36-66h** | 峰值->最后34kt **36-60h** | 尾 **24h**)')
    print()
    for tag, sel in (('全部', lambda r: True),
                     ('未命名(扰动)', lambda r: not r['named']),
                     ('曾命名', lambda r: r['named']),
                     ('曾达 34kt+', lambda r: r['pk'] >= 34.0)):
        sub = [r for r in rows if sel(r)]
        if not sub:
            continue
        lf = np.array([r['life'] for r in sub], float)
        cn = collections.Counter(r['cause'] for r in sub)
        print('--- %-12s n=%4d  寿命: 中位 %5.0fh  P25 %5.0f  P75 %5.0f  最长 %5.0f'
              % (tag, len(sub), np.median(lf), np.percentile(lf, 25),
                 np.percentile(lf, 75), lf.max()))
        print('    死因: ' + '  '.join('%s %d(%.0f%%)' % (k, c, 100 * c / len(sub))
                                       for k, c in cn.most_common(6)))
        al = np.array([r['lat'] for r in sub], float)
        print('    终态纬度中位 %5.1f | 终态强度中位 %5.1f kt' % (np.median(al), np.median([r['v0'] for r in sub])))
    print()
    print('判读: 若未命名扰动的寿命被 "lo_cap(72h)" 与 "fade(<8kt)" 主导, 而它们又占多数,')
    print('      那么"寿命短"主要是**残余低压 72 小时上限 + 弱系统衰减过快**造成的,')
    print('      而不是路径/环境问题 —— 观测量里扰动的轨迹长度(中位 180h)明显更长。')


if __name__ == '__main__':
    main()
