# -*- coding: utf-8 -*-
"""模拟模式**帧率/流畅度**实测台(只测量, 不修改任何被监视文件)。

回答: "模拟模式用起来卡不卡? 面板宣称的档位兑现了吗?"

真启动 `TySim`(dummy SDL 无头, 真实窗口尺寸 2560x1498), 进 MODE_SIM, 播一段。
每帧量(与 main.py 主循环同构: `sim.update(dt)` + `sim.draw(screen)`):
  · t_frame  墙钟帧时长 (ms) = update + draw + flip
  · t_update = 物理推进(main.py 的 `sim._t_update_ms`)
  · t_draw   = 渲染(main.py 的 `sim._t_draw_ms`)
  · _sim_advance 推进的模拟小时 / 其内部墙钟 `_sim_last_budget_ms`
  · 显示钟 `_sim_disp_h` 是否前进
对比面板口径:
  · `_sim_tier_target_ms()`  面板认为"一帧该花多久"(档位名)
  · `sim['stepCostMs']`      面板实测"每 6h 模拟时间的墙钟 ms"
  · `_sim_cost_warn()`       面板的"超上限"提示
  · `_sim_effective_x()` / `_sim_rate_text()`  面板显示的有效倍速

阶段(可组合, 一次预热全跑完):
  pacing   —— 逐档位 x 帧时长(物理+渲染, 真实播放)
  render   —— 暂停物理, 纯渲染: 逐图层组合
  switch   —— 暂停物理, 逐图层"刚打开那一帧"的冷启动开销
  combined —— 指定档位 + 全部图层(最坏组合)

用法:
  PYTHONIOENCODING=utf-8 python tools/probe_sim_fps.py --phases pacing,render,switch
  ... --tiers 400ms,50ms,100ms,800ms,2s --warm-tcs 8 --frames 240 --reps 2
"""
import argparse
import json
import os
import statistics
import sys
import time

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import pygame  # noqa: E402
pygame.init()

from app.config import AppConfig  # noqa: E402
import app.constants as constants  # noqa: E402
from app.ty_sim import TySim  # noqa: E402
from simulator.simcore import core as V  # noqa: E402
from app.ty_sim_mixins.sim_mode_mixin import SIM_LAYER_IDS  # noqa: E402
import app.ty_sim_mixins.sim_mode_mixin as M  # noqa: E402

ALL_LAYERS = list(SIM_LAYER_IDS)


def pct(xs, p):
    if not xs:
        return float('nan')
    ys = sorted(xs)
    i = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
    return ys[i]


def stats(xs):
    if not xs:
        return {'n': 0}
    return {'n': len(xs), 'p50': pct(xs, 50), 'p90': pct(xs, 90),
            'p99': pct(xs, 99), 'max': max(xs), 'mean': sum(xs) / len(xs)}


def over(xs, thr):
    return 100.0 * sum(1 for x in xs if x > thr) / max(1, len(xs))


def parse_layer_set(spec):
    if spec.strip().lower() == 'all':
        return set(ALL_LAYERS)
    return set(x.strip() for x in spec.split(',') if x.strip() in ALL_LAYERS)


