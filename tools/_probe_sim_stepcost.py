# -*- coding: utf-8 -*-
"""单步(6 模拟小时)墙钟成本 vs 场上风暴数 —— 长尾卡顿的触发条件。

做法: 用 100ms 档快进(集合关掉, 它只是诊断量), 每到一个检查点把集合打开,
实测 2 个完整 6h 步的墙钟 ms, 再关掉继续快进。记录 t / 系统总数 / 存活数 /
集合耗时。只测量, 不改被监视文件。单进程。
用法: PYTHONIOENCODING=utf-8 python tools/_probe_sim_stepcost.py [--tier 400ms]
"""
import argparse
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
import app.ty_sim_mixins.sim_mode_mixin as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='400ms')
    ap.add_argument('--warm-tier', default='100ms')
    ap.add_argument('--checkpoints', default='60,150,240,330,420,510')
    ap.add_argument('--reps', type=int, default=2)
    ap.add_argument('--max-hours', type=float, default=560.0)
    a = ap.parse_args()
    cps = [float(x) for x in a.checkpoints.split(',')]

    cfg = AppConfig.load(constants.CONFIG_FILE)
    constants.SW, constants.SH = 2560, 1498
    constants.MH = constants.SH - constants.CPH
    screen = pygame.display.set_mode((2560, 1498))
    app = TySim(screen, cfg=cfg)
    app.md = app.MODE_SIM
    cfg.sim_precision = a.warm_tier
    app._sim_reset(msg=False)
    sim = app.sim_v4
    M.SIM_FRAME_BUDGET_MS = 1e9
    M.SIM_MAX_UNITS_PER_FRAME = 10 ** 9
    app.sim_playing = True
    sim['params']['forecast'] = False

    print('BOOT tier=%s warm_tier=%s' % (a.tier, a.warm_tier))
    print('%-8s %-5s %-6s %-8s %-10s %-10s %-12s %-10s'
          % ('t_h', 'tcs', 'alive', 'trkpts', 'step_ms#1', 'step_ms#2',
             'median_ms', 'ens_ms'))
    for cp in cps:
        while float(sim['t']) < cp and float(sim['t']) < a.max_hours:
            app._sim_advance(5.0)
        sim['params']['precision'] = a.tier
        sim.pop('_pendStep', None)
        sim['params']['forecast'] = True
        sim['lastEnsH'] = -1e9
        sim['ensemble'] = None
        times = []
        for _ in range(a.reps):
            t0 = time.perf_counter()
            while True:
                if V.sim_step(sim, 6.0, max_inner=1):
                    break
            times.append((time.perf_counter() - t0) * 1000.0)
        # 单独量一次集合签发(它每步都会重发)
        sim['lastEnsH'] = -1e9
        sim['ensemble'] = None
        t0 = time.perf_counter()
        V.run_ensemble(sim)
        ens = (time.perf_counter() - t0) * 1000.0
        alive = len([x for x in sim['tcs'] if not x['dead']])
        trk = sum(len(x['track']) for x in sim['tcs'])
        print('%-8.0f %-5d %-6d %-8d %-10.0f %-10.0f %-12.0f %-10.0f'
              % (float(sim['t']), len(sim['tcs']), alive, trk,
                 times[0], times[-1], statistics.median(times), ens))
        sys.stdout.flush()
        sim['params']['forecast'] = False


if __name__ == '__main__':
    main()
