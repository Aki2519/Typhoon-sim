# -*- coding: utf-8 -*-
"""**台风预报检验**(任务 B 的第一块)。

两种检验, 分开报, 因为它们的对照物不同:

**① 自洽性检验(`--mode consistency`)—— 先证明"预报"和"主模型"是一套物理**
   每 `--issue-h` 小时, 取每个存活风暴的当前状态, 用 `core.forecast_members`
   发一个 **零扰动单成员** 72h 预报, 与**主模型自己后续的真实轨迹**(`tc['track']`)
   逐时次比。若两边是同一套物理, 误差应当接近 0(残差来自: 预报不做藤原、
   不做登陆阻尼、强度方程是简化版、以及主模型的随机项)。
   这是本会话反复踩到的"两套实现"陷阱的**制度化防线**:
   轮次 105 就发现预报原来自己写了一套引导(`0.75*u850+0.25*u200`, 无副高修正)。

**② 误差增长(`--mode ensemble`)**
   同样发 12 成员集合预报, 报:
     · 集合平均 vs 主模型分析 的**位置误差**(km, 大圆距离), 分 24/48/72h;
     · 集合**离散度**(成员两两距离的均方根, 或绕均值的半径), 用于判断
       "误差增长与离散度是否同量级"(集合是否过度自信/过度发散)。
   官方(业务)参照量级: 24h ~70-100km / 48h ~130-180km / 72h ~200-280km
   (JMA/JTWC 官方路径误差的气候态, 量级参照; 精确值见 `output/sim/_reference/`)。

**口径警告**: 这里比的是"预报 vs **模型自己的分析**", 不是"预报 vs 观测"。
模型分析本身就是模拟, 所以①只回答"两套实现是否一致", ②只回答"误差如何增长"。
"预报 vs 真实观测"需要**路径锁定(A 模式)导入真实台风**之后再做, 见 `--mode locked`(待实现)。

用法:
    python tools/verify_forecast.py --mode consistency --days 60 --seed 20250801
    python tools/verify_forecast.py --mode ensemble  --days 60 --seed 20250801
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys

import numpy as np

sys.path.insert(0, '.')
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

R_EARTH = 6371.0


def gc_km(lon0, lat0, lon1, lat1):
    """大圆距离(km), 经度按 0-360 环绕处理。"""
    p0, p1 = math.radians(lat0), math.radians(lat1)
    dl = math.radians((lon1 - lon0 + 180.0) % 360.0 - 180.0)
    c = (math.sin(p0) * math.sin(p1)
         + math.cos(p0) * math.cos(p1) * math.cos(dl))
    return R_EARTH * math.acos(max(-1.0, min(1.0, c)))


def track_at(track, t_h, t0):
    """在 track(t 为模拟小时)里取 t0+t_h 时刻的 (lon, lat); 没有就返回 None。"""
    for q in track:
        if abs(float(q['t']) - (t0 + t_h)) < 1e-6:
            return float(q['lon']) % 360.0, float(q['lat'])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='consistency',
                    choices=['consistency', 'ensemble'])
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--days', type=float, default=60.0)
    ap.add_argument('--start', default='2025-06-01 00')
    ap.add_argument('--issue-h', type=float, default=24.0,
                    help='每隔多少小时发一次预报(默认 24h)')
    ap.add_argument('--hours', type=int, default=72)
    ap.add_argument('--n-mem', type=int, default=12)
    a = ap.parse_args()

    from simulator.simcore import core as V
    # forecast=False: **主模型自己不要每 6h 内置发一次集合**(那是 GUI 用的),
    # 本工具是**手工签发**预报。开着会让主模型每步多跑 13 次 env_update,
    # 实测把 25 天的检验跑超时(>600s)。
    sim = V.init({'start': a.start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True,
                             'forecast': False}})
    nstep = int(a.days * 24 / 6)
    issue_every = max(1, int(round(a.issue_h / 6.0)))
    errs = collections.defaultdict(list)       # lead_h -> [km]
    vm_errs = collections.defaultdict(list)
    spread = collections.defaultdict(list)
    pending = []                               # 已发出、待结算的预报
    n_issue = 0
    for step in range(nstep):
        V.sim_step(sim, 6.0)
        tnow = float(sim['t'])
        # ① 结算到期的预报: 与**主模型此刻的位置**比(这才是"对答案")
        for pf in list(pending):
            h = int(round(tnow - pf['t0']))
            if h <= 0:
                continue
            t = pf['tc']
            if t['dead'] or h not in t['_fch']:
                if h >= a.hours:
                    pending.remove(pf)
                continue
            if h in (24, 48, 72):
                pts = [m[h] for m in pf['mems'] if h in m]
                if a.mode == 'consistency':
                    errs[h].append(gc_km(pts[0][0], pts[0][1], t['lon'], t['lat']))
                else:
                    sx = sum(math.cos(math.radians(p[0])) for p in pts)
                    sy = sum(math.sin(math.radians(p[0])) for p in pts)
                    mlon = math.degrees(math.atan2(sy, sx)) % 360.0
                    mlat = sum(p[1] for p in pts) / len(pts)
                    errs[h].append(gc_km(mlon, mlat, t['lon'], t['lat']))
                    spread[h].append(float(np.mean(
                        [gc_km(p[0], p[1], mlon, mlat) for p in pts])))
                vm_errs[h].append(abs(sum(m[h][2] for m in pf['mems'])
                                      / len(pf['mems']) - float(t['vmax'])))
            if h >= a.hours:
                pending.remove(pf)
        # ② 发新预报
        if step % issue_every == 0:
            # **环境序列每次签发只算一次, 全部风暴共享**(它只依赖时钟)
            envs = V.forecast_env_series(sim, a.hours, 6)
            for t in sim['tcs']:
                if t['dead']:
                    continue
                if a.mode == 'consistency':
                    mems = V.forecast_members(sim, t, a.hours, 1, 6, rng=None,
                                              envs=envs)
                else:
                    g = V.make_gauss(V.make_rng(int(sim['seed']) * 31
                                                + int(tnow * 7)))
                    mems = V.forecast_members(sim, t, a.hours, a.n_mem, 6, rng=g,
                                              envs=envs)
                t['_fch'] = set(range(24, a.hours + 1, 24))
                pending.append({'t0': tnow, 'tc': t, 'mems': mems})
                n_issue += 1
    print('=== 预报检验: mode=%s seed=%s %s 起 %d 天 | 发出预报 %d 次 | %d 成员 ==='
          % (a.mode, a.seed, a.start, a.days, n_issue,
             1 if a.mode == 'consistency' else a.n_mem))
    if a.mode == 'consistency':
        print('**自洽性**: 零扰动预报 vs 主模型自己的后续轨迹(应当接近 0)')
    else:
        print('**误差增长**: 集合平均 vs 主模型分析 | 离散度 = 成员到集合平均的距离')
    print('%-8s %7s | %-22s | %s' % ('时效', 'n', '位置误差 中位/平均 (km)', '强度误差中位 (kt)'))
    for h in sorted(errs):
        e = np.array(errs[h], float)
        v = np.array(vm_errs.get(h, [0.0]), float)
        extra = ''
        if a.mode == 'ensemble' and spread.get(h):
            extra = ' | 离散度中位 %.0f km' % np.median(spread[h])
        print('%-8s %7d | %8.1f / %8.1f      | %8.1f%s'
              % ('+%dh' % h, len(e), np.median(e), e.mean(), np.median(v), extra))
    print()
    print('官方业务路径误差量级参照: 24h 70-100km | 48h 130-180km | 72h 200-280km')
    print('判读: ① 自洽性若显著大于 0(比如 >30km@72h), 说明预报与主模型**不是同一套物理**,')
    print('        应先修实现而不是调参数; ② 集合模式下"离散度"应与"误差"同量级 ——')
    print('        离散度远小于误差 = 集合过度自信; 远大于 = 过度发散。')


if __name__ == '__main__':
    main()
