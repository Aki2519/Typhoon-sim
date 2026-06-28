# py/ty_sim_mixins/keyboard_mixin.py
"""键盘快捷键、模式切换、数据重载。"""
from __future__ import annotations
import os
import json
import logging
import pygame
from datetime import datetime

from ..constants import f_s, rt

logger = logging.getLogger(__name__)


class TySimKeyboardMixin:
    """Mixin: 键盘快捷键、模式切换、数据重载、截图。"""

    def reload_typhoons(self):
        saved_mlo, saved_Mlo = self.mlo, self.Mlo
        saved_mla, saved_Mla = self.mla, self.Mla

        self.repo.reload_typhoons()
        self.cti = self.repo.cti
        self.edit_typhoon = self.repo.edit_typhoon
        if self.md == self.MODE_EDIT and self.tys:
            self.edit_typhoon = self.tys[0]

        if hasattr(self, 'season_ctrl'):
            sc = self.season_ctrl
            sc.reset_to_first_year()
            self.sty = sc.sty
            self.edy = sc.edy
            self._sync_season_state()
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
            self._season_info_box_cache.pop(self.edit_typhoon, None)
            self._season_info_box_last_data.pop(self.edit_typhoon, None)
            if self.dialog_mgr.point_list.active:
                self.dialog_mgr.point_list._clear_row_cache()
                self.dialog_mgr.point_list._needs_save = True
            else:
                self.dialog_mgr.point_list.save_typhoon_to_file(self.edit_typhoon)

    def _redo_edit(self):
        if self.edit_typhoon and self.edit_typhoon.redo():
            self.edit_typhoon.update_screen_points(self.latlon_to_screen)
            self._refresh_ace_data()
            self._season_info_box_cache.pop(self.edit_typhoon, None)
            self._season_info_box_last_data.pop(self.edit_typhoon, None)
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
                logger.debug("restore_map_region_from_config failed", exc_info=True)

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
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti - 1) % len(self.tys)
                self.current_typhoon().rst()
            elif self.md == self.MODE_EDIT:
                idx = self.tys.index(self.edit_typhoon) if self.edit_typhoon in self.tys else 0
                idx = (idx - 1) % len(self.tys)
                self.edit_typhoon = self.tys[idx]
                self.edit_typhoon.rst()
            return True
        return False

    def _key_right_bracket(self) -> bool:
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti + 1) % len(self.tys)
                self.current_typhoon().rst()
            elif self.md == self.MODE_EDIT:
                idx = self.tys.index(self.edit_typhoon) if self.edit_typhoon in self.tys else 0
                idx = (idx + 1) % len(self.tys)
                self.edit_typhoon = self.tys[idx]
                self.edit_typhoon.rst()
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
        import os as _os
        out_dir = "./picture"
        _os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"screenshot_{ts}.png"
        fp = _os.path.join(out_dir, fn)
        try:
            top = self._get_top_dialog()
            if top:
                tmp = pygame.Surface((top.bg_rect.width, top.bg_rect.height))
                ox, oy = top.bg_rect.x, top.bg_rect.y
                top.bg_rect.x = 0
                top.bg_rect.y = 0
                if hasattr(top, '_layout_valid'):
                    top._layout_valid = False
                if hasattr(top, '_compute_layout'):
                    top._compute_layout()
                top.draw(tmp)
                top.bg_rect.x = ox
                top.bg_rect.y = oy
                if hasattr(top, '_layout_valid'):
                    top._layout_valid = False
                if hasattr(top, '_compute_layout'):
                    top._compute_layout()
            else:
                tmp = pygame.Surface((self.screen_width, self.screen_height))
                self.draw(tmp)
            pygame.image.save(tmp, fp)
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
                logger.debug("toggle_window_topmost failed", exc_info=True)
                return False
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

        if self.md == self.MODE_SEASON:
            if self._has_season_cache and self._cached_season_st is not None:
                self.st = self._cached_season_st
                self.ste = self._cached_season_ste
                self.sy = self._cached_season_sy
                self.csa = self._cached_season_csa
                self.current_ace_year = self.get_ace_year(
                    datetime(self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])))
            else:
                self.st = "010100"
                self.ste = 0
                self.csa = 0.0
                self.sy = self.sty
                current_dt = datetime(self.sy, 1, 1, 0)
                self.current_ace_year = self.get_ace_year(current_dt)
                self.csa = self.calc_accumulated_ace_up_to(
                    self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
            self._sync_to_season_ctrl()
        elif self.md == self.MODE_EDIT:
            if not self.edit_typhoon and self.tys:
                self.edit_typhoon = self.tys[0]
        else:
            self.edit_typhoon = None

        self._config_needs_save = True
        self.save_config()

    def _btn_play(self) -> bool:
        self.pl = not self.pl
        self.play_text = rt(f_s, "播放" if not self.pl else "暂停", (255, 255, 255))
        return True

    def _btn_reset(self) -> bool:
        if not self.tys:
            return True
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
                self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
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
