# 06 文档 / 验收方法论 / 工具脚本 / 测试 / 数据资产

> 探索者: teammate `evidence`(只读)。所有结论标注 `文件:行`;凡未实测的推断都显式写"推断"。
> 环境事实采信 `output/exploration/_BRIEF.md:9-25` 与 Lead 的两条广播更正:
> 真实运行时是 Windows 解释器 `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`(3.14.2, 已装 pytest 9.1.1 / pygame / netCDF4),
> 不是 WSL 的 `/usr/bin/python3`。**本报告所有"实测"数字都是在 Windows 解释器下跑出来的。**

## 一句话定位

项目的"工程纪律层"—— `docs/` 两份宪章 + `output/sim/_reference/` 34 篇口径文档与 6334 行轮次日志 + `tools/` 188 个脚本 + `tests/` 55 个测试,共同定义"**什么算通过**"以及"**哪些结论已经作废**"。

---

## 1. 清单与职责

### 1.1 文档层

| 文件 | 行数 | 职责 | git 状态 |
|---|---|---|---|
| `docs/TC模拟设计方案-v2.md` | 891 | 设计宪章 v2(10 章 + 附录 A–E,`docs/TC模拟设计方案-v2.md:1-8`) | **未跟踪** |
| `docs/agent.md` | 122 | 体验改进待办(S1 编辑渐变线 / P0-6 首启引导 / P2 三项),`docs/agent.md:1-8` | **未跟踪**(与 HEAD 里被删的根 `agent.md` **逐字节相同**) |
| `output/sim/_reference/ROUND_LOG.md` | 6334 | 逐轮日志(第 1 轮 → 轮次 164),唯一"过程真相" | 未跟踪 |
| `output/sim/_reference/LAUNCH_2026-09-25.md` | 328 | **对外口径的权威说明**,顶部是基线变更通告 | 未跟踪 |
| `output/sim/_reference/LAUNCH_CHECKLIST.md` | 113 | 上线收口清单(A 已完成 / B 待办 / B' 现行配置验收 / C 判读纪律) | 未跟踪 |
| `output/sim/_reference/STATUS.md` | 819 | 现状快照 + 目标完成度 + 未达标项 | 未跟踪 |
| `output/sim/_reference/NEXT_PLAN.md` | 1570 | 追加式"下一轮计划"(章节重复堆叠,见 `:5/:65/:90/:143`) | 未跟踪 |
| `output/sim/_reference/BDECK_FORMAT.md` | 223 | b-deck 12 列格式 / 命名 / 目录 / 搬家规则 | 未跟踪 |
| `output/sim/_reference/OBS_REFERENCE.md` | 70 | **观测基准表**(所有"观测值"的唯一出处) | 未跟踪 |
| `output/sim/_reference/DOC_AUDIT_R162.md` | 384 | **上一轮对 `_reference/` 自身的陈旧声明审计**(含逐条处置状态) | 未跟踪 |
| `output/sim/_reference/` 其余 md | 24 篇 | `VERIFY_*`(5)/`PERF_*`(3)/`INTENSITY_*`(7)/`TY4_*`(2)/`PHASE_*`(2)/`BORROW_FROM_WPAC.md` 等 | 未跟踪 |

`output/sim/_reference/` 共 **34 篇 md + ~30 个 json/txt 产物 + 6 张 png 组**(`ls output/sim/_reference | wc -l` = 164 项)。
其中 `ROUND_LOG.md` 单文件 393 681 B,是"追加式日志"(`BORROW_FROM_WPAC.md:128` 自述:全目录 28 份 md 合计 896 KB,新人要读五六份大文件才能拼出全貌)。

### 1.2 工具层(`tools/`,188 个 `.py` + 7 个 `.sh`/`.md`)

按前缀聚类(每族给出代表性脚本,族内其余脚本我按 docstring 归族):

| 族 | 数量 | 代表 | 职责 |
|---|---|---|---|
| `verify_*` | 4 | `verify_year.py` / `verify_forecast.py` | **主验收仪器**:跑一年数生成/发展/峰值分布/纬度 |
| `multi_year_check.py` | 1 | — | 多种子验收 + 逐种子原文落盘 + 断点续跑 |
| `summarize_acceptance.py` | 1 | — | **从产物重算**汇总(不信任跑动进程的汇总) |
| `acceptance_*` | 2 | `acceptance_check.py` / `acceptance_multi_seed.py` | 基态场 A–E 五项门禁 / 多种子强度验收 |
| `free_hash.py` | 1 | — | **位同判据**(重构后必须逐位不变) |
| `run_*` | 3 | `run_tests_perfile.py` / `run_tc_sim.py` / `run_enso_years.py` | 逐文件 pytest / 无头模拟 |
| `bdeck_summary.py` | 1 | — | 归档对账(直接数落盘文件) |
| `probe_*` | 19 | `probe_gen_cap.py` / `probe_lifecycle.py` | 诊断探针("为什么死/为什么生成这么多") |
| `diag_*` | 16 | `diag_genesis_lat.py` / `diag_mpi_ceiling.py` | 离线诊断(不推进物理) |
| `fetch_*` | 16 | `fetch_era5_day.py` / `fetch_cmems_global.py` | 数据抓取(CDS/CMEMS/AOML/NCEP/OISST) |
| `train_*` | 9 | `train_tc_genesis.py` / `train_env_warming.py` | 用观测标定生成率/强度/模态 |
| `build_*` | 9 | `build_base_d26_validated.py` / `build_terrain_dem.py` | 重建基态场与地形 |
| `render_*` | 8 | `render_sim_basins.py` / `render_month_panels.py` | 出对照图/三联图 |
| `make_*` | 6 | `make_heightmap.py` / `make_video.py` | 生成底图/视频 |
| `check_*` / `_check_*` | 12 | `check_intensity_calib.py` / `_check_settings_sync.py` | 门禁与体检 |
| `analyze_*` / `stat_*` / `fit_*` / `calib*` | 11 | `analyze_mpi_headroom.py` / `stat_rate_table.py` | 统计与标定 |
| `perf_*` | 4 | `perf_ab_r158.py` / `perf_baseline_r158.py` | 性能 A/B(带同进程交错与 A/A 噪声地板) |
| `_probe_*` / `_fix_*` / `_shot_*` 等一次性脚本 | ~26 | `_fix_9gb_r162.py` / `_probe_sections_r162.py` | 轮次专用、跑完即留 |

**工具层的 git 状态是极端异常**:`git ls-files tools/` 只有 **2 个文件**(`tools/make_terrain_map.py`、`tools/smoke_map.py`),
即 **186/188 个脚本未入库**;而 `.gitignore` 并没有忽略它们(`git check-ignore tools/acceptance_check.py` = NOT-IGNORED)。

### 1.3 测试层(`tests/`)

- `ls tests/` = 59 项,其中 **`test_*.py` = 55 个**(brief 里的"59 个测试文件"其实是目录条目数;另有 `conftest.py`、`__init__.py`、`script/`、`__pycache__`)。
- `tests/script/` = 6 个 json + `smoke_datamode.py`(被 `.gitignore:11` 的 `script/` 规则忽略)。
- **`tests/` 整个目录被 `.gitignore:12` 忽略**,但已有 11 个测试文件在 HEAD 里被跟踪(`git ls-files tests/` = 11),其中 5 个当前被修改。
  ⇒ 结论:忽略规则与被跟踪状态**冲突**,新增测试文件(如 `tests/test_panel_layer_row.py`、`tests/test_tylist_paging.py`)永远不会被 `git add -A` 收进去。

### 1.4 配置与依赖

| 文件 | 行数 | 说明 |
|---|---|---|
| `config.json` | 166 | 运行时设置(mlo/Mlo 视窗、`sp` 速度、`sim_*` 一整套模拟参数、`info_box_rows` 10 行) |
| `config.json.bak` | 167 | 手工备份(**未跟踪**),与 `config.json` 的差异见 §6 m3 |
| `requirements.txt` | — | **不存在**(见 §6 B2) |

---

## 2. 数据流 / 调用链

### 2.1 "轮次"工作流(项目真正的开发循环)

```
观察/用户报障
   └─> 一轮工作(改代码 / 量数据 / 做 A/B)
         ├─> tools/probe_*、diag_*、analyze_*        ← 先量,再改
         ├─> tools/verify_year.py --days 100          ← 单种子验收仪器 (verify_year.py:34-64)
         │      └─> tools/multi_year_check.py          ← 起 15 个子进程,逐种子落盘原文
         │             ├─ 源码指纹守卫                  ← multi_year_check.py:30-46, 176-183
         │             ├─ 逐种子写盘 + 即时 flush        ← multi_year_check.py:171, 193-195
         │             └─> output/sim/_reference/multi_year_<tag>.txt   (产物)
         ├─> tools/summarize_acceptance.py <产物>       ← 用"当前"parse_rec 重算,不信跑动进程
         ├─> tools/free_hash.py                         ← 与生成无关的改动必须逐位不变
         └─> tools/run_tests_perfile.py                 ← 逐文件回归(本机内存只有 1–3 GiB 可用)
   └─> 写 output/sim/_reference/ROUND_LOG.md 第 N 轮   ← 唯一过程记录
         └─> 汇总进 LAUNCH_2026-09-25.md / LAUNCH_CHECKLIST.md / STATUS.md
               └─> README.md 首段引用                ← README.md:5-11
```

### 2.2 基态场验收链(与台风统计验收**完全独立**的另一条链)

```
simulator/env/train_cache/era5/{day_*.nc, plev_*.nc, sstm_*.nc}   (实测存在, 738.9 MB)
simulator/env/train_cache/cmems/g2_*.npz                          (实测存在)
        │
        ├─> tools/acceptance_check.py:check_sst_monthly()   (A) ─┐
        ├─> tools/acceptance_check.py:check_sst_daily()     (B)  │ 阈值硬编码在 :9-13
        ├─> tools/acceptance_check.py:check_shear_daily()   (C)  │ A MAD≤1.0 / B ≤1.5
        └─> tools/acceptance_check.py:check_ohc_aoml()      (D/E)┘ C 相关≥0.9 / D ≥0.8 / E ≥0.7
                │
                ├─> 写 output/sim/_reference/ACCEPTANCE_AUTO.md  (acceptance_check.py:272-275)
                └─> 写 output/sim/_reference/acceptance_auto.json (:276-277)
```

### 2.3 归档对账链

```
sim_typhoon/<年>/<年><洋区>/*.dat (实时记录, 正式)
sim_typhoon/<年>/<年><洋区>_invest/*.dat
        └─> tools/sim_year.py:finalize_bdeck() 改名+搬家
              └─> tools/bdeck_summary.py 直接数落盘文件 → BDECK_SUMMARY.txt
                    └─ 一致性检查: 正式目录里字母命名文件应为 0  (bdeck_summary.py:90-104)
```

---

## 3. 核心机制详解

### 3.1 这个项目有 **6 种互不相同**的"通过"定义

这是本报告最重要的发现:项目里"通过"不是一个概念,而是六套独立判据。混用会得到假象,`LAUNCH_2026-09-25.md:280-285` 专列了"口径红线"。

| # | 判据 | 实现位置 | 定义"通过"的方式 | 本轮实测 |
|---|---|---|---|---|
| 1 | 基态场数值阈值 | `tools/acceptance_check.py:228-270` | A: MAD ≤ 1.0 °C;B: ≤ 1.5 °C;C: 相关 ≥ 0.9;D: 相关 ≥ 0.8;E: 相关 ≥ 0.7 | **已完整复现:A/B/C/D/E 五项全 PASS,耗时 779.4 s**(把 `REF` 重定向出工作区后跑的,见 §5.1) |
| 2 | 气候统计"范围跨过" | `tools/summarize_acceptance.py:39-53, 76-85` | **若模型多种子范围跨过观测值 ⇒ 无显著差别**(不取单点差) | **已复现**,见 §3.2 |
| 3 | 位同判据 | `tools/free_hash.py:21-49` | 与生成无关的重构后,SHA256 必须**逐位等于**基线;数据/解析两分支各一条 | 未跑(会改判据语境,见 §7) |
| 4 | 回归套件 | `tools/run_tests_perfile.py:44-57` | 逐文件 pytest,末行含 `passed` 且无 `failed/error` 且 rc=0 | **发现真实失败**,见 §3.6 |
| 5 | 归档对账 | `tools/bdeck_summary.py:90-104` | "正式目录字母命名文件 = 0" + 摘要与落盘数一致 | **已复现:483 系统 / 338 命名 / 0**(与 `output/sim/2025/BDECK_SUMMARY.txt` 逐字相同) |
| 6 | 单种子拒绝 | `tools/acceptance_multi_seed.py:86-88` | 成功种子 < 3 直接打印"不足以判定,不给结论" | 未跑(每次 60–150 天模拟) |

### 3.2 判据 #2 的量化定义(我完整复现了轮次 150 的对外口径)

`tools/summarize_acceptance.py:39-53` 的 `METRICS` 就是全项目的验收表:

```python
('grate', '年内发展率%', (48.0, 48.0), False),
('maxlat','最高纬中位(度)', (25.0, 26.0), True),
('p28',  '到过>=28 占比%', (40.0, 45.0), True),
('dlat', '净向极位移(度)',  (10.3, 13.3), True),
('life', '寿命中位(h)',     (180.0, 222.0), True),
('s100', '100-129 占比%',   (5.3, 11.1), True),
('s130', '130+ 占比%',      (12.3, 13.9), True),
('p50',  '峰值 P50',        (75.0, 75.0), False),
('p90',  '峰值 P90',        (128.0, 130.0), True),
('p95',  '峰值 P95',        (147.0, 155.0), True),
```

判读函数 `summarize_acceptance.py:76-85`:`范围跨过观测 ⇒ "无显著差别";整体落在区间内 ⇒ "落在区间内";整段低于/高于 ⇒ "**整体低于/高于观测**"`。

**我的独立复现**(只读,命令见 §5):

```
== multi_year_sb60g_nh_15seed.txt: 15 个种子 ==
   年内发展率%   均值 51.9  范围 [42.0, 59.0]   n=15/15  观测 48.0       范围跨过 => 无显著差别
   最高纬中位(度) 均值 25.7  范围 [24.3, 27.2]   n=15/15  观测 25.0-26.0  落在区间内
   到过>=28 占比% 均值 34.3  范围 [24.0, 44.0]   n=15/15  观测 40.0-45.0  落在区间内
   净向极位移(度) 均值 11.1  范围 [9.5, 13.2]    n=15/15  观测 10.3-13.3  落在区间内
   寿命中位(h)   均值 101.4 范围 [84.0, 120.0]  n=15/15  观测 180.0-222.0 **整体低于观测**
   100-129 占比% 均值 26.9  范围 [22.0, 36.0]   n=15/15  观测 5.3-11.1    **整体高于观测**
== 两窗口年量 ==
   每窗口均值 68.3 + 68.5 = 136.7 个/200 天 => **年生成 250**(目标 250)
```

⇒ 与 `ROUND_LOG.md:4724-4735`、`LAUNCH_2026-09-25.md:13-31`、`LAUNCH_CHECKLIST.md:67-78` 的表**逐格一致**。
**README 首段引用的两条缺口(寿命 101h vs 180-222h、中段 26.9% vs 5.3-11.1%)就是这么测出来的**:
`tools/verify_year.py` 逐种子打印 → `tools/multi_year_check.py` 落盘 15 份原文 → `tools/summarize_acceptance.py` 重算 → 手抄进 LAUNCH/CHECKLIST/README。
证据文件:`output/sim/_reference/multi_year_sb60g_nh_15seed.txt`(29 460 B,15 个 `>>> seed` 块)与 `multi_year_sb60g_sh_15seed.txt`(26 011 B)。

### 3.3 指标是怎么从 6h 轨迹里算出来的(`tools/verify_year.py`)

- **真年生成量必须绕开保留窗**:`core` 会剔掉死亡超过 2880 h 的台风,于是 `len(sim['tcs'])` 在 ~120 天处**饱和**。
  `verify_year.py:66-73` 把这个坑写进注释(实测 365 天只报 196 个,真值约 600),
  实现在 `:75-98`:**给 `V.make_tc` 包一层计数器**取真值,并逐存活步记录峰值(`:87-95`)。
- **"一生最高纬/到过≥28/净向极/寿命"四项显式跳过南半球**(`:125-126` `if sum(la)/len(la) < 0: continue`),
  所以 `summarize_acceptance.py:19-23` 明确写"1 月窗口这四行不可用"。
- **匹配子总体**:模型有 ~46% 的系统峰值 <34 kt,而观测里 <34 kt 只占 0–10%,所以 `:182-197` 同时输出
  "峰值≥34kt"与"<34kt"两个子集,并规定**以匹配子总体为主判据**(注释里给了实测差:全部 20.4% vs 子集 31.0%)。
- **档位占比口径**(`:161-171`):100-129 用 `5.3-11.1%`(单年多海盆),同时把 `17%/18%`(WP-only 多年)**标注为不可直接比**。
- **向极速度剖面**(`:203-225`):按 5 个纬度带累计 `(la1-la0)*110.57/6.0/1.852`(kt),每带 n<20 打 `n.a.`。

### 3.4 验收解析器为什么脆弱(`tools/multi_year_check.py:parse_rec`)

`parse_rec`(`:49-101`)完全靠**中文字符串锚点**从 `verify_year` 的 stdout 里抠数:
`'年内累计'`(:62)、`r'生成\s*(\d+)\s*个'`(:72)、`r'34kt\+\s*(\d+)\s*个'`(:75)、`'一生最高纬度'`(:95)……
注释里记着两次事故:锚点写 `'年内累计生成'` 导致 `gen/gdev/grate` **一直是空字段**(:66-71);
观测基准行也含 `P50/P90`,会把模型值覆盖(:54-58,过滤条件从"观测基准/参考:"放宽到"任何含'基准'的行")。

`summarize_acceptance.py:1-24` 的整个存在理由就是这件事:**不信任跑动进程内存里的解析结果**,
只读产物原文 + 用当前 `parse_rec` 重算。这是一条正确且罕见的工程纪律。

### 3.5 ★断点续跑机制是坏的,而且会**截断**权威产物(BLOCKER,详见 §6 B1)
`multi_year_check.py` 的续跑设计(`:156-160`)是为了对抗"本机可用内存只有 1–3 GiB、15 种子长验收会被整进程杀掉"。
但代码路径实际断成两半:

- `:193` 把**起跑行写进产物**:`fh.write('>>> seed %s\n' % sd)`;
- `:203` 把**完成行只 print 到 stdout**:`print('<<< seed %s 用时 %.0f s' % ...)`,**没有 `fh.write`**。
- 而"已完成"的判据是两者都在产物里(`:104-122` `find_done`:`a='>>> seed %s\n'`, `b='<<< seed %s 用时'`)。
- 产物打开模式:`:171` `fh = io.open(outp, 'a' if done else 'w', ...)`。

⇒ 我用 `find_done()` 对三份产物做了**只读**验证(结果见 §5),得到 `find_done=0`;
⇒ `done` 为空 ⇒ `:171` 以 **`'w'` 打开并截断**现有产物,再从头跑 15 个种子(轮次实测速率 **198–269 s/种子**,两窗口约 **1 小时 40 分**,`ROUND_LOG.md:4677`)。

### 3.6 ★当前工作区有**真实失败**的测试(判据 #4)

`tools/run_tests_perfile.py` 的判定(`:44-57`)本身很宽松(只看末行),但测试真的会红。实测:

```
tests/test_motion_smoothness.py ... 2 failed, 7 passed
FAILED tests/test_motion_smoothness.py::TestTrackCameraConvergence::test_converges_and_then_stops
       tests\test_motion_smoothness.py:255: assertLess(abs(dx), 0.3)  -> AssertionError: 3.0 not less than 0.3
FAILED tests/test_motion_smoothness.py::TestTrackCameraConvergence::test_clamped_axis_still_settles
       tests\test_motion_smoothness.py:281: assertLess(abs(dx), 0.3)  -> AssertionError: 1.0 not less than 0.3
```

两个用例测的是**镜头跟踪收敛**:连跑 300 帧(1/60 s)后残余偏差应 <0.3 px(`tests/test_motion_smoothness.py:242-259`),
实际残余 3.0 px / 1.0 px ⇒ **跟踪不收敛**。
**Lead 本轮独立复跑确认:**`2 failed / 7 passed / 1.41 s`,失败断言与实得值与我完全一致(见交接摘要)。
**归属推断**:`tests/test_motion_smoothness.py` 自 HEAD 起**未被修改**(它不在 `git status` 的 5 个被改测试里),
而被它测的 `app/map_mgr.py` 当前带着 **+642/-540** 未提交改动 —— 即"改坏了已入库的测试"最可能来自未提交的工作(推断,未回退验证)。

### 3.7 文档层的自我纠错机制(`DOC_AUDIT_R162.md`)

这个仓库有一套**自己的文档审计**:`DOC_AUDIT_R162.md` 由"只读 verifier"写成,列了 8 条"会误导决策"+ 5 条"只是难看",
每条给"位置与原文 / 为什么过期 / 与哪条更正冲突 / 建议怎么改 / 严重度",并规定只有 `grep/sed/md5sum/stat` 与两次系统读数可执行(`DOC_AUDIT_R162.md:7-18`)。
我抽查了它的**处置状态表**(`:25-42`)并逐条回文核对,**全部落地**:

| 它说已改的 | 我核到的现文 |
|---|---|
| §1.1 已撤回的 1.48x/1.13x/1.68x 加标记 | `PERF_BASELINE_R158.md:93`、`:103`、`:123`、`:133` 都有 ⚠ 撤回注 |
| §1.5 冻结 sha1 加注 | `PERF_BASELINE_R158.md:203`、`:209`、`:308` 都注明"只对 `080069cb…` 有效" |
| §1.6 "9GB" 全部改成 15.74 GiB | `PERF_BASELINE_R158.md:16-17`、`:279`;全目录再 grep `9GB` 只剩 DOC_AUDIT 自身与这两处更正句 |
| §1.7 "8 逻辑核" 改成 24 | 同上 |
| 留在仓库的一次性修复脚本 | `tools/_fix_9gb_r162.py`(docstring:"轮次 162: 把 4 个 .py 里过期的'本机 9GB'改成实测口径") |

⇒ **这套"审计 → 处置 → 加标记"的闭环是这个仓库最值得肯定的工程实践**,它自证了"文档不可信"这个问题是**被记录、被追踪**的。

---

## 4. 输入输出资产

### 4.1 体量盘点(实测,`du -sh` / `os.walk`)

| 路径 | 文件数 | 体量 | 是什么 | git 状态 |
|---|---|---|---|---|
| `simulator/env/` | — | **2111.9 MB** | `library/` 470 文件 1093.8 MB(逐年份逐月场)、`train_cache/` 472 文件 738.9 MB(ERA5/CMEMS/AOML 原始+缓存)、`climate/` 18 文件 150.8 MB、`model/` 32 文件 86.4 MB、`generated/` 17 文件 39.7 MB | **大部分被 `.gitignore:27-35` 忽略**,但 `env/model/`、`env/train_cache/` **未忽略**(`git check-ignore` = NOT-IGNORED) |
| `assets/` | 7538 | 542.1 MB | `ty_history/` 7340 文件(历史路径库,被 `.gitignore:40` 忽略)、`SMCY/` 110、`simple/` 67、`sound/` 13、`font/` 5 | 混合 |
| `map/` | 4 | 156.7 MB | `heightmap.png` 61.9 MB(真 DEM,未跟踪)、`land2.png` 45.9 MB(未跟踪)、`map.png` 48.2 MB、`land.png` 547 KB | 前两个未跟踪 |
| `output/` | 930 | 150.1 MB | `sim/` 887 文件(**全部验收证据**:`_reference/` 164 项、`2025/` 归档、`_archive/`)、`picture/` 24、`ref_20260914/` 9、`ref_grid/` 6、`logs/` 3 个 crash log | **整目录未跟踪且未忽略** |
| `typhoon/` | 210 | 5.3 MB | 观测最佳路径(`2028/` 93、`2025-2026sh/` 41、`2025wp/` 38、`pign/` 36) | 216 个 `M`(CR 噪声) |
| `sim_typhoon/` | 460 | 1.1 MB | 模拟 b-deck 实时记录(`2025/` 239、`2100/` 221) | 未跟踪 |
| `docs/` | 2 | 56 KB | 设计宪章 + 待办 | 未跟踪 |
| `tools/` | 195 | 2.2 MB | 188 `.py` + 7 其它 | 2/188 已跟踪 |
| `tests/` | 66 | 3.4 MB | 55 `test_*.py` + conftest + script | 11 已跟踪 |

### 4.2 关键产物对照

| 路径 | 格式 | 由谁产生 | 被谁消费 |
|---|---|---|---|
| `output/sim/_reference/multi_year_sb60g_{nh,sh}_15seed.txt` | 文本(CRLF,15 个 `>>> seed` 块) | `tools/multi_year_check.py:193-195` | `tools/summarize_acceptance.py:56-73`(**权威验收数字的唯一原始证据**) |
| `output/sim/_reference/acceptance_auto.json` | JSON(215 行,含 `sst_monthly/sst_daily/shear_daily/ohc_aoml` 四组) | `tools/acceptance_check.py:276-277` | `ACCEPTANCE_AUTO.md` 的机器可读版 |
| `output/sim/_reference/ACCEPTANCE_AUTO.md` | Markdown(66 行,5 个 PASS) | `tools/acceptance_check.py:272-275` | 人读 |
| `output/sim/2025/BDECK_SUMMARY.txt` | 文本(13 行) | `tools/bdeck_summary.py:107-111`(`--write`) | `output/sim/2025/README.md` |
| `output/sim/_reference/validate_ohc_aoml.json` | JSON(2 652 B) | `tools/validate_ohc_aoml.py` | `acceptance_check.py:172-189`(D/E 段直接读它) |
| `output/sim/_reference/accept_ms.json` | JSON(3 种子 / 60 天) | `tools/acceptance_multi_seed.py --json` | **无**(已过期,见 §6 M4) |
| `output/sim/_reference/{amode_*,tracks_*,phase_duration_stats.json,intensity_lifetime.json}` | JSON/NPZ | 各诊断脚本 | 已不引用(遗留) |

---

## 5. 如何运行 / 如何验证

### 5.1 我实际跑通的(只读,全部在 Windows 解释器下)

```bash
PY=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe
cd /mnt/g/气象/Typhoon-sim

# ① 单文件 pytest(快)
$PY -m pytest tests/test_ace_engine.py -q --no-header        # 19 passed in 1.17s
# ② 验收判据重算(纯解析,不跑模拟) —— 复现了 README 引用的两条缺口
$PY -c "import sys;sys.path.insert(0,'tools');sys.path.insert(0,'.');\
import summarize_acceptance as S;sys.argv=['x','output/sim/_reference/multi_year_sb60g_nh_15seed.txt',\
'output/sim/_reference/multi_year_sb60g_sh_15seed.txt'];S.main()"
# ③ 续跑判据只读验证(证明 find_done=0,不写任何文件)
$PY -c "import sys;sys.path.insert(0,'tools');import multi_year_check as M;...M.find_done(txt,seeds)"
# ④ 归档对账(不带 --write 纯打印)
$PY tools/bdeck_summary.py output/sim/2025 --year 2025      # 483 系统 / 338 命名 / 字母名 0
# ⑤ 失败用例的完整回溯
$PY -m pytest tests/test_motion_smoothness.py -q --no-header --tb=short
```

> ⚠ **`acceptance_check.py` 默认会写工作区**(`:195` 默认输出 `output/sim/_reference/ACCEPTANCE_AUTO.md`,且 `:276` 无条件写 `acceptance_auto.json`)。
> 铁律要求只读,所以我把 `ACCEPTANCE_CHECK.REF` monkeypatch 到 `G:\tmp\acc`(并先把 `validate_ohc_aoml.json` 复制出去)才敢跑。**任何"只读探索"跑这个工具都会污染验收产物。**

### 5.2 本环境跑不了的

| 项 | 原因 |
|---|---|
| `python main.py` | 会开真窗口;Lead 明确禁止 |
| 365 天窗口验收 | 本机 OOM(仓库自己在 `LAUNCH_2026-09-25.md:243-245` 写明"年尺度在本机跑不起来") |
| 全量 `pytest -q` 裸跑 | `tools/run_tests_perfile.py:1-7` 记录"整轮跑会被 `MemoryError` 打成 INTERNALERROR,连失败清单都打不出来" |
| CDS/CMEMS/AOML 抓取类工具 | 需要网络与限流等待(仓库记录 CDS 每 10–15 分钟放一个请求,`_reference/README.md:66-69`) |
| GUI 截图类工具(`_check_playable.py`、`_shot_*`、`ty4_render.py --show`) | 需要 pygame 窗口 |

### 5.3 逐文件回归的实测覆盖(本轮,每文件硬超时 + SIGKILL 兜底)

**方法**:`for f in tests/test_*.py; do timeout -k 5 <T> $PY -m pytest "$f" -q --no-header -p no:cacheprovider --tb=line; done`
(注意:本轮全程有**其它 teammate 的 pytest / 模拟进程**同时运行,`ps` 曾同时看到 **15 个 `python.exe`**,所以耗时被放大。)

> ⚠ **超时阈值的坑(实测教训)**:第一遍用 180 s,`tests/test_daily_instant.py` 与 `tests/test_sim_mode.py` 被标成 TIMEOUT。
> 单独重跑后 `test_daily_instant.py` **11 passed(293.77 s)** —— 不是挂,是**真的慢**:
> 最慢用例 `test_eddy_amplitude_is_peak_not_std` 一个就 **199.98 s**。
> 而仓库自己在 `LAUNCH_2026-09-25.md:174-175` 就写了"最慢 `test_sim_mode_ref` 267s / `test_sim_throttle` 126s / `test_sim_mode` 92s"。
> ⇒ **单文件超时必须 ≥400–600 s**,否则"超时"会被误读成"失败"。我把阈值改成 600 s 重跑(结果见下表)。

**结果(全部 55 个 `test_*.py`)**

| 项 | 值 |
|---|---|
| 覆盖文件数 | **54 / 55 有判定**(唯一缺口 `test_sim_mode.py`,见下) |
| 通过(rc=0) | **53 个文件** |
| **失败(rc=1)** | **1 个文件:`tests/test_motion_smoothness.py`(2 failed / 7 passed)** |
| 用例合计 | **828 passed / 2 failed / 2 skipped** |
| 未取到完整判定 | **1 个:`test_sim_mode.py`**(180 s 上限下 TIMEOUT;仓库自记 92 s) |

> ⚠ 仍然**不能**得出"全仓 0 failed":已确证有 1 个文件红。
> **本轮结论与文档头号声明冲突**:`LAUNCH_2026-09-25.md:174` 写"全量回归 **620 passed / 0 failed**",
> 而在**当前工作区**上至少有一个已跟踪、未修改的测试文件是红的(§3.6);
> 且用例总数已从 620 涨到 **828**(第 151–164 轮新增了大量回归锁),该数字本身也已过期。

#### 5.3.1 逐文件结果(截至本轮,合并其他 teammate 已实测的)

| 文件 | rc | 耗时 | pytest 末行 |
|---|---|---|---|
| `test_ace_engine.py` | 0 | 7 s | 19 passed |
| `test_bdeck_format.py` | 0 | 9 s | 43 passed |
| `test_cache_store.py` | 0 | 7 s | 11 passed |
| `test_config.py` | 0 | 7 s | 5 passed |
| `test_control_panel.py` | 0 | 7 s | 15 passed |
| `test_daily_instant.py` | 0 | **293.8 s** | 11 passed(单项 `test_eddy_amplitude_is_peak_not_std` **200.0 s**) |
| `test_edit_features.py` | 0 | 7 s | 45 passed |
| `test_envmodel_cache_hardening.py` | 0 | 9 s | 15 passed |
| `test_envmodel_solve_memo.py` | 0 | 24 s | 30 passed |
| `test_fx_sound.py` | 0 | 6 s | 27 passed |
| `test_landfall_view_invariance.py` | 0 | 20 s | 8 passed |
| `test_map_resize.py` | 0 | 4 s | 4 passed |
| **`test_motion_smoothness.py`** | **1** | 1.4 s | **2 failed, 7 passed** |
| `test_multi_year_tools.py` | 0 | 4 s | 5 passed |
| `test_normal_tracking.py` | 0 | 4 s | 18 passed |
| `test_ocean_area.py` | 0 | 4 s | 4 passed |
| `test_ocean_compact.py` | 0 | 4 s | 2 passed |
| `test_paint.py` | 0 | 41 s | 15 passed(app-feat 另测 69.6 s) |
| `test_pan_reuse.py` | 0 | 22 s | 8 passed |
| `test_panel_layer_row.py` | 0 | 12 s | 5 passed |
| `test_path_window.py` | 0 | 4 s | 5 passed |
| `test_phase_ladder.py` | 0 | 5 s | 52 passed |
| `test_real_source.py` | 0 | 6 s | 9 passed |
| `test_render_geom.py` | 0 | 4 s | 16 passed |
| `test_render_layers.py` | 0 | 4 s | 9 passed |
| `test_seam_render.py` | 0 | 5 s | 8 passed |
| `test_season_ctrl.py` | 0 | 4 s | 4 passed |
| `test_sim_climate.py` | 0 | 38 s | 22 passed |
| `test_sim_env.py` | 0 | 28 s | 7 passed |
| **`test_sim_mode.py`** | 124 | 180 s(上限) | **TIMEOUT**,未取到完整判定(自记 92 s) |
| `test_sim_mode_data.py` | 0 | 28 s | 4 passed |
| `test_sim_mode_ref.py` | 0 | **204 s** | 26 passed in 197.66 s |
| `test_sim_modes.py` | 0 | 9 s | 7 passed |
| `test_sim_settings.py` | 0 | 43 s | 14 passed |
| `test_sim_step_control.py` | 0 | 5 s | 20 passed |
| `test_sim_throttle.py` | 0 | 53 s | 5 passed |
| `test_sim_time_model.py` | 0 | 114 s | 19 passed |
| `test_sim_typhoons.py` | 0 | 34 s | 14 passed |
| `test_simcore_batch.py` | 0 | 16 s | 11 passed |
| `test_simcore_contours.py` | 0 | 8 s | 5 passed |
| `test_simcore_motion.py` | 0 | 17 s | 24 passed |
| `test_simcore_ocean.py` | 0 | 12 s | 10 passed |
| `test_simcore_perf_r158.py` | 0 | 6 s | 15 passed |
| `test_simcore_rewind.py` | 0 | 4 s | 30 passed |
| `test_simcore_ridge.py` | 0 | 5 s | 28 passed |
| `test_simcore_steer_friction.py` | 0 | 4 s | 38 passed, **2 skipped** |
| `test_simcore_synoptic.py` | 0 | 12 s | 15 passed |
| `test_simcore_terrain.py` | 0 | 28 s | 15 passed |
| `test_snap_grid.py` | 0 | 4 s | 5 passed |
| `test_trackpoint.py` | 0 | 4 s | 11 passed |
| `test_tylist_paging.py` | 0 | 13 s | 6 passed |
| `test_track_quality.py` | 0 | — | 17 passed(app-feat) |
| `test_typhoon.py` | 0 | — | 23 passed(app-feat) |
| `test_spline.py` | 0 | — | 20 passed(app-feat) |
| `test_subarea_stats.py` | 0 | — | 17 passed(app-feat) |

**耗时分布(实测,回答"哪个最慢")**:294 s(`test_daily_instant.py`,单项 200 s)≈ 204 s(`test_sim_mode_ref.py`)>
114 s(`test_sim_time_model.py`)> 53 s / 43 s / 41 s / 38 s / 34 s / 28 s×2 / 24 s / 22 s > **其余 30 个文件全部 <20 s**。
⇒ 全套 55 个文件的墙钟时间**基本由 4 个文件决定**(`daily_instant`、`sim_mode_ref`、`sim_time_model`、`sim_mode`);
这也解释了为什么"整轮裸跑"会撞内存/超时。

> ⚠ 与 `LAUNCH_2026-09-25.md:174` 的"**620 passed / 0 failed**"对比:本轮是 **828 passed / 2 failed**(覆盖 54 个文件)。
> 数字涨了是**正常的**(第 151–164 轮持续加回归锁);**2 failed 才是问题**。



### 5.4 仓库自检门禁脚本(退出码语义)

`tools/` 里 35 个脚本带 `sys.exit`/`raise SystemExit`。真门禁(0=PASS)很少,代表作:

| 脚本 | 门禁语义 | 本轮状态 |
|---|---|---|
| `tools/acceptance_check.py` | 5 项 PASS/FAIL(`:228-270`) | **实测 A/B/C/D/E 全 PASS(779.4 s)** |
| `tools/bdeck_summary.py` | "正式目录字母命名 = 0" | **实测 PASS(0)**,483 系统 / 338 命名 |
| `tools/summarize_acceptance.py` | 范围跨过判读(**无门禁**) | **实测复现轮次 150 全部数字** |
| `tools/_check_settings_sync.py` | 配置镜像一致性 | Lead 实测 **PASS(rc=0)** |
| `tools/_audit_settings_coverage.py` | 覆盖报告(不设门禁,rc 恒 0) | Lead 实测 missing=58 / ghost=6(ghost 是工具自身误报) |
| `tools/free_hash.py` | 哈希比对(**靠人眼看**,不设 exit code) | 未跑(见 §7) |
| `tools/run_tests_perfile.py` | 汇总"通过合计 / 有问题的文件",**不设 exit code** | 见 §5.3 |

⇒ 观察:**这个仓库的"门禁"几乎全部不返回非零退出码**,不能接 CI。"通过/失败"要靠人读输出。

### 5.5 本环境的四个坑(都是实测踩到的,写下来省下一个人的时间)

1. **DSH 的 bash 工具会在执行前把命令串里的 `$NAME` 提前展开**(未知变量变空串)。后果:
   - `for f in a b; do echo "$f"; done` 打印空;
   - 用 `bash -c` 内嵌 heredoc 写脚本时,脚本里的 `$f`/``$(date)`` 会被**写坏成空**,我因此得到一个
     `cd "" || exit 1` 的坏脚本(`timeout: failed to execute process`)。
   - ⇒ 结论:**临时脚本一律用 `write` 工具落盘再 `bash 路径` 执行**,不要在 bash 里 heredoc 生成脚本;诊断性遍历用 `python3 -` 或落盘脚本。
2. **WSL 侧 `/tmp` 会在 WSL VM 重启后清空**。本轮真的发生了:`uptime` 显示 `up 0 min`,我的
   `/tmp/pytest_results*.txt` 全部消失、后台作业被杀(exit code 1 且无输出)。
   ⇒ 结论:后台长任务的**日志要写到 Windows 侧路径**(`/mnt/g/tmp/...`),否则跑完也没了。
3. **Windows 解释器的中文输出是 GBK**,经 WSL 管道会乱码;`... | iconv -f GBK -t UTF-8` 或
   `PYTHONIOENCODING=utf-8` 可解(Lead 已提示,实测有效;但 pytest 的英文统计行不受影响)。
4. **★WSL interop + `timeout` + 命令替换 = 可能永久挂住**。我在 `$(timeout -k 5 600 $PY -m pytest ...)`
   里跑一个 Windows exe 时,`timeout` 杀掉的是 WSL 侧的 `/init` 包装,而**真正的 Windows 进程还活着并握着管道的写端**
   ⇒ 命令替换等不到 EOF,`timeout` 形同虚设,整个循环卡死(实测:第一个文件 `test_sim_mode_ref` 卡了 **15 分钟以上**无输出,`ps` 里 pytest 计数为 0 而脚本仍在等)。
   ⇒ 结论:跨 WSL 重活**不要用命令替换包 `timeout`**;改成 `timeout -k 5 N cmd > log 2>&1`(重定向到文件,不走管道),
   或用 `blender_rt`/`nohup` 式的独立进程 + 轮询日志。这也是我"20 个测试文件未覆盖"的直接原因。



---

## 6. 观察到的问题 / 风险 / 不一致

### BLOCKER

**B1. `--resume` 断点续跑失效,且会把权威验收产物截断。**

- 现象:`find_done()` 在三份产物上都返回 0;`:171` 因此在 `done` 为空时以 `'w'` 打开产物。
- 证据:`tools/multi_year_check.py:104-122`(判据要起跑行 **+** 完成行)、`:193`(**只有**起跑行写进产物)、`:203`(完成行**只 print**)、`:171`(`'a' if done else 'w'`)。
- 实测(只读):`multi_year_sb60g_nh_15seed.txt` → `>>>=15  <<<= 0  find_done=0`;`multi_year_sb60g_sh_15seed.txt` → 同上;`final3_nh_15seed.txt`(`<<<` 有 15 行)→ `find_done=0`(旧版把 stdout 形态 `>>> seed X ...` 写进了产物,锚点 `>>> seed X\n` 不匹配)。
- 影响:`ROUND_LOG.md:4674-4677` 把"`>>>` 5 个 / `<<<` 0 个"归因为**并发争用**;按代码看更直接的原因是**完成行从来没被落盘**。后果是双重的:① 续跑等于从头跑(1 h 40 min/窗口);② 一旦跑,`'w'` 会把 15 种子原文**清空**——而那是 `LAUNCH_2026-09-25.md` 全部数字的唯一原始证据。
- 严重度:BREAKER(证据销毁风险)。修法:给 `:203` 加 `fh.write`,并让 `find_done` 兼容旧形态;或把 `'a' if done else 'w'` 改成永不截断。

**B2. README 的"如何运行"整段是死的(`README.md:15-18, 98-118`)。**

| README 行 | 写的东西 | 磁盘现实 | 备注 |
|---|---|---|---|
| `:16` | `pip install -r requirements.txt` | **`requirements.txt` 不存在** | 无依赖声明,新人无法复现环境 |
| `:101,:102,:113` | `python simulator/run.py --seed/--headless` | **不存在**(git 里是被删跟踪文件) | 三条命令全部无效 |
| `:109` | `simulator/wrf/run_wrf.ps1`、`simulator/wrf/README.md`、产物 `simulator/env/wrfdir/` | 三者**全不存在** | 整个"内核A 真 WRF 精算"路径已从磁盘移除 |
| `:109` | 内核A 的"数据驱动回落"缓存 `simulator/env/realdir/` | 目录在,但**只有 1 个日期文件**(`20260811.npz`) | README 称"逐日缓存" |
| `:118` | "移植自 `typhoon-sim.html` 参考实现" | **`*.html` 不存在**;`.gitignore:15` 的 `.ref_v4f/` 目录也不存在 | `simulator/simcore/core.py:4` 已自认"最初打算逐行移植,实测效果不好" |
| `:89,:96` | 路径图输出到 `picture/` | 根目录无 `picture/`;实际是 `output/picture/`(24 个 png) | 路径少了一级 |
| `:25` vs `:116` | `H` = "切换模式(正常/季节/编辑)" vs "切到「模拟」" | **同一文件内自相矛盾** | 至少一处已过期 |
| `docs/agent.md:100` | 待办 19 的"位置:`simulator/run.py`" | 同上,文件不存在 | 待办不可执行 |

- 证据:`README.md:16`、`:25`、`:89`、`:96`、`:101-102`、`:109`、`:113`、`:116`、`:118`;`docs/agent.md:100`;
  磁盘核对用 `os.path.exists` 逐个判(见 §5.1 的脚本思路);git 侧:`git status` 里这 29 个文件是 ` D`(删除但已跟踪)。
- 影响:README 是唯一入口文档,而它的运行段、架构段、参考实现段**全部失效**;真正的入口 `python main.py` 与 `typhoon_sim.bat`(内容 `python main.py`)反而没写进 README 的"运行"节。

**B3. 16.9 天的未提交工作 + 未跟踪且未忽略的 `docs/`+`output/`+186 个 tools 脚本 = 一次 `git checkout/clean` 就全没。**

- 证据(实测):
  - `git log -1` = `4e4148f 2026-09-15 02:05:01 +0800`;当前 `2026-10-01` ⇒ **16.9 天无提交**;
  - `git rev-list --count origin/main..HEAD` = **0**,`HEAD..origin/main` = **0** ⇒ origin 与 HEAD 同步,**所有本地工作既没提交也没推送**;
  - `git status --porcelain` = **535 行**:` D` 29(已跟踪但删除)、` M` 280、`??` 226;`git stash list` = **0**(没有任何备份);
  - `git add -A --dry-run` = **2404 条**。
- 关键细化(**这条改变了风险判断**):309 个"变更文件"里 **236 个只差 CR-at-EOL**(`git diff --ignore-cr-at-eol` 后从 diff 里消失),**真正有内容改动的只有 73 个**。
  也就是说 `git diff --shortstat` 报的 "309 files changed, 50302 insertions(+), 48135 deletions(-)" 里绝大多数是**行尾噪声**;`.gitignore` 本身就是纯 CR 变更(53 行全变、语义零变化)。
  真正的大改是:`simulator/simcore/core.py` +3864/-285、`app/ty_sim_mixins/sim_mode_mixin.py` +2258/-630、`simulator/simcore/render.py` +1648/-946。
- 未跟踪且**不被 `.gitignore` 覆盖**的:整个 `docs/`(含 48 KB 设计宪章)、整个 `output/`(150.1 MB,含全部验收证据与 `ROUND_LOG.md`)、`tools/` 186 个脚本、`app/` 7 个新模块、`simulator/simcore/` 9 个新模块(含 `daily.py`/`phase.py`/`rewind.py`/`terrain.py`)、`sim_typhoon/`、`config.json.bak`、4 张根目录 png。
- 附带:`git worktree list` 有一个 **prunable** 外部工作树(`opencode/curious-comet`,来自 `C:/Users/Yukioto/.local/share/opencode/worktree/...`)。
- 影响:`docs/` 与 `output/` 是"知识资产但不是版本化资产";而 `docs/agent.md` 与 HEAD 里被删的根 `agent.md` **逐字节相同**(sha1 `370138831f3e`,7479 B)—— 说明这不是新建,是**一次未落账的目录搬迁**:`git checkout .` 会让旧路径复活而 `docs/agent.md` 消失。
- 严重度:BLOCKER(可恢复性)。

### MAJOR

**M1. 同一批权威文档之间数字互相矛盾(至少三处)。**

| 量 | 文档 A | 文档 B | 冲突 |
|---|---|---|---|
| 生成纬轴修复后 7 月生成纬中位 | `LAUNCH_2026-09-25.md:257` = **13.5** | `LAUNCH_CHECKLIST.md:16` 与 `output/sim/2025/README.md:24` = **14.5** | 同一测量,两个值 |
| 全量回归通过数 | `LAUNCH_2026-09-25.md:174` = **620**;`LAUNCH_CHECKLIST.md:21` = **620** | `LAUNCH_CHECKLIST.md:9` = **622**;`_reference/README.md:114` 与 `ROUND_LOG.md:662,680,717,818,960,1093` = **521** | 三代数字并存 |
| 发展率(僵胎后) | `LAUNCH_CHECKLIST.md:41` = "**56-60%**" | `LAUNCH_CHECKLIST.md:71` 与 `ROUND_LOG.md:4726` = **51.9** | 同一清单内自相矛盾 |

- 影响:`README.md:5-11` 让读者"以 LAUNCH 为准",但 LAUNCH 与 CHECKLIST/归档 README 打架时**没有仲裁规则**。
- 严重度:MAJOR(会让人引错数)。

**M2. 验收文档里大量内联代码引用被"剥空",可追溯性断裂。**

- 现象:`output/sim/_reference/ROUND_LOG.md` 有 **22 处**散文行该有 `文件/函数` 却只剩双空格或空括号,例如:
  `ROUND_LOG.md:229` "门控看的是  —— 这个标记得靠  传进来, 结果瞬时风切";
  `:243` "**新增回归测试**: (solve() 输出必须是瞬时值, 标了";
  `:849` "关掉自然生成()、同种子、…";
  `_reference/README.md:127` "用仓库里的**观测最佳路径**()";
  `ACCEPTANCE.md:134-135` "在  与瞬时 d26 上统一 **+9 m**(常数写在\n 与 , 有注释说明依据)";
  `ACCEPTANCE.md:149-150` "本地已有 2024 各月的 **ERA5 月均 SST**\n()"。
- 量化(保守口径:散文行 + 无反引号 + 无表格):`ROUND_LOG.md` 22、`ACCEPTANCE.md` 3、`_reference/README.md` 2、`PHASE_LADDER.md` 2、`BDECK_FORMAT.md` 2,合计 **45 处**(`DOC_AUDIT_R162.md` 自身也有 6 处,但那是它引用原文)。
- 影响:这些位置**恰好是"修法/证据"的指认处**(要改哪个文件、哪个标记),剥空后读者无法执行;而该项目全部可信度建立在"每条结论带 `文件:行`"上。
- 机制:**unresolved**(见 §7)。`ACCEPTANCE.md:134` 的缺口在 `:132-135` 的修正记录里,同一节还出现过"第一次实施时符号搞反"的记录(`:144`),说明该文件是**多轮追加**的,剥空很可能发生在某次"搬运/重写"里。

**M3. 行号引用会过期,而且正在快速过期。**`DOC_AUDIT_R162.md:20,236-259` 在审计时点统计 `*.py:<行号>` 引用 **325 处**;我现在重新统计 **34 篇 md 合计 447 处**(`VERIFY_R158.md` 95、`BORROW_FROM_WPAC.md` 84、`VERIFY_TERRAIN_FIX.md` 75、`TY4_SPEC.md` 63、`DOC_AUDIT_R162.md` 50)。
`ROUND_LOG.md:5646-5649` 立了"今后一律引用函数名,不再引用行号"的规矩,但 `DOC_AUDIT_R162.md:38` 明确写了"**按指示不全改**"⇒ 规矩只对"今后新写"生效,历史 +447 处**不受约束地继续漂**。
唯一反例(说明可行):`tools/模拟模式帧率实测报告.md:271,301` 引用 `docs/TC模拟设计方案-v2.md:708-709` 与 `:719`,我逐字核对**完全对得上**(`:708-709` 正是 `PRECISION_HARD_MS` 无引用的那条,`:719` 正是"1× = 每秒 6 个积分步")。

**M4. `accept_ms.json` 与现行判据不同代却并存。**

- `output/sim/_reference/accept_ms.json` 是 **3 个种子 / 60 天**(`:114-116`),而现行验收口径是 **15 种子 × 2 窗口 × 100 天**(`LAUNCH_2026-09-25.md:13`)。
- 更细的冲突:`tools/acceptance_multi_seed.py:27-35` 的观测目标 `peak_med=70.0 / ace_p50=5.43 / pre_h_med=54.0`,而 `OBS_REFERENCE.md:43-48` 的峰值 P50 是 **65/50/70/75**(分来源),`LAUNCH_2026-09-25.md:135-136` 说前生期"观测 60h"。
- 影响:读者可能拿 60 天 3 种子的 `accept_ms.json` 当验收证据;而它连"通过/不通过"都不判(`acceptance_multi_seed.py` 只打印比值 `%.2fx`)。

**M5. `tests/` 被 `.gitignore` 忽略,但已有 11 个测试被跟踪 ⇒ 新增测试永久不入库。**

- 证据:`.gitignore:12` = `tests/`;`git check-ignore --no-index tests/test_panel_layer_row.py` → `.gitignore:12` 命中;`git ls-files tests/` = **11 个**。
- 影响:`test_panel_layer_row.py`、`test_tylist_paging.py`、`test_sim_settings.py`、`test_envmodel_solve_memo.py` 等**新写的回归锁**(正是项目最强调的"口径回归测试锁住",`LAUNCH_CHECKLIST.md:24`)在版本控制外;而 `test_motion_smoothness.py` 这种已跟踪的测试反而在红。

**M6. 门禁不返回退出码,无法接 CI。** 见 §5.4:35 个带 `sys.exit` 的脚本里,真正的 0/1 门禁只有个位数,`run_tests_perfile.py`(`:56-62` 只打印)、`free_hash.py`、`acceptance_multi_seed.py` 全都"打印给人看"。
影响:"每轮验收"完全依赖人读输出并手抄进文档 —— 这正是 M1 那类数字矛盾的温床。

### MINOR

**m1. `_reference/README.md` 是明显的过期索引。** `:114` 写"测试: 全量 **521 passed**"(祖父代);它只索引 5 个文件(`:8-14`),而目录里已有 **34 篇 md**,且它没提 `LAUNCH_*`/`ROUND_LOG` 的新地位。

**m2. `config.json` 与 `config.json.bak` 的差异没有文档。** 实测差异:仅 `.bak` 有 `fullscreen/screen_width/screen_height`(当前配置**丢掉了这三个键**);值变化 `ac true→false`、`sp 0.5→2.0`、`point_name_mode false→true`、`icon_set simple→smcy`、`name_dx 0→-23`、`name_dy 0→17`;新增 `peak_dx/peak_dy`。
`config.json` 本身是 `M`(已跟踪被改),`.bak` 未跟踪。谁改的、为什么改,**无记录**;若 `app` 仍读 `screen_*`,则当前配置走默认值(推断,未核对 `app/config.py:211+/−12` 的改动)。

**m3. `tools/` 里留了 26 个一次性脚本**(`_fix_9gb_r162.py`、`_probe_sections_r162.py`、`_shot_layer_popup.py`、`_repro_smooth_apply.py` …)。
它们没有测试、没有索引,`tools/` 也没有 `README.md`。正面看它们是"可复现的证据脚本"(docstring 都写了轮次),负面看 188 个脚本里没有一张"哪些还活着"的清单。

**m4. 6 个 `.sh` 脚本 + 1 篇报告混在 `tools/` 里**(`_run_sb60g_accept.sh`、`pipeline_months.sh`、`post_run_all.sh`、`refix_all_render.sh`、`regen_g2.sh`、`run_three_runs.sh`、`模拟模式帧率实测报告.md`),职责边界与 `.py` 工具混在一起。

**m5. `simulator/env/realdir/` 只有 1 个日期文件**,而 `README.md:110` 承诺"逐日缓存于 `simulator/env/realdir/`";`LAUNCH_2026-09-25.md:238` 也承认"只有 1 个日期"。**文档与数据一致(已披露)**,故只列 MINOR。

---

## 7. unresolved(未能确证)

1. **内联代码被剥空的机制**。我确认了 45 处现象与分布,但无法确定是哪个环节(生成脚本 / 人工重写 / 编辑器)剥掉的。需要:找到一份同内容的早期版本(如 `git log` 里没有,因为 `output/` 未跟踪)对比。
2. **全量 55 个测试里只有 `test_sim_mode.py` 没拿到判定**(其余 54 个已实跑,见 §5.3.1)。
   它两次都在 180 s 上限下 TIMEOUT(仓库自记 92 s),原因**不是工程结论而是资源争用**:机器上同时有多个 teammate 的 pytest / 模拟进程(`load average` 一度到 **70**),外加 WSL VM 在本轮**重启了两次**把后台作业与日志清空(§5.5 第 2、4 条)。
   需要:一段空闲窗口 + 每文件 ≥400 s,单独跑这一个文件。
3. **`tests/test_daily_instant.py` 为什么这么慢** —— 已排除"挂":11 passed,293.77 s,单项 `test_eddy_amplitude_is_peak_not_std` 200.0 s。
   仍未确证原因:该用例在 `tests/test_daily_instant.py:238` 只有 `for _ in range(8)`,慢点应在 `solver` 的 mesoscale eddy 路径或 netCDF 写盘;同时 pytest 报了 56 条 NumPy 2.5 `DeprecationWarning`(`:36/:55/:58`,NumPy **2.5** 已弃用给数组赋 shape)。需要:对该用例做 `cProfile`。
4. **`tools/acceptance_check.py` 的 A–E 五项** —— **已确证全 PASS(779.4 s)**;`ACCEPTANCE_AUTO.md` 的 2026-09-21 结论**在当前数据上仍成立**。但要注意:**跑它会写工作区**(见 §5.1 警告)。
5. **"620 passed / 0 failed"与"622 passed"的出处**。文档只给了"`tools/run_tests_perfile.py`"(该工具**不落盘**、不入库、无退出码),无法回溯到具体产物;`_reference/` 里也没有 `run_tests_perfile` 的输出文件。
6. **`free_hash` 的两条基线是否仍成立**。需要跑 `V.init + 80 步 ×2 分支`,而我判断它"会改判据语境"(生成率/僵胎占比一变哈希必变,`tools/free_hash.py:48-49`),在本轮不宜跑;另 `_reference/README.md` 与 `ROUND_LOG` 有 6 处 521/620/622 的混杂,使"哪个基线对应哪代代码"也需澄清。
7. **`docs/TC模拟设计方案-v2.md` 是否仍是有效设计**(891 行、74 个标题、未跟踪、无 git 历史)。它只在 2 处被引用(`BORROW_FROM_WPAC.md:128` 当作"我方知识散乱"的对照组;`tools/模拟模式帧率实测报告.md:271,301` 引用其行号),`LAUNCH_*`/`STATUS`/`NEXT_PLAN` 都不引用它。需要:作者确认它是"现行设计"还是"历史提案"。
8. **236 个 CR-only 变更的来源与意图**。可能是编辑器/工具统一了行尾,但无法证实;需要:看某个文件的 mtime 分布与 `.gitattributes` 缺失情况(仓库里没有 `.gitattributes`)。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| **对外口径 / 必须说明的限制** | `output/sim/_reference/LAUNCH_2026-09-25.md:236-285`(限制) + `:13-33`(现行基线通告) |
| **验收判据(什么算通过)** | `tools/summarize_acceptance.py:39-53`(指标+观测区间)与 `:76-85`(判读规则) |
| 基态场阈值 | `tools/acceptance_check.py:9-13` |
| 位同判据基线值 | `tools/free_hash.py:21-49` |
| 单种子不给结论 | `tools/acceptance_multi_seed.py:86-88` |
| 观测基准(所有"观测值") | `output/sim/_reference/OBS_REFERENCE.md:11-69`(3b/3c 是强度档位口径) |
| 缺口怎么测出来的原始证据 | `output/sim/_reference/multi_year_sb60g_{nh,sh}_15seed.txt` → `tools/summarize_acceptance.py` |
| **轮次工作流 / 历史决策** | `output/sim/_reference/ROUND_LOG.md`(轮次 150 在 `:4661-4788`) |
| 判读纪律(每次判读前默念) | `output/sim/_reference/LAUNCH_CHECKLIST.md:107-113` |
| 上一轮文档审计 + 处置状态 | `output/sim/_reference/DOC_AUDIT_R162.md:25-42`(处置) + `:46-315`(逐条) |
| b-deck 格式与命名 | `output/sim/_reference/BDECK_FORMAT.md:18/94/114/154` |
| 归档哪些数不可信 | `output/sim/2025/README.md:17-28` |
| 设计宪章 | `docs/TC模拟设计方案-v2.md`(891 行,10 章 + 附录 A–E) |
| 未完成的体验待办 | `docs/agent.md:8-113`(S1 编辑渐变线 / P0-6 首启引导 / P2-18/19/20) |
| 逐文件回归怎么跑 | `tools/run_tests_perfile.py:9-11`(用法) + `:37-45`(判定) |
| 归档对账 | `tools/bdeck_summary.py:87-104` |
| **未提交/未跟踪的资产边界** | `.gitignore`(全文 53 行)+ `git status --porcelain`(535 行) |
| 当前已知红测试 | `tests/test_motion_smoothness.py:255`、`:281` |
