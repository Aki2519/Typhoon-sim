# -*- coding: utf-8 -*-
"""启动分阶段计时 —— 找出启动慢在哪 (轮次 160)。

用法:
    <解释器> tools/_probe_startup_timing.py [重复次数]        # 同进程重复(仅参考)
    <解释器> tools/_probe_startup_timing.py --one             # 单次, 机器可读行
    <解释器> tools/_probe_startup_timing.py --agg N           # N 个**独立进程**, 报 min/中位数

⚠ 为什么 `--agg` 才是可信的: import 在同一个进程里是缓存的, 第 2 次跑
  `import pygame/numpy/app.ty_sim` 全是 ~0 ms。要测 import 的真实离散度,
  必须**每个样本一个新进程**。

⚠ 轮次 160 修正的测量 bug: 旧版在 `TySim(...)` 之后又调了一次
  `sim.repo.load_typhoon_files()`。而 `TySim.__init__` 内部**已经加载过**
  (app/ty_sim.py:185) ⇒ 那一格把加载**算了两次**, 合计虚高约 0.4-1.9 s。
  生产路径 (main.py) 只加载一次, 所以这里也改成只测一次。
"""
import os
import statistics
import subprocess
import sys
import time

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: 启动阶段名(顺序即执行顺序)
STAGES_LABEL = (
    '1. import pygame',
    '2. import numpy',
    '3. import app.config',
    '4. import app.ty_sim(all mixins)',
    '5. pygame.init()',
    '6. AppConfig.load',
    '7. set_mode',
    '8. TySim() ctor (incl. load_typhoon_files)',
    '9. first update',
    '10. first draw',
)


def once(emit=False):
    """跑一遍真实启动路径, 返回 [(stage, ms), ...] (不含合计)。"""
    rows = []

    def mark(name, t0):
        dt = (time.perf_counter() - t0) * 1000.0
        rows.append((name, dt))
        if emit:
            print('S|%s|%.1f' % (name, dt), flush=True)
        return time.perf_counter()

    t = time.perf_counter()
    import pygame                                       # noqa: F401
    t = mark(STAGES_LABEL[0], t)

    import numpy                                        # noqa: F401
    t = mark(STAGES_LABEL[1], t)

    from app.config import AppConfig
    t = mark(STAGES_LABEL[2], t)

    from app.ty_sim import TySim
    t = mark(STAGES_LABEL[3], t)

    pygame.init()
    t = mark(STAGES_LABEL[4], t)

    cfg = AppConfig.load('config.json')
    t = mark(STAGES_LABEL[5], t)

    screen = pygame.display.set_mode((cfg.screen_width, cfg.screen_height),
                                     pygame.RESIZABLE, vsync=0)
    pygame.display.set_caption('typhoon sim')
    t = mark(STAGES_LABEL[6], t)

    # TySim.__init__ 内部已经 load_typhoon_files()(app/ty_sim.py:185) ——
    # 不要在这里再调一次, 否则重复计时(见文件头注释)。
    sim = TySim(screen, cfg=cfg)
    t = mark(STAGES_LABEL[7], t)

    sim.update(0.0)
    t = mark(STAGES_LABEL[8], t)
    sim.draw(screen)
    t = mark(STAGES_LABEL[9], t)
    return rows


def show_single(repeat):
    total_rows = {}
    for i in range(repeat):
        if i:
            print('-' * 52)
        rows = once()
        tt = 0.0
        for name, dt in rows:
            print('%-44s %9.1f' % (name[:44], dt))
            total_rows.setdefault(name, []).append(dt)
            tt += dt
        print('%-44s %9.1f' % ('== TOTAL ==', tt))
    if repeat > 1:
        print()
        print('(同进程重复: import 阶段从第 2 次起走 sys.modules 缓存 ⇒ 不可比;')
        print(' 要看离散度请用 --agg N)')


def show_agg(n):
    data = {}
    order = []
    for _ in range(n):
        out = subprocess.run([sys.executable, os.path.abspath(__file__), '--one'],
                             capture_output=True, text=True)
        for line in out.stdout.splitlines():
            if not line.startswith('S|'):
                continue
            _, name, ms = line.split('|')
            if name not in data:
                data[name] = []
                order.append(name)
            data[name].append(float(ms))
    print('== 启动分阶段计时: %d 个独立进程 ==' % n)
    print('%-44s %8s %9s %8s' % ('stage', 'min', 'median', 'max'))
    print('-' * 74)
    tot = []
    for name in order:
        v = data[name]
        print('%-44s %8.1f %9.1f %8.1f' % (name[:44], min(v),
                                           statistics.median(v), max(v)))
    # 每次进程的合计 = 该次所有阶段之和
    per_run = []
    for i in range(n):
        per_run.append(sum(data[k][i] for k in order))
    print('-' * 74)
    print('%-44s %8.1f %9.1f %8.1f' % ('== TOTAL (per-process sum) ==',
                                       min(per_run), statistics.median(per_run),
                                       max(per_run)))
    print()
    print('spread(min->max) = %.1f%%' % ((max(per_run) / min(per_run) - 1) * 100))


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--one' in args:
        once(emit=True)
    elif '--agg' in args:
        show_agg(int(args[args.index('--agg') + 1]))
    else:
        show_single(int(args[0]) if args else 1)
