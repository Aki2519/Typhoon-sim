# -*- coding: utf-8 -*-
"""时间轴回放的快照 / 回溯机制(轮次 158)。

**这个文件不 import `simulator.simcore.core`, 也不 import TySim。**
它把"取状态 / 置状态"抽象成对 **dict 状对象**的读写, 所以可以完全脱离内核
单测(tests/test_simcore_rewind.py 就是纯标准库 + 一个 20 行的假 sim)。

────────────────────────────────────────────────────────────────────────
一、为什么能做得这么便宜: 引用快照 + 少数真拷贝
────────────────────────────────────────────────────────────────────────
工程笔记原本就写明了这条性质(core.py:4417-4418):

    "数组用**引用快照**即可: `env_update` 对每个大场都是 `env[k] = <新数组>`,
     旧数组不会被原地改写。"

我把 `_env_update_impl`(core.py:1388-1818)里 `env[...]` 的**每一个**写入点
都过了一遍, 确认全部是 `env[k] = <新数组>.astype(np.float32)` 这种 **rebind**,
没有任何 `env[k][...] = ` / `env[k] *= ` 的原地改写 —— 行内的那些 `+=` / `-=`
(core.py:1498-1500 的 shear、1506-1533 的 rh、1543-1563 的 pe)都作用在**局部
临时量**上, 最后统一发布到 `env[k]`, 而 `sim['env']` 这个 **dict 对象本身**在
整个进程里恒定不变(core.py:4439-4440 与 4465-4476 的"clear+update"就是证据)。
=> **整张 env 场按引用进快照, 0 字节。**

同样是 rebind 的还有 `sim['cld']`(core.py:4010)、`sim['ensemble']`
(core.py:4595/4600)、每个 tc 的标量键(core.py:2343/2439/2846/3288/3315 ...),
所以它们也只存引用。

必须**真拷贝**的只有下面这些"原地改写"的:

  sim['wake'] / sim['ohcWake']   core.py:4303-4309  ← np.copyto / *= (reshape 视图原地写)
  sim['ocn']                     core.py:4320-4321  ← `ocn['Tml'] += _dT` / `ocn['h'] += _dh`
  sim['eddies']                  core.py:3400-3413  ← 逐字段 `+=` 之后还整体重掷
  sim['dryBlobs']                core.py:3414-3422  ← 同上, 而且 `remove` + `append` 会改**顺序**
  sim['_pendStep']               core.py:4747-4771  ← `pend['i'] += 1` / `pend['el'] +=`
  sim['_envBase']                core.py:4798-4805  ← 元组里装两个大数组, 拷贝上限 2×1.04MB
  sim['enso']                    core.py:1353-1375  ← `e['t'] = now` 是**积分分段边界**,
                                                      不还原则 t 之后的积分步数不同 ⇒ 不逐位一致
  sim['params'] / sim['usedNames'] / sim['investSeq'] / sim['cySeq']  ← 浅拷贝(标量/计数器)
  sim['land'] / sim['cldNoise'] / sim['start'] / sim['seed'] / ...
                                 静态或不该回退的常量(见二、与 SkipKeys)

────────────────────────────────────────────────────────────────────────
二、我**决定不存**的东西, 以及恢复时怎么显式失效
────────────────────────────────────────────────────────────────────────
1) `track` / `hist`(**不复制, 只记长度**)
   理由: 每 1 模拟小时给每个 tc 追加一条(core.py:4841-4864), 实测 track 点
   272 B / hist 记录 464 B; 3 个满载台风 = 2.96 MB —— 每个快照都深拷贝它是
   纯浪费。上限截断 `tc['hist'] = tc['hist'][-720:]`(core.py:4862/4864)是
   **rebind** 而不是原地删, 所以快照持有的旧 list 引用不会被截断污染; 而两个
   list 的**元素本身**全仓没有任何原地改写(grep `tc['track'][` / `tc['hist'][`
   只有读访问: app/ty_sim_mixins/sim_mode_mixin.py:298)。
   恢复: `del lst[n:]` 就地截断。
   ⚠ 代价: 回溯后重放产生的尾点与原来不同, 所以**恢复点之后的快照全部作废**
   (线性时间轴语义, 也是 redo 必须失效的原因)。

2) `land`(360×720 float32 = 1.04 MB)/ `cldNoise`(360×360, 一次性建, core.py:866-882)
   理由: 进程内**恒定**, 从不 rebind 也从不原地改写(land 的所有引用都是只读:
   core.py:621/646/4224)。存引用 = 0 字节。

3) `start` / `month0` / `year0` / `seed` / `envStepH`(**knob, 回放不该动**)
   理由: 它们是用户/UI 的设定, 不是模拟状态。拖回时间轴不应该把起始日期或
   环境步长改回去 —— 那等于"回放顺手改了配置"。
   ⚠ 但 `sim['month']` 与 `sim['t']` **存**: 它们由 `clock(sim)` 从 `t` 推出来
   (core.py:4780-4782), 虽然可以重算, 存下来能保证"逐位一致"不依赖日历实现。

4) `params`(**只记录, 恢复时不写回**)
   理由: 设置面板直接写 `sim['params'][k]`(app/sim_settings_dialog.py 一处,
   由 organizer 串行落地)。拖回时间轴**不该**把用户刚改的配置一起回退 ——
   那不是"回放", 那是"顺手改配置"。
   实现(轮次 159 定的语义, 见 `RewindPolicy.keep_items`): 采集时存一份**浅拷贝**
   (`dict(v)`, 所以快照与活 dict 不是同一个对象 —— 快照自描述, 也不会被后续
   改动污染), 但 `restore()` **跳过写回**。
   ⚠ 历史: 轮次 159 之前是"浅拷贝进快照 + 恢复时 `target[k] = v` 整体 rebind",
   活 dict 被换成快照里那份旧拷贝 ⇒ 用户的改动照样被回退
   (tests/test_simcore_rewind.py:511 卡住的就是这条)。
   ⚠ 代价(刻意): 回放期间改 params 会让新参数**立刻**作用于被恢复的未来。

5) `_pDispAnom`(**存引用, 不失效**)
   理由: 它是 `pDisp` 的时间平滑记忆(core.py:1769-1772)。不还原则回溯后第
   一帧的显示气压跳变(用户能看见); 还原则严格回到当时的显示状态。选**存**。

6) 恢复时必须**显式失效**的缓存(见 `RewindStore.restore` 的实现):
   · `sim['_envVer']`: 先还成快照值, 再 **+1**。它是 `_OCEAN_UV` 的缓存键
     (core.py:4273 `('usv', _envVer, shape)`)与渲染图层的复用键。回退会让版本号
     **撞上**旧内容 ⇒ 必须强制 bump, 让所有按版本号做键的缓存全部落空。
     轮次 159: 只 bump **快照里真的有**的版本键 —— 该键在恢复点还不存在时(还没
     跑过第一次 env_update / 云图更新), 恢复后也不能凭空造一个出来, 否则 `sim`
     的键集会偏离快照点。内核自己就是这个口径: core.py:4461
     `if keep_ver is not None: sim['_envVer'] = keep_ver`。
   · `sim['_cldVer']`: 同样 bump(core.py:3945, 渲染云图复用键)。
   · `_EDDY_FIELD`(core.py:1276): **不需要**失效 —— 键是 `(key, int(t//24))`
     (core.py:1295), 含量化时间, `f(t)` 是纯函数, 命中即正确。
   · `_NOISE_SPEC`(core.py:952)/ `_GEN_CACHE`(core.py:3439): 只依赖
     `_stable_seed`(crc32, core.py:919-928)与静态表, 与时间无关, 不用失效。
   · `sim['lastEnvH']` / `lastEnsH` / `lastGenH` / `lastOceanH`: **存**(标量,
     动态取键), 否则回溯后有一次"该刷新却没刷新"或"重复刷新"的物理静默偏差。
     `lastOceanH` 在 `init()` 里**根本不存在**(只有 core.py:4818-4819 创建),
     所以本模块按"当前实际存在的键"动态取, 不按 init 的键表硬编码。

7) `rng` / `gauss`: **闭包, copy.deepcopy 不会复制它们的状态**
   实测(本机 Python 3.14):
       deepcopy(sim)['rng']   is sim['rng']   -> True
       deepcopy(sim)['gauss'] is sim['gauss'] -> True
   两个函数的状态都活在 `__closure__` 的 cell 里:
       rng   的 co_freevars = ('a',)          cell 装 int(32 位状态)
       gauss 的 co_freevars = ('rng','spare') cell 装 function / list
   一个"看起来完整"的 deepcopy 快照恢复后随机流仍在**未来**, 重放轨迹立刻分叉
   且不会报错。本模块按 cell 逐个捕获 + 按名字回写(不写死下标), 并在恢复时校验
   `co_freevars` 一致 —— 名字对不上就拒绝恢复, 而不是静默给错状态。
   `gauss` 的 `spare` 是 list ⇒ 深拷贝, 恢复时用 `spare[:] = saved` 原地填回,
   保持**同一个 list 对象**(闭包捕获的是对象引用, 换成新 list 不会生效)。

────────────────────────────────────────────────────────────────────────
三、内存自适应
────────────────────────────────────────────────────────────────────────
`estimate_state_bytes()` / `estimate_snapshot_bytes()` 完全按 numpy 的
`nbytes` + 容器开销算(见 `_obj_bytes`), 于是运行时可以:

反推出"在给定内存预算下能存多少份 / 回溯多久":

    p = store.plan(budget_bytes)          # 见 RewindStore.plan() 的 docstring
    # p -> {'budget','per_snapshot','snapshots','hours','interval_h'}

⚠ **2026-09-30 更正(verifier 文档审计)**: 本段原先写的是
`n = rw.suggest_snapshots(budget_bytes, 目标回溯小时 / 每步小时)` 并说
"`RewindStore.plan()` 直接返回三元组" —— **两处都不对**: 全仓**没有**
`suggest_snapshots` 这个函数(照抄会 `AttributeError`), 而 `plan()` 返回的是**上面那个
5 键 dict**、不是三元组。请以 `RewindStore.plan()` 的 docstring 为准。
`footprint_bytes` / `rewind_hours()` 供 UI 显示。默认 `max_bytes=DEFAULT_MAX_BYTES`
(256 MB) 配合环形缓冲, 既可限份数也可限字节。

⚠ 但**一份快照的 footprint 不等于整状态深拷贝的估算**: 引用快照(§1)里的大场是
与活状态**共享**的, `MISSED_BY_DESIGN`(§2-1)连 list 都没复制 —— 它们都不该记在
任何一份快照的账上。`_owned_bytes()` 就是"只算真正新分配的那部分"的口径: 深拷贝
键全算、浅拷贝键只算容器表、引用键一分不算(ndarray 的 `copy.copy` 会真复制缓冲,
所以照 `nbytes` 全算)。轮次 159 修正: 修正前直接对 `values` 整体 `_obj_bytes`,
实测 40 步时 `footprint = 54312 B` 反而**大于**整状态的估算 `54259 B` —— 与上面
"引用快照的增量远小于整状态"自相矛盾, 后果是字节预算把每一份都判成"单份超预算"
而拒收, 40 步只留下 2 份(tests/test_simcore_rewind.py:714)。

单位(与 simcore 一致): 时间 h, 气压 hPa, 风 kt, 距离 km。
"""

