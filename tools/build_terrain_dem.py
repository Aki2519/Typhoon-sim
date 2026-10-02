# tools/build_terrain_dem.py
"""从 map/heightmap.png 生成与环境网格**严格对齐**的海拔场(轮次 158 + 掩码修复)。

与 tools/build_terrain.py(程序化高斯合成)的区别:
    那个是**合成**的近似; 这个是**真 DEM**(仓库里本来就有的那张全球高程栅格,
    `map/heightmap.png`, 30 px/度, 覆盖 ±90deg)。台湾中央山脉、吕宋 Cordillera/
    Sierra Madre、日本阿尔卑斯都在里面 ---- 无需人造山脊。

heightmap 编码(见 tools/make_cell_land_mask.py 的注释):
    像素值 = 高程(米) + 12000      -> 陆地 ⟺ 像素 > 12000
    行 = (90 - lat) * 30           (北在上, 全图 0..5400)
    列 = lon * 30                  (0..10800, 经度 0..360)

**海面高程 = 0, 不是负深度**(本轮的修复点)
    像素 ≤ 12000 编码的是**海底深度**(像素 1244 ⇒ −10756 m)。原先的脚本直接写
    `像素 − 12000`, 把海沟深度当成负高程存进 `elev_mean`/`elev_max`, 于是
    `terrain.py:126-133` 声明的两条不变量被破坏(纯海洋格海拔恒为 0 那一条)。
    现在把海底像素**先置 0 再统计**; 掩码(`apply_land_mask`)不是用来"把负数变回 0"
    的, 它只负责抹掉平滑/粗采样"漏"到海洋上的假山 —— 两者缺一不可。

输出网格(必须与内核一致, 见 core.py 的 env_lat/env_lon):
    shape = (ENVNY, ENVNX) = (360, 720)
    elev[j, i] = 纬度 LAT0+(j+0.5)*ENVD = -90+(j+0.5)*0.5  (row0 = 最南)
                 经度 LON0+(i+0.5)*ENVD = (i+0.5)*0.5
    -> **格心对齐**, 行序南在上。做错对齐会整体静默偏移 0.25deg, 所以本脚本
       末尾做三重自检: ① 位置自检(在已知山系/海沟上采样, 越界即报错); ② 把
       像素掩码算出的逐格陆地占比与权威 `land_cell.npz` 的 `landfrac` 逐格比对
       (独立来源, 一致即把映射钉死); ③ **判别力负对照** —— 把像素图整体平移
       0.267° 后必须对不上(证明 ② 不是因为判据太松), 另用南北极点判行序。

存两个场:
    elev_mean ---- 格内 15×15 像素均值(用于摩擦/热力类"面"效应)
    elev_max  ---- 格内 15×15 像素最大(用于"沿途最高海拔"这类"峰"效应)

陆地掩码(契约, 见 simulator/simcore/terrain.py:126-133 / apply_land_mask):
    权威陆地占比取自 `simulator/env/model/land_cell.npz` 的 `landfrac`(与内核同
    网格同对齐), 掩码直接调用 `terrain.apply_land_mask` —— 不在这里重写一遍规则:
        elev_mean = raw_mean · landfrac
        elev_max  = raw_max · [landfrac > 0]
    由此得到的可验证中性性质: **纯海洋格两个场严格 == 0** ⇒ 摩擦因子恒 1、
    衰减因子恒 1 ⇒ 海上行为逐位不变。

用法:
    python tools/build_terrain_dem.py            # 全图, 分块处理
"""
import json
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEIGHT = os.path.join(ROOT, 'map', 'heightmap.png')
OUT = os.path.join(ROOT, 'simulator', 'env', 'model', 'terrain_dem.npz')
TARGET = os.path.join(ROOT, 'simulator', 'simcore', 'terrain_0.5.npz')
BACKUP = os.path.join(ROOT, 'simulator', 'simcore', 'terrain_0.5_synth.npz')
LANDCELL = os.path.join(ROOT, 'simulator', 'env', 'model', 'land_cell.npz')

PX_PER_DEG = 30          # heightmap 分辨率
OFFSET = 12000           # 像素 = 高程 + OFFSET(像素 ≤ OFFSET ⇒ 海面, 按 0 处理)


# ═══════════════════════ 1. 逐格统计(裸合成场) ═══════════════════════

