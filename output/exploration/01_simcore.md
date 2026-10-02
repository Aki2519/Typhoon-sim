# 01 模拟内核 simulator/simcore

## 一句话定位

约 12.8k 行纯 Python/numpy 的台风数值内核：用 0.5° 全球环境场（烘焙 npz + 模态/环流型/天气尺度扰动）驱动逐 6h 状态机（生成→强度→移动→海洋→相态→命名），供 pygame GUI 与离线标定工具共用。

---

## 1. 清单与职责

`wc -l simulator/simcore/**/*.py`（本机实测，2026-10-01）：

| 文件 | 行数 | 职责 | 关键类 / 函数 |
|---|---|---|---|
| `core.py` | 5205 | 全部物理与状态机；唯一有副作用的内核 | `init` `env_update` `intensity_step` `motion_step` `ocean_step` `gen_check` `sim_step` `_sim_step_core` |
| `phase.py` | 708 | 纯函数相态分类（ATCF 阶段码阶梯） | `classify` `phase_spans` `CAP_KT` `WEAKEN_LADDER` |
| `render.py` | 1648 | pygame 渲染层（唯一依赖 pygame 的文件，按需显式 import） | `GridMap` `render_env_layer` `render_sat` `draw_streamlines` `Particles` |
| `rewind.py` | 959 | 时间轴快照/回溯；不 import core | `RewindStore` `RewindPolicy` `snapshot_state` `SkipKeys` |
| `terrain.py` | 684 | 真 DEM 山脉高度 + 摩擦/登陆衰减无量纲因子 | `elev_at` `friction_scale` `decay_scale` `available` |
| `climate.py` | 523 | 旧"气候库"数据驱动环境场（`dataMode` 的另一条路） | `build_env_fields` `load_months_env` |
| `synoptic.py` | 468 | 西风槽 + 冷空气准周期爆发（默认全关 = no-op） | `apply_to_env` `cold_index` `westerly_trough` |
| `daily.py` | 433 | ERA5/CMEMS 单日"瞬时场"读取（`_instant_fields` 的后端） | `load_fields` `coverage` `available_days` |
| `naming.py` | 419 | 真实命名表 + 命名时刻表 + 洋区/次洋区判定 | `Namer` `name_threshold` `sub_basin` |
| `envmodel/solver.py` | 773 | 基态(儒略日插值)+增暖+模态+环流型 → 0.5° 场 | `solve` `_solve_uncached` `_UPSAMPLE` |
| `envmodel/modes.py` | 207 | 模态指数序列（实测 / 按月覆盖 / AR 外推） | `ModeSeries` `_fit_ar` |
| `core.py` 内嵌 | — | 生成/命名/相态接线的辅助 | `maybe_name` `classify_genesis` |
| `import_tc.py` | 186 | b-deck 解析 + A 模式（路径锁定） | `parse_bdeck` `make_from_track` `track_at` |
| `fill.py` | 173 | 网格补洞/去尖峰（陆地点用邻域统计填充） | `smooth_fill` `ocean_fill` `outlier_mask` |
| `envmodel/store.py` | 81 | npz 载入 + 32 份 LRU 缓存 + 代次计数 | `load` `available` `generation` |
| `__init__.py` | 7 | 只 import `core`（惰性，避免拖入 pygame） | — |
| 数据资产 | — | `land_0.5.npz` / `terrain_0.5*.npz` / `ne_110m_land.geojson` | 见 §4 |

`simulator/simcore/__init__.py:4-7` 明确写"默认只导入 core(numpy, 无 pygame 依赖)"，`render` 由调用方显式 import —— 所以 `import simulator.simcore.core` 在本机 WSL 3.14.4（无 pygame）下成功，实测通过。

---

## 2. 数据流 / 调用链

### 2.1 生产入口（README 说的入口不存在）

| 声称 | 现实 |
|---|---|
| `README.md:101-113`：`python simulator/run.py --seed 1 --years 3 [--headless]` | `simulator/run.py` **不存在**（`ls` 报 No such file） |
| `README.md:109`：`simulator/wrf/run_wrf.ps1` 一键 WRF 管线 | `simulator/wrf/` **不存在** |

磁盘上唯一的真实入口是 `typhoon_sim.bat`（内容 `python main.py`）→ `main.py`（pygame GUI，`main.py:1-30` 初始化 DPI/pygame）→ `app/ty_sim.py` → mixin 体系。

### 2.2 调用链

```
typhoon_sim.bat  →  main.py:__main__  →  app/ty_sim.py
   └─ TySimModeMixin (app/ty_sim_mixins/sim_mode_mixin.py)
        ├─ 建模拟:  V.init({... 'params': {'dataMode': self.sim_data_mode, ...}})   sim_mode_mixin.py:841
        │            （self.sim_data_mode 默认 True, app/config.py:141）
        ├─ 每帧:    V.sim_step(sim, step_h, max_inner=1)                            sim_mode_mixin.py:1460
        └─ 单步按钮: V._sim_step_core(sim, dt, hold, force_env=False)               sim_step_control.py:162-166
                                   │
        ┌──────────────────────────┘
        │  core.sim_step(sim, dt, max_inner)                                   core.py:4994
        │    ├── precision_spec(params['precision']) → (target_ms, inner, hold, quality)
        │    │        PRECISION_TIERS                                            core.py:728-739
        │    ├── 分帧账本 sim['_pendStep']                                       core.py:5015-5024
        │    └── inner>1: 每内步 force_env=True 调 _sim_step_core(dt/inner)      core.py:5027-5035
        │
        └─ _sim_step_core(sim, dt, hold, force_env)                          core.py:5050
             ├── sim['t'] += dt ; sim['month'] = clock(sim).month            core.py:5052-5055
             ├── env_update(sim)                                             core.py:5065 → 1546
             │     └── (dataMode) climate.build_env_fields / envmodel.solve
             │            └── envmodel.store.load('base_*'/'modes_*'/'regimes'/'warming')
             ├── ocean_step(sim, stp/n_oc) × ceil(step/1h)                   core.py:5091-5094 → 4533
             ├── run_ensemble(sim)      （默认开）                            core.py:5101-5104 → 4857
             │     ├── forecast_env_series(sim,72,6)   ← 13 次 env_update     core.py:4675
             │     └── forecast_members(sim, prim, 72, 12, 6)                core.py:4751
             ├── gen_check(sim)                                              core.py:5105-5107 → 4028
             ├── for tc in sim['tcs']:  intensity_step → motion_step           core.py:5108-5113
             │     ├── intensity_step: sample_env → mpi → RMW/ERC/RI/EMBC →
             │     │                   shear/anvil/land/sink → phase.classify → 消亡判据
             │     │                   core.py:2566-3336
             │     └── motion_step:  _free_motion(引导+β+噪声+藤原) → 一阶滞后
             │                      → 位移 → seg_land_cross → 尾流沉积 → ACE
             │                      core.py:3526-3661
             └── eddy_step(sim, dt)                                          core.py:5138 → 3666
```

渲染侧（`render.py`，pygame）由 `sim_mode_mixin` 注入的函数绘制，读 `sim['env']` 的数组与 `sim['tcs']` 的路径；`render.py:1141` / `1196` 调 `core.wind_at_arr` 批量取风。渲染不写内核状态。

### 2.3 `cloud_step` 在**生产链路上不存在**

`cloud_step`（`core.py:4210`）的调用点只有测试：

```
$ grep -rn "cloud_step" --include=*.py .
app/ty_sim_mixins/sim_mode_mixin.py:1630:  # 云场已停算(模拟模式不要云图): 原 cloud_step 单次 34ms, ...
simulator/simcore/core.py:4210: def cloud_step(...)
tests/test_sim_mode_ref.py:298,301,308,339
tests/test_simcore_ocean.py:101-106
```

`_sim_step_core`（`core.py:5050-5150`）里没有它。冷尾流衰减被刻意从 `cloud_step` 挪到 `ocean_step`（`core.py:4278-4279` 与 `tests/test_simcore_ocean.py:4` 的 P0 回归说明）。**结论：云场整条支路（`cloud_step` + `sim['cld']` + `cldNoise` + `cld_sample` + `render_sat`）在生产中停算，仅由测试覆盖。**

---

## 3. 核心机制详解

### 3.1 网格、常数、单位

`core.py:47-61`：

```python
C = {'LON0':0.0,'LON1':360.0,'LAT0':-90.0,'LAT1':90.0,   # 全球 ±90（用户 2026-09-21 "扩域"）
     'ENVD':0.5,'CLDD':1.0,                              # 环境 0.5° / 云 1.0°
     'DT':10.0/60.0,                                     # 积分步长 10min（已废弃，见 3.2）
     'R_E':6371.0,'OMEGA':7.292e-5,'RHO':1.15,
     'CP':1005.0,'LV':2.5e6,'DEG':pi/180}
```

网格由此派生（`core.py:62-65`）：`ENVNY×ENVNX = 360×720`，`CLDNY×CLDNX = 180×360`。单位约定见 `core.py:17`"风速 m/s, 气压 hPa, 距离 km, 时间 h"，但内核实际**风速一律 kt**（`KT=1.944`，`core.py:71`）。`DOM_LAT0/LON0` 等模块级别名（`core.py:68-69`）是为绕开局部变量遮蔽而加的。

积分步长：`step_hours(sim)` = `snap_env_step(sim['envStepH'])`，档位 `ENV_STEPS=(1.0,3.0,6.0)`（`core.py:707`），默认 6h（`DEFAULT_ENV_STEP=6.0`，`core.py:751`）。`core.py:3369-3373` 解释了为什么把积分步长与环境步长合成一个旋钮："环境冻结期内强度 ODE 是自治的, 10 分钟积 36 步与 6 小时积 1 步数学等价"。`C['DT']` 因此成了遗留常量。

精度档位（`core.py:728-739`）：

| 名称 | 目标 ms | inner | hold | quality |
|---|---|---|---|---|
| 50ms | 50 | 1 | 16 | 0 |
| 100ms | 100 | 1 | 3 | 0 |
| **400ms（默认）** | 400 | 1 | 1 | 1 |
| 800ms | 800 | 4 | 1 | 1 |
| 2s | 2000 | 12 | 1 | 1 |
| 5s | 5000 | 31 | 1 | 1 |

`inner` = 把 6h 再细分几步（每步重建环境场）；`hold` = 反过来延长环境重建间隔，中间步只按尾流重算 SST/OHC（`core.py:5063-5080`）；`quality=0` 跳过中尺度噪声/涡旋（`core.py:1787`、`1853`）。

### 3.2 随机数：mulberry32，**不占 numpy 流**

`make_rng`（`core.py:166-177`）实现 mulberry32，`make_gauss`（`core.py:180-198`）用 Box-Muller + spare 缓存。`sim['rng']` 与 `sim['gauss']` 从同一 seed 派生（`core.py:844-845`）。这是**可复现性**的地基：`_stable_seed`（`core.py:931-940`）明确不用内置 `hash()`（PYTHONHASHSEED 加盐会让同一天气场每次启动不同）。

