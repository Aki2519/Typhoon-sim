# -*- coding: utf-8 -*-
"""轮次 162: 用**函数级 monkeypatch 计时**归因 `9_steer_eddy`(91.68 ms/步, 32.3%)。

为什么不用行探针: 那要改 `core.py`(危险 + 锚点会漂)。改成**包装函数**——不改仓库
任何文件, 而且顺带给出**全局**归因(同一函数可能在别的段也被调)。

用法: python tools/_probe_funcs_r162.py [步数]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                          # noqa: E402
from simulator.simcore import synoptic as SY                     # noqa: E402

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20

# (显示名, 目标对象, 属性名)
TARGETS = [
    ('mesoscale_noise', V, 'mesoscale_noise'),
    ('mesoscale_eddies', V, 'mesoscale_eddies'),
    ('sh_ridge_lat', V, 'sh_ridge_lat'),
    ('_lowpass', V, '_lowpass'),
    ('synoptic_steer', V, 'synoptic_steer'),
    ('enso_update', V, 'enso_update'),
    ('mjo_field', V, 'mjo_field'),
    ('_SY.apply_to_env', SY, 'apply_to_env'),
    ('ridge_core', getattr(V, '_RG', None), 'ridge_core'),
    ('ocean_step', V, 'ocean_step'),
    ('_advect_env', V, '_advect_env'),
    ('ocean_column_init', V, 'ocean_column_init'),
    ('all_land_mask', V, 'all_land_mask'),
    ('wind_driven_currents', V, 'wind_driven_currents'),
    ('mixed_layer_clim', V, 'mixed_layer_clim'),
    ('sst_clim_cached', V, 'sst_clim_cached'),
    ('motion_step', V, 'motion_step'),
    ('intensity_step', V, 'intensity_step'),
    ('env_update', V, 'env_update'),
    ('d26_from_column', V, 'd26_from_column'),
]

stat = {}


def wrap(label, obj, attr):
    if obj is None or not hasattr(obj, attr):
        return None
    orig = getattr(obj, attr)
    rec = {'n': 0, 't': 0.0}
    stat[label] = rec

    def inner(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            rec['t'] += time.perf_counter() - t0
            rec['n'] += 1
    setattr(obj, attr, inner)
    return (obj, attr, orig)


saved = []
for label, obj, attr in TARGETS:
    s = wrap(label, obj, attr)
    if s:
        saved.append(s)

try:
    sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
                  'params': {'dataMode': True, 'genesis': True,
                             'forecast': False}})
    dt = V.step_hours(sim)
    V.env_update(sim)
    for k in stat:
        stat[k]['n'] = 0
        stat[k]['t'] = 0.0
    t0 = time.perf_counter()
    for _ in range(STEPS):
        V.sim_step(sim, dt)
    wall = (time.perf_counter() - t0) * 1000.0 / STEPS
finally:
    for obj, attr, orig in saved:
        setattr(obj, attr, orig)

rows = sorted(stat.items(), key=lambda kv: -kv[1]['t'])
print('steps = %d   整步墙钟 = %.1f ms/步' % (STEPS, wall))
print('%-20s %10s %8s %10s' % ('function', 'calls/step', 'ms/step', '% of step'))
acc = 0.0
for k, r in rows:
    ms = r['t'] / STEPS * 1000.0
    acc += ms
    print('%-20s %10.1f %8.2f %9.1f%%'
          % (k, r['n'] / STEPS, ms, ms / wall * 100.0))
print('%-20s %10s %8.2f %9.1f%%' % ('(已归因合计)', '', acc, acc / wall * 100.0))
print('%-20s %10s %8.2f %9.1f%%' % ('(未归因余量)', '', wall - acc,
                                    (wall - acc) / wall * 100.0))
