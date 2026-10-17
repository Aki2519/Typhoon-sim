# py/ty_sim_mixins/event_mixin.py
"""事件处理: 鼠标点击、地图拖拽/缩放、对话框路由。"""
from __future__ import annotations
import math
from datetime import datetime
import pygame

from ..typhoon_render import _clear_geo_spline_cache


class TySimEventMixin:
    """事件处理: 键盘、鼠标点击"""

    def _feed_input_handler(self, e: pygame.event.Event) -> None:
        """把"本函数会提前 return、因而到不了 `self.input_handler.handle_event(e)`"的
        事件补喂给长按检测器。
        中#4/低#11: `InputHandler` 的 `_down` 状态只在它自己的 `handle_event`
        与"对话框打开"(update 里自愈)两条路复位。失焦分支与脚本面板分支既
        提前 return 又不算对话框 ⇒ `_down` 残留在 True, 200ms 后
        `update()` 会拿**按下时**的坐标触发 `on_long_press`(编辑模式 = 误弹
        报点编辑框)。这里只补喂"复位类"事件, 不喂 MOUSEBUTTONDOWN ——
        面板/失焦期间不该开始新的长按计时。
        """
        ih = getattr(self, 'input_handler', None)
        if ih is not None:
            ih.handle_event(e)

    def handle_event(self, e: pygame.event.Event) -> bool:
        # K20/R2-3: 窗口失焦时若处于拖拽中,松开按键的 MOUSEBUTTONUP 会丢失,
        # 必须复位拖拽状态,否则左键点击/右键拖动永久卡死
        # ACTIVEEVENT: gain=1 为获得焦点(无需复位),gain=0 为失去焦点
        if e.type == pygame.WINDOWFOCUSLOST:
            self._feed_input_handler(e)          # 中#4: 让 _down 真的复位
            self._cancel_drag()
            self._drag_needs_save = False
            return False
        if e.type == pygame.ACTIVEEVENT:
            if getattr(e, 'gain', 1):
                return False
            self._feed_input_handler(e)          # 中#4: 同 WINDOWFOCUSLOST
            self._cancel_drag()
            self._drag_needs_save = False
            return False

        # 名称行编辑态: 优先路由(否则中文输入/退格会误触发全局快捷键)
        if getattr(self, '_name_edit_field', None) is not None:
            if e.type == pygame.MOUSEBUTTONUP:
                # 低#11 同类: 编辑框会吞掉 UP, 补喂一次让 _down 复位
                self._feed_input_handler(e)
            if self._handle_name_edit_event(e):
                return True

        # 脚本引擎 WAIT_USER：任意按键/点击恢复
        # R2-5: WAIT_USER 期间若打开了对话框,事件交给对话框分发,
        # 否则对话框键盘操作死锁且任意按键误恢复脚本
        se = getattr(self, 'script_engine', None)
        if se and se._state == se.STATE_WAIT_USER:
            if not self.dialog_mgr.any_active():
                if e.type == pygame.KEYDOWN:
                    se.resume_from_user_wait()
                    return True
                if e.type == pygame.MOUSEBUTTONDOWN:
                    # 仅左键点击恢复脚本;右键交给地图平移,
                    # 否则等待期间右键拖拽永远无法开始(起始键被吞)
                    if e.button == 1:
                        se.resume_from_user_wait()
                        return True
                    return self._handle_map_pan(e)
                if e.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
                    return self._handle_map_pan(e)
                return True
            # 对话框打开: 交给下方对话框分发; 仅当对话框全部关闭后恢复 WAIT_USER

        # 需求 3.1：右侧脚本可视化编辑面板**优先于对话框**处理事件
        # （它覆盖在地图与右 1/4 功能栏之上；不吃事件的话时间轴拖不动）
        panel = getattr(self, 'script_panel', None)
        if panel is not None and panel.active:
            if e.type == pygame.MOUSEBUTTONUP:
                # 低#11: 面板会吃掉 UP, 且"面板打开"不是 dialog_mgr.any_active(),
                # InputHandler.update 不会自愈 ⇒ 显式补喂一次复位 _down
                self._feed_input_handler(e)
            if panel.handle_event(e):
                return True

        # 对话框优先处理（栈顶优先）
        if self.dialog_mgr.any_active():
            stack = getattr(self, '_dialog_stack', [])
            # 鼠标位置事件：按 bg_rect 层叠顺序精确路由
            if e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                          pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
                # MOUSEWHEEL 没有 .pos，用全局鼠标位置
                mouse_pos = e.pos if hasattr(e, 'pos') else pygame.mouse.get_pos()

                # ── 关键：先检查是否有对话框正在拖拽 ──
                # 拖拽时鼠标可能移出 bg_rect（快速移动 / 边缘钳制），
                # 必须把事件无条件路由给正在拖拽的对话框，否则
                # MOUSEBUTTONUP 丢失 → dragging 永远 True → 卡死
                dragging_dlg = None
                for dlg in reversed(list(stack)):
                    if dlg.active and getattr(dlg, 'dragging', False):
                        dragging_dlg = dlg
                        break

                if dragging_dlg is not None:
                    dragging_dlg.handle_event(e)
                    return True  # 拖拽期间所有鼠标事件归它

                for dlg in reversed(list(stack)):
                    if not dlg.active:
                        continue
                    if not hasattr(dlg, 'bg_rect') or not dlg.bg_rect.collidepoint(mouse_pos):
                        continue
                    # 鼠标在此对话框内 → 给它处理
                    if dlg.handle_event(e):
                        return True
                    # MOUSEBUTTONDOWN: 提升到栈顶
                    if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                        if dlg in stack:
                            stack.remove(dlg)
                            stack.append(dlg)
                    return True  # 拦截，不透传给下层对话框
                # 鼠标不在任何对话框内 → 继续 sim 自身处理
            else:
                # 键盘等事件：仅栈顶对话框
                for dlg in reversed(list(stack)):
                    if dlg.active:
                        if dlg.handle_event(e):
                            return True
                        break
                # 对话框打开时未消费的按键不得穿透到 sim 全局快捷键
                # （如设置打开时按 H 切换模式/空格播放,会绕过对话框状态）
                return True

        self.input_handler.handle_event(e)

        # 洋区编辑模式: 事件优先交给洋区编辑器(选点/拖点/加点/删点/H 退出)
        oe = getattr(self, 'ocean_edit', None)
        if oe is not None and oe.active:
            if oe.handle_event(e):
                return True

        # 编辑模式：右键优先拖动报点（未命中点时右键仍可拖地图）
        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if self._handle_drag_point(e):
                return True

        if self._handle_map_pan(e):
            return True

        # ── S4: 信息框悬停反馈(手型光标 + 框沿高亮) ──
        # 放在地图平移之后、缩放之前: **只更新悬停状态, 不消费事件** ⇒ 不影响
        # 拖图/缩放/框选等任何既有交互。命中只读 `blit_info_box` 绘制时登记的矩形。
        if e.type == pygame.MOUSEMOTION and hasattr(self, '_info_box_hit_record'):
            try:
                self._info_box_hover_update(e.pos)
            except Exception:
                self._info_box_hover_ty = None

        # ── S4b: 信息框滚轮热区(**先于**地图缩放) ──
        # 用户 2026-10-05: "在信息框的地方做一矩形, 无可见外观, 但鼠标放到矩形里
        # 滚动不是缩放而是滑动信息框, **即使在信息框之间的空白区域**; 矩形应该略宽于
        # 信息框。风季与模拟模式都这样"。
        # 优先级链与 task-59 一致: 功能栏 → **信息框热区** → 地图缩放。
        #   · 功能栏在地图区**之下**, 本处用 `my >= map_height` 直接让开(不串事件);
        #   · 热区**不可见**、略宽于框、纵向覆盖整列(含框间空白);
        #   · 内容不足一屏时 `_info_box_scroll_by` 返回 False ⇒ 回落到地图缩放
        #     (避免"在空矩形上滚半天没反应、还不能缩放")。
        if e.type == pygame.MOUSEWHEEL:
            try:
                # ⚠ 用"内容里的框总数"(`_info_box_content_n`, 绘制时写入), 不是
                #   登记表条数(那只等于"这一屏画出来的几个") —— 否则可滚范围被
                #   严重低估(实测 19 个框只滚得动 76px)。
                n_boxes = int(getattr(self, '_info_box_content_n', 0) or 0)
                if n_boxes <= 0:
                    n_boxes = len(getattr(self, '_info_box_hit', None) or {})
                if self._info_box_scroll_by(
                        getattr(e, 'y', 0),
                        # ⚠ 用绘制时记录的**本帧生效高度**(有"预报"行时高一行),
                        #   与布局同源; 否则钳制步进会和实际差一行。
                        getattr(self, '_info_box_box_h',
                                self._season_info_box_height()),
                        n_boxes):
                    return True
            except Exception:
                pass

        if self._handle_map_zoom(e):
            return True

        if e.type == pygame.KEYDOWN:
            # 模拟模式: 先处理模拟模式按键, 未消费则交给通用键盘
            if getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim'):
                if self._sim_handle_event(e):
                    return True
            return self._handle_keydown(e)
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 2:
            # 模拟模式: **中键 = 生成台风**(用户 2026-09-19, 默认开)。
            # 左键改成只选中(点地生成默认关, 见 cfg.sim_click_spawn),
            # 右键留给"未来轨迹预测"(后续再做, 目前不响应)。
            if (getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim')
                    and not self.dialog_mgr.any_active()
                    and bool(getattr(self.cfg, 'sim_middle_spawn', True))):
                mx, my = e.pos
                if my < self.map_height:
                    if self._sim_spawn_at_screen(mx, my):
                        return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # ⚠⚠ 功能栏按钮**必须先拿到优先权**, 哪怕它落在 `map_height` 以上
            # (2026-10-01: 模拟模式的图层组从功能栏第三行搬到**地图区域内**,
            #  于是下面的"地图左键"分支先把它吃掉了 —— 实测表现为
            #  **点击被消费、状态不变**, 即"看得见点不动"的安慰剂按钮。
            #  见 tools/_check_layer_clicks.py)
            # 只做一次 hit_test, 命中就走功能栏那条路(与 map_height 以下的点击同一实现)。
            # ⚠ 2026-10-01 (rev-simmix 复核): **必须限定模拟模式**。
            #   功能栏正常按钮都在 map_height 以下, 唯一浮在地图区内的面板部件是
            #   模拟模式的图层弹层(`ControlPanel.layer_popup_rect()`) —— 而该弹层
            #   不随模式切换关闭(`_layer_popup_open` 无人复位), 切到 NORMAL/EDIT 后
            #   它仍会被 draw() 画出来、hit_test() 仍返回 `layer_*`。
            #   若不限定模式, 这条前置 return 会在地图左下 ~640x46 的条带里**抢走**
            #   本来该给地图的点击: EDIT 的报点选择/线段插入(event_mixin.py:167-191)、
            #   NORMAL 的双击强度图表(193-205) 都到不了, 而改动前它们排在
            #   `_handle_click` 之前(旧兜底在 216 行) ⇒ 属本次引入的行为变化。
            #   非模拟模式下旧路径同样以 `_handle_click` 收尾, 收紧不丢功能。
            if (getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim')
                    and self.control_panel is not None
                    and self.control_panel.hit_test(e.pos) is not None):
                return self._handle_click(e.pos)
            # ── S4: 点台风信息框 ⇒ 镜头跳到该 TC(模拟模式 + 风季模式) ──
            # 用户名 2026-10-05: "左边不是有台风信息框吗, 加一个点击后跳到那个 tc
            # 的功能就行, 风季模式也加上"。
            # 位置: **在功能栏优先级之后**(功能栏按钮必须先拿到点击)、在
            #   `_sim_handle_click`(地图点击生成/选中) 与通用 `_handle_click` 之前 ⇒
            #   信息框盖住的那块地图不再被"点穿"到下面的台风/生成逻辑。
            # 命中矩形来自 `blit_info_box` 绘制时登记的那一份(不另算坐标)。
            # 模式限定在 SIM/SEASON: NORMAL 模式的框是 `_draw_icon_mixin` 另一套,
            #   且带"双击名称行改名"的既有交互(`event_mixin.py:218-226`), 不在这里动。
            if (not self.dialog_mgr.any_active()
                    and getattr(self, 'md', None) in (
                        getattr(self, 'MODE_SIM', 'sim'),
                        getattr(self, 'MODE_SEASON', 'season'))):
                _hit_ty, _hit_rect = self._info_box_at(e.pos)
                if _hit_ty is not None and self._info_box_jump_to(_hit_ty):
                    return True
            # 模拟模式: 地图左键点击 → 生成/选中台风
            if (getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim')
                    and not self.dialog_mgr.any_active()):
                mx, my = e.pos
                if my < self.map_height:
                    if self._sim_handle_click(mx, my):
                        return True
            if self._ms.active:
                # R3-4: 总结可见期间控制面板区域点击放行(面板按钮可正常使用),
                # 仅地图区域点击用于关闭总结
                cp_rect = pygame.Rect(0, self.map_height, self.screen_width,
                                      self.control_panel_height)
                if not cp_rect.collidepoint(e.pos):
                    self._ms.dismiss()
                    return True
            if self.dragging_point or self.right_button_dragging:
                return True
            if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
                mx, my = e.pos
                if my < self.map_height:
                    i = self._hit_point_index(self.edit_typhoon.screen_points, mx, my)
                    if i >= 0:
                        # 双击报点 → 打开报点列表(P1-9, 编辑模式)
                        now = pygame.time.get_ticks()
                        last = getattr(self, '_last_pt_dbl', None)
                        if (last and now - last[0] < 400
                                and math.hypot(mx - last[1][0], my - last[1][1]) < 6):
                            self.dialog_mgr.point_list.activate(self.edit_typhoon, readonly=False)
                            return True
                        self._last_pt_dbl = (now, (mx, my))
                        self._edit_selected_point = i
                        self._last_edited_point = i
                        # C9: 地图选中 ↔ 报点列表联动
                        pl = self.dialog_mgr.point_list
                        if pl.active:
                            pl.selected_index = i
                            pl.current_page = i // pl.rows_per_page
                            pl._clear_row_cache()
                        return True
                    # A4: 未命中报点 → 点击路径线段,弹出添加对话框(插值默认值)
                    if self._try_click_segment_insert(mx, my):
                        return True
            # 双击台风图标开强度折线图(P1-9, 正常模式)
            if self.md == self.MODE_NORMAL and not self.dialog_mgr.any_active():
                ty = self.current_typhoon()
                sp = getattr(ty, 'screen_points', None) if ty else None
                if sp and ty.pts and 0 <= ty.ci < len(sp):
                    ix, iy = sp[ty.ci]
                    if math.hypot(e.pos[0] - ix, e.pos[1] - iy) < 18:
                        now = pygame.time.get_ticks()
                        last = getattr(self, '_last_ty_dbl', None)
                        if (last and now - last[0] < 400
                                and math.hypot(e.pos[0] - last[1][0], e.pos[1] - last[1][1]) < 6):
                            self.dialog_mgr.intensity_chart.activate()
                            return True
                        self._last_ty_dbl = (now, e.pos)
            # 名称行双击编辑(S0 B4)
            nr = getattr(self, '_info_box_name_rect', None)
            if self.md == self.MODE_NORMAL and nr is not None and nr.collidepoint(e.pos):
                now = pygame.time.get_ticks()
                last = getattr(self, '_name_last_click', None)
                if (last and now - last[0] < 400
                        and math.hypot(e.pos[0] - last[1][0], e.pos[1] - last[1][1]) < 6):
                    self._start_name_edit(self.current_typhoon(), nr)
                    return True
                self._name_last_click = (now, e.pos)
            return self._handle_click(e.pos)
        return False

    def _handle_map_pan(self, e: pygame.event.Event) -> bool:
        # 脚本运行时禁止拖动地图;但 WAIT_USER(暂停等用户)期间允许，
        # 否则 R16 中转发来的 MOUSEMOTION/MOUSEBUTTONUP 仍被 running 守卫拦截,
        # 拖拽中的 MOUSEBUTTONUP 无法复位 right_button_dragging → 拖拽卡死
        se = getattr(self, 'script_engine', None)
        if se and se.running:
            if getattr(se, '_state', None) != getattr(se, 'STATE_WAIT_USER', None):
                return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            mx, my = e.pos
            if my < self.map_height and not self.dialog_mgr.any_active():
                self.right_button_dragging = True
                self.right_drag_start_pos = (mx, my)
                return True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            if self.right_button_dragging:
                self.right_button_dragging = False
                self._drag_offset_x = 0
                self._drag_offset_y = 0
                # 清空拖拽 bbox 缓存
                for ty in self.tys:
                    self._clear_drag_cache(ty)
                # 松手惰性重投影：仅可见台风在绘制时重算；样条用 burst 延期（拖3）
                ct = pygame.time.get_ticks()
                self.invalidate_screen_points_lazy()
                self._zoom_burst_until = ct + self._ZOOM_BURST_MS
                if self.smooth_path:
                    self._smooth_restore_due = ct + self._ZOOM_BURST_MS + 20
                self._view_dirty = True
                return True
        elif e.type == pygame.MOUSEMOTION and self.right_button_dragging:
            mx, my = e.pos
            # 钳到地图区域: 顶部 my<0 与底部 my>=map_height 都要钳,
            # 否则鼠标快速甩出窗口上下沿时 dy 突变使画面跳动/累积偏移
            if my < 0:
                my = 0
            elif my >= self.map_height:
                my = self.map_height - 1
            dx = mx - self.right_drag_start_pos[0]
            dy = my - self.right_drag_start_pos[1]
            if self.map_mgr.map_view:
                actual_dx, actual_dy = self.map_mgr.map_view.move_view(dx, dy)
                self._drag_offset_x -= actual_dx
                self._drag_offset_y -= actual_dy
                self._view_dirty = True
            self.right_drag_start_pos = (mx, my)
            return True
        return False

    def _handle_map_zoom(self, e: pygame.event.Event) -> bool:
        # 脚本运行时禁止缩放
        if hasattr(self, 'script_engine') and self.script_engine and self.script_engine.running:
            return False
        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if my >= self.map_height or self.dialog_mgr.any_active():
                return False
            if self.map_mgr.map_view:
                # 帧内多事件合并：帧末一次 zoom_at + 一次惰性失效（拖16）
                self._pending_wheel += e.y
                self._view_dirty = True
            return True
        return False

    def _handle_drag_point(self, e: pygame.event.Event) -> bool:
        ty = self.edit_typhoon
        if not ty or not ty.pts:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            if self.right_button_dragging:
                return False
            mx, my = e.pos
            if my < self.map_height:
                i = self._hit_point_index(ty.screen_points, mx, my)
                if i >= 0:
                    self.dragging_point = True
                    self.drag_typhoon = ty
                    self.drag_point_index = i
                    self.drag_start_pos = (mx, my)
                    ty.push_snapshot()
                    return True
            return False

        elif e.type == pygame.MOUSEMOTION and self.dragging_point:
            mx, my = e.pos
            if my < self.map_height:
                la, lo = self.screen_to_latlon(mx, my)
                # 编辑模式：按配置步长吸附到网格；按住 Shift 临时取消吸附
                step = getattr(self.cfg, 'edit_snap_step', 0.1)
                if step and step > 0 and not (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                    la = round(la / step) * step
                    lo = round(lo / step) * step
                pt = ty.pts[self.drag_point_index]
                if pt['la'] != la or pt['lo'] != lo:
                    pt['la'] = la
                    pt['lo'] = lo
                    # 全量重算屏幕点（含平滑样条），保证连线跟随
                    # N7: 先清 geo 样条缓存,再重投影(拖点就地修改 pts)
                    _clear_geo_spline_cache(ty)
                    ty.update_screen_points(self.latlon_to_screen)
                    # 拖动中同步重定位 ipos，避免残留旧位置与新点连出伪线
                    self._reposition_typhoon_on_path(ty)
                    self._drag_needs_save = True
                    # 使该台风的路径缓存失效，下一帧重绘时反映新位置
                    self._invalidate_path_cache_for_ty(ty)
            return True

        elif e.type == pygame.MOUSEBUTTONUP and e.button == 3 and self.dragging_point:
            self.dragging_point = False
            self.drag_typhoon = None
            self.drag_point_index = -1
            if getattr(self, '_drag_needs_save', False):
                # 拖动结束后全量更新 screen_points 以修正 bbox
                _clear_geo_spline_cache(ty)
                ty.update_screen_points(self.latlon_to_screen)
                self._invalidate_path_cache_for_ty(ty)
                self.refresh_typhoon_after_point_change(ty)
                self._refresh_ace_data(ty)
                # 拖点保存为静默(每次松手都落盘, 但不弹"已保存"提示)
                self.dialog_mgr.point_list.save_typhoon_to_file(ty, silent=True)
                self._drag_needs_save = False
            return True
        return False

    @staticmethod
    def _hit_point_index(screen_points, mx: int, my: int, tol: int = 8) -> int:
        """返回距 (mx,my) 容差内的报点索引,无则 -1。"""
        for i, (sx, sy) in enumerate(screen_points):
            if abs(mx - sx) < tol and abs(my - sy) < tol:
                return i
        return -1

    @staticmethod
    def _point_seg_dist(px, py, x1, y1, x2, y2):
        """点到线段的最近距离,返回 (距离, 最近点x, 最近点y)。"""
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(px - x1, py - y1), x1, y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
        qx, qy = x1 + t * dx, y1 + t * dy
        return math.hypot(px - qx, py - qy), qx, qy

    def _try_click_segment_insert(self, mx: int, my: int) -> bool:
        """A4: 左键点击路径线段(非报点)时,在最近位置弹添加报点对话框,
        预填两端插值的时间/强度/位置,提交后按时间排序插入。"""
        ty = self.edit_typhoon
        sp = ty.screen_points
        n = len(sp)
        if n < 2:
            return False
        best_i, best_d, best_xy = -1, 10.0, None
        for i in range(n - 1):
            x1, y1 = sp[i]
            x2, y2 = sp[i + 1]
            d, qx, qy = self._point_seg_dist(mx, my, x1, y1, x2, y2)
            if d < best_d:
                best_d, best_i, best_xy = d, i, (qx, qy)
        if best_i < 0:
            return False

        p0, p1 = ty.pts[best_i], ty.pts[best_i + 1]
        la, lo = self.screen_to_latlon(best_xy[0], best_xy[1])

        def _dt(s):
            try:
                if len(s) >= 12:
                    return datetime.strptime(s[:12], "%Y%m%d%H%M")
                return datetime.strptime(s[:10], "%Y%m%d%H")
            except (ValueError, TypeError):
                return None

        d0, d1 = _dt(p0.get('t', '')), _dt(p1.get('t', ''))
        if d0 is not None and d1 is not None and d1 > d0:
            mid = d0 + (d1 - d0) / 2
            has_min = len(p0.get('t', '')) >= 12 or len(p1.get('t', '')) >= 12
            if not has_min:
                mid = mid.replace(minute=0, second=0, microsecond=0)
            t = mid.strftime("%Y%m%d%H%M" if has_min else "%Y%m%d%H")
            st = p0.get('st', '') if (mid - d0) <= (d1 - mid) else p1.get('st', '')
        else:
            t = self.get_next_time_for_typhoon(ty)
            st = p1.get('st', '')
        w0, w1 = p0.get('w') or 0, p1.get('w') or 0
        p0v, p1v = p0.get('p') or 0, p1.get('p') or 0
        w = 15 if w0 + w1 == 0 else round((w0 + w1) / 2)
        p = 0 if p0v + p1v == 0 else round((p0v + p1v) / 2)
        current_name = ty.pts[-1]['name'] if ty.pts else (ty.sname or "")
        init = {
            'wind': str(w),
            'pressure': str(p),
            'type': st,
            'lat': f"{la:.1f}",
            'lon': f"{lo:.1f}",
            'time': t,
        }
        self.dialog_mgr.point_edit_dialog.activate(
            init, lambda vals: self.add_point_to_edit_typhoon(vals, current_name))
        return True

    def _cancel_drag(self):
        if self.dragging_point:
            if self.drag_typhoon and self.drag_point_index != -1:
                self.drag_typhoon.undo()
                self.drag_typhoon.update_screen_points(self.latlon_to_screen)
                self.refresh_typhoon_after_point_change(self.drag_typhoon)
                self._refresh_ace_data(self.drag_typhoon)
            self.dragging_point = False
            self.drag_typhoon = None
            self.drag_point_index = -1
        if self.right_button_dragging:
            self.right_button_dragging = False
            self._drag_offset_x = 0
            self._drag_offset_y = 0
            for ty in self.tys:
                self._clear_drag_cache(ty)
            self.update_all_screen_points()
            self._view_dirty = True

    def _handle_keydown(self, e: pygame.event.Event) -> bool:
        return self.input_ctrl.handle_keydown(e)

    def on_long_press(self, mx: int, my: int) -> None:
        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if my < self.map_height:
                ty = self.edit_typhoon
                i = self._hit_point_index(ty.screen_points, mx, my)
                if i >= 0:
                    pt = ty.pts[i]
                    init = {
                        'wind': str(pt.get('w', '')),
                        'pressure': str(pt.get('p', '')),
                        'type': str(pt.get('st', '')),
                        'lat': str(pt.get('la', '')),
                        'lon': str(pt.get('lo', '')),
                        'time': str(pt.get('t', ''))
                    }
                    self.dialog_mgr.point_edit_dialog.activate(
                        init,
                        lambda vals, idx=i: self.update_point_in_edit_typhoon(vals, idx),
                        point_nav=self._point_edit_nav,
                        point_index=i,
                        point_total=len(ty.pts),
                    )
                    # 与地图左键选中路径一致: 长按编辑的报点同步为选中点,
                    # 否则 _edit_selected_point 残留旧 idx,后续 Alt+方向 或 Delete 会操作错误报点
                    self._last_edited_point = i
                    self._edit_selected_point = i
                    return
                la, lo = self.screen_to_latlon(mx, my)
                current_name = (self.edit_typhoon.pts[-1]['name'] if self.edit_typhoon.pts
                                else (self.edit_typhoon.sname or ""))
                init = {
                    'wind': '',
                    'pressure': '',
                    'type': '',
                    'lat': f"{la:.1f}",
                    'lon': f"{lo:.1f}",
                    'time': self.get_next_time_for_typhoon(self.edit_typhoon)
                }
                self.dialog_mgr.point_edit_dialog.activate(
                    init, lambda vals: self.add_point_to_edit_typhoon(vals, current_name)
                )