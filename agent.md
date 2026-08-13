# 台风路径模拟系统 — Bug 复审结果(2026-08-01,代码已大改后)

> 背景:B1-B36 清单发布后代码被大量修改(46 文件,1453+/745-),本轮四路并行复审验证修复状态并查找**新引入的回归**。结论:**B1-B36 大部分已修复;仍有 3 条残留(B9/B18/B20),并发现 1 个高危新 bug + 1 个中危新 bug + 若干中低危问题。**

## 一、B1-B36 修复状态核验

### 已修复 ✅(27 条)
| Bug | 说明 |
|-----|------|
| B1 脚本除零 | 守卫移到除法前(script_engine.py:572-578) |
| B2 范围除零 | 对话框路径已校验;**残留:config.json 手改坏值仍可在启动时除零**(见 R2) |
| B3 字段/标签错位 | 已对齐 mlo/mla/span(settings.py:489-491,855-857) |
| B4 resize MapView | handle_resize 更新 mv 尺寸/min_scale/缓存(ty_sim.py:398-406) |
| B5 S 键重激活 | activate 幂等(settings.py:272-273) |
| B6 南半球重置 | reset 路径已修(season_ctrl.py:207-212);**残留:年回卷路径仍落 1 月 1 日**(见 R1) |
| B7 丢弃回滚 | _immediate_snapshot + _restore_immediate_applied(settings.py:706-722) |
| B8 拖拽后 smooth 过期 | 已修(ty_sim.py:496-503 触发恢复) |
| B10 脚本 w<=0 | 已校验(script_engine.py:271) |
| B11 可见性误裁 | 投影最多 16 个采样点 + 400px 边距(path_mixin.py:226-242) |
| B12 拖拽图标离轨 | 拖拽分支采样 smooth 曲线(icon_mixin.py:120-133);**残留:离散采样有步进**(见 R7) |
| B14 pan_cache 桶粗 | key 已含精确 scale(map_mgr.py:159) |
| B15 id 键控缓存 | _yearly_ace_cache 加 src 校验;geo_spline key 加 len+首点 id |
| B16 csa 双计 | _sync_to_season_ctrl 用未膨胀基准(ty_sim.py:552-559) |
| B17 跳页首键 | elif→if(point_list.py:258);**副作用:立即按 Enter 报错**(见 R13) |
| B19 Enter 无变化保存 | 已加 _has_field_changes 判断 |
| B21 信息框 key | 已加巅峰/起点/盆域;**残留:缺 official 字段**(见 R8) |
| B22 旧拖拽缓存清理 | 4 处已换 _clear_drag_cache |
| B25 点击控件 blur | 已修(settings.py:1342-1347,1416-1421) |
| B26 ACE min<=max | 已校验(settings.py:1516-1523) |
| B27 音量 float | 已改 float(val)/100(settings.py:1467-1468) |
| B28 _name_editing | 已在 _open_editor 重置(script_dialog.py:467) |
| B29 restore_defaults | 已重置 tab/滚动/下拉;**残留:_error_fields/_ace_changed 未清**(见 R14) |
| B30 跳页 ValueError | 已 try/except(保留弹窗) |
| B31 TSNote 回绕 | 已 %1800(particle_effect.py:206) |
| B32 mip 死级 | 已删 0.0625 级 |
| B33 多年度空缓存 | 空结果已入缓存 |
| B34 最近邻回绕 | 已用 min(abs, N-abs)(smcy_icon.py:149-152) |
| B36 is_land_at_geo | 已钳制 y(含 -90) |

### 仍残留 ❌(3 条)
- **B9 `_last_interact` 顺序(仍坏)**:`ty_sim.py:484-487` 在 `_apply_pending_wheel()` **之后**才读 `_pending_wheel`(已被清零)→ 滚轮永不更新 `_last_interact` → main.py 的交互期 60fps 封顶对缩放无效;且脚本 MOVING 的虚拟拖拽每帧刷新 `_last_interact` → **脚本镜头移动期间被钉在 60fps**。
- **B18 Settings IME 泄漏(仍坏)**:time_jump/new_typhoon/point_edit/point_list/script_dialog 都已修,唯独 **Settings.deactivate/_on_ok/_on_close/Enter/ESC/重载/快捷键按钮 7 条关闭路径都不 deactivate 字段** → start_text_input 残留。
- **B20 滚动 max 仍错(形式变了)**:bottoms 含全部 _targets(底部"确认"按钮 dy+dh-12)→ `_content_scroll_max` 恒 ≥36 → 每个 tab 都有约 36px 死滚动区,内容可滚出视口留空带。

---

## 二、本轮新发现 Bug(按严重度)

### R1.【高】南半球季节**年回卷**仍落在 1 月 1 日,且 Jul-1 ACE 边界 csa 永不重置
- 位置:`py/season_ctrl.py:69-103`(回卷)、`109-110`(ACE 年翻转)
- 问题:B6 只修了 reset 路径;`sy > edy` 回卷时 `ste` 被置 0(1 月 1 日),南半球又空转 6 个月;且 `current_ace_year` 在 7 月 1 日静默翻转时不重置 csa/_csa_base → 新季节显示 = 上季总额 + 本季增量,进度条相对 yad 虚高(北半球因边界恰逢 1 月 1 日回卷而掩盖)。
- 修复提示词:`回卷分支对南半球补 `self.ste = (datetime(sty,7,1)-datetime(sty,1,1)).total_seconds()`;把 ACE 年边界检测与日历回卷分离:凡 `ace_year(current_dt)` 变化且非季节重启时刻,重置 `csa=_csa_base=0`(并通知 ACE 图)。`

### R2.【高】SH 月度 ACE 柱状图:数据与月份标签错位(实际显示北半球顺序)
- 位置:`py/statistics/multi_year_chart.py:85-89`
- 问题:`buckets` 按自然月累计(`buckets[m] += val`),但取值用 `monthly_ace.append((cal, buckets[cal]))` 而非 `buckets[m]` → 南半球每根柱子显示的是"标签月"的自然月数据(标签 1=7月 却显示 1 月 ACE),整体退化为北半球顺序。
- 修复提示词:`改为 `monthly_ace.append((cal, buckets[m]))`(等价 `((m-7)%12)+1` 作标签);cal 映射本身正确,只改取值索引。`

### R3.【中】`landfall_records` 从不清理:季节循环统计翻倍 + 无界增长
- 位置:`py/playback_ctrl.py:42,256`、`py/season_ctrl.py:221-224`(只清 _was_fin 等)、`keyboard_mixin.py:42-44`(reload 只清 _was_fin/_lf_last)
- 问题:数据循环重放时每次循环追加相同登陆记录;season_stats 按年计数 → 登陆数 ×循环次数;列表无界增长。
- 修复提示词:`在 reset_to_first_year、`sy == sty` 回卷块、reload_typhoons 三处清空 `playback_ctrl.landfall_records`(及 sim 别名)。`

### R4.【中】脚本"停留速度"行覆盖到达速度
- 位置:`py/script_engine.py:299-323`(解析 `*speed`)、`544-553`(_update_dwell)、`557-561`(_start_moving_to)
- 问题:文档语义是 arrive 前的 `*`=到达速度、arrive 后的 `*`=停留速度;实际 `elif has_arrive:` 分支把后者**覆盖**进 approach_speed → 到达段按停留速度走,停留期间却没有任何速度设置。
- 修复提示词:`新增 `dwell_speed` 字段:arrive 后的 `*` 只写 dwell_speed 不动 approach_speed;_enter_target 进入 DWELL 时用 `dwell_speed if 定义 else approach_speed`。`

### R5.【中】缩放 burst 内旧 zoom 的 smooth 样条与新 zoom 屏幕点混用(今日改动回归)
- 位置:`py/typhoon_render.py:63-64`(view_rect 早退不清 smooth)、`py/ty_sim_mixins/_draw_path_mixin.py:373-375,538-550`
- 问题:invalidate_screen_points_lazy 现在只在 burst **开始**清 smooth;burst 内第 2+ 档滚轮:screen_points 按新 zoom 重建但 smooth 保留旧 zoom(上一帧拖拽分支重建的)→ `len>=2` 检查通过 → 平滑线与标记/地图错位,持续到 burst 结束(~500ms)。快速滚 2+ 档必现。
- 修复提示词:`update_screen_points 的 view_rect 早退分支同时 `v.smooth_screen_points.clear(); v._smooth_arc_lengths.clear()`,使拖拽分支按当前 zoom 从 geo 样条缓存重建。`

### R6.【中】切换图标集后旋转缓存仍显旧图(B13 残留)
- 位置:`py/typhoon_render.py:76`(`_img_cache` key 无图像标识)、`py/ty_sim.py:62-64`(只清 scale 缓存)
- 问题:`_clear_icon_scale_caches` 清了 ring/center/l3/purple 缩放缓存,但每台风 `v._img_cache`(旋转面)key 无 id(img) → 切换后图标继续用旧图标集旋转图直到 rst/重载。
- 修复提示词:`_get_rotated 的 key 加 `id(img)`,或 icon_set 变更时遍历 `self.tys` 清 `ty.v._img_cache`。`

### R7.【中】数字键盘输入不触发 on_change → 设置静默丢编辑
- 位置:`py/input_field.py:241-251`(numpad 分支插入后不 `_notify_change()`;其它所有编辑路径都通知)
- 问题:设置页用 on_change 置 `_needs_save`;用**小键盘**改字段后按 ESC → 无未保存确认、改动静默丢失(Enter 因 _has_field_changes 仍能保存,掩盖了问题)。
- 修复提示词:`numpad 成功插入与 _delete_selection 后补 `self._notify_change()`。`

### R8.【中】点列表行缓存仍每帧构建全部格子 surface
- 位置:`py/point_list.py:105-114`(`_get_row_data` 每次调用都建 7 列 rt surface)、`511-515`
- 问题:B 修复只去掉了"双重调用";surface 构建仍无条件每帧执行 → 15 行×7 次 font.render ≈105 次/帧。
- 修复提示词:`拆分 `_row_hash(pt, dark)`(纯字符串)与 `_row_surfs(...)`;hash 失配才调后者。`

### R9.【中】TEXTINPUT 事件全项目无人处理 → 中文输入不可能
- 位置:`py/input_field.py:263-267`(只喂 KEYDOWN)、`py/script_dialog.py:197-326`;全项目无 `pygame.TEXTINPUT` 处理
- 问题:IME 组合字符以 TEXTINPUT 到达,全部被丢弃 → 中文应用无法输入中文台风名/脚本注释/文件名。
- 修复提示词:`InputField.handle_event 与 _TextArea 增加 TEXTINPUT 分支:在光标处插入 event.text(过 validator/max_length)并通知。`

### R10.【中】`reset_to_first_year` 不重置 `_csa_base` → 重置后 ACE 条回跳
- 位置:`py/season_ctrl.py:204-229`(只 `csa=0`)、`py/playback_ctrl.py:87-91`(每帧 csa = csa_base / base+插值)
- 问题:重置后下一帧 csa 被 _csa_base 复原为旧累计,季节 ACE 永久虚高直到年回卷/跳转。
- 修复提示词:`reset_to_first_year 补 `self._csa_base = 0.0`。`

### R11.【中】插值模式:季中刷新 + 首点 ACE 可能双计
- 位置:`py/playback_ctrl.py:140-145`(ci==0 且 last_ace_ci==-1 时加 pts[0].pace)、`py/ty_sim.py:552-559`(refresh 重算 base 已含该点)
- 问题:刷新生效时台风恰在 ci==0 → 首点 pace 重复计入(窄窗口)。
- 修复提示词:`首点分支增加"该点未被 base 包含"判定(如 base 快照比对),或 refresh 时对所有活跃台风置 last_ace_ci = ci。`

### R12.【低】脚本 `_year_seconds_abs` 对 ≤1970 年份返回 0 → 老日期顺序反转
- 位置:`py/script_engine.py:683-688`
- 修复提示词:`epoch 前移(如 range(1, year))或钳制 year≥1970 并加偏移修正。`

### R13.【低】点列表跳页:点"跳页"后立即按 Enter 弹"请输入数字页码"
- 位置:`py/point_list.py:253-267`(创建字段的事件也喂给字段,Enter 提交空串)、`281-296`
- 修复提示词:`_do_jump 对空文本直接 return(不报错)。`

### R14.【低】`_restore_defaults` 残留:_error_fields 红框不清、_ace_changed/_hemisphere_changed 不重置
- 位置:`py/settings.py:749-765`
- 修复提示词:`恢复默认时清 `_error_fields` 并置 False 两个 changed 标志(否则下次 OK 无谓 recalc/季节跳转)。`

### R15.【低】Settings 丢弃路径无条件重跑盆域过滤,且 _ace_changed 残留
- 位置:`py/settings.py:706-722`(`_restore_immediate_applied`)
- 修复提示词:`记录本次是否真有即时项被改动,无则跳过 _apply_basin_filter/update_all_screen_points;丢弃时清 _ace_changed/_hemisphere_changed。`

### R16.【低】WAIT_USER 状态吞掉 MOUSEMOTION/MOUSEBUTTONUP → 脚本虚拟拖拽状态卡死
- 位置:`py/ty_sim_mixins/event_mixin.py:11-17`
- 修复提示词:`该分支只吞 KEYDOWN/MOUSEBUTTONDOWN/MOUSEWHEEL,MOUSEMOTION/MOUSEBUTTONUP 转发 _handle_map_pan。`

### R17.【低】`smooth_path_segments` 变更不失效路径缓存
- 位置:`py/settings.py`(apply 只查 smooth_path/mode)、`py/ty_sim_mixins/_draw_path_mixin.py:274-283`(key 无 segments)
- 修复提示词:`apply_settings 变化检测加 `smooth_path_segments`,并加入 _make_path_cache_key。`

### R18.【低】拖拽分支图标离散采样有步进(B12 残留)
- 位置:`py/ty_sim_mixins/_draw_icon_mixin.py:127-133`
- 修复提示词:`在 smooth_sp[i1] 与 smooth_sp[i1+1] 间按小数余量插值,或复用 position_at_arc。`

### R19.【低】信息框 key 仍缺 official(B21 残留)
- 位置:`py/ty_sim_mixins/_draw_icon_mixin.py:687-694` vs `780-783`(报别行)
- 修复提示词:`key_data 加 `point.get('official', True)` 与 `point['cat']`。`

### R20.【低】`_get_precomputed_landfalls` 跨 0 度经线长路绕插值
- 位置:`py/ty_sim_mixins/_draw_path_mixin.py:923-936`(`dlo` 未回卷,358→2 得 -356)
- 修复提示词:``dlo` 归一化到 (-180, 180] 再插值。`

### R21.【低】非拖拽路径 bbox 不含平滑点 → catmull 超调被裁剪
- 位置:`py/ty_sim_mixins/_draw_path_mixin.py:353-361`(拖拽分支 559-561 已含)
- 修复提示词:`平滑开启时 xs/ys 并入 smooth 点(镜像拖拽分支)。`

### R22.【低】`set_current_time` 越界分支不重置 `v.last_on_land`
- 位置:`py/typhoon_sim.py:181-188`
- 修复提示词:`else 分支补 `self.v.last_on_land = False`。`

### R23.【低】`_apply_pending_wheel` 在帧末重查鼠标位置 → 滚轮输入可能被吞
- 位置:`py/ty_sim.py:447-449`(事件时已校验,帧末再校验)
- 修复提示词:`事件时校验一次即可,帧末直接应用(或随计数存事件时位置)。`

### R24.【低】脚本分钟被时钟显示丢弃
- 位置:`py/script_engine.py:697-701`、`py/season_ctrl.py:158-168`(`st` 只有 %m%d%H)
- 修复提示词:`解析时按小时取整或文档化(时钟显示与 ste 可差 59 分钟)。`

### R25.【低】TS 阈值不一致:season_stats 用 ≥34,其余用 ≥35
- 位置:`py/statistics/season_stats.py:88` vs `_ace_eligible`(35)/`monthly_summary.py:139`(35)/`path_length_viewer.py:14`(34)
- 修复提示词:`计数统一 ≥35(与 _ace_eligible 一致);配色阈值保持 34 无妨。`

### R26.【低】路径对比框选为 toggle 且不作用于滚出视口行
- 位置:`py/statistics/path_comparison.py:429-445`
- 修复提示词:`无 Ctrl 时纯加选,Ctrl 才 toggle;或按视口外行一并处理。`

### R27.【低】SMCY 图标预加载尺寸仍不匹配:EX 差 2 倍、普通差 1px
- 位置:`py/smcy_icon.py:477-490` vs `py/ty_sim_mixins/_draw_icon_mixin.py:388-392`(绘制 104 vs 预载 105;EX 绘制 208 vs 预载 105)
- 修复提示词:`预加载用与绘制完全相同的公式(含 `4*round(.../4)` 量化与 EX 的 3.0 倍)。`

### R28.【低】`_TextArea._cut` 复制整篇文档到剪贴板;`_index_at` O(n²);每键全量快照
- 位置:`py/script_dialog.py:169-178`、`106-120`、`79-87`
- 修复提示词:`cut 只复制当前行;`_index_at` 前缀宽度二分;快照改为行列表拷贝并设总字符上限。`

### R29.【低】Shift 按住方向键自动重复会清除选区
- 位置:`py/input_field.py:187`(`_handle_lr_accel` 每次置 selection=None)
- 修复提示词:`Shift 按住时不清选区(查 mods),使选区连续扩展。`

### R30.【低】Settings 切 tab/切 ACE 模式丢弃字段未提交文本
- 位置:`py/settings.py:1405-1413,948-952,842-845`(rebuild_fields 重读 self.* 覆盖输入)
- 修复提示词:`rebuild 前把当前字段文本回写 self(尽力解析)或 _has_field_changes 时拒绝切换。`

---

## 修复顺序建议

1. **数据正确性**:R2(SH 月度图)、R1(SH 年回卷+csa)、R3(登陆记录翻倍)
2. **回归修复**:R5(burst 平滑错位)、R6(图标集旋转缓存)、R9(TEXTINPUT/中文输入)、R10/R11(ACE 基准)
3. **残留 B 项**:B9(_last_interact 顺序,含脚本 60fps 钉死)、B18(Settings IME)、B20(滚动死区)
4. **其余**:R4、R7、R8 → R12-R30

> 注:R1 与 B6 同源(只修了一半);R10/R11 与 B16 同族(ACE 基准一致性问题);R27 与 B35 同源(只修了一半)。修 B9 时注意:读 `n` 要在 `_apply_pending_wheel()` 之前,且脚本虚拟拖拽不应计为交互(main.py:64 判断加 `not script_engine.running`)。

---

# 功能需求提示词(2026-08-01)

> 两条新功能需求,实现前请确认当前代码状态(agent.md 复审结论显示代码已被大幅修改,行号可能漂移,以实际文件为准)。**只改下述文件,不动其他界面。**

## F1.【功能】路径长度按 ACE 年分段统计(仅统计界面)

- **需求**:统计界面(路径长度查看器 `path_length_viewer.py`、统计数据对话框 `season_stats_dialog.py` 的"总路径长度"项)的路径长度计算要**考虑 ACE 年**,把跨年台风按 ACE 年**分隔成两段**分别统计。为防止漏统计,每一段的统计窗口为 **今年 01/01 00Z 到 下一年 01/01 00Z(包含)**。**只改路径长度计算,其他统计项(ACE/风暴数/登陆等)不动。**
- **当前实现与问题**:
  - `py/statistics/path_length_viewer.py:41-52`:先按 `p.get('ace_year') == year` 筛点,再对筛选后**相邻**点求 haversine 段。跨年台风今年段从今年第一个点起算,明年段从明年第一个点起算——**1/1 边界两侧的首尾段被丢弃**(前一年最后一个点 → 今年第一个点之间那段没算);且南半球 ACE 年是 7/1-6/30(ace_engine.py:64-72),与需求要求的 1/1-1/1 窗口不一致。
  - `py/statistics/season_stats.py:107-115`(`total_path_km`):同样的 `_ace_eligible` 筛点+相邻 haversine,跨年段同样丢失。
- **修复提示词**:
  ```
  在 path_length_viewer.py 与 season_stats.py 中,把"按 ace_year 筛点后相邻连线"改为"按自然年时间窗口切分轨迹":
  1) 新增共享辅助函数(建议放 chart_helpers.py,如 compute_path_km_in_window(ty_pts, year) -> float):
     - 窗口 = [datetime(year,1,1,0), datetime(year+1,1,1,0)),即今年 01/01 00Z 至次年 01/01 00Z(含起始,不含结束;与现有点时间格式 %Y%m%d%H 对齐)。
     - 取台风点序列(已按时间排序)中与窗口有交叠的连续段:
       a. 若台风在窗口前已开始:在窗口起点 datetime(year,1,1,0) 处,对"窗口前最后一点→窗口内第一点"做线性插值(lat/lon 按时间比例,经度注意跨 0 度回卷,参考 _draw_path_mixin.py:933-934 的 dlo 回卷),得到边界起点坐标;
       b. 若台风持续到窗口后:在窗口终点 datetime(year+1,1,1,0) 处,对"窗口内最后一点→窗口后第一点"插值,得到边界终点坐标;
       c. 窗口内的相邻点对直接用 _haversine_chain 累加;边界插值点与相邻真实点之间同样 haversine 累加。
     - 台风完全在窗口内(或未跨越边界)时,退化为现行为(全段累加),保证旧数据结果不变。
  2) path_length_viewer.py:41-52 改用该函数(仍保留 _ts_eligible/point_in_limit 过滤,但过滤后按时间窗口切分而非按 ace_year 分桶);
  3) season_stats.py:107-115 同样改用该函数。
  4) 验证:跨年台风(如 2025-12-28 至 2026-01-05)在 2025 与 2026 两个 ACE 年的路径长度应各覆盖自己窗口内的部分,两段之和≈整条路径长度(不含非 TS 段);南半球也按 1/1-1/1 切(与需求一致,不按 7/1)。
  ```

## F2.【功能】强度折线图(K 键)显示路径长度

- **需求**:正常/编辑模式的单台强度界面(按 K 打开的强度折线图+ACE 累计图,`py/statistics/intensity_chart.py`)显示该台风的**路径总长度**。**不加 ACE 年限制**(显示整条台风完整路径,不分年)。
- **当前位置**:`intensity_chart.py:166-169` 标题区(名称+详情),图表上方按钮区(171-192)。
- **修复提示词**:
  ```
  在 intensity_chart.py 的 _build() 中:
  1) 用全部点(不分年、不做 TS 过滤)计算整条路径长度:
     pts = ty.pts; 若 len(pts) >= 2,用 _haversine_chain 逐对累加(复用 chart_helpers 的链式函数,首点 state=None)。
  2) 在标题下方(或图表左上角信息区)渲染一行,如 f"路径长度: {km:.0f} km"(用 rt(f_s, ...) 预渲染,参与 _cached_chart 缓存,勿每帧渲染)。
  3) 不添加任何 ACE 年切换/限制控件;总结条(summary_effect.py)、信息框等信息保持现状。
  4) 验证:正常/编辑模式按 K 打开,单台风详情面板显示路径总长;跨年台风显示整条长度(不与 ACE 年分段)。
  ```

> 注意:F1 只影响统计界面(路径长度查看器 + 统计数据对话框的总路径长度),F2 只影响强度折线图。两者共用 `_haversine_chain`,改动均在统计目录与 chart_helpers 内,不触碰 season_ctrl/playback 等运行时代码。

---

# 算法复杂度优化提示词(2026-08-01 审查)

> 范围:算法级复杂度优化(O(n²)→O(n log n)/O(n)、线性扫描→二分/哈希、重复全量扫描→单趟/增量/缓存),不含算术微优化(算1-算40 已覆盖)且不重复已实施的优化(bisect 的 position_at_arc、_geo_spline_cache、_row_hash 拆分、拖拽分层、_pan_cache、mip 链等均已确认存在)。数据规模参考:约 400-800 个台风文件、每台风 20-60 点,总点量 P≈1-4 万,ACE 年 Y≈25-30。按影响排序:**法1-法8 高,法9-法18 中,法19-法30 低**。每条含:位置、当前复杂度与触发时机、优化算法、等价性、收益。

## 一、高优先

### 法1.【每帧】路径 bbox 在缓存命中前每帧重算
- 位置:`py/ty_sim_mixins/_draw_path_mixin.py:353-365`(在 `_path_cache_key` 命中检查之前)
- 问题:每帧每台风对 screen_points(最多 ~400 点)+ smooth 点(n×segs 最多 ~4000)做 4 趟列表推导+min/max,**缓存命中时这些全白算**(blit 走 `_path_cache_blit`)。30 台风×4000 元素 ≈ 12 万次元素访问/帧。
- 优化:`把 bbox 计算(354-365)移到缓存失配重建分支内;或将 bbox 存到台风对象(与 `_path_cache_blit` 并列),命中直接取。bbox 仅依赖 screen_points/smooth_screen_points,已在缓存 key 指纹中(sp_first/sp_last/len/id(pts)/view version),无需担心过期。`
- 等价性:像素级一致。收益:缓存命中帧从 O(n) 变 O(1)/台风,最大稳态收益。

### 法2.【已移除,不采用】脚本镜头移动每帧全量重投影所有台风
- 状态:2026-08-01 用户确认**会带来问题**,不予实施。保留位置记录供追溯:`py/script_engine.py:657` 目前仍用 `update_all_screen_points()`(全量立即重投影+清全部路径缓存),**保持现状,不要改动**。若后续要优化此路径,需先解决脚本 MOVING 与惰性失效机制之间的兼容性问题(用户指出存在隐患),而非直接替换。

### 法3.【每帧·对话框】台风 ACE 图表每帧全量排序
- 位置:`py/statistics/typhoon_ace_chart.py:44-49`(缓存检查 56 行之前)
- 问题:ACE 图表对话框打开期间,每帧 `sorted(typhoon_sort_data, ...)` O(T log T)(T 可达 200)。
- 优化:`按 `(id(typhoon_sort_data), sort_mode)` 缓存 3 个排序变体(缓存条目已含该 key),page_data 从缓存切片。`
- 等价性:位级一致。收益:移除每帧 O(T log T)。

