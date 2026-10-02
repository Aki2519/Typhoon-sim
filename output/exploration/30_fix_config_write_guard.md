# 30 从根上堵掉"非应用进程写用户 config.json"（第 3 次事故的收口）

> 执行者：`app-feat`（task-27）　模式：**实施 + 两端验收**
> 写作用域：`app/config_store.py`、`main.py`、`tests/conftest.py`（未改）、本报告
> 探针：`G:\tmp\probe_config_guard.py`（A/B/D/E 四臂，各自独立进程）
> 用户 `config.json` 在每个臂结束时**按原始字节还原**并在末尾复核；全程 sha 未被留下改动。

## 一句话定位

把"谁有资格写用户配置"从**纪律**（探针要记得设 env，已失败三次）改成**身份**：只有应用本体（`main.py`）显式声明过自己，`ConfigStore` 才允许写用户的 `config.json`；其他一切进程（探针/测试/工具）的写自动落到临时文件并打一条 warning。

## 1. 问题与三次事故

| 次 | 时间 | 谁 | 后果 |
|---|---|---|---|
| 1 | 2026-10-01 前 | 未知 | `04_app_feat.md` 记为"已发生过的真实事故" |
| 2 | 轮五 | 某位测试者用"切 `hemisphere` 验证设置守卫" | 用户 `hemisphere` → south，Lead 还原 |
| **3** | **轮七** | **`sim-run` 的 W5 裸探针** | 用户 `hemisphere` → 再次 south，Lead 还原 |

**三次同一形态**，且都能归到同一条写入链（轮五已由 `24_fix_test_config_isolation.md` §2 确立）：

```
Settings.apply_settings()  →  _on_ok()  →  TySim.save_config()  →  ConfigStore.commit()
                                                                    ↑ 唯一落盘入口
```

轮五我只堵住了 **pytest 侧**（`tests/conftest.py` 四层隔离），并在报告里写明：

> **裸探针仍然会写**（Arm 1 实测）。要"任何进程默认都写不进去"就得让 `app/config.py` 的 `save()` 也读重定向 —— 按你的要求我没有动它，需要请授权。

本轮授权后，我选了比"save() 读重定向"**更强**的方案：不要求探针配合，而是要求**应用自证身份**。

## 2. 设计

### 2.1 语义

> **唯一合法写用户 `config.json` 的，是应用本体自己**（用户点"确定"那一刻）。

因此判据不是"探针记得设 env"，而是"**这个进程声明过自己是应用吗**"。

### 2.2 路径判定优先级（`resolve_config_path`）

| 序 | 条件 | 结果 | 说明 |
|---|---|---|---|
| 1 | 显式 override（`set_path_override()` 或 `TSIM_CONFIG_FILE`） | 用 override | 测试/工具的**显式**意图，最高优先 |
| 2 | 目标 basename ≠ `config.json` | **原样返回** | 只保护用户配置这一个文件，不干扰任何其他路径（比"非应用一律重定向"**窄**，爆炸半径更小） |
| 3 | 应用本体（`declare_app()` 已调 / `TSIM_APP_LAUNCH` 已设） | **原样返回** | ✅ 生产语义 —— 用户设置照常存下来 |
| 4 | 逃生开关 `TSIM_CONFIG_ALLOW_WRITE=1` | 原样返回 | 给确实要写真实配置的合法工具 |
| 5 | 其余（裸探针/测试/工具） | **回落临时文件** + 每进程一次 warning | 三次事故的形态全落在这里 |

### 2.3 与备选方案的取舍

| 方案 | 评价 |
|---|---|
| ① `AppConfig.save()` 读重定向（我上轮提的） | 只堵住"经过 save()"的路；**仍需探针配合**（或同时改 `app/config.py`）。本轮**未采用为唯一手段** |
| ② **应用自证身份（本轮采用）** | 探针**什么都不用做**就写不进去；生产只需 `main.py` 提前声明一次 |
| ③ 把 `MapManager` 式"全部路径都重定向" | 会干扰工具写自己的 `config.json`；已收窄为"仅 basename == config.json"（§2.2 第 2 条） |
| ④ 直接让 `main.py` 设 `TSIM_APP_LAUNCH=1` 而不调函数 | 也可行；但函数调用是**进程内权威标记**，不受"子进程继承/环境被污染"影响。本轮**两者都做**（`declare_app()` 顺便 `setdefault` 该 env，供晚 import / 子进程一致） |

