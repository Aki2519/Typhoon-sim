# -*- coding: utf-8 -*-
"""轮次 163: 从参考图里**反解轨迹**(圆点中心 -> 经纬度), 用于验证我们的渲染器。

用的是 terrain-data 反推出的裁剪窗口(`G:\tmp\ty4out\windows.json`)与已知投影
`x = lon*30`、`y = (90-lat)*30`(底图 lon 0..360)。

输出: 每个等级的点序列(lon/lat), 存 `G:\tmp\ty4out\track_from_image.json`
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image

# 2023 色阶(TD 用**实测值**, 不用 Typhoon4Py 里那个错的 0x5EBAFF)
PAL = {
    'TD': (110, 193, 234), 'TS': (77, 255, 255), 'C1': (255, 255, 217),
    'C2': (255, 217, 140), 'C3': (255, 158, 89), 'C4': (255, 115, 138),
    'C5': (141, 117, 230),
}
WINDOWS = json.load(io.open('G:/tmp/ty4out/windows.json', encoding='utf-8'))
ROOT = 'G:/气象/Typhoon-sim'


def clusters(mask, min_px=12):
    """把二值掩码聚成连通块(4 邻域, 用简单的 BFS; 图不大)。"""
    from collections import deque
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    out = []
    ys, xs = np.nonzero(mask)
    pts = set(zip(ys.tolist(), xs.tolist()))
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        acc = []
        while q:
            y, x = q.popleft()
            acc.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and (ny, nx) in pts:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(acc) >= min_px:
            a = np.array(acc, float)
            y0b, y1b = float(a[:, 0].min()), float(a[:, 0].max())
            x0b, x1b = float(a[:, 1].min()), float(a[:, 1].max())
            bw, bh = x1b - x0b + 1, y1b - y0b + 1
            fill = len(acc) / float(bw * bh)
            # ⚠ 轨迹**线段的颜色与圆点相同** ⇒ 细长线段也会被算成块。只保留
            # **紧凑近方形**的块(圆点), 用"边长范围 + 填充率 + 长宽差"三条一起筛。
            if 7 <= bw <= 24 and 7 <= bh <= 24 and 0.55 <= fill <= 1.05 \
                    and abs(bw - bh) <= 6:
                out.append((float(a[:, 0].mean()), float(a[:, 1].mean()),
                            len(acc)))
    return out


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'megi.png'
    win = WINDOWS[name]
    x0, y0 = win['x'], win['y']
    px = win['px_per_deg']
    im = Image.open(os.path.join(ROOT, name)).convert('RGB')
    a = np.asarray(im)
    H, W, _ = a.shape
    # 图例在右下角(色块颜色与轨迹点相同) ⇒ 先抹掉, 否则会被当成定位点。
    a = a.copy()
    a[H - 300:, W - 440:] = 0
    print('%s  %dx%d  窗口 x0=%d y0=%d  px/deg=%s'
          % (name, W, H, x0, y0, px))
    print('  该窗口覆盖: lon %.3f-%.3f E,  lat %.3f-%.3f N'
          % (x0 / px, (x0 + W) / px, 90 - y0 / px, 90 - (y0 + H) / px))
    # 图例区(右下)要排除, 否则会把图例圆点当成轨迹点
    ly0 = int(win['y'] * 0)  # 占位
    res = {}
    for cat, col in PAL.items():
        m = np.all(a == np.array(col, dtype=np.uint8), axis=2)
        n = int(m.sum())
        if n == 0:
            res[cat] = []
            continue
        cl = clusters(m)
        pts = []
        for cy, cx, npix in cl:
            lon = (x0 + cx) / px
            lat = 90.0 - (y0 + cy) / px
            pts.append({'lon': round(lon, 4), 'lat': round(lat, 4),
                        'px': npix, 'cx': round(cx, 2), 'cy': round(cy, 2)})
        pts.sort(key=lambda p: p['cx'])          # 按 x 排序(大致沿时间)
        res[cat] = pts
        print('  %-3s 像素%-6d 连通块 %-4d  (块像素中位 %s)'
              % (cat, n, len(cl),
                 int(np.median([p['px'] for p in pts])) if pts else '-'))
    io.open('G:/tmp/ty4out/track_from_image.json', 'w', encoding='utf-8').write(
        json.dumps({'file': name, 'window': win, 'points': res},
                   ensure_ascii=False, indent=1))
    print('-> G:/tmp/ty4out/track_from_image.json')


if __name__ == '__main__':
    main()