### 法4.【编辑/设置时】`refresh_all` 每次全量重扫 O(P·Y)
- 位置:`py/ace_engine.py:284-302`(+ yearly_ace:98、build_timeline_cache:259、typhoon_ace_list:181)
- 问题:每次点编辑(utils_mixin.py:120,183、point_list.py:436)、盆域/设置变更都做:fill_point_ace_years(扫 P+strptime)+ yearly_ace(扫 P)+ 每年 build_timeline_cache(再扫 P)+ typhoon_ace_list(再扫 P)≈ (2Y+2)≈62 趟全扫,每点含 point_in_limit(盆域模式=contains O(边))与 strptime。
- 优化:`单趟按 ace_year 分桶建"按年事件索引"(每点存 dt/pace/ty/in_limit/eligible/official/st/w),每年排序+前缀和;timeline、台风累计、daily/cumulative/active_periods 全部从该索引派生(法5-法7 共用)。O(P + ΣP_y·log P_y)。`
- 等价性:每年前缀和顺序与现排序后累加一致(≤1 ulp)。收益:每次编辑约 30-60 倍少访问点。

### 法5.【跳转时】`cumulative_ace_up_to` O(P) 且每点 strptime
- 位置:`py/ace_engine.py:121-135`;调用:`season_ctrl.jump_to:187`、`ty_list.py:358`、`script_engine.py:714/718`、`settings.py:1682`、`_sync_to_season_ctrl`
- 优化:`用法4 的按年排序事件+前缀和:bisect_right(dts, dt) 取前缀差,或直接用每台风 points_dt 二分定位窗口内点求和。O(log n)。`
- 等价性:包含点集合相同,求和顺序 ≤1 ulp。收益:每次跳转 P=3 万次访问 → ~15 次二分。

### 法6.【跳转时】`jump_to` + `set_current_time` O(N·P) 且 2×strptime/台风
- 位置:`py/season_ctrl.py:193-214`(每台风 2 次 strptime 首末点)、`py/typhoon_sim.py:163-181`(`set_current_time` 线性扫描 points_dt)
- 优化:`首末时间直接用 `ty.points_dt[0]`/`ty.points_dt[-1]`(recalc_simulated_times 已维护排序好的解析时间),段定位用 `bisect.bisect_right(ty.points_dt, dt)`(与现"首个 points_dt[i]<=dt<=points_dt[i+1]"完全一致);len(points_dt)!=len(pts) 时回退旧路径。`
- 等价性:位级一致。收益:O(N·P)→O(N log P) 且省 2N 次 strptime(1000 台风×100 点 → 10 万次比较变 1 万次)。

### 法7.【图表打开/切年】ACE 对话框重建 ~8-10 趟全扫
- 位置:`py/statistics/data_builder_chart.py:31-95`(daily_ace、daily_activity_count、active_periods、typhoon_sort_data 各自全扫 P)+ `py/statistics/dialog_chart.py:155-169`(_rebuild 还跑 calculate_season_stats)
- 优化:`(a) 基于法4 索引,四个函数各自只需单趟扫该年已过滤事件(免 point_in_limit/strptime/ace_year 判断),daily_ace 用前缀和二分 O(days·log n);(b) 整份 ChartData+_stats_data 按 `(year, cumulative_to_current, 当年 sim_dt)` 记忆化,refresh_all 时失效。`
- 等价性:桶/值相同,≤1 ulp。收益:每次重建 8-10 倍,切年变 O(1)。

### 法8.【C5 总结条】紫滤镜每帧 numpy 全图 float32
- 位置:`py/summary_effect.py:184-186`(调 `_apply_purple_filter`,`_draw_icon_mixin.py:44-62`)
- 问题:每帧每总结条 surf.copy+pixels3d+float32+4 趟向量运算(1920×64 ≈ 12 万像素),帧号每帧前进永不命中缓存。
- 优化:`滤镜是逐像素强度的纯函数(f=(mx-mn)·k):预计算每 (tier, strength) 一张 255 级 LUT,用 cv2.LUT 或 surfarray 映射,每帧成本降至几十 µs;并按 `(cat, hemi, frame_idx, tier)` 缓存结果面(复用 _purple_frame_cache 模式)。`
- 等价性:逐像素公式相同,取整后视觉一致。收益:1-3ms/帧 → 数十 µs。

## 二、中优先

### 法9.【每帧·季节】季节信息框 ACE 数字每帧裸 Font.render
- 位置:`py/ty_sim_mixins/draw_info_boxes_mixin.py:279`(每活跃箱,最多 8 个,绕过 SmartFont 缓存)
- 优化:`用本文件已有的 `_ace_digit_cache` 模式按 `(round(cace,4), tc)` 缓存(64 条上限),cace 已量化 4 位小数,相同数字跨帧极常见。`
- 等价性:字形一致,淡化路径仍 copy。收益:每帧最多省 8 次未缓存 render + GC 抖动。

### 法10.【预算超限时】SMCY 最近邻兜底 O(缓存)
- 位置:`py/smcy_icon.py:148-155`(解码预算耗尽时每流每台风 O(C) 扫 keys,C≤240)
- 优化:`维护并行有序键列表(插入递增+队首淘汰,几乎零成本),bisect_left 找最近邻 O(log C)。`
- 等价性:相同最近帧选择。收益:240 元素线性扫 → ~8 次比较(多台风同类别场景明显)。

### 法11.【每帧·对话框】多年度曲线图月网格线/标签每帧重建
- 位置:`py/statistics/chart_presets.py:646-664`(图表面已缓存,但月线/标签在每帧外循环重建:~40 个 datetime + 40 趟虚线逐像素 + 40 次标签渲染)
- 优化:`把月线+标签烘焙进 _multi_curve_cache 条目的 overlay 面(已有同款缓存实现 build_month_lines_surface,chart_helpers.py:310-348 可直接复用),每帧只 blit。`
- 等价性:几何/颜色一致。收益:每帧 40 次构建 → 2 次 blit。

### 法12.【每帧·盆域模式】`get_by_code` 线性扫描
- 位置:`py/ocean_mgr.py:233-236`;每帧调用:`py/playback_ctrl.py:223-224`(_check_landfall 每台风)、`py/ty_sim_mixins/draw_info_boxes_mixin.py:386`(ACE 条每帧);每次对话框:`season_stats.py:164`、`dialog_chart.py:593`、`summary_list.py:95`
- 优化:`在 `_load` 末尾建 `self._by_code = {a.code: a}`,`get_by_code` 变 O(1) 字典查。`
- 等价性:位级一致。收益:从每帧每台风线性扫 A≈10 → O(1)。

### 法13.【每帧·盆域】`contains` 无包围盒预检
- 位置:`py/ocean_mgr.py:83-96`(每次查询全边扫描 O(E),E≈30-100;`_ty_in_filter_basin` 每帧每台风、ACE 过滤每点调用)
- 优化:`_preprocess 预计算 `(min_lat, max_lat, min_lon, max_lon)`,contains 先 bbox 拒绝(绝大多数查询在盒外);find_area 先按半球(lat 符号)再 bbox 再边。`
- 等价性:bbox 是严格超集,判定语义不变。收益:每帧盆域检查从 ~30 边测试 → ~2 次比较。

### 法14.【对话框打开】热力图径向累计+像素循环
- 位置:`py/statistics/heatmap.py:146-181`(Phase3 每点×r² 带 sqrt;Phase4 全图 bw×bh≈170 万像素纯 Python 循环+每像素 _ace_heat_color/Color/map_rgb)
- 优化:`(a) Phase3 预计算一次性径向核表(半径固定时偏移集 (dx,dy,weight) 与点无关),每点只做乘加,免每像素 sqrt 与边界算术;进阶用 surfarray 加法 O(P·r²) 在 C 层;(b) Phase4 用 numpy:`np.asarray(heat,f32)` + LUT 索引 + surfarray 写回,50-100 倍。`
- 等价性:同 LUT 映射,取整一致。收益:打开从数秒 → 百毫秒内。

### 法15.【每帧·列表】台风列表行文本每行每帧 O(P) 扫描
- 位置:`py/ty_list.py:554-558`(`_get_row_hash`→`_build_row_texts` 每帧对每可见行做全点过滤+max)
- 优化:`把 O(P) 扫描结果(峰值风速+类别)按 `(id(ty.pts), len(ty.pts), ty.tace, name_display_mode, dark, basin)` 缓存;或加台风级修订计数(点变更时 bump),未变则跳过 _build_row_texts,每帧只做字符串比较。`
- 等价性:显示文本一致。收益:每帧 10 行×300 点 ≈ 3000 次迭代 → ~10 次 O(1) 比较。

### 法16.【打字时】脚本编辑器每键全量重解析
- 位置:`py/script_dialog.py:505-525`(`_live_parse` 由每个 TEXTINPUT/KEYDOWN 触发,每次全量 Script.parse+join)
- 优化:`防抖:on_change 只置 `_parse_dirty=True`,在 update()/定时器中最多每 150-300ms 重解析一次(或仅保存/运行/失焦时)。`
- 等价性:解析结果相同,状态栏延迟 <300ms 不可见。收益:O(L)/键 → ≤5 次/秒。

### 法17.【每帧·信息框】`max_wind_from_points` O(n) 每帧
- 位置:`py/ty_sim_mixins/_draw_icon_mixin.py:699`(普通/编辑模式信息框 key 每帧调用,构建过滤列表+max)
- 优化:`缓存 `ty._cached_max_wind`(与 _cached_max_wind_color 同失效点:view_state.py:77-79、typhoon_data.py:95-117)。`
- 等价性:相同值。收益:O(n)/帧 → O(1)。

### 法18.【脚本加载】repeat 块每次重复重解析
- 位置:`py/script_engine.py:363-391`(`_parse_repeat_block` 每重复一次新建子解析器重解析整块;嵌套时相乘 O(∏Rᵢ×B))
- 优化:`块解析一次成命令模板,每重复 `copy.deepcopy` 并重编号 TargetRegion.index(base += k×每迭代目标数,index 仅用于 __repr__,可完全等价)。`
- 等价性:命令序列一致。收益:900 目标嵌套示例从 ~4500 次解析 → ~900 次轻量克隆。

## 三、低优先

### 法19.【脚本加载】`_parse_target_block` O(T²) 目标计数
- 位置:`py/script_engine.py:280`(每个 target 全列表扫 CMD_TARGET 计数)
- 优化:`维护运行计数器(追加目标时 +1,repeat 子脚本并入),O(T²)→O(T)。`
- 等价性:idx 语义相同(repr 用)。收益:T=500 → 12.5 万次迭代 → 500 次自增。

### 法20.【每帧·季节】`_compute_interpolated_csa` 每帧逐台风 point_in_limit
- 位置:`py/playback_ctrl.py:175-194`(季节+插值模式每帧 O(活跃×边))
- 优化:`每台风保留部分 pace·t 累计,仅当 ci 前进或 t 跨粗量化桶(1/256)时重算;每点 point_in_limit 结果按点缓存(点静态)。`
- 等价性:变化 < 单点 pace 的 0.4%。收益:O(活跃×边)/帧 → O(变化量)。

### 法21.【统计构建】`calculate_season_stats` 每台风 4-6 趟
- 位置:`py/statistics/season_stats.py:59-129`(year_pts 过滤→basin 过滤→max/sum→风暴数扫描→ts_pts→路径→storm_day)
- 优化:`单趟循环算 has_td/ts/ty/mh/c5、max_wind、ace、ts_pts、storm_day(raw t[:10]);_parse_time 只对区间/路径代码调用;get_by_code 提升到 _compute_landfalls_from_data 循环外。`
- 等价性:谓词相同,值一致。收益:每次调用 4-5 倍。

### 法22.【多处】同时间串重复 strptime
- 位置:`py/ace_engine.py:18-22`(`_parse_dt`)、`season_stats.py:187-193`(`_parse_time`)、`chart_helpers.py:217`、`intensity_chart.py:140`、`intensity_comparison.py:149`、`summary_list.py:170-171`
- 优化:``_parse_dt/_parse_time` 加 `@functools.lru_cache`(按字符串键,ValueError→None 兜底保持);或直接复用 ty.points_dt。`
- 等价性:位级一致。收益:每次构建省 5-10 倍 strptime。

### 法23.【统计构建】`multi_year_chart` 盆域统计 A× 全扫
- 位置:`py/statistics/multi_year_chart.py:92-101`(每洋区调一次 calculate_season_stats,各全扫一遍)
- 优化:`单趟按点 find_area(或法4 的每点盆域归属)分桶,聚合计数/ACE/路径。`
- 等价性:每盆域聚合相同。收益:对话框打开 ~10 倍。

### 法24.【统计构建】`intensity_comparison` 重复过滤 pass
- 位置:`py/statistics/intensity_comparison.py:104-109 vs 138-141`(同一过滤谓词跑两遍+每点 strptime)
- 优化:`单趟构建 (pts, pts_dt, hours, t0),max_wind 从中取。`
- 等价性:位级一致。收益:构建 2 倍。

### 法25.【统计构建】`data_builder_chart` typhoon_sort_data 冗余全扫
- 位置:`py/statistics/data_builder_chart.py:60-81`(每台风全点扫峰值,过滤集与 get_tropical_points 相同)
- 优化:`复用 `ty._cached_max_wind_color`/`ty._cached_peaks`(_draw_icon_mixin.py:480-493 同排除集),或按 `(id(pts), len(pts))` 缓存。`
- 等价性:峰值相同。收益:每次重建省一次 O(P)。

### 法26.【事件】`ty_list._filtered_indices.index` O(N)/键
- 位置:`py/ty_list.py:380,405,434`(方向键/Enter/编辑定位)
- 优化:`_apply_filter 时建 `_pos_map = {idx: pos}`,O(1) 查位置。`
- 等价性:相同位置。收益:按键 O(N)→O(1)。

### 法27.【事件】`keyboard_mixin` `tys.index` O(N)/键
- 位置:`py/ty_sim_mixins/keyboard_mixin.py:200,213`(`[`/`]` 切换台风)
- 优化:`维护当前索引(导航时自增/自减)或 `{id(ty): idx}` 映射。`
- 等价性:相同。收益:按键 O(N)→O(1)。

### 法28.【每帧·列表】point_list 行 hash 字符串每帧构建
- 位置:`py/point_list.py:519-523`(每行每帧拼接 7 字段字符串,虽无点扫描)
- 优化:`加台风级修订 token(_clear_row_cache/_after_point_change 时 bump),未变跳过 hash 构建。`
- 等价性:变更检测一致。收益:每帧省 ~56 次字符串格式化。

### 法29.【特效】RI/登陆特效每帧 copy/rotate
- 位置:`py/particle_effect.py:140-143`(恒定 191 alpha 每帧 copy)、`py/landfall_effect.py:149-160`(img2.copy 每帧 + transform.rotate 每帧)
- 优化:`RI 按 `(idx, sw, sh)` 缓存 75% 淡化拷贝;登陆按 16 级 alpha 桶 _faded_copy 缓存,旋转按 4° 桶缓存。`
- 等价性:视觉差在 1 个量化步内。收益:每帧省整面 copy 与 rotate(≤2s 特效,影响有界)。

### 法30.【每帧】季节时钟 trig 列表每帧重建
- 位置:`py/ty_sim_mixins/draw_info_boxes_mixin.py:315-323`(每帧最多 120 次 cos/sin+列表推导)
- 优化:`模块级 360 项 cos/sin LUT,或按 progress 的 1/120 粒度缓存弧线面。`
- 等价性:亚像素一致。收益:~120 trig/帧 → ~0。

## 优先级建议

1. **第一梯队(每帧稳态)**:法1(bbox 延迟)、法3(ACE 图排序)
2. **第二梯队(跳转/编辑卡顿)**:法4(refresh_all 索引)、法5(cumulative 二分)、法6(jump_to 二分)、法7(图表记忆化)
3. **第三梯队(对话框打开)**:法8(紫滤镜 LUT)、法14(热力图向量化)、法21/法23/法24(统计单趟化)、法22(strptime 缓存)
4. **第四梯队(每帧小项)**:法9-法13、法15、法17、法20、法28-法30
5. **第五梯队(事件/加载)**:法16(防抖)、法18/法19(repeat/计数)、法25-法27

> 注意:
> - 法4-法7 同源(按年事件索引),一起实施收益叠加;法12/法13 同源(ocean_mgr 字典+bbox),先行可放大其余收益。
> - 法2 已移除(用户确认惰性替换会带来问题),script_engine.py:657 保持现状。
> - 已确认无需改动:position_at_arc bisect、recalc_simulated_times bisect、_merge_intervals 排序单趟、is_land_at_geo O(1)、_get_precomputed_landfalls 缓存、control_panel build 短路、settings ~50 次 _sync O(1)、season_ctrl 各缓存、undo deepcopy 事件级可接受。
---

---

# 第二轮 Bug 复审(2026-08-02)

> 范围:在 v1.4.4(2026-07-31 提交)基础上对全部代码重新走查,核验上轮(B1-B36/R1-R30)修复状态并查找新引入的回归。结论:**上轮 B1-B36 与 R1-R30 已全部修复(含 B9/B18/B20/R1-R16 等残留项);本轮新发现 3 个高危 + 12 个中危 + 10 个低危 bug + 2 个基础设施问题**,全部附修复提示词。行号以当前工作区文件为准。

## 一、上轮修复状态核验(已确认)

| 条目 | 状态 | 核验位置 |
|------|------|----------|
| B9 `_last_interact` 顺序 | ✅ 已修 | ty_sim.py:484-488 在 `_apply_pending_wheel()` 前读 `n`;脚本运行中不计交互 |
| B18 Settings IME 泄漏 | ✅ 已修 | deactivate/_on_ok/_on_close/_on_reload/快捷键钮/目标点击/Tab 切换均 `_deactivate_fields()`(settings.py:362-373,740-762,803-805,1397-1421,1507-1532) |
| B20 滚动死区 | ✅ 已修 | settings.py:672-678 用 `r.top+scroll < footer_top` 过滤底部按钮 |
| B2 残留(手改 config 除零) | ✅ 已修 | config.py:119-131 启动归一化 mlo/Mlo/mla/Mla/ace 范围 |
| B6/R1 南半球回卷+ACE 年翻转 | ✅ 已修 | season_ctrl.py:77-88(ste=7月1日偏移+重置 csa)、114-122(7/1 静默翻转重置) |
| R2 SH 月度柱错位 | ✅ 已修 | multi_year_chart.py:88 已是 `(cal, buckets[m])`(仅剩标签语义问题,见新 N21) |
| R4 停留速度覆盖 | ✅ 已修 | script_engine.py:126 `dwell_speed` 字段 + 307-331 解析 + 546-557/594-597/622-625 使用 |
| R5 burst 平滑错位 | ✅ 已修 | typhoon_render.py:63-64 早退清 smooth;ty_sim.py:433-441 burst 开始清空 |
| R6 图标集旋转缓存 | ✅ 已修 | typhoon_render.py:77 key 已含 `id(img)` |
| R7 小键盘不通知 | ✅ 已修 | input_field.py:272-278 补 `_notify_change()`(但长按退格仍漏,见新 N14) |
| R8 点列表行缓存 | ✅ 已修 | point_list.py:516-531 仅 hash 失配重建 |
| R9 TEXTINPUT 中文输入 | ✅ 已修(引入新 N2) | input_field.py:246-263;script_dialog.py:238-245 存在非激活插入缺陷 |
| R10 reset `_csa_base` | ✅ 已修 | season_ctrl.py:235 |
| R11 首点双计 | ✅ 已修 | ty_sim.py:562-563 `last_ace_ci = ty.ci` |
| R12 老日期顺序 | ✅ 已修 | script_engine.py:709-713 `range(1, year)` |
| R13 跳页空 Enter 报错 | ✅ 已修 | point_list.py:292-293 空文本直接 return |
| R14/R15 恢复默认/丢弃残留 | ✅ 已修 | settings.py:834-836,771-791 |
| R16 WAIT_USER 事件吞掉 | ✅ 已修 | event_mixin.py:13-19 MOUSEMOTION/MOUSEBUTTONUP 转发 |
| R17 smooth 段数失效 | ✅ 已修 | settings.py:1661-1664 + key 含 segments(_draw_path_mixin.py:283) |
| R19 信息框 official | ✅ 已修 | _draw_icon_mixin.py:723-731 已含 official/cat/name_display_mode |
| R21 路径 bbox 含平滑点 | ✅ 已修 | _draw_path_mixin.py:359-361 |
| R22 越界 last_on_land | ✅ 已修 | typhoon_sim.py:203-211 |
| R25 TS 阈值 | ✅ 已修 | season_stats.py:90 统一 ≥35 |
| R29 Shift 选区 | ✅ 已修 | input_field.py:316-343/188-193 |
| R30 切 tab 丢文本 | ✅ 已修 | settings.py:424-452 `_flush_fields` |

> 注:R23(帧末重查鼠标位置)仍存在,属已知设计取舍,未列入本轮。

## 二、本轮新发现 Bug 与修复提示词

### 高危

**N1.【高】报点编辑输入非法值 → "幽灵撤销",静默回滚上一次有效编辑**
- 位置:`py/point_list.py:348-354`(_update_point)、`375-387`(_add_point)、`389-411`(_apply_point_change)
- 问题:`_apply_point_change` 先执行 `w = int(vals['wind'])` / `p = int(vals['pressure'])`(390-391 行),**之后**才 `push_snapshot()`(404/411 行)。强度/气压填 `abc` 等非数字时 ValueError 发生在快照之前 → except 分支调用 `typhoon.undo()` 弹出的是**上一次操作**的快照 → 上一次成功编辑被静默撤销,且本次输入未生效。
- 触发:点列表编辑/插入报点,强度或气压栏输入非数字后确认。
- 修复提示词:`把 `push_snapshot()` 移到 `_apply_point_change` 开头(int 解析之前);except 分支仅在本次确实推过快照时才调用 undo(记录一个标志位)。注意 utils_mixin.py:83-85/152-154 的编辑模式路径同样先解析后 push_snapshot(117 行),但该路径异常不调 undo,只需保持"先快照后解析"的通用顺序。`

**N2.【高】脚本对话框:编辑文件名时 TEXTINPUT 污染脚本正文;文件名无法输入中文**
- 位置:`py/script_dialog.py:238-245`(_TextArea TEXTINPUT 分支在 `active` 检查之前)、`802-807`(文件名编辑)、`498-503`(_close_editor)
- 问题:TEXTINPUT 分支位于 `if not self.active` 之前 → 点击文件名框后(`_editor.active = False`,804-806)SDL 仍在发 TEXTINPUT(编辑器激活时 start_text_input 从未 stop)→ 每次在文件名栏敲键,KEYDOWN 进文件名(812-823),**同一次击键的 TEXTINPUT 把字符插入脚本正文旧光标处** → 脚本被静默改写;中文 IME 组合结果也全部进脚本,文件名栏永远收不到中文。
- 修复提示词:`1) _TextArea.handle_event 的 TEXTINPUT 分支开头加 `if not self.active: return False`;2) 点击文件名框时(802-807)调用 `pygame.key.start_text_input()+set_text_input_rect(文件名框 rect)`,退出文件名编辑(Enter/Tab/点击编辑器)时按编辑器/文件名状态切换启停,保证 TEXTINPUT 只投递给当前激活部件;3) _close_editor(498-503)补 `stop_text_input`。`

**N3.【高】设置 OK/确认后 `ace_geo_limit_enabled` 被陈旧本地值覆盖为 False,ACE 计算口径分裂**
- 位置:`py/settings.py:793-800`(_apply_filter_now)、`1541-1657`(apply_settings 的 `_sync` 列表)、`275-349`(activate 不读取该值)
- 问题:`_apply_filter_now` 只写 `self.sim.ace_geo_limit_enabled`(798 行),本地 `self.ace_geo_limit_enabled` 始终是 `_init_data` 的 False;`activate()` 也不从 sim 读它。打开设置点"确认"后,`_sync('ace_geo_limit_enabled')`(1648 行)发现 sim=True、self=False → **把 sim 覆盖回 False**。之后 `typhoon_data.recalc_ace:104` 的 `geo_enabled=False` → 区域外报点也计 pace → `ty.tace`/`tsa`/信息框 ACE 与 `ace_engine.yearly_ace/_year_events`(按 point_in_limit 过滤)口径不一致,总 ACE 数值互相矛盾。
- 触发:config 中 `ace_geo_limit_enabled: true`(或本会话已选过 ACE 限制模式)后,打开设置按 OK/Enter/关闭确认任一保存路径。
- 修复提示词:`activate() 补 `self.ace_geo_limit_enabled = self.sim.ace_geo_limit_enabled`;_apply_filter_now() 同步写 `self.ace_geo_limit_enabled = self.sim.ace_geo_limit_enabled`;apply_settings 的字段列表可保留但确保两者恒等(最稳做法:apply_settings 里把该字段从 _sync 列表移除,只在 _apply_filter_now 维护)。`

### 中危

**N4.【中】南半球路径长度统计:自然年窗口 vs ACE 年窗口不匹配,1-6 月半个赛季丢失/错配**
- 位置:`py/statistics/chart_helpers.py:200-212`(`compute_path_km_in_window` 固定 `[1/1, 1/1)`)、`py/statistics/season_stats.py:116-118`(传 `ace_year==year` 过滤后的 ts_pts)、`py/statistics/path_length_viewer.py:42-46`(未按 ace_year 过滤)
- 问题:SH 的 ACE 年是 `[7/1 Y, 6/30 Y+1)`。season_stats 传入的 ts_pts 含 1-6 月(Y+1)报点但窗口只到 `1/1 Y+1` → 这半季点全在窗口外被丢弃,且年总长不含它们;path_length_viewer 未按 ace_year 过滤,窗口 `[1/1 Y,1/1 Y+1)` 实际混合"上个赛季的 1-6 月 + 本季 7-12 月",本季 1-6 月缺失。NH 下两处均正确(自然年==ACE 年)。
- 修复提示词:`把窗口参数化:`compute_path_km_in_window(ts_pts, year, window=(datetime(year,1,1), datetime(year+1,1,1)))`,SH 时传 `(datetime(year,7,1), datetime(year+1,7,1))`;season_stats.py:117 与 path_length_viewer.py:46 按 `sim.hemisphere` 选择窗口(HEMISPHERE_NORTH→自然年,SOUTH→7/1-7/1)。path_length_viewer 保持按 ace_year 过滤或按窗口过滤二选一,须与标题语义一致。`

