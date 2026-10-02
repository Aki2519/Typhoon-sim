# 04 应用数据模型 / 统计 / 绘图 / 脚本引擎

> 探索者：`app-feat`　范围：`app/statistics/**`、数据模型与业务、绘图工具、脚本引擎、`spline.py` / `utils.py`。
> 报告区分 **实测**（本次在本机跑过命令/代码得到的输出）与 **推断**（只读源码得出的判断）。
> 本环境真实解释器：`C:\Users\Yukioto\AppData\Local\Python\bin\python.exe`（Python 3.14.2 + pygame-ce 2.5.7 + pytest 9.1.1）。

## 一句话定位

这一层是模拟器的"史料层 + 计算层 + 表现层"：把 b-deck 文本变成可回放的台风对象，在其上算 ACE/季节统计并画图，另附一个驱动镜头的脚本 DSL 与一套复刻 Typhoon4 的路径图导出。

## 1. 清单与职责

| 文件 | 行数 | 职责 | 关键类 / 函数 |
|---|---|---|---|
| `app/typhoon.py` | 220 | 台风数据类 + 视图字段代理 | `TrackPoint`、`TyphoonView`、`Typhoon`（`typhoon.py:22` / `:81` / `:132`） |
| `app/typhoon_data.py` | 226 | 数据操作 Mixin（加点/ACE/时间/撤销） | `_parse_pt_time`、`add_point`、`recalc_ace`、`recalc_simulated_times` |
| `app/typhoon_sim.py` | 349 | 运动 Mixin（曲线回放/运动矢量） | `start_move`、`_move_on_curve`、`motion_vector`（归 `app-sim`） |
| `app/data_repo.py` | 498 | 数据仓库：目录扫描/格式探测/解析/过滤/配色 | `DataRepository`、`detect_format`、`parse_typhoon_file`、`_parse_latlon` |
| `app/track_quality.py` | 96 | 路径质量度量（段速度/偏离记账） | `km_between`、`segment_speeds`、`track_quality`、`deviation_report` |
| `app/track_dialog.py` | 105 | 单条路径的调试信息面板 | `TrackDialog` |
| `app/ty_list.py` | 893 | 左侧台风列表（搜索/排序/分页/行缓存） | `TyList`、`_apply_filter`、`_sort_key_fn`、`_matches` |
| `app/point_list.py` | 790 | 报点列表对话框（翻页/跳转/编辑） | `PointList`、`get_total_pages`、`_keydown` |
| `app/point_edit_dialog.py` | 320 | 单报点编辑（经纬/风速/气压/时间） | `PointEditDialog`、`_populate_fields`、`submit` |
| `app/name_pos_dialog.py` | 590 | 名称/峰值标注位置微调（写回 config） | `NamePosDialog`、`_preview_rect`、`_readout_ys` |
| `app/new_typhoon_dialog.py` | 188 | 新建台风（写 `# NEW` 占位文件） | `NewTyphoonDialog._create` |
| `app/monthly_summary.py` | 212 | 月度总结浮窗（滑入/滑出动画） | `MonthlySummary`、`_compute`、`_build_surface` |
| `app/ace_engine.py` | 388 | ACE 引擎（资格/年/累计/曲线/活跃期/缓存） | `ACEEngine`、`_ace_eligible`、`cumulative_ace_up_to`、`refresh_all` |
| `app/season_ctrl.py` | 403 | 季节/时间轴推进与回卷、CSA 累计 | `SeasonController`、`calc_years`、`update`、`jump_to` |
| `app/playback_ctrl.py` | 482 | 回放调度（淡入淡出/登陆判定/粒子/图标缩放） | `PlaybackController`、`update_all`、`_check_landfall` |
| `app/time_jump.py` | 196 | 指定时间跳转对话框 | `TimeJump`、`_fill_quick`、`_jump` |
| `app/cache_store.py` | 118 | 字节预算 + LRU 的表面缓存 | `SurfaceCache`、`surface_bytes` |
| `app/resource_mgr.py` | 179 | 单例资源管理（图标集/音效/风圈图） | `ResourceManager`、`_reload_icons`、`_create_ring_icon` |
| `app/resource_manager.py` | 7 | 纯转发 shim（`__all__` 再导出） | — |
| `app/paint_dialog.py` | 969 | 绘画模式主窗口（左参数/右文件列表/预览） | `PaintDialog`、`_do_render`、`_do_save` |
| `app/paint_render.py` | 591 | 路径图渲染引擎（复刻 Typhoon4） | `TrackMapRenderer`、`scheme_color`、`ex_pressure_category` |
| `app/paint_file_browser.py` | 374 | Ty4 式文件浏览器（树/多选/全选） | `FileBrowserDialog`、`_refresh`、`_bottom_action` |
| `app/script_engine.py` | 840 | 脚本 DSL 解析器 + 镜头状态机 | `Script`、`_Parser`、`ScriptEngine`、`scan_scripts` |
| `app/script_dialog.py` | 934 | 脚本列表 / 文本编辑器（撤销/IME/实时解析） | `_TextArea`、`ScriptDialog`、`_flush_parse` |
| `app/spline.py` | 257 | 单调三次样条 + 向心 Catmull-Rom + 弧长参数化 | `build_spline`、`build_monotone_spline`、`position_at_arc` |
| `app/utils.py` | 355 | 通用工具（时间格式/风速/配色/移动速度） | `movement_speed_kt`、`peak_point`、`parse_lon/parse_lat` |
| `app/statistics/dialog_chart.py` | 983 | ACE 统计主对话框（4 图 + 8 个子入口） | `ACEChartDialog`、`_rebuild`、`_draw_bottom_buttons` |
| `app/statistics/chart_helpers.py` | 561 | 图表基建（刻度/虚线/滚动条/提示/地图裁剪） | `nice_step`、`compute_path_km_in_window`、`ChartGridMixin` |
| `app/statistics/chart_presets.py` | 889 | 8 种图表绘制函数 | `draw_curve_chart`、`draw_yearly_ace_chart`、`draw_monthly_bars_chart` |
| `app/statistics/data_builder_chart.py` | 123 | 把 `ACEEngine` 结果组装成 `ChartData` | `build_chart_data`、`ChartData`、`_cutoff` |
| `app/statistics/shared.py` | 46 | 强度阈值表 / 填充带 / 12 色板 | `_THRESHOLDS`、`_FILL_BANDS`、`COLORS` |
| `app/statistics/intensity_chart.py` | 474 | 单台风强度-时间曲线 | `IntensityChartDialog` |
| `app/statistics/intensity_comparison.py` | 318 | 多台风强度对比（x 轴=距生成小时） | `IntensityComparisonDialog._build` |
| `app/statistics/multi_year_chart.py` | 249 | 多年度（柱/多年曲线/月度/洋区 4 Tab） | `MultiYearDialog._build_data` |
| `app/statistics/path_comparison.py` | 476 | 多路径投影对比 + 框选 | `PathComparisonDialog._render` |
| `app/statistics/path_length_viewer.py` | 285 | TS+ 路径长度排行 | `PathLengthViewer`、`_ts_eligible` |
| `app/statistics/heatmap.py` | 439 | ACE 径向累积热力图 | `PathHeatmapDialog._render`、`_ace_heat_color` |
| `app/statistics/season_stats.py` | 227 | 洋区/年度统计计算（纯函数） | `calculate_season_stats`、`_merge_intervals` |
| `app/statistics/season_stats_dialog.py` | 246 | 季节统计展示 | `SeasonStatsDialog._build_content` |
| `app/statistics/summary_list.py` | 460 | "总结条列表"（逐风暴一行） | `SummaryListDialog._build_storm_rows` |
| `app/statistics/typhoon_ace_chart.py` | 177 | 台风 ACE 柱状图（分页 20 条） | `draw_typhoon_ace_chart` |
| `app/statistics/__init__.py` | 5 | 空包标记 | — |

