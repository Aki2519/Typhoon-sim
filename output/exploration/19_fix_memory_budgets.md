# 19 W3 实施：SMCY 图标/登陆帧内存预算（+ purple_frame 交接）

> 第四轮 · `sim-numerics` · **本轮改源码**（只诊断→实施）· 写作用域：`simulator/simcore/render.py`、`app/smcy_icon.py`、本报告。
> 安全网：`output/exploration/_backup/README_ROLLBACK.md`（单文件回退 = `cp output/exploration/_backup/smcy_icon.py app/smcy_icon.py`）。
> **实测** = 本机跑出来的数字（禁用计时，主判据是确定性计数与 RSS）；**推断** = 从代码/算式读出、无实验支撑。

## 一句话定位

上一轮报的"1.9–2.2 GB"不是单一来源，而是**六个互相独立的内存预算之和**（图标流 800 + 登陆流 320 + 摘要流 400 + purple 96 + name 32 + 基线 661 MB）。本轮在 `app/smcy_icon.py` 里**实测并修掉**了其中两条最硬的：① 主图标流的 `_MAX_STREAMS` 20 → **12**（实测 RSS 平台 800 → **623/646 MB**，在册流精确停在 12）；② 新发现并修掉 **`_landfall_cache` 只按条数封顶**——它存的是**整段登陆动画的全部帧**，单条字节数随尺寸平方增长，改前实测单条 2.5 MB(104px) 但可达 **228.9 MB(1000px)**、24 条无字节上界；现在有 **64 MB 硬预算**（任何时刻总字节 ≤ 64 MB，单条超预算直接不缓存）。`simulator/simcore/render.py` 经审计后**未改**（sha256 与改前逐位相同），理由见 §6。

---

## 1. 清单与职责（本轮动手面）

| 对象 | 位置 | 本轮动作 |
|---|---|---|
| `_MAX_STREAMS` | `app/smcy_icon.py:286` | **改** 20 → 12（+ 证据注释） |
| `_landfall_cache` / `_LANDFALL_CACHE_BYTES` / `_frames_bytes` | `app/smcy_icon.py:497-519`、`:529-566` | **改** 新增字节预算 + 单条超预算不缓存 |
| `_SURF_BYTES_PER_STREAM` / `_NATIVE_BYTES_PER_STREAM` | `app/smcy_icon.py:45-46` | **未改**（见 §6 候选-2） |
| `_MAX_LANDED_STREAMS` / `_MAX_SUMMARY_STREAMS` | `app/smcy_icon.py:583` / `:725` | **未改**（见 §6 候选-3/4，缺工作量测量） |
| `simulator/simcore/render.py` | 全文件 | **未改**（只读审计，§6-A） |
| `purple_frame` / `summary_purple` | `app/ty_sim_mixins/_draw_icon_mixin.py:846` / `app/statistics/summary_list.py:35` | **不动**（Lead 裁决转 app-feat，§6 给可套用改法） |

## 2. 数据流 / 调用链（被改的两条路径）

```
[主图标流]  playback_ctrl / _draw_icon_mixin._draw_smcy_frame
   └─ get_smcy_manager().get_frame(cat, hemi, idx, ts)
        └─ SMCYIconManager._open(cat, hemi)  →  _VideoStream(mp4)
              ├─ _streams[key] = stream         ← 受 _MAX_STREAMS 约束(LRU, :416)
              └─ _evict_lru(): release() + del   ← 我的改动让它在第 13 条时才开始动
           每流预算: _SURF_BYTES_PER_STREAM(16MB, 缩放帧面) + _NATIVE_BYTES_PER_STREAM(24MB, 原生帧)

[登陆特效帧表]  playback_ctrl.py:353-358 / _draw_icon_mixin._preload_landfall_effect
   └─ get_landfall_frames(cat, lf_size, lf_size)      ← 我改的函数
        ├─ 解码**整个** mp4 → frames=[Surface,...]（实测 60 帧/个）
        └─ _landfall_cache[(name, w, h)] = frames      ← 改前只有条数上限 24；现在 + 64MB 字节上限
```

`lf_size` 的来源（决定单条字节数）：
`playback_ctrl.py:357` `lf_size = max(20, 4 * round(70 * icon_factor * 1.5 / 4))`，
`icon_factor` 在开启"固定点/图标大小"时按 `_draw_path_mixin.py:182-185` 的
`k = scale / (min_scale * 2.5)` 放大（`map_mgr.py:210` 把 `scale` 夹在 `[min_scale, 8.0]`）
⇒ `lf_size` 可达 ~500–2000 px。**这是"条数上限失效"的根源**。

