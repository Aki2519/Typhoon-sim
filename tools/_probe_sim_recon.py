# -*- coding: utf-8 -*-
"""侦察: 模拟启动成本 / 自然生成速率 / 快进到 N 小时的墙钟代价。

只测量, 不修改任何被监视文件。无头(dummy SDL)。单进程。
用法: PYTHONIOENCODING=utf-8 python tools/_probe_sim_recon.py [--tier 400ms] [--hours 1200]
"""
import argparse
import os
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
import app.ty_sim_mixins.sim_mode_mixin as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='400ms')
    ap.add_argument('--hours', type=float, default=1200.0)
    ap.add_argument('--chunk', type=float, default=100.0)
    ap.add_argument('--size', default='2560x1498')
    a = ap.parse_args()

    sw, sh = [int(x) for x in a.size.lower().split('x')]
    cfg = AppConfig.load(constants.CONFIG_FILE)
    constants.SW, constants.SH = sw, sh
    constants.MH = sh - constants.CPH

    t0 = time.perf_counter()
    screen = pygame.display.set_mode((sw, sh))
    app = TySim(screen, cfg=cfg)
    t_boot = time.perf_counter() - t0

    app.md = app.MODE_SIM
    cfg.sim_precision = a.tier
    t1 = time.perf_counter()
    app._sim_reset(msg=False)
    t_reset = time.perf_counter() - t1

    sim = app.sim_v4
    print('BOOT  boot_s=%.2f reset_s=%.2f size=%dx%d tier=%s envStep=%g h '
          'tcs0=%d t0=%g'
          % (t_boot, t_reset, sw, sh, a.tier, app.sim_env_step_h,
             len(sim['tcs']), float(sim['t'])))

    app.sim_playing = True
    # 快进: 临时放开单帧预算(只是让状态快进, 不参与统计)
    M.SIM_FRAME_BUDGET_MS = 1e9
    M.SIM_MAX_UNITS_PER_FRAME = 10 ** 9

    t_ff0 = time.perf_counter()
    nxt = a.chunk
    while float(sim['t']) < a.hours:
        app._sim_advance(5.0)
        t = float(sim['t'])
        if t >= nxt:
            alive = len([x for x in sim['tcs'] if not x['dead']])
            el = time.perf_counter() - t_ff0
            print('FF  t=%7.1f h  tcs=%3d alive=%3d  wall=%.1f s  '
                  '(%.1f h/s sim)  stepCostMs=%.0f  disp=%g'
                  % (t, len(sim['tcs']), alive, el, t / max(1e-9, el),
                     float(sim.get('stepCostMs', 0.0)), float(app._sim_disp_h)))
            sys.stdout.flush()
            nxt += a.chunk
    print('DONE hours=%.1f wall=%.1f s tcs=%d'
          % (float(sim['t']), time.perf_counter() - t_ff0, len(sim['tcs'])))


if __name__ == '__main__':
    main()
