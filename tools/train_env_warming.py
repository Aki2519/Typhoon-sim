# -*- coding: utf-8 -*-
"""F4: 增暖情景曲线 + 趋势型(烘焙成运行时零依赖的 npz)。

曲线(用户 2026-09 拍板): 21 世纪末 +2.0°C, 2150 前后峰值 +2.5°C, 2250 回归 0。
基准是 1850-1900(工业化前)。控制点用**单调三次插值(PCHIP)**过点, 平滑不振荡。

烘焙成月度表(1700-2300), 运行时只做 np.interp —— 零依赖, 而且你想改曲线
直接改这张表即可, 不用重训。

趋势型: 每 1°C 全球增暖对应的局地变化。这里给的是**参数化型**(极地放大 +
东太厄尔尼诺型增暖 + 北大西洋冷斑), 并按全球面积加权归一化到均值 1.0,
所以 ΔT 就是全球平均异常。等有 1991-2020 的格点 SST 长序列后, 用真实回归型
替换即可(接口不变)。

用法: python tools/train_env_warming.py
输出: simulator/env/model/warming.npz
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')

#: (年份, 相对 1850-1900 的全球平均增暖 °C)
ANCHORS = [
    (1700, 0.00), (1800, 0.00), (1850, 0.00), (1900, 0.20), (1950, 0.45),
    (1980, 0.70), (2000, 0.95), (2026, 1.30), (2050, 1.55), (2075, 1.80),
    (2100, 2.00), (2125, 2.30), (2150, 2.50), (2175, 1.55), (2200, 0.70),
    (2225, 0.20), (2250, 0.00), (2300, 0.00),
]
Y0, Y1 = 1700, 2300


def _pchip(xk, yk, x):
    """单调三次插值(PCHIP)。scipy 可用就用它, 否则退回线性。

    只在**烘焙时**跑, 运行时不需要 scipy。
    """
    try:
        from scipy.interpolate import PchipInterpolator
        return PchipInterpolator(xk, yk)(x)
    except Exception:
        return np.interp(x, xk, yk)


def build_curve():
    """(Y1-Y0+1)*12 个月的增暖表 + 年月轴。"""
    xk = np.array([a[0] for a in ANCHORS], float)
    yk = np.array([a[1] for a in ANCHORS], float)
    yrs = np.arange(Y0, Y1 + 1)
    mon = np.arange(1, 13)
    frac = (yrs[:, None] - Y0) + (mon[None, :] - 0.5) / 12.0 + Y0
    dt = _pchip(xk, yk, frac.ravel()).reshape(yrs.size, 12)
    return yrs.astype(np.int16), mon.astype(np.int8), dt.astype(np.float32)


# ── 趋势型(E1° 网格) ───────────────────────────────────────────

def _grids(nlat=120, nlon=360):
    lat = -60.0 + (np.arange(nlat) + 0.5) * (120.0 / nlat)
    lon = (np.arange(nlon) + 0.5) * (360.0 / nlon)
    return np.meshgrid(lat, lon, indexing='ij')


def trend_patterns():
    """每 1°C 全球增暖对应的 SST / D26 / 风切 局地响应(归一化到全球均值 1)。"""
    LAT, LON = _grids()
    a = np.abs(LAT)
    # SST: 极地放大 + 东太厄尔尼诺型 + 北大西洋冷斑(工业时代观测到的三件特征)
    sst = (1.0 + 0.9 * (a / 60.0) ** 2
           - 0.55 * np.exp(-((LON - 250.0) / 45.0) ** 2)
           * np.exp(-(LAT / 11.0) ** 2)
           - 0.75 * np.exp(-((LON - 320.0) / 26.0) ** 2)
           * np.exp(-((LAT - 52.0) / 13.0) ** 2))
    w = np.cos(np.deg2rad(LAT))
    sst = sst / (sst * w).sum() * w.sum()          # 面积加权均值 = 1
    # D26: 表层增暖 → 26°C 等温线加深; 暖池加深最多, 副热带环流几乎不动
    # 量级: +2.5°C 时暖池 D26 应加深 ~10-20 m(表层增暖把 26°C 等温线压深),
    # 副热带环流几乎不动。0.9 m/°C 太小(只给 2 m), 这里乘 6。
    d26 = 6.0 * (0.9 * np.exp(-(LAT / 22.0) ** 2)
                 + 0.35 * np.exp(-((LON - 140.0) / 60.0) ** 2)
                 * np.exp(-((LAT - 12.0) / 14.0) ** 2))
    d26 = np.clip(d26, 0.0, None)
    # 风切: 变暖下急流极向移动、热带切变略减 —— 量级小, 给一个弱型
    shear = (-0.6 * np.exp(-((a - 30.0) / 12.0) ** 2)
             + 0.3 * np.exp(-((a - 50.0) / 12.0) ** 2))
    return sst.astype(np.float16), d26.astype(np.float16), shear.astype(np.float16)


def main():
    os.makedirs(MODEL, exist_ok=True)
    yrs, mon, dt = build_curve()
    sst, d26, shear = trend_patterns()
    p = os.path.join(MODEL, 'warming.npz')
    np.savez_compressed(p, year=yrs, month=mon, dt=dt,
                        pat_sst=sst, pat_d26=d26, pat_shear=shear,
                        anchors=np.array(ANCHORS, np.float32))
    print('-> warming.npz  %s' % p)
    print('   锚点(年, ΔT): %s' % ANCHORS)
    for y in (1850, 1950, 2000, 2026, 2100, 2150, 2200, 2250):
        i = y - Y0
        print('   %d  ΔT = %.2f °C' % (y, float(dt[i].mean())))
    print('   趋势型: SST 面积加权均值 %.3f(应=1)  范围 %.2f~%.2f'
          % (float((sst.astype(np.float32)
                    * np.cos(np.deg2rad(_grids()[0]))).sum()
                   / np.cos(np.deg2rad(_grids()[0])).sum()),
             float(sst.min()), float(sst.max())))
    print('   D26 型 范围 %.2f~%.2f (m/°C)   风切型 范围 %.2f~%.2f (kt/°C)'
          % (float(d26.min()), float(d26.max()),
             float(shear.min()), float(shear.max())))


if __name__ == '__main__':
    main()
