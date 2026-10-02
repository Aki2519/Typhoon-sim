# 13 启动性能诊断（整个程序 · 启动路径）

> 作者：teammate `app-sim` ｜ 只诊断不改代码 ｜ 测量机：`C:\Users\Yukioto\AppData\Local\Python\python.exe`（3.14.2 / pygame-ce 2.5.7 / SDL 2.32.10，`SDL_VIDEODRIVER=dummy`）
> 术语遵循 `_BRIEF3.md` §0：**模拟器 = 整个工作区程序**；**模拟模式 = 内部 SimCore**。本文只谈**启动**。
> 证据分级：**实测**＝本轮跑出来的；**静态**＝AST/文件系统/代码判读得到；**推断**＝由前两者推出。
> 计时的可信度前提见 **§5**；本节所有绝对耗时都是**区间**，不是承诺值。
>
> 章节：§1-§8 是标准骨架，**§9 是任务额外要求的一节"值得保留的现状"**。

---

## 一句话定位

这个 pygame 应用的启动是"**一次同步做完所有事**"：`TySim()` 构造函数一台机器上要 5.2–10.7 s，占整个启动的 **88%**，其中最大的三块是**解两张 10800×5400 的 PNG（各 222 MB）**、**建全分辨率陆地 alpha 表**、以及**把 SimCore 内核 import 进来**——而用户此刻可能只想看一眼路径。

---

## 1. 清单与职责（启动路径涉及的文件）

| 文件 | 行数 | 在启动期的职责 | 关键位置 |
|---|---|---|---|
| `main.py` | 255 | 入口：DPI、`pygame.init`、mixer、窗口、`TySim()`、首帧、崩溃日志 | `_apply_dpi:12-31`、`main:110-217` |
| `app/ty_sim.py` | 868 | 主控制类；构造期完成台风/地图/陆地/图标/对话框的**全部**加载 | `__init__:165-229`、`_init_sim_mode:176` |
| `app/config.py` | 507 | `AppConfig`（127 字段）+ 逐字段反序列化 | `load:284-338`、`_coerce:349-490` |
| `app/config_store.py` | 176 | 配置落盘唯一入口（启动只读，不写） | `RUNTIME_FIELDS:48-52` |
| `app/constants/fonts.py` | 217 | **import 期**建 6 个字体实例 + `SmartFont` + `rt()` | `_load_font:18-45`、`:48-56`、`:148-151` |
| `app/ty_sim_mixins/draw_info_boxes_mixin.py` | 685 | 模块级再建 3 个字体（54/34/25） | `:24`、`:29-30` |
| `app/data_repo.py` | 498 | 遍历 `typhoon/` 并解析**全部**报文 | `load_typhoon_files:130-175`、`parse_typhoon_file` |
| `app/map_mgr.py` | 642 | `MapView` 载底图；陆地掩码/覆盖层重建；`update_map_image = update_view`（别名） | `MapView.__init__:16-41`、`update_view`、`_rebuild_land_and_overlay:483-534`、`_ensure_land_mask:556-574`、`is_land_at_geo:586-604`、**`:613` 别名** |
| `app/resource_mgr.py` | 179 | 图标集：21 类别 × ring/center + 17 登陆图 + 21 音效 | `_load_all:72-128` |
| `app/particle_effect.py` | 328 | **模块级 `import cv2` + `from PIL import Image`**（只为粒子/特效） | `:8`、`:10`、`iter_preload_particles:58-67` |
| `app/smcy_icon.py` | 820 | **模块级 `import cv2` + numpy**；SMCY 视频流 LRU；4 个预热生成器 | `:10-11`、`iter_preload_landfall_effects:582-600`、`iter_preload_icon_streams:609-646`、`iter_preload_summary_streams:728-768` |
| `app/dialog_manager.py` | 109 | **急切构造 21 个对话框对象** | `__init__:40-64` |
| `app/settings.py` | 2597 | 主设置对话框（构造期预渲染大量文本） | `_pre_render_texts:563-686` |
| `app/statistics/*`（14 个文件） | 5387 | 统计对话框族（构造期各自建字体/预渲染） | `summary_list:22-25`、`dialog_chart:37` |
| `app/monthly_summary.py` / `summary_effect.py` / `landfall_effect.py` | 212/349/232 | 模块级 SmartFont（33/27、20/26/30/32、20） | `:14-15`、`:30-33`、`:10` |
| `app/typhoon_render.py` | 169 | 每只台风建 geo 样条（`smooth_path` 开着时） | `update_screen_points:56-112`、`_get_geo_spline:23-46` |
| `app/window_util.py` | 184 | 与系统实际窗口对账（headless 下 no-op） | `actual_client_size`、`keep_window_on_screen` |
| `simulator/simcore/core.py`（+`render.py`） | 5205(+1648) | **被启动期 import**（用户可能永不进模拟模式） | `snap_env_step:773`、`latest_real_start`、`set_label_fn` |

---

## 2. 数据流 / 调用链

### 2.1 `main.py` 的真实启动顺序（对账用）

```
main.py:12  _apply_dpi()                      # 读 config.json（IO #1）
main.py:33  pygame.init()                     # 起 SDL/SDL_image/SDL_ttf/SDL_mixer
main.py:35  pygame.mixer.set_num_channels(32)
main.py:39  pygame.key.stop_text_input()
main.py:43  from app.config import AppConfig  # → app.constants → colors/layout/fonts（6 个字体）
main.py:44  import app.constants as constants # 已缓存
main.py:45  from app.ty_sim import TySim      # ← 第一方闭包 80 个模块（§3.1）
main.py:48  _cfg = AppConfig.load(CONFIG_FILE)# 读 config.json（IO #2）
main.py:117 pygame.image.load(assets/icon.png) + set_icon
main.py:122/132  pygame.display.set_mode(...) # 首屏 2560×1540
main.py:126-138  start_maximized → _maximize_window()（Windows API）
main.py:150-157  window_util.actual_client_size / keep_window_on_screen
main.py:164-172  **以 surface 真实尺寸为准**，同步 constants + _cfg
main.py:178-179  _draw_splash() + display.flip()       # 首个可见画面
main.py:180  sim = TySim(screen, cfg=_cfg, on_load_progress=_on_load)   # ★ 88% 的时间在这里
main.py:187-217  主循环：event → update → draw → _tick_warmup → flip
```

### 2.2 `TySim.__init__` 内部（本轮**包装类方法**实测的顶层子步骤）

