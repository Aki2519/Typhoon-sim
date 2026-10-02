# -*- coding: utf-8 -*-
"""I1: 沿观测最佳路径采样我方环境场, 建"强度变化"数据集。

这是强度优化的**基准数据集** —— 没有它, 谈"模拟得像不像"就没有标尺。

做法:
  1. 从 train_tc_clim 取合并后的轨迹(优先级 xrq > miwu > 自评 > 官评)
  2. 只保留**卫星时代**(默认 1980-2024): 之前的飞机/船舶观测严重漏检,
     把它混进分布会把强度气候态系统性拉低
  3. 按 (年, 月) 分组 —— 每个月跑**一次** env_update, 再把该月所有点采样出来。
     逐点跑 env_update 是 12 万次 × 0.3s = 10 小时, 分组后只有 540 次 ≈ 3 分钟
  4. 每个点记录: 当前 vmax / MPI / |切变| / 切变方向 / OHC(D26) / SST / t200 /
     纬度 / 移速 / 24h 后的 Δvmax(强度变化, 这是要拟合的目标)

产出: simulator/env/model/tc_intensity.npz

用法: python tools/train_tc_intensity.py [--y0 1980] [--y1 2024]
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


def _dt_of(t):
    import datetime as _dt
    return _dt.datetime(t // 1000000, (t // 10000) % 100, (t // 100) % 100, t % 100)


def build(y0=1980, y1=2024):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tclim', os.path.join(ROOT, 'tools', 'train_tc_clim.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tracks, _ = m.collect()
    print('合并轨迹 %d 条' % len(tracks), flush=True)

    # 按 (年, 月) 归组; 同时保留每条风暴的完整序列用于算 Δvmax
    groups = collections.defaultdict(list)
    storms = {}
    for key, (prio, tr) in tracks.items():
        tr = [r for r in tr if y0 <= _dt_of(r[0]).year <= y1]
        if len(tr) < 5:
            continue
        storms[key] = tr
        for idx, r in enumerate(tr):
            d = _dt_of(r[0])
            groups[(d.year, d.month)].append((key, idx))
    print('卫星时代轨迹 %d 条, 覆盖 %d 个(年,月)'
          % (len(storms), len(groups)), flush=True)

    rec = []
    for gi, ((yy, mm), items) in enumerate(sorted(groups.items())):
        try:
            sim = V.init({'start': '%04d-%02d-15 00' % (yy, mm),
                          'params': {'dataMode': True}})
            V.env_update(sim)
        except Exception as e:
            print('  %d-%02d env 失败 %s' % (yy, mm, str(e)[:70]), flush=True)
            continue
        e = sim['env']
        for key, idx in items:
            tr = storms[key]
            r = tr[idx]
            d = _dt_of(r[0])
            la, lo, vm = r[1], r[2], r[3]
            try:
                sst = V.env_bilinear(e['sst'], lo, la)
                d26 = V.env_bilinear(e['ohc'], lo, la)
                sh = V.env_bilinear(e['shear'], lo, la)
                su = (V.env_bilinear(e['sh_u'], lo, la)
                      if 'sh_u' in e else 0.0)
                sv = (V.env_bilinear(e['sh_v'], lo, la)
                      if 'sh_v' in e else 0.0)
                t2 = (V.env_bilinear(e['t200'], lo, la) + 273.15
                      if 't200' in e else np.nan)
                rh = V.env_bilinear(e['rh'], lo, la) if 'rh' in e else np.nan
                land = V.env_bilinear(e['land'], lo, la) if 'land' in e else 0.0
            except Exception:
                continue
            # 移速(kt)
            sp = np.nan
            if idx + 1 < len(tr):
                nxt = tr[idx + 1]
                try:
                    dh = (_dt_of(nxt[0]) - d).total_seconds() / 3600.0
                    if dh > 0:
                        dy = (nxt[1] - la) * 60.0
                        dx = (nxt[2] - lo) * 60.0 * np.cos(np.radians(la))
                        sp = float(np.hypot(dx, dy) / dh)
                except ValueError:
                    pass
            # 24h 后的 Δvmax(找最接近 +24h 的点)
            dv24 = np.nan
            for j in range(idx + 1, min(idx + 8, len(tr))):
                try:
                    dh = (_dt_of(tr[j][0]) - d).total_seconds() / 3600.0
                except ValueError:
                    continue
                if dh >= 22.0:
                    dv24 = tr[j][3] - vm
                    break
            mpi = V.mpi(sst, d26, 0.5, la, t2 if np.isfinite(t2) else None)
            rec.append((vm, mpi, sst, d26, sh, su, sv,
                        t2 if np.isfinite(t2) else 300.0,
                        rh if np.isfinite(rh) else 60.0, float(land),
                        la, lo, sp if np.isfinite(sp) else 0.0,
                        dv24, int(d.year), int(d.month)))
        if (gi + 1) % 60 == 0:
            print('  进度 %d/%d  样本 %d' % (gi + 1, len(groups), len(rec)),
                  flush=True)

    A = np.asarray(rec, np.float32)
    names = ('vmax', 'mpi', 'sst', 'd26', 'shear', 'sh_u', 'sh_v', 't200',
             'rh', 'land', 'lat', 'lon', 'spd', 'dv24', 'year', 'month')
    os.makedirs(MODEL, exist_ok=True)
    np.savez_compressed(os.path.join(MODEL, 'tc_intensity.npz'),
                        data=A, names=np.array(names))
    ok = np.isfinite(A[:, 13])
    print('\n-> tc_intensity.npz  样本 %d(其中有 24h Δvmax 的 %d)'
          % (A.shape[0], int(ok.sum())))
    v = A[ok]
    print('  vmax      P25 %.0f  P50 %.0f  P75 %.0f' % tuple(
        np.percentile(v[:, 0], [25, 50, 75])))
    print('  MPI       P25 %.0f  P50 %.0f  P75 %.0f' % tuple(
        np.percentile(v[:, 1], [25, 50, 75])))
    print('  V/MPI     P50 %.2f' % np.median(v[:, 0] / np.maximum(v[:, 1], 1)))
    print('  Δvmax24h  P10 %.0f  P50 %.0f  P90 %.0f  增强(>+30) 占比 %.1f%%'
          % tuple(list(np.percentile(v[:, 13], [10, 50, 90]))
                  + [float((v[:, 13] > 30).mean() * 100)]))
    return A, names


def load():
    z = np.load(os.path.join(MODEL, 'tc_intensity.npz'), allow_pickle=False)
    return z['data'], [str(x) for x in z['names']]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--y0', type=int, default=1980)
    ap.add_argument('--y1', type=int, default=2024)
    a = ap.parse_args()
    build(a.y0, a.y1)
