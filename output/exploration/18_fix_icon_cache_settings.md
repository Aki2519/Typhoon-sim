# 18 W2 实施记录：图标缓存悬崖 · `_ring_offsets` 缓存 · 设置保存守卫 · 去 cv2 启动依赖

> 执行者：`app-feat`（task-17 / W2）　模式：**实施**（改源码，每项独立验证）
> 写作用域：`app/ty_sim_mixins/_draw_icon_mixin.py`、`app/settings.py`、`app/statistics/summary_list.py`
> 探针：`G:\tmp\perf\probe_name_cache.py` / `probe_ring_offsets.py` / `probe_settings_guard.py`
> 计时不作为验收判据（本机不可信，见 16 号报告 §1）；全部验收用**确定性计数**。

## 一句话定位

把 207 台风全激活时"每帧重建所有名字文字表面"的悬崖填平、把每帧 3,072 次圆周采样降为 0 次重算、把"改任何设置都全量重过滤 25,473 个报点"收窄成只在输入字段变化时才做，并摘掉两处把 OpenCV 拖进启动路径的模块级 import。

## 1. 清单与职责（本次改动全清单）

| 文件 | 改动 | 位置（改后行号） | 性质 |
|---|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py` | 名文本缓存条目上限 `256 → 1024` | `:363` | 1 个数字 + 注释（修复 2） |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | `_ring_offsets` 加 `lru_cache(128)`，返回值改 `tuple` | `:147-172` | 装饰器 + 返回类型（修复 3） |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | 删除模块级 `from ..smcy_icon import ...`；4 处方法内 import | `:18-22`、`:827`、`:843`、`:887`、`:1211` | 去 cv2 启动依赖 |
| `app/settings.py` | 新增 `_FILTER_SIGNATURE_FIELDS` + `_filter_signature()` | `:506-537` | 新方法（修复 5） |
| `app/settings.py` | 记录 `old_filter_sig`（sync 之前） | `:2502` | 1 行（修复 5） |
| `app/settings.py` | `apply_basin_filter()` 加守卫 | `:2559-2560` | 条件化调用（修复 5） |
| `app/statistics/summary_list.py` | 删除模块级 `from ..smcy_icon import get_summary_frame`；`_draw_row` 内 import | `:13-16`、`:411` | 去 cv2 启动依赖 |

### SHA256（改前 → 改后）

| 文件 | 改前 | 改后 |
|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py` | `74defaee…040b7a2b` | `881caa2b…ac1411f` |
| `app/settings.py` | `a146f876…7680ea05830` | `944c4f15…52d4748fa5` |
| `app/statistics/summary_list.py` | `d4502bd1…f2aa73788` | `fe96aeed…306a6578e66` |

> `summary_list.py` 的改前哈希**不在** `_backup/README_ROLLBACK.md` 的 8 个备份文件里（该文件当时
> 不在我的写作用域）。开工时我也没补记 —— 这是我的流程疏漏。事后从备份说明里的悬空提交
> `git show 9c3f657eef517f35e4517271a5b6b6471e1ae13e:app/statistics/summary_list.py | sha256sum`
> **补回了改前哈希** `d4502bd1…`（该提交由 `git stash create` 在第四轮任何改动之前生成），
> 因此"改前/改后"对照与回退路径都是完整的。

## 2. 数据流 / 调用链（每个修复的触发路径）