from __future__ import annotations

import copy
import sys
from bisect import bisect_right
from collections import deque
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = [
    'DEFAULT_INTERVAL',
    'DEFAULT_MAX_BYTES',
    'DEFAULT_MAX_SNAPSHOTS',
    'INPLACE_COPY',
    'MISSED_BY_DESIGN',
    'POLICY',
    'RewindPolicy',
    'RewindSnapshot',
    'RewindStore',
    'SkipKeys',
    'closure_state',
    'estimate_snapshot_bytes',
    'estimate_state_bytes',
    'format_bytes',
    'restore_closure',
    'snapshot_state',
]

#: 默认每多少步存一份(步 = 一次 `_sim_step_core`, 默认档 6h/步 ⇒ 默认回溯粒度 6h)
DEFAULT_INTERVAL = 1
#: 默认内存上限(字节)。为"可用内存只有 1-3 GiB"的本机留足余量
#: (物理 15.74 GiB, 但可用常年 1-3 GiB; 旧文写的 9GB 是 WSL 侧读数, 已作废);
#: 实测一份满载快照约 7 MB(见测试④)
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
#: 默认环形缓冲份数(与字节上限**同时**生效, 谁先到就按谁丢)
DEFAULT_MAX_SNAPSHOTS = 64

#: 容器/对象自身的开销估算(不含其内容)。CPython 3.14 的 dict 是紧凑表,
#: 这里取一个保守的常数, 目的是"估算为正且不低估"而不是精确 sizeof。
_CONTAINER_OVERHEAD = 64
#: 每个 dict 条目(键 + 值槽)的开销估算
_ENTRY_OVERHEAD = 40


