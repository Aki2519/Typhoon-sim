# py/ty_sim.py
"""台风路径模拟系统主控制类。"""
from __future__ import annotations

import math
import pygame
import logging
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from datetime import datetime

from .constants import f_s, rt, TXT, CPH, CONFIG_FILE
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
from .ty_list import TyList
from .time_jump import TimeJump
from .settings import Settings
from .new_typhoon_dialog import NewTyphoonDialog
from .point_edit_dialog import PointEditDialog
from .point_list import PointList
from .statistics.dialog_chart import ACEChartDialog
from .statistics.intensity_chart import IntensityChartDialog
from .statistics.path_comparison import PathComparisonDialog
from .statistics.heatmap import PathHeatmapDialog
from .statistics.path_length_viewer import PathLengthViewer
from .statistics.season_stats_dialog import SeasonStatsDialog
from .statistics.intensity_comparison import IntensityComparisonDialog
from .monthly_summary import MonthlySummary

from .ty_sim_mixins import (
    TySimUtilsMixin,
    TySimDrawMixin, TySimEventMixin,
)
from .ty_sim_mixins.keyboard_mixin import TySimKeyboardMixin
from .script_engine import ScriptEngine
from .script_dialog import ScriptDialog

if TYPE_CHECKING:
    from .resource_manager import MapView

logger = logging.getLogger(__name__)

