# -*- coding: utf-8 -*-
"""轮次 162: 拆 `_advect_env`(113 ms/步, 12 次/步) 的每一次成本。

嫌疑: `km_l = 111.32 * np.cos(lat2 * C['DEG'])` 只依赖**纬度网格 + C['DEG']**,
却在这个函数里**每次**都算(12 次/步)。以及 `field.any()` 与 `np.nonzero(field)`
两次全网格扫描。

不猜, 逐件测。
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                        # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
V.env_update(sim)
LAT = V._env_grids()[0]
U = np.asarray(sim['env']['u850'], np.float64)
WAKE = np.asarray(sim['wake']).reshape(V.ENVNY, V.ENVNX)
OHCW = np.asarray(sim['ohcWake']).reshape(V.ENVNY, V.ENVNX)
print('网格 =', LAT.shape, ' wake 非零格 = %d (%.4f%%)'
      % (int((WAKE != 0).sum()), (WAKE != 0).mean() * 100))
print('ohcWake 非零格 = %d (%.4f%%)'
      % (int((OHCW != 0).sum()), (OHCW != 0).mean() * 100))


def bench(fn, n=20, label=''):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    print('%-44s %8.3f ms' % (label, (time.perf_counter() - t0) / n * 1000.0))


print()
print('=== 逐件 ===')
bench(lambda: 111.32 * np.cos(LAT * V.C['DEG']), 20,
      'km_l = 111.32*np.cos(LAT*DEG)  [常量网格!]')
bench(lambda: np.cos(LAT * V.C['DEG']), 20, 'np.cos(LAT*DEG) 单项')
bench(lambda: WAKE.any(), 20, 'WAKE.any()')
bench(lambda: np.nonzero(WAKE), 20, 'np.nonzero(WAKE)  [稀疏]')
bench(lambda: np.nonzero(OHCW), 20, 'np.nonzero(OHCW)')
bench(lambda: np.abs(U * 0.01).max(), 20, 'np.abs(x).max()')

print()
print('=== 整体(真实输入, 真实 dt_h=1h) ===')
bench(lambda: V._advect_env(WAKE, U, U, 1.0, 1.0), 20,
      '_advect_env(wake, u, v, 1h)')
bench(lambda: V._advect_env(OHCW, U, U, 0.4, 1.0), 20,
      '_advect_env(ohcWake, u, v, 0.4h)')

print()
print('=== 对照: 把 km_l 换成预先算好的常量 ===')
KM_L = 111.32 * np.cos(LAT * V.C['DEG'])
print('  (仅列成本, 不改仓库代码)')
bench(lambda: 111.32 * np.cos(LAT * V.C['DEG']) * 0 + KM_L, 20,
      'km_l 取自常量(对照, 不含 cos)')
