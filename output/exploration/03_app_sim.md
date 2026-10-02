# 03 应用外壳与应用内 SimCore 模拟模式

> 作者：teammate `app-sim` ｜ 只读探索 ｜ 测量机：`C:\Users\Yukioto\AppData\Local\Python\python.exe`（Python 3.14.2 / pygame-ce 2.5.7 / SDL 2.32.10，`SDL_VIDEODRIVER=dummy`，即无头）
> 覆盖范围：`main.py`、`app/ty_sim.py`、`app/ty_sim_mixins/**`、`app/renderer.py`、`app/typhoon_render.py`、`app/render_geom.py`、`app/smcy_icon.py`、`app/*effect.py`、`app/map_mgr.py`、`app/ocean_*.py`、`app/input_*.py`、`app/view_state.py`、`app/window_util.py`、`app/config*.py`、`app/settings.py`、`app/*dialog*.py`、`app/constants/**`、`app/control_panel.py`
> 本文档中所有数字都标注了来源。**实测**＝本轮真的跑出来的；**引用**＝仓库内既有文档/注释说的；**推断**＝只读代码得到。

---

## 一句话定位

`TySim` 是一个 pygame 单窗口、60 FPS 主循环的台风数据可视化工具，它的**第 4 个模式**（`H` 键循环到的 `MODE_SIM`）把独立包 `simulator/` 的 SimCore 数值模拟器嵌进同一张地图上实时驱动并渲染——这一模式的心脏是 2258 行的 `app/ty_sim_mixins/sim_mode_mixin.py`。

---

## 1. 清单与职责

| 文件 | 行数 | 职责 | 关键类 / 函数 |
|---|---|---|---|
| `main.py` | 255 | 入口：DPI、pygame.init、窗口/最大化、**主循环**、崩溃日志 | `_apply_dpi` `_maximize_window` `_draw_splash` `main` |
| `app/ty_sim.py` | 868 | 主控制类：Config/Repo 描述符代理、`update`/`draw` 分发、模式状态、配置保存 | `TySim` `_ConfigProperty` `_RepoProperty` `update` `save_config` `handle_resize` |
| `app/ty_sim_mixins/sim_mode_mixin.py` | **2258** | **模拟模式全部逻辑**：SimCore 桥接、环境场/图层、播放时钟、回放、导出、HUD | `SimModeMixin`（约 60 个 `_sim_*` 方法） |
| `app/ty_sim_mixins/sim_step_control.py` | 281 | 单步（固定 15 min）+ 随机种子骰子 | `TySimStepControlMixin` `_sim_step_once` `_sim_dice_seed` |
| `app/ty_sim_mixins/event_mixin.py` | 492 | 事件总路由：对话框栈 → 洋区编辑 → 拖拽/缩放 → 键盘 → 鼠标 | `TySimEventMixin.handle_event` `_handle_map_pan` `_handle_map_zoom` |
| `app/ty_sim_mixins/keyboard_mixin.py` | 780 | `switch_mode`、功能栏按钮点击路由 `_btn_*` | `switch_mode` `_handle_click` `_btn_sim_layer` |
| `app/ty_sim_mixins/_draw_path_mixin.py` | 1462 | 路径渲染（点阵/渐变线 + 增量缓存）、`_show_future` | `_draw_typhoons` `draw_typhoon` `_render_path_to_surface` |
| `app/ty_sim_mixins/_draw_icon_mixin.py` | 1738 | 图标（simple / SMCY 视频帧）、名字、`draw_typhoon_info` | `draw_typhoon_info` `_draw_simple_icon` `_draw_smcy_frame` `_icon_live_screen` |
| `app/ty_sim_mixins/draw_info_boxes_mixin.py` | 685 | 风季/模拟**共用**信息框 + 实时 ACE 数字 + 模拟预报行 | `_render_info_box` `blit_info_box` `_sim_forecast_line` `_box_alpha` |
| `app/ty_sim_mixins/draw_mixin.py` | 19 | 三个子绘图 mixin 的聚合器（**文件带 UTF-8 BOM**） | `TySimDrawMixin.draw` `_draw_map` |
| `app/ty_sim_mixins/track_mixin.py` | 598 | 镜头跟踪（推镜/高亮/收尾）——**模拟模式不走**（见 §3.2） | `TySimTrackMixin._update_tracking` |
| `app/ty_sim_mixins/utils_mixin.py` | 523 | 坐标转换、颜色分级、toast、报点增删改 | `latlon_to_screen` `show_toast` `get_point_color` |
| `app/renderer.py` | 437 | `Renderer.draw` + **显式场景图层表** `scene_layers`、FPS/坐标 HUD/toast | `Renderer.scene_layers` `_draw_scene` `_layer_drawn` |
| `app/typhoon_render.py` | 169 | 台风屏幕点/地图空间点、跨缝归一化、geo 样条缓存 | `update_screen_points` `_get_geo_spline` |
| `app/render_geom.py` | 213 | 跨缝几何唯一来源：`normalize_chain` / Liang-Barsky 裁剪 / 吸附网格 | `normalize_chain` `clip_segment` `draw_polyline` |
| `app/smcy_icon.py` | 820 | SMCY 图标视频流 LRU、登陆/总结帧、分帧预热迭代器 | `SMCYIconManager` `iter_preload_icon_streams` |
| `app/particle_effect.py` | 328 | **RI / TS 升格特效**（不是风场粒子） | `RIEffect` `TSNoteEffect` `iter_preload_particles` |
| `app/landfall_effect.py` | 232 | 登陆特效（模拟模式被 `not is_sim` 门控掉） | `LandfallEffect` |
| `app/summary_effect.py` | 349 | 月度总结条（同上，模拟模式不画） | `MonthlySummary` |
| `app/map_mgr.py` | 642 | 地图图 `MapView`（cover 缩放、wrap、`geo_to_screen`）+ 陆地掩码 | `MapView` `MapManager` |
| `app/ocean_mgr.py` | 456 | 洋区多边形数据模型（`assets/Area_ocean.json`） | `OceanArea` `OceanManager` |
| `app/ocean_edit.py` | 580 | 洋区顶点编辑模式（第 5 个 md，`H` 退出） | `OceanEditMode` |
| `app/input_ctrl.py` | 105 | 全局快捷键命令表（`H` → `switch_mode` 等） | `InputController.commands` |
| `app/input_handler.py` | 54 | 长按判定（200 ms / 10 px 死区） | `InputHandler.update` |
| `app/input_field.py` | 642 | 文本输入控件（剪贴板、光标、加速删字） | `InputField` |
| `app/view_state.py` | 106 | 视口状态与坐标转换门面、屏幕点刷新 | `ViewState.latlon_to_screen` |
| `app/window_util.py` | 184 | 与操作系统**实际窗口**对账（查本进程 hwnd、挪回屏内） | `actual_client_size` `keep_window_on_screen` `_own_window` |
| `app/config.py` | 507 | `AppConfig` dataclass（127 字段）+ 逐字段反序列化/钳制 | `AppConfig.load` `_coerce` `_serialize_fields` |
| `app/config_store.py` | 176 | 配置落盘唯一入口：备份 + 读回校验 + `RUNTIME_FIELDS` 不落盘 | `ConfigStore.commit` `persisted_fields` |
| `app/settings.py` | 2597 | 主设置对话框（74 项 `_CFG_MIRROR`） | `Settings._flush_fields` `_sync` `_CFG_MIRROR` |
| `app/dialog_base.py` | 286 | 对话框基类（拖拽、暗色面板/遮罩缓存） | `Dialog` `DraggableDialog` |
| `app/dialog_manager.py` | 109 | 对话框集中创建与 `any_active()` | `DialogManager` |
| `app/sim_settings_dialog.py` | 605 | **模拟设置面板**（50 行表格，含集合/精度/环境参数） | `SimSettingsDialog._rows_spec` `_on_control` |
| `app/sim_time_dialog.py` | 239 | 模拟起始时刻（1700-2300）+ 环境步长档 | `SimTimeDialog._apply` |
| `app/sim_mode_dialog.py` | 184 | 12 个气候模态的手动 σ 覆盖 | `SimModeDialog` |
| `app/sim_value_dialog.py` | 150 | 通用单值输入（种子/数据年份/粒子密度） | `SimValueDialog.activate` |
| `app/constants/layout.py` | 100 | 窗口/布局常量、`CONFIG_FILE`、各 `MODE_*` | `CPH=100` `MODE_SIM="sim"` |
| `app/constants/colors.py` / `fonts.py` / `__init__.py` | 149 / 217 / 6 | 配色、字体与 `rt()` 渲染函数 | `rt` `f_s` `f_m` |
| `app/control_panel.py` | 620 | 下方功能栏：模式按钮、速度条、模拟模式 Row2 六按钮、图层弹层 | `ControlPanel.build` `_build_sim_layer_row` `hit_test` |

