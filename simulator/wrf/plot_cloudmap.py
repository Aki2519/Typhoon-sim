# -*- coding: utf-8 -*-
"""当前台风云图: 红外亮温(云顶温度) + SLP + 850hPa 风场。"""
import os, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from netCDF4 import Dataset
for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttf'):
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams['axes.unicode_minus'] = False

SRC = 'C:/Users/Yukioto/AppData/Local/Temp/dsh_share/latest_wrfout.nc'
OUT = 'C:/Users/Yukioto/AppData/Local/Temp/dsh_share/typhoon_cloudmap.png'

ds = Dataset(SRC)
def g(n):
    a = np.asarray(ds.variables[n][:], dtype=float)
    return a[0] if a.ndim >= 3 else a

lat = g('XLAT'); lon = g('XLONG')
lon = np.where(lon < 0, lon + 360.0, lon)   # 0-360E
psfc = g('PSFC')/100.0
hgt = g('HGT'); t2 = g('T2')
slp = psfc * np.exp(hgt*9.81/(287.05*t2))

P = g('P')+g('PB')
T = (300.0+g('T'))*(P/100000.0)**(287.05/1004.0)
cld = g('CLDFRA')
kmax = np.argmax(cld, axis=0)
cldmax = np.max(cld, axis=0)
tbb = T[kmax, np.arange(T.shape[1])[:,None], np.arange(T.shape[2])[None,:]].copy()
tbb = np.where(cldmax < 0.05, g('T2'), tbb)   # 无云处用地表温度; 云顶=CLDFRA最大层温度

U = g('U'); V = g('V')
Uc = 0.5*(U[..., :-1]+U[..., 1:])
Vc = 0.5*(V[..., :-1, :]+V[..., 1:, :])
k850 = np.argmin(np.abs(P-85000.0), axis=0)
ny, nx = P.shape[1:]
u850 = Uc[k850, np.arange(ny)[:,None], np.arange(nx)[None,:]]
v850 = Vc[k850, np.arange(ny)[:,None], np.arange(nx)[None,:]]
rnc = g('RAINC')+g('RAINNC')

i,j = np.unravel_index(np.argmin(slp), slp.shape)
clat, clon = lat[i,j], lon[i,j]
dlat = lat-clat; dlon = (lon-clon)*np.cos(np.deg2rad(clat))
dkm = np.sqrt((dlat*111.0)**2+(dlon*111.0)**2)
spd = np.sqrt(u850**2+v850**2)
vmax = spd[dkm<=400.0].max()
ts = ds.variables['Times'][:][0].tobytes().decode()

ds.close()

# --- plot ---
cmap = LinearSegmentedColormap.from_list('ir', ['#000000','#1a1a1a','#3b3b3b','#6b6b6b','#9c9c9c','#c8c8c8','#e8e8e8','#ffffff'])
fig, ax = plt.subplots(figsize=(12, 9.5), dpi=110)
im = ax.pcolormesh(lon, lat, tbb, cmap=cmap, vmin=190, vmax=300, shading='auto')
cs = ax.contour(lon, lat, slp, levels=np.arange(940, 1016, 4), colors='#ffd23e', linewidths=0.9, alpha=0.85)
ax.clabel(cs, fmt='%d', fontsize=8, colors='#ffd23e')
step = max(1, 407//36)
ax.quiver(lon[::step,::step], lat[::step,::step], u850[::step,::step], v850[::step,::step],
          color='#00e5ff', scale=450, width=0.0022, alpha=0.85, headwidth=3.5)
ax.plot(clon, clat, marker='*', ms=22, mfc='#ff2d55', mec='white', mew=1.5, zorder=6, label='台风中心')
ax.annotate(f'{clat:.1f}°N, {clon:.1f}°E', (clon, clat), textcoords='offset points',
            xytext=(14, 12), color='white', fontsize=12, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', fc='#ff2d55', ec='white', alpha=0.9))
# 范围: 以中心为中心 ±12°
ext = 12.0
ax.set_xlim(clon-ext, clon+ext); ax.set_ylim(clat-ext*0.82, clat+ext*0.82)
ax.set_xlabel('经度 °E'); ax.set_ylabel('纬度 °N')
t1 = 'WRF 台风云图(IR 亮温)  模型时间 %s  UTC' % ts
t2 = '中心 %.1fN %.1fE   中心气压 %.0f hPa   850hPa VMAX %.0f m/s (%.0f kt)' % (clat, clon, slp[i,j], vmax, vmax*1.944)
ax.set_title(t1 + chr(10) + t2, fontsize=13, color='white')
cb = fig.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
cb.set_label('云顶温度 K(白=深对流)', color='white')
cb.ax.yaxis.set_tick_params(color='white'); plt.setp(plt.getp(cb.ax.axes, 'yticklabels'), color='white')
for s in ax.spines.values(): s.set_color('#555')
ax.tick_params(colors='white')
ax.set_facecolor('#0a0a0a'); fig.patch.set_facecolor('#0a0a0a')
fig.tight_layout()
fig.savefig(OUT, facecolor='#0a0a0a')
print('SAVED:', OUT)
print(f'center {clat:.2f} {clon:.2f} p={slp[i,j]:.1f} vmax={vmax:.1f}')
