# simulator/wrf/wrfout_to_fields.py
"""wrfout(NetCDF) → 模拟器 121×360 逐日场(simulator/env/wrfdir/YYYYMMDD.npz)。

变量(wrfout 惯例):
  sst=SKINTEMP/SST(°C)  mslp=SLP(hPa)  rh700(700hPa 层 RH%)
  uv_steer=850/500/300/200 深度加权  shear=|V200-V850|
  vort850 / gpi / ohc(代理)  均由场派生, 公式与 real_source 一致。
F10 渲染新字段(与合成库/real_source 契约一致, 单位同契约):
  u10/v10  直接取 U10/V10(m/s)  u200/v200→uv200(200hPa 层, m/s, 双分量)
  t500     500hPa 层气温(K)     qv850  850hPa 层比湿(g/kg, QVAPOR×1000)
  hgt100   100hPa 层位势高度(dam, HGT m→dam; 无 HGT 变量时整场 NaN 并告警)
  precip24 RAINC+RAINNC 累积量逐日差分(mm/24h, 见 process_wrfout 说明)
区域外为 NaN(WrfFieldAPI 读取时自动用 real_source 全球场回填)。

用法:
  python wrfout_to_fields.py --wrfout simulator/wrf/case/wrfout --period 2026-08-01 --days 10
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # simulator/
sys.path.insert(0, BASE)
from env import field_io as F                       # noqa: E402
from env.real_source import _gpi, _vorticity        # noqa: E402

WRFDIR = os.path.join(os.path.dirname(BASE), 'simulator', 'env', 'wrfdir')
RD_CP = 287.05 / 1004.0     # Rd/Cp
P0 = 100000.0

LEVELS = {'850': 85000.0, '500': 50000.0, '300': 30000.0, '200': 20000.0}
STEER_W = {'850': 0.40, '500': 0.30, '300': 0.20, '200': 0.10}
RH_LEVEL = 70000.0


def _load_wrfout(path: str):
    from netCDF4 import Dataset
    ds = Dataset(path)
    try:
        out = {}
        for v in ('XLAT', 'XLONG', 'U', 'V', 'T', 'P', 'PB', 'PH', 'PHB',
                  'QVAPOR', 'SST', 'SLP', 'PSFC', 'T2', 'Q2', 'U10', 'V10',
                  'HGT', 'RAINC', 'RAINNC'):
            if v in ds.variables:
                out[v] = ds.variables[v][:]
        return out
    finally:
        ds.close()


def _full_pressure(ph, phb, p, pb):
    """完整气压 (Pa)。PH/PHB 为半层(交错 z), 插到整层。"""
    ph_full = ph + phb
    if ph_full.ndim == 4:
        ph_full = ph_full[0]
    ph_mid = (ph_full[:-1, :, :] + ph_full[1:, :, :]) / 2.0   # (nz-1, ny, nx)
    p_full = p + pb
    if p_full.ndim == 4:
        p_full = p_full[0]
    return ph_mid, p_full


def _theta_and_t(t_pert, p_full):
    """T 是扰动位温 → 完整位温 → 气温(K)。p_full: (nz, ny, nx) Pa。"""
    theta = 300.0 + np.asarray(t_pert, dtype=float)
    if theta.ndim == 4:
        theta = theta[0]
    return theta * (p_full / P0) ** RD_CP


def _rh_from_q(q, t, p):
    es = 611.2 * np.exp(17.67 * (t - 273.15) / (t - 29.65))
    qs = 0.622 * es / np.maximum(1.0, p - es)
    return 100.0 * np.clip(q / np.maximum(1e-6, qs), 0.0, 1.2)


def _destagger_x(a):
    return (a[..., :-1] + a[..., 1:]) / 2.0


def _destagger_y(a):
    return (a[:, :, :-1, :] + a[:, :, 1:, :]) / 2.0


def _level_uv(uv_dict, p_full, target):
    """取最接近 target Pa 的模式层 (u, v) 质量点场。v 可为 None。"""
    idx = int(np.argmin(np.abs(p_full[:, p_full.shape[1] // 2, p_full.shape[2] // 2] - target)))
    u = uv_dict['u'][idx]
    v = uv_dict['v']
    return (u, v[idx]) if v is not None else (u, None)


def _regrid_to_1deg(fld, xlat, xlong):
    """模型质量点场 → 121×360 1° 全球场(域外 NaN)。"""
    out = np.full((F.NLAT, F.NLON), np.nan, dtype=np.float32)
    la = F.LATS
    lo = F.LONS
    # 域内掩码(按模型网格行列快速判断: 用 xlat/xlong 极值矩形)
    lat0, lat1 = float(xlat.min()), float(xlat.max())
    lon0, lon1 = float(xlong.min()), float(xlong.max())
    span = 0.5
    sel_la = (la >= lat0 - span) & (la <= lat1 + span)
    sel_lo = (lo >= lon0 - span) & (lo <= lon1 + span)
    if not sel_la.any() or not sel_lo.any():
        return out
    sub_la = la[sel_la]
    sub_lo = lo[sel_lo]
    sub = np.meshgrid(sub_lo, sub_la)
    from scipy.interpolate import griddata
    pts = np.stack([xlong.ravel(), xlat.ravel()], axis=1)
    vals = np.asarray(fld, dtype=float).ravel()
    ok = np.isfinite(vals)
    if not ok.any():
        return out
    q = np.stack([sub[0].ravel(), sub[1].ravel()], axis=1)
    g = griddata(pts[ok], vals[ok], q, method='linear')
    bad = ~np.isfinite(g)
    if bad.any():
        # 平面/退化三角形时 linear 会 NaN → nearest 兜底(网格密, 误差可忽略)
        g[bad] = griddata(pts[ok], vals[ok], q[bad], method='nearest')
    out[np.ix_(sel_la, sel_lo)] = g.reshape(len(sub_la), len(sub_lo))
    return out


def process_wrfout(wrfout_dir: str, start: datetime, days: int,
                   out_dir: str = WRFDIR) -> list:
    import glob
    files = sorted(glob.glob(os.path.join(wrfout_dir, 'wrfout_*')))
    if not files:
        raise RuntimeError(f'未找到 wrfout 文件: {wrfout_dir}')
    os.makedirs(out_dir, exist_ok=True)
    written = []
    domain = None
    # 逐日降水差分跨日状态: pcum_prev=上一自然日最后一帧的累积量(RAINC+RAINNC);
    # has_prev=是否存在前一日(首日无法跨日差分 → 用当日帧内差分)。
    pcum_prev = None
    has_prev = False
    # W7: WRF 从 Start 00Z 积分 Days×24h, 最后一帧落在 Start+Days 日 00Z(第 Days+1 个自然日)
    for i in range(days + 1):
        day = start + timedelta(days=i)
        tag = day.strftime('%Y-%m-%d')
        day_files = [f for f in files if tag in os.path.basename(f)]
        if not day_files:
            print(f'[wrfout] {tag}: 无 wrfout, 跳过')
            continue
        acc = {}
        n = 0
        day_hgt_none = False          # 本日任一帧缺 HGT → hgt100 整场 NaN 并在当帧告警
        # 当日累积量(跨日差分的本日记录): 取当日最后一帧累积值
        pcum_day = None
        pcum_first = None             # 首帧累积值(首日帧内差分用)
        for f in day_files:
            d = _load_wrfout(f)
            # W1: wrfout 可能缺变量(不同物理方案输出集不同), 缺失时告警并跳过该帧
            need = ('PH', 'PHB', 'P', 'PB', 'T', 'U', 'V', 'QVAPOR', 'SLP')
            missing = [v for v in need if v not in d]
            if missing:
                print(f'[wrfout] {os.path.basename(f)}: 缺变量 {missing}, 跳过该帧')
                continue
            ph_mid, p_full = _full_pressure(d['PH'], d['PHB'], d['P'], d['PB'])
            p_full = np.asarray(p_full, dtype=float)
            t_full = _theta_and_t(d['T'], p_full)
            # U 在 x 向交错, V 在 y 向交错 → 各解交错到质量点
            u = _destagger_x(np.asarray(d['U'], dtype=float))[0]      # (nz,ny,nx)
            v = _destagger_y(np.asarray(d['V'], dtype=float))[0]
            qv = np.asarray(d['QVAPOR'], dtype=float)[0]
            rh = _rh_from_q(qv, t_full, p_full)
            uv = {k: _level_uv({'u': u, 'v': v}, p_full, tgt) for k, tgt in LEVELS.items()}
            rh700 = _level_uv({'u': rh, 'v': None}, p_full, RH_LEVEL)[0]
            u850, v850 = uv['850']
            u200, v200 = uv['200']
            tot = sum(STEER_W.values())
            uu = sum(STEER_W[k] * uv[k][0] for k in STEER_W) / tot
            vv = sum(STEER_W[k] * uv[k][1] for k in STEER_W) / tot
            shear = np.hypot(u200 - u850, v200 - v850)
            # W2: SST/SKINTEMP 双缺失时显式告警, 不静默写 NaN 场
            sst_raw = d.get('SST', d.get('SKINTEMP'))
            if sst_raw is None:
                print(f'[wrfout] {os.path.basename(f)}: 无 SST/SKINTEMP, 以 T2m 场回退')
                sst_raw = d.get('T2')
            sst = np.asarray(sst_raw, dtype=float) if sst_raw is not None \
                else np.full_like(u850, 288.15)
            if sst.ndim == 3:
                sst = sst[0]
            sst = np.nan_to_num(sst, nan=288.15) - 273.15
            mslp = np.asarray(d['SLP'], dtype=float)               # hPa
            if mslp.ndim == 3:
                mslp = mslp[0]
            # 云量 → OLR 代理(视频 OLR 层用); 缺 CLDFRA 时用 RH700 代理
            if 'CLDFRA' in d:
                cld = np.asarray(d['CLDFRA'], dtype=float)
                if cld.ndim == 4:
                    cld = cld[0]
                k700 = int(np.argmin(np.abs(
                    p_full[:, p_full.shape[1] // 2, p_full.shape[2] // 2] - RH_LEVEL)))
                olr = 300.0 - 120.0 * np.clip(cld[k700], 0, 1)
            else:
                olr = 300.0 - 1.8 * (np.clip(rh700, 30, 100) - 30.0)
            # 降水(累计 mm): 现 L7 语义改为逐日差分 → precip24(见进程末尾),
            # 这里仅记录该帧累积值用于跨日差分(不再写"累计帧均"的 precip 语义)。
            if 'RAINC' in d and 'RAINNC' in d:
                rc = np.asarray(d['RAINC'], dtype=float)
                rn = np.asarray(d['RAINNC'], dtype=float)
                rc = rc[0] if rc.ndim == 3 else rc
                rn = rn[0] if rn.ndim == 3 else rn
                cumcur = (rc + rn).astype(np.float32)
                pcum_day = cumcur                                # 保存当日最新(最后一)帧累积
                if pcum_first is None:
                    pcum_first = cumcur.copy()
            # ── F10 渲染新字段 ──
            f10f = {}                                             # 新字段的帧级值
            # u10/v10: wrfout 诊断风(质量点, 非交错, 直接取 [0])
            if 'U10' in d and 'V10' in d:
                u10 = np.asarray(d['U10'], dtype=float)
                v10 = np.asarray(d['V10'], dtype=float)
                u10 = u10[0] if u10.ndim == 3 else u10
                v10 = v10[0] if v10.ndim == 3 else v10
                f10f['u10'] = u10
                f10f['v10'] = v10
            # t500 / qv850: 用 p_full 选最接近目标气压的层
            t500 = _level_uv({'u': t_full, 'v': None}, p_full, 50000.0)[0]
            f10f['t500'] = t500
            qv850 = _level_uv({'u': qv * 1000.0, 'v': None}, p_full, 85000.0)[0]
            f10f['qv850'] = qv850
            # hgt100: wrfout 无 HGT 变量 → 整场 NaN 并告警, 不崩溃
            if 'HGT' in d:
                hgt = np.asarray(d['HGT'], dtype=float)
                hgt = hgt[0] if hgt.ndim == 4 else hgt
                hgt100 = _level_uv({'u': hgt, 'v': None}, p_full, 10000.0)[0]
                f10f['hgt100'] = hgt100 / 10.0                    # m → dam
            else:
                if not day_hgt_none:
                    day_hgt_none = True
                    print(f'[wrfout] {os.path.basename(f)}: 无 HGT 变量, '
                          f'hgt100 本日整场为 NaN')
                f10f['hgt100'] = np.full_like(t500, np.nan)
            # uv200: 200hPa 层风(双分量, 与 uv_steer 同构)
            f10f['uv200'] = (u200, v200)
            day_fields = {'sst': sst, 'mslp': mslp, 'uv_steer': (uu, vv),
                          'shear': shear, 'rh700': rh700, 'olr': olr}
            day_fields.update(f10f)
            for k, v in day_fields.items():
                acc.setdefault(k, []).append(v)
            n += 1
        if not n:
            continue
        # ── precip24: 逐日差分(推荐: 日总量 = 当日末帧累计 − 前日末帧累计)──
        # 说明: RAINC/RAINNC 为随积分单调递增的累积量(mm)。precip24 为该自然日(00-18Z
        # 各帧窗口)的 24h 累计增量——= 当日最后一帧累计值 减去 前一日最后一帧累计值
        # (跨日状态), 等价于当日各帧累计差之和; 网格单元取 max(0, ·) 防下洗负值。
        # 首日无"前一日"时, 改用当日首帧→末帧的帧内差分(>>6h 的合理近似);
        # 既无前一日也仅单帧时无法差分 → 整场 NaN。
        if pcum_day is not None:
            if has_prev and pcum_prev is not None:
                day_total = np.maximum(0.0, pcum_day - pcum_prev)
            elif pcum_first is not None and not np.allclose(pcum_first, pcum_day):
                day_total = np.maximum(0.0, pcum_day - pcum_first)
            else:
                day_total = np.full_like(pcum_day, np.nan, dtype=np.float32)
            acc.setdefault('precip24', []).append(day_total)
        # 更新跨日状态(本日末帧累计 → 下日前一帧基准)
        if pcum_day is not None:
            pcum_prev = pcum_day
        has_prev = True
        # 逐变量平均 → 1° 网格(坐标取自该日首帧唯一一次读取)
        first_d = _load_wrfout(day_files[0])
        xlat = first_d['XLAT'][0]
        xlong = first_d['XLONG'][0]
        if domain is None:
            domain = {'lat0': float(xlat.min()), 'lat1': float(xlat.max()),
                      'lon0': float(xlong.min()), 'lon1': float(xlong.max())}
        fields = {}
        for k, lst in acc.items():
            m = np.mean(lst, axis=0)
            if k in ('uv_steer', 'uv200'):                        # 双分量(2,·,·)
                fields[k] = np.stack([_regrid_to_1deg(m[0], xlat, xlong),
                                      _regrid_to_1deg(m[1], xlat, xlong)])
            else:
                fields[k] = _regrid_to_1deg(m, xlat, xlong)
        sst = fields['sst']
        fields['vort850'] = _vorticity(fields['uv_steer'][0], fields['uv_steer'][1])
        fields['gpi'] = _gpi(sst, fields['shear'], fields['rh700'], fields['vort850'])
        fields['ohc'] = np.clip(62.0 + 7.5 * np.maximum(0.0, sst - 26.0), 20.0, 170.0)
        # W3: 域外统一置 NaN(否则 _vorticity 的 nan_to_num 与 _gpi 的 where 会把
        # 域外写成 0 → WrfFieldAPI 判定"有值"不回填 real_source, 破坏"域外 NaN"契约)。
        oob = np.isnan(fields['sst'])
        if oob.any():
            fields['vort850'][oob] = np.nan
            fields['gpi'][oob] = np.nan
        path = os.path.join(out_dir, day.strftime('%Y%m%d') + '.npz')
        np.savez(path, **fields)
        written.append(path)
        print(f'[wrfout] {tag}: {len(day_files)} 帧 → {os.path.basename(path)}')
    if domain:
        with open(os.path.join(out_dir, 'domain.json'), 'w', encoding='utf-8') as f:
            json.dump(domain, f)
    return written


def main():
    ap = argparse.ArgumentParser(description='wrfout → 121×360 场')
    ap.add_argument('--wrfout', required=True, help='wrfout 目录')
    ap.add_argument('--period', required=True, help='模拟起始 YYYY-MM-DD')
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--out', default=WRFDIR)
    args = ap.parse_args()
    written = process_wrfout(args.wrfout, datetime.strptime(args.period, '%Y-%m-%d'),
                             args.days, args.out)
    print(f'完成: {len(written)} 天 → {args.out}')


if __name__ == '__main__':
    main()