```
ty_sim.py:175 _init_attributes()                 # O(1)，微秒级
ty_sim.py:176 _init_sim_mode()                   # ★ 675 ms（含 import simcore.core/render + latest_real_start）
ty_sim.py:177 _init_resource_managers()          # 258 ms（20 个 Sound + 42 次图标查找 + 程序化生成）
ty_sim.py:179 _install_descriptors()             # O(字段数)
ty_sim.py:183 ViewState(...)                     # 0 ms
ty_sim.py:185 ACEEngine(self)                    # 0 ms
ty_sim.py:186-187 DataRepository + bind          # 0 ms
ty_sim.py:189 SeasonController / 190 PlaybackController  # 0 ms / 0 ms
ty_sim.py:192 _pre_render_texts()                # 26 ms（~30 次 rt()）
ty_sim.py:194 repo.load_typhoon_files(on_load)   # ★ 544 ms（207 个文件 / 8491 报点）
ty_sim.py:195 map_mgr.update_map_image()         # ★★ 1952 ms（= update_view：载 map.png + 画地图 + 重建陆地）
ty_sim.py:196 get_draw_rect()
ty_sim.py:197 DialogManager(self)                # ★ 121 ms（急切构造 21 个对话框）
ty_sim.py:199-201 ScriptEngine / ScriptDialog / MonthlySummary  # 0 / 7 / 0 ms
ty_sim.py:202-204 InputHandler / InputController / Renderer
ty_sim.py:205 update_all_screen_points()         # ★ 286 ms（207 只台风重投影 + geo 样条）
ty_sim.py:206 map_mgr.update_land_mask()         # 0 ms（内部 lazy 标记）
ty_sim.py:209 map_mgr._ensure_land_mask()        # ★ 840 ms（land.png 解码 + 缩放 + tobytes→alpha）
ty_sim.py:211 map_mgr.is_land_at_geo(0.0, 0.0)   # ★ 469 ms（建 55.6 MB 地理 alpha 表）
```
（★ = 本轮实测的顶层耗时；绑核 0-7 那次，`ctor = 5179 ms`。）

### 2.3 已知探针（`tools/_probe_startup_timing.py`）与 `main.py` 的真实差异 —— 对账

`--agg 5` 的 10 个阶段（**独立进程**，min/median/max，ms）与**占比**（按 median ÷ 每次进程合计的 median 5908.7 ms）：

| # | 阶段 | min | median | max | 占比 |
|---|---|---|---|---|---|
| 1 | import pygame | 145.7 | 154.2 | 407.9 | 2.6% |
| 2 | import numpy | 114.8 | 159.0 | 270.2 | 2.7% |
| 3 | import app.config | 26.4 | 30.5 | 92.7 | 0.5% |
| 4 | import app.ty_sim(all mixins) | 158.5 | 164.9 | 851.4 | 2.8% |
| 5 | pygame.init() | 55.0 | 63.8 | 104.6 | 1.1% |
| 6 | AppConfig.load | 0.6 | 0.7 | 1.0 | 0.0% |
| 7 | set_mode | 14.4 | 18.5 | 20.9 | 0.3% |
| 8 | **TySim() ctor (incl. load_typhoon_files)** | 4841.8 | **5218.4** | 5711.5 | **88.3%** |
| 9 | first update | 1.4 | 2.3 | 3.5 | 0.0% |
| 10 | first draw | 92.7 | 131.1 | 279.1 | 2.2% |
| — | **合计** | 5643.6 | **5908.7** | 7627.1 | 100% |

**占比最大的 3 段：`TySim()` 88.3% ｜ import 段（1+2+4）8.1% ｜ first draw 2.2%。** 其中 import 段里最重的是 `app.ty_sim`（164.9 ms）与 `numpy`（159.0 ms）。

**探针漏测 / 与真实路径不同的地方（本轮实测或静态确认）**：

| # | `main.py` 真做了什么 | 探针 `once()` 的行为 | 影响 |
|---|---|---|---|
| a | `main.py:12-31 _apply_dpi()`：**在 `pygame.init()` 之前**读一次 `config.json` 并调 Windows DPI API | **完全没有** | 漏一次 config.json IO + DPI API（实测量级 <1 ms，但顺序被改） |
| b | `main.py:33 pygame.init()` 在**所有 app import 之前** | `pygame.init()` 在 **stage 5**，即 `import app.config`(stage 3) **之后** | ⚠ **阶段归属被挪动**：`app.constants.fonts:48-56` 在 import 期建 6 个字体；探针里这一步发生在 `pygame.init()` 之前，会走 `fonts.py:26-32` 的 `pygame.font.init()` 兜底 ⇒ 字体成本被记进 stage 3/4 而不是 stage 5 |
| c | `main.py:35-41`：`mixer.set_num_channels(32)` + `key.stop_text_input()` | 没有 | 这两句的成本被漏在 stage 5 之外 |
| d | `main.py:117-123`：`image.load(assets/icon.png)` + `set_icon` + `set_caption` | 只有 `set_mode` + `set_caption` | 漏 `assets/icon.png` 解码（实测 4 ms） |
| e | `main.py:126-138`：`start_maximized` → `_maximize_window()`（FindWindow/ShowWindow/GetClientRect）+ **第二次 `set_mode`** | **没有** | 漏一次 set_mode 与 Windows 窗口 API |
| f | `main.py:150-157`：`window_util.actual_client_size` + `keep_window_on_screen`（可能第三次 set_mode + 移动窗口） | **没有** | 实测 headless 下 3.5–6.1 ms；真机（含窗口移动）会更贵 |
| g | `main.py:178-179`：`_draw_splash()` + `display.flip()`（首个可见画面） | **没有** | 漏首屏绘制 |
| h | `main.py:174-176` `on_load_progress`：**每解析一个台风文件就 `_draw_splash` + `flip()`** ⇒ 207 次文字重渲染 + 207 次 `flip()` | **没有**（探针不传 `on_load_progress`） | ⚠ **这是最大的一处漏测**：真机上随台风数线性增长，dummy SDL 下 `flip()`≈0 但 `font.render` + 2 次 blit 仍在；台风库变大时这段会变成可见瓶颈 |
| i | `main.py:207-213`：`sim.update(dt)` + `sim.draw()` + **`_tick_warmup()`** | stage 9/10 用 `update(0.0)`/`draw()`，**没有 `_tick_warmup`** | 漏预热（本文 §3.5 单独测了：62 帧 / ~233 ms 每帧） |
| j | `main.py:187-192` 的帧上限与 `dt` 钳制 | 无关（只测首帧） | — |

