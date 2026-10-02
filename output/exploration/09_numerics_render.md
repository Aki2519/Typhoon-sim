# 09 平流数值验证（U3）+ render/fill/import_tc 补读（U4/U5）

> 第二轮 · `sim-numerics` · 写作用域仅本文件。
> 闭合对象：`01_simcore.md` §7 的 **U3**（`_advect_env` 数值精度/守恒性）、**U4**（`render.py` 完整结构）、**U5**（`fill.py` / `import_tc.py` 实现）。
> 全文凡标 **【实测】** 的都是本机跑出来的数字（附复现命令）；标 **【推断】** 的是从代码读出来的、没有实验支撑的结论。

## 一句话定位

`_advect_env` 是一个**逐行位移的周期域半拉格朗日上游双线性格式**：在它真正工作的参数区间（生产实测 CFL 中位数 0.013 格/小时）里几乎没有数值耗散（48 小时 −0.99%），但它在**三个边界上不成立** —— 经度环绕的最后一列插值取错了邻居对、纬度两端把倒数第二行复制到最后一行、极区 `km_l_c=1.0` 的地板让位移最大冲到 62.7 格/小时且**全代码没有 CFL 判据**。`render.py` 是纯 pygame 叠加渲染器（44 个顶层定义、9 套缓存），`fill.py` 里只有 `ocean_fill` 在生产链路上（经 `daily.py`），`import_tc.py` 的 A/B 注入模式**在 GUI 里没有任何入口**。

---

## 1. 清单与职责

| 对象 | 行号 | 职责 | 生产链路上？ |
|---|---|---|---|
| `_sample_grid` | `core.py:4175-4187` | 双线性上游采样器（数组版）。**被 3 处复用**：`_env_to_cld:4195`、`cloud_step`、`_advect_env` | 是 |
| `cloud_step` | `core.py:4210-4279` | 云场半拉格朗日平流 + 源项（3 层子步、涡旋风叠加、包围盒切片） | **否**（第一轮已证事实死码） |
| `_advect_env` | `core.py:4282-4334` | 0.5° env 网格的冷尾流/OHC 平流，**53 行** | 是（唯一调用点 `ocean_step:4576-4577`） |
| `ocean_step` | `core.py:4533-4594` | 每小时一个子步：`_advect_env` → 全陆掩码冻结 → 15/45 天指数衰减 → 向气候态松弛 | 是 |
| `render.py` | 1648 行 / 44 个顶层定义 | pygame 叠加渲染：云图/等值线/流线/风羽/粒子/路径/风圈/副高 | 是（唯一入口 `render_map`） |
| `fill.py` | 173 行 / 7 个定义 | 缺测/陆地/孤立尖峰的**平滑**填补（Laplace 扩散 + 距离变换铺底） | 部分（只有 `ocean_fill`） |
| `import_tc.py` | 186 行 / 8 个定义 | b-deck 解析 + A 模式（锁路径）/ B 模式（种涡旋）注入 | 只有 `track_at` |

> **行号勘误**：第二轮简报写的 `core.py:4173-4292` 是"云场 + 平流"整块（含 `cloud_step`），
> `_advect_env` 本体是 **4282-4334**（`inspect.getsourcelines` 实测）。第一轮报告 §3.7 标题的
> `4282-4334` 是对的，但正文把 `4173-4189` 也算进本节了。

---

## 2. 数据流 / 调用链

### 2.1 `_advect_env` 的调用链【实测，grep 全仓只有两个调用点】

```
_sim_step_core (core.py:5090-5094)   _n_oc = ceil(step/1.0); ocean_step(_stp/_n_oc)
  └─ ocean_step(dt_h)                                    core.py:4533
       ├─ 表层流缓存 _OCEAN_UV[('usv', _envVer, shape)]   core.py:4546-4563
       │    us = us_sv(风生环流或 OSCAR) + 3%·(u850 旋转 30°)   core.py:4548-4550
       ├─ wake  = sim['wake'].reshape(360,720)            core.py:4566
       ├─ ohcw  = sim['ohcWake'].reshape(360,720)         core.py:4567
       ├─ np.copyto(wake, _advect_env(wake, us, vs, dt_h))        core.py:4576
       ├─ np.copyto(ohcw, _advect_env(ohcw, us, vs, dt_h*0.4))    core.py:4577
       ├─ 全陆格点回写冻结（all_land_mask）                  core.py:4578-4581
       └─ wake *= exp(-dt_h/360h)；ohcw *= exp(-dt_h/1080h)  core.py:4582-4583
```

调用频率：6h 步长下 `ocean_step` 走 6 次 ⇒ `_advect_env` **12 次/步**（2 个场 × 6 子步），
`scale` 分别是 1.0（SST 尾流）与 0.4（OHC 消耗）。一年 8760 个 1h 子步（注释 `core.py:4292`）。

### 2.2 render / fill / import_tc 的外部调用点【实测】

| 被调 | 生产调用点 | 其它调用点 |
|---|---|---|
| `R.render_map` | `app/ty_sim_mixins/sim_mode_mixin.py:1864` | `tests/test_sim_mode_ref.py:348,350`、5 个 `tools/` |
| `R.GridMap` | `sim_mode_mixin.py:1810`（`_sim_grid_map`，包在 try/except 里，失败返回 None） | — |
| `R.Particles` | `sim_mode_mixin.py:868`、`:1621` | `tests/test_simcore_batch.py:147,167` |
| `R.set_label_fn` | `sim_mode_mixin.py:251`、`:1758` | — |
| `fill.ocean_fill` | `simulator/simcore/daily.py:161-162`（`_fill_land`，延迟 import） | 4 个 `tools/build_*`、`tools/add_sst_mesoscale.py` |
| `import_tc.track_at` | `core.py:3539`（`motion_step` 里的 A 模式分支，延迟 import） | `tools/verify_forecast.py:1` |
| `import_tc.parse_bdeck` | **无** | `tests/test_bdeck_format.py`（7 次）、6 个 `tools/` |
| `import_tc.make_from_track` | **无** | `tests/test_simcore_motion.py:243`、`tools/verify_forecast_locked.py:169` |

`daily.py` 的生死：`simulator/simcore/envmodel/solver.py:201` 与 `app/sim_settings_dialog.py:339`
都延迟 import 它 ⇒ 它是活的，所以 `fill.ocean_fill` 是活的。

