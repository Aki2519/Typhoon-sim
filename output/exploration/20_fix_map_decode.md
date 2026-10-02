# 20 W4 实施：地图/陆地解码内存优化（app/map_mgr.py）

## 一句话定位

`app/map_mgr.py` 负责把 `map/map.png` 与 `map/land.png`（各 10800×5400 RGBA = **222.47 MB**）解码成 pygame Surface，并用 `land.png` 的 alpha 通道回答"某经纬度是不是陆地"。本报告量化它的解码开销，并实施其中**唯一可证逐字节等价**的一项优化：消除取 alpha 时的整幅 222.47 MB 临时量。

> **本轮有环境事故**：实施中途 bash 工具链整体失效（每条命令都返回 `Bash/Service/E_UNEXPECTED`，连 `echo alive` / `true` 都失败），**只有 read/write/edit/grep/glob 文件工具仍然可用**。因此：改动已落盘，但**峰值实测与 4 个回归测试未能执行**（见 §6、§7）。报告严格区分：**实测**=失效前真跑出来的数字；**推断**=只由代码读出；**unresolved**=需要恢复工具链才能确证。
>
> **§11 是 Lead 裁决后追加的「待实施补丁说明」**：`_land_orig` 释放 222.47 MB 的两个变体 + 懒加载候选的取舍分析。**本轮均未实施**（bash 失效 ⇒ 无法为范围外文件建可验证备份；纪律要求"没有回退点的改动不做"）。

---

## 1. 清单与职责

### 1.1 改动文件（唯一）

| 文件 | 改前 sha256 | 改后 sha256 |
|---|---|---|
| `app/map_mgr.py`（642 行 → 704 行） | `c13f5284538832d7d96a75c7e46a40dfd0eab30273e3aa1944e7c8d4cdd46515`（与 `_backup/README_ROLLBACK.md:43` 一致 ⇒ 改前未被他人动过） | `05df5172bcd8cad3cd8ce6e5d1d1981f23163b2be9dce778c51657bae5b4f967`（实测 `sha256sum`） |

### 1.2 涉及的资产（实测尺寸/字节）

| 路径 | 文件大小 | PNG 头 | 解码后 RGBA |
|---|---|---|---|
| `map/map.png` | 48 174 825 B | `10800 × 5400`，bitdepth 8，colortype 6(RGBA) | **233 280 000 B = 222.47 MB** |
| `map/land.png` | 547 845 B | `10800 × 5400`，同上 | **233 280 000 B = 222.47 MB** |
| `map/land2.png` | 45 986 191 B | `10800 × 5400`，同上 | 233 280 000 B（回退掩码，启动期不读） |
| `map/heightmap.png` | 61 944 675 B | — | 启动期不读（audit 未命中） |

尺寸由 **PNG IHDR 头**读出（只读 33 字节，不解码像素）：

```bash
/usr/bin/python3 -c "import struct;d=open('map/map.png','rb').read(33);print(struct.unpack('>II',d[16:24]),d[24],d[25])"
# → (10800, 5400) 8 6
```

### 1.3 `MapView` / `MapManager` 的关键位置

| 位置 | 职责 |
|---|---|
| `map_mgr.py:74`（原 `:17`） | `MapView.__init__`：`pygame.image.load(img_path).convert()` → **222.47 MB 常驻** |
| `map_mgr.py:523`（原 `:466`） | `_load_land_orig()`：`pygame.image.load(land).convert_alpha()` → **222.47 MB 常驻** |
| `map_mgr.py:540`（原 `:483`） | `_rebuild_land_and_overlay()`：把 land 缩放成屏幕大小 → 提取 alpha（屏幕空间掩码） |
| `map_mgr.py:644`（原 `:586`） | `is_land_at_geo()`：建 **58 320 000 B = 55.62 MB** 地理 alpha 表；旧写法先造 222.47 MB 临时量 |
| `map_mgr.py:634`（原 `:576`） | `is_land_at_screen()`：**全仓零调用**（实测 grep，见 §4.4） |
| `map_mgr.py:675`（原 `:613`） | `update_map_image = update_view` 别名 |

---

## 2. 数据流 / 调用链

```
app/ty_sim.py:402   self.map_mgr = MapManager(self)
app/ty_sim.py:201   map_mgr.update_map_image()          → update_view()
                      └─ MapManager.update_view() :664
                           ├─ _init_map_view() :511 → MapView(...) → image.load(map.png).convert()
                           │                                      ★ 222.47 MB 常驻
                           └─ update_land_mask() :583
                                ├─ _load_land_orig()?  ← 否：只置 _land_lazy=True（懒加载设计）
                                └─ map_view.draw(_cached_map_render)
app/ty_sim.py:215   map_mgr._ensure_land_mask()          → _rebuild_land_and_overlay()
                      ├─ _load_land_orig() :505 → image.load(land.png).convert_alpha()
                      │                            ★ 222.47 MB 常驻
                      ├─ 缩放成 (screen_w, map_height) SRCALPHA  → land_img
                      ├─ _surface_alpha_bytes(land_img)          → _land_alpha_bytes（屏幕空间）
                      └─ 旧写法: image.tobytes(land_img,'RGBA')  ★ 15.78 MB 临时量 @2560×1540
app/ty_sim.py:217   map_mgr.is_land_at_geo(0.0, 0.0)     → 建 55.62 MB 地理 alpha 表
                      └─ 旧写法: image.tobytes(land,'RGBA')[3::4]
                                 ★★ 222.47 MB 整幅临时字节串再切片 —— 启动峰值主因
```

**关键结构性事实（实测 grep）**：`app/ty_sim.py:212-217` **显式强制**在构造/splash 阶段建掩码与 alpha 表（注释写明"否则窗口出现后第一帧才触发, 造成可见卡顿"）。⇒ `map_mgr.py` 里的"懒加载"设计**被调用方抵消**：启动期 `image.load` 稳定 **2 次、444.95 MB**。

**`land.png` 的三个消费者**：

| 消费者 | 用途 | 是否需要 RGBA 像素 |
|---|---|---|
| `_rebuild_land_and_overlay()` `:522` | 屏幕空间掩码 `_land_alpha_bytes` | 需要（要缩放） |
| `is_land_at_geo()` `:644` | 地理 alpha 表 `_land_geo_alpha`（55.62 MB） | 需要（要 alpha） |
| `_load_land_orig()` 的 **4 个外部调用方** | **只是"掩码存在吗"的存在性检查** | **不需要**（见 §4.3） |

---

## 3. 核心机制详解

### 3.1 旧写法为什么会产生 222.47 MB 临时量

```python
# app/map_mgr.py:594（改前）
ab = pygame.image.tobytes(land, 'RGBA')[3::4]
```

`pygame.image.tobytes(surf, 'RGBA')` 返回**整幅** 4 字节/像素的字节串：10800×5400×4 = **233 280 000 B**；随后 `[3::4]` 再拷出 1 字节/像素 = **58 320 000 B**。两者短暂共存 ⇒ 该语句瞬时占用 **233.28 + 58.32 = 291.6 MB**，其中 233.28 MB 是纯临时（切完即可回收）。这正是 `13_perf_startup.md` 记的"启动峰值 833–952 MB"里最大的一块可控项。

### 3.2 "窗口只要 15.8 MB" 的过配比（实测算术）

