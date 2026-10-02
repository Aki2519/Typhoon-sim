# 02 环境场数据管线（simulator/env + modes.py + clouds）

## 一句话定位

这是"台风模拟器"的**环境数据层**：它用**离线烘焙的 npz 场库**（合成或 ERA5/CMEMS 再分析）给内核提供 SST/切变/引导风/OHC 等环境场，外加一套气候模态（ENSO/PDO/AMO/IOD）序列生成器。

> 本报告所有"实测"均指在 WSL `/usr/bin/python3`（3.14.4，numpy/scipy/xarray/netCDF4）或 Lead 指定的 Windows 解释器 `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`（3.14.2，含 pytest/pygame）上实际跑出的结果；"推断"均已显式标注。

---

## 1. 清单与职责

### 1.1 Python 模块（本报告范围）

| 文件 | 行数 | 职责 | 关键类 / 函数 |
|---|---|---|---|
| `simulator/env/__init__.py` | 2 | 包声明 | — |
| `simulator/env/field_io.py` | 257 | npz 读写契约、月气候态、逐日插值、`FieldAPI` 查询门面 | `save_monthly_field:75`、`load_monthly_field:90`、`build_climatology:121`、`interpolate_daily:160`、`FieldAPI:182` |
| `simulator/env/build_library.py` | 585 | 建库主流程：合成库（默认）/ 真实库（CDS+ORAS5） | `synth_sst_month:31`、`build_synthetic_library:303`、`build_real_library:407`、`_parse_era5_nc:507` |
| `simulator/env/downloader.py` | 227 | ERA5/ORAS5 下载、凭证检查、断点续传、缺失月记录、OHC 深度积分 | `fetch_era5_month:111`、`fetch_oras5_month:176`、`ohc_from_temperature:197` |
| `simulator/env/real_source.py` | 578 | **联网**真实场（Open-Meteo IFS 9km / ERA5），4 层源路由 + 物理代理 + 双线性上采样 | `available:130`、`RealFieldAPI:149`、`_source_for:173`、`_fetch_day:186`、`_derive:292`、`_gpi:556` |
| `simulator/env/wrf_fields.py` | 135 | **真 WRF** 输出优先、域外回落 real_source | `WrfFieldAPI:22`、`available:41`、`get_field:85`、`source_of:132` |
| `simulator/env/generator.py` | 344 | Analog 生成器：季节约束候选池 + 马氏距离 + 轨迹记忆 + 片段复用 + 5 级降级链 | `AnalogGenerator:65`、`_mode_vec:38`、`_pick_analog:118`、`_degrade_chain:135`、`generate_monthly:197` |
| `simulator/env/param_fields.py` | 211 | 副高/ITCZ/季风槽参数化（从 MSLP 提取 + 回归标定 + 留一法 CV） | `_extract_ridge:22`、`_extract_itcz:54`、`_extract_trough:90`、`calibrate:114` |
| `simulator/env/validate.py` | 200 | 留出法验证（剔除 12% 历史月→生成→比对）、OHC-SST 独立性、平滑度 | `holdout_validate:47`、`_ohc_sst_independence:138`、`_smoothness_check:160` |
| `simulator/modes.py` | 772 | 气候模态层：定义表、NOAA 抓取归档、两段式序列生成、硬约束+软评分、校验 | `MODES:28`、`archive_mode:206`、`load_mode_series:239`、`ModeGenerator:318`、`generate:564`、`validate:685` |
| `simulator/clouds/render.py` | 274 | 云图渲染：IR-BD 色阶 + 全球背景（ITCZ/季风槽/锋面）+ 单台风眼壁/螺旋雨带 | `CloudRenderer:106`、`irbd_color:34`、`_background:136`、`_typhoon_field:173`、`render_ir:259` |
| `simulator/clouds/__init__.py` | 2 | 包声明 | — |

行数实测：`wc -l` 合计 3587 行（`modes.py` 772 + `env/*.py` 2539 + `clouds/*.py` 276）。

### 1.2 数据目录（实测文件数 / 体量）

| 目录 | 文件数 | 体量 | 内容口径 |
|---|---|---|---|
| `simulator/env/library/` | 470 | 1.09 GB | `{var}_{YYYY}.npz`，17 变量 × 48 年（1979–2026）；`(12,121,360)` float32 |
| `simulator/env/climate/` | 18 | 150.8 MB | 16 个 `{var}_clim.npz`（`clim`+`std`，float64）+ `currents_2019.npz` + `tchp.npz` |
| `simulator/env/train_cache/` | 472 | 0.74 GB | 原始/中间缓存（`era5/`45 + `cmems/`29 + `ibtracs/` + `idx_*.txt`×11 + NCEP/OISST/IBTrACS） |
| `simulator/env/model/` | 25 | 50.4 MB | **烘焙基态**：`base_{sst,d26,tchp,wind,shear,other}.npz` + `modes_index/modes_pat/regimes/warming/tc_*` |
| `simulator/env/generated/` | 17 | 39.7 MB | Analog 生成的架空年场（当前只有 1993 年一套） |
| `simulator/env/raw/` | 20 | 1.8 MB | ERA5 真实 SST 月文件 `sst_1979_01..12` + `sst_1980_01..08`（NETCDF4/HDF5） |
| `simulator/env/params/` | 3 | 44.7 KB | `ridge/itcz/monsoon_trough.npz`（576 行结构化数组 + 回归系数） |
| `simulator/env/realdir/` | 1 | 115 KB | `20260811.npz`（real_source 唯一一天缓存） |
| `simulator/env/reports/` | 1 | 1.1 KB | `holdout_report.json` |
| `simulator/mode_data/` | 4 | 206 KB | `nino34/soi/pdo/amo.csv`（**没有 `dmi.csv`**） |
| `simulator/output/` | 3 | 40.8 KB | `modes_series.json`、`modes_summary.json`、`records.json` |

> **纠正简报的一处环境假设**：简报说 `env/library`、`env/climate`、`env/train_cache` 里"有上万个小文件"。实测 `simulator/env` 整棵树共 **1051 个文件 / 2.11 GB**（`os.walk` 统计），并非上万小文件；`simulator/` 整体 1130 文件 / 2.12 GB。

---

## 2. 数据流 / 调用链

### 2.1 三条互不相同的"环境来源"（这是本模块最要紧的结构事实）

```
                    ┌── (A) 真 WRF 精算（README 宣称优先）
                    │     simulator/env/wrfdir/<YYYYMMDD>.npz   ← 磁盘上不存在
                    │     WrfFieldAPI.get_field      env/wrf_fields.py:85-107
                    │     域内缺/全 NaN → fallback.get_field(  :90-92 )
                    │     域外 NaN 逐格回填          (  :96-106 )
                    │     available() 要求 wrfdir 内有 npz  ( :41-45 ) → 恒 False
                    │
环境来源  ──────────┼── (B) 联网数据驱动（README 宣称回落）
                    │     RealFieldAPI.get_field      env/real_source.py:428-435
                    │     _load_day: 进程缓存 → env/realdir/*.npz → _day_fields  (:415-426)
                    │     _day_fields: _source_for 路由 + _fetch_day + _derive (:382-403)
                    │     _source_for 4 层路由              ( :173-183 )
                    │       未来 >16 天 → raise;≥ now-87d → ifs_now;
                    │       ≥2017-01-01 → ifs_hist;else → era5
                    │     磁盘缓存 env/realdir/%Y%m%d.npz   ( :466-467 )
                    │
                    └── (C) 离线烘焙场库（**当前 GUI/内核实际走的路**）
                          simcore/envmodel/solver.py → daily.py: ERA5 单日单时次
                            simulator/env/train_cache/era5/day_*.nc + plev_*.nc
                            simulator/env/train_cache/cmems/g2_*.npz   (daily.py:30,36)
                          兜底：simulator/env/model/base_*.npz（月气候态基态）
                            envmodel/solver.py:29-43 映射表
                          以及 simcore/climate.py：env/library 或 env/climate 或 env/raw
```