---

## 3. 核心机制详解

### 3.1 算子定义（逐行读出来的，非复刻）

```python
# core.py:4298-4299   位移（单位：格）
dx = -u_kt*1.852*dt_h*scale / _CGad['km_l_c'] / C['ENVD']     # km_l_c = max(111.32·cosφ, 1.0)
dy = -v_kt*1.852*dt_h*scale / 110.57          / C['ENVD']
# core.py:4323-4325   全网格退路
fx = np.mod(np.arange(nx, dtype=float64)[None,:] + dx, nx)     # 经度周期
fy = np.clip(np.arange(ny, dtype=float64)[:,None] + dy, 0, ny-1)  # 纬度夹取
# core.py:4328-4333  包围盒路径（i0<0 或 i1>nx 或 (j1-j0)>=ny 时走上面那条）
```

- `_const_grids()` 的 `km_l_c` 是 **float32**（`_env_grids()` 是 float32 meshgrid，`core.py:959`），
  所以 `dx/dy` 也是 float32；只有 `arange(...,float64) + dx` 那一步把它抬成 float64。
  代码注释（`core.py:4321-4322`）说"dx/dy 由 us/vs(float32) 算出，坐标斜坡必须 float64" —— 实测一致。
- `_sample_grid` 的**邻居对**：`x1 = np.clip(x0, 0, w-2)`（`core.py:4179-4180`），
  取 `arr[y1,x1]` 与 `arr[y1,x1+1]`。x0 = w-1 时会被夹到 w-2，**插值对整体西移一格**。
- 两个早退：`field.any()` 假 → 原对象返回（`4293-4294`）；`max|dx|,|dy| < 1e-3` → 原对象返回（`4305-4306`）。
- **全仓 grep 无任何 `CFL`/`cfl` 字样**（`grep -n "CFL\|cfl" core.py` 零命中）⇒ 没有 CFL 判据。

### 3.2 对照实验 A1/A2：数值耗散 vs CFL【实测】

**设计**：解析已知算例 —— 截断高斯（σ_lon=2°=4 格、σ_lat=1.5°、中心 20N/150E、4σ 外严格置 0），
在**常速均匀流**里平流。参考解 = **同一个上游位移下的解析高斯精确平移**（逐格点求值，周期化经度），
所以误差里只有插值耗散 + 舍入，不含"位移定义差"。
度量：峰值比、相对 L2、方差比、总质量比（对参考解归一）。

单步（`/mnt/g/tmp/adv_b.py` + `/mnt/g/tmp/adv_c.py`，`cd /mnt/g/气象/Typhoon-sim && python3 /mnt/g/tmp/adv_b.py`）：

| CFL(格/h) | 峰值比 | l2_rel | 方差比 | 质量比 |
|---|---|---|---|---|
| 1e-4 | 1.0000 | 2.96e-06 | 1.0000 | 1.000000 |
| 0.01 | 1.0000 | 2.73e-04 | 0.9997 | 0.999996 |
| 0.05 | 1.0000 | 1.29e-03 | 0.9985 | 0.999987 |
| 0.1 | 1.0000 | 2.43e-03 | 0.9972 | 0.999991 |
| 0.35 | 1.0000 | 6.14e-03 | 0.9929 | 0.999999 |
| 0.5 | 1.0000 | 6.75e-03 | 0.9922 | 1.000017 |
| 0.7 | 1.0000 | 5.65e-03 | 0.9935 | 0.999986 |
| 1.0 | 1.0000 | **2.01e-04** | 0.9998 | 0.999991 |
| 1.5 | 1.0000 | 6.74e-03 | 0.9922 | 1.000017 |
| 2.0 | 1.0000 | **3.79e-04** | 0.9997 | 0.999995 |
| 3.0 | 1.0000 | 5.60e-04 | 0.9995 | 0.999991 |

读法：**整数 CFL 几乎无误差**（位移正好整格 ⇒ 双线性退化为取数），**半整数 CFL 误差最大**。
这与双线性插值的放大因子 |G|² = 1 − 4a(1−a)sin²(θ/2) 完全一致（a 为格内小数部分，a=0.5 时耗散最强）。

多步累积（每步都重新插值，位移 = N×CFL 格）：

| CFL | N=1 | N=6 | N=24 | N=72 | N=168 | N=168 峰值比 | N=168 方差比 |
|---|---|---|---|---|---|---|---|
| 0.05 | 1.29e-03 | 7.63e-03 | 2.95e-02 | 8.19e-02 | 1.66e-01 | 0.823 | 0.817 |
| 0.10 | 2.43e-03 | 1.43e-02 | 5.40e-02 | 1.41e-01 | 2.63e-01 | 0.723 | 0.717 |
| 0.35 | 6.14e-03 | 3.53e-02 | 1.23e-01 | 2.77e-01 | 4.40e-01 | 0.547 | 0.543 |
| 0.50 | 6.75e-03 | 3.86e-02 | 1.33e-01 | 2.94e-01 | 4.59e-01 | **0.529** | 0.525 |
| 0.70 | 5.65e-03 | 3.26e-02 | 1.15e-01 | 2.63e-01 | 4.23e-01 | 0.563 | 0.559 |
| 0.90 | 2.40e-03 | 1.41e-02 | 5.33e-02 | 1.39e-01 | 2.60e-01 | 0.726 | 0.720 |
| 1.00 | 2.01e-04 | 1.12e-03 | 4.43e-03 | 1.30e-02 | **2.93e-02** | **1.0000** | 0.974 |
| 1.50 | 6.74e-03 | 3.86e-02 | 1.33e-01 | 2.94e-01 | 4.59e-01 | 0.529 | 0.525 |
| 2.00 | 3.79e-04 | 2.21e-03 | 8.68e-03 | 2.51e-02 | 5.46e-02 | **1.0000** | 0.951 |

结论【实测】：
- 半格 CFL 下 168 步后峰值掉到 **52.9%**，等效"每个子步 −0.38%"，即该格式在 a≈0.5 时是**强耗散**的；
- 整数 CFL 下 168 步峰值**不衰减**（1.0000），L2 误差仍累积到 2.9e-02（相位/形状误差来自整数格取数时的行间位移差）；
- 但生产实际 CFL 中位数是 **0.0132 格/小时**（见 §3.6），落在 `a≈0.013` 的最优区间，
  该区间的每步耗散 ≈ 0.002%（A2 的 0.05 行外推），**对冷尾流的实际耗散可以忽略**。

