"""一年有多少扰动?(用户问题)

定义(与 ATCF 台账一致):
  扰动 = 出现在 b-deck 里的系统(含只到 DB/WV 的)
  发展 = 该系统一生中任意一报 >= 34 kt(TS 及以上)

观测侧: 自评/2025、自评/2026 各是一年;xrq/wp 按文件名末 4 位分年。
模型侧: 跑一年, 数 sim['tcs'] 里的系统数与其中达到 34kt 的个数。
"""
import glob
import os
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
from stat_rate_table import read_storm          # noqa: E402


def scan(paths):
    tot = dev = 0
    for f in paths:
        try:
            rec = read_storm(f)
        except Exception:
            continue
        if len(rec) < 2:
            continue
        tot += 1
        if max(r[3] for r in rec) >= 34.0:
            dev += 1
    return tot, dev


def year_of(p):
    # 文件名形如 bcp011950.dat / bcp012025 Iona.dat ⇒ **末尾 4 位**才是年份
    base = os.path.basename(p).split()[0]
    base = base[:-4] if base.lower().endswith('.dat') else base
    m = re.search(r'(\d{4})$', base)
    return m.group(1) if m else '?'


print('=' * 74)
print('【观测】')
for nm, pat in (('自评 2025', r'G:\气象\数据\自评\2025\**\*.dat'),
                ('自评 2026', r'G:\气象\数据\自评\2026\**\*.dat')):
    fs = glob.glob(pat, recursive=True)
    t, d = scan(fs)
    print('  %-10s 扰动 %3d 个/年, 其中发展成 TS+ 的 %3d 个 (%.0f%%)'
          % (nm, t, d, 100.0 * d / t if t else 0))

wp = sorted(glob.glob(r'G:\气象\数据\xrq\wp\*.dat'))
by_year = {}
for f in wp:
    by_year.setdefault(year_of(f), []).append(f)
print('  xrq/wp 归档: %d 个文件, 覆盖 %d 个年份' % (len(wp), len(by_year)))
ys = sorted(by_year)
rows = []
for y in ys:
    t, d = scan(by_year[y])
    rows.append((y, t, d))
if rows:
    print('     年份   扰动  发展')
    for y, t, d in rows:
        print('     %s  %4d  %4d' % (y, t, d))
    tt = sum(r[1] for r in rows)
    dd = sum(r[2] for r in rows)
    print('  xrq/wp 合计 %d 个扰动 / %d 年 = **年均 %.0f 个**, 发展占比 %.0f%%'
          % (tt, len(rows), tt / len(rows), 100.0 * dd / tt if tt else 0))

print('=' * 74)
print('【模型】跑一年(365 天)...')
import numpy as np                                   # noqa: E402
import os as _os                                     # noqa: E402
_os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V               # noqa: E402

sim = V.init({'start': '2025-01-01 00', 'seed': 20250801, 'envStepH': 6.0,
              'params': {'precision': '100ms', 'dataMode': True}})
for _ in range(int(365 * 24 / 6)):
    V.sim_step(sim, 6.0)
tot = len(sim['tcs'])
dev = sum(1 for t in sim['tcs']
          if t.get('track') and max(float(q['vmax']) for q in t['track']) >= 34.0)
dist = sum(1 for t in sim['tcs'] if t.get('disturb'))
named = sum(1 for t in sim['tcs'] if t.get('named') or t.get('wasNamed'))
print('  模型一年: 系统 %d 个, 其中达到 34kt+ 的 %d 个 (%.0f%%), 纯扰动标记 %d, 命名 %d'
      % (tot, dev, 100.0 * dev / tot if tot else 0, dist, named))
