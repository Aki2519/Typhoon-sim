# P3 绘画模式：抗锯齿接入（H1）+ Typhoon4 保真度对账

> 执行者：`app-feat`（task-30）　写作用域：`app/paint_render.py`（唯一改动文件）+ 本报告
> 权威规格：`output/sim/_reference/TY4_SPEC.md`（623 行）
> 探针：`G:\tmp\probe_paint_aa.py`（原语）、`probe_paint_aa_render.py`（端到端 AA）、`probe_ty4_cmp.py`（对基准图）
> 全部判据为**确定性指标**（边缘像素数 / 直方图 / 尺寸 / 调用次数），未用计时。

## 一句话定位

把绘画模式里"肉眼可见的像素化定点"从根上修掉（全部图元改走共享抗锯齿底座 `app/paint_aa`），并以 Ty4 规格书为清单，把我们的实现逐条对账、把差异量化到像素。

## 1. 清单与职责

| 文件 | 改动 | 改前 sha256 | 改后 sha256 |
|---|---|---|---|
| `app/paint_render.py` | ① `_draw_shape` 三种点型改走 `paint_aa`；② 点半径**不再取整**（浮点）；③ 风圈改 `aa_ring`；④ 图例色点改 `aa_disc`；⑤ 线宽/点径按 §7.1 分成 `deg`/`px` 两分支（接上原死参数）；⑥ 删死参数 `multireset`；⑦ 新增 `projection` 透传 + P4 接线钩子（缺 P4 时回落等距圆柱 + 一次 warning） | `fb96d1599692e3b14797bc643d0ede56520c391209dbf23304c865673b1b05d7` | `41177e4e962d451817d41aec65c93ee48c565484d560fbc631869d4dd604ae44` |

> 改动只落在 `app/paint_render.py`（+`git status` 复核：`app/paint_aa.py` 是 Lead 的新文件、`app/constants/colors.py` 的 `M` 是 **Lead 自己加的注释**，两者我都未动）。

**回退**：`app/paint_render.py` 的 HEAD 版**恰好等于**我的改前版（实测 `git show HEAD:app/paint_render.py | sha256sum` = `fb96d159…`）
⇒ 一条命令即可回退，**不需要**依赖 `_backup/`（该文件也不在 `_backup/state_v4/` 里）：
```bash
git checkout HEAD -- app/paint_render.py
```

## 2. 数据流 / 调用链（本轮改动的落点）

```
PaintDialog._do_render                                  app/paint_dialog.py:635-657
  └─ TrackMapRenderer(opts).render(tys, mode, region, map_surface)
       ├─ resolve_bounds(...)                  margin_w/margin_h —— ⚠ **单位是"度"**（见 §4 F3）
       ├─ _auto_output_size(bounds)            画布 = 按"跨度纵横比 + min_w/min_h"导出（见 §4 F1）
       ├─ self._k = inner.w / span_lon         ← **px/° 由画布反推**（Ty4 恒为 30）
       ├─ _draw_tracks → _draw_ty_path         line_w = _px_track()  ← 本轮：deg/px 双分支
       │                              ↘ 全部段 aa_line（本轮；原「长段回落」已撤销，见 §3.4）
       │                 _draw_ty_points       r = _px_point_radius()（**浮点，不取整**，本轮）
       │                     ├─ _draw_radii    aa_ring（本轮）
       │                     └─ _draw_shape    aa_disc / aa_rect / aa_poly（本轮）
       ├─ _draw_legend                         色点 aa_disc（本轮）
       └─ _draw_labels
```

## 3. A. 抗锯齿（H1，本轮硬性要求）

### 3.1 改了什么（逐处）

