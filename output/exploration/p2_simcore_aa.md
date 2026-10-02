# P2 模拟模式渲染抗锯齿（`simulator/simcore/render.py`，21 处）

> 第四轮 · `sim-numerics` · **实施任务** · 写作用域：`simulator/simcore/render.py`、本报告（+ 任务点名的对比图 `p2_simcore_aa.png`）。
> 共享底座：`app/paint_aa.py`（**只读复用**，未改、未复制）。回退：`git show 9c3f657eef517f35e4517271a5b6b6471e1ae13e:simulator/simcore/render.py`（改前 sha 与它逐位相同，见 §4）。
> **实测** = 本机跑出来的数字；**推断** = 从代码读出。主判据是**确定性指标**（中间 alpha 像素数、ink 保留率、中心线位移、缓存条目/字节、调用次数），不报计时。

## 一句话定位

21 处 aliased 图元里 **17 处改成抗锯齿、4 处"有证据地不改"**（轴对齐经纬网 ×2、死码 `draw_tracks`、宽度 2 的椭圆眼墙）。
关键决策是**按"线宽"选技术**：1px 斜线/曲线走原生 `aaline/aalines`（**零分配、零缓存**），所以 `pressure` 层每帧 **26 862 段**等值线的 AA 代价是 **0 个缓存条目 / 0 字节**——派活单担心的"几千个缓存条目"只存在于 `width>1` 的精灵路径，而等值线是 1px。
顺带查出两条**既有缺陷**（都不在本次写域，已按纪律"保持原样 + 登记"）：① flow 层原用 colorkey 面，直接换 `aaline` 会产生**不透明暗边且无任何真 alpha**（实测 ink 261→504、mid_alpha 仍 0）⇒ 必须先改成 SRCALPHA；② `grade_kt(...)[2]` 取到的是**阈值(int) 而不是颜色**，台风图标一直是近黑色。

**物理确定性门**：`tools/free_hash.py 20 12345` = `2c30144786b0553a0b9bafa2c37c196b50ace7d4d502cb64be22f2c7436dcdb3` **与要求逐位相同**（渲染改动不碰物理）。

---

## 1. 清单与职责（21 处逐处判定）

| # | 行（改后） | 原图元 | 判定 | 改法 |
|---|---|---|---|---|
| 1 | `:840` | `draw.line` 气压等值线 ×26 862 | **要 AA** | `_aa_line(...,1)` → 原生 `aaline` |
| 2 | `:970` | `draw = pygame.draw.line` 风切等值线 | **要 AA** | 删除别名，`_aa_line(...,w)`（w=1 原生 / w=2 精灵） |
| 3 | `:1127` | `draw = pygame.draw.line` 流线 | **要 AA** | 删除别名，`_aa_line(...,1)`；面改 **SRCALPHA**（见 §3.4） |
| 4 | `:1267` | `draw.line` 风羽主干 | **要 AA** | `_aa_line(...,1)` |
| 5 | `:1276` | `draw.polygon(...,1)` 三角旗**描边** | **要 AA** | `pygame.draw.aalines(..., True, [...])`（描边≠填充，不能用 `aa_poly`） |
| 6 | `:1284` | `draw.line` 半羽 | **要 AA** | `_aa_line(...,1)` |
| 7 | `:1291` | `draw.line` 小羽 | **要 AA** | `_aa_line(...,1)` |
| 8 | `:1337` | `draw.line` 粒子尾迹 | **要 AA** | `_aa_line(...,1)` |
| 9 | `:1379` | `draw.lines(...,2)` 预报平均路径 | **要 AA** | 逐段 `_aa_line(...,2)`（≤13 段，见 §3.3） |
| 10 | `:1387` | `draw.ellipse(...,1)` 误差圈 | **要 AA** | `_aa_ellipse_outline(...,64)` → 参数化 `aalines` |
| 11 | `:1398` | `draw.circle(...,6,2)` 24h 空心标记 | **要 AA** | `_aa_ring(...,6,2)` |
| 12 | `:1400` | `draw.circle(...,4)` 24h 实心标记 | **要 AA** | `_aa_disc(...,4)` |
| 13 | `:1416` | `draw.lines(...,2/3)` 路径本体 | **不改**（**生产零调用**的死码，见 §3.3） | — |
| 14 | `:1453` | `draw.ellipse(...,2)` 眼墙 | **不改**（宽度 2 椭圆，本模块与底座都没有"粗椭圆 AA"原语，见 §3.3） | — |
| 15 | `:1465` | `draw.circle(...,5/7,0)` 图标底盘 | **要 AA** | `_aa_disc`（+`_as_rgb`，见 §3.5） |
| 16 | `:1466` | `draw.circle(...,5/7,1)` 图标外圈 | **要 AA** | `_aa_ring` |
| 17 | `:1484` | `draw.line` 虚线椭圆段 | **要 AA** | `_aa_line(...,1)` |
| 18 | `:1502` | `draw.lines(...,1)` 四象限弧 | **要 AA** | `pygame.draw.aalines(..., False, pts)` |
| 19 | `:1561` | `draw.circle(...,2)` 副高边界点 | **要 AA** | `_aa_disc` |
| 20 | `:1581` | `draw.line` 经纬网竖线 | **不改**：**轴对齐**，整数像素边界本来就精确 | — |
| 21 | `:1584` | `draw.line` 经纬网横线 | 同上 | — |