### 3.3 守恒性 A3/A3b【实测】

**设计**：构造 `u` 使**每一行的 `dx` 恰好相同**（`U = cfl·km_l_c·ENVD/1.852`，实测 `dx` min=max=−0.35/−0.70 格），
此时每行都是"整行等位移周期平移"，双线性格式应当**逐行严格守恒**。

| CFL | N | Σf/Σf₀（对参考解归一） | 面积权 Σf·cosφ |
|---|---|---|---|
| 0.35 | 1 / 24 / 168 | 1.000000 / 1.000000 / 1.000000 | 1.000000 |
| 0.70 | 1 / 24 / 168 | 1.000000 / 1.000000 / 1.000000 | 1.000000 |

【实测】结论：**非辐散、沿行等位移时，这个格式逐行精确守恒**（残差在 1e-6 的 float32 舍入量级，
`core.py:4332-4333` 的 `out = field.copy(); out[bbox] = sub` 也不会漏掉质量，因为 `_pad=ceil(dmax)+2` 足够）。

反例（辐合/辐散流，`u = k·(lon−200)`，k=±0.02，24 步）：

| 流型 | 24 步后 Σf/Σf₀ |
|---|---|
| 辐合 k=+0.02 | 1.008536（+0.85%） |
| 辐散 k=−0.02 | 0.991533（−0.85%） |

这是**格式的固有属性**而非 bug：`_advect_env` 解的是**平流形式** `∂f/∂t + u·∇f = 0`（沿轨迹守恒），
不是通量形式 `∂f/∂t + ∇·(uf) = 0`（质量守恒）。冷尾流是"海温异常"，按异常物质守恒来做是对的；
但**如果哪天有人把它当"热量收支"读，就会差 0.85%/天**。生产 `us/vs` 有散度（风生环流 + 3% 风），
真实漂移率未测（见 §7 U-B）。

### 3.4 经度环绕：**不无缝**（A4/A8b/A8c）【实测，本报告最硬的一条】

**(a) 单元测试直接测采样器 `_sample_grid`**（8×4 小算例，`a[r,c]=8r+c`，取 fy=1.0）：

| fx | 实测返回 | 周期域正确答案 | 差 |
|---|---|---|---|
| 3.000 | 11.000 | 11.000 | 0.0000 |
| 6.000 | 14.000 | 14.000 | 0.0000 |
| **7.000（=nx−1）** | **14.000** | **15.000** | **−1.0000** |
| **7.500** | **14.500** | **11.500** | **+3.0000** |
| **7.999** | **14.999** | 8.007 | **+6.9920** |

根因：`x1 = np.clip(x0, 0, w-2)`（`core.py:4179`）。当 `fx ∈ [nx−1, nx)` 时 `x0 = nx−1` 被夹成 `nx−2`，
插值对变成 `(f[nx−2], f[nx−1])`，而周期域正确的一对是 `(f[nx−1], f[0])`。

**(b) 在 `_advect_env` 里隔离该缺陷**（构造每行 `dx` 恰为 −1.000 整数格，无任何插值误差）：

输入（row220）= `[..., 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]` 放在第 714..719 列，向东移 1 格：

| 位置 | 期望（= `np.roll`） | 实测 |
|---|---|---|
| 第 0 列（环绕处） | **3.0** | **2.5** |
| 第 1 列 | 0.0 | 0.0 |
| 第 715..719 列 | 0.5,1.0,1.5,2.0,2.5 | **完全一致** |

错格点数 = **1**（只有第 0 列），max|Δ| = 0.5；**内部同形对照（第 354..359 列）错格点数 = 0**。
半格位移（dx=−0.5）时：第 0 列实测 **2.75**，周期域正确值 **1.5** ⇒ **相对误差 83%**。

**(c) 端到端形态对照**：肿块中心 359.5°E 与 179.5°E 各向东移 3 格（两个初值互为精确的 180° 周期平移），
接缝组滚回内部后相减：`max|Δ| = 5.93e-02`（峰值的 6%）、`L2_rel = 1.84e-02`，
差异**全部集中在 1 列**（滚回后第 363 列 = 接缝组的原第 3 列）。

【实测】判定：**"跨 0/360 无缝"不成立**，误差是**单列、单格**量级（相对 30–80%），
只在源点落进最后一列时触发。它影响的是"冷尾流平流到经度 0/360 附近"的那一列。
另外注意：**包围盒退路（`i0<0 or i1>nx`）在肿块离接缝 ≲3° 时就会被触发**，
所以真实触发条件是"尾流进入 0°/360° ± 约 2.5°"，而不是"必须跨越"。

### 3.5 纬度端点的极区"复制行"（A5）【实测】

**设计**：肿块中心 87°N，向北 3 kt（`dy = +0.1005 格/步`），跑 20 步。

| N | 顶端 2 行质量和 | Σf/Σf₀ | 面积权比 |
|---|---|---|---|
| 0 | 0.216978 | 1.000000 | 1.000000 |
| 2 | 0.350433 | 1.004233 | 1.033882 |
| 5 | 0.351796 | 1.005587 | 1.084414 |
| 10 | 0.351795 | 1.007786 | 1.168757 |
| 20 | 0.351795 | **1.012183** | **1.337919** |

【实测】纬度 `np.clip(..., 0, ny-1)` 不是"反射"也不是"吸收"，而是**把倒数第二行的内容复制到最后一行**：
最后一行 `y0 = ny−1` 被夹成 `ny−2` ⇒ 输出 = 第 ny−2 行的值，而第 ny−2 行自己的值**并不减少**。
于是极区**凭空造质量**（20 步 +1.2% 总量、面积权 +34%）。

【推断】生产里这条**大概率不可达**：`wake`/`ohcWake` 只在有台风走过的地方非零，
而目前模型里台风不会走到 ±89.75°。但它与 §6 MAJOR-2 的极区 CFL 是同一类隐患：
一旦台风按用户的"极区必须参与模拟"跑到高纬，这条边界就会生效。

### 3.6 CFL>1 的行为（A10）【实测】