---

## 2. 数据流 / 调用链

### 2.1 主循环（每帧 60 次）

```
main.py:110 main()
 ├─ 31  _apply_dpi()                              # 必须在 pygame.init() 之前
 ├─ 33  pygame.init(); 35 mixer 32 通道; 39 stop_text_input()
 ├─ 48  _cfg = AppConfig.load(constants.CONFIG_FILE)      # "config.json"，相对路径
 ├─ 111-122 screen = set_mode(cfg.screen_width/height)
 ├─ 126-138 start_maximized → _maximize_window() + set_mode(客户区)
 ├─ 150-157 window_util.actual_client_size / keep_window_on_screen
 ├─ 164-172 **以 surface 真实尺寸为准**，同步 constants + _cfg
 ├─ 180 sim = TySim(screen, cfg=_cfg, on_load_progress=_on_load)
 │     └─ app/ty_sim.py:165 TySim.__init__
 │         175 _init_attributes()  176 _init_sim_mode()  177 _init_resource_managers()
 │         179 _install_descriptors()  183 ViewState  185 ACEEngine  186 DataRepository
 │         194 repo.load_typhoon_files()（splash 进度回调）195-211 地图 + 陆地掩码
 │         204 Renderer(self)  197 DialogManager(self)  202 InputHandler  203 InputController
 └─ 187 while running:
      188-189  cap = 60 if (距上次交互 < 100ms) else max(0, cfg.fps_cap)
      192      dt = min(clock.tick(cap)/1000, 0.1)        # ★ 单帧最多按 100ms 推进
      193-205  for event in pygame.event.get():
                  QUIT  → sim.ocean_edit.exit()（洋区编辑唯一保存点）; running=False
                  VIDEORESIZE → sim.handle_resize(w,h); last_resize_save=now
                  else  → sim.handle_event(event)
      206-211  sim.update(dt);  sim.draw(screen);  记录 _t_update_ms / _t_draw_ms
      213      sim._tick_warmup()                     # 视觉资源分帧预热
      214      pygame.display.flip()
      215-217  尺寸变更 1s 后 sim.save_config()
```

### 2.2 事件 → 更新 → 绘制（模拟模式分支）

```
sim.handle_event(e)                     app/ty_sim_mixins/event_mixin.py:14
 ├─ 18-27  失焦复位拖拽
 ├─ 30-32  名字行编辑态优先
 ├─ 37-53  脚本 WAIT_USER
 ├─ 56-102 对话框栈优先（bg_rect 命中 / 拖拽无条件路由 / 键盘只给栈顶）
 ├─ 104    input_handler.handle_event(e)（长按计时）
 ├─ 107-110 ocean_edit
 ├─ 117-121 地图平移/缩放
 ├─ 123-128 KEYDOWN：MODE_SIM 先给 _sim_handle_event(e)（:125-127），否则 input_ctrl
 ├─ 129-139 中键 → _sim_spawn_at_screen（cfg.sim_middle_spawn 默认 True）
 └─ 140-167 左键：功能栏 hit_test 优先（仅 MODE_SIM，:157-160）→ 地图左键 _sim_handle_click

sim.update(dt)                          app/ty_sim.py:651
 ├─ 654  _fps = 1/dt（给 FPS HUD）
 ├─ 691-692 dialog_open → _update_dialog_pause()
 ├─ 694-697 MODE_SEASON 分支
 ├─ 699-703 **MODE_SIM 分支：_sim_update(dt) + script_engine.update(dt) 后 return**
 │          ⇒ 不跑 playback_ctrl / season_ctrl / _update_tracking / _ms（镜头跟踪在模拟模式不生效）
 └─ 705-718 其它模式：playback / season / 月度总结 / 跟踪

sim.draw(surface) → TySimDrawMixin.draw (draw_mixin.py:14) → Renderer.draw (renderer.py:65)
 ├─ 68  _draw_scene(surface, hidden) → 遍历 scene_layers()（见 §3.9）
 └─ 70  sim.draw_control_panel(surface); 72 dialog_mgr.draw(); 74-84 FPS/toast/坐标 HUD/图例
```

### 2.3 模拟模式一帧内部

```
_sim_update(dt)                          sim_mode_mixin.py:1614
 ├─ 1618-1621 粒子数变了 → Particles(want) 重建
 ├─ 1624-1625 if sim_playing and not dialog_open:
 │    1626 _sim_advance(dt) → 物理 + 显示钟（§3.3）
 │    1628 _sim_rewind_tick()（按 interval=2 存快照）
 │    1629 _sim_track_rate(hpf)（墙钟 3s 窗口测速）
 │    1633-1634 只有 'wind' 可见才 sim_particles.step()
 │    1636-1644 逐台风推进图标自旋角 ict.sa
 └─ 1645-1651 else: _sim_rate_t_prev = None（作废采样点）

_sim_render(surface)                     sim_mode_mixin.py:1818（由 renderer 的 'sim_field' 层调用）
 ├─ 1822 import simulator.simcore.render as R
 ├─ 1826-1836 fx/fy = app 地图视图的点映射
 ├─ 1838-1839 grid / grid_pad = _sim_grid_map*（格点层用**仿射**映射，见 §3.6）
 ├─ 1842 disp = _sim_display_sim()（显示钟插值视图）
 ├─ 1854-1861 layers / degrade（sim_auto_degrade 恒 False → degrade 恒 0，见 §6）
 ├─ 1862-1879 **计时块只包住 R.render_map(...)**
 ├─ 1890-1916 'track' 门控：逐台风 _sim_sync_path_ty → draw_typhoon（app 层路径）
 ├─ 1924-1954 同一门控：draw_typhoon_info（图标+名字）+ blit_info_box（仅选中那只）
 ├─ 1955 _sim_draw_hud（圆环钟 + 数据徽标 + 左下统计框 + 流场图例）
 └─ 1956 _sim_draw_probe（悬停取值）
```

---

## 3. 核心机制详解

### 3.1 模式切换与初始化（`H` 键 / 面板按钮）

- `H` → `input_ctrl.py:20` 命令表 → `sim.switch_mode`（`keyboard_mixin.py:468`）。顺序是 **正常 → 风季 → 编辑 → 模拟 → 正常**（`:487-498`）；进入模拟在 `:491-496`：
  ```python
  elif self.md == self.MODE_EDIT:
      self.md = self.MODE_SIM
      self.sim_playing = False
      self._sim_sync_play_text()
      self._sim_reset(msg=False)          # ← 同步、阻塞主线程
  ```
- `_init_sim_mode`（`sim_mode_mixin.py:176-253`）在 `TySim.__init__` 阶段就调用（`ty_sim.py:176`），做的是**配置恢复**而不是建场：图层初值 `sim_layers={'track'}`（`:179`）、种子兜底（`:186-187`）、起始时刻归一化 `_coerce_start`（`:200-212`）、`snap_env_step`（`:213-216`）、显示开关镜像 `_SIM_DISPLAY_KEYS`（`:229-231`）、把 `rt` 注入内核文本渲染（`:248-253`）。
- `_sim_reset`（`:827-890`）才真正建场：
  ```python
  _prec = str(getattr(self.cfg, 'sim_precision', DEFAULT_PRECISION))     # :834
  self.sim_v4 = V.init({ 'start': self._sim_start_dt,
                         'envStepH': self.sim_env_step_h, 'seed': self.sim_seed,
                         'params': {'shLat':31.,'shLon':146.,'shAmp':10.,'random':.55,
                                    'genesis': …, 'dataMode': …, 'dataYear': …,
                                    'dataClim': …, 'dataDayCycle':1.0,
                                    'modeOverride': …, 'forecast': bool(cfg.sim_ensemble),
                                    'precision': _prec, **cfg.sim_env_params} })  # :835-857
  V.env_update(self.sim_v4)                                              # :858
  self._sim_rewind_init()                                                # :861
  ```
  `'precision'`/`sim_env_params` **必须在内层 `params` 字典里**，否则被 `V.init` 忽略（注释 `:850-855`）。
