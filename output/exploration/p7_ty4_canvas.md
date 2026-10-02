# P7 Ty4 精确画布模式（`canvas_mode="ty4"`）

> 执行者：`app-feat`（task-34）　写作用域：`app/paint_render.py` + 本报告
> 依据：`output/sim/_reference/TY4_SPEC.md` §0-2 / §14（画布公式、px/°、1:1 裁剪）
> 探针：`G:\tmp\probe_ty4_mode.py`（`fit` / `ty4` / `ref` 三个模式）
> 判据：画布尺寸、px/°、点径、线宽、居中偏差、像素 sha（全部确定性，未用计时）

## 一句话定位

给绘画渲染器加一条**可选**的 Typhoon4 精确画布模式：px/° 恒为 30、画布 = `max(最小值, bbox_px + 2×像素留空)`、底图 1:1 像素直搬、轨迹严格居中；**默认 `"fit"` 模式逐位不变**（用 A/B 像素 sha 证明）。

## 1. 清单与职责

| 文件 | 改动 | 改前 sha256 | 改后 sha256 |
|---|---|---|---|
| `app/paint_render.py` | ① 新增 `cfg["canvas_mode"]`（`"fit"` 默认 / `"ty4"`）；② 新增 `_ty4_canvas()` 实现 §0-2 公式；③ `render()` 分支：ty4 模式下 `self._k = 30`（**不由画布反推**）；④ `_draw_map_crop_into` 增「±1 px 1:1 直搬」快路径；⑤ 新增 `_TY4_PX_PER_DEG = 30.0` | `41177e4e962d451817d41aec65c93ee48c565484d560fbc631869d4dd604ae44` | `f4d99f96ab446b1e81569c738d3b18afe000e67f0d44e16090cbb2587681400f` |

**回退**：`git checkout HEAD -- app/paint_render.py` 会回到**改前 P3 之前**的 `fb96d159…`（会丢掉 P3 的 AA）。
本轮的"改前"是 `41177e4e…`；⚠ **我只记了它的哈希、没有留内容副本**，而 `_backup/` 与 git 里都没有它
⇒ 无法做**字节级** before/after diff（见 §3 的替代证明与 §7 的流程教训）。

## 2. 机制

### 2.1 开关

```python
_cm = o.get("canvas_mode") or "fit"
self.canvas_mode = "ty4" if _cm == "ty4" else "fit"     # 默认 "fit" = 现状
self.ty4_px_per_deg = float(o.get("px_per_deg", _TY4_PX_PER_DEG))   # 默认 30
```
老调用方 / 老测试 / `tools/` 脚本不传该键 ⇒ 走 `"fit"`，行为与改前一致。

### 2.2 `"ty4"` 的画布公式（§14 逐行对应）

```
tw = (lon1−lon0)×30 ; th = (lat1−lat0)×30
W  = int(max(min_w, tw + 2×WidthSpace_px))      ← 留空按**像素**
H  = int(max(min_h, th + 2×HeightSpace_px))
x0 = round((lon0+lon1)/2×30 − W/2)              ← 轨迹严格居中（偏差 ≤0.5 px）
y0 = round((90−(lat0+lat1)/2)×30 − H/2)
bounds = (x0/30, (x0+W)/30, 90−(y0+H)/30, 90−y0/30)    ← 1:1 裁剪窗口
self._k = 30                                     ← px/° 恒定，不由画布反推
```

### 2.3 为什么"点径/线宽"随之自动正确

`_px_track()` = `max(1, round(0.05×k))`、`_px_point_radius()` = `max(2.0, 0.4×k/2)`（P3 已按 §7.1 实现）。
`k=30` ⇒ 线宽 = `round(1.5)` = **2 px**、点半径 = **6.0 px（⌀12）** —— 与 §7.2 完全一致（见 §4）。

### 2.4 `ceil` → 截断（一个实测出来的坑）

第一版我写了 `ceil(span×30) + 2×margin`。**实测 sinlaku 因此宽了 1 px**：
它的经度跨度是 `67.4000001°`（浮点误差），`ceil(2022.000003) = 2023` ⇒ W=2743，而基准图是 **2742**。
规格书 §14 用的是 `W = max(W_MIN, tw + 2*HALF_W)`（**浮点**，随后交给 PIL `crop` 取整）
⇒ 改为 **`int(...)` 截断**。改后 sinlaku 宽度 **2742 = 基准图** ✅（其余 3 张本来就相等）。

