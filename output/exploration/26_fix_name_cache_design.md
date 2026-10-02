# 26 名文本缓存：把工作集压下来（表面尺寸，不是加大预算）

## 一句话定位

`_name_shadow_cache` 在 207 台风强压下判 FAIL，瓶颈是 **32 MiB 字节预算**。本轮**不动预算**，改的是**表面尺寸**：`_glow_pad` 从 `round(6·scale)` 收到 `round(3·scale)`（= 真实最大辉光半径），**表面字节 −20.6%**、**741 键规模下驱逐 49 → 0**、**不透明背景下逐字节不变**，并留了回退开关。

> **本轮最重要的发现**：app-feat 的 "0 miss / 0 eviction ✅" 与 W5 的 FAIL **两个都对，但差异不是"场景不同"，而是探针把字号口径写小了 5.06 倍** —— `probe_name_cache.py:47,57` 硬编码 `name_factor=1.0`，而生产是 `name_size/100 × 1.5`（`config.json: name_size=150` ⇒ **2.25**）。面积比 (2.25/1.0)² = **5.06**，正是 7.96 MiB → ~44.9 MB 的差额来源。**W5 的 FAIL 才是生产代表值。**

---

## 1. 清单与职责

| 文件 | 改动 | sha256 改前 → 改后 |
|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py` | **唯一被改的源码**：新增模块常量 `GLOW_PAD_TIGHT`；重写 `_glow_pad`；更新 2 处旧注释 | `881caa2b…1411f` → **`be89079e…b6bc2f`** |
| `output/exploration/_backup/state_v2/_draw_icon_mixin.py` | 回退点（未动） | `881caa2b63c0bcff34d4a2f6f768738b0124dd81ba0d8a2fcd12eaf33ac1411f` |
| `app/cache_store.py` | **未改**（只用其 `SurfaceCache`） | — |
| `app/name_pos_dialog.py` | **未改**（写域外）—— 它 `from ... import _glow_pad`（`:71`）⇒ 自动跟随新口径，无需改 | — |
| `config.json` | **未改**（只读它取 `name_size=150` / `peak_label_size=150`） | — |

**改动规模**：`diff -u` = **+38 / −2 行**，全部落在 `_glow_pad` 一个函数（+ 一个常量 + 2 行注释）。**预算 32 MiB 一字未动**，`_name_shadow_cache` 的构造参数未动。

**回退方式**

```bash
cp output/exploration/_backup/state_v2/_draw_icon_mixin.py app/ty_sim_mixins/_draw_icon_mixin.py   # 文件级
python -c "import app.ty_sim_mixins._draw_icon_mixin as M; M.GLOW_PAD_TIGHT=False"                  # 运行时软开关
```

---

## 2. 数据流 / 调用链

```
draw_typhoon_name(surface, ty, x, y, ...)                      _draw_icon_mixin.py:1230
  ├── name_factor = getattr(self,'name_size',100)/100.0*1.5*zoom          :1280   ← ★ *1.5
  ├── pk_factor   = getattr(self,'peak_label_size',100)/100.0*zoom        :1378   ← 无 *1.5
  ├── name_off  = _glow_pad(name_factor)                                  :1349
  │     └── surf = self._get_name_surf(name, color, name_factor)          :1101
  │            size = max(8, round(_NAME_BASE_PX * name_factor))          :1107    (_NAME_BASE_PX=28)
  │            key  = ('name', name, tuple(color), size)                  :1108    ← 键已最小化
  │            miss → _sized_glow_text(_sized_font(_name_font_cache,size), name, color, name_factor)
  │                     └─ pad = _glow_pad(scale)                         :195     ← ★ 本次改这里
  │                        surf = (w+2p, h+2p)  ; 文字在 (p, p)
  │                        glow 三层半径 (3,2,1) → blit 在 (p+dx, p+dy)
  │            → self._name_shadow_cache.put(key, surf)                   :1113
  │            → self._blit_faded(surface, shadow_surf, (text_x-name_off, ty_pos-name_off))
  │                                                                       :1359-1360
  ├── _lbl_x = text_x - _glow_pad(pk_factor) + _pk_dx                     :1381
  ├── _name_core(name,color,name_factor) → text_core_rect(..., pad=_glow_pad(name_factor))  :1120-1121
  └── _label_core(label,color,pk_factor) → text_core_rect(..., pad=_glow_pad(pk_factor))    :1127-1128