**为什么敢用"回落临时"而不是更弱的方案**：先查清爆炸半径 —— `grep` 全仓后确认
**没有任何 `tools/` 脚本使用 `ConfigStore` 或 `AppConfig.save`**（所有 `.save(` 命中都是 `pygame.image.save` / PIL `Image.save`）⇒ 不存在"合法工具会被误伤"的情形（`24_fix_test_config_isolation.md` §7.2 曾把这条列为风险，本轮实测排除）。

## 3. 实现

### 3.1 `app/config_store.py`（`47c97ce7…` → `ee673ab0…` → **`535e86cf…`**）

新增公开 API（都写进 `__all__`）：

| 名称 | 作用 |
|---|---|
| `declare_app(identity='app')` | 应用本体声明；设进程内标记 + `os.environ.setdefault('TSIM_APP_LAUNCH', identity)` |
| `is_app()` | 进程是否已声明为应用 |
| `is_user_config_path(path)` | 是否指向用户 `config.json`（按 basename） |
| `ALLOW_WRITE_ENV = 'TSIM_CONFIG_ALLOW_WRITE'` | 逃生开关名 |
| `APP_LAUNCH_ENV = 'TSIM_APP_LAUNCH'` | 声明标记名 |
| `resolve_config_path(path)` | **按 §2.2 五级优先级**返回实际路径 |

要点：
- `_fallback_for_nonapp()` **惰性**建临时目录（`tempfile.mkdtemp(prefix='tsim_nonapp_cfg_')`），同一进程内所有 `ConfigStore` 共用一个落点；
- warning **每进程一次**（`logger.warning` + `print(..., file=sys.stderr)`，探针未配置 logging 也能看见）；
- 新增 `import sys, tempfile`（原文件只有 json/logging/os/shutil/time）；
- **读路径不变**：`resolve_config_path` 只作用于 `ConfigStore`（读写的 `self.path`）；`AppConfig.load()` 不走它 ⇒ 探针仍能**读到**用户真实设置（只读语义）。

### 3.2 `main.py`（`d8b46d38…`（改前，事后重建）→ `0c933b89…`）

在 `os.chdir(script_dir)` **之后**、任何 `app.*` 子模块 import **之前**插入：

```python
try:
    from app.config_store import declare_app as _declare_app
    _declare_app('main.py')
except Exception as _e:            # 声明失败不能拖垮启动；最坏退化为"按旧行为写"
    print(f'[main] declare_app 失败(配置写保护可能未生效): {_e}', file=sys.stderr)
```

- 位置选在这里的理由：`os.chdir` 已在上一行把 CWD 固定到仓库根（保证 `"config.json"` 语义正确）；`app/__init__.py` 是**空文件**，所以 `from app.config_store import …` 很轻，且早于任何 `app.*` 子模块 ⇒ `TySim.__init__` 造 `ConfigStore` 之前一定已声明。
- 声明是 **module 顶层**语句 ⇒ 无论 `python main.py` 还是被当模块 import，都会执行。
- 声明失败只打印、不抛 ⇒ 最坏退化为旧行为，不会让应用起不来。

### 3.3 `app/config.py`（`a9b10964…` → `8a4e49b8…`）：补完唯一旁路

`AppConfig.save(path)`（`:492`）是**唯一绕过 `ConfigStore` 的写入旁路**，本轮收口到同源判定
（写作用域由 Lead 授权新增 `app/config.py`）：

```python
def save(self, path: str) -> bool:
    """保存配置。返回是否成功(失败仅记日志, 不中断退出/模式切换)。"""
    try:
        from .config_store import resolve_config_path
        path = resolve_config_path(path)     # ← 新增：与 ConfigStore 同源
    except Exception:
        pass
    tmp = path + ".tmp"
    ...                                      # 原实现完全不变
```

- 用**函数内 import**：`config_store` 只依赖标准库（无环），不影响 import 顺序。
- `AppConfig.save` 的唯一生产调用点是 `app/ty_sim.py:458`（`config_store` 缺失时的回退分支）；
  应用本体下 `resolve_config_path` **原样返回** ⇒ 生产行为不变（由验收 G 实测）。

