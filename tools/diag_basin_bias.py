"""海盆偏差分解: 找出 sst/shear/rh 三项里是谁把某个海盆压住了(不用跑 sim)。

原理: 生成密度场(输入)乘以环境权重(调制)才得到实际生成率。
把三项不利度按密度加权到每个海盆, 看哪一项在某个海盆特别大。
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
    g = np.asarray(st.load('tc_clim')['genesis'], np.float32)      # (12,120,360)
    dlat = np.asarray(st.load('tc_clim')['lat'], np.float32)
    dlon = np.asarray(st.load('tc_clim')['lon'], np.float32)

    sst = np.asarray(st.load('base_sst')['sst'], np.float32)        # (12,360,720)?
    shr = np.asarray(st.load('base_shear')['shear'], np.float32)
    rh = np.asarray(st.load('base_other')['rh700'], np.float32)
    print('density', g.shape, '| sst', sst.shape, '| shear', shr.shape, '| rh', rh.shape)

    def sample(f, m, la, lo):
        j = int(np.clip(round((la + 89.75) / 0.5), 0, f.shape[1] - 1))
        i = int(np.clip(round((lo % 360.0) / 0.5 - 0.5), 0, f.shape[2] - 1))
        mm = m % f.shape[0]
        return float(f[mm, j, i])

    acc = {}
    for m in range(g.shape[0]):
        d = np.maximum(g[m], 0.0)
        s = float(d.sum())
        if s <= 0:
            continue
        d = d / s
        for j, la in enumerate(dlat):
            for i, lo in enumerate(dlon):
                w = float(d[j, i])
                if w <= 1e-7:
                    continue
                b = basin(float(la), float(lo))
                a = acc.setdefault(b, dict(w=0.0, sst=0.0, shr=0.0, rh=0.0))
                a['w'] += w
                a['sst'] += w * max(0.0, 27.0 - sample(sst, m, la, lo)) / 2.0
                a['shr'] += w * max(0.0, sample(shr, m, la, lo) - 12.0) / 10.0
                a['rh'] += w * max(0.0, 55.0 - sample(rh, m, la, lo)) / 20.0

    print()
    print('%-4s %8s %10s %10s %10s %10s' % ('海盆', '权重份额', 'SST项', '切变项', '湿度项', '合计blk'))
    for b, a in sorted(acc.items(), key=lambda x: -x[1]['w']):
        w = max(a['w'], 1e-9)
        t1, t2, t3 = a['sst'] / w, a['shr'] / w, a['rh'] / w
        print('%-4s %7.1f%% %10.3f %10.3f %10.3f %10.3f'
              % (b, 100 * a['w'] / sum(x['w'] for x in acc.values()), t1, t2, t3, t1 + t2 + t3))
    print()
    print('三项越大 = 该海盆被权重压得越狠(阻滞 = exp(-2.2*blk^2), blk 见上)。')


if __name__ == '__main__':
    main()
