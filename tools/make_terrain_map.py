# -*- coding: utf-8 -*-
"""从全球高程 GeoTIFF 生成 map/map.png 的"陆地地形版"底图。

源   : world_elevation_60arcsec_21600x10800.tif
       EPSG:4326, 经度 -180..180, 纬度 -90..90, int16, 60 arcsec, 含海底地形(海洋为负)
目标 : 与 map/map.png 同尺寸 10800x5400, 经度 0..360(与原底图一致)
       —— **只替换陆地像素(高程 > 0)**, 海洋沿用原底图像素:
          这样地图渲染管线一行不用改, map/land.png 掩码也继续有效。

用法: python tools/make_terrain_map.py [--source <tif>] [--preview-only]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DEFAULT = r'C:\Users\Yukioto\Pictures\地图\world_elevation_60arcsec_21600x10800.tif'
OUT = os.path.join(ROOT, 'map', 'map.png')
LEGACY = os.path.join(ROOT, 'map', 'map_legacy.png')
PREVIEW = os.path.join(ROOT, 'map', 'map_terrain_preview.png')
W, H = 10800, 5400
#: 高程色带 (米) 与颜色 —— 低地绿 -> 丘陵黄绿 -> 高原棕 -> 高山灰 -> 雪白
RAMP_Z = [0, 150, 600, 1500, 2800, 4200, 5200, 5786]
RAMP_C = [(56, 96, 58), (86, 126, 62), (140, 154, 74), (168, 148, 92),
          (150, 128, 96), (150, 148, 148), (225, 225, 228), (250, 250, 252)]


def build_elevation(src: str) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(src) as ds:
        print('源: %d x %d  %s  bounds=%s' % (ds.width, ds.height, ds.dtypes[0], ds.bounds))
        t = time.perf_counter()
        # 2x 平均降采样到 10800x5400(源 60 arcsec -> 目标 120 arcsec)
        el = ds.read(1, out_shape=(H, W), resampling=Resampling.average)
        print('降采样完成 %.1fs  dtype=%s' % (time.perf_counter() - t, el.dtype))
    el = np.asarray(el, dtype=np.int16)
    # 源经度 -180..180 -> 目标 0..360(与 map.png / MapView 一致): 整体左移半圈
    el = np.roll(el, W // 2, axis=1)
    print('经度对齐完成(-180..180 -> 0..360)')
    return el


def colorize(el: np.ndarray, row0: int, row1: int) -> np.ndarray:
    """把 [row0,row1) 行的高程染成 RGB(含晕渲)。高程单位米。"""
    pad0 = max(0, row0 - 1)
    pad1 = min(H, row1 + 1)
    sub = el[pad0:pad1].astype(np.float32)
    dzdy, dzdx = np.gradient(sub)
    # 光照: 西北 45 度(方位 315, 高度 45) —— 与地形图的常规画法一致
    az, alt = np.radians(315.0), np.radians(45.0)
    slope = np.pi / 2.0 - np.arctan(np.hypot(dzdx, dzdy) / (120.0 * 111.32))
    aspect = np.arctan2(dzdy, -dzdx)
    shade = (np.sin(alt) * np.sin(slope)
             + np.cos(alt) * np.cos(slope) * np.cos(az - aspect))
    shade = np.clip(shade, -1.0, 1.0)
    # 归一化并压对比度: 纯 hillshade 太灰, 与色带相乘后保留细节
    shade = 0.62 + 0.52 * shade
    z = np.clip(sub, 0.0, RAMP_Z[-1])
    out = np.empty(sub.shape + (3,), dtype=np.float32)
    for ch in range(3):
        out[..., ch] = np.interp(z, RAMP_Z, [c[ch] for c in RAMP_C])
    out *= shade[..., None]
    np.clip(out, 0, 255, out=out)
    off0 = row0 - pad0
    return out[off0:off0 + (row1 - row0)].astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=SRC_DEFAULT)
    ap.add_argument('--preview-only', action='store_true')
    a = ap.parse_args()
    if not os.path.exists(a.source):
        print('源文件不存在:', a.source); return 2

    import pygame
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    pygame.init(); pygame.display.set_mode((64, 64))
    t0 = time.perf_counter()
    el = build_elevation(a.source)
    land = el > 0
    print('陆地占比 %.3f' % float(land.mean()))

    base = pygame.image.load(OUT)
    print('现有底图: %s' % (base.get_size(),))
    if base.get_size() != (W, H):
        print('尺寸不符, 退出'); return 3
    rgb = pygame.surfarray.array3d(base).transpose(1, 0, 2).copy()   # (H, W, 3)
    if not a.preview_only:
        shutil.copy2(OUT, LEGACY)
        print('原底图已备份 ->', LEGACY)

    strip = 540
    for r0 in range(0, H, strip):
        r1 = min(H, r0 + strip)
        rgb[r0:r1][land[r0:r1]] = colorize(el, r0, r1)[land[r0:r1]]
        if (r0 // strip) % 3 == 0:
            print('  行 %5d/%d  %.0fs' % (r1, H, time.perf_counter() - t0))
    print('着色完成 %.1fs' % (time.perf_counter() - t0))

    surf = pygame.Surface((W, H))
    pygame.surfarray.blit_array(surf, rgb.transpose(1, 0, 2))
    pygame.image.save(surf, OUT)
    print('已写出 %s  %.1f MB' % (OUT, os.path.getsize(OUT) / 1e6))

    small = pygame.transform.smoothscale(surf, (W // 8, H // 8))
    pygame.image.save(small, PREVIEW)
    print('预览 -> %s' % PREVIEW)
    print('总耗时 %.1fs' % (time.perf_counter() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
