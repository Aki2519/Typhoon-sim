# -*- coding: utf-8 -*-
"""轮次 160 临时工具: 给 `_env_update_impl` 插计时探针, 实测各段每步耗时。

用法:
    python tools/_probe_sections.py inject    # 插探针(先自动备份)
    python tools/_probe_sections.py run [步数] # 跑并打印分段耗时
    python tools/_probe_sections.py restore   # 从备份还原并校验 sha256

⚠ 这是**临时诊断工具**, 跑完必须 restore。判据: 还原后 core.py 的 sha256 必须
回到 `577e5b1669c812347c96577ea28159b4aecddb59ee1def0f8048d942c4f458e`。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, 'simulator', 'simcore', 'core.py')
BACKUP = 'G:/tmp/core_r160_backup.py'
BACKUP_SHA = '577e5b1669c812347c96577ea28159b4aecdddb59ee1def0f8048d942c4f458e'

# (插入到这一行之前, 标签) —— 按**行号降序**处理, 保证前面的行号不漂移
BOUNDS = [
    (2032, '15_tail'),
    (2007, '14_data_status'),
    (1989, '13_disp_pressure'),
    (1962, '12_mslp_total'),
    (1951, '11_cold_surge'),
    (1929, '10_outflow'),
    (1914, '9_steer_eddy'),
    (1789, '8_geo_fric'),
    (1765, '7_pressure'),
    (1720, '6_rh'),
    (1687, '5_shear'),
    (1652, '4_bands'),
    (1644, '3_ocean_ohc'),
    (1586, '2_dataload'),
    (1569, '1_head'),
]

HELPER = '''
# ── 轮次 160 临时探针(用完必须 restore) ──────────────────────────────────
_PROBE_T = {}
_PROBE_T0 = [None]


def _probe(label):
    _t = _time.perf_counter()
    if _PROBE_T0[0] is not None:
        _PROBE_T[label] = _PROBE_T.get(label, 0.0) + (_t - _PROBE_T0[0])
    _PROBE_T0[0] = _t
# ── /探针 ──────────────────────────────────────────────────────────────────
'''


def sha_of(path):
    return hashlib.sha256(io.open(path, 'rb').read()).hexdigest()


def inject():
    src = io.open(CORE, encoding='utf-8').read()
    if '_PROBE_T0' in src:
        print('已经插过探针了, 先 restore')
        return 1
    if not os.path.exists(BACKUP):
        shutil.copy2(CORE, BACKUP)
    lines = src.split('\n')
    # 1) 在 def 行之前插 helper, 在 def 行之后插 T0 重置
    dl = next(i for i, l in enumerate(lines)
              if l.startswith('def _env_update_impl'))
    lines.insert(dl, HELPER)
    lines.insert(dl + 2, '    _PROBE_T0[0] = _time.perf_counter()')
    # 行号平移到新数组上: HELPER 是**一个**含内嵌换行的列表元素 ⇒ 只让索引平移 1,
    # 再加 T0 那一行, 共 2。(按 HELPER.count('\n') 算会错得很远 —— 实测踩过。)
    shift = 2
    for ln, label in BOUNDS:                      # 已按降序
        idx = ln - 1 + shift
        lines.insert(idx, "    _probe('%s')" % label)
    out = '\n'.join(lines)
    if 'import time as _time' not in out:
        out = out.replace('import math', 'import math\nimport time as _time', 1)
    io.open(CORE, 'w', encoding='utf-8').write(out)
    print('已注入探针; 原地 sha256 =', sha_of(CORE))
    return 0


def restore():
    if not os.path.exists(BACKUP):
        print('没有备份, 拒绝还原')
        return 1
    shutil.copy2(BACKUP, CORE)
    got = sha_of(CORE)
    ok = (got == BACKUP_SHA)
    print('已还原; sha256 =', got)
    print('与备份基线一致:', ok)
    return 0 if ok else 1


RUNNER = '''# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, __ROOT__)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
V.env_update(sim)
V._PROBE_T.clear()
dt = V.step_hours(sim)
N = __STEPS__
for _ in range(N):
    V.sim_step(sim, dt)

tot = sum(V._PROBE_T.values())
rows = sorted(V._PROBE_T.items(), key=lambda kv: -kv[1])
print('total steps = {}'.format(N))
print('{:<20} {:>12} {:>8}'.format('section', 'ms/step', 'share'))
for k, v in rows:
    print('{:<20} {:>12.2f} {:>7.1f}%'.format(k, v / N * 1000.0, v / tot * 100.0))
print('{:<20} {:>12.2f} {:>7.1f}%'.format('TOTAL', tot / N * 1000.0, 100.0))
'''


def run(steps):
    tmp = 'G:/tmp/_probe_runner.py'
    io.open(tmp, 'w', encoding='utf-8').write(
        RUNNER.replace('__ROOT__', repr(ROOT)).replace('__STEPS__', str(steps)))
    # ⚠⚠ 审查抓到(危险): 原来  是**独立子命令**, 完全手动。
    #   跑到一半 Ctrl-C / 抛异常 / 忘了跑 restore ⇒ **core.py(291KB) 停在
    #   被打补丁的状态**: 下次 inject 还会拿被 patch 的版本当基线, 越滚越错。
    #   现在无论成功/异常/中断都在 finally 里还原( 自带"没有备份
    #   就拒绝"的保护, 所以未被 inject 时调用它是安全的 no-op)。
    try:
        p = subprocess.run([sys.executable, tmp], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        print(p.stdout)
        if p.returncode:
            print('--- stderr ---')
            print(p.stderr[-2500:])
        return p.returncode
    finally:
        print('--- 自动还原 core.py ---')
        restore()


if __name__ == '__main__':
    op = sys.argv[1] if len(sys.argv) > 1 else 'inject'
    if op == 'inject':
        sys.exit(inject())
    if op == 'restore':
        sys.exit(restore())
    if op == 'run':
        sys.exit(run(int(sys.argv[2]) if len(sys.argv) > 2 else 20))
    print('用法: inject | run [步数] | restore')