注意 `gen_check` 里有一段刻意设计（`core.py:4149-4152`）："**必须短路**: 只有真的要抽'僵胎'时才消耗随机数 —— 否则默认配置下每次生成都多抽一个 rng, 随机流改变 ⇒ 位同校验会失败"。同一个纪律贯穿全仓（`tools/free_hash.py` 就是为此写的）。

### 3.3 强度方程 `intensity_step`（`core.py:2566-3336`，775 行，全文最关键）

#### (a) MPI 上限：`mpi()`（`core.py:358-390`）

```python
d = clamp(sst - 26.0, 0, 5.0)
v = 70.0 + 26.73*d - 0.729*d*d                       # 二次拟合（Emanuel 气候学）
if sst < 26.5: v *= max(0.15, (sst-24.5)/2.0)        # 冷海门
ohc_term = 0.875 + 0.25/(1+exp(-(max(0,ohc)-55)/28)) # 连续饱和（替换掉会触顶的 clamp(ohc/75)）
v *= ohc_term
v *= 0.92 + 0.08*clamp(outflow,0,1)
if t200 is not None:                                  # V² ∝ (Ts-To)/To 调制
    frac = max(0.05,(Ts-To)/max(1,To)); frac_ref = (300.0-T200_REF)/T200_REF
    v *= sqrt(clamp(frac/frac_ref, 0.2, 2.5))
v *= 0.72 + 0.28*min(1.0, abs(sin(lat))/sin(18°))     # 纬度项取 |sin|（南半球） 
return clamp(v, 0, 195)
```

其中 `T200_REF = 222.0`（`core.py:1332`，理由见 `core.py:382-384`：不能用理想化的 210K，否则全球 MPI 系统性低 8%）。

**实测分解**（探针 `/tmp/sc/p3.py`）：

| sst | ohc | outflow | lat | t200 | 松弛基 | OHC后 | 出流后 | T200后 | 纬度后 = `C.mpi()` |
|---|---|---|---|---|---|---|---|---|---|
| 29.5 | 80 | 0.5 | 15 | 无 | 154.6 | 162.7 | 156.2 | 156.2 | **156.21** |
| 29.5 | 80 | 0.5 | 15 | −51℃ | 154.6 | 162.7 | 156.2 | 158.6 | **151.43**\* |
| 30.5 | 120 | 0.9 | 15 | −54℃ | 175.5 | 193.5 | 192.0 | 201.1 | **191.98** |
| 26.0 | 60 | 0.5 | 15 | 无 | 70.0 | 53.1 | 51.0 | 51.0 | **48.64** |

\* 注：T200 分支里 `frac > frac_ref`（SST 29.5 / To 222.15K → frac=0.325，参考 0.351）时乘子 <1，所以这一格 MPI 反而**降**了 4.8kt —— 与 `core.py:365` "To 降 5K 约 +4%" 的方向一致（此处 To 比参考**高**，故降）。

`mpi()` 在 `intensity_step` 里还要再过三道闸：
1. **24h 指数平滑记忆**（`core.py:2583-2585`）：`tc['vpi_s'] += (vpi - vpi_s) * min(1, dt/24)`；理由见 `core.py:2579-2582`（瞬时 MPI 沿路径剧烈震荡 70→55→124→175）。
2. **陆地抑制**（`core.py:2587-2588`）：`vpi *= 1 - 0.85*min(1,land)`。
3. **外核惯性稳定度屏障 `eta_rmw`**（`core.py:239-254`、调用点 `2626`）：`η = clamp(1 - 0.30*max(0,RMW-22)/30, 0.65, 1.0)`，RMW≤22nm 时 η=1。

#### (b) RMW 是**预报量**，不是 vmax 的纯函数

平衡值 `rmw_eq`（`core.py:203-231`）：

```python
_base = 12.0 + 33.0*exp(-(v_kt-30.0)/35.0)
if v_kt > 120.0: _base -= 9.0*clamp((v_kt-120)/30, 0, 1)   # 极强端额外收紧
return clamp(_base, 2.5, 90.0)
```

演化（`core.py:2607-2624`）：

```python
_r_eq = rmw_eq(tc['vmax']); _t_r = 30.0
if tc['erc']:                        # ERC 期间平衡半径外扩、时间尺度收到 8h
    _x = min(1, erc['t']/erc['T'])
    _r_eq *= 1.0 + erc['rF'] * sin(pi*_x)
    _t_r = 8.0
tc['rmw'] += (_r_eq - tc['rmw']) * min(1, dt/_t_r)
tc['rmw'] += gauss() * 0.55 * sqrt(max(dt,1e-3))   # 环境湍流扰动
tc['rmw']  = clamp(tc['rmw'], 2.0, 120.0)
```

**实测 `rmw_eq`**（探针 p3）与 docstring 的 T5 标定表（`core.py:208-210`）**对不上**：

| vmax | docstring 表 | 实测 `rmw_eq` | 差 |
|---|---|---|---|
| 20 | 45 | **55.91** | +10.9 |
| 34 | 40 | 41.44 | +1.4 |
| 45 | 30 | 33.50 | +3.5 |
| 55 | 25 | 28.15 | +3.2 |
| 65 | 20 | 24.14 | +4.1 |
| 80 | 15 | 19.91 | +4.9 |
| 100 | 15 | 16.47 | +1.5 |
| 150 | 12 | 4.07 | −7.9 |

docstring 声称"拟合在以上各点的误差都 ≤2nm"，实测低端与中段偏差 1.4–4.9nm、v=20 差 10.9nm。见 §6 MINOR-1。

#### (c) 向 MPI 逼近的时间尺度 + 高段加速

```python
TAU_INTENSIFY_H = 38.0 ; TAU_WEAKEN_H = 30.0           # core.py:2540/2545
tau = TAU_INTENSIFY_H if vmax < vpi else TAU_WEAKEN_H
_tau_eff = tau
if vmax >= 100.0 and tau == TAU_INTENSIFY_H:
    _tau_eff = TAU_INTENSIFY_H * params.get('hiTauScale', 0.6)   # 高段松弛更快
dV = (vpi - vmax) / _tau_eff
```

`core.py:2532-2534` 说明 tau 的来历：观测 V=40kt/MPI≈120kt 时 24h 只 +3.2kt ⇒ tau_eff≈600h，而"早期写 38h 快了 16 倍"。**注意 600h 与现行 38h 相差 15.8 倍 —— 代码里 `TAU_INTENSIFY_H` 仍是 38**（`core.py:2540`），只有 docstring 记录反推值。这是一处未落实的标定（§6 UNRESOLVED-1）。

#### (d) RI（快速增强）状态机

触发（`core.py:2675-2687`）：

```python
if sst >= 28.4 and shear <= 16.0 and land < 0.05 and riCD <= 0 and vmax < vpi:
    if rng() < 0.05*dt:  tc['ri'] = {'t':0.0, 'life':36.0}
```

每步 `ri['t'] += dt`，超 `life` 后置空、`riCD = 12.0`（不应期，`core.py:2671-2674`）。

增长支幅度上限（`core.py:2692-2744`）：基础 `_cap6 = 6.0`；`vmax>=100` → `12.0`；`sst>=30 且 shear<=6` → `max(_, 8.0)`；RI 期间 `_cap6 = max(_cap6, _amp*(0.35+0.65*_head))`，其中 legacy 的 `_amp=20.0`、vshape 版为线性 profile，`_head = max(0, 1 - vmax/vpi)`。最终 `dV = min(dV, _cap6/6.0)`。

`core.py:2725-2730` 是自我批评最好的一处注释：legacy 式 `20*(0.35+0.65*_head)` 在 vmax=25/vpi=195 时给 **18.4 kt/6h = 73 kt/天**，`_head` 在低强度时**接近 1**、反而把幅度放大，与注释意图（限制低端）相反。

#### (e) 发展阻滞（vmax<34 且 dV>0）

```python
dV *= unfav_block(env, lat, k=params.get('unfavK', UNFAV_K))   # core.py:2750-2752
```
`unfav_index`（`core.py:3887-3904`，三项相加，纬度项已于轮次 53 移除）：
```python
max(0, 27.0-sst)/2.0 + max(0, shear-20.0)/15.0 + max(0, 55.0-rh)/20.0
```
`unfav_block`（`core.py:3910-3934`）：`exp(-k * unfav_index²)`，凸响应。

再叠两个开关：`disturbK`（默认 1.0 = 不生效，`core.py:2764-2766`）与 `stillbornK = 0.06`（`core.py:3834`，`core.py:2770-2771`）。

#### (f) 高空反气旋、有效切变、切变方向、互旋切变

```python
anvil_target = min(1.0, (vmax/155.0)*(0.55+0.45*min(1,age/240)))   # core.py:2780
anvil += (anvil_target-anvil)*min(1, dt/12)
if shear>29: anvil -= dt/30 ; elif shear>19: anvil -= dt/90          # 2782-2785
shear_eff = max(6.0, shear*(1.0-0.82*anvil))                        # 2789（残留至少 6kt）
```

切变**方向**（`core.py:2792-2797`）：`cosφ = (sh_u·mu + sh_v·mv)/(shear·|motion|)`，`shear_eff *= clamp(1+0.28cosφ, 0.72, 1.28)`。

其它台风的高空反气旋制造切变（`core.py:2803-2811`）：1500km 内每台 `3.0*(0.3+vmax/155)*(1-d/1500)`，求和后 `min(_mut, 8.0)` 封顶。

切变抑制（`shear_response`，`core.py:2518-2529`）：
```python
fs   = exp(-(max(0,s-6)/55)^2)      # 平均增长的温和抑制
gate = exp(-(s/27)^2)               # RI 机制的硬门控
```
实测（探针 p3）：S=10 → (fs 0.9947, gate 0.8718)；S=20 → (0.9373, 0.5777)；S=40 → (0.6824, 0.1114)；S=50 → (0.5273, **0.0324**)。`core.py:2812-2820` 记录了这是为复现"切变下 RI 率变 78 倍、但中位强度不变"这一观测事实。

干空气：`fd = exp(-((70-rh)/42)^1.4) if rh<70 else 1.0`（`core.py:2825`）。

#### (g) 陆地衰减与地形

```python
_ds = 1.0
if land > 0.02:
    if params['terrainOn'] and terrain.available():
        _raw = terrain.decay_scale(terrain.elev_at(lon,lat,use_max=True))   # 1+peak/1000
        _ds = min(max(1.0,cap), 1.0 + k*max(0, _raw-1.0))                  # k=0.25 cap=2.0
    dV -= vmax * min(1,land) * 0.012 * _ds
if land > 0.3: dV -= vmax * 0.002
```
（`core.py:2834-2861`）。`core.py:2836-2850` 记录了踩过的坑：曾经写成"裸倍率 `* _ds`"，台湾中央山脉格使该项从 −1.19 变 −4.80 kt/h（6h 一步 −28.8kt，而标定口径是 −7.2），被 verifier 判为 P1。