- 目标窗口 2560×1540 = 3 942 400 px ⇒ 屏幕 Surface 15 782 400 B = **15.05 MB**（RGBA）。
- 两张源图各 58 320 000 px ⇒ 每张是可见像素的 **14.79 倍**；两张合计 466 560 000 B = **444.95 MB** ⇒ **29.6 倍**。
- 与任务书里的"28×"一致（差异来自窗口是否按 2560×1540 还是含底栏的 map_height 计算）。

### 3.3 确定性常驻块清单（实测字节，非估算）

`/mnt/c/.../python.exe -u G:/tmp/probe_map_mem.py app.map_mgr_old 1280 800` 输出（`_land_*` 字节数由 `len()` 直接读出）：

| 块 | 1280×800 实测 | 公式 |
|---|---|---|
| `map original_img` | 233 280 000 B (222.47 MB) | 10800×5400×4 |
| `_land_orig` | 233 280 000 B (222.47 MB) | 10800×5400×4 |
| `land_img`（屏幕空间） | 4 096 000 B (3.91 MB) | screen_w×map_h×4 |
| `_land_alpha_bytes` | 1 024 000 B (0.98 MB) | screen_w×map_h×1 |
| `_land_geo_alpha` | **58 320 000 B (55.62 MB)** | 10800×5400×1 |
| `_cached_map_render` | 4 096 000 B (3.91 MB) | screen_w×map_h×4 |
| **合计** | **534 096 000 B = 509.35 MB** | |

按真实窗口 2560×1540 折算（后三项随屏幕线性放大）：

```text
合计 = 233,280,000×2 + 58,320,000 + 2560×1540×(4+1+4)
     = 466,560,000 + 58,320,000 + 2560×1540×9
     = 524,880,000 + 35,481,600 = 560,361,600 B = 534.41 MB
```

### 3.4 `pygame.image.load` 调用与解码像素量（实测）

| # | 路径 | 尺寸 | 解码字节 |
|---|---|---|---|
| 1 | `./map/map.png` | 10800×5400 | 233 280 000 B (222.47 MB) |
| 2 | `./map/land.png` | 10800×5400 | 233 280 000 B (222.47 MB) |
| | **合计** | | **466 560 000 B = 444.95 MB** |

⇒ **2 次 load，两次都是满幅 10800×5400**；`land2.png`/`heightmap.png` 启动期未读。

### 3.5 实施的三级 alpha 提取（本次改动核心）

新增 `_surface_alpha_bytes(surface)`（`map_mgr.py:39-75`），三级实现，**结果逐字节等价**，只改峰值：

| 级 | 实现 | 峰值 | 等价性论证 |
|---|---|---|---|
| 1 | `bytes(surface.get_view('3'))` | 55.62 MB（只拷 alpha 平面） | pygame-ce `get_view('3')` = 每像素第 4 字节；**长度守卫**只有 `len == w*h` 才采用 |
| 2 | 逐行 `subsurface(y,1) → tobytes('RGBA') → [3::4]` | 1 行 = w×4 B | 与旧表达式**同一算子**逐行施加，拼接结果相同 |
| 3 | 旧写法 `tobytes(s,'RGBA')[3::4]` | w×h×4 B（222.47 MB） | 原实现，最终兜底 |

任一级抛异常或长度不符即向下回退 ⇒ **不可能返回与旧实现不同的字节**（第 3 级就是旧代码原文）。

**为什么这条在"写作用域只有 `app/map_mgr.py`"的约束下是最优选择**：它减掉的是**峰值**（Lead 的首要关切），且不需要新增/改写任何资产文件。

---

## 4. 现状量化结论（含对任务书三处说法的核对）

### 4.1 ✅ "各解码 222.47 MB、合计 445.2 MB" —— 实测成立

本机实测 444.95 MB（= 466 560 000 B），与 445.2 MB 的差异来自 `444.95 vs 445.2` 的进位口径。

### 4.2 ✅ "`is_land_at_geo` 首调产生 222.5 MB 临时量" —— 代码级成立

`map_mgr.py:594`（改前）唯一路径即 `tobytes(land,'RGBA')`。**注意**：探针打印的 RSS/峰值增量是 `0.0`，因为我用的 `ctypes.windll.psapi.GetProcessMemoryInfo` 静默返回失败（后经单独验证 `K32GetProcessMemoryInfo` 可用、WS=13.7 MB）⇒ **我自己的 RSS/峰值读数无效**，该 222.5 MB 是**代码级确定量**（字节数可算），不是我的 RSS 实测。

### 4.3 ⚠ "`MapManager` 非单例（两个实例 1.29 GiB）" —— 静态证据不支持，需动态复核

实测 grep：

- `MapManager(` 全仓**只有 1 处实例化**：`app/ty_sim.py:402`；
- `TySim(` 全仓**只有 1 处实例化**：`main.py:180`；
- `app/resource_manager.py:5` 只是 `from .map_mgr import MapView, MapManager` 并放进 `__all__`（**不实例化**）。

⇒ 单进程常态下**只应有 1 个 `MapManager`**。同时存在两个要么来自"旧 `TySim` 未回收就重建"（模式切换/测试用例），要么来自某次探针的计数口径。**推断**：`16_perf_diagnosis.md:195` 的"两个实例"是测试/重入场景下的瞬时计数。⇒ 我**没有**实施"改单例"或"跨实例共享 Surface"：在只有一个实例的常态下收益为 0，而模块级缓存会在实例销毁后**继续持有** 222 MB（对 `test_paint.py` 这类连续构造 6 次 `TySim` 的场景反而是内存回归）。

### 4.4 🆕 `is_land_at_screen` 与 `land_img` 是事实死码（实测 grep）

- `is_land_at_screen` 全仓只出现在 `map_mgr.py`（定义 + docstring），**零调用方**；
- `land_img` 全仓只在 `app/ty_sim.py:594-595` 被读，而那段就是 `self.map_mgr.land_img = None`（**只丢弃，不使用**）。

⇒ `_rebuild_land_and_overlay()` 产出的屏幕空间掩码（2560×1540 时 15.78 + 3.94 MB，外加 15.78 MB 临时量）**没有任何消费者**。这是**下一步**最有价值的删减点（见 §7 建议①）。

### 4.5 🆕 `_land_pending` 恒为 False ⇒ 延时重建分支是死路

实测 grep：`_land_pending` 全仓**只有写 `False` 与读**，**没有任何一处写 `True`** ⇒ `map_mgr.py:590-592` 的"视图稳定后补做被推迟的陆地掩码重建"永不执行。

### 4.6 `_load_land_orig()` 的 4 个外部调用方全是存在性检查（逐行读过）

| 调用方 | 代码 | 是否用像素 |
|---|---|---|
| `app/ty_sim.py:594` 邻近 | 不调用 `_load_land_orig` | — |
| `app/view_state.py:100` | `if map_mgr._load_land_orig() is None: return` | 否 |
| `app/playback_ctrl.py:286` | `if mm._load_land_orig() is None: return` | 否 |
| `app/statistics/season_stats.py:173` | `if mm._load_land_orig() is None: return` | 否 |
| `app/ty_sim_mixins/_draw_path_mixin.py:1288` | `if mm is None or mm._load_land_orig() is None: return recs` | 否 |

**这条事实的意义**：222.47 MB 的 `_land_orig` 之所以必须常驻，**仅仅是为了这 4 处能拿到"非 None"**。若把这 4 处改成廉价的 `has_land_mask()`（或让 `_load_land_orig()` 在像素非必需时返回占位符），就能在建完 `_land_geo_alpha` 后**永久释放 222.47 MB**。这**需要改 4 个本任务范围之外的文件** ⇒ 按纪律我**没有动**，已在交接里请 Lead 决定（见 §7 建议②）。

