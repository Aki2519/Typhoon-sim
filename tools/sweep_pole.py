# -*- coding: utf-8 -*-
"""**参数扫参台**(副高向极修正 + RI 幅度成形): 一次跑出"经向剖面 + 生命周期 +
A 判据 + 峰值分布"的对照表。

为什么需要它(轮次 120 的诊断结论):
  · 模型经向移速是观测的 ~1.9 倍, 而**全部**向极引导都来自 `sh_pole_steer`
    (去掉它以后 15-25N 的引导是 -1~-2.9kt 向赤道, 纯引导+beta 已经与观测相当);
  · 净向极位移 ≈ 移速 x 寿命: 模型现在"2 倍速度 + 0.5 倍寿命", 乘积恰好等于观测
    ⇒ 把推速降到观测量级**可能同时**修好 C 的寿命与 A 的纬度判据。
  · 但这两个目标互相牵制, 单点试一个值很容易"修好一个弄坏另一个" ⇒ 必须**一次扫一组,
    同一批判据一起看**。所以所有要扫的参数都可由 `sim['params']` 覆盖
    (`shPoleS/W/Max`、`shPoleForm/Mu/Sig`、`riAmpForm/Base/Gain/V0/V1`),
    **扫参不改源码**, 口径也不会分叉(规则 14)。

用法:
    python tools/sweep_pole.py --days 100 --sets base,s8m5,s2m6,p8m6
    python tools/sweep_pole.py --days 100 --seeds 20250801,20250802

[!] **每个进程最多跑 2 组**(实测): Python 不把配额还给 OS, 同进程连跑到第 3 组会
在 `ocean_step` 里 OOM(`Unable to allocate 1.98 MiB for shape (360,720)`) ——
即使单看每一组都只要 2MB。要扫多组就**分多次调用**(每次新进程)或用 shell 串起来。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import tools.probe_v_profile as P  # noqa: E402

#: 参数组合。base = 现状默认(S=22.4 / W=20 / Max=8)。
SETS = {
    'base': {},
    's8m5': {'shPoleS': -8.0, 'shPoleW': 6.0, 'shPoleMax': 5.0},
    's2m6': {'shPoleS': -2.0, 'shPoleW': 8.0, 'shPoleMax': 6.0},
    'p8m6': {'shPoleS': 8.0, 'shPoleW': 8.0, 'shPoleMax': 6.0},
    'zero': {'shPoleMax': 0.0},
    # 高斯(带通)型: d = |lat| - 脊线, 峰在 d=mu, 宽 sig, 幅度 max
    'g12s6a45': {'shPoleForm': 'gauss', 'shPoleMu': 12.0, 'shPoleSig': 6.0,
                 'shPoleMax': 4.5},
    'g12s5a50': {'shPoleForm': 'gauss', 'shPoleMu': 12.0, 'shPoleSig': 5.0,
                 'shPoleMax': 5.0},
    'g15s6a50': {'shPoleForm': 'gauss', 'shPoleMu': 15.0, 'shPoleSig': 6.0,
                 'shPoleMax': 5.0},
    'g10s7a45': {'shPoleForm': 'gauss', 'shPoleMu': 10.0, 'shPoleSig': 7.0,
                 'shPoleMax': 4.5},
    # 轮次 121: 离线预筛 + 模型扫参都指向"**窄带通**" —— 峰在 33-35N 附近、40N 以上
    # 迅速收回(脊线在 7 月西太约 37N, 所以 d = |lat| - rl 的峰应在 d ≈ -3 ~ -6)。
    'gn3s35m45': {'shPoleForm': 'gauss', 'shPoleMu': -3.0, 'shPoleSig': 3.5,
                  'shPoleMax': 4.5},
    'gn3s5m45': {'shPoleForm': 'gauss', 'shPoleMu': -3.0, 'shPoleSig': 5.0,
                 'shPoleMax': 4.5},
    'gn6s4m5': {'shPoleForm': 'gauss', 'shPoleMu': -6.0, 'shPoleSig': 4.0,
                'shPoleMax': 5.0},
    'rp8w8m45': {'shPoleS': 8.0, 'shPoleW': 8.0, 'shPoleMax': 4.5},
    # 轮次 122 预备: **RI 幅度随强度成形**(`riAmpForm=vshape`)—— 修"弱端增强速率
    # 2.2 倍"(观测 8.8 kt/天 vs 模型 19.4)。默认 legacy 不受影响。
    'ri_v': {'riAmpForm': 'vshape'},
    # 组合臂: 窄带通副高修正 + RI 幅度成形(两个杠杆一起, 检验"增长慢+寿命长"是否互补)
    'gn_ri_v': {'shPoleForm': 'gauss', 'shPoleMu': -3.0, 'shPoleSig': 3.5,
                'shPoleMax': 4.5, 'riAmpForm': 'vshape'},
    'ri_v55': {'riAmpForm': 'vshape', 'riAmpBase': 5.5, 'riAmpGain': 7.2},
    'ri_v45v80': {'riAmpForm': 'vshape', 'riAmpBase': 4.5, 'riAmpGain': 8.2,
                  'riAmpV0': 80.0, 'riAmpV1': 100.0},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=100.0)
    ap.add_argument('--seeds', default='20250801')
    ap.add_argument('--start', default='2025-07-01 00')
    ap.add_argument('--obs-dir', default=P.DEFAULT_OBS)
    ap.add_argument('--sets', default='base,s8m5,s2m6,p8m6')
    a = ap.parse_args()
    names = [x for x in a.sets.split(',') if x.strip()]
    seeds = [x for x in a.seeds.split(',') if x.strip()]
    hemi = 1 if int(a.start[5:7]) >= 4 else -1

    ob = P.obs_profile(a.obs_dir, hemi)
    head = '%-8s | ' % '参数组'
    for b in P.BANDS:
        head += '%13s | ' % ('%d-%dN o/m' % b)
    head += ('%6s | %6s | %6s | %6s | %5s | %5s | %5s | %5s'
             % ('寿命', '最高纬', '>=28%', '净向极', '峰值', 'P90', '100+', '130+'))
    print('=== 参数扫参 | %s半球 | %s天 | seed(s) %s ==='
          % ('北' if hemi > 0 else '南', a.days, a.seeds))
    print(head)
    for nm in names:
        over = SETS.get(nm, {})
        for sd in seeds:
            mo, comp, life = P.model_profile(a.days, sd, a.start, hemi,
                                            over=over)
            line = '%-8s | ' % (nm if len(seeds) == 1 else '%s/%s' % (nm, sd))
            for b in P.BANDS:
                m = mo.get(b)
                o = ob.get(b)
                if m is None or m.size == 0:
                    line += '%13s | ' % '-'
                    continue
                line += '%13s | ' % ('%.1f/%.1f' % (
                    float(o.mean()) if o is not None and o.size else float('nan'),
                    float(m.mean())))
            if life:
                L = np.array([x['life'] for x in life], float)
                LA = np.array([x['latmax'] for x in life], float)
                NE = np.array([x['lat_net'] for x in life], float)
                PK = np.array([x['peak'] for x in life], float)
                line += ('%6.0f | %6.1f | %5.1f%% | %6.1f | %5.0f | %5.0f | '
                         '%4.1f%% | %4.1f%%' % (
                             np.median(L), np.median(LA),
                             100.0 * float((LA >= 28.0).mean()), np.median(NE),
                             np.median(PK), np.percentile(PK, 90),
                             100.0 * float((PK >= 100.0).mean()),
                             100.0 * float((PK >= 130.0).mean())))
            else:
                line += ('%6s | %6s | %6s | %6s | %5s | %5s | %5s | %5s'
                         % ('-', '-', '-', '-', '-', '-', '-', '-'))
            print(line, flush=True)
    print()
    print('观测(A 口径, 供对照): 最高纬中位 25-26 | 到过>=28 40-45% | 净向极 10.3-13.3')
    print('观测(C 口径): 总寿命 258h(本工具同定义) | 峰值中位 75kt | P90 ~130kt')
    print('  ["100+"/"130+" 是在 peak>=34kt 子总体里算的档位占比(与 verify_year 同口径);')
    print('   本窗口是单半球季 100 天, 与"8 种子全年"的绝对水平不可直接比, 只看移动方向]')


if __name__ == '__main__':
    main()
