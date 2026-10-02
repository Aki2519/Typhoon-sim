# tools/perf_ab_r158.py
"""**同进程交错 A/B** 测单项优化的真实收益(轮次 158 建, 轮次 159 扩展)。

为什么必须这样测:
    `tools/perf_baseline_r158.py` 是跨时间对比, 而本机在长时间重载后会**明显降频** ——
    实测同一份代码连测三次得到 1008 / 1114 / 1409 ms/步(漂移高达 40%)。
    在这种漂移下, "改前测一次、改后测一次"的结论**不可信**。
    本脚本把 A/B 交错在**同一个进程**里跑(A B A B), 用各自的最小值/中位数比较,
    漂移对两边的影响大致相同。

轮次 159 的三条纪律(前两条来自轮次 158 的翻车教训):
    1. **先跑 A/A 自检**(`aa` 模式): 两边配置完全相同, 极差就是纯噪声。
       极差 >= 15% 时, 之后任何 A/B 的加速比都**不许报确定值**。
    2. **报 min**, 不要只报中位数/均值 —— min 受降频影响最小。
       若 min 与中位数的结论方向相反, 必须把这个矛盾如实写出来。
    3. **旁路必须是"真的重算"**: 轮次 158 那版 `_const_grids` 旁路是把缓存数组
       复制一份 —— 复制比 `np.exp`/`np.mod` 便宜得多, "无缓存"那一臂其实**还在用缓存**,
       必然测出 ~1.0x。本版改成**清空缓存 dict 再调原函数**(= core `_perf_drop_caches`
       的官方口径), 走的是同一份源码 ⇒ 数值逐位不变, 只是真的每次重算。

用法:
    python tools/perf_ab_r158.py [每轮步数] [轮数] [aa|ab|ab3]

    aa  : A/A 自检(两边配置完全相同)
    ab  : 有全部缓存  vs  三个缓存全部旁路(合并收益)
    ab3 : 逐缓存交错(轮次 159 新增): 每轮按**轮转顺序**依次跑
          on(全开) / cg(只旁路 #1) / mb(只旁路 #2) / rg(只旁路 #3) / all(全旁路)
          ⇒ 一轮就拿到 #1/#2/#3 各自与合并的收益, 且 5 个配置在时间上贴得更近,
          漂移的抵消比"一个配置跑一整轮"更好。

三个缓存(全部在 `simulator/simcore/core.py`):
    #1 `_const_grids()`    `_CONST_GRID`   只依赖固定网格的常量(轮次 158 起, 159 扩充)
    #2 `_month_bands(...)` `_BAND_CACHE`   只随**整数月**变的纬度带 4+2(+2) 个 `np.exp`
    #3 `_ridge_grids()`    `_RIDGE_GRID_CACHE` 副高 7 个中心的 `(LAT-rlat)` 与经度取模
"""
import hashlib
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

#: 旁路名 → (缓存 dict 名, 它对应的优化编号)
CACHE_LABEL = (('cg', '#1 _const_grids'), ('mb', '#2 _month_bands'),
               ('rg', '#3 _ridge_grids'))