```

**`_glow_pad` 的全部消费点（6 处，全部把它当"表面边缘 → 文字原点"的距离 ⇒ 统一改小后文字落屏坐标不变）**

| 消费点 | 行 | 用途 |
|---|---|---|
| `_sized_glow_text` | `:195` | **创建表面**时留边距 |
| `_name_core` | `:1121` | 传 `text_core_rect(..., pad=...)` |
| `_label_core` | `:1128` | 同上 |
| `draw_typhoon_name` | `:1349` | `name_off` → blit 位置 `(text_x - name_off, ty_pos - name_off)` |
| `draw_typhoon_name` | `:1381` | `_lbl_x` 标注 x |
| `app/name_pos_dialog.py` | `:325,327` | 预览框定位（`noff`/`loff`），并用 `surf.get_size()` 建 rect ⇒ 自动一致 |

---

## 3. 核心机制详解

### 3.1 方向 1｜量化键空间：键**已经是极小的**，重复来自尺寸不在键里

**先看键设计**（`_get_name_surf` / `_get_peak_label_surf`）：

```python
key = ('name',  name,  tuple(color) if color else None, size)   # :1108
key = ('peak',  label, tuple(color) if color else None, size)   # :1075-1078 同构
```

⇒ 键的维度只有 `kind × text × color × size`。**app-feat 已经把 `name_factor` 收敛成 `size`**（注释 `:1104-1105`：不同 factor 落到同一字号本就该命中同一张面）⇒ **这一步没有剩余重复可挖**。

**实测键构成**（207 台风规模、生产 factor）

| kind | keys | bytes | avg |
|---|---|---|---|
| name | 74 | 7.66 MB | **105.9 KB** |
| peak | 166 | 9.09 MB | **56.1 KB** |
| 合计 | **240** | **16.75 MB** | 71.5 KB |

（用 74 个真实台风 + 生产格式标注；W5 的实际是 207 名 + 501 标注 = 708 键，比例 3.24 键/台风与本表 3.24 一致）

**两个"重复"候选，实测都不成立**：

| 候选 | 判定 | 依据 |
|---|---|---|
| **同文本 × 多 size**（方向 2 的前提） | ❌ **不存在** | 正常绘制时 `zoom=1.0` 且 `name_size` 是全局设置 ⇒ `name_factor` 是**常数** ⇒ `size` 只有一个值。`zoom≠1` 只在「名字位置」预览里出现（`:1245-1246`）。**按尺寸复用（渲染一次最大尺寸再 smoothscale）在 207 台风场景收益 ≈ 0**，且会引入位图缩放 ⇒ 画质不等价 |
| **color 维度爆炸**（方向 3） | ❌ **无可挖** | 名字色来自 `_get_max_wind_color(ty)`（强度档调色板），**每个台风在某一帧只有一种颜色**；207 个台风名互不相同 ⇒ `(name,color)` 天然唯一。更关键：`_sized_glow_text` 的**描边色是 `darken_color(color,0.62)`**（`:193`），辉光/描边像素**依赖 color** ⇒ 改成"白色字形 + 运行时着色"会**改变像素**，像素等价性不成立 |
| **降到整层合成**（方向 4） | ❌ **对本场景无效** | `intensity_chart` 的样板成立是因为图**打开时一次画完、之后不动**（稳态 `rt=0`）。而名字/标注**每帧跟着台风位置移动**，按帧合成整层 ⇒ 每帧都 miss ⇒ 光栅化不降反升。**只有静态标注层才适用** |

⇒ **结论：键设计已到极限，能动的只有"表面尺寸"。** 这正是本轮的落点。

### 3.2 为什么每张面这么大：47.3% 的像素 alpha≤1

**实测**（240 个真实样本，`pygame.Surface.get_bounding_rect(1)` 对照整面）：

| 量 | 实测 |
|---|---|
| 整面像素总数 | 4.19 Mpx（×4 B = 16.75 MB） |
| `alpha≤1` 的像素 | **47.3%** |
| 每面 x 方向多余边距（中位） | **14 px** |
| 每面 y 方向多余边距（中位） | **26 px** |

**根因（代码级、可证）**：`_sized_glow_text` 的三层辉光半径是 `(3,2,1)`，全部 blit 在 `(pad+dx, pad+dy)`，其中

```python
for radius, a in ((3,26),(2,46),(1,96)):
    for dx, dy in _ring_offsets(round(radius * scale)):     # :200  → 最大半径 r = round(3*scale)
        surf.blit(layer, (pad + dx, pad + dy))              # :201
