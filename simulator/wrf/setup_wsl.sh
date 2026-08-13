#!/bin/bash
# simulator/wrf/setup_wsl.sh —— 一次性: 安装依赖 + 编译 WRF/WPS + 下载 geog 静态数据
# 在 WSL Ubuntu 内运行(由 run_wrf.ps1 自动调用): bash /mnt/<盘>/.../simulator/wrf/setup_wsl.sh
set -e

WRF_ROOT="$HOME/wrf"
mkdir -p "$WRF_ROOT"
cd "$WRF_ROOT"

echo "=== [1/4] 安装系统依赖 ==="
sudo apt-get update -y
sudo apt-get install -y gfortran gcc g++ cpp m4 make tcsh perl wget curl git \
    libopenmpi-dev libnetcdf-dev libnetcdff-dev libhdf5-dev \
    libpng-dev unzip bzip2 \
    python3 python3-netcdf4 python3-numpy python3-scipy
# jasper 在 Ubuntu 24.10+/25.04 已被移除 → 缺失时改用 WPS 自带 --build-grib2-libs
if ! sudo apt-get install -y libjasper-dev 2>/dev/null; then
  echo "[setup] libjasper-dev 不可用(Ubuntu 25.04+ 已移除), 稍后用 --build-grib2-libs 源码构建"
fi

# 若 WRF/WPS 源码已存在则跳过下载
if [ ! -d "$WRF_ROOT/WRF" ]; then
  echo "=== [2/4] 下载 WRF/WPS 源码 ==="
  wget -q https://github.com/wrf-model/WRF/archive/refs/tags/v4.6.1.tar.gz -O wrf.tar.gz
  wget -q https://github.com/wrf-model/WPS/archive/refs/tags/v4.6.tar.gz -O wps.tar.gz
  tar -xzf wrf.tar.gz && mv WRF-4.6.1 WRF
  tar -xzf wps.tar.gz && mv WPS-4.6 WPS
fi

echo "=== [3/4] 编译 WRF(em_real, 约 20-60 分钟) ==="
cd "$WRF_ROOT/WRF"
export NETCDF=/usr
export WRF_EM_CORE=1
export WRF_NMM_CORE=0
printf '34\n1\n' | ./configure > configure.log 2>&1 || true
# 若 34 不是 dmpar 选项, 自动改选 GNU dmpar
if ! grep -q 'dmpar' configure.wrf; then
  echo "[setup] configure 未选到 dmpar, 自动重选…"
  printf '15\n1\n' | ./configure > configure.log 2>&1 || true   # 15 = GNU gfortran dmpar(旧版编号)
  if ! grep -q 'dmpar' configure.wrf; then
    echo "[setup] 自动选择失败, 请手动检查 configure.log 后重跑"; exit 1
  fi
fi
# Ubuntu 多架构库路径修正(库在 /usr/lib/x86_64-linux-gnu, NETCDF=/usr 时找不到)
sed -i 's|-L/usr/lib |-L/usr/lib/x86_64-linux-gnu |g; s|-L/usr/lib$|-L/usr/lib/x86_64-linux-gnu|g' configure.wrf
# 移除与 Ubuntu 预编译 netcdf 库不兼容的 LTO 标志(否则 nf_* 链接失败)
sed -i 's/ -flto=auto -ffat-lto-objects//' configure.wrf
# NoahMP 子模块(tar 下载无 git 子模块时手动建链接)
if [ ! -f phys/module_sf_noahmpdrv.F ]; then
  echo "[setup] 手动建立 NoahMP 符号链接(子模块未随 tar 下载)…"
  if [ -d phys/noahmp/drivers/wrf ]; then
    ln -sf noahmp/drivers/wrf/module_sf_noahmpdrv.F phys/module_sf_noahmpdrv.F
    ln -sf noahmp/src/module_sf_noahmp_glacier.F phys/module_sf_noahmp_glacier.F
    ln -sf noahmp/src/module_sf_noahmp_groundwater.F phys/module_sf_noahmp_groundwater.F
    ln -sf noahmp/src/module_sf_noahmplsm.F phys/module_sf_noahmplsm.F
    # NoahMP 新版参数表名为 NoahmpTable.TBL(旧版 MPTABLE.TBL)
    if [ -f phys/noahmp/parameters/NoahmpTable.TBL ]; then
      ln -sf ../phys/noahmp/parameters/NoahmpTable.TBL run/MPTABLE.TBL 2>/dev/null || true
    else
      ln -sf ../phys/noahmp/parameters/MPTABLE.TBL run/MPTABLE.TBL 2>/dev/null || true
    fi
  else
    echo "[setup] 警告: phys/noahmp 缺失! 请先下载 NCAR/noahmp 到 phys/noahmp(官方子模块)"
  fi