## 2. 数据流 / 调用链

```
① 磁盘 b-deck  →  内存台风对象
   typhoon/*.txt|*.dat（207 个，实测 5.5 MB）
     └─ DataRepository.load_typhoon_files        data_repo.py:130
          ├─ os.walk 收集 .txt/.dat，并跳过与 *_ty.txt 同名的原始 JTWC 文件  data_repo.py:138-150
          ├─ detect_format（看首个数据行第 5 列是否 BEST/CARQ/OFCL/OFCI）  data_repo.py:60-78
          ├─ parse_typhoon_file                     data_repo.py:179
          │    ├─ 多编码尝试 utf-8/gbk/latin-1/cp1252  data_repo.py:180-202
          │    ├─ 文件名取编号 → Typhoon("WP", n)     data_repo.py:207-211
          │    ├─ '# NEW' 占位头 → basin/n/name       data_repo.py:221-238
          │    ├─ 数据行 → _parse_latlon + ty.ap(...) data_repo.py:244-271
          │    └─ recalc_simulated_times()            data_repo.py:288
          ├─ 应用持久化自定义名 cfg.tn                data_repo.py:167-174
          ├─ _sort_by_basin / _refresh_ace / _fill_point_categories  data_repo.py:175-177
          └─ _all_tys_backup = 未过滤全集              data_repo.py:159

② 台风对象  →  ACE 数值
   Typhoon.add_point / recalc_ace（逐点 pace = w²/10000）  typhoon_data.py:110-111, :142-161
     └─ ACEEngine.fill_point_ace_years / refresh_all        ace_engine.py:97-102, :324-388
          ├─ sim.yad{year:ace}、_ace_timeline_cache、_ace_typhoon_cache  ace_engine.py:369-386
          └─ sim.tsa = Σ ty.tace、sim.sty/edy                    ace_engine.py:335-341

③ ACE 数值  →  图表像素
   ACEChartDialog.draw（needs_update → _rebuild）            dialog_chart.py:177, :247-250
     ├─ build_chart_data(sim, year, ...) → ChartData         data_builder_chart.py:31-102
     ├─ draw_curve_chart / draw_daily_ace_chart              dialog_chart.py:294, :301
     ├─ draw_active_periods_chart / draw_activity_count_chart dialog_chart.py:309, :314
     └─ 子对话框：热力图/路径对比/路径长度/多年度/总结条/季节统计  dialog_chart.py:44-52

④ 台风对象  →  路径图 PNG
   PaintDialog._do_render                                     paint_dialog.py:627-667
     └─ TrackMapRenderer.render                              paint_render.py:201-238
          ├─ resolve_bounds → 经纬范围                        paint_render.py:83-115
          ├─ _auto_output_size（保持经纬纵横比）              paint_render.py:260-270
          ├─ _draw_background/_draw_map_crop_into（裁剪不拉伸）paint_render.py:272-321
          ├─ _draw_tracks → _draw_ty_path + _draw_ty_points   paint_render.py:342-371
          └─ _draw_legend / _draw_labels                      paint_render.py:496-556
   PaintDialog._do_save → output/picture/trackmap_*.png        paint_dialog.py:669-681

⑤ 脚本文本  →  镜头运动
   ScriptDialog 列表（scan_scripts）→ 编辑器 → 实时解析        script_dialog.py:453, :496-530
     └─ ScriptEngine.load_script / start / update             script_engine.py:478, :491, :536
          ├─ _advance 逐条命令 → _enter_target/_do_time_jump  script_engine.py:559-582
          ├─ _update_dwell / _update_moving（缓动插值）        script_engine.py:602-690
          └─ _update_view_incremental → MapView.zoom_at/move_view  script_engine.py:694-740
```

## 3. 核心机制详解

### 3.1 一条路径的数据结构

`TrackPoint` 是 `@dataclass(slots=True)`，字段与含义（`typhoon.py:22-37`）：

| 字段 | 含义 |
|---|---|
| `t` | 报点时间串，10 位 `YYYYMMDDHH` 或 12 位 `YYYYMMDDHHMM`（`typhoon_data.py:14-22`） |
| `la` / `lo` | 纬度 / 经度，**经度 0–360 制**（西经转 `360-x`，`data_repo.py:327-330`） |
| `w` / `p` | 风速 kt（整数列）/ 中心气压 hPa |
| `st` | 性质代码：`TD/TS/TY/ST/HU/EX/SS/SD/LO/MD/DB/WV/…` |
| `ace` | **该点为止的累计 ACE**（前缀和，不是单点值）；`pace` 才是单点增量 |
| `official` | 是否正式报（`t[8:10] ∈ {00,06,12,18}`，`typhoon_data.py:98`） |
| `ace_year` | 该点归属的 ACE 年（南北半球规则不同，见 3.2） |
| `color`/`color_dim`/`cat` | 预计算配色与类别（`typhoon_data.py:123-125`） |

为了兼容老代码的 `pt['la']` 下标写法，`TrackPoint` 额外实现了 `__getitem__/__setitem__/get/update/__contains__` 五个 dict 协议方法（`typhoon.py:39-54`）——**它是 dataclass 但被当 dict 用**，这是全工程最常见的访问风格。

`Typhoon` 用 `__slots__`（`typhoon.py:132-147`）把 40+ 字段摊平，但把**纯视图状态**（屏幕点、图标旋转角、路径缓存、运动采样…共 30 项，`typhoon.py:57-78`）都塞进 `TyphoonView` 子对象（`typhoon.py:81-129`），并用 `__getattr__/__setattr__` 做透明代理（`typhoon.py:185-204`）。目的：视图状态可以整体丢弃/重建而不动数据字段。末尾还有一串 2–4 字母别名（`ap`/`cp`/`sm`/`um`/`cpos`/`us`/`rst`，`typhoon.py:214-220`）——**全文最密集的命名债**，读代码时必须回查这张表。