`terrain.py` 的两个因子（`terrain.py:654-684`）：
```python
friction_scale(elev) = exp(-max(0,elev)/1500)     # 引导权重折减（elev=0 恒为 1）
decay_scale(peak)    = 1 + max(0,peak)/1000       # 登陆衰减倍率（≥1）
```
契约见 `terrain.py:651-652`："海拔为 0 时严格等于 1 ⇒ 不改变现有海上行为"，默认 `terrainOn` 关（`core.py:41`）。

#### (h) 冷海 / 高龄 / 结构退化弱系统

```python
if sst < 26.5:  dV -= vmax*(26.5-sst)*0.0060                       # core.py:2869
if vmax > 108:  _d = vmax-108 ; dV -= _d*_d*1.6e-4                 # core.py:2885-2887
if age > 240:   dV -= 0.45*(age-240)/260*(0.5+vmax/155)            # core.py:2889-2890
weak: vmax<60 且 (sst<25.5 或 shear>35 或 land>0.25) → weak += dt/6
      且 dV -= vmax*0.004*weak ; 否则 weak -= dt/24               # core.py:2892-2903
```

#### (i) 减弱支：显式速率 r(V) × 有界环境汇（**这一段替换 dV，不是叠加**）

```python
if vpi < vmax:
    _r6 = -(2.0 + 3.5*clamp((v-34)/46, 0, 1))          # 34→80kt: -2 → -5.5
    if v > 80: _r6 = -5.5 - 4.5*clamp((v-80)/20, 0, 1) # 80→100kt: -5.5 → -10
    _sink = 0.0
    if land > 0.3:  _sink -= (6.0 + 7.0*min(1,land)) * _ds       # 含地形放大，见 2937-2945
    if sst < 25.5:  _sink -= min(6.0, 5.0*(25.5-sst))            # 冷海最多 -6
    if shear>=30:   _sink -= 5.0                                 # 强切变 -5
    _r6 = max(-18.0, _r6 + _sink)
    dV = min(dV, _r6/6.0)                                        # ← 唯一施加点
```
（`core.py:2919-2959`）。`core.py:2908-2918` 记录了为什么必须显式：把 `TAU_WEAKEN_H` 从 84 砍到 30（2.8 倍）峰值后减弱只从 −1.4 动到 −0.9，"因为 (vpi−V) 本身很小，衰减被'MPI 还在上面'屏蔽"。观测档位表（`core.py:2913-2914`）：34-48:−5 / 48-64:−5 / 64-80:−10 / 80-100:−10 / ≥100:−12.5 kt/6h。

`core.py:2937-2945` 是另一个"修 bug 反而架空新逻辑"的实例：地形放大原**只**乘在中段的 `dV` 上，但强台风登陆时 `vpi(陆地抑制后) < vmax` 恒成立 ⇒ 必走减弱支 ⇒ 末行的 `dV = min(dV,_r6/6)` **整段替换** dV，地形项被丢弃（实测 vmax0=65 时开关两臂差精确为 0）。

#### (j) 日变化与随机强迫

```python
lh = local_hour(sim, tc['lon'])                             # core.py:2338-2339, 以 120°E 为基准
dV *= 1 + 0.05*cos(2*pi*(lh-3)/24)                          # core.py:2962-2963（03 时峰值）
_jit = gauss() * random * 1.90 * (0.5 + vmax/155.0)         # core.py:2969-2970
dV += _jit
```
`core.py:2964-2978` 记录了系数 1.35→1.90 的依据：**剔除零步后**观测的升均值≈降均值（34-48: +6.20/−6.24；64-80: +8.27/−9.81）⇒ 步长分布无偏度，模型是纯尺度问题。

#### (k) 核心组织度 / 雨带 / EMBC / 屏障突破（"无状态机"设计）

`core.py:2992-2994` 的显式设计声明："BF(卷眼)/CF(塌眼) **不是二分而是连续谱** …… 所以不设状态机, 用一个连续量表达"。

`core_org` 初值（`core.py:2996-3002`）由生成时环境定：`clamp(0.45 + 0.30*clamp((rh-55)/25) - 0.20*clamp((shear-20)/25) + 0.10*gauss, 0.10, 0.95)`。

雨带活动 A（`core.py:3009-3021`）：`_band_env × _band_sup × (1-0.35*_dry)`，其中 `_band_sup` 对切变**非单调**（<12kt:0.75 太干净雨带疯长；12-28kt 递减；>28kt 再回升到 ≥0.35）。`core.py:3007` 引用户帖："完美的环境未必是最完美的"。

`core_org` 演化（`core.py:3024-3026`）：
```python
_heat = clamp((vmax/vpi) * (22.0/max(8.0,rmw)), 0, 2.0)
core_org = clamp(_org + (0.045*_heat - 0.030*A - 0.020*max(0,shear-25)/20)*dt, 0.05, 1.0)
```

**EMBC（嵌匿中心）**（`core.py:3033-3075`）：形成需 `vmax>55 且 land<0.1 且 org>0.55 且 shear_eff>9 且 rmw<40`，每步概率 `min(0.6, 0.10*dt*gate_sh)`；释放条件 `vmax>80 或 shear_eff<12 或 shear_eff<=形成时切变-6`；释放脉冲
```python
_head = max(0, 1 - vmax/vpi)
dV += min(_burst*8.2*_head, 12.0/6.0)          # _burst = 0.9+0.8*org (kt/h)
```
被压着时 `dV -= 0.25`。`core.py:3029-3076` 的注释链条非常完整：0.016 的触发率太小→"一生只 2-3 步满足前置"→一生触发 ~10%，而观测 45-63% 经历过 RI。

**屏障突破**（`core.py:3081-3085`）：`org<0.45 且 vmax>60 且 land<0.1 且 shear_eff<8 且 rh>68`，概率 `0.017*dt*gate_sh`；触发后 `core_org += 0.45`、`rmw *= 0.72`。

#### (l) ERC（眼墙置换）—— 只改了 ERC 触发律

触发（`core.py:3106-3117`）：
```python
_flux = clamp((sst-26)/3, 0, 1) * clamp((vmax-85)/25, 0, 1)     # 强度型通量代理
if not erc and ercCD<=0 and vmax>85 and land<0.15 and sst>26:
    _rmw = max(2.0, rmw)
    _p = 0.010 * dt * _flux * (20.0/_rmw)**1.5                  # 触发概率 ∝ RMW^-1.5
    if rng() < _p:
        _merger = rng() < min(1.0, 8.0/_rmw)                    # 小眼 ⇒ 多半融合式
        _T = max(0.75, min(48.0, 24.0*(_rmw/20.0)**1.7))        # T ∝ RMW^1.7
        erc = {'T':_T,'t':0.0,'merger':...,'rF':...,'dip':...}
        ercCD = _T + 24.0 + rng()*60.0                          # 不应期 = 本次时长 + 1~3.5 天
```

**实测**（探针 p3，以 `T = 24*(RMW/20)^1.7` 直接算）：RMW=2.5nm → T=0.75h（地板生效）；RMW=20nm → T=24.0h；RMW=45nm → 48.0h（上限）。与 `core.py:3095` 声称的"2.5nm→0.75h / 20nm→24h"逐点一致 ✅。

进行中（`core.py:3120-3145`）：
```python
x = erc['t']/erc['T'] ; _shape = sin(pi*min(1,x))
if dV > 0: dV = 0.0                                   # 事件期间先关增强通道
_loss = 5.0 if merger else 25.0
dV -= (_loss/max(1,T)) * _shape * (pi/2)              # ∫₀ᵀ = _loss  ⇒ 累计净亏 L kt
if x >= 1: erc = None; dV += 1.0
```
`core.py:3124-3134` 说明为什么"低谷不能乘在 dV 上"：原先那样做只让增长**变慢**，事件期间仍净增 +22.9/+27.0kt，而观测签名是**真的掉**（普通式 145→120）。

#### (m) 相态：结构相态（cuClass）与 ATCF 阶段码（phase）**两套并存**

`core.py:3171-3230` 先算结构相态 `TC → SD → SS/EX`：
```python
_sub_lat = |lat| >= 25.0 ; _sub_ev = (sst < 25.5 or shear >= 20.0)
if _sub_lat and _sub_ev and (weak>20 or (age>168 and vmax<50 and (sst<24.5 or |lat|>32))
                             or (land>0.6 and vmax<45 and age>24)): phase = 'SD'
elif phase=='SD':
    if vmax<32 or sst<20 or weak>45: phase = 'SS' if |lat|<30 else 'EX'
    elif vmax>70 and sst>26.5 and land<0.1: phase = 'TC'   # 重新热带化
```
非 TC 时 `dV2 = -0.08*(vmax/60)`，EX 且 sst<18 再 −0.5（`core.py:3201-3210`），并把 `rmw` **重置**回 `rmw_from_v(vmax)`（放弃预报量，`core.py:3209`）。

ET（温带变性，`core.py:3213-3230`）：四条件 `|lat|>=30 且 sst<27.5 且 shear>=22 且 vmax>=35` 持续 `ET_HOURS=12.0` 小时即转 SD。`core.py:2553-2562` 记录了 24h→12h 的实测依据："把四条件同时满足的最长连续段……全场最长只有 18h ⇒ 24h 的门槛在模型里根本不可能达到，EX 恒为 0 是设计上的不可能"。

LO（残余低压，`core.py:3232-3266`）：进门需 `age>=18` 且（`vmax<21` 或 `vmax<=33 且 (land>0.6 或 sst<24 或 weak>60)`），且（已命名 或 `_dvg<0`）；出门需 `_dvg>0.02 且 vpi>38 且 land<0.4`。LO 内 `_dVlo = -0.05 [-0.30 if sst<22] [-0.30 if land>0.6]`。

权威阶段码来自 `phase.classify`（`core.py:3274-3302` → `phase.py`）。`phase.py:41-56` 给出 `CAP_KT = {'DB':30,'WV':33,'LO':33,'MD':55,'SD':33,'SS':65}` 与 `WEAKEN_LADDER`（每次调用最多降一档，`phase.py:113-119`）。传参里有用 200hPa 温度算的暖心度 `dt_sst200`（`core.py:3276-3284`），缺 t200 时用 `40.0 + max(0,sst-24)*6.0` 兜底。

#### (n) 消亡判据（`core.py:3313-3336`）

```python
_fade_kt = params.get('fadeKt', 8.0)      # 低于它即死
_lo_cap  = params.get('loCapH', 72.0)     # LO 最多活 72h
_weak_h  = params.get('weakLifeH', 0.0)   # 默认关
if vmax < _fade_kt or weak > 120:                                  dead
elif phase=='LO' and (lo_h > _lo_cap or vmax < 10.0):              dead
elif vmax < weakV and (sst < 20.0 or land > 0.9):                  dead
elif phase=='EX' and vmax < 20:                                    dead
```
`core.py:3304-3312` 引 `tools/probe_death_cause.py --days 100`（179 系统）的死因分布：`lo_fade 61% / fade(<8kt) 22% / lo_cap(72h) 8%`，并指出"暖水上 10-19kt 的系统**可以无限期活着**"。