**N5.【中】ACE 统计对话框:时间跳转后图表数据不刷新(缓存 key 缺 sim 时间)**
- 位置:`py/statistics/dialog_chart.py:155-174`(key=`(year, cumulative_to_current, lm, bc, rev)`)、`640-648`(_jump_sim_to 置 needs_update=True 但 `_rebuild` 早退)
- 问题:`rev = sim._ace_data_revision` 只在 `refresh_all` 递增;jump_to/_sync_season_state 不触碰。`cumulative_to_current=True`(运行时间模式)时点"活跃周期图"跳转时间 → `needs_update=True` → `_rebuild()` key 未变 + `_stats_data 非空` → 直接 return → `build_chart_data` 的 `_cutoff(sim,...)` 仍是旧 sim 时间 → ACE 累计曲线/日 ACE 柱停在跳转前。
- 修复提示词:`key 中加入当前模拟时间指纹:`key = (year, self.cumulative_to_current, lm, bc, rev, self.sim.sy, self.sim.st)``;或 `_jump_sim_to` 里直接 `self._build_cache_key = None` 强制下次重建。`

**N6.【中】路径缓存 key 缺 `path_mode`:切换"路径模式"后画面不更新**
- 位置:`py/ty_sim_mixins/_draw_path_mixin.py:274-284`(_make_path_cache_key)、`settings.py:1652-1666`(path_mode 只 `_sync` 不失效缓存)
- 问题:key 含 point_size/版本/首尾点/len/smooth_segments 等,但**不含 `path_mode`**(及 `_line_mode()` 依赖项);设置应用 path_mode 变化时不调 `_invalidate_all_path_caches()`(仅 smooth 与 show_future_path 有失效)→ 缓存命中继续按旧模式渲染,直到视图版本变化。
- 触发:设置中把路径模式从"点阵"切"渐变线"应用后,路径外观不变(无缩放/拖拽时)。
- 修复提示词:`key 中加 `self.path_mode`(或 settings.apply_settings 中检测 path_mode 变化时调用 `self.sim._invalidate_all_path_caches()`)——注意 line_mode 在编辑模式被强制为 False(_line_mode:292-296),key 应直接取渲染分支实际使用的模式值。`

**N7.【中】平滑路径下编辑报点后:geo 样条缓存"先建后清",平滑曲线按旧位置渲染**
- 位置:`py/typhoon_render.py:22-36`(`_get_geo_spline` key=`(id(pts), len, id(pts[0]))` 对就地修改不敏感)、`py/ty_sim_mixins/utils_mixin.py:182-184`、`py/point_list.py:432-440`、`py/ty_sim_mixins/event_mixin.py:186-189`
- 问题:所有编辑路径都是先 `ty.update_screen_points(...)`(此时 `_get_geo_spline` 命中**旧**缓存生成 smooth_screen_points)**之后**才 `refresh_typhoon_after_point_change → _invalidate_path_cache_for_ty → _clear_geo_spline_cache`。点列表/对话框编辑只执行一次 → 平滑点永久停留在旧曲线(拖点场景靠后续帧反复 update 自愈)。
- 触发:`smooth_path=True` 时通过"修改报点"对话框或点列表改已有报点经纬度(就地改 dict,list 身份/长度不变)。
- 修复提示词:`把所有编辑路径的调用顺序改为"先 `_clear_geo_spline_cache(ty)` 再 `update_screen_points`"(三处:utils_mixin.py:183、point_list.py:436、event_mixin.py:186);或把 _get_geo_spline 缓存键改为含 pts 内容指纹(如首个被修改点的 id/值),在点编辑后强制失效。`

**N8.【中】config.json 中 `tn` 为 null/非 dict → 启动后任意 `save()` 崩溃**
- 位置:`py/config.py:134-178`(_coerce)
- 问题:`tn: Dict[str,str] = field(default_factory=dict)`,其 `fld.default` 是 `dataclasses.MISSING`;`_coerce` 对 `Dict[` 类型返回 `v if isinstance(v, dict) else fld.default`(175 行)→ 配置里 `"tn": null` 时 `cfg.tn = MISSING` → 之后 `cfg.save()`(json.dump)抛 `TypeError: Object of type _MISSING_TYPE is not JSON serializable`,在退出保存/设置保存/切换模式时崩溃。
- 修复提示词:`_coerce 中对 `t.startswith('Dict[')` 分支:`if not isinstance(v, dict): return fld.default_factory() if fld.default_factory is not MISSING else {}`;并给 save() 加异常保护(失败仅记日志,不中断退出)。`

**N9.【中】TSNoteEffect 在 SMCY 模式下可能永不结束(僵尸特效)**
- 位置:`py/particle_effect.py:209-220`、`py/playback_ctrl.py:86`(effects 过滤)、`py/renderer.py:77-81`(无条件绘制)
- 问题:SMCY 分支帧进度 `rel = (v._smcy_frame - _start_icon_frame) % _TOTAL_FRAMES`,完全依赖主图标视频帧推进;台风结束(sf)后 `_advance_smcy_frame` 不再被调用 → rel 冻结在 `[0, len(frames))` 内 → `update` 恒 True → 特效永远留在 effects 列表,静态帧永久绘制在台风最后位置(跨模式持续)。
- 触发:台风生命末期(最后 1-2 个报点)升格 TS,或升格后图标随即淡出。
- 修复提示词:`TSNoteEffect 增加真实时间上限:非 SMCY 分支已有时间推进,给 SMCY 分支补 `if current_time - self.start_time > 上限ms(如 2000): return False`(或当 `ty.v.icon_alpha <= 0` 时结束)。`

**N10.【中】`_draw_smcy_frame` 视频尺寸 (0,0) 时除零崩溃**
- 位置:`py/ty_sim_mixins/_draw_icon_mixin.py:418-422`
- 问题:`orig = mgr.get_size(cat, hemi) or (400, 400)` —— `(0,0)` 是非空 tuple 恒为真 → `scale = target / max(orig[0], orig[1])` → `0/0` ZeroDivisionError(同模块其它位置 smcy_icon.py 均显式防护,唯独此处遗漏)。
- 修复提示词:`改为 `if not orig or orig[0] <= 0 or orig[1] <= 0: orig = (400, 400)`。`

**N11.【中】脚本列表:点击项使用上一帧 `_hovered_idx`,可能运行错误的脚本**
- 位置:`py/script_dialog.py:763-766`
- 问题:`_hovered_idx` 只在 draw 中刷新;点击直接用该值运行脚本而非对 e.pos 做命中测试 → 鼠标快速移到另一行后点击,会执行上一帧高亮行的脚本。
- 修复提示词:`MOUSEBUTTONDOWN 时用 e.pos 遍历各行矩形(与 draw 相同的行布局公式)做命中测试,命中才 `_run_script(i)`。`

**N12.【中】脚本保存:扩展名错误(`script.txt.json`)+ 文件名路径穿越**
- 位置:`py/script_dialog.py:548-565`(_save_editor_script)
- 问题:`if not name.endswith('.json'): name += '.json'` —— 输入 "script.txt" 存成 "script.txt.json";文件名未消毒,`../../evil` 可写到 SCRIPT_DIR 之外。
- 修复提示词:`name 分离扩展名后统一替换为 .json(如 `name = os.path.splitext(name)[0] + '.json'`),并用 `os.path.basename(name)` 或正则 `[^\w\-. ]` 消毒后拼接到 SCRIPT_DIR,拒绝含路径分隔符的输入。`

**N13.【中】报点时间字段无校验,非法时间串直接写入数据文件**
- 位置:`py/point_list.py:390-394,478`(_apply_point_change/_save)、`py/point_edit_dialog.py:147-169`(submit 只校验经纬度)
- 问题:time 原样传入并写入文件;`t='abc'` 重载时 `recalc_simulated_times` 回退 2000-01-01(typhoon_data.py:135),数据文件被污染且时间信息永久丢失。
- 修复提示词:`在 point_edit_dialog.submit() 或 _apply_point_change 中对 `t` 做 `^\d{10}$` 校验(或至少 `datetime.strptime(t[:10], "%Y%m%d%H")` 可解析),非法则报错不提交。`

**N14.【中】长按退格加速删除不触发 on_change → 设置静默丢编辑(R7 不完整)**
- 位置:`py/input_field.py:143-168`(_handle_bs_accel)
- 问题:单次退格有 `_notify_change()`(367-376),但 500ms 后的加速删除(154-166 循环)与 `_delete_selection` 均无通知 → 设置页用 on_change 置 `_needs_save`,长按退格删掉的内容按 ESC 无未保存确认、静默丢弃。
- 修复提示词:`_handle_bs_accel 每次实际删除字符/选区后补 `self._notify_change()`(或在 `_bs_tick` 更新处统一通知一次)。`

**N15.【中】新建台风(空 pts)会被洋区过滤/重载丢弃**
- 位置:`py/data_repo.py:254-263`(apply_basin_filter)、`py/new_typhoon_dialog.py:136-137`(_all_tys_backup 追加)
- 问题:新台风 pts 为空,`any(area.contains(...) for p in ty.pts)` 对空列表为 False → 启用 ACE 洋区过滤时从 `self.tys` 消失,界面找不到刚建的台风。
- 修复提示词:`apply_basin_filter 的过滤条件改为 `if not ty.pts: 保留`(空 pts 台风豁免过滤);或在 new_typhoon_dialog 创建时给一个占位点。`

### 低危

**N16.【低】季节信息框缓存 key 缺 `point_name_mode`,切换逐点名称后名称不更新**
- 位置:`py/ty_sim_mixins/draw_info_boxes_mixin.py:128-134`(key_data)、`164-169`(tn 依赖 point_name_mode)
- 修复提示词:`key_data 补 `getattr(self,'point_name_mode',False)`(以及 name_display_mode);或设置应用这些项时清空 `_season_info_box_cache`。`

**N17.【低】summary_effect:窗口过窄时 `bar_w` 为负进 cv2/pygame 异常**
- 位置:`py/summary_effect.py:179-193`
- 修复提示词:`bar_w = max(64, min(self.BAR_H*20, max_w))`,并跳过 max_w<=0 的绘制。`

**N18.【低】`reload_typhoons` 不清空 `playback_ctrl.effects`,旧特效引用旧台风对象**
- 位置:`py/ty_sim_mixins/keyboard_mixin.py:18-70`
- 修复提示词:`reload 时 `self.playback_ctrl.effects = []`(及 summary_effect 的 `_slot_registry`/`_wait_queue` 相关状态)。`

**N19.【低】SMCY 图标帧长时间无绘制后一次性跳帧过多**
- 位置:`py/ty_sim_mixins/_draw_icon_mixin.py:402-407`
- 修复提示词:`advance 设上限(如 8 帧/次)并把 `_smcy_last_ticks` 前移。`

**N20.【低】台风列表"编辑名称/编号"不持久化**
- 位置:`py/ty_list.py:473-493`(_apply_edit)、`py/config.py:88`(tn 字段全工程无读取处)
- 修复提示词:`要么把 cust/n 写回数据文件首行注释或内容,要么实现 cfg.tn 的读写(load 时按 key 应用);当前 `save_config()` 只存了没地方读的 tn。`

**N21.【低】SH 多年度"月度 ACE"柱状图 x 轴标签与数据月序语义不符**
- 位置:`py/statistics/multi_year_chart.py:83-88`(数据本身正确 `(cal, buckets[m])`,但 chart_presets 用 MONTH_NAMES[i] 标注第 i 根柱 → 第 1 根(实为7月)标成"1月")
- 修复提示词:`SH 时按真实自然月显示标签(7..12,1..6)或按 cal 顺序标注,与多年累积曲线标签一致。`

**N22.【低】月度总结 TS/MH/C5 计数不排除非热带性质报点**
- 位置:`py/monthly_summary.py:139-144`(只按 w 阈值),与 season_stats.py:84-91 规则不一致
- 修复提示词:`计数前按 `st` 排除 MD/SS/SD/EX/LO(与 season_stats 相同谓词)。`

**N23.【低】`draw_yearly_ace_chart` 年数过多时柱宽为负**
- 位置:`py/statistics/chart_presets.py:474-475`
- 修复提示词:``bar_w = max(1, (w - bar_gap*(n+1)) / n)`,或按 `w // n` 计算。`

**N24.【低】`ocean_mgr._load` 文本路径逐行解析无异常保护,坏行导致启动崩溃**
- 位置:`py/ocean_mgr.py:133-137`(compact/geojson 路径有 try/except,文本路径没有)
- 修复提示词:`循环内包 try/except 跳过坏行(与 _load_compact 一致)。`

**N25.【低】台风列表行 surface 每帧重建(法15 缓存未生效)**
- 位置:`py/ty_list.py:554-580`
- 修复提示词:`哈希不变时直接复用 `_row_cache[oi]`,仅哈希变化才重新 render(参考 point_list.py:516-531 的实现)。`

## 三、基础设施与文档问题

**N26.【测试】pytest 无法运行:项目自身 `py/` 包遮蔽 pytest 的 `py.path` 依赖**
- 现象:`python -m pytest tests` 报 `AttributeError: module 'py' has no attribute 'path'`(site-packages 的 py.path 被本项目 py/ 包遮蔽,tests/__init__.py 使 pytest prepend 模式把项目根加入 sys.path)。
- 修复提示词:`加 pytest.ini/pyproject.toml 设 `[tool.pytest.ini_options] pythonpath = ["."]` 不解决问题时,改用 `--import-mode=importlib`;或把项目包从 `py/` 重命名(如 `app/`)——若重命名,全工程 import 引用需同步,收益是 CI/本地测试恢复。短期缓解:`python -m pytest tests -p no:cacheprovider --import-mode=importlib`(需验证)。`

**N27.【文档】README 快捷键表与代码不符**
- 现状:`J` 在代码里是脚本对话框(keyboard_mixin.py:289-291),README 写"历年统计";`T` 是时间跳转(_key_t),README 写"台风列表";台风列表是 `O`(_key_o);`K` 正常模式是强度折线图 ✓。
- 修复提示词:`按 keyboard_mixin.py 的实际绑定更新 README 按键表(J=脚本、T=时间跳转、O=台风列表、S=设置、G=点列表、P=截图、R=重置视图、F12=置顶、H=切换模式、I=新建、[ ]=切换台风)。`

## 四、修复顺序建议

1. **数据正确性**:N3(ACE 口径分裂)、N4(SH 路径长度)、N5(图表不刷新)
2. **编辑安全**:N1(幽灵撤销)、N2(脚本污染)、N13(时间未校验)、N14(退格丢通知)
3. **崩溃防护**:N8(config MISSING)、N10(除零)、N17(负尺寸)、N23/N24
4. **渲染一致性**:N6(path_mode)、N7(geo 样条顺序)、N9(僵尸特效)
5. **其余**:N11、N12、N15、N16 → N25,以及 N26(测试基建)、N27(文档)

> 注意:
> - N7 与 R5 同族(都是平滑缓存一致性);N14 是 R7 的漏网分支;N2 是 R9 引入的回归,修 R9 时务必同时处理 N2。
> - 修改前请以实际文件行号为准(上轮修复后行号已大量漂移)。
> - **执行状态(2026-08-02 核验)**:`py/` 已重命名为 `app/`,`pytest.ini` 已添加,`python -m pytest` 104 项全部通过(N26 已解决);本轮提示词中的文件路径一律以 `app/` 为准。

---

# F3.【功能】设置面板重新排版与优化(2026-08-02)

> 需求:对设置对话框(`app/settings.py`,约 1700 行)做整体排版重构 + 交互/性能优化。**只改设置面板相关文件(settings.py、app/constants/colors.py、必要时 app/input_field.py),不动其他界面与其他对话框**,不改动任何 ACE/播放/统计逻辑(行为必须与现状等价)。当前标签页:`SETTINGS_TAB_NAMES = ["通用","显示","地图","播放","ACE","数据"]`(app/constants/colors.py:79),保留 6 个标签页及其包含的所有设置项,一项都不能丢。

## 一、当前实现与问题

1. **坐标全靠魔法数字**:`_get_fields_config`(settings.py:486-550)用 `base_y = dialog_y + 95`、`y0..y5 = base_y + 30*n`;`_draw_tab_playback`(934-1014)用 `gap*6+28`、`gap*7+10+22`、`gap*10+15+22`、`gap*13+30+22`、`fc_y+34` 等互相避让的偏移堆叠——行高不一致、难以维护,任何一行增删都要重算全部偏移。
2. **控件列不统一**:checkbox 有的在 `dx+280`(fix_icon_point_size、show_ace_bar/ace_total),有的在 `dx + bg_rect.width - 50`(播放 tab 全部);开关组有的从 `dx+180` 起、宽 52/72/80/95/100/110/120 不等;标签左列统一 `dx+30` 但部分行 `y-1`、`y+5` 微调,垂直基线参差。
3. **播放 tab 密度过高**:半球 + 13 行开关 + 4 组模式选择挤在一页,且"平滑段数"输入框孤零零落在 `sg_y = base_y+13*30+30+22+34+30`(535 行),与"平滑"开关/模式行相距很远,用户不易建立关联。
4. **静态文本每帧重渲染**:`_draw_tab_*` 内 `dm_text = rt(f_m, ...)`(898 行)、`_tg`/`_draw_modern_button` 每帧 `rt(f_m, text)`(732、865 行)、tab 标签每帧 `rt(f_m, name, color)`(633 行)——打开设置期间每帧 30-60 次未缓存字体渲染(rt 本身有缓存但每帧查 key+blit 也浪费;且 `_draw_modern_button` 每次新建 surface)。
5. **悬停态使整块缓存困难**:tab 内容需要显示鼠标悬停/选中态,若整面缓存则悬停失效——需要"静态内容面 + 动态覆盖层"分离。
6. **滚动上限依赖 draw 顺序**:`_content_scroll_max` 在 draw() 末尾由 `_targets`/字段底部反推(673-678 行),若布局表化可直接由布局数据一次算清。
7. **"恢复默认"无二次确认**,误点即全部重置(已有 `_restore_defaults` 但无防误触)。

## 二、修复提示词

### A. 网格化布局系统(排版核心)

```
1) 在 Settings 内新增布局表驱动:
   - 常量:ROW_H = 30(行距网格)、LABEL_X = dx+30、LABEL_W = 210、CTRL_X = dx+250(字段输入框左列)、CHECK_X = dx+280(复选框列)、BTN_X = dx+180(开关组起点)、BTN_H = 22、FIELD_W/H = 80/24、SECTION_GAP = 18(分组间距)。
   - 定义纯数据布局:每个 tab = 一组行描述,行类型支持:
     a) field(key, label, validator)     → 生成 InputField(rect 由 (row, CTRL_X) 派生)
     b) checkbox(attr, label)            → 复选框在 CHECK_X
     c) toggle_group(attr, [(label,value)], btn_w) → 开关组从 BTN_X 起,按钮宽统一(同组相同宽度,如 90px)
     d) section(title)                   → 分组标题(小字+下划线/分隔线),行距加 SECTION_GAP
     e) note(text)                       → 灰色说明文字(12px,样式沿用 lonlat_note)
   - 所有行的 Y = content_top + row_index * ROW_H(+section 额外间距),滚动由统一的 row 偏移换算;字段 rect 与标签/控件 Y 从同一行索引派生,消灭 base_y+95 与 gap*N+M 魔法数。
2) 按功能重排各 tab(保持全部设置项,只改顺序与分组):
   - 通用:分组[速度]mis/mas;分组[音量]volume;分组[旋转]main_rot_speed/level3_rot_speed;分组[窗口]screen_width/screen_height(注明"即时生效"note)。
   - 显示:分组[大小]point_size/icon_size/name_size/peak_label_size;分组[主题]暗色/亮色 toggle、color_scheme toggle、fix_icon_point_size;note 说明。
   - 地图:分组[范围模式]两个模式 toggle(rebuild_fields 逻辑保留);分组[经纬范围/角点]字段;lonlat_note1/2 保留为 note。
   - 播放:分组[半球]hemisphere;分组[时间行为]ac、show_info_box_normal、show_info_box_season、monthly_summary;分组[消失]fade_typhoon、fade_path、fade_path_mode toggle;分组[平滑]smooth_path、smooth_path_mode、smooth_path_segments 字段(与开关同组!)、smooth_path_segments 行与平滑开关视觉紧邻;分组[路径]path_mode、show_future_path;分组[效果]show_ri_effect;分组[帧率]show_fps、fps_cap。
   - ACE:分组[限制]ace_limit_mode 三按钮、ace_limit_basin 下拉+过滤开关+note;分组[范围]latlon 字段;分组[显示]show_ace_bar/show_ace_total。
   - 数据:分组[图标]icon_set、icon_set_warn note;分组[名称]name_display_mode、point_name_mode。
3) 对齐规则:
   - 所有行首元素垂直居中统一 _lh(22);label 宽固定 210,超长文本在 _pre_render_texts 阶段 wrap;
   - checkbox 列统一 CHECK_X(不再用 dx+bg_rect.width-50);
   - toggle 组按钮宽统一(同组 90px,文字多可用 110px,组内一致);
   - 说明文字统一用 note 行类型,与控件的间距固定。
4) 滚动与字段:
   - _content_scroll_max = max(0, 布局总高 - content_h),由布局表一次算得,不再从 _targets 反推;
   - 字段 rect 在 rebuild_fields 时由布局表生成(_field_offsets 保留但来源统一);
   - 点击命中与绘制沿用现有 `rect.move(0,-scroll)` 方案,不做行为变更。
5) "恢复默认"加二次确认:点击后弹确认条(复用 _close_confirm 条样式),确认才执行 _restore_defaults。
```

### B. 性能优化

```
1) 静态文本全部移入 _pre_render_texts:
   - _draw_tab_display 的 dm_text(主题配色标签)等所有"纯标签"改为预渲染属性;
   - _tg/_draw_modern_button/_draw_cb 的按钮文字改为按 (text, dark, accent, 尺寸) 缓存 surface(模块级 dict,上限 64,复用 _light_panel_cache 模式);
   - tab 标签文字按 (name, color) 缓存(rt 已有缓存,但颜色随 hover 变化,建议按 名称×活跃×hover 3 态缓存 3 份)。
2) tab 内容双层渲染:
   - 静态层:整页控件(标签/复选框框/开关底/按钮底/字段底)烘焙到一张 per-(tab_index, dark_mode, 相关设置值hash) 的内容 surface(缓存上限 8,设置变更时失效);
   - 动态层:每帧只画悬停高亮、选中态、下拉框、字段文字/光标(字段文字由 InputField.draw 负责,注意与静态层 z 序一致);
   - 失效时机:对应设置值变化(如 dark_mode、icon_set、ace_limit_mode)或 rebuild_fields 时清空该 tab 缓存;滚动只改 blit 偏移,不重建。
   - 注意:悬停态(rect.collidepoint(mx,my))属于动态层,静态层不得包含 hover 高亮,否则缓存失效。
3) 滚动条 thumb 尺寸/位置改为布局表派生常量,每帧只算一次(现为每帧 2 次除法,可忽略但顺手统一)。
4) 打开/关闭设置不触发 rebuild_fields 之外的重型调用;避免在 draw() 内调用 _refresh_texts。
```

### C. 交互优化(行为等价前提下的增强)

```
1) 键盘:↑/↓ 在字段间移动焦点(可跨行,含当前 tab 全部字段),Enter 跳到下一字段(保留现逻辑),Tab/Shift+Tab 循环(保留),ESC 关闭(保留);
2) 字段聚焦时高亮边框加粗(现有 active 边框即可,不新增);
3) 恢复默认二次确认(见 A5);
4) 窗口尺寸字段(screen_width/height)在显示 tab 内保持原位,行内加 note"修改后立即重排窗口";
5) 洋区下拉:悬停行高亮保留;下拉列表改为可随内容 tab 滚动整体滚动(现列表固定在 y+75 不随 scroll,重排后统一)。
```

## 三、验证清单(实现后逐项确认)

1. 6 个 tab 全部设置项无遗漏、无重复;800x600 与 2560x1540 两种窗口下无重叠、无截断(超出部分可滚动)。
2. 字段文本在切 tab/切 ACE 模式后保留(_flush_fields 行为不变);Enter/ESC/OK/关闭/丢弃/恢复默认全部走通。
3. 打开设置面板期间 FPS 不降(对比优化前后);无每帧未缓存 render(可临时在 draw() 打点统计 rt 调用次数,目标:打开面板时每帧 0 次新建,全部命中缓存)。
4. 悬停/选中高亮正常(双层渲染后 hover 态不受缓存影响)。
5. 中文输入(TEXTINPUT)在字段中正常插入;N14(长按退格通知)与 N3(ace_geo_limit_enabled 回写)不回归。
6. `python -m pytest` 仍 104 项通过。
7. 确认/丢弃的即时生效项(盆域过滤、暗色主题)行为与现状一致(_apply_filter_now/_restore_immediate_applied 逻辑不动,仅坐标与缓存变化)。

> 注意:
> - 本提示词只改"表现层"(布局、对齐、缓存、静态文本),**不得改变 apply_settings 的校验顺序与赋值逻辑、不得改变任何设置项的取值语义**;
> - 布局表化后,`_get_fields_config`、`rebuild_fields`、`_sync_field_positions`、`draw` 的滚动计算、`handle_event` 的字段命中测试共 5 处需同步修改,确保坐标来源唯一;
> - 若 tab 高度超出视口,分组标题也要参与滚动(与内容同一滚动容器),避免"标题滚走、内容留下"的错位。

---

# 性能瓶颈复审(2026-08-02 第二轮)

> 范围:对当前代码(app/ 目录)重新走查每帧/事件/跳转热路径,核验上轮"算法复杂度优化提示词"(法1-法30)实施状态,并查找新瓶颈。结论:**法1-法30 已全部实施**;本轮新发现 2 个中优先级 + 1 个遗留 + 1 个 SMCY 专项,外加若干已确认无需处理的项。数据规模参考:400-800 台风文件、总点量 P≈1-4 万、活跃台风 3-15 个(季节模式)。

## 一、上轮法1-法30 实施核验(全部 ✅)

| 条目 | 核验证据 |
|------|----------|
| 法1 bbox 仅重建时算 | _draw_path_mixin.py:354-365(box 计算在缓存失配分支内) |
| 法2 脚本 MOVING 保留全量重投影 | script_engine.py:657 附近仍用 update_all_screen_points(设计保留) |
| 法3 ACE 图排序缓存 | typhoon_ace_chart.py:44-62(key 含 id(src)/page/sort_mode/size,命中直接切片) |
| 法4-7 ACE 引擎 | ace_engine.py:125-163(_year_events 前缀和+bisect)、refresh_all 受影响台风局部重算(312-360)、season_ctrl/typhoon_sim 用 points_dt 二分(season_ctrl.py:197-220、typhoon_sim.py:167-184)、dialog_chart.py:163-173(记忆化) |
| 法8 紫滤镜 | _draw_icon_mixin.py:410-437(_purple_frame_cache 按 (cat,hemi,idx,size,tier) 缓存) |
| 法9 ACE 数字 | draw_info_boxes_mixin.py:58-59、296-301、430-442(_ace_digit_cache) |
| 法10 SMCY 最近邻二分 | smcy_icon.py:151-173(bisect_left+回绕候选) |
| 法11 月度线烘焙 | chart_presets.py 相关(agent 复审确认 build_month_lines_surface 复用) |
| 法12/13 ocean_mgr | ocean_mgr.py:110(_by_code 字典)、83-92(bbox 预检) |
| 法14 热力图 numpy | heatmap.py:169-190(numpy LUT,无 numpy 时纯 Python 回退) |
| 法15 ty_list 文本缓存 | ty_list.py:162-178(_build_row_texts_cached;**但 surface 仍每帧重建,见法32**) |
| 法16 脚本解析防抖 | script_dialog.py:505-516(_parse_dirty+200ms) |
| 法17 max_wind 缓存 | _draw_icon_mixin.py:718-722(ty._cached_max_wind) |
| 法18/19 repeat 模板/计数 | script_engine.py:386-403、281-282 |
| 法20 插值 csa 缓存 | playback_ctrl.py:202-210(_ipol_key 桶) |
| 法21-25 统计单趟化 | season_stats.py:74-105(单趟)、multi_year_chart.py:94-110(单趟分桶)、_parse_dt lru(ace_engine.py:20-26)、chart_presets bar_w |
| 法26/27 索引缓存 | ty_list.py:130(_pos_map)、keyboard_mixin.py:195-203(_edit_ty_idx) |
| 法28 点列表修订 token | point_list.py:101-105(_row_rev) |
| 法29 特效淡化/旋转缓存 | landfall_effect.py:118-176(16级alpha+4°桶)、particle_effect.py:97-147(RI) |
| 法30 时钟 trig LUT | draw_info_boxes_mixin.py:31-42(4096级 LUT) |
| 其它已确认 | 描边文字缓存(draw_info_boxes_mixin.py:61-74)、_size_factors 按 key 缓存(_draw_icon_mixin.py:117-127)、SMCY 解码预算+LRU(smcy_icon.py:146-193)、map pan 缓存+陆地掩码 180ms 延迟(map_mgr.py:152-169、307-331)、滚轮合并+缩放 burst(ty_sim.py:443-476) |

## 二、本轮新发现瓶颈(按优先级)

### 法31.【中·每帧】台风图标旋转 1° 分辨率 → 每帧每台风 2-3 次 transform.rotate
- 位置:`app/typhoon_render.py:75-92`(_get_rotated,key=`int(angle) % 360`)、`app/typhoon_render.py:102-111`(update_rotation)
- 现状:主环 180°/s=3°/帧、3 级环 270°/s=4.5°/帧,`int(angle)` 每帧都变 → 每帧每台风对 ring/center/level3 各 rotate 一次(季节模式 10-15 个活跃台风 ≈ 30-45 次/帧,SRCALPHA 面 70-208px,约 1-3ms/帧,是场景绘制中最大单点开销之一)。
- 优化提示词:`_get_rotated 的 key 改为 `(key_prefix, id(img), (int(angle) // 6) * 6 % 360, mirror, tint)``(6° 桶;主环 3°/帧时旋转次数降 ~50%,3 级环 4.5°/帧时降 ~33%;视觉步进 ≤67ms 在旋转中不可察觉)。注意:只量化缓存 key,不改 `v.sa/v.sa3` 的累加值与 `update_rotation` 的推进公式,动画平滑度不变;`_img_cache` 720 上限无需调整(桶数 60×图标数仍远小于上限)。`

### 法32.【中·每帧】SMCY 模式:活跃台风视频帧未做运行时预载,逐帧 seek/解码
- 位置:`app/smcy_icon.py:229-238`(get_frame)、`app/ty_sim_mixins/_draw_icon_mixin.py:391-407`(_advance_smcy_frame)
- 现状:`preload_window`(smcy_icon.py:195-198)只在启动时对主图标流调用一次;运行中每帧对每可见台风 get_frame(新帧号→解码+缩放,受 128ms 解码预算约束,超预算时复用最近邻帧,可能产生"卡顿-跳帧"观感)。
- 优化提示词:`在 `_advance_smcy_frame` 推进帧号时,若 `(frame_idx+30) % _TOTAL_FRAMES` 不在缓存且预算允许,调用 `mgr.preload_window(cat, hemi, next_frame, 30)`(API 已存在,仅需在运行时接入);或把启动预载改为"台风激活时预载其帧窗口+类别切换时重新预载"。收益:避免播放中 seek 抖动,解码集中到空闲帧;注意预载与 _DECODE_BUDGET 共用预算,勿互相抢占(可在 update 阶段而非 draw 阶段预载)。`

### N25.【低·每帧·对话框】台风列表行 surface 每帧重建(上轮遗留)
- 位置:`app/ty_list.py:156-160`(_get_row_hash 内每次 rt() 2 个 surface)、`583-588`(draw 每行调用)
- 现状:文本缓存(_build_row_texts_cached)已生效,但 surface 仍每帧新建 dict+2 次查缓存(rt 命中) → 每帧 10 行×2 次无谓查表与分配。
- 修复提示词:`仿 point_list.py:516-531:`哈希变化时才调 `_row_surfs` 建 surface,命中直接 `self._row_cache[oi]`;把 `_get_row_hash` 拆成 `_row_hash(ty)`(纯字符串)与 `_row_surfs(ty)`。`

### 微优化清单(可选,收益小)
1. **`_check_ri_effect` officials 重建**:playback_ctrl.py:378 每台风每次 ci 前进 O(n) 重建官方报索引列表 → 台风级 `_officials` 缓存,在 rst/点变更(与 _clear_row_cache 相同失效点)时置空。节省:30 活跃台风×每秒 1 点 ≈ 每秒 2400 次迭代,收益微小。
2. **season `_pending` 逐帧线性扫描**:season_ctrl.py:124-156 每帧遍历全部未激活台风做 datetime 比较(400 台风≈400 次/帧,<0.1ms)→ 可按 start_dt 排序+索引跳过未到期,收益极低,不建议改。
3. **`control_panel`/`_draw_progress`/monthly_summary 的 rt()**:均有缓存命中,已确认无需处理。
4. **`draw_season_clock` 每帧 120 点弧线**:LUT 已消除 trig,绘制本身 <0.1ms,无需处理。

## 三、优先级建议

1. **法31(图标旋转量化)**:改动 1 行,收益稳定 1-3ms/帧,建议先做。
2. **法32(SMCY 运行时预载)**:仅 SMCY 图标集受影响,用户已知该模式重,建议在 SMCY 预载逻辑内做。
3. **N25(ty_list 行 surface)**:与 F3 设置面板优化同族(对话框每帧渲染),可一并处理。
4. 微优化 1-4:可做可不做,不阻塞。

> 结论:主循环稳态每帧开销已高度收敛(路径/信息框/ACE 条/特效/文字全部有缓存),当前唯一值得动的每帧大项是图标旋转(法31)与 SMCY 解码(法32);其余为对话框打开/事件级开销,已在此前轮次覆盖。
---

---

# F4.【功能】架空模拟器 — 阶段1:气候模态层(2026-08-02)

> 背景:新程序"台风架空模拟器"(独立程序,pygame 可视化,输出标准 `.dat` 由原程序回放)的**阶段1**——气候模态序列生成。输入:当前时间点之前各气候模态的实测值;输出:未来 2-3 年(接续现实)+ 3 年后(架空随机)的逐月模态指数序列,供阶段2(环境场)驱动。**本阶段只做模态序列与组合合理性,不涉及 SST 场、台风、云图**。
>
> **统计基准数据源(用户指定,阶段0/6 与阶段1 校验共用)**:历史最佳路径官评统一取自 `G:\气象\数据\xrq\`:
> - 结构:按年目录 `1949`-`2025` 内 `bwp{序号}{年份}.dat`(西太平洋 WP)、`wp\` 下 `bcp*.dat`(中北太平洋 CP)、`sh\` 下 `bsh*.dat`(南半球 SH,始于 1979);
> - 格式与现有程序 `.dat` 一致(`盆地, 序号, YYYYMMDDHH,  , XRQA, 0, 纬度, 经度, 风速, 气压, 性质`),可直接用 `app/data_repo.py` 的解析逻辑读取;
> - **规则:只使用 2000 年及以后的官评(2000-2025)作为统计基准**,2000 年以前(尤其外洋)不做评价依据;当前基准覆盖 WP/SH/CP 三盆地,AL/EP(北大西洋/东北太平洋)与地中海暂无基准数据,生成这些盆地时以气候学常识参数为准,后续补数据再校准。

## 一、输入与数据准备

```
1) 模态指数实测数据(每个模态一份,抓取一次归档为本地缓存,离线可用):
   - 建议模态集合:ENSO(Nino3.4 SST 距平 ONI)、SOI、PDO、AMO、IOD(DMI);可选:NAO、QBO(30/50hPa 风)。
   - 数据源:NOAA PSL/CPC 公开接口(如 psl.noaa.gov 的 .csv/.txt 下载),抓取失败时降级为内置占位并提示。
   - 归档格式:每模态一个 CSV(年月、指数值),带抓取时间戳;程序启动时若本地有归档则离线加载,允许手动更新。
   - 处理发布滞后与修订:记录抓取日期,旧归档不自动覆盖新值(修订版保留)。
2) 缺失值处理:插值(月内线性)或标注缺失,不静默填 0。
3) 基准统计:用 1950 年以来(或归档起始)的实测序列计算各模态的月气候态、月标准差、一阶自相关(逐月)、ENSO 3-7 年准周期谱特征,供生成器与校验使用。
```

## 二、设计提示词

```
1) 模态定义表(纯数据驱动):
   每个模态一行:{名称, 指数单位, 典型月气候态, 典型月std, AR系数(按月), 与其它模态的联合约束, 生成优先级}。
   生成器只读此表,新增模态 = 新增一行。

