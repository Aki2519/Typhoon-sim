# 第四轮简报：绘画复刻 + 全程序抗锯齿

> 用户 2026-10-02 下达。目标见 goal `goal-ad6a55d7`（phase=active）。
> 前三轮成果见 `23_perf_opt_result.md`；本项目已有 33 份报告在 `output/exploration/`。

## 0. 术语（沿用用户定义）

| 词 | 含义 |
|---|---|
| **模拟器** | 整个工作区程序（`main.py`） |
| **模拟模式** | 内部 SimCore（按 `H` 进） |
| **绘画模式** | 功能栏「绘画」/ `app/paint_dialog.py` —— 本轮主战场之一 |

## 1. 两条硬性要求（用户点名）

### H1：绘画的"定点/原点"太像素化
**实测根因**（Lead 已定位，`app/paint_render.py`）：
- `:457` `pygame.draw.circle(...)` —— **pygame 基础图元完全无抗锯齿**
- `:451` `pygame.draw.rect(...)`、`:455` `pygame.draw.polygon(...)` —— 同上
- `:493` `pygame.draw.circle(..., 1)`（风圈）—— 同上
- `:467` `r = max(2, int(round(...)))` —— 半径取整，进一步放大锯齿

**量化证据**：画在透明面上数"边缘中间 alpha 像素"（0<a<255）——
| 画法 | 中间 alpha 像素 |
|---|---|
| 旧 `pygame.draw.circle` | **0**（完全无 AA） |
| 新 `app/paint_aa.aa_disc` | **180** |

视觉对比图：`output/exploration/aa_before_after.png`（上排旧=锯齿星形，下排新=平滑）。

### H2：程序的地图渲染同样无抗锯齿，一并改掉
**实测全仓 aliased 图元 318 处**：`rect` 189 / `line` 70 / `circle` 32 / `polygon` 14 / `lines` 10 / `ellipse` 2，而 `aaline` **仅 1 处**。

| 文件 | 处数 | 用途 |
|---|---|---|
| `simulator/simcore/render.py` | 21 | **模拟模式地图** |
| `app/ty_sim_mixins/_draw_path_mixin.py` | 16 | **路径绘制** |
| `app/renderer.py` | 13 | **主渲染器** |
| `app/typhoon_render.py` | — | 台风图形 |
| `app/paint_render.py` | 10 | 绘画 |

⚠ **重要区分（别过度改）**：**轴对齐实心 `rect` 本来就不需要 AA** —— 整数像素边界是精确的，加 AA 只会白付代价。真正需要的是 **circle / polygon / ellipse / 斜线 / 粗线**。请逐处判断，不要机械替换。

## 2. 已交付的共享底座：`app/paint_aa.py`

Lead 已写好并验证，**所有渲染路径只读使用它，不要各自复制一份超采样逻辑**：

```python
from app import paint_aa
paint_aa.aa_disc(surf, color, center, radius)        # 抗锯齿实心圆（radius 可浮点）
paint_aa.aa_ring(surf, color, center, radius, width) # 抗锯齿圆环（风圈）
paint_aa.aa_poly(surf, color, points)                # 抗锯齿实心多边形（三角/方块）
paint_aa.aa_rect(surf, color, cx, cy, r)             # 抗锯齿方块（中心+半边长）
paint_aa.aa_line(surf, color, p1, p2, width)         # 抗锯齿线段
paint_aa.cache_stats() / clear_cache()               # 确定性指标 / 清缓存
```

**做法**：超采样（4×）精灵缓存 + `smoothscale` 降采样 + LRU 字节上限（6 MB）。
**接口契约**：颜色元组直接用；`center`/`points` 是世界坐标（浮点可以）。
**如果你发现某处需要"整画布超采样"**（例如几千条粗斜线），可以提，但先在报告里给出代价估算。

## 3. Ty4 复刻：权威材料（**先读，别重造**）

| 材料 | 说明 |
|---|---|
| **`output/sim/_reference/TY4_SPEC.md`（623 行）** | **权威规格书**。§14 有「复刻配方（照抄即可）」；§12 列出代码与实测不符处 |
| `tools/ty4_render.py`（696 行） | 已实现的 Ty4 渲染器，含 `PALETTE_2023` |
| `数据/Typhoon4(绘图)/Typhoon4Py/` | exe 的 Python 等价重写（`t4_render.py` / `t4_track.py` / `t4_config.py`） |
| `数据/Typhoon4(绘图)/Settings.ini` | 原程序实际参数：`Track=0.05 Point=0.4 WidthSpace=360 HeightSpace=270 Width=1920 Height=1080 Priority=2 DrawLegends=4 NewColor=1 AllPoints=1` |
| `数据/Typhoon4(绘图)/Typhoon4.exe` | 原程序（2.7 MB，可反编译） |
| 底图 | `G:\气象\typhoon3\map\[GLOBAL].png`（10800×5400 = 360°×180° @30px/°） |

