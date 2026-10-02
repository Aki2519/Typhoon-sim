"""诊断: 环境权重 _gi_weight 的四条曲线在各海盆的平均值(不用跑 sim)。

它是真正的"加权器": 生成率 = 密度场 x 权重曲线乘积。
密度场东太占 26.2%, 但模型产出只占 ~14% ⇒ 一定是权重曲线在压东太。
本脚本把四条曲线逐海盆、按密度加权求平均, 定位是**哪一条**压的。
"""
import sys

import numpy as np

sys.path.insert(0, '.')
from simulator.simcore.envmodel import store as st      # noqa: E402


def basin(la, lo):
    if la < 0:
        return 'SH'
    if 100 <= lo < 180:
        return 'WP'
    if 180 <= lo < 260:
        return 'EP'
    if 260 <= lo < 360:
        return 'AL'
    return 'IO'


def main():
    zc = st.load('tc_genesis')
    print('tc_genesis keys:', list(zc.keys()))
    curves = {}
    for k in ('sst', 'shear', 'rh', 'lat'):
        ck, wk = k + '_ctr', k + '_w'
        if ck in zc and wk in zc:
            curves[k] = (np.asarray(zc[ck], np.float64), np.asarray(zc[wk], np.float64))
    print('可用曲线:', list(curves.keys()))
    for k, (c, w) in curves.items():
        print('   %-6s 控制点 %s -> 权重 %s' % (k, np.round(c, 2), np.round(w, 3)))

    g = np.asarray(st.load('tc_clim')['genesis'], np.float64)      # (12,120,360)
    dlat = np.asarray(st.load('tc_clim')['lat'], np.float64)
    dlon = np.asarray(st.load('tc_clim')['lon'], np.float64)
    sst = np.asarray(st.load('base_sst')['sst'], np.float64)
    shr = np.asarray(st.load('base_shear')['shear'], np.float64)
    rh = np.asarray(st.load('base_other')['rh700'], np.float64)

    def s3(f, m, la, lo):
        j = int(np.clip(round((la + 89.75) / 0.5), 0, f.shape[1] - 1))
        i = int(np.clip(round((lo % 360.0) / 0.5 - 0.5), 0, f.shape[2] - 1))
        return float(f[m % f.shape[0], j, i])

    # lat 曲线用的是 |lat|
    acc = {}
    for m in range(12):
        d = np.maximum(g[m], 0.0)
        s = float(d.sum())
        if s <= 0:
            continue
        d = d / s
        for j, la in enumerate(dlat):
            for i, lo in enumerate(dlon):
                w = float(d[j, i])
                if w <= 1e-9:
                    continue
                vals = {'sst': s3(sst, m, la, lo), 'shear': s3(shr, m, la, lo),
                        'rh': s3(rh, m, la, lo), 'lat': abs(float(la))}
                b = basin(float(la), float(lo))
                a = acc.setdefault(b, dict(w=0.0, **{k: 0.0 for k in curves}))
                a['w'] += w
                for k, (c, ww) in curves.items():
                    a[k] += w * float(np.interp(vals[k], c, ww))
    print()
    print('%-4s %8s | %s | %8s' % ('海盆', '密度份额',
                                   ' '.join('%7s' % k for k in curves), '乘积'))
    tot = sum(x['w'] for x in acc.values())
    for b, a in sorted(acc.items(), key=lambda x: -x[1]['w']):
        w = max(a['w'], 1e-9)
        prod = 1.0
        cols = []
        for k in curves:
            v = a[k] / w
            cols.append('%7.3f' % v)
            prod *= v
        print('%-4s %7.1f%% | %s | %8.3f' % (b, 100 * a['w'] / tot, ' '.join(cols), prod))
    print()
    print('乘积越小 = 该海盆的环境权重越差。对比模型实际产出份额(轮次 56):')
    print('   WP 40.7% / EP 18.2% / AL 5.9% / IO 11.4% / SH 23.9%')


if __name__ == '__main__':
    main()
