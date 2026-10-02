# -*- coding: utf-8 -*-
"""抓**全球** CMEMS thetao(每月一天, 单日不做时段平均)→ 算全球 OHC/D26 基态。

为什么每月一天、不做平均: 用户明确要求"不能看时段平均, 看单日"。月平均会把
黑潮/湾流这类窄锋面和涡旋抹平(OISST 的 30 年月气候态就是这么把黑潮抹没的),
所以基态按"每月一个单日"建 12 个场。

数据源: GLOBAL_MULTIYEAR_PHY_001_030 (GLORYS12V1, 0.083°, 1993-01 起)
        cmems_mod_glo_phy_my_0.083deg_P1D-m
全球 60S-60N / 0-320m 单日 ≈ 344 MB, 12 个月 ≈ 4.1 GB。

坑(同 fetch_cmems_ohc.py):
  * thetao 的 _FillValue 是有限值 9.97e36 → 必须按物理范围卡
  * output_filename 含中文会静默失败 → 写 ASCII 临时目录
  * 凭证文件 = base64(INI 带 [credentials] 段)

用法(Windows Python):
  python tools/fetch_cmems_global.py --year 2024 --day 15
输出: simulator/env/train_cache/cmems/glob_<YYYYMMDD>.npz  (ohc, d26, lon, lat)
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
TMP = r'C:\cmems_tmp'
MY = 'cmems_mod_glo_phy_my_0.083deg_P1D-m'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2024)
    ap.add_argument('--day', type=int, default=15)
    ap.add_argument('--months', default='1,2,3,4,5,6,7,8,9,10,11,12')
    a = ap.parse_args()
    from fetch_cmems_ohc import compute
    import copernicusmarine as cm
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    for m in [int(x) for x in a.months.split(',')]:
        date = '%04d-%02d-%02d' % (a.year, m, a.day)
        tag = date.replace('-', '')
        dst = os.path.join(OUT, 'glob_%s.npz' % tag)
        if os.path.exists(dst):
            print('已缓存', os.path.basename(dst), flush=True)
            continue
        for f in glob.glob(os.path.join(TMP, '*.nc')):
            try:
                os.remove(f)
            except OSError:
                pass
        print('[抓取] 全球 %s' % date, flush=True)
        try:
            cm.subset(dataset_id=MY, variables=['thetao'],
                      minimum_longitude=-180, maximum_longitude=180,
                      minimum_latitude=-60, maximum_latitude=60,
                      start_datetime=date + 'T00:00:00',
                      end_datetime=date + 'T00:00:00',
                      minimum_depth=0.0, maximum_depth=320.0,
                      output_directory=TMP, overwrite=True,
                      disable_progress_bar=True)
            hits = glob.glob(os.path.join(TMP, '*thetao*.nc'))
            if not hits:
                print('  !! 没拿到', flush=True)
                continue
            import netCDF4
            p = hits[0]
            cwd = os.getcwd()
            os.chdir(os.path.dirname(p))
            d = netCDF4.Dataset(os.path.basename(p))
            dep = np.asarray(d.variables['depth'][:], float)
            la = np.asarray(d.variables['latitude'][:])
            lo = np.asarray(d.variables['longitude'][:])
            v = d.variables['thetao']
            # **按经度分条**读 + 立刻降到 0.5°。
            # 一次读全球 0.083°(29×4320×2160) 是 1.08 GB, compute 里还要再翻倍,
            # 实测直接把内存打爆(4 分钟无进展)。分条后每块只有 ~180 MB。
            ny, nx = la.size, lo.size
            oy = 240          # 目标 0.5°
            ox = 720
            sy = ny / oy
            sx = nx / ox
            OH = np.zeros((oy, ox), np.float32)
            D26 = np.zeros((oy, ox), np.float32)
            SST = np.zeros((oy, ox), np.float32)        # 表层(0.494m)≈ SST
            for c0 in range(0, nx, 360):                 # 每块 360 列(30°)
                c1 = min(c0 + 360, nx)
                T = np.asarray(v[0, :, :, c0:c1], np.float32)   # (z, lat, lon_blk)
                oh, d26 = compute(T, dep)
                sst = np.where(np.isfinite(T[0]) & (T[0] > -3) & (T[0] < 45),
                               T[0], np.nan)
                if la[0] > la[-1]:
                    sst = sst[::-1, :]
                if la[0] > la[-1]:
                    oh = oh[::-1, :]
                    d26 = d26[::-1, :]
                # 块平均降采样到 0.5°
                b = c1 - c0
                oyb, oxb = int(round(ny / sy)), int(round(b / sx))
                ny2, nx2 = oyb * int(sy), oxb * int(sx)
                if ny2 and nx2:
                    ohd = oh[:ny2, :nx2].reshape(oyb, int(sy), oxb, int(sx)).mean((1, 3))
                    d26d = d26[:ny2, :nx2].reshape(oyb, int(sy), oxb, int(sx)).mean((1, 3))
                    j0 = int(round(c0 / sx))
                    OH[:, j0:j0 + oxb] = ohd
                    D26[:, j0:j0 + oxb] = d26d
                    sstd = sst[:ny2, :nx2].reshape(oyb, int(sy), oxb,
                                                   int(sx)).mean((1, 3))
                    SST[:, j0:j0 + oxb] = sstd
                print('    经度块 %d-%d 完成' % (c0, c1), flush=True)
            d.close()
            os.chdir(cwd)
            la_d = la[::-1] if la[0] > la[-1] else la
            la_d = -60.0 + (np.arange(240) + 0.5) * 0.5
            lo_d = (np.arange(720) + 0.5) * 0.5
            ohc, d26 = OH, D26
            la, lo = la_d, lo_d
            np.savez_compressed(dst, ohc=ohc, d26=d26,
                                sst=SST.astype(np.float32),
                                lon=lo.astype(np.float32),
                                lat=la.astype(np.float32))
            v = ohc[np.isfinite(ohc)]
            w = d26[np.isfinite(d26)]
            print('-> %s OHC %.0f~%.0f 均值 %.0f | D26 %.0f~%.0f 均值 %.0f'
                  % (os.path.basename(dst), v.min(), v.max(), v.mean(),
                     w.min(), w.max(), w.mean()), flush=True)
        except Exception as e:
            print('  !! %s 失败: %s' % (date, str(e)[:130]), flush=True)
        finally:
            for f in glob.glob(os.path.join(TMP, '*.nc')):
                try:
                    os.remove(f)
                except OSError:
                    pass
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