**⇒ 修正后的真机启动分段（确定性 + 区间）**：`_apply_dpi`(<1 ms) → `pygame.init`+mixer+stop_text_input(约 65 ms) → `import app.config`+`app.constants`（含 6 个字体，约 31 ms）→ `import app.ty_sim`（约 165 ms）→ `AppConfig.load`(<1 ms) → icon+`set_mode`(约 15 ms) → `_maximize_window`+窗口对账（未测，Windows API，量级 5–50 ms）→ splash+flip(约 1 ms) → **`TySim()`（88%，4.8–13.5 s）** → 首帧 update/draw（约 130 ms）→ 预热 62 帧（~14 s，摊到之后的使用过程中）。**总可见首屏时间 ≈ 5.5–14 s（区间）**，其中 `TySim()` 占 ~88%。

### 2.4 首帧之后的"预热"（不在 ctor 里，但属于启动体验）

```
main.py:213 sim._tick_warmup()                    # 每帧调用
ty_sim.py:284-297 _tick_warmup: 首帧只记 deadline=now+1200ms，之后每帧一次 _warmup_visuals()
ty_sim.py:253-282 _warmup_visuals: 取当前 job 的生成器，**只 next(gen) 一次就 return**
ty_sim.py:261     门控：正在播放 / 右键拖拽 / 有对话框 → 整帧跳过（预热让路）
ty_sim.py:231-251 _warmup_jobs: 4 个任务（icon_set=='smcy' 时才有后两个）
```

---

## 3. 核心机制详解（逐段根因）

### 3.1 import 链：第一方闭包 **80 个模块 / 36,747 行**

- **静态实测**（`G:\tmp\closure.py`，AST 解析 `from .x import` / `import app.x` 的**模块级**语句）：
  `import app.ty_sim` 的第一方闭包 = **80 个模块、36,747 行**。
  ⚠ 第一轮报告与派活里说的"46 个模块"偏低；46 大概是"某个子集"或旧统计。精确值见探针输出。
- **运行期实测**：`sys.modules` 计数 **185（import pygame 后）→ 195（import app.config 后）→ 424（import app.ty_sim 后）**，即这一步新增 **229 个条目**（含第三方：cv2 一族、numpy、PIL、typing 等）。
- 重的模块级第三方依赖（静态 + 运行期双向确认）：
  | 位置 | 依赖 | 运行期是否真的进来了 |
  |---|---|---|
  | `app/particle_effect.py:8,10` | `cv2`、`PIL.Image` | **是**（`cv2 in sys.modules = 1`） |
  | `app/smcy_icon.py:10-11` | `cv2`、`numpy` | **是** |
  | `app/statistics/heatmap.py:8` | `PIL.Image` | **是**（经 `dialog_manager` 拉入） |
  | `app/ty_sim_mixins/_draw_icon_mixin.py:8` | numpy | **是** |
  | `app/ty_sim_mixins/sim_mode_mixin.py:18` | numpy | **是** |
  | matplotlib / scipy / xarray / netCDF4 / pandas | — | **否**（`matplotlib in sys.modules = 0`） ✅ |
- **为什么 cv2 会进来**：`app/ty_sim.py:39` 是唯一入口
  ```python
  from .particle_effect import preload_particles      # 模块级
  ```
  而 **`preload_particles` 全仓从未被调用**（grep：只有 `particle_effect.py:70` 定义 + `ty_sim.py:39` 导入 + `ty_sim.py:238` 在方法里**另外**延迟导入 `iter_preload_particles`）。也就是说：一行**无用**的模块级 import 把 OpenCV + Pillow 拖进了启动路径。
- **内核也被启动期 import 了**：运行期实测 `simulator.simcore.core in sys.modules = 1`、`simulator.simcore.render in sys.modules = 1`，而 `import app.ty_sim` 这一步（`_probe_startup_timing.py` 的第 4 段，165 ms）**不含**内核 —— 内核是在 `TySim.__init__` 的 `_init_sim_mode`（`ty_sim.py:176`）里被拉进来的：
  - `sim_mode_mixin.py:213-216` `from simulator.simcore.core import snap_env_step`
  - `sim_mode_mixin.py:471-474` `_coerce_start` → `parse_start / latest_real_start`（**读气候模态 npz**，audit 里 2 次 `.npz` open）
  - `sim_mode_mixin.py:248-253` `from simulator.simcore import render as _R` 只为注入 `set_label_fn`
  ⇒ **不进模拟模式的用户也要付这笔**（实测 `_init_sim_mode` = 675 ms 绑核 / 1251 ms 未绑核）。

### 3.2 资源加载：解码 **445 MB** 只为一张 2560×1540 的窗口

**实测**（包装 `pygame.image.load`，4 次调用，合计 `w×h×4` = **466,828,544 B = 445.2 MB**）：

| 调用 | 文件 | 原始大小 | 解码尺寸 | 解码字节 | 耗时（未绑核那次） |
|---|---|---|---|---|---|
| 1 | `map/map.png` | 45.9 MB | **10800×5400** | **222.47 MB** | 3245 ms（未绑核那次） |
| 2 | `map/land.png` | 0.5 MB | **10800×5400** | **222.47 MB** | 1730 ms |
| 3 | `assets/icon.png` | 小 | 256×256 | 0.25 MB | 4 ms |
| 4 | `assets/smcy/landfall/landfall_TD.png` | 小 | 40×40 | 0.01 MB | 0.5 ms |

- **过配比（确定性）**：窗口 2560×1540 = 3.94 Mpx；`map.png` 与 `land.png` 各 58.32 Mpx ⇒ **每张是可见像素的 14.8 倍**；两张解码共 445 MB vs 屏幕 Surface 15.8 MB ⇒ **28 倍**。
- 这两张 Surface 是**常驻**的：`map_view.original_img`（`map_mgr.py:18`）与 `_land_orig`（`map_mgr.py:473`）。
- 首帧渲染实际只用屏幕大小：`_cached_map_render = Surface(screen, map_height)` + `map_view.draw(...)`（`map_mgr.py:549-555`）。

