# -*- coding: utf-8 -*-
"""补齐模态表缺的 **ATL3(大西洋 Niño)**: PSL 没有现成索引, 自己算。

定义: ATL3 = 热带大西洋 3°S-3°N / 20°W-0° 的区域平均 SST 异常。
这里用与 SST 空间型同一套 NCEP sigma0.995 气温代理(海上气温异常 ≈ SST 异常),
所以索引与型是自洽的。

校验: 算出来的指数应当在 2010 / 2021 等已知大西洋 Niño 事件上为正,
      且与 ENSO(ONI)的相关不应过高(否则就不是独立的模态)。

用法: python tools/add_atl3.py          # 追加到 modes_index.npz
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')

ATL3 = (-3.0, 3.0, -20.0, 0.0)          # lat0, lat1, lon0, lon1(东西经)


def main():
    fa = os.path.join(CACHE, 'ncep_air.npz')
    if not os.path.exists(fa):
        print('缺 ncep_air.npz')
        return
    z = np.load(fa)
    air, yr, mo = z['air'], z['year'].astype(np.int32), z['month'].astype(np.int32)
    lat, lon = z['lat'], z['lon']
    lonn = np.where(lon > 180.0, lon - 360.0, lon)          # → -180..180
    jl = np.nonzero((lat >= ATL3[0]) & (lat <= ATL3[1]))[0]
    il = np.nonzero((lonn >= ATL3[2]) & (lonn <= ATL3[3]))[0]
    if jl.size == 0 or il.size == 0:
        print('ATL3 区取不到格点'); return
    box = air[:, jl][:, :, il].mean(axis=(1, 2))
    clim = np.array([box[mo == m].mean() for m in range(1, 13)])
    anom = box - clim[mo - 1]
    sd = float(anom.std())
    atl3 = (anom - anom.mean()) / (sd if sd > 1e-9 else 1.0)
    print('ATL3(气温代理): %d-%d  n=%d  σ=%.2f K' % (yr.min(), yr.max(),
                                                     atl3.size, sd))

    mi = np.load(os.path.join(MODEL, 'modes_index.npz'), allow_pickle=False)
    names = [str(x) for x in mi['names']]
    if 'atl3' in names:
        print('modes_index 里已有 atl3, 跳过')
        return
    K, T = mi['val'].shape
    y_lo = int(mi['y_lo'])
    val = np.full((K + 1, T), np.nan, np.float32)
    ok = np.zeros((K + 1, T), bool)
    year = np.zeros((K + 1, T), np.int16)
    month = np.zeros((K + 1, T), np.int8)
    val[:K] = mi['val']
    ok[:K] = mi['ok']
    year[:K] = mi['year']
    month[:K] = mi['month']
    for t in range(air.shape[0]):
        tt = (int(yr[t]) - y_lo) * 12 + int(mo[t]) - 1
        if 0 <= tt < T:
            val[K, tt] = atl3[t]
            ok[K, tt] = True
            year[K, tt] = yr[t]
            month[K, tt] = mo[t]
    names2 = names + ['atl3']
    # 关键: mi['val'] 已经是**标准化**过的, 这里的 s2 会算成 ~1.0。但 mi['sd']
    # 存的是**物理单位**的标准差(ONI 是 0.83°C), 模态空间型的振幅定标要靠它 ——
    # 覆盖成 1.0 会让 SST 型整体偏强 20%。所以原样保留前 K 个, 只给新模态算。
    mu_old = np.asarray(mi['mu'], np.float64)
    sd_old = np.asarray(mi['sd'], np.float64)
    atl3_phys_sd = float(np.std(anom))          # 新模态的物理 σ(K)
    mu = np.append(mu_old, float(np.mean(anom)))
    s2 = np.append(sd_old, atl3_phys_sd if atl3_phys_sd > 1e-6 else 1.0)
    # val 已经是"前 K 行标准化 + 新行未标准化"的混合, 只重写新行
    valz = val.copy()
    row = np.full(T, np.nan, np.float32)
    for t in range(air.shape[0]):
        tt = (int(yr[t]) - y_lo) * 12 + int(mo[t]) - 1
        if 0 <= tt < T:
            row[tt] = (anom[t] - anom.mean()) / (atl3_phys_sd
                                                 if atl3_phys_sd > 1e-6 else 1.0)
    valz[K] = row
    avail0 = np.array([year[k][np.argmax(ok[k])] if ok[k].any() else 9999
                       for k in range(K + 1)], np.int16)
    out = dict(names=np.array(names2), year=year, month=month, val=valz, ok=ok,
               avail0=avail0, mu=mu.astype(np.float32), sd=s2.astype(np.float32),
               y_lo=mi['y_lo'], y_hi=mi['y_hi'])
    if 'rmm' in mi.files:
        out['rmm'] = mi['rmm']
    np.savez_compressed(os.path.join(MODEL, 'modes_index.npz'), **out)
    print('-> modes_index.npz  %d 个模态: %s' % (len(names2), names2))

    # 校验: 已知大西洋 Niño 年(2010 / 2021)应为正; 与 ONI 的相关不应过高
    t2 = (2021 - y_lo) * 12 + 6
    k = names2.index('atl3')
    print('  2021-07 ATL3 = %+.2f σ   2010-07 = %+.2f σ'
          % (valz[k, (2010 - y_lo) * 12 + 6], valz[k, t2]))
    if 'oni' in names2:
        j = names2.index('oni')
        m = ok[k] & ok[j]
        print('  与 ONI 的相关 %.2f(应偏低 = 相对独立)'
              % float(np.corrcoef(valz[k][m], valz[j][m])[0, 1]))


if __name__ == '__main__':
    main()