class SkipKeys:
    """哨兵: 表示"这个键**完全**不进快照"(既不是引用也不是拷贝)。

    用在 `RewindPolicy.skip_closure` 上 —— `rng` / `gauss` 由闭包捕获机制单独
    处理, 不应再被当普通值走一遍。"""

    __slots__ = ()

    def __repr__(self) -> str:                                # pragma: no cover
        return 'SkipKeys'


#: 必须**真拷贝**的 `sim` 键(原地改写的, 逐条理由见模块 docstring 第一节)。
#: 值 = 深/浅(False = 浅拷贝容器即可, 因为里面装的是 rebind 的标量)。
INPLACE_COPY: dict[str, bool] = {
    'wake': False,          # ndarray, 直接 .copy()
    'ohcWake': False,
    'ocn': True,            # 里面 Tml/h 是 `+=` 原地写
    'eddies': True,         # 7 个 dict 原地改 age/lon/lat/amp/u/v
    'dryBlobs': True,       # 同上, 且 list.remove+append 会改顺序
    '_pendStep': True,      # pend['i']/['el'] 原地累加
    '_envBase': True,       # (sst_base, ohc_base) 两个 360x720 数组
    'enso': False,          # 浅拷贝即可: T/h/t 都是标量 rebind
    'params': False,        # 浅拷贝(只作记录); 恢复时**不写回**(见 keep_items)
    'usedNames': False,     # set, 浅拷贝
    'investSeq': False,     # {洋区: n}, 原地 +1
    'cySeq': False,         # {洋区: n}, 原地 +1
    'nextId': False,        # 标量, 引用快照即可(下面 _SCALAR 也会走到)
    'tcs': True,            # 台风列表: 逐 tc 深拷贝(标量键 + pred/hist 引用见 MISSED)
}

#: 只记长度、不复制内容的键(每个 tc 里的 list)。见 docstring 第二节 §1。
MISSED_BY_DESIGN: dict[str, str] = {
    'track': 'truncate',    # core.py:4864 每 1h 追加, 上限 2400 rebind 截断
    'hist': 'truncate',     # core.py:4862 每 1h 追加, 上限 720 rebind 截断
}

#: 默认策略:`sim` 本体由本模块管理, 上面三张表就是全部语义。
POLICY = None        # 延迟到 RewindPolicy 定义之后赋值(见文件末尾)


def _obj_bytes(o: Any) -> int:
    """递归估算一个对象占的字节数。只走 numpy/标准容器, 不 import 内核。

    - numpy 数组: 只算 `nbytes`(缓冲), 不再递归元素(元素是标量, 已含在里面);
    - list/tuple/set: 头部 + 逐元素;
    - dict/Mapping: 头部 + 逐条目;
    - 其他: `sys.getsizeof`。
    """
    nbytes = getattr(o, 'nbytes', None)
    if nbytes is not None and hasattr(o, 'shape') and hasattr(o, 'dtype'):
        return int(nbytes)                       # numpy ndarray
    if isinstance(o, (bytes, bytearray)):
        return len(o)
    if isinstance(o, str):
        return sys.getsizeof(o)
    if isinstance(o, Mapping):
        total = _CONTAINER_OVERHEAD + _ENTRY_OVERHEAD * len(o)
        for k, v in o.items():
            total += _obj_bytes(k) + _obj_bytes(v)
        return total
    if isinstance(o, (list, tuple, set, frozenset)):
        total = _CONTAINER_OVERHEAD
        for it in o:
            total += _obj_bytes(it)
        return total
    return int(sys.getsizeof(o))


def format_bytes(n: int) -> str:
    """人类可读的字节数(给 UI/日志用)。"""
    v = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if v < 1024.0 or unit == 'GB':
            return ('%d %s' % (n, 'B')) if unit == 'B' else ('%.2f %s' % (v, unit))
        v /= 1024.0
    return '%d B' % n                                                # pragma: no cover


# ── 闭包状态(rng / gauss) ────────────────────────────────────────────

def closure_state(fn: Callable) -> dict[str, Any]:
    """把闭包函数的状态拍下来: `{自由变量名: 值的深拷贝}`。

    为什么必须按 `co_freevars` 的**名字**定位: 写死下标(`__closure__[0]`)在
    `make_gauss` 这种双 cell 函数上必然错位 —— 实测
    `rng.__code__.co_freevars == ('a',)`、
    `gauss.__code__.co_freevars == ('rng', 'spare')`(轮次 158 实测)。

    值的处理:
      · int / float / str / bool / None → 原值(不可变, 不需要复制);
      · list(如 `gauss` 的 `spare`)  → 深拷贝(它是**可变**的缓冲槽);
      · 其他                          → 深拷贝兜底。

    非闭包(没有 `__closure__`)返回空 dict —— 调用方不应把它当错误。
    """
    code = getattr(fn, '__code__', None)
    cells = getattr(fn, '__closure__', None)
    if code is None or not cells or not code.co_freevars:
        return {}
    if len(cells) != len(code.co_freevars):
        raise ValueError('闭包 cell 数与自由变量名数不一致: %d != %d'
                         % (len(cells), len(code.co_freevars)))
    out: dict[str, Any] = {}
    for name, cell in zip(code.co_freevars, cells):
        v = cell.cell_contents
        if isinstance(v, (int, float, str, bytes, bool, type(None))):
            out[name] = v
        else:
            out[name] = copy.deepcopy(v)
    return out


