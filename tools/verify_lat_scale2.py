# -*- coding: utf-8 -*-
"""决定性测量: `_pad_poles` 把 sst 色阶层拉成了什么纬度比例?

上一版探针用"行均值偏离背景"找哨兵带, 被色表离散的边缘混叠影响(测到 5.0 rows/deg)。
这一版改用**两件事**互相印证:
  A. 直接把 `render._pad_poles` 换成"原样返回", 拿到**未填充**的数组, 量它的
     rows/deg —— 这是每一层真实携带的纬度分辨率;
  B. 用**面积守恒**推填充后的有效跨度: 填充只是在两端接上外推行, 中间的真实数据
     行数不变 ⇒ 若填充后总行数 = N_out, 而真实数据 = N_core 行、代表 span_core 度,
     则填充后真实数据被摊到 `span_core * N_out / N_core` 度上, 比例 = N_out/N_core。

判据: 若 B 的比例显著偏离 1, 则图层画出来就是被拉伸/压缩的。
"""
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from simulator.simcore import render as R  # noqa: E402
from simulator.simcore import core as V  # noqa: E402

ENVNY, ENVNX = int(V.ENVNY), int(V.ENVNX)
print('内核域: ENVNY=%d ENVNX=%d LAT0=%.1f LAT1=%.1f'
      % (ENVNY, ENVNX, V.C['LAT0'], V.C['LAT1']))

ny, nx = ENVNY, ENVNX
sst = np.full((ny, nx), 20.0, np.float32)
sst[:, :] = 20.0
sim = {'sst': sst, 'land': np.zeros((ny, nx), np.float32),
       'env': {'sst': sst}, '_envVer': 987654, 'month': 7, 'ocn': {},
       't': 0.0}

# ── A. 未填充的数组(拦截 _pad_poles) ──
_calls = []
_orig_pad = R._pad_poles


def _ident(arr, lat0=None, lat1=None):
    _calls.append((tuple(arr.shape), lat0, lat1))
    return arr


R._pad_poles = _ident
try:
    R._env_arr_cache.clear()
    core = R.render_env_layer(sim, 'sst', alpha=210)
finally:
    R._pad_poles = _orig_pad

print()
print('A. 未填充数组: shape=%s  (拦截到 %d 次调用)'
      % (tuple(core.shape), len(_calls)))

# ── B. 正常路径(带填充) ──
R._env_arr_cache.clear()
padded = R.render_env_layer(sim, 'sst', alpha=210)
print('B. 填充后数组: shape=%s' % (tuple(padded.shape),))

n_core, n_out = core.shape[0], padded.shape[0]
ratio = n_out / float(n_core)
# 真实数据本来覆盖 ±90(内核域). 填充后总跨度被当作 ±90 =>
# 真实数据被摊到的跨度 = 180 * n_core/n_out
span_after = 180.0 * n_core / n_out
print()
print('真实数据 %d 行 本来覆盖 180.0 度 (%.4f rows/deg)' % (n_core, n_core / 180.0))
print('填充后总 %d 行 被当作 ±90 铺开 => 真实数据只占 %.2f 度 (%.4f rows/deg)'
      % (n_out, span_after, n_out / 180.0))
print('=> 纬度比例 = %.4f  %s' % (n_core / n_out,
                                  'OK' if abs(n_core / n_out - 1.0) < 0.02 else '**被压缩**'))

# ── C. 复算: 修复后 `_pad_poles` 应在"输入已覆盖 ±90"时**幂等**(不补) ──
_k_span = float(V.C['LAT1']) - float(V.C['LAT0'])
print()
print('C. 内核域跨度 = %.1f 度 => 输入已覆盖 ±90, `_pad_poles` 应幂等(不补行)')
print('   预测输出行数 = %d (实测 %d) %s'
      % (n_core, n_out, 'OK' if n_out == n_core else '**仍在补行**'))
print()
print('结论: 真实数据 %.2f 度 铺在 180 度画布上 => 纬度比例 %.4f' % (span_after, n_core / n_out))
print('      (60N 画在约 %.0fN; 期望 60N)' % (60.0 * n_core / n_out))
