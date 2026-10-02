# -*- coding: utf-8 -*-
"""生成 2 月 / 9 月 WPAC 的 SST / OHC / 风切 对照图(自检用)。"""
import os, sys, time
os.environ.setdefault('SDL_VIDEODRIVER','dummy'); os.environ.setdefault('SDL_AUDIODRIVER','dummy')
import numpy as np, pygame
pygame.init(); pygame.display.set_mode((64,64))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from simulator.simcore import core as V
from simulator.simcore import render as R

BOX_LON, BOX_LAT = (100.0, 180.0), (0.0, 50.0)
W, H = 1000, 620
OUT = os.path.join(ROOT, 'output')


def fx(lon):
    return (lon - BOX_LON[0]) / (BOX_LON[1] - BOX_LON[0]) * W


def fy(lat):
    return (BOX_LAT[1] - lat) / (BOX_LAT[1] - BOX_LAT[0]) * H


BASE = pygame.image.load(os.path.join(ROOT, 'map', 'map.png'))


def base_region():
    x0 = int(BOX_LON[0] / 360.0 * 10800); x1 = int(BOX_LON[1] / 360.0 * 10800)
    y0 = int((90.0 - BOX_LAT[1]) / 180.0 * 5400); y1 = int((90.0 - BOX_LAT[0]) / 180.0 * 5400)
    sub = BASE.subsurface(pygame.Rect(x0, y0, x1 - x0, y1 - y0)).copy()
    return pygame.transform.smoothscale(sub, (W, H))


DATA_MODE = os.environ.get('SIM_DATA', '1') != '0'


def build(month, days=12.0, n_tc=4, seed=3):
    # 默认用真实气候库驱动 SST/OHC/风切(解析场只有纬向条纹, 与观测差太远)
    sim = V.init({'seed': seed, 'month': month,
                  'params': {'dataMode': DATA_MODE}})
    V.env_update(sim)
    for i in range(n_tc):
        tc = V.make_tc(sim, 128.0 + i * 11, 12.0 + i * 4, 45.0 + i * 12)
        sim['tcs'].append(tc)
    n = int(days * 24.0 / V.C['DT'])
    t0 = time.perf_counter()
    for k in range(n):
        V.sim_step(sim, V.C['DT'])
    print('  %2d 月: 跑 %.0f 天(%d 步) 用时 %.1fs  存活 %d'
          % (month, days, n, time.perf_counter() - t0,
             len([t for t in sim['tcs'] if not t['dead']])))
    return sim


def panel(sim, layer):
    s = base_region()
    if layer == 'shear':
        R.draw_shear_contours(s, sim, fx, fy)
    elif layer == 'current':
        R.draw_currents(s, sim, fx, fy)
    else:
        arr = R.render_env_layer(sim, layer)
        R._blit_layer(s, arr, fx, fy, alpha=215, cache_key=None)
    return s


_FONT = None


def _font(size=20):
    """项目自带字体; SysFont 在本机 pygame-ce 2.5.7 上会崩(splitext 收到 int)。"""
    global _FONT
    if _FONT is None:
        p = os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf')
        _FONT = pygame.font.Font(p, size) if os.path.exists(p) else pygame.font.Font(None, size)
    return _FONT


def stamp(surf, text):
    f = _font()
    img = f.render(text, True, (255, 255, 255))
    bg = pygame.Surface((img.get_width() + 12, img.get_height() + 6))
    bg.set_alpha(150); bg.fill((0, 0, 0))
    surf.blit(bg, (6, 6)); surf.blit(img, (12, 9))


def render_month(month, tag, layers=(('sst', 'SST'), ('ohc', 'OHC'),
                                     ('shear', 'SHEAR'), ('current', 'CURRENT')),
                 split=False):
    """渲染某个月的图层: 默认拼一张四联长图; split=True 时**每层单独一张**
    (用户 2026-09-19: "再次渲染三图" —— 长图在对话里被压缩, 分开看更清楚)。"""
    print('=== %d 月 ===' % month)
    sim = build(month)
    # 产物统一归档到 output/sim/<年份>/(用户 2026-09-19), 年份取该次模拟的起始年
    year = str(sim.get('start') or '')[:4] or 'unknown'
    out_dir = os.path.join(OUT, 'sim', year)
    os.makedirs(out_dir, exist_ok=True)
    ocn = sim['ocn']
    print('  混合层深度 min/均/max: %.0f/%.0f/%.0f m   OHC min/均/max: %.0f/%.0f/%.0f kJ/cm2'
          % (ocn['h'].min(), ocn['h'].mean(), ocn['h'].max(),
             ocn['ohc_kj'].min(), ocn['ohc_kj'].mean(), ocn['ohc_kj'].max()))
    print('  SST(100-180E,0-50N) min/均/max: %.1f/%.1f/%.1f  风切 min/均/max: %.1f/%.1f/%.1f kt'
          % (sim['env']['sst'][:, 100:180].min(), sim['env']['sst'][:, 100:180].mean(),
             sim['env']['sst'][:, 100:180].max(),
             sim['env']['shear'][:, 100:180].min(), sim['env']['shear'][:, 100:180].mean(),
             sim['env']['shear'][:, 100:180].max()))
    big = pygame.Surface((W, H * len(layers) + 8 * (len(layers) - 1)))
    for i, (layer, name) in enumerate(layers):
        p = panel(sim, layer)
        stamp(p, '%s  WPAC  %d 月' % (name, month))
        big.blit(p, (0, i * (H + 8)))
        if split:
            sp = os.path.join(out_dir, 'layer_%s_%s.png' % (tag, layer))
            pygame.image.save(p, sp)
            print('  -> %s  %s' % (sp, p.get_size()))
    path = os.path.join(out_dir, 'sim_%s.png' % tag)
    pygame.image.save(big, path)
    print('  -> %s  %s' % (path, big.get_size()))


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='WPAC 图层自检图(SST/OHC/风切/洋流)')
    ap.add_argument('--months', default='', help='逗号分隔, 如 9 或 2,9; 省略=2 与 9')
    ap.add_argument('--layers', default='sst,ohc,shear',
                    help='逗号分隔: sst,ohc,shear,current')
    ap.add_argument('--split', action='store_true', help='每层单独出图')
    a = ap.parse_args(argv)
    months = ([int(x) for x in a.months.split(',') if x.strip()]
              if a.months else [2, 9])
    names = {'sst': 'SST', 'ohc': 'OHC', 'shear': 'SHEAR', 'current': 'CURRENT'}
    layers = tuple((k.strip(), names[k.strip()]) for k in a.layers.split(',')
                   if k.strip() in names)
    for m in months:
        render_month(m, {2: 'feb', 9: 'sep'}.get(m, 'm%02d' % m),
                     layers=layers, split=a.split)


if __name__ == '__main__':
    _main()
