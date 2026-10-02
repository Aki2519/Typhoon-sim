# simulator/simcore/envmodel/store.py
"""npz 载入 + 缓存(模型产物的唯一入口)。"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Optional

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'env', 'model')

_CACHE_MAX = 32
_cache: 'OrderedDict' = OrderedDict()

#: 缺文件时只提示一次(免得每帧刷日志)
_warned = set()


def path_of(name: str) -> str:
    return os.path.join(MODEL_DIR, name + '.npz')


def load(name: str) -> Optional[dict]:
    """载入 model/<name>.npz → dict(字段名 → 数组); 不存在返回 None。"""
    v = _cache.get(name)
    if v is not None:
        _cache.move_to_end(name)
        return v
    p = path_of(name)
    if not os.path.exists(p):
        if name not in _warned:
            _warned.add(name)
        return None
    try:
        with np.load(p, allow_pickle=False) as z:
            d = {k: z[k] for k in z.files}
    except Exception:
        return None
    _cache[name] = d
    _cache.move_to_end(name)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return d


_avail_cache = [None]

#: 代次计数: `clear_cache()` 时自增。下游(如 `solver.solve` 的记忆化)可以把它
#: 放进缓存键, 这样"产物被重载"能**自动失效**, 不必让 store 反向依赖 solver
#: (反向 import 会成环)。轮次 160 加。
_generation = [0]


def generation() -> int:
    """产物代次; 每次 `clear_cache()` +1。"""
    return _generation[0]


def available() -> set:
    """已烘焙的产物名集合(不含扩展名)。

    **必须缓存**: 它是 , 而 solver.solve 每次 env_update 都调一次
    (base_available)。实测单次 4.6 ms —— 模型目录在挂载盘上, listdir 很贵。
    产物在运行中不会变, 所以缓存一次即可(clear_cache 会重置)。
    """
    if _avail_cache[0] is None:
        if not os.path.isdir(MODEL_DIR):
            _avail_cache[0] = set()
        else:
            _avail_cache[0] = {f[:-4] for f in os.listdir(MODEL_DIR)
                               if f.endswith('.npz')}
    return _avail_cache[0]


def clear_cache() -> None:
    _cache.clear()
    _warned.clear()
    _avail_cache[0] = None
    _generation[0] += 1
