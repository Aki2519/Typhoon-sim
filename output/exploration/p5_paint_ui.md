# P5 绘画界面优化 + 投影开关接线（app/paint_dialog.py / app/paint_file_browser.py）

## 一句话定位

绘画模式的**界面层**：`paint_dialog.py`（969 行）负责参数面板 / 文件列表 / 预览 / 统计的绘制与事件路由，`paint_file_browser.py`（374 行）负责 Ty4 风格的「打开文件」浏览器。本轮按 Lead 的 `task-32` 做**可用性走查 + 分类修复**，并把「投影」开关接到 P3/P4 约定的 `cfg["projection"]` 上。

> **实测口径**：全部在 headless（`SDL_VIDEODRIVER=dummy`，2560×1540）完成，未开真窗口、未跑 `main.py`。
> **改前 sha256**：`paint_dialog.py = be42025e861822cc2ca5ae1b7272d06a6703a3588aeb762c4a81337c64feee6c`、`paint_file_browser.py = 05431663c8806399307b2b6bd5cd54fe6265777f5e0537f9b1bf1be495fd9ee4`（与 `_backup/state_v4/` 基线一致）。
> **执行纪律**：按「一类问题一个 pass，先落盘报告再继续」推进，每个 pass 单独记 sha 与验收数字。

---

## 1. 与 P3 / P4 的接口约定（已对齐，实测确认）

| 项 | 结论 | 来源 |
|---|---|---|
| 投影透传字段名 | **`cfg["projection"]`**，值 `"equirect"` / `"conic"`；缺键 / `None` / 非法值 ⇒ `"equirect"` | app-feat 2026-10-02 回信①（我提议、他确认；我这边**只写键、不读**，故不阻塞） |
| P4 交付状态 | `app/paint_projection.py` 与 `app/paint_render_qq.py` **都还不存在**（实测 `ls`） | 本轮实测 |
| P3 的回落 | 模块不存在时回落自带等距圆柱 + 每进程一条 warning ⇒ 开关现在接上即"值被读到、conic 暂等于 equirect" | app-feat 回信① |
| `path_width` / `point_size` | **接上不删**：`coord_unit=="px"` 时线宽=`path_width`、点半径=`point_size/2`；`"deg"`（默认）走 `track_deg`/`point_deg` | app-feat 回信②（TY4_SPEC §7.1） |
| `multireset` | **删**（Ty4 的 `MultipleFile` 只实现 `==2`，`1` 无代码；本渲染每台风独立画路径，语义为空） | app-feat 回信③（§11.2） |
| `wiki` / `2023` 色阶 | **保留两选项 + 如实注解**；"两套相同"是对 Ty4 的忠实复刻（`t4_track.py:59-60 PALETTE_CLASSIC = dict(PALETTE_2023)`），**不得编造 RGB、不得合并** | Lead 2026-10-02 补信 + 回信④（§4.2/§4.3） |

---

## 2. 可用性走查表（Pass 0，**纯读不改**）

### 2.1 第一轮已列出的 4 条（复核结论）

| # | 问题 | 证据（`文件:行`） | 现象 | 修法 | 状态 |
|---|---|---|---|---|---|
| A1 | 「多文件重置」是死控件 | UI `paint_dialog.py:262-263`（3 选项）→ 透传 `:649` → 渲染器 `paint_render.py:177`（**只有一行赋值**，注释自承"保留(兼容)"，全文再无引用） | 改下拉什么都不发生 | P3 定案"删" ⇒ **UI 去掉这一行** | ✅ 已改（Pass 3：整行让给「投影」，布局零位移） |
| A2 | 「路径宽度 / 定点大小」是死参数 | 字段 `paint_dialog.py:233-234` → 透传 `:637-638` → `paint_render.py:165-166` 赋值后**从未读取**（实际线宽走 `track_deg`、点径走 `point_deg`） | 改数值无效果 | P3 定案"接上"：`coord_unit=="px"` 时生效 ⇒ **UI 加"仅『像素』单位生效"注解** | ✅ 已改（Pass 3：选项文案 + 选中提示；`px` 分支由 P3 接上） |
| A3 | 色阶三选一实际只有两套 | `colors.py:50-62`（`COLOR_2023_SCALE = dict(WIKI_SCALE)` + 3 个新键，而那 3 键在 `paint_render.py:69` 被归一化掉） | 后两项输出逐像素相同 | **保留两选项 + 如实注解**（与 Ty4 同构） | ✅ 已改（Pass 3/4 合并：`_SCHEME_UI_LABEL` + 常驻说明） |
| A4 | 区域输入无校验 | 解析 `paint_dialog.py:603-610`（只有恰好 4 个数才收，否则 `except ValueError: pass`）→ 语义 `paint_render.py:88-91` `(top,left,height,width)` | 填错**不报错**、静默用旧值/画出怪图 | **即时校验 + 可见提示** | ✅ **已改（Pass 1）**：红框 + 常驻状态 + 非法阻止渲染 |

