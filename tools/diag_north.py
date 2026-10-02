# -*- coding: utf-8 -*-
"""北向运动诊断(A 任务: 副高环流 / 风暴为什么不北上)。

第 101 轮已定量的现象: 模型风暴**一生最高纬度中位只有 18.5 度**、只有 **13%** 到过 28 度;
观测(自评)是 24-26 度 / 40%, 官方 32 度 / 56%。
=> 没有变性(EX) 与 高强度尾部偏弱**共用一个根**: 风暴不北上。

本脚本把"最高纬度"分解成三段, 定位到底是哪一段掉的:
    maxlat  =  genlat(生成纬度)  +  dlat(一生向极净位移)
    dlat    =  v_lat(向极速度)   x   lifetime(寿命)
四项逐一与**同口径观测**对比, 并给出向极速度的**分纬度带**剖面
(排除"模型只是在低纬就死了, 高纬向极速度其实正常"这种解释)。

用法:
    python tools/diag_north.py --seeds 20250801 --days 365 --tag north1
    python tools/diag_north.py --seeds 20250801,11,23 --days 365 --tag north3
    python tools/diag_north.py --obs-only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

KT_PER_DLAT = 110.57 / 6.0 / 1.852          # 0.1 度/6h  ->  kt(≈1.0)

OBS_SETS = {
    '自评2025': r'G:\气象\数据\自评\2025',
    '自评2026': r'G:\气象\数据\自评\2026',
    'xrq2025': r'G:\气象\数据\xrq\2025',
    'xrq_wp': r'G:\气象\数据\xrq\wp',
}


def parse_bdeck(path):
    """-> [(lat, lon, vmax, phase)]; ATCF 纬度/经度是**十分之几度**。"""
    out = []
    try:
        with open(path, encoding='ascii', errors='replace') as f:
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
                out.append((la, lo % 360.0, v, c[10].upper()))
    except OSError:
        pass
    return out


def collect_obs(paths):
    """-> list of tracks (每个文件一条), 只保留 >=2 个时次的。"""
    tr = []
    for p in paths:
        r = parse_bdeck(p)
        if len(r) >= 2:
            tr.append(r)
    return tr


def collect_model(seeds, days, sst_anom=0.0, verbose=True):
    from simulator.simcore import core as V
    tr = []
    for sd in seeds:
        if verbose:
            print('>>> 模拟 seed %s ...' % sd, flush=True)
        sim = V.init({'start': '2025-01-01 00', 'seed': sd, 'envStepH': 6.0,
                      'params': {'precision': '100ms', 'dataMode': True,
                                 'sstAnom': sst_anom}})
        for _ in range(int(days * 24 / 6)):
            V.sim_step(sim, 6.0)
        for t in sim['tcs']:
            tk = t.get('track') or []
            if len(tk) < 2:
                continue
            tr.append([(float(q['lat']), float(q['lon']) % 360.0,
                        float(q['vmax']), str(q.get('phase') or '')) for q in tk])
    return tr


# ── 统计 ──
def sys_stats(tracks):
    """每条路径 -> dict(genlat, maxlat, dlat, life_h, peak, endlat, endphase, phase_hist)"""
    out = []
    for r in tracks:
        la = np.array([x[0] for x in r], float)
        v = np.array([x[2] for x in r], float)
        ph = [x[3] for x in r]
        north = la.max() if la.max() >= -la.min() else -(-la.min())   # 取偏向的那半球
        hemi = 1.0 if la.mean() >= 0 else -1.0
        gen = la[0] * hemi
        mx = la.max() if hemi > 0 else -la.min()
        out.append({
            'hemi': hemi, 'genlat': gen, 'maxlat': mx, 'dlat': mx - gen,
            'absmax': float(np.abs(la).max()),
            'life_h': 6.0 * (len(r) - 1), 'peak': float(v.max()),
            'endlat': float(np.abs(la[-1])), 'endphase': ph[-1] if ph else '',
            'phases': ph,
        })
    return out


def report(tag, st, nh_only=True):
    if nh_only:
        st = [s for s in st if s['hemi'] > 0]
    if not st:
        print('--- %s: 无数据' % tag)
        return None
    g = np.array([s['genlat'] for s in st], float)
    m = np.array([s['maxlat'] for s in st], float)
    d = np.array([s['dlat'] for s in st], float)
    lf = np.array([s['life_h'] for s in st], float)
    pk = np.array([s['peak'] for s in st], float)
    n = len(st)
    print('--- %-10s n=%-4d 生成纬中位 %5.1f | **一生最高纬中位 %5.1f** | 净向极位移中位 %5.1f'
          % (tag, n, np.median(g), np.median(m), np.median(d)))
    print('    到过>=28 度 %5.1f%% | >=25 度 %5.1f%% | >=35 度 %5.1f%% | 寿命中位 %5.1fh'
          ' | 峰值中位 %5.1fkt'
          % (100 * (m >= 28).mean(), 100 * (m >= 25).mean(), 100 * (m >= 35).mean(),
             np.median(lf), np.median(pk)))
    # 只对"寿命 >= 72h"的子集再算一次位移(排除短命样本把位移稀释)
    sel = lf >= 72.0
    if sel.any():
        print('    [寿命>=72h 子集 n=%d] 净向极位移中位 %5.1f | 最高纬中位 %5.1f | 到过28度 %4.1f%%'
              % (int(sel.sum()), np.median(d[sel]), np.median(m[sel]),
                 100 * (m[sel] >= 28).mean()))
    return {'n': n, 'genlat': float(np.median(g)), 'maxlat': float(np.median(m)),
            'dlat': float(np.median(d)), 'p28': float(100 * (m >= 28).mean()),
            'life': float(np.median(lf)), 'peak': float(np.median(pk))}


def vlat_profile(tag, tracks, lo0=100.0, lo1=180.0, bands=None):
    """向极速度剖面: 分纬度带统计 |dlat/dt| (kt), 只取西太 NH 且两时次都在域内。"""
    if bands is None:
        bands = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 60)]
    acc = {b: [] for b in bands}
    for r in tracks:
        for i in range(1, len(r)):
            la0, lo0_, _v0, _p0 = r[i - 1]
            la1, lo1_, _v1, _p1 = r[i]
            if la0 < 0 or la1 < 0:
                continue
            if not (lo0 <= lo0_ <= lo1 and lo0 <= lo1_ <= lo1):
                continue
            mid = 0.5 * (la0 + la1)
            # 经度跨日界线跳变
            dlo = abs(lo1_ - lo0_)
            if dlo > 180.0:
                continue
            vv = (la1 - la0) * KT_PER_DLAT
            for b in bands:
                if b[0] <= mid < b[1]:
                    acc[b].append(vv)
                    break
    print('--- %s 向极速度剖面(西太 %.0f-%.0fE, NH)' % (tag, lo0, lo1))
    for b in bands:
        a = np.array(acc[b], float)
        if len(a) < 20:
            print('    %2d-%2dN  n=%-6d (样本不足)' % (b[0], b[1], len(a)))
            continue
        pos = a[a > 0]
        print('    %2d-%2dN  n=%-6d 均值 %+5.2f kt | 中位 %+5.2f | 正位移占比 %4.1f%% | 正位移中位 %5.2f'
              % (b[0], b[1], len(a), a.mean(), np.median(a),
                 100 * len(pos) / len(a), np.median(pos) if len(pos) else 0.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', default='20250801')
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--sst-anom', type=float, default=0.0)
    ap.add_argument('--tag', default='north')
    ap.add_argument('--obs-only', action='store_true')
    a = ap.parse_args()

    outdir = os.path.join('output', 'sim', '_reference')
    os.makedirs(outdir, exist_ok=True)

    obs = {}
    for nm, d in OBS_SETS.items():
        ps = glob.glob(os.path.join(d, '**', '*.dat'), recursive=True)
        obs[nm] = collect_obs(sorted(ps))

    if not a.obs_only:
        seeds = [s.strip() for s in a.seeds.split(',') if s.strip()]
        tr = collect_model(seeds, a.days, a.sst_anom)
        cache = os.path.join(outdir, 'tracks_%s.json' % a.tag)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(tr, f)
        print('-> 轨迹缓存 %s (%d 条)' % (cache, len(tr)))
    else:
        tr = None

    print()
    print('=' * 96)
    res = {}
    if tr is not None:
        res['模型'] = report('模型', sys_stats(tr))
        vlat_profile('模型', tr)
    for nm, tk in obs.items():
        res[nm] = report(nm, sys_stats(tk))
    print()
    # 观测侧向极速度剖面(用 xrq/wp: 命名台风, 轨迹最长, 剖面最干净)
    vlat_profile('观测 xrq/wp', obs['xrq_wp'])
    print()
    print('判读:')
    print('  ① 若"生成纬中位"已明显低于观测 -> 问题在生成(副高南侧/季风槽位置), 不是引导;')
    print('  ② 若"净向极位移"低而"向极速度剖面"正常 -> 寿命太短, 修寿命;')
    print('  ③ 若"向极速度剖面"在 20-30N **转负** -> 副高/引导场在副热带把风暴压回西行, 修副高;')
    print('  ④ 三项都正常仍不到 28 度 -> 再查变性判据本身。')
    with open(os.path.join(outdir, 'diag_north_%s.json' % a.tag), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('-> %s' % os.path.join(outdir, 'diag_north_%s.json' % a.tag))


if __name__ == '__main__':
    main()
