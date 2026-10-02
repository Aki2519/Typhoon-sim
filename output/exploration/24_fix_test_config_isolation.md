# 24 堵死"测试写用户 config.json"（第二次事故的收口）

> 执行者：`app-feat`（task-21）　模式：**实施 + 确定性验证**
> 写作用域：`tests/conftest.py`、`app/config_store.py`、本报告
> 探针：`G:\tmp\probe_config_leak.py`、`G:\tmp\probe_conftest_layers.py`
> 全程未改 `AppConfig` 默认行为、未改生产入口；用户 `config.json` 在所有实验后**逐位复原**。

## 一句话定位

把"测试/探针进程悄悄改写用户 `config.json`"这条已发生两次的路从 pytest 侧彻底堵死：**在 app 模块被 import 之前**就把配置路径重定向到临时文件，并在会话结束时校验用户文件、被改就还原并让本次 pytest 变红。

## 1. 清单与职责（改动全清单）

| 文件 | 改动 | 改前 sha256 | 改后 sha256 |
|---|---|---|---|
| `app/config_store.py` | 新增 `TSIM_CONFIG_FILE` 环境变量 + `set_path_override()/get_path_override()/resolve_config_path()`；`ConfigStore.__init__` 走 `resolve_config_path()` | `d3dcc679…b2fee486` | `47c97ce7…ce8197d394` |
| `tests/conftest.py` | 重写为 **L0–L3 四层隔离**（模块级环境变量 / 进程级覆盖 / 模块全局补丁 / 兜底包装 + 会话结束失效保护） | `0aead882…bd85cc7768e2d3` | `ffd2a6da…e3a2fde7b69d2` |
| `config.json`（用户文件） | **未改** | `677e09df…f97a533cda7` | 同左（逐位不变，见 §5） |

**生产语义零变化**：`resolve_config_path()` 在无覆盖时 `return path`；`set_path_override` 与 `TSIM_CONFIG_FILE` **只有测试/工具会设置**，生产代码一个调用点都没有（`grep` 可核）。

## 2. 数据流 / 调用链（写入路径与四层隔离的落点）

```
事故链（第二次事故就是这条）:
  某人用"把 hemisphere 切成 south"验证设置守卫生效
    → Settings.apply_settings()                      app/settings.py:2365-…
    → Settings._on_ok()                              app/settings.py:1330-1334
    → TySim.save_config(force=True)                  app/ty_sim.py:441-464
    → ConfigStore.commit(cfg, force=True)            app/config_store.py:91（唯一落盘入口）
    → os.replace(tmp, self.path)                     app/config_store.py:120
    ⇒ self.path 由 ConfigStore(CONFIG_FILE) 决定     app/ty_sim.py:179
       CONFIG_FILE = "config.json"（相对路径 → 仓库根）app/constants/layout.py:32
       ⚠ 若该进程**不经 pytest**（裸探针），旧 conftest 完全不生效 ⇒ 直接写用户文件

四层隔离（每层挡一类缺口）:
  L0  tests/conftest.py 模块顶层 os.environ['TSIM_CONFIG_FILE']=tmp
        └─ 早于任何 app 模块 import；被 app/config_store.resolve_config_path() 读取
  L1  fixture 里 config_store.set_path_override(tmp)（显式 API，生产不调用）
  L2  fixture 里把 4 处 CONFIG_FILE 模块全局指向 tmp
        · app.constants / app.ty_sim / app.constants.layout / app.ty_sim_mixins.keyboard_mixin
        · 第 4 处是**旧版漏掉的**（keyboard_mixin 读它做"恢复地图范围"）
        · ⚠ 必须**两趟**（先全部 import 再统一打补丁）—— 见 §3.4
  L3  a) 包一层 AppConfig.save：测试期把指向真实 config.json 的直调改道到 tmp
      b) pytest_sessionfinish：比对用户文件字节, 被改 → 还原 + session.exitstatus=1
```

## 3. 核心机制详解

### 3.1 旧隔离为什么不够（两条缺口，均为实测）

旧版（`_backup/state_v2/conftest.py`，`0aead882…`）只在 **session fixture 内**给 3 个模块全局打补丁。

