# Typhoon-sim 代码审查报告(2026-08-13 三轮迭代)

> 生成方式: 28 个并发子代理(Wave1, 每个严格限定编辑范围)→ 4 个复审/修复代理(Wave2)→ 2 个只读终审代理(Wave3)。
> 迭代规则: 每轮发现 bug 后再次自检,直至一轮检测不到新 bug(Wave3 达成零新 bug)。

## 一、验证结果

| 项目 | 基线(审查前) | 最终 |
|---|---|---|
| pytest 全量 | 收集错误阻塞(`catmull_rom` 导入失败) | **188/188 通过** |
| py_compile 全部 117 个文件 | — | **0 失败** |
| 修改文件数 | — | 45(另有测试 2 个) |
| 新发现 bug | — | 40+(12 高危 / 14 中危 / 15+ 低危) |
| 修复引入的新 bug(Wave2 复审捕获) | — | 3(已修复) |
| 终审轮新 bug | — | 0 |

## 二、高危修复清单(12)

| 文件 | 问题 | 修复 |
|---|---|---|
| simulator/wrf/bogus_vortex.py | 涡旋注入风场旋转方向反(反气旋位相) | 改为 `ang=atan2(la-lat,lo-lon)`,u=-v·sin, v=+v·cos |
| simulator/render/video_style.py | 等压线/850、200hPa 风羽/路径轨迹 3 处地图上下颠倒 | 统一 north-up 翻转 + `_map_rect` 参数纠正 |
| simulator/typhoons/sim.py | .dat 文件名双 `b`(`bbwp01xxxx.dat`) | 去掉重复前缀,`dat_prefix` 已含 'b' |
| app/ace_engine.py + app/typhoon_data.py | py/→app/ 迁移丢失 ACE **35kt 风速阈值**(弱 TS 虚增 ACE) | 恢复 `w>=35`(与测试/旧版/agent.md B1 一致) |
| app/config.py | `update_from` 迁移丢失(test_config 失败根因) | 恢复该方法 |
| app/spline.py | 公开 `catmull_rom` 迁移丢失 + centripetal 参数缺钳制 + 首段不平滑 | 恢复 uniform 包装函数、恢复 t 钳制、补第一段 |
| app/ty_sim_mixins/keyboard_mixin.py | 截图 `_os.path.join` NameError(P 键必崩) | 改 `os.path.join` |
| app/point_list.py | 点编辑异常路径"幽灵撤销"(撤销栈残留未消费快照) | 异常时先 `undo()` 再 raise |
| app/statistics/chart_helpers.py | haversine 链未归一化经度差(跨 0°/日期变更线路径长度虚高) | 经度差归一化到 (-180,180] |
| simulator/env/field_io.py | 4D `uv_steer` 被 3 维校验误拒(引导气流场永不可加载) | 按变量区分维度期望 |
| simulator/env/real_source.py | Open-Meteo 经度换算后 0..360 索引 KeyError(经度≥180 必崩) | 按全局点序反推原始列号 |
| app/settings.py | F3"角点+大小"地图模式 3 字段静默不生效 | 补 span 校验 + 角点换算 + 错误标记 |

## 三、中危修复清单(14)

- app/ty_sim.py: 中间年份跨年漏触发月度总结(修复)
- app/ty_sim_mixins/event_mixin.py: R16 残留——WAIT_USER 转发事件被 running 守卫二次拦截(修复)
- app/input_field.py: 粘贴绕过 validator + 选区替换索引错误(修复)
- simulator/wrf/wrfout_to_fields.py: vort850/GPI 域外写 0(应 NaN)+ 首帧重复读盘(修复)
- simulator/wrf/fetch_gfs.py: GRIB 有效性只查 size,补魔数校验(修复)
- simulator/env/real_source.py + build_library.py: 相对涡度公式轴交叉(Wave1 修复未生效,复审再修;数值验证 =2ω)(修复)
- simulator/typhoons/sim.py: EX 冷压带不重置 + 眼壁置换阶段时长共用计数(修复)
- simulator/typhoons/gen.py: C5 南半球只查 ITCZ、B5 GPI 最近邻、模式 B 时间偏移不对称(修复)
- app/statistics/season_stats.py: 单趟化重构丢失 `_ace_eligible` 35kt 阈值(Wave2 复审发现并修复)+ 删二次重扫(修复)
- app/summary_effect.py: 紫滤镜每帧重算;缓存键缺滤镜强度(Wave2 复审发现并修复)(修复)
- app/control_panel.py: 速度条填充比例未钳制(修复)
- simulator/run.py: 内核 A 回落分支缺口、设置面板输入时快捷键串扰、A/C 坐标校验 and→or(修复)
- app/ty_sim_mixins/track_mixin.py + app/season_ctrl.py: 总览零跨度除零;`_lf_last` 回卷/重置未清(漏记登陆)(修复)
- simulator/ui/main.py ModePanel.draw + simulator/run.py: 空序列 `min()` ValueError 崩溃(修复)
- app/playback_ctrl.py: 登陆特效预载尺寸与绘制不一致(预载失效)(修复)

## 四、低危/健壮性(节选)

