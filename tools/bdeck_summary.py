# -*- coding: utf-8 -*-
"""**重新生成归档摘要**(`BDECK_SUMMARY.txt`)并与落盘文件数对账。

为什么需要它: 任务 F 的归档缺陷有两半 ——
  ① 命名: "命名了但从未拿到低压号"的系统留在正式目录里挂着扰动码文件名
     (`wpE52025.dat`)。轮次 124 已修 `finalize_bdeck`(判据从"有低压号"改成
     "不是纯扰动"), 复验 `2025wp/` 内字母命名文件 **26 -> 0**。
  ② **计数**: `README.md` 说摘要是"权威口径: 直接数落盘的 b-deck", 而实测
     `2025wp/` 有 213 个文件、摘要却写"系统 65" —— 摘要与产物**不一致**。
     本工具就是那个"直接数落盘文件"的权威口径: 每个目录一个文件 = 一个系统,
     命名数 = 该文件里出现过名字的系统数, 峰值 = 该文件全生命期最大 vmax,
     ACE = sum(vmax^2)/1e4(6h 步, 与内核 `tc['ace']` 同式)。

用法:
    python tools/bdeck_summary.py output/sim/2025 --year 2025            # 只打印
    python tools/bdeck_summary.py output/sim/2025 --year 2025 --write    # 覆盖摘要
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

#: 第 12 列形如 `03W` / `90W` 的**不是名字**(与 finalize_bdeck/clean_staged_bdeck 同口径)
_NOTNAME = re.compile(r'^[0-9]{2}[A-Z]$')


def scan_file(path):
    """→ (max_vmax, named?, ace) —— 一个文件 = 一个系统。"""
    mx = 0.0
    ace = 0.0
    named = False
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for line in f:
                cols = [c.strip() for c in line.split(',')]
                if len(cols) < 12:
                    continue
                try:
                    v = float(cols[8])
                except ValueError:
                    continue
                mx = max(mx, v)
                ace += v * v * 1e-4
                nm = cols[11]
                if nm and not _NOTNAME.match(nm) and not nm.replace('.', '').isdigit():
                    named = True
    except OSError:
        return 0.0, False, 0.0
    return mx, named, ace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', help='归档根目录, 如 output/sim/2025(里面是 <year>/<year><basin>/)')
    ap.add_argument('--year', type=int, default=2025)
    ap.add_argument('--write', action='store_true', help='覆盖 BDECK_SUMMARY.txt')
    a = ap.parse_args()

    ydir = os.path.join(a.root, str(a.year))
    if not os.path.isdir(ydir):
        print('找不到 %s' % ydir)
        return 1
    lines = ['=== %d ===' % a.year]
    tot_sys = tot_named = 0
    for sub in sorted(os.listdir(ydir)):
        sdir = os.path.join(ydir, sub)
        if not os.path.isdir(sdir):
            continue
        files = sorted(glob.glob(os.path.join(sdir, '*.dat')))
        n = mx = ace = 0
        named = 0
        for p in files:
            m, nm, ac = scan_file(p)
            n += 1
            mx = max(mx, m)
            ace += ac
            named += 1 if nm else 0
        tot_sys += n
        tot_named += named
        lines.append('  %-14s 系统 %3d | 命名 %3d | 峰值 %5.0f kt | ACE %6.1f'
                     % (sub, n, named, mx, ace))
    lines.append('  合计           系统 %3d | 命名 %3d' % (tot_sys, tot_named))
    # **一致性检查**: 正式目录里不该再有字母命名的文件名
    bad = []
    for sub in sorted(os.listdir(ydir)):
        if sub.endswith('_invest'):
            continue
        sdir = os.path.join(ydir, sub)
        if not os.path.isdir(sdir):
            continue
        for p in glob.glob(os.path.join(sdir, '*.dat')):
            b = os.path.basename(p)
            if re.search(r'[A-Z]', b):
                bad.append(os.path.join(sub, b))
    lines.append('  正式目录里字母命名的文件: **%d**(应为 0)' % len(bad))
    for b in bad[:10]:
        lines.append('    ! %s' % b)
    txt = '\n'.join(lines) + '\n'
    print(txt)
    if a.write:
        outp = os.path.join(a.root, 'BDECK_SUMMARY.txt')
        with open(outp, 'w', encoding='utf-8') as f:
            f.write(txt)
        print('已写入 %s' % outp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
