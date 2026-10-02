# -*- coding: utf-8 -*-
"""把参考观测图(REMSS SST / CMEMS OHC / CIRA 风切)反演成数值网格, 供与模拟场对比。

原理:
  1) 用图内刻度文字位置做经纬度定标(线性拟合, 稳健);
  2) 用右侧色标每一档标签所在行的颜色建立调色板(自标定, 不依赖外部色表);
  3) 逐像素按最近调色板颜色分类 → 数值; 陆地/白底/黑底按掩码剔除;
  4) 按 1°×1°(格心 .5 偏置, 与 simcore 内核网格一致)取中位数聚合。

用法: python tools/digitize_ref.py
输出: output/ref_grid_<basin>_<field>.npz  (val, lon, lat, mask)
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFDIR = os.path.join(ROOT, 'output', 'ref_20260914')
OUTDIR = os.path.join(ROOT, 'output')

# ── 各图定标(由 tools/digitize_ref.py 的刻度检测得到) ──────────────
# (文件名, 底轴刻度文字中心x列表, 对应经度列表, 左轴刻度文字中心y列表, 对应纬度列表)
SPECS = {
    'sst_wpac': ('remss_sst_wpac.png',
                 [65, 234, 398, 563, 727, 892, 1056, 1221],
                 [100, 110, 120, 130, 140, 150, 160, 170],
                 [68, 231, 395, 560, 725, 891], [50, 40, 30, 20, 10, 0],
                 'sst'),
    'sst_epac': ('remss_sst_epac.png',
                 [66, 288, 508, 728, 946, 1166, 1383],
                 [-140, -130, -120, -110, -100, -90, -80],
                 [174, 394, 613, 843], [30, 20, 10, 0],
                 'sst'),
    'sst_natl': ('remss_sst_natl.png',
                 [67, 215, 362, 507, 654, 799, 946, 1092, 1239, 1384],
                 [-100, -90, -80, -70, -60, -50, -40, -30, -20, -10],
                 [139, 285, 432, 578, 724, 881], [50, 40, 30, 20, 10, 0],
                 'sst'),
    'ohc_wpac': ('ohc_wpac.png',
                 [65, 234, 398, 563, 727, 892, 1056, 1221],
                 [100, 110, 120, 130, 140, 150, 160, 170],
                 [68, 231, 395, 560, 725, 891], [50, 40, 30, 20, 10, 0],
                 'ohc'),
    'ohc_epac': ('ohc_epac.png',
                 [66, 288, 508, 728, 946, 1166, 1383],
                 [-140, -130, -120, -110, -100, -90, -80],
                 [174, 394, 613, 843], [30, 20, 10, 0],
                 'ohc'),
    'ohc_natl': ('ohc_natl.png',
                 [67, 215, 362, 507, 654, 799, 946, 1092, 1239, 1384],
                 [-100, -90, -80, -70, -60, -50, -40, -30, -20, -10],
                 [139, 285, 432, 578, 724, 881], [50, 40, 30, 20, 10, 0],
                 'ohc'),
}

CBAR_X0, CBAR_X1 = 1400, 1423      # 色标条内区
LBL_X0, LBL_X1 = 1432, 1475        # 色标文字区


def _label_rows(img: np.ndarray, gap: int = 6, minpix: int = 4):
    dark = (img.max(2) < 150)
    band = dark[:, LBL_X0:LBL_X1]
    ys = np.nonzero(band.any(1))[0]
    if len(ys) == 0:
        return []
    out, s, p = [], ys[0], ys[0]
    for v in ys[1:]:
        if v - p > gap:
            if p - s >= minpix:
                out.append((s + p) // 2)
            s = v
        p = v
    if p - s >= minpix:
        out.append((s + p) // 2)
    return out


def _palette(img: np.ndarray, rows, values):
    """在色标条内区按每一档标签行取颜色(±3 行、横向中位数)。"""
    pal = []
    for y, v in zip(rows, values):
        y0, y1 = max(0, y - 3), min(img.shape[0], y + 4)
        blk = img[y0:y1, CBAR_X0 + 4:CBAR_X1 - 4, :].reshape(-1, 3)
        pal.append((np.median(blk, axis=0), float(v)))
    return pal


def _classify(img: np.ndarray, pal):
    h, w, _ = img.shape
    flat = img.reshape(-1, 3).astype(np.int32)
    best = np.full(flat.shape[0], 1e18)
    val = np.full(flat.shape[0], np.nan)
    for col, v in pal:
        c = np.asarray(col, np.float64)
        d = ((flat - c) ** 2).sum(1)
        m = d < best
        best[m] = d[m]
        val[m] = v
    # 距离阈值: 超过 30(欧氏)→ 视为陆地/背景/文字, 不参与
    val[best > 30 ** 2] = np.nan
    # 色度判据(关键): 网格线 / 文字 / 海岸线的抗锯齿边缘是**灰的**(R≈G≈B),
    # 而 SST 调色板每一个档位都带明显色偏(冷端是淡紫, B 比 R 高 16~49)。
    # 只按"离纯白多远"剔除会漏掉这些灰像素 —— 它们到 #adaede(3°C)的距离只有
    # ~39, 落在 40 的阈值内, 于是热带太平洋被标出一堆 1-3°C 的假冷点
    # (实测 EPAC 4.1% 的格点 <10°C, 直接毁掉了相关系数)。
    ch = flat.max(1) - flat.min(1)
    val[ch < 18] = np.nan
    # 近白像素 = 图上"无数据/陆地留白"(REMSS 产品把陆地与缺测都画成白),
    # 而 SST 冷端色 #dfe3ef(223,227,239) 与纯白距离 32 > 25, 不会被误杀
    dwhite = ((flat - 255) ** 2).sum(1)
    val[dwhite < 25 ** 2] = np.nan
    return val.reshape(h, w)


def _georef(xs, lons, ys, lats):
    """线性拟合: lon = a + b·x ; lat = c + d·y (对离群刻度做一次剔除)。"""
    bx = np.polyfit(np.asarray(xs, float), np.asarray(lons, float), 1)
    by = np.polyfit(np.asarray(ys, float), np.asarray(lats, float), 1)
    # 残差 > 6px 的点剔除后重拟合
    rx = np.abs(np.polyval(bx, xs) - np.asarray(lons, float))
    ry = np.abs(np.polyval(by, ys) - np.asarray(lats, float))
    kx = rx < 0.8
    ky = ry < 0.8
    if kx.sum() >= 3:
        bx = np.polyfit(np.asarray(xs, float)[kx], np.asarray(lons, float)[kx], 1)
    if ky.sum() >= 3:
        by = np.polyfit(np.asarray(ys, float)[ky], np.asarray(lats, float)[ky], 1)
    return bx, by, int(kx.sum()), int(ky.sum())


def _land_mask(lon_c, lat_c):
    """用项目自带 ne_110m_land.geojson 光栅化 1° 陆地掩码(True=陆)。

    内核网格: row j → lat -59.5+j, col i → lon 0.5+i(0-360)。
    """
    sys.path.insert(0, ROOT)
    from simulator.simcore import core as V
    m = V.build_land().reshape(V.ENVNY, V.ENVNX)
    j = np.round(np.asarray(lat_c, float) + 59.5).astype(int) % V.ENVNY
    i = np.round(np.asarray(lon_c, float) - 0.5).astype(int) % V.ENVNX
    return (m[np.ix_(j, i)] >= 0.5)


def digitize(key):
    fn, xs, lons, ys, lats, kind = SPECS[key]
    img = np.asarray(Image.open(os.path.join(REFDIR, fn)).convert('RGB'))
    H, W, _ = img.shape
    bx, by, nkx, nky = _georef(xs, lons, ys, lats)
    rows = _label_rows(img)
    # 第一行是单位标签(℃ / KJ/cm²), 其余为数值档
    rows = rows[1:]
    if kind == 'sst':
        values = list(range(33, 33 - len(rows), -1)) or []
    else:
        values = list(range(220, 220 - 10 * len(rows), -10))
    assert len(values) == len(rows), (key, len(rows), values)
    pal = _palette(img, rows, values)
    val = _classify(img, pal)
    # 像素 → 经纬度
    jj, ii = np.mgrid[0:H, 0:W]
    lon_px = bx[0] * ii + bx[1]
    lat_px = by[0] * jj + by[1]
    # 只保留图框内
    lon0, lon1 = min(lons), max(lons)
    lat0, lat1 = min(lats), max(lats)
    inb = (lon_px >= lon0 - 1) & (lon_px <= lon1 + 1) & \
          (lat_px >= lat0 - 1) & (lat_px <= lat1 + 1)
    val = np.where(inb, val, np.nan)

    # 目标 1° 网格(格心 .5)
    lon_c = np.arange(np.floor(lon0) + 0.5, np.ceil(lon1) + 0.5, 1.0)
    lat_c = np.arange(np.floor(lat0) + 0.5, np.ceil(lat1) + 0.5, 1.0)
    out = np.full((lat_c.size, lon_c.size), np.nan, np.float32)
    cnt = np.zeros_like(out, np.int32)
    # 像素按 1° 归类(向量化)
    ix = np.clip(np.round(lon_px - lon_c[0]).astype(np.int32), 0, lon_c.size - 1)
    iy = np.clip(np.round(lat_px - lat_c[0]).astype(np.int32), 0, lat_c.size - 1)
    ok = np.isfinite(val)
    # 中位数聚合: 用排序分组(网格数很少, 逐格取)
    flat_i = (iy * lon_c.size + ix)[ok]
    flat_v = val[ok]
    order = np.argsort(flat_i, kind='stable')
    fi, fv = flat_i[order], flat_v[order]
    bounds = np.searchsorted(fi, np.arange(lat_c.size * lon_c.size + 1))
    for g in range(lat_c.size * lon_c.size):
        a, b = bounds[g], bounds[g + 1]
        if b > a:
            out.reshape(-1)[g] = np.median(fv[a:b])
            cnt.reshape(-1)[g] = b - a
    land = _land_mask(lon_c, lat_c)
    out = np.where(land, np.nan, out)
    return out, lon_c, lat_c, bx, by, cnt, len(rows), rows


def main():
    meta = {}
    for key in SPECS:
        out, lon_c, lat_c, bx, by, cnt, n, rows = digitize(key)
        p = os.path.join(OUTDIR, 'ref_grid_%s.npz' % key)
        np.savez_compressed(p, val=out, lon=lon_c, lat=lat_c)
        finite = np.isfinite(out)
        meta[key] = {
            'file': p,
            'n_levels': n,
            'label_rows': [int(r) for r in rows],
            'georef_lon': [round(float(bx[0]), 6), round(float(bx[1]), 3)],
            'georef_lat': [round(float(by[0]), 6), round(float(by[1]), 3)],
            'coverage': round(float(finite.mean()), 3),
            'min': None if not finite.any() else round(float(np.nanmin(out)), 2),
            'max': None if not finite.any() else round(float(np.nanmax(out)), 2),
            'mean': None if not finite.any() else round(float(np.nanmean(out)), 2),
        }
        print(key, meta[key])
    with open(os.path.join(OUTDIR, 'ref_grid_meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print('-> output/ref_grid_*.npz')


if __name__ == '__main__':
    main()
