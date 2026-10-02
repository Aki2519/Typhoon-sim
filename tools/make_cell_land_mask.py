# -*- coding: utf-8 -*-
"""生成**格点级陆地占比**并缓存, 供"全陆格点不再模拟/不再画"复用。

用户 2026-09-19:
  "图中有因为小岛造成的空缺。检查, 如果陆地占比达到 100% 再不模拟这个格子。
   检查一次后存起来方便复用。"

做法:
  heightmap.png(30 px/度, 北在上, -90..90) → 取 -60..60 那一带 → 与内核 0.5° 网格
  (ENVNY×ENVNX = 240×720)对齐, 每格 = 6×6 个 heightmap 像素 → 求陆地占比。

输出: simulator/env/model/land_cell.npz
  landfrac  (240, 720) float32   0..1, 南在上(row0 = -60, 与内核一致)
  all_land  (240, 720) bool      landfrac >= 1.0(即 100% 陆地, 该格不模拟)
  meta      json 字符串          来源/阈值/统计, 便于日后核对

用法:
  python tools/make_cell_land_mask.py            # 生成/刷新缓存
  python tools/make_cell_land_mask.py --check    # 只报告现有缓存
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HEIGHT = os.path.join(ROOT, 'map', 'heightmap.png')
OUT = os.path.join(ROOT, 'simulator', 'env', 'model', 'land_cell.npz')
# **从内核常量取网格**(用户 2026-09-21 要求扩域到 ±90): 原来写死 240×720/±60,
# 扩域后掩码会与场错位。现在 360×720, 覆盖 90S..90N。
sys.path.insert(0, ROOT)
from simulator.simcore import core as _V          # noqa: E402
ENVNY, ENVNX = int(_V.ENVNY), int(_V.ENVNX)
CELL_PX = 6                      # heightmap 在该网格下每格 6×6 像素(30 px/度 ÷ 2)

# heightmap 编码: 像素 = 高程(米) + 12000 → 陆地 <=> > 12000
LAND_OFFSET = 12000
# heightmap 行 = (90 - lat) * 30(北在上, 30 px/度): ±90 就是整张图 0..5400。
# (原来取 900..4500 = 60N..60S; 扩域后必须取全球)
BAND = (0, 5400)


def build() -> dict:
    from PIL import Image
    im = np.asarray(Image.open(HEIGHT))
    if im.ndim == 3:
        im = im[..., 0]
    band = im[BAND[0]:BAND[1]]                       # 5400×10800, 90N..90S, 北在上
    land = band > LAND_OFFSET
    # 30 px/度 × 0.5° = 每格 15×15 像素; 3600/240 = 15, 10800/720 = 15 整除
    px = int(round(180.0 / ENVNY * 30))              # 180°/ENVNY 度每格 × 30 px/度
    ny, nx = band.shape[0] // px, band.shape[1] // px
    land = land[:ny * px, :nx * px]
    frac_nu = land.reshape(ny, px, nx, px).mean(axis=(1, 3)).astype(np.float32)
    # 采样到内核 0.5° 网格(此处 ny==ENVNY, nx==ENVNX)
    iy = (np.arange(ENVNY) * ny // ENVNY)
    ix = (np.arange(ENVNX) * nx // ENVNX)
    block = frac_nu[np.ix_(iy, ix)]
    landfrac = np.ascontiguousarray(np.flipud(block))     # 南在上(与内核 row0 一致)
    all_land = landfrac >= 1.0 - 1e-6
    meta = {
        'source': 'map/heightmap.png',
        'band_rows': list(BAND), 'cell_px': CELL_PX,
        'threshold': 'landfrac >= 1.0 → all_land(该格不模拟)',
        'n_all_land': int(all_land.sum()), 'n_cell': int(all_land.size),
        'n_partial': int(((landfrac > 0) & ~all_land).sum()),
        'n_ocean': int((landfrac <= 0).sum()),
    }
    np.savez_compressed(OUT, landfrac=landfrac, all_land=all_land,
                        meta=json.dumps(meta, ensure_ascii=False))
    return meta


def check() -> dict:
    if not os.path.exists(OUT):
        return {}
    with np.load(OUT) as d:
        meta = json.loads(str(d['meta'])) if 'meta' in d else {}
        meta['_file'] = OUT
        meta['_mtime'] = os.path.getmtime(OUT)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description='格点级陆地占比缓存')
    ap.add_argument('--check', action='store_true', help='只报告现有缓存')
    a = ap.parse_args(argv)
    if a.check:
        m = check()
        print(json.dumps(m, ensure_ascii=False, indent=2) if m else '缓存不存在')
        return
    m = build()
    print('写出 %s' % OUT)
    print('  全陆格点(不模拟): %d / %d  部分陆: %d  纯海: %d'
          % (m['n_all_land'], m['n_cell'], m['n_partial'], m['n_ocean']))


if __name__ == '__main__':
    main()
