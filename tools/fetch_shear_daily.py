# -*- coding: utf-8 -*-
"""抓 ERA5 **逐日 6 小时 u/v 200hPa + 850hPa**, 自己算 |V200 − V850| 的**每日风切**。

为什么单独做这个脚本(而不是复用 tools/fetch_era5_day.py):
  * fetch_era5_day.py 是"**单日单时次**瞬时场", 一次请求只覆盖 1 个时次, 用来喂
    运行时的 day_/plev_ 缓存。要统计"一年/一个月的每日风切气候", 那个粒度
    要么发 365 次 CDS 请求(排队 13-78 min/次 ⇒ 不可行), 要么就没有。
  * 这里改成**按月一次请求**(同一个月所有日 × 00/06/12/18Z × 200/850 × u/v),
    一次排队拿一整个月, 请求数从 365 降到 12。
  * 产物口径与内核一致: **瞬时 |V200−V850|, 不乘 0.55**(0.55 只用于"月均矢量差"
    那条兜底路径, 见 simulator/simcore/climate.py 与 daily.py 的注释)。
    TC 尺度(高斯 sigma=1.75°, 有效半径 ~380 km, SHIPS/CIRA 口径)平滑另存为
    shear_tc, 方便与运行时 env['shear'] 直接对比。

产物:
  simulator/env/train_cache/shear_daily/_nc/plev_<YYYYMM>_<tag>.nc   原始 CDS netCDF
  simulator/env/train_cache/shear_daily/<YYYY>/<YYYYMMDD>.npz        每日风切
      shear     (ntime, nlat, nlon) float32  kt   —— 逐时次瞬时 |V200−V850|(点值)
      shear_tc  (ntime, nlat, nlon) float32  kt   —— 同上, TC 尺度高斯平滑(可选)
      shear_day (nlat, nlon)         float32  kt   —— 当日各时次平均
      lat, lon  (nlat,) (nlon,)                    —— 源网格(0.25°)
      times     (ntime,) 'YYYYMMDD_HH'
      meta      json 字符串(口径/单位/来源/请求体)

用法:
  # ① 先看请求体(可直接提交给 CDS), 不联网
  python tools/fetch_shear_daily.py --dry-run --year 2025 --month 8
  # ② 真抓(建议后台跑; CDS 排队 13-78 min)
  python tools/fetch_shear_daily.py --year 2025 --month 8 --times 00,06,12,18
  # ③ 抓完核对: 与运行时瞬时风切口径对照
  python tools/fetch_shear_daily.py --report --year 2025 --month 8
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'shear_daily')
NCDIR = os.path.join(OUT, '_nc')
DATASET = 'reanalysis-era5-pressure-levels'
KT = 1.94384
#: TC 尺度平均半径(度), 与 simulator/simcore/daily.py 的 TC_SHEAR_SCALE_DEG 一致
TC_SCALE_DEG = 1.75


# ---------------------------------------------------------------- 请求体
def area_tag(area):
    return 'N%dW%dS%dE%d' % (int(area[0]), int(area[1]), int(area[2]), int(area[3]))


def build_request(year, month, area, times, days=None, levels=('200', '850'),
                  variables=('u_component_of_wind', 'v_component_of_wind')):
    """CDS-Beta 请求体(与 tools/fetch_era5_day.py / cds_client.CDS.retrieve 同构)。

    days=None → 全月 01-31(CDS 会自动忽略该月不存在的日); 给了 days 就只取这些日,
    用于"整月太大"时按天切片(例: 全球 40N-40S 一个整月 ≈ 0.9 GB, 取 5 天 ≈ 0.15 GB)。
    """
    if days:
        dlist = ['%02d' % int(x) for x in days]
    else:
        dlist = ['%02d' % d for d in range(1, 32)]
    return {
        'product_type': ['reanalysis'],
        'variable': list(variables),
        'pressure_level': list(levels),
        'year': ['%04d' % int(year)],
        'month': ['%02d' % int(month)],
        'day': dlist,
        'time': ['%s:00' % t for t in times],
        'data_format': 'netcdf',
        'download_format': 'unarchived',
        'area': [float(area[0]), float(area[1]), float(area[2]), float(area[3])],
    }


# ---------------------------------------------------------------- 读 netCDF
def _rel(p):
    """netCDF4 在 Windows 上按 locale 编码传路径, 中文仓库路径必须用相对路径。"""
    try:
        return os.path.relpath(p, ROOT)
    except Exception:
        return p


def _time_values(d):
    """把 netCDF 的时间坐标读成 'YYYY-MM-DD HH' 字符串列表(兼容数值/字符两种)。"""
    import netCDF4
    name = 'valid_time' if 'valid_time' in d.variables else (
        'time' if 'time' in d.variables else None)
    if name is None:
        return None
    v = d.variables[name]
    raw = v[:]
    if raw.dtype.kind in 'SU':
        s = netCDF4.chartostring(raw).ravel().tolist()
        return [str(x).replace('T', ' ')[:13] for x in s]
    units = getattr(v, 'units', 'hours since 1900-01-01 00:00:00')
    cal = getattr(v, 'calendar', 'standard')
    try:
        dt = netCDF4.num2date(np.asarray(raw).ravel(), units, cal)
        return [t.strftime('%Y-%m-%d %H') for t in dt]
    except Exception:
        return None


def _sel_index(var, tname, lname, ti, li):
    """按**维度名**取索引: CDS 的 netCDF 可能带 expver/number 等多余维度。"""
    idx = []
    for dn in var.dimensions:
        if dn == tname:
            idx.append(ti)
        elif dn == lname:
            idx.append(li)
        else:
            idx.append(slice(None))          # 多余维度(expver=1 之类)整取, 后面 squeeze
    return tuple(idx)


def read_uv(path):
    """→ (times, lat, lon, u200, v200, u850, v850), 场形状 (nt, nlat, nlon)。"""
    import netCDF4
    os.chdir(ROOT)
    with netCDF4.Dataset(_rel(path)) as d:
        lat = np.asarray(d.variables['latitude'][:]).ravel().astype(np.float64)
        lon = np.asarray(d.variables['longitude'][:]).ravel().astype(np.float64)
        lname = 'pressure_level' if 'pressure_level' in d.variables else 'level'
        lv = np.asarray(d.variables[lname][:]).ravel().astype(int)
        tname = 'valid_time' if 'valid_time' in d.variables else 'time'
        times = _time_values(d)
        nt = len(times) if times else None
        got = {}
        for key in ('u', 'v'):
            var = d.variables[key]
            for L in (200, 850):
                k = int(np.argmin(np.abs(lv - L)))
                if abs(int(lv[k]) - L) > 5:
                    raise RuntimeError('netCDF 里没有 %d hPa(只有 %s)' % (L, lv.tolist()))
                a = np.asarray(var[_sel_index(var, tname, lname, slice(None), k)])
                a = a.squeeze()
                if a.ndim == 2:
                    a = a[None]
                got['%s%d' % (key, L)] = a.astype(np.float32)
        if nt is not None:
            for k, a in got.items():
                if a.shape[0] != nt:
                    raise RuntimeError('%s 时间维 %d != 时间坐标 %d' % (k, a.shape[0], nt))
        if times is None:
            times = ['%04d' % i for i in range(got['u200'].shape[0])]
    return times, lat, lon, got['u200'], got['v200'], got['u850'], got['v850']


# ---------------------------------------------------------------- 计算
def shear_from_uv(u200, v200, u850, v850, tc_scale=TC_SCALE_DEG):
    """瞬时 |V200 − V850|(kt) + TC 尺度平滑版本。

    **不乘 0.55** —— 0.55 是"月均矢量差"那条兜底路径的经验补偿(monthly mean 的
    矢量差比日尺度大 ~1.8 倍), 瞬时场本身就是日尺度, 再乘就低 45%。
    """
    du = (np.asarray(u200, np.float32) - np.asarray(u850, np.float32))
    dv = (np.asarray(v200, np.float32) - np.asarray(v850, np.float32))
    out = {'shear': (np.hypot(du, dv) * KT).astype(np.float32)}
    if tc_scale:
        try:
            from scipy import ndimage as ndi
            # 源网格 0.25° → sigma 格数(内核是 0.5° 网格, 这里换算成 0.25° 的格数)
            sg = float(tc_scale) / 0.25
            su = ndi.gaussian_filter(du, sigma=(0, sg, sg),
                                     mode=('nearest', 'nearest', 'wrap'))
            sv = ndi.gaussian_filter(dv, sigma=(0, sg, sg),
                                     mode=('nearest', 'nearest', 'wrap'))
            out['shear_tc'] = (np.hypot(su, sv) * KT).astype(np.float32)
        except Exception as e:
            print('  [warn] TC 尺度平滑失败: %s' % e, flush=True)
    return out


def save_days(times, lat, lon, fields, meta_extra, out_dir=None):
    """按日切分 → <年>/<YYYYMMDD>.npz。返回写出的文件列表。"""
    out_dir = out_dir or OUT
    keys = list(fields)
    days = {}
    for i, t in enumerate(times):
        ymd = t[:10].replace('-', '').replace(' ', '')[:8]
        days.setdefault(ymd, []).append(i)
    written = []
    for ymd, idx in sorted(days.items()):
        d = os.path.join(out_dir, ymd[:4])
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, '%s.npz' % ymd)
        arrs = {}
        for k in keys:
            arrs[k] = fields[k][idx]
        arrs['shear_day'] = np.nanmean(arrs['shear'], axis=0).astype(np.float32)
        meta = dict(meta_extra)
        meta.update({'ymd': ymd, 'ntime': len(idx),
                     'times': [times[i] for i in idx],
                     'units': 'kt', 'shear_def': '|V200-V850| instant, NO 0.55 factor',
                     'levels_hPa': [200, 850], 'source': 'ERA5 ' + DATASET})
        arrs['lat'] = lat.astype(np.float32)
        arrs['lon'] = lon.astype(np.float32)
        arrs['times'] = np.array([times[i] for i in idx])
        arrs['meta'] = np.array(json.dumps(meta, ensure_ascii=False))
        np.savez_compressed(fp, **arrs)
        written.append(fp)
    return written


# ---------------------------------------------------------------- 抓取
def fetch_month(year, month, area, times, days=None, force=False, poll=15, dry_run=False,
                rm_nc=False):
    from cds_client import CDS
    req = build_request(year, month, area, times, days=days)
    tag = area_tag(area)
    if days:
        tag += '_d%s-%s' % (min(days), max(days))
    nc = os.path.join(NCDIR, 'plev_%04d%02d_%s.nc' % (int(year), int(month), tag))
    print('=' * 78, flush=True)
    print('CDS 请求体(%s):' % DATASET, flush=True)
    print(json.dumps(req, ensure_ascii=False, indent=2), flush=True)
    print('落地 netCDF:', nc, flush=True)
    if dry_run:
        return None
    os.makedirs(NCDIR, exist_ok=True)
    ok = os.path.exists(nc) and os.path.getsize(nc) > 5e4
    if ok and not force:
        print('[已缓存] %s %.1f MB' % (os.path.basename(nc), os.path.getsize(nc) / 1e6),
              flush=True)
    else:
        if os.path.exists(nc):
            os.remove(nc)
        c = CDS()
        t0 = time.time()
        try:
            c.retrieve(DATASET, req, nc, poll=poll, verbose=True)
        except Exception as e:
            print('[失败] CDS 抓取失败(排队超时/权限/接口变更): %s' % str(e)[:500], flush=True)
            raise
        print('[下载完成] %.1f MB  用时 %.1f min'
              % (os.path.getsize(nc) / 1e6, (time.time() - t0) / 60), flush=True)
    times_, lat, lon, u200, v200, u850, v850 = read_uv(nc)
    print('[读取] %d 时次  lat %d (%.2f..%.2f)  lon %d (%.2f..%.2f)'
          % (len(times_), lat.size, lat.min(), lat.max(), lon.size, lon.min(), lon.max()),
          flush=True)
    f = shear_from_uv(u200, v200, u850, v850)
    meta = {'request': req, 'nc': os.path.relpath(nc, ROOT), 'area': list(area),
            'tc_scale_deg': TC_SCALE_DEG}
    fs = save_days(times_, lat, lon, f, meta)
    print('[写出] %d 个日文件 → %s' % (len(fs), os.path.join(OUT, str(int(year)))),
          flush=True)
    if rm_nc:                      # 原始 netCDF 只是中间产物, 日 npz 才是成品
        try:
            _sz = os.path.getsize(nc) / 1e6      # 先取大小, 删完再取会 FileNotFound
            os.remove(nc)
            print('[清理] 已删除原始 netCDF %s(省 %.1f MB)'
                  % (os.path.basename(nc), _sz), flush=True)
        except Exception as e:
            print('[清理失败] %s' % e, flush=True)
    return fs


# ---------------------------------------------------------------- 核对
def _q(a, ps=(5, 25, 50, 75, 95)):
    a = np.asarray(a, np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return [float(np.percentile(a, p)) for p in ps]


def report(year, month, area, times):
    """新下载的每日风切 vs 运行时瞬时风切(daily.load_fields)口径对照。"""
    from datetime import datetime
    fs = sorted(glob.glob(os.path.join(OUT, str(int(year)), '%04d%02d*.npz'
                                      % (int(year), int(month)))))
    if not fs:
        print('没有 %04d-%02d 的日文件' % (int(year), int(month)))
        return
    lat = lon = None
    point, tc = [], []
    for fp in fs:
        with np.load(fp, allow_pickle=False) as z:
            if 'shear' not in z:
                continue
            lat = np.asarray(z['lat'], np.float64)
            lon = np.asarray(z['lon'], np.float64)
            point.append(np.asarray(z['shear'], np.float32))
            if 'shear_tc' in z:
                tc.append(np.asarray(z['shear_tc'], np.float32))
    P = np.concatenate(point, 0)
    T = np.concatenate(tc, 0) if tc else None
    bands = {'热带 20S-20N': (np.abs(lat) <= 20), '副热带 20-40N/S': (
        (np.abs(lat) > 20) & (np.abs(lat) <= 40))}
    print('## 新下载 ERA5 每日风切(n=%d 时次, %s)' % (P.shape[0], os.path.basename(fs[0])))
    rows = []
    for bn, m in bands.items():
        mk = np.broadcast_to(m[:, None], P.shape[1:])
        for name, A in (('点值 |V200-V850|', P), ('TC 尺度 1.75°', T)):
            if A is None:
                continue
            sub = A[:, mk]
            q = _q(sub)
            rows.append((bn, name, float(sub.mean()), q))
            print('  %-16s %-18s mean %6.2f kt  p5/p25/p50/p75/p95 = %s'
                  % (bn, name, sub.mean(), ' '.join('%.1f' % x for x in q)))
    return rows


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description='ERA5 逐日 6h u/v 200/850 → 每日风切')
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--month', type=int, required=True)
    ap.add_argument('--area', default='60,0,-60,360',
                    help='N,W,S,E(默认 60,0,-60,360 与仓库其它 ERA5 缓存一致)')
    ap.add_argument('--times', default='00,06,12,18')
    ap.add_argument('--days', default='', help='逗号分隔的日(默认整月), 如 01,02,03')
    ap.add_argument('--dry-run', action='store_true', help='只打印请求体, 不联网')
    ap.add_argument('--force', action='store_true', help='忽略已有 netCDF 重下')
    ap.add_argument('--report', action='store_true', help='只做口径对照(读已下载的 npz)')
    ap.add_argument('--poll', type=int, default=15)
    ap.add_argument('--rm-nc', action='store_true',
                    help='处理完删掉原始 netCDF(一个全球 40S-40N 整月 ≈ 460 MB)')
    a = ap.parse_args(argv)
    area = [float(x) for x in a.area.split(',')]
    times = [t.strip().zfill(2) for t in a.times.split(',') if t.strip()]
    days = [int(x) for x in a.days.split(',') if x.strip()] or None
    if a.report:
        report(a.year, a.month, area, times)
        return
    fetch_month(a.year, a.month, area, times, days=days, force=a.force, poll=a.poll,
                dry_run=a.dry_run, rm_nc=a.rm_nc)
    if not a.dry_run:
        print('DONE', flush=True)


if __name__ == '__main__':
    main()
