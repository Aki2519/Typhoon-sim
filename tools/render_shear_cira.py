# -*- coding: utf-8 -*-
"""风切两种样式并排: 上=当前(0-60kt 连续填色), 下=CIRA 参考图样式(分级等值线)。

参考图样式照 CIRA/CIMSS 的 TC formation 产品:
  * 灰色底(代表 IR 云图背景)
  * 绿(5/10/15) 有利 / 黄(20) 中性 / 橙(25-30) / 红(35+) 不利 的等值线
  * 底部图例条 "TROPICAL CYCLONE FORMATION/INTENSIFICATION IS GENERALLY:
    FAVORABLE/NEUTRAL/UNFAVORABLE"
  * 经纬网 + 刻度标注

默认样式不变(填色), 这里只是**额外**出一张参考图样式供对比。

用法(Windows Python):
  python tools/render_shear_cira.py --start "1997-09-16 12" --tag 19970916
输出: output/shear_style_<tag>.png
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
          ('EPAC', 220.0, 280.0, 0.0, 35.0),
          ('NATL', 260.0, 350.0, 0.0, 50.0))
SCALE = 7.0
PANEL_H = 300
FONT = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                     'MapleMono-NF-CN-Medium.ttf'), 18)
BIG = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                    'MapleMono-NF-CN-Medium.ttf'), 22)


def cira_color(lv: int):
    """CIRA 分级配色。"""
    if lv <= 15:
        return (40, 210, 60)          # 绿: 有利
    if lv <= 20:
        return (240, 225, 60)         # 黄: 中性
    if lv <= 30:
        return (240, 150, 40)         # 橙
    return (230, 50, 45)              # 红: 不利


def draw_cira_panel(sim, lon0, lon1, lat0, lat1):
    w = int((lon1 - lon0) * SCALE)
    h = int((lat1 - lat0) * SCALE)
    s = pygame.Surface((w, h))
    s.fill((58, 58, 58))                                  # 灰底 ≈ IR 背景
    fx = lambda lo, a=lon0: (lo % 360 - a) * SCALE         # noqa: E731
    fy = lambda la, a=lat1: (a - la) * SCALE               # noqa: E731
    # 经纬网
    for lo in range(int(lon0), int(lon1) + 1, 10):
        x = fx(lo)
        pygame.draw.line(s, (95, 95, 95), (x, 0), (x, h), 1)
    for la in range(int(lat0), int(lat1) + 1, 10):
        y = fy(la)
        pygame.draw.line(s, (95, 95, 95), (0, y), (w, y), 1)
    # 等值线(复用 render 的 CIRA 色分级 + 数值标注)
    sh = sim['env']['shear']
    ny, nx = V.ENVNY, V.ENVNX
    xs0 = np.array([fx(V.C['LON0'] + (i + 0.5) * V.C['ENVD']) for i in range(nx)])
    xs1 = np.array([fx(V.C['LON0'] + (i + 1.5) * V.C['ENVD']) for i in range(nx)])
    ys0 = np.array([fy(V.C['LAT0'] + (j + 0.5) * V.C['ENVD']) for j in range(ny)])
    ys1 = np.array([fy(V.C['LAT0'] + (j + 1.5) * V.C['ENVD']) for j in range(ny)])
    # 只画参考图上出现的档位(5..70), 更高的档太密
    for lv in (5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70):
        seg = R._contour_segments(sh, (lv,), key=None)
        if seg is None:
            continue
        j0, i0, u0, v0, j1, i1, u1, v1 = seg
        col = cira_color(lv)
        wdt = 2 if lv >= 25 else 1
        x0 = xs0[i0] + u0 * (xs1[i0] - xs0[i0])
        y0 = ys0[j0] + v0 * (ys1[j0] - ys0[j0])
        x1 = xs0[i1] + u1 * (xs1[i1] - xs0[i1])
        y1 = ys1[j1] + v1 * (ys1[j1] - ys0[j1])
        for ax, ay, bx, by in zip(x0.tolist(), y0.tolist(),
                                  x1.tolist(), y1.tolist()):
            if max(ax, bx) < -20 or min(ax, bx) > w + 20:
                continue
            if max(ay, by) < -20 or min(ay, by) > h + 20:
                continue
            pygame.draw.line(s, col, (ax, ay), (bx, by), wdt)
        # 数值标注
        n = x0.shape[0]
        if n >= 6:
            step = max(1, n // 4)
            lab = FONT.render(str(lv), True, col)
            for k in range(0, n, step):
                if -10 < x0[k] < w - 20 and -10 < y0[k] < h - 16:
                    s.blit(lab, (x0[k] + 2, y0[k] + 1))
    return s


def draw_filled_panel(sim, lon0, lon1, lat0, lat1):
    w = int((lon1 - lon0) * SCALE)
    h = int((lat1 - lat0) * SCALE)
    s = pygame.Surface((w, h))
    s.fill((16, 24, 34))
    # 注意: 这里**不能**加 % 360 —— _blit_layer 用 fx(LON1)-fx(LON0) 算贴图宽度,
    # 取模会让 fx(360)==fx(0), 宽度算成 0, 整层不显示(实测填色面板全空)。
    fx = lambda lo, a=lon0: (lo - a) * SCALE               # noqa: E731
    fy = lambda la, a=lat1: (a - la) * SCALE               # noqa: E731
    R._blit_layer(s, R.render_env_layer(sim, 'shear'), fx, fy,
                  alpha=235, cache_key=None)
    return s


def legend_bar(width):
    bg = pygame.Surface((width, 30))
    bg.fill((0, 0, 0))
    t1 = FONT.render('TROPICAL CYCLONE FORMATION/INTENSIFICATION IS GENERALLY:',
                     True, (235, 235, 235))
    bg.blit(t1, (8, 6))
    x = 8 + t1.get_width() + 10
    for txt, col in (('FAVORABLE', (40, 210, 60)),
                     ('/NEUTRAL', (240, 225, 60)),
                     ('/UNFAVORABLE', (230, 50, 45))):
        t = FONT.render(txt, True, col)
        bg.blit(t, (x, 6))
        x += t.get_width()
    return bg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='1997-09-16 12')
    ap.add_argument('--tag', default='19970916')
    ap.add_argument('--data', type=int, default=1)
    a = ap.parse_args()
    sim = V.init({'start': a.start, 'params': {'dataMode': bool(a.data)}})
    V.env_update(sim)
    rows = []
    for name, lon0, lon1, lat0, lat1 in BASINS:
        rows.append((name, draw_filled_panel(sim, lon0, lon1, lat0, lat1),
                     draw_cira_panel(sim, lon0, lon1, lat0, lat1)))
    cw = max(p.get_width() for _, f, c in rows for p in (f, c))
    ch = max(p.get_height() for _, f, c in rows for p in (f, c))
    img = pygame.Surface((cw * 3 + 16, (ch + 46) * 2 + 40))
    img.fill((12, 12, 20))
    img.blit(BIG.render('风切 上=当前样式(填色 0-60kt)  下=CIRA 参考图样式(分级等值线)  %s'
                        % a.start, True, (255, 255, 255)), (8, 8))
    for k, (name, filled, cira) in enumerate(rows):
        x = k * (cw + 8)
        for r, (surf, lab) in enumerate(((filled, '当前样式'), (cira, 'CIRA 样式'))):
            y = 36 + r * (ch + 46)
            img.blit(FONT.render('%s  %s' % (name, lab), True, (255, 235, 120)),
                     (x + 4, y))
            img.blit(surf, (x, y + 20))
        img.blit(legend_bar(cw), (x, 36 + 2 * (ch + 46) - 30))
    _y = int(str(a.start)[:4])
    od = os.path.join(ROOT, 'output', 'sim', str(_y))
    os.makedirs(od, exist_ok=True)
    out = os.path.join(od, 'shear_style_%s.png' % a.tag)
    pygame.image.save(img, out)
    print('->', out, img.get_size())


if __name__ == '__main__':
    main()