| 缺口 | 机制 | 实测证据（`probe_conftest_layers.py`） |
|---|---|---|
| **① 补丁晚于 import** | pytest 先 import 测试模块、再跑 fixture；任何"import 期就读/构造"的路径躲过补丁 | 旧 conftest 里 `hasattr(mod,'_TMP_CONFIG') = False`（无模块级设置）；加载它之后立刻 `ConfigStore('config.json').path` = **`G:\气象\Typhoon-sim\config.json`**（真实文件） |
| **② 不经 pytest 完全不生效** | `python 探针.py` 不加载 conftest | `probe_config_leak.py` Arm 0：直接用**改前**的 `config_store` 调 `commit()` ⇒ 用户文件 sha `677e09df…` → **`dd78b45f…`（被改动 = True）** |
| **③（附带发现）漏了第 4 处全局** | 旧版只补 `app.constants` / `app.ty_sim` / `app.constants.layout`，而 `app/ty_sim_mixins/keyboard_mixin.py:10` 也 `from ..constants import … CONFIG_FILE`，在 `:136` 用它读配置 | 静态 grep；已补入 L2 |

### 3.2 复现（Lead 要求的"先证明会改"）

`probe_config_leak.py` 三个臂（每臂结束都把用户文件按原始字节还原）：

| 臂 | 代码 | 目标路径 | 用户文件 sha 变化 | 结论 |
|---|---|---|---|---|
| **0 BEFORE** | `_backup/state_v2/config_store.py` | `…\Typhoon-sim\config.json` | `677e09df…` → `dd78b45f…` | **泄漏复现** ✅ |
| 1 AFTER（裸脚本、无环境变量） | 现有 `config_store` | `…\Typhoon-sim\config.json` | `677e09df…` → `dd78b45f…` | **残余缺口**（见 §6） |
| **2 AFTER + 重定向** | 现有 `config_store` + `TSIM_CONFIG_FILE` | `%TEMP%\tcfg_leak_xxx\config.json` | `677e09df…` → **`677e09df…`（不变）** | 重定向生效 ✅（临时文件 62 B） |

> 说明：Arm 0 的写入路径与事故链完全一致（`commit()` 就是 `apply_settings()→save_config()` 的终点）。

### 3.3 新隔离生效的逐层证据

`probe_conftest_layers.py`（同一进程内对比新旧 conftest）：

```
A) 旧 conftest：无模块级 env；ConfigStore.path = G:\气象\Typhoon-sim\config.json  ⇒ 指向用户真实文件（缺口）
B) 新 conftest：模块顶层 TSIM_CONFIG_FILE = %TEMP%\tcsim_test_cfg_xxx\config.json
   ⇒ 立刻构造的 ConfigStore.path = 同一临时路径（已重定向）
C) 驱动新 fixture：
   keyboard_mixin.CONFIG_FILE == 临时路径  ⇒ 第 4 处全局已补丁 = True
   AppConfig.save 已包装 = True
   config_store 覆盖生效 = True
   经完整链路 commit()；真实文件 sha 677e09df… → 677e09df… **不变**；临时文件已写入 62 B
```

### 3.4 ⚠ 我在第一版里自己踩到的坑：补丁循环必须"两趟"

第一版 L2 写成**一趟**（边 import 边打补丁），顺序是
`app.constants → app.ty_sim → app.constants.layout → keyboard_mixin`。
后果：第 1 项已把 `app.constants.CONFIG_FILE` 改成临时路径，第 4 项才首次 import `keyboard_mixin`，而它是 `from ..constants import … CONFIG_FILE` ⇒ **import 期就绑到了临时路径**；于是 `old` 记录成临时值，**复原变成空操作**。

