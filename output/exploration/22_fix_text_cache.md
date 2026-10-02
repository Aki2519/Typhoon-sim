# 22 修复：`rt()` 默认分支的文本表面缓存（字节预算版）

## 一句话定位

在 `app/constants/fonts.py` 的 `rt()` 默认分支前置一层"字节预算 + 真 LRU"的文本表面缓存，把 `PaintDialog` 稳态每帧 **139–140 次真实字形光栅化降到 0–1.25 次**，`evictions` 恒为 **0**，输出逐字节不变，并留了模块级回退开关。

> **本轮最重要的一句话**：`rt()` 默认路径**并非"不缓存"** —— `SmartFont.render` 自带一个 **128 条 FIFO** 缓存，它就在默认路径上。真因是 **实测工作集 139 键 > 128**，缓存每帧被自己冲掉（**自噬**）。这改变了修法：不是"从零加缓存"，而是"把条数上限换成字节预算 + 真 LRU，且容量必须盖住工作集"。

---

## 1. 清单与职责

| 文件 | 改动 | 行数变化 | 职责 |
|---|---|---|---|
| `app/constants/fonts.py` | **唯一被改的源码** | 217 → 284（**+69，-2**） | 新增 `RT_TEXT_CACHE_BYTES` / `_rt_cache` / `_rt_font_refs` / `rt_cache_stats()` / `rt_cache_clear()`；只改 `rt()` 的 `max_width is None` 分支 |
| `app/cache_store.py` | **未改**（只 import） | — | 复用现成的 `SurfaceCache`（字节预算 + 真 LRU + hits/misses/evictions） |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | **未改** | — | 既有 `_name_shadow_cache`（32 MiB / 1024 条）与本次**键空间不重叠**，见 §3.5 |
| `app/dialog_base.py`、`app/input_field.py` | **未改**（写域外） | — | 它们直连 `f.render()` **绕过 `rt()`**，是本次**修不到**的残余，见 §3.6 |
| `output/exploration/_backup/fonts.py` | 未改（回退点） | 217 | `sha256 = a32bbdc4…13d3` |

**改动前后的 SHA256**

| | sha256 |
|---|---|
| 改前（= `_backup/fonts.py`，与 `README_ROLLBACK.md:40` 一致） | `a32bbdc436cb74fde5a79ed2ac1b992ef5279d8cc81017a1ccd51548a2f413d3` |
| 改后 | `f17d22a70e1657758cebb7360493404139eac98cdf668ba00ca61448f8e752c6` |

**回退方式**（二选一，均 1 条命令）

```bash
cp output/exploration/_backup/fonts.py app/constants/fonts.py          # 文件级
# 或运行时软开关（不必改文件）：
python -c "from app.constants import fonts as F; F.RT_TEXT_CACHE_BYTES = 0"   # 立即回到旧行为
```

`diff -u _backup/fonts.py app/constants/fonts.py` 共 **+69 / −2 行**，全部集中在两处：① `from ..cache_store import SurfaceCache`；② `rt()` 之前插入缓存块 + `rt()` 默认分支改写。`SmartFont`、`_load_font`、`max_width` 分支**一行未动**（`if not hasattr(rt, "_cache"):` 起完全保留，见 §3.7 的 diff 尾段）。

---

## 2. 数据流 / 调用链

```
调用方 (220 处 rt() 调用点)
  └─ rt(f, text, color, max_width=None, smooth=True)          fonts.py:161(改前) / :219(改后)
       ├── max_width is None  ← 本次改的分支（220 处里的 213 处）
       │     改前: return f.render(text, smooth, color)                       ← 直连
       │     改后: _rt_cache(SurfaceCache, 8 MiB) 命中 → 直接返回
       │            未命中 → f.render(...) → _rt_font_refs 钉住 f → _rt_cache.put
       │                                │
       │                                └─ SmartFont.render(text, antialias, color)
       │                                     ├── 自带 _cache: dict, maxsize=128, FIFO
       │                                     │   （fonts.py:94 / :117-130）
       │                                     └── 未命中 → font.render(seg, ...) ← 真正的字形光栅化
       └── max_width > 0  ← **行为完全不变**（7 处调用点）
             rt._cache: OrderedDict, 条数上限 1024, id(f) 键
             → f.render(ln, ...) 每行一次（fonts.py:208，同样绕过本次缓存）
```

