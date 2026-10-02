# -*- coding: utf-8 -*-
"""轮次 163: 反推 4 张参考图各自对应的 b-deck 文件(task-18 的 Lead 接管版)。

## 判据(与"点序"无关, 绕开我按经度排序在回折点会乱的伪影)
对每张参考图:
  · 用七色分割 + 紧凑连通块取出**定位点集合** A(经纬度, 无序);
  · 对每个候选 b-deck 取出点集合 B(有序, 但匹配时不看顺序);
  · **双向最近邻覆盖率**: `cov = (A里能在0.3度内找到B点的比例 + B里能找到A点的比例) / 2`
  · 取 cov 最高者; 并报告**最优与次优的差距**(否则"匹配上了"可能只是没别人跟它像)。
"""
import io
import json
import math
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

PAL = [(110, 193, 234), (77, 255, 255), (255, 255, 217), (255, 217, 140),
       (255, 158, 89), (255, 115, 138), (141, 117, 230)]
ROOT = 'G:/气象/Typhoon-sim'
XRQ = 'G:/气象/数据/xrq'
TOL = 0.30          # 度
WIN = json.load(io.open('G:/tmp/ty4out/windows.json', encoding='utf-8'))


def extract_points(name):
    """从参考图取定位点(经纬度)。返回 (N,2) 数组 [lon, lat]。"""
    w = WIN[name]
    x0, y0, px = w['x'], w['y'], w['px_per_deg']
    a = np.asarray(Image.open(os.path.join(ROOT, name)).convert('RGB'))
    H, W, _ = a.shape
    a = a.copy()
    # ⚠ 遮蔽右下角图例: **必须按画布比例** —— 规格已确证"图例尺寸随画布高度缩放"
    #   (megi H=1080 时 row=27; sinlaku H=2312 时 row≈58) ⇒ 固定像素遮蔽对高画布
    #   **盖不住**, 图例圆点会被当成轨迹点混入, 把包围盒撑爆导致粗筛全灭。
    a[int(H * 0.70):, int(W * 0.58):] = 0
    pts = []
    for c in PAL:
        m = np.all(a == np.array(c, dtype=np.uint8), axis=2)
        if not m.any():
            continue
        lab, n = ndimage.label(m)
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            if len(ys) < 40:                       # 太小 = 线段或抗锯齿碎块
                continue
            bw = xs.max() - xs.min() + 1
            bh = ys.max() - ys.min() + 1
            if not (7 <= bw <= 26 and 7 <= bh <= 26 and abs(bw - bh) <= 7):
                continue                           # 细长 = 线段, 不是圆点
            if len(ys) / float(bw * bh) < 0.5:     # 填充率低 = 空心/残缺
                continue
            pts.append(((x0 + xs.mean()) / px, 90.0 - (y0 + ys.mean()) / px))
    return np.array(pts, float)


def read_bdeck(path):
    """ATCF b-deck -> (N,2) [lon, lat]。经度统一到 0..360。"""
    out = []
    try:
        txt = io.open(path, encoding='utf-8', errors='ignore').read()
    except Exception:
        return np.zeros((0, 2))
    for line in txt.splitlines():
        fl = [x.strip() for x in line.split(',')]
        if len(fl) < 9:
            continue
        def deg(s):
            """ATCF b-deck 的经纬度是**十分之一度**存的!

            样例行 `WP, 08, ...,  73N, 1597E, ...` 里 `73N` = **7.3N**、
            `1597E` = **159.7E** —— 小数点被省略(整整度写 `1200E` = 120.0)。
            直接 `float(s[:-1])` 会把坐标**放大 10 倍**, 所有匹配都会归零。
            """
            if not s:
                return None
            suf = s[-1].upper()
            if suf in 'NSEW':
                body = s[:-1]
                v = float(body) if '.' in body else float(body) / 10.0
                return -v if suf in 'SW' else v
            v = float(s)
            return v if '.' in s else (v / 10.0 if abs(v) >= 100 else v)
        try:
            la, lo = deg(fl[6]), deg(fl[7])
        except ValueError:
            continue
        if la is None or lo is None:
            continue
        out.append((lo % 360.0, la))
    return np.array(out, float) if out else np.zeros((0, 2))


def coverage(A, B, tol=TOL):
    """双向最近邻覆盖率(与点序无关)。"""
    if len(A) == 0 or len(B) == 0:
        return 0.0, 0.0, 0.0
    # 经度差要按环形算(跨 180 时 359 与 1 只差 2 度)
    dlon = np.abs(A[:, 0][:, None] - B[:, 0][None, :])
    dlon = np.minimum(dlon, 360.0 - dlon) * np.cos(np.radians(A[:, 1][:, None]))
    dlat = A[:, 1][:, None] - B[:, 1][None, :]
    d = np.hypot(dlon, dlat)
    fwd = float((d.min(axis=1) <= tol).mean())
    bwd = float((d.min(axis=0) <= tol).mean())
    return (fwd + bwd) / 2.0, fwd, bwd


def candidates():
    for root, _dirs, files in os.walk(XRQ):
        for fn in files:
            if fn.lower().startswith('b') and fn.lower().endswith('.dat'):
                yield os.path.join(root, fn)


def main():
    targets = sys.argv[1:] or ['megi.png', 'rita.png', 'haiyan.png', 'sinlaku.png']
    for name in targets:
        A = extract_points(name)
        print('== %s  反解出 %d 个定位点  lon %.2f-%.2f lat %.2f-%.2f'
              % (name, len(A), A[:, 0].min(), A[:, 0].max(),
                 A[:, 1].min(), A[:, 1].max()))
        scored = []
        n_all = 0
        for p in candidates():
            B = read_bdeck(p)
            if len(B) < 5:
                continue
            n_all += 1
            # 先用包围盒粗筛: 候选必须覆盖反解点的范围。
            # ⚠ 反解点里可能有**假点**(例如图例随画布放大后没被遮蔽干净),
            # 会把包围盒撑爆导致粗筛全灭 => 见下面的**自动回退**。
            if B[:, 0].min() > A[:, 0].min() + 2 or B[:, 0].max() < A[:, 0].max() - 2:
                continue
            if B[:, 1].min() > A[:, 1].min() + 2 or B[:, 1].max() < A[:, 1].max() - 2:
                continue
            c, f, b = coverage(A, B)
            scored.append((c, f, b, p, len(B)))
        if not scored:
            print('   包围盒粗筛全灭 => **自动回退到全量评分**(反解点里应有假点, '
                  '它们把包围盒撑大了)')
            for p in candidates():
                B = read_bdeck(p)
                if len(B) < 5:
                    continue
                c, f, b = coverage(A, B)
                if c > 0.2:
                    scored.append((c, f, b, p, len(B)))
        scored.sort(reverse=True)
        if not scored:
            print('   **没有包围盒达标的候选**')
            continue
        for i, (c, f, b, p, nb) in enumerate(scored[:3]):
            print('   #%d cov=%.3f (fwd %.3f / bwd %.3f)  %d 点  %s'
                  % (i + 1, c, f, b, nb, os.path.relpath(p, XRQ)))
        if len(scored) > 1:
            print('   最优-次优 = %.3f  %s'
                  % (scored[0][0] - scored[1][0],
                     '(gap BIG => trustworthy)' if scored[0][0] - scored[1][0] > 0.15
                     else '(gap SMALL => needs review)'))


if __name__ == '__main__':
    main()
