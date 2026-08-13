# py/ty_sim.py
"""台风路径模拟系统主控制类。"""
from __future__ import annotations

import pygame
import logging
from typing import List, Optional, Dict, Tuple
from datetime import datetime

from .constants import (f_s, rt, TXT, CPH, CONFIG_FILE,
                         SEASON_SPEED_DEFAULT, MAX_INFO_BOX_SLOTS,
                         HEMISPHERE_SOUTH, MODE_NORMAL, MODE_SEASON, MODE_EDIT)
from .config import AppConfig
from .typhoon import Typhoon
from .landfall_effect import LandfallEffect
from .resource_manager import ResourceManager, MapManager
from .ace_engine import ACEEngine
from .view_state import ViewState
from .data_repo import DataRepository
from .playback_ctrl import PlaybackController
from .season_ctrl import SeasonController
from .input_ctrl import InputController
from .renderer import Renderer
from .monthly_summary import MonthlySummary

from .ty_sim_mixins import (
    TySimUtilsMixin,
    TySimDrawMixin, TySimEventMixin,
)
from .ty_sim_mixins.keyboard_mixin import TySimKeyboardMixin
from .script_engine import ScriptEngine
from .script_dialog import ScriptDialog
from .particle_effect import preload_particles
from .input_handler import InputHandler
from .dialog_manager import DialogManager
from .ty_sim_mixins.track_mixin import TySimTrackMixin

logger = logging.getLogger(__name__)




class _ConfigProperty:
    """Explicit descriptor: proxies attribute read/write to self.cfg (AppConfig)."""
    __slots__ = ('_name',)

    def __init__(self, name: str) -> None:
        self._name = name

    def __get__(self, obj: TySim | None, owner=None):
        if obj is None:
            return self
        return getattr(obj.cfg, self._name)

    def __set__(self, obj: TySim, value) -> None:
        setattr(obj.cfg, self._name, value)
        obj._config_needs_save = True
        if self._name == 'icon_set':
            obj.res_mgr.icon_set = value
            obj._clear_icon_scale_caches()


class _RepoProperty:
    """Explicit descriptor: proxies attribute read/write to self.repo (DataRepository)."""
    __slots__ = ('_name',)

    def __init__(self, name: str) -> None:
        self._name = name

    def __get__(self, obj: TySim | None, owner=None):
        if obj is None:
            return self
        return getattr(obj.repo, self._name)

    def __set__(self, obj: TySim, value) -> None:
        setattr(obj.repo, self._name, value)


