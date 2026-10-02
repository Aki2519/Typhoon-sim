# 27 补掉剩余 5 次绕过 `rt()` 的光栅化

> 第六轮 · `sim-numerics` · **实施任务（小）** · 写作用域：`app/dialog_base.py`、`app/input_field.py`、本报告。
> 上游：`22_fix_text_cache.md`（sim-core 的 `rt()` 字节预算缓存）。回退点：`output/exploration/_backup/state_v2/{dialog_base,input_field}.py`（`SHA256_STATE_V2.txt:16-17`）。
> **实测** = 本机跑出来的数字；**推断** = 从代码读出、无实验支撑。计时只作区间，主判据是确定性计数与逐字节 sha256。

## 一句话定位

把 `app/dialog_base.py:113/120/184` 与 `app/input_field.py:538/575` 这 5 处直连 `SmartFont.render()` 的调用改成走 `rt()`，
**每帧绕过 `rt()` 的光栅化从 1.25 次（`PaintDialog` 稳态）与 5 处逐站点各 1 次 → 全部归零**，
`_rt_cache` 的 `evictions` **仍是 0**（占用 8.8% 预算），8 个抽样文本的 sha256 **8/8 与改前逐字节相同**。
未加 `max_width`（调用点原本无限宽），返回面在本 5 处**只被 blit、没有就地修改** ⇒ 不需要 `.copy()`。

---

## 1. 清单与职责

| 位置（改前 → 改后行号） | 原代码 | 改后 | 字体 |
|---|---|---|---|
| `dialog_base.py:113` → `:116` | `ts = CONTENT_FONT.render(text, True, tc)` | `ts = rt(CONTENT_FONT, text, tc)` | `CONTENT_FONT = f_s`（`:21`） |
| `dialog_base.py:120` → `:124` | `ts = TITLE_FONT.render(text, True, SETTINGS_TEXT_LIGHT)` | `ts = rt(TITLE_FONT, text, SETTINGS_TEXT_LIGHT)` | `TITLE_FONT = f_m`（`:20`） |
| `dialog_base.py:184` → `:189` | `title = title_font.render(title_text, True, title_color)` | `title = rt(title_font, title_text, title_color)` | `title_font` 形参（默认 `TITLE_FONT`，`:177`） |
| `input_field.py:538` → `:539` | `label_surf = self.font.render(self.label, True, tc)` | `label_surf = rt(self.font, self.label, tc)` | `self.font`，构造默认 `f_s`（`:71/:78`） |
| `input_field.py:575` → `:577` | `text_surf = self.font.render(val, True, tc)` | `text_surf = rt(self.font, val, tc)` | 同上 |

配套改动（只这两行）：
- `dialog_base.py:14` 的 `from .constants import (...)` 末尾加 `rt`；
- `input_field.py:8` `from .constants import f_s, TXT, BUTTON_BORDER, rt`。
（`rt` 由 `app/constants/__init__.py:4` 的 `from .fonts import *` 再导出。）

**改动后的 SHA256**

| 文件 | 改前（= `SHA256_STATE_V2.txt:16-17`） | 改后 |
|---|---|---|
| `app/dialog_base.py` | `93d740aa0c639d9cb59643d6f042aee69af8cdd267d049dcc833c17cec5cac8a` | `5ab190e1c4fd24036e37b1f3527bf74fa22997f141ebc747e744175534d2e9ea` |
| `app/input_field.py` | `6e684a18882e2b867da4827b7aec9c2a36139b889f0148c7809d96ea7d8ecc07` | `5403f8891b6ac874ac95e49c80737570636ef90959794a70807d1452c1c26b76` |

回退（1 条命令）：
```bash
cp output/exploration/_backup/state_v2/dialog_base.py app/dialog_base.py
cp output/exploration/_backup/state_v2/input_field.py  app/input_field.py
```

**`grep -n "\.render(" app/dialog_base.py app/input_field.py` 改后 = 零命中**（`f.size(...)` 不在此列，它不光栅化）。
`CONTENT_FONT` / `TITLE_FONT` 这两个名字在全仓**只出现在 `dialog_base.py`**（grep 6 命中全在该文件）⇒ 没有别的模块绕过这两个字体的 `rt()`。

---

## 2. 数据流 / 调用链