**关键结论（实测 + 推断）**：整仓库检索 `RealFieldAPI|WrfFieldAPI|realdir|wrfdir`，除 `simulator/env/` 自身外，只有 `tests/test_real_source.py:15,23,26,84,107,124` 引用（实测 grep）。也就是说 (A)(B) 两条链**没有任何活着的调用方**，唯一入口 `simulator/run.py` 在磁盘上不存在（实测 `os.path.exists('simulator/run.py') == False`）。README:101-113 描述的 `--kernel A` 用法因此不可复现。

### 2.2 场库生产链（离线）

```
simulator/modes.py:206 archive_mode            # 抓 NOAA → mode_data/{nino34,soi,pdo,amo}.csv
        ↓
simulator/env/build_library.py:303 build_synthetic_library
        ├─ _modes_for(ym)  :290-300              # 读 nino34/pdo/dmi 序列 → 标准化
        ├─ synth_sst_month :31 / synth_ohc_month :62 / synth_mslp_month :92
        ├─ synth_wind_month:113 → shear_from_winds :255 / steer_from_winds :259
        │                        → vort850_from_winds :268 → gpi_from :279
        ├─ F10 近似场 synth_{surface_wind,precip24,t500,qv850,hgt500,hgt100,uv200}
        └─ F.save_monthly_field → env/library/{var}_{year}.npz   (field_io.py:75-87)
        ↓
simulator/env/field_io.py:121 build_climatology (1991-2020 基准)
        └─ env/climate/{var}_clim.npz  {clim, std}
        ↓
simulator/env/param_fields.py:114 calibrate   # 从 mslp 场提取脊线/槽线 → 回归
        └─ env/params/{ridge,itcz,monsoon_trough}.npz
        ↓
simulator/env/generator.py:197 AnalogGenerator.generate_monthly
        └─ env/generated/{var}_{year}.npz
        ↓
simulator/env/validate.py:47 holdout_validate
        └─ env/reports/holdout_report.json
```

### 2.3 消费链（谁读 env/）

| 资产 | 消费方（实测 grep） |
|---|---|
| `env/library/{var}_{year}.npz` | `simcore/climate.py:91`（`_var_path`）、`:157-190`（`load_months`）、`:282-298`（`load_months_env`） |
| `env/climate/{var}_clim.npz` | `simcore/climate.py:90`、`:170`（缺气候态时回退 `library/sst_2000.npz`，见 `:172`） |
| `env/climate/currents_2019.npz` | `simcore/core.py:4424-4436`（`_oscar_currents`，OSCAR 观测表层流 1° 南在上） |
| `env/climate/tchp.npz` | **未发现任何消费方**（实测 grep `tchp.npz` 无命中） |
| `env/raw/sst_{y}_{mm}.nc` | `simcore/climate.py:94-115`（`load_raw_sst_months`，真实 ERA5 SST 优先于合成库，见 `:397-406`） |
| `env/model/base_*.npz` | `simcore/envmodel/solver.py:29-43`、`simcore/envmodel/store.py:12` |
| `env/model/land_cell.npz` | `simcore/core.py:491`、`daily.py:31`、`render.py:449`、`terrain.py:23` |
| `env/model/modes_index.npz` | `simcore/envmodel/modes.py:23`、`core.py:1273,1372,5181` |
| `env/model/warming.npz` / `regimes.npz` / `modes_pat.npz` | `simcore/envmodel/solver.py:259-400` |
| `env/train_cache/era5/*.nc`、`cmems/g2_*.npz` | `simcore/daily.py:30,36`、`tools/acceptance_check.py:35-36` |
| `env/params/*.npz` | `env/field_io.py:233-245`（`FieldAPI.get_param`，按 `y*12+mo` 二分查找） |
| `mode_data/*.csv` | `simulator/modes.py:239-262`；`env/build_library.py:290-300`；`env/generator.py:53-62`；`env/param_fields.py:172-180` |
| `env/generated/` | `env/field_io.py:201-203`（`FieldAPI` 有 `generated_dir` 时优先于 `lib_dir`） |

---

## 3. 核心机制详解

### 3.1 两级数据源的回落判定（问题 1）

**（a）WRF → real_source 的回落**（`env/wrf_fields.py`）

```python
def _in_domain(dom, fld):                       # :81-83
    return dom is not None and fld is not None and np.isfinite(fld).any()

def get_field(self, var, dt0):                  # :85-107
    day = self._day(dt)                         # wrfdir/<YYYYMMDD>.npz，LRU 上限 16 天(:39,76-79)
    fld = day.get(var)
    if fld is None or not np.isfinite(fld).any():
        return self.fallback.get_field(var, dt) # ← 整场缺失即整体回落
    out = np.array(fld, dtype=np.float32)
    fb = self.fallback.get_field(var, dt)       # ← 部分 NaN 时逐格回填
    if fb is not None and fb.shape == out.shape:      # 形状守卫(:94-95)
        ...
```

判定层级共 3 档：① `wrfdir` 目录不存在或没有 npz → `available()` 直接 False（`:41-45`，实测 `simulator/env/wrfdir` 不存在）→ 调用方根本不会构造它；② 该日文件不存在 → `_day` 返回 `{}`（`:64-67`）→ 整场回落；③ 日文件在但该变量全 NaN → 整场回落；④ 只有零星 NaN → 逐格用回落场补（3D 双分量场逐面处理，`:98-102`）。`source_of()`（`:132-135`）据 `self._day(dt)` 是否非空返回 `'wrf'`，否则委派给 `fallback.source_of`。

**（b）IFS / ERA5 的源路由**（`env/real_source.py:173-183`）

```python
def _source_for(self, dt):
    now = date.today()
    if dt > now + timedelta(days=16):      raise ValueError(...)     # :175-177
    if dt >= now - timedelta(days=PAST_DAYS_LIMIT - 5): return 'ifs_now'   # 87 天 :178-179
    if dt >= IFS_BEGIN.date():              return 'ifs_hist'        # 2017-01-01 :53,180-181
    return 'era5'                                                    # :182
```

三条路由对应的端点与变量表分别在 `:206-209` 选择（`FORECAST_URL`/`HIST_FCST_URL`/`ARCHIVE_URL`，`IFS_VARS`/`IFS_HIST_VARS`/`ERA5_VARS`，见 `:71-89`）。历史 IFS 不提供 SST，故用 `temperature_2m` 代理（`:311-314`，`:66` 注释）；ERA5 只有表面变量，高空/海洋量全部用**显式标注的代理**（`:295-309`）：
- `sst ← T2m`、`rh700 ← RH2m`、`uv_steer ← 100m 风 × ERA5_STEER_SCALE(=0.85)`、`shear ← |V100m−V10m|`。
- `ohc` 无真实来源，用 SST 驱动的暖水柱代理：`clip(62 + 7.5·max(0,SST−26), 20, 170)`（`:375-377`）。
- 云端量 → OLR 代理：`olr = 260 − 90·clip(cc,0,100)/100`（`:361-364`）。

