# -*- coding: utf-8 -*-
"""**经向速度预算(分纬度带 × 分半球, 匹配子总体)**: 模型的向极移速快在哪里。

背景(轮次 120 的结论把它变成必做项):
  · `tools/probe_lifecycle.py`(100 天)给出: 模型 生成->34kt **18h** / ->50kt 30h /
    ->64kt 42h / ->100kt 54h, 观测分别是 **54 / 90 / 114 / 150 h** ——
    模型的生成后速率是观测的 2-3 倍; 总寿命 **132h vs 258h**(0.51x)。
  · 同时已知模型向极移速是观测的 ~2 倍(10-20N +6.0 vs +3.1 kt, 20-30N +11.1 vs +5.2)
    —— 风暴以两倍速度冲向冷海/高切变区, 于是"到得了纬度但活不过一半时间"。
    峰值分布之所以还大致对得上, 是因为 MPI 天花板在替寿命兜底。

本工具回答"快在哪里": 把每一步的 `mv` 拆成 steer_v + beta_v + fuji_v + 残差,
与**观测**同纬度带同半球的 dlat/dt 对照。

[!] **两次口径修正(轮次 120 现场踩到, 记下来避免重犯)**:
  1. **必须按半球分开**。纬度带若用 `abs(lat)`, 南半球的"向极"是负值, 与北半球
     混在一起会把均值抵消成假的"向赤道运动"。第一次跑(1-3 月的 60 天窗口)就是这样:
     模型 10-20N 报 **-0.74 kt**, 而它其实是南半球占多数。
  2. **窗口必须覆盖该半球的台风季**。北半球要用 7 月起(南半球用 1 月起), 否则
     窗口里根本没有该半球的风暴 —— "模型向极速度"会变成一句无意义的话。
  3. 模型侧取 **peak>=34kt 的匹配子总体**(与观测命名口径对齐, 见口径规则 15/16)。

用法:
    python tools/probe_v_profile.py --days 100 --start "2025-07-01 00"   # 北半球
    python tools/probe_v_profile.py --days 100 --start "2025-01-01 00"   # 南半球
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

BANDS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 60)]
DEFAULT_OBS = r'G:\气象\数据\xrq\wp'


def band_of(lat):
    a = abs(lat)
    for lo, hi in BANDS:
        if lo <= a < hi:
            return (lo, hi)
    return None


def obs_profile(obs_dir, hemi=1, min_peak=34.0):
    """观测: (带, 半球) -> dlat/dt 样本(kt)。1 度纬 = 60 海里。"""
    from simulator.simcore import import_tc as IMP
    acc = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(obs_dir, '**', '*.dat'), recursive=True)):
        try:
            rows = IMP.parse_bdeck(p)
        except Exception:
            continue
        if len(rows) < 4:
            continue
        if hemi * float(rows[0]['lat']) < -5.0:        # 只取目标半球
            continue
        pk = max(float(r['vmax'] or 0.0) for r in rows)
        if pk < min_peak:
            continue
        for i in range(len(rows) - 1):
            dt = (rows[i + 1]['t'] - rows[i]['t']).total_seconds() / 3600.0
            if dt <= 0 or dt > 12.0:
                continue
            b = band_of(rows[i]['lat'])
            if b is None:
                continue
            acc[b].append((rows[i + 1]['lat'] - rows[i]['lat']) * 60.0 / dt)
    return {k: np.array(v, float) for k, v in acc.items()}


def model_profile(days, seed, start, hemi=1, min_peak=34.0, over=None):
    from simulator.simcore import core as V
    _pr = {'precision': '100ms', 'dataMode': True, 'forecast': False}
    if over:
        _pr.update(over)
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0, 'params': _pr})
    V.env_update(sim)
    per = {}
    for _ in range(int(days * 24 / 6)):
        V.sim_step(sim, 6.0)
        for tc in sim['tcs']:
            if tc['dead']:
                continue
            d = per.setdefault(tc['id'], {'peak': 0.0, 'lo': 1e9, 'hi': -1e9,
                                          'v0': float(tc['vmax']),
                                          's': []})
            d['peak'] = max(d['peak'], float(tc['vmax']))
            d['lo'] = min(d['lo'], float(tc['lat']))
            d['hi'] = max(d['hi'], float(tc['lat']))
            b = band_of(tc['lat'])
            if b is None:
                continue
            st = V.steering_at(sim, tc['lon'], tc['lat'], tc['vmax'])
            be = V.beta_drift(tc['lat'])
            # `sh_pole_steer` 是**正压**加在两层的, 三层权重和为 1 ⇒ 引导里的
            # 修正量就是 dv 本身。于是 base = 总引导 - dv 精确成立, 不必另算一次。
            _du, _dv = V.sh_pole_steer(sim, tc['lon'], tc['lat'])
            d['s'].append((b, float(sim['t']), float(tc['lat']), float(tc['vmax']),
                           float(tc['mv']), float(st[1]), float(be[1]),
                           float(tc.get('fuji') or 0.0), float(_dv)))
    acc = collections.defaultdict(list)
    comp = collections.defaultdict(lambda: collections.defaultdict(list))
    life = []
    for k, d in sorted(per.items()):
        if d['peak'] < min_peak:
            continue
        if hemi > 0 and d['lo'] < -5.0:          # 北半球窗口: 排除南半球风暴
            continue
        if hemi < 0 and d['hi'] > 5.0:           # 南半球窗口: 排除北半球风暴
            continue
        for (b, tv, la, vm, mv, st, be, fu, pv) in d['s']:
            acc[b].append(mv)
            comp[b]['steer'].append(st)
            comp[b]['pole'].append(pv)           # sh_pole_steer 的经向贡献
            comp[b]['base'].append(st - pv)      # 纯分层引导(去掉副高修正)
            comp[b]['beta'].append(be)
            comp[b]['fuji'].append(fu)
            comp[b]['res'].append(mv - st - be - fu)
        m = _life(d, hemi)
        if m is not None:
            life.append(m)
    return ({k: np.array(v, float) for k, v in acc.items()},
            {k: {kk: float(np.mean(vv)) for kk, vv in v.items()}
             for k, v in comp.items()},
            life)


THR = (34.0, 50.0, 64.0, 100.0)


def _life(d, hemi=1):
    """从逐风暴记录算生命周期指标(定义与 `probe_lifecycle.py` 完全一致)。

    ⚠ **向极纬度的符号必须按半球取**: 北半球取 max(lat), 南半球取 min(lat) ——
    否则南半球风暴的"最高纬"会变成它**出发时**的纬度, `>=28%` 恒为 0。
    (轮次 121 现场踩到: 第一次跑南半球窗口时这两列都是废数。)
    """
    s = sorted(d['s'], key=lambda q: q[1])
    if len(s) < 2:
        return None
    t = np.array([q[1] for q in s], float)
    t = t - t[0]
    v = np.array([q[3] for q in s], float)
    latmax = d['hi'] if hemi > 0 else d['lo']
    out = {'life': float(t[-1]), 'v0': float(v[0]), 'peak': float(v.max()),
           'latmax': abs(float(latmax)),
           'lat_net': abs(float(latmax)) - abs(float(s[0][2]))}
    ip = int(np.argmax(v))
    out['t_peak'] = float(t[ip])
    out['t_decay'] = float(t[-1] - t[ip])
    for th in THR:
        idx = np.nonzero(v >= th)[0]
        out['to%d' % int(th)] = float(t[idx[0]]) if idx.size else float('nan')
    return out


def show_life(name, ms):
    if not ms:
        print('  %s: 无样本' % name)
        return
    a = np.array([m['life'] for m in ms], float)
    lat = np.array([m['latmax'] for m in ms], float)
    net = np.array([m['lat_net'] for m in ms], float)
    pk = np.array([m['peak'] for m in ms], float)
    tp = np.array([m['t_peak'] for m in ms], float)
    td = np.array([m['t_decay'] for m in ms], float)
    print('  %-5s n=%3d | 寿命 %5.0fh | 生成->峰值 %5.0fh | 峰值->消亡 %4.0fh | '
          '最高纬 %4.1f | 到过>=28 %4.1f%% | 净向极 %4.1f | 峰值中位 %3.0fkt'
          % (name, len(ms), np.median(a), np.median(tp), np.median(td),
             np.median(lat), 100.0 * float((lat >= 28.0).mean()),
             np.median(net), np.median(pk)))
    cells = []
    for th in THR:
        arr = np.array([m['to%d' % int(th)] for m in ms], float)
        arr = arr[~np.isnan(arr)]
        cells.append('->%dkt %.0fh(n=%d)' % (int(th),
                                             np.median(arr) if arr.size else
                                             float('nan'), arr.size))
    print('        ' + ' | '.join(cells))
    # 峰值分布(C 的判据): P50/P90/P95 与高强度档占比(全部在 peak>=34kt 的
    # 匹配子总体内算 —— 观测 xrq/wp 的命名口径也就是 >=34kt)。
    q = np.percentile(pk, [50, 90, 95])
    print('        峰值 P50/P90/P95 = %.0f/%.0f/%.0f kt | >=64kt %.1f%% | '
          '100-129kt %.1f%% | >=130kt %.1f%%'
          % (q[0], q[1], q[2],
             100.0 * float((pk >= 64.0).mean()),
             100.0 * float(((pk >= 100.0) & (pk < 130.0)).mean()),
             100.0 * float((pk >= 130.0).mean())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=100.0)
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--start', default='2025-07-01 00')
    ap.add_argument('--obs-dir', default=DEFAULT_OBS)
    ap.add_argument('--param', default='',
                    help='覆盖 sim["params"], 形如 shPoleS=-8,shPoleMax=5')
    ap.add_argument('--tag', default='')
    a = ap.parse_args()
    over = {}
    for kv in [x for x in a.param.split(',') if x.strip()]:
        k, _, v = kv.partition('=')
        try:
            over[k.strip()] = float(v)
        except ValueError:
            over[k.strip()] = v.strip()        # 例如 shPoleForm=gauss

    hemi = 1 if int(a.start[5:7]) >= 4 else -1
    print('=== 经向/生命周期联合验收: %s半球 | 模型 %s天 seed=%s | 观测 %s%s ==='
          % ('北' if hemi > 0 else '南', a.days, a.seed, a.obs_dir,
             (' | 覆盖 ' + a.param) if over else ''))
    ob = obs_profile(a.obs_dir, hemi)
    mo, comp, life = model_profile(a.days, a.seed, a.start, hemi, over=over)
    print('%-8s %7s | %10s | %8s | %8s | %8s | %7s | %7s | %7s | %7s'
          % ('纬度带', 'n(模型)', '观测 向极', '模型 mv', '总引导', '纯引导',
             '副高修正', 'beta', 'fuji', '残差'))
    for b in BANDS:
        m = mo.get(b)
        if m is None or m.size == 0:
            continue
        o = ob.get(b)
        c = comp[b]
        print('%2d-%2d    %7d | %10.2f | %8.2f | %8.2f | %8.2f | %7.2f | %7.2f | %7.2f | %7.2f'
              % (b[0], b[1], m.size,
                 float(o.mean()) if o is not None and o.size else float('nan'),
                 float(m.mean()), c['steer'], c['base'], c['pole'],
                 c['beta'], c['fuji'], c['res']))
    print()
    print('读法: 模型 mv = 总引导 + beta + fuji + 残差(残差含随机扰动与一阶滞后);')
    print('      总引导 = 纯引导(分层风环带平均) + 副高修正(`sh_pole_steer`, 正压)。')
    print('观测口径是**总移速**(含 beta), 所以应当比较 "模型 mv" 那一列。')
    print()
    print('--- 生命周期(A 的 3 个判据 + C 的寿命/峰值) ---')
    show_life('模型', life)


if __name__ == '__main__':
    main()