| 位置 | 改前 | 改后 |
|---|---|---|
| `_draw_shape` 圆 | `pygame.draw.circle(surf,color,(cx,cy),r)` | `paint_aa.aa_disc(surf,color,(cx,cy),r)` |
| `_draw_shape` 方 | `pygame.draw.rect(...,(cx-r,cy-r,2r,2r))` | `paint_aa.aa_rect(surf,color,cx,cy,r)` |
| `_draw_shape` 三角 | `pygame.draw.polygon(surf,color,pts3)` | `paint_aa.aa_poly(surf,color,pts3)` |
| 点半径 | `r = max(2, int(round(point_deg*k*0.5)))`（**取整**） | `r = self._px_point_radius()`（**浮点**） |
| 非正式报小点 | `max(1, r // 2)` | `max(1.0, r * 0.5)` |
| 风圈 | `pygame.draw.circle(line_surf,ring_color,c,R,1)` | `paint_aa.aa_ring(line_surf,ring_color,c,R,1)` |
| 图例色点 | `pygame.draw.circle(panel,color,...,8)` | `paint_aa.aa_disc(panel,color,...,8)` |
| 轨迹线 | `pygame.draw.line(...,line_w)` | **全部段** `paint_aa.aa_line`（见 §3.4 修订） |

### 3.2 原语级实测（`probe_paint_aa.py`，透明面上 r=6.0）

| 图元 | 中间 alpha 像素（改前 → 改后） | 实心像素（改前 → 改后） |
|---|---|---|
| circle | **0 → 28** | 104 → 96 |
| square | **0 → 25** | 144 → 144 |
| triangle | **0 → 35** | 64 → 40 |

⇒ 三种点型的边缘都从"完全无中间覆盖"变成有真正的 alpha 渐变（判据：`0 < a < 255` 的像素数）。

### 3.3 端到端实测（`probe_paint_aa_render.py`，4 台风 / 60 报点 / 958×500）

⚠ 注意度量口径：真实渲染**会铺不透明底色**，所以画布上不存在 alpha 中间值（alpha 合成到不透明底 ⇒ 恒 255）。
端到端改用**颜色混合像素**度量（既不是底色 `(200,220,240)`、也不是调色板/白等纯色的像素 = 抗锯齿边缘）。
为此把 `grid/draw_rad/legend` 全关，确保混合像素只来自轨迹线与点。

| 指标 | 改前（HEAD 版） | 改后 | 说明 |
|---|---|---|---|
| **边缘混合色像素** | **0** | **2124** | AA 生效的直接证据 |
| `pygame.draw.line` 调用（`_draw_ty_path` 内） | 56（= 全部线段） | **0** | 本数据集全部线段都进了 `aa_line` |
| `paint_aa` 缓存 | —（未使用） | `items=3, bytes=768 / 6 MiB` | 缓存有界 |

### 3.4 长度上限（`_AA_LINE_MAX_PX=96`）—— **已撤销**

`paint_aa.aa_line` 在 `width>1` 时走"包围盒超采样精灵"，而精灵尺寸 ∝ 线段长度（`_supersample` 会分配 `w×4 × h×4`）。
一条长跳段（数据缺口 / 跨洋）会退化成数千像素的 4× 画布 —— 例如 1000 px 段 ⇒ `4000×4000×4 = 64 MB`。
**当时**（底座还没有分块守卫）我在 `paint_render.py` 加了 `_AA_LINE_MAX_PX = 96`：≤96 px 的段走 `aa_line`（精灵 ≤ ~400² ≈ 0.6 MB），
**更长的段沿用 aliased 直绘**。实测本数据集回退次数 = **0**（全部 56 段都 ≤96 px）。
**当时这是对共享底座的一个健壮性提醒**（`paint_aa.aa_line` 自身没有尺寸守卫）—— 建议 Lead 在 P1/P2 复用时一并加，
或在 `paint_aa` 内部加同样的上限（**那是 Lead 的文件，我没动**）。
**为什么现在删掉**：底座有了 `_AA_LINE_CHUNK_PX` 分块（实测单次分配 64.7 MB → 665 KB / 97×，接缝 `zero_alpha_gaps: 0`）后，这个上限**从保护变成了有害**：>96 px 的长段（全屏级路径/长跳段）会**彻底失去 AA**。实测（长跳段专用探针，7500 px 段）：中段带混合色像素 **0 → 12547**。两个真相源会随底座改动悄悄失配 ⇒ 删除。

### 3.5 我**有证据地不改**的三处（避免白付代价）

