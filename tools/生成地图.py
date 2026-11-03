# -*- coding: utf-8 -*-
"""生成三张自制地图（**中文文件名**，用户 2026-10-19）：

    分层设色地形图.png   底图：分层设色（含冰盖、洼地、湖泊）
    经纬网格.png         叠加层：透明底经纬网 + 度数标注
    世界地图成图.png     成图：底图 + 叠加层

输出目录：`C:\\Users\\Yukioto\\Pictures\\地图\\世界地图\\`
尺寸与原图一致：10000×5000（等经纬投影，左上角 180°W / 90°N）。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 配色 as bb            # noqa: E402

OUT_DIR = r'C:\Users\Yukioto\Pictures\地图\世界地图'
DEM = os.path.join(OUT_DIR, 'world_elevation_60arcsec_21600x10800.tif')
底图 = os.path.join(OUT_DIR, '分层设色地形图.png')
叠加层 = os.path.join(OUT_DIR, '经纬网格.png')
成图 = os.path.join(OUT_DIR, '世界地图成图.png')
FONT = r'G:\气象\Typhoon-sim\assets\font\MapleMono-NF-CN-Medium.ttf'


def bake_base(w: int) -> Image.Image:
    h = w // 2
    res = rasterio.enums.Resampling.average if w < 21600 else rasterio.enums.Resampling.nearest
    with rasterio.open(DEM) as ds:
        z = ds.read(1, out_shape=(h, w), resampling=res).astype(np.float32)
    z = ndimage.gaussian_filter(z, 0.9)
    t0 = time.time()
    rgb = bb.colorize(z)
    print('配色 %.1fs' % (time.time() - t0))
    return Image.fromarray(rgb, 'RGB')


def make_overlay(w: int, h: int, step=10, label_step=30) -> Image.Image:
    ov = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    def X(lon):
        return int(round((lon + 180.0) / 360.0 * w))

    def Y(lat):
        return int(round((90.0 - lat) / 180.0 * h))

    try:
        f = ImageFont.truetype(FONT, max(11, w // 450))
    except Exception:
        f = ImageFont.load_default()

    for lon in range(-180, 181, step):
        x = X(lon)
        if 0 <= x < w:
            d.line([(x, 0), (x, h - 1)], fill=(255, 255, 255, 110 if lon == 0 else 70))
            if lon % label_step == 0 and lon not in (-180, 180):
                t = '0°' if lon == 0 else ('%d°E' % lon if lon > 0 else '%d°W' % -lon)
                d.text((x + 3, h // 2 + 3), t, font=f, fill=(255, 255, 255, 150))
    for lat in range(-80, 81, step):
        y = Y(lat)
        if 0 <= y < h:
            d.line([(0, y), (w - 1, y)], fill=(255, 255, 255, 110 if lat == 0 else 70))
            if lat % label_step == 0:
                t = '0°' if lat == 0 else ('%d°N' % lat if lat > 0 else '%d°S' % -lat)
                d.text((4, y + 2), t, font=f, fill=(255, 255, 255, 150))
    return ov


def 湖泊自检(base_img, z, w: int):
    """抽查若干著名湖/海的**中心**必须是水色（矢量湖泊数据下只要数据在就必然成立，
    这里留作回归：换配色/换数据时能立刻发现"湖没画出来"）。"""
    import numpy as np
    a = np.array(base_img.convert('RGB'))
    h = a.shape[0]
    water = {(0x26, 0xF4, 0xFF), (0x00, 0xE8, 0xF5), (0x00, 0xD5, 0xE1),
             (0x00, 0xBA, 0xE5), (0x00, 0xB0, 0xBC), (0x1D, 0x91, 0xFF),
             (0x00, 0x55, 0xC2), (0x00, 0x15, 0x81)}
    probes = [('苏必利尔', -87.9, 47.6), ('贝加尔', 108.2, 53.3), ('维多利亚', 33.0, -1.0),
              ('坦噶尼喀', 29.6, -6.0), ('马拉维', 34.5, -12.0), ('大熊湖', -121.0, 65.9),
              ('青海湖', 100.2, 36.9), ('里海', 50.5, 41.8), ('黑海', 34.0, 43.5),
              ('贝加尔西南', 104.6, 51.6)]
    bad = []
    for name, lon, lat in probes:
        x = int((lon + 180) / 360 * w)
        y = int((90 - lat) / 180 * h)
        if tuple(a[y, x]) not in water:
            bad.append('%s(#%02X%02X%02X)' % ((name,) + tuple(a[y, x])))
    print('湖泊自检：%d 个点，%d 个不是水色' % (len(probes), len(bad)))
    if bad:
        print('  异常:', ' '.join(bad))
    return bad


def main():
    w = 10000
    t0 = time.time()
    base = bake_base(w)
    print('底图 %dx%d 共 %.1fs' % (base.size[0], base.size[1], time.time() - t0))
    base.save(底图)
    ov = make_overlay(*base.size)
    ov.save(叠加层)
    cmp_ = base.convert('RGBA').copy()
    cmp_.alpha_composite(ov)
    cmp_.convert('RGB').save(成图)

    湖泊自检(base, None, w)

    a = np.array(base)
    for name, col in (('冰盖', bb.ICE), ('湖泊', bb.LAKE),
                      ('洼地', bb.DEPRESSION), ('深海', bb.DEEP_OCEAN)):
        n = int(((a[:, :, 0] == col[0]) & (a[:, :, 1] == col[1]) &
                 (a[:, :, 2] == col[2])).sum())
        print('  %-4s #%02X%02X%02X %9d px (%.2f%%)' % (name, *col, n, 100 * n / (a.shape[0] * a.shape[1])))
    for p in (底图, 叠加层, 成图):
        print('写入 %s  %.1f MB' % (os.path.basename(p), os.path.getsize(p) / 1e6))


if __name__ == '__main__':
    main()
