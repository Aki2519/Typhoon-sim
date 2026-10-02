# -*- coding: utf-8 -*-
"""把"缺测/陆地/海冰"格用**平滑**方式补上(而不是最近有效值跳变)。

为什么需要它(用户 2026-09-21 的反馈):
  "为什么 sst 会有跳变? 南极海域全是跳变, 印度洋也很严重, 北大西洋主洋面外海有一条跳变"
实测: 原实现(最近有效值)跨大块陆地时会从**另一侧的海**取数, 于是相邻格差最大到
18.8 °C(SST)、200 kJ(OHC, 分洋区文件的赤道南边界)。肉眼就是一条条直线跳变。

做法: 反复用**邻域已知值的加权平均**去填未知格(Laplace 扩散/调和插值), 迭代若干次。
边缘自然平滑过渡, 没有"从远处跳一个值过来"的硬边。经纬用 wrap(经度环绕),
纬度用 nearest(极区不出界)。
"""
from __future__ import annotations

import contextlib
import warnings

import numpy as np


@contextlib.contextmanager
def _quiet():
    """nanmedian/nanmin 遇到"整块都是 NaN"会刷 RuntimeWarning —— 这是预期情况, 静音。"""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        yield


def smooth_fill(a, bad=None, iters: int = 400, tol: float = 1e-4):
    """把 bad 处的缺测格用平滑插值补上(其余格保持不变)。

    参数
      a    : 2D 数组(高=纬, 宽=经)
      bad  : 布尔掩码(True=要补); None 时取 ~isfinite(a)
      iters: 最大迭代次数(每次把未知格替换为四邻均值; 收敛很快)
      tol  : 相邻两次迭代的最大变化小于它就提前停
    """
    a = np.array(a, dtype=np.float32, copy=True)
    if bad is None:
        bad = ~np.isfinite(a)
    bad = np.asarray(bad, bool)
    if not bad.any():
        return np.nan_to_num(a, nan=0.0)
    if bad.all():
        return np.zeros_like(a)
    a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
    known = ~bad
    # **初值用最近有效值铺底**(距离变换), 再做扩散。
    # 2026-09-21 实测教训: 初值取"全球均值"再扩散, 跨大块缺测区迭代几百次也收敛不了,
    # 反而造出 22 °C 的假跳变(比最近值更糟)。
    from scipy import ndimage as _ndi
    _, idx = _ndi.distance_transform_edt(bad, return_indices=True)
    a[bad] = a[tuple(i[bad] for i in idx)]

    def _nb(x, dy, dx):
        if dy:
            y = np.empty_like(x)
            if dy > 0:
                y[:-dy] = x[dy:]
                y[-dy:] = x[-1:]
            else:
                y[-dy:] = x[:dy]
                y[:-dy] = x[0:1]
            x = y
        if dx:
            x = np.roll(x, dx, axis=1)          # 经度环绕
        return x

    # **Laplace 修补**: 未知格取四邻均值(邻居里也含未知格), 已知格固定不动。
    # 原来把"已知格计数"写进了分母 ✗ —— 结果只有紧贴海岸的一圈被填, 内部纹丝不动,
    # 台阶照旧(合成用例实测: 19 °C 的台阶一点没降)。现在所有格都参与平均。
    for _ in range(int(iters)):
        s4 = _nb(a, 1, 0) + _nb(a, -1, 0) + _nb(a, 0, 1) + _nb(a, 0, -1)
        new = s4 * 0.25
        diff = float(np.max(np.abs(new[bad] - a[bad]))) if bad.any() else 0.0
        a[bad] = new[bad]
        if diff < tol:
            break
    # 极少数情况下仍有 NaN(全域孤立), 兜底
    return np.nan_to_num(a, nan=float(np.nanmean(a)))


def fill_to_ocean(a, landfrac, thresh: float = 0.999, **kw):
    """按陆地掩码补值: 把 landfrac>=thresh 的格当作"要补", 其余(含海岸、缺测)保持。"""
    bad = np.asarray(landfrac) >= thresh
    return smooth_fill(a, bad, **kw)


# ── 孤立离群格(源数据单格坏值) ─────────────────────────────────────