def load_landfrac(envny: int, envnx: int) -> np.ndarray:
    """读权威陆地占比(与内核同网格同对齐)。缺失/形状不符 -> 直接失败, 不写半成品。"""
    if not os.path.exists(LANDCELL):
        raise SystemExit('缺少 %s: 掩码无权威来源, 拒绝写出未掩码的场' % LANDCELL)
    with np.load(LANDCELL) as z:
        lf = np.asarray(z['landfrac'], dtype=np.float32)
    if lf.shape != (envny, envnx):
        raise SystemExit('land_cell.npz landfrac 形状 %s != (%d, %d)'
                         % (lf.shape, envny, envnx))
    return lf


def build_raw(full: np.ndarray, envny: int, envnx: int, envd: float,
              lat0: float, lon0: float):
    """逐格取 15×15 像素窗 -> (裸场 mean/max, 像素陆地占比)。

    **海面高程按 0 处理**: ``elev = np.where(像素 > OFFSET, 像素 − OFFSET, 0)``。
    先把海沟深度当负高程会污染海岸格的格内均值(半陆格直接被拖成负数), 所以
    置 0 必须在**统计之前**做, 而不是事后裁掉负值。

    **不假设相邻格在图像里连续**(整数取整会带来 ±0.5px 抖动, 之前按"块行数 =
    n_rows×15"的写法就是这么炸的); 行序不用翻: j=0 是最南(-89.75), 算出来的
    y0 自然落在图像底部。
    """
    H, _W = full.shape
    cell_px = int(round(envd * PX_PER_DEG))          # 0.5deg × 30 = 15
    raw_mean = np.zeros((envny, envnx), dtype=np.float32)
    raw_max = np.zeros((envny, envnx), dtype=np.float32)
    frac = np.zeros((envny, envnx), dtype=np.float32)
    for j in range(envny):
        lat = lat0 + (j + 0.5) * envd
        y0 = int(round((90.0 - lat) * PX_PER_DEG - cell_px / 2.0))
        y0 = max(0, min(H - cell_px, y0))
        strip = (full[y0:y0 + cell_px, :envnx * cell_px].astype(np.int32)
                 - OFFSET)
        land = strip > 0
        elev = np.where(land, strip, 0).astype(np.float32)
        blk = elev.reshape(cell_px, envnx, cell_px)
        raw_mean[j] = blk.mean(axis=(0, 2)).astype(np.float32)
        raw_max[j] = blk.max(axis=(0, 2)).astype(np.float32)
        frac[j] = (land.reshape(cell_px, envnx, cell_px)
                   .mean(axis=(0, 2)).astype(np.float32))
    return raw_mean, raw_max, frac


def cell_fraction(land: np.ndarray, envny: int, envnx: int, envd: float,
                  lat0: float) -> np.ndarray:
    """逐格陆地占比(取窗公式与 :func:`build_raw` **逐字相同**)。

    单独抽出来是为了做"对齐判别力"负对照: 把像素图整体平移若干像元再重算,
    看"与权威 landfrac 比对"这条判据能不能发现错位 —— 否则"位置自检全过"可能
    只是因为那几个阈值根本没有判别力(0.25° 错位时台湾/吕宋照样"很高")。
    """
    H = land.shape[0]
    cell_px = int(round(envd * PX_PER_DEG))
    out = np.zeros((envny, envnx), dtype=np.float32)
    for j in range(envny):
        lat = lat0 + (j + 0.5) * envd
        y0 = int(round((90.0 - lat) * PX_PER_DEG - cell_px / 2.0))
        y0 = max(0, min(H - cell_px, y0))
        blk = land[y0:y0 + cell_px, :envnx * cell_px]
        out[j] = blk.reshape(cell_px, envnx, cell_px).mean(axis=(0, 2))
    return out


#: 负对照的平移量(像元): 8 px / 30 px·度⁻¹ = 0.267°, 比半格(0.25°)还大一点。
ALIGN_SHIFT_PX = 8


