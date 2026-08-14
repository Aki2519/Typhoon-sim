# simulator/env/param_fields.py
"""F6 副高/ITCZ/季风槽参数化(独立模块,逐月,日插值)。

系数一律由场库历史数据标定(回归+留一法交叉验证),不许手写常数:
  - 副高脊线纬度 = 季节气候态 + a·ONI + b·PDO + 随机游走
  - ITCZ 中心纬度 = 季节迁移 + ENSO 调制;强度 = 气候态 + IOD 项
  - 季风槽位置/强度 = 季节迁移 + ENSO 调制
"""
from __future__ import annotations
import os
import numpy as np
from typing import Dict, List, Optional, Tuple

from . import field_io as F
from . import build_library as BL
from simulator import modes as M

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
PARAM_DIR = F.PARAM_DIR


def _extract_ridge(mslp: np.ndarray) -> Tuple[float, float, float]:
    """从 MSLP 场提取副高脊线纬度/强度/东西延伸(标定用):
    北半球 20-40N 带内纬向最大海压的纬度与中心强度,经度范围 = 高压区经度跨度。"""
    lat = F.LATS
    lon = F.LONS
    band = (lat >= 15) & (lat <= 45)
    slat = lat[band]
    m = mslp[band, :]
    # 全 NaN / 部分 NaN 防御: 输入无效或某纬度行全 NaN 时, 排除这些行后再
    # argmax, 避免 argmax 落在首个 NaN 行上产生非法纬度/NaN 强度。
    if not np.isfinite(m).any():
        return 0.0, 0.0, 0.0
    finite = np.isfinite(m)
    row_max = np.where(finite.any(axis=1),
                       np.max(np.where(finite, m, -np.inf), axis=1),
                       np.nan)
    good = np.isfinite(row_max)
    if not good.any():
        return 0.0, 0.0, 0.0
    lat_idx = int(np.flatnonzero(good)[int(np.argmax(row_max[good]))])
    ridge_lat = float(slat[lat_idx])
    strength = float(row_max[lat_idx])
    # 东西延伸: 脊线纬度上 > 1015 hPa 的经度跨度
    high = m[lat_idx] > 1015.0
    if high.any():
        spans = np.diff(np.where(np.diff(np.concatenate(([0], high, [0]))))[0])[::2]
        lon_range = float(spans.max() * 1.0) if len(spans) else 0.0
    else:
        lon_range = 0.0
    return ridge_lat, strength, lon_range


def _extract_itcz(mslp: np.ndarray) -> Tuple[float, float, float]:
    """从海压槽线提取 ITCZ 中心纬度/强度/半宽(标定用),返回 (lat, strength, lon_range)。
    20°S-20°N 带内纬向平均海压最低纬度与槽深;半宽 = 槽深半高处纬度跨度。
    R2-1: 卷积前边缘填充(zeros 填充使带边缘值被拉低 2/3, argmin 恒落边缘)。"""
    lat = F.LATS
    band = (lat >= -20) & (lat <= 20)
    slat = lat[band]
    a = mslp[band, :]
    # 全 NaN / 部分 NaN 防御: 只统计至少含一个有限格点的纬度行,
    # 避免 NaN 行让 argmin/min/max 返回非法槽位/NaN 强度与半宽。
    if not np.isfinite(a).any():
        return 0.0, 0.0, 8.0
    finite = np.isfinite(a)
    cnt = finite.sum(axis=1)
    row_mean = np.divide(np.where(finite, a, 0.0).sum(axis=1), cnt,
                         out=np.full_like(cnt, np.nan, dtype=np.float64),
                         where=cnt > 0)
    profile = row_mean
    good = np.isfinite(profile)
    if not good.any():
        return 0.0, 0.0, 8.0
    slat_sub = slat[good]
    p = profile[good]
    # 3 点平滑,防逐月跳变(对有限子集边缘 nearest 填充)
    if len(p) >= 3:
        padded = np.pad(p, 1, mode='edge')
        p = np.convolve(padded, np.ones(3) / 3.0, mode='valid')
    center = float(slat_sub[int(np.argmin(p))])
    strength = float(np.nanmean(a) - p.min())
    # 半宽: 槽深半高处的纬度跨度
    half = p.min() + (p.max() - p.min()) * 0.5
    above = np.where(p <= half)[0]
    width = float(slat_sub[above[-1]] - slat_sub[above[0]]) * 0.5 if len(above) > 1 else 8.0
    return center, max(0.0, strength), max(3.0, width)


def _extract_trough(mslp: np.ndarray) -> Tuple[float, float, float]:
    """季风槽: 0-20N 带内纬向最低海压的纬度/强度/经度中心,返回 (lat, strength, lon_range)。
    D12/R2-3: 强度 max(0,·) 截断;提取带扩至 0N(冬季槽低至 ~1.7N)。"""
    lat = F.LATS
    band = (lat >= 0) & (lat <= 20)
    slat = lat[band]
    m = mslp[band, :]
    # 全 NaN / 部分 NaN 防御: 排除全 NaN 纬度行后再 argmin(避免返回 NaN 纬度)。
    finite = np.isfinite(m)
    row_min = np.where(finite.any(axis=1),
                       np.min(np.where(finite, m, np.inf), axis=1),
                       np.nan)
    good = np.isfinite(row_min)
    if not good.any():
        return 0.0, 0.0, 0.0
    lat_idx = int(np.flatnonzero(good)[int(np.argmin(row_min[good]))])
    trough_lat = float(slat[lat_idx])
    strength = float(max(0.0, 1013.0 - row_min[lat_idx]))
    row = m[lat_idx]
    row_good = np.flatnonzero(np.isfinite(row))
    lon_center = float(F.LONS[row_good[int(np.argmin(row[row_good]))]])
    return trough_lat, strength, lon_center