for dx, dy in _ring_offsets(round(scale)):                  # :202  8 向描边, 半径 ≤ r
    surf.blit(bk, (pad + dx, pad + dy))
surf.blit(fg, (pad, pad))                                   # :204
```

⇒ **所有 blit 的可绘区域恒为 `[pad−r, pad+w−1+r] × [pad−r, pad+h−1+r]`**（`dx,dy ∈ [−r,r]`）。
而**旧式 `pad = round(6·scale) = 2r`** ⇒ 每侧 `r` px 落在可绘区域之外、**一个像素都不会被写**。

**数值验证**（`verify_geom.py`，7 个 factor）：

| factor | size | 旧 pad | 新 pad | 最大辉光半径 r | 实测非零像素是否越界 |
|---|---|---|---|---|---|
| 0.5 | 14 | 3 | 2 | 2 | 否 |
| 1.0 | 28 | 6 | **6**（保持） | 3 | 否 |
| 1.5 | 42 | 9 | 4 | 4 | 否 |
| 2.25 | 63 | 14 | 7 | 7 | 否 |
| 3.0 | 84 | 18 | 9 | 9 | 否 |

⇒ 新 `pad = r` 时表面恰好等于可绘区域 ⇒ **既不留死空间、也不裁掉任何像素**（`pad = r` 是**充分且必要**的）。

### 3.3 实现

```python
#: 辉光边距口径(2026-10-02)。True = 紧边距; False = 旧口径。回退开关。
GLOW_PAD_TIGHT = True


def _glow_pad(scale: float) -> int:
    scale = max(0.05, float(scale))
    if not GLOW_PAD_TIGHT:
        return max(1, int(round(_GLOW_PAD_PX * scale)))       # 旧: round(6*scale)
    if abs(scale - 1.0) < 1e-6:
        return _GLOW_PAD_PX                                   # =6, 与 utils.render_glow_text 一致
    return max(1, int(round(3.0 * scale)))                    # 新: == 最大辉光半径
