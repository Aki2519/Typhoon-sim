#!/bin/bash
# 定点后(_GEN_RATE 已按 15 种子均值缩到 ~0.86)的两窗口重跑
# 目的: ① 年生成落到 250; ② 用定点后的配置出**最终文档数字**
cd /mnt/g/气象/Typhoon-sim || exit 1

PY="/mnt/c/Users/Yukioto/AppData/Local/Programs/Python/Python312/python.exe"
SEEDS="20250801,11,23,37,53,67,79,83,97,101,113,127,131,149,151"
P="stillbornFrac=0.60,stillbornK=0.06"

echo "############ RATE-FIXED NH WINDOW (2025-07-01) $(date '+%F %T') ############"
"$PY" tools/multi_year_check.py --days 100 --start "2025-07-01 00" \
    --tag sb60g_nh_15seed --param "$P" --seeds "$SEEDS"
echo "############ RATE-FIXED NH DONE rc=$? $(date '+%F %T') ############"

echo "############ RATE-FIXED SH WINDOW (2025-01-01) $(date '+%F %T') ############"
"$PY" tools/multi_year_check.py --days 100 --start "2025-01-01 00" \
    --tag sb60g_sh_15seed --param "$P" --seeds "$SEEDS"
echo "############ RATE-FIXED SH DONE rc=$? $(date '+%F %T') ############"
echo "############ RATE-FIXED ALL DONE $(date '+%F %T') ############"