**合计：17 改 / 4 不改**（不改变处都写了理由，含 1 处死码）。

## 2. 数据流 / 调用链

```
app/ty_sim_mixins/sim_mode_mixin.py:1864  R.render_map(...)          ← 模拟模式唯一入口
  └─ render_map (本文件 :1616)  按 layers 分发
       ├─ 'pressure'  → draw_contours:804      → _aa_line ×26862/帧   ← 本轮最重的一处
       ├─ 'shear'     → draw_shear_contours:941
       ├─ 'flow'      → draw_streamlines:1140  → _build_streamlines:1075（SRCALPHA 面）
       ├─ 'current'   → draw_currents:1178     → 同一 _build_streamlines
       ├─ 'wind'      → draw_barbs:1213 / Particles.draw:1330
       ├─ 'ridge'     → draw_ridge:1511
       └─ 'track'     → draw_forecast:1340 / draw_tc_hud:1419
共享 AA 底座:  from app import paint_aa as _AA     （`:33-36`，fail-soft）
   _aa_line/_aa_disc/_aa_ring/_aa_ellipse_outline  （`:39-110`，含 `_AA_TRACE` 确定性计数）
```

**import 路径已确认可行**（派活单要求先确认）：
`app/__init__.py` 是 **0 行空文件** ⇒ `from app import paint_aa` 只载入 `paint_aa`（仅依赖 pygame），不会把 app 的其它模块拉进来。
实测三种顺序都通过：`app→render`、`render→app`、`app.ty_sim`（会连带 import 全部 mixin）后再 `render`。
仍做了 **fail-soft**（`_AA = None` 时退回 pygame 原生原语），保证 simcore 能独立运行 —— 这也保持了本模块原来"不依赖 app 层"的可独立测试性。

## 3. 核心机制详解

### 3.1 关键决策：为什么 1px 用原生 `aaline`，而不是逐段精灵/整画布超采样（回答派活单重点 ①）

**先证伪"几千个缓存条目"这个担心**：`app/paint_aa.py:153-176` 的 `aa_line` **按线宽分流** ——

```python
def aa_line(surf, color, p1, p2, width=1):
    if w <= 1:
        pygame.draw.aaline(surf, color, pt, qt); return     # ← 原生 AA，零分配零缓存
    ... _supersample(...)                                    # ← 只有 width>1 才走精灵
```

等值线是 **1px**（`:840` 原来的 `pygame.draw.line(..., 1)`），改后走的是 `aaline` 分支 ⇒ **缓存条目 0、分配 0**。
实测（`G:\tmp\aa_probe.py`，1280×800 全图视图、14 档等值线）：

| 方案 | 每帧调用 | `paint_aa` 缓存条目 | 缓存字节 | 额外分配 | 判定 |
|---|---|---|---|---|---|
| **A 原生 aaline（采用）** | 26 862 次 `aaline` | **0** | **0 B** | 0 | ✅ |
| B 逐段精灵（若线宽>1 才会走） | 26 862 次超采样 | 0（`aa_line(w>1)` 不缓存） | 0 B | **26 862 次** SS 画布 + `smoothscale`（每段 bbox×16 像素） | ❌ 分配量按段数线性爆炸 |
| C 整画布超采样 SS=2 | 1 次画布 + 1 次 `smoothscale` | 0 | — | **4.1 MB/帧**（1280×800×4×4；用户窗口 2560×1498 时 **61 MB/帧**） | ❌ 且 pressure 是**直接画在地图面**上的层，整画布超采样会把底图一起糊 |