**陆地 alpha 表（实测字节数）**：
- `_land_alpha_bytes`（屏幕空间）：**3.5 MB**，宽 2560（`_rebuild_land_and_overlay:530-533` 的 `tobytes(land_img)[3::4]`）
- `_land_geo_alpha`（地理空间，全分辨率）：**58,320,000 B = 55.6 MB**，10800×5400（`is_land_at_geo:594`）
- 两者**内容不同**（实测 `bytes(a1) == bytes(a2)` → False），不是冗余副本。
- 但 `is_land_at_geo` 首次调用会先 `pygame.image.tobytes(land, 'RGBA')` = **222.5 MB 临时对象**再切片 ⇒ 这是启动峰值的来源。

**内存（实测，两次独立进程）**：ctor 结束时 RSS **580.6 MB / 722.1 MB**，峰值 **833.2 MB / 944.5 MB**。
按 `_BRIEF3.md` §2（物理 15.74 GiB、可用常年 1–3 GiB、有 OOM 前科），**一个只想看路径图的用户占掉 ~0.6–0.95 GB 常驻**，其中 445 MB 是两张 10800×5400 的地图/掩码 Surface。

**台风数据（确定性）**：
- `typhoon/` = **210 文件 / 5.08 MB**（75 `.dat` + 132 `.txt` + 3 其他）；`parse_typhoon_file` 被调 **207 次**，产出 **207 只台风 / 8491 个报点**，实测 544 ms。
- audit hook 实测启动期 `open()`：`.dat` 75 次、`.txt` 132 次、`.pyc` 479 次、`.json` 11 次、`.npz` 2 次、`.py` 2 次、`.gif` 1 次、`.zip` 1 次。

**字体（确定性）**：`pygame.font.Font` 被调 **19 次不同 (文件, 字号)**，全部指向同一个 **19.6 MB** 的 `assets/font/MapleMono-NF-CN-Medium.ttf`：
- `app/constants/fonts.py:48-56,148-151` → 6 个（14/15/18/19/21/22）
- 其余 12–13 个来自**模块级** SmartFont：`landfall_effect.py:10`(20)、`monthly_summary.py:14-15`(33,27)、`statistics/dialog_chart.py:37`(30)、`statistics/summary_list.py:22-25`(28,36,32,64)、`summary_effect.py:30-33`(20,26,30,32)、`draw_info_boxes_mixin.py:24,29-30`(54,34,25)；第 19 个字号（63）来自**渲染期按需建**的 `_draw_icon_mixin.py:78` / `:134`（`_load_font(FONT_FILE, size, size)`，`size` 是变量，随 `peak_label_size`/`icon_size` 等设置变化）。
- `_load_font` 自身有 `(file,size)` 缓存（`fonts.py:33-36`），所以**同尺寸不会重复解析**；19 是去重后的真实解析次数。每次 0.3–1.0 ms（实测），合计 ~6–13 ms ⇒ **不是耗时大户，是内存与一致性问题**。

**音效（确定性）**：`pygame.mixer.Sound` **20 次**（18 个 `sound.landfall.*.ogg` + `ERI-1.ogg` + `note_ts.ogg`），实测单个 6–44 ms，合计约 200–300 ms（含 OGG 解码），全部发生在 `_init_resource_managers` 的 `_load_all(load_sounds=True)`（`resource_mgr.py:110-128`）。**注意**：当前 `icon_set='smcy'`，图标 PNG 在 `assets/SMCY/` 下根本不存在（那里是 mp4），所以 42 次 `fip()` 查找全部落空后走**程序化生成**（`_create_ring_icon/_create_center_icon`，`resource_mgr.py:129-158`）——即默认配置下图标是**画出来的**而不是加载的。

### 3.3 对话框族：**21 个对象在启动期全部构造**

**实测**：`DialogManager()` 顶层 121 ms（绑核）/ 520 ms（未绑核），构造 **21 个**含 `.active`+`.sim` 的对象（`dialog_manager.py:40-64`）——包括 `settings.Settings`(2597 行)、`PointList`(790)、`PaintDialog`(969)、`ScriptDialog`(934)、`TyList`(893)、14 个 `statistics/*` 对话框。它们各自的 `__init__` 会预渲染文本（例如 `settings._pre_render_texts:563-686`）。
**推断**：这就是"121–520 ms + 21 份常驻对象"的根因；用户启动后通常一个都不打开。

### 3.4 首帧重投影 + 样条：`update_all_screen_points` = 286 ms

`ty_sim.py:592-599` → `ViewState.update_screen_points`（`view_state.py:79-95`）对 **207 只台风**逐只 `ty.update_screen_points(f, view_rect, anchor)`；`smooth_path=true`（`config.json`）时 `typhoon_render.py:96-112` 还会为每只建/取 geo 样条（`_get_geo_spline`，10 段）。8491 报点 × 10 段 ≈ 8.5 万点量级。
**注意**（静态）：`typhoon_render.py:93-95` 在 bbox 与 `view_rect` 不相交时**早退**，所以屏幕外台风不建样条 —— 实际条数 < 207（未计数，见 §8）。

### 3.5 预热：**62 帧**，每帧 ~233 ms

**确定性实测**（两条路都跑了：① 把 4 个生成器的重活全换成桩、只数 `yield`；② **保留真 `SMCYIconManager`**、只把 `_VideoStream` 换成桩，让 `_MAX_STREAMS` 与 mp4 存在性真正生效。`G:\tmp\probe_warmup.py`、`G:\tmp\probe_iconyield.py`）：

| 任务 | 生成器 | 真实帧数 | 依据 |
|---|---|---|---|
| 粒子预加载 | `particle_effect.iter_preload_particles:58-67` | **4** | 4 个 `yield`，无上限 |
| 特效预加载 | `smcy_icon.iter_preload_landfall_effects:582-600` | **16** | `_LANDFALL_MAP` 18 项 / `_LANDED_MAP` 16 项，按 name 去重后每项一 yield（实测 16） |
| 图标流预加载 | `smcy_icon.iter_preload_icon_streams:609-646` | **20** | 报点覆盖 18 个 cat × 2 半球，被 **`_MAX_STREAMS=20`** 截断（真 manager 实测：正好打开 20 路流；若把上限拿掉是 36） |
| Summary 流预加载 | `smcy_icon.iter_preload_summary_streams:728-768` | **22** | `(cat, hemi)` 组合，逐组合 yield；LRU 上限 `_MAX_SUMMARY_STREAMS=10` 会让常驻流只留 10 路 |
| **合计** | | **62 帧** | |

