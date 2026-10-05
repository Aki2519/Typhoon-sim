# -*- coding: utf-8 -*-
"""绘画样式的**正交维度**与**预设**（用户要求③："不要独立样式类，拆成正交维度 + 预设配置"）。

本模块**只做数据**：维度取值表 + 两个预设 + `resolve()`（把预设展开成渲染器能吃的 opts）。
渲染逻辑仍在 `app/paint_render.py`（Ty4 侧）与 `app/paint_render_qq.py`（QQ 侧，sim-core）。

## 为什么要有这一层

以前"Ty4 风格"和"QQ 风格"是两套各自散装默认值：想要"Ty4 的底图 + QQ 的配色"就得改代码。
拆成维度后，任何组合都是 `resolve("ty4", palette="qq")` 一句话。

## 维度（命名已冻结，sim-core / sim-data 共用，**不要改名**）

| 维度 | 取值 | 说明 |
|---|---|---|
| `projection` | `equirect` / `conic` | 见 `app/paint_projection.py`（P4 冻结接口） |
| `labels` | `none` / `edge` / `inside` | 台风名标注 |
| `basemap` | `map_png` / `land_flat` / `none` | `map_png`=卫星底图 1:1 裁剪；`land_flat`=平涂陆海 |
| `marker` | `by_status` / `uniform` | `by_status` 按状态定形状；`uniform` = 老行为（同尺寸） |
| `palette` | `2023` / `classic` / `qq` | 色阶 |
| `legend` | `none`/`tl`/`tr`/`bl`/`br`/`auto` | 图例位置，**默认 none** |
| `gradient_line` | `True`/`False` | 轨迹线按强度渐变（+`gradient_subdiv`，默认 8） |

## 与旧 opts 键的兼容

`resolve()` **同时**输出新旧两套键 ⇒ 老调用方（`TrackMapRenderer(opts)` 直接传旧键）继续可用；
新调用方走 `resolve()`。见 `LEGACY_KEY_MAP`。
"""
from __future__ import annotations

from typing import Dict, Tuple

__all__ = [
    "DIMENSIONS", "TY4_PRESET", "QQ_PRESET", "DEFAULT_PRESET", "PRESETS",
    "MARKER_GEOM_TY4", "BASEMAP_COLORS", "LEGACY_KEY_MAP",
    "resolve", "dimensions_table", "preset_dict",
]

#: 七个正交维度（**冻结命名**）
DIMENSIONS: Dict[str, Tuple[str, ...]] = {
    "projection": ("equirect", "conic"),
    "labels": ("none", "edge", "inside"),
    "basemap": ("map_png", "land_flat", "none"),
    "marker": ("by_status", "uniform"),
    "palette": ("2023", "classic", "qq"),
    "legend": ("none", "tl", "tr", "bl", "br", "auto"),
    "gradient_line": (True, False),
}

