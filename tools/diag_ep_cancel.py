"""诊断: 厄尔尼诺/拉尼娜下, 东太的"权重增益"与"beta 亏损"谁大?(不用跑 sim)

两个效应:
  A) enso_update 让东太变暖(ensoPin=T 时 SST += 1.8*T*dE, dE 中心 145W)⇒ SST 权重上升;
  B) tc_genesis_modes 的 b_oni 在 东太 为负 ⇒ 密度调制下降。
若 A > B, 模型本该在东太多出系统(观测: 厄尔尼诺年中太/东太系统数是拉尼娜年 4 倍);
若 B 压过 A, 就说明 beta 的负号是"不该有的亏损"。
"""
import sys

import numpy as np

sys.path.insert(0, '.')
from simulator.simcore.envmodel import store as st      # noqa: E402


def main():
    zc = st.load('tc_genesis')
    sc, sw = np.asarray(zc['sst_ctr'], float), np.asarray(zc['sst_w'], float)
    g = np.asarray(st.load('tc_clim')['genesis'], np.float64)
    dlat = np.asarray(st.load('tc_clim')['lat'], np.float64)
    dlon = np.asarray(st.load('tc_clim')['lon'], np.float64)
    sst = np.asarray(st.load('base_sst')['sst'], np.float64)

    zm = st.load('tc_genesis_modes')
    b_oni = np.asarray(zm['b_oni'], np.float64)         # (12, 24, 72) 5度分箱
    BNY, BNX = b_oni.shape[1], b_oni.shape[2]
    blat = -60.0 + (np.arange(BNY) + 0.5) * 5.0
    blon = (np.arange(BNX) + 0.5) * 5.0

    LA, LO = np.meshgrid(dlat, dlon, indexing='ij')
    # 东太定义 180-260E(与海盆统计一致)
    ep = (LO >= 180.0) & (LO < 260.0)

    def sst_weight(la, lo, m):
        j = int(np.clip(round((la + 89.75) / 0.5), 0, sst.shape[1] - 1))
        i = int(np.clip(round((lo % 360.0) / 0.5 - 0.5), 0, sst.shape[2] - 1))
        return float(np.interp(float(sst[m, j, i]), sc, sw))

    print('东太(180-260E, 北半球)的 SST 权重随 ENSO 的变化:')
    for T in (2.0, 0.0, -2.0):
        num = den = 0.0
        for m in range(12):
            d = np.maximum(g[m], 0.0)
            if d.sum() <= 0:
                continue
            d = d / d.sum()
            for j in range(d.shape[0]):
                for i in range(d.shape[1]):
                    if not ep[j, i]:
                        continue
                    w = float(d[j, i])
                    if w <= 1e-9:
                        continue
                    num += w * sst_weight(dlat[j], dlon[i], m)
                    den += w
        print('   ensoPin=%+.1f: 东太 SST 权重(密度加权) = %.4f' % (T, num / max(den, 1e-9)))

    print()
    print('b_oni 对东太密度的贡献(按 5 度分箱, 逐年 8 月为例):')
    BLA, BLO = np.meshgrid(blat, blon, indexing='ij')
    epe = (BLO >= 180.0) & (BLO < 260.0)
    for T in (2.0, 0.0, -2.0):
        tot = []
        for m in range(12):
            tot.append(float(b_oni[m][epe].mean()) * T)
        print('   oni=%+.1f: 东太 b_oni 均值 x oni = %+.5f (月均)' % (T, np.mean(tot)))
    print()
    print('判读: 若 SST 权重在厄尔尼诺时**上升**, 而 b_oni 项**为负**, 两者符号相反 ⇒ 抵消。')
    print('      比较两者量级即可判断该修哪一边。')


if __name__ == '__main__':
    main()