---

## 5. 候选改法与取舍

| 候选 | 期望收益 | 画质/功能风险 | 是否在范围内 | 结论 |
|---|---|---|---|---|
| **(a) 懒加载：启动不预解码** | 启动期省 444.95 MB 解码 | 低 | ⚠ **被调用方抵消**：`app/ty_sim.py:212-217` 显式在构造期强制 `_ensure_land_mask()` + `is_land_at_geo()`，且注释说明这是**有意**的（避免首帧卡顿）。要真正懒加载必须改 `app/ty_sim.py`（越界） | **未实施**，已上报 |
| **(b) 降采样到接近窗口尺寸** | 常驻可降到 ~60 MB | **高**：`MapView.original_img` 还被 `app/statistics/chart_helpers.py:150` 当作全分辨率底图用（`orig.subsurface`+缩放），且缩放上限是 **8.0×**（`map_mgr.py:267` zoom_at、`:297-299` set_view_region）⇒ 任何非整数降采样都无法保证像素差为 0 | 是（但要重建资产或改消费方） | **未实施**（无法证明零像素差） |
| **(c) 分块/tile 按需解码** | 最彻底 | 中高 | 是 | **未实施**：PNG 无随机访问（PIL 的 `draft()` 只对 JPEG 有效），要重写 `_render_mip_window` + 三套缓存（`_pan_cache`/`_window_cache`/`_cached_map_render`），回归面极大 |
| **(d) `MapManager` 改单例 / 跨实例共享 Surface** | 每多一个实例省 ~278 MB | 低 | 是 | **未实施**：静态证据显示常态只有 1 个实例（§4.3），收益≈0；模块级缓存会在实例销毁后继续持有 222 MB，对连续构造场景是**回归** |
| **(e) 只消除取 alpha 的整幅临时量（本次实施）** | **峰值 −222.47 MB**，常驻 ±0 | **无**（逐字节等价 + 三级回退） | 是 | ✅ **已实施** |
| (f) 预计算 `land_mask.bin`（1 B/px，58.3 MB 文件） | 常驻 −222 MB | 需新增资产文件 | 否（要写 `map/`） | 未实施，建议上报 |

选择 **(e)** 的理由：唯一"可证逐字节等价"的一项；直接命中 Lead 的第一关切（**峰值** 833–952 MB）；改动集中在 1 个函数 + 2 个调用点；带运行时开关；不需要新资产、不触碰任何其他文件。

---

## 6. 已实施改动

### 6.1 改动内容（全部在 `app/map_mgr.py`）

| 位置 | 改动 |
|---|---|
| `:14-36` **新增** | 开关族：`_flag()` + `ALPHA_NO_TRANSIENT`（默认 True，环境变量 `TSIM_MAP_ALPHA_NO_TRANSIENT=0` 关闭） |
| `:39-75` **新增** | `_surface_alpha_bytes(surface)`：三级 alpha 提取，长度守卫 + 逐行回退 + 旧路径兜底 |
| `:572-576` **改** | `_rebuild_land_and_overlay()`：`raw = tobytes(land_img,'RGBA'); raw[3::4]` → `_surface_alpha_bytes(land_img)`（省 15.78 MB 临时量 @2560×1540） |
| `:650-657` **改** | `is_land_at_geo()`：`tobytes(land,'RGBA')[3::4]` → `_surface_alpha_bytes(land)`（省 **222.47 MB** 临时量） |

`os` 已在 `:3` 导入，无需新增 import；其余逻辑、属性、公开方法签名**一字未动**。

### 6.2 回退（两级，都不用改代码即可切回）

```bash
# ① 运行时开关（最快，无需重启以外的任何操作）
TSIM_MAP_ALPHA_NO_TRANSIENT=0 python main.py        # 恢复旧行为（整幅 tobytes）
# ② 整文件回退
cp output/exploration/_backup/map_mgr.py app/map_mgr.py
# ③ 校验其他文件未被本轮改动
sha256sum -c output/exploration/_backup/SHA256_BEFORE.txt
```

### 6.3 改前 / 改后 sha256（均实测）

```text
改前: c13f5284538832d7d96a75c7e46a40dfd0eab30273e3aa1944e7c8d4cdd46515  app/map_mgr.py
改后: 05df5172bcd8cad3cd8ce6e5d1d1981f23163b2be9dce778c51657bae5b4f967  app/map_mgr.py
```

改前的值与 `output/exploration/_backup/README_ROLLBACK.md:43` 逐字符一致 ⇒ 改前该文件**未被本批其他人动过**，本轮 diff 全部属于我。

---

## 7. 如何运行 / 如何验证（含未完成项）

### 7.1 已完成的验证（失效前）

| 项 | 结果 |
|---|---|
| PNG 真实尺寸（只读头，不解码） | `map/map.png` / `land.png` / `land2.png` 均 **10800×5400**，RGBA |
| `pygame.image.load` 调用次数 | **2**（map.png、land.png） |
| 解码像素合计 | **466 560 000 B = 444.95 MB** |
| Surface/表字节数 | 见 §3.3 表（`_land_geo_alpha` = 58 320 000 B 实测 `len()`） |
| 常驻块合计 | 1280×800 → 509.35 MB；按 2560×1540 折算 → 534.41 MB |
| 免费物理内存（探针运行时刻） | 可用 5099.2 MB / 总 15.74 GB（`GlobalMemoryStatusEx` 实测） |
| 我自己的 RSS/峰值读数 | ❌ **无效**：`ctypes.windll.psapi.GetProcessMemoryInfo` 返回 0；正确写法已确认为 `kernel32.K32GetProcessMemoryInfo`（实测 WS=13.7 MB） |

### 7.2 ✅ 已完成的等价比对（改前 app.map_mgr_old vs 改后 app.map_mgr，同进程顺序执行）

探针 `G:/tmp/cmp_map_render.py`，屏幕 640×384，虚拟 SDL，先跑 OLD 并只留摘要 →
`del` + `gc.collect()`（可用内存 2569 → 2687 MB）→ 再跑 NEW：

| 比对项 | OLD | NEW | 一致 |
|---|---|---|---|
| `_land_geo_alpha` 长度 | 58 320 000 | 58 320 000 | ✅ |
| **`_land_geo_alpha` sha256(前 16)** | `c755ccff7edab656` | `c755ccff7edab656` | ✅ **逐字节相同** |
| `_land_alpha_bytes` 长度 | 245 760 | 245 760 | ✅ |
| **`_land_alpha_bytes` sha256(前 16)** | `998f30a112971527` | `998f30a112971527` | ✅ **逐字节相同** |
| `is_land_at_geo` 36×72 网格 sha | `04595b23fa1571c0` | `04595b23fa1571c0` | ✅（陆地格 842 = 842） |
| `is_land_at_screen` 网格 sha | `56b611415b0501c2` | `56b611415b0501c2` | ✅（陆地格 218 = 218） |
| `_land_orig` 尺寸 | (10800, 5400) | (10800, 5400) | ✅ |
| 几何 `(img_w, img_h, min_scale)` | (10800, 5400, 0.071111111111) | 同 | ✅ |
| **像素：cover-min（最小缩放）** | `bbbb1ca556fb1190` | `bbbb1ca556fb1190` | ✅ **0 差异** |
| **像素：scale=1.0 平移** | `68cf708c376ed55e` | `68cf708c376ed55e` | ✅ **0 差异** |
| **像素：scale=2.0 放大** | `2d904eaeb00d50f6` | `2d904eaeb00d50f6` | ✅ **0 差异** |
| **像素：scale=8.0 最大放大** | `db7c5f22955ddb6c` | `db7c5f22955ddb6c` | ✅ **0 差异** |
| **像素：跨 0°/360° 缝** | `bb96891f8fca969f` | `bb96891f8fca969f` | ✅ **0 差异** |
| **像素：corner 底部对齐** | `c8c9f4904e0fdcb2` | `c8c9f4904e0fdcb2` | ✅ **0 差异** |
| 进程工作集（跑完时） | **537.7 MB** | **538.0 MB** | ✅ 持平（本次不动常驻） |
| 进程峰值（同进程内累计） | 539.8 MB | 540.1 MB | ✅ 持平 |