最后：`tc['vmax'] = clamp(vmax + dV*dt*v_target, 6, 250)`（`core.py:3165`），上限 185→250 是用户要求"185 是硬上限就去掉"（`core.py:3164`）。

### 3.4 移动方程 `motion_step` / `_free_motion`（`core.py:3476-3661`）

自由运动（`core.py:3476-3523`）：
```python
steer = steering_at(sim, lon, lat, vmax)
beta  = beta_drift(lat)
tc['noiseU'] += (gauss()*2.1*(0.4+r) - noiseU)*0.10        # 时间相关的随机扰动
tu = steer[0] + beta[0] + noiseU*r*1.6
tv = steer[1] + beta[1] + noiseV*r*1.6
_fu,_fv = fujiwhara_drift(...)                             # 藤原互旋
tu += _fu ; tv += _fv
if land>0.1: tu *= 1-0.25*min(1,land) ; tv 同             # 陆地减速
一阶滞后: mu += (tu-mu)*(1-exp(-dt/3.0))                   # _MOTION_TAU_H = 3.0 (core.py:3365)
```

**β 漂移**（`core.py:2316-2335`）：
```python
f = clamp(sin(|lat|)/sin(15°), 0.25, 1.4)
decay = 1/(1+((|lat|-32)/12)^2) if |lat|>32 else 1.0
hemi = +1 北 / -1 南
return (-3.6*f*decay,  2.9*f*decay*hemi)
```
**实测**（探针 p3）：lat=+15 → (−3.600, +2.900)；lat=+30 → (−5.040, +4.060)；lat=+50 → (−1.551, +1.249)；lat=−30 → (−5.040, **−4.060**)（南半球镜像 ✅）；**lat=0 → (−0.900, +0.725)** —— 赤道 β=0，但 `clamp(..., 0.25, ...)` 的地板给出 0.9kt 西向漂移（§6 MINOR-2）。

**引导气流**（`core.py:2081-2109`、`2245-2313`）：`steering_weights` **忽略两个入参**，恒返回 `(0.50, 0.25, 0.25)`：
```python
def steering_weights(vmax, lat):
    return 0.50, 0.25, 0.25     # eff850 = 0.50+0.6*0.25 = 0.65 ; eff200 = 0.35
```
`core.py:2084-2106` 是轮次 102 的重标定记录（`tools/probe_steer_obs.py` + `calib_steer.py`，1739 个 6h 步）：全局常数 eff850=0.65 总 RMSE **10.06**，逐带最优也只有 9.9 ⇒ 取常数。**实测确认 4 组 (vmax,lat) 全返回同一三元组**（探针 p3）。所以注释里旧的"|lat|>26° 时深层权重=1.0"已彻底废止，但函数签名仍保留 `vmax/lat`（§6 MINOR-3）。

分层采样（`steering_layers`，`core.py:2245-2300`）：3 环 × 8 方位 = 24 点面积加权（`_STEER_RINGS=((200,1.00),(400,0.80),(600,0.58))`，`core.py:2062`），权重要乘 `wl = 1-land`（`core.py:2266`），`wsum<0.5` 时退回中心点采样（`core.py:2281-2286`）。另加副高驱动的净向极修正 `sh_pole_steer`（`core.py:2194-2242`）：`d = |lat| - ridge_lat`，`ramp`（默认）或 `gauss` 型，默认 `shPoleS=22.4 / shPoleW=20 / shPoleMax=8.0`（`core.py:2189-2191`）。副高脊线由 `sh_ridge_lat`（`core.py:2112-2185`）从 **u850 的东风转西风零线**逐经度诊断，±20° 滑动平均。

`steering_at` 合成（`core.py:2303-2313`）：`mid = 0.6*s_sh + 0.4*s_dp`；`(ws*s_sh + wm*mid + wd*s_dp)/(ws+wm+wd)`；再叠 `params['steerU']*KT`。

**藤原互旋**（`fujiwhara_drift`，`core.py:3441-3473`）：
```python
if d<1 or d>800: skip
if max(v)/max(1,min(v)) > 3.0: skip                 # 强度差太多互不理
mag = min(4.0, 2.8*(o_vmax/50)*(1-d/800)*(rmw_km/30)**1.5)
方向 = 绕对方涡旋的切向（气旋式，hemi 反号）
```
标度校验（`core.py:3451-3452`、`3499-3500`）：d=200km/70kt/30km ≈ 3kt，d=700km ≈ 0.5kt。

**几何登陆求交**（`seg_land_cross`，`core.py:3381-3416`）：线段按 `span/(0.5*ENVD*111.32)` 分格采样（8..256 段），检测 `land` 从 ≤0.3 穿越到 >0.3，再二分 10 次细化。理由（`core.py:3387-3389`）："6h 一步、10kt 速度下线段长 60nm，而 0.5° 格约 30nm —— 按步采样完全可能跨过一条窄岛/海峡而错过登陆。这个问题**与步长无关**"。

**冷尾流沉积**（`core.py:3586-3650`）：仅当 `spd>6 且 sst>25`。
- 尾流中心在移动方向**右侧 60km**（`cx = lon + (60cosα − 60sinα)/111.32/cos(lat)`）。
- `Rkm = 150 + 40*min(1, vmax/97)`。
- 混合层越浅降温越大：`mld_gain = clamp(60/max(20,mld), 0.4, 3.0)`。
- Ekman 抽吸参数化：`we = ρ_a·C_D·V²/(1025·f·L)`，`pump = clamp(we*8e4, 0, 3)`（`core.py:3611-3615`）。
- **移速-尾流反馈**（Price 1981 型）：`speed_f = clamp(2/(1+sp_kt/8), 0.5, 1.8)`（`core.py:3620-3622`）。
- **障碍层**：`_bl = clamp(max(clamp((sst-28.5)/1.5,0,1), 0.75*exp(-((lat-_itcz)/7)²)), 0, 1)`，`bl_f = clamp(1-0.45*_bl, 0.55, 1)`；ITCZ 纬度 `_itcz = 6.0 + 5.0*cos(2π(month-8)/12)`（`core.py:3631-3638`）。
- 最终 `k = 0.30*(vmax/117)²*ohc_f*(0.7+0.3*rng)*mld_gain*(d_t/5)*speed_f*bl_f`；`ohc_f = 1-0.55*min(1,ohc/90)`；`d_t = ocn['Tml'] - ocn['Tsub']`（`core.py:3616`、`3639-3640`）。
- 沿**线段**沉积（`deposit_segment`，`core.py:3419-3433`），子步 `k = clamp(ceil(span/max(20,RMW*0.5)),1,12)`；`deposit_gauss`（`core.py:3341-3360`）用 `np.minimum(win + amp*g, 6.0, out=win)` 原地写入（**cap 6.0 同时作用于 `wake`(°C) 与 `ohcWake`(m)**）。
- Ekman 抽吸另铺一个 2.2×Rkm 的弱沉积（`k*dt*0.6*pump`）；OHC 消耗沉积 `k*dt*3.0`，半径 `Rkm+60`（`core.py:3645-3650`）。

**ACE**（`core.py:3656-3661`）：`_dace = vmax² * 1e-4 * (dt/6)`，同时记到 `tc['ace']` 与 `sim['aceSum']`（`core.py:3657-3658` 说明为什么必须记 sim 级：只读存活台风会漏掉已消散的）。

**A 模式（路径锁定）**（`core.py:3536-3568`）：`tc['track_lock']` 时用 `import_tc.track_at` 强制位置、把真实移速写回 `mu/mv`，再**共用**后半段（尾流/OHC/Ekman/登陆/ACE）。`core.py:3530-3535` 记录了旧实现直接 `return` 的后果："锁路径的台风从不冷却脚下的海面，MPI 永远停在气候态（热带 ~195kt），强度一路爬到 150-190kt…… 系统性 +40~+100kt 高偏"。

### 3.5 海洋柱与 `ocean_step`（`core.py:4351-4594`）

两层柱（`ocean_column_init`，`core.py:4379-4397`）：`Tml`（混合层温）、`h`（混合层深）、`Tsub = Tml - d_t`，`d_t = 4.0 + 4.5*exp(-(|lat|/22)²)`。

D26 与 TCHP 诊断（`d26_from_column`，`core.py:4400-4411`）：
```python
warm = max(0, Tml-26)
d26  = where(Tml>26, h + max(0,Tsub-26)/THERMO_GRAD, 0)          # THERMO_GRAD=0.05 °C/m
integ= warm*h + 0.5*max(0,Tsub-26)²/THERMO_GRAD
ohc_kj = RHO_C*integ/1e7                                          # RHO_C=4.086e6
```

混合层深度气候态（`_mld_raw`，`core.py:4369-4376`）：
```python
base = 36 + 52*exp(-((|lat|-26)/13)²) + 26*exp(-((|lat|-45)/16)²)
win  = clip(cos(2π(season-1)/12), 0, 1)
h    = base + (16 + 0.85*|lat|)*win*exp(-((|lat|-42)/28)²)
```

`ocean_step`（`core.py:4533-4594`）每模拟小时一步（`_sim_step_core` 内按 `ceil(step/1h)` 子步）：
1. 表层流 = `wind_driven_currents(sim)`（风生环流 + 黑潮/湾流） + `0.03 × u850` 偏转 30°（北右南左，`core.py:4551-4557`），按 `(_envVer, shape)` 缓存。
2. `_advect_env` 平流 `wake`（全速）与 `ohcWake`（0.4 速）。
3. 100% 陆格冻结（`all_land_mask()`，`core.py:4571-4580`）。
4. 指数恢复：`wake *= exp(-dt/(15*24))`（15 天）、`ohcw *= exp(-dt/(45*24))`（45 天）（`core.py:4581-4582`）。
5. `Tml` 向 `sst_clim_cached` 松弛（时间尺度 40 天）、`h` 向 `mixed_layer_clim` 松弛（20 天）（`core.py:4587-4594`）。

### 3.6 环境场 `env_update`（`core.py:1546-2024`）+ envmodel 求解

`env_update`（`core.py:2026`）是个薄包装，主体在 `_env_update_impl`（`core.py:1546`）。两条分支：

**(A) `dataMode=True`**：`climate.build_env_fields`（旧气候库）或 `envmodel.solve` 提供基态，`core` 再叠加：微扰动 0.06、冷尾流、模态/环流型（已在 solver 内）、中尺度涡 `mesoscale_eddies`、中尺度噪声 `mesoscale_noise`、MJO、ENSO 遥相关、瞬变槽脊引导、西风槽/冷空气。风切**大小**用 `DATA['shear']`、**方向**用 `u200-u850`（`core.py:1826-1838`，`core.py:1828-1832` 说明不这么做会把标定好的 18kt 顶回 37.7kt）。

**(B) `dataMode=False`（`init` 的默认值）**：纯解析场 —— `sst_clim`（`core.py:427-446`）、解析切变 `(shear_base + shear_seas + 2.5sin)·KT`（`core.py:1669-1673`）、解析湿度 `_B['rh']`（`core.py:1699-1706`）、多副高气压场 `pEnv`（`core.py:1718-1756`，7 个 `RIDGES` 中心，`core.py:450-458`）、地转风 + 30° 摩擦偏转 ×0.88（`core.py:1774-1781`）。