2) 序列生成(两段式):
   - 接续段(前 2-3 年):从最近实测值出发,按月 AR(1)/AR(2) 外推:
     a) 逐月自回归系数按月分别拟合(ENSO 春季可预测性障碍:春季自相关明显下降,外推幅值随之收敛);
     b) 残差 = 正态噪声 × 月 std × (1 - AR²) 开方,保证方差不漂移;
     c) 季节锁相约束:如 IOD 只在 5-11 月发展/消亡,冬季强制归零;SOI 与 Nino3.4 反向符号约束(硬约束)。
   - 架空段(3 年后起,完全随机):
     a) 每个模态按"频谱合成+马尔可夫位相转移"生成:如 ENSO 用准 3-7 年周期的正弦叠加+噪声(周期与相位从历史谱估计),偶尔发生位相转换(El Niño↔La Niña 转换概率从历史转移矩阵统计);
     b) PDO/AMO 用更慢的低频过程(10-30 年准周期)或随机游走+均值回归;
     c) 各模态先独立生成,再做组合评分,低分组合按概率重抽(见 3)。

3) 组合合理性评分(软约束,不硬禁):
   - 硬约束(直接拒绝重抽):同一模态绝对值超过 3×该月标准差(3σ)范围;Nino3.4 与 SOI 符号相反(以 Nino3.4 符号为准,SOI 反号);IOD 出现在非季内(12-4 月 DMI 异常显著非零且持续 2 月以上)。
   - 软评分(加权求和,重抽阈值):
     a) ENSO-IOD 联合:历史 DMI-ONI 同期相关(暖事件时正 IOD 概率高),偏差计入扣分;
     b) PDO 对 ENSO 频率的调制:PDO 暖位相时 El Niño 占比升高(按历史条件概率);
     c) AMO 位相对西太/大西洋活动的长期调制(弱权重);
     d) 与历史组合的相似度:对 (ONI, SOI, PDO, AMO, DMI) 五元组用历史分布的核密度(或马氏距离)打分,低密度区扣分;
     e) 年际持续性:同模态相邻年变化量超出历史 95 分位扣分。
   - 每次生成最多重抽 N 次(如 8 次),仍不达标则取评分最高者并打标"低置信",不无限重抽。

4) 输出:
   - `modes_series.json`(或 CSV):逐年逐月 × 各模态指数;每行附"生成段标记"(real/continuation/synthetic)与随机种子;
   - `modes_summary.json`:每年/每季的模态状态摘要(如 "2027 秋:中等强度 El Niño + 正 IOD + PDO 暖位相"),供界面状态条与阶段2 显示;
   - 每次生成记录:{全局种子, 各模态子种子, 重抽次数, 低置信标记},保证可复现与可追溯。

5) 校验(阶段1 自检):
   - 生成序列与 1950-今实测序列统计对比:逐月分布(KS 检验)、一阶自相关、ENSO 准周期谱、ENSO-IOD 联合分布、位相转移矩阵——偏差大于阈值的模态标红提示;
   - 提供模态曲线图数据接口:历史实况段 + 模拟延伸段一条曲线画出,供界面展示。
```

## 三、验证清单

1. 接续段起点与最新实测值连续(无跳变);2-3 年后平滑过渡到合成段。
2. 硬约束全部满足;软评分 ≥ 阈值的组合占比可调(界面有随机参数面板)。
3. 同一种子重跑结果一致;不同种子结果差异明显。
4. 模态摘要文本可直接用于界面状态条(中文,短句)。
5. 离线可用(断网时用本地归档)。

---

# F5.【功能】架空模拟器 — 主程序界面设计(2026-08-02)

> 需求:新模拟器主程序(独立 pygame 程序)的可视化界面。**必须能显示:当前台风云图、当前全球 SST、当前全球云图(以及后续 OHC/风切变/海压/ITCZ 等环境场)、模态状态、台风位置与路径**。UI 风格参考原回放程序(暗色主题、MapleMono 字体、圆角控件)与 `G:\气象\工具\风压对应`(上部场图+等值线+风圈+路径叠加、中部信息、下部操作区的三段式),但**布局必须按模拟器功能重新设计,不复用原程序的对话框栈与主循环结构**。本提示词只描述界面骨架与交互,数据由阶段2-5 逐步接入。

## 一、整体布局(五区)

```
┌────────────────────────────────────────────────────────────┐
│ ① 顶栏:程序名 | 当前模拟时间(年-月-日-时) | 模态状态摘要 | 种子显示 │
├────────┬─────────────────────────────────────┬─────────────┤
│ ② 左栏 │       ③ 中央主视图(全球地图)         │ ④ 右栏      │
│ 图层    │  当前图层渲染 + 台风符号/路径叠加    │ 选中台风详情 │
│ 控制    │  (滚轮缩放 / 右键拖拽 / 等距圆柱投影)│ 环境场摘要   │
├────────┴─────────────────────────────────────┴─────────────┤
│ ⑤ 底栏:时间轴(年/月/6h 步进·播放·倍率·跳转) | 生成控制按钮组 │
└────────────────────────────────────────────────────────────┘
```

## 二、分区设计提示词

```
1) 顶栏(40px 固定):
   - 左:程序名"台风架空模拟器"(副标题:当前阶段与数据版本);
   - 中:当前模拟时间(YYYY-MM-DD HH:00)+ 模拟进度条(总模拟时长);
   - 右:模态状态摘要(取 F4 的 modes_summary 文本,如 "2027 秋:中等强度 El Niño + 正 IOD")+ 随机种子号(可点击重新随机);
   - 全局状态色:生成中(琥珀)/正常(默认)/校验异常(红)。

2) 左栏·图层控制(260px 可折叠):
   - 图层列表(单选,每项:名称 + 快捷键):
     L1 台风云图(红外增强)   L2 台风云图(可见光)   L3 全球云图(合成)
     L4 全球 SST             L5 OHC               L6 垂直风切变
     L7 海平面气压+副高线    L8 ITCZ/季风槽       L9 台风符号+路径(始终可叠)
   - 每层选项:透明度滑条、色标(图例)、等值线开关(气压场/切变场);
   - 叠加开关:台风符号/路径/风圈半径(R34/R50/R64)是否叠加到当前图层;
   - 底部:模态叠加开关(在当前图上叠加模态异常色块,如 ENSO 暖区/冷区);
   - 面板可折叠为窄条,快捷键 H 隐藏全部 UI。

3) 中央主视图(等距圆柱投影,0-360°E / -90-90°N,与回放程序地图同投影):
   - 渲染当前图层为 numpy 贴图→pygame Surface(复用热力图/地图渲染思路),每帧只 blit,图层数据变化才重生成贴图;
   - 台风:符号(按强度分级着色,复用回放程序类别色)+ 历史路径线(已走实线/未来虚线)+ 风圈(选中台风画 R34/R50/R64 圆);
   - 置换状态可视:置换中台风画"双眼墙示意环"(外眼虚线环),失败置换(9711式)闪烁提示;
   - 交互:滚轮缩放(以鼠标为中心)、右键拖拽平移、左键点选台风(命中半径≥10px);
   - 云图动画:时间推进时云图帧按模拟 6h 步进切换(预渲染缓存帧,避免卡顿)。

4) 右栏·信息面板(320px 可折叠,分两段):
   - 选中台风段:名称/盆地/编号、当前位置(经纬度)、风速+等级、气压、阶段状态(生成/加强/置换中[普通|融合|失败]/减弱/变性/登陆/消散)、R34 等风圈、历史峰值;
   - 环境场段(随鼠标位置或选中台风):该点 SST/OHC/切变/引导气流(风向箭头+速度)、PI 潜力强度 vs 实际强度;
   - 若未选中台风:显示全局环境场摘要(全球 SST 均值/强切变区/ITCZ 位置)。

5) 底栏·时间轴与生成控制(70px):
   - 时间轴:◀◀ | ◀(6h) | ▶ 播放/暂停 | ▶▶(1天) | 倍率(1/2/4/8) | 跳转(输入 YYYY-MM-DD);
   - 生成控制组:【开始模拟】【重新随机(新种子)】【模拟内核】(默认:合成架空[内核B];
     可选:WRF 精算[内核A],未配置时置灰并提示)+【台风生成模式】(决定生成点/决定生成时间/两者同时/完全随机——下拉)+【仅生成不渲染】(批处理模式);
   - **模式 A/C 的交互输入(与 F7 对接)**:选择"决定生成点/两者同时"后,左键在主视图点选生成点(图上画十字标记,可拖动调整),点选后弹出时间输入框(或时间轴当前时刻作为生成时间);选择"决定生成时间"时,时间输入框在底栏激活;
   - 状态条:当前层阶段进度(模态→环境场→台风→云图)与耗时。

6) 模态面板(从右栏或顶栏呼出,模态曲线图):
   - 每条模态一条曲线:历史实况(2000 年至今)+ 模拟延伸(2-3 年接续段 + 架空段,两段用不同线型/颜色);
   - 选中曲线可放大;显示当前值/气候态/±1σ 带;组合评分实时显示。
```

## 三、交互与视觉提示词

```
1) 快捷键:Space 播放/暂停、←/→ 6h 步进、[ ] 1 天、1-9 切换图层、T 时间跳转、M 模态面板、
   H 隐藏/显示 UI、P 截图(picture/)、ESC 关闭弹出面板、F12 窗口置顶(沿用回放程序逻辑)。
2) 控件:沿用回放程序视觉语言(暗色面板、圆角按钮、开关、滚动条、MapleMono 字体),但布局与状态机全新;
   弹出面板(模态图/设置)用轻量"浮层面板"(可拖拽、可关闭),不复用回放程序的对话框栈。
3) 数据接入约定:
   - 所有图层渲染函数统一接口:render_layer(layer_id, sim_time) -> pygame.Surface(缓存 key 含 sim_time 与相关参数);
   - 时间轴与各层数据按 6h 对齐;云图帧按需预渲染(前瞻 24-48h 窗口),后台线程生成不阻塞主循环;
   - 模拟进程与渲染解耦:生成阶段跑批处理(进度回调),渲染阶段 60fps 只读数据。
4) 首版可先做骨架:图层框架+时间轴+台风叠加+空数据占位;再逐层接入(云图→SST→切变→海压)。
```

## 四、验证清单

1. 全屏(2560×1540)与 1366×768 下五区布局无重叠、无截断;图层面板/右栏可折叠。
2. 图层切换即时生效;透明度滑条实时;缩放/平移与回放程序手感一致;点选台风命中准确。
3. 时间轴播放时云图/台风位置按 6h 步进推进,无跳变卡顿;跳转任意时间点正常。
4. 快捷键全部生效;截图输出到 picture/;模态面板曲线历史+模拟两段线型可区分。
5. 批处理模式(仅生成不渲染)可后台完成一轮完整模拟,结束后提示可载入回放。
6. 未接入数据的图层显示"该图层数据未生成"占位,不崩溃。
```

> 说明:阶段1(F4)与界面骨架(F5)可并行开发;F5 首版骨架不依赖阶段2-5 的数据,只定义接口与占位。后续阶段提示词(环境场/台风生成/模拟/云图)待本阶段完成后按同样格式输出。

---

# F6.【功能】架空模拟器 — 阶段2:环境场层(2026-08-02)

> 决策(用户拍板):**真实场库 + 相似法(analog)** 为基底 / 库范围 **1979-今** / **OHC 必须真实计算,不接受 SST 近似** / 逐月场 + 日插值 / **副高·ITCZ·季风槽单独参数化**(不与场库强制一致)。
> 领域约束(用户提供,SST 与 OHC 的本质区别):**SST = 表层海水温度;OHC = 海水热焓量(深度积分)**,两者独立。小环流+高速台风:外围雨带几乎不消耗 SST,表层蒸发几乎全部用于 CDO 与风眼,强度几乎只受 SST 约束;大环流+普通/低移速台风:OHC 远比 SST 重要——例:2025 白海豚走 31-32°C 超高温海水,因环流太大+不成形外眼,深层 25-27°C 冷水被 Ekman 抽吸上翻,内眼迅速冷崩,对流只剩半圈 LG、另半圈 OW。**此约束的直接影响:OHC 场必须独立生成并进入强度模型(阶段4),不能与 SST 绑定;阶段4 将按"环流大小×移速"区分 SST 驱动型与 OHC 驱动型。** 风迷 SST 色标(显示与校验用):<26.5°C 冷水 / 27-28.5°C 温水 / 29-30.5°C 热水 / ≥31°C 开水;LG/OW 为 IR-BD 色阶术语(浅灰=弱对流云顶,OW=开阔水面/无云),用于阶段5 云图与 IR 增强显示。

## 一、输出清单(与阶段3-5 的契约)

```
逐日场(逐月场线性插值,60°S-60°N / 0-360°E / 1°×1°):
  1) SST             —— 海表温度(表层,驱动小环流台风 PI)
  2) OHC             —— 0-700m 热焓 ∫ρ·cp·T dz(独立于 SST,驱动大环流台风)
  3) 垂直风切变       —— |V(200hPa)-V(850hPa)|
  4) 中层湿度         —— 700hPa 相对湿度
  5) MSLP            —— 海平面气压(副高形态、OCI 环境气压喂 KZC/Chavas)
  6) 引导气流 U/V     —— 850-300hPa 深度平均风(三层质量加权;路径用,勿用单层 850hPa)
  7) 派生:850hPa 涡度、GPI 场(标准公式)、海温距平(相对 1991-2020 气候态)
逐月参数化(独立模块,系数由场库标定,见第四节):
  8) 副高:脊线纬度、强度(中心距平)、东西延伸经度范围
  9) ITCZ:中心纬度+半宽、强度(对流活跃度)
  10) 季风槽:位置(经纬带)、强度
元数据:每场附 数据源/覆盖/插值方式/置信标记;SST 色标按风迷惯例(冷水/温水/热水/开水)供界面显示。
查询接口(阶段3/4/5 只依赖此接口,不直接碰文件):
  get_field(var, date) -> np.ndarray   # var ∈ {sst, ohc, shear, rh700, mslp, uv_steer, vort850, gpi, ssta}
  get_param(name, date) -> dict        # name ∈ {ridge, itcz, monsoon_trough}
```

## 二、数据与建库