## 3. 默认（`fit`）模式逐位不变的证明

### 3.1 结构上只有一处可能影响 fit 路径

`render()` 的 fit 分支我**逐句保留**（`resolve_bounds` → `_auto_output_size` → `output_size`/`px` 分支 →
`max(320,…)` → `_compute_point_density` → `self._k = inner.w / span_lon`），只是被包进 `else:`；
`_ty4_canvas()` 只在 `_ty4 == True` 时调用。**唯一会改变 fit 输出的**是 `_draw_map_crop_into` 里新增的
「±1 px 1:1 直搬」快路径。

### 3.2 A/B 实测（`probe_ty4_mode.py fit`）

把快路径那一段**程序化替换回旧的两行**（`subsurface` + `smoothscale`，来源 = 我改前的原文），
同一个输入渲染两次比像素 sha：

| 分支 | 底图尺寸 | 新实现 sha | 旧实现 sha | 逐位相同 |
|---|---|---|---|---|
| 走缩放分支（尺寸差很大） | 1080×540 | `bf761252faced7a8` | `bf761252faced7a8` | ✅ |
| 走 1:1 快路径（尺寸刚好相同） | 917×500 | `e449d4cc7b6feed0` | `e449d4cc7b6feed0` | ✅ |

⇒ 两种分支下新实现与"改前实现"**像素完全一致**（同尺寸的 `smoothscale` 本来就是恒等变换，
这条 A/B 把它证实了，而不是假设它）。

### 3.3 回归

| 测试 | 结果 |
|---|---|
| `tests/test_paint.py` | **15 passed**（= 基线 15；改后重跑 53.76 s） |
| `tests/test_render_geom.py` | **16 passed** |

## 4. 验收：5 个基准台风 × `Settings.ini` 参数

参数：`Track=0.05 Point=0.4 WidthSpace=360 HeightSpace=270 Width=1920 Height=1080 Priority=2 DrawLegends=4 AllPoints=1`
（对应 `canvas_mode="ty4"`、`margin_w=360`、`margin_h=270`、`min_w=1920`、`min_h=1080`、`draw_order="points_top"`、`legend_pos="br"`）

| 台风 | 报点 | bbox 跨度(°) | **我们的画布** | **基准图** | 尺寸相等 | px/° | 点半径 | 线宽 |
|---|---|---|---|---|---|---|---|---|
| megi | 56 | 29.9 × 16.3 | **1920×1080** | 1920×1080 | ✅ | 30.0 | 6.0 | 2 |
| rita | 70 | 80.3 × 8.0 | **3129×1080** | 3129×1080 | ✅ | 30.0 | 6.0 | 2 |
| haiyan | 48 | 60.7 × 17.7 | **2541×1080** | 2541×1080 | ✅ | 30.0 | 6.0 | 2 |
| sinlaku | 83 | 67.4 × 59.0 | **2742×2310** | 2742×**2312** | ⚠ 宽 ✅ / 高 −2 | 30.0 | 6.0 | 2 |
| dolphin | 99 | 78.4 × 34.3 | 3071×1569 | (1837×1224) | ➖ 不可比 | 30.0 | 6.0 | 2 |

**改前 vs 改后（同一台风，Megi）**：

| 指标 | 改前（`fit`） | 改后（`ty4`） | Ty4 基准 |
|---|---|---|---|
| 画布 | 1981×1080 | **1920×1080** | 1920×1080 ✅ |
| px/° | **66.25** | **30.0** | 30 ✅ |
| 点径 | ⌀26.5 | **⌀12.0**（半径 6.0） | 公式 ⌀12 / 实测 ⌀11 |
| 线宽 | 3 px | **2 px** | 2 ✅（§7.2 实测） |

### 4.1 两条残差（如实说明）