- 发现方式：我自己的探针 `probe_conftest_layers.py` 在"先启动 fixture、后 import keyboard_mixin"的顺序下打印出 `复原后: keyboard_mixin.CONFIG_FILE = <临时路径>`，与另一顺序（先 import 再启动 fixture）的结果**矛盾**。
- 定位：隔离复现（只测一次 before/during/after）+ 打印 `sys.modules` 身份，确认"还原块确实执行了、env 已清、临时目录已删"，但该全局仍是临时值 ⇒ 只能是 `old` 记错了。
- 修法：**先全部 import、再统一记录+打补丁**（`tests/conftest.py` 的 `_TARGETS` 两趟循环）。
- 修后实测：在**同样**的"后 import"顺序下，`after == ('config.json','config.json')`（`keyboard_mixin` 与 `app.ty_sim` 都正确复原）。

> 这条值得单独记：**"打了补丁"和"能复原"是两件事**；这类模块级全局补丁如果顺序不对，会留下静默的脏状态。

### 3.5 L3 失效保护实测（不测的兜底等于没有兜底）

| 情形 | 期望 | 实测 |
|---|---|---|
| 用户文件未改动 | 不误报、`exitstatus` 不变 | `exitstatus = 0` ✅ |
| 人为把 `hemisphere` 改成 south | 还原原始字节 + 判定失败 | 打印 `[conftest 失效保护] …`；**文件已还原为原始字节**；`exitstatus = 1` ✅ |

## 4. 输入输出资产

| 路径 | 说明 |
|---|---|
| `G:\tmp\probe_config_leak.py` | 泄漏复现 + 重定向验证（三臂，自我还原） |
| `G:\tmp\probe_conftest_layers.py` | 新旧 conftest 行为差异 + L1/L2/L3 逐层证据 |
| `output/exploration/_backup/state_v2/config_store.py` | 改前基线（`d3dcc679…`），Arm 0 用它复现 |
| `output/exploration/_backup/state_v2/conftest.py` | 改前基线（`0aead882…`） |
| `output/exploration/_backup/config.json.after-restore` | Lead 还原后的用户配置快照 |
| 运行时临时物 | `%TEMP%\tcsim_test_cfg_*\config.json`（会话结束 `rmtree`） |

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe   # 本机 shell 吞 $VAR, 请写全路径

# 1) 复现 + 重定向验证（探针自己会还原用户文件）
$P 'G:\tmp\probe_config_leak.py'
# 2) 新旧 conftest 行为差异 + L1/L2/L3
$P 'G:\tmp\probe_conftest_layers.py'
# 3) 验收批次（改前记一次 sha256, 跑完再记一次）
sha256sum config.json
$P -m pytest tests/test_config.py        -q --no-header
$P -m pytest tests/test_ace_engine.py    -q --no-header
$P -m pytest tests/test_control_panel.py -q --no-header
$P -m pytest tests/test_sim_settings.py  -q --no-header
sha256sum config.json
```

### 验收结果（全部**实测**）

| # | 判据（Lead 指定） | 结果 |
|---|---|---|
| 1 | 改前：跑测试后 `config.json` sha256 **变化**（复现） | ✅ Arm 0：`677e09df…` → `dd78b45f…` |
| 2 | 改后：跑**同一批**测试后 sha256 **逐位不变** | ✅ 跑前 = 跑后 = **`677e09dffe603ff4150532533219905d674cf50e5461f6e4b0ad1f97a533cda7`** |
| 3 | 测试仍通过（`test_sim_settings` 基线 14） | ✅ `test_config` **5** / `test_ace_engine` **19** / `test_control_panel` **15** / `test_sim_settings` **14** ⇒ **合计 53 passed / 0 failed** |
| 4 | 四层隔离逐层生效 | ✅ §3.3（L0 env / L1 override / L2 四处全局 / L3a 包装 / L3b 校验） |
| 5 | L3 失效保护可触发且能还原 | ✅ §3.5 |

### 原始输出摘录（实测，未加工）

```
# probe_config_leak.py
用户 config.json 起始 sha256 = 677e09dffe603ff4  长度 4064
=== Arm 0  BEFORE（改前的 config_store）===
    commit() -> True   目标路径 = G:\气象\Typhoon-sim\config.json
    用户文件 sha: 677e09dffe603ff4 -> dd78b45fc3cff58c   被改动 = True
=== Arm 1  AFTER（改后的 config_store, 未设环境变量）===
    用户文件 sha: 677e09dffe603ff4 -> dd78b45fc3cff58c   被改动 = True     ← 残余缺口
