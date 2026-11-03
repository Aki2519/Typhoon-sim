# py/ty_sim_mixins/keyboard_mixin.py
"""键盘快捷键、模式切换、数据重载。"""
from __future__ import annotations
import os
import json
import logging
import pygame
from datetime import datetime

from ..constants import f_s, rt, HEMISPHERE_SOUTH, CONFIG_FILE
from .sim_mode_mixin import SIM_LAYER_IDS

logger = logging.getLogger(__name__)


class TySimKeyboardMixin:
    """Mixin: 键盘快捷键、模式切换、数据重载、截图。"""

    def reload_typhoons(self):
        # 中#6: 重载前先收掉拖拽。否则 `drag_typhoon` 仍指向**即将被替换**的旧
        # 对象: 重载后继续 MOUSEMOTION 会改旧对象的 pts, 松手时
        # `save_typhoon_to_file(旧对象)` 按旧 filepath 落盘, 直接覆盖刚重载的数据。
        self._cancel_drag()
        self._drag_needs_save = False

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
        # 重载后 edit_typhoon 是新对象(点数/顺序可能变化, 甚至换成 tys[0] 另一个台风),
        # 旧选中索引会指向错误报点或越界(Delete/Enter 会操作未选中的报点):
        # 与 switch_mode/_switch_edit_typhoon 一致, 按新点数夹紧/复位
        if self.md == self.MODE_EDIT:
            _n = len(self.edit_typhoon.pts) if self.edit_typhoon else 0
            _sel = getattr(self, '_edit_selected_point', None)
            if _sel is None or not (0 <= _sel < _n):
                _sel = 0 if _n else None
            self._edit_selected_point = _sel
            self._last_edited_point = _sel

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

        # 中#5: 镜头跟踪相关的**强引用**也必须一起清(与 `delete_typhoon`
        # `utils_mixin._tracking_seen.discard` / `_tracked_ty=None` 同款)。
        # 不清的后果: ① 旧 Typhoon 对象被集合/字段钉住, 台风列表重建后不 GC;
        # ② 下一轮收尾 `_track_finale_span(list(self._tracking_seen))` 会把
        #    **已被替换掉的旧对象**的报点算进总览 bbox(区域离谱/路径变小白点);
        # ③ 正在跑的收尾相位持有旧对象, 重载后继续对旧点推镜/高亮。
        seen = getattr(self, '_tracking_seen', None)
        if seen is not None:
            seen.clear()
        self._tracked_ty = None
        self.tracking_typhoon = None
        self._track_finale_tys = []
        self._track_finale_md = None
        self._track_hl_tys = []
        self._track_hl_ty = None
        self._track_hl_from = 0
        self._track_fade_from = 0
        self._track_phase = 'idle'
        self._track_phase_t0 = 0
        self._track_anim = None
        self._track_anim_dur = 0
        self._track_tgt_prev = None
        self._track_glide = False
        self._track_scale_changed = False

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
        # 与 _key_left/_key_right/_key_plus/_key_minus 一致: 钳制到配置速度区间,
        # 否则用户在设置里把 mis 调到 >1.0 后按 X 会把 sp 写成低于下限的非法值
        self.sp = min(self.mas, max(self.mis, 1.0))
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

    def _key_f11(self) -> bool:
        self.toggle_fullscreen()
        return True

    def toggle_fullscreen(self) -> bool:
        try:
            if not self.fullscreen:
                # 记录当前尺寸, 切到全屏
                self._pre_fs_size = (self.screen_width, self.screen_height)
                pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.RESIZABLE, vsync=0)
                w = pygame.display.get_surface().get_width()
                h = pygame.display.get_surface().get_height()
            else:
                w, h = self._pre_fs_size if hasattr(self, '_pre_fs_size') else (
                    self.cfg.screen_width, self.cfg.screen_height)
                pygame.display.set_mode((w, h), pygame.RESIZABLE, vsync=0)
            self.fullscreen = not self.fullscreen
            self.handle_resize(w, h)
            self.save_config()
            return True
        except Exception as e:
            logger.debug("toggle_fullscreen failed", exc_info=True)
            return False

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

    def _switch_edit_typhoon(self, delta: int) -> None:
        """编辑模式切换编辑台风([ / ])。"""
        idx = self._edit_ty_idx()
        idx = (idx + delta) % len(self.tys)
        self._edit_idx = idx
        self.edit_typhoon = self.tys[idx]
        self.edit_typhoon.rst()
        # 切换编辑台风后复位选中点,避免旧索引越界;
        # _last_edited_point 是全局的,也一并清掉,防跨台风残留 idx
        self._edit_selected_point = None
        self._last_edited_point = None

    def _key_left_bracket(self) -> bool:
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti - 1) % len(self.tys)
                self.current_typhoon().rst()
            else:
                # 编辑模式: [ 切换上一个编辑台风
                self._switch_edit_typhoon(-1)
            return True
        return False

    def _key_right_bracket(self) -> bool:
        if self.md in (self.MODE_NORMAL, self.MODE_EDIT) and self.tys:
            if self.md == self.MODE_NORMAL:
                self.cti = (self.cti + 1) % len(self.tys)
                self.current_typhoon().rst()
            else:
                # 编辑模式: ] 切换下一个编辑台风
                self._switch_edit_typhoon(1)
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
            try:
                if top:
                    # 重排布局+绘制+保存都在受保护块内:
                    # 任一步抛异常,finally 都复位 bg_rect 并恢复 _layout_valid,
                    # 避免把对话框永久移位到 (0,0) 且布局卡在失效态
                    if hasattr(top, '_compute_layout'):
                        top._compute_layout()
                    top.draw(tmp)
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
                    # K31: 无论绘制/保存是否抛异常都恢复对话框位置
                    top.bg_rect.x = ox
                    top.bg_rect.y = oy
                    if hasattr(top, '_layout_valid'):
                        top._layout_valid = False
                    if hasattr(top, '_compute_layout'):
                        top._compute_layout()
            self.show_toast(f"已保存到./picture/{fn}", 'success')
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
        """J: 打开镜头跟踪(风季/正常模式)。

        轮次 160: 用户要求「**打开镜头跟踪的快捷键改为 j**」。这里直接复用
        `_btn_track()` 那条既有路径(模式判断在它里面), **不另写一套激活逻辑**。

        ⚠ 这个键**原本已被占用**(`input_ctrl.py:42 pygame.K_j → _key_j` →
        `script_dialog.activate()`, 剧本对话框, 且未列在快捷键说明表里)。
        按用户明确要求让给镜头跟踪; 剧本对话框**功能不丢** —— 它仍有按钮入口
        `_btn_script()`(本文件下方)。若日后要把剧本对话框绑到别的键, 只需在
        `input_ctrl.py` 的命令表里加一行。
        """
        return self._btn_track()

    def _key_d(self) -> bool:
        """D: 打开绘画面板(正常/风季/编辑模式)。"""
        if self.md in (self.MODE_NORMAL, self.MODE_SEASON, self.MODE_EDIT):
            self.dialog_mgr.paint_dialog.activate()
            return True
        return False

    def set_window_topmost(self, state: bool) -> bool:
        if os.name == 'nt':
            try:
                import win32gui, win32con
                # ⚠⚠ 审查抓到: 这里原来**硬编码**标题 "台风路径模拟系统", 而
                # (ty_sim.py:679-698)在主循环里**每帧**把
                # 标题改成 "正常模式 / 风季 · … / 模拟 · 台风数值模拟"。
                # ⇒ 启动后 FindWindow 永远找不到窗口 ⇒ 返回 False ⇒
                #   (F12) 不改 cfg、也不提示 ——
                #   **F12 实际上一直是空按的**; 只有启动那一刻(标题还没被改)
                #   由  施加才生效, 这正是"置顶看起来只在重启后有效"的原因。
                # 用**当前真实标题**(与 settings.py 里 actual_client_size/
                # keep_window_on_screen 的取法一致)。
                try:
                    import pygame as _pg
                    _title = _pg.display.get_caption()[0]
                except Exception:
                    _title = "台风路径模拟系统"
                hwnd = win32gui.FindWindow(None, _title)
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

    def _apply_panel_height_for_mode(self) -> None:
        """按模式设定控制面板高度(并把 map_height 同步跟着移动)。

        用户 2026-09-19: "上方的图层显示放到下方功能栏" —— 模拟模式的图层开关
        当时搬进了功能栏的**第三行**, 而面板只有 100px(两行), 所以模拟模式加高 34px。

        ⚠⚠ 2026-10-01 用户要求改掉: **"功能栏不能抬高高度, 你需要找别的方法放下那些按钮"**
        ⇒ 图层那一组改成**浮在地图区域内**(见 `ControlPanel._layer_row_y`),
          功能栏**不再加高**, 所有模式统一 100px。
          原来这里的 `want = CPH + (34 if sim else 0)` 正是用户说的
          "模拟模式的各种叠加层会自动拉高功能栏"。
        """
        from ..constants import CPH
        want = CPH
        # ⚠ 审查抓到(一般): 图层弹层只在 sim 模式的 build() 里重建, 切到
        # NORMAL/EDIT 后  仍为 True ⇒ draw() 照画、hit_test
        # 仍返回 layer_*, 会抢走该条带内的地图点击。这里在**切模式时统一收起**。
        # (弹层本不该跨模式存活, 它是模拟模式专用的。)
        try:
            cp = getattr(self, 'control_panel', None)
            if cp is not None:
                cp.close_layer_popup()
        except Exception:
            pass
        if getattr(self, 'control_panel_height', None) == want:
            return
        self.control_panel_height = want
        self.map_height = max(120, self.screen_height - want)
        try:
            self.view.map_height = self.map_height
        except Exception:
            pass
        mv = getattr(getattr(self, 'map_mgr', None), 'map_view', None)
        if mv is not None:
            # 只让"可视高度"生效; **绝对不要**调 update_view() —— 它内部会
            # _fit_view() 把镜头重新对准配置里的洋区框(mlo/Mlo/mla/Mla), 于是
            # **每次切换模式视角都被重置**(用户 2026-09-19: "切换到模拟不要自作
            # 主张地改变其它模式的视角, 继承视角")。MapView.resize 现在会保持
            # 视野中心, 这里只需把缓存作废让新尺寸重绘。
            mv.resize(self.screen_width, self.map_height)
            try:
                self.map_mgr._cached_map_render = None
            except Exception:
                pass
            self.map_draw_rect = self.map_mgr.get_draw_rect()
        self._panel = None
        try:
            self.update_all_screen_points()
        except Exception:
            pass

    def switch_mode(self) -> None:
        if hasattr(self, '_panel'):
            self._panel = None

        # 洋区编辑模式: H/模式按钮 = 退出并还原进入时的模式(不进循环)
        oe = getattr(self, 'ocean_edit', None)
        if oe is not None and oe.active:
            oe.exit()
            return

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
        elif self.md == self.MODE_EDIT:
            self.md = self.MODE_SIM
            # 进入模拟模式: 初始化 SimCore 模拟会话
            self.sim_playing = False
            self._sim_sync_play_text()
            self._sim_reset(msg=False)
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
            # CRO5: _last_edited_point 是全局的,可能残留别的台风 idx;
            # 恢复选中索引时必须按当前 edit_typhoon 的点数夹紧,避免越界选中
            sel = self._last_edited_point
            n = len(self.edit_typhoon.pts) if self.edit_typhoon else 0
            if sel is None or not (0 <= sel < n):
                sel = 0 if n else None
            self._edit_selected_point = sel
        else:
            pass

        self._apply_panel_height_for_mode()

        self._config_needs_save = True
        self.save_config()

    def _handle_click(self, pos):
        cp = self.control_panel
        speed_ratio = cp.hit_test_speed_bar(pos)
        if speed_ratio is not None:
            self.sp = round(self.mis + speed_ratio * (self.mas - self.mis), 1)
            if getattr(self, 'md', None) == self.MODE_SIM:
                self._sim_notice(f"速度: {self.sp:g}×")
            return True
        btn_key = cp.hit_test(pos)
        if btn_key is None:
            return False
        # 功能栏第三行(仅模拟模式): 图层开关 / 环境层三选一 / 样式循环
        if btn_key.startswith('layer_'):
            self._sim_toggle_layer(btn_key[len('layer_'):])
            return True
        if btn_key.startswith('envlay_'):
            self._sim_set_env_layer(btn_key[len('envlay_'):])
            return True
        if btn_key == 'wind_mode':
            self._sim_cycle_wind_mode()
            return True
        if btn_key == 'shear_style':
            self._sim_cycle_shear_style()
            return True
        handler = getattr(self, f"_btn_{btn_key}", None)
        if handler:
            return handler()
        return False

    def _btn_play(self) -> bool:
        if getattr(self, 'md', None) == self.MODE_SIM:
            self.sim_playing = not self.sim_playing
            self._sim_sync_play_text()
            return True
        # R4-3: 统一走 _set_playing(清理单点完成标志/记录对话框期间手动切换)
        self._set_playing(not self.pl)
        return True

    def _btn_reset(self) -> bool:
        if getattr(self, 'md', None) == self.MODE_SIM:
            # 重置: 停止播放并新建 SimCore 模拟会话
            self.sim_playing = False
            self._sim_reset()
            self._sim_sync_play_text()
            return True
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

    # ── 模拟模式按钮 ──
    def _btn_sim_layer(self) -> bool:
        """「图层:<简称>」按钮: **循环切换独占环境层**(契约不变)。

        ⚠ 2026-10-01 我一度把它改成"开关弹层", 打断了
        `tests/test_sim_mode.py::test_layer_cycle` 钉住的行为(全量回归抓到) ——
        这个方法是**被直接调用的语义 API**, 不是纯 UI 钩子, 不能改含义。
        图层弹层改由**另一个按钮** `_btn_sim_layer_menu` 打开。
        """
        idx = SIM_LAYER_IDS.index(self.sim_layer)
        self._sim_set_layer(SIM_LAYER_IDS[(idx + 1) % len(SIM_LAYER_IDS)])
        return True

    def _btn_sim_layer_menu(self) -> bool:
        """打开/收起**图层弹层**(图层开关 + 环境层 + 样式)。

        用户 2026-10-01: "功能栏不能抬高高度" + "我要录视频! 不要把按钮放在界面里!"
        ⇒ 这些按钮收进**默认不可见**的弹层, 由这个按钮开关。
        """
        cp = getattr(self, 'control_panel', None)
        if cp is None:
            return False
        cp.toggle_layer_popup()
        return True

    def _btn_sim_spd(self) -> bool:
        # 速度档位循环: 走 SimModeMixin 的唯一实现(SIM_SPEEDS 表 + cfg 记忆),
        # 免得这里和面板"模拟速度"行各存一份档位表, 早晚漂开
        self._sim_cycle_speed()
        return True

    def _btn_sim_gen(self) -> bool:
        self._sim_spawn(17.0, 131.5)
        return True

    def _btn_sim_nat(self) -> bool:
        # 走 mixin 的唯一实现(面板与按键共用, 且会镜像到 cfg 记住)
        return self._sim_toggle_natural_gen()

    def _btn_sim_sett(self) -> bool:
        self.dialog_mgr.sim_settings.activate()
        return True

    def _btn_sim_export(self) -> bool:
        try:
            fn = self._sim_export(6)
            self._sim_notice(f"已导出: {os.path.basename(fn)}")
        except Exception as exc:
            self._sim_notice(f"导出失败: {exc}")
        return True

    def _btn_sim_move_bdeck(self) -> bool:
        """把 ./sim_typhoon 里的实时记录**移动到** typhoon/sim/ (用户手动触发)。

        设计(用户 2026-09-20):
          * 模拟期间的 b-deck **实时记录到 ./sim_typhoon**, **不写 typhoon/**;
          * 目录**分年份、分洋区**(与 G:\\气象\\数据\\自评 一样):
            `sim_typhoon/<年>/<年><洋区>/<洋区><NN><年>.dat`;
          * 看完觉得可以了, 再点这个按钮把整棵树搬进 `typhoon/sim/<年>/<年><洋区>/`,
            搬完清空源目录(真正的"移动")。
        """
        import re as _re
        import shutil as _sh
        # 本文件是 app/ty_sim_mixins/keyboard_mixin.py → 上三级才是**仓库根**
        # (原来少了一级, 结果把文件搬进了 app/typhoon/ ✗ 实测踩过)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        src = os.path.join(root, 'sim_typhoon')
        if not os.path.isdir(src):
            self._sim_notice('没有 sim_typhoon 目录(还没跑过实时记录)')
            return True
        n, years, basins = 0, set(), set()
        for yr in sorted(os.listdir(src)):
            ydir = os.path.join(src, yr)
            if not (os.path.isdir(ydir) and _re.fullmatch(r'20\d\d', yr)):
                continue
            for sub in sorted(os.listdir(ydir)):
                sdir = os.path.join(ydir, sub)
                if not os.path.isdir(sdir):
                    continue
                dst_dir = os.path.join(root, 'typhoon', 'sim', yr, sub)
                os.makedirs(dst_dir, exist_ok=True)
                for f in sorted(os.listdir(sdir)):
                    if not f.endswith('.dat'):
                        continue
                    _sh.move(os.path.join(sdir, f), os.path.join(dst_dir, f))
                    n += 1
                    years.add(yr)
                    basins.add(sub)
        if not n:
            self._sim_notice('sim_typhoon 里没有 b-deck 文件')
            return True
        self._sim_notice('已移入 typhoon/sim/%s: %d 个 (%s)'
                         % ('+'.join(sorted(years)), n, '+'.join(sorted(basins))))
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

    def _btn_export(self):
        """功能栏「导出」：画面/视频导出（用户 2026-10-19）。"""
        dlg = getattr(self.dialog_mgr, 'export_dlg', None)
        if dlg is None:
            return
        dlg.activate()

    def _btn_modules(self):
        """功能栏「模块」（用户 2026-10-14）：打开模块总表。"""
        dlg = getattr(self.dialog_mgr, 'modules', None)
        if dlg is None:
            return
        dlg.refresh()
        dlg.activate()

    def _btn_offscreen(self) -> bool:
        """域外风暴小窗的选择对话框（用户 2026-10-12 需求 1.5）。"""
        dlg = getattr(self.dialog_mgr, 'offscreen', None)
        if dlg is None:
            return False
        dlg.refresh()
        dlg.activate()
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
        # 风季/正常模式提供镜头跟踪(仅按钮入口, 无键盘快捷键)
        if self.md in (self.MODE_SEASON, self.MODE_NORMAL):
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

    def _key_u(self) -> bool:
        """U: 打开剧本对话框。

        轮次 160(用户裁决): 它原来是 `J` 的隐藏快捷键, 而 `J` 让给了"镜头跟踪"
        (`_key_j`), 剧本对话框改绑到空闲的 `U`。直接复用既有入口 `_btn_script()`,
        不另写激活逻辑; **模式限制跟着它** —— `_btn_script` 没有任何模式判断,
        所以 U 在**所有模式**下都能打开剧本对话框(与 J 只在风季/正常不同)。
        """
        return self._btn_script()

    def _btn_paint(self) -> bool:
        self.dialog_mgr.paint_dialog.activate()
        return True
