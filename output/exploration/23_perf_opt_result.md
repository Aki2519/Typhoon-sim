# 性能优化实施结果（第四~五阶段）

> 2026-10-02 · 对象：**整个模拟器**（`main.py` 那个 pygame 应用）
> 前置：`16_perf_diagnosis.md`（只诊断不修改）→ 本文件（实施 + 独立验证）
> 输入：`17`/`18`/`19`/`20`/`22_fix_*.md`（五个工作流）+ `21_fix_verification.md`（独立验证）
> **所有判据都是确定性量，没有一项用计时**（本机计时不可信：P 核/E 核差 1.90×、同代码墙钟极差 325%）

---

## 摘要

**改了 12 个源文件。独立验证判定：PASS 9 / 未达判据 1 / 不适用 1。没有任何一项是"零可测变化"。**

| 最有价值的四项收益 | 数字 |
|---|---|
| **启动期不再加载 OpenCV + Pillow** | `cv2`/`PIL` 在 `sys.modules` 里 **True/True → False/False**；模块数 426 → 363 |
| **启动期不再加载物理内核** | `TySim()` 构造后 `simcore.core` **True → False**（进模拟模式后正常 True） |
| **地图/陆地常驻内存** | **534.40 → 311.93 MB（−222.47 MB，−41.6%）**；整机 RSS 652.7 → 394.6 MB |
| **设置保存不再全量重过滤** | 改无关设置时 `point_in_limit` 调用 **25,473 → 0**（改相关设置仍 25,473 且生效） |

另有：启动期进度重绘 **208 → 13 次**；文本每帧光栅化 **139 → 1.25 次**；SMCY 图标流 **20 → 12 条**（RSS 终点 1917 → 1440.9 MB）；`_ring_offsets` 每帧 `cos` **11,492 → 0**。

**回归零退化**：15 个测试文件 **178 passed → 178 passed**，逐文件通过数相同，**0 failed / 0 timeout**；物理确定性门（ufunc 计数、元素数、状态指纹 `310d4efe…`）**逐位相同**。

**一项未达标，如实记录**：名文本缓存在**强压场景**下仍有 204 次驱逐（详见 §3）。

---

## 1. 改动清单（12 个文件）

| 工作流 | 文件 | 改前 → 改后 sha256（前 8 位） | 核心改动 |
|---|---|---|---|
| W1 | `app/ty_sim.py` | `1ff0c4a3` → 已改 | 删零调用 import；内核 import 延后 |
| W1 | `main.py` | `2c6eb7e9` → 已改 | 进度重绘节流（50 ms，末次必画） |
| W1 | `app/ty_sim_mixins/sim_mode_mixin.py` | `045e1ed2` → 已改 | `_sim_ensure_kernel()` 惰性加载内核 |
| W1 | `app/playback_ctrl.py` | `7c06e338` → 已改 | cv2/PIL 依赖改函数内 import |
| W1 | `app/summary_effect.py` | `dba97668` → 已改 | 同上 |
| W1 | `app/statistics/heatmap.py` | `e7d281c6` → 已改 | PIL 改函数内 import |
| W1 | `app/name_pos_dialog.py` | `0ea67835` → 已改 | `_TOTAL_FRAMES` 改首次取值缓存（不在逐帧 import） |
| W2 | `app/ty_sim_mixins/_draw_icon_mixin.py` | `74defaee` → `881caa2b` | 名文本缓存条数 256→1024；`_ring_offsets` 加 `lru_cache` |
| W2 | `app/settings.py` | `a146f876` → `944c4f15` | 新增过滤签名守卫 |
| W2 | `app/statistics/summary_list.py` | `d4502bd1` → `fe96aeed` | smcy_icon 改函数内 import |
| W3 | `app/smcy_icon.py` | `3e635fd0` → 已改 | `_MAX_STREAMS` 20→12；`_landfall_cache` 加 64 MB 字节预算 |
| W4 | `app/map_mgr.py` | `05df5172` → `f03340ce` | alpha 三级提取（瞬时峰值）；变体 A（常驻 −222 MB） |
| W5 | `app/constants/fonts.py` | `a32bbdc4` → `f17d22a7` | 文本缓存条数上限 → 8 MB 字节预算 |

**未改（有证据的不改，不是遗漏）**：

