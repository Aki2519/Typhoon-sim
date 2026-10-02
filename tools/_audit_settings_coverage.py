# -*- coding: utf-8 -*-
"""审计: 设置界面**缺少**了哪些配置项(即 AppConfig 里有、但 UI 改不到的)。

做法(纯静态, 不启动应用):
  1. 取 `AppConfig` 的全部字段名;
  2. 用 AST 从 `app/settings.py` 的 `_layout()` 里把**所有**被 UI 引用的 key 抓出来
     (`('checkbox', k)` / `('field', k)` / `('toggle', k, ..., label)` / `('checkbox_now', k)`);
  3. 差集 = 有配置、没界面入口的项。
另外反过来查一次: UI 引用但 AppConfig 里没有的 key(幽灵项)。
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SRC = io.open(os.path.join(ROOT, 'app', 'settings.py'), encoding='utf-8').read()
# ⚠⚠ 2026-10-01 教训: 第一版只遍历 , 于是把实现在**根目录 main.py** 的
#  误判成"全仓零引用的死字段", 还据此拒绝给它加 UI 入口。
# 判断"有没有实现"必须**扫全仓**(至少 app/ + tools/ + 根目录)。
# (原来这里有个从未被使用的 SCAN_DIRS; 已删 —— 注释承诺的"扫全仓"没有实现,
#  当前实现只做 AppConfig 字段 - settings.py UI key 的差集, 不需要它。)

KINDS = {'checkbox', 'field', 'toggle', 'checkbox_now', 'info', 'section',
         'button', 'number'}


def ui_keys():
    """从 settings.py 里抓 UI 引用到的配置 key。"""
    tree = ast.parse(SRC)
    keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or not node.elts:
            continue
        head = node.elts[0]
        if not (isinstance(head, ast.Constant) and head.value in KINDS):
            continue
        if head.value in ('section', 'info', 'button'):
            continue
        if len(node.elts) < 2:
            continue
        second = node.elts[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            keys.append(second.value)
    return keys


def main():
    from app.config import AppConfig
    from dataclasses import fields
    cfg = {f.name for f in fields(AppConfig)}
    ui = ui_keys()
    seen = set(ui)

    print('AppConfig 字段 %d 个; UI 引用 %d 处(去重 %d 个)'
          % (len(cfg), len(ui), len(seen)))
    print()
    missing = sorted(cfg - seen)
    print('=== A. 有配置、但设置界面**没有入口** (%d 个) ===' % len(missing))
    d = AppConfig()
    skip_hint = {
        'tn': '自定义名称表(内部数据, 不该做成控件)',
        'sim_path_tys': '内部缓存', 'smooth_path_segments': '已有',
    }
    for k in missing:
        v = getattr(d, k, None)
        print('   %-26s 默认=%-16r %s' % (k, v, skip_hint.get(k, '')))
    print()
    ghost = sorted(seen - cfg)
    print('=== B. UI 引用但 AppConfig 里**不存在** (%d 个) ===' % len(ghost))
    for k in ghost:
        print('   %s' % k)
    print('   (空 = 没有幽灵项)')
    print()
    print('RESULT missing=%d ghost=%d' % (len(missing), len(ghost)))


if __name__ == '__main__':
    main()
