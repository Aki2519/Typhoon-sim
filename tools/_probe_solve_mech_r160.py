# -*- coding: utf-8 -*-
"""轮次 160: `solve` 按天记忆化的**机制证据**(命中率 + 单次成本), 同进程交错。

整步 A/B 受宿主争用影响太大(实测 A/A 极差 34.5%)。这里改为量**机制**:
  · `_solve_uncached` 的实际调用次数(记忆化后应从 ~每步一次降到 ~每天一次);
  · `build_env_fields` 单次成本的交错 A/B/A/B。
两者都在**同一进程内**、紧邻着测, 争用对两臂的影响大致相同。
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                            # noqa: E402
from simulator.simcore import climate as CL                        # noqa: E402
from simulator.simcore.envmodel import solver as S                 # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
P = sim['params']

# 1) 命中率: 统计一次仿真的真实调用次数
calls = {'n': 0}
_orig = S._solve_uncached


def _counted(*a, **k):
    calls['n'] += 1
    return _orig(*a, **k)


S._solve_uncached = _counted
S.clear_solve_memo()
dt = V.step_hours(sim)
STEPS = 8
env_calls = 0
for _ in range(STEPS):
    V.sim_step(sim, dt)
S._solve_uncached = _orig

# 数一下 env_update 被调了几次(用 _envVer 的增量)
print('=== 机制 ===')
print('跑 %d 步(每步 %.1fh): `_solve_uncached` 实际执行 %d 次'
      % (STEPS, dt, calls['n']))
print('  => 每天(4 步)应执行约 1 次; 命中率 = 1 - 执行次数/调用次数')
print('  _SOLVE_MEMO 当前条目 =', len(S._SOLVE_MEMO))

# 2) build_env_fields 单次成本: 交错 A/B
def bench_be(memo_on, n=6):
    saved = S.solve
    if not memo_on:
        S.solve = lambda Pp, clock=None: S._solve_uncached(Pp, clock)
    try:
        S.clear_solve_memo()
        clk = V.clock(sim)
        # 走 4 个不同"天"再回到起点, 保证两臂都经历同样的 miss/hit 组合
        t0 = time.perf_counter()
        for i in range(n):
            for d in range(4):
                clock = clk.replace(day=min(28, 10 + d))
                CL.build_env_fields(P, float(d), sim['month0'], clock=clock)
        return (time.perf_counter() - t0) / (n * 4) * 1000.0
    finally:
        S.solve = saved


print()
print('=== build_env_fields 单次成本(交错) ===')
on, off = [], []
for r in range(3):
    a = bench_be(True)
    b = bench_be(False)
    on.append(a)
    off.append(b)
    print('  轮%d: 有记忆化 %7.2f ms   旁路 %7.2f ms' % (r + 1, a, b))
print('  中位: 有 %.2f  旁路 %.2f  => %.2fx' %
      (statistics.median(on), statistics.median(off),
       statistics.median(off) / statistics.median(on)))
print('  min : 有 %.2f  旁路 %.2f  => %.2fx' %
      (min(on), min(off), min(off) / min(on)))
