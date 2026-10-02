# -*- coding: utf-8 -*-
"""轮次 160: (1) 证明 `_lowpass`/`_gauss_kernel` 的重复定义**逐字节相同**
(相同才能安全删掉死的那一份); (2) 实测 `_lowpass`/`mesoscale_noise` 的成本。
"""
import hashlib
import io
import os
import re
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

SRC = io.open(os.path.join(ROOT, 'simulator', 'simcore', 'core.py'),
              encoding='utf-8').read()
LINES = SRC.split('\n')


def body_of(name, start1):
    """取从第 start1 行(1-based)开始的函数体(到下一个顶层定义/非缩进行)。"""
    i = start1 - 1
    out = [LINES[i]]
    j = i + 1
    while j < len(LINES):
        l = LINES[j]
        if l and not l.startswith((' ', '\t')):
            break
        out.append(l)
        j += 1
    return '\n'.join(out)


print('=== (1) 重复定义的逐字节比对 ===')
for nm, a, b in (('_lowpass', 1336, 1424), ('_gauss_kernel', 1321, 1409)):
    ba, bb = body_of(nm, a), body_of(nm, b)
    ha = hashlib.sha256(ba.encode()).hexdigest()[:16]
    hb = hashlib.sha256(bb.encode()).hexdigest()[:16]
    print('%-14s @%d sha=%s  |  @%d sha=%s  => 逐字节相同: %s'
          % (nm, a, ha, b, hb, ba == bb))

print()
print('=== (2) 成本实测 ===')
from simulator.simcore import core as V                        # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
V.env_update(sim)
env = sim['env']
lat, lon = V._env_grids()
field = np.asarray(env['u850'], np.float64)
F = getattr(V, '_lowpass')          # 取到的是**生效的那份**(第二份)


def bench(fn, n=8, label=''):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    print('%-40s %8.2f ms/次' % (label, (time.perf_counter() - t0) / n * 1000.0))


bench(lambda: F(field, 2.5), 8, '_lowpass(sigma=2.5)')
bench(lambda: F(field, 6.0), 8, '_lowpass(sigma=6.0)')
bench(lambda: V.mesoscale_noise(sim, 'probe_k', 1.0), 8,
      'mesoscale_noise')
bench(lambda: V.mesoscale_eddies(sim, 'probe_e', 6, 1.0, 600.0), 8,
      'mesoscale_eddies')
bench(lambda: V.mjo_field(sim, 5.0), 8, 'mjo_field')
bench(lambda: V.sh_ridge_lat(sim), 8, 'sh_ridge_lat')
print()
print('grid 尺寸 =', field.shape, ' dtype =', field.dtype)
