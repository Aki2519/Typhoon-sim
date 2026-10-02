# simulator/simcore/envmodel/modes.py
"""模态索引的运行时取值: 有实测用实测, 没有就用随机模拟器外推。

设计(用户 2026-09 拍板):
  * t 落在观测区间内 → 直接查 `modes_index.npz` 的实测值(锚定真实 ENSO/PDO/…);
  * t 超出观测(1850 年、2097 年、或从 t0 往后播) → 多元 AR(1) 从最后一个
    实测月往前推。AR 系数与噪声协方差都是从 1948-2026 的实现里拟合的,
    所以方差/模态间相关/ENSO 相位锁定都保留。

为什么是"从 t0 往后外推"而不是"整段都查表":
  模拟是往前跑的, 从 2026-09-14 起播时, 9 月用实测、之后让 ENSO 自己演变,
  否则整段时间的模态会被冻在最后一个可用月上。
"""
from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np

from . import store

_CACHE_KEY = 'modes_index'


def _table():
    return store.load(_CACHE_KEY)


def available() -> bool:
    return _table() is not None


#: 拟合出来的 AR(1) 参数缓存(每个模态一次)
_AR: dict = {}


def clear_cache() -> None:
    """清空本模块的缓存(AR 拟合系数)。

    轮次 161 新增: 在此之前**没有**这个入口, 于是 `solver.clear_cache()` 只能
    **跨界**直接写 `modes._AR` —— 私有属性被别的模块改, 是设计味道, 而且让
    "换掉 `modes_index.npz` 之后要清哪些东西"的责任散在两个模块里。
    现在由本模块自己负责: 谁 bump `store` 代次, 谁调这里。
    (perf-reviewer 发现 `_fit_ar` 的 latch 不清会让"表外月份仍用旧 AR 系数外推",
     见 VERIFY_SOLVE_MEMO.md)
    """
    _AR.clear()


def _fit_ar():
    z = _table()
    if z is None:
        return None
    if 'ar' in _AR:
        return _AR['ar']
    val, ok = z['val'], z['ok']
    K, T = val.shape
    A = np.zeros(K, np.float64)
    Q = np.eye(K, dtype=np.float64)
    for k in range(K):
        x = np.where(ok[k], val[k], np.nan)
        # 只用连续有效的相邻对
        m = np.isfinite(x[:-1]) & np.isfinite(x[1:])
        if m.sum() > 24:
            a, b = x[:-1][m], x[1:][m]
            A[k] = float(np.dot(a, b) / max(1e-9, np.dot(a, a)))
            A[k] = float(np.clip(A[k], -0.95, 0.95))
    # 残差协方差(用同一批相邻对, 逐模态残差)
    res = []
    for k in range(K):
        x = np.where(ok[k], val[k], np.nan)
        m = np.isfinite(x[:-1]) & np.isfinite(x[1:])
        if m.sum() > 24:
            res.append(x[1:][m] - A[k] * x[:-1][m])
    if res:
        n = min(len(r) for r in res)
        R = np.stack([r[:n] for r in res], 0)
        Q = np.cov(R) + np.eye(K) * 1e-4
    _AR['ar'] = (A, Q)
    return _AR['ar']


