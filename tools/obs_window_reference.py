# -*- coding: utf-8 -*-
"""**窗口匹配的观测基准**(轮次 138, 离线): 给"某个月份窗"的观测档位占比/分位数。

为什么需要: 验收用的是 **100 天窗口**(本机跑不了 365 天), 而 1 月窗与 7 月窗的
观测分布**本来就不一样**(7 月窗是北半球盛期、还有南半球各自的盛期)。
拿"全年多海盆"的基准去判"某个月份窗"的模型值, 是又一次口径混用
—— 本轮之前 C 的判读就有这个风险(模型 7 月窗的 100-129 高达 28.5%, 而基准是全年值)。

用法:
    python tools/obs_window_reference.py --obs "G:\\气象\\数据\\xrq\\wp" --months 7-10
    python tools/obs_window_reference.py --obs "G:\\气象\\数据\\xrq\\sh" --months 1-4

口径声明(与 `obs_reference_table.py` 一致): 一个文件 = 一个系统; "窗口内"按**首报月份**筛;
档位占比只在 peak>=34kt 的匹配子总体里算; 最高纬度取 |lat| 的极值。

⚠⚠ **池子选择会被窗口放大(轮次 138 实测, 必读)**:
同样筛"首报月份 7-10", 三个来源给出**完全不同**的结论 ——

| 来源 | n(发展) | 100-129 | 130+ | 最高\|纬\| 中位 | 到过>=28 |
|---|---|---|---|---|---|
| `xrq/wp`(1949-2026, 2340 个) | 1631 | 18.4% | 18.3% | **37.5** | 63.5% |
| `自评2025`(WP) | 24 | 8.3% | 16.7% | 25.2 | 41.7% |
| `自评2026`(WP) | 22 | 13.6% | 4.5% | 28.8 | 54.5% |

`xrq/wp` 那个 **37.5 度**是**历史池的选择偏差**: 早期(卫星前)只记录到长得远、活得久的
系统, 于是"7-10 月首报"这个子集被再弯向高纬的风暴主导。**不能用它判模型**。
⇒ 判读 100 天窗口时, 应用**现代单年集合池化**(自评2025+2026 同窗口: 发展 46 个,
100-129 ≈ **11%**, 130+ ≈ **11%**, 最高纬 25.2-28.8, 到过≥28 41.7-54.5%),
并且记住单年 n≈25 时档位占比的标准误有 4-6 个百分点。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--obs', default=r'G:\气象\数据\xrq\wp')
    ap.add_argument('--months', default='7-10', help='形如 7-10 或 1-4')
    ap.add_argument('--hemi', default='auto', choices=['auto', 'N', 'S'])
    a = ap.parse_args()
    m0, _, m1 = a.months.partition('-')
    m0, m1 = int(m0), int(m1 or m0)
    months = set(range(m0, m1 + 1)) if m0 <= m1 else (set(range(m0, 13)) | set(range(1, m1 + 1)))

    from simulator.simcore import import_tc as IMP
    peaks, latmax, latnet, n_all = [], [], [], 0
    for p in sorted(glob.glob(os.path.join(a.obs, '**', '*.dat'), recursive=True)):
        try:
            rows = IMP.parse_bdeck(p)
        except Exception:
            continue
        if len(rows) < 2:
            continue
        if rows[0]['t'].month not in months:
            continue
        n_all += 1
        v = np.array([float(r['vmax'] or 0.0) for r in rows], float)
        if v.size == 0:
            continue
        pk = float(v.max())
        peaks.append(pk)
        if pk >= 34.0:
            la = np.array([float(r['lat']) for r in rows], float)
            hemi = 1.0 if (a.hemi == 'N' or (a.hemi == 'auto' and rows[0]['lat'] >= 0)) else -1.0
            lm = float(np.max(hemi * la))
            latmax.append(lm)
            latnet.append(lm - float(hemi * la[0]))
    if not peaks:
        print('窗口 %s 内没有系统(检查 --obs 与 --months)' % a.months)
        return 1
    pk = np.array(peaks, float)
    dev = pk[pk >= 34.0]
    print('=== %s | 首报月份 %s | 系统 %d(发展 %d) ==='
          % (a.obs, a.months, n_all, dev.size))
    q = np.percentile(dev, [50, 90, 95])
    print('  峰值(发展子总体): P50 %.0f  P90 %.0f  P95 %.0f  最大 %.0f'
          % (q[0], q[1], q[2], dev.max()))
    print('  **100-129 %.1f%% | 130+ %.1f%%**(同口径: 匹配子总体内的档位占比)'
          % (100.0 * ((dev >= 100) & (dev < 130)).mean(),
             100.0 * (dev >= 130).mean()))
    if latmax:
        lm = np.array(latmax, float)
        ln = np.array(latnet, float)
        print('  最高|纬| 中位 %.1f | 到过>=28 %.1f%% | 净向极 中位 %.1f'
              % (np.median(lm), 100.0 * (lm >= 28).mean(), np.median(ln)))
    print()
    print('用法提醒: 判读模型的**同一个 100 天窗口**时, 必须用本工具给出的同窗口基准,')
    print('          不能用全年的 5.3-11.1% / 12.3-13.9%。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