⇒ **选 A**：对"几千条细线"这种场景，`aaline` 是平台原生的 Wu 反锯齿，**零缓存零分配**，比两个候选都优。
（`width>1` 的精灵路径确实会产生"每段一次超采样"，所以本轮**只对段数少的地方**用它：见 §3.3。）

### 3.2 抗锯齿度量（确定性，不是计时；口径已按 Lead 更正复核）

**度量口径（关键，先声明）**：每个图层都画在一张**新建、未铺底**的 `pygame.Surface((W,H), pygame.SRCALPHA)` 上，
再直接读 `pygame.surfarray.array_alpha` 数 `0 < a < 255`。
**没有先铺不透明底**——否则 `get_at()[3]` 恒为 255，"中间 alpha 像素数"必然 0（Lead 点名过这个陷阱）。
另按 Lead 的第二条判据补了**不同颜色数**（在 ink 内对 RGB 去重）作为**独立**佐证：
真 AA 会把"1 种纯色"变成几百上千种覆盖度；`draw.line` 永远是 1–2 种。

同一模拟状态、**种子化**（`np.random.seed(20261002)`，否则粒子层两次跑的位置不同 —— 见 §6 MINOR-2）、1280×800 全图：

| 层 | mid_alpha 改前 | mid_alpha 改后 | **颜色数 改前→改后** | ink 改前 → 改后 | `aa_*` 调用（后） |
|---|---|---|---|---|---|
| **pressure** | **0** | **82 931** | **1 → 1 569** | 41 815 → 82 938 | line 26 862 |
| shear | 5 473 | 135 316 | 45 → 2 684 | 91 539 → 154 167 | line 22 368 |
| **flow** | **0** | **25 651** | 642 → 11 660 | 12 831 → 25 652 | line 1 273 |
| **wind_barbs** | **0** | **39 740** | **1 → 1 096** | 18 633 → 39 763 | line 3 939 |
| ridge | 116 311 | 124 182 | 3 → 15 | 128 931 → 128 893 | disc 1 113 |
| tc_hud | 1 297 | 1 419 | 1 → 26 | 2 051 → 2 137 | ring 4 |
| forecast | 347 | 408 | 1 → 21 | 700 → 732 | line 12 / disc 4 / ell 13 |
| **particles** | **0** | **1 938** | 130 → 1 725 | 794 → 1 938 | line 194 |
| grid（**按设计不改**） | 18 446 | 18 446 | **1 → 1** | 18 446 → 18 446 | 0 |

- **两条独立判据一致**：四个原本完全无 AA 的层（pressure / flow / wind_barbs / particles）
  在"中间 alpha"上 0 → {82 931, 25 651, 39 740, 1 938}，在"颜色数"上 1→1 569 / 642→11 660 / 1→1 096 / 130→1 725。
- 之前就有 mid_alpha 的层（shear/ridge/tc_hud/forecast）来自**文字标签**（`_label` 的字体反锯齿），不是线。
- **grid 是关键反证**：`颜色数 1 → 1`、`mid 18 446 → 18 446`、质量比 **1.0000**、质心位移 **0.00**、bbox 差 0
  —— 它的 18 446 个"中间 alpha"像素是**常量 alpha=26**（颜色写死 `(255,255,255,26)`），不是 AA。
  这正好证明本文的度量能分辨"真 AA"与"本来就半透明"。
- 注：本探针用的全图视图下 pressure 有 **26 862 段/帧**（14 档全开、1280×800）。派活单引用的"~2800 段"是第三轮 `probe_layer_render` 在**另一种视图/档位**下的数字 —— 两者都是实测，差别来自视图与档位；**代价结构（逐段 aaline、零缓存）与段数无关**。

### 3.3 宽度 ≥2 的三处：逐个算代价再决定