**（c）失败语义**（这是本模块最严谨的部分）
- 4xx（除 429）判定为参数错误，立即放弃、不重试（`:115-119`，注释标 BUG-7）；429 指数退避（`:120-121`）。
- 失败日期进**进程内负缓存** `_failed`（`:159,387-388,397`），但"失败不写磁盘缓存"，以免断网后永远空场（`:397`）。
- 任一底层变量缺失 → 该字段**整场不产出**（`get_field` 返回 None），绝不写 0 值场（`:326-328,354-359`）。
- `_fetch_day` 对 `precipitation` 单独处理：Open-Meteo 的 `precipitation` 是**逐小时量**，24h 总量取目标当日各时次求和，而非"末−首"差（`:265-279`）。
- BUG-1（`:282-285`）：请求经度已换到 −180..180，不能用它索引 0..360 的列，改由全局点序 `(base+j) % nlon` 反推。
- BUG-2（`:248-257`）：只取目标日的 0/6/12/18 时次，避免 `past_days` 响应里跨日时次被平均成滑动平均。
- BUG-F10（`:338-346`）：Open-Meteo 默认摄氏，`t500` 需 +273.15；`qv850` 由 RH850 + T850(K) 经 Magnus 转比湿（`:529-536`：`es = 611.2·exp(17.67(T−273.15)/(T−29.65))`，`qs = 0.622 es/(p−0.378 es)`）。
- 风矢量换算（`:517-526`）：`u = −s·sin(dir)`，`v = −s·cos(dir)`，`s` 由 km/h ÷3.6 转 m/s。
- 涡度（`:539-553`）：`ζ = (1/(a cosφ))∂v/∂λ − (1/a)∂u/∂φ`，以 111320 m/deg 换算并按抓取格距与 cosφ 修正。
- GPI（`:556-563`）：`(1+0.1·|ζ|·1e5)³ ·(RH/50)³ ·(SST−26)⁴ ·(1+0.1·shear)⁻¹`，`SST≤26` 处归零。
- NaN 填充（`:491-514`）：逐纬度沿经度环形最近邻；整行全 NaN 用全球均值。上采样（`:566-577`）：`scipy.ndimage.zoom` 双线性，先填充再缩放。
- LRU 修正（`:405-413`）：磁盘命中路径原本绕过上限，现统一 128 天上限并刷新最近使用。

**（d）ERA5/ORAS5 离线下载链**（`env/downloader.py`）
- 凭证判定：`~/.cdsapirc` 或 `CDSAPI_KEY`+`CDSAPI_URL`，一次缓存（`:69-91`）。
- 产物校验用**文件头魔数**（`CDF` / `\x89HDF`）而非尺寸，尺寸只兜 <256B（`:94-108`，注释 R4：旧 1MB 阈值误杀合法小文件）。
- 无凭证时**不静默降级合成**：逐月记缺失并快速失败（`:126-135`）；`build_real_library` 任一变量缺月即 `raise`（`build_library.py:465-467`）。
- ORAS5 管线**从未接入**（`:176-194`，`D7` 注释显式告警）。
- OHC 深度积分（`:197-214`）：`Σ ρ·cp·max(T−26,0)·Δz / 1e7`，`ρ=1025`、`cp=3985`，单位 kJ/cm²，层厚用中心差分（首层用 `d[1]−d[0]`）。

### 3.2 合成场库的物理构造（"降级不是随便降级"）

`build_library.py` 用确定性种子 `seed_base=20260802`（`:304`）逐月合成，每条公式都写进了注释：
- SST（`:31-59`）：纬向廓线 `clim = 29.5 − 0.42|lat−clat| + 1.5·exp(−(lat−clat)²/32)`，`clat = 8·cos(2π(mo−8)/12)`（最暖纬带季节迁移）；ENSO 东太增暖（`lon>150` 权重）、PDO 北太距平，再加黑潮/湾流暖舌与秘鲁/加利福尼亚/索马里冷水（`:49-57`），最后 `N(0,0.18)` 噪声。
- OHC（`:62-89`）：**独立于 SST**，含随机上翻冷信号 `−55·exp(...)`，使冷暖水异常可出现在高温 SST 区（"白海豚式"）；ENSO 项为东西偶极 `((lon−210)/210)`（`D5` 修正：原实现全经度同号，与真实 ENSO-OHC 响应反号）。
- 海平面气压（`:92-110`）：季节基值 + 副高脊（`30+4·season`）+ ITCZ 槽（`6+5·season`）+ 南半球副高 + 西太副高经向结构 + ENSO。
- 风场（`:113-139`）：850/500/200 hPa 三层各自剖面 + 随季节南北迁移的急流核；切变、引导风、涡度、GPI 全部由风场派生（`:255-285`），保证内部一致性。
- F10 渲染近似场（`:160-249`）：`_cyclone_mask` 用 850hPa 气旋性涡度 + 生成区纬带；`qv850` 先 `°C→K` 再算 Magnus（`:208-220`，注释 R3 指出原实现把 °C 当 K 导致 18 倍偏差）。
- fast-path（`:311-325`）：9 个 VARS 齐全时直接复用，但**额外检查气候态是否缺失并补建**（`R4-完整性`），避免"库在、气候态缺"造成 `get_anomaly` 全链 None。
- 落盘后删除历史遗留 `ssta_clim.npz`（`:385-394`，BUG-8），并重算 `ssta = sst − clim`（`:395-403`）。

### 3.3 气候模态层（问题 2）

**（a）定义表**：`modes.py:28-84` 用纯数据驱动的一行一表定义 5 个模态：`nino34`(°C, std 0.8, AR(0.90), 春季障碍 3-5 月)、`soi`(hPa, std 7.0, AR(0.70))、`pdo`(°C, std 1.0, AR(0.92))、`amo`(°C, std 0.22, AR(0.95))、`dmi`(°C, std 0.4, AR(0.60))。每行含单位、气候态、月 std、AR 系数、硬约束名、优先级、**谱参数** `spectrum=((频率1/年, 幅度),...)` 与 NOAA 数据源 URL。

**（b）实测归档**：`archive_mode:206-229` 抓一次写 `mode_data/{mode}.csv`（已存在且非 force 就直接返回）。解析器 `_parse_noaa_text:145-203` 同时支持 PSL 风格（行首年 + 12 个月值）与 CPC 风格（6 位年月 + 值），缺失标记统一 `|v|>=99` 剔除（M1），并对 `-4.0-999.9` 这类粘连 token 做正则拆分（R2-5）。抓取失败**显式告警**并降级到内置占位（M7，`:214-217`）。

**（c）实测数据盘点（这是本节最重要的一条）**

| 模态 | 归档 CSV | 记录数 | 起止 | 结论 |
|---|---|---|---|---|
| nino34 | ✅ | 943 | 1948-01 → **2026-07** | 实测序列 |
| soi | ✅ | 900 | 1951-01 → 2025-12 | 实测序列 |
| pdo | ✅ | 2071 | 1854-01 → **2026-07** | 实测序列 |
| amo | ✅ | 2005 | 1856-01 → **2023-01** | 实测序列（尾部偏短） |
| dmi | ❌ **文件不存在** | 912 | 1950-01 → 2025-12 | **内置占位序列**（实测：`_builtin_placeholder` 首值与 `load_mode_series('dmi')` 首值逐位相等） |

