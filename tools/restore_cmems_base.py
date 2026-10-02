# -*- coding: utf-8 -*-
"""从 CMEMS 全球单日文件重建三个基态(SST / OHC / D26)。

为什么要单独一个脚本: tools/train_env_base.py 的 --stage build 会按
"OISST LTM > ERA5 逐月 > raw" 的优先级重写 base_sst, 并把 AOML 写回
base_tchp/base_d26 —— **实测把 CMEMS 单日的成果整个覆盖掉了**。
CMEMS 单日是当前最优来源(单日、0.083° 原生, 能解析黑潮/湾流), 所以单独
留一条恢复通道; train_env_base 里也加了探测, 见到 glob_*.npz 就跳过。

用法: python tools/restore_cmems_base.py
"""
import glob
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')


def iso(a):
    a = np.asarray(a, np.float32).copy()
    pad = [(0, 0)] * (a.ndim - 2) + [(1, 1), (1, 1)]
    for _ in range(80):
        m = ~np.isfinite(a)
        if not m.any():
            break
        p = np.pad(a, pad, mode='edge')
        nb = np.stack([p[..., 0:-2, 1:-1], p[..., 2:, 1:-1],
                       p[..., 1:-1, 0:-2], p[..., 1:-1, 2:],
                       p[..., 0:-2, 0:-2], p[..., 0:-2, 2:],
                       p[..., 2:, 0:-2], p[..., 2:, 2:]])
        with np.errstate(invalid='ignore'):
            avg = np.nanmean(nb, axis=0)
        a = np.where(m & np.isfinite(avg), avg, a)
    return np.where(np.isfinite(a), a, 0.0)


def main():
    fs = sorted(glob.glob(os.path.join(CACHE, 'glob_*.npz')))
    if len(fs) < 12:
        print('只有 %d 个全球单日文件, 需要 12 个' % len(fs))
        return
    z0 = np.load(fs[0])
    la, lo = z0['lat'], z0['lon']
    out = {k: np.zeros((12, la.size, lo.size), np.float32)
           for k in ('sst', 'ohc', 'd26')}
    for f in fs:
        z = np.load(f)
        m = int(os.path.basename(f)[9:11]) - 1
        for k in out:
            out[k][m] = z[k]
    for k in out:
        b = np.stack([iso(out[k][m]) for m in range(12)])
        if k == 'ohc':
            np.savez_compressed(os.path.join(MODEL, 'base_tchp.npz'), tchp=b)
            n = 'base_tchp.npz(tchp)'
        elif k == 'd26':
            np.savez_compressed(os.path.join(MODEL, 'base_d26.npz'), d26=b)
            n = 'base_d26.npz(d26)'
        else:
            np.savez_compressed(os.path.join(MODEL, 'base_sst.npz'), sst=b)
            n = 'base_sst.npz(sst)'
        print('%-22s %.1f~%.1f 均值 %.1f' % (n, b.min(), b.max(), b.mean()))


if __name__ == '__main__':
    main()