```

实测取值：`scale = 0.5 / 1.0 / 1.5 / 2.25 / 3.0` ⇒ 旧 `[3, 6, 9, 14, 18]` → 新 `[2, 6, 4, 7, 9]`。

**三个必须说清的边界**：

1. **`scale≈1.0` 必须保持 6**：那一档 `_sized_glow_text` **直接委托** `utils.render_glow_text`（`:187-189`），后者 pad 硬编码 6（`utils.py:247`）。保持 6 才能守住 `:182-183` 那条既有不变量"100% 档与旧观感逐像素一致"。
2. **跨过 1.0 时表面尺寸跳 3px/侧**：因为两档口径不同（6 vs `round(3·scale)`）。但**文字落屏位置在两侧都由 `_glow_pad` 一致补偿** ⇒ **场景无跳变**（§5 的逐字节证据覆盖了 factor 1.1 与 1.5）。
3. **`text_core_rect(..., pad=...)` 的语义不变**：`pad` 只在"实心像素包围盒为空"的退化分支里当兜底矩形用（`:122-125`）。新 pad 更小 ⇒ 退化分支的兜底框略变。这是**极淡/空文本**的边角情形，已在 §7 记为未测项。

### 3.4 为什么这不是"调参掩盖问题"

| 判据 | 本次 |
|---|---|
| 是否改了预算 | **否**。`SurfaceCache("name_text", 32*1024*1024, 1024)` 一字未动 |
| 是否改了条数上限 | **否**。`max_items=1024` 未动 |
| 改的是什么 | **删掉"永远不会被写入"的分配**（可绘区域之外的外圈 `r` px） |
| 有代价吗 | **无**。不丢像素（§3.5 证明）、不改键、不改布局、不增计算 |
| 是否用计时做判据 | **否**。全部判据是确定性计数（驱逐数 / 字节数 / 逐字节 sha256） |

---

## 4. 输入输出资产

| 路径 | 角色 | 体量/要点 |
|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py` | **被改文件** | 1771 → **1807** 行；sha256 `881caa2b…` → `be89079e…` |
| `output/exploration/_backup/state_v2/_draw_icon_mixin.py` | 回退点（未动） | sha256 `881caa2b63c0bcff34d4a2f6f768738b0124dd81ba0d8a2fcd12eaf33ac1411f` |
| `output/exploration/_backup/SHA256_STATE_V2.txt` | 基线清单（未动） | 含 `_draw_icon_mixin.py = 881caa2b…`、`fonts.py = f17d22a7…` |
| `G:\tmp\w5\w5_all.py:214-228` | W5 的强压探针（**别人的**，只读） | `for i,ty in enumerate(sim.tys): sim.draw_typhoon_name(off,ty,...)` —— 207 次直画 |
| `G:\tmp\w5\before_summary.txt:34` / `after_summary.txt:34` | W5 的判定数据 | BEFORE `items 256 / 16.0 MB / evict 548`；AFTER `items 528 / **33,513,616 B = 99.88%** / evict **204**` |
| `G:\tmp\perf\probe_name_cache.py:47,57` | app-feat 的探针（**别人的**，只读） | **`cats[...], 1.0`** —— `name_factor` 硬编码 1.0 |
| `C:\Users\...\Temp\sc4\probe_ws_name.py` | 本轮探针（工作区外） | 键空间 / 表面字节 / 边距浪费 |
| `…\Temp\sc4\verify_geom.py` | 本轮探针 | 辉光几何（可绘区域）数值验证 |
| `…\Temp\sc4\exp_pad2.py` | 本轮探针 | 三档键集的 A/B（含大预算测真工作集） |
| `…\Temp\sc4\verify_bytes.py`、`diff2.py`、`diff3.py` | 本轮探针 | 逐字节等价性（隔离 / 大画布 / 重叠贴边） |
| `…\Temp\sc4\final_verify.py` | 本轮总验收 | 不透明背景逐字节 + 驱逐 A/B + 日常路径 + 开关 |
| `…\Temp\sc4\{ws,exp_pad2,verify_bytes,final}.json` | 中间产物**已落盘** | 应对 bash 掉线 |

---

## 5. 如何运行 / 如何验证

真实运行时 `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`；全部 `SDL_VIDEODRIVER=dummy`，中文输出 `sys.stdout.reconfigure(encoding='utf-8')`。

```bash
cd /mnt/g/气象/Typhoon-sim
P="/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe"
D="C:/Users/Yukioto/AppData/Local/Temp/sc4"
timeout 300 "$P" "$D/probe_ws_name.py"     # 键空间 + 边距浪费
timeout 200 "$P" "$D/verify_geom.py"       # 辉光几何
timeout 400 "$P" "$D/exp_pad2.py"          # 三档键集 A/B
timeout 200 "$P" "$D/diff3.py"             # 重叠贴边逐字节
timeout 400 "$P" "$D/final_verify.py"      # 总验收
# 回归（单文件 timeout 300）
timeout 300 "$P" -m pytest tests/test_ace_engine.py tests/test_panel_layer_row.py tests/test_render_layers.py tests/test_cache_store.py -q
timeout 300 "$P" -m pytest tests/test_paint.py -q
```

### 5.1 判据 ①：每帧真实光栅化次数与驱逐次数**同时下降**，字节**不涨**

**驱逐 A/B**（同一键集、同一预算 32 MiB；"真工作集"= 大预算下的实际占用）

| 键集 | 真工作集 旧 → 新 | 变化 | 32 MiB 驱逐 旧 → 新 |
|---|---|---|---|
| 248 键 | 14.40 → **11.44 MB** | **−20.6%** | 0 → 0 |
| **741 键**（≈ W5 的 708） | 35.75 → **28.39 MB** | **−20.6%** | **49 → 0** ✅ |
| 1236 键 | 51.86 → **41.18 MB** | **−20.6%** | 270 → 148 |

- **字节单调下降 20.6%**（三档一致）⇒ "字节不涨" ✅
- **驱逐在 741 键规模降到 0** ⇒ 与 W5 的键数量级（708）对应的判断成立 ✅
- 驱逐下降 ⇒ 被逐出后重渲染的次数同步下降 ⇒ **每帧真实光栅化次数下降** ✅
- 1236 键仍驱逐 148：**容量仍是有限的**（见 §6 MAJOR-2，如实记）