即 **IOD/DMI 从未落盘归档**，`modes.py:257-260` 的兜底分支被激活：用固定种子 `20260807+sum(ord(c))` 的**多正弦 + 随机游走**合成序列冒充"实测"（`:112-131`）。而 `env/train_cache/idx_dmi.txt`（1870–2026 真实 DMI，19861 B）**与 modes.py 完全无连接**（实测 grep：`idx_dmi` 无命中，`idx_*.txt` 只被 `simcore`/`tools` 侧读取）。

**（d）序列生成**：`ModeGenerator.generate:564-608` 生成窗口 `[最新实测月+1, +total_years)`，最多 8 次重抽（`MAX_RETRY=8`，`:104`），每次须过 `_hard_ok` 再过 `_score`，命中 `SCORE_MIN=0.35` 即停。
- 两段式：前 `cont_years*12` 个月走 AR(1)/(2) 外推（`_extend_continuation:357-388`），其余走谱合成 + 马尔可夫位相转移（`_synthesize:392-427`，转移概率 0.04/月 ≈ 平均 2 年一次，`:413-415`）。
- 春季障碍：3-5 月 `a1 *= 0.55`（`:374`）。
- SOI 反号联动 nino34：`pred += −0.7·rv·(std/0.8)`，残差方差改条件方差（`:376-382`）。
- IOD 季节锁相：12-4 月强制 `v *= 0.25`（`:384-385`）。
- 硬约束 `_hard_ok:431-456`：出历史 `±3σ`、ENSO 显著期 Nino3.4 与 SOI 同号、非季 IOD 连续 2 月 `|v|>0.5` → 拒绝。
- 软评分 `_score:460-531`：5 项加权（ENSO-IOD 0.25 / PDO 调制 0.20 / AMO 0.10 / 五元组分布距离 0.30 / 年际持续性 0.15，`SCORE_WEIGHTS:96-102`）。
- 复现性：`save_output:659-680` 把 `seed`/`sub_seeds`/`retry_counts`/`low_confidence` 一并落盘（`output/modes_series.json`）。

**（e）模态 → 逐月环境场的三条路径**（回答问题"怎么转成 base_*/modes_*/warming"）

1. **合成库路径**：`build_library._modes_for:290-300` 只吃 `oni = nino34/0.8`、`pdo = pdo/1.0`、`dmi = dmi/0.4`，喂进 `synth_*_month` 作为确定性回归项 → `env/library/*.npz`。
2. **Analog 路径**：`generator._mode_values:53-62` 取 5 模态并各自 `÷ MODES[path]['std']`，`_mode_vec:38-50` 拼 **3 个月滞后**（`LAG=3`）→ **15 维匹配向量**（`MATCH_MODES:34`）。用候选池协方差的马氏距离选相似月（`:90-101,114-116`），协方差加 `0.01·λmax` 岭、奇异时伪逆（`G8`）。
3. **烘焙基态路径**（`env/model/`，非本模块生成但由本模块的模态概念驱动）：`modes_index.npz` 存 **12 个模态 × 1884 个月**的逐月实测值（`val` 含 10876 个 NaN、`ok` 掩码、`avail0` 起始年、`mu`/`sd`、`y_lo=1870`/`y_hi=2026`）；`modes_pat.npz` 存每个 `(字段, 模态, 滞后)` 的**空间响应型**（如 `sst__dmi: (6,73,144)`、`lag_dmi: [−6..4]`，`sst__aao/sst__amo/sst__nao: (1,73,144)`）；`warming.npz` 存 1700–2300 的 ΔT 轨迹（`dt: (601,12)`，`max=2.5°C`）+ 3 个增暖型 `pat_sst/pat_d26/pat_shear` + 18 个锚点。

`output/modes_series.json` 实测（seed=42，`retry_counts={'0':'0.570'}`，`low_confidence=[]`）：5 模态各 36 个月（2026-01 起），首行 `nino34 −1.427 / soi +15.816 / pdo +2.561 / amo −0.153 / dmi +0.044`。`modes_summary.json` 首条为"2026 冬: 强 La Niña + PDO 暖相位"。

### 3.4 library / climate / model 三套口径的差异（问题 3）

| 维度 | `env/library/{var}_{Y}.npz` | `env/climate/{var}_clim.npz` | `env/model/base_*.npz` |
|---|---|---|---|
| 含义 | **逐年** 12 个月面 | **1991–2020 月气候态** + 月 std | 烘焙**基态**（可含中尺度结构） |
| 键名 | `months` + `meta` | `clim` + `std` | 变量名（`sst`/`d26`/`u850`…）+ `meta` |
| 网格 | `(12,121,360)` ±60°/1° | `(12,121,360)` ±60°/1° | `base_sst (12,360,720)` ±90°/0.5°；`base_other/base_wind (12,180,360)`；`base_sst_oisst (12,240,720)`；`tc_clim (12,120,360)` |
| dtype | float32 | float64 | float32 / float16 |
| 生成者 | `build_library.py:303`（实测 `meta.source == 'synthetic'`，`seed_base=20260802`） | `field_io.py:121 build_climatology`（基准 `CLIM_BASE=1991..2020`，`build_library.py:20`） | `tools/train_env_base.py`、`build_base_d26_validated.py`、`add_sst_mesoscale.py`、`train_env_modes*.py`、`train_regimes.py`、`train_tc_*.py` |
| 消费方 | `simcore/climate.py:91,157-190` | `simcore/climate.py:90,170`；`env/field_io.py:227`（`get_anomaly`） | `simcore/envmodel/store.py:12`、`solver.py:29-43` |
| 缺失语义 | `save_monthly_field` 拒绝错形状（`field_io.py:80-83`）；`load_monthly_field` 把形状不符当缺失（`:102-105`） | 样本 <2 时 `std=NaN`（`:137-138`） | `store.load` + `available()`（`solver.py:253`） |

`build_climatology` 用 `count>0` 的 `nansum/count` 求均值、`count>=2` 才给 std（`field_io.py:132-138`），**不静默填 0**。

**实测矛盾点**：`env/climate/tchp.npz` 的 `tchp`/`d26` 三层全为 0（`np.nanmax == 0`，`year=[2025]`），且没有任何消费方；真正的 TCHP/D26 走 `env/model/base_tchp.npz`（`max=289.8 kJ/cm²`）。这是一份**零值死资产**。

### 3.5 Analog 生成器（本模块最复杂的算法）

- **候选池** = 库内存在 `sst` 场的全部年月（`generator.py:78-89`），池为空直接 `RuntimeError`。
- **季节强约束**：粗筛 `d = min(|pm−mo|, 12−|pm−mo|) <= win`（`G2` 环形距离，`:103-112`），初始 `SEASON_WIN=1`。
- **主选**：马氏距离最小（`:114-116,118-133`）；有"轨迹记忆"偏好时，优先在**上月所选源月时间邻居 ±3 月**（`TRACK_MEMORY_WIN:24`）内取马氏最近（`:246-250`）。
- **降级链 5 级**（`:135-155` + `:254-261`）：±1 月 → ±2 月 → 全季 ±3 → 全局 top-1 → **气候态 + 合成扰动**（`_blend_clim:288-297`，用 `clim + N(0,1)·std`）。
- **片段复用**（`:161-193`）：≥3 个月时在历史模态序列上滑窗找最相似 3–12 月轨迹，段内逐月季节须对齐 ±2（`C2`），均距 `< 2.5` 才采用。
- **主/次月混合**（`_blend_month:272-286`）：主月权重 0.85（`MAIN_WEIGHT:25`）+ 次相似月 0.15，**遍历 GEN_VARS 全变量**；`GEN_VARS = F.VARS + F.RENDER_VARS`（`:31`），保证架空年的 F10 渲染层也有场。
- **去重**：已选源月加入排除集（`R4-02`，`:264-265`）。
- **落盘清洗**（`G6`，`:209-225,299-331`）：删除窗口外年份的旧 npz；同文件既有月份保留、不 NaN 覆盖；4D 变量按 `_4D_VARS` 分叉（`:311-321`）。