=== Arm 2  AFTER（改后的 config_store + TSIM_CONFIG_FILE）===
    目标路径 = C:\Users\...\Temp\tcfg_leak_ufhnot3v\config.json
    用户文件 sha: 677e09dffe603ff4 -> 677e09dffe603ff4   被改动 = False ✅
    重定向目标内容 = True  字节 = 62
用户 config.json 最终 sha256 = 677e09dffe603ff4  与起始相同 = True

# probe_conftest_layers.py
A) 旧 conftest：无模块级 env；ConfigStore.path = G:\气象\Typhoon-sim\config.json  ⇒ 真实文件
B) 新 conftest：TSIM_CONFIG_FILE = %TEMP%\tcsim_test_cfg_0ik1f8jn\config.json
   新版下 ConfigStore.path = 同一临时路径  ⇒ 已重定向 = True
C) keyboard_mixin.CONFIG_FILE = <临时路径> ⇒ 第 4 处全局已补丁 = True
   AppConfig.save 已包装 = True ; config_store 覆盖生效 = True
   经完整链路 commit()=True; 真实文件 sha 677e09df… -> 677e09df… 不变 = True
   临时文件已写入 = True 62 B

# 验收批次（同一 sha 前后夹住）
跑前 config.json sha256 = 677e09dffe603ff4150532533219905d674cf50e5461f6e4b0ad1f97a533cda7
   test_config 5 passed | test_ace_engine 19 passed | test_control_panel 15 passed | test_sim_settings 14 passed
