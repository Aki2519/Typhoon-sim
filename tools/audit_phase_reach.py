# -*- coding: utf-8 -*-
"""**阶段码可达性审计**: 哪些 ATCF 相态在现有判据下根本**不可能**被判出来。

轮次 104 修 EX 时发现了两层"自锁":
  ① `ET_HOURS=24h` 在模型里不可能达到(实测四条件同时满足的最长连续段只有 18h);
  ② `warmth = clamp((dt_sst200 - 28)/32)` 用的 `dt_sst200 = sst - t200` 是**绝对值**,
     中纬度对流层顶 -55度C 让 dT 高达 70-80K => warmth **恒为 1.0** =>
     `warm_core` 永真 => `ex_ok` 里的 `not warm_core` 永假。
那 SS / SD / MD 是不是也被同一把锁锁住? 本工具**直接扫参数空间**给出答案 ——
`phase.classify` 是纯函数, 不需要跑模拟。

做法: 在物理上合理的 (纬度, 海温, 切变, 强度, 暖心温差, front) 网格上逐个调用
`classify`, 统计每个相态**出现在哪里、需要什么条件**, 并对每个相态报
"可达 / 只在 front=True 时可达 / 完全不可达"。

用法:
    python tools/audit_phase_reach.py
    python tools/audit_phase_reach.py --t200-lat 45   # 改对流层顶温度的纬度剖面
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def t200_at(lat, warm_trop=-52.0, cold_pole=-62.0):
    """200hPa 温度的粗略纬向剖面(度C)。模式里实际用的是环境场, 这里只要量级对。"""
    return warm_trop + (cold_pole - warm_trop) * min(1.0, abs(lat) / 55.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--self-consistent-dt', action='store_true', default=True,
                    help='dt_sst200 由 sst - t200(lat) 现算(与模式的传参方式一致)')
    a = ap.parse_args()

    from simulator.simcore import phase as PH

    lats = [2, 5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 33, 35, 38, 40, 45]
    ssts = [20.0, 22.0, 23.5, 25.0, 26.0, 27.0, 28.5, 30.0]
    shears = [2.0, 6.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0]
    vmaxs = [12, 18, 22, 28, 33, 35, 40, 50, 64, 80, 100, 130]
    fronts = (False, True)
    ages = (24.0,)

    #: `named/numbered` 必须分两档扫: 已命名/已编号的系统在分类器里会被强制
    #: 从 (DB/WV) 落到 LO, 所以"DB/WV 不可达"只在**未命名**那一档才说明问题。
    tiers = ((True, True, '已命名/已编号'), (False, False, '未命名/未编号'))
    #: `previous` 必须一起扫: 分类器里 DB/WV 的分支依赖 `_family(previous)`
    #: ("已经热带气旋化过的系统 → DB"), 固定 previous 会把 WV 判成"不可达"的假象。
    prevs = ('TY', 'TS', 'DB', 'WV', 'LO', 'EX', 'SD')
    for num, nam, tier in tiers:
        cnt = collections.Counter()
        by_front = collections.defaultdict(collections.Counter)
        by_prev = collections.defaultdict(set)
        first = {}
        n = 0
        for lat in lats:
            for sst in ssts:
                for sh in shears:
                    for v in vmaxs:
                        for fr in fronts:
                            for age in ages:
                                for pv in prevs:
                                    dt = sst - t200_at(lat)
                                    try:
                                        r = PH.classify(vmax=float(v), sst=sst,
                                                        lat=float(lat), shear=sh,
                                                        land=0.0, age=age,
                                                        dt_sst200=dt, front=fr,
                                                        numbered=num, named=nam,
                                                        weak=0.0, lon=150.0,
                                                        previous=pv)
                                    except Exception:
                                        continue
                                    n += 1
                                    ph = r['phase']
                                    cnt[ph] += 1
                                    by_prev[ph].add(pv)
                                    by_front[ph][fr] += 1
                                    if ph not in first:
                                        first[ph] = dict(
                                            lat=lat, sst=sst, shear=sh, vmax=v,
                                            front=fr, dt=round(dt, 1),
                                            warmth=round(r['warmth'], 2), prev=pv)
        print()
        print('=== 阶段码可达性审计: %s ===' % tier)
        print('参数网格: 纬度 %d x 海温 %d x 切变 %d x 强度 %d x front %d = **%d 个组合**'
              % (len(lats), len(ssts), len(shears), len(vmaxs), len(fronts), n))
        print('%-6s %10s %9s | %s' % ('相态', '命中数', '占比', '可达性判读'))
        for ph in PH.PHASE_CODES:
            c = cnt.get(ph, 0)
            f0 = by_front[ph].get(False, 0)
            f1 = by_front[ph].get(True, 0)
            if c == 0:
                verdict = '**完全不可达**'
            elif f0 == 0:
                verdict = '**只在 front=True 时可达**'
            elif f1 == 0:
                verdict = '只在 front=False 时可达'
            else:
                verdict = '可达(两种 front 都有)'
            print('%-6s %10d %8.2f%% | %s' % (ph, c, 100.0 * c / max(1, n), verdict))
        print('  可达性(按 previous): ' + '  '.join(
            '%s<-{%s}' % (ph, ','.join(sorted(by_prev[ph])))
            for ph in PH.PHASE_CODES if ph in by_prev))
        print('  首次出现时的条件: ' + '  '.join(
            '%s(lat=%s,sst=%s,sh=%s,v=%s,front=%s,dt=%s,warmth=%.2f)'
            % (ph, first[ph]['lat'], first[ph]['sst'], first[ph]['shear'],
               first[ph]['vmax'], first[ph]['front'], first[ph]['dt'],
               first[ph]['warmth'])
            for ph in PH.PHASE_CODES if ph in first))
    print()
    print('判读要点:')
    print('  · 若 SD/SS/MD 都只出现在 `front=True` 下, 说明它们与 EX 共用同一把锁 ——')
    print('    `warmth` 用 SST-200hPa 的**绝对值**, 中纬度恒为 1.0, 于是')
    print('    `weak_warm_core`(0.35<=warmth<0.60) 与 `cold_core`(warmth<0.35) 都不可达,')
    print('    而 SD/SS 要 `not warm_core`、MD 也要 `not warm_core` => **全部被锁死**。')
    print('  · `front` 只由内核的 ET 物理分支给出(需要 |lat|>=30 且持续 ET_HOURS),')
    print('    所以 25-29 度的副热带系统**永远不可能**被判成 SS/SD。')
    print('  · 修法方向: 让暖心判据相对**同纬度背景**而不是绝对温差(或用内核已有的')
    print('    `weak`/`cuClass` 结构信息代替饱和的代理), 而不是逐个相态打补丁。')


if __name__ == '__main__':
    main()