实测：`AnalogGenerator._index()` 的候选池理论规模 = 有 sst 场的年 × 12 = 48×12 = 576 个月。实测 `env/generated/` 只有 **1993 年一套 17 个变量**（mtime 2026-10-01 03:04），且每个文件 **NaN 数 = 0、无全 NaN 月**（实测逐文件统计）——说明这 12 个月的主/次月都命中了含 F10 场的年份。

**但这里有一个结构性缺口（实测）**：库内 F10 变量只覆盖 **1990/1991/1992/2020/2021**（`u10/t500/uv200/precip24/qv850/hgt100/v10`），`hgt500` 只覆盖 **1990/1991/1992**。原因在 `build_synthetic_library` 的 fast-path 条件：它对 `built` 里**全部 17 个变量**做 `all(... for v in built)` 检查（`:311-313`），而 9 个 VARS 早在 2026-08-13 就已全 48 年齐备（实测 mtime：`sst_1979/2026` = 08-13 00:13），因此后续只补齐了被显式指定年份的 F10 场。**后果**：对库内其余 43 个年份，`_blend_month` 载不到 `u10/t500/...` 就 `continue`（`:279-280`），对应月面留 NaN 落盘——即"大部分历史/架空年的 F10 渲染场实际缺失"。

### 3.6 副高 / ITCZ / 季风槽参数化

`param_fields.py` 三个提取器全部从 MSLP 场反演，且都有**全 NaN / 部分 NaN 防御**（不复用 `argmax` 直落 NaN 行）：
- `_extract_ridge:22-51`：15–45°N 带内按纬度行最大海压取脊线纬度与强度；东西延伸 = 该纬度 `>1015 hPa` 的最长连续经度跨度（`:44-50`）。
- `_extract_itcz:54-87`：20°S–20°N 纬向平均最低压 + 3 点平滑（边缘 `mode='edge'` 填充，`R2-1`），半宽 = 半高宽的一半，下限 3°。
- `_extract_trough:90-111`：0–20°N 最低压纬度 + `max(0, 1013−pmin)` 强度 + 最低压经度中心。

标定 `calibrate:114-169`：设计矩阵 `X = [1, cos(2π(mo−8)/12), ONI, PDO]`，`lstsq` 求系数，再**逐点留一法**算 `cv_rmse`（`:158-166`）。样本 <12 则系数置 None（`:152-154`）。

实测 `env/params/ridge.npz`：`years (576,)`（1979-01..2026-12 全覆盖）、`rows (576,)` 结构化 `[year,month,lat,strength,lon_range]`，首行 `(1979,1,lat=28.0,strength=1027.98,lon_range=360.0)`，`coef (4,)`，`cv_rmse = 1.965°`。
**问题**：`lon_range` 首行 = 360°，即"东西延伸"落到了 `.max()` 的退化上界（`:47-48`）——该字段在部分月份失去区分度。

### 3.7 留出法验证

`validate.holdout_validate:47-135`：按 `HOLDOUT_RATIO=0.12`（`:20`）随机剔除历史月（seed=2026），重建生成器协方差后用 `_pick_analog` 还原被剔除月，比对 6 个变量。判定阈值在 `:112-113`：`sst(0.9°C, 0.7)`、`ohc(20, 0.6)`、`shear(8, 0.5)`、`rh700(8, 0.5)`、`mslp(6, 0.7)`、`gpi(3, 0.3)`（GPI 在 `log1p` 域算 RMSE，`:95-97`）。变量完全没有有效样本时进 `_unvalidated` 并计入失败，避免"空报告 pass"（`:118-122`）。库真/假由 `sst` 文件 `meta.source` 判定（`:25-44`）。

实测 `env/reports/holdout_report.json`：
- `held_months = [[1990,2],[1990,3],[1992,1],[1992,9]]`（只剔了 4 个月，`n=4`）。
- `sst: rmse 0.940 / corr 0.9928`、`ohc: 10.26 / 0.9077`、`shear: 0.449 / 0.9341`、`rh700: 3.41 / 0.9321`、`mslp: 2.07 / 0.9384`、`gpi: 0.662 / 0.9313`。
- `_pass = false`，`_fails = ["sst"]`（SST 的 `rmse_mean 0.9395` 恰超阈值 `0.9`，差 0.04°C），`_mode = "synthetic"`。
- `independence.ohc_sst_corr_mean = 0.815`（n=20）、`smoothness.ok = true`（生成 1.330 vs 历史 1.172，`< 2.5×`）。

**注意口径**：留出法实际是**"用库内其它月当预测"**（`pred = b[picked[1]-1]`，`:90`），而不是"重建被剔月"，所以这些高相关部分来自库本身的空间自相关；且 `_mode='synthetic'` 时报告不参与真实库达标判定（`:111` 注释）。

### 3.8 云图渲染 `clouds/render.py`（问题 5 —— 需要纠正任务假设）

**任务描述假设 `clouds/render.py` 是"半拉格朗日云场"。实测不是。** 实测 grep `半拉格朗日|semi_lagrang|advect` 的命中全部在 `simulator/simcore/core.py`（`:4173` 标题"云场(半拉格朗日平流 + 物理源)"、`:4282 def _advect_env`、`:4284` docstring"0.5° env 网格上的半拉格朗日上游平流(经度方向环绕)"、`:4272` "kt → km/h(×1.852) 半拉格朗日位移"），`clouds/render.py` 内无任何平流/时间推进。半拉格朗日属 sim-core 分区（`01_simcore.md`），已在交接摘要中告知 Lead。

`clouds/render.py` 的真实职责是**给定时刻的静态亮温场合成 + 色阶映射**：
- 色阶两条路：内置 `_IR_BD` 10 段分段线性表（`:20-31`，`irbd_color:34-39`）；或外部 256 级 LUT `colormap_data.json`（`:45-49` 优先 `G:\气象\工具\Typhoon8(绘图脚本)\colormap_data.json`，回退 `assets/`）。`rgb_from_tb:244-257` 中外部 LUT 的索引映射为 `idx = clip((50−TB)/150·255, 0, 255)`——**索引 0 = +50°C、255 = −100°C**。
- 分辨率：默认 `res=2°`（`nl = 120//res+1, no = 360//res`，`:129-132`）；支持 `window=(clat,clon,hw_la,hw_lo)` 局部细网格（`:121-128`），注释明说"1° 全局网格放不下 0.4° 的眼"。
- 背景场 `_background:136-169`：海面 25°C 起，洋面低云（`rand>0.85` 时 −3°C）；ITCZ 带（`lat/strength/lon_range` 来自参数化，强度温和化 `min(1.5, act)`，`C5`）；季风槽 5 个随机云团（`:157-162`）；中纬锋面沿 `ridge.lat − 8`（`:164-168`）。
- 单台风 `_typhoon_field:173-228`：修正 Rankine 剖面给 CDO/眼半径（`cdo_r = max(2.2, (60+0.45w)/111)`、`eye_r = max(0.4, (25−0.05w)/111)`，`C6` 最小视觉尺寸）；CDO `−60 − 25·min(1, w/140)`；眼 −15°C、眼壁 −85°C；3 条对数螺旋雨带（`ang = base + 2.6·ln(1+2r)`，仅作用于眼外，`:200-211`）；置换结构按 `ew ∈ {3,6,4,7}` 分四种双眼墙/合并/重建形态（`:213-222`）；冷崩做半圈 LG / 半圈 OW（`:224-227`）。
- 合成 `_compose:232-237` 用 `np.minimum` 逐台风叠加（亮温取冷者）。
- `render_vis:263-268` 用 `albedo = clip((25−TB)/60, 0, 1)` 灰度。
- `to_surface:271-274` 需要 pygame（本机 WSL 无，Windows 解释器有）。

