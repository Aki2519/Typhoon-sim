# P8 投影开关真的接上（+ Ty4 模式与圆锥投影的相容性裁决）

> 执行者：`app-feat`（P8）　写作用域：`app/paint_render.py` + 本报告
> 探针：`G:\tmp\probe_projection_switch.py`　并排图：`output/exploration/p8_projection_switch.png`
> 判据：像素 sha、轨迹点最大位移 px、stderr 告警、回归（全部确定性）

## 一句话定位

投影开关此前**完全没生效**：我给 P4 传的 `ctx` 字段名不对，`KeyError:'n'` 被 `except Exception` 吞掉 ⇒ **每次静默回落到等距圆柱**（两种投影逐点完全相同）。本轮按 P4 **已冻结**的契约接上，并把"Ty4 精确模式 × 圆锥"的冲突查清、定下规则。

## 1. 清单与职责

| 文件 | 改动 | 改前 sha256 | 改后 sha256 |
|---|---|---|---|
| `app/paint_render.py` | ① 删掉手搓 ctx 的 `_project_via_p4`，改走 `make_ctx` + `project` + **`inner` 偏移**；② `ctx` **按 `(投影名, bounds, inner 宽高)` 缓存**在渲染器上（`make_ctx` 要采样边界，不能每点重建）；③ `equirect` **完全不经过 P4**（保逐位不变）；④ `canvas_mode="ty4"` 下把投影**钉死 equirect** + 一次明确告警；⑤ warning 改为**按 key 只打一次** | `f4d99f96ab446b1e81569c738d3b18afe000e67f0d44e16090cbb2587681400f` | `96fe22f31255231b2aa249096516ed5ee3bbaaa17e21fcb20ddda84e61928ead` |

**改前内容已按要求落盘**：`output/exploration/_backup/state_v6/paint_render.py` = `f4d99f96…`（**存内容，不只是哈希** —— 本轮的 before/after 比对就是靠它做的）。

## 2. 根因（我上一轮写的，契约没对齐）

```python
# 错（P8 之前）：字段名全不对
pt = _project_via_p4(self.projection, lo, la, {"inner": inner, "bounds": bounds, "k": self._k})
```
P4 的 `_raw_conic` 需要 `ctx['n'] / lon_c / lat_c / rho_k`（`paint_projection.py:129-133`），`_raw_equirect` 需要 `lon_c / lat_c`（`:138-140`），`project` 需要 `scale / ox / oy`（`:157-158`）。我给的三个键**一个都不匹配** ⇒ 第一次调用就 `KeyError: 'n'`，而 `_project_via_p4` 的 `except Exception` 把它变成"回落等距圆柱"，只在 stderr 留一句 `不可用('n')`。
**我当时写这段时 P4 还没交付**（我在 §2.2 里也是这么注明的），契约从来没有真正对齐过 —— 现在按冻结接口重接。

## 3. 修复

### 3.1 正确接法

```python
# ctx：按 (投影名, bounds, inner 宽高) 缓存，一帧只建一次
ctx = PJ.make_ctx(self._pj_active, bounds, (inner.w, inner.h))
x, y = PJ.project(self._pj_active, lo, la, ctx)
return (int(x) + inner.x, int(y) + inner.y)      # ← P4 返回的是【内容区相对】坐标
```
- `make_ctx(name, bounds, size, pad=0, **over)`：先填投影参数，再**采样边界求投影包围盒**，最后等比缩放 + 居中（`paint_projection.py:241-294`）。
- **缓存**：`self._pj_ctx_cache = (key, ctx)`，`key = (投影名, tuple(bounds), inner.w, inner.h)`（`_projection_ctx()`）。换台风/换窗口才重建；同一帧里几百上千个点复用同一个 ctx。
- **`inner` 偏移**：`ctx['size'] = (inner.w, inner.h)` ⇒ `project` 的原点在内容区左上角，忘加 `inner.x/y` 会让整条轨迹平移（Ty4 的 `fit` 模式 `inner.x=0` 时侥幸看不出，`margin` 非零就露馅）。

### 3.2 为什么 `equirect` **不能**走 P4（保逐位不变的关键）

`_raw_equirect` 是 `X = cos(lat_c)·dlon`、`Y = lat − lat_c`（`:138-140`），而我们的 `_proj` 是
`x = inner.x + (lo−lon_min)/span_lon·inner.w` —— **经纬用同一个尺度，没有 `cos(lat_c)` 因子**。
对 megi（`lat_c ≈ 20°`，`cos ≈ 0.94`）两者横向差 **6%**；再叠加 `make_ctx` 的自动装箱
（实测 `scale = 66.258`，见 §4），像素必然变。⇒ **`equirect` 一律走我们原有数学，绝不经过 P4**。

