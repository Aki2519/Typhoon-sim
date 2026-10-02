# 气象文件夹 / 台风模拟器 —— 探索总报告

> 生成：2026-10-02 · 工作区 `G:\气象\Typhoon-sim` · 6 名 teammate + Lead 并行探索
> 分报告：`01_simcore.md` `02_sim_data.md` `03_app_sim.md` `04_app_feat.md` `05_parent_survey.md` `06_evidence.md`
> 本报告是 6 份分报告的交叉核对与汇总，**每个数字都标注了来源**。

---

## 摘要（一分钟版）

**这是什么。** `G:\气象` 是一个人做的台风模拟/绘图工程群，总计约 8.8 GB、12 个顶层目录。核心是 `Typhoon-sim`：一个 Python+pygame 的**台风路径回放 + 架空台风模拟**桌面应用，10 万行代码，自己实现了一套从环境场、引导气流、强度上限到眼壁置换的热带气旋物理引擎。

**一句话结论。** 这是一个**物理内核相当认真、工程外壳严重欠债**的个人项目：内核有真物理、有标定、有验收文档（含实测数字和对不上的缺口），但**文档描述的入口一个都跑不通**，`docs/` 和 `output/` 全部不在版本控制内，16.9 天工作没提交。

**三条最重要的发现：**

1. **README 写的模拟器入口跑不动 —— 但文件其实还在 git 里。** `simulator/run.py`、`simulator/wrf/` 在工作树里不存在，**可是它们在 HEAD 里**（`git status` 的 29 条 ` D` = 已跟踪、工作树被删、删除从未提交；`simulator/wrf/` 在 HEAD 里有 18 个文件）。⇒ **`git checkout -- simulator/run.py simulator/wrf` 即可恢复**，不必重写。（第二轮更正，见 §11.1）
2. **"两个模拟内核"只有一个活着。** README 说的内核 A（真实大气）整条链无调用方；内核 B（`simulator/typhoons/`）在生产代码里**零引用**、只有测试用，且是 F7–F9 时期的第一代原型。实际驱动一切的是 `simulator/simcore/`，由应用内的「模拟模式」调用（99 个 tools 脚本 + 4 个 app 文件）。
3. **内核有个会放大 19 倍的口径陷阱（已端到端复现）。** `simcore/core.py` 的 `init()` 默认 `dataMode=False`（解析公式），而 GUI 和**所有标定工具**都用 `True`。60 天端到端实测：**37 个台风 vs 2 个**，逐候选接受率 **11.64% vs 0.61%**（19.2×），全年 228.6/年 vs 25.2/年。
4. **（第二轮新发现，最重）README 的头号数字"年生成 250 正中目标"已被当前代码推翻。** 用同一条命令、同一批种子原样重跑：NH 窗口 10 项里 9 项不同。补齐到两窗口各 5 种子后，**只有生成数真漂了**（NH 66.40→72.40，t=3.03，p≈0.019；SH 同向）⇒ **年量 250 → 260–268**；而发展率(t=0.09)、寿命(t=−0.53)、峰值 P50(t=0.10) **都没漂**。且**无法归因**：HEAD 停在 2026-09-15，早于验收日 09-24。详见 §11.2。

**风险提示（最急的一条）。** `tools/multi_year_check.py` 的 `--resume` 是坏的，且断点续跑会以 `'w'` 模式**截断**验收产物 —— 而那是 `LAUNCH_2026-09-25.md` 全部数字的唯一原始证据（`06_evidence.md` B1）。**叠加 §11.2 的漂移结论后这条更致命**：产物已在漂移、代码又没有可回退的版本点，那份证据是唯一能证明"曾经达到过什么"的东西。

---

## 0. 方法与可信度声明

| 项 | 说明 |
|---|---|
| 分工 | Lead 做侦察、定口径、派活、交叉核对；6 名 teammate 各占一块互不重叠的写作用域 |
| 只读约束 | 全程只读工程；每人只写自己那一份报告，探针脚本落在工作区外 |
| 交付 | 6 份分报告（**3,457 行**）+ 本汇总（550 行），合计 **4,007 行** |
| 引用核验 | Lead 用脚本对 6 份报告的**全部 235 处 `文件:行` 引用**做了存在性与行号范围校验：**235/235 通过，0 处捏造** |
| 抽查复核 | Lead 另逐条复跑了每个 teammate 的头号结论（见下），**8 项全部命中** |
| 实测 vs 推断 | 所有分报告都区分「我实测跑的」与「我只读代码推断的」；本汇总沿用该区分 |

**Lead 本轮亲自复跑并确认的结论**（不是转述）：

| 被复核的结论 | 出处 | Lead 复核结果 |
|---|---|---|
| `test_ace_engine.py` 可跑 | 环境侦察 | ✅ 19 passed / 0.95 s |
| `tests/test_motion_smoothness.py` 失败 | `06_evidence.md` | ✅ **2 failed / 7 passed / 1.41 s**（断言在 `tests/test_motion_smoothness.py:255`） |
| 色阶 `2023 == wiki` | `04_app_feat.md` M1 | ✅ `app/constants/colors.py:51-56` 确为 `dict(WIKI_SCALE)` + 3 键 |
| `path_width` 是死参数 | `04_app_feat.md` M2 | ✅ 全文仅 2 处：注释 `:162`、赋值 `:165` |
| `segment_speeds` 差 43200 倍 | `04_app_feat.md` M4 | ✅ `app/track_quality.py:50` 除 3600 vs `app/typhoon_data.py:186-188` 步长 0.5 |
| 半拉格朗日平流在 core.py 不在 clouds | `02_sim_data.md` | ✅ `simulator/simcore/core.py:4173` 节标题确为「云场(半拉格朗日平流 + 物理源)」 |
| IOD/DMI 未归档 | `02_sim_data.md` MAJOR-1 | ✅ `mode_data/` 只有 amo/nino34/pdo/soi 四个 CSV，且 `modes.py:257-260` 有占位兜底 |
| F10 变量只覆盖 5 年 | `02_sim_data.md` MAJOR-2 | ✅ 逐变量统计：9 个变量各 48 年，7 个各 5 年，`hgt500` 只 3 年 |
| 6 个 "ghost" 设置项是工具误报 | Lead 发现 → `03_app_sim.md` 复核 | ✅ 双向确认；`tools/_audit_settings_coverage.py:71` 未读 `_CFG_MIRROR` |
| `git ls-files tests/` = 11 | `06_evidence.md` M5 | ✅ 确认 |

**本环境的一个关键坑（值得单列）。** 项目的**真实运行时**是
`C:\Users\Yukioto\AppData\Local\Python\pythoncore-3.14-64\python.exe`（Python 3.14.2，装了 **pygame-ce 2.5.7 + pytest 9.1.1** + numpy/scipy/matplotlib/xarray/netCDF4/pandas/requests）。
WSL 的 `/usr/bin/python3` 没有 pygame/pytest —— 一开始整队都以为"跑不了、只能静态读"。改用真实解释器后，才拿到本报告里全部实测数字。**注意**：它的中文输出是 GBK，经 WSL 管道需 `| iconv -f GBK -t UTF-8`。

