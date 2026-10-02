# 28 关掉最后一个 FAIL：裁 alpha 包围盒 + `SurfaceCache._trim` 守卫

## 一句话定位

任务 A：把文字表面从"整面"裁到 **`alpha >= 1` 紧包围盒**，文字原点改成**逐文本**的 `(dx, dy)` 并同步 `name_pos_dialog`；任务 B：给 `SurfaceCache._trim` 补 `> 0` 守卫。结果：**741 键工作集 28.39 → 17.78 MB（−37.4%）**、32 MiB 下**驱逐 0**、**不透明画布逐字节相同**、**预览框零偏移**；B 让 `max_bytes=0` 恢复"不限字节"语义。

> **本轮最重要的三条过程记录**（都是"差点写出假结论"）：
> 1. **`Surface.get_bounding_rect(0)` 返回整幅** —— 我的第一版 `_tight_crop` 因此**静默变成 no-op**（实测可裁 **0 px**），而 `SURF_TIGHT_CROP=True/False` 测出来一模一样。是"两臂完全相同、收益正好 0.0%"这个反常现象把它揪出来的。
> 2. **我自己的验收脚本第一版有一个假通过**：`arm()` 把预算写死成 `1<<40`，于是"32 MiB 驱逐"那一列**恒为 0**（看起来完美）。自查代码时才发现，修正后才有真实数字。
> 3. **上一轮我把可裁量估成 −33.6%（→23.7 MB），依据是旧 pad 表面的 47.3% 浪费** —— 紧边距之后真实可裁是 **39.4%**；两个数都写在报告里，不掩盖。

---

## 1. 清单与职责

| 任务 | 文件 | sha256 改前（`state_v3`） → 改后 | 行数 | 改动规模 |
|---|---|---|---|---|
| **B** | `app/cache_store.py` | `99fdcf81…e6870` → **`4ea9c552…21d8d`** | 118 → 122 | `_trim` 1 个条件 + 5 行注释 |
| **A** | `app/ty_sim_mixins/_draw_icon_mixin.py` | `be89079e…6bc2f` → **`6df1753f…4785b`** | 1806 → 1892 | **+127 / −41** |
| **A** | `app/name_pos_dialog.py` | `c2ffb92e…e50d4` → **`f38972b6…49352`** | 608 → 611 | **+13 / −10**（含 1 个 import 清理） |
| — | `output/exploration/_backup/state_v3/*` | 回退点，**未动** | — | — |

**A 与 B 分两次改、分两次验**（B 先做，见 §5.5；A 后做，见 §5.1-5.4）。

**回退**（`state_v3` 是上一轮结束时的状态）

```bash
cp output/exploration/_backup/state_v3/cache_store.py           app/cache_store.py           # 只退 B
cp output/exploration/_backup/state_v3/_draw_icon_mixin.py      app/ty_sim_mixins/
cp output/exploration/_backup/state_v3/name_pos_dialog.py       app/name_pos_dialog.py       # 退 A
```

**运行时软开关**（A 与 B 都有，互不影响）

```python
import app.ty_sim_mixins._draw_icon_mixin as M
M.SURF_TIGHT_CROP = False     # 关闭紧裁, 立刻回到整面 + _glow_pad 定位
M.GLOW_PAD_TIGHT  = False     # (上一轮的开关, 仍在)
from app.cache_store import SurfaceCache
SurfaceCache("x", 0)          # B: max_bytes<=0 = 不限字节
```

---

## 2. 数据流 / 调用链

### 2.1 表面与偏移的产生（**同源**是设计要点）

```
_name_surf_off(name,color,name_factor) / _label_surf_off(label,color,pk_factor)
  ├── key = ('name'|'peak', text, tuple(color), size)          size = round(BASE*factor)
  ├── off = _rt_surf_off.get(key) ; surf = _name_shadow_cache.get(key)
  ├── if surf is None or off is None:          ← 任一缺失 ⇒ 重渲染(绝不混用旧偏移)
  │     full = _sized_glow_text(_sized_font(cache,size), text, color, factor)
  │     surf, dx, dy = _tight_crop(full, _glow_pad(factor), key)
  │     _rt_surf_off[key] = (dx, dy)           ← 先登记偏移
  │     _name_shadow_cache.put(key, surf)      ← 再写缓存
  └── return surf, dx, dy                      ← 表面与偏移恒同源
```

