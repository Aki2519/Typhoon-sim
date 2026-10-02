# 14 渲染性能诊断（全程序：回放模式帧成本 + 缓存效率 + 内存）

> 第三轮 · `sim-numerics` · 只诊断不改代码（perf-optimize 技能模式 E）· 写作用域仅本文件。
> 前序：`09_numerics_render.md`（simcore/render.py 44 个定义 + 12 套缓存）。本轮做**应用层**。
> **证据分级**：【实测·确定性】= 调用次数/缓存计数，与时间无关；【实测·计时】= 绑核后的区间；
> 【推断】= 从代码读出、没有实验支撑。

## 一句话定位

用户日常的**正常回放模式**在 1600×900、绑 P 核、30 帧预热后是 **p50 11.5 ms/帧**（≈87 FPS），
**没有危机**；真正吃掉这半帧的是 **SMCY 图标视频解码（约 3.5 次 `cv2.read` + 2.8 次 `grab`/帧）**，
不是路径绘制（路径整层只有 27 次 blit/帧、1 次 `draw_typhoon`）。风季模式的**真实峰值**（数据实测
最多 12 个台风同时存活）是 **p50 30.3 ms/帧**、文本缓存 **100% 命中**；而"207 个全激活"这个
**当前数据到不了的**合成场景会掉进一个**确定的缓存抖动陷阱**（文本表面缓存条目上限 256 < 帧内
工作集 708 ⇒ 每帧 768 次驱逐 + 768 次重建 + 54,806 次 blit，`_sized_glow_text` 占 64.7% 累计时间）。
另有一条**结构性内存风险**：SMCY 视频流按"每流 40 MB × 最多 20 流"预算，实测 10 流时
图标缓存 389 MB、进程 RSS **2638 MB**，而本机可用内存只有 1–3 GiB。

---

## 1. 清单与职责（本轮测量面）

| 模块 | 行数 | 职责 | 本轮 |
|---|---|---|---|
| `app/renderer.py` | 437 | `draw()` 总入口 + **12 层显式图层表**（`:202-298`）+ 裁剪几何 | 主测量对象 |
| `app/ty_sim_mixins/_draw_path_mixin.py` | 1462 | 路径：`_draw_typhoons:472`、`should_draw_typhoon:245`、`_is_typhoon_visible:272`、`draw_typhoon:993`、`_render_path_to_surface:570` + 路径缓存 | 路径侧计数 |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | 1738 | 图标/名字/巅峰标注：`text_core_rect:108`、`_ring_offsets:146`、`_sized_glow_text:162`、`draw_typhoon_info:450`、`_get_peak_label_surf:1043`、`_get_name_surf:1069`、`_label_layout:1098`、`_draw_smcy_frame:853` | **热点所在** |
| `app/smcy_icon.py` | ~800 | SMCY 视频流（cv2）解码 + 三级缓存（缩放帧/原生帧/LRU 流） | 第二热点 + 内存 |
| `app/map_mgr.py` | 642 | `MapView`（`_pan_cache:279`、`_window_cache:300`、`draw:387`）+ `MapManager.draw_map:615` | 底图缓存 |
| `app/cache_store.py` | 118 | 字节预算 LRU + hits/misses/evictions | **确定性指标来源** |
| `app/ty_sim_mixins/draw_info_boxes_mixin.py` | — | 信息框/季节时钟/进度条 | 计数 |
| `app/control_panel.py` | — | 控制面板（17 次 `draw.rect`/帧） | 计数 |

---

## 2. 数据流 / 调用链（回放模式）

```
main.py:207-214   sim.update(dt) → sim.draw(screen) → pygame.display.flip()
  └─ TySim.draw → Renderer.draw:65
       ├─ _draw_scene:306  ← 12 层显式图层表(scene_layers:202-298)
       │    map        → sim._draw_map      → MapManager.draw_map:615 → MapView.draw:387
       │    graticule  → Renderer._draw_graticule:168
       │    sim_field  → sim._sim_render（模拟模式专用，本轮不测）
       │    ocean_areas/ocean_edit/clock/ace_bar
       │    typhoons   → sim._draw_typhoons:472  ← 路径 + 路径缓存
       │    track_label/season_info_boxes
       │    typhoon_info → sim.draw_typhoon_info:450  ← 图标 + 名字 + 巅峰标注
       │    effects    → 登陆特效/粒子
       └─ draw_control_panel:17×draw.rect/帧
       └─ dialog_mgr.draw / FPS / toasts / coord_hud / legend / script_status
```