```
1) 数据源(下载管线参考 G:\气象\工具\风压对应 的 GFS/ERA5 下载与 .npz 缓存实现):
   - SST:ERA5 sea_surface_temperature,月平均,1979-今;
   - OHC:实现时**先核实 ERA5 分层海温的深度层与覆盖年份**;若覆盖不足(如 1979 前缺层),
     主源切换为 ORAS5(1958-今,0.25° 重采样到 1°),重叠期做交叉校准;热焓 = Σ ρ·cp·T·Δz
     按 ERA5/ORAS5 提供的标准深度层积分(ρ≈1025 kg/m³、cp≈3985 J/(kg·K) 常数近似,在元数据
     中记录所用深度范围与近似),缺层按深度加权外推并标注,不得用 SST 距平代替;
   - MSLP、850/500/300hPa 与 200hPa 风(U/V)、700hPa 相对湿度:ERA5 月平均,1979-今
     (500/300hPa 用于深度平均引导气流,必须与 850hPa 同源同网格);
   - (可选)OLR/云量:用于 ITCZ 校验与云图背景参考。
2) 分辨率与存储:1°×1°(60°S-60°N 共约 4.3 万点);存储布局为 `{变量}_{年份}.npz`
   (如 sst_1979.npz),每文件含 12 个月面;模态缺失月单独记录不静默填 0;
3) 模态对齐:1979-今每个月存 {模态向量(ONI/SOI/PDO/AMO/DMI,标准化), 场快照引用},
   约 550-560 个月;可选扩展:ENSO Modoki 指数 EMI/Niño3/Niño4(CP 型 ENSO 对西太活动
   重要),若阶段1 提供则并入匹配向量;
4) 月气候态与月标准差:同库计算(1991-2020 基准),供距平/校验/显示;
5) 模块划分(实现时按此结构,便于测试与复用):
   downloader.py(抓取/重试/断点) → build_library.py(建库离线批处理) →
   generator.py(analog 匹配+插值) → param_fields.py(副高/ITCZ/季风槽参数化) →
   field_io.py(npz 读写、get_field/get_param 接口、日插值)。
```

## 三、Analog 生成器(核心)

```
1) 候选池季节强约束(最重要,缺失会跨季错配):目标月 m 的候选池先限定为
   "历史同月 ±1 个月"(如目标 7 月 → 历史 6/7/8 月),再在池内按模态距离排序;
   1 月的场与 7 月的场结构差异远大于模态差异,禁止跨季匹配。
2) 模态匹配向量(考虑响应滞后):模态对场的响应滞后 1-3 个月(如 ENSO 对 SST 场的
   响应峰在事件发展后期),匹配向量用模态时间窗 [m, m-1, m-2] 展平(标准化),
   用模态协方差做马氏距离;实现时允许"分场敏感子集"(SST 场侧重 ONI/PDO,
   切变场侧重 ONI/IOD,湿度场侧重 IOD)——先按统一向量实现,敏感度分析后再拆。
3) 降级链(极端组合候选不足时,按序降级,不允许空结果):
   a. 同月±1 → b. ±2 个月 → c. 全季(±3 个月) → d. 全局 top-K → e. 气候态+合成扰动;
   每次降级在日志记录原因。
4) 时间连贯性(防月间跳变):
   - 轨迹记忆:目标月 m 优先从"目标月 m-1 所选历史月的时间邻居(±3 个月,且满足季节约束)"中选;
   - 片段复用:在历史模态序列上对目标未来 3-12 个月轨迹做滑窗搜索(标准化欧氏距离),
     最相似片段整段借用其场序列,再对"目标与借用段的模态差"做逐月线性残差修正;
     片段复用优先于逐月匹配(整段连续性最好),失败再退回逐月。
5) 混合与插值:主月 80-90% + 次相似月 10-20% 加权;逐月场之间按日线性插值得到逐日场;
6) 天气尺度扰动:阶段2 不生成(月尺度 GPI 不需要),留给阶段3 生成扰动与阶段5 云图
   (阶段5 按参数化副高/ITCZ 位置合成天气尺度云系,见第四节);
7) 引导气流与切变等派生量一律由插值后的风场计算(与场同步),不另做诊断;
8) 生成日志:每月输出匹配摘要(候选池规模/选中的历史月/降级原因/置信),存 log 文件,
   界面"模态面板"可追溯查看为什么选这些月。
```

## 四、派生场与参数化模块

```
1) 派生(场库内计算):切变 |V200-V850|;GPI = f(SST, 切变, 700hPa 湿度, 850hPa 绝对涡度)标准公式;
   ITCZ/季风槽的"参考位置"由 850hPa 辐合带/海压槽线导出,用于标定与校验参数化结果(见 2)。
2) 参数化(独立模块,逐月,日插值;**系数一律由场库历史数据标定,不许手写常数**):
   - 标定:从场库 MSLP 场提取逐月副高脊线(如 5880 gpm 等值线/纬向风零线)与强度,
     从风场辐合带提取 ITCZ 参考位置,得到逐月历史序列;
   - 副高:脊线纬度 = 季节气候态(月函数) + a·ONI + b·PDO + 随机游走;
     强度/东西延伸同理;a、b 用历史序列回归拟合(留一法交叉验证);
   - ITCZ:中心纬度随太阳赤纬季节迁移 + ENSO 调制(暖事件东太 ITCZ 向赤道/东南偏移)+
     强度(对流活跃度)与 IOD 相关项;系数同上回归标定;
   - 季风槽:位置(纬度随季节迁移)+ 强度(ENSO 调制:暖事件西太季风槽偏弱/偏东),系数回归标定;
   - 输出参数结构:{ridge: {lat, strength, lon_range}, itcz: {center_lat, half_width, activity},
     monsoon_trough: {lat, lon_center, strength}};
3) 与场库的一致性处理(用户选择独立参数化,接受不一致):
   - GPI 生成概率优先用场库场;ITCZ/季风槽/副高线用于阶段3 的"生成位置约束"、阶段5 云图背景与界面显示;
   - 若参数化与场明显冲突(如副高脊线落在 MSLP 低压区),界面标注"参数化与场不一致"警告,不静默吞掉;
   - 参数化回归残差记录(每月),供一致性诊断。
```

## 五、性能

```
- 建库:批处理一次性(下载+解析+归档),可断点续传;下载失败自动重试并记录缺失月;
- 生成 2-3 年:逐月 analog 匹配(数百次马氏距离)+ 日插值 + 派生场,纯 numpy,分钟级;
- 默认存储:只落盘逐月场+插值参数,get_field 按日期运行时插值(避免 2-3 年逐日场 ~1GB 落盘);
  逐日场生成仅在对性能敏感的批量导出模式下落盘;
- 生成过程可中断重跑(种子固定则结果一致)。
```

## 六、验证清单

```
1) 【无偏验证,留出法】随机剔除 10-15% 历史月不参与建库,用生成器还原被剔除月的场,
   与真实场对比(逐月均方根误差、空间相关、GPI 分布);此条替换"与桶内场对比"
   (后者匹配自己,无验证意义);留出比例与误差上限(如 RMSE 阈值)先定死再实现;
2) 生成场统计对比:逐月均值/方差无系统性偏差;ENSO-SST/ENSO-切变回归图与历史回归一致
   (暖事件东太增暖、中太增暖型等);
3) 逐月平滑度:相邻月场差异在历史相邻月差异范围内(轨迹记忆生效),无跳变;
4) OHC 与 SST 的独立性:生成场中 OHC-SST 相关系数与历史一致(不过高),
   冷水上翻信号(负 OHC 距平区)可出现在高温 SST 区(白海豚式);
5) GPI 场提前验证:生成场的 GPI 空间分布与历史同模态月对比(阶段3 的输入先过检);
6) 副高/ITCZ/季风槽参数与场库标定序列分布对比(脊线纬度年内迁移、ENSO 位相下的偏移方向),
   回归系数交叉验证误差报告;
7) 空区/缺失月(如 1979-1981 OHC)有元数据标注,不静默填 0;
8) 性能:2-3 年逐月场生成总耗时记录在案(目标 <5 分钟);留出法验证报告存库。
```

> 说明:阶段2 输出即阶段4 强度模型的输入契约——SST 驱动型(小环流+高速)与 OHC 驱动型(大环流+慢速)的区分逻辑在阶段4 实现,阶段2 只保证两场独立、可信。

---

# F7.【功能】架空模拟器 — 阶段3:台风生成层(2026-08-02)

> 输入:阶段2 的逐日 GPI 场与参数化副高/ITCZ/季风槽;统计基准 `G:\气象\数据\xrq\`(仅 2000-2025 官评,覆盖 WP/SH/CP)。输出:本季所有台风的生成记录 {id, 盆地, 生成时刻(6h 对齐), 生成位置, 初始强度}。AL/EP 与地中海无基准,用气候学频率参数并标注"无基准"。

## 一、生成模式(用户指定,四种,界面可切换)

```
模式 A 决定生成点:用户给定坐标(界面点选/输入)→ 引擎在该点邻域(±3°)内生成台风,
                  生成时间由"统计分布抽样"(见二.3);
模式 B 决定生成时间:用户给定时刻 → 在该时刻邻域(±48h)内生成,生成位置由
                  GPI 空间分布抽样;
模式 C 同时决定:坐标+时间都给定(界面两选,引擎只负责后续模拟);
模式 D 完全随机:时间与位置全由引擎按统计分布抽样(默认,用于整季无人值守生成)。
各模式都必须通过"环境允许性检查"(见二.2),不通过的给界面提示原因(切变过大/
SST 不足/纬度不够),而不是静默跳过或强行生成。
```

## 二、生成算法

```
1) 生成数:从基准逐月生成数分布抽样(逐盆地逐月经验分布,2000-2025);
   可选模态调制:暖事件(ONI>0.5)时西太生成数略减但系统偏强(ACE 高),
   东太生成数略增——调制系数作为可开关参数,默认关闭。
2) 环境允许性(硬门槛,全部满足才生成):
   - |纬度| > 5°(且 < 40°,超出以衰减系统处理);
   - SST ≥ 26.5°C(用阶段2 get_field('sst'),出生点);
   - GPI 值 ≥ 阈值:分位基准来自"xrq 2000-2025 历史台风生成点处的 GPI 经验分布",
     取该分布 40 分位(生成点 GPI 应不低于历史生成点中位数水平,实现时按分布校准);
   - 垂直切变 < 20kt(用 get_field('shear'));
   - 生成点应在参数化 ITCZ/季风槽活动带内(±8° 纬度容差;无基准盆地放宽为 GPI 条件)。
3) 时间分布:逐月生成数 = 目标数(见1),月内时间均匀随机;时次对齐 00/06/12/18。
4) 空间分布:以 GPI 场为权重抽样(拒绝采样或累积分布逆变换);
   优先落在参数化 ITCZ/季风槽带内(权重 ×2)。
5) 盆地归属(0-360°E 制,与现有程序经度制一致):WP(100-180°E,北)/SH(南半球,
   全经度)/CP(180-220°E,北)/EP(220-280°E,北)/AL(280-360°E,北);
   地中海作为可选扩展盆地(约 350-360°E,30-45°N;无基准,仅模式 A/C 可用)。
   盆地写 .dat 的 basin 字段(与现有洋区 code 兼容)。
6) 初始强度:TD 级,风速 15-25kt 随机,气压 1005-1010hPa 随机,性质 'TD'。
7) 输出 records.json:每台风 {id, basin, t0, la0, lo0, w0, p0, mode, 生成理由日志};
   固定种子可复现。
```

## 三、验证清单

1. 生成的逐月生成数分布 vs xrq 基准(逐盆地 KS 检验);
2. 生成点空间密度 vs 基准(5°×5° 网格核密度对比);
3. 全部生成通过环境允许性检查(记录拒绝率:拒绝数/尝试数,过高提示 GPI 阈值或参数化问题);
4. 季节窗口外零生成(北半球 1-3 月无 WP 台风等,允许个例);
5. 同一批次 3 次不同种子:数量在基准方差范围内波动。

---

# F8.【功能】架空模拟器 — 阶段4:台风模拟层(2026-08-02)

> 输入:阶段2 get_field/get_param、阶段3 records.json、基准 xrq(风压散点/强度变化率/ACE 分布)、
> **全球高程图 `C:\Users\Yukioto\Pictures\world_elevation_60arcsec_21600x10800.tif`**(60 弧秒,21600×10800,约 1.85km,全球)、
> **EX/SS/SD 风压补充数据源**(xrq 的 EX 段无气压,改用):`G:\气象\数据\miwu\`(1979-2025,
> 按年分 WPAC/EPAC/CPAC/ATL/SHEM 等,EX 段带气压)、`G:\气象\数据\自评\`(2025/2026 各盆地,
> 如 2025-2026sh\bsh172026 Luana.dat)、NHC 风格目录(AL/EP/CP/SH/WP 各盆地,AL 的 EX 段
> 3552 条带气压)——用于 EX 风压联合分布约束域,而非公式拟合。
> 输出:每台风标准 `.dat`(与 app/data_repo.py 解析格式一致:逗号分隔、
> `盆地, 序号, YYYYMMDDHH,  , XRQA, 0, 纬度(如 224N), 经度(如 1411E), 风速(int kt), 气压(int hPa), 性质, 名称`,
> 时次必须落在 00/06/12/18,气压 0 表示未知、模拟器一律填实值;第 12 列名称每行写同一值)。
> 文件命名规范:`b{盆地小写}{序号:02d}{年份}.dat`(如 bwp012026.dat),与 xrq 目录一致,
> 输出目录可直接配置为回放程序 typhoon/ 或复制过去。
> **性质标法(用户规则,已按 xrq 数据核验:性质值域无 STS、49-63kt 段标 TS)**:
> TD/TS **全球统一**(35-63kt 一律标 TS);64-129kt:**WP/SH 标 TY,CP/EP/AL/地中海标 HU**
> (HU=飓风级,仅 NHC/CPHC 辖区(含地中海)对 TY 的叫法);**≥130kt 全球统一标 ST**
> (除 HU/TY 的叫法差异外,其余所有性质全球统一,包括 ST;与 xrq 的 CP 数据
> 含 ST 一致);EX/SS/SD 用于变性后,性质可随过程切换(见生命周期);
> DB/MD/SS/SD/WV 不用于模拟输出(生成即 TD,见 F7)。
> 此标法与回放程序 ACE 判定(仅 TS/TY/ST/HU/空 计 ACE)及 xrq 统计口径完全一致。
> **执行约束:禁止添加提示词未规定的机制**(寿命上限、能量上限等任何硬性截断
> 一律禁止);一切与用户规则不符的实现视为错误。

## 一、模拟内核选型(双轨:完整 WRF 主方案 + 半 WRF 简略版)

```
定位:阶段4 的台风演化内核有两种实现,接口一致(同一 .dat 输出与状态结构),
     UI/阶段5 不感知差异。**默认内核 B(半 WRF 合成,完全架空环境);
     内核 A(完整 WRF,历史真实环境)为可选精算模式,需 WRF 环境就绪才可用**,
     界面提供内核选择(默认 B),未配置 WRF 时选 A 提示不可用并留在 B。
**职责划分(关键,防实现冲突)**:
- 内核 A(WRF):路径、强度、气压、暖心、锋面、云场均**由 WRF 数值演化自洽产生**;
  三(路径)、四(强度与气压)、五(置换状态机)的参数化逻辑**不驱动 WRF**——
  在内核 A 下它们只作:初始化植入参数、性质标注依据(暖心/风场结构诊断)、
  置换状态"标注"(从 WRF 场识别双眼墙/置换特征,不强制改写 Vmax)、消散判定的辅助;
- 内核 B(半 WRF):三/四/五全部作为驱动逻辑;
- 两内核共用的输出层:性质标法、结构诊断判定规则(见六)、.dat 输出。

【内核 A:完整 WRF(可选精算模式,用户接受 2 天级计算,需断点续传)】
1) 环境:WRF 4.5+ / WPS,Linux(WSL2 或 Docker);geog 地形数据一次下载;
2) 域与分辨率:逐台风**分段连续运行**:单段计算窗口默认 2 天(可配,仅指单次
   WRF 运行长度,不是寿命上限——台风全程由多段 restart 链拼接直至消散判定);
   单域覆盖台风活动区,水平 9-30km 可配;物理方案:WSM6 微物理 + YSU 边界层 +
   RRTMG 辐射 + 浅对流;积云参数化 10km 以下关闭;
   **分辨率与结构声明**:9-30km 不能解析眼/双眼墙/细螺旋带(ERC 需 ~1-3km);
   大尺度(路径/气压/暖心/强度/锋面)用 WRF 输出;精细云结构(眼/CDO/螺旋带/
   双眼墙)由 F9 合成增强层叠加;可选局部 3km 加密域(单台风 12h 窗口,计算量
   ×10 左右,默认关);
3) 初始场与边界条件(LBC):用真实 GFS 0.25°(逐 6h)或 ERA5;时段选择 =
   **阶段2 的 analog 模态匹配结果**(目标模态组合 → 匹配的历史时段 → 取该时段
   真实 GFS/ERA5 做 LBC)——模态决定"用哪段真实环境",合成场不做 WRF 输入
   (垂直结构与时间连续性不足),此取舍写文档;
   **定位说明:WRF 方案下环境场来自历史真实时段(非架空合成场),即"历史环境
   + 模拟台风";环境完全架空只能走内核 B——此定位需用户确认;**
4) 台风初始化(经典难点,必须显式处理):F7 生成的 TD(15-25kt)以合成涡旋植入
   初始场(参考 GFS bogus/bogus vortex 做法:按 Vmax/r34/暖心剖面生成风场与
   气压增量叠加到初始场),spin-up 6-12h 后开始记录报点;植入参数由 F8 状态
   (Vmax/r34/暖心)生成,禁止冷启动裸涡或让大尺度场自行发展出台风;
5) 断点续传与运行:按 12-24h 分段运行,每段结束写 restart 文件,崩溃/中断后
   从最近 restart 续跑;多台风并行(进程隔离);整季 30-40 台风按核心数排队;
   **域边界:台风接近域边(如距边 <2 格距)时提前结束本段并在新位置重建域
   (restart 投影到新域),保证全程域覆盖;窗口结束 ≠ 消散**;
6) 输出后处理:netCDF → 每 6h 提取:MSLP 极小值中心追踪(场先平滑再取局部极小,
   多中心时按连续性选择)、10m 风场最大风速(Vmax)与风圈半径、850-600hPa
   暖心诊断、边界层风场(供结构判定);云水/云冰/云顶温度场直接输出给阶段5;
   **Vmax 校准(避免与 .dat/ACE/风压基准对比失真)**:WRF 10m 风在 9-30km 网格
   上被平滑、峰值系统性偏低,须按"WRF 模拟 Vmax vs 同网格实况 Vmax"的经验
   偏差做校准(记录校准系数,默认系数先置 1 并标注未校准,待与实况对比后拟合);
7) **WRF 输出变量完整清单(对标 B 站《5612温黛 WRF 数值模拟复盘》视频 8 项输出,
   全部为标准功能,按需启用)**:
   - 模拟卫星云图(向外长波辐射):OLR(RRTMG 输出)+ 云顶温度 CTT/云水/云冰
     合成 IR 增强图(F9 已定义 IR-BD 色条);叠加 3.9μm/可见光可选;
   - 组合反射率(雷达):namelist 开 `diagflag=1` 与 `do_radar_ref=1`,输出
     REFL_10CM(微物理混合比合成);降水叠加可选;
   - 地面形势图:SLP(由 PSFC/T2 后处理换算,wrf-python 的 wrf.slp)、
     24h 累计降水 = RAINC+RAINNC 的 24h 差值、10m 风 U10/V10(风羽/风向杆);
   - 中低层形势图:500hPa 位势高度 GHT:500 与温度 T:500(等高线+等温线)、
     850hPa 风羽 U/V:850、低空急流(850-700hPa 风速 ≥12 m/s 带)、
     高比湿区(QVAPOR 850/700 高值带);
   - 高空形势图:100hPa 位势高度 GHT:100、200hPa 风与辐散
     (散度 = ∂u/∂x+∂v/∂y,由风场后处理计算)、高空急流(200-300hPa 风速
     ≥30 m/s 带);
   - 单点演变:地图点选 → 该点 SLP/10m 风随模拟时间的曲线(时间序列抽取);
   - 台风中心气压与最大风速演化:中心追踪输出 MSLP-min 与 Vmax 时间曲线
     (**1min 平均口径:WRF 输出为模式瞬时 10m 风,与实况 1min 平均对比前
     先做 1min 时间平均,或标注口径差异**);
   - 路径与最佳路径对比:模拟路径 vs 实况最佳路径(基准 = xrq 2000-2025 /
     历史 b-deck),逐 6h 距离误差/登陆点误差/强度误差表;
   - 后处理依赖:wrf-python(UPP 替代可选项)统一做 SLP/散度/急流/时间序列;
     各图输出走 F5 render_layer 接口(新增图层:id olr/refl/fig_sfc/fig_mid/
     fig_high/point_ts/center_ts/track_compare);
8) 可选质量增强:网格 nudging(grid_fdda)约束大尺度环境场,缓解"初始涡旋
   组织不足、增强偏慢、路径漂移"(视频作者自评的三项缺陷;nudging 选项默认关);
9) 内核 A 为可选模式:仅当 WRF 环境(WSL2/Docker + WRF/WPS 编译成功 + geog 数据
   就绪)配置完成时可用;未就绪时界面选择 A 给出提示并停留在默认内核 B。

【内核 B:半 WRF(合成风场+结构诊断,简略/测试版)】
1) 每时次构建"台风环境合成风场" W(x,y) = 台风涡旋场(修正 Rankine 剖面,
   梯度风平衡,含暖心气压凹陷)+ 环境背景场(阶段2 引导气流与大尺度场)
   + 天气尺度结构(锋面/槽/东风波,按参数化副高/ITCZ 合成或环境场诊断);
2) 结构诊断:闭合流函数/等压线中心检测、双中心合并判定、锋面与中心相接、
   暖心/冷心诊断(类 Hart 相空间简化)、环流完整度;
3) 消散/EX/吞并/维持判定全部基于结构诊断(见六-生命周期),无硬性条件;
4) 风压:热带阶段用 KZC/Chavas(风压对应工具代码);EX/SS/SD 阶段风压解耦
   (见四-3);云图由合成风场+阶段2 背景合成(见 F9)。

两内核共用:状态结构(二)、结构诊断判定规则(六)、置换状态机(五,仅 B 驱动、
A 标注)、性质标法与 .dat 输出(六)。
```

## 二、逐 6h 状态定义

```
state = {la, lo, vmax(kt), mslp(hPa), r34/r50/r64(nm), oci(hPa), 环流尺度因子 S,
         雨带数 nb, 置换状态机(见四), 性质(见标法), 暖心状态(暖/冷/过渡), 锋面相接(是否),
         合成风场对象(仅内核 B)}
注:台风从生成到消散全程持续演化,性质可多次切换(热带↔EX/SS/SD),无寿命上限。
```

## 三、路径模拟(内核 B 驱动;内核 A 下路径由 WRF 演化,本节省略)

```
1) 引导气流:V_steer = get_field('uv_steer')(850-300hPa 深度平均,阶段2 输出),
   在台风位置双线性采样;
2) β漂移:约 2.5-4 m/s 向赤道侧偏西 + 1-2 m/s 向极,幅度随纬度(|lat| 15-25° 最强);
3) 随机扰动:红噪声(AR(1)),幅度与环境引导强度成反比(引导弱时路径更"漂移"),
   典型 0.5-2 m/s;6h 位移 = (V_steer + β + 扰动) × 6h;
4) 藤原效应(可选项,默认关,可配置):两台风中心距 < 1500km 时互旋
   (角速度 ∝ 另一台强度/距离²,距离 >800km 时影响迅速衰减),叠加在各自位移上;
5) 陆地衰减(用户指定:由全球高程图驱动,不用固定 0.8/6h):
   - 数据预处理(一次性):读 world_elevation_60arcsec_21600x10800.tif → numpy 高程数组;
     模拟用降采样版本(建议 5 弧分 ≈ 9km,4320×2160,int16 约 19MB,保持内陆/岛屿形态;
     原始 60 弧秒版按需懒加载,不常驻内存);派生海陆掩码(海拔 >0 为陆);
   - 每 6h 在台风位置双线性采样海拔 alt;衰减系数 f(alt) 为分段线性连续函数:
     alt ≤ 0(海上):无衰减(f=1),此前因登陆造成的衰减随强度方程自然恢复
     ——"再入海可重新增强";
     0 < alt ≤ 200m:f = 1.00→0.82(沿海平原,缓衰减);
     200 < alt ≤ 800m:f = 0.82→0.55(丘陵,中衰减);
     alt > 800m:f = 0.55→0.30(山地/高原,强衰减);
     vmax 每 6h 乘 f(alt),下限 20kt;
   - 可选增强项(默认关):靠近 800m 以上山体(距离 <200km)时地形抬升触发
     短暂对流增强(24h 内 +5-15kt),模拟台湾中央山脉/吕宋山地式过山前增强;
   - 校验:模拟登陆衰减曲线(登陆时刻 Vmax、6h/12h/24h 后)与 xrq 登陆台风
     样本对比(如 2000-2025 西太登陆台风的 24h 衰减分布)。
6) 大西洋-西太跨盆转换:中心越过 180° 或 140W 边界时盆地字段随区域更新(可选)。
```

## 四、强度与气压模拟(内核 B 驱动;内核 A 下由 WRF 演化,本节仅用于标注与 EX 约束域)

```
1) 强度上限 PI:
   - 环流尺度因子 S(由 r34 与移速归一化,0-1):小环流+高速 → S→0;大环流+慢速 → S→1;
   - SST 驱动型(S 小):PI 仅用 get_field('sst')(Emanuel 简化公式,ΔT 相对 26.5°C);
   - OHC 驱动型(S 大):PI 用 OHC 折算等效水温(T_eq 由 0-700m 热焓反算,阶段2 场),
     等效水温可低于 SST——天然复现"白海豚式:31-32°C 开水上冷崩"。
2) 强度演化:dV/dt = (PI_eff - V)/τ - 切变抑制 - 干空气抑制;τ 按增强/减弱阶段
   取值(增强 τ≈24-48h,减弱 τ≈12-24h);24h 变化量受基准增强率分布约束
   (从 xrq 逐 6h Vmax 序列统计分位,防止"每 6h +20kt"式爆发,可选放宽);
   PI_eff = PI × (1 - k_s·shear/20kt) - k_d·干空气项(700hPa 湿度 <50% 时激活);
   置换状态机可强制改写 Vmax(见五,仅内核 B;内核 A 下仅作标注)。