**日常路径不许回归**（12 台风）

| | items | bytes | misses | evictions |
|---|---|---|---|---|
| 旧 | 24 | 1.61 MB | 24 | **0** |
| 新 | 24 | **1.29 MB** | 24 | **0** ✅ |

⇒ 0/0 保持，且占用再降 20% ✅

### 5.2 判据 ②：像素等价性（**量化，不假装**）

**关键量：`alpha` 通道逐位相同；RGB 差异只出现在 `alpha==0` 的像素上。**

**（a）不透明背景（真实合成语境，最强判据）** —— 1280×720 不透明画布 + 60 个面（20 名 + 20 巅峰 + 20 登陆）：

```
旧 sha256 = a08f71a30e1043f2e0db09c87f73f207
新 sha256 = a08f71a30e1043f2e0db09c87f73f207      ★ 逐字节相同 = True
```

**不透明背景下 `alpha` 恒为 255 ⇒ 最终像素完全由 RGB 决定 ⇒ 这条直接证明"看不见任何差别"。**

**（b）逐样本独立画布**：67 个样本（20 名 + 40 标注 + 7 个边界 factor：0.5/0.8/1.0/1.1/1.5/2.25/3.0）⇒ **逐字节不同 0 个**。

**（c）大画布非重叠合成**（6000×4000）：`alpha` 差异 0、`RGB` 差异 0、**sha256 相同**。

**（d）重叠 + 贴边合成**（700×700，60 个面互相重叠 —— 真实屏幕上标注确实互相重叠）：

| 量 | 实测 |
|---|---|
| `alpha` 通道差异像素 | **0**（max diff **0**） |
| RGB 差异像素 | 9701 |
| **其中 `alpha>0`（可见）的** | **0** ✅ |
| 其中 `alpha==0`（全透明、永不参与合成）的 | 9701 |

**如实说明差异的性质**：那 9701 个像素是**全透明像素的 RGB 陈旧值**——它们在任何合成（含不透明背景、含 alpha 混合）下**贡献恒为 0**，所以 (a) 的逐字节相同就是它的证明。**不是"人眼可接受的最小差异"，而是"数学上不可见"**；差异确实存在，故不写"逐字节相同"，而是分开报 `alpha` 与"可见 RGB"。

**（e）表面尺寸确实变了**（这是本改动的**目的**，不是回归）：

| kind | text | factor | 旧 size/pad | 新 size/pad |
|---|---|---|---|---|
| name | WP03 | 2.25 | 180×112 / 14 | **166×98 / 7** |
| peak | Peak 60kt 950mb | 1.5 | 273×55 / 9 | **263×45 / 4** |
| name | 边界factor | 1.0 | 182×49 / 6 | 182×49 / 6（**不变**） |
| name | 边界factor | 3.0 | 538×147 / 18 | **520×129 / 9** |
| 样本表面字节合计 | — | — | 4448.70 KB | **3574.05 KB（−19.7%）** |

### 5.3 判据 ③：回退开关

```
GLOW_PAD_TIGHT=False -> pad(2.25)=14  pad(1.5)=9   (旧口径)
GLOW_PAD_TIGHT=True  -> pad(2.25)=7   pad(1.5)=4   (紧边距)
scale==1.0 保持 6: True
```
运行时置 `GLOW_PAD_TIGHT=False` **即刻生效**（`_glow_pad` 每次调用都读模块常量）。

### 5.4 判据 ④：回归

| 测试 | 基线 | 结果 |
|---|---|---|
| `test_ace_engine.py` | 19 passed | ✅（并入下面 44 passed） |
| `test_panel_layer_row.py` + `test_render_layers.py` + `test_cache_store.py` | — | ✅ |
| 上述四文件合计 | — | **44 passed in 8.73s** |
| `test_paint.py` | 15 passed / 49.0 s | **15 passed in 41.29s** ✅（pass 数与基线相同） |

未跑全量 pytest（铁律）。**W5 的 15 文件清单**（`21_fix_verification.md:107-133`）未整份复跑 —— 其中 `test_sim_throttle.py`（61 s）、`test_sim_settings.py`（13 s）与本改动无交集（本改动不改设置、不改帧调度）；已跑的是与本改动直接相关的 5 个文件。

---

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜app-feat 的 "0/0 ✅" 是探针把字号口径写小了 5.06 倍