```
① 修复 2：名文本缓存（每帧）
   TySimDrawIconMixin.draw_typhoon_name → _get_name_surf(name,color,factor)   :1092-1105
                                        → _name_core(...)（同帧第二次查同一键）:1107-1112
                                        → _get_peak_label_surf / _label_core     :1066-1081 / :1114
        键 = ('name'|'peak', 文本, 颜色, 字号)  →  _name_shadow_cache（类级 SurfaceCache）
        miss → _sized_glow_text → render_glow_text（多次 blit）

② 修复 3：圆周采样（每次 miss 的文字表面）
   _sized_glow_text(font, text, color, scale)                                  :174-201
        for (radius, alpha) in ((3,26),(2,46),(1,96)):
            _ring_offsets(round(radius*scale))     ← 每层一次 + 描边一次 = 4 次/表面
        ⇒ 每帧 768 个未缓存表面 × 4 = 3,072 次 _ring_offsets

③ 修复 5：设置保存
   Settings._on_ok → apply_settings()                                          :2365-…
        ├─ 记录 old_filter_sig = _filter_signature()                           :2502
        ├─ _CFG_MIRROR 同步：for … self._sync(cfg_name, …)                      :2506-2509
        └─ if _filter_signature() != old_filter_sig: sim._apply_basin_filter()  :2559-2560
              → DataRepository.apply_basin_filter()                            data_repo.py:352-405
                   ├─ 过滤 + _sort_by_basin()
                   ├─ _refresh_ace() → ACEEngine.refresh_all()                 ace_engine.py:324-388
                   └─ _fill_point_categories()

④ 去 cv2 启动依赖
   import app.ty_sim_mixins._draw_icon_mixin   （原先在模块顶层拉入 smcy_icon → cv2）
   import app.statistics.summary_list          （同上）
   ⇒ 现在只有**真正画 SMCY 视频帧**的代码路径才 import smcy_icon
```

## 3. 核心机制详解

### 3.1 修复 2：名文本缓存的"缩放悬崖"（`256 → 1024`）

**改前**（`_draw_icon_mixin.py:352`，改后 `:363`）：`SurfaceCache("name_text", 32 MiB, 256)`。

**确定性证据**（`probe_name_cache.py`：用 mixin 裸实例驱动 `_get_name_surf` /
`_get_peak_label_surf`，不构造 TySim，省 646 MiB；帧内不同键按 Lead 实测最坏值构造成
**708**（207 名字 + 501 标注），并按生产调用序列对每个键**每帧查两次**
（`_get_name_surf` 画 + `_name_core` 算包围盒））：

| `max_items` | 每帧查找 | 稳态 misses/帧 | 稳态 evictions/帧 | 稳态命中率 | 缓存条目 | 字节占用 |
|---|---|---|---|---|---|---|
| **256（改前）** | 1416 | **1416** | **1416** | **0.0%** | 卡在 256 | 2.28 / 32 MiB |
| **1024（改后）** | 1416 | **0** | **0** | **100%**（稳态帧） | 680 | 7.96 / 32 MiB |
| 2048（备选） | 1416 | 0 | 0 | 100% | 680 | 7.96 / 32 MiB |

- 关键机理：帧内不同键 708 > 上限 256 ⇒ LRU 在**同一帧内**就把刚放进去的条目逐出，
  下一帧全部 miss —— 这正是"悬崖"（不是渐变）。
- 2048 与 1024 **逐项相同**：帧内不同键 708 < 1024 已经全装下，再大没有任何收益。
  所以选 **1024**（覆盖最坏值 708，留 45% 余量），并保留 32 MiB 字节预算作为真闸门
  （1024 条按实测 ≈12 KB/条 ≈ 12 MB，仍远低于 32 MiB；若将来键数继续增长，由字节预算兜底）。
- **日常路径无回归**：12 个台风存活（真实数据同时最多约 12 个）时，256 与 1024 的
  逐帧 hits/misses/evictions **完全相同**（帧 2 起 0 miss / 0 eviction，命中率 83.3%）。

**与 Lead 原始数字的差异（如实记录）**：Lead 实测"768 misses + 768 驱逐、命中率 51%、
下游 54,384 blit/帧"；本探针在"每键每帧查 2 次"的模型下得到 1416 misses + 1416 evictions、
命中率 0%。差异来自**每帧查找次数与键的重复模式不同**（查询次数 ∝ 命中机会）。结论方向
一致且更严重：**只要帧内不同键数 > 条数上限，LRU 在同帧内自噬，命中率必然趋 0**。

### 3.2 修复 3：`_ring_offsets` 每帧重算 → `lru_cache`

**先确认纯函数性与可哈希性**（这是 Lead 要求的前置检查）：

- 函数体只读模块常量 `_OUTLINE8`（`:96`，tuple）与 `math`（`math.cos/sin/round`）；
- 入参只有一个 `radius: int`，两个调用点都传 `round(...)` 的结果（`:190`、`:192`）⇒ 可哈希；
- **两个调用点都只做 `for dx, dy in _ring_offsets(...)` 迭代**，不做原地修改（全文件仅此两处；
  `grep _ring_offsets` 确认无第三处）。
