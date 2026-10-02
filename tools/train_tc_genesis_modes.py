# -*- coding: utf-8 -*-
"""T2-b: 把**生成密度的年际异常回归到模态索引** —— 生成率与生成位置的年际调制。

为什么需要: 实测 1997(强厄尔尼诺)模拟出 91 条而观测只有 57 条 —— 现在的
gen_check 用的是**气候态生成密度**, 完全没有年际信号。真实的厄尔尼诺会把西太
生成区**东移**、中东太生成增多, 拉尼娜相反。

做法(标准线性响应):
  1. 逐年建生成密度 (12, 120, 360), 减去气候态 → 年异常
  2. 对模态索引(年/月尺度)做最小二乘: anom(year) = Σ_m β_m · idx_m(year)
  3. 存 β_m(每模态每月的 (120,360) 空间型)与截距
运行时: density_eff = density_clim + Σ_m β_m · idx_m(t)  (下限截到 0)

产出: simulator/env/model/tc_genesis_modes.npz
用法: python tools/train_tc_genesis_modes.py
"""
from __future__ import annotations

import collections
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
LAT0, NY, NX = -60.0, 120, 360
#: 回归用的空间分箱(度)。**必须分箱**: 1° 网格下绝大多数格点常年为 0,
#: 45 个样本回归 43200 个格点必然过拟合, 而且 R² 会被空格点主导(实测 -4657)。
BIN = 5.0
BNY, BNX = int(120 / BIN), int(360 / BIN)
#: 参与回归的模态(生成对 ENSO/AMO/PDO 的响应最实在; 其余信噪比太低)
MODES = ('oni', 'pdo', 'amo')
Y0, Y1 = 1980, 2024


def _dt(t):
    import datetime as d
    return d.datetime(int(t) // 1000000, (int(t) // 10000) % 100,
                      (int(t) // 100) % 100, int(t) % 100)


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tclim', os.path.join(ROOT, 'tools', 'train_tc_clim.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tracks, _ = m.collect()

    # 逐年逐月生成密度
    dens = collections.defaultdict(lambda: np.zeros((12, BNY, BNX), np.float32))
    nper = collections.Counter()
    for key, (prio, tr) in tracks.items():
        named = [r for r in tr if r[3] >= 34.0]
        if not named:
            continue
        r = named[0]
        d = _dt(r[0])
        if not (Y0 <= d.year <= Y1):
            continue
        j = int((r[1] - LAT0) / BIN) % BNY
        i = int((r[2] % 360) / BIN) % BNX
        dens[d.year][d.month - 1, j, i] += 1.0
        nper[d.year] += 1
    years = sorted(dens)
    print('年份 %d-%d (%d 年), 命名风暴合计 %d'
          % (min(years), max(years), len(years), sum(nper.values())))

    # 气候态(年际平均)
    clim = np.mean([dens[y] for y in years], axis=0)
    print('气候态生成密度: 非零格 %d, 年生成均值 %.1f'
          % ((clim > 0).sum(), clim.sum() / 12.0 * 12))

    # 模态索引(年、月)
    mi = np.load(os.path.join(MODEL, 'modes_index.npz'), allow_pickle=False)
    names = [str(x) for x in mi['names']]
    val, ok, my, mm = mi['val'], mi['ok'], mi['year'], mi['month']
    y_lo = int(mi['y_lo'])
    K = val.shape[1]
    idx = {}
    for nm in MODES:
        if nm not in names:
            continue
        k = names.index(nm)
        tbl = {}
        for t in range(K):
            if ok[k, t]:
                tbl[(int(my[k, t]), int(mm[k, t]))] = float(val[k, t])
        idx[nm] = tbl
    print('模态:', list(idx))

    # 逐(年,月)回归: anom = Σ β·idx
    beta = {nm: np.zeros((12, BNY, BNX), np.float32) for nm in idx}
    r2 = {nm: np.zeros(12, np.float32) for nm in idx}
    for mo in range(12):
        X, Y = [], []
        for y in years:
            row = []
            good = True
            for nm in idx:
                v = idx[nm].get((y, mo + 1))
                if v is None:
                    good = False
                    break
                row.append(v)
            if good:
                X.append(row)
                Y.append(dens[y][mo] - clim[mo])
        if len(X) < 12:
            continue
        X = np.asarray(X, np.float64)
        Y = np.asarray(Y, np.float64).reshape(len(X), -1)
        A = X.T @ X + np.eye(X.shape[1]) * 1e-3
        B = np.linalg.solve(A, X.T @ Y)                 # (n_mode, ncell)
        pred = X @ B
        ss_res = ((Y - pred) ** 2).sum(0)
        ss_tot = ((Y - Y.mean(0)) ** 2).sum(0)
        # 只在**有方差的格点**上算 R² —— 常年为 0 的格点 ss_tot=0, 会把均值
        # 拉到 -几千(实测 -4657), 那是指标坏了不是拟合坏了
        m = ss_tot > 1e-6
        for j, nm in enumerate(idx):
            beta[nm][mo] = B[j].reshape(BNY, BNX)
            r2[nm][mo] = float(np.mean(1 - ss_res[m] / ss_tot[m])) if m.any() else np.nan

    print('\n各模态对生成密度年际异常的方差解释率(月平均 R²):')
    for nm in idx:
        print('  %-5s R² 均值 %+.3f' % (nm, r2[nm].mean()))
    # 物理校验: 厄尔尼诺应让中西太生成减少、中东太增多
    if 'oni' in beta:
        b = beta['oni'][8]           # 9 月
        def sbox(x0, x1, y0, y1):
            lo = (np.arange(BNX) + 0.5) * BIN
            la = LAT0 + (np.arange(BNY) + 0.5) * BIN
            il = np.nonzero((lo >= x0 % 360) & (lo <= x1 % 360))[0]
            jl = np.nonzero((la >= y0) & (la <= y1))[0]
            return float(b[np.ix_(jl, il)].sum())     # 单位: 风暴/年
        print(chr(10) + '9 月 ONI 响应(每 +1σ ONI 该区域的年生成数变化, 单位 风暴/年):')
        print('  西太 120-160E, 5-20N : %+.2f' % sbox(120, 160, 5, 20))
        print('  中东太 160E-140W     : %+.2f' % sbox(200, 220, 5, 20))
        print('  东太 140-100W, 8-20N : %+.2f' % sbox(220, 260, 8, 20))
        print('  全球合计             : %+.2f' % float(b.sum()))
        print('  (厄尔尼诺应: 西太减少、中东太增多)')
    np.savez_compressed(os.path.join(MODEL, 'tc_genesis_modes.npz'),
                        clim=clim.astype(np.float32),
                        **{('b_' + nm): beta[nm] for nm in beta},
                        modes=np.array(list(beta)))
    print('\n-> tc_genesis_modes.npz')


if __name__ == '__main__':
    main()
