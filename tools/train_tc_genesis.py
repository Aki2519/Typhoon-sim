# -*- coding: utf-8 -*-
"""T2-a: 用观测标定**生成的环境依赖** —— 生成点 vs 可用海域的对比。

为什么必须重标: core.gen_check 里的 GPI 因子
    f1=clamp((SST-26.5)/2.5)  f2=clamp((19.4-SHEAR)/15.6)  f3=clamp((RH-55)/25)
是按**旧的合成环境场**拍的(旧切变最大 24.5kt、多数 <15)。换成真实 NCEP 切变后
TC 海域均值就有 20.5kt, f2 几乎恒为 0 —— 实测 90 天只生成 1 个台风(应有 ~21 个)。

做法(标准的"病例-对照"):
  病例 = 观测的**生成点**(每条命名风暴的首点)
  对照 = 同月、同纬度带内**所有海洋格点**
  对每个环境变量分箱, 算 P(生成|变量分箱) 与基准率的比 → 这就是该变量的
  相对权重曲线, 直接换成新的 f1/f2/f3/f4。

产出: simulator/env/model/tc_genesis.npz(各变量的分箱中心与相对权重)
用法: python tools/train_tc_genesis.py [--y0 1980] [--y1 2024]
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
from simulator.simcore import core as V                      # noqa: E402

#: TC 生成带(做对照时只取这个范围, 否则极地/中纬的冷水面会把基准率稀释)
#: 只取生成带。取 40 会把副热带急流圈进来 —— 实测对照样本里 48.8% 的切变
#: 都 >38kt, 把相对权重曲线整个压平。
BELT_LAT = 30.0
BINS = {
    'sst':   np.arange(24.0, 31.01, 0.5),
    'shear': np.arange(0.0, 41.0, 2.5),
    'rh':    np.arange(20.0, 91.0, 5.0),
    'lat':   np.arange(0.0, 31.0, 2.0),
}


def _dt_of(t):
    import datetime as _dt
    return _dt.datetime(t // 1000000, (t // 10000) % 100, (t // 100) % 100, t % 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--y0', type=int, default=1980)
    ap.add_argument('--y1', type=int, default=2024)
    a = ap.parse_args()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tclim', os.path.join(ROOT, 'tools', 'train_tc_clim.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tracks, _ = m.collect()

    # 生成点归到 (年,月)
    gen = collections.defaultdict(list)
    n_storm = 0
    for key, (prio, tr) in tracks.items():
        named = [r for r in tr if r[3] >= 34.0]
        if not named:
            continue
        r = named[0]
        d = _dt_of(r[0])
        if not (a.y0 <= d.year <= a.y1):
            continue
        gen[(d.year, d.month)].append((r[1], r[2]))
        n_storm += 1
    print('生成点 %d 个, 覆盖 %d 个(年,月)' % (n_storm, len(gen)), flush=True)

    case = collections.defaultdict(list)         # 变量 → 病例值
    ctrl = collections.defaultdict(list)         # 变量 → 对照值
    glat = -59.75 + np.arange(V.ENVNY) * V.C['ENVD']
    glon = 0.25 + np.arange(V.ENVNX) * V.C['ENVD']
    belt = np.abs(glat) <= BELT_LAT
    for gi, ((yy, mm), pts) in enumerate(sorted(gen.items())):
        try:
            sim = V.init({'start': '%04d-%02d-15 00' % (yy, mm),
                          'params': {'dataMode': True}})
            V.env_update(sim)
        except Exception:
            continue
        e = sim['env']
        land = np.asarray(e.get('land', np.zeros_like(e['sst'])))
        ocean = belt[:, None] & (land < 0.3)
        jj, ii = np.nonzero(ocean)
        # 对照必须按 **cos(lat) 加权** —— 经纬网格里高纬格点的实际面积小得多,
        # 均匀抽格点会让高纬被过度代表(和急流圈一起把曲线压平)。
        wgt = np.cos(np.radians(glat[jj]))
        wgt = wgt / wgt.sum()
        n_sel = min(3000, jj.size)
        sel = np.random.default_rng(20260917 + gi).choice(
            jj.size, size=n_sel, replace=False, p=wgt)
        ctrl['sst'].append(np.asarray(e['sst'])[jj[sel], ii[sel]])
        ctrl['shear'].append(np.asarray(e['shear'])[jj[sel], ii[sel]])
        ctrl['rh'].append(np.asarray(e['rh'])[jj[sel], ii[sel]])
        ctrl['lat'].append(np.abs(glat)[jj[sel]])
        # 病例
        for la, lo in pts:
            try:
                case['sst'].append(V.env_bilinear(e['sst'], lo, la))
                case['shear'].append(V.env_bilinear(e['shear'], lo, la))
                case['rh'].append(V.env_bilinear(e['rh'], lo, la))
                case['lat'].append(abs(la))
            except Exception:
                pass
        if (gi + 1) % 80 == 0:
            print('  进度 %d/%d  病例 %d' % (gi + 1, len(gen),
                                             len(case['sst'])), flush=True)

    out = {}
    print('\n%-8s %-16s %10s %10s %10s' % ('变量', '分箱', '病例占比', '对照占比', '相对权重'))
    for k, edges in BINS.items():
        c = np.asarray(case[k], float)
        q = np.concatenate(ctrl[k]).astype(float)
        c, q = c[np.isfinite(c)], q[np.isfinite(q)]
        idx = np.clip(np.digitize(c, edges) - 1, 0, len(edges) - 2)
        jdx = np.clip(np.digitize(q, edges) - 1, 0, len(edges) - 2)
        pc = np.bincount(idx, minlength=len(edges) - 1).astype(float)
        pq = np.bincount(jdx, minlength=len(edges) - 1).astype(float)
        pc /= max(1.0, pc.sum())
        pq /= max(1.0, pq.sum())
        # 相对权重 = (病例在该箱的占比) / (对照在该箱的占比), 归一到最大 1
        w = pc / np.maximum(pq, 1e-9)
        w = w / max(1e-9, w.max())
        ctr = 0.5 * (edges[:-1] + edges[1:])
        out[k + '_ctr'] = ctr.astype(np.float32)
        out[k + '_w'] = w.astype(np.float32)
        out[k + '_pc'] = pc.astype(np.float32)
        out[k + '_pq'] = pq.astype(np.float32)
        print('  %-8s' % k)
        for i in range(len(ctr)):
            if pq[i] < 1e-4 and pc[i] < 1e-4:
                continue
            print('    %-16s %9.3f%% %9.3f%% %10.2f'
                  % ('%.1f' % ctr[i], pc[i] * 100, pq[i] * 100, w[i]))
    np.savez_compressed(os.path.join(MODEL, 'tc_genesis.npz'), **out)
    print('\n-> tc_genesis.npz')


if __name__ == '__main__':
    main()