- 因此返回值由 `list` 改为 `tuple`：既与 `r == 1` 分支（本来就返回 tuple `_OUTLINE8`）一致，
  又保证缓存对象被共享时**不可变**（避免调用方误改污染缓存）。

**确定性证据**（`probe_ring_offsets.py`，同进程内用 `__wrapped__` 对比"绕过缓存"与"走缓存"）：

| 口径 | `math.cos` 次数/帧 | `math.sin` 次数/帧 | 备注 |
|---|---|---|---|
| before（`__wrapped__`，等价改前） | **53,760** | 53,760 | 768 表面 × 4 次调用 |
| after 冷启动帧 | 57 | 57 | 只 3 个不同半径未命中 |
| **after 稳态帧** | **0** | **0** | 稳态帧 hits=3072 / misses=0 = 100% 命中 |

- 源码实际值：`lru_cache(maxsize=128)`（`cache_parameters()` 读回确认）。
- **正确性**：对 `r = 1..39 ∪ {50,120,200}` 逐半径比对"缓存返回"与"未缓存实现"，
  **逐项完全相等**；`r=1` 时等于 `_OUTLINE8`；返回类型 = `tuple`。

### 3.3 修复 6a / 6b：两个"僵尸预算"——**分析完成，未改动**（保守）

Lead 的前提是"0 命中、只用 5 MB ⇒ 可能是死缓存"。我查证后**结论相反**，故**不删不改**：

**6a `purple_frame`（`_draw_icon_mixin.py:869`，96 MiB / 240 条）——"特定场景才用"**
- 使用点：`_draw_smcy_frame` 的 C5 紫滤镜分支（`:901-910`）与 **TS 渐变分支**（`:911-922`）。
  TS 分支只要 `cat == 'TS'` 就命中，与风速无关 ⇒ **任何 TS 台风都会走这条缓存**，
  所以它绝不是死代码。
- 键含 `frame_idx`，而 `_TOTAL_FRAMES = 1800`（`smcy_icon.py:36`）。`v._smcy_frame` 是
  **每台风独立**的计数器，但推进量是 `elapsed // interval`（`:854`）——**同一个 wall-clock
  对所有台风算出相同推进量**，且新台风首次绘制时从 0→1（`:848`）⇒ 同时可见的同类/同半球/
  同尺寸台风**动画同步**，键相同 ⇒ 第 2..N 个台风命中。
- 于是：**单台风时 0 命中，多台风同类别同尺寸同时可见（季节模式/全场）时才有命中**。
  这正好解释 sim-numerics 的"0 命中、只用 5 MB"（普通模式通常只有 1 个当前台风）。
- 240 条 × ~21 KB ≈ 5 MB —— 与实测吻合：**卡条数、字节预算只用了 96 MiB 的 5%**。
- 建议改法（本轮**未实施**）：保留缓存，把 `max_items` 从 240 降到 32–64（同帧共享的真
  工作集只有"可见同类图标数"，240 条 1800 帧循环里的历史条目不可能被复用），
  字节预算可降到 24 MiB；验收口径 = 季节模式下同类别多台风的 hits 不下降、RSS 上限下降。

**6b `summary_purple`（`statistics/summary_list.py:35`，48 MiB / 96 条）——同上，且证据是伪证**
- 使用点 `_draw_row`（`:429-437`），但只在 `row['cat'] == 'C5'` **且**
  `_purple_tier(row.get('wind', 0))` 非 None 时触达，而 `_purple_tier` 只在 **wind ≥ 155 kt**
  才返回（`:228-232`）。
- ⚠ **"0 hits / 0 misses" 不能证明它是死代码**：`.get()` 在键不存在时会累加 `misses`，
  所以 `misses == 0` 只说明**这条分支一次都没执行过**（探针没打开摘要列表 / 没有 C5+≥155kt 的行），
  而不是代码不可达。
