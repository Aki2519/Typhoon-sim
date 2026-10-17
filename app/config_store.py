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
import sys
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ['ConfigStore', 'RUNTIME_FIELDS', 'set_path_override',
           'get_path_override', 'resolve_config_path', 'PATH_OVERRIDE_ENV',
           'declare_app', 'is_app', 'is_user_config_path', 'ALLOW_WRITE_ENV',
           'APP_LAUNCH_ENV']

# ── 用户配置的写保护（2026-10-02，第 3 次事故后从根上收口）────────────────────
# 语义：**唯一合法写用户 `config.json` 的，是应用本体自己**（用户点"确定"）。
# 探针 / 测试 / 工具默认**写不进去** —— 它们的写会落到临时文件，并打一条 warning。
#
# 为什么不再靠"探针记得设 env"：这个纪律**已经失败三次**
#   （见 `output/exploration/30_fix_config_write_guard.md` §1 的事故表）。
# 所以改成"**应用必须显式声明自己是谁**"：`main.py` 在最早期调用 `declare_app()`。
#
# 判定优先级（`resolve_config_path`）：
#   1) 显式 override（`set_path_override()` 或 `TSIM_CONFIG_FILE`）→ 用 override
#   2) 目标不是用户配置（basename != config.json）→ 原样返回（不干扰别的文件）
#   3) 应用本体（`declare_app()` 已调用，或 `TSIM_APP_LAUNCH` 已设）→ 原样返回 ✅生产
#   4) 逃生开关 `TSIM_CONFIG_ALLOW_WRITE=1` → 原样返回（给确实要写真实配置的工具）
#   5) 其余（= 裸探针/测试/工具）→ **回落临时路径** + 每进程一次 warning
#
# 读路径不受影响：`AppConfig.load()` 不经这里，探针仍能读到用户的真实设置。
PATH_OVERRIDE_ENV = 'TSIM_CONFIG_FILE'
#: 逃生开关：非应用进程确实要写真实用户配置时设 `TSIM_CONFIG_ALLOW_WRITE=1`
ALLOW_WRITE_ENV = 'TSIM_CONFIG_ALLOW_WRITE'
#: 应用声明标记（`declare_app()` 会设它；子进程/晚 import 也能看到）
APP_LAUNCH_ENV = 'TSIM_APP_LAUNCH'
#: 用户配置的文件名 —— 只有这个名字的目标才受写保护
_USER_CONFIG_BASENAME = 'config.json'
#: 仓库根（本文件在 <repo>/app/config_store.py）—— 用户配置就是这里的 config.json
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_path_override: Optional[str] = None
_app_declared: bool = False
_nonapp_fallback: Optional[str] = None
_nonapp_warned: bool = False


def declare_app(identity: str = 'app') -> None:
    """**应用本体**在 import 任何 app 模块之前调用，声明"我是应用"。

    `main.py` 是唯一调用点。调用后 `resolve_config_path()` 对用户 `config.json`
    原样放行 —— 即"用户点确定能存下来"这一生产语义得以保留。
    """
    global _app_declared
    _app_declared = True
    try:
        os.environ.setdefault(APP_LAUNCH_ENV, str(identity or 'app'))
    except Exception:
        pass


def is_app() -> bool:
    """本进程是否已声明为应用本体（进程内标记 或 `TSIM_APP_LAUNCH`）。"""
    if _app_declared:
        return True
    try:
        return bool(os.environ.get(APP_LAUNCH_ENV))
    except Exception:
        return False


def is_user_config_path(path) -> bool:
    """`path` 是否指向"用户的 config.json"（唯一受写保护的目标）。

    只按 **仓库根那一份** 判定，**不是**"basename 叫 config.json"：
    后者会把测试/工具自己造的临时 `config.json` 也算进来 —— 实测那会让
    `tests/test_config.py::test_save_and_load_roundtrip` 与
    `tests/test_sim_settings.py::test_new_sim_config_fields_roundtrip` **往返失败**
    （它们的落盘被重定向到别处，读回自然读不到）。
    用户的配置只有一份，就是应用在仓库根读写的那份（`main.py` 启动时
    `os.chdir(script_dir)` 保证 CWD = 仓库根）。
    """
    try:
        raw = os.fspath(path)
    except Exception:
        return False
    if os.path.basename(raw) != _USER_CONFIG_BASENAME:
        return False
    if os.path.dirname(raw) == '':
        # 裸文件名 `config.json` ⇒ 就是"当前目录下的应用配置"（应用启动即
        # `os.chdir(仓库根)`，探针也通常在仓库根运行）⇒ 一律视为用户配置。
        return True
    try:
        return os.path.abspath(raw) == os.path.abspath(os.path.join(_REPO_ROOT, _USER_CONFIG_BASENAME))
    except Exception:
        return False