**本次缓存键**：`(id(f), text, color, bool(smooth))`；值 = `pygame.Surface`。
**容量语义**：`SurfaceCache` 按 `宽×高×4` 计字节（`cache_store.py:19-24`），超预算时 `popitem(last=False)` 真 LRU 淘汰并 `evictions += 1`（`cache_store.py:98-104`）。

---

## 3. 核心机制详解

### 3.1 先证伪诊断前提：默认路径**本来就有缓存**

派活单写的是"`max_width is None` 那条路现在直接渲染 ⇒ 给它加缓存"，app-feat 的文档写的是"`rt()` 的默认路径**不缓存**"（`15_perf_data_ui.md:28`）、"单行渲染没有"（`:132`）。**这两句都不准确**：

```python
# app/constants/fonts.py:90-130（改前，未改动）
class SmartFont:
    def __init__(self, en_font, zh_font, maxsize=128):
        self._cache: Dict[...] = {}
        self.maxsize = maxsize                      # ← 128

    def render(self, text, antialias, color):
        key = (text, color, antialias)
        if key in self._cache:
            return self._cache[key]                 # ← 命中就返回，不重新光栅化
        if len(self._cache) >= self.maxsize:
            self._cache.pop(next(iter(self._cache)))  # ← FIFO 淘汰
        ...
        surf = font.render(text, antialias, color)   # ← 只有 miss 才真光栅化
        self._cache[key] = surf
        return surf
```

`rt()` 默认分支调用的就是 `f.render(...)`，**而 `f` 是 `SmartFont`** ⇒ **默认路径有缓存，且带 128 条上限**。所以"220 处调用点绕过缓存"这个说法在机制上不成立（它们绕过的只是 `rt()` 自己那个 **`max_width` 专用** `OrderedDict`）。

### 3.2 真因：**139 个键 > 128 条上限 ⇒ 每帧自噬**

**实测（探针 `probe_ws.py` / `harness.py`，`SDL_VIDEODRIVER=dummy`，1920×1080，14 个真实 b-deck 文件）**

| 量 | 实测值 |
|---|---|
| `PaintDialog` 稳态每帧 `rt()` 调用 | **169** |
| 稳态每帧**真实字形光栅化**（`SmartFont.render` miss） | **139**（无预览）/ **140**（有预览） |
| 其中**经 `rt()`** 到达的 | **134 / 135** |
| 其中**绕过 `rt()`** 的（`dialog_base`/`input_field` 直连） | **5 / 5** |
| 每帧命中数 | 34–35（命中率 **20%**） |
| **稳态工作集（3 帧去重）** | **146 键 / 654,308 B（0.64 MiB）** |
| 单张最大表面 | **21,120 B** |
| **`f_s` 稳态键数** | **139  >  `maxsize = 128`** ⇒ 超限 |

- 命中率只有 20%、`f_s._cache` 长度**恒等于 128（= maxsize，已饱和）** ⇒ 典型的**每帧自噬**：每帧把上一帧的条目挤出去，下一帧再全部重新光栅化。
- **这是本批第三次遇到同一模式**：`_name_shadow_cache` 用 256 条上限导致 0% 命中、`_landfall_cache` 只按 24 条封顶导致最坏 1.37–5.5 GB（见派活单）。**条数上限**在这个代码库里是已被反复证伪的闸门形式。
- 关键量化：**工作集只比上限大 11 条（139 vs 128）**。所以"把 128 调大"也能治 —— 但那既碰了 `SmartFont`（写域外），又保留了条数闸门这个已被证伪的形式。**改成字节预算才是对的**。

### 3.3 容量：为什么是 8 MiB（而不是 8 MiB 拍脑袋）

| 依据 | 数字 |
|---|---|
| `PaintDialog` 稳态工作集（实测） | **0.64 MiB / 146 键** |
| 8 MiB ÷ 0.64 MiB | **≈ 12.8 倍余量** |
| 实测平均键字节（300 键微基准：1,209,920 B / 300） | **4,033 B/键** ⇒ 8 MiB ≈ **2,080 键** |
| 实测最大单键（PaintDialog） | **21,120 B** ⇒ 最坏情况 8 MiB ≈ **397 键** |
| 本机可用内存（`16_perf_diagnosis.md` / 派活单） | ≈ **2.5 GB**，`app` 单实例 RSS 已 **646 MB** |
| 8 MiB 占可用内存 | **0.31 %** |
| 对照：既有共享缓存 | `_name_shadow_cache` **32 MiB**、`_purple_frame_cache` **96 MiB**、`summary_list._purple_frame_cache` **48 MiB** |