关键**不对称**（【实测·确定性】，是下面 MINOR-1 的根因）：

| 层 | 遍历 | 可见性裁剪 |
|---|---|---|
| `typhoons`（路径） | `for ty in self.tys` + `should_draw_typhoon` | **有**（`_is_typhoon_visible`） |
| `typhoon_info`（图标+文字） | `for ty in sim.tys`（`renderer.py:246-248` 的 `_typhoon_info`） | **没有** |

---

## 3. 核心机制与测量结果

### 3.1 确定性计数（每帧）【实测·确定性】

三种场景，同一进程、同一数据（207 文件 → **206 台风 / 8491 报点**）、1600×900：

| 指标/帧 | NORMAL 稳态 | NORMAL 平移8px | NORMAL 缩放2% | SEASON 207 全激活 | SEASON 真实峰值(12) |
|---|---|---|---|---|---|
| `should_draw_typhoon` | **207** | 207 | 207 | 207 | 207 |
| `draw_typhoon`（路径） | 1 | 1 | 1 | **87** | 12* |
| `draw_typhoon_info`（图标+文字） | 1 | 1 | 1 | **207** | 12 |
| `probe` 路径报点数 | 21 | 21 | 21 | **4043** | ~700* |
| `pygame.draw.line` | 1 | 1 | 1 | 1 | 1 |
| `pygame.draw.lines` | 1 | 1 | 1 | **86** | — |
| `pygame.draw.rect` | 17 | 17 | 17 | 16 | 16 |
| `Surface.blit`（cProfile） | **27** | — | — | **54 806** | — |
| `_sized_glow_text` 调用 | 0（全命中） | — | — | **768** | 0（全命中） |
| `_ring_offsets` 调用 | 0 | — | — | **3 072** | — |
| `math.cos`/`math.sin` | —（未测） | — | — | **57 968 / 57 960** | — |
| `builtins.round` | — | — | — | **127 757** | — |
| `Font.render` | 2 | — | — | 1 400 | — |
| `cv2.VideoCapture.read` | **3.5** | — | — | **90** | — |
| `cv2.VideoCapture.grab` | 2.8 | — | — | 112 | — |
| `get_bounding_rect` | 0 | — | — | **708** | — |
| `_draw_offset`（`map_mgr:262`） | 499 | — | — | 499 | — |

\* 真实峰值 + 计数没跑全量（只跑了计时与缓存），标 `—` 的格是本轮没测。

**地图底图**：`map_mgr.draw_map` 每帧 **1 次 blit**，平移 8px/帧时计数**完全不变**
（`_win_cache_key` 稳定）⇒ 第 3 轮之前做的"路径缓存改地图空间/放大态窗口缓存"是有效的，
**平移不再重建**（【实测·确定性】，见 §6「值得保留的现状」）。

### 3.2 计时区间（绑核 P8 + 30 帧预热）【实测·计时】

按 Lead 的绑核实验（本机 i7-13700HX，P 核逻辑 8，`SetProcessAffinityMask(1<<8)` 返回 1）：

| 场景 | min | p50 | max | n |
|---|---|---|---|---|
| MODE_NORMAL 稳态 | **7.3** | **11.5** | 1759.4 | 30 |
| MODE_NORMAL 每帧平移 8px | 11.2 | 14.1 | 51.5 | 30 |
| MODE_NORMAL 每帧缩放 2% | 11.0 | 19.0 | 1788.2 | 30 |
| MODE_SEASON **真实峰值（12 存活）** | **21.3** | **30.3** | 123.8 | 30 |
| MODE_SEASON 207 全激活（**合成**） | 1049.6 | 1274.0 | 2003.2 | 30 |

单位 ms/帧。**max 不可信**（两个 1.7-1.8 s 的离群值我无法归因：绑核是"偏好"不是独占，core 8 上仍可能
被其它进程抢占；也可能是视频解码首次 seek 的抖动）。**跨时间也不可比**——按 Lead 的结论只作区间。

### 3.3 缓存效率【实测·确定性】

`app/cache_store.py` 自带计数。**逐帧重置后统计**（`reset_stats()` 后跑 10 帧）：