- **代码没有任何 CFL 判据**（`grep CFL core.py` 零命中）。两个早退只管"位移≈0"，不管"位移过大"。
- 半拉格朗日是无条件稳定的，所以 CFL>1 **不会爆**（A2 的 CFL=2、3 行误差反而很小，因为是整数格），
  但它会把**远距离的上游值直接搬过来**，物理上无意义。
- 真实生产输入（`dataMode=True`、2025-08-15、seed 12345，`ocean_step` 后的 `_OCEAN_UV`）：

| 指标 | 实测 |
|---|---|
| `us` 范围 | −16.926 ~ +16.407 kt；`mean|us| = 0.314` |
| `|vs|` max | 15.485 kt |
| `|dx|`（格/小时） | 均值 0.0631、p50 **0.0132**、p99 1.0191、**max 62.6939** |
| `|dy|`（格/小时） | p99 0.0435、max 0.5188 |
| **CFL>1 的格点** | **2637 / 259200 = 1.0174%** |
| `|dx|<1e-3`（走早退 2） | 4.01% |

按纬度带拆开（**完整实测表**，`/mnt/g/tmp/adv_d.out` 前 20 行）：

| 纬度带 | mean&#124;dx&#124; | p99 | max | CFL>1 占比 |
|---|---|---|---|---|
| −90..−60 | 0.24923 | 3.6308 | **62.6939** | **4.856%** |
| −60..−40 | 0.02629 | 0.05031 | 0.05461 | 0.000% |
| −40..−20 | 0.01153 | 0.04049 | 0.04952 | 0.000% |
| −20..+20 | 0.00909 | 0.03122 | 0.05063 | 0.000% |
| +20..+40 | 0.01017 | 0.02914 | 0.05273 | 0.000% |
| +40..+60 | 0.01206 | 0.04506 | 0.07894 | 0.000% |
| +60..+90 | 0.07718 | 1.2105 | 2.6590 | 1.248% |

⇒ **CFL>1 只出现在 |lat|>60 的两条极带**；热带/中纬所有带的 max&#124;dx&#124; ≤ 0.079 格/小时。
**max|dx| = 62.694 格/小时出现在 lat=−89.75、lon=166.75**，
那一行 `KM_L_C = 1.000`（撞上 `np.maximum(111.32·cosφ, 1.0)` 的地板）。

**真凶是输入不是格式**【实测】：该点 `v850 = +719.94 kt`、`u850 = −235.83 kt`。
`env["v850"]` 全局最大 **722.12 kt**（lat=−89.75, lon=156.25），`u850` 最大 239.57 kt，
`hypot(u850,v850)` 最大 757.6 kt ⇒ `3%×757.6 = 22.7 kt` 的风生漂流上限。
逐行看极区风速是**教科书式的 1/cosφ 放大**：

| row | lat | 1/cosφ | max|u850| | max|v850| |
|---|---|---|---|---|
| 0 | −89.75 | 229.2 | 239.57 | 722.12 |
| 1 | −89.25 | 76.4 | 86.16 | 205.06 |
| 2 | −88.75 | 45.8 | 71.93 | 123.95 |
| row0/row1 比值 | — | 3.00 | **2.78** | **3.52** |
| row1/row2 比值 | — | 1.667 | **1.198** | **1.654** |

【推断】`env_update` 的极区场存在 1/cosφ 奇点（南半球明显、北半球弱），
`env["div"]` max 也到 436.5。**这不是我的分区，交 sim-data / sim-core 裁定**，
我只提供"它的后果"：把极区 `us/vs` 放大到 16.9 kt，再乘 `km_l_c=1.0` 的地板，得到 62.7 格/小时的位移。

真实输入下的热带肿块漂移（20N/150E，逐小时 48 步，用真实 `us/vs`）：

| 小时 | Σf/Σf₀ | 面积权比 | 峰值比 |
|---|---|---|---|
| 1 | 0.999783 | 0.999769 | 1.0000 |
| 6 | 0.998707 | 0.998624 | 0.9999 |
| 24 | 0.994930 | 0.994601 | 0.9976 |
| 48 | **0.990119** | 0.989463 | 0.9906 |

【实测】≈ **−0.021%/小时**（−1%/48h）。对 15 天衰减时标的冷尾流，这个数值耗散是**次要项**。

### 3.7 两条返回路径的行为差异（A6/A9）【实测】

- **数值**：小位移走包围盒路径时，与手工"全网格"计算 `max|Δ| = 2.75e-08`（float32 eps 量级）、**不是逐位相同**；
  跨缝走全网格退路时**逐位相同 = True**。
- **dtype**：`_advect_env` 在小位移（包围盒）路径返回 **float32**，在全网格退路返回 **float64**；
  两个早退分支返回**输入对象本身**（dtype 随输入）。调用点 `np.copyto(wake, ...)` 把它拷回 float32，
  所以生产无影响 —— 但这是一个 API 层的不一致（`cloud_step` 直接调 `_sample_grid`，不走这条路）。
- 【实测】`_pad` 用的是**全网格** `_dmax`（`core.py:4312`）。极区 62.7 格 ⇒ `_pad = 65`，
  意味着只要走包围盒路径，切片至少扩 65 格（130 宽 × 130 高）。**这是正确性上无害、性能上偏保守**的写法。

### 3.8 副产品：`wind_driven_currents` 的上游 `OSCAR` 场定向【实测，建议归 sim-data 复核】

`_oscar_currents()`（`core.py:4424-4448`）只要 `simulator/env/climate/currents_2019.npz` 存在就**无条件**
返回它（实测存在，235 KB，`u/v` shape=(120,360)、max|u|=1.552），`wind_driven_currents` 直接 **return oc**
（`core.py:4479-4481`）⇒ 风生环流公式与它自己的 ±4 kt clip **全部被短路**。

docstring 说"1° 南在上"（`core.py:4425`），`_resample_grid`（`:4451-4464`）**没做任何 flip**；
而项目自己的可视化脚本 `tools/check_currents.py:22` 却写了 `rgba = rgba[::-1]  # 南在上 -> 北在上`。

