"""观测检验: 厄尔尼诺年 vs 拉尼娜年的**生成经度分布**(不用跑 sim)。

目的: 核验 `tc_genesis_modes` 里 b_oni 的经度符号是否符合观测。
     "厄尔尼诺东移" = 西太远东(100-140E)减少、中太(180-220)与东太(220-290)增多。

数据: G:\\气象\\数据\\xrq\\<年>\\*.dat(按年分目录, 文件名 b<basin><NN><YYYY>.dat)
指数: modes.ModeSeries 的 oni(月值), 年值取年均。
"""
import glob
import os
import re
import sys

import numpy as np

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
from stat_rate_table import read_storm                      # noqa: E402
from simulator.simcore.envmodel import modes as md          # noqa: E402

ROOT = r'G:\气象\数据\xrq'


def main():
    ms = md.ModeSeries()
    iname = ms.names.index('oni') if 'oni' in ms.names else None
    if iname is None:
        print('没有 oni 模态'); return

    lon_by_year = {}
    for ydir in sorted(glob.glob(os.path.join(ROOT, '*'))):
        if not os.path.isdir(ydir):
            continue
        try:
            year = int(os.path.basename(ydir))
        except ValueError:
            continue
        recs = []
        for f in sorted(glob.glob(os.path.join(ydir, '*.dat'))):
            base = os.path.basename(f)
            m = re.match(r'b([a-z]{2})(\d{2})(\d{4})', base)
            if not m:
                continue
            basin = m.group(1)                 # wp / cp / ep / al / io / sh
            try:
                rec = read_storm(f)
            except Exception:
                continue
            if len(rec) < 2:
                continue
            recs.append((basin, float(rec[0][2])))    # 生成经度(首报)
        if recs:
            lon_by_year[year] = recs

    yrs = sorted(lon_by_year)
    print('可用年份 %d-%d (%d 年), 共 %d 个系统'
          % (yrs[0], yrs[-1], len(yrs), sum(len(v) for v in lon_by_year.values())))

    oni = {}
    for y in yrs:
        vals = [float(ms.at(y, mm)[iname]) for mm in range(1, 13)]
        oni[y] = float(np.mean(vals))

    groups = {'厄尔尼诺(ONI>=1.0)': [y for y in yrs if oni[y] >= 1.0],
              '拉尼娜(ONI<=-1.0)': [y for y in yrs if oni[y] <= -1.0]}

    print()
    print('%-20s %5s %6s | %s' % ('组', '年数', '系统数', '经度带占比(100-140 / 140-180 / 180-220 / 220-290)'))
    for nm, ys in groups.items():
        lons = [lo for y in ys for (b, lo) in lon_by_year[y] if b == 'wp']
        if not lons:
            continue
        a = np.asarray(lons, float)
        n = a.size
        b1 = float(((a >= 100) & (a < 140)).mean())
        b2 = float(((a >= 140) & (a < 180)).mean())
        b3 = float(((a >= 180) & (a < 220)).mean())
        b4 = float(((a >= 220) & (a < 290)).mean())
        print('%-20s %5d %6d | %.0f%% / %.0f%% / %.0f%% / %.0f%%   平均 %.1fE'
              % (nm, len(ys), n, 100 * b1, 100 * b2, 100 * b3, 100 * b4, a.mean()))
        # 中太/东太的系统数(非 wp 前缀)
        cp = sum(1 for y in ys for (b, lo) in lon_by_year[y] if b in ('cp', 'ep'))
        print('%-20s %5s %6d | 中太/东太(cp+ep)系统数 %d' % ('', '', cp, cp))
    print()
    print('判读: 若厄尔尼诺组的中太/东太占比与系统数**更高**, 则观测支持"东移";')
    print('      再看 b_oni 在 220-290 是负(-0.0005)是否符合 —— 若不符, 就是拟合口径问题。')


if __name__ == '__main__':
    main()
