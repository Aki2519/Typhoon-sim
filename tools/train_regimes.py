# -*- coding: utf-8 -*-
"""阶段 3:环流型(regimes)—— 从逐日 Z500 提取真实的低频环流型。

## 为什么用环流型而不是"半月快照"

用户最初的直觉("环流整体维持一段再转")是对的, 但**半个月是人为尺度**:
真实的低频变率是 10-60 天的**连续谱**。用半月阶梯就要拍一个"进动"的 ω,
而且段间怎么过渡也是人为的。

环流型方案把这件事交给数据:
  - 型是**真实的**(Z500 异常的 EOF)
  - 持续性是真的(PC 序列本身有红谱自相关)
  - 日期是准的(1979-2026 逐日 PC 都是实测)
  - 没有任何人为时间尺度

## 步骤

1. 逐日 Z500(1979-2026, 2.5°, 60S-60N)→ 去季节循环 → 异常场
2. **低通**(15 天滑动)→ 只保留 10-60 天以上的低频部分
3. EOF 分解 → 前 N 个型
4. PC 序列按日存档(实测)
5. 把**已有的月均风场/SST** 对月均 PC 回归 → 每个型的空间响应型

产物: simulator/env/model/regimes.npz
    eof    (n_r, nlat, nlon)      型(Z500 异常, gpm)
    pc     (nday, n_r)            逐日 PC
    date   (nday,)                YYYYMMDD
    lat/lon
    resp_<var> (n_r, nlat_o, nlon_o)  响应型(1° 网格, 与 base_* 同网格)
    resp_names
用法: python tools/train_regimes.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
N_EOF = 6                 # 取前 6 个环流型
LP_DAYS = 15              # 低通窗长(天)
LAT0, ENVD = -60.0, 0.5


def _lowpass_time(a, n):
    """沿时间轴做 n 天滑动平均(用累积和, 边界用 edge padding)。"""
    if n <= 1:
        return a
    pad = n // 2
    ap = np.concatenate([np.repeat(a[:1], pad, 0), a,
                         np.repeat(a[-1:], pad, 0)], 0)
    c = np.cumsum(ap, 0)
    c = np.concatenate([np.zeros((1,) + a.shape[1:], a.dtype), c], 0)
    return (c[n:] - c[:-n]) / float(n)


def main():
    z = np.load(os.path.join(CACHE, 'z500_daily.npz'))
    hgt = np.asarray(z['hgt'], np.float64)          # (nday, nlat, nlon)
    date = np.asarray(z['date'], np.int64)
    lat = np.asarray(z['lat'], np.float64)
    lon = np.asarray(z['lon'], np.float64)
    nd, ny, nx = hgt.shape
    print('Z500 %s  经纬 %dx%d  %d-%d' %
          (hgt.shape, ny, nx, date[0] // 10000, date[-1] // 10000), flush=True)

    # 缺测(有限值 _FillValue 已被量程挡掉)用该点的气候均值补
    bad = ~np.isfinite(hgt)
    if bad.any():
        print('  缺测 %d 点(%.2f%%), 用逐日气候态补' % (bad.sum(), bad.mean() * 100),
              flush=True)
        doy = np.array([(d % 10000) // 100 * 100 + d % 100 for d in date])
        for j in range(ny):
            for i in range(nx):
                col = hgt[:, j, i]
                m = np.isfinite(col)
                if m.all():
                    continue
                hgt[~m, j, i] = np.interp(doy[~m], doy[m], col[m])

    # ── 去季节循环 ──
    doy = np.array([(d % 10000) // 100 * 100 + d % 100 for d in date])
    uq = np.unique(doy)
    clim = np.zeros((len(uq), ny, nx), np.float32)
    pos = {d: k for k, d in enumerate(uq)}
    idx = np.array([pos[d] for d in doy])
    cnt = np.bincount(idx, minlength=len(uq)).astype(np.float32)
    for k in range(ny):
        np.add.at(clim[:, k, :], idx, hgt[:, k, :])
    clim /= np.maximum(cnt[:, None, None], 1.0)
    anom = hgt - clim[idx]
    print('  季节循环已去除', flush=True)

    # ── 低通(只保留 10-60 天以上的低频) ──
    anom = _lowpass_time(anom, LP_DAYS)
    # 纬度加权(面积), 否则 EOF 会被高纬主导
    wgt = np.sqrt(np.cos(np.radians(lat)))[None, :, None]
    A = (anom * wgt).reshape(nd, -1)
    # 去掉时间均值
    A -= A.mean(0, keepdims=True)
    print('  做 EOF(SVD, %d x %d)...' % A.shape, flush=True)
    # 只算前 N_EOF 个: 用协方差矩阵的特征分解(比全 SVD 快)
    Cm = (A.T @ A) / max(1, nd - 1)
    if Cm.shape[0] > 6000:
        # 维度太高, 改用随机化 SVD 的近似: 直接对 A 做 economy SVD
        U, S, Vt = np.linalg.svd(A, full_matrices=False)
        eof = Vt[:N_EOF].reshape(N_EOF, ny, nx)
        pc = (U[:, :N_EOF] * S[:N_EOF])
        var = (S ** 2) / (S ** 2).sum()
    else:
        ev, evec = np.linalg.eigh(Cm)
        o = np.argsort(ev)[::-1][:N_EOF]
        eof = evec[:, o].T.reshape(N_EOF, ny, nx)
        pc = A @ evec[:, o]
        var = ev[o] / ev.sum()
    # 归一化 EOF(单位范数)并把量级放到 PC 上
    for k in range(N_EOF):
        nrm = np.sqrt((eof[k] ** 2).mean())
        if nrm > 0:
            eof[k] /= nrm
            pc[:, k] *= nrm
    print('  前 %d 个型解释方差: %s (累计 %.1f%%)' %
          (N_EOF, np.round(var[:N_EOF] * 100, 1), var[:N_EOF].sum() * 100),
          flush=True)

    out = {'eof': eof.astype(np.float32), 'pc': pc.astype(np.float32),
           'date': date, 'lat': lat.astype(np.float32),
           'lon': lon.astype(np.float32),
           'var': var[:N_EOF].astype(np.float32)}

    # ── 响应型: **逐月实测场**对月均 PC 回归 ──
    #
    # **不能用 base_wind.npz**: 那是 12 个月的气候态(同一个月每年都一样),
    # 年际方差为零 → 回归系数必然是 0(实测响应型量级只有 0.18 kt)。
    # 必须用有年际变化的逐月实测场 —— train_cache/ncep_{uwnd,vwnd}_lv.npz
    # 有 1991-2020 共 360 个月, 正好够。
    try:
        res = {}
        for tag, fn in (('u', 'ncep_uwnd_lv.npz'), ('v', 'ncep_vwnd_lv.npz')):
            fp = os.path.join(CACHE, fn)
            if not os.path.exists(fp):
                continue
            zz = np.load(fp)
            for key in zz.files:
                if not key.startswith(tag + 'wnd'):
                    continue
                arr = np.asarray(zz[key], np.float32)         # (nmon, nlat, nlon)
                if arr.ndim != 3 or 'year' not in zz.files:
                    continue
                ym = np.asarray(zz['year'], np.int64) * 100 + np.asarray(zz['month'], np.int64)
                # 与逐日 PC 对齐: 每个 (年,月) 取该月 PC 的均值
                pc_day = np.asarray(out['pc'], np.float64)
                dym = np.array([d // 100 for d in date], np.int64)
                pcm = np.zeros((len(ym), N_EOF), np.float64)
                okr = np.zeros(len(ym), bool)
                for k, y in enumerate(ym):
                    sel = dym == y
                    if sel.sum() >= 20:
                        pcm[k] = pc_day[sel].mean(0)
                        okr[k] = True
                if okr.sum() < 60:
                    continue
                X = pcm[okr]
                X = X - X.mean(0, keepdims=True)
                Y = arr[okr].reshape(int(okr.sum()), -1).astype(np.float64)
                Y = Y - Y.mean(0, keepdims=True)
                B, *_ = np.linalg.lstsq(X, Y, rcond=1e-3)
                res[key] = B.reshape(N_EOF, *arr.shape[1:]).astype(np.float32)
        if res:
            names = []
            for k, v in res.items():
                out['resp_' + k] = v
                names.append(k)
            out['resp_names'] = np.array(names)
            print('  响应型: %s  (回归样本 %d 个月)' % (names, int(okr.sum())), flush=True)
        else:
            print('  响应型: 没有可用的逐月实测场', flush=True)
    except Exception as e:
        print('  响应型回归失败(不阻塞): %s' % str(e)[:100], flush=True)

    o = os.path.join(MODEL, 'regimes.npz')
    np.savez_compressed(o, **out)
    print('-> %s' % o, flush=True)


if __name__ == '__main__':
    main()
