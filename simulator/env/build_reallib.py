"""S8: 构建**真实**逐年库 `env/reallib/{var}_{year}.npz`(方案①) —— **Open-Meteo 兜底路线**。

⚠ **本路仅作无 CDS 凭证时的兜底**：S8 已实测本机 `~/.cdsapirc` 可用
   (`downloader.check_credentials()==True`) ⇒ **首选 `build_reallib_cds.py`**（无形状限制、
   无限流、服务端 regrid）。Open-Meteo 这条实测有三个限制：
   ① 多坐标请求**形状必须规整 m×n**（180 点同纬 → HTTP 400；11×11=121 点 ✓）；
   ② 疑似经度须在 −180..180（**未证实**，四组对照因 429 未跑成）；
   ③ 免费额度 **429 限流**（约 60–80 次请求后锁死整个窗口）。
   ⚠ **本文件尚未实现"经度映射 + 节流退避"** —— 使用前必须补上，并复用
   `env/realdir/` 里的分块缓存断点续跑。

与 `env/library/` **同 shape (12,121,360) / 同 keys ['months','meta'] / 同 dam 口径**,
但 `meta.source='open-meteo-ifs'` —— 不覆盖合成库, 供 simcore 优先读取。

数据源: Open-Meteo IFS historical-forecast 端点(免 key)。
  ⚠ 陷阱(S7 实测): **ERA5-archive 端点对 geopotential_height_500hPa 会"返回该键但全为 null"**,
    必须走 IFS 端点; 且该变量在 2016/2017/2018 也是全 null(存档覆盖问题, 非凭证问题)。
  ⚠ 单位: IFS 返回**米(=gpm)**; hgt500 需 `/10.0` 转 **dam**(只除一次!)。
格点: 2° 抓取 (61x180) → 双线性上采样到 1° (121x360), 与 real_source.py 的约定一致。
缓存: env/realdir/reallib_chunk_*.npz (断点续传)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding='utf-8')

ENV = os.path.dirname(os.path.abspath(__file__))
REALLIB = os.path.join(ENV, 'reallib')
REALDIR = os.path.join(ENV, 'realdir')
IFS = 'https://historical-forecast-api.open-meteo.com/v1/forecast'
TIMEOUT = 45
YEAR = int(os.environ.get('S8_YEAR', '2024'))
CHUNK = 200                      # 每请求格点数(URL 长度与响应大小的折中)

FETCH_LATS = np.arange(-60.0, 60.0 + 1e-9, 2.0)      # 61
FETCH_LONS = np.arange(0.0, 360.0, 2.0)             # 180
TGT_LATS = np.linspace(-60.0, 60.0, 121)            # 与 library 一致
TGT_LONS = np.linspace(0.0, 359.0, 360)

VARS = {
    # 键名: (Open-Meteo daily 变量, 后处理)
    'mslp': ('pressure_msl_mean', lambda a: a),                      # hPa
    # ⚠ hgt500 不能用 daily `_mean` 后缀(实测 HTTP 400); 只能走 hourly 再按日聚合。
    #   本文件暂不自动抓 hgt500, 见 S8 报告: 需 hourly 24×365 值, 另行批量。
    # 'hgt500': ('geopotential_height_500hPa_mean', lambda a: a / 10.0),
}
ONLY = os.environ.get('S8_ONLY', '').split(',') if os.environ.get('S8_ONLY') else None
if ONLY:
    VARS = {k: v for k, v in VARS.items() if k in ONLY}


def log(*a):
    print(*a, flush=True)


def fetch(params, tries=4):
    url = IFS + '?' + urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'typhoon-sim/1.0'})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode('utf-8')), None
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
            time.sleep(1.5 * (i + 1))
    return None, last


def month_index(dates):
    """'2024-07-15' → 6 (0-based month)。"""
    return np.array([int(d[5:7]) - 1 for d in dates], dtype=int)


def upsample(field2d):
    """(61,180) 2° → (121,360) 1° 双线性(numpy interp, 确定性)。"""
    # 经度先补一个环绕点, 再插值
    lon_src = np.concatenate([FETCH_LONS, [360.0]])
    f = np.concatenate([field2d, field2d[:, :1]], axis=1)
    out = np.empty((TGT_LATS.size, TGT_LONS.size), dtype=float)
    for i in range(field2d.shape[0]):
        out[i] = np.interp(TGT_LONS, lon_src, f[i])
    out2 = np.empty_like(out)
    for j in range(out.shape[1]):
        out2[:, j] = np.interp(TGT_LATS, FETCH_LATS, out[:, j])
    return out2


def main():
    os.makedirs(REALLIB, exist_ok=True)
    os.makedirs(REALDIR, exist_ok=True)
    start, end = '%d-01-01' % YEAR, '%d-12-31' % YEAR

    # 网格点列表
    # **必须按规整 m×n 小网格分块**（S8 实测 4 组对照）:
    #   180 点(1 纬 × 180 经) → HTTP 400; 200 点(100 经 × 2 纬) → 400;
    #   而 11纬×11经=121 点、14×26=364 点(S7 v4/v5) 都成功, 364 点 URL 4152 字符也能过
    #   ⇒ 限制在**坐标列表形状**(等长成对 + 规整矩形), 不在点数或 URL 长度。
    BLK_LAT, BLK_LON = 11, 11
    chunks = []
    for i0 in range(0, FETCH_LATS.size, BLK_LAT):
        for j0 in range(0, FETCH_LONS.size, BLK_LON):
            la_blk = FETCH_LATS[i0:i0 + BLK_LAT]
            lo_blk = FETCH_LONS[j0:j0 + BLK_LON]
            chunks.append({'i0': i0, 'j0': j0, 'nlat': la_blk.size, 'nlon': lo_blk.size,
                           'pts': [(a, b) for a in la_blk for b in lo_blk]})
    log('YEAR=%d  抓取网格=%dx%d  chunks=%d (每块 %dx%d 规整矩形)  目标 shape=(12,%d,%d)'
        % (YEAR, FETCH_LATS.size, FETCH_LONS.size, len(chunks), BLK_LAT, BLK_LON,
           TGT_LATS.size, TGT_LONS.size))

    for var, (api_name, post) in VARS.items():
        out_path = os.path.join(REALLIB, '%s_%d.npz' % (var, YEAR))
        if os.path.exists(out_path):
            log('[%s] 已存在, 跳过: %s' % (var, out_path))
            continue
        acc = np.zeros((12, FETCH_LATS.size, FETCH_LONS.size), dtype=float)
        cnt = np.zeros(12, dtype=float)
        dates = None
        fails = 0
        t0 = time.time()
        for ci, chunk in enumerate(chunks):
            pts_c = chunk['pts']
            cache = os.path.join(REALDIR, 'reallib_%s_%d_c%03d.npz' % (var, YEAR, ci))
            if os.path.exists(cache):
                z = np.load(cache, allow_pickle=True)
                vals, dates_c = z['vals'], list(z['dates'])
            else:
                params = {
                    'latitude': ','.join('%.0f' % p[0] for p in pts_c),
                    'longitude': ','.join('%.0f' % p[1] for p in pts_c),
                    'start_date': start, 'end_date': end,
                    'daily': api_name, 'timezone': 'UTC',
                }
                data, err = fetch(params)
                if data is None:
                    fails += 1
                    log('  [%s] chunk %d/%d 失败: %s' % (var, ci + 1, len(chunks), err))
                    continue
                if isinstance(data, dict):
                    data = [data]
                dates_c = ((data[0].get('daily') or {}).get('time') or [])
                vals = np.full((len(data), len(dates_c)), np.nan)
                for k, item in enumerate(data):
                    v = ((item.get('daily') or {}).get(api_name) or [])
                    vals[k, :len(v)] = [x if x is not None else np.nan for x in v]
                np.savez_compressed(cache, vals=vals, dates=np.array(dates_c, dtype='U10'))
            if dates is None:
                dates = dates_c
                mi = month_index(dates)
                log('  [%s] 日期数=%d (首=%s 末=%s)' % (var, len(dates), dates[0], dates[-1]))
            mi = month_index(dates_c)
            for m in range(12):
                sel = (mi == m)
                if not sel.any():
                    continue
                # 规整矩形分块互不重叠 ⇒ **赋值**(不是累加), 每格恰由一块写入
                acc[m, chunk['i0']:chunk['i0'] + chunk['nlat'],
                    chunk['j0']:chunk['j0'] + chunk['nlon']] = \
                    np.nanmean(vals[:, sel], axis=1).reshape(chunk['nlat'], chunk['nlon'])
                cnt[m] += 1
            if (ci + 1) % 10 == 0 or ci + 1 == len(chunks):
                log('  [%s] %d/%d chunks  (%.0fs, 失败 %d)' % (var, ci + 1, len(chunks),
                                                             time.time() - t0, fails))
        # 平均各 chunk 的月均值, 再上采样
        months = np.full((12, TGT_LATS.size, TGT_LONS.size), np.nan)
        for m in range(12):
            if cnt[m] <= 0:
                continue
            f2 = acc[m]          # 规整分块已赋值, 无需再除 cnt
            months[m] = post(upsample(f2))
        meta = {
            'source': 'open-meteo-ifs',
            'endpoint': IFS,
            'api_var': api_name,
            'units': 'hPa' if var == 'mslp' else 'dam',
            'year': YEAR,
            'grid': 'lat 60S..60N 1deg x121 ; lon 0..359 1deg x360',
            'fetch_grid': '2deg 61x180 -> bilinear 1deg',
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'chunks_ok': int(cnt.max()), 'chunks_failed': int(fails),
            'note': ('ERA5-archive 端点对该变量返回键但全 null, 必须用 IFS 端点; '
                     'hgt500 由 geopotential_height_500hPa(m) / 10.0 转 dam(只除一次)'),
        }
        np.savez_compressed(out_path, months=months.astype(np.float32), meta=meta)
        fin = np.isfinite(months)
        log('[%s] 写出 %s  %.1f MB  shape=%s  min=%.3f max=%.3f (有效 %.1f%%)'
            % (var, out_path, os.path.getsize(out_path) / 1e6, months.shape,
               np.nanmin(months[fin]), np.nanmax(months[fin]), 100.0 * fin.mean() / months.size * months.size / months.size))
    log('DONE')


if __name__ == '__main__':
    main()
