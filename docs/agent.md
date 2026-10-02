# 台风路径模拟系统 — 体验改进规划(待办)

> 状态:前期规划的体验改进项**大部分已完成**(toast/经纬度 HUD/全屏/保存备份/崩溃日志/splash/脚本状态条/双击/信息框溢出提示/洋区边界/时间跳转快捷/列表排序/对话框记忆/经纬网格/强度图例/动态标题/信息框自定义 S0 均已实现)。
> 本文件仅保留**尚未实现**的待办项。S1 为当前最高优先级,其余按 P0/P1/P2。行号以当前工作区为准,实现前请以实际代码复核。

---

# S1.【专项】编辑模式使用渐变线路径模式(未完成,2026-08 用户拍板)

> 目标:**编辑模式(MODE_EDIT)也可以选择"渐变线"路径模式**,不再被强制点阵。
> 背景:渐变线模式不绘制报点标记,多个报点连成直线且强度跨多级时无法确定报点位置,因此**不改渐变线本身的绘制逻辑,编辑模式对渐变线做适配(叠加报点标记)**。

## 一、现状与核心改动

### 1.【渲染】`_line_mode()` 放开编辑模式强制点阵
- 位置:`app/ty_sim_mixins/_draw_path_mixin.py:297-301`
- 现状:
```
def _line_mode(self) -> bool:
    """当前是否为渐变线模式（编辑模式强制点阵，不改动配置）。"""
    if self.md == self.MODE_EDIT:
        return False
    return getattr(self, 'path_mode', 'markers') == 'line'
```
- 方案:删除 `if self.md == self.MODE_EDIT: return False` 前置分支,直接跟随配置:
```
def _line_mode(self) -> bool:
    """当前是否为渐变线模式（编辑模式由配置控制）。"""
    return getattr(self, 'path_mode', 'markers') == 'line'
```
- 影响面:`_draw_path_mixin.py:385,455,506,630,814,842` 全部走同一 `_line_mode()`,放开后自动在编辑模式启用渐变线;`keyboard_mixin.py:384` 注释("编辑模式的点阵显示由渲染层强制")已过时,需同步更新。

### 2.【缓存】路径缓存 key 已含 `path_mode`(无需额外改动)
- 位置:`_draw_path_mixin.py:274-289`(`_make_path_cache_key` 第 286 行已含 path_mode)
- 结论:模式切换已触发缓存重建;`_render_drag_path_surface` 的 `drag_extra`(592-597)也含 path_mode,拖拽同样正确。

## 二、关键适配:编辑模式渐变线必须叠加报点标记(不改渐变线)

### 3.【核心】为什么需要适配
- 渐变线没有点:多报点连直线、跨多级强度时无法确定报点位置 → 编辑必须知道报点在哪(选中/拖拽/插入都以报点为对象)。
- 约束:不改 `_draw_lines_colored`(103-118)、`_build_line_colors`(324-332)、线宽/虚线规则;正常/季节/模拟渐变线观感不变。
- 方案:**编辑模式 = 渐变线 + 在报点位置叠加可辨识点标记**。

### 4.【实现】两处渲染函数在 line_mode 分支后追加报点标记
- 位置:
  - `_render_path_to_surface`(约 334-499):future 层画线分支约 398-407、traversed 层画线分支约 430-434 / 470-487;
  - `_render_drag_path_surface`(约 555-669+):future 层画线分支约 645-650;
- 方案:
```
1) line_mode 为真且 self.md == self.MODE_EDIT 时,画完渐变线后追加报点标记:
   - 复用现有点标记(_make_point_marker 303-321 / _get_circle_marker / _get_tri_marker / _get_rect_marker),
     半径沿用点阵模式 radius(_size_factors 派生),颜色取 p['color'](编辑 highlight 实色);
   - future 层追加 dim 色 + FUTURE_LINE_ALPHA(参考 408-415);traversed 层追加实色(参考 436-445);
   - 拖拽分支同样在 line_mode 分支后追加(local_marks 已存在,参考 651-659)。
2) 标记层级:渐变线在下、点标记在上,同一 surface 先 line 后 mark。
3) 不新增 config 开关(编辑模式适配恒启用);后续如要"纯线无点"再加 edit_show_point_markers。
```
- 缓存:标记在构建时一次性写入 `_path_cache_full/_path_cache_traversed` 或拖拽 future/trav 面;缓存 key 不变(path_mode 已覆盖)。

