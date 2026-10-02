# -*- coding: utf-8 -*-
"""逐个测试文件跑 pytest, 汇报每个文件的通过/失败 —— 专治"整轮跑被内存打断"。

为什么要它: 本机可用内存只有 1-3 GiB(物理 15.74 GiB)+ 用户的训练作业,
会让 `pytest -q` 在跑到一半时
`MemoryError` 直接 INTERNALERROR, 连失败清单都打不出来(轮次 107 实测)。
逐个文件跑把峰值内存降到单文件量级, 且失败清单不会丢。

用法: python tools/run_tests_perfile.py            # 全部
      python tools/run_tests_perfile.py --from 40   # 从第 40 个文件开始(分批)
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
ENV = dict(os.environ)
ENV['PYTHONIOENCODING'] = 'utf-8'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='start', type=int, default=0)
    ap.add_argument('--to', dest='stop', type=int, default=10 ** 9)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(ROOT, 'tests', 'test_*.py')))
    bad, tot_ok = [], 0
    for i, f in enumerate(files):
        if i < a.start or i >= a.stop:
            continue
        r = subprocess.run([PY, '-m', 'pytest', f, '-q', '--tb=line',
                            '-p', 'no:cacheprovider'],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=ENV)
        out = (r.stdout or '') + (r.stderr or '')
        last = [ln for ln in out.strip().split('\n') if ln.strip()]
        tail = last[-1][:110] if last else '(无输出)'
        ok = ('failed' not in tail and 'error' not in tail.lower()
              and r.returncode == 0 and 'passed' in tail)
        # passed 数
        n_ok = 0
        for ln in last:
            if 'passed' in ln:
                try:
                    n_ok = int(ln.split(' passed')[0].split()[-1])
                except Exception:
                    pass
        tot_ok += n_ok
        print('%-46s %s' % (os.path.basename(f), tail), flush=True)
        if not ok:
            bad.append((os.path.basename(f), tail))
    print()
    print('=' * 90)
    print('通过合计 %d | 有问题的文件 %d 个' % (tot_ok, len(bad)))
    for nm, t in bad:
        print('  !! %-44s %s' % (nm, t))


if __name__ == '__main__':
    main()
