# 17 W1 实施：启动路径修复（W1 六文件）

> 作者：teammate `app-sim`（W1）｜ 任务 `task-16` ｜ 2026-10-02
> 性质：**实施**（改源码）。诊断依据：`13_perf_startup.md`（W1 启动诊断）、`16_perf_diagnosis.md`。
> 纪律：每个修复**独立完成、独立验证**；改前/改后记 SHA256；**判据用确定性指标**（模块在不在 `sys.modules`、调用次数、模块数），**不用计时**（本机墙钟极差 325%、P/E 核 1.90×）。
> 回退：`output/exploration/_backup/README_ROLLBACK.md`（单文件 `cp` + `sha256sum -c SHA256_BEFORE.txt`）。

---

## 一句话定位

本轮把启动路径上**四处"白付的成本"**拆掉：一行把 OpenCV/Pillow 拖进启动的死 import、三条模块级 `smcy_icon`/`particle_effect` 依赖、每次加载进度都 `flip()` 的 207 次重绘、以及构造期就把 SimCore 内核 import 进来的初始化。三处只动"**何时做**"，不动"做什么"。

---

## 1. 改动清单（最终状态）

| # | 文件 | 改动 | 判据（确定性） | 状态 |
|---|---|---|---|---|
| 1 | `app/ty_sim.py` | 删除第 39 行死 import `from .particle_effect import preload_particles` | `app.ty_sim` 不再有该属性 | ✅ 已落地｜**可观测收益 0**（§2.1） |
| 1R-a | `app/playback_ctrl.py` | `:10` 的 particle_effect 四名字改为函数内 import（`_check_ts_note` / `_check_ri_effect`） | `particle_effect` 不再在启动期加载 | ✅ 已落地并验证 |
| 1R-b | `app/summary_effect.py` | `:18` 的 `smcy_icon` 两名字改为函数内 import（`available_for` / `draw`） | 同上 | ✅ 已落地并验证 |
| 1R-c | `app/statistics/heatmap.py` | `:8` 的 `PIL.Image` 移进懒加载的 `_load_heat()` | `PIL` 不再在启动期加载 | ✅ 已落地并验证 |
| 6c | `app/name_pos_dialog.py` | `:52` 的 `from .smcy_icon import _TOTAL_FRAMES` 改为**首次使用缓存**（`_smcy_total_frames()`） | 该模块不再在 import 期拉 `smcy_icon` | ✅ 已落地并验证 |
| 4 | `main.py` | 新增模块级 `_make_load_progress()` 节流工厂，替换内联 `_on_load` | 207 次回调 → 重绘 **2–12 次**（原 207） | ✅ 已落地并验证 |
| 10 | `app/ty_sim_mixins/sim_mode_mixin.py` | `_init_sim_mode` 的内核 import 全部推迟到 `_sim_ensure_kernel()` | 构造后 `simcore.core` 不在 `sys.modules` | ✅ **已落地并验证达标** |

**SHA256（改后；改前见 `_backup/SHA256_BEFORE.txt`）**

| 文件 | 改后 |
|---|---|
| `app/ty_sim.py` | `780afd157b09fc3e3506e67eb764f4b3480de98f16e298bda3a034a174ec446f` |
| `main.py` | `d8b46d3807d56e9f13be173c593f02307d782e0c19bfafd5cc3a998111b43243` |
| `app/playback_ctrl.py` | `05d0e44066ab9239c070c114a09be52e31a46d4471c7e8cb55aa843cf21f9652` |
| `app/summary_effect.py` | `3953d8752cf353080123ea607bad827049d2862a4625c11330cc0d3457b9d33b` |
| `app/statistics/heatmap.py` | `8c0a7f9155f0d98f69fc9cb2268bd601befd4d9e4834865bfa44b2466ad0df83` |
| `app/name_pos_dialog.py` | `c2ffb92ec177ffe58bf172eeb9968426cb670996f89f76ed44193e0fe9de50d4` |
| `app/ty_sim_mixins/sim_mode_mixin.py` | `c18884f7874c554cfa9187547f515ec49d56366ebba56b01e169c1151700c689` |

**总判据（`import app.ty_sim` 后的 `sys.modules`，独立进程实测）**

