"""S8: 用 **CDS/ERA5** 构建真实逐年库 `env/reallib/{var}_{year}.npz`(方案①)。

为什么用 CDS 而不是 Open-Meteo(S8 实测):
  - Open-Meteo 免费额度: ① 多坐标请求**形状必须规整 m×n**(180 点同纬→400); ② 疑似经度须
    在 -180..180(**未证实**); ③ **429 限流**(约 60-80 次后锁死整个窗口)。
  - **CDS 本机已配好凭证**(`~/.cdsapirc`, `downloader.check_credentials()==True`), 一次请求
    即可拿整月、**服务器端 regrid**(`grid=[1,1]`)与 **area 裁剪**, 无形状/限流问题。
    ⚠ S7 报告曾写"CDS 要 key、拿不到" —— **那是错的**, 已在 S8 更正。

单位陷阱(两条都在 S8 实测过, 别踩):
  1. `msl`(海平面气压) CDS 返回 **Pa**, 需 /100 → hPa(实测 100107.75..102432.75 Pa)。
  2. 500hPa `z`(geopotential) CDS 返回 **m²/s²**, 需 /9.80665 → **gpm(米)**, 再 /10 → **dam**
     (实测 57225.6..58339.9 m²/s² ⇒ 5835.4..5949.0 gpm ⇒ 583.5..594.9 dam, 峰值 ≥5880 ✓)。
     **只除一次 9.80665、只除一次 10** —— 双重换算会把 5880 变成 58800 或 58.8。

输出与 `env/library/` 同 shape `(12,121,360)` / 同 keys `['months','meta']`, 但 meta 写明真实来源。
**不覆盖** `library/` 与 `climate/`。
"""
from __future__ import annotations

import io
import os
import sys
import time
from contextlib import contextmanager

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding='utf-8')

ENV = os.path.dirname(os.path.abspath(__file__))
REALLIB = os.path.join(ENV, 'reallib')
REALDIR = os.path.join(ENV, 'realdir')
YEAR = int(os.environ.get('S8_YEAR', '2024'))
ONLY = [v for v in os.environ.get('S8_ONLY', '').split(',') if v]

#: 与 library/ 对齐的目标网格
TGT_LATS = np.linspace(-60.0, 60.0, 121)      # -60..60, 1°
TGT_LONS = np.linspace(0.0, 359.0, 360)       # 0..359, 1°
AREA = [60, -180, -60, 180]                   # N, W, S, E —— CDS 用 -180..180 经度

VARS = {
    # 键名: (CDS dataset, 变量名, pressure_level, 单位换算)
    'mslp': ('reanalysis-era5-single-levels', 'mean_sea_level_pressure', None,
             lambda a: a / 100.0),                       # Pa → hPa
    'hgt500': ('reanalysis-era5-pressure-levels', 'geopotential', '500',
               lambda a: a / 9.80665 / 10.0),            # m²/s² → gpm → dam
}


def log(*a):
    print(*a, flush=True)


def wait_file_stable(path, stable_for=2.0, timeout_s=900):
    """等文件**真正落地完成**: 存在 且 大小**连续 stable_for 秒不变**。

    为什么不能只判"存在": S8 实测 cdsapi 的 `retrieve()` 会**先返回、目标文件随后才落地**
    (日志里 8–9 MB 的进度条在异常之后才走完), **存在但没写完是最坑的情况**。
    固定 120s 轮询也没覆盖住 ⇒ 改用"大小稳定"判据 + **总超时上限 900s**。
    ⚠ 当时有并发干扰(两遍 CDS 重叠), 该结论**需单进程复跑同一月份确认**。
    """
    t0 = time.time()
    last_sz, last_change = -1, time.time()
    while time.time() - t0 < timeout_s:
        if os.path.exists(path):
            try:
                sz = os.path.getsize(path)
            except OSError:
                sz = -1
            if sz != last_sz:
                last_sz, last_change = sz, time.time()
            elif sz > 1024 and (time.time() - last_change) >= stable_for:
                return True
        time.sleep(0.5)
    return False


