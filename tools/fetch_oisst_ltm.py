# -*- coding: utf-8 -*-
"""抓 OISST v2.1 的 1991-2020 月气候态(0.25°, 46 MB)当 SST 基态。

为什么要它(而不是继续用 ERA5):
  1. **ERA5 的 SST 在 EPAC 赤道冷舌有强冷偏差** —— 0N/110W ERA5 给 22.7°C,
     而同期卫星产品是 28.0°C。用户看对比图时一眼就看出了"EPAC 赤道偏冷"。
  2. ERA5 单年基态会把**那一年的 ENSO 状态**烤进"气候态"。
  OISST 是卫星+浮标融合(与用户用的 REMSS 同源), 而且这个文件本身就是
  1991-2020 的 30 年月气候态 —— 两个问题一次解决。

PSL 的 fileServer 只是慢(实测 ~20 KB/s, 46 MB 约 40 分钟); dodsC 大范围读
会挂。所以直下 + `curl -C -` 断点续传 + 多轮重试。

用法: python tools/fetch_oisst_ltm.py     (可后台)
输出: simulator/env/train_cache/oisst_ltm.nc
"""
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'simulator', 'env', 'train_cache', 'oisst_ltm.nc')
URL = ('https://psl.noaa.gov/thredds/fileServer/Datasets/noaa.oisst.v2.highres/'
       'sst.mon.ltm.1991-2020.nc')


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for k in range(30):
        sz = os.path.getsize(OUT) if os.path.exists(OUT) else 0
        if sz > 40_000_000:
            print('完成 %.1f MB' % (sz / 1e6), flush=True)
            break
        r = subprocess.run(
            ['curl', '-sSL', '--noproxy', '*', '-m', '3000',
             '--retry', '5', '--retry-all-errors', '--retry-delay', '5',
             '--speed-time', '150', '--speed-limit', '512',
             '-C', '-', '-o', OUT, '-w', '%{http_code}', URL],
            capture_output=True)
        sz = os.path.getsize(OUT) if os.path.exists(OUT) else 0
        print('  轮 %2d: %.1f MB (http=%s)' % (k + 1, sz / 1e6,
                                               (r.stdout or b'').decode().strip()),
              flush=True)
        time.sleep(3)
    print('DONE %d' % (os.path.getsize(OUT) if os.path.exists(OUT) else 0))


if __name__ == '__main__':
    main()
