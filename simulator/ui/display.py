# simulator/ui/display.py
"""参考图式显示界面(替代旧地图视图)。

参考图(IR-BW OLR 双域)还原:
  - 白底画布; D01(左, 区域图) + D02(右, 台风中心追踪) 双面板并排
  - 每面板左上标题 "D0X Outgoing Longwave Radiation (W/m²)" + 时间戳
  - 灰色虚线经纬网格、黄色海岸线、四边经纬刻度
  - 底部居中色标(100→300 W/m², 刻度 + 单位)
  - 台风符号 + 追踪中心十字标记

图层形态:
  dual   : D01/D02 双域(OLR/红外/可见光/全球云图/SST/OHC/切变/海压/ITCZ/雷达/地面/中低层/高空/路径对比)
  single : 单张整幅图(台风符号+路径)
  table  : 上风速表 + 下气压表(单站时间序列 / 中心强度演化)

D01 区域可拖动(左键)与缩放(滚轮), 区域中心/跨度可在设置面板调整;
D02 以被追踪台风为中心(多台风时通过底部功能栏选择追踪对象)。
"""
from __future__ import annotations
import math
import os
from datetime import datetime

import numpy as np
import pygame

from simulator.env.field_io import NLAT, NLON, LATS, LONS

from . import theme as T

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRID_C = (200, 200, 200)
AXIS_C = (60, 60, 70)
LAND_EDGE = (230, 200, 60)
PANEL_BORDER = (120, 120, 120)

# 图层元数据: layer_id → (标题, 单位, 形态)
LAYER_META = {
    'olr':         ('Outgoing Longwave Radiation', 'W/m²', 'dual'),
    'cloud_ir':    ('Infrared Enhanced Imagery', '', 'dual'),
    'cloud_vis':   ('Visible Imagery', '', 'dual'),
    'cloud_globe': ('Global Cloud Composite', '', 'dual'),
    'sst':         ('Sea Surface Temperature', '°C', 'dual'),
    'ohc':         ('Ocean Heat Content', 'kJ/cm²', 'dual'),
    'shear':       ('Vertical Wind Shear', 'kt', 'dual'),
    'slp':         ('Sea Level Pressure', 'hPa', 'dual'),
    'itcz':        ('ITCZ / Monsoon Trough / Ridge', '', 'dual'),
    'video_refl':  ('Radar Reflectivity', 'dBZ', 'dual'),
    'video_sfc':   ('Surface SLP & 10m Wind', 'hPa', 'dual'),
    'video_mid':   ('500hPa Height & 850hPa Wind', 'hPa', 'dual'),
    'video_high':  ('200hPa Wind & Jet Stream', 'kt', 'dual'),
    'video_track': ('Track Comparison', 'hPa', 'dual'),
    'typhoon':     ('Typhoon Track & Symbols', '', 'single'),
    'video_station': ('Station Time Series', '', 'table'),
    'video_center':  ('Typhoon Intensity Evolution', '', 'table'),
}

# 非 OLR 图层色标: layer_id → (colors, lo, hi)
COLORBARS = {
    'sst': ([(90, 190, 235), (120, 220, 120), (250, 200, 90), (240, 90, 90)], 16, 32),
    'ohc': ([(30, 30, 110), (40, 110, 220), (90, 220, 150),
             (250, 220, 90), (210, 70, 40)], 40, 170),
    'shear': ([(70, 200, 90), (230, 230, 90), (240, 130, 60), (210, 50, 50)], 0, 30),
    'slp': ([(220, 60, 60), (240, 210, 90), (130, 220, 130),
             (90, 160, 220), (60, 60, 180)], 995, 1030),
    'video_sfc': ([(220, 60, 60), (240, 210, 90), (130, 220, 130),
                   (90, 160, 220), (60, 60, 180)], 995, 1030),
    'video_mid': ([(220, 60, 60), (240, 210, 90), (130, 220, 130),
                   (90, 160, 220), (60, 60, 180)], 995, 1030),
    'video_refl': ([(0, 0, 255), (0, 128, 255), (0, 200, 255), (0, 200, 0),
                    (0, 255, 0), (255, 255, 0), (255, 140, 0), (255, 0, 0),
                    (255, 0, 255)], 5, 75),
}

# 基础场分辨率(0-360°, 60N-60S)
BASE_W, BASE_H = 720, 241

# 台风符号颜色(按强度)
_TC_COLORS = [(120, 200, 230), (250, 220, 60), (240, 130, 60), (230, 70, 70)]
_D02_SPAN = (10.0, 12.5)          # D02 经纬跨度(参考图: 120-132.5E, 20-30N)

_CACHE: dict = {}
_CACHE_MAX = 64


def _clear_cache():
    _CACHE.clear()


def _olr_gray(v):
    """100→300 W/m²: 白=100(深对流)→黑=300。"""
    return int(max(0, min(255, 255 - (v - 100) / 200.0 * 255)))


def _colorize_arr(fld, colors, lo, hi):
    """(121,360) 场 → (BASE_H, BASE_W, 3) RGB。"""
    a = np.nan_to_num(fld, nan=lo)
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    cm = np.array(colors, dtype=np.uint8)
    idx = t * (len(colors) - 1)
    i0 = np.clip(np.floor(idx).astype(int), 0, len(colors) - 2)
    f = (idx - i0)[..., None]
    rgb = (cm[i0] * (1 - f) + cm[i0 + 1] * f).astype(np.uint8)   # (121,360,3)
    from scipy.ndimage import zoom
    out = np.zeros((BASE_H, BASE_W, 3), dtype=np.uint8)
    for c in range(3):
        out[..., c] = zoom(rgb[..., c].astype(np.float32),
                           (BASE_H / 121, BASE_W / 360), order=1).astype(np.uint8)
    return out


