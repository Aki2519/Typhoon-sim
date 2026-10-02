# -*- coding: utf-8 -*-
"""轮次 162 临时工具: 给 `_env_update_impl` 插分段计时探针, **按内容锚点定位**。

为什么改成锚点: 轮次 160 那版把段边界**硬编码成行号**, 而之后 `core.py` 又加过
ENSO 常量(+8 行)、删过 `_lowpass`/`_gauss_kernel` 的重复定义(-39 行) ⇒ 硬编码行号
会插到**错的位置**(甚至插进表达式中间造成语法错)。锚点按**注释内容**找, 不会漂。

用法:
    python tools/_probe_sections.py inject     # 插探针(自动备份 + sha256)
    python tools/_probe_sections.py run [步数]  # 跑并打印分段耗时
    python tools/_probe_sections.py restore    # 还原并校验 sha256

⚠ 这是**临时诊断工具**, 跑完必须 restore。
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, 'simulator', 'simcore', 'core.py')
BACKUP = 'G:/tmp/core_r162_backup.py'
SHAFILE = 'G:/tmp/core_r162_backup.sha256'

# (锚点子串, 标签) —— 探针插在**匹配行的前一行**。标签语义: 该标签累计的是
# "从上一个探针到这里"的耗时(即**结束**于该锚点的那一段)。
ANCHORS = [
    ('── 数据驱动模式', '1_head'),
    ('# 海洋柱状态', '2_dataload'),
    ('# 季风槽/副高纬度带', '3_ocean_ohc'),
    ('# 风切变(kt)', '4_bands'),
    ('# 中层湿度', '5_shear'),
    ('# 环境气压', '6_rh'),
    ('# 地转风 + 摩擦', '7_pressure'),
    ('# 数据模式: 真实引导', '8_geo_fric'),
    ('# F8: **出流场**', '9_steer_eddy'),
    ('# F9: **冷高压', '10_outflow'),
    ('# 总气压 = 环境', '11_cold_surge'),
    ('── 显示用气压', '12_mslp_total'),
    ('# 数据驱动状态', '13_disp_pressure'),
    ('**数据是瞬时还是月均兜底**', '14_data_status'),
]

HELPER = '''
# -- 轮次 162 临时探针(用完必须 restore) --
_PROBE_T = {}
_PROBE_T0 = [None]


def _probe(label):
    _t = _time.perf_counter()
    if _PROBE_T0[0] is not None:
        _PROBE_T[label] = _PROBE_T.get(label, 0.0) + (_t - _PROBE_T0[0])
    _PROBE_T0[0] = _t
# -- /探针 --
'''

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
print('steps = {}  (per-step ms, share of env_update)'.format(N))
print('{:<20} {:>12} {:>8}'.format('segment', 'ms/step', 'share'))
for k, v in rows:
    print('{:<20} {:>12.2f} {:>7.1f}%'.format(k, v / N * 1000.0, v / tot * 100.0))
print('{:<20} {:>12.2f} {:>7.1f}%'.format('TOTAL', tot / N * 1000.0, 100.0))
'''


def sha_of(p):
    return hashlib.sha256(io.open(p, 'rb').read()).hexdigest()


def func_span(lines):
    """`_env_update_impl` 的 [起, 止) 行下标。"""
    i = next(k for k, l in enumerate(lines)
             if l.startswith('def _env_update_impl'))
    j = i + 1
    while j < len(lines):
        s = lines[j]
        if s and not s.startswith((' ', '\t', '#')):
            break
        j += 1
    return i, j


def inject():
    src = io.open(CORE, encoding='utf-8').read()
    if '_PROBE_T0' in src:
        print('已插过探针, 先 restore')
        return 1
    shutil.copy2(CORE, BACKUP)
    io.open(SHAFILE, 'w').write(sha_of(CORE))
    lines = src.split('\n')
    a, b = func_span(lines)
    # 找锚点(只在函数体内找)
    hits = []
    for pat, label in ANCHORS:
        idx = next((k for k in range(a, b) if pat in lines[k]), None)
        if idx is None:
            print('锚点找不到: %r  => 放弃(不写文件)' % pat)
            return 1
        hits.append((idx, label))
    hits.sort()
    print('锚点定位(前 3 个): %s' % [(h[0] + 1, h[1]) for h in hits[:3]])
    # 降序插(避免索引漂移)
    for idx, label in sorted(hits, reverse=True):
        lines.insert(idx, "    _probe('%s')" % label)
    dl = next(k for k, l in enumerate(lines)
              if l.startswith('def _env_update_impl'))
    lines.insert(dl, HELPER)
    lines.insert(dl + 2, '    _PROBE_T0[0] = _time.perf_counter()')
    out = '\n'.join(lines)
    if 'import time as _time' not in out:
        out = out.replace('import math', 'import math\nimport time as _time', 1)
    io.open(CORE, 'w', encoding='utf-8', newline='\n').write(out)
    print('已注入; sha256 =', sha_of(CORE))
    return 0


def restore():
    want = io.open(SHAFILE).read().strip() if os.path.exists(SHAFILE) else ''
    shutil.copy2(BACKUP, CORE)
    got = sha_of(CORE)
    print('已还原; sha256 =', got)
    print('与备份一致:', got == want)
    return 0 if got == want else 1


def run(steps):
    tmp = 'G:/tmp/_probe_runner162.py'
    io.open(tmp, 'w', encoding='utf-8').write(
        RUNNER.replace('__ROOT__', repr(ROOT)).replace('__STEPS__', str(steps)))
    # ⚠⚠ 审查抓到(危险): 原来  是**独立子命令**, 完全手动。
    #   跑到一半 Ctrl-C / 抛异常 / 忘了跑 restore ⇒ **core.py(291KB) 停在
    #   被打补丁的状态**: 下次 inject 还会拿被 patch 的版本当基线, 越滚越错。
    #   现在无论成功/异常/中断都在 finally 里还原( 自带"没有备份
    #   就拒绝"的保护, 所以未被 inject 时调用它是安全的 no-op)。
    try:
        p = subprocess.run([sys.executable, tmp], cwd=ROOT, capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
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