---

## 1. 全局地图：`G:\气象`

```
G:\气象\                                    8.8 GB / 12 个顶层目录
├── Typhoon-sim\                    3.9 GB  ★ 主项目（唯一的 git 仓）
├── Typhoon-sim-wrf-legacy\         2.0 GB  2026-09-19 从主仓移出的 WRF 版（run.py 78 KB + wrf/）
├── typhoon3\                       1.7 GB  ★ 素材库，被主项目硬依赖（地图/音效/图标）
├── 工具\                           1.3 GB  10 个独立外部工具（Typhoon4~8、GR2Analyst、暖心分析…）
├── 气象教材\                        550 MB  参考资料
├── 数据\                           454 MB  Typhoon4(绘图)/cma_bst/miwu/tieba/云图/官评…
├── 假想风季\                        69 MB  与主项目 typhoon/2028 同名不同内容
├── 气象知识\                       3.4 MB  ★ 12 篇正文(00–11) + README + memory/ 记忆库
├── Typhoon-sim-other\              647 MB  同源分支
├── py\                             2.5 MB  主项目的**直接前身**（35 模块版）
├── tmp_scratch\                    1.5 MB  临时
└── 其它\                            4 KB
```

**只有 `Typhoon-sim` 是 git 仓库**，其余 11 个全部是裸文件（`parent-survey` BLOCKER-1）。任何"这份数据/知识有没有被改过"的问题都无法用版本历史回答，只能靠时间戳与 md5 互证。

### 1.1 主项目的四代血缘（已实证）

```
typhoon(动画) 1.1.2   2025-10-24    1,387 行
      ↓
typhoon(动画) 1.2.3   2026-02-15    2,258 行
      ↓
typhoon(动画) 1.3.0   2026-03-04    3,295 行
      ↓
./py                  2026-06-24~27  拆成 35 模块 + 12 mixin + 17 图表
      ↓
Typhoon-sim           2026-06-28    git 首提交 dd4f9d0（顶层目录就叫 py/，62 个 .py）
      ↓
提交 011096c "v1.5"    py/ → app/ 改名（今 84 个模块）
```

判据三条独立：时间戳单调、四代同一 `pygame.display.set_caption("台风路径模拟系统")`、同族文件名而 md5 全不同。
Lead 补充确证：首提交 `dd4f9d0` 的 `git ls-tree` 顶层**确实有 `py/`**（且已有 `ty_sim_mixins` 12 个条目），`git log --reverse -- app/` 与 `-- py/` 的交界点正是 `011096c`。

### 1.2 `气象知识` 是知识底座，但主项目 0 处引用

12 篇正文（不是 10 篇）：`00_术语缩写速查` `01_台风结构动力学` `02_台风定强与观测` `03_台风环境与大尺度` `04_台风演化与形态` `05_实战案例_202519浣熊` `06_纪录与极值` `07_机构与自评口径` `08_ENSO与四洋区TC活跃度` `09_贴吧增量_202609` `10_贴吧增量_202609B` `11_TC资料站点与数据源`。

**已经落进代码的**：`08` 的四洋区响应 → `simulator/simcore/core.py:1493-1538` 的 ENSO 充放电振子（`dT/dt = aT - bh - cT³, dh/dt = -eT`，取 a=0.05/b=0.35/e=0.05 ⇒ 周期 ≈47 个月）；`08:16-19` 上升支东移 → `simulator/env/build_library.py:43-46`。

**零实现的**：`09`（暖心反演降压回归式）与 `10`（DR 法雷达反推定强）—— 这是知识库与内核最大的落差。

**结构性问题**：`grep -rn "气象知识" Typhoon-sim --include=*.py` → **0 命中**。经验参数与知识库之间没有可追溯的引用边（`05_parent_survey.md` MAJOR-2）。

### 1.3 兄弟目录 → 主项目的 6 条硬消费链

| # | 被消费的东西 | 主项目消费点 |
|---|---|---|
| 1 | `typhoon3/map/[GLOBAL].png` | `tools/ty4_render.py:38`（硬编码 `DEFAULT_MAP`） |
| 2 | `工具/Typhoon8/colormap_data.json` | `simulator/clouds/render.py:44-46` |
| 3 | `工具/风压对应/风压对应.py` 的 KZC 算法 | `simulator/typhoons/kzc.py:2`（注释标明移植来源） |
| 4 | `数据/miwu` | `simulator/typhoons/basin.py:65`（`MIWU_ROOT`） |
| 5 | `数据/Typhoon4/Python` 规格 | `app/paint_render.py`（复刻 WikiProject Track Drawer） |
| 6 | `气象知识/08` | `simulator/simcore/core.py:1493-1538`、`simulator/env/build_library.py:43-59,77-89` |

⚠ `typhoon3` 有 1.7 GB、被主项目硬依赖，却**没有任何 README**；其中 `sound/landfall/` 现只剩一个 1006 B 的 `.lnk` 快捷方式（实体已丢失）。

---

## 2. 主项目全景

### 2.1 规模

| 模块 | 文件 | 行数 | 说明 |
|---|---|---|---|
| `app/` | 84 | **37,505** | pygame 应用层（渲染/UI/对话框/统计/绘画/脚本引擎） |
| `tools/` | 188 | **30,085** | 标定/诊断/抓数/验收脚本（项目的"实验室"） |
| `simulator/` | 36 | **18,255** | 模拟内核（simcore + env + typhoons + clouds） |
| `tests/` | 58 | **14,882** | 回归测试（**被 .gitignore 忽略，但 11 个已被跟踪**） |
| `main.py` | 1 | ~255 | 唯一真正的应用入口 |
| **合计** | ~367 | **100,982** | |

数据资产：`assets/` 529 MB、`map/` 150 MB、`output/` 145 MB、`typhoon/` 5.5 MB、`simulator/` 2.0 GB（其中 `env/` 1051 文件 / 2.11 GB）。

### 2.2 架构：`simulator` 是干净的 DAG，`app` 是一整个 46 模块的循环团

Lead 用 AST 扫全仓，得到 87 个模块、265 条内部依赖边：

```
   main.py
      │ 1 条边
      ▼
   ┌──────────────────────────────────────────┐
   │  app/   ← 46 个模块塌缩成【单个强连通分量】 │
   │  （dialog_manager / ty_sim / typhoon /    │
   │    playback_ctrl / statistics/* … 互相缠绕）│
   │  fan-out 最大: dialog_manager(22), ty_sim(20)│
   │  fan-in  最大: dialog_base(23), typhoon(14) │
   └──────────────────────────────────────────┘
      │ 只有 4 个文件 import simulator.*（且全是函数内延迟 import）
      ▼
   ┌──────────────────────────────────────────┐
   │  simulator/  ← 干净 DAG，零循环依赖        │
   │  simcore.core ── envmodel.{solver,store}  │
   │       ↑ daily / climate / render / …      │
   └──────────────────────────────────────────┘
      ▲ 94 条边
   tools/  （实验室层：import simulator 94 次、import app 22 次）
```