3) 气压(与 Vmax 部分解耦):
   - 基准:复用 G:\气象\工具\风压对应 的 KZC / Chavas 模型
     (Pmin = f(Vmax, R34, OCI, 纬度, Penv);Penv = OCI + 2hPa,OCI 从
      get_field('mslp') 按 0.1hPa 步进判闭合等压线——参考风压对应工具代码);
   - 雨带修正:同 Vmax 同环流下,雨带越多气压越低(用户规则):修正项
     ΔP_rain = -c·(nb - E[nb]),其中 E[nb] = 期望雨带数 = f(Vmax, 环流尺度因子 S)
     (随强度与环流增大的单调函数,实现时定义),系数 c 用 xrq (w,p,环流代理)
     散点回归标定;
   - 置换期解耦:普通置换期间 mslp 持平或仅微升(±3hPa),不受 Vmax 下降影响
     (用户规则:置换中气压与风速完全不对应);
    - 气压巅峰滞后:mslp 谷底比 vmax 峰值滞后 3-12h(按分布抽样,置换结束
      外眼收缩期气压继续下探——普通置换结束后 Vmax 回升前气压先至谷底);
   - **EX/SS/SD 阶段风压解耦(用户规则:EX 风压极端不对应)**:
     性质为 EX/SS/SD 后,不再用 KZC/Chavas,气压与风速各自独立演化
     (mslp 由环境场+系统自身演化的低压结构决定,vmax 由风场结构决定),
     二者关系仅受约束域限制:从 miwu(1979-2025 各盆地)与 NHC 风格/自评数据的
     EX/SS/SD 段 (w,p) 联合分布中抽样/约束(实测:同 65kt 可对应 974-996hPa,
     40kt 可对应 978-1002hPa,极端如 45/938 需在分布允许范围内),**禁止用任何
     热带经验公式硬套 EX 段**;SS/SD 同理。
4) 结构参数 r34/r50/r64:与 Vmax 正相关、与纬度/环流尺度相关,用 xrq 数据
   (如有风圈列)或 KZC 半径参数标定;无风圈基准时按气候学经验公式,标注"未校准"。
5) 雨带数 nb:状态变量,随强度与置换阶段演化(增强期雨带增多、置换期重组),
   反哺 3) 气压修正,并输出给阶段5 云图。
6) 生命周期(用户规则:**无寿命上限,性质可多次切换,消散由模拟自然发生**):
   - **禁止任何寿命上限/寿命分布/保险上限**;台风从生成到消散全程持续演化,
     性质可多次切换(热带 ↔ EX/SS/SD);
   - **EX/SS/SD 后可继续模拟**:变性/转化不终止系统——例如锋面(温度梯度带)
     接入台风中心(由结构诊断判定,内核 A 用 WRF 温度场、内核 B 用合成场
     温度梯度)→ 性质转 EX,系统继续演化(斜压发展、重新增强或衰减都允许);
     EX/SS/SD 期间持续输出报点(风压按 3)解耦规则);
   - **消散判定(全部由模拟自然发生,无硬性条件)**:
     a. 风场完全并入背景:系统不再有独立可辨识的环流中心(闭合等压线/流函数
        中心消失,环流已无法从背景风场中区分,例如完全并入低压槽/背景风场)
        → 判定消散并记录;单纯"不闭合"不构成消散(可能仍是 WV/LO);
     b. WV(波/东风波)与 LO(槽性低压)是合法形态:环流呈波状或长条槽状、
        风场结构符合东风波(东侧南风/北侧信风东风/西南侧弱风)或槽型时,
        系统继续存在并按相应形态演化,不判消散;
     c. 暖心与风场完整:即使长时间处于冷水区,只要暖心仍为热带性质(气旋温度
        高于同层面背景温度,即暖芯;非"暖心分析")且风场结构完整 → 继续演化,
        不判消散;
     d. 吞并:两系统风场合并为一个环流中心(即使呈槽状)才判定弱方被吞并
        (保留合并时风速较高者);若距离近但对方移速高、风场未合并 → 不判消散;
     e. 陆地:在陆上依靠棕海效应可增强甚至开眼(如 2025-26 南半球 Luana,
        G:\气象\数据\自评\2026\2025-2026sh\bsh172026 Luana.dat:峰值 65kt/985hPa
        出现在陆地阶段),只有风场完全并入背景/低压区才判消散;
     f. 冷/切变等环境抑制只通过强度方程与结构诊断自然表现(强度持续下降 →
        风场结构弱化 → 最终并入背景),不单独触发消散;
   - 消散记录:消散点、消散时刻与"消散原因"(并入背景/被吞并/等)写入台风日志,
     供验证与统计;被吞并方记录"被 XX 吞并"。
```

## 五、眼壁置换状态机(用户规则,普通/融合/失败/重爆;内核 B 驱动,内核 A 仅从 WRF 场标注)

```
1) 状态:IDLE → EW_FORMING(外眼形成,内眼未损)→ EW_DEV(外眼发展)
   → [普通] EW_CUTOFF(外眼切断内眼水汽供应,内眼减弱消亡)
   → EW_CONTRACT(外眼收缩加强,新眼)→ IDLE;
   [融合] EW_MERGE(外眼紧贴内眼、内眼被吸收,单环)→ EW_CONTRACT → IDLE;
   异常:EW_FAILED(置换失败,双眼墙长期共存,9711 式)→ 可能 EW_REBUILD
   (外眼破裂、内眼重新爆发,杜苏芮式)→ IDLE。
2) 触发:vmax ≥ 100kt(可配)且处于增强/巅峰期;环境(切变弱 + 高 OHC)触发概率高;
   同一台风两次置换间隔 ≥ 36h;每台风置换次数 ≤ 3(超限降概率)。
3) 普通置换(vmax 呈 V 字曲线:下降→谷底(降幅为巅峰的 10-25%)→回升至
   新巅峰;mslp 持平或微升 ±3hPa;时长 ≥ 9h,按环境切变/湿度抽样 9-36h;
   环境差 → EW_FAILED(时长超上限仍未完成,双眼墙持续,Vmax 低位徘徊,
   mslp 仍低——9711 式);EW_FAILED 后若切变再转好 → EW_REBUILD 概率。
4) 融合置换(海燕式):外眼紧贴内眼(间距 < 0.3×内眼半径);vmax 平台或略降
   (<10%);mslp 影响小(±2hPa);时长 6-15h。
5) 类型选择:普通:融合 = 基础比(如 6:4)+ 环境调制(高切变/冷涌 → 融合概率升)。
6) 置换全程记录日志 {台风, 类型, 起止时刻, 失败/重爆标记},供界面显示
   (双眼墙示意环)与阶段5 云图结构输入。
```

## 六、.dat 输出与验证

```
1) .dat 输出:每台风一行一报点;盆地/序号(按生成顺序)、性质按"输出"栏规则;
   名称 = 模拟名(如"Dolphin-01"或用户命名池,默认按编号);
2) 验证清单:
   a. 模拟 (w, p) 散点 vs xrq 2000-2025 散点(风压带对比,超带 20% 以上标红);
   b. Vmax 逐 6h 变化率分布 vs 基准(增强/减弱速率分位对比);
   c. 单台风 ACE 与全季 ACE 序列分布 vs 基准(KS);
   d. 置换事件频率:强台风(CAT3+)中经历过 ≥1 次置换的比例与气候学一致
      (大西洋约 6-7 成,西太类似量级);
   e. 路径密度(5°×5° 网格)vs 基准;
   f. 输出可被 app/data_repo.py 直接解析(解析测试:把模拟 .dat 放入回放程序
      typhoon/ 目录重载无警告);
   g. 同一模拟批次 3 个种子:季 ACE 总量在基准 10-90 分位区间内波动。
```

---

# F9.【功能】架空模拟器 — 阶段5:云图渲染层(2026-08-02)

> 输入:阶段2 场/参数化(ITCZ/季风槽/副高)、阶段4 台风逐 6h 状态(含置换状态/雨带数/结构参数)。
> **云图来源双轨(与 F8 内核对应)**:**默认合成管线(内核 B,完全架空)**;
> 内核 A(完整 WRF)时,单台风云图改用 WRF 输出的云顶温度/云水/云冰/云光学厚度场渲染
> (IR 增强=云顶温度,可见光=反照率)。两轨输出同一 IR/VIS 接口。
> 输出:逐 6h 全球云图帧(IR 增强 + 可见光)+ 单台风细节图;对接 F5 的 render_layer 接口。
> 参考素材:`G:\气象\参考\IR-BD_德沃夏克\`(海燕 IR-BD 实况图、Dvorak 1973 流程图),IR-BD 色阶术语:OW(开阔暖水面/无云)、LG(浅灰,弱对流中云顶)、MG/DG、冷增强段(极冷云顶,白/彩)。

## 一、单台风云图(逐 6h)

```
1) 风场剖面:修正 Rankine 涡由 r34/r50/r64 反演(剖面参数随环流尺度因子 S 变化);
2) 眼:半径随强度/置换状态(普通置换中内眼缩小外眼可见);
   红外表现:眼区亮温高(暖,约 -10~-30°C),眼壁环极冷(-70~-90°C);
3) CDO:半径与云顶温度由 Vmax 反查 Dvorak 定强表(T 值↔亮温,参考已提取的
   T 值表:如 T5.0≈90kt、T6.5≈127kt);增强期 CDO 增大变冷,减弱期破碎;
4) 螺旋雨带:条数 = 雨带数 nb(阶段4 状态),沿对数螺旋分布,亮温 -40~-60°C,
   宽度/强度随半径衰减;FBM/Perlin 噪声叠加细节;
5) 置换结构(状态机输出直接驱动):
   - 普通置换:双眼墙(外眼环 + 内眼环同时可见,外眼较暗/冷)、内眼消亡期
     内眼亮温回升;EW_FAILED:双眼墙长期共存(9711 式);EW_REBUILD:内眼重新
     爆发(明亮小眼重回中心);
   - 融合置换:单环紧贴(无间距双环),整体略呈椭圆;
6) 冷崩表现(白海豚式):OHC 驱动型 + 冷水上翻(阶段4 标记)→ CDO 破裂,
   半圈 LG 半圈 OW(IR-BD 下:一半浅灰弱对流、一半开阔水面);
7) 风切变拉扯:高层云砧向切变方向延伸(CDO/雨带非对称,砧状云);
8) 可见光合成:云顶反照率按亮温/云厚映射(粗糙版,白亮=厚云,深=海面)。
```

## 二、全球背景与合成

```
1) ITCZ 云带:按阶段2 参数化(中心纬度/半宽/强度)合成带状中高云
   (亮温 -30~-50°C,强度调制云量);
2) 季风槽云团:槽位附近合成对流云团簇;
3) 中纬度锋面云带(可选):按副高边缘合成波状云带;
4) 洋面:低云(10-40% 覆盖随机场)与晴空;冰雪/陆地反射率常值;
5) 合成:背景 + 各台风结构图,优先级台风在上;输出 IR(亮温→IR-BD 色条映射,
   色条含 OW/LG/MG/DG/冷增强段)与 VIS 两版;
6) 性能:逐 6h 帧按需预渲染(F5 已定义前瞻窗口);单帧生成目标 <2s
   (0.5° 分辨率);低分辨率快速模式(2°)供全局快速预览。
```

## 三、验证清单

1. 单台风 IR 结构对照真实图(用已下载海燕 IR-BD 图作参照):眼/眼壁/CDO/雨带形态
   在强度相同时结构可比;**适用范围:内核 B 或 3km 加密域;9-30km WRF 输出
   大尺度云图不参与此项,其精细结构由合成增强层(F9-一)验证;**
2. 场景验证:构造并回放 普通置换/融合置换/9711失败/杜苏芮重爆/白海豚冷崩 五类
   测试台风,IR 输出与描述特征一致(双眼墙、单环紧贴、半圈 LG 半圈 OW 等);
3. (可选)Dvorak 自洽校验:由合成云图按 Dvorak 模式(眼清晰度→T 值)反查风速,
   与阶段4 Vmax 对比,偏差 > 1.5 个 T 值(约 25kt;T 值表 0.5 档约 12-18kt,
   参考 F4 附件提取的中文维基 T 值表)则告警——用于发现强度与云图不一致的 bug;
4. IR-BD 色条与标准色阶一致(OW/LG/MG/DG/冷段顺序与颜色);界面图层可切换
   IR/VIS/SST(与 F5 L1-L3 对接);
5. 性能:单帧 <2s、快速模式 <0.5s;2-3 年数据预渲染总量与磁盘占用记录在案。

> 说明:阶段3(F7)、阶段4(F8)、阶段5(F9)严格串行依赖:3 的生成记录喂 4,
> 4 的逐 6h 状态喂 5;三者共用的"性质标法/HU 语义/盆地值域"以 F8 输出栏为准,
> 实现时先做 F8 的最小闭环(模式 D 全随机 + KZC 风压 + 普通置换),再加 F7 的
> 模式 A/B/C 与 F9 的置换/冷崩结构。

---

# F10.【功能】模拟画面样式对齐:B 站《5612 WRF 模拟》视频同款(2026-08-07)

> 需求:程序中的**模拟画面**(不含 UI,仅渲染输出)需与 B 站视频
> 《【WRF模拟】5612号超强台风"温黛"》画面风格一致。已下载视频流逐帧验证,
> 8 类画面全部确认(OLR/雷达/地面形势/500hPa 中低层/100hPa 高空/单站时间序列/
> 中心强度演化/路径对比,见下表)。**本提示词只定义画面样式与渲染规范,
> 不改变任何模拟逻辑**;输出走 F5 render_layer 接口,新增图层 id。

## 一、通用画布规范(所有画面共用)

```
1) 白底;顶部居中黑字标题;底部左侧小字图例;经纬网格+四边刻度标签;
2) 陆地轮廓:黄色细线(视频同款,需海岸线数据:可从现有地图图片派生轮廓
   或内置简化边界);
3) 时间戳格式与视频一致:双域图 "15/08/20 09:34:00"(DD/MM/YY HH:MM:SS);
   曲线图 "1956-08-01 10Z – 08-02 14Z";路径图按日期标注;
4) 双域图规范:D01(大域)+ D02(放大域)左右并排,共享同一色标,
   标题分别带 D01/D02 前缀;D02 以台风中心为焦点(域尺寸约为 D01 的 1/4 经度跨度);
5) 单位与视频一致:m/s(10m 风)、km/h(最大风速曲线)、kt(200hPa 风)、
   hPa、dam(位势高度)、mm(降水)、dBZ(反射率)、W/m²(OLR)、10⁻⁶ s⁻¹(散度);
6) 风羽(barbs):半羽=2.5 m/s、整羽=5 m/s、旗=25 m/s(黑色,实心圆=静风);
   实现 barb 绘制函数,按风分量生成。
```

## 二、8 类画面规格(以视频逐帧验证为准)

```
1) 模拟卫星云图(OLR):标题 "Outgoing Longwave Radiation (W/m²)" + 时间戳;
   灰度色标 100→300 W/m²(白=100 低值=深对流,黑=300);D01/D02 双域;
2) 雷达组合反射率:标题 "Radar Reflectivity (DBZ)" + 时间戳;色标 5→75 dBZ:
   蓝(5-15)→绿(20-30)→黄(35-45)→橙(50-55)→红(60-65)→洋红(70-75);
   D01/D02 双域,眼区低值、眼墙高值;
3) 地面形势图:左面板 = SLP 等值线(黑线,hPa)+ 24h 累计降水填色
   (白0→绿1→蓝25→紫/红>800mm)+ 标记:红 H(高压)、蓝 L(低压)、红 T(台风中心);
   右面板 = 10m 风矢量(箭头)+ SLP 等值线,风矢量色标(5→25 m/s 冷色到暖色);
   标题如 "D01 SLP (hPa) & 24h Precip. (mm)" / "D02 SLP (hPa) & 10m Wind m/s";
4) 中低层形势图:标题 "500 hPa Height (dam) and Temp (°C), 850 hPa Wind (barb)";
   500hPa 高度=蓝色等值线(dam);500hPa 温度=红色虚线(°C);
   850hPa 风羽=黑色;紫色填色=850hPa 风速≥12 m/s(低空急流);
   绿色阴影=850hPa 比湿≥12 g/kg;
5) 高空形势图:标题 "100 hPa Height (dam), 200 hPa Wind (knots) and
   Divergence (10⁻⁶ s⁻¹)";100hPa 高度=等值线(dam);200hPa 风羽=黑色;
   紫色填色=200hPa 风速>30 kt(高空急流);散度填色(正散度>5×10⁻⁶ 紫色/
   负散度=辐合区标色);
6) 单站时间序列:标题 "站点名 (WRF) | 起-止时间"(如 "Haimen (WRF) |
   1956-08-01 10Z – 08-02 14Z");双面板:上=气压(hPa,930-990 量程)、
   下=10m 风(m/s,0-25 量程);黑色曲线;浅灰网格线;x 轴时间刻度
   "Aug 01 12Z" 格式;无图例;
7) 中心气压与最大风速演化:标题 "Typhoon (WRF + JMA) | Jul 25 00Z – Aug 04 00Z";
   双面板:上=中心气压(hPa,920-1000),下=最大风速(km/h,0-60);
   WRF=黑色实线、实况=红点+红线;图例右上 "WRF"/"JMA";
   (我们无实况对比时:黑色为模拟曲线,红色为 xrq 同型台风基准或空置);
8) 路径与最佳路径对比:标题 "Typhoon Track (WRF + Best Track, colored by
   central pressure)";模拟轨迹按中心气压着色,色标 990→1030 hPa
   (红990→黄1000→绿1010→青1020→蓝1030,右下角);实况最佳路径=红点+红线;
   图例左上:"Best Track"(红点)/"WRF"(蓝点);轨迹沿线标注日期;
   逐 6h 点;虚线表示模拟、实线表示实况(或按视频:模拟=着色线、实况=红点线)。
```

## 三、实现与接入

```
1) 新增渲染模块(建议 simulator/render/video_style.py):每类画面一个
   渲染函数 render_olr/render_refl/render_sfc/render_mid/render_high/
   render_station/render_center/render_track,输入=阶段4 逐 6h 状态+阶段2 场
   (或 WRF 输出,内核 A),输出=白底 pygame Surface,缓存按(时间,画面类型);
2) F5 render_layer 新增图层 id:video_olr/video_refl/video_sfc/video_mid/
   video_high/video_station/video_center/video_track;
3) 提供"画面模式":主视图可切换到视频同款单画面(隐藏全部 UI,仅渲染图面,
   时间轴推进时按 6h 步进刷新)——快捷键或图层面板入口;
4) 双域 D01/D02:内核 A 用 WRF 嵌套域输出;内核 B 用模拟全域+以台风中心
   缩放的放大域近似 D02;
5) 风羽/海岸线/色标均为通用绘制函数,供 8 类画面复用;
6) 与模拟解耦:画面渲染只读数据,不反作用于模拟(纯展示层)。
```

## 四、验证清单

1. 8 类画面逐帧对照视频截图(临时目录已存 wrf_t1..t6/a..h/station1-3 共 14 帧):
   标题格式、色标、图例文字、坐标范围、陆地黄色轮廓一致;
2. 单站画面:点击地图任意点 → 生成该点气压/10m 风双面板,标题格式与视频一致;
3. 双域图:D02 放大域正确跟随台风中心;共享色标;
4. 时间推进:画面随 6h 步进刷新,无跳变;快速模式帧率可接受;
5. 画面模式切换:进入/退出无残留 UI 元素;截图功能(视频同款图面)可保存。

---

# F11.【里程碑】端到端联调与演示(2026-08-07)

> 目标:一键运行完整链路(模态→环境场→台风生成→模拟→云图→视频同款画面),
> 产出可观看的模拟风季。前置:阶段 F4-F9 代码已存在(合成数据闭环);
> 本阶段 = 剩余修正 + 管线串联 + F10 画面落地 + 演示模式。

## 一、前置修正(直接引用前轮提示词,不再重复)

```
1) A2-1/A2-2/A2-3/A2-4(结构诊断 4 处占位)→ 见《执行核验修正提示词(第二轮)》;
2) F5 界面【模拟内核】选择控件 → 见同章节第二节;
3) B2 的 genesis_gpi_threshold 与 A1 真实数据属可选(合成闭环不阻塞),
   待 CDS 凭证就绪后执行。
```

## 二、端到端管线串联(run.py 重构)

```
1) run.py 单命令完成一轮模拟(示例:python simulator/run.py --seed 1 --years 3):
   a. modes 阶段1(离线/内置数据,自动生成 modes_series.json);
   b. env 阶段2:generator.generate_monthly(起点=接续段末月,长度=模拟年数×12)
      → generated 逐月场(合成库;真实库就绪后自动切换 use_real);
   c. typhoons.gen 阶段3:模式 D 全随机(默认)→ records.json;
   d. typhoons.sim 阶段4:simulate_records → 各台风 .dat(输出目录可配置,
      默认为回放程序 typhoon/ 的旁路目录,提示用户复制或直接指向);
   e. clouds 阶段5 + video_style 阶段 F10:按需渲染(默认只渲染用户
      时间轴经过的帧,快速模式 2°);
2) 每步输出与输入校验:上一步产物缺失/为空时给出明确错误与修复提示,
   不允许静默跳过(与 A1 同原则);
3) 日志:每步耗时、台风数、被拒生成数、结构诊断摘要,写 logs/run_<seed>.log;
4) 可复现:--seed 固定时整条链路结果一致(各阶段 RandomState 均由 seed 派生)。
```

## 三、F10 画面落地(优先实现 8 类中的 6 类:OLR/雷达/地面/中低层/单站/路径)

```
1) 新建 simulator/render/video_style.py(或并入 clouds/):
   - 通用画布:白底 + 顶部居中标题(黑字)+ 底部图例 + 经纬网格刻度
     + 黄色陆地轮廓(用现有地图图片派生轮廓数组,首次加载缓存);
   - 风羽绘制函数(半羽 2.5/整羽 5/旗 25 m/s,黑色);
   - 8 类画面渲染函数(输入=阶段4 逐 6h 状态+阶段2 场,输出=Surface),
     规格严格按 F10 第二节表;
2) 对接 F5 render_layer:新增图层 id video_olr/video_refl/video_sfc/
   video_mid/video_high/video_station/video_center/video_track;
3) "画面模式":主视图切换为视频同款单画面(隐藏全部 UI),时间轴 6h 步进
   刷新;快捷入口(如 F 键或图层面板底部"视频画面"按钮);
4) 单站画面:地图点选 → 生成该点(气压,10m 风)双面板(标题含站名与时段);
5) 中心强度/路径对比:实况对比线先用 xrq 最相似台风(按生成月+盆地+强度)
   作为参考(标注 "reference"),真实对比留待 WRF 与实况库就绪。
```

## 四、演示验证(端到端)

```
1) python simulator/run.py --seed 1 --years 3 一次跑通,产出:
   records.json、各台风 .dat、逐 6h 场、至少 6 类视频同款画面帧;
2) .dat 抽样(3 个台风)放入回放程序 typhoon/ 重载:路径/信息框/ACE 统计正常,
   性质标法边界(SH/CP/EP/AL/MD × 34/63/64/129/130kt)抽查正确;
3) 画面模式:时间轴播放 1 个模拟月,6 类画面逐帧切换无跳变、无 UI 残留;
   对照视频截图(wrf_t*.jpg 等 14 帧)检查标题/色标/图例/陆地轮廓;
4) 同种子重跑结果一致;不同种子(2/3)台风数与季 ACE 在基准分布内波动;
5) 修复 A2 后运行模拟,检查 EX/消散/吞并日志与结构诊断一致
   (无"硬条件消散"残留)。

---

# F12.【里程碑】阶段6:回放验证与收尾(2026-08-07)

> 目标:对模拟产物做**回放端 + 统计端**全套验证,并汇总"全部完成"收尾检查表。
> 前置:F11 端到端联调跑通、A2 结构诊断修复完成。

## 一、回放端验证(模拟 .dat 接入回放程序)

```
1) 抽样 5 个台风(覆盖 WP/SH/CP/EP/AL/MD 盆地与 TD/TS/TY/HU/ST 各性质段):
   把 simulator 输出的 .dat 复制到回放程序 typhoon/ 目录 → 重载:
   - 解析无警告、无跳行(对照 data_repo.py 解析器);
   - 路径显示、信息框(名称/强度/气压)、强度折线图正常;
   - ACE 统计:单台风 ACE 与模拟器 ace_of 计算结果一致(±0.01);
2) 性质标法边界抽查:34/63/64/129/130kt × 各盆地,回放程序显示的性质/等级
   与 nature_code 一致(重点:SH 64-129→TY、CP/EP/AL/MD 64-129→HU、≥130→ST);
3) 洋区统计对话框:模拟台风按 basin 落入正确洋区;无异常弹窗。
```

## 二、统计端验证(模拟序列 vs 基准)

```
1) 生成一个完整模拟季(默认 3 年,种子 1/2/3 各一轮):
   - 逐盆地逐月生成数分布 vs xrq 2000-2025(KS 检验,报告 p 值);
   - 季 ACE 序列 vs xrq 基准(10-90 分位区间,超区间标红);
   - 路径密度(5°×5° 网格)vs 基准;
   - 模拟 (w,p) 散点 vs xrq/miwu 散点(风压带,超带 20% 标红);
   - Vmax 逐 6h 变化率分布 vs 基准(xrq 统计);
2) 置换频率:模拟强台风(CAT3+)中经历 ≥1 次置换比例 vs 气候学(6-7 成);
3) EX/消散/吞并日志核对:每条消散记录必须有结构诊断依据(并入背景/
   被吞并/锋面接入 EX),无"硬条件消散"残留;WV/LO 形态系统不判消散;
4) 单台风形态:随机抽 2 个台风画 强度/气压/置换状态 时间序列,人工核对
   V 字曲线、气压平台、滞后 3-12h 等用户规则;
5) 结果汇总:reports/validation_report.json(含全部指标与 pass/fail)+
   控制台摘要,失败项列出原因与修复指引。
```

## 三、收尾检查表(全部完成判定)

