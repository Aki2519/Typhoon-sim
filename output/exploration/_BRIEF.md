# 探索任务统一简报 (Team Lead 下发, 2026-10-01)

## 0. 任务总目标

用户要求：**先探索整个 `G:\气象` 文件夹；然后着重、仔细地探索"这个模拟器"。**

最终交付是一套中文探索报告，落到 `output/exploration/`。每个成员只写自己那一份报告文件。

## 1. 环境事实（已由 Lead 实测，直接采信）

| 项 | 值 |
|---|---|
| 工作区根（Windows） | `G:\气象\Typhoon-sim` |
| 工作区根（WSL） | `/mnt/g/气象/Typhoon-sim` |
| 父目录 | `G:\气象`（含 11 个兄弟目录，见任务 05） |
| 项目规模 | ~101k 行 Python，3.9 GB |
| 版本控制 | git，当前有大量未提交改动（`M`/`??`），**基线不干净** |
| **项目真实运行时（首选！）** | `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe` = `C:\Users\Yukioto\AppData\Local\Python\pythoncore-3.14-64\python.exe`，Python 3.14.2 |
| 真实运行时已装 | **pytest 9.1.1、pygame 2.5.7**、numpy 2.5.1、scipy 1.18.0、matplotlib 3.11.0、PIL、xarray、netCDF4、pandas、requests |
| 备用 Python（WSL） | `/usr/bin/python3` = 3.14.4，只有 numpy 2.3.5、scipy 1.16.3、xarray、netCDF4、pandas、PIL |
| WSL Python 缺 | pytest、pygame、matplotlib、h5py |
| 已实测可导入 | `import simulator.simcore.core` ✅、`import simulator.typhoons.sim` ✅ |
| **已实测测试可跑** | `cd /mnt/g/气象/Typhoon-sim && timeout 180 <真实运行时> -m pytest tests/test_ace_engine.py -q --no-header` → **19 passed in 0.95s** |

> ⚠️ **凡"能不能跑"的判断一律用上面那个 Windows 解释器。** 禁止全量 pytest（58 个文件），按需跑单文件，每个 `timeout 180`。
> 报告里必须区分「我实测跑的」和「我只读代码推断的」。
| README 与现实的偏差 | README 里的 `python simulator/run.py` 与 `simulator/wrf/` **在磁盘上不存在**（Lead 已核实）。README 该节是过时/失效文档，请以此为线索继续挖 |

> 用 `read` / `grep` / `glob` 工具读文件，不要用 `cat`/`find`/`rg`。
> 路径用相对工作区根的写法即可，例如 `simulator/simcore/core.py`。

## 2. 铁律

1. **只读探索。** 不修改、不格式化、不删除任何源码 / 配置 / 资产 / git 状态。
2. **唯一可写文件是你自己那份报告。** 不要写别的文件（临时脚本写到 `/tmp/`，不要写进工作区）。
3. **每条结论必须带证据**，格式 `文件路径:行号`（单行用 `:123`，区间用 `:123-145`）。没有证据的猜测必须显式标为"推测"。
4. **不确定就写 `unresolved`**，不要编。宁可少写也不要写没验证过的东西。
5. 不要跑 GUI（pygame 不可用）。不要跑全量 pytest。
6. 报告用中文。

## 3. 报告统一结构（照抄这个骨架）

```markdown
# <编号> <标题>

## 一句话定位
（≤60 字，说清这块东西到底是什么、为谁服务）

## 1. 清单与职责
| 文件 | 行数 | 职责 | 关键类 / 函数 |
|---|---|---|---|

## 2. 数据流 / 调用链
（ASCII 图或缩进列表，标注每一步的 文件:行）

## 3. 核心机制详解
（这一节的篇幅应占全文 40%+。写清算法/物理公式/状态机/数据结构，
 每条都带 文件:行 引用。公式用行内代码或代码块写出来。）

## 4. 输入输出资产
| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|

## 5. 如何运行 / 如何验证
（可复现的命令；标出哪些在本环境跑不了及原因）

## 6. 观察到的问题 / 风险 / 不一致
（分级：BLOCKER / MAJOR / MINOR。每条：现象 → 证据(文件:行) → 影响）

## 7. unresolved（未能确证）
（列清单，写清卡在哪、需要什么才能确证）

## 8. 关键位置速查表
| 想找什么 | 去哪看 |
|---|---|
```

## 4. 分工与写作用域（互不重叠）

| 任务 | 负责人 | 只写这个文件 |
|---|---|---|
| 01 模拟内核 simcore | sim-core | `output/exploration/01_simcore.md` |
| 02 环境场数据管线 | sim-data | `output/exploration/02_sim_data.md` |
| 03 应用内模拟器与渲染 | app-sim | `output/exploration/03_app_sim.md` |
| 04 应用数据模型/统计/工具 | app-feat | `output/exploration/04_app_feat.md` |
| 05 整个气象文件夹 | parent | `output/exploration/05_parent_survey.md` |
| 06 文档/验收/测试/工具方法论 | evidence | `output/exploration/06_evidence.md` |
| 07 汇总 | **Lead** | `output/exploration/SUMMARY.md` |

发现别的分区里的东西，不要自己写报告——用 `send_message` 告诉 Lead。

## 5. 交付标准

- 报告 ≥ 250 行，结构完整（8 节齐全）。
- 至少 30 处 `文件:行` 证据引用。
- ≥ 5 条 unresolved 或 ≥ 5 条分级问题（有多少写多少，凑数禁止）。
- 完成后用 `send_message` 给 `lead` 发一条 ≤15 行的交接摘要：报告路径 + 3 条最重要发现 + unresolved 数量。
