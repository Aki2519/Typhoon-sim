# -*- coding: utf-8 -*-
"""SST / OHC / 风切 三合一对照图: 观测(上) vs 模拟模式当前环境场(下)。

观测来源(用户提供的产品图):
  SST  REMSS MW-IR 2026/09/14
  OHC  CMEMS OHC 2026/09/13
  风切 CIRA/CIMSS GOES-EAST(NATL) / GOES-WEST(EPAC) / HIMAWARI-9(WPAC)
模拟: output/simmap_<field>_cur.png(走主程序 render_env_layer + _blit_layer)

用法: python tools/make_compare_figure.py
输出: output/figure_<field>_compare.png
"""
from __future__ import annotations

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'output')
REF = os.path.join(OUT, 'ref_20260914')

#: 观测图的图区(裁掉坐标轴/色标/底部说明条), 见 tools/digitize_ref.py 的刻度检测
REF_BOX = {'sst': {'wpac': (68, 68, 1385, 892), 'epac': (66, 66, 1386, 834),
                   'natl': (68, 68, 1384, 872)},
           'ohc': {'wpac': (68, 68, 1385, 892), 'epac': (66, 66, 1386, 834),
                   'natl': (68, 68, 1384, 872)},
           'shear': {'wpac': (0, 0, 1400, 648), 'epac': (0, 0, 900, 648),
                     'natl': (0, 0, 900, 648)}}
REF_FILE = {
    'sst': {'wpac': 'remss_sst_wpac.png', 'epac': 'remss_sst_epac.png',
            'natl': 'remss_sst_natl.png'},
    'ohc': {'wpac': 'ohc_wpac.png', 'epac': 'ohc_epac.png',
            'natl': 'ohc_natl.png'},
    'shear': {'wpac': 'wgmsshr-1.jpg', 'epac': 'wg9shr.jpg',
              'natl': 'wg8shr.jpg'}}
TITLE = {'sst': 'SST  °C   (观测 REMSS 2026/09/14  vs  模拟)',
         'ohc': 'OHC  kJ/cm2   (观测 CMEMS 2026/09/13  vs  模拟)',
         'shear': '垂直风切变 kt   (观测 CIRA/CIMSS  vs  模拟)'}
#: 模拟渲染图里三个面板的位置(render_sim_basins.py: SCALE=7, 间隔 8, 顶部 40)
SIM_SCALE = 7.0
SIM_PANEL = {'wpac': (0, 40, 80, 50), 'epac': (568, 40, 60, 35),
             'natl': (996, 40, 90, 50)}
TARGET_W = 620
LABEL_H = 26


def font(sz=20):
    p = os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf')
    return ImageFont.truetype(p, sz) if os.path.exists(p) else ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='1997-09-16 12')
    ap.add_argument('--tag', default='cur')
    ap.add_argument('--outdir', default=None)
    a = ap.parse_args()
    year = int(str(a.start)[:4])
    # 产物已按 output/sim/<年>/ 归档
    simdir = a.outdir or os.path.join(OUT, 'sim', str(year))
    os.makedirs(simdir, exist_ok=True)
    F, FH = font(20), font(24)
    for field in ('sst', 'ohc', 'shear'):
        cols = []
        for basin in ('wpac', 'epac', 'natl'):
            r = Image.open(os.path.join(REF, REF_FILE[field][basin])).convert('RGB')
            r = r.crop(REF_BOX[field][basin])
            rh = int(r.height * TARGET_W / r.width)
            r = r.resize((TARGET_W, rh), Image.LANCZOS)
            im = Image.open(os.path.join(simdir,
                                         'simmap_%s_%s.png' % (field, a.tag))).convert('RGB')
            x, y, wd, hd = SIM_PANEL[basin]
            s = im.crop((x, y, x + int(wd * SIM_SCALE), y + int(hd * SIM_SCALE)))
            s = s.resize((TARGET_W, rh), Image.LANCZOS)
            cols.append([r, s])
        colh = max(sum(p.height for p in c) + LABEL_H * 2 for c in cols)
        img = Image.new('RGB', (TARGET_W * 3 + 16, colh + 44), (14, 14, 22))
        d = ImageDraw.Draw(img)
        d.text((8, 8), '%s   @%s' % (TITLE[field], a.start),
                       fill=(255, 255, 255), font=FH)
        for k, (basin, rows) in enumerate(zip(('WPAC', 'EPAC', 'NATL'), cols)):
            x, y = k * (TARGET_W + 8), 44
            for t, im in zip(('OBS 观测', 'SIM 1997-09-16 12z'), rows):
                d.rectangle((x, y, x + TARGET_W, y + LABEL_H - 2), fill=(30, 34, 48))
                d.text((x + 6, y + 3), '%s  %s' % (basin, t),
                       fill=(255, 235, 120), font=F)
                y += LABEL_H
                img.paste(im, (x, y))
                y += im.height
        p = os.path.join(simdir, 'figure_%s_compare.png' % field)
        img.save(p)
        print('->', p, img.size)


if __name__ == '__main__':
    main()
