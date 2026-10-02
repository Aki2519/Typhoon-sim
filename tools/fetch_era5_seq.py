# -*- coding: utf-8 -*-
"""F2/F3 ERA5 串行取数: 一次只提交/等待一个请求, 下载完再提下一个。

为什么不用 fetch_era5_cds.py 的批量模式: 实测 CDS-Beta 对**密集提交**会返回
rejected(整批 36 个请求全废, 连最初成功过的请求形式也一样), 冷却约 1 小时后
才恢复。串行 + 完成后立刻下载, 把在队请求数压到 1, 最稳。

用法:
  python tools/fetch_era5_seq.py --list          # 看计划与进度
  python tools/fetch_era5_seq.py                 # 跑(可后台)
  python tools/fetch_era5_seq.py --only sst      # 只跑某一组
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cds_client import CDS                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
STATE = os.path.join(OUT, 'seq_state.json')
AREA = [60, 0, -60, 360]
YEARS = list(range(1991, 2021))
CHUNK = 5

SURF = 'reanalysis-era5-single-levels-monthly-means'
PLEV = 'reanalysis-era5-pressure-levels-monthly-means'


#: 单请求字段数上限: 实测 CDS 对象存储的下载在 2-4 MB 处断流(48 MB 的 5 年
#: 块下不到 10%, curl 续传也救不回来), 而单月(0.25°, 60S-60N)是 801 KB 稳定通过。
#: 所以 SST 改"逐月"取一个近年 —— 12 个 ~800KB 请求, 比 5 年块可靠得多。
#: 基态气候态取哪些年 —— 关键: **必须跨 ENSO 状态**, 否则单年会把那一年的
#: ENSO 状态烤进"气候态"(实测: 用 2024(发展中拉尼娜)当基态, EPAC 赤道比
#: 2026-09 的观测冷 2.8°C)。这里挑的 8 年平均 ONI 接近 0:
#:   2015 强厄尔尼诺(+2.6) / 2016 弱拉尼娜 / 2018 弱拉尼娜 / 2019 中性
#:   2020 拉尼娜 / 2021 拉尼娜 / 2022 拉尼娜 / 2023 强厄尔尼诺(+2.0)
SST_MONTH_YEARS = (2015, 2016, 2018, 2019, 2020, 2021, 2022, 2023)


#: 只取某几个月(由 --months 指定, 空 = 全年 12 个月)
SST_MONTHS = ()


def plan_sst_months():
    jobs = []
    for y in SST_MONTH_YEARS:
        for m in (SST_MONTHS or tuple(range(1, 13))):
            jobs.append(('sst', 'sstm_%d_%02d' % (y, m), SURF,
                         'sea_surface_temperature', None,
                         [str(y)], ['%02d' % m]))
    return jobs


def plan():
    """(组名, 任务名, 数据集, 变量, 层次, 年份块)。"""
    jobs = []
    for y0 in range(YEARS[0], YEARS[-1] + 1, CHUNK):
        ys = [str(y) for y in range(y0, min(y0 + CHUNK, YEARS[-1] + 1))]
        jobs.append(('sst', 'sst_%d' % y0, SURF,
                     'sea_surface_temperature', None, ys))
    jobs = plan_sst_months() + jobs
    for y0 in range(YEARS[0], YEARS[-1] + 1, CHUNK):
        ys = [str(y) for y in range(y0, min(y0 + CHUNK, YEARS[-1] + 1))]
        for grp, var, lev in (('wind', 'u_component_of_wind', ['850', '200']),
                              ('wind', 'v_component_of_wind', ['850', '200']),
                              ('other', 'mean_sea_level_pressure', None),
                              ('other', 'relative_humidity', ['700']),
                              ('other', 'temperature', ['200'])):
            jobs.append((grp, '%s_%d' % (var.split('_')[0][:6], y0), PLEV,
                         var, lev, ys))
    return jobs


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(s):
    os.makedirs(OUT, exist_ok=True)
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default=None)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--gap', type=float, default=20.0)
    ap.add_argument('--years', default=None,
                    help='逗号分隔的年份(覆盖 SST_MONTH_YEARS)')
    ap.add_argument('--months', default=None,
                    help='逗号分隔的月份(只取这几个月)')
    a = ap.parse_args()
    global SST_MONTH_YEARS, SST_MONTHS
    if a.years:
        SST_MONTH_YEARS = tuple(int(x) for x in a.years.split(','))
    if a.months:
        SST_MONTHS = tuple(int(x) for x in a.months.split(','))
    jobs = plan()
    st = load_state()
    if a.list:
        for grp, nm, ds, var, lev, ys in jobs:
            f = os.path.join(OUT, nm + '.nc')
            mark = 'OK ' if (os.path.exists(f) and os.path.getsize(f) > 1e5) \
                else (st.get(nm, '(未提交)') and 'queued')
            print('%-6s %-22s %s' % (grp, nm, mark))
        return

    c = CDS()
    if a.only == 'sstm':
        jobs = plan_sst_months()
    for grp, nm, ds, var, lev, ys, *rest in [
            (j[0], j[1], j[2], j[3], j[4], j[5],
             (j[6] if len(j) > 6 else None)) for j in jobs]:
        if a.only and a.only != 'sstm' and grp != a.only:
            continue
        months = rest[0]
        f = os.path.join(OUT, nm + '.nc')
        if os.path.exists(f) and os.path.getsize(f) > 1e5:
            continue
        pid = st.get(nm)
        req = {'product_type': ['monthly_averaged_reanalysis'],
               'variable': [var], 'year': ys,
               'month': months or ['%02d' % m for m in range(1, 13)],
               'time': ['00:00'], 'data_format': 'netcdf',
               'download_format': 'unarchived', 'area': AREA}
        if lev:
            req['pressure_level'] = lev
        if not pid:
            for k in range(4):
                try:
                    pid = c.submit(ds, req)
                    st[nm] = pid
                    save_state(st)
                    print('[提交] %-22s -> %s' % (nm, pid), flush=True)
                    break
                except Exception as e:
                    print('[提交失败] %s (%d/4) %s' % (nm, k + 1, str(e)[:100]),
                          flush=True)
                    time.sleep(30)
            if not pid:
                continue
        # 等它完成(单任务超时 3h)
        ok = False
        t0 = time.time()
        while time.time() - t0 < 3 * 3600:
            try:
                s = (c.status(pid).get('status') or '').lower()
            except Exception:
                time.sleep(30)
                continue
            if s in ('completed', 'successful', 'finished'):
                ok = True
                break
            if s in ('failed', 'dismissed', 'rejected'):
                print('[失败] %-22s %s' % (nm, s), flush=True)
                st.pop(nm, None)
                save_state(st)
                break
            time.sleep(30)
        if not ok:
            continue
        try:
            c.download(pid, f)
            sz = os.path.getsize(f)
            print('[完成] %-22s %.1f MB' % (nm, sz / 1e6), flush=True)
            if sz < 1e5:
                os.remove(f)
                st.pop(nm, None)
                save_state(st)
        except Exception as e:
            print('[下载失败] %s %s' % (nm, str(e)[:110]), flush=True)
        time.sleep(a.gap)
    print('全部完成', flush=True)


if __name__ == '__main__':
    main()
