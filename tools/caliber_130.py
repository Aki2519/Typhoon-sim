"""定 130+ 的验收口径: 按**同一套 ENSO 相位频率**加权, 让观测与模型可比。

观测: G:\\气象\\数据\\xrq\\<年>\\*.dat + 自评, 按系统所在年份的 ONI 分组, 各自算 130+ 占比;
模型: 已测的三个相位值(厄尔尼诺 16% / 中性 9.3% / 拉尼娜 8%)。
权重用**观测样本自己的相位频率** —— 这样两边是同一把尺子。

用法: python tools/caliber_130.py
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

XP = r'G:\气象\数据\xrq'
ZI = (r'G:\气象\数据\自评\2025', r'G:\气象\数据\自评\2026')
MODEL = {'el': 0.16, 'neu': 0.093, 'la': 0.08}      # 轮次 67/69 实测


def phase(oni):
    if oni >= 1.0:
        return 'el'
    if oni <= -1.0:
        return 'la'
    return 'neu'


def main():
    ms = md.ModeSeries()
    i_oni = ms.names.index('oni')
    oni_year = {}
    for y in range(1949, 2027):
        oni_year[y] = float(np.mean([float(ms.at(y, m)[i_oni]) for m in range(1, 13)]))

    peaks = {k: [] for k in ('el', 'neu', 'la')}
    for ydir in sorted(glob.glob(os.path.join(XP, '*'))):
        if not os.path.isdir(ydir):
            continue
        try:
            y = int(os.path.basename(ydir))
        except ValueError:
            continue
        ph = phase(oni_year.get(y, 0.0))
        for f in sorted(glob.glob(os.path.join(ydir, '*.dat'))):
            if not os.path.basename(f).startswith('bwp'):
                continue
            try:
                rec = read_storm(f)
            except Exception:
                continue
            if len(rec) < 2:
                continue
            v = max(r[3] for r in rec)
            if v >= 34.0:
                peaks[ph].append(float(v))
    for d in ZI:
        for f in sorted(glob.glob(os.path.join(d, '**', '*.dat'), recursive=True)):
            b = os.path.basename(f)
            m = re.search(r'(\d{4})', b.split()[0])
            if not m:
                continue
            y = int(m.group(1))
            ph = phase(oni_year.get(y, 0.0))
            try:
                rec = read_storm(f)
            except Exception:
                continue
            if len(rec) < 2:
                continue
            v = max(r[3] for r in rec)
            if v >= 34.0:
                peaks[ph].append(float(v))

    print('观测(西太 bwp + 自评), 按 ENSO 相位分组:')
    tot = sum(len(v) for v in peaks.values())
    obs_share = {}
    for k, nm in (('el', '厄尔尼诺'), ('neu', '中性'), ('la', '拉尼娜')):
        a = np.asarray(peaks[k], float)
        if a.size == 0:
            continue
        share = float((a >= 130.0).mean())
        obs_share[k] = share
        print('   %-6s n=%-4d | 130+ 占比 %.0f%% | 峰值 P50 %.0f P90 %.0f'
              % (nm, a.size, 100 * share, np.percentile(a, 50), np.percentile(a, 90)))
    print()
    w = {k: len(peaks[k]) / max(tot, 1) for k in peaks}
    print('观测的相位频率(作为权重): ' + ' '.join('%s=%.0f%%' % (k, 100 * w[k]) for k in ('el', 'neu', 'la')))
    o_w = sum(w[k] * obs_share.get(k, 0.0) for k in w)
    m_w = sum(w[k] * MODEL[k] for k in w)
    print()
    print('**同一权重下**: 观测混合 130+ = %.1f%%  |  模型混合 130+ = %.1f%%  |  比 %.2fx'
          % (100 * o_w, 100 * m_w, m_w / max(o_w, 1e-9)))
    print()
    print('另: 不加权的观测总体 130+ = %.0f%%(此前引用的 18%%)'
          % (100 * np.mean([x for v in peaks.values() for x in (np.asarray(v) >= 130.0)])))


if __name__ == '__main__':
    main()