**总判定：`全部一致 ✅`**（脚本原话）。⇒ 平移/缩放/跨缝/底部对齐的**底图像素完全一致**，
陆地判定（`is_land_at_geo` / `is_land_at_screen` 两条口径）与两张 alpha 表**逐字节相同**。

### 7.3 未能执行的验证（bash 工具链两次失效）

> **探针已修好**：`G:/tmp/probe_map_mem.py` 的内存读法已从"静默失败返回 0"的 `ctypes.windll.psapi.GetProcessMemoryInfo` 改为 **`kernel32.K32GetProcessMemoryInfo` + 显式 `argtypes/restype`**（同一改法在 `cmp_map_render.py` 里实测读到 WS=537.7 MB，确认可用）⇒ 谁跑这两条命令都能拿到真实工作集/峰值。

```bash
# ① 改后峰值（**关键剩余项**：必须在**独立进程**里跑，PeakWorkingSetSize 是进程累计量）
/mnt/c/.../python.exe -u "G:/tmp/probe_map_mem.py" app.map_mgr_old 2560 1540   # 预期峰值 ≈ 535+233 MB
/mnt/c/.../python.exe -u "G:/tmp/probe_map_mem.py" app.map_mgr     2560 1540   # 预期峰值 ≈ 535 MB

# ② 目标窗口尺寸下的等价比对（640×384 已通过；2560×1540 属加强项）
CMP_W=2560 CMP_H=1540 /mnt/c/.../python.exe -u "G:/tmp/cmp_map_render.py"

# ③ 回归（单文件 timeout 300，禁全量）
for f in test_map_resize test_render_geom test_ocean_area test_landfall_view_invariance; do
  timeout 300 /mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe -m pytest tests/$f.py -q --no-header
done

# ④ 开关自检：关掉开关后结果必须仍与改前一致
TSIM_MAP_ALPHA_NO_TRANSIENT=0 /mnt/c/.../python.exe -u "G:/tmp/cmp_map_render.py"
```

### 7.4 预期收益（确定性，非计时）

| 指标 | 改前 | 改后（预期） | 依据 |
|---|---|---|---|
| `is_land_at_geo` 首调峰值临时量 | 233.28 MB（整幅 RGBA）+ 58.32 MB | **55.62 MB**（或逐行回退时 1 行） | 代码路径 |
| `_rebuild_land_and_overlay` 临时量 @2560×1540 | 15.78 MB + 3.94 MB | **3.94 MB** | 代码路径 |
| 常驻块合计 | 534.41 MB | **534.41 MB（不变）** | 本次不动常驻 |
| `image.load` 次数 / 解码像素 | 2 / 444.95 MB | **2 / 444.95 MB（不变）** | 本次不动解码 |

---

## 8. 观察到的问题 / 风险 / 不一致

**MAJOR-1｜两张 10800×5400 底图常驻 444.95 MB，而窗口只需 15.05 MB（29.6× 过配）。**
现象：`map.png` 与 `land.png` 各 222.47 MB，启动期 2 次 `image.load`。
证据：§1.2、§3.4 实测；`map_mgr.py:74`（原 `:17`）、`:523`（原 `:466`）。
影响：这是启动 RSS 的主体。**本轮只消除了峰值的一部分**，常驻 444.95 MB 未动 ⇒ 仍需资产侧或调用方改动。

**MAJOR-2｜`app/ty_sim.py:212-217` 强制启动期建掩码，抵消了 `map_mgr.py` 的懒加载设计。**
证据：`map_mgr.py:583-605`（`update_land_mask` 只置 `_land_lazy=True`）vs `app/ty_sim.py:215,217` 显式调用。
影响：候选 (a) 无法在 `app/map_mgr.py` 内单独生效 ⇒ 必须 Lead 决策是否动 `app/ty_sim.py`。

**MAJOR-3｜222.47 MB 的 `_land_orig` 只是为了 4 处"存在性检查"而常驻。**
证据：§4.6 逐行核对 4 个调用方（`view_state.py:100`、`playback_ctrl.py:286`、`season_stats.py:173`、`_draw_path_mixin.py:1288`），全部是 `if ... is None: return`。
影响：改成 `has_land_mask()` 即可**常驻 −222.47 MB**；但需改 4 个范围外文件 ⇒ 已上报。

**MINOR-1｜`is_land_at_screen` 与 `land_img` 事实死码，却仍在启动期付出 15.78 + 3.94 MB 常驻与 15.78 MB 临时量。**
证据：§4.4（grep 零调用方；`ty_sim.py:594-595` 只把 `land_img` 置 None）。

**MINOR-2｜`_land_pending` 恒 False ⇒ `map_mgr.py:590-592` 的延时重建分支永不执行。**
证据：§4.5（全仓只有写 `False`，无写 `True`）。

**MINOR-3｜任务书的"两个 `MapManager` 实例 1.29 GiB"与静态证据不符。**
证据：§4.3（唯一实例化点 `app/ty_sim.py:402`；`TySim(` 唯一实例化点 `main.py:180`）。
影响：若据此去做"单例化"，收益可能为 0 并引入模块级 222 MB 常驻。

**MINOR-4｜`is_land_at_screen` 的屏幕掩码在视图变化后不重建（陈旧）。**
证据：`update_land_mask:583-605` 只置 `_land_lazy=True`；`_ensure_land_mask:614-632` 在 `_land_alpha_bytes is not None` 时直接返回 ⇒ 平移/缩放后屏幕掩码仍是首帧的。
影响：因 `is_land_at_screen` 无调用方，当前无害；一旦有人启用它会得到**错误**的陆海判定（比 `is_land_at_geo` 更不可靠）。

**MINOR-5｜我自己的内存读数在首次探针里是坏的（0.0 MB）。**
证据：`ctypes.windll.psapi.GetProcessMemoryInfo` 返回失败但不抛异常；正确 API 为 `kernel32.K32GetProcessMemoryInfo`（实测 WS 13.7 MB）。
影响：本报告所有"MB"都改用**字节计数**（`len()` / `get_pitch()×h`）而非 RSS；RSS 侧只能引用 `13_perf_startup.md`（ctor 后 580.6/722.1 MB、峰值 833.2/944.5 MB）。

---

## 9. unresolved（未能确证）