时间口径有两套并存：

1. `points_dt`：真实 `datetime` 列表，解析失败回落到 `2000-01-01`（`typhoon_data.py:171-179`）。
2. `points_time`：**自定义压缩小时轴**，`0.5 = 6 模拟小时`（1.0 = 12 小时）。依据：`typhoon_data.py:186-188`（每个正式报 +0.5）、`ty_sim_mixins/sim_mode_mixin.py:363-364`（`float(q['t'])/12.0`，注释同款）、以及消费端 `utils.movement_speed_kt` 里 `dt_h = (points_time[ib]-points_time[ia]) * 12.0`（`utils.py`，`movement_speed_kt` 内）。

非正式报点（03/09/15/21Z）用线性插值挤在两个正式报之间（`typhoon_data.py:204-225`），并强制单调（`:200-202`）。

### 3.2 ACE 公式与三处口径

核心公式（三处实现必须一致）：

```
pace = w² / 10000        # w 单位 kt，单点 ACE 增量，保留 4 位小数
ACE  = Σ pace

资格（三条件同时成立）：
  ① st ∈ {TS, TY, ST, HU, ""}        # 排除 EX/SS/SD/LO/MD/DB/WV
  ② w ≥ 35 kt
  ③ official == True（多数路径）/ 可选 strict
```

- 加点路径：`typhoon_data.py:110-111`（`st_up in ('TS','TY','ST','HU','')` + `w>=35` + `is_official` + 地理限制）
- 重算路径：`typhoon_data.py:149-153`
- 引擎路径：`ace_engine.py:29-38`（`_ace_eligible`，`strict` 控制是否要求 official）

**实测**（真实解释器调用 `_ace_eligible`）：`TS/35kt/official` → True；`TS/34kt` → False；`EX/100kt` → False；`""/50kt/official=False` → `strict=False` 为 True、`strict=True` 为 False。

ACE 年规则（`ace_engine.py:70-78`）：北半球 = 日历年；南半球 = **7 月 1 日切分**（`month>=7 → year`，否则 `year-1`），年度窗口 `[Y-07-01, (Y+1)-06-30]`。地理限制三态 `none / latlon / basin`（`ace_engine.py:80-95`），`basin` 走 `ocean_areas.get_by_code(...).contains()`。

活跃日与活跃期（`ace_engine.py:194-219`, `:233-297`）：活跃日集合的键是 `id(ty)`（`daily_activity_count`），而活跃期的起止由「资格翻转点」（`_ace_eligible` 变化处）+ 首末非次天气系统报点组成；`type2_times` 记录只出现一次的翻转时刻（`ace_engine.py:271-273`）——这是一个很细的去重技巧。

缓存分层（`ace_engine.py:324-388`）：单台风年 ACE（`ty._ace_ty_yearly`，键含 `id(ty.pts)/len/mode`）→ 年时间轴（`sim._ace_timeline_cache`，6 小时步长，`ace_engine.py:299-322`）→ 年台风榜（`sim._ace_typhoon_cache`）。局部刷新时会把**旧缓存里出现过的年份**也并入 changed 集合（`:352-356`），否则"点被改到别的年份"会让旧年图表留着脏数据。该年 ACE 归零时主动删缓存（`:383-386`）。

**实测**：`typhoon/` 下 207 个数据文件，解析成功 206 条台风、8491 个报点，其中 `official` 8454 个、非正式 37 个（0.44%），全量解析耗时 0.60 s。

### 3.3 b-deck 解析：整十分之一度与"小数点即坏点"

`_parse_latlon`（`data_repo.py:301-331`）的关键约定：

```
"1597E" → 159.7°E     "73N" → 7.3°N      # 数值统一 /10.0
"100W"  → 360-10 = 350.0                  # 西经转 0-360
"0W"    → 0.0                             # 与 0E 等价（:329-330）
```

强制规则：**带小数点的坐标视为坏点，抛 `ValueError` 由调用方整行跳过**（`data_repo.py:305-322`）。文件头注释说明理由：正则允许 `99.98E`，`/10` 会得到 9.998 这种"完全错误却不报错"的坐标。**实测**：`_parse_latlon("1597E","73N")` → `(7.3, 159.7)`；`_parse_latlon("99.98E","02N")` → 抛 `ValueError("经纬度为整十分之一度格式, 不应含小数点")`。

分钟列处理（`data_repo.py:248-253`）：第 4 列长度 ≤2 且为数字时拼到时间串后面，`t` 从 10 位变 12 位；下游 `_parse_pt_time` 会吃到分钟位但 `official` 仍只看 `t[8:10]`，所以 03:30 这类点永远不会被当成正式报。

JTWC 原始格式的两个特殊处理：
- 只看第 5 列是 `BEST/CARQ/OFCL/OFCI`（`data_repo.py:73-77`）；
- 第 6 列（风圈标记）非 `0` 的行直接跳过（`data_repo.py:258-259`），转换时也丢弃第 12 列为 `50/64` 的行（`data_repo.py:89-91`），并把第 5 列改写为 `chunshu`（`:93-95`）。

`ensure_simple_bdeck_copy`（`data_repo.py:97-128`）是一个**数据安全闸**：JTWC 原件不可写，必须先转换成 `*_ty.txt` 再改指针；转换走 `.tmp` + `os.replace` 原子替换，0 字节产物视为失败并返回 False，调用方据此放弃保存（否则会把 simple-bdeck 文本写回原始 JTWC 件，不可逆）。

### 3.4 统计模块的判据与算法

**`season_stats.calculate_season_stats`**（`app/statistics/season_stats.py:24-165`）是纯函数式的洋区统计，返回 17 个字段。要点：

- 先按 ACE 年 + 地理限制筛点（`:69-71`），再按洋区 `contains` 二次筛（`:76-81`）；
- 计数档位：`TD+ = w≥29`、`TS+ = w≥35`、`TY(C1+) = w≥64`、`MH(C3+) = w≥96`、`C5 = w≥137`（`:97-101`）——注意这里**不看 `st` 代码**，只看风速，且先剔除了 `MD/SS/SD/EX/LO`（`:91-92`）；
- `storm_days = len(唯一报点时刻) × 0.25`（`:147-148`，集合键是 `%Y%m%d%H`，`:107`）；
- `total_active_hours` = TS+ 区间并集（逐台风取首末，去重叠合并，`:136-145` + `_merge_intervals` `:213-227`）；
- 路径长度按 ACE 年窗口切分跨年台风：南半球传 `(Y-07-01, (Y+1)-07-01)`（`:128-132`）；
- 登陆次数优先用 `sim.landfall_records`，为 0 才回落到从数据点检测（`:150-160`）。

