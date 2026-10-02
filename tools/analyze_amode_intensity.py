# -*- coding: utf-8 -*-
"""**A 模式强度诊断**(轮次 120 新增)。

`tools/verify_forecast_locked.py --dump` 落下的逐样本 JSON 在这里做三件汇总表做不到的事:

  1. **精确配对 A/B**: 汇总表只给中位数, 而两次运行的样本集会变 —— A 模式下台风会在
     观测路径上提前消亡(实测同一命令修复前 53 条 / 修复后 38 条), 拿两张汇总表对比
     等于拿不同总体比。这里按 (路径, 起报序号, 时效) 求交集, 只比同一批样本。
  2. **回归到均值诊断**: 把"模型时效强度"对"起报观测强度"做回归。斜率 →0 意味着模型
     把任何初值都拉向同一个 MPI, 即**没有初值记忆**; 这是"弱扰动爆炸 / 强台风被砍"的
     共同根因, 也是单点强度预报无技巧的直接证据。
  3. **分档强度变化**: 按起报强度分档, 比较观测与模型的平均 24/48/72h 变化量。

用法:
    # 只看一次运行(诊断)
    python tools/analyze_amode_intensity.py output/sim/_reference/amode_after.json
    # 配对 A/B
    python tools/analyze_amode_intensity.py before.json after.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

BINS = [(20, 34), (34, 50), (50, 65), (65, 85), (85, 200)]


def load(p):
    with open(p, encoding='utf-8') as f:
        rows = json.load(f)
    keep = []
    for r in rows:
        if r.get('fc_vmax') is None:
            continue
        keep.append(r)
    return keep


def key(r):
    return (r['track'], r['issue'], r['h'])


def _fit(x, y):
    """最小二乘 y = a + b x, 返回 (b, r)。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if x.size < 3 or x.std() < 1e-9:
        return float('nan'), float('nan')
    b = float(np.polyfit(x, y, 1)[0])
    r = float(np.corrcoef(x, y)[0, 1])
    return b, r


def report(rows, tag):
    print('--- %s: %d 条 ---' % (tag, len(rows)))
    for h in (24, 48, 72):
        s = [r for r in rows if r['h'] == h]
        if not s:
            continue
        iv = np.array([r['init_vmax'] for r in s], float)
        ov = np.array([r['obs_vmax'] for r in s], float)
        fv = np.array([r['fc_vmax'] for r in s], float)
        b, rr = _fit(iv, fv)
        bo, ro = _fit(iv, ov)
        print('  +%dh n=%3d | 起报 %.1f | 观测 %.1f (变化 %+5.1f, σ%4.1f) | '
              '模型 %.1f (变化 %+5.1f, σ%4.1f) | 偏差 %+5.1f (|误差| %.1f)'
              % (h, len(s), iv.mean(), ov.mean(), (ov - iv).mean(),
                 (ov - iv).std(), fv.mean(), (fv - iv).mean(), (fv - iv).std(),
                 (fv - ov).mean(), np.abs(fv - ov).mean()))
        print('        回归 模型=f(起报): 斜率 %+.2f  r=%+.2f   |   观测=f(起报): '
              '斜率 %+.2f  r=%+.2f   (斜率→0 = 没有初值记忆, =回归到同一 MPI)'
              % (b, rr, bo, ro))
        print('        分档平均变化量  ΔV(模型) vs ΔV(观测):')
        for lo, hi in BINS:
            m = (iv >= lo) & (iv < hi)
            if m.sum() < 3:
                continue
            print('          %3d-%3dkt n=%3d: 模型 %+6.1f | 观测 %+6.1f'
                  % (lo, hi, int(m.sum()), (fv - iv)[m].mean(),
                     (ov - iv)[m].mean()))


def main():
    ps = sys.argv[1:]
    if not ps:
        print(__doc__)
        return 1
    for p in ps:
        if not os.path.exists(p):
            print('找不到 %s' % p)
            return 1
    data = [load(p) for p in ps]
    if len(data) == 1:
        report(data[0], os.path.basename(ps[0]))
        return 0
    A, B = data[0], data[1]
    ka = {key(r): r for r in A}
    kb = {key(r): r for r in B}
    common = sorted(set(ka) & set(kb))
    print('=== 配对 A/B ===')
    print('A=%s: %d 条 | B=%s: %d 条 | 交集 %d 条'
          % (os.path.basename(ps[0]), len(A), os.path.basename(ps[1]), len(B),
             len(common)))
    if not common:
        print('**没有交集** —— 两次运行的起报样本完全错开, 无法配对(检查参数是否一致)')
        return 2
    print('独有: A %d 条, B %d 条(样本集变化本身就是信息: A 模式提前消亡会掉样本)'
          % (len(set(ka) - set(kb)), len(set(kb) - set(ka))))
    print()
    for h in (24, 48, 72):
        ks = [k for k in common if k[2] == h]
        if not ks:
            continue
        ia = np.array([ka[k]['fc_vmax'] - ka[k]['obs_vmax'] for k in ks], float)
        ib = np.array([kb[k]['fc_vmax'] - kb[k]['obs_vmax'] for k in ks], float)
        ea = np.array([abs(ia[i]) for i in range(len(ks))], float)
        eb = np.array([abs(ib[i]) for i in range(len(ks))], float)
        print('+%dh n=%3d | 平均偏差 %+6.1f -> %+6.1f kt | 平均|误差| %5.1f -> %5.1f'
              % (h, len(ks), ia.mean(), ib.mean(), ea.mean(), eb.mean()))
    print()
    print('=== 各自全样本(含未配对的) ===')
    report(A, 'A ' + os.path.basename(ps[0]))
    report(B, 'B ' + os.path.basename(ps[1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
