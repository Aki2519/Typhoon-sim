# -*- coding: utf-8 -*-
"""把 DEM（60 arcsec）烘成**分层设色底图**。

配色 = 用户给的既有方案 + 从 `world_hypsometric_chunshu.png` 里海处取到的**洼地深绿**。

关键点：
    · 陆地按 LAND_BANDS 分段（硬边），6000 m 以上 HIGH_LAND
    · 海洋按 OCEAN_BANDS 分段，-5000 以下 DEEP_OCEAN
    · **洼地**（里海/死海/吐鲁番这种"陆地但低于海平面"）用 DEPRESSION 深绿 ——
      原来的方案里没记这一档，所以这些地方要么被涂成海洋色、要么没有颜色。
      判定：在 z<0 的连通域里，**连不到图边**的那几块 = 内陆洼地/内陆海。
    · 想抗锯齿就传 --ss 2（2× 超采样后降采样），代价是 ~4 倍时间。

用法：
    python tools/bake_basemap.py --out map/sample_1024.png --w 1024
    python tools/bake_basemap.py --out map/map_new.png --w 10800 --ss 2
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import rasterio
from PIL import Image
from scipy import ndimage

DEM = r'C:\Users\Yukioto\Pictures\地图\world_elevation_60arcsec_21600x10800.tif'

#: 洼地（从 world_hypsometric_chunshu.png 里海处取色：#005200）
DEPRESSION = (0x00, 0x52, 0x00)

#: 陆地色阶：(-下限, 上限, RGB)，lo/hi 都含 lo 不含 hi；最后一条含上界
LAND_BANDS = [
    (0, 50, (0x00, 0x8D, 0x24)),
    (50, 100, (0x2A, 0x7B, 0x50)),
    (100, 200, (0x5D, 0x89, 0x71)),
    (200, 300, (0x74, 0x9B, 0x86)),
    (300, 500, (0x78, 0xBA, 0x97)),
    (500, 750, (0x94, 0xDA, 0xAD)),
    (750, 1000, (0xF2, 0xF3, 0xA3)),
    (1000, 1500, (0xF7, 0xDA, 0x86)),
    (1500, 2000, (0xFF, 0x9A, 0x91)),
    (2000, 3000, (0xFF, 0x68, 0x5A)),
    (3000, 4000, (0xFF, 0x35, 0x22)),
    (4000, 6000, (0x83, 0x83, 0x83)),
]
HIGH_LAND = (0xEA, 0xED, 0xE9)
LAND_TOP = 6000

OCEAN_BANDS = [
    (-50, 0, (0x26, 0xF4, 0xFF)),
    (-100, -50, (0x00, 0xE8, 0xF5)),
    (-200, -100, (0x00, 0xD5, 0xE1)),
    (-500, -200, (0x00, 0xBA, 0xE5)),
    (-1000, -500, (0x00, 0xB0, 0xBC)),
    (-2000, -1000, (0x1D, 0x91, 0xFF)),
    (-5000, -2000, (0x00, 0x55, 0xC2)),
]
DEEP_OCEAN = (0x00, 0x15, 0x81)

#: 冰盖（格陵兰内陆 + 南极）。用户 2026-10-19："加冰盖"。
#: 原方案没有这一档，格陵兰/南极会被 3000-4000m 档涂成红色（实测确实如此）。
ICE = (0xE6, 0xEE, 0xF5)

#: 湖水色（从 lakes.py 同步；湖泊单独一档，不再按海底深度分色）
from 湖泊 import LAKE, lake_mask   # noqa: E402  （同目录工具模块）
OCEAN_BOTTOM = -5000


def _paint(rgb: np.ndarray, mask: np.ndarray, color) -> None:
    """把 mask 处涂成 color（rgb 是 HxWx3 uint8）"""
    rgb[mask] = np.array(color, dtype=np.uint8)


def sea_mask(z: np.ndarray) -> np.ndarray:
    """海洋 = z<0 且**连通到图边**的连通域；其余 z<0 的是内陆洼地/内陆海。"""
    neg = z < 0
    lab, n = ndimage.label(neg, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return np.zeros_like(neg)
    border = np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])
    border = border[border > 0]
    if border.size == 0:
        return np.zeros_like(neg)
    vals, counts = np.unique(border, return_counts=True)
    ocean_ids = set(vals[counts >= max(4, border.size * 0.002)].tolist())
    if not ocean_ids:                      # 兜底：取边上最大的那个
        ocean_ids = {vals[int(np.argmax(counts))]}
    return np.isin(lab, list(ocean_ids))


COASTLINE_PNG = r'C:/Users/Yukioto/Pictures/地图/世界地图/world_coastline_transparent.png'
_coast_cache = {}


def enclosed_water(z: np.ndarray) -> np.ndarray:
    """海岸线图围起来、且高程 < 0 的水域 = 内陆海（里海/黑海/波罗的海/亚速海…）。

    为什么要它：这份 DEM 只有高程，里海沿岸低地（−28 m 的**陆地**）和里海水面几乎
    一样高，纯靠高程/漫水分不开 —— 实测洼地北边一大片低地被当成水涂了。
    海岸线图里画了里海、黑海的岸线（贝加尔这种淡水湖没有），正好用来切干净。
    """
    from PIL import Image
    h, w = z.shape
    key = (h, w)
    line = _coast_cache.get(key)
    if line is None:
        try:
            im = Image.open(COASTLINE_PNG).convert('RGBA')
            if im.size != (w, h):
                im = im.resize((w, h), Image.NEAREST)
            line = np.array(im)[:, :, 3] > 0
        except Exception:
            line = np.zeros((h, w), dtype=bool)
        _coast_cache[key] = line
    if not line.any():
        return np.zeros((h, w), dtype=bool)
    line = ndimage.binary_dilation(line, np.ones((3, 3), bool))
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    seed = border & ~line
    outside = ndimage.binary_propagation(seed, mask=~line)
    enclosed = ~line & ~outside
    return enclosed & (z < 0)


def ice_mask(z: np.ndarray) -> np.ndarray:
    """冰盖掩膜：南极（lat<=-60 全部）+ 格陵兰内陆（lon -74..-11, lat>=59.5, z>=100）。

    格陵兰沿海低地（z<100）保留陆地色 —— 那里是冰盖边缘的裸岩/苔原，
    整块涂白反而假。掩膜做一次轻微高斯，避免边界像刀切。
    """
    h, w = z.shape
    lat = (90.0 - (np.arange(h) + 0.5) * (180.0 / h))[:, None]
    lon = (-180.0 + (np.arange(w) + 0.5) * (360.0 / w))[None, :]
    antarctic = lat <= -60.0
    greenland = (lat >= 59.5) & (lon >= -74.0) & (lon <= -11.0) & (z >= 100)
    # 冰岛落在格陵兰那个经度框里（-24..-13），会被误涂白 —— 实测第一版冰岛整块变白
    iceland = (lat >= 62.5) & (lat <= 67.5) & (lon >= -26.0) & (lon <= -12.0)
    greenland &= ~iceland
    m = (antarctic | greenland).astype(np.float32)
    m = ndimage.gaussian_filter(m, 1.2) > 0.5
    return m


def colorize(z: np.ndarray, *, supersample: int = 1) -> np.ndarray:
    h, w = z.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    # 封闭海（里海/黑海/波罗的海）由海岸线围合判定；矢量湖表里没有里海，
    # 所以这里必须保留 enclosed_water，否则里海会掉进"内陆洼地"变深绿。
    is_sea = sea_mask(z) | enclosed_water(z)
    is_lake, lake_surf = lake_mask(z, return_surface=True)
    is_lake &= ~is_sea
    inland_dep = (z < 0) & ~is_sea & ~is_lake

    # 海洋分段
    for lo, hi, col in OCEAN_BANDS:
        _paint(rgb, is_sea & (z >= lo) & (z < hi), col)
    _paint(rgb, is_sea & (z < OCEAN_BOTTOM), DEEP_OCEAN)
    # ⚠ 曾经在这里补过一行"z<最浅档下限也涂最浅色"，它把**所有深水**都覆盖成浅青
    #   （实测输出 65% 是 #26F4FF、深水 0 px）。深水由上面 DEEP_OCEAN 兜住，别再加。
    # 内陆洼地（里海/死海/吐鲁番…）
    _paint(rgb, inland_dep, DEPRESSION)
    # 陆地分段
    for lo, hi, col in LAND_BANDS:
        _paint(rgb, (z >= lo) & (z < hi), col)
    _paint(rgb, z >= LAND_TOP, HIGH_LAND)
    # 湖泊：**按深度分色**（湖面−湖底），跟海洋同一套色阶但只在湖范围里用。
    #   之前一律涂 LAKE 单色，贝加尔和一个小池塘一个颜色（用户："怎么都是浅蓝色"）。
    if is_lake.any():
        depth = np.clip(lake_surf - z, 0.0, None)          # 湖深（m）
        for lo, hi, col in OCEAN_BANDS:                     # 复用海深色阶
            _paint(rgb, is_lake & (depth < -lo) & (depth >= -hi), col)
        _paint(rgb, is_lake & (depth >= -OCEAN_BANDS[-1][0]), (0x00, 0x55, 0xC2))
        _paint(rgb, is_lake & (lake_surf < 0), DEEP_OCEAN)   # 极深的（贝加尔底部）
        _paint(rgb, is_lake & ~np.isfinite(depth), LAKE)     # 保险
    # 冰盖最后涂（要盖掉上面的红色高山档）
    _paint(rgb, ice_mask(z) & (z > 0), ICE)
    return rgb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--w', type=int, default=10800, help='输出宽度（高=宽/2）')
    ap.add_argument('--ss', type=int, default=1, help='超采样倍数（抗锯齿）')
    ap.add_argument('--smooth', type=float, default=0.9,
                    help='配色前对高程做一点高斯平滑（像素）；0=关。'
                         '不平滑的话深海会出现盐胡椒点（1px 深度跨档），实测很难看')
    args = ap.parse_args()

    w = args.w * max(1, args.ss)
    h = w // 2
    t0 = time.time()
    # ⚠ 用 **average** 而不是 bilinear：这是降采样（21600→几千），
    #   average 才是正确的面积平均，bilinear 会丢高频、海岸线发糊（第一版就是糊的）。
    res = rasterio.enums.Resampling.average if w < 21600 else rasterio.enums.Resampling.nearest
    with rasterio.open(DEM) as ds:
        z = ds.read(1, out_shape=(h, w), resampling=res)
    print('DEM 读取 %.1fs → %dx%d' % (time.time() - t0, w, h))

    if args.smooth and args.smooth > 0:
        z = ndimage.gaussian_filter(z.astype(np.float32), args.smooth)
        print('高程平滑 σ=%.2f px' % args.smooth)

    t0 = time.time()
    rgb = colorize(z, supersample=args.ss)
    print('配色 %.1fs' % (time.time() - t0))

    img = Image.fromarray(rgb, 'RGB')
    if args.ss > 1:
        img = img.resize((w // args.ss, h // args.ss), Image.BOX)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    img.save(args.out)
    # 统计
    a = np.array(img)
    print('输出 %s  %dx%d  %.1f MB' % (args.out, img.size[0], img.size[1],
                                     os.path.getsize(args.out) / 1e6))
    for name, col in (('洼地', DEPRESSION), ('海洋最浅', OCEAN_BANDS[0][2]),
                      ('深海', DEEP_OCEAN), ('高山', HIGH_LAND)):
        n = int(((a[:, :, 0] == col[0]) & (a[:, :, 1] == col[1]) &
                 (a[:, :, 2] == col[2])).sum())
        print('  %-6s #%02X%02X%02X  %8d px (%.2f%%)' % (name, *col, n, 100 * n / a[:, :, 0].size))


if __name__ == '__main__':
    main()