⇒ 8 MiB **比本仓既有文本类缓存小 4–12 倍**，且是实测需求的 12.8 倍余量。保守侧的理由是派活单的 OOM 前科与"可用仅 2.5 GB"。

### 3.4 `id(f)` 安全性论证（派活单要求"先论证"）

**结论：`id(f)` 不天然安全，必须**钉住**字体对象；本实现已钉住。**

**（实测）全 app 的 `SmartFont` 实例化点共 24 处**（`grep -rn "SmartFont(" app/ main.py`）。按生命周期分三类：

| 类别 | 位置 | 是否常驻 |
|---|---|---|
| 模块级全局（18 处） | `fonts.py:153-158`（`f_l/f_m/f_s/f_name/f_15/f_19`）、`landfall_effect.py:10`、`monthly_summary.py:14-15`、`summary_list.py:24-27`、`summary_effect.py:37-40`、`draw_info_boxes_mixin.py:25-30` | ✅ 常驻（进程期不释放） |
| 懒建但锁存在模块全局（1 处） | `dialog_chart.py:26,35-38` `_get_ace_title_font()`（`global _font_ace_title` 一次性 latch） | ✅ 首次调用后常驻 |
| **按字号缓存在模块 dict 且会淘汰（5 处）** | `_draw_icon_mixin.py:75-84 _info_box_font()`（`_info_box_font_cache`，**>24 淘汰**）、`:134-143 _sized_font(cache,size)`（`_name_font_cache` / `_peak_font_cache`，**>24 淘汰**） | ⚠ **可被 GC** |

⇒ **存在非全常驻的 `SmartFont`**：用户在字号滑杆上扫过 **25 个以上**不同字号时，`_info_box_font_cache` / `_name_font_cache` / `_peak_font_cache` 会 `pop(next(iter(...)))` 把旧实例踢掉；被踢掉的实例若同时还在我的缓存键里（`id(f)`），GC 之后**它的 `id` 可能被新建对象复用** ⇒ 命中别人的表面 = **串味**。

**本实现的处置**：`_rt_font_refs[id(f)] = f` 持强引用（`fonts.py` 改后 `rt()` 内）。

**验证（探针 `verify.py`，实测）**

```
字体已进 _rt_font_refs: True
删除字体引用 + gc.collect() 后缓存项仍在: True (键 id=2034974123936)
新对象是否复用同一 id: False
```

⇒ 钉住后 `id` 在缓存有效期内**不可能**被复用。代价：全 app 的 `SmartFont` 实例是**常数级（24 个）**，多持有 24 个对象的引用可忽略（每个 SmartFont 只含 2 个字体引用 + 一个 dict，字体本体已在 `_font_cache` 常驻）。

⚠ **两个遗留的弱化说明（如实记）**：
1. 既有的 `max_width` 缓存（`rt._cache`，7 处调用点）**也用 `id(f)` 且没有这层保护** —— 那是既有问题，按"只改默认分支"的约束本次**不动**。
2. 键里同时含 `text`（调用方字符串）。若某调用方传入**内容可变**的 str 子类，哈希语义会漂 —— 但 `str` 不可变，且 227 处调用点传的都是字面量/f-string，不构成风险。

### 3.5 与 `_name_shadow_cache` 的对齐（派活单要求"别造两层互相打架的缓存"）

**结论：键空间不重叠，无双层冲突。**

```
_name_shadow_cache  (_draw_icon_mixin.py:379)  SurfaceCache("name_text", 32 MiB, 1024)
  键: ('name'|'peak'|'cname', name, tuple(color), size)     ← _get_name_surf:1100 / _get_peak_label_surf:1077
  值: _sized_glow_text(...) 的输出（含描边+辉光的合成面）
  数据源: 直连 font.render(text, True, (255,255,255)) / darken_color(...)  (_draw_icon_mixin.py:192-193)
          —— **不经过 rt()**
```

| | `_name_shadow_cache` | 本次 `_rt_cache` |
|---|---|---|
| 键 | 含 `size`、`'name'/'peak'/'cname'` 前缀 | `(id(f), text, color, smooth)` |
| 值 | 带描边/辉光的**合成**面 | 单次 `f.render` 的**裸**文本面 |
| 上游 | `_sized_glow_text` → `font.render` | `rt()` → `SmartFont.render` |
| 预算 | 32 MiB / 1024 条 | 8 MiB / 无条数上限 |

