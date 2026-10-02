# -*- coding: utf-8 -*-
"""用**已校验**的 CMEMS 单日场(g2_*.npz)重建 base_d26 / base_tchp。

设计要点(2026-09-20 第 4 轮):
  * 只吃 `cmems/g2_<YYYYMMDD>.npz` —— 那是 `tools/build_ohc_from_cmems.py` 的产物,
    写盘前**必须**过"同日 ERA5 瞬时 SST 平均 |Δ| ≤ 1.5 °C"的门控(meta 里记 verdict);
  * 缺月用**最近的可用月**填, 并把 `months_src` 写进 meta(诚实标注哪些月是插补的),
    下一轮抓到该月的单日场再替换;
  * 陆地格用最近有效格填充; 结果写 `base_d26.npz` / `base_tchp.npz`, 旧文件先备份。

用法:
  python tools/build_base_d26_validated.py            # 组装 + 报告
  python tools/build_base_d26_validated.py --check    # 只报告现有 g2_* 与基态状态
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')
CMEMS = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
BACKUP = os.path.join(MODEL, '_bak_20260920')
CELL_NPZ = os.path.join(MODEL, 'land_cell.npz')
ENVNY, ENVNX = 240, 720
#: D26 的系统偏差修正(米, **加**到 CMEMS 上)。依据: 2026-09-20 与 AOML(卫星+Argo)
#: 12 个月逐格比, bias = AOML - CMEMS = **+14.6 m**(除 2 月外每月 +7..+12 m, MAD 13-15 m,
#: 相关 0.86-0.945) —— 也就是 CMEMS 的 26 度等温线**偏浅**(负号搞反会把偏差从 +14.6 推到
#: +23.5, 实测踩过)。内核 MPI 用 D26(米)当热含量输入, 偏浅会让潜在强度偏低,
#: 所以按独立观测把这个偏差补回去(取 9 m, 留一点保守余量)。
#: TCHP(积分量)偏差只有 -2.4 kJ/cm2, **不修正**。
D26_BIAS_M = 9.0


def _days():
    """{月: (日期, 文件, verdict)} —— 每月取一天(优先已 PASS 的)。"""
    out = {}
    for f in sorted(os.listdir(CMEMS)):
        if not f.startswith('g2_') or not f.endswith('.npz'):
            continue
        ymd = f[3:11]
        m = int(ymd[4:6])
        try:
            with np.load(os.path.join(CMEMS, f)) as z:
                meta = json.loads(str(z['meta'])) if 'meta' in z else {}
        except Exception:
            meta = {}
        v = meta.get('validate', '?')
        cur = out.get(m)
        # PASS 优先于 unverified
        rank = {'PASS': 2, 'unverified': 1}.get(v, 0)
        if cur is None or rank > cur[3]:
            out[m] = (ymd, os.path.join(CMEMS, f), v, rank)
    return out


def _fill_land(a, warm=None, despike=True, zero_med=None):
    """陆地/缺测格用**平滑**方式补(2026-09-21: 原来用最近有效值, 会跨陆地跳变)。

    2026-09-21 二次修正: despike=True 时把**孤立尖峰**也当洞补掉。实测 g2_20240915 的
    (2.0S,153.2E) 格是 14.1 kJ/cm², 邻格 187~223, 同格 SST 30.8°C —— 源数据单格坏值,
    原来是"0=洞"规则抓不到的, 留成 194.5 的相邻跳变(用户报的"跳变/极严重问题")。
    """
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from simulator.simcore.fill import ocean_fill
    lf = None
    try:
        with np.load(CELL_NPZ) as z:
            lf = np.asarray(z['landfrac'], np.float32)
    except Exception:
        lf = None
    return ocean_fill(a, warm=warm, land=lf, despike=despike, zero_med=zero_med)


def build():
    days = _days()
    if not days:
        print('没有 g2_*.npz 可用 —— 先跑 tools/build_ohc_from_cmems.py')
        return None
    print('可用单日场: %s' % ', '.join(
        '%d月(%s,%s)' % (m, days[m][0], days[m][2]) for m in sorted(days)))
    d26 = np.zeros((12, ENVNY, ENVNX), np.float32)
    tchp = np.zeros((12, ENVNY, ENVNX), np.float32)
    src = {}
    have = sorted(days)
    for m in range(1, 13):
        # 最近可用月(月份环状距离)
        best = min(have, key=lambda x: min(abs(x - m), 12 - abs(x - m)))
        ymd, path, verdict, _ = days[best]
        with np.load(path) as z:
            _sst = np.asarray(z['sst'], np.float32) if 'sst' in z else None
            # zero_med: 孤立 0 洞(源文件写坏)判据 —— 实测东北太平洋 3-10N 有 9% 格是 0
            # 却被 90~140 的邻格包着, 而它们的 SST 只有 24.6~26.1(卡在 26°C 定义线附近)
            d26[m - 1] = (_fill_land(np.asarray(z['d26'], np.float32), warm=_sst,
                                     zero_med=45.0) + D26_BIAS_M).astype(np.float32)
            tchp[m - 1] = _fill_land(np.asarray(z['ohc'], np.float32), warm=_sst,
                                     zero_med=40.0)
        src[m] = ('%s(%s)%s' % (ymd, verdict,
                               '' if best == m else ' ← %d月' % best))
    meta = {'source': 'CMEMS GLORYS 单日 thetao → D26/OHC(g2_*.npz)',
            'd26_bias_correction_m': D26_BIAS_M,
            'definition': 'D26=26°C 等温线深度(m); TCHP=ρcp∫max(T-26,0)dz (kJ/cm²)',
            'months_src': src,
            'note': '缺月用最近可用月插补, 标了 ←; 抓到该月单日场后重跑本工具即可替换'}
    os.makedirs(BACKUP, exist_ok=True)
    for nm, arr, key in (('base_d26', d26, 'd26'), ('base_tchp', tchp, 'tchp')):
        dst = os.path.join(MODEL, nm + '.npz')
        if os.path.exists(dst):
            shutil.copy2(dst, os.path.join(BACKUP, nm + '.npz'))
        np.savez_compressed(dst, meta=json.dumps(meta, ensure_ascii=False), **{key: arr})
        print('写出 %s  %s  %.1f..%.1f' % (dst, arr.shape, arr.min(), arr.max()))
    return meta


def check():
    days = _days()
    print('g2_* 单日场 %d 个: %s' % (len(days), ', '.join(
        '%d月:%s' % (m, days[m][2]) for m in sorted(days))))
    for nm, key in (('base_d26', 'd26'), ('base_tchp', 'tchp')):
        p = os.path.join(MODEL, nm + '.npz')
        if not os.path.exists(p):
            print(nm, '不存在'); continue
        with np.load(p) as z:
            a = np.asarray(z[key])
            meta = json.loads(str(z['meta'])) if 'meta' in z else {}
        print('%-10s %s  %.1f..%.1f 均值 %.1f | 来源 %s'
              % (nm, a.shape, a.min(), a.max(), a.mean(),
                 meta.get('source', '?')[:40]))
        ms = meta.get('months_src')
        if ms:
            filled = [int(k) for k, v in ms.items() if '←' in v]
            if filled:
                print('           插补月: %s' % sorted(filled))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    if a.build:
        build()
    if a.check or not a.build:
        check()


if __name__ == '__main__':
    main()
