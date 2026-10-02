# -*- coding: utf-8 -*-
"""F3b: 模态空间型回归 —— 把"真实模态索引"翻译成 SST/D26/风切 的空间异常型。

原理: 对每个模态 k、每个滞后 L 做一次最小二乘
        anom(t) = β_L · idx_k(t + L)
      anom 是去掉月气候态的异常场。带滞后是免费的"传播": ENSO 的温跃层异常
      以开尔文波东传、副热带以罗斯贝波西传 —— 滞后回归出来的型自然带相位差,
      不用跑浅水方程, 运行时一次线性组合就有传播。

振幅标定(关键): 输入用 **NCEP sigma0.995 气温**当 SST 代理(海上气温异常与
      SST 异常高度相关, 但振幅不同)。ONI 的定义就是"Niño3.4 区 SST 异常",
      所以 1σ ONI 对应的 Niño3.4 区型值应当 ≈ 1.0°C —— 用它把所有型整体定标,
      比拍一个经验系数靠谱, 而且可复核。

陆地: 气温在陆地上的异常振幅远大于 SST, 用 simcore 的陆地掩码置零。

用法: python tools/train_env_modes_pat.py
输出: simulator/env/model/modes_pat.npz  (型在 2.5° 网格, 运行时双线性升采样)
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')

#: 需要多滞后的模态(有传播)。其余只做 lag=0 —— 产物小一半, 也不会用噪声去
#: 拟合慢变模态。
LAGS = {
    'oni': (-8, -6, -4, -2, 0, 2, 4, 6, 8),
    'nino34': (-8, -6, -4, -2, 0, 2, 4, 6, 8),
    'tni': (-6, -4, -2, 0, 2, 4),
    'dmi': (-6, -4, -2, 0, 2, 4),
    'pdo': (-4, -2, 0, 2, 4),
}
LAG0 = (0,)
#: Niño3.4 区(5N-5S, 120W-170W) —— ONI 的定义区, 用来定标振幅
N34 = (5.0, -5.0, 190.0, 240.0)


def _land25(lat, lon):
    """2.5° 陆地掩码(从 simcore 的 0.5° 场最近邻采样)。"""
    try:
        from simulator.simcore import core as V
        m = V.build_land().reshape(V.ENVNY, V.ENVNX)
        glat = -59.75 + np.arange(V.ENVNY) * V.C['ENVD']
        glon = 0.25 + np.arange(V.ENVNX) * V.C['ENVD']
        out = np.zeros((lat.size, lon.size), bool)
        for j, la in enumerate(lat):
            if not (-60.0 <= la <= 60.0):
                continue
            jj = int(np.argmin(abs(glat - la)))
            for i, lo in enumerate(lon):
                ii = int(np.argmin(abs(glon - (lo % 360.0))))
                out[j, i] = m[jj, ii] >= 0.5
        return out
    except Exception:
        return np.zeros((lat.size, lon.size), bool)


def load_fields():
    """返回 [(字段名, 异常场(T,nlat,nlon), year, month, lat, lon, 定标目标)]。

    SST 用 NCEP 海上气温当代理(长序列可得); 风切用 NCEP 的 200-850 风矢量差,
      是**真值**, 直接回归即可。
    """
    out = []
    fa = os.path.join(CACHE, 'ncep_air.npz')
    if os.path.exists(fa):
        z = np.load(fa)
        out.append(('sst', z['air'], z['year'].astype(np.int32),
                    z['month'].astype(np.int32), z['lat'], z['lon']))
    fu = os.path.join(CACHE, 'ncep_shr_uwnd.npz')
    fv = os.path.join(CACHE, 'ncep_shr_vwnd.npz')
    if os.path.exists(fu) and os.path.exists(fv):
        zu, zv = np.load(fu), np.load(fv)
        # 两文件的时间轴都是 1991-2020 连续 360 月
        sh = np.hypot(zu['data'], zv['data']) * 1.94384          # m/s → kt
        n = sh.shape[0]
        yr = np.repeat(np.arange(1991, 1991 + n // 12 + 1), 12)[:n]
        mo = np.tile(np.arange(1, 13), n // 12 + 1)[:n]
        out.append(('shear', sh, yr.astype(np.int32), mo.astype(np.int32),
                    zu['lat'], zu['lon']))
    return out


def _anom(a, mo, land):
    clim = np.stack([a[mo == m].mean(0) for m in range(1, 13)])
    an = a - clim[mo - 1]
    if land is not None:
        an[:, land] = 0.0
    return an


def main():
    fields = load_fields()
    if not fields:
        print('缺 ncep_air.npz / ncep_shr_*.npz, 先跑对应的抓取脚本')
        return

    mi = np.load(os.path.join(MODEL, 'modes_index.npz'), allow_pickle=False)
    names = [str(x) for x in mi['names']]
    mval, mok = mi['val'], mi['ok']
    y_lo = int(mi['y_lo'])
    T = mval.shape[1]

    results = {}
    for fname, raw, yr, mo, lat, lon in fields:
        land = _land25(lat, lon) if fname == 'sst' else None
        anom = _anom(raw, mo, land)
        print('[%s] 序列 %s  %d-%d  异常 std(热带) %.2f'
              % (fname, raw.shape, yr.min(), yr.max(),
                 float(anom[:, max(0, anom.shape[1] // 2 - 12):
                             anom.shape[1] // 2 + 12].std())), flush=True)
        results[fname] = (anom, yr, mo, lat, lon)

    # ── 全模态 × 全滞后 **一次联合最小二乘**(每个字段各解一次) ──
    # 两层重复计数都要避免:
    #   (1) 同一模态的多个滞后高度自相关 —— 逐滞后边际回归再相加会放大 4~5 倍;
    #   (2) PDO/TNI/DMI 与 ENSO 本身强相关 —— 分模态各自回归再相加, 等于把
    #       厄尔尼诺算了两三遍(实测东太 0N/110W 被推到 +7.9°C, 真实约 +4)。
    # 把 (模态 × 滞后) 全部放进一个设计矩阵联合求解, 重建才无偏。
    cols, col_of = [], {}
    for k, nm in enumerate(names):
        for L in LAGS.get(nm, LAG0):
            col_of[(nm, L)] = len(cols)
            cols.append((k, nm, L))

    out = {'names': np.array(names), 'src': np.array(['ncep'])}
    for fname, (anom, yr, mo, lat, lon) in results.items():
        rows, tgt = [], []
        for t in range(anom.shape[0]):
            v, ok = [], True
            for (k, nm, L) in cols:
                tt = (int(yr[t]) - y_lo) * 12 + int(mo[t]) - 1 + L
                if tt < 0 or tt >= T or not mok[k, tt]:
                    ok = False
                    break
                v.append(float(mval[k, tt]))
            if ok:
                rows.append(v)
                tgt.append(t)
        print('[%s] 设计矩阵 %d 列 × %d 有效月'
              % (fname, len(cols), len(tgt)), flush=True)
        if len(tgt) < 60:
            continue
        X = np.asarray(rows, np.float64)
        Y = anom[tgt].reshape(len(tgt), -1).astype(np.float64)
        A = X.T @ X + np.eye(X.shape[1]) * 1e-4          # 岭正则: 模态间共线
        B = np.linalg.solve(A, X.T @ Y)
        rec = X @ B
        print('[%s] 重建 std %.2f(异常 std %.2f)'
              % (fname, float(rec.std()), float(Y.std())), flush=True)

        scale = 1.0
        if fname == 'sst':
            # 定标必须用**全部模态的总重建** —— 联合解里 ENSO 的信号被分摊到了
            # 与之相关的 oni/nino34/tni/pdo/pmm 上, 只看 oni 一个会低估一半。
            # 目标: ONI 的定义就是"Niño3.4 区 3 个月滑动 SST 异常", 所以
            # 1σ 标准化 ONI 对应 Niño3.4 区 σ_ONI(=0.83°C)的异常。
            sd_idx = float(mi['sd'][names.index('oni')]) if 'oni' in names else 0.83
            jl = np.nonzero((lat <= N34[0]) & (lat >= N34[1]))[0]
            il = np.nonzero((lon >= N34[2]) & (lon <= N34[3]))[0]
            bf = np.zeros(anom.shape[1] * anom.shape[2], bool)
            for j in jl:
                for i in il:
                    bf[j * anom.shape[2] + i] = True
            box = rec[:, bf].mean(1)
            if np.std(box) > 1e-6:
                scale = sd_idx / float(np.std(box))
            print('[%s] 振幅定标: 重建 Niño3.4 std %.3f K/σ, σ_ONI %.2f°C → ×%.3f'
                  % (fname, float(np.std(box)), sd_idx, scale), flush=True)
        else:
            # 风切是**真值**, 回归系数本身就是 kt/σ, 不需要再定标
            print('[%s] 不做振幅定标(真值场, 系数即 kt/σ)' % fname, flush=True)
        out['scale_%s' % fname] = np.float32(scale)

        for nm in names:
            ls = [L for (k2, nm2, L) in cols if nm2 == nm]
            if not ls:
                continue
            stack = [B[col_of[(nm, L)]].reshape(anom.shape[1:]).astype(np.float32)
                     * scale for L in ls]
            out['%s__%s' % (fname, nm)] = np.stack(stack).astype(np.float16)
            out['lag_%s' % nm] = np.array(ls, np.int8)

    os.makedirs(MODEL, exist_ok=True)
    np.savez_compressed(os.path.join(MODEL, 'modes_pat.npz'), **out)
    print('-> modes_pat.npz  fields=%s' % sorted(results))
    for fname in results:
        print('  [%s] scale=%.3f  anom_std=%.2f'
              % (fname, float(out['scale_%s' % fname]),
                 float(results[fname][0].std())))
        for nm in names:
            kk = '%s__%s' % (fname, nm)
            if kk not in out:
                continue
            p2 = out[kk].astype(np.float32)
            print('    %-8s %-26s %8.2f .. %-8.2f'
                  % (nm, str(out['lag_%s' % nm].tolist()), p2.min(), p2.max()))


if __name__ == '__main__':
    main()
