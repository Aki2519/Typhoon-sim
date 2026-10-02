# 05 整个 G:\气象 文件夹（主项目 Typhoon-sim 以外的 11 个兄弟目录）

> **路径约定**：本报告 99% 的路径以 `G:\气象` 为根，简写为 `./`；涉及主项目时写 `Typhoon-sim/...`（等价于工作区根）。
> 所有体量来自 `du -sh`，所有时间戳来自 `stat -c %y`。只读探索，未修改任何文件。

## 一句话定位

`G:\气象` 不是"一个项目 + 一堆备份"，而是**一个追风爱好者（bilibili@麦块姚是屑 / 哈呐项目作者）六年积累的台风工作台**：四代自研路径软件的血缘链（typhoon(动画) 1.1.2→1.2.3→1.3.0→`./py`→`Typhoon-sim/app`）、一套被主项目直接消费的观测数据仓（`./数据`）、一批独立外部工具（`./工具`），以及由贴吧两帖提炼出的知识底座（`./气象知识`）。

## 1. 清单与职责

### 1.1 全局盘点表（一层 + 体量 + 定位）

| 目录 | 体量 | 修改时间 | 一句话定位 | 与主项目关系 |
|---|---|---|---|---|
| `Typhoon-sim` | 3.9 G | 2026-10-01 21:23 | 地球版主项目（git 仓，HEAD `4e4148f`） | **本体** |
| `./Typhoon-sim-other` | 647 M | 2026-08-24 03:43 | 哈呐星球分支 + 单文件旧版归档 | **分支 + 前身** |
| `./Typhoon-sim-wrf-legacy` | 2.0 G | 2026-09-20 00:00 | WRF 耦合旧模拟器（2026-09-19 移出主仓） | **前身（已废弃）** |
| `./typhoon3` | 1.7 G | 2026-07-22 22:11 | 纯素材库（地图/图标/音效/视频底），无 .py | **素材源（在用）** |
| `./假想风季` | 69 M | 2026-08-15 22:35 | 架空风季 b-deck 剧场数据 | **数据源（在用）** |
| `./工具` | 1.3 G | 2026-09-26 19:54 | 12 个独立外部工具 | **部分被 tools/ 消费** |
| `./数据` | 454 M | 2026-10-01 03:35 | b-deck 观测仓（xrq/miwu/官评/自评）+ 研究 | **数据源（核心）** |
| `./气象教材` | 550 M | 2026-08-30 00:47 | 气象学 PDF 教材 + 贴吧原帖全文 | **参考 + 知识原始料** |
| `./气象知识` | 3.4 M | 2026-10-01 12:13 | 00–11 共 **12 篇**正文 + README（13 个 `.md`）+ 原子记忆库 | **知识底座** |
| `./py` | 2.5 M | 2026-06-27 13:49 | 主项目 `app/` 的**前 git 快照**（包名还叫 `py`） | **前身** |
| `./tmp_scratch` | 1.5 M | 2026-09-30 22:02 | `工具/雷达速度图` 的性能对照草稿区 | **无关** |
| `./其它` | 4.0 K | 2026-09-29 22:04 | 两句创作文案 | **无关** |

### 1.2 ASCII 树（2–3 层）

```
G:\气象\
├── Typhoon-sim\                    3.9G  地球版主项目（git, 29 commits, 535 处未提交改动）
│   ├── app\            (pygame 应用层: ty_sim.py / settings.py / paint_*.py / ty_sim_mixins\)
│   ├── simulator\      (架空台风内核: simcore\ + typhoons\ + env\ + clouds\ + modes.py)
│   ├── tools\          (~200 个一次性探针/标定/渲染脚本)
│   ├── typhoon\        (可播放路径数据: 2025wp / 2025-2026sh / 2028)
│   ├── sim_typhoon\    (模拟输出 2025 / 2100)
│   ├── assets\  map\  docs\  tests\  output\
│   └── main.py  README.md  config.json
├── Typhoon-sim-other\               647M
│   ├── Typhoon-sim-Haena\   553M  (哈呐星球分支, 包名仍是 py\)
│   ├── Typhoon4-Haena\      284K  (Typhoon4 的哈呐历重写, PyQt5)
│   ├── typhoon(动画) 1.1.2\ 7.3M  (单文件 typhoon_sim.py, 1387 行)
│   ├── typhoon(动画) 1.2.3\  25M  (2258 行)
│   └── typhoon(动画) 1.3.0\  62M  (3295 行)
├── Typhoon-sim-wrf-legacy\         2.0G   ← 2026-09-19 从主仓移出
│   ├── wrf\   (WPS/WRF 管线 + case\, case 单项占 2.0G)
│   ├── run.py (78K 旧模拟器入口) / ui\ / render\ / tests\
├── typhoon3\                       1.7G   (纯素材, 无 .py)
│   ├── map\      ([GLOBAL].png ← 主项目 tools/ty4_render.py 的默认底图!)
│   ├── gui\  mainicon\  landfall\  summary\  particle\  sound\  font\  option\
│   └── timeline\ (12 个月 mp4) / 评分表\ / 4 个 F13 音乐视频
├── 假想风季\                        69M   (2024–2028/30/47wp 的架空 b-deck 剧场)
├── 工具\                           1.3G  (Typhoon5/6.1/6.5/7/8 + gongwu + 实测分析 + 帖子统计
│                                          + 暖心分析 + 雷达速度图 + 风压对应 + GR2Analyst)
├── 数据\                           454M
│   ├── xrq\ (81 年目录, 4790 个 .dat) / miwu\ (27 年) / 官评\ (187 年) / 自评\
│   ├── _research\ (286M, b-deck 源站调研) / bdeck更新\ / cma_bst\ (77 个 CH*BST.txt)
│   └── Typhoon4(绘图)\ / Typhoon6(转换)\ / 云图\ / 贴吧\ (16 个 TID) / tieba_10613685985\
├── 气象教材\                        550M  (6 个学科子目录 PDF + lz2.txt + 两帖总结.txt)
├── 气象知识\                        3.4M  (00–11 篇 .md + _staging\ + memory\)
├── py\                             2.5M  (主项目 app\ 的前身, 2026-06-24~27)
├── tmp_scratch\                    1.5M  (雷达速度图优化实验)
└── 其它\                           4.0K
```

### 1.3 各目录职责（按盘点到的一手文件）

**`./Typhoon-sim-other`** —— 三个互不相同的产物挤在一个目录：
`Typhoon-sim-Haena/README.md:1-15` 自述"基于地球版『Typhoon-sim』代码同步升级，适配哈纳星球历法与地理设定"，其 `agent.md:1` 是一份 151 行的拖拽/缩放性能专项提示词，引用路径 `py/map_mgr.py:245-267`、`py/ty_sim_mixins/event_mixin.py:106-117` —— **哈呐分支的包名仍是旧的 `py`，地球版已改名为 `app`**。
`typhoon(动画) 1.1.2 / 1.2.3 / 1.3.0` 各含一个单文件 `typhoon_sim.py`（1387 / 2258 / 3295 行），第 34 行都是 `pygame.display.set_caption("台风路径模拟系统")` —— 与主项目 `README.md:1` 同名，是同一个程序的三个世代。

**`./Typhoon-sim-wrf-legacy`** —— 迁移说明在 `README.md:1-8`："这些文件原本在主仓 `G:\气象\Typhoon-sim` 内……主程序改用自内核之后，下面这些已不再被主程序引用（全仓 grep 验证）……其中 `wrf/case` 一项就占 2.0 GB，所以按用户要求移出主仓"。内含 `run.py`（78 KB 旧模拟器入口）、`ui/main.py`（44 KB）、`render/video_style.py`（48 KB）、`wrf/`（`run_wrf.ps1` / `run_wrf.sh` / `namelist.*` / `bogus_vortex.py` / `track_vortex.py` / `wrfout_to_fields.py`）。

**`./typhoon3`** —— 全目录无 `.py`（`ls -R | grep '\.py'` 为空），只有图/音/视频与 `option/*.xlsx`。它**不是**纯粹的旧归档：主项目 `tools/ty4_render.py:38` 写着 `DEFAULT_MAP = r'G:\气象\typhoon3\map\[GLOBAL].png'`。

**`./假想风季`** —— 17 个以年份命名的子目录（`2024(real)wp+`、`2025(real)wp+2.0`、`25wp`、`27wp`、`27wp+`、`28wp`、`29wp`、`30wp`、`47wp`、`2028 global`…），内容是 ATCF b-deck 文本，例如 `29wp/2901.txt:1` = `WP,00, 00000000,    , INVEST,   0, 160N, 1473E,  15,   1005, DB,`。另有 7 张洋区合并路径大图（`2028.png` 21 MB、`2028WP.png` 等）与一首原创歌词 `西太的寒风哨戒班.txt`。

**`./工具`** —— 12 个子目录，全部是 pygame / tkinter / PyQt / matplotlib 独立程序，**没有一个被主项目 import**；但其中 2 个的**产物/算法**被主项目消费（见 §3.4）。

**`./数据`** —— 主项目的**观测数据仓**。`xrq`（81 个年目录、4790 个 `.dat`）与 `miwu`（27 个年目录、`<年>/<洋区>/*.dat`）是 b-deck 同族格式；`官评`（187 年，1851→）与 `自评`（2025/2026/2027/杂评 + `规则.txt`）是人工整理版；`cma_bst` 是 77 个 `CH<年>BST.txt`（CMA 年鉴）；`_research` 286 M 是 2026-10-01 当天的 b-deck 源站可用性调研。

**`./气象教材`** —— `动力气象学`/`大气探测`/`大气科学基础`/`天气学`/`气候学`/`雷达气象学` 六个学科 PDF 子库（如 `雷达气象学/多普勒雷达与气象观测 第2版.pdf`）+ `风迷自编教材`（7 份 PDF：`德沃夏克分析法教程V3.0【修正版】.pdf`、`白话气象1-3.pdf`、`台风 必修1/2-1.pdf`）+ 三份贴吧原帖文本。