def check_alignment(full: np.ndarray, lf: np.ndarray, frac: np.ndarray,
                    envny: int, envnx: int, envd: float, lat0: float,
                    tag: str) -> None:
    """对齐判据 + **判别力负对照**。

    正面: 像素掩码算出的逐格陆地占比必须与权威 ``land_cell.npz`` 的 ``landfrac``
    逐格一致 —— 它俩完全独立(一个从 heightmap 的 ``像素 > 12000`` 数, 一个是内核
    在用的权威掩码), 逐格全等就等于把"像素 → 网格"的映射钉死了。
    反面: 把像素图整体平移 0.267° 后同法重算, 必须**明显对不上** —— 这条证明上面
    的"一致"不是因为判据太松。行序(南北)另用极点做一次独立判别: row 0 = 最南 =
    南极冰盖(全陆地), row 359(最后一行) = 最北 = 北冰洋(全海洋)。
    """
    bad = []

    def rep(label, ok, detail):
        print('  %-48s %-4s %s' % (label, 'OK' if ok else 'FAIL', detail))
        if not ok:
            bad.append(label)

    print('-- 对齐自检 + 判别力负对照 (%s) --' % tag)
    land = full > OFFSET
    f0 = cell_fraction(land, envny, envnx, envd, lat0)
    rep('取窗复算与 build_raw 的 frac 一致',
        float(np.abs(f0 - frac).max()) == 0.0,
        'max|d|=%.3e' % float(np.abs(f0 - frac).max()))
    d0 = float(np.abs(f0 - lf).max())
    rep('逐格 == land_cell.landfrac (max|d| <= 1e-6)', d0 <= 1e-6,
        'max|d|=%.3e  mean|d|=%.3e' % (d0, float(np.abs(f0 - lf).mean())))
    for axis, lbl in ((1, '经向'), (0, '纬向')):
        fs = cell_fraction(np.roll(land, ALIGN_SHIFT_PX, axis=axis),
                           envny, envnx, envd, lat0)
        ds = float(np.abs(fs - lf).max())
        rep('负对照: 平移 %.3f°(%s) 必须被发现'
            % (ALIGN_SHIFT_PX / PX_PER_DEG, lbl), ds > 0.2,
            'max|d|=%.3f  n(差>0.05)=%d' % (ds, int((np.abs(fs - lf) > 0.05).sum())))
    rep('行序: row 0 = 最南(南极冰盖 landfrac≈1)', float(lf[0].mean()) > 0.95,
        'lf[0].mean=%.4f' % float(lf[0].mean()))
    rep('行序: 末行 = 最北(北冰洋 landfrac≈0)', float(lf[-1].mean()) < 0.05,
        'lf[-1].mean=%.4f' % float(lf[-1].mean()))

    if bad:
        raise SystemExit('对齐自检失败, 未写出任何文件: %s' % bad)
    print('  对齐自检 + 负对照全部通过')


# ═══════════════════════ 2. 契约自检 ═══════════════════════
def check_contract(mean: np.ndarray, mx: np.ndarray, lf: np.ndarray,
                   tag: str) -> None:
    """把 ``terrain.py:126-133`` 的不变量变成可执行判据。任一条不满足 -> SystemExit。"""
    bad = []
    n_ocean = int((lf <= 0.0).sum())
    n_land = int((lf > 0.0).sum())
    print('-- 契约自检 terrain.py:126-133 (%s) --' % tag)

    def rep(label, ok, detail):
        print('  %-48s %-4s %s' % (label, 'OK' if ok else 'FAIL', detail))
        if not ok:
            bad.append(label)

    rep('shape == (ENVNY, ENVNX) == (360, 720)',
        mean.shape == mx.shape == lf.shape == (360, 720),
        '%s / %s / %s' % (mean.shape, mx.shape, lf.shape))
    rep('dtype == float32', mean.dtype == mx.dtype == np.float32,
        '%s / %s' % (mean.dtype, mx.dtype))
    rep('elev_mean.min() >= 0', float(mean.min()) >= 0.0,
        'min=%.6g' % float(mean.min()))
    rep('elev_max.min()  >= 0', float(mx.min()) >= 0.0,
        'min=%.6g' % float(mx.min()))
    o_m = float(np.abs(mean[lf <= 0.0]).max())
    o_x = float(np.abs(mx[lf <= 0.0]).max())
    rep('纯海洋格 elev_mean == 0 (n=%d)' % n_ocean, o_m == 0.0,
        '|max|=%.6g' % o_m)
    rep('纯海洋格 elev_max  == 0 (n=%d)' % n_ocean, o_x == 0.0,
        '|max|=%.6g' % o_x)
    n_neg = int((mean[lf > 0.0] < 0.0).sum())
    rep('含陆格 elev_mean < 0 的格数 == 0', n_neg == 0, 'count=%d' % n_neg)
    n_nz = int((mx > 0.0).sum())
    rep('n_nonzero(elev_max) == 含陆格数', n_nz == n_land,
        '%d vs %d' % (n_nz, n_land))

    if bad:
        raise SystemExit('契约自检失败, 未写出任何文件: %s' % bad)
    print('  契约自检全部通过')