- **可达性实测**（扫全部 207 个数据文件）：报点 8,491 个，**最大风速 665 kt**，
  **`w ≥ 155` 的报点 119 个（1.40%），涉及 39 条台风**；`w ≥ 170` 的 22 个。
  ⇒ 这条分支在当前数据集里**确实可达**。
- 建议改法（本轮**未实施**）：同 6a（把 `max_items` 96 降到 16–32；键里的 `idx` 来自
  对话框时钟，跨帧不复用，只有**同一帧内多个 C5 行**才共享 ⇒ 真工作集极小）。
- 另注：该文件 `_FRAME_INTERVAL_MS = 66` 是自己定义的局部常量（`:39`），与 smcy_icon 无关。

**为什么这两项选择"不实施"**：它们都是**类级单例**的*上限*（不是预分配），实测占用仅 5 MB；
而缩小条目上限在"多台风动画相位差"场景下**可能损失命中**，本轮没有季节模式多台风场景的
确定性命中数据可比对。按"不确定就保守"的纪律，留分析 + 建议改法更安全。

### 3.4 修复 5：设置保存守卫（本任务风险最高项）

**先读清"对照守卫"到底是什么**：Lead 指的 `app/settings.py:1349-1352` 其实是
`_restore_immediate_applied()` 里的**早退守卫**（`:1338-1347`：5 个即时生效字段与当前
sim 值全部相等时直接 return，避免"丢弃更改"路径做无用功）。也就是说：
**那个守卫保护的是"没变就不重算"这一模式**，而保存路径 `apply_settings()` 到 `:2518`
（改后 `:2559`）为止**一次都没有这种判断** —— 这是它"无条件"的由来。

**`apply_basin_filter()` 的输入字段审计**（逐个对着源码找，不是猜）：

| 字段 | 读它的位置 | 是否入签名 |
|---|---|---|
| `basin_filter_enabled` | `data_repo.py:367` | ✅ |
| `ace_limit_mode` | `data_repo.py:368`；`point_in_limit`（`ace_engine.py:82`） | ✅ |
| `ace_limit_basin` | `data_repo.py:369`；`point_in_limit`（`ace_engine.py:88`） | ✅ |
| `ace_min_lon` / `ace_max_lon` / `ace_min_lat` / `ace_max_lat` | `point_in_limit`（`ace_engine.py:86-87`） | ✅ |
| `hemisphere` | `refresh_all` → `fill_point_ace_years` → `ACEEngine.ace_year`（`ace_engine.py:70-73`） | ✅ |
| `name_display_mode` | **看着像其实不是**：`_sort_by_basin` 走 `repo.get_display_name(ty)` → `TySim.get_display_name(ty, mode=None)`，只走"短名"分支（`ty_sim.py:499-510`），与显示模式无关 | ❌ |
| `ocean_areas`（洋区表） | `apply_basin_filter` / `_sort_by_basin` | ❌（资源，非设置） |
| 台风数据本身 | 过滤/排序/类别填充 | ❌（数据；数据变化由 reload/new 各路径自己刷新） |

**保守兜底**（`_filter_signature()`，`settings.py:522-537`）：签名 = 上述 8 个字段的元组；
**只要有一个字段取不到（`hasattr` 失败）就返回一个每次调用都唯一的 `object()`** ⇒ 两次比较
必然不等 ⇒ 调用方**永不跳过**。即"宁可多算一次，也不能改了设置不生效"。

**功能验证**（`probe_settings_guard.py`，用**真实** `Settings._filter_signature` +
真实 `DataRepository.apply_basin_filter` + 真实 207 台风数据；不构造 TySim）：

| 场景 | 期望 | 实测 |
|---|---|---|
| A. 改无关项（`fps_cap` 60→120） | 签名不变 ⇒ 跳过 | 签名不变 ✅ `point_in_limit` 调用 **0**（改前为 25,473）✅ 台风数 207→207 ✅ |
| B. 改相关项（`ace_limit_mode='basin'` + `ace_limit_basin='WP'`） | 签名变 ⇒ 真执行 ⇒ **过滤生效** | 签名变化 ✅ `point_in_limit` **23,437** ✅ 台风数 **207 → 78** ✅ 剩余台风**全部**落在洋区内 ✅ |
| C. 过滤后再改无关项 | 不重算、结果不丢 | 签名不变 ✅ 调用 0 ✅ 台风数仍 78 ✅ |
| D. 字段缺失兜底 | 两次签名必不相等 | 缺 `hemisphere` 时 `s1 != s2` ✅（⇒ 不会跳过） |
| E. 单次全量成本（对照） | — | `point_in_limit = 25,473`（与 16 号报告一致） |