我用"行平均速率剖面"做了客观判向：文件行平均速率**在 row 90 取极大（0.2362）**，向两侧单调下降；
北在上时 row 90 = 45.5°S（南大洋 ACC 带，行平均极强），南在上时 = 45.5°N（北半球无同等强度的环球带）。
row 0..10 的速率**恰好全 0**（冰盖下的极区缺测）。
⇒ 【实测+推断】**文件实际是"北在上"**，即 `_resample_grid` 不 flip 是对的，而
`_oscar_currents` 的 docstring 与 `tools/check_currents.py` 的 `[::-1]` 是**错的**（那个图是上下颠倒的）。
经度约定**未确证**：最大速率格 (row90, col123)；0..360 解释 = 45.5S/123.5E（南大洋），
−180..180 解释 = 45.5S/−56.5E（南大西洋），两者都合理。用内核陆地掩码做"陆上速率应更低"的检验时，
`北在上 + lon 0..360` 得到陆/洋 = 0.0486/0.0735（最低），`北在上 + −180..180` = 0.0598/0.0683，
**证据倾向"列就是 0..360、与内核一致"**，但两个假设差距不大，标为弱证据。

### 3.9 `render.py` 结构（1648 行 / 44 个顶层定义）

**分层**（行号区间）：

| 区段 | 行 | 内容 |
|---|---|---|
| 色标 | 24-128 | `_build_lut`（256 级 RGBA 插值；`:34-40` 处理 stops 降序）、`LUT_IR/LUT_SST/LUT_OHC`、`_WIND_STOPS`/`_wind_color`、`_CMA_KT` 分级 + `grade_kt`/`cat_of_kt`/`cat_zh_kt` |
| 云图 | 130-344 | 缓存头、`GridMap`、`_tc_signature`、`_cloud_surface`、`render_sat`、`_cld_grids` |
| 陆地掩码 | 347-375 | `_ROOT_DIR`、`_hi_land_mask`（读 `map/heightmap.png` 现算） |
| 风切 | 378-433 | `LUT_SHEAR`、`_smooth_field`、`_build_step_lut`/`_hex`、`SST_STEPS`/`OHC_STEPS` |
| 环境图层 | 436-622 | `_env_arr_cache`、`LAYER_LAT0/1`、`_all_land_hi`、`_pad_poles`、`render_env_layer` |
| 等值线 | 625-793 | `_pressure_extremes`、`_contour_segments`（marching squares）、`draw_contours`、`set_label_fn`/`_label` |
| 风切等值线 | 796-886 | `_shear_segments`、`draw_shear_contours` |
| 流线/洋流/风羽 | 889-1179 | `_stream_seeds*`、`_visible_window`、`_flow_throttle_*`、`_build_streamlines`、`draw_streamlines`、`draw_currents`、`draw_barbs` |
| 粒子/路径/HUD | 1182-1372 | `Particles`、`draw_forecast`、`draw_tracks`、`draw_tc_hud`、`_draw_ellipse_dashed`、`_draw_quadrant_ring` |
| 副高/网格/总入口/贴图 | 1375-1648 | `RIDGE_VISIBLE_PX`、`draw_ridge`、`draw_grid`、`render_map`、`_blit_layer` |

**`GridMap`（`:144-236`）语义**：环境格点 → 屏幕的**仿射**映射（不做逐点环绕翻面，理由见 `:145-156`）。
`x(lon)/y(lat)` 线性插值；`xs` 只是 `x` 的列表版；`px_per_deg_x()` = `|x1−x0|/(lon1−lon0)`；
`visible(size)` 返回屏幕可见矩形（完全不可见返回 None，`:199-211`）；
`src_window(nx,ny,vis)` 返回**源格窗口 `(c0,r0,c1,r1)` + 目标 `pygame.Rect`**（`:213-236`），
`wrap>1` 时在 `__init__` 里一次选好最近的环绕副本（`:169-174`）。

**四条主渲染路径**：

| 入口 | 行 | 输入 → 输出 | 关键分支 |
|---|---|---|---|
| `render_sat` | 275-326 | `sim['cld']` (120×360) → RGBA (120,360,4) uint8 | `VIS` 早退 `:279-286`；IR 里逐台风叠加眼墙/ERC/螺旋雨带 `:290-319`；LUT 索引方向被修过 `:321-323` |
| `render_env_layer` | 531-622 | 一个 env 场 → RGBA (360,720,4)，再 `_hi_land_mask` 上采样到 (720,2160)、按 `all_land` 挖洞、`_pad_poles`、`np.flipud` | **缓存键带内容校验和** `:549-552`（理由：直接改 `sim['env'][name]` 不会更新 `_envVer`）；`ohc` 走 `sim['ocn']['ohc_kj']` `:544-545` |
| `render_map` | 1455-1574 | 按 `layers` 集合逐层叠加到传入 surface | 每个图层独立 try/except + `logger.debug`；`cloud→sst/ohc/rh/shear→pressure→flow→current→ridge→wind→track` 固定顺序；`gx/gy=grid.x/grid.y`，点类仍用 fx/fy `:1469-1470` |
| `_blit_layer` | 1577-1648 | RGBA 数组 → 贴到 surface | **grid 路径**（可见窗口切片 + `smoothscale` + 表面缓存 `:1590-1615`）与**旧整域路径**（按行/列自动推纬度跨度 `:1623-1625`） |

**缓存策略逐个**（键 → 容量 → 失效）：

