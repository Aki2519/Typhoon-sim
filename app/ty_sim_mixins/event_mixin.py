# py/ty_sim_mixins/event_mixin.py
"""事件处理: 鼠标点击、地图拖拽/缩放、对话框路由。"""
from __future__ import annotations
import math
from datetime import datetime
import pygame

from ..typhoon_render import _clear_geo_spline_cache


class TySimEventMixin:
    """事件处理: 键盘、鼠标点击"""

    def handle_event(self, e: pygame.event.Event) -> bool:
        # K20/R2-3: 窗口失焦时若处于拖拽中,松开按键的 MOUSEBUTTONUP 会丢失,
        # 必须复位拖拽状态,否则左键点击/右键拖动永久卡死
        # ACTIVEEVENT: gain=1 为获得焦点(无需复位),gain=0 为失去焦点
        if e.type == pygame.WINDOWFOCUSLOST:
            self._cancel_drag()
            self._drag_needs_save = False
            return False
        if e.type == pygame.ACTIVEEVENT:
            if getattr(e, 'gain', 1):
                return False
            self._cancel_drag()
            self._drag_needs_save = False
            return False

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

        # 编辑模式：右键优先拖动报点（未命中点时右键仍可拖地图）
        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if self._handle_drag_point(e):
                return True

        if self._handle_map_pan(e):
            return True

        if self._handle_map_zoom(e):
            return True

        if e.type == pygame.KEYDOWN:
            return self._handle_keydown(e)
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
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
                self.dialog_mgr.point_list.save_typhoon_to_file(ty)
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
                        lambda vals, idx=i: self.update_point_in_edit_typhoon(vals, idx)
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