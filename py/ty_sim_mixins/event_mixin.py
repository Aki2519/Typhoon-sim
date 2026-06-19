# py/ty_sim_mixins/event_mixin.py
"""事件处理: 键盘、鼠标点击。支持对话框焦点栈。"""
import os
import pygame
import json
from datetime import datetime
from typing import Tuple

from ..constants import (
    f_s, rt,
    CONTROL_PANEL_ROW1_Y_OFFSET, CONTROL_PANEL_ROW2_Y_OFFSET,
    CONTROL_BUTTON_W, CONTROL_BUTTON_H, SPEED_BAR_W, SPEED_BAR_H,
    BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED
)


class TySimEventMixin:
    """事件处理: 键盘、鼠标点击"""

    _last_rctrl_time: int = 0       # 右 Ctrl 防抖
    _RCTRL_DEBOUNCE_MS: int = 500  # 500ms 内忽略重复触发

    def handle_event(self, e: pygame.event.Event) -> bool:
        # 对话框优先处理（栈顶优先）
        if self.dialog_mgr.any_active():
            stack = getattr(self, '_dialog_stack', [])
            # 鼠标位置事件：按 bg_rect 层叠顺序精确路由
            if e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                          pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
                # MOUSEWHEEL 没有 .pos，用全局鼠标位置
                mouse_pos = e.pos if hasattr(e, 'pos') else pygame.mouse.get_pos()
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
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            mx, my = e.pos
            if my < self.map_height and not self.dialog_mgr.any_active():
                self.right_button_dragging = True
                self.right_drag_start_pos = (mx, my)
                return True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            if self.right_button_dragging:
                self.right_button_dragging = False
                self._view_dirty = True
                return True
        elif e.type == pygame.MOUSEMOTION and self.right_button_dragging:
            mx, my = e.pos
            dx = mx - self.right_drag_start_pos[0]
            dy = my - self.right_drag_start_pos[1]
            if self.map_mgr.map_view:
                self.map_mgr.map_view.move_view(dx, dy)
                self.update_all_screen_points()
                self._view_dirty = True
            self.right_drag_start_pos = (mx, my)
            return True
        return False

    def _handle_map_zoom(self, e: pygame.event.Event) -> bool:
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
                ty.update_screen_points(self.latlon_to_screen)
                self._drag_needs_save = True
            return True

        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self.dragging_point:
            self.dragging_point = False
            self.drag_typhoon = None
            self.drag_point_index = -1
            if getattr(self, '_drag_needs_save', False):
                ty.update_screen_points(self.latlon_to_screen)
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
        if e.key == pygame.K_r and (e.mod & pygame.KMOD_CTRL):
            self.reload_typhoons()
            return True

        key_handlers = {
            pygame.K_h: self._key_h,
            pygame.K_g: self._key_g,
            pygame.K_RCTRL: self._key_rctrl,
            pygame.K_o: self._key_o,
            pygame.K_s: self._key_s,
            pygame.K_t: self._key_t,
            pygame.K_j: self._key_t,
            pygame.K_x: self._key_x,
            pygame.K_LEFT: self._key_left,
            pygame.K_RIGHT: self._key_right,
            pygame.K_PLUS: self._key_plus,
            pygame.K_EQUALS: self._key_plus,
            pygame.K_MINUS: self._key_minus,
            pygame.K_SPACE: self._key_space,
            pygame.K_F12: self._key_f12,
            pygame.K_i: self._key_i,
            pygame.K_LEFTBRACKET: self._key_left_bracket,
            pygame.K_RIGHTBRACKET: self._key_right_bracket,
            pygame.K_k: self._key_k,
            pygame.K_r: self._key_r,
        }
        handler = key_handlers.get(e.key)
        if handler:
            return handler()

        if self.md == self.MODE_EDIT and self.edit_typhoon:
            if e.key == pygame.K_z and (e.mod & pygame.KMOD_CTRL):
                self._undo_edit()
                return True
            elif e.key == pygame.K_y and (e.mod & pygame.KMOD_CTRL):
                self._redo_edit()
                return True
        return False

    def reload_typhoons(self):
        saved_mlo, saved_Mlo = self.mlo, self.Mlo
        saved_mla, saved_Mla = self.mla, self.Mla

        self.tys.clear()
        self.load_typhoon_files()
        self.cti = 0
        self.edit_typhoon = None
        if self.md == self.MODE_EDIT and self.tys:
            self.edit_typhoon = self.tys[0]

        self._refresh_ace_data()
        self.calc_season_years()
        self.calc_yearly_ace()

        if self.md == self.MODE_SEASON:
            self.st = "010100"
            self.ste = 0
            self.sy = self.sty
            current_dt = datetime(self.sy, 1, 1, 0)
            self.current_ace_year = self.get_ace_year(current_dt)
            self.csa = self.calc_accumulated_ace_up_to(
                self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])
            )
        self.update_all_screen_points()
        if self.dialog_mgr.point_list.active:
            self.dialog_mgr.point_list.deactivate()
        if self.dialog_mgr.new_typhoon_dialog.active:
            self.dialog_mgr.new_typhoon_dialog.deactivate()

        self.mlo, self.Mlo = saved_mlo, saved_Mlo
        self.mla, self.Mla = saved_mla, saved_Mla

    def _undo_edit(self):
        if self.edit_typhoon and self.edit_typhoon.undo():
            self.edit_typhoon.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            if self.dialog_mgr.point_list.active:
                self.dialog_mgr.point_list._clear_row_cache()
                self.dialog_mgr.point_list._needs_save = True
            else:
                self.dialog_mgr.point_list.save_typhoon_to_file(self.edit_typhoon)

    def _redo_edit(self):
        if self.edit_typhoon and self.edit_typhoon.redo():
            self.edit_typhoon.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            if self.dialog_mgr.point_list.active:
                self.dialog_mgr.point_list._clear_row_cache()
                self.dialog_mgr.point_list._needs_save = True
            else:
                self.dialog_mgr.point_list.save_typhoon_to_file(self.edit_typhoon)

    def _key_h(self) -> bool:
        self.switch_mode()
        return True

    def _key_g(self) -> bool:
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self.dialog_mgr.point_list.activate(typhoon=self.edit_typhoon, readonly=False)
        else:
            current = self.current_typhoon()
            if current:
                self.dialog_mgr.point_list.activate(typhoon=current, readonly=True)
        return True

    def _key_rctrl(self) -> bool:
        now = pygame.time.get_ticks()
        if now - self._last_rctrl_time < self._RCTRL_DEBOUNCE_MS:
            return True
        self._last_rctrl_time = now
        self.reset_map()
        return True

    def _key_r(self) -> bool:
        self.restore_map_region_from_config()
        return True

    def restore_map_region_from_config(self):
        config_file = getattr(self, 'CONFIG_FILE', 'config.json')
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self.mlo = cfg.get("mlo", self.mlo)
                self.Mlo = cfg.get("Mlo", self.Mlo)
                self.mla = cfg.get("mla", self.mla)
                self.Mla = cfg.get("Mla", self.Mla)
                self.map_mgr.update_view()
                self.update_all_screen_points()
            except Exception:
                pass

    def _key_o(self) -> bool:
        self.dialog_mgr.tl.activate()
        return True

    def _key_s(self) -> bool:
        self.dialog_mgr.sd.activate()
        return True

    def _key_t(self) -> bool:
        if self.md == self.MODE_SEASON:
            self.dialog_mgr.tj.activate()
            return True
        return False

    def _key_x(self) -> bool:
        self.sp = 1.0
        return True

    def _key_left(self) -> bool:
        self.sp = max(self.mis, self.sp / 2)
        return True

    def _key_right(self) -> bool:
        self.sp = min(self.mas, self.sp * 2)
        return True

    def _key_plus(self) -> bool:
        self.sp = min(self.mas, self.sp + 1.0)
        return True

    def _key_minus(self) -> bool:
        self.sp = max(self.mis, self.sp - 1.0)
        return True

    def _key_space(self) -> bool:
        self.pl = not self.pl
        self.play_text = rt(f_s, "播放" if not self.pl else "暂停", (255, 255, 255))
        return True

    def _key_f12(self) -> bool:
        self.toggle_window_topmost()
        return True

    def _key_i(self) -> bool:
        if self.md == self.MODE_EDIT:
            self.dialog_mgr.new_typhoon_dialog.activate()
            return True
        return False

    def _key_left_bracket(self) -> bool:
        if self.md == self.MODE_NORMAL and self.tys:
            self.cti = (self.cti - 1) % len(self.tys)
            self.current_typhoon().rst()
            return True
        return False

    def _key_right_bracket(self) -> bool:
        if self.md == self.MODE_NORMAL and self.tys:
            self.cti = (self.cti + 1) % len(self.tys)
            self.current_typhoon().rst()
            return True
        return False

    def _key_k(self) -> bool:
        if self.md == self.MODE_SEASON:
            self.dialog_mgr.ace_chart.activate()
            return True
        elif self.md in (self.MODE_NORMAL, self.MODE_EDIT):
            ty = self.current_typhoon() if self.md == self.MODE_NORMAL else self.edit_typhoon
            if ty:
                self.dialog_mgr.intensity_chart.activate()
            return True
        return False

    def _handle_click(self, pos: Tuple[int, int]) -> bool:
        x, y = pos
        if y < self.map_height:
            return False
        by = self.map_height + CONTROL_PANEL_ROW1_Y_OFFSET
        sby = self.map_height + CONTROL_PANEL_ROW2_Y_OFFSET

        if 15 <= x <= 15 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
            self.pl = not self.pl
            self.play_text = rt(f_s, "播放" if not self.pl else "暂停", (255, 255, 255))
            return True
        if 105 <= x <= 105 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
            if self.tys:
                if self.md == self.MODE_NORMAL:
                    self.current_typhoon().rst()
                elif self.md == self.MODE_SEASON:
                    for ty in self.tys:
                        ty.rst()
                    self.st = "010100"
                    self.ste = 0
                    self.csa = 0.0
                    self.sy = self.sty
                    current_dt = datetime(self.sy, 1, 1, 0)
                    self.current_ace_year = self.get_ace_year(current_dt)
                    self.csa = self.calc_accumulated_ace_up_to(
                        self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])
                    )
                elif self.md == self.MODE_EDIT and self.edit_typhoon:
                    self.edit_typhoon.rst()
            return True

        if self.md == self.MODE_NORMAL:
            if 195 <= x <= 195 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
                if self.tys:
                    self.cti = (self.cti - 1) % len(self.tys)
                    self.current_typhoon().rst()
                    self.pst = 0
                    self.po = 0
                return True
            if 285 <= x <= 285 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
                if self.tys:
                    self.cti = (self.cti + 1) % len(self.tys)
                    self.current_typhoon().rst()
                    self.pst = 0
                    self.po = 0
                return True
        else:
            if 195 <= x <= 195 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
                self.dialog_mgr.new_typhoon_dialog.activate()
                return True
            if 285 <= x <= 285 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
                if self.edit_typhoon:
                    self.dialog_mgr.point_list.activate(typhoon=self.edit_typhoon, readonly=False)
                return True

        if 375 <= x <= 375 + CONTROL_BUTTON_W and by <= y <= by + CONTROL_BUTTON_H:
            self.switch_mode()
            return True

        if 465 <= x <= 465 + self.SPEED_BAR_W and by + 15 <= y <= by + 15 + self.SPEED_BAR_H:
            rx = x - 465
            self.sp = self.mis + (rx / self.SPEED_BAR_W) * (self.mas - self.mis)
            self.sp = max(self.mis, min(self.sp, self.mas))
            return True

        bw = 70
        bsp = 5
        sbx = 15
        sby = self.map_height + CONTROL_PANEL_ROW2_Y_OFFSET

        if sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            self.dialog_mgr.tl.activate()
            return True
        sbx += bw + bsp
        if sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            self.dialog_mgr.sd.activate()
            return True
        sbx += bw + bsp
        if sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            if self.md == self.MODE_SEASON:
                self.dialog_mgr.tj.activate()
            return True
        sbx += bw + bsp
        if sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            if self.md == self.MODE_SEASON:
                self.dialog_mgr.ace_chart.activate()
            return True
        sbx += bw + bsp
        undo_enabled = (self.md == self.MODE_EDIT and self.edit_typhoon)
        if undo_enabled and sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            self._undo_edit()
            return True
        sbx += bw + bsp
        redo_enabled = (self.md == self.MODE_EDIT and self.edit_typhoon)
        if redo_enabled and sbx <= x <= sbx + bw and sby <= y <= sby + CONTROL_BUTTON_H:
            self._redo_edit()
            return True

        return False

    def toggle_window_topmost(self) -> bool:
        if os.name == 'nt':
            try:
                import ctypes
                import win32gui, win32con
                hwnd = win32gui.FindWindow(None, "台风路径模拟系统")
                if hwnd:
                    if not self.window_topmost:
                        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    else:
                        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    self.window_topmost = not self.window_topmost
                    self.save_config()
                    return True
                return False
            except ImportError:
                return False
            except Exception:
                return False
        return False

    def switch_mode(self) -> None:
        if self.md == self.MODE_NORMAL:
            self.md = self.MODE_SEASON
        elif self.md == self.MODE_SEASON:
            self.md = self.MODE_EDIT
        else:
            self.md = self.MODE_NORMAL

        for ty in self.tys:
            ty.rst()
        self.pst = 0
        self.po = 0
        self._cancel_drag()

        if self.md == self.MODE_SEASON:
            self.st = "010100"
            self.ste = 0
            self.csa = 0.0
            self.sy = self.sty
            current_dt = datetime(self.sy, 1, 1, 0)
            self.current_ace_year = self.get_ace_year(current_dt)
            self.csa = self.calc_accumulated_ace_up_to(
                self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])
            )
        elif self.md == self.MODE_EDIT:
            if not self.edit_typhoon and self.tys:
                self.edit_typhoon = self.tys[0]
        else:
            self.edit_typhoon = None

        self._config_needs_save = True
        self.save_config()

    def on_long_press(self, mx: int, my: int) -> None:
        if self.md == self.MODE_EDIT and self.edit_typhoon and not self.dialog_mgr.any_active():
            if my < self.map_height:
                ty = self.edit_typhoon
                for sx, sy in ty.screen_points:
                    if abs(mx - sx) < 8 and abs(my - sy) < 8:
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