| 位置 | 为什么不改 |
|---|---|
| `_draw_grid` 的经纬线（`:405`/`:411`） | **轴对齐**直线：整数像素边界本身就是精确的，加 AA 只有代价没有收益（BRIEF4 §1 明确要求逐处判断，不要机械替换）。而且规格书 §0-4 说网格在底图里、程序本不该画（见 §4 F7）。 |
| 图例面板底框（`:608`/`:609`） | 轴对齐 `rect`（带 `border_radius`，圆角由 pygame 内部处理），同上。 |
| ~~轨迹**长段**直绘~~ | **已撤销**：`paint_aa` 内部分块后不再需要调用侧上限（§3.4）。 |

## 4. B. Typhoon4 保真度逐条对账表

图例：✅一致 ／ ❌差异 ／ ⚠证据不足 **不改** ／ 🆕本轮已修

| # | 规格书条目（出处） | 我们的实现 | 差异 | 状态 |
|---|---|---|---|---|
| B1 | 底图 `lon 0..360 / lat 90..−90 / 30 px/°`；`x=lon*30`、`y=(90−lat)*30`（§0-1） | 等距圆柱，但 **px/° 由画布反推**：`k = inner.w / span_lon`（`paint_render.py:299`） | **实测 Megi：我们 66.25 px/° vs Ty4 30** ⇒ 我们的地图比例随台风 bbox 变化 | ❌ 结构差异（见 §5） |
| B2 | 画布 = `max(最小宽, bbox宽px + 2×半宽留空) × max(最小高, bbox高px + 2×半高留空)`；**底图 1:1 裁剪**；轨迹严格居中（§0-2） | `_auto_output_size`：`aspect=跨度比`；`tw=min_w`，`th=tw/aspect`，`th<min_h` 则抬到 `min_h` 反推 `tw`（`:333-343`）——**完全不用 bbox 像素** | **实测 Megi：我们 1981×1080 vs Ty4 1920×1080**（+61 px）；居中我们更好（-7,+5 vs 基准受图例干扰 +20.5,+117） | ❌ 主要差异 |
| B3 | `WidthSpace/HeightSpace` 是**像素**（`tw + 2*HALF_W`） | `margin_w/margin_h` 在 `deg` 模式里被当作**度**（`resolve_bounds` 直接 `lon_min -= margin_w`）；只有 `coord_unit=="px"` 才当像素用 | 语义分叉：用户照 Ty4 填 `360/270` 在 deg 模式下会得到 ±360° 的巨大留空 | ❌ 主要差异（见 §6 建议） |
| B4 | 网格/经纬度标签/水印/红框/黄线**全在底图里，程序不画**（§0-4） | `grid` 选项默认 `False`；`labels` 默认 `False` ⇒ 默认不画 ✅。但**若用户把 `grid` 打开、且用 Ty4 底图**就会重复画 | 默认一致；开启时重复 | ✅（默认）/ ⚠（开启时） |
| B5 | **预报点=上三角、历史点=圆；预报段连线白色、历史段连线=起点等级色**（§0-5、§12.1） | 全部线段**白色**；形状按 `st` 分（SS/SD 方、EX 系三角、其余圆），**无 forecast 判定** | **数据模型缺口**：`TrackPoint` 没有 tau/forecast 字段（`typhoon.py:22-37`）⇒ **本文件内无法实现** | ❌ 无法实现（缺口在数据层） |
| B6 | 强度分级 `TD<34 / TS[34,64) / C1[64,83) / C2[83,96) / C3[96,113) / C4[113,137) / C5≥137`（§4.1） | `scheme_color` + `_eff_category`（走 `utils.infer_strength_category` 再归一化 `C2-→C2 / C3-→C3 / C4-ST→C4 / STS→TS`） | 需逐风速对账（见 §4.1 探针建议）；归一化后的类目**应对齐** Ty4 | ⚠ 待对账（本轮未跑逐风速） |
| B7 | 2023 色阶七色（§4.2 像素实测） | `WIKI_SCALE` | **逐字节相同**（TD 110,193,234 / TS 77,255,255 / C1 255,255,217 / C2 255,217,140 / C3 255,158,89 / C4 255,115,138 / C5 141,117,230）；且基准图直方图 7 色全命中（§5） | ✅ **一致** |
| B8 | `NewColor` 在 Ty4 里**切换无效**（`PALETTE_CLASSIC = dict(PALETTE_2023)`，§4.3） | `wiki` 与 `2023` 逐像素相同 | **这是忠实复刻，不是缺陷**（Lead 已核实并改口；规格书明令"不要编造经典色阶 RGB"） | ✅ **与 Ty4 一致，无需修改**（`colors.py` 未动） |
| B9 | 颜色取**线段起点**等级（§4.4） | 我们的线段是纯白，未使用等级色 | 同 B5（历史段着色与 forecast 判定是同一处缺口） | ❌ 未实现 |
| B10 | 线宽/点径：`px_track=round(Track×px_per_deg)`、`px_point=Point×px_per_deg/2`；实测线宽 **2 px**、点 ⌀**11**（§7.1-7.2） | `track_deg×k` / `point_deg×k/2`（`k` 是反推值） | **实测 Megi：我们线宽 3 px（0.05×66.25）、点半径 13.25 px（⌀26.5）** vs Ty4 2 px / ⌀11 ⇒ **放大 2.21×**（B1 的连带后果） | ❌ 主要差异（公式方向一致、量纲基准不同） |
| B11 | 风圈：3 档半径（r34/r50/r64）各画 45° **扇形多边形**、灰色 (232,232,232)、alpha 90/60/40、**画在线之前**（§8） | 单档**圆环**、黑色 `(0,0,0,120)`、半径 `max(4, rad34+point_size)`、数据缺 `r34` 时用**假值** `wind×0.5` | 形状/档数/颜色/透明度/半径来源全不同；且本机数据无 RAD 行 ⇒ 视觉影响有限 | ❌ 差异（数据也无 RAD 行） |
| B12 | 图例：行距 `27×H/1080`、圆点 `11×H/1080`、只列出现过的等级、右对齐、`x=W-pad-dot`、`y=H-row*n-pad`（§5、§14） | `_draw_legend`：`item_h=26` 固定、`entries` 默认**总是 7 条**、色点半径 **8** 固定 | 未随画布高度缩放；未按"出现过的等级"过滤；条目高/点径与公式不同 | ❌ 差异（本轮只把色点改成 AA） |
| B13 | 绘画顺序 `Priority`：**点统一置顶**（=2）时"先画全部线、再画全部点"（§6、§14） | 默认 `draw_order="simult"` ⇒ **逐台风** path+points 交替 ⇒ 后一条台风的线会压住前一条的点 | 多台风时与 Ty4 不同；我们的 `points_top` 选项才是 Ty4 语义 | ❌ 默认值差异 |
| B14 | 输出为 **RGB PNG 无 alpha**（§11.2 末） | `pygame.Surface(..., SRCALPHA)` | 透明通道多出来；PNG 保存时会有 alpha | ⚠ 次要差异 |
| B15 | `SkipInvest`（§11.2）：过滤 `is_invest` 点，**过滤后为空则保留原样** | `skip_invest` = 跳过 `DB/LO/WV/MD/INV` | 语义接近；"为空则保留原样"未实现 | ⚠ 次要差异 |
| B16 | `AllPoints=0 ⇒ 只画线不画点`（§11.2） | `all_points=False` ⇒ `pool = pts[-1:]`（只画**最后一个点**，不是"不画点"） | 语义不同 | ❌ 差异 |
| B17 | `ShowText`：预报点 `P+%dh\n%dkt`、历史点 `%02d%02d%02dZ`、位置 `(x+4,y−16)`、字号 `max(10, px_per_deg*0.55)`（§11.2） | `labels` 选项自己的一套 | 未对账文案/位置/字号 | ⚠ 未核 |
| B18 | 抗锯齿（用户 H1；Ty4 原程序用 PIL，亦无 AA） | pygame 基础图元（完全无 AA） | — | 🆕 **本轮已修**（§3） |
| B19 | 死参数：`path_width`/`point_size` 赋值后从不读取；`multireset` 无实现（我第一轮的发现） | — | — | 🆕 **本轮已处理**（§6） |