| 项 | 理由 |
|---|---|
| `simulator/simcore/render.py` | sha256 与改前**逐位相同**。审计出 9 套缓存全为条数封顶（2560×1498 最坏 ~311 MB），但两大项只在模拟模式填充、**无实测占用** ⇒ 不做 |
| W3-⑥ `purple_frame` 96 MB 预算 | 分析判定为「**特定场景才用**」（键含 `frame_idx`，多台风同屏才命中），非僵尸 |
| W3-6b `summary_purple` 48 MB | "0 hits/0 misses" 是**伪证**（`.get()` 未命中必计 miss）；扫数据发现 `w≥155kt` 涉及 **39 条台风** ⇒ 分支可达 |
| W3-8b 每流预算 24→20 MB | 省 48 MB 落在 RSS 噪声内，代价真实（原生帧 39→32、破坏"换档不 seek"） |
| W4-⑦ 地图懒加载 | 分析证明它**不降常驻**（`view_state.py:100` 每次视图更新必触发解码），只把 222 MB 从 splash 挪到首帧，而首帧卡顿是有意避免的 |

---

## 2. 独立验证结果（`21_fix_verification.md`，由不参与实施的 W5 做）

| 项 | BEFORE → AFTER | 判定 |
|---|---|---|
| W1-① cv2/PIL 移出启动 | `T/T → F/F`（n_mods 426 → **363**） | ✅ PASS |
| W1-④ 真实 `main.py` flip | **208 → 13**（progress 仍 207） | ✅ PASS |
| W1-⑩ 内核延迟 import | 构造后 `T/T → F/F`；进模拟模式后 `T/T` 且 `sim_step` 正常 | ✅ PASS |
| **W2-② 名文本缓存** | 驱逐 548 → 204、items 256 → 528；**但字节 31.96/32 MiB = 99.88%** | ❌ **未达判据** |
| W2-③ `_ring_offsets` | 两轮 `cos` **11,492/11,492 → 11,391/0** | ✅ PASS |
| W2-⑤ 设置守卫 | 无改动 **25,473 → 0/0**；改 `hemisphere` 后仍 25,473 **且生效** | ✅ PASS |
| W3-⑥ purple | 两侧均未命中该路径 | ➖ 不适用 |
| W3-⑧ SMCY | 20 流 / RSS Δ+672 / 终点 1917 → **12 流 / Δ+384.6 / 终点 1440.9** | ✅ PASS |
| W3-⑧c landfall 字节预算 | 改前**单条 8.05 GiB 会被缓存**（items=1）→ 改后不缓存；30 档 58.34/64 MiB | ✅ PASS |
| W4-⑦ 变体 A | 常驻 **652.7 → 394.6 MB**；`_land_orig` 10800×5400 → 1×1；**地图+陆地像素差 max\|Δ\|=0** | ✅ PASS |
| 物理确定性门 | 2790/2670/902,017,200/25,209/指纹 `310d4efe…` **逐位相同** | ✅ PASS |
| 回归 15 文件 | **178 passed → 178 passed**，逐文件相同，0 failed / 0 timeout | ✅ PASS |

---

## 3. 未达标的那一项：名文本缓存（W2-②）

**两个结论都对，差别只在场景：**

| 场景 | 工作集 | 结果 |
|---|---|---|
| app-feat 的**真实帧**（12 台风日常路径） | ~16–17 MB | **0 miss / 0 eviction** ✅ |
| sim-run 的**强压**（207 台风逐一直画名字） | ≥33.5 MB | 驱逐 548 → **204**，字节 **99.88%** ❌ |

**瓶颈从"条数上限 256"挪到了"32 MB 字节预算"** —— 所以这次改动**在真实场景达标、在强压场景未达标**。

**这不是继续调参能解决的。** 要继续降低，需要动**键设计或表面尺寸**（按帧复用、降采样、或把名字文本改成一次性合成整层），而不是把 32 MB 改成 64 MB —— 那正是 `perf-optimize` 验收清单里"**用调参掩盖设计问题**"的实例。**判断：本轮到此为止，方案留给下一轮。**

---

## 4. 三条"诊断被实施/验证证伪"的更正（Lead 记错）

这一轮最有价值的部分之一：**诊断报告里有三处判断被实测推翻**。