### 3.4 `tests/conftest.py`：**未改**（`ffd2a6da…` 保持）

conftest 的 L0（模块顶层 `TSIM_CONFIG_FILE`）属于 §2.2 的第 1 优先级 ⇒ **override 仍然最高优先**，pytest 行为不变。L3b（会话结束校验用户文件）继续作为独立的安全网。本轮未改动它，验收 C 覆盖了兼容性。

## 4. 验收结果（两端都测；全部实测）

| 判据 | 期望 | 实测 |
|---|---|---|
| **A. 裸探针**（不声明应用、不设任何 env，调 `commit()`） | 用户文件 sha 逐位不变 + 临时文件被写 + 1 条 warning | ✅ **PASS**：`ConfigStore.path = %TEMP%\tsim_nonapp_cfg_jlvdqo4t\config.json`；用户 sha `677e09df…` → `677e09df…`（不变）；临时文件 62 B；**两次构造只 1 条 warning**；两实例共用同一落点 |
| **B. 真实应用路径**（逐字执行 `main.py` 的声明 + 真实 `TySim` 保存链） | **仍能正确持久化到真实 `config.json`** | ✅ **PASS**：`is_app()=True`；`resolve_config_path('config.json')=='config.json'`（**未被重定向**）；`sim.config_store.path` = 真实文件；`sim.save_config(force=True)` 把 `fps_cap 60→61` **真的写进磁盘**（sha `677e09df…`→`2531e227…`，mtime 更新，磁盘 JSON 里是新值） |
| **C. pytest** | `test_config` 5 / `test_sim_settings` 14 / `test_control_panel` 15 / `test_ace_engine` 19 全过 | ✅ **53 passed / 0 failed**；`config.json` sha 跑前=跑后=`677e09df…f97a533cda7` |
| **D. 逃生开关** | 设置后能写真实路径 | ✅ **PASS**：`TSIM_CONFIG_ALLOW_WRITE=1`、未声明应用 ⇒ `path` = 真实文件、`commit()` 真的改了用户文件（探针随后还原） |
| **E.（额外）** | override 优先 / 非 `config.json` 路径不受干预 | ✅ **PASS**：声明应用 + 有 override ⇒ 用 override；未声明应用 + `other_settings.json` ⇒ **原样返回**（不干扰） |
| **F. 裸探针直调 `AppConfig.save()`**（本轮 ① 的 A 端） | 用户文件 sha 逐位不变 | ✅ **PASS**：`resolve_config_path(REAL)` 被重定向到 `%TEMP%\tsim_nonapp_cfg_*\config.json`；`cfg.save(REAL)=True` 但**落点在新值上**（临时文件 `fps_cap=61`）；用户 sha `677e09df…` → `677e09df…` **逐位不变** |
| **G. 真实应用路径直调 `AppConfig.save()`**（本轮 ① 的 B 端） | **仍写真实文件** | ✅ **PASS**：走 `main.py` 的声明 ⇒ `resolve_config_path(REAL)` **未被重定向**；`cfg.save(REAL)` 把 `fps_cap 60→61` 写进真实 `config.json`（sha `677e09df…`→`ddf3740d…`，磁盘值正确） |

### 原始输出摘录（未加工）

```
# ARM A
=== A) 裸探针（不声明应用、不设任何 env）===
   ConfigStore.path         = C:\Users\...\Temp\tsim_nonapp_cfg_jlvdqo4t\config.json
   是否被重定向(≠真实路径)   = True
   两个实例共用同一落点      = True
   warning 次数(两次构造)    = 1（期望 1，每进程一次）
   commit()=True; 用户文件 sha 677e09dffe603ff4 -> 677e09dffe603ff4  不变 = True
   临时文件已写入            = True  62 B
   A 判据 = PASS

# ARM B（逐字执行 main.py 的声明代码后）
   is_app()                 = True
   resolve_config_path("config.json") = config.json   ⇒ 未被重定向 = True
   ConfigStore.path         = G:\气象\Typhoon-sim\config.json   ⇒ 指向真实文件 = True
   sim.config_store.path    = config.json
   sim.save_config(force=True) = True   (fps_cap 60 -> 61)
   用户文件 sha 677e09dffe603ff4 -> 2531e22793d793c6  被写入 = True   mtime 更新 = True
   磁盘上确实是新值         = True
   B 判据 = PASS

# ARM C（四个文件，sha 前后夹住）
跑前 config.json sha256 = 677e09dffe603ff4150532533219905d674cf50e5461f6e4b0ad1f97a533cda7
   test_config 5 | test_ace_engine 19 | test_control_panel 15 | test_sim_settings 14  → 53 passed
跑后 config.json sha256 = 677e09dffe603ff4150532533219905d674cf50e5461f6e4b0ad1f97a533cda7  ← 逐位不变 ✅
```

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe     # 本机 shell 吞 $VAR, 请写全路径