跑后 config.json sha256 = 677e09dffe603ff4150532533219905d674cf50e5461f6e4b0ad1f97a533cda7   ← 逐位不变 ✅
```

## 6. 观察到的问题 / 风险

**严重**

1. **裸探针（不经 pytest）仍会写用户配置**（Arm 1 实测）。本轮修的是 pytest 侧；`python my_probe.py` 若直接调 `ConfigStore.commit()` / `AppConfig.save()` 且**不设** `TSIM_CONFIG_FILE`，依然会写真实文件。
   - 缓解（本轮已提供）：探针开头一行 `os.environ['TSIM_CONFIG_FILE'] = <临时路径>`（或 `from app.config_store import set_path_override; set_path_override(tmp)`）即可；`config_store.py` 的 docstring 已写明。
   - **建议**：把它变成硬约束——在 `tools/` 里给一个统一入口（如 `tools/_probe_env.py`），或由 Lead 决定是否授权改 `app/config.py`（那样 `AppConfig.save()` 也能读重定向，见 §7.2）。
2. **`transparency`：本轮改动不会入库。** `tests/` 整目录在 `.gitignore:12`，且 `tests/conftest.py` **未被跟踪**（`git ls-files tests/conftest.py` 为空；`tests/` 只有 11 个文件被跟踪）。
   ⇒ 我的 `conftest.py` 改动**只存在于磁盘**，任何 `git checkout`/干净克隆都会丢掉它（`app/config_store.py` 是跟踪文件，不受影响）。**建议把 `tests/conftest.py` 强制入库**（`git add -f`）或从 `.gitignore` 里放行 conftest。

**一般**

3. **`config.json` 是**被跟踪**文件**（`git ls-files config.json` 非空）⇒ 测试写它会直接出现在 `git status` 里。这是有用的检测信号，但也说明**只需一次误写就会污染工作树**。
4. **旧版 conftest 漏了第 4 处全局**（`keyboard_mixin.CONFIG_FILE`）。它只被用来**读**配置（`restore_map_region_from_config`，`keyboard_mixin.py:135-143`），所以不是写入泄漏；但它会让"F12/`_key_r` 恢复地图范围"这类用例**读到用户的真实配置**，属隔离不完整。已补。

**建议**

5. **把"用户文件不可被测试改动"纳入 CI 校验**：例如一个 `tools/check_config_untouched.py`，跑完测试后比对 `config.json` 的 sha256（与本轮 L3 同一判据），失败即红。L3 目前只在 pytest 会话内生效。

## 7. 值得保留的现状 / unresolved

### 7.1 值得保留的现状

1. **`ConfigStore` 的单一落盘入口设计是对的**：`commit()` 是唯一写入点，带备份 + 读回校验 + 原子替换（`app/config_store.py:91-132`），失败时原文件一字节不动。正因为它**只有一个入口**，本轮的隔离才能一处生效（`resolve_config_path` 加在 `__init__` 一处即可覆盖全部调用方）。
2. **`RUNTIME_FIELDS` 区分"用户意图 vs 运行时状态"**（`config_store.py:48-52`）已经解决了"窗口尺寸被运行值覆盖"那类问题，不要动。
3. **旧 conftest 的三处补丁是有价值的起点**：它证明了"测试走完整 load/save 路径但落在 tmp"这个思路可行；本轮只是把它**提前到 import 之前**并补齐缺口，没有改变该思路。
4. **`.bak` 机制仍在**：`commit()` 首次写盘前会把原文件复制成 `config.json.bak`（`:106-109`）⇒ 即使发生误写，用户配置仍有可回退副本（本机 `.bak` 存在）。
5. **完全可回退**：改动只涉及 2 个文件，且都只是"加一层重定向"，回退就是 `cp output/exploration/_backup/state_v2/{config_store.py} app/` 与 `cp …/state_v2/conftest.py tests/`。

### 7.2 unresolved

1. **裸探针的默认安全性未解决**（§6.1）。要做到"任何进程默认都不能写用户 config.json"，需要在**生产入口之外**再加一层判据（例如 `app/config.py` 的 `save()` 也读重定向），而那超出了本任务写作用域 —— 我按 Lead 的要求**没有先斩后奏**，如需要请授权。
2. **`AppConfig.save()` 的直调**只在 pytest 会话内被 L3a 包装挡住；裸探针直接 `cfg.save("config.json")` 仍会写（与 §6.1 同源）。
3. **未在"真实 GUI 会话"里验证**：本轮所有验证都是 headless（无 `python main.py`），因此"生产路径（用户点确定保存）仍然正常写配置"这一点是**按代码推断**（`resolve_config_path` 无覆盖时原样返回），未做端到端 GUI 实测。**这是本轮最需要补的一条**。
4. **未验证 `TSIM_CONFIG_FILE` 与 pytest-xdist 并发**：多 worker 会各自 `mkdtemp`，理论上互不干扰，但未实测（本项目不用 xdist）。
5. **L3 触发后的"是否已入库"未联动**：L3 会还原文件并让 pytest 变红，但不会自动把"哪条路径写的"定位出来（只打印路径与长度差）。若要更强诊断，可在 `ConfigStore.commit()` 里记录 `traceback` 尾巴。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 唯一落盘入口 | `app/config_store.py:91-132`（`ConfigStore.commit`） |
| 路径重定向入口（本轮新增） | `app/config_store.py:39-71`（`PATH_OVERRIDE_ENV`/`set_path_override`/`resolve_config_path`） |
| `ConfigStore` 取路径处 | `app/config_store.py:84-87`（`__init__` 里的 `resolve_config_path(path)`） |
| 事故链终点 | `app/ty_sim.py:441-464`（`save_config`）→ `app/settings.py:1330-1334`（`_on_ok`） |
| 4 处 `CONFIG_FILE` 模块全局 | `app/constants/layout.py:32`、`app/ty_sim.py:11`、`app/ty_sim_mixins/keyboard_mixin.py:10,136` |
| 四层隔离 | `tests/conftest.py`：L0 模块顶层 env；fixture 内 L1/L2/L3a；`pytest_sessionfinish` = L3b |
| 两趟补丁（防 `old` 记错） | `tests/conftest.py` 的 `_TARGETS` / `_mods` 两趟循环 |
| 失效保护（还原 + 判红） | `tests/conftest.py` 的 `pytest_sessionfinish` |
| 改前基线 | `output/exploration/_backup/state_v2/{conftest.py,config_store.py}`、`SHA256_STATE_V2.txt` |
| 本轮探针 | `G:\tmp\probe_config_leak.py`、`G:\tmp\probe_conftest_layers.py` |