| 指标 | 改前（基线） | 改后（全部 6 文件） |
|---|---|---|
| `cv2` | True | **False** ✅ |
| `PIL` / `PIL.Image` | True | **False** ✅ |
| `app.particle_effect` 已加载 | True | **False** ✅ |
| `app.smcy_icon` 已加载 | True | **False** ✅ |
| `sys.modules` 条目数 | **422** | **362**（**−60**）✅ |

---

## 2. 逐项：证据 → 改法 → 判据

### 2.1 修复 1：删死 import（`app/ty_sim.py:39`）—— 可观测收益 0（如实记录）

**证据（静态，全仓 grep）**：`preload_particles` 只有"定义"（`app/particle_effect.py:70`）与"唯一 import"（`app/ty_sim.py:39`），**零调用**；真正在用是 `app/ty_sim.py:238` 的 `from .particle_effect import iter_preload_particles`（函数内）。而 `particle_effect.py:8,10` 有 `import cv2` / `from PIL import Image`。

**改法**：删掉第 39 行，原地留注释说明（含"真正的粒子预热是函数内 import、保持不变"）。

**判据（独立进程，`G:\tmp\probe_import_heavy.py`）**

| 指标 | 改前 | 只做修复 1 后 |
|---|---|---|
| `app.ty_sim` 有 `preload_particles` 属性 | True | **False** ✅ |
| `'cv2' in sys.modules` | True | True ❌ |
| `'PIL' in sys.modules` | True | True ❌ |
| `sys.modules` 条目数 | 422 | 422 |

**⇒ 本修复的可观测收益 = 0。** 死代码清理本身是对的（保留，不回退），但 `16_perf_diagnosis.md` 把它列为"1 行改动、收益最直接"的**判断已被本轮实测证伪**：另有三条模块级路径仍在拖 cv2/PIL。

### 2.2 修复 1R + 6c：把 cv2/PIL 移出启动路径

**归因证据（每模块一个独立子进程，`G:\tmp\probe_cv2_attrib.py`）**

| 被导入模块 | cv2 | PIL | 说明 |
|---|---|---|---|
| `app.particle_effect` | True | True | cv2/PIL 的源头（`:8,10`） |
| `app.playback_ctrl` | True | True | `:10` 模块级 import particle_effect ⇒ **已修** |
| `app.dialog_manager` | True | True | 经 summary_list / name_pos_dialog 等 |
| `app.statistics.heatmap` | False | True | `:8` PIL ⇒ **已修** |
| `app.summary_effect` | True | False | `:18` → smcy_icon ⇒ **已修** |
| `app.smcy_icon` | True | False | `:10` `import cv2`（源头之一） |

**改法（逐文件 read 确认使用点后改；只移动 import 时机，不改逻辑）**
- `app/playback_ctrl.py`：四名字的使用点集中在 `_check_ts_note`（`:439/:443`）与 `_check_ri_effect`（`:478/:480`），已各自函数内 import。**不在逐帧路径**：两者只由 `_check_particle_effects`（`:426`）调用，而该调用点在 `:155/:181` 的 `if typhoon.ci != prev_ci` 里（越过报点才触发）。该文件 `:193` 本来就有 `from .summary_effect import TyphoonSummary` 的函数内 import，风格一致。
- `app/summary_effect.py`：`has_summary_video` → `available_for()`（每次创建摘要一次）；`get_summary_frame` → `draw()`（只有摘要条在动画时才走到；`sys.modules` 命中后仅一次 dict 查找）。
- `app/statistics/heatmap.py`：`PILImage` 唯一使用点在 `_load_heat()`，而它本身就是**懒加载 + 记忆化**（`:28-29` 守卫 + 失败哨兵 `_HEAT_H < 0`）⇒ 把 import 放进去，首次真正要用色表时才加载。
- `app/name_pos_dialog.py`（6c/#3）：`_TOTAL_FRAMES` 是**纯 int 常量**（`smcy_icon.py:36 = 1800`），**唯一使用点在 `_draw_preview()`（逐帧）** ⇒ 既不能留模块级、也不适合裸函数内 import（会每帧执行）。改为**首次使用时取一次并缓存**的模块级访问器 `_smcy_total_frames()`，启动不 import、之后零额外开销。

**判据（独立进程，`G:\tmp\probe_import_heavy.py`）**