`chart_helpers.compute_path_km_in_window` 与它配套：跨窗口边界时按时间线性插值出边界点再累加 haversine，经度差先归一到 (-180,180]（`chart_helpers.py`，`_haversine_chain` / `compute_path_km_in_window`），避免 1/1 边界首尾段被丢弃、也避免跨 0° 经线绕远路。

**月度总结**（`monthly_summary.py:104-169`）：按 `t[4:6] == 月份` 且 `t[:4] == 年` 过滤，先剔除次天气系统报点，再统计风暴数 / TS+ / MH / C5 / 月 ACE / 最强风暴；`strongest` 用 `>=` 而非 `>`，注释说明是为了"月内有风暴但风速全 0"时最强行不缺失（`:154-157`）。

**热度图**（`heatmap.py:96-256`）：Phase 3 是径向核累积——核半径固定为 `2° 纬距对应的像素`并封顶 120 px（`heatmap.py:177-179`），权重 `w = pace × (1 - d/r)`（`:222`），用 numpy `np.add.at` 累加；Phase 4 从 `assets/SMCY/resource/ace-heat.png` 取 LUT，量化 `idx = floor((1 - v×0.125)×H)`（`:243-244`）。色表缺失时降级为红→绿线性色阶（`heatmap.py:49-60`），保证热力图仍能画。

**强度阈值与填充带**（`shared.py:16-46`）是两套独立的表：`_THRESHOLDS`（12 档实线，`29→TD … 170→C5_D`）只用于画横线，`_FILL_BANDS`（13 段，含 `0-29 → DB`）用于画背景色带。**注意 `_THRESHOLDS` 的第一档是 29 不是 0**——因为它的用途是画线，不是查表，所以不构成越界 bug（消费者见 `intensity_chart.py:241`, `intensity_comparison.py:169`）。

**多台风强度对比**（`intensity_comparison.py:90-241`）：x 轴 = `距生成的小时数`（以每条台风自己的首个 ACE 年报文为 0 点，`:149-152`），全局 x 上限向上取整到 24 的倍数（`:180`），颜色取 `COLORS[i % 12]`（`:198`），超过 12 条台风会重复配色。

**多年度**（`multi_year_chart.py:46-117`）：柱状图用 `sim.yad`；曲线只画**最近 8 年**（`:58`）；月度 Tab 把 ACE 年的第 m 个月映射到南半球自然月 `m±6`（`:83-88`），渲染时再补一次标签换算（`:178-180`）；洋区 Tab 用 `areas.find_area()` 单趟分桶（`:106-108`）。

### 3.5 脚本引擎：语法（实测）与状态机

**解析流程**（`script_engine.py:190-243`）：逐行 → 去空行/`#` 注释 → 去掉行尾内联注释（`:203-206`）→ 顶层缩进检查（`:208-210`）→ `%` 终止（`:212-213`）→ 分派到 `jump / target / to / repeat / wait / pause / *speed / speed / hold`。

**实测语法清单**（真实解释器逐条跑过）：

| 写法 | 结果 |
|---|---|
| `jump 2026-04-12 00` | `CMD_JUMP`，`_parse_date` 支持 `HH:MM`、可省小时 |
| `jump 2026-04-12-06z` | 旧格式仍可解析（`:85-95`） |
| `target 115 18 w=15` / `to 115 18 w=15` | 等价，`to` 是别名（`:219-220`） |
| `target … -smooth` / `-s` | `constant_speed=False`（走 ease-in-out）；否则匀速（`:268-270, :632`） |
| `100W` / `5S` | 经度 260.0 / 纬度 -5.0（`:48-62`） |
| `w=0` | 报错"宽度必须大于 0"（`:282-283`） |
| `arrive 2026-04-12 06:45` | → `07:00`；`06:20` → `06:00`（30 分进位，`:82-84`） |
| `*1.5` / `speed 1.5` | 块内按位置决定阶段：arrive 前=接近、arrive 后=停留、depart 后=离开（`:319-343`） |
| `repeat 2:` + 缩进块 | 展开成 N 份，`target.index` 全局唯一编号（实测 7 个 target → 索引 0..6） |
| `wait 5s` / `wait 2m` / `hold 2m` | 真实时间等待（`_parse_duration` `:103-110`，`hold` 别名 `:233-239`） |
| `wait` / `pause` | `CMD_WAIT_USER`，脚本停住等交互（`:225-226`） |
| 顶层缩进 | 报错"顶层命令不应有缩进" |
| `repeat 999999999` | 报错"repeat 次数超过上限 1000" |
| 双层 `repeat 1000 / repeat 30` | 报错"展开后命令数 30000 超过上限 20000" |

重复执行保护是**解析期**拒绝而非运行期限流（`_MAX_REPEAT_COUNT=1000`、`_MAX_EXPANDED_COMMANDS=20000`，`script_engine.py:43-44`），且块只解析一次成模板、迭代时 `copy.deepcopy` 并按 `base` 重编号（`:402-428`）——这段注释解释了"原地重编号会污染模板导致 index 漂移"的坑。

状态机 5 态（`script_engine.py:434-438`）：`IDLE / DWELL / MOVING / WAIT_REAL / WAIT_USER`。时间比较统一走"绝对秒"：`_sim_abs_ste() = sim.ste + Σ(1..sy-1 年秒数)`（`:749-758`），并用 `lru_cache` 缓存年秒数；`_ease_in_out` 是标准 cubic in-out（`:786-791`，实测端点 0/0.5/1 正确）。`_update_moving` 复用手动拖动路径（`right_button_dragging=True` + 清缓存，`:645-648`），到位后必须关掉虚拟拖动并清各台风拖动缓存（`:656-661`）。

两个"不许倒写时间"的守卫很关键：只有脚本**显式**写了 `arrive`/`depart` 日期才允许把模拟时间拨到该时刻（`_has_explicit_move_to/_from`，`:609-610, :667-668`），否则自动回退的 `2000-01-01` 会把无日期 target 强行拉回 2000 年（注释标为 R3 修复）。

`scan_scripts`（`:804-840`）**只认 `.json`**，读首个非空行、若以 `#` 开头则作为简介；脚本保存时也强制把扩展名换成 `.json`（`script_dialog.py` 的 `_save_editor_script`，`os.path.splitext(...)[0] + '.json'`）。所以"脚本文件"其实是**后缀为 .json 的纯文本 DSL**。

### 3.6 绘画模式：Typhoon4 复刻要点

