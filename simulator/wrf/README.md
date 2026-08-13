# 真 WRF 管线(simulator/wrf/)

用 **ERA5 初始/边界场 + 真 WRF 模式**直接模拟台风,为模拟器"内核A"提供数据。
无需手动编译 WRF:在 WSL2 Ubuntu 里一键 apt 依赖 + 官方源码编译(一次性约 30-60 分钟)。

## 流程

```
ERA5(CDS, GRIB) ──► WPS(geogrid→ungrib→metgrid) ──► real.exe
                                                       │
                        [架空台风: bogus_vortex.py 涡旋注入 wrfinput]
                                                       ▼
                                                wrf.exe ──► wrfout(NetCDF, 6h)
                                                       │
        ┌──────────────────────────────────────────────┤
        ▼                                              ▼
 track_vortex.py                              wrfout_to_fields.py
 (涡旋追踪: 路径/中心气压/最大风速/r34)         (→ env/wrfdir/*.npz 121×360 场)
        │                                              │
        ▼                                              ▼
 wrfdir/track.json                             模拟器渲染(内核A 直接使用)
        │
        ▼
 模拟器"内核A"直接用 WRF 模拟的台风(不再用简化物理状态机)
```

## 使用(Windows)

### 方式一: 模拟器界面全自动(推荐)

启动模拟器后:
1. 底栏切到内核 **A(真实大气)**;
2. 点 **生成设置**:填坐标/时间,**初始强度(kt)默认 30**(架空台风用,可改;历史台风可不注入),RMW、模拟天数可选;
3. 点 **开始模拟**:
   - 已有 WRF 结果且覆盖该时段 → 直接播放(WRF 模拟的台风);
   - WRF 场已有但没追踪 → 秒级补跑追踪;
   - 完全没有 WRF 数据 → **后台自动跑完整管线**(下载 ERA5 → WSL 跑 WRF → 涡旋追踪,数小时),底部状态栏显示进度,完成自动载入,不需要碰命令行。

### 方式二: 命令行

```powershell
# 0. 一次性: 安装 WSL2 Ubuntu(管理员 PowerShell)
wsl --install -d Ubuntu

# 1. 全流程(含首次编译, 数小时): 默认西太区域 95E-205E/0-50N, dx=30km
.\simulator\wrf\run_wrf.ps1 -Start 2026-08-01 -Days 10 -Setup

# 2. 架空/未来台风: 默认注入 30kt 初始涡旋(可 -BogusVmax 自定义)
.\simulator\wrf\run_wrf.ps1 -Start 2026-08-01 -Days 10 `
    -BogusVmax 70 -BogusLat 18 -BogusLon 128 -BogusRmw 30

# 3. 真实历史台风(用 ERA5 自带环流, 不注入涡旋)
.\simulator\wrf\run_wrf.ps1 -Start 2000-08-15 -Days 10 -NoBogus

# 4. 只跑某步
.\simulator\wrf\run_wrf.ps1 -Start 2026-08-01 -Days 10 -SkipFetch   # 已下载好场
.\simulator\wrf\run_wrf.ps1 -Start 2026-08-01 -Days 10 -SkipRun     # 只下载+解析已有 wrfout
```

参数: `-Lon0/-Lon1/-Lat0/-Lat1` 区域, `-Dx` 分辨率(米), `-Days` 天数,
`-HistInterval` 输出步长(分钟, **默认 12, 范围 1-60**), `-BogusVmax/-BogusRmw` 初始涡旋。
界面"生成设置"里同样可填(输出步长默认 12 分)。

> 注意: 步长越短 wrfout 文件越多(12 分钟 ≈ 120 帧/天, 每帧数十 MB, 10 天约 30-60GB);
> 纯看路径用 60 分钟即可, 要看台风旋转/眼壁动画再调小。
1. 有 `wrfdir/track.json`(涡旋追踪结果)→ **直接使用 WRF 模拟的台风**(路径/强度来自 wrfout,渲染场来自 wrfout);
2. 有 wrfdir 但无追踪 → WRF 场 + 数据驱动生成台风;
3. 无 WRF → 回落 Open-Meteo IFS/ERA5 数据驱动。

## 数据说明

- **ERA5**(优先): CDS API(`~/.cdsapirc` 已有凭证), 26 个气压层(u/v/t/z/rh)
  + 单层(10m 风/2m 温露点/SST/MSLP/地表气压/海陆掩膜/皮肤温度), 6h 间隔, GRIB。
  自动读取 Windows 系统代理。
- **GFS**(备用): `fetch_gfs.py`, NOMADS filter 服务, 免注册。
- **geog 静态数据**: 低分辨率版(~1GB, 一次性, mmm.ucar.edu)。

## 时间成本(参考, 笔记本)

| 步骤 | 时间 |
|---|---|
| WSL Ubuntu 安装 | 5-15 分钟(用户操作) |
| WRF/WPS 编译(一次性) | 30-60 分钟 |
| ERA5 下载(10 天, 全区域) | 30 分钟-2 小时(CDS 队列) |
| WRF 积分(30km, 100°×50° 域, 10 天) | 3-10 小时 |
| wrfout 解析 | 1-2 分钟 |

## 文件

| 文件 | 作用 |
|---|---|
| `run_wrf.ps1` | Windows 总启动器(检查 WSL/编译/下载/运行/注入/追踪/解析) |
| `setup_wsl.sh` | WSL 内: apt 依赖 + WRF/WPS 源码编译 + geog |
| `run_wrf.sh` | WSL 内: geogrid→ungrib→metgrid→real→(bogus)→wrf |
| `fetch_era5.py` / `fetch_gfs.py` | 初始/边界场下载(ERA5 优先) |
| `namelist.wps.template` / `namelist.input.template` | 区域/物理方案模板(台风区默认) |
| `Vtable.ERA5` | ungrib 解码表(与 WPS 自带 Vtable.ERA-interim.pl 一致) |
| `bogus_vortex.py` | 架空台风涡旋注入(Rankine 风场+暖核+湿度增强) |
| `track_vortex.py` | wrfout 涡旋追踪(路径/中心气压/最大风速/R34 → track.json) |
| `wrfout_to_fields.py` | wrfout → `env/wrfdir/` 121×360 逐日场(含 OLR/降水代理) |
