# -*- coding: utf-8 -*-
"""轮次 161: 定位 `_lowpass`/`_gauss_kernel` 的重复定义, 并打印"两份之间夹着什么",
以便安全删除**死的那一份**(第二份生效 ⇒ 删第一份零行为变化)。只读, 不改文件。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'simulator', 'simcore', 'core.py')
L = io.open(SRC, encoding='utf-8').read().split('\n')


def top_defs():
    """所有顶层 def/class/赋值 的行号与名字"""
    out = []
    for i, l in enumerate(L):
        m = re.match(r'^(def|class)\s+(\w+)', l)
        if m:
            out.append((i + 1, m.group(2), m.group(1)))
    return out


def span(start1):
    """从顶层 def 行到下一个顶层语句之前(去掉尾部空行)"""
    i = start1 - 1
    j = i + 1
    while j < len(L):
        s = L[j]
        if s and not s.startswith((' ', '\t')):
            break
        j += 1
    while j - 1 > i and not L[j - 1].strip():
        j -= 1
    return (i + 1, j)


names = ('_gauss_kernel', '_lowpass')
all_defs = top_defs()
spans = {}
for nm in names:
    sp = [span(a) for (a, n, k) in all_defs if n == nm]
    spans[nm] = sp
    print('%-14s 共 %d 处: %s' % (nm, len(sp), sp))

print()
print('=== 第一份与第二份之间夹着的**顶层**定义 ===')
lo = min(s[0] for v in spans.values() for s in v)
hi = max(s[0] for v in spans.values() for s in v)
inside = [(a, n, k) for (a, n, k) in all_defs
          if lo <= a <= hi and n not in names]
for a, n, k in inside:
    e = span(a)[1]
    print('  %5d-%5d  %s %s   (%d 行)' % (a, e, k, n, e - a + 1))

print()
print('=== 删除候选(第一份)的精确范围 ===')
for nm in names:
    a, b = spans[nm][0]
    print('  %-14s 行 %d-%d (%d 行)  首行: %s' % (nm, a, b, b - a + 1, L[a - 1].strip()))

print()
print('=== 安全边界检查 ===')
for nm in names:
    (a1, b1), (a2, b2) = spans[nm][0], spans[nm][1]
    gap_a, gap_b = b1 + 1, a2 - 1
    print('  %-14s 第一份 %d-%d, 第二份 %d-%d, 中间空档 %d-%d'
          % (nm, a1, b1, a2, b2, gap_a, gap_b))
    if gap_b >= gap_a:
        mid = [x for x in inside if gap_a <= x[0] <= gap_b]
        print('        空档里的顶层定义: %s' % (mid if mid else '无'))