class TySim(TySimUtilsMixin,
            TySimDrawMixin, TySimEventMixin, TySimKeyboardMixin,
            TySimTrackMixin):

    MODE_NORMAL = MODE_NORMAL
    MODE_SEASON = MODE_SEASON
    MODE_EDIT = MODE_EDIT

    _REPO_FIELDS = frozenset({'tys', 'cti', 'edit_typhoon', '_all_tys_backup'})

    # ── 可读属性（只读别名，指向缩写规范属性） ──

    @property
    def is_playing(self) -> bool:
        return self.pl

    @is_playing.setter
    def is_playing(self, v: bool) -> None:
        self.pl = v

    @property
    def season_time(self) -> str:
        return self.st

    @property
    def season_time_elapsed(self) -> float:
        return self.ste

    @property
    def season_speed_factor(self) -> float:
        return self.ssf

    @property
    def total_season_ace(self) -> float:
        return self.tsa

    @property
    def cumulative_season_ace(self) -> float:
        return self.csa

    @property
    def season_year(self) -> int:
        return self.sy

    @property
    def season_start_year(self) -> int:
        return self.sty

    @property
    def season_end_year(self) -> int:
        return self.edy

    @property
    def year_finished(self) -> bool:
        return self.yf

    @property
    def yearly_ace_data(self) -> Dict[int, float]:
        return self.yad

    def __init__(self, screen: pygame.Surface, cfg: Optional[AppConfig] = None) -> None:
        self.screen = screen
        self.control_panel_height = CPH

        self.cfg = cfg if cfg is not None else AppConfig.load(CONFIG_FILE)

        self._init_attributes()
        self._init_resource_managers()

        self._install_descriptors()

        self.screen_width, self.screen_height = screen.get_size()
        self.map_height = self.screen_height - CPH
        self.view = ViewState(self.screen_width, self.screen_height, self.map_height,
                              self.map_mgr, self.cfg)
        self.ace_engine = ACEEngine(self)
        self.repo = DataRepository(self.cfg, self.res_mgr)
        self.repo.bind(self)

        self.season_ctrl = SeasonController(self.cfg, self.repo, self.ace_engine)
        self.playback_ctrl = PlaybackController(self.cfg, self.repo, self.view,
                                                 self.ace_engine, self.res_mgr, self.map_mgr)
        self._pre_render_texts()

        self.repo.load_typhoon_files()
        self.map_mgr.update_map_image()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.dialog_mgr = DialogManager(self)
        self.season_ctrl.bind(self.dialog_mgr)
        self.script_engine = ScriptEngine(self)
        self.script_dialog = ScriptDialog(self)
        self._ms = MonthlySummary(self)
        self.input_handler = InputHandler(self)
        self.input_ctrl = InputController(self)
        self.renderer = Renderer(self)
        self.update_all_screen_points()
        self.map_mgr.update_land_mask()

        preload_particles()
        # 预加载登陆特效帧 / Landed 流 / SMCY 主图标视频（按当前图标大小）
        try:
            from .smcy_icon import preload_landfall_effects, preload_icon_streams
            icon_factor = self._size_factors()[1]
            lf_size = max(20, 4 * round(70 * icon_factor * 1.5 / 4))
            marker = max(6, int(13 * self.cfg.point_size / 100.0))
            preload_landfall_effects(lf_size, int(marker * 96 / 40))
            if getattr(self.cfg, 'icon_set', '') == 'smcy':
                from .smcy_icon import preload_icon_streams
                n = preload_icon_streams(self.tys, icon_factor)
                logger.debug(f"SMCY 图标流预加载: {n} 个")
            # 登陆点标记 png 预热
            from .ty_sim_mixins._draw_path_mixin import preload_landfall_markers
            from .landfall_effect import landfall_marker_name
            names = set()
            for w, cat in ((40, 'TS'), (60, 'C1'), (80, 'C2'), (100, 'C3'), (120, 'C4'), (155, 'C5'), (170, 'C5')):
                n = landfall_marker_name(w, cat)
                if n:
                    names.add(n)
            preload_landfall_markers(list(names), marker)
            # 摘要视频流预热
            from .smcy_icon import preload_summary_streams
            bar_h = 64
            n2 = preload_summary_streams(self.tys, bar_h, self.screen_width - 520 - 20)
            if n2:
                logger.debug(f"Summary 流预加载: {n2} 个")
        except Exception:
            logger.debug("特效/图标预加载失败", exc_info=True)

        if self.window_topmost:
            self.set_window_topmost(self.window_topmost)

        if self.md == MODE_SEASON:
            self._init_season_ace()
            self._sync_to_season_ctrl()
            if self.hemisphere == HEMISPHERE_SOUTH:
                self.season_ctrl.jump_to(datetime(self.sty, 7, 1, 0))
                self._sync_season_state()

        self._dialog_stack: list = []
        self.landfall_records: list = []

    @classmethod
    def _install_descriptors(cls) -> None:
        if getattr(cls, '_descriptors_installed', False):
            return
        cls._descriptors_installed = True
        for name in AppConfig._serialize_fields():
            if not hasattr(cls, name) or isinstance(getattr(cls, name, None), _ConfigProperty):
                setattr(cls, name, _ConfigProperty(name))
        for name in cls._REPO_FIELDS:
            setattr(cls, name, _RepoProperty(name))

    def _init_attributes(self) -> None:
        self.tys: List[Typhoon] = []
        self.cti = 0
        self.edit_typhoon: Optional[Typhoon] = None

        self.pl = False
        self.lst = pygame.time.get_ticks()
        self._fps = 60.0
        self._ui_hidden = False
        self._dialog_pause_active = False
        self._pl_before_dialog = False

        self.st = "010100"
        self.ste = 0.0
        self.ssf = SEASON_SPEED_DEFAULT
        self.tsa = self.csa = 0.0
        self.sy = self.sty = self.edy = 2000
        self.yf = False
        self.yad: Dict[int, float] = {}
        self.current_ace_year = 2000

        self.effects: List[LandfallEffect] = []

        self.info_box_slots: Dict[Typhoon, int] = {}
        self.info_box_free_slots = list(range(MAX_INFO_BOX_SLOTS))

        self.pst = 0
        self.po = 0

        self.error_message = ""
        self.error_time = 0
        self.dialog_page_cache = {}
        self._config_needs_save = False
        self._cached_season_st: Optional[str] = None
        self._cached_season_ste: float = 0.0
        self._cached_season_sy: int = 0
        self._cached_season_csa: float = 0.0
        self._has_season_cache: bool = False

        self.dragging_point = False
        self.drag_typhoon: Optional[Typhoon] = None
        self.drag_point_index = -1
        self.drag_start_pos = (0, 0)
        self.right_button_dragging = False
        self.right_drag_start_pos = (0, 0)

        # 屏幕坐标惰性刷新（缩放优化）
        self._sp_version = 0
        self._zoom_burst_until = 0
        self._smooth_restore_due: Optional[int] = None
        self._path_invalidate_due: Optional[int] = None
        self._pending_wheel = 0
        self._last_interact = 0
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._view_dirty = False
        self._game_ct: int = 0

        self._ace_timeline_cache: Dict[int, List[Tuple[datetime, float]]] = {}
        self._ace_typhoon_cache: Dict[int, List[Tuple[str, float]]] = {}

        self._edit_selected_point: Optional[int] = None
        self._init_tracking()
        self._last_edited_point: Optional[int] = None

    def _init_resource_managers(self) -> None:
        self.res_mgr = ResourceManager()
        self.res_mgr.icon_set = self.cfg.icon_set
        self.map_mgr = MapManager(self)
        self.map_mgr.cmp = self.cfg.cmp

    def _season_dt(self) -> datetime:
        return datetime(self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))

    def _init_season_ace(self) -> None:
        try:
            dt = self._season_dt()
            self.current_ace_year = self.ace_engine.ace_year(dt)
            self.csa = self.ace_engine.cumulative_ace_up_to(dt)
        except Exception:
            logger.debug("_init_season_ace failed", exc_info=True)

    def _pre_render_texts(self) -> None:
        W = (255, 255, 255)
        self.play_text = rt(f_s, "播放", W)
        self.pause_text = rt(f_s, "暂停", W)
        self.reset_text = rt(f_s, "重置", W)
        self.prev_text = rt(f_s, "上一个", W)
        self.next_text = rt(f_s, "下一个", W)
        self.new_text = rt(f_s, "新建台风", W)
        self.point_list_text = rt(f_s, "报点列表", W)
        self.normal_mode_text = rt(f_s, "正常", W)
        self.season_mode_text = rt(f_s, "台风季", W)
        self.edit_mode_text = rt(f_s, "编辑", W)
        self.ty_list_text = rt(f_s, "台风列表", W)
        self.settings_text = rt(f_s, "设置", W)
        self.time_jump_text = rt(f_s, "时间跳跃", W)
        self.ace_chart_text = rt(f_s, "ACE图表", W)
        self.track_text = rt(f_s, "镜头跟踪", W)
        self.undo_text = rt(f_s, "撤销", W)
        self.redo_text = rt(f_s, "重做", W)
        self.script_text = rt(f_s, "脚本", W)
        self.mode_desc_normal = rt(f_s, "模式: 正常", TXT)
        self.mode_desc_season = rt(f_s, "模式: 台风季", TXT)
        self.mode_desc_edit = rt(f_s, "模式: 编辑", TXT)

    def save_config(self, force: bool = False) -> None:
        if not force and not self._config_needs_save:
            return
        self.cfg.save(CONFIG_FILE)
        self._config_needs_save = False

    def _refresh_ace_data(self, ty=None) -> None:
        # 法4: 点编辑传受影响台风,只重算该台风涉及的年份
        self.ace_engine.refresh_all([ty] if ty is not None else None)

    def get_ace_year(self, dt: datetime) -> int:
        return self.ace_engine.ace_year(dt)

    def calc_accumulated_ace_up_to(self, y: int, m: int, d: int, h: int) -> float:
        return self.ace_engine.cumulative_ace_up_to(datetime(y, m, d, h))

    def _point_in_ace_limit(self, la: float, lo: float) -> bool:
        return self.ace_engine.point_in_limit(la, lo)

    def _apply_basin_filter(self) -> None:
        self.repo.apply_basin_filter()
        self.tys = self.repo.tys
        if hasattr(self, 'season_ctrl'):
            self._sync_to_season_ctrl()

    def recalc_all_ace(self) -> None:
        self.repo.recalc_all_ace()
        if hasattr(self, 'md') and self.md == self.MODE_SEASON:
            try:
                current_dt = self._season_dt()
                self.current_ace_year = self.ace_engine.ace_year(current_dt)
                self.csa = self.ace_engine.cumulative_ace_up_to(current_dt)
            except Exception:
                logger.debug("recalc_all_ace season reset failed", exc_info=True)
        if hasattr(self, 'season_ctrl'):
            self._sync_to_season_ctrl()

    def get_display_name(self, ty: Typhoon) -> str:
        if ty.cust:
            return ty.cust
        if ty.sname:
            return ty.sname
        year = ty.start_time[:4] if ty.start_time and len(ty.start_time) >= 4 else ""
        base = f"{ty.basin}{ty.n}" if ty.basin else ty.n
        return f"{base}{year}" if year else base

    def current_typhoon(self) -> Optional[Typhoon]:
        return self.tys[self.cti] if self.tys and 0 <= self.cti < len(self.tys) else None

    def reset_map(self) -> None:
        self.map_mgr.reset_map()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.update_all_screen_points()

    def handle_resize(self, width: int, height: int) -> None:
        self.screen_width = width
        self.screen_height = height
        self.map_height = height - CPH
        self.view.screen_width = width
        self.view.screen_height = height
        self.view.map_height = height - CPH
        self.control_panel_height = CPH
        mv = self.map_mgr.map_view
        if mv is not None:
            # 同步 MapView 尺寸/缩放极限并复位渲染缓存（B4）
            mv.screen_width = width
            mv.screen_height = self.map_height
            mv.min_scale = max(width / mv.img_w, self.map_height / mv.img_h)
            mv._cached_scale = -1.0
            mv._cached_sw = -1
            mv._pan_cache_key = None
        self.map_mgr.update_view()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.update_all_screen_points()
        if self.map_mgr.land_img is not None:
            self.map_mgr.land_img = None
        self._view_dirty = True

    def update_all_screen_points(self) -> None:
        self.view.update_screen_points(self.tys, self.edit_typhoon)
        ver = self._sp_version
        for ty in self.tys:
            ty.v._sp_ver = ver
        if self.edit_typhoon:
            self.edit_typhoon.v._sp_ver = ver
        self._invalidate_all_path_caches()

    _ZOOM_BURST_MS = 500

    def invalidate_screen_points_lazy(self) -> None:
        """标记全部屏幕坐标过期：由 draw_typhoon 按需（仅可见台风）重算。
        缩放时避免对所有台风做全量坐标+样条重建。
        路径缓存失效推迟到 burst 结束（拖12/拖18）；burst 内重复事件跳过清空（拖13）。
        平滑样条与屏幕点同帧重建（draw_typhoon），缩放期间始终保持平滑。"""
        self._sp_version += 1
        now = pygame.time.get_ticks()
        if now >= getattr(self, '_zoom_burst_until', 0):
            self._path_invalidate_due = now + self._ZOOM_BURST_MS
        else:
            self._path_invalidate_due = max(self._path_invalidate_due or 0,
                                            now + self._ZOOM_BURST_MS)

    def _apply_pending_wheel(self) -> None:
        """帧末合并滚轮事件：一次 zoom_at + 一次惰性失效（拖16）。"""
        if not self._pending_wheel:
            return
        n = self._pending_wheel
        self._pending_wheel = 0
        if not self.map_mgr.map_view:
            return
        mx, my = pygame.mouse.get_pos()
        factor = 1.1 ** n if n > 0 else 0.9 ** -n
        self.map_mgr.map_view.zoom_at(factor, mx, my)
        if self.right_button_dragging:
            # 拖拽中缩放：坐标系冲突 → 惰性重投影 + 清拖拽面，
            # 各可见台风在绘制时按新视图重算（draw_typhoon 拖拽分支）
            ct = pygame.time.get_ticks()
            self.invalidate_screen_points_lazy()
            self._zoom_burst_until = ct + self._ZOOM_BURST_MS
            if self.smooth_path:
                self._smooth_restore_due = ct + self._ZOOM_BURST_MS + 20
            self._drag_offset_x = 0
            self._drag_offset_y = 0
            for ty in self.tys:
                self._clear_drag_cache(ty)
        else:
            # 惰性刷新：仅可见台风在绘制时重算；突发期内跳过平滑样条
            ct = pygame.time.get_ticks()
            self.invalidate_screen_points_lazy()
            self._zoom_burst_until = ct + self._ZOOM_BURST_MS
            if self.smooth_path:
                self._smooth_restore_due = ct + self._ZOOM_BURST_MS + 20
            if self.md == self.MODE_EDIT and self.edit_typhoon:
                self.edit_typhoon.update_screen_points(self.latlon_to_screen)
                self.edit_typhoon.v._sp_ver = self._sp_version
        self._view_dirty = True

    def update(self, dt: float) -> None:
        ct = pygame.time.get_ticks()
        self.lst = ct
        self._fps = 1.0 / dt if dt > 0 else 60.0
        self.input_handler.update(ct)

        n = self._pending_wheel
        self._apply_pending_wheel()
        se = getattr(self, 'script_engine', None)
        if n or (self.right_button_dragging and not (se and se.running)):
            self._last_interact = ct

        if self._view_dirty:
            self._view_dirty = False
            self.map_mgr.update_land_mask()
            if not self.right_button_dragging:
                self._sync_land_state()

        # 缩放突发结束后：一次合并路径缓存失效 + 惰性恢复平滑样条（拖18/拖12）
        if self._smooth_restore_due is not None and ct >= self._smooth_restore_due:
            self._smooth_restore_due = None
            self._path_invalidate_due = None
            self._sp_version += 1
            self._invalidate_all_path_caches()
        elif self._path_invalidate_due is not None and ct >= self._path_invalidate_due:
            self._path_invalidate_due = None
            self._invalidate_all_path_caches()

        dialog_open = self.dialog_mgr.any_active()
        self._update_dialog_pause(dialog_open)

        if self.md == MODE_SEASON:
            # 任意界面打开时季节时钟一律冻结，避免时钟与台风运动脱节
            self.season_ctrl._pl = self.pl and not dialog_open
            self.season_ctrl.update(dt)

        self.script_engine.update(dt)

        self.playback_ctrl._pl = self.pl
        self.playback_ctrl.update_all(ct, dt, dialog_open, self.season_ctrl)
        self.effects = self.playback_ctrl.effects
        self.landfall_records = self.playback_ctrl.landfall_records
        if self.pl != self.playback_ctrl._pl:
            self.pl = self.playback_ctrl._pl
            self.season_ctrl._pl = self.pl
        self._sync_season_state()
        if self.md == MODE_SEASON and self.pl:
            self._check_monthly_summary()
        self._update_tracking(ct)
        self._ms.update(dt)

    def _set_playing(self, playing: bool, dialog_auto: bool = False) -> None:
        """设置播放状态并同步播放按钮文字。"""
        self.pl = playing
        if playing:
            # R3-1: 重新播放时清单点台风完成标志,允许再次淡出流程
            for ty in self.tys:
                ty._single_fin_done = False
        # R3-2: 对话框自动暂停期间用户手动切换,记录标志不再自动恢复
        if (not dialog_auto) and getattr(self, '_dialog_pause_active', False):
            self._dialog_user_toggled = True
        self.play_text = rt(f_s, "暂停" if playing else "播放", (255, 255, 255))

    def _update_dialog_pause(self, dialog_open: bool) -> None:
        """打开任意界面时自动暂停，全部关闭后恢复原播放状态。
        若期间用户手动改变了播放状态（空格/播放键/脚本启动），以用户操作为准。"""
        if dialog_open and not self._dialog_pause_active:
            self._dialog_pause_active = True
            self._pl_before_dialog = self.pl
            self._dialog_user_toggled = False
            if self.pl:
                self._set_playing(False, dialog_auto=True)
        elif not dialog_open and self._dialog_pause_active:
            self._dialog_pause_active = False
            # R3-2: 对话框打开期间用户手动按过播放/暂停则不再自动恢复
            if not self.pl and self._pl_before_dialog and not self._dialog_user_toggled:
                self._set_playing(True)

    def _sync_to_season_ctrl(self) -> None:
        sc = self.season_ctrl
        sc.sy = self.sy
        sc.sty = self.sty
        sc.edy = self.edy
        sc.st = self.st
        sc.ste = self.ste
        if getattr(self.cfg, 'ace_interpolated', False):
            # 插值模式下 sim.csa 可能已含插值部分，重新计算未膨胀基准（B16）
            try:
                confirmed = self.ace_engine.cumulative_ace_up_to(self._season_dt())
            except Exception:
                confirmed = self.csa
            sc.csa = confirmed
            sc.set_csa_base(confirmed)
            # 基准已含截至当前的确认点：同步 last_ace_ci，防首点/中间点重复计入（R11）
            # 未激活台风(尚未开始)保持 last_ace_ci=-1,避免其首点 ACE 永久漏计
            # R2-4: 已结束(sf)台风也同步,防回拨时间后重复计入首点
            for ty in self.tys:
                if (ty.act or ty.ss or ty.ci > 0 or ty.fin or ty.sf
                        or ty.v.ipos is not None):
                    ty.last_ace_ci = ty.ci
        else:
            sc.csa = self.csa
            sc.set_csa_base(self.csa)
        sc.current_ace_year = self.current_ace_year
        sc.ssf = self.ssf

    def _sync_season_state(self) -> None:
        sc = self.season_ctrl
        self.st = sc.st
        self.ste = sc.ste
        self.sy = sc.sy
        self.csa = sc.csa
        self.current_ace_year = sc.current_ace_year
        self.yf = sc.yf

    def _check_monthly_summary(self) -> None:
        try:
            mo = int(self.st[0:2])
        except (ValueError, IndexError):
            return
        if not (1 <= mo <= 12):
            return
        prev_ste = getattr(self, '_last_month_ste', None)
        if prev_ste is not None and self.ste < prev_ste - 1e-6:
            # ste 在每次跨年边界都会回卷(NH:12->1,SH:6->7),也会在整个季节
            # 循环回绕时回卷。边界月的总结必须在"中间年份"与"最后年份"回绕时
            # 都补触发,否则中间年份的 12 月(NH)/6 月(SH)月度总结会被漏掉。
            last_key = getattr(self, '_last_month_key', None)
            cur_key = (self.sy, mo)
            if last_key is not None:
                # 北半球: 上一月为 12 月, 当前在 1 月 -> 补触发上一年的 12 月
                # (覆盖中间年 1 月边界与完整循环回绕到首年两种情况)
                if last_key[1] == 12 and mo == 1:
                    self._ms.trigger(last_key[0], 12)
                    self._last_month_key = cur_key
                    self._last_month_ste = self.ste
                    return
                # 南半球: 上一月为 6 月, 当前处于 7 月(中间年)或回卷点 6/30(完整循环)
                # -> 补触发上一季的 6 月总结
                if (last_key[1] == 6 and mo in (6, 7)
                        and self.cfg.hemisphere == HEMISPHERE_SOUTH):
                    self._ms.trigger(last_key[0], 6)
                    self._last_month_key = cur_key
                    self._last_month_ste = self.ste
                    return
            self._last_month_key = None
        self._last_month_ste = self.ste
        key = (self.sy, mo)
        if not hasattr(self, '_last_month_key') or self._last_month_key is None:
            self._last_month_key = key
            return
        prev_year, prev_mo = self._last_month_key
        if key == (prev_year, prev_mo):
            return
        expected = ((prev_year + 1, 1) if prev_mo == 12 else (prev_year, prev_mo + 1))
        if key == expected:
            self._ms.trigger(prev_year, prev_mo)
        elif (prev_mo == 12 and key == (self.sty, 1)
              and prev_year == self.edy):
            # R3-5: 跨年回卷到第一个台风年时补触发上一年的 12 月总结
            self._ms.trigger(prev_year, prev_mo)
        self._last_month_key = key

    def _sync_land_state(self) -> None:
        self.view.sync_land_state(self.tys)