| 场景 | 缓存 | hits/帧 | misses/帧 | evictions/帧 | 命中率 | 帧内不同键 | items | cap_items | 已用字节 |
|---|---|---|---|---|---|---|---|---|---|
| NORMAL | `name_text` | 4.0 | 0.0 | 0.0 | **100%** | 3 | 3 | 256 | 0.2 / 32 MB |
| NORMAL | `purple_frame` | 0 | 0 | 0 | — | 0 | 0 | 240 | 0 / 96 MB |
| SEASON 207 | `name_text` | 803.0 | **768.0** | **768.0** | **51.1%** | **708** | **256(满)** | 256 | **15.3 / 32 MB** |
| SEASON 207 | `purple_frame` | 0 | 1.0 | 0 | 0% | 10 | 13 | 240 | 5.0 / 96 MB |
| SEASON 真实峰值 | `name_text` | 122.0 | 0.0 | 0.0 | **100%** | 81 | 81 | 256 | 4.3 / 32 MB |
| SEASON 真实峰值 | `purple_frame` | 0 | 0 | 0 | — | 0 | 0 | 240 | 0 / 96 MB |

**核心事实（确定性，与时间无关）**：
1. `name_text`（`_draw_icon_mixin.py:352`，32 MB / **256 条**）在 207 激活时**帧内不同键 = 708 > 256** ⇒
   每帧必然驱逐 **768** 条、重建 **768** 张文字表面，命中率被压到 **51.1%**；
2. **卡的是条目上限、不是字节预算**：15.3 MB / 32 MB，字节预算只用掉 48%；
3. 只在真实规模（12 存活、81 键）下，它是 **100% 命中、0 驱逐** ——
   ⇒ 这是**缩放悬崖（scaling cliff）**，不是日常缺陷；
4. `purple_frame`（`:846`，96 MB / 240 条）实测 **0 命中**：`_draw_smcy_frame:868-879` 的键含
   `frame_idx`（动画逐帧推进），每帧 1 个新键 ⇒ **设计上就是"写一次用一次"**，
   96 MB 预算只用 5%，不是缺陷（但也没必要给它 96 MB）；
5. `Typhoon._img_cache`（`app/typhoon.py:103`，12 MB / 720 条）在 207 个实例上
   **hits=0 misses=0 items=0** —— 它只被 `app/typhoon_render.py:137` 使用，而当前
   `config.json: icon_set = smcy` ⇒ 走不到那条路。**不是 bug，但 207 个空缓存实例 + 12 MB 名义预算容易误读**。

### 3.4 blit / 热点归属（cProfile callers）【实测·确定性】

**MODE_SEASON 207 全激活，6 帧，总调用 3 031 104（505 184/帧）**：

| 调用者 | blit 次数(6帧) | 占比 | tottime(6帧) |
|---|---|---|---|
| **`_draw_icon_mixin.py:162(_sized_glow_text)`** | **326 304** | **99.2%** | **3.134 s / 7.205 s = 43.5%** |
| `_draw_smcy_frame:853`（图标本体） | 1 236 | 0.4% | 0.049 s |
| `draw_typhoon:993`（路径） | 1 020 | 0.3% | 0.133 s |
| `map_mgr.py:615(draw_map)` | 6 | 0.002% | 0.003 s |
| 控制面板/时钟/信息框 | 216 | 0.07% | ~0 |

`_sized_glow_text` 累计时间 **4.663 s / 7.205 s = 64.7%**；ncalls **4 608/6 = 768/帧**
（与 `name_text` 的 768 misses/帧**逐一对上**）。每次调用 **326 304/4 608 ≈ 70.8 次 blit**：
`_sized_glow_text:181-188` 对 (3,26)/(2,46)/(1,96) 三层辉光各 copy 一次背景，
再按 `_ring_offsets(round(radius*scale))` 铺一圈（半径 r 的环有 `max(8, round(2πr))` 个点，
scale>1 时逐个膨胀）；再加上 `_ring_offsets(round(scale))` 的 8 向描边与 1 次前景。

**MODE_NORMAL（6 帧，总调用 36 514 = 6 086/帧）热点完全不同**：
`_draw_typhoons` tottime 0.014 s、**`cv2.VideoCapture.read` 0.014 s + `grab` 0.006 s +
`merge` 0.005 + `resize` 0.005 + `tobytes` 0.003 + `split` 0.003 ≈ 0.036 s / 6 帧 ≈ 6 ms/帧**，
而 `draw_typhoon_info` 累计 0.041 s / 6 帧 ≈ 6.8 ms/帧；帧总量 0.083 s/6 = 13.8 ms（cProfile 口径）。
⇒ **正常模式下每帧有 3.5 次 `read` + 2.8 次 `grab`、而屏幕上只有 1 个图标**：
`_draw_icon_mixin.py:456` 的 `_preload_upcoming_icon` 与 `:840` 的
`mgr.preload_window_step(..., budget=3, ...)` 每帧**预算 3 次解码**做预载。
【推断】这是"首次切换类别不卡顿"的刻意取舍，但在稳态下它把每帧视频解码量放大了 ~4 倍。