**两个要点：**

- **`app` 包内部零分层**。46 个模块互相 import，`app/ty_sim.py` 被 28 个模块依赖（god object）。这是本项目最大的结构性债务。
- **`app → simulator` 的桥只有 4 个文件**，而且刻意用**函数内延迟 import**（`sim_mode_mixin.py` 里有 32 处），目的是让 `main.py` 启动时不背 numpy 的加载成本。这个决定是清醒的，但也意味着**只有切进「模拟模式」才会加载内核**。

### 2.3 git 现状：16.9 天未提交，且没有任何备份

| 指标 | 实测值 |
|---|---|
| HEAD | `4e4148f` @ **2026-09-15**（距探索日 **16.9 天**） |
| `git status --porcelain` | **535 行**（` D` 29 / ` M` 280 / `??` 226） |
| `git stash list` | **0**（无任何备份） |
| origin 同步 | `rev-list --count origin/main..HEAD` = 0 ⇒ **既没提交也没推送** |
| 真实内容改动 | 309 个变更里 **236 个只差 CR-at-EOL**，真改内容的只有 **73 个** |
| 大改集中在 | `simcore/core.py` +3864/-285、`ty_sim_mixins/sim_mode_mixin.py` +2258/-630、`simcore/render.py` +1648/-946 |

**未跟踪且不被 `.gitignore` 覆盖**的：整个 `docs/`（含 48 KB 设计宪章）、整个 `output/`（150 MB，含全部验收证据与 `ROUND_LOG.md`）、`tools/` 186 个脚本、`app/` 7 个新模块、`simulator/simcore/` 9 个新模块。

⇒ **一次 `git clean -fd` 会让这个项目失去设计文档和全部验收证据。**（`06_evidence.md` B3）

---

## 3. 这个"模拟器"到底是什么

"模拟器"这个词在本工程里指**三个不同的东西**，README 把它们混在一起讲，这是最大的认知陷阱。

### 3.1 三个"模拟器"

| # | 名字 | 位置 | 是什么 | 活着吗 |
|---|---|---|---|---|
| ① | **SimCore 物理内核** | `simulator/simcore/` | 0.5° 全球环境场 + 时间步进 + TC 物理。**真正的心脏** | ✅ **活的**：99 个 tools 脚本 + 4 个 app 文件在用 |
| ② | **应用内「模拟模式」** | `app/ty_sim_mixins/sim_mode_mixin.py`（2258 行）等 | pygame 里按 `H` 进入的互动模拟界面，驱动 ① | ✅ **活的**：唯一的生产驱动方 |
| ③ | **独立 `simulator/` 包（"内核A/B"）** | `simulator/typhoons/`、`simulator/env/`、README 说的 `run.py` | README 宣传的"架空台风生成器" | ❌ **入口不存在**；`typhoons/` 生产零引用 |

### 3.2 内核 ② 里没有的东西：CLI

```
README:101  python simulator/run.py --seed 1 --years 3        ← run.py 不存在
README:113  python simulator/run.py --headless --kernel A …   ← 同上
README:109  simulator/wrf/run_wrf.ps1 + simulator/wrf/README.md ← 目录不存在
```

`simulator/` 下只有 `clouds/ env/ logs/ mode_data/ modes.py output/ simcore/ typhoons/`。
**`run.py` 与 `wrf/` 正是 2026-09-19 被移进 `Typhoon-sim-wrf-legacy/` 的那两件**（该目录 `run.py` 78 KB、首行注释 `# simulator/run.py`）。

`simulator/env/` 里确实有几个可执行的 `__main__`（`build_library.py:583`、`param_fields.py:208`、`validate.py:197`、`modes.py:771`、`typhoons/basin.py:395`、`typhoons/gen.py:372`），但那是**离线数据准备/自检**，不是 README 描述的模拟器。

### 3.3 "内核A 真实大气"是死代码

README 说内核 A 有"真 WRF 精算"和"数据驱动回落（Open-Meteo ECMWF IFS + ERA5）"两级。

- `WrfFieldAPI` / `RealFieldAPI` 在 `simulator/`、`app/`、`tools/` 内**零引用**，只有 `tests/test_real_source.py` 直接构造它们。
- `simulator/env/wrfdir/` 不存在；`simulator/env/realdir/` 只有 **1 个**日期文件（`20260811.npz`），而 README 承诺"逐日缓存"。
- 实际在跑的环境源是离线的 `simulator/env/train_cache/era5+cmems` → `simcore/daily.py:30,36` → `envmodel/solver.py:29-43`。

### 3.4 "内核B 合成"也没有生产调用方

`grep -rn "simulator.typhoons" app/ tools/` → **0 命中**。只有 `tests/test_sim_typhoons.py` 在 import `basin / gen / sim / kzc`。

⇒ **结论：这个项目没有"模拟器命令行"。能跑起来的只有 `python main.py`，然后在界面里按 `H` 进模拟模式。**

---

## 4. 物理内核逐层拆解（用户重点）

以下每一层的公式都出自 `01_simcore.md`，行号已核验。

### 4.1 数据层：`simulator/simcore/envmodel/` —— 求解"给定年月，环境场长什么样"

```
store.py  →  加载 env/model/*.npz（base_sst / base_tchp / base_shear / base_d26 / base_wind / base_other）
   ↓
solver.py:29-43  →  基场 + 气候模态线性叠加
   ↓
modes.py  →  模态系数（ENSO / PDO / AMO / IOD / MJO）
```

**ENSO 不是查表而是振子**（`simcore/core.py:1493-1538`）：

```
dT/dt = a·T − b·h − c·T³        (a=0.05, b=0.35, c=0.008)
dh/dt = −e·T                    (e=0.05)
⇒ 周期 2π/√(be − a²/4) ≈ 47 个月
```

代码注释自己写明："e 不能太大，否则 h 摆幅超过限幅(±1.5)会把周期压扁"——这是充放电振子（recharge-oscillator）的标准形式，参数是手调的、不是拟合的。

`solver.py` 的三条环境源（`02_sim_data.md`）：

| 源 | 文件 | 覆盖 |
|---|---|---|
| 合成库 | `env/library/*_YYYY.npz` | 1979–2026，**但 F10 渲染变量只有 5 年**（见 §8） |
| 气候态 | `env/climate/*_clim.npz` | 12 个月气候态，月间+日内插值 |
| 真 ERA5 SST | `env/raw/sst_*.nc` | **只有 20 个文件（1979 全年 + 1980 前 8 月）⇒ 实际只有 1979 年能命中"真实 SST 优先"分支** |

### 4.2 状态机：`simcore/core.py`（5,205 行）—— 每 6 小时一步

主干是 `_sim_step_core`（`core.py:5050-5150`），顺序大致为：

```
clock 推进 → _env_update（环境场重建，含半拉格朗日平流）
   → 引导气流 steering（三层加权）
   → β 漂移
   → 强度 intensity_step（PI/MPI 上限、摩擦、干空气、OHC 消耗）
   → 眼壁置换 ERC
   → 相 phase（TD→TS→TY→…→EX/SS/SD）
   → 地形 terrain
   → 天气尺度涡旋 synoptic
   → 副高 ridge
```