- **实测每帧成本**：连续 8 帧 `_tick_warmup()` 共 **1861 ms**（≈233 ms/帧），8 帧后 `_warmup_done=False`、`job_idx` 仍在第 1 个任务。⇒ 62 帧 × ~233 ms ≈ **14.4 s 的间歇卡顿**（每帧一次大停顿，视觉上是"每隔几帧卡一下"）。
- 预热结束时长驻 **30 路视频流**（20 图标 + 10 Summary），对应 `assets/SMCY/mainicon`(343.64 MB) 与 `summary`(73.71 MB)。
- 门控（`ty_sim.py:261`）：正在播放 / 右键拖拽 / 有对话框 → **跳过**该帧。用户一边操作，预热就一直做不完 —— 它把 ~14 s 的卡顿摊进整个使用过程，而不是集中在启动。
- 只有 `icon_set=='smcy'` 才追加后两个任务（`ty_sim.py:245-250`），simple 图标集不会付这笔 —— 这个判断本身是对的。

---

## 4. 输入输出资产（启动期真正读到的）

| 路径 | 文件数 | 字节 | 启动期读法 | 说明 |
|---|---|---|---|---|
| `config.json` | 1 | 4 KB | **读 2 次**（`main.py:16-19` 的 `_apply_dpi` + `main.py:48` 的 `AppConfig.load`） | audit 实测 `.json` 11 次 open |
| `map/map.png` | 1 | 45.9 MB | `image.load().convert()` → **10800×5400 / 222.47 MB Surface** | `map_mgr.py:18` |
| `map/land.png` | 1 | 0.5 MB | `image.load().convert_alpha()` → **10800×5400 / 222.47 MB Surface** | `map_mgr.py:473` |
| `map/land2.png`, `map/heightmap.png` | 2 | 43.9 / 59.1 MB | **启动期完全没有读**（audit 未见） | 用途未确证（§8） |
| `assets/icon.png` | 1 | 小 | 窗口图标 | `main.py:117` |
| `assets/font/MapleMono-NF-CN-Medium.ttf` | 1 | **20,553,556 B** | 被 `pygame.font.Font` 解析 **19 次**（19 个不同字号） | 75.8 MB 的 `assets/font/` 里只用了这一个 |
| `assets/sound/*.ogg` | 20 | 684 KB 目录 | `mixer.Sound` 20 次 | 18 landfall + ERI + note_ts |
| `typhoon/**` | **210**（75 `.dat` + 132 `.txt`） | **5,323,864 B** | 全量解析 → 207 台风 / 8491 报点 | `data_repo.py:130-175` |
| `simulator/simcore/*.npz` | 2 | — | 被 `latest_real_start` 读（audit `.npz` 2 次） | 经 `_init_sim_mode` |
| `assets/SMCY/**` | 110 | **424.86 MB**（`mainicon` 343.64 + `summary` 73.71） | **启动期只读了 1 张 40×40 的 landfall png**；mp4 由预热在首帧之后分帧打开 | 预热 62 帧的来源 |
| `assets/` 合计 | **7538** | **516.97 MB** | 启动期实际解码 ≈ 445 MB（见 §3.2） | |
| `map/` 合计 | 4 | **149.40 MB** | 启动期解码 2 张 = 445 MB | |
| 目录体积参照 | — | `typhoon/` 5.08 MB、`simulator/` 2.0 GB（项目 3.9 GB 主体） | | |

---

## 5. 如何运行 / 如何验证（测法与这台机器允许的结论强度）

### 5.1 用的命令（全部只读；探针在 `G:\tmp\`，不进工作区）

```bash
cd /mnt/g/气象/Typhoon-sim
# 全路径写(本会话的 bash 里 $VAR 展开不可靠, 别用变量)

# ① 仓库现成工具（10 阶段、独立进程）—— 沿用，未修改
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe tools/_probe_startup_timing.py --agg 5
# ② 绑核对照（父进程设掩码，子进程继承 ⇒ 不改仓库工具）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/agg_affinity.py 3     # 顺序两臂(有漂移混淆)
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/agg_interleave.py 2   # 交错三臂(推荐)
# ③ 逐逻辑核速度图
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/core_map.py 24
# ④ 详细分解：主路径分段 + ctor 子步骤 + 确定性计数 + audit open()
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_startup.py      # 默认绑 0-7；STARTUP_MASK 可覆盖
# ⑤ 预热帧数(桩)/内存/两张 alpha 表/CPU-墙钟比
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_warmup.py
# ⑤b 用**真** SMCYIconManager 数预热 yield(只换 _VideoStream, 不解码)
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_iconyield.py
# ⑥ 静态: 第一方 import 闭包(不 import, 纯 AST)
python3 /mnt/g/tmp/closure.py
# 中文输出若乱码: ... 2>&1 | iconv -f GBK -t UTF-8
```

### 5.2 这台机器的计时约束（照 `_BRIEF3.md` §3，并加了本轮自己的验证）

- **机器状态（本轮）**：可用物理内存 662 MB → 1928 MB（波动）；WSL `loadavg` 0.12；无并发重活（我的进程都是串行的）。
- **绑核交错对照（实测，`G:\tmp\agg_interleave.py`，每臂 2 次、三臂按轮次交错）**：

  | 臂 | min | median | max | 极差 |
  |---|---|---|---|---|
  | 不绑核（0xFFFFFF） | 7566.5 ms | 7714.5 ms | 7862.6 ms | **3.9%** |
  | 绑**单个**逻辑核 8（0x100） | 14503.4 ms | 15311.4 ms | 16119.4 ms | **11.1%** |
  | 绑 0-7（0x00FF） | 7692.9 ms | 7757.2 ms | 7821.5 ms | **1.7%** |