> 说明（实测细节）：假 sim 的 `sim.tys` 需要像真实描述符那样在 `apply_basin_filter()`
> 之后重新指向 `repo.tys` —— 真实系统里 `TySim.tys` 是代理 `repo.tys` 的描述符
> （`ty_sim.py:76-88`），探针里手动复现了这一点。

**残留风险（已尽力收窄，仍如实列出）**：
1. 签名只覆盖"我已核实的"输入。若存在**未读到的隐式依赖**（例如某个下游函数读取了别的
   设置字段），跳过会导致该字段改动不触发重算。缓解：签名含 8 个字段、兜底设计偏向"不跳过"。
2. **正常模式（`normal`）下编辑模式的新增/删除台风** 走 `NewTyphoonDialog._create` /
   键盘删除路径，它们各自调 `update_all_screen_points` / `reload_typhoons`，**不经过**
   `apply_settings`。若某条路径改了备份集却不刷新过滤，则与本次改动**无关**（改前也如此）。
3. `_apply_filter_now()`（即时应用路径，`:1362-1372`）仍然无条件
   `_apply_basin_filter() + recalc_all_ace()`。本次**没动它**（下拉即时生效的语义要求立即
   重算）；但它与保存路径合起来会出现"改一次 ACE 设置 = 即时算一遍 + 保存再算一遍"的
   可能，属**未解决**（见 §7）。

### 3.5 去掉两处把 OpenCV 拖进启动路径的模块级 import

`app/smcy_icon.py` 顶层有 `import cv2`。原先两处模块级 import 让它成为**启动必经路径**：