#: Ty4 预设：底图自带网格/经纬度标签/红框/黄多边形 ⇒ **程序不画任何标注**
#: （依据：实测底图 `[GLOBAL].png` 自带这些元素；`t4_render.py:427-447` 的 `_draw_grid`
#:   在参考图里**未启用**（`ShowGrid=0`），故视觉上看不出它开没开）
#:
#: ⚠ **T1 真 bug（2026-10-03 修）**：这里原先**没有** `canvas_mode` ⇒ `resolve("ty4")` 出的 dict
#: 缺该键 ⇒ 渲染器回落 `"fit"`（自适应画布），**Ty4 画布公式从未生效**：森拉克渲成 2022×1770，
#: 基准图是 2742×2312（用户："图幅比你这个大很多，你这个甚至不全"）。
#: ⇒ 预设现在**显式**给出 Ty4 公式所需的全部项。
#: 注：渲染器侧读取本来就是 `o.get("canvas_mode") or "fit"`（缺键/None 都安全），
#: **缺的是预设这一侧** —— 教训：预设必须自洽，不能靠渲染器兜底。
TY4_PRESET: Dict[str, object] = {
    "projection": "equirect",
    "labels": "none",
    "basemap": "map_png",
    "marker": "by_status",
    "palette": "2023",
    "legend": "none",
    "gradient_line": False,
    # ── 画布与尺寸：Ty4 公式所需（Settings.ini: Width=1920 Height=1080
    #    WidthSpace=360 HeightSpace=270 Track=0.05 Point=0.4）──
    "canvas_mode": "ty4",
    "min_w": 1920,
    "min_h": 1080,
    # ── **R9（用户 2026-10-03 裁定）：画布参数化 + 比例留白 + 等比缩放** ──
    #:   · `canvas_w/canvas_h` = **出图尺寸**（默认 **2560×1440**；任意更大尺寸可用；
    #:     长宽比**自由**，不强制 16:9）。
    #:   · `pad_ratio = 0.1` = **每侧留白 = 0.1 × 轨迹 bbox 跨度**（比例留白 ⇒ 约束方向
    #:     恰好占 `1/(1+2×0.1)` = **83.3%**）。
    #:   · 几何基准 = **1920×1080**：`scale = canvas_w/1920` ⇒ 标记尺寸/线宽/字号**全乘它**
    #:     （实现 = px/° `k` 随画布等比：2560 档 k=40、1920 档 k=30 ⇒ 比值 **1.3333**）。
    #:   · **与 Ty4 原版不冲突（更正 2026-10-03）**：原版 `WidthSpace=360 / HeightSpace=270`
    #:     是**固定 1920×1080 画布上的固定像素** ⇒ **本身就是固定比例**：
    #:     **360/1920 = 0.1875**（横）、**270/1080 = 0.25**（纵）。Ty4 把画布固定住，
    #:     所以"像素"看起来像常数。现按**比例参数化**（**画布缩放时留白同步放大，无例外**）；
    #:     以用户最新给的 `pad_ratio = 0.1` 为准，原版的 0.1875/0.25 并列记此备查。
    #:   · **仍能按基准图尺寸出图**（供与基准图叠加比对）：把 `canvas_w/canvas_h` 置 `None`
    #:     ⇒ 回老 Ty4 公式 `max(min_w, bbox×30 + 2×WidthSpace)`，可复现
    #:     rita 3129×1080 / haiyan 2541×1080 / megi 1920×1080 / sinlaku 2742×2312。
    "canvas_w": 2560,
    "canvas_h": 1440,
    "pad_ratio": 0.1,
    "scale_base_w": 1920.0,
    "margin_w": 360,          # WidthSpace（**legacy**：ty4 模式且 canvas_w=None 时按像素用）
    "margin_h": 270,          # HeightSpace（**legacy**，同上）
    "coord_unit": "deg",
    "track_deg": 0.05,
    "point_deg": 0.4,
    "draw_order": "points_top",   # = Ty4 Priority=2（点统一置顶）
    "all_points": True,
    # **R14（用户 2026-10-04 裁定）**：ty4 连线改**白色**（用户："为什么 ty4 连接线不是白色?"）。
    # 历史：T3 曾按 Ty4 反推把它设成 `"category"`（起点等级色）——**按用户裁定改回白**。
    # `"white"` 也是渲染器该键的默认（`paint_render.py`：`o.get("track_line") or "white"`）。
    "track_line": "white",
    # T2①（修订）：非正式报按**基准图实测比值** 0.64 缩小
    # （正式报中位 ⌀11 n=244 / 非正式报中位 ⌀7 n=6，腐蚀+2 口径）
    # ⚠ 像素反推，未由二进制验证（x32dbg 动态取参在本环境未打通，
    #   见 output/exploration/re4_environment_pitfalls.md）
    "nonofficial_scale": 0.64,
}

#: QQ 预设（圆锥 + 平涂陆海 + 边缘标注；渲染由 `app/paint_render_qq.py` 负责）
QQ_PRESET: Dict[str, object] = {
    "projection": "conic",
    "labels": "edge",
    "basemap": "land_flat",
    "marker": "by_status",
    "palette": "qq",
    "legend": "none",
    "gradient_line": False,
    #: `@xrq` 斜向水印。**关**（2026-10-03 用户裁定：水印属于自评提供者 xrq，
    #: 我们要的是样式复刻，不替对方署名）。非七维度之一，但属于"样式"的可见成分，
    #: 所以放在预设里显式声明；显式传 `watermark=True` 仍可开。
    "watermark": False,
    # QQ 走自己的取景（不是 Ty4 的 1:1 裁剪公式）⇒ **显式**声明，别靠缺键回落
    "canvas_mode": "fit",
}

