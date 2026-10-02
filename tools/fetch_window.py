# -*- coding: utf-8 -*-
"""按"一次一个日期 + 间隔"的节奏补 ERA5 单日文件, 目标是凑出**连续多日**的瞬时窗口。

背景: CDS 对连续请求会限流(静置后的第一个请求常成功、紧接着的失败), 而强度对照实验
(瞬时 vs 月均)需要连续 5-10 天的 day+plev。所以这里逐日拉、每天之间留 gap 秒。

用法:
  python tools/fetch_window.py --from 1997-09-13 --to 1997-09-20 --hour 12:00 --gap 180
  python tools/fetch_window.py --dates 1997-09-14,1997-09-17 --gap 120
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERA5 = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'era5')
TOOL = os.path.join(ROOT, 'tools', 'fetch_era5_day.py')


def have_day(d, hour):
    hh = hour.replace(':', '')[:2]
    return (os.path.exists(os.path.join(ERA5, 'day_%s_%s.nc' % (d.replace('-', ''), hh)))
            and os.path.exists(os.path.join(ERA5, 'plev_%s_%s.nc' % (d.replace('-', ''), hh))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='d0', default=None)
    ap.add_argument('--to', dest='d1', default=None)
    ap.add_argument('--dates', default=None)
    ap.add_argument('--hour', default='12:00')
    ap.add_argument('--gap', type=float, default=180.0, help='两个日期之间的间隔秒数')
    ap.add_argument('--tries', type=int, default=2, help='每个日期最多尝试几轮')
    a = ap.parse_args(argv)
    if a.dates:
        days = [x.strip() for x in a.dates.split(',') if x.strip()]
    else:
        y0, m0, dd0 = [int(x) for x in (a.d0 or '1997-09-13').split('-')]
        y1, m1, dd1 = [int(x) for x in (a.d1 or '1997-09-20').split('-')]
        d, end = date(y0, m0, dd0), date(y1, m1, dd1)
        days = []
        while d <= end:
            days.append(d.isoformat())
            d += timedelta(days=1)
    print('目标日期: %s' % ', '.join(days), flush=True)
    for rnd in range(1, max(1, a.tries) + 1):
        todo = [d for d in days if not have_day(d, a.hour)]
        if not todo:
            break
        print('第 %d 轮, 还缺 %d 天: %s' % (rnd, len(todo), ', '.join(todo)), flush=True)
        for d in todo:
            if have_day(d, a.hour):
                print('  [已有] %s' % d, flush=True)
                continue
            t0 = time.time()
            subprocess.run([sys.executable, '-u', TOOL, '--pairs', d, '--hour', a.hour],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
            ok = have_day(d, a.hour)
            print('  %s %s (%.0fs)' % ('[到手]' if ok else '[失败]', d, time.time() - t0),
                  flush=True)
            time.sleep(max(0.0, a.gap))
    left = [d for d in days if not have_day(d, a.hour)]
    print('DONE 缺 %d 天: %s' % (len(left), ', '.join(left) if left else '无'), flush=True)


if __name__ == '__main__':
    main()
