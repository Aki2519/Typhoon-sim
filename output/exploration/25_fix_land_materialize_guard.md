# 25 给「释放后再次物化 land 像素」加护栏（app/map_mgr.py）

## 一句话定位

W4b/变体 A 让 `land.png` 的 222.47 MB 像素在"建完 alpha 表之后"被释放（稳态常驻 −222.47 MB），但**任何路径一旦再索取全尺寸像素，这 222.47 MB 会静默回来**。本轮把这条隐含约束变成**代码里的护栏**：释放后再物化 ⇒ 进程内第一次打一条 `logging.warning`（含调用方位置）+ 计数累加；另附一个默认关闭的严格断言开关给 CI 用。

> 本轮**只改 `app/map_mgr.py`**（823 行，+64 行）；唯一可写的另一份是本报。
> 改前 sha256 `f03340cebb286526664cfec47a360ba4c85880b726c82c5f00209a613d2387bf`
> （= `_backup/state_v2/map_mgr.py`，与工作区改前逐字符一致，已复核）
> → 改后 `c3ab53742edcaf09ae43120da4ce5a7f8c8fc45c0fe63c84de9197b228b8c3c5`。

---

## 1. 清单与职责

### 1.1 本轮改动点（全部在 `app/map_mgr.py`）

| # | 位置（改后行号） | 改动 | 性质 |
|---|---|---|---|
| 1 | `:4` | `import sys`（`_land_caller_hint()` 需要 `sys._getframe`） | 新增依赖（标准库） |
| 2 | `:45-59` | 护栏开关块：`LAND_MATERIALIZE_WARN`（默认 **True**）、`LAND_MATERIALIZE_STRICT`（默认 **False**）、进程级 `_LAND_MAT = {count, warned, last_caller}` | 新增 |
| 3 | `:62-68` | `land_materialize_stats()` —— 观测接口（探针/测试/运行期自检可读） | 新增 |
| 4 | `:71-82` | `_land_caller_hint()` —— 从栈里取**第一个不在本文件内**的帧，即真实调用方 `文件:行 in 函数()` | 新增 |
| 5 | `:85-103` | `_note_land_rematerialize()` —— 计数 + 一次性告警（或严格模式断言） | 新增 |
| 6 | `:605-610` | `_land_pixels()` 内插入护栏判定点（3 行新代码 + 2 行注释） | 新增 |

**没有改动**任何既有判定逻辑、返回值、缓存策略或渲染路径。

### 1.2 护栏的语义（一句话）

`_note_land_rematerialize()` 只在**一种情形**触发：`_land_pixels()` 被调用时 `_land_orig is None` **且** `_land_ok is True`
—— 即"**曾经拿到过真像素，之后被释放，现在又要**"。这正是会让 222.47 MB 常驻回涨的那一步。

```python
# app/map_mgr.py:602-610（节选）
        if self._land_orig is not None:
            self._land_ok = True
            return self._land_orig
        if LAND_RGBA_TRANSIENT and getattr(self, '_land_ok', False):
            # 曾经拿到过真像素、现在 _land_orig 为空 ⇒ 这是"释放后再次物化"
            _note_land_rematerialize()
        for path in (LAND_MASK, LAND_MASK_FALLBACK):   # ← 真正的解码仍在下面照常执行
```

**护栏不阻止解码**：构建期路径（掩码失效重建）本来就需要像素，任务要求"不要试图禁止合法路径"。

---

## 2. 全仓穷举：谁在取陆地像素（任务步骤 1）

grep 三个符号（`_land_orig` / `_land_pixels` / `_load_land_orig`），全仓**排除 `output/`（报告与备份）**后共 26 处，逐个判定：

### 2.1 ✅ 合法物化点 —— 构建期，必须要全尺寸像素（4 处，全在 `map_mgr.py`）

| 位置 | 谁 | 为什么合法 | 发生时机 |
|---|---|---|---|
| `map_mgr.py:644` | `_load_land_orig()` 兜底分支 | `_land_ok=False`（从未成功过）⇒ 不是"再物化" | 首次加载；加载失败时每次重试 |
| `map_mgr.py:647` | `_rebuild_land_and_overlay()` | 要把全尺寸掩码按视图缩成 `land_img`（屏幕尺寸） | `_ensure_land_mask()` 首调 |
| `map_mgr.py:733` | `_ensure_land_mask()` | 同上（`_land_orig is None` 时先取像素再重建） | 同上 |
| `map_mgr.py:765` | `is_land_at_geo()`，**仅当 `_land_geo_alpha is None`** | 要建 55.62 MB 地理 alpha 表（从 alpha 平面派生） | 首调（生产里由 `ty_sim.py:217` 在启动期强制触发） |

