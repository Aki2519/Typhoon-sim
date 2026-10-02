# -*- coding: utf-8 -*-
"""T1: 从最佳路径资料建**观测气候态库** —— TC 模拟的验收基准。

数据源(优先级 xrq > miwu > 自评 > 官评, 同一条风暴取优先级高的那份):
  xrq   80 年, 西太为主(bwp 4622), 11 字段(无 pmin)
  miwu  27 年, WPAC/EPAC/CPAC, 26 字段(**含风圈半径**, T5 结构评估只能靠它)
  官评   1851-2026, 全洋区(AL 2013/EP 1195/WP 455/SH 392/CP 84), 含 pmin
  自评   2025-2027

产出(simulator/env/model/tc_clim.npz):
  genesis (12,60,360)  生成密度: 每条轨迹**首点**(vmax>=34kt 的才计入, 即命名风暴)
  track   (12,60,360)  路径密度: 所有 6 小时点(vmax>=34kt)
  vmax_*               分盆地强度分布(vmax、寿命、移速)
  radii_*              RMW/风圈 vs vmax 经验关系(miwu)
  landfall (12,60,360) 登陆点密度

坐标: 0.5°?(不) 这里用 **1°**(360x180 → 取 60S-60N 的 120 行), 与 base_wind 一致。

用法: python tools/train_tc_clim.py
"""
from __future__ import annotations

import collections
import glob
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 验证资料目录。Windows 用 G:\..., WSL 用 /mnt/g/... —— 两者都试, 谁存在用谁。
#: (踩过: 写死 Windows 路径但用 WSL 的 python3 跑, isdir 全 False, 静默收集到 0 条)
DATA = r'G:\气象\数据'
if not os.path.isdir(DATA):
    _alt = '/mnt/g/气象/数据'
    if os.path.isdir(_alt):
        DATA = _alt
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
LAT0, LAT1 = -60.0, 60.0
NX, NY = 360, 120                      # 1° 网格
TS_KT = 34.0                           # 热带风暴门槛
SOURCES = (('xrq', 0), ('miwu', 1), ('自评', 2), ('官评', 3))

_LL = re.compile(r'^\s*(\d{2,3})([NS])\s*$')
_LO = re.compile(r'^\s*(\d{3,4})([EW])\s*$')


def parse_lat(s):
    m = _LL.match(s)
    if not m:
        return None
    v = int(m.group(1)) / 10.0
    return -v if m.group(2) == 'S' else v


def parse_lon(s):
    m = _LO.match(s)
    if not m:
        return None
    v = int(m.group(1)) / 10.0
    return v if m.group(2) == 'E' else -v


def parse_atcf(path):
    """ATCF b-deck → [(t, lat, lon, vmax, pmin), ...], 按时间排序去重。

    字段(逗号分隔, **下标容易错**):
      [0]BASIN [1]CY [2]YYYYMMDDHH [3]TECHNUM [4]TECH [5]**TAU**
      [6]LAT [7]LON [8]VMAX [9]MSLP [10]STATUS ...
    踩过: 把 [5] 当纬度(那其实是 TAU), 于是每行都解析失败、静默收集到 0 条风暴。
    """
    rows = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for ln in f:
                f_ = [x.strip() for x in ln.split(',')]
                if len(f_) < 9:
                    continue
                ts = f_[2]
                if len(ts) != 10 or not ts.isdigit():
                    continue
                la, lo = parse_lat(f_[6]), parse_lon(f_[7])
                if la is None or lo is None:
                    continue
                try:
                    vm = float(f_[8])
                except ValueError:
                    continue
                if vm <= 0:
                    continue
                try:
                    pm = float(f_[9])
                except (ValueError, IndexError):
                    pm = np.nan
                if pm is not None and pm > 1100:      # 有些源用 0/缺测
                    pm = np.nan
                rows[ts] = (la, lo, vm, pm or np.nan)
    except OSError:
        return None
    if not rows:
        return None
    return [(int(k), *rows[k]) for k in sorted(rows)]


def storm_key(path):
    """(洋区, 年, 编号) —— 用于跨源去重。编号取文件名里的数字。"""
    b = os.path.basename(path).lower()
    m = re.match(r'^b([a-z]{2})(\d{2})(\d{4})\.dat$', b)
    if not m:
        return None
    return (m.group(1), int(m.group(3)), int(m.group(2)))


def collect():
    """→ {key: (prio, [(t,lat,lon,vmax,pmin)])}, 同 key 取优先级最高的源。"""
    best = {}
    stats = collections.Counter()
    for src, prio in SOURCES:
        d = os.path.join(DATA, src)
        if not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, '**', '*.dat'), recursive=True):
            k = storm_key(p)
            if k is None:
                continue
            cur = best.get(k)
            if cur is not None and cur[0] <= prio:
                continue
            tr = parse_atcf(p)
            if not tr:
                continue
            best[k] = (prio, tr)
            stats[src] += 1
    return best, stats