**`./py`** —— 2026-06-24~27 的 35 个 `.py` + `ty_sim_mixins/`（12 个 mixin）+ `statistics/`（17 个图表模块）。**主项目 git 首个提交是 2026-06-28**（`git log` 尾部 `dd4f9d0 2026-06-28 Initial commit: Typhoon-sim`）——即 `./py` 是**建仓前一夜的快照**。

**`./tmp_scratch`** —— `make_baseline.py:6-7` 写死了 `SRC = r'G:\气象\工具\雷达速度图\py\雷达速度图.py'`、`DST = r'G:\气象\tmp_scratch\雷达速度图_before.py'`；`demo_fixA.py:14` 写 `ROOT = r'G:\气象\工具\雷达速度图'`。**与主项目零关系**，是"同一台机器上做 A/B 对照测量"的草稿盘。

**`./其它`** —— `rolling cyclone.txt`（21 行，Rolling Girl 中文填词）与 `一些话语.txt`（10 行，三句写给 2518 Ragasa / 2614 鲸鱼 / 2626 Surigae 的题词）。

## 2. 数据流 / 调用链

### 2.1 主项目血缘链（四代自研软件）

```
typhoon(动画) 1.1.2 / typhoon_sim.py   1387 行   2025-10-24 21:09   59716 B
        │  单文件 pygame 程序, 自带 map\ + sucai\ + typhoon\ (b-deck)
        ▼
typhoon(动画) 1.2.3 / typhoon_sim.py   2258 行   2026-02-15 11:26   89827 B
        ▼
typhoon(动画) 1.3.0 / typhoon_sim.py   3295 行   2026-03-04 16:40  135806 B
        │  ← 单文件已达 3300 行, 拆分压力出现
        ▼
./py/  (包名 py, 35 模块 + ty_sim_mixins\ + statistics\)   2026-06-24~27
        │  ← 主项目 git 首提交 2026-06-28 ("Initial commit: Typhoon-sim")
        │  ← 建仓后包名 py → app, 见 app/ty_sim_mixins/, app/statistics/, app/constants/
        ▼
Typhoon-sim/app/  (纯 pygame 应用层, 2026-10-01 仍在改)
        │
        ├──► 分支: Typhoon-sim-other/Typhoon-sim-Haena/py/  (哈呐历 + 哈呐洋区, 包名保留 py)
        └──► 分支: Typhoon-sim-other/Typhoon4-Haena/ty4haena/  (Typhoon4 的哈呐历重写, PyQt5)
```

判据（三条独立证据）：
1. **时间戳单调**：1.1.2 (2025-10-24) → 1.2.3 (2026-02-15) → 1.3.0 (2026-03-04) → `./py` (2026-06-24~27) → `Typhoon-sim` 首提交 (2026-06-28)。
2. **同名字符串**：四代的 `pygame.display.set_caption` 都是"台风路径模拟系统"。
3. **同族文件名**：`./py` 与 `Typhoon-sim/app` 共有 `ace_engine.py` / `config.py` / `data_repo.py` / `playback_ctrl.py` / `script_engine.py` / `season_ctrl.py` / `settings.py` / `ty_list.py` / `ty_sim.py` / `typhoon.py` / `view_state.py`，以及 `ty_sim_mixins/`（`event_mixin.py`、`keyboard_mixin.py`、`draw_mixin.py`）与 `statistics/`。三者内容 hash 全不相同（`md5sum` 实测：`py/settings.py=31a7464d…`、`Haena/py/settings.py=38b443a3…`、`app/settings.py=c34dfdfa…`），说明是**各自持续演进的三条活线**，不是简单拷贝。

### 2.2 数据 / 知识的消费链（主项目 ← 兄弟目录）

```
./气象教材/提问帖总结.txt          (49 M 帖, 台风吧 TID 7802007288)
./气象教材/台风202519浣熊讨论帖总结.txt (TID 10050867933)
./气象教材/lz2.txt                (TID 9365381829, 红外探测仪暖心曲线帖全文)
        │  人工提炼
        ▼
./气象知识/00–11_*.md             ← 知识库本体(概念/口径/公式/纪录)
        │  再原子化
        ▼
./气象知识/memory/atoms.json (1.76 MB) + links.json (950 KB) + 联想图.md
        │  参数化
        ▼
Typhoon-sim/simulator/  (ENSO 振子 / 副高脊线回归 / KZC 风压 / 定强口径)

./数据/xrq|miwu|官评|自评  ──┐
./假想风季/*wp*/            ──┼──► Typhoon-sim/tools/*.py (标定/统计/校验) ──► simulator 参数
./数据/Typhoon4(绘图)/Typhoon4Py ──┘                                          └──► app/paint_*.py
./typhoon3/map/[GLOBAL].png ─────────────────────────────► tools/ty4_render.py
./工具/Typhoon8(绘图脚本)/colormap_data.json ────────────► simulator/clouds/render.py
./工具/风压对应/风压对应.py ─────────────────────────────► simulator/typhoons/kzc.py
```

## 3. 核心机制详解

### 3.1 `./气象知识` 逐篇概括 + 对模拟器的参数化映射

本目录共 12 篇正文（`00_` … `11_`）+ `README.md`（索引与可信度规则）+ `_staging/`（16 份原始提炼稿）+ `memory/`（原子记忆库），合计 13 个 `.md`。
`README.md:4-11` 交代来源：两帖（提问帖 TID 7802007288 390 页、浣熊帖 TID 10050867933 242 页）+ 用户补充 + 2026-09 两批增量 + 站点清单批次 + 单帖增补。
`README.md:13-17` 立了**可信度规则**：吧务发布且 1 年内可信度最高；每条标【高/中/低】+ 来源；"自评=吧友估值，官评=机构正式报，两者经常不一致（自评往往偏高）"——这条规则本身就是模拟器校验口径的前提。

| # | 文件 | 行 | 要点（2–4 句） | 对接模拟器的哪一处 |
|---|---|---|---|---|
| 00 | `00_术语缩写速查.md` | 61 | TC 术语/缩写词典。含 RMW 定义（`:6` 普通 C5 多在 10–30 km）、KZC 风压关系（`:32` 比 Holland 可靠、双眼时不可用）、XIQQ 吧主私人定强法（`:34` 含 `(移速−11kt)×0.7` 与 `2·cos((当地时−10.5)/24·360°)` 日夜修正）、0.9/0.93/0.88/0.95 四套折算口径"不可混用"（`:35`）、移速加成（`:46`）、win7=温气黑称（`:52`） | 折算系数表、移速加成 → `simulator/typhoons/sim.py` 的强度换算；KZC → `simulator/typhoons/kzc.py` |
| 01 | `01_台风结构动力学.md` | 45 | 暖心=水相变潜热，可抽象为卡诺热机 `η=(T₂−T₁)/T₂`（`:7`）；"加强期气压滞后于风速"的动力学解释（`:9`）；兰金模型 `Δp ∝ (RMW/r₂)^(2b)` 推出"最优降压更像大环流针眼"（`:20`）；置换后 RMW 变大→风速滞后更大（`:27`）；梯度风平衡 `0 = −V²/R − ∇p/ρ − fV`（`:31`） | 眼墙置换（ERC）与风速/气压滞后 → `simulator/typhoons/sim.py` 的生命史状态机；风压解耦 |
| 02 | `02_台风定强与观测.md` | 68 | 定强总原则"气压比风速可靠、可靠范围内 Tip 最低"（`:6`）；德法成环 vs 合格（`:13`）；NHC 取最高 10 s FL ×0.9（`:25`）；KZC 不含 RMW 项、双眼不可直接用（`:40-41`）；XIQQ 完整流程与 CDO 大小系数"每多 1 km 加 0.08 kt"（`:44-48`）；机构口径 CMA 2 min / JMA 10 min / JTWC 1 min（`:52`）；**§9 雷达测风全套**：折算链路三项加性补正 × 高度因子（`:58`）、60° 可测眼墙（`:61`）、四例登陆折算（`:67`）、2004 艾利贴岸反例（`:68`） | **定强口径的权威出处**；KZC/Chavas 参数标定 → `tools/calib_steer.py`、`tools/fit_intensity_offline.py`、`tools/caliber_130.py` |
| 03 | `03_台风环境与大尺度.md` | 53 | 副高识别 588 dagpm（`:6`）；垂直风切变标准定义 = 200−850 hPa 矢量差（`:19`）；SST ≥26℃ 为生成底限、31℃+ 后趋于饱和（`:24`）；风速→海温反馈与菲东低 OHC 成因（`:26-27`）；ENSO 预测流程（`:42`）；SOI = 塔希提−达尔文 30 天滑动（`:43`）；2025 西太 ACE 全年 69.8（`:53`） | 风切变场/引导场 → `simulator/simcore/core.py`；OHC/冷水上翻 → `simulator/env/build_library.py` |
| 04 | `04_台风演化与形态.md` | 49 | 生命史经典流程（`:6`）；缓慢移动→冷水上翻成主要减弱力（`:8`）；日变化利于凌晨/入夜清空风眼（`:13`）；开眼三方式（`:20-24`）；转温判据"风速 ≥18 m/s 仍被 CMA 标热带低压的多半是温气"（`:29`）；命名 ≥35 kt（`:34`）；SSHS 分级 64/83/96/113/137 kt（`:35`）；STY ≥51 m/s（`:36`）；槽爆二重性（`:40-43`） | EX/SS/SD 相位判定 → `simulator/simcore/phase.py`、`simulator/typhoons/basin.py:65` 的 miwu EX 约束域 |
| 05 | `05_实战案例_202519浣熊.md` | 31 | 一台真台风被完整分析的示范：09-18 冷涡转暖命名 → 09-21 18Z 自评巅峰 140 kt/925 hPa（`:12`）→ 穿心实测 933 mb、投落仪 925 hPa@84 m/26.8℃→934.0 hPa（`:11`）→ 09-28 槽爆三巅到 37°N（`:15-16`）→ 10-04 转温。还有 ADT 84 vs 主观 115–127 kt 的客观/主观分歧（`:9`） | 案例基准（"这一段应该长什么样"）→ `tools/verify_year.py`、`tools/acceptance_check.py` |
| 06 | `06_纪录与极值.md` | 37 | 高纬纪录必须分口径：全洋区最北 C4 = 2004 Alex 约 40°N（`:6`），西太最北 C4 = 202519 浣熊约 37°N（`:8`），西太最高纬 MH = 01 Vipa 约 38°N（`:9`）；最低气压 Tip（`:13`）；Meranti 静轨眼温 23.9℃（`:15`）；0.9/0.93/0.88/0.95 系数纪录（`:19-21`） | **"最高纬度分布与观测一致"这条验收判据的对照值来源**（主项目 `README.md:7-8` 明写该指标） |
| 07 | `07_机构与自评口径.md` | 35 | 四机构口径表（`:4-9`）；自评 vs 官评差异来源（`:14-17`）；win7 黑称与风迷方言（`:19-22`）；CMA 六级强度分级 17.2/24.5/32.7/41.5/51 m/s（`:28`）；"JMA 假 10 分钟 / CMA 假 2 分钟"（`:35`） | 机构换算 → `tools/build_name_times_multi.py`；自评/官评双集 → `tools/stat_rate_table.py` |
| 08 | `08_ENSO与四洋区TC活跃度.md` | 54 | 四洋区总表（`:10-13`）：WP 微降+结构东移+**下限较高**；EP 明显增强但**方差极大下限很低**；SP 对 El Niño 敏感度四洋区最高且**强年代际下降**；AL 明显抑制（最稳健）。超强厄年西太"风王"常只 150–160 kt（`:20`），拉年菲东暖水集中反而出 165–170+ 顶超。1982-83 南太 4 个顶超、ACE >200（`:33`）。发展年 vs 厄后次年相反（`:22`） | **直接对应 `simulator/simcore/core.py:1493-1538` 的 ENSO 充放电振子**、`simulator/env/build_library.py:43-59` 的 SST 距平、`:77-89` 的 OHC 东西偶极、`simulator/env/param_fields.py:5-7,142` 的副高/ITCZ/季风槽回归；`simulator/mode_data/` 放 nino34/pdo/soi/amo 四张 CSV |
| 09 | `09_贴吧增量_202609.md` | 大 | 2026-09 批次 8 帖 281 条（`:6-14` 列 TID）。核心是**暖心反演与降压模型**：静力平衡积分与虚温修正、权重函数与波段选择、`ΔP = -0.185*(B35*环均温) + 2.2*(B36-B34) - 2.6*B35 + 日夜项 + 10.2`、Terra/Aqua 系统偏差、倾角修正表、QBO 解释 2019 年底 Band36 变冷 | 眼下**尚未**接进模拟器；是 `工具/暖心分析`、`工具/实测分析` 的方法学来源，也是未来"从卫星反演定强"的接口 |
| 10 | `10_贴吧增量_202609B.md` | 大 | 2026-09 批次 B、6 帖 193 条（`:6-12` 列 TID）。含"脱水"定义与 2025 西太脱水率 77%（口径分歧见同节）、DR 法（雷达径向速度+梯度风平衡反推降压）三步、自评 kt↔m/s 档位表、格美 "官125/自150"（差 25 kt）、1991ANGOLA 记的 GAEMI +30 kt | 定强口径的另一批对照样本；"脱水率"可作弱台风生成/存活率验收指标 |
| 11 | `11_TC资料站点与数据源.md` | 189 | 非贴吧来源（博客园 Mount256 清单）。2026-09-30 对 67 个 URL 实测 58 可用（`:5`）；列出本仓库已在用的 10 个源（`:36-45`）；JTWC 官方年度 ZIP 覆盖 1945–2024 且 80/80 可下（`:67`）；mesovortices **确认已死**且域本体健康、坏的是 Cloudflare 1016 源站（`:150`）；**两条实测纪律**：判死活必须以 Windows 侧为准（WSL 无代理会误报死站）、判死前必须重试（`:8-21`） | 数据源选型依据 → `tools/fetch_*.py` 系列与 `数据/_research/` 的复测脚本 |

