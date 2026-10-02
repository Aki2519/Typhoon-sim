# -*- coding: utf-8 -*-
"""全球流速图: 底图 + 流速色带(证明 OSCAR 场是全球的)。"""
import os, sys
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import numpy as np, pygame
pygame.init(); pygame.display.set_mode((64, 64))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from simulator.simcore import render as R
d = np.load(os.path.join(ROOT, 'simulator', 'env', 'climate', 'currents_2019.npz'))
spd = np.hypot(d['u'], d['v']) * 1.944
step = np.clip(spd / 2.5, 0, 1)
rgba = np.dstack([(255 * step).astype(np.uint8),
                  (255 * np.sqrt(step)).astype(np.uint8),
                  (255 * (1 - step) ** 2).astype(np.uint8),
                  np.where(spd > 0.05, 215, 0).astype(np.uint8)])
rgba = rgba[::-1]                                   # 南在上 -> 北在上
base = pygame.image.load(os.path.join(ROOT, 'map', 'map.png'))
base = pygame.transform.smoothscale(base, (2160, 1080))
fx = lambda lo: lo / 360.0 * 2160.0
fy = lambda la: (90.0 - la) / 180.0 * 1080.0
R._blit_layer(base, rgba, fx, fy, alpha=255, cache_key=None)
font = pygame.font.Font(os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf'), 22)
base.blit(font.render('OSCAR 2019 表层流(kt) — 全球', True, (255, 255, 0)), (10, 8))
_od = os.path.join(ROOT, 'output', 'sim', '2019')      # 洋流资料年份(用户 2026-09-19 归档口径)
os.makedirs(_od, exist_ok=True)
pygame.image.save(base, os.path.join(_od, 'currents_global.png'))
print('-> output/currents_global.png', base.get_size())
