# -*- coding: utf-8 -*-
"""轮次 160: 拆开 `build_env_fields` 的每步成本 —— 到底贵在 `_EM.solve` 还是
7 次 `interp_month`(还是 `load_*` 的缓存没命中)。

只做计时, 不改任何东西。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V          # noqa: E402
from simulator.simcore import climate as CL      # noqa: E402
from simulator.simcore import envmodel as EM     # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
P = sim['params']
clk = V.clock(sim)
t_h = float(sim['t'])
m0 = sim['month0']


def bench(fn, n=10, label=''):
    fn()                                          # 预热/填缓存
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n * 1000.0
    print('%-34s %9.2f ms/次' % (label, dt))
    return dt


print('=== build_env_fields 总量 ===')
bench(lambda: CL.build_env_fields(P, t_h, m0, clock=clk), 10,
      'build_env_fields(整体)')

print()
print('=== 拆件 ===')
bench(lambda: EM.solve(P, clock=clk), 20, '_EM.solve')

for var in ('sst', 'ohc', 'shear', 'rh700', 'mslp'):
    bench(lambda v=var: CL.load_months_env(v, P.get('dataYear'),
                                          bool(P.get('dataClim', False)) or
                                          P.get('dataYear') is None),
          50, 'load_months_env(%s)  [应已缓存]' % var)

_m = CL.load_months_env('sst', P.get('dataYear'), True)
bench(lambda: CL.interp_month(_m, t_h, m0, 1.0, 0.0, clock=clk), 50,
      'interp_month(sst) 一次')

print()
print('=== load_raw_sst_months / resample_env ===')
bench(lambda: CL.load_raw_sst_months(int(P.get('dataYear') or 2024)), 20,
      'load_raw_sst_months')
_raw = CL.load_raw_sst_months(int(P.get('dataYear') or 2024))
if _raw is not None:
    bench(lambda: CL.resample_env(_raw), 10, 'resample_env(raw_sst)')
else:
    print('resample_env                          (无 raw sst, 跳过)')

print()
print('=== 缓存规模 ===')
print('_env_cache 条目数 =', len(getattr(CL, '_env_cache', {})))
print('entries =', list(getattr(CL, '_env_cache', {}).keys())[:12])
