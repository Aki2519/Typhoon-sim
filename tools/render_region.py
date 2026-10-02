# -*- coding: utf-8 -*-
"""渲染任意 SST 场的一个区域(用 REMSS 调色板), 供目视检查。
用法: python tools/render_region.py --out x.png --region 120,160,20,45 f1.npz:sst:9 f2.nc:sst:9
"""
import argparse, os, sys
os.environ.setdefault('SDL_VIDEODRIVER','dummy')
import numpy as np, pygame
pygame.init(); pygame.display.set_mode((64,64))
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from simulator.simcore import render as R
F=pygame.font.Font(os.path.join(ROOT,'assets','font','MapleMono-NF-CN-Medium.ttf'),22)

def layered(a, lat, lon, lo0, lo1, la0, la1):
    """把 (nlat,nlon) 按真实坐标轴裁到区域, 上色 → Surface(北在上)。"""
    lons=np.where(lon>180, lon-360, lon) if lon.max()>180 else lon
    il=np.nonzero((lons>=lo0)&(lons<=lo1))[0]
    jl=np.nonzero((lat>=la0)&(lat<=la1))[0]
    if il.size<2 or jl.size<2: return None
    sub=a[np.ix_(jl,il)]
    ok=np.isfinite(sub)
    t=np.clip(np.where(ok,sub,0)/33.0,0,1)*255.0
    rgb=R.LUT_SST_STEPS[t.astype(int)][...,:3].copy()
    rgb[~ok]=(60,60,64)
    if lat[jl[0]] < lat[jl[-1]]: rgb=np.flipud(rgb)
    return pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(),(rgb.shape[1],rgb.shape[0]),'RGB')

def load(spec):
    path,key,idx = spec.split(':')
    if path.endswith('.npz'):
        z=np.load(path); a=z[key].astype(np.float32)
        if a.ndim==3: a=a[int(idx)]
        if 'lat' in z.files: return a, z['lat'], z['lon']
        gl=-59.75+np.arange(a.shape[0])*(120.0/a.shape[0]); go=0.25+np.arange(a.shape[1])*(360.0/a.shape[1])
        return a, gl, go
    import netCDF4
    cwd=os.getcwd(); os.chdir(os.path.dirname(os.path.abspath(path)))
    d=netCDF4.Dataset(os.path.basename(path))
    a=np.asarray(d.variables[key][:],np.float32).squeeze()
    la=np.asarray(d.variables['lat' if 'lat' in d.variables else 'latitude'][:])
    lo=np.asarray(d.variables['lon' if 'lon' in d.variables else 'longitude'][:])
    d.close(); os.chdir(cwd)
    if a.ndim==3: a=a[int(idx)]
    if np.nanmax(a)>200: a=a-273.15
    if la[0]>la[-1]: a=a[::-1]; la=la[::-1]
    return a, la, lo

ap=argparse.ArgumentParser(); ap.add_argument('--out'); ap.add_argument('--region')
ap.add_argument('--title',default=''); ap.add_argument('srcs',nargs='+')
A=ap.parse_args()
lo0,lo1,la0,la1=[float(x) for x in A.region.split(',')]
W=560; H=int(W*(la1-la0)/(lo1-lo0))
imgs=[]
for s in A.srcs:
    nm,spec=s.split('=',1)
    a,la,lo=load(spec)
    surf=layered(a,la,lo,lo0,lo1,la0,la1)
    imgs.append((nm, pygame.transform.smoothscale(surf,(W,H)) if surf else None))
img=pygame.Surface((W*len(imgs)+8*(len(imgs)-1), H+34)); img.fill((14,14,22))
img.blit(F.render('%s   经度 %.0f-%.0fE  纬度 %.0f-%.0fN'%(A.title,lo0,lo1,la0,la1),True,(255,255,255)),(6,4))
for k,(nm,sf) in enumerate(imgs):
    x=k*(W+8)
    img.blit(F.render(nm,True,(255,235,120)),(x+4,30-24)) if False else None
    img.blit(F.render(nm,True,(255,235,120)),(x+4,4))
    img.blit(sf,(x,34))
pygame.image.save(img,A.out); print('->',A.out,img.get_size())
