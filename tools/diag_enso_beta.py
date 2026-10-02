"""诊断: tc_genesis_modes 里 nino34 的 beta 在经度上的结构(不用跑 sim)。

"厄尔尼诺时生成点东移"要求: beta 在中东太(180-140W)**为正**、在西太(120-160E)**为负**。
若符号反了或量级太小, 那么不管怎么把相位接进去, 生成位置都不会按观测方向移动。
"""
import sys

import numpy as np

sys.path.insert(0, '.')
from simulator.simcore.envmodel import store as st      # noqa: E402


def main():
    z = st.load('tc_genesis_modes')
    if z is None:
        print('没有 tc_genesis_modes'); return
    print('keys:', list(z.keys()))
    modes = [str(x) for x in z['modes']] if 'modes' in z else []
    print('modes:', modes)
    clim = np.asarray(z['clim'], np.float64)
    print('clim shape:', clim.shape)
    # 逐模态找 beta 场
    cand = [k for k in z.keys() if k not in ('clim', 'modes', 'lat', 'lon')]
    print('候选 beta 键:', cand[:12])
    for nm in modes:
        for k in (nm + '_b', nm + '_beta', nm):
            if k in z:
                b = np.asarray(z[k], np.float64)
                print('%-8s beta 键=%s shape=%s 量级: min %.4f max %.4f |均值| %.5f'
                      % (nm, k, b.shape, b.min(), b.max(), abs(b).mean()))
                if 'nino' in nm.lower():
                    _band(b, z, nm)
                break


def _band(b, z, nm):
    lon = np.asarray(z['lon'], np.float64) if 'lon' in z else None
    lat = np.asarray(z['lat'], np.float64) if 'lat' in z else None
    print('   lon' , None if lon is None else (lon.min(), lon.max(), len(lon)),
          '| lat', None if lat is None else (lat.min(), lat.max(), len(lat)))
    m = 7            # 8 月(北半球盛期)
    bm = b[m] if b.ndim == 3 else b
    if lon is None:
        return
    print('   %-6s %14s %14s' % ('模态', nm + ' beta 均值', '同纬度带占比'))
    for lo, hi, tag in ((100, 140, 'WP 西'), (140, 180, 'WP 东'),
                        (180, 220, 'CP 中太'), (220, 290, 'EP 东太')):
        sel = (lon >= lo) & (lon < hi)
        if sel.sum() == 0:
            continue
        v = float(np.mean(bm[:, sel]))
        print('   %-6s %14.5f' % (tag, v))


if __name__ == '__main__':
    main()