## 3. 核心机制详解（改了什么、为什么）

### 3.1 修复 8a：`_MAX_STREAMS` 20 → 12

**改前（实测，受控扫描：只 import `app.smcy_icon`、不建 TySim、不渲染）**

| 项 | 值 |
|---|---|
| 每流账面 | **34.1 MB**（20 流合计 682 MB：缩放帧 512 帧 312 MB + 原生帧 580 帧 370 MB） |
| 实测 RSS | 第 1–10 流 +70.6 MB/流；**20 流平台 = 800 MB**（约为账面的 1.2–2×，含解码器/中间帧/每流最多 4 个 cv2 游标） |
| 泄漏 | 无：`release()+gc` 后 RSS **800 → 35 MB** |
| 组合空间 | 18 个类别 × 2 半球 = 36 个组合，`_MAX_STREAMS=20` ⟹ 仍可能驱逐，但实测"207 台风全激活"只用 **10** 条 |

**取值依据（这是本轮唯一的硬约束推理）**：当前数据（206 台风 / 8491 报点）实测**最多 12 个台风同时存活**（2028-09-11），
而一个台风同一时刻只占 **1 个 (类别,半球)** 组合 ⟹ **12 就是真实工作集**。取 12 ⇒ 当前数据**不会触发驱逐**。

**候选值对比（同一场景：依次打开实测观察到的 20 个 (类别,半球) 组合、每条灌 30 帧 @400×400）**

| `_MAX_STREAMS` | 在册流峰值（精确） | 真实驱逐（精确） | RSS 平台（区间） | 判断 |
|---|---|---|---|---|
| 10 | 10 | 10 | 578 MB | 低于实测工作集(10–12)，会常态驱逐 ⇒ 不取 |
| **12（采用）** | **12** | **8** | **623 / 646 MB**（两次） | 等于工作集，上界安全 |
| 16 | 16 | 4 | 887 / 955 MB | 上界仍高，且 16 已超过实测工作集 |
| 20（旧） | 20 | 0 | 800–900 MB | 等于不设界 |

> RSS 有 **±100 MB** 的跨次噪声（同代码两次分别 623/646 与 887/955），所以**判据用"在册流数"和"真实驱逐次数"这两个精确量**，
> RSS 只作区间。`_evict_lru` **调用次数**不等于驱逐次数（它每次 `_open` 都被调一次）——我第一版计数踩过这个坑，已改成按"在册流数下降"计。

**改后（验收，实测）**：在册流**精确停在 12**（第 13 条起驱逐），20 次打开共 **8 次真实驱逐**，RSS 峰值 **646 MB**，
`release()+gc` 后 **61 MB**。⇒ 同场景 **RSS 800 → 646 MB**，且**有硬上界**。

### 3.2 修复 8c（本轮新发现）：`_landfall_cache` 从"只按条数"改成"条数 + 字节"

**改前（实测）**：`_LANDED_MAP`/`_LANDFALL_MAP` 对应 **9 个不同 name**，每个 mp4 **恰好 60 帧**；
104px 时 **2.5 MB/条**（9 条 = 22.3 MB，正常），但 `get_landfall_frames` 只做
`if len(cache) >= 24: pop(最旧)` —— **没有任何字节约束**。而单条字节 = `60 × w × h × 4`：
| lf_size | 单条字节（算式，60 帧） | 24 条上界 |
|---|---|---|
| 104 px（默认） | 2.5 MB | 60 MB |
| 300 px | 21.6 MB | 518 MB |
| 500 px | 57.2 MB | **1.37 GB** |
| 1000 px | 228.9 MB | **5.5 GB** |

`lf_size` 由 §2 的算式给到 500–2000 px 是**可达的**（`scale` 上限 8.0 + `fix_icon_point_size`），
所以这是我在本文件里发现的**比 8a 更无界**的一条。