`_get_name_surf` / `_get_peak_label_surf` 保留为**只返回表面**的薄包装（向后兼容），
但要定位**必须**用 `_*_surf_off`。

### 2.2 紧裁

```
_tight_crop(full, pad, key):
    a  = pygame.surfarray.array_alpha(full)     # (w,h) uint8
    nz = np.nonzero(a)                          # ← alpha >= 1(不是 >1!)
    x0,x1,y0,y1 = nz 的极值
    若已是最紧 / 全透明 / 空文本 → (full, pad, pad)
    否则 → (full.subsurface(rect).copy(), pad-x0, pad-y0)
```

### 2.3 消费点（`(dx, dy)` 的传播）

| 消费点 | 改前 | 改后 |
|---|---|---|
| `draw_typhoon_name` 名字 blit | `(text_x - _glow_pad(name_factor), ty_pos - ...)` | `(text_x - _ndx, ty_pos - _ndy)`，来自 `_name_surf_off` |
| 交叉淡入 `old_surf`（名字切换） | 复用同一个 `name_off` | **各自**取 `_odx/_ody`（旧名字的偏移不同！） |
| `_name_bottom`（标注的 y 基准） | `(ty_pos - name_off) + _name_core(...).bottom` | `(ty_pos - _ndy) + ...` |
| 3 处标注 blit（`label_override` / 巅峰 / 登陆） | 共用一个 `_lbl_x` | **每条标注**用 `_label_surf_off(_txt,_col,pk_factor)` 的 `dx` |
| `_name_core` / `_label_core` | 传 `_glow_pad(factor)` 当 `pad` | 传真实 `(dx,dy)`；返回**裁剪面相对**的 ink 框（下游算式不变） |
| `_label_layout` | 用 `_label_core(...).top/.h` 排槽位 | **无需改** —— 它只用"表面相对"的 ink 框，自动跟随裁剪 |
| `name_pos_dialog._scene` | `_glow_pad(nf)` + `surf.get_size()` | `_name_surf_off` / `_label_surf_off` 的偏移 |
| `name_pos_dialog` 命中框 `_hit`（`:597-605`） | `rect.x + core.x` | **无需改** —— 该式在裁剪前后算出的都是同一个"画布 ink 位置" |

---

## 3. 核心机制详解

### 3.1 为什么必须用 `(dx, dy)` 而不是标量 `_glow_pad`

实测（`MEGI`，`name_size=150` ⇒ factor 2.25）：

| | 整面（`SURF_TIGHT_CROP=False`） | 紧裁（True） |
|---|---|---|
| 表面尺寸 | 166 × 98 | **155 × 62** |
| 文字原点在表面内 | `(7, 7)` = `_glow_pad(2.25)` | **`(3, -11)`** |

**`dy = -11` 是负的** —— 即"文字原点"落在裁剪面的**上边界之外**（因为 `get_bounding_rect` 只保留 `alpha>=1` 的像素，而字形上方的行框空白被裁掉了，辉光最上缘比字形顶还高一截）。
⇒ **任何标量 `_glow_pad(scale)` 都无法表达这个位置**，这正是必须把契约改成三元组的根本原因，也是"不改 `name_pos_dialog` 必然偏移"的来源。

### 3.2 `_tight_crop` 的两个 API 陷阱（本轮实测）

**陷阱 1：`Surface.get_bounding_rect(0)` 返回整幅**

```
=== get_bounding_rect 语义测试(SDL 2.32.10 / pygame-ce 2.5.7) ===
  造: 20x20 全透明 + (5,5,2,2) alpha=1 + (10,10,2,2) alpha=3
  get_bounding_rect(0) = (0, 0, 20, 20)      ← 整幅! 等于不做过滤
  get_bounding_rect(1) = (5, 5, 7, 7)        ← 对应 alpha > 1
  get_bounding_rect(2) = (10, 10, 2, 2)      ← 只 alpha=3
  numpy alpha>=1 bbox  = x[5,11] y[5,11]
```

第一版 `_tight_crop` 用的就是 `get_bounding_rect(0)` ⇒ **静默 no-op**。
证据是那次验收里 `SURF_TIGHT_CROP` 两臂**完全相同**、工作集变化 **+0.0%**：

