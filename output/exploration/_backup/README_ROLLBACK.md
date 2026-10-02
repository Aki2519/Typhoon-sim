# 性能优化改动 —— 安全网与回退

> 建立于 2026-10-02，在**任何源码改动之前**。**最后更新：2026-10-02 第六轮开始。**
> 背景：本仓库 HEAD 停在 2026-09-15（16.9 天未提交）、无 stash、`docs/`+`output/` 未跟踪。
> **没有回退点就不该开始改源码。**

---

## 🟢 当前权威基线 = `_backup/state_v2/`（第六轮及以后请用这个）

前五个工作流（W1/W2/W3/W4/W5b）的改动**全部落地并通过独立验证**之后，已把**当时的工作区状态**整体快照为：

| 内容 | 位置 |
|---|---|
| 13 个已改动文件的当前内容 | `_backup/state_v2/*.py` |
| 4 个第六轮新增目标文件 | 同上（`tests/conftest.py`、`app/config_store.py`、`app/dialog_base.py`、`app/input_field.py`） |
| 权威哈希清单（**17 条**） | `_backup/SHA256_STATE_V2.txt` |

**回退到"第五轮结束时"的完整状态**：按下面这张对照表**逐个 `cp`**（本机 bash 的通配与 `$VAR` 不可靠，别写循环）。

| `state_v2/` 里的文件 | 目标路径 |
|---|---|
| `ty_sim.py` | `app/ty_sim.py` |
| `main.py` | `main.py` |
| `sim_mode_mixin.py` | `app/ty_sim_mixins/sim_mode_mixin.py` |
| `playback_ctrl.py` | `app/playback_ctrl.py` |
| `summary_effect.py` | `app/summary_effect.py` |
| `heatmap.py` | `app/statistics/heatmap.py` |
| `name_pos_dialog.py` | `app/name_pos_dialog.py` |
| `_draw_icon_mixin.py` | `app/ty_sim_mixins/_draw_icon_mixin.py` |
| `settings.py` | `app/settings.py` |
| `summary_list.py` | `app/statistics/summary_list.py` |
| `smcy_icon.py` | `app/smcy_icon.py` |
| `map_mgr.py` | `app/map_mgr.py` |
| `fonts.py` | `app/constants/fonts.py` |
| `conftest.py` | `tests/conftest.py` |
| `config_store.py` | `app/config_store.py` |
| `dialog_base.py` | `app/dialog_base.py` |
| `input_field.py` | `app/input_field.py` |

**校验**：`sha256sum -c output/exploration/_backup/SHA256_STATE_V2.txt`（**注意该清单里的路径是原始相对路径**，在仓库根执行）

> ⚠ **`SHA256_BEFORE.txt` 与 `_backup/*.py`（无 `state_v2/` 前缀）是"本轮改动之前"的状态** —— 只在"整体回到最原始"时用，
> **不要**用它们回退第六轮的改动（否则会连带撤掉前五轮所有已验收的优化）。

---

## ⚠ 四条已被实践证伪的早期假设（务必先读）

1. **"备份一次就够"是错的。** `_backup/map_mgr.py` 是 **alpha 优化之前**的版本；#2 变体 A 之后再用它回退，会**连带撤掉已验收的 alpha 优化**。
   ⇒ **每次新改动落地后，回退点要更新。** 这就是 `state_v2/` `state_v3/` `state_v4/` `state_v5/` 逐份存在的原因。
2. **"只记哈希就够了"是错的。** 光有 `sha256` **取不回内容** —— `app-feat` 在 P7 只记了 `41177e4e…` 的哈希，事后既无 `_backup/` 副本、git（HEAD 更早）、`__pycache__`（已被后续 import 覆盖）也无从取回 ⇒ **做不到字节级 before/after diff**，只能用 A/B 变体间接证明。（`main.py` 那次同类问题还导致哈希是"事后重建"的。）
   ⇒ **落地前必须复制内容**，不能只登记哈希。见下方"落地三步"。