**投影**：等距圆柱，`x = inner.x + (lo-lon_min)/span_lon*inner.w`，`y = inner.y + (lat_max-la)/span_lat*inner.h`（`paint_render.py:189-197`）；全局范围固定 `0..360 × -90..90`（`:86-87`）。

**尺寸**（`paint_render.py:224-226, :260-270`）：`k = inner.w / 经度跨度`（每度像素），内容区按经纬跨度比生成，再与 `min_w/min_h`（默认 1920×1080）取大；`coord_unit=px` 且设了留边时，输出 = 内容区 + 四周留边，地图只画在内容区——这样地图不会被拉伸。

**地图裁剪**（`:285-321`）：源图 `map/map.png` 是 0..360 × -90..90 等距圆柱，先按经度与纬度双轴裁剪再 `smoothscale` 到内容区；经度环绕时拆成两段贴图（`:302-316`）。**这是"只按经度裁剪会把纬向拉长"的针对性修复**。

**路径线**（`:373-403`）：**白色细线**（Ty4 原版风格，不是强度色），宽 `max(1, round(track_deg × k))`，`track_deg` 默认 0.05°；相邻点按环面最短经度差 `((lo2-lo1+180)%360)-180` 连线，`|dlat|>90` 视为跳段丢弃。

**定点**（`:405-477`）复刻 Ty4 点型规则（注释 `:405-410` 就是规格）：

| 情形 | 形状 | 颜色 |
|---|---|---|
| 普通点 | 圆 | 按风级推断类别取色 |
| `SS` | 方 | 用 `TS` 色 |
| `SD` | 方 | 用 `TD` 色 |
| `EX/ED/ES/E1..E5/LO` | 三角 | `ED/ES/LO` → `TS` 色；`E1..E5` → `C1..C5` 色 |

EX 系的气压分级（`ex_pressure_category`，`:411-434`）**实测**：`0→ES`（缺失视为居中）、`>1005→LO`、`1001-1005→ED`、`986-1000→ES`、`971-985→E1`、`961-970→E2`、`951-960→E3`、`940-950→E4`、`<940→E5`。

正式报判定（`:459-464`）：`t[8:10] ∈ {00,06,12,18}`，正式报半径 `r`、非正式报 `r//2`（`:476`）。

**风圈**（`:479-494`）：`draw_rad ∈ none/all/last/tropical`，半径 `rad34 = p['r34'] or int(wind×0.5)`，画 1 px 圆环，颜色 `(0,0,0,120)`。**这里没有真实 r34 数据**，退化成风速线性近似（且单位是像素）。

**图例**（`:496-535`）：默认 7 档英文条目 `WIKI_LEGEND_ENTRIES`（`constants/colors.py:64-72`），位置支持 `auto`，`auto` 按四象限点密度选最空的一角（`_auto_legend_corner` `:564-571`），无底框时给文字加 4 向描边。

**色阶三方案**（`paint_render.py:51-61` + `constants/colors.py:33-62`）：`app` 用主程序色板、`wiki` 用经典 Wiki 色板、`2023` 用 `dict(WIKI_SCALE)` 加 3 个键。**实测**：渲染路径可请求的 13 个类别里，`wiki` 与 `2023` **逐色完全相同**（差异只存在于 `C2-/C3-/C4-ST`，而这 3 个类别在 `_eff_category` 里已被归一化成 `C2/C3/C4`，`paint_render.py:69`，永远不会被请求）。

### 3.7 脚本镜头的数值细节（增量视图更新）

脚本移动镜头时**不重建整幅视口**，而是每帧算"目标中心 → 当前中心"的像素差再平移（`script_engine.py:694-740`）：

```
clon = (mlo+Mlo)/2 ,  clat = (mla+Mla)/2                # 目标经纬中心
new_scale = clamp( min( mv.screen_width  / (lon_span/mv.width_deg  * mv.img_w),
                        mv.screen_height / (lat_span/mv.height_deg * mv.img_h) ), min_scale, 8.0 )
if |new_scale - mv.scale| > 1e-4:  mv.zoom_at(factor, 屏中心)  → 全量重建屏幕点 + 清拖动缓存
target_vx = ((clon - mv.lon_min)/mv.width_deg * mv.img_w - mv.screen_width/(2*mv.scale)) % mv.img_w
target_vy = (mv.lat_max - clat)/mv.height_deg * mv.img_h - mv.screen_height/(2*mv.scale)
raw_dx = mv.view_x - target_vx   ；>半个图宽 / <负半个图宽 时 ±img_w（经度环绕取近端）
mv.move_view(raw_dx*mv.scale, (mv.view_y-target_vy)*mv.scale) → 把实际位移反补到 _drag_offset
```

要点：缩放被判定为"破坏旧坐标系"，因此走全量重建 + 清零偏移（`:715-720`）；而纯平移只改 `_drag_offset_x/y`（`:736-738`），复用手动拖动的快速路径。经度环绕在这里是显式的"取近端"补偿，否则镜头会绕地球一圈（`:727-732`）。

对应的绝对时间轴换算（`:749-762`）：`sim.ste` 是"当年内的秒数"，跨年需要加上历史年份秒数；`_year_seconds_abs` 用 `lru_cache(maxsize=64)` 按年缓存闰年判定结果，`_dt_to_abs_ste` 则把脚本里的 `datetime` 转成同一坐标，两者相减即插值进度 `t`。

### 3.8 支撑件

**`spline.py`** 两种曲线：
- 默认 `monotone`：Fritsch-Carlson 单调三次，分别对 x(t)、y(t) 求导，端点导数用"相邻割线镜像外推 + `3×|斜率|` 限幅"（`spline.py:80-116`），弦长参数化（`:146-151`），Hermite 基函数求值（`:121-135`）。
- `catmull`：向心 Catmull-Rom（参数 `sqrt(chord)`，alpha=0.5），<4 点自动回落单调样条（`:183-223`），首末段用复制幻影点 + 参数外推避免退化（`:199-201, :215-218`）。
- **实测**：5 点折线 `build_spline(...,10)` 出 41 点，首末点精确等于原首末点，catmull 模式下 5 个原采样点全部严格穿过；弧长单调、`position_at_arc` 取中点正确；`catmull_rom` 匀参版 `t=0→p1`、`t=1→p2`。

**`cache_store.py`**：真 LRU（`OrderedDict.move_to_end`）+ 字节预算，单个超预算对象直接不缓存（`:71-82`），带 hits/misses/evictions。所有生产调用点存的都是 pygame Surface（`typhoon.py:103`、`typhoon_render.py:149`、`_draw_icon_mixin.py:352/846`、`summary_list.py:35`）。