**两条分支的实质差异（实测，探针 p10/p1）**：

| 量 | `dataMode=False` | `dataMode=True` |
|---|---|---|
| 热带(±30°)切变均值 | 15.9 kt | 19.5 kt |
| **抽样生成点切变中位** | **16.9 kt** | **8.5 kt** |
| 抽样生成点 SST 均 | 27.79 ℃ | 28.73 ℃ |
| `_gi_weight` 接受率 | **0.89 %** | **17.43 %** |
| `env_update` 冷启动 | 567 ms | 3990 ms |

另有两处在**解析分支恒为 no-op**、只在数据分支才有值：
- `env['div200'] = np.full(..., 0.0)`（`core.py:1891`）vs 数据分支 `np.tanh((du+dv)/4e-6)`（`core.py:1920`）⇒ `intensity_step` 的出流修正 `_ov += 0.35*div200`（`core.py:2571-2574`）在解析模式下完全无效。
- `env['coldSurge'] = np.zeros(...)`（`core.py:1892`）vs 数据分支由气压梯度算（`core.py:1941`）⇒ `vortex_wind` 的冷高压危险半圆项（`core.py:337-352`）在解析模式下为 0（且 `vortex_wind` 本身生产无调用方，见 §6 MAJOR-6）。
（`env['div']` 两条分支都算，`core.py:1905`，所以 `cloud_step` 的辐合项不受影响。）

envmodel 求解链（`envmodel/solver.py:658-773`）：
```
solve(sim_params, clock)   → 按 (params 指纹, clock) 记忆化          solver.py:637
  └─ _solve_uncached                                                  solver.py:658
       ├─ base_available()（store.available(), 结果缓存）               solver.py:246
       ├─ _instant_fields(clock) → daily.load_fields(clock)          solver.py:193
       │     （有瞬时场时：基态就是那一刻的真实观测 ⇒ 不再叠增暖/模态/环流型，
       │      否则 El Niño 被算两遍，solver.py:665-668）
       ├─ sst/d26/shear: base + warming + mode_anomaly + regime_anomaly  solver.py:675-694
       ├─ 其余变量: base + regime_anomaly（**只在风场层叠**）           solver.py:696-713
       ├─ rh = rh700 ; uMid/vMid = 0.45*u850 + 0.55*u200               solver.py:716-720
       ├─ **shear × 0.55**（月均矢量差比日尺度大 ~1.8 倍）              solver.py:721-747
       └─ ohc = d26（米）; ohc_kj = tchp（按 d26 暖/未暖比值缩放）      solver.py:748-769
```
`store.py`：`MODEL_DIR = simulator/env/model`（`store.py:11-12`），`load` 用 32 份 LRU（`store.py:14`、`43-44`），`available()` 缓存 listdir（`store.py:61-74`，注释说单次 4.6ms），`generation()` 代次计数供下游缓存键失效（`store.py:50-58`）。

模态序列（`envmodel/modes.py:84-207`）：实测（`ok` 掩码）优先 → 全局/按月 `override` → AR 外推 `x = A·x + L·ε·seas`，`seas = 1+0.25cos(2π(mo-12)/12)`（ENSO 类噪声 11-1 月方差峰），外推值 clip 到 ±4σ（`modes.py:158-177`）。

### 3.7 `_advect_env`：半拉格朗日上游平流（`core.py:4282-4334`）

> **口径澄清**（Lead 转 sim-data 实测）：真正的半拉格朗日平流在 `core.py:4173-4292` 这一段（`_advect_env` 与它的采样器 `_sample_grid`），**不在** `simulator/clouds/render.py`（后者只是静态亮温合成 + IR-BD 色阶）。本节确认覆盖。

数值格式（`core.py:4298-4334`）：

```python
# 上游位移（格数）。dx/dy 由 us/vs(float32) 算出，但坐标斜坡必须 float64
dx = -u_kt*1.852*dt_h*scale / km_l_c / ENVD
dy = -v_kt*1.852*dt_h*scale / 110.57  / ENVD
```
- **时间步**：`ocean_step` 传 `dt_h` = 1 小时子步（`_sim_step_core` 里 `_n_oc = ceil(step/1.0)`、`ocean_step(_stp/_n_oc)`，`core.py:5090-5094`），`scale=1.0` 用于 SST 尾流、`0.4` 用于 OHC 消耗（`core.py:4576-4577`）。**不是 CFL 自适应步长**：位移量不参与步长选择。
- **边界条件**：经度**周期环绕** `fx = np.mod(arange(nx) + dx, nx)`（`core.py:4323`、`4328`）；纬度**夹取** `fy = np.clip(arange(ny) + dy, 0, ny-1)`（`core.py:4324`、`4329-4330`）。
- **插值**：双线性上游采样 `_sample_grid(field, nx, ny, fx, fy)`（`core.py:4175-4189` 定义，`4331` 调用）。
- **两条早退**（性能）：场全零直接返回（`core.py:4293-4294`，注释实测占 `ocean_step` 30ms 中的 20ms，且一年要调 8760 次）；`max(|dx|,|dy|) < 1e-3` 格时返回原场（`core.py:4300-4306`）。
- **包围盒优化**（`core.py:4307-4333`）：尾流半径约 200km≈4 格，而全网格 360×720=259200 格 ⇒ 只在非零区域外扩 `ceil(dmax)+2` 格做。**经度一旦跨边界就退回全网格**（`core.py:4317-4325`），注释记录了不这么做的实测报错 `"operands could not be broadcast (1,357) (134,354)"`。

⚠ 我**没有**在本环境验证 `_advect_env` 的守恒性/精度（无参考解），故"它是否正确实现半拉格朗日"标为推断；可确证的是格式（上游 + 双线性 + 经度 mod 环绕 + 纬度 clip）、子步长来源与调用点。

### 3.8 生成 `gen_check`（`core.py:4028-4170`）

```python
if not params['genesis']: return
if 存活数 >= _GEN_MAX_ALIVE(60): return                                   # core.py:4031
rate = _GEN_RATE(0.84) * params['genesisRate'] * (0.4 + params['random'])  # core.py:4035
if rng() > rate: return
for _ in range(_GEN_TRIES=2):
    从观测生成密度场 dens(month) 抽样（+ 模态调制 0.5/0.5 合成）           core.py:4046-4118
    env = sample_env(...) ; if env['land'] > 0.3: continue                 core.py:4124-4126
    if rng() < _gi_weight(C, env, lat, lon):
        _dist = rng() < disturbFrac(0.87) ; _sb = stillbornFrac(0.60) and rng()<_sbf
        make_tc(vmax = 6+7r 若纯扰动 否则 15+5r)                            core.py:4154-4155
        classify_genesis → 定初始 RMW 与 core_org                            core.py:4163-4167
```

生成类型分类（`classify_genesis`，`core.py:3980-4025`）产出 5 类，各带初始 `(RMW, org)`：

| kind | 判据（按顺序） | rmw0 / org0 |
|---|---|---|
| `ex_ss` | `|lat|>25 且 sst<25` | 48.0 / 0.34 |
| `cold_low` | `|lat|>25 且 sst<27`，或 `t200>-48℃ 且 |lat|>18` | 38.0 / 0.46 |
| `monsoon_dep` | `u850>1.5 且 rh>62 且 |lat|<20` | 44.0 / 0.40 |
| `easterly_wv` | `u850<-1.5 且 rh<62` | 24.0 / 0.72 |
| `itcz` | 其余且 `||lat|-ITCZ|<9`，否则 `easterly_wv` | 32.0 / 0.58 |

`_gi_weight`（`core.py:3937-3978`）是 4 因子 `np.interp` 连乘（sst/shear/rh/lat），曲线来自 `envmodel/tc_genesis.npz`（`core.py:3735-3744`）。该函数的 docstring 是一份**回退记录**：压缩/软闸门/硬否决/盆地归一化四种改法全部试过并回退。

### 3.9 集合预报（`core.py:4668-4938`）

- `forecast_env_series`（`core.py:4675-4750`）：把**未来** 72h 的环境逐 6h 算出来（`env_update` 只依赖时钟、与风暴位置无关，所以 12 个成员共享一份序列）。必须保存/还原 `sim['t']/month/_envVer/enso/ocn/lastEnvH/_envBase`（`core.py:4693-4703`），注释说明不还原 `lastEnvH` 会让主模型**整整 72 小时不再刷新环境**。
- `forecast_members`（`core.py:4751-4856`）：12 成员蒙特卡洛扰动。
- `run_ensemble`（`core.py:4857-4938`）：算 `fcPeak`（各成员自身峰值的均值 + P10/P90）与 `fcLand`（首次触陆强度，需 ≥40% 成员登陆，`core.py:4915`）。

**实测成本**（探针 `/tmp/sc/p6.py`，本机）：
```
run_ensemble 首次            = 5469 ms   (nMem=12, hours=72)
forecast_env_series(72h,6h)  = 7790 ms
一步(默认 forecast=True)     = 9289 ms
一步(强制重算集合)           = 9300 ms
```
即 **默认配置下每个 6h 模拟步要跑一次完整的 12 成员 72h 集合，约 9.3 秒墙钟**；`core.py:5097-5098` 估的是"13 次 env_update（约 4 秒）"。而 `params['forecast']=False` 时同一步只需 **430 ms**（探针 p2）—— **21 倍**。见 §6 MAJOR-1。

### 3.10 主循环分帧与精度档位（`sim_step`，`core.py:4994-5047`）

```python
_tgt,_inner,_hold,_qual = precision_spec(params['precision'])
pend = sim['_pendStep']                    # 换档/换步长 → 丢弃半截记账重开
_n = min(inner - pend['i'], max_inner)     # max_inner=1: 每帧只跑 1 个内步
for _ in range(_n):
    if inner > 1: _sim_step_core(sim, dt/inner, hold, force_env=True)
    else:         _sim_step_core(sim, dt, hold, force_env=False)
sim['stepCostMs'] 指数平滑（×0.15）        # 只累计"干活"时间，不含帧间空闲
```
`core.py:5006-5010` 说明 `max_inner` 是"自动降速的地基"（2s/5s 档一个 6h 步要 2-5s，一口气跑完会冻住 UI）。

### 3.11 `rewind.py`：引用快照 + 少数真拷贝

设计要点（`rewind.py:1-171`）：
- **引用快照**：`env_update` 对每个大场都是 `env[k] = <新数组>`（rebind）⇒ 整张 env 按引用进快照、0 字节（`rewind.py:12-30`）。
- **必须真拷贝**的 8 类原地改写（`rewind.py:32-56`）：`sim['wake']/sim['ohcWake']`（`np.copyto`/`*=`）、`sim['ocn']`（`Tml += `）、`sim['eddies']`、`sim['dryBlobs']`（还会改顺序）、`sim['_pendStep']`、`sim['_envBase']`、`sim['enso']`（`e['t']` 是积分分段边界）、`sim['params']/usedNames/investSeq/cySeq`（浅拷贝）。
- **只记长度不复制**：`tc['track']` / `tc['hist']`（恢复时 `del lst[n:]`）；代价是恢复点之后的快照全部作废（`rewind.py:58-80`）。
- **恢复时显式失效**：`sim['_envVer'] += 1`、`sim['_cldVer'] += 1`（否则版本号会撞上旧缓存内容，`rewind.py:112-125`）。
- **`params` 采集但不写回**（`rewind.py:96-110`）：拖时间轴不该顺手回退用户刚改的配置。

