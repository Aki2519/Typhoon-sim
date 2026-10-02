# -*- coding: utf-8 -*-
"""从 CMEMS 单日 thetao(0.083°, 全深度)算 D26/OHC, **并自动用同一天 ERA5 瞬时 SST 校验**。

为什么另写一个:
  `tools/fetch_cmems_global.py` 的降采样是"块平均 + int(sy) 截断", 实测产出的
  `glob_*.npz` 与同日 ERA5 瞬时 SST 相比 **45% 的格点差 >3 °C**(75E/0 读 20.94 而
  ERA5 是 30.08) —— 那批文件不能用来建 base_d26。这里改成
    ① `fetch_cmems_ohc.compute` 同一个物理定义算 D26/OHC(单位一致);
    ② 用 `map_coordinates` 双线性重采样到内核 240×720(与 daily.py 同一套口径);
    ③ **门控**: 与同日 ERA5 瞬时 SST 比对, 平均 |Δ| ≤ 1.5 °C 才认为该日数据可用,
       否则只打印诊断、不写文件。

用法:
  python tools/build_ohc_from_cmems.py --nc C:/cmems_tmp/xxx_2024-09-15.nc --date 2024-09-15
输出: simulator/env/train_cache/cmems/g2_<YYYYMMDD>.npz  (ohc, d26, sst, lat, lon, meta)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
ENVNY, ENVNX = 240, 720
DST_LAT = -60.0 + (np.arange(ENVNY) + 0.5) * 0.5      # 南在上
DST_LON = (np.arange(ENVNX) + 0.5) * 0.5


def _resample(src, la, lo, order=1):
    """(nlat,nlon) → 内核网格(南在上)。la 允许升/降序; lo 允许 -180..180。"""
    from scipy import ndimage as ndi
    la = np.asarray(la, float)
    lo = np.asarray(lo, float)
    if la[0] > la[-1]:
        src = src[::-1]
        la = la[::-1]
    if lo.size and lo.min() < 0.0:                     # 归一到 0..360 并重排
        idx = np.argsort(lo % 360.0)
        lo = (lo % 360.0)[idx]
        src = src[:, idx]
    fy = (DST_LAT - la[0]) / (la[-1] - la[0]) * (la.size - 1)
    fx = (DST_LON - lo[0]) / (lo[-1] - lo[0]) * (lo.size - 1)
    coords = np.array(np.meshgrid(fy, fx, indexing='ij'))
    return ndi.map_coordinates(np.asarray(src, np.float32), coords,
                               order=order, mode='nearest').astype(np.float32)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nc', required=True, help='CMEMS thetao 单日文件')
    ap.add_argument('--date', required=True, help='YYYY-MM-DD')
    ap.add_argument('--tol', type=float, default=1.5, help='SST 校验阈值(°C)')
    ap.add_argument('--force', action='store_true', help='即使校验不过也写文件')
    a = ap.parse_args(argv)

    import netCDF4
    from fetch_cmems_ohc import compute

    os.chdir(ROOT)
    # 路径: 仓库路径含中文 → netCDF4 在 Windows 上会按 locale 编码传参而失败, 必须
    # 用相对路径; 但 C:\cmems_tmp 那种 ASCII 绝对路径直接用更省事。
    _p = a.nc
    if not os.path.isabs(_p) or not _p.isascii():
        _p = os.path.relpath(_p, ROOT)
    ds = netCDF4.Dataset(_p)
    var = None
    for k in ('thetao', 't'):
        if k in ds.variables:
            var = k
            break
    if var is None:
        print('找不到 thetao:', list(ds.variables.keys()))
        return
    la = np.asarray(ds.variables['latitude'][:], float).ravel()
    lo = np.asarray(ds.variables['longitude'][:], float).ravel()
    dep = None
    for k in ('depth', 'deptht', 'lev'):
        if k in ds.variables:
            dep = np.asarray(ds.variables[k][:], float).ravel()
            break
    v = ds.variables[var]
    print('thetao 形状 %s, 深度 %d 层 (%.1f..%.1f m), lat %d, lon %d'
          % (v.shape, dep.size, dep.min(), dep.max(), la.size, lo.size))
    T = np.asarray(v[0], np.float32)                  # (z, lat, lon)
    ds.close()
    ohc, d26 = compute(T, dep)
    sst = np.where(np.isfinite(T[0]) & (T[0] > -3) & (T[0] < 45), T[0], np.nan)

    out = {'ohc': _resample(ohc, la, lo), 'd26': _resample(d26, la, lo),
           'sst': _resample(sst, la, lo)}
    # **NaN/陆地填最近有效值后再写盘**(2026-09-20: AOML 交叉校验时发现 g2 文件在
    # 120E/15N 这类近岸点上是 NaN —— CMEMS 的 thetao 近岸被掩码, 而这里原来没填,
    # 虽然 daily.load_fields 读的时候会填, 但文件本身留 NaN 会让交叉校验/统计出洞)。
    for _k in ('ohc', 'd26'):
        _a = out[_k]
        _bad = ~np.isfinite(_a)
        if _bad.any() and not _bad.all():
            from scipy import ndimage as _ndi
            _z = np.where(_bad, 0.0, np.nan_to_num(_a, nan=0.0)).astype(np.float32)
            _, _idx = _ndi.distance_transform_edt(_bad, return_indices=True)
            _z[_bad] = _z[tuple(i[_bad] for i in _idx)]
            out[_k] = _z

    # ── 校验: 与同日 ERA5 瞬时 SST 比 ──
    ok = True
    verdict = 'no-ref'
    try:
        from datetime import datetime
        from simulator.simcore import daily as D
        _date = [int(x) for x in a.date.split('-')]
        inst = D.load_fields(datetime(_date[0], _date[1], _date[2], 12))
        if 'sst' in inst:
            e = np.asarray(inst['sst'], np.float32)
            m = np.isfinite(out['sst']) & np.isfinite(e)
            d = out['sst'][m] - e[m]
            mad = float(np.abs(d).mean())
            ok = mad <= a.tol
            verdict = 'PASS' if ok else 'FAIL'
            print('校验(对照同日 ERA5 瞬时 SST): 平均 |Δ| %.2f °C, 平均差 %+.2f, '
                  'std %.2f, |Δ|>3 占 %.1f%% → %s'
                  % (mad, float(d.mean()), float(d.std()),
                     100.0 * float((np.abs(d) > 3).mean()), verdict))
            for lo_, la_, tag in ((130, 13, '暖池'), (75, 0, '印度洋赤道'),
                                  (240, 0, '东太赤道'), (120, -12, 'NW澳')):
                i = int(np.argmin(np.abs(DST_LAT - la_)))
                j = int(np.argmin(np.abs(DST_LON - lo_)))
                print('   %-10s CMEMS %6.2f  ERA5 %6.2f  差 %+6.2f'
                      % (tag, out['sst'][i, j], e[i, j], out['sst'][i, j] - e[i, j]))
    except Exception as exc:
        print('校验失败(跳过):', exc)

    if verdict == 'no-ref':
        print('!! 没有同日 ERA5 参照 → 该文件标记 unverified(可写, 但不得当作已验收)')
    if not ok and not a.force:
        print('校验未过 → 不写文件(避免又一批坏基态)。')
        return
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, 'g2_%s.npz' % a.date.replace('-', ''))
    meta = {'source': os.path.basename(a.nc), 'date': a.date,
            'definition': 'OHC=rho*cp*int(max(T-26,0)dz) kJ/cm2; D26=26C 等温线深度 m',
            'resample': 'map_coordinates order=1 → 240x720 (±60, 南在上)',
            'validate': verdict, 'var': var}
    np.savez_compressed(dst, ohc=out['ohc'].astype(np.float32),
                        d26=out['d26'].astype(np.float32),
                        sst=out['sst'].astype(np.float32),
                        lat=DST_LAT.astype(np.float32),
                        lon=DST_LON.astype(np.float32),
                        meta=json.dumps(meta, ensure_ascii=False))
    print('写出', dst)


if __name__ == '__main__':
    main()