| 指标 | 改前 | 修复 1 后 | **1R 三文件后** | **1R+6c 全部后** |
|---|---|---|---|---|
| `'PIL' in sys.modules` | True | True | **False** ✅ | **False** ✅ |
| `'cv2' in sys.modules` | True | True | True | **False** ✅ |
| `app.particle_effect` 已加载 | True | True | **False** ✅ | **False** ✅ |
| `sys.modules` 条目数 | 422 | 422 | **396** | **362**（−60）✅ |

**`cv2` 的最后一条路径 = 6c（本报告完成）**：基线时 `cv2` 由 4 条模块级路径拖入（`playback_ctrl.py:10`→particle_effect、`summary_effect.py:18`→smcy_icon、`name_pos_dialog.py:52`→smcy_icon、`_draw_icon_mixin.py:18`/`summary_list.py:13`→smcy_icon）。前两条由 1R 修掉，最后一条由 6c 修掉；`_draw_icon_mixin.py:18` 与 `summary_list.py:13` 在同期由 **W2/app-feat** 改成函数内 import（现为 `_draw_icon_mixin.py:827/843/887/1211`、`summary_list.py:411`，原行位置留了"不要再写模块级"的警示注释）。
**⇒ 合并后判据达标：`cv2` 与 `PIL` 都不在启动期 `sys.modules`。**

### 2.3 修复 4：`on_load_progress` 的 flip 节流（`main.py`）—— 已达标

**证据**：`app/data_repo.py:152-158` 每解析一个文件回调一次；本仓库 `typhoon/` 有 **207** 个 `.dat/.txt`（实测 `parse_typhoon_file` 调 207 次）⇒ 原来 **207 次 `font.render` + 414 次 blit + 207 次 `flip`**。

**改法**：内联闭包 → 模块级工厂 `_make_load_progress(screen, min_interval_ms=50)`；调用点 `_on_load = _make_load_progress(screen)`。
**契约**：① 距上次重绘 <50 ms 且未加载完 → 跳过；② **`done >= total` 一定重绘**（末次不丢）；③ `total <= 0` 一定重绘；④ 首次一定重绘。
**为什么提成模块级**：内联闭包无法在不运行 `main()` 的前提下测试；提出来后判据可**确定性计数**。

**判据（`G:\tmp\probe_flip_throttle.py`）**

| 场景 | 回调次数 | 重绘 | flip | 期望 |
|---|---|---|---|---|
| 合成 快时钟 0.2 ms/文件 | 207 | **2** | 2 | 首次 + 末次 |
| 合成 慢时钟 60 ms/文件 | 207 | **207** | 207 | 间隔 >50 ms ⇒ 不节流 |
| 边界 首次/间隔1ms/末次 | 3 | **2** | 2 | 末次**必须**画 ✔ |
| 边界 `total=0` ×2 | 2 | **2** | 2 | 不受节流 ✔ |
| 边界 `min_interval=0` | 207 | **207** | 207 | 等价关闭 ✔ |
| **真实路径 `TySim(on_load_progress=…)`** | **207**（真实） | **2** | 2 | 对照未节流 207 |

**按文件耗时换算**（`G:\tmp\probe_throttle_rates.py`）：0.2/1.0/**2.6(本仓库均速)**/5/10/30/60 ms 每文件 → 重绘 **2/6/12/22/43/104/207** 次。⇒ 本仓库量级 **207 → 12**（−94%），进度条仍按 ≥50 ms 刷新。
**回归**：`tests/test_ace_engine.py` → **19 passed in 0.61s**（基线一致）。

### 2.4 修复 10：内核延迟到首次进模拟模式（`sim_mode_mixin.py`）

**证据**：`_init_sim_mode` = **675 ms**（绑核 0-7）/ 1251 ms（未绑核）；构造后 `simcore.core`、`simcore.render` 均在 `sys.modules`；其中 2 次 `.npz` open 来自 `latest_real_start`。
**blast radius（改前 grep 全仓，已确认）**：
- `_sim_start_dt` / `sim_start_dt`：只在本文件（`:194/:212/:223` 构造、`:456-458` property、`:836/:889` `_sim_reset`、`:914` `_sim_set_start`）+ 两个对话框读 **property**（`sim_settings_dialog.py:311`、`sim_time_dialog.py:58`）。
- `sim_env_step_h`：本文件 + `sim_settings_dialog.py:98`、`sim_time_dialog.py:125`、`tools/_probe_sim_recon.py:54`。
- `sim_mode_override`：本文件 + 两个模拟面板对话框。
- **`sim_month`：全仓只写不读**（唯一两处写入 `:223/:915`；`season_ctrl` 的 `_reset_sim_month_tracker` 是无关的另一个东西）⇒ 独立发现，见 §6。