- **现象**：同一改动，app-feat 测到 **0 miss / 0 eviction**（`18_fix_icon_cache_settings.md:83-84` 表格与 `:342` 结论），W5 测到 **204 eviction**（`21_fix_verification.md:332`）。W5 把差异归因为"场景更严"，Lead 据此判 FAIL。
- **实测根因**：`G:\tmp\perf\probe_name_cache.py:47` `names = [(f"TY{i:03d}", cats[...], **1.0**) ...]`、`:57` `peaks.append((f"{35+k%140}kt", cats[...], **1.0**))` —— **`name_factor` 硬编码 1.0**。
  而生产路径是 `name_factor = name_size/100 × 1.5 × zoom`（`_draw_icon_mixin.py:1280`），`config.json` 的 `name_size = 150` ⇒ **2.25**。
- **量化**：表面面积 ∝ factor² ⇒ (2.25/1.0)² = **5.06×**。
  app-feat 的 680 items / **7.96 MiB**（avg 11.7 KB）→ 按 5.06 放大 = **40.3 MB**，与 W5 的 708 键实际工作集 **~44.9 MB** 同量级（余下差额来自 app-feat 的合成标注 `"120kt"`（5 字符）比生产的 `"Peak 120kt 950mb"`（16 字符）窄）。
- **影响**：① **两个结论不是"场景不同"，而是"字号不同"** —— W5 的 FAIL 是生产代表值，app-feat 的 PASS 是探针产物；② 这个前提必须先纠正，否则"改键设计/表面尺寸"的方向会被误判（例如误以为只要压住键数就行）；③ **任何以 `name_factor=1.0` 做的缓存容量结论都低估 5 倍内存**。

### MAJOR-2｜−20.6% 是**在写域内的上限**，对 W5 的**确切**键集可能仍差 ~2 MB

- **实测**：`_glow_pad` 收紧把工作集单调压低 **20.6%**（三档键集一致），并在 **741 键**规模把驱逐打到 0。
- **但 W5 的真实键集平均每键更大**：W5 `after_summary.txt:34` 给出 528 items = 33,513,616 B ⇒ **63.5 KB/键**；我的复现键集是 35.75 MB / 741 = **48.3 KB/键**。
- **据此估算 W5 的真工作集**：708 × 63.5 KB = **44.9 MB** → 收紧后 **35.7 MB**，比 32 MiB（33.55 MB）**仍高约 2.1 MB** ⇒ **可能仍需驱逐**。
- **为什么不能直接量 W5 的确切键集**：`w5_all.py` 需要构造完整 `TySim`（它一次 RSS 就 1917 MiB，见 `21_fix_verification.md:407`），且需要 207 条真实台风数据（本仓 `typhoon/*/*.dat` 只有 **74** 个）。⇒ 只能给出估算区间，**不写成 PASS**。见 §7 U1。
- **影响**：本条改动是**严格更优**（三判据全部改善 + 零视觉变化），但**不足以断言 W5 转 PASS**。要**保证** 0 驱逐需要下一步（MAJOR-3）。

### MAJOR-3｜要保证 0 驱逐，下一步必须是"裁到非零 alpha 包围盒"（+ 改表面/偏移契约）

- **实测**：整面里 **47.3%** 的像素 `alpha≤1`（§3.2）。裁到 `alpha>0` 的包围盒 ⇒ 字节 **×0.527**（−47.3%），**比本轮多省 33.6%**。
- **投影**：W5 的 44.9 MB × 0.527 = **23.7 MB** ≪ 32 MiB ⇒ **保证 0 驱逐**，且余量 ~10 MB。
- **为什么本轮不能做**：裁到 `alpha>0` 的偏移是**逐文本不同**的，而 `_glow_pad(scale)` 是**与文本无关**的纯函数，签名里拿不到 text。要做到就得把契约从
  `surf = _get_name_surf(name,color,factor)` + `pad = _glow_pad(factor)`
  改成
  `surf, dx, dy = _get_name_surf_with_off(name,color,factor)`。
  消费点有 6 处，其中 `app/name_pos_dialog.py:325-328` **在本次写域之外**（它同时用 `_glow_pad(nf)` 与 `surf.get_size()` 定位预览框），不改它就会让「名字位置」预览的框整体偏移（最多 ~11 px，可见回归）。
