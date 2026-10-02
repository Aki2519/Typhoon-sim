# -*- coding: utf-8 -*-
"""轮次 160 决定性实验: `envmodel.solve` 的输出到底以什么时间粒度变化?

若是"按天"变(而非按小时), 就能以 (参数, 日期) 为键做**逐位安全**的记忆化,
6h 步长下一天 4 步共享同一结果 ⇒ 命中率 75%。

做法: 固定参数, 取若干时刻调 solve, 两两比较每个字段是否逐位相同。
"""
import datetime as _dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                    # noqa: E402
from simulator.simcore.envmodel import solver as S         # noqa: E402
from simulator.simcore.envmodel import store as ST         # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
P = sim['params']
print('base_available =', S.base_available())
print('产物集合(前 12) =', sorted(ST.available())[:12])


def at(y, m, d, h):
    return _dt.datetime(y, m, d, h, 0)


def fields(dt):
    S.clear_caches() if hasattr(S, 'clear_caches') else None
    o = S.solve(P, clock=dt)
    return o or {}


def same(a, b):
    """返回 (是否完全相同, 不同的字段名列表)"""
    if set(a) != set(b):
        return False, ['键集不同: %s' % (set(a) ^ set(b))]
    diff = []
    for k in sorted(a):
        x, y = a[k], b[k]
        if isinstance(x, np.ndarray):
            if x.shape != y.shape or not np.array_equal(x, y):
                diff.append(k)
        elif x != y:
            diff.append(k)
    return (not diff), diff


base = fields(at(2025, 8, 15, 0))
print('字段数 =', len(base), ' 键 =', sorted(base)[:16])

print()
print('=== 同一天内, 只改小时 ===')
for h in (6, 12, 18):
    ok, diff = same(base, fields(at(2025, 8, 15, h)))
    print('  08-15 %02d时 vs 00时: 逐位相同=%-5s  差异字段=%s'
          % (h, ok, diff if diff else '无'))

print()
print('=== 同月内, 只改天 ===')
for d in (16, 20, 28):
    ok, diff = same(base, fields(at(2025, 8, d, 0)))
    print('  08-%02d 00时 vs 08-15: 逐位相同=%-5s  差异字段数=%d %s'
          % (d, ok, len(diff), diff[:6] if diff else ''))

print()
print('=== 跨月/跨年 ===')
for (y, m, d) in ((2025, 9, 15), (2025, 8, 15), (2024, 8, 15)):
    ok, diff = same(base, fields(at(y, m, d, 0)))
    tag = '跨年' if y != 2025 else '跨月'
    print('  %s %04d-%02d-%02d vs 2025-08-15: 相同=%-5s 差异字段数=%d %s'
          % (tag, y, m, d, ok, len(diff), diff[:6] if diff else ''))

print()
print('=== 缓存状态 ===')
print('store._cache 条目 =', len(ST._cache))
print('solver._up_cache 条目 =', len(getattr(S, '_up_cache', {})))
print('solver._warm_cache 条目 =', len(getattr(S, '_warm_cache', {})))
