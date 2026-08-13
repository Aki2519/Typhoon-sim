# simulator/ui/main.py
"""F5 台风架空模拟器 — 主程序界面骨架。

五区布局(不复用回放程序的对话框栈与主循环):
  ① 顶栏: 程序名 | 模拟时间+进度 | 模态摘要+种子 | 状态色
  ② 左栏: 图层控制(260px 可折叠)
  ③ 中央: 主视图(等距圆柱投影,图层渲染接口 render_layer)
  ④ 右栏: 选中台风详情 / 环境场摘要(320px 可折叠)
  ⑤ 底栏: 时间轴 + 生成控制

图层数据未接入时显示"该图层数据未生成"占位,不崩溃。
"""
from __future__ import annotations
import json
import math
import os
import sys
from datetime import datetime
import numpy as np
import pygame

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from . import theme as T

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ════════════════════ 图层定义表(数据驱动)════════════════════

LAYERS = [
    # (id, 名称, 快捷键, 已接入数据?)
    ('olr',        'OLR 云图 (IR-BW)',    '1', True),
    ('cloud_ir',   '红外增强 (IR)',       '2', True),
    ('cloud_vis',  '可见光 (VIS)',        '3', True),
    ('cloud_globe','全球云图 (合成)',     '4', True),
    ('sst',        '全球 SST',            '5', True),
    ('ohc',        'OHC',                 '6', True),
    ('shear',      '垂直风切变',          '7', True),
    ('slp',        '海平面气压',          '8', True),
    ('itcz',       'ITCZ/季风槽',         '9', True),
    ('typhoon',    '台风符号+路径',       '0', True),
    # 视频同款画面(参考图样式)
    ('video_refl', '雷达反射率',          'q', True),
    ('video_sfc',  '地面形势',            'w', True),
    ('video_mid',  '中低层形势',          'e', True),
    ('video_high', '高空形势',            'r', True),
    ('video_track','路径对比',            't', True),
    ('video_station','单站时间序列',      'y', True),
    ('video_center','中心强度演化',       'u', True),
]

PLACEHOLDER = "该图层数据未生成"

# ════════════════════ 界面设置(设置面板可改, 持久化到 settings.json)════════════════════
# 规范界面样式参考: 灰底 + 黄色陆地轮廓 + 灰色虚线经纬网格 + 坐标标注
#                    + 标题/时间戳 + 底部色标(见参考图 5612 WRF 模拟)
UI_SETTINGS = {
    'show_grid': True,          # 是否显示经纬线
    'basemap': 'spec',          # 'spec' 规范浅底 | 'map' map.png | 'dark' 暗色
    'colormap': 'CA',           # 红外色阶(CA/CC/OTT/RAMMB/RBTOP/BD/BW)
    # 区域图(D01)参数 + 单站坐标(设置面板可改, 持久化)
    'region_lat': 20.0, 'region_lon': 125.0,
    'region_lat_span': 40.0, 'region_lon_span': 70.0,
    'station_lat': '', 'station_lon': '',
}
LAND_EDGE_YELLOW = (230, 210, 60)
GRID_DASH_C = (140, 140, 150)
AXIS_TEXT_C = (60, 60, 70)
SPEC_OCEAN = (205, 212, 224)    # 规范浅灰海面

# 已知bug修复: 设置持久化(重启后保留, 不再每次重置为默认)
_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'settings.json')


def _load_settings() -> None:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in UI_SETTINGS:
                    UI_SETTINGS[k] = v
    except Exception:
        pass


