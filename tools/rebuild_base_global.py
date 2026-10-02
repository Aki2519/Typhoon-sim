# -*- coding: utf-8 -*-
"""把 6 个基态场从 ±60 重建到**全球 ±90**(用户 2026-09-21: "必须扩域!极区必须参与模拟")。

做法与理由(为什么不需要重新下载任何东西):
  * **SST**:源本来就是全球的 —— OISST v2.1 1991-2020 月气候态到 ±89.9。
    仓库里 `base_sst_oisst.npz` 就是它(±60 的裁剪版), 而当前 `base_sst.npz` =
    它 + CMEMS 单日场的中尺度异常(用户要的"SST 要像 OHC 一样细")。
    于是: anomaly = base_sst.npz − base_sst_oisst.npz(逐格相减, 天然避免重复叠加 —— 
    2026-09-21 我自己就踩过"异常叠两遍"的坑), 新基态 = 重采样到 ±90 的 oisst + 内嵌 anomaly。
    **极区只有 OISST(平滑), 没有中尺度异常** —— 如实标注。
  * **D26/TCHP**:CMEMS 洋区单日场只有 ±60(原始文件没留), 但 D26/TCHP 按定义
    **在 26°C 等温线以外恒为 0** —— 所以极区补 0 不是伪造, 而是定义。顺带用同一条规则
    修掉北边界: 现有 base_d26 在 59.75°N 有 81.81 m 这种非物理值(源文件边界污染),
    按 "SST<26 → 0" 一律归零。
  * **风/湿度/气温/切变**:NCEP/NCAR R1 月均 2.5° 本来就是全球 ±90, 由
    `tools/train_env_base.py` 的 `_to_one_deg/_to_half_deg` 重采样(已改成跟随内核常量)。
    跑 `python tools/train_env_base.py --stage build` 即可重建 —— 注意它见到 CMEMS 单日
    就整体跳过, 所以本工具带 `--atmos` 直接用 NCEP 缓存自己重建。

用法:
  python tools/rebuild_base_global.py --sst --ocn     # 地球物理两件套(不需下载)
  python tools/rebuild_base_global.py --atmos         # 需要 ncep_*_g.npz(见 fetch_ncep_plev.py --suffix _g)
  python tools/rebuild_base_global.py --check
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                       # noqa: E402
from simulator.simcore.envmodel import store                   # noqa: E402

NY, NX = int(V.ENVNY), int(V.ENVNX)
CLDNY, CLDNX = int(V.CLDNY), int(V.CLDNX)
LAT0, LAT1 = float(V.C['LAT0']), float(V.C['LAT1'])
DLAT = float(V.C['ENVD'])
#: 内核格心纬度(南在上, row0 = -89.75)
LAT_K = LAT0 + (np.arange(NY) + 0.5) * DLAT
LAND = None



def open_nc(rel):
    """netCDF4 打不开**含中文的绝对路径**(实测 FileNotFoundError) ⇒ chdir 到仓库根走相对路径。

    这是这台机器上的老坑(daily.py 里也有同款 _rel 处理)。rel 相对 ROOT。
    """
    import netCDF4
    cwd = os.getcwd()
    try:
        os.chdir(ROOT)
        return netCDF4.Dataset(rel)
    finally:
        os.chdir(cwd)


def land():
    global LAND
    if LAND is None:
        with np.load(os.path.join(MODEL, 'land_cell.npz')) as z:
            LAND = np.asarray(z['landfrac'], np.float32)
    return LAND


def resample(a, src_lat, src_lon, ny=NY, nx=NX, lat0=LAT0, lat1=LAT1):
    """(…,nlat,nlon) 任意源 → 内核网格(南在上), 双线性 + 经度周期。"""
    a = np.asarray(a, np.float32)
    la = np.asarray(src_lat, float)
    lo = np.asarray(src_lon, float)
    if la[0] > la[-1]:                       # 北在上 → 南在上
        a = a[..., ::-1, :]
        la = la[::-1]
    dst_lat = lat0 + (np.arange(ny) + 0.5) * ((lat1 - lat0) / ny)
    dst_lon = (np.arange(nx) + 0.5) * (360.0 / nx)
    j = np.clip(np.searchsorted(la, dst_lat) - 1, 0, la.size - 2)
    # **必须夹取**: dst 落在源范围外时, 线性外推会跑飞(实测 60 格外推 → ±90 变 -136°C)。
    # 夹到 [0,1] 后极区=取边界行(要真极区数据就换全球源, 别让它外推)。
    fj = np.clip((dst_lat - la[j]) / (la[j + 1] - la[j]), 0.0, 1.0)
    lw = np.where(lo < 0, lo + 360.0, lo)
    order = np.argsort(lw)
    lw = lw[order]
    aw = a[..., order]
    ext = np.concatenate([lw - 360.0, lw, lw + 360.0])
    ae = np.concatenate([aw, aw, aw], axis=-1)
    i = np.clip(np.searchsorted(ext, dst_lon) - 1, 0, ext.size - 2)
    fi = np.clip((dst_lon - ext[i]) / (ext[i + 1] - ext[i]), 0.0, 1.0)
    tmp = ae[..., i] * (1 - fi) + ae[..., i + 1] * fi
    out = tmp[..., j, :] * (1 - fj)[:, None] + tmp[..., j + 1, :] * fj[:, None]
    return out.astype(np.float32)


def _embed60(a60, fill=0.0):
    """把 ±60 的 (…,240,720) 内嵌到 (…,NY,NX) 画布中央。"""
    a = np.asarray(a60, np.float32)
    out = np.full(a.shape[:-2] + (NY, NX), fill, np.float32)
    p = (NY - a.shape[-2]) // 2
    out[..., p:p + a.shape[-2], :] = a
    return out


def _half60_idx():
    """±60 那 240 行在 ±90 画布上的行区间。"""
    p = int(round(((90.0 - 60.0) / 180.0) * NY))
    return p, p + int(round(120.0 / DLAT))


def build_sst(save=True):
    """SST = **全球** OISST 月气候态(到 ±89.9) + ±60 内叠 CMEMS 单日中尺度异常。

    第一版踩的坑: 拿 ±60 的裁剪版 () 去重采样到 ±90,
    源范围外做了线性外推 → 极区算出 -136°C。现在源直接取全球 nc。
    """
    from scipy.ndimage import gaussian_filter
    from simulator.simcore.fill import ocean_fill

    d = open_nc('simulator/env/train_cache/oisst_ltm.nc')
    a = np.asarray(d.variables['sst'][:], np.float32)
    la = np.asarray(d.variables['lat'][:]).ravel()
    lo = np.asarray(d.variables['lon'][:]).ravel()
    d.close()
    # OISST 缺测是 _FillValue(9.97e36, **有限值**) → 用物理上下界挡掉
    a = np.where(np.isfinite(a) & (a < 60.0) & (a > -5.0), a, np.nan)
    base = resample(a, la, lo)                      # (12, NY, NX) 南在上
    for m in range(base.shape[0]):
        base[m] = ocean_fill(base[m], land=land(), despike=False)
    print('  OISST 全球气候态 → %s  范围 %.2f..%.2f' % (base.shape, np.nanmin(base), np.nanmax(base)))

    # ── ±60 内叠 CMEMS 单日中尺度异常(与 tools/add_sst_mesoscale.py 同一套算法) ──
    g2s = []
    for m in range(1, 13):
        fs = glob.glob(os.path.join(CACHE, 'cmems', 'g2_*%02d15.npz' % m))
        if not fs:
            g2s = []
            break
        with np.load(sorted(fs)[0]) as z:
            if 'sst' not in z.files:
                g2s = []
                break
            g2s.append(ocean_fill(np.asarray(z['sst'], np.float32), land=None, despike=False))
    if len(g2s) == 12:
        g = np.stack(g2s)
        clim = g.mean(0)
        p0, p1 = _half60_idx()
        for i in range(12):
            an = g[i] - clim
            an = an - gaussian_filter(an, sigma=(2.0, 2.0), mode=('nearest', 'wrap'))
            an = an - an.mean(axis=1, keepdims=True)          # 去纬向平均(不与 OISST 季节循环重复)
            an = np.clip(an, -2.5, 2.5) * np.clip((base[i][p0:p1] - 0.0) / 4.0, 0.0, 1.0)
            base[i][p0:p1] += an
        print('  CMEMS 单日中尺度异常已叠进 ±60 那 %d 行(std %.2f °C)'
              % (p1 - p0, float(np.std((g - clim)[:, 120:360]))))
    else:
        print('  !! 缺 12 个月的 CMEMS 单日 sst, 跳过中尺度异常(极区/全球都是平滑气候态)')
    for m in range(base.shape[0]):
        base[m] = ocean_fill(base[m], land=land(), despike=False)
    print('base_sst  %s  范围 %.2f..%.2f' % (base.shape, np.nanmin(base), np.nanmax(base)))
    if save:
        meta = {'source': 'OISST v2.1 1991-2020 月气候态(全球 ±89.9)'
                          ' + CMEMS GLORYS 单日中尺度异常(仅 ±60)',
                'domain': 'global ±90 (0.5°)', 'grid': [NY, NX],
                'polar_note': '极区(|lat|>60)只有 OISST 气候态, 无中尺度异常'}
        _backup('base_sst.npz')
        np.savez_compressed(os.path.join(MODEL, 'base_sst.npz'),
                            sst=base.astype(np.float32),
                            meta=np.array(json.dumps(meta, ensure_ascii=False)))
        print('  -> base_sst.npz')
    return base


def build_ocn(save=True):
    """D26/TCHP: ±60 内嵌 + **按 26°C 等温线定义**归零(极区=0 是定义, 不是伪造)。"""
    from simulator.simcore.fill import ocean_fill
    # 冷掩码直接用**已经重建好的全球** base_sst(同一份 OISST + 中尺度异常, 更贴近模型实际看到的 SST)
    with np.load(os.path.join(MODEL, 'base_sst.npz')) as z:
        sst90 = np.asarray(z['sst'], np.float32)
    if sst90.shape[-2:] != (NY, NX):
        raise SystemExit('base_sst 还不是 ±90 网格(%s) —— 先跑 --sst' % (sst90.shape,))
    cold = sst90 < 26.0                                 # 26°C 等温线以外
    out = {}
    for nm, key, bias in (('base_d26', 'd26', 9.0), ('base_tchp', 'tchp', 0.0)):
        with np.load(os.path.join(MODEL, nm + '.npz')) as z:
            a60 = np.asarray(z[key], np.float32)
            meta = json.loads(str(z['meta'].item())) if 'meta' in z.files else {}
        raw = _embed60(a60 - bias) if bias else _embed60(a60)
        n_before = int((raw[~cold] <= 0.5).sum())
        raw[cold] = 0.0                                 # 定义: 没有 26°C 水 → 深度/热含量为 0
        for m in range(raw.shape[0]):
            raw[m] = ocean_fill(raw[m], land=land(), despike=False)
        val = raw + bias
        out[key] = val
        print('%s  %s  0 值格 %d/%d (%.0f%%)  范围 %.1f..%.1f  [热侧仍有值的格: %d]'
              % (nm, val.shape, int((val <= bias + 0.5).sum()), val.size,
                 100.0 * (val <= bias + 0.5).mean(), val.min(), val.max(), n_before))
        if save:
            meta = dict(meta)
            meta.update({'domain': 'global ±90 (0.5°)',
                         'polar_rule': 'SST<26°C → D26/TCHP=0(26°C 等温线的定义, 不是插值/伪造)',
                         'grid': [NY, NX]})
            _backup(nm + '.npz')
            np.savez_compressed(os.path.join(MODEL, nm + '.npz'),
                                **{key: val.astype(np.float32)},
                                meta=np.array(json.dumps(meta, ensure_ascii=False)))
            print('  -> %s.npz' % nm)
    return out



def embed_lat(a, src_lat, src_lon=None, ny=None, nx=None, lat0=LAT0, lat1=LAT1):
    """±60 的粗场 → 目标纬向网格(纬度先把边界行补到 ±90, 再双线性重采样)。

    第一版只改了纬度、**没动经度** —— 结果 base_other 出来是 (180, 144)(NCEP 2.5° 的经度),
    与内核广播不上。现在两个轴都走 resample; 极区靠"在源两端各补一行 ±90、值取边界行"
    实现 hold(两个端点同值 ⇒ 中间线性插值就是常数, 不会外推跑飞)。
    """
    a = np.asarray(a, np.float32)
    la = np.asarray(src_lat, float)
    if la[0] > la[-1]:
        a, la = a[..., ::-1, :], la[::-1]
    if la[0] > lat0:
        a = np.concatenate([a[..., :1, :], a], axis=-2)
        la = np.concatenate([[lat0], la])
    if la[-1] < lat1:
        a = np.concatenate([a, a[..., -1:, :]], axis=-2)
        la = np.concatenate([la, [lat1]])
    lon = (np.asarray(src_lon, float) if src_lon is not None
           else np.arange(a.shape[-1], dtype=float))
    return resample(a, la, lon, ny=(CLDNY if ny is None else ny),
                    nx=(CLDNX if nx is None else nx), lat0=lat0, lat1=lat1)


def build_atmos(save=True, suffix='_g'):
    """NCEP/NCAR R1 全球月均 → base_wind / base_other / base_shear。"""
    def _conv(arr, la, lo, d, coarse=True):
        ny = CLDNY if coarse else NY
        nx = CLDNX if coarse else NX
        if d.get('_interim'):
            return embed_lat(arr, la, lo, ny=ny, nx=nx)
        return resample(arr, la, lo, ny=ny, nx=nx)

    def load(nm):
        """优先全球缓存 ; 没有就用旧的 ±60 缓存 + embed_lat 正确定位(极区取边界行)。"""
        fp = os.path.join(CACHE, 'ncep_%s%s.npz' % (nm, suffix))
        g = os.path.exists(fp)
        if not g:
            fp = os.path.join(CACHE, 'ncep_%s.npz' % nm)
            if not os.path.exists(fp):
                return None
            print('  (临时: 用未扩域的 %s, 极区取边界行 —— 全球版抓完请重跑 --atmos)'
                  % os.path.basename(fp))
        z = np.load(fp)
        d = {k: z[k] for k in z.files}
        if not g:
            d['_interim'] = True
        return d

    def monthly(d, key):
        mon = d['month']
        return np.stack([d[key][mon == m].mean(0) for m in range(1, 13)])

    uvl, vvl = load('uwnd_lv'), load('vwnd_lv')
    if uvl is None or vvl is None:
        print('!! 缺 ncep_uwnd_lv%s / ncep_vwnd_lv%s —— 先跑 tools/fetch_ncep_plev.py --lat0 -90 --lat1 90 --suffix _g'
              % (suffix, suffix))
    else:
        la, lo = uvl['lat'], uvl['lon']
        wind = {}
        for L in (850, 500, 300, 200):
            ku, kv = 'uwnd%d' % L, 'vwnd%d' % L
            if ku not in uvl or kv not in vvl:
                continue
            wind['u%d' % L] = _conv(monthly(uvl, ku), la, lo, uvl, coarse=True) * 1.94384
            wind['v%d' % L] = _conv(monthly(vvl, kv), la, lo, vvl, coarse=True) * 1.94384
        if len(wind) >= 8 and save:
            _backup('base_wind.npz')
            np.savez_compressed(os.path.join(MODEL, 'base_wind.npz'),
                                **{k: v.astype(np.float16) for k, v in wind.items()})
            print('base_wind %s  u850 %.1f..%.1f kt' % (wind['u850'].shape,
                                                        wind['u850'].min(), wind['u850'].max()))
        # 切变 = |V200 - V850|(与内核口径一致: 瞬时/月均都不再乘 0.55)
        if 'u200' in wind and 'u850' in wind:
            sh = np.hypot(_conv(monthly(uvl, 'uwnd200'), la, lo, uvl, coarse=False) -
                          _conv(monthly(uvl, 'uwnd850'), la, lo, uvl, coarse=False),
                          _conv(monthly(vvl, 'vwnd200'), la, lo, vvl, coarse=False) -
                          _conv(monthly(vvl, 'vwnd850'), la, lo, vvl, coarse=False)) * 1.94384
            if save:
                _backup('base_shear.npz')
                np.savez_compressed(os.path.join(MODEL, 'base_shear.npz'),
                                    shear=sh.astype(np.float16))
            print('base_shear %s  %.1f..%.1f kt(全球, 由 NCEP 200/850 层风算)' %
                  (sh.shape, sh.min(), sh.max()))
    rh = load('rhum_lv')
    slp = load('slp')
    air = load('air200_lv')
    other = {}
    if rh is not None and 'rhum700' in rh:
        other['rh700'] = _conv(monthly(rh, 'rhum700'), rh['lat'], rh['lon'], rh, True)
    if slp is not None and 'slp' in slp:
        # **单位自适应**: NCEP 这个缓存的 slp 已经是 hPa(实测 955..1080),
        # 我却按 Pa 除了 100 → 气压场变成 ~10 hPa, 直接把 pEnv 相关测试打红。
        _sl = _conv(monthly(slp, 'slp'), slp['lat'], slp['lon'], slp, True)
        if float(np.nanmean(_sl)) > 5000.0:
            _sl = _sl / 100.0
        other['mslp'] = _sl
    if air is not None and 'air200' in air:
        other['t200'] = (_conv(monthly(air, 'air200'), air['lat'], air['lon'], air, True)
                         - 273.15)
    if other and save:
        _backup('base_other.npz')
        np.savez_compressed(os.path.join(MODEL, 'base_other.npz'),
                            **{k: v.astype(np.float16) for k, v in other.items()})
        print('base_other %s  %s' % (list(other.values())[0].shape, sorted(other)))
    return other


def _backup(fn, day=None):
    b = os.path.join(MODEL, '_bak_%s' % (day or time.strftime('%Y%m%d')))
    os.makedirs(b, exist_ok=True)
    src = os.path.join(MODEL, fn)
    dst = os.path.join(b, fn)
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy2(src, dst)
        print('  (旧 %s 已备份)' % fn)


def check():
    for nm, keys in (('base_sst', ('sst',)), ('base_d26', ('d26',)), ('base_tchp', ('tchp',)),
                     ('base_shear', ('shear',)), ('base_wind', ('u850',)), ('base_other', ('rh700',))):
        p = os.path.join(MODEL, nm + '.npz')
        if not os.path.exists(p):
            print('%-11s 缺' % nm)
            continue
        with np.load(p) as z:
            a = np.asarray(z[keys[0]], np.float32)
        ok = a.shape[-2:] in ((NY, NX), (CLDNY, CLDNX))
        print('%-11s %s  范围 %.2f..%.2f  %s' % (nm, a.shape, np.nanmin(a), np.nanmax(a),
                                                 'OK' if ok else '**形状与内核不符**'))
    with np.load(os.path.join(MODEL, 'land_cell.npz')) as z:
        print('%-11s %s' % ('land_cell', z['landfrac'].shape))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--sst', action='store_true')
    ap.add_argument('--ocn', action='store_true')
    ap.add_argument('--atmos', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--suffix', default='_g')
    a = ap.parse_args(argv)
    if not any((a.sst, a.ocn, a.atmos, a.check)):
        a.check = True
    if a.sst:
        build_sst()
    if a.ocn:
        build_ocn()
    if a.atmos:
        build_atmos(suffix=a.suffix)
    if a.check:
        check()
    return 0


if __name__ == '__main__':
    main()