```
PaintDialog.draw / 任意 Dialog.draw / InputField.draw
  ├─ Dialog.draw_dark_button  ──► (改前) CONTENT_FONT.render ──► SmartFont.render ──► 128 条 FIFO
  │                              (改后) rt(CONTENT_FONT, ...) ──► fonts._rt_cache(8 MiB, 真 LRU)
  ├─ Dialog.draw_dark_title   ──► 同上（TITLE_FONT）
  ├─ Dialog.draw_title_bar    ──► _title_bar_cache(32 条) miss 时才渲染
  │                              └─ (改后) rt(title_font, ...) ──► _rt_cache
  └─ InputField.draw          ──► label(:538) / value(:575) ──► (改后) rt(self.font, ...)

rt(f, text, color, max_width=None, smooth=True)        app/constants/fonts.py:213
  └─ max_width is None/<=0  ⇒ 本次 5 处走的分支
       key = (id(f), text, color, bool(smooth))   → 命中直接返回（0 光栅化）
       miss → f.render(text, smooth, color) → 钉住 id(f) → _rt_cache.put
```

**为什么这 5 处是"到不了 0"的唯一残余**：`rt()` 的新缓存在 `max_width is None` 分支前置；
这 5 处**不经过 `rt()`**，所以只能吃 `SmartFont` 自带的 **128 条 FIFO**（`fonts.py:92-130`），
而 `f_s` 的工作集是 **139 键 > 128** ⇒ 上一帧的条目被挤掉、下一帧重新光栅化（自噬）。
改后它们与其余 213 处共用同一个 8 MiB 字节预算缓存。

---

## 3. 核心机制详解

### 3.1 参数顺序：不是简单替换

| | 签名 | 说明 |
|---|---|---|
| 直连 | `SmartFont.render(text, antialias, color)` | 第 2 位是 antialias |
| `rt()` | `rt(f, text, color, max_width=None, smooth=True)` | 第 3 位是 color，第 5 位才是 smooth |

⇒ 正确的等价替换是 `f.render(text, True, color)` ⟶ `rt(f, text, color)`
（`smooth=True` 是 `rt()` 的默认值，`max_width=None` 命中"无限宽"分支）。
本 5 处**逐条核对过实参顺序**，没有把 `color` 当 `smooth` 传进去。

### 3.2 语义等价性（逐条）

| 点 | 等价性论证 |
|---|---|
| `:113` | `tc` 由 `SETTINGS_TEXT_LIGHT/TEXT_DIM` 或常量元组给出（`:106-110`），元组 ⇒ `rt()` 可哈希；`text` 只在 `isinstance(text, str)` 为真时渲染（`:112`）|
| `:120` | `SETTINGS_TEXT_LIGHT` 是常量元组；无空串分支，`rt("")` 与 `f.render("", True, c)` 都是 1×h 的空面 |
| `:184` | `title_color` 默认 `TXT`（元组）；调用方若传**不可哈希**对象，`rt()` 的 `hash(key)` 抛 `TypeError` → **退回直渲**（`fonts.py:217-221`），行为与改前一致、不会崩 |
| `:539` / `:577` | `tc` 是 `(220,220,240)` 或 `TXT`（`:533`），元组 |

**没有加 `max_width`**：5 处原本都是无限宽，改后 `max_width=None` ⇒ 仍走"不换行"分支；
`rt()` 里 `max_width>0` 那条换行/裁剪分支**一行未动**，本次也不经过它。

### 3.3 共享表面审计（`rt()` 的返回面现在跨帧长存）

`rt()` 命中时返回**同一个 Surface 对象**（`SmartFont.render` 命中时本来也返回共享对象，本次只是把共享期变长）。
逐点审计这 5 处对返回值的用法：

| 点 | 变量 | 用法 | 判定 |
|---|---|---|---|
| `:116` | `ts` | `ts.get_width()/get_height()` + `surface.blit(ts, ...)`（`:119`） | ✅ 只读 |
| `:124` | `ts` | `surface.blit(ts, ...)`（`:125`） | ✅ 只读 |
| `:189` | `title` | `get_width/get_height` + `bar_surf.blit(title, ...)`（`:190-192`）；被 `.fill()` 的是**新建的 `bar_surf`**（`:186`），不是返回值 | ✅ 只读 |
| `:539` | `label_surf` | `surface.blit(label_surf, ...)`（`:540`） | ✅ 只读 |
| `:577` | `text_surf` | `surface.blit(text_surf, ...)`（`:578`） | ✅ 只读 |