def _field_to_base(var, fld, layer_id):
    """场数组 → 基础 RGB(A) 数组。"""
    if layer_id == 'olr':
        g = np.nan_to_num(fld, nan=300.0)
        g = np.clip(255 - ((g - 100) / 200.0 * 255), 0, 255).astype(np.uint8)
        from scipy.ndimage import zoom
        g2 = zoom(g.astype(np.float32), (BASE_H / 121, BASE_W / 360), order=1)
        g2 = np.clip(g2, 0, 255).astype(np.uint8)
        return np.stack([g2, g2, g2], axis=-1)
    if layer_id == 'video_refl':
        idx = np.clip(((np.nan_to_num(fld, nan=0) - 5) / 70.0
                       * (len(COLORBARS['video_refl'][0]) - 1)).astype(int),
                      0, len(COLORBARS['video_refl'][0]) - 1)
        lut = np.array(COLORBARS['video_refl'][0], dtype=np.uint8)
        rgb = lut[idx]
        from scipy.ndimage import zoom
        out = np.zeros((BASE_H, BASE_W, 3), dtype=np.uint8)
        for c in range(3):
            out[..., c] = zoom(rgb[..., c].astype(np.float32),
                               (BASE_H / 121, BASE_W / 360), order=0).astype(np.uint8)
        return out
    # S1: shear 场为 m/s, 色标范围为 kt(0-30), 需与主图层(_build_env_layer)一致先换算
    if layer_id == 'shear':
        fld = np.nan_to_num(fld, nan=0.0) * 1.944
    cb = COLORBARS.get(layer_id)
    if cb:
        return _colorize_arr(fld, cb[0], cb[1], cb[2])
    return None


def _synth_olr_arr(api, dt, tys):
    """OLR 合成(与 video_style 一致): 高湿/台风区低 OLR。"""
    olr = np.full((NLAT, NLON), 300.0)
    if api is not None:
        rh = None
        try:
            rh = api.get_field('rh700', dt)
        except Exception:
            rh = None
        if rh is not None:
            olr = 300.0 - 1.8 * (np.nan_to_num(rh) - 30.0)
    if tys:
        ty = tys[0]
        la, lo, w = ty['la'], ty['lo'], ty['w']
        dlon = (LONS - lo + 180) % 360 - 180
        d = np.hypot(LATS[:, None] - la, dlon[None, :])
        r_cdo = max(1.0, (60.0 + 0.45 * w) / 111.0)
        eye_r = max(0.2, (25.0 - 0.05 * w) / 111.0)
        core = 120.0 + 60.0 * (1.0 - w / 200.0)
        out = np.where(d <= eye_r, 260.0, olr)
        out = np.where((d > eye_r) & (d <= r_cdo), core, out)
        return out
    return olr


def _cloud_base(layer_id, dt):
    """云图层基础场(RGBA, 陆地透明)。"""
    from simulator.clouds.render import CloudRenderer
    from simulator.ui.main import _video_api, _video_typhoons_full, UI_SETTINGS
    api = _video_api()
    tys = _video_typhoons_full(dt)
    params = {}
    if api is not None:
        for name in ('itcz', 'monsoon_trough', 'ridge'):
            try:
                params[name] = api.get_param(name, dt)
            except Exception:
                pass
    colormap = UI_SETTINGS.get('colormap') if layer_id != 'cloud_vis' else None
    cr = CloudRenderer(params=params, typhoons=tys, res=0.5, colormap=colormap)
    tb = cr.tb_field()
    albedo = np.clip((25.0 - tb) / 75.0, 0.0, 1.0)
    if layer_id == 'cloud_vis':
        v = (albedo * 255).astype(np.uint8)
        rgb = np.stack([v, v, v], axis=-1)
        alpha = albedo * 240.0
    else:
        rgb = cr.rgb_from_tb(tb)
        if layer_id == 'cloud_globe':
            rgb = (rgb.astype(np.float32) * (0.45 + 0.55 * albedo[..., None])).astype(np.uint8)
        alpha = np.clip((25.0 - tb) / 55.0, 0.0, 0.9) * 255.0
    rgba = np.concatenate([rgb, alpha[..., None].astype(np.uint8)], axis=-1)
    from scipy.ndimage import zoom
    out = np.zeros((BASE_H, BASE_W, 4), dtype=np.uint8)
    for c in range(4):
        z = zoom(rgba[..., c].astype(np.float32), (BASE_H / rgba.shape[0],
                                                   BASE_W / rgba.shape[1]), order=1)
        out[..., c] = np.clip(z, 0, 255).astype(np.uint8)
    return out


