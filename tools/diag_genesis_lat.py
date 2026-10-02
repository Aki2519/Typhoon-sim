# -*- coding: utf-8 -*-
"""**离线拆解"生成纬度偏北 4-7 度"的成因**(轮次 129, 不需要跑模拟)。

轮次 128 实测(同月同海盆, 首报纬度中位): 模型比观测偏北 **+4.4 ~ +6.8 度**, 每个月都偏。
生成点的抽样链是两段:

    ① 从密度场抽点:  d = dens[month]           (+ 可选的 0.5*d + 0.5*模态场 混合)
    ② 环境闸门:      rng() < _gi_weight(C, env, lat, lon)  ← 四因子连乘

所以偏北只可能来自三处: **密度场本身**、**模态混合**(把 5 度分箱的场等权混进来)、
**环境闸门**(四因子在高纬更有利)。本工具把三段各自的**纬度分布**单独算出来:

    · dens 原始(1 度)                  -> 纬度中位 + 各纬度带的份额
    · 混合后(代码同款 0.5/0.5)          -> 与原始的差
    · 闸门 w(lat) 的纬度剖面(按经度带平均) -> 在中位纬度上是 >1 还是 <1
    · **有效接受分布** = 归一化(d x w)    -> 它的纬度中位, 与观测(10.6-12.9 度)比

⚠ 口径: 这里算的是**抽样分布**, 不是"最终落盘的生成纬度" —— 后者还经过
`land>0.3 跳过` 与"同一步只生成一个"等筛选。所以本工具回答的是"**偏北是不是抽样链造成的**",
最后一锤仍要靠落盘产物(轮次 128 的表)。

用法:
    python tools/diag_genesis_lat.py --month 7
    python tools/diag_genesis_lat.py --month 7 --lon-band 100 180
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', type=int, default=7)
    ap.add_argument('--lon-band', type=float, nargs=2, default=(100.0, 180.0))
    ap.add_argument('--npz', default=None,
                    help='可选: 用真实观测的生成纬度分布做对照(txt, 每行一个纬度)')
    a = ap.parse_args()

    from simulator.simcore import core as V
    C = V._gen_tables()
    dens = C.get('dens')
    if dens is None:
        print('没有密度场(C["dens"] 为空) —— 该配置不会走密度抽样分支')
        return 1
    lat = np.asarray(C['dens_lat'], float)
    lon = np.asarray(C['dens_lon'], float)
    d0 = np.array(dens[a.month - 1], float)
    print('密度场形状 %s | 纬度轴 %.1f..%.1f (n=%d) | 经度轴 %.1f..%.1f (n=%d)'
          % (d0.shape, lat[0], lat[-1], lat.size, lon[0], lon[-1], lon.size))

    # ── ① 原始密度场(1 度)的纬度分布 ──
    def prof(dd, tag):
        p = dd.sum(axis=1)
        p = p / max(1e-30, p.sum())
        med = float(lat[np.searchsorted(np.cumsum(p), 0.5)])
        print('  %-22s 纬度中位 %6.1f | 0-10 %.1f%% 10-20 %.1f%% 20-30 %.1f%% '
              '30-45 %.1f%%' % (
                  tag, med,
                  100 * p[(lat >= 0) & (lat < 10)].sum(),
                  100 * p[(lat >= 10) & (lat < 20)].sum(),
                  100 * p[(lat >= 20) & (lat < 30)].sum(),
                  100 * p[(lat >= 30) & (lat < 45)].sum()))
        return p, med

    print('① 密度场:')
    p_raw, med_raw = prof(d0, 'dens 原始')

    # ── ② 模态混合(与 gen_check 同款: 0.5*d + 0.5*上采样模态场) ──
    d_b = d0.copy()
    try:
        if C.get('modes') is not None:
            _cl, _bs = C['modes']
            _ny, _nx = d_b.shape
            _by, _bx = _cl.shape[1], _cl.shape[2]
            _sy, _sx = _ny // _by, _nx // _bx
            _m = _cl[a.month - 1].copy()
            for _nm, _b in _bs.items():
                _m += _b[a.month - 1] * 0.0        # 相位取 0(中性)做形状对比
            _m = np.maximum(_m, 0.0)
            if _m.sum() > 0 and _sy > 0 and _sx > 0:
                _mw = np.repeat(np.repeat(_m / _m.sum(), _sy, 0), _sx, 1)[:_ny, :_nx]
                d_b = 0.5 * d_b + 0.5 * _mw
        else:
            print('  (该配置没有模态场, 跳过混合对比)')
    except Exception as exc:
        print('  模态混合复算失败: %s' % exc)
    p_bl, med_bl = prof(np.maximum(d_b, 0.0), '混合后 0.5/0.5')

    # ── ③ 环境闸门 w(lat): 在 100-180E 上按纬度平均 ──
    sim = V.init({'start': '2025-%02d-15 00' % a.month, 'seed': 7, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True}})
    V.env_update(sim)
    C['_month'] = a.month
    j0 = int(np.searchsorted(lat, 0.0))
    rows = []
    for j in range(j0, min(j0 + 90, lat.size)):
        la = float(lat[j])
        ws = []
        for lo in np.arange(a.lon_band[0], a.lon_band[1], 5.0):
            env = V.sample_env(sim, float(lo % 360.0), la)
            ws.append(float(V._gi_weight(C, env, la, float(lo % 360.0))))
        rows.append((la, float(np.mean(ws))))
    w_lat = np.array([r[0] for r in rows])
    w_val = np.array([r[1] for r in rows])
    print('③ 环境闸门 w(lat) (经度 %.0f-%.0fE 平均):' % tuple(a.lon_band))
    for k in range(0, len(rows), 8):
        print('    %4.0fN  w=%.3f' % (w_lat[k], w_val[k]))

    # ── ④ 有效接受分布 = 归一化(混合密度 x 闸门) ──
    W = np.zeros_like(lat)
    W[j0:j0 + len(w_val)] = w_val
    eff = np.maximum(d_b, 0.0) * W[:, None]
    p_eff, med_eff = prof(eff, '有效接受 d x w')
    print()
    print('小结: 原始密度中位 %.1f | 混合后 %.1f | **有效接受 %.1f**' %
          (med_raw, med_bl, med_eff))
    print('      (观测的生成纬中位是 **10.6-12.9 度**, 各月 7-15 度)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
