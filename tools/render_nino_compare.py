# -*- coding: utf-8 -*-
"""Niño 区(20S-20N, 160E-80W)SST 对比: 模拟 vs 真实(ERA5 月均)。

用户 2026-09 指出: EPAC 赤道看着只有 24°C, 而"就算超强拉尼娜年也该有 26-28,
1997 超强厄尔尼诺该有 31-33"。这里把两个极端年份并排, 用**同期真实月均**做对照。

真实数据: simulator/env/train_cache/era5/sstm_<年>_09.nc
  (由 tools/fetch_era5_seq.py --only sstm --years 1997,1999 --months 9 抓取)

用法(Windows Python):
  python tools/render_nino_compare.py --years 1997,1999
输出: output/nino_compare.png
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

LON0, LON1, LAT0, LAT1 = 160.0, 280.0, -20.0, 20.0
W = 900
H = int(W * (LAT1 - LAT0) / (LON1 - LON0))
FONT = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                     'MapleMono-NF-CN-Medium.ttf'), 19)
BIG = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                    'MapleMono-NF-CN-Medium.ttf'), 24)


def paint_field(arr, glat, glon):
    """把一个**带自己坐标轴**的 SST 场裁到 Niño 区并上色。

    必须用场自带的坐标轴: ERA5 是 60S-60N / 0.25°(481 点), 内核是 0.5°,
    拿内核的轴去裁 ERA5 会切到完全不相干的纬度(实测切出一片平滑蓝绿)。
    """
    il = np.nonzero((glon >= LON0) & (glon <= LON1))[0]
    jl = np.nonzero((glat >= LAT0) & (glat <= LAT1))[0]
    if il.size == 0 or jl.size == 0:
        srf = pygame.Surface((W, H))
        srf.fill((40, 40, 44))
        return srf
    sub = arr[np.ix_(jl, il)]
    ok = np.isfinite(sub)
    t = np.clip(np.where(ok, sub, 0.0) / 33.0, 0, 1) * 255.0
    rgb = R.LUT_SST_STEPS[t.astype(int)][..., :3].copy()
    rgb[~ok] = (70, 70, 74)
    rgb = np.flipud(rgb)
    surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(),
                                   (rgb.shape[1], rgb.shape[0]), 'RGB')
    return pygame.transform.smoothscale(surf, (W, H))


def paint(sim, is_field=False):
    """把模拟模式的 SST 场画到 (W,H)(复用 render 的整层 + 海岸线掩膜)。"""
    if is_field:
        s = pygame.Surface((W, H))
        s.fill((20, 26, 36))
        fx = lambda lo: (lo - LON0) / (LON1 - LON0) * W          # noqa: E731
        fy = lambda la: (LAT1 - la) / (LAT1 - LAT0) * H          # noqa: E731
        arr = sim
        glat = -59.75 + np.arange(V.ENVNY) * V.C['ENVD']
        glon = 0.25 + np.arange(V.ENVNX) * V.C['ENVD']
        il = np.nonzero((glon >= LON0) & (glon <= LON1))[0]
        jl = np.nonzero((glat >= LAT0) & (glat <= LAT1))[0]
        sub = arr[np.ix_(jl, il)]
        ok = np.isfinite(sub)
        # NaN(陆地/冰盖)先填 0 再上色, 之后整片涂成中性灰 —— 直接 astype(int)
        # 会把 NaN 变成 INT64_MIN, 拿去索引调色板直接越界
        t = np.clip(np.where(ok, sub, 0.0) / 33.0, 0, 1) * 255.0
        rgb = R.LUT_SST_STEPS[t.astype(int)][..., :3].copy()
        rgb[~ok] = (70, 70, 74)
        rgb = np.flipud(rgb)                       # 南在上 → 北在上
        surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(),
                                       (rgb.shape[1], rgb.shape[0]), 'RGB')
        return pygame.transform.smoothscale(surf, (W, H))
    # sim 路径: 复用 render 的整层(含海岸线掩膜), 再裁窗
    full = pygame.Surface((int(360 * 6), int(120 * 6)))
    R._blit_layer(full, arr, lambda lo: lo * 6.0, lambda la: (60.0 - la) * 6.0,
                  alpha=255, cache_key=None)
    x0 = int(LON0 * 6)
    sub = full.subsurface(pygame.Rect(x0, 0, int((LON1 - LON0) * 6),
                                      int(120 * 6))).copy()
    return pygame.transform.smoothscale(sub, (W, H))


def load_era5(year, month=9):
    import netCDF4
    p = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5',
                     'sstm_%d_%02d.nc' % (year, month))
    if not os.path.exists(p):
        return None
    # netCDF4 在 Windows 打不开含中文的绝对路径(项目里 climate._chdir_ctx 同款绕法)
    cwd = os.getcwd()
    os.chdir(os.path.dirname(p))
    try:
        d = netCDF4.Dataset(os.path.basename(p))
        a = np.asarray(d.variables['sst'][:], np.float32).squeeze()
        la = np.asarray(d.variables['latitude'][:])
        lo = np.asarray(d.variables['longitude'][:])
        d.close()
    finally:
        os.chdir(cwd)
    if a.ndim == 3:
        a = a[0]
    # 必须用 nanmax: 数组里有 NaN(陆地) 时 a.max() 返回 NaN, NaN>200 为假,
    # 开尔文就悄悄漏了过去(实测真实值打出 297 K)
    if np.nanmax(a) > 200.0:
        a = a - 273.15
    if la[0] > la[-1]:
        a = a[::-1, :]
        la = la[::-1]
    return a, la, lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='1997,1999')
    ap.add_argument('--month', type=int, default=9)
    ap.add_argument('--day', type=int, default=15)
    a = ap.parse_args()
    years = [int(x) for x in a.years.split(',')]
    cols = []
    for y in years:
        sim = V.init({'start': '%d-%02d-%02d 12' % (y, a.month, a.day),
                      'params': {'dataMode': True}})
        V.env_update(sim)
        sst_sim = np.asarray(sim['env']['sst'], np.float32)
        real = load_era5(y, a.month)
        cols.append((y, paint(sst_sim, True),
                     None if real is None else paint_field(*real), real))
    ch = H
    img = pygame.Surface((W * len(cols) + 12 * (len(cols) - 1), ch * 2 + 40 + 34))
    img.fill((12, 12, 20))
    img.blit(BIG.render('Niño 区 20S-20N / 160E-80W   SST   '
                        '上=模拟(离线环境场)  下=真实 ERA5 %d月 15 日'
                        % a.month, True, (255, 255, 255)), (8, 6))
    for k, (y, sim_img, real_img, real) in enumerate(cols):
        x = k * (W + 12)
        img.blit(FONT.render('%d-%02d-%02d 12z  模拟' % (y, a.month, a.day),
                             True, (255, 235, 120)), (x + 4, 34))
        img.blit(sim_img, (x, 56))
        img.blit(FONT.render('%d-%02d  真实 ERA5' % (y, a.month),
                             True, (140, 240, 160)), (x + 4, 60 + ch))
        if real_img is not None:
            img.blit(real_img, (x, 82 + ch))
    od = os.path.join(ROOT, 'output', 'sim', str(years[0]))
    os.makedirs(od, exist_ok=True)
    out = os.path.join(od, 'nino_compare.png')
    pygame.image.save(img, out)
    print('->', out, img.get_size())

    # 数值对照
    glat = -59.75 + np.arange(V.ENVNY) * V.C['ENVD']
    for y in years:
        sim = V.init({'start': '%d-%02d-%02d 12' % (y, a.month, a.day),
                      'params': {'dataMode': True}})
        V.env_update(sim)
        f = np.asarray(sim['env']['sst'], np.float32)
        r = load_era5(y, a.month)
        print('\n=== %d-%02d ===' % (y, a.month))
        print('%-18s %10s %10s' % ('点位', '模拟', '真实ERA5'))
        for lab, la, lo in (('Nino4 5N,160E', 5, 200), ('Nino3.4 0N,120W', 0, 240),
                            ('Nino3 5S,110W', -5, 250), ('Nino1+2 0N,85W', 0, 275),
                            ('eq 0N,140W', 0, 220), ('20S,120W', -20, 240)):
            j = int(round((la + 59.75) / 0.5))
            i = int(round((lo % 360 - 0.25) / 0.5))
            sv = float(f[j, i])
            rv = '—'
            if r is not None:
                ra, rla, rlo = r
                jj = int(np.argmin(abs(rla - la)))
                ii = int(np.argmin(abs(rlo - (lo % 360))))
                rv = '%.1f' % float(ra[jj, ii])
            print('%-18s %10.1f %10s' % (lab, sv, rv))


if __name__ == '__main__':
    main()