### 3.5 内存归属【实测·确定性】

三阶段 RSS（同一进程，1600×900）：

| 阶段 | RSS | 主图标流数 | 缩放帧面 | 原生帧 | `name_text` | `purple_frame` |
|---|---|---|---|---|---|---|
| 构建完 | **661 MB** | 0 | 0 | 0 | 0.2 MB | 0 |
| NORMAL 10 帧 | 771 MB | 1 | 10 MB | 16 MB | 0.2 MB | 0 |
| SEASON 真实峰值(12) 12 帧 | 1029 MB | 2 | 32 MB | 48 MB | 4.5 MB | 0 |
| SEASON 207 全激活 12 帧 | **2638 MB** | **10** | **158 MB** | **231 MB** | 15.3 MB(满) | 4.6 MB |

实测**每流 ≈ 39 MB**（15.8 缩放 + 23.1 原生），与 `app/smcy_icon.py:45-46` 的
`_SURF_BYTES_PER_STREAM = 16 MB` + `_NATIVE_BYTES_PER_STREAM = 24 MB` **逐条对上**。
而 `app/smcy_icon.py:271` 是 `_MAX_STREAMS = 20`、`:529` 是 `_MAX_LANDED_STREAMS = 8`
⇒ 【推断+实测外推】**图标流最坏 20×40 = 800 MB**（`smcy_icon.py:44` 的注释自己写"现在 ~820 MB"）、
登陆流再 +8×40 = 320 MB，加 `purple_frame` 96 MB + `name_text` 32 MB + 基线 661 MB
⇒ **可达 1.9–2.2 GB**，而本机可用内存 **1–3 GiB（Lead 实测 1.6 GiB）**。
实测已出现 **2638 MB**。这是**结构性 OOM 风险**（见 §6 严重-1）。

### 3.6 每帧重复计算清单【实测·确定性】

| 重复计算 | 位置 | 每帧次数 | 备注 |
|---|---|---|---|
| `_ring_offsets(r)` 纯函数重算 | `_draw_icon_mixin.py:146` | **3 072** | 只依赖整数半径；3 072 次共 57 968 cos + 57 960 sin + 127 757 round（`round` 总数 1 022 056/8 帧里的大头） |
| 文字表面重建（缓存抖动） | `:162` + `:352` | **768** | 帧内 708 个不同键 > 256 条上限 |
| `get_bounding_rect` | `:118`（`text_core_rect`） | **708** | `_text_core_cache` 上限 512 条（`:123`），同样在抖 |
| `should_draw_typhoon` × 全表 | `_draw_path_mixin.py:245` | **207** | NORMAL 下 206 次必然返回 False（只有当前台风能过） |
| `_typhoon_info` 全表 + 无可见性裁剪 | `renderer.py:246-248` | **207 循环 / 207 次 draw_typhoon_info**（207 激活时） | 路径层裁了、信息层没裁 |
| `_label_layout` 对**全部候选**求 core | `:1098-1145` | 每台风 ~4 次 `_get_*_surf` | 设计如此（固定槽位），但与缓存上限叠加就是抖动 |
| 视频预载预算 3/帧 | `:840` | 3 次解码 | 稳态下也在预载 |
| `_draw_offset` | `map_mgr.py:262` | 499 | 每格点调用一次（`_advect`… 实际是风羽/等值线网格），有缓存判断但仍是 499 次 Python 调用 |

---

## 4. 输入输出资产

| 资产 | 体量/规格 | 消费者 |
|---|---|---|
| `typhoon/**`（207 文件） | **206 台风 / 8491 报点 / 平均 41 点/台风** | `DataRepository.load_typhoon_files` |
| `assets/SMCY/**` | **84 个 mp4**（mainicon / landfall）+ 粒子/资源/音效 | `smcy_icon._VideoStream` |
| `map/*.png` | 底图 10800×5400（`MapView.original_img` = **233 MB 常驻**） | `MapView` |
| `config.json` | 113 键；`icon_set=smcy`、`screen_width/height=None`（用实际窗口） | `AppConfig` |
| `app/cache_store.SurfaceCache` 预算合计 | 48+12+32+96 = **188 MB**（实测峰值用 20 MB） | 4 个实例 |

---

## 5. 测法、机器状态与本机允许的结论强度