⇒ 两者**没有共同的键，也没有共同的调用链**（`_name_shadow_cache` 的输入根本不经过 `rt()`）。因此不存在"同一张图被缓存两次"或"两层互相淘汰"的问题。唯一的关系是它们**合计**占用 32 + 8 = 40 MiB，仍低于既有单一项 `_purple_frame_cache` 的 96 MiB。另：`keyboard_mixin.py:47-48` 只清 `_name_shadow_cache`，与本次缓存无关（本次缓存不需要按键盘清空 —— 键里含 text/color，改字号会换 `id(f)` 自然失效）。

### 3.6 修不到的残余：绕过 `rt()` 的 5 次/帧（诊断漏掉的一类）

派活单与 app-feat 的证据都只统计 `rt()` 调用点。但实测把未命中**按来源拆分**后发现，每帧还有 **5 次光栅化完全绕过 `rt()`**：

| 位置 | 代码 | 说明 |
|---|---|---|
| `app/dialog_base.py:113` | `CONTENT_FONT.render(text, True, tc)` | `CONTENT_FONT = f_s`（`dialog_base.py:21`） |
| `app/dialog_base.py:120` | `TITLE_FONT.render(text, True, SETTINGS_TEXT_LIGHT)` | `TITLE_FONT = f_m`（`dialog_base.py:20`） |
| `app/dialog_base.py:184` | `title_font.render(title_text, True, title_color)` | 标题栏 |
| `app/input_field.py:538, 575` | `self.font.render(self.label/val, True, tc)` | 输入框 |

这些直连 `SmartFont.render`，**因此只有 `SmartFont` 那个 128 条 FIFO 在管它们**，同样自噬。**它们不在本次写域**（派活单：写作用域只有 `fonts.py`），所以修复后仍有 **0.25–1.25 次/帧**的残余。要彻底归零需要第二轮改 `dialog_base.py` / `input_field.py`（把 `X.render(...)` 换成 `rt(X, ...)`，或给 `SmartFont.render` 加字节预算）。

### 3.7 实现（关键片段，含 diff 边界）

```python
# fonts.py（改后）
from ..cache_store import SurfaceCache                      # ① 新增 import
RT_TEXT_CACHE_BYTES = 8 * 1024 * 1024                       # ② 回退开关（<=0 即关闭）
_rt_cache = SurfaceCache("font_rt_text", RT_TEXT_CACHE_BYTES) if RT_TEXT_CACHE_BYTES > 0 else None
_rt_font_refs: dict = {}                                    # id(f) → f 强引用（防 id 复用）

def rt(f, text, color, max_width=None, smooth=True):
    if max_width is None or max_width <= 0:                 # ③ 只改这一支
        if _rt_cache is None or RT_TEXT_CACHE_BYTES <= 0:
            return f.render(text, smooth, color)            # 旧行为（开关关闭）
        try:
            key = (id(f), text, color, bool(smooth))
            hash(key)
        except TypeError:
            return f.render(text, smooth, color)            # color 不可哈希 → 不缓存
        surf = _rt_cache.get(key)
        if surf is not None:
            return surf
        surf = f.render(text, smooth, color)
        if id(f) not in _rt_font_refs:                      # 先钉住字体再写缓存
            _rt_font_refs[id(f)] = f
        _rt_cache.put(key, surf)
        return surf
    if not hasattr(rt, "_cache"):                           # ④ 以下完全未动
        rt._cache = OrderedDict()
    ...
```

**安全点逐条**：

| 风险 | 处置 | 证据 |
|---|---|---|
| `color` 不可哈希（list） | `hash(key)` 的 `TypeError` → 退回直渲 | `fonts.py` 改后；既有 `_outlined_cache` 也已用 `color` 当键（`summary_effect.py:205`）⇒ 实践中是 hashable |
| `id(f)` 被复用 | 强引用钉住 | `verify.py`：`del` + `gc` 后缓存项仍在 |
| 预算被绕过 | `SurfaceCache._trim()` 在每次 `put` 后执行 | `cache_store.py:81,98-104`；§5 的容量实验 |
| 单对象超预算 | `put` 返回 `False` 且**不写入**（不会冲掉整池） | `cache_store.py:73-75`；实测最大单键 21 KB ≪ 8 MiB |
| 视图无回归 | 返回同一个 Surface 对象（与 `SmartFont` 既有语义一致） | §3.8 的调用方突变审计 |
| 想关掉 | `RT_TEXT_CACHE_BYTES = 0` **运行时即刻生效** | `verify.py`：关闭后 hits/misses 不变动且输出逐字节相同 |

### 3.8 共享表面的风险审计（缓存让 Surface 跨帧长存，会不会被就地改坏？）

