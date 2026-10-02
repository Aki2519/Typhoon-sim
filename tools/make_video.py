# -*- coding: utf-8 -*-
"""把 <data>/fields/*.npy 里存的逐 6 小时场渲染成视频(全球 / 西太两个视图)。

为什么从 npy 出片而不是直接拼 PNG: 存下来的原始场让我可以**换视图、换缩放、换配色**
重新出片, 不用重跑模拟。配色复用 compare_fields 的 LUT(与对照图一致)。

用法:
  python tools/make_video.py --field sst --domain global --fps 24
  python tools/make_video.py --all                      # 三场 x 两视图
  python tools/make_video.py --field ohc --domain wpac --fps 12 --upscale 2
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

DEFAULT_DATA = r'G:\气象\数据\sim2025'
#: 视图: (行起, 行止, 列起, 列止) —— **在 ±90 的北在上画布上**(见 compare_fields.pad_polar)。
#: 画布 360 行 × 0.5°, 行 0 = 89.75°N ⇒ 纬度 lat 对应行 (90-lat)/0.5。
#: 内核只算 ±60°, 域外那 60+60 行由 pad_polar 填 NaN, 出图时画成"无数据"色。
VIEWS = {
    'global': (0, 360, 0, 720),
    'wpac': (90, 180, 200, 360),           # 45N~0N, 100-180E
}


def load_frames(data, field):
    d = os.path.join(data, 'fields')
    fs = sorted(glob.glob(os.path.join(d, '%s_*.npy' % field)))
    if not fs:
        raise SystemExit('没有找到 %s 的 npy: %s' % (field, d))
    out = []
    for f in fs:
        if not re.search(r'_(\d{6})\.npy$', f):
            continue
        a = np.load(f)
        out.append(a)
    return np.concatenate(out, axis=0) if out else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=DEFAULT_DATA)
    ap.add_argument('--out', default=None, help='默认 <data>/video')
    ap.add_argument('--field', default='sst', choices=['sst', 'ohc', 'shear'])
    ap.add_argument('--domain', default='global', choices=sorted(VIEWS))
    ap.add_argument('--fps', type=float, default=24.0)
    ap.add_argument('--upscale', type=int, default=1)
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args(argv)
    outdir = a.out or os.path.join(a.data, 'video')
    os.makedirs(outdir, exist_ok=True)
    import imageio.v2 as imageio
    import compare_fields as CF
    land = CF.cell_land()
    jobs = ([(f, d) for f in ('sst', 'ohc', 'shear') for d in ('global', 'wpac')]
            if a.all else [(a.field, a.domain)])
    for fld, dom in jobs:
        frames = load_frames(a.data, fld)
        if frames is None:
            print('%-6s %-7s 无数据' % (fld, dom))
            continue
        r0, r1, c0, c1 = VIEWS[dom]
        # **一条路出图**: 先把 ±60 的场摆到 ±90 的北在上画布上(pad_polar, 域外 NaN),
        # 再用**同一个** rgb_domain 上色 —— 帧(PNG)与视频(mp4)从此共用这套代码。
        # 这正好治本: 上一轮"PNG 翻了、视频没翻"就是因为两条路各自实现翻转。
        c0_shape = CF.pad_polar(frames[0], None)[0].shape
        canvas = np.empty((frames.shape[0],) + c0_shape, np.float32)
        for k in range(frames.shape[0]):
            canvas[k] = CF.pad_polar(frames[k], None)[0]
        # 陆地掩码同样摆到 ±90 画布(用 0 场骗一次形状, 只取掩码)
        _, sub_land_all = CF.pad_polar(np.zeros(frames.shape[1:], np.float32), land)
        sub_land = (None if sub_land_all is None
                    else np.ascontiguousarray(sub_land_all[r0:r1, c0:c1]))
        up = max(1, int(a.upscale))
        outp = os.path.join(outdir, '%s_%s.mp4' % (fld, dom))
        wr = imageio.get_writer(outp, fps=a.fps, macro_block_size=None,
                                codec='libx264', quality=7,
                                ffmpeg_params=['-pix_fmt', 'yuv420p'])
        n = 0
        for k in range(canvas.shape[0]):
            arr = np.ascontiguousarray(canvas[k][r0:r1, c0:c1])
            rgb = CF.rgb_domain(fld, arr, sub_land)
            if up > 1:                       # 最近邻放大(不引入插值开销)
                rgb = np.repeat(np.repeat(rgb, up, axis=0), up, axis=1)
            wr.append_data(rgb)
            n += 1
        wr.close()
        print('%-6s %-7s %4d 帧 → %s (%.1f MB)' % (
            fld, dom, n, outp, os.path.getsize(outp) / 1e6))


if __name__ == '__main__':
    main()
