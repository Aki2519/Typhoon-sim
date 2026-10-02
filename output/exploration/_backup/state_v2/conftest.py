"""pytest 全局夹具。

**这里最重要的一件事: 别让测试写用户的 config.json。**

`app/constants/layout.py` 里 `CONFIG_FILE = "config.json"`(相对路径, 解析到仓库根),
而 `TySim.save_config()` 会写到它 —— 于是测试里任何一次 `switch_mode()` / 点"确认"
都会把**用户的真实配置**覆盖成测试进程里的状态(实测: 一次测试跑完, config.json
从 92 个键变成 104 个, `md` 从 season 变成 sim)。

所以在任何 TySim 被构造之前, 把 `app.ty_sim.CONFIG_FILE` 与
`app.constants.CONFIG_FILE` 都指到一个临时文件; 测试仍然走完整的 load/save 路径,
但落在 tmp 里。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture(scope='session', autouse=True)
def _isolate_config_file():
    tmp = os.path.join(tempfile.mkdtemp(prefix='tcsim_test_cfg_'), 'config.json')
    patched = []
    try:
        import app.constants as C
        patched.append((C, 'CONFIG_FILE', getattr(C, 'CONFIG_FILE', 'config.json')))
        C.CONFIG_FILE = tmp
    except Exception:
        pass
    try:
        import app.ty_sim as T
        patched.append((T, 'CONFIG_FILE', getattr(T, 'CONFIG_FILE', 'config.json')))
        T.CONFIG_FILE = tmp
    except Exception:
        pass
    try:
        import app.constants.layout as L
        L.CONFIG_FILE = tmp
    except Exception:
        pass
    yield tmp
    for mod, name, old in patched:
        try:
            setattr(mod, name, old)
        except Exception:
            pass
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
