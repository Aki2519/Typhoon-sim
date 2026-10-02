"""改动后的验收:一年跑完, 数扰动数 / 发展率 / 峰值分布。

用法:
    python tools/verify_year.py --sst-anom 0.0 --seed 20250801 --days 365 --tag neutral
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V            # noqa: E402

# 观测基准(ATCF invest 台账, 2017-2025 平均 + 2025 明细)
OBS = {'total': 200, 'developed': 100, 'wp_total': 64, 'wp_dev': 33}


def basin(lat, lon):
    lo = lon % 360.0
    if lat < 0:
        return 'SH'
    if 100 <= lo < 180:
        return 'WP'
    if 180 <= lo < 260:
        return 'EP'
    if 260 <= lo < 360:
        return 'AL'
    return 'IO'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sst-anom', type=float, default=0.0)
    ap.add_argument('--enso-pin', type=float, default=None,
                    help='钉住 ENSO 相位(摄氏度, 用模拟器自带空间型): +2 超强厄尔尼诺 / 0 中性 / -2 超强拉尼娜')
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--start', default='2025-01-01 00',
                    help='起始时刻。**年度量必须用 1 月 + 7 月两个窗口分别跑再合起来**'
                         '(单窗口是单半球季, 外推有偏; 轮次 123 实测 1 月窗口里 '
                         'EP=AL=IO=0、WP=15/SH=38)')
    ap.add_argument('--tag', default='run')
    ap.add_argument('--param', default='',
                    help='覆盖 sim["params"], 形如 unfavK=6,shPoleMax=5')
    a = ap.parse_args()

    # forecast=False: 关掉集合预报(只读诊断, 不影响任何验收指标;
    # 但每次签发要 13 次 env_update, 会让全年跑慢好几倍 —— 见 core._sim_step_core)
    _pr = {'precision': '100ms', 'dataMode': True, 'sstAnom': a.sst_anom,
           'forecast': False}
    if a.enso_pin is not None:
        _pr['ensoPin'] = a.enso_pin
    for _kv in [x for x in a.param.split(',') if x.strip()]:
        _k, _, _v = _kv.partition('=')
        try:
            _pr[_k.strip()] = float(_v)
        except ValueError:
            _pr[_k.strip()] = _v.strip()
    if a.param:
        print('== 参数覆盖: %s' % a.param, flush=True)
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0, 'params': _pr})

    # ── **必须另外数"年内累计生成"**(轮次 115 修, 第 19 次口径问题) ──
    # `core` 在每步末尾会把**死亡超过 120 天**(2880h)的台风从 `sim['tcs']` 里剔掉
    # (见 `_sim_step_core` 末尾"死亡台风不删除 … 只清理很久以前的")。
    # 于是 **一年跑完时 `len(sim['tcs'])` 不是"这一年生成了几个", 而是"最近 120 天内
    # 生成、且还没被剔掉的个数"** —— 它会在 ~120 天处**饱和**。
    # 实测: 25 天 46 个 / 50 天 92 个(1.8 个/天), 而 365 天只报 196 个;
    # 按 1.63-1.75 个/天 外推, 真实年生成量约 **600 个**, 是"197"的 3 倍。
    # 这直接推翻了任务 E 的现状基线(189.5/年)与"观测 ~200 ⇒ 差不多"的判读。
    # 这里用**给 make_tc 包一层计数器**的办法拿真值(不改内核)。
    _gen = [0]
    _peaks = {}                      # id(tc) -> 一生最大强度(逐存活步更新)
    _orig_make = V.make_tc

    def _counting_make(sim_, *args, **kw):
        _gen[0] += 1
        return _orig_make(sim_, *args, **kw)

    V.make_tc = _counting_make
    try:
        for _ in range(int(a.days * 24 / 6)):
            V.sim_step(sim, 6.0)
            # **必须逐存活步记录峰值**: 死亡超过 120 天的台风会被 `_sim_step_core`
            # 从 `sim['tcs']` 里剔掉, 靠最后的列表统计会丢掉前面那 2/3 的系统。
            for _t in sim['tcs']:
                if _t['dead']:
                    continue
                _k = id(_t)
                _v = float(_t['vmax'])
                if _v > _peaks.get(_k, 0.0):
                    _peaks[_k] = _v
    finally:
        V.make_tc = _orig_make
    sim['gen_total'] = _gen[0]
    _pk = np.array(list(_peaks.values()), float)
    sim['gen_dev_total'] = int((_pk >= 34.0).sum())
    sim['gen_peak_all'] = _pk

    tot = len(sim['tcs'])
    pk_all, dev_b, tot_b = [], collections.Counter(), collections.Counter()
    for t in sim['tcs']:
        b = basin(float(t['lat']), float(t['lon']))
        tot_b[b] += 1
        tr = t.get('track') or []
        p = max((float(q['vmax']) for q in tr), default=0.0)
        pk_all.append(p)
        if p >= 34.0:
            dev_b[b] += 1
    pk = np.array([x for x in pk_all if x >= 34.0], float)
    dev = int((np.array(pk_all) >= 34.0).sum())

    # ── 任务 A 验收: 一生最高纬度分布 + 净向极位移 + 寿命(只算 NH) ──
    # 为什么放在主验收工具里: 单种子噪声大(轮次 41-44 吃过亏), 而 multi_year_check
    # 只解析本文件的固定几行 —— 指标放进来才能自动做多种子均值。
    gen_lat, mx_lat, dlat, life, peak_l = [], [], [], [], []
    for t in sim['tcs']:
        tr = t.get('track') or []
        if len(tr) < 2:
            continue
        la = [float(q['lat']) for q in tr]
        if sum(la) / len(la) < 0:                     # 南半球另有分布, 不混算
            continue
        gen_lat.append(la[0])
        mx = max(la)
        mx_lat.append(mx)
        dlat.append(mx - la[0])
        life.append(6.0 * (len(tr) - 1))
        peak_l.append(max(float(q['vmax']) for q in tr))
    mx_lat = np.array(mx_lat, float)
    peak_l = np.array(peak_l, float)

    print('=== %s: sstAnom=%+.1f ensoPin=%s seed=%s %d 天 ==='
          % (a.tag, a.sst_anom, a.enso_pin, a.seed, a.days))
    print('扰动总数 %d  (观测 ~%d, 比 %.2fx)'
          % (tot, OBS['total'], tot / OBS['total']))
    _gt, _gd = sim['gen_total'], sim['gen_dev_total']
    print('**年内累计: 生成 %d 个(%.2f 个/天) | 其中发展成 34kt+ %d 个(发展率 %.0f%%)**'
          % (_gt, _gt / max(1.0, a.days), _gd, 100.0 * _gd / max(1, _gt)))
    if a.days > 130.0:
        print('   (上面"扰动总数 %d / 发展 %d"是**最近 120 天保留窗**内的数,'
              ' >120 天会饱和; 与观测的**年计数**比较必须用累计那一行)'
              % (tot, dev))
        if sim['gen_peak_all'].size:
            _p = sim['gen_peak_all']
            print('   年内全部系统的峰值: P50 %.0f P90 %.0f P95 %.0f (n=%d)'
                  % (np.percentile(_p, 50), np.percentile(_p, 90),
                     np.percentile(_p, 95), _p.size))
    print('发展成 34kt+ %d  (观测 ~%d, 比 %.2fx) | **发展率 %.0f%%**(观测 ~50%%)'
          % (dev, OBS['developed'], dev / OBS['developed'], 100.0 * dev / tot if tot else 0))
    print('西太: 扰动 %d (观测 64) / 发展 %d (观测 33)'
          % (tot_b['WP'], dev_b['WP']))
    print('各海盆扰动: ' + ' '.join('%s=%d' % (k, tot_b[k]) for k in ('WP', 'EP', 'AL', 'IO', 'SH')))
    if pk.size:
        print('峰值分布(>=34kt 的 %d 个): P50 %.0f  P75 %.0f  P90 %.0f  P95 %.0f  P99 %.0f  最大 %.0f'
              % (pk.size, np.percentile(pk, 50), np.percentile(pk, 75), np.percentile(pk, 90),
                 np.percentile(pk, 95), np.percentile(pk, 99), pk.max()))
        for lo, hi, nm in ((100, 130, '100-129 强'), (130, 999, '130+ 超强')):
            # ⚠ 口径(轮次 122 修): 这里原来只打 "观测 17%/18%", 那是 **xrq/wp 的
            # WP-only 多年代**口径; 而本工具跑的是**全球单年**, 同口径的观测是
            # 自评/xrq2025 这类单年多海盆集合(见 OBS_REFERENCE.md 3b/3c)。
            # 拿 WP-only 的数当基准会**系统性高估缺口**(轮次 112 已定案)。
            _obs = ('5.3-11.1%' if lo == 100 else '12.3-13.9%')
            _wpo = (17.0 if lo == 100 else 18.0)
            print('   %-11s %3d 个 (%.0f%%)   观测**单年多海盆** %s  '
                  '[WP-only 多年 %.0f%% —— 不可直接比]'
                  % (nm, int(((pk >= lo) & (pk < hi)).sum()),
                     100.0 * ((pk >= lo) & (pk < hi)).mean(), _obs, _wpo))
    print('(参考: 观测 P50 75 / P90 140 / P95 155 / P99 165 / 最大 185)')
    if mx_lat.size:
        print('一生最高纬度(NH n=%d): 中位 %.1f 度 | 到过>=25 %.0f%% | **>=28 %.0f%%** | >=35 %.0f%%'
              '  (观测 25-26 度 / 53-63%% / **40-45%%** / 23-34%%)'
              % (mx_lat.size, float(np.median(mx_lat)),
                 100.0 * float((mx_lat >= 25).mean()), 100.0 * float((mx_lat >= 28).mean()),
                 100.0 * float((mx_lat >= 35).mean())))
        print('生成纬中位 %.1f 度(观测 10.6-12.9) | **净向极位移中位 %.1f 度**(观测 10.3-13.3)'
              ' | 寿命中位 %.0f h(观测 180-222)'
              % (float(np.median(gen_lat)), float(np.median(dlat)), float(np.median(life))))
        # ── **同口径子总体**(第 15 次口径修正, 轮次 104) ──
        # 模型有 ~46% 的系统峰值 <34kt(纯扰动), 而观测的 自评/xrq 里 <34kt 只占 0-10%
        # ⇒ 拿"模型全部"对"观测"会把 8% 达标率的弱系统混进去, 把指标压出一个假缺口
        # (实测: 全部 20.4% vs 峰值>=34kt 子集 **31.0%**)。
        # 所以两种都报, 并**以匹配子总体为主判据**。
        for tag, sel in (('峰值>=34kt(与观测主口径匹配)', peak_l >= 34.0),
                         ('峰值<34kt(纯扰动)', peak_l < 34.0)):
            if int(sel.sum()) < 5:
                continue
            print('   [%s] n=%d | 到过>=28 %.0f%% | >=25 %.0f%% | 最高纬中位 %.1f | 净向极中位 %.1f'
                  % (tag, int(sel.sum()), 100.0 * float((mx_lat[sel] >= 28).mean()),
                     100.0 * float((mx_lat[sel] >= 25).mean()),
                     float(np.median(mx_lat[sel])),
                     float(np.median(np.array(dlat, float)[sel]))))
        print('   **观测同口径基准**: 峰值>=34kt 的到过>=28 度 = 自评2025 **36.1%**'
              ' / 自评2026 **45.6%** / xrq2025 **40.6%**;最高纬中位 25.1-25.9 度;净向极 11.2-13.3 度')
        # ── **实测向极速度剖面**(西太 100-180E, NH) ──
        # 为什么要放进主验收工具: 引导标定(`calib_steer`)量的是**局地引导**,
        # 而"最高纬度"是**积分量**; 两者会在"寿命/停留"上脱节(轮次 104 实测:
        # 局地引导的纬度偏差已压到 |dv|<=0.8kt, 但匹配子总体的净向极位移 17.7 度
        # vs 观测 11.2-13.3 —— 超出 1.4 倍)。有了剖面就能分清"速度太快"还是"活得太久"。
        _bands = ((0, 10), (10, 20), (20, 30), (30, 40), (40, 60))
        acc = {b: [] for b in _bands}
        for t in sim['tcs']:
            _tr = t.get('track') or []
            for i in range(1, len(_tr)):
                la0, lo0 = float(_tr[i - 1]['lat']), float(_tr[i - 1]['lon']) % 360.0
                la1, lo1 = float(_tr[i]['lat']), float(_tr[i]['lon']) % 360.0
                if la0 < 0 or la1 < 0:
                    continue
                if not (100.0 <= lo0 <= 180.0 and 100.0 <= lo1 <= 180.0):
                    continue
                if abs(lo1 - lo0) > 180.0:
                    continue
                mid = 0.5 * (la0 + la1)
                for b in _bands:
                    if b[0] <= mid < b[1]:
                        acc[b].append((la1 - la0) * 110.57 / 6.0 / 1.852)
                        break
        print('   实测向极速度剖面(西太 100-180E, NH; 观测 +2.17/+3.07/+5.16/+9.50/+9.88): '
              + '  '.join(('%d-%dN %+.2f(n=%d)' % (b[0], b[1], float(np.mean(acc[b])), len(acc[b])))
                          if len(acc[b]) >= 20 else
                          ('%d-%dN n.a.(n=%d)' % (b[0], b[1], len(acc[b])))
                          for b in _bands))

    # ── 生成经度分布(检验 ENSO) ──
    # **指标修正(2026-09-21 轮次 59)**: 原来只报"160E 以东占比", 但那个分界把
    # 140-160E 归到西边, 而厄尔尼诺的再分配恰恰就发生在这一段 —— 实测两相位都是 35%,
    # 看着"完全不动", 其实 140-180E 段是 22% vs 7%(明显东移)。
    # 现在改为: 分带占比 + **平均生成经度**(对分布的整体位移敏感)。
    edges = [100, 140, 180, 220, 290]
    tags = ['100-140E(远东)', '140-180E(WP东)', '180-220(中太)', '220-290(东太)']
    cnt = [0] * len(tags)
    lon_sum = 0.0
    n_all = 0
    for t in sim['tcs']:
        lo = float(t['lon']) % 360.0
        lon_sum += lo
        n_all += 1
        for k in range(len(tags)):
            if edges[k] <= lo < edges[k + 1]:
                cnt[k] += 1
                break
    print('生成经度分布(全部系统, n=%d):' % n_all)
    for tg, c in zip(tags, cnt):
        print('   %-14s %4d 个 (%4.1f%%)' % (tg, c, 100.0 * c / max(1, n_all)))
    print('   **平均生成经度 %.1fE**  |  140E 以东占比 %.0f%%'
          % (lon_sum / max(1, n_all), 100.0 * sum(cnt[1:]) / max(1, n_all)))


if __name__ == '__main__':
    main()
