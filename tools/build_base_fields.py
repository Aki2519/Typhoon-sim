# -*- coding: utf-8 -*-
"""重建环境场基态(base_sst / base_d26 / base_tchp), 单一来源 + 陆地最近海面填充。

背景(2026-09-20, 用户对照真实观测发现):
  * `base_sst.npz` 是坏的 —— 印度洋赤道、印尼-澳洲西北整体偏冷 5-9 °C,
    东太平洋赤道冷舌被填成 30 °C(真实 23), 且**带着陆地皮温**(全场 -2.3..35.2)。
    追到来源: `train_cache/cmems/glob_*.npz` 的 `sst` 就是 -2.08..35.24, 与坏
    基态的范围一模一样 —— 旧的 `build_sst_base_cmems.py` 正是拿它铺的。
  * 修法: SST 用 **OISST v2.1 1991-2020 月气候态**(`train_cache/oisst_ltm.nc`,
    0.25°, 已核验: NW澳 27.2 / 印度洋赤道 28.9 / 东太赤道 23.7, 与实测一致);
    D26/TCHP 用 **CMEMS GLORYS 全球月场**(`train_cache/cmems/glob_YYYYMM15.npz`,
    已是内核网格 240×720), 不再混源。
  * 所有场做**陆地最近海面填充**(距离变换), 这样"部分陆地格"着色不再有
    10-20 °C 假跳变, `env['sst']` 在陆地上也不再是垃圾。

用法:
  python tools/build_base_fields.py --check          # 只核对现有基态与真实参照
  python tools/build_base_fields.py --build          # 重建 sst/d26/tchp
  python tools/build_base_fields.py --build --only sst
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
OISST = os.path.join(CACHE, 'oisst_ltm.nc')
CMEMS = os.path.join(CACHE, 'cmems')
BACKUP = os.path.join(MODEL, '_bak_20260920')

ENVNY, ENVNX = 240, 720
LAT0, LAT1 = -60.0, 60.0
DST_LAT = LAT0 + (np.arange(ENVNY) + 0.5) * (LAT1 - LAT0) / ENVNY     # 南在上
DST_LON = (np.arange(ENVNX) + 0.5) * 360.0 / ENVNX

#: 与内核/显示同一份"格点陆地占比"缓存(tools/make_cell_land_mask.py 生成)
CELL_NPZ = os.path.join(MODEL, 'land_cell.npz')

#: 真实参照(9 月, 观测/资料值) —— 用来验收, 见 --check
REF_SEP = {
    'NW澳(帝汶海) 120E/12S': (120.0, -12.0, 26.8),
    'NW澳 115E/20S': (115.0, -20.0, 23.9),
    '印尼-澳洲 128E/15S': (128.0, -15.0, 28.0),
    '印度洋赤道 60E/0': (60.0, 0.0, 28.3),
    '印度洋赤道 75E/0': (75.0, 0.0, 28.1),
    '印度洋赤道 90E/0': (90.0, 0.0, 28.1),
    '东太赤道 240E/0': (240.0, 0.0, 23.6),
    '东太赤道 270E/0': (270.0, 0.0, 22.6),
    '西太暖池 130E/13N': (130.0, 13.0, 29.3),
    '西太 150E/0': (150.0, 0.0, 29.0),
    '巴士海峡 121E/21N': (121.0, 21.0, 29.0),
    '南印 50E/8S': (50.0, -8.0, 26.0),
}


def _cell_land():
    """格点陆地占比(240×720, 南在上); 缺失返回 None。"""
    try:
        with np.load(CELL_NPZ) as z:
            f = np.asarray(z['landfrac'], dtype=np.float32)
        return f if f.shape == (ENVNY, ENVNX) else None
    except Exception:
        return None


def fill_land_nearest(arr: np.ndarray, bad: np.ndarray) -> np.ndarray:
    """把  标记的格点用**最近有效格**的值填掉(距离变换, 不引入假梯度)。

    2026-09-20 修: 原实现把传入的掩码当"陆地"后又**重新算了 bad**(land | ~isfinite),
    于是调用方传进来的  /  这些"源数据缺失"标记被丢掉 ——
    实测印度洋/南半球那些 0 值就原样留在了 base_d26 里。
    """
    # **2026-09-21 改成平滑扩散填充**(用户: "sst 为什么会有跳变? 南极海域全是跳变"):
    # 最近有效值会跨大块陆地/海冰从另一侧"跳"过来, 实测相邻格差最大 18.8 °C。
    # 现在用 Laplace 扩散把缺测格平滑补上, 不再有硬边。
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from simulator.simcore.fill import smooth_fill
    arr = np.array(arr, dtype=np.float32, copy=True)
    bad = np.asarray(bad) | ~np.isfinite(arr)
    if not bad.any() or bad.all():
        return np.nan_to_num(arr, nan=0.0)
    return smooth_fill(arr, bad)


def _resample_to_env(src: np.ndarray, src_lat: np.ndarray, src_lon: np.ndarray,
                     order: int = 1) -> np.ndarray:
    """(nlat,nlon) 北在上 or 南在上 → 内核网格(240,720) 南在上。

    src_lat 允许升序或降序; src_lon 必须 0..360 升序。
    """
    from scipy import ndimage as ndi
    la = np.asarray(src_lat, dtype=np.float64)
    if la[0] > la[-1]:                      # 北在上 → 转成南在上
        src = src[::-1]
        la = la[::-1]
    lo = np.asarray(src_lon, dtype=np.float64)
    # 目标索引(用比例, 与网格无关)
    fy = (DST_LAT - la[0]) / (la[-1] - la[0]) * (src.shape[0] - 1)
    fx = (DST_LON - lo[0]) / (lo[-1] - lo[0]) * (src.shape[1] - 1)
    coords = np.array(np.meshgrid(fy, fx, indexing='ij'))
    return ndi.map_coordinates(np.asarray(src, dtype=np.float32), coords,
                               order=order, mode='nearest').astype(np.float32)


# ── SST: OISST LTM ────────────────────────────────────────────────

def build_sst():
    import netCDF4
    # **netCDF4 在 Windows 上走 locale 编码把路径传给 C 库** —— 仓库路径含中文
    # ("气象")时绝对路径会变成乱码 → FileNotFoundError。这里切到仓库根用
    # **相对 ASCII 路径**打开(实测绝对路径必失败, 相对路径正常)。
    os.chdir(ROOT)
    d = netCDF4.Dataset(os.path.relpath(OISST, ROOT))
    sst = np.asarray(d.variables['sst'][:], dtype=np.float32)      # (12,720,1440) °C
    lat = np.asarray(d.variables['lat'][:], dtype=np.float64).ravel()
    lon = np.asarray(d.variables['lon'][:], dtype=np.float64).ravel()
    fill_val = getattr(d.variables['sst'], '_FillValue', None)
    land_src = ~np.isfinite(sst)
    if fill_val is not None and np.isfinite(fill_val):
        land_src |= np.abs(sst - float(fill_val)) < 1e-3
    land_src |= (sst < -100.0) | (sst > 60.0)
    sst = np.where(land_src, np.nan, sst)
    land = _cell_land()
    out = np.zeros((12, ENVNY, ENVNX), dtype=np.float32)
    for m in range(12):
        r = _resample_to_env(sst[m], lat, lon)
        bad = ~np.isfinite(r)
        if land is not None:
            bad |= (np.flipud(land) >= 0.999)        # 全陆格: 也必须填(否则垃圾值)
        r = np.where(bad, np.nan, r)
        # 最近海面填充(陆地在 resample 里已是 NaN → 一起填)
        filled = fill_land_nearest(np.nan_to_num(r, nan=0.0), bad)
        out[m] = filled
    meta = {'source': 'OISST v2.1 mon ltm 1991-2020 (train_cache/oisst_ltm.nc)',
            'units': 'degC', 'grid': [ENVNY, ENVNX],
            'land_fill': 'nearest sea (distance transform)'}
    _save('base_sst', {'sst': out}, meta)
    return out


# ── D26 / TCHP: CMEMS GLORYS 全球月场 ─────────────────────────────

def build_d26():
    files = sorted(f for f in os.listdir(CMEMS)
                   if f.startswith('glob_') and f.endswith('.npz'))
    if len(files) < 12:
        raise RuntimeError('CMEMS 月场不足 12 个: %d' % len(files))
    files = files[:12]
    land = _cell_land()
    d26 = np.zeros((12, ENVNY, ENVNX), dtype=np.float32)
    ohc = np.zeros((12, ENVNY, ENVNX), dtype=np.float32)
    for m, f in enumerate(files):
        with np.load(os.path.join(CMEMS, f)) as z:
            a = np.asarray(z['d26'], dtype=np.float32)      # 已是内核网格(南在上)
            b = np.asarray(z['ohc'], dtype=np.float32)
        bad = (a <= 0.0) | ~np.isfinite(a)
        if land is not None:
            bad |= (land >= 0.999)
        d26[m] = fill_land_nearest(a, bad)
        bad2 = (b <= 0.0) | ~np.isfinite(b)
        if land is not None:
            bad2 |= (land >= 0.999)
        ohc[m] = fill_land_nearest(b, bad2)
    meta = {'source': 'CMEMS GLORYS glob_YYYYMM15.npz (2024 十二个月)',
            'd26_units': 'm', 'tchp_units': 'kJ/cm2',
            'land_fill': 'nearest sea (distance transform)'}
    _save('base_d26', {'d26': d26}, meta)
    _save('base_tchp', {'tchp': ohc}, meta)
    return d26, ohc


def _save(name: str, arrays: dict, meta: dict):
    os.makedirs(BACKUP, exist_ok=True)
    dst = os.path.join(MODEL, name + '.npz')
    if os.path.exists(dst):                    # 先备份旧文件(可回退)
        shutil.copy2(dst, os.path.join(BACKUP, name + '.npz'))
    np.savez_compressed(dst, meta=json.dumps(meta, ensure_ascii=False), **arrays)
    print('写出 %s  %s' % (dst, {k: getattr(v, 'shape', None) for k, v in arrays.items()}))


# ── 核对 ──────────────────────────────────────────────────────────

def check():
    from simulator.simcore.envmodel import store
    print('%-24s %8s %8s %8s' % ('参照点(9 月)', '基态', '真实', '差'))
    tot = 0.0
    n = 0
    sst = np.asarray(store.load('base_sst')['sst'], dtype=np.float64)[8]
    lat = DST_LAT
    lon = DST_LON
    for tag, (lo, la, ref) in REF_SEP.items():
        i = int(np.argmin(np.abs(lat - la)))
        j = int(np.argmin(np.abs(lon - (lo % 360.0))))
        v = sst[i, j]
        d = v - ref
        tot += abs(d)
        n += 1
        flag = '  OK' if abs(d) <= 1.5 else ('  ~' if abs(d) <= 3.0 else '  BAD')
        print('%-24s %8.2f %8.2f %+8.2f%s' % (tag, v, ref, d, flag))
    print('平均绝对误差 %.2f °C (n=%d)' % (tot / max(1, n), n))


def main(argv=None):
    ap = argparse.ArgumentParser(description='重建环境场基态')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--only', default='all', choices=['all', 'sst', 'd26', 'shear'])
    a = ap.parse_args(argv)
    if a.build:
        if a.only in ('all', 'sst'):
            build_sst()
        if a.only in ('all', 'd26'):
            build_d26()
        if a.only == 'shear':
            print('shear 由 NCEP 缓存构建(见 build_shear_from_ncep), 本轮沿用现有文件')
    if a.check or not a.build:
        check()


if __name__ == '__main__':
    main()