**改法（只动"构造期这一个点"；该文件 32 处既有函数内 import 一律未动）**
1. `_init_sim_mode` 去掉三处内核触碰：`self._coerce_start(_cfg_start)`、`from simulator.simcore.core import snap_env_step`、`from simulator.simcore import render` 注入 `set_label_fn`。
   - `self._sim_start_dt = None`（惰性）、`self._sim_kernel_ready = False`
   - `sim_env_step_h` 先取 cfg 原值（`float(...)`，异常回落 6.0），**吸附推迟**
   - `sim_mode_override` 的 dict 归一化**留在构造期**（内核无关）
   - `self.sim_month = None`
2. 新增 `_sim_ensure_kernel()`（幂等，`_sim_kernel_ready` 短路）：import `simcore.core` → `_coerce_start` 填 `_sim_start_dt` → `snap_env_step` 吸附 `sim_env_step_h` → 设 `sim_month` → import `simcore.render` 注入 `set_label_fn`。**内核 import 失败时降级返回且不置位**（与原来 try/except 的降级语义一致，只是不再抛出去）。
3. `sim_start_dt` property 改为**惰性解析 + 缓存**（`_sim_start_dt` 为 None 时先 `_sim_ensure_kernel()`，再兜底 `_coerce_start`）。
4. `_sim_reset()` 开头调用 `self._sim_ensure_kernel()`（这是"进入模拟模式 / 按 R 重置"的必经入口）；`V.init({'start': self.sim_start_dt, …})` 与重置提示改用它。

**行为等价性论证（改前 vs 改后）**
- `sim_env_step_h` 未吸附的窗口期 = "构造完成" → "首次进模拟模式"。该值的两个读者（`sim_settings_dialog._get('_env_step')`、`sim_time_dialog` 的档位高亮）**都只能在模拟模式里打开** ⇒ 到达时必然已吸附。`_sim_reset` 自己用的是吸附后的值。
- `latest_real_start` 的读取时机从"构造"挪到"首次进模拟模式"——**这是本修复的目的**（用户不进模拟模式就不该付）。
- `sim_month` 只写不读 ⇒ 置 None 无观察者。

**判据（`G:\tmp\probe_kernel_defer.py`，800×600 小窗口省内存）—— ✅ 达标**

| 检查点 | 结果 |
|---|---|
| `import app.ty_sim` 之后 | `core=False render=False`（本来就如此） |
| **`TySim()` 构造之后** | **`core=False render=False`** ✅（改前 True —— 这是本修复的判据） |
| 构造后非内核状态仍可用 | `sim_layers=['track']`、`sim_seed=20250801`、`sim_env_step_h=6.0`、`_sim_start_dt=None`、`_sim_kernel_ready=False` |
| 读 `sim_start_dt` property | 解析为 `2026-02-28 00:00:00`，此后 `core=True render=True`（惰性路径工作正常） |
| `_sim_reset()` 进入模拟模式之后 | `core=True render=True`；`sim_v4 start=2026-02-28 00:00:00, envStepH=6.0, t=0.0`；`env` 23 键；`particles.n=3000` |
| 功能正常（内核真能用） | `V.sim_step(v4, 6.0, max_inner=1)` → `t=6.0`，`stepCostMs=898.2`；`_sim_kernel_ready=True`、`_sim_start_dt=2026-02-28 00:00:00`、`sim_month=2` |

**回归**
| 测试 | 结果 |
|---|---|
| `tests/test_ace_engine.py` | **19 passed**（基线一致） |
| `tests/test_fx_sound.py`（与 1R 特效相关） | passed（合计 46 passed，0.85 s） |
| `tests/test_sim_time_model.py` + `tests/test_render_layers.py` + 上述两文件 | **74 passed in 92.49 s** |
| `tests/test_sim_mode.py`（与修复 10 最相关） | 见 §2.6（单文件后台运行） |

### 2.5 ⚠ 环境事实：bash 服务多次中断（已恢复，验收已完成）