def _days_in_month(year, month):
    import calendar
    return calendar.monthrange(year, month)[1]


@contextmanager
def _chdir_ctx(path: str):
    """临时切换工作目录, 离开时恢复(即便异常)。

    ⚠ S8/§D7 实测根因: **netCDF4/HDF5 的 C 库在 Windows 上打不开含非 ASCII 的绝对路径**
    （`G:/气象/.../x.nc` ⇒ FileNotFoundError，而同进程 `os.path.getsize()` 取得到大小，
    所以特别迷惑；同一文件复制到 `G:/tmp/` 就能开）。
    解法与 `simcore/climate.py::_chdir_ctx` 同口径：chdir 到文件所在目录、只传**相对文件名**。
    副作用限定在上下文内、可重入（离开时恢复原 cwd）。
    """
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _open_nc(path):
    """把 .nc **读进内存**再用 xarray 打开 —— 完全绕开路径（S8b 定稿修法）。

    ⚠ 四种方式在同一进程、同一 9.03 MB 文件上的实测（见 `s8_env_daily.md` §D7 与 S8b 报告）：

    | 方式 | 结果 |
    |---|---|
    | (a) 中文绝对路径 | **FileNotFoundError** |
    | (b) `chdir` 到文件目录 + 只传**相对名**（§D8.4 原定修法） | **FileNotFoundError —— 无效** |
    | (c) 暂存到纯 ASCII 目录再开 | OK |
    | **(d) 读字节 → `BytesIO` 打开（本实现）** | **OK**（默认引擎与 h5netcdf 都可） |

    (b) 为什么无效：HDF5 的 C 库会用**进程 ANSI 代码页**把相对名解析回绝对路径，
    而 cwd 本身就在 `G:/气象/...` 下 ⇒ 解析结果**仍然含中文** ⇒ 照样打不开。
    所以"换 cwd"救不了这件事，**必须让 C 库拿到的路径整体是 ASCII**（(c)）
    或者**根本不经过路径**（(d)）。
    (d) 不落临时文件、不依赖 ASCII 目录、不做复制 ⇒ 三者里最干净，也不违反
    "不用复制到 /tmp" 的约束。
    """
    import xarray as xr
    with open(path, 'rb') as f:
        _raw = f.read()
    return xr.open_dataset(io.BytesIO(_raw))