### 3.12 `phase.py` 阶梯（`phase.py:1-119` 及 `classify`）

- 11 个阶段码 `PHASE_CODES`（`phase.py:56`）。
- `CAP_KT`（`phase.py:83-84`）：`DB 30 / WV 33 / LO 33 / MD 55 / SD 33 / SS 65`；LO 上限在 `weak >= LO_UNCAPPED_WEAK=60.0` 时取消（`phase.py:80-82`，物理理由是"LO 的物理含义是槽/非闭合系统，与强度无关"）。
- `WEAKEN_LADDER`（`phase.py:113-119`）：`TS --(vmax<34)--> TD --(vmax<20)--> LO --> DB`，**每次调用最多降一档**。
- 阈值 `KT_TS=34 / KT_TY=64 / KT_ST=130`（`phase.py:106-107`）。

---

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `simcore/land_0.5.npz` | npz，`land` float32 **(240,720)** | 0.69 MB | `build_land()`（`core.py:516-582`，首次 ~28s） | `build_land()` 的**第二**优先分支（`core.py:534-542`）—— 见 §6 MAJOR-2 |
| `simulator/env/model/land_cell.npz` | npz，`landfrac` float32 (360,720) + `all_land` bool (360,720) + meta | 51 KB | `tools/make_cell_land_mask.py`（`core.py:487-491`） | `build_land()` 首选（`core.py:528-533`）、`all_land_mask()`（`core.py:511-513`）、`ocean_step`（`core.py:4573`） |
| `simcore/terrain_0.5.npz` | npz，`elev_mean`/`elev_max`/`landfrac` (360,720) + `lat0/lon0/d/meta` | 0.61 MB | `tools/build_terrain_dem.py`（`core.py:40`） | `terrain.load()`（`terrain.py:413-434`），仅 `terrainOn=True` 时 |
| `simcore/terrain_0.5_synth.npz` | 同上（合成版，`elev_max` 达 8815m） | 0.22 MB | `terrain.synthesize()`（`terrain.py:339-371`） | 仅测试/回退；`terrain.py:398` 的 `_FILE` 指向 `terrain_0.5.npz`，所以 synth 版**默认不被加载** |
| `simcore/ne_110m_land.geojson` | GeoJSON，127 个 Polygon、5091 个顶点 | 0.14 MB | Natural Earth 110m | `_load_polys()`（`core.py:463-482`）→ `build_land()` 的兜底分支 |
| `simulator/env/model/base_*.npz` | sst/d26/shear/wind/other/tchp | 共 ~35 MB | `tools/train_env_base.py` | `envmodel/solver._base_field` |
| `simulator/env/model/modes_index.npz` / `modes_pat.npz` | 模态指数 / 型 | 0.22 / 1.27 MB | `tools/train_env_modes.py` | `modes.ModeSeries`、`solver._mode_anomaly` |
| `simulator/env/model/regimes.npz` | 逐日环流型 + 响应型 | 1.82 MB | 训练产物 | `solver._regime_*`、`core.latest_real_start`（`core.py:5165`） |
| `simulator/env/model/tc_clim.npz` | `genesis`(12,120,360) `lat`(120) `lon`(360) `landfall` `life` `spd` `vmax` `track` | — | `tools/train_tc_genesis.py` 一族 | `_gen_tables()`（`core.py:3716-3732`） |
| `simulator/env/model/tc_genesis.npz` | `{sst,shear,rh,lat}_ctr/_w` 经验权重曲线 | 3.8 KB | 同上 | `_gi_weight`（`core.py:3735-3744`） |
| `simulator/env/model/tc_genesis_modes.npz` | `clim`(12,BNY,BNX) + `b_<mode>` | 19 KB | 同上 | `gen_check` 的模态调制（`core.py:3750-3762`） |
| `simulator/env/model/tc_mjo.npz` | RMM（存在时） | 243 B | 训练产物 | `mjo_field`（`core.py:1285`） |
| `simulator/env/model/` 里的 `tc_names*.json` | 命名表/命名时刻/年度表 | ~230 KB | 抓取产物 | `naming.Namer`（`naming.py:83-142`） |
| `simulator/env/train_cache/era5/` `cmems/` | ERA5/CMEMS 单日缓存 | — | 下载产物 | `daily.load_fields`（`daily.py:301`） |

**内存覆盖**：`sim['env']` 有 12 个 (360,720) float32 场 ≈ 12.4 MB；`sim['wake']`/`ohcWake`/`land`/`cld`/`cldNoise` 各 1-1.04 MB；`sim['ocn']` 4 个 (360,720) 场。

---

## 5. 如何运行 / 如何验证

### 5.1 环境

| 解释器 | 版本 | 可用 | 用途 |
|---|---|---|---|
| WSL `/usr/bin/python3` | 3.14.4 | numpy 2.3.5 / scipy / xarray / netCDF4 / pandas / PIL；**无 pygame、无 pytest** | numpy 数值探针 |
| `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe` | 3.14.2 | pytest 9.1.1 / pygame 2.5.7 / numpy 2.5.1 / scipy / matplotlib / xarray / netCDF4 / pandas | **项目真实运行时**；任何涉及 pygame/pytest 的验证 |

> Windows 解释器经 WSL 管道的中文输出是 GBK：探针脚本里加 `sys.stdout.reconfigure(encoding='utf-8')`（我用的）或管道加 `| iconv -f GBK -t UTF-8`。`env PYTHONIOENCODING=utf-8` 对它**无效**。

### 5.2 冒烟命令（本机实测通过）

```bash
# 导入 + 建场（WSL 解释器亦可）
python3 -c "import simulator.simcore.core"                       # ✅ 已实测

# 真实运行时下跑 simcore 相关定向测试（本机实测汇总）
"/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe" -m pytest \
  tests/test_simcore_motion.py tests/test_phase_ladder.py tests/test_simcore_ocean.py \
  tests/test_simcore_steer_friction.py tests/test_simcore_ridge.py \
  tests/test_simcore_synoptic.py tests/test_simcore_terrain.py -q --no-header
# → 182 passed, 2 skipped in 127.44s

"/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe" -m pytest \
  tests/test_simcore_rewind.py tests/test_envmodel_cache_hardening.py \
  tests/test_envmodel_solve_memo.py tests/test_phase_ladder.py tests/test_sim_modes.py -q
# → 134 passed in 45.50s
```

⚠ 未跑全量 pytest（简报铁律 5）。

### 5.3 我的只读探针（原始数字来源）

| 脚本 | 内容 | 关键输出 |
|---|---|---|
| `/tmp/sc/p1.py` | `init` + 首次 `env_update` | 冷启动 **673 ms**，热 **182 ms**；`sst∈[3.26,30.52]`、`ohc∈[18,95.2]`、`shear∈[2.92,120.62]`、`rh∈[45.2,83.0]` |
| `/tmp/sc/p2.py` | 手动放入 1 台 @(138°E,15.5°N) 跑 240h（`dataMode` 默认；`forecast=False`） | 35kt→**峰值 108.6**（`hist` 最大）→ 谷 **59.5** → 末 **68.8**；`ACE=21.6`；`distKm=3408`；末阶段 `TY`；**430 ms/步** |
| `/tmp/sc/p3.py` | 9 个纯物理函数的数值表 | 见 §3.3(a)(b)、§3.4 |
| `/tmp/sc/p4.py` | 180 天、seed=11、2025-06-01 起、genesis 开 | 720 步 / 742s（**1031 ms/步**）；**生成 14 个**；`ACE_sum=44.40`；阶段码记录 `TD 107 / DB 95 / LO 67 / TS 65 / TY 24 / EX 6 / SS 2 / SD 1`；峰值 P50 **31.4** / P95 85.4 / max **110.6**；≥64kt **3 个**、≥130kt **0 个** |
| `/tmp/sc/p5.py` | 资产体检 | `land_0.5.npz` 是 **(240,720)**；`terrain_0.5.npz` `elev_max` 7488m；geojson 127 多边形 |
| `/tmp/sc/p6.py` | 集合成本 | `run_ensemble` 5469ms、`forecast_env_series` 7790ms、**默认一步 9289ms** |
| `/tmp/sc/p7.py` | 生成表 + 接受率重放 | `tc_clim` **有** `lat`/`lon`；`dens_lat`/`dens_lon` **未载入**；2000 步重放：3042 个采样点 → **接受 21**（0.69%）→ 外推 **~15/年** |
| `/tmp/sc/p8.py` | 纬轴核对 + `_gi_weight` 源码 | `tc_clim['lat']` = −59.5..59.5，与兜底公式**偏差 0.000**；`dens` 各月总和 6 月 79 / 9 月 195 |
| `/tmp/sc/p10.py` | **`dataMode` 对照实验**（同 seed / 同日期 / 同接受链） | `False`: 接受率 **0.89%** → ≈**20 生成/年**，`env_update` 567ms；`True`: 接受率 **17.43%** → ≈**385/年**，`env_update` **3990ms**（详见 §6 MAJOR-4） |