| 缓存 | 行 | 键 | 上限 | 说明 |
|---|---|---|---|---|
| `_cloud_surf_cache` | 135-138 | `(mode, _cldVer, round(t*4), _tc_signature, w, h)` | 6（满则 `clear()`） | 另有墙钟限流 `_CLOUD_MIN_INTERVAL_MS=300ms`，用一个全局 `_cloud_last`（`[surf,made,w,h]`） |
| `_layer_surf_cache` | 140-141 | `(cache_key,'g',c0,r0,c1,r1,dst.w,dst.h,alpha)` | 12 | `_blit_layer` 的缩放后 pygame surface |
| `_env_arr_cache` | 436-437 | `(name, _envVer, alpha, round(sum,3))` | 8 | 校验和兜住"原地改场" |
| `_contour_cache` | 643-644 | `(id(p), _envVer, levels, p.shape, sum(p))` | 4 | 只缓存"格坐标线段"，屏幕映射每帧做 |
| `_shear_cache` | 801-802 | `(id(sh), _envVer, sum(sh))` | 2 | 整批（18 档）缓存 |
| `_flow_cache` | 956-957 | `(envVer, w, h, int(fx(0)), int(fy(0)), int(fx(180)), 量化 window)` | 4 | 流线图 `flow` 与 `cur` 共用一张表（键不同） |
| `_flow_last`（限流） | 964 | **按图层名分开**的 `[surf, ms]` | — | 150ms 窗口；`:958-963` 记录了"共享一份导致洋流层拿到风场图"的旧缺陷 |
| `_SEED_WINDOW_CACHE` | 905 | `(n, 量化 lon/lat 窗口)` | 24 | 种子点 |
| `_STREAM_SEEDS` | 889 | `n` | 1 | 全域名义种子（旧路径） |
| `_label_cache` | 759 | `(text, color, 是否注入 fn)` | >64 弹出**插入序**最早 | 不是 LRU |
| `_ALL_LAND_CACHE` | 443 | `{'tried','mask'}` | 1 | 读 `simulator/env/model/land_cell.npz` |
| `_HI_LAND`/`_HI_LAND_TRIED` | 350-351 | 全局单例 | 1 | `map/heightmap.png` 现算 |
| `_cld_grids_cache` | 329 | `id(sim)` | >4 弹最早 | 用 `id()` 做键（`core.py:4356` 明确说过"不用 id()，见 sst_clim_cached 的教训"）—— 但这里缓存的是常量网格，退化的后果只是重算 |

**其它要点**：`Particles`（`:1182-1224`）向量化采样风场、越界或上陆就重新随机撒点，
`draw` 是 1200 次 Python 循环 + 每点 2 次 fx/fy；`draw_streamlines` 用 **8h/步 × 5 步 RK2**
（`:1010-1014`），`draw_currents` 用 **240h/步 × 5 步、min_speed=0.05**（`:1093-1096`）；
`draw_barbs` 的取样网格**硬夹在 lat ∈ [−55,55]**（`:1132-1133`），间距按 48px 反推并吸附 0.5°。

### 3.10 `fill.py`（173 行）实现

职责：把"缺测 / 陆地 / 孤立尖峰"三类格点用**平滑扩散**补掉（而不是最近有效值跳变，动机见 `:1-12`：
原来跨陆地取数会产生 18.8 °C 的相邻格跳变）。

- `smooth_fill(a, bad, iters=400, tol=1e-4)`（`:29-80`）：**距离变换铺初值**（`scipy.ndimage.distance_transform_edt`，`:51-53`）
  → 迭代"未知格取四邻均值"，**已知格固定**（`:72-78`）。注释 `:69-71` 记录了旧版把"已知格计数"写进分母的缺陷。
  经度用 `np.roll`（`:66`），纬度用 edge 复制（`:55-64`）。
- `fill_to_ocean(a, landfrac, thresh=0.999)`（`:83-86`）：一行包装。
- `outlier_mask(a, k=0.45, abs_min=20.0)`（`:115-133`）：**比全部 8 邻居越界 `|k·median|+20`** 才算尖峰；
  背景是 base_tchp 在 (2.0S,153.2E) 的单格坏值（`:118-121`）。
- `ocean_fill(a, warm, land, despike, zero_med, ...)`（`:136-173`）：把三类判据并起来 ——
  `~isfinite`、`(a<=0.5)&(warm>26.5)`、`(a<=0.5)&(warm>23.5)&(邻域中位数>zero_med)`、`landfrac>=0.999`、
  `outlier_mask`；然后 `smooth_fill`。
- 辅助：`_quiet()`（`:21-26` 静音 nanmedian 警告）、`_neigh_stack`（9 视图，`:91-100`）、`_nb_stat`（`:103-112`）。

### 3.11 `import_tc.py`（186 行）实现

- 单位换算：`_lat/_lon` 把 b-deck 的 `125N`/`0180E`（**十分之一度**）转成度，`_lon` 要求 `len(s)>=4`（`:34-53`）。
- `parse_bdeck(path)`（`:63-109`）：按时间字典去重、按时间排序；非法行（列数<10、时间戳非 10 位数字、
  纬度/经度/风速缺失或 vmax≤0）直接跳过；`pmin>1100` 置 None；`rmw` 取 `[19]` 并限制在 (0,200) nm。
  **方言判定**（`:86-100`）：列数>27 用 `[27]` 当名字（观测 ATCF），≤27 且 `[11]` **含字母**才当名字（模型 12 列落盘），
  轮次 153 把"非纯数字"收紧成"含字母"以排除 `-99`。
- `make_from_track(sim, rows, mode)`（`:127-152`）：mode A 写 `tc['track_lock']=rows`/`track_i`/`importMode='A'`；
  mode B 只写 `importMode='B'`；初值 `v0 = clip(rows[0].vmax, 20, 60)`（`:138`）。
- `track_at(tc, clock_dt)`（`:155-174`）：按小时线性插值出 `(lon, lat, vmax)`，超出末端返回末点。
- `summarize(rows)`（`:112-124`）：UI 摘要。
- `list_candidates(root, limit=40)`（`:177-186`）：`os.walk` 找 `*.dat`。

---

## 4. 输入输出资产

| 资产 | 谁用 | 实测 |
|---|---|---|
| `simulator/env/climate/currents_2019.npz` | `_oscar_currents`（`:4436`） | 235315 B，`u/v` (120,360) float32，max|u| 1.552、max|v| 1.074 |
| `map/heightmap.png` | `_hi_land_mask`（`:366`） | PIL 实测 `size=(10800,5400)`、`mode=I;16`；`[900:4500]` 正好对应 60N..60S；`[::5,::5]` → **(720,2160)** |
| `simulator/env/model/land_cell.npz` | `_all_land_hi`（`:455`） | 存在（01 报告已证它是生效的陆地掩码） |
| `assets/font/MapleMono-NF-CN-Medium.ttf` | `_label` 回退字体（`:784-786`） | 存在 |
| b-deck `*.dat` | `parse_bdeck` / `list_candidates` | 生产 app **不用**（见 §6 MINOR-6） |
| `map/map.png` | 底图（app 侧） | `tools/check_currents.py:20` 缩放到 2160×1080 |

---

## 5. 如何运行 / 如何验证