def validate_nc(path, year=None, month=None, times_per_day=4):
    """落地后**内容校验**（不只是"能打开"）。返回 (ok, 说明)。

    ⚠ S8 教训：`cds_mslp_2024_09.nc` **只有 2.00 MB**（其余 8.3–8.6 MB）**却是合法 HDF5 头**
    （`\\x89HDF\\r\\n\\x1a\\n`）⇒ **"xarray 能打开"挡不住截断文件**。所以查内容：
      ① 时间维须覆盖整月（该月天数 × times_per_day）
      ② 数据变量在, 且**不是全 NaN / 有效数据不过半**
      ③ 经纬网格与声明的 area/grid 一致（1° ⇒ ~121×360）
    """
    try:
        ds = _open_nc(path)          # ⚠ 走 chdir+相对名（§D7：非 ASCII 绝对路径打不开）
    except Exception as e:
        return False, '打不开 %s: %s' % (type(e).__name__, str(e)[:120])
    try:
        vars_ = list(ds.data_vars)
        if not vars_:
            return False, '无数据变量'
        v = vars_[0]
        da = ds[v]
        sizes = dict(da.sizes)
        tdim = 'valid_time' if 'valid_time' in sizes else ('time' if 'time' in sizes else None)
        n_t = int(sizes.get(tdim, 0)) if tdim else 0
        n_lat = int(sizes.get('latitude', 0))
        n_lon = int(sizes.get('longitude', 0))
        if tdim and n_t:
            arr = np.asarray(da.isel({tdim: n_t // 2}).values, dtype=float)
        else:
            arr = np.asarray(da.values, dtype=float)
    except Exception as e:
        return False, '读内容失败 %s: %s' % (type(e).__name__, str(e)[:120])
    finally:
        ds.close()
    if 'latitude' not in sizes or 'longitude' not in sizes:
        return False, '缺经纬维: %s' % list(sizes)
    if arr is None or not np.isfinite(arr).any():
        return False, '数据全 NaN/空'
    if np.isfinite(arr).mean() < 0.5:
        return False, '有效数据不足一半(%.0f%%)' % (100 * np.isfinite(arr).mean())
    if year and month and tdim:
        exp = _days_in_month(year, month) * times_per_day
        if n_t != exp:
            return False, ('时间维 %d != 期望 %d（%d 天 × %d 时次）⇒ 疑似截断'
                           % (n_t, exp, _days_in_month(year, month), times_per_day))
    if n_lat < 100 or n_lon < 300:
        return False, '经纬网格异常小: %dx%d（期望 ~121x360）' % (n_lat, n_lon)
    return True, '%s %s' % (v, sizes)


def fetch_month(var, month):
    """取某年某月的**逐日**(每天 4 个时次)ERA5, 服务器端 regrid 到 1°。返回 (days, 121, 360)。"""
    import cdsapi
    ds_name, cds_var, level, conv = VARS[var]
    out = os.path.join(REALDIR, 'cds_%s_%d_%02d.nc' % (var, YEAR, month))
    if not os.path.exists(out):
        req = {
            'product_type': 'reanalysis',
            'variable': [cds_var],
            'year': str(YEAR), 'month': '%02d' % month,
            'day': ['%02d' % d for d in range(1, 32)],
            'time': ['00:00', '06:00', '12:00', '18:00'],
            'area': AREA,
            'grid': [1.0, 1.0],
            'data_format': 'netcdf',
        }
        if level:
            req['pressure_level'] = [level]
        log('  [%s] 请求 CDS %d-%02d ...' % (var, YEAR, month))
        t0 = time.time()
        cdsapi.Client(timeout=600, quiet=True).retrieve(ds_name, req, out)
        if not wait_file_stable(out):
            raise RuntimeError('CDS 文件 15 分钟未落地(大小始终不稳定): %s' % out)
        log('  [%s] ✓ %d-%02d %.0fs %.1f MB' % (var, YEAR, month, time.time() - t0,
                                                os.path.getsize(out) / 1e6))
    # **缓存命中路径也要校验**(存在 ≠ 写完)
    _ok, _info = validate_nc(out)
    if not _ok:
        raise RuntimeError('CDS 文件校验失败(%s): %s' % (_info, out))
    log('  [%s] %d-%02d 校验通过: %s' % (var, YEAR, month, _info))
    ds = _open_nc(out)               # ⚠ 同上：chdir + 相对名
    vname = list(ds.data_vars)[0]
    da = ds[vname]
    # 维度名兼容: valid_time / time
    tdim = 'valid_time' if 'valid_time' in da.dims else 'time'
    lat = np.asarray(ds['latitude'].values, dtype=float)
    lon = np.asarray(ds['longitude'].values, dtype=float)
    arr = np.asarray(da.values, dtype=float)
    arr = np.squeeze(arr)
    while arr.ndim > 3:                    # 去掉多余的 pressure_level 单例维
        arr = arr.squeeze(axis=1) if arr.shape[1] == 1 else arr.reshape(arr.shape[0], -1, arr.shape[-2], arr.shape[-1]).squeeze(axis=1)
    if arr.ndim == 2:                      # 单时次
        arr = arr[None]
    # 目标网格插值(CDS 已是 1°, 但顺序/范围要对齐)
    nt = arr.shape[0]
    out3 = np.full((nt, TGT_LATS.size, TGT_LONS.size), np.nan)
    lon_idx = np.argsort(lon)
    lon_s, lat_s = lon[lon_idx], lat
    # ⚠ S8b 修：**CDS 返回的 latitude 是降序（60 → -60）**，而 `np.interp` 要求 `xp` **递增**。
    # 原判据 `lat_s[0] < lat_s[-1]`（=升序才翻）在 CDS 的降序下**永不成立** ⇒ 不翻
    # ⇒ 把降序当 xp 传进 np.interp ⇒ 结果退化（实测**沿纬度恒等于 fp[0]**，
    #   于是每个纬度带的 min/max/mean 一模一样、全球均值 988 hPa 这种明显不对的值）。
    # 正确判据：**降序就翻**（归一到升序），并把数据一起翻回来。
    if lat_s[0] > lat_s[-1]:
        lat_s = lat_s[::-1]
        arr = arr[:, ::-1, :]
    for t in range(nt):
        a = arr[t][:, lon_idx]
        f = np.empty((lat_s.size, 360))
        for i in range(lat_s.size):
            f[i] = np.interp(TGT_LONS, np.concatenate([lon_s, [lon_s[0] + 360.0]]),
                             np.concatenate([a[i], a[i, :1]]))
        out3[t] = np.array([np.interp(TGT_LATS, lat_s, f[:, j]) for j in range(360)]).T
    return conv(out3), len(da[tdim].values)


def _existing_is_valid(path):
    """产物内容校验（S8b）：用于"已存在 ⇒ 跳过"之前的把关。

    没有这一步时，一次失败运行留下的**全 NaN 桩**会让后续运行永远跳过它
    ⇒ 坏状态锁死。校验四项：keys / shape / 有效点比例 / `days_per_month` 无 0。
    返回 (是否合格, 说明)。
    """
    try:
        with np.load(path, allow_pickle=True) as z:
            if 'months' not in z or 'meta' not in z:
                return False, 'keys=%s' % list(z.keys())
            m = np.asarray(z['months'])
            meta = z['meta'].item()
        # extended (task G): uv_steer is dual-component 4D (12,2,121,360);
        # the original 3D-only check would judge it invalid.
        _ok_shape = (tuple(m.shape) == (12, TGT_LATS.size, TGT_LONS.size)
                     or tuple(m.shape) == (12, 2, TGT_LATS.size, TGT_LONS.size))
        if not _ok_shape:
            return False, 'shape=%s (期望 %s)' % (m.shape, (12, TGT_LATS.size, TGT_LONS.size))
        fin = np.isfinite(m)
        if fin.mean() < 0.99:
            return False, '有效点仅 %.1f%%' % (100 * fin.mean())
        nd = list(meta.get('days_per_month') or [])
        if len(nd) != 12 or min(nd) <= 0:
            return False, 'days_per_month=%s' % (nd,)
        return True, 'valid %.0f%% days=%s' % (100 * fin.mean(), nd)
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:60])


def main():
    os.makedirs(REALLIB, exist_ok=True)
    os.makedirs(REALDIR, exist_ok=True)
    log('CDS 构建真实库: YEAR=%d  vars=%s  目标 shape=(12,%d,%d)'
        % (YEAR, ONLY or list(VARS), TGT_LATS.size, TGT_LONS.size))
    for var in (ONLY or list(VARS)):
        if var not in VARS:
            log('  跳过未知变量 %s' % var)
            continue
        out_path = os.path.join(REALLIB, '%s_%d.npz' % (var, YEAR))
        # ⚠ S8b：**"已存在"不能无脑跳过**。
        # 本脚本原来在"成功判定"之前就落盘 ⇒ 一次失败的运行会留下**全 NaN 的桩**
        # （实测: `days_per_month=[0]*12`、`finite` 0.0%、文件仅 2.9 KB），
        # 而下一次运行看到它就跳过 ⇒ **坏状态被锁死**，之后怎么重跑都还是坏数据。
        # 现在：**先做内容校验，合格才跳过**。
        if os.path.exists(out_path):
            _ok_skip, _why = _existing_is_valid(out_path)
            if _ok_skip:
                log('  [%s] 已存在且内容校验通过, 跳过 (%s)' % (var, _why))
                continue
            log('  [%s] 已存在但**内容校验不通过**(%s) ⇒ 判废重算' % (var, _why))
        months = np.full((12, TGT_LATS.size, TGT_LONS.size), np.nan)
        n_days = np.zeros(12, dtype=int)
        for m in range(1, 13):
            try:
                days, nt = fetch_month(var, m)
            except Exception as e:
                log('  [%s] %d-%02d 失败: %s: %s' % (var, YEAR, m, type(e).__name__, str(e)[:200]))
                continue
            # 每天 4 时次 → 按日平均 → 再按整月平均
            per_day = days.reshape(nt // 4, 4, TGT_LATS.size, TGT_LONS.size) if nt % 4 == 0 else days.reshape(nt, 1, TGT_LATS.size, TGT_LONS.size)
            months[m - 1] = np.nanmean(per_day, axis=(0, 1))
            n_days[m - 1] = per_day.shape[0]
            log('  [%s] %02d 月完成: %d 时次 → 月均 min=%.3f max=%.3f'
                % (var, m, nt, np.nanmin(months[m - 1]), np.nanmax(months[m - 1])))
        meta = {
            'source': 'era5-cds',
            'dataset': VARS[var][0],
            'cds_variable': VARS[var][1],
            'pressure_level': VARS[var][2],
            'units': 'hPa' if var == 'mslp' else 'dam',
            'unit_note': ('msl: Pa/100 → hPa' if var == 'mslp'
                          else 'z: m²/s² /9.80665 → gpm, 再 /10 → dam（各只除一次）'),
            'year': YEAR,
            'days_per_month': n_days.tolist(),
            'grid': 'lat 60S..60N 1deg x121 ; lon 0..359 1deg x360 (CDS 服务端 regrid)',
            'area': AREA,
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'credentials': '~/.cdsapirc (check_credentials()==True)',
        }
        # ── 写盘前的最后一道闸（S8b）──────────────────────────────
        # 1) `days_per_month` 里出现 0 = 该月整月没成 ⇒ 数据不完整
        #    （这正是那次留下全 NaN 桩的特征）⇒ **宁可不写，也不落桩文件**
        _nd = np.asarray(n_days)
        if int((_nd == 0).sum()) > 0:
            log('[%s] **跳过写盘**: days_per_month 有 %d 个月为 0 (%s) ⇒ 不落桩文件'
                % (var, int((_nd == 0).sum()), _nd.tolist()))
            continue
        fin = np.isfinite(months)
        if fin.mean() < 0.99:
            log('[%s] **跳过写盘**: 有效点仅 %.1f%% ⇒ 不落桩文件' % (var, 100 * fin.mean()))
            continue
        # 2) **原子落盘**：先写 `.tmp`，再 `os.replace`（同盘原子）。
        #    这样"写到一半"或"校验没过"都不会留下一个看起来正常的产物。
        _tmp = out_path[:-4] + '.tmp.npz'   # savez_* 会给没有 .npz 后缀的名字**再补一个**
        np.savez_compressed(_tmp, months=months.astype(np.float32), meta=meta)
        os.replace(_tmp, out_path)
        log('[%s] 写出 %s  %.1f MB  min=%.3f max=%.3f'
            % (var, out_path, os.path.getsize(out_path) / 1e6,
               np.nanmin(months[fin]), np.nanmax(months[fin])))
        # clim: 由已有真实年份平均（现在只有一年 ⇒ n_years=1，**不是气候态**，meta 写明）
        years = sorted(int(f.split('_')[-1].split('.')[0]) for f in os.listdir(REALLIB)
                       if f.startswith(var + '_') and f.endswith('.npz') and '_clim' not in f)
        stack = []
        for y in years:
            stack.append(np.load(os.path.join(REALLIB, '%s_%d.npz' % (var, y)), allow_pickle=True)['months'])
        clim = np.nanmean(np.stack(stack), axis=0)
        _clim_path = os.path.join(REALLIB, '%s_clim.npz' % var)
        _clim_tmp = _clim_path[:-4] + '.tmp.npz'   # 同上：必须自带 .npz 后缀
        np.savez_compressed(_clim_tmp, clim=clim.astype(np.float32),
                            meta={'source': 'era5-cds', 'n_years': len(years), 'years': years,
                                  'warning': 'n_years=1 时这只是**一年的年循环**, 不是 30 年气候态；'
                                             '回填更多年份后本文件自然变准。',
                                  'units': 'hPa' if var == 'mslp' else 'dam'})
        os.replace(_clim_tmp, _clim_path)     # 原子落盘(S8b)
        log('[%s] 写出 clim (n_years=%d, years=%s)' % (var, len(years), years))
    log('DONE')


# ── S13(task-69): 真·多年气候态（1991–2020, 30 年）─────────────────────────
#: 月平均数据集（= 上面的数据集名 + `-monthly-means`）。**一条请求列多年**，
#: 而不是 30 年 × 12 月 = 360 条请求。
CLIM_YEARS = list(range(1991, 2021))
CLIM_SPLIT = 5           # 每块最多几年（块小 ⇒ 返回快、失败只丢一块；别切成 360 条）
CHUNK_TIMEOUT = 600      # 单块 CDS 请求超时（秒）
CHUNK_RETRIES = 2        # 单块失败后的重试次数


def _to_target_2d(a, lat, lon):
    """把一张 (nlat, nlon) 场插值到目标网格 (121, 360) —— 与 `fetch_month` 同一口径。

    ⚠ 纬度必须归一到**递增**：CDS 返回降序，`np.interp` 的 `xp` 必须递增；
    原判据 `lat[0] < lat[-1]` 在降序下永不成立（S8b 的著名 bug）。
    """
    lat_s = np.asarray(lat, dtype=float)
    lon_s = np.asarray(lon, dtype=float)
    lon_idx = np.argsort(lon_s)
    lon_s = lon_s[lon_idx]
    a = a[:, lon_idx]
    if lat_s[0] > lat_s[-1]:
        lat_s = lat_s[::-1]
        a = a[::-1, :]
    f = np.empty((lat_s.size, TGT_LONS.size))
    xp = np.concatenate([lon_s, [lon_s[0] + 360.0]])
    for i in range(lat_s.size):
        f[i] = np.interp(TGT_LONS, xp, np.concatenate([a[i], a[i, :1]]))
    return np.array([np.interp(TGT_LATS, lat_s, f[:, j]) for j in range(TGT_LONS.size)]).T


def fetch_clim_chunk(var, years, tag):
    """取 `years` 这些年的**月平均**（一条请求），返回 {month: [场, ...]}。"""
    import cdsapi
    ds_name, cds_var, level, conv = VARS[var]
    out = os.path.join(REALDIR, 'cds_clim_%s_%s.nc' % (var, tag))
    if not os.path.exists(out):
        req = {
            'product_type': 'monthly_averaged_reanalysis',
            'variable': [cds_var],
            'year': [str(y) for y in years],
            'month': ['%02d' % m for m in range(1, 13)],
            'time': '00:00',
            'area': AREA,
            'grid': [1.0, 1.0],
            'data_format': 'netcdf',
        }
        if level:
            req['pressure_level'] = [level]
        log('  [%s] 请求 CDS 月平均 %d-%d（%d 年）...' % (var, years[0], years[-1], len(years)))
        last = None
        for attempt in range(1, CHUNK_RETRIES + 2):
            t0 = time.time()
            try:
                # ⚠ quiet=False：排队期**必须有进度输出**，否则分不清"在排队"还是"挂死了"
                cdsapi.Client(timeout=CHUNK_TIMEOUT, quiet=False).retrieve(
                    ds_name + '-monthly-means', req, out)
                if not wait_file_stable(out, timeout_s=CHUNK_TIMEOUT):
                    raise RuntimeError('文件未在 %ds 内稳定落地: %s' % (CHUNK_TIMEOUT, out))
                last = None
                log('  [%s] ✓ %d-%d 第%d次 %.0fs %.1f MB'
                    % (var, years[0], years[-1], attempt, time.time() - t0,
                       os.path.getsize(out) / 1e6))
                break
            except Exception as e:
                last = e
                log('  [%s] ✗ %d-%d 第%d次失败(%.0fs): %s: %s'
                    % (var, years[0], years[-1], attempt, time.time() - t0,
                       type(e).__name__, str(e)[:200]))
                try:
                    if os.path.exists(out):
                        os.remove(out)          # 坏文件必须删，否则 _existing 判据会误命中
                except OSError:
                    pass
        if last is not None:
            raise last                          # 交给 build_clim 记录"该块失败"并继续下一块
    ds = _open_nc(out)
    vname = list(ds.data_vars)[0]
    da = ds[vname]
    tdim = 'valid_time' if 'valid_time' in da.dims else 'time'
    times = np.asarray(da[tdim].values)
    lat = np.asarray(ds['latitude'].values, dtype=float)
    lon = np.asarray(ds['longitude'].values, dtype=float)
    arr = np.asarray(da.values, dtype=float).squeeze()
    while arr.ndim > 3:
        arr = arr.reshape(arr.shape[0], -1, arr.shape[-2], arr.shape[-1]).squeeze(axis=1)
    by_month = {}
    for k in range(arr.shape[0]):
        ts = str(times[k])
        try:                                    # 'YYYY-MM-DD...' ⇒ 月份
            mon = int(ts[5:7])
        except Exception:
            mon = k % 12 + 1
        # ⚠ 必须过单位换算：fetch_month 是 conv(out3)；这里漏了会让 mslp 存成 Pa (97858) 而不是 hPa
        by_month.setdefault(mon, []).append(conv(_to_target_2d(arr[k], lat, lon)))
    ds.close()
    return by_month


def build_clim(y0=1991, y1=2020):
    """S13: 用多年月平均建**真气候态**，写 `*_clim.npz`（clim + meta）。

    **单块失败 ⇒ 记进 `MISSING` 并继续下一块；只要有缺口就绝不组装 `_clim`**
    （"未取到"不许写成"已完成"，也不许静默跳过）。
    """
    global MISSING
    MISSING = []
    all_years = list(range(int(y0), int(y1) + 1))
    log('=== S13 气候态 %d-%d（%d 年）===' % (all_years[0], all_years[-1], len(all_years)))
    for var in VARS:
        out_path = os.path.join(REALLIB, '%s_clim.npz' % var)
        if _clim_is_valid(out_path, len(all_years), var):
            log('[%s] 已有合格 clim，跳过: %s' % (var, out_path))
            continue
        acc = {m: [] for m in range(1, 13)}
        chunks = [all_years[i:i + CLIM_SPLIT] for i in range(0, len(all_years), CLIM_SPLIT)]
        failed = []
        for ch in chunks:
            try:
                bm = fetch_clim_chunk(var, ch, '%d_%d' % (ch[0], ch[-1]))
            except Exception as e:                      # 单块失败 ⇒ 记下来继续下一块
                failed.append((ch[0], ch[-1], '%s: %s' % (type(e).__name__, str(e)[:200])))
                log('  [%s] !! 块 %d-%d 失败，继续下一块: %s' % (var, ch[0], ch[-1], str(e)[:160]))
                continue
            for m, v in bm.items():
                acc[m].extend(v)
        if failed:
            _miss = ', '.join('%d-%d' % (a, b) for a, b, _ in failed)
            log('[%s] 有 %d 块失败（缺 %s）⇒ **不组装 _clim**，保留旧文件；错误原文:' % (var, len(failed), _miss))
            for a, b, err in failed:
                log('    %d-%d: %s' % (a, b, err))
            MISSING.append({'var': var, 'missing': _miss,
                            'errors': ['%d-%d: %s' % (a, b, e) for a, b, e in failed]})
            continue
        clim = np.stack([np.nanmean(np.stack(acc[m]), axis=0) for m in range(1, 13)])
        assert clim.shape == (12, TGT_LATS.size, TGT_LONS.size), clim.shape
        meta = {
            'source': 'era5-cds', 'dataset': VARS[var][0] + '-monthly-means',
            'product_type': 'monthly_averaged_reanalysis',
            'variable': VARS[var][1], 'years': all_years, 'n_years': len(all_years),
            'units': 'hPa' if var == 'mslp' else 'dam',
            'conversion': ('msl Pa -> hPa: /100' if var == 'mslp'
                           else 'z m2/s2 -> gpm: /9.80665 -> dam: /10'),
            'area': AREA, 'grid': [1.0, 1.0], 'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        _tmp = out_path[:-4] + '.tmp.npz'   # savez_* 会给没有 .npz 后缀的名字**再补一个**
        np.savez_compressed(_tmp, clim=clim.astype(np.float32), meta=meta)
        os.replace(_tmp, out_path)              # 原子落盘
        fin = np.isfinite(clim)
        log('[%s] 写出 %s  %.2f MB  shape=%s  min=%.3f max=%.3f  n_years=%d'
            % (var, out_path, os.path.getsize(out_path) / 1e6, clim.shape,
               np.nanmin(clim[fin]), np.nanmax(clim[fin]), len(all_years)))
    log('CLIM DONE')


#: S13 追加：**数值域**校验。为什么必须有 —— 本轮实测被咬过一次：
#: 漏套 `conv` 时 `mslp` 写成 **Pa**（97858/103798）、`hgt500` 写成 **m²/s²**（48789/58252），
#: 而 shape/有限点/n_years **全部合格** ⇒ 只查那三维会把它判"合格"并**长期锁死**。
#: 边界取"真数据能过"的宽松值（真实月气候态 mslp 978.6–1038.0、hgt500 497.5–594.0）。
CLIM_DOMAIN = {'mslp': (950.0, 1060.0, 'hPa'), 'hgt500': (480.0, 600.0, 'dam')}


def _clim_is_valid(path, n_years, var=None):
    """"已存在 ⇒ 跳过"前的内容校验（防失败的 NaN 桩 / **错单位**把坏状态锁死）。

    校验四维：`shape` + `有限点≥99%` + `n_years` + **数值域（含换算后单位）**。
    任一不过 ⇒ 重建，并在日志里写出**换算后的实际 min/max 与单位**（一眼看出是 Pa 还是 hPa）。
    """
    if not os.path.exists(path):
        return False
    try:
        z = np.load(path, allow_pickle=True)
        clim = z['clim']
        meta = z['meta'].item() if hasattr(z['meta'], 'item') else dict(z['meta'])
        unit = meta.get('units', '?')
        ok_shape = tuple(clim.shape) == (12, TGT_LATS.size, TGT_LONS.size)
        ok_fin = float(np.isfinite(clim).mean()) >= 0.99
        ok_years = int(meta.get('n_years', 0)) == int(n_years)
        lo, hi, exp_unit = CLIM_DOMAIN.get(var, (float('-inf'), float('inf'), unit))
        cmin = float(np.nanmin(clim)) if ok_fin else float('nan')
        cmax = float(np.nanmax(clim)) if ok_fin else float('nan')
        ok_dom = (lo <= cmin and cmax <= hi)
        if ok_shape and ok_fin and ok_years and ok_dom:
            return True
        log('  [clim] 校验不通过 ⇒ 重建: shape=%s(ok=%s) fin=%.4f n_years=%s(ok=%s) '
            'domain=[%.3f, %.3f]%s 期望 %s∈[%s, %s] %s'
            % (clim.shape, ok_shape, np.isfinite(clim).mean(), meta.get('n_years'), ok_years,
               cmin, cmax, unit, (var or '?'), lo, hi, exp_unit))
        if not ok_dom:
            log('  [clim] ⚠ 数值域越界: 实测 %s∈[%.3f, %.3f]，期望 %s∈[%s, %s] %s '
                '⇒ 常见根因是**漏了单位换算**（msl Pa 未 ÷100 / z 未 ÷9.80665÷10）'
                % ((var or '?'), cmin, cmax, (var or '?'), lo, hi, exp_unit))
    except Exception as e:
        log('  [clim] 读取失败(%s) ⇒ 重建' % e)
    return False


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'clim':
        _y0 = int(sys.argv[2]) if len(sys.argv) > 2 else 1991
        _y1 = int(sys.argv[3]) if len(sys.argv) > 3 else 2020
        build_clim(_y0, _y1)
    else:
        main()