**A4 实测证据**（`/tmp/probe_paint_ui_shot.py`）：输入框填 `40,120,30`（3 个数）时

```text
region 输入框文本      = '40,120,30'
self.region（生效值）  = (60, 90, 60, 100)      ← 静默回退到上一次的合法值
_collect_fields()["region"] = None            ← 解析失败被吞
界面上的错误提示        = 无
```

### 2.2 本轮自行走查新发现（全部带实测证据）

| # | 问题 | 证据（`文件:行` / 实测） | 现象 | 修法 | 状态 |
|---|---|---|---|---|---|
| **B1** | **左栏 3 处分节标题与上一行**完全重叠 | 实测 `/tmp/probe_paint_ui_overlap.py`（只读插桩 `_label`/`_button` 记录真实矩形后两两求交）：normal 模式 `'设置'(434,505,44,24) × '最小高度(px)'(434,505,100,19)` 交点 **44×19**；`'显示'(434,701,44,24) × '图例位置'(434,701,68,19)` 交点 **44×19**；region 模式再加 `'参数'(434,309,44,24) × '区域(上,左,高,宽)'(434,309,142,19)` 交点 **44×19**。根因：标题放在 `row(N)-26`（= `row(N-1)+2`），而 `row(N-1)` 的内容高 26px ⇒ 同 y 叠字。截图 `p5_ui_before.png` 里可见"参数上,左,高,宽)"与"图例位置/显示"叠字 | 分节标题与上一行叠字，读不出标题 | 三个标题改放到 `row(N)+1`（那三个**预留空行**本来就空着，实测 y=334/530/726 无任何元素） | ✅ **已改（Pass 2）**：真重叠 3 → **0** |
| **B2** | **「区域」输入框在非区域模式仍可点击/编辑**（不可见控件） | 事件 `paint_dialog.py:405-407`（把事件给**所有** `self._fields`）vs 绘制 `:777-779`（仅 `mode=="region"` 画它）；`input_field.py:304-307` 左键点在 rect 内即 `activate_at` 并 `return True` | 普通/全局模式下 row 2 处有个看不见的输入框能抢焦点，输入的字进了隐藏字段 | 事件路由跳过隐藏字段 + 切换模式时 `deactivate()` | ✅ **已改（Pass 1）**：3/3 用例 PASS |
| **B3** | 「刷新」按钮**不刷新** | `paint_dialog.py:536-538`：`elif key == "refresh": self._status = "已刷新"` —— 不重扫目录、不重读尺寸 | 用户以为列表被重读，实际没有 | 真刷新（重扫当前文件的 `size`）或改名"提示" | ✅ **已改（Pass 3）**：实测 1 KB→3.0 MB、并报告"1 个已不存在" |
| **B4** | 浏览器切换「寻找范围」会**清空已勾选** | `paint_file_browser.py:225-227`（toggle `recursive` → `_refresh()`）→ `:176-179`（`self.checked = [False]*len(files)`、`_file_scroll=0`） | 勾了 5 个 → 切"所有子目录"→ 勾选全丢 | `_refresh` 改为**保留已有路径的勾选**（按路径集合求交） | ✗ **未改**（见 §8.1 B4） |
| **B5** | 「↑ 上一级」与路径输入框**重叠 30px**，重叠区被输入框吃掉 | 字段 `paint_file_browser.py:92-94`：`x0+60 .. x0+60+(W-200)`；按钮 `:101-102`：`x0+60+W-230 .. x0+60+W-120` ⇒ W=1080 时重叠 `[x0+910, x0+940]` = **30px**（算术）；且 `handle_event:195` **先**给输入框（`input_field.py:304-307` 点内即 True） | 按钮左侧 23% 宽度点了没反应（变成聚焦输入框） | 字段宽度改成 `W-200-130` 或按钮左移 | ✗ **未改**（见 §8.1 B5） |
| **B6** | 「去重」按 `(文件名, 尺寸)` 判重而非路径 | `paint_dialog.py:527`：`k = (d['name'], d['size'])` | 不同目录下同名同尺寸的两个文件会被误删其一 | 改按 `d['path']` | ✗ **未改**（低危，见 §8.1 B6） |
| **B7** | `_rebuild_fields` 的 `_conv` 形参从未使用 | `paint_dialog.py:242`（`for key, idx, val, mlen, _conv in rows`），真正的转换硬编码在 `:615-618` | 代码卫生：两个来源不同步（改这里不影响行为） | 统一到 `_conv` 或删形参 | ✗ **未改**（纯代码卫生，见 §8.1 B7） |
| **B8** | 校验/状态提示 **8 秒后消失** | `paint_dialog.py:966`（`time.time() - self._status_ts < 8`）、`paint_file_browser.py:306` 同 | 校验类提示要"看得见"，8 秒后没了 | 校验错误改为**随字段常驻**（不靠状态栏） | ✅ **已改（Pass 1）**：红框常驻 + `_status_sticky` 常驻 |
| **B9** | 小窗口下文件工具条会溢出到左栏 | 8 按钮 ×(96+6)=816px，从 `fr.right` 往左排（`paint_dialog.py:359-372`）；`sw=1280` 时 `bg.w=1240`、文件面板宽 `= bg.w-260-16-…` ≈ 536px ⇒ **溢出 ~280px**（算术；2560 宽下无碍） | 窄窗口下按钮压到参数面板上 | 工具条换行或缩短 | ✗ **未改**（仅窄窗口触发，见 §8.1 B9） |