### 5.【设置文案】「路径模式」选项说明同步
- 位置:`app/settings.py:495-496`(path_mode_text / path_mode_modes)
- 方案:说明文案补充一句,如 `点阵 / 渐变线(编辑模式下渐变线同时显示报点)`;README 操作表同步。

## 三、S1 验证清单
1. 编辑模式路径模式切到"渐变线"后,地图路径立即变为渐变线(逐点强度色、EX/SS/SD 虚线、线宽 4),且每个报点位置有清晰点标记;
2. 关键场景:多报点连直线 + 强度跨多级,标记明确标出每个报点(可定位/选中/拖拽);
3. 编辑模式核心交互全部正常:左键选中、右键拖拽(网格吸附/圆环/实时坐标)、点击线段插入、Undo/Redo;
4. 渐变线绘制逻辑零改动:git diff 确认 `_draw_lines_colored`/`_build_line_colors` 无修改;正常/季节/模拟观感与改动前一致;
5. 点阵模式不回归(切回"点阵"三模式全部恢复旧观感);
6. 平滑路径(smooth_path)组合正确:**报点标记画在报点 screen_points 位置,不跟随样条插值点**;
7. 缓存切换立即重建,无旧画面残留;缩放/拖拽期间一致;
8. `python -m pytest` 全量通过;渲染/键盘测试不回归。

> 说明:本项改动面小(`_line_mode` 1 处 + 两处渲染函数叠加标记),可独立完成。

---

# 一、P0 快赢(剩 1 项)

### 6.【引导】首次运行无提示(未实现)
- 现状:无 `first_run` 标记/引导条逻辑(grep 无命中)。
- 方案:
```
1) 首次运行(config 无 first_run_done 标记)时,地图中央显示半透明引导条:
   "空格=播放/暂停 H=切换模式 O=台风列表 S=设置(内含快捷键大全) F1=隐藏界面",任意键后消失,
   写 config 标记 first_run_done=true;
2) 控制面板"脚本"旁新增"?"帮助按钮(三模式通用),点击直接打开设置内快捷键面板(复用 show_shortcuts);
3) 验证:删 config.json 后首启出现引导条且任意键消失;帮助按钮可达快捷键面板;F1 隐藏/显示不受影响。
```

---

# 二、P2 剩余(3 项)

### 18.【设置】设置面板无搜索(未实现)
- 位置:`app/settings.py`(6 tab 布局表驱动)
- 建议:面板顶部加搜索框:跨 tab 匹配行标签(_FIELD_LABELS/_CHECK_LABELS/布局表 label),命中高亮所在 tab 并跳到该行;纯 UI 辅助不改设置语义。

### 19.【模拟器】headless 批次运行进度可见性(未实现)
- 位置:`simulator/run.py`、`simulator/logs/`
- 建议:headless 按"已生成台风/总数"输出 \r 进度行,结束打印汇总(台风数/ACE/耗时/输出目录);日志滚动保留最近 N 个。

### 20.【数据】typhoon/ 目录无"打开文件夹"入口(未实现)
- 位置:`app/settings.py`(数据 tab)
- 建议:数据 tab 增加【打开数据文件夹】按钮(os.startfile(TYPHOON_DIR));同理【打开截图目录】。

---

# 三、优先级与实施顺序

1. **S1(编辑渐变线)**:先做(改动面最小,1 处 `_line_mode` + 两处渲染函数叠加标记)。
2. **P0-6 首启引导**:独立小块。
3. **P2-18/19/20**:按需排期。

# 四、验证清单(实现后逐项确认)

1. `python -m pytest` 全量通过;
2. S1:编辑模式渐变线 + 报点标记 + 交互零回归(见 S1 三节验证清单);
3. P0-6:删 config.json 后首启引导条出现一次,任意键消失,帮助按钮可达快捷键面板;
4. P2-18/19/20 各自场景通过。

> 注:本文件仅保留待办;已完成项(toast/经纬度 HUD/全屏/备份/崩溃日志/splash/脚本状态条/双击/信息框溢出/洋区边界/时间跳转快捷/列表排序/对话框记忆/网格/图例/动态标题/信息框自定义 S0)均已从本文件移除。
