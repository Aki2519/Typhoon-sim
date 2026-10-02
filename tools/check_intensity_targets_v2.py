"""逐格强度步长验收(判据 v2.1)—— 目标 ③ 的过程量检查器。

用法:
    python tools/check_intensity_targets_v2.py --samples "output/sim/_reference/samples_v2/samples_*.npz"

**v2.1 口径修正(轮次 34)**: v2 里用 dn_mean 跟观测比是**错的**。
观测的"降"按 `dV<=0` 划分, 其中包含大量**恰为 0 的步**(官方/自评定位之间强度被写成不变,
零步约占 30%), 而模型的 dV 是连续量、几乎没有精确的 0 ⇒ 观测的 dn_mean 被零步稀释
(如 <34 档 −0.70), 模型的 dn_mean 没有 ⇒ 两者不可比, 会让人去"修"一个不存在的问题。
本版改用**可比口径**:
    · 分位数 p10 / p50 / p90(对零步质量不敏感, 两边同定义);
    · 平步占比 |dV| <= 2 kt/6h(直接度量"几乎没变"的那部分质量);
    · 均值 mean(无划分, 两边同定义, 仍然可比);
    · up 占比 / up 均值(升侧只在 dV>0 上定义, 两边同定义, 可比)。
dn_mean 仅作参考打印, **不作为判据**。
"""
import argparse
import glob
import io
import json

import numpy as np

BINS = [(0, 34), (34, 48), (48, 64), (64, 80), (80, 100), (100, 999)]
LABELS = ['<34', '34-48', '48-64', '64-80', '80-100', '100+']


def load(pattern):
    Vs, DV, SST, SHR = [], [], [], []
    for p in sorted(glob.glob(pattern)):
        d = np.load(p, allow_pickle=True)
        tc = d['tc']
        v = d['vmax'].astype(np.float64)
        t = d['t'].astype(np.float64)
        order = np.lexsort((t, tc))
        tc, v, t = tc[order], v[order], t[order]
        sst = d['sst'].astype(np.float64)[order]
        shr = d['shear'].astype(np.float64)[order]
        dts = np.diff(t)
        same = tc[1:] == tc[:-1]
        ok = same & (dts > 0) & (dts < 24)
        dv6 = (v[1:] - v[:-1])[ok] / dts[ok] * 6.0
        Vs.append(v[:-1][ok])
        DV.append(dv6)
        SST.append(sst[:-1][ok])
        SHR.append(shr[:-1][ok])
    return (np.concatenate(Vs), np.concatenate(DV),
            np.concatenate(SST), np.concatenate(SHR))


def stats(dv):
    if dv.size == 0:
        return dict(n=0)
    up = dv[dv > 0]
    dn = dv[dv < 0]
    return dict(n=int(dv.size), mean=float(dv.mean()),
                p10=float(np.percentile(dv, 10)), p50=float(np.median(dv)),
                p90=float(np.percentile(dv, 90)),
                flat=float(np.mean(np.abs(dv) <= 2.0)),
                p_up=float(up.size / dv.size),
                up_mean=float(up.mean()) if up.size else 0.0,
                dn_mean=float(dn.mean()) if dn.size else 0.0)


def line(tag, s, o=None):
    if not s.get('n'):
        print('%-14s n=0' % tag)
        return
    a = ('%-14s n=%-6d mean=%+5.2f  p10=%+6.1f p50=%+6.1f p90=%+6.1f  flat=%3.0f%% 升=%2.0f%%(升均%+5.2f)'
         % (tag, s['n'], s['mean'], s['p10'], s['p50'], s['p90'],
            100 * s['flat'], 100 * s['p_up'], s['up_mean']))
    print(a)
    if o:
        b = ('%-14s             mean=%+5.2f  p10=%+6.1f p50=%+6.1f p90=%+6.1f  flat=%3.0f%% 升=%2.0f%%(升均%+5.2f)'
             % ('   观测', o['mean'], o['p10'], o['p50'], o['p90'],
                100 * o['flat'], 100 * o['p_up'], o['up_mean']))
        print(b)


def obs_frac_flat(lab):
    """观测的平步占比只能从 rate_tables 的划分反推上界; 这里显式声明为未知(None)。"""
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', required=True)
    ap.add_argument('--ref', default=r'output/sim/_reference/rate_tables.json')
    a = ap.parse_args()

    V, DV, SST, SHR = load(a.samples)
    obs = json.load(io.open(a.ref, encoding='utf-8'))
    print('模型样本 %d 步  (源: %s)' % (V.size, a.samples))
    print('口径: 分位数 + 平步占比(|dV|<=2) + mean + 升占比/升均值; dn_mean 不再作判据')
    print('=' * 122)
    for lab, (lo, hi) in zip(LABELS, BINS):
        m = (V >= lo) & (V < hi)
        o = obs['by_v'].get(lab) or {}
        oref = None
        if o.get('all'):
            n_all = o['all']['n']
            oref = dict(mean=o['all']['mean'], p10=o['all'].get('p10', float('nan')),
                        p50=o['all'].get('med', float('nan')), p90=o['all'].get('p90', float('nan')),
                        flat=float('nan'),      # 观测的平步占比无法从该表给出 ⇒ 不假装知道
                        p_up=(o['up']['n'] / n_all if n_all and o.get('up') else 0),
                        up_mean=(o['up']['mean'] if o.get('up') else 0))
        line('  %-8s' % lab, stats(DV[m]), oref)

    print()
    print('34-48kt 逐环境格')
    m48 = (V >= 34) & (V < 48)
    for tag, mask, key in (('SST>=28.5', SST >= 28.5, '>=28.5'),
                           ('shear 10-20', (SHR >= 10) & (SHR < 20), '10-20'),
                           ('shear >=30', SHR >= 30, '>=30')):
        e = obs['by_env'].get('34-48', {}).get(key) or {}
        oref = None
        if e.get('all'):
            n_all = e['all']['n']
            oref = dict(mean=e['all']['mean'], p10=e['all'].get('p10', float('nan')),
                        p50=e['all'].get('med', float('nan')), p90=e['all'].get('p90', float('nan')),
                        flat=float('nan'),
                        p_up=(e.get('up', {}).get('n', 0) / n_all if n_all else 0),
                        up_mean=e.get('up', {}).get('mean', 0))
        line('  %-12s' % tag, stats(DV[m48 & mask]), oref)


if __name__ == '__main__':
    main()