- **实测（本轮）**：headless 构造 `TySim` 后 `sim._sim_reset(msg=False)` 首次 **16.87 s / 16.14 s / 7.53 s / 6.68 s**（4 次不同进程，OS 文件缓存影响明显），第二次同进程 **0.98 s**。⇒ 用户按 `H` 进模拟模式或按 `R` 重置时，主线程会冻结数秒到十几秒（`switch_mode` 是同步调用，没有分帧/异步）。
- 重置时会**清理上一代台风对象**及其按对象做键的缓存（`_sim_drop_ty_caches` `:793-825`、`:876-884`），避免 `_path_cache_full` Surface 泄漏。
- **记忆不丢**（2026-09-18 review 修复）：本轮实测 `_sim_set_display('_show_barbs', False)` + `_particle_count=1234` + `_sim_set_param('wtAmp',10)` + `('shTilt',1)` 之后 `_sim_reset()`，四者全部保留（`sim_v4` 与 `cfg` 都对）。

### 3.2 与 `simulator/` 包的桥接方式（架构要点）

- **`app/` 里没有任何模块级 `import simulator`**（本轮 grep 确认：`^from simulator|^import simulator` 在 `app/` 下 0 命中）。内核一律在**函数内延迟 import**：`sim_mode_mixin.py` 有 **32 处** `from simulator...`（如 `:214 / :250 / :623 / :828 / :862 / :1108 / :1440 / :1822`），`sim_step_control.py:83` 1 处，`sim_settings_dialog.py` / `sim_time_dialog.py` 各 2 处。
- 好处：`main.py` 的启动不背 numpy/xarray/climate 数据装载；内核缺席时降级而不是崩（`_init_sim_mode` 的 try/except `:213-221`、`_sim_step_control_core` 返回 None 时降级 `sim_step_control.py:80-87`）。
- 反向注入：内核不依赖 app —— 文本渲染由 app 注入 `render.set_label_fn(lambda t,c: rt(f_s,t,c))`（`:248-253`、`:1752-1761`）。
- 渲染数据跨越边界时用**浅拷贝视图**而不是改物理状态：`_sim_display_sim`（`:1541-1591`）。

### 3.3 时间模型：物理钟 / 显示钟 / 播放速度

**内核侧**：全局域 `0-360°E / 90°S-90°N`，环境网格 **0.5°（720×360）**、云网格 1.0°（360×180）（`simulator/simcore/core.py:53-65`，本轮实测 `ENVNX×ENVNY = 720×360`）。`C['DT'] = 10/60` h（10 min）**仍在**（`core.py:57`），但**实际积分步长 = 环境步长**：

```python
def step_hours(sim):                                     # core.py:3368-3378
    return float(snap_env_step(sim.get('envStepH', DEFAULT_ENV_STEP)))
ENV_STEPS = (1.0, 3.0, 6.0)                              # core.py:707，上限锁 6h
```
`snap_env_step` 实测：`0.25→1.0, 2.0→1.0, 12.0→6.0, 999→6.0`。⇒ 任务描述里的"10 分钟积分步"是**历史口径**；`C['DT']` 在当前构建里已不是主积分步长（本轮 grep `C['DT']` 在 `simulator/` 下无生产引用；见 §7 unresolved-1）。

**app 侧** `_sim_advance`（`sim_mode_mixin.py:1425-1467`）——**显示钟是主时钟，物理永远领先它一步**：

```
step_h  = V.step_hours(sim)                     # :1442
inner   = precision_spec(...)[1]                # :1443
若未初始化: disp = max(0, t_phys - step_h)      # :1445-1447
若未播放:   return 0                            # :1448-1449
d_h   = sp * dt * SIM_HOURS_PER_SEC_AT_1X       # :1450（1× = 6 模拟小时/实时秒，:50）
target = disp + d_h + step_h                    # :1452
budget_units = max(1, int(SIM_FRAME_BUDGET_MS / _sim_unit_ms(sim, step_h)))   # :1456（200ms，:55）
while t_phys < target-1e-9 and done < SIM_MAX_UNITS_PER_FRAME:                # :1457（16，:58）
    if done and done >= budget_units: break                                   # :1458
    V.sim_step(sim, step_h, max_inner=1)                                      # :1460
disp = min(max(disp + d_h, 0), max(0, t_phys - step_h))                       # :1465-1466
```
不变量 `disp ≤ max(0, t_phys - step_h)`，且 disp 单调不回退。**本轮实测**（`_sim_update` 驱动、`sp∈{0.5,1,4}`、各 60 帧）：**违例 0 条**。
第二条不变量（"播放时每秒推进多少模拟小时"）用**墙钟**测（`_sim_track_rate` `:1469-1516`，2026 轮次 151 把被钳的 `dt` 换成 `time.perf_counter()` 差值，`:1499-1509`）：3 s 窗口 + 丢弃 >2 s 空档（`:1506`）。`_sim_effective_x`（`:1525-1538`）把实测值按 `SIM_HOURS_PER_SEC_AT_1X` 换算并**上限钳到请求值**；红字由 `control_panel.py:530-545` 画（`8× → 有效 0.58×` 那种）。

**单步（`.`）**：固定 `SIM_STEP_ONCE_H = 0.25` h（`sim_step_control.py:53`），**保持暂停**，优先走内核私有入口 `V._sim_step_core(sim, dt, hold, force_env=False)`（`:178-201`），并沿 `_sim_advance` 的同一不变式同步显示钟（`:137-158`）。为什么要私有入口：公开 `sim_step` 会把 `stepCostMs` 写成 24 倍假值、高档位还会连做 inner 次强制环境重建（模块 docstring `:16-20`）。
**实测**：`_sim_step_once()` → `t 0.000 → 0.250`（正好 15 min），`sim_playing` 保持 False。
**已知偏差（实测复现）**：连按 24 次单步（共 6 h）→ `t=6.00`、该台风 **7 个报点**；直接播放一步 6 h → `t=6.00`、**2 个报点**。原因是强度随机强迫 / 路径 OU / 涡旋 OU 都是"每步一次、不按 dt 归一"（`sim_step_control.py:20-23` 列了全表）。⇒ **同样 6 个模拟小时，"单步走"与"播放走"的抖动统计不可比**。

**骰子（`;`）**：`_sim_dice_seed`（`:220-261`）取 32 位、与上次撞了就 +1，经 `_sim_set_seed` 落 cfg 后立刻 `_sim_reset(msg=False)`。实测：返回 3891227857 / 1422070648，`cfg.sim_seed` 与 `sim.sim_seed` 同步。

**回放（`,`）**：`simulator/simcore/rewind.py`（纯标准库，959 行）的 `RewindStore`，app 侧 `interval=2 / max_snapshots=240 / max_bytes=96 MiB / min_keep=4`（`:615-617`、`:628-634`）。恢复走 `on_restore` 集中失效：清 `sim_path_tys` / `sim_icon_tys` / `sim_sel_tc_id`、清 `render._flow_last` 与 `_flow_cache`、清 `core._OCEAN_UV`、把显示钟跟到恢复点（`:649-697`）。
**实测**：12 帧 → 1 份快照，`, → False`（需要 ≥2 份）；跑到 `indices=[1,2,3,4]`、`cursor=4` 后 `, → True`，`t 48.0 → 36.0`，恢复后 `indices=[1,2,3]`（线性时间轴 `_drop_after`），三个缓存 dict 全空，`_sim_disp_h = 30.0 = 36 - 6` ✔。**没有"前进"键是有意的**（`:732-742`）。

### 3.4 图层系统（`1`-`9` 键 + 功能栏弹层）

```python
SIM_LAYERS = [('pressure','气压','1'), ('wind','风场','2'), ('sst','海温','3'),
              ('ohc','热含量','4'), ('track','路径','5'), ('ridge','副高','6'),
              ('shear','风切','7'), ('flow','环流','8'), ('current','洋流','9')]   # :30-40
ENV_LAYERS = ('sst', 'ohc', 'shear')      # 三选一互斥，:1039
```
⚠ **任务描述说"六个图层（1-6）"，代码里是九个（1-9）**；`shear/flow/current` 是"去云图 + 实时海洋平流/风切/环流层"那一轮加的（`git log 4e4148f`）。

三条入口，语义不同（这是被反复修过的区域）：
| 入口 | 函数 | 语义 | 实测 |
|---|---|---|---|
| 数字键 `1`-`9` | `_sim_set_layer` `:406-419` | **单选**：`sim_layers = {lid} ∪ {'track'}` | `1→['pressure','track']`，`6→['ridge','track']`，`9→['current','track']` |
| 功能栏 `layer_*` / 面板 cycle | `_sim_toggle_layer` `:421-453` | **叠加**开关；环境层委托给互斥分支 | `pressure` 开→关、`wind` 叠加 → `['pressure','track','wind']` |
| 面板 `envlay_*` | `_sim_set_env_layer` `:1050-1073` | 环境层**再点即关 / 点别的即换** | `sst→ohc→shear→ohc` 始终只有 1 个环境层 ✔ |