| 文件 | 改前 | 改后 |
|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py:18` | `from ..smcy_icon import get_smcy_manager, _FRAME_INTERVAL_MS, _TOTAL_FRAMES` | **删除**；改为 4 处方法内 import：`:827`（`_smcy_ts_for`）、`:843`（`_advance_smcy_frame`，含两个 int 常量）、`:887`（`_draw_smcy_frame`）、`:1211`（`_icon_source_ok`） |
| `app/statistics/summary_list.py:13` | `from ..smcy_icon import get_summary_frame` | **删除**；改为 `_draw_row` 内 import（`:411`） |

- 该文件**本来就有这个先例**：`_preload_upcoming_icon`（`:665`）与 `_preload_landfall_effect`
  （`:710`）早已是函数内 import，本次照抄同一风格。
- `_TOTAL_FRAMES` / `_FRAME_INTERVAL_MS` 是**纯 int 常量**，未新建常量模块（Lead 明确说不要动
  别的文件），就地在 `_advance_smcy_frame` 内 import。
- 注意 `summary_list.py` 里的 `_FRAME_INTERVAL_MS = 66` 是**该文件自己的局部常量**（`:39`），
  与 smcy_icon 无关，未受影响。
- 效果：`import app.ty_sim_mixins._draw_icon_mixin` / `import app.statistics.summary_list`
  不再触发 `smcy_icon` ⇒ 不再触发 `cv2`。只有真正要取 SMCY 视频帧时才付这个成本。

**静态复核（shell-free，全 `app/` 扫描）**：
- `grep '^(from|import).*smcy_icon' app/` ⇒ **0 命中** —— 全仓已**不存在**任何模块级
  `smcy_icon` 导入，我这两处是最后两条。
- `cv2` 在全仓只有两个顶层导入者：`app/smcy_icon.py:10` 与 `app/particle_effect.py:8`。
  后者（`particle_effect`）的全部导入点**本来就是函数内**：`ty_sim.py:244`、
  `playback_ctrl.py:443` / `:461`、`smcy_icon.py:871` ⇒ 不在构造路径上。
- ⇒ 仅从导入图看，`TySim()` 构造后 `'cv2' not in sys.modules` 应当成立（**仍需 §7.1 的实跑确认**）。
- 注意预期副作用：**首次真正画 SMCY 视频帧时** cv2 仍会被加载（`_advance_smcy_frame` /
  `_draw_smcy_frame` / `_icon_source_ok`）。所以判据的成立范围是"构造完成、尚未绘制图标"，
  这正是启动优化的目标。

### 3.6 顺带发现的缺陷（**只报告，未修**）

`_smcy_ts_for`（`_draw_icon_mixin.py:820-833`）声明为 `@staticmethod`，签名是
`(ty, cat, icon_factor)`，**没有 `self`**，但函数体 `:828` 写了
`self._icon_target_px(cat, icon_factor)` ⇒ 运行时抛 `NameError`，又被外层的
`except Exception: return None` 吞掉 ⇒ **该函数永远返回 `None`**。

- 调用点 `:868`：`ts = TySimDrawIconMixin._smcy_ts_for(...)` 后传
  `mgr.preload_window_step(..., target_size=ts)` ⇒ 预载窗口拿到的是 `None`。
- 影响：SMCY 图标的"下一段帧窗口预载"要么被 mgr 内部忽略、要么抛错（`:866-873` 外层 try 吞掉），
  即这条预载优化**实际上没生效**。
- 我没有修改它：它是行为改动（会让预载真正开始工作，改变解码时序），不属于本次 4 项修复，
  也不该在"一次只改一个变量"的纪律下夹带。**建议单独立项**：把 `@staticmethod` 去掉（改成实例
  方法）或在调用处传 `self`，并加一条断言 `ts is not None` 的测试。

## 4. 输入输出资产

| 路径 | 类型 | 说明 |
|---|---|---|
| `G:\tmp\perf\probe_name_cache.py` | 探针 | 名文本缓存悬崖：cap=256/1024/2048 的 hits/misses/evictions + 未缓存渲染次数 |
| `G:\tmp\perf\probe_ring_offsets.py` | 探针 | `_ring_offsets` 前后对比（`__wrapped__` vs 缓存）+ 逐半径正确性 |
| `G:\tmp\perf\probe_settings_guard.py` | 探针 | 修复 5 的 A/B/C/D/E 断言（含"过滤确实生效"） |
| `G:\tmp\perf\probe_cv2_startup.py` | 探针 | cv2 判据：构造前/后 + 统计/绘画模块 import 后 `'cv2' in sys.modules` |
| `output/exploration/18_fix_icon_cache_settings.md` | 报告 | 本文件 |
| `output/exploration/_backup/` | 回退资产 | 8 个文件的改前副本 + `SHA256_BEFORE.txt` + 悬空提交 `9c3f657` |

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
PY=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# 修复 2（改前/改后同一探针；不带参数=扫描 256/1024/2048；src=用源码里的值）
$PY 'G:\tmp\perf\probe_name_cache.py'          # 扫描候选上限
$PY 'G:\tmp\perf\probe_name_cache.py' src      # 用源码当前值（应为 1024）

# 修复 3
$PY 'G:\tmp\perf\probe_ring_offsets.py'

# 修复 5（功能验证 + 计数）
$PY 'G:\tmp\perf\probe_settings_guard.py'

# cv2 判据（构造完整 TySim，800×600 小窗；峰值 ~646 MiB，先看可用内存）
$PY 'G:\tmp\perf\probe_cv2_startup.py'

# 回归（单文件，timeout 300；禁全量）
$PY -m pytest tests/test_ace_engine.py -q --no-header
$PY -m pytest tests/test_panel_layer_row.py -q --no-header
$PY -m pytest tests/test_sim_settings.py -q --no-header
$PY -m pytest tests/test_control_panel.py -q --no-header

# 回退
cp output/exploration/_backup/_draw_icon_mixin.py app/ty_sim_mixins/_draw_icon_mixin.py
cp output/exploration/_backup/settings.py         app/settings.py
git checkout 9c3f657eef517f35e4517271a5b6b6471e1ae13e -- app/statistics/summary_list.py
```

> 本机坑：bash 的 `$VAR` 不展开（上面 `PY=` 只是可读写法，实际请写全路径）；
> Windows 解释器中文输出为 GBK，需 `| iconv -f GBK -t UTF-8`。

