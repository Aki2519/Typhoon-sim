@echo off
chcp 65001 >nul
cd /d "%~dp0"
python simulator/run.py --seed 1 --years 3
pause
