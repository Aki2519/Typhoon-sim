# -*- coding: utf-8 -*-
"""批量: 下载 CMEMS GLORYS 单日 thetao → 算 D26/OHC → 用同日 ERA5 瞬时 SST 门控 → 存 g2_*.npz。

要点(2026-09-20 夜里踩出来的):
  * copernicusmarine 的 subset() 把 nc **下完之后**会在校验阶段挂住(实测卡 15 分钟),
    所以驱动方式改成: **每月一个子进程 + 硬超时**, 超时就跳过该月, 不拖住整条流水线;
    (multiprocessing 在本机 interop 下会 PermissionError: WinError 5, 用 subprocess。)
  * 只要 nc 已完整落地(>100 MB), 即使客户端"超时"也照样拿去计算;
  * 计算/门控在 tools/build_ohc_from_cmems.py 里(与 daily.py 同一套重采样口径)。

用法:
  python tools/pipeline_cmems.py --drive --year 2024 --day 15 \
         --months 3,4,5,6,7,8,10,11,12 --per-month-timeout 420
  python tools/pipeline_cmems.py --year 2024 --day 15 --months 9      # 单月
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
TMP = r'C:\cmems_tmp'
DS = 'cmems_mod_glo_phy_my_0.083deg_P1D-m'
BUILD = os.path.join(ROOT, 'tools', 'build_ohc_from_cmems.py')


def nc_path(year, month, day):
    return os.path.join(TMP, 'thetao_%04d%02d%02d.nc' % (year, month, day))


def fetch_day(year, month, day, depth_max=320.0):
    import copernicusmarine as cm
    tgt = nc_path(year, month, day)
    if os.path.exists(tgt) and os.path.getsize(tgt) > 1e8:
        print('[已有] %s %.0f MB' % (tgt, os.path.getsize(tgt) / 1e6), flush=True)
        return tgt
    os.makedirs(TMP, exist_ok=True)
    t0 = time.time()
    cm.subset(dataset_id=DS, variables=['thetao'],
              minimum_longitude=-180.0, maximum_longitude=179.92,
              minimum_latitude=-60.0, maximum_latitude=60.0,
              minimum_depth=0.0, maximum_depth=depth_max,
              start_datetime='%04d-%02d-%02dT12:00:00' % (year, month, day),
              end_datetime='%04d-%02d-%02dT12:00:00' % (year, month, day),
              output_filename=os.path.basename(tgt), output_directory=TMP,
              overwrite=True, disable_progress_bar=True)
    print('[下载] %s %.0f MB  %.0fs' % (tgt, os.path.getsize(tgt) / 1e6,
                                       time.time() - t0), flush=True)
    return tgt


def one_month(year, month, day):
    """单月: 下载(若缺) → 计算+门控。返回 True/False。"""
    date = '%04d-%02d-%02d' % (year, month, day)
    g2 = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'cmems',
                      'g2_%04d%02d%02d.npz' % (year, month, day))
    if os.path.exists(g2):
        print('[跳过] %s 已存在' % os.path.basename(g2), flush=True)
        return True
    try:
        nc = fetch_day(year, month, day)
    except Exception as e:                                   # noqa: BLE001
        print('[下载失败] %s: %s' % (date, str(e)[:160]), flush=True)
        return False
    r = subprocess.run([sys.executable, '-u', BUILD, '--nc', nc, '--date', date],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    tail = [ln for ln in (r.stdout or '').splitlines()
            if ('PASS' in ln or 'FAIL' in ln or '写出' in ln or '不写' in ln
                or 'unverified' in ln or '校验' in ln)]
    print('[%s] %s' % (date, ' | '.join(tail[-3:]) or (r.stderr or '')[-200:]),
          flush=True)
    return os.path.exists(g2)


def drive(year, day, months, tmo):
    """每月一个子进程 + 硬超时(客户端可能卡在校验阶段)。"""
    for m in months:
        cmd = [sys.executable, '-u', os.path.abspath(__file__),
               '--year', str(year), '--day', str(day), '--months', str(m)]
        t0 = time.time()
        try:
            subprocess.run(cmd, cwd=ROOT, timeout=tmo, check=False)
            print('[月 %02d 完成] %.0fs' % (m, time.time() - t0), flush=True)
        except subprocess.TimeoutExpired:
            print('[月 %02d 超时 %ds] 若 nc 已完整则直接计算' % (m, tmo), flush=True)
            nc = nc_path(year, m, day)
            if os.path.exists(nc) and os.path.getsize(nc) > 1e8:
                subprocess.run([sys.executable, '-u', BUILD, '--nc', nc,
                                '--date', '%04d-%02d-%02d' % (year, m, day)],
                               cwd=ROOT, check=False)
            else:
                print('   nc 不完整, 跳过该月', flush=True)
    print('DONE', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2024)
    ap.add_argument('--day', type=int, default=15)
    ap.add_argument('--months', default='1,2,3,4,5,6,7,8,9,10,11,12')
    ap.add_argument('--drive', action='store_true',
                    help='逐月开子进程 + 硬超时(推荐)')
    ap.add_argument('--per-month-timeout', type=int, default=420)
    a = ap.parse_args(argv)
    months = [int(x) for x in a.months.split(',') if x.strip()]
    if a.drive:
        drive(a.year, a.day, months, a.per_month_timeout)
        return
    for m in months:
        one_month(a.year, m, a.day)


if __name__ == '__main__':
    main()