def restore_closure(fn: Callable, saved: Mapping[str, Any]) -> None:
    """把 `closure_state()` 拍下来的状态**原地**写回闭包 cell。

    原地写(而不是换一个新函数)是硬要求: `sim['rng']` 的引用被内核各处直接
    调用, 换对象不会生效。`gauss` 的 `spare` 是 list ⇒ 用 `[:]` 填回, 保持
    同一个 list 对象。

    自由变量名不匹配直接抛 `ValueError` —— 宁可炸掉也不静默给错状态。
    """
    code = getattr(fn, '__code__', None)
    cells = getattr(fn, '__closure__', None)
    if code is None or not cells:
        if saved:
            raise ValueError('目标不是闭包, 但有 %d 个待恢复的自由变量' % len(saved))
        return
    names = tuple(code.co_freevars)
    if set(names) != set(saved):
        raise ValueError('闭包自由变量不匹配: 现场 %r, 快照 %r'
                         % (names, tuple(sorted(saved))))
    if len(cells) != len(names):
        raise ValueError('闭包 cell 数与自由变量名数不一致')
    for name, cell in zip(names, cells):
        v = saved[name]
        cur = cell.cell_contents
        if isinstance(cur, list) and isinstance(v, list):
            cur[:] = copy.deepcopy(v)          # 保持同一个 list 对象
        else:
            cell.cell_contents = copy.deepcopy(v)


# ── 配置与快照对象 ───────────────────────────────────────────────────

@dataclass
class RewindPolicy:
    """快照策略。每一项都能被替换, 所以测试能构造出任意组合的假 sim。"""
    #: 深拷贝(True)或浅拷贝的顶层键 → 是否深拷贝
    copy_items: dict[str, bool] = field(
        default_factory=lambda: dict(INPLACE_COPY))
    #: 只记长度、恢复时截断的键 → 模式(目前只有 'truncate')
    missed_items: dict[str, str] = field(
        default_factory=lambda: dict(MISSED_BY_DESIGN))
    #: 完全跳过的顶层键
    skip_items: frozenset[str] = field(default_factory=lambda: frozenset({
        # 静态/一次性(见 docstring §2-1/2)
        'land', 'cldNoise',
        # knob: 回放不该动配置(§2-3)
        'start', 'month0', 'year0', 'seed', 'envStepH',
        # 闭包: 由 capture_closure 单独处理(§2-7)
        'rng', 'gauss',
    }))
    #: 走闭包捕获的键
    closure_items: tuple[str, ...] = ('rng', 'gauss')
    #: 恢复后强制 +1 的版本号键(缓存失效, §2-6)。**只对快照里存在的键生效**。
    version_items: tuple[str, ...] = ('_envVer', '_cldVer')
    #: **只记录、恢复时不写回**的键(轮次 159)。装的是"用户配置"而不是模拟状态:
    #: 采集时照样存一份浅拷贝(快照自描述), 但拖时间轴不许把用户刚改的配置一起
    #: 回退(见 docstring §2-4)。
    keep_items: tuple[str, ...] = ('params',)
    #: 是否把 `env` 也当"原地写"处理(默认不: 整张 env 是 rebind 的, 见 §1)
    env_copy: bool = False

    def signature(self) -> tuple:
        """策略指纹。缓存里带指纹 ⇒ 换策略(或换 sim 键集)时旧快照自然作废。"""
        return (tuple(sorted(self.copy_items.items())),
                tuple(sorted(self.missed_items.items())),
                tuple(sorted(self.skip_items)),
                tuple(self.closure_items),
                tuple(self.version_items),
                tuple(self.keep_items),
                bool(self.env_copy))


@dataclass
class RewindSnapshot:
    """一份快照。`footprint` 是估算字节, 用于环形缓冲的字节预算。"""
    index: int                      # 单调递增序号(1, 2, 3 ...)
    step: int                       # 采样时的步计数
    t: float                        # 采样时的模拟时间(h)
    signature: tuple                # 策略指纹
    values: dict[str, Any] = field(default_factory=dict)
    missed: dict[str, Any] = field(default_factory=dict)
    closure: dict[str, Any] = field(default_factory=dict)
    footprint: int = 0

    @property
    def state_bytes(self) -> int:
        """本快照持有内容的字节数(不含 dataclass 自身)。

        轮次 159: `footprint` 现在就是"真正新分配的那部分"(见 `_owned_bytes`),
        所以这里直接返回它 —— 旧实现又把 values/missed/closure 加了一遍, 同一份
        内存被记了两次(`state_bytes` 恒等于 footprint 的两倍)。
        """
        return self.footprint


def snapshot_state(state: Mapping) -> dict[str, Any]:
    """把一份 dict 状状态整体深拷贝(给 `estimate_state_bytes` 或测试用)。"""
    return {k: copy.deepcopy(v) for k, v in state.items()}


def estimate_state_bytes(state: Any) -> int:
    """估算任意对象(通常是 `sim` 的一部分)占的字节数。

    numpy 数组按 `shape × itemsize`(`ndarray.nbytes`)算, 容器按头部 + 逐项算。
    **只读, 不改动入参。**
    """
    return _obj_bytes(state)


def estimate_snapshot_bytes(values: Any) -> int:
    """`estimate_state_bytes` 的别名(语义更贴调用点: 估一份快照存的东西)。"""
    return _obj_bytes(values)