**B1 的修法为何是 `row(N)+1`**：三处标题当前都在 `row(N)-26`；实测 `row(2)/row(3)`…的元素清单显示 `row(3)=334`、`row(10)=530`、`row(17)=726` **三个空行本来就没有任何元素**（`probe_paint_ui_overlap.py` 的元素表）。把标题从"上一行"挪到"本行"即可零重叠，且**不需要改任何行号、不会撑破面板**（左栏可用高 684px、当前用到 668px）。

---

## 3. 交付物与验收方式

| 交付 | 路径 / 命令 |
|---|---|
| 改前截图 | `output/exploration/p5_ui_before.png`（2560×1540，场景：区域模式 + 非法输入 `40,120,30`；Surface RGB sha256 前 16 = `cee427cca3e67bdc`） |
| 改后截图 | `output/exploration/p5_ui_after.png`（同一脚本、同一场景） |
| 截图探针 | `/tmp/probe_paint_ui_shot.py <out_png> [region_bad|default]` |
| 重叠度量探针 | `/tmp/probe_paint_ui_overlap.py`（输出"左栏元素数 / 重叠对数 / 元素清单"） |
| 回归 | `tests/test_paint.py`（基线 15 passed）、`tests/test_panel_layer_row.py`（单文件 `timeout 300`） |
| sha256 | 每个 pass 前后各记（见各 Pass 小节） |

---

## 4. Pass 1 — 区域输入校验 + 隐藏字段不可交互 ✅ 已完成

**sha256**：改前 `be42025e861822cc2ca5ae1b7272d06a6703a3588aeb762c4a81337c64feee6c` → 改后 `5883cf02fcbd345686b632e7ce991a5ae2bff5a0a94065d3ac046dbc9af20400`（只动 `paint_dialog.py`）

