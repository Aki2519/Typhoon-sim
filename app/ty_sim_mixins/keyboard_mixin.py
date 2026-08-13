# py/ty_sim_mixins/keyboard_mixin.py
"""键盘快捷键、模式切换、数据重载。"""
from __future__ import annotations
import os
import json
import logging
import pygame
from datetime import datetime

from ..constants import f_s, rt, HEMISPHERE_SOUTH, CONFIG_FILE

logger = logging.getLogger(__name__)


class TySimKeyboardMixin:
    """Mixin: 键盘快捷键、模式切换、数据重载、截图。"""

    def reload_typhoons(self):
        saved_mlo, saved_Mlo = self.mlo, self.Mlo
        saved_mla, saved_Mla = self.mla, self.Mla

        saved_basin = saved_num = None
        if self.edit_typhoon:
            saved_basin = self.edit_typhoon.basin
            saved_num = self.edit_typhoon.n

        self.repo.reload_typhoons()
        self.cti = self.repo.cti
        self.edit_typhoon = self.repo.edit_typhoon
        # 数据重载后季节缓存失效,否则切回 SEASON 仍恢复旧 st/sy/csa
        self._has_season_cache = False

        # 清空所有持有旧 Typhoon 对象引用的缓存，防止泄漏
        self._info_box_cache_typhoon.clear()
        self._info_box_last_data.clear()
        self._season_info_box_cache.clear()
        self._season_info_box_last_data.clear()
        try:
            # 模块级地理样条缓存: id 键可能被新数据复用,必须整体清空
            from ..typhoon_render import _geo_spline_cache
            _geo_spline_cache.clear()
        except Exception:
            pass
        if hasattr(self, '_name_anim'):
            self._name_anim.clear()
        if hasattr(self, '_name_shadow_cache'):
            self._name_shadow_cache.clear()
        if hasattr(self, '_clear_icon_scale_caches'):
            self._clear_icon_scale_caches()
        if hasattr(self, 'playback_ctrl'):
            self.playback_ctrl._was_fin.clear()
            self.playback_ctrl._lf_last.clear()
            self.playback_ctrl.landfall_records.clear()
            # N18: 清空特效列表与总结条状态,防旧特效引用旧台风对象
            self.playback_ctrl.effects = []
            try:
                from ..summary_effect import _slot_registry, _wait_queue
                _slot_registry.clear()
                _wait_queue.clear()
            except Exception:
                pass
        if self.md == self.MODE_EDIT and self.tys:
            found = None
            if saved_basin and saved_num:
                for ty in self.tys:
                    if ty.basin == saved_basin and ty.n == saved_num:
                        found = ty
                        break
            self.edit_typhoon = found if found else self.tys[0]

        if hasattr(self, 'season_ctrl') and self.md == self.MODE_SEASON:
            sc = self.season_ctrl
            sc.reset_to_first_year()
            self.sty = sc.sty
            self.edy = sc.edy
            self._sync_season_state()
        self.update_all_screen_points()
        if hasattr(self, '_panel'):
            self._panel = None
        if self.dialog_mgr.point_list.active:
            self.dialog_mgr.point_list.deactivate()
        if self.dialog_mgr.new_typhoon_dialog.active:
            self.dialog_mgr.new_typhoon_dialog.deactivate()

        self.mlo, self.Mlo = saved_mlo, saved_Mlo
        self.mla, self.Mla = saved_mla, saved_Mla

    def _apply_history(self, action: str) -> None:
        if not self.edit_typhoon:
            return
        fn = getattr(self.edit_typhoon, action, None)
        if fn and fn():
            self.edit_typhoon.update_screen_points(self.latlon_to_screen)
            self.refresh_typhoon_after_point_change(self.edit_typhoon)
            self._refresh_ace_data(self.edit_typhoon)
            self._season_info_box_cache.pop(self.edit_typhoon, None)
            self._season_info_box_last_data.pop(self.edit_typhoon, None)
            self._last_edited_point = None
            if self.dialog_mgr.point_list.active:
                self.dialog_mgr.point_list._clear_row_cache()
                self.dialog_mgr.point_list._needs_save = True
            else:
                self.dialog_mgr.point_list.save_typhoon_to_file(self.edit_typhoon)

    def _undo_edit(self):
        self._apply_history('undo')

    def _redo_edit(self):
        self._apply_history('redo')

    def _key_g(self) -> bool:
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self.dialog_mgr.point_list.activate(typhoon=self.edit_typhoon, readonly=False)
        else:
            current = self.current_typhoon()
            if current:
                self.dialog_mgr.point_list.activate(typhoon=current, readonly=True)
        return True

    def _key_r(self) -> bool:
        self.restore_map_region_from_config()
        return True

    def restore_map_region_from_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            logger.debug("restore_map_region_from_config load failed", exc_info=True)
            return
        if not isinstance(raw, dict):
            return
        from ..config import AppConfig
        from dataclasses import fields as _dfields
        known = {f.name: f for f in _dfields(AppConfig)}
        changed = False
        for key in ('mlo', 'Mlo', 'mla', 'Mla'):
            fld = known.get(key)
            if fld is None or key not in raw:
                continue
            rv = raw[key]
            if not isinstance(rv, (int, float)) or isinstance(rv, bool):
                continue
            val = AppConfig._coerce(fld, rv)
            if val != getattr(self, key):
                setattr(self, key, val)
                changed = True
        if changed:
            self.map_mgr.update_view()
            self.update_all_screen_points()

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
        # R4-3: 统一走 _set_playing(清理单点完成标志/记录对话框期间手动切换)
        self._set_playing(not self.pl)
        return True

    def _key_f1(self) -> bool:
        self._ui_hidden = not self._ui_hidden
        return True

    def _key_f12(self) -> bool:
        self.toggle_window_topmost()
        return True

    def _key_i(self) -> bool:
        if self.md == self.MODE_EDIT:
            self.dialog_mgr.new_typhoon_dialog.activate()
            return True
        return False

    def _edit_ty_idx(self) -> int:
        """法27: 编辑台风当前索引(缓存,防每键 O(N) index)。"""
        if not self.edit_typhoon:
            return 0
        idx = getattr(self, '_edit_idx', -1)
        if idx < 0 or idx >= len(self.tys) or self.tys[idx] is not self.edit_typhoon:
            idx = self.tys.index(self.edit_typhoon) if self.edit_typhoon in self.tys else 0
            self._edit_idx = idx
        return idx

    def _key_left_bracket(self) -> bool:
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti - 1) % len(self.tys)
                self.current_typhoon().rst()
            elif self.md == self.MODE_EDIT:
                idx = self._edit_ty_idx()
                idx = (idx - 1) % len(self.tys)
                self._edit_idx = idx
                self.edit_typhoon = self.tys[idx]
                self.edit_typhoon.rst()
                # 切换编辑台风后复位选中点,避免旧索引越界
                self._edit_selected_point = None
            return True
        return False

    def _key_right_bracket(self) -> bool:
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti + 1) % len(self.tys)
                self.current_typhoon().rst()
            elif self.md == self.MODE_EDIT:
                idx = self._edit_ty_idx()
                idx = (idx + 1) % len(self.tys)
                self._edit_idx = idx
                self.edit_typhoon = self.tys[idx]
                self.edit_typhoon.rst()
                # 切换编辑台风后复位选中点,避免旧索引越界
                self._edit_selected_point = None
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

    def _key_p(self) -> bool:
        out_dir = "./picture"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"screenshot_{ts}.png"
        fp = os.path.join(out_dir, fn)
        try:
            top = self._get_top_dialog()
            moved = False
            if top:
                tmp = pygame.Surface((top.bg_rect.width, top.bg_rect.height))
                ox, oy = top.bg_rect.x, top.bg_rect.y
                top.bg_rect.x = 0
                top.bg_rect.y = 0
                moved = True
                if hasattr(top, '_layout_valid'):
                    top._layout_valid = False
                if hasattr(top, '_compute_layout'):
                    top._compute_layout()
                top.draw(tmp)
            try:
                if top:
                    pygame.image.save(tmp, fp)
                elif self._ui_hidden:
                    tmp = pygame.Surface((self.screen_width, self.map_height))
                    self.renderer._draw_scene(tmp, hidden=True)
                    pygame.image.save(tmp, fp)
                else:
                    tmp = pygame.Surface((self.screen_width, self.screen_height))
                    self.draw(tmp)
                    pygame.image.save(tmp, fp)
            finally:
                if moved:
                    # K31: 无论绘制是否抛异常都恢复对话框位置
                    top.bg_rect.x = ox
                    top.bg_rect.y = oy
                    if hasattr(top, '_layout_valid'):
                        top._layout_valid = False
                    if hasattr(top, '_compute_layout'):
                        top._compute_layout()
            self.show_error(f"已保存到./picture/{fn}")
        except Exception as ex:
            logger.error(f"截图失败: {ex}")
            self.show_error(f"截图失败: {ex}")
        return True

    def _get_top_dialog(self):
        stack = getattr(self, '_dialog_stack', [])
        for d in reversed(stack):
            if d.active:
                return d
        return None

    def _key_j(self) -> bool:
        self.script_dialog.activate()
        return True

    def set_window_topmost(self, state: bool) -> bool:
        if os.name == 'nt':
            try:
                import win32gui, win32con
                hwnd = win32gui.FindWindow(None, "台风路径模拟系统")
                if hwnd:
                    win32gui.SetWindowPos(hwnd,
                                          win32con.HWND_TOPMOST if state else win32con.HWND_NOTOPMOST,
                                          0, 0, 0, 0,
                                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    return True
                return False
            except ImportError:
                return False
            except Exception:
                logger.debug("set_window_topmost failed", exc_info=True)
                return False
        return False

    def toggle_window_topmost(self) -> bool:
        new_state = not self.window_topmost
        if self.set_window_topmost(new_state):
            self.window_topmost = new_state
            self.save_config()
            return True
        return False

    def switch_mode(self) -> None:
        if hasattr(self, '_panel'):
            self._panel = None
        if self.md == self.MODE_SEASON:
            self._cached_season_st = self.st
            self._cached_season_ste = self.ste
            self._cached_season_sy = self.sy
            self._cached_season_csa = self.csa
            self._has_season_cache = True

        # 编辑模式的点阵显示由渲染层强制（_line_mode），无需改动配置

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
        self._sync_land_state()
        self._invalidate_all_path_caches()

        if self.md == self.MODE_SEASON:
            if self._has_season_cache and self._cached_season_st is not None:
                self.st = self._cached_season_st
                self.ste = self._cached_season_ste
                self.sy = self._cached_season_sy
                self.csa = self._cached_season_csa
                self.current_ace_year = self.get_ace_year(self._season_dt())
            else:
                if self.hemisphere == HEMISPHERE_SOUTH:
                    self.st = "070100"
                else:
                    self.st = "010100"
                self.ste = 0
                self.csa = 0.0
                self.sy = self.sty
                current_dt = datetime(self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
                self.current_ace_year = self.get_ace_year(current_dt)
                self.csa = self.calc_accumulated_ace_up_to(
                    self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
            self._sync_to_season_ctrl()
            # 按当前风季时间对齐台风状态：已结束的跳过、进行中的定位到当前时刻、未开始的等待
            self.season_ctrl.jump_to(self._season_dt())
            self._sync_season_state()
        elif self.md == self.MODE_EDIT:
            if not self.edit_typhoon and self.tys:
                self.edit_typhoon = self.tys[0]
            self._edit_selected_point = self._last_edited_point if self._last_edited_point is not None else 0
        else:
            pass

        self._config_needs_save = True
        self.save_config()

    def _handle_click(self, pos):
        cp = self.control_panel
        speed_ratio = cp.hit_test_speed_bar(pos)
        if speed_ratio is not None:
            self.sp = round(self.mis + speed_ratio * (self.mas - self.mis), 1)
            return True
        btn_key = cp.hit_test(pos)
        if btn_key is None:
            return False
        handler = getattr(self, f"_btn_{btn_key}", None)
        if handler:
            return handler()
        return False

    def _btn_play(self) -> bool:
        # R4-3: 统一走 _set_playing(清理单点完成标志/记录对话框期间手动切换)
        self._set_playing(not self.pl)
        return True

    def _btn_reset(self) -> bool:
        if not self.tys:
            return True
        if self.md == self.MODE_NORMAL:
            self.current_typhoon().rst()
        elif self.md == self.MODE_SEASON:
            self.season_ctrl.reset_to_first_year()
            self._sync_season_state()
            self.update_all_screen_points()
        elif self.md == self.MODE_EDIT and self.edit_typhoon:
            self.edit_typhoon.rst()
        return True

    def _btn_prev(self) -> bool:
        if self.tys and self.md == self.MODE_NORMAL:
            self.cti = (self.cti - 1) % len(self.tys)
            self.current_typhoon().rst()
            self.pst = 0
            self.po = 0
        return True

    def _btn_next(self) -> bool:
        if self.tys and self.md == self.MODE_NORMAL:
            self.cti = (self.cti + 1) % len(self.tys)
            self.current_typhoon().rst()
            self.pst = 0
            self.po = 0
        return True

    def _btn_new_typhoon(self) -> bool:
        self.dialog_mgr.new_typhoon_dialog.activate()
        return True

    def _btn_point_list(self) -> bool:
        if self.edit_typhoon:
            self.dialog_mgr.point_list.activate(typhoon=self.edit_typhoon, readonly=False)
        return True

    def _btn_mode(self) -> bool:
        self.switch_mode()
        return True

    def _btn_ty_list(self) -> bool:
        self.dialog_mgr.tl.activate()
        return True

    def _btn_settings(self) -> bool:
        self.dialog_mgr.sd.activate()
        return True

    def _btn_time_jump(self) -> bool:
        if self.md == self.MODE_SEASON:
            self.dialog_mgr.tj.activate()
        return True

    def _btn_ace_chart(self) -> bool:
        if self.md == self.MODE_SEASON:
            self.dialog_mgr.ace_chart.activate()
        return True

    def _btn_track(self) -> bool:
        if self.md == self.MODE_SEASON:
            self.dialog_mgr.track_dialog.activate()
        return True

    def _btn_undo(self) -> bool:
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self._undo_edit()
        return True

    def _btn_redo(self) -> bool:
        if self.md == self.MODE_EDIT and self.edit_typhoon:
            self._redo_edit()
        return True

    def _btn_script(self) -> bool:
        self.script_dialog.activate()
        return True