不变量"`sim_layers` 恒含 `'track'`"（`:410-418`）本轮 9 个层逐一切换都成立；`_sim_layer_short` 在 ≥2 个非路径层时报 `'4层'`（`:1231-1243`，实测吻合）。

`render_map` 的分发（`simulator/simcore/render.py:1455-1575`）：
| 层 | 数据源 | 画法/着色 | 本轮实测（1280×800 无头，冷/热） |
|---|---|---|---|
| `pressure` | `env['pDisp']`（否则 `pTot`，`render.py:717`） | 14 条等值线 976–1028 hPa + 高低压「低/高」标注；几何按内容校验和缓存 | 187.6 → **91.7 ms**，px 2444（**热态仍 ~92 ms，等于没有表面缓存**） |
| `wind` | `env` 风场 | `_wind_mode`：`barbs` 风羽（`:1105`）/ `stream` 流线（`:1032`）/ `particles` 粒子（`Particles` `:1182-1226`） | 7.1 ms（barbs） |
| `sst` | `env['sst']` | 0–33 °C，1 °C 一档离散 LUT（`:558-562`），alpha 210 | **2589 → 84 ms**，px 831100 |
| `ohc` | `ocn['ohc_kj']`（否则 `env['ohc']`，`:544-547`） | 0–220 kJ/cm²，10 一档（`:564-567`），alpha 190 | 432 → 69 ms |
| `shear` | `env['shear']` | 0–60 kt 线性（`:569-574`）；`_shear_fill=False` 时走 CIMSS 等值线（`:1503-1504`） | 388 → 67 ms |
| `ridge` | `env['pDisp']`（否则 `pEnv`） | 自适应阈值 `max(1012, p88)` 的半透明橙填充 + 描边 + 阈值标注（`render.py:1381-1420`） | 98.8 → **96.6 ms** |
| `flow` | 风场流线（可见窗口内撒种） | `_build_streamlines(step_h=8, steps=5, min_speed=1.5)`；`_flow_cache` + 150 ms 节流 | 22.6 ms，px 12852 |
| `current` | `ocn['u']/['v']`（Ekman 漂流） | 同流线，`step_h=240, min_speed=0.05`（`:1093-1096`）；`'u' not in ocn` 时**静默 return**（`:1085-1087`） | t=0 7155 px / 61.5 ms，t=204 6058 px / 57.6 ms |
| （`cloud`） | `cld` 场 | 代码保留（`render.py:1472-1478`），但 **app 侧已从 `SIM_LAYERS` 移除**（`:29` 注释"已去掉云图图层"），`sim_cloud_mode` 只作为 `mode=` 参数传下去（`:1865`） | 不渲染 |

### 3.5 六／九个图层的**渲染路径分叉**（关键）

格点色阶层（sst/ohc/shear/rh/ridge）**不能**用 app 的点映射：`geo_to_screen` 会按"离屏幕中心最近的副本"逐点翻面，对整张 0.5° 场是灾难（`fx(0)` 与 `fx(360)` 落到同一像素 → 贴图宽度 0，整层永不显示；风羽还会闪烁）。所以 `_sim_grid_map`（`:1781-1816`）只取投影的**仿射部分**构造 `render.GridMap`：

```python
x_aff = int(((lon - mv.lon_min) * mv._scale_x) * k) - vx + ox       # :1799-1800
y_aff = int(((mv.lat_max - lat) * mv._scale_y) * k) - vy + oy       # :1802-1803
R.GridMap(C['LON0'], C['LON1'], la0, la1, x_aff(LON0), x_aff(LON1),
          y_aff(la1), y_aff(la0), wrap=mv.wrap_px(), clip=(0,0,W,map_height))  # :1810-1813
```
`_sim_grid_map_pad` 额外把纬度域扩到 `LAYER_LAT0/1 = ±90`（`render.py:440`），因为数组已被 `_pads_poles` 补到两极（`:1776-1779`）。点类对象（台风/风圈/预报圈）仍用 `fx/fy`（`:1826-1836`）。

### 3.6 数据环境开关（`D` 键）

```
D  → _sim_handle_event :1688-1690 → _sim_toggle_data_mode :777-790
      sim_data_mode 取反 → cfg.sim_data_mode → **_sim_reset(msg=False)**（环境场来源变了）
      提示文案按 sim['dataState'] 映射（'loaded'/'partial'/'fallback'/'off'）
```
内核写状态（`core.py:1984-2020`）：`dataState` ∈ `off / fallback / partial / loaded`；`dataSrc` ∈ `瞬时 / 瞬时·部分 / 月均 / -`；失败原因写 `dataWarn`。HUD 徽标在 `_sim_draw_hud`（`:2223-2248`）读**真实状态**而不是设置，并显示 `模式: 数据环境·部分 [D]` + `⚠ dataWarn`。
**实测**：`sim_data_mode=True, dataState='loaded', dataSrc='瞬时·部分'` → `D` → `False / 'off' / '-' / params.dataMode=False`；再按一次回到开。

### 3.7 12 成员集合预报（`sim_ensemble`）

- 内核：`forecast_members(sim, tc0, hours=72, n_mem=12, dt_e=6, …)`（`core.py:4751`）、`run_ensemble`（`:4857-4934`），每 **6 模拟小时**签发一次（`if sim['ensemble'] and sim['t'] - sim['ensemble']['hour'] < 5.9`，`:4858`）。
- 代价：每次签发要 `forecast_env_series` 重建环境场 **13 次**（`:4872` 附近、`core.py:4816` 注释自承"13 次 env_update"），另有 12 个成员 × 72 h 路径积分。
- app 侧**默认关闭**：`cfg.sim_ensemble = False`（`app/config.py:155-161`），`_sim_reset` 显式传 `'forecast': bool(cfg.sim_ensemble)`（`:846-848`），面板有开关 `('_ensemble','集合预报(实时,慢):','check')`（`app/sim_settings_dialog.py:222`，映射 `:49`，改动后立即 `_sim_reset` `:116-121`）。
- 打开"预报路径"但集合没开时给提示而不擅自打开（`:522-528`）。
- 预报结果的行显示：`fcPeak / fcLand` 挂到 Typhoon 上（`:375-376`），信息框多一行 `预巅峰:xxkt(p10~p90) +Nh  预登陆:…`（`draw_info_boxes_mixin.py:330-354`，插入点 `:274-277`），框高 +1 行（`:1940-1942`）。
- ⚠ **`tools/模拟模式帧率实测报告.md:278-282` 说"集合预报默认 True 且面板没有开关"——该结论对本构建已过期**（见 §6-M4）。

### 3.8 台风本体：从内核 `tc` 映射到 `Typhoon`，复用其它模式的渲染管线

`_sim_sync_path_ty`（`:255-404`）：首次为每个 `tc['id']` 建一个真 `Typhoon`，**必须绑定 `ty.sim = self`**（`:292`）：

> `typhoon_render.py:96` 的样条门是 `if self.sim and self.sim.cfg.smooth_path:`，不绑定则 `smooth_screen_points` 恒空 ⇒ ①模拟模式平滑路径 100% 不生效；②`_draw_path_mixin.py:947-950` 的"开关开着但样条为空⇒判过期"守卫每帧为真 ⇒ 平移捷径被否、每帧全量重投影（注释 `:277-291`）。