- **探针**：`G:\tmp\perf_render.py`（图元/方法计数）、`perf_render2.py`（绑核+预热+计时+逐帧缓存键+blit 归属）、
  `perf_render3.py`（内存归属）。全部 headless（`SDL_VIDEODRIVER=dummy`），**不启动 `main.py`**，
  **不写仓库任何文件**（`CONFIG_FILE` 被指到临时目录，见 `tests/conftest.py` 的做法）。
  复现：
  ```
  cd /mnt/g/气象/Typhoon-sim
  /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf_render2.py' timing | iconv -f GBK -t UTF-8
  ... cache / real / blit / keys
  /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf_render3.py' | iconv -f GBK -t UTF-8
  ```
- **绑核**：按 Lead 的作业，探针开头 `SetProcessAffinityMask(GetCurrentProcess(), 1<<8)`（返回 1），
  计时前 **30 帧预热**（Lead 实测前 2 轮慢数倍）。
- **我能下的结论强度**：
  - 【强】一切**计数类**结论（blit 数、调用次数、缓存 hits/misses/evictions、帧内不同键数、
    每流字节）：与时间无关、可复现、噪声为零。本报告的主干全部是这一类。
  - 【中】**p50/min 计时**：绑核 + 30 帧预热后可用于**同进程内、同一场景之间**的比较；
    我**不**给跨时间或跨配置的确定加速比。
  - 【弱/不可用】**max 与单次测量**：本机即使是 p50 也在不同轮次间变化；两个 1.7–1.8 s 的
    max 我无法归因，已标注为不可信。
  - **未覆盖**：用户真实窗口 2560×1498（我只跑 1600×900，blit 目标面积差 2.7×，会等比更贵）；
    对话框/统计/绘画/编辑模式/粒子/登陆特效（归 15 分区）；启动（归 13 分区）；
    真实交互（鼠标拖动 >120px/帧会关掉窗口缓存，我没测那条分支 —— 代码里 `map_mgr.py:311-319`
    自己记了实测 600px/帧 30.6 ms vs 11.3 ms）。

---

## 6. 观察到的问题 / 风险 / 不一致

> **分级说明**：本轮渲染侧**没有"阻塞"级**问题 —— 日常回放模式 p50 11.5 ms/帧、真实风季峰值
> p50 30.3 ms/帧，都能用。下面 严重-1/2 是"会在数据规模或场景变化时咬人"的结构性风险，
> 一般/建议是确定性的白做工。每条给：位置 / 证据 / 根因 / 修法 / 预期收益 / 成本 / 风险。

### 严重-1｜渲染层内存预算总和超过本机可用内存（**结构性 OOM 风险**）
- **位置**：`app/smcy_icon.py:45-46`（16+24 MB/流）、`:271`（`_MAX_STREAMS=20`）、`:529`（登陆流 8）、
  `_draw_icon_mixin.py:352`（32 MB）、`:846`（96 MB）。
- **证据**：【实测】10 个流时图标缓存 389 MB、RSS **2638 MB**；每流 39 MB 与声明的 40 MB 上限吻合；
  基线（地图+数据+pygame）已 **661 MB**。
- **根因**：乘性预算（每流 40 MB × 20 流 = 800 MB）是按"内存充裕"设的；它和"可用只剩 1.6 GiB、
  有 OOM 前科"的环境不匹配。`_MAX_STREAMS=20` 的注释理由是"过小会导致 LRU 反复驱逐重开视频"——
  这是**用内存换解码抖动**的取舍，但代价没有上限保护。
- **建议**：给**全局**图标内存设总预算（而不是每流预算），超限时按"流 LRU"释放；
  或按屏幕上的图标尺寸自适应 `_NATIVE_BYTES_PER_STREAM`（原生帧只在换挡时有用，
  实测 `_scale_size` 恒定在一个值，原生帧 231 MB 里很大一部分是"提前量"）。
- **预期收益**：把最坏内存从 ~2 GB 量级压到基线 + 固定预算内（例如 -600~-800 MB）；**成本中**（要动缓存策略）；
  **风险中**（降预算会让换挡 seek 变多，可能引入卡顿——必须配 A/B）。

### 严重-2｜文本表面缓存条目上限（256）小于帧内工作集（708）⇒ 每帧 768 次重建
- **位置**：`_draw_icon_mixin.py:352`（`SurfaceCache("name_text", 32MB, 256)`）；
  消费点 `:1043-1052`（`_get_peak_label_surf`）、`:1069-1082`（`_get_name_surf`）、`:1098-1145`（`_label_layout`）。