⇒ **5 处都不需要 `.copy()`**（与 sim-core 审计过的另 4 处候选一致：那 4 处是先 copy 再 `set_alpha`）。

### 3.4 缓存预算会不会被挤爆（派活单的"允许的出口"条件）

实测：加这 5 处后 `_rt_cache` 从 **144 条 / 710,532 B** 变成 **152 条 / 735,324 B**，
即 **8.8 % of 8 MiB**，`evictions` **仍为 0** ⇒ **没有触发"退回不改"的条件**。

---

## 4. 输入输出资产

| 路径 | 角色 |
|---|---|
| `app/dialog_base.py` | 被改（sha256 见 §1） |
| `app/input_field.py` | 被改 |
| `app/constants/fonts.py` | 未改（上游缓存；`rt()` 在 `:213`） |
| `app/cache_store.py` | 未改（`SurfaceCache`） |
| `output/exploration/_backup/state_v2/dialog_base.py`、`input_field.py` | 回退点 |
| `output/exploration/_backup/SHA256_STATE_V2.txt` | 改前哈希基线（`:16-17`） |
| `G:\tmp\harness27.py` | 验收探针（工作区外）：`PaintDialog` 稳态计数 + 按来源拆分 + 8 抽样 sha256 |
| `G:\tmp\sites27.py` | 验收探针（工作区外）：**5 个调用点逐站点**判定走没走 `rt()` |
| `G:\tmp\r27_before.json` / `r27_after.json`、`sites_before.json` / `sites_after.json` | 原始产物（落盘，可复核） |

---

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# 改前（已完成，产物 r27_before.json / sites_before.json）
timeout 300 "$P" 'G:\tmp\harness27.py' before
timeout 200 "$P" 'G:\tmp\sites27.py'   before
# 改后
timeout 300 "$P" 'G:\tmp\harness27.py' after
timeout 200 "$P" 'G:\tmp\sites27.py'   after

