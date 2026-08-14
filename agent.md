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
