# -*- coding: utf-8 -*-
"""核心分支的定量检查: `u850 = 0.55*DATA['u850'] + 0.45*地转(pEnv)` 里,
**地转那 45% 到底把真实 850hPa 引导改掉了多少?**

轮次 102 的起因: 用**原始训练基态**(base_wind.npz)在观测路径位置取值, 30-40N 的
850hPa 是 (+6.8, +4.7) —— 正是转向所需的西南气流; 可在**内核实际用的** env['u850']
上取值, 同一纬度带只剩 (+0.9, +3.3), u 几乎被抹平。两者差 5.9kt, 必须查清楚
是"数据融合权重(0.55/0.45)"造成的, 还是别的环节。

做法: env['pEnv'] 是内核算地转时的输入, 所以可以在**不改一行代码**的前提下
把地转分量逐格重算出来, 再由 0.55/0.45 反解出 DATA['u850'], 与原始基态对比。

用法: python tools/probe_geo_blend.py [--month 8]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', type=int, default=8)
    ap.add_argument('--lat', type=float, default=35.0)
    ap.add_argument('--lon', type=float, default=140.0)
    a = ap.parse_args()

    from simulator.simcore import core as V
    sim = V.init({'start': '2025-%02d-01 00' % a.month, 'seed': 3, 'envStepH': 6.0,
                  'params': {'dataMode': True, 'precision': '1s'}})
    V.env_update(sim)
    env = sim['env']
    LAT, LON = V._env_grids()
    ny, nx = V.ENVNY, V.ENVNX
    # ── 与 core.env_update 里**逐字相同**的地转重建 ──
    f = 2 * V.C['OMEGA'] * np.sin(LAT * V.C['DEG'])
    f_eff = np.where(f >= 0, 1.0, -1.0) * np.maximum(np.abs(f), 3.2e-5)
    cos_lat = np.cos(LAT * V.C['DEG'])
    dx_m = V.C['ENVD'] * 111.32 * cos_lat
    dy_m = V.C['ENVD'] * 110.57
    pe = env['pEnv']
    dpdx = np.empty_like(pe)
    dpdx[:, 1:-1] = (pe[:, 2:] - pe[:, :-2]) / (2.0 * dx_m[:, 1:-1])
    dpdx[:, 0] = (pe[:, 1] - pe[:, 0]) / dx_m[:, 0]
    dpdx[:, -1] = (pe[:, -1] - pe[:, -2]) / dx_m[:, -1]
    dpdy = np.empty_like(pe)
    dpdy[1:-1, :] = (pe[2:, :] - pe[:-2, :]) / (2.0 * dy_m)
    dpdy[0, :] = (pe[1, :] - pe[0, :]) / dy_m
    dpdy[-1, :] = (pe[-1, :] - pe[-2, :]) / dy_m
    ug = -0.1 * dpdy / (V.C['RHO'] * f_eff)
    vg = 0.1 * dpdx / (V.C['RHO'] * f_eff)
    sp = np.hypot(ug, vg)
    ang = 0.30
    uf = ug * np.cos(ang) * 0.88 - vg * np.sin(ang) * 0.88
    vf = ug * np.sin(ang) * 0.88 + vg * np.cos(ang) * 0.88
    ua = np.where(sp > 0.3, uf, 0.0) * V.KT
    va = np.where(sp > 0.3, vf, 0.0) * V.KT
    g = 0.55
    # env['u850'] = g*DATA + (1-g)*ua + steerU*KT*0.5
    d_u = (env['u850'] - (1 - g) * ua - sim['params']['steerU'] * V.KT * 0.5) / g
    d_v = (env['v850'] - (1 - g) * va - sim['params']['steerV'] * V.KT * 0.5) / g

    def at(arr, lat, lon):
        j = min(max(int((lat - V.C['LAT0']) / V.C['ENVD']), 0), ny - 1)
        i = min(max(int((lon - V.C['LON0']) / V.C['ENVD']), 0), nx - 1)
        return float(arr[j, i])

    print('月=%d  检查点: %.0fN 附近一串经度' % (a.month, a.lat))
    print('%6s | %-17s | %-17s | %-17s | %s'
          % ('lon', "env['u850'] (内核用)", "地转 45% 分量", "反解 DATA['u850']", '差(内核-真实)'))
    for lon in (110, 120, 130, 140, 150, 160, 170, 180):
        print('%6.0fE | %+6.2f / %+6.2f  | %+6.2f / %+6.2f  | %+6.2f / %+6.2f  | %+6.2f / %+6.2f'
              % (lon, at(env['u850'], a.lat, lon), at(env['v850'], a.lat, lon),
                 at(ua, a.lat, lon), at(va, a.lat, lon),
                 at(d_u, a.lat, lon), at(d_v, a.lat, lon),
                 at(env['u850'], a.lat, lon) - at(d_u, a.lat, lon),
                 at(env['v850'], a.lat, lon) - at(d_v, a.lat, lon)))
    print()
    # 纬向剖面: 西太 120-160E 平均
    sel = (LON >= 120.0) & (LON <= 160.0)
    print('西太 120-160E 纬向剖面(kt)')
    print('%5s | %-17s | %-17s | %s' % ('lat', "env['u850']", "地转分量", "反解 DATA['u850']"))
    for la in (10, 15, 20, 25, 30, 35, 40, 45):
        j = int((la - V.C['LAT0']) / V.C['ENVD'])
        print('%4dN | %+6.2f / %+6.2f  | %+6.2f / %+6.2f  | %+6.2f / %+6.2f'
              % (la, env['u850'][j, sel].mean(), env['v850'][j, sel].mean(),
                 ua[j, sel].mean(), va[j, sel].mean(),
                 d_u[j, sel].mean(), d_v[j, sel].mean()))


if __name__ == '__main__':
    main()