**改前的语义**：`SmartFont.render` 命中时**已经返回共享对象**。所以"返回共享 Surface"是**既有语义**，本次只是把共享期从"若干帧"变成"稳定长存"。

**审计**：全仓扫描 `X = rt(...)` 之后对 `X` 的就地修改（`set_alpha` / `set_colorkey` / `fill` / `convert` / `scroll` / `lock`）。

| 命中位置 | 代码 | 判定 |
|---|---|---|
| `app/summary_effect.py:211, 213` | `black = black.copy(); black.set_alpha(...)` / `fg = fg.copy(); fg.set_alpha(...)` | ✅ **先 copy 再改**，安全 |
| `app/ty_sim_mixins/draw_info_boxes_mixin.py:679-680` | `white = white.copy(); white.set_alpha(...)` | ✅ 先 copy，安全 |

**共 4 处候选，全部先 `.copy()`** ⇒ **无就地突变 ⇒ 无视觉回归风险**（源按 `black = rt(font, text, (0,0,0))` 见 `summary_effect.py:208-209`）。

---

## 4. 输入输出资产

| 路径 | 角色 | 体量 |
|---|---|---|
| `app/constants/fonts.py` | **被改文件** | 217 → 284 行；sha256 `a32bbdc4…` → `f17d22a7…` |
| `output/exploration/_backup/fonts.py` | **回退点**（未改） | 217 行；sha256 `a32bbdc4…`（与 `README_ROLLBACK.md:40` 一致） |
| `app/cache_store.py` | 复用件（未改） | 118 行；`SurfaceCache` 提供 `surface_bytes/stats/reset_stats` |
| `C:\Users\Yukioto\AppData\Local\Temp\sc3\harness.py` | 验收探针（工作区外） | 改前/改后计数 + 工作集 + 逐字节抽样 |
| `…\Temp\sc3\verify.py` | 验收探针（工作区外） | 回退开关 / 容量上界 / id 钉住 |
| `…\Temp\sc3\probe_ws.py`、`probe_split.py`、`probe_rt.py` | 诊断探针（工作区外） | 工作集字节、按来源拆分未命中 |
| `…\Temp\sc3\result.json`、`verify.json` | 中间产物**已落盘** | 应对本轮 bash 反复掉线 |
| 运行时新增内存 | `_rt_cache`（8 MiB 预算，实测占用 **750,584 B**）+ `_rt_font_refs`（静态扫描 24 个常驻 `SmartFont`；极端字号扫描下可更多，见 §7 U3） | ≈ **0.73 MiB** |

---

## 5. 如何运行 / 如何验证

真实运行时：`/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`（3.14.2，pygame-ce 2.5.7）。
探针一律 `SDL_VIDEODRIVER=dummy`；中文输出用 `sys.stdout.reconfigure(encoding='utf-8')`。

### 5.1 复现命令

```bash
cd /mnt/g/气象/Typhoon-sim
P="/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe"

# ① 改前/改后 计数 + 工作集 + 逐字节抽样（写 result.json）
timeout 300 "$P" "C:/Users/Yukioto/AppData/Local/Temp/sc3/harness.py"
# ② 回退开关 / 容量上界 / id 钉住（写 verify.json）
timeout 300 "$P" "C:/Users/Yukioto/AppData/Local/Temp/sc3/verify.py"
# ③ 诊断：按来源拆分未命中
timeout 300 "$P" "C:/Users/Yukioto/AppData/Local/Temp/sc3/probe_split.py"

# ④ 回归（单文件 timeout 300，禁止全量）
timeout 300 "$P" -m pytest tests/test_ace_engine.py -q --no-header                    # 19 passed
timeout 300 "$P" -m pytest tests/test_fx_sound.py tests/test_panel_layer_row.py -q     # 32 passed
timeout 300 "$P" -m pytest tests/test_render_layers.py tests/test_control_panel.py -q  # 24 passed
timeout 300 "$P" -m pytest tests/test_paint.py -q --no-header                          # 15 passed in 49.00s
```

### 5.2 验收 ① 命中率与光栅化（判据：确定性计数）

