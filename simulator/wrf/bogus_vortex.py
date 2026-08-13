# simulator/wrf/bogus_vortex.py
"""向 wrfinput 注入台风涡旋(bogus), 供架空/未来台风使用。

在 wrfinput_d01 的 U/V/T/QVAPOR/PH 场叠加一个轴对称涡旋:
  - 风: Rankine 涡旋(V = Vmax·(r/RMW) 内圈, V = Vmax·(RMW/r)^0.6 外圈),
    叠加后保证中心风速 → Vmax(kt)。
  - 暖核: 温度异常 ΔT = 4·exp(-(r/R_T)²)·exp(-((z-zc)/zc)²), zc≈5km。
  - 湿度: 低层 QVAPOR 增强。
  - 气压: 通过 PH 位势异常(间接)或直接调整 P? 直接调 P 会与 T 不一致;
    常规 bogus 只改风/温/湿, 气压由 WRF 初始化时自我调整。
用法:
  python bogus_vortex.py --wrfinput WRF/run/wrfinput_d01 \
      --lat 18 --lon 128 --vmax 70 --rmw 30
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
# B1: 不再顶层导入 wrfout_to_fields(其导入链含 scipy/env 依赖; WSL 侧
# PYTHONPATH 不含 simulator 包时 ModuleNotFoundError 会终止 run_wrf.sh 整条管线)。
# WF = None  # noqa: E402

RD = 287.05
G = 9.81
KT = 0.514444


def _load_4d(d, name):
    a = np.asarray(d[name], dtype=float)
    return a[0] if a.ndim == 4 else a


def _destagger_u(a):
    return (a[..., :-1] + a[..., 1:]) / 2.0


def _destagger_v(a):
    return (a[:, :, :-1, :] + a[:, :, 1:, :]) / 2.0


def inject_bogus(path: str, lat: float, lon: float, vmax_kt: float,
                 rmw_km: float = 30.0, p_anom: float = 25.0) -> str:
    """在 wrfinput 中注入涡旋。修改 U/V(交错网格)/T/QVAPOR。"""
    from netCDF4 import Dataset
    vmax = vmax_kt * KT
    rmw = rmw_km * 1000.0

    with Dataset(path, 'r+') as ds:
        xlat = np.asarray(ds.variables['XLAT'][0], dtype=float)
        xlong = np.asarray(ds.variables['XLONG'][0], dtype=float)
        u = np.asarray(ds.variables['U'][0], dtype=float)     # (nz, ny, nx+1)
        v = np.asarray(ds.variables['V'][0], dtype=float)     # (nz, ny+1, nx)
        t = np.asarray(ds.variables['T'][0], dtype=float)     # 扰动位温
        qv = np.asarray(ds.variables['QVAPOR'][0], dtype=float)
        phb = np.asarray(ds.variables['PHB'][0], dtype=float)  # (nz+1, ny, nx)
        nz, ny, nx = u.shape[0], u.shape[1], u.shape[2] - 1

        # 质量点坐标(XLAT/XLONG 即质量点坐标)
        la_m = xlat
        lo_m = xlong
        # U/V 交错点坐标(U 仅 x 向交错: (ny, nx+1); V 仅 y 向交错: (ny+1, nx))
        la_u = np.pad(xlat, ((0, 0), (0, 1)), mode='edge')
        lo_u = np.pad((xlong[:, :-1] + xlong[:, 1:]) / 2.0, ((0, 0), (1, 1)), mode='edge')
        la_v = np.pad((xlat[:-1, :] + xlat[1:, :]) / 2.0, ((1, 1), (0, 0)), mode='edge')
        lo_v = np.pad(xlong, ((0, 1), (0, 0)), mode='edge')

        # 各层高度(z, m)
        z_mid = (phb[:-1, :, :] + phb[1:, :, :]) / 2.0 / G

        def _dist2(la_g, lo_g):
            dlo = ((lo_g - lon + 180) % 360) - 180
            return np.hypot((la_g - lat) * 111000.0,
                            dlo * 111000.0 * np.cos(np.radians(lat)))

        # ── 风场(Rankine, 叠加到原场)──
        for k in range(nz):
            # 气旋式(北半球): ang 从正东起沿逆时针, u=-v·sin, v=+v·cos。
            # (旧式 atan2(dlo, dla) + pi/2 使 V 分量反号 → 反气旋位相, 已修正)
            r = _dist2(la_u, lo_u)
            vtan = np.where(r <= rmw, vmax * r / max(rmw, 1.0),
                            vmax * (rmw / np.maximum(r, 1.0)) ** 0.6)
            ang = np.arctan2(la_u - lat, lo_u - lon)
            u[k] += -vtan * np.sin(ang)
            r = _dist2(la_v, lo_v)
            vtan = np.where(r <= rmw, vmax * r / max(rmw, 1.0),
                            vmax * (rmw / np.maximum(r, 1.0)) ** 0.6)
            ang = np.arctan2(la_v - lat, lo_v - lon)
            v[k] += vtan * np.cos(ang)

        # ── 暖核(位温异常, 高度衰减)──
        r_m = _dist2(la_m, lo_m)
        zc = 5000.0
        for k in range(nz):
            zk = z_mid[k]
            dT = 4.0 * np.exp(-(r_m / (rmw * 3.0)) ** 2) * np.exp(-((zk - zc) / zc) ** 2)
            t[k] += dT

        # ── 低层湿度增强 ──
        for k in range(min(6, nz)):
            qv[k] = qv[k] + 0.004 * np.exp(-(r_m / (rmw * 4.0)) ** 2)
        qv = np.clip(qv, 0.0, 0.05)

        # 写回
        ds.variables['U'][0] = u
        ds.variables['V'][0] = v
        ds.variables['T'][0] = t
        ds.variables['QVAPOR'][0] = qv
    print(f'[bogus] 已注入涡旋: ({lat},{lon}) vmax={vmax_kt}kt RMW={rmw_km}km '
          f'→ {path}')
    return path


def main():
    ap = argparse.ArgumentParser(description='wrfinput 台风涡旋注入')
    ap.add_argument('--wrfinput', required=True)
    ap.add_argument('--lat', type=float, required=True)
    ap.add_argument('--lon', type=float, required=True)
    ap.add_argument('--vmax', type=float, default=70.0, help='初始最大风速(kt)')
    ap.add_argument('--rmw', type=float, default=30.0, help='最大风速半径(km)')
    args = ap.parse_args()
    inject_bogus(args.wrfinput, args.lat, args.lon, args.vmax, args.rmw)


if __name__ == '__main__':
    main()
