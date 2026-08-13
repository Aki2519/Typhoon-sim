#!/bin/bash
# simulator/wrf/run_wrf.sh —— 在 WSL 内运行完整 WRF 流程(geogrid→ungrib→metgrid→real→wrf)
# 输入: $HOME/wrf/case/ 下应有 namelist.wps / namelist.input / Vtable / ERA5:* grib 文件
# 输出: $HOME/wrf/case/wrfout/ 下的 wrfout_* 文件(再由 Windows 侧 wrfout_to_fields.py 解析)
set -e

WRF_ROOT="$HOME/wrf"
CASE="$WRF_ROOT/case"
WPS="$WRF_ROOT/WPS"
WRF_DIR="$WRF_ROOT/WRF"
export NETCDF=/usr
if [ -d "$WPS/grib2/lib" ]; then
  export JASPERLIB="$WPS/grib2/lib"
  export JASPERINC="$WPS/grib2/include"
  export LD_LIBRARY_PATH="$WPS/grib2/lib:$LD_LIBRARY_PATH"
else
  export JASPERLIB=/usr/lib/x86_64-linux-gnu
  export JASPERINC=/usr/include/jasper
fi

mkdir -p "$CASE/wrfout"
cd "$CASE"

echo "=== [1/5] geogrid(定义区域; 需 ~/.wrf/geog 缓存?) ==="
cp namelist.wps "$WPS/namelist.wps"
cd "$WPS"
rm -f geo_em.d01.nc
./geogrid.exe > geogrid.log 2>&1 || { echo "geogrid 失败: $(tail -5 geogrid.log)"; exit 1; }

echo "=== [2/5] ungrib(解码 ERA5 GRIB → 中间格式) ==="
rm -f ERA5:*
VTABLE=""
for cand in ungrib/Variable_Tables/Vtable.ECMWF "$CASE/Vtable.ERA5" \
            ungrib/Variable_Tables/Vtable.ERA-interim.pl \
            ungrib/Variable_Tables/Vtable.ERA5; do
  if [ -f "$cand" ]; then VTABLE="$cand"; break; fi
done
[ -n "$VTABLE" ] || { echo "无 Vtable"; exit 1; }
ln -sf "$VTABLE" Vtable
./link_grib.csh "$CASE"/input/era5_*.grib > link.log 2>&1
./ungrib.exe > ungrib.log 2>&1 || { echo "ungrib 失败: $(tail -5 ungrib.log)"; exit 1; }

echo "=== [3/5] metgrid(插值到模式网格) ==="
rm -f met_em.*
./metgrid.exe > metgrid.log 2>&1 || { echo "metgrid 失败: $(tail -5 metgrid.log)"; exit 1; }

echo "=== [4/5] real(生成初始场/边界条件) ==="
cp namelist.input "$WRF_DIR/run/namelist.input"
cd "$WRF_DIR/run"
rm -f wrfinput_d01 wrfbdy_d01 met_em.*
ln -sf "$CASE"/met_em.* .
./real.exe > real.log 2>&1 || { echo "real 失败: $(tail -5 real.log)"; exit 1; }

echo "=== [4b/5] 涡旋注入(bogus, 架空/未来台风时生效) ==="
if [ -f "$CASE/bogus.json" ]; then
  python3 - "$CASE/bogus.json" <<'PYEOF'
import json, sys
cfg = json.load(open(sys.argv[1]))
print('[bogus] 配置:', cfg)
PYEOF
  python3 "$CASE/tools/bogus_vortex.py" --wrfinput wrfinput_d01 \
    --lat "$(python3 -c "import json;print(json.load(open('$CASE/bogus.json'))['lat'])")" \
    --lon "$(python3 -c "import json;print(json.load(open('$CASE/bogus.json'))['lon'])")" \
    --vmax "$(python3 -c "import json;print(json.load(open('$CASE/bogus.json'))['vmax'])")" \
    --rmw "$(python3 -c "import json;print(json.load(open('$CASE/bogus.json')).get('rmw', 30))")"
else
  echo "[bogus] 未配置(无 bogus.json), 跳过"
fi

echo "=== [5/5] wrf(主积分, 视区域/天数需数小时) ==="
./wrf.exe > wrf.log 2>&1 || { echo "wrf 失败: $(tail -5 wrf.log)"; exit 1; }

cp wrfout_* "$CASE/wrfout/"
echo "=== 完成: $(ls "$CASE/wrfout" | wc -l) 个 wrfout 文件 ==="