fi
# GCC 15+ 修复: pack_spatial.c 重复声明(与 gribfuncs.h 冲突)
if [ -f external/io_grib1/MEL_grib1/pack_spatial.c ]; then
  sed -i '/^    unsigned long grib_local_ibm();$/d' external/io_grib1/MEL_grib1/pack_spatial.c
fi
# GCC 15+ 修复: rsl_bcast.c K&R 函数指针声明
if [ -f external/RSL_LITE/rsl_bcast.c ]; then
  sed -i 's/int (\*dfcn)() ;/int (*dfcn)(void *) ;/' external/RSL_LITE/rsl_bcast.c
fi
./compile em_real -j "$(nproc)" > compile.log 2>&1
ls -la main/wrf.exe main/real.exe | cat

echo "=== [4/4] 编译 WPS 并下载 geog 静态数据 ==="
cd "$WRF_ROOT/WPS"
export NETCDF=/usr
if [ -f /usr/include/jasper/jasper.h ]; then
  export JASPERLIB=/usr/lib/x86_64-linux-gnu
  export JASPERINC=/usr/include/jasper
  printf '3\n' | ./configure > configure.log 2>&1 || true
else
  # jasper 缺失: 用 WPS 自带脚本源码构建 zlib/libpng/jasper 到 grib2/
  echo "[setup] 使用 --build-grib2-libs 源码构建 grib2 库(约 2 分钟)…"
  printf '3\n' | ./configure --build-grib2-libs > configure.log 2>&1 || true
  export JASPERLIB="$WRF_ROOT/WPS/grib2/lib"
  export JASPERINC="$WRF_ROOT/WPS/grib2/include"
fi
if ! grep -q 'dmpar' configure.wps; then
  printf '5\n' | ./configure > configure.log 2>&1 || true       # 5 = serial gfortran(旧版编号)
fi
# 强制修正编译器为 gfortran/gcc(configure 管道输入在部分版本不可靠)
sed -i 's/^SFC                 = pgf90/SFC                 = gfortran/' configure.wps
sed -i 's/^SCC                 = pgcc/SCC                 = gcc/' configure.wps
sed -i 's/^SFC                 = ifort/SFC                 = gfortran/' configure.wps
sed -i 's/^SCC                 = icc/SCC                 = gcc/' configure.wps
# 修正 PGI 风格 flags 为 gfortran(-Mfree → -ffree-form; -byteswapio → -fconvert=big-endian)
sed -i 's/-Mfree/-ffree-form/g; s/-byteswapio/-fconvert=big-endian/g' configure.wps
# g2/grib2 旧代码与 gfortran 15 类型检查冲突 → 放宽参数匹配
sed -i 's/FFLAGS              = \$(FORMAT_FREE) -fconvert=big-endian -O/FFLAGS              = \$(FORMAT_FREE) -fconvert=big-endian -O -fallow-argument-mismatch/' configure.wps
sed -i 's/F77FLAGS            = \$(FORMAT_FIXED) -fconvert=big-endian -O/F77FLAGS            = \$(FORMAT_FIXED) -fconvert=big-endian -O -fallow-argument-mismatch/' configure.wps
# Ubuntu 多架构库路径(WPS 链接 netcdf)
sed -i 's|-L$(NETCDF)/lib  -lnetcdf|-L/usr/lib/x86_64-linux-gnu -lnetcdff -lnetcdf|' configure.wps
./compile > compile.log 2>&1
ls -la geogrid.exe ungrib.exe metgrid.exe | cat

if [ ! -d "$WRF_ROOT/WPS_GEOG" ] || [ ! -f "$WRF_ROOT/WPS_GEOG/HGT_M_10M" ]; then
  echo "[setup] 下载 geog 低分辨率静态数据(约 1GB, 一次性)…"
  cd "$WRF_ROOT"
  wget -q https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz -O geog.tar.gz
  mkdir -p WPS_GEOG
  tar -xzf geog.tar.gz -C WPS_GEOG
fi

echo "=== 完成 ==="
ls "$WRF_ROOT/WRF/main/wrf.exe" "$WRF_ROOT/WRF/main/real.exe" \
   "$WRF_ROOT/WPS/geogrid.exe" "$WRF_ROOT/WPS/ungrib.exe" "$WRF_ROOT/WPS/metgrid.exe" | cat