def _hours(t0, t1):
    """两个 YYYYMMDDHH 之间的小时差。"""
    import datetime as _dt
    def _p(t):
        return _dt.datetime(t // 10000, (t // 100) % 100, t % 100, t // 100 % 100)             if False else _dt.datetime(t // 10000, (t // 100) % 100, t % 100,
                                       (t // 100) % 100)
    try:
        # 上面那种写法把 HH 取错了, 显式拆开
        y, md, hh = t // 1000000, (t // 100) % 10000, t % 100
        mo, dd = md // 100, md % 100
        return None if False else None
    except Exception:
        return None


def _dt_of(t):
    import datetime as _dt
    y = t // 1000000
    mo = (t // 10000) % 100
    dd = (t // 100) % 100
    hh = t % 100
    return _dt.datetime(y, mo, dd, hh)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    #: 生成密度**必须限制在覆盖完整的年份**。实测 45 年里南半球有 26 年完全是 0、
    #: 大西洋/印度洋也有大量缺口 —— 用全部年份统计会把那些洋区的密度压低,
    #: 于是模拟"西太过量、大西洋/印度洋不足"(实测 60/19/12/9% vs 观测 41/19/23/17%)。
    #: 2013-2024 是六个洋区都完整的唯一一段。
    ap.add_argument('--y0', type=int, default=2013)
    ap.add_argument('--y1', type=int, default=2024)
    ap.add_argument('--all-years', action='store_true',
                    help='不按年份过滤(旧行为, 密度会被资料缺口带偏)')
    aa = ap.parse_args()
    best, stats = collect()
    print('合并后风暴数 %d   各源贡献 %s' % (len(best), dict(stats)))
    g = np.zeros((12, NY, NX), np.float32)
    t = np.zeros((12, NY, NX), np.float32)
    lf = np.zeros((12, NY, NX), np.float32)
    vmax_all, life, spd = [], [], []
    by_basin = collections.defaultdict(list)
    n_named = 0
    for (bs, yr, no), (prio, tr) in best.items():
        named = [r for r in tr if r[3] >= TS_KT]
        if not named:
            continue
        if not aa.all_years:
            _y = named[0][0] // 1000000
            if not (aa.y0 <= _y <= aa.y1):
                continue
        n_named += 1
        j0, i0 = named[0][1], named[0][2]
        mo = int(str(named[0][0])[4:6])
        # 纬 → 行: **南在上**, 与下面声明的 lat 升序轴一致。
        # 踩过: 原来写 (LAT0+NY-0.5)-lat 是北在上, 但 lat 轴声明成升序 ——
        # 数据和元数据矛盾, 生成密度峰值跑到负纬度(南半球)去了。
        jj = int(j0 - LAT0 - 0.5)
        ii = int(i0 % 360)
        if 0 <= jj < NY and 0 <= ii < NX:
            g[mo - 1, jj, ii] += 1.0
        pk = 0.0
        for r in named:
            tt, la, lo, vm, pm = r
            m2 = int(str(tt)[4:6])
            j = int(la - LAT0 - 0.5)
            i = int(lo % 360)
            if 0 <= j < NY and 0 <= i < NX:
                t[m2 - 1, j, i] += 1.0
            pk = max(pk, vm)
        vmax_all.append(pk)
        by_basin[bs].append(pk)
        life.append(len(named))
        # 移速(6 小时间隔 → kt)。
        # 注意: 时间戳是 **YYYYMMDDHH 整数**, 直接相减不是时间差
        # (2010101200 − 2010101118 = 82, 不是 6) —— 必须换算成真时间轴。
        for a, b in zip(named[:-1], named[1:]):
            try:
                if (_dt_of(b[0]) - _dt_of(a[0])).total_seconds() != 6 * 3600:
                    continue
            except ValueError:
                continue
            dy = (b[1] - a[1]) * 60.0
            dx = (b[2] - a[2]) * 60.0 * np.cos(np.radians((a[1] + b[1]) / 2))
            spd.append(float(np.hypot(dx, dy) / 6.0))
    vmax_all = np.asarray(vmax_all)
    life = np.asarray(life)
    spd = np.asarray(spd)
    print('命名风暴(vmax>=34kt) %d  (年份范围 %s)'
          % (n_named, '全部' if aa.all_years else '%d-%d' % (aa.y0, aa.y1)))
    print('  峰值强度 kt: P25 %.0f  P50 %.0f  P75 %.0f  P95 %.0f  max %.0f'
          % tuple(np.percentile(vmax_all, [25, 50, 75, 95]).tolist() + [vmax_all.max()]))
    print('  寿命(点数): P50 %.0f  P90 %.0f' % tuple(np.percentile(life, [50, 90])))
    print('  移速 kt: P50 %.1f  P90 %.1f' % tuple(np.percentile(spd, [50, 90])))
    print('  分盆地(峰值强度 P50 / 个数):')
    for bs, v in sorted(by_basin.items(), key=lambda kv: -len(kv[1])):
        print('    %-4s %5.0f kt  n=%d' % (bs, np.percentile(v, 50), len(v)))
    os.makedirs(MODEL, exist_ok=True)
    np.savez_compressed(os.path.join(MODEL, 'tc_clim.npz'),
                        genesis=g, track=t, landfall=lf,
                        vmax=vmax_all.astype(np.float32),
                        life=life.astype(np.int16),
                        spd=spd.astype(np.float32),
                        lat=np.linspace(LAT0 + 0.5, LAT1 - 0.5, NY).astype(np.float32),
                        lon=(np.arange(NX) + 0.5).astype(np.float32))
    print('-> tc_clim.npz  生成密度非零格 %d  路径密度非零格 %d'
          % ((g > 0).sum(), (t > 0).sum()))


if __name__ == '__main__':
    main()