| 指标 | 改前（`_backup/fonts.py`） | 改后 | 判据 | 结果 |
|---|---|---|---|---|
| `PaintDialog` 稳态每帧真实光栅化（无预览） | **139** | **1.25** | ≈0 | ✅ **−99.1%** |
| `PaintDialog` 稳态每帧真实光栅化（有预览） | **140** | **0.25** | ≈0 | ✅ **−99.8%** |
| 其中经 `rt()` 到达 | 134 / 135 | **0.00 / 0.25** | ≈0 | ✅ |
| 其中绕过 `rt()`（写域外） | 5 / 5 | 1.25 / 0.00 | 不适用 | ⚠ 残余，见 §3.6 |
| `_rt_cache` hits / misses | —（无此缓存） | **2056 / 152** | — | 命中率 93.1%（含冷启动） |
| **`_rt_cache` evictions** | — | **0** | **必须 0** | ✅ **0** |

> "稳态"= 预热 1 帧后取 4 帧均值。残余的 1.25/0.25 起伏来自 §3.6 那 5 个绕过 `rt()` 的调用点，其命中与否取决于 `SmartFont` 那个 128 条 FIFO 的状态。

**A/B 微基准（第二组独立证据：单字体 300 个不同文本/帧，键空间远超任何条数上限）**

| 臂 | 每帧真实光栅化 | 缓存项 | 字节 | evictions | 命中率 |
|---|---|---|---|---|---|
| **A 旧行为**（`RT_TEXT_CACHE_BYTES=0`） | **300.00** | 0 | 0 | 0 | 0.00% |
| **B 新缓存**（8 MiB 字节预算） | **0.00** | 300 | 1,209,920 / 8,388,608 | **0** | 80.0%（含 300 次冷启动） |

⇒ 在"工作集 300 键"这一远比 `PaintDialog`(146) 更大的压力下，新缓存**每帧 0 次光栅化、0 驱逐**。这证明修法不是只对 `PaintDialog` 调参，而是**容量形式本身**对了。

### 5.3 验收 ② 容量上界

| 检查 | 实测 |
|---|---|
| 运行时占用 `_rt_cache.total_bytes()` | **750,584 B** = 预算的 **8.9 %** |
| 预算 | 8,388,608 B（8 MiB） |
| 强制填充实验（每张 1.37 MB） | 塞到第 **6** 张开始驱逐；`evictions = 1`；`total = 6.87 MB ≤ 8.00 MB` ✅ **任何时刻不超预算** |
| **键空间多大时开始驱逐** | 按实测均值 4,033 B/键 ⇒ 约 **2,080 键**；按实测最大单键 21,120 B ⇒ 最坏约 **397 键** |
| 对照：`PaintDialog` 实际工作集 | **146 键 / 0.64 MiB** ⇒ 离驱逐线还有 **12.8 倍** |

### 5.4 验收 ③ 视觉无回归（逐字节）

同一组文本/颜色，改前（`_backup/fonts.py`）与改后各渲染一次，`sha256(pygame.image.tobytes(surf, "RGBA"))`：

| 文本 \| color | 改前 sha256 | 改后 sha256 | 相同 |
|---|---|---|---|
| `绘画模式` \| (25,32,48) | `135d4779…1492` | `135d4779…1492` | ✅ |
| `ACE / 路径统计` \| (196,204,218) | `5885dac0…1466` | `5885dac0…1466` | ✅ |
| `▾` \| (25,32,48) | `11063c1c…0aea68` | `11063c1c…0aea68` | ✅ |
| `12` \| (196,204,218) | `bd06efe8…a06a5ac` | `bd06efe8…a06a5ac` | ✅ |
| `wp012025.dat` \| (25,32,48) | `0970cfb1…c0e993` | `0970cfb1…c0e993` | ✅ |
| `已加入 14 个文件` \| (240,205,110) | `04c768a0…8528c9` | 同 | ✅ |
| `""`（空串） \| (25,32,48) | `e3b0c442…b2b855`（= 空输入的 SHA256） | 同 | ✅ |
| `放大 ×1.0 · 滚轮缩放` \| (245,245,250) | `c15f24f7…8ee8` | 同 | ✅ |

**8/8 项逐字节一致** ✅（对照 sim-data 的"14/14 项逐字节一致"标准）。

另：`PaintDialog` 整帧在**视觉产物**上的等价性由 `tests/test_paint.py` **15 passed** 覆盖（它真实渲染路径图并断言几何）。

### 5.5 回退开关验证

```
关闭后 (hits,misses) 前=(1200, 300) 后=(1200, 300)  => 未变动 = True   （该次调用完全没碰缓存）
关闭 vs 开启 输出逐字节相同: True
```
⇒ `RT_TEXT_CACHE_BYTES = 0` 时 `rt()` **逐位回到旧行为**，且缓存计数不再变动（证明确实绕开）。

### 5.6 回归测试