### 4.3 路径：引导 + β 漂移

- **引导气流**：`steering_weights(vmax, lat)`（`core.py:2081-2109`）恒返回 `(0.50, 0.25, 0.25)` —— **两个入参被完全忽略**（Lead 复核：签名里保留但函数体是常量）。原本"弱系统受 850hPa 引导"的强度依赖物理已不再表达。
- **β 漂移**：`beta_drift(0)` 实测 = `(-0.900, +0.725)` kt。地板来自 `clamp(sin|lat|/sin15°, 0.25, 1.4)` —— 赤道上给出 β=0 时不该有的西向+向极漂移（当前无实际后果，因为没有台风在 0°）。

### 4.4 强度：PI/MPI 上限 + 摩擦 + 干空气 + OHC

- **MPI 记忆**用 `dt/24` 做平滑（`core.py:2583-2584`）。
- **`T200_REF = 222.0`**（`core.py:1332`）—— 顶部注释写 "To=210K" 是过期的，`core.py:382-384` 解释了为什么必须 222。
- **`TAU_INTENSIFY_H` = 38h**（`core.py:2532-2540`），但同一段注释反推观测值是 ~600h，**没有说明为什么放弃 600**（`01_simcore.md` U1，未确证）。
- **`v_target` 是死变量**：`core.py:3119` 赋成 `1.0` 后再无写点，却仍以 `vmax + dV*dt*v_target` 参与最终更新（`core.py:3165`）—— 轮次 49 重写 ERC 时留下的残留乘法。
- **`erc['dip']` 写入后全仓无读取点**（`core.py:3116`）。

### 4.5 半拉格朗日平流（`_advect_env`，`core.py:4173-4292`）

**这是本次探索中一处重要的口径纠正**：任务书原以为它在 `simulator/clouds/render.py`，实测它在 `simcore/core.py`。

已确证的格式要点：
- **上游位移式**：`dx/dy` 由 `us/vs`(float32) 算出，但**坐标斜坡必须 float64**（`core.py:553` 附近的注释）
- **经度 `mod` 环绕 / 纬度 `clip`**
- **非零包围盒**优化：只在活跃区域算；**跨边界时退回全网格**
- 两条早退路径

⚠ **未验证**：数值耗散与守恒性（`01_simcore.md` U3）。

`simulator/clouds/render.py` 是另一回事：它只做**给定时刻的静态亮温场合成 + IR-BD 色阶**，且 `CloudRenderer` 除 `tests/test_sim_typhoons.py` 外无生产调用方。

### 4.6 事实死码：内核算得出来，但没人用

`01_simcore.md` MAJOR-6 实测确认三条支路在生产中零调用：

| 死码 | 位置 | 证据 |
|---|---|---|
| 云场 `cloud_step` | `core.py:4210-4279` | 唯一生产调用点已被移除；`sim_mode_mixin.py:1630` 注释"云场已停算(模拟模式不要云图)" |
| 涡旋风合成 `vortex_wind` / `_arr` | `core.py:300-353` / `674-740` | 生产零调用；真实风场走 `render.py:1002/1141/1196` 的 `wind_at_arr` |
| 气压亏损 `tc_deficit` | `core.py:292-297` | 生产零调用 |

**代价**：约 200 行 + 2 MB（`sim['cld']`/`cldNoise`）的事实死码；更麻烦的是 `rewind.py:96-110` 仍把 `_cldVer` 列入"恢复时必须 bump"的失效键 —— 为一条已经不跑的通路保留复杂度。而 `vortex_wind` 里的冷高压危险半圆逻辑（含"符号反了、危险半圆反而比安全半圆弱 1.8kt"的修 bug 记录）**这条知识不再被任何生产路径执行**。

### 4.7 文档行号大面积漂移 ⇒ 知识损失

`core.py` 里"注释引用另一处行号"是**这个仓库的核心知识载体**（很多标定结论只存在于注释里）。实测这些引用**逐条失效**：

| 引用处 | 引的行号 | 真实位置 |
|---|---|---|
| `rewind.py:9-10` | `core.py:4417-4418` | `core.py:4690-4691` |
| `rewind.py:14` | `core.py:1388-1818` | `core.py:1546-2024` |
| `rewind.py:34` | `core.py:4303-4309` | `core.py:4576-4580` |
| `ridge.py:68` | `core.py:1551-1553` | 真实调用点 `core.py:1745` |
| `synoptic.py:54/60/61` | `core.py:44/1038/1039` | `ENVD` 在 `core.py:56` |

`06_evidence.md` 进一步量化：34 篇验收文档里共 **447 处** `*.py:<行号>` 引用，而 `ROUND_LOG.md:5646-5649` 虽立了"今后一律引用函数名"的规矩，`DOC_AUDIT_R162.md:38` 却明确写了"按指示不全改"⇒ 历史 447 处**不受约束地继续漂**。

### 4.8 应用内 SimCore 模拟模式（`app/ty_sim_mixins/sim_mode_mixin.py`）

**帧结构**（`03_app_sim.md`）：

```
main.py:187-217   60 FPS 主循环，dt 钳到 0.1s
   → handle_event (event_mixin.py:14)
   → update (ty_sim.py:699-703 —— MODE_SIM 提前 return，不走回放逻辑)
   → Renderer.scene_layers (renderer.py:202-298) 【显式图层表 = z 序数据化】
   → sim_field → _sim_render (sim_mode_mixin.py:1818)
```

**图层是九个（1–9 键）**，不是任务书/README 印象里的六个。

**实测性能数字**（1280×800 headless，真实解释器）：

| 项 | 冷 | 热 |
|---|---|---|
| `sst` 层 | **2589 ms** | 84 ms |
| `ohc` / `shear` | 432 / 388 ms | 69 / 67 ms |
| `ridge` | 98.8 ms | **96.6 ms**（无表面缓存） |
| `pressure` | 187.6 ms | **91.7 ms**（只缓存等值线几何，仍每帧画 ~2800 段） |
| 整层 `_sim_render` | — | `{track}` 24.9 ms → `{track,sst}` 39.9 ms → **全 9 层 240 ms（≈4 FPS）** |

**三个"看起来有其实没有"的机制：**

1. **播放倍速 ≥1× 基本无效**。`_sim_advance` 的预算闸 `budget_units = max(1, int(200/unit_ms))`（`sim_mode_mixin.py:1456`），在默认 400ms 档下 `unit_ms ≥ 400` ⇒ **一帧最多 1 个内步**。实测请求 8×（48 h/s）只跑出 **8.1 h/s ≈ 1.25×**。
2. **`sim_auto_degrade` 是死的**。全仓源码**只有 1 处出现**（`sim_mode_mixin.py:1856` 的读取），**没有任何地方写它** ⇒ `degrade` 恒 0，`_sim_degrade_level`（`:1763-1774`）不可达，"图层降级"提示永不出现。
3. **`_sim_render_ms` 少测约 400 倍**。计时块只包住 `R.render_map(...)`，而 app 层的 `draw_typhoon_info`（13.2 ms）等都在块外。实测整层 24.85 ms 时它记 **0.063 ms**。