> 这 4 处在正常路径上**都不会触发护栏**：前 3 处发生时 `_land_ok` 还是 False；第 4 处发生在释放之前（顺序：`_rebuild_land_and_overlay` → `is_land_at_geo` 建表 → 释放）。**已由探针 [1] 实测确认 0 告警。**

### 2.2 ✅ 合法的**非**物化点 —— 只判 `is None`，走 1×1 占位（4 处，`app/`）

| 位置 | 代码 | 是否取像素 |
|---|---|---|
| `app/view_state.py:100` | `if map_mgr._load_land_orig() is None: return` | ❌ 不取（`_land_ok=True` ⇒ 占位符） |
| `app/playback_ctrl.py:295` | `if mm._load_land_orig() is None: return` | ❌ |
| `app/statistics/season_stats.py:173` | `if mm._load_land_orig() is None: return` | ❌ |
| `app/ty_sim_mixins/_draw_path_mixin.py:1288` | `if mm is None or mm._load_land_orig() is None: return recs` | ❌ |

这 4 处是变体 A 能成立的全部依据（`20_fix_map_decode.md` §4.6 逐行核对）。**它们不产生护栏告警**，也**不该**被改成 `_land_pixels()`。

### 2.3 ✅ 测试内的桩/注入（合法，且是约束来源）

| 位置 | 形态 |
|---|---|
| `tests/test_landfall_view_invariance.py:28`、`test_pan_reuse.py:28`、`test_seam_render.py:82` | 自定义 `_load_land_orig()` 桩方法 |
| `tests/test_subarea_stats.py:168` | `sim.map_mgr._load_land_orig.return_value = None` |
| `tests/test_landfall_view_invariance.py:182` | `mgr._land_orig = surf`（36×18 合成掩码，`object.__new__` 绕过 `__init__`） |

⇒ 这是 **W4b 保留属性名 `_land_orig`（不改叫 `_land_px`）**的原因（`20_fix_map_decode.md` §12.2-①），本轮沿用。

### 2.4 ⚠ 高风险模式 —— 渲染期索取全尺寸像素（**当前仓库内 0 处**）

- `_land_pixels()` 的调用点**全部**在 `map_mgr.py` 内部的上述 4 个构建期位置（grep 实测）；`app/`、`tests/`、`tools/` 里**没有任何外部调用方**。
- 已实测触发的唯一实例是**外部探针**：W5 的 land 专项探针在 `21_fix_verification.md` §6.2 里"自己把它触发了"（`_land_orig` 从 1×1 回到 10800×5400）。
- ⇒ **结论（实测）**：本轮**没有发现非法的生产调用点**；风险是"**未来新增**"，护栏的价值是把这件事从"静默"变成"可观测 + 可计数 + 可断言"。

### 2.5 边界：护栏会覆盖不到的两种情况（诚实声明）

1. **掩码失效重建**（`_land_alpha_bytes` 被清空后再跑 `_ensure_land_mask()`）：这是**合法构建路径**，但也会让 222.47 MB 回来 ⇒ **会**触发护栏。这是**真阳性**（内存确实回来了），不是 bug；告警里的调用方信息（`_ensure_land_mask`）能让人一眼区分"合法重建"与"渲染期乱要"。已写进 §6。
2. **直接给 `_land_orig` 赋值**（如测试注入）：绕过 `_land_pixels()` ⇒ 不触发护栏。这是有意的（测试注入不该刷告警），但也意味着护栏**只覆盖"经由 `_land_pixels()` 的物化"**。

---

## 3. 核心机制

### 3.1 `land` 像素的三态与释放点（现状，W4b 已实现）

```
_land_orig is None, _land_ok False   ①「从未加载」→ _load_land_orig() 返回 None(不可用)
_land_orig is Surface(10800×5400)    ②「像素常驻」→ _load_land_orig() 返回真像素
_land_orig is None, _land_ok True    ③「已释放」  → _load_land_orig() 返回 1×1 占位(可用)
                    │
                    └─ 此时若 _land_pixels() 被调用 → **重新物化 222.47 MB** ⇒ 【本轮护栏的触发点】
```

释放发生在 `is_land_at_geo()` 建表成功之后（`map_mgr.py:779`，`self._land_orig = None`）。

### 3.2 三种候选护栏的取舍（任务给的 a/b/c）

