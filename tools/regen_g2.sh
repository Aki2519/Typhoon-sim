#!/usr/bin/env bash
# 用修好的写盘逻辑(NaN 最近填充)重算全部 12 个月的 g2 单日场。
# 2026-09-20: 早期写的 g2 文件近岸留 NaN(虽然读取时会填, 但归档文件应干净)。
set -u
cd /mnt/g/气象/Typhoon-sim
PY="/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe"
LOG=/mnt/g/tmp/reg2.log
echo "== start $(date +%H:%M) ==" >> "$LOG"
for M in 01 02 03 04 05 06 07 08 09 10 11 12; do
  NC="C:/cmems_tmp/thetao_2024${M}15.nc"
  if [ ! -f "/mnt/c/cmems_tmp/thetao_2024${M}15.nc" ]; then
    echo "[skip] 月 $M 没有 nc" >> "$LOG"; continue
  fi
  timeout 900 "$PY" -u tools/build_ohc_from_cmems.py --nc "$NC" --date "2024-${M}-15" --force >> "$LOG" 2>&1
  echo "[ok] 月 $M $(date +%H:%M)" >> "$LOG"
done
echo "== DONE $(date +%H:%M) ==" >> "$LOG"
