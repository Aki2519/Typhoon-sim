# -*- coding: utf-8 -*-
"""F3: 11 个气候模态索引 —— 下载 + 解析 + 落表(运行时零外部数据)。

模态(用户 2026-09 拍板"11 个都要"):
  ENSO(Niño3.4/ONI) PDO AMO NAO IOD(DMI) 大西洋Niño(ATL3) PMM NPGO SAM/AAO QBO
  + MJO(季节内, 单独存 RMM1/RMM2 逐日)

记录起点各不相同, 必须逐模态存可用性掩码 —— 1850 年只有 NAO 是实测,
其余走随机模拟器; 1875 年 6 实 5 虚。

输出 simulator/env/model/modes_index.npz:
  names   (K,)      模态名
  year    (K,T)     年(仅用于诊断)
  month   (K,T)     月
  val     (K,T)     标准化后的指数
  ok      (K,T)     bool, 该模态该月是否有实测
  avail0  (K,)      该模态首个可用年
  rmm1/rmm2/t_rmm   MJO 逐日(BOM RMM)
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'simulator', 'env', 'train_cache')
MODEL = os.path.join(ROOT, 'simulator', 'env', 'model')

#: 模态名 → (URL, 缺测值集合, 说明)
SOURCES = {
    'nino34': ('https://psl.noaa.gov/data/correlation/nina34.data',
               (-9.9, -9.99, -99.9, -999.0), 'Niño3.4 SST 异常'),
    'oni': ('https://psl.noaa.gov/data/correlation/oni.data',
            (-9.9, -9.99, -99.9, -999.0), 'ONI(3 月滑动)'),
    'pdo': ('https://psl.noaa.gov/data/correlation/pdo.data',
            (-9.9, -9.99, -99.9, -999.0), 'PDO'),
    'amo': ('https://psl.noaa.gov/data/correlation/amon.us.data',
            (-9.9, -9.99, -99.9, -999.0), 'AMO(未平滑)'),
    'nao': ('https://psl.noaa.gov/data/correlation/nao.data',
            (-9.9, -9.99, -99.9, -999.0), 'NAO'),
    'aao': ('https://psl.noaa.gov/data/correlation/aao.data',
            (-9.9, -9.99, -99.9, -999.0), 'AAO/SAM'),
    'qbo': ('https://psl.noaa.gov/data/correlation/qbo.data',
            (-9.9, -9.99, -99.9, -999.0), 'QBO(30hPa 纬向风)'),
    'npgo': ('https://psl.noaa.gov/data/correlation/npgo.data',
             (-9.9, -9.99, -99.9, -999.0), 'NPGO'),
    'pmm': ('https://psl.noaa.gov/data/correlation/pmm.data',
            (-9.9, -9.99, -99.9, -999.0), 'PMM'),
    'tni': ('https://psl.noaa.gov/data/correlation/tni.data',
            (-9.9, -9.99, -99.9, -999.0), 'TNI(经向 ENSO)'),
    'dmi': ('https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data',
            (-999.9, -999.0, -99.99), 'IOD/DMI(HadISST)'),
}
RMM_URL = 'http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt'


def _download(url, out, retries=3, timeout=60):
    import urllib.request
    if os.path.exists(out) and os.path.getsize(out) > 200:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if len(body) > 200:
                with open(out, 'wb') as f:
                    f.write(body)
                return out
        except Exception as e:
            print('  下载失败(%d/%d) %s: %s' % (k + 1, retries, url[:60],
                                                str(e)[:60]), flush=True)
            time.sleep(2)
    return None


def _is_missing(v, missing):
    """缺测判定。PSL 各文件用的哨兵值并不统一(实测见过 -9.9 / -99.0 / -99.99
    / -999 / -9999), 只按显式列表匹配会漏掉, 于是"缺测"被当成真实值进入
    均值/方差 —— AAO 的均值一度被算成 -138。这里用"≤ -90 一律缺测"兜底,
    物理上这些指数都不可能出现 -90 以下的真值(量级都在 ±40 以内)。"""
    if not np.isfinite(v):
        return True
    if v <= -90.0:
        return True
    return any(abs(v - mz) < 1e-2 for mz in missing)


def parse_psl(path, missing):
    """PSL 'correlation' 格式: 首行 ' y0 y1', 之后每行 'year v1..v12'。"""
    rows = []
    with open(path, encoding='utf-8', errors='replace') as f:
        first = f.readline().split()
        years = [int(x) for x in first[:2]] if len(first) >= 2 else [0, 0]
        for line in f:
            p = line.split()
            if len(p) < 13:
                continue
            try:
                y = int(float(p[0]))
                v = np.array([float(x) for x in p[1:13]])
            except ValueError:
                continue
            for m in range(12):
                if _is_missing(v[m], missing):
                    v[m] = np.nan
            rows.append((y, v))
    return years, rows


def main():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(MODEL, exist_ok=True)
    raw = {}
    for name, (url, missing, desc) in SOURCES.items():
        fp = os.path.join(CACHE, 'idx_%s.txt' % name)
        if _download(url, fp) is None:
            print('!! %-7s 不可达, 跳过(%s)' % (name, desc), flush=True)
            continue
        try:
            yr, rows = parse_psl(fp, missing)
            raw[name] = (yr, rows)
            print('%-7s %-22s %s' % (name, desc, yr), flush=True)
        except Exception as e:
            print('!! %s 解析失败 %s' % (name, str(e)[:80]), flush=True)

    # MJO
    rmm = None
    fp = os.path.join(CACHE, 'rmm.txt')
    if _download(RMM_URL, fp) is not None:
        try:
            rmm = np.loadtxt(fp, skiprows=2, usecols=(0, 1, 2, 3, 4))
            print('rmm      MJO RMM 逐日           %d 行' % rmm.shape[0], flush=True)
        except Exception as e:
            print('!! rmm 解析失败', str(e)[:80], flush=True)

    if not raw:
        print('没有任何模态可用'); return

    # 统一时间轴(月): 最早年份 1 月 ~ 最晚年份 12 月
    y_lo = min(v[0][0] for v in raw.values())
    y_hi = max(r[-1][0] for _, r in raw.values())
    T = (y_hi - y_lo + 1) * 12
    names = sorted(raw)
    K = len(names)
    val = np.full((K, T), np.nan, np.float32)
    year = np.zeros((K, T), np.int16)
    month = np.zeros((K, T), np.int8)
    ok = np.zeros((K, T), bool)
    for k, nm in enumerate(names):
        yr, rows = raw[nm]
        for (y, v) in rows:
            if y < y_lo or y > y_hi:
                continue
            base = (y - y_lo) * 12
            for m in range(12):
                t = base + m
                year[k, t] = y
                month[k, t] = m + 1
                if np.isfinite(v[m]):
                    val[k, t] = v[m]
                    ok[k, t] = True

    # 标准化(每模态各自去均值/除标准差, 只用有实测的部分)
    mu = np.array([np.nanmean(val[k][ok[k]]) for k in range(K)])
    sd = np.array([np.nanstd(val[k][ok[k]]) for k in range(K)])
    sd[sd < 1e-6] = 1.0
    valz = (val - mu[:, None]) / sd[:, None]
    avail0 = np.array([year[k][np.argmax(ok[k])] if ok[k].any() else 9999
                       for k in range(K)], np.int16)

    out = dict(names=np.array(names), year=year, month=month, val=valz,
               ok=ok, avail0=avail0, mu=mu.astype(np.float32),
               sd=sd.astype(np.float32), y_lo=np.int16(y_lo),
               y_hi=np.int16(y_hi))
    if rmm is not None:
        out['rmm'] = rmm.astype(np.float32)
    np.savez_compressed(os.path.join(MODEL, 'modes_index.npz'), **out)

    print('\n== 模态索引表 ==')
    print('%-8s %8s %8s %10s %10s %8s' %
          ('模态', '起点', '实测月数', '均值', '标准差', '缺测率'))
    for k, nm in enumerate(names):
        n = int(ok[k].sum())
        print('%-8s %8d %8d %10.3f %10.3f %7.1f%%' %
              (nm, avail0[k], n, mu[k], sd[k],
               100.0 * (1 - n / max(1, int((y_hi - avail0[k] + 1) * 12)))))
    print('\n-> simulator/env/model/modes_index.npz  (T=%d 月, %d 模态)'
          % (T, K))
    # 关键年份抽查
    for probe in (1850, 1875, 1950, 1997, 2015, 2026):
        if probe < y_lo or probe > y_hi:
            continue
        t = (probe - y_lo) * 12 + 8          # 9 月
        got = [(names[k], round(float(valz[k, t]), 2)) for k in range(K)
               if ok[k, t]]
        print('  %d-09 实测模态: %s' % (probe, got if got else '(无)'))


if __name__ == '__main__':
    main()
