# -*- coding: utf-8 -*-
"""强度分布验收: 模型自然生成的风暴 vs 仓库里的**观测最佳路径**。

为什么要它: 之前所有验收都只验"环境场对不对", 没有人验"生成出来的风暴强度分布对不对"。
风切/OHC 换成瞬时口径之后(还统一了集合预报的切变响应), 必须有一把尺子量强度。

观测侧: 复用 tools/train_tc_clim.parse_atcf 解析 typhoon/**/*.dat(ATCF 格式)。
模型侧: 跑一段回放, 收集 sim['tcs'] 的 track。

统计量(逐风暴, 不依赖风暴数, 因为观测文件只是**抽样**):
  · 峰值强度 P50 / P90
  · RI 事件数(24h 内 vmax 增幅 ≥30 kt)
  · ACE(kt², 逐 6 小时累加)
  · 寿命(小时)

用法:
  python tools/validate_intensity.py --obs-basin wp --obs-years 2000-2024 \
      --start "2016-07-01 00" --days 180
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from simulator.simcore import core as V          # noqa: E402


#: **正确的观测参照目录**(用户 2026-09-20 指出): 仓库里的 typhoon/ 是他自己标注的
#: "疯狂假想"集(2025 西太 P50 高达 90kt、还带一条 665kt 的坏记录), 不能当观测。
#: 真正的自评数据在 G:\气象\数据\自评\<年>\<年><洋区>\。
OBS_ROOT = r'G:\气象\数据\自评'


def obs_dir(year, basin='wp'):
    return os.path.join(OBS_ROOT, str(year), '%d%s' % (year, basin))


def obs_storms(basin='wp', y0=2000, y1=2030, tag='', obs_dir_override=None):
    """仓库里的观测最佳路径 → 逐风暴统计。

    目录/文件名有两套命名(bwp162016.dat 与 "2025 03W wp032025(2561).dat"), 所以
    **海区看路径、年份看首条记录的时间戳**, 不再依赖文件名格式(2026-09-20 修:
    原来只认 b??.dat, 于是把 2025wp 那 38 条整季样本全漏了, 只能用 32 条"每年挑一两个"
    的偏样本 —— 那批的峰值 P50 高达 160 kt, 明显是挑过的强台风)。
    """
    from train_tc_clim import parse_atcf          # 复用现成解析
    out = []
    _root = obs_dir_override or (obs_dir(y0 if y0 == y1 else 2025, basin))
    _files = sorted(glob.glob(os.path.join(_root, '*.dat'))) if os.path.isdir(_root) else []
    if not _files:                                 # 兜底: 老的 typhoon/ 目录
        _files = sorted(glob.glob(os.path.join(ROOT, 'typhoon', '**', '*.dat'), recursive=True))
    for f in _files:
        b = os.path.basename(f)
        rel = f.replace(ROOT, '').lower()
        if tag:
            if tag not in rel:
                continue
        elif basin:
            # 海区: 优先看目录名里的 wp / sh / ...; 兜底看 ATCF 文件名
            bsn = ''
            for tok in ('wp', 'sh', 'ep', 'al', 'io'):
                if tok in rel:
                    bsn = tok
                    break
            if bsn != basin:
                continue
        try:
            tr = parse_atcf(f)
        except Exception:
            tr = None
        if not tr or len(tr) < 4:
            continue
        yr = int(str(tr[0][0])[:4])
        if not (y0 <= yr <= y1):
            continue
        v = np.asarray([float(q[3]) for q in tr], float)
        if v.size < 4 or not np.isfinite(v).any():
            continue
        if float(np.nanmax(v)) > 300.0:      # 坏记录(实测 665 kt) —— 不能进统计
            print('  [跳过坏记录] %s 峰值 %.0f kt' % (os.path.basename(f), np.nanmax(v)))
            continue
        out.append(_stats_from_v(v))
    return out


def _stats_from_v(v):
    """由 6 小时间隔的 vmax 序列算统计量。

    RI 用**独立过程**计数(连续满足 24h 增幅≥30kt 的窗口只算一次) —— 原来直接数重叠窗口,
    一个持续增强 4 天的风暴会被数成十几次"RI", 与"RI 率"这个通行指标不是一回事。
    """
    v = np.asarray(v, float)
    ri = 0
    if v.size > 4:
        hit = (v[4:] - v[:-4]) >= 30.0
        ri = int(np.sum(hit & np.r_[True, ~hit[:-1]]))
    has_ri = bool(ri > 0)
    # **标准 ACE**(2026-09-20 修): ACE = 10^-4 * Σ v_max^2, 只累加 v>=34kt 的 6 小时点。
    # 我原来写成 Σ(v-34)^2 —— 那是个"超能量"口径, 比标准值低 2.6 倍, 而且对不同强度
    # 分布压缩程度不同(弱风暴被压得更狠), 用在"模型 vs 观测"上会**系统性高估模型**。
    _vv = v[v >= 34.0]
    ace = float(np.sum(_vv ** 2) / 10000.0)
    return {'peak': float(np.nanmax(v)), 'ri': ri, 'has_ri': has_ri, 'ace': ace,
            'life': float(v.size * 6.0), 'n_pts': int(v.size)}


def sim_storms(start, days, seed=20250801, precision='100ms'):
    """跑一段回放, 收集自然生成的风暴统计。"""
    D_ = None
    try:
        from simulator.simcore import daily as D_
        D_._CACHE.clear()
    except Exception:
        pass
    # 精度档决定 quality(0/1) 与 inner: 400ms 是满细节(1.70 s/模拟天), 100ms 是
    # quality=0(0.63 s/天, 2.7 倍快), 50ms 更快(0.38)。长回放默认用 100ms,
    # 并在输出里写明 —— 两条对比路径必须用**同一个**档, 否则轨迹不可比。
    sim = V.init({'start': start, 'seed': seed, 'envStepH': 6.0,
                  'params': {'precision': precision, 'dataMode': True}})
    n = int(days * 24 / max(1.0, V.step_hours(sim)))
    step_h = V.step_hours(sim)
    for i in range(n):
        V.sim_step(sim, step_h)
        # **进度**: 原来跑完才输出, 卡住了看不出来(2026-09-20 实测被这一条坑了 17 分钟)
        if (i + 1) % max(1, int(24 * 20 / max(1.0, step_h))) == 0:
            alive = sum(1 for t in sim.get('tcs', []) if not t.get('dead'))
            print('  ... 第 %.0f 天, 存活风暴 %d, 累计 %d'
                  % ((i + 1) * step_h / 24.0, alive, len(sim.get('tcs', []))), flush=True)
    out = []
    for tc in sim.get('tcs', []):
        tr = tc.get('track') or []
        if len(tr) < 4:
            continue
        v = np.asarray([float(q['vmax']) for q in tr], float)
        out.append(_stats_from_v(v))
    return out


def _summ(name, rows):
    if not rows:
        print('%-8s 无样本' % name)
        return None
    pk = np.array([r['peak'] for r in rows], float)
    d = {'n': len(rows), 'p50': float(np.percentile(pk, 50)),
         'p90': float(np.percentile(pk, 90)),
         'ri_per': float(np.mean([r['ri'] for r in rows])),
         'ri_frac': float(np.mean([1.0 if r.get('has_ri') else 0.0 for r in rows])),
         'ace': float(np.mean([r['ace'] for r in rows])),
         'life': float(np.mean([r['life'] for r in rows]))}
    print('%-8s n=%-3d 峰值 P50 %5.1f / P90 %5.1f kt | 有RI的风暴 %.0f%% | RI过程/风暴 %.2f | '
          'ACE/风暴 %5.1f | 寿命 %5.0f h'
          % (name, d['n'], d['p50'], d['p90'], 100.0 * d['ri_frac'], d['ri_per'],
             d['ace'], d['life']))
    return d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--obs-basin', default='wp')
    ap.add_argument('--obs-years', default='2000-2030')
    ap.add_argument('--obs-tag', default='', help='只看路径里含该子串的文件, 如 2025wp')
    ap.add_argument('--obs-dir', default=None, help='观测 b-deck 目录(默认 自评/<年>/<年>wp)')
    ap.add_argument('--start', default='2016-07-01 00')
    ap.add_argument('--days', type=float, default=180.0)
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--precision', default='100ms',
                    help='模拟精度档(400ms=满细节最慢, 100ms/50ms 更快, 见 core.PRECISION_TIERS)')
    a = ap.parse_args(argv)
    y0, y1 = [int(x) for x in a.obs_years.split('-')]
    o = obs_storms(a.obs_basin, y0, y1, a.obs_tag, a.obs_dir)
    print('观测: %s 里 %s 最佳路径 %d 条' % (a.obs_dir or obs_dir(y1, a.obs_basin), a.obs_basin, len(o)))
    m = sim_storms(a.start, a.days, a.seed, a.precision)
    print('模型: %s 起 %.0f 天(精度档 %s), 自然生成 %d 个'
          % (a.start, a.days, a.precision, len(m)))
    do = _summ('观测', o)
    dm = _summ('模型', m)
    if do and dm:
        print('\n模型/观测: 峰值 P50 %.2f  P90 %.2f | RI/风暴 %.2f | ACE/风暴 %.2f | 寿命 %.2f'
              % (dm['p50'] / max(1e-9, do['p50']), dm['p90'] / max(1e-9, do['p90']),
                 dm['ri_per'] / max(1e-9, do['ri_per']), dm['ace'] / max(1e-9, do['ace']),
                 dm['life'] / max(1e-9, do['life'])))
        print('(判据建议: 峰值 P50/P90 在 0.8-1.25 之间, RI/风暴 在 0.5-2 倍之间、ACE 在 0.5-2 倍之间)')


if __name__ == '__main__':
    main()
