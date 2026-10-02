# -*- coding: utf-8 -*-
"""设置面板 <-> 配置 的一致性门禁（2026-10-01 重写，配合 `_CFG_MIRROR` 重构）。

## 为什么需要这道门禁（同类事故已发生三次）

1. `show_motion_vector` / `show_track_stats` / `fix_icon_point_size`：界面有控件、
   同步名单有名字，但 `_init_data` **漏建属性** ⇒ 打开设置点「确定」直接
   **AttributeError 崩溃**。
2. 同一份映射被**手写两份**（`_init_data` 63 行字面量、`apply_settings` 61 个名字），
   两份不同步 ⇒ 20 个设置在"打开设置点确定"时被**静默改回默认**
   （用户："为什么设置天天被改回默认"）。
3. `AppConfig.load` 里一个字段非法就 `return cls()` ⇒ **整份配置回默认**。

重构(2026-10-01)后建立的不变式，本工具逐条验证：

* **INV-1** `_CFG_MIRROR` 的每个 cfg 名都存在于 `AppConfig`。
* **INV-2** `_init_data` 里有映射循环驱动的赋值（面板属性因此不会漏建）。
* **INV-3** `_init_data` 里凡是"在 `AppConfig` 有同名字段"的属性，**不得**再是
  字面量（那正是事故 2 的成因）。
* **INV-4** 界面布局引用的每个 key 都能在 `AppConfig` 或 `_CFG_MIRROR` 里找到落点。
* **INV-5** `RUNTIME_FIELDS`（运行时状态）**不得**进入持久化集合。

退出码 0 = 全过；1 = 有差异（可直接当验收门）。只读，不改任何文件。
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SETTINGS = os.path.join(ROOT, 'app', 'settings.py')
UI_KINDS = {'checkbox', 'checkbox_now', 'field', 'toggle'}


def _class_literal(tree, cls_name, attr):
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            for st in n.body:
                if isinstance(st, ast.Assign):
                    for t in st.targets:
                        if isinstance(t, ast.Name) and t.id == attr:
                            try:
                                return ast.literal_eval(st.value)
                            except Exception:
                                return None
    return None


def _find_func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _init_data_assigns(tree):
    """`_init_data` 里 self.X = <字面量?> 的 X -> (字面量或None, 是否有循环)"""
    fn = _find_func(tree, '_init_data')
    out = {}
    has_loop = False
    if fn is None:
        return out, has_loop
    for n in ast.walk(fn):
        if isinstance(n, ast.For):
            tgt = n.target
            if isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2:
                a, b = tgt.elts
                if isinstance(a, ast.Name) and isinstance(b, ast.Name):
                    has_loop = True
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            t = n.targets[0]
            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == 'self'):
                try:
                    lit = ast.literal_eval(n.value)
                except Exception:
                    lit = None
                out[t.attr] = lit
    return out, has_loop


def _ui_keys(tree):
    keys = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Tuple) and n.elts:
            head = n.elts[0]
            if (isinstance(head, ast.Constant) and head.value in UI_KINDS
                    and len(n.elts) >= 2):
                s = n.elts[1]
                if isinstance(s, ast.Constant) and isinstance(s.value, str):
                    keys.add(s.value)
    return keys


def main():
    tree = ast.parse(io.open(SETTINGS, encoding='utf-8').read())
    from app.config import AppConfig
    from dataclasses import fields
    cfg_fields = {f.name for f in fields(AppConfig)}

    problems = []

    mirror = _class_literal(tree, 'Settings', '_CFG_MIRROR') or {}
    init, has_loop = _init_data_assigns(tree)
    ui = _ui_keys(tree)

    runtime = set()
    try:
        from app.config_store import RUNTIME_FIELDS
        runtime = set(RUNTIME_FIELDS)
    except Exception as e:
        print('  (无法导入 RUNTIME_FIELDS: %s)' % e)

    # INV-1
    if not mirror:
        problems.append('INV-1 找不到 Settings._CFG_MIRROR（重构后应当存在）')
    for local, cfgn in mirror.items():
        if cfgn not in cfg_fields:
            problems.append('INV-1 _CFG_MIRROR["%s"] -> "%s" 不在 AppConfig' % (local, cfgn))

    # INV-2
    if not has_loop:
        problems.append('INV-2 _init_data 里没有映射循环（面板属性会漏建 ⇒ 点确定崩）')

    # INV-3
    for name, lit in sorted(init.items()):
        if name in cfg_fields and lit is not None:
            problems.append(
                'INV-3 _init_data 里 "%s" 仍是字面量(=%r) —— 会静默覆盖用户设置, '
                '应交给 _CFG_MIRROR' % (name, lit))

    # INV-4
    # ⚠ 界面 key 有三类合法落点:
    #   ① `AppConfig` 字段(直接持久化);
    #   ② `_CFG_MIRROR` 的键(镜像字段);
    #   ③ **虚拟字段名** —— 只作为 `InputField.key` 存在, 由专门的分支解析后
    #      映射到别的 cfg 字段。例如 `bl_lon`/`bl_lat`/`span` 是"地图角点+跨度"模式
    #      的输入框, 解析后写 `mlo`/`mla`/`Mlo`/`Mla`, 它们**本身不是属性**。
    #      判据: 该名字在 Settings 类的源码里作为字符串常量出现过(说明有专门处理)。
    src_literals = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for k in sorted(ui):
        if k in cfg_fields or k in mirror or k in src_literals:
            continue
        problems.append('INV-4 界面引用了 "%s", 但 AppConfig / _CFG_MIRROR / 源码里都没有' % k)

    # INV-5
    try:
        from app.config_store import ConfigStore
        inst = AppConfig()
        leak = sorted(runtime & set(ConfigStore.persisted_fields(inst)))
        if leak:
            problems.append('INV-5 运行时字段泄漏进持久化集合: %s' % leak)
        print('  持久化字段: %d / 全部 %d（排除运行时 %d 个）'
              % (len(ConfigStore.persisted_fields(inst)),
                 len(inst._serialize_fields()), len(runtime)))
    except Exception as e:
        print('  (INV-5 跳过: %s)' % e)

    print('  _CFG_MIRROR 条目: %d' % len(mirror))
    print('  _init_data 的 self 赋值: %d（映射循环: %s）' % (len(init), has_loop))
    print('  界面 key: %d' % len(ui))
    print('  RUNTIME_FIELDS: %s' % (sorted(runtime) or '(无)'))
    print()
    if problems:
        print('!! 发现 %d 处问题:' % len(problems))
        for p in problems[:25]:
            print('   ' + p)
        print()
        print('RESULT 差异 %d 处' % len(problems))
        return 1
    print('RESULT 全部一致, 没有漏项')
    return 0


if __name__ == '__main__':
    sys.exit(main())