**进入/重置模拟模式会同步冻结主线程 6.7–16.9 秒**（`_sim_reset` 是 `V.init + V.env_update` 的同步大函数，`keyboard_mixin.py:491-496`；同进程第二次 0.98 s）。视觉资源预热做过分帧，**这里没有**。

**单步与播放不是同一份统计**：连按 24 次单步（共 6 模拟小时）得 **7 个报点**，播放一步 6h 得 **2 个报点**，`t` 都为 6.00。原因是强度随机强迫 / 路径 OU / 涡旋 OU 都是"每步一次、不按 dt 归一"（`sim_step_control.py:20-23` 自述）。

---

## 5. 怎么跑起来

### 5.1 环境（当前机器实测）

| 项 | 值 |
|---|---|
| 真实解释器 | `C:\Users\Yukioto\AppData\Local\Python\pythoncore-3.14-64\python.exe`（Python 3.14.2） |
| 已装 | pygame-ce 2.5.7 / pytest 9.1.1 / numpy 2.5.1 / scipy 1.18.0 / matplotlib 3.11.0 / PIL / xarray / netCDF4 / pandas / requests |
| WSL 备用 | `/usr/bin/python3`（3.14.4）—— 有 numpy/scipy/xarray/netCDF4/pandas，**无 pygame/pytest** |
| ⚠ | **没有 `requirements.txt`**，README 的 `pip install -r requirements.txt` 是死的 |

### 5.2 能跑的命令

```bash
cd /mnt/g/气象/Typhoon-sim            # = G:\气象\Typhoon-sim
PY=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# ① 启动应用（唯一真正的入口；会开窗口）
$PY main.py                            # 或 typhoon_sim.bat（内容就是 python main.py）

# ② 跑测试（单个文件；中文输出乱码时加 | iconv -f GBK -t UTF-8）
$PY -m pytest tests/test_ace_engine.py -q --no-header      # → 19 passed / 0.95s
#  注意耗时差异极大：test_paint 69.6s、test_sim_env+climate+daily_instant 327s、test_daily_instant 单项 200s

# ③ 仓库自带门禁（退出码 0 = PASS）
$PY tools/_check_settings_sync.py       # → RESULT 全部一致, 没有漏项（退出码 0）
$PY tools/_audit_settings_coverage.py   # → missing=58 ghost=6（ghost 是工具误报，见 §8）

# ④ 验收判据（慢，会写工作区，实测 779.4s，A–E 全 PASS）
$PY tools/acceptance_check.py
```

**跑不了的**：README 里的 `python simulator/run.py …`（文件不存在）、`simulator/wrf/run_wrf.ps1`（目录不存在）。

### 5.3 测试现状（实测）

| 范围 | 结果 |
|---|---|
| `test_ace_engine` | 19 passed / 0.95 s |
| app-feat 的 8 个文件 | **159 passed / 0 failed**（含 `test_paint` 69.61 s） |
| sim-data 的 3 个文件 | **40 passed / 0 failed / 327 s** |
| sim-core 的定向测试 | **182 passed, 2 skipped / 127 s** + **134 passed / 45 s** |
| app-sim 的 7 个文件 | **102 passed / 584.48 s** |
| `acceptance_check.py` | A–E 五项 **全 PASS / 779.4 s** |
| **全仓汇总**（`06_evidence.md` 实测 54/55 文件） | **828 passed / 2 failed / 2 skipped** |
| ↑ 唯一的 2 个失败 | `test_motion_smoothness.py` **2 failed / 7 passed / 1.41 s**（Lead 复跑确认） |

**这条与文档冲突，很重要**：`LAUNCH_2026-09-25.md:174` 的头号声明是 "620 passed / 0 failed"，但在**当前工作区它是假的** —— `test_motion_smoothness.py` 自 HEAD 未改动，而它依赖的 `app/map_mgr.py` 带着 +642/-540 的未提交改动。

**覆盖盲区**（`04_app_feat.md`）：17 个模块在 `tests/` 里**零提及**，其中
`script_engine.py` + `script_dialog.py` 合计 **1,774 行零用例**、`season_stats.py`（227 行纯函数）**零用例**。

---

## 6. 验收方法论与真实缺口

### 6.1 "轮次"工作流

`output/sim/_reference/ROUND_LOG.md` 记录的是**编号的迭代轮次**：每轮改一处物理/参数 → 跑多种子验收 → 记录数字 → 决定保留/回退。`LAUNCH_2026-09-25.md`（轮次 150）是上线口径快照，`LAUNCH_CHECKLIST.md` 是收口进度。

**当前对外口径**（README:7-10）：年生成 250（正中目标）、最高纬度分布与观测一致、发展率从 86.9% 降到 51.9%。
**承认的两条缺口**：寿命偏短（101h vs 180–222h）、中段 100–129kt 过胖（26.9% vs 5.3–11.1%）。

### 6.2 判据怎么定义"通过"

`tools/acceptance_check.py` 的 A–E 五项 —— Lead 侧实测**全 PASS（779.4 s）**，与 `ACCEPTANCE_AUTO.md` 的 2026-09-21 结论一致。

但方法论有硬伤（`06_evidence.md` M6）：**门禁不返回退出码**。35 个带 `sys.exit` 的脚本里，真正的 0/1 门禁只有个位数；`run_tests_perfile.py`（`:56-62` 只打印）、`acceptance_multi_seed.py`（只打印 `%.2fx` 比值、连通过/不通过都不判）全都"打印给人看"。

⇒ **"每轮验收"完全依赖人读输出并手抄进文档** —— 这正是 §6.3 那些数字矛盾的温床。

### 6.3 权威文档之间数字互相矛盾（至少三处）

| 量 | 文档 A | 文档 B | 冲突 |
|---|---|---|---|
| 7 月生成纬中位 | `LAUNCH_2026-09-25.md:257` = **13.5** | `LAUNCH_CHECKLIST.md:16` = **14.5** | 同一测量两个值 |
| 全量回归通过数 | `LAUNCH_2026-09-25.md:174` = **620** | `_reference/README.md:114` / `ROUND_LOG.md` 多处 = **521** | 三代数字并存（还有 622） |
| 发展率（僵胎后） | `LAUNCH_CHECKLIST.md:41` = "**56–60%**" | 同文件 `:71` = **51.9** | **同一清单内自相矛盾** |

`README.md:5-11` 让读者"以 LAUNCH 为准"，但 LAUNCH 与 CHECKLIST/归档 README 打架时**没有仲裁规则**。

### 6.4 验收文档里的代码引用被"剥空"

`ROUND_LOG.md` 有 **22 处**散文行该有 `文件/函数` 却只剩双空格或空括号，例如：

> `ROUND_LOG.md:229`　"门控看的是　—— 这个标记得靠　传进来, 结果瞬时风切"