| # | `16_perf_diagnosis.md` 的原话 | 实测 | 谁发现的 |
|---|---|---|---|
| 1 | "删 `ty_sim.py:39` 那行 **1 行改动、收益最直接**" | **可观测收益 = 0** —— 它是死代码，但另有三条模块级路径仍在拖 cv2/PIL；真收益来自改那 3 个文件 | app-sim |
| 2 | "`MapManager` **非单例** ⇒ 两个实例 1.29 GiB" | **全仓只有 1 处实例化**（`app/ty_sim.py:402`）。"两个实例"是**测试场景**（连续构造两次 `TySim`）的读数，非生产常态 ⇒「改单例」收益≈0，**该条已从清单撤销** | sim-data |
| 3 | "`rt()` **默认不缓存**，220 处调用点绕过" | `rt()` 默认路径**本来就有缓存**（`SmartFont.render` 自带 128 条 FIFO，`fonts.py:90-130`）。真因是实测 `f_s` 稳态 **139 键 > 128 ⇒ 每帧自噬**（命中率 20%）⇒ 修法是"**条数换字节预算**"而非"加一层缓存" | sim-core |

**这三条已写进 `16_perf_diagnosis.md` 的更正区。** 教训：诊断阶段的"读数"必须问清**是在什么场景下量的** —— 三处错误都是把测试场景/局部读数当成了生产事实。

---

## 5. 一条真实副作用（必须披露）

### 我们的测试改动了用户的 `config.json`

- 会话开始时 `hemisphere = "north"`（依据：`config.json.bak` 为 north；且 `04_app_feat.md` 第一轮做的 `config.json` vs `.bak` 差异清单里**没有 hemisphere**）
- 会话结束时变成 **`"south"`** —— 某位测试者用"把半球切到 south"来验证设置守卫生效，`apply_settings()` **落盘到了用户的真实配置**
- **Lead 已还原为 `north`**，并复核：非预先存在、非新增字段的差异 **= 空**（用户原有设置已全部还原）
- 备份：`_backup/config.json.after-restore`

> ⚠ **这个坑项目自己早就知道**：`tests/conftest.py` 开头就写着"别让测试写用户的 `config.json`"，而 `04_app_feat.md` 也把它记为"**已发生过的真实事故**"。**这次是第二次。**
> ⇒ **建议**：把 `config.json` 纳入安全网（本轮疏漏了），并考虑让测试统一走临时配置路径。

**另一条自我批评**：本轮的备份纪律有漏洞 —— ① `config.json` 未备份；② `_backup/map_mgr.py` 是 **alpha 优化之前**的版本，用它回退会连带撤掉已验收的 alpha 改动（app-feat 发现并新建了 `map_mgr_v2.py`）。**"备份一次就够"是错的，每次新改动落地后回退点要更新。**

---

## 6. 环境事故（非工程问题，但影响效率）

**宿主 `bash` 服务反复失效**：`Bash/Service/E_UNEXPECTED`，**连 `echo` 都失败**，本轮共 **5 人次**遇到（app-sim ×2、app-feat、sim-numerics、sim-data），另 sim-run 上一轮也遇到（>20 min 后台作业被杀）。

应对（可复用）：
- `read`/`write`/`edit`/`grep`/`glob` **不受影响** —— 代码与报告照常落盘
- sim-run 用 **Blender 附带的 Python 作 launcher 起 detached 子进程**跑探针，绕过 bash
- sim-numerics 用 **headless Blender 的 Python 3.13** 做 AST 校验 + sha256，并用桩版 pygame/cv2 验证缓存不变量
- **纪律：关键中间产物随时落盘，不要把结果攒在上下文里等最后一次性写**

**内存**：全程在 **768 MB – 2.6 GB 可用**之间波动（物理 15.74 GiB），**有 OOM 前科**。所有内存实验都要求单进程、禁止并发 —— 这条纪律本轮被遵守了。

---

## 7. 如何回退

见 **`_backup/README_ROLLBACK.md`**（已更新为权威版，含每个文件的正确回退点、以及"备份一次就够是错的"这条实践教训）。