## 4. Lead 提问的裁决：`make_ctx` 的自动装箱会不会破坏 Ty4 的 `px/° 恒 30`？

**会，而且很彻底。** 实测（megi 的 Ty4 窗口 `bounds=(117.2,147.1,11.8,28.1)`、`size=(1920,1080)`）：

| 调用 | 自动装箱算出的 `scale` | `ox` | `oy` | 与 Ty4 的 30 px/° 之比 |
|---|---|---|---|---|
| `make_ctx("equirect", …)` | **66.258** | 960.00 | 540.00 | **2.209×** |
| `make_ctx("conic", …)` | **3.006** | 960.00 | −7014.72 | **0.100×** |

`make_ctx` 的 `auto` 分支（`:276-293`）把 `scale = min((W−2p)/bw, (H−2p)/bh)`（**按投影后的包围盒装箱**），
并重算 `ox/oy` 让内容居中 —— 这与"`px/°` 恒 30 + 底图 1:1 裁剪 + 窗口由 bbox+像素留空决定"**是两套取景语义**。
而且 `auto` 只在调用方**没传** `scale`/`ox`/`oy` 时才启用（`:276`），**显式传了就跳过**（实测：传 `scale=30.0, ox=0.0, oy=0.0` 时原样保留 ✅）。

**为什么我没有选"给 ty4 显式传 `scale=30`"**：
即便把 `scale` 钉住，ty4 模式下垫在下面的**底图就是等距圆柱栅格**（`[GLOBAL].png` 1:1 裁剪，
网格/经纬度标签/红框都烘焙在图里，TY4_SPEC §0-4）—— 在上面画一条**圆锥**轨迹，轨迹与底图的地理位置会系统性错位
（网格是直的、轨迹按圆锥弯）。**这不是参数能调和的，是语义冲突。**
⇒ **规则**：`canvas_mode="ty4"` ⇒ 投影**钉死 `"equirect"`**，若调用方要了别的投影则**打一次明确告警**说明要改用 `canvas_mode="fit"`。
已验证 ty4+conic 与 ty4+equirect **输出逐位相同且尺寸仍是 1920×1080**（Ty4 保真不受影响）。

## 5. 验收（`probe_projection_switch.py`，全部实测）

| # | 判据 | 结果 |
|---|---|---|
| 1 | 同一台风两种投影各渲一次 ⇒ 像素 sha **必须不同** | ✅ `equirect=16b60393b764dab96a2e95a6` vs `conic=2d4fb312a563906cc150a3d0` |
| 1b | 轨迹点**最大位移 px**（应显著 >0） | ✅ **最大 77 px / 平均 36.0 px**（megi 在 ~130–147°E；符合"几十~上百 px"） |
| 2 | `equirect` **逐位不变** | ✅ 改前（`_backup/state_v6`）= 改后 = `16b60393b764dab96a2e95a6` **逐位相同** |
| 3 | 不再打印 `不可用('n')` | ✅ `stderr(equirect)` 与 `stderr(conic)` 均**为空** |
| 4 | 并排图 | ✅ `output/exploration/p8_projection_switch.png`（2212×600，左 equirect / 右 conic） |
| 5 | ty4 相容性 | ✅ ty4+conic 与 ty4+equirect **逐位相同**、尺寸 **1920×1080**，并打印一次裁决告警 |
| 6 | 回归 | ✅ `tests/test_paint.py` **15 passed**；`tests/test_render_geom.py` **16 passed** |

### 5.1 原始输出摘录

```
=== 1) equirect vs conic（同一台风、同一参数）===
  equirect sha = 16b60393b764dab96a2e95a6
  conic    sha = 2d4fb312a563906cc150a3d0
  两者不同 = True    <== 判据 1
  轨迹点最大位移 = 77 px   平均 = 36.0 px  <== 判据 1
  stderr(equirect) = (无)
  stderr(conic)    = (无)               <== 判据 3：不再有 不可用('n')

=== 2) equirect 逐位不变（改前 f4d99f96 vs 改后）===
  改前 sha = 16b60393b764dab96a2e95a6
  改后 sha = 16b60393b764dab96a2e95a6
  逐位相同 = True    <== 判据 2

=== 3) ty4 模式 + conic ⇒ 钉死 equirect ===
  ty4+equirect sha = 193b783828e8269f6bec8fc7  尺寸=(1920, 1080)
  ty4+conic    sha = 193b783828e8269f6bec8fc7  尺寸=(1920, 1080)
  两者相同(钉死生效) = True
  stderr = [paint_render] canvas_mode='ty4' 固定等距圆柱（底图是 1:1 裁剪的等距圆柱栅格，
           网格/标签已烘焙在其中）；投影 'conic' 需要 canvas_mode='fit'。本次按 equirect 渲染。
```

