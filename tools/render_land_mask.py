# -*- coding: utf-8 -*-
"""只做一件事: 渲染一张与陆地完全对齐的陆地掩码。

关键: map.png 与 heightmap.png 是**同一像素空间**(都是 10800x5400、北在上、经度 0..360),
所以直接 land = heightmap > 12000, 无需任何翻转/缩放/坐标换算, 天然像素对齐。
输出: output/land_mask_check.png(底图 + 红色半透明陆地) 与 land_mask_only.png(纯掩码)
"""
import os
import numpy as np
from PIL import Image
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = Image.open(os.path.join(ROOT, 'map', 'map.png')).convert('RGB')
HM = Image.open(os.path.join(ROOT, 'map', 'heightmap.png'))
print('map.png     ', MAP.size, MAP.mode)
print('heightmap   ', HM.size, HM.mode)
a_map = np.asarray(MAP)
a_hm = np.asarray(HM)
print('阵列形状 map %s  heightmap %s' % (a_map.shape, a_hm.shape))
assert a_map.shape[:2] == a_hm.shape[:2], '两张图尺寸必须一致'
land = a_hm > 12000                      # 编码: 值 = 高程(米) + 12000
print('陆地占比 %.4f' % land.mean())

# 像素级校验(北在上: row0 = +90N, col0 = 0E)
def q(lon, lat):
    c = int(lon % 360 / 360 * land.shape[1]); r = int((90.0 - lat) / 180 * land.shape[0])
    return bool(land[r, c])
for nm, lo, la, exp in (('新西兰(173E,41S)', 173, -41, True), ('日本(140E,36N)', 140, 36, True),
                        ('中国(110E,35N)', 110, 35, True), ('澳洲(135E,25S)', 135, -25, True),
                        ('中太平洋(175E,20N)', 175, 20, False), ('南太平洋(240E,30S)', 240, -30, False)):
    got = q(lo, la)
    print('  %-20s %s (期望 %s) %s' % (nm, got, exp, 'OK' if got == exp else '<< 不一致'))

# 1) 底图 + 红色半透明陆地
blend = a_map.astype(np.float32)
blend[land] = blend[land] * 0.45 + np.array([255, 0, 0], np.float32) * 0.55
out1 = Image.fromarray(blend.astype(np.uint8)).resize((2160, 1080), Image.LANCZOS)
out1.save(os.path.join(ROOT, 'output', 'land_mask_check.png'))
# 2) 纯掩码(白=陆地 黑=海洋)
out2 = Image.fromarray((land * 255).astype(np.uint8)).resize((2160, 1080), Image.NEAREST)
out2.save(os.path.join(ROOT, 'output', 'land_mask_only.png'))
print('-> output/land_mask_check.png  (底图+红色陆地)')
print('-> output/land_mask_only.png   (纯掩码)')
