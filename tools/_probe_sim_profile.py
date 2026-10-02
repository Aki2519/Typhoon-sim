# -*- coding: utf-8 -*-
"""定位模拟单步成本的构成: 集合预报(默认开) vs 环境重建 vs 每台风。

只读测量, 不改被监视文件。单进程。dummy SDL。
用法: PYTHONIOENCODING=utf-8 python tools/_probe_sim_profile.py [--tier 400ms] [--hours 250]
"""
import argparse
import cProfile
import io
import os
import pstats
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
import app.ty_sim_mixins.sim_mode_mixin as M  # noqa: E402


def full_step_ms(sim, reps=1):
    """跑完整 6h 一步的墙钟 ms(2s 档会分多个内步, 全部跑完)。"""
    out = []
    for _ in range(reps):
        t0 = time.perf_counter()
        while True:
            if V.sim_step(sim, 6.0, max_inner=1):
                break
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='400ms')
    ap.add_argument('--hours', type=float, default=250.0)
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--warm-tier', default='100ms')
    a = ap.parse_args()

    cfg = AppConfig.load(constants.CONFIG_FILE)
    constants.SW, constants.SH = 2560, 1498
    constants.MH = constants.SH - constants.CPH
    screen = pygame.display.set_mode((2560, 1498))
    app = TySim(screen, cfg=cfg)
    app.md = app.MODE_SIM
    cfg.sim_precision = a.warm_tier
    app._sim_reset(msg=False)
    sim = app.sim_v4

    # 快进(集合关掉, 它只是诊断, 不影响生成/路径)
    M.SIM_FRAME_BUDGET_MS = 1e9
    M.SIM_MAX_UNITS_PER_FRAME = 10 ** 9
    sim['params']['forecast'] = False
    app.sim_playing = True
    t0 = time.perf_counter()
    while float(sim['t']) < a.hours:
        app._sim_advance(5.0)
    warm_s = time.perf_counter() - t0
    alive = len([x for x in sim['tcs'] if not x['dead']])
    trk = sum(len(x['track']) for x in sim['tcs'])
    print('WARM tier=%s warm_s=%.1f t=%.0f h tcs=%d alive=%d trackpts=%d'
          % (a.warm_tier, warm_s, float(sim['t']), len(sim['tcs']), alive, trk))
    sys.stdout.flush()

    for tier in (a.tier,):
        sim['params']['precision'] = tier
        sim.pop('_pendStep', None)
        sim['stepCostMs'] = 0.0
        # ① 集合关闭
        sim['params']['forecast'] = False
        off = full_step_ms(sim, a.reps)
        # ② 集合打开
        sim['params']['forecast'] = True
        sim['lastEnsH'] = -1e9
        sim['ensemble'] = None
        on = full_step_ms(sim, a.reps)
        print('STEP tier=%s t=%.0f h  forecast_off_ms=%s median=%.0f | '
              'forecast_on_ms=%s median=%.0f | delta_median=%.0f'
              % (tier, float(sim['t']), ['%.0f' % x for x in off],
                 statistics.median(off), ['%.0f' % x for x in on],
                 statistics.median(on), statistics.median(on) - statistics.median(off)))
        sys.stdout.flush()

        # ③ 拆解: 一次 env_update / 一次 run_ensemble
        sim['params']['forecast'] = False
        t0 = time.perf_counter()
        V.env_update(sim)
        env_ms = (time.perf_counter() - t0) * 1000.0
        sim['lastEnsH'] = -1e9
        sim['ensemble'] = None
        t0 = time.perf_counter()
        V.run_ensemble(sim)
        ens_ms = (time.perf_counter() - t0) * 1000.0
        print('PART env_update_ms=%.0f  run_ensemble_ms=%.0f  '
              'n_alive=%d  stepCostMs=%.0f'
              % (env_ms, ens_ms, alive, float(sim.get('stepCostMs', 0.0))))
        sys.stdout.flush()

    # ④ profile 一步(集合开)
    sim['params']['forecast'] = True
    sim['lastEnsH'] = -1e9
    sim['ensemble'] = None
    pr = cProfile.Profile()
    pr.enable()
    while True:
        if V.sim_step(sim, 6.0, max_inner=1):
            break
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats('tottime').print_stats(18)
    print('--- PROFILE tottime ---')
    print(s.getvalue())


if __name__ == '__main__':
    main()
