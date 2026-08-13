# simulator/typhoons/kzc.py
"""KZC / Chavas 风压模型(移植自 G:\\气象\\工具\\风压对应\\风压对应.py)。

- KZC(Courtney & Knaff 2009 改编,1900hurricane 实现): Δp = f(Vmax, 移速, R34, 纬度)
- Chavas(Knaff-Klotzbach 2025): Vmax(m/s) → Pmin(hPa)
- 标定: 用 miwu(1979-2025,含气压)+ 自评/NHC 风格目录的热带段 (w,p) 散点
  回归 KZC 系数;样本不足维持原文献系数并标注"文献系数"。
"""
from __future__ import annotations
import math
from typing import List, Optional, Tuple

import numpy as np

OMEGA = 7.2921e-5
_CK = {"b0": -6.60, "bV2": -0.0127, "bS": -5.506, "bSV": 109.013}

# KZC 文献系数(1900hurricane 实现;系数表已按公式符号展开,
# 公式本身为负号: -12.587·S - 0.483·lat / -6.8·S)
_KZC = {
    'a': [23.286, -0.483, 24.254, 12.587, 0.483],   # 高纬: dp = a0 + a1·Vsrm - (Vsrm/a2)² - a3·S - a4·lat
    'b': [5.962, -0.267, 18.26, 6.8],                # 低纬: dp = b0 + b1·Vsrm - (Vsrm/b2)² - b3·S
}
_KZC_SOURCE = 'literature'    # 'literature' | 'calibrated'


def kzc_dp(v: float, c: float, r34: float, lat: float) -> float:
    """KZC 气压差 hPa。v: kt;c: 移速 kt;r34: nm;lat: °(南半球取绝对值)。"""
    if lat < 0:
        lat = -lat
    # E8: 标定幂律(dp = α·v^β,高纬加 -a4·lat)优先
    if _KZC_SOURCE == 'calibrated' and '_pow' in _KZC:
        a, beta = _KZC['_pow']
        dp = a * v ** beta
        if lat >= 18.0 or v > 95:
            dp = dp - _KZC['a'][4] * lat
        if v < 34.0:
            dp = dp * max(0.0, v / 34.0)
        return dp
    Vsrm = v - 1.5 * c ** 0.63
    V500 = r34 / 9.0 - 3.0
    Rmax = 66.785 - 0.09102 * v + 1.0619 * (lat - 25.0)
    X = 0.1147 + 0.0055 * v - 0.001 * (lat - 25.0)
    V500c = v * (Rmax / 500.0) ** X
    S = V500 / V500c
    if S < 0.4:
        S = 0.4
    if lat >= 18.0 or v > 95:
        a = _KZC['a']
        dp = a[0] + a[1] * Vsrm - (Vsrm / a[2]) ** 2 - a[3] * S - a[4] * lat
    else:
        b = _KZC['b']
        dp = b[0] + b[1] * Vsrm - (Vsrm / b[2]) ** 2 - b[3] * S
    # E12: 弱风段(v<34kt,KZC 经验域外)线性收窄到 0,
    # 避免 20kt 弱风算出 +25hPa 的荒谬气压(被 1012 钳制掩盖)
    if v < 34.0:
        dp = dp * max(0.0, v / 34.0)
    return dp


def kzc_pmin(v: float, c: float, r34: float, lat: float, oci: float,
             penv_shift: float = 2.0) -> float:
    """KZC: 风速 → 中心气压 hPa。"""
    return penv_shift + oci + kzc_dp(v, c, r34, lat)


def _ck_half_fr(lat: float, r34_km: float) -> float:
    # E7: 南半球取绝对值(科氏参数符号与半球无关,只取大小)
    f = 2.0 * OMEGA * math.sin(math.radians(abs(lat)))
    return 0.5 * f * (r34_km * 1000.0)


def chavas_pmin(vmax_ms: float, vtrans_ms: float, r34_km: float,
                lat: float, penv: float) -> Optional[float]:
    """Chavas-Knaff-Klotzbach(2025) Eq.5: Vmax(m/s) → Pmin(hPa)。"""
    vbar = vmax_ms - 0.55 * vtrans_ms
    if vbar <= 0:
        return None
    s = _ck_half_fr(lat, r34_km)
    dp = _CK['b0'] + _CK['bV2'] * vbar ** 2 + _CK['bS'] * s + _CK['bSV'] * s / vbar
    return penv + dp


def min_pressure(vmax_kt: float, r34_nm: float, oci_hpa: float,
                 lat: float = 15.0, penv_shift: float = 2.0,
                 speed_kt: float = 10.0, model: str = 'kzc') -> float:
    """统一接口。model: 'kzc' | 'chavas'(默认 KZC,与回放工具一致)。"""
    if model == 'chavas':
        vmax_ms = vmax_kt * 0.514444
        vtrans_ms = speed_kt * 0.514444
        r34_km = r34_nm * 1.852
        p = chavas_pmin(vmax_ms, vtrans_ms, r34_km, lat, oci_hpa + penv_shift)
        return p if p is not None else oci_hpa + penv_shift
    return kzc_pmin(vmax_kt, speed_kt, r34_nm, lat, oci_hpa, penv_shift)


def calibrate(wp_pairs: Optional[List[Tuple[int, int]]] = None) -> dict:
    """标定 KZC 系数(回归): 用 (w, p) 散点拟合低纬/高纬分支的 dp 公式。
    样本不足时维持文献系数,标注 _KZC_SOURCE='literature'。"""
    global _KZC, _KZC_SOURCE
    if not wp_pairs or len(wp_pairs) < 30:
        _KZC_SOURCE = 'literature'
        return {'source': _KZC_SOURCE, 'n': 0, 'rmse': None}
    ws = np.array([p[0] for p in wp_pairs], dtype=float)
    ps = np.array([p[1] for p in wp_pairs], dtype=float)
    dp = 1013.0 - ps
    ok = (dp > 5) & (ws >= 35)
    if ok.sum() < 30:
        _KZC_SOURCE = 'literature'
        return {'source': _KZC_SOURCE, 'n': int(ok.sum()), 'rmse': None}
    # 简化回归: dp = α·v^β + γ·lat(不含 R34/移速的独立系数,保持原公式结构)
    vs = ws[ok]
    ds = dp[ok]
    try:
        beta, loga = np.polyfit(np.log(vs), np.log(ds), 1)
        # E8: β 必须参与公式(否则标定无效)。用标定幂律替换 dp 主项:
        #   dp = α·v^β - a4·lat(高纬) / α·v^β(低纬), 系数按纬度分支取
        a = math.exp(loga)
        _KZC['a'] = [0.0, 0.0, 1.0, 0.0, _KZC['a'][4]]
        _KZC['b'] = [0.0, 0.0, 1.0, 0.0]
        _KZC['_pow'] = (a, beta)          # 标定幂律: dp = α·v^β
        pred = a * vs ** beta
        rmse = float(np.sqrt(np.mean((pred - ds) ** 2)))
        _KZC_SOURCE = 'calibrated'
        return {'source': _KZC_SOURCE, 'n': int(ok.sum()), 'rmse': rmse,
                'alpha': a, 'beta': float(beta)}
    except (ValueError, np.linalg.LinAlgError):
        _KZC_SOURCE = 'literature'
        return {'source': _KZC_SOURCE, 'n': int(ok.sum()), 'rmse': None}


def source() -> str:
    return _KZC_SOURCE
