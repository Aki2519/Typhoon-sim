# tools/perf_profile_r158.py
"""用 cProfile 找出模拟模式每步的**真实**热点(轮次 158)。

与 tools/perf_baseline_r158.py 配套: 那个测总量, 这个找原因。

用法:
    python tools/perf_profile_r158.py [步数] [topN]

⚠ 内存纪律: 一次只跑一个进程。默认 15 步(按 1690ms/步 约 30 秒)。
"""
import cProfile
import io
import os
import pstats
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    topn = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    from simulator.simcore import core as V

    sim = V.init({'seed': 20260801})
    dt = float(V.step_hours(sim))
    for _ in range(3):                      # 预热(一次性成本不算)
        V.sim_step(sim, dt, max_inner=1)

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(steps):
        V.sim_step(sim, dt, max_inner=1)
    pr.disable()

    buf = io.StringIO()
    st = pstats.Stats(pr, stream=buf).sort_stats('tottime')
    st.print_stats(topn)
    out = buf.getvalue()
    # 只留表头 + 前 topN 行, 丢掉冗长的路径前缀噪音
    lines = [ln for ln in out.splitlines() if ln.strip()]
    for ln in lines[:12 + topn]:
        print(ln[:170])


if __name__ == '__main__':
    main()