class ModeSeries:
    """按模拟时间轴取模态索引的标准化值 (K,)。"""

    def __init__(self, sim=None, override=None):
        """override: {模态名: σ值} —— **手动覆盖入口**。

        为什么要它: 公开索引滞后约 1 个月(modes_index 最新到 2026-08), 模拟
        "今天"时只能走 AR 外推, 符号都可能反(实测 2026-09 被外推成偏拉尼娜,
        于是 EPAC 赤道比实况冷 5°C)。填上当前实测的 ONI/PDO 等, 这一项立刻修正。
        """
        z = _table()
        if z is None:
            raise RuntimeError('modes_index.npz 不存在')
        self.names = [str(x) for x in z['names']]
        self.year, self.month, self.val, self.ok = (
            z['year'], z['month'], z['val'], z['ok'])
        self.y_lo = int(z['y_lo'])
        self.T = self.val.shape[1]
        self.K = self.val.shape[0]
        ar = _fit_ar()
        self.A, self.Q = ar if ar is not None else (np.zeros(self.K),
                                                    np.eye(self.K))
        self.sim = sim
        self._gen = {}
        self._rng = {}
        # 手动覆盖: 只认已知模态名, 值按"标准化 σ"理解(与 val 同口径)。
        #
        # **两种键都支持**(阶段 12, 2026-09-18):
        #   全局:  {'oni': 2.5}                    —— 整段模拟都用这个值
        #   按月:  {'1997-09': {'oni': 2.5}}       —— 只在该月生效(优先于全局)
        # 为什么要按月: 之前只能给一个全程常数, 而厄尔尼诺是**逐月演化**的。
        # 用户要测的 1997-09-15 正处在一次强厄尔尼诺的发展期, 一个年度常数
        # 表达不了"9 月 +1.9σ、12 月 +2.4σ"这种进程。
        self.override = {}
        self.override_m = {}          # (year, month) → {模态名: σ}
        for k, v in (override or {}).items():
            if isinstance(v, dict):
                key = str(k).strip()
                ym = None
                for sep in ('-', '/', '.', '_'):
                    if sep in key:
                        a, b = key.split(sep, 1)
                        try:
                            ym = (int(a), int(b))
                        except ValueError:
                            ym = None
                        break
                if ym is None and len(key) == 6 and key.isdigit():
                    ym = (int(key[:4]), int(key[4:]))     # '199709'
                if ym is None:
                    continue
                d = {}
                for kk, vv in v.items():
                    if kk in self.names:
                        try:
                            d[str(kk)] = float(vv)
                        except (TypeError, ValueError):
                            pass
                if d:
                    self.override_m[ym] = d
            elif k in self.names:
                try:
                    self.override[k] = float(v)
                except (TypeError, ValueError):
                    pass
        self._ov_idx = [self.names.index(k) for k in self.override]

    # ── 实测 ──
    def _real_at(self, t: int):
        if t < 0 or t >= self.T:
            return None
        return np.where(self.ok[:, t], self.val[:, t], np.nan)

    # ── AR 外推序列(每个起点只生成一次) ──
    def _emulate_from(self, t0: int, n: int, seed: int) -> np.ndarray:
        key = (t0, n, seed)
        got = self._gen.get(key)
        if got is not None:
            return got
        rng = np.random.default_rng(seed & 0x7FFFFFFF)
        x = self.val[:, max(0, min(self.T - 1, t0))].astype(np.float64).copy()
        x = np.where(np.isfinite(x), x, 0.0)
        out = np.empty((self.K, n), np.float32)
        L = np.linalg.cholesky(self.Q + np.eye(self.K) * 1e-6)
        for i in range(n):
            # 季节锁相: ENSO 类噪声在 11-1 月最大(观测方差峰), 夏末最小
            mo = ((t0 + i) % 12) + 1
            seas = 1.0 + 0.25 * math.cos(2 * math.pi * (mo - 12) / 12.0)
            x = self.A * x + (L @ rng.standard_normal(self.K)) * seas
            out[:, i] = np.clip(x, -4.0, 4.0)
        if len(self._gen) > 8:
            self._gen.clear()
        self._gen[key] = out
        return out

    def at(self, year: int, month: int) -> np.ndarray:
        """(K,) 标准化索引。**按月覆盖 > 全局覆盖 > 实测 > AR 外推**。"""
        if not self.override and not self.override_m:
            return self._at_raw(year, month)
        out = self._at_raw(year, month)
        for k, nm in enumerate(self.names):
            if nm in self.override:
                out[k] = self.override[nm]
        m = self.override_m.get((int(year), int(month)))
        if m:
            for k, nm in enumerate(self.names):
                if nm in m:
                    out[k] = m[nm]
        return out

    def _at_raw(self, year: int, month: int) -> np.ndarray:
        t = (year - self.y_lo) * 12 + (month - 1)
        real = self._real_at(t)
        if real is not None and np.isfinite(real).all():
            return real.astype(np.float32)
        # 找最近的有实测的月份作为 AR 起点
        seed = 12345 if self.sim is None else int(self.sim.get('seed', 12345))
        t0 = min(max(0, t), self.T - 1)
        n = abs(t - t0) + 1
        gen = self._emulate_from(t0, n, seed)
        col = gen[:, -1] if t >= t0 else gen[:, 0]
        if real is not None:
            col = np.where(np.isfinite(real), real, col)
        return col.astype(np.float32)