def calibrate(years: range = BL.LIB_YEARS, lib_dir: str = F.LIB_DIR,
              param_dir: Optional[str] = None) -> Dict[str, dict]:
    """从场库历史数据提取逐月参数序列并回归标定系数。
    param_dir: 输出目录(默认共享 PARAM_DIR;测试请传临时目录避免污染)。"""
    os.makedirs(PARAM_DIR, exist_ok=True)
    cal = {}
    for name, extract in (('ridge', _extract_ridge), ('itcz', _extract_itcz),
                          ('monsoon_trough', _extract_trough)):
        rows = []
        for y in years:
            for mo in range(1, 13):
                if name == 'itcz':
                    mslp = F.load_monthly_field('mslp', y, lib_dir)
                    if mslp is None:
                        continue
                    r = extract(mslp[mo - 1])
                elif name == 'ridge':
                    mslp = F.load_monthly_field('mslp', y, lib_dir)
                    if mslp is None:
                        continue
                    r = extract(mslp[mo - 1])
                else:
                    mslp = F.load_monthly_field('mslp', y, lib_dir)
                    if mslp is None:
                        continue
                    r = extract(mslp[mo - 1])
                rows.append((y, mo, r[0], r[1], r[2]))
        cal[name] = rows
    # 回归标定: lat = a0 + a1·cos(季节) + a2·ONI + a3·PDO
    coeffs = {}
    for name, rows in cal.items():
        X = []
        yv = []
        for y, mo, lat, s1, s2 in rows:
            mod = _modes((y, mo))
            X.append([1.0, np.cos(2 * np.pi * (mo - 8) / 12.0),
                      mod.get('oni', 0.0), mod.get('pdo', 0.0)])
            yv.append(lat)
        if len(yv) < 12:
            coeffs[name] = None
            continue
        Xa = np.array(X)
        ya = np.array(yv)
        coef, *_ = np.linalg.lstsq(Xa, ya, rcond=None)
        # 留一法交叉验证误差
        errs = []
        for i in range(len(ya)):
            mask = np.ones(len(ya), bool)
            mask[i] = False
            c = np.linalg.lstsq(Xa[mask], ya[mask], rcond=None)[0]
            errs.append(abs(Xa[i] @ c - ya[i]))
        coeffs[name] = {'coef': coef.tolist(), 'cv_rmse': float(np.sqrt(np.mean(
            np.array(errs) ** 2)))}
        # 落盘参数化序列
        _save_param(name, cal[name], coeffs[name], param_dir or PARAM_DIR)
    return {'rows': cal, 'coeffs': coeffs}


def _modes(ym: Tuple[int, int]) -> Dict[str, float]:
    out = {}
    for k, path in (('oni', 'nino34'), ('pdo', 'pdo'), ('dmi', 'dmi')):
        rows = M.load_mode_series(path)
        d = {(y, mo): v for y, mo, v in rows}
        v = d.get(ym)
        if v is not None:
            out[k] = v / M.MODES[path]['std']
    return out


def _save_param(name: str, rows: List[tuple], coeff: dict,
                param_dir: Optional[str] = None) -> None:
    param_dir = param_dir or PARAM_DIR
    os.makedirs(param_dir, exist_ok=True)
    dt = np.dtype([('year', 'i4'), ('month', 'i4'),
                   ('lat', 'f4'), ('strength', 'f4'), ('lon_range', 'f4')])
    arr = np.array([(r[0], r[1], r[2], r[3], r[4]) for r in rows], dtype=dt)
    np.savez(os.path.join(param_dir, f"{name}.npz"),
             years=arr['year'] * 12 + arr['month'],
             rows=arr, coef=np.array(coeff['coef']),
             cv_rmse=np.array([coeff['cv_rmse']]))


def forecast_param(name: str, ym: Tuple[int, int],
                   coeffs: Optional[dict] = None) -> Optional[dict]:
    """按标定系数预测目标月参数(未标定时返回 None → 由 FieldAPI 用库序列)。"""
    if not coeffs or coeffs.get(name) is None:
        return None
    c = coeffs[name]['coef']
    mod = _modes(ym)
    lat = (c[0] + c[1] * np.cos(2 * np.pi * (ym[1] - 8) / 12.0)
           + c[2] * mod.get('oni', 0.0) + c[3] * mod.get('pdo', 0.0))
    return {'lat': float(lat), 'strength': 0.0, 'lon_range': 0.0}


if __name__ == '__main__':
    result = calibrate()
    for name, c in result['coeffs'].items():
        print(f"  {name}: cv_rmse={c['cv_rmse']:.2f}°" if c else f"  {name}: 未标定")