- **顺序两臂（同样 3 次，先不绑核再绑核 8）** 得到 unbound 16.0% / 1P 46.1% —— **两项都明显差于交错版**，说明**臂顺序/机器状态漂移才是第一轮离散度的主因**，交错设计把它压掉了。这一点建议同步给 task-12（测法修复）。
- **`cpu/wall ≈ 0.96–0.98`**（两次独立进程：wall 8246 ms / cpu 8094 ms；wall 6207 / cpu 5938）⇒ 启动 ctor **基本是单线程**。因此"绑单核变慢 2 倍"只能解释为**那颗核当时更慢/被别的负载占着**，而不是"并行度被剥夺"。
- **逐逻辑核微基准失败**（`G:\tmp\core_map.py`，3M 次整数运算 ×2）：core 10 = 490 ms、core 16 = 1630 ms、core 8/9 = 925–935 ms、core 0–7 = 556–1059 ms。这个负载**太短、噪声太大**（±40% 抖动），**不足以**独立验证 P/E 拓扑归因。⇒ 我**不**对"逻辑 8 是 P 核还是 E 核"下结论；只报"绑 0-7 这一组最稳、绑单核最不稳"这个可直接复现的操作性结论。
- **绝对耗时只能当区间**：同一份代码的 `TySim()` ctor 实测分布（逐个来源可查）：`--agg 5` → 4841.8 / 5218.4 / 5711.5 ms；交错三臂 unbound → 6530.9 / 6927.2 ms、绑 0-7 → 6795.3 / 6816.1 ms、绑单核 8 → 11542.6 / 13470.5 ms；`probe_startup.py` 绑 0-7 → **5178.8 ms**、未绑核 → **10650.8 ms**；`probe_warmup.py` → 8246 / 6207 ms。**跨度 4.8–13.5 s**。所以本文的主结论全部建立在 §3 的**确定性证据**（模块数 80、解码 445.2 MB、Surface 10800×5400、字体 19 次、Sound 20 次、台风 207/8491、对话框 21 个、预热 62 帧、alpha 表 3.5 + 55.6 MB）之上；计时只用于**同一窗口内的相对排序**。

### 5.3 本轮**没有**测的

真实窗口（`flip()`/vsync/DPI 缩放）、SSD 冷缓存下的首次 IO、绘画/统计/脚本模式的数据加载、内存随时间的变化（只测了 ctor 后与预热 8 帧后）。

---

## 6. 问题清单与优化建议（分级）

### 阻塞

**B1｜两张 10800×5400 的地图/掩码 Surface 常驻 445 MB，峰值 833–952 MB —— 在"可用 1–3 GiB、有 OOM 前科"的机器上这是启动路径的最大风险。**
- 位置：`app/map_mgr.py:18`（`map.png` → `MapView.original_img`）、`app/map_mgr.py:473`（`land.png` → `_land_orig`）、`app/map_mgr.py:594`（`tobytes` 222.5 MB 临时量）、`app/ty_sim.py:209-211`（**构造期强制**建掩码）
- 证据（实测）：`image.load` 4 次、解码合计 **445.2 MB**；`map.png`/`land.png` 各 10800×5400 = 222.47 MB；ctor 后 RSS **580.6/722.1 MB**、峰值 **833.2/944.5 MB**；窗口只有 2560×1540 = 15.8 MB ⇒ **28 倍**
- 根因：底图按"全球 0.5°/px 级"的原始分辨率整幅解码并常驻，而运行时只需要屏幕可见窗口的缩放结果（`_cached_map_render` 就是屏幕大小）
- 建议修法：① 把 `map.png`/`land.png` 预处理成**金字塔/瓦片**（或预缩放到屏幕 2× ≈ 5120×2880 → 59 MB）；② 把陆地 alpha 离线预计算成 **1 字节/点的 `land_mask.bin`**（58.3 MB 文件直接 `np.frombuffer`，省掉 222.5 MB 的 `tobytes` 临时量）
- 预期收益：常驻 445 MB → 60–120 MB（**−325 MB 量级**）；峰值 **−222 MB**；`image.load` 的像素量按比例下降
- 成本：中（需改 `MapView` 的坐标换算与 `_rebuild_land_and_overlay`，并加一个离线预处理脚本）
- 风险：放大时底图变糊（`min_scale`/`wrap_px`/`geo_to_screen` 的像素域会变）⇒ 必须回归 `tests/test_map_resize.py`、`tests/test_pan_reuse.py`、`tests/test_seam_render.py`

### 严重

**S1｜`TySim.__init__` 把一切同步做完，占启动 88%，且首屏 splash 之后没有进度反馈。**
- 位置：`app/ty_sim.py:165-229`（全部）；`main.py:180`（同步调用）
- 证据（实测，绑 0-7）：`ctor = 5179 ms` / 总 `5908 ms`；`--agg` 10 段里第 8 段 median 5737 ms / 总 6427 ms；ctor 内 4 个大块：`update_map_image` 1952、`_ensure_land_mask` 840、`_init_sim_mode` 675、`load_typhoon_files` 544
- 根因：构造期把"必须"（配置/窗口/渲染器）与"可以稍后"（陆地掩码、地理 alpha 表、台风重投影+样条、21 个对话框）混在一起
- 建议修法：把 4 个可延后块挂到已有的分帧预热框架上（`_warmup_jobs`/`_warmup_visuals` 已经在做这件事），或至少在每块之间 `flip()` 一次进度条（`_draw_splash` 已支持 `done/total`，`main.py:87-107`）
- 预期收益：首屏可见时间 **−1.4～−2.5 s 区间**（= 840+469+286+121 的和，绑核实测），且确定性产出不变
- 成本：中
- 风险：`_sync_land_state` 在首帧 `update` 里就要用陆地状态（`ty_sim.py:667-679`）⇒ 掩码的延后必须保证"首帧之前就绪"或首帧跳过登陆判定

**S2｜用户可能永不进模拟模式，却付了 SimCore 内核的 import + 一份 `latest_real_start`。**
- 位置：`app/ty_sim.py:176` → `app/ty_sim_mixins/sim_mode_mixin.py:213-216`、`:248-253`、`:471-474`
- 证据（实测）：`_init_sim_mode` = **675 ms**（绑核）/1251 ms（未绑核）；`simulator.simcore.core in sys.modules = 1`、`simulator.simcore.render in sys.modules = 1`；audit 里 2 次 `.npz` open；`import app.ty_sim` 那一段（165 ms）**不含**内核
- 根因：`_init_sim_mode` 在构造期就调用会 import 内核的函数（`snap_env_step` / `_coerce_start`→`latest_real_start` / 注入 `set_label_fn`）
- 建议修法：构造期只保存 cfg 里的字符串（`sim_start` 已是字符串），把三个 import 推迟到 `switch_mode()` 进入 `MODE_SIM` 那一步；`numpy` 的两个模块级 import 也只有模拟模式用得到
- 预期收益：启动 **−675 ms 区间** + 少拉约 130 个第三方模块（cv2 已在别处拉入，见 S3）
- 成本：低-中
- 风险：`_sim_start_dt` 的解析时机变化 ⇒ 回归 `tests/test_sim_time_model.py`、`tests/test_sim_mode.py`
- ⚠ **不要误伤**：`app → simulator` 的**函数内**延迟 import 本身是**做对的**（见 §9）；问题只在于"构造期就调用了那些函数"。