def _env_base(layer_id, dt):
    """环境/视频场基础 RGB 数组(api 缺失时返回 None)。"""
    from simulator.ui.main import _video_api
    api = _video_api()
    if api is None:
        return None
    if layer_id in ('cloud_ir', 'cloud_vis', 'cloud_globe'):
        return _cloud_base(layer_id, dt)
    if layer_id == 'olr':
        from simulator.ui.main import _video_typhoons_full
        fld = None
        try:
            fld = api.get_field('olr', dt)
        except Exception:
            fld = None
        if fld is None:
            fld = _synth_olr_arr(api, dt, _video_typhoons_full(dt))
        return _field_to_base('olr', fld, 'olr')
    var = {'sst': 'sst', 'ohc': 'ohc', 'shear': 'shear',
           'slp': 'mslp', 'video_sfc': 'mslp', 'video_mid': 'mslp'}.get(layer_id)
    if var is None:
        return None
    try:
        fld = api.get_field(var, dt)
    except Exception:
        fld = None
    if fld is None:
        return None
    if layer_id in ('slp', 'video_sfc', 'video_mid'):
        # 等压线(深灰细线)直接画进基础场
        a = np.nan_to_num(fld, nan=1013.0)
        from scipy.ndimage import zoom
        base = _field_to_base(layer_id, fld, layer_id)
        for lev in (1004.0, 1008.0, 1012.0, 1016.0, 1020.0):
            sign = np.sign(a - lev)
            cross = np.abs(np.diff(sign, axis=1)) > 1.0
            ys, xs = np.where(cross)
            if not len(ys):
                continue
            px = ((xs + 0.5) / 360.0 * BASE_W).astype(int)
            py = (ys / 120.0 * BASE_H).astype(int)
            for x, y in zip(px, py):
                if 0 <= x < BASE_W and 0 <= y < BASE_H:
                    base[y, x] = (40, 44, 54)
        return base
    return _field_to_base(layer_id, fld, layer_id)


def _itcz_base(dt):
    """ITCZ/季风槽/副高 → 透明基础层(BASE_H×BASE_W×4)。"""
    from simulator.ui.main import _video_api
    api = _video_api()
    if api is None:
        return None
    out = np.zeros((BASE_H, BASE_W, 4), dtype=np.uint8)
    nx = 72
    lon = np.linspace(0.0, 360.0, nx)
    xs = lon / 360.0 * BASE_W

    def y_of(la):
        return int((60.0 - la) / 120.0 * BASE_H)

    def curve(base, amp, k, ph):
        return base + amp * np.sin(2 * np.pi * k * lon / 360.0 + ph)

    for name, color, width in (('itcz', (120, 220, 255), 3),
                               ('monsoon_trough', (150, 230, 140), 3),
                               ('ridge', (250, 220, 60), 3)):
        try:
            p = api.get_param(name, dt)
        except Exception:
            p = None
        if not p:
            continue
        cy = curve(p.get('lat', 0.0), 2.5, 2, 0.6)
        for x, la in zip(xs, cy):
            y = y_of(la)
            if 0 <= y < BASE_H:
                for yy in range(max(0, y - 1), min(BASE_H, y + 2)):
                    xx = int(x)
                    if 0 <= xx < BASE_W:
                        out[yy, xx, :3] = color
                        out[yy, xx, 3] = 255
    return out


def _track_base(dt):
    """路径对比(按中心气压着色) → 透明基础层。"""
    from simulator.ui.main import _VIDEO_CTX
    from simulator.render.video_style import track_pressure_color
    st = _VIDEO_CTX['states']
    if not st:
        return None
    out = np.zeros((BASE_H, BASE_W, 4), dtype=np.uint8)

    def t_dt(s):
        try:
            return datetime.strptime(str(s['t']), '%Y%m%d%H')
        except Exception:
            return datetime(2000, 1, 1)

    by_id = {}
    for s in st:
        by_id.setdefault(s.get('id', 0), []).append(s)
    for tid, rows in by_id.items():
        rows = sorted(rows, key=lambda s: s.get('t', ''))
        rows = [r for r in rows if t_dt(r) <= dt]
        if len(rows) < 2:
            continue
        pts = [(int(r['lo'] % 360.0 / 360.0 * BASE_W),
                int((60.0 - r['la']) / 120.0 * BASE_H)) for r in rows]
        # S2: 按段首状态着色; 不能用 pts.index((x0,y0))——慢速台风多段像素重合时
        # 会取到首个相同像素, 导致颜色错配
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:])):
            c = track_pressure_color(rows[i]['p'])
            _line(out, x0, y0, x1, y1, c)
    return out


