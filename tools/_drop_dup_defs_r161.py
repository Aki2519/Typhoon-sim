# -*- coding: utf-8 -*-
"""轮次 161: 删掉 `core.py` 里 `_gauss_kernel`/`_lowpass` 的**死的第一份**。

背景: 两个名字各定义两次, 两份**逐字节相同**, 而 Python 后面那份生效 ⇒
**改第一份等于没改**(perf-reviewer 在 task-10 报的地雷)。

为什么可以删: 第二份生效 ⇒ 删第一份**零行为变化**。准入 = 双模式 `free_hash` 逐位不变。

⚠ 这份脚本带**内容守卫**: 每一处边界都对不上就**直接放弃、不写文件**。
因为第一份 `_gauss_kernel` 与第一份 `_lowpass` 之间夹着 `try: from scipy import
ndimage as _SCI_NDI`(**必须保留**), 而 `_lowpass` #1 之后紧跟着 `_EDDY_CACHE` 与
`_up2`(**也必须保留**)。

用法: python tools/_drop_dup_defs_r161.py [--apply]
      (不给 --apply 只做干跑与守卫检查)
"""
import hashlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'simulator', 'simcore', 'core.py')

GAUSS1 = (1321, 1329)      # def + 体 + 尾部空行
LOW1 = (1336, 1365)        # def + 体 + 尾部空行


def get(lines, n):
    return lines[n - 1]


def body(lines, start1):
    i = start1 - 1
    j = i + 1
    while j < len(lines):
        s = lines[j]
        if s and not s.startswith((' ', '\t')):
            break
        j += 1
    while j - 1 > i and not lines[j - 1].strip():
        j -= 1
    return '\n'.join(lines[i:j])


def main():
    apply = '--apply' in sys.argv
    raw = io.open(SRC, encoding='utf-8').read()
    lines = raw.split('\n')

    print('=== 守卫检查 ===')
    checks = [
        (get(lines, 1321).startswith('def _gauss_kernel'), 1321, 'def _gauss_kernel'),
        (get(lines, 1327).strip() == 'return k / k.sum()', 1327, 'return k / k.sum()'),
        (get(lines, 1330).startswith('try:'), 1330, 'try:'),
        ('from scipy import ndimage' in get(lines, 1331), 1331, 'scipy import(必须保留)'),
        (get(lines, 1336).startswith('def _lowpass'), 1336, 'def _lowpass'),
        (get(lines, 1363).strip() == 'return o', 1363, 'return o'),
        ('_EDDY_CACHE' in get(lines, 1367), 1367, '_EDDY_CACHE(必须保留)'),
        (get(lines, 1370).startswith('def _up2'), 1370, 'def _up2(必须保留)'),
        (get(lines, 1409).startswith('def _gauss_kernel'), 1409, 'def _gauss_kernel(生效那份)'),
        (get(lines, 1424).startswith('def _lowpass'), 1424, 'def _lowpass(生效那份)'),
    ]
    ok = True
    for good, ln, what in checks:
        print('  %s  L%-5d %s' % ('OK ' if good else 'FAIL', ln, what))
        ok = ok and good
    if not ok:
        print('\n守卫不过 => 放弃(不写文件)。行号可能已漂, 请重新定位。')
        return 1

    print()
    print('=== 两份是否逐字节相同(删的前提) ===')
    for nm, s1, s2 in (('_gauss_kernel', 1321, 1409), ('_lowpass', 1336, 1424)):
        b1, b2 = body(lines, s1), body(lines, s2)
        h1 = hashlib.sha256(b1.encode()).hexdigest()[:16]
        h2 = hashlib.sha256(b2.encode()).hexdigest()[:16]
        print('  %-14s #1 sha=%s  #2 sha=%s  相同=%s' % (nm, h1, h2, b1 == b2))
        if b1 != b2:
            print('  => 不同! 放弃。')
            return 1

    print()
    print('将删除: _gauss_kernel#1 %d-%d, _lowpass#1 %d-%d (共 %d 行)'
          % (GAUSS1[0], GAUSS1[1], LOW1[0], LOW1[1],
             GAUSS1[1] - GAUSS1[0] + 1 + LOW1[1] - LOW1[0] + 1))
    if not apply:
        print('(干跑; 加 --apply 才写文件)')
        return 0

    # 降序删, 保持前面的行号不漂
    for a, b in sorted([GAUSS1, LOW1], reverse=True):
        del lines[a - 1:b]
    io.open(SRC, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
    print('已写入; 新行数 =', len(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
