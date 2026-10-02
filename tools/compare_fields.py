# -*- coding: utf-8 -*-
"""模型 vs 真实: 逐域并排对照图 + 逐点误差表(自我改进用的验收工具)。

真实源(全部离线、单一来源):
  * SST   OISST v2.1 1991-2020 月气候态   train_cache/oisst_ltm.nc
  * OHC   CMEMS GLORYS 全球月场(2024)    train_cache/cmems/glob_*.npz
  * 风切   NCEP/NCAR 月均 |V200-V850|×0.55 train_cache/ncep_{uwnd,vwnd}_lv.npz

用法:
  python tools/compare_fields.py --field sst --start "1997-09-16 12"
  python tools/compare_fields.py --field all --start "2026-02-28 00"
产物: output/sim/<起始年>/cmp_<field>_<domain>.png
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))

from simulator.simcore import core as V          # noqa: E402
from simulator.simcore import render as R         # noqa: E402

CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
CELL_NPZ = os.path.join(ROOT, 'simulator', 'env', 'model', 'land_cell.npz')

SCALE = 6.0
#: **内核数据的纬度范围**。240 行 × 0.5° —— 北冰洋(>60°N)与南极(<60°S)**不在模型域内**。
DATA_LAT = (-60.0, 60.0)
#: **出图画布**: 与 App 交互图层一致(render.py 的 LAYER_LAT0/LAT1 = ±90)。
VIEW_LAT = (-90.0, 90.0)
NODATA_RGB = (46, 48, 56)          # 域外: 深灰, 一眼看出"这里模型没算"
EDGE_RGB = (128, 132, 146)         # 域边界线


def pad_polar(arr, land=None, view_lat=VIEW_LAT, data_lat=DATA_LAT, dlat=0.5,
              north_up=True):
    """把 ±60 的数据摆到 ±90 的画布中央(默认**北在上**), 域外填 NaN。

    为什么不直接拉伸边界行: 那样会在**存档产品**里伪造两极数据。App 的交互图层选择外推
    补齐(所以那里看着是满的), 存档图这里选择**如实标注域边界**(用户 2026-09-21 问
    "为什么北冰洋及南极没有数据" —— 答案是内核只算 ±60, 必须让图自己说清楚)。
    返回 (画布数组, 陆地掩码或 None); 画布行 0 = 89.75°N。
    """
    a = np.asarray(arr, np.float32)
    ny = int(round((view_lat[1] - view_lat[0]) / dlat))
    ny_d = a.shape[0]
    pad = ny - ny_d
    if north_up:
        a = a[::-1, :]
    if pad <= 0:
        return np.ascontiguousarray(a), (None if land is None
                                         else np.asarray(land) >= 0.999)
    top = int(round(pad / 2.0))
    out = np.full((ny, a.shape[1]), np.nan, np.float32)
    out[top:top + ny_d] = a
    if land is None:
        return out, None
    lm = np.zeros((ny, np.asarray(land).shape[1]), bool)
    src = np.asarray(land) >= 0.999
    if north_up:
        src = src[::-1, :]
    lm[top:top + ny_d] = src
    return out, lm


def rgb_domain(fld, arr, land=None, nodata=NODATA_RGB, edge=EDGE_RGB):
    """上色 + 陆地挖空 + **域外画成无数据色**(并在域边界画一条线)。"""
    a = np.asarray(arr, np.float32)
    bad = ~np.isfinite(a)
    rgb = np.ascontiguousarray(_lut_rgb(fld, np.nan_to_num(a, nan=0.0)).astype(np.uint8))
    if land is not None:
        lm = np.asarray(land)
        if lm.dtype != bool:
            lm = lm >= 0.999
        rgb = np.where(lm[..., None], np.array([110, 110, 105], np.uint8), rgb).astype(np.uint8)
    if bad.any():
        rgb[bad] = nodata
        rows = np.where(bad.all(axis=1))[0]
        if rows.size:
            # 画在**紧贴数据的那一行上**(上面那块的最后一行 / 下面那块的第一行),
            # 原来用 rows.min()-1 和 rows.max()+1 —— 那是画布外的行号, 一条线都没画上。
            half = rgb.shape[0] // 2
            _t = rows[rows < half]
            _b = rows[rows >= half]
            for r in ([int(_t.max())] if _t.size else []) + \
                     ([int(_b.min())] if _b.size else []):
                if 0 <= r < rgb.shape[0]:
                    rgb[r] = edge
    return rgb

DOMAINS = (
    ('global', 0.0, 360.0, -60.0, 60.0),
    ('india', 35.0, 115.0, -12.0, 22.0),
    ('nwaust', 108.0, 140.0, -25.0, -2.0),
    ('wpac', 100.0, 180.0, 0.0, 50.0),
    ('epac', 220.0, 280.0, 0.0, 35.0),
    ('natl', 260.0, 350.0, 0.0, 50.0),
)
#: 验收参照点(9 月/2 月通用, 取自观测常识值; 与 build_base_fields.REF_SEP 同源)
REFS = {
    'sst': (('NW澳 120E/12S', 120, -12, 26.8), ('NW澳 115E/20S', 115, -20, 23.9),
            ('印度洋赤道 75E/0', 75, 0, 28.1), ('东太赤道 240E/0', 240, 0, 23.6),
            ('暖池 130E/13N', 130, 13, 29.3), ('巴士 121E/21N', 121, 21, 29.0),
            ('南印 50E/8S', 50, -8, 26.0),
            # 用户点名的区域要都在表里: EPAC / NATL
            ('加勒比 300E/15N', 300, 15, 29.3),
            ('东太 250E/15N', 250, 15, 28.5)),
    # OHC / 风切也要“逐点核数值”(用户要求)。坐标与 sst 同一批,
    # 第四列是“常识量级”(仅供参考, 不参与误差计算)。
    'ohc': (('暖池 130E/13N', 130, 13, 110.0), ('东太赤道 240E/0', 240, 0, 20.0),
            ('印度洋赤道 75E/0', 75, 0, 85.0), ('NW澳 120E/12S', 120, -12, 55.0),
            ('南印 50E/8S', 50, -8, 60.0), ('巴士 121E/21N', 121, 21, 90.0),
            ('加勒比 300E/15N', 300, 15, 90.0),
            ('东太 250E/15N', 250, 15, 60.0)),
    'shear': (('暖池 130E/13N', 130, 13, 8.0), ('东太赤道 240E/0', 240, 0, 20.0),
              ('印度洋赤道 75E/0', 75, 0, 12.0), ('NW澳 120E/12S', 120, -12, 18.0),
              ('日本南 140E/35N', 140, 35, 45.0), ('中太 200E/40N', 200, 40, 20.0),
              ('加勒比 300E/15N', 300, 15, 12.0),
              ('东太 250E/15N', 250, 15, 15.0)),
}

_LAND = None


def cell_land():
    global _LAND
    if _LAND is None:
        try:
            with np.load(CELL_NPZ) as z:
                _LAND = np.asarray(z['landfrac'], dtype=np.float32)
        except Exception:
            _LAND = np.zeros((240, 720), dtype=np.float32)
    return _LAND


def _lut_rgb(name, arr):
    arr = np.nan_to_num(np.asarray(arr, dtype=np.float32), nan=28.0,
                        posinf=28.0, neginf=28.0)   # 缺测(陆地)用中性值, 由掩码挖掉
    if name == 'sst':
        t = np.clip(arr / 33.0, 0, 1) * 255.0
        return R.LUT_SST_STEPS[t.astype(int)][..., :3]
    if name == 'ohc':
        t = np.clip(arr / 220.0, 0, 1) * 255.0
        return R.LUT_OHC_STEPS[t.astype(int)][..., :3]
    t = np.clip(arr / getattr(R, 'SHEAR_LUT_MAX_KT', 60.0), 0, 1) * 255.0
    return R.LUT_SHEAR[t.astype(int)][..., :3]


def _base_map(lon0, lon1, lat0, lat1, W, H):
    MAP = pygame.image.load(os.path.join(ROOT, 'map', 'map.png'))
    x0 = int(lon0 / 360.0 * MAP.get_width()); x1 = int(lon1 / 360.0 * MAP.get_width())
    y0 = int((90.0 - lat1) / 180.0 * MAP.get_height())
    y1 = int((90.0 - lat0) / 180.0 * MAP.get_height())
    return pygame.transform.smoothscale(
        MAP.subsurface(pygame.Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))).copy(),
        (W, H))


def panel(field, arr, lat_axis, lon_axis, lon0, lon1, lat0, lat1, title, land=None):
    """arr: (nlat,nlon) 与 lat_axis 对齐; 输出贴到底图的面(双线性 + 陆地掩码)。"""
    W = int((lon1 - lon0) * SCALE); H = int((lat1 - lat0) * SCALE)
    img = _base_map(lon0, lon1, lat0, lat1, W, H)
    lat_s = lat1 - (np.arange(H) + 0.5) / SCALE
    lon_s = lon0 + (np.arange(W) + 0.5) / SCALE
    # **双线性重采样**(原来用 argmin 最近邻, 在 0.25° 的 ERA5 场上会画出一格格
    # 矩形色块 —— 2026-09-20 修)。经度按环绕距离取插值权重。
    from scipy import ndimage as ndi
    la_ax = np.asarray(lat_axis, float)
    lo_ax = np.asarray(lon_axis, float)
    fy = (lat_s - la_ax[0]) / (la_ax[-1] - la_ax[0]) * (la_ax.size - 1)
    fx = ((lon_s % 360.0) - lo_ax[0]) / (lo_ax[-1] - lo_ax[0]) * (lo_ax.size - 1)
    samp = ndi.map_coordinates(np.nan_to_num(np.asarray(arr, np.float32), nan=0.0),
                               np.array(np.meshgrid(fy, fx, indexing='ij')),
                               order=1, mode='nearest')
    rgb = _lut_rgb(field, samp).transpose(1, 0, 2)
    ii = jj = None
    ov = pygame.Surface((W, H), pygame.SRCALPHA)
    px = pygame.surfarray.pixels3d(ov); al = pygame.surfarray.pixels_alpha(ov)
    px[..., :3] = rgb
    a = np.full((W, H), 225, np.uint8)
    if land is not None:
        lm = ndi.map_coordinates(np.asarray(land, np.uint8),
                                 np.array(np.meshgrid(fy, fx, indexing='ij')),
                                 order=0, mode='nearest').astype(bool).T
        a[lm] = 0
    al[..., :] = a
    del px, al
    img.blit(ov, (0, 0))
    f = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                      'MapleMono-NF-CN-Medium.ttf'), 18)
    img.blit(f.render(title, True, (255, 240, 120)), (6, 4))
    return img


# ── 真实场读取 ────────────────────────────────────────────────────

def real_sst(month_idx, land=None):
    import netCDF4
    os.chdir(ROOT)
    d = netCDF4.Dataset(os.path.relpath(os.path.join(CACHE, 'oisst_ltm.nc'), ROOT))
    v = d.variables['sst'][month_idx]
    sst = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(v[:])), np.nan).astype(np.float32)
    lat = np.asarray(d.variables['lat'][:], dtype=np.float64).ravel()
    lon = np.asarray(d.variables['lon'][:], dtype=np.float64).ravel()
    # 陆地/冰: 缺测 或 明显越界(|>60| 覆盖 fill 的 ±1e36 与 0 值填充)
    bad = ~np.isfinite(sst) | (np.abs(sst) > 60.0)
    # 也把"格点陆地占比 100%"的格算进来(海冰/大湖等掩码不全的地方)
    lf = cell_land()
    if lf is not None:
        iy = np.clip((lat + 60.0) / 120.0 * lf.shape[0], 0, lf.shape[0] - 1).astype(int)
        ix = np.clip(lon / 360.0 * lf.shape[1], 0, lf.shape[1] - 1).astype(int)
        bad = bad | (lf[np.ix_(iy, ix)] >= 0.999)
    sst = np.where(bad, np.nan, sst)
    return sst, lat, lon, bad


def real_shear(month_idx):
    U = np.load(os.path.join(CACHE, 'ncep_uwnd_lv.npz'))
    Vl = np.load(os.path.join(CACHE, 'ncep_vwnd_lv.npz'))
    mon = np.asarray(U['month']).ravel()
    lat = np.asarray(U['lat']).ravel()
    lon = np.asarray(U['lon']).ravel()
    m = (mon == (month_idx + 1))
    sh = np.hypot(np.asarray(U['uwnd200'])[m].mean(0) - np.asarray(U['uwnd850'])[m].mean(0),
                  np.asarray(Vl['vwnd200'])[m].mean(0) - np.asarray(Vl['vwnd850'])[m].mean(0))
    return (sh * 1.94384 * 0.55).astype(np.float32), lat, lon


def real_ohc(month_idx):
    files = sorted(f for f in os.listdir(os.path.join(CACHE, 'cmems'))
                   if f.startswith('glob_') and f.endswith('.npz'))[:12]
    with np.load(os.path.join(CACHE, 'cmems', files[month_idx])) as z:
        ohc = np.asarray(z['ohc'], dtype=np.float32)
    lat = -60 + (np.arange(240) + 0.5) * 0.5
    lon = (np.arange(720) + 0.5) * 0.5
    return ohc, lat, lon


def model_fields(start, month_idx):
    sim = V.init({'start': start, 'seed': 3, 'envStepH': 6.0,
                  'params': {'precision': '400ms', 'dataMode': True}})
    V.sim_step(sim, 6.0)
    lat = -60 + (np.arange(240) + 0.5) * 0.5
    lon = (np.arange(720) + 0.5) * 0.5
    return sim, np.asarray(sim['env']['sst'], np.float64), \
        np.asarray(sim['env']['shear'], np.float64), lat, lon


def _sample(arr, lat_axis, lon_axis, lon0, lat0):
    """在 (lon0, lat0) 最近格点取一个值(逐点核对用)。"""
    if arr is None or lat_axis is None or lon_axis is None:
        return None
    a = np.asarray(arr, np.float64)
    if a.ndim != 2:
        return None
    i = int(np.argmin(np.abs(np.asarray(lat_axis, float) - float(lat0))))
    j = int(np.argmin(np.abs(((np.asarray(lon_axis, float) - float(lon0) + 180.0)
                              % 360.0) - 180.0)))
    v = float(a[i, j])
    return v if np.isfinite(v) else None


def real_ref(field, clock, month_idx):
    """真实参照: **优先瞬时**(ERA5 单日 / CMEMS 单日洋区), 没有才用月气候态。

    用户 2026-09-20: "风切及一切数据都要用瞬时"。验收也要照着瞬时比, 否则拿月均
    当靶子会把瞬时场本身的真实结构判成"误差"。
    返回 (arr, lat, lon, bad, tag)
    """
    try:
        from simulator.simcore import daily as _D
        inst = _D.load_fields(clock)
    except Exception:
        inst = {}
    lat_k = -60 + (np.arange(240) + 0.5) * 0.5
    lon_k = (np.arange(720) + 0.5) * 0.5
    if field == 'sst' and 'sst' in inst:
        return inst['sst'], lat_k, lon_k, ~np.isfinite(inst['sst']), '瞬时 ERA5'
    if field == 'ohc' and 'ohc' in inst:
        a = inst['ohc']
        return a, lat_k, lon_k, (a <= 0), '瞬时 CMEMS 单日 OHC'
    if field == 'shear' and 'shear' in inst:
        a = inst['shear']
        return a, lat_k, lon_k, None, '瞬时 ERA5'
    if field == 'sst':
        a, la, lo, bad = real_sst(month_idx)
        return a, la, lo, bad, 'OISST LTM 月气候态'
    if field == 'ohc':
        # **独立参照优先**: ① 当日 CMEMS 单日洋区 OHC(瞬时); ② 当日 AOML tile(卫星+Argo,
        # 完全独立); ③ 都没有就如实说"参照不可用", 不要退回到模型自己的产物(自比自 =
        # 假验收)。旧版这里依赖 cmems/glob_*.npz, 而那批坏文件已被移走 →
        # 会静默失去参照(2026-09-20 实测 2024-09-15 的 OHC 表直接空了)。
        try:
            _ymd = '%04d%02d%02d' % (clock.year, clock.month, clock.day)
            _tile = os.path.join(r'C:\cmems_tmp', 'aoml_wpac_%s.nc' % _ymd)
            if os.path.exists(_tile):
                import xarray as _xr
                _d = _xr.open_dataset(_tile)
                _a = np.asarray(_d['Tropical_Cyclone_Heat_Potential'].values,
                                np.float32).squeeze()
                _la = np.asarray(_d['latitude'].values, float).ravel()
                _lo = np.asarray(_d['longitude'].values, float).ravel()
                _d.close()
                return _a, _la, _lo, (_a <= 0), 'AOML tile(卫星+Argo, 独立)'
        except Exception:
            pass
        a, la, lo = real_ohc(month_idx)
        if a is not None and np.isfinite(a).any():
            return a, la, lo, (a <= 0), 'CMEMS 月场'
        return None, None, None, None, '参照不可用(无 CMEMS 单日 / AOML tile)'
    a, la, lo = real_shear(month_idx)
    return a, la, lo, None, 'NCEP 月均×0.55'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--field', default='sst', choices=['sst', 'ohc', 'shear', 'all'])
    ap.add_argument('--start', default='1997-09-16 12')
    a = ap.parse_args(argv)
    month_idx = int(a.start[5:7]) - 1
    year = a.start[:4]
    outdir = os.path.join(ROOT, 'output', 'sim', year)
    os.makedirs(outdir, exist_ok=True)
    from datetime import datetime
    _ = datetime

    fields = ['sst', 'ohc', 'shear'] if a.field == 'all' else [a.field]
    from datetime import datetime as _dt
    clock = _dt(y_ := int(a.start[:4]), int(a.start[5:7]), int(a.start[8:10]),
                int(a.start[11:13]) if len(a.start) > 11 else 12)
    sim, sst_m, sh_m, lat_m, lon_m = model_fields(a.start, month_idx)
    land = (cell_land() >= 0.999)
    print('模型 %s  dataState=%s' % (a.start, sim.get('dataState')))

    for fld in fields:
        _ref, _rlat, _rlon, _rbad, _tag = real_ref(fld, clock, month_idx)
        print('  真实参照源: %s' % _tag)
        if _ref is None:
            # 没有独立参照就**不做假验收**, 明确跳过这一层
            print('  → %s 这一层缺独立参照, 跳过数值核对(不是模型错, 是参照缺)' % fld)
            continue
        if fld == 'sst':
            real, rlat, rlon, rbad = _ref, _rlat, _rlon, _rbad
            land_r = rbad
            mfield, mlat, mlon = sst_m, lat_m, lon_m
            land_m = land
        elif fld == 'ohc':
            real, rlat, rlon = _ref, _rlat, _rlon
            land_r = _rbad if _rbad is not None else (real <= 0)
            # **量纲对齐**(2026-09-20): 内核契约里 env['ohc'] 是 **D26(米)**, 而真实参照
            # (CMEMS 单日 / AOML / CMEMS 月场)是 **TCHP(kJ/cm2)** —— 直接把两个数相减会
            # 得到"误差 34 kJ"这种假数字(实测东太 +73.7 其实只是 111 m vs 37.8 kJ)。
            # 参照是 kJ 时就用模型自己的 TCHP 场 ocn['ohc_kj'] 来比。
            ocn = sim.get('ocn') or {}
            _ref_is_kj = bool(_tag and ('kJ' in _tag or 'AOML' in _tag or 'OHC' in _tag))
            if _ref_is_kj and ocn.get('ohc_kj') is not None:
                mfield = np.asarray(ocn['ohc_kj'], np.float64)
                print('  (量纲: 模型用 ocn[ohc_kj] kJ/cm2 与参照对齐)')
            else:
                mfield = (np.asarray(sim['env'].get('ohc'), np.float64)
                          if 'ohc' in sim['env'] else None)
                if mfield is None:
                    mfield = (np.asarray(ocn.get('ohc_kj'))
                              if ocn.get('ohc_kj') is not None else None)
            if mfield is None:
                print('  跳过 ohc(模型无该场)')
                continue
            mlat, mlon = lat_m, lon_m
            land_m = land
        else:
            real, rlat, rlon = _ref, _rlat, _rlon
            land_r = _rbad
            mfield, mlat, mlon = sh_m, lat_m, lon_m
            land_m = None

        # 单位/量纲对齐: model ohc 若是 d26(米) 而真实是 kJ → 各自归一化后再比
        print('\n=== %s ===' % fld.upper())
        errs = []
        for dom, lon0, lon1, lat0, lat1 in DOMAINS:
            A = panel(fld, mfield, mlat, mlon, lon0, lon1, lat0, lat1,
                      '模型 %s %s' % (fld.upper(), dom), land=land_m)
            B = panel(fld, real, rlat, rlon, lon0, lon1, lat0, lat1,
                      '真实 %s %s' % (fld.upper(), dom), land=land_r)
            W, H = A.get_size()
            canv = pygame.Surface((W, H * 2 + 6)); canv.fill((12, 16, 26))
            canv.blit(A, (0, 0)); canv.blit(B, (0, H + 6))
            out = os.path.join(outdir, 'cmp_%s_%s.png' % (fld, dom))
            pygame.image.save(canv, out)
        # **逐点误差(三层都打表)**: 模型与真实在同一批参照点上取值。
        # 原来只给 sst 打表(OHC/风切只出图), 于是"逐点核数值"对后两层是空的 ——
        # 2026-09-20 补: 三层都按 REFS 的点位取值打印, 并给平均/最大误差。
        if fld in REFS:
            print('%-20s %9s %9s %9s' % ('参照点', '模型', '真实', '差'))
            _e = []
            # **参照覆盖范围之外的点不能硬采样**(2026-09-20): AOML 的 tile 只覆盖
            # 100-180E/0-35N, 若对印度洋/南半球的点硬取最近格点, map_coordinates 会
            # 把边缘值 clamp 过来 → 得到 +47 kJ 这种荒唐"误差"。所以先判是否在覆盖内,
            # 不在就标 "—"并排除出统计。
            _rla = np.asarray(rlat, float) if rlat is not None else None
            _rlo = np.asarray(rlon, float) if rlon is not None else None
            _la0, _la1 = (_rla.min(), _rla.max()) if _rla is not None else (-999, 999)
            _lo0, _lo1 = (_rlo.min(), _rlo.max()) if _rlo is not None else (-999, 999)
            _skipped = []
            for _tag2, _lo, _la, _obs in REFS[fld]:
                _lonn = _lo % 360.0
                if not (_la0 - 0.6 <= _la <= _la1 + 0.6 and _lo0 - 0.6 <= _lonn <= _lo1 + 0.6):
                    _skipped.append(_tag2)
                    print('%-20s %9s %9s %9s' % (_tag2, '—', '—', '参照无覆盖'))
                    continue
                try:
                    _mv = _sample(mfield, mlat, mlon, _lo, _la)
                    _rv = _sample(real, rlat, rlon, _lo, _la)
                except Exception:
                    continue
                if _mv is None or _rv is None or not (np.isfinite(_mv) and np.isfinite(_rv)):
                    continue
                _e.append(abs(_mv - _rv))
                print('%-20s %9.2f %9.2f %+9.2f' % (_tag2, _mv, _rv, _mv - _rv))
            if _e:
                _u = '°C' if fld == 'sst' else ('kJ/cm2' if fld == 'ohc' else 'kt')
                print('平均绝对误差 %.2f %s  最大 %.2f %s  (有效点 %d/%d)' % (
                    float(np.mean(_e)), _u, float(np.max(_e)), _u,
                    len(_e), len(REFS[fld])))
            print('  产物 → %s' % os.path.join(outdir, 'cmp_%s_*.png' % fld))
            continue
        # 没有参照点表的老路径(理论上不会走到)
        if fld == 'sst':
            print('%-22s %8s %8s %8s' % ('参照点', '模型', '真实', '差'))
            for tag, lo, la, ref in REFS['sst']:
                i = int(np.argmin(np.abs(mlat - la)))
                j = int(np.argmin(np.abs(mlon - (lo % 360.0))))
                ir = int(np.argmin(np.abs(rlat - la)))
                jr = int(np.argmin(np.abs(rlon - (lo % 360.0))))
                d = float(mfield[i, j]) - float(real[ir, jr])
                errs.append(abs(d))
                print('%-22s %8.2f %8.2f %+8.2f' % (tag, mfield[i, j], real[ir, jr], d))
            print('平均绝对误差 %.2f °C  最大 %.2f °C'
                  % (float(np.mean(errs)), float(np.max(errs))))
        print('  产物 → output/sim/%s/cmp_%s_*.png' % (year, fld))


if __name__ == '__main__':
    main()
