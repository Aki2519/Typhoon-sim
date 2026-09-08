# simulator/env/downloader.py
"""F6/A1 数据下载管线: ERA5(月平均)/ORAS5 抓取,重试/断点/缺失月记录。

真实数据需 CDS API 凭证(cdsapi)与 ORAS5 访问权限。未配置凭证时:
  - fetch_* 返回 None 并记录缺失月(不静默合成);
  - build_library(use_real=True) 将报错列出全部缺失月,绝不静默降级合成。
合成库仅作为 use_real=False 的显式选项。
"""
from __future__ import annotations
import os
import json
import socket
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

socket.setdefaulttimeout(20.0)    # 下载网络超时,避免无网络时挂起(A1)

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(ENV_DIR, 'raw')
MISSING_LOG = os.path.join(ENV_DIR, 'missing_months.json')

# 变量 → (数据源, ERA5 变量名/ORAS5 变量)
SOURCES = {
    'sst':     ('era5', 'sea_surface_temperature'),
    'ohc':     ('oras5', 'thetao'),
    'mslp':    ('era5', 'mean_sea_level_pressure'),
    'u850':    ('era5', 'u_component_of_wind_850'),
    'v850':    ('era5', 'v_component_of_wind_850'),
    'u500':    ('era5', 'u_component_of_wind_500'),
    'v500':    ('era5', 'v_component_of_wind_500'),
    'u300':    ('era5', 'u_component_of_wind_300'),
    'v300':    ('era5', 'v_component_of_wind_300'),
    'u200':    ('era5', 'u_component_of_wind_200'),
    'v200':    ('era5', 'v_component_of_wind_200'),
    'rh700':   ('era5', 'relative_humidity_700'),
}
# OHC 深度层(ORAS5 标准层,m)
OHC_DEPTHS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200,
              250, 300, 400, 500, 600, 700]
RHO = 1025.0      # kg/m³
CP = 3985.0       # J/(kg·K)


