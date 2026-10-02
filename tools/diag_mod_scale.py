"""诊断: gen_check 里 d(归一化密度) 与 _m(模态调制项) 的**量级关系**。

嫌疑: `d = d + _m`, 但 d 是**归一化到和为 1** 的 1 度密度(每格量级 1e-5),
而 _m = clim + Σ b·idx 是 **5 度分箱**的量(量级未知)。
若两者差几个数量级, 那么相加后由其中一方完全主导, beta 的**相对作用**就被放大或压掉,
表现为"beta 符号是对的, 但结果没反应"。
"""
import sys

import numpy as np

sys.path.insert(0, '.')
from simulator.simcore.envmodel import store as st      # noqa: E402


def band_mean(arr2d, lat1d, lon1d, lo, hi, south=False):
    """在给定经度带(可限南半球)内取平均。"""
    LA, LO = np.meshgrid(lat1d, lon1d, indexing='ij')
    sel = (LO >= lo) & (LO < hi)
    if south:
        sel &= (LA < 0)
    else:
        sel &= (LA >= 0)
    if sel.sum() == 0:
        return float('nan')
    return float(np.mean(arr2d[sel]))


def main():
    z = st.load('tc_clim')
    g = np.asarray(z['genesis'], np.float64)          # (12,120,360) 1 度
    dlat = np.asarray(z['lat'], np.float64)
    dlon = np.asarray(z['lon'], np.float64)

    zm = st.load('tc_genesis_modes')
    clim = np.asarray(zm['clim'], np.float64)          # (12, BNY, BNX) 5 度
    modes = [str(x) for x in zm['modes']]
    bs = {nm: np.asarray(zm['b_' + nm], np.float64) for nm in modes}
    print('modes:', modes, '| clim shape', clim.shape)
    print('b_oni shape', bs['oni'].shape)

    BNY, BNX = clim.shape[1], clim.shape[2]
    blat = -60.0 + (np.arange(BNY) + 0.5) * 5.0
    blon = (np.arange(BNX) + 0.5) * 5.0

    m = 7          # 8 月
    d = np.maximum(g[m], 0.0)
    d = d / max(float(d.sum()), 1e-9)                  # **与 gen_check 一致: 归一化**
    print()
    print('d(归一化 1 度密度): 平均 %.3e | 最大 %.3e | 全格和 %.3f'
          % (d.mean(), d.max(), d.sum()))

    for pin in (0.0, 2.0):
        _m = clim[m].copy()
        for nm in modes:
            idx = float(pin) if nm == 'oni' else float(
                __import__('simulator.simcore.envmodel.modes', fromlist=['x'])
                .ModeSeries().at(2025, m + 1)[
                    __import__('simulator.simcore.envmodel.modes', fromlist=['x'])
                    .ModeSeries().names.index(nm)])
            _m = _m + bs[nm][m] * idx
        _m = np.maximum(_m, 0.0)
        print()
        print('=== oni = %+.1f ===' % pin)
        print('_m(5 度调制项): 平均 %.3e | 最大 %.3e | 全格和 %.3f'
              % (_m.mean(), _m.max(), _m.sum()))
        print('   差值 d 与 _m 的比值(全格平均): _m/d = %.1f 倍'
              % (_m.mean() / max(d.mean(), 1e-30)))
        # 东太 / 西太 各自的比例
        for lo, hi, tag in ((100, 180, 'WP 100-180E'), (180, 260, 'EP 180-260E')):
            dv = band_mean(d, dlat, dlon, lo, hi)
            mv = band_mean(_m, blat, blon, lo, hi)
            print('   %-14s d=%.3e  _m=%.3e  _m/d=%6.1f' % (tag, dv, mv, mv / max(dv, 1e-30)))
        print('   加调制后 d 在 东太/西太 的比值: %.3f'
              % ((band_mean(d, dlat, dlon, 180, 260) + band_mean(_m, blat, blon, 180, 260))
                 / max(band_mean(d, dlat, dlon, 100, 180) + band_mean(_m, blat, blon, 100, 180), 1e-30)))


if __name__ == '__main__':
    main()
