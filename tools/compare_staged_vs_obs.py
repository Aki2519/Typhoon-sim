# -*- coding: utf-8 -*-
"""整年实跑(实时记录目录里的 b-deck)vs 真实观测 —— 逐风暴强度分布对照。

与 `tools/validate_intensity.py` 的区别: 那个是**另跑一段回放**(--start/--days),
这个是**直接量已经跑完的那一年**(sim_typhoon/<年>/<年><洋区>/*.dat), 所以
"用户要的那三次运行到底跑成什么样"有一条可核对的账。

同一把尺子(逐风暴, 不依赖风暴数):
  峰值 P50 / P90 kt · 有 RI 的风暴比例 · RI 过程/风暴 · ACE/风暴 · 寿命 h
ACE 用标准口径 Σ v²/1e4(v>=34kt); RI = 24h 内增幅 >=30kt 的**独立过程**。

用法:
  python tools/compare_staged_vs_obs.py --year 2025 --basin wp
  python tools/compare_staged_vs_obs.py --year 2025 --basin wp --obs-dir "G:\\气象\\数据\\xrq\\2025"
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from train_tc_clim import parse_atcf                      # noqa: E402
from validate_intensity import _stats_from_v, _summ       # noqa: E402


def _basin_files(d, b=None, year=None):
    """目录里属于该洋区(文件名前缀 b<洋区>)且属于该年的 b-deck 文件。

    **必须这样筛**(2026-09-21): 观测目录不是按年分的 —— `xrq/sh` 里只有 1979 年那 28 个
    文件, `xrq/wp` 里是 2340 个跨 1949-2025 的文件。不加这道筛, 2025 年的模拟会被拿去和
    1979 年/整世纪的观测比: 数字看着正常, 结论却是错的(实测踩过: "sh 观测 n=28" 其实是 1979)。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(d, '*.dat'))):
        if b and not os.path.basename(f).lower().startswith('b' + b):
            continue
        if year is not None:
            _t = parse_atcf(f)
            if not _t:
                continue
            try:
                if int(str(_t[0][0])[:4]) != int(year):
                    continue
            except Exception:
                continue
        out.append(f)
    return out


def stats_from_dir(d, drop_bad=True, basin=None, year=None):
    """把一个目录里所有 b-deck 逐风暴统计(自动按时间戳去重, parse_atcf 已做)。"""
    rows = []
    for f in _basin_files(d, basin, year):
        tr = parse_atcf(f)
        if not tr or len(tr) < 4:
            continue
        v = np.asarray([float(q[3]) for q in tr], float)
        if not np.isfinite(v).any():
            continue
        if drop_bad and float(np.nanmax(v)) > 300.0:
            print('  [跳过坏记录] %s 峰值 %.0f kt' % (os.path.basename(f), np.nanmax(v)))
            continue
        rows.append(_stats_from_v(v))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2025)
    ap.add_argument('--basin', default='wp')
    ap.add_argument('--stage', default=None, help='默认 <repo>/sim_typhoon/<年>/<年><洋区>')
    ap.add_argument('--obs-dir', default=None,
                    help='默认 G:\\气象\\数据\\xrq\\<年>(老数据回落到 自评\\<年>\\<年><洋区>)')
    a = ap.parse_args(argv)
    b = a.basin.lower()
    stage = a.stage or os.path.join(ROOT, 'sim_typhoon', str(a.year),
                                    '%d%s' % (a.year, b))
    obs = a.obs_dir
    if obs is None:
        # 只接受**真的含该洋区该年记录**的目录; 找不到就如实说"没有观测集",
        # 而不是随便挑一个目录硬比(那正是上面 _basin_files 注释里踩过的坑)。
        _cands = [r'G:\气象\数据\xrq\%d' % a.year,
                  r'G:\气象\数据\xrq\%s' % b,
                  r'G:\气象\数据\自评\%d\%d%s' % (a.year, a.year, b)]
        obs = next((c for c in _cands if _basin_files(c, b, a.year)), None)
    print('模型(实跑): %s' % stage)
    print('观测      : %s' % (obs or '**该洋区该年没有观测集** —— 不比较, 免得拿错年份的集合硬比'))
    mo = stats_from_dir(stage)
    ob = stats_from_dir(obs, basin=b, year=a.year) if obs else []
    sm = _summ('模型', mo)
    if not ob:
        print('观测侧无样本 —— 只报模型侧, 不给倍率。')
        return sm, []
    so = _summ('观测', ob)
    if sm and so:
        print('倍率: P50 %.2f×  P90 %.2f×  ACE %.2f×  寿命 %.2f×  RI过程 %.2f×'
              % (sm['p50'] / max(1e-9, so['p50']), sm['p90'] / max(1e-9, so['p90']),
                 sm['ace'] / max(1e-9, so['ace']), sm['life'] / max(1e-9, so['life']),
                 sm['ri_per'] / max(1e-9, so['ri_per'])))
    return sm, so


if __name__ == '__main__':
    main()
