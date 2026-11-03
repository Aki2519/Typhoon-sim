# -*- coding: utf-8 -*-
"""湖泊掩膜：**用 Natural Earth 10m 矢量数据**（用户 2026-10-19 要求先找数据）。

数据：`data/lakes/ne_10m_lakes.geojson`（1355 个湖，含里海、贝加尔、五大湖、维多利亚…）
      下载自 jsdelivr 镜像的 natural-earth-vector（GitHub 直连在本机握手失败）。

相比之前"手工 50 个中心点 + 漫水"的版本：
    · 形状是真的（不再是漫水顺着低地爬出来的形状）
    · 不用优先漫水 ⇒ 配色从 60 s 降到 ~10 s
    · 里海沿岸低地不会再被当成水（多边形边界就在岸线上）

湖深 = 湖面高程 − 湖底高程；湖面用多边形内高程的 95 分位近似（近岸高处≈水面）。
"""
from __future__ import annotations

import json
import os

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

#: 湖水色（无深度信息时的兜底；与最浅海色一致）
LAKE = (0x26, 0xF4, 0xFF)

HERE = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(os.path.dirname(HERE), 'data', 'lakes', 'ne_10m_lakes.geojson')

_mask_cache = {}


def _rings(geom):
    """把 Polygon / MultiPolygon 拆成 [外环, 内环...] 的列表（每个环是 [(lon,lat)...]）"""
    t = geom.get('type')
    if t == 'Polygon':
        return [geom['coordinates']]
    if t == 'MultiPolygon':
        return geom['coordinates']
    return []


def rasterize(w: int, h: int) -> np.ndarray:
    """把湖泊多边形画成 w×h 的布尔掩膜（跨越 180° 经线的环画两遍）。"""
    key = (w, h)
    if key in _mask_cache:
        return _mask_cache[key]
    img = Image.new('L', (w, h), 0)
    d = ImageDraw.Draw(img)
    if not os.path.exists(GEOJSON):
        _mask_cache[key] = np.zeros((h, w), dtype=bool)
        return _mask_cache[key]

    with open(GEOJSON, 'r', encoding='utf-8') as f:
        gj = json.load(f)

    def X(lon):
        return (lon + 180.0) / 360.0 * w

    def Y(lat):
        return (90.0 - lat) / 180.0 * h

    for feat in gj.get('features', []):
        for poly in _rings(feat.get('geometry') or {}):
            if not poly:
                continue
            outer = poly[0]
            lons = [p[0] for p in outer]
            wraps = (max(lons) - min(lons)) > 180.0
            for shift in ((0.0, 360.0, -360.0) if wraps else (0.0,)):
                pts = [(X(p[0] + shift), Y(p[1])) for p in outer]
                if max(x for x, _ in pts) < 0 or min(x for x, _ in pts) > w:
                    continue
                d.polygon(pts, fill=255)
                for hole in poly[1:]:
                    d.polygon([(X(p[0] + shift), Y(p[1])) for p in hole], fill=0)
    m = np.array(img) > 127
    _mask_cache[key] = m
    return m


def lake_mask(z: np.ndarray, *, return_surface: bool = False):
    """返回湖掩膜（原分辨率）；return_surface=True 时另返回"湖面高程"数组。"""
    h, w = z.shape
    m = rasterize(w, h)
    if not return_surface:
        return m

    # 湖面高程：每个连通湖取"多边形内高程的 95 分位"（近岸≈水面；湖底是负值）
    surf = np.zeros((h, w), dtype=np.float32)
    lab, n = ndimage.label(m, structure=np.ones((3, 3), int))
    if n:
        idx = np.arange(1, n + 1)
        # 分位不好向量化，用"腐蚀后的边界高处"近似：各湖 z 的 95 分位
        order = np.argsort(lab.ravel())
        flat_lab = lab.ravel()[order]
        flat_z = z.ravel()[order]
        starts = np.searchsorted(flat_lab, idx, side='left')
        ends = np.searchsorted(flat_lab, idx, side='right')
        for i, (s, e) in enumerate(zip(starts, ends)):
            if e > s:
                surf.ravel()[order[s:e]] = np.percentile(flat_z[s:e], 95)
    return m, surf
