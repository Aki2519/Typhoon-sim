# 21 四组修复的独立验证（BEFORE/AFTER 确定性指标 + 回归）

> 第四轮 · 负责人 `sim-run`（W5 独立验证方）· 唯一可写文件 `output/exploration/21_fix_verification.md`
> 探针全部在 `G:\tmp\w5\`（工作区外）；BEFORE 树在 `G:\tmp\perf_before\`（工作区外）。
> **本人不改任何源码。** 全篇区分 **【实测】/【推断】/【未能验证】**。

## 一句话定位

**【实测】** 我先在**工作区外**把"改动前"冻结成一棵可跑的树（`G:\tmp\perf_before\`），
再用同一份确定性探针在**工作区外的 BEFORE 树**上跑出基线 —— 这样验证不依赖
"改之前正好跑到"，也不会被我自己的探针写入污染。

**阶段 1（✅ 已完成）**：
① BEFORE 树建立在工作区外并与 `_backup/SHA256_BEFORE.txt` **对账 12/13 逐位 OK**；
② 验收台搭好（5 个探针 + 15 文件回归 + 自动对比脚本 `compare.py`，其"自己比自己"自检给出
**像素差 0.000 / 物理指纹相同 / 回归无变红**，证明机器本身没坏）；
③ BEFORE 基线**全部冻结**（9 条判据 + 物理确定性门 + 回归 15/15 PASS 178 passed）。

**阶段 2（✅ 已完成，Lead 2026-10-02 放行）**：静态核验（13 文件 sha 与清单逐个对上，`render.py` 逐位未改）
→ `w5_all → w5_mainrun → w5_det → run_regression → w5_landfall/w5_land → compare.py` **依次单进程**跑完。
**结果（§6.1）**：**PASS 9 项**（W1-①④⑩、W2-③⑤、W3-⑧、W3-⑧c、W4-⑦、物理门、回归）、
**未达判据 1 项**（W2-②：驱逐 548→204，但未到 0）、**不适用 1 项**（W3-⑥，有证据的不改）、
**信息项 1 项**（`n_mods` 712→388）。**没有"零可测变化"的改动。**
**W3-⑥ / W4-⑦ 按 Lead 解释记为「有证据的不改 / 暂缓」= 不适用；W4 只认常驻、不判峰值。**

> ⚠ **开工时发现的事实**（写在这里以免被当成 AFTER 结果）：**工作区此刻已经是"改了一部分"的状态**。
> 例：`app/ty_sim_mixins/_draw_icon_mixin.py` 的 `_name_shadow_cache` 现在是 `...1024`，
> 而改动前是 `...256`（`_backup/README_ROLLBACK.md` 与诊断报告 §3 #2 都记 256）；
> `app/smcy_icon.py` 的 `_MAX_STREAMS` 现在是 **12**，改动前是 **20**。
> ⇒ **绝不能用"当前工作区"当 BEFORE 臂**，这正是本任务要求先冻结 BEFORE 树的原因。

---

## 1. 清单与职责

### 1.1 BEFORE 树怎么来的（可复现）

| 步 | 命令 | 结果 |
|---|---|---|
| 1 | `git archive 9c3f657eef517f35e4517271a5b6b6471e1ae13e app tests tools simulator \| tar -x -C /mnt/g/tmp/perf_before` | **26 MB**，已跟踪文件的**改动前状态** |
| 2 | `git archive 9c3f657… assets map typhoon config.json icon.ico README.md main.py \| tar -x -C …` | 累计 **155 MB** |
| 3 | **`mkbefore.py SRC DST "2026-10-02 13:00:00"`** | 补齐"工作区有、提交里没有"的**未跟踪**文件，**只补 mtime < cutoff 的**（cutoff = `_backup/` 建立前）。查出并补上的关键项：`app/ty_sim_mixins/sim_step_control.py`、`simulator/env/model/*.npz`、`map/heightmap.png`、`map/land2.png` 等；**mtime ≥ cutoff 的只有 1 个**（`app/name_pos_dialog.py`，14:12:38 —— 本轮新建）⇒ **正确排除** |

> ⚠ **`git stash create` 不含未跟踪文件** —— 这是本环节的第一个坑：
> 直接 `git archive` 出来的树**跑不起来**（`import app.ty_sim` 就 `ModuleNotFoundError:
> app.ty_sim_mixins.sim_step_control`）。第 3 步的 mtime 过滤是把它补对的关键：
> 既补齐"旧的新增文件"，又不把"本轮新建文件"混进 BEFORE。

**【实测】BEFORE 树确实是"改动前"（内容级核验，与诊断报告的行号/取值逐字吻合）**：

| 位置 | BEFORE 树（`G:\tmp\perf_before`） | 当前工作区 | 诊断报告引用 |
|---|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py:352` | `_name_shadow_cache = SurfaceCache("name_text", 32MB, **256**)` | `:379`，**1024** | `16_perf_diagnosis.md` §3 #2 记 `:352` / 256 条 |
| `app/smcy_icon.py:271` | `_MAX_STREAMS = **20**` | `:286`，**12** | §3 #11 记 `_MAX_STREAMS=20` |
| `app/ty_sim_mixins/_draw_icon_mixin.py:846` | `_purple_frame_cache = SurfaceCache("purple_frame", 96MB, 240)` | 同值（有意未改，见 §6.0） | §3 #6 记 96 MB |

⇒ 两棵树的差异是**真实的改动前后差异**，不是复制误差。

**【实测】BEFORE 树的位级核验（与 `_backup/SHA256_BEFORE.txt` 对账）**：

```
$ cd /mnt/g/tmp/perf_before && sha256sum -c …/output/exploration/_backup/SHA256_BEFORE.txt
app/ty_sim.py: OK             main.py: OK
app/ty_sim_mixins/_draw_icon_mixin.py: OK    app/settings.py: OK
app/constants/fonts.py: OK    simulator/simcore/render.py: OK
app/smcy_icon.py: OK          app/map_mgr.py: OK
… 共 12 个 OK
sha256sum: app/name_pos_dialog.py: No such file or directory   ← 唯一缺口（见下）
```

⇒ **12/13 逐位 OK**。唯一缺口 `app/name_pos_dialog.py`：

| 事实 | 值 |
|---|---|
| `_backup/SHA256_BEFORE.txt` 里记的（改动前） | `0ea67835d17774e7c59c98da1b232ddbeba924b9dabb1cc6f7d0ebe4cc928037` |
| 工作区当前 | `c2ffb92ec177ffe58bf172eeb9968426cb670996f89f76ed44193e0fe9de50d4`，mtime **14:12:38** |
| `_backup/` 里有没有它的副本 | **没有**（只有 8 个 .py 副本，不含它） |
| 悬空提交里有没有 | **没有**（它是未跟踪文件） |
| 对本次验证的影响 | **无** —— BEFORE 树里只有 `settings.py:1746` 在函数内（点"调整名字位置…"时）才 import 它；我的 11 类判据与 15 个回归文件都**不触碰该路径**（15/15 全 PASS 即证据） |
| 复原可能 | **不可复原**（改动前内容没被备份到任何地方）⇒ 记为 §7 U6 |

**【推断·给下一轮的教训】**：安全网只备份"被点名的 8 个文件"是不够的 ——
任何**在同一批里被改动的其他文件**（本例 `name_pos_dialog.py`）都没有改动前副本。
正确做法：**先对整个未跟踪集合做一次完整快照**（或 `git stash -u`），再开工。

### 1.2 验收台（探针，全在 `G:\tmp\w5\`）

| 探针 | 覆盖判据 | 说明 |
|---|---|---|
| `w5_all.py TREE TAG` | W1-①④⑩、W2-②③⑤、W3-⑥⑧、W4-⑦ | **单进程总探针**（构造 1 次 `TySim`；import 状态在 import 之前测） |
| `w5_mainrun.py TREE TAG` | W1-④ 的**真实** flip 次数 | 跑真的 `main.py` 启动路径，在 `TySim.__init__` 返回瞬间抛 `BaseException` 停下；`save_config` 被换成 no-op ⇒ **不写文件** |
| `w5_det.py TREE TAG` | **物理确定性门**（跨树必须逐位相同） | 同质 `sim_step`：ufunc 调用/全网格/元素数 + `setprofile` C 调用 + 缓存规模 + 状态指纹 |
| `run_regression.py TREE TAG` | 回归（15 个快文件，逐文件 `timeout 300`，禁全量） | 输出 `reg_<TAG>.json` |
| `w5_landfall.py TREE TAG` | **追加判据**：`_landfall_cache` 的字节预算 + 单条超预算不缓存 | 用替身（stub）绕开解码 ⇒ **零大内存分配**、逐位可复现 |
| `w5_land.py TREE TAG` | **追加判据**：W4 变体 A 的**陆地图层**画质（512×288 指纹 + 逐像素差 + 海陆抽样） | 变体 A 改的正是"陆地像素放哪" |
| `run_after.py TREE TAG` | 阶段 2 驱动：**依次单进程**跑上面 4 个 | 内存纪律 |
| `compare.py B A` | **自动出 PASS/FAIL 清单**（纯确定性判据） | 输出 `compare_B_A.txt`；含像素差计算 |
| `fmt.py TAG OUT` | 把 JSON 压成可读摘要（`read` 工具会截断单行 JSON） | 辅助 |
| `mkbefore.py SRC DST CUTOFF` | 补 BEFORE 树的未跟踪文件（mtime 过滤） | 见 §1.1 第 3 步 |

> **工程只读披露**：唯一写入是 `G:\tmp\perf\` 与 `G:\tmp\w5\` 的探针与产物。仓库侧只多了
> gitignore 覆盖的 `__pycache__`（import/pytest 自动生成，30 分钟内 14 个 .pyc）。
> **一条如实披露**：跑 AFTER 期间 `config.json` 的 **mtime 变成 14:27:39**
> （`apply_settings()` 可能触发了一次 cfg 落盘），但 **sha256 与改动前逐位相同**（`6fd1b974…`）
> ⇒ **内容零变更**。**W5 未修改任何源码/配置内容**（`git status` 的 309 个已跟踪改动全部来自 W1-W4）。

### 1.3 回归清单与状态（逐文件 `timeout 300`，**禁全量**）

**BEFORE 树（`G:\tmp\perf_before`）实测 —— 15/15 全 PASS，合计 178 passed / 0 failed / 0 timeout**：

| 测试文件 | 结果 | 备注 |
|---|---|---|
| `test_ace_engine.py` | **PASS 19 passed / 0.35 s** | 与基线 19 passed **逐字吻合** |
| `test_cache_store.py` | **PASS 11 passed / 0.31 s** | W2-②③⑥ 的缓存语义 |
| `test_sim_settings.py` | **PASS 14 passed / 13.46 s** | W2-⑤ 设置 |
| `test_config.py` | **PASS 5 passed / 0.38 s** | — |
| `test_sim_step_control.py` | **PASS 20 passed / 0.34 s** | W1-⑩ 模拟模式分帧 |
| `test_sim_throttle.py` | **PASS 5 passed / 60.94 s** | W1-④（这个文件是慢的，63 s） |
| `test_map_resize.py` | **PASS 4 passed / 0.40 s** | W4 地图 |
| `test_pan_reuse.py` | **PASS 8 passed / 12.20 s** | 地图平移 |
| `test_render_layers.py` | **PASS 9 passed / 0.44 s** | 渲染层 |
| `test_seam_render.py` | **PASS 8 passed / 0.50 s** | 跨缝 |
| `test_render_geom.py` | **PASS 16 passed / 0.41 s** | 几何 |
| `test_typhoon.py` | **PASS 23 passed / 0.29 s** | 通用 |
| `test_snap_grid.py` | **PASS 5 passed / 0.41 s** | 通用 |
| `test_trackpoint.py` | **PASS 11 passed / 0.28 s** | 通用 |
| `test_spline.py` | **PASS 20 passed / 0.32 s** | 通用 |

> **排除在外的**：`test_sim_modes.py`（会超时）、`test_daily_instant.py`（单项 200 s）—— 任务书明令禁全量。
> **AFTER 必须跑同一份清单**，逐文件比 PASS/FAIL（本报告只认"BEFORE PASS → AFTER 非 PASS"为回归）。

---

## 2. 数据流 / 调用链（每条判据"量的是什么"）

| 判据 | 量的东西 | 为什么它与时间无关 | 触发点 |
|---|---|---|---|
| W1-① | `'cv2' in sys.modules` / `'PIL' in sys.modules` | 是"有没有 import"的**布尔事实** | `import app.ty_sim` 之后立刻查 |
| W1-④ | `on_load_progress` 调用次数 + `pygame.display.flip` 次数 | **调用计数** | 真 `main.py`（`w5_mainrun.py`）+ 朴素回调（`w5_all.py`） |
| W1-⑩ | `'simulator.simcore.core' in sys.modules` | 同上 | import 期 / 构造后 / 进模拟模式后 |
| W2-② | `SurfaceCache` 的 `hits/misses/evictions/items/bytes` | **缓存自带计数器**（`app/cache_store.py:37-39,106-118`） | 一帧 `sim.draw()` + 全 207 台风直接画名 |
| W2-③ | `math.cos` / `math.sin` 调用次数 | **调用计数** | 一帧 `sim.draw()` 内包一层计数 |
| W2-⑤ | `AE.ACEEngine.point_in_limit` 调用次数 | **调用计数** | `settings_dialog.apply_settings()` 调两次（有/无改动） |
| W3-⑥ | `_purple_frame_cache` 的 `hits/misses/evictions` | 缓存计数器 | 一帧前后 |
| W3-⑧ | `_MAX_STREAMS`、实际开流数、`release()` 次数、RSS 增量 | 计数 + RSS 区间 | `preload_icon_streams(tys, 1.0)` |
| W4-⑦ | `pygame.image.load` 次数、地图/陆地 `Surface` 尺寸、**512×288 重采样像素指纹** | 计数 + 尺寸 + 像素哈希 | 启动/构造期 |
| 物理门 | ufunc 调用/元素数 + `setprofile` C 调用 + 状态指纹 | **逐位可复现**（上一轮已证：跨 P/E 核两遍一致） | `w5_det.py`（解析分支同质负载） |

**【实测·上一轮结论，本轮的测法依据】**（`12_perf_method.md`）：
确定性计数器在 **P 核(c8) 与 E 核(c16)** 两遍运行**逐位相同**，且在 **3 步与 35 步**两个尺度上
每步速率完全相同 ⇒ 计数器可作硬门；而墙钟同配置两次运行差 **2.13×** ⇒ **本报告不用计时做判据**。

---

## 3. 核心机制详解 —— BEFORE 基线（**已冻结，阶段 1 完成**）

> 全部数字来自 `G:\tmp\w5\RESULT_before.json`（+ `det_before.json`）；探针与产物都在工作区外。
> **AFTER 列**见 §6.1（阶段 2 完成后填入；由 `compare.py before after` 自动生成
> `G:\tmp\w5\compare_before_after.txt`）。

### 3.1 启动/import（W1）

| 量 | BEFORE（实测） | AFTER | 判据 |
|---|---|---|---|
| `cv2` in sys.modules（`import app.ty_sim` 后） | **True** | — | 应 **False** |
| `PIL` in sys.modules（import 后） | **True** | — | 参照（cv2 的连带项） |
| `simulator.simcore.core` in sys.modules（import 后） | **False** | — | — |
| `simulator.simcore.core` in sys.modules（**TySim 构造后**） | **True** | — | 应 **False**（延迟到进模拟模式） |
| `simulator.simcore.render`（构造后） | **True** | — | 同上 |
| `len(sys.modules)`：import 前 / import 后 / 构造后 / 进模拟模式后 | **76 / 426 / 712 / 717** | — | 构造后应显著变小 |
| 真实 `main.py` 的 `pygame.display.flip` 次数 | **208** | — | 应降到个位数~十几次 |
| `on_load_progress` 调用次数（驱动器量） | **207** | — | 预期不变（节流发生在回调内） |
| 朴素回调下 `flip_direct` | **207** | — | 参照 |
| `pygame.display.update` 次数 | 0 | — | 参照 |
| `import app.ty_sim` 耗时 | 824.3 / 1351.7 ms（两次） | — | **只作区间** |
| `TySim` 构造耗时 | 4056 / 5232 / 5309 ms（三次） | — | **只作区间** |
| 进模拟模式后能否步进（功能） | **可以**（3 步后 `t=18.0`, `n_tcs=0`） | — | 必须仍可以 |
| 台风数 / 报点数 | 207 / 8491 | — | 功能不回归 |

### 3.2 帧内工作（W2-②③⑤、W3-⑥）

| 量 | BEFORE（实测） | AFTER | 判据 |
|---|---|---|---|
| `_name_shadow_cache` **全 207 直接压**：misses / hits / evictions | **801 / 771 / 548** | — | 应 0 驱逐；命中率↑ |
| 同上：`items` / `bytes` | **256（=条目上限！）/ 16.0 MB**（上限 32 MB） | — | 条目不再是瓶颈 |
| `_name_shadow_cache` 一帧（默认视角） | 3 items，一帧 miss 0 | — | 这一帧只画了 3 个名字 ⇒ **不足以判定**，故用上面"全 207 直接压" |
| `_ring_offsets` 有 `cache_info` 吗 | **False** | — | 应 True |
| `_ring_offsets` 60 个不同半径：第一轮 / 第二轮 `math.cos` 调用 | **11,492 / 11,492** | — | 第二轮应 ≈0 |
| 一帧 `math.cos` / `math.sin`（1280×720 默认视角） | 1 / 0 | — | **本场景不触发环形绘制 ⇒ 不作判据**（改用上面的直接压） |
| `_purple_frame_cache` 一帧 hits/misses/evictions | **0 / 0 / 0**（items 0，上限 96 MB） | — | 本场景**未命中该路径 ⇒ 该判据在本次场景下不可判定** |
| `apply_settings()` 第 1 次（无改动）`point_in_limit` | **25,473** | — | 应 **0**（守卫生效） |
| `apply_settings()` 第 2 次（再无改动）`point_in_limit` | **25,473** | — | 应 **0** |
| 第 3 次：**改 `hemisphere`（→ south）**后 `point_in_limit` | **25,473** | — | 应 **> 0**（守卫必须放行） |
| 第 3 次的**功能效果**（`sim.hemisphere` 真的变了吗） | **True（→south）** | — | 必须仍为 True |
| 设置对话框属性名 / `activate()` | `dialog_mgr.sd` / `settings_activated=true` | — | — |
| `apply_settings()` 三次返回值 | 都 **True** | — | — |

> **BEFORE = 25,473 与 `16_perf_diagnosis.md` §3 #5 的数字逐字吻合** ⇒ 探针口径对上了。

**§3.2b 首版探针的一个真 bug（诚实记录）**：`settings_attr = dialog_mgr.sd` 找到了对话框，
但直接调 `apply_settings()` 抛
`AttributeError: 'Settings' object has no attribute '_hemisphere_changed'`（`settings.py:2528`）
—— 因为那些 `_*_changed` 标志在**真实 UI 流程**里由 `activate()` 建立。
⇒ 已修：先 `dlg.activate()` 再调，于是拿到上面这组基线。
**教训**：跳过 UI 流程直接调业务方法会测出**假结果**（不是"没效果"，是"环境不对"）。

### 3.3 内存（W3-⑧、W4-⑦）

| 量 | BEFORE（实测） | AFTER | 判据 |
|---|---|---|---|
| RSS：SDL 后 → TySim 构造后 | **64.0 → 652.6 MiB** | — | 显著下降 |
| RSS：SMCY 预载后 → 结束 | **1448.9 → 1917.3 MiB** | — | **显著下降**（本机可用仅 1.6 GiB ⇒ BEFORE 已经越界） |
| `pygame.image.load` 次数（构造期） | **2** | — | 若降采样后仍是 2，则收益只在"解码尺寸/RSS" |
| 地图原图 `MapView.original_img` | **10800×5400** | — | 应显著变小 |
| 陆地原图 `MapManager._land_orig` | **10800×5400** | — | 同上 |
| 窗口缓存面 `map_view._win_cache_surf` | 2304×1644 | — | 参照 |
| 画质指纹 map / land（512×288 重采样） | **`31b04f675436e4a4b1fc` / `6bfd8c92bef45cd8ebf7`** | — | **算像素差**（§5.3） |
| SMCY `_MAX_STREAMS` | **20** | — | 应变小 |
| SMCY 预载：新开流数 / 实际开流数 | **19 / 20（撞满上限）** | — | — |
| SMCY `release()` 次数 / RSS 增量 | **0 / +672.0 MiB** | — | RSS 增量应显著下降 |

### 3.4 物理确定性门（跨树）

| 指标 | BEFORE（实测） | AFTER | 判据 |
|---|---|---|---|
| ufunc 调用合计 / 全网格 / 元素数 | **2790 / 2670 / 902,017,200** | — | **必须逐位相同** |
| ufunc 分解 | `exp 630 · maximum 660 · where 510 · minimum 420 · hypot 330 · sin 90 · gradient 60 · cos 30 · tanh 30 · clip 30` | — | 同上 |
| `setprofile` C 调用合计 | **25,209** | — | 同上 |
| 缓存规模 | `_CONST_GRID 1 / _BAND_CACHE 1 / _RIDGE_GRID_CACHE 1 / _sst_clim_cache 1 / _GEN_CACHE ['ok']` | — | 同上 |
| 状态指纹（30 步，`genesis=False`+`forecast=False`） | **`310d4efe3688a45a55e0`**（`sim_hours=198.0`, `n_tcs=0`） | — | 同上 |

> ⚠ **不要拿这里的分母去比上一轮的 `p3_det` 数字**（3162 / 3026 / 1,022,286,160 / 1683）：
> 上一轮的探针在 30 步之后**又跑了 `setprofile` 2 步 + cProfile 2 步**，那些调用也计进了同一个计数器；
> 本轮 `w5_det` 只统计纯 30 步，所以更小。**同轮内 BEFORE vs AFTER 可比**，跨轮不可比。

---

## 4. 输入输出资产

| 资产 | 位置 | 说明 |
|---|---|---|
| **BEFORE 树（冻结）** | `G:\tmp\perf_before\` | 155 MB + 未跟踪资产；来源 `git 9c3f657…` + 未跟踪资产拷贝 |
| BEFORE 探针产物 | `G:\tmp\w5\RESULT_before_*.json`、`det_before.json` | 本报告 §3 的数字来源 |
| 画质像素快照 | `G:\tmp\w5\map512_before*.npy`、`land512_before*.npy` | AFTER 时用于算像素差 |
| 探针源码 | `G:\tmp\w5\{w5_all,w5_mainrun,w5_det}.py` | BEFORE/AFTER 同一份 |

---

## 5. 我用了什么测法 / 允许的结论强度

### 5.1 允许

- **确定性计数**（调用次数、缓存 hits/misses/evictions、元素数、模块在不在 `sys.modules`）⇒ **可给确定值、可做 PASS/FAIL**。
- **状态指纹**（`sim_step` 全状态 sha1）⇒ **可做逐位门**。
- **RSS** ⇒ 只能给**区间**（分配器/GC 抖动），除非差值远大于抖动。
- **像素差** ⇒ 可给"最大/平均绝对差"，但**阈值需要人来定**（见 §5.3）。

### 5.2 不允许（本机限制，见 `12_perf_method.md`）

- 任何**跨时间绝对耗时**、任何**单一加速比**、任何**跨运行 min 比较**（同配置两次差 2.13×）。
- **绑单核**：对多线程负载 2.5× 惩罚，且不排他。本报告所有探针**不绑核**（只用计数判据，不需计时）。

### 5.3 画质判据的定义（W4-⑦ 需要的"函数像素差"）

【推断】W4 的方向是"降采样/分块/懒加载地图解码"，**显示结果必然有微小差异**，
所以"哈希相等"是错误的判据。我改用：
1. 把地图（与陆地）图层**平滑缩放到固定 512×288**；
2. 存成 `.npy`；
3. AFTER 时逐像素算 `max|Δ|`、`mean|Δ|`、以及 `>8/255` 的像素占比。
**判 PASS 的条件（本报告采用）**：`mean|Δ| ≤ 2/255` 且 `max|Δ| ≤ 32/255` 且
`>8/255 占比 ≤ 2%`；**超过则判 FAIL 并附图**。阈值是我的判断，不是项目既有标准 ⇒ 已在 §7 标注为可挑战项。
**【实测·本轮结果】两层的实际差都是 `max|Δ| = 0`、`mean|Δ| = 0.0000` ⇒ 用任何阈值都过**：
- `MapView.original_img`（地图层）：`mean|Δ|=0.0000 max|Δ|=0 >8/255=0.000%`
- `map_mgr.land_img`（陆地图层）：`mean|Δ|=0.0000 max|Δ|=0 >8/255=0.000%`
**已有快照**：`G:\tmp\w5\{MapView_original_img_original_img,landimg}_{before,after}.npy`（512×288×3）。

### 5.4 AFTER 的前置条件与执行清单（**等 Lead 说"全部落地"再跑**）

| 步 | 动作 | 为什么 |
|---|---|---|
| 0 | 等 Lead 通知"**全部落地**"（本次还要含 **`map_mgr.py` 变体 A**） | 变体 A 会再改一次 `map_mgr.py`，早测会测到中间态 |
| 1 | **重做静态核验**：9 项 + 追加项逐个比对 `file:line` | 防止把"改到一半"当成结果 |
| 2 | `w5_all.py <TREE> after`（单进程） | 同一条探针，逐位可比 |
| 3 | `w5_mainrun.py <TREE> after`（单进程） | W1-④ 的真实 flip 数 |
| 4 | `w5_det.py <TREE> after`（单进程） | 物理门（必须与 BEFORE 逐位相同） |
| 5 | `run_regression.py <TREE> after`（15 文件，逐文件 timeout 300） | 只认"BEFORE PASS → AFTER 非 PASS"为回归 |
| 6 | `compare.py before after` | 自动出 PASS/FAIL 清单（§6 的表由它生成） |

**内存纪律**：②③④⑤ **依次跑**（不并行）。BEFORE 臂在 SMCY 预载阶段已到 1917 MiB，
AFTER 若更低则说明 W3-⑧ 生效；若仍接近上限，就在 `w5_all.py` 里把 SMCY 段缩减到 4 条流再测（并注明）。

---

## 6. 观察到的问题 / 风险 / 不一致

### 6.0 【实测·静态】9 台改动的落地状态（**阶段 2 开跑前重新核验过**）

> **最终状态**：**7 项已落地**（含追加的 `map_mgr.py` 变体 A 与 `fonts.py`），
> **2 项是"分析后有意不改"**（W3-⑥ purple 预算、W4-⑦ 懒加载），
> 另有 `simulator/simcore/render.py` sha256 与 BEFORE **逐位相同**（确为不改）。
> 静态证据 = 直接读工作区源码；**这不是 PASS/FAIL 判定**，只是"改动在不在"。

| 修复 | 落地? | 静态证据（工作区） | BEFORE 对照 |
|---|---|---|---|
| W1-① 删零调用 import | ✅ | `app/ty_sim.py:40-45` 已注释掉 `from .particle_effect import preload_particles`，并说明 cv2/PIL 来源；粒子 import 移到 `:244` 的惰性 `_warmup_jobs` | 该 import 在模块级 |
| W1-④ flip 节流 | ✅ | `main.py:112` `_LOAD_PROGRESS_MIN_MS = 50`；`:115-142` `_make_load_progress()` 生成节流回调，注释直指"207 次 flip"；`:209` 使用它 | `main.py:174-176` 直接 `flip()` |
| W1-⑩ 内核延迟 import | ✅ | `sim_mode_mixin.py:203-210, 269-305`：两处模块级 import 移入 `_sim_ensure_kernel()`（函数内 `from simulator.simcore...`） | 模块级 `snap_env_step` / `render` import |
| W2-② 名文本缓存 | ✅ | `_draw_icon_mixin.py:379` `SurfaceCache("name_text", 32MB, **1024**)` | `:352` **256** |
| W2-③ `_ring_offsets` | ✅ | `_draw_icon_mixin.py:150-151` `@functools.lru_cache(maxsize=128)` | 无装饰器 |
| W2-⑤ 设置守卫 | ✅ | `settings.py:506/523` 新增 `_FILTER_SIGNATURE_FIELDS` + 签名函数；`:2499` 注释"修复 5"；`:2560` 改条件调用 | `:2518` 无条件 `self.sim._apply_basin_filter()` |
| W3-⑥ purple 预算 | ⏸ **有证据的"不改"** | `_draw_icon_mixin.py:877` 仍 96MB/240；app-feat 判定 `purple_frame` 是"多台风同屏才命中"的**场景缓存**而非僵尸（键含 `frame_idx`）；同类 `summary_list.py:35` 的"0 misses"是**伪证**（`.get()` 未命中必记 miss，"0"只说明分支没跑），且 `w≥155kt` 涉及 **39 条台风** ⇒ 分支可达 | 96 MB / 240 |
| W3-⑧ SMCY 流预算 | ✅ | `app/smcy_icon.py:286` `_MAX_STREAMS = **12**` | `:271` **20** |
| W4-⑦ 地图解码 | ⏸ **暂缓 + 只改峰值** | `app/map_mgr.py:74` 仍全尺寸 `image.load().convert()`（10800×5400）—— 这是 Lead **有意暂缓**的懒加载项（会动启动路径且"只挪成本不降常驻"）；sim-data 改的是 `is_land_at_geo` 里 **233 MB 的 `tobytes` 瞬时峰值** | 同 |
| （追加）map_mgr `#2 变体 A` | ⏳ **正在落地** | `_load_land_orig()` 在"已解码且像素已释放"时返回 1×1 占位、真像素走 `_land_pixels()`；预期常驻 **−222.47 MB（534.41 → 311.95）** | — |

**【推断】** W3-⑥ / W4-⑦ 的"不改"**不是遗漏，是分析后的决定**（Lead 2026-10-02 明确）。
⇒ 阶段 2 对这两项应记 **"不适用 / 有证据的不改动"**，而不是 FAIL。
⇒ **map_mgr 变体 A 落地前不要测 W4-⑦**，否则会测到中间态 —— 这是本报告 AFTER 的前置条件。

### 6.1 【阶段 2 · 实测】逐项 PASS/FAIL（判据全部是确定性量，无一项用计时）

**静态核验（开跑前）**：13 个文件的 sha256 与 Lead 给的清单**逐个对上**
（`_draw_icon_mixin.py` = `881caa2b…`、`settings.py` = `944c4f15…`、`summary_list.py` = `fe96aeed…`、
`smcy_icon.py` = `c2c07f27…`、`map_mgr.py` = `f03340ce…`、`fonts.py` = `f17d22a7…`），
`simulator/simcore/render.py` = `8a6f27bf…` **与 BEFORE 逐位相同**（确为"不改"）。

| # | 修复 | BEFORE（确定性） | AFTER（确定性） | 独立判定 |
|---|---|---|---|---|
| 1 | **W1-①** 零调用 import（cv2/PIL） | `cv2=True, PIL=True`；`n_mods` 426 | **`cv2=False, PIL=False`**；`n_mods` **363** | ✅ **PASS** |
| 2 | **W1-④** splash flip 节流 | 真实 `main.py` flip = **208**（`on_load_progress` 207） | **13**（`on_load_progress` 仍 207） | ✅ **PASS** |
| 3 | **W1-⑩** 内核延迟 import | 构造后 `simcore.core=True`/`render=True`；`n_mods` 712 | 构造后 **False/False**；`n_mods` **388**；进模拟模式后 **True/True**；`sim_step` 3 步 `t=18.0` 正常 | ✅ **PASS**（含功能） |
| 4 | **W2-②** 名文本缓存 | items **256（撞条数上限）**、misses 801、**evictions 548**、bytes 15.3 MiB | items **528**、misses **729**、**evictions 204**、bytes **31.96 MiB / 32 MiB（99.9%）** | ⚠ **未达判据**（按"0 驱逐"=**FAIL**，但有 −63% 可测收益，见下） |
| 5 | **W2-③** `_ring_offsets` | 无 `cache_info`；60 半径两轮 cos **11,492 / 11,492** | `cache_info=True`；**11,391 / 0** | ✅ **PASS** |
| 6 | **W2-⑤** 设置守卫 | 无改动 ×3 次都 **25,473** | 无改动 **0 / 0**；改 `hemisphere`→south 后 **25,473** 且 `effect_ok=True` | ✅ **PASS**（守卫 + 功能双证） |
| 7 | **W3-⑥** purple 预算 | 本场景 0/0/0（未命中），上限 96 MB/240 | 同（未命中） | ➖ **不适用**（有证据的不改） |
| 8 | **W3-⑧** SMCY 流预算 | `_MAX_STREAMS=20`、open **20**、preload 19、RSSΔ **+672.0 MiB**、终点 **1917.3** | **`12`**、open **12**、preload **11**、RSSΔ **+384.6 MiB**、终点 **1440.9**、`release` 0 | ✅ **PASS** |
| 9 | **W3-⑧c** `_landfall_cache` 字节预算（新判据） | **无字节预算**；单条 **8.05 GiB 会被缓存**（items 1）；30 档时 items=**24** | 64 MiB 预算；单条 8.05 GiB **不进缓存**（items 0，仍返回帧表）；30 档实测 **58.34 / 64 MiB**、items 6 | ✅ **PASS**（改前有明确反例） |
| 10 | **W4-⑦ / 变体 A** 陆地图层 | 构造后 RSS **652.7**；`_land_orig` **10800×5400 常驻** | 构造后 RSS **394.6（−258.1 MiB）**；`_land_orig` = **1×1 占位**；`image_load` 仍 2；地图原图 hash512 **`31b04f67…` 逐位相同** | ✅ **PASS**（**只认常驻**，峰值按 Lead 要求不判） |
| 11 | **物理确定性门** | ufunc **2790/2670/902,017,200**、setprofile **25,209**、fp **`310d4efe3688a45a55e0`**、`sim_hours 198.0` | **全部逐位相同** | ✅ **PASS**（最强的"没动物理"证据） |
| 12 | **回归（15 文件）** | **15/15 PASS，178 passed / 0 failed / 0 timeout** | **15/15 PASS，178 passed / 0 failed / 0 timeout**（逐文件 passed 数完全相同） | ✅ **PASS（无回归）** |
| 13 | **W3-⑧c** 追加判据（Lead 要求） | — | 见 §6.1 第 9 行 | ✅ **PASS** |

> **汇总（12 条有判据的项）**：**PASS 9 项**（W1-①④⑩、W2-③⑤、W3-⑧、W3-⑧c、W4-⑦、物理门、回归）
> ＋ **FAIL/未达判据 1 项**（W2-②，有收益但未到 0 驱逐）＋ **不适用 1 项**（W3-⑥，有证据的不改）
> ＋ **信息项 1 项**（`n_mods` 712 → 388，作参照）。
> **没有任何"没生效"的改动 —— 每一项都有确定性指标上的可测变化。**

#### 判定 4（W2-②）为什么是「部分达标」而不是 PASS —— 照实说

- **做对了的部分**【实测】：条目上限 256→1024 **确实解除了"条数瓶颈"**（AFTER items 达到 528，已远超旧上限 256）；
  驱逐 **548 → 204（−63%）**，命中 771 → 843。
- **没到判据的部分**【实测】：任务书给的判据是"**0 驱逐**、命中率 ~100%"。我的场景下
  **驱逐仍有 204**，因为 **32 MB 字节预算成了新的约束**：实测占用 **33,513,616 / 33,554,432 = 99.88%**。
  即：**瓶颈从"条数"被挪到了"字节"**，不是被消除。
- **为什么 app-feat 测到 0/0 而我测到 204**（两个都对）：app-feat 的场景是**真实一帧**（约 768 个键、
  约 16–17 MB 表面），**装得下** 32 MB；我的场景是**207 条台风逐一直画名字**（更严的压测，
  工作集 ≥33.5 MB），**装不下**。⇒ **判据必须写明场景**，否则同一改动会得出两个相反结论。
- **结论**：**按任务书的字面判据（0 驱逐）记 FAIL / 未达标**；
  但要同时写清它**有真实可测的收益**（−63% 驱逐、解除条数瓶颈），
  且 app-feat 在**真实一帧**场景下确实拿到了 0/0 ⇒ **这不是"没生效"，是"判据场景比它的设计目标更严"**。
  若要把这条做成 PASS，需要改的是**键设计/表面尺寸**（例如按帧复用、或降采样名字表面），
  而不是继续加大上限 —— 这正是 §8 验收清单里"**是否用调参掩盖设计问题**"那一条的实例。

#### 判定 10（W4 变体 A）的**画质**独立复核

- 地图原图 `MapView.original_img` 的 512×288 重采样指纹：BEFORE `31b04f675436e4a4b1fc` →
  AFTER **`31b04f675436e4a4b1fc`（逐位相同）** ⇒ 地图层**零像素差**。
- **陆地图层另有专项**（变体 A 改的正是"陆地像素放哪"）：见 §6.2。
- 按 Lead 明确要求：**峰值内存不判**（两臂峰值均 ~840 MB，未降），**只认常驻**；
  常驻 **652.7 → 394.6 MiB（−258.1，−39.5%）**。

### 6.2 【实测】W4 变体 A 的陆地图层专项（512×288 逐像素）

| | BEFORE | AFTER |
|---|---|---|
| `map_mgr.land_img` 尺寸 | 1280×620 | 1280×620 |
| `map_mgr._land_orig` 尺寸 | **10800×5400（常驻）** | **1×1（占位）** |
| **`land_img` 512×288 指纹** | `6bfd8c92bef45cd8ebf7` | **`6bfd8c92bef45cd8ebf7`（逐位相同）** |
| **陆地图层像素差** | — | **mean\|Δ\|=0.0000 / max\|Δ\|=0 / >8 占比 0.000%** |
| `map_mgr._land_alpha_bytes` | 793,600 | 793,600 |
| `_land_pixels()` 存在且可调用 | ❌ 不存在 | ✅ 返回 **Surface 10800×5400** |
| 海陆判定抽样（5 点） | 探针用错 API 名 ⇒ 未取到（不算 FAIL） | 同 |

**结论 **：**W4 变体 A 的画质零损失**（地图层与陆地图层都是 max|Δ|=0）。
**【实测·一条行为注意】**：`_land_pixels()` 一旦被调用，`_land_orig` 会**重新物化回 10800×5400**
（我这份 land 探针就把它自己触发了）⇒ 变体 A 省的是"**没人要像素时的常驻**"，
**不是"像素永不物化"**。所以 ①按窗口渲染的实际路径不碰全尺寸像素 ⇒ 省下来；
②任何"顺手拿全尺寸陆地像素"的新代码会把 222 MB 再要回来。这是**给下一轮的约束**，不是缺陷。

### 6.3 【实测/复核】对本轮诊断报告的三条更正（**别再沿用旧说法**）

| 旧说法（`16_perf_diagnosis.md`） | 更正 | 依据 |
|---|---|---|
| §3 #1 "删 `app/ty_sim.py` 那行零调用 import ⇒ 拿掉 cv2+PIL" | **可观测收益 = 0**：删它**没有**单独减少任何模块；真正带来收益的是**另外 3 个文件里的函数内 import**。W5 的判据只看"cv2/PIL 在不在 `sys.modules`"，**不区分是谁的功劳** ⇒ 本报告不把 W1-① 记成独立收益 | Lead 转述 app-sim 实测；我的 `mods_after_import` 只能证明"结果"，不能归因 |
| §3 #14 "`MapManager` 非单例 ⇒ 两个实例 1.29 GiB" | **是错的**：全仓只有 **1 处**实例化（`map_mgr.py:512` 的 `MapView` 由 `MapManager` 持有），"改单例"收益 ≈ 0，已撤销 | Lead 明确；我在 §3 的 `surface_attrs` 里也只看到一套 `map_view.*` |
| §3 #8 "`rt()` 默认不缓存 ⇒ 220 处绕过" | **不准确**：`SmartFont.render` 本来就有 **128 条 FIFO**；真因是 **139 个键 > 128 ⇒ 自噬**，修法是"**条数换字节预算**"而不是"再加一层缓存" | Lead 明确；W5b 改的是 `app/constants/fonts.py`（sha `a32bbdc4` → `f17d22a7`） |

**【实测·补充一条边界】**：`rt()` 修好后**每帧仍有约 5 次光栅化完全绕过 `rt()`**
（`dialog_base.py:113/120/184`、`input_field.py:538/575`）⇒ **AFTER 不会是 0**。
本报告的判据里没有 rt()（不在我 9 条内），此处只作记录：**"没到 0"不等于 FAIL**。

### 🟠 严重｜"当前工作区"在验证开始时已经是"半改"状态
- **证据**：`_name_shadow_cache` 256 → 1024；`_MAX_STREAMS` 20 → 12。
- **影响**：任何"拿工作区当 BEFORE"的验证都会把已落地的收益算进基线 ⇒ 低估甚至反向判定。
- **本报告的处理**：BEFORE 一律用 `G:\tmp\perf_before\`（来自 git 悬空提交 + mtime 过滤的未跟踪资产）。

### 🟠 严重｜BEFORE 树里这个 SMCY 场景的 RSS 已经 **1917 MiB**（本机可用仅 ~1.6 GiB）
- **证据**：§3.3（652.6 → 1448.9 → 1917.3 MiB）。
- **影响**：BEFORE 树在"预载 20 条图标流"这一步**本身就接近/超过可用内存** ⇒ 测 W3-⑧ 时随时可能 OOM，
  且这正是诊断 §3 #11 说的"结构性内存靶子"。⇒ 阶段 2 必须**单独跑**这一节，并在 AFTER 上期待显著下降。

**【实测·Lead 代跑的交叉核对（本报告引用，不重复跑）】**
- **合并判据**：`TySim()` 构造后 **`cv2=False` / `PIL=False` / `simcore.core=False`**；
  `sys.modules` **422 → 377**。与我 BEFORE 臂的 **`cv2=True` / `PIL=True` / 构造后 `simcore.core=True`**
  （`n_mods 712`）**方向一致、可对上** ⇒ **W1-① 与 W1-⑩ 独立双向确认**。
- **回归**：`test_ace_engine + test_panel_layer_row + test_sim_settings + test_control_panel +
  test_map_resize + test_render_geom + test_ocean_area` = **77 passed / 44.23 s**；
  `test_landfall_view_invariance + test_fx_sound + test_render_layers` = **44 passed / 20.80 s**；
  `test_sim_mode_ref` = **26 passed / 184.36 s**（比我担心的乐观）。

### ✅ 值得保留的现状
- **`git stash create` 的悬空提交是可用的回退点**（`9c3f657…`），本轮 BEFORE 树就建立在它上面。
- **`cache_store` 自带 hits/misses/evictions**：W2-② 的判据不需要任何插桩就拿到了（801/771/548）。
- **`_purge`/`_win_cache_surf` 这类窗口缓存**在 BEFORE 里已存在（`map_view._win_cache_surf` 2304×1644）
  ⇒ W4 若改"解码尺寸"，别把已有的窗口缓存逻辑改坏。

---

## 7. unresolved

| # | 卡在哪 | 需要什么 |
|---|---|---|
| U1 | 画质像素差的**阈值**没有项目既有标准（§5.3） | 作者/用户给一个可接受的画质损失口径；或把 BEFORE/AFTER 各出一张截图给人看 |
| U2 | 本机 bash 服务在本轮多次 `Bash/Service/E_UNEXPECTED` 掉线、长命令被杀 | 需要更稳的执行窗口；本报告已把探针收敛到 3 个进程以降低暴露面 |
| U3 | ~~`w5_all.py` 的 SMCY 段可能触发内存压力~~ **已解决**：AFTER 实测 RSS 终点 **1440.9 MiB**（< BEFORE 1917.3）⇒ 不再越界，无需缩减版 | — |
| U6 | **`app/name_pos_dialog.py` 的改动前内容不可复原**（`_backup/` 没备份它、悬空提交不含未跟踪文件、mtime 14:12 > cutoff）⇒ BEFORE 树里该文件缺失 | 需要一个"开工前对未跟踪集合的完整快照"。影响面已界定为 **0**（§1.1 的导入图分析 + 15/15 回归通过），但严格说 BEFORE 树在这一个文件上不完整 |
| U7 | **W3-⑥ 无法用"确定性指标下降"判定** | 这是"有证据的不改"（§6.0）。若后续要求量化，需要造一个"多台风同屏命中 `purple_frame`"的场景再比 evictions |
| U8 | **`_land_pixels()` 之后 `_land_orig` 会重新物化回 10800×5400**（§6.2 实测）⇒ 变体 A 省的是"无人索取像素时的常驻" | 需要一条纪律/断言：**渲染路径不得直接取全尺寸陆地像素**。可用一个探针长期盯 `_land_orig` 的尺寸（>64×64 即报警） |
| U9 | 海陆判定抽样没取到（我用错了 API 名 `land_at_geo`） | 需要 `MapManager` 的真实公开查询方法名；这一项与变体 A 的像素等价**无因果关系**（像素已 0 差），故未阻塞判定 |

---

## 8. 关键位置速查表 + 阶段 3 验收清单

| 想找什么 | 去哪看 |
|---|---|
| 回退方式（单文件 / 全量 / 校验） | `output/exploration/_backup/README_ROLLBACK.md` |
| 改动前 SHA256 | `output/exploration/_backup/SHA256_BEFORE.txt` |
| 悬空提交 | `git cat-file -t 9c3f657eef517f35e4517271a5b6b6471e1ae13e`（commit，457 文件） |
| 诊断与判据来源 | `16_perf_diagnosis.md` §1（测法）、§3（问题清单） |
| 测法协议 | `12_perf_method.md` §5.3/§5.4 |
| `_name_shadow_cache` | `app/ty_sim_mixins/_draw_icon_mixin.py:352`（BEFORE）/ 当前 `:375` |
| `_purple_frame_cache` | 同文件 `:846`（BEFORE） |
| `_MAX_STREAMS` | `app/smcy_icon.py:271`（BEFORE，=20）/ 当前 `:286`（=12） |
| `point_in_limit` | `app/ace_engine.py`（`AE.ACEEngine.point_in_limit`）；调用点 `app/settings.py:2518`（无守卫）vs `:1353/:1369`（有守卫） |
| 设置保存入口 | `app/settings.py:2340 apply_settings()` |
| 地图解码 | `app/map_mgr.py:73-75`（`pygame.image.load`）、`_load_land_orig` `:523-538` |
| 我的探针 | `G:\tmp\w5\{w5_all,w5_mainrun,w5_det,run_regression,w5_landfall,w5_land,run_after,compare,fmt,mkbefore}.py` |
| 探针产物 | `G:\tmp\w5\RESULT_{before,after}.json`、`det_{before,after}.json`、`reg_{before,after}.json`、`landfall_{before,after}.json`、`land_{before,after}.json`、`*_summary.txt`、`compare_before_after.txt`、`*.npy` |
| 阶段 2 驱动日志 | `G:\tmp\w5\after_status.json`（4 步各 rc/sec）、`log_after_*.txt` |
| BEFORE 树 | `G:\tmp\perf_before\` |

### 阶段 3：验收清单（照 `perf-optimize` 第七节）

| 项 | 判定 | 说明 |
|---|---|---|
| 优化前有量化基线（非感觉） | ✅ | BEFORE 树的确定性计数已冻结（§3）；且**基线本身可用**：与诊断 #5 的 25,473、#4 的 207 逐字吻合 |
| 瓶颈定位有证据 | ✅ | 引用 `16_perf_diagnosis.md` §3（每条带 `文件:行` + 确定性计数），W5 不重做定位 |
| 一次只改一个变量，每步有前后对比 | ⚠ **形式不满足，实质可分辨** | 本批是 **4 个工作流并行**落地（13 个文件、含 6 个 W1 文件），**不是"一次只改一个变量"**。但 W5 的判据**逐项独立**（每项一个确定性量、逐个文件 sha 可查），所以"哪一项有效"仍可分辨 ⇒ 记录为形式缺陷，不影响本报告的判定 |
| 改动可回退 | ✅ | 三档回退齐备：单文件 `_backup/*.py`、全量悬空提交 `9c3f657…`、`SHA256_BEFORE.txt` 校验；W2-② 是"改一个数字"、W3-⑧ 是"改一个常量" ⇒ 回退成本极低 |
| 无过早优化 | ✅ | 12 条判据里 **9 PASS / 1 未达判据 / 1 不适用 / 1 信息项**，**没有一项是"零可测变化"**（§6.1 汇总） |
| 功能无回归 | ✅ | **15/15 回归全 PASS，178 passed 与 BEFORE 逐文件相同**；功能判据：W1-⑩ 进模拟模式后 `sim_step` 正常（t=18.0）、W2-⑤ 改 `hemisphere` 后 25,473 次过滤**仍执行且生效**、W4 变体 A **地图+陆地像素差 max\|Δ\|=0** |
| 已排查常见反模式 | ✅ | 三条诊断更正（§6.3：W1-① 收益归因、`MapManager` 假非单例、`rt()` 本来有缓存）+ 探针自身的两个坑（跳过 UI、跨口径比分母）+ 缓存"0 未命中"伪证 |
| 若涉及资源调优，已确认不是用硬件掩盖设计问题 | ⚠ **有一条实例** | **W2-② 是"调参换瓶颈"**：条目上限 256→1024 把驱逐从 548 压到 204，但**字节预算 32 MB 成了新约束（实测占 99.88%）** ⇒ 在我的强压场景下**没有消除**驱逐。**改法应是键设计/表面尺寸，不是继续加上限**。其余资源类改动（W3-⑧ 12 流、W3-⑧c 64 MB 字节预算、W4 变体 A 常驻）都是**预算/生命周期**层面的修正，不是硬件掩盖 |
