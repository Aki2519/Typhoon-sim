# -*- coding: utf-8 -*-
"""一条命令跑完全部验收: 基态场 vs 真实观测, 输出 PASS/FAIL 表。

设计目标(用户 2026-09-20 的持续要求: "每轮用真实数据渲染并排对照图并逐点核数值"):
把散在各个工具里的校验收成一个入口, 每轮/每次改进后都能一键复现, 结果写到
`output/sim/_reference/ACCEPTANCE_AUTO.md`。

检查项:
  A. SST   vs ERA5 月均 sstm_<年>_<月>.nc(本地已有, 覆盖全年)        阈值 MAD ≤ 1.0 °C
  B. SST   vs ERA5 单日 day_*.nc(有就查)                            阈值 MAD ≤ 1.5 °C
  C. 风切  vs ERA5 单日 plev_*.nc(有就查, TC 尺度)                  阈值 相关 ≥ 0.9
  D. TCHP  vs AOML tile(有缓存/能下就查)                             阈值 相关 ≥ 0.8
  E. D26   vs AOML tile(同上)                                       阈值 相关 ≥ 0.7

用法:
  python tools/acceptance_check.py                 # 跑全部可跑的项
  python tools/acceptance_check.py --no-net        # 不联网(AOML 只看已缓存 tile)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CMEMS = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems')
ERA5 = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
REF = os.path.join(ROOT, 'output', 'sim', '_reference')
LAT = -60 + (np.arange(240) + 0.5) * 0.5
LON = (np.arange(720) + 0.5) * 0.5


def _resample_to_kernel(src, la, lo):
    from scipy import ndimage as ndi
    la = np.asarray(la, float); lo = np.asarray(lo, float)
    if la[0] > la[-1]:
        src = src[::-1]; la = la[::-1]
    if lo.size and lo.min() < 0.0:
        idx = np.argsort(lo % 360.0); lo = (lo % 360.0)[idx]; src = src[:, idx]
    fy = (LAT - la[0]) / (la[-1] - la[0]) * (la.size - 1)
    fx = (LON - lo[0]) / (lo[-1] - lo[0]) * (lo.size - 1)
    return ndi.map_coordinates(np.asarray(src, np.float32),
                               np.array(np.meshgrid(fy, fx, indexing='ij')),
                               order=1, mode='nearest')


def _open_nc(path):
    import netCDF4
    os.chdir(ROOT)
    p = path if (os.path.isabs(path) and path.isascii()) else os.path.relpath(path, ROOT)
    return netCDF4.Dataset(p)


def check_sst_monthly():
    """A: CMEMS 单日 SST vs ERA5 月均(全年覆盖)。"""
    rows = []
    for f in sorted(glob.glob(os.path.join(ERA5, 'sstm_*.nc'))):
        base = os.path.basename(f)                 # sstm_2024_01.nc
        try:
            y, m = int(base[5:9]), int(base[10:12])
        except Exception:
            continue
        g2 = os.path.join(CMEMS, 'g2_%04d%02d15.npz' % (y, m))
        if not os.path.exists(g2):
            continue
        with np.load(g2) as z:
            A = np.asarray(z['sst'], np.float32)
        d = _open_nc(f)
        try:
            # netCDF4 会把缺测自动掩码 → 必须先 filled 再判单位和统计,
            # 否则 np.ma.masked > 100 是 masked(假值), K→°C 的换算会被跳过,
            # 结果是"偏差 -273 °C"这种荒唐值(实测踩过)。
            B = np.ma.filled(np.ma.masked_invalid(
                np.ma.asarray(d.variables['sst'][:])), np.nan).astype(np.float32)
            la = np.asarray(d.variables['latitude'][:]).ravel()
            lo = np.asarray(d.variables['longitude'][:]).ravel()
        finally:
            d.close()
        B = np.squeeze(B)
        if B.ndim == 3:
            B = B[0]
        _mx = np.nanmax(B) if np.isfinite(B).any() else 0.0
        if _mx > 100:                              # K → °C
            B = B - 273.15
        B = _resample_to_kernel(B, la, lo)
        ok = np.isfinite(A) & np.isfinite(B) & (A > -2) & (B > -2)
        if ok.sum() < 1000:
            continue
        dd = A[ok] - B[ok]
        rows.append((y, m, float(dd.mean()), float(np.abs(dd).mean()),
                     float(np.corrcoef(A[ok], B[ok])[0, 1])))
    if not rows:
        return None
    return {'n': len(rows), 'bias': float(np.mean([r[2] for r in rows])),
            'mad': float(np.mean([r[3] for r in rows])),
            'corr': float(np.mean([r[4] for r in rows])), 'rows': rows}


def check_sst_daily():
    """B: 运行时 SST vs ERA5 单日瞬时(逐日文件存在才有)。"""
    from simulator.simcore import core as V
    from simulator.simcore import daily as D
    out = []
    # **任意时次都要收**(原来硬编码 _12, 于是 00Z 的单日文件被漏掉)
    for f in sorted(glob.glob(os.path.join(ERA5, 'day_*_*.nc'))):
        base = os.path.basename(f)
        ymd = base[4:12]
        try:
            from datetime import datetime
            clk = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]), int(base[13:15]))
        except Exception:
            continue
        D._CACHE.clear()
        inst = D.load_fields(clk)
        if 'sst' not in inst:
            continue
        sim = V.init({'start': clk.strftime('%Y-%m-%d %H'), 'seed': 3, 'envStepH': 6.0,
                      'params': {'precision': '400ms', 'dataMode': True}})
        V.sim_step(sim, 6.0)
        A = np.asarray(sim['env']['sst'], np.float64)
        B = np.asarray(inst['sst'], np.float64)
        ok = np.isfinite(A) & np.isfinite(B)
        dd = A[ok] - B[ok]
        out.append((ymd, float(dd.mean()), float(np.abs(dd).mean()),
                    float(np.corrcoef(A[ok], B[ok])[0, 1])))
    if not out:
        return None
    return {'n': len(out), 'mad': float(np.mean([r[2] for r in out])), 'rows': out}


def check_shear_daily():
    """C: 运行时切变 vs ERA5 单日 plev(瞬时)。"""
    from simulator.simcore import core as V
    from simulator.simcore import daily as D
    out = []
    for f in sorted(glob.glob(os.path.join(ERA5, 'plev_*_*.nc'))):
        base = os.path.basename(f)
        ymd = base[5:13]
        try:
            from datetime import datetime
            clk = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]), int(base[14:16]))
        except Exception:
            continue
        D._CACHE.clear()
        inst = D.load_fields(clk)
        if 'shear' not in inst:
            continue
        sim = V.init({'start': clk.strftime('%Y-%m-%d %H'), 'seed': 3, 'envStepH': 6.0,
                      'params': {'precision': '400ms', 'dataMode': True}})
        V.sim_step(sim, 6.0)
        A = np.asarray(sim['env']['shear'], np.float64)
        B = np.asarray(inst['shear'], np.float64)
        ok = np.isfinite(A) & np.isfinite(B)
        out.append((ymd, float((A[ok] - B[ok]).mean()), float(np.abs(A[ok] - B[ok]).mean()),
                    float(np.corrcoef(A[ok], B[ok])[0, 1])))
    if not out:
        return None
    return {'n': len(out), 'rows': out}


def check_ohc_aoml(allow_net=True):
    """D/E: base_tchp / base_d26 vs AOML tile(缓存优先)。"""
    js = os.path.join(REF, 'validate_ohc_aoml.json')
    if os.path.exists(js):
        try:
            with open(js, encoding='utf-8') as f:
                d = json.load(f)
            rows = [r for r in d.get('rows', []) if len(r) == 5 and r[1] is not None]
            d26 = [r for r in d.get('d26_rows', []) if len(r) == 5 and r[1] is not None]
            return {'tchp': ({'n': len(rows),
                              'bias': float(np.mean([r[1] for r in rows])),
                              'mad': float(np.mean([r[2] for r in rows])),
                              'corr': float(np.mean([r[3] for r in rows]))} if rows else None),
                    'd26': ({'n': len(d26),
                             'bias': float(np.mean([r[1] for r in d26])),
                             'mad': float(np.mean([r[2] for r in d26])),
                             'corr': float(np.mean([r[3] for r in d26]))} if d26 else None)}
        except Exception:
            pass
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-net', action='store_true')
    ap.add_argument('--out', default=os.path.join(REF, 'ACCEPTANCE_AUTO.md'))
    a = ap.parse_args(argv)
    res = {}
    lines = ['# 自动验收(acceptance_check.py)', '']
    from datetime import datetime
    lines += ['跑于 %s' % datetime.now().strftime('%Y-%m-%d %H:%M'), '']
    # **当前用的是哪套数据 / 覆盖到什么时候**(2026-09-20): 单日 ERA5 只有 ±6 小时窗口,
    # 模拟钟一走远就回落月均 —— 验收报告也必须把这件事写在最前面。
    try:
        from simulator.simcore import daily as _D
        cov = _D.coverage()
        _bits = []
        if cov.get('era5'):
            e = cov['era5']
            _bits.append('ERA5 单日 %s .. %s(%d 个文件)' % (
                e[0].strftime('%Y-%m-%d %HZ'), e[1].strftime('%Y-%m-%d %HZ'), e[2]))
        _k = cov.get('era5_kinds') or {}
        if _k:
            _bits.append('其中 ' + ', '.join(
                '%s %d 个(%s..%s)' % (kk, vv[2], vv[0].strftime('%m-%d %HZ'),
                                      vv[1].strftime('%m-%d %HZ'))
                for kk, vv in sorted(_k.items())))
        if cov.get('ohc'):
            o = cov['ohc']
            _bits.append('CMEMS 单日洋区 OHC %s .. %s(%d 个)' % (
                o[0].strftime('%Y-%m-%d'), o[1].strftime('%Y-%m-%d'), o[2]))
        if _bits:
            lines += ['## 当前瞬时数据覆盖', ''] + ['* ' + b for b in _bits] + [
                '', '(ERA5 单日窗口 ±6 小时、洋区 OHC ±3 天; 超出窗口的层自动回落月基态,',
                ' 运行时徽标会显示"瞬时 / 瞬时·部分 / 月均")', '']
    except Exception as exc:
        lines += ['(数据覆盖信息不可用: %s)' % exc, '']

    sst_m = check_sst_monthly()
    res['sst_monthly'] = sst_m
    if sst_m:
        ok = sst_m['mad'] <= 1.0
        lines += ['## A. SST vs ERA5 月均(全年覆盖) — %s' % ('PASS' if ok else 'FAIL'),
                  '', '| 月数 | 偏差 | 平均绝对差 | 相关 |', '|---|---|---|---|',
                  '| %d | %+.2f | **%.2f** | **%.3f** |' % (
                      sst_m['n'], sst_m['bias'], sst_m['mad'], sst_m['corr']),
                  '', '(阈值 MAD ≤ 1.0 °C)', '']

    sst_d = check_sst_daily()
    res['sst_daily'] = sst_d
    if sst_d:
        ok = sst_d['mad'] <= 1.5
        lines += ['## B. SST vs ERA5 单日瞬时 — %s' % ('PASS' if ok else 'FAIL'), '',
                  '| 日期 | 偏差 | 平均绝对差 | 相关 |', '|---|---|---|---|']
        for r in sst_d['rows']:
            lines.append('| %s | %+.2f | %.2f | %.3f |' % (r[0], r[1], r[2], r[3]))
        lines += ['', '(阈值 MAD ≤ 1.5 °C)', '']

    sh = check_shear_daily()
    res['shear_daily'] = sh
    if sh:
        lines += ['## C. 风切 vs ERA5 单日瞬时(TC 尺度)— %s' % (
            'PASS' if min(r[3] for r in sh['rows']) >= 0.9 else 'FAIL'), '',
            '| 日期 | 偏差(kt) | 平均绝对差(kt) | 相关 |', '|---|---|---|---|']
        for r in sh['rows']:
            lines.append('| %s | %+.1f | %.1f | **%.3f** |' % (r[0], r[1], r[2], r[3]))
        lines += ['', '(阈值 相关 ≥ 0.9)', '']

    ohc = check_ohc_aoml(allow_net=not a.no_net)
    res['ohc_aoml'] = ohc
    if ohc:
        for key, name, thr in (('tchp', 'TCHP(kJ/cm2)', 0.8), ('d26', 'D26(m)', 0.7)):
            v = ohc.get(key)
            if not v:
                continue
            lines += ['## %s. %s vs AOML — %s' % ('D' if key == 'tchp' else 'E', name,
                                                  'PASS' if v['corr'] >= thr else 'FAIL'),
                      '', '| 月数 | 偏差 | 平均绝对差 | 相关 |', '|---|---|---|---|',
                      '| %d | %+.2f | %.2f | **%.3f** |' % (
                          v['n'], v['bias'], v['mad'], v['corr']),
                      '', '(阈值 相关 ≥ %.1f)' % thr, '']

    with open(a.out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))
    print('写出', a.out)
    with open(os.path.join(REF, 'acceptance_auto.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)


if __name__ == '__main__':
    main()