| 候选 | 成本 | 默认行为 | 能否被 CI 强制 | 结论 |
|---|---|---|---|---|
| **(a) 一次性告警** | 极低（仅一次日志） | 不变 | 不能（但可被日志断言） | ✅ **主选**：任务点名的"最低风险"；只增加可观测性 |
| (b) 可选严格断言 | 极低（默认关） | 不变（默认关时**零影响**） | **能** | ✅ **附带实现**：`TSIM_MAP_LAND_MATERIALIZE_STRICT=1` 时才 raise；已实测正常路径**不会**误触发 |
| (c) 给合法路径打标记 | 中（要在 3 处构建路径埋 marker + 维护状态机） | 不变 | 能 | ❌ **未采纳**：需要给 `_rebuild_land_and_overlay`/`_ensure_land_mask`/`is_land_at_geo` 传"我是合法构建"的上下文，改动面大、状态机易失配；而 (a) 的告警文本里已经带**调用方 `文件:行 in 函数()`**，人工区分成本几乎为零 |

### 3.3 计数语义（探针第一版把我自己坑了一次，值得记录）

护栏的 `count` 统计的是"**释放→物化**这个事件"，**不是 `_land_pixels()` 的调用次数**：

- 物化之后 `_land_orig` 又非常驻 ⇒ 后续 `_land_pixels()` 走 `:602-604` 的早退，**不加计数、不告警**（这是对的：此时没有新增内存）。
- 因此"重复调用不刷屏"要用"**重复释放 + 物化**"来测：我的探针第一版写的是"再调 3 次 `_land_pixels()`"，实测 `count` 停在 1 —— **判定 FAIL 是探针的期望写错了，不是护栏错**。改成 3 轮 `_land_orig=None; _land_pixels()` 后 `count` 1→4、告警恒为 1（见 §4.2）。这条已写进 `land_materialize_stats()` 的 docstring 语义里。

---

## 4. 验收结果（全部实测，判据均为确定性量）

探针 `/tmp/probe_land_guard.py`（`SDL_VIDEODRIVER=dummy`，2560×1540，单进程）：

```bash
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe
$P -u "G:/tmp/probe_land_guard.py"          # 正常/人为触发/开关关闭
$P -u "G:/tmp/probe_land_guard.py" strict    # 严格模式
```

### 4.1 [1] 正常路径（构造 → `_ensure_land_mask` → `is_land_at_geo` → 6 视角渲染 → 400 点陆地查询）

| 观测量 | 实测 |
|---|---|
| 护栏计数 | `{'count': 0, 'warned': False, 'last_caller': ''}` |
| **WARNING 条数** | **0** ✅ |
| `_land_orig` | **`None`（已释放）** ✅ |
| 陆地查询 | `is_land_at_geo` 命中 137 / `is_land_at_screen` 命中 159（正常出结果，非全 False） |
| **常驻块合计** | **327,081,600 B = 311.93 MB** ✅（与 `20_fix_map_decode.md` §12.3 的 311.93 MB **逐字节相同**，收益未退化） |
| 分项 | map 233,280,000 + `_land_geo_alpha` 58,320,000 + `land_img` 15,769,600 + `_land_alpha_bytes` 3,942,400 + `_cached_map_render` 15,769,600 + `_land_orig` **0** |
| 进程工作集 | 428.1 MB（含探针自身的 2560×1540 画面等开销） |

**判定：PASS —— 正常路径零告警、收益未退化。**

### 4.2 [2] 人为触发（释放后再要像素）

| 观测量 | 实测 |
|---|---|
| 第 1 次 `_land_pixels()` | 返回 **Surface (10800, 5400)**；常驻回涨 **222.47 MB**（复现了 W5 的约束） |
| **WARNING 条数** | **恰好 1** ✅ |
| 告警文本 | `land 像素在释放后被**再次物化**: 10800×5400 = 222.47 MB 会重新常驻, W4b 的稳态收益当场失效。调用方 G:/tmp/probe_land_guard.py:125 in <module>()。正常渲染路径不应索取全尺寸陆地像素(请用 land_img/_land_alpha_bytes); 本次只告警一次…` |
| 含调用方信息 | ✅ `文件:行 in 函数()` |
| 再制造 3 轮"释放→物化" | `count` **1 → 4**，**WARNING 条数仍为 1**（不刷屏）✅ |

**判定：PASS —— 恰好 1 条告警、不刷屏、可复现 222.47 MB 物化。**

### 4.3 [3] 开关关闭（`TSIM_MAP_LAND_MATERIALIZE_WARN=0`）

`warning 关闭后 WARNING 条数 = 0`；`count` 仍正常累加（5）。**行为只少一条日志，其余不变** ✅。

### 4.4 严格模式（`TSIM_MAP_LAND_MATERIALIZE_STRICT=1`）