def _fallback_for_nonapp() -> str:
    """非应用进程的落点（同一进程内所有 ConfigStore 共用一个临时文件）。"""
    global _nonapp_fallback, _nonapp_warned
    if _nonapp_fallback is None:
        _nonapp_fallback = os.path.join(
            tempfile.mkdtemp(prefix='tsim_nonapp_cfg_'), _USER_CONFIG_BASENAME)
    if not _nonapp_warned:
        _nonapp_warned = True
        msg = (
            "非应用进程调用 ConfigStore 写用户配置：已重定向到临时文件 "
            f"{_nonapp_fallback}（真实 config.json 不会被改动）。"
            f"若确需写真实配置，请设 {ALLOW_WRITE_ENV}=1，"
            f"或先用 {PATH_OVERRIDE_ENV} 指定目标。"
        )
        logger.warning(msg)
        try:
            print(f'[config_store] {msg}', file=sys.stderr)
        except Exception:
            pass
    return _nonapp_fallback


def set_path_override(path: Optional[str]) -> None:
    """重定向**所有**新建 `ConfigStore` 的目标路径（None = 取消重定向）。

    供测试/批处理工具在构造任何 `ConfigStore` 之前调用。
    """
    global _path_override
    _path_override = path or None


def get_path_override() -> Optional[str]:
    return _path_override or os.environ.get(PATH_OVERRIDE_ENV) or None


def resolve_config_path(path: str) -> str:
    """把请求路径解析成实际路径。

    * 生产（应用本体，见 `declare_app`）：**原样返回** —— 与改前逐位相同。
    * 非应用进程 + 目标是**用户** `config.json`：回落临时路径（§模块头 5 条优先级）。
    * **不是**用户配置的路径：一律**原样返回，连 override 也不套用** ——
      否则测试/工具给自己造的临时 `.json` 会被劫持（实测会破坏两个往返用例）。

    即：本函数只对"仓库根那一份 config.json"这一**个**目标做 5 级判定，
    对其它任何路径都是恒等函数。
    """
    if not is_user_config_path(path):
        return path                    # 不是用户配置 ⇒ 恒等（连 override 都不参与）
    ov = get_path_override()
    if ov:
        return ov                      # 测试/工具的显式重定向（只针对用户配置）
    if is_app():
        return path                    # 应用本体 ⇒ 生产语义
    try:
        if os.environ.get(ALLOW_WRITE_ENV, '').strip().lower() in ('1', 'true', 'yes', 'on'):
            return path                # 逃生开关
    except Exception:
        pass
    return _fallback_for_nonapp()


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
        # 测试/工具可通过 `TSIM_CONFIG_FILE` 或 `set_path_override()` 重定向；
        # 生产无覆盖 ⇒ `self.path is path`（与改前逐位相同）。
        self.path = resolve_config_path(path)
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
    def persisted_fields(cfg):
        """该 cfg 里**应当被持久化**的字段名, 已剔除运行时状态。

        ⚠ 审查 R6-7(2026-10-15): 取字段失败时**必须返回 None**，不能返回 []。
        原来吞成  ⇒  ⇒ 回读校验拿空集和空集比"相等"通过 ⇒
         把用户的 config.json **覆盖成空对象**（唯一的防线也失效了）。
        返回 None 时  直接失败、原文件不动。
        """
        try:
            names = list(cfg._serialize_fields())
        except Exception:
            return None
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

        _names = self.persisted_fields(cfg)
        if not _names:
            # 取不到字段（或字段表为空）⇒ 绝不能写盘，否则会把配置清空
            self._last_error = '无法枚举配置字段，已放弃写盘（原文件未改动）'
            return False
        payload = {k: getattr(cfg, k, None) for k in _names}
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
                f.flush()
                # ⚠ 审查 R6-9: 原子写缺 fsync ——  只保证"改名原子"，
                #   不保证数据已落盘；断电可能留下 0 字节的 config.json。
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
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
        # ⚠  现在可能返回 None（字段枚举失败），不能直接迭代
        for k in (self.persisted_fields(cfg) or ()):
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