其它映射契约：
- 报点等级/颜色走**风季口径** `get_point_color`/`get_strength_category`（`:329-331`），而不是内核 `grade_kt`（8 档、不看 phase）；注释 `:315-326` 说明旧口径导致同风速两种模式图标/颜色/等级串不同，且 `_ace_eligible` 只认 TS/TY/ST/HU ⇒ 旧口径下模拟台风 ACE 几乎从不生效。
- `pts` 键与 `typhoon_data.add_point` 对齐（`:345-353`）：`t/la/lo/w/p/st/cat/color/color_dim/official/ace`；**实测 `sorted(pts[0].keys())` 与之一致**。
- `points_time` 单位与其它模式一致：`t/12.0`（`:364`，0.5 = 6 模拟小时）。
- 增量 append 而不是整表重建，以命中路径增量缓存（`:266-269`）；同时手动清 `_cached_max_wind_color / _cached_peaks / _cached_name_colors`（`:354-360`）。
- `ci` = 显示钟之前的最后一报（`:366-371`）；`at = disp_h/12`（`:372`，**未钳制**）；`track_total_n = len(track)`（`:403`，修复"图标 alpha 恒 0"）。
- `ty.v.ipos` = 相邻两报之间按显示钟插值的位置（`:383-395`）。
- **实测**：`disp_h = 0/3/6/12/24/47` → `ci = 0/0/1/2/4/6`（报点 t=[0,6,…,36]），`at = 0/0.25/0.5/1/2/3.92`，`pts` 恒为 7（**不截断**，2026 轮次 155 删掉了每帧截断）；`disp_h=200` → `ci=6`、`at=16.67`（超出最后报点仍在增长）。

绘制复用（`_sim_render` 尾部）：
```python
if 'track' in self.sim_layers:                       # :1890
    for tc in disp['tcs']:
        ty = self._sim_sync_path_ty(tc)              # :1895
        self.draw_typhoon(surface, ty, highlight=(tc['id'] == self.sim_sel_tc_id))  # :1913
if 'track' in self.sim_layers:                       # :1924（同一门控，防"幽灵台风"）
    ... self.draw_typhoon_info(surface, ty, with_name=… )                     # :1934
    ... self.blit_info_box(surface, _sel_ty, SEASON_INFO_BOX_WIDTH, _h,
                           SEASON_INFO_BOX_START_X, SEASON_INFO_BOX_START_Y, alpha)  # :1947-1952
```
`_show_future` 对 `MODE_SIM` **一律 False**（`_draw_path_mixin.py:513-531`）——模拟没有"未来段"，物理领先那几步不该画；路径只画到 `cur_idx`，所以删掉截断后画面不变，但活线段/类别过渡/图标预载/信息框起止四个前置条件一起恢复。
**实测（1280×800、1 只 20 kt 扰动、`{'track'}`）**：`R.render_map` 自身 **0.02 ms**，`draw_typhoon` 0.08 ms，**`draw_typhoon_info` 13.23 ms**，`_sim_draw_hud` 0.37 ms，整层 `_sim_render` 中位 **24.85 ms**。⇒ 模拟模式里真正把台风画上屏的是 **app 层管线**，不是 simcore 的 `render_map`。

### 3.9 渲染层序（"显式图层表"）

`git log` 里 `4118a81 渲染场景改显式图层表(Step 5): z 序数据化 + 一次性钩子`。实现是 `Renderer.scene_layers(hidden)`（`renderer.py:202-298`）：返回 `(名字, 可见性判定, 绘制函数)` 三元组**有序元组，顺序即 z 序**，由 `_draw_scene`（`:306-313`）遍历，并对每个可见层回调 `_layer_drawn(name, ms)`（`:300-304`）供性能面板/测试覆写。

| # | 层 | 可见性（模拟模式下） | 绘制 |
|---|---|---|---|
| 1 | `map` | 恒真 | `sim._draw_map` |
| 2 | `graticule` | `cfg.show_graticule`（默认 False） | `_draw_graticule`；注释 `:264-268` 说明必须排在 `sim_field` **之前** |
| 3 | `sim_field` | `is_sim and not hidden` | `sim._sim_render`（**模拟模式全部内容一次画完**） |
| 4 | `ocean_areas` | `not is_sim and …` | `_draw_ocean_areas` |
| 5 | `ocean_edit` | `not is_sim and oe.active` | `oe.draw` |
| 6 | `clock` | `not is_sim and not hidden` | 风季左上 / 正常·编辑左下的圆环钟 |
| 7 | `ace_bar` | `not hidden and cfg.show_ace_bar` | `draw_ace_display`（**模拟模式也用**） |
| 8 | `typhoons` | `not is_sim` | `_draw_typhoons` |
| 9 | `track_label` | `not is_sim and …` | 跟踪指示 |
| 10 | `season_info_boxes` | `not is_sim and not hidden and MODE_SEASON` | `draw_season_info_boxes` |
| 11 | `typhoon_info` | `not is_sim and not hidden` | 正常/风季/编辑的图标+信息框 |
| 12 | `effects` | `not is_sim and not hidden` | 登陆特效等 |

**本轮实测**（headless TySim）：`scene_layers` 名字序列 = `['map','graticule','sim_field','ocean_areas','ocean_edit','clock','ace_bar','typhoons','track_label','season_info_boxes','typhoon_info','effects']`（与 `tests/test_render_layers.py:17-27` 钉的 `EXPECTED_ORDER` 完全一致）；模拟模式下 `visible()` 为真的只有 **`['map','sim_field','ace_bar']`**。
⇒ 模拟模式的 z 序事实是：地图底图 → （可选经纬网）→ SimCore 图层+路径+图标+信息框+HUD → ACE 条。

### 3.10 性能节流：config.json 的键如何被消费

| config.json 键（实测值） | 读取点 | 落到哪里 | 实测效果 |
|---|---|---|---|
| `sim_precision="400ms"` | `_sim_reset:834`、`_sim_cycle_precision:1102-1118`、`_sim_tier_target_ms:1132-1141` | `sim_v4['params']['precision']` → `V.sim_step` 的 `precision_spec`（`core.py:5012-5014`）→ `inner/hold/quality` | 档位表 `50ms/100ms/400ms/800ms/2s/5s` = `(目标ms, inner, hold, quality)`；400ms 档 `(400,1,1,1)` |
| `sim_env_step_h=6.0` | `_init_sim_mode:213-216`（`snap_env_step`）、`_sim_set_env_step:920-937` | `sim_v4['envStepH']` → `core.step_hours` | 实测 `1/3/6` 生效、`12→6`（吸附） |
| `sim_particle_count=3000` | `_sim_particle_count:892-899`（钳 500-6000） | `Particles(n)`；`_sim_update:1618-1621` 发现数量变化即重建 | 只有 `'wind' in sim_layers` 时才 `step()`（`:1633-1634`，注释：原实现无条件 step ≈63 ms/帧） |
| `sim_env_params={}` | `_sim_reset:856` 合并进 `params`；`_sim_param_value/_sim_set_param/_sim_cycle_param/_sim_toggle_param:552-608` | `sim_v4['params'][k]` + cfg 记忆 | 11 个旋钮（`shTilt/shWestAmp/shExt{W,E,N,S}/wtOn/wtAmp/csOn/csAmp/terrainOn`），`_SIM_PARAM_STEPS:534-548` 每项 `[0]` = 内核默认 |
| `sim_ensemble=False` | `_sim_reset:848` | `params['forecast']` | 见 §3.7 |
| `sim_seed=20250801` | `_init_sim_mode:186`、`_sim_set_seed:984-1000` | `V.init(seed=…)` | 骰子实测落盘同步 |
| `sim_start=""` | `_coerce_start:461-474` → `latest_real_start(6.0)` | `V.init(start=…)` | 实测解析为 **2026-02-28 00:00**（不是注释里写的 1997-09-16） |
| `sim_data_mode=True` / `sim_data_year=None` / `sim_data_clim=True` | `:224-227`、`:1002-1031` | `params.dataMode/dataYear/dataClim` | 见 §3.6 |
| `sim_wind_mode="barbs"` / `sim_shear_fill=True` | `_sim_sync_layer_styles:1041-1048` | `sim_v4['_wind_mode'] / ['_shear_fill']` | 面板 cycle 三态/两态 |
| `sim_click_spawn=False` / `sim_middle_spawn=True` | `_sim_handle_click:1725`、`event_mixin.py:133-139` | 交互 | 左键只选中；中键生成 |
| `fps_cap=60` | `main.py:188-189` | 帧上限；交互后 100 ms 内**强制 60** | 与 `SIM_FRAME_BUDGET_MS=200` 配合 |

**单帧预算公式**（`_sim_unit_ms` `:1391-1411`）：
```
unit_ms      = max( 目标ms × step_h/6 / inner ,  stepCostMs × step_h/6 / inner ,  1.0 )
budget_units = max(1, int(200 / unit_ms))         # 再被 SIM_MAX_UNITS_PER_FRAME=16 钳
```
本轮实测（`step_h=6`，用档位目标代 `stepCostMs`）：`50ms→unit 50 / budget 4`、`100ms→100/2`、`400ms→400/1`、`800ms→200/1`（inner=4）、`2s→166.7/1`（inner=12）、`5s→161.3/1`。⇒ **默认 400ms 档一帧只允许 1 个内步（=6 模拟小时）**；`sim['stepCostMs']` 是本机实测"每 6h 墙钟 ms"（`core.py:5042-5044`，指数平滑 0.15），本轮内核探针单步实测 **759.2 ms**。