### 4.1 改动清单（一类问题：区域输入的"可校验 + 可交互性"）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `_region_error()`（新增，`paint_dialog.py:289-337`） | 新增即时校验：**4 个数 / 数字 / 高宽>0 / 纬度 −90..90 / 经度 0..360（含跨缝）**；返回可直接显示的中文文案，合法返回 `None` |
| 2 | `_set_mode()`（新增） | 切换绘制范围；**离开「区域」时失活隐藏输入框** + 清掉常驻错误 |
| 3 | `handle_event` 字段循环 | 按**可见性**路由事件：`key=="region" and mode!="region"` ⇒ 跳过（修 B2） |
| 4 | `_click` 模式按钮 | `self.mode = mode` → `self._set_mode(mode)` |
| 5 | `_collect_fields` region 分支 | 只有 `_region_error() is None` 才写入 `out["region"]`（解析失败行为不变，错误文案统一由 (1) 给出） |
| 6 | `_do_render` 开头 | 区域模式下输入非法 ⇒ **明确报错并 return，不再用旧值静默画怪图**；`_status_sticky=True` |
| 7 | `_draw_left` 区域行 | 非法时给输入框画**常驻红框**（不依赖 8 秒状态栏） |
| 8 | `_draw_status` | 校验类提示**常驻**（`_status_sticky`），普通状态仍 8 秒淡出（修 B8 的一半） |

**为什么"高/宽 ≤ 0"必须报错**：`resolve_bounds`（`paint_render.py:93-96`）只在 `width>0 and height>0` 时采纳 region，否则**静默回落到轨迹 bbox 分支** —— 这正是"设了范围却画出怪图"的机制。

### 4.2 验收（`/tmp/probe_paint_region.py`，headless 2560×1540，全部 PASS）

| 组 | 判据 | 结果 |
|---|---|---|
| [1] 校验函数 | 13 个用例（合法 / 3 个数 / 5 个数 / 高=0 / 宽<0 / 非数字 / 空 / 上>90 / 上-高<−90 / 左<0 / 右>360 / 全角逗号） | **13/13 PASS** |
| [2] 隐藏字段（B2） | 普通模式点隐藏框**不聚焦**；区域模式同一点**正常聚焦**；切回普通模式**自动失活** | **3/3 PASS** |
| [3] 非法阻止渲染 | 非法 ⇒ `_result is None`、状态栏 `区域输入有误：需要 4 个数（上,左,高,宽），当前 3 个`、`_status_sticky=True`；合法 ⇒ 校验通过且 `_collect_fields()["region"] == (40.0,120.0,30.0,60.0)` | **5/5 PASS** |
| [4] 截图像素差 | 不同像素 **844 / 3,942,400 = 0.0214%**，包围盒 `(589,303,775,335)`，**恰好等于** region 输入框红框 `(589,303,186,32)` | **PASS**（改动视觉影响被严格限定在目标控件） |

**回归**：`tests/test_paint.py` **15 passed**（与基线一致）、`tests/test_panel_layer_row.py` **5 passed**。

**截图**：`p5_ui_before.png`（Surface RGB sha 前 16 `cee427cca3e67bdc`）vs `p5_ui_after.png`（`7c3acb3b55f56b07`）；同一探针同一场景（区域模式 + 非法输入 `40,120,30`）。

## 5. Pass 2 — 左栏分节标题重叠（B1）✅ 已完成

**一类问题**：分节标题的 y 定位。

| 位置 | 改前 | 改后 |
|---|---|---|
| `_draw_left` 「参数」 | `row(3) - 26`（= row(2)+2，与上一行同 y） | **`row(3) + 1`** |
| `_draw_left` 「设置」 | `row(10) - 26`（与「最小高度(px)」同 y） | **`row(10) + 1`** |
| `_draw_left` 「显示」 | `row(17) - 26`（与「图例位置」同 y） | **`row(17) + 1`** |

**为什么这样改就够**：实测元素清单显示 `row(3)`（y=334）、`row(10)`（y=530）、`row(17)`（y=726）本来就是三个**预留空行**（`probe_paint_ui_overlap.py` 的元素表里没有任何元素）。把标题从"上一行"挪进"本行"即可零重叠，**不动任何行号、不撑破面板**（左栏可用高 684px，当前占 668px）。另：「绘制范围」标题在 `row(0)-26`，它上面没有行 ⇒ 本来就正常，**有意不动**。