```
keys= 741 真工作集 28.39 -> 28.39 MB (+0.0%) | 32MiB 驱逐 0 -> 0      ← 假通过
False -> size=(166, 98) off=(7,7)   True -> size=(166, 98) off=(7,7)   ← 开关无效果
```

**陷阱 2：`get_bounding_rect(1)` 会丢 `alpha == 1` 的像素**

上表里 `alpha==1` 的像素**确实存在**（4 个）。用 `get_bounding_rect(1)` 裁会把它们丢掉 ⇒ **alpha 通道不再逐位相同** ⇒ 不满足本任务的等价性判据。
⇒ 唯一可证无损的取法是 **numpy `alpha >= 1`**（`pygame.surfarray.array_alpha` + `np.nonzero`，`numpy` 本文件早已 import）。

### 3.3 真实可裁量：39.4%（不是 47.3%，也不是 0%）

**逐样本（紧边距表面上，`alpha>=1` 包围盒 vs 整面）**

| text | factor | pad | 整面 | `alpha>=1` 包围盒 | 可裁 px | 可裁 % |
|---|---|---|---|---|---|---|
| MEGI | 2.25 | 7 | 166×98 | 155×62 @4,18 | 6658 | 40.9 |
| PULASAN | 2.25 | 7 | 280×98 | 271×62 @5,18 | 10638 | 38.8 |
| SON-TINH | 2.25 | 7 | 318×98 | 308×62 @4,18 | 12068 | 38.7 |
| HAIKUI | 2.25 | 7 | 242×98 | 230×62 @5,18 | 9456 | 39.9 |
| WP01 | 2.25 | 7 | 166×98 | 161×62 @2,18 | 6286 | 38.6 |
| Peak 120kt 950mb | 1.5 | 4 | 280×45 | 276×29 @2,8 | 4596 | 36.5 |
| Landfall 85kt | 1.5 | 4 | 229×45 | 223×29 @4,8 | 3838 | 37.2 |
| MEGI | 1.5 | 4 | 108×64 | 102×39 @2,12 | 2934 | 42.4 |
| MEGI | 3.0 | 9 | 218×129 | 205×81 @5,24 | 11517 | 41.0 |
| MEGI | 0.8 | 2 | 56×34 | 53×20 @1,7 | 844 | 44.3 |
| **合计** | | | | | | **39.4%** |

**诚实标注**：上一轮（26 号报告 MAJOR-3）我写的是"整面里 **47.3%** 的像素 `alpha<=1` ⇒ 再裁可省 33.6%"。那个 47.3% 是在**旧 pad（`round(6·scale)`）**的表面上量的，里面混了已经被 `GLOW_PAD_TIGHT` 去掉的那一段死边距；在紧边距表面上真值是 **39.4%**。两者都列在此，不做掩盖。

### 3.4 为什么"裁到包围盒"就是无损的

裁掉的全是 `alpha == 0` 的像素。`blit` 的混合式是 `dst = src*srcA + dst*(1-srcA)`，`srcA=0` ⇒ 恒等于 `dst`。
把表面裁到"所有 `alpha>=1` 像素的最小外接矩形"，再让 blit 原点左移/上移同样的 `(x0, y0)`，**每个非零像素落在画布上的绝对坐标不变**，被移除的像素本来就不写任何东西 ⇒ **画布字节逐位不变**。
这是**可证**的，不是经验判断；§5.1/5.3 的三重逐字节证据是它的实测确认。

---

## 4. 输入输出资产

