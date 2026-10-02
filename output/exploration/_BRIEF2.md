# 第二轮简报：把 unresolved 追到底

> 第一轮已交付 7 份报告（`00`–`06` + `SUMMARY.md`），见 `output/exploration/`。
> 本轮目标是**闭合第一轮明确列出的未确证项**，方法是**做实验**，不是再读一遍代码。

## 0. 铁律（与第一轮相同）

1. **只读工程**。不改任何源码/配置/资产。
2. **唯一可写文件是你自己那份报告**（写在 `output/exploration/`）。
3. 探针脚本写到**工作区外**（`G:\tmp\` 或 WSL `/tmp/`），不要写进仓库。
4. **每条结论带 `文件:行` 或"实测数字 + 复现命令"**。跑不出来的必须写 `unresolved`，不许编。
5. 报告用中文，沿用第一轮的 8 节骨架（见 `_BRIEF.md`）。
6. **报告必须区分「实测」与「推断」**。

## 1. 环境（已实测，直接采信）

| 项 | 值 |
|---|---|
| 工作区 | `G:\气象\Typhoon-sim` = `/mnt/g/气象/Typhoon-sim` |
| **真实运行时** | `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`（Python 3.14.2） |
| 已装 | pygame-ce 2.5.7、pytest 9.1.1、numpy 2.5.1、scipy 1.18.0、matplotlib 3.11.0、xarray、netCDF4、pandas、PIL、requests |
| WSL python3 | 只有 numpy/scipy/xarray/netCDF4/pandas，**无 pygame/pytest**（只适合纯 numpy 探针） |
| 中文输出乱码 | Windows 解释器输出 GBK，管道里加 `\| iconv -f GBK -t UTF-8`（`PYTHONIOENCODING` 无效） |
| **本会话 bash 的坑** | `$VAR` 不展开、`for` 循环常失效。**写单行直白命令，不要用变量和循环** |
| **read 工具的坑** | `read` 不接受 `/mnt/g/...`，要用 `G:\气象\...` 绝对路径 |

## 2. 已知的性能预算（第一轮实测，用来估你的实验时长）

| 场景 | 实测耗时 |
|---|---|
| `V.sim_step(sim, 6.0)` **默认**（12 成员 72h 集合开着） | **9.3 s / 步** |
| 同上，**关掉 `forecast`** | **430 ms / 步** |
| `dataMode=True` 的 `env_update` 冷启动 | 3.99 s（解析分支 0.567 s） |
| `run_tc_sim.py --days N` | 由上面推算：365 天 ≈ 1460 步 |

> **重要**：做长跑实验前先确认 `forecast` 是否开着。默认开着会让 1 年跑变成 ~3.8 小时。
> 关掉后 1 年约 10–20 分钟。**任何长跑都要用后台作业 + 每个实验单独记耗时。**

## 3. 本轮四个分区（互不重叠）

| 任务 | 负责人 | 只写这个文件 | 类型 |
|---|---|---|---|
| 08 端到端实跑 + 四象限 A/B | sim-run | `output/exploration/08_endtoend.md` | **计算密集**（串行！） |
| 09 平流数值验证 + render/fill/import_tc 补读 | sim-numerics | `output/exploration/09_numerics_render.md` | 中等 |
| 10 标定常数溯源 | sim-calib | `output/exploration/10_calib_constants.md` | 纯文本/轻计算 |
| 11 资产链与死码影响面 | sim-assets | `output/exploration/11_assets_deadcode.md` | 纯代码阅读 |

**因为 sim-run 是计算密集的，其余三位请避免跑长模拟**，把 CPU 让给它。轻量探针（秒级）随意。

## 4. 第一轮的关键结论（本轮的前提，不必重新证明）

- `simulator/run.py`、`simulator/wrf/`、`requirements.txt` **不存在**；README 运行段全死。
- 唯一真入口是 `python main.py`；应用内按 `H` 进模拟模式。
- 内核 A（真实大气 WRF）**零调用方**；内核 B（`simulator/typhoons/`）在生产代码里**零引用**。
- 真正活着的是 `simulator/simcore/`：99 个 tools 脚本 + 4 个 app 文件（`sim_mode_mixin.py` 等，全部是**函数内延迟 import**）。
- `_advect_env`（半拉格朗日平流）在 `simulator/simcore/core.py:4173-4292`，**不在** `clouds/render.py`。
- 默认 `dataMode=False` vs GUI/标定工具 `True` ⇒ 生成率 ≈20/年 vs ≈385/年。
- 事实死码：`cloud_step`（`core.py:4210-4279`）、`vortex_wind`/`_arr`（`:300-353`/`:674-740`）、`tc_deficit`（`:292-297`）。

## 5. 交付标准

- 报告 ≥ 200 行，8 节齐全。
- **至少一个真实的对照实验**（有对照组、有数字、有复现命令）——这是本轮与第一轮的区别。
- 每条 unresolved 要写清"卡在哪、需要什么才能确证"。
- 完成后 `team_task_update action=complete`，并 `send_message` 给 `lead` 发 ≤15 行摘要。