34 篇合计 **45 处**。而这些位置**恰好是"要改哪个文件、哪个标记"的指认处**，剥空后读者无法执行。机制未确证（`06_evidence.md` U1）。

### 6.5 留出法验证的实际强度

`env/reports/holdout_report.json`：**只剔了 4 个月**（每变量 n=4）、`_pass=false`、`_fails=["sst"]`（0.9395 vs 阈值 0.9），且 `_mode='synthetic'`（评的是合成库，与真实 ERA5 精度无关）。

更关键的是 `validate.py:90` 的 `pred = b[picked[1]-1]` —— 那是**"用库内别的月代替"**，不是真正重建。这份报告容易被误读为"SST RMSE≈0.94°C 达标"。

---

## 7. 分报告各自的头号发现

| 报告 | 一句话 | 最硬的发现 |
|---|---|---|
| `01_simcore.md`（876 行 / 358 处引用） | 心脏 | 默认每步跑 12 成员 72h 集合 ⇒ **单步 9.3 s**（关掉后 430 ms，**21×**）；`dataMode` 默认值把生成率从 385/年 压到 20/年 |
| `02_sim_data.md`（481 行 / 98 处） | 数据 | 环境层**三套并行实现**，文档指向的那套是死码；IOD 3 维是随机占位；F10 变量只覆盖 5 年 |
| `03_app_sim.md`（537 行 / 112 处） | 交互模拟器 | 播放倍速旋钮 ≥1× 基本无效；`sim_auto_degrade` 双重死；进/重置模拟模式冻结主线程 6.7–16.9 s |
| `04_app_feat.md`（476 行 / 42 处） | 数据模型与工具 | 两套色阶逐像素相同；`path_width` 死参数；`segment_speeds` 单位差 **43200 倍**且测试用错误口径 |
| `05_parent_survey.md`（496 行 / 100+ 处） | 全局 | 四代血缘实证；`typhoon3` 是被硬依赖的无 README 素材库；11 个兄弟目录全裸文件 |
| `06_evidence.md`（591 行 / 107 处） | 方法论 | `--resume` 会**截断**唯一的验收证据；README 运行段全死；16.9 天未提交 + `docs/`+`output/` 未跟踪 |

---

## 8. 风险清单（汇总，按严重度）

### BLOCKER

| # | 问题 | 证据 |
|---|---|---|
| B1 | `tools/multi_year_check.py` 的 `--resume` 失效，且以 `'w'` 打开会**截断**验收产物（那是 LAUNCH 全部数字的唯一原始证据） | `multi_year_check.py:104-122`（判据要起跑行**和**完成行）、`:193`（只写起跑行）、`:203`（完成行只 print）、`:171`（`'a' if done else 'w'`） |
| B2 | README 的"如何运行"整段跑不动：`requirements.txt`、`typhoon-sim.html`、`picture/` **真的不存在**；而 `simulator/run.py`/`simulator/wrf/` **在 HEAD 里、只是工作树被删**（29 条 ` D`，删除未提交）；README 内部对 `H` 键的描述自相矛盾 | `README.md:16,25,89,96,101-102,109,113,116,118`；`git ls-tree HEAD:simulator/wrf` = 18 文件 |
| B3 | 16.9 天未提交、无 stash、无推送；`docs/`（设计宪章）与 `output/`（全部验收证据）**未跟踪且未被 ignore** ⇒ 一次 `git clean` 全没。**且 HEAD 早于轮次 150，导致代码漂移无法归因**（见 §11.2） | `git status --porcelain` = 535 行；`git ls-files output/` = 0；`git log -1` = 2026-09-15 |
| B4 | "内核A 真 WRF"整条链无调用方（**文件在 HEAD 里，恢复后仍无调用方**） | `WrfFieldAPI`/`RealFieldAPI` 在 `simulator/`+`app/`+`tools/` 零引用 |

### MAJOR

| # | 问题 | 影响 |
|---|---|---|
| M1 | `core.init` 默认 `dataMode=False` 与 GUI/全部标定口径（True）不一致 | **60 天端到端实测 37 vs 2 个**，接受率 11.64% vs 0.61%（19.2×）；任何脚本直接调 `init({})` 都落到另一个气候分支 |
| M2 | `forecast` 内核默认 `True`，被 `run_tc_sim.py` 等**默认继承**（GUI 已显式传 False） | **不是正确性 bug**（开关逐位相同），纯粹是墙钟：单步 **9.5 s vs 0.66 s**（集合占 91.2%），全年 **3.9 h vs 16 min**；`PRECISION_TIERS` 的 50/100ms 档因此物理上不可能达标 |
| M3 | F10 渲染变量只覆盖 5 个年份（1990/91/92/2020/21） | 其余 43 年 D01/D02 层 `_blend_month` 载不到 → **NaN** |
| M4 | IOD/DMI 从未归档 | `_mode_vec` 15 维里 3 维是随机占位，而真实的 `idx_dmi.txt`(1870–2026) 无人读 |
| M5 | 播放倍速旋钮 ≥1× 基本无效 | 请求 8× 实跑 1.25×；用户看到时间轴在走但 6 模拟小时要等数秒 |
| M6 | `sim_auto_degrade` 永不被写 + `_sim_render_ms` 少测 400× | "帧预算自适应降级"这条设计路径**当前完全不存在** |
| M7 | 进/重置模拟模式同步冻结主线程 **6.7–16.9 s** | 按 `H`/`R` 后界面无反馈地冻住 |
| M8 | 单步 15min 与播放 6h 统计不可比（7 报点 vs 2 报点） | 逐帧检视得到的抖动与播放态不可比 |
| M9 | `segment_speeds` 单位差 **43200 倍** | 对真实台风报 1,578,654 km/h（真值 36.54）；测试用秒口径造数据 ⇒ 17 用例全绿是**虚假安全感** |
| M10 | 两套色阶逐像素相同；`path_width`/`multireset` 是死参数 | README 宣传的三方案实为两方案；用户调节无效 |
| M11 | 权威文档数字互相矛盾（纬中位 13.5 vs 14.5；回归 521/620/622；发展率 56-60% vs 51.9） | 会让人引错数，且无仲裁规则 |
| M12 | 验收文档 447 处行号引用持续漂移 + 45 处内联引用被剥空 | 项目全部可信度建立在"每条结论带 `文件:行`"上，这条被破坏 |
| M13 | 门禁不返回退出码 | "每轮验收"靠人抄数字 ⇒ 正是 M11 的成因 |

### 已确证为**误报**（不必修）

`tools/_audit_settings_coverage.py` 报的 `ghost=6`（`_map_range_mode / bl_lat / bl_lon / level3_rot_speed / main_rot_speed / span`）**不是真幽灵**：前三个在 `app/settings.py:443-444,457` 的 `_CFG_MIRROR` 里（→ `map_corner_mode` / `main_rotation_speed` / `level3_rotation_speed`），后三个是**派生显示字段**（显示 `:1067-1071`、回写 `:906-923` 与 `:2404-2410`）。工具 `:71` 只做 `ghost = seen - cfg`，**没读 `_CFG_MIRROR`**。
对照：`tools/_check_settings_sync.py` 判 **PASS（退出码 0）**—— 这两道门禁互相打架，是对的那个和错的那个。

