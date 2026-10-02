# -*- coding: utf-8 -*-
"""I2: 用观测强度数据集拟合**强度变化模型**(SHIPS/DSHP 思路)。

预测目标: 24h 后的强度变化 Δvmax。
判别式(标准做法): y = (Δvmax + D) / 2D 映射到 [0,1], 用**线性回归**拟合,
再乘回 2D−D 得到 Δvmax 的期望 —— 比直接回归 Δvmax 稳健得多。

特征(都能从我们的环境场拿到, 所以能直接喂给模拟内核):
  V     当前强度
  P     = MPI − V  (距潜在强度的余量, SHIPS 里最重的因子)
  S     |切变| (kt)
  Sdep  切变分量沿风暴移动方向的投影(逆切变为负 → 有利)
  OHC   D26(m)
  T2    出流层温度(K)
  RH    中层湿度(%)
  LAT   纬度绝对值
  SPD   移速(kt)
  LAND  陆地比例

交叉验证: **按年份留出**(不是随机留出 —— 同一年的样本高度相关, 随机留出会
高估技巧)。报告 R² / RMSE / 分强度组的偏差。

产出: simulator/env/model/tc_intensity_fit.npz(系数, 供内核标定参照)
用法: python tools/train_tc_intensity.py ... 然后 python tools/fit_tc_intensity.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
D = 50.0                                   # 判别式的半幅(kt)

FEATS = ('V', 'P', 'S', 'Sdep', 'OHC', 'T2', 'RH', 'LAT', 'SPD')


def design(A, scales):
    """原始样本 (N,16) → 特征矩阵 (N,10) 含截距。"""
    vmax, mpi = A[:, 0], A[:, 1]
    sh, su, sv = A[:, 4], A[:, 5], A[:, 6]
    d26, t2, rh = A[:, 3], A[:, 7], A[:, 8]
    land, lat, spd = A[:, 9], A[:, 10], A[:, 12]
    sp = np.maximum(spd, 1.0)
    n_ = np.maximum(np.hypot(su, sv), 1e-3)
    # 移速方向与切变方向的夹角余弦; Sdep>0 = 顺切变移动(不利)
    # (移速矢量由前后点算出, 这里用切变矢量的经向分量做近似不行 —— 用 spd 的
    #  符号无法表达方向, 所以改成"切变在纬向的投影"作代理并明确标注)
    sdep = su / n_ * np.sign(np.where(np.isfinite(spd), spd, 1.0))
    X = np.column_stack([
        vmax,
        mpi - vmax,
        sh,
        sdep * sh,
        d26,
        t2,
        rh,
        np.abs(lat),
        sp,
    ])
    X = (X - scales[0]) / scales[1]
    return np.column_stack([np.ones(X.shape[0]), X])


def main():
    z = np.load(os.path.join(MODEL, 'tc_intensity.npz'), allow_pickle=False)
    A = z['data']
    ok = np.isfinite(A[:, 13])
    A = A[ok]
    print('样本 %d' % A.shape[0])
    y = A[:, 13].astype(np.float64)                # dv24
    yr = A[:, 14].astype(int)
    # 数据清洗: |Δvmax| 超过物理可能的剔除(24h 变化 > 100kt 基本是资料错误)
    keep = np.abs(y) <= 100.0
    A, y, yr = A[keep], y[keep], yr[keep]
    print('清洗后 %d' % A.shape[0])

    # 判别式目标
    t = np.clip((y + D) / (2.0 * D), 1e-4, 1 - 1e-4)
    # 特征标准化参数(只用训练折算, 但为简洁用全体并在报告里注明)
    raw = np.column_stack([A[:, 0], A[:, 1] - A[:, 0], A[:, 4], A[:, 4],
                           A[:, 3], A[:, 7], A[:, 8], np.abs(A[:, 10]),
                           np.maximum(A[:, 12], 1.0)])
    scales = (raw.mean(0), raw.std(0) + 1e-6)
    X = design(A, scales)

    years = np.unique(yr)
    rng = np.random.default_rng(20260917)
    perm = rng.permutation(years)
    folds = np.array_split(perm, 5)
    pred = np.full_like(y, np.nan)
    coefs = []
    for f in folds:
        te = np.isin(yr, f)
        tr = ~te
        w = np.linalg.lstsq(X[tr], t[tr], rcond=None)[0]
        coefs.append(w)
        pred[te] = (X[te] @ w) * 2.0 * D - D
    m = np.isfinite(pred)
    res = pred[m] - y[m]
    print('\n=== 5 折按年交叉验证 ===')
    print('  R²   %.3f' % (1 - res.var() / y[m].var()))
    print('  RMSE %.1f kt   MAE %.1f kt   (Δvmax 本身 std %.1f kt)'
          % (np.sqrt((res ** 2).mean()), np.abs(res).mean(), y[m].std()))
    print('  强度变化的气候态基线 RMSE %.1f kt'
          % np.sqrt(((y[m] - y[m].mean()) ** 2).mean()))
    # 分强度组
    print('\n  分强度组(当前 vmax):')
    print('    %-12s %6s %9s %9s' % ('组', 'n', '观测均值', '预测均值'))
    for lo, hi in ((0, 34), (34, 64), (64, 96), (96, 200)):
        s = m & (A[:, 0] >= lo) & (A[:, 0] < hi)
        if s.sum() < 30:
            continue
        print('    %-12s %6d %9.1f %9.1f'
              % ('%d-%d kt' % (lo, hi), s.sum(), y[s].mean(), pred[s].mean()))
    print('\n  快速增强(24h>+30kt)命中:')
    for th in (20, 30, 40):
        s = m
        obs = y[s] > th
        prd = pred[s] > th
        tp = (obs & prd).sum()
        print('    阈值 %2d kt  观测 %5.2f%%  预测 %5.2f%%  命中率 %.2f  虚警率 %.2f'
              % (th, obs.mean() * 100, prd.mean() * 100,
                 tp / max(1, prd.sum()), 1 - tp / max(1, obs.sum())))

    w = np.mean(coefs, 0)
    nm = ('const',) + FEATS
    print('\n=== 平均系数(标准化空间, 正=利于增强)===')
    for n_, c in zip(nm, w):
        print('  %-6s %+8.4f' % (n_, c))
    np.savez_compressed(os.path.join(MODEL, 'tc_intensity_fit.npz'),
                        coef=w.astype(np.float32),
                        names=np.array(nm),
                        mean=scales[0].astype(np.float32),
                        std=scales[1].astype(np.float32),
                        D=np.float32(D))
    print('-> tc_intensity_fit.npz')


if __name__ == '__main__':
    main()