**改法（`app/smcy_icon.py`）**
```python
_LANDFALL_CACHE_BYTES = 64 * 1024 * 1024     # 新增：帧表总字节上限
_landfall_cache_bytes = 0                    # 与 _landfall_cache 同步维护

def _frames_bytes(frames): ...               # 逐帧 w*h*4 求和（定义在 :513）

# get_landfall_frames 尾部（原：只有 if len>=24 pop 一条）
nbytes = _frames_bytes(frames)
if nbytes > _LANDFALL_CACHE_BYTES:           # 单条超预算 ⇒ 不缓存（仍返回给调用者）
    return frames
while _landfall_cache and (len(_landfall_cache) >= _LANDFALL_CACHE_MAX
                           or _landfall_cache_bytes + nbytes > _LANDFALL_CACHE_BYTES):
    _landfall_cache_bytes -= _frames_bytes(_landfall_cache.pop(next(iter(_landfall_cache))))
_landfall_cache[cache_key] = frames
_landfall_cache_bytes += nbytes
```
- **保留** 条数上限 24（语义不变）；**新增** 字节上限 64 MB；
- 单条超预算**不缓存**——与 `app/cache_store.SurfaceCache.put` 的"单个超预算对象直接不缓存"同一策略，杜绝"一条吃满"；
- 取 64 MB 的依据：默认量级（104px ≈2.5 MB/条，9 个 name 全开 = 22.3 MB）下，即使凑到 24 条（≈60 MB，需要 2–3 个尺寸档）
  也基本不会被裁掉 ⇒ **常用路径行为不变**；它只在尺寸变大、条数上限失去意义时兜底。

**验收（真实 pygame + 真实 mp4，`G:\tmp\landfall_verify.py`）**

| 步骤 | 条数 | 总字节 | 计数器一致 | 在预算内 |
|---|---|---|---|---|
| A 9 个 name @104px（默认路径） | 9 | 22.28 MB | ✓ | ✓ |
| B +300px | 10 | 42.88 MB | ✓ | ✓ |
| B +400px（38.4MB/条） | 4 | 62.17 MB | ✓ | ✓ |
| B +500px（57.2MB/条） | 1 | 57.22 MB | ✓ | ✓ |
| B +700px（112MB/条 > 预算） | 1 | 57.22 MB（**未进缓存**） | ✓ | ✓ |
| C 1000px（228.9MB/条） | 1（未变） | 未变；**仍返回 60 帧给调用者** | ✓ | ✓ |

`全部断言通过 = True`。另用**桩版功能测试**（headless Blender 的 Python3.13 + 假 pygame/cv2，`_frames_bytes` 与逐条重算在 6 个尺寸步上都一致）交叉验证了同一套不变量。

### 3.3 一处口径更正（按 Lead 要求写清）

上一轮 14 号报告写的"**可达 1.9–2.2 GB**"应理解为**六个独立预算之和**，不是单一来源：

| 来源 | 名义上界 | 实测/状态 |
|---|---|---|
| 主图标流 `mgr._streams` | 20×40 = 800 MB | **实测 RSS 800 MB**（改后 12×40 = 480 MB 名义 / 实测平台 646 MB） |
| 登陆流 `_landed_streams`（`_MAX_LANDED_STREAMS=8`） | 7×40 = **280 MB**（`_LANDED_MAP` 只有 **7 个不同 name**，而该字典按 **name** 键 ⇒ cap 8 **永不触发**，名义值 320 MB 反而高估） | 未测量工作集 ⇒ 本轮未改 |
| 摘要流 `_summary_streams`（`_MAX_SUMMARY_STREAMS=10`） | 10×40 = 400 MB | 未测量工作集 ⇒ 本轮未改 |
| `_landfall_cache` | 改前**无字节上界**（500px 时 24 条 = 1.37 GB） | 改后**硬上限 64 MB**（实测） |
| `purple_frame` | 96 MB | 实测峰值 5 MB / 13 条（0 命中）⇒ 转 app-feat |
| `name_text` | 32 MB | 实测 15.3 MB / 256 条（顶点） |
| 基线（map+数据+pygame，1600×900） | — | **实测 661 MB** |

⇒ "流本身封顶 ~800 MB"这个更正在 8a 之后变成了"**主图标流实测平台 646 MB，且现在有 12 条的硬数**"。

## 4. 输入输出资产

| 资产 | 规格（实测） | 消费者 |
|---|---|---|
| `assets/SMCY/mainicon/**.mp4` | 源尺寸 400×400（EX 为 400×600），1800 帧/条 | `_VideoStream`（主图标流） |
| `assets/SMCY/landfall/*.mp4` | **60 帧/条**，9 个不同 name | `get_landfall_frames`（**本轮改的缓存**） |
| `assets/SMCY/landfall/Landed-*.mp4` | 流式，一次性播放 | `_landed_streams`（未改） |
| `typhoon/**` | 206 台风 / 8491 报点；**最大同时存活 12** | 决定 `_MAX_STREAMS=12` |

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
# 改前/改后 SHA256
sha256sum app/smcy_icon.py simulator/simcore/render.py