class Harness:
    def __init__(self, size, cfg_path):
        self.sw, self.sh = size
        self.cfg = AppConfig.load(cfg_path)
        constants.SW, constants.SH = self.sw, self.sh
        constants.MH = self.sh - constants.CPH
        t0 = time.perf_counter()
        self.screen = pygame.display.set_mode((self.sw, self.sh))
        self.app = TySim(self.screen, cfg=self.cfg)
        self.boot_s = time.perf_counter() - t0
        self.app.md = self.app.MODE_SIM
        self._rec = {'h': 0.0}
        self._orig_advance = self.app._sim_advance
        app = self.app

        def _wrapped(dt):
            r = self._orig_advance(dt)
            self._rec['h'] = r
            return r
        app._sim_advance = _wrapped

    def reset(self, tier):
        self.cfg.sim_precision = tier
        self.app._sim_reset(msg=False)
        self.app.sim_v4.setdefault('params', {})['precision'] = tier

    def set_tier(self, tier):
        self.cfg.sim_precision = tier
        self.app.sim_v4.setdefault('params', {})['precision'] = tier
        self.app.sim_v4.pop('_pendStep', None)
        self.app.sim_v4['stepCostMs'] = 0.0
        self.app._sim_rate_reset()

    def warm(self, hours, warm_tier, tcs_target, forecast=False, hard_s=600.0):
        """快进到 t>=hours 或 tcs>=tcs_target(先到者)。用临时放宽的单帧预算。"""
        self.reset(warm_tier)
        sim = self.app.sim_v4
        sim.setdefault('params', {})['forecast'] = bool(forecast)
        _b0, _u0 = M.SIM_FRAME_BUDGET_MS, M.SIM_MAX_UNITS_PER_FRAME
        M.SIM_FRAME_BUDGET_MS = 1e9
        M.SIM_MAX_UNITS_PER_FRAME = 10 ** 9
        self.app.sim_playing = True
        t0 = time.perf_counter()
        try:
            while (float(sim['t']) < hours
                   and len(sim['tcs']) < tcs_target
                   and time.perf_counter() - t0 < hard_s):
                self.app._sim_advance(5.0)
        finally:
            M.SIM_FRAME_BUDGET_MS, M.SIM_MAX_UNITS_PER_FRAME = _b0, _u0
        sim['params']['forecast'] = True
        sim['lastEnsH'] = -1e9
        sim['ensemble'] = None
        self.warm_s = time.perf_counter() - t0
        return self.tc_info()

    def tc_info(self):
        sim = self.app.sim_v4
        alive = [x for x in sim['tcs'] if not x['dead']]
        return {'t_h': float(sim['t']), 'tcs': len(sim['tcs']),
                'alive': len(alive),
                'trackpts': sum(len(x['track']) for x in sim['tcs'])}

    def cell(self, tier, layers, frames, dt, cell_max_s, label='', playing=True,
             dt_mode='real', cap=60):
        """跑 frames 帧真实主循环, 返回逐帧统计。

        playing=False → 冻结物理(`_sim_update` 不调 `_sim_advance`), 得到**纯渲染**帧。
        dt_mode='real' → 完全照抄 main.py:143: dt=min(clock.tick(cap)/1000, 0.1),
        即"上一帧真实耗时(上限 0.1s)", 并模拟 clock.tick 的限帧等待。这是**忠实**
        口径 —— 固定 dt 会漏掉"卡顿后 dt 被钳到 0.1s"对显示钟与测速窗口的影响。
        """
        app, screen = self.app, self.screen
        self.set_tier(tier)
        app.sim_layers = set(layers)
        app.sim_layer = sorted(layers)[0] if layers else 'track'
        app.sim_playing = bool(playing)
        frame_budget = 1.0 / float(cap)

        def _next_dt(last):
            if dt_mode == 'fixed':
                return dt, last
            now = time.perf_counter()
            d = min(now - last, 0.1)          # main.py:143 的钳制
            return d, now

        # 预热丢弃: 让图层缓存/字体/路径缓存热起来
        last = time.perf_counter()
        for _ in range(5):
            d, last = _next_dt(last)
            app.update(d)
            app.draw(screen)
        app._sim_rate_reset()
        F, U, D = [], [], []      # frame / update / draw
        H, B, DISP, DTS = [], [], [], []   # adv h / budget ms / disp dt / real dt
        n_step = 0
        t_wall0 = time.perf_counter()
        last = time.perf_counter()
        for i in range(frames):
            self._rec['h'] = 0.0
            d0 = float(app._sim_disp_h)
            d_use, last = _next_dt(last)
            t0 = time.perf_counter()
            app.update(d_use)
            t1 = time.perf_counter()
            app.draw(screen)
            pygame.display.flip()
            t2 = time.perf_counter()
            F.append((t2 - t0) * 1000.0)
            U.append((t1 - t0) * 1000.0)
            D.append((t2 - t1) * 1000.0)
            h = float(self._rec['h'])
            H.append(h)
            if h > 0:
                n_step += 1
            B.append(float(getattr(app, '_sim_last_budget_ms', 0.0)))
            DISP.append(float(app._sim_disp_h) - d0)
            DTS.append(d_use * 1000.0)
            # 模拟 pygame.time.Clock.tick(cap) 的限帧等待(卡顿时不等待; fixed 模式不等待)
            if dt_mode == 'real':
                _slp = frame_budget - (t2 - t0)
                if _slp > 0:
                    time.sleep(_slp)
            if time.perf_counter() - t_wall0 > cell_max_s:
                break
        sim = app.sim_v4
        block = [b for b, h in zip(B, H) if h > 0]
        wall = time.perf_counter() - t_wall0
        out = {
            'label': label or ('%s|%s' % (tier, ','.join(sorted(layers)) or 'none')),
            'tier': tier, 'layers': sorted(layers),
            'frames': len(F), 'dt_ms': dt * 1000.0, 'dt_mode': dt_mode,
            'real_dt_p50_ms': pct(DTS, 50), 'real_dt_max_ms': max(DTS),
            'speed': float(app.sp),
            'tier_target_ms': float(app._sim_tier_target_ms()),
            'panel_step_cost_ms': float(sim.get('stepCostMs', 0.0) or 0.0),
            'panel_cost_warn': bool(app._sim_cost_warn()),
            'frame': stats(F), 'update': stats(U), 'draw': stats(D),
            'physics_block_ms': stats(block),
            'over16_7_pct': over(F, 16.7), 'over33_pct': over(F, 33.0),
            'n_physics_frames': n_step,
            'pct_wall_blocked': 100.0 * sum(block) / max(1e-9, sum(F)),
            'max_block_ms': (max(block) if block else 0.0),
            'frames_per_block': len(F) / max(1, n_step),
            'pct_frames_no_advance': 100.0 * sum(1 for h in H if h <= 0.0) / max(1, len(H)),
            'pct_frames_disp_frozen': 100.0 * sum(1 for dd in DISP
                                                 if abs(dd) < 1e-9) / max(1, len(DISP)),
            'sim_hours_advanced': sum(H),
            'wall_s': wall,
            'sum_dt_s': sum(DTS) / 1000.0,
            'panel_rate_h': float(getattr(app, '_sim_rate_h', -1.0)),
            'panel_rate_s': float(getattr(app, '_sim_rate_s', -1.0)),
            'panel_rate_hps': float(getattr(app, '_sim_rate_hps', -1.0)),
            'panel_rate_hps_raw': (float(getattr(app, '_sim_rate_h', 0.0))
                                   / max(1e-9, float(getattr(app, '_sim_rate_s', 0.0)))),
            'wall_effective_x': (sum(H) / 6.0) / max(1e-9, wall),
            'eff_x': float(app._sim_effective_x()),
            'rate_text': str(app._sim_rate_text()),
            'req_x': float(app.sp),
            'tc': self.tc_info(),
        }
        return out