**验收**（同一探针，改动前后各跑一次）：

| 模式 | 改前重叠对 | 改后重叠对 |
|---|---|---|
| `normal` | 3（含 1 对探针自造的假阳性）⇒ **真 2** | **1（仅假阳性）** |
| `region` | 4（含同一假阳性）⇒ **真 3** | **1（仅假阳性）** |

假阳性说明：`button '选择地图'` 与我用 `_map_browse_rect` 重建的同名条目是**同一个矩形**（`_draw_left` 里 `br` 既用于 `_button` 又赋给 `_map_browse_rect`），不是真重叠。

**sha256**：`5883cf02…` → **`6369afb0…`**（与 Pass 3 合并记录，见 §6）

---

## 6. Pass 3 — 投影开关接线（任务 B）+ 死控件（A1/A2/A3/B3）✅ 已完成

**sha256**：`app/paint_dialog.py` 改前 `be42025e…` → 最终 **`6369afb01ac3fabaecc31d4955cdfdc1e822b5026ff08fc0659a95d4a712b930`**；`app/paint_file_browser.py` **未改**（`05431663…`，见 §8 未修项）。

### 6.1 改动清单

| # | 位置 | 改动 |
|---|---|---|
| 1 | `__init__` | 新增 `self.projection = "equirect"`（默认=历史行为） |
| 2 | `_rebuild_dropdowns` | 新增 `("projection", 15, [("equirect","等距圆柱"), ("conic","圆锥(Lambert 正形)")])`；**删除 `multireset` 那一行**（P3 定案：Ty4 的 `MultipleFile` 只实现 `==2`，语义为空） |
| 3 | `_draw_left` `dd_lab` | `("multireset","多文件重置")` → `("projection","投影")`（**复用同一行号 15**，布局零位移） |
| 4 | `_do_render` cfg | 新增 `"projection": self.projection`；**删除 `"multireset"`** |
| 5 | 新增 `_on_dd_select(key, v)` | 取代原 `lambda … setattr`：写属性 + 对三类"容易被误解"的选项给**说明性**提示（不改任何渲染参数） |
| 6 | 模块级 `_SCHEME_UI_LABEL` | 色阶两个选项的 **UI 文案**（`经典 Wiki 色阶（同 2023）` / `2023 新版色阶（同经典）`）；**值仍来自 `colors.py`，不新建颜色** |
| 7 | `coord_unit` 选项文案 | `像素(px)` → **`像素(px)·启用宽度/大小`**（A2 的注解；P3 已把 `px` 分支接上 `path_width`/`point_size`） |
| 8 | `_file_action("refresh")` | 由"只改状态文字"改为**真刷新**：重读每个文件的尺寸，并如实报告已不存在的条目数（B3） |

### 6.2 验收（`/tmp/probe_paint_dropdowns.py`，全 PASS）

| 组 | 判据 | 结果 |
|---|---|---|
| [1] 下拉清单 | `projection` 在、`multireset` 不在；投影恰 2 项；圆锥文案含 **Lambert 正形**；默认 `equirect`；色阶带"同 2023/同经典"；坐标单位带"启用宽度/大小" | **7/7 PASS** |
| [2] 透传 | 通过**真实下拉回调**选 conic ⇒ `self.projection=="conic"`；`_do_render` 传给 `TrackMapRenderer` 的 cfg 里 **`projection == "conic"`**；cfg **不再含 `multireset` 键**；切回 equirect 同样正确；切换时给用户可见提示 | **5/5 PASS** |
| [3] 渲染侧链路 | P3 `TrackMapRenderer({'projection':'conic'}).projection == "conic"`；非法值归一 `equirect`；**P4 `app/paint_projection.py` 已交付**（`PROJECTIONS=('equirect','conic')`）且 `project('conic',130,20,make_ctx(...)) = (400.0, 311.887)` | **4/4 PASS** |
| [4] 刷新（B3） | 文件从 1 KB 涨到 3 MB 后点「刷新」⇒ 尺寸变 `3.0 MB`；文件被删后 ⇒ 状态如实说"1 个已不存在" | **2/2 PASS** |
| [5] 说明性提示 | 选 `px` ⇒ 提示"路径宽度/定点大小生效"；选 `deg` ⇒ 提示"这两个框不生效"；**且切换不改变 `path_width` 的值** | **3/3 PASS** |

