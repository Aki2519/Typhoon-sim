# simulator/wrf/track_vortex.py
"""从 wrfout 追踪台风涡旋: 每 6h 输出路径/中心气压/最大风速/r34。

方法:
  - 中心: SLP 最低点(时间连续性: 以上一帧中心为初猜, ±3° 窗内搜索,
    双线性抛物线拟合到亚格点精度)。
  - 强度: 中心 SLP; VMAX = 距中心 400km 内 850hPa 最大风速(WRF 30km
    分辨率下强度系统性偏弱, 如实输出)。
  - R34: 850hPa 风场沿径向 34kt 半径(4 象限平均)。

输出与模拟器状态序列兼容:
  [{t:'YYYYMMDDHH', la, lo, p, w, r34, st, nb, ew, stage}, ...]
用法:
  python track_vortex.py --wrfout case/wrfout --start 2026-08-01 --days 10
                         [--guess-lat 20 --guess-lon 130]
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from simulator.wrf import wrfout_to_fields as WF   # noqa: E402

KT = 0.514444          # kt → m/s
R_MAX_SEARCH = 3.0     # 搜索窗半径(度)
R_VMAX = 400.0         # VMAX 搜索半径(km)
R34_LEVEL = 34.0       # kt


def _parse_fname(fn: str):
    """wrfout_d01_2026-08-01_000000 或 wrfout_d01_2026-08-01_00:00:00 → datetime。"""
    m = re.search(r'(\d{4}-\d{2}-\d{2})[_ ](\d{2})[:_-]?(\d{2})', fn)
    if not m:
        return None
    try:
        return datetime.strptime(f'{m.group(1)}_{m.group(2)}{m.group(3)}',
                                 '%Y-%m-%d_%H%M')
    except ValueError:
        return None


def _load_2d(d, name):
    a = np.asarray(d[name], dtype=float)
    return a[0] if a.ndim == 3 else a


def _slp_field(d) -> np.ndarray:
    return _load_2d(d, 'SLP')


def _time_squeeze(a):
    """去掉最前的时间维(若存在), 返回 3D 场; 与 _load_2d 口径一致。"""
    a = np.asarray(a, dtype=float)
    return a[0] if a.ndim == 4 else a


def _destag3(a, axis):
    """对已去时间维的 (nz, …) 网格在 axis∈{'x','y'} 上去交错(交错格→质量格)。"""
    if axis == 'x':
        return (a[..., :-1] + a[..., 1:]) / 2.0      # U: (nz, ny, nx+1) → (nz, ny, nx)
    return (a[:, :-1, :] + a[:, 1:, :]) / 2.0        # V: (nz, ny+1, nx) → (nz, ny, nx)


def _uv850(d):
    """850hPa 附近层 u/v 质量点场。"""
    ph_mid, p_full = WF._full_pressure(d['PH'], d['PHB'], d['P'], d['PB'])
    p_full = np.asarray(p_full, dtype=float)
    # 先 d['U']/d['V'](可能是 4D 带时间)去时间维再本地去交错, 不依赖
    # wrfout_to_fields._destagger_* 的散维假设(其 _destagger_y 硬编码 4D 下标)。
    u = _destag3(_time_squeeze(d['U']), 'x')
    v = _destag3(_time_squeeze(d['V']), 'y')
    k = int(np.argmin(np.abs(p_full[:, p_full.shape[1] // 2,
                                  p_full.shape[2] // 2] - 85000.0)))
    return u[k], v[k]


def _subgrid_min(fld, xlat, xlong, la, lo, half=1.0):
    """以 (la,lo) 为中心 ±half° 窗口内 SLP 最低点(亚格点抛物线拟合)。"""
    mask = ((np.abs(xlat - la) <= half) & (np.abs(((xlong - lo + 180) % 360) - 180) <= half))
    if not mask.any():
        return None, None
    v = fld[mask]
    j, i = np.where(mask)
    k = int(np.argmin(v))
    y, x = j[k], i[k]
    la0, lo0 = float(xlat[y, x]), float(xlong[y, x])

    # T1: 一维抛物线拟合(沿纬/经方向各 3 点)。
    # 纬度方向沿行变化 → 固定列 x 取 fld[d1,x]/fld[y,x]/fld[d2,x];
    # 经度方向沿列变化 → 固定行 y 取 fld[y,d1]/fld[y,x]/fld[y,d2]。
    # 原实现 lat 方向取 fld[d1,y](用行号 y 当列), 亚格点纬度系统性偏移。
    def _fit(center, limit, axis, fixed):
        d1 = center - 1 if center > 0 else center
        d2 = center + 1 if center < limit - 1 else center
        if axis == 0:
            f0, f1, f2 = fld[d1, fixed], fld[center, fixed], fld[d2, fixed]
        else:
            f0, f1, f2 = fld[fixed, d1], fld[fixed, center], fld[fixed, d2]
        den = f0 - 2 * f1 + f2
        if abs(den) < 1e-9:
            return 0.0
        return 0.5 * (f0 - f2) / den

    dx = _fit(x, fld.shape[1], 1, y)
    dy = _fit(y, fld.shape[0], 0, x)
    if x + 1 < fld.shape[1]:
        lo1 = lo0 + dx * (float(xlong[y, x + 1]) - float(xlong[y, x]))
    else:
        lo1 = lo0 + dx * (float(xlong[y, x]) - float(xlong[y, x - 1])) if x > 0 else lo0
    if y + 1 < fld.shape[0]:
        la1 = la0 + dy * (float(xlat[y + 1, x]) - float(xlat[y, x]))
    else:
        la1 = la0 + dy * (float(xlat[y, x]) - float(xlat[y - 1, x])) if y > 0 else la0
    return la1, lo1


def _dist_km(la1, lo1, la2, lo2):
    dlo = ((lo2 - lo1 + 180) % 360) - 180
    return np.hypot((la2 - la1) * 111.0, dlo * 111.0 * np.cos(np.radians((la1 + la2) / 2)))


def _vmax_r34(uv, xlat, xlong, la, lo):
    """距中心 R_VMAX km 内 850hPa 最大风速 + 34kt 半径(4 象限平均)。"""
    u, v = uv
    spd = np.hypot(u, v)
    la_m = xlat - la
    lo_m = ((xlong - lo + 180) % 360) - 180
    d = np.hypot(la_m * 111.0, lo_m * 111.0 * np.cos(np.radians(la)))
    near = d <= R_VMAX
    if not near.any():
        return 0.0, None
    vmax_ms = float(np.max(spd[near]))
    # T3: 先找最大风速半径(RMW), 射线从 RMW 起向外搜索首个 <34kt 点;
    # 原实现从 20km 起搜, 弱台风在 20km 处已 <34kt → r34 被低估到 20km
    rmax_km = float(d[near][int(np.argmax(spd[near]))]) if near.any() else 30.0
    radii = []
    for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        ray = np.linspace(max(20.0, rmax_km), 600.0, 60)
        for r in ray:
            tla = la + r / 111.0 * np.cos(ang)
            tlo = lo + r / (111.0 * np.cos(np.radians(la))) * np.sin(ang)
            d2 = np.hypot((xlat - tla) * 111.0,
                          (((xlong - tlo + 180) % 360) - 180) * 111.0
                          * np.cos(np.radians(tla)))
            k = np.unravel_index(int(np.argmin(d2)), spd.shape)
            if spd[k] < R34_LEVEL * KT:
                radii.append(r)
                break
    r34_km = float(np.mean(radii)) if radii else None
    # T2: 全链路 r34 口径为海里(nm)(run.py 信息面板/station_series_at 均按 nm)
    r34 = r34_km / 1.852 if r34_km is not None else None
    return vmax_ms / KT, r34


def track_wrfout(wrfout_dir: str, start: datetime, days: int,
                 guess: tuple = None) -> list:
    files = sorted(glob.glob(os.path.join(wrfout_dir, 'wrfout_*')))
    if not files:
        raise RuntimeError(f'未找到 wrfout: {wrfout_dir}')
    t0 = _parse_fname(os.path.basename(files[0]))
    if t0 is None:
        t0 = start
    pts = []
    la, lo = guess if guess else (20.0, 135.0)
    first = True
    for f in files:
        t = _parse_fname(os.path.basename(f))
        if t is None:
            continue
        if t < start or t >= start + timedelta(days=days):
            continue
        d = WF._load_wrfout(f)
        # T5: 与 wrfout_to_fields.W1 同口径——缺核心变量(气压/风)时该帧无法追踪,
        # 跳过而非让 _uv850 的裸 d['PH'] 抛 KeyError 终止整条追踪管线。
        _need = ('SLP', 'U', 'V', 'PH', 'PHB', 'P', 'PB')
        if any(v not in d for v in _need):
            print(f'[track] {os.path.basename(f)}: 缺核心变量, 跳过该帧')
            continue
        xlat = _load_2d(d, 'XLAT')
        xlong = _load_2d(d, 'XLONG')
        slp = _slp_field(d)
        if first or la is None:
            # 首帧: 全局 SLP 最低点(有初猜则在其 ±10° 内, 避免远处错误低压)
            if guess is not None:
                m = mask_of(xlat, xlong, guess[0], guess[1], 10.0)
                if not m.any():
                    m = np.ones_like(slp, dtype=bool)
                js, is_ = np.where(m)
                k = int(np.argmin(slp[m]))
                la, lo = float(xlat[js[k], is_[k]]), float(xlong[js[k], is_[k]])
            else:
                j, i = np.unravel_index(int(np.argmin(slp)), slp.shape)
                la, lo = float(xlat[j, i]), float(xlong[j, i])
            first = False
        # 多轮精化(每轮缩窗)
        for half in (3.0, 1.5, 0.6):
            nla, nlo = _subgrid_min(slp, xlat, xlong, la, lo, half)
            if nla is not None:
                la, lo = nla, nlo
        vmax, r34 = _vmax_r34(_uv850(d), xlat, xlong, la, lo)
        m = mask_of(xlat, xlong, la, lo, 0.8)
        p = float(np.min(slp[m])) if m.any() else float(slp.min())
        stage = '巅峰' if vmax >= 90 else ('增强' if vmax >= 34 else '生成')
        pts.append({'t': t.strftime('%Y%m%d%H'), 'la': round(la, 2),
                    'lo': round(lo % 360.0, 2), 'p': int(round(p)),
                    'w': int(round(vmax)), 'r34': int(r34) if r34 else 60,
                    'st': 'TY' if vmax >= 64 else ('TS' if vmax >= 34 else 'TD'),
                    'nb': 2, 'ew': 0, 'stage': stage})
        print(f"[track] {pts[-1]['t']} ({la:.2f},{lo:.2f}) "
              f"{pts[-1]['p']}hPa {pts[-1]['w']}kt r34={pts[-1]['r34']}")
    return pts


def mask_of(xlat, xlong, la, lo, half):
    return ((np.abs(xlat - la) <= half)
            & (np.abs(((xlong - lo + 180) % 360) - 180) <= half))


def save_track(pts: list, path: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(pts, f, ensure_ascii=False, indent=1)


def write_dat(pts: list, out_dir: str, basin: str = 'WP', no: int = 1,
              year: str = '') -> str:
    """与模拟器 .dat 格式一致(回放程序可直接读)。"""
    os.makedirs(out_dir, exist_ok=True)
    name = f"b{basin.lower()}{no:02d}{year or (pts[0]['t'][:4] if pts else '')}.dat"
    path = os.path.join(out_dir, name)
    lines = []
    for st in pts:
        la_s = f"{int(abs(st['la']) * 10):3d}{'N' if st['la'] >= 0 else 'S'}"
        # T4: st['lo'] 已 %360 但 round 可把 359.9x 进位到 360.0 → 必须再 %360,
        # 否则经度接近 360 时写出 '0W'(实为 0°E/日界线)。与 sim.py 的 C7 处理一致。
        vlo = st['lo'] % 360.0
        lo_s = f"{int((360 - vlo) * 10):4d}W" if vlo > 180 \
            else f"{int(vlo * 10):4d}E"
        lines.append(f"{basin}, {no:02d}, {st['t']},   , XRQA,   0,"
                     f" {la_s}, {lo_s}, {st['w']:3d}, {st['p']:4d}, {st['st']}")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def main():
    ap = argparse.ArgumentParser(description='wrfout 台风涡旋追踪')
    ap.add_argument('--wrfout', required=True)
    ap.add_argument('--start', required=True, help='YYYY-MM-DD')
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--guess-lat', type=float, default=None)
    ap.add_argument('--guess-lon', type=float, default=None)
    ap.add_argument('--out', default=None, help='json 输出(默认 wrfout/track.json)')
    args = ap.parse_args()
    pts = track_wrfout(args.wrfout, datetime.strptime(args.start, '%Y-%m-%d'),
                       args.days,
                       (args.guess_lat, args.guess_lon) if args.guess_lat is not None else None)
    out = args.out or os.path.join(args.wrfout, 'track.json')
    save_track(pts, out)
    print(f'完成: {len(pts)} 个报点 → {out}')


if __name__ == '__main__':
    main()
