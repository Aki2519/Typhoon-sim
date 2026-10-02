"""按海盆比较"发展率"(不用跑 sim), **已按系统去重**。

去重原则(轮次 89 修): 归档里同一系统可能**同时有** invest 段文件与编号文件。
- 编号文件(wp012025.dat 等): 每个文件 = 一个系统, 且按定义都发展过(>=34kt);
- invest 文件(wpA42025.dat 等): **只有一生从未到 34kt 的**才算独立扰动(纯扰动),
  其余都是已发展系统的"前身", 不重复计数。

观测(2025 ATCF invest 台账): 全球 200/103=52% | 东太 21/18=86% | 大西洋 19/13=68%
                            西太 64/33=52% | 南半球 76/32=42% | 北印 17/4=24%
用法: python tools/diag_basin_devrate.py --bdeck "output/sim/2025/2025"
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
from stat_rate_table import read_storm      # noqa: E402

OBS = {'wp': (64, 33), 'ep': (21, 18), 'al': (19, 13), 'sh': (76, 32), 'io': (17, 4)}


def peak(paths):
    out = []
    for f in paths:
        try:
            rec = read_storm(f)
        except Exception:
            continue
        if len(rec) < 2:
            continue
        out.append(max(r[3] for r in rec))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bdeck', default=r'output\sim\2025\2025')
    a = ap.parse_args()
    print('模型归档: %s  (已按系统去重)' % a.bdeck)
    print()
    print('%-6s | %-14s | %-14s | %s' % ('海盆', '模型 扰动/发展', '观测 扰动/发展', '发展率 模型 vs 观测'))
    tt = td = ot = od = 0
    for b in ('wp', 'ep', 'al', 'io', 'sh'):
        named = peak(glob.glob(os.path.join(a.bdeck, '2025' + b, '*.dat')))
        inv = peak(glob.glob(os.path.join(a.bdeck, '2025' + b + '_invest', '*.dat')))
        dev_named = sum(1 for v in named if v >= 34.0)
        pure = sum(1 for v in inv if v < 34.0)          # 纯扰动才算独立系统
        mt = len(named) + pure
        md = dev_named
        ob, odv = OBS.get(b, (0, 0))
        tt += mt
        td += md
        ot += ob
        od += odv
        print('%-6s | %4d / %4d    | %4d / %4d    | %3.0f%% vs %3.0f%%'
              % (b, mt, md, ob, odv, 100 * md / max(mt, 1), 100 * odv / max(ob, 1)))
    print('%-6s | %4d / %4d    | %4d / %4d    | %3.0f%% vs %3.0f%%'
          % ('合计', tt, td, ot, od, 100 * td / max(tt, 1), 100 * od / max(ot, 1)))
    print()
    print('判读: 观测的发展率是**海盆依赖**的(24%~86%) ⇒ 要对齐的是**每一行**,')
    print('      而不是把全局发展率压到 52%(那会把东太从 86% 一起拉下来)。')


if __name__ == '__main__':
    main()