| 处 | 段数（实测/上界） | 逐段精灵的代价 | 决定 |
|---|---|---|---|
| `:1379` forecast 平均路径 w=2 | **12 段**（6h 间隔到 72h，≤13） | 12 次超采样/帧；实测 `aa_line` 调用 12、缓存条目 **+0**、`paint_aa` 总缓存 **1 项 400 B** | ✅ 改（段数少） |
| `:1453` 眼墙椭圆 w=2 | 1–2 个/台风 | 参数化 64 段 × 精灵 = 64–128 次超采样/台风 | ❌ **不改**：`paint_aa` 只有 `aa_ring`（正圆）没有粗椭圆；逐段精灵代价 > 该层价值。**正确做法是给共享底座加 `aa_ellipse_ring`**（不在本任务写域） |
| `:1416` `draw_tracks` w=2/3 | 多台风时 87×40≈3 480 段 | 3 480 次超采样/帧 | ❌ **不改**：该函数**生产零调用**（第一轮全仓调用点普查；`render_map` 的 'track' 分支只画预报圈，路径本体由 `app/ty_sim_mixins/_draw_path_mixin` 画，见 `:1612-1614` 的注释）。死码 + 高代价 ⇒ 不动 |

### 3.4 本轮最有价值的发现：flow 层的 **colorkey 陷阱**

`_build_streamlines` 原来返回的是**不透明面 + `fill(_FLOW_KEY)` + `set_colorkey(_FLOW_KEY)`**（给 `draw.line` 用的 colorkey 快速 blit 路径）。
把 `draw.line` 直接换成 `aaline` 会**踩坑**——用一个 20 行的三方案微探针（`G:\tmp\flow_aa_test.py`，300×300 面、一条斜线）实测：

| 方案 | 源面"非 key 色"像素 | blit 后 ink | blit 后 mid_alpha | 结论 |
|---|---|---|---|---|
| A 旧：`draw.line` + colorkey | 261 | 261 | 0 | 基线（锯齿但干净） |
| B 我第一版：`aaline` + colorkey | **504** | **504** | **0** | ❌ **暗边**：aaline 把线色与 key 色混成**不透明**中间色，像素 ≠ key ⇒ 被 blit 出去，**且没有任何真 alpha** |
| C `SRCALPHA` + aaline | — | 504 | **504** | ✅ 真 AA（边缘是部分 alpha） |
| D `SRCALPHA` + aaline + `set_colorkey` | — | 504 | **504** | ✅ 与 C **逐位相同**（SRCALPHA 背景是 (0,0,0,0)，没有像素等于 key ⇒ **key 惰性**） |

**采用 D**：既拿到真 AA，又保留返回面的 `get_colorkey()` 不为 None —— 于是
`tests/test_simcore_ocean.py:199` 的既有断言与缓存/节流语义**都不用改**（我第一版用 C，该测试报 `AssertionError: unexpectedly None`；D 让它 `10 passed`）。
`ink` 从 12 831 → 25 652（≈2×）是 AA 的正常代价：每条 1px 线会把覆盖率摊到相邻像素上。

### 3.5 顺带发现的**既有 bug**（保持原样 + 登记）

`draw_tc_hud:1465` 原来写 `pygame.draw.circle(surface, g[2], ...)`，其中 `g = grade_kt(v_kt)`。
但 `grade_kt:194-200` 返回的是 `_CMA_KT` 的**整条 4 元组** `(中文, 英文, 阈值, 颜色)`（`:164-175`），
而它的 docstring 写的是"返回 (中文名, 英文, 颜色)" ⇒ **`[2]` 是阈值(int)、`[3]` 才是颜色**。
`pygame.draw.circle` 把 int 当 **packed 0xRRGGBB** 用 ⇒ 图标一直画成**近黑色**（阈值 0/21/34/64/83/96/113/137）。
`draw_tracks:1416`（死码）有同样写法。

**本次的处置**：新增 `_as_rgb()`（`:38-55`）按 pygame 的同一语义把 int 归一成 RGB 元组，**逐像素保持改前外观**（对比图里图标仍是深色）——
**不趁机改颜色**（那会改变渲染外观、超出"抗锯齿"的授权范围，也会打乱其他人正在做的像素对比）。
建议 Lead 决定是否单独修成 `g[3]`（1 行 + 一处死码）。

### 3.6 确定性代价（派活单要求"若超预算要如实说"）

`paint_aa.cache_stats()`（上限 6 MB / LRU）在全部 9 个图层画完后的**峰值**：