_MODE_NORMAL = "normal"
_MODE_SEASON = "season"
_MODE_EDIT = "edit"


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
            TySimDrawMixin, TySimEventMixin, TySimKeyboardMixin):

    MODE_NORMAL = _MODE_NORMAL
    MODE_SEASON = _MODE_SEASON
    MODE_EDIT = _MODE_EDIT

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

    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self.screen_width, self.screen_height = screen.get_size()
        self.control_panel_height = CPH
        self.map_height = self.screen_height - CPH

        self.cfg = AppConfig.load(CONFIG_FILE)

        self._init_attributes()
        self._init_resource_managers()
        self.view = ViewState(self.screen_width, self.screen_height, self.map_height,
                              self.map_mgr, self.cfg)
        self.ace_engine = ACEEngine(self)
        self.repo = DataRepository(self.cfg, self.res_mgr)
        self.repo.bind(self)

        self._install_descriptors()

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

        if self.window_topmost:
            self.toggle_window_topmost()

        if self.md == _MODE_SEASON:
            self._init_season_ace()
            self._sync_to_season_ctrl()

        self._dialog_stack: list = []
        self.landfall_records: list = []

    @classmethod
    def _install_descriptors(cls) -> None:
        if getattr(cls, '_descriptors_installed', False):
            return
        cls._descriptors_installed = True
        for name in AppConfig._FIELDS:
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

        self.st = "010100"
        self.ste = 0.0
        self.ssf = 12 * 3600
        self.tsa = self.csa = 0.0
        self.sy = self.sty = self.edy = 2000
        self.yf = False
        self.yad: Dict[int, float] = {}
        self.current_ace_year = 2000

        self.effects: List[LandfallEffect] = []

        self.info_box_slots: Dict[Typhoon, int] = {}
        self.info_box_free_slots = list(range(14))

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
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._view_dirty = False
        self._game_ct: int = 0

        self._ace_timeline_cache: Dict[int, List[Tuple[datetime, float]]] = {}
        self._ace_typhoon_cache: Dict[int, List[Tuple[str, float]]] = {}

    def _init_resource_managers(self) -> None:
        self.res_mgr = ResourceManager()
        self.map_mgr = MapManager(self)

    def _init_season_ace(self) -> None:
        try:
            dt = datetime(self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
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

    def _refresh_ace_data(self) -> None:
        self.ace_engine.refresh_all()

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
                current_dt = datetime(
                    self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
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
        self.map_mgr.update_view()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.update_all_screen_points()
        if self.map_mgr.land_img is not None:
            self.map_mgr.land_img = None
        self._view_dirty = True

    def update_all_screen_points(self) -> None:
        self.view.update_screen_points(self.tys, self.edit_typhoon)
        self._invalidate_all_path_caches()

    def update(self, dt: float) -> None:
        ct = pygame.time.get_ticks()
        dt = (ct - self.lst) / 1000.0
        self.lst = ct
        self._fps = 1.0 / dt if dt > 0 else 60.0
        self.input_handler.update(ct)

        if self._view_dirty:
            self._view_dirty = False
            self.map_mgr.update_land_mask()
            self._sync_land_state()

        if self.md == _MODE_SEASON:
            self.season_ctrl._pl = self.pl
            self.season_ctrl.update(dt)

        self.script_engine.update(dt)

        dialog_open = self.dialog_mgr.any_active()
        self.playback_ctrl._pl = self.pl
        self.playback_ctrl.update_all(ct, dt, dialog_open, self.season_ctrl)
        self.effects = self.playback_ctrl.effects
        self.landfall_records = self.playback_ctrl.landfall_records
        if self.pl != self.playback_ctrl._pl:
            self.pl = self.playback_ctrl._pl
            self.season_ctrl._pl = self.pl
        self._sync_season_state()
        if self.md == _MODE_SEASON and self.pl:
            self._check_monthly_summary()
        self._ms.update(dt)

    def _sync_to_season_ctrl(self) -> None:
        sc = self.season_ctrl
        sc.sy = self.sy
        sc.sty = self.sty
        sc.edy = self.edy
        sc.st = self.st
        sc.ste = self.ste
        sc.csa = self.csa
        sc.current_ace_year = self.current_ace_year

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
        self._last_month_key = key

    def _sync_land_state(self) -> None:
        self.view.sync_land_state(self.tys)

    def _mark_view_dirty(self) -> None:
        self._view_dirty = True


class InputHandler:
    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self._down = False
        self._down_time = 0
        self._down_pos = (0, 0)
        self._triggered = False

    def handle_event(self, e: pygame.event.Event) -> None:
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self._down = True
            self._down_time = pygame.time.get_ticks()
            self._down_pos = e.pos
            self._triggered = False
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._down = False

    def update(self, ct: int) -> None:
        if self.sim.dialog_mgr.any_active():
            return
        if self._down and not self._triggered and ct - self._down_time >= 200:
            mx, my = pygame.mouse.get_pos()
            if math.hypot(mx - self._down_pos[0], my - self._down_pos[1]) < 10:
                self.sim.on_long_press(mx, my)
            self._triggered = True


class DialogManager:
    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self.tl = TyList(sim)
        self.sd = Settings(sim)
        self.tj = TimeJump(sim)
        self.new_typhoon_dialog = NewTyphoonDialog(sim)
        self.point_edit_dialog = PointEditDialog(sim)
        self.point_list = PointList(sim)
        self.ace_chart = ACEChartDialog(sim)
        self.intensity_chart = IntensityChartDialog(sim)
        self.path_comparison = PathComparisonDialog(sim)
        self.heatmap = PathHeatmapDialog(sim)
        self.path_length_viewer = PathLengthViewer(sim)
        self.season_stats = SeasonStatsDialog(sim)
        self.intensity_comparison = IntensityComparisonDialog(sim)

    def handle_event(self, e: pygame.event.Event) -> bool:
        return any(d.handle_event(e) for d in self._all() if d.active)

    def draw(self, surface: pygame.Surface) -> None:
        stack: list = getattr(self.sim, '_dialog_stack', [])
        drawn = set()
        for d in stack:
            if d.active:
                d.draw(surface)
                drawn.add(id(d))
        for d in self._all():
            if d.active and id(d) not in drawn:
                d.draw(surface)

    def any_active(self) -> bool:
        return any(d.active for d in self._all())

    def _all(self) -> tuple:
        return (self.tj, self.sd, self.tl, self.new_typhoon_dialog,
                self.point_edit_dialog, self.point_list, self.ace_chart,
                self.intensity_chart, self.path_comparison, self.heatmap,
                self.path_length_viewer, self.season_stats, self.intensity_comparison,
                self.sim.script_dialog)
