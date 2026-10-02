# -*- coding: utf-8 -*-
"""T3/T4: TC 模拟 vs 观测的**路径与强度**评估。

对标口径(与用户约定一致):
  vmax = 1 分钟平均海面最大持续风(JTWC 口径, 观测资料本身就是这个口径)
  命名门槛 34 kt; 快速增强 = 24h +30 kt

指标:
  生成   年生成数、分洋区数、生成点空间分布(与观测密度场的相关)
  路径   路径密度场相关、平均移速、平均生命史、转向点纬度
  强度   峰值强度分位(P25/50/75/95/max)、24h Δvmax 分布、RI 发生率、
         ACE、MPI 利用率 V/MPI、登陆后衰减率

用法:
  python tools/eval_tc.py --sim output/sim/1997/tcsim_1997-01-01_00_365d.npz
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
TS = 34.0


def resample6(tc):
    """把模拟的**逐小时**序列抽成 6 小时一次, 与观测最佳路径口径对齐。

    踩过: 不抽的话"生命史"看起来是 361 点(其实是小时)、移速被除错、
    Δvmax 的 24h 窗口变成 4h、ACE 归一化也跟着错 —— 全是口径问题不是模型问题。
    """
    t = np.asarray(tc['t'], float)
    if t.size < 2:
        return tc
    dt = np.median(np.diff(t))
    step = max(1, int(round(6.0 / max(dt, 1e-6))))
    out = {}
    for k in ('t', 'lon', 'lat', 'vmax', 'pmin'):
        out[k] = np.asarray(tc[k])[::step]
    return out


def load_sim(path):
    z = np.load(path, allow_pickle=False)
    ids = sorted({int(k.split('_')[1]) for k in z.files if k.startswith('t_')})
    out = []
    for i in ids:
        try:
            t = z['t_%d' % i]
            if t.size < 4:
                continue
            out.append(resample6({'t': t, 'lon': z['lon_%d' % i],
                                  'lat': z['lat_%d' % i],
                                  'vmax': z['vmax_%d' % i],
                                  'pmin': z['pmin_%d' % i]}))
        except KeyError:
            continue
    return out


def load_obs(y0, y1):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tclim', os.path.join(ROOT, 'tools', 'train_tc_clim.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tracks, _ = m.collect()

    def dt(t):
        import datetime as d
        return d.datetime(t // 1000000, (t // 10000) % 100, (t // 100) % 100, t % 100)
    out = []
    for k, (prio, tr) in tracks.items():
        tr = [r for r in tr if y0 <= dt(r[0]).year <= y1]
        if len(tr) < 4:
            continue
        # **时间轴必须是"自起点的小时数", 不能直接用 YYYYMMDDHH 整数**。
        # 整数相减不是时间差: 2015102400 − 2015102318 = 82, 而同日 6h 间隔是 6。
        # 原来 stats() 用 tt[k]-tt[j] >= 22 找"24h 后的点", 于是几乎任何跨日点对
        # 都满足 → 实际算的是 6~18h 的变化, **把观测的 RI 率系统性低估**
        # (评估器给 1.57%, 而 I1 用真 datetime 算出的是 3.5%, 两者矛盾了很久)。
        _t0 = dt(tr[0][0])
        hrs = np.array([(dt(r[0]) - _t0).total_seconds() / 3600.0 for r in tr],
                       np.float32)
        out.append({'t': hrs,
                    'lat': np.array([r[1] for r in tr], np.float32),
                    'lon': np.array([r[2] for r in tr], np.float32),
                    'vmax': np.array([r[3] for r in tr], np.float32),
                    'pmin': np.array([r[4] for r in tr], np.float32),
                    'basin': k[0]})
    return out


def _dtv(t):
    import datetime as d
    return d.datetime(int(t) // 1000000, (int(t) // 10000) % 100,
                      (int(t) // 100) % 100, int(t) % 100)


def stats(tracks, label):
    peak, ace, life, spd, dv24, vmpi = [], [], [], [], [], []
    #: **按风暴口径**的 RI: "经历过至少一次 RI 的命名风暴占比"。
    #: 这与"24h 窗口里含 RI 的比例"是**两个不同的量** —— 一个 30 点(180h)的
    #: 风暴有几十个窗口, 所以"47% 的风暴经历过 RI"和"4% 的窗口含 RI"完全相容。
    #: 用户 2026-09-17 指出这一点: xrq 数据按风暴口径是 49.8%(>=30kt)/
    #: 38.5%(>=35kt), 而我一直只报窗口口径(3.75%), 把结论带偏了。
    ri_storm30 = ri_storm35 = ri_n = 0
    for tc in tracks:
        v = np.asarray(tc['vmax'], float)
        if v.size < 4:
            continue
        peak.append(v.max())
        tt = np.asarray(tc['t'], float)
        dh = np.diff(tt)
        dh = np.where(dh > 0, dh, 6.0)
        # ACE = Σ v²×1e-4, 每 6h 一点(与观测同口径)
        ace.append(float((v ** 2 * 1e-4).sum() * (np.median(dh) / 6.0)))
        life.append(v.size)
        for j in range(v.size - 1):
            spd.append(np.hypot((tc['lat'][j + 1] - tc['lat'][j]) * 60.0,
                                (tc['lon'][j + 1] - tc['lon'][j]) * 60.0
                                * np.cos(np.radians(tc['lat'][j]))) / dh[j])
        # 24h 后的 Δvmax: 按**真实时间**找 ≥22h 的第一个点, 不是按点数
        _h30 = _h35 = False
        for j in range(v.size):
            for k in range(j + 1, v.size):
                if tt[k] - tt[j] >= 22.0:
                    _d = v[k] - v[j]
                    dv24.append(_d)
                    if _d >= 30:
                        _h30 = True
                    if _d >= 35:
                        _h35 = True
                    break
        if v.max() >= 34.0:                 # 命名风暴才计入"经历 RI 的风暴"
            ri_n += 1
            ri_storm30 += 1 if _h30 else 0
            ri_storm35 += 1 if _h35 else 0
    peak = np.asarray(peak)
    out = {'n': len(peak), 'peak': peak,
           'ace': np.asarray(ace), 'life': np.asarray(life),
           'spd': np.asarray(spd), 'dv24': np.asarray(dv24),
           'ri30': ri_storm30 / max(1, ri_n), 'ri35': ri_storm35 / max(1, ri_n)}
    if len(peak) == 0:
        return out
    print('  %-6s 风暴数 %4d' % (label, len(peak)))
    print('    峰值强度  P25 %3.0f  P50 %3.0f  P75 %3.0f  P95 %3.0f  max %3.0f'
          % tuple(np.percentile(peak, [25, 50, 75, 95]).tolist() + [peak.max()]))
    print('    生命史(点) P50 %3.0f   ACE 均值 %5.1f'
          % (np.median(out['life']), out['ace'].mean()))
    if out['spd'].size:
        print('    移速(kt)  P50 %4.1f' % np.median(out['spd']))
    if out['dv24'].size:
        d = out['dv24']
        print('    24h Δvmax  P10 %4.0f  P50 %4.0f  P90 %4.0f'
              % tuple(np.percentile(d, [10, 50, 90])))
        print('    RI  风暴口径(≥30kt) %.1f%%  (≥35kt) %.1f%%   |   窗口口径 %.2f%%'
              % (out['ri30'] * 100, out['ri35'] * 100, (d > 30).mean() * 100))
    return out


#: 洋区边界(按生成点经度) —— 不同资料的覆盖年份差别极大(实测南半球 45 年里
#: 有 26 年完全是 0), 混在一起比会把结论带偏, 所以必须分洋区。
BASIN_LON = (('WP', 100.0, 180.0), ('EP', 180.0, 260.0),
             ('AL', 260.0, 360.0), ('IO', 20.0, 100.0))


def basin_of(lon):
    lo = lon % 360
    for nm, a, b in BASIN_LON:
        if a <= lo < b:
            return nm
    return 'SH' if lo < 20 or lo >= 0 else 'XX'


def basin_stats(tracks, label):
    by = collections.defaultdict(list)
    for tc in tracks:
        lon = np.asarray(tc['lon'], float)
        if lon.size == 0:
            continue
        by[basin_of(float(lon[0]))].append(tc)
    out = {}
    for nm in ('WP', 'EP', 'AL', 'IO'):
        if by.get(nm):
            out[nm] = stats(by[nm], '%s-%s' % (label, nm))
    return out


#: 登陆点统计用的陆地区域名(按经度分箱), 便于看"登陆在哪"
def landfall_stats(tracks, label, sim=None):
    """登陆事件: 陆地比例从 <0.3 跨到 >0.3 的那一刻。

    T6 要求比对登陆点密度/次数。掩码修好之前这件事做不了 —— 西半球整个
    缺失, 东太/大西洋的登陆率算出来是 0%。
    """
    from simulator.simcore import core as V
    if sim is None:
        sim = V.init({'start': '1997-08-15 00', 'params': {'dataMode': True}})
    per = collections.Counter()
    pts = []
    n = 0
    for tc in tracks:
        lon = np.asarray(tc['lon'], float)
        lat = np.asarray(tc['lat'], float)
        if lon.size < 3:
            continue
        b = basin_of(float(lon[0]))
        lv = np.array([V.land_at(sim, float(o), float(a))
                       for a, o in zip(lat, lon)])
        idx = np.nonzero((lv[1:] > 0.3) & (lv[:-1] <= 0.3))[0]
        if idx.size:
            n += 1
            per[b] += 1
            for i in idx[:3]:
                pts.append((float(lat[i + 1]), float(lon[i + 1]) % 360))
    return {'n': n, 'per': per, 'pts': pts}


def track_density(tracks, w=5.0):
    """路径密度场: 把每条路径的每个点投到 w 度网格。返回 (lat, lon, 场)。"""
    import numpy as _np
    la0, la1 = -60.0, 60.0
    ny = int(round((la1 - la0) / w))
    nx = int(round(360.0 / w))
    g = _np.zeros((ny, nx), _np.float64)
    for tc in tracks:
        lo = _np.asarray(tc['lon'], float) % 360.0
        la = _np.asarray(tc['lat'], float)
        for a, b in zip(la, lo):
            j = int((a - la0) / w)
            i = int(b / w)
            if 0 <= j < ny and 0 <= i < nx:
                g[j, i] += 1.0
    lat = la0 + (_np.arange(ny) + 0.5) * w
    lon = (_np.arange(nx) + 0.5) * w
    return lat, lon, g


def density_report(sim, obs, w=5.0):
    """T3 的路径密度差: 哪里模拟偏密、哪里偏疏。"""
    import numpy as _np
    la, lo, gs = track_density(sim, w)
    _, _, go = track_density(obs, w)
    # 按总点数归一(两者风暴数不同, 比的是**空间分布**不是绝对量)
    gs = gs / max(1.0, gs.sum()) * 100.0
    go = go / max(1.0, go.sum()) * 100.0
    d = gs - go
    print('\n=== 路径密度(T3, %g° 网格, 已按总点数归一到 %%)===' % w)
    regions = (('西太 100-180E 5-30N', 100, 180, 5, 30),
               ('东太 180-260E 5-25N', 180, 260, 5, 25),
               ('大西洋 260-360E 5-35N', 260, 360, 5, 35),
               ('北印度 40-100E 5-25N', 40, 100, 5, 25),
               ('南半球 0-360E 0-40S', 0, 360, -40, -5),
               ('中高纬 >35N', 0, 360, 35, 60))
    print('  %-24s %9s %9s %9s' % ('区域', '模拟%', '观测%', '差'))
    for nm, x0, x1, y0, y1 in regions:
        i0 = int(x0 / w) % (360 // int(w))
        i1 = int(x1 / w) % (360 // int(w)) or (360 // int(w))
        j0 = max(0, int((y0 + 60.0) / w))
        j1 = min(gs.shape[0], int((y1 + 60.0) / w))
        if i1 <= i0:
            a = gs[j0:j1, i0:].sum() + gs[j0:j1, :i1].sum()
            b = go[j0:j1, i0:].sum() + go[j0:j1, :i1].sum()
        else:
            a = gs[j0:j1, i0:i1].sum()
            b = go[j0:j1, i0:i1].sum()
        print('  %-24s %8.1f%% %8.1f%% %+8.1f' % (nm, a, b, a - b))
    # 最偏密/偏疏的格点
    idx = _np.dstack(_np.unravel_index(_np.argsort(d.ravel())[::-1][:5], d.shape))[0]
    print('  模拟最偏密的 5 个格点:',
          ', '.join('%dN %dE(+%.2f)' % (la[j], lo[i], d[j, i]) for j, i in idx))
    idx = _np.dstack(_np.unravel_index(_np.argsort(d.ravel())[:5], d.shape))[0]
    print('  模拟最偏疏的 5 个格点:',
          ', '.join('%dN %dE(%.2f)' % (la[j], lo[i], d[j, i]) for j, i in idx))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', required=True)
    ap.add_argument('--y0', type=int, default=1980)
    ap.add_argument('--y1', type=int, default=2024)
    a = ap.parse_args()
    sim = load_sim(a.sim)
    obs = load_obs(a.y0, a.y1)
    print('模拟 %d 条路径   观测 %d 条(1980-2024)' % (len(sim), len(obs)))
    ny = (a.y1 - a.y0 + 1)
    print('\n=== 生成 ===')
    print('  模拟年生成 %.1f 个   观测年均 %.1f 个'
          % (len(sim), len(obs) / ny))
    print('\n=== 强度/路径 ===')
    s = stats(sim, '模拟')
    o = stats(obs, '观测')
    # 分洋区对比: 不同资料的覆盖年份差别极大(实测南半球 45 年里 26 年完全是 0),
    # 全球汇总会把"资料缺口"当成"模拟偏差"。按洋区各自对比才是同类比。
    sb = basin_stats(sim, '模拟')
    ob = basin_stats(obs, '观测')
    if sb and ob:
        print('\n=== 分洋区对比 ===')
        print('  %-5s %7s %7s | %9s %9s | %9s %9s'
              % ('洋区', '模拟n', '观测n', '模拟P50', '观测P50',
                 '模拟P95', '观测P95'))
        for nm in ('WP', 'EP', 'AL', 'IO'):
            if nm not in sb or nm not in ob:
                continue
            print('  %-5s %7d %7d | %9.0f %9.0f | %9.0f %9.0f'
                  % (nm, sb[nm]['n'], ob[nm]['n'],
                     np.median(sb[nm]['peak']), np.median(ob[nm]['peak']),
                     np.percentile(sb[nm]['peak'], 95),
                     np.percentile(ob[nm]['peak'], 95)))
    # T3: 路径密度差 —— 哪里模拟偏密、哪里偏疏
    try:
        density_report(sim, obs)
    except Exception as e:
        print('  路径密度评估失败: %s' % str(e)[:80])
    # T6: 登陆评估
    try:
        import sys as _sys
        _sys.path.insert(0, ROOT)
        from simulator.simcore import core as _V
        _sim = _V.init({'start': '1997-08-15 00', 'params': {'dataMode': True}})
        ls = landfall_stats(sim, '模拟', _sim)
        lo = landfall_stats(obs, '观测', _sim)
        if ls['n'] or lo['n']:
            print('\n=== 登陆(T6)===')
            print('  登陆风暴数: 模拟 %d / %d (%.0f%%)   观测 %d / %d (%.0f%%)'
                  % (ls['n'], len(sim), ls['n'] / max(1, len(sim)) * 100,
                     lo['n'], len(obs), lo['n'] / max(1, len(obs)) * 100))
            print('  %-6s %10s %10s' % ('洋区', '模拟', '观测'))
            for b in ('WP', 'EP', 'AL', 'IO'):
                print('  %-6s %10d %10d'
                      % (b, ls['per'].get(b, 0), lo['per'].get(b, 0)))
    except Exception as e:
        print('  登陆评估失败: %s' % str(e)[:80])
    if len(s['peak']) and len(o['peak']):
        print('\n=== 对比 ===')
        for q in (25, 50, 75, 95):
            print('  峰值 P%-2d  模拟 %3.0f   观测 %3.0f   差 %+4.0f'
                  % (q, np.percentile(s['peak'], q), np.percentile(o['peak'], q),
                     np.percentile(s['peak'], q) - np.percentile(o['peak'], q)))
        print('  RI 发生率  模拟 %.2f%%   观测 %.2f%%'
              % ((s['dv24'] > 30).mean() * 100, (o['dv24'] > 30).mean() * 100))
        print('  ACE 均值   模拟 %.1f    观测 %.1f'
              % (s['ace'].mean(), o['ace'].mean()))


if __name__ == '__main__':
    main()
