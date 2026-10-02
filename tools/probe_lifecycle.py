# -*- coding: utf-8 -*-
"""**生命周期/增强速率**诊断: 从生成到 34/50/64/100kt 各要多久, 峰值在哪, 之后多久消亡。

为什么必须有这个工具(轮次 120 的结论把它逼出来的):
  `tools/verify_forecast_locked.py` 的逐样本配对显示, A 模式修好之后偏差的**结构**变了:

    起报 20-34kt: 模型 24h ΔV **+48.7** / 观测 +6.8
    起报 34-50kt: 模型 24h ΔV **+58.2** / 观测 +0.0
    起报 85kt+  : 模型 72h ΔV **-17.1** / 观测 -17.0    <- 强端已经几乎完全正确

  也就是说"弱系统增强得太快"成了**唯一的系统性强度偏差**, 而它同时牵动:
    · B(预报)  —— 弱端爆炸 ⇒ 单点强度预报没有技巧;
    · C(寿命)  —— 模型寿命 90h vs 观测 180-222h, 而"增强快"与"寿命短"会互相抵消,
                   所以**必须一起量、一起改**。

判据(全部用**匹配子总体** peak>=34kt, 见口径规则 15/16):
  · 生成 -> 34kt 的中位小时数(命名耗时)
  · 生成 -> 50kt / 64kt / 100kt 的中位小时数
  · 生成 -> 峰值的中位小时数
  · 峰值 -> 消亡的中位小时数(衰减期)
  · 总寿命中位小时数

用法:
    python tools/probe_lifecycle.py --days 100 --seed 20250801
    python tools/probe_lifecycle.py --days 100 --obs-dir "G:\\气象\\数据\\xrq\\wp"
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

THR = (34.0, 50.0, 64.0, 100.0)
DEFAULT_OBS = r'G:\气象\数据\xrq\wp'


def _stats(rows):
    """rows: [(t_hours, vmax)] 按时间升序 -> 指标字典(可能缺项)。"""
    if len(rows) < 2:
        return None
    t0 = rows[0][0]
    v = np.array([r[1] for r in rows], float)
    t = np.array([r[0] for r in rows], float) - t0
    out = {'life': float(t[-1]), 'v0': float(v[0]), 'peak': float(v.max())}
    ip = int(np.argmax(v))
    out['t_peak'] = float(t[ip])
    out['t_decay'] = float(t[-1] - t[ip])
    for th in THR:
        idx = np.nonzero(v >= th)[0]
        out['to%d' % int(th)] = float(t[idx[0]]) if idx.size else float('nan')
    out['n'] = len(rows)
    return out


def obs_metrics(obs_dir, min_len=4):
    from simulator.simcore import import_tc as IMP
    out = []
    for p in sorted(glob.glob(os.path.join(obs_dir, '**', '*.dat'), recursive=True)):
        try:
            rows = IMP.parse_bdeck(p)
        except Exception:
            continue
        if len(rows) < min_len:
            continue
        rr = [(r['t'], float(r['vmax'] or 0.0)) for r in rows]
        rr.sort(key=lambda q: q[0])
        hrs = [(q[0] - rr[0][0]).total_seconds() / 3600.0 for q in rr]
        m = _stats([(hrs[i], rr[i][1]) for i in range(len(rr))])
        if m:
            m['src'] = os.path.basename(p)
            out.append(m)
    return out


def model_metrics(days, seed, start):
    from simulator.simcore import core as V
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': '100ms', 'dataMode': True,
                             'forecast': False}})
    V.env_update(sim)
    rec = {}
    dead = set()
    for _ in range(int(days * 24 / 6)):
        V.sim_step(sim, 6.0)
        for tc in sim['tcs']:
            k = tc['id']
            if tc['dead']:
                # 消亡后 `_sim_step_core` 还会把它留在 `sim['tcs']` 里最长 120 天,
                # 若继续记录, "寿命"会被拉到窗口末尾 —— 必须跳过。
                dead.add(k)
                continue
            rec.setdefault(k, []).append((float(sim['t']), float(tc['vmax'])))
    out = []
    trunc = 0
    for k, rows in rec.items():
        if k not in dead:
            trunc += 1                 # 窗口结束时仍存活 => 生命史被截断, 不参与统计
            continue
        m = _stats(rows)
        if m:
            out.append(m)
    return out, trunc


def show(name, ms):
    ms = [m for m in ms if m['peak'] >= 34.0]           # 匹配子总体
    if not ms:
        print('%s: 无样本' % name)
        return
    def med(key):
        a = np.array([m[key] for m in ms if not np.isnan(m.get(key, np.nan))], float)
        return (np.median(a), a.size) if a.size else (float('nan'), 0)
    print('--- %s: n=%d (peak>=34kt) ---' % (name, len(ms)))
    print('  起报强度中位 %.0fkt | 峰值中位 %.0fkt' %
          (np.median([m['v0'] for m in ms]), np.median([m['peak'] for m in ms])))
    line = []
    for th in THR:
        v, n = med('to%d' % int(th))
        line.append('->%dkt %.0fh(n=%d)' % (int(th), v, n))
    print('  ' + ' | '.join(line) + '   (从**首条记录**算起)')
    print('  生成->峰值 %.0fh | 峰值->消亡 %.0fh | 总寿命 %.0fh'
          % (med('t_peak')[0], med('t_decay')[0], med('life')[0]))
    # 低端增强速率: 从 v0 到 50kt 的平均速率(kt/天), 只统计真的到过 50kt 的
    r = []
    for m in ms:
        if not np.isnan(m.get('to50', float('nan'))) and m['to50'] > 1.0:
            r.append((50.0 - m['v0']) / (m['to50'] / 24.0))
    if r:
        print('  低端增强速率(v0 -> 50kt) 中位 **%.1f kt/天**' % float(np.median(r)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=100.0)
    ap.add_argument('--seed', default='20250801')
    ap.add_argument('--start', default='2025-01-01 00')
    ap.add_argument('--obs-dir', default=DEFAULT_OBS)
    ap.add_argument('--skip-model', action='store_true')
    a = ap.parse_args()

    print('=== 生命周期/增强速率: 模型 %s天 seed=%s vs 观测 %s ==='
          % (a.days, a.seed, a.obs_dir))
    om = obs_metrics(a.obs_dir)
    show('观测', om)
    print()
    if not a.skip_model:
        mm, trunc = model_metrics(a.days, a.seed, a.start)
        show('模型', mm)
        print('  (另有 %d 个在窗口结束时仍存活 => 生命史被截断, 未计入)' % trunc)
    print()
    print('口径: 观测是**命名台风**(xrq/wp), 覆盖 1980-2024 全样本, 含北大/东太/西太/南半球;')
    print('      模型是**全球 100 天窗口**的自由生成。两者都取 peak>=34kt 的匹配子总体。')
    print('      首条记录的定义在两侧不同(观测 b-deck 首行 vs 模型 make_tc), 这会让')
    print('      "->34kt" 在模型侧偏短约一步(6h) —— 结论只在小时量级上使用。')


if __name__ == '__main__':
    main()
