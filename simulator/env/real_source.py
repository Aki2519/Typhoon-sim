# simulator/env/real_source.py
"""内核 A 真实大气场提供器(免注册、免 key、纯 pip): 用现成全球模式产品替代 WRF。

双源(均为 Open-Meteo 免费接口, 无 API key, 4 层规则路由):
  - IFS (ECMWF 9km, 2017-至今):  全变量 —— SST/MSLP/RH700/850-200hPa 风/云量。
    近 92 天用 forecast API(含 past_days), 更早用 historical-forecast API。
  - ERA5 (0.25°, 1940-至今):     仅表面变量(T2m/RH2m/10-100m 风/MSLP/云量)。
    高空/海洋变量用"真实表面数据 + 明确标注的物理代理"派生(见 _proxy_*)。

派生场与现有 FieldAPI 契约完全一致(1°×1° 网格, 60°S-60°N, 121×360):
  - sst       IFS: 实况 SST; ERA5: T2m 海上代理(海面 2m 气温≈SST±1.5°C)
  - shear     IFS: |V200-V850|; ERA5: |V100m-V10m| 切变代理
  - uv_steer  IFS: 850/500/300/200 深度加权; ERA5: 100m 风引导代理
  - rh700     IFS: 实况 RH700; ERA5: RH2m 代理
  - ohc       无真实来源: SST 驱动代理公式(暖水柱热焓, 只影响 PI 调制)
  - vort850/gpi 由上述风场/SST/切变按标准公式派生
  - mslp      双源均实况; 云量 → 视频 OLR/反射率近似层

F10 渲染新字段(与合成库/契约一致, 单位: u10/v10 m/s, precip24 mm/24h,
t500 K, qv850 g/kg, hgt100 dam, uv200 m/s 双分量):
  - u10/v10=wind_speed/direction_10m(ERA5 实况; IFS 提供 10m 风)
  - precip24=precipitation 日累计差分(Open-Meteo 累计 → 24h 总量, 可能已含 0)
  - t500=temperature_500hPa  qv850=RH850→比湿(需 temperature_850hPa)
  - hgt100=geopotential_height_100hPa(m→dam)  uv200=200hPa 双分量风
  缺失/获取失败时该字段整场不清零(get_field 返回 None), 渲染侧回退。

下载策略: 2° 网格抓取(61×180 点, 500 点/批)后双线性上采样到 121×360,
逐日缓存到 env/realdir/。首次抓取某日 ≈ 22 个并发请求; 之后全部离线复用。
"""
from __future__ import annotations
import os
import json
import math
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import field_io as F

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ENV_DIR, 'realdir')

# ── 接口端点(Open-Meteo, 免注册)──
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'                 # IFS 实时+近 92 天
HIST_FCST_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast'  # IFS 2017-至今
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'           # ERA5 1940-至今

IFS_BEGIN = datetime(2017, 1, 1)        # IFS 数据起点(Open-Meteo 集成)
PAST_DAYS_LIMIT = 92                    # forecast API 可回溯天数
FETCH_GRID_STEP = 3.0                   # 抓取网格(°), 上采样到 1°; 3°≈20 请求/日
BATCH = 250                             # 每请求点数上限(URL 长度限制 ~8KB, 250×2 坐标 ≈5.5KB)

# IFS 六层风(与 build_library 引导权重一致)
STEER_LEVELS = ('850', '500', '300', '200')
STEER_WEIGHTS = (0.40, 0.30, 0.20, 0.10)

# ERA5 代理: 高空风速 → 引导风比例(100m 风 ≈ 850hPa 风速的 ~0.75, 经验系数)
ERA5_STEER_SCALE = 0.85

