# -*- coding: utf-8 -*-
"""轮次 160: 用**调用次数**(而非时间)证明 `solve` 记忆化消除了多少冗余功。

宿主争用让任何计时都不可信(实测同进程 A/A 极差 34.5%, 同配置单次成本从 113 波动
到 180 ms)。调用次数是**计数**, 不受降频/争用影响 —— 它直接回答
"这一步本来要算 17 个基态场, 现在算了几个"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                          # noqa: E402
from simulator.simcore.envmodel import solver as S               # noqa: E402

STEPS = 20


def run(memo_on):
    from simulator.simcore import core as _V
    counts = {}

    def wrap(mod, name):
        fn = getattr(mod, name)

        def inner(*a, **k):
            counts[name] = counts.get(name, 0) + 1
            return fn(*a, **k)
        setattr(mod, name, inner)

    saved = {}
    for nm in ('_base_field', '_mode_anomaly', '_regime_anomaly',
               '_warming_field'):
        saved[nm] = getattr(S, nm)
        wrap(S, nm)
    orig_unc = S._solve_uncached
    solved = {'n': 0}

    def _unc(*a, **k):
        solved['n'] += 1
        return orig_unc(*a, **k)
    S._solve_uncached = _unc
    saved_solve = S.solve
    # ⚠⚠ 必须**同时**打 `solver.solve` 与 `envmodel.solve`: `envmodel/__init__.py` 是
    # `from .solver import solve`, 那是**另一个绑定**。只打 solver 那处, climate.py 走的
    # `_EM.solve` 仍然是记忆化版 ⇒ 两臂计数完全相同(实测踩过, 靠"计数一样"才发现)。
    from simulator.simcore import envmodel as _EM
    saved_em_solve = _EM.solve
    if not memo_on:
        _byp = lambda P, clock=None: S._solve_uncached(P, clock)  # noqa: E731
        S.solve = _byp
        _EM.solve = _byp
    try:
        S.clear_solve_memo()
        sim = _V.init({'start': '2025-08-01 00', 'seed': 12345,
                       'params': {'dataMode': True, 'genesis': True,
                                  'forecast': False}})
        dt = _V.step_hours(sim)
        for _ in range(STEPS):
            _V.sim_step(sim, dt)
        n_env = int(sim.get('_envVer', 0))
        return solved['n'], counts, n_env, len(S._SOLVE_MEMO)
    finally:
        for nm, fn in saved.items():
            setattr(S, nm, fn)
        S._solve_uncached = orig_unc
        S.solve = saved_solve
        _EM.solve = saved_em_solve


print('跑 %d 步(6h/步 = %.1f 天), 数据模式' % (STEPS, STEPS * 6 / 24))
print()
res = {}
for tag, on in (('旁路(每次真算)', False), ('有记忆化', True)):
    n, counts, n_env, memo = run(on)
    res[tag] = (n, counts, n_env)
    print('--- %s ---' % tag)
    print('  env_update 次数(_envVer)      = %d' % n_env)
    print('  solve 真算次数(_solve_uncached)= %d' % n)
    print('  _base_field 调用              = %d' % counts.get('_base_field', 0))
    print('  _mode_anomaly 调用            = %d' % counts.get('_mode_anomaly', 0))
    print('  _regime_anomaly 调用          = %d' % counts.get('_regime_anomaly', 0))
    print('  _warming_field 调用           = %d' % counts.get('_warming_field', 0))
    print('  _SOLVE_MEMO 条目              = %d' % memo)
    print()

a = res['旁路(每次真算)']
b = res['有记忆化']
print('=== 省掉的冗余功 ===')
for nm, ka, kb in (('solve 真算', a[0], b[0]),
                   ('_base_field', a[1].get('_base_field', 0),
                    b[1].get('_base_field', 0))):
    if ka:
        print('  %-14s %5d -> %4d  (省 %.1f%%)'
              % (nm, ka, kb, (1 - kb / ka) * 100.0))