# 四个臂各自独立进程（guard 状态是进程内的）
$P 'G:\tmp\probe_config_guard.py' A     # 裸探针不可写
$P 'G:\tmp\probe_config_guard.py' B     # 真实应用路径仍可持久化（含真实 TySim, 需 ~650MB）
$P 'G:\tmp\probe_config_guard.py' D     # 逃生开关
$P 'G:\tmp\probe_config_guard.py' E     # override 优先 / 非 config.json 不干预

# 回归
$P -m pytest tests/test_config.py        -q --no-header
$P -m pytest tests/test_ace_engine.py    -q --no-header
$P -m pytest tests/test_control_panel.py -q --no-header
$P -m pytest tests/test_sim_settings.py  -q --no-header
```

> ⚠ 本机 shell **不传递 env 前缀**（`env VAR=1 cmd` / `VAR=1 cmd` 都到不了进程，实测 `TSIM_FOO` 为 None）
> ⇒ D 臂用"脚本内 `os.environ[...]` 设值"实现，B 臂用"从 `main.py` 逐字提取声明块再 `exec`"实现。
> 探针输出为 UTF-8，**不要接 `iconv -f GBK`**。

## 6. 观察到的问题 / 风险

**阻塞（仓库完整性，先于本任务的所有收益）**

0. **未跟踪模块 + 已跟踪文件的"半提交"风险**（**已按 Lead 实测修正表述**）。

   **先纠正我上一轮的错话**：我写过"干净克隆会因缺模块而 import 失败"——**不准确**。实测复核（本机复跑，与 Lead 一致）：

   | 复核项 | 结果 |
   |---|---|
   | `git show HEAD:app/settings.py \| grep -c window_util` | **0** |
   | `git show HEAD:app/ty_sim.py \| grep -c config_store` | **0** |
   | HEAD 的 `app/*.py` 数量 | **77** |
   | 工作区 `app/**/*.py` 递归数量 | **84**（多出的 7 个正是未跟踪模块） |
   | 那 7 个模块是否在 HEAD | **全部 NO**（`git cat-file -e HEAD:<path>` 逐个确认） |

   ⇒ **HEAD 是自洽的，干净克隆能正常跑**（只是拿到旧状态）。**问题不在 HEAD，在工作区的"半提交"风险**：

   | 命令 | 后果 |
   |---|---|
   | `git commit -am`（只提交已跟踪） | ❌ **坏**：把带 `window_util` 导入的 `settings.py` 提交了，被导入的模块却没进库 |
   | `git clean -fd` | ❌ **坏**：删掉那 7 个未跟踪模块，而**已修改的已跟踪文件仍在引用它们** |
   | `git add -A && git commit` | ✅ 安全 |

   **严重度必须分开写**（Lead 指出，已逐条核实缩进）：

   | 引用点 | 形态 | 破裂后果 |
   |---|---|---|
   | `app/settings.py:28` `from .window_util import (actual_client_size, keep_window_on_screen)` | **模块级**（第 0 列） | **启动即炸**（`import app.settings` 直接 ImportError） |
   | `app/ty_sim.py:73` / `:178` `from .config_store import …` | **函数内**（8 空格缩进） | 只有走到该代码路径才炸（`TySim.__init__` 会用，实际等于启动即用） |

   **与本任务交付物的关系依然成立且重要**：轮五的 `tests/conftest.py` 隔离、本轮的 `app/config_store.py` 写保护、`app/config.py` 的同源改动**都住在未跟踪文件里** ⇒ `git clean -fd` / 换机 / 部分提交都会让写保护**整个消失**。
   **建议**：`git add -f app/config_store.py app/name_pos_dialog.py app/sim_mode_dialog.py app/sim_time_dialog.py app/sim_value_dialog.py app/ty_sim_mixins/sim_step_control.py app/window_util.py tests/conftest.py`，并只用 `git add -A && git commit`。

**严重**

1. **`AppConfig.save(path)` 直调**（`app/config.py:492`）曾是唯一写入旁路 —— **本轮已修复**（§3.3，验收 F/G 两端通过）。保留此条仅作记录：它证明"唯一落盘入口"这类断言需要定期用 `grep` 复核（我上轮是靠 grep 才发现这条旁路）。

**一般**

2. **本轮第一版实现引入过一个回归，被验收 C 抓到并修掉** → 见 §6.1。
3. **三次事故的写入链都是 `ConfigStore.commit()`** ⇒ 把判定放在 `resolve_config_path()`（`ConfigStore.__init__` 调用）能一次覆盖全部已知路径；这也是"唯一落盘入口"设计带来的红利。
4. **`main.py` 的改前 sha256 是事后重建的**：`_backup/state_v3/` 未含 `main.py`，我开工时也没先记。重建方式 = 现有文件去掉我插入的那一块（差异已 `diff` 确认**只有**该 13 行块，288 → 301 行，重建版 `ast.parse` 通过）⇒ `d8b46d38…`。**这是我的流程疏漏**，如实记录；回退方式：删除 `main.py` 中 `os.chdir(script_dir)` 之后那段 `# ── 声明"我是应用本体"` 注释块。

