# -*- coding: utf-8 -*-
"""生成 map/heightmap.png(含海洋的全高程) 与 map/land.png(纯黑陆地+透明海洋)。

约定: 北在上, 经度 0..360, 10800x5400 —— 与 map.png 完全一致。
heightmap 用 16bit 灰度, 值 = 高程(米) + 12000 偏移(这样负水深也能存进无符号 PNG),
读取时 elev = val - 12000。
"""
import os, time
import numpy as np, rasterio
from rasterio.enums import Resampling
from PIL import Image
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r'C:\Users\Yukioto\Pictures\地图\world_elevation_60arcsec_21600x10800.tif'
H, W = 5400, 10800
OFF = 12000
t0 = time.perf_counter()
with rasterio.open(SRC) as ds:
    z = ds.read(1, out_shape=(H, W), resampling=Resampling.average)
z = np.asarray(z, dtype=np.int32)
z = np.roll(z, W // 2, axis=1)          # 经度偏移 180 度(-180..180 -> 0..360)
print('高程范围 %d .. %d 米' % (z.min(), z.max()))

val = np.clip(z + OFF, 0, 65535).astype(np.uint16)
p1 = os.path.join(ROOT, 'map', 'heightmap.png')
Image.fromarray(val).save(p1, compress_level=6)     # PIL: (高, 宽) 不再 transpose
print('heightmap.png %s  %.1f MB  mode=%s'
      % (val.shape, os.path.getsize(p1) / 1e6, Image.open(p1).mode))

rgba = np.zeros((H, W, 4), dtype=np.uint8)          # 纯黑陆地 + 透明海洋
rgba[..., 3] = np.where(z > 0, 255, 0).astype(np.uint8)
p2 = os.path.join(ROOT, 'map', 'land.png')
Image.fromarray(np.ascontiguousarray(rgba), 'RGBA').save(p2, compress_level=6)
print('land.png %.1f MB  陆地占比 %.3f' % (os.path.getsize(p2) / 1e6, (z > 0).mean()))
print('总耗时 %.1fs' % (time.perf_counter() - t0))