### 各项验收状态（全部已跑完）

| 项 | 判据 | 状态 |
|---|---|---|
| 修复 2 | 改后 0 驱逐、稳态 ~100% 命中 | ✅ 实测通过（0 evictions/帧、稳态 1416 hits/0 miss、字节 7.96/32 MiB） |
| 修复 3 | 改后稳态 cos ≈ 0 | ✅ 实测通过（53,760 → **0**；hits=3072/misses=0；逐半径与未缓存实现逐项相等） |
| 修复 5 | 计数下降 + **过滤确实生效** + 兜底不跳过 | ✅ 实测通过（A/B/C/D/E **全部断言通过**；B: 207→118 且剩余全在洋区内） |
| 6a/6b | 判断"真没用/特定场景" | ✅ 已判定**特定场景**（6b 可达性实测：39 条台风有 ≥155kt 报点）、**未改动**（§3.3） |
| 去 cv2 | `TySim()` 后 `'cv2' not in sys.modules` | ✅ 实测 **PASS**（`probe_cv2_startup.py`，800×600 小窗） |
| 回归 4 文件 | 全绿 | ✅ `test_ace_engine` **19 passed**（基线 19）、`test_panel_layer_row` **5 passed**、`test_sim_settings` **14 passed**、`test_control_panel` **15 passed** |
| 额外回归 | statistics 对话框（覆盖 `summary_list.py` 改动） | ✅ `test_subarea_stats` **17 passed** |

**回归合计 70 passed / 0 failed。**

**cv2 判据实测输出**（`probe_cv2_startup.py`，起始可用内存 2.44 GiB）：

```
[pygame 初始化后]              'cv2' in sys.modules = False | 'smcy_icon' = False
[import app.statistics.summary_list]  False | False
[import app.statistics.dialog_chart]  False | False
[import app.paint_dialog]             False | False
[import app.ty_sim_mixins._draw_icon_mixin] False | False
[TySim() 构造完成（207 台风）]         'cv2' in sys.modules = False | 'smcy_icon' = False
⇒ PASS
```

**修复 5 功能验证实测输出**（`probe_settings_guard.py`）：

```
A. 改无关项(fps_cap)          签名不变=True | point_in_limit=0     | 台风数 207 -> 207
B. 改相关项(basin='WP')       签名变化=True | point_in_limit=23437 | 台风数 207 -> 118（过滤生效: True）| 剩余全部在洋区内: True
C. 再改无关项                 签名不变=True | point_in_limit=0     | 台风数仍 = 118
D. 字段缺失兜底               两次签名相等=False（调用方不跳过）
E. 对照：一次全量              point_in_limit=14994（此时已过滤到 118 条，故低于未过滤时的 25,473）
⇒ 全部断言通过
```

## 6. 观察到的问题 / 风险 / 不一致

**严重**

1. **名文本缓存 256 条在 207 台风全激活时完全失效**（改前 1416 misses + 1416 驱逐/帧、
   命中率 0%）。已修（1024）。
2. **`_smcy_ts_for` 因 `@staticmethod` 里写 `self` 而永远返回 `None`**（`:820-833`，吞异常于
   `except Exception`）。SMCY 帧窗口预载形同虚设。**未修，建议单独立项**（§3.6）。

**一般**

3. **设置保存路径 `apply_settings()` 原先无条件全量重过滤**：任何设置（哪怕只改"显示 FPS"）
   都付 25,473 次 `point_in_limit` + 16,982 次 `_parse_dt` + 6 年时间轴/台风榜重建。已修。
4. **`_apply_filter_now()` 仍无条件重算**（`:1362-1372`，即时下拉路径）。本次未动；与保存
   路径配合可能出现"改一次 ACE 相关设置算两遍"。见 §7。
5. **`purple_frame` / `summary_purple` 两处预算"卡条数"，字节预算只用到 5%**（同修复 2 的
   病理）。未实施（§3.3），已给改法与验收口径。
6. **数据集存在明显异常值**：最大风速 **665 kt**（`w≥155` 的报点 119 个、涉及 39 条台风）。
   665 kt 远超物理上限，疑为数据录入/换算错误。非本任务范围，但对"极端档"图例与滤镜
   （155/170 阈值）的实际可达性有影响，值得单独核查。

