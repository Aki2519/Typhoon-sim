"""EX(变性/温带)相关是否正常? —— 模型 vs 观测的阶段码分布。

数据: 模型 output/sim/<年>/<年>/<年><海盆>/*.dat; 观测 自评 + xrq。
阶段码在 b-deck 第 11 列(0-based index 10): WV/DB/LO/TD/TS/TY/ST/EX/SS/SD/MD。
"""
import collections
import glob
import os
import re
import sys

sys.path.insert(0, '.')

MODEL = r'output\sim\2025\2025'
OBS = [r'G:\气象\数据\自评\2025', r'G:\气象\数据\自评\2026',
       r'G:\气象\数据\xrq\2025', r'G:\气象\数据\xrq\wp']


def phases(paths, maxf=None):
    cnt = collections.Counter()
    syscnt = collections.Counter()
    nf = 0
    for p in paths:
        try:
            with open(p, encoding='ascii', errors='replace') as f:
                rows = [l for l in f if l.strip()]
        except OSError:
            continue
        if not rows:
            continue
        nf += 1
        seen = set()
        for l in rows:
            c = [x.strip() for x in l.split(',')]
            if len(c) < 11:
                continue
            ph = c[10].upper()
            cnt[ph] += 1
            seen.add(ph)
        for ph in seen:
            syscnt[ph] += 1
        if maxf and nf >= maxf:
            break
    return cnt, syscnt, nf


def show(tag, cnt, syscnt, nf):
    tot = sum(cnt.values()) or 1
    print('--- %s(%d 个文件, %d 条记录)---' % (tag, nf, tot))
    keys = ['DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD']
    parts = []
    for k in keys:
        parts.append('%s %4.1f%%(%d)' % (k, 100 * cnt.get(k, 0) / tot, cnt.get(k, 0)))
    print('   记录占比: ' + '  '.join(parts[:6]))
    print('             ' + '  '.join(parts[6:]))
    ex = syscnt.get('EX', 0)
    print('   **有 EX 记录的系统数: %d** (占 %d 个系统的 %.0f%%)'
          % (ex, nf, 100 * ex / max(nf, 1)))


def main():
    mp = glob.glob(os.path.join(MODEL, '*', '*.dat'))
    print('=== 模型: %s ===' % MODEL)
    show('模型', *phases(mp))
    print()
    print('=== 观测(自评 2025/2026 + xrq 2025 + xrq/wp)===')
    op = []
    for d in OBS:
        op += glob.glob(os.path.join(d, '**', '*.dat'), recursive=True)
    show('观测', *phases(op, maxf=2500))
    print()
    print('判读要点: ① 模型有没有 EX 记录; ② EX 记录占比与观测差多少;')
    print('          ③ 有 EX 的系统数占比(观测里西太变性台风每年都有若干个)。')


if __name__ == '__main__':
    main()