1. **改后的峰值（PeakWorkingSetSize）实测**：需要**独立进程**各跑一次 OLD/NEW（峰值是进程累计量，同进程比不出来）。我的正确读法（`kernel32.K32GetProcessMemoryInfo` + `argtypes`）已在 `G:/tmp/cmp_map_render.py` 里验证可用（读到 WS=537.7 MB），但 `probe_map_mem.py` 在 2560×1540 的那次运行**恰好撞上 bash 第二次失效**（`Bash/Service/E_UNEXPECTED`）。命令见 §7.3①。**预期**：OLD 峰值 ≈ 535+233 MB，NEW ≈ 535 MB。
2. **`get_view('3')` 快路是否真的被采用**：`cmp_map_render.py` 只证明了**结果**逐字节相同，没有区分它走的是快路还是逐行回退。卡点：需要打印一次实际分支。**重要**：无论走哪条，222.47 MB 的整幅临时量都不再出现（逐行路径的峰值是 1 行），所以**内存结论不受影响**；受影响的只是 alpha 提取的耗时。
   附带一条**已确证**的事实（比我原计划的"未验证"更强）：`cmp_map_render.py` 成功 `import` 并驱动了改后的 `app/map_mgr`，跑通了 `update_view / _ensure_land_mask / is_land_at_geo / is_land_at_screen / update_land_mask / draw_map`，且**改动函数 `_surface_alpha_bytes()` 的真实输出（10800×5400 的 alpha 表）与旧表达式 `tobytes(...)[3::4]` 的 sha256 完全相同** ⇒ 模块可导入、可运行、被改动的代码路径确实执行了、且结果逐字节等价。
3. **4 个回归测试未跑**：`test_map_resize` / `test_render_geom` / `test_ocean_area` / `test_landfall_view_invariance`（bash 两次失效，未取得时间窗）。命令见 §7.3③。
4. **2560×1540 目标窗口下的等价比对**：只在 640×384 跑通了（6 视角 + 两张网格全等）。目标尺寸属**加强项**——它改变的只是屏幕尺寸常量与 `min_scale`，代码路径相同。命令见 §7.3②。
5. **开关关闭态（`TSIM_MAP_ALPHA_NO_TRANSIENT=0`）的自检**：未跑。按代码结构它必然退回旧表达式原文，但未实测。命令见 §7.3④。
6. **"两个 `MapManager` 实例"的动态复核**：静态只有 1 个实例化点（`app/ty_sim.py:402`）与 1 个 `TySim` 实例化点（`main.py:180`），但 `16_perf_diagnosis.md:195` 声称实测 2 个。卡点：需要跑一轮 GUI 并计数（本轮内存/工具链不允许）。
7. **`chart_helpers.render_map_subset` 是否对 `original_img` 有写操作**：只读代码看到 `subsurface`/缩放（只读），未逐行读完整函数。与候选 (b) 的可行性有关；本轮未实施降采样，故不影响本次结论。

---

## 10. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 本次新增开关与三级 alpha 提取 | `app/map_mgr.py:14-75` |
| 屏幕空间掩码取 alpha（改动点①） | `app/map_mgr.py:572-576` |
| 地理 alpha 表取 alpha（改动点②，原 222.47 MB 临时量） | `app/map_mgr.py:650-657` |
| `land.png` 解码点 | `app/map_mgr.py:523`（原 `:466`） |
| `map.png` 解码点 | `app/map_mgr.py:74`（原 `:17`） |
| 强制启动期建掩码（懒加载被抵消处） | `app/ty_sim.py:212-217` |
| 4 处"仅判存在"的调用方 | `app/view_state.py:100`、`app/playback_ctrl.py:295`、`app/statistics/season_stats.py:173`、`app/ty_sim_mixins/_draw_path_mixin.py:1288` |
| 零调用方的屏幕掩码 API | `app/map_mgr.py:634`（原 `:576`）`is_land_at_screen` |
| `_land_pending` 死分支 | `app/map_mgr.py:590-592` |
| 全分辨率底图的第二个消费者 | `app/statistics/chart_helpers.py:150` |
| 度量探针 | `G:/tmp/probe_map_mem.py` |
| 等价比对探针 | `G:/tmp/cmp_map_render.py` |
| 回退 | `output/exploration/_backup/README_ROLLBACK.md`、`TSIM_MAP_ALPHA_NO_TRANSIENT=0` |
| 改前基线 sha256 | `output/exploration/_backup/README_ROLLBACK.md:43` |
| **待实施补丁（#1 懒加载 / #2 释放 land RGBA）** | **§11** |

---

## 11. 待实施补丁说明（Lead 已裁决：#2 批准待备份、#1 作为候选留待环境恢复）

> 前置条件（两条都适用）：**bash 恢复** → `sha256sum app/map_mgr.py` 记基线 → 把**当前**文件备份到 `output/exploration/_backup/map_mgr_v2.py`（现有 `_backup/map_mgr.py` 是"本轮 alpha 改动之前"的版本，回退它会连带撤掉已验收的 alpha 优化）→ 再动手。

### 11.1 #2 释放 `_land_orig`：常驻 −222.47 MB　✅ **已于 2026-10-02 实施并验收通过，见 §12**

> 实施时的**唯一偏离**：像素属性保持名为 `_land_orig`（设计里叫 `_land_px`），原因是
> `tests/test_landfall_view_invariance.py:173-186` 直接注入 `mgr._land_orig`，而 `tests/`
> 不在写作用域。详见 §12.2 —— 这处偏离反而让改动更小、且不破坏任何测试。

**收益算式（确定性字节，基于 §3.3 实测）**

| | @2560×1540 |
|---|---|
| 现状常驻块合计 | 233 280 000 ×2 + 58 320 000 + 2560×1540×9 = **560 361 600 B = 534.41 MB** |
| #2 之后 | 560 361 600 − 233 280 000 = **327 081 600 B = 311.95 MB（−222.47 MB，−41.6%）** |
| 峰值额外收益 | 释放后，后续任何路径都走 `_land_pixels()` 按需重解码；与已实施的 alpha 优化叠加 ⇒ **222.47 MB 的整幅临时量不会再次出现** |

**方案选择：推荐「变体 A」——只改 `app/map_mgr.py`，4 处调用方一行都不用动。**

Lead 原计划是"把 4 处 `if mm._load_land_orig() is None:` 换成 `has_land_mask()`"。我在写补丁时发现一个**更省事、也更安全**的等价做法：**让 `_load_land_orig()` 自己在"像素不需要"时返回一个廉价的真值占位符**。因为那 4 处**只判 `is None`**（§4.6 逐行核对过），它们对返回值"是不是真 Surface"毫无兴趣 ⇒ 语义完全保持，且不触碰任何范围外文件、也不会让那 4 个文件的既有测试 stub 失效。

**变体 A 的精确步骤（全部在 `app/map_mgr.py` 内）**

1. **加开关**（放在已实施开关族 `:35-36` 旁边）：

```python
#: True = 建完地理 alpha 表后释放 land RGBA(常驻 −222.47 MB); _load_land_orig() 改为
#: 返回 1×1 占位(4 处调用方只判 None, 见报告 §4.6); 需要像素请用 _land_pixels()。
LAND_RGBA_TRANSIENT = _flag('MAP_LAND_RGBA_TRANSIENT', True)
```

2. **`MapManager.__init__`（现 `:484-501` 区块）新增三个属性**（保留 `_land_orig` 语义不变）：

```python
        self._land_px = None          # 真实像素 Surface(可能已被释放)
        self._land_ok = False         # 是否**曾经成功解码**(决定能否安全返回占位符)
        self._land_token = None       # 1×1 SRCALPHA 占位(懒建, 仅判 None 用)
```

3. **新增 `_land_pixels()`**（把现有 `_load_land_orig()` 的**代码体原文搬过来**，只把 `self._land_orig` 换成 `self._land_px`，成功时置 `_land_ok = True`）：