**规格书已确证的关键结论（直接用，不必重验）**：
1. 底图 lon 0..360 / lat 90..−90 / 30 px/°；`x = lon*30`、`y = (90−lat)*30`
2. 画布 = `max(最小宽, 轨迹bbox宽 + 2×半宽留空) × max(最小高, bbox高 + 2×半高留空)`；**底图 1:1 裁剪不缩放**；轨迹**严格居中**
3. 4 张参考图**全部**是当前 `[GLOBAL].png` 的 1:1 裁剪（逐像素吻合 99.3–99.4%）
4. **网格 / 经纬度标签 / 水印 / 红框 / 黄实线多边形 / 黄虚线，全在底图里，程序不画**
5. **预报定位点画三角、历史定位点画圆；预报段连线白色，历史段连线按起点等级着色**
6. `Typhoon4Py` 的 `MAP_BOUNDS` 差 180°；TD 配色、图例尺寸公式、`NewColor` 切换、`MultipleFile` 均与实测不符

⚠ **第一轮已发现的一个真 bug**：`app/constants/colors.py:51-56` 的 `COLOR_2023_SCALE = dict(WIKI_SCALE)` 只多 3 个键，而那 3 个键在 `paint_render.py:69` 被归一化掉 ⇒ **`wiki` 与 `2023` 两套色阶逐像素相同**。规格书 §4.2 说 TD 颜色与代码不符 —— 请一并核对。

## 4. QQ 机器人样式：从 `xrq-dolphin.png` 反推

Lead 已目视提取（1837×1224）：

| 特征 | 值 |
|---|---|
| **投影** | **圆锥**：经线弯曲并向北收敛、纬线是弧形（**不是等距圆柱**） |
| 底图 | 平涂**绿**陆地 + **深蓝**海洋（无卫星纹理） |
| 经纬网 | **虚线**，浅蓝，带 `40°N/30°N/.../0°` 与 `120°E...180°...170°` 标注 |
| 标记 | **圆 + 三角 + 方块**（分阶段），抗锯齿 |
| 连线 | 细白线 |
| 配色 | 浅青→蓝→白→橙→红→深红（按强度） |
| 图例 | **无**（图里没有图例） |
| 水印 | `@xrq` 斜向重复（可选复刻） |

**头号任务是把圆锥投影反推出来**：请从图里的经纬网几何（经线收敛角、纬线弧率）拟合出投影参数，给出**可复现的公式**与**拟合残差**。候选：Lambert 正形圆锥 / 等距圆锥 / Albers。**用网格交点做最小二乘拟合，别靠猜。**

## 5. 环境与纪律（沿用前三轮）

| 项 | 值 |
|---|---|
| 真实运行时 | `/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`（3.14.2 + pygame-ce 2.5.7） |
| 中文输出 | 加 `\| iconv -f GBK -t UTF-8` |
| bash 坑 | `$VAR` 不展开、`for` 循环常失效、`awk '{print $5}'` 被吞 ⇒ **写单行直白命令，解析用 python** |
| `read` 工具 | 用 `G:\气象\...` 绝对路径（不接受 `/mnt/g/...`） |
| **不要开真窗口** | 用 `SDL_VIDEODRIVER=dummy`；**禁止** `python main.py` |
| 内存 | 可用 0.7–2.6 GB 波动，**有 OOM 前科** ⇒ 单进程、别并发 |
| 回退基线 | `_backup/state_v4/` + `SHA256_STATE_V4.txt`（前三轮最终态）；**本轮新文件另建** |

**纪律**：
1. **只改你写作用域内的文件**，越界先问 lead。
2. 每条结论带 `文件:行` 或实测数字 + 复现命令；**区分「实测」与「推断」**。
3. **改前/改后记 `sha256sum`**。
4. 验收用**确定性指标**（抗锯齿：中间 alpha 像素数、边缘梯度；复刻：几何残差、像素差、配色直方图），**不要用计时**（本机计时不可信，P/E 核差 1.90×）。
5. **"有证据地不改"是合格交付** —— 若某处判断不需要 AA，写清理由。

## 6. 六个分区（写作用域互不重叠）