**回归**：`tests/test_paint.py` **15 passed**（= 基线）、`tests/test_panel_layer_row.py` **5 passed**（Pass 1 后与 Pass 3 后各跑一次，均通过）。

### 6.3 渲染侧接线状态（如实记录，不谎报效果）

| 环节 | 状态 | 证据 |
|---|---|---|
| UI 开关 | ✅ 已接线（本轮） | `_dropdowns["projection"]`、`cfg["projection"]` |
| P3 渲染器读键 | ✅ 已就绪 | `paint_render.py:232-233` `_pj = o.get("projection") or "equirect"`，非法值归一 |
| P3 投影钩子 | ✅ 已就绪 | `paint_render.py:171-192` `_project_via_p4()`，每进程一条 warning 回落 |
| P4 模块 | ✅ 已交付 | `app/paint_projection.py`（Lead 通报 sha `274c2b7e…`）；实测 `project('conic',…)` 返回 `(400.0, 311.887)` |
| **QQ 样式渲染器** | ⏳ **sim-core 仍在写** | `app/paint_render_qq.py`（本轮 `ls` 时不存在） |
| **"选圆锥后图纸一定变样"** | ❌ **不能这样承诺** | 需要 P3 把**所有坐标变换**都走 `project()` 且 QQ 样式渲染器落地；本轮只保证"选择被正确透传、且链路上的三个模块都能正常读到/处理它"。**我没有验证"选 conic 后的实际图纸像素"** —— 这是 P3/P4/P6 的范围 |

**截图**（`p5_ui_before.png` vs `p5_ui_after.png`，同一探针同一场景"区域模式 + 非法输入 `40,120,30`"）：可见四处差异 —— ① 区域输入框**红框**；② `参数` 标题不再与区域行叠字；③ `设置`/`显示` 标题不再与上一行叠字；④ 「多文件重置/不重置」整行变成 **「投影/等距圆柱」**，色阶显示为 `经典 Wiki 色阶（同 2023）`。Surface RGB sha 前 16：`cee427cca3e67bdc` → `3f82269f5b7ea2d5`。

---

## 7. Pass 4 — 色阶选项注解（A3）

**已在 Pass 3 一并完成**（与死控件同属"选项语义说明"一类，故合并）：`_SCHEME_UI_LABEL` 把两个选项的显示文案改为 `经典 Wiki 色阶（同 2023）` / `2023 新版色阶（同经典）`，并在选中时给一条常驻说明：*"经典 Wiki 与 2023 两套色阶在本项目与原版 Ty4 中都相同（Ty4 的 NewColor 开关同样无视觉差异），不是本程序的缺陷"*。

**没有做的事（有意）**：不新建 RGB、不移除选项、不动 `app/constants/colors.py`（不在写作用域）。依据：`colors.py:50-62`、`TY4_SPEC.md` §4.2/§4.3、Lead 补信。

---

## 8. 未修项 / 风险 / unresolved

### 8.1 走查发现但**本轮有意未改**（都属其他问题类别，避免"一轮改多类"）

| # | 问题 | 为什么未改 |
|---|---|---|
| B4 | 浏览器切换「寻找范围」清空已勾选（`paint_file_browser.py:225-227` → `:176-179`） | 属"浏览器交互状态保持"一类，与本轮三类不同；修法是 `_refresh` 按路径求交保留勾选，**会改变 `_refresh` 的既有语义**，需单独一轮 + 单独回归 |
| B5 | 「↑ 上一级」与路径输入框重叠 30px 且被输入框吃掉 | 同上（`paint_file_browser.py` 的几何）；改字段宽度即可，但会影响输入框可用宽度，值得单独截图验证 |
| B6 | 「去重」按 `(文件名, 尺寸)` 而非路径判重（`paint_dialog.py:527`） | 低危 + 语义有争议（同名同尺寸是否算重复），需先确认用户意图 |
| B7 | `_rebuild_fields` 的 `_conv` 形参从未使用（`paint_dialog.py:242`） | 纯代码卫生，无用户可见影响；改动会碰 8 行字段定义，风险/收益不划算 |
| B9 | 窄窗口（≤1280 宽）下文件工具条溢出到左栏（算术：8×102=816px > sw=1280 时面板宽 ~536px） | 有条件触发（当前 2560 宽无碍）；修法是换行或缩短，属布局一类 |