```python
    def _land_pixels(self):
        """需要**像素**时调用: 常驻则直接返回, 否则(重新)解码 land.png(222.47 MB)。"""
        if self._land_px is not None:
            return self._land_px
        for path in (LAND_MASK, LAND_MASK_FALLBACK):
            if not os.path.exists(path):
                continue
            try:
                self._land_px = pygame.image.load(path).convert_alpha()
                self._land_ok = True
                if path != LAND_MASK:
                    logger.info(f"LAND_MASK {LAND_MASK} 缺失, 已退化为 {path}")
            except Exception as e:
                logger.error(f"加载陆地掩码失败({path}): {e}")
                self._land_px = None
            if self._land_px is not None:
                break
        return self._land_px
```

4. **`_load_land_orig()` 改为两步**（**保持旧签名与旧语义：返回 None ⇔ 掩码不可用**）：

```python
    def _load_land_orig(self):
        """**保持旧契约**：掩码不可用 → None；可用 → 非 None。
        但"可用"时**不保证是真像素**：已成功解码过且像素已释放时返回 1×1 占位
        (常驻 0 字节)。4 处外部调用方只判 `is None`(报告 §4.6)。
        需要像素请用 `_land_pixels()`。"""
        if self._land_px is not None:
            return self._land_px
        if LAND_RGBA_TRANSIENT and self._land_ok:
            if self._land_token is None:
                self._land_token = pygame.Surface((1, 1), pygame.SRCALPHA)
            return self._land_token
        return self._land_pixels()        # 从未成功过 → 真解码(失败即 None, 与旧行为同)
```

5. **两个内部消费者改用 `_land_pixels()`**（各 1 行）：
   - `_rebuild_land_and_overlay()` 现 `:541`：`land = self._load_land_orig()` → `land = self._land_pixels()`
   - `is_land_at_geo()` 现 `:647`：`land = self._load_land_orig()` → `land = self._land_pixels()`
   并在 `is_land_at_geo()` **建完表之后**追加释放：
   ```python
        if LAND_RGBA_TRANSIENT:
            self._land_px = None      # 像素不再需要; _land_ok 保持 True ⇒ 外部判存在仍为真
   ```
6. **`_ensure_land_mask()` 现 `:619-624` 的前置探测**：把 `self._land_orig` 统一改成 `self._land_px`（它读该属性就是为判"像素是否常驻"）。

**语义等价性论证（关键：会不会改变任何可观察行为）**

| 情形 | 旧行为 | 变体 A 行为 | 等价？ |
|---|---|---|---|
| 无掩码文件 | `_load_land_orig()` → None，4 处 return | 同（`_land_ok=False` → 走真加载 → None） | ✅ 完全一致 |
| 文件存在但解码抛异常 | 每调用一次就重试一次加载，返回 None | 同（`_land_ok` 保持 False ⇒ 每次都重试） | ✅ 完全一致 |
| 文件正常（生产常态） | 返回真 Surface（222.47 MB 常驻） | 首次返回真 Surface；`is_land_at_geo` 建表后释放，此后返回 1×1 占位 | ✅ **4 处调用方只判 None，分支走向完全相同**；`is_land_at_geo`/`is_land_at_screen` 结果由 alpha 表决定，不受占位符影响 |
| 有人在范围外拿返回值当像素用 | 拿到真 Surface | 拿到 1×1 | ⚠ **唯一风险**；已实测 grep：`app/` 内 4 处全是 `is None`，`tests/` 内 3 处是自定义 stub、1 处 `return_value=None`，**无一处使用像素** |

⇒ 变体 A 与旧实现的**分支走向与查询结果完全相同**，差异只在"内存里有没有那份像素"。

**变体 B（Lead 原指定：4 处替换为 `has_land_mask()`）—— 精确位置（`grep` 权威行号，2026-10-02 复核）**

| # | 文件:行 | 原文 | 归属 |
|---|---|---|---|
| 1 | `app/playback_ctrl.py:295` | `if mm._load_land_orig() is None:` | **app-sim**（Lead 已指派，避免写作用域冲突） |
| 2 | `app/view_state.py:100` | `if map_mgr._load_land_orig() is None:` | 待指派 |
| 3 | `app/statistics/season_stats.py:173` | `if mm._load_land_orig() is None:` | 待指派 |
| 4 | `app/ty_sim_mixins/_draw_path_mixin.py:1288` | `if mm is None or mm._load_land_orig() is None:` | 待指派 |

> **行号漂移说明**：我上一轮报的是 `playback_ctrl.py:286`，那是**备份副本** `output/exploration/_backup/playback_ctrl.py:286` 的行号；app-sim 在本文件**靠前处**改了 9 行，实际位置是 `:295`（Lead 的 grep 正确）。另两个内部调用点（`app/map_mgr.py:541` / `:647`）在变体 B 下同样要改成 `_land_pixels()`，否则释放后重建会再解码一次。

**`has_land_mask()` 的语义确认（Lead 要求说清"能不能安全替换"）**

```python
def has_land_mask(self) -> bool:
    """陆地掩码是否可用。**与旧的 `_load_land_orig() is not None` 精确等价**。"""
    if self._land_ok:                       # 曾经成功解码过 → 答案已知且精确
        return True
    if self._land_px is not None:
        return True
    return os.path.exists(LAND_MASK) or os.path.exists(LAND_MASK_FALLBACK)
```

- **在掩码"未建"时它给出的答案对吗？** 分两种版本：
  - **朴素版**（`return os.path.exists(...)`，即纯文件存在性）**不完全等价**：差异只在"**文件存在但解码失败**"这一降级情形——旧代码返回 `None`（调用方 `return`，**不改** `ty.v.last_on_land`），朴素版返回 `True`（调用方继续 → `is_land_at_geo` 因 `land is None` 恒返回 `False` → **把 `last_on_land` 一律置为 False**）。这是**可观察的行为差异**，发生在损坏/权限异常的掩码上。
  - **推荐版**（上面的 `_land_ok` 混合版）**在生产路径上与旧语义逐字等价**：`app/ty_sim.py:215`（`_ensure_land_mask()`）与 `:217`（`is_land_at_geo(0,0)`）在构造期就**强制**跑过一次真实加载（§4 已证），所以从启动起 `_land_ok` 已确定；此后 `has_land_mask()` 返回的正是旧 `_load_land_orig() is None` 的真值，**零解码、零行为差异**。唯一理论差异在"从未加载过 + 文件损坏"这一**不可达组合**（因为启动期一定会加载）。
- **⇒ 结论：只有"混合版"能安全替换；朴素 `os.path.exists` 版会把降级情形从"不动"变成"强制判海"，必须避免。**

**变体 B 的测试影响（必须同步更新，否则会红）**

| 测试 | 现在怎么用 | 替换后 |
|---|---|---|
| `tests/test_subarea_stats.py:168` | `sim.map_mgr._load_land_orig.return_value = None` | 该 stub 失效 ⇒ 必须改成 `_land_ok=False` / stub `has_land_mask.return_value=False` |
| `tests/test_landfall_view_invariance.py:28`、`test_pan_reuse.py:28`、`test_seam_render.py:82` | 各自**定义** `_load_land_orig` 桩方法 | 若被测代码改调 `has_land_mask()`，这些桩也要补同名方法 |

**⚠ 变体 A 没有这个测试影响**（不动任何调用方 ⇒ 既有 stub 继续生效）—— 这是我推荐 A 的第三个理由。

**回退（两个变体通用）**

```bash
# 运行时开关（无需改代码）
TSIM_MAP_LAND_RGBA_TRANSIENT=0 python main.py
# 整文件回退（务必用改动前新备份的那份，不要用 _backup/map_mgr.py —— 它是本报告 alpha 优化之前的）
cp output/exploration/_backup/map_mgr_v2.py app/map_mgr.py
```