- **证据**：【实测·确定性】207 激活时 `name_text` 每帧 **misses=768 / evictions=768 / 帧内不同键=708**，
  items 恰好卡在 256；`_sized_glow_text` 因此每帧被调 **768** 次、产生 **54 384 blit/帧（占全部 blit 的 99.2%）**、
  占累计时间 **64.7%**。字节预算只用 15.3/32 MB。
- **根因**：容量按**条数**封顶且取 256；而键 = (类型, 文本, 颜色, 字号)，工作集随"同时可见台风数 ×
  每台风的巅峰/登陆标注数"线性增长。**当前 206 台风数据下最多 12 个同时存活**（12 → 81 键、100% 命中），
  所以这是**缩放悬崖**：约 30–40 个同时可见台风才会掉下去，合成 207 激活时必掉。
- **建议**：① 把**字节预算**作为唯一硬约束（256 条 → 按 32 MB 反推约 520 条），或直接把条目上限提到
  1024–2048 并同步抬高字节预算到 48–64 MB；② 把 `_ring_offsets` 的**结果**按半径缓存（见 一般-1），
  使"即使 miss 也便宜"；③ 给 `_text_core_cache`（`:123`，512 条）同样处理（实测 708 次 `get_bounding_rect`/帧）。
- **预期收益**：【确定性】稳态下 `_sized_glow_text` 从 **768 次/帧 → 0 次/帧**、blit 从 **54 806/帧 →
  约 27/帧 + 图标本体量级**；对应 3.7 s/帧的计数型工作消失（**计时区间**：该场景 p50 1274 ms → 无法在
  本机给出可靠外推，只能给"上界量级"）。**成本低**（改两个常量 + 键容量策略）；**风险低**
  （纯缓存容量，不改语义），但要复查内存（+16~48 MB，与 严重-1 冲突 ⇒ 两项要一起决策）。

### 一般-1｜`_ring_offsets` 每帧重算 3 072 次（纯函数）
- **位置**：`_draw_icon_mixin.py:146-159`；调用点 `:184`（3 次/调用）、`:186`（1 次/调用）。
- **证据**：【实测·确定性】3 072 次/帧、18 432 次/6 帧；连带 **57 968 cos + 57 960 sin + 127 757 round/帧**；
  tottime 0.578 s / 7.205 s = 8%。
- **根因**：函数只依赖整数半径 `r`（与 `scale` 的乘积也量化到整数），结果却每次重新做三角+去重+append。
- **建议**：`functools.lru_cache` 或模块级 `dict` 按 `r` 缓存（r 的取值集合很小：scale 档 × 半径 1/2/3）。
- **预期收益**：3 072 次调用/帧 + 约 24 万次三角/舍入/append 归零（稳态）；**成本极低**（3 行）；**风险极低**。

### 一般-2｜`typhoon_info` 层没有可见性裁剪（与路径层不对称）
- **位置**：`app/renderer.py:246-248`（`_typhoon_info` 的 season 分支）。
- **证据**：【实测·确定性】207 激活时 `draw_typhoon`（路径，有裁剪）= **87**，而
  `draw_typhoon_info`（图标+文字，无裁剪）= **207**；`draw_typhoon_name` 也 206/帧。
  真实峰值 12 存活时是 12 次 vs 约 8 个可见 ⇒ 约 1/3 白做。
- **根因**：两个层的门控写法不一致 —— 路径层 `should_draw_typhoon` + `_is_typhoon_visible`，
  信息层只有 `ty.act and ty.ss and not ty.sf`。
- **建议**：在 `_typhoon_info` 里加 `if not sim._is_typhoon_visible(ty): continue`
  （`_is_typhoon_visible:272` 已是现成的 bbox 快速判断）。
- **预期收益**：信息层调用量降到可见数（207→87、12→8），文字表面构建同比例下降；**成本极低**；**风险低**
  （注意 `_is_typhoon_visible` 对无 bbox 的台风返回 True，语义保守）。

### 一般-3｜正常模式每帧 3.5 次视频解码 + 2.8 次 grab（屏幕只有 1 个图标）
- **位置**：`_draw_icon_mixin.py:456`（`_preload_upcoming_icon`）、`:840-841`
  （`preload_window_step(..., budget=3, target_size=ts)`）；`smcy_icon.py:146-186`（`_read_at`/`get_frame`）。
- **证据**：【实测·确定性】NORMAL 稳态 `cv2.read` 3.5/帧、`grab` 2.8/帧、`get_frame` 4/帧；
  【实测·计时】NORMAL 帧 p50 = 11.5 ms，而 cProfile 里视频那几项合计约 6 ms/帧（口径放宽，只作量级）。
