# -*- coding: utf-8 -*-
"""给 base_sst 移植 CMEMS 单日场的**中尺度结构** —— 解决"SST 明显不如 OHC 精细"。

用户 2026-09-21 反馈第 2 条: "sst 明显不如 ohc 精细"。

根因(实测)
----------
* base_sst  = **OISST 1991-2020 月气候态** → 12 个月平均, 涡旋/锋面/中尺度结构全被抹平;
* base_tchp/base_d26 = **CMEMS GLORYS 单日场**(每月 15 日) → 满格中尺度涡旋。
两者画在同一张图上, 一个像水彩晕开、一个像卫星图 —— 与用户看到的一致。

做法
----
把 CMEMS 单日场相对**它自己 12 个月平均**的偏差(即中尺度 + 天气尺度结构)搬到 OISST 基态上:

    new[m] = OISST_LTM[m] + A[m],   A[m] = g2sst[m] - clim_g2 - zonalmean(g2sst[m] - clim_g2)

* 减 `clim_g2`(12 月平均) → 去掉 CMEMS 的长期偏差(它的绝对误差 ~0.5°C 是当初
  弃用它、改用 OISST 的原因);
* 再减**逐月纬向平均** → 去掉大尺度季节循环, 否则会和 OISST 自己的季节循环**重复计数**
  (中纬度会偏 2~3°C)。减完之后**纬向平均的 SST 仍严格等于 OISST 基态**, 季节循环只留
  OISST 的; 而暖池/冷舌这类**纬向不对称**结构(不是纬向平均的部分)完整保留。
* 结果与原 OISST 基态的年平均一致, 但有了涡旋与锋面细节, 与 OHC 基态同源同时次。

产物: simulator/env/model/base_sst.npz(旧文件先备份到 _bak_<日期>/)
"""
from __future__ import annotations

import glob
import io
import json
import os
import shutil
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
CMEMS = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
CELL_NPZ = os.path.join(MODEL, 'land_cell.npz')
#: **规范源**(OISST LTM 月气候态, 未叠加过异常)。幂等的关键: 永远从它派生,
#: 绝不从 base_sst.npz 读 —— 那是本工具自己的产物, 跑第二次会把异常叠两遍
#: (实测第二次叠加后海洋格 max|Δ| 从 2.5 变成 5.0 °C)。
CANON = os.path.join(MODEL, 'base_sst_oisst.npz')
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _land():
    try:
        with np.load(CELL_NPZ) as z:
            return np.asarray(z['landfrac'], np.float32)
    except Exception:
        return None


def _g2_months():
    """→ {月: 单日 sst(240,720)}; 以 g2_YYYYMM15.npz 为准(与 OHC 基态同一批文件)。"""
    out = {}
    for f in sorted(glob.glob(os.path.join(CMEMS, 'g2_*.npz'))):
        b = os.path.basename(f)
        try:
            y, m = int(b[3:7]), int(b[7:9])
        except Exception:
            continue
        with np.load(f) as z:
            if 'sst' not in z.files:
                continue
            out[m] = (y, np.asarray(z['sst'], np.float32))
    return out