**建议**

5. **把 A 判据纳入常规流程**：一个 5 行的 `tools/check_config_untouched.py`（跑前后比 sha）即可防止"第四次"。轮五的 L3b 已覆盖 pytest 会话内，但裸探针需要外部校验。

### 6.1 本轮第一版实现引入的回归（已修复）——"两端验收"救回来的东西

**现象**：加上 `app/config.py` 的旁路收口后跑验收 C，**两个往返用例红了**：

```
tests/test_config.py::TestAppConfig::test_save_and_load_roundtrip            → 1 failed, 4 passed
tests/test_sim_settings.py::test_new_sim_config_fields_roundtrip             → 1 failed, 13 passed
```

**根因**（我第一版的判定顺序错了）：`resolve_config_path` 把 **override 放在最前面且无条件生效**，
于是 conftest 的 `TSIM_CONFIG_FILE` 会把**任何** `config.json` 目标劫持到 conftest 的临时文件。
那两个用例给自己造的是**临时落盘文件**并立刻读回：

- `test_config.py:43-48` → `tempfile.NamedTemporaryFile(suffix=".json")`（文件名如 `tmpXXXX.json`）
- `test_sim_settings.py:228` → `tmp_path / 'cfg.json'`

它们的落盘被改道，`load(path)` 自然读到空 → **往返断言失败**。

**修法（两处，一次改完立刻重验全部臂）**：
1. `is_user_config_path()` 收紧为"**仓库根那一份** `config.json`"，或**裸文件名** `config.json`（= CWD 下的应用配置），**不再**用"basename 相同"兜底 —— 测试自己造的绝对路径临时文件因此不再被算作"用户配置"；
2. `resolve_config_path()` 把 override 判定**移到用户配置门之后** ⇒ 非用户配置路径对它是**恒等函数**（连 override 都不套用）。
   这是更好的语义：override 的本意是"把**用户配置**的写重定向掉"，不是"劫持所有 json"。

**回归修复后的复验**（全部重跑）：A/B/D/E/F/G 六臂全 PASS；两个往返用例转绿；`test_config` 5 / `test_ace_engine` 19 / `test_control_panel` 15 / `test_sim_settings` 14 = **53 passed**；`config.json` sha 跑前=跑后。

> 教训：`tests/test_sim_settings.py:17` 自己就写着"**不写 config.json（只在 tmp_path 上做往返）**" —— 如果我只测 A（裸探针）就交，这条**专门用来防污染用户配置的测试**反而会被我的保护逻辑弄红。**"两端验收"不只是防"应用写不进去"，也防"保护逻辑误伤别人"。**

## 7. 值得保留的现状 / unresolved

### 7.1 值得保留的现状