def _neigh_stack(a):
    """返回 (H,W) 的 9 个视图: 自身 + 8 邻域(纬度 edge 复制, 经度环绕)。"""
    H, W = a.shape
    p = np.empty((H + 2, W + 2), a.dtype)
    p[1:-1, 1:-1] = a
    p[0, 1:-1] = a[0]
    p[-1, 1:-1] = a[-1]
    p[:, 0] = p[:, -2]
    p[:, -1] = p[:, 1]
    return [p[dy:dy + H, dx:dx + W] for dy in range(3) for dx in range(3)]


def _nb_stat(a, how='median'):
    """8 邻域统计(不含自身); 纬度 edge 复制、经度环绕。"""
    st = _neigh_stack(a)
    others = np.stack([s for i, s in enumerate(st) if i != 4])
    with _quiet(), np.errstate(all='ignore'):
        if how == 'median':
            return np.nanmedian(others, axis=0)
        if how == 'min':
            return np.nanmin(others, axis=0)
        return np.nanmax(others, axis=0)


def outlier_mask(a, k: float = 0.45, abs_min: float = 20.0):
    """**孤立尖峰**掩码: 该格比**全部 8 个邻居**都低/高出一大截。

    背景(2026-09-21 实测): base_tchp 在 (2.0S, 153.2E) 是 14.1 kJ/cm², 邻格 187~223,
    同格 SST 30.8 °C。它既不是洞(>0.5)也不是陆地 —— 是 CMEMS 源文件里的单格坏值,
    画出来就是用户说的"跳变/极严重问题"。原来的"0=洞"规则根本抓不到。

    判据用**相对中位数的比例 + 绝对下限**, 不用固定阈值, 免得把整体低值区(近岸、
    高纬)误判; 且要求**越出全部邻居的范围**(真实锋面两侧都有邻居夹着, 不会触发)。
    """
    a = np.asarray(a, np.float32)
    st = _neigh_stack(a)
    with _quiet(), np.errstate(all='ignore'):
        nmin = _nb_stat(a, 'min')
        nmax = _nb_stat(a, 'max')
        med = np.nanmedian(np.stack(st), axis=0)
    delta = np.abs(k * med) + abs_min
    ok = np.isfinite(a) & np.isfinite(nmin) & np.isfinite(med)
    return ok & ((a < nmin - delta) | (a > nmax + delta))


def ocean_fill(a, warm=None, land=None, despike: bool = True, zero_med=None,
               k: float = 0.45, abs_min: float = 20.0, iters: int = 400):
    """海面场统一补值: **洞 + 陆地 + 孤立尖峰** 三类一起平滑补掉。

    参数
      a      : 2D(或会被压成 2D)数组
      warm   : SST 场; 给定时 "暖水(SST>26.5°C)里却是 0" 也算洞
               (冷海 TCHP=0 是**合法值**, 全球 61% 的格都这样)
      land   : landfrac 场(>=0.999 视为要补)
      despike: 是否识别孤立尖峰(见 outlier_mask)
      zero_med: 再给一条"**孤立 0**"判据 —— 0 值格的 8 邻域中位数大于它就当洞。
               2026-09-21 实测: 东北太平洋 3-10N 有 9% 的格是 0(邻格 90~140 kJ),
               而这些格的 SST 是 24.6~26.1 °C, **卡在 26 °C 定义线附近** ——
               单靠 "SST>26.5 却是 0" 一条判据全漏掉。用邻域中位数就能区分:
               紧贴 26 °C 等温线的**成片** 0 是真 0(邻域中位数也低),
               被高值包住的**孤立** 0 是源文件的空洞(邻域中位数很高)。
    """
    a = np.asarray(a, np.float32)
    bad = ~np.isfinite(a)
    if warm is not None:
        w = np.asarray(warm, np.float32)
        bad = bad | ((a <= 0.5) & (w > 26.5))
        if zero_med is not None:
            probed = a.copy()
            probed[bad] = np.nan
            bad = bad | ((a <= 0.5) & (w > 23.5)
                         & (_nb_stat(probed, 'median') > float(zero_med)))
    if land is not None:
        lf = np.asarray(land, np.float32)
        if lf.shape == a.shape:
            bad = bad | (lf >= 0.999)
    if despike:
        probed = a.copy()
        probed[bad] = np.nan          # 已知坏格不参与邻居统计, 免得把错误传染开
        bad = bad | outlier_mask(probed, k=k, abs_min=abs_min)
    if not bad.any() or bad.all():
        return np.nan_to_num(a, nan=0.0).astype(np.float32)
    return smooth_fill(a, bad, iters=iters)