### 4.1 待补的一条对账（诚实标注）

B6（强度阈值）我**没有**跑逐风速对账（本轮时间/上下文用在了 A 与基准图对比上）。
建议的确定性做法（一条探针即可）：对 `w=0..200` 逐值比较 `_eff_category({'w':w,'st':''})` 与规格书 §4.1 的 `category()`，
输出第一处不一致的风速 —— 这能在 5 秒内判死，留给下一轮或 P6 复核。

## 5. 与基准图的可量化对比（Megi，同一份数据）

数据：`G:\气象\数据\自评\杂评\201017 鲇鱼 Megi wp152010.dat`（56 报点，lon 117.2–147.1 / lat 11.8–28.1）
基准图：`megi.png`（仓库根，Ty4 实测输出）

| 指标 | Ty4 基准图 | 我们（等距圆柱，min 1920×1080，margin=0） | 差异 |
|---|---|---|---|
| 画布尺寸 | **1920×1080** | **1981×1080** | 宽 **+61 px** |
| px/° | **30**（§0-1 确证） | **66.25** | **2.21×** |
| Ty4 公式预期 | `W=max(1920, 897+720)=1920`、`H=max(1080, 489+540)=1080` | — | 我们未按此公式 |
| 调色板直方图 | TD1171 TS1256 C1:1615 C2:1443 C3:1061 C4:690 C5:977 | TD:2148 TS:4823 C1:5578 C2:4998 C3:3118 C4:1164 C5:2640 | 纯色像素约 **3×**（点径/线宽放大的连带后果） |
| 七色是否命中 | 7/7 | 7/7（同一组 RGB） | **色阶完全一致** ✅ |
| ink 居中偏移 | (+20.5, +117.0)（受右下**图例**干扰，非纯轨迹） | (-7.0, +5.0)（我们同样含图例） | 我们更居中 |