- [1] 正常路径**照旧 PASS**（不误触发 ⇒ 护栏不会在合法构建期炸生产）；
- [2] 第 1 次再物化即 `AssertionError: land 像素被再次物化(222.47 MB 常驻回归), 调用方 …:125 in <module>(); 渲染路径请改用 land_img/_land_alpha_bytes` ✅（strict 下 `warned` 保持 False、0 条 warning —— 断言优先于日志，符合设计）。

### 4.5 渲染/判定等价性（回归护栏有没有碰到画面）

`cmp_map_render.py`（OLD = `_backup/map_mgr.py`(alpha 之前) vs NEW = 当前）：

| 项 | OLD | NEW | 一致 |
|---|---|---|---|
| 6 视角底图像素 sha（cover-min / scale1 / scale2 / **scale8** / **跨 0-360 缝** / corner-bottom） | 6 个 sha | **同 6 个** | ✅ 全等 |
| `_land_geo_alpha` sha | `c755ccff7edab656` | 同 | ✅ 逐字节 |
| `_land_alpha_bytes` sha | `998f30a112971527` | 同 | ✅ 逐字节 |
| `is_land_at_geo` 36×72 网格 | `04595b23fa1571c0`（842 陆地格） | 同（842） | ✅ |
| `is_land_at_screen` 网格 | `56b611415b0501c2`（218） | 同（218） | ✅ |
| 几何 `(img_w, img_h, min_scale)` | (10800,5400,0.071111111111) | 同 | ✅ |
| `land_shape` | (10800, 5400) | **None** | ⚠ **唯一差异，且正是本次要的效果**（`20_fix_map_decode.md` §12.7-④ 的已知"假阴性"） |
| 进程工作集 | **537.5 MB** | **315.7 MB** | ✅ **−221.8 MB**（确定性块差 = −233,280,000 B = −222.47 MB；WS 含其它开销故略有出入） |

> 脚本自打印的"总判定 ❌"是**假阴性**：它把 `land_shape` 当等价项，而那一项就是"像素被释放"本身。其余 15 项全 OK。

### 4.6 回归（单文件 `timeout 300`，禁全量）

| 文件 | 结果 |
|---|---|
| `tests/test_map_resize.py` | **4 passed** |
| `tests/test_ocean_area.py` | **4 passed** |
| `tests/test_landfall_view_invariance.py` | **8 passed** |
| `tests/test_subarea_stats.py` | **17 passed** |
| **合计** | **33 passed / 0 failed** |

---

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# ① 护栏验收（零告警 / 恰好 1 条 / 内存不退化 / 关掉开关）
$P -u "G:/tmp/probe_land_guard.py"
# ② 严格模式（CI 用：渲染期索取全尺寸像素立刻失败）
$P -u "G:/tmp/probe_land_guard.py" strict
# ③ 画面与判定等价性（16 项里 15 项必须全等, 唯一允许差异是 land_shape）
$P -u "G:/tmp/cmp_map_render.py"
# ④ 回归（单文件）
for f in test_map_resize test_ocean_area test_landfall_view_invariance test_subarea_stats; do
  timeout 300 $P -m pytest tests/$f.py -q --no-header -p no:cacheprovider