| 量 | 实测 |
|---|---|
| 缓存条目 | **4**（tc_hud 的 disc/ring 各 2 个半径） |
| 缓存字节 | **3 680 B** = 预算的 **0.06 %** |
| 未进缓存的分配 | forecast 的 12 次 `aa_line(w=2)` 超采样（每次一小块 bbox，随即释放） |
| `_aa_*` 调用合计 | line 54 630 / disc 1 121 / ring 4 / ell 13（全图视图、9 层各一帧） |

⇒ **远未超预算**；`paint_aa` 的字节-LRU 只在半径/颜色组合极多时才会起作用（本模块的半径取值是固定的 2/4/5/6/7）。

## 4. 输入输出资产

| 路径 | 角色 |
|---|---|
| `simulator/simcore/render.py` | **唯一被改的源码**；sha256 `8a6f27bf…` → **`94e3a9cc44e15778c27c1f09ef429826965220dcfc54fea4acebf8eacb0fe5a9`** |
| `app/paint_aa.py` | 共享底座，**未改**（只读 import） |
| 改前源码（回退点） | `git show 9c3f657eef517f35e4517271a5b6b6471e1ae13e:simulator/simcore/render.py` ⇒ sha256 `8a6f27bf…`（**实测与改前工作树逐位相同**，已用它跑了种子化 before 基线） |
| `output/exploration/p2_simcore_aa.png` | **对比图**（1104×3342）：上排 pressure 全图前/后，下面 5 组 ×5 局部放大（pressure / shear / wind_barbs / tc_hud / particles） |
| `G:\tmp\aa_probe.py`、`aa_compare.py`、`flow_aa_test.py` | 验收探针（工作区外） |
| `G:\tmp\aa_before2\`、`aa_after2\`、`aa_geom.json`、`aa_before2.json`、`aa_after2.json` | 原始产物（种子化，可复核） |

## 5. 如何运行 / 如何验证

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe

# ① 抗锯齿度量 + 逐层图（种子化）
timeout 300 "$P" 'G:\tmp\aa_probe.py' before2     # 需先把 render.py 换成 git 里那一版
timeout 300 "$P" 'G:\tmp\aa_probe.py' after2
# ② 几何等价 + 拼对比图
timeout 200 "$P" 'G:\tmp\aa_compare.py'           # 写 output/exploration/p2_simcore_aa.png
# ③ flow 层 colorkey 方案判定（A/B/C/D 四方案）
timeout 120 "$P" 'G:\tmp\flow_aa_test.py'
# ④ 物理确定性门
timeout 600 "$P" tools/free_hash.py 20 12345
# ⑤ 回归（单文件 timeout 300；test_sim_mode_ref 基线 ~197s, 给 600）
timeout 300 "$P" -m pytest tests/test_simcore_contours.py tests/test_simcore_ocean.py \
    tests/test_simcore_batch.py tests/test_render_layers.py tests/test_seam_render.py -q --no-header
timeout 600 "$P" -m pytest tests/test_sim_mode_ref.py -q --no-header
```

### 5.1 验收 ① 抗锯齿

见 §3.2 表：四个原本**完全无 AA**的层 `mid_alpha 0 → {82 931, 39 740, 25 651, 1 938}`；
按设计不改的 `grid` 逐位不变。**对比图**：`output/exploration/p2_simcore_aa.png` ——
局部放大 ×5 下，改前是明显的**阶梯/锯齿**（pressure 的等值线、shear 的曲线、barbs 的三角旗），改后是平滑过渡。

### 5.2 验收 ② 几何等价（种子化改前/改后）

| 层 | 旧 ink 保留率 | 中心线位移 mean | p95 | 严格 IoU(alpha>0) |
|---|---|---|---|---|
| pressure | **95.2 %** | 0.05 px | 0.00 | 0.4688 |
| shear | 94.6 % | 0.05 px | 1.00 | 0.5441 |
| flow | 88.7 % | 0.11 px | 1.00 | 0.4202 |
| wind_barbs | 91.2 % | 0.09 px | 1.00 | 0.4105 |
| ridge | 99.5 % | 0.00 px | 0.00 | 0.9906 |
| tc_hud | 99.8 % | 0.00 px | 0.00 | 0.9078 |
| forecast | **100.0 %** | 0.00 px | 0.00 | 0.9563 |
| particles | 89.2 % | 0.11 px | 1.00 | 0.3498 |
| grid | 100.0 % | 0.00 px | 0.00 | **1.0000** |

