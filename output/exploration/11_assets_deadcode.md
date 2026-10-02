# 11 资产链与死码影响面穷举

## 一句话定位

本报告把第一轮"哪些是死资产"的结论从 `env/` 推到**整个 `simulator/simcore/`**：查清 4 份陆地/地形 npz 的生成-消费-死亡关系、`sim['land']` 的全部写点与 `dataMode` 的关系、`_gen_tables` 的 dict/NpzFile API 混用到底错在哪、以及 **387 个 simcore 函数里 59 个（15.2%）在 GUI 生产路径上不可达**。

> **实测 vs 推断**：所有"实测"都在 WSL `/usr/bin/python3`（3.14.4，numpy/scipy）或 Windows 解释器上跑出，命令与数字均给出；"推断"逐条标注。探针脚本在 `/tmp/`（=`G:\tmp\`），仓库只读。

---

## 1. 清单与职责

### 1.1 本次查清的四份资产

| 资产 | 形状 / 键 | size | mtime | 生成者 | 消费者 | 状态 |
|---|---|---|---|---|---|---|
| `simulator/simcore/land_0.5.npz` | `land` float32 **(240,720)**，nz=48543 | 691 462 B | 2026-09-17 12:16 | `core.build_land()`（`core.py:516-582`）首次多边形扫描后回写 | `build_land()` 第二优先分支（`core.py:534-542`）—— **形状守卫永不通过** | **扩域前残留，永不生效，只会被静默覆盖**（自愈实测 356 s、RMSE 0.1102，见 §3.1） |
| `simulator/env/model/land_cell.npz` | `landfrac` (360,720) f32 nz=96392 + `all_land` bool (n=80987) + meta | 51 079 B | 2026-09-21 02:31 | `tools/make_cell_land_mask.py`（OUT=`:34`） | `core.cell_land_cache()`（`:495-508`）→ `build_land()` 首选、`all_land_mask()`（`:511-513`） | **权威、活着** |
| `simulator/simcore/terrain_0.5.npz` | `elev_mean/elev_max/landfrac` (360,720) + `lat0/lon0/d/meta` | 606 216 B | 2026-09-29 23:38 | `tools/build_terrain_dem.py`（TARGET=`:58`，真 DEM） | `terrain.load()`（`terrain.py:398,413-434`） | **活着（`terrainOn` 时）** |
| `simulator/env/model/terrain_dem.npz` | 与上一行**逐键 array_equal**（实测） | 606 216 B | 2026-09-29 23:38 | 同一脚本的 OUT（`build_terrain_dem.py:57`） | **全仓无 .py 读取方** | 同一产物的第二份拷贝，**运行时死** |
| `simulator/simcore/terrain_0.5_synth.npz` | 同上，`elev` nz=**48808**（landfrac nz=96392） | 220 390 B | 2026-09-29 01:05 | `tools/build_terrain.py` → `terrain.synthesize()`（`terrain.py:339-371`） | **全仓无 .py 读取方**（只作为 `build_terrain_dem.py:59` 的 BACKUP 目标被写） | **死（备份）** |

### 1.2 本次查清的两个"没人用"的包

| 包 | 文件 / 行数 | 实现了什么 | 与 `simcore/` 的关系 |
|---|---|---|---|
| `simulator/typhoons/` | `basin.py` 403 / `fields.py` 221 / `gen.py` 377 / `kzc.py` 173 / `sim.py` 728（共 1902 行） | F7 生成层（四种生成模式+记录）、F8 逐 6h 状态机（路径/强度/气压/置换）、盆地定义、合成涡旋风场与结构诊断、**KZC+Chavas 风压模型** | **第一代原型**，被 `simcore/` 全面取代；仅 `tests/test_sim_typhoons.py` import |
| `simulator/clouds/` | `render.py` 274（+`__init__` 2） | F9 云图：IR-BD 色阶、全球背景（ITCZ/季风槽/锋面）、单台风眼壁/螺旋雨带 | 同上；`CloudRenderer` 仅被测试引用（第一轮已证，本轮复核 0 生产引用） |

### 1.3 一组"存在但被删"的资产：git HEAD 有、工作树没有（实测）

查本报告时顺手核了 git 状态，得到一个对第一轮 BLOCKER 的**重要修正**：README 指向的模块不是"文档陈旧"，而是**已被删除但仍在 HEAD 里**。

```text
$ git status --porcelain | grep '^ D'   → 29 条
$ git ls-tree -r --name-only HEAD -- simulator/wrf | wc -l   → 18
$ git cat-file -e HEAD:simulator/run.py && echo run.py 存在于 HEAD   → run.py 存在于 HEAD
```

| 被删（工作树）但在 HEAD 里 | 条目数 | 含义 |
|---|---|---|
| `simulator/wrf/**` | 18（HEAD 内）/ 14（删改条目） | `run_wrf.ps1`、`run_wrf.sh`、`setup_wsl.sh`、`fetch_era5.py`、`wrfout_to_fields.py`、`track_vortex.py`、`namelist.*`、`Vtable.ERA5`、`README.md` 等 ⇒ **"真 WRF 精算"整条管线只要 `git checkout` 就能回来** |
| `simulator/run.py` | 1 | README:101-113 的入口脚本真实存在过（HEAD 最后一改：`6b51cde` 2026-08-20） |
| `simulator/ui/*`、`simulator/render/*` | 6 | 早期 UI/视频渲染层 |
| `map/map_terrain_preview.png`、`agent.md`、`fictional_sim.bat` 等 | 4 | 杂项 |

**影响**：第一轮"`simulator/run.py` / `simulator/wrf/` 在磁盘上不存在 ⇒ README 运行段全死"这个结论**描述正确但归因需改**：它们是**未提交的删除**（`D`），不是从未存在。任何"复现 README 用法"的动作应先 `git checkout -- simulator/run.py simulator/wrf`，而不是判定文档失效。

### 1.4 穷举方法（可复现）

- 探针 `/tmp/scan_dead2.py`：`ast` 解析全仓 `.py`（跳过 `.git/__pycache__/output/assets/map`），收集 `FunctionDef/AsyncFunctionDef`（387 个非 dunder，位于 `simcore/` 17 个 `.py` 中的 15 个含定义模块），以 **Name/Attribute 引用**（不只是 Call）建边，种子 = `app/**` 全部函数定义，做可达性闭包。
- 交叉校验 `/tmp/verify_names.py`、`/tmp/check_strlit.py`、`/tmp/mk_table.py`（表格生成）、`/tmp/check_dirty.py`（会话内是否改过仓库）。

```bash
cd /mnt/g/气象/Typhoon-sim
/usr/bin/python3 /mnt/g/tmp/scan_dead2.py      # → /tmp/dead2.json + 控制台表
/usr/bin/python3 /mnt/g/tmp/check_strlit.py    # 检查 getattr/字符串派发
```

---

## 2. 数据流 / 调用链

### 2.1 陆地掩码链（实测）

```
map/heightmap.png
   └─ tools/make_cell_land_mask.py:34 ──► env/model/land_cell.npz {landfrac(360,720), all_land}
                                              │
                            core.cell_land_cache()  core.py:495-508   ← 首选，0.033 s（实测 A）
                                              │  形状校验 f.shape == (ENVNY,ENVNX)  :503
                                              ▼
                            core.build_land()       core.py:516-582
                                              │  ① cell 可用 → 直接返回（:530-533）
                                              │  ② land_0.5.npz 且形状 (360,720) → 返回（:534-542）
                                              │  ③ 否则 ne_110m_land.geojson 多边形 3×3 子采样（:543-576）
                                              │     并 np.savez 回写 (360,720)（:577-581，静默自愈）
                                              ▼
                            sim['land']  ← core.py:871（构造函数内，**全仓唯一写点**）
                                              │
              ┌───────────────────────────────┴────────────────────────────┐
              ▼                                                            ▼
  land_at(sim,...)  core.py:632-633                        wind_driven_currents()
  land_at_arr(sim,...) core.py:656-660                       core.py:4497: (sim['land']>=0.5)
  （另被 steering_layers 的 core.py:2266 与                      （洋流只作用在海洋格）
    render 的 Particles.step core.py→render.py:1206 使用）
              │
              ▼
  env_bilinear(sim['land'],...)  core.py:607-629（负经度回绕 :616）
              │
              ▼
  sample_env()  core.py:2039-2058  → env['land'] = clamp(5 点均值, 0, 1)（:2045-2052）
              │   （中心 + 半径 0.9° 的 4 个方位，共 5 次 land_at）
              ▼
  所有 tc_step / intensity_step 里的 env['land'] 判据（:2587, :2678, :2835, :2860, :2936,
  :2945, :3033, :3081, :3107, :3194, :3199, :3245, :3257, :3262, :3327, :3329, :4125, :4845）
```

### 2.2 地形链

```
map/heightmap.png ─► tools/build_terrain_dem.py ─┬─► env/model/terrain_dem.npz   （无运行时消费方）
                                                └─► simcore/terrain_0.5.npz      （terrain._FILE :398）
                                                          │  terrain.load() :413-434（形状校验 :427）
                                                          ▼
                                          terrain.elev_at / 摩擦与登陆衰减（仅 terrainOn=True）
tools/build_terrain.py ─► terrain.synthesize() :339-371 ─► simcore/terrain_0.5_synth.npz（备份，死）
```

### 2.3 生成表链（任务 B）

```
env/model/tc_clim.npz  {genesis(12,120,360), track, landfall, vmax, life, spd, lat(120), lon(360)}
        │  store.load('tc_clim')  → **dict**（store.py:25-45, :38 {k: z[k] for k in z.files}）
        ▼
core._gen_tables()  core.py:3709-3764
        ├─ :3717-3725  dens = lowpass(genesis,σ=1.5) → 归一化（**成功**）
        ├─ :3729-3732  `'lat' in z.files` → **AttributeError**（z 是 dict）→ 被 :3733-3734 吞掉
        │              ⇒ dens_lat / dens_lon **永不写入**
        ├─ :3735-3745  tc_genesis 权重 w（成功）
        └─ :3750-3763  tc_genesis_modes 模态调制（成功）
        ▼
core.gen_check()  core.py:4045-4118
        └─ :4094-4098 dens_lat 分支（**恒不执行**） / :4099-4118 兜底分支（**实际执行**）
```

---

## 3. 核心机制详解

### 3.1 `build_land()` 四级优先与"静默自愈"（任务 A，实测）

**四级优先**（`core.py:526-582`）：

| 级 | 条件 | 代价（实测） | 结果 |
|---|---|---|---|
| ① `cell_land_cache()` | `land_cell.npz` 存在且形状 =(360,720)（`:503`） | **0.0328 s** | 与 `land_cell.landfrac` **逐点 array_equal = True** |
| ② `land_0.5.npz` | 文件存在且 `arr.shape == (ENVNY,ENVNX)`（`:538`） | 读盘 | 当前文件是 (240,720) → **永不通过** |
| ③ `ne_110m_land.geojson` 多边形 | `_load_polys()` 127 个多边形，加载仅 **0.026 s**；随后 360×720×9 = 2 332 800 次子采样点测试 | **实测 356.15 s（≈5.9 分钟）** | 得到**粗分辨率** landfrac（3×3 子采样） |
| ④ 无多边形 | 返回全 0 场（`:546-547`） | — | 静默"全海洋" |

**③ 的质量代价（实测，B 组产出 vs 权威 `land_cell.landfrac`）**：

| 指标 | 实测值 |
|---|---|
| RMSE | **0.1102**（landfrac 量程 0–1） |
| max\|Δ\| | **1.0000**（存在整格判反） |
| 判为陆地的格数 | **89 668** vs 权威 **96 392** ⇒ 少 **6 724** 格（权威陆地的 **7.0%** 被判成海） |
| 在权威陆地格上，多边形版均值 | **0.8910**（系统性低估 ≈11%） |
| 在权威海洋格上，多边形版均值 | **0.0016**（少量误判） |
| 写入文件 | `/tmp/selfheal_probe.npz`，1 037 062 B，键 `land`，shape (360,720)，与返回值逐点相等 |

（对照：把多边形集合裁到前 3 个时同样流程只要 **50.121 s** ⇒ 耗时随多边形数近似线性，127 个 ≈ 356 s。）

**自愈回写**（`core.py:577-581`）：

```python
    _LAND_CACHE = land
    try:
        np.savez(_LAND_FILE, land=land)     # _LAND_FILE = simcore/land_0.5.npz  (:486)
    except Exception:
        pass
    return land
```

- 触发条件**三者同时**成立：`cell_land_cache()` 返回 (None,None)（`:530-533` 不命中）**且** `land_0.5.npz` 缺失或形状不符（`:534-542` 不命中）**且** 多边形非空。
- 行为：把**刚算出的 (360,720) 粗掩码**直接写进 `simcore/land_0.5.npz`，覆盖旧文件。
- 风险（推断 + 实测支撑）：
  1. **运行期写源码树**：`build_land()` 在 `init()` 里被调（`core.py:871`），所以只要 `land_cell.npz` 丢失，模拟一启动就会往仓库写文件；`np.savez` 不带原子替换，中途失败会留下半截文件（而 `except Exception: pass` 让它**不报错**）。
  2. **掩盖问题**：自愈成功后 `land_0.5.npz` 变成 (360,720)，下一轮 `land_0.5` 形状检查通过；此时"权威资产 `land_cell.npz` 丢了"这件事在界面/日志上**没有任何痕迹**（`:506-507` 只 `logger.debug`）。
  3. **质量降级被固化**：自愈写入的是多边形 3×3 子采样结果，而 `land_cell.npz` 是 15×15 像素统计（`core.py:487-489` 注释）。实测两者差异见 §3.3 的对照。
  4. **实测证据**：把 `_LAND_FILE` 重定向到 `/tmp` 后，A 组 **0.0328 s** 返回且逐点相等；B 组进入多边形扫描，**356.15 s** 后写出一份 (360,720) 的新 npz（1 037 062 B），与权威掩码 RMSE 0.1102、7.0% 的陆地格判反。

### 3.2 `sim['land']` 的全部写点，以及 `dataMode=True` 会不会覆盖它（闭合 U9）

**写点穷举（实测）**：全仓对 sim 字典的 `land` 赋值只有 **1 处**：

- `core.py:871`：`'land': build_land(),` —— 位于 `init()` 构造的大字典里。

其余出现 `'land'` 的地方都不是写 `sim['land']`：

| 位置 | 语义 |
|---|---|
| `core.py:2052` | `sample_env()` **返回的临时字典**里的标量 `env['land']`（2.6 节的 5 点均值），不是网格 |
| `simcore/phase.py:598,620` | 写**记录行** dict（`rec['land']`），与 sim 无关 |
| `tools/verify_lat_scale2.py:34` | 测试用假 sim：`{'sst':..., 'land': np.zeros(...)}` |
| `tools/sim_obs_probe.py:60` | 读 `sim['land']` 快照 |

**`env_update` 是否覆盖**：`_env_update_impl`（`core.py:1546-2022`）**整段 477 行里没有任何一行含 "land"**（实测：逐行扫描该区间，0 命中）。并且 `sim['env']` 的键表（`core.py:856-870`）里**没有** `land` 键。因此：

- `dataMode=False` 与 `dataMode=True` **都不会**改写 `sim['land']`；U9 的答案是**否**。
- 两套 `land` 是**不同对象、不同语义**：`sim['land']` 是 (360,720) 栅格（`core.py:871`），`env['land']` 是逐台风采样出的 0..1 标量（`core.py:2039-2058`）。所有物理判据用的是后者；前者只被 `land_at`/`land_at_arr`/`ocean_step` 读。
- 顺带闭合 U9 的隐含担忧：`dataMode` 只影响 `sim['env']` 里的 12 个环境场数组，**不碰掩码**。

### 3.3 地形三份资产的关系，以及一个与既有文档相反的实测结果（任务 A）

**关系（实测）**：
- `simulator/simcore/terrain_0.5.npz` 与 `simulator/env/model/terrain_dem.npz` **7 个键全部 `np.array_equal == True`**（含 `meta` 字符串），mtime 完全相同（2026-09-29 23:38）⇒ 同一脚本 `tools/build_terrain_dem.py` 写了两份：`OUT=:57`（env/model）、`TARGET=:58`（simcore）。运行时只读 simcore 那份（`terrain.py:398`），**env/model 那份没有读取方**。
- `terrain_0.5_synth.npz` 的 `landfrac` 与前两者 array_equal（同一掩码），但 `elev_mean/elev_max` 非零元只有 **48 808** 个格，而 landfrac 非零格有 **96 392** 个——即合成版只在约一半陆地格上有高程（51 个程序化 feature）。无读取方。

**与 `output/sim/_reference/VERIFY_TERRAIN_FIX.md` P2 相反的实测**（该文档称"`elev_mean` 在 `lf==0` 的海洋格上取值 −8837…−2"）：**当前磁盘上的数据已不成立**。实测：

```text
(exact mask match) (elev_mean != 0) == (landfrac > 0)   → True
elev_mean[landfrac == 0].max() = 0.0
elev_mean[landfrac > 0].min()  = 1.975e-05
elev_mean 全域 min = 0.0, max = 5725.63
elev_max[landfrac == 0].max()  = 0.0
land_cell.landfrac == terrain.landfrac                → True
```

⇒ `terrain.py:133-140` 声明的两条不变量（"纯海洋格海拔恒为 0"、"`elev_mean = raw_mean · landfrac`"）**在当前文件上成立**。`output/sim/_reference/VERIFY_TERRAIN_FIX.md` 的 P2 已**落后于数据**（数据 mtime 2026-09-29 23:38），其中"建议在 docstring 顶部加一句说明来源"这条也**已经落实**（`terrain.py:11-18` 有 2026-09-30 的更正段）。**这是一条对第一轮结论的更正**，建议 SUMMARY 引用数据而不是那份 reference 文档。

### 3.4 `_gen_tables` 的 dict/NpzFile 混用：三层实测结论（任务 B）

**① API 混用已复现**（`/tmp/exp_gen_tables.py`）：

```text
store.load("tc_clim") type = dict | keys = ['genesis','landfall','lat','life','lon','spd','track','vmax']
z.files  -> AttributeError: 'dict' object has no attribute 'files'
_GEN_CACHE keys = ['dens','modes','ok','w']     ← 没有 dens_lat / dens_lon
dens shape = (12, 120, 360) | w keys = ['lat','rh','shear','sst'] | modes loaded True
```

⇒ 第一轮 MAJOR-3 成立。同时**证实 `core.py:4107` 的注释是错的**：它写"`tc_clim` 里**没有** `lat` 成员（实测 `'dens_lat' in C` 为 False）"——`'dens_lat' in C` 为 False 是**结果**不是原因；`tc_clim.npz` **确实有** `lat`(120) 与 `lon`(360)（实测 keys 表）。根因是 dict 上取 `.files`。

**② 兜底公式的偏差到底是不是 0.000 —— 换 seed/月份复测 + 逐点代数对照**：

两组独立测量，结论一致：

- **逐点对照**（120 行 × 360 列全比）：真轴 `tc_clim['lat']` = −59.5…+59.5，step = 1.000000；兜底 `(jy+0.5) − 120/2` = −59.5…+59.5，step = 1.000000 ⇒ **`max|Δlat| = max|Δlon| = 0.000000000000`（12 位小数全 0）**。
- **A/B 抽样对照**（6 个 seed × 12 个月 × 14 次尝试 = 1008 次抽样，同一密度场、同一串随机数，只切换纬度分支）：A（dens_lat 分支）与 B（兜底分支）抽出的纬度**集合完全相等**（63 个不同纬度），`max|Δ| = 0.000000000000`。

⇒ **在这个特定网格（120 行、1°、赤道居中）上偏差是精确 0，不是"约 0"**。代数上也可证：真轴 = `jy − 59.5`，兜底 = `jy + 0.5 − 60` = `jy − 59.5`，恒等。

**③ 一旦 dens 栅格变化会怎样 —— 静默给错纬度（实测）**：把同一个密度场按最近邻重采样到 4 种候选栅格，各做 1008 次抽样，比较兜底公式与该栅格真值的差：

| dens 栅格假设 | max\|Δ\| | mean\|Δ\| | 结论 |
|---|---|---|---|
| 121 行 ±60（1°） | 0.000° | 0.000° | 恰好也对 |
| 120 行 ±60（step 120/119） | 0.349° | 0.126° | 小偏差 |
| **240 行 ±60（0.5°）** | **41.077°** | **14.959°** | **灾难性错位**（把 10N 当 60S 附近） |
| **240 行 ±90（0.5°）** | **20.366°** | **7.417°** | **灾难性错位** |

**④ 那个守卫是坏的**（`core.py:4113-4114`）：

```python
if abs((DOM_LAT1 - DOM_LAT0) - float(d.shape[0])) > 0.5:
    logger.debug('密度场行数 %d 与假设的 1 度栅格不一致', d.shape[0])
```

实测 `|(DOM_LAT1−DOM_LAT0) − 120| = |180 − 120| = 60 > 0.5` ⇒ **在完全正确的情形下守卫也恒为真**（它拿环境域跨度 ±90=180 去比密度场行数 120，而不是密度场自己的跨度 ±60=120）。而它只打 `logger.debug`，默认日志级别下**不可见**。若换成 240 行，`|180−240| = 60` 同样为真——守卫对"对"和"错"给出同一反应，等于没有。

**⑤ 这个 `except Exception: pass` 还吞掉了什么**（`_gen_tables` 共 3 个 try 块，逐点列举）：

| 块 | 行 | 可失败的调用 | 被吞后的后果 |
|---|---|---|---|
| 1 | `3714-3734` | `from ... import store`（ImportError）；`_st.load('tc_clim')`；`np.asarray(z['genesis'])`（`'genesis' in z` 已守卫）；`_lowpass(g[m],1.5)`（scipy）；`np.stack([...])`（形状不一致）；`'lat' in z.files`（**AttributeError，当前实际失败点**）；`np.asarray(z['lat'])` | 若在 `:3722-3725` 失败 → `dens=None` → `gen_check` 掉进 `:4119-4123` 的 `GEN_ZONES` **均匀分布**分支，生成地理分布**完全不同**（不再聚集暖池东南侧/EPAC 10-15N），且无任何提示。当前实际失败点只损 dens_lat/dens_lon。 |
| 2 | `3735-3745` | `_st2.load('tc_genesis')`；`np.asarray(z2[ck])`（`ck in z2` 已守卫） | `w={}` → `_gi_weight` 走内置兜底权重曲线 |
| 3 | `3750-3763` | `_st3.load('tc_genesis_modes')`；`str(x) for x in z3['modes']`；`np.asarray(z3[k])` | 无 `modes` → 生成密度**失去 ENSO 年际调制**（厄尔尼诺时西太减少/中东太增多这条机制失效） |

另有一条**缓存语义**风险（实测）：`:3713` 在三个 try **之前**就写了 `_GEN_CACHE['ok']=True`，所以加载失败会被**永久缓存**在本进程内、不会重试；第一轮 `rewind.py:93` 也提到 `_GEN_CACHE` 不受回退影响。

### 3.5 死码边界穷举（任务 C）

**口径**：种子 = `app/**` 的全部函数定义（GUI 是唯一真入口 `python main.py`）；边 = 定义体内对任意 Name/Attribute 的**引用**（Call 或读取），按名字解析（跨模块、跨类，**过近似**）。过近似的可达集 ⇒ 死码集**偏保守**（可能漏报，不会冤枉）。

**总量**：

| 模块 | 定义数 | 生产不可达 | 占比 |
|---|---|---|---|
| `simcore/core.py` | 121 | 10 | 8% |
| `simcore/render.py` | 51 | 7 | 14% |
| `simcore/rewind.py` | 42 | 12 | 29% |
| `simcore/terrain.py` | 31 | 12 | 39% |
| `simcore/envmodel/solver.py` | 22 | 1 | 5% |
| `simcore/climate.py` | 20 | 0 | 0% |
| `simcore/synoptic.py` | 19 | 2 | 11% |
| `simcore/naming.py` | 17 | 1 | 6% |
| `simcore/phase.py` | 14 | 5 | 36% |
| `simcore/daily.py` | 13 | 1 | 8% |
| `simcore/envmodel/modes.py` | 8 | 1 | 12% |
| `simcore/fill.py` | 8 | 1 | 12% |
| `simcore/import_tc.py` | 8 | 4 | **50%** |
| `simcore/ridge.py` | 8 | 1 | 12% |
| `simcore/envmodel/store.py` | 5 | 1 | 20% |
| **合计** | **387** | **59** | **15.2%** |

**分层定义（实测计数，见 `/tmp/count_tiers.py`）**：

- `T0`（25 个，代码 271 行）= 除自身定义处外**全仓无引用**（注释、`__all__`、测试都没有），其中
  - `T0a`（**19 个**）= 连自身文件内部也零引用：`tc_deficit`、`doy_of`、`_env_to_cld`、`tc_cloud_top`、`radar_refl`、`available_days`、`fill_to_ocean`、`list_candidates`、`peek`、`cat_of_kt`、`cat_zh_kt`、`draw_tracks`、`draw_grid`、`snapshot_state`、`estimate_snapshot_bytes`、`set_state_callback`、`path_samples`、`mean_along`、`peak_along_series`；
  - `T0b`（6 个）= 只被**自身文件内**同样不可达的兄弟函数引用：`_t_of`、`_fields_of`、`_build_lut`、`_build_step_lut`、`_seg_samples`、`elev_profile`。
- `T1`（34 个，代码 480 行）= 有引用，但引用者全部是 tests / tools / `__all__` 导出 / 同样不可达的兄弟函数。

**完整清单（59 条）**：

| 定义 | 名字 | 行数 | 层 | 引用来源（实测计数） |
|---|---|---|---|---|
| `core.py:276` | `tangent_v` | 8 | T1 | simulator(self)×1, tests×2 |
| `core.py:292` | `tc_deficit` | 6 | T0 | — |
| `core.py:300` | `vortex_wind` | 54 | T1 | simulator(self)×1, tests×2 |
| `core.py:795` | `doy_of` | 2 | T0 | — |
| `core.py:1134` | `_perf_drop_caches` | 9 | T1 | — |
| `core.py:4190` | `_env_to_cld` | 6 | T0 | — |
| `core.py:4602` | `wind_at` | 20 | T1 | tests×10 |
| `core.py:4652` | `cld_sample` | 14 | T1 | tests×2 |
| `core.py:4939` | `tc_cloud_top` | 26 | T0 | — |
| `core.py:4969` | `radar_refl` | 21 | T0 | — |
| `daily.py:423` | `available_days` | 11 | T0 | — |
| `envmodel/modes.py:38` | `clear_cache` | 11 | T1 | simulator×4, tests×28 |
| `envmodel/solver.py:58` | `clear_cache` | 44 | T1 | simulator(self)×4, tests×28 |
| `envmodel/store.py:77` | `clear_cache` | 5 | T1 | simulator×4, tests×28 |
| `fill.py:83` | `fill_to_ocean` | 4 | T0 | — |
| `import_tc.py:63` | `parse_bdeck` | 47 | T1 | tests×14, tools×15 |
| `import_tc.py:112` | `summarize` | 13 | T1 | simulator×1, tests×2 |
| `import_tc.py:127` | `make_from_track` | 26 | T1 | tests×2, tools×2 |
| `import_tc.py:177` | `list_candidates` | 10 | T0 | — |
| `naming.py:400` | `peek` | 20 | T0 | — |
| `phase.py:182` | `max_kt_for` | 3 | T1 | tests×8 |
| `phase.py:578` | `_t_of` | 7 | T0 | simulator(self)×1 |
| `phase.py:587` | `_fields_of` | 36 | T0 | simulator(self)×1 |
| `phase.py:625` | `classify_series` | 25 | T1 | simulator(self)×1, tests×8 |
| `phase.py:652` | `phase_spans` | 57 | T1 | tests×20 |
| `render.py:24` | `_build_lut` | 22 | T0 | simulator(self)×4 |
| `render.py:111` | `cat_of_kt` | 2 | T0 | — |
| `render.py:115` | `cat_zh_kt` | 13 | T0 | — |
| `render.py:406` | `_build_step_lut` | 5 | T0 | simulator(self)×2 |
| `render.py:413` | `_hex` | 2 | T1 | simulator(self)×2, tools×2 |
| `render.py:1285` | `draw_tracks` | 9 | T0 | — |
| `render.py:1446` | `draw_grid` | 7 | T0 | — |
| `rewind.py:258` | `format_bytes` | 8 | T1 | tests×10 |
| `rewind.py:387` | `state_bytes` | 8 | T1 | tests×1 |
| `rewind.py:397` | `snapshot_state` | 3 | T0 | — |
| `rewind.py:411` | `estimate_snapshot_bytes` | 3 | T0 | — |
| `rewind.py:538` | `dropped` | 3 | T1 | tests×1 |
| `rewind.py:543` | `footprint_bytes` | 3 | T1 | tests×6 |
| `rewind.py:557` | `snapshots` | 3 | T1 | tests×6 |
| `rewind.py:755` | `can_rewind` | 3 | T1 | tests×8 |
| `rewind.py:759` | `t_at` | 3 | T1 | tests×8 |
| `rewind.py:763` | `index_at_t` | 12 | T1 | tests×6 |
| `rewind.py:776` | `rewind_hours` | 5 | T1 | tests×2 |
| `rewind.py:946` | `set_state_callback` | 3 | T0 | — |
| `ridge.py:262` | `ridge_anomaly` | 15 | T1 | tests×8 |
| `synoptic.py:248` | `trough_streamfunction` | 7 | T1 | tests×14 |
| `synoptic.py:380` | `cold_index` | 14 | T1 | tests×20 |
| `terrain.py:185` | `lon_of_col` | 3 | T1 | tests×2, tools×2 |
| `terrain.py:190` | `lat_of_row` | 3 | T1 | tests×6, tools×2 |
| `terrain.py:385` | `describe_features` | 9 | T1 | tools×4 |
| `terrain.py:407` | `reset_cache` | 4 | T1 | tools×6 |
| `terrain.py:550` | `_seg_samples` | 15 | T0 | simulator(self)×3 |
| `terrain.py:567` | `path_samples` | 23 | T0 | — |
| `terrain.py:592` | `elev_profile` | 6 | T0 | simulator(self)×3 |
| `terrain.py:600` | `peak_along` | 9 | T1 | tests×4 |
| `terrain.py:611` | `mean_along` | 5 | T0 | — |
| `terrain.py:618` | `peak_along_series` | 6 | T0 | — |
| `terrain.py:626` | `peak_along_track` | 22 | T1 | — |
| `terrain.py:660` | `friction_scale` | 8 | T1 | tests×6, tools×2 |

**误报排查（实测）**：

1. **动态派发**：把 57 个死码名字拿去搜**字符串字面量**，19 个命中，全部是各自模块的 `__all__`（`phase.py:54`、`rewind.py:154`、`terrain.py:165-166`、`ridge.py:64`）或测试里的按名导入 —— **没有任何 `getattr(mod, '名字')` 式派发**。⇒ T0 清单可以直接当"可删"用。
2. **名字过近似导致的假可达**（已手工复核并修正）：`Particles.step`（`render.py:1194`）这种通用名会被 app 里的局部变量 `step` 误连边，从而把 `wind_at_arr` → `vortex_wind_arr` 标成可达。**手工沿真实调用链核对后结论不变**：`app/ty_sim_mixins/sim_mode_mixin.py:1864: R.render_map(...)` → `render_map`（`render.py:1455`）→ `draw_streamlines`/`draw_currents`/`draw_barbs` → `wind_at_arr`（`core.py:4624`）→ `vortex_wind_arr`（`core.py:674`）；`sim_mode_mixin.py:1634: self.sim_particles.step(...)` 是第二条通路。
3. 由此得到一条对第一轮的**更正**：`vortex_wind_arr` **不是死码**（它在活路径上）；死的只有标量版 `vortex_wind`（唯一调用点 `core.py:4618` 在 `wind_at` 内，而 `wind_at` 只被测试调用）。第一轮 §6 MAJOR-6 说"`vortex_wind` 与 `vortex_wind_arr` 在生产代码里零调用"——`vortex_wind` 部分不准确（`core.py:4618` 有调用点，只是调用者本身是死的），`vortex_wind_arr` 结论相反。

**三条已知死码的复核**：`tc_deficit`（T0，零引用）✅；`cloud_step`（T1：唯一生产调用点已移除，仅 tests×4 + `sim_mode_mixin.py:1630` 一句注释）✅；`vortex_wind`（T1，见上）⚠️ 部分更正。

**新发现的高价值死码候选**（`T0a`，全仓零引用，可直接删）：

- `core.tc_cloud_top`（26 行）、`core.radar_refl`（21 行）、`core._env_to_cld`（6 行）—— 云/雷达诊断三条一次都没被调用过；结合 `cloud_step` 已死，说明**整个云物理分支是死的**（渲染用的是 `clouds/render.py` 那套静态合成，而它本身也没生产调用方——见 §3.6）。
- `render.cat_of_kt` / `render.cat_zh_kt`（风级→中文名，15 行合计）—— 分类显示全靠别的实现。
- `render.draw_tracks` / `render.draw_grid`（9+7 行）。
- `fill.fill_to_ocean`（4 行）—— `fill.py` 唯一的公开函数之一。
- `import_tc.list_candidates`、`naming.peek`、`daily.available_days`、`rewind.snapshot_state`/`estimate_snapshot_bytes`/`set_state_callback`、`terrain.path_samples`/`mean_along`/`peak_along_series`。
- `ridge.ridge_anomaly`（T1，仅 tests）—— 它是 `core.py:1548-1554` 副高循环的**预备替换件**（`ridge.py:267-274` 的 docstring 明写"可以直接替换 core.py:1548-1554 循环体内的那一行"），但从未接线；**核心还在用原循环**，所以这条"重构"是半成品。
- `synoptic.trough_streamfunction` / `cold_index`（仅 tests×14/×20）—— 两个诊断函数只活在测试里。

### 3.6 `typhoons/` 与 `clouds/`：第一代原型，以及与 `simcore/` 的**重复实现**（任务 D）

**生产零引用（本轮复核）**：`grep -rn "simulator\.typhoons|simulator\.clouds|typhoons\."` 在 `app/`、`tools/` 下 **0 命中**；唯一引用方是 `tests/test_sim_typhoons.py`（14 passed）。文档里的阶段名（F5/F7/F8/F9、"阶段2 get_field"）与 `typhoons/*.py` docstring 一一对应 ⇒ 这是**比 simcore 早一代的 F7–F9 实现**。

**它们确实跑过（实测痕迹）**：
- `simulator/output/records.json`（mtime 2026-08-13 00:14）的 schema 与 `typhoons/gen.py:5` 完全一致：`{records, rejections}` + 每条 `{id, basin, t0, la0, lo0, w0, p0, nature, mode, reason}`；写出者是 `TyphoonGenerator.save()`（`gen.py:323-329`）。⇒ **第一轮 unresolved #7（records.json 谁生成）就此闭合**。注意当前默认路径是 `GEN_DIR/'output'` = `simulator/typhoons/output/records.json`（`gen.py:18,324`），而该目录**不存在** ⇒ 当年是用显式 `path=` 写成 `simulator/output/records.json` 的（推断）。
- `simulator/logs/gen_1.log`、`gen_42.log` 的格式与 `gen.py:331-348` 的 `save_log()` 完全一致。**但其 mtime 是 2026-10-02 01:40（本轮会话期间）**，很可能是并行任务（08 号 `sim-run` 的端到端长跑）刚写下的，**不能当作历史证据**——故仅列为"内核 B 的生成层今天仍可被跑通"的旁证。
- `env/generated/sst_1993.npz` 等 17 个 1993 年场（mtime 2026-10-01 03:04）与 `gen_*.log` 里的 1993 年记录同时代，说明这套原型当年用 1993 年做演示年。

**与 `simcore/` 的重复实现清单**：

| 能力 | 原型（死） | 现役（活） | 是否两套独立实现 |
|---|---|---|---|
| 风压关系 | `typhoons/kzc.py:36-124`（KZC + Chavas） | `core.pmin_from_v:268-269` + `holland_b:272-273` | **是，且已发散**（见下） |
| 逐 6h 状态机 | `typhoons/sim.py`（728 行，输出标准 `.dat`） | `core.sim_step`（`_sim_step_core`） | 是 |
| 生成层 | `typhoons/gen.py`（四种模式 + 环境允许性） | `core.gen_check`（`core.py:4045-4168`） | 是 |
| 环境场 | `typhoons/fields.py`（10°×10° 窗口合成风场+暖心/锋面诊断） | `core._env_update_impl`（`core.py:1546-2022`） | 是 |
| 盆地定义 | `typhoons/basin.py:1-403`（`XROOT = r'G:\气象\数据\xrq'`，**仓库外硬编码路径**） | `core.GEN_ZONES` | 是 |
| 云图 | `clouds/render.py:106-274` | `core.cloud_step`（**自己也是死码**）+ 渲染层 | 两套都死 |

**kzc vs pmin_from_v：两套独立实现，实测已漂到 31–33 hPa**（`/tmp/exp_kzc.py`）：

- 实现 1（现役，`core.py:268-269`）：`p = 1010 − 0.59·(v_kt·0.514444)^1.13` —— **只吃 Vmax**，无移速/R34/纬度/OCI。
- 实现 2（原型，`kzc.py:79-82,109-124`）：`p = clamp(penv_shift + OCI + dp(V,移速,R34,lat), 870, 1022)`，`dp` 分低纬/高纬两支（`kzc.py:61-66`），另有 Chavas 2025 Eq.5（`:91-106`）与"标定幂律"分支（`:45-52`）。

实测对照（r34=100 nm、OCI=1010、移速=10 kt、lat=15）：

| vmax(kt) | core | kzc | kzc−core |
|---|---|---|---|
| 20 | 1001.78 | 1010.31 | +8.53 |
| 50 | 986.85 | 997.88 | +11.03 |
| 100 | 959.34 | 962.90 | +3.56 |
| 110 | 953.58 | 954.48 | +0.90 |
| 120 | 947.75 | 945.63 | −2.12 |
| 140 | 935.91 | 926.68 | −9.23 |
| 160 | 923.84 | 905.99 | −17.85 |
| 185 | 908.48 | 877.48 | **−31.00** |

`|kzc−core|`：**均值 10.66 hPa，最大 31.00 hPa**（OCI=1008 时最大 **33.00**）。两套在 **≈110 kt** 交叉，低端 core 偏低、高端 core 偏高。

**会不会漂？会，而且有两个独立的漂移源（实测）**：

1. **输入维度不同**：同一 `vmax=140 kt`，`core` 恒为 935.91 hPa；而 `kzc` 随 (lat, R34) 在 **917.91 … 930.55** 之间变化（跨度 12.6 hPa）；`vmax=100` 时跨度 11.7 hPa。只要有人把 kzc 接进内核或用它做标定对照，同一风速就会给出不同气压。
2. **进程级可变全局量**：`kzc._KZC` / `_KZC_SOURCE` 是模块全局（`kzc.py:20-26`），`calibrate()`（`:127-170`）会原地改写它们。实测：喂 40 个同点样本就能"标定成功"（`source='calibrated'`, `rmse=7e-15`, α=7.937, β=0.433），此后同一输入 `min_pressure(120,100,1010)` 从 **945.63 → 1022.0**（撞上限），需 `_reset_literature()` 才复原。而 `calibrate()` 在**全仓无调用方**（实测 grep：只有定义处）⇒ 现在不会漂，但**退化样本（单点 40 次）也能通过标定**这一点没有守卫。

### 3.7 本节的方法学限制（显式声明）

- 可达性按**函数名**解析、跨类跨模块，属**过近似** ⇒ 死码集偏保守；反过来，`getattr` 字符串派发会被漏掉（已用字符串字面量扫描排查，0 命中）。
- `__all__` 导出**不算**可达（导出 ≠ 使用）；上表已把"仅 `__all__`"归入 T0/T1 并在引用列注明。
- `tools/**` 与 `tests/**` **不算生产根**。若把 `tools/` 也算根，T1 里 `parse_bdeck`、`lon_of_col`、`reset_cache`、`describe_features`、`friction_scale`、`peak_along` 等会变"可达"，但它们只在**离线标定脚本**里活，不影响 GUI 结论。

---

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `simulator/simcore/land_0.5.npz` | npz `land` (240,720) f32 | 691 KB | `core.build_land()`（`core.py:578-581`） | 自身的第二优先分支（形状永不匹配） |
| `simulator/env/model/land_cell.npz` | npz `landfrac`+`all_land`+meta | 51 KB | `tools/make_cell_land_mask.py:34` | `core.py:495-508`（首选）、`:511-513`、`daily.py:31`、`render.py:455`、`terrain.py:23` |
| `simulator/simcore/terrain_0.5.npz` | npz 7 键 | 606 KB | `tools/build_terrain_dem.py:58` | `terrain.py:398,413-434` |
| `simulator/env/model/terrain_dem.npz` | 同上（逐键相等） | 606 KB | 同脚本 `:57` | **无** |
| `simulator/simcore/terrain_0.5_synth.npz` | npz（程序化合成） | 220 KB | `tools/build_terrain.py` → `terrain.synthesize():339-371` | **无**（仅作 `build_terrain_dem.py:59` 的 BACKUP） |
| `simulator/simcore/ne_110m_land.geojson` | GeoJSON 127 多边形 | 0.14 MB | Natural Earth | `core._load_polys():463-482`（兜底） |
| `simulator/typhoons/*.py` | 1902 行 | 0.09 MB | 第一代 F7–F9 | 仅 `tests/test_sim_typhoons.py` |
| `simulator/clouds/render.py` | 274 行 | 0.013 MB | 第一代 F9 | 仅 `tests/test_sim_typhoons.py:172,186` |
| `simulator/output/records.json` | JSON `{records, rejections}` | 27.6 KB | `typhoons/gen.py:323-329`（第一轮 U7 闭合） | 人工/验收；**GUI 不读** |
| `simulator/logs/gen_{1,42}.log` | 文本 | 165 B / 8.3 KB | `typhoons/gen.py:331-348` | 人工；mtime 2026-10-02 01:40（疑为本轮 08 号长跑） |

---

## 5. 如何运行 / 如何验证

**本报告所有实测的复现命令**（只读；探针在仓库外）：

```bash
cd /mnt/g/气象/Typhoon-sim

# 任务 A：build_land 自愈 A/B（纯读；自愈写盘已被重定向到 /tmp）
/usr/bin/python3 -u /mnt/g/tmp/exp_land2.py > /tmp/land2_out.txt 2>&1 &

# 任务 B：dict/NpzFile 混用 + 兜底纬度偏差 A/B + 栅格敏感性
/usr/bin/python3 /mnt/g/tmp/exp_gen_tables.py

# 任务 C：AST 可达性死码扫描（输出 /tmp/dead2.json）
/usr/bin/python3 /mnt/g/tmp/scan_dead2.py
/usr/bin/python3 /mnt/g/tmp/check_strlit.py        # getattr/字符串派发排查
/usr/bin/python3 /mnt/g/tmp/mk_table.py            # 生成 markdown 表

# 任务 D：两套风压实现对照
/usr/bin/python3 /mnt/g/tmp/exp_kzc.py

# 卫生检查：确认本轮没有改动仓库（mtime 全部早于开工时间）+ 数清"被删但未提交"的资产
/usr/bin/python3 /mnt/g/tmp/check_dirty.py
git status --porcelain | grep -c '^ D'
git ls-tree -r --name-only HEAD -- simulator/wrf | wc -l
```

**实测关键数字**（本节结论的证据）：

| 实验 | 结果 |
|---|---|
| A 组 `build_land()`（cell 可用） | **0.0328 s**，与 `land_cell.landfrac` 逐点相等 |
| `_load_polys()` | 127 个多边形，**0.026 s** |
| B 组 `build_land()`（cell 缺失 + 旧 240 行资产） | **356.15 s**（≈5.9 min），写出 1 037 062 B / (360,720) 的新 npz；与权威掩码 RMSE 0.1102、max\|Δ\|=1.0、陆地格 89 668 vs 96 392 |
| 对照组 C：多边形集合裁到 3 个 | **50.121 s** ⇒ 耗时随多边形数近似线性 |
| `store.load('tc_clim')` | 返回 **dict**；`z.files` → `AttributeError` |
| `_gen_tables()` 后的 `_GEN_CACHE` | `['dens','modes','ok','w']`（**无 dens_lat/dens_lon**） |
| 兜底 vs 真纬轴 | 逐点 `max\|Δ\| = 0.000000000000`（120 行 × 360 列）；1008 次抽样集合完全一致 |
| 栅格改 240 行 ±60 | 兜底静默错 **max 41.08° / mean 14.96°** |
| 死码 | **59 / 387 = 15.2%** 生产不可达；其中 T0 零引用 24 个 |
| `kzc` vs `pmin_from_v` | 均值差 10.66 hPa，**最大 31.00 hPa**（vmax=185kt） |
| `kzc.calibrate()` 后同输入 | 945.63 → **1022.0** hPa（进程级全局量被改写） |

**"能否跑测试"**：本轮不新增 pytest 运行（08 号任务占 CPU）；死码结论不依赖测试，全部由静态 + 数值探针支撑。第一轮已验证 `test_sim_env/test_sim_climate/test_daily_instant` 40 passed。

---

## 6. 观察到的问题 / 风险 / 不一致

**MAJOR-1｜`build_land()` 的静默自愈会往源码树写文件，并掩盖权威资产丢失。**
现象：`land_cell.npz` 一旦丢失/损坏，`build_land()` 会跑**5.9 分钟**（实测 **356.15 s**）的多边形扫描，然后把粗掩码写进 `simcore/land_0.5.npz`（`core.py:577-581`），失败也 `pass`。
证据：`core.py:530-533,534-542,543-576,577-581`；实测 A **0.0328 s** vs B **356.15 s**（**10 858×**）；自愈产出与权威掩码 RMSE **0.1102**、7.0% 陆地格判反（§3.1）。
影响：① 启动卡死数分钟却无任何用户可见提示（`np.savez` 成功/失败都不报）；② 写文件到 git 工作树（基线本就不干净，会被误认为"代码改动"）；③ 把"资产丢了"变成"本地静默改了资产"，排查成本转移给下一次 diff。

**MAJOR-2｜`land_0.5.npz` 已 8 个多月没被任何代码采纳，却仍被当作有效缓存写回。**
现象：文件是 (240,720)（扩域到 ±90 之前的形状），而 `build_land()` 要求 (360,720)（`core.py:538`）⇒ 第二优先分支**永远不生效**。
证据：实测 `land` shape (240,720)、nz=48543、mtime 2026-09-17 12:16；第一轮的"2025-09-17"是笔误（mtime 实测为 **2026**-09-17）。
影响：这是"扩域漏改资产"的化石；只要 `land_cell.npz` 在场就无害，但它让①的自愈后果（粗掩码）更容易被误当成"正常缓存"。

**MAJOR-3｜`_gen_tables` 的 dict/NpzFile 混用使 `dens_lat`/`dens_lon` 永不载入，且守卫是坏的。**
证据：实测 `z.files` → `AttributeError`；`_GEN_CACHE` 缺两键；`core.py:4113` 的守卫用环境域跨度（180）比密度场行数（120），**恒真**且只 `logger.debug`；栅格换 240 行时兜底静默错 **41.08°**。
影响：今天数值无害（偏差精确 0），但"密度场换代（0.5°/全 ±90）"这个**很可能会发生的改动**会静默把生成点搬到高纬冷海；同期 `core.py:4107` 的注释把根因写成"`tc_clim` 没有 `lat` 成员"，会误导后续修复方向。

**MAJOR-4｜`except Exception: pass` 在 `_gen_tables` 里掩盖三类**不同**故障。**
证据：`core.py:3733-3734`、`:3744-3745`、`:3762-3763`；失败点清单见 §3.4⑤。
影响：`dens` 加载失败会静默退化成 `GEN_ZONES` 均匀分布，模态调制失败会静默失去 ENSO 年际信号——两者都会**改变气候统计**而日志无痕；且 `:3713` 先把 `ok=True` 写死，失败**不可重试**。

**MAJOR-5｜两套风压实现已发散到 31–33 hPa，且第二套带进程级可变全局量。**
证据：`core.py:268-269` vs `kzc.py:36-124`；实测对照表 §3.6；`kzc.calibrate` 无调用方但能改写 `_KZC`，实测同输入 945.63→1022.0。
影响：任何用 `kzc` 做标定/对照的结论**不能**直接搬到内核（反之亦然）；`kzc` 一旦被接线，同一进程内"是否跑过 calibrate"会改变全世界的气压。

**MINOR-1｜"首次 ~28 s"的注释与实测差一个数量级。**
证据：`core.py:517` 的 docstring 写"首次 ~28s"；实测 B 组 **356.15 s**（127 多边形、2 332 800 次子采样）⇒ **12.7×**。
影响：任何"缺 `land_cell.npz` 也没关系，就慢 28 秒"的判断都错了；`tools/probe_*` 之类脚本若走到这条路径会看起来像挂死。

**MINOR-2｜`env/model/terrain_dem.npz` 与 `terrain_0.5_synth.npz` 是两份没有读取方的地形资产。**
证据：`array_equal` 证实前者是 `terrain_0.5.npz` 的逐键复制；grep 全仓无 `.py` 读取后者；`terrain.py:16` 自己写"不再被本模块消费"。
影响：约 0.83 MB 死重。**更正**：`terrain.py` 的 docstring **已经改对**（`:11-18` 是 2026-09-30 的更正段，明说现行是真 DEM、合成版只作备份），但 `:120-131` 的 `FEATURES` 数值表与"合成在 0.5° 格内做 sub×sub 细采样"仍留在文件里且没有"仅用于 `terrain_0.5_synth.npz`"的就地标注——读者仍需读到 `:11-18` 才知道该表不是现行数据来源。

**MINOR-3｜`terrain.py` 有 7 个零引用/仅测试的诊断函数（含整个"沿路径采样"族）。**
证据：`path_samples`（23 行）、`mean_along`、`peak_along_series`、`peak_along_track`（22 行）、`_seg_samples`、`elev_profile`、`peak_along`；`terrain.py:165-166` 的 `__all__` 仍在导出它们。
影响：`terrain.py` 12/31 = 39% 的函数不在生产路径上，模块体积与真实用途不符。

**MINOR-4｜`import_tc.py` 的 4/8 函数生产不可达，其中 `parse_bdeck`（47 行）与 app 自带的 b-deck 解析重复。**
证据：`parse_bdeck` 引用仅在 tests×14 + tools×15；app 侧走 `app/data_repo.py:81 convert_jtwc_to_simple_bdeck`（自己的解析实现）。
影响：b-deck 方言修复要改两处；`tests/test_bdeck_format.py:393-447` 只覆盖 `parse_bdeck` 那一份。

**MINOR-5｜`ridge.ridge_anomaly` 是"预备替换件"，docstring 里的接线说明从未被执行。**
证据：`ridge.py:267-274` 明写"可以直接替换 `core.py:1548-1554` 循环体内的那一行"；实测引用只有 tests×8。`core.py:1548-1554` 仍在用原循环。
影响：副高场的两套算法长期并存，改动可能只改一边。

**MINOR-6｜内核 B 依赖仓库外的硬编码数据路径。**
证据：`typhoons/basin.py:10`：`XROOT = r'G:\气象\数据\xrq'`；`BASE_YEARS = range(2000,2026)`。
影响：`typhoons/` 即使被重新接线，也无法在只有本仓库的机器上跑完整流程。

**MINOR-7｜关于"云物理是死的"的连锁事实。**
证据：`cloud_step`（T1，唯一生产调用点已移除）、`_env_to_cld`（T0）、`tc_cloud_top`（T0）、`radar_refl`（T0）四个函数全部不在生产路径；而**替代它的** `clouds/render.py` 也没生产调用方（仅测试）。
影响：模拟器当前**没有任何活的云场实现**（`core.py:4173-4292` 的平流代码属 09 号分区，其调用者 `cloud_step` 已死）。这条应进 SUMMARY。

**MINOR-8｜29 个 tracked 文件被删但未提交，README 因此"看起来"在说谎。**
证据：`git status --porcelain` 有 29 条 `D`；`git ls-tree -r HEAD -- simulator/wrf` = **18** 个文件；`git cat-file -e HEAD:simulator/run.py` 通过（HEAD 最后修改 `6b51cde`）。
影响：① 第一轮把"run.py / wrf/ 不存在"归因为"README 过时"，实际是**未提交的删除**，`git checkout` 即可恢复——SUMMARY 若照旧写法会误导修复动作；② 同一批删除里还有 `simulator/ui/*`、`simulator/render/*`（6 个文件），说明这是一次范围较大的"界面层剥离"没有提交；③ 因为基线不干净（63 条 `M`/`??` + 29 条 `D`），任何"按 README 复现"的尝试都会得到与作者不同的结论。

---

## 7. unresolved（未能确证）

> 本轮开始时列了 7 条，其中 2 条（B 组完整耗时、自愈产出的质量差）在本轮内已跑出数字并闭合，见下面前两条的"已闭合"标注；剩 5 条仍卡住。

1. ~~**B 组 `build_land()` 的完整耗时与自愈产出质量**~~ —— **已闭合**：356.15 s / RMSE 0.1102 / 最大错 1.0 / 陆地格 89 668 vs 96 392（§3.1、§5）。复现：`/usr/bin/python3 -u /mnt/g/tmp/exp_land2.py`。
2. **`simulator/output/records.json` 的确切写出路径**：schema 与 `typhoons/gen.py:323-329` 完全一致，但当前默认路径是 `simulator/typhoons/output/records.json`（该目录不存在）。是"当年 `GEN_DIR` 不同"还是"显式传了 `path`"——需要当年的运行脚本或 git 历史才能定论（**推断**：显式 `path=`）。
3. **`simulator/logs/gen_{1,42}.log` 是谁写的**：格式与 `gen.py:save_log()` 一致，但 mtime（2026-10-02 01:40）落在本轮会话内，疑似并行任务产物。需要问 08 号（`sim-run`）确认，或看它的命令历史。卡点：我无法区分"历史残留被 touch"与"本轮新写"。
4. **`app/` 侧是否存在以字符串/配置驱动的 simcore 函数派发**：已扫字符串字面量（0 命中），但不排除 `importlib` + 拼接名、或从 JSON 配置读取函数名的路径。卡点：需要逐文件读 app 的插件式注册表（`app/config_store.py`、`app/resource_manager.py` 等），本轮未分配预算。
5. **`RIDGES`（`core.py:437-446`）与 `ridge.py` 的 RIDGES 是否同源**：`ridge_anomaly` 的 docstring 写"取自 `RIDGES`(core.py:437-446)"，未逐值比对两边常量表——若不一致，那条"可直接替换"的说明本身也不成立。卡点：只需比对两张常量表，属秒级，但要在 `ridge.py` 里定位它自己的 RIDGES 定义（`__all__` 里没有）。
6. **三个 `simcore/*/__init__.py` 的 import 副作用**：若某个 `__init__` 在 import 时把某个"零调用"函数注册进回调表，它就不是死码。已用字符串扫描间接排查（0 命中），但未逐文件读那 3 个 `__init__.py`。卡点：文件极小（每个 2–6 行），下轮可直接读。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| `build_land()` 四级优先 | `simcore/core.py:516-582`（cell `:530-533`、npz `:534-542`、多边形 `:543-576`） |
| 静默自愈回写 | `simcore/core.py:577-581`（`_LAND_FILE` 定义在 `:486`） |
| `land_cell.npz` 读取与形状校验 | `simcore/core.py:487-508`（`:503` 校验）、`all_land_mask():511-513` |
| `sim['land']` 唯一写点 | `simcore/core.py:871` |
| `env['land']` 采样（5 点均值） | `simcore/core.py:2039-2058`（`:2045-2052`） |
| 经度回绕（负经度陷阱） | `simcore/core.py:607-629`（`:616`）、`:636-649` |
| 地形加载与形状校验 | `simcore/terrain.py:398, 407, 413-434` |
| 地形合成（已退化为备份） | `simcore/terrain.py:339-371`（`synthesize`） |
| 真 DEM 生成脚本（双写） | `tools/build_terrain_dem.py:57,58,59` |
| 权威陆地掩码生成 | `tools/make_cell_land_mask.py:34,43,55` |
| `_gen_tables` 全貌 | `simcore/core.py:3709-3764`（dict 误用 `:3729-3732`、吞异常 `:3733-3734`） |
| 兜底纬度/经度公式 | `simcore/core.py:4099-4118`（纬度 `:4112`、坏守卫 `:4113-4114`） |
| dens_lat 分支（永不执行） | `simcore/core.py:4094-4098` |
| 生成表缓存语义 | `simcore/core.py:3706, 3711-3713` |
| `store.load` 返回 dict | `simcore/envmodel/store.py:25-45`（`{k: z[k] for k in z.files}` 在 `:38`） |
| 现役风压关系 | `simcore/core.py:268-269`（`pmin_from_v`）、`:272-273`（`holland_b`） |
| 原型 KZC / Chavas | `typhoons/kzc.py:36-76`（`kzc_dp`）、`:79-124`（`kzc_pmin`/`min_pressure`）、`:127-170`（`calibrate`） |
| 原型生成层（记录/日志） | `typhoons/gen.py:323-329`（`save`）、`:331-348`（`save_log`） |
| 原型硬编码外部数据根 | `typhoons/basin.py:10`（`XROOT`） |
| 死码扫描脚本 | `/tmp/scan_dead2.py`（结果 `/tmp/dead2.json`）、`/tmp/mk_table.py`、`/tmp/check_strlit.py`、`/tmp/count_tiers.py` |
| 被删但仍在 HEAD 的资产 | `git ls-tree -r HEAD -- simulator/wrf`（18 个）、`git cat-file -e HEAD:simulator/run.py` |
| 本轮是否动过仓库 | `/tmp/check_dirty.py`（实测：无一条 mtime 晚于 2026-10-01 23:30） |
| 任务 A 探针 | `/tmp/exp_land2.py`（输出 `/tmp/land2_out.txt`） |
| 任务 B 探针 | `/tmp/exp_gen_tables.py` |
| 任务 D 探针 | `/tmp/exp_kzc.py` |