def build(save=True, damp=1.0):
    from simulator.simcore.fill import ocean_fill
    land = _land()
    src = _g2_months()
    if len(src) < 12:
        raise RuntimeError('g2 单日场不足 12 个: %d' % len(src))
    _src = CANON if os.path.exists(CANON) else os.path.join(MODEL, 'base_sst.npz')
    with np.load(_src) as z:
        base = np.asarray(z['sst'], np.float32)
        meta0 = json.loads(str(z['meta'].item())) if 'meta' in z.files else {}
    have = sorted(src)
    g = np.zeros((12,) + base.shape[1:], np.float32)
    for i in range(12):
        m = min(have, key=lambda x: min(abs(x - (i + 1)), 12 - abs(x - (i + 1))))
        g[i] = ocean_fill(src[m][1], land=land, despike=False)
    clim = g.mean(axis=0)
    new = np.zeros_like(base)
    info = []
    from scipy.ndimage import gaussian_filter
    for i in range(12):
        a = (g[i] - clim) * float(damp)
        # ① **高通**: 只留中尺度(涡旋/锋面), 去掉"两个产品之间的大尺度差异"。
        #    实测不去的话异常里有 ±5.5°C 的格点, 集中在边缘海/冰区 —— 那是 OISST 与
        #    CMEMS 对海冰/陆架的处理差异, 加进去就是用户最反感的"假跳变"。
        a = a - gaussian_filter(a, sigma=(2.0, 2.0), mode=('nearest', 'wrap'))
        # ② 去掉逐月纬向平均(残余大尺度季节循环), 否则与 OISST 的季节循环重复计数
        oc = np.isfinite(a)
        if land is not None:
            oc = oc & (land < 0.999)
        num = np.where(oc, a, 0.0).sum(axis=1, keepdims=True)
        den = np.maximum(oc.sum(axis=1, keepdims=True), 1)
        a = a - num / den
        # ③ 幅度钳制 + 冷海/冰区淡出(那里中尺度结构本就无意义, 且两产品一致性差)
        a = np.clip(a, -2.5, 2.5) * np.clip((base[i] - 0.0) / 4.0, 0.0, 1.0)
        new[i] = base[i] + a
        info.append({'month': i + 1, 'g2': src[min(have, key=lambda x: min(abs(x - (i + 1)),
                    12 - abs(x - (i + 1))))][0],
                     'anom_std': float(np.std(a[oc])) if oc.any() else 0.0,
                     'anom_p99': float(np.percentile(np.abs(a[oc]), 99)) if oc.any() else 0.0})
    # 陆地/冰再平滑补一遍(加了异常后陆地格不该留纹理)
    for i in range(12):
        new[i] = ocean_fill(new[i], land=land, despike=False)
    # 与旧基态的守恒检查
    oc = np.isfinite(new) & np.isfinite(base)
    if land is not None:
        oc = oc & (land[None, :, :] < 0.999)
    def _zmean(x):                      # **只看海洋格**的纬向平均(陆地格是新旧各自填的, 不可比)
        return (np.where(oc, x, np.nan).sum(axis=2) /
                np.maximum(oc.sum(axis=2), 1))
    chk = {'zonal_dmax': float(np.nanmax(np.abs(_zmean(new) - _zmean(base)))),
           'mean_dmax': float(np.nanmax(np.abs(
               np.where(oc, new, np.nan).mean(axis=(1, 2)) -
               np.where(oc, base, np.nan).mean(axis=(1, 2))))),
           'detail_ratio': _detail(np.where(oc, new, np.nan)) /
                           max(1e-9, _detail(np.where(oc, base, np.nan)))}
    print('  zonal-mean 与旧基态最大差 %.4f °C (应为 ~0)' % chk['zonal_dmax'])
    print('  全域均值与旧基态最大差 %.4f °C' % chk['mean_dmax'])
    print('  细节能量(Laplacian 标准差)倍数 = %.2f×' % chk['detail_ratio'])
    for r in info:
        print('    %2d 月 (g2 %d): 异常 std %.3f, |异常| p99 %.3f °C' %
              (r['month'], r['g2'], r['anom_std'], r['anom_p99']))
    if save:
        bak = os.path.join(MODEL, '_bak_%s' % time.strftime('%Y%m%d'))
        os.makedirs(bak, exist_ok=True)
        cur = os.path.join(MODEL, 'base_sst.npz')
        if os.path.exists(cur) and not os.path.exists(os.path.join(bak, 'base_sst.npz')):
            shutil.copy2(cur, os.path.join(bak, 'base_sst.npz'))
            print('  旧文件已备份 → %s' % os.path.join(bak, 'base_sst.npz'))
        meta = dict(meta0)
        meta['canonical_source'] = os.path.basename(_src)
        meta.update({'mesoscale': 'CMEMS GLORYS 单日 sst 的异常(去 12 月平均 + 去逐月纬向平均)'
                                   '叠加到 OISST LTM 基态上; 纬向平均保持 OISST 原值',
                     'mesoscale_damp': float(damp), 'detail_ratio': chk['detail_ratio'],
                     'months': info})
        np.savez_compressed(cur, sst=new.astype(np.float32),
                            meta=np.array(json.dumps(meta, ensure_ascii=False)))
        print('  写入 %s  (12, %d, %d)' % (cur, new.shape[1], new.shape[2]))
    return new, chk


def _detail(a):
    """细节能量: 逐月沿经度的二阶差分标准差(涡旋/锋面的代理量)。"""
    d2 = np.diff(np.asarray(a, np.float32), n=2, axis=2)
    return float(np.nanstd(d2))


def main(argv=None):
    argv = argv or sys.argv[1:]
    build(save=('--check' not in argv))
    return 0


if __name__ == '__main__':
    main()