```
□ A2 结构诊断 4 处占位已修复(锋面梯度检测/合并槽化/真实温度代理/位置环境风)
□ F5 界面【模拟内核】控件(默认合成/WRF 置灰)
□ F10 视频同款画面 8 类(至少 6 类)已落地并经 14 帧截图比对
□ F11 端到端:run.py --seed 1 --years 3 一键跑通,日志完整
□ 阶段6 本文件:回放端 3 项 + 统计端 5 项全部 pass
□ A1 真实数据:CDS 凭证配置后 build_library --use-real 重建,留出法以真实库
  为基准(未配置凭证时此项标记"待凭证",不阻塞其余项)
□ 3 种子复现:同种子结果一致,不同种子在基准分布内波动
全部 □ 勾选后,模拟器视为"阶段1-6 完成";WRF 内核 A 接入与真实数据属
后续增强项,不在此清单内。

---

# 执行核验修正提示词(第三轮:界面 bug,2026-08-07)

> 用户报告:① 界面大量文字/选项重合;② 文字莫名其妙换行被遮挡;③ 很糊;
> ④ 窗口/全屏放大后界面尺寸不变(只支持默认分辨率)。已定位 3 个根因 + 2 个连带问题。

## G1.【高】`theme.text` 默认 `max_w=0` 触发 rt 的 wrap 分支 → 所有文本被逐词拆行+空首行
- 位置:`simulator/ui/theme.py:54-55`(`def text(size, s, color=TEXT, max_w: int = 0)`
  调用 `rt(font(size), s, color, max_w)`);`app/constants/fonts.py:136-170`(rt 中
  `max_width=None` 才直接 render,`0` 会进入 wrap 分支:首词 append 空行、逐词独立成行)
- 现象:每个中文标签渲染成"空行+单行文本",向下错位一个行高,与下一行文字重叠;
  长文本被逐词拆行 → 遮挡/重合的直接原因。
- 修复提示词:
```
1) theme.text 的 max_w 默认改为 None(不 wrap,直接 render);
2) 需要换行的场景(如模态摘要、gen_msg 超长)显式传具体像素宽度;
3) 在 rt 的 wrap 分支入口加防护:`if max_width is not None and max_width <= 0:
   return f.render(text, True, color)`,防止任何调用方误传 0。
```

## G2.【高】`font(size)` 每次调用新建 Font+SmartFont → rt 缓存(id(font) 作 key)全失效
- 位置:`simulator/ui/theme.py:50-51`;`app/constants/fonts.py:143`(rt 缓存 key 含 `id(f)`)
- 现象:每个 `text()` 都加载 20MB TTF + 全量 wrap/render + 新建 canvas,缓存永不命中;
  每帧数十次字体渲染 → 性能差、GC 抖动、文字发虚(连带的"很糊")。
- 修复提示词:
```
theme.py 增加模块级字体缓存:`_FONT_CACHE: Dict[int, SmartFont] = {}`(上限 16),
`font(size)` 命中返回缓存实例,未命中创建并登记;
text()/draw_button 等全部走缓存字体;验证同一文本第二次调用命中缓存
(可打印 font.render 调用计数)。
```

## G3.【高】主循环无 VIDEORESIZE 处理 → 窗口/全屏放大后布局与内容不更新
- 位置:`simulator/run.py:120-141`(事件循环只处理 QUIT/KEYDOWN/浮层/视图/点击);
  `__init__` 中 `self.sw/sh` 与 `_layout()` 仅执行一次;`SimView(self.view_rect)` 持有 rect 副本
- 现象:窗口拉大/全屏后,顶栏/侧栏/底栏/中央视图全部保持原尺寸,仅背景扩大
  ("界面大小没有丝毫变化")。
- 修复提示词:
```
1) 事件循环增加 `elif e.type == pygame.VIDEORESIZE:` 分支:
   self.sw, self.sh = e.w, e.h;self._layout();
   self.view.rect = self.view_rect(同步 SimView 持有的 rect);
2) 建议将 SimView.rect 改为引用 view_rect(布局重建后自动同步),或统一在
   _layout 末尾调用 view.rect = view_rect;
3) 全屏(F11 或 Alt+Enter 可选)后同样触发 _layout;gen_msg/模态摘要等
   长文本按新宽度重排(依赖 G1 的显式宽度)。
```

## G4.【中】侧栏/底栏垂直与水平空间不足导致重合(滚动缺失)
- 位置:`simulator/run.py:410-450`(_draw_sidebar:17 个图层 × 28px + 4 个选项 +
  透明度滑条 + H 提示,固定 658px 高度内,小窗口/全屏高度不同时重叠)、
  `488-506`(_draw_bottom:时间显示与生成控制、内核下拉、gen_msg 在 70px 底栏内)
- 修复提示词:
```
1) 侧栏图层列表改为可滚动区:记录 _layer_scroll(滚轮在侧栏内滚动),
   行 y = r.y + 34 - _layer_scroll;滚动上限 = max(0, 总高 - 可见高);
   选项区(等值线/风圈/模态/透明度)固定在列表区下方,列表滚动不覆盖选项;
2) 底栏元素按宽度自适应:窗口过窄(<1000px)时隐藏内核下拉或并入
   "设置"弹出面板;gen_msg 改为宽度受限单行(截断+省略号);
3) 顶栏:模态摘要与种子之间加宽度预留,窗口过窄时摘要截断。
```

## G5.【中】"很糊"的连带来源与验证
- 现象:除 G2 外,若 `simulator/render/video_style.py` 内部以固定小尺寸(如 640×420)
  渲染再 `smoothscale` 拉伸到 view_rect,也会发糊。
- 修复提示词:
```
1) 先修 G2 后实测文字清晰度;若仍糊,检查 video_style 各渲染函数:
   直接以目标尺寸渲染(内部网格按 size 生成),禁止渲染后拉伸;
   2° 快速模式允许低分辨率,但标注"快速模式"且不拉伸到原尺寸以上;
2) 主循环 FPS 显示(可选调试):文字缓存生效后 1366×768 全 UI 应稳定 60fps。
```

## G6.【低】rt 的 wrap 按空格断词,对中文不友好
- 位置:`app/constants/fonts.py:149-159`
- 修复提示词:`wrap 改为逐字符宽度累计断行(优先按中文标点/空格断,
  否则按字符宽度),保证中文换行后不出现孤字/空行;max_width<=0 防护见 G1。`

## G7.【低】清理
- `simulator/run.py:189-190`(`if k == pygame.K_f: pass` 冗余分支删除);
- 顶栏"阶段1 气候模态层 · 界面骨架"副标题随功能完成度更新。

## 验证清单

1. 打开界面:顶栏/侧栏/底栏/信息面板所有文字单行、无空行、无重合;
2. 拖拽窗口拉大到全屏:五区随窗口重排,图层列表可滚动访问全部 17 项,
   选项区不被列表覆盖;
3. 文字清晰(AA),同文本第二次渲染命中缓存(text 调用计数验证);
4. 快速模式/视频画面无拉伸模糊;
5. 修复后与修复前截图对比(临时目录),确认无回归。

---

# 执行核验修正提示词(第四轮:生成模式界面接入,2026-08-07)

> 需求:把 F7 的四种生成模式(全随机 D / 决定生成点 A / 决定生成时间 B / 两者同时 C)
> 接入模拟器界面,替换现有"开始模拟"空按钮;复用 `gen.Generator.generate(mode, ...)`
> 已实现逻辑,界面只做输入采集与结果展示。位置:simulator/run.py(底栏生成控制区、
> 主视图选点)、simulator/ui/main.py(可选)。

## H1. 生成控制区改造(底栏)

```
1) 现有底栏右侧生成控制组(开始模拟/重新随机/仅生成不渲染 + 内核下拉)重构为:
   [模式下拉] [输入状态提示(动态)] [开始模拟(主按钮)] [重新随机] [仅生成不渲染]
   - 模式下拉四项:全随机(D) / 决定生成点(A) / 决定生成时间(B) / 两者同时(C);
2) 状态提示文本(gen_msg)随模式切换更新:
   - D: "全随机:直接开始生成整季台风";
   - A: "请在地图上点选生成点(左键),再次点击可改选";
   - B: "请输入生成时间(默认=时间轴当前时刻)";
   - C: "请点选生成点 + 输入生成时间";
3) 新增模式状态变量:self.gen_mode('D'/'A'/'B'/'C')、self.pick_point(坐标或 None)、
   self.pick_time(datetime 或 None);时间输入用浮层小输入框(复用 FloatPanel 风格,
   格式 YYYY-MM-DD HH,Enter 确认,非法输入提示);
4) 与内核下拉、种子按钮保持同排不重叠(窄窗口时生成控制区整体可滚动或隐藏提示)。
```

## H2. 地图选点交互(模式 A/C)

```
1) 处于 A/C 模式且点选状态开启时,主视图左键点击 → self.pick_point = 地理坐标
   (通过 latlon_to_xy 反算);绘制十字标记(白色 12px 十字+外圈),台风符号层之上;
2) 再次点击改选;右键拖拽地图时选点不移动(仅平移视图);
3) 选点后状态提示显示坐标(如 "生成点: 17.3°N 141.2°E");坐标超出
   合法范围(纬度 ±60°、经度 0-360)时提示"超出地图范围";
4) 模式 C 选点后弹出时间输入框(默认=时间轴当前时刻);模式 B 直接弹时间输入框;
5) ESC 取消选点/输入框,恢复原模式提示。
```

## H3. 生成与结果展示(对接 gen.Generator)

```
1) 点击"开始模拟"时按模式校验输入:
   - D: 无输入要求,直接调 generate('D', year=当前模拟年, month=当前月);
   - A: 无 pick_point → 提示"请先点选生成点"不执行;有 → generate('A',
     coord=self.pick_point, year/month=当前模拟时间);
   - B: 无 pick_time → 默认用时间轴当前时刻;→ generate('B', t0=...);
   - C: pick_point 与 pick_time 都必需,缺失时提示对应缺失项;
   - 环境场保证:生成前确保 api 已覆盖模拟年份(generate_monthly 缺失时
     自动补生成该年场,与 F11 管线一致);
2) 结果展示:生成失败(records 为空)时 gen_msg 显示拒绝原因汇总
   (rejections 前 3 条,红字);成功时:
   - gen_msg: "生成 N 个台风(拒绝 M 次)";
   - 把 records 转成主视图 typhoons 列表(名称=模式+序号,初始强度 w0);
   - 自动调用 simulate_records(后台线程可选,界面不冻结)→ 逐 6h 状态
     追加到 typhoons(路径/强度由主视图与信息面板显示);
   - .dat 输出目录显示在 gen_msg 或状态栏(默认 simulator/output/run_<seed>/dat,
     可配置指向回放程序 typhoon/);
3) 重新随机(种子+1)与"仅生成不渲染"(批处理,直接调 F11 run_pipeline)
   行为保持。
```

## H4. 验证清单

1. 四种模式均可从界面触发,输入缺失时提示明确且不执行;
2. 模式 A:点选坐标正确(与鼠标经纬度一致 ±0.1°),改选生效;拒绝原因可见
   (如故意点 80°N 应显示"纬度超出 40°");
3. 模式 B:时间输入框 Enter 生效,非法格式提示;默认时间=时间轴当前时刻;
4. 模式 C:点选+时间都缺失时分别提示;补齐后可生成;
5. 模式 D:一键生成整季,主视图出现台风符号,信息面板可选中查看;
6. 生成→模拟→.dat 全链路在界面完成(不依赖命令行);
7. 窗口拉伸后生成控制区不重叠(与第三轮 G3/G4 联动验证)。
```
```

---

# 执行核验修正提示词(2026-08-07)

> 背景:模拟器首版(simulator/)已实现 F4-F8 全链路骨架,运行于**合成占位数据**闭环。
> 以下为"提示词 vs 实际执行"核验发现的不符项修正提示词,按严重度排列,全部以
> 当前 simulator/ 实际文件为准。**执行顺序:先修 A 组(数据与判定正确性),再修 B 组,
> 最后 C 组;每组修完跑对应验证。**

## 一、高危不符修正(A 组)

### A1.【高】阶段2 数据源未落实:全部为合成占位场,非真实 ERA5/ORAS5
- 实际位置:`simulator/env/build_library.py`(build_real_library 为空 TODO;synth_* 系列生成全部变量;风场为 tanh+正弦,**500hPa 风直接复制 850hPa**;`build_library(use_real=False)` 默认走合成)
- 与提示词 F6 的差异:用户拍板"真实 ERA5 月平均 1979-今 + OHC 真实计算(ERA5 分层海温/ORAS5)"。当前库(1979-2026 全部 npz)是解析式合成,holdout 报告 shear corr 0.9999、mslp 0.97 是合成场特性,不能代表真实数据质量。
- 修复提示词:
```
1) 实现 build_real_library 的真实下载/解析链路(参考 G:\气象\工具\风压对应 的
   GFS/ERA5 下载与 .npz 缓存实现):
   - SST/MSLP/850/500/300/200hPa 风/700hPa 湿度:ERA5 月平均,1979-今,1° 网格;
   - OHC:先核实 ERA5 分层海温深度层与覆盖;不足则 ORAS5(0.25°→1° 重采样),
     重叠期交叉校准;热焓 = Σρ·cp·T·Δz(ρ≈1025, cp≈3985, 记录深度范围);
   - 下载失败重试+断点续传,缺失月记录元数据,不允许静默用合成场冒充真实场;
2) build_library 的降级语义改为:use_real=True 时真实库必须完整(任一变量缺失
   即报错列出缺失月),不再静默降级合成;合成库仅保留为 use_real=False 的显式选项,
   且 npz 元数据 source 必须为 'synthetic' 或 'era5'/'oras5',界面与报告可区分;
3) 重建后重跑 validate.py 留出法:以真实库 RMSE/相关为基准(合成场指标
   仅作为管线自检,不得混入报告);
4) uv_steer 深度平均必须使用真实 850/500/300hPa 三层风,禁止复制层。
```

### A2.【高】内核 B 的"合成风场+结构诊断"未实现:EX/消散/吞并判定仍是标量简化
- 实际位置:`simulator/typhoons/sim.py`(_update_nature 用高纬 36h/切变/陆上 1% 触发 EX;
  _check_dissipation 用 "vmax≤15kt 持续 24h";simulate_records 用 "距离<400km+差≥25kt" 判吞并;
  无合成风场对象、无暖心/冷心诊断、无锋面接入检测、无闭合流函数/等压线中心检测)
- 与提示词 F8(内核 B)的差异:用户规定"消散/EX/吞并判定全部基于风场结构(合成风场+结构诊断),
  禁止硬性条件";WV/LO 是合法形态;暖心+风场完整不判消散;风场合并为一个环流中心(即使槽状)
  才判吞并。
- 修复提示词:
```
1) 实现合成风场构建(每 6h):台风涡旋场(修正 Rankine 剖面,由 vmax/r34/r50/r64 生成,
   梯度风平衡气压凹陷)+ 环境背景场(uv_steer 大尺度风 + mslp 场)+ 天气尺度结构
   (锋面/槽/东风波:从 mslp/rh700 温度梯度带与参数化副高/ITCZ 合成);
2) 实现结构诊断模块(输出给判定):
   - 闭合流函数/等压线中心检测:在合成场中寻找可辨识的独立环流中心
     (中心气压极值 + 周边闭合等压线/流函数闭合圈),输出中心位置/强度/是否闭合;
   - 暖心/冷心诊断(类 Hart 相空间简化):中心附近 850-600hPa 温度距平符号与强度
     (热带暖芯 vs 斜压冷芯,用于 EX 判定与"冷水但暖心不消散"判定);
   - 锋面与中心相接:背景温度梯度带是否接入中心(触发 EX 的条件,替代"高纬36h"硬条件);
   - 双中心合并判定:两系统风场是否已合并为一个环流中心(即使呈槽状);
   - 环流完整度:中心环流是否可从背景风场中辨识(并入背景判定依据);
3) 重写判定逻辑(全部由结构诊断驱动):
   - EX 触发:锋面接入中心 或 暖心转为冷心(斜压化),不再用高纬时间硬条件;
   - 消散:环流中心消失/不可辨识(并入背景或低压槽)→ 判消散并记录原因;
     WV(东风波形态:东侧南风/北侧信风东风/西南侧弱风)与 LO(槽状低压)是合法形态,
     继续演化不判消散;冷水但暖心+风场完整 → 不判消散;
   - 吞并:两中心风场合并为一个环流中心(即使槽状)才判弱方被吞并,保留合并时
     风速较高者;距离近但风场未合并(对方移速高)不判消散;
   - 陆地:棕海效应维持/增强(开眼)是合法状态,只有风场并入背景才判消散。
```

### A3.【高】性质标法仍为 F8 旧版错误写法
- 实际位置:`simulator/typhoons/basin.py:33-43`(nature_code)
- 与用户规则差异:① ≥130kt 非 WP 盆地返回 'HU'——应**全球统一 'ST'**;
  ② SH 盆地 64-129kt 返回 'HU'——SH 不属于 NHC/CPHC 辖区,应 'TY'。
- 修复提示词:
```
nature_code 改为:
  if w < 34: return 'TD'
  if w <= 63: return 'TS'
  if w <= 129: return 'TY' if basin in ('WP', 'SH') else 'HU'   # HU 仅 CP/EP/AL/MD
  return 'ST'                                                  # ≥130kt 全球统一
```

## 二、中危不符修正(B 组)

### B1.【中】ACE 公式错误,基准/验证口径失真
- 实际位置:`simulator/typhoons/basin.py:260-266`(ace_of 用 `(w/65.0)**2`)
- 正确口径(与回放程序 app/typhoon_data.py 一致):ACE = Σ(w²/10000),w 单位 kt;
  当前公式偏差 2.37×,季 ACE 基准与全部 ACE 相关验证失真。
- 修复提示词:`ace_of 改为 `ace += (w*w)/10000.0`(保留 35kt 阈值与性质白名单
  TS/TY/ST/HU/空);重跑 season_ace 基准。`

### B2.【中】GPI 硬门槛未接入生成检查
- 实际位置:`simulator/typhoons/gen.py`(env_allowed 的 gpi_thr 参数未使用)、
  `simulator/typhoons/basin.py:282-296`(genesis_gpi_threshold 为占位,用初始风速近似)
- 修复提示词:
```
1) genesis_gpi_threshold 改为真实实现:遍历 xrq 2000-2025 生成点(首报位置),
   用阶段2 的 gpi 场(按生成月)双线性采样生成点 GPI,取 40 分位;
2) env_allowed 增加 GPI 硬门槛检查:生成点 GPI 值(api.get_field('gpi') 双线性采样)
   < 阈值时返回原因"GPI 不足";未通过不生成;
3) 重新生成记录后核对拒绝率(拒绝数/尝试数)并记录。
```

### B3.【中】KZC 未复用风压对应工具,自造公式未标定
- 实际位置:`simulator/typhoons/kzc.py`(Δp = 0.10·Vmax^1.5·(0.85+0.15·min(1.5,R34/120)),
  docstring 自标"工程近似,未校准";wind_pressure_pairs 因 xrq 无气压列实际为空,calibrate 空跑)
- 修复提示词:
```
1) 移植 G:\气象\工具\风压对应 的 KZC(Courtney & Knaff 2009)与 Chavas 模型实现,
   接口保持 kzc.min_pressure(vmax, r34, oci, lat, penv_shift);
2) 标定数据源改为 miwu(1979-2025,含气压)+ 自评目录(2025/2026)+ NHC 风格目录
   (AL/EP/CP/SH/WP):用其热带段(TD-TY/HU,排除 EX/SS/SD)的 (w, p) 散点回归
   KZC 系数,回归样本数不足时维持 KZC 原文献系数并标注"文献系数";
3) 回归结果与验证散点存 reports/,界面可查。
```

### B4.【中】21 天寿命硬上限残留,违反"禁止任何寿命上限"
- 实际位置:`simulator/typhoons/sim.py:429-436`(run(max_days=21))、
  `455-491`(simulate_records 的 range(21*4))
- 修复提示词:`去掉 max_days 截断:run/simulate_records 改为"模拟至消散判定
  或记录数超过宽限(如 360 报点=90 天,仅防死循环,达到时打警告日志并继续
  按物理判据处理,不截断)"。`

### B5.【中】环境采样用最近邻而非双线性
- 实际位置:`simulator/typhoons/sim.py:130-136`(_sample 用 int() 取整)
- 修复提示词:`路径/强度/切变/湿度等全部环境采样统一改用 _bilinear(与 uv_steer
  一致);gen.py 的 env_allowed 与 _sample_position 同步改为双线性。`

## 三、低危修正(C 组)

### C1.【低】留出法报告无失败判定
- 位置:`simulator/env/validate.py`(RMSE_SST_TARGET=0.9、CORR_TARGET=0.7 定义了但 sst corr 0.66 未告警)
- 修复提示词:`报告增加 pass/fail 字段:任一变量 corr 低于目标或 rmse 高于目标
  时标记 FAIL 并列出变量;合成库模式单独标记 (synthetic),不参与真实库达标判定。`

### C2.【低】片段复用未加季节约束,可能跨季匹配
- 位置:`simulator/env/generator.py:138-160`(_fragment_search 全历史滑窗,无季节限制)
- 修复提示词:`片段复用的候选段首月必须与目标段首月同月 ±1(即段内逐月季节对齐);
  且段内各月与目标月逐一满足季节窗口(±2),不满足的段不参与评分。`

### C3.【低】modes.py 1 月滞后窗计算错误
- 位置:`simulator/env/generator.py:36-41`(mo-lag<=0 一律映射 (y-1,12),1 月滞后 2/3 月应指向 11/10 月)
- 修复提示词:`按实际差值回退:mo-lag<=0 时 month=(mo-lag)%12 或 +12,year 相应 -1
  (如 (1,2)->(y-1,11),(1,3)->(y-1,10)),并加单元测试覆盖 1/2 月。`

### C4.【低】地中海盆地 MD 被 AL 截获,永远不可达
- 位置:`simulator/typhoons/basin.py:26-30`(basin_of 顺序 AL 在 MD 前)
- 修复提示词:`basin_of 先查 MD(30-45N, 350-360E)再查 AL;并保持 WP/SH/CP/EP/AL/MD
  判定顺序,增加 basin_of 单元测试(如 (35N,355E)->MD、(20N,300E)->AL)。`

### C5.【低】ITCZ 生成检查放宽 +12° 且只查北半球
- 位置:`simulator/typhoons/gen.py:39-42`
- 修复提示词:`去掉 +12 放宽(恢复 ±8°);南半球用季风槽带(monsoon_trough)做对称检查
  (la<0 时查与槽位纬度差)。`

### C6.【低】次月混合仅 sst 生效
- 位置:`simulator/env/generator.py:216-234`(_blend_month 中 `if var == 'sst'`)
- 修复提示词:`对全部变量做主/次月混合(权重 0.85/0.15),或在文档中明确
  "仅 sst 混合、其余单月"并给出理由(避免实现与提示词不一致)。`

### C7.【低】.dat 输出经度 >180 时写 E 不写 W
- 位置:`simulator/typhoons/sim.py:446-449`(lo_s 恒 'E')
- 修复提示词:`与 xrq/回放程序惯例一致:lo>180 时写 `int((360-lo)*10)W`,否则 `int(lo*10)E`;
  输出后用一个 .dat 用 app/data_repo.py 解析自检(解析结果与原状态一致)。`

## 四、优先级与验证

1. **A 组先修**:A1(真实数据,重建库并重跑留出法)→ A3(性质标法,一行改动)→ A2(结构诊断,工作量最大,建议拆成 合成风场→中心检测→暖心/锋面诊断→判定接线 四步);
2. **B 组**:B1(ACE)→ B2(GPI)→ B3(KZC)→ B5(双线性)→ B4(去上限);
3. **C 组**:C3/C4 附带单元测试,其余顺手修;
4. 全部修完后重跑:validate.py 留出法、生成一批次模拟(3 种子)、.dat 解析自检、
   性质标法全盆地抽样核对(含 SH/CP/EP/AL/MD 的 TS/TY/HU/ST 边界)、季 ACE 与
   xrq 基准对比。

---

# 执行核验修正提示词(第二轮,2026-08-07 晚)

> 核验结果:第一轮 A1/A2/A3、B1-B5、C1-C7 的执行状态——**A3、B1-B5、C1-C7 共 13 项已修复
> (核验证据见附表);A1 代码管线完成但数据仍为合成(待用户凭证);A2 判定接线已改、
> 但 4 个结构检测函数仍是占位;另有 F5 界面内核选择控件未实施**。本轮提示词只列
> 剩余不符项,已修复项不再重复。

## 一、A2 剩余修正(结构诊断的 4 处占位)

### A2-1【高】`front_contact` 是纬度硬编码,不是锋面检测
- 位置:`simulator/typhoons/fields.py:98-102`(`return abs(self.clat - 35.0) < 3.0`)
- 问题:锋面接入判定 = "纬度在 32-38°N"——与锋面是否真实存在无关(台风在 33°N 无锋面
  也会触发 EX;在 25°N 遇冷锋不触发)。用户规则:"探测到锋面接入台风中心 → 判定 EX"。
- 修复提示词:
```
1) 从环境场构建真实温度梯度带:用阶段2 的温度代理(mslp 派生温度或将来真实温度场)
   计算 850hPa 温度梯度 |∇T|(像素梯度→物理单位,参考 vort850_from_winds 的梯度换算);
   锋面带 = |∇T| 超过阈值(如 1.5°C/100km)的连续区域;
2) front_contact 改为:检测是否存在锋面带延伸至台风中心半径 200km 内
   (中心附近 4°×4° 窗口内 |∇T| 超阈值的连通区与中心距离 ≤200km);
3) 合成温度场的锋面位置不再写死 35°N:由 mslp 场的气压梯度/纬度带参数化
   (极地锋带随季节迁移,如 30°N+6·cos(季节)),保证占位与真实场共用同一接口。
```

### A2-2【高】`merge_with` 仍是距离阈值,未实现"合并为一个环流中心"
- 位置:`simulator/typhoons/fields.py:110-114`(merge_with 返回 d<250)、
  `simulator/typhoons/sim.py:484-491`(外层再叠加 d<500)