| 路径 | 角色 | 要点 |
|---|---|---|
| `app/ty_sim_mixins/_draw_icon_mixin.py` | **A 的被改文件** | 1806 → 1892 行；`be89079e…` → `6df1753f…`；新增 `SURF_TIGHT_CROP`、`_rt_surf_off`、`_tight_crop`、`_name_surf_off`、`_label_surf_off`；`text_core_rect` 接受 `(px,py)` |
| `app/name_pos_dialog.py` | **A 的被改文件** | 608 → 611 行；`_scene` 改用 `_name_surf_off`/`_label_surf_off`；移除随之失效的 `_glow_pad` import |
| `app/cache_store.py` | **B 的被改文件** | 118 → 122 行；`_trim` 补 `> 0` 守卫 |
| `output/exploration/_backup/state_v3/` | 回退点（未动） | 含三个文件的改前副本 + `SHA256_STATE_V3.txt` |
| `…\Temp\sc5\verifyA.py` | 本轮探针 | 像素等价 / 工作集 / 日常路径 / 开关 |
| `…\Temp\sc5\verify_preview.py` | 本轮探针 | **调用真实 `NamePosDialog._scene`** 比 ink 框 |
| `…\Temp\sc5\e2e.py` | 本轮探针 | **端到端真实 `draw_typhoon_name`** 逐字节 |
| `…\Temp\sc5\diag.py` / `diag2.py` | 本轮探针 | `get_bounding_rect` 语义 / 可裁量 |
| `…\Temp\sc5\{verifyA,preview,e2e}.json` | 中间产物**已落盘** | 应对 bash 掉线 |
| `_rt_surf_off`（运行时） | 新增内存 | 每键 2 个 int；741 键 ≈ 数十 KB，**有界**（键空间 = `(kind,text,color,size)`） |

---

## 5. 如何运行 / 如何验证

真实运行时 `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`；`SDL_VIDEODRIVER=dummy`；中文输出 `sys.stdout.reconfigure(encoding='utf-8')`。

```bash
cd /mnt/g/气象/Typhoon-sim
P="/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe"
D="C:/Users/Yukioto/AppData/Local/Temp/sc5"
timeout 200 "$P" "$D/diag2.py"          # get_bounding_rect 语义 + 可裁量
timeout 500 "$P" "$D/verifyA.py"        # 判据 ①②③④
timeout 200 "$P" "$D/verify_preview.py" # 判据: 预览框偏移
timeout 200 "$P" "$D/e2e.py"            # 端到端 draw_typhoon_name
# 回归(单文件 timeout 300)
timeout 300 "$P" -m pytest tests/test_ace_engine.py tests/test_panel_layer_row.py tests/test_render_layers.py tests/test_cache_store.py -q
timeout 300 "$P" -m pytest tests/test_paint.py -q
```

### 5.1 验收 ① 像素等价性（三条独立证据）

| 证据 | 判据 | 结果 |
|---|---|---|
| **端到端** 真实 `draw_typhoon_name`（预览路径：`name_override` + `label_override` + `zoom` 0.7/1.0/1.3，6 个名字） | 不透明画布 sha256 | 旧 `9edb7985…5fa2` = 新 `9edb7985…5fa2` ✅ |
| 同上，`SRCALPHA` 画布 | `alpha` 差异像素 / 可见 RGB 差异 | **0 / 0** ✅ |
| 独立合成（20 名 + 40 标注，重叠贴边，1280×720） | 不透明画布 sha256 | 旧 = 新 **逐字节相同** ✅ |
| 同上，`SRCALPHA` | `alpha` 差异 / 可见(alpha>0) RGB 差异 | **0 / 0** ✅ |

> `SRCALPHA` 臂里 RGB 差异有 127079 个像素，但**全部 `alpha == 0`**（全透明、永不参与合成）；不透明背景那条逐字节相同就是它的证明。**不写"逐字节相同"，分开报 `alpha` 与"可见 RGB"。**

### 5.2 验收 ② 工作集 / 驱逐（32 MiB 预算不变）

| 键集 | 真工作集 上一轮 → 本轮 | 变化 | 32 MiB 驱逐 |
|---|---|---|---|
| 248 键 | 11.44 → **7.15 MB** | −37.5% | 0 → **0** |
| **741 键**（≈ W5 的 708） | 28.39 → **17.78 MB** | **−37.4%** | 0 → **0** ✅ |
| 1236 键 | 41.18 → **25.82 MB** | −37.3% | 0 → **0** ✅ |

**累计（对 W5 的判据）**：上一轮 W5 的真实工作集实测 **33,513,616 B = 44.9 MB（估算全量）**。
- 轮六（`GLOW_PAD_TIGHT`）−20.6% ⇒ 35.7 MB（仍高 2.1 MB ⇒ 我上轮标 unresolved）
- **本轮（`SURF_TIGHT_CROP`）再 −37.4% ⇒ ≈ 22.4 MB**，比 32 MiB **低约 11.1 MB** ⇒ **预期 0 驱逐**
（用本轮 741 键的绝对值比 17.78 / 35.75 = 0.4973 推算；与上一轮投影的 ~23.7 MB 同量级。）