`_staging/` 是"一批一帖一文件"的原始提炼稿（15 份 `TID.md` + `_gen09.py`），`README.md:8-11` 逐条列出哪篇并入了哪一节。
`memory/README.md:1-9` 说明它模仿 A-MEM/Mem0/MemOS/Zettelkasten，把教材降维成 `atoms.json` 原子卡 + `links.json` 关系边 + `index_tags.json` 标签索引；`:34-38` 给出 `python 记忆工具.py build/search/related/graph/stats` 五个操作。原子卡 schema 见 `:12-27`（含 `rel` 八种：因果/支撑/实例/对照/前提/结果/扩展/反例）。

### 3.2 `./工具` 逐个识别

| 工具 | 体量 | 是什么 | 技术栈 | 被主项目消费吗 |
|---|---|---|---|---|
| `GR2Analyst/` | 20 M | Greg Stumpf 的 **GR2Analyst 3** 商业雷达分析软件安装包（`gr2analyst_3_setup.exe`，20.3 MB） | Windows 安装程序 | ❌ 只是安装包，未接入 |
| `Typhoon5(坐标)` | 12 K | 把 b-deck 经纬度换算成**屏幕像素坐标**：`typhoon_analyzer.py:62-68` 用 `x=(lon−lon_ref)/width_ref*screen_W`、`y=(lat−lat_ref)/height_ref*screen_H`，`:40-60` 解析 `145N`/`1291E`（÷10）；`:84-85` 跳过第 12 列为 50/64 的行 | 纯 stdlib | ❌ 未被 import；`tools/ty4_render.py` 的投影是独立重写 |
| `Typhoon6.1(台风模拟器-自评)` | 508 K | 自评 b-deck 的**时间轴整备**：`process_files.py` 有 `add_hours_to_date` / `subtract_hours_from_date`（`:5-20`），把缺报时刻补成等间隔；含 `30/`、`input/`、`output/` | 纯 stdlib | ❌ |
| `Typhoon6.5(命名)` | 1.6 M | **命名映射**：`file_processor.py:1-20` 读 `命名.txt`（1.42 MB 的名称表）做中英/编号映射 | 纯 stdlib | ❌（主项目用 `tools/check_tc_names.py`、`tools/check_tc_names_history.py` 自建） |
| `Typhoon7(统计图表)` | 4 K | **名存实亡**：只剩 `洋区范围.txt`（16 行多边形顶点定义，WP/CP/EP/AL/IO/ME/AR/AU/SO/SP/SU/SA/SR），代码已不在 | — | ⚠️ 数据片段：与 `数据/数据统计/ocean_area.json`、`数据/数据统计/Area_ocean.json` 同源 |
| `Typhoon8(绘图脚本)` | 427 M | **向日葵 8/9 卫星数据下载 + 出图**：`app.py`（71 KB）+ `colormap_data.json`（86 KB）+ `使用说明.txt:1-3` 自述"从 JAXA P-Tree FTP 或 NOAA AWS S3 下载 Himawari-8/9 数据"，作者"小荷童" | tkinter/脚本 | ✅ **`simulator/clouds/render.py:44,46` 写死读取 `G:\气象\工具\Typhoon8(绘图脚本)\colormap_data.json`** |
| `gongwu/` | 11 M | 短程序 `gongwu.py`（4.4 KB）：解析 `145N`/`1291E` 坐标（`:6-25`），按小时聚合（`defaultdict`）——疑似"公武/工务"式报文整理 | 纯 stdlib | ❌ |
| `实测分析/` | 233 M | **复刻 TID 10719844767「高精度飞机实测数据可视化」**：`说明.md:1-11` 说明画出"窗口时间序列 / 航迹图 / 压风曲线 / 层面对比"四类图；两种数据源（本地 HDOB / AOML 1 秒）；`核心/` 有 `aoml.py`、`localdata.py`、`parsers.py`、`plots.py`、`series.py`、`theme.py`；`tools/fetch_aoml.py`、`fetch_tieba_images.py` | matplotlib（**明写依赖 Python 3.12**） | ❌ 独立工具 |
| `帖子统计/` | 129 M | **百度贴吧帖子抓取+统计**：`帖子统计.py`（51 KB）；`说明.txt:1-4` 给出用法，`:9-13` 支持断点续传与缓存复用；`_fetch_full.py`/`_fetch_lzl.py`/`_count_lzl.py` 等 20 个探针脚本 | requests + 并发 | ❌（但主项目 `数据/贴吧/` 的 16 个 TID 目录、`气象知识/_staging/` 显然由它产出） |
| `暖心分析/` | 44 K | **已删实现、只留样式的绘图模块**：`交接文档.md:3` 明写"已删除全部暖心分析算法与前端代码"；`:106-110` 列出被删的 15 个文件；保留 `plotting.py`（23 KB，`plot_composite`/`plot_band`）+ 从参考图提取的 `bd_ref_colormap.npy`/`wv_ref_colormap.npy`；`:52-93` 留下直接法代表层面（B33=542/B34=412/B35=338/B36=238 hPa）、固定 0° 标准大气背景表、45 楼倾角修正表、静力积分方向；`:96-103` 给 4 个 AQUA/TERRA 验证样本 | matplotlib | ❌ 独立工具（算法待重写） |
| `雷达速度图/` | 389 M | **单雷达多普勒测风定强 GUI**：`py/雷达速度图.py` + `_regress.py`/`_test_*.py` 6 个测试；`说明.txt` 37 KB、`优化建议.md` 61 KB、`优化证据/`、`文档/`（Run-Length Encoding 与 no13701/13702 论文）；**方法论出处 = `气象知识/02_台风定强与观测.md:56` 明写"本地 `工具\雷达速度图` 的「TC 分析」面板即按此方法学实现"** | matplotlib/tkinter | ❌ 独立工具 |
| `风压对应/` | 94 M | **KZC / Chavas 风压推演器**：`风压对应.py`（87.6 KB）；`说明.md:1-18` 列功能（多源下载 b-deck、GFS/ERA5 背景气压、0.1 hPa 递进判 OCI、KZC 与 Chavas 双向推演）；`:42-47` 给出模型：KZC 源自 Courtney & Knaff (2009)、`P = 2 + OCI + ΔP`；**Chavas 2025 W&F Eq.(5)：`ΔP = -6.60 - 0.0127·V̄² - 5.506·(½f·R̄34) + 109.013·(½f·R̄34)/V̄`**，`V̄ = Vmax - 0.55·Vtrans`、`R̄34 = 0.85×四象限平均`；`bin/` 带 ecCodes DLL | pygame + cfgrib | ✅ **算法被移植：`simulator/typhoons/kzc.py:2` 自述"移植自 `G:\气象\工具\风压对应\风压对应.py`"** |