**实测速度天花板**（`_sim_update` 驱动、墙钟 20.8 s、sp=8、400ms 档、track 层）：
推进 `168 h / 20.8 s ≈ 8.1 h/s` ⇒ `_sim_effective_x() = 1.25×`，而请求 8×（48 h/s）。窗口 20.8 s 内只闭合 5 次（3 s 窗口 + 丢弃 >2 s 间隔，`:1504-1516`）。**⇒ 播放速度旋钮在 ~1× 以上对实际推进几乎无效**，面板红字才是唯一反馈（`control_panel.py:530-545`）。

### 3.11 悬停取值 / HUD / 统计框

- `_sim_probe_value`（`:2027-2073`）：只在 `md == MODE_SIM` 且鼠标在地图区且**该层真的在 `sim_layers` 里**时取值（`:2045-2046`，修过"关掉海温仍报海温"）；`sst → 'SST 27.3 °C'`、`ohc → 'OHC %.0f'`、`shear → '%.1f kt'`、`pressure → '%.1f hPa'`；取样走 `core.env_bilinear`。
- `_sim_draw_hud`（`:2207-2258`）：圆环钟用**含分/秒的显示钟**（`:2215-2222`，修过"恒 HH00Z"）；数据徽标（`:2223-2248`）；左下统计框（`_sim_draw_stats_box` `:2139-2205`）报"总命名 / 存活(命名 N) / 北半球年最强 / 南半球年最强"，命名判定读 `tc['named']` 而不是当前 vmax；流场图例（`_sim_draw_flow_legend` `:2093-2137`）给出环流 0-60 kt / 洋流 0-2 kt 的色标。
- 提示统一走 `show_toast`（`:1160-1174`），不再自绘右下角（会与经纬度 HUD 重叠）。

### 3.12 导出与入库

`_sim_export(interval_h=6)`（`:1340-1388`）写纯 b-deck（ATCF BEST 行）到 **`app/sim_output/sim_bdeck_YYYYmmddHHMMSS.bde`**（`:1344-1348`，`.gitignore:37 sim_output/` 已忽略）；`_bdeck_lat/_bdeck_lon` 不留多余前导零（`:1253-1269`）；「移入台风库」的目标是 `typhoon/sim/<年>/<年><洋区>[_invest]/`（`:1249-1251`、`:1313-1338`），实测该目录**当前不存在**（尚未搬过）。`sim_typhoon/` 是实时记录根（`:1247`，实测含 `2025/`、`2100/`，1.9 MB，git 未跟踪）。

---

## 4. 输入输出资产

| 路径 | 格式 | 体量 | 由谁产生 | 被谁消费 |
|---|---|---|---|---|
| `config.json` | JSON（113 键） | 4 KB | `ConfigStore.commit`（`config_store.py:91-132`） | `AppConfig.load`（`config.py:284-338`）→ 全应用 |
| `config.json.bak` | JSON | 4 KB | 首次落盘时 `shutil.copy2`（`config_store.py:106-109`） | `restore_backup()` |
| `assets/Area_ocean.json` (+`.bak`) | JSON 多边形 | ~100 KB | `ocean_edit.exit()` 写回 | `OceanManager` / `renderer._draw_ocean_areas` |
| `map/map.png`, `map/land.png`, `map/land2.png`, `map/heightmap.png` | PNG | 46 / 0.5 / 44 / 59 MB（`map/` 合计 **150 MB**） | 人工/工具 | `MapView`（`map_mgr.py:18` 加载）、`MapManager` 陆地掩码 |
| `assets/SMCY/**`（`N-DC-*.mp4` 等）、`assets/sound/`、`assets/font/` | mp4 / gif / ogg / ttf | `assets/` 合计 **529 MB** | 素材 | `smcy_icon.SMCYIconManager`、`particle_effect`、`constants.fonts` |
| `app/sim_output/sim_bdeck_*.bde` | ATCF b-deck 文本 | 346 个（1.5 MB，`.gitignore:37` 忽略） | `_sim_export`（快捷键 `E`） | 外部工具 / `tools/sim_year` |
| `sim_typhoon/<年>/<年><洋区>[_invest]/` | b-deck | 1.9 MB（`2025/`、`2100/`，git 未跟踪） | 内核/工具落盘（`BDECK_STAGE_ROOT` `:1247`） | 「移入台风库」→ `typhoon/sim/` |
| `output/logs/crash_*.log` | 文本 | 3 个（8/28、9/19、10/1） | `main.py:218-235` 异常路径 | 人工排障；内容含 `mode=` / `cti=` 上下文 |
| `typhoon/` | `.dat` 报文 | **5.5 MB**（不是项目体积主体） | 用户/外部 | `DataRepository.load_typhoon_files` |
| `simulator/`（含 `simcore/*.npz`） | py + npz + 气候库 | **2.0 GB**（项目 3.9 GB 的主体） | `core._land_field` 首次 ~28 s 生成 `land_0.5.npz` | 内核环境场 |

---

## 5. 如何运行 / 如何验证

**正确解释器**（WSL 下）：`/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe`（Python 3.14.2 + pygame-ce 2.5.7 + pytest 9.1.1）。中文输出是 GBK，需 `| iconv -f GBK -t UTF-8`。

```bash
cd /mnt/g/气象/Typhoon-sim
# 下面用全路径写，避免依赖 shell 变量（本会话的 bash 里 $VAR 展开不可靠）
PYBIN=/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe   # 仅示意；实际请写全路径

# ① 直接跑测试（不要跑 main.py：会开真窗口）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe -m pytest \
      tests/test_sim_mode.py tests/test_render_layers.py \
      tests/test_render_geom.py tests/test_sim_step_control.py tests/test_sim_throttle.py \
      tests/test_sim_time_model.py tests/test_panel_layer_row.py -q --no-header
#   本轮实测：102 passed in 584.48s

# ② 设置门禁
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe tools/_check_settings_sync.py      # RESULT 全部一致, 没有漏项（退出码 0）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe tools/_audit_settings_coverage.py  # missing=58 ghost=6

# ③ headless 只读探针（本轮写的脚本，落在 Windows 的 G:\tmp\）
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_runtime.py    # 图层像素/耗时、_sim_advance、D 开关、scene_layers
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_invariants.py # 图层不变量、按键分派、显示钟不变量、回放、sim→Typhoon 映射
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_clock.py      # ci 映射、回放、速度天花板、单步 vs 播放步
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_perf.py       # 测速窗口、洋流层、冷热成本
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_breakdown.py  # _sim_render 各段耗时
/mnt/c/Users/Yukioto/AppData/Local/Python/bin/python.exe G:/tmp/probe_render.py     # _sim_render_ms 到底测了什么
# 中文输出若乱码: ... 2>&1 | iconv -f GBK -t UTF-8
```
⚠ 探针的**只读纪律**：照抄 `tests/conftest.py` 的做法，在**导入 `app.ty_sim` 之前**把 `app.constants.CONFIG_FILE`、`app.constants.layout.CONFIG_FILE`、`app.ty_sim.CONFIG_FILE` 指到临时文件，否则 `save_config()` 会覆盖用户真实配置（conftest 的 docstring 记录过一次真实事故）。所有探针都设 `SDL_VIDEODRIVER=dummy`。

**本环境跑不了的**：
1. `python main.py`（真窗口，会让会话挂住）。
2. 真机 `pygame.display.flip()` / vsync 成本 —— headless 下近似 0，**本文所有帧时长都偏乐观**。
3. GPU/独显路径（内核纯 CPU；`pygame` 用 SDL 软件表面）。
4. `tests/` 全量（本轮按 lead 要求只跑单文件/小集合）。

---

## 6. 观察到的问题 / 风险 / 不一致

### BLOCKER / MAJOR