- **建议的下一步（方案已给全）**：① 新增 `_name_surf_off(...) -> (surf, dx, dy)`（内部 `surf.get_bounding_rect(0)` 后 `surf.subsurface(...).copy()`）；② 把 `:1349/:1381` 与 `name_pos_dialog.py:325-328` 一起改成用返回的 `dx/dy`；③ 验收判据沿用本轮 §5.2 的"不透明背景逐字节"（裁到 `alpha>0` 后连 `alpha==0` 的 RGB 陈旧值都不再存在 ⇒ 连"透明像素 RGB 差异"也会消失，比本轮更强）。**估计收益 −47.3%，W5 必过。**

### MAJOR-4｜`SurfaceCache` 的 `max_bytes=0` 行为与 docstring 不符（我在自己探针里踩到）

- **现象**：`cache_store.py:28` 的 docstring 写"`max_bytes=0` 表示不限字节(只受 max_items 约束)"，但 `_trim`（`:99-101`）是
  ```python
  while self._d and (self._bytes > self.max_bytes or (self.max_items is not None and len(self._d) > self.max_items)):
  ```
  `max_bytes=0` 时条件恒为 `self._bytes > 0` ⇒ **一 `put` 就立刻把自己逐出**。
- **实测**：我第一版 `exp_pad.py` 用 `budget=0` 想测"无限预算真工作集"，结果 `items=0 / bytes=0.00 MB / evictions=741` —— 每个条目刚进就被踢。改用 `1<<40` 后正常。
- **影响**：任何按 docstring 传 `max_bytes=0` 想"关掉字节约束"的调用方会得到**完全失效的缓存**（每帧全 miss）。本仓当前没有调用方传 0（`_name_shadow_cache` 是 32 MiB、`_purple_frame_cache` 96 MiB、`_landfall_cache` 64 MiB），故是**潜伏**缺陷。修法：`_trim` 的条件加 `self.max_bytes > 0 and`，与 `put`（`:74`）保持一致。`cache_store.py` 不在本次写域，仅记录。

### MINOR-1｜`name_factor` 的 `*1.5` 放大是本次容量压力的主要来源（但不该动）

`_draw_icon_mixin.py:1280` 的 `name_size/100 × **1.5** × zoom` 让"100% 档"实际渲染 42 px（基准 28 px），
`config.json` 的 `name_size=150` ⇒ **63 px**。面积上它比"factor = name_size/100"大 **2.25 倍**。
这是**继承自旧实现的语义**（`:90-91` 注释：旧实现固定 28px 再 `smoothscale(×name_factor)`，`name_size=100 ⇒ factor 1.5`）⇒ **改它等于改用户看到的字号**，本轮**不动**。仅提示：若用户愿意把 `name_size` 从 150 调到 100，工作集直接降到 (1.5/2.25)² = 44% ⇒ 44.9 → **19.9 MB**，W5 稳过。这是**配置**层面的解法，不是代码调参。

### MINOR-2｜`peak_label_size` 已是 150，但 `pk_factor` **没有** 1.5 放大

`:1378` `pk_factor = peak_label_size/100 × zoom`（无 `*1.5`），而 `:1280` 名字有。所以同样是"150"，名字渲染 63 px、标注渲染 28 px。两套口径不一致 —— 可能是历史遗留（名字的 1.5 来自旧的位图缩放语义，标注后加时没跟）。**不改**（会改观感），但值得记录：**标注的"150%"与名字的"150%"不是同一个视觉量级**。

### MINOR-3｜两档口径在 factor=1.0 处不连续（表面尺寸跳 3px/侧）