- **根因**：预载预算固定为 3，与"当前屏幕上有几个图标/会不会切类别"无关，稳态也照跑。
- **建议**：① 预载预算按"离下一次类别切换还有多久"动态给（距下一次切换 > N 帧就降到 0–1）；
  ② 跟踪中的台风与其镜像半球之外不再预载。
- **预期收益**：稳态解码次数降到 1/帧量级（**-2.5 次 read、-2 次 grab/帧**）；**成本低**；**风险中**
  （预载不足会让类别切换那一帧变卡，需要 A/B —— 这正是项目当初加预载要解决的问题）。

### 建议-1｜`should_draw_typhoon` 在正常模式对全表 207 个台风逐个判定，只有 1 个能过
- **位置**：`_draw_path_mixin.py:245-253`、`_draw_typhoons:485-492`。
- **证据**：【实测·确定性】207 次/帧；cProfile 里 `should_draw_typhoon` + `current_typhoon`
  合计 cumtime 约 1.3 ms/帧（NORMAL 帧 13.8 ms 的约 9%，cProfile 口径）。
- **建议**：MODE_NORMAL 下先取 `current_typhoon()`，只对"当前 + fade_path 未过期"的台风走判定。
- **预期收益**：正常模式省 206 次函数调用 + 相应属性查找/帧；**成本极低**；**风险低**。

### 建议-2｜`purple_frame` 的 96 MB 预算名不副实
- **位置**：`_draw_icon_mixin.py:846`。
- **证据**：【实测·确定性】实测峰值 5.0 MB / 13 items，**0 命中**（键含 `frame_idx`，逐帧推进）。
- **建议**：预算降到 24–32 MB（够放 33 个大尺寸帧），把省下的预算让给 严重-2 的 `name_text`。
- **预期收益**：账面 -64 MB 上限；**成本极低**；**风险低**（字节预算不改变命中语义）。

### 建议-3｜`Typhoon._img_cache` 的 12 MB × 207 实例
- **位置**：`app/typhoon.py:103`；唯一消费者 `app/typhoon_render.py:137`。
- **证据**：【实测·确定性】207 个实例 hits=misses=items=0（当前 `icon_set=smcy`）。
- **建议**：**改成惰性创建**（首次 `typhoon_render` 用到时再建），或直接把预算从 12 MB 降到 4 MB。
  纯粹是"账面干净"级别，不是性能问题。

### 一致性观察（不是 bug，但会误导后来人）
1. `renderer.py:202-298` 的图层表里，`typhoon_info` 与 `typhoons` 两个层的裁剪语义不一致（见 一般-2）；
2. `_draw_icon_mixin.py:1323` 注释明确写了"空名时仍要算同名高度"，所以 `_skip_name` 分支**仍然构建文字表面**——
   这是**有意的**（定位基准），不是漏掉的条件；别按"没显示就不该建"去优化它；
3. `perf_render.py` 探针实测 `Surface.blit` **无法 monkeypatch**（`pygame.Surface` 是不可变扩展类型），
   所以 blit 计数走 cProfile 的 builtin 计数 —— 数量精确，但 `tottime` 含 profiler 开销；
4. 首轮 `09_numerics_render.md` 覆盖的是 `simulator/simcore/render.py`（模拟模式），
   与本轮的 `app/` 层是**两套独立实现**（模拟模式的图层缓存有 12 套，回放模式的图标/路径缓存是另一套）。
   两边的 blit 计数**不可直接比较**。

### 值得保留的现状（明确建议不要回退）
1. **显式图层表**（`app/renderer.py:202-298`）：z 序数据化、每层自带可见性判定 + `_layer_drawn` 埋点，
   使"加一层/调顺序"变成改数据而不是改 if/elif 链。**这是本轮唯一让"按层测量"成为可能的架构。**
2. **地图窗口缓存**（`app/map_mgr.py:300-357` + `_pan_cache:279-296`）：实测**平移 8px/帧时
   `draw_map` 仍是 1 次 blit/帧**、`_win_cache_key` 稳定不重建；`_PAN_WINDOW_MARGIN=512` 的
   "重建摊到 ~512px 平移一次"、整数源像素量化、`>120px/帧` 快速拖动时主动放弃缓存的取舍都写明了实测依据。
3. **`cache_store.SurfaceCache`**（`app/cache_store.py`）：字节 + 条数双约束、真 LRU（`move_to_end`）、
   单对象超预算不缓存、**自带 hits/misses/evictions**。本轮**全部主结论都建立在它自带的计数上**，
   这本身就是它最值得保留的地方 —— 建议后续给其它手写缓存也迁到它（迁移难度极低，接口兼容 dict）。