1. **sinlaku 高 −2 px**：我们的经纬跨度是 `lat 3.0→62.0 = 59.000°`（H = `int(1770+540)` = 2310），
   而基准图 `H=2312` 反推需要 `59.067°`。**经度侧已完全吻合（2742）**，所以这不是公式误差，
   而是**源数据侧**差了约 `0.067°`（2 px）—— 很可能那份 `wp042026.dat` 与当年画基准图用的
   b-deck 不是同一修订版（Lead 已发现 Dolphin 那份也踩过数据问题）。**无原始 b-deck 无法进一步判定。**
2. **dolphin 不可比**：`xrq-dolphin.png` 是 **1837×1224 的 QQ 机器人风格图**（圆锥投影 + 平涂陆海 + 虚线网格），
   不是 Ty4 的 1:1 底图裁剪 ⇒ 它的尺寸与 Ty4 公式**本来就不该相等**（它是 P4 的目标）。
   本轮只能确认它能被解析（99 报点）并按 ty4 公式出图。

### 4.2 居中

构造上 `x0 = round(cx − W/2)` ⇒ 轨迹 bbox 中心落在画布中心，**偏差 ≤0.5 px**（规格书容差 ≤1 px）。
实测（关掉图例后量 ink bbox 中心）：rita **(−0.5, −0.5)** ✅ 与构造一致；
megi/haiyan/sinlaku/dolphin 量到 −7…+7 px，但那是**度量偏差**而非几何偏差 ——
我的 ink 集合只取了 7 个色阶 + 白色，**漏掉了 SD/SS 的灰/蓝/橙**（`scheme_color` 里 SD/SS 不在 7 色内），
而转向温带/副热带的点恰在轨迹两端 ⇒ 两端被截掉一头，中心自然偏。**要精确量需把 SD/SS 色也纳入 ink 集合**（§7 待办）。

### 4.3 原始输出摘录（未加工）

```
# probe_ty4_mode.py fit   —— 默认模式 A/B（唯一影响 fit 路径的改动）
  小底图(走缩放分支)       底图=(1080, 540)  新=bf761252faced7a8  旧=bf761252faced7a8  逐位相同=True
  刚好同尺寸(走1:1快路径)  底图=(917, 500)   新=e449d4cc7b6feed0  旧=e449d4cc7b6feed0  逐位相同=True

# probe_ty4_mode.py ref   —— 基准图实际尺寸
  megi     (1920, 1080)      rita  (3129, 1080)     haiyan (2541, 1080)
  sinlaku  (2742, 2312)      dolphin (1837, 1224)  ← QQ 风格图，非 Ty4 裁剪

# probe_ty4_mode.py ty4   —— 逐台风验收（修 ceil 之后）
台风      数据报点  bbox跨度(°)      我们的画布   px/°   基准图尺寸   尺寸相等  点半径  线宽  居中偏差
  megi         56   29.9x16.3  1920x1080   30.0  (1920, 1080) True         6.0    2  (-6.5,+4.0)
  rita         70   80.3x 8.0  3129x1080   30.0  (3129, 1080) True         6.0    2  (-0.5,-0.5)
  haiyan       48   60.7x17.7  2541x1080   30.0  (2541, 1080) True         6.0    2  (-7.0,+2.0)
  sinlaku      83   67.4x59.0  2742x2310   30.0  (2742, 2312) False        6.0    2  (-1.5,-6.0)
  dolphin      99   78.4x34.3  3071x1569   30.0  (1837, 1224) False        6.0    2  (+7.0,-3.0)

# sinlaku 的残差诊断（源数据侧，不是公式）
lat min (3.0, '2099040512')  lat max (62.0, '2099042518')  span=59.000
需要 H=2312 => span=59.067    我们 span=59.000      ← 差 0.067° = 2 px
lon min (144.4)  lon max (211.8)  span=67.400          ← 修了 ceil 后 W 2742 = 基准图 ✅
```

## 5. 与 P5 的接口

- P5 只需在 `_do_render()` 的 cfg 里加 `cfg["canvas_mode"] = "ty4" / "fit"`（默认不写 = `fit`）。
- ty4 模式下 `margin_w/margin_h` 是**像素**（`Settings.ini` 的 360/270），而 fit 模式下它们仍是"度"。
  ⇒ UI 文案建议：勾选"Ty4 精确画布"时把两个留空框的单位标成 **px**，并按 `360/270` 预填。
