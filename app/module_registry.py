# app/module_registry.py
"""**模块系统**（用户 2026-10-14）。

把"不属于程序基础功能"的东西做成**可启用/停用**的模块：停用之后，
它的功能栏按钮与界面入口都不再出现，设置也从主设置里剥离出来、
收进模块自己的设置界面。

约定：
  · 开关存在 `cfg.modules`（dict[str, bool]），**默认全部启用** ——
    这样"没动过模块系统"的观感与以前完全一致（回归面最小）。
  · 每个模块声明自己的功能栏按钮 key（`button`）与打开设置的方式（`open_settings`）。
  · 判定统一走 `is_enabled(sim, mid)`；`control_panel` / 各 mixin 只认它，
    不再各自读零散开关（例如原来的 `cfg.show_offscreen_boxes`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ModuleDef:
    mid: str                       # 稳定 id（存 cfg.modules 的键）
    name: str                      # 显示名
    desc: str                      # 简介（对应台风列表里的"台风消息"那一列）
    default: bool = True           # 默认是否启用
    button: Optional[str] = None   # 功能栏按钮 key（停用时按钮不出现）
    open_settings: Optional[str] = None   # 'dialog:<属性名>' / 'settings:<tab>' / 'script'


#: 注册表。顺序 = 模块界面里的显示顺序。
MODULES: List[ModuleDef] = [
    ModuleDef(
        mid='offscreen',
        name='域外风暴小窗',
        desc='屏幕边缘用白框显示屏幕外的风暴（不计算 ACE）；可多选、最多 5 个',
        button='offscreen',
        open_settings='dialog:offscreen',
    ),
    ModuleDef(
        mid='scriptviz',
        name='脚本可视化编辑',
        desc='右侧面板 + 线性时间轴：拖动编辑 target 时间段、变速点与 jump',
        button='script',
        open_settings='script',
    ),
    ModuleDef(
        mid='landfall_merge',
        name='登陆汇总',
        desc='把时间地点很近、强度相同的一串弱登陆并成一次（按时间或地点判据）',
        open_settings='settings:3',
    ),
    ModuleDef(
        mid='charts',
        name='统计与图表',
        desc='ACE 曲线等统计图表入口',
        button='ace_chart',
        open_settings='dialog:ace_chart',
    ),
    ModuleDef(
        mid='paint',
        name='绘画工具',
        desc='在海图上手绘（做视频封面/示意图用），不参与模拟',
        button='paint',
        open_settings='dialog:paint_dialog',
    ),
]

_BY_ID: Dict[str, ModuleDef] = {m.mid: m for m in MODULES}


def get(mid: str) -> Optional[ModuleDef]:
    return _BY_ID.get(mid)


def _cfg(sim):
    return getattr(sim, 'cfg', None)


def is_enabled(sim, mid: str) -> bool:
    """模块是否启用。没配置过 ⇒ 用模块自己的默认值（默认全开）。"""
    mod = _BY_ID.get(mid)
    if mod is None:
        return True
    cfg = _cfg(sim)
    table = getattr(cfg, 'modules', None) if cfg is not None else None
    if isinstance(table, dict) and mid in table:
        return bool(table[mid])
    # 兼容：旧配置里的 `show_offscreen_boxes` 仍生效（不丢用户已有的选择）
    if mid == 'offscreen':
        old = getattr(sim, 'show_offscreen_boxes', None)
        if old is None and cfg is not None:
            old = getattr(cfg, 'show_offscreen_boxes', None)
        if old is not None:
            return bool(old)
    return bool(mod.default)


def set_enabled(sim, mid: str, on: bool) -> None:
    cfg = _cfg(sim)
    if cfg is None:
        return
    table = getattr(cfg, 'modules', None)
    if not isinstance(table, dict):
        table = {}
        try:
            cfg.modules = table
        except Exception:
            return
    table[mid] = bool(on)
    # 同步到 sim（运行时读取用 `is_enabled(sim, ...)`，这里只是让 cfg 落盘）
    try:
        sim.save_config()
    except Exception:
        pass


def any_enabled(sim) -> bool:
    return any(is_enabled(sim, m.mid) for m in MODULES)


def open_module_settings(sim, mid: str) -> str:
    """打开某个模块的设置界面。返回一句状态文本（给界面显示）。"""
    mod = _BY_ID.get(mid)
    if mod is None or not is_enabled(sim, mid):
        return '模块未启用'
    spec = mod.open_settings or ''
    try:
        if spec.startswith('dialog:'):
            dlg = getattr(getattr(sim, 'dialog_mgr', None), spec.split(':', 1)[1], None)
            if dlg is None:
                return '该模块没有独立设置界面'
            fn = getattr(dlg, 'refresh', None)
            if callable(fn):
                fn()
            dlg.activate()
            return '打开 %s 设置' % mod.name
        if spec.startswith('settings:'):
            tab = int(spec.split(':', 1)[1])
            sd = getattr(getattr(sim, 'dialog_mgr', None), 'sim_settings', None)
            if sd is None:
                return '设置界面不可用'
            sd.activate()
            try:
                sd.tab_index = tab
                if hasattr(sd, 'rebuild_fields'):
                    sd.rebuild_fields()
            except Exception:
                pass
            return '打开 %s 设置' % mod.name
        if spec == 'script':
            sd = getattr(sim, 'script_dialog', None)
            if sd is not None:
                sd.activate()
                return '打开脚本界面（编辑器里有「可视化编辑」按钮）'
            return '脚本界面不可用'
    except Exception as exc:                       # 设置打不开也不能崩
        return '打开失败: %s' % exc
    return '该模块没有设置界面'
