# -*- coding: utf-8 -*-
"""从 CMEMS 三维温度算 OHC(kJ/cm²) 与 D26(m)—— 重建 OHC/D26 基态用。

CMEMS **没有现成的格点 OHC 产品**(全量 307 个产品里只有 OMI_CLIMATE_OHC_* 那种
区域平均异常曲线)。你图上那种地图是算出来的, 定义:
    OHC = ρ·cp·∫(T − 26℃) dz      只积 T>26 的部分
    D26 = 26℃ 等温线深度(线性插值到 T=26)
ρ=1025 kg/m³, cp=3985 J/(kg·K); J/m² → kJ/cm² 乘 1e-7。

数据源(按年份自动选):
  再分析 1993-2026  GLOBAL_MULTIYEAR_PHY_001_030     cmems_mod_glo_phy_my_0.083deg_P1D-m
  NRT 2021-今      GLOBAL_ANALYSISFORECAST_PHY_001_024 cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m

三个必须绕开的坑(实测):
  1. 凭证文件必须是 base64(INI 带 [credentials] 段), 见 README 说明
  2. thetao 的 _FillValue 是 **有限值** 9.97e36 —— isfinite 拦不住, 必须按物理范围卡
  3. cm.subset(output_filename=<含中文的路径>) 会**静默失败** —— 一律先写 ASCII 临时目录再搬

用法(Windows Python):
  python tools/fetch_cmems_ohc.py --dates 1997-09-15,2026-09-13 --basins wpac,epac,natl
输出: simulator/env/train_cache/cmems/ohc_<YYYYMMDD>_<basin>.npz  (ohc, d26, lon, lat)
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
TMP = r'C:\cmems_tmp'                     # 必须是纯 ASCII 路径(见坑 3)
RHO, CP = 1025.0, 3985.0
RHO_CP = RHO * CP
MAXDEPTH = 320.0
BASINS = {'wpac': (100.0, 180.0, 0.0, 50.0),
          'epac': (-140.0, -80.0, 0.0, 35.0),
          'natl': (-100.0, -10.0, 0.0, 55.0),
          'nino': (-180.0, -80.0, -20.0, 20.0)}
MY = 'cmems_mod_glo_phy_my_0.083deg_P1D-m'        # GLORYS12V1, 1993-01 起
NRT = 'cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m'


def layer_thickness(dep):
    e = np.empty(dep.size + 1)
    e[0] = 0.0
    e[1:-1] = 0.5 * (dep[:-1] + dep[1:])
    e[-1] = dep[-1] + (dep[-1] - e[-2])
    return np.diff(e)


def compute(T, dep):
    """(nz, nlat, nlon) 温度 → OHC(kJ/cm²), D26(m)。"""
    ok = np.isfinite(T) & (T > -3.0) & (T < 45.0)     # 坑 2: 卡物理范围
    dz = layer_thickness(np.asarray(dep, float))
    Tc = np.where(ok, T, np.nan)
    ohc = np.nansum(np.where(ok, np.maximum(Tc - 26.0, 0.0), 0.0)
                    * dz[:, None, None], axis=0) * RHO_CP / 1.0e7
    # D26: 从上往下找第一处 T<26, 在两层间线性插值
    nz = Tc.shape[0]
    below = (Tc < 26.0) & ok
    first = np.argmax(below, axis=0)
    has = below.any(0)
    j = np.arange(Tc.shape[1])[:, None]
    k = np.arange(Tc.shape[2])[None, :]
    zz = np.asarray(dep, float)[first]
    z0 = np.asarray(dep, float)[np.maximum(first - 1, 0)]
    t1 = Tc[first, j, k]
    t0 = Tc[np.maximum(first - 1, 0), j, k]
    frac = np.where(np.abs(t1 - t0) > 1e-6, (26.0 - t0) / (t1 - t0), 0.0)
    d26 = np.where(has & (first > 0), z0 + frac * (zz - z0), np.nan)
    d26 = np.where(has & (first == 0), 0.0, d26)
    surf = np.max(np.isfinite(Tc), axis=0)
    ohc = np.where(surf, np.nan_to_num(ohc), np.nan)
    return ohc.astype(np.float32), d26.astype(np.float32)


def fetch(date, basin):
    import copernicusmarine as cm
    lo0, lo1, la0, la1 = BASINS[basin]
    tag = date.replace('-', '') + '_' + basin
    dst = os.path.join(OUT, 'ohc_%s.npz' % tag)
    if os.path.exists(dst):
        print('已缓存', os.path.basename(dst), flush=True)
        return dst
    ds = MY if int(date[:4]) < 2021 else NRT
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(TMP, '*.nc')):
        os.remove(f)                                   # 清掉上一轮, 便于按名字找
    print('[抓取] %s %s %s' % (basin, date, ds), flush=True)
    cm.subset(dataset_id=ds, variables=['thetao'],
              minimum_longitude=lo0, maximum_longitude=lo1,
              minimum_latitude=la0, maximum_latitude=la1,
              start_datetime=date + 'T00:00:00', end_datetime=date + 'T00:00:00',
              minimum_depth=0.0, maximum_depth=MAXDEPTH,
              output_directory=TMP, overwrite=True, disable_progress_bar=True)
    hits = glob.glob(os.path.join(TMP, '*thetao*.nc'))
    if not hits:
        print('  !! 没拿到文件', flush=True)
        return None
    import netCDF4
    p = hits[0]
    cwd = os.getcwd()
    os.chdir(os.path.dirname(p))
    d = netCDF4.Dataset(os.path.basename(p))
    T = np.asarray(d.variables['thetao'][:], np.float32).squeeze()
    dep = np.asarray(d.variables['depth'][:], float)
    la = np.asarray(d.variables['latitude'][:])
    lo = np.asarray(d.variables['longitude'][:])
    d.close()
    os.chdir(cwd)
    if T.ndim == 4:
        T = T[0]
    if la[0] > la[-1]:
        T = T[:, ::-1, :]
        la = la[::-1]
    ohc, d26 = compute(T, dep)
    np.savez_compressed(dst, ohc=ohc, d26=d26,
                        lon=lo.astype(np.float32), lat=la.astype(np.float32))
    v = ohc[np.isfinite(ohc)]
    w = d26[np.isfinite(d26)]
    print('-> %s  OHC %.0f~%.0f 均值 %.0f | D26 %.0f~%.0f 均值 %.0f  (%.0f~%.0f m, %d 层)'
          % (os.path.basename(dst), v.min(), v.max(), v.mean(),
             w.min(), w.max(), w.mean(), dep.min(), dep.max(), dep.size), flush=True)
    for f in glob.glob(os.path.join(TMP, '*.nc')):
        try:
            os.remove(f)
        except OSError:
            pass
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', required=True)
    ap.add_argument('--basins', default='wpac,epac,natl')
    a = ap.parse_args()
    for date in a.dates.split(','):
        for b in a.basins.split(','):
            try:
                fetch(date.strip(), b.strip())
            except Exception as e:
                print('  !! %s %s 失败: %s' % (date, b, str(e)[:150]), flush=True)


if __name__ == '__main__':
    main()