1. **`ConfigStore` 是唯一落盘入口**（`app/config_store.py:91`，带备份 + 读回校验 + 原子替换）—— 正因为唯一，本轮的"入口处判定"才能一次收口。
2. **轮五四层 pytest 隔离仍然有效且不需改动**：`TSIM_CONFIG_FILE` 是最高的第 1 优先级，conftest 的 L0 在 import 前就设好 ⇒ 兼容性由验收 C 实测保证。
3. **落点收窄到 `basename == 'config.json'`**：只保护用户配置这一个目标，最大限度避免误伤（验收 E 实测：别的路径完全不干预）。
4. **读/写分离**：探针仍能读到用户真实设置（`AppConfig.load` 不经 `resolve_config_path`），只是写不进去即 —— 对"只读探针"完全透明。
5. **声明失败可降级**：`main.py` 的 `try/except` 保证不会因为声明问题让应用起不来（最坏回到旧行为）。

### 7.2 unresolved

1. ~~**`AppConfig.save()` 直调**未覆盖~~ → **本轮已修复**（§3.3，验收 F/G 两端通过）。**当前已无已知写入旁路**；但"是否还有别的旁路"只能靠 `grep` 复核（本轮就是这么找到这一条的），建议下次改动前重跑一遍 `grep -rn "\.save(\|ConfigStore(" app/ tools/`。
2. **未在真实窗口里跑一次"改设置→点确定→重启后仍在"**：验收 B/G 用的是 headless（`SDL_VIDEODRIVER=dummy`、800×600）+ **真实 `TySim` + 真实 `save_config(force=True)` / `AppConfig.save`**，且逐字执行了 `main.py` 的声明代码；但**没有**走完整的 `main.py` 事件循环（无法在此环境开真窗口）。"用户在 GUI 里点确定"这条链的最后一跳（`Settings._on_ok`）在轮五已被单独验证过，两者叠加可覆盖，但严格说不是同一次端到端。
3. **子进程/`nuitka`/打包运行未测**：若将来把应用打包，`main.py` 的声明仍然生效（同一进程），但若引入"启动器进程 → 应用进程"的两段式启动，需要在启动器里也声明一次。
4. **`TSIM_APP_LAUNCH` 被外部环境预设时会绕过保护**（`is_app()` 认可该 env）。这是设计上的逃生口之一（另一种形式的逃生开关），但在被污染的环境里会削弱保护；未做进一步收敛。
5. **`is_user_config_path` 对"仓库根以外的 `config.json`"不再保护**（收紧的代价）：绝对路径指向别处的 `config.json` 现在是**恒等函数**。这是为修 §6.1 回归必须付出的取舍 —— 换来的是"测试/工具自己的临时 json 不被误伤"。裸文件名 `config.json` 与仓库根那一份仍在保护内（覆盖全部已知事故形态）。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 路径判定优先级与全部开关 | `app/config_store.py` 模块头注释 + `resolve_config_path()` |
| 应用声明 API | `app/config_store.py` 的 `declare_app()` / `is_app()` |
| 用户配置识别 | `app/config_store.py` 的 `is_user_config_path()`（**仓库根那一份** config.json，或裸文件名 config.json） |
| 非应用回落的临时落点 + warning | `app/config_store.py` 的 `_fallback_for_nonapp()` |
| `main.py` 的声明块 | `main.py`：`os.chdir(script_dir)` 之后、`def _apply_dpi()` 之前 |
| 唯一落盘入口 | `app/config_store.py` 的 `ConfigStore.commit()` |
| 旁路收口（本轮 ① 修复） | `app/config.py` 的 `AppConfig.save()` 开头 2 行 |
| 事故链终点 | `app/ty_sim.py:441-464`（`save_config`）→ `app/settings.py:1330-1334`（`_on_ok`） |
| 轮五的 pytest 四层隔离 | `tests/conftest.py`（L0 顶层 env / L1 override / L2 模块全局 / L3a 包装 + L3b 校验） |
| §6.1 那个回归的两个"受害者"用例 | `tests/test_config.py::test_save_and_load_roundtrip`、`tests/test_sim_settings.py::test_new_sim_config_fields_roundtrip` |
| 改前基线 | `output/exploration/_backup/state_v3/{config_store.py,conftest.py}`、`SHA256_STATE_V3.txt`（**不含 `app/config.py` / `main.py`** —— 这两者改前值见 §3.2/§3.3） |
| 本轮探针 | `G:\tmp\probe_config_guard.py`（A/B/D/E/F/G 六臂） |