本轮**两次**出现 bash 通道整体不可用：每条命令（含 `echo`/`true`）都返回
```
~p��'`Ee�� ... Bash/Service/E_UNEXPECTED
```
- 第 1 次：约 3 分钟；恢复后立即重跑修复 1R 的判据（数据即当时取得）。
- 第 2 次：从修复 10 的判据执行前开始，持续约数十分钟；**恢复后我补跑了 AST/判据/回归，Lead 也独立复现**（见下）。
- 影响（已消解）：修复 10、6c 的判据与最后一次回归当时没跑成；`read`/`grep`/`edit`/`write` 全程不受影响，故代码与报告始终落盘。
- Lead 侧统计：本轮 `Bash/Service/E_UNEXPECTED` 共出现 **3 次**（W1 2 次、app-feat 1 次、sim-numerics 1 次、sim-data 1 次）⇒ 已记为**环境事实**。
- （另据 Lead：sim-run 上一轮也遇到过同类现象，>20 min 的后台作业被杀。）

**恢复后补齐的验证**
- 我自己：`probe_kernel_defer.py` → **RESULT OK**（§2.4 表）；`probe_import_heavy.py` → `cv2/PIL = False`、`sys.modules 362`；AST 检查 5 个文件 OK（`name_pos_dialog.py`/`sim_mode_mixin.py` 由 `probe_kernel_defer.py` 成功导入间接证明可解析）。
- **Lead 独立代跑**（不同探针、独立进程）：
  ```
  --- import 后 ---      cv2 = False | PIL = False | simcore.core = False
  --- TySim() 构造后 --- cv2 = False | PIL = False | simcore.core = False
  n_modules = 377
  ```
  ⇒ 与我的结论一致（模块数差异来自"计数前已 import 了什么"：我裸 `import app.ty_sim` 后 362，Lead 的探针在计数前还 import 了 `AppConfig`/`pygame`/`set_mode` 等 ⇒ 377。两者都相对基线 422 显著下降，**结论不受影响**）。
- 回归（Lead 在改后代码上跑）：`test_ace_engine + test_panel_layer_row + test_sim_settings + test_control_panel + test_map_resize + test_render_geom + test_ocean_area` → **77 passed / 44.23 s**；`test_landfall_view_invariance + test_fx_sound + test_render_layers` → **44 passed / 20.80 s**。
- 我自己补跑：`tests/test_sim_mode.py` → **28 passed in 113.07 s**（与修复 10 最相关的单文件，通过）。

---

## 3. 核心机制详解（为什么这样改是安全的）

- **修复 1**：纯删除 + 注释；被删名字全仓无人读。
- **修复 1R / 6c**：**只移动 import 时机**。三个文件的新位置都在"首次真正用到"的方法体内；`name_pos_dialog` 因使用点是逐帧，改为"首次取值即缓存"的访问器（等价于把 `import` 提到首次调用，之后零开销）。
- **修复 4**：把"每文件重绘"改成"每 ≥50 ms 重绘 + 末次必绘"；`_draw_splash` 内部本就 `try/except` 包住，异常不影响加载。
- **修复 10**：构造期只保留内核无关状态；内核相关三件事收进一个幂等入口。**没有改任何既有函数内 import**（那 32 处是正面例子），也没有改 `_coerce_start`/`_sim_set_start`/`_sim_set_env_step`（它们本就在模拟模式路径上）。

**已静态复核（bash 不可用期间用 grep/read 完成）**
- `sim_mode_mixin.py`：**模块级 `from simulator` = 0 处**；内核触碰点仅剩 `:286/:300`（都在 `_sim_ensure_kernel()` 的 try 内）与 `:531`（`_coerce_start`，静态方法、本来就惰性）；`:891` 起全是既有惰性点。
- `name_pos_dialog.py`：模块级 `_TOTAL_FRAMES` 已无；访问器定义在唯一调用点（`:575`）之前；`smcy_icon` 只在访问器内部 import。
- `datetime` 模块级 import 仍被 `:1421` 使用 ⇒ 未产生新的未用 import。

---

## 4. 输入输出资产

本轮**不新增/不删除**任何资产，不改任何文件格式。受影响的"读取时刻"：

| 资产 | 改前何时用 | 改后何时用 |
|---|---|---|
| `assets/SMCY/mainicon/*.mp4`（cv2） | 预热第 3 帧起 | 不变（本修复只挪 **import 时机**，打开时机本就在预热期） |
| `assets/SMCY/resource/ace-heat.png`（PIL） | 首次画热力图 | 不变（`_load_heat()` 懒加载，`:28-29` 守卫） |
| `simulator/simcore/*.npz`（模态表） | **构造期**（`_init_sim_mode`） | **首次进模拟模式**（修复 10 的目的） |
| `config.json` | 构造期读 2 次 | 不变（本次未动 `_apply_dpi`） |

探针（均在工作区外 `G:\tmp\`）：`probe_import_heavy.py`、`probe_cv2_attrib.py`、`probe_flip_throttle.py`、`probe_throttle_rates.py`、`probe_kernel_defer.py`、`w1_astcheck.py`。

---

## 5. 如何运行 / 如何验证（含 bash 恢复后要复跑的两条）

```bash
cd /mnt/g/气象/Typhoon-sim
# 全路径写(本会话 $VAR 展开不可靠)
PY=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# ① 语法检查(所有被改文件)
python3 G:/tmp/w1_astcheck.py

# ② 修复 1/1R/6c 判据(独立进程; 目标: cv2=False PIL=False —— 需 app-feat 的 #1/#2 完成后才可达)
$PY G:/tmp/probe_import_heavy.py

# ③ 1R 归因(逐模块独立子进程)
$PY G:/tmp/probe_cv2_attrib.py

# ④ 修复 4 计数(假时钟, 确定性)
$PY G:/tmp/probe_flip_throttle.py
$PY G:/tmp/probe_throttle_rates.py

# ⑤ 修复 10 判据(⚠ bash 恢复后**必须跑**; 800x600 小窗口省内存)
$PY G:/tmp/probe_kernel_defer.py

# ⑥ 回归(单文件, 禁全量)
$PY -m pytest tests/test_ace_engine.py -q --no-header        # 基线 19 passed
$PY -m pytest tests/test_fx_sound.py -q --no-header          # 与 1R(特效) 相关
$PY -m pytest tests/test_sim_mode.py -q --no-header          # 与修复 10 相关(慢, 单文件)
$PY -m pytest tests/test_sim_time_model.py -q --no-header    # 与 sim_start 惰性化相关
```
**内存纪律**：本机可用物理内存一度只剩 **662–768 MB**，而 `TySim()` 构造要解码 **445 MB** PNG。所有构造类判据用 **800×600** 窗口、单进程串行（不与他人并发）。

---

## 6. 观察到的问题 / 风险 / 不一致

| 级别 | 内容 | 证据 |
|---|---|---|
| **严重（判断被证伪）** | 诊断把"删 `ty_sim.py:39`"列为"1 行改动、收益最直接"；实测**可观测收益 = 0**（另有 3 条模块级路径）。真正的收益来自 1R 的三个文件。 | §2.1 表 |
| ~~严重（未验收）~~ **已消解** | 修复 10 的判据一度因 bash 中断没能跑；**bash 恢复后已补齐**：我的 `probe_kernel_defer.py` **RESULT OK** + Lead 独立复现（构造后 `simcore.core=False`）+ `test_sim_mode.py` **28 passed**。 | §2.4 / §2.5 |
| **一般（新发现）** | `self.sim_month` **全仓只写不读**（唯一两处写入 `sim_mode_mixin.py:223/:915`，无任何读取点；`season_ctrl._reset_sim_month_tracker` 是无关的另一套）。属可删的遗留状态；本次只把它置 `None`（不删，避免越界）。 | §2.4 blast radius |
| **一般** | `main.py` 的 `_on_load` 内联闭包无法被测试，正是"每文件 flip 207 次"长期没被发现的原因；已提成模块级工厂。 | §2.3 |
| **一般** | 1R 的代价：首次调用多一次 import（`sys.modules` 未命中）。已确认调用点不在逐帧路径 —— 唯一"逐帧"案例（`name_pos_dialog._draw_preview`）改用**首次取值缓存**处理。 | §2.2 |
| **建议** | `tools/_probe_startup_timing.py` 漏测 `on_load_progress`（诊断 §2.3 项 h）。建议在工具里补上，否则真机启动耗时会被系统性低估。 | `13_perf_startup.md` §2.3 |
| **环境事实** | **bash 通道本轮两次不可用**（`Bash/Service/E_UNEXPECTED`，连 `echo` 都失败）；`read/grep/edit/write` 不受影响。已在 §2.5 记录对验收的影响。 | §2.5 |
| **不一致** | `_backup/SHA256_BEFORE.txt` 的 git 基线记"535 行脏改动"（` M` 280 / `??` 226）⇒ **任何回退都必须以 SHA256 为准**，不能靠 `git diff` 判断。 | `README_ROLLBACK.md:46` |

---

## 7. unresolved（未能确证）

1. ~~**修复 10 的判据未跑**（bash 中断）~~ → **已验达标**：我 `probe_kernel_defer.py` **RESULT OK**（构造后 `simcore.core=False`；进模拟模式后 True 且 `sim_step` 推进到 `t=6.0`）；**Lead 独立复现**同结论（`TySim()` 构造后 `simcore.core = False`）。
2. ~~**6c（`name_pos_dialog.py`）的判据未跑**~~ → **已验**：`import app.ty_sim` 后 `cv2=False`、`smcy_icon` 不再加载（Lead 独立复现 `cv2=False`）。
3. ~~**`cv2` 的最终判据未达标**~~ → **合并后达标**：W2/app-feat 的 `_draw_icon_mixin.py`、`summary_list.py` 与本报告的 6c 一起清空最后三条模块级路径；`cv2`/`PIL` 均 **False**。
4. **`sim_env_step_h` 在"构造完成→首次进模拟模式"窗口内的值**：本仓库 `config.json` 是 `6.0`（本身就在 `ENV_STEPS` 内），所以该窗口内的"未吸附"值恰好等于吸附值、无差异；但若用户手改成 `12.0`，窗口期内 `SimTimeDialog` 的高亮会短暂"不对应任何档"——该对话框只可能在模拟模式内打开，故判为不可观察（**未实测**）。
5. **修复 10 之后 `latest_real_start` 的耗时是否会转移到首次进模拟模式的卡顿里**：按设计是（那是"用户主动进入模拟模式"的时刻，可接受），但**未测**该时刻的耗时。
6. **`import app.ty_sim` 的模块数从 422 → 396 的 26 个模块清单**未逐个列出（只确认了 `app.particle_effect` 及其 cv2/PIL 一条链）。
7. **`bash` 中断的根因**：现象与 sim-run 上一轮一致（长作业被杀），但无日志可查 ⇒ 无法确证是本机资源、沙箱服务还是 harness 的问题。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 被删的那行 + 为什么 | `app/ty_sim.py`（`script_dialog` 之后、`input_handler` 之前的注释块） |
| 粒子预热真正的入口（**不要动**） | `app/ty_sim.py` 的 `_warmup_jobs()`（函数内 `from .particle_effect import iter_preload_particles`） |
| cv2 的源头与剩余拖入路径 | `app/particle_effect.py:8,10`、`app/smcy_icon.py:10`；剩余：`_draw_icon_mixin.py:18`、`summary_list.py:13`（均 app-feat） |
| 已修的三条拖入路径 | `app/playback_ctrl.py`（`_check_ts_note`/`_check_ri_effect`）、`app/summary_effect.py`（`available_for`/`draw`）、`app/statistics/heatmap.py`（`_load_heat`） |
| 6c 的惰性访问器 | `app/name_pos_dialog.py` 的 `_SMCY_TOTAL_FRAMES` / `_smcy_total_frames()`（调用点 `_draw_preview`） |
| 修复 4 的实现 | `main.py` 的 `_LOAD_PROGRESS_MIN_MS` / `_make_load_progress()`；调用点 `main()` 内 `_on_load = _make_load_progress(screen)` |
| 进度回调源（207 次） | `app/data_repo.py:152-158` |
| 修复 10 的实现 | `app/ty_sim_mixins/sim_mode_mixin.py`：`_init_sim_mode`（内核无关部分）+ **`_sim_ensure_kernel()`** + `sim_start_dt` property + `_sim_reset()` 开头 |
| 回退与校验 | `output/exploration/_backup/README_ROLLBACK.md`、`sha256sum -c …/SHA256_BEFORE.txt` |
| 判据探针 | `G:\tmp\probe_import_heavy.py`、`probe_cv2_attrib.py`、`probe_flip_throttle.py`、`probe_throttle_rates.py`、`probe_kernel_defer.py`、`w1_astcheck.py` |