### 5.3 验收 ③ 预览框定位**不偏移**（方案里点名的风险点）

**调用的是真实 `NamePosDialog._scene`**（未绑定方法 + 假 `self`），比较两臂在**场景坐标**下的 ink 框（即 `name_pos_dialog.py:597-605` 命中判据的基准 `rect.x + core.x`）：

| 场景 | 旧 name_ink | 新 name_ink | 一致 |
|---|---|---|---|
| TY MEGI | (19, −1, 145, 52) | (19, −1, 145, 52) | ✅ |
| TY PULASAN | (20, −1, 262, 52) | (20, −1, 262, 52) | ✅ |
| TY SON-TINH | (19, −1, 299, 52) | (19, −1, 299, 52) | ✅ |
| EX HAIKUI | (20, −1, 220, 52) | (20, −1, 220, 52) | ✅ |
| TD WP01 | (17, −1, 151, 52) | (17, −1, 151, 52) | ✅ |
| TY `x` | (20, 11, 32, 40) | (20, 11, 32, 40) | ✅ |

**`label_ink` 6/6 亦完全一致（17, 53, 169, 25）** ⇒ **ink 框不一致样本 = 0 / 6 ✅**
（`name_surf_rect` 会变小，例：`(10,-24,166,98)` → `(14,-6,155,62)` —— 那是紧裁的**目的**；判据是它加 `core` 后的 ink 框不变。）

**12 台风日常路径**：`items=24`，字节 **1.29 → 0.80 MB**，`evictions = 0`（不回归）✅

### 5.4 验收 ④ 回退开关

```
SURF_TIGHT_CROP=False -> size=(166, 98) off=(7,7)      (整面, 旧口径)
SURF_TIGHT_CROP=True  -> size=(155, 62) off=(3,-11)    (紧裁)
```
运行时改模块常量**即刻生效**（`_tight_crop` 每次调用都读）。

**健壮性（实测）**：首次 / 缓存命中 / 外部 `cache.clear()`（`keyboard_mixin.py:48` 就是这么干的）/ 替换缓存对象 —— 四种情形返回的 `(surf, dx, dy)` **全部正确**（表面与偏移同源产生 ⇒ 不会混用旧偏移）。

### 5.5 任务 B 验收（独立完成、独立验证）

| 检查 | 改前 | 改后 |
|---|---|---|
| `SurfaceCache("x", 0)` + `put` 1 个（3200 B） | `len=0` / `evictions=1`（**一 put 就自逐**） | **`len=1` / `evictions=0`** ✅ |
| `max_bytes=1<<40`（"不限"的等价写法） | `len=1` / `evictions=0` | `len=1` / `evictions=0`（一致） |
| `max_items=2`、5 次 put | `len=2` / `evictions=3` | **不变** ✅ |
| 字节预算 6000 B、每张 3200 B、3 次 put | `len=1` / `evictions=2` | **不变** ✅ |
| 单张 1 MB > 6000 B 预算 | `put=False` / `len=0` | **不变** ✅ |
| `tests/test_cache_store.py` | — | **11 passed** ✅ |

### 5.6 回归

| 测试 | 基线 | 结果 |
|---|---|---|
| `test_paint.py` | 15 passed / 41.29 s | **15 passed in 41.60s** ✅ |
| `test_ace_engine.py` | 19 passed | ✅（并入下条 44） |
| `test_panel_layer_row.py` + `test_render_layers.py` + `test_cache_store.py` | — | ✅ |
| 上述四文件合计 | — | **44 passed in 8.49s** ✅ |

未跑全量 pytest（铁律）。

---

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜`Surface.get_bounding_rect(0)` 返回整幅 —— 一个"静默 no-op"陷阱