**S3｜预热 62 帧 × ~233 ms/帧 ≈ 14 s 的间歇卡顿，而且门控让它可以永远做不完。**
- 位置：`app/ty_sim.py:253-297`（分帧驱动）、`:231-251`（4 个任务）、`app/smcy_icon.py:582-600/609-646/728-768`
- 证据（确定性）：4 + 16 + **20**（被 `_MAX_STREAMS=20` 截断；上限拿掉是 36）+ 22 = **62 帧**；实测 8 帧 **1861 ms**、8 帧后 `_warmup_done=False`
- 根因：**分帧粒度是"一个类别/一个 (cat,hemi)"**，而单个类别内部要解码一路 400×400 mp4 的首帧（`_VideoStream.get_frame`）；一次 `next(gen)` 就 200–300 ms，分帧救不了单个 yield 的粒度
- 建议修法：把单个 yield 内部再拆（例如按"打开流" / "解码首帧"两步各 yield 一次），或把预热改成"只在真正需要某类别时才预热该类别"（按需预热），并给出开关
- 预期收益：消除 ~14 s 的间歇停顿（**确定性帧数 → 时间换算**）
- 成本：高（要动 `smcy_icon` 的预载粒度与 LRU 交互）
- 风险：预热不足会让"首次切到某类别"的解码卡顿回到交互路径上（但那至少是用户主动触发的）

### 一般

**N1｜一行无用的模块级 import 把 OpenCV + Pillow 拖进启动路径。**
- 位置：`app/ty_sim.py:39` `from .particle_effect import preload_particles`；`app/particle_effect.py:8,10`（cv2、PIL）
- 证据（实测）：`preload_particles` **全仓零调用**（唯一使用者 `ty_sim.py:238` 用的是延迟 import 的 `iter_preload_particles`）；`cv2 in sys.modules = 1`；`app/smcy_icon.py:10` 也模块级 import cv2（但它在预热时才被 `ty_sim.py:237` 延迟 import，本身不是启动路径）
- 建议修法：删掉 `ty_sim.py:39`（或改为函数内 import）；`statistics/heatmap.py:8` 的 PIL 同理可延迟到开热力图时
- 预期收益：省 cv2 + PIL 的 import（数十到数百 ms 区间，未单独测）与相应常驻
- 成本：**极低**（1 行）
- 风险：极小（该名字无人使用）

**N2｜21 个对话框对象在启动期全部构造。**
- 位置：`app/dialog_manager.py:40-64`
- 证据（实测）：构造 `DialogManager()` = 121 ms（绑核）/520 ms（未绑核）；**21 个**含 `.active`+`.sim` 的对象
- 建议修法：`DialogManager.__getattr__` 懒建（首次访问时构造），保留 `_dialog_stack` / `any_active()` 语义
- 预期收益：−121～−520 ms 区间 + 少 21 份常驻对象与它们的预渲染文本
- 成本：中
- 风险：中-高 —— `event_mixin.py:56-102` 会遍历 `_dialog_stack` 并读 `.active`，`dialog_mgr.draw` 也要遍历；懒建必须覆盖所有访问点（漏一个就是"对话框打不开"）

**N3｜19 个字体实例（同一 19.6 MB TTF）散落在 8 个模块的模块级。**
- 位置：`app/constants/fonts.py:48-56,148-151`（6 个）+ `landfall_effect.py:10`、`monthly_summary.py:14-15`、`statistics/summary_list.py:22-25`、`statistics/dialog_chart.py:37`、`summary_effect.py:30-33`、`ty_sim_mixins/draw_info_boxes_mixin.py:24,29-30`
- 证据（实测）：`pygame.font.Font` **19 次不同 (文件, 字号)**；每次 0.3–1.0 ms；唯一字体文件 20,553,556 B
- 建议修法：把非 `constants/fonts.py` 的 `_load_font` 调用集中成一张"字号表"，共享 `f_l/f_m/f_s` 的缓存（`_load_font` 已有 `(file,size)` 去重，成本在"模块级副作用"而非重复解析）
- 预期收益：墙钟 ~6–13 ms（**小**）；主要收益是**模块导入副作用收敛**（少 13 处 import 期建对象）
- 成本：低
- 风险：低（字号语义要逐个核对，避免改观感）

### 建议

**A1｜`config.json` 启动期被读 2 次。**
- 位置：`main.py:16-19`（`_apply_dpi` 里 `json.load`）与 `main.py:48`（`AppConfig.load`）
- 证据（实测）：audit `.json` open 11 次（含 2 次 config.json）
- 建议：`_apply_dpi` 的结果并入 `AppConfig.load` 之后的初始化；或把 `disable_dpi_scaling` 缓存起来传下去
- 收益：<1 ms（**只是整洁性**）；成本：极低；风险：低（DPI 必须在 `pygame.init()` 之前生效）

**A2｜`map/land2.png`(43.9 MB) 与 `map/heightmap.png`(59.1 MB) 在启动路径上完全没被读。**
- 证据（实测）：audit 只看到 `map.png` + `land.png`；`heightmap.png` 在 app/ 下未被引用（未做全仓穷尽 grep）
- 建议：确认用途（绘画/地形？）后从发布包里剔除或改成按需下载 ⇒ 发布体积 −103 MB
- 收益：体积/首装体验；成本：低；风险：中（要确认没有隐藏调用点）

---

## 7. 值得保留的现状（**不要动**）