`GLOW_PAD_TIGHT=True` 时 `_glow_pad(1.0)=6`（走 utils），`_glow_pad(1.001)≈3`（走本地实现）。表面尺寸在 factor 跨过 1.0 时跳变 3px/侧。**场景无跳变**（文字落屏位置由 `_glow_pad` 一致补偿，§5.2(b) 的 1.0 与 1.1 样本均逐字节相同），但若将来有代码读 `surf.get_size()` 做尺寸推断，会在这一点上不连续。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U1 | **W5 的确切 708 键集在改动后的驱逐数是否为 0**。我只能给出"741 键复现 ⇒ 0"与"按 W5 的 63.5 KB/键估算 ⇒ 35.7 MB，仍高 ~2.1 MB"两个区间 | 用 W5 的 `G:\tmp\w5\w5_all.py` 原样复跑（需完整 `TySim` + **207 条**真实台风数据；本仓只有 74 个 `.dat`）。**这是唯一能把本条从"估计"变"实测"的路径** |
| U2 | app-feat 的 680 items / 7.96 MiB 是否**正好**等于 factor=1.0 下的键集 | 我没有 `probe_name_cache.py` 的原始 `RESULT` json（`G:\tmp\perf\` 下只有脚本）。7.96 MiB 与我按 5.06× 反推的 44.9/5.06 = 8.87 MB 相差 11%，差额疑来自合成标注文本更短（`"120kt"` vs `"Peak 120kt 950mb"`）⇒ 属**可解释但未直接证实** |
| U3 | `text_core_rect(..., pad=_glow_pad(...))` 的**退化分支**（实心像素为空）在 pad 变小后的兜底矩形变化，对极淡/空文本的定位影响 | 需要构造"名字全为极淡像素"的个例（如 alpha 全 <200 的文本）跑对照。实测样本里没有这种文本 |
| U4 | 裁到 `alpha>0` 包围盒（MAJOR-3 方案）的**实际**收益 | 我按 240 样本的 47.3% 给出了投影；未实现，故未实测 |
| U5 | rotation/`zoom≠1` 的「名字位置」预览路径下的逐字节等价性 | 本轮所有判据都在 `zoom=1.0`；`name_pos_dialog` 的 `real_factor()` 可能取其他值。`_glow_pad` 的口径对任意 factor 自洽（§3.3 边界 2），但未对预览路径单独做逐字节对照 |
| U6 | `test_paint.py` 之外是否还有读 `_glow_pad`/表面尺寸的代码（写域外） | 已 `grep -rn "_glow_pad" app/` 全仓：只有本文件 6 处 + `name_pos_dialog.py:325,327`；`surf.get_size()` 的消费点未做全仓穷举 |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| **被改的函数** | `app/ty_sim_mixins/_draw_icon_mixin.py:_glow_pad`（改后 `:145-180`）+ 常量 `GLOW_PAD_TIGHT`（`:145`） |
| 回退点 | `output/exploration/_backup/state_v2/_draw_icon_mixin.py`（`881caa2b…`） |
| **生产字号口径（容量根因）** | `_draw_icon_mixin.py:1280`（`name_factor = name_size/100*1.5*zoom`）、`:1378`（`pk_factor` 无 1.5） |
| 键设计（已最小化） | `:1108`（name）、`:1075-1078`（peak）、`:1104-1105`（size 取代 factor 的注释） |
| **辉光几何（pad 的可证依据）** | `:195-204`（`pad` 与三层半径 `(3,2,1)`）、`:150-175`（`_ring_offsets`） |
| 表面尺寸/边距实测 | `…\Temp\sc4\probe_ws_name.py`、`ws.json` |
| 几何数值验证 | `…\Temp\sc4\verify_geom.py` |
| 驱逐 A/B（三档键集） | `…\Temp\sc4\exp_pad2.py`、`final_verify.py`、`final.json` |
| **逐字节等价性** | `…\Temp\sc4\final_verify.py`(①不透明背景)、`diff2.py`(隔离/大画布)、`diff3.py`(重叠贴边) |
| W5 的判定数据 | `G:\tmp\w5\after_summary.txt:31-34`、`G:\tmp\w5\w5_all.py:214-228` |
| **app-feat 的探针口径错误** | `G:\tmp\perf\probe_name_cache.py:47,57`（`name_factor` 硬编码 1.0）、`output/exploration/18_fix_icon_cache_settings.md:83-84,342` |
| 应用配置 | `config.json`：`name_size=150`、`peak_label_size=150` |
| `_glow_pad` 的写域外消费点 | `app/name_pos_dialog.py:71`（import）、`:325,327`（`noff`/`loff`） |
| `SurfaceCache` 的 `max_bytes=0` 缺陷 | `app/cache_store.py:28`（docstring）vs `:99-101`（`_trim`）、`:74`（`put` 有 `>0` 守卫） |
| 回归 | `tests/test_ace_engine.py`(19)、`test_panel_layer_row.py`、`test_render_layers.py`、`test_cache_store.py`、`test_paint.py`(15) |