def _fmt_cell(c):
    f, u, d, b = c['frame'], c['update'], c['draw'], c['physics_block_ms']
    return ('%-34s n=%3d dtMode=%s realdt p50 %.1f max %.1f | '
            'FRAME p50 %7.1f p90 %7.1f p99 %8.1f max %9.1f | '
            'UPD p50 %7.1f p90 %7.1f | DRAW p50 %6.1f p90 %7.1f | '
            '\n    >16.7ms %5.1f%%  >33ms %5.1f%% | noAdv %5.1f%% dispFroz %5.1f%% | '
            'blocks=%d medBlock %s maxBlock %.0f wallBlocked %5.1f%% 1blk/%4.1ff | '
            'tierTgt %6.1f panelCost %8.1f warn=%s | '
            'req %gx panelEff %.2fx wallEff %.3fx "%s" | tcs %d alive %d'
            % (c['label'], c['frames'], c.get('dt_mode', '?'),
               c.get('real_dt_p50_ms', float('nan')),
               c.get('real_dt_max_ms', float('nan')),
               f['p50'], f['p90'], f['p99'], f['max'],
               u['p50'], u['p90'], d['p50'], d['p90'],
               c['over16_7_pct'], c['over33_pct'],
               c['pct_frames_no_advance'], c['pct_frames_disp_frozen'],
               c['n_physics_frames'],
               ('%.0f' % b['p50']) if b.get('n') else '-',
               c['max_block_ms'], c['pct_wall_blocked'], c['frames_per_block'],
               c['tier_target_ms'], c['panel_step_cost_ms'], c['panel_cost_warn'],
               c['req_x'], c['eff_x'], c.get('wall_effective_x', float('nan')),
               c['rate_text'],
               c['tc']['tcs'], c['tc']['alive']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phases', default='pacing,render,switch')
    ap.add_argument('--tiers', default='400ms,100ms,50ms,800ms,2s')
    ap.add_argument('--layer-sets', default='track;track,pressure,wind;all')
    ap.add_argument('--frames', type=int, default=240)
    ap.add_argument('--dt', type=float, default=1.0 / 60.0,
                    help='--dt-mode fixed 时的固定步长')
    ap.add_argument('--dt-mode', default='real', choices=('real', 'fixed'),
                    help='real = 忠实照抄 main.py:143 (dt=min(真实耗时,0.1))')
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--warm-tcs', type=int, default=8)
    ap.add_argument('--warm-hours', type=float, default=4000.0)
    ap.add_argument('--warm-tier', default='100ms')
    ap.add_argument('--warm-forecast', action='store_true',
                    help='预热时也开着集合(很慢, 默认关)')
    ap.add_argument('--warm-max-s', type=float, default=600.0)
    ap.add_argument('--cell-max-s', type=float, default=120.0)
    ap.add_argument('--reps', type=int, default=2)
    ap.add_argument('--size', default='2560x1498')
    ap.add_argument('--json', default='')
    ap.add_argument('--combined-tier', default='400ms')
    ap.add_argument('--measure-forecast', default='on', choices=('on', 'off'),
                    help='测量阶段是否开着"实时集合预报"(sim.params.forecast, '
                         '默认 True 且面板无开关)')
    a = ap.parse_args()

    size = tuple(int(x) for x in a.size.lower().split('x'))
    H = Harness(size, constants.CONFIG_FILE)
    print('BOOT sec=%.2f size=%dx%d' % (H.boot_s, size[0], size[1]))
    info = H.warm(a.warm_hours, a.warm_tier, a.warm_tcs,
                  forecast=a.warm_forecast, hard_s=a.warm_max_s)
    H.app.sp = a.speed
    cap = int(getattr(H.cfg, 'fps_cap', 60) or 60)
    print('WARM tier=%s sec=%.1f %s'
          % (a.warm_tier, H.warm_s, json.dumps(info)))
    print('dt_mode=%s cap=%d' % (a.dt_mode, cap))
    sys.stdout.flush()

    phases = [p.strip() for p in a.phases.split(',') if p.strip()]
    layer_sets = [parse_layer_set(s) for s in a.layer_sets.split(';')]
    H.app.sim_v4.setdefault('params', {})['forecast'] = (a.measure_forecast == 'on')
    results = {'boot_s': H.boot_s, 'warm': {'tier': a.warm_tier,
                                            'sec': H.warm_s, **info},
               'dt_ms': a.dt * 1000.0, 'dt_mode': a.dt_mode, 'fps_cap': cap,
               'measure_forecast': a.measure_forecast,
               'speed': a.speed,
               'size': list(size), 'cells': [], 'switch': [], 'meta': {}}

    if 'pacing' in phases:
        print('\n=== PHASE pacing: 逐档位 x 帧时长 (物理+渲染, 真实播放) ===')
        for tier in [t.strip() for t in a.tiers.split(',') if t.strip()]:
            for rep in range(a.reps):
                c = H.cell(tier, {'track'}, a.frames, a.dt, a.cell_max_s,
                           label='pacing %s rep%d' % (tier, rep + 1),
                           dt_mode=a.dt_mode, cap=cap)
                c['phase'] = 'pacing'
                c['rep'] = rep + 1
                results['cells'].append(c)
                print(_fmt_cell(c))
                sys.stdout.flush()

    if 'render' in phases:
        print('\n=== PHASE render: 暂停物理, 纯渲染 x 图层组合 ===')
        H.app.sim_playing = False
        for ls in layer_sets:
            for rep in range(a.reps):
                c = H.cell(a.combined_tier, ls, a.frames, a.dt, a.cell_max_s,
                           label='render %s rep%d' % (','.join(sorted(ls)) or 'none',
                                                      rep + 1),
                           playing=False, dt_mode='fixed')
                c['phase'] = 'render'
                c['rep'] = rep + 1
                results['cells'].append(c)
                print(_fmt_cell(c))
                sys.stdout.flush()

    if 'switch' in phases:
        print('\n=== PHASE switch: 逐图层打开那一帧的开销(暂停物理) ===')
        app, screen = H.app, H.screen
        app.sim_playing = False
        app.sim_layers = {'track'}
        for _ in range(8):
            app.update(a.dt)
            app.draw(screen)
        base_warm = []
        for _ in range(a.frames):
            t0 = time.perf_counter()
            app.update(a.dt)
            app.draw(screen)
            base_warm.append((time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        app._sim_toggle_layer('track')
        call_ms = (time.perf_counter() - t0) * 1000.0
        app.sim_layers = {'track'}
        print('base=track  warm p50=%.1f p90=%.1f | _sim_toggle_layer() 调用本体 %.3f ms'
              % (pct(base_warm, 50), pct(base_warm, 90), call_ms))
        results['switch'].append({'layer': '(base)track', 'call_ms': call_ms,
                                  'cold_ms': None, 'warm_p50': pct(base_warm, 50),
                                  'warm_p90': pct(base_warm, 90)})
        for lid in ALL_LAYERS:
            if lid == 'track':
                continue
            app.sim_layers = {'track'}
            for _ in range(3):
                app.update(a.dt)
                app.draw(screen)
            t0 = time.perf_counter()
            app._sim_toggle_layer(lid)
            call_ms = (time.perf_counter() - t0) * 1000.0
            # 刚打开那一帧(冷缓存)
            t0 = time.perf_counter()
            app.update(a.dt)
            app.draw(screen)
            cold = (time.perf_counter() - t0) * 1000.0
            warm = []
            for _ in range(24):
                t0 = time.perf_counter()
                app.update(a.dt)
                app.draw(screen)
                warm.append((time.perf_counter() - t0) * 1000.0)
            rec = {'layer': lid, 'call_ms': call_ms, 'cold_ms': cold,
                   'warm_p50': pct(warm, 50), 'warm_p90': pct(warm, 90),
                   'n_layers_now': len(app.sim_layers)}
            results['switch'].append(rec)
            print('  +%-9s toggle_call %6.3f ms | 打开那一帧 %8.1f ms | '
                  '稳态 p50 %7.1f p90 %7.1f (层数 %d)'
                  % (lid, call_ms, cold, rec['warm_p50'], rec['warm_p90'],
                     rec['n_layers_now']))
            sys.stdout.flush()
        # 一次性全开
        app.sim_layers = {'track'}
        for _ in range(3):
            app.update(a.dt)
            app.draw(screen)
        t0 = time.perf_counter()
        app.sim_layers = set(ALL_LAYERS)
        app.sim_layer = 'track'
        set_ms = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        app.update(a.dt)
        app.draw(screen)
        cold_all = (time.perf_counter() - t0) * 1000.0
        warm_all = []
        for _ in range(a.frames):
            t0 = time.perf_counter()
            app.update(a.dt)
            app.draw(screen)
            warm_all.append((time.perf_counter() - t0) * 1000.0)
        print('  ALL9 set_layers本体 %.3f ms | 第一帧 %8.1f ms | 稳态 p50 %.1f p90 %.1f'
              % (set_ms, cold_all, pct(warm_all, 50), pct(warm_all, 90)))
        results['switch'].append({'layer': 'ALL9', 'call_ms': set_ms,
                                  'cold_ms': cold_all,
                                  'warm_p50': pct(warm_all, 50),
                                  'warm_p90': pct(warm_all, 90)})

    if 'combined' in phases:
        print('\n=== PHASE combined: 全 9 图层 + 物理(最坏组合) ===')
        for rep in range(a.reps):
            c = H.cell(a.combined_tier, set(ALL_LAYERS), a.frames, a.dt,
                       a.cell_max_s, label='combined %s all9 rep%d'
                       % (a.combined_tier, rep + 1),
                       dt_mode=a.dt_mode, cap=cap)
            c['phase'] = 'combined'
            c['rep'] = rep + 1
            results['cells'].append(c)
            print(_fmt_cell(c))
            sys.stdout.flush()

    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print('\nJSON -> %s' % a.json)
    print('\nRESULT ' + json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