**M1（MAJOR，性能天花板）播放速度旋钮在 ≥1× 时基本无效，默认档实际推进远低于面板承诺。**
- 现象：请求 8×（48 h/s），实测约 **8.1 h/s（1.25×）**；`_sim_update` 驱动 20.8 s 只推进 168 模拟小时。
- 证据：`_sim_advance` 的预算闸 `budget_units = max(1, int(200/unit_ms))`（`sim_mode_mixin.py:1456`）；400ms 档 `unit_ms = max(400, stepCostMs) ≥ 400` ⇒ 一帧最多 1 个内步；本机 `stepCostMs` 实测 759 ms/6h（内核探针 `V.sim_step(sim,6.0,max_inner=1)`）。仓库内既有报告测得 400ms 档 5598–6634 ms/6h、真实有效 0.143×（`tools/模拟模式帧率实测报告.md:35`、`:105-106`）。
- 影响：用户看到"时间轴匀速走"，但 6 模拟小时要等数秒；只有速度条上方红字如实提示（`control_panel.py:530-545`），而红字要等 3 s 测速窗口闭合才出现。

**M2（MAJOR，可用性）进入模拟模式 / 重置会同步冻结主线程数秒到十几秒。**
- 现象：`_sim_reset` 首次 16.87 / 16.14 / 7.53 / 6.68 s（4 次不同进程），同进程第二次 0.98 s。
- 证据：`keyboard_mixin.py:491-496` 在 `switch_mode` 里同步调 `_sim_reset(msg=False)`；`_sim_reset:835-858` 是 `V.init` + `V.env_update` 的同步大函数；`_sim_handle_event:1685-1687` 的 `R` 键同样直接调它。
- 影响：按 `H` 或 `R` 后界面无任何反馈地冻住几秒；没有任何分帧/进度提示（对比：视觉资源预热做过分帧 `_tick_warmup`/`_warmup_visuals` `ty_sim.py:253-297`）。

**M3（MAJOR，性能）图层渲染地板远高于 60 FPS 预算，且部分层**没有**表面缓存。**
- 本轮实测（1280×800 headless，冷→热）：`sst` **2589 → 84 ms**、`ohc` 432 → 69、`shear` 388 → 67、`ridge` 98.8 → **96.6**、`pressure` 187.6 → **91.7**；整层 `_sim_render`：`{'track'}` 中位 24.9 ms、`{track,sst}` 39.9 ms、**全 9 层 240 ms（≈4 FPS）**。
- 证据：`pressure` 只缓存**等值线几何**但仍每帧 Python 画 ~2800 段线 + 标注（`render.py:712-762`）；`ridge` 每帧重算 `np.percentile` + mask + RGBA（`render.py:1381-1420`），`_blit_layer` 的缓存只覆盖贴图（`render.py:1577-1614`）。
- 影响：只开 `pressure` 或 `ridge` 就掉到 ~10 FPS；`sst` 首次切换有 ~2.6 s 单帧冻结（既有报告在 2560×1498 下量到 1431 ms，`tools/模拟模式帧率实测报告.md:83`）。

**M4（MAJOR，文档/代码漂移）仓库内的《模拟模式帧率实测报告》（2026-09-24）对本构建已过期，而它是最容易被当作"当前事实"引用的文档。**
- 报告 `:278-282` 说"实时集合预报默认 True，面板上没有开关"——现状：`cfg.sim_ensemble=False`（`app/config.py:155-161`）、面板有开关（`app/sim_settings_dialog.py:222`、`:116-121`）、`_sim_reset` 显式传 False（`sim_mode_mixin.py:846-848`）。
- 报告 `:284-290` 说"红色有效倍速读数系统性高估 4–9 倍，因为分母是被钳过的 dt"——现状已改：`_sim_track_rate` 用 `time.perf_counter()` 差值、不再收 `dt`（`sim_mode_mixin.py:1469-1516`）。
- 报告的多数行号已偏移（例：它引 `_sim_advance` 在 `:1046-1088`，实际 `:1425-1467`；引红字在 `control_panel.py:399-411`，实际 `:530-547`）。
- 影响：新读者会得到与代码相反的结论（"最大性能项不可见不可关"）。

**M5（MAJOR，指标失真 + 死代码）自适应降级是双重死的：既没有开关写入，它的指标也只测了整层耗时的很小一部分。**
- `sim_auto_degrade` **全仓源码只有一处出现**（`sim_mode_mixin.py:1856` 的读取；`grep --include=*.py` 复核），**没有任何地方写它** ⇒ `degrade` 恒 0，`_sim_degrade_level`（`:1763-1774`）不可达，`图层降级: …` 提示（`:1881-1884`）永不出现。（仓库内既有报告 `tools/模拟模式帧率实测报告.md:292-298` 独立得出同一结论。）
- `_sim_render_ms` 的唯一写点是 `:1879`，而计时块 `t0` 在 `:1862`、只包住 `R.render_map(...)`；**实测** `{'track'}` 整层 24.85 ms 时 `_sim_render_ms = 0.063 ms`（差 ~400×），因为 app 层的 `draw_typhoon_info`（13.23 ms）、`draw_typhoon`、`_sim_draw_hud` 以及 `_sim_grid_map` 都在计时块之外。
- 附带：EMA 会把冷启动离群值带很久（`prev*0.85 + dt*0.15`），实测 `+sst` 那次 `_sim_render_ms=188 ms` 而整层只有 39.9 ms。
- 影响："帧预算自适应降级"这条设计路径在当前构建里完全不存在，而代码/注释给人的印象是它只是被关掉了。

**M6（MAJOR，数据可比性）单步 15 min 与播放一步 6 h 不是同一份统计。**
- 实测：连按 24 次 `.`（共 6 模拟小时）→ 7 个报点；播放一步 6 h → 2 个报点，`t` 都为 6.00。
- 证据：强度随机强迫 / 路径 OU / 涡旋 OU 都是"每步一次、不按 dt 归一"（`sim_step_control.py:20-23` 自述，本轮实测复现）。
- 影响：用单步逐帧检视得到的强度/路径抖动，与播放态不可比；导出 b-deck 时按 `interval_h` 采样（`:1361-1370`）也无法消除这个差异。

### MINOR

**m1** 6 个 `.py` 带 UTF-8 BOM（`app/__init__.py`、`app/resource_manager.py`、`app/ty_sim_mixins/__init__.py`、`app/ty_sim_mixins/draw_mixin.py`、`app/statistics/intensity_chart.py`、`app/statistics/typhoon_ace_chart.py`）。CPython 导入正常（tokenizer 跳 BOM），但 `ast.parse(open(p).read())` 直接 `SyntaxError: invalid non-printable character U+FEFF` —— 任何基于 `ast` 的静态工具（含我的探针）都会挂在这 6 个文件上。

**m2** `config.json` 只有 113 键，而 `AppConfig` 有 127 字段；缺的 14 个 = 3 个 `RUNTIME_FIELDS`（`config_store.py:48-52`，故意不落盘）+ 11 个后加的镜头跟踪字段（`config.py:247-265`）。加载时逐字段回落默认值（`config.py:304-318`），行为正确；但对"config.json 即契约"的读者是噪音。

**m3** 死代码：`_sim_draw_tc_icon`（`:1978-2005`）全仓**零调用点**；`_sim_cost_warn`（`:1120-1130`）与 `_sim_tier_target_ms`（`:1132-1141`）在生产代码里**零调用点**（只在 `tools/probe_sim_fps.py` 被用），而 `_sim_cost_warn` 的 docstring 说"UI 据此显示已达上限"、`core.py:738` 也说"超过它 UI 就提示"——**承诺的告警不存在**。

**m4** `sim_mode_mixin` 的 docstring/注释行号普遍过期：例 `_sim_set_seed (sim_mode_mixin.py:744)` 实际在 `:984`（`:744` 现在是 `_sim_set_display_particle`）；`_sim_step_control_key (sim_mode_mixin.py:1417-1445)` 实际 `:1697`；`_sim_rate_text` 引 `control_panel.py:399-411` 实际 `:530-547`。

**m5** `_sim_sync_path_ty` 里 `ty.at = _h / 12.0` **不钳制**（`:372`）：显示钟越过最后一报后 `at` 继续增长（实测 `disp_h=200 → at=16.67`，而最后报点 t=36 即 at=3.0）。当前靠 `ty.v.ipos` 的钳制兜住，但 `at` 会被路径插值读到，是潜在的越界外推。

**m6** `_sim_set_speed` 的提示语走 `_sim_rate_text`（`:976`），而"有效倍速"的**常驻**显示在别处重算了一遍（`control_panel.py:534-536`）——同一语义两处拼字符串，改文案要改两地。

**m7** 瞬时数据窗口短：面板会显示 `dataSrc='瞬时·部分'` 与 ERA5/OHC 覆盖窗口（`sim_settings_dialog.py:333-353`），但窗口一过就回落月均；`dataState` 仍是 `'loaded'`，HUD 徽标也只区分 `loaded/partial/fallback/off`，**看不出"瞬时→月均"的降级**（`_sim_draw_hud:2234-2237` 只是把 `dataSrc` 拼到文案后面）。