**读法（重要）**：细线层的"严格 IoU"只有 0.35–0.54，**这不是几何漂移**，而是 AA 的固有定义 ——
把 1px 硬边换成"核心 + 半透明边"会把 ink 集合**撑大一倍**（见 §3.2 的 ink 列），集合 IoU 必然被稀释。
真正衡量几何的指标是这两列：
**旧 ink 保留率 88.7–100 %**（改后仍覆盖住绝大多数原来的线）与 **中心线位移 mean ≤ 0.11 px / p95 ≤ 1 px**（亚像素级）。
点/圆类（ridge/tc_hud/forecast）连严格 IoU 都在 0.91–1.00。

### 5.3 验收 ③ 确定性代价与回归

| 项 | 结果 |
|---|---|
| `paint_aa.cache_stats()` 峰值 | **4 项 / 3 680 B**（预算 6 MB 的 0.06 %）✅ 未超预算 |
| 物理确定性门 `free_hash 20 12345` | `2c30144786b0553a0b9bafa2c37c196b50ace7d4d502cb64be22f2c7436dcdb3` ✅ **与要求逐位相同** |
| `tests/test_simcore_contours.py` | 5 passed |
| `tests/test_simcore_ocean.py` | **10 passed**（含 `test_draws_and_caches` 的 colorkey 断言，见 §3.4） |
| `tests/test_simcore_batch.py` | 11 passed |
| `tests/test_render_layers.py` | 9 passed |
| `tests/test_seam_render.py` | 8 passed |
| 上述 5 个文件合计（最终版一次跑） | **43 passed in 24.22s** |
| `tests/test_sim_mode_ref.py`（内核渲染重头戏） | **26 passed in 173.99s**（既有基线 26 passed / 197.66 s） |

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜flow 层的 colorkey 会吃掉抗锯齿（已在本轮内解决，但值得全仓警惕）
- **现象/证据**：见 §3.4 的 A/B/C/D 表。直接 `aaline` + colorkey = **不透明暗边 + mid_alpha 仍 0**（比不改还糟）。
- **根因**：`aaline` 写的是"线色 × 覆盖率 + 背景色 ×(1−覆盖率)"，**背景是 key 色**时得到的是不透明中间色。
- **影响面**：任何"不透明面 + colorkey + 想要 AA"的写法都会踩。`grep set_colorkey` 在 `app/` 里还有别的使用者，建议 P1/P3 的负责人先查同样的组合。
- **已处置**：本模块改成 `SRCALPHA + aaline`（colorkey 保留但惰性）。

### MAJOR-2｜`grade_kt(...)[2]` 是阈值不是颜色 ⇒ 图标一直是近黑色（**既有 bug，未改**）
- **证据**：`_CMA_KT:180-191` 是 4 元组；`grade_kt:194-200` 返回整条；`draw_tc_hud:1465` 用 `[2]`（阈值）当颜色。
- **处置**：本次只加 `_as_rgb()` 保持**逐像素原样**（不趁机改外观）。建议单独修成 `g[3]`（同时 `draw_tracks:1416` 死码处一并修）。
- **判定依据**：改颜色会改变外观、影响其它分区的像素对比 ⇒ 不属于"抗锯齿"授权范围。

### MINOR-1｜`draw_forecast` 里 `rxp/ryp` 的口径（既有，未改）
`:1383-1385` 把"度"直接当"像素"用（`rxp = rkm/111.32/cos_lat` 后直接塞进 `pygame.Rect`）。只有在 `fx` 恰好是"1°=1px"的全图回退视图下才等价。本轮**逐位沿用同一几何**（不改口径），只用参数化折线替换了同样的椭圆。

### MINOR-2｜粒子层做 A/B 必须固定随机种子（探针纪律）
`Particles.__init__` 用 `np.random.uniform` 撒点 ⇒ 两次运行的位置不同。
我第一版探针没固定种子，测出 `particles` 的 IoU=0.0000（**假阳性**）；加 `np.random.seed(20261002)` 后是 0.3498 / 保留率 89.2 %。
⇒ 本报告所有几何数字都来自**种子化**的 `before2/after2` 两次运行。

### MINOR-3｜`_FLOW_KEY` 现在名不副实
它的值仍被 `set_colorkey` 用着（惰性），但"colorkey 魔法色"的注释已不准确。本轮在 `:1049` 与调用处都补了说明，没有删常量（删它会动到 `set_colorkey` 这行、进而动到测试断言）。