### 3.3 `./数据` 逐个识别

| 子目录 | 体量 | 是什么 | 被主项目哪些脚本读 |
|---|---|---|---|
| `xrq/` | 21 M | 81 个年目录（1949→）、**4790 个 `.dat`**，GitHub `CyanideCN/xrqa_bdeck_data` 镜像的本地副本；`xrq/wp`、`xrq/sh` 是同内容全年代合集 | `simulator/typhoons/basin.py`、`tools/stat_intensity_profile.py:11-13`、`tools/build_name_times_multi.py:10`、`tools/compare_staged_vs_obs.py:14`、`tools/obs_enso_genesis.py:6`、`tools/probe_lifecycle.py:25`、`tools/caliber_130.py:3` |
| `miwu/` | 5.2 M | 27 个年目录（1979–2025），`<年>/<OCEAN>/b<basin><NN><year>.dat`，**26 字段含风圈半径**（`tools/train_tc_clim.py:6` 标注"T5 结构评估只能靠它"）；含 EX/SS/SD 段 | `simulator/typhoons/basin.py:65` `MIWU_ROOT = r'G:\气象\数据\miwu'`；`tools/read_miwu.py:62` `DEFAULT_DIR`；`tools/obs_zero_check.py:27`；`tools/stat_rate_table.py:206` |
| `官评/` | 70 M | 187 个年目录（**1851→**），机构正式报 b-deck | `tools/train_tc_clim.py:43` 数据源优先级 `SOURCES = (('xrq',0), ('miwu',1), ('自评',2), ('官评',3))` |
| `自评/` | 5.3 M | 2025/2026/2027/杂评 四个子目录 + `规则.txt`。`规则.txt:1-10` 定义文件名优先级（JMA 命名 > JTWC 编号 > JTWC 扰动编号 > 自编）与 12 列 b-deck 格式；`:12-17` 定义 Excel 配色阈值（强度/气压/ACE/ACE 差值/路径长度） | `tools/build_name_times_multi.py:10`、`tools/stat_intensity_profile.py:14`、`tools/stat_rate_table.py` |
| `cma_bst/` | 3.0 M | **77 个 `CH<年>BST.txt`（1949–2024）** CMA 热带气旋年鉴文本，源文件时间戳 2023-08~2025-04；另 `taifeng.xlsx` | 未在 `*.py` 里 grep 到 `cma_bst` 字样 → 见 §6 MINOR-4 |
| `_research/` | 286 M | **2026-10-01 当天做的 b-deck 源站可用性调研**：`JTWC_bdeck镜像调研.md`（15.8 KB）、`JTWC_bdeck_NOAA源_复测报告.md`、`ATCF_bdeck_镜像复测报告.md`、`JTWC直连可行性报告.md`、`实测飞机数据源.md`、`西太ACE50+_xrq_miwu.md`、`编号异常行_待复核.md`、8 个 `_bdeck_cache*` 缓存 + 13 个 `_bdeck_probe*.py` + `站点实测结果.csv`；`_check_sites.py` 被 `气象知识/11_TC资料站点与数据源.md:7` 引用为复测脚本 | 独立调研 |
| `bdeck更新/` | 19 M | `更新bdeck.py`（29.6 KB）+ `_manifest.json`（17.3 KB）+ `_cache/` + `说明.md`：b-deck 增量抓取器 | 独立 |
| `Typhoon4(绘图)/` | 31 M | **Typhoon4.exe**（易语言写的 WikiProject Track Drawer 1.2.7.2，2.78 MB，2025-03-11）+ `Typhoon4Py/`（Python 等价重写，`README.md:1-6`） + `Settings.ini`/`language.ini`/`区域.txt`/`GridData`/`strings_all.tsv` | ✅ `tools/ty4_render.py:8` 标明规格来自 `Typhoon4Py/t4_render.py`/`t4_track.py`；`:11-16` 列出三处与 `Typhoon4Py` 不一致（TD 配色、经度偏移 180°、经典色阶没实现）；`app/paint_render.py:2` 声称复刻 Ty4 |
| `Typhoon6(转换)/` | 132 K | `process_tc_data.py:8-20`：**b-deck 列裁剪器**——删第 12 列为 50/64 的行、删 12–27 列、删 29 列后、第 5 列改 `chunshu` | 未 grep 到引用 |
| `云图/GOE-7/IR/` | 0 | **空目录**（`du -sh` = 0），只剩 GOES-7 红外云图的空壳 | — |
| `贴吧/` | 3.4 M | 16 个 `tieba_<TID>/` 目录，与 `气象知识/_staging/` 的 TID 一一对应 | `气象知识/README.md:8-11` 引用 |
| `tieba_10613685985/` | 32 K | **单独放**的《【2026】台风吧导航贴》抓取结果（`对话全文.json` 18.9 KB + `对话全文_精简.txt` 10.8 KB + cookie jar）；内容是吧主 `qscdefb` 列的 RSMC/机构/报文/实时云图资源清单 | 导航索引 |
| `数据统计/` | 408 K | `typhoon_path_analyzer.py`（60.5 KB）+ `ocean_area.json` + `Area_ocean.json` + `TD-name.txt`（35 KB）；`_research/西太ACE50+_xrq_miwu.md` 开头说明"本表数字与 `typhoon_path_analyzer.calculate_typhoon_data()` 的 `basin_ace['WP']` **逐条零差**（两条独立代码通路复核）" | 交叉校验通路 |
| `自评工具/` | 11 M | **自评工作的主力 GUI**：`pygame界面.py`（161 KB）+ `pygui.py`（40.6 KB）+ `图表面板.py`（32.8 KB）+ `图像浏览.py`（29.3 KB）；`气象知识/11` 的 §一 反复引用它（"`数据\自评工具\自评工具.py`、`自评界面.py`、`pygame界面.py`"） | 独立 |

### 3.4 「谁真正消费谁」的硬证据

1. **地图**：`Typhoon-sim/tools/ty4_render.py:38` → `DEFAULT_MAP = r'G:\气象\typhoon3\map\[GLOBAL].png'`；`:24` 同样指向 `G:/气象/typhoon3/map/[GLOBAL].png`。`typhoon3/map/` 下确有 `[GLOBAL].png` 与 `[GLOBAL].tif`。
2. **色标**：`Typhoon-sim/simulator/clouds/render.py:44-46` → `r'G:\气象\工具\Typhoon8(绘图脚本)\colormap_data.json'`（两个候选路径，第一优先就是它）。
3. **风压模型**：`Typhoon-sim/simulator/typhoons/kzc.py:2` → "移植自 `G:\气象\工具\风压对应\风压对应.py`"。
4. **观测集**：`Typhoon-sim/simulator/typhoons/basin.py:65` `MIWU_ROOT = r'G:\气象\数据\miwu'`；`tools/read_miwu.py:62` `DEFAULT_DIR = r"G:\气象\数据\miwu"`。
5. **Ty4 规格**：`tools/ty4_render.py:8` 指向 `G:\气象\数据\Typhoon4(绘图)\Typhoon4Py\t4_render.py` / `t4_track.py`。
6. **资产同源**：主项目 `assets/sound/` 的 13 个 `.ogg`（`ERI-1.ogg`、`note_ts.ogg`、`weaken.ogg`、`sound.landfall.*.ogg`）与 `typhoon3/sound/random/`（`ERI-1.ogg`、`note_ts.ogg`、`weaken.ogg`、`Rapid Intensification.ogg`、`noice_170.ogg`…）、`typhoon3/sound/landfall/` 的文件名逐字对应；主项目 `assets/simple/`（67 个 PNG）对应 `typhoon3/gui/simpleicon/`（12 个，`0-TD.png`、`1-C1.png`、`5-C155.png`、`5-C170.png`…）。⚠ `typhoon3/sound/landfall/` 现只剩 1006 B 的 `.lnk` 快捷方式（原文件已不在原位）。

### 3.5 主项目血缘的分子级判据（为什么是"py 早于 git"）

| 证据 | 内容 |
|---|---|
| 时间戳 | `./py/*.py` 集中在 2026-06-24 01:53 ~ 2026-06-27 13:49；`git log` 尾部为 `dd4f9d0 2026-06-28 Initial commit: Typhoon-sim` |
| 包名演进 | `./py/ty_sim_mixins/`（12 mixin：`data_mixin`/`draw_ace_mixin`/`draw_control_panel_mixin`/`draw_info_boxes_mixin`/`draw_season_clock_mixin`/`draw_typhoon_mixin`/`season_mixin`/`utils_mixin`…）→ `app/ty_sim_mixins/`（10 个，换成了 `_draw_icon_mixin`/`_draw_path_mixin`/`sim_mode_mixin`/`sim_step_control`/`track_mixin`）；`./py/statistics/`（17 个图表）→ `app/statistics/` |
| 新增能力 | `app/` 独有 `constants/`（`colors.py`/`fonts.py`/`layout.py`）、`cache_store.py`、`config_store.py`、`paint_dialog.py`/`paint_render.py`/`paint_file_browser.py`（Ty4 风格绘画模式）、`ocean_mgr.py`、`render_geom.py`、`track_dialog.py`、`window_util.py`、`name_pos_dialog.py`、6 个 `sim_*_dialog.py` |
| 哈呐分支位置 | `Typhoon-sim-Haena/README.md:13-15` 自述已并入地球版 v1.4.4 之后的新功能（设置面板、镜头跟踪、报点列表、截图、界面隐藏、脚本增强），**但 `simulator/` 暂未移植** → 哈呐分支 = 地球版 app 层的分叉，不含模拟内核 |
| git 仓唯一性 | 只有 `Typhoon-sim` 是 git 仓（`git remote -v` = `https://github.com/Aki2519/Typhoon-sim.git`）；`-other`/`-wrf-legacy`/`typhoon3` 下 `git log` 全部报 `not a git repository` |

### 3.6 WRF 遗留版的独立设计点