- `px_per_deg` 若换底图可传 `cfg["px_per_deg"] = 底图宽/360`（默认 30）。

## 6. 值得保留的现状

1. **`self._k` 是整条渲染链的比例基准**：P3 把它按 §7.1 接进了 `_px_track`/`_px_point_radius`，
   P7 只需在 ty4 模式下把 `_k` 钉成 30 ⇒ 线宽/点径**自动**落到 Ty4 的 2 px / ⌀12，不必改这两处。这是 P3 那次拆分的红利。
2. **`fit` 模式的公式与语义一个字没动**：`canvas_mode` 是纯增量开关，风险被限制在新分支里。
3. **1:1 直搬同时是个小优化**：`fit` 模式下若某天裁剪尺寸恰好等于内容区，也不再白付一次 `smoothscale`（已 A/B 证明不影响像素）。
4. **`ceil`→截断这条**值得记入"复刻类工作"的通用经验：**规格书给的浮点公式不要"顺手加 ceil"**，
   差 1 px 就会在像素级对账里露出来。

## 7. 问题 / 风险 / unresolved

**一般**
1. **流程教训（我的疏漏）**：开工时我按任务要求记了 `sha256sum`（`41177e4e…`），但**没有留内容副本**；
   `_backup/`、git（HEAD 是更早的 `fb96d159…`）、`__pycache__`（已被我随后的 import 覆盖）都取不回它
   ⇒ 只能用 §3.2 的 A/B 证明"fit 逐位不变"，而**做不到字节级 before/after diff**。
   ⇒ 与本轮之前的 `main.py` 同类问题：**光记哈希不足以支撑事后对比，落地新改动前要复制一份内容**。
   （Lead 已把"回退点要更新"写进 `README_ROLLBACK.md`；本条是"**要存内容，不只是哈希**"的补强。）
2. **未用真实底图跑一次完整 ty4 输出**：验收里 `map_path=None`（只出轨迹+图例），
   所以"底图 1:1 直搬"只在 §3.2 的合成底图上验证过（`e449d4cc…` 那条分支）。
   建议补一条（需 ~233 MB 内存加载 10800×5400）：
   `TrackMapRenderer(dict(canvas_mode="ty4", margin_w=360, margin_h=270, min_w=1920, min_h=1080, map_path=r"G:\气象\typhoon3\map\[GLOBAL].png")).render([megi])`
   预期：`megi.png` 与输出**逐像素高度接近**（规格书 §2.3 称 4 张参考图与底图 1:1 裁剪吻合 99.3–99.4%）。
3. **居中度量口径**待收紧（§4.2）：把 SD/SS 颜色纳入 ink 集合后重测，才能给出可信的"居中偏差"数字。

**证据不足 / 明确不做**
4. **sinlaku 的 −2 px**：源数据修订版不可得 ⇒ 保留为已知残差，**不通过猜数据去凑**。
5. **dolphin 与 Ty4 尺寸不可比**：它是 QQ 风格图，属 P4 范围；P7 只保证公式正确。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 模式开关 | `app/paint_render.py` 的 `__init__`：`self.canvas_mode` / `self.ty4_px_per_deg` |
| Ty4 画布公式 | `_ty4_canvas()`（含 `ceil`→截断的注释） |
| 分支与 `_k` 钉死 | `render()` 里的 `_ty4 = ...` / `self._k = self.ty4_px_per_deg` |
| 底图 1:1 直搬 | `_draw_map_crop_into()` 的 ±1 px 快路径 |
| px/° 常量 | `_TY4_PX_PER_DEG = 30.0` |
| 线宽 / 点半径公式 | `_px_track()` / `_px_point_radius()`（P3 按 §7.1 实现） |
| 探针 | `G:\tmp\probe_ty4_mode.py`（`fit` = 不变性 A/B；`ty4` = 验收表；`ref` = 基准图尺寸） |
| 基准图 | 仓库根 `megi.png` / `rita.png` / `haiyan.png` / `sinlaku.png` / `xrq-dolphin.png` |
| 素材数据 | 见 task-34 的表（5 份，均**在仓库外**只读） |