# 修复 8a 验收：流数平台 + 驱逐次数 + RSS（受控扫描，不建 TySim）
SMCY_LIMIT=1400 /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\smcy_verify.py' | iconv -f GBK -t UTF-8
#   候选值 A/B（改的是内存里的模块常量，不动源文件）
... 'G:\tmp\smcy_verify.py' 10 | iconv -f GBK -t UTF-8
... 'G:\tmp\smcy_verify.py' 16 | iconv -f GBK -t UTF-8

# 修复 8c 验收：帧表字节预算（真实 pygame + 真实 mp4）
SMCY_LIMIT=1500 /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\landfall_verify.py' | iconv -f GBK -t UTF-8

# 回归（单文件 timeout 300；禁全量）
python.exe -m pytest tests/test_fx_sound.py    -q --no-header   # 27 passed
python.exe -m pytest tests/test_render_layers.py -q --no-header # 9 passed
python.exe -m pytest tests/test_simcore_ocean.py -q --no-header # 10 passed
python.exe -m pytest tests/test_sim_mode_ref.py  -q --no-header # 见 §6-E（需 ≥420s）
```
**探针脚本**（工作区外，未写仓库）：`G:\tmp\smcy_mem.py`（改前扫描）、`smcy_verify.py`（流平台/驱逐/候选 A-B）、
`landfall_mem.py`（改前帧表实测）、`landfall_verify.py`（改后字节预算验收）、
桩版功能测试（headless Blender 内联，`G:\tmp\` 无落盘）。

**确定性判据**：在册流数、真实驱逐次数、`_landfall_cache_bytes` 与逐条重算的一致性、总字节是否 ≤ 预算、
缓存 hits/misses/evictions。**计时仅作区间**（本机前两轮比稳态慢数倍；RSS 也有 ±100 MB 跨次噪声）。

## 6. 观察到的问题 / 风险 / 不一致

### A. `simulator/simcore/render.py`：审计后**未改**（有证据说明为什么不改）
- **sha256 与改前逐位相同**：`8a6f27bf4c42f6ce67df82e3c78b04e97dd676d664d799cf4d0486ed6d7dfc87` ⇒ 我对它的改动为零，内核回归因此不被触发。
- 审计结论：它的 9 套缓存**全部只有条数上限、没有字节预算**，按用户窗口 2560×1498 估算最坏：
  `_layer_surf_cache` 12 条 × ≤14.3 MB ≈ **172 MB**、`_cloud_surf_cache` 6 条 × ≤14.3 MB ≈ **86 MB**、
  `_env_arr_cache` 8 条 × 6.2 MB ≈ **50 MB**，其余（`_contour_cache` 4 / `_shear_cache` 2 / `_label_cache` 64 /
  `_cld_grids_cache` 4×0.7MB）合计 <5 MB ⇒ **约 311 MB 上界**。
- **为什么不改**：这是"最坏上界估算"，不是**实测占用**——两个大项只在**模拟模式**（`_sim_render`）填充，
  而回放模式（用户日常）根本不进那两条路径。按本轮纪律"有证据才动、没证据明确写不改"，我**不动它**。
- 要动它需要的测量（留给后续，一次就能定）：在模拟模式下逐层开 sst/ohc/rh/shear/pressure/flow/current/wind，
  每帧记录 `sum(surface_bytes(v) for v in _layer_surf_cache.values())` 与 `_cloud_surf_cache` / `_env_arr_cache`，
  与 `len(cache)` 一起打出来；若实测峰值 >100 MB 才值得加字节预算（`app/cache_store` 不能直接引入 simcore，
  要保持"simcore 渲染不依赖 app 层"，需在 render.py 内加一个 20 行的字节计数）。

### B. 修复 6（`purple_frame`）——**转 app-feat**，以下是可直接套用的改法与验收口径
- **位置**：`app/ty_sim_mixins/_draw_icon_mixin.py:846`
  `_purple_frame_cache = SurfaceCache("purple_frame", 96 * 1024 * 1024, 240)`；
  取用/写入在 `:870-891`（`_draw_smcy_frame`），清空在 `:367`。
- **证据（我上一轮的确定性计数，207 台风全激活、逐帧 `reset_stats`）**：`hits=0 / misses=1.0 每帧 / evictions=0`，
  帧内不同键 **10**，items **13**，占用 **5.0 / 96 MB**。键 = `(cat, hemi, frame_idx, ts, tier)`（`:871` / `:883`），
  **含 `frame_idx`**，而动画每帧推进 ⇒ "写一次、用一次"是**设计使然**，不是 bug：
  只有**同一帧内另一个台风请求同一 (类别,半球,帧号,尺寸)** 时才会命中。
- **结论**：**不是"完全没用"，是"只在多台风同类别同帧时命中"**。所以**不要删**，**保守调小**。
- **建议改法**：96 MB → **24 MB**（实测条目 ≈400 KB ⇒ 60 条容量；实测峰值 13 条/5 MB 有余量）；
  `max_items` 240 可同时降到 64（与字节预算同量纲）。
- **验收口径**：`reset_stats()` 后跑 30 帧**含 ≥2 个 C5(≥155kt) 且同类别**的场景，读该实例：
  ① `bytes ≤ max_bytes`；② `evictions == 0`；③ 若两个同类 C5 同屏，`hits > 0`（否则说明 key 里的 `ts`/`frame_idx` 永远不同，可进一步只按 `(cat,hemi,tier)` 缓存"滤镜后的帧"而不是"某帧某尺寸"）。
- **另一个（不同实例，别混淆）**：`app/statistics/summary_list.py:35`
  `_purple_frame_cache = SurfaceCache("summary_purple", 48 * 1024 * 1024, 96)`，取用 `:430-433`。
  我两轮的探针都**没触发**过这条路径（`hits=0 / misses=0 / items=0`）⇒ 证据是"**未被触发**"而不是"没用"。
  **建议**：48 MB → **16 MB**；验收口径 = 打开"摘要列表/多年度图表"（≥10 个台风）后再读 `stats()`，
  要求 `bytes ≤ max_bytes` 且 `evictions` 不随帧数增长。

### C. 未改但已量化的另外两条流注册表（建议后续处理）
| 位置 | 现值 | 名义上界 | 风险 | 需要什么才能定值 |
|---|---|---|---|---|
| `_MAX_LANDED_STREAMS`（`app/smcy_icon.py:583`） | 8 | **7×40 = 280 MB**（不是 320：`_landed_streams` 按 **name** 键，`_LANDED_MAP` 只有 7 个不同 name ⇒ **cap 8 永不触发**，等于"无约束但被文件名自然封顶"） | 低-中 | 一次"多台风同时登陆"场景统计 `len(_landed_streams)` 峰值（理论上 ≤7） |
| `_MAX_SUMMARY_STREAMS`（`app/smcy_icon.py:725`） | 10 | 10×40 = **400 MB** | 中 | 打开摘要列表时的 `len(_summary_streams)` 峰值 |

两处都是"每条流 40 MB × 条数"的同一结构，**分子（条数）没有工作集测量**，所以本轮不动——
按 Lead 的纪律"先算清实际需要几个流再定值"。

### D. 未改的每流字节预算（候选-2）
`_SURF_BYTES_PER_STREAM=16MB` + `_NATIVE_BYTES_PER_STREAM=24MB` = 40 MB/流。
降 `_NATIVE` 24→20 MB 可再省 4 MB/流（12 流 ≈ 48 MB 账面），但**实测收益落在 RSS 的 ±100 MB 噪声里**，
而代价是真实的：源尺寸 400×400 时原生帧容量 39 → **32 帧**（EX 400×600 时 26 → **21 帧**），
而"原生帧服务任意尺寸、换档不重新 seek"正是 `_native_cap` 注释里写的设计意图。
⇒ **本轮不做**（收益不可测、成本可测），列为候选。

### E. 回归测试结果（含一条未完成）
| 测试 | 结果 |
|---|---|
| `tests/test_fx_sound.py`（本仓唯一提到 SMCY 的测试） | **27 passed** |
| `tests/test_render_layers.py` | **9 passed** |
| `tests/test_simcore_ocean.py` | **10 passed** |
| `tests/test_sim_mode_ref.py` | **未完成**：`timeout 300` 下返回 exit 1 且无 summary；既有基线是 **26 passed / 197.66 s**（`06_evidence.md:377`），本机争用下 300 s 不够。改用 `timeout 600` 重试时**WSL bash 再次整体不可用**（`E_UNEXPECTED`），故本轮始终没拿到它的 summary。**该文件只 import `simulator.simcore.core/render` 与 numpy/pygame**（grep 全部 import，无 `app.smcy_icon`/`app.ty_sim_mixins`）⇒ **本次改动不可能影响它**。建议用 **≥420 s** 重跑 |

### F. 工具链事故（影响可复现性，必须记录）
本轮中途 **WSL bash 服务两次不可用**（`E_UNEXPECTED`，连 `echo hi` 都失败），
期间我改用 **headless Blender 的 Python 3.13** 完成 `ast.parse` 语法校验与 sha256 计算，
并用**桩版 pygame/cv2** 跑通了 `get_landfall_frames` 的功能验证。bash 恢复后补跑了真实 pygame 验收与上表测试。
⇒ 报告里凡标"实测"的真实 pygame 数据，都是在 bash 可用的窗口内跑出来的；桩版验证另行标注。

### G. 值得保留的现状（本轮确认有效，别回退）
1. `_VideoStream` 的**三级缓存**（缩放帧 / 原生帧 / 流 LRU）——结构正确，本轮只调了"流条数"这一个旋钮；
2. `release()` 真的归还内存（改前实测 800 → 35 MB，改后 646 → 61 MB）⇒ 没有泄漏，LRU 驱逐是安全的；
3. `_landfall_cache` 原来就有的**按尺寸键缓存**（避免每帧重解码 60 帧）——本轮保留了它，只加了字节闸门；
4. `app/cache_store.SurfaceCache` 的"单对象超预算不缓存"策略（我把同一策略移植到了帧表缓存）。

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U-1 | `test_sim_mode_ref.py` 是否真的全绿 | 一次 **≥420 s** 的单文件跑（本机争用下 300 s 不足；既有基线 26 passed/197.66 s）。该文件不 import 我这轮改的任何模块 |
| U-2 | `_MAX_LANDED_STREAMS=8` / `_MAX_SUMMARY_STREAMS=10` 的真实工作集 | ① 一个"多台风同时登陆"的场景统计 `len(_landed_streams)` 峰值；② 打开摘要列表后统计 `len(_summary_streams)` 峰值 |
| U-3 | `simcore/render.py` 9 套缓存的**实测**占用（我只有上界估算 ~311 MB@2560×1498） | 模拟模式逐层开 8 个图层，边跑边读各缓存的 `surface_bytes` 合计（§6-A 给了度量方法） |
| U-4 | `purple_frame` 在"两个同类 C5 同屏"时到底会不会命中 | 由 app-feat 在改后按 §6-B 的验收口径跑一次（`hits > 0` 才算这条缓存有效） |
| U-5 | `_MAX_STREAMS=12` 在**长会话**（把 36 个组合都摸一遍）下的驱逐频率 | 一次整季回放（含南北半球）统计 `_evict_lru` 的实际驱逐次数；若频繁，说明该抬到 16 并接受更高上界 |

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 本轮改动 1：主图标流上限 | `app/smcy_icon.py:286`（`_MAX_STREAMS = 12` + 证据注释） |
| 本轮改动 2：帧表字节预算 | `app/smcy_icon.py:497-519`（常量 + `_frames_bytes`）、`:529-566`（`get_landfall_frames` 尾部，预算判断在 `:554-566`） |
| 流 LRU 驱逐 | `app/smcy_icon.py:415`（`_evict_lru`）、`:478`（`_open` 里调用） |
| 每流字节预算 | `app/smcy_icon.py:45-46`、`:115-131`（`_cache_cap`/`_native_cap`） |
| 登陆流 / 摘要流（未改） | `app/smcy_icon.py:583` / `:725` |
| `lf_size` 的来源（解释尺寸为何会变大） | `app/playback_ctrl.py:357`、`app/ty_sim_mixins/_draw_path_mixin.py:175-188`、`app/map_mgr.py:210` |
| 转给 app-feat 的两条 purple 缓存 | `app/ty_sim_mixins/_draw_icon_mixin.py:846`（+`:870-891`）、`app/statistics/summary_list.py:35`（+`:430-433`） |
| 改前/改后哈希 | `render.py` = `8a6f27bf…`（未变）；`smcy_icon.py` = `3e635fd0…` → `c2c07f27a04bb9f127ac98fc0011aafc999434c8f650b50746c06d0f9c7770c0` |
| 回退 | `cp output/exploration/_backup/smcy_icon.py app/smcy_icon.py` |
| 探针 | `G:\tmp\smcy_mem.py`、`smcy_verify.py`、`landfall_mem.py`、`landfall_verify.py` |
