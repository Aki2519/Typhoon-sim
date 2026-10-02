# -*- coding: utf-8 -*-
"""轮次 160: `envmodel.solve` 按天记忆化的**同进程交错 A/B** + A/A 稳定性闸门。

为什么必须交错: 本机有宿主争用, 跨时间对比不可信(轮次 159 实测 A/A 极差 75.6%)。
先跑 A/A 自检; 极差 <15% 才认 A/B 的数字, 否则只报区间。

旁路方式: 把 `solver.solve` 换成直接调 `_solve_uncached`(即"每次真算"), 语义完全
等价, 只是没有记忆化。两臂**数值必须逐位相同** —— 脚本顺带断言这一点。

用法:
    python tools/perf_ab_solve_r160.py 40 3 aa    # A/A 稳定性自检
    python tools/perf_ab_solve_r160.py 40 3 ab    # 交错 A/B
"""
import hashlib
import io
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def _run(steps, memo_on):
    """跑 steps 步, 返回 (每步毫秒, 状态指纹)。memo_on=False 时旁路记忆化。

    ⚠⚠ **必须同时打 `solver.solve` 与 `envmodel.solve`**: `envmodel/__init__.py`
    是 `from .solver import solve, clear_cache` —— 那是**另一个绑定**。
    只打 `solver.solve` 的话, `climate.build_env_fields` 走的 `_EM.solve` 仍是
    记忆化版 ⇒ **两臂其实都在用缓存**, 测出来的差全是噪声。
    (轮次 160 实测踩过: 两臂调用计数完全相同, 靠计数才发现。)
    """
    from simulator.simcore import core as V
    from simulator.simcore import envmodel as EM
    from simulator.simcore.envmodel import solver as S

    orig, orig_em = S.solve, EM.solve
    if not memo_on:
        def _no_memo(P, clock=None):
            return S._solve_uncached(P, clock)
        S.solve = _no_memo
        EM.solve = _no_memo
    try:
        sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
                      'params': {'dataMode': True, 'genesis': True,
                                 'forecast': False}})
        dt = V.step_hours(sim)
        h = hashlib.sha256()
        t0 = time.perf_counter()
        for _ in range(steps):
            V.sim_step(sim, dt)
            for tc in sorted(sim['tcs'], key=lambda q: q['id']):
                h.update(('%.7f %.7f %.5f %s|' % (
                    tc['lon'], tc['lat'], tc['vmax'], tc['phase'])).encode())
        ms = (time.perf_counter() - t0) * 1000.0 / steps
        return ms, h.hexdigest()[:16]
    finally:
        S.solve = orig
        EM.solve = orig_em


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    mode = (sys.argv[3] if len(sys.argv) > 3 else 'ab').lower()

    if mode == 'aa':
        vals, fps = [], []
        for i in range(rounds):
            a, fa = _run(steps, True)
            b, fb = _run(steps, True)
            vals += [a, b]
            fps += [fa, fb]
            print('轮%d(A/A): %.1f vs %.1f ms/步' % (i + 1, a, b))
        lo, hi = min(vals), max(vals)
        sp = (hi / lo - 1.0) * 100.0
        print('\nA/A 样本:', ['%.1f' % v for v in vals])
        print('min %.1f  max %.1f  极差 %.1f%%' % (lo, hi, sp))
        print('状态指纹种类 =', len(set(fps)), sorted(set(fps)))
        print('结论:', '稳定, 可做 A/B' if sp < 15 else
              '**不稳定** —— A/B 数字不可信, 只能报区间')
        return

    on, off, fp_on, fp_off = [], [], set(), set()
    for i in range(rounds):
        a, fa = _run(steps, True)
        b, fb = _run(steps, False)
        on.append(a)
        off.append(b)
        fp_on.add(fa)
        fp_off.add(fb)
        print('轮%d: 有记忆化 %.1f   旁路 %.1f ms/步' % (i + 1, a, b))
    print('\n有记忆化: min %.1f  中位 %.1f' % (min(on), statistics.median(on)))
    print('旁路    : min %.1f  中位 %.1f' % (min(off), statistics.median(off)))
    print('加速比(min)  = %.3fx' % (min(off) / min(on)))
    print('加速比(中位) = %.3fx' % (statistics.median(off) / statistics.median(on)))
    print('指纹 有记忆化=%s  旁路=%s  → 逐位相同: %s'
          % (sorted(fp_on), sorted(fp_off),
             fp_on == fp_off and len(fp_on) == 1))


if __name__ == '__main__':
    main()