# 每点每层抓取的变量表(IFS 全量 / ERA5 表面)
# 注: historical-forecast API(2017-近3月)不提供 SST → 用 temperature_2m 代理(海上 T2m≈SST)
# F10 渲染字段契约(units 见下方 _derive):
#    u10/v10=wind_speed/direction_10m  precip24=precipitation(日累计)
#    t500=temperature_500hPa  qv850=relative_humidity_850hPa+temperature_850hPa→比湿
#    hgt100=geopotential_height_100hPa(m→dam)  uv200=wind_speed/direction_200hPa
F10_VARS = ['wind_speed_10m', 'wind_direction_10m', 'precipitation',
            'temperature_500hPa', 'temperature_850hPa',
            'relative_humidity_850hPa', 'geopotential_height_100hPa',
            'wind_speed_200hPa', 'wind_direction_200hPa']
IFS_VARS = (['sea_surface_temperature', 'pressure_msl', 'relative_humidity_700hPa',
             'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high']
            + [f'wind_speed_{p}hPa' for p in STEER_LEVELS]
            + [f'wind_direction_{p}hPa' for p in STEER_LEVELS]
            + list(F10_VARS))
IFS_HIST_VARS = (['temperature_2m', 'pressure_msl', 'relative_humidity_700hPa',
                  'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high']
                 + [f'wind_speed_{p}hPa' for p in STEER_LEVELS]
                 + [f'wind_direction_{p}hPa' for p in STEER_LEVELS]
                 + list(F10_VARS))
ERA5_VARS = ['temperature_2m', 'relative_humidity_2m', 'pressure_msl',
             'wind_speed_10m', 'wind_direction_10m',
             'wind_speed_100m', 'wind_direction_100m',
             'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high'
             ] + list(F10_VARS)

# 比湿换算: 850hPa 层气压常数(Pa)
_QV850_P = 85000.0

_HOURLY = 'hourly'
_HTTP_TIMEOUT = 30
_reach_cache: Optional[bool] = None
_reach_t = 0.0


def _date_tuple(d) -> Tuple[int, int, int]:
    if hasattr(d, 'year'):
        return d.year, d.month, d.day
    return int(d[0]), int(d[1]), int(d[2])


