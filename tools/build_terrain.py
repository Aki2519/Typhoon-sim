# -*- coding: utf-8 -*-
"""生成 SimCore 山脉高度场 ``simulator/simcore/terrain_0.5.npz``(轮次 158)。

**程序化合成, 零下载**: 要素表(台湾中央山脉 / 吕宋科迪勒拉+马德雷山 / 日本本州脊
等 51 条定向高斯脊与峰)写在 ``simulator/simcore/terrain.py`` 的 ``FEATURES`` 里,
本脚本只负责: 合成 → 加陆地掩码 → 存盘 → 打印形状/统计量 + 位置自检。

与 ``tools/build_terrain_dem.py`` 的分工(两个文件互不冲突):
    build_terrain_dem.py  用仓库里既有的真 DEM(map/heightmap.png, 30 px/度)
                          → simulator/env/model/terrain_dem.npz
    **build_terrain.py**  程序化合成(不依赖任何外部数据/图片)
                          → simulator/simcore/terrain_0.5.npz(与 land_0.5.npz 同目录)
两者网格、对齐、字段名(``elev_mean``/``elev_max``)一致, 可互为校验。

输出字段:
    elev_mean (360,720) float32  格内(5×5 细采样)平均海拔 m, 海洋格 = 0
    elev_max  (360,720) float32  格内最高海拔 m,      海洋格 = 0
    landfrac  (360,720) float32  所用陆地占比(可追溯; --no-mask 时为全 1)
    lat0/lon0/d float32          网格原点与分辨率
    meta      json 字符串        要素表版本 / sub / 掩码来源 / 统计量

用法:
    python tools/build_terrain.py                # 生成(默认 sub=10, 带陆地掩码)
    python tools/build_terrain.py --sub 10       # 更细的格内采样
    python tools/build_terrain.py --no-mask      # 不掩码(海洋上会留假高程, 仅调试)
    python tools/build_terrain.py --check        # 只报告现有 npz
    python tools/build_terrain.py --print-table  # 只打印要素数值表
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from simulator.simcore import terrain as T          # noqa: E402

#: 陆地占比来源(与内核同网格同对齐; 由 tools/make_cell_land_mask.py 生成)
LAND_CELL = os.path.join(ROOT, 'simulator', 'env', 'model', 'land_cell.npz')

#: 位置自检: (名称, 经度, 纬度, 阈值, 比较符)。对齐错了这里会立刻炸。
CHECKS = (
    ('台湾中央山脉', 121.00, 23.45, 1500.0, '>'),
    ('吕宋科迪勒拉', 120.90, 17.00,  800.0, '>'),
    ('吕宋马德雷山', 122.05, 16.20,  600.0, '>'),
    ('日本本州脊',   137.80, 36.00,  800.0, '>'),
    ('富士山',       138.73, 35.36, 1500.0, '>'),
    ('太平洋中部',   180.00, 20.00,   10.0, '<'),
    ('南大洋 60S',   200.00, -60.00,  10.0, '<'),
    ('北大西洋',     320.00, 30.00,   10.0, '<'),
)


def load_landfrac():
    """读 land_cell.npz → (landfrac, 来源字符串); 没有则 (None, 原因)。"""
    if not os.path.exists(LAND_CELL):
        return None, 'missing(%s)' % LAND_CELL
    try:
        with np.load(LAND_CELL) as z:
            lf = np.asarray(z['landfrac'], dtype=np.float32)
    except Exception as exc:                                   # pragma: no cover
        return None, 'error(%s)' % exc
    if lf.shape != (T.ENVNY, T.ENVNX):
        return None, 'shape%s' % (lf.shape,)
    return lf, 'land_cell.npz'


def build(sub: int, use_mask: bool) -> dict:
    t0 = time.time()
    raw_mean, raw_max = T.synthesize(sub=sub)
    t_syn = time.time() - t0

    src = 'none(未掩码)'
    lf = np.ones((T.ENVNY, T.ENVNX), dtype=np.float32)
    if use_mask:
        got, src = load_landfrac()
        if got is None:
            raise SystemExit('陆地掩码不可用: %s —— 用 --no-mask 可强制不掩码' % src)
        lf = got
    mean, mx = T.apply_land_mask(raw_mean, raw_max, lf) if use_mask \
        else (raw_mean.astype(np.float32), raw_max.astype(np.float32))

    meta = {
        'source': '程序化合成(定向高斯脊 + 峰高, 见 simulator/simcore/terrain.py FEATURES)',
        'features_version': int(T.FEATURES_VERSION),
        'n_features': len(T.FEATURES),
        'sub': int(sub),
        'cell_deg': float(T.ENVD),
        'sub_deg': float(T.ENVD / max(1, sub)),
        'mask': bool(use_mask), 'mask_source': src,
        'grid': {'ENVNY': int(T.ENVNY), 'ENVNX': int(T.ENVNX), 'ENVD': float(T.ENVD),
                 'LAT0': float(T.LAT0), 'LON0': float(T.LON0)},
        'stats': {
            'raw_mean_max': float(raw_mean.max()), 'raw_max_max': float(raw_max.max()),
            'mean_max': float(mean.max()), 'max_max': float(mx.max()),
            'mean_avg': float(mean.mean()), 'n_land_cells': int((mx > 0).sum()),
        },
    }
    np.savez_compressed(T.terrain_file(), elev_mean=mean, elev_max=mx,
                        landfrac=lf,
                        lat0=np.float32(T.LAT0), lon0=np.float32(T.LON0),
                        d=np.float32(T.ENVD),
                        meta=json.dumps(meta, ensure_ascii=False))
    return {'meta': meta, 'sec_synth': t_syn, 'sec_total': time.time() - t0,
            'mean': mean, 'max': mx}


def report(sub: int, use_mask: bool) -> int:
    print('=' * 78)
    print('SimCore 山脉高度场 —— 程序化合成(轮次 158)')
    print('=' * 78)
    print('要素数值表: %d 个要素(定向高斯脊 R / 峰 P / 极冠 C)' % len(T.FEATURES))
    print(T.describe_features())
    print('-' * 78)

    r = build(sub, use_mask)
    m, x = r['mean'], r['max']
    print('写出 %s' % T.terrain_file())
    print('  形状   elev_mean %s  elev_max %s   dtype %s'
          % (m.shape, x.shape, x.dtype))
    print('  网格   格心 lat = %.2f + i·%.2f,  lon = %.2f + j·%.2f'
          % (T.lat_of_row(0), T.ENVD, T.lon_of_col(0), T.ENVD))
    print('  细采样 每格 %d×%d = %.3f° ≈ %.0f km'
          % (sub, sub, T.ENVD / sub, T.ENVD / sub * 111.32))
    print('  elev_mean  min %.1f  max %.1f  mean %.1f  m'
          % (m.min(), m.max(), m.mean()))
    print('  elev_max   min %.1f  max %.1f  mean %.1f  m'
          % (x.min(), x.max(), x.mean()))
    print('  非零格 %d / %d (%.2f%%)   NaN %d'
          % ((x > 0).sum(), x.size, 100.0 * (x > 0).sum() / x.size,
             int(np.isnan(m).sum() + np.isnan(x).sum())))
    print('  陆地掩码 %s (%s)' % ('开' if use_mask else '关', r['meta']['mask_source']))
    print('  耗时   合成 %.2fs  合计 %.2fs   文件 %.0f KB'
          % (r['sec_synth'], r['sec_total'],
             os.path.getsize(T.terrain_file()) / 1024.0))
    print('-' * 78)

    # 位置自检 —— 用 terrain 自己的采样器(它已与 core.env_bilinear 同口径)
    T.reset_cache()
    bad = []
    print('位置自检(在 elev_max 上双线性采样):')
    for nm, lo, la, thr, op in CHECKS:
        v = T.elev_at(lo, la, use_max=True)
        ok = (v > thr) if op == '>' else (v < thr)
        print('  %-14s lon=%7.2f lat=%6.2f  elev_max = %8.1f m   期望 %s %-7.0f %s'
              % (nm, lo, la, v, op, thr, 'OK' if ok else 'FAIL'))
        if not ok:
            bad.append(nm)
    if bad:
        print('位置自检失败: %s —— 经纬度对齐错了' % bad)
        return 1
    print('位置自检全部通过(%d/%d)' % (len(CHECKS), len(CHECKS)))
    print('=' * 78)
    return 0


def check() -> int:
    T.reset_cache()
    st = T.stats()
    if not st.get('exists'):
        print('缓存不存在: %s' % st['path'])
        return 1
    print(json.dumps({k: v for k, v in st.items() if k != 'meta'},
                     ensure_ascii=False, indent=2))
    print('meta: %s' % json.dumps(st.get('meta', {}), ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='SimCore 山脉高度场合成(轮次 158)')
    ap.add_argument('--sub', type=int, default=T.DEFAULT_SUB,
                    help='格内细采样数(sub×sub, 默认 %d)' % T.DEFAULT_SUB)
    ap.add_argument('--no-mask', action='store_true',
                    help='不加陆地掩码(海洋上会留假高程, 仅调试)')
    ap.add_argument('--check', action='store_true', help='只报告现有 npz')
    ap.add_argument('--print-table', action='store_true', help='只打印要素数值表')
    a = ap.parse_args(argv)
    if a.print_table:
        print(T.describe_features())
        return 0
    if a.check:
        return check()
    return report(max(1, int(a.sub)), not a.no_mask)


if __name__ == '__main__':
    raise SystemExit(main())