**并排图**（左 = 等距圆柱，右 = 圆锥）：`output/exploration/p8_projection_switch.png` —— 右图轨迹明显被圆锥几何压缩/弯折，且被 `make_ctx` 装箱居中；两者肉眼可辨，与 77 px 的最大位移一致。

### 5.2 复现 / 复验命令

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe
$P 'G:\tmp\probe_projection_switch.py'        # 本节全部数字 + 重新生成并排图
$P -m pytest tests/test_paint.py -q --no-header
$P -m pytest tests/test_render_geom.py -q --no-header
```

### 5.3 这个 bug 为什么能活到现在（检测缺口）

我在 P3 那一轮**就写了** `不可用('n')` 的告警，但它只在"**有人真的渲一次 conic**"时才会出现 ——
P3/P7 的验收探针全部只跑 `equirect`（Ty4 复刻本来就只关心等距圆柱），所以那句告警**一次都没被打出来过**。
⇒ 教训：**"接口接了但没被调用过"= 没接**。凡是"新开关 + 老验证集"的组合，验收里至少要有一条**非默认取值**的端到端用例
（本轮的判据 1 就是这条；P7 的 `ty4` 之所以没踩坑，是因为它是**画布**开关、我在验收里真的用它渲了 5 个台风）。

## 6. 值得保留的现状

1. **`_proj` 是唯一投影出口**：所有绘制（点/线/风圈）都经它 ⇒ 投影开关只需在这一处接对，不必改任何绘制代码。
2. **`make_ctx` 的 `over` 机制是好设计**：传了 `scale/ox/oy` 就跳过自动装箱 —— 将来若要做"圆锥 + 固定比例"的取景，这就是现成的钩子（本轮没用，理由见 §4）。
3. **warning 按 key 去重**：`_proj_warn(key, msg)` 用集合去重，既不静默也不刷屏。
4. **ctx 缓存挂在渲染器实例上**：投影/窗口不变时一帧只采样一次边界；换台风自动重建（key 含 `bounds`）。

## 7. 风险 / unresolved

**一般**
1. **`conic` 目前只有"装箱居中"一种取景**：`canvas_mode="fit"` 下圆锥会把经纬框装箱进画布，`px/°` 随纬度变化（正形圆锥本来就如此）。若 P5/P4 想给圆锥也做"固定比例"，用 `make_ctx(..., scale=…, ox=…, oy=…)` 即可（本轮未做，因为 Ty4 保真与圆锥取景是两套语义，见 §4）。
2. **`ctx` 缓存的生命周期 = 渲染器实例**：`_draw_labels` 等后续绘制若用同一个渲染器换 `bounds` 会被 key 命中失效而重建 ✅；但**跨台风混画**（一次 `render` 里多台风共用同一 bounds）本来就只有一个 key，无影响。
3. **`conic` 的底图**：`fit` 模式下若配 `map_path`（等距圆柱底图），圆锥轨迹与底图同样会错位 —— 与 §4 同理。**圆锥要配自己的底图/底图绘制方式**（P4/P5 范围）；本轮只保证"投影数学接对了"，未处理"圆锥专用底图"。

**unresolved**
4. **未做圆锥的定量几何校验**（P4 已从 `xrq-dolphin.png` 拟合过 `n/lon_c/rho_k`，属 P4 的验收范围）。本轮只证明：开关**生效**、位移**显著**、`equirect` **不变**。
5. **`projection` 与 `legend/labels` 的关系未测**：`_draw_labels` 也走 `_proj`，`conic` 下文字位置会跟着投影走（预期如此），但未逐项验收。

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 投影开关的接线 | `app/paint_render.py` 的 `_proj()` → `_proj_via_p4()` |
| ctx 构造 + 缓存 | `_projection_ctx()`（key = `(投影名, bounds, inner.w, inner.h)`） |
| `inner` 偏移 | `_proj_via_p4()` 里的 `+ inner.x, + inner.y`（**别漏**） |
| 生效投影（ty4 钉死） | `render()` 里的 `self._pj_active` 与 `ty4-pin` 告警 |
| 投影名白名单 | `_PROJECTIONS = ("equirect", "conic")`（与 P4 `PROJECTIONS`、P5 `cfg["projection"]` 同名） |
| 告警去重 | `_proj_warn(key, msg)` / `_proj_warned_keys` |
| P4 契约 | `app/paint_projection.py`：`make_ctx`(:241) / `project`(:143) / `CONIC_REFERENCE`(:108) |
| 改前基线 | `output/exploration/_backup/state_v6/paint_render.py`（`f4d99f96…`） |
| 探针 / 并排图 | `G:\tmp\probe_projection_switch.py` / `output/exploration/p8_projection_switch.png` |
