# -*- coding: utf-8 -*-
"""轮次 162: 把 4 个 .py 里过期的"本机 9GB"改成实测口径。

实测(2026-09-30, verifier 审计): 物理 **15.74 GiB**、**可用常年只剩 1-3 GiB**、
Windows 侧 **24 逻辑核**(WSL 只见 8)。
⚠ 真正的约束是**可用量**(1-3 GiB), 不是总量 —— 旧文"9GB"是 WSL 侧读数, 已作废。
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAIRS = [
    ('simulator/simcore/rewind.py',
     '#: 默认内存上限(字节)。9GB 机器留足余量; 实测一份满载快照约 7 MB(见测试④)',
     '#: 默认内存上限(字节)。为"可用内存只有 1-3 GiB"的本机留足余量\n'
     '#: (物理 15.74 GiB, 但可用常年 1-3 GiB; 旧文写的 9GB 是 WSL 侧读数, 已作废);\n'
     '#: 实测一份满载快照约 7 MB(见测试④)'),
    ('tools/multi_year_check.py',
     '# **断点续跑**(轮次 110 加): 本机 9GB 内存 + 用户训练作业, 15 种子长验收',
     '# **断点续跑**(轮次 110 加): 本机可用内存只有 1-3 GiB(物理 15.74 GiB)\n'
     '# + 用户训练作业, 15 种子长验收'),
    ('tools/perf_baseline_r158.py',
     '⚠ 内存纪律: 本机 9GB 且有 OOM 前科 ---- 这个脚本**一次只跑一个进程**,',
     '⚠ 内存纪律: 本机物理 15.74 GiB 但**可用常年只剩 1-3 GiB**、有 OOM 前科 ----\n'
     '这个脚本**一次只跑一个进程**,'),
    ('tools/run_tests_perfile.py',
     '为什么要它: 本机 9GB 内存 + 用户的训练作业会让 `pytest -q` 在跑到一半时',
     '为什么要它: 本机可用内存只有 1-3 GiB(物理 15.74 GiB)+ 用户的训练作业,\n'
     '会让 `pytest -q` 在跑到一半时'),
]

for rel, old, new in PAIRS:
    p = os.path.join(ROOT, rel)
    s = io.open(p, encoding='utf-8').read()
    if old not in s:
        print('!! 找不到原文(跳过): %s' % rel)
        continue
    io.open(p, 'w', encoding='utf-8', newline='\n').write(s.replace(old, new, 1))
    print('已改: %s' % rel)