fonts.py 中文换行(G6)与无头导入自动 init;ty_list 行 surface 每帧重建(N25);settings 切 tab 丢角点字段(R30 分支);信息框/摘要文本渲染缓存(法8/法9 残留);input_handler 长按状态残留;插值 CSA 越界守卫;validate 默认年份漏 2026;param_fields 全 NaN 场守卫;modes 死代码 IO;display 负尺寸 clamp + LAYER_META 回退;typhoon_sort_data 标注 4→5 元组;测试补 pygame.init()/MockSim 属性;summary_effect 缓存键补滤镜强度;path_length_viewer `_ts_eligible` 补 35kt(R25 口径统一);script_engine repeat 目标 index 全局唯一;bogus `_destagger` 死代码清理等。

## 五、遗留事项(需人工确认/设计决策,非代码 bug)

1. **GPI/切变/SST 硬门槛已按"用户决策"移除,仅作采样权重**——与 agent.md F7 规格冲突,建议确认
2. **NI 盆地 64-129kt 标 TY 而非 HU**(当前按"HU 仅 NHC/CPHC 辖区"语义,合理但提示词字面不同)
3. **precip 取 RAINC+RAINNC 累计值跨日重叠**(仅显示用,消费方确认)
4. **多年度"洋区对比"不应用 ace_geo_limit_enabled,盆域之和可 ≠ 年度总 ACE**
5. **F10 规格偏差**:render_center 用 kt 非 km/h、MODEL_TAG 为 IFS/ERA5 非 WRF、缺 500hPa 温度/比湿/100hPa 层次(需 field_io 扩展字段)
6. **GUI 与 headless 输出目录布局不一致**(records.json 位置)
7. **DPI 默认值不一致**:config.py `disable_dpi_scaling` 默认 False vs main.py 缺省 True(首启/次启观感不一致)
8. R23 滚轮锚点"帧末重查鼠标位置"为已知设计取舍;SH 回卷点"数据播完到次年 7/1 空转"为 M4 设计取舍
9. 性能建议:heatmap Phase3 像素循环、intensity_comparison 裸 strptime、wrf_fields `_day` 缓存无上限、smcy_icon 预载预算

## 六、变更范围

- 修改 45 个文件:app/ 27 个、simulator/ 15 个、tests/ 2 个、main.py 0(未改)
- **所有改动未提交**(仓库本身处于 py/→app/ 迁移的未提交状态,含大量既有 staged 改动)
- 审查前基线快照:`%TEMP%\typhoon-sim-baseline\`(含 hashes.txt),可逐文件 diff 核对
- 期间清理了子代理遗留的杂散文件(tmp_*.py/sh、ctx_*.txt 等),根目录现仅含项目原文件

---

# 附:遗留事项决策执行轮(用户拍板后,2026-08-13)

| # | 决策 | 执行 |
|---|---|---|
| 1 | GPI/切变/SST 硬门槛 | **保留现状**(仅作采样权重),不改代码 |
| 2 | 性质标法:HU 仅 (EP,AL,CP,MD),其余 TY | ✅ simulator/typhoons/basin.py `nature_code` 64-129kt 分支改为 `'HU' if basin in ('EP','AL','CP','MD') else 'TY'`(注释注明无独立南大西洋代码,南半球 SH→TY);测试 14 passed |
| 3 | precip 改为逐日差分 | ✅ wrfout_to_fields.py precip24 日总量差分(跨日状态 `pcum_prev`),首日帧内差分、单帧 NaN;real_source.py 侧同日累计差分 |
| 4 | 洋区对比口径 | ⏸ 待用户确认(见下"待确认") |
| 5 | F10:kt/角标不变;渲染内容改 | ✅ video_style.py render_sfc(双域:24h 降水填色+H/L/T 标记 / 10m 风矢量)、render_mid(500hPa 温度红虚线+850hPa 比湿绿阴影)、render_high(100hPa 等值线+200hPa 风羽+散度填色);字段供给:field_io/build_library(合成库 7 新场)/real_source(Open-Meteo)/wrfout_to_fields 全部落地,缺失字段优雅回退 |
| 6 | GUI/headless 输出目录统一 | ✅ run.py GUI records.json 移入 `run_{seed}/` 并新增 run.log |
| 7 | DPI 默认统一 | ✅ config.py `disable_dpi_scaling=True`;settings.py 重置路径硬编码同步为 True |
| 8 | R23 滚轮/SH 回卷空转 | 不改(设计取舍) |
| 9 | 性能 | ✅ heatmap Phase3 向量化(逐位一致)、intensity_comparison strptime lru 缓存、wrf_fields `_day` LRU(16)、smcy_icon 预载预算 3→6;settings 双 recalc 消除(`_ace_recalc_dirty`);run.py 模式 D 传 month + gen.py 模式 D 起始月对齐 `range(month or 1, 13)`;死代码清理(ui/main.py 9 个未用函数、season_ctrl `_finale` 残留) |
| 10 | README 脚本语法 + downloader 命名 | ✅ README 脚本示例按 script_engine 实际语法重写(实测可解析);downloader `_NO_CRED` → `_HAS_CRED` |

**验证**:全量 pytest 188/188 通过;117/117 py_compile;F10 三画面 mock 实测(有数据/缺数据/无台风)不崩溃且优雅回退;heatmap 新旧逐位一致;settings 重算计数场景 4 例全部符合预期。

**待确认(第 4 项)**:多年度 ACE"洋区对比"页签是否应遵守设置里的 ACE 地理限制(现状:不遵守,各洋区之和可能 > 年度总 ACE)。方案 a=遵守(数据一致,推荐)/ b=保持全量分解+界面说明。
