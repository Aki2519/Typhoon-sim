# -*- coding: utf-8 -*-
"""按主程序模拟模式的真实渲染管线输出 WPAC/EPAC/NATL 的 SST / OHC / 风切 图。

与 app/ty_sim_mixins/sim_mode_mixin.py 一致: V.init + env_update + render_env_layer
+ _blit_layer(叠在主程序底图 map/map.png 上)。模拟时刻 = month0 月 14 日 06z。

用法(Windows Python, 需要 pygame):
  python tools/render_sim_basins.py --data 1 --tag dm1
  python tools/render_sim_basins.py --data 0 --tag dm0
输出: output/simmap_<field>_<tag>.png  (三盆地横排)
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import numpy as np                                          # noqa: E402
import pygame                                               # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from simulator.simcore import core as V                      # noqa: E402
from simulator.simcore import render as R                    # noqa: E402

BASINS = (('WPAC', 100.0, 180.0, 0.0, 50.0),
          ('EPAC', 220.0, 280.0, 0.0, 35.0),     # 140W..80W -> 220..280
          ('NATL', 260.0, 350.0, 0.0, 50.0),     # 100W..10W  -> 260..350
          # 全球: 0..360 × 60S..60N(内核计算域), 用户 2026-09-19 要求加上
          ('GLOBAL', 0.0, 360.0, -60.0, 60.0))
SCALE = 7.0
MAP = pygame.image.load(os.path.join(ROOT, 'map', 'map.png'))
FONT = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                     'MapleMono-NF-CN-Medium.ttf'), 20)
BIG = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                    'MapleMono-NF-CN-Medium.ttf'), 26)


def region(lon0, lon1, lat0, lat1):
    w = int((lon1 - lon0) * SCALE)
    h = int((lat1 - lat0) * SCALE)
    x0 = int(lon0 % 360 / 360 * 10800)
    x1 = int(lon1 / 360 * 10800)
    y0 = int((90 - lat1) / 180 * 5400)
    y1 = int((90 - lat0) / 180 * 5400)
    x1 = x0 + (x1 - x0 if x1 > x0 else 10800 - x0)
    sub = MAP.subsurface(pygame.Rect(x0, y0, x1 - x0, y1 - y0)).copy()
    return pygame.transform.smoothscale(sub, (w, h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=int, default=1)
    ap.add_argument('--outdir', default=None,
                    help='产物目录; 省略 = output/sim/<起始年份>/')
    ap.add_argument('--month', type=int, default=9)
    ap.add_argument('--t', type=float, default=13 * 24 + 6.0)
    ap.add_argument('--start', default=None,
                    help='绝对起始时刻 YYYY-MM-DD HH; 给了就用它(与 sim_obs_probe 对齐)')
    ap.add_argument('--tag', default='')
    ap.add_argument('--split', action='store_true',
                    help='每个域单独出一张(而不是横排拼图)')
    args = ap.parse_args()
    tag = args.tag or ('dm%d' % args.data)

    # 与 sim_obs_probe 保持一致: 现在默认起始时刻是 1997-09-16 12z,
    # 旧的 --month/--t 接口会算出别的日期, 所以优先走 --start。
    if args.start:
        init_kw = {'start': args.start, 'seed': 20250801}
    else:
        init_kw = {'seed': 20250801, 'month': args.month}
    _y = int(str(args.start)[:4]) if args.start else 1997 + (args.month - 1) // 12
    outdir = args.outdir or os.path.join(ROOT, 'output', 'sim', str(_y))
    os.makedirs(outdir, exist_ok=True)
    sim = V.init({**init_kw,
                  'params': {'dataMode': bool(args.data), 'dataClim': True,
                             'dataYear': None, 'dataDayCycle': 1.0}})
    sim['t'] = 0.0 if args.start else float(args.t)
    V.env_update(sim)
    print('dataState', sim.get('dataState'))

    for field in ('sst', 'ohc', 'shear'):
        panels = []
        for name, lon0, lon1, lat0, lat1 in BASINS:
            s = region(lon0, lon1, lat0, lat1)
            fx = lambda lo, a=lon0: (lo - a) * SCALE
            fy = lambda la, a=lat1: (a - la) * SCALE
            arr = R.render_env_layer(sim, field)
            R._blit_layer(s, arr, fx, fy, alpha=215, cache_key=None)
            s.blit(FONT.render('%s %s  %s' % (name, field.upper(), tag),
                               True, (255, 240, 120)), (8, 5))
            panels.append(s)
            if args.split:                      # 每域单独一张, 便于逐个看
                fp = os.path.join(outdir, 'basin_%s_%s_%s.png' % (name.lower(), field, tag))
                pygame.image.save(s, fp)
                print('  ->', fp, s.get_size())
        CW = max(p.get_width() for p in panels)
        CH = max(p.get_height() for p in panels)
        img = pygame.Surface((CW * len(panels) + 8 * (len(panels) - 1), CH + 40))
        img.fill((15, 15, 25))
        img.blit(BIG.render('%s  %s  dataMode=%d  (%s)'
                            % (field.upper(),
                               args.start or ('月序 %d t=%.0f' % (args.month, args.t)),
                               args.data, tag),
                            True, (255, 255, 255)), (8, 6))
        for k, p in enumerate(panels):
            img.blit(p, (k * (CW + 8), 40))
        out = os.path.join(outdir, 'simmap_%s_%s.png' % (field, tag))
        pygame.image.save(img, out)
        print('->', out, img.get_size())


if __name__ == '__main__':
    main()