4. **跨缝几何单一来源** `app/render_geom.py`（`clip_segment`/`normalize_chain`/`draw_snap_grid`）：
   `_draw_ocean_areas`、洋流编辑、路径都复用它，避免了三处各写一份环绕翻面。
5. **`GridMap` 的仿射映射**（`simulator/simcore/render.py:144-236`，本轮复核）：
   把"逐点翻面"从整层贴图里拿掉，是模拟模式 sst/ohc 能画出来的前提。
6. **SMCY 流的三级缓存**（缩放帧 / 原生帧 / 流 LRU）：方向是对的（原生帧服务任意尺寸、换挡不 seek），
   本轮的问题只是**预算总量**（严重-1）与**预载节奏**（一般-3），不是结构。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U-1 | 用户**真实窗口 2560×1498** 下的帧成本 | 我先用 1600×900 控制内存（本机只剩 1.6 GiB）。需要一次单独的 2560×1498 跑（估算基线 RSS 会到 ~1.0 GB，有 OOM 风险，建议先做 严重-1 的预算收敛） |
| U-2 | 两个 1.7–1.8 s 的 max 离群值是什么 | 绑核是"偏好"不是独占，core 8 仍可能被占。需要 `time.process_time` 与 `perf_counter` 同时记（Lead 的 `wall_vs_cpu.py` 手法）+ 多次重复，才能区分"被抢占"与"解码 seek 抖动" |
| U-3 | `_sized_glow_text` 的真实占比（我的 64.7% 是 cProfile 口径） | cProfile 对 C 函数按调用计费会放大 Python 循环密集的函数。需要一次**不插桩**的对照：临时把 `name_text` 条目上限调到 1024 再测同一场景（A/B 单变量），我没做（属"改代码"，本轮禁止） |
| U-4 | 快速拖动（>120px/帧）下窗口缓存被主动放弃的那条分支 | 需要模拟真实鼠标拖动轨迹（我按 8px/帧 平移，走的是缓存命中路径）。代码注释自记 600px/帧 30.6 ms vs 11.3 ms，但那是旧数据 |
| U-5 | 登陆特效 / 粒子 / 总结条 / 对话框的渲染成本 | 属 15 分区的交互场景；我只在"默认配置 + 无对话框"下测，`effects` 层实测为空（0 次绘制） |
| U-6 | `_text_core_cache`（512 条）是否也在抖 | 我只测到 `get_bounding_rect` **708 次/帧**（远大于 512），但没直接读它的 len/命中；需要给它加同样的一次性计数器 |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 帧总入口 / 图层表 | `app/renderer.py:65` / `:202-298` |
| `typhoon_info` 无裁剪的循环 | `app/renderer.py:246-248` |
| 路径绘制 / 缓存键 | `_draw_path_mixin.py:993` / `:496-511` |
| 路径可见性裁剪 | `_draw_path_mixin.py:272-296`、`should_draw_typhoon:245` |
| **文字表面热点** | `_draw_icon_mixin.py:162-189`（`_sized_glow_text`） |
| **环形偏移重算** | `_draw_icon_mixin.py:146-159`（`_ring_offsets`）、调用点 `:184/:186` |
| **文本缓存与容量** | `_draw_icon_mixin.py:352`（`name_text` 32MB/256）、`:1043/:1069`（取用）、`:123`（`_text_core_cache` 512） |
| 标注布局（固定槽位） | `_draw_icon_mixin.py:1098-1145` |
| 图标本体与紫滤镜缓存 | `_draw_icon_mixin.py:853-905`、`:846`（`purple_frame` 96MB/240） |
| 视频预载预算 | `_draw_icon_mixin.py:456`、`:840-841` |
| SMCY 解码/缓存 | `app/smcy_icon.py:104-186`（`_cache_cap`/`_native_cap`/`_read_at`/`get_frame`）、`:271`（20 流）、`:45-46`（16+24MB） |
| 地图缓存 | `app/map_mgr.py:279-296`（`_pan_cache`）、`:300-357`（`_window_cache`）、`:387-423`（`draw`）、`:615`（`draw_map`） |
| 缓存基类与计数 | `app/cache_store.py:27-118`（`stats()` 在 `:106`） |
| 探针 | `G:\tmp\perf_render.py`、`perf_render2.py`、`perf_render3.py` |
| 原始输出 | `G:\tmp\perf14_all.txt`、`perf14_blit.txt` |
