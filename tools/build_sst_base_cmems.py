# -*- coding: utf-8 -*-
"""SST 单日基态: 从 CMEMS 全球单日文件取表层温度, 渲 12 个月目视, 再入库。

为什么用 CMEMS thetao 表层而不是 ERA5/OISST 气候态:
  * **单日**: 用户要求"不能看时段平均, 看单日"。月均/气候态会把黑潮这类窄锋面
    抹平 —— OISST 的 1991-2020 月气候态连黑潮都抹没了(已目视确认)。
  * **0.083° 原生**: GLORYS12V1 能解析黑潮/湾流/冷舌。
  * 表层(0.494 m)位温 ≈ 基础 SST。

用法(Windows Python):
  python tools/build_sst_base_cmems.py --render   # 只出 12 个月目视大图
  python tools/build_sst_base_cmems.py --build    # 目视通过后入库 base_sst.npz
"""
from __future__ import annotations

import argparse
import glob
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
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')


def isotropic_fill(a):
    """把 NaN(陆地)填成**最近的有效海温**, 不要填 0。

    为什么不能填 0: 0℃ 在 SST 调色板里是最浅的那一档(淡紫灰), 于是陆地会被
    画成一片浅灰盖住底图, 而且低通会把 0 洇进沿岸海面(实测三盆地出现大片灰)。
    各向同性最近邻让填充值来自四面八方的最近有效观测, 沿岸过渡也自然。
    """
    a = np.asarray(a, np.float32).copy()
    for _ in range(80):
        m = ~np.isfinite(a)
        if not m.any():
            break
        p = np.pad(a, 1, mode='edge')
        nb = np.stack([p[0:-2, 1:-1], p[2:, 1:-1], p[1:-1, 0:-2], p[1:-1, 2:],
                       p[0:-2, 0:-2], p[0:-2, 2:], p[2:, 0:-2], p[2:, 2:]])
        with np.errstate(invalid='ignore'):
            avg = np.nanmean(nb, axis=0)
        a = np.where(m & np.isfinite(avg), avg, a)
    return np.where(np.isfinite(a), a, 28.0)


def load_months():
    """→ (12, 240, 720) SST, lat, lon。缺月用 NaN 占位。"""
    fs = sorted(glob.glob(os.path.join(CACHE, 'glob_*.npz')))
    if not fs:
        return None, None, None
    z0 = np.load(fs[0])
    la, lo = z0['lat'], z0['lon']
    a = np.full((12, la.size, lo.size), np.nan, np.float32)
    got = []
    for f in fs:
        z = np.load(f)
        if 'sst' not in z.files:
            continue
        m = int(os.path.basename(f)[9:11]) - 1
        a[m] = z['sst']
        got.append(m + 1)
    return a, la, lo, got


def render(a, la, lo, out):
    from simulator.simcore import render as R
    W, H = 660, 220
    grid = pygame.Surface((W * 4 + 24, (H + 26) * 3 + 30))
    grid.fill((12, 12, 20))
    F = pygame.font.Font(os.path.join(ROOT, 'assets', 'font',
                                      'MapleMono-NF-CN-Medium.ttf'), 18)
    grid.blit(F.render('CMEMS GLORYS12V1 单日表层温度(每月 15 日 00z)  ' 
                       '— 黑潮/湾流/冷舌/暖池 结构检查', True, (255, 255, 255)),
              (8, 6))
    for m in range(12):
        v = a[m]
        ok = np.isfinite(v)
        t = np.clip(np.where(ok, v, 0.0) / 33.0, 0, 1) * 255.0
        rgb = R.LUT_SST_STEPS[t.astype(int)][..., :3].copy()
        rgb[~ok] = (40, 40, 44)
        rgb = np.flipud(rgb)
        surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(),
                                       (rgb.shape[1], rgb.shape[0]), 'RGB')
        surf = pygame.transform.smoothscale(surf, (W, H))
        gx, gy = (m % 4) * (W + 8), 30 + (m // 4) * (H + 26)
        grid.blit(F.render('%d 月' % (m + 1), True, (255, 235, 120)), (gx + 4, gy - 20))
        grid.blit(surf, (gx, gy))
    pygame.image.save(grid, out)
    print('->', out, grid.get_size())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--render', action='store_true')
    ap.add_argument('--build', action='store_true')
    a = ap.parse_args()
    res = load_months()
    if res[0] is None:
        print('没有 glob_*.npz'); return
    sst, la, lo, got = res
    print('已拿到月份:', got, ' 形状', sst.shape)
    if a.render or not a.build:
        od = os.path.join(ROOT, 'output', 'sim', '1997')
        os.makedirs(od, exist_ok=True)
        render(sst, la, lo, os.path.join(od, 'sst_singleday_12months.png'))
    if a.build:
        if len(got) < 12:
            print('!! 只有 %d 个月, 不建库' % len(got))
            return
        b = np.stack([isotropic_fill(sst[m]) for m in range(12)]).astype(np.float32)
        np.savez_compressed(os.path.join(MODEL, 'base_sst.npz'), sst=b)
        print('  陆地填充后: %.1f~%.1f 均值 %.1f' % (b.min(), b.max(), b.mean()))
        print('-> base_sst.npz %s  %.1f~%.1f 均值 %.1f  [CMEMS GLORYS 单日]'
              % (b.shape, b.min(), b.max(), b.mean()))


if __name__ == '__main__':
    main()