> 探针脚本写在 `/tmp/sc/` 与 `C:\Users\...\Temp\sc\`（Windows 解释器只能读 Windows 路径），均在工作区之外，未改动仓库任何文件。

### 5.4 本环境跑不了的部分

- **GUI 主程序**：`main.py` 需要 pygame + 显示器；可导入但不能端到端跑（本环境无图形会话）。
- **`render.py` 的绘制路径**：同样需要 pygame surface。
- **`climate.build_env_fields` 的 `dataClim=False`（具体年份库）分支**：依赖 `simulator/env/library/` 的产物（`climate.py:52-56`），本报告未验证其完整性。
- **长年尺度验收**：180 天就 742s（genesis 开、forecast 关），全年 >25 min/seed；多种子验收不在本次范围内。

---

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜默认参数下每步都跑一次 12 成员 72h 集合：单步 9.3 秒

- **现象**：`init()` 的 `params` 字典里**没有** `forecast` 键（`core.py:803-823`），而 `_sim_step_core` 用 `.get('forecast', True)`（`core.py:5101`）⇒ 默认开启。`run_ensemble` 的新鲜判据是 `sim['t'] - ensemble['hour'] < 5.9`（`core.py:4858`），而每步正好推进 6h ⇒ 每步必然重算。
- **证据**：`core.py:5096-5104`；实测（探针 p6）`run_ensemble`=5469ms、`forecast_env_series(72h)`=7790ms、**单步 9289ms**；关闭后同一步 **430 ms**（探针 p2）⇒ **21×**。
- **影响**：① 6h 模拟时间要 9.3s 墙钟，`PRECISION_TIERS` 的 50ms/100ms 档（目标 50/100ms/6h）在默认配置下**物理上不可能达到**，`stepCostMs` 实测值会被集合成本完全支配；② 用 `core` 做任何年尺度统计（我这次 180 天）必须先手动关掉 `forecast`，否则时间成本不可接受；③ `core.py:5097-5098` 自估"约 4 秒"已被实测翻倍。
- 注：`core.py:5096-5099` 自己写了"年尺度验收完全不需要集合"，说明作者知道，但**默认值是开**、且 `tools/probe_gen_rate.py` 那类标定工具并未关它。

### MAJOR-2｜`land_0.5.npz` 形状陈旧（240 行 vs 环境 360 行）→ 永远走不到

- **现象**：`build_land()` 要求 `arr.shape == (ENVNY, ENVNX) = (360,720)`（`core.py:538`），而 `land_0.5.npz` 里 `land` 是 **(240,720)**（探针 p5）⇒ 形状检查失败、落回多边形路径。
- **证据**：`core.py:516-582`；`ls -la simulator/simcore/land_0.5.npz` = 691 KB、2025-09-17（早于扩域到 ±90°）；扩域记录见 `core.py:48-52`。实际生效的是 `land_cell.npz`（(360,720)，`core.py:503` 通过检查）。
- **影响**：这是"扩域漏改资产"的残留。今天无害（`land_cell.npz` 优先命中，`core.py:530-533`），但**一旦 `land_cell.npz` 缺失**，`build_land()` 会退到 ~28s 的多边形扫描（`core.py:517`），并**回写**一份 (360,720) 的 `land_0.5.npz`（`core.py:578-581`）—— 即静默自愈、掩盖了资产陈旧这件事本身。

### MAJOR-3｜`_gen_tables` 用 `z.files` 访问 **dict** ⇒ `dens_lat`/`dens_lon` 从未被载入

- **现象**：`_gen_tables()` 里 `z = store.load('tc_clim')` 返回的是 **dict**（`store.py:37-38` 构造 `{k: z[k] for k in z.files}`），但代码写的是 `if 'lat' in z.files:` / `if 'lon' in z.files:`（`core.py:3729-3732`）—— dict 没有 `.files`，`AttributeError` 被同一 `try` 的 `except Exception: pass`（`core.py:3733-3734`）吞掉。
- **证据**：探针 p7 输出 `_GEN_CACHE keys = ['dens','modes','ok','w']` —— **没有 `dens_lat`/`dens_lon`**；而 `tc_clim.npz` 实际**有** `lat`(120) 与 `lon`(360)（探针 p8）。
- **影响**：① `gen_check` 落进兜底分支，`core.py:4100-4114` 的注释把根因**误判**为"`tc_clim` 里**没有** `lat` 成员"——这是错的，真因是 dict/NpzFile 的 API 混用；② 当前兜底公式（`lat=(jy+0.5)-120/2`）与真实纬轴**偏差 0.000**（探针 p8），所以今天数值上无害；③ 但这是**潜伏**的：一旦 `dens` 改成非 120 行或非 1° 栅格，兜底会静默给出错误纬度，而 `except Exception: pass` 保证没有任何提示。

### MAJOR-4｜生成率差 19 倍的根因锁定：`core.init` 默认 `dataMode=False` 与真实运行口径（True）不一致

> 这一条是两个现象合并后的定论（原"MAJOR-4 生成率不符"与"MAJOR-5 dataMode 默认值不一致"是同一个 bug 的两面）。对照实验已跑完（探针 p10）。

- **现象 A（口径不一致）**：`init()` 默认 `'dataMode': False`（`core.py:809`，注释写"True = 气候库/模态模拟器, False = 解析公式"）；而 GUI 的默认是 **True**：`app/config.py:141: sim_data_mode: bool = True` → `sim_mode_mixin.py:225` → `sim_mode_mixin.py:841` 传入 `init`。所有标定工具用的也是 True（`tools/probe_gen_rate.py:37`、`tests/test_envmodel_cache_hardening.py:81`）。
- **现象 B（差额量级）**：`_GEN_TRIES` 的注释（`core.py:3872-3875`、`3882`）记录"修后实测：tries=2 → ~358/年"，并据此把 `_GEN_TRIES` 定为 **2**（`core.py:3884`），用户目标"250 生成/年"。用 core 默认值跑，实测只有 **~15-28/年**。
- **对照实验（探针 p10，seed=11，2025-06-01，同一密度场与同一接受链）**：

| `dataMode` | `env_update` 冷启动 | 热带(±30°)切变均值 | 热带 SST 均值 | 4000 抽样点接受率 | **外推生成/年** | 抽样点切变中位 | 抽样点 SST 均 |
|---|---|---|---|---|---|---|---|
| **False（core 默认）** | 567 ms | 15.9 kt | 27.04 ℃ | **0.89 %** | **≈ 20** | 16.9 kt | 27.79 ℃ |
| **True（GUI / 全部标定）** | **3990 ms** | 19.5 kt | 26.30 ℃ | **17.43 %** | **≈ 385** | 8.5 kt | 28.73 ℃ |

  ≈385/年 与注释里的 ~358/年 吻合（差 7%），⇒ **差额 100% 由 `dataMode` 解释**；机制是 `_gi_weight`（`core.py:3937-3978`）的切变权重曲线在**真实再分析切变**上训练，而解析分支抽样点的切变中位 16.9kt（解析式 `(shear_base+seas)*KT`，`core.py:1669-1673`）比数据分支的 8.5kt 高一倍，直接把接受率从 17.4% 压到 0.9%。
- **端到端佐证（探针 p4）**：`dataMode` 默认下 180 天（2025-06-01 起，genesis 开）**只生成 14 个**（外推 ~28/年），`aceSum=44.40`，峰值 P50 31.4 / max 110.6、≥130kt **0 个**；阶段码记录 `TD 107 / DB 95 / LO 67 / TS 65 / TY 24 / EX 6 / SS 2 / SD 1`。
- **影响**：① 任何"直接调 `C.init({})`"的脚本、工具、测试都落在**另一个环境场分支**，得到与 GUI 完全不同的气候统计 —— 而这件事在 `init` 的 docstring 与 `_GEN_RATE`/`_GEN_TRIES` 的标定注释里**没有任何提示**；② 解析分支的生成率只有目标的 1/19，属于"跑得通但毫无气候意义"；③ `gen_check` 的注释（`core.py:4039`）说"实测只生成 85 个/年"，与 `_GEN_TRIES` 注释里的 358/年 差 4.2 倍；现在可以判定 85/年 那一组大概是解析分支（或早期参数）的读数，**同一文件里两组互不相容的数字得以解释**。
- 附带问题：`tests/test_simcore_batch.py:25` 还传了第三种值 `"dataMode": "off"`（字符串）—— 非空字符串为真，语义上等于 True（`core.py:1562` 只做 `if P.get('dataMode')`）。同一参数存在 `False/True/"off"` 三种写法。
- 附带成本：`dataMode=True` 的 `env_update` 冷启动 **3990 ms** vs 解析分支 **567 ms**（探针 p10）—— 即 GUI 走的那条路每 6h 环境重建贵 7 倍。

### MAJOR-6｜三条支路在生产中是事实死码：云场、涡旋风合成、气压亏损

- **云场**：`cloud_step`（`core.py:4210-4279`）的唯一生产调用点已在 app 侧移除。证据：`grep -rn cloud_step` 全仓只有 app 的一句注释（`sim_mode_mixin.py:1630`"云场已停算(模拟模式不要云图)"）+ `core.py:4210` 定义 + 4 处测试；`_sim_step_core`（`core.py:5050-5150`）不含它。
- **涡旋风合成**：`vortex_wind`（`core.py:300-353`）与 `vortex_wind_arr`（`core.py:674-740`）**在生产代码里零调用** —— `grep -rn` 只在 `core.py`（定义处）、`synoptic.py:428`（一句已过期的注释说"三个调用点 core.py:4345/4373"）与 `tests/test_sim_mode_ref.py:198-204` 出现。生产的风场采样走 `wind_at_arr`（`render.py:1002/1141/1196`）。
- **气压亏损**：`tc_deficit`（`core.py:292-297`）在生产里零调用（仅定义处）。
- **影响**：`sim['cld']`/`cldNoise`（各 ~1 MB）+ `cld_sample`（`core.py:4652-4665`）+ `render_sat`（`render.py:275`）+ `_cloud_surface`（`render.py:253`）构成约 200 行 + 2 MB 的**事实死码**；更麻烦的是 `rewind.py:96-110` 仍把 `_cldVer` 列入"恢复时必须 bump"的失效键 —— 那是为一条已经不跑的通路保留的复杂度。`vortex_wind` 里的冷高压危险半圆逻辑（`core.py:328-352`，含"符号反了、危险半圆反而比安全半圆弱 1.8kt"的修 bug 记录）因此**这条知识不再被任何生产路径执行**。

### MAJOR-7｜文档行号大面积漂移（跨模块引用全部指向错行）

- **现象**：多个模块的注释用 `core.py:NNNN` 定位，但那些行号是旧版本（core.py 已 5205 行）的。
- **证据（逐条核对）**：
  - `rewind.py:9-10` 引 `core.py:4417-4418`（"数组用引用快照"）—— 现 4417 行在 `d26_from_column` 区间；真实对应文字在 `core.py:4690-4691`。
  - `rewind.py:14` 引 `_env_update_impl`(core.py:1388-1818) —— 实际 **1546-2024**。
  - `rewind.py:34` 引 `core.py:4303-4309` 的 `np.copyto` —— 实际 **4576-4580**。
  - `rewind.py:36-37` 引 `core.py:4320-4321` 的 `ocn['Tml'] +=` —— 实际 **4593-4594**。
  - `ridge.py:68` 说四向伸展"逐字等于 `core.py:1551-1553`" —— 1551 行现在是 `sim['month'] = clock(sim).month`；真实调用点在 `core.py:1745`。
  - `ridge.py:98` 引 `core.py:1550` 的 wrap180 算式 —— 1550 行同样是月份赋值。
  - `synoptic.py:54/60/61` 引 `core.py:44/1038/1039` —— 现 `ENVD` 在 `core.py:56`。
  - `synoptic.py:428` 说"`vortex_wind` 的三个调用点都没传 env（core.py:4345/4373）" —— `vortex_wind` 现在**一个生产调用点都没有**（只有测试），结论本身过期。
  - `phase.py` 文档与 `core.py:3176` 的接线注释自洽，未漂。
- **影响**：`core.py` 里"某处注释引用另一处行号"的交叉引用是这个仓库的核心知识载体（很多标定结论只存在于注释里），行号漂移使这些引用**逐条失效**、且无法用 grep 定位。这是**维护性 BLOCKER 级的知识损失**，但当前不影响运行时行为。

### MINOR-1｜`rmw_eq` docstring 标定表与实现不符

`core.py:208-210` 的表（20-34→45 / 34-45→40 / 45-55→30 / 55-65→25 / 65-80→20 / 80-100→15）与 `core.py:224` 的 `12+33exp(-(v-30)/35)` 实测差 1.4–10.9 nm（详见 §3.3(b)），而 docstring 声称"误差都 ≤2nm"。100nm 以上的额外收紧（`core.py:229-230`）也未反映在表里。

### MINOR-2｜`alpha_outer` 的两个锚点与线性式差 0.017

`core.py:263` 写"由两个锚点解出 α(50kt)=0.729、α(185kt)=0.891 → 线性式如下"，但 `core.py:265` 的 `0.70 + 0.0012*min(max(v-40,0),145)` 实测给 α(50)=**0.7120**、α(185)=**0.8740**。用锚点反推的直线应是 `0.669 + 0.0012v`（截距 0.669 ≠ 0.70）。差值 0.017 会让 `radius_at_wind` 的 R34 系统性偏小几 %。

### MINOR-3｜`steering_weights(vmax, lat)` 两个入参被完全忽略

`core.py:2081-2109` 恒返回 `(0.50,0.25,0.25)`（探针 p3 四组输入验证）。参数保留但仍出现在签名里，**调用方会以为有强度/纬度依赖**。另外该函数 docstring（`core.py:2082`）与 `core.py:2090-2096` 的表都还在讨论分层，但"浅层主导"这一强度相关物理（弱系统受 850hPa 引导）已不再表达。

### MINOR-4｜赤道处 β 漂移因地板不为 0

`beta_drift(0)` = `(-0.900, +0.725)` kt（探针 p3），`clamp(sin|lat|/sin15°, 0.25, 1.4)` 的地板在赤道给出了 β=0 时不该有的西向+向极漂移。当前无实际后果（无台风在 0°），但会把"生成点落在赤道附近"的路径往错误方向推。

### MINOR-5｜`_mjo_tables` 被定义两次

`core.py:1267-1283` 与 `core.py:1366-1382` 是**逐字相同**的两个 `_mjo_tables` 定义，后者遮蔽前者；模块级 `_MJO = {'ok': False, 'lut': None}` 也在 `core.py:1264` 与 `core.py:1363` 各写一次。属纯死码重复（行为等价）。

### MINOR-6｜常量注释与实际取值不一致

- `core.py:704-706` 说档位是"1/3/6/**12/24**h"，紧接的 `ENV_STEPS = (1.0, 3.0, 6.0)`（`core.py:707`）只有三档（同一段的"上限锁 6h"是对的）。
- `core.py:364-365` 说"参考态取 Ts=300K / **To=210K**"，而实际 `T200_REF = 222.0`（`core.py:1332`）——`core.py:382-384` 解释了为什么必须用 222，但顶部这句没同步。
- `core.py:4044` 仍写"用户目标: 观测全球 ~200 个扰动/年 ⇒ **6 -> 14**（2.33 倍）"，而当前 `_GEN_TRIES = 2`（`core.py:3884`）—— 轮次 41 的旧结论没清。
- `core.py:2583-2584` 用 `dt/24` 做 MPI 记忆，但 `mpi()` 的 `outflow` 参数在 `intensity_step` 里先被 `div200` 修正过（`core.py:2571-2574`），而 `hist` 里另存一个**不带**该修正的 `vpi`（`core.py:5127`）——同一时刻 `tc['env']['vpi']` 与 `tc['hist'][-1]['vpi']` 可能不等。诊断口径不一致，未验证影响面。

### MINOR-8｜ERC 重写（轮次 49）留下的两处死角：`v_target` 与 `erc['dip']`

- `v_target` 在 `core.py:3119` 被赋成 `1.0`，全程**再无任何写点**（`grep -n v_target` 只有 3119、3125 注释、3165 使用），但仍以 `tc['vmax'] = clamp(vmax + dV*dt*v_target, 6, 250)` 的形式参与最终更新（`core.py:3165`）。`core.py:3125` 的注释自己说明旧写法 `v_target = 1 - dip*sin(pi*x)` 已被替换 ⇒ `v_target` 是改写后的残留乘法。
- `erc['dip']` 在 `core.py:3116` 被写入，但**全仓没有任何读取点**（`grep "dip"` 只有 3116 一处）—— 同一次重写（"低谷改为强度上的真实亏损"，`core.py:3124-3138`）把触发时的 `dip` 留成了死键。
- **影响**：纯清洁度问题（数值上 `×1.0` 与写入未读的键都不改变结果），但它说明**轮次 49 的那次重写没有清理旧参数**，而 `erc` 字典的键集（`T/t/merger/rF/dip`）在 `tests/test_simcore_*` 与离线分析里被当作接口读，多一个永假键会误导下游。

### MINOR-9｜`tc['distKm']` 的 A 模式计数与自由运动口径不同

`tc['distKm'] += hypot(u*1.852*dt, v*1.852*dt)`（`core.py:3585`）在 A 模式下用的是**观测移速**（`core.py:3548-3550`），在自由运动下用的是模式移速 —— 两者都能算"走过的距离"，但混在一起做统计时口径不同（未验证是否有下游依赖）。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U1 | `TAU_INTENSIFY_H` 为何最终是 38h 而非 docstring 反推的 ~600h（`core.py:2532-2540`）。同一段注释同时给出"观测反推 600h"与"现行 38h"，没有说明放弃 600 的理由 | `INTENSITY_TARGETS_V2.md` 的判据表 + 能跑多种子强度分布验收的环境 |
| U2 | ~~生成率差额是否由 `dataMode` 决定~~ **已确证**（探针 p10）：`False` ≈20/年 接受率 0.89%，`True` ≈385/年 接受率 17.43%。见 §6 MAJOR-4 | — |
| U3 | `_advect_env` 的**数值精度/守恒性**（半拉格朗日上游格式的数值耗散、是否有质量不守恒） | 需要一个解析平流参考解或与 `tools/` 里未找到的对照脚本；本报告只确证了格式与调用点 |
| U4 | `render.py`（1648 行）的完整结构与死码边界。我只读了 §1 的 API 清单与 `render_sat`/`wind_at_arr` 的调用点；`GridMap`/`draw_*` 系列未逐行读 | 归 03（app-sim）分区，或本任务追加预算后重读 |
| U5 | `fill.py`（173 行）与 `import_tc.py`（186 行）只读了函数清单，未读实现 | 同上，属 P2 |
| U6 | `climate.py:354-523` 的 `build_env_fields` 具体合成式（`dataMode=True` + `dataClim=False` 的具体年份库分支）只读了函数清单 | `simulator/env/library/` 产物的完整性 + 一次带 `dataClim=False` 的探针 |
| U7 | `sim['env']['div200']` / `coldSurge` 在 `dataMode=False` 分支下恒为 0（`core.py:1891-1892`），因此出流修正与冷高压项在默认分支恒为 no-op（`core.py:2571-2574`、`core.py:337`）。已确证"恒为 no-op"这一事实；**未量化**它对解析模式强度分布的影响 | 关掉 `div200`/`coldSurge` 做 A/B 的强度分布统计（需 dataMode=True 长跑） |
| U8 | `land_0.5.npz` 是否曾被 `tools/` 里某个脚本当作**输入**（若是，则该脚本也读到了旧尺寸） | `grep -rn "land_0.5" tools/ app/`（我只查了 simcore 内部） |
| U9 | `sim['land']` 在 `dataMode=True` 下是否会被 `env_update` 覆盖成另一套（`_hi_land_mask` 是 render 侧的） | 读 `core.py` 全文中对 `sim['land']` 的写点 |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 全球常数 / 网格尺寸 / 单位 | `core.py:47-72` |
| CMA 等级 / 洋区划分 / 生成区 | `core.py:108-152`、`core.py:126-134` |
| RNG（mulberry32 / 稳定种子） | `core.py:166-198`、`core.py:931-940` |
| RMW 平衡值 / 惯性稳定度屏障 / 外圈衰减 | `core.py:203-265` |
| Atkinson-Holliday 气压 / Holland B | `core.py:268-273` |
| Holland 廓线切向风 / R34 / 气压亏损 | `core.py:276-297` |
| MPI 公式（含 T200 修正） | `core.py:358-390` |
| 解析 SST 气候 / 副高中心 | `core.py:427-458` |
| 陆地掩码（三条优先级） | `core.py:495-582` |
| 精度档位 inner/hold/quality | `core.py:728-739` |
| 模拟状态字典（`sim`）全貌 | `core.py:799-876` |
| **环境场重建** | `core.py:1546-2024`（`_env_update_impl`） |
| 环境采样 / 引导权重 / β 漂移 | `core.py:2039-2109`、`core.py:2316-2335` |
| 副高脊线诊断 / 向极修正 | `core.py:2112-2242` |
| 三层引导采样 | `core.py:2245-2313` |
| 命名门槛 / 命名概率 / 低压编号 | `core.py:2410-2517` |
| 切变响应 / tau / ET 时长常量 | `core.py:2518-2563` |
| **强度方程（775 行）** | `core.py:2566-3336` |
| 尾流沉积（高斯 / 沿线段） | `core.py:3341-3433` |
| 藤原互旋 / 自由运动 / 登陆求交 | `core.py:3441-3473`、`core.py:3476-3523`、`core.py:3381-3416` |
| 移动步 + 冷尾流 + Ekman + ACE | `core.py:3526-3661` |
| 生成密度表 / 生成类型分类 / 生成步 | `core.py:3709-3764`、`core.py:3980-4025`、`core.py:4028-4170` |
| 半拉格朗日平流 `_advect_env` | `core.py:4282-4334`（采样器 `4175-4189`） |
| 海洋柱 / D26 / 混合层气候 / 洋流 | `core.py:4351-4531`、`core.py:4533-4594` |
| 集合预报（环境序列 / 成员 / 统计） | `core.py:4675-4750`、`core.py:4751-4856`、`core.py:4857-4938` |
| **主循环 / 分帧 / 精度落地** | `core.py:4994-5047`、`core.py:5050-5150` |
| 默认起始时间（最后有实测数据的时刻） | `core.py:5153-5205` |
| 相态阶梯 / 上限 / 逐级减弱 | `phase.py:41-119`、`phase.py:classify` |
| 时间回溯（引用快照 / 真拷贝清单） | `rewind.py:1-171`、`rewind.py:189-228`、`rewind.py:478+` |
| 地形摩擦 / 登陆衰减因子 | `terrain.py:650-684` |
| 西风槽 + 冷空气（默认 no-op） | `synoptic.py:423-468`、`synoptic.py:67-96` |
| 副高形状参数化（默认逐位一致） | `ridge.py:70-128` |
| envmodel 求解链 | `envmodel/solver.py:637-773` |
| 模态序列（实测/覆盖/AR 外推） | `envmodel/modes.py:84-207` |
| npz 载入与缓存 | `envmodel/store.py:25-81` |
| 真·生产入口 | `typhoon_sim.bat` → `main.py` → `app/ty_sim.py` → `app/ty_sim_mixins/sim_mode_mixin.py:841,1460` |
| 定向回归测试 | `tests/test_simcore_*.py`、`tests/test_phase_ladder.py`、`tests/test_sim_modes.py` |