# ═══════════════════════ 3. 位置自检(对齐) ═══════════════════════

#: (名称, 场, 经度, 纬度, 阈值, 比较)。前 5 项是原脚本的 5 项位置自检, **逐项保留**;
#: 唯一改动: 海洋那项的期望从 "< 0"(编码的是"未掩码的负深度")改成 "== 0"
#: (terrain.py 的契约: 纯海洋格海拔严格为 0)。其余为掩码修复新增的复验项。
CHECKS = (
    ('台湾中央山脉', 'elev_max', 121.0, 23.7, 1500.0, '>'),
    ('吕宋北部', 'elev_max', 120.8, 17.0, 800.0, '>'),
    ('日本本州中部', 'elev_max', 138.0, 36.0, 800.0, '>'),
    ('太平洋中部', 'elev_max', 180.0, 20.0, 0.0, '=='),
    ('珠峰', 'elev_max', 86.9, 28.0, 4000.0, '>'),
    # ── 掩码修复新增: "面"场与"峰"场支撑集一致 + 海洋恒 0 ──
    ('台湾中央山脉 mean', 'elev_mean', 121.0, 23.7, 500.0, '>'),
    ('吕宋北部 mean', 'elev_mean', 120.8, 17.0, 500.0, '>'),
    ('日本本州中部 mean', 'elev_mean', 138.0, 36.0, 500.0, '>'),
    ('半陆格 121.60/23.60 mean', 'elev_mean', 121.60, 23.60, 0.0, '>'),
    ('太平洋中部 mean', 'elev_mean', 180.0, 20.0, 0.0, '=='),
    ('南印度洋 mean', 'elev_mean', 80.0, -30.0, 0.0, '=='),
)

_EPS = 1e-6


def run_checks(fields, sample, tag: str) -> None:
    """位置自检: 山系必须高、大洋必须严格 0。越界即 SystemExit。"""
    print('-- 位置自检 (%s) --' % tag)
    bad = []
    for nm, key, lo, la, thr, op in CHECKS:
        v = float(sample(fields[key], lo, la))
        if op == '>':
            ok = v > thr
        elif op == '<':
            ok = v < thr
        else:                                  # '=='
            ok = abs(v - thr) <= _EPS
        print('  %-26s %-9s =%9.1f m  期望 %s %.0f  %s'
              % (nm, key, v, op, thr, 'OK' if ok else 'FAIL'))
        if not ok:
            bad.append(nm)
    if bad:
        raise SystemExit('位置自检失败: %s ---- 经纬度对齐错了, 或掩码没生效' % bad)
    print('  位置自检全部通过')


# ═══════════════════════ 4. 主流程 ═══════════════════════