def _fetch_json(url: str, params: Dict[str, str], retries: int = 3) -> Optional[dict]:
    qs = urllib.parse.urlencode(params)
    full = f'{url}?{qs}'
    last = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(full, headers={'User-Agent': 'typhoon-sim/1.0'})
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            last = e
            # BUG-7: 4xx 为参数错误(非限流), 重试无意义, 立即放弃
            if 400 <= e.code < 500 and e.code != 429:
                return None
            if e.code == 429:
                time.sleep(2.0 * (attempt + 1))     # 限流: 指数退避
            else:
                time.sleep(0.5 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    return None


def available(timeout: int = 8) -> bool:
    """轻量连通性探测(结果缓存 60s)。内核 A 可用性 = 能访问任一接口。"""
    global _reach_cache, _reach_t
    if _reach_cache is not None and time.time() - _reach_t < 60:
        return _reach_cache
    ok = False
    for url in (ARCHIVE_URL, FORECAST_URL):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'typhoon-sim/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            continue
    _reach_cache, _reach_t = ok, time.time()
    return ok


class RealFieldAPI:
    """真实大气场提供器。接口与 FieldAPI 一致:
    get_field(var, date) -> (121,360) 或 (2,121,360); get_param; get_anomaly。"""

    def __init__(self, cache_dir: str = CACHE_DIR, grid_step: float = FETCH_GRID_STEP,
                 batch: int = BATCH):
        self.cache_dir = cache_dir
        self.grid_step = grid_step
        self.batch = batch
        self._cache: Dict[Tuple[str, str], np.ndarray] = {}
        self._failed: set = set()          # 本次进程失败日期(负缓存)
        self.fetch_stats = {'days': 0, 'requests': 0, 'points': 0, 'failed': 0}

    # ── 网格 ──

    def _target_grid(self) -> Tuple[np.ndarray, np.ndarray]:
        """抓取网格: -60..60 纬度, 0..360 经度, 步长 grid_step(经度闭合 360)。"""
        step = self.grid_step
        lats = np.arange(-60.0, 60.0 + 1e-9, step)
        lons = np.arange(0.0, 360.0 + 1e-9, step)
        return lats, lons

    # ── 日期路由 ──

    def _source_for(self, dt: date) -> str:
        now = date.today()
        if dt > now + timedelta(days=16):
            # BUG-4: 未来超过 forecast API 上限(约 16 天)时显式失败而非静默空场
            raise ValueError(f'日期 {dt} 超出真实大气预报范围(未来 >16 天)')
        if dt >= now - timedelta(days=PAST_DAYS_LIMIT - 5):
            return 'ifs_now'          # forecast API(past_days + forecast_days)
        if dt >= IFS_BEGIN.date():
            return 'ifs_hist'         # historical-forecast API
        return 'era5'                 # archive API(仅表面)

    # ── 逐日抓取(整日 6h 采样)──

    def _fetch_day(self, dt: date) -> Dict[str, np.ndarray]:
        """抓取某日全部变量 → 按抓取网格排布。多坐标响应为按请求顺序的 dict 列表。"""
        src = self._source_for(dt)
        lat_list, lon_list = self._target_grid()
        lat_idx = {float(la): i for i, la in enumerate(lat_list)}
        # Open-Meteo 经度范围 -180..180; 0..360 网格换算后请求
        pts = [(float(la), (float(lo) + 180.0) % 360.0 - 180.0)
               for la in lat_list for lo in lon_list]
        start, end = dt.isoformat(), (dt + timedelta(days=1)).isoformat()
        today = date.today()
        base_params = {'timezone': 'UTC', 'cell_selection': 'sea'}
        if src == 'ifs_now':
            if dt < today:
                base_params['past_days'] = str((today - dt).days)
                base_params['forecast_days'] = '1'
            else:
                base_params['past_days'] = '0'
                base_params['forecast_days'] = str((dt - today).days + 1)
        else:
            base_params.update({'start_date': start, 'end_date': end})
        url = FORECAST_URL if src == 'ifs_now' else (HIST_FCST_URL if src == 'ifs_hist'
                                                     else ARCHIVE_URL)
        vars_ = IFS_HIST_VARS if src == 'ifs_hist' else (IFS_VARS if src != 'era5'
                                                         else ERA5_VARS)

        def fetch_block(block_pts: List[Tuple[float, float]]) -> Optional[list]:
            p = dict(base_params)
            p['latitude'] = ','.join(f'{x[0]:.2f}' for x in block_pts)
            p['longitude'] = ','.join(f'{x[1]:.2f}' for x in block_pts)
            p['hourly'] = ','.join(vars_)
            r = _fetch_json(url, p)
            if r is None:
                return None
            return r if isinstance(r, list) else [r]   # 多坐标 → dict 列表

        blocks = [pts[i:i + self.batch] for i in range(0, len(pts), self.batch)]
        self.fetch_stats['requests'] += len(blocks)
        self.fetch_stats['points'] += len(pts)
        with ThreadPoolExecutor(max_workers=min(16, max(1, len(blocks)))) as ex:
            block_res = list(ex.map(fetch_block, blocks))

        ok_blocks = sum(1 for b in block_res if b)
        if ok_blocks < len(blocks):
            self.fetch_stats['failed'] += len(blocks) - ok_blocks
            if getattr(self, '_warned_fetch', False) is False:
                self._warned_fetch = True
                print(f'[real_source] 警告: {dt} 部分请求失败 '
                      f'({ok_blocks}/{len(blocks)} 批成功, 可能限流/断网; 已缓存成功部分)')

        out: Dict[str, np.ndarray] = {}
        day_str = dt.isoformat()
        for bi, br in enumerate(block_res):
            if not br:
                continue
            base = bi * self.batch
            for j, (la, lo) in enumerate(blocks[bi]):
                if j >= len(br) or not isinstance(br[j], dict):
                    continue
                rows = br[j].get('hourly') or {}
                times = rows.get('time', [])
                if not times:
                    continue
                # BUG-2: 仅取目标日期的 0/6/12/18 时次
                # (ifs_now 的 past_days 响应覆盖 N+2 天, 之前取全部时次均值 → 滑动平均)
                idxs = [i for i, t in enumerate(times)
                        if len(t) >= 13 and t[:10] == day_str
                        and int(t[11:13]) in (0, 6, 12, 18)]
                if not idxs:
                    idxs = [i for i, t in enumerate(times)
                            if len(t) >= 13 and t[:10] == day_str]
                if not idxs:
                    continue
                for var in vars_:
                    col = rows.get(var)
                    if col is None or len(col) < len(times):
                        continue
                    if var not in out:
                        out[var] = np.full((len(lat_list), len(lon_list)),
                                           np.nan, dtype=np.float32)
                    if var == 'precipitation':
                        # 降水: Open-Meteo 每小时 precipitation 为"前 1 小时降水量"
                        # (mm, 逐小时量, 非跨日跑和累计)。24h 总量 = 目标当日全部时次之和
                        # (0-23 逐小时求和 ≈ 日总量), 而非"末时次−首时次"的累计差
                        # (对逐小时量该差分≈0, 无意义)。
                        # 仅取目标当日全部时次, 不复用 idxs 的 0/6/12/18 子集。
                        pidx = [q for q, t in enumerate(times)
                                if len(t) >= 13 and t[:10] == day_str
                                and q < len(col) and col[q] is not None
                                and not np.isnan(col[q])]
                        if len(pidx) >= 2:
                            olo = (base + j) % len(lon_list)
                            out[var][lat_idx[la], olo] = float(
                                max(0.0, sum(float(col[q]) for q in pidx)))
                        continue
                    ok = [col[i] for i in idxs if i < len(col) and col[i] is not None]
                    if ok:
                        # BUG-1: pts 中 lon 已换算成 Open-Meteo 的 -180..180 表示,
                        # 该值不能直接索引 0..360 的 lon_idx(西半球/东经≥180 会 KeyError)。
                        # 由全局点序((la,lo) 按 [for la for lo] 展平)反推原始 0..360 网格列。
                        olo = (base + j) % len(lon_list)
                        out[var][lat_idx[la], olo] = float(np.mean(ok))
        self.fetch_stats['days'] += 1
        return out

    # ── 派生场 ──

    def _derive(self, raw: Dict[str, np.ndarray], src: str) -> Dict[str, np.ndarray]:
        """原始点数据 → 模拟/渲染所需 9 变量(抓取网格)。"""
        out: Dict[str, np.ndarray] = {}
        if src == 'era5':
            t2 = raw.get('temperature_2m')
            if t2 is not None:
                out['sst'] = t2                     # 海上 T2m ≈ SST 代理
            out['rh700'] = raw.get('relative_humidity_2m')
            out['mslp'] = raw.get('pressure_msl')
            u10 = _wind_uv(raw.get('wind_speed_10m'), raw.get('wind_direction_10m'))
            u100 = _wind_uv(raw.get('wind_speed_100m'), raw.get('wind_direction_100m'))
            if u100 is not None:
                out['uv_steer'] = np.stack(
                    [u100[0] * ERA5_STEER_SCALE, u100[1] * ERA5_STEER_SCALE])
            if u10 is not None and u100 is not None:
                du = u100[0] - u10[0]
                dv = u100[1] - u10[1]
                out['shear'] = np.hypot(du, dv)
        else:
            if src == 'ifs_hist':
                out['sst'] = raw.get('temperature_2m')    # IFS 历史无 SST → T2m 代理
            else:
                out['sst'] = raw.get('sea_surface_temperature')
            out['rh700'] = raw.get('relative_humidity_700hPa')
            out['mslp'] = raw.get('pressure_msl')
            uvs = [_wind_uv(raw.get(f'wind_speed_{p}hPa'), raw.get(f'wind_direction_{p}hPa'))
                   for p in STEER_LEVELS]
            if all(u is not None for u in uvs):
                tot = sum(STEER_WEIGHTS)
                uu = sum(w * u[0] for u, w in zip(uvs, STEER_WEIGHTS)) / tot
                vv = sum(w * u[1] for u, w in zip(uvs, STEER_WEIGHTS)) / tot
                out['uv_steer'] = np.stack([uu, vv])
            if uvs[0] is not None and uvs[3] is not None:
                out['shear'] = np.hypot(uvs[3][0] - uvs[0][0], uvs[3][1] - uvs[0][1])
        # ── F10 渲染新字段(双源通用)──
        # 关键约束: 任一底层变量缺失/获取失败 → 该字段检出即整场 NaN(or 不产出,
        # get_field 返回 None), 绝不写 0 值场; 渲染侧据"有值/NaN"判定回退。
        u10v = _wind_uv(raw.get('wind_speed_10m'), raw.get('wind_direction_10m'))
        if u10v is not None:
            out['u10'] = u10v[0]
            out['v10'] = u10v[1]
        pc = raw.get('precipitation')
        if pc is not None:
            out['precip24'] = np.maximum(0.0, np.asarray(pc, dtype=float))
        t500 = raw.get('temperature_500hPa')
        if t500 is not None:
            # BUG-F10: Open-Meteo 未指定 temperature_unit 时默认返回 Celsius,
            # 而契约要求 t500 单位为 K → +273.15。
            out['t500'] = np.asarray(t500, dtype=float) + 273.15
        rh850 = raw.get('relative_humidity_850hPa')
        t850 = raw.get('temperature_850hPa')
        if rh850 is not None and t850 is not None:
            # BUG-F10: temperature_850hPa 同为 Celsius, _rh_to_q 需 Kelvin 输入。
            out['qv850'] = _rh_to_q(rh850, np.asarray(t850, dtype=float) + 273.15,
                                    _QV850_P)     # g/kg
        hgt100 = raw.get('geopotential_height_100hPa')
        if hgt100 is not None:
            out['hgt100'] = np.asarray(hgt100, dtype=float) / 10.0     # m → dam
        uv2 = _wind_uv(raw.get('wind_speed_200hPa'), raw.get('wind_direction_200hPa'))
        if uv2 is not None:
            out['uv200'] = np.stack([uv2[0], uv2[1]])           # (2, ny, nx)
        # NaN 填充: 陆地/缺测格点用最近有效值(避免派生场/参数提取全链 NaN)
        for k in list(out):
            v = out[k]
            if v is None or getattr(v, 'ndim', 0) < 2:
                # BUG-3: 变量缺失/无效时删除而不是保留 None(下游 _fill_nan 崩溃)
                out.pop(k)
                continue
            out[k] = _fill_nan(v)
        # 云量 → OLR 代理(视频 OLR 层用, 全暗=0)
        cc = raw.get('cloud_cover_mid')
        if cc is not None:
            out['olr'] = 260.0 - 90.0 * np.clip(_fill_nan(cc), 0, 100) / 100.0
        # vort850 / gpi
        if out.get('uv_steer') is not None:
            u850 = out['uv_steer'][0]
            v850 = out['uv_steer'][1]
            lats, _lons = self._target_grid()
            out['vort850'] = _vorticity(u850, v850, lats,
                                        self.grid_step, self.grid_step)
        if all(k in out for k in ('sst', 'shear', 'rh700')) and 'vort850' in out:
            out['gpi'] = _gpi(out['sst'], out['shear'], out['rh700'], out['vort850'])
        # ohc 代理(暖水柱热焓, 只参与 S>0.55 时的 PI 调制)
        if out.get('sst') is not None:
            out['ohc'] = np.clip(62.0 + 7.5 * np.maximum(0.0, out['sst'] - 26.0),
                                 20.0, 170.0)
        return out

    # ── 主查询接口 ──

    def _day_fields(self, dt: date) -> Dict[str, np.ndarray]:
        key = dt.isoformat()
        cached = self._cache.get(('day', key))
        if cached is not None:
            return cached
        if key in self._failed:
            return {}                     # 本次进程内失败不再重试(负缓存)
        try:
            src = self._source_for(dt)
        except ValueError as ex:
            print(f'[real_source] 跳过 {key}: {ex}')
            self._failed.add(key)
            return {}
        raw = self._fetch_day(dt)
        if not raw:
            self._failed.add(key)         # 失败不缓存(否则离线后永远空)
            return {}
        day = self._derive(raw, src)
        day['_source'] = np.array([src])  # 供标注
        self._save_cache(dt, day)
        self._cache_put(key, day)
        return day

    def _cache_put(self, key: str, day: Dict[str, np.ndarray]) -> None:
        """写入进程内日缓存并维持 LRU 上限(命中/写入都刷新最近使用)。
        旧实现只有 _day_fields 一处设上限, 磁盘命中路径(_load_day)完全绕过。"""
        ck = ('day', key)
        if ck in self._cache:
            self._cache.pop(ck)          # 重插到末尾 = 最近使用
        elif len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[ck] = day

    def _load_day(self, dt: date) -> Dict[str, np.ndarray]:
        key = dt.isoformat()
        day = self._cache.get(('day', key))
        if day is None:
            day = self._load_cache(dt)
            if day is None:
                day = self._day_fields(dt)
            else:
                self._cache_put(key, day)
        else:
            self._cache_put(key, day)    # 命中刷新 LRU 顺序
        return day

    def get_field(self, var: str, dt0) -> Optional[np.ndarray]:
        y, mo, d = _date_tuple(dt0)
        dt = date(y, mo, d)
        day = self._load_day(dt)
        fld = day.get(var)
        if fld is None:
            return None
        return _upsample(fld, F.NLAT, F.NLON)

    def get_anomaly(self, var: str, dt0) -> Optional[np.ndarray]:
        f = self.get_field(var, dt0)
        if f is None:
            return None
        cl = F.load_climatology(var)
        if cl is None or cl['clim'] is None:
            return None
        _, mo, _ = _date_tuple(dt0)
        return f - cl['clim'][mo - 1]

    def get_param(self, name: str, dt0) -> Optional[dict]:
        """从真实 MSLP 场提取副高/ITCZ/季风槽参数(与 param_fields 口径一致)。"""
        mslp = self.get_field('mslp', dt0)
        if mslp is None:
            return None
        from . import param_fields as PF
        extract = {'ridge': PF._extract_ridge, 'itcz': PF._extract_itcz,
                   'monsoon_trough': PF._extract_trough}.get(name)
        if extract is None:
            return None
        lat, strength, lon_range = extract(mslp)
        return {'lat': lat, 'strength': strength, 'lon_range': lon_range}

    def source_of(self, dt) -> str:
        y, mo, d = _date_tuple(dt)
        return self._source_for(date(y, mo, d))

    # ── 磁盘缓存 ──

    def _cache_path(self, dt: date) -> str:
        return os.path.join(self.cache_dir, f'{dt.strftime("%Y%m%d")}.npz')

    def _save_cache(self, dt: date, day: Dict[str, np.ndarray]) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            arrs = {k: v for k, v in day.items() if isinstance(v, np.ndarray)}
            if arrs:
                np.savez(self._cache_path(dt), **arrs)
        except Exception:
            pass

    def _load_cache(self, dt: date) -> Optional[Dict[str, np.ndarray]]:
        p = self._cache_path(dt)
        if not os.path.exists(p):
            return None
        try:
            with np.load(p, allow_pickle=False) as z:
                return dict(z)
        except Exception:
            return None


# ════════════════════ 派生工具 ════════════════════

def _fill_nan(fld) -> np.ndarray:
    """NaN 格点用沿经度方向最近有效值填充(逐纬度); 全 NaN 行用全球均值。
    3D(如 uv_steer 双分量)沿首维逐面处理。"""
    if fld is None:
        return np.full((121, 360), 0.0, dtype=np.float32)
    a = np.array(fld, dtype=float)
    if a.ndim == 0:
        return np.full((121, 360), float(a), dtype=np.float32)
    if a.ndim == 3:
        return np.stack([_fill_nan(a[i]) for i in range(a.shape[0])]).astype(np.float32)
    if not np.isnan(a).any():
        return a
    for i in range(a.shape[0]):
        row = a[i]
        if np.isnan(row).all():
            good = a[~np.isnan(a)]
            row[:] = float(np.mean(good)) if len(good) else 0.0
            continue
        # 环形最近邻(经度首尾相接)
        vi = np.flatnonzero(~np.isnan(row))
        for j in np.flatnonzero(np.isnan(row)):
            d = np.minimum((j - vi) % len(row), (vi - j) % len(row))
            row[j] = row[vi[int(np.argmin(d))]]
    return a.astype(np.float32)


def _wind_uv(speed, direction) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """风速(km/h)+风向(气象学, 来向) → (u, v) m/s。缺失格点保留 NaN。"""
    if speed is None or direction is None:
        return None
    s = np.array(speed, dtype=float) / 3.6            # km/h → m/s
    d = np.array(direction, dtype=float)
    rad = np.deg2rad(d)
    u = -s * np.sin(rad)
    v = -s * np.cos(rad)
    return u, v


def _rh_to_q(rh, t, p) -> np.ndarray:
    """相对湿度(%RH) → 比湿(g/kg), 用 Magnus 饱和公式在气压 p(Pa) 处近似。
    输入/输出缺测(NaN)格点保留 NaN。"""
    t = np.asarray(t, dtype=float)
    rh = np.asarray(rh, dtype=float)
    es = 611.2 * np.exp(17.67 * (t - 273.15) / (t - 29.65))     # Pa
    qs = 0.622 * es / np.maximum(1.0, p - 0.378 * es)            # kg/kg
    return np.maximum(0.0, (rh / 100.0) * qs) * 1000.0           # g/kg


def _vorticity(u: np.ndarray, v: np.ndarray,
               lat: Optional[np.ndarray] = None,
               dlat: float = 1.0, dlon: float = 1.0) -> np.ndarray:
    """相对涡度 ζ = (1/(a·cosφ))∂v/∂λ - (1/a)∂u/∂φ (s^-1)。
    格距必须按实际抓取网格传入(默认 1° 保持旧行为); 缺 cosφ 会把高纬
    东西向梯度按赤道尺度高估。"""
    u = np.nan_to_num(u, nan=0.0)
    v = np.nan_to_num(v, nan=0.0)
    deg_m = 111320.0
    dv_dlon = np.gradient(v, axis=1) / (deg_m * dlon)
    du_dlat = np.gradient(u, axis=0) / (deg_m * dlat)
    if lat is not None and len(lat) == u.shape[0]:
        coslat = np.cos(np.deg2rad(np.asarray(lat, dtype=float)))[:, None]
        dv_dlon = dv_dlon / np.maximum(coslat, 1e-6)
    return dv_dlon - du_dlat


def _gpi(sst, shear, rh, vort) -> np.ndarray:
    v850 = np.abs(vort) * 1e5
    sst_a = np.maximum(0.0, sst - 26.0)
    shear_a = np.nan_to_num(shear, nan=10.0) / 1.0
    rh_a = np.nan_to_num(rh, nan=60.0)
    gp = (1 + 0.1 * v850) ** 3 * (rh_a / 50.0) ** 3 * \
        (sst_a ** 4) * (1 + 0.1 * shear_a) ** -1
    return np.where(sst > 26.0, np.maximum(gp, 0), 0.0)


def _upsample(fld: np.ndarray, nlat: int, nlon: int) -> np.ndarray:
    """(2° → 1°) 双线性上采样; 已是目标网格时原样返回。NaN 先填充避免扩散。"""
    if fld.shape == (nlat, nlon):
        return fld
    from scipy.ndimage import zoom
    if fld.ndim == 2:
        # BUG-6: nan=np.nan 是空操作, 改为最近邻填充后再上采样
        z = zoom(_fill_nan(fld),
                 (nlat / fld.shape[0], nlon / fld.shape[1]), order=1)
        return z.astype(np.float32)
    if fld.ndim == 3:
        return np.stack([_upsample(fld[i], nlat, nlon) for i in range(fld.shape[0])])
    return fld
