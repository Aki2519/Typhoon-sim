# -*- coding: utf-8 -*-
"""轮次 160: 验证 `enso_update` 的常量网格化 —— (a) 不抛 NameError,
(b) 缓存值与"每次现算"**逐位相同**, (c) `enso_update` 的实际输出与手工参考一致。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

from simulator.simcore import core as V                        # noqa: E402

sim = V.init({'start': '2025-08-01 00', 'seed': 12345,
              'params': {'dataMode': True, 'genesis': True, 'forecast': False}})
V.env_update(sim)

LAT, LON = V._env_grids()
CG = V._const_grids()

# (b) 缓存 vs 现算: 逐位
lo_ref = np.where(LON > 180.0, LON - 360.0, LON)
dE_ref = np.exp(-((lo_ref + 145.0) / 42.0) ** 2) * np.exp(-(LAT / 13.0) ** 2)
dW_ref = np.exp(-((lo_ref - 130.0) / 40.0) ** 2) * np.exp(-(LAT / 13.0) ** 2)

for nm, ref in (('enso_lo', lo_ref), ('enso_dE', dE_ref), ('enso_dW', dW_ref)):
    got = CG[nm]
    same = (got.shape == ref.shape and np.array_equal(got, ref))
    print('%-9s 形状 %-12s 逐位相同 = %s' % (nm, str(got.shape), same))
    assert same, '缓存与现算不一致: ' + nm

# (a)+(c) 真跑 enso_update, 与手工参考对比
e = sim['enso']
e['T'] = 1.7                       # 造一个非零 ENSO 相位
sst0 = sim['env']['sst'].copy()
sh0 = sim['env']['shear'].copy()
V.enso_update(sim)                 # 若还有 lat/lon 的 NameError, 这里会炸
T = float(e['T'])
sst_ref = (sst0 + T * 1.8 * dE_ref - T * 0.7 * dW_ref).astype(np.float32)
sh_ref = np.maximum(0.0, sh0 + T * 1.8 * dW_ref - T * 1.0 * dE_ref).astype(np.float32)
print()
print('enso_update 未抛异常(无 lat/lon NameError)')
print('sst   与手工参考逐位相同 =', np.array_equal(sim['env']['sst'], sst_ref))
print('shear 与手工参考逐位相同 =', np.array_equal(sim['env']['shear'], sh_ref))

# (d) 别名: 缓存数组没有被 enso_update 就地改坏
for nm in ('enso_lo', 'enso_dE', 'enso_dW'):
    ref = {'enso_lo': lo_ref, 'enso_dE': dE_ref, 'enso_dW': dW_ref}[nm]
    assert np.array_equal(CG[nm], ref), '缓存被就地在改了: ' + nm
print('缓存数组未被就地改写 = True')