### 值得保留的现状（别回退）
1. `_contour_segments` 的**几何缓存**（只缓存"格坐标线段"，屏幕映射每帧做）—— 与 AA 正交，本轮一行未动；
2. `_flow_cache` / `_flow_throttle_get/put`（按图层名分开的 150 ms 节流）—— 本轮只改了**面的格式**，缓存语义未动；
3. `_label` 的**字体表面缓存**（32/64 项）—— 它本来就是 AA 的（mid_alpha 来源之一）；
4. **轴对齐不 AA** 的判断（经纬网）—— 这是本轮"逐处判定"的直接结论，别被"全仓 AA"的运动式改法覆盖；
5. `app/paint_aa.py` 的**按线宽分流**（1px→`aaline`/更粗→精灵）与字节-LRU —— 本轮的代价结论完全建立在它上面。

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U-1 | **眼墙椭圆（宽 2）**的 AA | 需要一个"粗椭圆 AA"原语：建议给 `app/paint_aa.py` 加 `aa_ellipse_ring(surf,color,center,rx,ry,width)`（超采样精灵缓存）。它在 `app/` 下 ⇒ **不在本任务写域**，我没有实现也没有复制 |
| U-2 | 生产路径下（用户 2560×1498 窗口、默认图层组合）的每帧 `aa_*` 调用量与 `paint_aa` 峰值占用 | 我用 1280×800 全图视图测；用户窗口面积是它的 3.7× ⇒ 调用量≈段数随视图变化。要给出**生产数字**需要一次真实 `TySim` 模拟模式跑（本轮未做，属内存纪律 + 时间预算） |
| U-3 | `SRCALPHA` 的逐像素 alpha blit 相对 colorkey blit 的**固定开销** | 本机计时不可信（P/E 核差 1.90×），我没报。结构上它是**每帧一次整屏 blit**（该层本来也是整屏 blit），只差混合模式 |
| U-4 | `draw_streamlines` 的 `flow` 层在 SRCALPHA 下与 `_flow_throttle` 的 150 ms 复用是否完全等价 | 测试覆盖了"缓存 1 项 + colorkey 非 None"（`test_draws_and_caches`），但**没有**覆盖"节流复用那一帧的画面"。需要一次连续帧的逐位对比（改前/改后节流帧） |

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| AA 底座 import + helper + 计数器 | `simulator/simcore/render.py:33-110`（`try: from app import paint_aa` / `_aa_line` / `_aa_disc` / `_aa_ring` / `_aa_ellipse_outline` / `_as_rgb`） |
| **最重的一处**：气压等值线 | `:804-841`（`_aa_line` 在 `:840`） |
| 风切等值线 | `:941-973`（`_aa_line` 在 `:970`） |
| flow 层（SRCALPHA + 惰性 colorkey） | `:1075-1137`（面构造 `:1093`、`_aa_line` `:1127`、`set_colorkey` `:1136`） |
| 风羽（主干/三角旗/半羽/小羽） | `:1267` / `:1276` / `:1284` / `:1291` |
| 粒子 | `:1330-1338`（`_aa_line` `:1337`） |
| 预报路径与误差圈 | `:1379`（逐段 2px）/ `:1387`（参数化椭圆）/ `:1398`、`:1400`（24h 标记） |
| 眼墙（**不改**，含理由） | `:1449-1456` |
| 图标底盘/外圈 + `_as_rgb` 的 bug 说明 | `:1465-1466`、`:44-58` |
| 虚线椭圆 / 四象限弧 | `:1484` / `:1502` |
| 副高边界点 | `:1561` |
| 经纬网（**不改**，含理由） | `:1576-1585` |
| 死码 `draw_tracks`（**不改**，含理由） | `:1404-1418` |
| 共享底座（只读） | `app/paint_aa.py:153-176`（`aa_line` 按线宽分流）、`:54-78`（字节-LRU 与 `cache_stats`） |
| 验收探针 / 产物 | `G:\tmp\aa_probe.py`、`aa_compare.py`、`flow_aa_test.py`、`aa_before2/`、`aa_after2/`、`aa_geom.json` |
| 对比图 | `output/exploration/p2_simcore_aa.png` |
| 回退 | `git show 9c3f657eef517f35e4517271a5b6b6471e1ae13e:simulator/simcore/render.py > simulator/simcore/render.py` |