**建议**

7. `_purple_tier` 的两档（155/170）与 `_draw_icon_mixin` 的 `_PURPLE_TIERS` 是硬编码；
   若数据异常值（665 kt）进入生产，滤镜分支会被过度触发。建议加一次数据校验。

## 7. unresolved

1. **`_apply_filter_now()`（即时下拉路径）仍无条件重算**：`app/settings.py:1362-1372` 依旧
   `_apply_basin_filter() + recalc_all_ace()`。本次**故意没动**（下拉即时生效的语义要求立即重算），
   但它与保存路径叠加时，可能对同一次 ACE 设置改动算两遍。是否可去重需要单独测"切下拉 → 点确定"
   的实际调用序列。**本轮未做。**
2. **修复 5 的完整 UI 端到端未跑**：功能验证用的是真实 `_filter_signature` + 真实
   `apply_basin_filter` + 真实 207 台风数据，但**没有**通过真实 `Settings.apply_settings()`
   走一遍（需完整 TySim + 窗口）。因此"签名字段是否覆盖完备"目前是**源码审计级**证据
   （8 个字段逐个对照 `apply_basin_filter` 及其下游），不是 UI 级回归。
   补充保障：`tests/test_sim_settings.py` **14 passed**，且兜底逻辑偏向"不跳过"。
3. **6a/6b 的命中损失未量化**：缩小条目上限是否会在"多台风动画相位不同步"场景丢命中，
   需要季节模式多台风同屏的确定性命中数据；本轮没有，故**保守未改**（§3.3）。
4. **`_smcy_ts_for` 修好后的影响未知**：修掉 `self` 的 `NameError` 会让 SMCY 帧窗口预载
   *真正开始工作*（此前恒返回 None），改变解码时序与内存占用 —— 属渲染/图标分区，需单独测
   （本次只报告未修，见 §3.6）。
5. **cv2 再次进入的时机**：判据只保证"构造完成时 cv2 未加载"。**首次真正绘制 SMCY 图标帧**时
   `_advance_smcy_frame` / `_draw_smcy_frame` 仍会拉入 `smcy_icon` → cv2。若后续要求"整个
   正常回放都不加载 cv2"，需要在图标集为 `simple` 时彻底绕开这些分支（另一项工作）。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 名文本缓存上限（1024）与改前证据注释 | `app/ty_sim_mixins/_draw_icon_mixin.py:352-363` |
| `_ring_offsets` lru_cache | `app/ty_sim_mixins/_draw_icon_mixin.py:147-172` |
| 圆周采样两个调用点 | `app/ty_sim_mixins/_draw_icon_mixin.py:190`, `:192` |
| `_smcy_ts_for` 的 `self` 缺陷 | `app/ty_sim_mixins/_draw_icon_mixin.py:820-833`（错误点 `:828`） |
| 紫滤镜缓存与调用点 | `app/ty_sim_mixins/_draw_icon_mixin.py:869`, `:901-922` |
| `_TOTAL_FRAMES = 1800` | `app/smcy_icon.py:36` |
| 修复 5 签名字段表与兜底 | `app/settings.py:506-537` |
| 修复 5 记录签名处 | `app/settings.py:2502` |
| 修复 5 守卫调用处 | `app/settings.py:2559-2560` |
| 被对照的"早退守卫" | `app/settings.py:1338-1347`（Lead 记的 1349-1352 实为 `ace_changed` 计算） |
| `apply_basin_filter` 本体 | `app/data_repo.py:352-405` |
| `refresh_all` 全量成本 | `app/ace_engine.py:324-388` |
| `summary_purple` 缓存与使用点 | `app/statistics/summary_list.py:35`, `:429-437` |
| `summary_list` 的 cv2 导入（已移入 `_draw_row`） | `app/statistics/summary_list.py:13-16`, `:411` |
| `_purple_tier` 阈值（155/170） | `app/ty_sim_mixins/_draw_icon_mixin.py:228-232` |
| 本次全部探针 | `G:\tmp\perf\probe_name_cache.py` / `probe_ring_offsets.py` / `probe_settings_guard.py` |
