# app/cache_store.py
"""带字节预算的表面缓存(渲染层共用)。

统一替换各模块手写的 "len(cache) > N: cache.pop(next(iter(cache)))"：

  - 按**总字节**而不是条数封顶: 图标放大后单帧可达数 MB, 条数上限挡不住内存;
  - 真 LRU(OrderedDict.move_to_end): 命中的热帧不会被先淘汰(旧的 FIFO 会);
  - 单个超预算对象直接不缓存, 不会把整池冲掉;
  - 带 hits/misses/evictions 计数: 性能面板与回归测试可直接断言。

接口刻意与 dict 兼容(get/put/pop/clear/len/in/[]), 迁移时调用点几乎不用改。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional


def surface_bytes(surf: Any) -> int:
    """表面占用估算: 宽×高×4(RGBA)。取不到尺寸时按 0 计。"""
    try:
        return int(surf.get_width()) * int(surf.get_height()) * 4
    except Exception:
        return 0


class SurfaceCache:
    """字节预算 + LRU 的表面缓存。max_bytes=0 表示不限字节(只受 max_items 约束)。"""

    __slots__ = ("name", "max_bytes", "max_items", "hits", "misses", "evictions",
                 "_d", "_bytes")

    def __init__(self, name: str, max_bytes: int, max_items: Optional[int] = None) -> None:
        self.name = name
        self.max_bytes = int(max_bytes)
        self.max_items = int(max_items) if max_items else None
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._d: "OrderedDict[Any, Any]" = OrderedDict()
        self._bytes = 0

    # ── dict 兼容接口 ──
    def __contains__(self, key) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self):
        return iter(self._d)

    def get(self, key, default=None):
        v = self._d.get(key)
        if v is None:
            self.misses += 1
            return default
        self._d.move_to_end(key)          # 命中即刷新为最近使用
        self.hits += 1
        return v

    def __getitem__(self, key):
        v = self._d[key]
        self._d.move_to_end(key)
        self.hits += 1
        return v

    def __setitem__(self, key, value) -> None:
        self.put(key, value)

    def put(self, key, value) -> bool:
        """写入并维持预算。返回是否真正缓存(单个对象超预算时返回 False)。"""
        n = surface_bytes(value)
        if self.max_bytes > 0 and n > self.max_bytes:
            return False
        old = self._d.pop(key, None)
        if old is not None:
            self._bytes -= surface_bytes(old)
        self._d[key] = value
        self._bytes += n
        self._trim()
        return True

    def pop(self, key, default=None):
        v = self._d.pop(key, None)
        if v is None:
            return default
        self._bytes -= surface_bytes(v)
        return v

    def clear(self) -> None:
        self._d.clear()
        self._bytes = 0

    def total_bytes(self) -> int:
        return self._bytes

    def _trim(self) -> None:
        while self._d and (self._bytes > self.max_bytes
                           or (self.max_items is not None
                               and len(self._d) > self.max_items)):
            _k, v = self._d.popitem(last=False)
            self._bytes -= surface_bytes(v)
            self.evictions += 1

    def stats(self) -> dict:
        return {
            "name": self.name,
            "items": len(self._d),
            "bytes": self._bytes,
            "max_bytes": self.max_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }

    def reset_stats(self) -> None:
        self.hits = self.misses = self.evictions = 0