- **现象**：`get_bounding_rect(0)` 在 SDL 2.32.10 / pygame-ce 2.5.7 上返回 `(0,0,w,h)`，等于不做过滤。用它做"裁到 alpha>0 包围盒"的优化会**静默失效**：不报错、不抛异常、尺寸看起来"合理"。
- **证据**：`diag2.py` 的语义测试（`get_bounding_rect(0)=(0,0,20,20)` vs `(1)=(5,5,7,7)`）；以及第一版 `_tight_crop` 的运行结果 —— `SURF_TIGHT_CROP` 两臂完全相同、工作集变化 **+0.0%**、`off` 恒为 `(7,7)`。
- **影响**：① 本轮因此白跑一轮实现（幸好验收是"两臂对照 + 预期 −33.6%"，**收益正好为 0** 这个反常值暴露了它）；② 任何同类优化（如渲染层的精灵裁剪）用这个 API 都会无声失效。**正确写法**：`np.nonzero(pygame.surfarray.array_alpha(surf))`。

### MAJOR-2｜`get_bounding_rect(1)` 不能用于"要求逐位等价"的场合

- **现象**：`get_bounding_rect(1)` 只收 `alpha > 1`，会丢 `alpha == 1` 的像素（实测确实存在，如某个测试面里有 4 个）。
- **证据**：`diag2.py` 语义测试；`alpha==1 像素数 = 4`。
- **影响**：若用 `(1)` 来裁，`alpha` 通道就**不再逐位相同**，本任务的等价性判据（alpha 逐位 + 可见 RGB 为 0）会失败 —— 虽然视觉上不可见，但**不能声称逐字节等价**。⇒ 本实现用 numpy `alpha >= 1`。

### MAJOR-3｜我自己的验收脚本第一版存在"假通过"（过程记录）

- **现象**：`verifyA.py` 第一版里 `arm()` 把 `SurfaceCache` 的预算**写死成 `1<<40`**，而外层循环仍按 `32*1024*1024` 标注 —— 于是"32 MiB 驱逐"那一列**两臂恒为 0**，看起来完美通过。
- **发现方式**：写完后自查 `arm()` 的签名，发现 `budget` 从来没有被传进去；改成 `arm(tight, budget, items)` 后真实数字才出来（round 6 的 `exp_pad2.py` 是正确的，所以那轮数字可信）。
- **影响/教训**：**"所有判据都通过"本身需要被怀疑一次**。本轮因此固定了两个习惯：① 每个判据都要有一个"预期会失败/会变化"的对照臂；② 任何"两臂完全相同"的结果都要先怀疑是开关没生效（MAJOR-1 正是被这条抓住的）。

### MAJOR-4｜被改的代码路径**没有专用测试**

- **现象**：`grep -rln "draw_typhoon_name|name_pos_dialog|NamePosDialog|_get_name_surf" tests/` **0 命中**。
- **证据**：全仓只有 `name_pos_dialog.py:585` 与 `_draw_icon_mixin.py:681` 调用 `draw_typhoon_name`；`tests/` 下无任何文件引用这些符号。
- **影响**：`test_paint`（15 passed）只覆盖路径图绘制，**不画台风名字**；本轮的像素等价性与预览框一致性**完全依赖我临时写的探针**，没有回归网。建议下一轮把 `verify_preview.py` / `e2e.py` 的核心断言固化成 `tests/test_name_pos_preview.py`（判据现成：ink 框一致 + 不透明画布 sha256）。

### MINOR-1｜`_rt_surf_off` 不会被 `_name_shadow_cache.clear()` 同步清理

`keyboard_mixin.py:47-48` 只清 `_name_shadow_cache`（不在本次写域）。`_rt_surf_off` 会在那里留下同键的偏移条目。
**实测无正确性影响**：`_name_surf_off` / `_label_surf_off` 要求 `surf is not None and off is not None`，`clear()` 后 `surf is None` ⇒ 重渲染并**用同一键覆盖**偏移 ⇒ 永不错配（已实测 4 种情形）。代价只是 `_rt_surf_off` 的大小略大于活跃缓存（741 键 × 2 int ≈ 数十 KB，有界）。

### MINOR-2｜上一轮的可裁量估计偏高（47.3% → 真值 39.4%）

见 §3.3。26 号报告 MAJOR-3 写的"再省 33.6% / → 23.7 MB"依据的是**旧 pad 表面**上量的 47.3% 浪费；紧边距后真值 39.4%，实测本轮降幅 −37.4%（绝对量 28.39 → 17.78 MB）。**结论方向与量级都对，但当时的百分比混入了已被 `GLOW_PAD_TIGHT` 消掉的那段死边距。** 本轮给出修正值。