def _save_settings() -> None:
    try:
        with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(UI_SETTINGS, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


_load_settings()


def set_setting(key: str, value) -> None:
    """设置面板写入 UI_SETTINGS(并清理受影响缓存, 持久化到磁盘)。"""
    UI_SETTINGS[key] = value
    _save_settings()
    if key in ('basemap', 'colormap'):
        _layer_cache.clear()
        _BASE_MAP.clear()
        _LAND_MASK_CACHE.clear()

# ════════════════════ 图层渲染接口(阶段2-5 逐个接入)════════════════════

_layer_cache: dict = {}


def render_layer(layer_id: str, sim_time, size) -> pygame.Surface:
    """统一图层渲染接口: render_layer(layer_id, sim_time) -> Surface。
    缓存 key 含 sim_time 与 size(K22: 窗口缩放后旧尺寸贴图不再复用)。"""
    key = (layer_id, sim_time, size)
    if key in _layer_cache:
        return _layer_cache[key]
    surf = _build_layer(layer_id, sim_time, size)
    if surf is not None:
        if len(_layer_cache) > 128:
            _layer_cache.pop(next(iter(_layer_cache)))
        _layer_cache[key] = surf
    return surf


def _build_layer(layer_id: str, sim_time, size):
    """阶段2-5 在此按 layer_id 生成贴图。"""
    if layer_id == 'olr':
        # 参考图主图层: OLR 灰度(D01/D02 由 display 模块处理, 此处渲染整幅)
        from ..render.video_style import render_olr
        api = _video_api()
        if not _VIDEO_CTX.get('states'):
            return None
        dt = _video_datetime(sim_time)
        ty = _video_typhoon(dt)
        return render_olr(api, dt, [ty] if ty else None, *size)
    if layer_id.startswith('video_'):
        try:
            from ..render.video_style import (render_olr, render_refl, render_sfc,
                                              render_mid, render_high, render_station,
                                              render_center, render_track)
            api = _video_api()
            # 已知bug: 未模拟(无台风状态)时视频图层显示"数据未生成",
            # 不再用合成占位冒充模拟结果
            if not _VIDEO_CTX.get('states'):
                return None
            dt = _video_datetime(sim_time)
            ty = _video_typhoon(dt)
            if layer_id == 'video_olr':
                surf = render_olr(api, dt, [ty] if ty else None, *size)
            elif layer_id == 'video_refl':
                surf = render_refl(api, dt, [ty] if ty else None, *size)
            elif layer_id == 'video_sfc':
                surf = render_sfc(api, dt, [ty] if ty else None, *size)
            elif layer_id == 'video_mid':
                surf = render_mid(api, dt, [ty] if ty else None, *size)
            elif layer_id == 'video_high':
                surf = render_high(api, dt, [ty] if ty else None, *size)
            elif layer_id == 'video_station':
                station = _video_station(dt)
                surf = render_station(api, dt, station, *size)
            elif layer_id == 'video_center':
                surf = render_center(api, dt,
                                     {'states': _video_typhoon_states(dt)},
                                     None, size[0], size[1])
            else:
                surf = render_track(api, dt,
                                    {'states': _video_typhoon_states(dt)},
                                    None, size[0], size[1])
            return surf
        except Exception:
            return None
    if layer_id in ('cloud_ir', 'cloud_vis', 'cloud_globe'):
        return _build_cloud_layer(layer_id, sim_time, size)
    if layer_id in ('sst', 'ohc', 'shear', 'slp'):
        return _build_env_layer(layer_id, sim_time, size)
    if layer_id == 'itcz':
        return _build_itcz_layer(sim_time, size)
    if layer_id == 'typhoon':
        return _build_typhoon_layer(sim_time, size)
    return None


# ════════════════════ 基础图层(阶段2/5 场渲染)════════════════════

def _video_typhoons_full(dt=None) -> list:
    """所有系统在 dt 时刻最近状态(云图/台风图层用,含 nb/ew/结构参数)。"""
    st = _VIDEO_CTX['states']
    if not st:
        return []
    if dt is None:
        return [dict(s) for s in st]
    by_id: dict = {}
    for s in st:
        by_id.setdefault(s.get('id', 0), []).append(s)
    out = []
    for rows in by_id.values():
        best = min(rows, key=lambda s: abs((_parse_state_t(s) - dt).total_seconds()))
        out.append(dict(best))
    return out


# ── 地图底图(./map/map.png + land 掩码)──

_BASE_MAP: dict = {}
_BASE_MIP: dict = {}          # mip 中间层(缩放性能优化)
_LAND_MASK_CACHE: dict = {}   # 陆地掩码按尺寸缓存
_LAND_EDGE_CACHE: dict = {}   # 陆地轮廓掩码缓存


def _land_edge_px(w: int, h: int) -> np.ndarray:
    """陆地轮廓掩码(掩码边缘膨胀差, 规范样式黄线用)。"""
    key = (w, h)
    if key in _LAND_EDGE_CACHE:
        return _LAND_EDGE_CACHE[key]
    from scipy.ndimage import binary_dilation
    m = _land_mask_px(w, h)
    edge = binary_dilation(m, iterations=1) & ~m
    _LAND_EDGE_CACHE[key] = edge
    if len(_LAND_EDGE_CACHE) > 32:
        _LAND_EDGE_CACHE.pop(next(iter(_LAND_EDGE_CACHE)))
    return edge


def _base_map(w: int, h: int):
    """(w,h) 全图地图贴图(缓存)。返回 (Surface, RGB ndarray)。
    地图覆盖 -90..90°N, 视图为 -60..60°N, 需先裁出该纬带再缩放。
    性能: 从 2160×720 预缩放中间层再缩放(避免每次动 48MB 原图)。"""
    key = (w, h)
    if key in _BASE_MAP:
        return _BASE_MAP[key]
    import os
    p = os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'map', 'map.png'))
    if os.path.exists(p):
        mip = _BASE_MIP.get((2160, 720))
        if mip is None:
            img = pygame.image.load(p)
            ih = img.get_height()
            y0 = int((90.0 - 60.0) / 180.0 * ih)
            y1 = int((90.0 + 60.0) / 180.0 * ih)
            img = img.subsurface((0, y0, img.get_width(), y1 - y0))
            mip = pygame.transform.smoothscale(img, (2160, 720))
            _BASE_MIP[(2160, 720)] = mip
        surf = pygame.transform.smoothscale(mip, (w, h))
    else:
        surf = pygame.Surface((w, h))
        surf.fill((24, 38, 58))
    arr = pygame.surfarray.array3d(surf).transpose(1, 0, 2).copy()
    _BASE_MAP[key] = (surf, arr)
    if len(_BASE_MAP) > 64:
        _BASE_MAP.pop(next(iter(_BASE_MAP)))
    return _BASE_MAP[key]


def _land_mask_px(w: int, h: int) -> np.ndarray:
    key = (w, h)
    if key in _LAND_MASK_CACHE:
        return _LAND_MASK_CACHE[key]
    from ..render.video_style import land_mask_for
    m = land_mask_for(w, h, 0.0, 360.0, -60.0, 60.0)
    _LAND_MASK_CACHE[key] = m
    if len(_LAND_MASK_CACHE) > 64:
        _LAND_MASK_CACHE.pop(next(iter(_LAND_MASK_CACHE)))
    return m


