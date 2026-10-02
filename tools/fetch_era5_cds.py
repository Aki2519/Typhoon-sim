# -*- coding: utf-8 -*-
"""F2 主数据源: CDS ERA5 月均(0.25°, 1991-2020 气候态)。

为什么不用 PSL: fileServer 限速(46MB 要 40 分钟)、dodsC 大范围切片会挂起、
NCSS 时好时坏。CDS 一次请求就能拿整段年份的多变量月均, 而且 ERA5 0.25° 比
NCEP R1 的 2.5° 好一个量级 —— 风切/急流结构差在这。

请求分块(按 10 年)以免单次产物过大:
  单层: sea_surface_temperature, mean_sea_level_pressure
  气压层: u/v @ 850/500/300/200, relative_humidity @ 700, temperature @ 200

用法:
  python tools/fetch_era5_cds.py --stage submit     # 提交全部任务(不等)
  python tools/fetch_era5_cds.py --stage wait       # 等待并下载
  python tools/fetch_era5_cds.py --stage all
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
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
JOBS = os.path.join(CACHE, 'jobs.json')
AREA = [60, 0, -60, 360]           # N, W, S, E
YEARS = list(range(1991, 2021))
MONTHS = ['%02d' % m for m in range(1, 13)]
CHUNK = 5      # 单请求字段数上限不稳定(实测 60 通过/96 被拒), 取 5 年=60 场
SUBMIT_GAP = 25  # 提交间隔(秒): CDS 对密集提交会返回 rejected(像速率限制)

SURF_VARS = ['sea_surface_temperature', 'mean_sea_level_pressure']
#: 气压层: 变量 → 需要的层(不同变量层不同, 正好一变量一请求)
PLEV_SPEC = {
    'u_component_of_wind': ['850', '500', '300', '200'],
    'v_component_of_wind': ['850', '500', '300', '200'],
    'relative_humidity': ['700'],
    'temperature': ['200'],
}


def plan():
    """每变量一个请求。

    实测 CDS-Beta **单次请求不接受多变量**(`variable` 给两个就 rejected,
    单变量 + 多变量年/月都正常)。所以按变量拆开 —— 3 段 × (2 单层 + 4 气压层)
    = 18 个请求。
    """
    out = []
    for y0 in range(YEARS[0], YEARS[-1] + 1, CHUNK):
        ys = [str(y) for y in range(y0, min(y0 + CHUNK, YEARS[-1] + 1))]
        for v in SURF_VARS:
            out.append({
                'name': 'surf_%s_%d' % (v[:12], y0),
                'dataset': 'reanalysis-era5-single-levels-monthly-means',
                'request': {
                    'product_type': ['monthly_averaged_reanalysis'],
                    'variable': [v], 'year': ys, 'month': MONTHS,
                    'time': ['00:00'], 'data_format': 'netcdf',
                    'download_format': 'unarchived', 'area': AREA,
                },
            })
        for v, levels in PLEV_SPEC.items():
            out.append({
                'name': 'plev_%s_%d' % (v[:12], y0),
                'dataset': 'reanalysis-era5-pressure-levels-monthly-means',
                'request': {
                    'product_type': ['monthly_averaged_reanalysis'],
                    'variable': [v], 'pressure_level': levels,
                    'year': ys, 'month': MONTHS, 'time': ['00:00'],
                    'data_format': 'netcdf', 'download_format': 'unarchived',
                    'area': AREA,
                },
            })
    return out


def load_jobs():
    if os.path.exists(JOBS):
        with open(JOBS, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_jobs(j):
    os.makedirs(CACHE, exist_ok=True)
    with open(JOBS, 'w', encoding='utf-8') as f:
        json.dump(j, f, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='all',
                    choices=('submit', 'wait', 'all', 'status'))
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    c = CDS()
    jobs = load_jobs()

    if a.stage == 'status':
        for p in plan():
            print('%-12s %s' % (p['name'], jobs.get(p['name'], '(未提交)')))
        return

    if a.stage in ('submit', 'all'):
        for p in plan():
            if jobs.get(p['name']):
                print('%-12s 已提交' % p['name'], flush=True)
                continue
            for attempt in range(3):
                try:
                    pid = c.submit(p['dataset'], p['request'])
                    jobs[p['name']] = pid
                    save_jobs(jobs)
                    print('%-12s -> %s' % (p['name'], pid), flush=True)
                    break
                except Exception as e:
                    print('%-12s 提交失败(%d/3) %s'
                          % (p['name'], attempt + 1, str(e)[:110]), flush=True)
                    time.sleep(10)
            time.sleep(SUBMIT_GAP)

    if a.stage in ('wait', 'all'):
        pending = [p for p in plan() if jobs.get(p['name'])]
        t0 = time.time()
        while pending and time.time() - t0 < 6 * 3600:
            still = []
            for p in pending:
                pid = jobs[p['name']]
                out = os.path.join(CACHE, p['name'] + '.nc')
                if os.path.exists(out) and os.path.getsize(out) > 1e5:
                    continue
                try:
                    st = c.status(pid)
                    s = (st.get('status') or '').lower()
                    if s in ('completed', 'successful', 'finished'):
                        c.download(pid, out)
                        print('%-12s 下载完成 %.1f MB'
                              % (p['name'], os.path.getsize(out) / 1e6),
                              flush=True)
                        continue
                    if s in ('failed', 'dismissed', 'rejected'):
                        print('%-12s 失败: %s' % (p['name'], json.dumps(st)[:200]),
                              flush=True)
                        continue
                    still.append(p)
                except Exception as e:
                    print('%-12s 查询异常 %s' % (p['name'], str(e)[:80]), flush=True)
                    still.append(p)
            pending = still
            if pending:
                print('  ... 仍在排队: %s' % [p['name'] for p in pending],
                      flush=True)
                time.sleep(60)
        print('全部完成' if not pending else '超时, 仍有 %d 个未完成' % len(pending))


if __name__ == '__main__':
    main()