| 测试文件 | 基线 | 改后 | 结果 |
|---|---|---|---|
| `tests/test_ace_engine.py` | 19 passed | **19 passed in 0.47s** | ✅ 一致 |
| `tests/test_fx_sound.py` + `test_panel_layer_row.py` | — | **32 passed in 10.14s** | ✅ |
| `tests/test_render_layers.py` + `test_control_panel.py` | — | **24 passed in 0.63s** | ✅ |
| `tests/test_paint.py` | 单文件 69.6 s | **15 passed in 49.00s** | ✅ |

未跑全量 pytest（铁律 6）。

---

## 6. 观察到的问题 / 风险 / 不一致

### MAJOR-1｜诊断报告的前提不成立：默认路径**本来就有缓存**，真因是"上限只差 11 条"

- **现象**：`16_perf_diagnosis.md:184` 写"`rt()` **默认不缓存**"、`15_perf_data_ui.md:132` 写"换行渲染有缓存，单行渲染没有"。
- **证据**：`fonts.py:90-130` 的 `SmartFont.render` 自带 `maxsize=128` 缓存，而 `rt()` 默认分支调的就是 `f.render`。实测 `f_s` 稳态 **139 键 vs 上限 128** ⇒ 自噬，命中率 **20%**。
- **影响**：修法描述（"给它加缓存"）会把人引向"另建一层缓存"；真实需要的是**容量形式换成字节预算且盖住工作集**。若不纠正，很可能实现出一个**上限 1024 条**的新缓存（照抄既有 `rt._cache` 的做法）—— 在 `PaintDialog` 上恰好够用，但**又留了一个条数闸门**（本批已两次被它坑）。本次按字节预算实现。

### MAJOR-2｜"220 处调用点绕过缓存"口径漏掉了绕过 `rt()` 的那一类

- **现象**：诊断只统计 `rt(...)` 调用点。实测每帧还有 **5 次光栅化完全绕过 `rt()`**（`dialog_base.py:113/120/184` 的 `CONTENT_FONT`/`TITLE_FONT`、`input_field.py:538/575` 的 `self.font`）。
- **证据**：探针 `probe_split.py` 按来源拆分未命中 = `经rt 134.0 / 绕过rt 5.0`（无预览）。
- **影响**：**任何只在 `rt()` 里做的优化，天花板都是"每帧剩 5 次"**，不可能到 0。要归零必须同时改 `dialog_base.py` / `input_field.py`（本次写域外）。这也是"改后残余 0.25–1.25 次/帧"的唯一来源。

### MINOR-1｜既有 `max_width` 缓存的 `id(f)` 没有钉住保护

`rt._cache`（7 处调用点，条数上限 1024）同样用 `id(f)` 作键，但**没有** `_rt_font_refs` 那层强引用。按 §3.4，`_info_box_font` / `_sized_font` 在 >24 字号时会淘汰 `SmartFont` ⇒ 那 7 处**可能串味**。本次按"只改默认分支"的约束**未修**，但它是一个**真实存在的既有缺陷**，建议下一轮一并处理（改动极小：把 `id(f)` 换成"钉住的 id"）。

### MINOR-2｜`ace_chart`（app-feat 报告 24 次/帧）本轮**未能独立复现**

- **现象**：`15_perf_data_ui.md:155` 报 `ace_chart` 稳态 24 次/帧，本轮无法验证。
- **卡在哪**：`ACEChartDialog.activate()` → `_rebuild()` → `build_chart_data` → `sim.ace_engine` → `calculate_season_stats` → **`sim.map_mgr`**（`season_stats.py:172`）。即需要**完整 `TySim` 与真实地图管理器**（222 MB 图像），超出"秒级轻探针"的预算。
- **已尝试**：逐层补 mock（`yad`/`current_ace_year`/`sy`/`st`/`hemisphere`/`res_mgr.ocean_areas`），止步于 `map_mgr`；尝试过程与报错已存 `result.json` 的 `ace` 字段（**证据保留**）。
- **影响**：不影响本次结论 —— 修复在 `rt()` 层，对**所有** `rt()` 调用点一致生效（`ace_chart` 的 9 处 `rt(f_s, ...)` 见 `dialog_chart.py:44-52`）。但"改后 `ace_chart` ≈0"这一条**未实测**，如实记为 unresolved。

### MINOR-3｜`_landfall_cache` / `_name_shadow_cache` 的同类风险未在本次处置

