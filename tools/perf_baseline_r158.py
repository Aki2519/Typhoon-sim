# tools/perf_baseline_r158.py
"""模拟模式性能基线(轮次 158) ---- 优化前后必须用**同一个**测法复测。

用法:
    "/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe" tools/perf_baseline_r158.py [步数]

为什么单独做一个:
    轮次 158 的性能优化要求"有测量证据与前后对比"。如果每次用不同的测法,
    数字就没法比。这里固定住 种子/参数/精度/步数/预热, 只输出可比的量。

⚠ 内存纪律: 本机物理 15.74 GiB 但**可用常年只剩 1-3 GiB**、有 OOM 前科 ----
这个脚本**一次只跑一个进程**,
   不要并发跑多个; 步数默认 200(约 30-60 秒)。

输出(单行 JSON, 便于脚本比对):
    HEADLESS {"steps":200,"total_s":..,"per_step_ms":..,"steps_per_s":..}
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    from simulator.simcore import core as V

    # 固定: 种子 / 步长 / 精度 / 参数 ---- 与标定/验收同一套默认值
    sim = V.init({'seed': 20260801})
    dt = float(V.step_hours(sim))
    sim['params']['genesis'] = True

    # 预热(首次调用含查表/LUT 构建、land 载入等一次性成本, 不能算进每步)
    for _ in range(3):
        V.sim_step(sim, dt, max_inner=1)

    t0 = time.perf_counter()
    for _ in range(steps):
        V.sim_step(sim, dt, max_inner=1)
    t1 = time.perf_counter()

    total = t1 - t0
    tcs = sim.get('tcs') or []
    print("HEADLESS " + json.dumps({
        "steps": steps,
        "total_s": round(total, 3),
        "per_step_ms": round(total * 1000.0 / steps, 2),
        "steps_per_s": round(steps / total, 2) if total > 0 else None,
        "sim_hours": round(float(sim.get('t', 0.0)), 1),
        "n_tcs": len(tcs),
        "n_alive": sum(1 for t in tcs if not t.get('dead')),
        "step_h": dt,
    }, separators=(",", ":")))


if __name__ == '__main__':
    main()
