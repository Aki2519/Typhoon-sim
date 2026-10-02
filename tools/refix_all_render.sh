#!/usr/bin/env bash
# 统一出图路径之后的一次性重出(2026-09-21): 帧(PNG)与视频(mp4)都走
# compare_fields.pad_polar + rgb_domain ⇒ 北在上 + ±90 画布 + 域外标注, 不再各写一套。
set -u
PY='/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe'
cd /mnt/g/气象/Typhoon-sim || exit 1
export PYTHONIOENCODING=utf-8

echo "=== ① 重出帧 2025 $(date '+%F %T') ==="
"$PY" tools/refresh_frames.py --data 'G:\气象\数据\sim2025'
echo "=== ① 重出帧 2100 $(date '+%F %T') ==="
"$PY" tools/refresh_frames.py --data 'G:\气象\数据\sim2100'

echo "=== ② 视频 2025 $(date '+%F %T') ==="
"$PY" tools/make_video.py --data 'G:\气象\数据\sim2025' --all --fps 24
echo "=== ② 视频 2100 $(date '+%F %T') ==="
"$PY" tools/make_video.py --data 'G:\气象\数据\sim2100' --all --fps 24

echo "=== ③ 三联图与年代对照 $(date '+%F %T') ==="
"$PY" tools/make_montage.py --data 'G:\气象\数据\sim2100' --ts 2100091512
"$PY" tools/make_montage.py --data 'G:\气象\数据\sim2100' --ts 2100091512 \
  --ts2 2025091512 --data2 'G:\气象\数据\sim2025' \
  --out 'G:\气象\数据\sim2100\montage_21000915_vs_20250915.png'
echo "=== 完成 $(date '+%F %T') ==="