3. **"`git stash create` 能兜住一切"是错的。** 它**不含未跟踪文件**；直接 archive 出来的树 `import app.ty_sim` 就会炸（sim-run 实测）。
   ⇒ 完整 BEFORE 树以 **`G:\tmp\perf_before\`** 为准（457 个已跟踪文件 + 按 mtime 过滤补齐的未跟踪资产）。
4. **"测试不会碰用户文件"是错的。** 测试**三次**改写用户的 `config.json`（`hemisphere: north → south`）。`tests/conftest.py` 早就警告过。
   ⇒ 第六轮 task-21 做了 pytest 侧四层隔离；第七/八轮 task-27 做了**根因修复**（应用自证身份，非应用进程回落临时路径）。

---

## 落地三步（每次改源码前照做）

```bash
# ① 复制内容（不只是哈希！）
cp <目标文件> output/exploration/_backup/state_vN/<文件名>
# ② 登记哈希
sha256sum <目标文件> >> output/exploration/_backup/SHA256_STATE_VN.txt
# ③ 改完再登记一次，两次都进报告
```
⚠ **谁都不许只写"改前 sha256 = xxx"就动手** —— 那条哈希在需要回退时是废纸。

---

## 回退方式（三选一，从快到全）

### 1) 单文件回退（最常用）—— **下表是权威回退点**

| 目标文件 | 回退点（`_backup/` 下） | 说明 |
|---|---|---|
| `app/ty_sim.py` | `ty_sim.py` | W1 |
| `main.py` | `main.py` | W1 |
| `app/ty_sim_mixins/sim_mode_mixin.py` | `sim_mode_mixin.py` | W1 |
| `app/playback_ctrl.py` | `playback_ctrl.py` | W1 |
| `app/summary_effect.py` | `summary_effect.py` | W1 |
| `app/statistics/heatmap.py` | `heatmap.py` | W1 |
| `app/name_pos_dialog.py` | `name_pos_dialog.py` | W1 |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | `_draw_icon_mixin.py` | W2 |
| `app/settings.py` | `settings.py` | W2 |
| `app/statistics/summary_list.py` | **无独立备份** → 用下方第 2 条从悬空提交取 | W2 |
| `app/smcy_icon.py` | `smcy_icon.py` | W3（8a/8c） |
| `simulator/simcore/render.py` | `render.py` | **未改**（sha256 与改前逐位相同） |
| `app/map_mgr.py` | **`map_mgr_v2.py`** ⚠ | **不要用 `map_mgr.py`**（那是 alpha 之前的） |
| `app/constants/fonts.py` | `fonts.py` | W-`rt()` |

```bash
cd /mnt/g/气象/Typhoon-sim
cp output/exploration/_backup/map_mgr_v2.py app/map_mgr.py     # ⚠ 注意用 v2
# 其余同名 cp 即可
```

**全部回退**（除 `map_mgr.py` / `summary_list.py`）：
```bash
cp output/exploration/_backup/ty_sim.py app/ty_sim.py
cp output/exploration/_backup/main.py main.py
cp output/exploration/_backup/sim_mode_mixin.py app/ty_sim_mixins/sim_mode_mixin.py
cp output/exploration/_backup/playback_ctrl.py app/playback_ctrl.py
cp output/exploration/_backup/summary_effect.py app/summary_effect.py
cp output/exploration/_backup/heatmap.py app/statistics/heatmap.py
cp output/exploration/_backup/name_pos_dialog.py app/name_pos_dialog.py
cp output/exploration/_backup/_draw_icon_mixin.py app/ty_sim_mixins/_draw_icon_mixin.py
cp output/exploration/_backup/settings.py app/settings.py
cp output/exploration/_backup/smcy_icon.py app/smcy_icon.py
cp output/exploration/_backup/fonts.py app/constants/fonts.py
cp output/exploration/_backup/map_mgr_v2.py app/map_mgr.py
```

### 2) git 悬空提交回退（覆盖全部 309 个已跟踪文件的改动）
```bash
git checkout 9c3f657eef517f35e4517271a5b6b6471e1ae13e -- <文件或 .>
```
由 `git stash create` 生成，**没有改动工作树、索引或任何 ref**。
⚠ **不含未跟踪文件**（如 `app/statistics/summary_list.py` 若从未被跟踪就用不了）。

### 3) 校验文件是否被改过
```bash
sha256sum -c output/exploration/_backup/SHA256_BEFORE.txt
```

### 4) 完整 BEFORE 树（最权威）
`G:\tmp\perf_before\`（sim-run 冻结，含位级对账）。可用于任何"改前 vs 改后"对比。

---

## 改动前的 SHA256（`SHA256_BEFORE.txt` 为权威）

```
1ff0c4a3268fc4196dfb5d0ea5b9ed0cc4d4e721e1741d6da3373cc5cd66c3f7  app/ty_sim.py
2c6eb7e9563e5ed67d8db02130ec280f163b209e55d0a816180da4992b0211a2  main.py
74defaeeffd3b3bfee3851f2c1a3f537e99d1860e19b285644e2b742040b7a2b  app/ty_sim_mixins/_draw_icon_mixin.py
a146f87660ab916b402650abfdab093873005167047455e8e85bc7680ea05830  app/settings.py
a32bbdc436cb74fde5a79ed2ac1b992ef5279d8cc81017a1ccd51548a2f413d3  app/constants/fonts.py
8a6f27bf4c42f6ce67df82e3c78b04e97dd676d664d799cf4d0486ed6d7dfc87  simulator/simcore/render.py
3e635fd0d99aa2859e1e36eb029b2feeea3dc6d3abb6ab3843e0864395e039f3  app/smcy_icon.py
c13f5284538832d7d96a75c7e46a40dfd0eab30273e3aa1944e7c8d4cdd46515  app/map_mgr.py   ← alpha 之前
045e1ed2c8bf329fc18e3508da70c9d72aa5bbb090db3889924aa38f53092f81  app/ty_sim_mixins/sim_mode_mixin.py
7c06e338f7e5c6a1dc2b3e86e6f8909525f2add215580fa1a3a532f6dc3914c2  app/playback_ctrl.py
dba976686b00fd28640c196a4b9f4090c50434bd36b6d2bddcd4026562522ec0  app/summary_effect.py
e7d281c686e051680c78b8bbe9c3be0261800ac6a61ec7b630adb5c2432f1b96  app/statistics/heatmap.py
0ea67835d17774e7c59c98da1b232ddbeba924b9dabb1cc6f7d0ebe4cc928037  app/name_pos_dialog.py
```

**中间回退点**：`map_mgr_v2.py` = `05df5172bcd8cad3cd8ce6e5d1d1981f23163b2be9dce778c51657bae5b4f967`（alpha 之后、变体 A 之前）

**git 状态基线**：`git status --porcelain` = **535 行**（` D` 29 / ` M` 280 / `??` 226）。

---

## 环境备选回退（无需 bash）

本轮宿主 `bash` 服务反复报 `Bash/Service/E_UNEXPECTED`（**5 人次遇到**，连 `echo` 都失败）。
若 bash 不可用，`read` + `write` 工具通道**不受影响**，可用它读 `_backup/` 内容再写回目标文件。
`edit` / `grep` / `glob` 同样不受影响。

---

## 实施纪律（本轮实际执行过的）

1. **一次只改一个变量** —— 每个修复单独验证，不合并。
2. **改前/改后各记 `sha256sum`**。
3. **每个修复可独立回退**（配置开关 + 文件备份双保险）。
4. **改后跑回归**（单文件 `timeout 300`，禁止全量）。
5. **主判据用确定性指标**（调用次数 / 缓存 hits-misses-evictions / 字节数 / RSS / 像素 sha），计时只作区间 —— 本机计时不可信（P 核/E 核差 1.90×，同代码墙钟极差 325%）。
6. **每个修复必须可独立验收** —— 说不清收益的要如实写"收益为 0"（W1-① 就是这样）。
7. **"有证据地不改"是合格交付** —— 6a/6b、8b、`render.py` 都据此未改。