**实测发现**：`clouds/render.py` 的 `CloudRenderer` **没有任何生产调用方**——实测 grep `CloudRenderer` 只命中 `tests/test_sim_typhoons.py:172,186`。图纸上的对接方（"F5 render_layer 接口 cloud_ir/cloud_vis/cloud_globe"，`:7`）在当前代码里不存在。

### 3.9 `env/generated/` 与 `env/realdir/` 的现状

- `env/generated/`：只有 1993 一套（17 变量 × `(12,121,360)`，`uv200/uv_steer` 为 `(12,2,121,360)`），无 NaN。`FieldAPI(generated_dir=...)` 会优先读它（`field_io.py:201-203`），但实测无调用方传 `generated_dir`（grep `generated_dir` 仅命中 `field_io.py` 与 `generator.py` 自身）。
- `env/realdir/`：只有 `20260811.npz`（115 KB）。`RealFieldAPI.get_field` 的"当天缓存→磁盘缓存→联网"三级链（`:415-426`）只对这一天下线可用；其余日期必须联网。

---

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `env/library/{var}_{1979..2026}.npz` | npz `months`+`meta` | 470 文件 / 1.09 GB | `env/build_library.py:303 build_synthetic_library`（实测 `source='synthetic'`） | `simcore/climate.py:91,157` |
| `env/climate/{var}_clim.npz` | npz `clim`+`std` float64 | 16 文件 / 150.8 MB | `env/field_io.py:121 build_climatology`（1991-2020） | `simcore/climate.py:90`；`field_io.py:227` |
| `env/climate/currents_2019.npz` | npz `u`/`v` (120,360) | 1 文件 / 235 KB | 推断：OSCAR 2019 月平均（生成脚本未定位） | `simcore/core.py:4424-4436` |
| `env/climate/tchp.npz` | npz `tchp`/`d26`/`year` | 1 文件 / 4.7 KB | 推断 `tools/fetch_tchp.py` | **无消费方（全 0 死资产）** |
| `env/model/base_sst.npz` | npz `sst`+`meta` | (12,360,720) float32 | `tools/train_env_base.py` + `add_sst_mesoscale.py` | `envmodel/solver.py:29` |
| `env/model/base_{d26,tchp}.npz` | npz + `meta` | (12,360,720) | `tools/build_base_d26_validated.py`（CMEMS g2_*） | `envmodel/solver.py:30,43` |
| `env/model/base_{wind,shear,other}.npz` | npz | (12,180,360) float16 | `tools/rebuild_base_global.py` / `train_env_base.py` | `envmodel/solver.py:696-707` |
| `env/model/{modes_index,modes_pat,warming,regimes}.npz` | npz 多键 | 0.2–10.9 MB | `tools/train_env_modes*.py`、`train_env_warming.py`、`train_regimes.py` | `envmodel/{modes,solver}.py` |
| `env/model/tc_{clim,genesis,genesis_modes,intensity,intensity_fit,mjo}.npz` | npz | 0.2 KB–5 MB | `tools/train_tc_*.py` | `simcore` 生成/强度模块 |
| `env/model/{land_cell,terrain_dem}.npz` | npz | 51 KB / 606 KB | `tools/make_cell_land_mask.py`、`build_terrain_dem.py` | `core.py:491`、`daily.py:31`、`render.py:449` |
| `env/model/tc_names*.json` | JSON | 3–112 KB | `tools/build_name_times*.py` | `simcore/naming.py:30` |
| `env/raw/sst_{1979..1980}.nc` | NETCDF4 | 20 文件 / 1.8 MB | `env/downloader.py:111 fetch_era5_month`（CDS） | `simcore/climate.py:94-115` |
| `env/train_cache/era5/day_*.nc`、`plev_*.nc` | NETCDF4 | 45 文件 / 196 MB | `tools/fetch_era5_day.py`、`fetch_era5_daily.py` | `simcore/daily.py:30,81-120` |
| `env/train_cache/cmems/g2_*.npz` | npz `ohc/d26/sst/lat/lon/meta` | 29 文件 / 28 MB | `tools/build_ohc_from_cmems.py` | `simcore/daily.py:36,253-294` |
| `env/train_cache/ibtracs/bdeck/`、`last3years.csv` | b-deck 文本 / CSV | 2+29 项 / 10.5 MB | `tools/build_obs_bdeck_ibtracs.py`、`build_name_times_multi.py` | **无 .py 消费方（单向产物）** |
| `env/train_cache/idx_*.txt` | 文本矩阵 | 11 文件 / ~104 KB | 推断：tools 侧下载 | `simcore`/`tools` 侧（**不被 `modes.py` 读取**） |
| `env/train_cache/{ncep_*,oisst_ltm.nc,aoml_d26_*,z500_daily.npz,shear_daily/}` | npz/nc | ~0.4 GB | `tools/fetch_ncep_*.py`、`fetch_oisst_ltm.py`、`validate_ohc_aoml.py` | `tools/train_env_*.py`（烘焙期，非运行时） |
| `env/generated/{var}_1993.npz` | npz | 17 文件 / 39.7 MB | `env/generator.py:299 _save` | `env/field_io.py:201`（需显式传 `generated_dir`） |
| `env/params/{ridge,itcz,monsoon_trough}.npz` | npz 结构化数组 | 3 文件 / 44.7 KB | `env/param_fields.py:183 _save_param` | `env/field_io.py:233-245`、`clouds/render.py:143-168` |
| `env/realdir/20260811.npz` | npz | 115 KB | `env/real_source.py:469-476` | `env/real_source.py:478-486` |
| `env/reports/holdout_report.json` | JSON | 1.1 KB | `env/validate.py:188-194` | 人工/验收 |
| `env/missing_months.json` | JSON 列表 | 10.2 KB / 598 项 | `env/downloader.py:60-66 record_missing` | 人工/`build_real_library` |
| `mode_data/{nino34,soi,pdo,amo}.csv` | CSV | 4 文件 / 206 KB | `simulator/modes.py:206 archive_mode` | `versions` 全链（`modes.py:239`、`build_library.py:290`、`generator.py:53`、`param_fields.py:172`） |
| `output/{modes_series,modes_summary,records}.json` | JSON | 3 文件 / 40.8 KB | `modes.py:659 save_output`；`records.json` 推断由 `simulator/typhoons/` 侧写 | 人工/验收 |

---

## 5. 如何运行 / 如何验证

**可跑（本环境实测通过）**

