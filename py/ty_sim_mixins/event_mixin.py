# py/ty_sim_mixins/event_mixin.py
"""事件处理: 鼠标点击、地图拖拽/缩放、对话框路由。"""
from __future__ import annotations
import pygame
import logging
from typing import Tuple

from ..constants import f_s, rt

logger = logging.getLogger(__name__)


class TySimEventMixin:
    """事件处理: 键盘、鼠标点击"""

    def handle_event(self, e: pygame.event.Event) -> bool:
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

        self.input_handler.handle_event(e)

        if self._handle_map_pan(e):
            return True

        if self._handle_map_zoom(e):
            return True

        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if self._handle_drag_point(e):
                return True

        if e.type == pygame.KEYDOWN:
            return self._handle_keydown(e)
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.dragging_point or self.right_button_dragging:
                return True
            return self._handle_click(e.pos)
        return False

    def _handle_map_pan(self, e: pygame.event.Event) -> bool:
        # 脚本运行时禁止拖动地图
        if hasattr(self, 'script_engine') and self.script_engine and self.script_engine.running:
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
                    ty._path_cache_drag_surf = None
                    ty._path_cache_drag_key = ()
                self.update_all_screen_points()
                self._view_dirty = True
                return True
        elif e.type == pygame.MOUSEMOTION and self.right_button_dragging:
            mx, my = e.pos
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
                factor = 1.1 if e.y > 0 else 0.9
                self.map_mgr.map_view.zoom_at(factor, mx, my)
                self.update_all_screen_points()
                self._view_dirty = True
            return True
        return False

    def _handle_drag_point(self, e: pygame.event.Event) -> bool:
        ty = self.edit_typhoon
        if not ty or not ty.pts:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            mx, my = e.pos
            if my < self.map_height:
                for i, (sx, sy) in enumerate(ty.screen_points):
                    if abs(mx - sx) < 8 and abs(my - sy) < 8:
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
                pt = ty.pts[self.drag_point_index]
                pt['la'] = la
                pt['lo'] = lo
                # 仅更新被拖动的这一个点的屏幕坐标，避免 O(N) 全量重算
                sx, sy = self.latlon_to_screen(la, lo)
                if self.drag_point_index < len(ty.screen_points):
                    ty.screen_points[self.drag_point_index] = (sx, sy)
                self._drag_needs_save = True
                # 使该台风的路径缓存失效，下一帧重绘时反映新位置
                self._invalidate_path_cache_for_ty(ty)
            return True

        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self.dragging_point:
            self.dragging_point = False
            self.drag_typhoon = None
            self.drag_point_index = -1
            if getattr(self, '_drag_needs_save', False):
                # 拖动结束后全量更新 screen_points 以修正 bbox
                ty.update_screen_points(self.latlon_to_screen)
                self._invalidate_path_cache_for_ty(ty)
                self._refresh_ace_data()
                self.dialog_mgr.point_list.save_typhoon_to_file(ty)
                self._drag_needs_save = False
            return True
        return False

    def _cancel_drag(self):
        if self.dragging_point:
            if self.drag_typhoon and self.drag_point_index != -1:
                self.drag_typhoon.undo()
                self.drag_typhoon.update_screen_points(self.latlon_to_screen)
                self._refresh_ace_data()
            self.dragging_point = False
            self.drag_typhoon = None
            self.drag_point_index = -1

    def _handle_keydown(self, e: pygame.event.Event) -> bool:
        return self.input_ctrl.handle_keydown(e)

    def on_long_press(self, mx: int, my: int) -> None:
        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if my < self.map_height:
                ty = self.edit_typhoon
                for i, (sx, sy) in enumerate(ty.screen_points):
                    if abs(mx - sx) < 8 and abs(my - sy) < 8:
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