# -*- coding: utf-8 -*-
"""从 `<data>/fields/*.npy` **离线**重出逐 6 小时 PNG 帧(不必重跑模拟)。

为什么需要: 帧原来是 `sim_year.save_maps` 在跑的过程中写的, 而视频是 `make_video`
从 npy 另画 —— 两条路各写各的翻转/画布, 于是出现"PNG 是正的、视频是倒的"(用户
2026-09-21 连报两次)。现在两条路都走 `compare_fields.pad_polar + rgb_domain`,
这个工具把**已有**的 fields 重新出成同一套帧(含 ±90 画布与域外标注), 不需要重跑。

用法:
  python tools/refresh_frames.py --data "G:\\气象\\数据\\sim2100"
  python tools/refresh_frames.py --data "G:\\气象\\数据\\sim2025" --zoom 1
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

FIELDS = ('sst', 'ohc', 'shear')


def refresh(data, zoom=1, fields=FIELDS):
    import compare_fields as CF
    from sim_year import png_bytes
    land = CF.cell_land()
    # 帧的时次: 从 fields 里每个月的文件里拿不到时间戳 ⇒ 用已有的 PNG 文件名做时次表
    # (旧帧的文件名就是时次)。哪个场最全就用它的文件名集合。
    ts_all = None
    for f in fields:
        d = os.path.join(data, f)
        cur = sorted(x[:-4] for x in os.listdir(d) if x.endswith('.png')) \
            if os.path.isdir(d) else []
        if ts_all is None or len(cur) > len(ts_all):
            ts_all = cur
    if not ts_all:
        raise SystemExit('%s 下没有任何已有帧, 无法确定时次' % data)
    n = 0
    for f in fields:
        files = sorted(glob.glob(os.path.join(data, 'fields', '%s_*.npy' % f)),
                       key=lambda p: re.search(r'_(\d{6})\.npy$', p).group(1))
        arr = np.concatenate([np.load(p) for p in files], axis=0)
        if arr.shape[0] < len(ts_all):
            print('  %s 只有 %d 帧 < 时次表 %d, 按前 %d 帧处理'
                  % (f, arr.shape[0], len(ts_all), arr.shape[0]))
        m = min(arr.shape[0], len(ts_all))
        outdir = os.path.join(data, f)
        os.makedirs(outdir, exist_ok=True)
        z = max(1, int(zoom))
        for k in range(m):
            canvas, lm = CF.pad_polar(np.asarray(arr[k], np.float32), land)
            if z > 1:
                h, w = canvas.shape
                h2, w2 = h // z, w // z
                canvas = np.nanmean(canvas[:h2 * z, :w2 * z].reshape(h2, z, w2, z),
                                    axis=(1, 3))
                if lm is not None:
                    lm = (lm[:h2 * z, :w2 * z].reshape(h2, z, w2, z).mean(axis=(1, 3)) > 0.6)
            rgb = CF.rgb_domain(f, canvas, lm)
            with open(os.path.join(outdir, '%s.png' % ts_all[k]), 'wb') as fh:
                fh.write(png_bytes(np.ascontiguousarray(rgb)))
            n += 1
        print('  %-6s %d 帧重出完成' % (f, m))
    print('%s: 共重出 %d 张(±90 画布, 北在上)' % (data, n))
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--zoom', type=int, default=1)
    a = ap.parse_args(argv)
    refresh(a.data, a.zoom)
    return 0


if __name__ == '__main__':
    main()