### 8.2 风险与边界

- **状态栏长文案可能压到统计面板**：`_draw_status` 画在 `pr.bottom - 32`，不裁剪；本轮新加的三条说明性提示较长（最长约 50 汉字 ≈ 700px < 预览区宽 1222px，实测截图无溢出）。若将来文案更长需加裁剪。
- **`self.multireset` 属性保留**（只是不再显示/不再透传）：极端情况下外部工具若读它仍是 `"none"`，与 P3 的"旧调用方传该键也不报错"一致。
- **投影开关的"能点但可能看不出差异"是预期内的**（见 §6.3），报告已如实标注，开关文案也会在选中圆锥后提示"若图纸外观暂未变化，说明渲染侧仍在接线中"。

### 8.3 unresolved

1. **"选圆锥后图纸像素真的变了"未验证**：需要 P3 完成全部坐标变换接线 + `paint_render_qq.py` 落地。卡点：不在我的写作用域，且需要 P4/P3 交付。可验证方式（建议给 P6）：同一组台风、`projection` 取 `equirect`/`conic` 各渲一次，比 rawsave 的像素 sha 与经纬网几何残差。
2. **B4/B5 是否要在下一轮修**：取决于 Lead 排期；两者都在 `paint_file_browser.py`，建议合并成一轮"浏览器交互"。
3. **真实窗口下的观感**：全部验收在 headless 2560×1540 完成，未开真窗口（纪律要求）。像素差只落在目标区域（Pass 1 实测 844/3 942 400 = 0.0214%），但"人眼看是否舒服"未做人工比对。
4. **`paint_dialog.py` 无 v4 基线备份**：`_backup/state_v4/` 里**没有** `paint_dialog.py`（实测 `ls`）⇒ 本轮只能靠 sha256 与 Pass 0 截图做前后对比；若需要"整文件回退"，本轮唯一回退手段是 `git`/手工反向 patch（见 §9）。建议 Lead 把本轮最终版纳入下一份备份。

---

## 9. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 区域输入校验（本轮新增） | `app/paint_dialog.py` `_region_error()`（约 `:289-337`） |
| 模式切换 + 隐藏字段失活 | `_set_mode()` |
| 事件按可见性路由 | `handle_event` 字段循环（`region` 非 region 模式跳过） |
| 非法输入阻止渲染 + 常驻提示 | `_do_render` 开头；`_draw_status`；`_draw_left` 红框 |
| 投影开关 | `_rebuild_dropdowns` 的 `("projection", 15, …)`；`__init__` 的 `self.projection` |
| 投影透传 | `_do_render` 的 `"projection": self.projection` |
| 死控件移除 | `_rebuild_dropdowns`（无 `multireset`）、`_draw_left` `dd_lab`、`_do_render` cfg |
| 选项说明性提示 | `_on_dd_select()` |
| 色阶 UI 文案 | 模块级 `_SCHEME_UI_LABEL` |
| 「刷新」真刷新 | `_file_action("refresh")` |
| 分节标题定位（Pass 2） | `_draw_left` 的 `参数`/`设置`/`显示` 三处 `_label(...)` |
| 截图探针 | `/tmp/probe_paint_ui_shot.py <out.png> [region_bad\|default] [src.py]` |
| 重叠度量探针 | `/tmp/probe_paint_ui_overlap.py` |
| Pass 1 验收探针 | `/tmp/probe_paint_region.py` |
| Pass 3 验收探针 | `/tmp/probe_paint_dropdowns.py` |
| 未修项（浏览器） | §8.1：B4/B5/B6/B7/B9 |
| 投影契约（P4） | `app/paint_projection.py`（`PROJECTIONS`/`project`/`make_ctx`/`CONIC_REFERENCE`） |
| 投影消费（P3） | `app/paint_render.py:160-192, 228-243` |