def _shallow_container_bytes(v: Any) -> int:
    """浅拷贝一个容器时**真正新增**的字节(只有容器表本身)。

    引用快照路径上, 容器里的数组/对象被快照与活状态**共享**, 不该记进任何一份的
    账; ndarray / 标量 / 字符串这些走"纯引用"的值返回 0。
    """
    if isinstance(v, Mapping):
        return _CONTAINER_OVERHEAD + _ENTRY_OVERHEAD * len(v)
    if isinstance(v, (list, tuple, set, frozenset)):
        return _CONTAINER_OVERHEAD + 8 * len(v)
    return 0


def _borrowed_bytes(v: Any, rec: Any) -> int:
    """`v`(tcs 列表)里**借来的活 list**(track/hist)的估算字节, 用于扣除。

    这两个 list 是 `MISSED_BY_DESIGN`(只记长度, 见 docstring §2-1): 快照持有的是
    活 list 的**引用**, 一个字节都没有复制, 所以不能算进 footprint。
    """
    if not isinstance(v, list) or not isinstance(rec, list):
        return 0
    drop = 0
    for tc, item in zip(v, rec):
        if not isinstance(tc, Mapping) or not isinstance(item, Mapping):
            continue
        for mk in item:
            drop += _obj_bytes(tc.get(mk))
    return drop


def _owned_bytes(pol: RewindPolicy, values: Mapping, missed: Mapping,
                 closure: Mapping) -> int:
    """一份快照**真正持有**(因此该记进内存预算)的字节数。

    与"整状态深拷贝"的估算(`estimate_state_bytes`)是两回事, 三条规则:
      · `missed_items`: 记录/闭包都是真拷贝 ⇒ 全算(在 `values` 里的那份借来的
        活 list 再单独扣除, 见 `_borrowed_bytes`);
      · `copy_items[k] is True`: 深拷贝 ⇒ 全算;`is False` ⇒ 浅拷贝, 只算容器表
        (ndarray 例外 —— `copy.copy(arr)` 会真复制缓冲, 照 `nbytes` 全算);
      · 其余键是**引用快照** ⇒ 只算我们新建的容器(如 `env` 的 dict 副本),
        共享的大场一分不算。

    轮次 159 修正的理由见 `RewindStore._build` 与模块 docstring 第三节。
    """
    total = _obj_bytes(missed) + _obj_bytes(closure)
    for k, v in values.items():
        if k in pol.missed_items:
            continue                        # 已算在 `_obj_bytes(missed)` 里
        if k in pol.copy_items:
            if pol.copy_items[k]:
                total += _obj_bytes(v) - _borrowed_bytes(v, missed.get(k))
            elif isinstance(v, (Mapping, list, tuple, set, frozenset)):
                total += _shallow_container_bytes(v)
            else:
                total += _obj_bytes(v)
        else:
            total += _shallow_container_bytes(v)
    return max(0, total)


# ── 环形缓冲 / 快照管理器 ────────────────────────────────────────────