最快路径：
```bash
cd /mnt/g/气象/Typhoon-sim
cp output/exploration/_backup/map_mgr_v2.py app/map_mgr.py     # ⚠ 必须用 v2
# 其余同名 cp；summary_list.py 用 git checkout 9c3f657 -- <文件>
```
运行时开关（无需改文件即可回退单项）：
- `F.RT_TEXT_CACHE_BYTES = 0` → 文本缓存回旧行为（已验证输出逐字节相同）
- `LAND_RGBA_TRANSIENT` → 变体 A 开关（关闭后常驻回到 534.40 MB，逐字节相同）

**完整 BEFORE 树**：`G:\tmp\perf_before\`（sim-run 冻结，位级对账通过）

---

## 8. 下一步建议（按收益/成本排序）

| # | 事项 | 依据 |
|---|---|---|
| 1 | **把 `config.json` 纳入安全网 + 让测试走临时配置** | §5，同类事故已发生两次 |
| 2 | **给 `_land_orig` 加纪律/断言**：渲染路径不得直接取全尺寸陆地像素；长期盯 `_land_orig` 尺寸 >64×64 报警 | W4 变体 A 的使用约束（`_land_pixels()` 一旦被调用会重新物化 10800×5400） |
| 3 | **名文本缓存改键设计/表面尺寸**（而非继续加上限） | §3，当前在强压场景 99.88% 满 |
| 4 | **补全剩余 5 次绕过 `rt()` 的光栅化**：`dialog_base.py:113/120/184`、`input_field.py:538/575` | sim-core 实测，这是"每帧到不了 0"的唯一残余 |
| 5 | **`_smcy_ts_for` 恒返回 None**（`@staticmethod` 里用 `self` ⇒ `NameError` 被 `except` 吞掉） | 两人独立发现；⚠ 修它会**启用一条当前死掉的功能**，可能增加内存，需单独评估 |
| 6 | 落地 `perf-optimize` 的**验收协议**为常设纪律（A/A 闸门 + 确定性主指标 + 计时只作区间） | `12_perf_method.md` |

---

## 9. 交付物索引

| 文件 | 行数 | 内容 |
|---|---|---|
| `17_fix_startup_import.md` | 296 | W1：启动路径 6 项（含"收益为 0"的如实记录） |
| `18_fix_icon_cache_settings.md` | 402 | W2：缓存悬崖、`lru_cache`、设置守卫 |
| `19_fix_memory_budgets.md` | 274 | W3：SMCY 流预算、landfall 无界缓存 |
| `20_fix_map_decode.md` | 674 | W4：alpha 三级提取、变体 A（常驻 −222 MB） |
| `22_fix_text_cache.md` | 416 | W5b：文本缓存条数 → 字节预算 |
| `21_fix_verification.md` | 468 | **独立验证**：BEFORE/AFTER 位级对比 + 验收清单 |
| `_backup/README_ROLLBACK.md` | — | 权威回退指南 |
| **本文件** | — | 实施结果汇总（§10 为第六/七轮追加） |

**源码改动：第一~五轮 13 个文件。**（`git status` 在后两轮从 535 升到 **537** —— 因为 `app/cache_store.py`、`app/dialog_base.py`、`app/input_field.py` 是**已跟踪且原本干净**的文件，我们改了它们，新增 2 条 ` M`。**这是改源码的必然结果，不是污染。**）

---

## 10. 第六 / 七轮追加（2026-10-02）

第五轮结束后又做了 5 项。**全部有确定性判据，全部有回退点。**

### 10.1 新增改动

| 轮 | 项 | 文件 | sha256 |
|---|---|---|---|
| 6 | **堵死"测试写用户 config.json"** | `app/config_store.py` | `d3dcc679` → `47c97ce7` |
| 6 | 同上（四层隔离） | `tests/conftest.py` | `0aead882` → `ffd2a6da` |
| 6 | `_land_orig` 重新物化护栏 | `app/map_mgr.py` | `f03340ce` → `c3ab5374` |
| 6 | 残留 5 处光栅化改走 `rt()` | `app/dialog_base.py` | `93d740aa` → `5ab190e1` |
| 6 | 同上 | `app/input_field.py` | `6e684a18` → `5403f889` |
| 6 | 名文本表面去死边距 | `app/ty_sim_mixins/_draw_icon_mixin.py` | `881caa2b` → `be89079e` |
| 7 | 裁到 `alpha>0` 包围盒 + `name_pos_dialog` 同步 | `_draw_icon_mixin.py` / `app/name_pos_dialog.py` | `be89079e`→`6df1753f` / `c2ffb92e`→`f38972b6` |
| 7 | `SurfaceCache._trim` 的 `>0` 守卫 | `app/cache_store.py` | `99fdcf81` → `4ea9c552` |

### 10.2 新增收益

| 指标 | 结果 |
|---|---|
| **配置泄漏** | 改前复现：测试调 `commit()` ⇒ 用户 `config.json` 被写（`677e09df` → `dd78b45f`）；改后同一批测试 **sha 逐位不变**，且 53 passed |
| **残留光栅化** | 5 处全部从"绕过 `rt()`"→"经 `rt()`"；PaintDialog 稳态 **1.25 → 0.00 次/帧** |
| **名文本工作集** | 741 键 **35.75 → 17.78 MB（累计 −50.3%）**；@32 MiB 驱逐 **49 → 0** |
| **W5 强压场景推算** | 44.9 MB →（轮六 −20.6%）35.7 MB →（轮七 −37.4%）**≈22.4 MB**，比 32 MiB 低 ~11.1 MB |
| **`SurfaceCache(max_bytes=0)`** | put 后 `len=0/evictions=1`（自逐）→ **`len=1/evictions=0`**（与 docstring 一致） |
| **12 台风日常路径** | 名字缓存 1.61 → **0.80 MB**，全程 0 驱逐（无回归） |
| 像素等价性 | 端到端真实 `draw_typhoon_name`（zoom 0.7/1.0/1.3）**不透明画布 sha256 逐字节相同**；SRCALPHA 下 alpha 差异 **0**、可见 RGB 差异 **0**；预览框定位 **6/6 一致** |

### 10.3 这一轮又抓到 7 个"差点写成假结论"的时刻

这是本批最值得记录的部分 —— **每一次都是"反常值"或"自查"把假结论挖出来的**：

| # | 差点写下的假结论 | 暴露它的东西 |
|---|---|---|
| 1 | app-feat："12 台风场景 **0/0 ✅**，W5 的 FAIL 只是场景更严" | sim-core 发现其探针 `probe_name_cache.py:47,57` 硬编码 `name_factor=1.0`，**生产是 2.25**（差 5.06 倍面积）⇒ **W5 的 FAIL 才是生产代表值** |
| 2 | sim-core 第一版 `_tight_crop`"已生效" | **收益正好 +0.0%** 这个反常值 ⇒ 查出 `Surface.get_bounding_rect(0)` 在 SDL 2.32.10 **返回整幅**，实现是静默 no-op |
| 3 | 用 `get_bounding_rect(1)` 裁剪 | 它会**丢 `alpha==1` 像素**（实测存在）⇒ alpha 不再逐位相同。必须 numpy `alpha>=1` |
| 4 | sim-core 验收脚本"32 MiB 驱逐 = 0" | **两臂恒为 0** ⇒ 自查发现 `arm()` 把预算写死 `1<<40`，**假通过** |
| 5 | app-feat 第一版隔离"已复原" | 自己探针抓到：L2"边 import 边打补丁"导致 `keyboard_mixin` 绑到临时值，**复原成了空操作** |
| 6 | Lead："删那行 import 收益最直接" | app-sim 实测**可观测收益 = 0**（还有 3 条模块级路径） |
| 7 | Lead："`MapManager` 非单例 1.29 GiB" | sim-data 实测**全仓只有 1 处实例化**，那 1.29 GiB 是测试场景读数 |

**共同教训：任何"收益为 0"或"两臂完全相同"的结果，先怀疑开关/探针没生效，再怀疑结论。**

### 10.4 ✅ 最后一条 unresolved 已关掉（`evictions == 0` ⇒ PASS）

**卡点原来是我方数错了文件**：sim-core 报"本仓只有 74 个 `.dat`，凑不齐 207 条台风"，因此标为 unresolved。
Lead 复算：`typhoon/` = **210 文件 = 75 `.dat` + 132 `.txt` + 3 其它** ⇒ **207 个台风数据文件**（只数了 `.dat`）。
且实测可加载：`DataRepository.load_typhoon_files()` → **台风 207 / 报点 8491 / 0.53 s**，与 BEFORE 基线逐字吻合。

**用 W5 自己的 `w5_all.py` 原样复跑**（同 1280×720、同 207 台风、同循环与顺序）：

| | BEFORE | 上一版 AFTER | **本版（轮6+7）** |
|---|---|---|---|
| **`evictions`** | **548** | 204 | **0** ✅ **PASS** |
| `misses` | 801 | 729 | 706 |
| `items` | 256（撞条数上限） | 528 | **709（= misses+3 ⇒ 工作集完整保留）** |
| 字节 / 预算 | 15.27 MiB | 31.96 / **32 MiB（99.88%）** | **21.85 / 32 MiB（68.3%）** |
| 余量 | — | 已顶满 | **10.15 MiB（31.7%）** |
| 每键 | 62.6 KB | 63.5 KB | **32.3 KB** |

**⇒ 判据 `evictions == 0` 达标。** sim-core 推算 ≈22.4 MB vs 实测 **22.91 MB**，差 2.3%。

**这一格还留下一个有普适价值的对照**：上一版的 204 驱逐**不是判据变松，而是预算当时被顶满**（99.88%）。
⇒ **"把上限调大"只把瓶颈从条数挪到字节；"把表面减小"才真正达标** —— 即 `perf-optimize` 验收清单里那条"用调参掩盖设计问题"的**实测反例**。

**顺带一条方法论要点**：名字绘制的**原始 RGBA sha 确实变了**（`d7c09718…`→`ea7de0a6…`），但差异**只在 `alpha==0` 的残留 RGB**（永不参与合成）。
⇒ **判"像素等价"必须声明口径**是"可见量"还是"含透明区"。本轮结论建立在两个**可见量**口径上：不透明底合成 sha、以及仅 `alpha>0` 的 RGB sha —— **8 例逐字节相同**；地图/陆地指纹 `31b04f67…`/`6bfd8c92…` 与 BEFORE **逐位相同（max|Δ|=0）**。

### 10.5 仍然记录在案的风险（未关）

**被改的代码路径（`draw_typhoon_name` / `NamePosDialog` / `_get_name_surf`）在 `tests/` 里 0 命中** ⇒ 上述验证**全靠临时探针，没有回归网**。
建议把断言固化为 `tests/test_name_pos_preview.py`（判据现成：ink 框一致 + 不透明画布 sha256 + 仅 `alpha>0` 的 RGB sha）。
另：sim-run 的**预览框 6/6 未独立复算**（如实标注）—— 它改用更强的替代：读了消费链证明**预览与地图绘制共用同一偏移源**，加上端到端可见像素零变化。

### 10.6 第八 / 九轮：配置写保护收口（2026-10-02）

**起因**：`config.json` 的 `hemisphere` **第三次**被探针改成 `south`（第 3 次由 `sim-run` 的 W5 裸探针造成，Lead 再次还原）。这是"靠纪律"必然失败的第 N 次证明。

**根因修复（采用更强方案：应用自证身份）**

| 文件 | 改动 |
|---|---|
| `app/config_store.py` | 新增 `declare_app/is_app/is_user_config_path`；五级优先级的 `resolve_config_path`；**非应用进程 ⇒ 回落临时路径 + 每进程一次 warning** |
| `main.py` | `os.chdir` 后、任何 `app.*` import 前**一行声明自己是应用** |
| `app/config.py` | `AppConfig.save()` 开头同源判定（补上**唯一已知的写入旁路**） |

**九臂验收全 PASS**：

| 臂 | 判据 | 结果 |
|---|---|---|
| A | 裸探针 `ConfigStore.commit()` | 用户文件 sha **逐位不变**，写落 `%TEMP%\tsim_nonapp_cfg_*` |
| B | **真实应用路径**（`main.py` 声明 + 真实 `TySim.save_config(force=True)`） | `fps_cap 60→61` **真的写进磁盘** ⇒ 用户"确定"照常存得住 |
| C | 回归 | `test_config` 5 / `test_sim_settings` 14 / `test_control_panel` 15 / `test_ace_engine` 19 = **53 passed**，config sha 跑前=跑后 |
| D | 逃生开关 `TSIM_CONFIG_ALLOW_WRITE=1` | 放行并确实写入 |
| E | 非 `config.json` 路径 | 完全不干预（爆炸半径收窄） |
| F | 裸探针 `AppConfig.save()` | 写落临时落点，用户文件 sha 逐位不变 |
| G | 真实应用路径 `AppConfig.save()` | 写进真实文件，磁盘值正确 |

**⚠ 这一轮最有价值的一条**：`app-feat` 的**第一版实现引入了回归** —— 把 override 无条件放最前 ⇒ conftest 的 `TSIM_CONFIG_FILE` 会**劫持任何 `config.json` 目标**，导致两个"在 `tmp_path` 做往返"的用例变红。而 `test_sim_settings.py:17` 自己就写着"**不写 config.json**" —— **只测 A 端就交的话，这条专门防污染的测试会反过来被保护逻辑弄红。**

⇒ **"两端验收"不只防"应用写不进去"，也防"保护逻辑误伤别人"。** 修法：`is_user_config_path()` 收紧为"仓库根那份 `config.json` 或裸文件名"，且 override 判定移到用户配置门之后（对非用户配置路径成为恒等函数）。
**已知代价**（如实记录）：仓库根以外的**绝对路径** `config.json` 不再受保护（换来不误伤）；裸文件名与仓库根那份仍全覆盖。

### 10.7 ⚠ 仓库完整性：一条比性能更紧急的事（需你决定）

**工作区里有 7 个模块从未入库，而已跟踪且已修改的文件正在引用它们**：

```
app/ 未跟踪模块（git ls-files 全部 0 条）：
  config_store.py  name_pos_dialog.py  sim_mode_dialog.py
  sim_time_dialog.py  sim_value_dialog.py  window_util.py
  ty_sim_mixins/sim_step_control.py