```bash
cd /mnt/g/气象/Typhoon-sim
# 1) 留出法验证（重写 env/reports/holdout_report.json —— 只读探索请勿执行）
/usr/bin/python3 -m simulator.env.validate
# 2) 参数标定
/usr/bin/python3 -m simulator.env.param_fields
# 3) 合成库重建（写 ~1 GB，且会调用 param_fields.calibrate —— 勿执行）
/usr/bin/python3 -m simulator.env.build_library
# 4) 模态序列生成（写 output/*.json）
/usr/bin/python3 -m simulator.modes --seed 42 --years 5
```

**实测：用 Lead 指定的 Windows 解释器跑相关测试**

```bash
cd /mnt/g/气象/Typhoon-sim && timeout 180 \
  /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe \
  -m pytest tests/test_sim_env.py -q --no-header
```
实测结果：`tests/test_sim_env.py tests/test_sim_climate.py tests/test_daily_instant.py` 三个文件合并运行
**`40 passed, 56 warnings in 327.24s`，0 failed / 0 error（`grep -c FAILED\|ERROR` = 0）**。
这为"库/climate/daily 三条读取链在本机确实可用"提供了独立佐证（不依赖我的静态阅读）。注意耗时 5 分 27 秒——单文件 `timeout 180` 会超时，跑这三个文件需要 ≥400 s。

**跑不了的**
- `python simulator/run.py ...`（README:101）：文件不存在（实测）。
- `build_library(use_real=True)`：需 CDS 凭证 + ORAS5 访问；且 `fetch_oras5_month` 未接入（`downloader.py:176-194`），必 raise。
- `RealFieldAPI.get_field(20260811 以外的日期)`：需联网 Open-Meteo。
- `clouds.render.to_surface`：WSL 无 pygame（Windows 解释器有）。
- `simulator/env/wrfdir/` 路径全链：目录不存在。

---

## 6. 观察到的问题 / 风险 / 不一致

**BLOCKER-1｜README 宣称的"内核 A 真 WRF 精算"整条链在磁盘上不存在，且无调用方。**
现象：README:108-109 说优先 `simulator/wrf/run_wrf.ps1` → `simulator/env/wrfdir/`；README:113 给的 `python simulator/run.py --headless --kernel A` 无法运行。
证据：`os.path.exists('simulator/run.py') == False`、`'simulator/wrf' == False`、`'simulator/env/wrfdir' == False`（实测）；`WrfFieldAPI`/`RealFieldAPI` 在 `simulator/`、`app/`、`tools/` 内**零引用**，只有 `tests/test_real_source.py` 直接构造。
影响：环境层有 2 套"真实大气"实现（联网 `real_source.py` + 离线 `daily.py`），但文档指向的那一套是死代码；任何照 README 复现"真实大气模式"的尝试都会失败。

**MAJOR-1｜IOD/DMI 实测归档缺失，模态层用合成序列冒充实测，且证据文件被闲置。**
现象：`modes.py:28-84` 声明了 5 个模态，`mode_data/` 只有 4 个 CSV。
证据：实测 `mode_data/dmi.csv` 不存在；`load_mode_series('dmi')` 返回 912 条 1950-01..2025-12 的记录，其首值 0.2796756727436729 与 `_builtin_placeholder(...)` 首值**逐位相同**（判定为占位分支，`modes.py:257-260`）。同时 `env/train_cache/idx_dmi.txt`（真实 DMI 1870–2026）实测无人读取。
影响：`_mode_vec`（generator.py:53-62）的 15 维匹配向量中 3 维（IOD 及其两个滞后）来自随机占位；IOD 对副高/ITCZ 强度与 IOD 季节锁相的全部调节都建立在假序列上。而 `modes_series.json` 输出的 dmi 段会显得"很真实"。

**MAJOR-2｜F10 渲染变量在库内只覆盖 5 个年份，其余年份必然缺场。**
现象：`GEN_VARS = F.VARS + F.RENDER_VARS`（generator.py:31）要求 `u10/v10/precip24/t500/qv850/hgt100/uv200`，但库内这些变量实测只存在于 1990/1991/1992/2020/2021，`hgt500` 只在 1990-1992。
证据：`env/library` 逐变量年份扫描（实测）；`build_synthetic_library:311-325` 的 fast-path 只要 VARS 齐全就整体跳过重建；`_blend_month:277-280` 载不到变量时 `continue`，对应月面留 NaN。
影响：模拟 1993/2020/2021 之外的年份时，D01/D02 等 F10 渲染层拿不到 `u10/v10/precip24/…`，只能走渲染侧回退。

**MAJOR-3｜`output/modes_series.json` 与 `mode_data/*.csv` 不同代，已不可复现。**
现象：`output/modes_series.json`/`modes_summary.json` 的 mtime 为 2026-08-11 **02:48**，而 `mode_data/*.csv` 为同日 **11:38**；JSON 里序列从 2026-01 起（`continuation`），但当前 CSV 的 nino34/pdo 已到 **2026-07**，`generate()` 的窗口起点会是 2026-08（`modes.py:571-574`）。
影响：把 `output/modes_series.json` 当成"当前模态设定"使用会引入约 7 个月的陈旧相位；报告/验收若引用它需标明生成时间。

**MAJOR-4｜留出法验证实际只剔了 4 个月，且 `_pass=false`，不能作为"环境场可信"的证据。**
现象：`holdout_report.json` 的 `held_months` 只有 4 项、每变量 `n=4`；`_pass=false`、`_fails=["sst"]`（0.9395 vs 阈值 0.9）。
证据：`validate.py:56-58`（`n_hold = max(1, int(len(all_months)*0.12))`，而 `all_months` 只统计 `load_monthly_field('sst', y)` 存在的年月——若库视图不全则样本骤减）；`pred = b[picked[1]-1]`（`:90`）是"用库内别的月代替"，不是真正重建。
影响：这份报告容易被误读为"SST RMSE≈0.94°C 达标"。另外 `_mode='synthetic'` 说明它评的是合成库，与真实 ERA5 精度无关。

**MINOR-1｜`real_source.py` 顶部 docstring 的抓取网格与代码不符。**
证据：`:27` 写"2° 网格（61×180 点，500 点/批）"，`:55-56` 实为 `FETCH_GRID_STEP = 3.0`、`BATCH = 250` → 实际 41×121 = 4961 点 / 20 批；`:62` 写 ERA5 高空→引导风比例"~0.75"，`:63` 实为 `0.85`。`:28` 写"≈ 22 个并发请求"，与实测 20 批也不符。
影响：仅文档失真；但 `_fetch_day` 的 `max_workers=min(16, len(blocks))`（`:224`）说明并发上限 16，"22 并发"本就不可能。

**MINOR-2｜`env/climate/tchp.npz` 是零值死资产。**
证据：实测 `tchp`/`d26` 两个 `(12,120,360)` 数组 `min=max=0`，`year=[2025]`；全仓库 grep `tchp.npz` 无消费方。真正的 TCHP 在 `env/model/base_tchp.npz`（max 289.8）。
影响：容易让后来者以为气候态 TCHP 可用；实际取到的是全 0。

**MINOR-3｜`env/params` 的 `lon_range` 在部分月份退化为 360°。**
证据：实测 `rows[0] = (1979, 1, lat=28.0, strength=1027.98, lon_range=360.0)`；`_extract_ridge:44-50` 用 `high = m[lat_idx] > 1015` 的最长连续跨度的 `.max()`。
影响：`clouds/render.py:145-152` 把 `lon_range` 当 ITCZ 半宽用（`hw`），副高的 `lon_range` 若为 360 会失去"东西延伸"的区分度。

