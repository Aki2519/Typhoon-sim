# tools/probe_layer_render.py
"""模拟模式(MODE_SIM)叠加层**像素级**渲染审查探针(只读审查, 不改 app/ 与内核)。

用法(WSL, 串行, 单进程):
  PYTHONIOENCODING=utf-8 timeout 900 \
    "/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe" \
    tools/probe_layer_render.py --stage main

stage:
  smoke : 只验证"能起 TySim + 能推进 + 能渲染"
  main  : 逐图层像素对比 + 对抗性验证 + 出图
  flow  : 单独复现 flow/current 共用 _flow_last 限流缓存造成的串层
  view  : 只打印视图/投影数字

硬约束: 不调 pygame.quit(); 单进程; 脚本自己结束。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.chdir(REPO)

import numpy as np  # noqa: E402
import pygame  # noqa: E402

OUT_DIR = os.path.join(REPO, 'output', 'sim', '_reference', 'layer_check')

_LOG_RECORDS = []


class _Capture(logging.Handler):
    def emit(self, record):
        try:
            _LOG_RECORDS.append({
                'logger': record.name, 'level': record.levelname,
                'msg': record.getMessage(),
                'exc': (repr(record.exc_info[1]) if record.exc_info else None)})
        except Exception:
            pass


def install_log_capture():
    for name in ('app.ty_sim_mixins.sim_mode_mixin', 'simulator.simcore.render',
                 'simulator.simcore.core', 'app.renderer'):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.disabled = False
        lg.propagate = False
        lg.addHandler(_Capture())


def log_reset():
    _LOG_RECORDS.clear()


def log_errs():
    return [r for r in _LOG_RECORDS if r['exc']]


def log_dump(path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(_LOG_RECORDS, f, ensure_ascii=False, indent=1)


# ─────────────────────────── app 装机 ───────────────────────────
def build_app(w=1280, h=720, global_view=True):
    pygame.init()
    screen = pygame.display.set_mode((w, h))
    from app.ty_sim import TySim
    t0 = time.time()
    app = TySim(screen)
    t_build = time.time() - t0
    app.md = app.MODE_SIM
    app._apply_panel_height_for_mode()
    if global_view:
        set_global_view(app)
    return app, screen, t_build


def set_global_view(app, lon=(0.0, 360.0), lat=(-60.0, 60.0)):
    """把视图设成"整个 0-360 / ±60 计算域", 让所有图层都有可见内容。"""
    mv = app.map_mgr.map_view
    mv.set_view_region(float(lon[0]), float(lon[1]), float(lat[0]), float(lat[1]))
    app.map_mgr._cached_map_render = None
    return {'center': [mv.lon_min, mv.lon_max, mv.lat_min, mv.lat_max],
            'scale': round(float(mv.scale), 5)}


def advance(app, target_h, cap_s=90.0):
    from simulator.simcore import core as V
    step_h = app._sim_step_h()
    t0 = time.time()
    n = 0
    while float(app.sim_v4['t']) < float(target_h) - 1e-6:
        V.sim_step(app.sim_v4, step_h, max_inner=1)
        n += 1
        if time.time() - t0 > cap_s:
            break
    return {'steps': n, 't_h': round(float(app.sim_v4['t']), 3),
            'wall_s': round(time.time() - t0, 2), 'step_h': step_h,
            'stepCostMs': round(float(app.sim_v4.get('stepCostMs') or 0.0), 1)}


def render_frame(app, screen, layers, reset_path_cache=True):
    """走真实渲染入口 Renderer.draw(底图+面板+图层), 返回 (H, W, 3) uint8。

    reset_path_cache=True 时清掉 sim_path_tys(路径对象增量缓存): 图标/名字/
    信息框那一段**不受 sim_layers 门控**(sim_mode_mixin.py:1466-1495), 上一帧
    留下的 Typhoon 对象会让它在关掉 track 时照样被画出来。探针默认清掉以保证
    逐帧隔离; 该泄漏本身在 adv_stale_path 里单独验证。
    """
    if reset_path_cache:
        app.sim_path_tys = {}
    app.sim_layers = set(layers)
    app._sim_msg = ''
    app._toasts = []
    screen.fill((0, 0, 0))
    app.renderer.draw(screen)
    a = pygame.surfarray.array3d(screen)
    return np.transpose(a, (1, 0, 2)).copy()


def absdiff(a, b):
    return np.abs(a.astype(np.int16) - b.astype(np.int16))


def mask_of(a, b, thr=8):
    return absdiff(a, b).max(axis=2) > thr


def analyze(base, frame, map_h, noise_mask=None, thr=8):
    d = absdiff(frame, base)
    m = d.max(axis=2) > thr
    pix = int(m.sum())
    tot = int(m.size)
    mm = m[:map_h]
    mm_n = int(mm.sum())
    res = {
        'pix': pix, 'pct_frame': round(100.0 * pix / max(1, tot), 4),
        'pix_map': mm_n, 'pct_map': round(100.0 * mm_n / max(1, mm.size), 4),
        'pix_panel': pix - mm_n,
        'mean_abs_frame': round(float(d.mean()), 4),
        'mean_abs_on_diff': round(float(d[m].mean()), 2) if pix else 0.0,
        'max_abs': int(d.max()), 'bbox': None, 'diff_mean_color': None,
        'uniq_colors': 0, 'noise_overlap_pct': 0.0, 'net_pix': pix,
    }
    if pix:
        ys, xs = np.nonzero(m)
        res['bbox'] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        px = frame[m].astype(np.float64)
        res['diff_mean_color'] = [round(float(v), 1) for v in px.mean(axis=0)]
        res['uniq_colors'] = int(np.unique(px.astype(np.uint8), axis=0).shape[0])
        if noise_mask is not None:
            res['noise_overlap_pct'] = round(
                100.0 * float((m & noise_mask).sum()) / max(1, pix), 2)
            res['net_pix'] = int((m & ~noise_mask).sum())
    return res, m


def verdict(res):
    if res['net_pix'] <= 0:
        return 'NO-CHANGE'
    if res['pct_map'] < 0.005:
        return 'TINY(<0.005%% map)'
    if res['pct_map'] < 0.1:
        return 'FAINT(0.005-0.1%%)'
    return 'VISIBLE'


def flatness(res):
    if not res['pix'] or not res['diff_mean_color']:
        return 'n/a'
    c = res['diff_mean_color']
    tags = []
    if all(v < 6 for v in c):
        tags.append('near-black')
    if all(v > 249 for v in c):
        tags.append('near-white')
    if res['uniq_colors'] <= 3:
        tags.append('flat-single-color')
    return ','.join(tags) if tags else 'ok'


# ─────────────────────────── 出图 ───────────────────────────
def _save_panels(path, panels, highlight=None):
    h, w = panels[0].shape[:2]
    n = len(panels)
    canvas = np.zeros((h, w * n, 3), dtype=np.uint8)
    for i, p in enumerate(panels):
        img = p.copy()
        if highlight is not None and i == n - 1:
            img[highlight] = (255, 40, 40)
        canvas[:, i * w:(i + 1) * w] = img
    surf = pygame.Surface((w * n, h))
    pygame.surfarray.blit_array(surf, np.transpose(canvas, (1, 0, 2)))
    pygame.image.save(surf, path)


def tc_summary(app):
    out = []
    for tc in (app.sim_v4 or {}).get('tcs', []):
        out.append({'id': tc['id'], 'name': tc['name'], 'named': bool(tc.get('named')),
                    'cy': tc.get('cy'), 'basin': tc.get('basin'),
                    'lat': round(float(tc['lat']), 2), 'lon': round(float(tc['lon']), 2),
                    'vmax': round(float(tc['vmax']), 1), 'dead': bool(tc.get('dead')),
                    'track_n': len(tc.get('track') or [])})
    return out


# ─────────────────────────── stages ───────────────────────────
def _setup_sim(app, target_h, cap):
    app._sim_reset(msg=False)
    for la, lo, v in ((16.0, 138.0, 65.0), (14.0, 265.0, 55.0), (-14.0, 75.0, 50.0)):
        app._sim_spawn(la, lo, vmax_kt=v)
    return advance(app, target_h, cap_s=cap)


def stage_smoke():
    install_log_capture()
    app, screen, t_build = build_app()
    print('BUILD s=%.2f screen=%s map_height=%s md=%s'
          % (t_build, screen.get_size(), app.map_height, app.md))
    st = _setup_sim(app, 24.0, 60.0)
    print('ADVANCE', json.dumps(st))
    print('TCS', json.dumps(tc_summary(app), ensure_ascii=False))
    f0 = render_frame(app, screen, set())
    f1 = render_frame(app, screen, {'track'})
    s, _ = analyze(f0, f1, app.map_height)
    print('DIFF track-vs-none', json.dumps(s))
    print('LOGERR', json.dumps(log_errs(), ensure_ascii=False))
    return 0


def stage_view():
    install_log_capture()
    app, screen, t_build = build_app()
    W, H = screen.get_size()
    mv = app.map_mgr.map_view
    for tag in ('after_build', 'after_global'):
        if tag == 'after_global':
            set_global_view(app)
        print('[%s] scale=%.5f view=(%.1f..%.1f, %.1f..%.1f) img=%dx%d'
              % (tag, mv.scale, mv.lon_min, mv.lon_max, mv.lat_min, mv.lat_max,
                 mv.img_w, mv.img_h))
        for (lat, lon) in ((60.0, 180.0), (30.0, 150.0), (0.0, 180.0), (-30.0, 150.0)):
            print('   lat=%6.1f lon=%6.1f -> px=%s' % (lat, lon,
                                                      app.latlon_to_screen(lat, lon)))
        print('   corners: tl=%s br=%s'
              % (app.screen_to_latlon(2, 2), app.screen_to_latlon(W - 3, app.map_height - 3)))
    return 0


def stage_flow():
    """复现 render.draw_streamlines / draw_currents 共用 _flow_last 限流缓存。"""
    install_log_capture()
    from simulator.simcore import render as Rmod
    app, screen, t_build = build_app()
    st = _setup_sim(app, 24.0, 120.0)
    print('[flow] advance', json.dumps(st))
    MAP_H = int(app.map_height)
    base = render_frame(app, screen, set())

    Rmod._flow_cache.clear()
    Rmod._flow_last.clear()
    f_flow = render_frame(app, screen, {'flow'})
    d_flow = mask_of(base, f_flow)

    Rmod._flow_cache.clear()
    Rmod._flow_last.clear()
    f_cur = render_frame(app, screen, {'current'})
    d_cur = mask_of(base, f_cur)

    # 单帧内同时开 flow + current: render_map 先 flow 后 current, 两者相隔 << 150ms
    Rmod._flow_cache.clear()
    Rmod._flow_last.clear()
    f_both = render_frame(app, screen, {'flow', 'current'})
    d_new = mask_of(f_flow, f_both)          # current 层在"两张都开"里贡献的像素
    inter_flow = int((d_new & d_flow).sum())
    inter_cur = int((d_new & d_cur).sum())
    out = {
        'flow_pix': int(d_flow.sum()), 'current_pix': int(d_cur.sum()),
        'both_new_pix': int(d_new.sum()),
        'new_and_flow': inter_flow, 'new_and_current': inter_cur,
        'iou_new_vs_flow': round(inter_flow / max(1, int((d_new | d_flow).sum())), 4),
        'iou_new_vs_current': round(inter_cur / max(1, int((d_new | d_cur).sum())), 4),
        'cache_keys': len(Rmod._flow_cache),
    }
    print('[flow]', json.dumps(out))
    p = os.path.join(OUT_DIR, 'adv_flowcache__flow_vs_current.png')
    _save_panels(p, [base, f_flow, f_cur, f_both], highlight=d_new)
    with open(os.path.join(OUT_DIR, 'flowcache.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log_dump(os.path.join(OUT_DIR, 'flowcache_logs.json'))
    return 0


def stage_main(args):
    from app.ty_sim_mixins.sim_mode_mixin import SIM_LAYERS, SIM_LAYER_IDS
    from simulator.simcore import render as Rmod
    from simulator.simcore import core as V

    install_log_capture()
    report = {'probe': 'tools/probe_layer_render.py', 'args': vars(args)}
    app, screen, t_build = build_app()
    W, H = screen.get_size()
    MAP_H = int(app.map_height)
    mv = app.map_mgr.map_view
    report['build'] = {'wall_s': round(t_build, 2), 'screen': [W, H],
                       'map_height': MAP_H, 'panel_h': app.control_panel_height,
                       'scale': round(float(mv.scale), 6),
                       'view': [round(mv.lon_min, 1), round(mv.lon_max, 1),
                                round(mv.lat_min, 1), round(mv.lat_max, 1)]}
    print('[build] %.2fs screen=%dx%d map_height=%d scale=%.5f'
          % (t_build, W, H, MAP_H, mv.scale))

    app._sim_reset(msg=False)
    log_reset()
    st = _setup_sim(app, args.target_h, args.adv_cap)
    report['advance'] = st
    report['tcs'] = tc_summary(app)
    report['reset_log_errs'] = log_errs()
    log_reset()
    print('[advance]', json.dumps(st))
    print('[tcs]', json.dumps(report['tcs'], ensure_ascii=False))
    print('[view] tl=%s br=%s' % (app.screen_to_latlon(2, 2),
                                  app.screen_to_latlon(W - 3, MAP_H - 3)))

    # ── 噪声地板 ──
    n1 = render_frame(app, screen, set())
    n2 = render_frame(app, screen, set())
    noise = mask_of(n1, n2, thr=args.thr)
    report['noise_floor'] = {'pix': int(noise.sum()),
                             'pct_frame': round(100.0 * int(noise.sum()) / noise.size, 4)}
    print('[noise]', json.dumps(report['noise_floor']))

    # ── 3. 逐图层像素对比(基线 = 关闭所有叠加层) ──
    def layer_table(screen, MAP_H, noise, tag):
        base_none = render_frame(app, screen, set())
        base_track = render_frame(app, screen, {'track'})
        rows = []
        pngs = []
        for lid in SIM_LAYER_IDS:
            f_one = render_frame(app, screen, {lid})
            f_pair = render_frame(app, screen, {'track', lid})
            r_primary, m_primary = analyze(base_none, f_one, MAP_H, noise, thr=args.thr)
            r_iso, _ = analyze(base_track, f_pair, MAP_H, noise, thr=args.thr)
            p = os.path.join(OUT_DIR, 'layer_%s__%s.png' % (tag, lid))
            _save_panels(p, [base_none, f_one, f_one], highlight=m_primary)
            pngs.append(p)
            row = {'id': lid, 'name': dict((a, b) for a, b, _ in SIM_LAYERS)[lid],
                   'vs_all_off': r_primary, 'vs_track_on': r_iso,
                   'verdict': verdict(r_primary), 'flatness': flatness(r_primary),
                   'png': os.path.relpath(p, REPO).replace('\\', '/')}
            rows.append(row)
            print('[layer:%s] %-9s pix=%-7d pct_map=%-8s panel_px=%-5d meanRGB=%-24s %s'
                  % (tag, lid, r_primary['pix'], r_primary['pct_map'],
                     r_primary['pix_panel'], str(r_primary['diff_mean_color']),
                     row['verdict']))
            print('            iso(vs track-on) pix=%-7d pct_map=%s flat=%s'
                  % (r_iso['pix'], r_iso['pct_map'], row['flatness']))
        return rows, pngs

    table, pngs = layer_table(screen, MAP_H, noise, 'global')
    files = []
    files.extend(pngs)
    report['layer_table'] = table
    report['layer_log_errs'] = log_errs()
    log_reset()

    # ── 3b. 默认视图(用户配置的镜头)下重做一遍逐层表 ──
    try:
        app.map_mgr._fit_view()
        app.map_mgr._cached_map_render = None
        mv2 = app.map_mgr.map_view
        report['default_view'] = {'scale': round(float(mv2.scale), 6),
                                  'tl': [round(v, 2) for v in app.screen_to_latlon(2, 2)],
                                  'br': [round(v, 2)
                                         for v in app.screen_to_latlon(W - 3, MAP_H - 3)]}
        n1 = render_frame(app, screen, set())
        n2 = render_frame(app, screen, set())
        noise2 = mask_of(n1, n2, thr=args.thr)
        table2, pngs2 = layer_table(screen, MAP_H, noise2, 'defaultview')
        files.extend(pngs2)
        report['layer_table_default_view'] = table2
        print('[view:default]', json.dumps(report['default_view']))
    except Exception as e:      # noqa: BLE001
        report['layer_table_default_view'] = {'error': repr(e)}
    # 回到全局视图继续对抗性测试
    set_global_view(app)
    base_none = render_frame(app, screen, set())
    base_track = render_frame(app, screen, {'track'})
    log_reset()

    # ── 4. 风场三种子样式 ──
    wind_modes = {}
    orig_wmode = app.sim_v4.get('_wind_mode')
    try:
        for wm in ('barbs', 'stream', 'particles'):
            app.sim_v4['_wind_mode'] = wm
            if wm == 'particles':
                # 真实运行里粒子由 _sim_update -> particles.step 推进
                # (sim_mode_mixin.py:1224); 不推进则 u=v=0, draw 里 sp>=0.5 全跳过
                for _ in range(3):
                    app.sim_particles.step(app.sim_v4, 0.5)
            fr = render_frame(app, screen, {'wind'})
            res, mm = analyze(base_none, fr, MAP_H, noise, thr=args.thr)
            wind_modes[wm] = {'vs_all_off': res, 'verdict': verdict(res),
                              'flatness': flatness(res)}
            p = os.path.join(OUT_DIR, 'wind__%s.png' % wm)
            _save_panels(p, [base_none, fr, fr], highlight=mm)
            files.append(p)
            print('[wind:%-9s] pix=%d pct_map=%s meanRGB=%s %s'
                  % (wm, res['pix'], res['pct_map'], res['diff_mean_color'],
                     verdict(res)))
    finally:
        app.sim_v4['_wind_mode'] = orig_wmode
    report['wind_modes'] = wind_modes

    # ── 5. 对抗 a) 三互斥层 ──
    mutex = {'handlers': [], 'stacking': {}}
    app.sim_layers = set()
    for lid in ('sst', 'ohc', 'shear', 'sst'):
        app._sim_set_env_layer(lid)
        mutex['handlers'].append({'call': '_sim_set_env_layer(%r)' % lid,
                                  'sim_layers': sorted(app.sim_layers),
                                  'text': app._sim_env_layer_text()})
    app.sim_layers = set()
    for lid in ('sst', 'ohc', 'shear'):
        app._sim_toggle_layer(lid)
        mutex['handlers'].append({'call': '_sim_toggle_layer(%r)' % lid,
                                  'sim_layers': sorted(app.sim_layers),
                                  'text': app._sim_env_layer_text()})
    app.sim_layers = set()
    for lid in ('sst', 'ohc'):
        app._sim_set_layer(lid)
        mutex['handlers'].append({'call': '_sim_set_layer(%r)' % lid,
                                  'sim_layers': sorted(app.sim_layers)})
    f_sst = render_frame(app, screen, {'sst'})
    f_ohc = render_frame(app, screen, {'ohc'})
    f_both = render_frame(app, screen, {'sst', 'ohc'})
    mutex['frames'] = {
        'sst': analyze(base_none, f_sst, MAP_H, noise, thr=args.thr)[0],
        'ohc': analyze(base_none, f_ohc, MAP_H, noise, thr=args.thr)[0],
        'sst+ohc': analyze(base_none, f_both, MAP_H, noise, thr=args.thr)[0]}
    mutex['stacking']['both_vs_sst'] = analyze(f_sst, f_both, MAP_H, noise, thr=args.thr)[0]
    mutex['stacking']['both_vs_ohc'] = analyze(f_ohc, f_both, MAP_H, noise, thr=args.thr)[0]
    _, m_stack = analyze(f_sst, f_both, MAP_H, noise, thr=args.thr)
    p = os.path.join(OUT_DIR, 'adv_mutex__env_stack.png')
    _save_panels(p, [base_none, f_sst, f_ohc, f_both], highlight=m_stack)
    files.append(p)
    report['adv_mutex'] = mutex
    print('[mutex] handlers=%s' % json.dumps(mutex['handlers'], ensure_ascii=False))
    print('[mutex] both_vs_sst pix=%d pct_map=%s ; both_vs_ohc pix=%d pct_map=%s'
          % (mutex['stacking']['both_vs_sst']['pix'],
             mutex['stacking']['both_vs_sst']['pct_map'],
             mutex['stacking']['both_vs_ohc']['pix'],
             mutex['stacking']['both_vs_ohc']['pct_map']))

    # ── 6. 对抗 b) 有风暴 vs 无风暴(图层是否只是静态底图) ──
    tcs_backup = app.sim_v4['tcs']
    app.sim_v4['tcs'] = []
    base_none_ns = render_frame(app, screen, set())
    frames_ns = {lid: render_frame(app, screen, {lid}) for lid in SIM_LAYER_IDS}
    app.sim_v4['tcs'] = tcs_backup
    hud_mask = mask_of(base_none, base_none_ns, thr=args.thr)
    coupling = {}
    for lid in SIM_LAYER_IDS:
        fr = render_frame(app, screen, {lid})
        res, m = analyze(frames_ns[lid], fr, MAP_H, noise, thr=args.thr)
        m2 = m & ~hud_mask
        res['excl_hud_pix'] = int(m2.sum())
        res['excl_hud_pct_map'] = round(
            100.0 * float(m2[:MAP_H].sum()) / max(1, MAP_H * W), 4)
        res['hud_pix'] = int((m & hud_mask).sum())
        coupling[lid] = res
        p = os.path.join(OUT_DIR, 'adv_nostorm__%s.png' % lid)
        _save_panels(p, [frames_ns[lid], fr, fr], highlight=m)
        files.append(p)
        print('[nostorm] %-9s pix=%-6d pct_map=%-8s excl_hud=%-6d (%.4f%%) hud=%d'
              % (lid, res['pix'], res['pct_map'], res['excl_hud_pix'],
                 res['excl_hud_pct_map'], res['hud_pix']))
    report['adv_storm_coupling'] = coupling
    report['adv_hud_mask_pix'] = int(hud_mask.sum())
    report['nostorm_log_errs'] = log_errs()
    log_reset()

    # ── 7. 对抗 c) 投影对齐 ──
    align = {'gridmap_vs_pointmap': [], 'marker_roundtrip': {}}
    gpad = app._sim_grid_map_pad(screen)
    gcore = app._sim_grid_map(screen)
    for lat in (50.0, 30.0, 0.0, -30.0, -50.0):
        for lon in (60.0, 150.0, 240.0, 330.0):
            px, py = app.latlon_to_screen(lat, lon)
            row = {'lat': lat, 'lon': lon, 'point_px': [int(px), int(py)]}
            if gpad is not None:
                row['pad_px'] = [round(gpad.x(lon), 1), round(gpad.y(lat), 1)]
                row['pad_dx'] = round(gpad.x(lon) - px, 1)
                row['pad_dy'] = round(gpad.y(lat) - py, 1)
            if gcore is not None:
                row['core_px'] = [round(gcore.x(lon), 1), round(gcore.y(lat), 1)]
            align['gridmap_vs_pointmap'].append(row)
    if gpad is not None:
        align['pad_abs_dx_max'] = round(max(abs(r['pad_dx'])
                                            for r in align['gridmap_vs_pointmap']), 1)
        align['pad_abs_dy_max'] = round(max(abs(r['pad_dy'])
                                            for r in align['gridmap_vs_pointmap']), 1)

    orig = app.sim_v4['env']['sst']
    ny, nx = orig.shape[:2]

    def _cell(lat, lon):
        j = int((lat - V.C['LAT0']) / V.C['ENVD'])
        i = int(((lon - V.C['LON0']) % 360.0) / V.C['ENVD'])
        return max(0, min(ny - 1, j)), i % nx

    def _render_const(blob=None):
        arr = np.full((ny, nx), 5.0, dtype=orig.dtype)
        if blob is not None:
            j, i = _cell(blob[0], blob[1])
            j0, j1 = max(0, j - 6), min(ny, j + 7)
            arr[j0:j1, (i - 6) % nx:(i + 7) % nx] = 33.0
        app.sim_v4['env']['sst'] = arr
        Rmod._env_arr_cache.clear()
        Rmod._layer_surf_cache.clear()
        return render_frame(app, screen, {'sst'})

    try:
        f_const = _render_const(None)
        pts = [(50.0, 150.0), (35.0, 150.0), (20.0, 150.0), (0.0, 150.0),
               (-20.0, 150.0), (-35.0, 150.0), (-50.0, 150.0),
               (20.0, 60.0), (20.0, 240.0), (20.0, 330.0)]
        rows = []
        for (lat, lon) in pts:
            sx, sy = app.latlon_to_screen(lat, lon)
            inside = (0 <= sx < W) and (0 <= sy < MAP_H)
            f = _render_const((lat, lon))
            w = absdiff(f, f_const).max(axis=2).astype(np.float64)
            w[w <= 2] = 0.0
            tot = float(w.sum())
            rec = {'true_lat': lat, 'true_lon': lon, 'expect_px': [int(sx), int(sy)],
                   'on_screen': bool(inside)}
            if tot > 0:
                ys, xs = np.nonzero(w)
                cy = float((ys * w[ys, xs]).sum() / tot)
                cx = float((xs * w[ys, xs]).sum() / tot)
                la2, lo2 = app.screen_to_latlon(int(round(cx)), int(round(cy)))
                rec.update({'centroid_px': [round(cx, 1), round(cy, 1)],
                            'back_lat': round(float(la2), 2),
                            'back_lon': round(float(lo2), 2),
                            'dlat': round(float(la2) - lat, 2),
                            'dlon': round(float(lo2) - lon, 2),
                            'marker_pix': int((w > 0).sum())})
            rows.append(rec)
            print('[align] true=(%6.1f,%6.1f) on=%-5s back_lat=%-7s dlat=%-7s dlon=%s'
                  % (lat, lon, inside, rec.get('back_lat'), rec.get('dlat'),
                     rec.get('dlon')))
        align['marker_roundtrip']['sst'] = rows
        ok = [r for r in rows if r.get('on_screen') and 'back_lat' in r]
        if len(ok) >= 3:
            y = np.array([r['true_lat'] for r in ok], dtype=float)
            x = np.array([r['back_lat'] for r in ok], dtype=float)
            A = np.vstack([y, np.ones_like(y)]).T
            slope, icpt = np.linalg.lstsq(A, x, rcond=None)[0]
            resid = float(np.max(np.abs(A @ np.array([slope, icpt]) - x)))
            align['marker_roundtrip']['fit_back_lat_vs_true_lat'] = {
                'slope': round(float(slope), 4), 'intercept': round(float(icpt), 3),
                'max_resid_deg': round(resid, 3), 'n': len(ok)}
            yl = np.array([r['true_lon'] for r in ok], dtype=float)
            xl = np.array([r['back_lon'] for r in ok], dtype=float)
            Al = np.vstack([yl, np.ones_like(yl)]).T
            sl, il = np.linalg.lstsq(Al, xl, rcond=None)[0]
            align['marker_roundtrip']['fit_back_lon_vs_true_lon'] = {
                'slope': round(float(sl), 4), 'intercept': round(float(il), 3)}
            print('[align] fit back_lat = %.4f*true_lat + %.3f (max_resid=%.2f deg, n=%d)'
                  % (slope, icpt, resid, len(ok)))
            print('[align] fit back_lon = %.4f*true_lon + %.3f' % (sl, il))
        # 出图: 常数场 / 一个纬度的标记 / 差异
        _, m_hi = analyze(f_const, _render_const((35.0, 150.0)), MAP_H, thr=2)
        p = os.path.join(OUT_DIR, 'adv_align__sst_marker_35N.png')
        _save_panels(p, [f_const, _render_const((35.0, 150.0)),
                         _render_const((35.0, 150.0))], highlight=m_hi)
        files.append(p)
    finally:
        app.sim_v4['env']['sst'] = orig
        Rmod._env_arr_cache.clear()
        Rmod._layer_surf_cache.clear()

    try:
        arr_pad = Rmod.render_env_layer(app.sim_v4, 'sst')
        align['env_layer_array'] = {
            'core_sst_shape': list(orig.shape),
            'padded_sst_shape': list(arr_pad.shape),
            'row_ratio': round(arr_pad.shape[0] / orig.shape[0], 3),
            'blit_lat_span': [Rmod.LAYER_LAT0, Rmod.LAYER_LAT1],
            'core_lat_span': [V.C['LAT0'], V.C['LAT1']],
            'rows_per_deg_blit': round(arr_pad.shape[0] /
                                       (Rmod.LAYER_LAT1 - Rmod.LAYER_LAT0), 3),
            'rows_per_deg_core': round(orig.shape[0] / (V.C['LAT1'] - V.C['LAT0']), 3)}
    except Exception as e:      # noqa: BLE001
        align['env_layer_array'] = {'error': repr(e)}
    print('[align] env array', json.dumps(align['env_layer_array']))

    # 点类图层: track 差异像素到风暴期望位置的最小距离
    try:
        _, mt = analyze(base_none, base_track, MAP_H, noise, thr=args.thr)
        ys, xs = np.nonzero(mt)
        pts_px = np.stack([xs, ys], axis=1).astype(np.float64)
        prox = []
        for tc in report['tcs']:
            ex, ey = app.latlon_to_screen(tc['lat'], tc['lon'])
            on = (0 <= ex < W) and (0 <= ey < MAP_H)
            d = (round(float(np.min(np.hypot(pts_px[:, 0] - ex, pts_px[:, 1] - ey))), 2)
                 if len(pts_px) else None)
            prox.append({'id': tc['id'], 'name': tc['name'], 'expect': [int(ex), int(ey)],
                         'on_screen': bool(on), 'min_dist_px': d})
        align['track_pixel_proximity'] = prox
        print('[align] track proximity', json.dumps(prox, ensure_ascii=False))
    except Exception as e:      # noqa: BLE001
        align['track_pixel_proximity'] = {'error': repr(e)}
    report['adv_align'] = align

    # ── 8. 对抗 d) 残影 ──
    residue = {}
    a1 = render_frame(app, screen, {'sst'})
    render_frame(app, screen, {'shear'})
    a2 = render_frame(app, screen, {'sst'})
    residue['sst_shear_sst'] = analyze(a1, a2, MAP_H, noise, thr=args.thr)[0]
    residue['none_after_heavy_vs_first_none'] = analyze(
        base_none, render_frame(app, screen, set()), MAP_H, noise, thr=args.thr)[0]
    b1 = render_frame(app, screen, {'ohc'})
    residue['ohc_repeat'] = analyze(
        render_frame(app, screen, {'ohc'}), b1, MAP_H, noise, thr=args.thr)[0]
    report['adv_residue'] = residue
    _save_panels(os.path.join(OUT_DIR, 'adv_residue__sst_shear_sst.png'),
                 [a1, a2, a2], highlight=mask_of(a1, a2, args.thr))
    files.append(os.path.join(OUT_DIR, 'adv_residue__sst_shear_sst.png'))
    print('[residue]', json.dumps({k: v['pix'] for k, v in residue.items()}))

    # ── 9. 对抗 d2) sim_path_tys 泄漏 ──
    stale = {}
    app.sim_path_tys = {}
    f_clean_none = render_frame(app, screen, set(), reset_path_cache=False)
    render_frame(app, screen, {'track'}, reset_path_cache=False)
    f_none_after = render_frame(app, screen, set(), reset_path_cache=False)
    stale['none_first_vs_none_after_track'] = analyze(
        f_clean_none, f_none_after, MAP_H, noise, thr=args.thr)[0]
    stale['path_tys_after'] = {str(k): len(v.pts) for k, v in app.sim_path_tys.items()}
    report['adv_stale_path'] = stale
    _save_panels(os.path.join(OUT_DIR, 'adv_stale__none_after_track.png'),
                 [f_clean_none, f_none_after, f_none_after],
                 highlight=mask_of(f_clean_none, f_none_after, args.thr))
    files.append(os.path.join(OUT_DIR, 'adv_stale__none_after_track.png'))
    print('[stale] pix=%d pct_map=%s'
          % (stale['none_first_vs_none_after_track']['pix'],
             stale['none_first_vs_none_after_track']['pct_map']))

    # ── 10. 对抗 e) 无风暴逐层 ──
    tcs_backup = app.sim_v4['tcs']
    app.sim_v4['tcs'] = []
    log_reset()
    nostorm = {}
    try:
        b_ns = render_frame(app, screen, set())
        for lid in SIM_LAYER_IDS:
            fr = render_frame(app, screen, {lid})
            res, _ = analyze(b_ns, fr, MAP_H, noise, thr=args.thr)
            nostorm[lid] = {'vs_all_off': res, 'verdict': verdict(res),
                            'flatness': flatness(res)}
        report['adv_nostorm_layers'] = nostorm
        report['adv_nostorm_log_errs'] = log_errs()
        print('[nostorm-layer]', json.dumps(
            {k: [v['vs_all_off']['pix'], v['vs_all_off']['pct_map']]
             for k, v in nostorm.items()}))
    finally:
        app.sim_v4['tcs'] = tcs_backup
        log_reset()

    # ── 11. 面板按钮可见性 ──
    try:
        app.draw_control_panel(screen)
        pnl = getattr(app, '_panel', None)
        bm = getattr(pnl, '_btn_map', None) if pnl is not None else None
        if bm:
            report['buttons'] = {k: bool(getattr(v, 'visible', True))
                                 for k, v in bm.items()
                                 if k.startswith(('layer_', 'envlay_', 'wind_mode',
                                                  'shear_style'))}
    except Exception as e:      # noqa: BLE001
        report['buttons'] = {'error': repr(e)}
    print('[buttons]', json.dumps(report['buttons']))

    report['files'] = [os.path.relpath(p, REPO).replace('\\', '/') for p in files]
    with open(os.path.join(OUT_DIR, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    log_dump(os.path.join(OUT_DIR, 'render_logs.json'))
    print('[done] files=%d' % len(report['files']))
    return 0


def env_stats(app):
    """数值证据: 环境场极值 + 台风中心处的场值。"""
    from simulator.simcore.core import env_bilinear
    env = (app.sim_v4 or {}).get('env') or {}
    out = {'fields': {}}
    for k in ('pDisp', 'pTot', 'pEnv', 'sst', 'shear', 'ohc'):
        a = env.get(k)
        if a is None:
            continue
        out['fields'][k] = {'min': round(float(np.min(a)), 2),
                            'max': round(float(np.max(a)), 2),
                            'mean': round(float(np.mean(a)), 2),
                            'std': round(float(np.std(a)), 2)}
    at = []
    from simulator.simcore.core import C as _C
    _ENVD = float(_C['ENVD'])
    for tc in (app.sim_v4 or {}).get('tcs', []):
        if tc['dead']:
            continue
        lo, la = float(tc['lon']), float(tc['lat'])
        row = {'id': tc['id'], 'name': tc['name'], 'lon': round(lo, 2),
               'lat': round(la, 2), 'pmin_model': round(float(tc['pmin']), 1),
               'vmax': round(float(tc['vmax']), 1)}
        for k in ('pDisp', 'pTot', 'sst', 'shear'):
            a = env.get(k)
            if a is not None:
                row[k + '_at_center'] = round(float(env_bilinear(a, lo, la)), 2)
                j = int((la - _C['LAT0']) / _ENVD)
                i = int(((lo - _C['LON0']) % 360.0) / _ENVD)
                j0, j1 = max(0, j - 3), min(a.shape[0], j + 4)
                i0, i1 = max(0, i - 3), min(a.shape[1], i + 4)
                row[k + '_min_7x7'] = round(float(np.min(a[j0:j1, i0:i1])), 2)
        at.append(row)
    out['at_tc_center'] = at
    return out


def stage_coupling(args):
    """对抗 b) 两次独立运行: 有风暴 vs 完全无风暴(关掉自然生成, 不手动生成)。

    只清 tcs 列表**测不出**环境层与风暴的关系 —— 环境场(含 TC 亏损/冷尾流)是在
    env_update 时算好的, 渲染只读数组。所以必须跑两遍同种子模拟。
    """
    from app.ty_sim_mixins.sim_mode_mixin import SIM_LAYER_IDS
    from simulator.simcore import render as Rmod

    install_log_capture()
    app, screen, t_build = build_app()
    MAP_H = int(app.map_height)
    print('[coupling] build %.1fs global view scale=%.5f'
          % (t_build, app.map_mgr.map_view.scale))

    def run_case(spawn, natgen, tag):
        app.sim_natural_gen = bool(natgen)
        app._sim_reset(msg=False)
        if spawn:
            for la, lo, v in ((16.0, 138.0, 65.0), (14.0, 265.0, 55.0),
                              (-14.0, 75.0, 50.0)):
                app._sim_spawn(la, lo, vmax_kt=v)
        st = advance(app, args.target_h, cap_s=args.adv_cap)
        # 重置后 _envVer 回到 1: 不清缓存会命中上一轮的整层贴图
        Rmod._env_arr_cache.clear()
        Rmod._layer_surf_cache.clear()
        Rmod._flow_cache.clear()
        Rmod._flow_last.clear()
        log_reset()
        base = render_frame(app, screen, set())
        frames = {lid: render_frame(app, screen, {lid}) for lid in SIM_LAYER_IDS}
        errs = log_errs()
        env_raw = {k: v for k, v in (app.sim_v4.get('env') or {}).items()
                   if isinstance(v, np.ndarray) and v.ndim == 2}
        print('[coupling:%s] adv=%s n_tc=%d logerrs=%d'
              % (tag, json.dumps(st), len(app.sim_v4['tcs']), len(errs)))
        return {'base': base, 'frames': frames, 'adv': st, 'env_raw': env_raw,
                'tcs': tc_summary(app), 'env': env_stats(app), 'log_errs': errs}

    A = run_case(True, True, 'storm')
    B = run_case(False, False, 'nostorm')
    hud = mask_of(B['base'], A['base'], thr=args.thr)
    out = {'storm': {'adv': A['adv'], 'tcs': A['tcs'], 'env': A['env']},
           'nostorm': {'adv': B['adv'], 'tcs': B['tcs'], 'env': B['env']},
           'hud_mask_pix': int(hud.sum()), 'layers': {}}
    files = []
    for lid in SIM_LAYER_IDS:
        res, m = analyze(B['frames'][lid], A['frames'][lid], MAP_H, None, thr=args.thr)
        m2 = m & ~hud
        res['excl_hud_pix'] = int(m2.sum())
        res['excl_hud_pct_map'] = round(
            100.0 * float(m2[:MAP_H].sum()) / max(1, MAP_H * A['base'].shape[1]), 4)
        out['layers'][lid] = res
        p = os.path.join(OUT_DIR, 'coupling__%s.png' % lid)
        _save_panels(p, [B['frames'][lid], A['frames'][lid], A['frames'][lid]],
                     highlight=m)
        files.append(p)
        print('[coupling] %-9s pix=%-7d pct_map=%-8s excl_hud=%-7d (%.4f%%)'
              % (lid, res['pix'], res['pct_map'], res['excl_hud_pix'],
                 res['excl_hud_pct_map']))

    # ── 环境场逐格差(风暴到底有没有进环境数组) ──
    ev = {}
    envA, envB = A['env_raw'], B['env_raw']
    for k in sorted(set(envA) & set(envB)):
        a, b = np.asarray(envA[k]), np.asarray(envB[k])
        if a.shape != b.shape or a.ndim != 2 or a.dtype.kind not in 'fc':
            continue
        d = np.abs(a.astype(np.float64) - b.astype(np.float64))
        ev[k] = {'max_abs': round(float(d.max()), 4),
                 'mean_abs': round(float(d.mean()), 5),
                 'cells_gt_0.5': int((d > 0.5).sum()),
                 'cells_gt_2': int((d > 2.0).sum()), 'n': int(d.size)}
    out['env_cellwise_storm_vs_nostorm'] = ev
    print('[coupling] env cellwise', json.dumps(ev, ensure_ascii=False))

    # ── 气压层用的是 pDisp(环境场)还是 pTot(含 TC 亏损)? ──
    # 见 core.py:1734-1771: pTot = pEnv + Holland 亏损; pDisp = 1013 + 平滑(pEnv-mean)
    # render.py:678 draw_contours 取 pDisp 优先。这里把同一帧的场在 pDisp(A) 与
    # pTot(A) 之间切换, 看"风暴的闭合等压线"本来会不会画出来。
    try:
        env_now = app.sim_v4['env']
        saved = env_now.get('pDisp')
        env_now['pDisp'] = envA['pDisp']
        f_pdisp = render_frame(app, screen, {'pressure'})
        env_now['pDisp'] = envA['pTot']
        f_ptot = render_frame(app, screen, {'pressure'})
        env_now['pDisp'] = saved
        res, m = analyze(f_pdisp, f_ptot, MAP_H, None, thr=args.thr)
        out['pressure_pdisp_vs_ptot'] = res
        _save_panels(os.path.join(OUT_DIR, 'adv_pressure__pdisp_vs_ptot.png'),
                     [A['base'], f_pdisp, f_ptot], highlight=m)
        files.append(os.path.join(OUT_DIR, 'adv_pressure__pdisp_vs_ptot.png'))
        print('[coupling] pressure pDisp-vs-pTot pix=%d pct_map=%s'
              % (res['pix'], res['pct_map']))
    except Exception as e:      # noqa: BLE001
        out['pressure_pdisp_vs_ptot'] = {'error': repr(e)}
    print('[coupling] env_storm', json.dumps(A['env'], ensure_ascii=False))
    print('[coupling] env_nostorm', json.dumps(B['env'], ensure_ascii=False))
    out['files'] = [os.path.relpath(p, REPO).replace('\\', '/') for p in files]
    with open(os.path.join(OUT_DIR, 'coupling.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log_dump(os.path.join(OUT_DIR, 'coupling_logs.json'))
    return 0


def stage_extra(args):
    """① 已播路径(显示钟推进过)下的 track 层像素验证; ② 对齐参考线出图。"""
    from simulator.simcore import render as Rmod
    from simulator.simcore import core as V

    install_log_capture()
    app, screen, t_build = build_app()
    W, H = screen.get_size()
    MAP_H = int(app.map_height)
    st = _setup_sim(app, args.target_h, args.adv_cap)
    print('[extra] build=%.1fs advance=%s' % (t_build, json.dumps(st)))

    # ── ① 把显示钟推到"物理 - 一步"(等价于已播放到最后一报) ──
    step_h = app._sim_step_h()
    app._sim_disp_init = True
    app._sim_disp_h = max(0.0, float(app.sim_v4['t']) - step_h)
    base_none = render_frame(app, screen, set())
    f_track = render_frame(app, screen, {'track'})
    res, m = analyze(base_none, f_track, MAP_H, None, thr=args.thr)
    pts = {}
    for tc in app.sim_v4['tcs']:
        ty = app.sim_path_tys.get(tc['id'])
        if ty is not None:
            pts[str(tc['id'])] = {'len_pts': len(ty.pts), 'ci': int(ty.ci),
                                  'track_n': len(tc['track'])}
    out = {'disp_h': app._sim_disp_h, 't_phys': float(app.sim_v4['t']),
           'track_layer': res, 'path_points_drawn': pts}
    print('[extra] disp_h=%.1f track layer pix=%d pct_map=%s path_pts=%s'
          % (app._sim_disp_h, res['pix'], res['pct_map'], json.dumps(pts)))
    p = os.path.join(OUT_DIR, 'layer_global__track_played.png')
    _save_panels(p, [base_none, f_track, f_track], highlight=m)
    files = [p]

    # ── ② 对齐参考线: env.sst 换成常数 + 已知纬度的小块, 画期望/实际两条线 ──
    orig = app.sim_v4['env']['sst']
    ny, nx = orig.shape[:2]
    rows = []
    try:
        for lat in (50.0, 35.0, 20.0, 0.0, -20.0, -35.0, -50.0):
            lon = 150.0
            arr = np.full((ny, nx), 5.0, dtype=orig.dtype)
            j = int((lat - V.C['LAT0']) / V.C['ENVD'])
            i = int(((lon - V.C['LON0']) % 360.0) / V.C['ENVD'])
            arr[max(0, j - 6):min(ny, j + 7), (i - 6) % nx:(i + 7) % nx] = 33.0
            app.sim_v4['env']['sst'] = arr
            Rmod._env_arr_cache.clear()
            Rmod._layer_surf_cache.clear()
            f = render_frame(app, screen, {'sst'})
            # 用"常数场"帧做差更稳: 再渲一帧常数
            arr0 = np.full((ny, nx), 5.0, dtype=orig.dtype)
            app.sim_v4['env']['sst'] = arr0
            Rmod._env_arr_cache.clear()
            Rmod._layer_surf_cache.clear()
            f0 = render_frame(app, screen, {'sst'})
            d = absdiff(f, f0).max(axis=2).astype(np.float64)
            d[d <= 2] = 0.0
            tot = float(d.sum())
            ey = int(app.latlon_to_screen(lat, lon)[1])
            rec = {'true_lat': lat, 'expect_y': ey}
            if tot > 0:
                ys, xs = np.nonzero(d)
                cy = float((ys * d[ys, xs]).sum() / tot)
                cx = float((xs * d[ys, xs]).sum() / tot)
                la2, lo2 = app.screen_to_latlon(int(round(cx)), int(round(cy)))
                rec.update({'drawn_y': round(cy, 1), 'dy_px': round(cy - ey, 1),
                            'back_lat': round(float(la2), 2),
                            'dlat': round(float(la2) - lat, 2)})
            rows.append(rec)
            # 画参考线: 绿=期望(真实纬度), 红=实际画出来的
            surf = pygame.Surface((W, H))
            pygame.surfarray.blit_array(surf, np.transpose(f, (1, 0, 2)))
            pygame.draw.line(surf, (0, 255, 0), (0, ey), (W - 1, ey), 2)
            pygame.draw.line(surf, (255, 60, 60), (0, int(round(rec.get('drawn_y', ey)))),
                             (W - 1, int(round(rec.get('drawn_y', ey)))), 2)
            lbl = rt_label('true %.0fN  y=%d   drawn %.1fN  y=%s   dlat=%s'
                           % (lat, ey, rec.get('back_lat', float('nan')),
                              rec.get('drawn_y'), rec.get('dlat')))
            surf.blit(lbl, (10, 300))
            pth = os.path.join(OUT_DIR, 'adv_align_line__lat%+03d.png' % int(lat))
            pygame.image.save(surf, pth)
            files.append(pth)
            print('[extra:align] true=%+.0f expect_y=%-4d drawn_y=%-7s back_lat=%-7s dlat=%s'
                  % (lat, ey, rec.get('drawn_y'), rec.get('back_lat'), rec.get('dlat')))
    finally:
        app.sim_v4['env']['sst'] = orig
        Rmod._env_arr_cache.clear()
        Rmod._layer_surf_cache.clear()
    out['align_lines'] = rows
    out['files'] = [os.path.relpath(p, REPO).replace('\\', '/') for p in files]
    with open(os.path.join(OUT_DIR, 'extra.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log_dump(os.path.join(OUT_DIR, 'extra_logs.json'))
    print('[extra] files=%d' % len(files))
    return 0


def rt_label(text):
    from app.constants import rt, f_s
    return rt(f_s, text, (255, 255, 0))


def stage_flow2(args):
    """flow/current 共用 `_flow_last` 150ms 限流缓存的**决定性**验证。

    做法: 包 _build_streamlines 记录"谁真的重建了流线面"(field=None → 环流;
    field=(u,v) → 洋流), 并把右下角图例区域从差异里扣掉(图例条数不同会污染 diff)。
    """
    from simulator.simcore import render as Rmod

    install_log_capture()
    app, screen, t_build = build_app()
    W, H = screen.get_size()
    MAP_H = int(app.map_height)
    st = _setup_sim(app, args.target_h, args.cap_flow)
    print('[flow2] build=%.1fs advance=%s' % (t_build, json.dumps(st)))
    print('[flow2] ocn keys=%s'
          % sorted((app.sim_v4.get('ocn') or {}).keys())[:8])

    calls = []
    orig_build = Rmod._build_streamlines

    def wrapped(sim, fx, fy, w, h, seeds, steps, step_h, field=None, **kw):
        calls.append({'layer': 'flow' if field is None else 'current',
                      'seeds': seeds, 'steps': steps})
        return orig_build(sim, fx, fy, w, h, seeds, steps, step_h,
                          field=field, **kw)

    Rmod._build_streamlines = wrapped

    def one(layers):
        Rmod._flow_cache.clear()
        Rmod._flow_last.clear()
        calls.clear()
        f = render_frame(app, screen, layers)
        return f, list(calls), len(Rmod._flow_cache)

    A, cA, kA = one({'flow'})
    C, cC, kC = one({'current'})
    D, cD, kD = one({'flow', 'current'})
    A2, cA2, kA2 = one({'flow'})
    Rmod._build_streamlines = orig_build

    # 扣掉右下角"流场图例"面板(条数会随开关变化)与控制面板(y>=MAP_H, 图层标签会变)
    legend = np.zeros((H, W), dtype=bool)
    legend[455:556, 1030:W] = True
    legend[MAP_H:, :] = True
    base = render_frame(app, screen, set())
    mA = mask_of(base, A, args.thr) & ~legend
    mC = mask_of(base, C, args.thr) & ~legend
    mD_new = mask_of(A, D, args.thr) & ~legend    # D 比 A 多出来的像素
    out = {
        'build_calls': {'flow_only': cA, 'current_only': cC, 'both': cD,
                        'flow_again': cA2},
        'cache_keys': {'flow_only': kA, 'current_only': kC, 'both': kD},
        'determinism_flow_two_renders_pix': analyze(A, A2, H, None,
                                                    thr=args.thr)[0]['pix'],
        'mask_excludes': 'y>=map_height=%d 或 右下角图例矩形(455:556,1030:%d)' % (MAP_H, W),
        'pix': {'flow': int(mA.sum()), 'current': int(mC.sum()),
                'both_new': int(mD_new.sum())},
        'iou_new_vs_flow': round(float((mD_new & mA).sum())
                                 / max(1, int((mD_new | mA).sum())), 4),
        'iou_new_vs_current': round(float((mD_new & mC).sum())
                                    / max(1, int((mD_new | mC).sum())), 4),
        'new_eq_flow_exact': bool(np.array_equal(mD_new, mA)),
        'new_is_empty': bool(mD_new.sum() == 0),
    }
    print('[flow2]', json.dumps(out, ensure_ascii=False))
    _save_panels(os.path.join(OUT_DIR, 'adv_flowcache2__mask.png'),
                 [base, A, C, D],
                 highlight=mask_of(A, D, args.thr))
    with open(os.path.join(OUT_DIR, 'flowcache2.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


def stage_particles(args):
    """粒子风场在"从未播放过"时是否有内容(Particles.__init__ 里 u=v=0)。"""
    from app.ty_sim_mixins.sim_mode_mixin import SIM_LAYER_IDS  # noqa: F401
    install_log_capture()
    app, screen, t_build = build_app()
    MAP_H = int(app.map_height)
    app._sim_reset(msg=False)
    app._sim_spawn(16.0, 138.0, vmax_kt=65.0)
    base = render_frame(app, screen, set())
    out = {}
    for wm in ('barbs', 'particles'):
        app.sim_v4['_wind_mode'] = wm
        f = render_frame(app, screen, {'wind'})
        res, _ = analyze(base, f, MAP_H, None, thr=args.thr)
        out[wm + '_no_play'] = res
        print('[particles] %-9s (sim_playing=%s) pix=%d pct_map=%s'
              % (wm, app.sim_playing, res['pix'], res['pct_map']))
    # 模拟真实播放: 按 _sim_update 的做法推进粒子 3 帧
    app.sim_v4['_wind_mode'] = 'particles'
    for _ in range(3):
        app.sim_particles.step(app.sim_v4, 0.5)
    f = render_frame(app, screen, {'wind'})
    res, _ = analyze(base, f, MAP_H, None, thr=args.thr)
    out['particles_after_3_steps'] = res
    print('[particles] after 3 steps pix=%d pct_map=%s' % (res['pix'], res['pct_map']))
    print('[particles] n=%d u_absmax=%.3f v_absmax=%.3f'
          % (app.sim_particles.n,
             float(np.abs(app.sim_particles.u).max()),
             float(np.abs(app.sim_particles.v).max())))
    with open(os.path.join(OUT_DIR, 'particles.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='smoke')
    ap.add_argument('--target-h', type=float, default=24.0)
    ap.add_argument('--adv-cap', type=float, default=120.0)
    ap.add_argument('--cap-flow', type=float, default=60.0)
    ap.add_argument('--thr', type=int, default=8)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    return {'smoke': stage_smoke, 'main': lambda: stage_main(args),
            'flow': stage_flow, 'flow2': lambda: stage_flow2(args),
            'view': stage_view, 'coupling': lambda: stage_coupling(args),
            'particles': lambda: stage_particles(args),
            'extra': lambda: stage_extra(args)}.get(args.stage, lambda: 2)()


if __name__ == '__main__':
    sys.exit(main())