def _same(a, b, name: str) -> None:
    """断言旁路重算出来的东西与走缓存拿到的**逐位**相同。

    若重算值与缓存值不逐位相同, 那 A/B 两侧跑的就不是同一件事, 测出来的
    "加速比"毫无意义。NaN 视为相等(只比"位模式一致")。
    """
    if hasattr(b, 'shape'):
        if not np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True):
            raise AssertionError('%s: 旁路重算值与缓存值不逐位相等' % name)
        return
    if isinstance(b, dict):
        if set(a) != set(b):
            raise AssertionError('%s: 键不一致 %s vs %s' % (name, sorted(a), sorted(b)))
        for k in sorted(b):
            _same(a[k], b[k], '%s[%s]' % (name, k))
        return
    if isinstance(b, (list, tuple)):
        if len(a) != len(b):
            raise AssertionError('%s: 长度不一致 %d vs %d' % (name, len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, '%s[%d]' % (name, i))
        return
    if a != b:
        raise AssertionError('%s: 标量不等 %r vs %r' % (name, a, b))


def _install(C, which):
    """装上 `which`(子集 of {'cg','mb','rg'})的旁路, 返回 restore 回调列表。

    旁路机制 = **清空缓存 dict + 调原函数**(与 core `_perf_drop_caches()` 同口径):
    不再复用缓存数组, 所以每个消费点都真的重算一次; 但走的是**同一份源码**,
    数值逐位不变 —— 首次调用时用 `_same` 断言一次(重算值 == 走缓存拿到的值)。
    """
    undo = []
    o_cg, o_mb = C._const_grids, C._month_bands
    o_rg = getattr(C, '_ridge_grids', None)
    d_cg = getattr(C, '_CONST_GRID', None)
    d_mb = getattr(C, '_BAND_CACHE', None)
    d_rg = getattr(C, '_RIDGE_GRID_CACHE', None)
    checked = {}

    if 'cg' in which and d_cg is not None:
        def cg():
            if not checked.get('cg'):
                cached = o_cg()                 # 先按"有缓存"的方式取一次
                d_cg.clear()                    # 清空 ⇒ 下一次必须重算
                fresh = o_cg()
                _same(fresh, cached, '#1 _const_grids')
                checked['cg'] = True
                return fresh
            d_cg.clear()
            return o_cg()
        C._const_grids = cg
        undo.append(lambda: setattr(C, '_const_grids', o_cg))

    if 'mb' in which and d_mb is not None:
        def mb(LAT, LON, mt_n, mt_s, month):
            if not checked.get('mb'):
                cached = o_mb(LAT, LON, mt_n, mt_s, month)
                d_mb.clear()
                fresh = o_mb(LAT, LON, mt_n, mt_s, month)
                _same(fresh, cached, '#2 _month_bands')
                checked['mb'] = True
                return fresh
            d_mb.clear()
            return o_mb(LAT, LON, mt_n, mt_s, month)
        C._month_bands = mb
        undo.append(lambda: setattr(C, '_month_bands', o_mb))

    if 'rg' in which and o_rg is not None and d_rg is not None:
        def rg():
            if not checked.get('rg'):
                cached = o_rg()
                d_rg.clear()
                fresh = o_rg()
                _same(fresh, cached, '#3 _ridge_grids')
                checked['rg'] = True
                return fresh
            d_rg.clear()
            return o_rg()
        C._ridge_grids = rg
        undo.append(lambda: setattr(C, '_ridge_grids', o_rg))

    return undo


def _state_sig(sim) -> str:
    """跑完后对模拟状态取一个便宜的**位级**指纹。

    用途: 证明"缓存开"与"缓存关"两条路径不仅算得一样多, 而且**结果逐位相同**
    —— 否则 A/B 比的就不是同一件事的时间。
    """
    h = hashlib.blake2b(digest_size=8)
    env = sim.get('env') or {}
    for k in ('sst', 'shear', 'rh', 'pEnv'):
        v = env.get(k)
        if hasattr(v, 'tobytes'):
            h.update(k.encode())
            h.update(np.ascontiguousarray(v).tobytes())
    h.update(repr(float(sim.get('t', -1.0))).encode())
    return h.hexdigest()


def _run(steps: int, bypass=()):
    """跑 steps 步, 返回 (每步毫秒, 状态指纹)。

    `bypass` 是要**强制每次重算**的缓存名集合(空 = 全走缓存)。
    """
    from simulator.simcore import core as C
    from simulator.simcore import core as V

    undo = _install(C, set(bypass))
    try:
        sim = V.init({'seed': 20260801})
        dt = float(V.step_hours(sim))
        for _ in range(3):
            V.sim_step(sim, dt, max_inner=1)
        t0 = time.perf_counter()
        for _ in range(steps):
            V.sim_step(sim, dt, max_inner=1)
        t1 = time.perf_counter()
        return (t1 - t0) * 1000.0 / steps, _state_sig(sim)
    finally:
        for f in reversed(undo):
            f()


def _spread(vals) -> float:
    lo, hi = min(vals), max(vals)
    return (hi / lo - 1.0) * 100.0 if lo > 0 else 999.0


def _stat_line(vals, what: str) -> float:
    """打印一组样本的 n/min/中位数/max/极差, 返回极差百分比。"""
    print('%-22s n=%d  min %.1f  中位数 %.1f  max %.1f  极差 %.1f%%'
          % (what, len(vals), min(vals), statistics.median(vals), max(vals),
             _spread(vals)))
    print('%-22s 样本 %s' % ('', ['%.1f' % v for v in vals]))
    return _spread(vals)


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    mode = (sys.argv[3] if len(sys.argv) > 3 else 'ab').lower()
    print('== perf_ab_r158 | %d 步/轮 × %d 轮 | 模式 %s ==' % (steps, rounds, mode))

    # ── A/A 自检 ──
    # 两边配置**完全相同**时也应当测出 ~0 的差。若 A/A 就已经散了(比如 >5%),
    # 那么随后的 A/B 无论出什么数字都**不可信** —— 必须先把机器弄稳定。
    if mode == 'aa':
        a_list, b_list, sig = [], [], set()
        for i in range(rounds):
            a, sa = _run(steps)
            b, sb = _run(steps)
            a_list.append(a)
            b_list.append(b)
            sig.add(sa)
            sig.add(sb)
            print('轮 %d(A/A): %.1f  vs  %.1f ms/步' % (i + 1, a, b))
        allv = a_list + b_list
        print()
        spread = _stat_line(allv, 'A/A 全部样本')
        print('A/A 状态指纹 %d 种 %s(1 种 = 每次跑的轨迹逐位相同)'
              % (len(sig), sorted(sig)))
        if spread < 5.0:
            print('结论: 机器**稳定**(极差 < 5%), 可以做 A/B')
        elif spread < 15.0:
            print('结论: 边缘(5-15%), A/B 需多轮取 min, 结论要标注不确定度')
        else:
            print('结论: **不稳定**(极差 >= 15%) —— 现在做的 A/B 不可信, 先降温/停负载')
        return

    # ── 逐缓存交错 ab3 ──
    if mode == 'ab3':
        names = ['on'] + [k for k, _ in CACHE_LABEL] + ['all']
        short = {'on': '全开(基准)', 'all': '全旁路(合并)'}
        short.update(dict(CACHE_LABEL))
        rows = {n: [] for n in names}
        sigs = {n: set() for n in names}
        for i in range(rounds):
            order = names[i % len(names):] + names[:i % len(names)]   # 轮转, 抵消顺序偏差
            line = []
            for n in order:
                byp = set() if n == 'on' else (
                    set(k for k, _ in CACHE_LABEL) if n == 'all' else {n})
                ms, sg = _run(steps, byp)
                rows[n].append(ms)
                sigs[n].add(sg)
                line.append('%s %.1f' % (n, ms))
            print('轮 %d(顺序 %s): %s' % (i + 1, '/'.join(order), '  '.join(line)))
        print()
        for n in names:
            _stat_line(rows[n], '%-5s %s' % (n, short[n]))
        print()
        on_min, on_med = min(rows['on']), statistics.median(rows['on'])
        print('基准(全开): min %.1f  中位数 %.1f' % (on_min, on_med))
        print('%-5s %-18s %8s %8s %9s %9s' % ('旁路', '对应优化', 'min', '中位数',
                                              'min比', '中位比'))
        for n in [k for k, _ in CACHE_LABEL] + ['all']:
            om, od = min(rows[n]), statistics.median(rows[n])
            rm, rd = om / on_min, od / on_med
            print('%-5s %-18s %8.1f %8.1f %8.3fx %8.3fx'
                  % (n, dict(CACHE_LABEL).get(n, '合并三种'), om, od, rm, rd))
            if (rm - 1.0) * (rd - 1.0) < 0:
                print('      !! **矛盾**: min 说%s, 中位数说%s —— 方向相反, 必须如实报出'
                      % ('更慢' if rm > 1 else '更快', '更慢' if rd > 1 else '更快'))
        print()
        for n in names:
            print('状态指纹 %-5s: %d 种 %s' % (n, len(sigs[n]), sorted(sigs[n])))
        return

    # ── A/B(合并: 三个缓存全开 vs 全旁路)──
    allc = set(k for k, _ in CACHE_LABEL)
    on, off = [], []
    sig_on, sig_off = set(), set()
    for i in range(rounds):
        a, sa = _run(steps, ())
        b, sb = _run(steps, allc)
        on.append(a)
        off.append(b)
        sig_on.add(sa)
        sig_off.add(sb)
        print('轮 %d: 有缓存 %.1f ms/步   无缓存 %.1f ms/步' % (i + 1, a, b))
    print()
    mo, mf = statistics.median(on), statistics.median(off)
    print('中位数: 有缓存 %.1f   无缓存 %.1f' % (mo, mf))
    print('加速比 = %.3fx' % (mf / mo if mo > 0 else 0.0))
    print('min   : 有缓存 %.1f   无缓存 %.1f  (min 受降频影响最小)'
          % (min(on), min(off)))
    print('加速比(min) = %.3fx' % (min(off) / min(on) if min(on) > 0 else 0.0))
    if (mf / mo - 1.0) * (min(off) / min(on) - 1.0) < 0:
        print('!! **矛盾**: min 与中位数的结论方向相反 —— 必须如实报出, 不许挑好看的那个')
    print()
    print('状态指纹 有缓存 %d 种 %s' % (len(sig_on), sorted(sig_on)))
    print('状态指纹 无缓存 %d 种 %s' % (len(sig_off), sorted(sig_off)))
    print('两边指纹: %s' % ('**完全相同**(逐位同轨迹)'
                            if sig_on == sig_off else
                            '**不同** —— 旁路改变了结果, A/B 不可比, 停止!'))


if __name__ == '__main__':
    main()