**`track_quality.py`**：局地平面近似（`110.57 km/deg` 纬向、`111.32×cos(lat)` 经向，`:13-14, :30-39`），`wrap_dlon` 归一经差（`:24-27`），`SEGMENT_SPEED_LIMIT_KMH=80`、`CURVE_DEV_LIMIT_KM=25`。**实测**：`km_between(10,120,10,122)=219.2576 km`，与 `2×111.32×cos10°` 完全一致；`wrap_dlon(359,1)=2.0`、`wrap_dlon(1,359)=-2.0`。

**`script_dialog.py`**：`_TextArea` 自带撤销栈 / 粘贴复制剪切 / 可视行滚动 / IME 取得与释放；实时解析做了 200 ms 防抖（`_live_parse` 只置脏标志，`_flush_parse` 限频执行）。

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `typhoon/**/*.txt`、`*.dat` | b-deck 文本（simple / JTWC） | 207 个文件，5.5 MB，8491 报点 | 人工/外部下载、`# NEW` 占位与 `*_ty.txt` 派生件由程序写 | `DataRepository.load_typhoon_files`（`data_repo.py:130`） |
| `assets/` | 图片/音效/字体/图标集 | 529 MB | 美术资源 | `resource_mgr`、`paint_render.load_map_image`、`heatmap._load_heat` |
| `assets/SMCY/resource/ace-heat.png` | PNG 色表（竖条 LUT） | 单文件 | 美术资源 | `heatmap._load_heat`（`heatmap.py:25-46`） |
| `tests/script/*.json` | **纯文本 DSL**（后缀为 .json） | 6 个文件 | 手工/编辑器保存（强制 .json） | `scan_scripts`（`script_engine.py:804-840`）、`ScriptDialog` |
| `output/picture/trackmap_*.png` | PNG 路径图 | — | `PaintDialog._do_save`（`paint_dialog.py:669-681`） | 用户 |
| `output/` 其它子目录（logs/sim/video/ref_*） | 日志/模拟产物/视频/参考图 | — | 各子系统 | 用户/文档 |
| `config.json`（+`config.json.bak`） | JSON 配置 | 3744 / 3784 B，113 / 114 键 | `TySim.save_config` | 全体模块 |

## 5. 如何运行 / 如何验证

**GUI（本环境可跑但有窗口依赖，未尝试长期运行）**
```
pip install -r requirements.txt
python main.py
```

**本次实测的 pytest（真实解释器，最多 8 个单文件，每个 `timeout 180`）**

| 命令（省略 `-m pytest`） | 结果 |
|---|---|
| `tests/test_ace_engine.py -q` | **19 passed** in 1.03 s |
| `tests/test_bdeck_format.py -q` | **43 passed** in 2.20 s |
| `tests/test_track_quality.py -q` | **17 passed** in 0.76 s |
| `tests/test_typhoon.py -q` | **23 passed** in 0.98 s |
| `tests/test_spline.py -q` | **20 passed** in 0.73 s |
| `tests/test_multi_year_tools.py -q` | **5 passed** in 0.90 s |
| `tests/test_subarea_stats.py -q` | **17 passed** in 1.08 s |
| `tests/test_paint.py -q` | **15 passed** in **69.61 s** |

合计 **159 passed / 0 failed**。解释器：
`cd /mnt/g/气象/Typhoon-sim && timeout 180 /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe -m pytest tests/test_ace_engine.py -q --no-header`
（该解释器中文输出为 GBK，管道需 `| iconv -f GBK -t UTF-8`。）

**本次实测的独立验证脚本**（真实运行时，非 pytest）：
1. 色阶对比：`scheme_color('wiki',c)` vs `scheme_color('2023',c)` 遍历全部类别 + 全部渲染可达类别 → 差异仅 3 个不可达类别；
2. 用真实 `Typhoon` 加点后调 `segment_speeds` → 1,578,654.71 km/h，同日历真值 36.54 km/h；
3. `SurfaceCache(name, 0)` → put 后 len=0、evictions=1；
4. `DataRepository` 直连解析 207 个真实数据文件 → 206 条台风 / 8491 报点 / 0.60 s；
5. `Script.parse` 跑 README 示例 + `tests/script/*.json` 6 个文件 → 4 OK / 2 FAIL。

**在本环境跑不了 / 未跑的**：全量 pytest（禁止）；GUI 交互（需要窗口，未做）；`tests/test_trackpoint.py`（23 个候选里为遵守 8 文件额度而未跑，非失败）。

### 5.1 本范围内的测试覆盖（实测统计）

`tests/` 共 57 个文件；与本人范围相关 19 个、297 个用例。**零提及**的模块 17 个：
`typhoon_data`、`monthly_summary`、`time_jump`、`resource_mgr`、`paint_file_browser`、`name_pos_dialog`、`script_engine`、`script_dialog`、`chart_presets`、`data_builder_chart`、`heatmap`、`multi_year_chart`、`path_length_viewer`、`season_stats`、`shared`、`summary_list`、`typhoon_ace_chart`。

其中最值得注意的是：**脚本引擎（840 + 934 行）没有任何 pytest 用例**，`tests/script/` 下只有一个 `smoke_datamode.py`（且它测的是 SimCore 环境场，与脚本引擎无关）。

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR

**M1 `wiki` 与 `2023` 两套色阶完全相同（功能名不副实）**
现象：绘画模式「色阶方案」下拉有三个选项，但后两个输出逐像素相同。
证据：`COLOR_2023_SCALE = dict(WIKI_SCALE)` 只多 3 个键（`constants/colors.py:51-56`），而这 3 个键在渲染前已被 `_eff_category` 归一化掉（`paint_render.py:69`）。
实测：遍历渲染可达的 13 个类别与 7 个图例条目，`wiki`/`2023` 差异数 = 0；差异只出现在 `C2-/C3-/C4-ST`。
影响：README:94 宣传的三方案实际是两方案；`C2-/C3-/C4-ST` 的细分色在绘画模式里从未生效。

**M2 「路径宽度 / 定点大小」参数对绘制结果无影响**
现象：绘画模式左栏可填路径宽度与定点大小（px），改数值后路径线与定点不变。
证据：渲染器赋值 `self.path_width`（`paint_render.py:165`）后**全文再未读取**；路径线宽实际用 `track_deg × k`（`:382`，`track_deg` 默认 0.05°），定点半径用 `point_deg × k × 0.5`（`:467`，默认 0.4°）；对话框却把 `path_width/point_size` 传了进去（`paint_dialog.py:637-638`）。`point_size` 唯一的用处是风圈半径的加项（`paint_render.py:493`）。
影响：Ty4 的「角度制尺寸」被硬编码，用户调节失效。