**参考图侧的独立复核**：`megi.png` 的 TD 计数 **1171**，规格书 §4.2 用容差 6 量得 **1174** ⇒ 一致；
且 7 个调色板色**全部命中**（若颜色错位，直方图会大面积落空）⇒ **我们的 `WIKI_SCALE` 就是实测的 2023 色阶**。

⚠ **未做像素级叠加比对**：我们的画布公式与 Ty4 不同（B2/B3），像素不对齐 ⇒ 逐像素差没有意义。
本轮的对比落在"**尺寸 / 比例 / 直方图 / 居中**"四个可量化维度上，这已足以定位差异来源。

## 6. 死参数决策（第一轮发现的两个 + `multireset`）

| 参数 | 决策 | 理由（带规格书依据） |
|---|---|---|
| `path_width` | **接上**（不删） | TY4_SPEC §7.1：`unit="px"` 分支 `px_track = max(1, round(Track))`。我们现在 `coord_unit=="px"` 时线宽取它，`"deg"` 时仍走 `track_deg×k` |
| `point_size` | **接上**（不删） | 同上：`unit="px"` 时**半径** = `Point/2`（§7.1「除 2 = 半径」）。已据此实现 `_px_point_radius()` |
| `multireset` | **删掉** | §11.2：Ty4 的 `MultipleFile` 只实现了 `==2`（仅最后定位），`1`（自动重置）**无任何代码**；而我们本就**逐台风独立画路径**、不存在跨文件连线需要重置 ⇒ 语义为空。旧调用方仍传该键不会报错（`o.get` 不再读取） |

⇒ 对 P5 的答复：两个输入框**保留**（建议标注"仅『像素』单位生效"）；「多文件重置」下拉**可以去掉**。

## 7. 问题 / 风险 / unresolved

**已修（本轮）**
1. 🆕 **H1 抗锯齿**：原语中间 alpha **0 → 25/28/35**；端到端边缘混合像素 **0 → 2124**（§3）。
2. 🆕 **点半径取整**已去掉（浮点）；**风圈/图例色点**一并 AA。
3. 🆕 **死参数**：`path_width`/`point_size` 接上 `px` 分支；`multireset` 删除（§6）。
4. 🆕 **投影名透传**（`cfg["projection"]`，与 P4/P5 对齐；P4 未交付时回落等距圆柱 + 一次 warning）。