**MINOR-4｜`missing_months.json` 记录的失败是"快速失败"的产物，不代表完整缺失清单。**
证据：598 项 = `ohc_YYYY_MM` × 576（ORAS5 从未接入）+ 21 个 ERA5 变量月 + 1 个 `ERA5_FAILED_u850: HTTPError`；实测 `env/raw` 只有 `sst` 1979-01..1980-08 共 20 个文件。`downloader.py:133-135` 在同类失败 ≥3 次后直接记缺失不再尝试。
影响：清单末尾停在 `v850_1979_03` 这类早期月份，看起来像"只缺 1980 年前"，实际是 1980-08 之后**根本没跑**（**推断**：`_FAILED_COUNT['era5']>=3` 触发快速失败链）。

**MINOR-5｜`env/train_cache/ibtracs/bdeck/` 是只写不读的单向产物。**
证据：实测 grep `ibtracs` 仅命中 `tools/build_obs_bdeck_ibtracs.py`（写）、`tools/build_name_times_multi.py`（只读 `last3years.csv`）；无任何模块读取 `bdeck/` 下的 `.dat`。
影响：它服务的是"人工/外部工具对照观测"，不是训练集也不是验证集——任务描述里"训练集/验证集"的猜测不成立（**实测否证**）。

**MINOR-6｜`env/raw` 的 20 个真实 SST 文件决定了 `climate.py` 的"真实 SST 优先"只在 1979/1980 生效。**
证据：`climate.py:94-115` 要求该年 12 个月文件齐全才返回；实测只有 1979 全年 + 1980 前 8 个月 → **只有 1979 年**能命中该分支；1980 年因缺 9-12 月整年不采用（`:105-106,111`）。

---

## 7. unresolved（未能确证）

1. **`env/climate/currents_2019.npz` 的生成者**：只有消费方 `simcore/core.py:4424-4436`（docstring 说 OSCAR 观测表层流）。已扫 `tools/` 未见写它的脚本（`tools/check_currents.py` 只是检查）。需要读 `tools/check_currents.py` 全文或找已有产物日志才能定论。
2. **`env/model/{base_*}` 每个文件的确切生成命令与顺序**：`_bak_20260920`/`_bak_20260921` 两个备份目录证明 `base_d26/base_tchp/base_sst` 被重烘焙过至少两轮，但 `tools/train_env_base.py`、`rebuild_base_global.py`、`build_base_d26_validated.py`、`add_sst_mesoscale.py` 四条链的先后关系需逐个精读（本报告只据 docstring 与 mtime 排序）。
3. **2000 年 `library` 的特殊 mtime 与 `ssta_*` 的一致性**：`sst_1979/2026` mtime 为 08-13 00:13、`sst_1990..1992` 为 08-14 01:01/11:59、`generated` 为 10-01 03:04。分三批重烘焙，但是否存在"批间口径漂移"（例如 `ssta` 用不同气候态基准算过）未验证——需逐文件读 `meta` 并比对 `ssta == sst − clim`。
4. **`generated/` 为什么停在 1993**：只有一套场。需要跑模拟才知道是某次一次性演练的残留，还是被当作"固定演示年"。`FieldAPI` 无调用方传 `generated_dir` 也需确认是否在 `app/` 侧经反射构造。
5. **`tools/fetch_era5_day.py` 的实际抓取范围**：`era5/` 里日期分布极不均（1997-09 连续 8 天 + 2024-01-15 + 2024-09-15 + 2026-02-27/28 + `sstm_*` 月场）。哪些模拟场景依赖这些具体日期，需要看 `simcore/envmodel/solver.py` 的时钟与 `app` 的默认时间设定。
6. **`env/params/*.npz` 的 `lon_range=360` 是否为 bug**：从 `_extract_ridge` 逻辑看是 `.max()` 命中环绕整圈；需对照"副高东西延伸"的预期量纲（度 vs 半宽），未在代码中找到消费者解释（`clouds/render.py` 只把 ITCZ 的 `lon_range` 当半宽）。
7. **`records.json` 的生成者**：`simulator/output/records.json` 有 `records`/`rejections` 两键（首次生成台风记录，含 `mode:"D"` 与 `reason:"环境允许性通过"`），但生成代码在 `simulator/typhoons/` 或 `simcore` 侧，未在本范围内确认。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 网格/形状/单位契约 | `simulator/env/field_io.py:18-46`（`NLAT/NLON/VARS/RENDER_VARS/FIELD_DEFS`）+ `:51-62`（4D 自检） |
| npz 读写与坏文件判定 | `field_io.py:75-107` |
| 月气候态与 std 的算法 | `field_io.py:121-141` |
| 月→日插值（含 12 月跨年） | `field_io.py:154-177` |
| `get_field/get_anomaly/get_param` 门面 | `field_io.py:182-245` |
| 合成 SST / OHC / MSLP / 风 / RH 公式 | `build_library.py:31-154` |
| F10 近似场公式 | `build_library.py:157-249` |
| 建库主流程与 fast-path | `build_library.py:303-404` |
| 真实库（ERA5/ORAS5）与缺失即报错 | `build_library.py:407-477` |
| ERA5 NetCDF 解析/纬度翻转/单位换算 | `build_library.py:507-542` |
| CDS 凭证/断点续传/文件头校验 | `downloader.py:69-135` |
| ORAS5 为何不可用 | `downloader.py:176-194` |
| OHC 深度积分公式 | `downloader.py:197-214` |
| IFS/ERA5 源路由（4 层） | `real_source.py:173-183` |
| 抓取网格/批量/并发 | `real_source.py:55-56,186-233` |
| 降水 24h、时次选取、经度回卷等 BUG 修正 | `real_source.py:248-288` |
| ERA5/IFS 派生与物理代理 | `real_source.py:292-378` |
| 三级缓存与失败负缓存 | `real_source.py:382-486` |
| 风矢/比湿/涡度/GPI/上采样工具函数 | `real_source.py:491-577` |
| WRF→real_source 回落判定 | `wrf_fields.py:41-45,58-107,132-135` |
| Analog 生成器全部降级规则 | `generator.py:24-35,103-155,161-193,197-331` |
| 15 维模态匹配向量 | `generator.py:34,38-62` |
| 副高/ITCZ/季风槽提取与标定 | `param_fields.py:22-111,114-169` |
| 留出法验证与阈值 | `validate.py:20-22,47-135` |
| 模态定义表（含 URL/谱/硬约束名） | `modes.py:28-84` |
| NOAA 文本解析（PSL/CPC 双风格） | `modes.py:145-203` |
| 模态序列生成（AR + 谱 + 硬/软约束） | `modes.py:318-608` |
| 模态校验（KS/自相关/周期） | `modes.py:685-741` |
| IR-BD 色阶与外部 LUT | `clouds/render.py:20-95` |
| 云图背景与台风结构渲染 | `clouds/render.py:136-228` |
| 半拉格朗日平流（**不在 clouds/**） | `simulator/simcore/core.py:4173-4292`（`_advect_env`） |
| 运行时瞬时场（ERA5/CMEMS） | `simulator/simcore/daily.py:1-120,253-294` |
| 库/climate/raw 三条读取口径 | `simulator/simcore/climate.py:88-190,390-419` |
| 基态 npz 映射表 | `simulator/simcore/envmodel/solver.py:29-43` |
| 数据资产重新烘焙的脚本入口 | `tools/train_env_*.py`、`tools/train_regimes.py`、`tools/train_tc_*.py`、`tools/fetch_*.py`、`tools/build_*.py` |
