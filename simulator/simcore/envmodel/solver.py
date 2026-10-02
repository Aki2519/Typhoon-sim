# simulator/simcore/envmodel/solver.py
"""把烘焙好的 npz 合成成内核需要的环境场。

合成公式(全部离线, 运行时零外部数据):
    F(φ, t) = base(φ, doy)                 ← 基态气候场, 按儒略日插值(月平均锚在月中)
            + warm(t) · trend(φ)           ← 增暖情景(F4)
            + Σ_mode Σ_lag pat(φ) · idx(t+lag)   ← 模态异常(F3)
            + noise(φ, t)                  ← 天气尺度随机场(由 core 叠加)

单位与内核一致: SST °C, D26 m, 风切 kt, 风 kt, RH %, MSLP hPa。
缺产物时返回 None, 上层回落旧库。
"""
from __future__ import annotations

import hashlib
import logging
import math
import pickle
from typing import Optional

import numpy as np

from . import store

logger = logging.getLogger(__name__)

#: 各变量所在产物与分辨率标记
BASE_FILES = {
    'sst': ('base_sst', 'sst', 'half'),
    'd26': ('base_d26', 'd26', 'half'),
    'shear': ('base_shear', 'shear', 'half'),
    'u850': ('base_wind', 'u850', 'one'),
    'v850': ('base_wind', 'v850', 'one'),
    'u500': ('base_wind', 'u500', 'one'),
    'v500': ('base_wind', 'v500', 'one'),
    'u300': ('base_wind', 'u300', 'one'),
    'v300': ('base_wind', 'v300', 'one'),
    'u200': ('base_wind', 'u200', 'one'),
    'v200': ('base_wind', 'v200', 'one'),
    'rh700': ('base_other', 'rh700', 'one'),
    'mslp': ('base_other', 'mslp', 'one'),
    't200': ('base_other', 't200', 'one'),
    'tchp': ('base_tchp', 'tchp', 'half'),
}

_base_cache: dict = {}
#: 上采样缓存上限。型是静态且总数有界的(模态型 ~48 + 环流型响应型 ~48),
#: 256 足够容纳全部且不会抖动。每个 0.5° 场约 0.7 MB, 256 个约 180 MB —— 偏大,
#: 取 128 折中(实测全部用到的是 ~96 个)。
#: **不要为了省内存把它调小**(2026-09-23 轮次 111 试过 24, 立刻回退):
#: 下面那段注释已经写明"上限必须盖住型的总数, 否则命中率抖到 0" ——
#: 实测 16 时命中率恒为 0、每次白花 194ms。省下的 ~90MB 换不来这个代价,
#: 而且年尺度验收被杀是**全机提交内存**问题, 不是本进程的单点。
_UP_CACHE_MAX = 128
_up_cache: dict = {}


def clear_cache() -> None:
    """清空本模块的全部缓存(产物重载/测试隔离用)。

    ⚠ 轮次 160 修正: 原来**只清** `_base_cache`/`_up_cache` + `store`, 而
    `_SOLVE_MEMO`(按天记忆化)与 `_DAY_MEMO`(瞬时场, 按 (y,m,d,h) latch)都不清 ⇒
    **"运行中把新数据落盘"没有办法强制重读**: `_SOLVE_MEMO` 那侧还能靠键里的
    `store.generation()` 兜住, `_DAY_MEMO` 那侧什么都没有, 两层策略不一致。
    更糟的是 `_DAY_MEMO` 会**击败 `daily.load_fields` 自己的 `(path, mtime, size)`
    感知** —— 同一小时内它根本不调 `load_fields`, 于是被替换的文件看不见。
    (perf-reviewer 实测发现, 见 VERIFY_SOLVE_MEMO.md)

    ⚠ 轮次 160 做严(task-13): 再加**三张"表"级 latch** ——
    `_warm_cache`(warming 的基准期均值)、`_REG` + `_REG_RESP_CACHE`(regimes 表与
    由其派生的重采样响应型)、`_MS_CACHE`(ModeSeries, 内含 modes_index 表)。
    它们各自**也**在键里带了 `store.generation()`(所以别人直接调 `store.clear_cache()`
    也能失效), 这里再清一遍是为了**立即释放内存**并让"清空全部缓存"这句话名副其实。
    另: `modes._fit_ar()` 在 `modes.py` 里还有一层 `_AR` latch, 不清它的话
    "换了 modes_index.npz 但月份落在表外" 时仍会用旧 AR 系数外推 —— 见下面那段。
    """
    _base_cache.clear()
    _up_cache.clear()
    # 瞬时场 latch: 置空 key 才会重新调 `daily.load_fields`
    try:
        _DAY_MEMO['key'] = None
        _DAY_MEMO['fields'] = None
    except Exception:
        logger.debug('_DAY_MEMO 重置失败', exc_info=True)
    _SOLVE_MEMO.clear()
    # 三张表级 latch(见 docstring)
    _warm_cache.clear()
    _MS_CACHE.clear()
    _REG.update({'ok': False, 'gen': None, 'eof': None, 'pc': None, 'date': None,
                 'resp': {}})
    _REG_RESP_CACHE.clear()
    # `modes._AR` 是 modes.py 里的 AR 拟合 latch —— 不清它的话, "换掉
    # `modes_index.npz` 之后落在表外的月份仍会用旧 AR 系数外推", 失效就不彻底。
    # 轮次 161 **已改成调公开 API**(`modes.clear_cache()`), 不再跨界写私有属性:
    # 谁 bump `store` 代次, 谁负责清自己的 latch。
    try:
        from . import modes as _M
        _M.clear_cache()
    except Exception:
        logger.debug('modes.clear_cache 调用失败', exc_info=True)
    store.clear_cache()