# 回归（单文件 timeout 300，禁全量）
timeout 300 "$P" -m pytest tests/test_paint.py           -q --no-header   # 15 passed in 41.75s
timeout 300 "$P" -m pytest tests/test_panel_layer_row.py -q --no-header   # 5 passed
timeout 300 "$P" -m pytest tests/test_ace_engine.py      -q --no-header   # 19 passed
timeout 300 "$P" -m pytest tests/test_control_panel.py   -q --no-header   # 15 passed
```

### 5.1 验收 ① 逐站点：5 处各自是否走 `rt()`（**本轮的判决性证据**）

方法：每次先清空 `SmartFont._cache` 与 `_rt_cache`（冷），调一次目标方法，
看这唯一的一次光栅化被记成 `via_rt`（经 `rt()`）还是 `bypass`（绕过）：

| 调用点 | 改前 miss / via_rt / **bypass** | 改后 miss / via_rt / **bypass** |
|---|---|---|
| `dialog_base.py:113` `draw_dark_button` | 1 / 0 / **1**（记为 `dialog_base.py:113`） | 1 / 1 / **0** |
| `dialog_base.py:120` `draw_dark_title` | 1 / 0 / **1**（`:120`） | 1 / 1 / **0** |
| `dialog_base.py:184` `draw_title_bar` | 1 / 0 / **1**（`:184`） | 1 / 1 / **0** |
| `input_field.py:538` label | 1 / 0 / **1**（`:538`） | 1 / 1 / **0** |
| `input_field.py:575` value | 1 / 0 / **1**（`:575`） | 1 / 1 / **0** |

**5/5 从"绕过"变成"经 rt()"**，且 `miss` 恒为 1（仍是同一次光栅化，只是现在被缓存接管）。

### 5.2 验收 ② `PaintDialog` 稳态（4 帧均值，确定性计数）

| 指标 | 改前 | 改后 | 判据 |
|---|---|---|---|
| 每帧 `rt()` 调用 | 169 | 175（+6 = 新增 5 个调用点 + 1 处文本变化） | — |
| 每帧真实光栅化（无预览） | **1.25** | **0.00** | ≈0 ✅ |
| 其中**绕过 `rt()`**（无预览） | **1.25** | **0.00** | **必须 0** ✅ |
| 其中经 `rt()`（无预览） | 0.00 | 0.00 | — |
| 每帧真实光栅化（有预览） | 0.25 | 0.25（**全部经 `rt()`**，无绕过） | ✅ |
| `input_field` 单跑：绕过 `rt()`/帧 | 0.50 | **0.00** | ✅ |
| `_rt_cache` hits / misses | 1379 / 144 | 1434 / 152 | — |
| **`_rt_cache` evictions** | **0** | **0** | **不许升高** ✅ |
| `_rt_cache` items / bytes | 144 / 710,532 B（8.5 %） | 152 / **735,324 B（8.8 %）** | 预算 8 MiB ✅ |

> 改前 `PaintDialog` 稳态只有 `input_field.py:575` 以 1.25 次/帧的身份出现 —— `dialog_base` 那三处
> 在本场景里被 `SmartFont` 的 128 条 FIFO 兜住了（命中则不计 miss）；它们是否暴露为"绕过"取决于
> 同帧其它文本对 FIFO 的挤占。**逐站点证据（§5.1）才是这 5 处的判决性证据**，稳态计数是它的补充。

### 5.3 验收 ③ 逐字节视觉一致（8 抽样）

`sha256(pygame.image.tobytes(surf,'RGBA'))`，改前 vs 改后：

| 文本 \| color | 改前 = 改后 |
|---|---|
| `绘画模式` \| (25,32,48) | `135d4779…1492` ✅ |
| `ACE / 路径统计` \| (196,204,218) | `5885dac0…1466` ✅ |
| `▾` \| (25,32,48) | `11063c1c…0aea68` ✅ |
| `12` \| (196,204,218) | `bd06efe8…a06a5ac` ✅ |
| `wp012025.dat` \| (25,32,48) | `0970cfb1…c0e993` ✅ |
| `已加入 14 个文件` \| (240,205,110) | `04c768a0…8528c9` ✅ |
| `""`（空串）\| (25,32,48) | `e3b0c442…b2b855` ✅ |
| `放大 ×1.0 · 滚轮缩放` \| (245,245,250) | `c15f24f7…8ee8` ✅ |

**8/8 逐字节相同**；且这 8 个哈希与 `22_fix_text_cache.md` §5.4 **逐个吻合** ⇒ 我的探针复现了上一轮的基线口径。

### 5.4 回归

| 测试 | 基线 | 改后 | 结果 |
|---|---|---|---|
| `tests/test_paint.py` | 15 passed / 49.0 s | **15 passed in 41.75s** | ✅ |
| `tests/test_panel_layer_row.py` | 5 passed | **5 passed in 6.81s** | ✅ |
| `tests/test_ace_engine.py` | 19 passed | **19 passed in 0.30s** | ✅ |
| `tests/test_control_panel.py` | 15 passed | **15 passed in 0.32s** | ✅ |

未跑全量 pytest（铁律 6）。改后两文件 `ast.parse` 通过。

---

## 6. 观察到的问题 / 风险 / 不一致

### MINOR-1｜`draw_title_bar` 的渲染被**两层缓存**包住（不冲突，但值得知道）
`draw_title_bar` 自己有 `_title_bar_cache`（32 条，键 `(rect.width, title_text, title_color, title_font)`，`dialog_base.py:173`、`:184-195`），
`rt()` 现在在它的 **miss 分支内部**。⇒ 命中的语义没有变化（返回值仍是那张 `bar_surf`），
只是"标题栏缓存 miss"不再必然等于"一次光栅化"。**没有叠加冲突**（两者键空间不同、职责不同：
一个是整条标题栏合成面，一个是裸文本面）。

### MINOR-2｜`rt()` 的 `id(f)` 键依赖 `_rt_font_refs` 钉住字体
这 5 处的字体是 `f_s` / `f_m`（模块级常驻）或构造默认值（`InputField(font=f_s)`，共 17 个调用点**全部**用默认或显式 `f_s`，已 grep 核对）
⇒ 钉住不会引入新的保留（字体本来就是进程级常驻）。`draw_title_bar` 的 `title_font` 是形参，若将来有人传**每帧新建**的 SmartFont，
`_rt_font_refs` 会随之增长 —— 属上游 `rt()` 的已知边界（`22_fix_text_cache.md` §7 U3），本次不改。

### MINOR-3｜`input_field.py` 仍有一处**非文本**的每帧新建面
`:567-572` 的选区高亮面按 `(val, start, end, w, h)` 缓存在 `_sel_hl_cache`（32 条 FIFO）。
它与本次的文本缓存无关（不是字形光栅化），但也属"条数闸门"家族，**未在本轮写域内**，仅记录。

### 一致性观察
1. 派活单给的行号（`113/120/184/538/575`）是**改前**行号；改后因加了注释与 import，变为
   `116/124/189/539/577`。本报告两套都给，便于 `git diff` 对照。
2. 探针 `sites27.py` 为了做归属统计会给模块**打补丁**（`DB.rt = rt_wrap`），
   所以它打印的 `dialog_base_has_rt` 恒为 True —— **不要把它当"源码里已经有 rt"的证据**；
   真正的证据是逐站点的 `bypass 1 → 0`。
3. 本轮没有发现需要"退回不改"的情形：语义等价（§3.2）、无就地突变（§3.3）、预算未被挤爆（§3.4）、逐字节一致（§5.3）。

### 值得保留的现状（别回退）
- `rt()` 的**字节预算 + 真 LRU**（`fonts.py`）与 `RT_TEXT_CACHE_BYTES=0` 运行时开关：本轮所有收益都靠它；
- `SmartFont` 自带的 128 条 FIFO **保留不动**（它是 `max_width` 分支与未迁移调用方的兜底，删了会退化）；
- `Dialog.draw_title_bar` 的 `_title_bar_cache`：整条标题栏合成面只建一次，比逐帧重绘便宜，**不要因为有了 `rt()` 就删掉它**。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U-1 | **ACE 图表对话框**改后是否也归零 | 上一轮就卡在 `ACEChartDialog.activate()` → `calculate_season_stats` 需要完整 `TySim`/`map_mgr`（222 MB 图），本轮我按"小活 + 内存纪律"**没有**重跑该分支（探针里已去掉 ace 段）。需要一个能构造完整 `TySim` 的环境，或解耦 `sim.map_mgr` |
| U-2 | 其余对话框（`settings`/`ty_list`/`point_edit`…）里是否还有**别的**绕过 `rt()` 的光栅化 | 我只验证了本任务圈定的 5 处 + `PaintDialog`/`InputField` 两条路径。归一化的做法是在 `SmartFont.render` 外面加一个**全仓计数器**（探针里已有 `bypass_sites` 归属），把每个用到的对话框都 `draw()` 若干帧，读出完整的"绕过站点"清单 —— 本轮按"小活"范围没做 |
| U-3 | `rt()` 新缓存的**长期**驱逐行为（长会话/多对话框切换） | 实测本轮 152 条 / 8.8 %；要给出"多久会驱逐"需要一次多小时的多对话框扫描。`SurfaceCache` 有 8 MiB 硬上界，所以最坏情况可控 |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 本次 5 处改动 | `app/dialog_base.py:116`、`:124`、`:189`；`app/input_field.py:539`、`:577` |
| 两处 import 改动 | `app/dialog_base.py:14`（加 `rt`）、`app/input_field.py:8`（加 `rt`） |
| 上游缓存实现 | `app/constants/fonts.py:213-230`（`rt()` 默认分支）、`:190-210`（`RT_TEXT_CACHE_BYTES`/`_rt_cache`/`_rt_font_refs`/`rt_cache_stats`/`rt_cache_clear`） |
| 自噬的根因（128 条 FIFO） | `app/constants/fonts.py:92-130`（`SmartFont.maxsize=128`） |
| 标题栏自带缓存 | `app/dialog_base.py:173-195`（`_title_bar_cache`，32 条） |
| 输入框选区面（同类但未改） | `app/input_field.py:566-573`（`_sel_hl_cache`，32 条） |
| 改前/改后哈希 | `output/exploration/_backup/SHA256_STATE_V2.txt:16-17` vs 本报告 §1 |
| 回退 | `cp output/exploration/_backup/state_v2/dialog_base.py app/dialog_base.py`（`input_field.py` 同理） |
| 验收探针与产物 | `G:\tmp\harness27.py`、`G:\tmp\sites27.py`、`G:\tmp\r27_{before,after}.json`、`G:\tmp\sites_{before,after}.json` |
