#!/usr/bin/env bash
# 逐月: 下载(可能被 timeout 打断在客户端校验阶段, 但 nc 已完整) → 立即计算+门控
# 2026-09-20 夜里: copernicusmarine 下完后会卡在校验里, python 端的 subprocess timeout
# 在本机 interop 下不生效, 所以改用 bash 显式逐月展开(不依赖子进程超时机制)。
set -u
cd /mnt/g/气象/Typhoon-sim
PY="/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe"
LOG=/mnt/g/tmp/pipe3.log
echo "== start $(date +%H:%M) ==" >> "$LOG"

run_month () {
  local M="$1"
  local NC="C:/cmems_tmp/thetao_2024${M}15.nc"
  local G2="simulator/env/train_cache/cmems/g2_2024${M}15.npz"
  if [ -f "$G2" ]; then echo "[skip] $M 已有" >> "$LOG"; return; fi
  if [ ! -f "/mnt/c/cmems_tmp/thetao_2024${M}15.nc" ]; then
    echo "[dl] 月 $M 开始 $(date +%H:%M)" >> "$LOG"
    timeout 600 "$PY" -u tools/pipeline_cmems.py --year 2024 --day 15 --months "$M" >> "$LOG" 2>&1
    echo "[dl] 月 $M 结束 $(date +%H:%M)" >> "$LOG"
  fi
  if [ -f "/mnt/c/cmems_tmp/thetao_2024${M}15.nc" ]; then
    timeout 900 "$PY" -u tools/build_ohc_from_cmems.py --nc "$NC" --date "2024-${M}-15" >> "$LOG" 2>&1
    echo "[calc] 月 $M 完成 $(date +%H:%M)" >> "$LOG"
  else
    echo "[fail] 月 $M 没有 nc" >> "$LOG"
  fi
}

for M in 04 05 06 07 08 10 11 12; do
  run_month "$M"
done
echo "== DONE $(date +%H:%M) ==" >> "$LOG"