**M3 「多文件重置」下拉是死控件**
现象：三个选项（不重置/自动重置/仅最后定位）改什么都不发生。
证据：`multireset` 在渲染器里只有一行赋值，注释自承"保留(兼容)"（`paint_render.py:177`），再无引用；UI 侧有三个选项（`paint_dialog.py:262-263`）并透传（`:649`），README:91 也把它列为显示选项。
影响：README 与 UI 都承诺了一个不存在的行为。

**M4 `track_quality.segment_speeds` 单位口径与全工程相反（43200 倍）**
现象：对真实台风给出的移速是 1,578,654 km/h，任何路径都会被判 `review`。
证据：函数按"秒"处理时间轴 `dt_h = (points_time[i+1]-points_time[i]) / 3600.0`（`track_quality.py:50`），但生产数据的 `points_time` 单位是 `0.5 = 6 小时`（`typhoon_data.py:186-188`、`ty_sim_mixins/sim_mode_mixin.py:363-364`），同工程正确消费方 `utils.movement_speed_kt` 用的是 `× 12.0`。
实测：真实 `Typhoon`（6 小时东移 2°）→ `segment_speeds` = 1578654.71 km/h；按 `points_dt` 算真值 = 36.54 km/h；差值恰为 43200（=3600×12）。
而 `tests/test_track_quality.py:24-29` 的 `fake_ty` 用 `h * 3600.0` 造时间轴，**测的是秒口径**，所以 17 个用例全绿。
影响：`track_quality()` 目前无生产调用点（`renderer.py:17` 注释"面板已去掉; 常量留着备用"），所以是**潜伏 bug**：一旦复用该函数就会得到全 `review`；同时该测试文件给出的是虚假安全感。

**M5 两个"坏脚本"会出现在脚本列表里，运行即报错**
现象：脚本列表按目录扫 `.json`（`script_engine.py:809-810`），把 `2025wp.json` 与 `evil.json` 一起列出；点运行则弹"解析失败"。
实测（真实解释器）：
- `2025wp.json` → `ValueError: 第2行: 未知命令: >2025-01-01-00z`（内容是 `>date` / `/120E 10;60` / `==date` 的**旧语法**）；
- `evil.json`（内容一个 `x`）→ `第1行: 未知命令: x`；
- 其余 4 个（`2026/2099wp/25-26sh/Dolphin`）解析正常。
影响：这更像"故意留的坏样本 + 一份没迁移的旧脚本"，但 UI 不做预校验也不标注，用户会以为脚本坏了。README:85 只含糊提到"旧格式中的符号 `=`"。

**M6 脚本模块自称 `.txt`，实际只认 `.json`**
现象：`script_engine.py` 模块 docstring 写"语法 (.txt)"（`script_engine.py:11`），`scan_scripts` 只接受 `.json`（`:810`），保存时强制改名为 `.json`（`script_dialog.py` `_save_editor_script`）。**实测**：`scan_scripts("./typhoon")` 返回 `[]`。
影响：文档与实现漂移；真实脚本目录还是 `tests/script/`（`script_dialog.py:40-41` 注释"script/ 已并入 tests/script/"），用户脚本与测试样本混放。

### MINOR

**m1 README 说 PNG 存到 `picture/`，实际是 `output/picture/`**　证据：README:89 vs `paint_dialog.py:676-677`（注释"截图统一进 output/，用户 2026-09-19 精简主目录"）。

**m2 `SurfaceCache` 文档说 `max_bytes=0` 不限字节，实际全部驱逐**　证据：docstring `cache_store.py:28` vs `_trim` 的 `self._bytes > self.max_bytes`（`:99`）。实测 `SurfaceCache("x",0)` put 一个 10×10 表面后 `len=0, evictions=1`。当前 5 个调用点都传正数，故未爆发。

**m3 `storm_days` 的注释与实现不符**　注释说"正式报中活跃的报点数 ×0.25"（`season_stats.py:56`），实现是**跨台风按时间戳去重**的集合大小 ×0.25（`:63, :107, :147-148`）。同时活动的两个系统只贡献 0.25 天而不是 0.5 天。

**m4 登陆回退检测没有套 ACE 地理限制**　主循环筛点带 `engine.point_in_limit`（`season_stats.py:69-71`），但 `_compute_landfalls_from_data` 只按 `ace_year` 筛（`:176`）。开了 `latlon/basin` 限制时，登陆计数与国家统计口径分叉。

**m5 `data_builder_chart` 缓存缺失时 ACE 曲线为空，且不会回填**　`ace_curve_points` 只在 `sim._ace_timeline_cache` 命中时赋值（`data_builder_chart.py:43-53`），没有 `else: engine.build_timeline_cache(year)` 分支。推断（非实测）：正常情况下 `refresh_all` 会为 `ace>0` 的年份建缓存（`ace_engine.py:376-386`），因此仅当缓存被外部清掉时才会出现空曲线。

**m6 文件名编号提取在 201/207 个文件上退化成 6 位年份串**　代码注释声称"取文件名中 1~3 位数字段，避免把年份/日期段误当编号"（`data_repo.py:207-209`），但第一正则 `\b(\d{1,3})\b` 对 `bsh012026` 这类无分隔串不匹配，回退到无长度限制的 `(\d+)`。**实测**：`bsh012026 01S.txt → "012026"`，201/207 个文件命中该退化。实际影响小：数据行第 2 列会覆盖 `ty.n`（`data_repo.py:254-256`），`# NEW` 头也会覆盖（`:224-234`）——所以这是"注释与代码不符 + 潜在脆弱点"，不是当前故障。

**m7 `_auto_legend_corner` 有重复条件**　`if not density or not density:`（`paint_render.py:565`），第二个条件显然是笔误（大概本意是判 `w/h`）。后果仅是"图例永远优先左上"的退化，不易察觉。

**m8 多年度对话框有死属性 + 滚轮切 Tab**　`self._selected_year`、`self._monthly_data` 在 `activate` 里赋值后全文未用（`multi_year_chart.py:39-40`）；`MOUSEWHEEL` 直接切换 Tab（`:244-248`），与其他对话框"滚轮滚动内容"的约定相反。

**m9 `config.json` 与 `config.json.bak` 已分叉，且注释自承测试会写用户配置**　实测 diff：删掉 `screen_width/screen_height/fullscreen`，新增 `peak_dx/peak_dy`，并改了 `ac: true→false`、`sp: 0.5→2.0`、`point_name_mode: false→true`、`icon_set: simple→smcy`、`name_dx/dy: 0/0 → -23/17`。`tests/conftest.py` 开头就写了"别让测试写用户的 config.json"，说明这是**已发生过的真实事故**。当前两份都在磁盘上，无法判断哪份是用户想要的状态。