def load_missing() -> List[str]:
    if os.path.exists(MISSING_LOG):
        try:
            with open(MISSING_LOG, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


_HAS_CRED = None          # 有凭证标志(一次性检查; None=未检查, True=有凭证, False=无凭证)
_FAILED_COUNT = {}        # BUG-24: 按 key 计失败次数(≥3 才快速失败, 单次抖动不毒化全库)


def record_missing(key: str) -> None:
    missing = load_missing()
    if key not in missing:
        missing.append(key)
        os.makedirs(ENV_DIR, exist_ok=True)
        with open(MISSING_LOG, 'w', encoding='utf-8') as f:
            json.dump(sorted(missing), f, ensure_ascii=False, indent=1)


def _has_credential_config() -> bool:
    """离线判断是否存在可用的 CDS 凭证源(不碰网络):
    ~/.cdsapirc 或 CDSAPI_KEY/URL 环境变量。"""
    if os.environ.get('CDSAPI_KEY') and os.environ.get('CDSAPI_URL'):
        return True
    try:
        return os.path.exists(os.path.join(os.path.expanduser('~'), '.cdsapirc'))
    except (OSError, ValueError):
        return False


def check_credentials() -> bool:
    """检查 CDS 凭证是否就绪(仅 import cdsapi 不算配置凭证, 还要有 key 源)。
    一次性缓存到 _HAS_CRED, 避免每次调用反复探测文件系统。"""
    global _HAS_CRED
    if _HAS_CRED is not None:
        return _HAS_CRED
    try:
        import cdsapi
        _HAS_CRED = _has_credential_config()
    except ImportError:
        _HAS_CRED = False
    return _HAS_CRED


def _valid_download(path: str, min_bytes: int = 256) -> bool:
    """下载产物校验(离线): 极小尺寸兜底 + 文件头探测。
    CDS 以 format=netcdf 返回 → 期望 NETCDF3/4(HDF5)魔数;
    半截/损坏/HTML 错误页等非场文件不会被误判成功, 断点续传不丢损坏缓存。
    R4: 阈值由 1MB 降至 256B——ERA5 1°×1° 单变量月平均 netCDF 常仅几十~几百 KB,
    旧阈值会把这类合法小文件误杀(判为损坏→反复重下→误记缺失)。真正区分合法/损坏
    的是文件头魔数(CDF/HDF)而非尺寸, 尺寸兜底只拦零字节/极短截断(<256B)。"""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < min_bytes:
            return False
        with open(path, 'rb') as f:
            head = f.read(8)
        return head[:3] == b'CDF' or head[:4] == b'\x89HDF'
    except OSError:
        return False


def fetch_era5_month(var: str, year: int, month: int,
                     retries: int = 3) -> Optional[str]:
    """ERA5 月平均下载(CDS API)。返回 NetCDF 路径或 None(记缺失)。
    首次失败后快速失败(避免无网络/无配额时逐月挂起,A1)。"""
    key = f"{var}_{year}_{month:02d}"
    out = os.path.join(RAW_DIR, f"{key}.nc")
    # BUG-23: 半截/损坏残文件不判成功(_valid_download 校验头与尺寸),
    # 否则断点续传缓存永远损坏
    if _valid_download(out):
        return out
    if os.path.exists(out):
        try:
            os.remove(out)          # 残留残文件 → 删除重下
        except OSError:
            pass
    if not check_credentials():
        if not getattr(fetch_era5_month, '_no_credentials_logged', False):
            fetch_era5_month._no_credentials_logged = True
            record_missing('NO_CDS_CREDENTIALS')
        # D6: 快速失败也逐月记录缺失(重启后缺失清单完整)
        record_missing(key)
        return None
    if _FAILED_COUNT.get('era5', 0) >= 3:
        record_missing(key)      # D6
        return None
    try:
        import cdsapi
        src, era5_var = SOURCES.get(var, ('', ''))
        if src != 'era5':
            record_missing(key)
            return None
        os.makedirs(RAW_DIR, exist_ok=True)
        c = cdsapi.Client()
        request = {
            'product_type': 'monthly_averaged_reanalysis',
            'variable': era5_var,
            'year': [str(year)],
            'month': [f"{month:02d}"],
            'time': '00:00',
            'area': [60, 0, -60, 360],
            'grid': [1.0, 1.0],
            'format': 'netcdf',
        }
        # D13: 真正的重试循环(断点续传=文件已通过校验即复用)
        last_exc = None
        for attempt in range(max(1, retries)):
            try:
                c.retrieve('reanalysis-era5-single-levels-monthly-means',
                           request, out)
                if _valid_download(out):
                    return out
                last_exc = RuntimeError('下载文件为空/过小/损坏')
                try:
                    os.remove(out)      # 残留残文件清理
                except OSError:
                    pass
            except Exception as e:
                last_exc = e
        raise last_exc
    except Exception as e:
        _FAILED_COUNT['era5'] = _FAILED_COUNT.get('era5', 0) + 1
        record_missing(f'ERA5_FAILED_{var}: {type(e).__name__}')
        return None


def fetch_oras5_month(year: int, month: int, retries: int = 3) -> Optional[str]:
    """ORAS5 海温(0.25°→1°)下载(3D 温度场,含深度层)。
    D7: 尚未接入实际下载管线(需 ORAS5 存储/凭证方案)——显式告警而非伪装凭证问题。"""
    key = f"ohc_{year}_{month:02d}"
    out = os.path.join(RAW_DIR, f"{key}.nc")
    # R4: 与 ERA5 口径一致——损坏/残文件不判成功(避免断点续传永久复用坏缓存)。
    if _valid_download(out):
        return out
    if os.path.exists(out):
        try:
            os.remove(out)          # 残留残文件 → 删除重下
        except OSError:
            pass
    if not getattr(fetch_oras5_month, '_warned', False):
        fetch_oras5_month._warned = True
        print("[downloader] 警告: ORAS5 下载管线未接入(需要 ORAS5 存储/凭证方案),"
              "OHC 真实数据不可用;按 F6 预案可用 ERA5 分层海温替代或标注缺失")
    record_missing(key)     # ORAS5 访问需另行配置(存储/凭证),未配置即记缺失
    return None


def ohc_from_temperature(t3d, depths=OHC_DEPTHS) -> float:
    """热焓(kJ/cm²): Σ ρ·cp·T·Δz,0-700m 深度积分。t3d: (nz, lat, lon)°C。
    层数与深度表不一致时按实际层数积分(避免索引越界)。"""
    nz = t3d.shape[0]
    dz = []
    for i in range(min(nz, len(depths))):
        if i == 0:
            dz.append(depths[1] - depths[0])
        elif i == len(depths) - 1 or i == nz - 1:
            dz.append(depths[i] - depths[i - 1])
        else:
            dz.append((depths[i + 1] - depths[i - 1]) / 2.0)
    ohc = np.zeros(t3d.shape[1:], dtype=float)
    for i, t in enumerate(t3d[:len(dz)]):
        # OHC 只积分超过 26°C 的暖水柱(标准定义); 积分总温度会把量级放大
        # 3 个数量级, 与库合同(5~180 kJ/cm²)不符
        ohc += RHO * CP * np.maximum(t - 26.0, 0.0) * dz[i]
    return ohc / 1e7      # J/m² → kJ/cm² (1 kJ/cm² = 1e7 J/m²)


def fetch_range(var: str, years: range) -> Dict[str, str]:
    out = {}
    for y in years:
        for m in range(1, 13):
            if var == 'ohc':
                p = fetch_oras5_month(y, m)
            else:
                p = fetch_era5_month(var, y, m)
            if p:
                out[f"{y}_{m:02d}"] = p
    return out