PRESETS: Dict[str, Dict[str, object]] = {"ty4": TY4_PRESET, "qq": QQ_PRESET}
DEFAULT_PRESET = "ty4"

#: ── 二进制静态证据（RE4，2026-10-03；逐条 `Typhoon4.exe!地址` 可核）──────────────
#: 出处：`output/exploration/re4b_ghidra.md`（Ghidra 伪代码）§3/§8/§9 与
#: `re4_ty4_binary.md` §3.1–§3.7（objdump 反汇编）；动态取参未打通的原委见
#: `re4_environment_pitfalls.md`。
#:
#: ⚠ **已推翻的一条（原文留痕）**：此前写"形状跳表 `0x0044AFAC` 的成对索引 (0,1)/(2,3)/(4,5)
#:    ⇒ 从二进制侧支持'同一形状两档（实心/空心）'" —— **不成立**：该跳表属于**控件画笔**
#:   `FUN_0044aae0`（`CPaintDC` + `PatBlt` + `GetClientRect(控件自身 HWND)`，地址在易语言
#:   控件注册表 `.rdata:0x0055B364`），**不是地图标记画笔**。控件的两档 ≠ 地图标记的两档，
#:   二者之间**没有任何二进制证据连接**。
#:   ⇒ **在地图标记这一层，凡形状 / 空心的断言一律标注"未由二进制验证"。**
#:
#: ① **图元清单（成立）**：`Polygon` 全程序**仅 1 处** `Typhoon4.exe!0x004432DD`（在
#:    `FUN_00442c00` 的 `case 0xc`）；`Ellipse` 仅 2 处：`0x00442EB5`（`FUN_00442c00` mode 4）、
#:    `0x0044AE2C`（控件画笔）。⇒ **`FUN_00442c00` 是唯一能画 Polygon（三角）的函数
#:    ⇒ 最可能的轨迹点实现**；但**"最可能"≠"已证明"**：它的两条 thunk（`0x443A80` 压 1、
#:    `0x443B40` 压 0x0F）落在函数指针表 `0x54B6C0–0x54B7FF`，而**全程序没有任何指令引用
#:    该表区间**（立即数扫描 0 处）⇒ 调用点**静态不可见**，由易语言运行时按索引走。
#: ② **线宽是查表，不是公式（成立；但该表位于控件画笔那段代码里）**：
#:    `Typhoon4.exe!0x44AB89-0x44ABC6` 读 `[ebx+0x4c]` → **`0→5, 1→6, 2→1, 3→2, 4→3,
#:    else→4`**（随后 `0x44AC1D push esi` 作 `CreatePen` 的宽度）⇒ Ty4 线宽是 **6 档离散值**。
#:    ⚠ **与本实现冲突**：我们用 `round(track_deg×30)=2`（见 `paint_render._px_track`）；
#:      `[ebx+0x4c]` 这个索引从哪来、怎么对应 `Settings.ini` 的 `Track=0.05`，**未取到**
#:      ⇒ **只记录冲突，不据此改数**（没有映射关系就改是瞎猜）。
#:    ⚠ 且该查表在控件画笔内 ⇒ **它是否适用于地图标记同样未证**。
#: ③ **笔色是独立字段（成立）**：`[ebx+0x54]` 一个 COLORREF（`0x44AC0F mov eax,[ebx+0x54]`、
#:    `0x44AC15 push eax`），样式开关 `[ebx+0x48]`；伪代码里**没有 EX/ED/ES/E1..E5 的分档
#:    配色逻辑**（只有单字段）—— 这是"染色阶梯不在绘制层"的**排除性证据**。
#: ④ **程序链了 MFC**：Ghidra FID 把 `0x0053B048` 匹配成
#:    `public: int __thiscall CDC::LineTo(int,int)`（VS2003/2005）⇒ 图元调用走 MFC CDC 包装。
#:
#: ⚠ **方法论边界（永久留痕）**：**三角顶点公式不在二进制里，在数据里** —— `case 0xc` 只读
#:   调用方预算好的点数组（`[p2+0xc]`，`n = 元素数/2`），**不读 `[+0x24]/[+0x30]`、不调用
#:   `DPtoLP`**，且已反编译范围内**不存在 ×1.414 / ×0.866 之类系数** ⇒ 对这类目标，
#:   **量基准图像素反推尺寸是正确方法**，不是"没去找源码"。详见 `re4b_ghidra.md` §8/§9。

