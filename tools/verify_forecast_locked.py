# -*- coding: utf-8 -*-
"""**预报对观测的检验**(任务 B 的正式判据)。

与 `verify_forecast.py` 的区别(两个检验回答的问题不同, 必须分开报):
  · `verify_forecast.py` = "预报 vs **模型自己的分析**" → 只回答"两套实现是否自洽";
  · **本工具** = "预报 vs **真实观测**" → 才是业务意义上的路径/强度误差。

做法(标准的路径锁定检验):
  1. 从观测 b-deck 读一条真实路径(`自评` 含扰动 / `xrq/wp` 只含命名台风);
  2. 把模拟的起始时刻设成该路径的首报时刻, 用 A 模式(`import_tc.make_from_track`)
     **把位置锁在观测路径上**, 强度仍由模型自己演化 —— 这样强度误差才归因于强度方程,
     而不是"路径走偏导致环境不同";
  3. 沿真实路径推进, 每隔 `--issue-h` 小时从**当时的锁定状态**发一次 72h 自由预报
     (`core.forecast_members`, 单成员确定性 / 多成员集合);
  4. 把预报位置/强度与**同一时刻的真实观测**比 → 位置误差(km) / 强度误差(kt),
     按 24/48/72h 汇总。

**口径声明**:
  · 位置误差用大圆距离; 经度按 0-360 环绕。
  · 只统计**预报时效内观测仍在报**的样本(风暴提前消散/观测中断的样本不计入该时效),
    这会**乐观**; 因此同时报样本数 n。
  · 集合模式额外报**离散度**(成员到集合平均的距离), 与误差同量级才算"可信的集合"。
  · 参照量级: 官方业务 24h ~70-100km / 48h ~130-180km / 72h ~200-280km
    (JMA/JTJWC 近年官方路径误差气候态; 这是**量级参照**, 精确值需另行核对)。

用法:
    python tools/verify_forecast_locked.py --n-tracks 20 --hours 72
    python tools/verify_forecast_locked.py --n-tracks 20 --mode ensemble --n-mem 12
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys
from datetime import timedelta

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

R_EARTH = 6371.0
DEFAULT_OBS = r'G:\气象\数据\xrq\wp'


def clim_drift(tracks):
    """从观测路径统计**气候漂移基线**: (纬度带, 月份带, 时效) -> 平均 (dlon, dlat)。

    为什么必须有基线: 模型的"环境"是**月气候态 + 合成瞬变**(实测 `simulator/env/realdir/`
    里只有 1 个日期的瞬时场), 所以这个"预报"本质上**没有实时信息**。
    它的误差只有与**同样没有实时信息的基线**比, 才知道有没有技巧:
      · 持续性(persistence): 位置不动;
      · 气候漂移(climatology): 加上该纬度/季节/时效的平均位移。
    与官方业务误差(24h 70-100km)直接比是**没有意义的** —— 那需要真实分析场。
    """
    acc = {}
    for r in tracks:
        for i in range(len(r) - 1):
            la0, lo0 = r[i][0], r[i][1]
            b = int(abs(la0) // 10) * 10
            if not (0 <= b <= 50):
                continue
            for h in (24, 48, 72):
                j = i + h // 6
                if j >= len(r):
                    break
                la1, lo1 = r[j][0], r[j][1]
                dlo = (lo1 - lo0 + 180.0) % 360.0 - 180.0
                k = (b, r[i][2], h) if False else (b, h)
                acc.setdefault(k, []).append((dlo, la1 - la0))
    out = {}
    for k, v in acc.items():
        a = np.array(v, float)
        out[k] = (float(a[:, 0].mean()), float(a[:, 1].mean()), len(v))
    return out


def gc_km(lon0, lat0, lon1, lat1):
    p0, p1 = math.radians(lat0), math.radians(lat1)
    dl = math.radians((lon1 - lon0 + 180.0) % 360.0 - 180.0)
    c = math.sin(p0) * math.sin(p1) + math.cos(p0) * math.cos(p1) * math.cos(dl)
    return R_EARTH * math.acos(max(-1.0, min(1.0, c)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--obs', default=DEFAULT_OBS)
    ap.add_argument('--n-tracks', type=int, default=20)
    ap.add_argument('--year-min', type=int, default=2015)
    ap.add_argument('--year-max', type=int, default=2024)
    ap.add_argument('--month-min', type=int, default=6)
    ap.add_argument('--month-max', type=int, default=11)
    ap.add_argument('--hours', type=int, default=72)
    ap.add_argument('--issue-h', type=float, default=24.0)
    ap.add_argument('--mode', default='det', choices=['det', 'ensemble'])
    ap.add_argument('--n-mem', type=int, default=12)
    ap.add_argument('--min-len', type=int, default=12)
    # 逐样本落盘(JSON)。**为什么必须有**: 这个检验是 A/B 标定的主战场, 而汇总表
    # 只给中位数 —— 两次运行的**样本集**会变(A 模式下台风会在观测路径上提前消亡,
    # 样本数 53 vs 38), 拿两张汇总表对比等于拿不同总体比。有了逐样本记录才能
    # 按 (路径, 起报序号, 时效) 精确配对, 算"同一批样本上改动的净效果"。
    ap.add_argument('--dump', default=None, help='把逐样本记录写成 JSON')
    a = ap.parse_args()

    from simulator.simcore import core as V
    from simulator.simcore import import_tc as IMP

    paths = sorted(glob.glob(os.path.join(a.obs, '**', '*.dat'), recursive=True))
    tracks = []
    for p in paths:
        try:
            rows = IMP.parse_bdeck(p)
        except Exception:
            continue
        if len(rows) < a.min_len:
            continue
        t0 = rows[0]['t']
        if not (a.year_min <= t0.year <= a.year_max):
            continue
        if not (a.month_min <= t0.month <= a.month_max):
            continue
        if rows[0]['lat'] < 0:                     # 只做北半球(分布另有口径)
            continue
        # 步长必须规则(6h), 否则"第 k 步对应第 k 行"的假设不成立
        dt = {(rows[i + 1]['t'] - rows[i]['t']).total_seconds() / 3600.0
              for i in range(len(rows) - 1)}
        if dt != {6.0}:
            continue
        tracks.append(rows)
        if len(tracks) >= a.n_tracks:
            break
    if not tracks:
        print('没有可用的观测路径')
        return
    print('=== 预报对观测检验: %d 条路径 | 起报间隔 %.0fh | 时效 %dh | 模式 %s ==='
          % (len(tracks), a.issue_h, a.hours, a.mode))

    errs = collections.defaultdict(list)
    verrs = collections.defaultdict(list)
    vbias = collections.defaultdict(list)     # 带符号的强度偏差(模型-观测)
    spread = collections.defaultdict(list)
    _pairs = collections.defaultdict(list)    # (观测强度, 模型强度) 样本
    pers = collections.defaultdict(list)      # 持续性基线
    clim_err = collections.defaultdict(list)  # 气候漂移基线
    _all_obs = []
    for _p in paths:
        try:
            _r = IMP.parse_bdeck(_p)
        except Exception:
            continue
        if len(_r) >= a.min_len:
            _all_obs.append([(q['lat'], q['lon'] % 360.0, q['vmax']) for q in _r])
    CLIM = clim_drift(_all_obs)
    n_issue = 0
    dump = []
    for _ti, rows in enumerate(tracks):
        start = rows[0]['t'].strftime('%Y-%m-%d %H')
        try:
            sim = V.init({'start': start, 'seed': 7, 'envStepH': 6.0,
                          'params': {'precision': '100ms', 'dataMode': True,
                                     'forecast': False}})
            V.env_update(sim)
            tc = IMP.make_from_track(sim, rows, mode='A')
        except Exception as exc:
            print('  [跳过] %s: %s' % (start, exc))
            continue
        if tc is None:
            continue
        issue_every = max(1, int(round(a.issue_h / 6.0)))
        idx = {rows[i]['t']: i for i in range(len(rows))}
        for k in range(len(rows) - 1):
            try:
                V.sim_step(sim, 6.0)
            except Exception:
                break
            if tc.get('dead'):
                break
            if k % issue_every:
                continue
            t_issue = rows[k + 1]['t']
            # 时效不能超出观测路径
            lead_ok = [h for h in (24, 48, 72)
                       if h <= a.hours and (t_issue + timedelta(hours=h)) in idx]
            if not lead_ok:
                continue
            hours = max(lead_ok)
            n_issue += 1
            if a.mode == 'det':
                mems = V.forecast_members(sim, tc, hours, 1, 6, rng=None)
            else:
                g = V.make_gauss(V.make_rng(int(sim['seed']) * 31
                                            + int(sim['t'] * 7)))
                mems = V.forecast_members(sim, tc, hours, a.n_mem, 6, rng=g)
            for h in lead_ok:
                obs = rows[idx[t_issue + timedelta(hours=h)]]
                pts = [mm[h] for mm in mems if h in mm]
                if not pts:
                    continue
                if a.mode == 'det':
                    flo, fla, fv = pts[0]
                else:
                    sx = sum(math.cos(math.radians(q[0])) for q in pts)
                    sy = sum(math.sin(math.radians(q[0])) for q in pts)
                    flo = math.degrees(math.atan2(sy, sx)) % 360.0
                    fla = sum(q[1] for q in pts) / len(pts)
                    fv = sum(q[2] for q in pts) / len(pts)
                    spread[h].append(float(np.mean(
                        [gc_km(q[0], q[1], flo, fla) for q in pts])))
                errs[h].append(gc_km(flo, fla, obs['lon'], obs['lat']))
                verrs[h].append(abs(fv - float(obs['vmax'])))
                vbias[h].append(float(fv) - float(obs['vmax']))
                _pairs.setdefault(h, []).append((float(obs['vmax']), float(fv)))
                # 持续性基线: 起报位置不动
                pers[h].append(gc_km(rows[k + 1]['lon'], rows[k + 1]['lat'],
                                     obs['lon'], obs['lat']))
                # 气候漂移基线: 起报位置 + 同纬度带宽的平均位移
                _b = int(abs(rows[k + 1]['lat']) // 10) * 10
                _c = CLIM.get((_b, h))
                _clim_e = None
                if _c is not None:
                    _clim_e = gc_km(
                        rows[k + 1]['lon'] + _c[0], rows[k + 1]['lat'] + _c[1],
                        obs['lon'], obs['lat'])
                    clim_err[h].append(_clim_e)
                dump.append({
                    'track': _ti, 'issue': k, 'h': h,
                    't_issue': t_issue.strftime('%Y-%m-%d %H'),
                    'init_lon': rows[k + 1]['lon'], 'init_lat': rows[k + 1]['lat'],
                    'init_vmax': float(rows[k + 1]['vmax']),
                    'obs_lon': obs['lon'], 'obs_lat': obs['lat'],
                    'obs_vmax': float(obs['vmax']),
                    'fc_lon': flo, 'fc_lat': fla, 'fc_vmax': float(fv),
                    'err_km': gc_km(flo, fla, obs['lon'], obs['lat']),
                    'pers_km': gc_km(rows[k + 1]['lon'], rows[k + 1]['lat'],
                                     obs['lon'], obs['lat']),
                    'clim_km': _clim_e,
                })
    print('发出预报 %d 次' % n_issue)
    print('%-8s %7s | %-22s | %-18s | %-18s | %s'
          % ('时效', 'n', '模型 位置 中位/平均', '持续性基线 中位', '气候漂移基线 中位',
             '强度误差 中位(带符号)'))
    for h in sorted(errs):
        e = np.array(errs[h], float)
        v = np.array(verrs.get(h, [0.0]), float)
        pe = np.median(pers[h]) if pers.get(h) else float('nan')
        ce = np.median(clim_err[h]) if clim_err.get(h) else float('nan')
        extra = ''
        if a.mode == 'ensemble' and spread.get(h):
            extra = ' | 离散度中位 %.0f km' % np.median(spread[h])
        print('%-8s %7d | %7.1f / %7.1f    | %8.1f        | %8.1f        | %6.1f (%+6.1f)%s'
              % ('+%dh' % h, len(e), np.median(e), e.mean(), pe, ce,
                 np.median(v), np.median(vbias[h]) if vbias.get(h) else 0.0, extra))
    print()
    for h in sorted(errs):
        e = np.array(errs[h], float)
        ce = np.array(clim_err.get(h, []), float)
        if ce.size == e.size and ce.size:
            sk = 1.0 - e.mean() / max(1e-9, ce.mean())
            print('  +%dh: 相对气候漂移的技巧分 = 1 - 模型误差/气候基线 = %+.2f %s'
                  % (h, sk, '(有技巧)' if sk > 0.1 else '(几乎没有技巧)'))
    print()
    print('**强度样本(观测 -> 模型)**, 每档前 12 对:')
    for h in sorted(_pairs):
        print('  +%dh: ' % h + '  '.join('%d->%d' % p for p in _pairs[h][:12]))
    print()
    print('官方业务量级参照: 24h 70-100km | 48h 130-180km | 72h 200-280km')
    print()
    print('**重要口径(轮次 118)**: 模型的"环境"是**月气候态 + 合成瞬变**')
    print('(`simulator/env/realdir/` 里只有 1 个日期的 ERA5 瞬时场), 所以这个"预报"')
    print('**本质上没有实时信息** —— 与官方业务误差直接比是**没有意义的**(那需要真实分析场)。')
    print('有意义的判据是**相对基线**: 只要明显优于"持续性"与"气候漂移", 就说明引导场')
    print('与强度方程本身没有坏; 若还不如气候漂移, 那才是模型的问题。')
    if a.dump:
        with open(a.dump, 'w', encoding='utf-8') as f:
            json.dump(dump, f, ensure_ascii=False)
        print('逐样本记录已写出: %s (%d 条)' % (a.dump, len(dump)))


if __name__ == '__main__':
    main()