| 任务 | 负责人 | 文件 |
|---|---|---|
| P1 地图/路径渲染抗锯齿 | app-sim | `app/renderer.py`、`app/ty_sim_mixins/_draw_path_mixin.py`、`app/typhoon_render.py` |
| P2 模拟模式渲染抗锯齿 | sim-numerics | `simulator/simcore/render.py` |
| P3 绘画模式：Ty4 保真 + AA 接入 | app-feat | `app/paint_render.py` |
| P4 QQ 机器人样式渲染器（新文件） | sim-core | `app/paint_render_qq.py`（新建）、`app/paint_projection.py`（新建） |
| P5 绘画界面优化 + 投影开关接线 | sim-data | `app/paint_dialog.py`、`app/paint_file_browser.py` |
| P6 独立验证 | sim-run | 只写报告 |

**P3 与 P4 的接口**：P4 提供 `app/paint_projection.py`，导出
`project(name, lon_deg, lat_deg, ctx) -> (x, y)` 与 `PROJECTIONS = {"equirect", "conic"}`。
**P3 不要等 P4**：先保持自己的等距圆柱数学，把投影调用抽成一个可替换的函数指针；
若 `paint_projection` 尚不存在，**回落自带实现 + 一条 warning**，等 P4 交付后接线（由 P5 做开关）。

---

## 7. 本轮新增纪律（都是拿真 bug 换来的）

1. **"接口接了但没被调用过" = 没接。**
   P3 写好了 `不可用('n')` 的告警，但因 P3/P7 的验收探针**全部只跑 equirect**，那句告警一次都没打出来 ⇒ 投影开关**静默失效**，直到 Lead 用非默认值实测才发现。
   ⇒ **凡"新开关 / 新取值 + 老验证集"，验收里必须至少有一条"非默认取值"的端到端用例。**
2. **回退点要存内容，不只是哈希。**
   只记 `sha256` 取不回内容（git 可能是更早版本、`__pycache__` 会被覆盖）⇒ 做不到字节级 before/after diff。
   ⇒ 落地前先 `cp` 到 `_backup/state_vN/`，再登记哈希。
3. **AA 判据有两个前置条件**（都栽过）：
   ① 只在**未铺底**的 SRCALPHA 面上数 `0<a<255`（铺了底则 alpha 恒 255，两边都是 0）；
   ② **别用 45°/轴对齐线**做 AA 检测用例（端点对齐时覆盖率恰好 1.0/0.0，任何正确实现都测出 0）。
   `aaline` **确实抗锯齿**（45°→260、30°→509、5°→517 个中间 alpha 像素），此前"无 AA"的结论是错的。
4. **细斜线的二值 IoU 不能当等价性判据**（上限仅 0.55–0.78，相位敏感）。改用 **alpha 质量比 + 质心位移 + ink bbox**。
5. **不完整的树会伪造物理门失败。** 只带 `simulator/env/model` 时 `free_hash` 给 `5de6d218…`；补齐 8 个数据目录才回到 `2c301447…`。⇒ 复测物理门必须用完整树。
6. **复刻类工作别"顺手加 ceil"。** `ceil(span×30)` 让 sinlaku 宽多 1 px（其跨度 67.4000001°）；规格书用浮点截断 ⇒ 应 `int()`。
7. **测绘/投影类结论要有多路独立验证。** P4 的圆锥：网格残差 0.003 px + 陆地掩码 IoU 0.8833 + 真实 99 报点 99/99 命中（中位 0.00 px）—— 三路都对上才可信。
   反例：P4 第一版用"角度直方图集中度"找顶点，**cy→−∞ 得分最高**，会得出"经线平行 = 等距圆柱"的**完全相反**结论（尺度污染）⇒ 换 RANSAC 才对。
8. **"尽量复刻"不等于"照抄代码常量"。** `TY4_SPEC §4.2` 实测 TD 色 `(110,193,234)` 与 Ty4 源码写的 `(94,186,255)` 不符，4 张参考图里后者命中 **0** 次 ⇒ 以实测为准。
9. **语义冲突不是参数冲突。** `canvas_mode="ty4"` 必须钉死 `equirect`：垫底图 `[GLOBAL].png` 1:1 裁剪**本身就是等距圆柱栅格**（网格/标签/红框都烘焙在图里），在上面画圆锥轨迹会**轨迹与底图系统性错位** —— 给 `make_ctx` 传 `scale=30` 也调和不了。要圆锥请用 `canvas_mode="fit"`。
10. **归属不清先问，别自行扩范围、也别硬凑。** Lead 在本轮任务书里把 B6/B7/B9 写成了"在浏览器"，sim-data 用 grep 逐条证实它们其实在 `paint_dialog.py`，先请裁决再动手 —— 这是正确姿势。
