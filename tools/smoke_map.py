# -*- coding: utf-8 -*-
"""P1 冒烟: 用真实新底图构造 MapView 并画一帧(确认换图没破坏现有渲染管线)。"""
import os, sys, time
os.environ.setdefault('SDL_VIDEODRIVER','dummy')
import pygame
pygame.init(); pygame.display.set_mode((1280, 800))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.constants import DEFAULT_MAP
from app.map_mgr import MapView
t0=time.perf_counter()
mv = MapView(DEFAULT_MAP, 0.0, 360.0, -90.0, 90.0, 1280, 800)
print('MapView 构造 %.2fs  min_scale=%.4f  img=%s' % (time.perf_counter()-t0, mv.min_scale, (mv.img_w, mv.img_h)))
mv.scale = mv.min_scale * 1.7
mv.view_x = 40.0; mv.view_y = 10.0
mv._cached_scale = -1.0
surf = pygame.Surface((1280, 800))
mv.draw(surf)
arr = pygame.surfarray.array3d(surf)
print('draw 非黑像素 %d  均值亮度 %.1f' % (int((arr.sum(axis=2)>0).sum()), arr.mean()))
# 经纬映射一致性(跨缝/两极)
for la, lo in ((0.0, 0.0), (0.0, 359.0), (30.0, 140.0), (-30.0, 300.0)):
    x = mv.geo_to_map(lo, la); s = mv.geo_to_screen(lo, la)
    print('  (%6.1f,%5.1f) -> map %s  screen %s' % (lo, la, x, s))
print('OK')