### 已核实：Lead 说的两道门禁结论**都成立**

- `tools/_check_settings_sync.py` 本轮复跑：`持久化字段: 124 / 全部 127（排除运行时 3 个）`、`_CFG_MIRROR 条目: 74`、`界面 key: 75`、`RESULT 全部一致, 没有漏项`，退出码 0。
- `tools/_audit_settings_coverage.py` 本轮复跑：`missing=58 ghost=6`，6 个 ghost = `_map_range_mode / bl_lat / bl_lon / level3_rot_speed / main_rot_speed / span`。
- **它们是误报，Lead 判对了**：`main_rot_speed→main_rotation_speed`、`level3_rot_speed→level3_rotation_speed`、`_map_range_mode→map_corner_mode` 都在 `_CFG_MIRROR`（`app/settings.py:443-444`、`:457`），而 `_CFG_MIRROR` 的 .items() 被两个方向都遍历（`:411` 读、`:2473` 写）；`bl_lon / bl_lat / span` 是**派生的显示字段**（不持久化）：显示值在 `settings.py:1067-1071` 由 `mlo/mla/Mlo-mlo` 算出，校验回写有两条路径（`:906-923` 与 `:2404-2410`）。工具 `tools/_audit_settings_coverage.py:71` 只做 `ghost = seen - cfg`，**没有读 `_CFG_MIRROR`**，所以把别名/派生键都算成了幽灵 —— 这是工具自身的判定缺陷。
- 补充观察（工具没覆盖的风险）：`bl_lon/bl_lat/span` 依赖**特判分支**而不是 `_CFG_MIRROR`，属于第三套机制；目前被 `_check_settings_sync.py` 覆盖，但改这两处时要小心两边不同步。

---

## 7. unresolved（未能确证）

1. **冷 `_sim_reset` 6.7–16.9 s 的内部分布**没有拆开。只知道总量，不知道 `V.init`、`V.env_update`、气候/日数据装载各占多少（我的内核探针里 `init+env_update` 只用了几秒，app 路径却又多花了时间，差异未定位）。需要逐段打点。
2. **`current`（洋流）图层第一次单层渲染得 0 px / 0.1 ms**（`probe_runtime`），而清空 `render._flow_cache` / `_flow_last` 后同条件得 7155 px / 61.5 ms（`probe_perf`）。差异根因未确证：可能是模块级节流缓存、也可能是当时 `ocn['u']` 缺失（`render.py:1085-1087` 静默 return）。需要在这两个状态下分别 dump `ocn` 键与 `_flow_last`。
3. **`_sim_render_ms` 在某些调用序列里大于整层耗时**（实测 188 ms vs 整层 39.9 ms；全 9 层 296 ms vs 240 ms）。唯一写点是 `:1879`，所以只能用"EMA 被更早的冷启动离群值主导"解释，但没有逐帧追踪确认。
4. **`sim_auto_degrade` 是否有意永久关闭**：代码注释说"用户反馈风羽闪烁，默认关闭"（`:1850-1853`），但**没有任何配置字段**能打开它，也没有 issue/轮次记录说明是"预留"还是"忘了接线"。
5. **`C['DT'] = 10/60` 是否还有别的消费者**：本轮在 `simulator/` 下 grep `['DT']` 无生产引用，但没有做全仓（含 `tools/`、`simulator/simcore/envmodel/`）的穷尽检索，也没有确认它是否只被注释引用。
6. **真实窗口下的帧率**：全部测量在 dummy SDL；`flip()`/交换链/DPI 缩放的成本未计入。
7. **`sim_wind_mode='particles'`** 路径未实测：`Particles.draw` 是 3000 次 Python 循环、每次 4 次投影（`render.py:1218-1226`），`Particles.step` 每帧都跑（`sim_mode_mixin.py:1633-1634`）。只做了代码判读。
8. **≥10 个同时存活台风**的渲染/物理组合未实测（本轮探针最多 1 只；既有报告也只到 8 只存活）。
9. **`app/sim_output/` 里 23:42 与 23:51 两个新 `.bde` 不是我的探针写的**：本轮我写的 6 个脚本全仓 grep 无 `K_e` / `_sim_export` 调用（`probe_invariants.py` 的按键表里没有 `E`）。疑为其它 teammate 的探针或应用自身运行所致，未确证；该目录被 `.gitignore:37` 忽略。
10. **`_apply_pending_wheel` 与模拟模式缩放的相互作用**未实测（`ty_sim.py:616-649` 的惰性失效只对 `self.tys` 走，模拟模式没有 `tys`）。

---

## 8. 关键位置速查表

| 想找什么 | 去哪看 |
|---|---|
| 主循环 / 帧上限 / dt 钳制 | `main.py:187-217`（`cap` `:188-189`，`dt=min(...,0.1)` `:192`） |
| 进/出模拟模式的入口 | `app/ty_sim_mixins/keyboard_mixin.py:468-547`（进模拟 `:491-496`）；`input_ctrl.py:20`（`H`） |
| 模拟模式初始化（配置恢复） | `app/ty_sim_mixins/sim_mode_mixin.py:176-253` |
| 建场 / 重置（含 params 全表） | `sim_mode_mixin.py:827-890`（`params` `:835-857`，`env_update` `:858`） |
| 显示钟 vs 物理钟推进 | `sim_mode_mixin.py:1425-1467`（`_sim_advance`） |
| 单帧预算 / 内步估算 | `sim_mode_mixin.py:1391-1411`（`_sim_unit_ms`），`:1456-1461`（闸） |
| 实测速度 → "有效 x×" | `sim_mode_mixin.py:1469-1538`；红字 `app/control_panel.py:530-545` |
| 播放/暂停按键 | `sim_mode_mixin.py:1676-1679`（SPACE）；面板 `keyboard_mixin.py:578-585` |
| 图层表（9 个）与三个入口 | `sim_mode_mixin.py:30-40`、`:406-453`、`:1050-1073`、`:1231-1243` |
| 格点层仿射映射 | `sim_mode_mixin.py:1781-1816`（`_sim_grid_map*`） |
| 各图层实际画法 | `simulator/simcore/render.py:1455-1575`（分发）；`render_env_layer:531`、`draw_contours:712`、`draw_ridge:1381`、`draw_streamlines:1032`、`draw_currents:1070`、`draw_barbs:1105`、`Particles:1182` |
| 数据环境开关 `D` | `sim_mode_mixin.py:1688-1690` → `:777-790`；状态字 `simulator/simcore/core.py:1984-2020`；徽标 `:2223-2248` |
| 12 成员集合预报 | `simulator/simcore/core.py:4751`、`:4857-4934`；app 开关 `app/config.py:155-161` + `app/sim_settings_dialog.py:222`；显示行 `app/ty_sim_mixins/draw_info_boxes_mixin.py:330-354` |
| 台风对象映射（sim → Typhoon） | `sim_mode_mixin.py:255-404`（`ty.sim` 绑定 `:292`，`track_total_n` `:403`） |
| 模拟模式的路径/图标/信息框 | `sim_mode_mixin.py:1890-1954`；`_show_future` 门 `app/ty_sim_mixins/_draw_path_mixin.py:513-531` |
| 回放（`,`） | `sim_mode_mixin.py:610-742`；内核 `simulator/simcore/rewind.py` |
| 单步 / 骰子 | `app/ty_sim_mixins/sim_step_control.py:162-216` / `:220-261`；按键 `:275-281` |
| 显式渲染图层表 | `app/renderer.py:202-298`（顺序即 z 序），遍历 `:306-313`；测试 `tests/test_render_layers.py` |
| 悬停取值 | `sim_mode_mixin.py:2027-2091` |
| HUD / 统计框 / 图例 | `sim_mode_mixin.py:2093-2258` |
| 导出 b-deck / 入库 | `sim_mode_mixin.py:1340-1388` / `:1245-1338` |
| 设置面板的模拟行表 | `app/sim_settings_dialog.py:204-254`（`_get`/`_set` `:82-138`，`_on_control` `:551-605`） |
| 配置落盘与运行时字段 | `app/config_store.py:48-52`、`:91-132`；`app/ty_sim.py:435-460` |
| 环境参数旋钮表 | `sim_mode_mixin.py:530-608` |
| 相机跟踪为何在模拟模式无效 | `app/ty_sim.py:699-703`（MODE_SIM 提前 return，`_update_tracking` 不到） |
