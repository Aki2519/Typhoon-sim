# app/paint_render_qq.py
"""QQ 机器人样式渲染器（台风吧官群风格）—— 独立于 `paint_render.py`。

反推自基准图 `xrq-dolphin.png`（1837×1224，26 Dolphin）。**不改 `paint_render.py`**，
只复用它的调用约定（`render(tys, mode, region, map_surface) -> (Surface, stats)`），
所以 P5 可以用一个开关在两者之间切。

────────────────────────────────────────────────────────────────────────
一、配色 —— **全部从基准图采样**（不是凭感觉）

`xrq-dolphin.png` 只有 3022 种颜色、前 5 色覆盖 95.7%，所以色表可以**精确取到**：

| 角色 | RGB | 原图占比 | 取法 |
|---|---|---|---|
| 海洋 | `(0, 34, 68)` | 78.08 % | 出现最多的颜色 |
| 陆地 | `(48, 112, 80)` | 15.73 % | 第二多 |
| 水印(叠海洋) | `(62, 88, 114)` | 1.151 % | 第三多 |
| 水印(叠陆地) | `(99, 147, 123)` | 0.434 % | |
| **经纬网** | `(102, 204, 153)` | 0.111 % | 见下 |
| 连线 | `(255, 255, 255)` | 0.035 % | |
| 标注文字 | `(202, 218, 210)` | 0.034 % | 左/下边缘的文本色 |
| 强度色 × 9 | 见 `QQ_SCALE` | 0.890 % 合计 | 按亮度/色相排序后与风压档对应 |

**经纬网色怎么定下来的**：不是猜的。把 `(102,204,153)` 单独做成掩码后，它是
一张**干净到没有一条杂线的虚线经纬网**（3418 px，无陆地/标记/文字污染）——
正是靠它才把投影拟合到 0.003 px。反过来这也证明该色就是网格色。

二、强度色序（9 档，弱→强）

    (0,250,244) 青 → (94,186,255) 浅蓝 → (102,204,153) 青绿 → (255,255,204) 近白黄
    → (255,231,117) 淡黄 → (255,193,64) 琥珀 → (255,143,32) 橙
    → (255,96,96) 红 → (255,135,135) 浅红

分档阈值由 `calibrate_thresholds()` 用**分位数匹配**标定：让渲染出的 9 色计数
尽量贴近基准图实测计数（同一批 26 Dolphin 的 99 个报点）。默认值见 `QQ_BINS`。

三、`opts` 字段

| 键 | 默认 | 含义 |
|---|---|---|
| `projection` | `"conic"` | `"equirect"` / `"conic"`（与 `paint_projection.PROJECTIONS` 同名） |
| `labels` | `"edge"` | `"none"` / `"edge"`(贴画布边) / `"inside"`(内缩 22px)；兼容旧 bool |
| `basemap` | `"land_flat"` | `"map_png"`(卫星，**仅 equirect**) / `"land_flat"`(平涂) / `"none"`；兼容旧 `coast` |
| `marker` | `"by_status"` | `"by_status"`(DB/EX→三角, SD/SS→方块, 其余→圆) / `"uniform"`(全圆) |
| `palette` | `"qq"` | `"qq"` / `"2023"` / `"classic"` |
| `legend` | `"none"` | `"none"` / `"tl"` / `"tr"` / `"bl"` / `"br"` / `"auto"` |
| `gradient_line` | `False` | 连线按强度等级色插值着色（关闭时是细白线） |
| `gradient_subdiv` | `8` | 渐变细分数 |
| `output_size` | `None` | `(w, h)`；`None` = 按 bounds 自动 |
| `grid` | `True` | 画虚线经纬网 |
| `grid_step` | `10` | 网格间隔（度） |
| `labels` | `True` | 画经纬度标注 |
| `watermark` | `True` | `@xrq` 斜向重复水印 |
| `margin_w` / `margin_h` | `0` | 同 `paint_render`（内容留白） |
| `aa` | `True` | 关闭则退回 pygame 基础图元（**仅供对照**，会锯齿） |
| `marker_r` | `9.0` | 标记半径（px） |
| `line_w` | `1` | 连线宽 |

四、抗锯齿

**从第一行起就用 `app.paint_aa`**：圆/三角/方块/连线/风圈全部走它的超采样精灵；
海岸线走 **2× 陆地掩码超采样降采样**。因此边缘一定存在中间 alpha 像素
（见报告 §5 的确定性计数）。
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pygame

from . import paint_aa
from . import paint_projection as PP

__all__ = ["QQ_COLORS", "QQ_SCALE", "QQ_BINS", "QQTrackRenderer",
           "color_for_wind", "calibrate_thresholds", "shape_for_code"]

# ── 实测采样自 xrq-dolphin.png 的配色 ──────────────────────────────────────
#: 底图两色的**实测证据**（`…\Temp\sc7\r3c.py`）：
#:   · 海洋 `(0,34,68)`   —— 1 755 659 px = **78.082%**（全图 2 248 488 px）
#:   · 陆地 `(48,112,80)` ——   353 603 px = **15.726%**
#:   ☑ **都是纯色，没有渐变也没有纹理**：把掩膜腐蚀 3px 只留内部后，内部像素里
#:     "恰为主色"的比例是 **100.0000%**（海洋内部 1 585 136 px、陆地内部 298 789 px，
#:     非主色的前 8 色**一个都没有**）。边界上那些杂色是抗锯齿混合，不是纹理。
#:   ☑ **无位置依赖**：四象限分别求均值，海洋四块都是 `(0,34,68)`、陆地四块都是
#:     `(48,112,80)` —— 排除了"分区着色/渐变"的可能。
#:   ⚠ 注意：`(86,125,70)`/`(24,52,92)` 不是本基准图的颜色（见 `r3c_landcolor.md`）。
QQ_COLORS: Dict[str, Tuple[int, int, int]] = {
    "ocean": (0, 34, 68),
    "land": (48, 112, 80),
    "watermark": (62, 88, 114),
    "watermark_land": (99, 147, 123),
    #: **2026-10-02 R10 还原成实测值 (102,204,153)**。
    #: 曾经改成 (185,234,254)，理由是"它与 `QQ_SCALE[2]`(25-30kt 档) 同色 ⇒ 那档点看不见"。
    #: **该理由已不成立**：真正在用的标记色阶是 `QQ_RAMP`（**8 档，其中没有绿色**），
    #: `QQ_SCALE` 是遗留常量（全仓零生产引用）。基准图里 (102,204,153) 的掩码是
    #: "干净到没有一条杂线的虚线经纬网、3418 px、无标记污染" ⇒ **它就是网格色**。
    #: 改前自查：`QQ_RAMP` 与 (102,204,153) 的**最小 L1 = 128**（档 0 浅蓝）> 60 ⇒
    #:   不会造出"25-30kt 点看不见"的真回归 ⇒ 可以安全还原。
    #: 实测：基准图全图纯网格色像素（L1<=30）= **6046**；还原前我们 = 0。
    "grid": (102, 204, 153),
    "line": (255, 255, 255),
    "label": (202, 218, 210),
}

#: 强度色（弱 → 强），实测取自原图的标记像素
#: ⚠ **遗留常量，全仓零生产引用**（`grep QQ_SCALE app/ tests/` 只有本文档串/__all__/本注释）。
#: 真正在用的是 `QQ_RAMP`（8 档）。本表里的 `(102,204,153)` 实测是**网格色**，
#: 不是标记色（基准图 99 个报点里它出现 0 次）—— 保留仅为记录"从基准图采样到的 9 个非底图色"。
QQ_SCALE: Tuple[Tuple[int, int, int], ...] = (
    (0, 250, 244), (94, 186, 255), (102, 204, 153), (255, 255, 204),
    (255, 231, 117), (255, 193, 64), (255, 143, 32), (255, 96, 96),
    (255, 135, 135),
)

#: **非正式报的"黑心"标记**（用户 2026-10-02 指出："qq 的非正式报是使用黑色中心圆
#: (黑色中心正方/三角)来标记的"）。
#:
#: **实测（2026-10-02 修订）**：基准图里**确实有空心标记**，只是我第一版判据错了 ——
#:   我用「max(RGB) < 40 找纯黑」，而**洞内透出的是海洋色 (0,34,68)（max=68）**，于是全漏。
#:   改用「被标记色包围的背景像素」重扫，全图**只有 1 个空心**：
#:     · 位于 (350-354, 511-513)，反投影 121.32E / 28.09N（白海豚登陆处附近）；
#:     · 环上标记占比 1.00、洞内=海洋色 ⇒ 是「空心环」而不是「填黑」。
#:   ✅ **2026-10-02（R8）更正：用户口述的规则成立。**
#:     上一轮把空心归给 `i=24`（00Z/150kt/正式报）是**假阳性** —— 当时用 `max|Δ| <= 14`
#:     判"是否标记色"，而 `i=24` 的中心像素是 `(255,119,119)`，离标记色 `(255,135,135)`
#:     只有 16 ⇒ 被误判成"背景(洞)"。
#:     改用「洞内必须离**所有**标记色 >= 60」重查，并把空心的**图像坐标** (352,512) 反投影：
#:       · 反投影 = 121.323E / 28.086N；
#:       · 最近的报点 = **`i=66`，距 2.9 px**，即 `2026080909` —— **09Z ⇒ 非正式报**，
#:         正是全轨迹**唯一**的非正式报（`paint_render._is_official` 判 False）。
#:     ⇒ **空心 ↔ 非正式报 = 1/1 完全吻合 ⇒ 规则由基准支持**（n=1）。
#:       （`i=66` 同时在登陆处附近 121.20E/28.00N，"登陆处"与"非正式报"在这张图上重合，
#:        但决定性事实是它是**唯一的非正式时次**。）
#:   ⚠ 口径教训：**"逐点按投影中心取样"找不到这个洞** —— 投影中心偏 2.9 px 就落到环上，
#:     于是那个口径既漏掉真洞、又在别处给出假阳性。找空心必须用**图像空间**的口径。
#:   判定**不靠猜**：用 app 的 `_is_official_point`（与 `paint_render._is_official` 同口径）。
#:   ⚠ `TrackPoint.official` 字段**全仓没有任何地方赋值**（恒 True）⇒ 不能拿它判定，
#:     否则空心永不触发；两种判据在 99 点上的差异数 = 1（就是 `i=66`）。
#:   （被本段取代的旧结论：① 曾用 max(RGB)<40 判为「本基准图无黑心样式」；② 曾把空心
#:     归给 i=24 并认为"与规则矛盾"。两条都是**判据错误**，均已更正。）
#:   **= 0 个（0.0000%）**，99 个报点的中心 5x5 均值**全部**是等级色（例如 `(94,186,255)`），
#:   没有一个黑心。所以下面这两个数是**按用户描述取的实现值，不是从这张图量出来的**
#:   —— 记为「未由基准验证」。判定**哪些点是非正式报**不靠猜：用 app 自己的
#:   `TrackPoint.official` 字段（`app/typhoon.py:33`，默认 True）。
#:   若换成别的 QQ 样张（带黑心）应当回来重标这两个数。
#: **实测值**（2026-10-02 修订）：基准图 xrq-dolphin.png 在 (350-354, 511-513) 处有一个
#: **真的空心环** —— 洞内是**海洋色 (0,34,68)**（透出背景），**不是填黑**。
#: 垂直剖面（x=352，未被相邻标记污染的那条）：外径 19 px，环厚 4-5 px/侧，洞径 9 px
#: ⇒ **洞径/外径 = 9/19 = 0.474**。旧值 0.55 是我按用户描述猜的，已作废。
#: ⚠ 水平剖面里洞径只有 5 px，但该处多个标记水平相连（环被并成 16-17 px 厚），
#:   外径不可测 ⇒ **取垂直口径 0.47**。为什么必须垂直取：水平那条剖面里左右两侧的环
#:   被**相邻标记并成了 16-17 px 厚**（该处轨迹密集），外径完全测不出来；垂直那条
#:   （x=352）上下只有 4-5 px 环厚、且上下都没有紧邻的标记，是唯一干净的取样方向。
#:   水平洞径偏小(5 vs 9)的原因未定（洞可能非正圆），记 unresolved。
#: ── 2026-10-02（R8）用**同一套覆盖率法**重量 ─────────────────────────────
#: 洞径 **9.0 px**（垂直剖面手工 50% 交叉）、外径取同批圆的实测 **20.74 px**
#: ⇒ 空心比 = 9.0 / 20.74 = **0.434**（上一版 0.474 用的外径 19 是已废的旧尺寸）。
#: ⚠ 全图**只有 1 个空心**（`i=66`）⇒ 这条仍然 **n=1、未充分验证**；且洞径来自剖面法
#:   而非团块法。上下方向可能撞到相邻标记，左右方向被并成 16-17 px ⇒ 只有垂直那条干净。
QQ_CORE_RATIO = 0.43            # 洞径 / 外径（n=1，未充分验证）
#: ⚠ 像素反推，未由二进制验证（x32dbg 动态取参在本环境未打通，
#:   见 output/exploration/re4_environment_pitfalls.md）。
#: 本文件里所有从 `xrq-dolphin.png` 量出来的数字都适用：
#:   · 空心比 **0.47（9/19）**（上面的 `QQ_CORE_RATIO`）
#:   · 尺寸表 圆 **⌀19** / 方块 **18×18** / 三角 **19×16**（下方 `QQ_MARKER_SIZE`）
#:   · 经纬度标注字号 **label_px = 23**（下方 `self.label_px`，基准墨迹 18 px 高 / 57 px 宽）
#: 二进制侧给到的只有"成对形状索引 ⇒ 通两档"这一结构事实，见 `re4_ty4_binary.md` §3.6。
QQ_CORE_COLOR = (0, 0, 0)       # 纯黑（基准图里没有纯黑，这是按"黑心"字面取的）

#: 自建超采样的倍率（与 `paint_aa.SS` 同值；本模块不 import 它的私有常量）
QQ_AA_SS = 4
#: 自建精灵缓存: key -> Surface
_QQ_SPRITE: Dict[tuple, pygame.Surface] = {}

#: 基准取景用的投影 ctx：`CONIC_REFERENCE` 的**固定投影参数**（n / λ0 / A / apex），
#: 由 99 个锚点拟合而来（99/99 投准）。基准取景 = 直接沿用这套参数 + 按轨迹跨度定画布尺寸，
#: **不再**用"画布反投影外包宽度"反推尺度（那个口径在圆锥下会虚大 ⇒ 轨道画小 1.8 倍）。
_REF_CTX: Dict[str, float] = dict(PP.CONIC_REFERENCE)

#: 三种标记的**尺寸**（px，宽 x 高）。全部从基准图 xrq-dolphin.png 逐形状实测。
#:
#: **测量口径（很重要：换口径结果会差近一倍）**：只取**孤立报点**（到最近其它报点
#: >30px），窗口 ±30，且要求连通域**不触碰窗口边界**；并排除与网格同色的 SCALE2
#: (102,204,153)（它既是强度色又是虚线网格色）。同时打印 fill 面积占比做交叉验证：
#: 圆 0.84 / 方块 1.00 / 三角 0.49 —— 与 pi/4、1、1/2 吻合，说明分类本身没错。
#:
#:     圆   19 x 19    (n=4, fill 0.84)
#:     方块 18 x 18    (n=4, fill 1.00)
#:     三角 19 x 16    (n=5, fill 0.49)   <= 宽与圆的直径相同, 只是**矮** 3px
#:
#: 2026-10-02 **两次修正**，如实记录：
#:   1. 先前量出「三角 31.5x26.0」—— **口径太松**（33px 小窗口、不检查是否触边），
#:      把**与相邻标记相连 / 被窗口裁切**的连通域也算了进去。那个值**作废**。
#:   2. 我一度按 31.5x26 改三角，于是三角反而**变大**到 29x24（基准是 19x16）。
#:      现在改回 19x16。原始（marker_r=9 时代）是 20.7x20.3 —— **宽度本来就接近，
#:      偏的是高度（高了约 4px）**。
#:
#: **Ty4 侧基准不同**：Ty4 的圆是 11 px 直径（app-feat 18 个风速桶实测），QQ 侧是
#:    19 px。两套基准**别混用** —— 这正是「维度化」要解决的：尺寸属于 marker 维度，
#:    由各自预设给值，不能有一个全局常量。
QQ_SHAPE_SIZE = {
    #: **尺寸更换记录（2026-10-02，R8）**
    #: 实测（方法：亚像素覆盖率剖面 `cov = d_bg/(d_bg+d_mc)` + 沿法向取 50% 交叉，
    #:       并与"团块法"交叉验证；样本：基准图 `xrq-dolphin.png`，
    #:       画布已与基准逐像素同尺寸 1837x1224，99 个锚点 99/99 投准）：
    #:   · 圆    ⌀ **20.74** px        n=41  IQR 20.57-20.79
    #:   · 方块  边 **18.09** px        n=4   IQR 18.09-18.09
    #:   · 三角  底 **19.63** × 高 **15.16** px  n=17  IQR 底 17.20-19.69 / 高 15.00-16.40
    #:       （三角"底边"由**质心行宽 × 1.5** 推出：等腰三角在离底边 h/3 处的宽 = 底 × 2/3）
    #: **旧值 19.0 / 18.0 / 19x16 已废** —— 那是早期 n=4/4/5 小样本、且用"精确色掩膜"量的；
    #:   那个口径会把抗锯齿边缘整圈排除掉 ⇒ **系统性偏小**（圆实测 20.74 vs 旧的 19.0）。
    #:   ⚠ `app-feat` 曾明确"sim-core 的 19/18/19x16 我既不确认也不否定"，本条即复核结论。
    #: ⚠ 提取口径的**自验证门**（R6 规矩：门不过则不出数）：在孤立圆点上比较
    #:   ray-H / ray-V / blob 三种口径，两两**中位差最差 0.22 px <= 0.5 px** ⇒ 放行。
    #: ⚠ 99 点里只有 62 点可测（33 点左/右粘连、4 点上/下粘连 ⇒ 射线走不到 50% 交叉），
    #:   粘连点已剔除，未用任何近似值补。
    "circle":   (20.74, 20.74),  # (宽, 高)
    "square":   (18.09, 18.09),
    "triangle": (19.63, 15.16),  # 宽(底) = 19.63, 高 15.16（不是等边, 也不是 31.5x26）
}

#: 亮度/色相序下的分档上界（kt）。由 `calibrate_thresholds` 分位数匹配标定：
#: 让 26 Dolphin 的 99 个报点落进 9 个色桶后的**计数分布**尽量贴近基准图实测计数。
#: ── 色阶：**逐点反解**出来的档位（2026-10-02 重做，取代"分位数匹配"那版）──────────
#:
#: 上一版用 `calibrate_thresholds()` 做**分位数匹配**：它只对齐"9 个色桶的计数分布"，
#: 并**不保证**风速→颜色的对应关系正确 ⇒ 实测逐点一致率只有 **73.7%**，
#: 而且**前期错得最狠**（用户原话："前期颜色差别这么大看不见?"）：
#:     i=0..2  15kt  我们给青 (0,250,244) ／ 基准给浅蓝 (94,186,255)
#:     i=10..13 35-60kt 我们给青绿/近白黄 ／ 基准给**青** (0,250,244)
#: 现在改为用 **99 个锚点的逐点对应**直接反解每个颜色的风速区间（见报告 §R7-②）：
#:
#:     基准色            实测风速区间   n
#:     (94,186,255) 浅蓝   15 .. 30    39
#:     (0,250,244)  青     35 .. 60     6     <= **青排在浅蓝之后**（不是"最弱"）
#:     (255,255,204) 近白黄 65 .. 90     7
#:     (255,231,117) 淡黄   85 .. 95    14
#:     (255,193,64)  琥珀  100 .. 110   18
#:     (255,143,32)  橙    115 .. 130    9
#:     (255,96,96)   红    140 .. 160    4
#:     (255,135,135) 浅红  140 .. 150    2
#:
#: ⚠ **重要修正**：`(102,204,153)` 绿色在 99 个报点里**一次都没被用作标记色**（0 个样本）——
#:   它是**虚线网格**的颜色。上一版把它当成"9 档里的一档"，于是同时造成两个问题：
#:   ① 25-30kt 的点画在同色网格上**看不见**；② 整条色阶多了一档、排列也对不上。
#:   现在**把它从标记色阶里去掉**（网格色另用 `QQ_COLORS["grid"]`）。
#:
#: ⚠ 顶端两档（红/浅红）在 140-160 kt 区间**互相冲突**（140kt 两点一个红一个浅红、
#:   160kt 是红而 150kt 是浅红）⇒ 该处**未能定档**，暂按 147 分界，记 unresolved。
#: ⚠ 第 3 档上界 2026-10-02 从 92 收到 **82**：实测 6 个 **85 kt** 的点基准图给的都是
#:   `(255,231,117)`（不是 `(255,255,204)`），而 65-80 kt 的点才是 `(255,255,204)`
#:   ⇒ 分界在 80 与 85 之间。收到 82 后逐点一致率 91.9% → **98.0%**。
QQ_BINS: Tuple[float, ...] = (32.0, 62.0, 82.0, 97.0, 112.0, 135.0, 147.0)
#: 标记色档：与 `QQ_BINS` 一一对应（**8 档，无绿色**）
QQ_RAMP: Tuple[Tuple[int, int, int], ...] = (
    (94, 186, 255),   # <=32   浅蓝   (DB/TD/SD/EX 全部；实测 39 点)
    (0, 250, 244),    # <=62   青     (TS；实测 6 点)
    (255, 255, 204),  # <=92   近白黄
    (255, 231, 117),  # <=97   淡黄
    (255, 193, 64),   # <=112  琥珀
    (255, 143, 32),   # <=135  橙
    (255, 96, 96),    # <=147  红
    (255, 135, 135),  # >147   浅红
)

#: 基准图里 9 个强度色的**实测像素计数**（用于直方图距离与分档标定）
QQ_REF_COUNTS: Tuple[int, ...] = (1650, 6915, 2507, 1732, 3811, 5206, 2582, 1203, 423)


def color_for_wind(w_kt: float) -> Tuple[int, int, int]:
    """风速(kt) → 强度色。弱→强按 `QQ_BINS` 分档。"""
    for i, hi in enumerate(QQ_BINS):
        if w_kt <= hi:
            return QQ_RAMP[i]
    return QQ_RAMP[-1]


def calibrate_thresholds(winds: Sequence[float],
                         ref_counts: Sequence[int] = QQ_REF_COUNTS,
                         n_iter: int = 400) -> Tuple[float, ...]:
    """用**分位数匹配**把 `QQ_BINS` 标定到基准图的 9 色计数上。

    思路：把参考图的 9 个色桶计数归一化成累计分布，再取风序列的对应分位数作为
    档位边界 ⇒ 渲染后的色计数分布与基准图尽量一致。返回新的分档上界。
    这是**可复现的定量标定**，不是手调。
    """
    w = np.sort(np.asarray(list(winds), dtype=float))
    if w.size == 0:
        return tuple(QQ_BINS)
    c = np.asarray(list(ref_counts), dtype=float)
    c = c / c.sum()
    c = c[:-1]                     # 前 8 个累计边界（第 9 桶是余量）
    cum = np.cumsum(c)
    bins = [float(np.quantile(w, min(0.999, max(0.001, q)))) for q in cum]
    # 单调化，避免并列风值造成的逆序
    out = []
    for b in bins:
        while out and b < out[-1] + 0.5:
            b = out[-1] + 0.5
        out.append(b)
    return tuple(out)


def _line_frac(surf, color, p1, p2, w: float) -> None:
    """**分数宽度**的抗锯齿直线。

    为什么需要它：`paint_aa.aa_line` 里是 `w = max(1, int(width))` ⇒ **宽度被量化成整数**
    （实测：1.1~1.8 的像素数完全相同、2.0 才跳到 2）。而基准图轨迹白线的等效宽度落在
    **3 与 4 之间**（见报告 R10-F2 扫描表），整数档一头一尾都落在验收区间外。

    做法与 `paint_aa` 同源（4x 超采样 + smoothscale），差别只有两点：
      · 线宽不取整（`w*ss`）；
      · 先把缓存面填成 `(color, alpha=0)` 再画 —— 否则 smoothscale 会把透明区的
        **黑 RGB** 平均进边缘、按非预乘 blit 后形成一圈**黑描边**（本项目 Q1 已定位过的坑）。
    按 ~48 px 分块，避免长线段产生巨大临时面。
    """
    w = float(w)
    # ⚠ R11-T3：**不再走 `aaline` 短路**。原来 w<=1.0 时委托 `paint_aa.aa_line(...,1)` -> `aaline`，
    #   它在深色底上**峰值到不了纯色**（实测 grid_w=1.0 时纯网格色只有 2785，指基准 6046 的 0.46x）。
    #   改成照样走 4x 超采样后：1.0 px 的线在 4x 面上市 4 px 宽、降采样时**芯部达满 alpha**
    #   ⇒ 既保住真实线宽 1.0，又能出实心纯色（实测见报告 R11-T3 对照表）。
    #   只在 w 极小时才退回 aaline（避免 4x 面宽 0）。
    if w < 0.35:
        paint_aa.aa_line(surf, color, p1, p2, 1)
        return
    ss = 4
    x0, y0 = float(p1[0]), float(p1[1])
    x1, y1 = float(p2[0]), float(p2[1])
    L = max(abs(x1 - x0), abs(y1 - y0))
    n = max(1, int(L // 48) + 1)
    col = (int(color[0]), int(color[1]), int(color[2]))
    for i in range(n):
        t0 = i / float(n); t1 = (i + 1) / float(n)
        ax, ay = x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0
        bx, by = x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1
        pad = w * 0.5 + 2.0
        bx0 = int(min(ax, bx) - pad); by0 = int(min(ay, by) - pad)
        bx1 = int(max(ax, bx) + pad) + 2; by1 = int(max(ay, by) + pad) + 2
        bw = bx1 - bx0; bh = by1 - by0
        if bw <= 0 or bh <= 0:
            continue
        big = pygame.Surface((bw * ss, bh * ss), pygame.SRCALPHA)
        big.fill((col[0], col[1], col[2], 0))
        pygame.draw.line(big, col, ((ax - bx0) * ss, (ay - by0) * ss),
                         ((bx - bx0) * ss, (by - by0) * ss),
                         max(1, int(round(w * ss))))
        surf.blit(pygame.transform.smoothscale(big, (bw, bh)), (bx0, by0))


#: ── 用户 2026-10-04 直接裁定的**类型/颜色规则**（与 Ty4 侧同源、逐字相同）──────
#: 用户原话："三角形方块圆的规则完全一样!" / "TD为什么要变色?" / "SD也不要变色!" /
#:          "SS要变色但没有td的蓝色" / "除了TC的几个类型,只有EX,SS,LO,WV变色" /
#:          "不要让我看见灰色图标"。用户明确说"**不要完全参考 ty4 了**" ⇒ 按规则实现，
#:          **不再回去反推 Ty4**。
QQ_TRIANGLE: Tuple[str, ...] = ("DB", "EX", "WV", "LO", "MD", "INV", "INVEST")
QQ_SQUARE: Tuple[str, ...] = ("SD", "SS")
#: 会**随风速变色**的码 = 热带风暴及以上（TS/TY/ST/HU）+ EX/SS/LO/WV
QQ_VARIES: Tuple[str, ...] = ("TS", "TY", "ST", "HU", "EX", "SS", "LO", "WV")
#: **固定色**的码 —— 一律用色阶最弱那一档 `QQ_RAMP[0]`（避免造新色）：TD/SD/DB/MD/INV/INVEST
QQ_FIXED: Tuple[str, ...] = ("TD", "SD", "DB", "MD", "INV", "INVEST")
#: `SS` 特例：变色，但**下限夹到第 2 档**（不含最弱那档的蓝）
QQ_SS_MIN_IDX = 1


def color_for_code(w_kt: float, code: str = "") -> Tuple[int, int, int]:
    """风速(kt) + 类型码 → 颜色（**用户裁定的规则**）。

    · `QQ_FIXED` 里的码 ⇒ 恒定 `QQ_RAMP[0]`，**不随风速变**；
    · `SS` ⇒ 按风速取档，但**下限夹到 `QQ_RAMP[1]`**（不含最弱的蓝）；
    · `QQ_VARIES` 及其它未列出的码 ⇒ 按风速取档（不因出现新码而退化）。
    """
    c = (code or "").strip().upper()
    if c in QQ_FIXED:
        return QQ_RAMP[0]
    idx = len(QQ_RAMP) - 1
    for i, hi in enumerate(QQ_BINS):
        if float(w_kt) <= hi:
            idx = i
            break
    if c == "SS":
        idx = max(idx, QQ_SS_MIN_IDX)
    return QQ_RAMP[idx]


def _is_official_point(p) -> bool:
    """是否**正式报**。口径与 `paint_render._is_official` **一致**（UTC 时次 ∈ {00,06,12,18}）。

    为什么不用 `TrackPoint.official`：那个字段在 `app/typhoon.py:33` 定义为默认 `True`，
    而**全仓没有任何地方给它赋值**（`grep -rn official --include=*.py app/ simulator/` 只有
    读取点与 `paint_render._is_official`）⇒ 用它判定会**永远视为正式报**、空心永不触发。
    app 真正在跑的是时次法，所以这里跟它对齐。

    U7 实测：两种判据在 26 Dolphin 的 99 个报点上**只差 1 个点**（`i=66 / 2026080909`，09Z）。
    """
    t = str(p.get("t", "") or "")
    hh = t[8:10] if len(t) >= 10 else t[-2:]
    return hh in ("00", "06", "12", "18")


def shape_for_code(code: str) -> str:
    """报点类型码 → 标记形状。**用户 2026-10-04 裁定**（与 Ty4 侧同一套规则）：

        三角 : DB, EX, WV, LO, MD, INV, INVEST
        方块 : SD, SS
        圆   : 其余全部（TD, TS, TY, ST, HU, …）

    ⚠ 与基准图的关系：26 Dolphin 的 99 个报点实测 **SD→方块 4/4、EX→三角 4/4**
    （4x 放大图 `ref_EXSD.png` 可直接看到），与上表**一致**；`INV/INVEST/WV/LO/MD/SS`
    在该基准图里**没有样本**，形状由**用户裁定**（不再按"家族约定"猜）。
    ⚠ 2026-10-04 修：旧实现只列了 `WV/LO/MD`，把 **`INV`/`INVEST` 归成了圆**，
    与 Ty4 侧 `{DB,EX,WV,LO,MD,INV,INVEST}` 全三角**不一致** ⇒ 违反用户第 1 条。
    """
    c = (code or "").strip().upper()
    if c in QQ_TRIANGLE:
        return "triangle"
    if c in QQ_SQUARE:
        return "square"
    return "circle"


# ── 陆地掩码：(10800x5400, 30 px/°) **按输出画布裁剪窗口**后采样 ──
#: 高分辨率底图（alpha=255 陆 / alpha=0 海）。repo 内只读使用。
LAND_PNG = os.path.join("map", "land.png")
#: 源图分辨率（px/°）—— 10800/360 = 30
LAND_PPD = 30.0
#: 裁剪窗口在经纬度上多留的余量（度），防边界采样溢出
LAND_MARGIN_DEG = 1.0
#: 相对**源图**的采样倍率：1.0 = 直接用 30 px/° 的原始分辨率（默认）。
#: 调小可省内存/时间但会让海岸线变钝；调大无意义（源图没有更多信息）。
LAND_RES = 1.0
#: 若 >0: 把裁剪窗口**降采样**到该 px/度 再采样(仅供 A/B 对比块状程度 / 省内存)。
LAND_PPD_MAX = 0.0

#: 海岸线超采样倍率(1 = 关)。配合源图双线性采样一起给出平滑边缘。
COAST_SS = 2
_LAND_IMG = {"tried": False, "im": None}
#: 全局兜底掩码（0.5° land_cell.npz）。**注意行序与 land.png 相反**：
#: npz 的 row 0 = 90°S；land.png 的 row 0 = 90°N。这个差异曾导致陆地上下翻转。
_LAND_FALLBACK = {"tried": False, "frac": None, "dlon": 0.5, "dlat": 0.5}


#: `_open_land_png()` 的定位结果（供 `stats["land_source"]` 与自检读取）
_LAND_SRC = {"path": None, "tried": [], "source": None}


#: ── R16：墨迹尺寸改挂**地图比例 k**（px/度）────────────────────────────────
#: 标定（26 Dolphin 基准档 1837x1224，实测）：**k = 15.011 px/°**、标记 ⌀20.74、
#: line_w = 3.45、grid_w = 1.2。于是每"度"对应多少墨迹：
#:     marker_r = (20.74/2) * (k / K_REF)     line_w = 3.45 * (k / K_REF)
#:     grid_w   = 1.2      * (k / K_REF)
#: 基准档下 k ≈ K_REF ⇒ 三个值与现状相同（差 ~5e-6，被 int(round()) 量化吸收）。
#:
#: ⚠ 为什么不再用 geom_scale = canvas_w / 1837：它假定"画布越大 ⇒ 地图比例越大"，
#: 对**单台风**成立（bbox 固定时 k ∝ canvas_w，两式等价），但对**多台风/全球图不成立**
#: —— k 由并集 bbox 决定、与画布脱钩；两边都正比画布宽 ⇒ 点径/每度像素恒定
#: ⇒ 全球图放大画布只是把整团一起放大（110 个台风糊成一团的根因）。
#: ⚠ R17：改用**基准档实测**的 k（15.1247），而不是从"画布反投影外包"推的 15.0111。
#: 旧的 15.0111 只在"ctx.scale 归一到 1.0"时才成立，而基准档实际 scale=1.00678
#: ⇒ 三个尺寸会比标定值大 0.76%，当时**靠 int(round()) 量化侥幸吸收**（U40）。
#: 现在配合下面的"取小"口径，基准档走**画布口径**（几何上恰等于实测值）⇒ 构造上相等。
QQ_K_REF = 15.124729349                     # 基准档实测 k（px/度）
QQ_MARKER_PX = 20.74 / 2.0                  # 基准档标记半径（实测）
QQ_LINE_PX = 3.45                           # 基准档线宽（实测）
QQ_GRID_PX = 1.2                            # 基准档网格宽（实测）
#: 「角度口径」= 每"度"对应多少墨迹（物理含义：标记在地图上恒占 ≈1.38°）。
#: 分子取自基准档实测像素、分母用 `QQ_K_REF`（基准档实测 k），保证基准档两口径相等。
QQ_MARKER_DEG = QQ_MARKER_PX * 2.0 / QQ_K_REF   # 1.3817 度（标记直径）
QQ_LINE_DEG = QQ_LINE_PX / QQ_K_REF             # 0.2281
QQ_GRID_DEG = QQ_GRID_PX / QQ_K_REF             # 0.0793


def _px_per_deg(ctx, W: int, H: int, bounds=None) -> float:
    """地图比例 **k = 画布宽像素 / 画布覆盖的经度跨度**（px/度）。基准档 = 15.011。

    **把 ctx['scale'] 归一到 1.0 再反投影**：`canvas_lonlat_envelope` 的跨度随 scale 变
    （基准档 scale=1.00678），不归一会得到 15.125 而不是标定值 15.011。
    """
    c = dict(ctx)
    c["scale"] = 1.0
    try:
        env = PP.canvas_lonlat_envelope(c, (W, H), pad_px=0.0)
    except Exception:
        env = None
    if env and (env[1] - env[0]) > 1e-9:
        return float(W) / float(env[1] - env[0])
    if bounds is not None and (float(bounds[1]) - float(bounds[0])) > 1e-9:
        return float(W) / (float(bounds[1]) - float(bounds[0]))
    return float(W) / 360.0


#: ── 模块级符号**存在性哨兵**（R14 教训，永久保留）──────────────────────────
#: 背景：本文件里"函数定义"与"模块级常量区"是**交错**的（例：`shape_for_code`
#: 之后紧跟着"陆地掩码"常量区）。2026-10-04 我用 `s.index("\ndef ", start+10)`
#: 去找一个函数的结尾，**那个切片把函数之间整段吞掉了** —— 包括 `LAND_PNG` /
#: `LAND_PPD` 与模块级 `_LAND_IMG` / `_LAND_FALLBACK`，直到某条渲染路径才
#: `NameError: name '_LAND_FALLBACK' is not defined`（不是 import 时报错）。
#: 这里在**导入期**就把它们显式查一遍：少了任何一个立刻炸在导入处，
#: 而不是等到跑某张图才炸。**不要删这个哨兵**；新增/重命名这些符号时同步更新清单。
_REQUIRED_MODULE_SYMBOLS: Tuple[str, ...] = (
    "LAND_PNG", "LAND_PPD", "LAND_MARGIN_DEG", "LAND_RES", "COAST_SS",
    "_LAND_IMG", "_LAND_FALLBACK", "_LAND_SRC",
    "QQ_BINS", "QQ_RAMP", "QQ_TRIANGLE", "QQ_SQUARE", "QQ_VARIES", "QQ_FIXED",
    "QQ_K_REF", "QQ_MARKER_PX", "QQ_LINE_PX", "QQ_GRID_PX",
    "QQ_MARKER_DEG", "QQ_LINE_DEG", "QQ_GRID_DEG",
)
_MISSING_SYMBOLS = [k for k in _REQUIRED_MODULE_SYMBOLS if k not in globals()]
if _MISSING_SYMBOLS:
    raise RuntimeError(
        "paint_render_qq: 模块级符号丢失 %s —— 多半是某个补丁把函数之间的常量区"
        "一起替换掉了（见本哨兵上方注释）。" % _MISSING_SYMBOLS)


def _find_land_png():
    """从**仓库根**方向定位 `map/land.png`。返回 `(path_or_None, tried_paths)`。

    ⚠ **为什么不能再用 `dirname(dirname(__file__))`（R13 修的陷阱）**：
    原来的定位是"从本文件上溯两层"，只有当本文件*恰好*位于 `<repo>/app/` 时才正确。
    **一旦本模块被复制/挪到别处**（例：把改前版本拷到 `output/exploration/_backup/state_r12/`
    做 A/B 对拍），上溯两层就落到 `_backup/`，那里没有 `map/land.png` ⇒
    原来的 `except` 把 `im` 设成 `None`、**静默**回落到 0.5° 的 `land_cell.npz` 粗掩码：
    **无异常、无日志**，只是底图无声降级（实测那次让 17.5% 的像素从海洋变陆地）。
    这正是"我们刚踩、白花一轮"的那个坑。

    现在的做法：**沿着 `__file__` 的各级祖先目录向上搜**，取第一个含 `map/land.png` 的目录；
    再兜底试 CWD 与 CWD 的各级祖先。**找不到就返回 None，并把所有试过的路径带回去**
    （由调用方 warning 出来），**不再静默**。
    """
    tried = []
    here = os.path.dirname(os.path.abspath(__file__))
    roots = []
    d = here
    for _ in range(8):                      # 沿 __file__ 向上
        roots.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    cwd = os.path.abspath(os.getcwd())
    d = cwd
    for _ in range(8):                      # 再沿 CWD 向上
        if d not in roots:
            roots.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for r in roots:
        cand = os.path.join(r, LAND_PNG)
        tried.append(cand)
        if os.path.isfile(cand):
            return cand, tried
    return None, tried


def _open_land_png():
    """懒打开 `map/land.png`（**不** load 全量到内存）。

    ⚠ 定位失败时**不再静默回落**：打印一份 warning（含所有试过的路径），并把
    `_LAND_SRC["source"]` 标成 `"fallback_npz_0.5deg"`，由 `render()` 写进
    `stats["land_source"]`，让调用方能立刻看出"底图降级了"。
    """
    if _LAND_IMG["tried"]:
        return _LAND_IMG["im"]
    _LAND_IMG["tried"] = True
    path, tried = _find_land_png()
    _LAND_SRC["tried"] = tried
    if path is None:
        _LAND_SRC["path"] = None
        _LAND_SRC["source"] = "fallback_npz_0.5deg"
        import warnings
        warnings.warn(
            "paint_render_qq: 找不到 %s —— 底图将**静默降级**为 0.5 度粗掩码 "
            "(land_cell.npz)。试过的路径: %s" % (LAND_PNG, tried),
            RuntimeWarning, stacklevel=2)
        print("[paint_render_qq] WARNING: 找不到 %s，回落到 0.5 度粗掩码。试过: %s"
              % (LAND_PNG, tried))
        _LAND_IMG["im"] = None
        return None
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        _LAND_IMG["im"] = Image.open(path)
        _LAND_SRC["path"] = path
        _LAND_SRC["source"] = "map/land.png"
    except Exception as e:
        _LAND_SRC["path"] = path
        _LAND_SRC["source"] = "fallback_npz_0.5deg"
        print("[paint_render_qq] WARNING: 打开 %s 失败(%s)，回落到 0.5 度粗掩码" % (path, e))
        _LAND_IMG["im"] = None
    return _LAND_IMG["im"]


def _fallback_frac():
    if _LAND_FALLBACK["tried"]:
        return _LAND_FALLBACK
    _LAND_FALLBACK["tried"] = True
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with np.load(os.path.join(root, "simulator", "env", "model", "land_cell.npz")) as z:
            a = np.asarray(z["landfrac"], dtype=np.float32)
        _LAND_FALLBACK.update({"frac": a, "dlon": 360.0 / a.shape[1],
                               "dlat": 180.0 / a.shape[0]})
    except Exception:
        _LAND_FALLBACK["frac"] = None
    return _LAND_FALLBACK


def _land_window(lon0, lon1, lat0, lat1):
    """裁出源图上覆盖该经纬度窗口的一块，返回 。

     是 float32 的**陆地占比 0..1**（源图是二值 alpha，裁剪 + 双线性插值后
    天然给出 0..1 的边缘覆盖率 ⇒ 海岸线抗锯齿）。**arr 的 row 0 = 北(纬度大)**。
    内存只与窗口有关（例如 75°x48° 窗口 ≈ 3.2 M px ≈ 13 MB float32），
    **不会**把 10800x5400（233 MB）常驻。
    """
    im = _open_land_png()
    if im is None:
        return None
    m = LAND_MARGIN_DEG
    lon0 = max(0.0, lon0 - m); lon1 = min(360.0, lon1 + m)
    lat0 = max(-90.0, lat0 - m); lat1 = min(90.0, lat1 + m)
    if lon1 - lon0 < 1e-6 or lat1 - lat0 < 1e-6:
        return None
    x0 = int(max(0, np.floor(lon0 * LAND_PPD)))
    x1 = int(min(im.size[0], np.ceil(lon1 * LAND_PPD)))
    # land.png: y = (90 - lat) * ppd  ⇒ 纬度越大 y 越小
    y0 = int(max(0, np.floor((90.0 - lat1) * LAND_PPD)))
    y1 = int(min(im.size[1], np.ceil((90.0 - lat0) * LAND_PPD)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if LAND_PPD_MAX > 0.0 and LAND_PPD > LAND_PPD_MAX:
        scale = LAND_PPD_MAX / LAND_PPD
        w = max(2, int((x1 - x0) * scale)); h = max(2, int((y1 - y0) * scale))
        tile = im.crop((x0, y0, x1, y1)).getchannel("A").resize((w, h), Image.BOX)
        ppd = (x1 - x0) * scale / max(1e-9, (x1 - x0)) * LAND_PPD
        ppd = LAND_PPD_MAX
        return np.asarray(tile, dtype=np.float32) / 255.0, float(x0), float(y0), float(ppd)
    if LAND_RES != 1.0:
        w = max(2, int((x1 - x0) * LAND_RES)); h = max(2, int((y1 - y0) * LAND_RES))
        tile = im.crop((x0, y0, x1, y1)).getchannel("A").resize((w, h))
        ppd = LAND_PPD * (w / float(x1 - x0))
        arr = np.asarray(tile, dtype=np.float32) / 255.0
        return arr, float(x0), float(y0), ppd
    tile = im.crop((x0, y0, x1, y1)).getchannel("A")
    arr = np.asarray(tile, dtype=np.float32) / 255.0
    return arr, float(x0), float(y0), LAND_PPD


def _bilinear(arr, fx, fy):
    """双线性采样（fx/fy 是**像素坐标**，可浮点）。越界夹取。"""
    ny, nx = arr.shape
    fx = np.clip(fx, 0.0, nx - 1.0001)
    fy = np.clip(fy, 0.0, ny - 1.0001)
    ix = fx.astype(np.int32); iy = fy.astype(np.int32)
    dx = (fx - ix).astype(np.float32); dy = (fy - iy).astype(np.float32)
    a = arr[iy, ix]; b = arr[iy, ix + 1]
    c = arr[iy + 1, ix]; d = arr[iy + 1, ix + 1]
    return (a * (1.0 - dx) * (1.0 - dy) + b * dx * (1.0 - dy)
            + c * (1.0 - dx) * dy + d * dx * dy)


def _canvas_window(ctx, W, H, pad_px=2.0):
    """画布覆盖的经纬度包围盒（采样四条边 + 四角后取极值）。"""
    xs = np.concatenate([np.linspace(-pad_px, W + pad_px, 48)] * 2
                        + [np.full(48, -pad_px), np.full(48, W + pad_px)])
    ys = np.concatenate([np.full(48, -pad_px), np.full(48, H + pad_px)]
                        + [np.linspace(-pad_px, H + pad_px, 48)] * 2)
    lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
    # ⚠ **不要**把 lon 归一化到 -180..180: land.png 是 0..360, 混用会让裁剪窗口
    #   退化成整圈 [0,360] 并把西侧全夹到 lon=0(实测表现为右半张图多出一大块假陆地)。
    span = float(lon.max() - lon.min())
    if span > 300.0:                       # 跨 0° 经线: 交给兜底掩码, 不做窗口裁剪
        return None
    return (float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max()))


def _sample_land_window(win, ctx, xs, ys):
    """画布像素 → 陆地占比 0..1（走**投影**，不是拉伸图片）。"""
    arr, wx0, wy0, ppd = win
    lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
    fx = lon * ppd - wx0
    fy = (90.0 - lat) * ppd - wy0        # land.png 的 row 0 是 90°N
    return _bilinear(arr, fx, fy)


class QQTrackRenderer:
    """QQ 机器人样式的路径图渲染器。接口与 `paint_render.TrackMapRenderer` 同形。"""

    def __init__(self, opts: Optional[dict] = None) -> None:
        o = dict(opts or {})
        # ── 维度键：与 `app/paint_style.py`(app-feat) 的**冻结命名**一致 ──────
        # ⚠ 本模块**只消费** `resolve()` 出的 dict，**不 import** `paint_style`
        #   （避免循环依赖，也便于它未落地时先跑）。默认值就是 `QQ_PRESET`：
        #     conic / labels=edge / land_flat / by_status / qq / legend=none / gradient=False
        self.projection = str(o.get("projection", "conic"))
        #: **地图中央经度**（可选，默认 `None` = 用 `_REF_CTX['lon_c']`≈152.4°E，逐位兼容）。
        #: 等距圆柱把经度折叠在"中央经度 ±180°"，所以**缝**在 `lon_c + 180`。
        #: 单台风时无所谓；**多台风/全球图**时如果缝落在有数据的洋区，会把一条轨迹
        #: 劈成两半扔到画布左右两端（实测：缝在 28°W 时，Dolly 70.6°W–16.4°W 被劈开，
        #: x 从 921 跳到 7341）。调用方应把中央经度设在**数据经度最大的空档**里。
        self.lon_c = o.get("lon_c")
        self.output_size = o.get("output_size")
        self.grid = bool(o.get("grid", True))
        self.grid_step = int(o.get("grid_step", 10))
        # labels: "none" | "edge" | "inside"（兼容旧的 bool）
        _lab = o.get("labels", "edge")
        if isinstance(_lab, bool):
            _lab = "edge" if _lab else "none"
        self.labels = str(_lab)
        # basemap: "map_png" | "land_flat" | "none"（兼容旧的 coast=bool）
        _bm = o.get("basemap")
        if _bm is None:
            _bm = "land_flat" if bool(o.get("coast", True)) else "none"
        self.basemap = str(_bm)
        # marker: "by_status" | "uniform"
        self.marker = str(o.get("marker", "by_status"))
        # palette: "qq" | "2023" | "classic"
        self.palette = str(o.get("palette", "qq"))
        # legend: "none" | "tl" | "tr" | "bl" | "br" | "auto"
        self.legend = str(o.get("legend", "none"))
        #: 非正式报是否画黑心（用户点名的 QQ 样式）。默认开；基准图无黑心故不影响其复现。
        self.black_core = bool(o.get("black_core", True))
        #: 经纬度标注字号（px）。基准图实测标注墨迹 **18 px 高 / 57 px 宽**("40°N")，
        #: 而 `f_s`(14) 只有 **10 px** ⇒ 需要 ~1.8 倍。用 pygame 直接按 px 建字体
        #: （`f_l`(22) 也只有 16 px，不够）。
        #: ⚠ 像素反推，未由二进制验证（见本文件上方 `QQ_CORE_RATIO` 处的统一标注）。
        #: ── R9：画布**参数化** + **几何等比缩放** ─────────────────────────────
        #: 用户要求（2026-10-02）：
        #:   · 几何基准 = **1920x1080**（所有几何常数都在这个尺度下定义）；
        #:   · 默认出图 = **2560x1440**；
        #:   · 支持任意更大画布（canvas_w / canvas_h 是参数）；
        #:   · **长宽比自由**（不强制 16:9）；
        #:   · 画布变大时**标记 / 线宽 / 字号 / 留白全部等比放大**。
        #: `geom_scale` 的定义：以**实测基准画布宽 1837** 为锚（`QQ_SHAPE_SIZE` 的
        #: ⌀20.74 等就是在 1837x1224 下量的）⇒ geom_scale = canvas_w / 1837。
        #:   · canvas_w=1837 ⇒ 1.000 ⇒ 与 xrq-dolphin.png **逐像素同尺度**（叠加比对用）；
        #:   · canvas_w=1920 ⇒ 1.0452 ; canvas_w=2560 ⇒ 1.3936（两档之比 = 2560/1920 = 1.3333）；
        #:   等价于 Lead 说的 canvas_w/1920 x (1920/1837)，只是把锚点放在**实测画布**上。
        self.canvas_w = int(o.get("canvas_w", 2560))
        self.canvas_h = int(o.get("canvas_h", 1440))
        self.geom_scale = float(o.get("geom_scale", self.canvas_w / 1837.0))
        self.label_px = int(round(o.get("label_px", 23) * self.geom_scale))
        self._label_font = None
        # gradient_line + 细分（与 paint_render 同名同默认）
        self.gradient_line = bool(o.get("gradient_line", False))
        self.gradient_subdiv = max(1, int(o.get("gradient_subdiv", 8)))
        self.map_path = o.get("map_path")
        self._warned = set()
        #: `@xrq` 斜向水印。**默认关**（2026-10-03 用户裁定：水印属于自评提供者 xrq，
        #: 我们要的是样式复刻，不需要替对方署名）。代码保留 ⇒ 显式 `watermark=True` 仍可开。
        self.watermark = bool(o.get("watermark", False))
        self.margin_w = float(o.get("margin_w", 0.0))
        self.margin_h = float(o.get("margin_h", 0.0))
        # ── 画布下限：照 Typhoon4 的 Width / Height ─────────────────────────
        # Ty4 的公式(规格书 §2.2, 语言包键 57-60 原文确证 "最小宽度"/"最小高度"):
        #     W = max(Width,  track_w + 2*WidthSpace)
        #     H = max(Height, track_h + 2*HeightSpace)
        # 默认取 Settings.ini 的 Width=1920 / Height=1080。
        # 原先这里下限只有 320x240 ⇒ 小台风会渲出 320x259 这种"缩略图"。
        # ⚠ 关键差别: Ty4 的 px/° **恒定**, 所以画布变大是"**看到更多地图**",
        #   不是"把轨迹放大"。本渲染器照此办理(见 render 里"同一比例重新居中")。
        self.min_w = int(o.get("min_w", 1920))
        self.min_h = int(o.get("min_h", 1080))
        #: 内存护栏(我们加的, Ty4 没有): 超大包围盒不无限涨
        self.max_w = int(o.get("max_w", 4096))
        self.max_h = int(o.get("max_h", 3072))
        #: 内容 px/°(Ty4 是 30; 本样式按基准图观感取 ~22.6)
        # ── ① 取景：复现基准图 `xrq-dolphin.png` 的取景（2026-10-02 重做）──────────
        # 实测（报告 §R7-①，用 99 个锚点的投影反推）：
        #   · 基准画布 1837x1224，宽高比 **1.5008**；
        #   · 基准画布反投影出来的经纬度外包 = lon[91.24, 213.62] lat[-7.34, 54.68]；
        #     轨迹经纬度 bbox = lon[113.20, 191.60] lat[9.50, 43.80]
        #     ⇒ **经度方向左右各留 22.0°**（左 21.96 / 右 22.02，**对称**，这是强信号）；
        #   · 把"经度窗口"映射到画布宽 ⇒ **15.01 px/度**（1837 / 122.376）。
        # 旧逻辑是 `min_w=1920 / min_h=1080` + "同尺度重定心"，那是**拍出来的**，
        # 复现不了基准取景：实测我们 1952x1099 vs 基准 1837x1224（基准偏竖）。
        #: 经度方向留白（度，实测 ±22.0）
        self.lon_pad_deg = float(o.get("lon_pad_deg", 22.0))
        #: 画布宽高比（实测基准 1837/1224）
        #: 经度窗口 -> 画布宽 的 px/度（实测 15.01）
        self.lon_ppd = float(o.get("lon_ppd", 1837.0 / 122.376))
        #: ── R8b：基准取景 = **固定画布 + 按 bbox 比例留白 + 缩放到轨迹占宽 track_w_frac** ──
        #: 用户要求（2026-10-02）：**留白必须按"bbox 跨度 × 比例"算，不能是固定角度**
        #:   —— 固定角度对大/小台风一视同仁，小台风会被留白挤到画布中间一小块。
        #: 基准实测：轨迹经度跨度 78.4°，画布窗口 ≈94.5° ⇒ 总留白 16.1° ⇒ 每侧 8.05°
        #:   ⇒ **每侧留白比例 = 8.05/78.4 = 0.103**（总 0.205）。
        #: 实现上等价地表达为：**轨迹横向跨度占画布宽 track_w_frac = 1/(1+2×0.103) = 0.828**
        #:   —— 这个式子是"按比例"的：不论台风大小，轨迹都占画布宽 82.8%。
        #: 每侧留白 = pad_ratio x (轨迹 bbox 跨度)。用户指定 **0.1**（旧值 0.1039 是实测反推）。
        self.pad_ratio = float(o.get("pad_ratio", 0.1))
        #: 轨迹横向跨度 / 画布宽（由 pad_ratio 推出；基准实测 1520.5/1837 = 0.8277）
        self.track_w_frac = float(o.get("track_w_frac", 1.0 / (1.0 + 2.0 * self.pad_ratio)))
        #: 纵向同口径系数（默认等于横向）。**R12 修 bug**：原来尺度只用了投影宽 `_sx`，
        #: `_sy` 从头到尾没参与计算 ⇒ 当"投影高 × k > 画布高"时轨迹**纵向溢出被裁**。
        #: 26 Dolphin 的投影宽高比 ≈2.07 > 画布 1.5 ⇒ 恰好是"宽为约束方向"，**蒙对了**；
        #: 换一张"宽高比 < 画布宽高比"的图（如 16.8°×12.8° 的合成 b-deck）立刻暴露。
        #: 现在改成**双方向 contain**：约束方向的轨迹占比 = 1/(1+2×pad_ratio) = 83.33%。
        self.track_h_frac = float(o.get("track_h_frac", self.track_w_frac))
        #: 兼容旧名：frame_w/frame_aspect 现在只是 canvas_w/canvas_h 的别名（R9 参数化）
        self.frame_w = int(o.get("frame_w", self.canvas_w))
        self.frame_aspect = float(o.get("frame_aspect", float(self.canvas_h) / max(1, self.canvas_w)))
        #: 轨迹投影 bbox 中心落在画布的位置（基准实测：x 986.25/1837、y 542.05/1224）
        self.track_cx_frac = float(o.get("track_cx_frac", 986.25 / 1837.0))
        self.track_cy_frac = float(o.get("track_cy_frac", 542.05 / 1224.0))
        #: 关掉"基准取景"回到旧的 min_w/min_h 逻辑（A/B 用）
        self.frame_reference = bool(o.get("frame_reference", True))
        #: 旧路径用的 px/度（`frame_reference=False` 时才走到；保留以免那条分支 AttributeError）
        self.px_per_deg = float(o.get("px_per_deg", 22.6))
        self.aa = bool(o.get("aa", True))
        #: 圆半径。默认 9.5 = 基准图圆的 ⌀19.0/2（见 `QQ_SHAPE_SIZE`）。
        #: 显式传入等价于"整体缩放三种形状"（旧行为：默认 9.0）。
        #: R16：用户**显式**给过尺寸就尊重；否则 render() 里按地图比例 k 重算。
        self._explicit_sizes = {kk: (kk in o) for kk in ("marker_r", "line_w", "grid_w")}
        self.px_per_deg_k = None
        self.marker_r = float(o.get("marker_r",
                                       QQ_SHAPE_SIZE["circle"][0] / 2.0 * self.geom_scale))
        # 轨迹白线的宽度（**基线值，基准档 1837x1224 下生效**）。
        # 实测：基准图轨迹白线在框 (380,200)-(820,560) 里 light(三通道>170)=531、
        #   其中纯白(>240)=384、中位色 (255,255,255)；等效宽度落在 **3 与 4 之间**。
        # `paint_aa.aa_line` 会把宽度量化成整数（1.1~1.8 全等于 1、2.0 才到 2），
        #   而整数档一头一尾都在验收区间外（w=3 -> 449/301；w=4 -> 607/486）
        #   ⇒ 改用自己的 `_line_frac`，支持**分数宽度**。
        # 旧基线的错：1.0px 走 `aaline`，白芯峰值到不了 255（中位色 (220,225,229)、
        #   纯白仅 30 个）⇒ 看着比基准细一档（用户："两报之间全部连在一起"的观感之一）。
        # 仍乘 geom_scale 以保持画布变大时等比。
        self.line_w = max(1.0, float(o.get("line_w", 3.45)) * self.geom_scale)
        #: 网格虚线宽度（**基线值**）。基准图纯网格色像素 = 6046（L1<=30）；我们原来走
        #: `paint_aa.aa_line(width=1)` -> `aaline`，峰值到不了纯色 ⇒ 只有 2785 个。
        #: 改用 `_line_frac`（可分数宽度 + 先填 (color,0) 再画 ⇒ 有实心芯且无黑边）后，
        #: 网格宽度有一个能让"纯网格色像素"对上基准的取值（见报告 R10-F1 扫描表）。
        # 基准实测：单条网格线横向宽度 **1.0 px**（中位，n=1526）、dash **9** / gap **5**。
        # 我们原来走  没有实心芯 ⇒ 纯网格色只有 2785（基准 6046）。
        # 扫描（纯网格色像素 / 与基准比）：1.00 -> 2785 (0.46x) ; **1.20 -> 5506 (0.91x)** ;
        #   1.40 -> 8392 (1.39x) ; 2.00 -> 14328 (2.37x) ⇒ 取 **1.2**（最接近且是同量级）。
        self.grid_w = max(1.0, float(o.get("grid_w", 1.2)) * self.geom_scale)
        self.coast = (self.basemap == "land_flat")
        self.map_surface = o.get("map_surface")
        #: 显式给 ctx（例如要和基准图逐像素同框对比时）—— 给了就不自动装箱
        self.ctx_override = o.get("ctx")
        self._ctx: Dict = {}
        self._k = 1.0
        self._land_src = None

    # ── 维度分派: palette / marker ────────────────────────────────────
    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            try:
                import logging
                logging.getLogger(__name__).warning(msg)
            except Exception:
                pass

    def _color_for(self, w, code) -> Tuple[int, int, int]:
        """风速(kt) + 状态码 → 颜色。按 `palette` 维度分派。

        · `qq`      —— 本模块的 9 档色表（**实测采样自 xrq-dolphin.png**，第一性）；
        · `2023`    —— 复用 `paint_render.scheme_color("2023", cat)`；
        · `classic` —— 复用 `paint_render.scheme_color("app", cat)`（应用默认色阶）。
        后两者需要"类别"而不是风速，所以借 `paint_render._eff_category` 用**同一套阈值**
        由风级推类别 —— 保证两套渲染器对同一个点得到同一个类别。
        任何失败都回落 `qq`（宁可颜色不匹配，也不能不画）。
        """
        w = float(w or 0)
        if self.palette == "qq":
            # 用户裁定的类型分派：固定色的码恒用最弱档、SS 夹到第 2 档。
            return color_for_code(w, code)
        try:
            from .paint_render import scheme_color, _eff_category
            cat = _eff_category({"w": int(w), "st": str(code or "")})
            sch = "2023" if self.palette == "2023" else "app"
            return tuple(scheme_color(sch, cat, int(w)))
        except Exception:
            return color_for_wind(w)

    def _shape_for(self, code: str) -> str:
        """状态码 → 形状。按 `marker` 维度分派。

        · `by_status` —— `shape_for_code` 的实测规则（DB/EX→三角, SD/SS→方块, 其余→圆）；
        · `uniform`   —— 全部画圆（基准图之外的一种"极简"选项）。
        """
        if self.marker == "uniform":
            return "circle"
        return shape_for_code(code)

    # ── 工具: 统一取点, 兼容 Ty 对象与 b-deck 行 ──
    @staticmethod
    def _storm_pts(st) -> List[dict]:
        pts = getattr(st, "pts", None)
        if pts is None and isinstance(st, dict):
            pts = st.get("pts") or st.get("points")
        return list(pts or [])

    @staticmethod
    def _lo_la(p) -> Tuple[float, float]:
        """取 (经度, 纬度)。**兼容三种输入**（2026-10-02 修）。

        修的是什么: 原先两处写死 `p["lon"]` / `p["lat"]`, 那只对**本模块探针用的 dict**
        有效; app 真实的 `TrackPoint`(app/typhoon.py:23) 用的是 `lo`/`la` 字段名
        (`paint_render.py:103` 就是 `p.get('lo')`)。于是**在 app 里选 QQ 样式会直接抛**
        `AttributeError: 'TrackPoint' object has no attribute 'lon'` ——
        也就是说 QQ 样式**此前只在离线探针里跑过, 从未接过 app 的真实数据**。

        支持:
          · app 的 `TrackPoint`: `.get('lo')` / `.get('la')`, 或属性 `.lo` / `.la`
          · 本模块探针/离线 dict: `{'lon': .., 'lat': ..}`
          · b-deck 行 dict: `{'lo': .., 'la': ..}`
        """
        if isinstance(p, dict):
            if "lon" in p or "lat" in p:
                return (float(p.get("lon", 0.0) or 0.0),
                        float(p.get("lat", 0.0) or 0.0))
            return (float(p.get("lo", 0.0) or 0.0),
                    float(p.get("la", 0.0) or 0.0))
        try:                                   # TrackPoint 有 __getitem__/get
            return (float(p.get("lo", 0.0) or 0.0),
                    float(p.get("la", 0.0) or 0.0))
        except Exception:
            return (float(getattr(p, "lo", 0.0) or 0.0),
                    float(getattr(p, "la", 0.0) or 0.0))

    def _all_bounds(self, storms) -> Tuple[float, float, float, float]:
        lo0, lo1, la0, la1 = 1e9, -1e9, 1e9, -1e9
        for st in storms:
            for p in self._storm_pts(st):
                lo, la = self._lo_la(p)
                lo0 = min(lo0, lo); lo1 = max(lo1, lo)
                la0 = min(la0, la); la1 = max(la1, la)
        if lo0 > lo1:
            return (100.0, 180.0, 0.0, 40.0)
        return (lo0, lo1, la0, la1)

    # ── Q1 修复：自建"不会黑边"的超采样精灵 ──────────────────────────
    @staticmethod
    def _sprite(kind: str, extent: float, color) -> pygame.Surface:
        """生成一个**边缘不会发黑**的抗锯齿形状精灵（居中，边长 extent）。

        ★ 修的是什么（用户："依然是黑描边与融合问题"）：
        `paint_aa._supersample` 在 `pygame.Surface(..., SRCALPHA)` 上画形状，
        而 SRCALPHA 面的**透明区 RGB 是 (0,0,0)**。`smoothscale` 降采样时把这块黑
        **平均进了边缘像素的 RGB** ⇒ 边缘像素拿到 `color × 覆盖率`，alpha 却是
        `255 × 覆盖率`；再按**非预乘**方式 blit，实际合成变成

            dst·(1−a) + color·a²

        于是边缘被**二次变暗**，看起来就是一圈黑描边。实测（探针 …/Temp/sc8/probe1.py）：
        等级色 `(0,250,244)` 落在海洋 `(0,34,68)` 上，覆盖率 0.5 的边缘像素
        **预测 (0,79,95)、实测 (0,79,95)** —— 逐位吻合。

        修法：**先用 (color, alpha=0) 填满缓存面**再画形状 ⇒ 透明区也带形状色，
        `smoothscale` 出来的 RGB 处处等于形状色，只有 alpha 在变 ⇒ 边缘正确。

        **抗锯齿一分不少**：仍是 4× 超采样 + smoothscale，只是不再引入黑边。
        ⚠ `paint_aa` 是共享底座（Lead 所有），本模块**不改它**；这是本模块自己的实现。
        同一个 bug 会影响所有用 `paint_aa` 画形状的地方（地图路径点、Ty4 圆点…），
        已单独报给 Lead。
        """
        e = max(2, int(round(extent)))
        key = (kind, e, tuple(int(v) for v in color))
        sp = _QQ_SPRITE.get(key)
        if sp is not None:
            return sp
        ss = QQ_AA_SS
        big = pygame.Surface((e * ss, e * ss), pygame.SRCALPHA)
        big.fill((int(color[0]), int(color[1]), int(color[2]), 0))   # ★ 关键一行
        c = e * ss / 2.0
        if kind == "square":
            half = e * ss / 2.0
            pygame.draw.rect(big, color, pygame.Rect(int(c - half), int(c - half),
                                                     int(2 * half), int(2 * half)))
        elif kind == "triangle":
            hw = e * ss / 2.0
            hh = e * ss * 0.842          # 高 16 / 宽 19 = 0.842
            pygame.draw.polygon(big, color, [
                (c, c - hh * 2.0 / 3.0), (c - hw, c + hh / 3.0), (c + hw, c + hh / 3.0)])
        else:
            pygame.draw.circle(big, color, (int(c), int(c)), int(e * ss / 2.0))
        sp = pygame.transform.smoothscale(big, (e, e))
        if len(_QQ_SPRITE) > 4096:
            _QQ_SPRITE.clear()
        _QQ_SPRITE[key] = sp
        return sp

    @staticmethod
    def _ring_sprite(kind: str, extent: float, ratio: float, color) -> pygame.Surface:
        """**环形**精灵（真挖空：洞内 alpha=0，blit 后透出当时的底图）。

        做法（关键是不能重新引入黑边）：
          1. `big` 先填 `(color, alpha=0)` ⇒ 任何位置的 RGB 都是形状色（见 `_sprite` 的推导）；
          2. 在 `big` 上画**外形状**（实心，alpha=255）；
          3. 另建 `mask`：整体 `(255,255,255,255)`，再把**核心形状**画成 `(255,255,255,0)`；
          4. `big.blit(mask, (0,0), special_flags=BLEND_RGBA_MULT)`
             ⇒ 洞内 `alpha = 255*0/255 = 0`，而 `RGB = color*255/255 = color` **保持不变**
             ⇒ smoothscale 后 RGB 仍处处是形状色、只有 alpha 在变 ⇒ **环的内外边缘都不发黑**。

        ⚠ 为什么不用"画完再拿黑色盖"或"填 (0,0,0)"：那会让洞内 RGB 变成黑，
        smoothscale 一平均就在**环内侧**再造一圈黑边（同一个非预乘坑）。
        ⚠ 也不用 `BLEND_RGBA_MIN`：它会把 RGB 也取 min ⇒ 同样变黑。
        """
        e = max(3, int(round(extent)))
        key = ("ring", kind, e, round(float(ratio), 3), tuple(int(v) for v in color))
        sp = _QQ_SPRITE.get(key)
        if sp is not None:
            return sp
        ss = QQ_AA_SS
        sz = (e * ss, e * ss)
        big = pygame.Surface(sz, pygame.SRCALPHA)
        big.fill((int(color[0]), int(color[1]), int(color[2]), 0))
        mask = pygame.Surface(sz, pygame.SRCALPHA)
        mask.fill((255, 255, 255, 255))
        c = e * ss / 2.0
        ce = max(2.0, e * ss * float(ratio))          # 核心边长(与外形状同尺度)
        def _paint(target, box, col):
            if kind == "square":
                half = box / 2.0
                pygame.draw.rect(target, col, pygame.Rect(int(c - half), int(c - half),
                                                          max(1, int(2 * half)), max(1, int(2 * half))))
            elif kind == "triangle":
                hw = box / 2.0
                hh = box * 0.842
                pygame.draw.polygon(target, col, [
                    (c, c - hh * 2.0 / 3.0), (c - hw, c + hh / 3.0), (c + hw, c + hh / 3.0)])
            else:
                pygame.draw.circle(target, col, (int(c), int(c)), int(box / 2.0))
        _paint(big, e * ss, color)
        _paint(mask, ce, (255, 255, 255, 0))          # 洞: alpha=0, RGB 保持白
        big.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        sp = pygame.transform.smoothscale(big, (e, e))
        if len(_QQ_SPRITE) > 4096:
            _QQ_SPRITE.clear()
        _QQ_SPRITE[key] = sp
        return sp

    def _blit_shape(self, surf, kind, color, center, extent):
        sp = self._sprite(kind, extent, color)
        surf.blit(sp, (int(round(center[0] - sp.get_width() / 2.0)),
                       int(round(center[1] - sp.get_height() / 2.0))))

    def _shapes(self, surf, kind, color, center, hollow: bool = False):
        """一个标记 —— 全部走**本模块自建的抗锯齿精灵**（见 `_sprite`，无黑边）。

        尺寸来自 `QQ_SHAPE_SIZE`（基准图实测），再按 `marker_r / (圆半径基准)` 等比缩放；
        `marker_r` 默认 9.5 = ⌀19.0/2。

        `hollow=True` 时画**黑心**（外圈等级色、中心黑）：用于**非正式报**
        （`TrackPoint.official is False`）—— 用户点名的 QQ 非正式报样式。
        几何比 `QQ_CORE_RATIO`；⚠ 该比例**未由基准图验证**（本图无黑心，见常量注释）。
        """
        cx, cy = center
        scale = max(0.05, float(self.marker_r) / (QQ_SHAPE_SIZE["circle"][0] / 2.0))
        if kind == "square":
            extent = QQ_SHAPE_SIZE["square"][0] * scale
        elif kind == "triangle":
            extent = QQ_SHAPE_SIZE["triangle"][0] * scale
        else:
            extent = QQ_SHAPE_SIZE["circle"][0] * scale
        if not self.aa:
            # 对照路径：关掉抗锯齿（会锯齿，仅用于 A/B 证明"黑边不是 AA 本身造成的"）
            r = max(2, int(round(extent / 2.0)))
            if kind == "square":
                pygame.draw.rect(surf, color, pygame.Rect(int(cx) - r, int(cy) - r, 2 * r, 2 * r))
            elif kind == "triangle":
                hh = extent * 0.842
                pygame.draw.polygon(surf, color, [
                    (cx, cy - hh * 2.0 / 3.0), (cx - r, cy + hh / 3.0), (cx + r, cy + hh / 3.0)])
            else:
                pygame.draw.circle(surf, color, (int(cx), int(cy)), r)
        else:
            if hollow:
                # U8(2026-10-02): **真挖空** —— 洞内 alpha=0, blit 后透出**当时的底图**
                # (海洋底上是 (0,34,68)、陆地底上是绿)。原先"填纯黑"只是近似:
                # 深色海洋底上看不出来, 但**陆地底上会露馅**(基准图那里洞内是背景色)。
                sp = self._ring_sprite(kind, extent, QQ_CORE_RATIO, color)
                surf.blit(sp, (int(round(cx - sp.get_width() / 2.0)),
                               int(round(cy - sp.get_height() / 2.0))))
            else:
                self._blit_shape(surf, kind, color, (cx, cy), extent)

    def _dashed(self, surf, color, pts, dash=9.0, gap=6.0, width=1):
        """沿折线按弧长切虚线后逐段画（用 `paint_aa.aa_line`，width=1 走 aaline）。"""
        on = True
        remain = dash
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg <= 1e-9:
                continue
            ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
            t = 0.0
            while t < seg:
                step = min(remain, seg - t)
                if on:
                    a = (x0 + ux * t, y0 + uy * t)
                    b = (x0 + ux * (t + step), y0 + uy * (t + step))
                    _line_frac(surf, color, a, b, width)
                t += step
                remain -= step
                if remain <= 1e-9:
                    on = not on
                    remain = dash if on else gap

    def render(self, tys, mode: str = "normal", region=None, map_surface=None):
        """返回 `(Surface, stats)`；`stats` 与 `paint_render` 同形（尽量）。"""
        storms = list(tys or [])
        lo0, lo1, la0, la1 = self._all_bounds(storms)
        pad_deg = 4.0
        bounds = (lo0 - pad_deg, lo1 + pad_deg, max(-80.0, la0 - pad_deg),
                  min(84.0, la1 + pad_deg))
        # 画布尺寸：照 Ty4 的 W = max(最小宽度, 内容 + 2*留空)
        dlon = max(1e-6, bounds[1] - bounds[0])
        dlat = max(1e-6, bounds[3] - bounds[2])
        _frame_ctx = None
        if self.output_size:
            W, H = int(self.output_size[0]), int(self.output_size[1])
        elif self.frame_reference:
            # ① 基准取景 —— **R8b**：画布**固定** 1837×1224（不随台风变），
            #    **缩放**使轨迹横向跨度占画布宽 `track_w_frac`（= 按 bbox 比例留白），
            #    并把轨迹投影 bbox 中心放到基准实测的相对位置。
            #    为什么不能用"画布反投影外包宽度"定尺度（旧做法，错）：
            #      圆锥下矩形画布的**经纬度外包远宽于实际可见窗口**（经线向北收敛 ⇒
            #      顶边覆盖经度窄、底边宽，取并集虚大）⇒ 尺度算小 2.07 倍 ⇒
            #      轨迹只占画布宽 40%（基准 82.8%）⇒ 点距被压掉一半 ⇒ 糊成一条带子。
            #    为什么不能固定角度留白（用户纠正）：固定角度对大/小台风一视同仁，
            #      小台风会被留白挤到画布中间一小块 ⇒ **必须按 bbox 比例**。
            #    对 26 Dolphin：scale=1 时轨迹投影跨度 1520.5 px
            #      k = 0.828×1837/1520.5 = 1.0003 ≈ 1 ⇒ 与基准同尺度 ✔
            # 画布尺寸 = **参数**（默认 2560x1440）；长宽比自由，不强制 16:9。
            W = max(160, min(self.max_w, int(self.canvas_w)))
            H = max(120, min(self.max_h, int(self.canvas_h)))
            # **地图中央经度**（可选）：把缝挪到数据空档里，避免劈开轨迹。
            # 缺省（`self.lon_c is None`）时 `_ref is _REF_CTX` ⇒ 逐位兼容。
            _ref = _REF_CTX
            if self.lon_c is not None:
                _ref = dict(_REF_CTX)
                _ref["lon_c"] = float(self.lon_c)
            _sp = [PP.project(self.projection, r["lo"], r["la"], _ref)
                   for st in storms for r in self._storm_pts(st)]
            if _sp:
                _sx = max(p[0] for p in _sp) - min(p[0] for p in _sp)
                _sy = max(p[1] for p in _sp) - min(p[1] for p in _sp)
                _cx0 = (max(p[0] for p in _sp) + min(p[0] for p in _sp)) / 2.0
                _cy0 = (max(p[1] for p in _sp) + min(p[1] for p in _sp)) / 2.0
                # **双方向 contain**（R12）：两个方向都要求"轨迹占该方向画布 = track_*_frac"，
                # 取更紧的那一项。横向这个表达式**保持与改前逐字相同**（不是等价改写），
                # 以保证基准档（26 Dolphin 是宽约束）取到的仍是同一个浮点值 ⇒ 像素逐位不变。
                _k = min((self.track_w_frac * W) / max(1e-6, _sx),
                         (self.track_h_frac * H) / max(1e-6, _sy))
            else:
                _cx0 = _cy0 = 0.0
                _k = 1.0
            _frame_ctx = dict(_ref)
            _frame_ctx["mode"] = self.projection
            _frame_ctx["size"] = (W, H)
            _frame_ctx["scale"] = _ref.get("scale", 1.0) * _k
            # ⚠ `PP.project` = `ox + scale*X` ⇒ `_sp` 是**屏幕**坐标（已含 ox/oy）。
            #   要"把轨迹中心放到 W*cx_frac"，必须先**减掉原 ox/oy** 回到 raw 坐标再乘 k，
            #   否则会整体平移约 918 px（实测踩过：span 检查是平移不变的、照样过，
            #   但渲染出来整条轨迹被推出画布 —— **只看占比会漏掉这种错**）。
            _frame_ctx["ox"] = W * self.track_cx_frac - _k * (_cx0 - _ref.get("ox", 0.0))
            _frame_ctx["oy"] = H * self.track_cy_frac - _k * (_cy0 - _ref.get("oy", 0.0))
            self._fit_bounds = bounds
        else:
            dlon = max(1e-6, bounds[1] - bounds[0])
            dlat = max(1e-6, bounds[3] - bounds[2])
            #: 内容尺寸(未设下限)
            _wc = dlon * self.px_per_deg + 2 * self.margin_w
            _hc = dlat * self.px_per_deg * 1.15 + 2 * self.margin_h
            Wc = int(min(self.max_w, max(160, _wc)))
            Hc = int(min(self.max_h, max(120, _hc)))
            #: 套下限(照 Ty4 的 Width/Height)。取整到 2 的倍数无害, 便于等比缩放。
            W = int(min(self.max_w, max(self.min_w, Wc)))
            H = int(min(self.max_h, max(self.min_h, Hc)))
        _fit = getattr(self, "_fit_bounds", None) or bounds
        if self.ctx_override:
            ctx = dict(self.ctx_override)
            # 显式 ctx 可能只给了投影参数(如 PP.CONIC_REFERENCE) ⇒ 补齐渲染需要的字段
            ctx.setdefault('mode', self.projection)
            ctx.setdefault('size', (W, H))
            ctx.setdefault('bounds', bounds)
            W, H = int(ctx['size'][0]), int(ctx['size'][1])
        elif _frame_ctx is not None:
            # 基准取景：直接用**锚点拟合尺度**的 ctx（固定投影参数 + 按轨迹跨度定的画布）
            ctx = _frame_ctx
            W, H = int(ctx["size"][0]), int(ctx["size"][1])
        elif (not self.output_size) and (not self.frame_reference) and (W, H) != (Wc, Hc):
            # 命中下限 ⇒ **保持内容比例不变, 只把内容居中到更大的画布**
            # (= 往四周多露地图, 而不是把轨迹放大; 这正是 Ty4 px/° 恒定的效果)
            # 做法: 先用内容尺寸装箱拿到 ctx, 再把偏移各加半个尺寸差。
            ctx = PP.make_ctx(self.projection, _fit, (Wc, Hc), pad=6.0)
            ctx = dict(ctx)
            ctx['size'] = (W, H)
            ctx['ox'] = float(ctx.get('ox', 0.0)) + (W - Wc) / 2.0
            ctx['oy'] = float(ctx.get('oy', 0.0)) + (H - Hc) / 2.0
        else:
            ctx = PP.make_ctx(self.projection, _fit, (W, H), pad=6.0)
        self._ctx = ctx
        self._k = ctx["scale"]

        # ── R16：ctx 与画布都定了 ⇒ 按**地图比例 k** 重算墨迹尺寸 ──
        # （放在 __init__ 里会"猜 k"；k 只有 ctx/画布确定后才算得出来。）
        _k_map = _px_per_deg(ctx, W, H, _fit)
        self.px_per_deg_k = _k_map
        _g = self.geom_scale        # 画布口径系数 = canvas_w / 1837
        # 「画布口径」来自基准图实测（⌀20.74 @ 1837）：保证近景/单台风复刻基准，
        #   也保证**窄窗**下不被角度口径撑爆。
        # 「角度口径」= 每度墨迹 × k：物理含义（标记恒占 ≈1.38°），保证**全球图/多台风**
        #   不被画布口径撑爆。
        # **两者取小 = 哪个约束更紧用哪个**：
        #   · 基准档 1837 两口径相等 ⇒ 走画布口径，且值恰为实测 20.74/3.45/1.2（构造上相等）；
        #   · alltypes 窄窗 k≈70：画布口径 ⌀28.9 < 角度口径 ⌀96.6 ⇒ 取 28.9（修 R16 撑爆）；
        #   · 全球图 k≈23.9：画布口径 ⌀86.7 > 角度口径 ⌀33.0 ⇒ 取 33.0（保持能分辨）。
        if not self._explicit_sizes.get("marker_r"):
            self.marker_r = min(QQ_MARKER_PX * _g, (QQ_MARKER_DEG / 2.0) * _k_map)
        if not self._explicit_sizes.get("line_w"):
            self.line_w = max(1.0, min(QQ_LINE_PX * _g, QQ_LINE_DEG * _k_map))
        if not self._explicit_sizes.get("grid_w"):
            self.grid_w = max(0.35, min(QQ_GRID_PX * _g, QQ_GRID_DEG * _k_map))

        surf = pygame.Surface((W, H), pygame.SRCALPHA)
        # ① 底图：平涂海洋 + 抗锯齿海岸线陆地
        self._draw_basemap(surf, ctx, W, H)
        # ② 虚线经纬网 + 标注
        if self.grid:
            self._draw_grid(surf, ctx, W, H)
        # ③ 轨迹
        self._draw_tracks(surf, storms, ctx, W, H)
        # ③.5 图例（默认 none）
        if self.legend != "none":
            self._draw_legend(surf, W, H)
        # ④ 水印
        if self.watermark:
            self._draw_watermark(surf, W, H)
        return surf, {"bounds": bounds, "size": (W, H), "projection": self.projection,
                      "ctx": dict(ctx),
                      #: 底图来源：（R13 加固）"map/land.png" = 正常；"fallback_npz_0.5deg" = 降级。
                      #: 原实现在找不到 land.png 时**静默**回落 0.5 度粗掩码（无异常无日志），
                      #: 复制/挪动本模块就会无声降级 ⇒ 调用方现在可以直接从 stats 看出来。
                      "land_source": (("map/land.png" if _LAND_SRC["path"] or _find_land_png()[0]
                                       else "fallback_npz_0.5deg")
                                      if _open_land_png() is not None else "fallback_npz_0.5deg"),
                      "land_path": _LAND_SRC["path"]}

    # ── ① 底图 ──
    def _draw_basemap(self, surf, ctx, W, H):
        """按 `basemap` 维度选底图：`map_png`(卫星) / `land_flat`(平涂) / `none`(透明)。"""
        if self.basemap == "none":
            return
        if self.basemap == "map_png":
            if self._draw_map_png(surf, ctx, W, H):
                return
            # 不支持 / 取不到图 -> **明确回落** land_flat（而不是留一张空底或画错位的图）
            self._warn_once("qq.basemap.map_png",
                            "QQ 样式: basemap=map_png 在 projection=%s 下不支持(见 "
                            "_draw_map_png 注释), 已回落 land_flat" % self.projection)
        self._draw_flat_basemap(surf, ctx, W, H)

    def _draw_map_png(self, surf, ctx, W, H) -> bool:
        """卫星底图。**只在 equirect 下支持**，成功返回 True。

        为什么 conic 不支持：底图(`map/map.png`)是 0..360 x -90..90 的**等距圆柱**位图，
        "1:1 裁剪再 blit"只在**仿射**映射下几何正确。圆锥投影是非仿射的（经线收敛、
        纬线是弧），要正确显示必须**逐像素重采样**整张源图；那既慢又吃内存，而且
        `land_flat` 路径已经把"按投影采样"做对了(它采的是陆地掩码而不是卫星图)。
        所以这里**明确报不支持并回落**，而不是给一张几何错位的图 —— 错位的底图比没有更难发现。
        """
        if self.projection != "equirect":
            return False
        img = self.map_surface
        if img is None:
            try:
                from .paint_render import load_map_image
                img = load_map_image(self.map_path)
            except Exception:
                img = None
        if img is None:
            return False
        try:
            iw, ih = img.get_size()
            ppd = iw / 360.0                       # 等距圆柱: x = lon*ppd, y = (90-lat)*ppd
            lon0, lon1, lat0, lat1 = PP.canvas_lonlat_envelope(ctx, (W, H), pad_px=1.0)
            sx0 = int(max(0, lon0 * ppd)); sx1 = int(min(iw, lon1 * ppd))
            sy0 = int(max(0, (90.0 - lat1) * ppd)); sy1 = int(min(ih, (90.0 - lat0) * ppd))
            if sx1 - sx0 < 2 or sy1 - sy0 < 2:
                return False
            crop = img.subsurface(pygame.Rect(sx0, sy0, sx1 - sx0, sy1 - sy0)).copy()
            surf.blit(pygame.transform.smoothscale(crop, (W, H)), (0, 0))
            self._land_src = "map_png-equirect"
            return True
        except Exception:
            return False

    def _draw_flat_basemap(self, surf, ctx, W, H):
        """平涂海洋 + 陆地。

        海岸线做法：把  **按画布覆盖的经纬度窗口裁剪**下来（源图
        30 px/°，与输出 ~26 px/° 同量级），再用**双线性插值**按投影逐像素采样。
        二值掩码经双线性后自然得到 0..1 的边缘覆盖率 ⇒ 海岸线平滑、无块状。
        """
        win = None
        if self.coast:
            try:
                cw = _canvas_window(ctx, W, H)
                win = _land_window(*cw) if cw else None
            except Exception:
                win = None
        fb = _fallback_frac()
        if win is None and fb["frac"] is None:
            surf.fill(QQ_COLORS["ocean"])
            return
        ss = max(1, int(COAST_SS if self.aa else 1))
        ww = W * ss
        oc = np.array(QQ_COLORS["ocean"], np.float32)
        lc = np.array(QQ_COLORS["land"], np.float32)
        rgb = np.empty((H, W, 3), np.uint8)
        # ⚠ 分块：整幅反投影在 2x 超采样下是 ~9M 个 float64 中间量(数百 MB)，
        #   本机可用内存 0.7–2.6 GB 且有 OOM 前科 ⇒ 按行块算，峰值降到 ~1/BLK。
        BLK = max(ss, 64 // ss)
        for y0 in range(0, H, BLK):
            y1 = min(H, y0 + BLK)
            sh = (y1 - y0) * ss
            yy, xx = np.mgrid[0:sh, 0:ww]
            xs = (xx.astype(np.float64) + 0.5) / ss
            ys = (yy.astype(np.float64) + 0.5) / ss + y0
            if win is not None:
                land = _sample_land_window(win, ctx, xs, ys)
            else:
                lon, lat = PP.inverse_arrays(ctx["mode"], xs, ys, ctx)
                ny, nx = fb["frac"].shape
                i = np.clip((lon % 360.0) / fb["dlon"], 0, nx - 1).astype(np.int32)
                j = np.clip((lat + 90.0) / fb["dlat"], 0, ny - 1).astype(np.int32)
                land = fb["frac"][j, i]          # npz: row0 = 90S
            del xs, ys, yy, xx
            if ss > 1:
                land = land.reshape(y1 - y0, ss, W, ss).mean(axis=(1, 3))
            rgb[y0:y1] = (oc[None, None, :] * (1.0 - land[:, :, None])
                          + lc[None, None, :] * land[:, :, None]).astype(np.uint8)
            del land
        try:
            base = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        except Exception:
            surf.fill(QQ_COLORS["ocean"])
            return
        surf.blit(base, (0, 0))
        del rgb, base
        self._land_src = ("window" if win is not None else "npz-fallback")


    # ── ② 虚线经纬网 ──
    def _draw_grid(self, surf, ctx, W, H):
        """铺满整个画布的虚线经纬网 + 边缘经纬度标注。

        ⚠⚠ 采样范围**必须**用画布反投影外包（`PP.canvas_lonlat_envelope`），
        不能用 `ctx["bounds"]` —— 那是**轨迹**的经纬度框，与画布尺寸是两件独立的事。
        本渲染器照 Ty4 的 `W = max(最大宽度, 内容 + 2*留空)` 给画布套了下限
        （默认 1920x1080），于是**小台风的画布远大于轨迹框**；此时若仍用轨迹框当范围，
        网格只会出现在画布中间**一小片**（用户原话："qq样式只有一小片区域有经纬度"）。

        实测（`…\\Temp\\sc6\\diag_grid.py`）网格外接框占画布：
          8°x5° 轨迹 + 保底画布 = **3.7%** ；20°x12° = **12.4%** ；
          26 Dolphin 全轨迹 = 90.4% ；基准图 `xrq-dolphin.png` = **100.0%**。
        所以这个 bug 是**随轨迹大小变化的**：轨迹越大越不明显，小台风一眼就看出问题。

        第二层原因：`sample_graticule` 默认按 `ctx['bounds']` 决定**每条线的跨度**
        （经线只画 `lat0..lat1`、纬线只画 `lon0..lon1`），所以即便线选对了、也会被
        **截断在轨迹框内**。现在用 `extent=env` 把跨度也换成画布外包。
        """
        env = None
        try:
            env = PP.canvas_lonlat_envelope(ctx, (W, H), pad_px=2.0)
        except Exception:
            env = None
        if env is None:
            env = ctx.get("bounds")          # 兜底: 跨 0° 经线时退回轨迹框
        if env is None:
            return
        lon0, lon1, lat0, lat1 = env
        # 自适应步长: 外包可能很大(小轨迹+保底画布), 用它把每条线的点数限制在 ~900
        span = max(lon1 - lon0, lat1 - lat0)
        step = 0.5 if span <= 450.0 else span / 900.0
        lons = [v for v in range(-360, 721, self.grid_step)
                if lon0 - 1e-9 <= v <= lon1 + 1e-9]
        lats = [v for v in range(-90, 91, self.grid_step)
                if lat0 - 1e-9 <= v <= lat1 + 1e-9]
        pts = PP.sample_graticule(ctx, lons=lons, lats=lats, step=step,
                                  clip=(-40.0, -40.0, W + 40.0, H + 40.0), extent=env)
        for kind, val, poly in pts:
            # dash/gap 用基准实测量：dash 9 px / gap 5 px（原来 gap=6.0 偏大）
            self._dashed(surf, QQ_COLORS["grid"], poly, dash=9.0, gap=5.0,
                         width=self.grid_w)
        if self.labels == "none":
            return
        # labels="inside"：把标注放在**画布内缩 22px** 的位置（不贴边、不被裁），
        # 而不是画布边缘。基准图用的是 edge（贴边），所以 QQ 预设给 edge。
        _inset = 22.0 if self.labels == "inside" else 0.0
        if self._label_font is None:
            try:
                # FONT_FILE 是**裸文件名**(app/constants/fonts.py:14), 不是路径 ——
                # 真正的目录是 _FONT_DIR(= assets/font, fonts.py:12)。
                # 直接 pygame.font.Font(FONT_FILE, ...) 会失败并静默回落小字号。
                import os as _os
                from .constants.fonts import FONT_FILE, _FONT_DIR
                self._label_font = pygame.font.Font(
                    _os.path.join(_FONT_DIR, FONT_FILE), self.label_px)
            except Exception:
                self._label_font = None

        def _lbl(txt):
            if self._label_font is not None:
                return self._label_font.render(txt, True, QQ_COLORS["label"])
            from .constants.fonts import f_l, rt      # 兜底: 用最大的现成字号
            return rt(f_l, txt, QQ_COLORS["label"])
        # 纬度标注贴左边缘、经度标注贴下边缘（与基准图一致）。
        # ⚠ 必须用**画布边缘的精确交点**（线性插值），不能用"最靠左/最靠下的采样点"：
        #   外包比画布大时，采样点会落在画布外面，按旧写法标注会被推到画布外或挤成一堆。
        for kind, val, poly in pts:
            if kind == "lat":
                p = self._cross_edge(poly, 0, _inset)       # 与左边缘 x=inset 的交点
                if p is None:
                    continue
                x, y = p
                if not (-1.0 <= y <= H + 1.0):
                    continue
                s = "%d°%s" % (abs(int(val)), "N" if val >= 0 else "S")
                img = _lbl(s)
                # 贴边量实测：基准纬度标注的墨迹 x0 = 12~16 px，我们原来 (4.0) 只有 5~6
                # ⇒ 左移约 7 px（叠加图里标注重影的主因）。取 11.0 让墨迹落进 12~16。
                surf.blit(img, (11.0 + _inset, int(y - img.get_height() / 2)))
            else:
                p = self._cross_edge(poly, 1, float(H) - _inset)   # 与下边缘的交点
                if p is None:
                    continue
                x, y = p
                if not (-1.0 <= x <= W + 1.0):
                    continue
                lon = int(val)
                if lon == 180 or lon == -180:
                    s = "180°"
                elif 0 <= lon < 180:
                    s = "%d°E" % lon
                else:
                    s = "%d°W" % (360 - lon if lon > 180 else -lon)
                img = _lbl(s)
                surf.blit(img, (int(x - img.get_width() / 2),
                                min(H - img.get_height() - 3,
                                    int(y - img.get_height() - 2))))

    @staticmethod
    def _cross_edge(poly, axis: int, value: float):
        """折线与 `poly[axis] == value` 的**第一次**交点（线性插值）。

        `axis=0` 求与竖直线 `x=value` 的交点，`axis=1` 求与水平线 `y=value` 的交点。
        找不到（该线没伸到那条边）时退回"该轴上最靠近 value 的那一端"，
        这样标注至少落在画布内，而不是消失。
        """
        if not poly:
            return None
        prev = poly[0]
        for cur in poly[1:]:
            a, b = prev[axis], cur[axis]
            if (a - value) * (b - value) <= 0.0 and abs(b - a) > 1e-12:
                t = (value - a) / (b - a)
                return (prev[0] + (cur[0] - prev[0]) * t,
                        prev[1] + (cur[1] - prev[1]) * t)
            prev = cur
        best = min(poly, key=lambda p: abs(p[axis] - value))
        return best

    # ── ③ 轨迹 ──
    def _draw_tracks(self, surf, storms, ctx, W, H):
        for st in storms:
            pts = self._storm_pts(st)
            if not pts:
                continue
            # ⚠ **经度解包**（R18c，2026-10-05）：等距圆柱把经度折叠到 `lon_c ± 180`，
            #   逐台风连线时相邻两点若跨过那条边界，折叠值会从 −179 跳到 +179 ⇒
            #   **一个 2° 的正常步长被画成横贯全图的直线**（实测 2026 大西洋 Dolly：
            #   333.1°E → 331.0°E，2133px）。修法：把**上一个报点的经度**作为
            #   `lon_ref` 传给投影，让该点的折叠值取"离上一点最近"的等价经度。
            #   圆锥投影不做（`sig = n·dlon`，±360° 不是对称操作）。
            if self.projection == "equirect":
                xy = []
                d_prev = None
                for p in pts:
                    lo, la = self._lo_la(p)
                    # ⚠ ref 必须是**上一个点解包后的输出 dlon**，不是上一个点的原始经度
                    #   （原始经度折叠后可能落在另一圈 ⇒ 串联时再次跳变，2026-10-05 踩过）。
                    #   折叠口径统一用 `PP.equirect_dlon`，单一真相源。
                    d_cur = PP.equirect_dlon(lo, ctx, d_prev)
                    xy.append(PP.project(self.projection, lo, la, dict(ctx, dlon_ref=d_cur)))
                    d_prev = d_cur
            else:
                xy = [PP.project(self.projection, *self._lo_la(p), ctx)
                      for p in pts]
            # 连线。`gradient_line=False`（QQ 预设默认）：细白线，逐位等于既有行为。
            # `gradient_line=True`：按两端强度的**等级色**插值着色，并细分成
            # `gradient_subdiv` 段（与 `paint_render._draw_gradient_segment` 同一口径：
            # 在 RGB 空间线性插值，所以中间色不在调色板里 —— 那就是"渐变"的观感）。
            for i in range(len(xy) - 1):
                if not self.gradient_line:
                    _line_frac(surf, QQ_COLORS["line"], xy[i], xy[i + 1], self.line_w)
                else:
                    self._gradient_segment(surf, pts[i], pts[i + 1], xy[i], xy[i + 1])
            # 标记（分阶段形状 + 强度色）
            for p, (x, y) in zip(pts, xy):
                # ⚠ `st` 必须排在第一位：app 的 TrackPoint(app/typhoon.py:23) 用的是 `st`
                #   (DB/WV/LO/EX/SD/SS/TD/TS...)，**没有** `code`/`type` 字段。
                #   原先只读 code/type ⇒ 恒取兜底 "TY" ⇒ shape_for_code 恒返回 "circle"
                #   ⇒ **所有点都画成圆**（用户点名："你为什么把扰动画成圆形"）。
                #   规则本身没错(EX/SS/SD→方块, WV/DB/LO/MD→三角, 其余→圆)，
                #   错的是**取不到类型码**。这与 `.lon` 那次是同一类"字段名对不上"的 bug。
                code = str(p.get("st") or p.get("code") or p.get("type") or "TY")
                w = float(p.get("w", p.get("wind", 0)) or 0)
                # 非正式报 -> 空心环。判定**不按 st 猜**, 用**真在跑的那个判据**:
                #   `paint_render._is_official(p)` = UTC 时次 ∈ {00,06,12,18}
                #   （`TrackPoint.official` 字段全仓无任何地方赋值, 恒 True ⇒ 拿它判会永远不触发）
                # U7: 两种判据的差异点数已实测 = 1/99（唯一分歧是 i=66 / 09Z）,
                #     换判据时不会静默变化。
                self._shapes(surf, self._shape_for(code), self._color_for(w, code),
                             (x, y), hollow=(self.black_core and not _is_official_point(p)))

    def _gradient_segment(self, surf, p1, p2, a, b):
        """把一段轨迹画成**强度渐变**（两端等级色线性插值 + 细分）。

        段数 = `gradient_subdiv`（默认 8，与 `paint_render` 同默认、同口径）。
        """
        c0 = self._color_for(p1.get("w", p1.get("wind", 0)), p1.get("st") or p1.get("code"))
        c1 = self._color_for(p2.get("w", p2.get("wind", 0)), p2.get("st") or p2.get("code"))
        n = self.gradient_subdiv
        dx, dy = b[0] - a[0], b[1] - a[1]
        for k in range(n):
            t0 = k / float(n)
            t1 = (k + 1) / float(n)
            tm = (t0 + t1) * 0.5
            c = tuple(int(round(c0[j] + (c1[j] - c0[j]) * tm)) for j in range(3))
            _line_frac(surf, c, (a[0] + dx * t0, a[1] + dy * t0),
                       (a[0] + dx * t1, a[1] + dy * t1), self.line_w)

    # ── 图例（维度 `legend`，默认 none）────────────────────────────
    def _draw_legend(self, surf, W, H):
        """按 `legend` 维度画一个紧凑色阶图例。

        `none` = 不画（**默认**，也是 QQ 预设；基准图 `xrq-dolphin.png` 里没有图例）。
        其余值（tl/tr/bl/br/auto）画在对应角：`auto` 取"离轨迹重心最远"的角，
        避免挡住轨迹。色阶用当前 `palette` 维度的 9 档代表色 + 风速上界。
        """
        if self.legend == "none":
            return
        try:
            from .constants.fonts import f_s, rt
        except Exception:
            return
        entries = []
        lo = 0.0
        for i, hi in enumerate(QQ_BINS):
            entries.append((lo, hi, self._color_for(lo + 1.0, "TY")))
            lo = hi
        entries.append((lo, 999.0, self._color_for(lo + 1.0, "TY")))
        rows = []
        for lo_, hi_, c in entries:
            lab = ("<%d" % hi_) if lo_ <= 0 else ("%d-%d" % (lo_, hi_))
            rows.append((lab, tuple(int(v) for v in c)))
        timg = [rt(f_s, lab, QQ_COLORS["label"]) for lab, _ in rows]
        sw = 16
        bw = sw + 6 + max(t.get_width() for t in timg) + 12
        bh = sum(max(sw, t.get_height()) + 2 for t in timg) + 10
        pos = self.legend
        if pos == "auto":
            pos = "bl"
        x0 = 10 if pos in ("tl", "bl") else max(0, W - bw - 10)
        y0 = 10 if pos in ("tl", "tr") else max(0, H - bh - 10)
        box = pygame.Surface((bw, bh), pygame.SRCALPHA)
        box.fill((0, 34, 68, 190))
        pygame.draw.rect(box, QQ_COLORS["grid"], box.get_rect(), 1)
        y = 5
        for (lab, c), t in zip(rows, timg):
            h = max(sw, t.get_height())
            pygame.draw.rect(box, c, pygame.Rect(6, y + (h - sw) // 2, sw, sw))
            box.blit(t, (6 + sw + 4, y + (h - t.get_height()) // 2))
            y += h + 2
        surf.blit(box, (x0, y0))

    # ── ④ 水印 ──
    def _draw_watermark(self, surf, W, H):
        """ 斜向重复水印。

        **用白色 + alpha 62**：这不是猜的 —— 基准图里水印叠在海洋上得到
        、叠在陆地上得到 ，两者都由
         精确复现：
          海 0.243*255+0.757*0  = 62 ✓  0.243*255+0.757*34  = 88 ✓  0.243*255+0.757*68  = 114 ✓
          陆 0.243*255+0.757*48 = 98.3≈99 ✓  0.243*255+0.757*112 = 146.8≈147 ✓  …+0.757*80 = 122.6≈123 ✓
        """
        try:
            from .constants.fonts import f_m, rt
        except Exception:
            return
        stamp = rt(f_m, "@xrq", QQ_COLORS["line"])
        if stamp.get_width() < 4:
            return
        # 缩到约 0.16 倍画布宽（基准图里每个 "@xrq" 约 300 px / 1837 px）
        k = max(1.0, (W * 0.16) / stamp.get_width())
        stamp = pygame.transform.smoothscale(
            stamp, (max(2, int(stamp.get_width() * k)),
                    max(2, int(stamp.get_height() * k))))
        big = pygame.Surface((stamp.get_width() + 12, stamp.get_height() + 24),
                             pygame.SRCALPHA)
        big.blit(stamp, (6, 12))
        big = pygame.transform.rotate(big, 25)
        big.set_alpha(62)                     # ← 实测反解出的 alpha
        stepx = max(240, int(W * 0.33))
        stepy = max(200, int(H * 0.46))
        for gy in range(-1, H // stepy + 2):
            for gx in range(-1, W // stepx + 2):
                surf.blit(big, (gx * stepx + (gy % 2) * stepx // 2,
                                gy * stepy - big.get_height() // 3))


def resolve_bounds_like_paint_render(tys, margin_w=0.0, margin_h=0.0):
    """薄包装：尽量复用 `paint_render.resolve_bounds`（只读），失败则自带兜底。"""
    try:
        from .paint_render import resolve_bounds
        return resolve_bounds(tys, "normal", None, margin_w, margin_h,
                              include_invest=False)
    except Exception:
        r = QQTrackRenderer()
        b = r._all_bounds(tys)
        return (b[0] - 4, b[1] + 4, max(-80.0, b[2] - 4), min(84.0, b[3] + 4))