#: **Ty4 侧标记几何**（2026-10-03 严格口径实测：4 张基准图、孤立报点 + 连通域不触窗口边）。
#:
#: ⚠ 下列数字（圆 ⌀11 / 方块 9.0×9.0 / 三角 8.9×7.9）**像素反推，未由二进制验证**
#:   （x32dbg 动态取参未打通；Ghidra 也没有给出尺寸公式 —— 顶点是**数据数组**而非公式，
#:   见本文件上方"⚠ 方法论边界"；环境坑见 `output/exploration/re4_environment_pitfalls.md`）。
#:
#: 实测（本机 `probe_ty4_strict.py`；对照真源码 `t4_render.py:205-213` 的 `px_point`：
#: 0.4×30/2 = 6.0 半径 ⇒ 名义 ⌀12，而基准图实测 ⌀11，故复刻取 5.5/6.0 的系数）：
#: 圆 **⌀11.0**(n=87, fill 0.730) / 方块 **9.0×9.0**(n=2, fill 1.000)
#: / 上三角 **8.9×7.9**(n=36, fill 0.517)；下三角 0 样本。
#:
#: 我们的名义几何（`k=30, point_deg=0.4` ⇒ 半径 `r = 6.0`）：
#:   圆 ⌀`2r=12` · 方块边长 `2r=12` · 三角 底 `2r=12` / 高 `1.6r=9.6`
#: ⇒ 要让**名义**落到基准实测值，系数 = 目标 ÷ 名义：
#:   圆 `5.5/6.0=0.917`（基准图实测 ⌀11；公式侧 `t4_render.py:210-213` 给 6.0 ⇒ ⌀12，
#:                        取实战值 ⌀11 ⇒ 半径 5.5。方法与样本见 `r4_ty4_fix.md`）
#:   方块 `4.5/6.0=0.750`（目标 9.0×9.0）     三角 底 `8.9/12=0.742` · 高 `7.9/9.6=0.823`
#: ⚠ **不要与 QQ 侧混用**：QQ 基准图（`xrq-dolphin.png`）是 圆 ⌀19 / 方块 18 / 三角 19×16。
#: ⚠ 这些尺寸同样是**像素反推，未由二进制验证**（x32dbg 动态取参在本环境未打通，
#:   见 `output/exploration/re4_environment_pitfalls.md`）。
#: ⚠ 也别用"bbox 直接比"校尺寸：我们自己的渲染是 4× 超采样，**三角尖端 bbox 会被系统性低估**
#:   （实测：名义 12×9.6 的三角量出来只有 7×6；方块 12→12；圆 12→10）⇒ 以**名义几何**对齐。
#:
#: **口径决策（(a) 名义对齐，2026-10-03）**：sim-core 用"50% 覆盖率面积"这个独立口径复测，
#: 指出我们**名义已对齐 19×16、但渲染墨迹面积仍小 18%**（131 vs 159）。
#: 备选 (b) 是"对齐墨迹面积"：名义放大 `sqrt(159/131) ≈ 1.10` ⇒ 约 `(21, 17.6)`。
#: **我们选 (a)**，理由：(b) 的系数依赖**外部阈值**（50% 覆盖率），换阈值就得重调 ——
#: 那是"用调参掩盖渲染差异"，且会让名义几何失去可读性。残余的 18% 墨迹差是
#: **我们 4× 超采样这一渲染事实**的必然结果，应如实记录而不是用尺寸补偿。
#: 若将来用户反馈"三角观感偏小"，正解是改**渲染**（降低超采样/提高尖端覆盖率），不是改名义尺寸。
MARKER_GEOM_TY4: Dict[str, Dict[str, float]] = {
    #: **圆回到名义 ⌀12（2026-10-03 撤销 0.917 缩放）**。两条独立证据：
    #:   · **源码公式**：`r = Point × px_per_deg / 2 = 0.4 × 30 / 2 = 6.0` ⇒ **⌀12**
    #:     （`Settings.ini` 的 `Point=0.4`；`Typhoon4Py/t4_render.py:210-213` 的 `px_point`）
    #:   · **本机新口径实测（亚像素·名义几何）**：**⌀11.86，IQR 11.85–11.90，n=52**
    #:     （72 条射线、0.05px 步长、归属度覆盖率的 50% 交叉；跨 4 图极差 0.1px）⇒ 与 ⌀12 差 **1.2%**
    #:   · 旧的 ⌀11 是**腐蚀口径的系统性偏小（−0.86）**，不是真值 ⇒ 据此反推的 `5.5/6.0=0.917` **已撤销**。
    #: ⇒ `r_scale = 1.0`（= 源码公式，不再补偿）。
    "circle":   {"r_scale": 1.000},
    "square":   {"half_scale": 0.750},
    #: **R6 收口（2026-10-03）：底边改用实测 9.85，撤掉 ad-hoc 的 ×1.15 补偿。**
    #:   · **实测 9.85 px**（n=29，IQR **9.85–9.90**）—— 口径：**团块法 / 沿团块主轴每站取垂直方向
    #:     `cov=0.5` 亚像素交叉的中位**（`W = r₊+r₋`，即三角**底边**）。该口径**先通过了自验证门**：
    #:     圆团块 n=94、W=11.70、IQR 11.65–11.75、|W−11.86| = 0.16 ≤ 0.5（与射线法 11.86 互证）。
    #:   · **依据**：三角组在分类器验收里 **28/29 判对、0 个误判成圆** ⇒ **该组未被污染**
    #:     （分类器**整体**未通过：圆仅 69% < 90% ⇒ 故此处只据"组未被污染"使用三角数字）。
    #:   · 旧腐蚀口径 **8.9**（系统性偏小）；**被撤掉的补偿值 10.2** = 8.9×1.15（拿有偏口径反推 ⇒ 撤）。
    #:   · 推导：名义底边 = `2r` = 12.0 ⇒ `base_scale = 9.85/12.0 = 0.8208`。
    #: ⚠ **高度不是新口径测的**：新口径只给出**底边 W**、**没给高** ⇒ `height_scale` **维持现状**
    #:   `0.947`（仍源自**旧腐蚀口径**、偏小未修）。**不许用宽高比反推一个新高度**（那是编数）。
    #: （历史：T5 曾按"观感"在基准系数上乘 ×1.15；该补偿已由 R6 实测取代。）
    "triangle": {"base_scale": 0.8208, "height_scale": 0.947},
}

