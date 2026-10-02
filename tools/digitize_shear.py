# -*- coding: utf-8 -*-
"""把 CIRA/CIMSS 风切图(彩色等值线)反演成"有利/中性/不利"分档网格。

CIRA 图只有彩色等值线(5/10/15 绿, 20 黄, 25/30 橙, 35+ 红), 没有填色场。
做法: 按颜色分出三类等值线像素 → 用欧氏距离变换把每一格归到最近的等值线类别
(等值线之间的场是连续的, 最近邻是这种图的标准重建方式) → 1° 多数类 → 面积占比。

地理定标: 用图内白色经纬网线(每 10°, 检测精度 ±1px), 与底部刻度文字交叉确认。

用法: python tools/digitize_shear.py
输出: output/ref_shear_<basin>.npz (cls, lon, lat)  +  output/shear_report.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFDIR = os.path.join(ROOT, 'output', 'ref_20260914')
OUT = os.path.join(ROOT, 'output')

# basin -> (文件, 经度原点x/经度, 纬度原点y/纬度, 每度像素)
SHEAR = {
    # GOES-EAST: 覆盖墨西哥湾/加勒比/北大西洋 (NATL)
    'NATL': ('wg8shr.jpg', 103.0, -90.0, 90.0, 40.0, 13.88),
    # GOES-WEST: 覆盖中东太平洋 (EPAC)
    'EPAC': ('wg9shr.jpg', 227.0, -170.0, 117.0, 40.0, 11.125),
    # HIMAWARI-9 MSSHR: 覆盖西北太平洋 (WPAC)
    'WPAC': ('wgmsshr-1.jpg', 143.0, 100.0, 141.0, 40.0, 13.90),
}

# 类别: 0=有利(绿) 1=中性(黄) 2=不利(橙/红/浅红)
CAPTION_ROWS = 40        # 底部说明条高度(不参与)


def classify_colors(a: np.ndarray) -> np.ndarray:
    r = a[..., 0].astype(int)
    g = a[..., 1].astype(int)
    b = a[..., 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = mx - mn
    cls = np.full(r.shape, -1, np.int8)
    strong = (sat > 55) & (mx > 100)
    green = strong & (g > r + 25) & (g > b + 25)
    yellow = strong & (r > 150) & (g > 150) & (b < 130) & (np.abs(r - g) < 70)
    warm = strong & (r > 110) & (r > g + 45) & (r > b + 45)
    cls[warm] = 2
    cls[yellow] = 1
    cls[green] = 0
    return cls


def build_field(cls: np.ndarray, land: np.ndarray, caption: int):
    """等值线 → 连续分档场: 在最近的两条**不同类**等值线之间做距离线性插值。

    直接"取最近等值线的类"会系统性偏向等值线更密的那一类(红/橙档位多, 面积被
    放大到 80%+)。按 d1/(d1+d2) 在最近两类之间插值, 与等值线密度无关。
    """
    H, W = cls.shape
    dists, classes = [], []
    for c in range(3):
        m = (cls == c)
        m[caption:, :] = False
        m[land] = False
        if m.sum() < 20:
            continue
        dists.append(ndimage.distance_transform_edt(~m))
        classes.append(np.full((H, W), c, np.int8))
    if len(dists) < 2:
        return np.full(cls.shape, -1, np.int8)
    D = np.stack(dists)                      # (k,H,W)
    C = np.stack(classes)
    order = np.argsort(D, axis=0)
    d1 = np.take_along_axis(D, order[0:1], 0)[0]
    d2 = np.take_along_axis(D, order[1:2], 0)[0]
    c1 = np.take_along_axis(C, order[0:1], 0)[0].astype(np.float32)
    c2 = np.take_along_axis(C, order[1:2], 0)[0].astype(np.float32)
    est = c1 + (c2 - c1) * (d1 / np.maximum(d1 + d2, 1e-6))
    out = np.full(cls.shape, -1, np.int8)
    out[est < 0.5] = 0
    out[(est >= 0.5) & (est < 1.5)] = 1
    out[est >= 1.5] = 2
    out[caption:, :] = -1
    out[land] = -1
    return out


def _caption_top(a: np.ndarray) -> int:
    """底部说明条上沿: 从下往上找连续"近黑占多数"的行。"""
    H = a.shape[0]
    dark = (a.max(2) < 45).mean(1)
    y = H - 1
    while y > 0 and dark[y] > 0.45:
        y -= 1
    return max(0, y - 1)


def main():
    sys.path.insert(0, ROOT)
    from simulator.simcore import core as V
    LANDG = V.build_land().reshape(V.ENVNY, V.ENVNX)
    report = {}
    grid = {}
    for basin, (fn, x0, lon0, y0, lat0, ppd) in SHEAR.items():
        a = np.asarray(Image.open(os.path.join(REFDIR, fn)).convert('RGB'))
        H, W, _ = a.shape
        cls = classify_colors(a)
        jj, ii = np.mgrid[0:H, 0:W]
        lon_px = lon0 + (ii - x0) / ppd
        lat_px = lat0 - (jj - y0) / ppd
        # 陆地掩码(粗)
        j = np.round(lat_px + 59.5).astype(int)
        i = np.round(lon_px % 360.0 - 0.5).astype(int) % V.ENVNX
        okj = (j >= 0) & (j < V.ENVNY)
        land = np.zeros((H, W), bool)
        land[okj] = (LANDG[np.clip(j, 0, V.ENVNY - 1)[okj], i[okj]] >= 0.5)
        cap = _caption_top(a)
        filled = build_field(cls, land, cap)
        print('   caption top row =', cap)

        # 1° 多数类
        lon_c = np.arange(np.floor(lon_px.min()) + 0.5, np.ceil(lon_px.max()), 1.0)
        lat_c = np.arange(np.floor(lat_px.min()) + 0.5, np.ceil(lat_px.max()), 1.0)
        out = np.full((lat_c.size, lon_c.size), -1, np.int8)
        ix = np.clip(np.round(lon_px - lon_c[0]).astype(int), 0, lon_c.size - 1)
        iy = np.clip(np.round(lat_px - lat_c[0]).astype(int), 0, lat_c.size - 1)
        ok = filled >= 0
        fi = (iy * lon_c.size + ix)[ok]
        fv = filled[ok]
        for c in range(3):
            hit = np.zeros(lat_c.size * lon_c.size, np.int32)
            np.add.at(hit, fi[fv == c], 1)
            m = hit.reshape(lat_c.size, lon_c.size)
            out = np.where((out < 0) & (m > 0), c, out)
        # 用出现次数最多的类覆盖
        cnt = np.zeros((lat_c.size, lon_c.size), np.int32)
        for c in range(3):
            hit = np.zeros(lat_c.size * lon_c.size, np.int32)
            np.add.at(hit, fi[fv == c], 1)
            m = hit.reshape(lat_c.size, lon_c.size)
            upd = m > cnt
            out = np.where(upd, c, out)
            cnt = np.where(upd, m, cnt)
        out[cnt == 0] = -1
        grid[basin] = (out, lon_c, lat_c)
        np.savez_compressed(os.path.join(OUT, 'ref_shear_%s.npz' % basin),
                            cls=out, lon=lon_c, lat=lat_c)
        vals = out[out >= 0]
        report[basin] = {
            'file': fn,
            'domain': [float(lon_c[0] - 0.5), float(lon_c[-1] + 0.5),
                       float(lat_c[0] - 0.5), float(lat_c[-1] + 0.5)],
            'frac_favorable': round(float((vals == 0).mean()), 3),
            'frac_neutral': round(float((vals == 1).mean()), 3),
            'frac_unfavorable': round(float((vals == 2).mean()), 3),
            'n_cells': int(vals.size),
        }
        print(basin, report[basin])

    # 模拟场同样分档(同一盆地框, 不做 cos 权重以便与参考的"格数占比"同口径)
    sim = np.load(os.path.join(OUT, 'sim_20260914_dm1.npz'))
    sim0 = np.load(os.path.join(OUT, 'sim_20260914_dm0.npz'))
    for basin, (out, lon_c, lat_c) in grid.items():
        box = (lon_c[0] - 0.5, lon_c[-1] + 0.5, max(0.0, lat_c[0] - 0.5),
               lat_c[-1] + 0.5)
        for tag, s in (('dm1', sim), ('dm0', sim0)):
            sh = s['shear']
            gl, gla = s['lon'], s['lat']
            il = np.nonzero((gl >= box[0] % 360) & (gl <= box[1] % 360))[0]
            if il.size == 0:
                # 跨 180 的盆地
                il = np.nonzero((gl >= box[0] % 360) | (gl <= box[1] % 360))[0]
            jl = np.nonzero((gla >= box[2]) & (gla <= box[3]))[0]
            sub = sh[np.ix_(jl, il)]
            land = s['land'][np.ix_(jl, il)] >= 0.5
            v = sub[~land]
            fr = [float((v < 15).mean()), float(((v >= 15) & (v < 25)).mean()),
                  float((v >= 25).mean())]
            report[basin]['sim_%s' % tag] = {
                'frac_favorable': round(fr[0], 3),
                'frac_neutral': round(fr[1], 3),
                'frac_unfavorable': round(fr[2], 3),
                'mean_kt': round(float(v.mean()), 1),
                'max_kt': round(float(v.max()), 1),
                'p90_kt': round(float(np.percentile(v, 90)), 1),
                'thresholds': 'fav<15, neu 15-25, unfav>=25 (kt)',
            }
    with open(os.path.join(OUT, 'shear_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('\n== 风切三类面积占比(参考 vs 模拟) ==')
    print('%-5s %-28s %6s %6s %6s' % ('basin', 'source', 'fav', 'neu', 'unfav'))
    for basin, r in report.items():
        print('%-5s %-28s %6.3f %6.3f %6.3f' % (
            basin, 'OBS(CIRA ' + r['file'] + ')',
            r['frac_favorable'], r['frac_neutral'], r['frac_unfavorable']))
        for tag in ('dm1', 'dm0'):
            d = r['sim_%s' % tag]
            print('%-5s %-28s %6.3f %6.3f %6.3f   mean %.1f p90 %.1f max %.1f kt' % (
                basin, 'SIM ' + tag, d['frac_favorable'], d['frac_neutral'],
                d['frac_unfavorable'], d['mean_kt'], d['p90_kt'], d['max_kt']))


if __name__ == '__main__':
    main()
