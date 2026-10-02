# -*- coding: utf-8 -*-
"""轮次 160: 拆 `envmodel.solve` 的 72 ms/步 到底花在哪。

solve() 每步要算 ~14 个基态场 + 增暖 + 模态 + 环流型异常, 每个都是全网格。
逐个子件计时, 找真正的热点。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                       # noqa: E402
from simulator.simcore.envmodel import solver as S            # noqa: E402
from simulator.simcore.envmodel import store as ST            # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
P = sim['params']
clk = V.clock(sim)


def bench(fn, n=20, label=''):
    try:
        fn()
    except Exception as e:
        print('%-46s  ERROR %s: %s' % (label, type(e).__name__, e))
        return None
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n * 1000.0
    print('%-46s %9.3f ms/次' % (label, dt))
    return dt


print('=== store 层 ===')
bench(lambda: ST.available(), 200, 'store.available()')
bench(lambda: ST.load('base_sst'), 500, 'store.load(base_sst)  [应已缓存]')

print()
print('=== solve 的子件 ===')
bench(lambda: S._instant_fields(clk), 50, '_instant_fields(clock)')
bench(lambda: S._month_weight(clk), 200, '_month_weight(clock)')
for nm in ('sst', 'd26', 'shear', 'u850', 'mslp'):
    bench(lambda n=nm: S._base_field(n, clk), 50, '_base_field(%s)' % nm)
for nm in ('sst', 'd26', 'shear'):
    bench(lambda n=nm: S._warming_field(n, clk, (121, 360)), 30,
          '_warming_field(%s)' % nm)
    bench(lambda n=nm: S._mode_anomaly(n, clk, (121, 360)), 30,
          '_mode_anomaly(%s)' % nm)
for nm in ('sst', 'shear', 'u850', 'mslp'):
    bench(lambda n=nm: S._regime_anomaly(n, clk, (121, 360)), 30,
          '_regime_anomaly(%s)' % nm)

print()
print('=== solve 整体 ===')
bench(lambda: S.solve(P, clock=clk), 15, 'solve(整体)')

print()
print('=== 各子件被调用次数(一次 solve 内) ===')
cnt = {}


class _Count:
    def __init__(self, obj, name):
        self.obj, self.name = obj, name

    def __call__(self, *a, **k):
        cnt[self.name] = cnt.get(self.name, 0) + 1
        return self.obj(*a, **k)


for nm in ('_base_field', '_warming_field', '_mode_anomaly', '_regime_anomaly',
           '_instant_fields'):
    setattr(S, nm, _Count(getattr(S, nm), nm))
S.solve(P, clock=clk)
for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
    print('  %-22s %3d 次/solve' % (k, v))