```

**先澄清一个我实测修正过的说法**：**HEAD 是自洽的，干净克隆能正常跑**（HEAD 里没有这些模块，也没有引用它们的代码；HEAD 77 个 `app/*.py` vs 工作区 84 个）。**问题不在 HEAD，在工作区的"半提交"。**

| 命令 | 后果 |
|---|---|
| `git commit -am`（只提交已跟踪） | ❌ **坏** —— 提交了引用缺失模块的代码 |
| `git clean -fd` | ❌ **坏** —— 删掉 7 个未跟踪模块，而已修改的已跟踪文件仍在引用 |
| `git add -A && git commit` | ✅ **安全** |

**严重度分两档**：
- `app/settings.py:28` 是**模块级** `from .window_util import (...)` ⇒ **启动即炸**
- `app/ty_sim.py:73/178` 对 `config_store` 是**函数内** import ⇒ **走到才炸**

**并且**：本轮两项配置保护（轮五的 `tests/conftest.py` 隔离、轮八的 `config_store` 写保护）**都住在未跟踪文件里** ⇒ **任何 checkout / 换机都会让写保护整体消失。**

⇒ **建议**：`git add -A && git commit`（一次把 309 个已跟踪改动 + 这 7 个新模块 + `docs/` + `output/` 一起入库）。**这一步同时消掉本报告反复提到的 B3 风险。**

**Lead 最终复核（集成后状态）**：14 个测试文件 **154 passed / 31.68 s**，0 failed；`config.json` 与还原备份**逐字节一致**（`hemisphere: north`）。**整个性能优化批次收口。**

---

### 10.8 回退基线已按轮次分层

| 基线 | 内容 | 用途 |
|---|---|---|
| `_backup/*.py` + `SHA256_BEFORE.txt` | **最原始**（本轮改动前） | 整体回到起点 |
| `_backup/map_mgr_v2.py` | alpha 之后、变体 A 之前 | 单步回退 |
| `_backup/state_v2/`（17 文件）+ `SHA256_STATE_V2.txt` | **第五轮结束时** | 回退全部第一~五轮 |
| `_backup/state_v3/`（8 文件）+ `SHA256_STATE_V3.txt` | **第六轮结束时** | 回退第六 ~ 七轮 |

⚠ **不要跨层用**。`SHA256_STATE_V2/V3.txt` 里的路径是**仓库相对路径**，需在仓库根执行 `sha256sum -c`。

**Lead 最终复核（集成后状态）**：13 个测试文件 **127 passed / 52.43 s**，0 failed。