def _line(arr, x0, y0, x1, y1, color, w=2):
    """在 (H,W,4) 数组上画线段。"""
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        for yy in range(max(0, y0 - w // 2), min(arr.shape[0], y0 + w // 2 + 1)):
            for xx in range(max(0, x0 - w // 2), min(arr.shape[1], x0 + w // 2 + 1)):
                arr[yy, xx, :3] = color
                arr[yy, xx, 3] = 255
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


# ════════════════════════════════════════════ 显示视图 ════════════════════════════════════════════

class DisplayView:
    """中央显示界面(参考图式)。"""

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        # D01 区域设置(默认参考图范围: 90-160°E, 0-40°N)
        self.region = {'lat': 20.0, 'lon': 125.0,
                       'lat_span': 40.0, 'lon_span': 70.0}
        self.track_id = None            # 追踪台风 id(None = 自动选最强)
        self.drag = None                # (start_pos, start_lon, start_lat)
        self.table_scroll = 0           # 表格图层滚动
        self.title_inset = 0            # M3: 左上角状态框存在时, D01 标题右移避开
        self._panels = {}               # layer_id → (d01_rect, d02_rect)

    # ── 事件 ──

    def handle_event(self, e):
        """左键拖动平移 D01 区域 / 滚轮缩放区域; 表格图层滚轮滚动。"""
        r = self.rect
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.drag is None and r.collidepoint(e.pos):
                self.drag = (e.pos, self.region['lon'], self.region['lat'])
                return True
        elif e.type == pygame.MOUSEMOTION and self.drag:
            p0, lon0, lat0 = self.drag
            dx = e.pos[0] - p0[0]
            dy = e.pos[1] - p0[1]
            span_lon = self.region['lon_span']
            span_lat = self.region['lat_span']
            # 以视图宽度为基准换算拖拽距离(未绘制过时回退视图尺寸)
            w_ref = r.w or 1000
            h_ref = r.h or 600
            self.region['lon'] = (lon0 - dx / w_ref * span_lon) % 360.0
            self.region['lat'] = max(-55.0, min(55.0,
                                                lat0 + dy / h_ref * span_lat))
            return True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.drag = None
            return True
        elif e.type == pygame.MOUSEWHEEL:
            mp = pygame.mouse.get_pos()
            if not r.collidepoint(mp):
                return False
            d01 = self._panels.get('__d01')
            if d01 is not None and d01.collidepoint(mp):
                # 区域缩放
                f = 0.85 if e.y > 0 else 1.18
                self.region['lon_span'] = max(15.0, min(120.0,
                                                        self.region['lon_span'] * f))
                self.region['lat_span'] = max(10.0, min(80.0,
                                                        self.region['lat_span'] * f))
            else:
                self.table_scroll += e.y * 2
            return True
        return False

    # ── 绘制入口 ──

    def draw(self, surface, layer_id, sim_time, opacity: int = 100):
        r = self.rect
        surface.fill(WHITE, r)
        meta = LAYER_META.get(layer_id)
        if meta is None:
            meta = ('', '', 'dual')
        kind = meta[2]
        from simulator.ui.main import _video_datetime
        dt = _video_datetime(sim_time)
        if kind == 'table':
            self._draw_tables(surface, layer_id, dt)
        elif kind == 'single':
            self._draw_single(surface, layer_id, dt)
        else:
            self._draw_dual(surface, layer_id, dt)
        pygame.draw.rect(surface, (190, 195, 205), r, 1)

    # ── 公共: 台风信息 ──

    def active_typhoons(self, dt):
        """dt 时刻所有活跃台风(按 id 取最近状态)。"""
        from simulator.ui.main import _VIDEO_CTX
        st = _VIDEO_CTX['states']
        if not st:
            return []

        def t_dt(s):
            try:
                return datetime.strptime(str(s['t']), '%Y%m%d%H')
            except Exception:
                return datetime(2000, 1, 1)

        by_id = {}
        for s in st:
            by_id.setdefault(s.get('id', 0), []).append(s)
        out = []
        for tid, rows in by_id.items():
            best = min(rows, key=lambda s: abs((t_dt(s) - dt).total_seconds()))
            out.append(dict(best))
        out.sort(key=lambda s: -s.get('w', 0))
        return out

    def tracked(self, dt):
        """被追踪台风状态(dt 时刻最近)。"""
        tys = self.active_typhoons(dt)
        if not tys:
            return None
        if self.track_id is None:
            return tys[0]
        for ty in tys:
            if ty.get('id') == self.track_id:
                return ty
        return tys[0]

    def set_region(self, lat=None, lon=None, lat_span=None, lon_span=None):
        if lat is not None:
            self.region['lat'] = max(-55.0, min(55.0, lat))
        if lon is not None:
            self.region['lon'] = lon % 360.0
        if lat_span is not None:
            self.region['lat_span'] = max(10.0, min(80.0, lat_span))
        if lon_span is not None:
            self.region['lon_span'] = max(15.0, min(120.0, lon_span))

    def reset(self):
        """重置 D01 区域到默认参考图范围。"""
        self.region = {'lat': 20.0, 'lon': 125.0,
                       'lat_span': 40.0, 'lon_span': 70.0}
        self.table_scroll = 0

    # ── 双域绘制 ──

    def _draw_dual(self, surface, layer_id, dt):
        r = self.rect
        meta = LAYER_META.get(layer_id, ('', '', 'dual'))
        title, unit = meta[0], meta[1]
        cb_h = 52 if (layer_id == 'olr' or layer_id in COLORBARS) else 26
        pan_top = r.y + 8
        pan_h = r.h - 16 - cb_h
        gap = 14
        margin = 40
        pw = max(1, (r.w - margin * 2 - gap) // 2)
        d01 = pygame.Rect(r.x + margin, pan_top, pw, pan_h)
        d02 = pygame.Rect(r.right - margin - pw, pan_top, pw, pan_h)
        self._panels['__d01'] = d01
        self._panels['__d02'] = d02

        tracked = self.tracked(dt)
        tys = self.active_typhoons(dt)
        # D01: 用户区域; D02: 以追踪台风为中心
        c0, c1, la0, la1 = self._window_span(self.region)
        if tracked is not None:
            tla, tlo = tracked['la'], tracked['lo']
            d2_la, d2_lo = _D02_SPAN
            c20 = (tlo - d2_lo / 2) % 360
            c21 = (tlo + d2_lo / 2) % 360
            la20, la21 = tla - d2_la / 2, tla + d2_la / 2
        else:
            c20, c21, la20, la21 = 90.0, 130.0, 10.0, 30.0
        # 先画各面板底图/数据
        self._draw_panel(surface, d01, layer_id, dt, 'D01', c0, c1, la0, la1,
                         show_lat_side='left', tys=tys, tracked=tracked)
        self._draw_panel(surface, d02, layer_id, dt, 'D02', c20, c21, la20, la21,
                         show_lat_side='right', tys=tys, tracked=tracked)
        # 色标
        if layer_id == 'olr':
            self._draw_olr_colorbar(surface, r)
        elif layer_id in COLORBARS:
            self._draw_colorbar(surface, r, *COLORBARS[layer_id], unit)

    def _window_span(self, region):
        c = region['lon']
        hw = region['lon_span'] / 2
        c0, c1 = (c - hw) % 360, (c + hw) % 360
        if c1 <= c0:
            c1 = c0 + region['lon_span']     # 跨 0° 时保持跨度
        la0 = region['lat'] - region['lat_span'] / 2
        la1 = region['lat'] + region['lat_span'] / 2
        return c0, c1, la0, la1

    def _draw_panel(self, surface, rect, layer_id, dt, prefix,
                    c0, c1, la0, la1, show_lat_side, tys, tracked):
        # G7: 未知/未登记图层回退默认元数据, 避免 KeyError 崩溃
        meta = LAYER_META.get(layer_id, ('', '', 'dual'))
        pygame.draw.rect(surface, WHITE, rect)
        pygame.draw.rect(surface, PANEL_BORDER, rect, 1)
        # 标题 + 时间戳(参考图: 左上角; M3: D01 标题避开左上角状态框)
        title = f"{prefix} {meta[0]}" + (f" ({meta[1]})" if meta[1] else "")
        ts = T.text(17, title, BLACK)
        ins = self.title_inset if prefix == 'D01' else 0
        surface.blit(ts, (rect.x + 10 + ins, rect.y + 8))
        dt_s = T.text(15, dt.strftime('%Y-%m-%d %H:%M:%S'), AXIS_C)
        surface.blit(dt_s, (rect.x + 12 + ins, rect.y + 34))
        # 面板内绘图区(按纵横比留白, 上下留标题/色标空间)
        # M1: title_h 加高, 顶部经度刻度(inner.y-20)不与时间戳重叠
        title_h = 72
        avail = pygame.Rect(rect.x + 6, rect.y + title_h,
                            rect.w - 12, rect.h - title_h - 8)
        span_lon = ((c1 - c0) % 360.0) or 360.0
        span_lat = abs(la1 - la0)
        aspect = span_lon / max(1e-9, span_lat)
        # G4: 面板过小/被裁剪时确保内区至少 1px, 防 smoothscale(0,0) / 除零崩溃
        hh = max(1.0, min(avail.h, avail.w / aspect))
        ww = max(1.0, hh * aspect)
        inner = pygame.Rect(int(avail.centerx - ww / 2), int(avail.centery - hh / 2),
                            max(1, int(ww)), max(1, int(hh)))
        # 1) 数据底图
        base = self._layer_base(layer_id, dt)
        if base is not None:
            self._blit_window(surface, inner, base, c0, c1, la0, la1, layer_id)
        else:
            self._placeholder(surface, inner)
        # 2) 虚线网格 + 海岸线 + 刻度(参考图样式; 先于台风符号绘制, M2)
        self._draw_grid(surface, inner, c0, c1, la0, la1)
        self._draw_coastline(surface, inner, c0, c1, la0, la1)
        # 3) 台风叠加(符号 + 追踪十字, 画在网格/海岸线之上)
        if layer_id != 'video_track':
            self._draw_typhoons(surface, inner, c0, c1, la0, la1, tys, tracked,
                                layer_id == 'olr')
        self._draw_ticks(surface, rect, inner, c0, c1, la0, la1, show_lat_side)
        if layer_id in ('video_sfc', 'video_mid', 'video_high'):
            self._draw_barbs(surface, inner, dt, c0, c1, la0, la1, layer_id)

    def _layer_base(self, layer_id, dt):
        key = (layer_id, dt.strftime('%Y%m%d%H'))
        if key in _CACHE:
            return _CACHE[key]
        if layer_id == 'itcz':
            base = _itcz_base(dt)
        elif layer_id == 'video_track':
            base = _track_base(dt)
        elif layer_id == 'video_refl':
            from simulator.ui.main import _video_typhoons_full
            from simulator.render.video_style import _synth_refl
            fld = _synth_refl(_video_typhoons_full(dt), (0, 360, 60, -60))
            base = _field_to_base('video_refl', fld, 'video_refl')
        else:
            base = _env_base(layer_id, dt)
        if base is not None:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = base
        return base

    def _blit_window(self, surface, inner, base, c0, c1, la0, la1, layer_id):
        """基础场(0-360, 60N-60S) → 窗口裁剪放大。base: (BASE_H, BASE_W, 3|4)。"""
        span_lon = ((c1 - c0) % 360.0) or 360.0
        # 源像素范围(经度可跨 0°)
        x0f = (c0 % 360.0) / 360.0 * BASE_W
        x1f = x0f + span_lon / 360.0 * BASE_W
        lat0c, lat1c = max(-60.0, la0), min(60.0, la1)
        y1f = (60.0 - lat1c) / 120.0 * BASE_H      # 北界 → 上
        y0f = (60.0 - lat0c) / 120.0 * BASE_H
        h = int(round(abs(y1f - y0f)))
        if h <= 0:
            return
        arr = base
        # 逐行采样(水平插值由 smoothscale 完成)
        rows = np.clip(np.linspace(min(y0f, y1f), max(y0f, y1f) - 1, h).astype(int),
                       0, BASE_H - 1)
        # L1: 列数取整(避免端点恰为整数时多取一列回绕)
        n_cols = max(1, int(round(x1f - x0f)))
        cols = np.arange(int(x0f), int(x0f) + n_cols) % BASE_W
        sub = arr[np.ix_(rows, cols)]
        if sub.shape[2] == 3:
            surf = pygame.image.frombuffer(np.ascontiguousarray(sub).tobytes(),
                                           (sub.shape[1], sub.shape[0]), 'RGB')
        else:
            surf = pygame.image.frombuffer(np.ascontiguousarray(sub).tobytes(),
                                           (sub.shape[1], sub.shape[0]), 'RGBA')
        scaled = pygame.transform.smoothscale(surf, (inner.w, inner.h))
        surface.blit(scaled, inner.topleft)

    def _draw_typhoons(self, surface, inner, c0, c1, la0, la1, tys, tracked, is_olr):
        span_lon = ((c1 - c0) % 360.0) or 360.0
        for ty in tys:
            la, lo = ty['la'], ty['lo']
            # H1: 窗口左边界式映射(与网格/刻度一致)
            dlo = (lo - c0) % 360.0
            if dlo > span_lon or not (-2 <= dlo <= span_lon + 2):
                continue
            if not (la0 - 2 <= la <= la1 + 2):
                continue
            x = inner.x + int(dlo / span_lon * inner.w)
            y = inner.y + int((la1 - la) / (la1 - la0) * inner.h)
            if not inner.inflate(8, 8).collidepoint(x, y):
                continue
            w = ty.get('w', 0)
            ci = min(3, max(0, int(w // 45)))
            color = _TC_COLORS[ci]
            pygame.draw.circle(surface, color, (x, y), 7)
            pygame.draw.circle(surface, BLACK, (x, y), 7, 1)
            if is_olr and tracked is not None and ty.get('id') == tracked.get('id'):
                # 追踪中心十字
                pygame.draw.line(surface, BLACK, (x - 13, y), (x + 13, y), 1)
                pygame.draw.line(surface, BLACK, (x, y - 13), (x, y + 13), 1)
                pygame.draw.circle(surface, BLACK, (x, y), 10, 1)

    def _draw_barbs(self, surface, inner, dt, c0, c1, la0, la1, layer_id):
        """风羽(850 引导流, 3° 间距)。"""
        from simulator.ui.main import _video_api
        from simulator.render.video_style import draw_barb
        api = _video_api()
        if api is None:
            return
        try:
            uv = api.get_field('uv_steer', dt)
        except Exception:
            uv = None
        if uv is None:
            return
        span_lon = ((c1 - c0) % 360.0) or 360.0
        step = 3.0
        for la in np.arange(la0, la1, step):
            for lo in np.arange(c0, c0 + span_lon, step):
                la_i = int(np.clip(round(la + 60), 0, NLAT - 1))
                lo_i = int(lo % 360)
                u, v = float(uv[0][la_i, lo_i]), float(uv[1][la_i, lo_i])
                if math.isnan(u) or math.isnan(v):
                    continue
                # H2: 左边界式映射(与 _draw_typhoons/网格一致)
                dlo = (lo - c0) % 360.0
                x = inner.x + int(dlo / span_lon * inner.w)
                y = inner.y + int((la1 - la) / (la1 - la0) * inner.h)
                if inner.collidepoint(x, y):
                    draw_barb(surface, x, y, u, v, scale=1.4)

    # ── 框架(网格/海岸线/刻度)──

    def _draw_grid(self, surface, inner, c0, c1, la0, la1):
        span_lon = ((c1 - c0) % 360.0) or 360.0
        step = _nice_step(span_lon / 6.0)
        for lo in np.arange(math.floor(c0 / step) * step, c0 + span_lon + 0.01, step):
            x = inner.x + int(((lo - c0) % 360.0) / span_lon * inner.w)
            if 0 <= x - inner.x <= inner.w:
                _dashed_v(surface, x, inner)
        step_lat = _nice_step((la1 - la0) / 6.0)
        for la in np.arange(math.floor(min(la0, la1) / step_lat) * step_lat,
                            max(la0, la1) + 0.01, step_lat):
            y = inner.y + int((la1 - la) / (la1 - la0) * inner.h)
            if 0 <= y - inner.y <= inner.h:
                _dashed_h(surface, y, inner)

    def _draw_coastline(self, surface, inner, c0, c1, la0, la1):
        from scipy.ndimage import binary_dilation
        from simulator.render.video_style import land_mask_for
        span_lon = ((c1 - c0) % 360.0) or 360.0
        m = land_mask_for(inner.w, inner.h, c0, c0 + span_lon,
                          max(-60, la0), min(60, la1))
        if not m.any():
            return
        edge = binary_dilation(m, iterations=1) & ~m
        ys, xs = np.where(edge)
        for x, y in zip(xs[::2], ys[::2]):
            pygame.draw.rect(surface, LAND_EDGE, (inner.x + x, inner.y + y, 2, 2))

    def _draw_ticks(self, surface, rect, inner, c0, c1, la0, la1, side):
        span_lon = ((c1 - c0) % 360.0) or 360.0
        f = T.font(13)
        step = _nice_step(span_lon / 6.0)
        # 经度: 上下(顶部标签紧贴内区上缘, 不与面板标题/时间戳重叠)
        for lo in np.arange(math.floor(c0 / step) * step, c0 + span_lon + 0.01, step):
            x = inner.x + int(((lo - c0) % 360.0) / span_lon * inner.w)
            if not (inner.x <= x <= inner.right):
                continue
            tag = _lon_tag(lo)
            s = f.render(tag, True, AXIS_C)
            surface.blit(s, (x - s.get_width() // 2, inner.y - 20))
            # L2: 底刻度画在内区下缘 +6px, 避免贴色标上沿/跨面板边框
            surface.blit(s, (x - s.get_width() // 2, inner.bottom + 6))
        # 纬度: 外缘(side='left' 画左侧, 'right' 画右侧)
        step_lat = _nice_step((la1 - la0) / 6.0)
        for la in np.arange(math.floor(min(la0, la1) / step_lat) * step_lat,
                            max(la0, la1) + 0.01, step_lat):
            y = inner.y + int((la1 - la) / (la1 - la0) * inner.h)
            if not (inner.y <= y <= inner.bottom):
                continue
            # L3: 负零显示 "0°" 而非 "0°S"
            if abs(la) < 0.5:
                tag = "0°"
            else:
                tag = f"{abs(la):.0f}°{'N' if la > 0 else 'S'}"
            s = f.render(tag, True, AXIS_C)
            if side == 'left':
                surface.blit(s, (rect.x + 8, y - s.get_height() // 2))
            else:
                surface.blit(s, (rect.right - 8 - s.get_width(),
                                 y - s.get_height() // 2))

    def _placeholder(self, surface, inner):
        ts = T.text(18, '该图层数据未生成', T.TEXT_DIM)
        surface.blit(ts, (inner.centerx - ts.get_width() // 2,
                          inner.centery - ts.get_height() // 2))

    # ── 色标 ──

    def _draw_olr_colorbar(self, surface, r):
        bw = min(560, r.w // 4)
        bh = 18
        x0 = r.centerx - bw // 2
        y0 = r.bottom - 46
        t = np.linspace(0.0, 1.0, bw)
        g = np.clip(255 - t * 255, 0, 255).astype(np.uint8)
        grad = np.repeat(g[None, :], bh, axis=0)
        bar = pygame.image.frombuffer(np.ascontiguousarray(
            np.stack([grad, grad, grad], axis=-1)).tobytes(), (bw, bh), 'RGB')
        surface.blit(bar, (x0, y0))
        pygame.draw.rect(surface, (120, 120, 120), (x0, y0, bw, bh), 1)
        f = T.font(14)
        for i, v in enumerate((100, 150, 200, 250, 300)):
            x = x0 + int((v - 100) / 200 * (bw - 1))
            s = f.render(str(v), True, AXIS_C)
            surface.blit(s, (x - s.get_width() // 2, y0 + bh + 2))
        su = T.text(15, 'W/m²', AXIS_C)
        surface.blit(su, (r.centerx - su.get_width() // 2, y0 + bh + 20))

    def _draw_colorbar(self, surface, r, colors, lo, hi, unit):
        bw = min(560, r.w // 4)
        bh = 18
        x0 = r.centerx - bw // 2
        y0 = r.bottom - 46
        t = np.linspace(0.0, 1.0, bw)
        cm = np.array(colors, dtype=np.uint8)
        idx = t * (len(colors) - 1)
        i0 = np.clip(np.floor(idx).astype(int), 0, len(colors) - 2)
        f = (idx - i0)[..., None]
        grad = (cm[i0] * (1 - f) + cm[i0 + 1] * f).astype(np.uint8)
        grad = np.repeat(grad[None, :, :], bh, axis=0)
        bar = pygame.image.frombuffer(np.ascontiguousarray(grad).tobytes(),
                                      (bw, bh), 'RGB')
        surface.blit(bar, (x0, y0))
        pygame.draw.rect(surface, (120, 120, 120), (x0, y0, bw, bh), 1)
        f2 = T.font(14)
        for i, v in enumerate((lo, (lo + hi) / 2, hi)):
            x = x0 + int((i / 2.0) * (bw - 1))
            s = f2.render(f"{v:.0f}", True, AXIS_C)
            surface.blit(s, (x - s.get_width() // 2, y0 + bh + 2))
        su = T.text(15, unit, AXIS_C)
        surface.blit(su, (r.centerx - su.get_width() // 2, y0 + bh + 20))

    # ── 单图(台风符号+路径)──

    def _draw_single(self, surface, layer_id, dt):
        r = self.rect
        # 单图/表格图层不再复用上一次双域绘制的 D01/D02 面板几何,
        # 否则滚轮在这些图层上会误缩放已失效的 D01 区域(状态残留 bug)
        self._panels.clear()
        meta = LAYER_META.get(layer_id, ('', '', 'single'))
        title = meta[0] + (f" ({meta[1]})" if meta[1] else "")
        pygame.draw.rect(surface, WHITE, r)
        pygame.draw.rect(surface, PANEL_BORDER, r, 1)
        ts = T.text(20, title, BLACK)
        surface.blit(ts, (r.x + 12, r.y + 10))
        dt_s = T.text(16, dt.strftime('%Y-%m-%d %H:%M:%S'), AXIS_C)
        surface.blit(dt_s, (r.x + 14, r.y + 38))
        inner = pygame.Rect(r.x + 50, r.y + 66, max(1, r.w - 100), max(1, r.h - 100))
        # 路径 + 符号(参考图黄海岸线 + 虚线网格)
        self._draw_grid(surface, inner, 90, 180, 0, 50)
        self._draw_coastline(surface, inner, 90, 180, 0, 50)
        from simulator.ui.main import _VIDEO_CTX
        st = _VIDEO_CTX['states']
        if not st:
            self._placeholder(surface, inner)
            pygame.draw.rect(surface, PANEL_BORDER, inner, 1)
            return

        def t_dt(s):
            try:
                return datetime.strptime(str(s['t']), '%Y%m%d%H')
            except Exception:
                return datetime(2000, 1, 1)

        by_id = {}
        for s in st:
            by_id.setdefault(s.get('id', 0), []).append(s)
        colors = [(120, 200, 230), (250, 220, 60), (240, 130, 60), (230, 70, 70)]
        for ci, (tid, rows) in enumerate(sorted(by_id.items())):
            rows = [x for x in rows if t_dt(x) <= dt]
            if not rows:
                continue
            color = colors[ci % len(colors)]
            pts = [(inner.x + int((row['lo'] % 360 - 90) / 90.0 * inner.w),
                    inner.y + int((50 - row['la']) / 50.0 * inner.h))
                   for row in rows if 90 <= (row['lo'] % 360) <= 180]
            if len(pts) > 1:
                pygame.draw.lines(surface, color, False, pts, 2)
            if pts:
                cx, cy = pts[-1]
                pygame.draw.circle(surface, color, (cx, cy), 8)
                pygame.draw.circle(surface, BLACK, (cx, cy), 8, 1)
                r34 = int(rows[-1].get('r34', 0))
                if r34 > 0:
                    pygame.draw.circle(surface, (150, 170, 200), (cx, cy),
                                       max(10, int(r34 / 111.0 / 50.0 * inner.h)), 1)
        pygame.draw.rect(surface, PANEL_BORDER, inner, 1)

    # ── 表格(上风速表 + 下气压表)──

    def _draw_tables(self, surface, layer_id, dt):
        r = self.rect
        # 表格/单图图层不再复用上一次双域绘制的 D01/D02 面板几何,
        # 否则滚轮在表格上会误缩放已失效的 D01 区域(状态残留 bug)
        self._panels.clear()
        meta = LAYER_META.get(layer_id, ('', '', 'table'))
        pygame.draw.rect(surface, WHITE, r)
        pygame.draw.rect(surface, PANEL_BORDER, r, 1)
        ts = T.text(20, meta[0], BLACK)
        surface.blit(ts, (r.x + 12, r.y + 10))
        dt_s = T.text(16, dt.strftime('%Y-%m-%d %H:%M:%S'), AXIS_C)
        surface.blit(dt_s, (r.x + 14, r.y + 38))
        # 数据
        if layer_id == 'video_station':
            from simulator.ui.main import _video_station
            st = _video_station(dt)
            name = st.get('name', 'Station')
            rows = st.get('pts', [])
            unit_w = 'm/s'
        else:
            from simulator.ui.main import _video_typhoon_states
            rows = _video_typhoon_states(dt)
            ty = self.tracked(dt)
            name = f"#{ty.get('id')}" if ty else 'Typhoon'
            unit_w = 'kt'
        if not rows:
            self._placeholder(surface, pygame.Rect(r.x + 40, r.y + 60,
                                                   r.w - 80, r.h - 100))
            return
        top_h = (r.h - 70) // 2 - 8
        bot_h = r.h - 70 - top_h - 16
        # 上: 风速表
        self._draw_table(surface, pygame.Rect(r.x + 30, r.y + 66,
                                              r.w - 60, top_h),
                         f"风速 ({unit_w}) — {name}", rows, 'w')
        # 下: 气压表
        self._draw_table(surface, pygame.Rect(r.x + 30, r.y + 66 + top_h + 16,
                                              r.w - 60, bot_h),
                         f"气压 (hPa) — {name}", rows, 'p')

    def _draw_table(self, surface, rect, title, rows, key):
        pygame.draw.rect(surface, (245, 246, 248), rect)
        pygame.draw.rect(surface, PANEL_BORDER, rect, 1)
        ts = T.text(17, title, BLACK)
        surface.blit(ts, (rect.x + 8, rect.y + 6))
        header_y = rect.y + 32
        head = pygame.Rect(rect.x + 6, header_y, rect.w - 12, 26)
        pygame.draw.rect(surface, (228, 231, 236), head)
        fh = T.font(16)
        surface.blit(fh.render('时间', True, BLACK), (head.x + 10, head.y + 5))
        surface.blit(fh.render('数值', True, BLACK), (head.x + 160, head.y + 5))
        row_h = 24
        max_rows = max(1, (rect.bottom - header_y - 30) // row_h)
        total = len(rows)
        top = max(0, min(self.table_scroll, max(0, total - max_rows)))
        old = surface.get_clip()
        surface.set_clip(rect)
        y = header_y + 30
        for i in range(top, min(total, top + max_rows)):
            s = rows[i]
            t = str(s.get('t', ''))
            tstr = f"{t[4:6]}/{t[6:8]} {t[8:10]}:00" if len(t) >= 10 else t
            v = s.get(key)
            vs = '-' if v is None else f"{float(v):.1f}"
            if i % 2 == 0:
                pygame.draw.rect(surface, (250, 250, 252),
                                 (rect.x + 6, y, rect.w - 12, row_h))
            surface.blit(fh.render(tstr, True, AXIS_C), (head.x + 10, y + 4))
            surface.blit(fh.render(vs, True, BLACK), (head.x + 160, y + 4))
            y += row_h
        surface.set_clip(old)
        # 滚动指示
        if total > max_rows:
            bar_h = max(10, int(max_rows / total * (rect.h - 60)))
            bar_y = header_y + 30 + int((rect.h - 60 - bar_h)
                                        * top / max(1, total - max_rows))
            pygame.draw.rect(surface, (160, 165, 175),
                             (rect.right - 10, bar_y, 4, bar_h), border_radius=2)


def _nice_step(span: float) -> float:
    """标尺步长取整(1/2/5 系)。"""
    mag = 10 ** math.floor(math.log10(max(1e-9, span)))
    for m in (1, 2, 5, 10):
        if span <= m * mag:
            return m * mag
    return 10 * mag


def _lon_tag(lo):
    lo = lo % 360
    if lo == 0:
        return '0°'
    if lo <= 180:
        return f'{lo:.0f}°E'
    return f'{360 - lo:.0f}°W'


def _dashed_v(surface, x, inner):
    y = inner.y
    while y < inner.bottom:
        pygame.draw.line(surface, GRID_C, (x, y), (x, min(y + 8, inner.bottom)), 1)
        y += 14


def _dashed_h(surface, y, inner):
    x = inner.x
    while x < inner.right:
        pygame.draw.line(surface, GRID_C, (x, y), (min(x + 8, inner.right), y), 1)
        x += 14
