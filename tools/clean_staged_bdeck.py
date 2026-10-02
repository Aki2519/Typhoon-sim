# -*- coding: utf-8 -*-
"""把实时记录目录(sim_typhoon/<年>/<年><洋区>/*.dat)做两件事:

  1. **同一时次去重**, 保留**最后**一条 —— 生成时刻那条"步前"记录与主循环同一步补的
     "步后"记录标同一个 t(实测 b-deck 头两行同标 2025010806 却是 20kt/31kt)。
     新写的文件已经有这道保险(tools/sim_year.bdeck_lines), 这个工具用来收拾
     **改动之前**跑出来的旧文件。
  2. **按洋区重新编号**(与 sim_year.finalize_bdeck 同一口径), 顺便统计:
     各洋区系统数 / 命名数 / 峰值 / ACE。

用法:
  python tools/clean_staged_bdeck.py --dir sim_typhoon            # 只清理+报告
  python tools/clean_staged_bdeck.py --dir sim_typhoon --years 2025
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def clean_file(path):
    """→ (保留行数, 丢弃行数)"""
    with open(path, encoding='utf-8', errors='replace') as f:
        rows = [l.rstrip('\n') for l in f if l.strip()]
    if not rows:
        return 0, 0
    out = []
    for ln in rows:
        cols = ln.split(',')
        lab = cols[2].strip() if len(cols) > 2 else ''
        if out:
            prev = out[-1].split(',')
            if len(prev) > 2 and prev[2].strip() == lab:
                out[-1] = ln            # 同时次: 用后一条覆盖
                continue
        out.append(ln)
    drop = len(rows) - len(out)
    if drop:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
    return len(out), drop


def ace_of(path):
    """标准 ACE = Σ v²/1e4(每条 6h 记录)。"""
    s = 0.0
    with open(path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            c = ln.split(',')
            if len(c) < 9:
                continue
            try:
                v = float(c[8])
            except Exception:
                continue
            if v > 300:
                continue
            s += v * v / 1e4
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=os.path.join(ROOT, 'sim_typhoon'))
    ap.add_argument('--years', default=None, help='逗号分隔, 默认全部')
    a = ap.parse_args(argv)
    years = ([y.strip() for y in a.years.split(',')] if a.years
             else sorted(os.listdir(a.dir)) if os.path.isdir(a.dir) else [])
    tot_drop = 0
    for y in years:
        yp = os.path.join(a.dir, y)
        if not os.path.isdir(yp):
            continue
        print('=== %s ===' % y)
        for bd in sorted(os.listdir(yp)):
            dp = os.path.join(yp, bd)
            if not os.path.isdir(dp):
                continue
            files = sorted(f for f in os.listdir(dp) if f.endswith('.dat'))
            named, peak, ace, drop = set(), 0.0, 0.0, 0
            for f in files:
                fp = os.path.join(dp, f)
                n, d = clean_file(fp)
                drop += d
                ace += ace_of(fp)
                with open(fp, encoding='utf-8', errors='replace') as fh:
                    for ln in fh:
                        c = ln.split(',')
                        if len(c) < 12:
                            continue
                        ident = c[11].strip()
                        # 末列 = 低压号(01S)/invest 号(90W) 都不是名字; 其余(如 Dikeledi)才是
                        if ident and not re.match(r'^\d{2}[A-Za-z]$', ident):
                            named.add(ident.upper())
                        try:
                            peak = max(peak, float(c[8]))
                        except Exception:
                            pass
            tot_drop += drop
            print('  %-10s 系统 %3d | 命名 %3d | 峰值 %5.0f kt | ACE %6.1f%s'
                  % (bd, len(files), len(named), peak, ace,
                     ('  (去重 %d 行)' % drop) if drop else ''))
    print('去重合计 %d 行' % tot_drop)
    return tot_drop


if __name__ == '__main__':
    main()