def main() -> None:
    from PIL import Image
    from simulator.simcore import core as V
    from simulator.simcore import terrain as T

    envny, envnx, envd = V.ENVNY, V.ENVNX, V.C['ENVD']
    lat0, lon0 = V.C['LAT0'], V.C['LON0']
    assert (envny, envnx) == (360, 720), (envny, envnx)

    im = Image.open(HEIGHT)
    W, H = im.size
    # **一次性解码整图**(uint16, 5400×10800 = 116MB)。不要逐行 crop ----
    # PIL 对 PNG 的每次 crop 都会触发重解码, 360 次 = 360 遍整图, 会慢到不可用。
    full = np.asarray(im)
    assert full.shape == (H, W), full.shape

    landfrac = load_landfrac(envny, envnx)
    raw_mean, raw_max, frac = build_raw(full, envny, envnx, envd, lat0, lon0)
    print('裸场(掩码前): mean[%.3f..%.1f]  max[%.1f..%.1f]'
          % (raw_mean.min(), raw_mean.max(), raw_max.min(), raw_max.max()))

    # ── 掩码: 规则**只**来自 terrain.apply_land_mask, 不在这里重写一遍 ──
    mean, mx = T.apply_land_mask(raw_mean, raw_max, landfrac)
    print('掩码后:       mean[%.3f..%.1f]  max[%.1f..%.1f]'
          % (mean.min(), mean.max(), mx.min(), mx.max()))

    # 自检 1/3: 契约 + 对齐(含判别力负对照) + 位置 —— 全在**内存数组**上先跑,
    # 过了才落盘(旧的写法是"先写盘再自检", 失败时会留下半成品)。
    check_contract(mean, mx, landfrac, '内存数组')
    check_alignment(full, landfrac, frac, envny, envnx, envd, lat0, '内存数组')
    fields = {'elev_mean': mean, 'elev_max': mx}
    run_checks(fields, V.env_bilinear, 'core.env_bilinear / 内存数组')

    # ── 落盘 ──
    meta = json.dumps({
        'source': 'map/heightmap.png (真 DEM, 30 px/度, ±90deg)',
        'note': '轮次 158 + 掩码修复: 高程 = max(像素 − 12000, 0)'
                '(海面 = 0, 不把海底深度当负高程); '
                'elev_mean = raw_mean · landfrac, '
                'elev_max = raw_max · [landfrac > 0] (terrain.apply_land_mask)',
        'elev_min': float(mean.min()), 'elev_max': float(mx.max()),
        'landfrac_from': 'simulator/env/model/land_cell.npz',
        'mask': 'terrain.apply_land_mask (terrain.py:364-374)',
        'shape': [int(v) for v in mean.shape],
    }, ensure_ascii=False)
    scal = dict(lat0=np.float32(lat0), lon0=np.float32(lon0), d=np.float32(envd))

    np.savez_compressed(OUT, elev_mean=mean, elev_max=mx, landfrac=landfrac,
                        meta=np.str_(meta), **scal)
    print('wrote %s  mean[%.1f..%.1f] max[%.1f..%.1f] MB=%.1f'
          % (OUT, mean.min(), mean.max(), mx.min(), mx.max(),
             os.path.getsize(OUT) / 1e6))

    # ── 同时产出 `simulator.simcore.terrain` 期望的格式 ──
    # 该模块读 `simulator/simcore/terrain_0.5.npz`, 键: elev_mean/elev_max(必需)
    # + landfrac/meta(可选)。**保留模块、只换数据** ---- 山脉变成真的, 而模块的
    # `friction_scale` / `peak_along_track` 等接口一行都不用改。
    if os.path.exists(TARGET) and not os.path.exists(BACKUP):
        shutil.copy2(TARGET, BACKUP)
        print('已备份原(程序化合成)版 -> %s' % os.path.basename(BACKUP))
    np.savez_compressed(TARGET, elev_mean=mean, elev_max=mx,
                        landfrac=landfrac, meta=np.str_(meta), **scal)
    print('wrote %s (供 terrain.py, landfrac=True)' % TARGET)

    # 自检 2/3 + 3/3: **从落盘文件重新读回**(terrain.load / elev_at 走的是磁盘那一份),
    # 再用 terrain 自己的双线性复验一遍 —— 与 core.env_bilinear 同口径。
    T.reset_cache()
    disk = T.load(force=True)
    if disk is None:
        raise SystemExit('terrain.load() 读不到刚写的 %s' % TARGET)
    dfields = {'elev_mean': disk['elev_mean'], 'elev_max': disk['elev_max']}
    run_checks(dfields, V.env_bilinear, 'core.env_bilinear / 落盘文件')
    run_checks(dfields, T.bilinear, 'terrain.bilinear（terrain.elev_at 内部）')
    # 两条采样通路逐点对拍(不同实现, 必须同口径)
    dmax = max(abs(float(V.env_bilinear(dfields[k], lo, la))
                   - float(T.bilinear(dfields[k], lo, la)))
               for _nm, k, lo, la, _t, _o in CHECKS)
    print('core.env_bilinear vs terrain.bilinear: 最大偏差 %.3e m' % dmax)
    if dmax > 1e-3:
        raise SystemExit('两条双线性通路不一致: %.3e' % dmax)
    for _nm, k, lo, la, _t, _o in CHECKS:
        v = T.elev_at(lo, la, use_max=(k == 'elev_max'))
        print('  terrain.elev_at(%7.2f, %6.2f, use_max=%-5s) = %9.2f m  '
              'friction=%.4f' % (lo, la, k == 'elev_max', v, T.friction_scale(v)))
    print('terrain.stats():', json.dumps(
        {k: v for k, v in T.stats().items() if k != 'meta'},
        ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