### MINOR-3｜`_tight_crop` 的 `key` 参数目前未使用

签名保留了 `key`（原打算在其中登记偏移），实际登记在做调用方的 `_*_surf_off` 里。保留它便于日后需要"按键查偏移"时直接扩展，但当前是未使用参数 —— 属清洁度问题。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U1 | **W5 的 708 键确切键集在改动后的驱逐数**。本轮把它从"估 35.7 MB（高 2.1 MB）"压到"估 **22.4 MB**（低 11.1 MB）"，但仍是**估算**，未用 W5 harness 原样复跑 | 用 `G:\tmp\w5\w5_all.py` 复跑（需完整 `TySim` + **207** 条真实台风；本仓只有 **74** 个 `.dat`）。这是把 U1 从"估算"变"实测"的唯一路径 |
| U2 | `_rt_surf_off` 的**长期**上界与是否需要跟随缓存淘汰 | 需要一次长会话（多海域、字号滑遍）的实测。当前判断：键空间有界 ⇒ 数十 KB 量级；无正确性风险（§6 MINOR-1） |
| U3 | `text_core_rect` 退化分支（`alpha>=200` 为空）在两轴偏移不同后的行为 | 需要构造"极淡文本"个例（现有样本都没有触发该分支）。本轮已把 `pad` 扩展成 `(px,py)` 使语义正确，但**未实测**该分支 |
| U4 | `zoom≠1` 的「名字位置」预览路径在**真实对话框**里（而非假 `self`）的逐字节一致 | 本轮用真实 `_scene` + 真实 `draw_typhoon_name` 分别验了；但没构造完整 `NamePosDialog`（需 `TySim`）。`e2e.py` 已覆盖 `zoom` 0.7/1.0/1.3 的绘制路径 |
| U5 | 裁到 `alpha>=1` 后**深层**是否还有可裁空间（如把辉光/描边合成改为按需） | 需要一次渲染管线的重构级分析；当前 `alpha<=1` 像素已只剩"字体行框内、字形之外"的部分，且**它们在包围盒内部**（矩形裁不掉） |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| **A 的开关与常量** | `_draw_icon_mixin.py`：`SURF_TIGHT_CROP`、`_rt_surf_off` |
| **紧裁实现** | `_draw_icon_mixin.py:_tight_crop`（含两个 API 陷阱的注释） |
| 表面+偏移的同源入口 | `_name_surf_off` / `_label_surf_off`；薄包装 `_get_name_surf` / `_get_peak_label_surf` |
| ink 框（`pad` 现接受 `(px,py)`） | `_draw_icon_mixin.py:text_core_rect`、`_name_core`、`_label_core` |
| 名字/标注的定位点 | `draw_typhoon_name`：名字 blit、交叉淡入 `old_surf`、`_name_bottom`、3 处标注 blit（各自 `_label_surf_off`） |
| `zoom`/字号口径 | `:1396`（`name_factor = name_size/100*1.5*zoom`）、`:1413`（`pk_factor` 无 1.5） |
| **预览框定位** | `app/name_pos_dialog.py:_scene`（`:305-340`）、命中框 `_hit`（`:597-605`） |
| **B 的改动** | `app/cache_store.py:_trim`（`max_bytes > 0` 守卫）；docstring `:28` 是"不限字节"的语义来源 |
| `get_bounding_rect` 语义证据 | `…\Temp\sc5\diag2.py`（含 alpha=1 像素的对照面） |
| 本轮三个探针 | `…\Temp\sc5\verifyA.py`、`verify_preview.py`、`e2e.py`（+ `diag.py`/`diag2.py`） |
| 上轮回退点 | `output/exploration/_backup/state_v3/` + `SHA256_STATE_V3.txt` |
| 上一轮报告（含被修正的 47.3%） | `output/exploration/26_fix_name_cache_design.md` §3.2 / MAJOR-3 |
| 回归 | `tests/test_paint.py`、`test_ace_engine.py`、`test_panel_layer_row.py`、`test_render_layers.py`、`test_cache_store.py` |
