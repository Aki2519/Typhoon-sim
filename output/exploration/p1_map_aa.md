# P1 地图/路径渲染抗锯齿（`renderer.py` + `_draw_path_mixin.py` + `typhoon_render.py`）

> 作者：teammate `app-sim`（P1）｜ 任务 `task-28` ｜ 2026-10-02
> 依据：`_BRIEF4.md` §1 H2（用户原话："既然无抗锯齿，那么在程序的地图渲染也有这个问题，也改掉"）。
> 纪律：逐处审计、**不机械替换**；确定性指标验收（中间 alpha 像素数 / alpha 质量比 / 质心位移 / bbox / 缓存计数），**不用计时**（本机 P/E 核差 1.90×）。
> 回退：改动前副本在 `G:\tmp\p1_backup\`；SHA256 见 §1。共享底座 `app/paint_aa.py` **只读使用，未改**。

---

## 一句话定位

把地图渲染里**真正会产生锯齿的图元**（报点圆点、EX 三角、路径斜折线、虚线、拖动指示环、实时位置点、海洋区薄填充）换成 `app.paint_aa` 的超采样绘制；同时用**逐处审计**证明"轴对齐实心 rect / 水平垂直 1px 线 / 已经是软边的掩膜"**不该改**，并写明三处有证据的例外。

---

## 1. 清单与职责

| 文件 | 行数 | 本任务角色 | 改了几处 |
|---|---|---|---|
| `app/ty_sim_mixins/_draw_path_mixin.py` | 1573 → 1612 | **路径绘制主体**（最显眼的锯齿源） | **8 处改 AA**；4 处判定不改；1 处例外保持 aliased |
| `app/renderer.py` | 437 → 461 | 主渲染器（海洋区边界/薄填充、经纬网、UI 面板） | **2 处改 AA**（薄填充 + 海洋边界，后者为**修订**）；10 处判定不改 |
| `app/typhoon_render.py` | 169 | 坐标/样条 mixin —— **没有任何 `pygame.draw.*`** | **0 处**（证据：`grep -c "pygame.draw\." = 0`） |
| `app/paint_aa.py`（**只读**） | 176 | 共享 AA 底座（4× 超采样精灵缓存 + 6 MB 字节上限 LRU） | 未改 |

**SHA256**

| 文件 | 改前 | 改后 |
|---|---|---|
| `app/renderer.py` | `4dc6909178475305aecbbdcbde472cb757f24a2b9e3ce9596600b183ff606920` | `7fcc3b1589e0707c71f32c91b16c3b62388c9a9db910b30eb86ecdd3c687d557`（分块修订后） |
| `app/ty_sim_mixins/_draw_path_mixin.py` | `b64056429f4d742ec67601b822d25bcc6fcac9b18accb212d69ba221f75d905d` | `69b9374613bb9fbd9b6632c62eae5de57c8e88193c08d1802a2aa974d8537d27`（分块修订后） |
| `app/typhoon_render.py` | `81fafe3c82932bff3355fd74815edbd18791495612f1ea352dbaee1da98d31a0` | **未改**（同上） |

> ⚠ `_backup/SHA256_STATE_V4.txt` 只覆盖 10 个文件、**不含本任务这三个**；`_backup/state_v4/` 里也没有它们的副本。回退基线用**本报告记的改前 SHA256** + `G:\tmp\p1_backup\` 的整文件副本（我已核过：用它替换回去跑 `tests/test_pan_reuse.py` = 8 passed）。
> 
> **分块修订前的中间态**（含 `d3a5fdd784…` / `7c75708580…` + 已通过 59 测试的版本）已按纪律**存内容**到 `output/exploration/_backup/state_v7/{_draw_path_mixin.py,renderer.py}`（`sha256sum` 与上表中间态一致）。

---

## 2. 逐处审计（34 处全表）

判定口径：**需要 AA** = circle / polygon / ellipse / 斜线 / 粗线；**不需要 AA** = 轴对齐实心 rect、水平或垂直的 1px 线、以及"已经经过 smoothscale 平均"的掩膜。

### 2.1 `app/ty_sim_mixins/_draw_path_mixin.py`（16 处 `pygame.draw.*`）

| 改前行 | 图元 | 用途 | 判定 | 依据 |
|---|---|---|---|---|
| 83 | `line` | `_draw_dashed` 的每段 dash（**任意方向**） | ✅ **改 AA** | 斜线；单个 dash 只 6px 长 ⇒ 区域包围盒极小、代价可忽略 |
| 101 / 104 | `lines` | `_draw_lines_safe` 路径折线尾段/整段（w=2） | ✅ **改 AA** | **最显眼的锯齿源**（用户点名的"地图渲染"） |
| 122 | `line` | `_draw_lines_colored` 逐段着色路径线（w=2/4） | ✅ **改 AA** | 同上 |
| 136 | `circle` | `_get_circle_marker` 报点圆点精灵（缓存 512） | ✅ **改 AA** | 圆；**用户抱怨的"定点像素化"就是它**；精灵缓存 ⇒ 一次性代价 |
| 168 | `polygon` | `_get_tri_marker` EX 三角精灵（缓存 128） | ✅ **改 AA** | 多边形；同上 |
| 1155 | `circle` | 实时位置圆点（r=2） | ✅ **改 AA** | 圆；`paint_aa` 精灵缓存，代价极小 |
| 1232 | `circle` | `_get_radial_mask` 的 32×32 掩膜 → `smoothscale` 到目标尺寸 | ❌ **不改** | 它本身马上被 `smoothscale` 平均，**已经是软边**；这是掩膜不是图元 |
| 1262 / 1270 | `line` | 编辑吸附网格的竖线 / 横线（1px） | ❌ **不改** | **轴对齐**，整数像素边界精确 |
| 1368 / 1369 | `circle(width=2/1)` | 拖动指示环（r≥11） | ✅ **改 AA** | 半径大、环细 ⇒ AA 收益明确（`aa_ring`） |
| 1373 | `line` | 十字准星 4 段（全水平/垂直，1px） | ❌ **不改** | 轴对齐 1px |
| 1439 / 1440 | `circle(width=2)` | 编辑**选中点**的黄/白环 | ⚠️ **不改（例外 R2）** | 见 §3.3：与红色高亮盘在**同一半径区间互相覆盖**，AA 后两者软边互吃，`tests/test_pan_reuse.py` 用来定位的饱和红像素从 4 个变 0 个 |
| 1451 | `circle(width=1)` | 悬停点环（1px） | ⚠️ **不改（例外 R2）** | 同上（1px 细环，AA 收益亚像素级） |
| — | `_get_rect_marker` 的 `surf.fill` | 不可计算 ACE 台风的小方块（轴对齐实心） | ❌ **不改** | **轴对齐实心方块本来就是精确的**（brief §1 明确） |
| — | `_blit_highlight` 复用的两个圆盘 | 当前位置红盘 + 中心点色盘 | ⚠️ **不改（例外 R2）** | 见 §3.3；另开 `_get_circle_marker_aliased` 专用缓存（不与 AA 精灵混键） |

### 2.2 `app/renderer.py`（13 处）

| 改前行 | 图元 | 用途 | 判定 | 依据 |
|---|---|---|---|---|
| 54 | `line` | `_clip_seg_rect()` 内的通用线段绘制 | ❌ **不改（死代码）** | 全仓 `grep -rn "_clip_seg_rect"` = **只有定义、零调用点** ⇒ 给它加 AA 是纯浪费；已在报告标注（建议清理） |
| 127 | `line` | 海洋区边界折线（**任意方向**，2px） | ✅ **改 AA（修订）** | 初版判"不改"（`aa_line` 单段 4× 超采样在全屏级段上 ≈252 MB/帧）。**修订**：改用 §3.1 的**分块**超采样并传 `max_ss_pixels=1_000_000` ⇒ 峰值=单块 **3.8 MB**、AA 全覆盖（实测 5 段跨屏 mid_alpha=**4938**，见 §5）。该层只在 `cfg.show_ocean_areas=True` 时画（默认 False） |
| 151 | `polygon` | 薄海洋区的实心填充（任意方向） | ✅ **改 AA**（`paint_aa.aa_poly`） | 多边形；顶点已按视口过滤，包围盒 = 该区在屏内范围；每次一处、且只在上面那个开关打开时执行 |
| 176 / 180 | `line` | 经纬网竖线 / 横线（1px） | ❌ **不改** | **轴对齐** |
| 193 / 194 | `rect(+radius)` | 图例底框 / 描边 | ❌ **不改** | 轴对齐 UI 面板（圆角半径小，属 UI 而非地图地形） |
| 197 | `rect` | 图例色块（14×12） | ❌ **不改** | 轴对齐 |
| 322 / 323 | `rect` | 错误面板底/边 | ❌ **不改** | 轴对齐 UI |
| 351 / 352 | `rect` | 顶部 toast 底/边 | ❌ **不改** | 轴对齐 UI |
| 431 | `rect` | 统计面板边框 | ❌ **不改** | 轴对齐 UI |

### 2.3 `app/typhoon_render.py`（0 处）

`grep -c "pygame.draw\." app/typhoon_render.py` = **0**。该文件是坐标变换 / 旋转 / 屏幕点 / geo 样条缓存，**没有任何绘制调用** ⇒ 无需 AA。任务表里写的"台风图形"实际分布在 `_draw_path_mixin.py`（本任务，已改）与 `_draw_icon_mixin.py`（他人 scope）。

**合计（修订后）：改 AA 10 处（path 8 + renderer 2）；判定不改 19 处（含 1 处死代码）；刻意保留 aliased 3 处（例外 R2）。**

### 2.4 证据索引（`文件:行`，改后行号）

| 结论 | 位置（改后行号，已抽查） |
|---|---|
| 折线区域超采样（核心） | `app/ty_sim_mixins/_draw_path_mixin.py:109-160`（`_draw_segments_aa`）；上限常量 `:103`（`_AA_MAX_SS_PIXELS`）、`:106`（`_AA_SS`） |
| 虚线入口 | `app/ty_sim_mixins/_draw_path_mixin.py:71-88` |
| 安全折线入口（防跨屏伪线） | `app/ty_sim_mixins/_draw_path_mixin.py:162-187` |
| 逐段着色折线入口 | `app/ty_sim_mixins/_draw_path_mixin.py:189-226` |
| 报点圆点精灵改 AA | `app/ty_sim_mixins/_draw_path_mixin.py:228-252`（`paint_aa.aa_disc` 在 `:244`） |
| 例外 R2 的专用缓存 | `app/ty_sim_mixins/_draw_path_mixin.py:255-278`（`_get_circle_marker_aliased`） |
| EX 三角精灵改 AA | `app/ty_sim_mixins/_draw_path_mixin.py:291-311`（`paint_aa.aa_poly` 在 `:302`） |
| 例外 R2 主体（高亮盘） | `app/ty_sim_mixins/_draw_path_mixin.py:1106-1130`（`_blit_highlight`） |
| 实时位置点改 AA | `app/ty_sim_mixins/_draw_path_mixin.py:1309` |
| 拖动指示环改 AA | `app/ty_sim_mixins/_draw_path_mixin.py:1524-1526` |
| 例外 R2（选中/悬停细环，保持 aliased） | `app/ty_sim_mixins/_draw_path_mixin.py:1603`、`:1615` |
| renderer 薄区填充改 AA | `app/renderer.py:168`（`paint_aa.aa_poly`） |
| renderer 海洋边界改 AA（分块，cap=1M） | `app/renderer.py:124-166` |
| renderer 死代码 | `app/renderer.py:25-56`（`_clip_seg_rect`，全仓零调用） |
| 未改的经纬网 | `app/renderer.py:193-197` |
| 未改的吸附网格 / 十字准星 | `app/ty_sim_mixins/_draw_path_mixin.py:1416`、`:1424` / `:1530` |
| AA 底座 | `app/paint_aa.py:95-176`（`aa_disc:95` / `aa_ring:113` / `aa_poly:130` / `aa_line:153`）；缓存与计数 `:46-84` |

---

## 3. 核心机制详解

### 3.1 为什么路径折线**不能**直接用 `paint_aa.aa_line`

`aa_line(..., width>1)` 的实现是"按**该段自己的包围盒**建一张 4× 超采样面 → smoothscale"。对台风路径：

* 条数：`smooth_path` 开时每报点 10 段（`config.json: smooth_path_segments=10`），50 个报点 ⇒ **~500 段**；
* 每段长度：跨屏段可达上千像素 ⇒ 单段超采样面可达 **252 MB**。

⇒ 代价是 **O(段数) × O(段长²)**，正是任务 §验收4 要求"报出来并给替代方案"的那类爆炸（`paint_aa` 自己的注释也提醒"段数极多且要求粗线应改用整画布超采样"）。

**P1 的做法（修订版：整组包围盒 + 分块超采样）**：`_draw_segments_aa(surface, segs, width, max_ss_pixels=None)` —— 整组线段只算**一个**包围盒；若超采样像素数超过 `max_ss_pixels`，就按 `tile` 网格**切块**，每块各自"一张超采样面 → `pygame.draw.line` 逐段画进去 → `smoothscale` → blit"。代价与**组包围盒面积**成正比、**与段数无关**，而**内存峰值恒等于单块**：

```python
tile = max(32, isqrt(cap // (ss*ss)))          # cap=4M, ss=2 ⇒ tile=1000 px
for oy in range(y0, y0+hh, tile):
    for ox in range(x0, x0+ww, tile):
        big = pygame.Surface((tw*ss, th*ss), SRCALPHA)      # 每块一张, 用完即弃
        for (x1,y1),(x2,y2),c in segs:
            if 段与该块不相交: continue                      # 快速剔除
            pygame.draw.line(big, c, scaled(a), scaled(b), w*ss)
            if lw >= 6: pygame.draw.circle(big, c, b, lw//2)  # 只在粗线时补接头
        surface.blit(pygame.transform.smoothscale(big, (tw, th)), (ox, oy))
```

> ⚠ **修订记录（Lead 实测报缺，2026-10-02）**：初版是"**整组一张面 + 超限就回落到 `pygame.draw.line`**"，等价于"**包围盒 > 1 M px（≈1000×1000）就没有 AA**"。而**横跨屏幕的台风路径**包围盒轻松到 1912×1072 = 2.05 M px ⇒ **最常见的情形反而完全没 AA**（实测 `mid_alpha=0`，`G:/tmp/p1_fullscreen_check.py` 复现：全屏 8198656 SS 像素 → 回落 → 0）。
> 分块版修掉了它，并且**小包围盒（≤cap）时只有 1 块 ⇒ 与"单张面"实现逐像素一致**（见下方接缝验证）。

**实测确定性代价与 AA 覆盖（`G:/tmp/p1_tiled_verify.py`，本次修订后）**

| 用例（斜线横跨画布） | 组包围盒 | `mid_alpha` | 块数 | **单块峰值** | 显式 Surface |
|---|---|---|---|---|---|
| 小图 408×308 | 400×300 | 683 | 1 | 1.8 MB | 1 |
| 半屏 1008×608 | 1000×600 | 1571 | 1 | 9.2 MB | 1 |
| **全屏 1920×1080** | 1912×1072 | **2915**（初版 **0** ❌） | 4 | **15.3 MB** | 4 |
| 超宽 2560×1540 | 2552×1532 | **4083** | 6 | **15.3 MB** | 6 |
| 500 点路径 w=2（`p1_aa_probe2.py`） | 960×640 级 | 596 | 1 | — | **1** |
| 500 点路径 w=4 | 同上 | 611 | 1 | — | **1** |
| renderer 海洋区边界（5 段跨屏，cap=1 M） | 1912×1072 | **4938** | 8 | **3.8 MB** | 8 |

⇒ **内存峰值与包围盒大小解耦**（全屏 15.3 MB、超宽也 15.3 MB；renderer 用 1 M cap ⇒ 3.8 MB），**不是 O(段数) 的分配**，也不会喂爆 `paint_aa` 的 6 MB 缓存（该路径根本不进 `paint_aa` 的缓存）。

**接缝验证（关键）**：把 cap 调到 10¹² 强制**单块**，与 cap=4 M 的**分块**结果**逐像素比对**：

```
分块(cap=4M) vs 单块(cap=1e12): 不同像素 0, 最大 alpha 差 0   ✅
```

⇒ 块原点都是整数、`ss` 为整数 ⇒ 每个输出像素的 2×2 个超采样点完整落在同一块内 ⇒ **无接缝、无重复绘制、无遗漏**。

### 3.2 三个调用点如何接进去（保持外部契约不变）

* `_draw_dashed`：把一串 dash 展开成 `((x1,y1),(x2,y2),color)` 列表 → 一次 `_draw_segments_aa`。
* `_draw_lines_safe`：保留原有"按 `max_seg_len` 切段防跨屏伪线"的逻辑，改成把每段 run 交给 `_draw_segments_aa`（原先是 `pygame.draw.lines` 一次画一整段）。
* `_draw_lines_colored`：逐段颜色 + EX/SS/SD 走虚线；虚线展开进**同一张**超采样面；遇到跨折返点（原先会 `continue` 跳过）时先把已积累的组画掉再开新组 —— 组数上限 = 折返次数（极少），仍与段数无关。

**外部契约零变化**：三个函数的签名、`points`/`point_colors`/`point_types` 语义、切片规则、颜色取值全部照旧；调用点（`_render_path_to_surface` / `_render_drag_path_surface` 的 12 处）一行没改。

### 3.3 唯一一处"有证据地不改"（例外 R2）

`_blit_highlight` 原来是"blit 半径 `highlight_r` 的红盘（`CUR_POS=(220,60,60)`）→ 再用半径 `radius` 的点色盘盖住中心"，**可见部分只有 `[radius, highlight_r]` 那 2px 细环**；而 `_draw_edit_selection` 随后又用白环（span `[ring_r-2, ring_r]`）把这段区间再压一层。

我把圆点/圆环改 AA 之后，`tests/test_pan_reuse.py::TestEditSelectionHighlight::test_highlight_follows_selection` **失败**（它靠"画面上存不存在 `(220,60,60)`±容差 的像素"来定位选中点）。逐层定位（`G:/tmp/p1_diag.py`，在测试自己的 FakeSim 场景里打印颜色直方图）：

| 版本 | 命中判据的像素 | 直方图里的红 |
|---|---|---|
| 改前（aliased，全部） | **4** | `RGB(220,60,60) alpha=255` ×4 ✅ |
| 红盘+内盘都改 AA | **0** | 无（`(142,62,62)` 之类被混掉） |
| 红盘 aliased、**内盘 AA** | **0** | `RGB(142,62,62) alpha=255` ×4 ❌（内盘软边把红压暗） |
| 红盘+内盘**都 aliased**（当前） | **4** | ✅ 测试通过 |

⇒ 根因：**2px 细环上叠加两层软边，饱和度必然被吃掉**。而该测试编码的是**可观察的产品行为契约**，修改它要动 `tests/`（不在本任务写作用域）。
**决定**：这一处（红盘 + 其中心点色盘 + 选中/悬停细环）**保持无 AA**，并写成显式注释 + 独立缓存 `_get_circle_marker_aliased`（32 条上限，避免与 AA 精灵共用 `(radius,color)` 键互相污染）。
**影响评估**：这些都是 **2px/1px 细环**，AA 收益亚像素级；而**报点圆点标记**（`_make_point_marker` → `_get_circle_marker`）、**路径折线**、**虚线**、**EX 三角**、**实时位置点**、**拖动指示环**（r≥11）全部保留 AA ⇒ 用户点名的"定点/原点像素化"与"地图上最显眼的锯齿（路径线）"都已被覆盖。
**替代方案（留待裁决）**：把红环也 AA，同时把 `test_highlight_follows_selection` 从"找饱和色像素"改成"按 alpha>0 统计环位置"。

### 3.4 ⚠ 更正（我先前报错的一条）：`pygame.draw.aaline` **是有抗锯齿的**

**先前结论（错）**：我在初版报告里写过"`pygame.draw.aaline` 在本环境没有抗锯齿（中间 alpha 像素 = 0）"。
**更正**：该结论**不成立**。Lead 复测后我按**角度扫描**重测，确认 `aaline` 有 AA，而且我那条 0 值的真正原因是**测试用例退化**，不是 pygame 的问题。

**复测证据（`G:/tmp/p1_aaline_angle.py`，未铺底的 SRCALPHA 面，线长 260px）**

| 线方向 | `draw.line(w=1)` | `draw.aaline` |
|---|---|---|
| 水平 (20,150)→(280,150) | mid=0，colors=1 | mid=0，colors=1（轴对齐本来就不需要 AA） |
| 垂直 (150,20)→(150,280) | mid=0，colors=1 | mid=0，colors=1 |
| **45°** (20,20)→(280,280) | mid=0，colors=1 | mid=**260**，colors=2 |
| **30°** (20,150)→(280,0) | mid=0，colors=1 | mid=**509**，colors=**33** |
| **5°** (20,150)→(280,127) | mid=0，colors=1 | mid=**517**，colors=**255** |
| **1°** (20,150)→(280,145) | mid=0，colors=1 | mid=**514**，colors=**83** |

**我为什么会测出 0（根因）**：初版用的是 **(20,20)→(120,120)，一条端点恰好落在整数像素网格上的 45° 线**。45° 且对齐时，理想 AA 的每像素覆盖率**恰好是 1.0 或 0.0**，压根不存在"部分覆盖"像素 ⇒ 无论实现多正确，`0<a<255` 都必然是 **0**。**这是检测 AA 的最坏用例**（把线拉长到 260px 后同样 45° 也能测出 mid=260，可见是"端点相位"而非"实现无 AA"）。
另有一个必须写清的口径前提（Lead 指出，成立）：**"中间 alpha 像素"只能在未铺底、且带 alpha 通道的面上量** —— 不透明面 `get_at()[3]` 恒 255，或在 SRCALPHA 面先 `fill` 了不透明底，该度量都会恒为 0。我的初版确实用的是**未铺底**的 SRCALPHA 面（这一点没错），错的是**线角度**。

**对本任务无影响**：`_draw_segments_aa` 本来就不走 `aaline`，全部走超采样（§3.1），所以 P1 的所有产物与结论不受这条更正影响。
**顺带的量化对照（诚实记录）**：在 1px 细线上 `aaline` 的 alpha 梯度**比我的 SS=2 更细**——5° 线 `aaline` mid=517/colors=255，而 `_draw_segments_aa` mid=285/colors=4（SS=2 只有 4 级覆盖；45° 时两者接近：260 vs 261）。⇒ **细线（w=1）用 `aaline` 更优，粗线（w≥2）只能用超采样**。P1 的路径线宽是 2/4，所以维持超采样不变；**P2/P3 若画 1px 线应优先用 `aa_line`（=aaline），但别再用 45°/轴对齐线去判它有没有 AA**。

---

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `output/exploration/p1_aa_before_after.png` | PNG 4860×1982 | 5 张图元 ×2 行（上=旧 aliased，下=新 AA），4× 放大 | `G:/tmp/p1_aa_probe2.py` | 人工目视核对（本轮已核：位置/尺寸一致，边缘变软） |
| `app/paint_aa.py` 的 `_CACHE` | 内存 LRU | 上限 6 MB；实测 500 点路径后 items=2 / 12 KB | `paint_aa` 自身 | 所有 AA 绘制 |
| 路径缓存面 `ty._path_cache_full/_traversed` | `pygame.Surface` | bbox 尺寸（既有设计，未改） | `_render_path_to_surface` | `draw_typhoon` |
| `_circle_cache` / `_tri_marker_cache` / `_rect_marker_cache` | 内存 LRU | 512 / 128 / 128 条（既有上限，未改） | 本 mixin | 标记绘制 |

本轮**不新增**任何持久资产（只多了一张验收对比图）。

---

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
# 全路径写(本会话 $VAR 展开不可靠)
PY=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# ① 逐图元抗锯齿度量 + 几何等价 + 代价 + 对比图(确定性, 无计时)
$PY G:/tmp/p1_aa_probe2.py
# ② 线宽/相位实验与 SS 倍率扫描(解释 IoU 为何对细线偏低)
$PY G:/tmp/p1_sweep.py
$PY G:/tmp/p1_width2.py          # 量 draw.line/lines 的 width 与 aaline 行为
$PY G:/tmp/p1_aaline_angle.py    # ★ 按角度复测 aaline 的 AA(§3.4 的更正依据)
$PY G:/tmp/p1_fullscreen_check.py # ★ 复现"全屏包围盒回落 ⇒ mid_alpha=0"的缺口
$PY G:/tmp/p1_tiled_verify.py     # ★ 分块修订验收: AA 覆盖/块数/峰值/接缝逐像素一致
$PY G:/tmp/p1_aaline_recheck.py  # 三种度量 × 四种底 的 aaline 复测
# ③ 例外 R2 的逐层定位(在测试自己的 FakeSim 场景里打印颜色直方图)
$PY G:/tmp/p1_diag.py NEW
# ④ 回归(单文件, 禁全量)
$PY -m pytest tests/test_render_layers.py tests/test_render_geom.py \
      tests/test_pan_reuse.py tests/test_seam_render.py tests/test_normal_tracking.py \
      -q --no-header
```

**验收实测（`G:/tmp/p1_aa_probe2.py`，`SDL_VIDEODRIVER=dummy`）**
> **度量前提（必须写清，否则数字不可比）**：本表所有"中间 alpha 像素"都是在**刚 `pygame.Surface(..., SRCALPHA)` 出来、未 `fill` 任何底**的面上测的（`get_at()[3]` 能反映真实覆盖率）。若换成不透明面、或在 SRCALPHA 面上先铺了不透明底，`get_at()[3]` 恒为 255 ⇒ 该度量**必然为 0**（这不是"没有 AA"）。
> 另：**不要用 45° 或轴对齐线做 AA 检测用例** —— 覆盖率恰好 0/1，正确实现也可能测出 0；见 §3.4 的角度扫描。

| 图元 | 中间 alpha 像素(旧→新) | alpha 质量比 | 质心位移 | ink bbox | IoU(ink / core) |
|---|---|---|---|---|---|
| 报点圆点 (r=16) | **0 → 100** | 1.024 | **0.000 px** | **完全相同** | 0.929 / 0.970 |
| EX 三角 | **0 → 42** | 1.021 | 0.377 px | ±1px（软边外扩） | 0.897 / 0.944 |
| 拖动指示环 (r=20,w=2) | **0 → 196** | **1.004** | **0.000 px** | 内缩 1px（软边未到满值） | 0.446 / 0.350 |
| 路径折线 w=2（10 点波折） | **0 → 596** | **0.996** | 0.552 px | ±1px | 0.625 / 0.549 |
| 路径折线 w=4 | **0 → 611** | 1.011 | 0.677 px | ±1px | 0.767 / 0.744 |
| 单条 45° 斜线（逐行质心） | — | — | **均值 0.251 px / 最大 0.500 px** | — | — |

**判据解读（重要）**：细斜线的**二值 IoU 天然对相位敏感** —— 把一条"硬边阶梯线"换成"同标称宽度的软边线"，二值重叠率必然掉到 0.55–0.77（`G:/tmp/p1_sweep.py` 扫了 ss=2/3 × 线宽 1..8 共 22 种组合，最高也只有 0.625/0.549，且更细的线 IoU 更差）。因此**几何等价性用相位无关的三个量来证**：
* **alpha 质量比 0.996–1.024**（面积/视觉重量等价）；
* **质心位移 ≤0.68 px**（位置等价；单条斜线逐行质心均值 0.25 px）；
* **ink bbox ±1px**（范围等价，±1 来自软边）。
> 折线"更胖"是 AA 的固有代价：实测 ink +36%（823 → 1119），因为它把硬边扩展成 1px 软带。

**分块修订后的全屏验收（`G:/tmp/p1_tiled_verify.py`）**：全屏 1920×1080 斜线 `mid_alpha` **0 → 2915**（4 块、峰值 15.3 MB）；超宽 2560×1540 → **4083**（6 块、峰值 15.3 MB）；几何等价：alpha 质量比 **0.998**、质心位移 **0.566 px**、ink bbox ±1px；**分块 vs 强制单块逐像素差 = 0**（无接缝）；renderer 海洋边界（cap=1 M）5 段跨屏 → `mid_alpha` **4938**、8 块、峰值 **3.8 MB**。

**回归（改后）**：`test_render_layers + test_render_geom + test_pan_reuse + test_seam_render + test_normal_tracking` → **59 passed in 21.9s**。
**机器状态**：headless dummy SDL；本轮全部为**计数/像素统计**，不含计时结论。

---

## 6. 观察到的问题 / 风险 / 不一致

| 级别 | 内容 | 证据 |
|---|---|---|
| **已更正** | ~~`pygame.draw.aaline` 在本机没有抗锯齿~~ → **错**：`aaline` **有** AA（30°/5°/1° 实测 mid=509/517/514、colors 最高 255）。我初版测出 0 的真正原因是**测试线用了端点对齐的 45°（每像素覆盖恰好 0/1，不存在部分覆盖像素）**。`paint_aa.aa_line(width<=1)` 仍然可用；P2/P3 只需注意"**别用 45°/轴对齐线判 AA**"与"**只在未铺底的 alpha 面上量中间 alpha**"。 | §3.4 角度扫描表 |
| **一般（R2，已按例外处理）** | 2px 细环叠两层软边会吃掉饱和度，导致 `test_pan_reuse.py::test_highlight_follows_selection` 失败。本轮**保留该处 aliased** 并写清替代方案。 | §3.3 四行定位表 |
| ~~一般（R1，未改）~~ **已修复（修订）** | `renderer.py:127` 海洋区边界（任意方向 2px）：初版判“不改”（`aa_line` 单段 ≈252 MB/帧），**现改用分块超采样（cap=1 M ⇒ 峰值 3.8 MB、AA 全覆盖，实测 mid_alpha=4938）**。 | §2.2 表 / §3.1 |
| **一般（R4）** | 细斜线的二值 IoU 不能单独作为几何判据（相位敏感）；本报告改用质量比/质心/bbox。**给后续 P2/P3 的建议：验收 AA 时别只看 IoU。** | §5 判据解读 |
| **建议** | `renderer._clip_seg_rect`（`:23-54`）是**死代码**（全仓零调用），里面还有一处 aliased `line` ⇒ 建议清理而不是给它加 AA。 | 全仓 grep |
| **建议** | `paint_aa.aa_poly / aa_rect / aa_line(w>1)` **没有走精灵缓存**（只有 `aa_disc/aa_ring` 有）；本任务对 `aa_poly` 的使用点（EX 三角）由 mixin 自己的缓存兜住，但 renderer 的薄区填充是每次调用新建超采样面 ⇒ 若将来该层默认打开，需要缓存或限量。 | 读 `paint_aa.py:130-176` |
| **不一致** | `_backup/SHA256_STATE_V4.txt`（10 个文件）与 `_backup/state_v4/` 都**不含本任务的三个文件** ⇒ 该"回退基线"对本任务无效，我用自建副本 + 自记 SHA256 兜底。 | §1 注 |

---

## 7. 值得保留的现状（**不要动**）

1. **`app/paint_aa.py` 的设计是对的**：4× 超采样 + `smoothscale`（盒式平均 = 真 alpha 渐变）+ **LRU 字节上限 6 MB**（避免"只按条数封顶"那种历史事故）+ 单张超上限就不进缓存（防止把整池冲掉）。本任务只调用、未改。
2. **路径的"地图空间 + 增量缓存"结构**：`_render_path_to_surface` 把路径画进 bbox 尺寸的**离线面**并按视图无关键缓存（平移不重建）—— 正因为有这层缓存，"整组超采样"才只需在重建时付一次代价。**这个结构不要为了 AA 改动。**
3. **`_draw_lines_safe` 的"按 `max_seg_len` 切段"防跨屏伪线**逻辑（`_max_seg_len`）—— AA 改造**原样保留**，它是防"跨 0°/360° 缝画出横贯全屏伪线"的关键。
4. **标记精灵缓存**（`_circle_cache` 512 / `_tri_marker_cache` 128 / `_rect_marker_cache` 128，均为 LRU）—— AA 只改了**精灵怎么造**，没改缓存纪律。
5. **轴对齐 UI rect 不 AA**（图例/toast/错误面板/统计面板/经纬网/吸附网格/十字准星）：整数像素边界是精确的，加 AA 只白付代价 —— 本轮共 20 处按此判定不改。
6. **`renderer.scene_layers` 的显式图层表**（`renderer.py:202-298`）—— 本轮没动渲染层序，AA 只在层内替换图元。
7. **拖拽/缩放期间的延迟失效节流**（`_ZOOM_BURST_MS`）—— 它把“超采样只在重建时发生”的频次压住；再加上**分块**把内存峰值钉在单块（15.3 MB），这两条一起构成“敢对全屏路径做 AA”的依据。

---

## 8. unresolved（未能确证）

1. ~~`renderer.py:127` 海洋区边界的 AA 未落地~~ → **已落地（分块，峰值 3.8 MB）**。仍未做的是“按视图 hash 缓存整层”那种更省的方案（当前每帧重新分块超采样，全屏块数 ≈8）；**全帧分配峰值未实测**（只在合成画布上量了单块）。
2. **全帧端到端未测**：本轮所有 AA 度量都是在合成画布上对**单个图元/单条折线**做的；没有驱动一次真实的 `draw_typhoon`（含 15 只台风）去量整帧的超采样分配峰值与 `paint_aa` 缓存峰值。
3. **`_AA_MAX_SS_PIXELS = 4 M SS px` 现在只是“单块上限”**（不再是“超了就丢 AA”）：全屏 1920×1080 切 4 块、超宽 2560×1540 切 6 块、峰值恒 15.3 MB。剩余未量化的是**真实台风路径的块数与每帧 draw call 数**（合成用例下已给块数）。
4. **`aa_ring` 在半径更大时的行为**（拖动指示环 r≥11）：只验证了 r=20/w=2 的度量（质量比 1.004、质心 0.000），没验证脉冲动画（`r + pulse`，pulse∈{1,2}）逐帧切换时的观感。
5. **`paint_aa.aa_poly` 无缓存**这一点在 renderer 薄区填充上的真实调用频次未测（要 `show_ocean_areas=True` 且存在薄区）。
6. **`typhoon_render.py` 的 0 处**结论只基于 `grep "pygame.draw."`；若将来有人在该文件里用 `surface.blit`/`surfarray` 直接写像素，本审计口径不覆盖。

---

## 9. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| AA 底座（只读） | `app/paint_aa.py`（`aa_disc/aa_ring/aa_poly/aa_rect/aa_line`、`cache_stats()`、`SS=4`、6 MB 上限） |
| 折线**分块**超采样（本任务核心） | `_draw_path_mixin.py` 的 `_draw_segments_aa`（`_AA_MAX_SS_PIXELS=4_000_000` = **单块**上限 / `_AA_SS=2` / `max_ss_pixels` 参数） |
| 折线三个入口 | 同文件 `_draw_dashed` / `_draw_lines_safe` / `_draw_lines_colored` |
| 标记精灵改 AA | 同文件 `_get_circle_marker`（`aa_disc`）/ `_get_tri_marker`（`aa_poly`） |
| **例外（保持 aliased）** | 同文件 `_blit_highlight` + `_get_circle_marker_aliased` + `_draw_edit_selection` 的选中/悬停环（注释里写了完整理由与替代方案） |
| renderer 的 AA 接入点 | `app/renderer.py` 薄区填充（`paint_aa.aa_poly`）+ 海洋边界（分块 `_draw_segments_aa`，cap=1 M） |
| 未改的轴对齐项 | `renderer.py` 经纬网 `:193/:197`、UI `rect` `:210/:211/:214/:339/:340/:368/:369/:448`；`_draw_path_mixin.py` 吸附网格 `:1366/:1374`、十字准星 `:1478` |
| 验收探针 | `G:/tmp/p1_aa_probe2.py`（逐图元度量+对比图）、`p1_tiled_verify.py`（★ 分块验收: 全屏/块数/峰值/接缝）、`p1_fullscreen_check.py`（★ 缺口复现）、`p1_sweep.py`（线宽/SS 扫描）、`p1_width2.py`（width 语义）、`p1_aaline_angle.py`（★ aaline 按角度复测）、`p1_aaline_recheck.py`（三度量×四底）、`p1_diag.py`（例外 R2 定位） |
| 对比图 | `output/exploration/p1_aa_before_after.png` |
| 回退 | `G:\tmp\p1_backup\{renderer.py,typhoon_render.py,_draw_path_mixin.py}` + §1 的改前 SHA256 |