### 关于"只读"的一处诚实披露

跑项目的 pytest **有副作用**：本轮产生了 `app/sim_output/sim_bdeck_2026100123*.bde`（来自 `tests/test_sim_mode.py`）与 `simulator/env/generated/*_1993.npz` 的重写（来自 env 生成器测试）。
Lead 已核实：**`git status` 在这两个路径上零变更**（前者未被跟踪、后者 9 个被跟踪文件重写后字节相同）⇒ **无仓库污染**，属幂等副作用。全程没有任何源码/配置被修改。

---

## 9. 给用户的上手路径

**想快速看懂这个项目** → 按这个顺序读：

1. `output/sim/_reference/LAUNCH_2026-09-25.md` —— 当前口径与已知缺口（**这是唯一还算准的总览**）
2. `simulator/simcore/core.py` 的 §`_sim_step_core`（`:5050-5150`）—— 一个时间步做了什么
3. `simulator/simcore/core.py:1493-1538` —— ENSO 振子，看这个项目的物理风格
4. `app/ty_sim_mixins/sim_mode_mixin.py` —— 交互模拟器（2258 行，重点看六个图层与内核桥接）
5. `docs/TC模拟设计方案-v2.md` —— 48 KB 设计宪章（**注意：未被 `LAUNCH_*` 引用，作者需确认它是否仍有效**）

**想跑起来** → `main.py`（见 §5.2）。**不要照 README 的"运行"节做**。

**想改内核** → 先记住 §8 的 M1（`dataMode` 默认值），否则你跑出来的气候统计和 GUI 不是一回事。

**最该先做的四件事**（我的建议，按"不可逆程度"排序）：
1. **立刻 `git add -A && git commit`（或至少 `git stash` 一份）** —— 现在 16.9 天工作、`docs/`、`output/`、整个标定时代的 `core.py` 都只存在于这一块磁盘上，且**没有可回退的版本点**（B3 + §11.2）。这一条同时是修复其余所有问题的前提：**不先固化，就无法判断任何改动是不是在改善。**
2. **修 `multi_year_check.py` 的 `--resume` 截断**（B1）—— `:203` 加 `fh.write`，或把 `:171` 的 `'a' if done else 'w'` 改成永不截断。它会在下一次续跑时销毁唯一证据。
3. **决定 README 的模拟器两节怎么处理**（B2/B4）—— `simulator/run.py` 与 `simulator/wrf/` 只要 `git checkout -- simulator/run.py simulator/wrf` 就能回来（它们在 HEAD 里）；回来之后要么接线、要么从 README 里删掉，别再挂着。
4. **给 `init()` 的 `dataMode` 补一条显式默认 + 在 README/工具里写明"验收口径 = `dataMode=True`, `precision='100ms'`"** —— 这是 M1 和 §11.3 那个"验收档 ≠ 用户档"的共同根因。

---

## 10. 交付物索引

| 文件 | 行数 | 内容 |
|---|---|---|
| `output/exploration/_BRIEF.md` | 97 | 统一简报（环境、铁律、报告骨架） |
| `output/exploration/01_simcore.md` | 876 | 物理内核（358 处证据） |
| `output/exploration/02_sim_data.md` | 481 | 环境场数据管线（98 处） |
| `output/exploration/03_app_sim.md` | 537 | 应用外壳 + 内置 SimCore（112 处） |
| `output/exploration/04_app_feat.md` | 476 | 数据模型 / 统计 / 绘图 / 脚本（42 处） |
| `output/exploration/05_parent_survey.md` | 496 | 整个 `G:\气象`（100+ 处） |
| `output/exploration/06_evidence.md` | 591 | 文档 / 验收 / 工具 / 测试 / 资产（107 处） |
| `output/exploration/_BRIEF2.md` | 88 | 第二轮简报 |
| `output/exploration/08_endtoend.md` | 700 | **端到端实跑 + 四象限 A/B**（第二轮，35 处实测） |
| `output/exploration/09_numerics_render.md` | 534 | 平流数值验证 + render/fill/import_tc 补读 |
| `output/exploration/10_calib_constants.md` | 620 | 标定常数溯源（38h 之谜） |
| `output/exploration/11_assets_deadcode.md` | 594 | 资产链与死码穷举（387 函数可达性扫描） |
| `output/exploration/sim_probe_tracks.png` | — | Lead 实跑的 3 个模拟台风航迹/强度/环境图 |
| **本文件** | — | 交叉核对与汇总 |

**引用核验结果（第一轮 6 份）**：共 **235 处** `文件:行` 引用，**235/235 通过**存在性与行号范围校验，**0 处捏造**。
**第二轮 4 份报告**另含 **60+ 条 `python.exe` 复现命令**与 **40+ 处【实测】标记**，均由 Lead 抽查复核。

**未覆盖 / 未确证**：两轮共列 **60+ 条 unresolved**，最要紧的四条是
① 代码漂移**无法归因到具体轮次**（HEAD 早于轮次 150，没有可回退版本点，见 §11.2）；
② 全仓 55 个测试文件里 **1 个未判定**（`test_sim_mode.py`，作业服务所杀，非工程原因）；
③ `precision` 档位（验收 100ms vs 用户 400ms）的效应量在 2 个配对种子上方向相反，**n 不足**；
④ `docs/TC模拟设计方案-v2.md` 是否仍是有效设计（无 git 历史可判）。

---

## 11. 第二轮：实验闭环与结论修订

第一轮是"读代码 + 微基准"；第二轮改成**做对照实验**，闭合第一轮点名的 unresolved。四个分区全部产出了带对照组、数字和复现命令的结果。**有 6 条第一轮结论被推翻或修正**——这是第二轮最大的价值。

### 11.1 六条更正（第一轮 → 第二轮）

| # | 第一轮的说法 | 第二轮实测 | 出处 |
|---|---|---|---|
| 1 | `simulator/run.py`、`simulator/wrf/` "不存在" | **在 HEAD 里**，只是工作树被删且删除未提交（29 条 ` D`）⇒ `git checkout` 可恢复 | `11_assets_deadcode.md` |
| 2 | `forecast` 默认 True 让"单步 9.3 s"，疑似 bug | **开/关逐位相同**（含 RNG 流位置）⇒ **不是正确性 bug**；作者有意；GUI 早已显式传 False。危害是 `run_tc_sim.py` 这类调用方**默认继承** | `08_endtoend.md` §3.2、MAJOR-1 |
| 3 | `vortex_wind_arr` 是死码 | **不是**——`render_map → draw_barbs → wind_at_arr:4624 → vortex_wind_arr:674` 是活链路；死的只有标量版 `vortex_wind` | `11_assets_deadcode.md` |
| 4 | `rmw_eq` 实现与 docstring 标定表差 1.4–10.9 nm | **实现没错，是 docstring 缺了一列 v**；且项目自检只查了 8 个锚点里的 5 个，被跳过的区间超线 | `10_calib_constants.md` e2 |
| 5 | `steering_weights` 两入参被忽略（像 bug） | **是刻意的轮次 102 决策**：旧表 RMSE 94.08 kt、数据本身是坏的；4 组 g 全部选出 `eff850=0.65`，RMSE 12.90→10.06。残留只是签名里的死参数 | `10_calib_constants.md` |
| 6 | `div200`/`coldSurge` 在 `dataMode=False` 下恒为 0 | `div200` **并不为 0**；而 `sample_env` 根本不带它 ⇒ 出流修正**两个分支都不生效**；`coldSurge` 唯一读取点在死码里 ⇒ 影响恰为 0 | `08_endtoend.md` NEW-1 |

