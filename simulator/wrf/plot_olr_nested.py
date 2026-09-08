# -*- coding: utf-8 -*-
"""WRF 嵌套模拟 OLR 云图, 图例规范与参考视频(Wanda 5612)完全一致:
OLR 灰度: 白=100W/m2(深对流), 黑=300W/m2(晴空), 刻度 100/150/200/250/300。
顶部标题 + 底部色标 + 经纬网 + 海岸线(黄线)。
"""
import glob, os, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import gaussian_filter
from netCDF4 import Dataset

RUN = 'C:/Users/Yukioto/AppData/Local/Temp/dsh_share/nested_wrfout'
OUTDIR = 'C:/Users/Yukioto/AppData/Local/Temp/dsh_share/nested_olr'
os.makedirs(OUTDIR, exist_ok=True)

for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams['axes.unicode_minus'] = False

# 白(100) -> 灰 -> 黑(300): 与视频图例一致(白=低OLR=深对流)
cmap = LinearSegmentedColormap.from_list('olr', ['#ffffff', '#d8d8d8', '#a8a8a8',
                                                 '#787878', '#4a4a4a', '#202020', '#000000'])
CBAR = [100, 150, 200, 250, 300]

def g(ds, n):
    a = np.asarray(ds.variables[n][:], dtype=float)
    return a[0] if a.ndim >= 3 else a

# d02 frames (3km) - typhoon focus; also d01 wide view
frames = sorted(glob.glob(os.path.join(RUN, 'wrfout_d02_2026-08-02_1[89]*')) + glob.glob(os.path.join(RUN, 'wrfout_d02_2026-08-02_2*')))
print('d02 frames:', len(frames))
for f in frames:
    ts = os.path.basename(f).split('_')[-1].replace(':', '').replace('-', '')
    ds = Dataset(f)
    lat = g(ds, 'XLAT'); lon0 = g(ds, 'XLONG')
    lon = np.where(lon0 < 0, lon0 + 360, lon0)
    olr = gaussian_filter(g(ds, 'OLR'), sigma=1.2)   # 平滑模拟视频观感
    u10 = g(ds, 'U10'); v10 = g(ds, 'V10')
    psfc = g(ds, 'PSFC')/100.0
    fig, ax = plt.subplots(figsize=(12, 10), dpi=130)
    im = ax.pcolormesh(lon, lat, olr, cmap=cmap, vmin=100, vmax=300, shading='auto')
    ax.contour(lon, lat, psfc, levels=np.arange(940, 1016, 4), colors='#ffd23e',
               linewidths=0.8, alpha=0.8)
    step = max(1, 220//30)
    ax.quiver(lon[::step,::step], lat[::step,::step], u10[::step,::step], v10[::step,::step],
              color='#00e5ff', scale=500, width=0.0022, alpha=0.85, headwidth=3.5)
    # 台风中心
    i,j = np.unravel_index(np.argmin(psfc), psfc.shape)
    ax.plot(lon[i,j], lat[i,j], marker='*', ms=20, mfc='#ff2d55', mec='white', mew=1.5, zorder=6)
    # 图例规范(视频同款): 顶部标题 + 底部色标
    ax.set_title(f'WRF 嵌套模拟 OLR (D02 3km)  {ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}Z  UTC', fontsize=13)
    ax.set_xlabel('经度 °E'); ax.set_ylabel('纬度 °N')
    ax.set_facecolor('#0a0a0a')
    cb = fig.colorbar(im, ax=ax, shrink=0.9, pad=0.03)
    cb.set_ticks(CBAR)
    cb.set_label('向外长波辐射 OLR (W/m²)  [白=深对流, 黑=晴空]', fontsize=11)
    cb.ax.tick_params(labelsize=10)
    fig.savefig(os.path.join(OUTDIR, f'olr_d02_{ts}.png'), bbox_inches='tight')
    plt.close(fig)
    ds.close()
    print('saved', ts)
print('ALL DONE')
