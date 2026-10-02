# -*- coding: utf-8 -*-
"""模拟场 vs 观测: 定量对比(SST / OHC / 风切) + 三联对照图。

数据来源:
  模拟: output/sim_20260914_dm1.npz (数据环境) / _dm0.npz (解析公式)
  观测: output/ref_grid_*.npz     (由 tools/digitize_ref.py 反演)
  风切: output/ref_shear_*.npz    (由 tools/digitize_shear.py 反演, 分档面积占比)

用法: python tools/compare_sim_obs.py
输出: output/cmp_report.json + output/cmp_<field>_<basin>.png
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'output')
sys.path.insert(0, ROOT)

BASINS = {
    'WPAC': (100.0, 180.0, 0.0, 50.0),
    'EPAC': (-140.0, -80.0, 0.0, 35.0),
    'NATL': (-100.0, -10.0, 0.0, 55.0),
}

# 与参考产品一致的取色(仅用于画对照图)
SST_STEPS = ["#dfe3ef", "#cbcee7", "#c2c3e7", "#adaede", "#9d9ad4", "#8885cf",
             "#7573c5", "#5f5ebc", "#4d49b5", "#003b9d", "#0748ad", "#1153bf",
             "#175fce", "#1f6add", "#206ce2", "#2c8ad8", "#3aa8dd", "#6bd5e6",
             "#6dd5e6", "#3fc9c0", "#70eaa3", "#6eea9e", "#53d479", "#38c050",
             "#1bab28", "#019501", "#f2b000", "#e58000", "#da5201", "#c20305",
             "#d22b58", "#e454aa", "#e96bd4", "#ec5ede"]
OHC_STEPS = ["#01c7c8", "#01cfa8", "#00d18d", "#17d67a", "#5fdd4f", "#a3e52f",
             "#c8e02f", "#e6db2f", "#e7c530", "#e7ae30", "#e8952f", "#e87a2c",
             "#e85d28", "#e84025", "#e62222", "#dc1435", "#d2104a", "#c80e5f",
             "#bd0c72", "#b60a80", "#c00a86", "#d40c7f", "#e01a78"]


def _hex(c):
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


PAL = {'sst': [_hex(c) for c in SST_STEPS], 'ohc': [_hex(c) for c in OHC_STEPS]}
VRANGE = {'sst': (0.0, 33.0), 'ohc': (0.0, 220.0)}


def load_sim(tag):
    d = np.load(os.path.join(OUT, 'sim_%s.npz' % tag))
    return {k: d[k] for k in d.files}


def basin_index(lon_c, lat_c, box):
    lo0, lo1, la0, la1 = box
    il = np.nonzero((lon_c >= lo0) & (lon_c <= lo1))[0]
    jl = np.nonzero((lat_c >= la0) & (lat_c <= la1))[0]
    return jl, il


def sim_field(sim, name, target_lon, target_lat):
    """把全局场抽到 target 网格(经度按 0-360 归一)。

    分辨率无关: 用网格自身的步长换算下标。写死 1° 会在内核换成 0.5° 后
    把纬度错位一倍(取到另一个半球), 算出负相关。
    """
    lon_g = sim['lon']
    lat_g = sim['lat']
    dy = float(lat_g[1] - lat_g[0])
    dx = float(lon_g[1] - lon_g[0])
    j = np.round((target_lat - lat_g[0]) / dy).astype(int)
    i = np.round(((target_lon % 360.0) - lon_g[0]) / dx).astype(int) % lon_g.size
    okj = (j >= 0) & (j < lat_g.size)
    arr = sim[name]
    out = np.full((target_lat.size, target_lon.size), np.nan, np.float32)
    jj = np.clip(j, 0, lat_g.size - 1)
    out[np.ix_(okj, np.arange(target_lon.size))] = arr[np.ix_(jj[okj], i)]
    out[~okj, :] = np.nan
    return out


def colorize(vals, field):
    lo, hi = VRANGE[field]
    pal = np.array(PAL[field], np.uint8)
    n = pal.shape[0]
    idx = np.clip((np.nan_to_num(vals) - lo) / (hi - lo) * n, 0, n - 1).astype(int)
    rgb = pal[idx]
    rgb[~np.isfinite(vals)] = 255
    return rgb


def stats(obs, sim):
    m = np.isfinite(obs) & np.isfinite(sim)
    if m.sum() < 10:
        return {'n': int(m.sum())}
    o, s = obs[m], sim[m]
    d = s - o
    return {
        'n': int(m.sum()),
        'obs_mean': round(float(o.mean()), 2),
        'sim_mean': round(float(s.mean()), 2),
        'bias': round(float(d.mean()), 2),
        'mae': round(float(np.abs(d).mean()), 2),
        'rmse': round(float(np.sqrt((d ** 2).mean())), 2),
        'corr': round(float(np.corrcoef(o, s)[0, 1]), 3),
        'obs_p10': round(float(np.percentile(o, 10)), 1),
        'obs_p50': round(float(np.percentile(o, 50)), 1),
        'obs_p90': round(float(np.percentile(o, 90)), 1),
        'sim_p10': round(float(np.percentile(s, 10)), 1),
        'sim_p50': round(float(np.percentile(s, 50)), 1),
        'sim_p90': round(float(np.percentile(s, 90)), 1),
        'obs_std': round(float(o.std()), 2),
        'sim_std': round(float(s.std()), 2),
    }


def fractions(arr, field, edges):
    m = np.isfinite(arr)
    if m.sum() == 0:
        return {}
    a = arr[m]
    out = {}
    for k in range(len(edges) - 1):
        out['%.0f-%.0f' % (edges[k], edges[k + 1])] = round(
            float(((a >= edges[k]) & (a < edges[k + 1])).mean()), 3)
    return out


def save_panel(path, rows):
    """rows: [(title, rgb_array), ...] 全部同形状 → 纵向拼接(带标题)。"""
    h, w, _ = rows[0][1].shape
    bar = 22
    img = Image.new('RGB', (w, (h + bar) * len(rows)), (18, 18, 26))
    d = ImageDraw.Draw(img)
    for k, (t, rgb) in enumerate(rows):
        img.paste(Image.fromarray(rgb), (0, k * (h + bar) + bar))
        d.text((6, k * (h + bar) + 5), t, fill=(255, 235, 120))
    img.save(path)


def main():
    report = {}
    sims = {'dm1': load_sim('20260914_dm1'), 'dm0': load_sim('20260914_dm0')}
    if os.path.exists(os.path.join(OUT, 'sim_20260914_y1979.npz')):
        sims['y1979'] = load_sim('20260914_y1979')   # 真实 ERA5 1979-09 月场
    if os.path.exists(os.path.join(OUT, 'sim_f2_real.npz')):
        sims['f2real'] = load_sim('f2_real')         # F2: 真实 SST 基态 + D26/TCHP
    for field in ('sst', 'ohc'):
        report[field] = {}
        for basin, box in BASINS.items():
            key = '%s_%s' % (field, basin.lower())
            p = os.path.join(OUT, 'ref_grid_%s.npz' % key)
            if not os.path.exists(p):
                continue
            r = np.load(p)
            obs, lon_c, lat_c = r['val'], r['lon'], r['lat']
            jl, il = basin_index(lon_c, lat_c, box)
            obs_b = obs[np.ix_(jl, il)]
            ent = {'box': box, 'obs': stats(obs_b, obs_b)}
            rows = [('%s OBS (digitized)  %s' % (key, box), colorize(obs_b, field))]
            for tag in sims:
                sf = sim_field(sims[tag], field, lon_c[il], lat_c[jl])
                # 陆地掩膜: 观测无效处(陆地)一并剔除
                ent[tag] = stats(obs_b, sf)
                ent[tag + '_frac'] = fractions(sf, field,
                                              np.arange(0, 34, 2) if field == 'sst'
                                              else np.arange(0, 221, 20))
                rows.append(('%s SIM %s  %s' % (key, tag, box), colorize(sf, field)))
            ent['obs_frac'] = fractions(obs_b, field,
                                        np.arange(0, 34, 2) if field == 'sst'
                                        else np.arange(0, 221, 20))
            report[field][basin] = ent
            save_panel(os.path.join(OUT, 'cmp_%s.png' % key), rows)
            print('-> output/cmp_%s.png' % key)

    with open(os.path.join(OUT, 'cmp_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 终端摘要
    for field in ('sst', 'ohc'):
        print('\n===== %s =====' % field.upper())
        hdr = '%-5s %-8s %8s %8s %8s %8s %8s %7s' % (
            'basin', 'mode', 'obs_mean', 'sim_mean', 'bias', 'rmse', 'mae', 'corr')
        print(hdr)
        for basin in BASINS:
            e = report[field].get(basin)
            if not e:
                continue
            for tag in sims:
                s = e.get(tag, {})
                if 'bias' not in s:
                    print('%-5s %-8s  n=%s' % (basin, tag, s.get('n')))
                    continue
                print('%-5s %-8s %8.2f %8.2f %8.2f %8.2f %8.2f %7.3f' % (
                    basin, tag, s['obs_mean'], s['sim_mean'], s['bias'],
                    s['rmse'], s['mae'], s['corr']))
            print('%-5s %-8s p10/p50/p90 obs %s  sim(dm1) %s  sim(dm0) %s' % (
                basin, 'pct',
                (e['obs']['obs_p10'], e['obs']['obs_p50'], e['obs']['obs_p90']),
                (e['dm1'].get('sim_p10'), e['dm1'].get('sim_p50'), e['dm1'].get('sim_p90')),
                (e['dm0'].get('sim_p10'), e['dm0'].get('sim_p50'), e['dm0'].get('sim_p90'))))


if __name__ == '__main__':
    main()