class RewindStore:
    """时间轴回放的快照管理器(环形缓冲 + 拖回第 k 份)。

    用法(内核侧):

        rw = RewindStore(sim)              # 也可以 RewindStore(lambda: sim)
        rw.advance()                       # 每推进一步调一次
        ...
        k = rw.index_at_t(拖到的时刻)      # 拖到哪一份
        rw.restore(k)                      # 恢复 + 丢弃 k 之后的快照

    也可以把"取状态 / 置状态"换成回调(不传 sim):

        rw = RewindStore(get_state=lambda: sim, set_state=lambda s: sim.update(s))

    内存: `max_snapshots` 与 `max_bytes` **同时**生效, 谁先到就按谁丢最老的。
    """

    def __init__(self, state: Any = None, *, interval: int = DEFAULT_INTERVAL,
                 max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
                 max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
                 min_keep: int = 2,
                 policy: Optional[RewindPolicy] = None,
                 get_state: Optional[Callable[[], Any]] = None,
                 set_state: Optional[Callable[[Any], None]] = None,
                 on_restore: Optional[Callable[[RewindSnapshot], None]] = None):
        if interval < 1:
            raise ValueError('interval 必须 >= 1(步)')
        if max_snapshots < 1:
            raise ValueError('max_snapshots 必须 >= 1')
        if state is None and get_state is None:
            raise ValueError('必须给 state(dict 状对象)或 get_state 回调')
        self.interval = int(interval)
        self.max_snapshots = int(max_snapshots)
        self.max_bytes = None if max_bytes is None else int(max_bytes)
        self.min_keep = max(1, int(min_keep))
        self.policy = policy if policy is not None else RewindPolicy()
        self._get = get_state if get_state is not None else (lambda: state)
        self._set = set_state
        self.on_restore = on_restore
        self._ring: deque[RewindSnapshot] = deque()
        self._steps = 0
        self._next_index = 1
        self._cursor_index = 0            # 当前"活"的快照序号(0 = 还没有)
        self._dropped = 0                 # 统计: 被环形缓冲挤掉的份数
        self._footprint = 0               # 当前持有内容的估算字节
        self._skipped_big = 0             # 统计: 因单份超预算而**放弃**采集的次数

    # ── 基本属性 ──
    @property
    def steps(self) -> int:
        """已 `advance()` 过的步数。"""
        return self._steps

    @property
    def count(self) -> int:
        """当前持有的快照份数。"""
        return len(self._ring)

    @property
    def dropped(self) -> int:
        """被环形缓冲挤掉(或被回溯作废)的份数。"""
        return self._dropped

    @property
    def footprint_bytes(self) -> int:
        """当前所有快照占的估算字节(用于 UI 显示 / 反推粒度)。"""
        return int(self._footprint)

    @property
    def indices(self) -> list[int]:
        """当前有效快照的序号(升序)。"""
        return [s.index for s in self._ring]

    @property
    def cursor(self) -> int:
        """最近一次恢复到的序号(0 = 从未恢复)。"""
        return self._cursor_index

    def snapshots(self) -> list[RewindSnapshot]:
        """按时间升序返回快照对象(只读用途, 不要改它们)。"""
        return list(self._ring)

    def __len__(self) -> int:
        return len(self._ring)

    def __bool__(self) -> bool:
        return bool(self._ring)

    # ── 采集 ──
    def _key_signature(self) -> tuple:
        """策略指纹 + 当前 sim 的顶层键集。键集变了 ⇒ 旧快照自然作废。"""
        try:
            keys = tuple(sorted(str(k) for k in self._get().keys()))
        except Exception:
            keys = ()
        return (self.policy.signature(), keys)

    def capture(self, step: Optional[int] = None, t: Optional[float] = None,
                *, force: bool = True) -> Optional[RewindSnapshot]:
        """立刻存一份快照(不受 `interval` 限制)。返回快照, 被预算拒收则 None。"""
        snap = self._build(step=self._steps if step is None else int(step),
                           t=t)
        if snap is None:
            return None
        if not force and self._ring and snap.t <= self._ring[-1].t:
            return None
        self._push(snap)
        return snap

    def advance(self) -> Optional[RewindSnapshot]:
        """推进一步。每 `interval` 步自动存一份。返回新快照或 None。"""
        self._steps += 1
        if self._steps % self.interval:
            return None
        # 轮次 159 修正 step 的 off-by-one: `_steps` 已经是"已完成的步数"(即 1 基
        # 步号), 旧写法传 `self._steps - 1` 会让快照的 `step` 比现场少 1, 也与
        # `capture()` 的默认值(`self._steps`)不一致。
        return self.capture(step=self._steps)

    def _build(self, step: int, t: Optional[float]) -> Optional[RewindSnapshot]:
        state = self._get()
        if t is None:
            t = float(state.get('t', 0.0))
        pol = self.policy
        values: dict[str, Any] = {}
        missed: dict[str, Any] = {}
        for k in list(state.keys()):
            if k in pol.skip_items:
                continue
            v = state[k]
            if k in pol.missed_items:
                missed[k] = self._capture_missed(v)
                continue
            if k in pol.copy_items:
                if pol.copy_items[k]:
                    # "原地写"的深拷贝。**例外**: 若这是一个"元素是 dict、且
                    # 元素里带嵌套 missed 键"的 list(内核里只有 `sim['tcs']`),
                    # 就走 `_copy_tcs` —— 深拷贝它会把每个 tc 的 track(上限 2400
                    # 点)/hist(上限 720 点)整段复制成 Python dict, 3 个满载台风
                    # 实测 2.96 MB/份, 纯浪费(见 docstring §2-1)。
                    values[k] = self._copy_tcs(v, missed, k, pol)
                else:
                    values[k] = self._copy_inplace(v)
                continue
            if k == 'env' and pol.env_copy:
                values[k] = copy.deepcopy(v)
                continue
            if isinstance(v, MutableMapping):
                # **必须存容器副本**: 恢复时对 `env` 是"clear + update 原地
                # 复用同一个 dict"(内核持有它, 见 docstring §1)。若快照里装的
                # 就是活的那个 dict, clear() 会把快照**自己**清空 —— 实测表现
                # 是恢复后 `sim['env']` 变成空 dict, 下一步 KeyError('sst')。
                # 浅拷贝只复制 dict 结构(几十个引用), 里面的数组仍是引用快照。
                values[k] = dict(v)
                continue
            values[k] = v                     # 引用快照(见 docstring §1)
        cl: dict[str, Any] = {}
        for k in pol.closure_items:
            fn = state.get(k)
            if fn is not None:
                cl[k] = closure_state(fn)
        snap = RewindSnapshot(index=self._next_index, step=int(step), t=float(t),
                              signature=self._key_signature(), values=values,
                              missed=missed, closure=cl)
        # 轮次 159: footprint 只算**真正新分配**的那部分(引用共享的大场与借来的
        # track/hist 都不算), 否则字节预算会把引用快照当成整状态深拷贝。
        snap.footprint = _owned_bytes(pol, snap.values, snap.missed, snap.closure)
        self._next_index += 1
        if (self.max_bytes is not None and snap.footprint > self.max_bytes
                and len(self._ring) >= self.min_keep):
            # 单份就超总预算: 拒收(比"清空已有快照"安全 —— 用户至少还能拖回去)。
            # 轮次 159 修正: 旧条件是 `and self._ring`(只要手上已有一份就拒收),
            # 于是"单份超预算"的极端情形会**永久停在 1 份** —— 1 份没有历史可拖,
            # 比"略微超预算但留下 min_keep 份"更没用。现在与 `_push()` 的
            # "硬保 min_keep 份"口径一致(tests :437 卡住的就是这条)。
            self._skipped_big += 1
            self._next_index -= 1
            return None
        return snap

    @staticmethod
    def _copy_inplace(v: Any) -> Any:
        """浅拷贝容器(里面装的是 rebind 的标量/数组)。"""
        if isinstance(v, MutableMapping):
            return dict(v)
        if isinstance(v, list):
            return list(v)
        if isinstance(v, set):
            return set(v)
        return copy.copy(v)

    def _copy_tcs(self, v: Any, missed: dict, key: str,
                  pol: RewindPolicy) -> Any:
        """`sim['tcs']` 的结构浅拷贝 + 历史长度记账。

        每个 tc 只浅拷贝它自己的 dict(标量键是 rebind 的, 引用快照即精确),
        但其中的 `track` / `hist` **换回活 list 的引用**、只记长度:

          · 元素是不可变的历史记录(全仓只 append, 且上限截断是 **rebind**
            `tc['track'] = tc['track'][-2400:]`, core.py:4862/4864 ⇒ 旧引用
            不会被截断污染);
          · 恢复时按长度 `del lst[N:]` 就地截断活 list;
          · 代价: 快照里那份 list 会被后续重放改写(重放产生的未来快照本来就被
            作废), 所以**不要**用快照去查询"当时的历史内容", 只用于恢复。

        若 `v` 不是"元素为 dict 的 list", 回退成普通深拷贝。
        """
        if not isinstance(v, list):
            return copy.deepcopy(v)
        out: list = []
        recs: list = []
        touched = False
        for tc in v:
            if not isinstance(tc, MutableMapping):
                out.append(copy.deepcopy(tc))
                recs.append({})
                continue
            sub = dict(tc)                       # 浅拷贝: 标量/引用
            rec: dict = {}
            for mk in pol.missed_items:
                if mk not in tc:
                    continue
                sub[mk] = tc[mk]                 # 活 list 的引用
                rec[mk] = self._capture_missed(tc[mk])
                touched = True
            out.append(sub)
            recs.append(rec)
        if touched:
            missed[key] = recs
        return out

    def _capture_missed(self, v: Any) -> Any:
        """只记长度(list)或整体(其他可浅拷贝对象)。"""
        if isinstance(v, list):
            return {'n': len(v)}
        if isinstance(v, Mapping):
            return {'n': len(v), 'ref': dict(v)}
        return {'n': -1, 'ref': copy.deepcopy(v)}

    def _push(self, snap: RewindSnapshot) -> None:
        self._ring.append(snap)              # deque 无 maxlen: 自己控制丢弃点
        self._footprint += snap.footprint
        self._cursor_index = snap.index
        # ① 份数上限
        while len(self._ring) > self.max_snapshots:
            self._evict_left()
        # ② 字节上限。**硬保 min_keep 份**(默认 2):
        #    回溯功能至少需要"两份"才有意义, 所以字节预算超一点也要留住它们。
        #    单份本身就超总预算的情形在 `_build` 里就拒收了(不会把已有快照清空)。
        while (self.max_bytes is not None and self._footprint > self.max_bytes
               and len(self._ring) > self.min_keep):
            self._evict_left()

    def _evict_left(self) -> None:
        old = self._ring.popleft()
        self._footprint -= old.footprint
        self._dropped += 1

    def _drop_after(self, index: int) -> int:
        """丢掉序号 > index 的全部快照(回溯后重放会产生不同历史)。"""
        n = 0
        while self._ring and self._ring[-1].index > index:
            old = self._ring.pop()
            self._footprint -= old.footprint
            self._dropped += 1
            n += 1
        return n

    # ── 查询 ──
    def _find(self, index: int) -> RewindSnapshot:
        for s in self._ring:
            if s.index == index:
                return s
        raise KeyError('没有第 %d 份快照(当前有效: %r)'
                       % (index, [s.index for s in self._ring]))

    def can_rewind(self) -> bool:
        """是否至少有两份快照(有历史可拖)。"""
        return len(self._ring) >= 2

    def t_at(self, index: int) -> float:
        """第 `index` 份快照对应的模拟时间(h)。"""
        return self._find(index).t

    def index_at_t(self, t: float) -> int:
        """把"拖到的时刻"映射到**不晚于**它的最近一份快照序号。

        比最早的快照还早 → 返回最早那份(拖到头就停在头一份, 而不是报错)。
        """
        if not self._ring:
            raise KeyError('还没有任何快照')
        ts = [s.t for s in self._ring]
        pos = bisect_right(ts, float(t)) - 1
        if pos < 0:
            pos = 0
        return self._ring[pos].index

    def rewind_hours(self) -> float:
        """当前能回溯的时间跨度(h)。"""
        if len(self._ring) < 2:
            return 0.0
        return float(self._ring[-1].t - self._ring[0].t)

    def plan(self, budget_bytes: Optional[int] = None) -> dict:
        """按内存预算反推"能存多少份 / 能回溯多久"。

        返回 `{budget, per_snapshot, snapshots, hours, interval_h}`:
          · `budget` = 本次用的预算(`budget_bytes` 优先, 否则用 `self.max_bytes`);
          · `per_snapshot` = 一份快照的实测估算字节(还没有快照时回退到
            对当前状态整体 `estimate_state_bytes()` 的 1/8 —— 引用快照的增量
            远小于整状态, 直接用整状态会**高估 8 倍**从而把 N 压得过小);
          · `snapshots` = budget 允许的份数(至少 2);
          · `hours` = 份数 × 每份间隔的小时数; `interval_h` 由已存快照的
            Δt/Δstep 实测(还没有两份快照时按 0)。
        """
        budget = self.max_bytes if budget_bytes is None else int(budget_bytes)
        per = 0
        if self._ring:
            per = max(1, int(self._footprint // max(1, len(self._ring))))
        if per <= 0:
            per = max(1, estimate_state_bytes(self._get()) // 8)
        if budget is None or budget <= 0:
            n = self.max_snapshots
        else:
            n = max(2, int(budget // per))
        iv_h = self.interval_hours()
        return {'budget': budget, 'per_snapshot': int(per), 'snapshots': int(n),
                'hours': float(n * (iv_h if iv_h > 0 else self.interval)),
                'interval_h': float(iv_h)}

    def interval_hours(self) -> float:
        """每个采样间隔推进的小时数(由已存快照的 Δt/Δstep 实测)。"""
        if len(self._ring) < 2:
            return 0.0
        a, b = self._ring[0], self._ring[-1]
        ds = max(1, b.step - a.step)
        return float((b.t - a.t) / ds * self.interval)

    # ── 恢复 ──
    def restore(self, index: int) -> RewindSnapshot:
        """**拖回到第 `index` 份快照**: 恢复状态 + 丢弃它之后的全部快照。

        恢复顺序(顺序有讲究):
          ① 普通键(引用/拷贝)  ② `missed` 截断  ③ 闭包(rng/gauss)
          ④ 版本号 +1(缓存失效)  ⑤ 回调 `on_restore`

        ⚠ 代价(轮次 159): `copy_items` 里的键会把快照那份**再复制一份**交给活
        状态(见 `_copy_out`)。必须这样 —— 否则活状态与快照共用同一块内存, 重放时
        的原地改写会把快照改脏。一次拖动复制一份, 与"每一步都深拷贝"不可同日而语。
        """
        snap = self._find(index)
        if self._set is None:
            if not isinstance(self._get(), MutableMapping):
                raise TypeError('没有 set_state 回调, 对象又不是 MutableMapping')
            target = self._get()
        else:
            target = self._get()
        if self._key_signature()[0] != snap.signature[0]:
            raise ValueError('策略已改变, 第 %d 份快照不可用(键集/策略指纹不同)'
                             % index)
        self._apply_to(target, snap)
        self._drop_after(index)
        self._cursor_index = index
        if self.on_restore is not None:
            self.on_restore(snap)
        return snap

    def _apply_to(self, target: Any, snap: RewindSnapshot) -> None:
        pol = self.policy
        # ① 普通键: 先删掉"快照之后才出现"的键, 再写回快照值。
        #    必须删: 例如 `lastOceanH` / `_envBase` / `cySeq` 在 init() 里根本
        #    不存在(只有 core.py:4818/4798/2245 会创建), 留着它们会让
        #    `sim.get('lastOceanH', -1e9)` 退化 ⇒ 物理静默改变。
        #    `keep_items`(用户配置)既不删也不写 —— 回放不碰配置(轮次 159)。
        for k in list(target.keys()):
            if (k in pol.skip_items or k in pol.missed_items
                    or k in pol.keep_items):
                continue
            if k not in snap.values:
                try:
                    del target[k]
                except (KeyError, TypeError):
                    pass
        for k, v in snap.values.items():
            if k in pol.keep_items:
                continue                 # 只记录不回写, 活值(用户现场配置)保留
            if k == 'env' and isinstance(v, Mapping):
                # 引用快照(默认)时 v 的数组是**共享引用**, 直接塞给活 dict 即可;
                # `env_copy=True` 时 v 是我们自己深拷贝的 ⇒ 再复制一份再交出去
                # (否则内核原地写 env 数组会把快照改脏, 同下面 `copy_items` 的道理)。
                fresh = copy.deepcopy(dict(v)) if pol.env_copy else v
                cur = target.get('env')
                if isinstance(cur, MutableMapping):
                    cur.clear()          # 保持同一个 env dict 对象(内核持有它,
                    cur.update(fresh)    #  见 docstring §1); fresh 是副本, 不会自清
                else:
                    target[k] = dict(fresh)
                continue
            if k in pol.copy_items:
                # 轮次 159: 快照里装的是**我们自己复制的那份**, 直接把它挂成活状态
                # 等于让"活状态"与"快照"共用同一块内存 —— 内核重放时的原地改写
                # (`ocn['Tml'] +=` core.py:4320 / `ohcWake *=` core.py:4303 /
                # `eddies[i]['age'] +=` core.py:3400) 会把快照自己改脏, 第二次拖回
                # 同一份就拿到错的物理状态。实测(修前): restore → 重放 3 步 →
                # 再 restore 同一份, `ocn['Tml']` 从 28.02 变成 28.05。
                target[k] = self._copy_out(k, v, pol)
                continue
            target[k] = v
        # ② 只记长度的容器: 就地截断(保证"逐位一致"的尾部长度)
        self._apply_missed(target, snap)
        # ③ 闭包(rng / gauss): 原地写回 cell, 否则随机流停在"未来"
        for k, st in snap.closure.items():
            fn = target.get(k)
            if fn is not None:
                restore_closure(fn, st)
        # ④ 版本号: 恢复后再 +1, 让所有"按版本号做键"的缓存全部落空
        #    (_OCEAN_UV 的键是 ('usv', _envVer, shape), core.py:4273)。
        #    轮次 159: 只处理**快照里真的有**的版本键 —— 恢复点还没产生过这个键时
        #    不许凭空造一个(否则键集偏离快照点; 内核 core.py:4461 也是
        #    `if keep_ver is not None` 才还原)。
        for k in pol.version_items:
            if k not in snap.values:
                continue
            try:
                target[k] = int(snap.values[k] or 0) + 1
            except Exception:
                pass

    def _copy_out(self, k: str, v: Any, pol: RewindPolicy) -> Any:
        """把快照里那份拷贝**再复制一份**交给活状态(见 `_apply_to` ①)。

        `tcs` 例外: 每个 tc 只再发一层**浅拷贝**(标量键是 rebind 的, 浅拷贝即
        精确), `track`/`hist` 仍然是**同一个活 list 对象** —— §2-1 的
        "恢复时 `del lst[n:]` 就地截断 + 重放继续 append" 就靠这个身份不变。
        """
        if k == 'tcs' and isinstance(v, list):
            return [dict(tc) if isinstance(tc, MutableMapping) else copy.copy(tc)
                    for tc in v]
        if pol.copy_items.get(k):
            return copy.deepcopy(v)
        return self._copy_inplace(v)

    def _apply_missed(self, target: Any, snap: RewindSnapshot) -> None:
        """`missed` 截断。顶层(如 `track`)+ 一层嵌套(如每个 tc 的 track/hist)。"""
        for k, rec in snap.missed.items():
            cur = target.get(k)
            if k == 'tcs' and isinstance(rec, list):
                # 嵌套: rec[i] = {'track': {'n': N}, 'hist': {'n': N}}
                if isinstance(cur, (list, tuple)):
                    for i, item in enumerate(rec):
                        if not isinstance(item, Mapping) or i >= len(cur):
                            continue
                        tc = cur[i]
                        if not isinstance(tc, MutableMapping):
                            continue
                        for mk, r2 in item.items():
                            lst = tc.get(mk)
                            if (isinstance(lst, list) and isinstance(r2, Mapping)
                                    and int(r2.get('n', -1)) >= 0):
                                del lst[int(r2['n']):]
                continue
            if isinstance(cur, list) and isinstance(rec, Mapping) \
                    and int(rec.get('n', -1)) >= 0:
                del cur[int(rec['n']):]
            elif isinstance(rec, Mapping) and 'ref' in rec and rec['n'] < 0:
                target[k] = copy.deepcopy(rec['ref'])

    def set_state_callback(self, fn: Callable[[Any], None]) -> None:
        """接一个 `置状态` 回调(对象不是 MutableMapping 时用)。"""
        self._set = fn

    def clear(self) -> None:
        """丢掉全部快照(不动 sim)。"""
        self._dropped += len(self._ring)
        self._ring.clear()
        self._footprint = 0
        self._cursor_index = 0


# 延迟绑定(避免 dataclass 定义顺序问题)
POLICY = RewindPolicy()
