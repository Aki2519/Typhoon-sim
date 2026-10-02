# -*- coding: utf-8 -*-
"""渲染**真实数据**参照图: 瞬时 ERA5 vs 月气候态 vs 差值。

用户 2026-09-20: "风切及一切数据都要用瞬时(除了一些特殊数据用长时间平均)"。
这个脚本把"瞬时 / 气候态 / 差值"三张图并排出来, 一眼能看出月均把什么抹平了。

用法:
  python tools/render_real_reference.py --date 1997-09-16 --hour 12
  python tools/render_real_reference.py --date 2024-09-15 --hour 12 --field shear
产物: output/sim/_reference/real_<field>_<YYYYMMDD><HH>_<domain>.png
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))

from simulator.simcore import daily as D          # noqa: E402

_spec = importlib.util.spec_from_file_location(
    'cf', os.path.join(ROOT, 'tools', 'compare_fields.py'))
cf = importlib.util.module_from_spec(_spec)
sys.modules['cf'] = cf
_spec.loader.exec_module(cf)

DOMAINS = (('global', 0.0, 360.0, -60.0, 60.0, 2.6),
           ('nwaust', 108.0, 140.0, -25.0, -2.0, 6.0),
           ('india', 35.0, 115.0, -12.0, 22.0, 6.0),
           ('wpac', 100.0, 180.0, 0.0, 50.0, 4.0))


def _clim(field, month_idx):
    """月气候态参照(特殊的长时期平均产品)。"""
    if field == 'sst':
        a, la, lo, bad = cf.real_sst(month_idx)
        return a, la, lo, bad
    if field == 'ohc':
        a, la, lo = cf.real_ohc(month_idx)
        return a, la, lo, (a <= 0)
    a, la, lo = cf.real_shear(month_idx)
    return a, la, lo, None


def _to_clim_grid(arr, lat, lon, la, lo):
    from scipy import ndimage as ndi
    fy = (lat - la[0]) / (la[-1] - la[0]) * (len(la) - 1)
    fx = (lon - lo[0]) / (lo[-1] - lo[0]) * (len(lo) - 1)
    coords = np.array(np.meshgrid(fy, fx, indexing='ij'))
    return ndi.map_coordinates(np.nan_to_num(arr, nan=0.0), coords,
                               order=1, mode='nearest')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='1997-09-16')
    ap.add_argument('--hour', type=int, default=12)
    ap.add_argument('--field', default='sst', choices=['sst', 'ohc', 'shear', 'all'])
    a = ap.parse_args(argv)
    from datetime import datetime
    y, m, d = (int(x) for x in a.date.split('-'))
    clock = datetime(y, m, d, a.hour)
    fields = ['sst', 'ohc', 'shear'] if a.field == 'all' else [a.field]
    inst = D.load_fields(clock)
    print('瞬时字段:', sorted(k for k in inst if not k.startswith('_')),
          inst.get('_source'))
    lat = -60 + (np.arange(240) + 0.5) * 0.5
    lon = (np.arange(720) + 0.5) * 0.5
    outdir = os.path.join(ROOT, 'output', 'sim', '_reference')
    os.makedirs(outdir, exist_ok=True)
    f = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                      'MapleMono-NF-CN-Medium.ttf'), 20)

    for fld in fields:
        if fld not in inst:
            print('  跳过 %s: 该时次没有瞬时 %s(抓取未完成?)' % (fld, fld))
            continue
        cur = np.asarray(inst[fld], dtype=np.float32)
        ref, rla, rlo, rbad = _clim(fld, m - 1)
        clim = _to_clim_grid(ref, lat, lon, rla, rlo)
        diff = cur - clim
        # 差值图: 以气候态为中性色 → 用同一色标, 但把差值平移一个中性温度
        mid = 28.0 if fld == 'sst' else (60.0 if fld == 'ohc' else 20.0)
        diff_img = diff + mid
        # 三张图统一掩码: 用**格点陆地占比 100%** 的网格(瞬时/气候态/差值口径一致;
        # 瞬时 ERA5 自带陆地皮温、LTM 自带 fill, 不统一掩就会画出假的高温/白块)
        _landm = (cf.cell_land() >= 0.999) if cf.cell_land() is not None else rbad
        for tag, lon0, lon1, lat0, lat1, sc in DOMAINS:
            cf.SCALE = sc
            title = '瞬时 ERA5 %s %02dZ' % (a.date, a.hour)
            A = cf.panel(fld, cur, lat, lon, lon0, lon1, lat0, lat1, title,
                         land=_landm)
            B = cf.panel(fld, clim, lat, lon, lon0, lon1, lat0, lat1,
                         '月气候态(%s)' % fld.upper(), land=_landm)
            C = cf.panel(fld, diff_img, lat, lon, lon0, lon1, lat0, lat1,
                         '差值 = 瞬时 − 气候态(中值 %.0f)' % mid,
                         land=_landm)
            W, H = A.get_size()
            canv = pygame.Surface((W, H * 3 + 70)); canv.fill((12, 16, 26))
            for k, (img, txt) in enumerate(((A, '瞬时 ERA5 %s %02dZ' % (a.date, a.hour)),
                                            (B, '月气候态 %s' % fld.upper()),
                                            (C, '差值 = 瞬时 − 气候态'))):
                canv.blit(f.render(txt, True, (255, 240, 120)), (8, k * (H + 23) + 2))
                canv.blit(img, (0, k * (H + 23) + 20))
            out = os.path.join(outdir, 'real_%s_%s%02d_%s.png'
                               % (fld, a.date.replace('-', ''), a.hour, tag))
            pygame.image.save(canv, out)
            print('  saved', out, canv.get_size())
        # 数值: 几个已知点
        pts = {'sst': ((130, 13), (120, -12), (75, 0), (240, 0), (270, 0)),
               'shear': ((130, 13), (140, 35), (200, 40), (300, 15)),
               'ohc': ((130, 13), (240, 0), (300, 15), (75, 0))}[fld]
        print('  %-14s %8s %8s %8s' % ('点', '瞬时', '气候态', '差'))
        for lo, la in pts:
            i = int(np.argmin(np.abs(lat - la))); j = int(np.argmin(np.abs(lon - lo)))
            print('  (%3dE,%4d) %8.2f %8.2f %+8.2f'
                  % (lo, la, cur[i, j], clim[i, j], diff[i, j]))


if __name__ == '__main__':
    main()