此外 `_advect_env` 的真实行号是 **`:4282-4334`**（第一轮记的 `:4173-4292` 是节注释起点）。

### 11.2 最重要的新发现：头号验收数字已被当前代码推翻

**数字本身没记错，是代码漂了。**

- ✅ **对账成立**：用 `tools/summarize_acceptance.py` 对**轮次 150 的既有产物**重算 → 年生成 250、发展率 51.9%、最高纬中位 25.7、寿命 101.4h、100-129kt 占比 26.9% —— **每一项都一位不差**。所以 LAUNCH 文档是对那份产物的忠实摘要。
- ❌ **重跑不成立**：用同一条 `verify_year.py` 命令、同一种子、同一窗口、同一参数（默认值即产物所传）重跑 → **NH 窗口 10 项里 9 项不同、SH 窗口 10 项里 9 项不同**。
- 📊 **补齐到两窗口各 5 种子后，只有"生成数"真漂了**：

| 量 | 产物(轮次150) | 重跑(轮次164) | 判定 |
|---|---|---|---|
| 生成数（NH） | 66.40 | **72.40** | **t=3.03, p≈0.019 显著** |
| 生成数（SH） | 65.80 | 70.00 | 同向，不显著 |
| **年量推算** | **250** | **260–268** | **+4~7%** |
| 发展率 | 55.60 | 53.0 | t=0.09，**没漂** |
| 寿命中位 | 104.4h | — | t=−0.53，**没漂** |
| 峰值 P50 | — | — | t=0.10，**没漂** |

⇒ **README 的"年生成 250（正中目标）"是一个历史读数，当前代码跑不出来。** 但同一份 README 的另外三条（发展率跨过 48%、最高纬落在 25–26、寿命偏短）**经复验仍然成立**。

**为什么以前没人发现，现在也很难定案（两条都很关键）：**
1. `tools/free_hash.py` 的两条位级基线（`:32,:35`）今天仍**逐位命中** ⇒ 轮次 161-164 没动默认物理。**但那条基线是轮次 158 集成之后才立的**（`:29-38` 明说旧基线因 `_GEN_RATE` 改动而不可比）⇒ **"轮次 158 的改动有没有保住轮次 150 的数字"从来没有位级判据**。
2. `git log -1` = **2026-09-15**，**早于轮次 150 的 09-24**。工作区 535 项未提交、309 文件改动、无 stash、全仓仅 29 个 commit ⇒ **轮次 ~100-164 的全部成果只存在于工作区**，**无法 checkout 回轮次 150**，因此那条验收**不能重放、漂移不能 bisect**。

**【推断】**（未定案）漂移最可能来自轮次 158-160 的三类改动（副高脊线参量化 / 西风槽+冷空气接线 / 基态记忆化）。它们**声称**"默认关 ⇒ 逐位不变"，但当时的位级基线在集成后重建，那个声称**没有可比较的参照**。要定案需要逐模块做"关掉 vs 打开"的位级 A/B。

### 11.3 第三个口径维度：验收档 ≠ 用户档

验收跑 `precision='100ms'`（环境每 **18h** 重建一次），而 GUI 与 `run_tc_sim.py` 跑 `'400ms'`（每 **6h**）⇒ **对外承诺的分布与用户看到的不是同一个配置**。
效应量**未定案**：两个配对种子上方向相反（seed 11 的 DEV P95 +23 kt，seed 23 的 −1 kt），而 400ms 档本身的种子间噪声底 SD 只有 3.4 kt。报告作者据此**推翻了自己先前的单种子结论**并照实写入。

### 11.4 新挖出的硬缺陷（第一轮没有的）

| 缺陷 | 实测 | 出处 |
|---|---|---|
| **`_advect_env` 经度环绕不无缝** | `_sample_grid:4179` 的 `x1=clip(x0,0,w-2)` 在 `fx∈[nx-1,nx)` 时插值倒退一格；单测 `fx=7.5` 返回 14.5（应为 11.5）；半格位移下相对误差 **83%** | `09_numerics_render.md` |
| **CFL>1 无任何设防** | grep 零命中；实测 1.0174% 格点 CFL>1，`max\|dx\|=62.69` 格/h。**根因是输入**：`env['v850']` 实测 **722 kt**（极区 `1/cosφ` 放大） | 同上 |
| **死码规模量化** | simcore 387 个非 dunder 函数里 **59 个（15.2%）GUI 生产不可达**；`terrain` 39% / `import_tc` 50% / `phase` 36% / `rewind` 29% | `11_assets_deadcode.md` |
| **两套风压各漂各的** | `typhoons/kzc.py` vs `core.pmin_from_v`：\|Δ\| 均值 10.66 hPa、**最大 31.00 hPa**，约 110 kt 交叉；`kzc.calibrate()` 会改写进程级全局（同输入 945.63→1022.0） | 同上 |
| **静默自愈比文档慢 12.7×** | `core.py:578-581` 回写 `land_0.5.npz` 实测 **356.15 s**（docstring 写 "~28s"），产出粗掩码 vs 权威 `land_cell` **RMSE 0.1102、7.0% 陆地格判反** | 同上 |
| **`_mjo_tables` 是"已修复"的回归** | 仍有 2 份逐字节相同定义，而台账 `README.md:91` #15 与 `ROUND_LOG:530` 都写"已删掉一份" | `10_calib_constants.md` |
| **`beta_drift` 注释单位错** | docstring 把 2.24 **kt** 写成"2.1 **m/s**"（实为 1.15），差 1.82× | 同上 |
| **`dataMode=False` 连 `t200` 都没有** | `sample_env` 该分支无 `t200` ⇒ 暖心判据退化、新生系统第一步被打成 SD/SS | `08_endtoend.md` MINOR-NEW |

### 11.5 第二轮的运行教训（对以后有用）

- **作业服务会杀掉 >20 min 的后台作业**（`E_UNEXPECTED`, exit 15）。本轮损失：2 条 365 天跑、真入口 60 天跑、5 个验收种子。**下一轮请把单次后台作业控制在 ≤20 min，验收种子用前台跑。**
- 我（Lead）的校准探针实测：`run_tc_sim.py` 默认参数 **3 天 = 74.4 s**（≈6.2 s/步），72 小时生成 3 个台风 ⇒ 与 19× 结论吻合。
