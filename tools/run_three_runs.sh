#!/usr/bin/env bash
# 用户 2026-09-21 夜里要求的三次运行(顺序执行, 避免互相抢 CPU / 抢数据目录):
#   ① 2100-09-15 三图(sst/ohc/shear)
#   ② 2025 全年(带扰动 + 本轮全部修复: 瞬时口径 / 命名时间 / 孤立洞与尖峰填充)
#   ③ 2100 全年(同上)
set -u
PY='/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe'
cd /mnt/g/气象/Typhoon-sim || exit 1
export PYTHONIOENCODING=utf-8
echo "=== ① 2100-09-15 三图  $(date '+%F %T') ==="
"$PY" tools/sim_year.py --year 2100 --start '2100-09-15 00' --days 1 --zoom 1 \
  --data 'G:\气象\数据\sim2100'
echo "=== ② 2025 全年 $(date '+%F %T') ==="
"$PY" tools/sim_year.py --year 2025 --days 365 --zoom 1 --data 'G:\气象\数据\sim2025'
echo "=== ③ 2100 全年 $(date '+%F %T') ==="
"$PY" tools/sim_year.py --year 2100 --days 365 --zoom 1 --data 'G:\气象\数据\sim2100'
echo "=== ALL DONE $(date '+%F %T') ==="