`Typhoon-sim-wrf-legacy/wrf/README.md:5-25` 画出完整管线：`ERA5(CDS,GRIB) → WPS(geogrid→ungrib→metgrid) → real.exe → [bogus_vortex.py 涡旋注入 wrfinput] → wrf.exe → wrfout(NetCDF,6h) → track_vortex.py（路径/中心气压/最大风速/r34）+ wrfout_to_fields.py（→ env/wrfdir/*.npz 121×360 场）`。
`wrf/核爆扰动注入方案.md:1-24` 是一份**只做设计不含实现**的实验方案：在 WRF 积分中途用 restart 场注入"理想化热爆炸"，并诚实写出物理限制——"dx=30km 单格空气约 10¹⁶ kg，一颗 10 万吨 TNT（4.2×10¹⁴ J）注入后只升温 ~0.04 K，几乎无感"，因此必须用放大档位（升温几百~几千 K、径向风 100+ m/s）才能在 30 km 网格看到效果。⚠ 该 README 列出的 `clouds/` 目录**在磁盘上不存在**（见 §6 MINOR-1）。

### 3.7 b-deck：整个 `G:\气象` 唯一通用的交换格式

十一个兄弟目录之间的"接口"不是 API，而是一种 12 列（或 26 列）的 CSV 文本格式。`./数据/自评/规则.txt` 是它的**唯一成文规范**：

```
规则.txt:8   洋区,编号A,年月日,分钟(一般留空),chunshu,0,纬度,经度,强度,中心最低气压,性质,编号B
规则.txt:9   编号A: JTWC热带低压编号数字(如 17) > JTWC扰动编号(如 B6) > 自编(如 2A)
规则.txt:10  编号B(对于每一报只判定当时的情况): RSMC命名名字(如 Nokaen) > JTWC编号(如 03W)
             > JTWC扰动编号(如 93L) > 自编(如 3AA)
```

实测同构证据（三处独立文件，格式逐字相同）：
- `./假想风季/29wp/2901.txt:1` → `WP,00, 00000000,    , INVEST,   0, 160N, 1473E,  15,   1005, DB,`
- `./数据/自评/规则.txt:1-6` 规定 WP 文件名为"国际编号+中文名 英文名 低压编号"，外洋为"b+低压编号 名字"——与 `./数据/miwu/<年>/WPAC/bwp011970.dat`（`tools/stat_intensity_profile.py:944` 引用的路径形态）一致。
- `Typhoon-sim/typhoon/2028/2801Gus wp012026.txt` 与 `./假想风季/2028 global/2801Gus wp012026.txt` 同名（内容已分叉，见 §6 MINOR-3）。

由此可以解释几件看似无关的事：
1. **`工具/Typhoon5(坐标)` 与 `工具/gongwu` 为什么各自重写一遍经纬度解析**（`typhoon_analyzer.py:40-60`、`gongwu.py:6-25`）——因为 `145N` / `1291E` 这种"数值÷10 + 半球字母"的写法是 b-deck 的约定，每个独立工具都得自带解析器。
2. **`工具/Typhoon6(转换)/process_tc_data.py:8-20` 为什么要"删第 12 列为 50/64 的行、删 12–27 列、第 5 列改 `chunshu`"**——这是把 26 列 miwu 版**降维**成 12 列 xrq 版。`工具/Typhoon5/typhoon_analyzer.py:84-85` 则是同一规则的**反向过滤**（跳过 `parts[11] in ['50','64']`）。
3. **`工具/Typhoon6.5(命名)` 的 1.42 MB `命名.txt`** 正是 `规则.txt:2,5,10` 里"命名优先级"那条规则的数据化：JMA 名字 > JTWC 编号 > 扰动编号 > 自编。
4. **`工具/Typhoon6.1` 的时间轴整备**（`process_files.py:5-20` 的 `add_hours_to_date` / `subtract_hours_from_date`）解决的是 b-deck 报文时刻不等间隔（00/06/12/18 与临时定位混排）的问题；主项目 `simulator/simcore/import_tc.py` 做的是同一件事的另一实现。
5. **`./数据/xrq` 与 `./数据/miwu` 的 12↔26 列差异**决定了主项目的取舍：`tools/train_tc_clim.py:6` 明写"miwu 27 年，WPAC/EPAC/CPAC，**26 字段（含风圈半径），T5 结构评估只能靠它**"——只有 miwu 有风圈，所以结构类标定必须走 miwu。

### 3.8 数据仓的规模、年代覆盖与内部一致性

| 源 | 年代覆盖 | 规模 | 列数 | 独有信息 |
|---|---|---|---|---|
| `./数据/官评/` | **1851 → 2026**（187 个年目录） | 70 M | b-deck | 年代最长（北大最早） |
| `./数据/xrq/` | 1949 → 2026（81 个年目录） | 21 M / **4790 个 `.dat`** | 12 | 全年代合集 `xrq/wp`、`xrq/sh` |
| `./数据/miwu/` | 1979 → 2025（27 个年目录，**年份不连续**：1979/1987/1989/1990/1992/1996/1997/1998/1999/2002/2005/2006/2007/2009/2010/2013/2015…） | 5.2 M | **26（含风圈）** | EX/SS/SD 约束域、R34 四象限 |
| `./数据/自评/` | 2025 / 2026 / 2027 | 5.3 M | 12 | 人工自评（唯一带"未来年份"的源） |
| `./数据/cma_bst/` | 1949 → 2024（77 个 `CH*BST.txt`） | 3.0 M | CMA 年鉴文本 | CMA 官方 2 分钟口径 |
| `./假想风季/` | 2024 → 2028 / 30 / 47（架空） | 69 M | 12 | 手写剧场 |

**内部一致性已有一条硬证据**：`./数据/_research/西太ACE50+_xrq_miwu.md` 开头自述"本表数字与 `typhoon_path_analyzer.calculate_typhoon_data()` 的 `basin_ace['WP']` **逐条零差**（两条独立代码通路）"，并给出 ACE 口径 —— `ACE = Σ V²/10⁴`，只累加 6 小时网格上 TS+（≥34 kt）的记录，且"只算西太部分 = 只累加 `basin_for_point()` 判定为 WP（100°E–180°E，赤道以北）的记录"。
**口径冲突的证据**：`./数据/xrq` 的 `wp/` 与 `sh/` 是同内容镜像，该报告明确"已跳过以免重复计数"——说明**同一份数据在目录结构里存在冗余副本**，任何遍历式统计都必须去重。

**`./数据/_research`（286 M）的时间语义**：它是 2026-10-01 当天的一次性调研，`JTWC_bdeck镜像调研.md:3-5` 写明"调研机：中国大陆，`G:\气象`；判定链路：**Windows Python**（3.14.2）；调研时间：2026-10-01"，`:11-30` 逐条记录 `https://www.metoc.navy.mil/jtwc/...` 的 HTTP 状态（`/jtwc/products/<已知文件名>` 全 200、列目录一律 403）。这批文件是 `./数据` 里**唯一带明确采集日期与方法的**部分，也是判断其它数据"新不新"的唯一锚点。

### 3.9 知识 → 参数化的逐项对照（`./气象知识` 落进 `simulator/` 的哪里）

| 知识库条目 | 结论 | 落到模拟器的哪一行 |
|---|---|---|
| `08:10-13` 四洋区 ENSO 响应总表 | WP 微降 / EP 方差大 / SP 敏感度最高 / AL 抑制 | `simulator/simcore/envmodel/modes.py:5-11`（t 落在观测区间直接查 `modes_index.npz`，"方差/模态间相关/ENSO 相位锁定都保留"）；`simulator/mode_data/{nino34,pdo,soi,amo}.csv` |
| `08:12` SP 的强年代际下降 | 叠加在下行背景上的脉冲 | `simulator/simcore/envmodel/solver.py:258,275`（`BASE_PERIOD = (1991, 2020)` 作标定基期） |
| `08:16-19` WP 机制：沃克环流弱化→上升支东移 | 生成东移、近岸暖水减少 | `simulator/env/build_library.py:43-46` `enso_anom = oni * 1.2 * east * np.exp(-((lat-0)**2)/(2*14**2))`（`east` 即"西半球经度 >180 更暖"） |
| `03:26-27` 菲东低 OHC / 赤道逆流上升流 | OHC 东西偶极、温跃层抬升 | `simulator/env/build_library.py:77-89` 注释直写"原实现全经度同号增暖与真实 ENSO-OHC 响应相反" |
| `03:19` 垂直风切变 = 200−850 hPa 矢量差 | 大值不利 | `simulator/env/param_fields.py:5-7`（脊线/ITCZ/季风槽 = 气候态 + a·ONI + b·PDO + 随机游走）、`:142` 回归标定式 |
| `08:3` RONI = 扣除全球变暖趋势的 Niño3.4 距平 | 用距平而非绝对值 | `simulator/simcore/envmodel/modes.py:169`（"季节锁相：ENSO 类噪声在 11–1 月最大，夏末最小"） |
| `02:40-41` + `00:32` KZC 比 Holland 可靠、不含 RMW 项、双眼不可用 | 风压关系选型 | `simulator/typhoons/kzc.py:2`（移植自 `工具/风压对应/风压对应.py`）；`simulator/typhoons/sim.py:385` 注释"EX/SS/SD 风压解耦：从 miwu EX 段 (w,p) 约束域抽样，禁用热带公式" |
| `02:52` 机构口径 CMA 2 min / JMA 10 min / JTWC 1 min | 折算不能混用 | `tools/build_name_times_multi.py:10`、`tools/stat_rate_table.py`（多源并置比较） |
| `02:25` FL ×0.9 → 地面 1 min | 实测折算链 | 知识侧落成 `./工具/实测分析`（HDOB/IWG1 可视化）；主项目侧见 `tools/caliber_130.py:3`（按系统所在年份的 ONI 分组算 130+ 占比） |
| `06:6-9` 高纬纪录（Alex 40°N / 浣熊 37°N / Vipa 38°N） | 最高纬度分布的验收对照 | `Typhoon-sim/README.md:7-8`"最高纬度分布与观测一致（15+15 种子两窗口，A 三条判据全落在区间内）" |
| `04:29` 温气判据 | EX 相位识别 | `simulator/simcore/phase.py`；`tools/diag_ex_phase.py`、`tools/probe_et_gate.py` |
| `05` 浣熊个案（含 ADT 84 vs 主观 115–127 kt） | 客观/主观分歧的实例 | `tools/obs_reference_table.py`、`tools/verify_forecast_locked.py` |