- 问题:注释写"两中心连线中点附近气压低于两端(单槽化)",实现只有距离判断。
  用户规则:"风场合并为一个环流中心(即使是一个槽)才能判定吞并;距离近但
  对方移速高、风场未合并 → 不判消散"。
- 修复提示词:
```
1) merge_with 改为风场合并检测:在两中心连线中点构建局部窗口(覆盖两中心),
   检查窗口内风场是否已无法区分两个独立环流中心——判据:
   a) 中点处风速显著低于两端中心附近峰值(单槽形态:中点=槽底,风弱);
   b) 或中点 3° 邻域内风场涡度/流函数只有一个极值(单中心化);
2) 删除 sim.py 吞并逻辑中多余的 `d < 500` 外层距离前置(保留 d < 800 作为
   性能裁剪即可,判定本身以 merge_with 为准);
3) 保留"合并时风速较高者存活"规则(absorb 现状),但 absorb 仅由 merge_with
   判定触发,不再由距离+强度差直接触发。
```

### A2-3【中】`warm_core` 依赖合成伪温度场,冷心判定失真
- 位置:`simulator/typhoons/fields.py:139-142`(合成温度 `t = -p*0.12 + 28.0 - 0.08*(LAT-10) + 0.4*sin(...)`)与 `88-96`(warm_core)
- 问题:暖心/冷心诊断的输入温度场是从气压凹陷派生的"伪温度",与真实大气热力结构无关;
  `_update_nature` 用 `warm < -0.3` 判 EX(斜压化),伪温度会系统性误判。
- 修复提示词:
```
1) 温度场改由环境场派生(与背景一致):850hPa 温度代理 = f(mslp, 纬度, 湿度)
   (如 T ≈ T0(lat) - k·ln(p/1000)·R/cp 静力近似,或直接用阶段2 rh700/mslp
   回归的简化温度),台风暖心叠加项仅在"热带性质且强度>阈值"时生效
   (暖心增量 ∝ Vmax,眼墙 200km 内);
2) warm_core 判据不变(中心 2° vs 周边 6-8° 距平),但输入必须是环境真实
   温度代理 + 暖心增量,不再用气压凹陷伪温度;
3) EX 触发阈值 warm<-0.3 需用修正后的温度场重新标定(可与 A2-1 的锋面
   检测共同作为 EX 双条件:front OR 冷心)。
```

### A2-4【中】环境背景风用全局均值,应取台风位置环境风
- 位置:`simulator/typhoons/fields.py:123-128`(`env_u = np.nanmean(uv[0])`)
- 问题:合成风场的背景用整场平均,台风所在区域的环境引导(如副高南缘的东风)
  被平均掉,导致"环流可辨识度"(identifiable)与背景对比失真。
- 修复提示词:`背景风取台风中心 10°×10° 窗口(排除中心 2° 涡旋区)的均值,
  或直接双线性采样中心处 uv_steer;identifiable 的对比基准同步改为该环境风。`

## 二、F5 界面:模拟内核选择控件未实施

- 位置:`simulator/ui/main.py`(生成控制区无内核选择;现有 9 图层中 cloud_globe 为"合成")
- 提示词 F5 要求:生成控制组含【模拟内核】(默认:合成架空[内核B];可选:WRF 精算[内核A],
  未配置时置灰并提示)。
- 修复提示词:
```
1) 底栏生成控制组增加"内核"下拉:默认"合成(内核B)";"WRF(内核A)"项
   仅当 WRF 环境就绪标记(如 simulator/wrf_ready.flag 存在且配置检查通过)
   才可选,否则置灰,悬停提示"需配置 WRF(WSL2/Docker + WRF/WPS + geog)";
2) 内核选择写入 run 配置(随种子一起保存/显示),批次运行按所选内核分发:
   B → 现有 simulate_records 链路;A → 调用 WRF 启动器(预留接口
   run_wrf_leg(rec, seg_hours=12) 占位,未实现前选择 A 时提示"内核A尚未接入");
3) 图层列表中"全球云图(合成)"的合成标注与内核选择联动:选 A 时该层
   切换为"WRF 云场"来源(仅展示差异,渲染接口不变)。
```

## 三、A1 数据落地:需要用户操作(非代码缺陷)

- 状态:downloader/build_real_library 已按提示词实现;当前库为合成(meta source='synthetic'),
  missing_months.json 已记录 `NO_CDS_CREDENTIALS`。
- 待办(用户侧):
```
1) 配置 CDS API 凭证:创建 %USERPROFILE%\.cdsapirc(格式 url: https://cds.climate.copernicus.eu/api
   key: <uid>:<api-key>),并在 cds.climate.copernicus.eu 注册账号;
2) ORAS5(ohc)需另行确认存储/凭证方案:若不可用,则按提示词 F6 的预案
   改回 ERA5 分层海温(核实深度层覆盖),或暂时以 ORAS5 单层近似并标注;
3) 凭证就绪后运行 `python -m simulator.env.build_library --use-real` 重建,
   缺失月清单(missing_months.json)应为空,重建后重跑 validate.py 留出法,
   以真实库指标为最终基准。
```

## 四、已验证通过项(本轮核验确认,无需再改)

| 项 | 证据 |
|---|---|
| A3 性质标法 | basin.py:43-44(≤129kt:WP/SH→TY,其余→HU;≥130kt→ST 全局) |
| B1 ACE 口径 | basin.py:266 `(w*w)/10000.0` |
| B2 GPI 门槛 | gen.py:63-68 硬门槛 + basin.py:283-306 生成点 40 分位 |
| B3 KZC/Chavas | kzc.py 完整移植(文献系数+miwu 标定入口) |
| B4 寿命上限 | sim.py:423-427 90 天宽限+告警(非截断) |
| B5 双线性 | sim.py:131-133 统一 _bilinear |
| C1-C7 | validate pass/fail;片段季节±2;滞后窗回退;MD 在 AL 前;ITCZ±8+南半球对称;次月混合全变量;dat 经度 W/E |

## 五、本轮修复后验证清单

1. 构造测试台风:25°N 无锋面(应保持热带)、36°N 有强温度梯度带(应触发 EX)、
   两中心相距 200km 但风场各自闭合(不应吞并)、两中心已槽化合并(应吞并);
2. `warm_core` 在真实温度代理下:强台风 >0.4、弱系统接近 0、变性后 < -0.3;
3. 界面:内核下拉默认"合成",WRF 置灰提示;批次运行记录内核选择;
4. 全量回归:validate 留出法(合成库)可跑通、3 种子生成、.dat 解析自检、
   性质标法边界抽样(SH/CP/EP/AL/MD × 34/63/64/129/130kt)。


---

# 体验改进规划(2026-08-13)

> 定位:在前几轮 Bug 修复(B1-B36/R1-R30/N1-N30+)、算法优化(法1-法32)与功能提示词(F1-F8)全部落地后,
> 从**最终用户视角**对 GUI 主程序(app/)与模拟器(simulator/)做一次体验走查,提出下一阶段体验改进项。
> 本规划只涉及**体验/可用性**,不重复已有 Bug 清单与性能优化;每条含 位置/现状问题/改进方案/验证,按优先级分组:
> **P0 快赢(改动小、感知强)、P1 主要体验、P2 锦上添花**。行号以当前工作区为准。

## 一、P0 快赢(1-2 天可落地)

### 1.【反馈】截图/保存成功消息用错误横幅展示,语义错位
- 位置:`app/ty_sim_mixins/keyboard_mixin.py:299`(`self.show_error(f"已保存到./picture/{fn}")`)、`app/renderer.py:97-108`(_draw_error)
- 现状:全项目只有一个错误横幅(顶部居中、红边、ERROR_TIMEOUT_MS 超时消失);截图成功、文件保存成功等**正向反馈**也走 show_error,用户会误以为出错。
- 改进方案:
```
1) 新增轻量 toast 系统(建议放 app/toast.py,由 Renderer 统一绘制):
   - API: show_toast(text, level='info'|'success'|'warning'|'error', duration_ms=2500),
     与 show_error 并存(show_error 内部转发为 level='error');
   - 渲染:屏幕顶部居中堆叠(最多 3 条),按 level 区分边框/图标色(绿=成功/蓝=信息/黄=警告/红=错误),
     沿用现有暗色圆角横幅样式(_draw_error 改造),逐条独立超时淡出;
   - 实现要点:条目存 (text, level, expire_at) 列表,每帧只重绘可见条目;文本 surface 按 (text,level) 缓存,
     复用 _light_panel_cache/_overlay_cache 同款缓存模式;F1 隐藏 UI 时不绘制。
2) 改造既有调用点(先改 3 处正向反馈):
   - keyboard_mixin._key_p 截图成功 → show_toast(f"已保存到 ./picture/{fn}", 'success');
   - point_list._save 保存成功 → show_toast(f"已保存 {os.path.basename(ty.filepath)}", 'success');
   - settings 确认应用 → show_toast("设置已保存", 'success');
   - 其余 show_error 调用保持红色语义(level='error')。
3) 验证:截图后绿色"已保存"横幅;连续 3 条 toast 堆叠不重叠;F1 隐藏时不绘制;超时后消失;
   pytest 全量通过(renderer/show_error 相关测试不回归)。
```

### 2.【信息】地图无鼠标经纬度读数(天气应用刚需)
- 位置:`app/ty_sim.py`(TySim 绘制入口)、`app/view_state.py`(screen_to_latlon 已有)、`app/utils.py`
- 现状:鼠标在地图上移动时没有任何经纬度/坐标读数,用户无法快速知道"这个位置是哪";只有编辑报点时才有坐标信息。
- 改进方案:
```
1) 在 draw() 末尾(信息框之后、特效之前)增加 HUD:鼠标位于地图区(0,0)-(sw,map_height)内时,
   用 view.screen_to_latlon(mx,my) 换算,右下角(或左上角与镜头跟踪标签错开)绘制
   "121.3°E 22.5°N"(经度>180 显示 360-x+"W",南半球纬度带 S 后缀,格式与 settings 经纬输入一致);
2) 实现要点:文本每帧按量化值(0.1° 粒度)缓存 surface,避免每帧 rt();暗色/亮色主题分别缓存;
   F1 隐藏 UI 时不绘制;编辑模式拖拽报点时同步显示被拖点坐标(替代现有拖拽坐标提示)。
3) 验证:移动鼠标读数实时变化;跨 0°/180° 经线格式正确;F1 隐藏后不显示;无对话框时可用;
   与 settings 中经纬度输入格式(如 121.3E)一致。
```

### 3.【窗口】无全屏模式
- 位置:`main.py:44`(set_mode RESIZABLE)、`app/ty_sim.py`(handle_resize)
- 现状:只有可调窗口,无全屏切换;演示/录屏场景需要全屏。
- 改进方案:
```
1) 新增快捷键 F11 切换全屏/窗口(放 keyboard_mixin,与 F12 置顶并列):
   - 全屏: pygame.display.set_mode((0,0), pygame.FULLSCREEN|pygame.RESIZABLE) 后
     读取实际尺寸并走 handle_resize(w,h)(复用现有 resize 路径,含地图 min_scale 重算);
   - 退出全屏: set_mode((cfg.screen_width, cfg.screen_height), pygame.RESIZABLE) 并 handle_resize;
   - config.json 增字段 fullscreen(bool,默认 false),切换时 save_config(),启动时按配置直接全屏;
2) 注意:全屏分辨率变化会触发 VIDEORESIZE,需防重复 set_mode;窗口置顶(F12)在全屏下忽略;
3) 验证:全屏/窗口往返无布局错乱;切换后地图比例/信息框/控制面板正常;重启后保持全屏状态;
   分辨率变化时 handle_resize 不崩溃。
```

### 4.【数据安全】编辑保存直接覆盖原文件,无备份
- 位置:`app/point_list.py:542-566`(_save 直接 open(filepath,'w'))
- 现状:编辑/新增报点后保存直接覆写 .dat,误操作(如清空点列表后保存)无法找回,无 .bak 兜底。
- 改进方案:
```
1) _save 写盘前自动备份:若目标文件存在且内容将变化,先复制为 "<原名>.bak"(同目录,覆盖旧 .bak,
   用 shutil.copy2 保留时间戳);
2) 可选二次确认(默认关,config 增 backup_on_save=true 时启用):保存前 toast "已备份至 xxx.bak";
3) 验证:编辑保存后同目录生成 .bak 且内容为保存前;无 pts 台风保存也走备份;备份失败(权限)不阻塞保存。
```

### 5.【稳定】主循环无全局异常处理,崩溃即静默退出
- 位置:`main.py:52-88`(while 循环无 try/except)
- 现状:update/draw 中任何未捕获异常直接导致窗口消失、用户数据(未保存编辑/未写盘配置)丢失,无任何提示。
- 改进方案:
```
1) main() 的 while running 循环外包 try/except Exception:
   - 异常时:先尝试 sim.save_config() 与当前编辑台风 _save(尽力保存);
   - 写崩溃日志到 logs/crash_<ts>.log(traceback.format_exc() + 当前模式/时间/台风索引上下文);
   - 弹出原生消息框(ctypes MessageBoxW 或 pygame 内错误页)提示"程序发生错误,日志已保存至 logs/crash_xxx.log",
     再退出;若已处于全屏,先恢复窗口模式再弹窗;
2) 另在 sys.excepthook 兜底非主循环线程异常(同样写日志);
3) 验证:人为注入异常(如临时 raise)后:配置与当前台风被保存、crash 日志生成且含堆栈、弹窗可见、
   进程退出码非 0;正常路径无回归。
```

### 6.【引导】首次运行无任何提示,快捷键全靠 README/设置面板
- 位置:`main.py`(TySim 构造后)、`app/settings.py:844`(快捷键面板已有,但藏在设置内)
- 现状:新用户打开即面对地图,不知道 Space/H/J/O/S 等核心操作;快捷键帮助在"设置→快捷键"里,入口深。
- 改进方案:
```
1) 首次运行(config.json 不存在或无 first_run 标记)时,在地图中央显示 1 条半透明引导条:
   "空格=播放/暂停  H=切换模式  O=台风列表  S=设置(内含快捷键大全)  F1=隐藏界面",点击/任意键后消失,
   并写 config 标记 first_run_done=true;
2) 控制面板"脚本"按钮旁新增"?"帮助按钮(三模式通用),点击直接打开设置内的
   快捷键面板(复用 show_shortcuts 逻辑,可通过 sim.dialog_mgr.sd 打开并置 show_shortcuts=True);
3) 验证:删除 config.json 后首启出现引导条且任意键消失;帮助按钮三模式可打开快捷键面板;
   F1 隐藏/显示不受影响。
```

## 二、P1 主要体验(3-5 天)

### 7.【反馈】启动加载无进度(400-800 个台风文件同步解析)
- 位置:`app/data_repo.py:117-143`(load_typhoon_files 同步 os.walk+parse)、`app/ty_sim.py`(构造)
- 现状:启动时全量解析 typhoon/ 下 .txt/.dat(数百文件),期间窗口无响应、无进度,慢盘上可达数秒。
- 改进方案:
```
1) 启动阶段显示最小 splash:在 TySim 构造前由 main.py 绘制一个纯色底+文字"正在加载台风数据…"窗口
   (pygame 首帧即显示,不阻塞事件循环),加载完成后自然过渡到主界面;
2) 若需精确进度:load_typhoon_files 增加回调 on_progress(done,total)(文件总数先 walk 一遍计数),
   splash 上画进度条;资源预载(SMCY 图标流等)同样回调进度;
3) 验证:启动期间窗口始终有画面(不白屏/不冻结);大目录(1000+ 文件)下进度平滑;加载完成即进入主界面;
   加载失败文件数在日志可见。
```

### 8.【反馈】脚本运行中无进度/状态指示
- 位置:`app/script_engine.py`(状态机 STATE_MOVING/DWELL/WAIT_USER)、`app/renderer.py`
- 现状:脚本运行(长镜头脚本可达数分钟)时用户无法得知:当前第几个目标、是移动中还是停留、何时结束;
  仅 WAIT_USER 暂停时无提示,用户可能误以为卡死。
- 改进方案:
```
1) 脚本运行期间在地图左下角(与光标经纬度 HUD 错开)显示状态条:
   "脚本:目标 3/12 · 移动中" 或 "目标 5/12 · 停留 0:08"、"脚本暂停(按任意键继续)";
   script_engine 暴露 current_target_index/total_targets/state_text 只读属性,渲染层每帧取;
2) WAIT_USER 时状态条高亮闪烁提示"按任意键继续/点击恢复";
3) 验证:长脚本运行时状态条数字随目标推进;暂停/恢复文案正确;脚本结束后状态条消失;F1 隐藏时不绘制。
```

### 9.【交互】双击台风可直接打开详情/统计
- 位置:`app/ty_sim_mixins/event_mixin.py:120-142`(单击命中报点/线段)、`app/ty_sim_mixins/keyboard_mixin.py`(K 键逻辑)
- 现状:正常/编辑模式单击只选中台风;要查看强度图需按 K,要编辑点需按 G——发现性差。
- 改进方案:
```
1) 正常模式:双击台风图标(两击间隔 <300ms 且位移 <5px,复用 _hit_point_index/图标命中判定)
   → 直接打开该台风的强度折线图(intensity_chart.activate,与 K 键一致);
2) 编辑模式:双击当前编辑台风图标 → 打开点列表(point_list.activate);
   实现要点:在 event_mixin 记录 last_click (pos,time,ty),第二次 MOUSEBUTTONDOWN 命中同台风且
   间隔达标即触发,并置标志防止 _handle_click 重复处理;
3) 验证:双击不干扰单击选中/拖拽;双击空白处无动作;双击与右键拖拽无冲突;三模式行为符合上述约定。
```

### 10.【信息】季节模式信息框 8 槽溢出无提示
- 位置:`app/constants/layout.py:19`(MAX_INFO_BOX_SLOTS=8)、`app/ty_sim_mixins/draw_info_boxes_mixin.py:305-316`
- 现状:活跃台风超过 8 个时,超出台风的信息框静默不显示,用户无从得知"还有 N 个台风没框"。
- 改进方案:
```
1) 分配信息框时若 free_slots 已空:在框区底部(或第一列下方)绘制一行小字
   "另有 N 个活跃台风未显示信息框"(N=活跃-已分配),随活跃数实时更新;
2) 可选增强(默认关):按"最新生成优先"挤掉最旧信息框(需记录分配顺序,避免复杂度,先只做提示);
3) 验证:构造 >8 个同时活跃台风,提示行存在且 N 正确;≤8 个时不显示;信息框回收后提示行消失。
```

### 11.【信息】洋区边界/名称不可见(ACE 盆域过滤无空间感)
- 位置:`app/ocean_mgr.py`(OceanAreaManager 已加载 Area_ocean.json,仅用于判定,从不绘制)、`app/ace_engine.py:60-61`
- 现状:ACE 限制按盆域时,用户看不到"NE 盆域到底覆盖哪里";地图上只有海陆色块。
- 改进方案:
```
1) 设置"显示"tab 增开关 show_ocean_areas(默认关):开启后按 Area_ocean.json 的 areas 多边形,
   用当前投影(latlon_to_screen)绘制半透明边界线+盆域代码标签(如 NE/SH/WP 等,位置取多边形中心);
   多边形点数大时按 4° 间隔抽稀(参考 _draw_path_mixin 采样策略),与 map 同投影缓存到一张 overlay surface,
   视图变化才重建;
2) 开启 ACE 盆域限制模式时自动显示对应盆域边界(高亮);
3) 验证:开关开/关即时生效;缩放/平移后边界与地图对齐;盆域标签不重叠(重叠时只画代码);
   ACE 限制盆域高亮正确;F1 隐藏时同主图一起隐藏。
```

### 12.【交互】时间跳转对话框无快捷选项
- 位置:`app/time_jump.py:55-110`(四个字段:年/月/日/时)
- 现状:季节模式 T 跳转需手输 4 个字段;常用目标(季节开始/结束、当前 ACE 年边界 7/1 或 1/1)无快捷方式。
- 改进方案:
```
1) 对话框底部加 3 个快捷按钮:【季节开始】【季节结束】【当前时间】(分别填回四个字段,不直接跳转,
   用户可再确认;或直接跳转并关闭,按按钮语义二选一,建议"填回字段"避免误跳);
2) 南半球注意:季节开始/结束按 season_ctrl.sty/edy 与 hemisphere 换算(7/1 vs 1/1);
3) 验证:三按钮在季节模式正确预填;南/北半球边界正确;手输路径行为不变。
```

### 13.【可发现】台风列表排序/过滤增强
- 位置:`app/ty_list.py:106-182`(现有 sort_mode 三档 + 搜索)
- 现状:列表已有搜索(名称/编号/盆域/文件名)与排序循环;但无"按 ACE/巅峰风速"排序,统计用户需
  打开 ACE 图才能对比。
- 改进方案:
```
1) sort_mode 增加两档:"ACE 降序"与"巅峰风速降序"(取 ty.tace / max_wind_from_points,
   与 typhoon_ace_chart 同一数据源,避免重复计算);排序按钮文案/循环顺序同步扩展;
2) 验证:两档排序结果与 ACE 图/统计面板一致;空 ACE(0)排末尾;与搜索叠加正确;性能无回退
   (排序本身 O(N log N),取值走已有缓存)。
```

### 14.【交互】对话框位置/大小不记忆
- 位置:`app/dialog_base.py`(DraggableDialog)、`app/dialog_manager.py`
- 现状:可拖拽对话框每次激活回到默认居中位置;用户调整过位置后下次打开又复位。
- 改进方案:
```
1) DraggableDialog.deactivate 时记录 bg_rect 到 sim.dialog_positions[类名](内存字典,不落盘);
   activate 时若有记录且屏幕尺寸未变,直接用记忆位置(越界则回退居中);
2) config.json 增 dialog_positions 字段(可选持久化,按类名存 x/y/w/h),save_config 时写入;
3) 验证:拖动对话框→关闭→重开位置保持;改变窗口分辨率后越界位置回退居中;多对话框互不串位。
```

## 三、P2 锦上添花(可排期)

### 15.【显示】地图无经纬网格/比例尺
- 位置:`app/map_mgr.py`(地图绘制)、`app/renderer.py`
- 建议:设置"地图"tab 增 show_graticule(默认关):按 10° 间隔画经纬虚线+经纬度标注(投影后为直线段,
  与洋区 overlay 同款缓存);比例尺可选,省略不画(经纬网格已隐含尺度)。

### 16.【显示】强度类别无图例
- 位置:`app/renderer.py`(无图例绘制)
- 建议:设置"显示"tab 增 show_legend(默认关):地图角落绘制 TD→ST 颜色条(复用 get_point_color/类别色),
  含中文类别名;与信息框/经纬网格布局错开。

### 17.【窗口】标题栏静态"台风路径模拟系统"
- 位置:`main.py:50`(set_caption 一次)
- 建议:按模式/当前时间更新标题,如"台风路径模拟系统 — 正常 · 台风名(2026-08-01)"、
  "— 台风季 · 2026-08-01 00Z"(在 sim.update 中节流更新,每秒最多 1 次,避免无谓调用)。

### 18.【设置】设置面板无搜索
- 位置:`app/settings.py`(6 个 tab、布局表驱动)
- 建议:设置面板顶部加搜索框:输入关键字时跨 tab 匹配行标签(基于 _FIELD_LABELS/_CHECK_LABELS/布局表
  的 label 文本),命中则高亮所在 tab 并跳到该行(或列出"去往:显示→大小"),纯 UI 辅助不改任何设置语义。

### 19.【模拟器】headless 批次运行进度可见性
- 位置:`simulator/run.py`、`simulator/logs/`
- 建议:headless 模式按"已生成台风/总数"输出 \r 进度行(参考 records.json 计数),并在结束打印
  汇总(台风数/ACE/耗时/输出目录);日志文件滚动保留最近 N 个(现状 gen_*.log 无限累积)。

### 20.【数据】typhoon/ 目录无"打开文件夹"入口
- 位置:`app/settings.py`(数据 tab)
- 建议:设置"数据"tab 增加【打开数据文件夹】按钮(os.startfile(TYPHOON_DIR)),便于用户手动放
  .dat/.txt 后重载(R 键/重载按钮);同理可加【打开截图目录】。

## 四、优先级与建议实施顺序

1. **第一周(P0,独立可并行)**:1 toast 系统 → 2 经纬度 HUD → 3 全屏 F11 → 4 保存备份 → 5 崩溃日志 → 6 首启引导;
   其中 1/2/3 共用渲染层新增绘制,可一起做;5 单独做(风险最低收益最高)。
2. **第二周(P1)**:7 启动进度 → 8 脚本状态条 → 9 双击详情 → 10 信息框溢出提示 → 11 洋区边界 → 12 时间跳转快捷 → 13 列表排序 → 14 对话框记忆。
3. **后续(P2)**:15-20 按需排期。

## 五、验证清单(实现后逐项确认)

1. `python -m pytest` 全量通过(现有 188 项 + 新增 toast/HUD 相关测试);
2. 三模式(正常/季节/编辑)+ 暗色/亮色主题下:新 HUD(经纬度、toast、脚本状态条、图例、网格)
   均正常且不重叠;
3. F1 隐藏 UI 时所有新增覆盖层不绘制;P 截图(F1 隐藏模式)不含 HUD;
4. 全屏切换/窗口缩放/置顶(F12)三者组合无崩溃;对话框记忆在分辨率变化后回退安全;
5. 人为注入异常:crash 日志含堆栈与上下文,当前编辑内容已尽力保存;
6. 首启引导只出现一次(config 标记);帮助按钮三模式可达快捷键面板;
7. 启动加载进度:1000+ 文件目录平滑显示,加载期间窗口可响应(至少不白屏);
8. 保存备份:编辑保存后 .bak 存在且为旧内容;重复保存覆盖 .bak。

> 注:第 1 条(toast)改造会触碰 show_error 的现有调用面,建议先加 toast 系统并保持 show_error
> 行为完全不变(内部转发),待 P0 全部通过后再逐步迁移正向消息,避免一次改太多调用点。