def _env_dims():
    from .. import core as _C
    return _C.ENVNY, _C.ENVNX


def _month_weight(clock) -> tuple:
    """儒略日 → (m0, m1, f)。月平均锚在月中(与 F1 的 phase 修正口径一致)。

    没有 clock 时用 7 月 15 日占位(不会发生: core 一定会传 clock)。
    """
    if clock is None:
        return 6, 7, 0.0
    import calendar
    dim = calendar.monthrange(clock.year, clock.month)[1]
    mf = ((clock.month - 1) + (clock.day - 15.0) / dim) % 12.0
    m0 = int(math.floor(mf)) % 12
    f = mf - math.floor(mf)
    return m0, (m0 + 1) % 12, float(f)


def _upsample(a: np.ndarray, key=None) -> np.ndarray:
    """1° (120,360) → 0.5° (240,720), 双线性。

    缓存 key **不能用 id(a)** —— 调用方每次传的都是新建的临时数组, 临时数组
    被回收后 id 会被复用, 于是命中别人的缓存(实测导致同一输入两次得到不同
    结果: "数据模式可复现"测试因此变红)。改由调用方给稳定 key。

    ⚠ **轮次 160 做严: 内部缓存键必须再拼上域尺寸 `(ny, nx)`**。
    调用方给的是 `('base', name)` / `('warm', name)` / `('pat', name, mode, lag)`
    —— **都不含 dims**, 于是换域时(ENVNY/ENVNX 变)会命中**旧尺寸**的结果
    (实测: 连形状都不对还照样命中)。这里在 `_upsample` **内部**拼 dims,
    而不是去改调用点: 调用点有 3 处且以后还会加, 内部拼一次就不可能漏。
    (perf-reviewer 实测发现; 见 VERIFY_SOLVE_MEMO.md / task-13)
    """
    ny, nx = _env_dims()
    if a.shape == (ny, nx):
        return a
    if key is None:
        return _bilinear2x(a)
    ck = (ny, nx, key)                      # ← dims 进键(见 docstring)
    v = _up_cache.get(ck)
    if v is not None:
        return v
    sy = a.shape[0] / ny
    sx = a.shape[1] / nx
    yy = np.clip((np.arange(ny) + 0.5) * sy - 0.5, 0, a.shape[0] - 1)
    xx = np.clip((np.arange(nx) + 0.5) * sx - 0.5, 0, a.shape[1] - 1)
    j0 = np.floor(yy).astype(np.int64)
    i0 = np.floor(xx).astype(np.int64)
    j1 = np.minimum(j0 + 1, a.shape[0] - 1)
    i1 = np.minimum(i0 + 1, a.shape[1] - 1)
    fj = (yy - j0).astype(np.float32)[:, None]
    fi = (xx - i0).astype(np.float32)[None, :]
    tmp = a[j0][:, i0] * (1 - fi) + a[j0][:, i1] * fi
    tmp2 = a[j1][:, i0] * (1 - fi) + a[j1][:, i1] * fi
    out = (tmp * (1 - fj) + tmp2 * fj).astype(np.float32)
    # **缓存上限必须盖住"型"的总数, 否则会抖动到零命中。**
    # 实测: 上限写 16, 而 modes_pat 里有 24 个 <字段>__<模态> 型 x 2 个字段
    # = 48 个不同 key —— 每次都在第 17 个时被 clear(), 于是命中率恒为 0
    # (实测 392 次调用 0 命中, 白白花掉 194 ms/次)。型是**静态**的,
    # 总数有界(~百级), 所以可以放心缓存; 溢出时只淘汰最旧的一半。
    if len(_up_cache) > _UP_CACHE_MAX:
        for k in list(_up_cache)[:len(_up_cache) // 2]:
            _up_cache.pop(k, None)
    _up_cache[ck] = out                     # ← 用带 dims 的键(与上面的 ck 同一个)
    return out


def _bilinear2x(a: np.ndarray) -> np.ndarray:
    ny, nx = _env_dims()
    sy = a.shape[0] / ny
    sx = a.shape[1] / nx
    yy = np.clip((np.arange(ny) + 0.5) * sy - 0.5, 0, a.shape[0] - 1)
    xx = np.clip((np.arange(nx) + 0.5) * sx - 0.5, 0, a.shape[1] - 1)
    j0 = np.floor(yy).astype(np.int64)
    i0 = np.floor(xx).astype(np.int64)
    j1 = np.minimum(j0 + 1, a.shape[0] - 1)
    i1 = np.minimum(i0 + 1, a.shape[1] - 1)
    fj = (yy - j0).astype(np.float32)[:, None]
    fi = (xx - i0).astype(np.float32)[None, :]
    t1 = a[j0][:, i0] * (1 - fi) + a[j0][:, i1] * fi
    t2 = a[j1][:, i0] * (1 - fi) + a[j1][:, i1] * fi
    return (t1 * (1 - fj) + t2 * fj).astype(np.float32)


#: 单日瞬时场的查找记忆(避免每个变量都扫一遍目录)
_DAY_MEMO = {'key': None, 'fields': None}


def _instant_fields(clock):
    """该时次的 ERA5 **瞬时**场(没有则 {}); 按 (年,月,日,时) 记忆。"""
    if clock is None:
        return {}
    k = (getattr(clock, 'year', 0), getattr(clock, 'month', 0),
         getattr(clock, 'day', 0), getattr(clock, 'hour', -1))
    if _DAY_MEMO['key'] != k:
        try:
            from simulator.simcore import daily as _daily
            _DAY_MEMO['fields'] = _daily.load_fields(clock)
        except Exception:
            logger.debug('瞬时场加载失败', exc_info=True)
            _DAY_MEMO['fields'] = {}
        _DAY_MEMO['key'] = k
    return _DAY_MEMO['fields'] or {}


def _base_field(name: str, clock) -> Optional[np.ndarray]:
    """基态场(0.5°)。

    **优先瞬时**: 有 ERA5 单日单时次缓存时直接用那一时次的瞬时场
    (用户 2026-09-20: "风切及一切数据都要用瞬时"), 月气候态只做兜底。
    """
    d_inst = _instant_fields(clock)
    if name in d_inst:
        # ⚠ 轮次 160: **必须 copy** —— `d_inst[name]` 是 `daily._CACHE` 里的**同一对象**,
        # 而外层 `_freeze()` 会把它设成 `writeable=False`。不 copy 就等于**越过模块
        # 所有权边界去冻结别人的缓存**: 失败模式还是**顺序相关**的(将来 daily 侧若
        # 就地写, 只在"本进程先跑过 solve"时抛)。
        # 代价: 只在**有瞬时场**的日子每场多一次全网格 copy(约 0.2ms), 相比 solve
        # 的 72ms 可忽略。(perf-reviewer 实测 identity 命中, 见 VERIFY_SOLVE_MEMO.md)
        return np.array(d_inst[name], np.float32, copy=True)
    spec = BASE_FILES.get(name)
    if spec is None:
        return None
    fname, key, res = spec
    # 轮次 160: 删掉一行死代码 `ck = (name, id(clock) if clock is None else 0)` ——
    # 它算了但从没被用过, 而且用的正是本模块 docstring 在 `_upsample` 处**明确警告
    # 过**的 `id()` 建键模式(临时对象回收后 id 复用 ⇒ 命中别人的缓存)。
    # 留着它只会诱使后来者"顺手用起来"。(perf-reviewer 指出, VERIFY_SOLVE_MEMO.md)
    d = store.load(fname)
    if d is None or key not in d:
        return None
    arr = d[key]
    m0, m1, f = _month_weight(clock)
    # (12, ny, nx) → 两月加权; 若是 1° 再升采样
    a = arr[m0] * (1.0 - f) + arr[m1] * f
    a = np.asarray(a, np.float32)
    if res == 'one':
        a = _upsample(a, key=('base', name))
    return a


def base_available() -> bool:
    """只要 SST 基态在就算可用。

    不强求 base_wind: core.env_update 的 data 分支是**逐字段**判断的 —— 给了
    SST 就用真 SST, 缺 shear/u850 就单独回落到解析风场并把 dataState 标成
    'partial'。所以"部分基态"是有意义的中间态, 不该整块退回旧合成库。
    """
    return 'base_sst' in store.available()


#: 基准期(与 base_*.npz 的气候态一致)。趋势要相对它算增量, 否则会把
#: 1991-2020 已经包含的增暖重复加一遍。
BASE_PERIOD = (1991, 2020)
#: `warming.npz` 的基准期均值缓存。
#: ⚠ 轮次 160 做严: 键里必须带 `store.generation()` —— 否则运行中换掉
#: `warming.npz`(或任何调用方直接 `store.clear_cache()`)之后, `store.load()` 会
#: 重新读文件, 但这里的均值仍是**旧产物**的, 于是 "重算" 出来的 ΔT 依旧是旧气候态。
#: (perf-reviewer 实测发现, 见 VERIFY_SOLVE_MEMO.md / task-13)
_warm_cache: dict = {}


def _warming_delta(clock) -> float:
    """当前时刻相对基准期的全球平均增暖增量 (°C)。"""
    d = store.load('warming')
    if d is None or clock is None:
        return 0.0
    key = ('base_dt', store.generation())        # ← 代次进键: 产物重载即失效
    if key not in _warm_cache:
        yrs = d['year'].astype(np.int32)
        sel = (yrs >= BASE_PERIOD[0]) & (yrs <= BASE_PERIOD[1])
        _warm_cache[key] = float(d['dt'][sel].mean())
        # 只留当前代次: 键带 generation ⇒ 旧代次的条目已经是死数据(否则
        # 每被 `store.clear_cache()` 一次就多留一个 float, 无界)。
        for _k in [k for k in _warm_cache if k[1] != key[1]]:
            _warm_cache.pop(_k, None)
    yrs = d['year'].astype(np.int32)
    i = int(np.clip(clock.year - int(yrs[0]), 0, yrs.size - 1))
    dt_now = float(d['dt'][i, int(clock.month) - 1])
    return dt_now - _warm_cache[key]


def _warming_field(name: str, clock, shape) -> Optional[np.ndarray]:
    """趋势贡献 = ΔT(t) × 趋势型。name: sst / d26 / shear。"""
    d = store.load('warming')
    if d is None:
        return None
    key = 'pat_%s' % name
    if key not in d:
        return None
    pat = d[key].astype(np.float32)
    if pat.shape != shape:
        pat = _upsample(pat, key=('warm', name)) if pat.shape[1] != shape[1] else pat
    if pat.shape != shape:
        return None
    mul = float(sim_params_get('warmMul', 1.0))
    return (pat * (_warming_delta(clock) * mul)).astype(np.float32)


def sim_params_get(k, default=None):
    return _CUR_PARAMS.get(k, default)


_CUR_PARAMS: dict = {}


#: ModeSeries 缓存(键 = override 的稳定签名)。见 _mode_anomaly。
#: ⚠ 轮次 160 做严: 键里再拼 `store.generation()`。否则运行中换掉 `modes_index.npz`
#: 之后, 重算虽然会走 `store.load()` 重读文件, 但这里会命中**旧 ModeSeries**
#: (它的 `val/ok/year/month` 全是旧表的)。
#: 注意: `modes._fit_ar()` 在 `modes.py` 里另有一层 `_AR` latch, 见 `clear_cache()`。
#: (perf-reviewer 实测发现, 见 VERIFY_SOLVE_MEMO.md / task-13)
_MS_CACHE: dict = {}


def _mode_anomaly(name: str, clock, shape) -> Optional[np.ndarray]:
    """Σ_mode Σ_lag 指纹 · 索引(F3b)。产物没烘焙时返回 None。

    产物格式(tools/train_env_modes_pat.py):
      modes_pat.npz: <field>__<mode> → (L, nlat, nlon) 在该字段的原生网格;
                     lag_<mode> → 滞后列表, 第 i 层配 idx(t + lag_i)
                     (带滞后 = 免费的传播); scale_<field> → 振幅定标系数。
    目前有 sst(NCEP 海上气温代理 + 按 ONI 定义定标)与 shear(NCEP 真值,
    系数即 kt/σ)。d26 还缺长序列(AOML 只有 2022-2026), 返回 None。
    """
    d = store.load('modes_pat')
    if d is None or clock is None:
        return None
    from . import modes as _M
    if not _M.available():
        return None
    # **ModeSeries 必须缓存**: 它的构造函数要做 AR 拟合 + 载表, 实测 2.9 ms/次,
    # 而 _mode_anomaly 每次 env_update 被调 3 遍(sst/d26/shear)。
    # 它只依赖 override, 所以按 override 的稳定签名缓存即可。
    # 签名不能用 id(dict) —— 那是 _upsample/sst_clim_cached 踩过的同一个坑。
    # ⚠ 轮次 160: 键再拼 `store.generation()` ⇒ 换掉 modes_index.npz 之后
    #   ModeSeries 会被重建(否则拿到的还是旧表的 val/ok/year/month)。
    _ov = _CUR_PARAMS.get('modeOverride') or {}
    try:
        _sig = tuple(sorted((str(k), str(v)) for k, v in _ov.items()))
    except Exception:
        _sig = ()
    _sig = (store.generation(), _sig)
    ms = _MS_CACHE.get(_sig)
    if ms is None:
        try:
            ms = _M.ModeSeries(override=_ov)
        except Exception:
            return None
        if len(_MS_CACHE) > 8:
            _MS_CACHE.clear()
        _MS_CACHE[_sig] = ms
    # nino34 与 oni 是同一个现象(Niño3.4 的绝对海温 vs 异常), 两条一起加会把
    # ENSO 的振幅翻倍。只保留 oni。
    _skip = {'nino34'} if 'oni' in d else set()
    acc = np.zeros(shape, np.float32)
    hit = False
    for k, nm in enumerate(ms.names):
        if nm in _skip:
            continue
        kk = '%s__%s' % (name, nm)          # 产物按 字段__模态 存
        if kk not in d:
            continue
        pats = np.asarray(d[kk], np.float32)
        if pats.ndim != 3:
            continue
        lk = 'lag_%s' % nm
        lags = np.asarray(d[lk], np.int32) if lk in d else             np.arange(pats.shape[0], dtype=np.int32) - pats.shape[0] // 2
        for i in range(min(len(lags), pats.shape[0])):
            yy, mm = _shift_month(clock, int(lags[i]))
            v = float(ms.at(yy, mm)[k])
            if abs(v) < 1e-6:
                continue
            p = _upsample(pats[i], key=('pat', name, nm, int(lags[i])))
            if p.shape != shape:
                continue
            acc += p * v
            hit = True
    return acc if hit else None


def _shift_month(clock, n: int):
    m = clock.month - 1 + n
    y = clock.year + m // 12
    return y, (m % 12) + 1


#: 环流型响应型的重采样缓存(静态, 只需一次)。
_REG_RESP_CACHE: dict = {}

#: 环流型(regimes)缓存。见 `_regime_anomaly`。
#: ⚠ 轮次 160 做严: `'gen'` 记下建表时的 `store.generation()`; 代次一变就**整块重建**,
#: 并清掉由旧表派生的 `_REG_RESP_CACHE`。否则运行中换掉 `regimes.npz` 之后, 即使
#: `store.generation()` 让 `solve` 重算, 重算过程读到的仍是**旧表**
#: (store 会重读文件, 但这里的 latch 不会)。
#: (perf-reviewer 实测发现, 见 VERIFY_SOLVE_MEMO.md / task-13)
_REG = {'ok': False, 'gen': None, 'eof': None, 'pc': None, 'date': None, 'resp': {}}


def _regime_tables():
    g = store.generation()
    if _REG['ok'] and _REG.get('gen') == g:
        return _REG
    # 首次 / 代次变了: 整块重建(并把旧表派生的重采样缓存一并丢掉)
    _REG_RESP_CACHE.clear()
    _REG.update({'ok': True, 'gen': g, 'eof': None, 'pc': None, 'date': None,
                 'resp': {}})
    try:
        from . import store as _st
        z = _st.load('regimes')
        if z is not None and 'pc' in z:
            _REG['pc'] = np.asarray(z['pc'], np.float64)
            _REG['date'] = np.asarray(z['date'], np.int64)
            names = [str(x) for x in z['resp_names']] if 'resp_names' in z else []
            for nm in names:
                k = 'resp_' + nm
                if k in z:
                    _REG['resp'][nm] = np.asarray(z[k], np.float64)
    except Exception:
        pass
    return _REG


def _regime_pc(clock):
    """该时刻的环流型 PC(按日取; 超出范围用最近年份的同日序形)。"""
    T = _regime_tables()
    if T['pc'] is None or clock is None:
        return None
    key = clock.year * 10000 + clock.month * 100 + clock.day
    d = T['date']
    i = int(np.searchsorted(d, key))
    if i >= len(d):
        # 未来日期: 用**历史类比重采样** —— 取同月同日的历史年(保留真实持续性,
        # 又不会产生训练范围外的极端值)。这是设计方案 §2.5 定的做法。
        md = clock.month * 100 + clock.day
        cand = np.nonzero((d % 10000) == md)[0]
        if len(cand) == 0:
            return None
        i = int(cand[clock.year % len(cand)])
    return T['pc'][i]


def _regime_anomaly(field_name: str, clock, shape):
    """环流型对某变量的异常贡献: Σ_r resp_r · PC_r(t)。

    设计方案的 2.4 节: 型是 Z500 异常的 EOF(真实的), PC 是逐日实测序列,
    响应型是已有月均场对月均 PC 的回归系数。这一项提供 **10-60 天的真实低频
    变率** —— 正是"所有风暴看到的环境趋同、峰值分布太窄"缺的那块。
    """
    T = _regime_tables()
    _key = field_name
    resp = T['resp'].get(_key)
    if resp is None and len(field_name) > 1 and field_name[0] in 'uv':
        # 训练产物的键是 uwnd850/vwnd850…, 环境场用的是 u850/v850…
        # **必须按首字母分派**: 原来是 'uwnd'+field_name[1:] 与 'vwnd'+field_name[1:]
        # 两个候选依次试, 于是 'v850' 会先命中 'uwnd850' 拿到错的分量(实测
        # u850 与 v850 的异常场一模一样)。
        _pre = 'uwnd' if field_name[0] == 'u' else 'vwnd'
        _key = _pre + field_name[1:]
        resp = T['resp'].get(_key)
    if resp is None:
        return None
    pc = _regime_pc(clock)
    if pc is None:
        return None
    # resp: (n_r, nlat_r, nlon_r) —— 与 base 网格不一定一致, 用最近邻重采样
    ny, nx = shape[0], shape[1]
    ck = ('reg', _key, ny, nx)
    resp = _REG_RESP_CACHE.get(ck)
    if resp is None:
        _r0 = T['resp'][_key]          # 用**解析后的键**(u850→uwnd850)
        ry, rx = _r0.shape[1], _r0.shape[2]
        if (ry, rx) != (ny, nx):
            # 最近邻重采样 —— 只做一次并缓存。原来每次调用都做, 实测
            # 40 次 tensordot 花 136 ms 而 reshape 另花 117 ms。
            jj = np.clip((np.arange(ny) * ry // ny), 0, ry - 1)
            ii = np.clip((np.arange(nx) * rx // nx), 0, rx - 1)
            resp = np.ascontiguousarray(_r0[:, jj][:, :, ii])
        else:
            resp = _r0
        _REG_RESP_CACHE[ck] = resp
    a = np.tensordot(pc, resp, axes=(0, 0))
    return a.astype(np.float32)


#: ── 轮次 160: `solve` 的**按天记忆化** ────────────────────────────────────
#: 实测(2026-09-30, `tools/_probe_envmodel_r160.py` / `_probe_solve_grain_r160.py`):
#:   · `solve` 每次 **72 ms**, 而 `build_env_fields` 整体只有 104 ms ⇒ solve 占 69%;
#:   · `build_env_fields` 又占 `_env_update_impl` 的 ~40%(145 ms/362 ms), 是热区第一;
#:   · **关键实测: `solve` 的输出在"一天之内"完全不变** —— 同一天 00/06/12/18 时
#:     19 个字段**逐位相同**; 而换天/换月/换年则 15 个字段全变。
#:     原因: `_month_weight` 只用 `clock.day`/`clock.month`(整数天),
#:     模态/环流型索引同样是日粒度。
#:   ⇒ 6 小时步长下**一天 4 步可复用 3 次**, 期望省 ~54 ms/步。
#:
#: **为什么逐位安全**: 键覆盖了全部决定输出的输入(见 `_memo_key`); 命中时返回的是
#: 同一批数组。为了防止下游**就地改写**污染缓存, 缓存里的每个数组都被设为
#: `writeable=False` —— 真有就地写会**立刻抛异常**(而不是安静地把历史改掉)。
_SOLVE_MEMO: dict = {}


def _val_fp(v):
    """单个参数值的**稳定指纹**。

    ⚠⚠ 不能用裸 `repr`: numpy 对长数组的 repr 会**截断成省略号** ⇒ `np.zeros(2000)`
    与"只改中间一个元素"的同尺寸数组会得到**同一个 repr**, 于是同一个键、静默返回
    旧值。当前 `core` 传进来的 params 全是标量/str/dict ⇒ 潜在而非现行, 但一旦有人
    把数组场塞进 `sim['params']` 就会踩。ndarray 一律走 `dtype+shape+sha1(tobytes())`。
    (test-hardener 实测发现, 见 tests/test_envmodel_solve_memo.py)

    ⚠ 轮次 160(task-13)把**残余碰撞**逐个收掉 —— 编码里带**类型标签**:
      · `[1,2]` 与 `(1,2)`(list vs tuple)、`{1,2}` 与 `frozenset({1,2})` 原来同指纹
        ⇒ 现在分别是 `l[…]` / `t(…)` / `s{…}` / `fs{…}`(**假性命中**, 危险方向);
      · `object`/结构化 dtype 的 ndarray: `tobytes()` 拷的是**指针**不是内容, 原来
        一旦抛异常就退到 `'nd?dtype:shape'`(**丢内容** ⇒ 所有同形状对象数组同指纹)
        ⇒ 现在退到 pickle 内容哈希、再退到 repr 内容哈希(**假性命中**, 危险方向);
      · ndarray **子类**(如 `MaskedArray`)带类名, 且 `mask` 内容也进指纹 ——
        普通 `ndarray` 的编码**保持原样**(键字符串不变, 不会平白改变命中模式)。

    仍然**已知不区分**(都属"取值相同 ⇒ 行为相同", 或极端输入, 方向安全):
      · 所有 NaN(`repr(float('nan'))` 恒为 `'nan'`)—— 下游 `float()` 后都是 NaN;
      · `np.float32(1.0)` 与 `np.float64(1.0)` 这类**同值不同 dtype** 的标量(见下);
      · 不可 pickle 的对象数组退到 `repr(v.tolist())`, 其 repr 可能含地址 ⇒ 跨进程
        可能不同 ⇒ 只造成**假性 miss**, 不会假性命中。
    """
    if isinstance(v, np.ndarray):
        h = None
        try:
            h = hashlib.sha1(np.ascontiguousarray(v).tobytes()).hexdigest()
        except Exception:
            h = None
        if h is None or v.dtype == object:
            # object/结构化 dtype: `tobytes()` 拷指针, 不能代表内容 ⇒ 换内容哈希
            try:
                h = 'p' + hashlib.sha1(pickle.dumps(
                    np.ascontiguousarray(v), protocol=4)).hexdigest()
            except Exception:
                try:
                    h = 'r' + hashlib.sha1(repr(
                        np.ascontiguousarray(v).tolist()).encode('utf-8')).hexdigest()
                except Exception:
                    h = 'x'                      # 连 repr 都不行 ⇒ 只剩 dtype+shape
        _tn = type(v).__name__
        _pre = '' if _tn == 'ndarray' else _tn + ':'
        _mask = getattr(v, 'mask', None)          # 普通 ndarray 没有 mask ⇒ ''
        _ms = '' if _mask is None else '|m' + _val_fp(np.asarray(_mask))
        return 'nd:%s%s:%s:%s%s' % (_pre, v.dtype.str, v.shape, h, _ms)
    # ⚠ 轮次 160: **刻意不做** `np.generic → v.item()` 规范化。
    # 收益 ≈ **0**(`sim['params']` 的值类型在一次运行里是稳定的 ⇒ 实践中不会因
    # "np 标量 vs Python 标量"产生假性 miss); 代价是**改动指纹编码**本身。
    # ⚠ 措辞修正(task-13, 复核后): 我原来写"它会折叠 float32/float64 ⇒ 危险方向"——
    # **这个理由不成立**: `np.float32(x).item()` 只把**float64 取值相同**的标量折叠到
    # 一起(取值不同的 float32/float64 转成不同的 Python float, 例如
    # `np.float32(16777217).item()==16777216.0` 而 `np.float64(16777217).item()==16777217.0`),
    # 而"取值相同 ⇒ 下游 `float()` 后行为相同" ⇒ 折叠并不危险。
    # 不采纳的真正理由是: **零收益 + 裸 `repr` 严格更细(只多假性 miss, 安全方向)
    # + 少一处要随 numpy 大版本维护的转换逻辑**。
    # 已由 `tests/test_envmodel_solve_memo.py::test_numpy_scalar_and_int_float_give_a_safe_false_miss` 钉住。
    if isinstance(v, dict):
        # 按 key 排序 ⇒ **插入顺序不同但逻辑等价**的 dict 得到同一个指纹
        # (裸 repr 会因顺序不同产生假性 miss)。
        # ⚠ 键也必须走 `_val_fp`: 原来用 `str(k)` 会把 `{1:'a'}` 与 `{'1':'a'}` 折叠成
        # 同一个指纹 —— 那是**假性命中**(危险方向)。字符串的 repr 自带引号, 因此不撞。
        return 'd{%s}' % ','.join('%s=%s' % (_val_fp(k), _val_fp(x))
                                  for k, x in sorted(v.items(),
                                                     key=lambda kv: _val_fp(kv[0])))
    if isinstance(v, list):
        return 'l[%s]' % ','.join(_val_fp(x) for x in v)
    if isinstance(v, tuple):
        return 't(%s)' % ','.join(_val_fp(x) for x in v)
    if isinstance(v, set):
        return 's{%s}' % ','.join(sorted(_val_fp(x) for x in v))
    if isinstance(v, frozenset):
        return 'fs{%s}' % ','.join(sorted(_val_fp(x) for x in v))
    return repr(v)


def _memo_key(sim_params: dict, clock):
    """记忆化键。**任何一个拿不准的输入都放进键里, 拿不准就别缓存。**"""
    if clock is None:
        return None
    try:
        # 参数指纹: 用**稳定指纹**而不是 id/hash —— `sim['params']` 会被**就地修改**,
        # 缓存 id 会漏掉改动。(整字典进键 ⇒ 属**过完备**, 安全方向。)
        fp = tuple(sorted((str(k), _val_fp(v))
                          for k, v in (sim_params or {}).items()))
        # ⚠ 瞬时场(单日 ERA5 单时次)是**按小时**变的 ⇒ 有它时必须把小时放进键,
        #   否则会把 00 时的场发给 06 时(静默错)。没有瞬时场时(月产品, 常见情形)
        #   按天即可, 这样才拿得到 75% 命中。
        _inst = bool(_instant_fields(clock))
        # ⚠⚠ 轮次 162 补(全量 pytest 暴露): **必须把"产物可用集"也放进键**。
        # 原来只有 `store.generation()`, 而它只在 `clear_cache()` 时自增 —— 若
        # `store.available()` 的结果被改变(测试 monkeypatch、或将来运行中动态
        # 增删产物文件)而没走 `clear_cache()`, 代次不变 ⇒ **命中上一份完整结果**,
        # 于是"产物缺失应回落"的分支永远走不到。
        # 实测(exposed by tests/test_sim_mode_data.py::test_data_mode_partial_visible):
        # 该用例把 `store.available` 换成 `lambda: set()`, 期望 `dataState='partial'`,
        # 实际拿到缓存里的 `'loaded'` ⇒ `assert 'loaded' == 'partial'` 失败。
        # `available()` 自己有缓存(listdir 只做一次), 这里只是取 frozenset。
        return (fp, clock.year, clock.month, clock.day,
                clock.hour if _inst else -1,
                tuple(_env_dims()), store.generation(),
                frozenset(store.available()))
    except Exception:
        logger.debug('solve 记忆化键构造失败, 本次不缓存', exc_info=True)
        return None


def _freeze(out):
    """把缓存出去的数组设为只读 —— 就地写会立刻炸, 不会安静污染历史。

    ⚠ 已知边界(perf-reviewer, VERIFY_SOLVE_MEMO.md): 这个守卫会**跨模块生效** ——
    走瞬时场那天 `out['sst']`/`out['ohc_kj']` 一度就是 `daily._CACHE` 里的同一对象。
    已在 `_base_field` 里对瞬时场 `copy=True` 堵住入口; 若将来别处再出现跨模块别名,
    本守卫会**替别人失败**(且是顺序相关的)。另外它对 `clock=None` 的路径不生效
    (`_memo_key` 返回 None ⇒ 根本不缓存 ⇒ 数组保持可写), `deepcopy` 会丢掉该标志
    (pickle 保留)。
    """
    for v in out.values():
        if isinstance(v, np.ndarray):
            try:
                v.flags.writeable = False
            except Exception:
                pass
    return out


def clear_solve_memo() -> None:
    """清空按天记忆化(`store.clear_cache()` 后由调用方按需调用)。"""
    _SOLVE_MEMO.clear()


def solve(sim_params: dict, clock=None) -> Optional[dict]:
    """`_solve_uncached` 的**按天记忆化**外壳(轮次 160)。语义完全不变。

    返回的是缓存的**浅拷贝 dict**(数组仍是同一批只读对象) —— 这样调用方增删键
    不会污染后续命中。perf-reviewer 实测过: 往返回的 dict 里塞 `__probe__` 会
    真的进 `_SOLVE_MEMO`。(用浅拷贝而不是 `MappingProxyType`, 是为了不改变
    `isinstance(..., dict)` 的语义。)
    """
    key = _memo_key(sim_params, clock)
    if key is not None:
        hit = _SOLVE_MEMO.get(key)
        if hit is not None:
            return dict(hit)
    out = _solve_uncached(sim_params, clock)
    if key is not None and out:
        _SOLVE_MEMO.clear()          # 只保留当前那一刻, 不随运行时长增长
        _SOLVE_MEMO[key] = _freeze(out)
        return dict(_SOLVE_MEMO[key])
    return out


def _solve_uncached(sim_params: dict, clock=None) -> Optional[dict]:
    """构建内核 DATA 字典。缺关键产物 → None(上层回落)。"""
    if not base_available():
        return None
    _CUR_PARAMS.clear()
    _CUR_PARAMS.update(sim_params or {})
    out = {}
    # **瞬时场优先级**: 单日 ERA5/CMEMS 缓存存在时, 基态本身就是"那一刻的真实观测",
    # 增暖趋势与模态/环流型异常**已经包含在里面** —— 再叠一遍就是重复计入
    # (实测 1997-09 东太赤道: 瞬时 26.51 → 又叠了模态 +3.8 → 运行时 30.35,
    #  而真实只有 +2.8; 这是"El Niño 被算了两遍")。
    _inst = _instant_fields(clock)
    _inst_on = bool(_inst)
    sst = _base_field('sst', clock)
    if sst is None:
        return None
    # F4: 增暖趋势 + F3: 模态异常(都以"每 1°C / 每 1σ"的型线性叠加)
    for nm, fld in (('sst', 'sst'), ('d26', 'd26'), ('shear', 'shear')):
        base = _base_field(fld, clock)
        if base is None:
            base = sst if fld == 'sst' else None
        if base is None:
            continue
        if not _inst_on:
            a = _warming_field(nm, clock, base.shape)
            if a is not None:
                base = base + a
            ma = _mode_anomaly(nm, clock, base.shape)
            if ma is not None:
                base = base + ma
            ra = _regime_anomaly(fld, clock, base.shape)
            if ra is not None:
                base = base + ra
        if fld == 'sst':
            sst = base
        else:
            out[fld] = base.astype(np.float32)
    out['sst'] = sst
    for k in ('d26', 'shear', 'u850', 'v850', 'u500', 'v500', 'u300', 'v300',
              'u200', 'v200', 'rh700', 'mslp', 't200'):
        # (环流型对风场的响应用 _regime_anomaly 在下面统一叠加)
        # 不能无条件覆盖: d26/shear 已经在上面吃过增暖与模态异常, 这里再写一遍
        # 等于把趋势抹掉(实测 2150 年 +7.8 m 的加深被冲成 0)。
        if k in out:
            continue
        v = _base_field(k, clock)
        if v is not None:
            # **环流型(regimes)对风场的响应** —— 这是 10-60 天真实低频变率的
            # 来源。必须在**风场这一层**叠加: 上面的三变量循环只覆盖
            # sst/d26/shear, 而训练出来的响应型是 uwnd850/vwnd850… 等 8 个风场。
            # 实测漏掉这一步的后果是"接入环流型后全年回放字节数完全相同"。
            if not _inst_on:                     # 瞬时场已含低频变率, 不再叠加
                ra = _regime_anomaly(k, clock, v.shape)
                if ra is not None:
                    v = v + ra
            out[k] = v.astype(np.float32)
    # 内核契约: uMid/vMid 由 850 与 200 合成(与旧库一致)
    # 键名对齐 core.env_update 的契约: 它要的是 'rh', 而产物里叫 'rh700'
    if 'rh700' in out and 'rh' not in out:
        out['rh'] = out['rh700']
    if 'u850' in out and 'u200' in out:
        out['uMid'] = (0.45 * out['u850'] + 0.55 * out['u200']).astype(np.float32)
        out['vMid'] = (0.45 * out['v850'] + 0.55 * out['v200']).astype(np.float32)
    # ── 风切标定(2026-09-19) ──
    # 基态是**月平均**风场, 而 |V200−V850| 用的是"均值的矢量差", 它比
    # "矢量差的日常值"系统性偏大(月均把瞬变涡动平滑掉了, 剩下的平均经向梯度
    # 更强): 实测基态全球均值 34.9 kt、最大 132 kt, 而 CIMSS/CIRA 口径的
    # 气候态是均值 ~17 kt、急流核 ~65 kt。乘 0.55 之后均值 19.2、最大 72.6,
    # 与观测口径一致 —— 这才是"西风带风切看得见、热带风切不至于 50kt"的关键。
    if _inst_on:
        # **"瞬时"要分清是哪几层**(2026-09-20): 洋区 OHC 用 ±3 天窗口, 而 ERA5 单日
        # 只有 ±6 小时窗口 —— 模拟钟往后走几小时, 就会出现"OHC 还是瞬时、SST/风切已经
        # 回落月均"的混合态。这时如果只报"瞬时"就是撒谎(实测 18Z 后风切已回落 18.6 kt,
        # 徽标仍显示瞬时)。所以: 关键层(sst+shear)全瞬时才算"瞬时", 否则算"瞬时·部分"。
        _has_sst = 'sst' in _inst
        _has_sh = 'shear' in _inst
        _has_ohc = ('ohc' in _inst) or ('d26' in _inst)
        out['_instant_groups'] = {'sst': _has_sst, 'shear': _has_sh, 'ohc': _has_ohc}
        out['_instant'] = bool(_has_sst and _has_sh)
        out['_instant_partial'] = bool(_has_sst or _has_sh or _has_ohc)
        out['_instant_src'] = _inst.get('_source')
        # **标记必须传下去**: 原来只写了 _instant, 而下面的 0.55 门控看的是
        #  —— 结果瞬时风切**又**被乘了 0.55
        # (实测 41.2 → 22.7 kt, 等于把 42.8 的真实切变压成 22.9)。
        if _has_sh:
            out['_shear_instant'] = True
    if 'shear' in out and not out.get('_shear_instant'):
        # 只有**月均**口径才需要这个 0.55(月均的矢量差比日尺度大 ~1.8 倍);
        # 瞬时场(单日 ERA5)的 |V200-V850| 就是真实切变, 不能再乘
        out['shear'] = (np.asarray(out['shear'], np.float32) * 0.55).astype(np.float32)
    # F5 之前: env['ohc'] 需要 D26(米); 显示用 kJ 由 core 另算
    if 'd26' in out:
        out['ohc'] = out['d26']              # 物理: D26(米)
    tchp = _base_field('tchp', clock)
    if tchp is not None:
        # **增暖必须出现在显示用的 TCHP 上**(2026-09-21 实测): 这里原来直接取未加增暖的
        # base_tchp, 于是 2100 与 2025 的 ohc_kj 场**逐字节相同**, 而物理量 env['ohc'](=d26)
        # 是暖过的 —— 界面/视频上的 OHC 层因此看不到任何气候趋势。
        # 关系式: TCHP = ρcp·(T̄−26)·D26; 在"层平均超温不变"的假设下同一格 **TCHP ∝ D26**,
        # 所以用模型**自己**的 d26 暖/未暖比值缩放 —— 不引入任何新的自由参数。
        # 物理量一个都不动: MPI 吃的是 env['ohc'](d26, 米), 这里是显示口径。
        if not _inst_on:
            _d0 = _base_field('d26', clock)
            _d1 = out.get('d26')
            if (_d0 is not None and _d1 is not None
                    and np.shape(_d0) == np.shape(tchp)):
                _r = np.where(np.asarray(_d0, np.float32) > 1.0,
                              np.asarray(_d1, np.float32) /
                              np.maximum(np.asarray(_d0, np.float32), 1e-6), 1.0)
                tchp = (np.asarray(tchp, np.float32)
                        * np.clip(_r, 0.2, 3.0)).astype(np.float32)
        out['ohc_kj'] = tchp                 # 显示: TCHP(kJ/cm²)
    if 'shear' not in out and 'u200' in out and 'u850' in out:
        out['shear'] = np.hypot(out['u200'] - out['u850'],
                                out['v200'] - out['v850']).astype(np.float32)
    return out