**反向缺口**：`09`（暖心反演降压模型全套回归式）与 `10`（DR 法雷达反推定强）在 `simulator/` 里**没有任何对应实现** —— 这两篇是当前知识库与模拟内核之间最大的落差，也是 `./工具/暖心分析`（算法已删待重写）与 `./工具/雷达速度图`（独立 GUI）存在的理由。

### 3.10 `./假想风季`：一套横跨 6 个年份、两种编号制的架空剧场

| 子目录 | 编号制 | 命名风格 | 备注 |
|---|---|---|---|
| `2024(real)wp+` | `24-03w.txt` + `2402 马力斯.txt` | **真实年 + 中文名** | "real" = 真实风季的扩展 |
| `2025(real)wp+` | `2025 03W wp032025(2561).dat` | 真实编号 + 中文名 + 内码 | 与主项目 `typhoon/2025wp` 目录名一一对应（`diff` 为空） |
| `2025(real)wp+2.0` | 同上 | 同上 | 2.0 修订版 |
| `25wp` / `27wp` / `28wp` / `29wp` / `30wp` | `2501…` / `2901.txt`…`2909.txt` | **纯序号 + 中文名**（`3001 Yaimen 雅曼`） | 逐年一本"剧集" |
| `27wp+` | `04w.txt` / `05w.txt` | 短编号 | `+` 表示增补 |
| `47wp` | `4701(030712z).txt`…`4710(070418z).txt` | 序号 + **(月日时 z) 时间标签** | 见 §7 unresolved-8 |
| `2028 global` | `2801Gus wp012026.txt` + `bal/bcp/bep/bio/bme…` | **全球多洋区** | 66 文件，对应主项目 `typhoon/2028`（93 文件） |

两种编号制并存是这套数据的核心特征：`29wp/2901.txt` 的 **04 段仍是 `WP,00,00000000`**（编号字段留空，只写 INVEST），说明"2901"是**剧场内部的集号**，不是 ATCF 编号；而 `2028 global/2801Gus wp012026.txt` 则是**真 ATCF 编号 `wp012026`**。因此同一批数据里既有"可以喂给 b-deck 解析器"的（后者），也有"只是占位/草图"的（前者）——这正是主项目 `typhoon/` 只挑了 `2025wp` / `2025-2026sh` / `2028` 三个子集的原因（`ls Typhoon-sim/typhoon` 实测 6 项：`2025-2026sh`、`2025wp`、`2028`、`pign`、`bsh141989 Hinano.dat`、一张 `2025-2026sh.png`）。

配套的 7 张洋区大图（`2028.png` 21.3 MB、`2028WP.png` 5.1 MB、`2028AL.png` 5.0 MB、`2028EP.png` 2.4 MB、`2028IO.png` 3.7 MB、`2028ME.png` 3.7 MB、`2028CP.png` 270 KB）是**按洋区拆分的年度全路径总图**，与 `数据/数据统计/ocean_area.json`、`工具/Typhoon7(统计图表)/洋区范围.txt` 的多边形划分属于同一套洋区定义（WP/CP/EP/AL/IO/ME/…）。`西太的寒风哨戒班.txt`（1.5 KB，无扩展名的原创歌词，副歌反复"再现1997 / 看那伊万琼安双超台共舞"）与 `27wp/2027+.png` 说明这个目录一半是数据、一半是**同人创作**。

### 3.11 `./气象教材`：知识库的"上游原料层"，层级是四跳不是一跳

```
第 1 跳  原始帖抓取（纯文本，带楼层与作者）
  ./气象教材/提问帖总结.txt            49 879 B   TID 7802007288  Q&A 帖 390 页/6505 主楼
  ./气象教材/台风202519浣熊讨论帖总结.txt 16 130 B  TID 10050867933  单台风追风 242 页
  ./气象教材/lz2.txt                   384 989 B  TID 9365381829  红外探测仪暖心曲线帖
                                        （文件头即为抓取元数据：`=== URL: https://tieba.baidu.com/p/9365381829?pn=1&see_lz=1 ===`）
第 2 跳  单帖提炼稿（一帖一文件，统一编号）
  ./气象知识/_staging/<TID>.md         15 份（9114277820 / 4122107335 / 8568335457 / 9829229379 /
                                        6049034416 / 10994418227 / 9365381829 / 10549079661 /
                                        10719844767 / 8580624243 / 8585720043 / 10972852757 /
                                        11031366774 / 10258213259 / 8893416542）+ _gen09.py
第 3 跳  教材式合并稿（按主题重排 + 与既有篇目判重）
  ./气象知识/00_…11_*.md               每篇头部都写"来源格式统一为「来源：tieba <TID> <作者>（<等级>）@ <时间>」"
第 4 跳  原子化记忆（供检索）
  ./气象知识/memory/atoms.json (1.76 MB) + links.json (950 KB) + index_tags.json (55 KB) + 联想图.md (58 KB)