```bash
# 环境: WSL python3 足够（core.py 是纯 numpy/pygame-free；探针不需要 pygame）
cd /mnt/g/气象/Typhoon-sim
python3 -c "from simulator.simcore import core as V; print(V.ENVNY, V.ENVNX)"   # 360 720

# 解析平流对照（A1/A2/A3/A4/A5/A6/A7）—— 约 40 s
python3 /mnt/g/tmp/adv_b.py
# 低 CFL 段 + 接缝隔离 + dtype + 生产 CFL 分布 —— 约 30 s
python3 /mnt/g/tmp/adv_c.py
# 非对称接缝算例 + 真实 us/vs 漂移 + OSCAR 定向 —— 约 30 s
python3 /mnt/g/tmp/adv_d.py
# us=16.9kt 溯源 + OSCAR 陆地掩码判向 —— 约 20 s
python3 /mnt/g/tmp/adv_e.py
# 极区 1/cosφ 放大 + heightmap 形状 + 真实行号 —— 约 15 s
python3 /mnt/g/tmp/adv_g.py
# 调用点普查（纯文本 AST）
python3 /mnt/g/tmp/census.py
```

探针脚本全部在**工作区外**（`G:\tmp\` = WSL `/mnt/g/tmp/`），未写入仓库，未修改任何源码。
只读验证手段：`inspect.getsourcelines` 核行号、`V._advect_env(...)` 直调真实函数（不依赖 `sim` 状态，
只需 `field/u/v` 三个同形状数组 + `_const_grids()` 惰性初始化）。
**数值环境差异**：探针用的是 WSL python3 + numpy 2.3.5，项目运行时是 Windows Python 3.14 + numpy 2.5.1；
本报告所有量级结论（1e-3 ~ 1e+2）远大于版本差异可能引入的浮点差。

---

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜`_sample_grid` 的经度末列 off-by-one ⇒ 环绕不无缝
`core.py:4179-4180` 的 `x1 = np.clip(x0, 0, w-2)`。实测（§3.4）末列误差 1.0 格、半格位移时相对误差 83%，
接缝组 vs 内部同形组的 `max|Δ| = 5.93e-02`。**同一缺陷对 `cloud_step` 也是活的**
（`cloud_step:4275` 直接调 `_sample_grid`，且它的 `fx_cld` 根本没做 `np.mod`）。
修法只需在 `x1 == w-1` 时把右邻居换成 0（并让 `tx` 用 `fx - (w-1)`）。

### MAJOR-2｜没有任何 CFL 设防；极区 `km_l_c=1.0` 地板 + 1/cosφ 奇点 ⇒ 位移 62.7 格/小时
`core.py:1027` 的 `np.maximum(111.32·cosφ, 1.0)` 在 |φ|>89.48° 生效；
`env['v850']` 在 lat=−89.75 实测 722.12 kt（§3.6），两者合成 `max|dx| = 62.694`。
实测 **1.0174% 的格点 CFL>1**。半拉格朗日不会爆，但位移量已经无物理意义。
**触发条件**：只要 `_advect_env` 的场在那一带非零。目前尾流到不了极区 ⇒ 现状不可见，
但用户明确要求"极区必须参与模拟"，一旦台风上高纬这条就生效。

### MAJOR-3（转交）｜`env` 极区风场疑似 1/cosφ 奇点
`v850` max 722.12、`u850` max 239.57、`div` max 436.5，且逐行比值与 `1/cosφ` 吻合（§3.6）。
根因在 `env_update` / 数据层，**不在本分区**；我这里只给出后果与复现命令。

### MINOR-1｜`_advect_env` 两条返回路径 dtype 不一致
包围盒路径 float32、全网格退路 float64、早退返回原对象（§3.7）。调用点会 `copyto` 回去故无害。

### MINOR-2｜`render_env_layer` 的 `hi` 掩码纬度错配（当前走不到）
`render.py:588-596` 把 `hi`（(720,2160)，只覆盖 **60N..60S**）套到 `big`（(720,2160)，覆盖 **±90**）上。
两者都"南在上"，但一个 720 行=120°、一个 720 行=180° ⇒ 海岸线被按 1.5 倍纬度拉伸。
`_hi_land_mask` 的 docstring（`:355`）写"(1080,2160)"，实测返回 **(720,2160)**。
**当前不生效**：`:596` 之后走的是 `_all_land_hi`（`land_cell.npz` 在 ±90 内核网格上，正确），
`hi` 只在 `land_cell.npz` 缺失时兜底（`:606-607`）。属"给以后扩域的人下套"一类。

### MINOR-3｜`render.py` 里多处 ±60 时代的残留
`LAYER_LAT0/1` 注释（`:439-440`）说"内核只算 ±60"（实际 ±90）；`draw_barbs` 硬夹 lat ∈[−55,55]（`:1132-1133`）；
`draw_grid` 只画 ±60（`:1449-1452`）；`sim_mode_mixin.py:1824` 与 `:1836` 的兜底 fx/fy 仍按 60S-60N 算。
另 `_pad_poles` 的整段外推代码（`:514-528`）**当前不可达**（作者自己在 `:512-513` 写明），
因为两个调用点传进来的数组都真实覆盖 ±90 ⇒ 函数是幂等的。

### MINOR-4｜`_cld_grids` 用 `id(sim)` 做缓存键
`render.py:333`。`core.py:4356` 明确写过"不用 id()，见 sst_clim_cached 的教训"。
这里的缓存物是常量网格，撞 id 的后果只是重算/共用（无害），但与项目自己的约定不一致。

### MINOR-5｜`fill_to_ocean` 是死包装
`fill.py:83-86`，全仓零调用（`smooth_fill` 本身被 `tools/build_base_fields.py:92` 用，是活的）。

### MINOR-6｜`import_tc` 的 A/B 注入模式在 GUI 里没有入口
`make_from_track`（唯一写 `track_lock` 的地方，`import_tc.py:145`）全仓只有
`tests/test_simcore_motion.py:243` 与 `tools/verify_forecast_locked.py:169` 两个调用点；
`list_candidates`（`:177`）、`summarize`（`:112`）**全仓零调用**；
`parse_bdeck` 只有测试 + 6 个 tools。而消费端是活的：`core.py:3537-3562` 的 A 模式分支
（注释里记着轮次 120 修过的"+40~+100kt 高偏"）。⇒ **"把观测路径喂进模拟"这条需求目前只能靠 tools 脚本跑。**

### 死码边界（全仓调用点普查，`/mnt/g/tmp/census.py` + 定点 grep）

| 文件 | 生产零调用（含仅测试/仅工具） | 说明 |
|---|---|---|
| `render.py` | `cat_of_kt:111`、`cat_zh_kt:115`、`draw_grid:1446`、`draw_tracks:1285`、`draw_forecast:1227`（外部零调用，仅 `render_map:1561` 内部调用） | `paint_render.py:323/342` 的 `_draw_grid`/`_draw_tracks` 是**同名不同函数**，别误算 |
| `render.py` | 仅工具/测试：`render_sat`、`render_env_layer`、`_blit_layer`、`draw_contours`、`draw_streamlines`、`draw_currents`、`draw_ridge`、`Particles`、`_flow_throttle_put` | 这些在**生产里由 `render_map` 内部驱动**，不是死码 |
| `render.py` | 生产唯一入口：`render_map` ← `sim_mode_mixin.py:1864`；`set_label_fn` ← `:251/:1758`；`GridMap` ← `:1810`；`Particles` ← `:868/:1621` | |
| `fill.py` | `fill_to_ocean:83`（真死码）；`smooth_fill:29` 仅 tools 直调（但被 `ocean_fill` 内部使用 ⇒ 活） | `ocean_fill` 经 `daily.py:161` 在活链路上 |
| `import_tc.py` | `list_candidates:177`、`summarize:112`（真死码）；`parse_bdeck:63`、`make_from_track:127` 仅测试/工具 | `track_at:155` 是唯一进生产（`core.py:3539`） |

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U-A | 接缝 off-by-one 与极区"复制行"在**真实生产轨迹**里到底触发过没有、影响几个风暴 | 需要一次带风暴的长模拟（`--days ≥ 60`、关 `forecast`），逐小时记录 `wake` 非零格的经度范围与是否落在 [nx−1,nx)，以及是否有 |lat|>88 的非零格。本轮禁跑长模拟，故只给了"合成算例已确证、生产触发条件已定量" |
| U-B | 真实 `us/vs`（含散度）下 `wake` 总质量的**长期**漂移率 | 我实测的是 48 小时内的合成肿块（−0.99%/48h）；生产 `wake` 是每步都在源汇（沉积/衰减）的活场，需要一条 ≥30 天的长跑逐日记录 `Σwake`。可与 U-A 共用一次跑 |
| U-C | `OSCAR currents_2019.npz` 的**经度**约定（0..360 还是 −180..180） | 需要源头文件（OSCAR 产品说明书）或原作者生成脚本；本轮只有"陆地掩码陆/洋速率比"这一条弱证据（倾向 0..360）。纬度方向已用行剖面确证为"北在上" |
| U-D | `env['v850']=722 kt` 的成因（1/cosφ 除零？数据源极区坏值？） | 需要读 `env_update` 全链（`core.py:1546-2024`）与 `env/climate/*` 产物；**不属于本分区**，已把行级证据交给 sim-data / sim-core |
| U-E | `import_tc` 的 A/B 模式是"曾经接线后来删了"还是"从未接线" | 需要 `git log -S"make_from_track"`（`output/` 未跟踪，但 `app/` 与 `simulator/` 在 git 里）。若历史上 app 有入口，则是回归缺陷；否则是未完成需求 |
| U-F | `_advect_env` 的 float32 舍入对**多步累积**的贡献 | A7 单步对照里 f32 输入与 f64 输入的 6 项指标**完全一致**（l2_rel 都是 6.746e-03、峰值比都是 1.0000、质量比都是 1.000017），说明单步的 f32 舍入低于该算例的分辨率；但 168 步累积后的 f32/f64 对照没做（分析上没有放大机制，标为可忽略，未实测定论） |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 双线性上游采样器（含末列缺陷） | `simulator/simcore/core.py:4175-4187`（夹取在 `:4179-4180`） |
| 平流主函数 | `core.py:4282-4334`（早退 `4293/4305`、位移 `4298-4299`、退路 `4320-4325`、包围盒 `4326-4333`） |
| `km_l_c` 极区地板 | `core.py:1027`（`_const_grids`） |
| 唯一调用点 | `core.py:4576-4577`（`ocean_step`） |
| 海洋柱/衰减/全陆冻结 | `core.py:4533-4594` |
| 表层流（OSCAR / 风生环流） | `core.py:4424-4464`、`4466-4526`、缓存 `4530/4546-4563` |
| 生产 CFL 输入 | `_OCEAN_UV[('usv',_envVer,shape)]`，`ocean_step` 第一段 |
| 云场（事实死码，但共用采样器） | `core.py:4210-4279` |
| render 总入口 | `render.py:1455-1574`，生产调用 `app/ty_sim_mixins/sim_mode_mixin.py:1864` |
| 格点仿射映射 | `render.py:144-236`；构造 `sim_mode_mixin.py:1781-1816` |
| 环境图层 + 掩码 + 缓存 | `render.py:531-622`（校验和 `549-552`、`hi` 掩码 `588-607`、`_pad_poles` 调用 `613`） |
| 贴图两条路径 | `render.py:1577-1648`（grid `1590-1615`、整域 `1617-1648`） |
| marching squares | `render.py:647-709` + `draw_contours:712-756` |
| 流线/洋流/风羽/粒子 | `render.py:981-1179`、`1182-1224` |
| 缓存清单 | `render.py:135-141`、`436-437`、`643-644`、`801-802`、`905`、`956-964` |
| `fill` 平滑填补 | `fill.py:29-80`（距离变换铺底 `51-53`、Laplace `72-78`） |
| `fill` 三类判据 | `fill.py:115-133`、`136-173` |
| `fill` 生产消费者 | `simulator/simcore/daily.py:152-162` → `envmodel/solver.py:201` |
| b-deck 解析（含两种方言） | `import_tc.py:63-109`（方言 `86-100`） |
| A 模式注入与消费 | 产 `import_tc.py:127-152`；消费 `core.py:3537-3562` |
| 探针脚本 | `/mnt/g/tmp/adv_b.py`、`adv_c.py`、`adv_d.py`、`adv_e.py`、`adv_g.py`、`census.py` |
