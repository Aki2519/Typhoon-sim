# -*- coding: utf-8 -*-
"""**从验收产物重算汇总**(轮次 136, 上线前的最后一道对账工具)。

为什么要单独一个工具, 而不是直接用 `multi_year_check` 自己打出来的汇总:
  · 那个汇总是**跑动进程内存里的解析器**算的。若验收期间修过解析器(轮次 134 就修了
    `parse_rec` 的锚点), 它的输出**仍然缺字段**, 与修好的解析器不一致;
  · 而且它的"均值"是"有这个字段的种子"的均值 —— 缺字段时会**静默**只对一部分种子求
    (轮次 135 已加 `n=` 与告警, 但那是新进程才生效)。
⇒ **一切以逐种子原文为准**: 本工具只读验收文件里每个种子的完整输出, 用**当前**的
`parse_rec` 重算, 并把 `n` 与"范围是否跨过观测"一起打出来。

用法:
    python tools/summarize_acceptance.py output/sim/_reference/final2_nh_15seed.txt
    python tools/summarize_acceptance.py final2_nh_15seed.txt final2_sh_15seed.txt
        # 给两个窗口时会额外算"两窗口合计 x365/200"的年量

判读规则(本会话既定): **若范围跨过观测值, 该指标与观测无显著差别**, 不得再拿单点差取舍。

⚠⚠ **"一生最高纬/到过≥28/净向极/寿命"这四行在 1 月窗口下不可用**:
`tools/verify_year.py` 里这几项**显式跳过南半球风暴**(`if mean(lat) < 0: continue`)——
于是 1 月窗算的是"北半球淡季的那几场", 与"观测年度 NH 分布"不可比(会显示严重偏低)。
峰值分布那几行(100-129 / 130+ / P50 / P90 / P95)是**全海盆**的, 两个窗口都可用。
⇒ 判读时: 1 月窗只看 E 与峰值分布; A 只用 7 月窗(或全年)。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import multi_year_check as MYC  # noqa: E402

#: (字段, 名称, 观测区间(可比口径), 是否"落在区间内即通过")
#: 观测口径见 OBS_REFERENCE.md 3b/3c —— 100-129 / 130+ 必须用**单年多海盆**。
METRICS = (
    ('gen', '真年生成量(100天窗口内)', None, None),
    ('grate', '年内发展率%', (48.0, 48.0), False),
    ('maxlat', '最高纬中位(度)', (25.0, 26.0), True),
    ('p28', '到过>=28 占比%', (40.0, 45.0), True),
    ('dlat', '净向极位移(度)', (10.3, 13.3), True),
    ('life', '寿命中位(h)', (180.0, 222.0), True),
    ('s100', '100-129 占比%', (5.3, 11.1), True),
    ('s130', '130+ 占比%', (12.3, 13.9), True),
    ('p50', '峰值 P50', (75.0, 75.0), False),
    ('p90', '峰值 P90', (128.0, 130.0), True),
    ('p95', '峰值 P95', (147.0, 155.0), True),
    ('total', '保留窗内扰动数(会饱和)', None, None),
    ('dev', '保留窗内发展数', None, None),
)


def load(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        s = f.read()
    # ⚠ 验收文件跑完时, `multi_year_check` 会把**自己的汇总块**追加在末尾
    # (`'=' * 92` + `多种子汇总(...)`)。那些行里含"扰动总数 均值 …"这类文本,
    # 会被 `parse_rec` 当成种子数据去解析 ⇒ `int('均值')` 直接崩
    # (轮次 141 实测: 把同一个文件传两次就触发)。
    # ⇒ 先截掉汇总块, 再按 `>>> seed` 切块。
    for mark in ('=' * 40, '多种子汇总('):
        i = s.find(mark)
        if i >= 0:
            s = s[:i]
    recs = []
    for blk in re.split(r'>>> seed ', s)[1:]:
        sd = blk.split()[0]
        r = MYC.parse_rec(blk, sd)
        recs.append(r)
    return recs


def verdict(v, lo, hi, inside_ok):
    """范围跨过观测 => 无显著差别。"""
    if lo is None:
        return '-'
    a, b = min(v), max(v)
    if a <= lo and b >= lo or a <= hi and b >= hi or (a >= lo and b <= hi):
        return '范围跨过 => 无显著差别' if not inside_ok else '落在区间内'
    if b < lo:
        return '**整体低于观测**'
    return '**整体高于观测**'


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 1
    allrecs = {}
    for p in paths:
        if not os.path.exists(p):
            print('找不到 %s' % p)
            continue
        recs = load(p)
        recs = [r for r in recs if r.get('total') is not None]
        allrecs[os.path.basename(p)] = recs
        print('== %s: %d 个种子 ==' % (os.path.basename(p), len(recs)))
        for k, nm, obs, ok in METRICS:
            v = [r[k] for r in recs if k in r]
            if not v:
                print('   %-24s (无该字段)' % nm)
                continue
            o = ('观测 %s' % ('%.1f-%.1f' % obs if obs[0] != obs[1] else '%.1f' % obs[0])
                 if obs else '')
            print('   %-24s 均值 %7.1f  范围 [%.1f, %.1f]  n=%d/%d  %s  %s'
                  % (nm, sum(v) / len(v), min(v), max(v), len(v), len(recs), o,
                     verdict(v, *(obs if obs else (None, None)), ok)))
        print()
    # 两窗口 => 年量
    if len(allrecs) == 2:
        names = list(allrecs)
        g1 = [r['gen'] for r in allrecs[names[0]] if 'gen' in r]
        g2 = [r['gen'] for r in allrecs[names[1]] if 'gen' in r]
        if g1 and g2:
            per_window = (sum(g1) / len(g1) + sum(g2) / len(g2))
            ann = per_window * 365.0 / 200.0
            print('== 两窗口年量(每个窗口 100 天) ==')
            print('   每窗口均值 %.1f + %.1f = %.1f 个/200 天  => **年生成 %.0f**(目标 250)'
                  % (sum(g1) / len(g1), sum(g2) / len(g2), per_window, ann))
    return 0


if __name__ == '__main__':
    sys.exit(main())