```

这套四跳结构有一个明确的设计意图，写在 `气象知识/memory/README.md:3-9`：模仿 A-MEM（Zettelkasten 式原子条目）、Mem0（ADD→UPDATE→MERGE→DELETE→SEARCH 管线）、MemOS / dsh-memento。`memory/README.md:29-31` 定义了 8 种关系（因果/支撑/实例/对照/前提/结果/扩展/反例），检索时"命中卡 → 取其 links 一跳（可直接）、二跳（相关）'联想'"。

**关键观察**：`气象教材/` 里的 PDF 与 txt 是**两个不同来源**——PDF 是正规教材（`雷达气象学/多普勒雷达与气象观测 第2版.pdf`、`气候学/热带海洋—大气相互作用.pdf`、`大气科学基础/气象学与生活 第12版.pdf`），txt 是贴吧原文。`气象知识/` 只把后者做成了体系（`README.md:4-11` 列出的来源全是贴吧 + 用户补充），**PDF 教材完全没有被提炼进 `气象知识/`**。这解释了为什么知识库里 `01`–`04` 的动力学条目大量标注"【高】来自 SHSHPP2/qscdefb 等吧友"而不是教材引用，也解释了 `README.md:14-16` 为什么必须先立"贴内内容并非全为权威"的可信度规则。

### 3.12 `Typhoon4`–`Typhoon8` 更像一条**工具链**，而不是八个并列工具

散落在 `./数据` 与 `./工具` 的 TyphoonN 系列，按"对 b-deck 做什么加工"排出来是一条能对上的流水线（**加工顺序为推测，工具本身的功能均为实测**）：

| 序 | 工具 | 输入 | 输出 / 加工 | 证据 |
|---|---|---|---|---|
| 4 | `数据/Typhoon4(绘图)/Typhoon4.exe` + `Typhoon4Py/` | b-deck / BP / TK / RAD + `Settings.ini` | **路径图 PNG**（WikiProject Track Drawer 1.2.7.2 原版 + Python 等价重写） | `Typhoon4Py/README.md:1-20`；`Typhoon4(绘图)/区域.txt`、`GridData`、`language.ini`、`*.lang` |
| 6 | `数据/Typhoon6(转换)/process_tc_data.py` | 26 列 miwu 版 b-deck | **12 列 xrq 版**（删 50/64 行、删 12–27 列、第 5 列改 `chunshu`） | `process_tc_data.py:8-20` |
| 6.5 | `工具/Typhoon6.5(命名)/file_processor.py` | 上一步的 12 列 | **补中文名/英文名**（读 1.42 MB `命名.txt`） | `规则.txt:1-6` 的命名优先级 |
| 6.1 | `工具/Typhoon6.1(台风模拟器-自评)/process_files.py` | 12 列 | **时间轴整备**（`add_hours_to_date` / `subtract_hours_from_date` 补等间隔报） | `process_files.py:5-20`；含 `30/`、`input/`、`output/` |
| 5 | `工具/Typhoon5(坐标)/typhoon_analyzer.py` | 12 列 | **经纬度 → 屏幕像素坐标**（`x=(lon−lon_ref)/width_ref*screen_W`） | `typhoon_analyzer.py:62-68`、`:84-85` |
| 8 | `工具/Typhoon8(绘图脚本)/app.py` | JAXA P-Tree FTP / NOAA AWS S3 | **卫星云图 PNG + 色标表**（`colormap_data.json`） | `使用说明.txt:1-25` |
| 7 | `工具/Typhoon7(统计图表)/洋区范围.txt` | — | **洋区多边形定义**（代码已失；仅剩 16 行顶点表） | 见 §6 MINOR-6 |

反向印证：`Typhoon8` 的产物 `colormap_data.json` 被主项目 `simulator/clouds/render.py:44-46` 直接读取；`Typhoon4` 的行为规格被主项目 `tools/ty4_render.py:8-16` 拿来对照（并纠正了它三处错误）；`Typhoon7` 的洋区定义与主项目 `数据/数据统计/ocean_area.json` 同类。也就是说这条链上**至少 4 个环节的产物/规格进了主项目**，尽管主项目从不 import 它们的代码。

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `./数据/xrq/<年>/*.dat` | ATCF b-deck 文本 | 21 M / 4790 文件 | GitHub `CyanideCN/xrqa_bdeck_data` 镜像下载 | `simulator/typhoons/basin.py`、`tools/stat_intensity_profile.py:11` 等 ≥7 个 tools 脚本 |
| `./数据/miwu/<年>/<洋区>/*.dat` | b-deck 26 字段（含风圈） | 5.2 M / 27 年 | 第三方再分析集 | `simulator/typhoons/basin.py:65`、`tools/read_miwu.py:62` |
| `./数据/官评/<年>/` | b-deck | 70 M / 187 年 | 机构正式报汇编 | `tools/train_tc_clim.py:43`（第 4 优先级） |
| `./数据/自评/{2025,2026,2027,杂评}/` | 12 列 b-deck + `规则.txt` | 5.3 M | 人工自评 | `tools/stat_rate_table.py`、`tools/stat_intensity_profile.py:14` |
| `./数据/cma_bst/CH<年>BST.txt` | CMA 年鉴文本 | 3.0 M / 77 文件 | CMA 官方下载（2023-08~2025-04） | ⚠ 未在 `*.py` 中 grep 到引用 |
| `./数据/Typhoon4(绘图)/Typhoon4Py/` | Python + Pillow | 31 M | 对 `Typhoon4.exe` 的反编译复刻 | `tools/ty4_render.py:8,11-16` 作为规格来源 |
| `./数据/_research/` | md / json / csv / py | 286 M | 2026-10-01 单日 b-deck 源站调研 | 人读；`_check_sites.py` 被 `气象知识/11:7` 引用 |
| `./假想风季/<风季>/*.txt` | b-deck | 69 M | 用户手写架空风季 | 主项目 `typhoon/` 目录的可播放数据（同名异构，见 §6 MINOR-3） |
| `./typhoon3/map/[GLOBAL].png` | PNG | 1.7 G（整目录） | 卫星底图拼接 | ✅ `tools/ty4_render.py:24,38` |
| `./typhoon3/sound/**`、`gui/simpleicon/**` | ogg / png | — | 素材整理 | ✅ 主项目 `assets/sound`（13 个同名 ogg）、`assets/simple`（67 png） |
| `./工具/Typhoon8(绘图脚本)/colormap_data.json` | JSON 色标 | 86 K | Ty8 工具导出 | ✅ `simulator/clouds/render.py:46` |
| `./工具/风压对应/bin/*.dll` | ecCodes DLL | — | 随工具附带 | `风压对应.py` 读 GRIB2 |
| `./气象知识/memory/atoms.json` | JSON 原子卡 | 1.76 M | `记忆工具.py build` | agent 检索（`atoms.json` 在 `Typhoon-sim` 内 **0 处引用**，见 §6 MINOR-5） |
| `./气象教材/lz2.txt` | 贴吧原帖全文 | 385 K | 贴吧抓取 | `工具/暖心分析/交接文档.md:19` 指向它 |
| `./tmp_scratch/雷达速度图_before.py` / `_fixA.py` | Python 副本 | 298 K / 301 K | `make_baseline.py:6-7` | `工具/雷达速度图` 的 A/B 对照（**不回流到该工具**） |

## 5. 如何运行 / 如何验证

本环境（WSL `/usr/bin/python3` = 3.14.4，有 numpy/scipy/xarray/netCDF4/pandas/PIL，**无 pygame/matplotlib/h5py**）能跑什么：

| 目标 | 命令 | 本环境可行性 |
|---|---|---|
| 看知识库原子卡统计 | `python3 气象知识/memory/记忆工具.py stats`（在该目录下） | ✅ 纯 stdlib |
| 检索知识库 | `python3 气象知识/memory/记忆工具.py search KZC` | ✅ |
| 数各数据源文件量 | `ls 数据/xrq \| wc -l`、`find 数据/xrq -name '*.dat' \| wc -l` | ✅（已实测 81 / 4790） |
| 读 b-deck 格式 | 任意 `head -3 假想风季/29wp/2901.txt` | ✅ |
| Typhoon5 坐标换算 | `cd 工具/Typhoon5\(坐标\) && python3 typhoon_analyzer.py` | ✅ 纯 stdlib（但会读 `input/`，只读探索下不建议实际执行） |
| Typhoon6/6.1/6.5/gongwu | 同上，纯 stdlib | ✅ 语法可读 |
| Typhoon8 / 实测分析 / 暖心分析 / 雷达速度图 / 风压对应 | 需 tkinter+matplotlib 或 pygame+cfgrib+ecCodes | ❌ 本环境缺 pygame/matplotlib |
| GR2Analyst | `gr2analyst_3_setup.exe` | ❌ Windows 商业软件安装包 |
| 哈呐分支 / `./py` / typhoon(动画) 1.x | `python main.py` / `python typhoon_sim.py`，需 `pygame-ce` | ❌ 本环境无 pygame（**可用 Lead 找到的 `C:\Users\Yukioto\AppData\Local\Python\pythoncore-3.14-64\python.exe`，装 pygame 2.5.7**） |
| WRF 遗留版 | `python run.py --seed 1 --years 3`；WRF 路线另需 WPS/WRF | ❌ 依赖已移出主仓的 `simulator/env` 等模块（`README.md:20-24`） |
| 验证"兄弟目录被主项目消费" | `grep -rn "G:\\\\气象" Typhoon-sim --include=*.py` | ✅ 已实测 93 处命中 |

**只读验证的替代法**（我在本任务中实际用的）：`du -sh` 定体量、`ls -la`/`stat -c %y %s` 定时间与大小、`md5sum` 判同名文件是否同源、`diff <(ls A) <(ls B)` 判目录同构、`grep -nE` 在**主项目内**找对外部路径的引用。

## 6. 观察到的问题 / 风险 / 不一致

**BLOCKER-1｜兄弟目录全部不在版本控制下，且主项目基线不干净。**
现象：只有 `Typhoon-sim` 是 git 仓；`-other` / `-wrf-legacy` / `typhoon3` / `假想风季` / `工具` / `数据` / `气象知识` 全部裸文件。同时主仓 `git status --short` 有 **535 处**改动（`M`/`D`/`??`），HEAD 停在 `4e4148f 2026-09-15`，而磁盘上 `app/settings.py` 等文件已改到 2026-10-01 22:02。
证据：`git -C "G:\气象\Typhoon-sim-other" log` → `fatal: not a git repository`；`git status --short | wc -l` = 535。
影响：任何"这些数据/知识是不是被改过"的问题都**无法用版本历史回答**，只能靠文件时间戳与 md5 互证。`气象知识/memory/*.json` 1.76 MB 亦是裸文件。

**MAJOR-1｜`./数据` 与 `./气象知识` 有时效性/口径标注，但 `./工具` 几乎没有。**
现象：`气象知识/11_TC资料站点与数据源.md:5,33` 明确写"实测列是 **2026-09-30** 的快照，有时效性"；`README.md:14-16` 定义可信度分级。而 `工具/` 下 `Typhoon8/使用说明.txt:1-3` 只写"更新内容 6.21 / 7.8"，没有任何版本号或日期语义。
证据：上列各文件行。
影响：外部工具的输出（尤其色标 `colormap_data.json`、KZC 实现）会随上游改版漂移，而主项目**在代码里硬拷了它们的路径或算法**（§3.4 第 2、3 条），改版时会静默失配。

**MAJOR-2｜`./气象知识` 是主项目参数化的隐性依赖，但主项目里 0 处引用它。**
现象：`grep -rn "气象知识" Typhoon-sim --include=*.py` → 0 命中；但 `08_ENSO与四洋区TC活跃度.md` 的结论显然已落进 `simulator/simcore/core.py:1493-1538`（ENSO 充放电振子）与 `simulator/env/build_library.py:43-59,77-89`（SST 距平、OHC 东西偶极），`tools/caliber_130.py:3` 也是按"系统所在年份的 ONI 分组"。
证据：`grep` 结果 vs 上列代码行。
影响：`simulator/` 里的经验参数与知识库之间**没有可追溯的引用边**。改知识库不会提醒任何人去改代码，反之亦然；`气象知识/memory/links.json` 的关系图也断在主项目门口。

**MINOR-1｜WRF 遗留版 README 与磁盘不一致。**
现象：`Typhoon-sim-wrf-legacy/README.md:17` 列出移出内容包含 `clouds/`（"云图相关"），但该目录在磁盘上不存在。
证据：`ls -la /mnt/g/气象/Typhoon-sim-wrf-legacy` 只有 `README.md`、`fictional_sim.bat`、`render/`、`run.py`、`tests/`、`ui/`、`wrf/`。
影响：迁移清单不可作为"原件完整性"依据。

**MINOR-2｜主项目 README 引用了不存在的路径（Lead 已核实，此处补血缘侧证据）。**
现象：`Typhoon-sim/README.md:98-114` 让用户跑 `python simulator/run.py`，并描述 `simulator/wrf/run_wrf.ps1`；而 `simulator/` 下只有 `clouds/ env/ logs/ mode_data/ modes.py output/ simcore/ typhoons/`，**没有 `run.py`，也没有 `wrf/`**——`run.py` 与 `wrf/` 正是 2026-09-19 被移进 `Typhoon-sim-wrf-legacy/` 的那两件。
证据：`ls Typhoon-sim/simulator`（无 run.py/wrf）对比 `Typhoon-sim-wrf-legacy/run.py`（78 KB，首行 `# simulator/run.py`）与 `Typhoon-sim-wrf-legacy/wrf/`。
影响：README 的"模拟器"整节目前是死链接；同节还引用了 `simulator/env/wrfdir/`、`wrfdir/track.json` 这些随 `wrf/` 一起搬走的概念。

**MINOR-3｜`./假想风季/2028 global` 与主项目 `typhoon/2028` 同名不同内容。**
现象：主项目 93 个文件、假想风季 66 个，**前 38 个名字完全一致**，但 `md5sum "2801Gus wp012026.txt"` 分别为 `957f41ca…`（主项目）与 `1a92a050…`（假想风季）；主项目**多出 27 个** `bal*`/`bio*`/`bme*` 文件（含 `bal132025.txt`），假想风季则把 `bcp042028` 叫 `Pena`、主项目叫 `Pama`，`bep232028` 假想风季是 `Otto`、主项目是 `Virgil`。
证据：`diff <(ls Typhoon-sim/typhoon/2028) <(ls 假想风季/2028\ global)` 输出；两次 `md5sum`。
影响：无法判断哪边是权威版本；若主项目按名字读文件，两边的命名分歧会静默换成拿错数据。

**MINOR-4｜`./数据/cma_bst`（77 个 CMA 年鉴）没有被任何脚本消费。**
现象：全仓 `grep -rn "cma_bst" Typhoon-sim --include=*.py` → 0 命中；而它是 `气象知识/07_机构与自评口径.md:8,28`（CMA 2 分钟口径、六级分级）唯一能拿来做实证的官方一手料。
证据：grep 结果 + `ls 数据/cma_bst | wc -l` = 77。
影响：3 MB 官方年鉴处于"存了但没接"状态；CMA 口径校验目前只能靠 `xrq/官评` 间接做。

**MINOR-5｜`./气象知识/memory/` 的原子记忆库在主项目里 0 处引用。**
现象：`atoms.json` 1.76 MB、`links.json` 950 KB、`index_tags.json` 55 KB、`联想图.md` 58 KB，`memory/README.md:34-38` 给了 `build/search/related/graph/stats` 五个命令；但 `grep -rn "atoms\.json" Typhoon-sim` → 0 命中。
证据：上列。
影响：知识库的"可检索层"目前只服务于 agent 手工检索，没接进模拟器的参数标定回路。

**MINOR-6｜`./工具/Typhoon7(统计图表)` 只剩一个数据片段。**
现象：目录名承诺"统计图表"，实际只有 4 KB 的 `洋区范围.txt`（16 行洋区多边形定义），无代码。
证据：`ls -la 工具/Typhoon7\(统计图表\)` 单文件；`du -sh` = 4.0 K。
影响：命名与实际不符；那份洋区定义的等价物已存在于 `数据/数据统计/ocean_area.json` 与 `Area_ocean.json`。

**MINOR-7｜`typhoon3` 是"在用素材库"，但目录结构暗示它是废弃工程。**
现象：目录名 `typhoon3` 无任何说明文件（无 README），内含 4 个大视频（`Force Thirteen's Music Collection 2024.MP4` 233 MB、`F13经典音乐 (1).m4a` 99 MB）与 `评分表/`（7 张评分标准 PNG，如 `F 路径绘制.png`、`G 数据质量.png`）。同时 `map/[GLOBAL].png` 正被主项目当默认底图用。
证据：`ls -la typhoon3`；`Typhoon-sim/tools/ty4_render.py:24,38`。
影响：一个被 `ty4_render.py` 硬依赖的 1.7 GB 目录没有任何 README 说明其用途/来源/许可，删除或整理时会踩到主项目。

## 7. unresolved（未能确证）

1. **`./py` 与 `Typhoon-sim` 首提交的包含关系未直接证实。**
   卡在：`./py` 不是 git 仓，无法 `git log --follow`；我只能用"时间戳早于首提交 4 天 + 同族文件名"推断它是前身。需要：`Typhoon-sim` 首提交 `dd4f9d0` 的文件树（`git show --stat dd4f9d0` 或 `git ls-tree -r dd4f9d0`）才能证明"首提交里就有 `app/` 还是 `py/`"。

2. **`typhoon(动画) 1.x` → `./py` 之间是否还有别的中间版本，未知。**
   卡在：`Typhoon-sim-other` 只有三个版本目录（1.1.2/1.2.3/1.3.0），但 `1.3.0` 的 `typhoon/` 里放的是 **2025 年真实风季**（`03W wp032025(2561).txt`…），而 `./py` 已经在读 b-deck 全集。中间是否用过 `工具/Typhoon6.5(命名)` 做数据整备，无从判断。

3. **`./数据/cma_bst` 与 `./数据/_research` 是否被主项目之外的脚本消费，未穷尽。**
   卡在：我只 grep 了 `Typhoon-sim` 内的 `*.py`；`工具/`、`数据/` 自身也有大量 `*.py`（如 `数据/自评工具/pygame界面.py` 161 KB、`数据/数据统计/typhoon_path_analyzer.py` 60 KB），未逐个 grep。

4. **`./其它/一些话语.txt` 里的日期与实体无法对齐。**
   卡在：文件写 "For 2518 Ragasa(Peak1) 约2026.05"、"2614 鲸鱼 2026.08.05"、"2626 Surigae 2026.09.09"，但 `气象知识/05` 的 202519 浣熊是 2025-09，"2518 Ragasa/桦加沙"在 `气象知识/04:44` 也是 2025 年。这里 2026 的编号体系（26xx）与 2025（25xx）混用，无法确定是笔误还是两套纪年。

5. **`typhoon3` 的 1.7 GB 素材是否 100% 来自 Force Thirteen（F13）系列，未确证。**
   卡在：`map/sources/Blue_Marble_2002_bg21600_05.png` 显然是 NASA Blue Marble，`mainicon/*.mp4`、`summary/Summary-N-TC-C5.mp4`、`landfall/*.mp4` 风格与 F13 视频高度一致，且目录里直接放了 F13 的音乐合集。但没有 README/许可文件，无法判定自制 vs 搬运。

6. **`工具/暖心分析` 被删掉的 15 个算法文件是否有别处备份，未找到。**
   卡在：`交接文档.md:106-110` 列出被删的 `analysis.py`/`retrieval.py`/`background.py`/`modis_reader.py` 等；我在 `Typhoon-sim`、`数据`、`tmp_scratch` 均未搜到同名文件（仅做了文件名级观察，未做全盘 find）。

7. **`Typhoon-sim-wrf-legacy/README.md:20-24` 说 `simulator/env/`、`simulator/typhoons/`、`simulator/modes.py`、`simulator/mode_data/` 仍在主仓"被主仓的 tests/ 与库构建工具使用"——这条未逐条验证。**
   卡在：未检查 `Typhoon-sim/tests/` 是否真的 import 这些模块。

8. **`./假想风季/47wp` 的 47 号编号体系含义不明。**
   卡在：目录内文件名形如 `4701(030712z).txt`…`4710(070418z).txt`，编号 47 超出常见年度编号（25xx/26xx/27xx/28xx/29xx/30xx），且带 `(030712z)` 这种月日时后缀。无法判断是"第 47 个风季"还是别的分类维度。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 四代软件血缘/版本时间线 | `./Typhoon-sim-other/typhoon(动画) 1.1.2 / 1.2.3 / 1.3.0/typhoon_sim.py`、`./py/`、`Typhoon-sim/app/`；`git log` 尾部 `dd4f9d0 2026-06-28` |
| 哈呐星球分支是什么 | `./Typhoon-sim-other/Typhoon-sim-Haena/README.md:1-15`（历法 1 环=78120 s）、`:13-15`（已并入地球版 v1.4.4 后功能、simulator 未移植） |
| 哈呐分支的历代优化清单 | `./Typhoon-sim-other/Typhoon-sim-Haena/agent.md:1`（151 行，拖 1–拖 26 / 算 1–算 3） |
| Ty4（WikiProject Track Drawer）原版与 Python 重写 | `./数据/Typhoon4(绘图)/Typhoon4.exe`、`./数据/Typhoon4(绘图)/Typhoon4Py/README.md:1-20`、`Typhoon-sim/tools/ty4_render.py:8-16` |
| KZC / Chavas 风压公式 | `./工具/风压对应/说明.md:42-47`；`Typhoon-sim/simulator/typhoons/kzc.py:2` |
| 雷达测风定强方法学 | `./气象知识/02_台风定强与观测.md:55-68`；`./工具/雷达速度图/说明.txt` |
| 暖心反演降压模型全谱 | `./气象知识/09_贴吧增量_202609.md`（§1.4）；`./工具/暖心分析/交接文档.md:52-110`；原始帖 `./气象教材/lz2.txt` |
| 飞机实测可视化工具 | `./工具/实测分析/说明.md:1-30`、`./工具/实测分析/核心/` |
| 贴吧抓取器 | `./工具/帖子统计/帖子统计.py`、`说明.txt:1-25`；产出 `./数据/贴吧/tieba_*/` |
| 向日葵卫星下载/出图 | `./工具/Typhoon8(绘图脚本)/使用说明.txt:1-25`、`app.py`、`colormap_data.json` |
| 主项目默认底图 | `./typhoon3/map/[GLOBAL].png` ← `Typhoon-sim/tools/ty4_render.py:38` |
| 主项目音效/图标来源 | `./typhoon3/sound/{random,damages,landfall}/`、`./typhoon3/gui/simpleicon/` ↔ `Typhoon-sim/assets/{sound,simple}/` |
| 观测集（xrq/miwu/官评/自评） | `./数据/xrq/`、`./数据/miwu/`、`./数据/官评/`、`./数据/自评/规则.txt:1-17` |
| b-deck 源站可用性调研（2026-10-01） | `./数据/_research/JTWC_bdeck镜像调研.md:1-30`、`JTWC_bdeck_NOAA源_复测报告.md`、`站点实测结果.csv` |
| 站点清单与死链替代 | `./气象知识/11_TC资料站点与数据源.md:32-45`（在用源）、`:146-165`（死链） |
| WRF 管线与"核爆扰动"实验设计 | `./Typhoon-sim-wrf-legacy/wrf/README.md:5-25`、`./Typhoon-sim-wrf-legacy/wrf/核爆扰动注入方案.md:1-24` |
| 架空风季数据 | `./假想风季/{2028 global,29wp,30wp,47wp,2025(real)wp+2.0}/` |
| 知识库原子检索 | `./气象知识/memory/README.md:1-45`、`记忆工具.py`（`build/search/related/graph/stats`） |
| 知识库原始提炼稿 | `./气象知识/_staging/<TID>.md`（15 份）、`_gen09.py` |
| 气象学教材 PDF | `./气象教材/{动力气象学,大气探测,大气科学基础,天气学,气候学,雷达气象学}/`、`./气象教材/风迷自编教材/` |
| 雷达速度图优化草稿 | `./tmp_scratch/make_baseline.py:1-20`、`demo_fixA.py:1-25`、`summarize_probe.py:1-30` |
| 与主项目无关的创作文案 | `./其它/一些话语.txt`、`./其它/rolling cyclone.txt` |

---

### 交付自检（对照 `_BRIEF.md` §5）

- 行数 ≥ 250：✅
- 8 节齐全：✅（§1 清单/§2 数据流/§3 机制（占全文 ~45%）/§4 资产/§5 运行验证/§6 问题/§7 unresolved/§8 速查）
- `文件:行` 证据 ≥ 30 处：✅（§3.1 表格 11 篇 × 多行 + §3.2/§3.3/§3.4/§6 共 100+ 处）
- unresolved ≥ 5 条：✅（8 条）；分级问题 ≥ 5 条：✅（1 BLOCKER + 2 MAJOR + 7 MINOR）
- 只读遵守：✅ 未修改/未删除任何文件；未运行 `./工具` 下任何程序