def _draw_colorbar(surf: pygame.Surface, colors, lo: float, hi: float,
                   unit: str, w: int, h: int) -> None:
    """底部渐变图例条 + 数值/单位。"""
    spec = UI_SETTINGS.get('basemap', 'spec') == 'spec'
    bw = min(640, w // 4)
    bh = 18
    x0, y0 = 24, h - 58
    t = np.linspace(0.0, 1.0, bw)
    cm = np.array(colors, dtype=np.uint8)
    idx = t * (len(colors) - 1)
    i0 = np.clip(np.floor(idx).astype(int), 0, len(colors) - 2)
    f = (idx - i0)[..., None]
    grad = (cm[i0] * (1 - f) + cm[i0 + 1] * f).astype(np.uint8)
    grad = np.repeat(grad[None, :, :], bh, axis=0)
    bar = pygame.image.frombuffer(np.ascontiguousarray(grad).tobytes(), (bw, bh), 'RGB')
    box = (232, 236, 242) if spec else (40, 46, 62)
    txt = AXIS_TEXT_C if spec else T.TEXT
    dim = (120, 126, 136) if spec else T.TEXT_DIM
    pygame.draw.rect(surf, box, (x0 - 4, y0 - 4, bw + 8, bh + 8), border_radius=4)
    surf.blit(bar, (x0, y0))
    sz = max(14, min(24, w // 110))
    s1 = T.text(sz, f"{lo:.0f}", txt)
    s2 = T.text(sz, f"{hi:.0f}", txt)
    su = T.text(sz, unit, dim)
    surf.blit(s1, (x0, y0 + bh + 4))
    surf.blit(s2, (x0 + bw - s2.get_width(), y0 + bh + 4))
    surf.blit(su, (x0 + bw + 14, y0 + bh // 2 - su.get_height() // 2))


def _build_cloud_layer(layer_id: str, sim_time, size):
    """cloud_ir: 台风区域放大 IR-BD;cloud_vis: 全球可见光;
    cloud_globe: 全球合成(IR 亮度调制)。均叠加地图陆地。"""
    from scipy.ndimage import zoom
    from simulator.clouds.render import CloudRenderer
    api = _video_api()
    # 已知bug: 未进行模拟时(无视频上下文)不渲染合成云图, 显示"数据未生成"占位
    if api is None or not _VIDEO_CTX.get('states'):
        return None
    dt = _video_datetime(sim_time)
    w, h = size
    params = None
    if api is not None:
        params = {}
        for name in ('itcz', 'monsoon_trough', 'ridge'):
            params[name] = api.get_param(name, dt)
    res = 1 if max(size) > 800 else 2
    tys = _video_typhoons_full(dt)
    colormap = UI_SETTINGS.get('colormap') if layer_id != 'cloud_vis' else None
    cr = CloudRenderer(params=params, typhoons=tys, res=res, colormap=colormap)
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
    if layer_id == 'cloud_ir' and tys:
        # 台风区域放大: 细网格窗口(眼/眼壁可分辨),按纵横比留白居中
        ty = tys[0]
        hw_la, hw_lo = 14, 20
        cr_win = CloudRenderer(params=params, typhoons=[ty], res=0.1,
                               window=(ty['la'], ty['lo'], hw_la, hw_lo),
                               colormap=colormap)
        tb_w = cr_win.tb_field()
        alb_w = np.clip((25.0 - tb_w) / 75.0, 0.0, 1.0)
        rgb_w = cr_win.rgb_from_tb(tb_w)
        rgb_w = (rgb_w.astype(np.float32) * (0.5 + 0.5 * alb_w[..., None])).astype(np.uint8)
        alpha_w = np.clip((25.0 - tb_w) / 55.0, 0.0, 0.9) * 255.0
        aspect = (2 * hw_lo + 1) / (2 * hw_la + 1)
        ww = int(min(w, h * aspect))
        hh = int(ww / aspect)
        rgb_big = _scale_arr(rgb_w, ww, hh)
        alpha_big = _scale_l(alpha_w, ww, hh)
        # 透明底 RGBA(底图由 SimView 叠加)
        rgba = np.dstack([rgb_big, alpha_big.astype(np.uint8)])
        win = pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                      (ww, hh), 'RGBA')
        canvas = pygame.Surface((w, h), pygame.SRCALPHA)
        canvas.blit(win, ((w - ww) // 2, (h - hh) // 2))
        return canvas
    rgb_big = _scale_arr(rgb, w, h)
    alpha_big = _scale_l(alpha, w, h)
    rgba = np.dstack([rgb_big, alpha_big.astype(np.uint8)])
    surf = pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(), (w, h), 'RGBA')
    return surf


def _scale_arr(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    """RGB 数组 → pygame smoothscale(比 scipy zoom 快数十倍)。"""
    s = pygame.image.frombuffer(np.ascontiguousarray(arr, dtype=np.uint8).tobytes(),
                                (arr.shape[1], arr.shape[0]), 'RGB')
    return pygame.surfarray.array3d(pygame.transform.smoothscale(s, (w, h))) \
        .transpose(1, 0, 2).copy()


def _scale_l(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    """单通道数组 → pygame smoothscale('L' 不受支持, 用 3 通道灰度)。"""
    a = np.ascontiguousarray(arr, dtype=np.uint8)
    a3 = np.repeat(a[..., None], 3, axis=2)
    s = pygame.image.frombuffer(a3.tobytes(), (arr.shape[1], arr.shape[0]), 'RGB')
    return pygame.surfarray.array3d(pygame.transform.smoothscale(s, (w, h)))[..., 0] \
        .transpose(1, 0).copy().astype(np.uint8)


def _colorize(fld, lo: float, hi: float, colors, w: int, h: int) -> pygame.Surface:
    """(121, 360) 场 → (w, h) Surface: lo→hi 线性映射到 colors 渐变。"""
    a = np.nan_to_num(fld, nan=lo)
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    cm = np.array(colors, dtype=np.uint8)
    idx = t * (len(colors) - 1)
    i0 = np.clip(np.floor(idx).astype(int), 0, len(colors) - 2)
    f = (idx - i0)[..., None]
    rgb = (cm[i0] * (1 - f) + cm[i0 + 1] * f).astype(np.uint8)
    surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(), (360, 121), 'RGB')
    if (w, h) != (360, 121):
        surf = pygame.transform.smoothscale(surf, (w, h))
    return surf


def _build_env_layer(layer_id: str, sim_time, size):
    """sst / ohc / shear / slp: 场数据层(RGBA, 陆地透明, 底图/框架由 SimView 叠加)。"""
    api = _video_api()
    if api is None:
        return None
    dt = _video_datetime(sim_time)
    w, h = size
    if layer_id == 'sst':
        fld = api.get_field('sst', dt)
        if fld is None:
            return None
        colors = [(90, 190, 235), (120, 220, 120), (250, 200, 90), (240, 90, 90)]
        surf = _colorize(fld, 16.0, 32.0, colors, w, h)
    elif layer_id == 'ohc':
        fld = api.get_field('ohc', dt)
        if fld is None:
            return None
        colors = [(30, 30, 110), (40, 110, 220), (90, 220, 150),
                  (250, 220, 90), (210, 70, 40)]
        surf = _colorize(fld, 40.0, 170.0, colors, w, h)
    elif layer_id == 'shear':
        fld = api.get_field('shear', dt)
        if fld is None:
            return None
        colors = [(70, 200, 90), (230, 230, 90), (240, 130, 60), (210, 50, 50)]
        surf = _colorize(fld * 1.944, 0.0, 30.0, colors, w, h)
    else:   # slp: 海平面气压 + 副高线
        fld = api.get_field('mslp', dt)
        if fld is None:
            return None
        colors = [(220, 60, 60), (240, 210, 90), (130, 220, 130),
                  (90, 160, 220), (60, 60, 180)]
        surf = _colorize(fld, 995.0, 1030.0, colors, w, h)
        # 等压线(跨层检测 → 连续细线)
        a = np.nan_to_num(fld, nan=1013.0)
        for lev in (1004.0, 1008.0, 1012.0, 1016.0, 1020.0):
            sign = np.sign(a - lev)
            cross = np.abs(np.diff(sign, axis=1)) > 1.0
            ys, xs = np.where(cross)
            if not len(ys):
                continue
            px = ((xs + 0.5) / 360.0 * w).astype(int)
            py = (ys / 120.0 * h).astype(int)
            order = np.lexsort((px, py))
            rows = np.split(order, np.flatnonzero(np.diff(ys[order])))
            for seg in rows:
                if len(seg) < 2:
                    continue
                pts = [(int(px[i]), int(py[i])) for i in seg]
                pygame.draw.lines(surf, (20, 22, 30), False, pts, 1)
        # 副高脊线(15-45N 纬向最大海压,5 点滑动平均平滑)
        band = fld[15:46, :] if fld.shape[0] >= 46 else fld
        lats = []
        for j in range(band.shape[1]):
            col = band[:, j]
            lats.append(60.0 - (int(np.nanargmax(np.nan_to_num(col, nan=0.0))) + 15))
        lats = np.array(lats)
        sm = np.convolve(lats, np.ones(5) / 5.0, mode='same')
        pts = [(int(j / 360.0 * w), int((60.0 - la) / 120.0 * h))
               for j, la in enumerate(sm)]
        pygame.draw.lines(surf, (250, 220, 60), False, pts, 3)
    # 陆地透明(底图显示 map.png)
    m = _land_mask_px(w, h)
    if m.any():
        arr = pygame.surfarray.array3d(surf).transpose(1, 0, 2).copy()
        alpha = np.where(m[..., None], 0, 255).astype(np.uint8)
        rgba = np.dstack([arr, alpha])
        surf = pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                       (w, h), 'RGBA')
    else:
        rgba = np.dstack([pygame.surfarray.array3d(surf).transpose(1, 0, 2),
                          np.full((h, w, 1), 255, np.uint8)])
        surf = pygame.image.frombuffer(np.ascontiguousarray(rgba).tobytes(),
                                       (w, h), 'RGBA')
    return surf


def _build_itcz_layer(sim_time, size):
    """ITCZ/季风槽: 参数化带 + 副高脊(透明底, 底图由 SimView 叠加)。"""
    api = _video_api()
    if api is None:
        return None
    dt = _video_datetime(sim_time)
    w, h = size
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    import numpy as np
    nx = 72
    lon = np.linspace(0.0, 360.0, nx)
    xs = lon / 360.0 * w

    def curve(base, amp, k, ph):
        return base + amp * np.sin(2 * np.pi * k * lon / 360.0 + ph)

    def y_of(la):
        return int((60.0 - la) / 120.0 * h)

    itcz = api.get_param('itcz', dt)
    if itcz is not None:
        la = itcz.get('lat', 0.0)
        hw = max(3.0, itcz.get('lon_range', 6.0))
        cy = curve(la, 2.5, 2, 0.6)
        top = curve(la + hw, 2.0, 2, 0.2)
        bot = curve(la - hw, 3.0, 2, 1.0)
        band = pygame.Surface((w, h), pygame.SRCALPHA)
        poly = [(x, y_of(t)) for x, t in zip(xs, top)] + \
               [(x, y_of(b)) for x, b in zip(xs[::-1], bot[::-1])]
        pygame.draw.polygon(band, (120, 200, 230, 55), poly)
        surf.blit(band, (0, 0))
        pygame.draw.lines(surf, (120, 220, 255), False,
                          [(x, y_of(c)) for x, c in zip(xs, cy)], 3)
    mt = api.get_param('monsoon_trough', dt)
    if mt is not None:
        cy = curve(mt.get('lat', 10.0), 2.5, 3, 2.0)
        pygame.draw.lines(surf, (150, 230, 140), False,
                          [(x, y_of(c)) for x, c in zip(xs, cy)], 3)
    ridge = api.get_param('ridge', dt)
    if ridge is not None:
        cy = curve(ridge.get('lat', 30.0), 2.0, 2, 4.0)
        pygame.draw.lines(surf, (250, 220, 60), False,
                          [(x, y_of(c)) for x, c in zip(xs, cy)], 3)
    return surf


def _build_typhoon_layer(sim_time, size):
    """台风符号+路径(叠加): 全部系统历史路径 + 当前风圈(透明底)。"""
    st = _VIDEO_CTX['states']
    if not st:
        return None
    dt = _video_datetime(sim_time)
    w, h = size
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    by_id: dict = {}
    for s in st:
        by_id.setdefault(s.get('id', 0), []).append(s)
    colors = [(235, 235, 235), (120, 200, 230), (250, 220, 60),
              (240, 130, 60), (230, 70, 70)]
    for ci, (tid, rows) in enumerate(sorted(by_id.items())):
        color = colors[ci % len(colors)]
        rows = [r for r in rows if _parse_state_t(r) <= dt]
        if not rows:
            continue
        path = [latlon_to_xy(r['la'], r['lo'], w, h) for r in rows]
        if len(path) > 1:
            pygame.draw.lines(surf, color, False, path, 3)
        cx, cy = path[-1]
        pygame.draw.circle(surf, color, (cx, cy), 9)
        pygame.draw.circle(surf, T.TEXT, (cx, cy), 9, 2)
        r34 = int(rows[-1].get('r34', 0) * 1.852)
        pygame.draw.circle(surf, (150, 170, 200), (cx, cy),
                           max(10, int(r34 / 111.0 / 120.0 * h)), 2)
    return surf


# F10: 视频画面数据桥接(从运行环境取场 API 与台风状态)
_VIDEO_CTX = {'api': None, 't0': None, 'states': [], 'station': None, 'track': None}


def set_video_context(api, states: list, t0=None) -> None:
    _VIDEO_CTX['api'] = api
    _VIDEO_CTX['states'] = states
    _VIDEO_CTX['t0'] = t0
    _VIDEO_CTX['station'] = None
    _VIDEO_CTX['track'] = None
    # 数据更新后旧时间戳图层缓存失效,避免重新生成后仍显示上一轮数据(F4-15)
    _layer_cache.clear()


def set_video_station(latlon) -> None:
    """单站画面: 用户点选自定义站点(纬度, 经度); None 表示恢复台风中心。"""
    _VIDEO_CTX['station'] = tuple(latlon) if latlon else None


def set_video_track(tid) -> None:
    """设定被追踪台风 id(单站/中心/路径画面跟随该台风)。"""
    _VIDEO_CTX['track'] = tid
    _layer_cache.clear()


def _video_api():
    return _VIDEO_CTX['api']


def _video_datetime(sim_time):
    from datetime import datetime
    y, mo, d, h = sim_time
    return datetime(y, mo, d, h)


def _video_typhoon(dt=None):
    """取与请求时间最接近的状态(K24: 不再固定终末 states[-1])。"""
    st = _VIDEO_CTX['states']
    if not st:
        return None
    if dt is None:
        s = st[-1]
        return {'la': s['la'], 'lo': s['lo'], 'w': s['w']}
    best = st[0]
    best_d = abs((_parse_state_t(best) - dt).total_seconds())
    for s in st[1:]:
        d = abs((_parse_state_t(s) - dt).total_seconds())
        if d < best_d:
            best, best_d = s, d
    return {'la': best['la'], 'lo': best['lo'], 'w': best['w']}


def _video_typhoon_states(dt=None) -> list:
    """dt 时刻最接近的单台风完整状态序列(按时间升序)。
    F11: 中心强度/路径/单站画面只画一个台风,不把全部系统状态混成一条线。
    追踪台风 id(_VIDEO_CTX['track'])优先, 否则取最近台风。"""
    st = _VIDEO_CTX['states']
    if not st:
        return []
    tid = _VIDEO_CTX.get('track')
    if dt is not None:
        best = min(st, key=lambda s: abs((_parse_state_t(s) - dt).total_seconds()))
        if tid is None or not any(s.get('id') == tid for s in st):
            tid = best.get('id')
    else:
        if tid is None:
            tid = st[-1].get('id')
    rows = [s for s in st if s.get('id') == tid]
    rows.sort(key=lambda s: s.get('t', ''))
    return rows


def _parse_state_t(s) -> datetime:
    try:
        return datetime.strptime(str(s['t']), '%Y%m%d%H')
    except Exception:
        return datetime(2000, 1, 1)


def _video_states():
    return _VIDEO_CTX['states']


def _video_station(dt=None) -> dict:
    """单站画面数据: 用户点选站点时合成该点序列,否则默认台风中心(单台风)。
    默认台风中心直接把状态序列转成 m/s(与单站画面 10m 风单位一致)。"""
    api = _video_api()
    st = _VIDEO_CTX.get('station')
    if st is not None:
        from ..render.video_style import station_series_at
        la, lo = st
        pts = station_series_at(_video_states(), api, la, lo)
        ns = 'N' if la >= 0 else 'S'
        we = 'E' if lo <= 180 else 'W'
        lo_disp = lo if lo <= 180 else 360 - lo
        return {'name': f"{abs(la):.1f}°{ns} {lo_disp:.1f}°{we}",
                'la': la, 'lo': lo, 'pts': pts}
    pts = []
    for s in _video_typhoon_states(dt):
        p = dict(s)
        p['w'] = float(s.get('w', 0)) * 0.514444    # kt → m/s
        pts.append(p)
    return {'name': 'Typhoon Center', 'pts': pts}


# ════════════════════ 等距圆柱投影 ════════════════════

def latlon_to_xy(lat: float, lon: float, w: int, h: int):
    """0-360°E / -90-90°N → 像素。"""
    x = (lon % 360) / 360.0 * w
    y = (90 - lat) / 180.0 * h
    return int(x), int(y)


class GeoView:
    """主程序 MapView 移植: 等距圆柱投影 + cover 铺满 + X 回绕 + Y 钳制。
    源纹理: (0-360°E, -90..90°N) 像素图, 视口任意尺寸。"""

    def __init__(self, rect: pygame.Rect, img_w: int, img_h: int):
        self.rect = rect
        self.img_w, self.img_h = img_w, img_h
        self.view_x = self.view_y = 0.0
        self.scale = 1.0
        # cover 模式: 最小缩放使地图铺满视口(上下/左右不露边)
        self.min_scale = max(rect.w / img_w, rect.h / img_h)
        self.max_scale = 8.0
        self._offset = (0, 0)
        self._offset_key = None

    @property
    def _max_view_y(self) -> float:
        return max(0.0, self.img_h - self.rect.h / self.scale)

    def _clamp_view_y(self):
        self.view_y = max(0.0, min(self.view_y, self._max_view_y))

    def _src_y(self, view_h: float) -> float:
        return max(0.0, min(self.view_y, self.img_h - view_h))

    def _draw_offset(self) -> tuple:
        key = (round(self.scale, 3), self.rect.w, self.rect.h)
        if key != self._offset_key:
            vw = min(self.rect.w / self.scale, self.img_w)
            vh = min(self.rect.h / self.scale, self.img_h)
            self._offset = (int((self.rect.w - vw * self.scale) / 2),
                            int((self.rect.h - vh * self.scale) / 2))
            self._offset_key = key
        return self._offset

    def geo_to_screen(self, lon: float, lat: float):
        """经纬(0-360, -90..90) → 视口像素。"""
        px = lon / 360.0 * self.img_w
        py = (90.0 - lat) / 180.0 * self.img_h
        ox, oy = self._draw_offset()
        ddx = px - self.view_x
        if ddx > self.rect.w / (2.0 * self.scale) + self.img_w / 2.0:
            ddx -= self.img_w
        elif ddx < self.rect.w / (2.0 * self.scale) - self.img_w / 2.0:
            ddx += self.img_w
        return int(ddx * self.scale) + ox, int((py - self.view_y) * self.scale) + oy

    def screen_to_geo(self, sx: int, sy: int):
        ox, oy = self._draw_offset()
        px = self.view_x + (sx - ox) / self.scale
        py = self.view_y + (sy - oy) / self.scale
        lon = (px / self.img_w * 360.0) % 360.0
        lat = max(-90.0, min(90.0, 90.0 - py / self.img_h * 180.0))
        return lon, lat

    def move_view(self, dx: int, dy: int):
        self.view_x -= dx / self.scale
        self.view_y -= dy / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()

    def zoom_at(self, factor: float, mx: int, my: int):
        """以鼠标为中心缩放(主程序行为)。"""
        ox, oy = self._draw_offset()
        mx, my = mx - ox, my - oy
        old = self.scale
        self.scale = max(self.min_scale, min(self.scale * factor, self.max_scale))
        self.view_x = (self.view_x + mx / old) - mx / self.scale
        self.view_y = (self.view_y + my / old) - my / self.scale
        self.view_x %= self.img_w
        self._clamp_view_y()

    def reset_view(self):
        self.view_x = self.view_y = 0.0
        self.scale = self.min_scale
        self._clamp_view_y()

    def blit_src(self, screen: pygame.Surface, src: pygame.Surface) -> None:
        """按视图窗口把源纹理(0-360/-90..90)渲染到视口(经度回绕分段)。"""
        r = self.rect
        vw = min(r.w / self.scale, self.img_w)
        vh = min(r.h / self.scale, self.img_h)
        sx = self.view_x % self.img_w
        sy = self._src_y(vh)
        ox, oy = self._draw_offset()
        sw, sh = src.get_size()
        f_x = sw / self.img_w
        f_y = sh / self.img_h
        x_off = 0
        segs = [(sx, min(self.img_w - sx, vw))] if self.img_w - sx >= vw \
            else [(sx, self.img_w - sx), (0, vw - (self.img_w - sx))]
        for seg_x, seg_w in segs:
            if seg_w <= 0:
                continue
            rect = pygame.Rect(int(seg_x * f_x), int(sy * f_y),
                               max(1, int(math.ceil(seg_w * f_x))),
                               max(1, int(math.ceil(vh * f_y))))
            rect = rect.clip(src.get_rect())
            if rect.width <= 0 or rect.height <= 0:
                continue
            try:
                scaled = pygame.transform.scale(
                    src.subsurface(rect),
                    (max(1, int(math.ceil(seg_w * self.scale))),
                     max(1, int(math.ceil(vh * self.scale)))))
                screen.blit(scaled, (r.left + ox + x_off, r.top + oy))
                x_off += scaled.get_width()
            except ValueError:
                pass


class SimView:
    """中央主视图(照搬主程序 MapView): map.png 底图投影 +
    数据图层叠加 + 参考图界面框架(白底/标题/虚线网格/坐标标注/色标)。"""

    LAYER_TITLES = {
        'cloud_ir': '红外增强(IR)', 'cloud_vis': '可见光(VIS)',
        'cloud_globe': '全球云图(IR)', 'sst': '全球 SST',
        'ohc': '海洋热含量 OHC (0-700m)', 'shear': '垂直风切变 (200-850 hPa)',
        'slp': '海平面气压 + 副高脊线(黄)', 'itcz': 'ITCZ / 季风槽 / 副高脊',
        'typhoon': '台风路径',
    }
    # 色标: layer_id → (colors, lo, hi, unit)
    COLORBARS = {
        'sst': ([(90, 190, 235), (120, 220, 120), (250, 200, 90), (240, 90, 90)],
                16, 32, '°C'),
        'ohc': ([(30, 30, 110), (40, 110, 220), (90, 220, 150),
                 (250, 220, 90), (210, 70, 40)], 40, 170, 'kJ/cm²'),
        'shear': ([(70, 200, 90), (230, 230, 90), (240, 130, 60), (210, 50, 50)],
                  0, 30, 'kt'),
        'slp': ([(220, 60, 60), (240, 210, 90), (130, 220, 130),
                 (90, 160, 220), (60, 60, 180)], 995, 1030, 'hPa'),
    }

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.view = GeoView(rect, 10800, 5400)      # map.png 尺寸
        self.panning = False
        self.pan_last = (0, 0)
        self.selected = None

    def reset(self):
        self.view.reset_view()

    def handle_event(self, e, w, h):
        if e.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.view.zoom_at(1.1 ** e.y, *pygame.mouse.get_pos())
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 3 and self.rect.collidepoint(e.pos):
            self.panning = True
            self.pan_last = e.pos
            return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            self.panning = False
        if e.type == pygame.MOUSEMOTION and self.panning:
            dx = e.pos[0] - self.pan_last[0]
            dy = e.pos[1] - self.pan_last[1]
            self.pan_last = e.pos
            self.view.move_view(dx, dy)
            return True
        return False

    def draw(self, surface, layer_id, sim_time, typhoons, station_point=None,
             opacity: int = 100):
        r = self.rect
        # 白底(参考图界面框架)
        surface.fill((245, 247, 250), r)
        # 底图: map.png(0-360/-90..90, 与主程序一致)
        mip = _BASE_MIP.get((2160, 1080))
        if mip is None:
            import os as _os
            p = _os.path.normpath(_os.path.join(SCRIPT_DIR, '..', 'map', 'map.png'))
            if _os.path.exists(p):
                img = pygame.image.load(p)
                mip = pygame.transform.smoothscale(img, (2160, 1080))
                _BASE_MIP[(2160, 1080)] = mip
            else:
                mip = pygame.Surface((2160, 1080))
                mip.fill((200, 210, 225))
        self.view.blit_src(surface, mip)
        # 数据图层(仅覆盖 60S-60N; RGBA 叠加)
        if not layer_id.startswith('video_'):
            base_w, base_h = 2160, 720          # 60S-60N 全图基准
            src = render_layer(layer_id, sim_time, (base_w, base_h))
            if src is not None:
                if opacity < 100:
                    src = src.copy()
                    src.set_alpha(max(0, min(255, int(opacity * 2.55))))
                self._blit_data(surface, src, base_w, base_h)
            else:
                ts = T.text(20, PLACEHOLDER, T.TEXT_DIM)
                surface.blit(ts, (r.centerx - ts.get_width() // 2,
                                  r.centery - ts.get_height() // 2))
        else:
            src = render_layer(layer_id, sim_time, (r.w, r.h))
            if src is not None:
                surface.blit(src, r.topleft)
        # 界面框架: 虚线网格 + 坐标标注(参考图样式)
        if UI_SETTINGS.get('show_grid', True):
            _draw_proj_grid(surface, r, self.view)
        # 标题 + 时间戳(参考图: 左上角)
        title = self.LAYER_TITLES.get(layer_id, layer_id)
        dt = _video_datetime(sim_time)
        ts = T.text(16, f'{title}  {dt.strftime("%Y-%m-%d %HZ")}', AXIS_TEXT_C)
        pygame.draw.rect(surface, (232, 236, 242),
                         (r.x + 8, r.y + 8, ts.get_width() + 18, ts.get_height() + 10),
                         border_radius=8)
        surface.blit(ts, (r.x + 17, r.y + 13))
        # 色标(底部)
        cb = self.COLORBARS.get(layer_id)
        if cb:
            _draw_colorbar(surface, cb[0], cb[1], cb[2], cb[3], r.w, r.h)
        # 台风叠加
        for ty in typhoons:
            sx, sy = self.view.geo_to_screen(ty.get('lon', 0) % 360, ty.get('lat', 0))
            sx += r.x
            sy += r.y
            if not r.collidepoint(sx, sy):
                continue
            color = T.LAYER_COLORS[min(4, max(0, ty.get('cat', 0)))]
            pygame.draw.circle(surface, color, (sx, sy), 6)
            if ty.get('id') == self.selected:
                pygame.draw.circle(surface, T.TEXT, (sx, sy), 10, 2)
        # 单站选点标记
        if station_point is not None and r.collidepoint(station_point):
            px, py = station_point
            pygame.draw.line(surface, T.TEXT, (px - 8, py), (px + 8, py), 2)
            pygame.draw.line(surface, T.TEXT, (px, py - 8), (px, py + 8), 2)
            pygame.draw.circle(surface, T.TEXT, (px, py), 10, 1)
        pygame.draw.rect(surface, T.BORDER, r, 1)

    def _blit_data(self, surface, src, base_w, base_h):
        """数据层(0-360, 60S-60N)按同一投影窗口化叠加到视口。
        源纹理坐标系: (0-360, 60N..60S 从上到下), 映射到 GeoView 的
        (0-360, -60..60), 即 img 坐标 y0 = img_h*(90-60)/180。"""
        gv = self.view
        r = self.rect
        vw = min(r.w / gv.scale, gv.img_w)
        vh = min(r.h / gv.scale, gv.img_h)
        sx = gv.view_x % gv.img_w
        sy = gv._src_y(vh)
        ox, oy = gv._draw_offset()
        # 源纹理经度/纬度覆盖: 0-360, 60N..60S(img_y: img_h/6 .. img_h*5/6)
        src_la0 = int(gv.img_h / 6.0 * base_h / gv.img_h) if False else 0
        # 源纹理对应 img 空间: 高 = img_h*4/6(120°), 顶 = img_h/6
        top_img = gv.img_h / 6.0
        f_x = base_w / gv.img_w
        f_y = base_h / (gv.img_h * 4.0 / 6.0)
        y_off_src = int((sy - top_img) * f_y)
        if y_off_src < 0:
            y_off_src = 0
        vh_src = min(base_h - y_off_src, int(vh * f_y))
        if vh_src <= 0:
            return
        x_off = 0
        segs = [(sx, min(gv.img_w - sx, vw))] if gv.img_w - sx >= vw \
            else [(sx, gv.img_w - sx), (0, vw - (gv.img_w - sx))]
        for seg_x, seg_w in segs:
            if seg_w <= 0:
                continue
            rect = pygame.Rect(int(seg_x * f_x), y_off_src,
                               max(1, int(math.ceil(seg_w * f_x))), vh_src)
            rect = rect.clip(src.get_rect())
            if rect.width <= 0 or rect.height <= 0:
                continue
            try:
                scaled = pygame.transform.scale(
                    src.subsurface(rect),
                    (max(1, int(math.ceil(seg_w * gv.scale))),
                     max(1, int(math.ceil(vh * gv.scale)))))
                surface.blit(scaled, (r.left + ox + x_off, r.top + oy))
                x_off += scaled.get_width()
            except ValueError:
                pass


def _draw_proj_grid(surface: pygame.Surface, r: pygame.Rect, gv: GeoView) -> None:
    """投影虚线经纬网格 + 坐标标注(参考图界面框架)。"""
    for lon in range(0, 360, 30):
        x, _ = gv.geo_to_screen(lon, 0)
        x += r.x
        if not (r.x <= x <= r.right):
            continue
        y = r.y
        while y < r.bottom:
            pygame.draw.line(surface, GRID_DASH_C, (x, y), (x, min(y + 8, r.bottom)), 1)
            y += 14
        tag = '0°' if lon == 0 else (f'{lon}°E' if lon <= 180 else f'{360 - lon}°W')
        ts = T.font(12).render(tag, True, AXIS_TEXT_C)
        surface.blit(ts, (x - ts.get_width() // 2, r.bottom - ts.get_height() - 2))
    for lat in range(-60, 61, 20):
        _, y = gv.geo_to_screen(0, lat)
        y += r.y
        if not (r.y <= y <= r.bottom):
            continue
        x = r.x
        while x < r.right:
            pygame.draw.line(surface, GRID_DASH_C, (x, y), (min(x + 8, r.right), y), 1)
            x += 14
        tag = f'{abs(lat)}°N' if lat > 0 else (f'{abs(lat)}°S' if lat < 0 else '0°')
        ts = T.font(12).render(tag, True, AXIS_TEXT_C)
        surface.blit(ts, (r.x + 2, y - ts.get_height() // 2))


# ════════════════════ 模态曲线数据(接续段/架空段分线型)════════════════════

def load_mode_curve(series: dict, mode: str):
    """→ (real[(x,v)...], cont[(x,v)...], syn[(x,v)...]) x 为月序号。"""
    real = []
    cont = []
    syn = []
    for i, (y, mo, v, seg) in enumerate(series[mode]):
        x = y * 12 + mo
        if seg == 'real':
            real.append((x, v))
        elif seg == 'continuation':
            cont.append((x, v))
        else:
            syn.append((x, v))
    return real, cont, syn


# ════════════════════ 浮层面板 ════════════════════

class FloatPanel:
    """轻量浮层面板(可拖拽、可关闭)。"""

    def __init__(self, title: str, rect: pygame.Rect):
        self.title = title
        self.rect = rect
        self.dragging = False
        self.offset = (0, 0)

    def handle_event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.rect.collidepoint(e.pos):
                self.dragging = True
                self.offset = (e.pos[0] - self.rect.x, e.pos[1] - self.rect.y)
                return True
        if e.type == pygame.MOUSEMOTION and self.dragging:
            self.rect.x = e.pos[0] - self.offset[0]
            self.rect.y = e.pos[1] - self.offset[1]
            return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.dragging = False
        return False

    def draw(self, surface, dark=True):
        T.draw_panel(surface, self.rect)
        ts = T.text(18, self.title, T.ACCENT)
        surface.blit(ts, (self.rect.x + 10, self.rect.y + 6))


class ModePanel(FloatPanel):
    """模态曲线面板: 历史实况 + 接续段 + 架空段(分线型)。"""

    def __init__(self, series, mode_names=None):
        super().__init__('模态曲线', pygame.Rect(60, 60, 720, 430))
        self.series = series
        self.names = mode_names

    def draw(self, surface, **kw):
        super().draw(surface)
        r = self.rect
        chart = pygame.Rect(r.x + 12, r.y + 34, r.w - 24, r.h - 46)
        pygame.draw.rect(surface, T.BG, chart)
        if not self.series:
            return
        # 先收集全部点;若所有模式 rows 均为空,只画标题与空图表,不崩溃
        pts = [p for m in self.series for p in self.series[m]]
        if not pts:
            return
        y_min = min(v for _, _, v, _ in pts)
        y_max = max(v for _, _, v, _ in pts)
        span = (y_max - y_min) or 1.0
        x_min = min(y * 12 + mo for y, mo, _, _ in pts)
        x_max = max(y * 12 + mo for y, mo, _, _ in pts)
        x_span = (x_max - x_min) or 1.0
        # 分模态绘制(历史实况 + 接续 + 架空: 实线/长虚/短虚)
        for idx, (mode, rows) in enumerate(self.series.items()):
            color = T.LAYER_COLORS[idx % len(T.LAYER_COLORS)]
            for seg, dash in (('real', None), ('continuation', (8, 4)), ('synthetic', (3, 3))):
                pts = [(y * 12 + mo, v) for y, mo, v, s in rows if s == seg]
                if len(pts) < 2:
                    continue
                px = [chart.x + (x - x_min) / x_span * chart.w for x, _ in pts]
                py = [chart.y + chart.h - (v - y_min) / span * chart.h for _, v in pts]
                _draw_polyline_dash(surface, list(zip(px, py)), color, dash)
        # 名称图例(文字底缘不越出面板)
        lx = r.x + 14
        for idx, mode in enumerate(self.series):
            c = T.LAYER_COLORS[idx % len(T.LAYER_COLORS)]
            pygame.draw.line(surface, c, (lx, r.bottom - 8), (lx + 22, r.bottom - 8), 2)
            nm = self.names.get(mode, mode) if self.names else mode
            ts = T.text(14, nm, T.TEXT_DIM)
            surface.blit(ts, (lx + 26, r.bottom - 17))
            lx += 26 + ts.get_width() + 14


def _draw_polyline_dash(surface, pts, color, dash):
    if dash is None:
        if len(pts) > 1:
            pygame.draw.lines(surface, color, False, pts, 2)
        return
    on, off = dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length <= 0:
            continue
        n = int(length / (on + off)) + 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for i in range(n):
            s = i * (on + off)
            e = min(s + on, length)
            pygame.draw.line(surface, color,
                             (x0 + ux * s, y0 + uy * s),
                             (x0 + ux * e, y0 + uy * e), 2)