**m10 `tests/test_paint.py` 单文件 69.61 s（占本次 8 个文件总耗时的 88%）**　同批其它文件 0.7–2.2 s。推断：该文件构造了完整的 `TySim` + `ControlPanel` + 渲染（`tests/test_paint.py:128-129, :166`）。会显著拖慢迭代。

**m11 统计与脚本大量模块零测试**　见 5.1 的 17 个零提及模块清单，其中 `season_stats.py`（227 行纯计算）**完全可纯函数测试却一个用例都没有**。

**m12 「绘画」范围模式 `region` 输入顺序易错**　`resolve_bounds` 把 region 解释为 `(top, left, height, width)` 并返回 `(left, left+width, top-height, top)`（`paint_render.py:88-91`）；README:93 写的是 `上,左,高,宽`，与实现一致，但对话框的解析只是把逗号分隔的 4 个数原样传给渲染器（`paint_dialog.py:603-609`），填错顺序不会报错只会画出怪图。

## 7. unresolved（未能确证）

1. **`data_builder_chart` 空曲线是否可达**：需要构造"year 有台风但 `_ace_timeline_cache` 无该年"的状态并跑 GUI，未做 → 停在推断（m5）。
2. **`storm_days` 跨台风去重是否为有意设计**：注释与实现冲突，无文档/无测试可佐证 → 需作者确认（m3）。
3. **`path_width/point_size` 是"暂未接线"还是"已废弃"**：渲染器注释称它们是"像素兜底"（`paint_render.py:162`），但兜底分支在代码里不存在 → 需作者确认（M2）。
4. **`multireset` 三个选项的预期语义**：Ty4 原版的"多文件路径重置"具体行为未知，无参考实现可比 → 无法判断是漏实现（M3）。
5. **`2025wp.json` 是否应该保留**：旧语法脚本在新解析器下必然失败，是留档还是该删/该迁移 → 需作者确认（M5）。
6. **`config.json` 的正确基线**：两份文件均存在且互有增删，无法判断哪份是用户当前期望状态（m9）。
7. **`tests/script/` 作为生产脚本目录是否合理**：`SCRIPT_DIR` 指向测试目录（`script_dialog.py:40-41`），用户的脚本与测试样本共存；是否有意为之未确认。
8. **`_THRESHOLDS` 缺 `0→DB` 一档是否有意**：它只被用于画线（`intensity_chart.py:241`, `intensity_comparison.py:169`），所以目前无害；但若被复用为查表函数就会把 29 kt 以下错误染成 TD 色。
9. **`ace_engine` 局部刷新在"点被移出地理限制"时的完整性**：代码已处理"消失的年份"（`ace_engine.py:352-356`），但没有对应测试验证 `_ace_timeline_cache` 与 `yad` 的最终一致性。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| TrackPoint 全部字段与含义 | `app/typhoon.py:22-37` |
| 视图字段清单（为何有 `_v` 代理） | `app/typhoon.py:57-78`, `:185-204` |
| 2–4 字母方法别名 | `app/typhoon.py:214-220` |
| 正式报（official）判定 | `app/typhoon_data.py:98`、`app/paint_render.py:459-464` |
| ACE 单点公式与资格 | `app/typhoon_data.py:110-111`、`app/ace_engine.py:29-38` |
| ACE 年（南北半球切分） | `app/ace_engine.py:70-78` |
| ACE 地理限制三态 | `app/ace_engine.py:80-95` |
| ACE 缓存与局部刷新 | `app/ace_engine.py:324-388` |
| ACE 曲线时间轴（6 h 步长） | `app/ace_engine.py:299-322` |
| 活跃期 / 活跃日 | `app/ace_engine.py:194-219`, `:233-297` |
| points_time 单位（0.5 = 6 h） | `app/typhoon_data.py:186-188`、`app/utils.py` 的 `movement_speed_kt` |
| b-deck 格式探测 | `app/data_repo.py:60-78` |
| 整十分之一度 / 小数点即坏点 | `app/data_repo.py:301-331` |
| JTWC → simple-bdeck 转换与安全闸 | `app/data_repo.py:81-128` |
| 文件扫描去重（`*_ty.txt` 优先） | `app/data_repo.py:138-150` |
| `# NEW` 占位台风 | `app/data_repo.py:221-238`、`app/new_typhoon_dialog.py` 的 `_create` |
| 类别 → 颜色映射（app 色阶） | `app/data_repo.py:478-497` |
| Wiki / 2023 色板 | `app/constants/colors.py:33-62` |
| 洋区年度统计全部判据 | `app/statistics/season_stats.py:24-165` |
| 路径长度跨年切分 | `app/statistics/chart_helpers.py` 的 `compute_path_km_in_window` |
| 强度阈值线 / 填充带 | `app/statistics/shared.py:16-46` |
| 热力图径向核与色带 | `app/statistics/heatmap.py:96-256`, `:25-60` |
| 多台风强度对比坐标 | `app/statistics/intensity_comparison.py:90-241` |
| 多年度 4 个 Tab 的数据 | `app/statistics/multi_year_chart.py:46-117` |
| 脚本语法实现（唯一真相） | `app/script_engine.py:190-428` |
| README 脚本语法表 | `README.md:46-85` |
| 脚本状态机 5 态 | `app/script_engine.py:434-438`, `:536-690` |
| 绝对时间轴与缓动 | `app/script_engine.py:749-791` |
| repeat 展开上限 | `app/script_engine.py:43-44`, `:379-428` |
| 脚本扫描与简介 | `app/script_engine.py:804-840` |
| 脚本编辑器（撤销/IME/防抖） | `app/script_dialog.py` 的 `_TextArea` / `_flush_parse` |
| 绘画投影与地图裁剪 | `app/paint_render.py:189-197`, `:285-321` |
| Ty4 点型规则（圆/方/三角） | `app/paint_render.py:405-477` |
| EX 气压分级边界 | `app/paint_render.py:411-434` |
| 风圈与图例 | `app/paint_render.py:479-535` |
| 绘画参数 → 渲染器 | `app/paint_dialog.py:627-660` |
| 样条（单调 / 向心 CR） | `app/spline.py:138-223` |
| 弧长参数化 | `app/spline.py:228-257` |
| 表面缓存（LRU + 字节预算） | `app/cache_store.py:27-118` |
| 路径质量阈值与距离口径 | `app/track_quality.py:13-39` |
| 回放登陆判定（视图无关） | `app/playback_ctrl.py:281-303` |
| 时间跳转与年份钳制 | `app/time_jump.py` 的 `_jump` |
| 台风列表搜索/排序 | `app/ty_list.py` 的 `_matches` / `_sort_key_fn` |
| 报点列表分页 | `app/point_list.py:159-164` |
| 配置隔离（测试不得写用户配置） | `tests/conftest.py:1-40` |
