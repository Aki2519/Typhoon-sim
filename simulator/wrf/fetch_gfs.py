# simulator/wrf/fetch_gfs.py
"""GFS 初始/边界场下载(NOMADS filter 服务, 免注册, grib2)。

ERA5 不可用时的备用源。用法与 fetch_era5.py 一致。
"""
from __future__ import annotations
import argparse
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

BASE = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl'

# 变量通过 URL 的 var_*=on 显式声明(见 fetch_gfs), 层次含气压层 + 表面。
GFS_LEVELS = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 750,
              700, 650, 600, 550, 500, 450, 400, 350, 300, 250,
              225, 200, 175, 150, 125, 100]


def _is_valid_grib(path: str) -> bool:
    """GRIB2 校验: 大小 >1000 且以 b'GRIB' 开头(排除 HTML 错误页/半截文件)。"""
    try:
        if os.path.getsize(path) <= 1000:
            return False
        with open(path, 'rb') as f:
            return f.read(4) == b'GRIB'
    except OSError:
        return False


def _get(url: str, path: str, retries: int = 3) -> bool:
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'typhoon-sim/1.0'})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, 'wb') as f:
                f.write(r.read())
            if _is_valid_grib(path):
                return True
            # G3: 半截/空/错误页文件不判成功, 删除残留
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f'[fetch] 重试 {attempt + 1}: {e}')
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            time.sleep(3 * (attempt + 1))
    return False


def _norm_lon(x: float) -> float:
    """0~360 → [-180,180) 有符号经度(NOMADS filter 只接受 -180..180)。"""
    return ((x + 180.0) % 360.0) - 180.0


def fetch_gfs(out_dir: str, start: datetime, days: int, area: tuple) -> list:
    """按 6h 周期下载 GFS 0.25° 分析场(过去日期)或预报场。"""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    # G5: area=(N,W,S,E) 经度为 0~360 约定; NOMADS filter 要求 -180..180。
    # 0~360 西经(W,lon0) 与 东经(E,lon1) 各自折算到有符号值; 跨越 0° 时
    # leftlon > rightlon(NOMADS 以该形态表示跨反经线箱子), 两者顺序不变即正确。
    leftlon = _norm_lon(area[1])
    rightlon = _norm_lon(area[3])
    # G1: filter 服务要求逐层传参 lev_<p> mb=on, 不能用一个逗号串。
    # G4: 表面变量(PRMSL/SPFH/DSWRF/SST/PRES/TMP)必须额外带 lev_surface=on,
    #     否则 filter 只取气压层、表面变量被静默丢弃(仅标了 var_* 无对应 lev)。
    lev_qs = (''.join(f'&lev_{urllib.parse.quote(f"{p} mb")}=on' for p in GFS_LEVELS)
              + '&lev_surface=on')
    for i in range(days):
        day = start + timedelta(days=i)
        for hh in (0, 6, 12, 18):
            cycle = f'{day:%Y%m%d}{hh:02d}'
            url = (f'{BASE}?dir=/gfs.{day:%Y%m%d}/{hh:02d}/atmos'
                   f'&file=gfs.t{hh:02d}z.pgrb2.0p25.f000'
                   f'{lev_qs}'
                   f'&var_UGRD=on&var_VGRD=on&var_TMP=on&var_RH=on'
                   f'&var_PRMSL=on&var_SPFH=on&var_DSWRF=on&var_SST=on'
                   f'&subregion=&leftlon={leftlon}&rightlon={rightlon}'
                   f'&toplat={area[0]}&bottomlat={area[2]}')
            path = os.path.join(out_dir, f'gfs.t{hh:02d}z.pgrb2.0p25.f000.{cycle}')
            if _is_valid_grib(path):
                out.append(path)
                continue
            print(f'[fetch] GFS {cycle} …')
            if _get(url, path):
                out.append(path)
    return out


def main():
    ap = argparse.ArgumentParser(description='GFS 初始/边界场下载(免注册, 备用源)')
    ap.add_argument('--start', required=True, help='起始日期 YYYY-MM-DD')
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--lon0', type=float, default=95.0)
    ap.add_argument('--lon1', type=float, default=205.0)
    ap.add_argument('--lat0', type=float, default=0.0)
    ap.add_argument('--lat1', type=float, default=50.0)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    base = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(base, 'input_gfs')
    area = (args.lat1, args.lon0, args.lat0, args.lon1)
    files = fetch_gfs(out, datetime.strptime(args.start, '%Y-%m-%d'),
                      args.days, area)
    print(f'完成: {len(files)} 个文件 → {out}')


if __name__ == '__main__':
    main()
