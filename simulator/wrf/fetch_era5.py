# simulator/wrf/fetch_era5.py
"""ERA5 初始/边界场下载(CDS API, 供 WRF 的 ungrib 使用)。

下载内容(WPS 标准 ERA5 配方):
  - reanalysis-era5-pressure-levels: u/v/t/z/rh @ 1000-100 hPa, 6h 间隔, GRIB
  - reanalysis-era5-single-levels: 10u/10v/2t/2d/sst/mslp/sp, 6h 间隔, GRIB

用法:
  python fetch_era5.py --start 2026-08-01 --days 12 \
      --lon0 95 --lon1 205 --lat0 0 --lat1 50 --out wrf/input
CDS 凭证: ~/.cdsapirc(已有)。代理: 自动读系统代理/HTTP(S)_PROXY 环境变量。
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

PRESS_LEVELS = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 750,
                700, 650, 600, 550, 500, 450, 400, 350, 300, 250,
                225, 200, 175, 150, 125, 100]

PLEVEL_VARS = ['u_component_of_wind', 'v_component_of_wind', 'temperature',
               'geopotential', 'relative_humidity']
SLEVEL_VARS = ['10m_u_component_of_wind', '10m_v_component_of_wind',
               '2m_temperature', '2m_dewpoint_temperature',
               'sea_surface_temperature', 'mean_sea_level_pressure',
               'surface_pressure', 'land_sea_mask', 'skin_temperature',
               # 土壤层(metgrid/WRF 需要 ST/SM/SW/SOILM)
               'soil_temperature_level_1', 'soil_temperature_level_2',
               'soil_temperature_level_3', 'soil_temperature_level_4',
               'volumetric_soil_water_layer_1', 'volumetric_soil_water_layer_2',
               'volumetric_soil_water_layer_3', 'volumetric_soil_water_layer_4']
TIMES = ['00:00', '06:00', '12:00', '18:00']


def _proxy_env() -> None:
    """把 Windows 系统代理注入环境变量(requests 会读取)。"""
    if os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY'):
        return
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
        en, _ = winreg.QueryValueEx(k, 'ProxyEnable')
        server, _ = winreg.QueryValueEx(k, 'ProxyServer')
        if en and server:
            if not server.startswith('http://'):
                server = 'http://' + server
            os.environ['HTTP_PROXY'] = server
            os.environ['HTTPS_PROXY'] = server
            os.environ['http_proxy'] = server
            os.environ['https_proxy'] = server
            print(f'[fetch] 使用系统代理 {server}')
    except Exception:
        pass


def _days_list(start: datetime, days: int):
    return [(start + timedelta(days=i)).strftime('%d') for i in range(days)]


def _month_chunks(start: datetime, days: int):
    """把 [start, start+days) 按自然月切分 → [(year, month, [day,...]), ...]。
    E1: CDS 的 year/month/day 必须属于同一月, 跨月窗口若只给首月会静默取错天。"""
    chunks = []
    cur = start
    remaining = days
    while remaining > 0:
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1)
        take = min(remaining, (nxt - cur).days)
        days_list = [(cur + timedelta(days=i)).strftime('%d') for i in range(take)]
        chunks.append((cur.year, cur.month, days_list))
        cur = nxt
        remaining -= take
    return chunks


def _is_valid_grib(path: str) -> bool:
    """GRIB 文件校验: 大小 >1MB 且以 GRIB 魔数开头(GRIB1: 前4字节 'GRIB';
    GRIB2: 'GRIB' + 版本号)。半截/HTML 错误页会判失败。"""
    try:
        if os.path.getsize(path) < 1_000_000:
            return False
        with open(path, 'rb') as f:
            head = f.read(4)
        return head == b'GRIB'
    except OSError:
        return False


def fetch_era5(out_dir: str, start: datetime, days: int,
               area: tuple, retries: int = 3) -> list:
    """下载 ERA5 GRIB 到 out_dir。返回文件路径列表。"""
    import cdsapi
    _proxy_env()
    os.makedirs(out_dir, exist_ok=True)
    chunks = _month_chunks(start, days)
    end = start + timedelta(days=days - 1)
    suffix = f'{start.strftime("%Y%m%d")}-{end.strftime("%Y%m%d")}'

    c = cdsapi.Client()
    out = []
    # 每个自然月单独请求(文件按 起止 命名; 同月单文件, 跨月多文件)
    for ci, (y, m, day_list) in enumerate(chunks):
        ds_chunks = [
            ('reanalysis-era5-pressure-levels', {
                'product_type': 'reanalysis',
                'variable': PLEVEL_VARS,
                'pressure_level': [str(p) for p in PRESS_LEVELS],
                'year': str(y),
                'month': f'{m:02d}',
                'day': day_list,
                'time': TIMES,
                'area': list(area),
                'data_format': 'grib2',
            }, f'era5_plev_{suffix}{"_%02d" % ci if len(chunks) > 1 else ""}.grib'),
            ('reanalysis-era5-single-levels', {
                'product_type': 'reanalysis',
                'variable': SLEVEL_VARS,
                'year': str(y),
                'month': f'{m:02d}',
                'day': day_list,
                'time': TIMES,
                'area': list(area),
                'data_format': 'grib2',
            }, f'era5_slev_{suffix}{"_%02d" % ci if len(chunks) > 1 else ""}.grib'),
        ]
        for ds, req, fname in ds_chunks:
            path = os.path.join(out_dir, fname)
            if _is_valid_grib(path):
                print(f'[fetch] 已存在且有效: {fname}')
                out.append(path)
                continue
            if os.path.exists(path):
                # 半截/损坏文件不判成功, 删除重下
                print(f'[fetch] 移除损坏/半截文件: {fname}')
                try:
                    os.remove(path)
                except OSError:
                    pass
            last = None
            for attempt in range(max(1, retries)):
                try:
                    print(f'[fetch] {ds} {y}-{m:02d}({len(day_list)} 天) '
                          f'area={area} (第 {attempt + 1} 次尝试)…')
                    c.retrieve(ds, req, path)
                    if _is_valid_grib(path):
                        print(f'[fetch] OK {fname} ({os.path.getsize(path)//1024} KB)')
                        out.append(path)
                        break
                    # 半截/错误文件不判成功, 删除残留
                    if os.path.exists(path):
                        os.remove(path)
                except Exception as e:
                    last = e
                    print(f'[fetch] 失败: {e}')
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        pass
                    time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f'{ds} 下载失败: {last}')
    return out


def main():
    ap = argparse.ArgumentParser(description='ERA5 WRF 初始/边界场下载(CDS)')
    ap.add_argument('--start', required=True, help='起始日期 YYYY-MM-DD')
    ap.add_argument('--days', type=int, default=10, help='下载天数(含边界)')
    ap.add_argument('--lon0', type=float, default=95.0, help='区域西界')
    ap.add_argument('--lon1', type=float, default=205.0, help='区域东界')
    ap.add_argument('--lat0', type=float, default=0.0, help='区域南界')
    ap.add_argument('--lat1', type=float, default=50.0, help='区域北界')
    ap.add_argument('--out', default=None, help='输出目录(默认 simulator/wrf/input)')
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(base, 'input')
    start = datetime.strptime(args.start, '%Y-%m-%d')
    area = (args.lat1, args.lon0, args.lat0, args.lon1)   # 北-西-南-东
    files = fetch_era5(out, start, args.days, area)
    print(f'完成: {len(files)} 个文件 → {out}')
    for f in files:
        print('  ', os.path.basename(f))


if __name__ == '__main__':
    main()