**验收判据（照 alpha 那次的"逐字节等价"标准，全部可脚本化）**

1. `cmp_map_render.py` 的 **14/14 项全等**（6 视角像素 sha + 两张 alpha 表 sha + `is_land_at_geo`/`is_land_at_screen` 网格 + 几何）；
2. 新增一条：改后 `_land_px is None`（已释放）且 `_land_ok is True`，而 `mm._load_land_orig() is not None`（占位符路径生效）；
3. 新增一条：在改后实例上再次调用 `is_land_at_geo` 全网格，与改前结果**逐点相同**（证明释放不影响查询）；
4. 确定性指标：常驻块合计 **534.41 MB → 311.95 MB**（用 §3.3 的同一个 `get_pitch()×h` 清单复算）、`pygame.image.load` 次数本进程内仍为 **2**（不因释放而增加；若某路径导致第 3 次加载即为回归）；
5. 回归：`test_map_resize` / `test_render_geom` / `test_ocean_area` / `test_landfall_view_invariance` 单文件过（另加变体 B 涉及的 `test_subarea_stats`）。

### 11.2 #1 懒加载：候选方案 + 取舍分析（Lead 暂不批准，留待环境恢复后定）

**现状（实测代码）**：`app/ty_sim.py:212-217`

```python
        self.map_mgr.update_land_mask()      # :212  仅置 _land_lazy=True，不建掩码
        # 陆地掩码(大 PNG 解码 + alpha 查表, 实测 ~1.1-1.4s)在构造/splash 阶段
        # 一次性构建: 否则窗口出现后第一帧 update 的陆地状态同步才触发, 造成可见卡顿
        self.map_mgr._ensure_land_mask()     # :215  ★ 强制解码 land.png
        # 经纬度掩码查表同样在 splash 阶段预构建(~0.3s), 避免首帧陆地判定卡顿
        self.map_mgr.is_land_at_geo(0.0, 0.0)  # :217 ★ 强制建 55.62 MB 表
```

`map_mgr.py` 自身**已经**实现了懒加载（`:583-605` 只置 `_land_lazy=True`、`:614-632` 由首次 `is_land_at_*` 触发），是这两行**显式**把它抵消掉了，且注释说明这是**有意**的取舍。

**候选改法**：删掉 `:215`/`:217`（或加开关包起来），把 land 解码推到首次 `is_land_at_*`。

**取舍（必须写清，否则容易误判收益）**

| 维度 | 分析 |
|---|---|
| **常驻** | **不变**。`is_land_at_geo` 由 `app/view_state.py:100`（`sync_land_state`，每次视图更新都会走到）**必然触发** ⇒ 只要用户看了地图/台风，land.png 照样解码、照样常驻 222.47 MB。**懒加载不会降低稳态内存。** |
| **启动峰值** | 只把 land 的 222.47 MB 从 splash 挪到首帧。若 splash 阶段同时还没解码 map，则峰值窗口变窄（可能少一次叠加），但**最终峰值量级不变**。 |
| **首帧体验** | 明确变差：注释写"否则窗口出现后第一帧 update 才触发, 造成可见卡顿"，实测该段 ~1.1–1.4 s（`13_perf_startup.md`）。 |
| **真正能省的场景** | 只构造 `TySim` 但从不显示地图的路径（测试、批处理脚本）——对 `test_paint.py` 这类 6 次构造的用例收益明显。 |
| **与 #2 的关系** | 若先做 #2（释放 land RGBA），稳态已降到 311.95 MB；此时 #1 的价值进一步下降（省的是"启动期一次 222 MB 的短暂停留"）。**建议顺序：#2 先做，#1 视环境恢复后的内存实况再定。** |
| **风险** | 改的是**启动路径**（用户第一眼看到的东西）；且需要改 `app/ty_sim.py`（范围外，且属 app-sim 的写作用域）。**在无备份 + bash 失效期不动**——与 Lead 的判断一致。 |

**若最终要做，最小改造**：保留 `:212`，把 `:215`/`:217` 换成受开关控制的调用，并把开关默认值设为"保持 eager"，例如
`if os.environ.get('TSIM_MAP_LAZY_LAND', '0') not in ('1','true','on'): <原两行>`——这样默认零行为变化，可随时用户侧试跑。**验收判据**：启动到首帧的 `PeakWorkingSetSize`（需独立进程）、首帧时刻的墙钟（仅作区间）、以及 `is_land_at_geo` 在首帧后与改前**逐点一致**。

---

## 12. #2「变体 A」实施记录（2026-10-02，已完成）

**状态：已实施 + 全部验收通过。** 改动只落在 `app/map_mgr.py` 一个文件里，**4 处外部调用方一行未动**。

### 12.1 实际改动清单

| # | 位置（改后行号） | 改动 |
|---|---|---|
| 1 | `:38-43` | 新增开关 `LAND_RGBA_TRANSIENT = _flag('MAP_LAND_RGBA_TRANSIENT', True)` |
| 2 | `:498-506` | `__init__` 新增 `_land_ok`（曾成功拿到像素）/ `_land_token`（1×1 占位，懒建） |
| 3 | `:537-560` | 新增 `_land_pixels()` —— 真像素访问器（代码体搬自旧 `_load_land_orig`，多加 `_land_ok=True`） |
| 4 | `:562-578` | `_load_land_orig()` 改为三态返回：真像素 / 1×1 占位 / `None`，**外部契约（`is None` ⇔ 不可用）不变** |
| 5 | `:580` | `_rebuild_land_and_overlay()`：`land = self._land_pixels()` |
| 6 | `:665-668` | `_ensure_land_mask()` 前置探测：`_load_land_orig()` → **`_land_pixels()`**（关键，见 12.2-②） |
| 7 | `:690-714` | `is_land_at_geo()`：**先查 `_land_geo_alpha` 缓存**，建表成功后释放 `self._land_orig = None` |

**sha256**：改前 `05df5172bcd8cad3cd8ce6e5d1d1981f23163b2be9dce778c51657bae5b4f967`
→ 改后 `f03340cebb286526664cfec47a360ba4c85880b726c82c5f00209a613d2387bf`

**新增回退点**：`output/exploration/_backup/map_mgr_v2.py`（= 改前那份，`05df5172…`）。

### 12.2 ⚠ 与 §11.1 设计的两处偏离（都是被测试/正确性逼出来的，且都是改进）

**① 像素属性保持名为 `_land_orig`，不改叫 `_land_px`。**
`tests/test_landfall_view_invariance.py:173-186` 用 `object.__new__(MapManager)`（**跳过 `__init__`**）构造，然后直接 `mgr._land_orig = surf` 注入一个 36×18 合成掩码，并在**同一实例上多次**调用 `is_land_at_geo`。`tests/` 不在本次写作用域内 ⇒ 改名会让那个真实采样测试直接失效。
⇒ 保留 `_land_orig` 作为像素持有者，只新增 `_land_ok`/`_land_token`。**副作用是好的**：该测试顺带验证了"合成掩码在多次查询之间保持一致"。

**② `is_land_at_geo` 必须"先查缓存表、再碰像素"；`_ensure_land_mask` 必须用 `_land_pixels()`。**
这两条不写会静默退化，记录在案以免后人改回去：