#: `marker="uniform"` = 改前的老几何（三形状同尺度，便于逐位兼容）
MARKER_GEOM_UNIFORM: Dict[str, Dict[str, float]] = {
    "circle":   {"r_scale": 1.00},
    "square":   {"half_scale": 1.00},
    "triangle": {"base_scale": 1.00, "height_scale": 1.00},
}

#: `basemap="land_flat"` 的平涂配色 —— **实测值**（sim-core 从 QQ 基准图 `xrq-dolphin.png`
#: 用零容差 `np.unique(..., return_counts=True)` 取出，并做了三路交叉验证）。
#:
#: | 角色 | RGB | 像素数 | 占全图 | 腐蚀 3px 后"恰为主色" |
#: |---|---|---|---|---|
#: | 海洋 | `(0,34,68)`   | 1 755 659 | 78.082% | 100.0000% |
#: | 陆地 | `(48,112,80)` |   353 603 | 15.726% | 100.0000% |
#:
#: 内部非主色**一个都没有** ⇒ 无渐变/无纹理/无噪点；其余 ~3000 色**全部来自抗锯齿边缘**
#: （海岸线、标记、网格、文字）⇒ **海岸线必须有中间色**，见 `_draw_land_flat` 的线性混合。
#: ⚠ **`(102,204,153)` 一色兼两职**（虚线网格色 **且** QQ 色阶 SCALE2 强度色）——
#:   取底图色时**不能**把它算进来（sim-core 曾因此污染过一次测量）。
#: ⚠ 早先我们自选的 `(86,125,70)/(24,52,92)` 已作废（与实测差 +38/+13/−10 与 +24/+18/+24）。
BASEMAP_COLORS = {"land": (48, 112, 80), "sea": (0, 34, 68)}

