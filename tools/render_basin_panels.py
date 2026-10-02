# -*- coding: utf-8 -*-
"""WPAC / EPAC / NATL 的 2 月与 9 月 SST / OHC 图(数据模式, 真实气候态)。"""
import os, sys
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import numpy as np, pygame
pygame.init(); pygame.display.set_mode((64, 64))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from simulator.simcore import core as V
from simulator.simcore import render as R

BASINS = (('WPAC', 100.0, 180.0, 0.0, 45.0),
          ('EPAC', 200.0, 285.0, 0.0, 35.0),
          ('NATL', 260.0, 360.0, 0.0, 45.0))
SCALE = 8.0
MAP = pygame.image.load(os.path.join(ROOT, 'map', 'map.png'))
font = pygame.font.Font(os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf'), 20)
big_font = pygame.font.Font(os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf'), 26)

def region(lon0, lon1, lat0, lat1):
    w = int((lon1 - lon0) * SCALE); h = int((lat1 - lat0) * SCALE)
    x0 = int(lon0 % 360 / 360 * 10800); x1 = int(lon1 / 360 * 10800)
    y0 = int((90 - lat1) / 180 * 5400); y1 = int((90 - lat0) / 180 * 5400)
    x1 = x0 + (x1 - x0 if x1 > x0 else 10800 - x0)
    sub = MAP.subsurface(pygame.Rect(x0, y0, x1 - x0, y1 - y0)).copy()
    return pygame.transform.smoothscale(sub, (w, h))

for month in (2, 9):
    sim = V.init({'seed': 1, 'month': month, 'params': {'dataMode': True}})
    sim['t'] = 14 * 24 + 12          # 该月 15 日 12z 的瞬时场(不是月平均)
    V.env_update(sim)
    cols = []
    for name, lon0, lon1, lat0, lat1 in BASINS:
        panels = []
        for field in ('sst', 'ohc'):
            s = region(lon0, lon1, lat0, lat1)
            w, h = s.get_size()
            # 必须用**全局线性**映射(1 度 = SCALE 像素): _blit_layer 会按
            # (fx(LON0), fy(LAT1)) 整幅贴上, 用盆地相对映射会把宽度算成 0
            fx = lambda lo, a=lon0: (lo - a) * SCALE
            fy = lambda la, a=lat1: (a - la) * SCALE
            arr = R.render_env_layer(sim, field)
            R._blit_layer(s, arr, fx, fy, alpha=215, cache_key=None)
            s.blit(font.render('%s %s  %d月' % (name, field.upper(), month), True, (255, 240, 120)), (8, 5))
            panels.append(s)
        cols.append(panels)
    CW = max(p.get_width() for c in cols for p in c)
    CH = max(p.get_height() for c in cols for p in c)
    img = pygame.Surface((CW * 2 + 8, (CH + 6) * 3 + 40))
    img.fill((15, 15, 25))
    img.blit(big_font.render('%d 月  SST / OHC  (WPAC / EPAC / NATL)  数据模式' % month, True, (255, 255, 255)), (8, 6))
    for r, panels in enumerate(cols):
        for c, p in enumerate(panels):
            img.blit(p, (c * (CW + 8), 40 + r * (CH + 6)))
    # 统一归档到 output/sim/<年份>/(用户 2026-09-19)
    _y = str(sim.get('start') or '')[:4] or 'unknown'
    _od = os.path.join(ROOT, 'output', 'sim', _y)
    os.makedirs(_od, exist_ok=True)
    out = os.path.join(_od, 'basins_%02d.png' % month)
    pygame.image.save(img, out)
    print('->', out, img.get_size())
