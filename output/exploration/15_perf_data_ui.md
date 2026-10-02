# 15 数据层 / UI 对话框 / 统计图表 / 绘画模式 / 内存常驻 —— 性能诊断

> 探索者：`app-feat`（task-15）　模式：**只诊断、不改代码**（perf-optimize 技能 E 模式）
> 真实运行时：`C:\Users\Yukioto\AppData\Local\Python\bin\python.exe`（3.14.2 / pygame-ce 2.5.7）
> 探针脚本：`G:\tmp\perf\`（工作区外，未改动工程内任何文件）
> 报告区分 **实测**（本机跑出的计数/字节/RSS）与 **推导**（按代码算出的量），计时只给区间。

## 一句话定位

这一层管"数据怎么进来、界面怎么建、图表怎么算、路径图怎么画"；诊断结论是：**数据层几乎不吃性能（全量解析 0.4 s / +7.6 MiB），真正的重量在"每个 TySim 实例常驻 646 MiB"和"打开对话框每帧重新光栅化文字"这两件事上**。

## 1. 清单与职责（本次实际测量的对象）

| 文件 / 模块 | 行数 | 本次测到的量 |
|---|---|---|
| `app/data_repo.py` | 498 | 207 文件解析；扫描去重规则；`apply_basin_filter` |
| `app/typhoon_data.py` | 226 | 每报点 ACE/时间插值（被 `recalc_ace` 调用） |
| `app/typhoon.py` | 220 | **每个台风自带一个 12 MiB SurfaceCache** |
| `app/ace_engine.py` | 388 | 时间轴/每日/活跃期/刷新；每点调用计数 |
| `app/statistics/data_builder_chart.py` | 123 | 建一次图表 = 6,300+5,829+3,612 次每点调用 |
| `app/statistics/season_stats.py` | 227 | 单年 2,704 `_parse_time` + 1,873 `_ace_eligible` |
| `app/statistics/heatmap.py` | 439 | 径向核逐点 bbox（复杂度见 §6 G5） |
| `app/statistics/*.py` 其余 | ~4,000 | 每帧字体渲染调用点 |
| `app/paint_render.py` | 591 | 一次渲染 15,933 图元（207 台风） |
| `app/paint_dialog.py` | 969 | 稳态每帧 67–121 次未缓存字体渲染 |
| `app/dialog_base.py` / `dialog_manager.py` | 23 个 fan-in / 22 个 fan-out | 构造期一次性建 21–22 个对话框 |
| `app/cache_store.py` | 118 | 共享缓存上限 32+96+48 MiB |
| `app/constants/fonts.py` | — | `rt()` 默认路径**不缓存** |
| `tests/test_paint.py` | 199 | 69.61 s 的真实构成（§3.1） |

## 2. 数据流 / 调用链（附本层实测成本）

```
① TySim.__init__                                   ty_sim.py:165-229
   ├─ _init_resource_managers()                    图标/音效（ResourceManager 是单例，只加载一次）
   ├─ repo.load_typhoon_files()                     ty_sim.py:194  → 207 文件 / 8491 报点
   │     └─ parse_typhoon_file ×207                 实测 0.38–0.41 s，常驻 +7.6 MiB
   ├─ map_mgr.update_map_image()                    → MapView.__init__ 载入 map.png
   │                                                10800×5400 → 222.5 MiB（全分辨率常驻）
   ├─ update_all_screen_points() / update_land_mask()
   ├─ _ensure_land_mask()                           载入 land.png 222.5 MiB + 建 _land_geo_alpha 55.6 MiB
   └─ DialogManager(self)                           一次性构造 21–22 个对话框对象
   ⇒ 实测单实例 RSS = 646.2 MiB（§3.2）

② ACE 刷新（配置变更 / 重载）
   settings.py:2518  sim._apply_basin_filter()      ← 每次保存设置都调用（无守卫）
     └─ DataRepository.apply_basin_filter()         data_repo.py:352-405
          ├─ 过滤 + _sort_by_basin()
          ├─ _refresh_ace() → ACEEngine.refresh_all()   ace_engine.py:324-388
          └─ _fill_point_categories()              8491 次类别推断
   ⇒ 实测 point_in_limit=25,473  _parse_dt=16,982  时间轴重建=6  台风榜=6（§3.4）

③ 图表（切年份 / 打开 ACE 图）
   ACEChartDialog.draw → needs_update → _rebuild      dialog_chart.py:247-250
     └─ build_chart_data(sim, year, ...)             data_builder_chart.py:31-102
          ├─ engine.daily_ace(year)                  → 每点扫一遍（2107）
          ├─ engine.daily_activity_count(year)       → 2107 _parse_dt + 2107 _ace_eligible
          └─ engine.active_periods(year)             → 3722 _ace_eligible
   ⇒ 实测 6,300 point_in_limit / 5,829 _ace_eligible / 3,612 _parse_dt（§3.5）
   ⇒ 实测 **不是每帧重建**：`chartdata` 在稳态帧 = 0（§3.8）

④ 绘画模式
   PaintDialog._do_render()                          paint_dialog.py:627-660（仅按钮触发，非每帧）
     └─ TrackMapRenderer.render                     paint_render.py:201-238
          ├─ _compute_point_density()               逐点投影（8491 次）
          ├─ _draw_tracks → pygame.draw.line/circle  实测 15,933 图元
          └─ PNG 编码                                实测 81.1 KiB（全局图，1440×720）
   PaintDialog.draw() 每帧                           实测 rt=67（无预览）/121（有预览）
   _refresh_stats_now() 每次勾选                     实测 8,285 次 haversine（§6 G2）

⑤ 内存聚合
   每个 Typhoon 自带 SurfaceCache("rot", 12 MiB, 720)  typhoon.py:103
     ⇒ 207 实例 × 12 MiB = **2,484 MiB 聚合上限**；max_items 聚合 149,040 个 Surface（§3.2）
```

## 3. 核心机制详解

### 3.1 先说结论：69.61 s 里绘画渲染只占 4.7%

第一轮我把 `tests/test_paint.py` 的 69.61 s 记为"绘画模式慢的硬信号"。本轮用 `--durations` 拆开，**这个推断被证伪**：

| 测试类 | 用例数 | 实测耗时 | 内容 |
|---|---|---|---|
| `TestPaintDialog` | 6 | **38.71 s** | 每个用例各 `_make_sim()` → 一次完整 `TySim(...)` 构造 |
| `TestTrackMapRenderer` | 6 | **3.24 s** | 真正的路径图渲染（含 1 次载入 map.png = 2.31 s） |
| `TestColorScheme` | 3 | ≈0 s | 纯常量断言 |

- `TestPaintDialog` 6 个用例分别 7.38 / 7.32 / 6.52 / 6.26 / 5.62 / 5.10 s（`--durations=10` 实测），而每个用例的业务动作只有 `activate()` / 点一下按钮 / `deactivate()`。它们的时间**就是** `TySim()` 构造的时间。
- `TestTrackMapRenderer` 里纯渲染用例 **0.01–0.02 s**；唯一 2.31 s 的是 `test_render_global_with_map`，它花在 `pygame.image.load("map/map.png")`（10800×5400）。
- 单次渲染的确定性工作量：4 台风 / 60 报点 → 66 条线 + 57 个点；**207 台风 / 8491 报点 → 15,933 个图元 + 2 个 Surface(4.1 MiB) + PNG 81.1 KiB**（`PaintDialog._do_render` 实测）。

⇒ 两次运行合计 38.71+3.24 = 41.95 s，与第一轮的 69.61 s 差 ~28 s；这段时间差**不应归因于代码**，而属于本轮 §5 记录的机器噪声（Lead 实测 A/A 极差 4.6% 已是绑核后的最好情况，未绑核 325%）。

### 3.2 内存：单实例 646 MiB，其中 445 MiB 是两张全分辨率位图

实测（`G:\tmp\perf\probe_mem_stages.py` / `probe_tysim.py`，psutil RSS）：

| 阶段 | RSS | 增量 |
|---|---|---|
| 进程启动 | 25.1 MiB | — |
| SDL dummy 显示 | 33.8 MiB | +8.7 |
| **解析全部 207 个台风文件** | **41.4 MiB** | **+7.6 MiB** |
| 载入 `map/map.png`（10800×5400） | 269.7 MiB | **+236.3 MiB**（理论 222.5） |
| 载入 `map/land.png`（10800×5400） | 492.2 MiB | **+222.5 MiB** |
| 完整 `TySim(...)` 构造完成 | **646.2 MiB** | +609 MiB（相对 36.5 基线） |

关键比例：**整个台风数据集只占 7.6 MiB（1.2%），两张地图位图占 445 MiB（73%）**。逐项核对（`probe_gc.py` 实测对象尺寸）：

```
map_mgr.map_view.original_img   10800×5400 = 222.5 MiB   ← MapView.__init__ map_mgr.py:17
map_mgr._land_orig              10800×5400 = 222.5 MiB   ← map_mgr.py:473 convert_alpha
map_mgr._land_geo_alpha         bytes 10800×5400 = 55.6 MiB
map_mgr._land_alpha_bytes       bytes = 0.4 MiB
map_mgr.land_img / _cached_map_render  800×500 = 1.5 MiB each
```

回收行为（实测 `probe_gc2.py`，在**函数作用域**内构造以排除探针自身持引用）：构造后 645.8 MiB → 返回作用域 + `gc.collect()` 后 **131.5 MiB**，存活 `TySim` 实例数 = **0**。⇒ **没有泄漏**；但**约 70 MiB 不归还 OS**（分配器/堆行为），且单实例峰值 646 MiB 在"可用常年 1–3 GiB"的机器上已占 21–63%。

**聚合缓存上限**（实测 `probe_cache_ceiling.py`）：每个台风对象自带一个 SurfaceCache（`typhoon.py:103`，`SurfaceCache("rot", 12*1024*1024, 720)`）。207 个台风 ⇒ 字节上限 **2,484 MiB**、条目上限 **149,040** —— **单这一项的上限就超过本机可用内存**。共享（类级）缓存则是固定上限：`_name_shadow_cache` 32 MiB / `_purple_frame_cache` 96 MiB / `summary_list._purple_frame_cache` 48 MiB。

### 3.3 字体渲染：`rt()` 的缓存被默认调用形式绕过

`app/constants/fonts.py:161-173`：

```python
def rt(f, text, color, max_width=None, smooth=True):
    if max_width is None or max_width <= 0:
        return f.render(text, smooth, color)     # ← 直接光栅化，不进缓存
    ...                                          # 只有给了 max_width 才走 OrderedDict 缓存(上限1024)
```

即：**换行渲染有缓存，单行渲染没有。** 而绝大多数调用点写的是 `rt(f_s, "文本", color)`（三参）。

静态统计（AST 扫本层全部文件的 `rt(...)` 调用点）：

| 文件 | 无 `max_width`（不缓存） | 带 `max_width`（缓存） | 其中位于 `draw*` 函数内 |
|---|---|---|---|
| `app/paint_dialog.py` | 21 | 0 | 16 |
| `app/ty_list.py` | 20 | 4 | 10 |
| `app/statistics/dialog_chart.py` | 18 | 1 | 9 |
| `app/script_dialog.py` | 18 | 1 | 8 |
| `app/point_list.py` | 17 | 0 | 4 |
| `app/statistics/chart_presets.py` | 15 | 0 | 15 |
| `app/name_pos_dialog.py` | 14 | 0 | 12 |
| `app/statistics/intensity_chart.py` | 12 | 0 | 0 |
| ……（其余 20 个文件） | 14 | 0 | 2 |
| **合计（本层全部文件）** | **220** | **7** | **113** |

**实测每帧暴露量**（`probe_dialog_frame.py`，绑核 P 核，3 帧稳态均值）：

| 界面 | 稳态每帧 `rt()`（未缓存光栅化） | 每帧图元 |
|---|---|---|
| `PaintDialog`（无预览结果） | **67** | 63 |
| `PaintDialog`（有预览结果） | **121** | — |
| `ace_chart`（ACE 统计主图） | 24 | 14 |
| `intensity_chart` | **0**（整图缓存在 Surface 里，打开时 rt=36 / 11 个 Surface / 3.79 MiB） | 1 |
| `multi_year` / `season_stats` / `path_comparison` / `heatmap` / `summary_list` | 0–3 | 1–5 |

⇒ 同一个代码库里，`intensity_chart`（打开时一次性画进 Surface，之后只 blit）是**做对的样板**；`PaintDialog` 是最坏的反例（静态面板每帧重画 67 次文字）。

### 3.4 配置保存：无条件全量重过滤 + 全量 ACE 刷新

`app/settings.py:2518` 在设置保存流程里**无条件**调用 `self.sim._apply_basin_filter()`（同一函数在 `:1349-1352` 有一条"字段没变就提前 return"的守卫，但保存路径这条没有）。它链式触发：

```
apply_basin_filter()                        data_repo.py:352-405
  → _sort_by_basin()                        207 次排序键
  → _refresh_ace() → refresh_all()          ace_engine.py:324-388（全量，无 affected 参数）
  → _fill_point_categories()                8491 次类别推断
```

**实测每点调用计数**（`probe_refresh_work.py`，monkeypatch 计数；207 台风 / 8491 报点 / 6 个 ACE 年）：

| 调用 | `point_in_limit` | `_parse_dt` | 时间轴重建 | 台风榜重建 |
|---|---|---|---|---|
| `ACEEngine.refresh_all()` 全量 | **25,473** | **16,982** | 6 | 6 |
| `DataRepository.apply_basin_filter()` | **25,473** | **16,982** | 6 | 6 |
| `refresh_all(affected=[1 个台风])` 局部 | 3,593 | 1,808 | 1 | 1 |

- 全量刷新 = 25.5k 次每点地理判定 + 17.0k 次 `strptime`（`_parse_dt` 有 `lru_cache(65536)`，但年份过滤/建轴仍是全点扫）。
- **局部刷新并不"局部"**：`refresh_all(affected)` 只省掉未受影响台风的年 ACE 分桶，但**受影响年份的时间轴与台风榜仍是整年全量重建**（3,593 次判定 / 1,808 次解析 —— 远超单个台风自己的 ~100 个报点）。
- 若同时命中 ACE 相关改动，`recalc_all_ace()`（`data_repo.py:444-447`）还会再来一遍 `207 × recalc_ace` + 一次全量 `refresh_all`，即一次保存 ≈ **5 万次以上每点调用**。

### 3.5 图表构建：一次单年图表 = 15,741 次每点调用

`build_chart_data` 实测（同一个 fake-sim，接真实 207 台风数据）：

```
build_chart_data(1 年) → point_in_limit=6,300  _ace_eligible=5,829  _parse_dt=3,612
产出：曲线点 1,460 / 每日 365 / 活动数 365 / 活跃期 36 / 台风榜 36
```

组成（按代码 + 计数对齐）：

| 子调用 | 每点成本 |
|---|---|
| `engine.daily_ace(year)` | 该年 2,107 个报点各一次 `_parse_dt`（`ace_engine.py:173-192`） |
| `engine.daily_activity_count(year)` | 2,107 `_parse_dt` + 2,107 `_ace_eligible`（`:194-219`） |
| `engine.active_periods(year)` | 3,722 `_ace_eligible`（`:233-297`，含两轮 strict/non-strict 判定） |
| `typhoon_ace_list(year)` | 该年全部报点各一次 `point_in_limit` + 求和 |

对照：`build_timeline_cache(1 年)` 只需 2,107 次 `_parse_dt` 产出 1,460 个时间轴点。**时间轴是预算好并缓存的（`sim._ace_timeline_cache`），而"每日/活动数/活跃期"每次都要重新扫全年报点。**

多年度对话框（`multi_year_chart.py:46-117`）的洋区分桶只扫当前年（实测 2,107 个报点），且曲线复用 `_ace_timeline_cache`（只需最近 8 年）——这部分是**做对的**。

### 3.6 数据层：没有 O(n²)，也没有重复全量重扫（除非显式重载）

- 解析成本线性：207 文件 → 207 次 `parse_typhoon_file`，报点 8,491，实测 0.38–0.41 s、**+7.6 MiB**。
- **去重规则有效**：`data_repo.py:138-150` 跳过与 `*_ty.txt` 同名的原始 JTWC 件。实测对照：不走该规则 walk 出 **280 个文件 / 11,918 报点**，走规则后 **207 / 8,491** ⇒ 省掉 **73 个重复文件 / 3,427 个重复报点（29%）**。
- `_ace_eligible` 的三处口径一致（`typhoon_data.py:110`、`ace_engine.py:29-38`、`season_stats.py:11-20`），没有出现"同一份数据两套判据各自扫一遍"。
- 时间/ACE 缓存命中率高：`_ace_timeline_cache`、`_ace_typhoon_cache`、`ty._ace_ty_year`、`ty._ace_year_events`、`_cached_peak_info`、`ty_list._row_surfs`。
- 唯一"整表重扫"点：`apply_basin_filter`/`reload_typhoons`（§3.4），但它由显式用户动作触发，不是每帧。

### 3.7 绘图的确定性工作量（每次点击"开始绘画"）

| 场景 | 输入 | 输出 | 图元总数 | Surface 新建 | PNG |
|---|---|---|---|---|---|
| 与 `test_render_returns_surface_and_stats` 同参 | 4 台风 / 60 报点 | 958×500 | 133（66 线+57 点+6 矩形+4 三角） | 3 个 / 3.8 MiB | 7.1 KiB |
| 全部台风 global（`min_w=1280,min_h=720`） | 280 台风 / 11,918 报点 | 1440×720 | **23,619** | 3 个 / 8.0 MiB | 81.1 KiB |
| `PaintDialog` 实际全场（一键全部 + global） | 207 台风 / 8,491 报点 | — | **15,933** | 2 个 / 4.1 MiB | — |

关键点：**整幅路径图只有 2–3 个 Surface 分配、PNG 只有 81 KiB**。渲染路径没有"每点建一个 Surface"或"每点做一次 smoothscale"这类反模式（`smoothscale`/`scale` 计数 = 0，因为地图未载入；载入地图时也只有 1 次裁剪缩放）。所以绘画模式的**算法侧是健康的**，问题在它所在的进程底座（§3.1/3.2）。

## 4. 输入输出资产

| 路径 | 内容 | 体量 | 说明 |
|---|---|---|---|
| `typhoon/**/*.txt|*.dat` | b-deck 数据 | 207 文件 / 5.5 MB / 8,491 报点 | 解析 +7.6 MiB、0.4 s |
| `map/map.png` | 底图 | 48.2 MB PNG → **10800×5400 = 222.5 MiB 常驻** | 每实例一份 |
| `map/land.png` / `land2.png` | 陆地掩码 | 45.9 MB PNG → **222.5 MiB 常驻** | 每实例一份 |
| `map/heightmap.png` | 高度图 | 61.9 MB PNG（I;16） | 属渲染分区（#14），本报告只记体量 |
| `assets/SMCY/resource/ace-heat.png` | 热力图色表 LUT | 单文件 | 加载一次并缓存 |
| `config.json` / `.bak` | 配置 | 3.7 KB | 每次保存触发 §3.4 |
| `G:\tmp\perf\probe_*.py` | 本报告 6 个探针 | — | 可复现命令见 §5 |

## 5. 如何运行 / 如何验证（以及"我用了什么测法、允许下多强的结论"）

**探针（全部在 `G:\tmp\perf\`，逐个单进程跑，未并发）**

```
cd /mnt/g/气象/Typhoon-sim
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_mem_stages.py'    # 阶段内存归因
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_tysim.py'         # 一次 TySim 构造：计数+内存
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_gc2.py'           # 回收行为（函数作用域）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_paint_work.py'    # 绘画图元/PNG 字节
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_stats_work.py'    # ACE/季节统计每点计数
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_refresh_work.py'  # refresh_all / apply_basin_filter / 建图
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_dialog_frame.py'  # 对话框打开 vs 每帧（绑核）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe 'G:\tmp\perf\probe_cache_ceiling.py' # 缓存聚合上限
# 测试拆分（两次独立运行，避免同时两个重进程）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe -m pytest "tests/test_paint.py::TestTrackMapRenderer" -q --no-header --durations=10
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe -m pytest "tests/test_paint.py::TestPaintDialog"      -q --no-header --durations=10
```

（本机 bash 的 `$VAR` 不展开，故上面全部写绝对路径；中文输出需 `| iconv -f GBK -t UTF-8`。）

**测法与本机允许的结论强度**

1. **主证据是确定性计数**，与机器状态无关：函数调用次数（monkeypatch 计数）、缓存上限字节、RSS 字节、图元数、PNG 字节、复杂度量化。这些数字可以直接当作事实。
2. **计时只作区间**，且绑核后才采信相对关系。Lead 的绑核结论（P 核 = 逻辑 0–15，P ≈232 ms/步 vs E ≈380–436 ms/步，1.85 倍差）已采用；`probe_dialog_frame.py` 绑到逻辑核 8。
3. **本机不允许的结论**：任何单一确定加速比。证据：同一次 TySim 构造，未绑核单样本 5.00 s、绑核单样本 7.30 s（**+46%**）；`--durations` 里同一构造的 6 次实例落在 5.10–7.38 s（**1.45 倍跨度**）。所以 §3.1 的结论只用**计数**（6 次构造 / 6 个用例 / 3.24 s 渲染）而不引用 69.61 s 的绝对值。
4. **未做的事**：没有启动 `python main.py`；没有跑全量 pytest；没有测真实 2560×1540 窗口下的几何（探针用 800×600 dummy，只影响 Surface 像素数、不影响调用计数）。

## 6. 观察到的问题 / 风险 / 不一致

分级：**阻塞 / 严重 / 一般 / 建议**。

---

### 阻塞

**P1 每个台风自带 12 MiB 表面缓存 ⇒ 聚合上限 2,484 MiB > 本机可用内存**
- 位置：`app/typhoon.py:103`（`self._img_cache = SurfaceCache("rot", 12*1024*1024, 720)`，位于 `TyphoonView.__init__`，**每实例一份**）
- 证据（实测）：`probe_cache_ceiling.py` → 207 个台风实例，每个 `max_bytes=12 MiB / max_items=720` ⇒ **聚合上限 2,484 MiB、149,040 条目**；对照 `probe_mem_stages.py` 实测系统可用常年 1–3 GiB。
- 根因：缓存预算写成了"每实例"的量级，但实例数由数据文件数决定（207 且可增长）；`SurfaceCache` 只保证**单实例**不超预算，没有任何全局总量约束。
- 建议修法：把"旋转图标"这类**跨台风可共享**的缓存提到类级/全局（键里含台风无关的 (angle, size, cat, scheme)），或给全部 `_img_cache` 加一个全局预算管理器（总量封顶 + LRU 跨实例淘汰）。
- 预期收益：把最坏情况从 2.4 GiB 压到几十 MiB；消除一条真实 OOM 路径。
- 成本：中（需要改缓存键语义 + 一处全局注册）；风险：中（图标旋转结果若依赖台风专属配色，需把配色并入键）。
- 可达性：**推断**（需要台风被渲染到才填充；本报告只确证了上限，未构造出打满的场景）。

---

### 严重

**P2 两张 10800×5400 位图全分辨率常驻，每实例 445 MiB**
- 位置：`app/map_mgr.py:17`（`MapView.__init__`: `pygame.image.load(...).convert()` 保留原图）、`app/map_mgr.py:473`（`_land_orig = ...convert_alpha()`）、`app/map_mgr.py:435-443`（`_land_geo_alpha` 等字节表）
- 证据（实测）：`map.png` 222.5 MiB + `land.png` 222.5 MiB + `_land_geo_alpha` 55.6 MiB；完整 `TySim` RSS **646.2 MiB**，其中台风数据只占 7.6 MiB。`MapManager` **不是单例**（`map_mgr.py:426-427`），两个实例 = 1.29 GiB。
- 根因：为了任意缩放质量把原图整幅留在内存；而本机 `scale` 最大 8×、屏幕只有 2560×1540，实际可见的最高需求远低于 10800 宽。
- 建议修法：改为分块（tile）或金字塔（mipmap）加载；或按当前 `scale` 动态决定保留哪一级（`MapView` 已有 `img_w/img_h` 与 `scale`，加一级 1/2、1/4 降采样即可）；`_land_orig` 与 `land.png` 可只在建 mask 时用一次，建完释放原图。
- 预期收益：单实例常驻从 646 MiB 降到 ~200 MiB 量级（估算，需实现后实测）。
- 成本：中高（地图绘制/陆地判定/缝处理都要跟着改）；风险：高（`03_app_sim.md` 记录的跨 0°/180° 缝合逻辑对原图尺寸敏感）。
- 注：地图/渲染的进一步优化属分区 #14（sim-numerics），此处只报"每实例常驻"这一与内存约束直接冲突的结论。

**P3 一次 `TySim()` 构造 = 207 次文件解析 + 2 次 10800×5400 PNG 解码 + 全量 ACE 刷新**
- 位置：`app/ty_sim.py:165-229`（构造链），`ty_sim.py:194`（解析）、`:195`（地图）、`:206-211`（陆地掩码）
- 证据（实测）：计数 `parse_typhoon_file=207`、`pygame.image.load=2`、`pygame.transform.scale=2`、`Surface=148`；耗时区间 5.00 s（未绑核单样本）/ 7.30 s（绑核单样本）；`tests/test_paint.py` 6 个用例各一次 → `TestPaintDialog` 38.71 s。
- 根因：构造器把"数据加载 + 地图解码 + 掩码构建 + ACE 全量刷新"全做在一条路径上，且没有任何跨实例复用（`MapManager` 非单例、`DataRepository` 每次新建）。
- 建议修法：（a）测试侧改用 `setUpClass` 共享一个 `TySim`（只改测试，不改产品）；（b）产品侧把"地图/掩码"做成进程级单例缓存，构造时按 `screen` 尺寸复用。
- 预期收益：测试 38.71 s → 约 6–7 s（-82%，按计数推导）；产品多开/重建场景同理。
- 成本：低（测试）+ 中（产品）；风险：低。

**P4 `rt()` 单行渲染不缓存，220 个调用点每帧重新光栅化**
- 位置：`app/constants/fonts.py:161-163`（`max_width is None` 直接 `f.render`）；调用点分布见 §3.3
- 证据（实测）：静态 220 处无 `max_width`（仅 7 处走缓存）；`probe_dialog_frame.py` 稳态每帧 `rt()`：`PaintDialog` **67**（无预览）/ **121**（有预览），`ace_chart` 24。
- 根因：缓存机制存在但入口条件是"必须传 max_width"；于是只有需要换行的调用受益，最常见的单行标签全部绕过。SDL_ttf 单次 `render` 会新建 Surface（像素填充 + 分配）。
- 建议修法：（1）把 `rt` 的缓存键补上 `max_width=None` 分支，做成 `lru_cache`/OrderedDict（文本+颜色+字体 id，上限已存在 1024）；（2）顺带把 `PaintDialog` 的静态左栏/字段标签预渲染为一张 Surface（`monthly_summary._build_surface`、`intensity_chart._build` 已是样板）。
- 预期收益：按计数推导，`PaintDialog` 稳态每帧 67→接近 0 次光栅化；全库 220 个调用点受益。
- 成本：低（改 `rt` 一处，全局生效）；风险：低（缓存键含颜色与 `id(f)`，字体对象是常驻单例）。

**P5 设置保存无条件全量重过滤 + 全量 ACE 刷新**
- 位置：`app/settings.py:2518`（无守卫调用 `_apply_basin_filter()`）；对照 `settings.py:1349-1352`（有"字段未变则 return"守卫）；`data_repo.py:352-405`；`ace_engine.py:324-388`
- 证据（实测）：`apply_basin_filter()` = `point_in_limit 25,473` + `_parse_dt 16,982` + 时间轴重建 6 + 台风榜 6；若 ACE 相关再叠加 `recalc_all_ace()`（`207 × recalc_ace` + 又一次全量刷新）。
- 根因：保存路径没有把"这次到底改了什么"传下去；`apply_basin_filter` 内部也无法判断过滤条件是否变化（只看当前值）。
- 建议修法：保存前记录 `(basin_filter_enabled, ace_limit_mode, ace_limit_basin)` 旧值，未变则跳过；变了再区分"只需重过滤"（不重建时间轴）与"需重算 ACE"两档。
- 预期收益：无变化的保存从 ~4.2 万次每点调用降到 0（按计数推导）。
- 成本：低；风险：低（需要在 `_apply_filter_now` 那条"即时应用"路径上保持一致，否则会漏刷新——`settings.py:1363-1372` 已做过一次去重，说明这个坑踩过）。

---

### 一般

**P6 建一次单年 ACE 图表要 15,741 次每点调用，且不随缓存复用**
- 位置：`app/statistics/data_builder_chart.py:31-102`；`ace_engine.py:173-192`（daily_ace）、`:194-219`（daily_activity_count）、`:233-297`（active_periods）、`:221-231`（typhoon_ace_list）
- 证据（实测）：`point_in_limit=6,300`、`_ace_eligible=5,829`、`_parse_dt=3,612`（207 台风 / 6 个 ACE 年，单年）。
- 根因：单年数据被 4 个函数各扫一遍，且只有"时间轴"和"台风榜"进了缓存；`daily_activity_count` 与 `active_periods` 的 `_ace_eligible(strict=True)` 判定完全重复。
- 建议修法：建年缓存 `{year: {'daily': [...], 'activity': [...], 'periods': [...]}}`，随 `_ace_data_revision` 失效（`ace_engine.py:334` 已经在维护这个 revision）。
- 预期收益：切年份/重开图表从 1.57 万次降到 0（首次仍需一次）。
- 成本：低；风险：低。

**P7 绘画对话框每次勾选/文件动作重算全部选中台风的统计（8,285 次 haversine）**
- 位置：`app/paint_dialog.py:309-312`（`_refresh_stats_now`）；调用点 `:199, :480, :517, :523, :532, :535, :541, :544, :575`（9 处）
- 证据（实测）：`compute_track_stats(repo.tys)` 一次 = **8,285 次 `haversine_km`**（207 台风 / 8,491 报点）。
- 根因：统计对"选中集合"整体重算，而勾选只改变单个文件；也没有按台风缓存。
- 建议修法：`compute_track_stats` 按台风对象缓存（键 `id(ty.pts), len(ty.pts)`），勾选只重算新选中的。
- 预期收益：单次勾选 8,285 → 约 40 次（单条路径）；一键全部仍 8,285（一次可接受）。
- 成本：低；风险：低。

**P8 `refresh_all(affected)` 的"局部"只覆盖分桶，不覆盖时间轴/台风榜**
- 位置：`app/ace_engine.py:324-388`（`:345-366` 分桶复用，`:376-386` 按年重建时间轴/榜单）
- 证据（实测）：`refresh_all(affected=[1 个台风])` 仍产生 `point_in_limit=3,593` / `_parse_dt=1,808` / 时间轴重建 1 / 台风榜重建 1 —— 而单个台风只有约 100 个报点。
- 根因：时间轴与台风榜的缓存粒度是"整年"，只要该年受影响就整年重建。
- 建议修法：给 `build_timeline_cache` 加"按台风增量修改"的能力（当前 `_ace_year_events` 已有按台风的排序事件+前缀和缓存，可据此增量更新年份时间轴，而不必重扫全部报点）。
- 预期收益：点编辑（拖点）场景下从 3,593 次判定降到约 100 次量级。
- 成本：中（需要证明增量与全量数值一致）；风险：中高（ACE 数值口径已被历轮修补过，改动容易引入回归——`ace_engine.py` 里 K9/B1/R-4 等注释显示这块踩坑很多）。

**P9 `data_builder_chart` 缓存缺失时 ACE 曲线为空且不回填**
- 位置：`app/statistics/data_builder_chart.py:43-53`
- 证据（代码）：`cached = timeline_cache.get(year)`；只有 `cached is not None` 才赋 `ace_curve_points`，**没有 else 分支调用 `engine.build_timeline_cache(year)`**。
- 根因：把"缓存必须已由 `refresh_all` 预热"当成隐含前提（`ace_engine.py:376-386` 只在 `ace > 0` 时建）。
- 影响：一旦缓存被清理/年份新增/ACE 归零后回升，图表显示空白而不是即时构建（第一轮 m5 已记录）。
- 建议修法：补 `else` 分支按需构建（并复用已有的 `_ace_timeline_cache` 失效规则）。
- 预期收益：正确性 > 性能（避免用户看到空白图再反复切年份触发重建）。
- 成本：极低；风险：低。

**P10 热力图径向核逐点构造包围盒数组（复杂度量化）**
- 位置：`app/statistics/heatmap.py:177-224`
- 证据（推导，代码量级）：`radius_px = min(2.0/(Mlat-mlat)*bh, 120)`，每点包围盒 `(2*int(radius_px)+3)²` 个元素。以 `Mlat-mlat=60°`、`bh=1500 px` 计 → `radius_px=50`、`r_int=51` ⇒ 每点 **10,609** 个元素；当前年 2,107 个 ACE 报点 ⇒ 约 **2.2×10⁷** 元素被 `d2/mask/sqrt/add.at` 处理一遍，且每点都新分配 `(ny,nx)` 浮点数组。半径封顶 120 时单点可达 **58,081** 个元素。
- 根因：核为线性衰减（`w = pace*(1-d/r)`）的散点累积，用逐点 bbox + `np.add.at` 实现，未做卷积/降采样。
- 建议修法：把核放到低分辨率网格（如 1/4 采样）后再上采样着色；或用 `scipy.signal.convolve`/`fftconvolve` 一次完成；或按 `pace` 分箱批量累积。
- 预期收益：元素处理量降 16 倍（1/4 降采样）量级；内存峰值从"每点一个 bbox 数组"变为"一次卷积的工作缓冲"。
- 成本：中；风险：中（色阶视觉会变，需对照 `ace-heat.png` 图例验收）。

---

### 建议

**P11 测试设计：`tests/` 57 个测试文件里，凡是 `_make_sim()` 的用例都在重复付 646 MiB / 5–7 s**
- 位置：`tests/test_paint.py:127-130`（`_make_sim`）、6 个用例各调一次
- 证据（实测）：`TestPaintDialog` 38.71 s / 6 用例，每用例 5.10–7.38 s，业务动作仅 activate+点击。
- 建议：`setUpClass` 建一次并在用例间复用（注意 `conftest.py` 的 config 隔离夹具已经存在，可沿用其思路）；重控件用例按需 `reload`。
- 预期收益：`test_paint.py` 从 ~42 s（本次实测两次运行之和）降到 ~8 s 量级。
- 成本：极低；风险：低（需注意用例间的状态污染，故建议只读用例共用）。

**P12 `PaintDialog` 静态面板可整幅预渲染**
- 位置：`app/paint_dialog.py:689-719`（`draw` 依次调 `_draw_left/_draw_file_panel/_draw_preview/_draw_stats/...`）；样板：`app/monthly_summary.py:171-179`（`_build_surface` 缓存）、`app/statistics/intensity_chart.py`（打开时一次画完）
- 证据（实测）：`PaintDialog` 稳态每帧 `rt=67`（无预览）/`121`（有预览）、图元 63。
- 建议：把左栏 + 文件面板的静态部分做成两张 Surface，仅在 `_rebuild_fields/_rebuild_dropdowns`（已有这两个方法）后失效。
- 预期收益：按计数推导，稳态每帧光栅化 67→约 5。
- 成本：低；风险：低。

**P13 `_land_orig` 用完即弃**
- 位置：`app/map_mgr.py:471-479`（载入 `_land_orig` 建 mask）；`_land_ge_alpha` 建完后 `_land_orig` 仍常驻
- 证据（实测）：`_land_orig` 222.5 MiB 与 `_land_geo_alpha` 55.6 MiB 同时存在；两者信息重叠（一个 RGBA 图、一个 alpha 字节表）。
- 建议：mask 建完即释放 `_land_orig`（或反之，只留一个）。
- 预期收益：-222 MiB/实例（估算，需实现后实测）。
- 成本：低；风险：中（`land2.png` 回退路径与 `_load_land_orig()` 的重复调用需要一起处理）。

## 7. 值得保留的现状 / unresolved

### 7.1 值得保留的现状（避免误伤）

1. **ACE 引擎的每点扫描是严格线性的**：为 6 个 ACE 年各建一次时间轴，`_parse_dt` 合计 = 8,491 = **每点恰好 1.00 次**；各年 `daily_ace` 同理。没有 O(n²)、没有"每年全表重扫"（实测 `probe_stats_work.py`）。
2. **时间轴/台风榜/年事件三重缓存齐全**：`sim._ace_timeline_cache`、`sim._ace_typhoon_cache`、`ty._ace_year_events`（排序事件 + 前缀和，`ace_engine.py:133-157`），`cumulative_ace_up_to` 用 `bisect` 做到 O(log n)（实测一次调用无每点扫描）。
3. **图表对话框不是每帧重建**：`ace_chart` 稳态每帧 `build_chart_data` 调用数 = **0**（`needs_update` 门控，`dialog_chart.py:247-250`）；`intensity_chart` 打开时一次画完（11 个 Surface / 3.79 MiB），之后每帧 `rt=0`。这是"该缓存就缓存"的正确样板。
4. **绘画渲染没有像素级反模式**：一次全局图 = 3 个 Surface、23,619 图元、PNG 81.1 KiB；没有每点 smoothscale、没有每点新建 Surface（实测计数）。
5. **数据去重规则有效**：`data_repo.py:138-150` 避免 73 个重复文件 / 3,427 个重复报点的二次加载（29%）。
6. **多年度对话框成本可控**：曲线只用已缓存时间轴（最多 8 年），洋区分桶只扫当前年（2,107 报点）。
7. **`SurfaceCache` 本身是好的抽象**：字节预算 + 真 LRU + 自带 hits/misses/evictions 计数（`cache_store.py:27-118`），可以直接作为后续优化的验收指标；共享的 3 个实例（32/96/48 MiB）上限固定、不随数据量增长。
8. **`rt()` 的换行缓存是存在的**（7 个调用点受益，上限 1024）——P4 要补的是"单行分支"，不是从零实现缓存。
9. **无内存泄漏**：`TySim` 在函数作用域内可被 `gc` 完整回收（实测存活实例 0，RSS 646→131 MiB）。

### 7.2 unresolved

1. **P1 的可达性**：只确证了 2,484 MiB 的上限，没有构造出"207 个台风的 `_img_cache` 同时填满"的场景（需要长时间季节回放 + 大量图标旋转）。需要一次长跑采样才能判定是"必然"还是"理论"。
2. **P2 建议改法的收益量级**：若改成 mipmap/分块，实际常驻能降到多少，取决于 `scale` 上限与屏幕尺寸的组合；未做实现实验。
3. **`_land_orig` 与 `_land_geo_alpha` 是否可以只留一个**：需要确认 `is_land_at_geo` 的查表路径与 `land2.png` 回退路径是否共用同一个原图（属 #14 渲染分区的接口）。
4. **真实窗口（2560×1540）下的几何差异**：本报告所有对话框/渲染计数都在 800×600 dummy 下取得；Surface 字节数与图层尺寸相关，调用计数不受影响，但"每帧光栅化次数"可能因可见行数变化而不同。
5. **`settings.py` 保存路径是否还有其他无守卫调用**：本次只核实了 `:2518` 与 `:1369`；`settings.py` 有 2500+ 行，不排除别处还有同类模式（建议 #13 启动分区或后续用 AST 全扫 `_apply_basin_filter`/`recalc_all_ace` 调用点）。
6. **69.61 s 与本次 41.95 s 的 ~28 s 差额**：归因为机器噪声（本机 A/A 极差在绑核后仍有 4.6%，未绑核 325%），但没有在该时刻同步记录系统负载，因此无法进一步拆分。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 单实例内存构成（445 MiB 地图 + 7.6 MiB 数据） | `app/map_mgr.py:17`、`:473`、`app/ty_sim.py:194-211` |
| 每台风自带的 12 MiB 缓存（聚合上限 2.48 GiB） | `app/typhoon.py:103` |
| 共享缓存上限（32/96/48 MiB） | `app/ty_sim_mixins/_draw_icon_mixin.py:352`, `:846`；`app/statistics/summary_list.py:35` |
| `rt()` 不缓存的根因 | `app/constants/fonts.py:161-163` |
| 未缓存字体渲染的调用点 | `app/paint_dialog.py`（21 处）、`app/ty_list.py`（20）、`app/statistics/dialog_chart.py`（18）、`app/script_dialog.py`（18）、`app/point_list.py`（17） |
| 设置保存触发全量重过滤 | `app/settings.py:2518`（对照守卫 `:1349-1352`） |
| 全量 ACE 刷新与局部刷新 | `app/ace_engine.py:324-388`（`:345-366` 分桶、`:376-386` 按年重建） |
| 建一次单年图表 | `app/statistics/data_builder_chart.py:31-102` |
| 图表缓存缺失不回填 | `app/statistics/data_builder_chart.py:43-53` |
| 每日/活跃期每点扫描 | `app/ace_engine.py:173-219`、`:233-297` |
| 时间轴（6 h 步长，1,460 点/年） | `app/ace_engine.py:299-322` |
| 季节统计每点成本 | `app/statistics/season_stats.py:24-165` |
| 绘画一次渲染的工作量 | `app/paint_render.py:201-238`、`:342-371` |
| 绘画统计重算（8,285 haversine） | `app/paint_dialog.py:309-312`（9 个调用点） |
| 绘画静态面板每帧重画 | `app/paint_dialog.py:689-719` |
| 热力图径向核 | `app/statistics/heatmap.py:177-224` |
| 数据扫描去重规则 | `app/data_repo.py:138-150` |
| 配置隔离夹具（测试别写用户配置） | `tests/conftest.py:1-40` |
| 本次全部探针 | `G:\tmp\perf\probe_*.py` |