**主要差异（未修，需 Lead 裁决 —— 都在本文件之外或会改变既有输出）**
5. **B1+B2+B3+B10：画布/比例/尺寸基准整条链与 Ty4 不同**（实测 px/° 66.25 vs 30、画布 1981×1080 vs 1920×1080、点径 ⌀26.5 vs ⌀11、线宽 3 vs 2）。
   - 根因：Ty4 的画布是 `max(min, bbox_px + 2×margin_px)` 且**底图 1:1 裁剪**（px/° 恒 30）；我们把 `margin` 当"度"、并由画布反推 px/°。
   - **为什么不擅自改**：`margin` 的单位语义被 `paint_dialog.py`（P5 的文件）的 `coord_unit` 开关共用，改它会让"度/像素"两套语义在 UI 上打架，且会改变所有既有输出（含 `tests/test_paint.py` 的断言）。建议**单独立项**，由 P3+P5 一起定：`coord_unit=="deg"` 用"度留空 + 反推比例"，另外提供一条**Ty4 精确模式**（`px_per_deg=30` 固定 + bbox_px 公式）。
6. **B5+B9：预报/历史未区分**（三角 vs 圆、白线 vs 起点等级色）。**卡在数据模型**：`TrackPoint` 无 `tau`/forecast 字段（`app/typhoon.py:22-37`，不在我写作用域）。需要先在数据层补字段（P2/P5 或 Lead 裁决）。
7. **B11 风圈**与 §8 差别大（形状/档数/颜色/透明度/半径来源），且本机数据**无 RAD 行**（规格书 §8 亦未验证）；我们的 `r34` 缺省是假值 `wind×0.5`。建议要么按 §8 实现、要么**明确标注"无数据"**而不是画一个假圈。
8. **B12 图例**未按 §5 公式随画布缩放（固定 `item_h=26`/半径 8/总 7 条）；**B16** `all_points=False` 语义与 Ty4 的"只画线不画点"不同；**B13** 默认绘画顺序 `simult` 与 Ty4 `Priority=2`（点置顶）不同。三条都是低风险的小改，但都会**改变既有输出**，建议与 P5/P6 一起排。

**⚠ 证据不足，明确不改**
9. **B7/B8 色阶**：`wiki` 与 `2023` 逐像素相同 = **忠实于 Ty4**（§4.3；Lead 已核实并要求不要编造经典色阶）。`colors.py` **未动**。
10. **SS/SD 的形状与颜色**（我们：方块 + TS/TD 色；规格书 §14 配方：灰 `(204,204,204)`）：§4.2 的**像素实测只覆盖图例的 7 档**，SS/SD 无像素证据 ⇒ 按 Lead 指示**不动**。

**unresolved**
11. **B6 强度阈值逐风速对账未跑**（§4.1 给了 5 秒可判死的做法）。
12. **未做像素级叠加对比**（画布公式不同，像素不对齐；见 §5）。
13. ~~`paint_aa.aa_line` 的尺寸守卫~~ → **Lead 已做进底座**（`_AA_LINE_CHUNK_PX=96` 分块 + 接缝补偿），我随即**撤销了调用侧上限**（§3.4 修订）。
14. **`report` 里未核**：`ShowText`（B17）文案/位置/字号、`Mode=1` TCV 解析器、透明底需求（B14）。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 抗锯齿点/方/三角 | `app/paint_render.py` 的 `_draw_shape` |
| 点半径（浮点，不取整） | `_px_point_radius()` |
| 线宽双分支（deg/px） | `_px_track()` |
| 抗锯齿线（**无调用侧上限**） | `_draw_ty_path` 内的注释（历史与理由） |
| 风圈（AA 圆环） | `_draw_radii()` |
| 图例色点（AA） | `_draw_legend()` |
| 投影透传 + P4 接线 | `_PROJECTIONS` / `_project_via_p4()` / `__init__` 的 `self.projection` |
| 死参数决策 | `__init__` 里 `multireset` 的注释 + §6 |
| 画布公式（差异点 F1） | `_auto_output_size()`（`:333-343`） |
| margin 单位（差异点 F3） | `resolve_bounds()` 的 `lon_min -= margin_w` |
| 回退 | `git checkout HEAD -- app/paint_render.py`（HEAD 版 = 改前 `fb96d159…`） |
| 探针 | `G:\tmp\probe_paint_aa.py` / `probe_paint_aa_render.py` / `probe_ty4_cmp.py` |
| 对比基线 | `megi.png` / `rita.png` / `haiyan.png` / `sinlaku.png` / `xrq-dolphin.png`（仓库根） |