派活单提到本批已有两起条数闸门事故（`_name_shadow_cache` 256 条 0% 命中；`_landfall_cache` 24 条 → 1.37–5.5 GB）。本次只处理了 `rt()`；那两处**不属于本次写域**，但属于同一根因族，建议单独一轮统一改造成 `SurfaceCache`。

---

## 7. unresolved（未能确证）

| # | 卡在哪 | 需要什么才能确证 |
|---|---|---|
| U1 | **`ace_chart` 改后的每帧光栅化数**（改前 app-feat 报 24，本轮未能复现基线、也未能测改后） | 需要一个可构造的完整 `TySim`（含 `map_mgr`）。或把 `calculate_season_stats` 的 `sim.map_mgr` 依赖解耦（那是另一项改动） |
| U2 | **`PaintDialog` 每帧 67（无预览）/ 121（有预览）** 这两个 app-feat 数字的精确复现条件 | 我用 14 个 b-deck 测得 **169 次 `rt()` / 139-140 次光栅化**，量级一致但数值不同 —— 差异应来自**文件列表长度**（我 14 个）与窗口/布局。app-feat 的探针脚本 `probe_dialog_frame.py` **不在磁盘上**（`find` 无结果），无法照原样重跑。⇒ 我以**自己的可复现基线**作为对照，而非引用其数字 |
| U3 | `_rt_font_refs` 的**长期**增长上界 | 静态扫描得 24 个 `SmartFont` 实例化点，但 `_info_box_font` / `_sized_font` 可按字号**任意**新建。极端情况下（用户扫遍 1..300 号字号的每一个值）引用数可达数百 ⇒ 内存仍可忽略（每个仅 2 个字体指针 + 一个 dict），但**未做**长时压测证实 |
| U4 | 是否存在**多线程**同时调用 `rt()` | 扫描 `app/` 未发现 `threading`/`concurrent.futures` 调用 `rt()`；但 `SurfaceCache` 与 `_rt_font_refs`/`dict` **不是线程安全的**。若将来引入后台渲染线程会需要加锁 |
| U5 | `text` 为**超长字符串**（如整段路径文本）时单键字节 | 实测最大单键 21 KB（`PaintDialog`）。若有调用方传入极长文本，单键可能接近/超过 8 MiB ⇒ `SurfaceCache.put` 会**静默不缓存**（`cache_store.py:73-75`，返回 `False`），行为正确但**不会计入 evictions**，性能面板无法体现。需要一次全库最长文本扫描才能给出上界 |

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| **被改的文件** | `app/constants/fonts.py`（217→284 行；改后 sha256 `f17d22a7…`） |
| **回退点** | `output/exploration/_backup/fonts.py`（sha256 `a32bbdc4…`；`README_ROLLBACK.md:14,40`） |
| 新增的缓存与开关 | `app/constants/fonts.py`：`RT_TEXT_CACHE_BYTES`、`_rt_cache`、`_rt_font_refs`、`rt_cache_stats()`、`rt_cache_clear()` |
| **真因证据：`SmartFont` 自带的 128 条 FIFO** | `app/constants/fonts.py:90-130`（`maxsize=128`、`pop(next(iter(...)))`） |
| 字节预算 + 真 LRU 的实现 | `app/cache_store.py:19-24`（计字节）、`:71-82`（put）、`:98-104`（_trim 驱逐）、`:106-115`（stats） |
| **改动边界（diff 尾段）** | `if not hasattr(rt, "_cache"):` 起 = `max_width` 分支，**未动** |
| 绕过 `rt()` 的残余（写域外） | `app/dialog_base.py:20-21, 113, 120, 184`；`app/input_field.py:538, 575` |
| 会淘汰 `SmartFont` 的三处（`id(f)` 风险源） | `app/ty_sim_mixins/_draw_icon_mixin.py:75-84`、`:134-143` |
| 与 `_name_shadow_cache` 的边界 | `app/ty_sim_mixins/_draw_icon_mixin.py:379`（定义）、`:1070-1113`（用法，不经过 `rt()`） |
| 共享表面突变审计（4 处均先 copy） | `app/summary_effect.py:208-214`；`app/ty_sim_mixins/draw_info_boxes_mixin.py:679-680` |
| 验收探针（工作区外） | `C:\Users\Yukioto\AppData\Local\Temp\sc3\{harness,verify,probe_ws,probe_split}.py` |
| 验收数字（已落盘） | `…\Temp\sc3\result.json`、`…\Temp\sc3\verify.json` |
| 诊断原文（**前提需修正**） | `output/exploration/16_perf_diagnosis.md:184`；`output/exploration/15_perf_data_ui.md:121-159` |