- `is_land_at_geo` 若沿用旧顺序（开头就 `land = self._load_land_orig()`），释放后**第二次**调用会重新解码 222.47 MB；在测试里更严重 —— 会覆盖掉注入的合成掩码 → **`test_landfall_view_invariance` 变红**。改后顺序：`ab` 已缓存时**完全不触碰像素**。
- `_ensure_land_mask()` 里那行若用 `_load_land_orig()`，在"曾解码 + 已释放"时会返回 **1×1 占位**并把它存进 `_land_orig`，随后 `_rebuild_land_and_overlay()` 拿占位当像素 ⇒ `land_img` 被建成 1×1，**陆地渲染全废**。故必须用 `_land_pixels()`。

### 12.3 验收结果（全部**实测**，判据均为确定性量）

| # | 判据（Lead 指定） | 结果 |
|---|---|---|
| 1 | **逐字等价性**：4 处 `if … is None: return` 分支走向与旧代码完全相同（**含"文件存在但解码失败"降级情形**） | ✅ **PASS**，4 个场景 × 4 类观测量全等（见 12.4） |
| 2 | **常驻内存** 534.41 → 311.95 MB | ✅ **实测 560,361,600 B = 534.40 MB → 327,081,600 B = 311.93 MB（−222.47 MB，−41.6%）**；RSS 617.6 → 395.1 MB |
| 3 | **峰值实测**（§7.3①，独立进程） | ⚠ **两臂均为 840.1 MB，峰值未下降** —— 修正 §11.1 的"峰值额外收益"预期，见 12.5 |
| 4 | 回归 `test_map_resize` / `test_ocean_area` / `test_landfall_view_invariance` | ✅ 4 / 4 / 8 passed（另加 `test_render_geom` 16 passed） |
| 5 | 额外：另 3 个 `_load_land_orig` 桩位点 | ✅ `test_subarea_stats` 17 / `test_pan_reuse` 8 / `test_seam_render` 8 passed ⇒ **回归合计 65 passed / 0 failed** |
| 6 | 额外：**像素等价**（`cmp_map_render` 变体，pre-variant-A vs after） | ✅ 6 个视角像素 sha **全等**、两张 alpha 表 sha 全等、`geo_grid`/`screen_grid` sha 全等（`geo_true=842`、`screen_true=218` 相等） |
| 7 | 额外：**释放后再重建掩码**不得退化成 1×1 | ✅ 清空 `_land_alpha_bytes` 后重跑 `_ensure_land_mask()` → `land_img=(640,384)`、`alpha_len=245760` |
| 8 | 额外：**开关关闭 = 完全回滚** | ✅ 常驻回到 560,361,600 B = **534.40 MB**（与改前逐字节相同） |

命令（本机 bash 会吞 `env VAR=` 前缀与 `$VAR`，故一律写全路径；`probe_map_mem.py` 自己输出 UTF-8，**不要再接 iconv**）：

```bash
cd /mnt/g/气象/Typhoon-sim
P=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe
$P 'G:\tmp\probe_map_mem.py' app.map_mgr_v2 2560 1540   # BEFORE 臂：534.40 MB / peak 840.1
$P 'G:\tmp\probe_map_mem.py' app.map_mgr    2560 1540   # AFTER  臂：311.93 MB / peak 840.1
$P 'G:\tmp\probe_land_equiv.py'                          # 逐字等价性 4 场景
$P 'G:\tmp\probe_land_rebuild.py'                        # 释放后重建 + 开关关闭
$P 'G:\tmp\cmp_map_render_w4b.py'                        # 像素/网格 sha 等价
$P 'G:\tmp\run_probe_flagoff.py'                         # 开关关闭的常驻字节（端到端回滚）
```

### 12.4 逐字等价性明细（Lead 指定判据 1）

对每个场景比较 OLD(`_backup/map_mgr_v2.py`) 与 NEW(`app/map_mgr.py`)：
`_load_land_orig() is None`（**4 处外部调用方唯一关心的判据**）、`land_img` 尺寸、
`_land_alpha_bytes` 长度、以及 **56 点经纬网格**的 `is_land_at_geo` 布尔结果。

| 场景 | OLD | NEW | 判定 |
|---|---|---|---|
| S1 `land.png` 正常（生产常态） | `is None=False`；`land_img=(640,384)`；`alpha=245760`；网格 56 点 | 同左 | ✅ 全等；建表后常驻 `_land_orig`：OLD 222.47 MB / **NEW 0.00 MB**（唯一有意差异） |
| S2 无任何掩码文件 | `is None=True`；`land_img=None`；`alpha=None` | 同左 | ✅ 全等 |
| S3 **文件存在但解码失败（降级）** | `is None=True`；`land_img=None`；`alpha=None` | 同左 | ✅ 全等 |
| S4 NEW + 开关关闭 | — | `_land_orig=222.47 MB`、`is None=False` | ✅ 与 OLD **逐项含常驻字节**完全相等 |

> 等价性是**结构性**的：`_load_land_orig()` 返回 `None` 当且仅当"既无驻留像素、`_land_ok` 为假、
> 且真解码也失败"；旧实现是"既无驻留像素、且真解码也失败"。两者在"解码失败"时都保持
> `_land_ok=False`（每次调用都重试），故分支走向逐字相同。

### 12.5 ⚠ 修正 §11.1 的一处预期：**峰值没有下降**

§11.1 曾预期"#2 之后峰值也有额外收益"。**实测证伪**：两臂 `PeakWorkingSetSize` 均为 **840.1 MB**。
原因清楚：峰值出现在 **`_ensure_land_mask` / `is_land_at_geo` 建表阶段**（进程高水位），而释放发生在
**建表成功之后** ⇒ 释放永远晚于峰值时刻，改不到峰值。**#2 的收益是"稳态常驻 −222.47 MB"，不是峰值。**
若要降峰值只能另行动刀（例如 §11.2 的懒加载，或对 `map.png` 也做同样的释放）。

### 12.6 回滚（⚠ 与 Lead 原先给的那条不同）

**Lead 引用的 `_backup/map_mgr.py`（`c13f5284538832d7…`）是"alpha 优化之前"的版本** —— 回退它会**连带撤掉已验收的 alpha 优化**（那份文件与本次改动前的实际内容不同，已用 `cmp` 确认）。正确回退点：

```bash
# 运行时开关（无需改代码；本机 shell 吞 env 前缀，需在 python 内设置后再 import）
#   os.environ['TSIM_MAP_LAND_RGBA_TRANSIENT'] = '0'   →  实测生效, 常驻回到 534.40 MB
# 整文件回退
cp output/exploration/_backup/map_mgr_v2.py app/map_mgr.py
```

### 12.7 unresolved（本轮新增）

1. **峰值未降**（12.5）。若要降峰值需另行动刀，本轮不做。
2. **释放后若掩码被再次失效重建，会重新解码 222.47 MB**（实测 `probe_land_rebuild.py` [3]：重建正确、像素重新常驻）。当前 `_land_alpha_bytes` 只在 `_rebuild_land_and_overlay` 内被重建，触发点极少（`_ensure_land_mask` 首调 + `_land_pending` 这条死分支），但**未量化其发生频率**，也意味着"222.47 MB 永久释放"并非绝对。
3. **未跑真实 GUI**：全部验收在 headless（`SDL_VIDEODRIVER=dummy`，640×384 与 2560×1540 两个尺寸）完成；像素等价由 6 视角 sha + 两张网格 sha 证明，未做"改前/改后整屏截图"的人工比对，也未在真实窗口下观察一帧。
4. `cmp_map_render_w4b.py` 的"总判定 ❌"是**假阴性**：它的对比项含 `land_shape`（`(10800,5400)` vs `None`），而那正是本次改动本身；其余 15 项全 OK。
