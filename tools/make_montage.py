# -*- coding: utf-8 -*-
"""把三张场图(sst/ohc/shear)按同一时次拼成一张横向三联图。

用户 2026-09-21: "跑一次 2100.09.15 三图模拟" —— 跑完给一张能直接看的对照图。

用法:
  python tools/make_montage.py --data "G:\\气象\\数据\\sim2100" --ts 2100091512
"""
from __future__ import annotations

import argparse
import os

FIELDS = ('sst', 'ohc', 'shear')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--ts', required=True, help='时次, 如 2100091512')
    ap.add_argument('--out', default=None)
    ap.add_argument('--fields', default=','.join(FIELDS))
    ap.add_argument('--ts2', default=None, help='第二个时次: 与第一个**上下叠**成两行(做年代对照)')
    ap.add_argument('--data2', default=None, help='第二个时次所在的数据目录(默认同 --data)')
    a = ap.parse_args(argv)
    import imageio.v2 as iio
    import numpy as np

    def row(ts, data):
        imgs = []
        for f in a.fields.split(','):
            p = os.path.join(data, f.strip(), '%s.png' % ts)
            if not os.path.exists(p):
                raise SystemExit('缺文件: %s' % p)
            imgs.append(iio.imread(p)[..., :3])
        return np.hstack(imgs)

    top = row(a.ts, a.data)
    if a.ts2:
        bot = row(a.ts2, a.data2 or a.data)
        w = min(top.shape[1], bot.shape[1])
        img = np.vstack([top[:, :w], bot[:, :w]])
    else:
        img = top
    out = a.out or os.path.join(a.data, 'montage_%s%s.png'
                                % (a.ts, ('_vs_' + a.ts2) if a.ts2 else ''))
    iio.imwrite(out, img)
    print('-> %s  %s' % (out, img.shape))
    return out


if __name__ == '__main__':
    main()
