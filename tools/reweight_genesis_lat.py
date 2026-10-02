# -*- coding: utf-8 -*-
"""生成密度的**纬向重加权**(dry-run 默认, 只有 --apply 才写盘)。

为什么要做(证据链, 见 output/sim/_reference/ROUND_LOG.md 第 55/56 轮):
  内核的生成密度 `tc_clim['genesis']`(西太 8 月)加权平均纬度 **19.1°N**;
  用**同一批**自评 2025/2026 西太 75 个系统换口径重算:
    首报纬度(与内核"扰动起步"同口径) 中位 12.7 / 平均 13.5
    首次达 20kt                        中位 13.7 / 平均 14.2
    首次达 34kt(命名)                 中位 17.2 / 平均 16.5
  ⇒ 换口径最多解释到 16.5, 密度仍高 **2.6°**(按内核实际口径高 5.6°)。**是真偏差**。
  另有独立小偏差: 模态调制把生成再往北推 ~1.9°(西太北半模态 +0.036 / 南半 0.000)。

做法: 按洋区求"观测/模型"的**逐纬度质量比**, 平滑后作为每行(纬度)的乘性权重,
归一化后写回密度。**每个洋区单独标**, 不用全球系数。

用法:
  python tools/reweight_genesis_lat.py --check          # 只报告(dry-run, 默认)
  python tools/reweight_genesis_lat.py --apply --backup # 写回 tc_clim.npz(先备份)
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')

#: 洋区 → (观测目录(只读), 经度范围 0-360, 半球 +1 北 / -1 南)
#: **半球必须显式给** —— 第一版 SH 用 (-180,360) 当切片, 既取错经度又没排掉北半球,
#: 算出"南半球密度平均 +14.7°N"这种荒唐值(dry-run 发现的, 已修)。
BASINS = {
    'WP': (r'G:\气象\数据\自评\2025\2025wp', (100.0, 180.0), +1),
    'WP2026': (r'G:\气象\数据\自评\2026\2026wp', (100.0, 180.0), +1),
    'SH': (r'G:\气象\数据\自评\2026\2025-2026sh', (0.0, 360.0), -1),
}


def _lat_of(s):
    m = re.match(r'^(\d+)([NS])$', (s or '').strip())
    return None if not m else (int(m.group(1)) / 10.0) * (1 if m.group(2) == 'N' else -1)


def obs_latitudes(dirs, thr=20.0):
    """观测: 每个系统"首次达到 thr kt"时的纬度(与内核的 TD 门槛可比)。"""
    out = []
    for d in dirs:
        for f in glob.glob(os.path.join(d, '*.dat')) if os.path.isdir(d) else []:
            L, V = [], []
            for ln in io.open(f, encoding='utf-8', errors='replace'):
                c = [x.strip() for x in ln.split(',')]
                if len(c) >= 9:
                    la = _lat_of(c[6])
                    try:
                        v = float(c[8])
                    except Exception:
                        continue
                    if la is not None:
                        L.append(la)
                        V.append(v)
            if not L:
                continue
            L, V = np.array(L), np.array(V)
            if V.max() < thr:
                continue
            out.append(float(L[int(np.argmax(V >= thr))]))
    return np.array(out)


def check(apply=False, backup=False):
    z = np.load(os.path.join(MODEL, 'tc_clim.npz'))
    gen = np.asarray(z['genesis'], np.float32)
    lat = np.asarray(z['lat'], np.float32)
    lon = np.asarray(z['lon'], np.float32)
    info = {}
    for nm, (obs_dir, (lo, hi), hemi) in BASINS.items():
        obs = obs_latitudes([obs_dir])
        obs = obs[np.sign(obs) == hemi] if hemi else obs
        if obs.size < 5:
            print('  %-7s 观测样本不足(%d), 跳过' % (nm, obs.size))
            continue
        j0 = int(np.searchsorted(lon, lo % 360.0))
        j1 = int(np.searchsorted(lon, hi % 360.0)) if hi < 360 else lon.size
        sub = gen[:, :, j0:j1]
        rows = lat > 0 if hemi > 0 else lat < 0          # **半球筛选**
        dens = sub[:, rows, :].sum(axis=(0, 2))          # 逐纬度质量(只剩该半球)
        lat_r = lat[rows]
        dens = dens / max(dens.sum(), 1e-9)
        # 观测直方图 → 同一纬轴
        bins = np.concatenate([[lat_r[0] - 0.5], lat_r + 0.5])
        oh, _ = np.histogram(obs, bins=bins)
        oh = oh / max(oh.sum(), 1e-9)
        ratio = np.where(dens > 1e-6, oh / np.maximum(dens, 1e-9), 1.0)
        # 平滑(5 行滑动)避免把噪声放大成条纹
        k = np.ones(5) / 5.0
        rs = np.convolve(ratio, k, mode='same')
        rs = np.clip(rs, 0.25, 4.0)
        rs = rs / max(rs.mean(), 1e-9)
        wm_now = float((dens * lat_r).sum())
        wm_new = float((dens * rs * lat_r).sum() / max((dens * rs).sum(), 1e-9))
        info[nm] = {'obs_n': int(obs.size), 'obs_mean_lat': float(obs.mean()),
                    'dens_mean_lat': wm_now, 'after_rew': wm_new,
                    'factor_range': [float(rs.min()), float(rs.max())]}
        print('  %-7s 观测 n=%3d 平均 %5.1f° | 密度 %5.1f° → 重加权后 %5.1f° (系数 %.2f~%.2f)'
              % (nm, obs.size, obs.mean(), wm_now, wm_new, rs.min(), rs.max()))
        if apply:
            w = np.ones(gen.shape[1], np.float32)
            w[rows] = rs
            gen = gen * w[None, :, None]
    print()
    if not apply:
        print('(dry-run: 未写盘。应用: --apply --backup)')
        return info
    tot = gen.sum(axis=(1, 2), keepdims=True)
    gen = gen / np.maximum(tot, 1e-9)
    if backup:
        b = os.path.join(MODEL, '_bak_latrew')
        os.makedirs(b, exist_ok=True)
        dst = os.path.join(b, 'tc_clim.npz')
        if not os.path.exists(dst):
            import shutil
            shutil.copy2(os.path.join(MODEL, 'tc_clim.npz'), dst)
            print('  旧文件已备份 →', dst)
    save = {k: z[k] for k in z.files}
    save['genesis'] = gen.astype(np.float32)
    meta = json.loads(str(z['meta'].item())) if 'meta' in z.files else {}
    meta['lat_reweight'] = info
    save['meta'] = np.array(json.dumps(meta, ensure_ascii=False))
    np.savez_compressed(os.path.join(MODEL, 'tc_clim.npz'), **save)
    print('  已写回 tc_clim.npz')
    return info



def monthly_report(basin='WP'):
    """逐月对比: 观测(首次达 20kt 的纬度) vs 内核密度(该月, 该洋区, 该半球)。

    **为什么必须逐月**(第 56→57 轮的教训): 全年汇总时 WP 密度 15.0° vs 观测 15.9° 几乎无偏,
    但 8 月单月是 19.1° vs 13.5° —— 偏的是**季节循环**, 用全年系数去修会把正确的年分布推歪。
    """
    obs_dir, (lo, hi), hemi = BASINS[basin]
    z = np.load(os.path.join(MODEL, 'tc_clim.npz'))
    gen = np.asarray(z['genesis'], np.float32)
    lat = np.asarray(z['lat'], np.float32)
    lon = np.asarray(z['lon'], np.float32)
    obs = obs_latitudes([obs_dir])
    obs = obs[np.sign(obs) == hemi] if hemi else obs
    j0 = int(np.searchsorted(lon, lo % 360.0))
    j1 = int(np.searchsorted(lon, hi % 360.0)) if hi < 360 else lon.size
    rows = lat > 0 if hemi > 0 else lat < 0
    lat_r = lat[rows]
    # 观测: 用文件的月(从首报时间戳取月)
    omon = []
    for f in glob.glob(os.path.join(obs_dir, '*.dat')) if os.path.isdir(obs_dir) else []:
        L, V, T = [], [], None
        for ln in io.open(f, encoding='utf-8', errors='replace'):
            c = [x.strip() for x in ln.split(',')]
            if len(c) >= 9:
                if T is None and len(c[2]) == 10:
                    T = c[2][4:6]
                la = _lat_of(c[6])
                try:
                    v = float(c[8])
                except Exception:
                    continue
                if la is not None:
                    L.append(la); V.append(v)
        if L and V and max(V) >= 20:
            omon.append((T, L[int(np.argmax(np.array(V) >= 20))]))
    print('  %s 逐月(观测 n / 观测平均纬 / 密度平均纬):' % basin)
    print('   月   n   观测    密度    差')
    for m in range(1, 13):
        mm = '%02d' % m
        ov = [x for t, x in omon if t == mm and np.sign(x) == hemi]
        dens = gen[m - 1][rows, :][:, j0:j1].sum(axis=1)   # (lat,) 该月该洋区该半球
        dm = float((dens * lat_r).sum() / max(dens.sum(), 1e-9))
        if ov:
            print('   %2d  %3d  %5.1f  %5.1f  %+5.1f' % (m, len(ov), np.mean(ov), dm, np.mean(ov) - dm))
        else:
            print('   %2d    -      -   %5.1f' % (m, dm))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--backup', action='store_true')
    ap.add_argument('--monthly', default=None, help='逐月报告, 如 WP')
    a = ap.parse_args(argv)
    if a.monthly:
        monthly_report(a.monthly)
        return 0
    check(apply=a.apply, backup=a.backup)
    return 0


if __name__ == '__main__':
    main()