done
# ⑤ 校验只改了这一个文件
sha256sum -c output/exploration/_backup/SHA256_STATE_V2.txt 2>/dev/null | grep -v OK
```

**改前/改后 sha256**

```text
改前  f03340cebb286526664cfec47a360ba4c85880b726c82c5f00209a613d2387bf  app/map_mgr.py
改后  c3ab53742edcaf09ae43120da4ce5a7f8c8fc45c0fe63c84de9197b228b8c3c5  app/map_mgr.py
```

**回退**

```bash
cp output/exploration/_backup/state_v2/map_mgr.py app/map_mgr.py   # ✅ 本轮正确回退点(f03340ce)
# 运行时关护栏（无需改代码; 本机 shell 吞 env 前缀, 建议在 python 内设置后再 import）
#   os.environ['TSIM_MAP_LAND_MATERIALIZE_WARN'] = '0'
```

> ⚠ 不要用 `_backup/map_mgr.py`（alpha 之前，`c13f5284…`）或 `_backup/map_mgr_v2.py`（变体 A 之前，`05df5172…`）—— 会连带撤掉已验收的 alpha 优化/变体 A。

---

## 6. 观察到的问题 / 风险 / 不一致

**MINOR-1｜护栏只覆盖"经由 `_land_pixels()` 的物化"。**
现象：直接给 `_land_orig` 赋值（测试注入）不走护栏。
证据：§2.5-2；`tests/test_landfall_view_invariance.py:182`。
影响：有意的（测试注入不该刷告警），但若将来有人在渲染期直接 `mm._land_orig = ...`，护栏看不见。已写入 §2.5。

**MINOR-2｜合法构建路径（掩码失效重建）也会告警，看起来像"误报"。**
现象：`_land_alpha_bytes` 被清空后重跑 `_ensure_land_mask()` ⇒ 222.47 MB 回来 ⇒ 告警。
证据：§2.5-1；`20_fix_map_decode.md` §12.7-② 记录该路径"触发点极少但会发生"。
影响：这是**真阳性**（内存确实回来了），告警里的调用方（`_ensure_land_mask`）足以区分；但若这条路径将来变高频，日志会变成"已知噪音"。缓解：`land_materialize_stats()['count']` 可量化其频率，>1 时说明不止是启动那一次。

**MINOR-3｜`_land_caller_hint()` 在冻结/嵌入式环境下可能取不到帧。**
证据：`:71-82` 已 try/except 兜底返回 `'<unknown>'`；`sys._getframe` 在 CPython 上恒可用，但 PyPy/某些嵌入式实现可能不提供。
影响：仅影响告警文本可读性，不影响判定或行为。

**MINOR-4｜探针第一版的期望写错，产生过一次假 FAIL。**
现象：`count` 在"重复调用 `_land_pixels()`"下停在 1，而探针期望 4。
证据：§3.3；修正为"重复释放+物化"后 PASS。
影响：无（探针已修）；已把该语义写进 `land_materialize_stats()` 的 docstring，避免后人重复踩。

---

## 7. unresolved（未能确证）

1. **真实 GUI 下的告警发生率**：全部验收在 headless（`SDL_VIDEODRIVER=dummy`，2560×1540）完成，未在真实窗口跑一帧。卡点：本轮不做 GUI 长跑（内存 0.7–2.6 GB 波动）。需要时：跑一轮 `python main.py` 进模拟模式，观察是否出现 `land 像素在释放后被再次物化` 告警。
2. **`strict` 模式在完整 GUI 流程下是否零误触**：已证明它不在"启动 + 掩码构建 + 建表 + 6 视角渲染 + 400 点查询"里触发；但 GUI 里还有"窗口 resize → `update_view()`"等路径（`resize()` 会 `_clamp_view_y`、复位缓存），未逐一覆盖。卡点：同 1。
3. **多实例场景**：`land_materialize_stats()` 是**进程级**计数（有意如此以防刷屏）。若同进程先后建多个 `MapManager`，`count` 会累加但只告警一次 —— 无法从 stats 区分"哪个实例"。卡点：生产常态只有 1 个实例（`20_fix_map_decode.md` §4.3），需要时再加 per-instance 计数。
4. **告警级别是否该提升为 `error`**：当前是 `warning`（任务指定）。若将来把"收益被吃掉"视为必须修的问题，可改成 `error` 或直接把 strict 打开进 CI。这是策略选择，留给 Lead。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 护栏开关（warn / strict） | `app/map_mgr.py:45-59` |
| 护栏观测接口 | `app/map_mgr.py:62-68` `land_materialize_stats()` |
| 调用方定位 | `app/map_mgr.py:71-82` `_land_caller_hint()` |
| 护栏触发/告警/断言 | `app/map_mgr.py:85-103` `_note_land_rematerialize()` |
| 护栏判定点（释放后再物化） | `app/map_mgr.py:605-610` |
| 真像素访问器 | `app/map_mgr.py:595` `_land_pixels()` |
| 三态返回（旧契约） | `app/map_mgr.py:627-644` `_load_land_orig()` |
| 释放点 | `app/map_mgr.py:779`（`is_land_at_geo` 建表成功后） |
| 4 处"只判 None"的外部调用方 | `view_state.py:100`、`playback_ctrl.py:295`、`season_stats.py:173`、`_draw_path_mixin.py:1288` |
| 合法构建路径（会真物化） | `map_mgr.py:647` `_rebuild_land_and_overlay()`、`:733` `_ensure_land_mask()`、`:765` `is_land_at_geo()` |
| 测试注入点（保留 `_land_orig` 名字的原因） | `tests/test_landfall_view_invariance.py:182` |
| 护栏验收探针 | `G:/tmp/probe_land_guard.py`（`strict` 参数=严格模式） |
| 等价性探针 | `G:/tmp/cmp_map_render.py` |
| 回退点 | `output/exploration/_backup/state_v2/map_mgr.py`（`f03340ce…`） |
| 变体 A 的设计与验收 | `output/exploration/20_fix_map_decode.md` §11.1 / §12 |
| W5 实测约束原文 | `output/exploration/21_fix_verification.md` §6.2 |
