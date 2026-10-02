#!/usr/bin/env bash
# 三次运行跑完之后的一次性收尾(2026-09-21 夜):
#   ① b-deck 去重 + 各洋区汇总(系统数/命名数/峰值/ACE)
#   ② 两个年份的 6 个视频(sst/ohc/shear × global/wpac)
#   ③ 2025-09-15 vs 2100-09-15 的年代对照三联图(同一天, 便于直接比)
set -u
PY='/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe'
cd /mnt/g/气象/Typhoon-sim || exit 1
export PYTHONIOENCODING=utf-8

echo "=== ① b-deck 去重与汇总 $(date '+%F %T') ==="
"$PY" tools/clean_staged_bdeck.py --dir sim_typhoon

echo "=== ② 视频 2025 $(date '+%F %T') ==="
"$PY" tools/make_video.py --data 'G:\气象\数据\sim2025' --all --fps 24
echo "=== ② 视频 2100 $(date '+%F %T') ==="
"$PY" tools/make_video.py --data 'G:\气象\数据\sim2100' --all --fps 24

echo "=== ③ 年代对照图 $(date '+%F %T') ==="
"$PY" tools/make_montage.py --data 'G:\气象\数据\sim2100' --ts 2100091512 \
  --ts2 2025091512 --data2 'G:\气象\数据\sim2025' \
  --out 'G:\气象\数据\sim2100\montage_21000915_vs_20250915.png'
echo "=== 收尾完成 $(date '+%F %T') ==="
