# -*- coding: utf-8 -*-
"""配置持久化的**唯一入口**（2026-10-01 重写保存链路）。

## 为什么要有这个模块

原来的保存链路散在 11 个调用点 + `TySim.save_config()` + `AppConfig.save()` 三处，
各有各的缺陷：

1. **静默跳过**：`save_config()` 不带 `force` 时，若 `_config_needs_save` 为假就
   `return True` —— 改动悄悄丢掉，调用方以为成功。
2. **运行时漂移被持久化**：`_config_needs_save` 由**任何** cfg 写入置位，
   包括 `handle_resize()` 写的"实际窗口尺寸"。于是"用户想要 2560x1540"被
   "系统实际给了 1498"覆盖掉，每次启动重复一次（用户报的"config 里还是 1498"）。
3. **没有备份**：直接 `os.replace` 覆盖，写坏一次原配置就没了。
4. **失败只记日志**：调用点基本忽略返回值，用户以为存了其实没存。

## 本模块的契约

* `commit()` 是**唯一**的落盘入口；写入前先备份、写入后**读回校验**，失败则
  **原文件一字节不动**并把错误交给调用方（由界面提示用户）。
* **区分"用户意图"与"运行时状态"**：只有前者会被持久化。见 `RUNTIME_FIELDS`。
  运行时字段仍然存在于 `cfg` 里（供内部读写），但**不写入 config.json**。
* 幂等：`commit()` 可在任意时刻无副作用地调用；只有脏了才真写。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ['ConfigStore', 'RUNTIME_FIELDS']

#: **运行时状态**（不是"用户意图"）—— 存在于 cfg 里供内部读写, 但**不持久化**。
#:
#: ⚠ 2026-10-01: 这些字段以前会被存进 config.json, 造成两个真实 bug:
#:   * `screen_width/height` —— `handle_resize()` 把**系统实际给的**尺寸写回 cfg,
#:     于是用户设的 2560x1540 每次启动被"实际值"覆盖, 表现为"改了不生效/不保存"。
#:     用户要的尺寸应当来自设置面板, 而不是窗口当前多高。
#:   * `fullscreen` —— 它是"当前是不是全屏"的状态(`toggle_fullscreen` 靠它判方向),
#:     不是"我要全屏"; 存下来会让下次启动 / F11 方向错乱。
#:   * `start_maximized` **不在**这里 —— 它确实是用户意图(要不要启动即最大化),
#:     必须持久化。
RUNTIME_FIELDS = frozenset({
    'screen_width',
    'screen_height',
    'fullscreen',
})


class ConfigStore:
    """配置文件的读写与备份。一个实例管一个文件。"""

    def __init__(self, path: str, backup_count: int = 1):
        self.path = path
        self.backup_count = max(0, int(backup_count))
        self._dirty = False
        self._last_error: Optional[str] = None

    # ── 脏标记 ──────────────────────────────────────────────────────────
    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def last_error(self) -> Optional[str]:
        """上一次 commit 失败的原因(成功时为 None)。调用方应据此提示用户。"""
        return self._last_error

    def clear_error(self) -> None:
        self._last_error = None

    # ── 字段筛选 ────────────────────────────────────────────────────────
    @staticmethod
    def persisted_fields(cfg) -> list:
        """该 cfg 里**应当被持久化**的字段名, 已剔除运行时状态。"""
        try:
            names = list(cfg._serialize_fields())
        except Exception:
            names = []
        return [n for n in names if n not in RUNTIME_FIELDS]

    # ── 落盘 ────────────────────────────────────────────────────────────
    def commit(self, cfg, force: bool = False, reason: str = '') -> bool:
        """把 cfg 落盘。**唯一入口**。

        成功 → True；失败 → False，且**原文件未被改动**，`last_error` 给出原因。
        `force=True` 时即使不脏也写（用于"确定"按钮那种用户显式动作）。
        """
        if not force and not self._dirty:
            # ⚠ 与旧实现不同: 这里**不再静默 return True** 就当作成功 —— 语义仍是
            # "无需写盘即为已保存", 但调用方可以通过 `dirty` 判断是否真的有改动。
            return True

        payload = {k: getattr(cfg, k, None) for k in self.persisted_fields(cfg)}
        tmp = self.path + '.tmp'
        try:
            # 1) 备份(仅首次, 且原文件存在)
            if self.backup_count:
                bak = self.path + '.bak'
                if os.path.exists(self.path) and not os.path.exists(bak):
                    shutil.copy2(self.path, bak)
            # 2) 写临时文件
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            # 3) **读回校验** —— 写出去的必须能被解析回来, 否则绝不覆盖原文件。
            #    这一步是"写坏一次就没救"的防线(旧实现没有)。
            with open(tmp, 'r', encoding='utf-8') as f:
                back = json.load(f)
            if not isinstance(back, dict) or set(back) != set(payload):
                raise ValueError('回读字段集合与写入不一致')
            # 4) 原子替换
            os.replace(tmp, self.path)
            self._dirty = False
            self._last_error = None
            return True
        except (TypeError, OSError, ValueError) as e:
            self._last_error = f'{type(e).__name__}: {e}'
            logger.warning('配置保存失败(%s): %s: %s', reason or '无原因', self.path, e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    # ── 读入 ────────────────────────────────────────────────────────────
    def load_into(self, cfg, load_fn) -> bool:
        """用 `load_fn(path)` 的结果覆盖 `cfg` 的字段(逐字段, 坏字段不连坐)。

        `load_fn` 保持由 `AppConfig.load` 提供(那里已重写成逐字段恢复)。
        """
        try:
            fresh = load_fn(self.path)
        except Exception as e:
            self._last_error = f'load: {e}'
            logger.warning('配置加载失败: %s', e)
            return False
        # 只覆盖**持久化字段**, 不碰运行时状态(screen_width 等由窗口决定)
        for k in self.persisted_fields(cfg):
            if hasattr(fresh, k):
                try:
                    setattr(cfg, k, getattr(fresh, k))
                except Exception:
                    pass
        self._dirty = False
        return True

    def backup_path(self) -> str:
        return self.path + '.bak'

    def has_backup(self) -> bool:
        return os.path.exists(self.backup_path())

    def restore_backup(self) -> bool:
        """从 `.bak` 还原(原子)。失败返回 False。"""
        bak = self.backup_path()
        if not os.path.exists(bak):
            self._last_error = '没有备份'
            return False
        try:
            tmp = self.path + '.restore'
            shutil.copy2(bak, tmp)
            os.replace(tmp, self.path)
            self._last_error = None
            return True
        except OSError as e:
            self._last_error = f'OSError: {e}'
            return False
