"""pytest 全局夹具。

# 最重要的一件事: 别让测试写用户的 config.json

`app/constants/layout.py` 里 `CONFIG_FILE = "config.json"`(相对路径, 解析到仓库根),
而 `TySim.save_config()` 会写到它 —— 于是测试里任何一次 `switch_mode()` / 点"确认"
都会把**用户的真实配置**覆盖成测试进程里的状态
(首轮实测: 一次测试跑完, config.json 从 92 个键变成 104 个, `md` 从 season 变成 sim)。

## 为什么旧版隔离不够(同类事故已发生两次)

旧版只在 **session fixture 里**给 3 个模块全局打补丁。它有两个缺口:

1. **补丁晚于 import**: pytest 先 import 测试模块、再跑 fixture; 任何"import 期就读/
   构造"的路径都躲过补丁。
2. **不经 pytest 就完全不生效**: `python 某探针.py` 直接驱动 `apply_settings()` /
   `save_config()` 时, conftest 根本不会被加载 —— 第二次事故
   (`hemisphere` 被切成 south 落到用户真实配置) 就属这一类。

## 现在的四层隔离(全部不改生产语义)

* **L0 模块级环境变量**: 本文件被 import 的最初几行就设 `TSIM_CONFIG_FILE`,
  它早于任何 app 模块 import。`app/config_store.py:resolve_config_path()` 读它。
* **L1 进程级重定向**: fixture 里调 `config_store.set_path_override()`(显式 API)。
* **L2 模块全局补丁**: 把已知的 4 处 `CONFIG_FILE` 全局逐个指向临时文件
  (含旧版漏掉的 `app.ty_sim_mixins.keyboard_mixin`, 它读该常量做"恢复地图范围")。
* **L3 兜底 + 失效保护**: 包一层 `AppConfig.save`(挡住绕过 ConfigStore 的直接调用);
  并在 **session 结束时校验用户真实 `config.json` 的字节** —— 一旦被改动,
  **立即还原原始字节并把本次 pytest 判为失败**, 绝不静默放过。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

# ══════════════════════════════════════════════════════════════════════════
# L0: 必须发生在**任何 app 模块被 import 之前** —— 所以写在模块顶层, 不放进 fixture
# ══════════════════════════════════════════════════════════════════════════
_TMP_DIR = tempfile.mkdtemp(prefix='tcsim_test_cfg_')
_TMP_CONFIG = os.path.join(_TMP_DIR, 'config.json')
_PREV_ENV = os.environ.get('TSIM_CONFIG_FILE')
os.environ['TSIM_CONFIG_FILE'] = _TMP_CONFIG

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_CONFIG = os.path.join(_REPO_ROOT, 'config.json')

#: 会话开始时用户真实 config.json 的原始字节(L3 的比对基准 + 还原来源)
_ORIG_BYTES = None
try:
    with open(_REAL_CONFIG, 'rb') as _f:
        _ORIG_BYTES = _f.read()
except OSError:
    _ORIG_BYTES = None

#: L3 统计: [(目标路径, 字节数)] —— 供 session 结束时的诊断输出
_WRITE_LOG: list = []


@pytest.fixture(scope='session', autouse=True)
def _isolate_config_file():
    patched = []

    # ── L1: 进程级重定向(显式 API; 生产不调用) ──
    try:
        from app.config_store import set_path_override
        set_path_override(_TMP_CONFIG)
    except Exception:
        pass

    # ── L2: 模块全局补丁(含旧版漏掉的 keyboard_mixin) ──
    # ⚠ **两趟**: 先全部 import、再统一记录+打补丁。一趟写会踩到下面这个坑(我自己
    # 的探针实测到的真 bug): 第 1 项把 `app.constants.CONFIG_FILE` 改成临时路径之后,
    # 第 4 项才 import `keyboard_mixin`, 而它是 `from ..constants import CONFIG_FILE`
    # ⇒ 绑到的**已经是临时路径**; 于是 `old` 记成临时值, 复原变成空操作。
    # 两趟之后每个模块都在"未打补丁"的状态下被 import, `old` 必为原始值。
    _TARGETS = (
        'app.constants',
        'app.ty_sim',
        'app.constants.layout',
        'app.ty_sim_mixins.keyboard_mixin',
    )
    _mods = []
    for modname in _TARGETS:
        try:
            _mods.append(__import__(modname, fromlist=['*']))
        except Exception:
            pass
    for mod in _mods:
        old = getattr(mod, 'CONFIG_FILE', 'config.json')
        patched.append((mod, 'CONFIG_FILE', old))
        try:
            setattr(mod, 'CONFIG_FILE', _TMP_CONFIG)
        except Exception:
            pass

    # ── L3a: 兜住绕过 ConfigStore 的 `AppConfig.save(path)` 直调 ──
    orig_save = None
    try:
        from app.config import AppConfig
        orig_save = AppConfig.save

        def _guarded_save(self, path=None, *a, **k):
            # 测试期一律落到临时文件; 生产不会走到这个包装(只在 pytest 会话内安装)
            if path is not None and os.path.abspath(str(path)) == os.path.abspath(_REAL_CONFIG):
                _WRITE_LOG.append((str(path), 'AppConfig.save→redirected'))
                path = _TMP_CONFIG
            return orig_save(self, path, *a, **k)

        AppConfig.save = _guarded_save
    except Exception:
        pass

    yield _TMP_CONFIG

    # ── 复原 ──
    if orig_save is not None:
        try:
            AppConfig.save = orig_save
        except Exception:
            pass
    for mod, name, old in patched:
        try:
            setattr(mod, name, old)
        except Exception:
            pass
    try:
        from app.config_store import set_path_override
        set_path_override(None)
    except Exception:
        pass
    if _PREV_ENV is None:
        os.environ.pop('TSIM_CONFIG_FILE', None)
    else:
        os.environ['TSIM_CONFIG_FILE'] = _PREV_ENV
    try:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    """L3b: 会话结束时校验用户真实 `config.json` —— 被改了就还原并**判失败**。

    这一层是"堵死"的最后一道: 即使将来出现本文件没预料到的写入路径,
    用户的文件也不会被留下改动, 而且本次 pytest 会红着退出(不静默)。
    """
    if _REAL_CONFIG is None or not os.path.exists(_REAL_CONFIG):
        return
    try:
        with open(_REAL_CONFIG, 'rb') as f:
            now = f.read()
    except OSError:
        return
    if _ORIG_BYTES is not None and now == _ORIG_BYTES:
        return
    # 被改了: 还原 + 大声失败
    try:
        if _ORIG_BYTES is not None:
            with open(_REAL_CONFIG, 'wb') as f:
                f.write(_ORIG_BYTES)
            restored = '已还原为会话开始时的字节'
        else:
            restored = '无基准字节, 未还原'
    except OSError as e:
        restored = f'还原失败: {e}'
    msg = (
        "\n[conftest 失效保护] 用户真实 config.json 在本次 pytest 中被改动了!\n"
        f"  路径: {_REAL_CONFIG}\n"
        f"  原始 sha256 vs 现在: 长度 {len(_ORIG_BYTES or b'')} -> {len(now)}\n"
        f"  {restored}\n"
        "  ⇒ 说明仍有绕过 L0~L3 的写入路径; 请把该路径加进本文件的隔离链。\n"
    )
    print(msg, file=sys.stderr)
    session.exitstatus = 1