#: 旧 opts 键 → 新维度/键（兼容一个版本；`resolve()` 两套都输出）
LEGACY_KEY_MAP: Dict[str, str] = {
    "scheme": "palette（反向：wiki/app→palette；缺省窗口见 `_palette_to_scheme`）",
    "legend_pos": "legend",
    "legend_entries": "legend_entries（保留）",
    "labels(bool)": "labels（True→edge / False→none）",
    "map_path": "basemap=map_png 时的底图路径",
    "canvas_mode": "（不属于 7 维度，原样透传：fit/ty4 画布公式）",
    "gradient_subdiv": "gradient_line 的细分段数（保留）",
}


def _palette_to_scheme(palette: str) -> str:
    """`palette` 维度 → `scheme_color()` 认识的色阶名。

    · `2023` → `2023`（= `WIKI_SCALE`；真源码 `Typhoon4Py/t4_track.py:47-58` 的 `PALETTE_2023`，
      但代码里 TD `(94,186,255)` 在 4 张基准图 **0 命中**，实测是 `(110,193,234)`）
    · `classic` → **也是 `2023`**：真源码 `Typhoon4Py/t4_track.py:59-60`
      `PALETTE_CLASSIC = dict(PALETTE_2023)`，两套逐色相同 ⇒ 忠实复刻就该一样，**不要编造经典色阶**。
    · `qq` → 原样透传（`paint_render_qq.py` 自己实现；`scheme_color` 对未知值会安全回落）。
    """
    return {"2023": "2023", "classic": "2023"}.get(palette, palette)


def preset_dict(preset: str = DEFAULT_PRESET) -> Dict[str, object]:
    """取预设副本（改它不会污染全局预设）。"""
    if preset not in PRESETS:
        raise KeyError(f"未知预设 {preset!r}（可选 {tuple(PRESETS)}）")
    return dict(PRESETS[preset])


def resolve(preset: str = DEFAULT_PRESET, **overrides) -> Dict[str, object]:
    """把预设展开成渲染器 opts。**UI 只需要调这一个函数。**

    用法::

        opts = paint_style.resolve("ty4")                     # Ty4 全套
        opts = paint_style.resolve("qq")                      # QQ 全套
        opts = paint_style.resolve("ty4", palette="qq")       # Ty4 底图 + QQ 配色
        opts = paint_style.resolve(preset="ty4", legend="br") # 显式开图例

    返回的 dict **同时包含新维度键与旧兼容键**（`scheme` / `legend_pos` / `labels` 等），
    所以直接 `TrackMapRenderer(opts)` 即可，老的渲染路径与测试不用改。

    其余渲染参数（`track_deg`/`point_deg`/`min_w`/`min_h`/`margin_w`/`margin_h`/
    `canvas_mode`/`map_path`/`font`/`bg_color`/`draw_order`/`all_points`/`draw_rad`/
    `skip_invest`/`output_size`…）通过 `**overrides` 原样透传。
    """
    opts = preset_dict(preset)
    # 未知维度键：原样透传（渲染器自己认得的参数），不报错
    for k, v in overrides.items():
        if k in DIMENSIONS and v not in DIMENSIONS[k]:
            raise ValueError(f"维度 {k} 不支持取值 {v!r}（可选 {DIMENSIONS[k]}）")
        opts[k] = v

    dim = {k: opts.get(k) for k in DIMENSIONS}
    opts["gradient_subdiv"] = int(opts.get("gradient_subdiv", 8) or 8)

    # ── 旧兼容键（同时输出，老调用方零改动）──
    opts["scheme"] = _palette_to_scheme(str(dim["palette"]))
    opts["legend_pos"] = str(dim["legend"])
    opts["marker_geom"] = (MARKER_GEOM_TY4 if dim["marker"] == "by_status"
                           else MARKER_GEOM_UNIFORM)
    opts["_labels_mode"] = str(dim["labels"])
    # `labels` 这个键老版本是 bool；这里保持 bool 语义（"none"→False）以免老代码误读真值
    opts["labels"] = dim["labels"] != "none"
    return opts


def dimensions_table() -> str:
    """给人看的维度速查（UI/报告用）。"""
    lines = ["维度 | 取值", "---|---"]
    for k, v in DIMENSIONS.items():
        lines.append(f"`{k}` | " + " / ".join(f"`{x}`" for x in v))
    lines += ["", f"预设 = {tuple(PRESETS)}，默认 `{DEFAULT_PRESET}`"]
    return "\n".join(lines)