1. **`app → simulator` 的函数内延迟 import 是做对的**：`app/` 下**没有任何模块级 `import simulator`**（静态全仓确认），内核另外 32 处都在函数内（`sim_mode_mixin.py`）。它让"不进模拟模式的启动"不背 `simulator` 的 import。**本节 S2 要改的是"构造期调用"，不是这条设计。**
2. **分帧预热框架本身方向正确**：`_tick_warmup` 用 1200 ms deadline 让首帧先画出来（`ty_sim.py:293`），`_warmup_visuals` 每帧只走一步（`:282`），并且在播放/拖拽/对话框期间让路（`:261`）。S3 要改的是**粒度**，不是结构。
3. **`cache_store` 的字节预算 LRU 自带 hits/misses/evictions 计数**——本轮未用上，但它是"确定性指标"的现成来源（`app/cache_store.py`）。
4. **有界缓存无处不在**：`_font_cache`(64)、`_img_cache`、`_layer_surf_cache`、`_flow_cache`(4)、`_MAX_STREAMS=20`、`_MAX_SUMMARY_STREAMS=10`、`_geo_spline_cache`(64) —— 没有无界增长。
5. **`config_store` 的"备份 + 读回校验 + `RUNTIME_FIELDS` 不落盘"**（`config_store.py:91-132`、`:48-52`）——启动只读不写，行为正确。
6. **陆地掩码的"拖拽期延后重建"**：`_land_pending` / `_land_lazy` / `_LAND_REBUILD_DELAY_MS=180`（`map_mgr.py:536-556`）把非视觉的重建从拖拽路径上摘掉。
7. **`_warmup_jobs` 只在 `icon_set=='smcy'` 时才追加图标流与 Summary 流两个任务**（`ty_sim.py:245-250`）——按图标集裁剪，避免 simple 集付无谓成本。
8. **`_maximize_window` 的 ctypes 原型声明**（`main.py:56-84`）与 `window_util._own_window` 的按 pid 比对（`window_util.py` docstring）—— 修过的 64 位 HWND 截断问题，别再改回去。
9. **`_probe_startup_timing.py` 的 `--agg N` 用独立进程测离散度**（工具 docstring `:9-11`）—— 方法正确，本轮沿用了它；只是**别用单核绑定**（§5.2）。
10. **`matplotlib` / scipy / xarray / netCDF4 / pandas 都不在启动 import 路径上**（实测 `matplotlib in sys.modules = 0`）—— 派活里担心的"全套科学栈"并没有被启动拉起来。

---

## 8. unresolved（未能确证）

1. **逐逻辑核速度图噪声过大**（`G:\tmp\core_map.py`：core 10 = 490 ms ↔ core 16 = 1630 ms，同核两次也能差 40%），无法独立验证 Lead 的"P 核 0-15 / E 核 16-23、1.85×"归因。我能确证的只是：**绑单个逻辑核 8 比绑 0-7 慢 2 倍且更不稳**（交错实测）。需要一台空闲机器或更长的基准负载才能分离拓扑与争用。
2. **`update_map_image = update_view`（`map_mgr.py:613`）把两件事混在一个入口**，我的包装计时（1952 ms）没有拆分"`map_view.draw` 画地图"与"`_rebuild_land_and_overlay` 重建陆地"各占多少。需要在这两个函数上分别打点。
3. **`update_all_screen_points` 286 ms 里实际建了多少条 geo 样条**未计数（受 `typhoon_render.py:93-95` 的 `view_rect` 早退影响，实际 < 207）。
4. **首帧 `draw`（74.8–193 ms）的构成**未拆：`map` 层 + `ace_bar` + 非模拟模式的 `_draw_typhoons`（207 只台风重建路径面）各占多少未测。
5. **`map/land2.png` 与 `heightmap.png` 的真实用途**未确证（只确认启动期不读）。需要全仓穷尽 grep + 运行期 audit。
6. **`assets/SMCY/mainicon`（343.64 MB / 44 mp4）的时长与"预热 36 帧"的覆盖率**未逐类别核对；`_MAX_STREAMS=20` 是否会让常用类别被 LRU 挤掉也未测。
7. **`mixer.Sound` 的 20 次解码在总时长里的占比**只按单次 6–44 ms 估算（合计 200–300 ms），未单独做"关掉音效"的 A/B（会改行为，本轮只诊断）。
8. **SSD 冷缓存下的首次启动**未测：本轮所有运行都在 OS 文件缓存已热的状态下（`config.json` 4 KB 级、`map.png` 45.9 MB、mp4 首帧都可能冷读更慢）。
9. **`_init_sim_mode` 675 ms 里"内核 import"与"`latest_real_start` 读 npz"各占多少**未拆分（只知总量与 2 次 npz open）。
10. **真实窗口（非 dummy SDL）下的启动耗时**未测：`set_mode`、`set_icon`、`flip()` 在真机上还要加交换链/DPI 成本，本文所有启动时间都偏乐观。

---

## 9. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 启动顺序 / 每个阶段 | `main.py:12-217`（本报告 §2.1 有逐行对账） |
| 启动最大的一块 | `app/ty_sim.py:165-229`（`TySim.__init__`） |
| 底图解码（222 MB） | `app/map_mgr.py:16-41`（`MapView.__init__`） |
| 陆地掩码（222 MB + 55.6 MB） | `app/map_mgr.py:466-481`、`:483-534`、`:556-574`、`:586-604`；构造期强制入口 `app/ty_sim.py:209-211` |
| 台风全量加载 | `app/data_repo.py:130-175` |
| 字体（19 个字号） | `app/constants/fonts.py:18-56,148-151` + §6 N3 列的 8 个模块 |
| 音效（20 个） | `app/resource_mgr.py:110-128` |
| 图标集（smcy 下是程序化生成） | `app/resource_mgr.py:72-108`、`:129-158` |
| 21 个对话框急切构造 | `app/dialog_manager.py:40-64` |
| 预热（62 帧） | `app/ty_sim.py:231-297`；生成器 `app/smcy_icon.py:582-600/609-646/728-768`、`app/particle_effect.py:58-67` |
| 内核被启动期 import | `app/ty_sim_mixins/sim_mode_mixin.py:213-216/248-253/471-474`（经 `app/ty_sim.py:176`） |
| cv2/PIL 进启动路径的**唯一**原因 | `app/ty_sim.py:39` + `app/particle_effect.py:8,10` |
| 首帧重投影 + 样条 | `app/ty_sim.py:205` → `app/view_state.py:79-95` → `app/typhoon_render.py:56-112` |
| 测法（独立进程） | `tools/_probe_startup_timing.py`（`--agg N`，docstring `:9-11`） |
| 本轮探针 | `G:\tmp\probe_startup.py`（分段+ctor 子步骤+确定性计数+audit）、`probe_warmup.py`（预热帧数/内存/alpha 表/CPU-墙钟）、`probe_iconyield.py`（真 manager 数 yield）、`agg_interleave.py`（交错绑核）、`agg_affinity.py`（顺序两臂）、`core_map.py`（逐核速度）、`closure.py`（第一方 import 闭包） |
| 探针 vs 真机路径的**漏测清单** | 本报告 §2.3 的对账表（a–j 十项，其中 (b) 阶段归属、(h) 207 次 `flip` 最关键） |
