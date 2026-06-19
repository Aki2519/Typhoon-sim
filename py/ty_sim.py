# py/ty_sim.py
"""台风路径模拟系统主控制类。"""
import math
import pygame
import logging
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from .constants import f_s, rt, TXT, CPH, CONFIG_FILE
from .config import AppConfig
from .typhoon import Typhoon
from .landfall_effect import LandfallEffect
from .resource_manager import ResourceManager, MapManager
from .ace_engine import ACEEngine
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

from .ty_sim_mixins import (
    TySimUtilsMixin, TySimDataMixin, TySimSeasonMixin,
    TySimDrawMixin, TySimEventMixin,
)

logger = logging.getLogger(__name__)

_MODE_NORMAL = "normal"
_MODE_SEASON = "season"
_MODE_EDIT = "edit"


class TySim(TySimUtilsMixin, TySimDataMixin, TySimSeasonMixin, TySimDrawMixin, TySimEventMixin):

    MODE_NORMAL = _MODE_NORMAL
    MODE_SEASON = _MODE_SEASON
    MODE_EDIT = _MODE_EDIT

    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.screen_width, self.screen_height = screen.get_size()
        self.control_panel_height = CPH
        self.map_height = self.screen_height - CPH

        self._init_attributes()
        self._init_resource_managers()
        self.ace_engine = ACEEngine(self)
        self._pre_render_texts()

        self.cfg = AppConfig.load(CONFIG_FILE)      # <-- cfg 在这里赋值
        self._sync_config_hot_fields()              # <-- 现在安全了
        self.load_typhoon_files()
        self.map_mgr.update_map_image()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.dialog_mgr = DialogManager(self)
        self.input_handler = InputHandler(self)
        self.update_all_screen_points()
        self.map_mgr.update_land_mask()

        if self.window_topmost:
            self.toggle_window_topmost()

        if self.md == _MODE_SEASON:
            self._init_season_ace()

        # 焦点栈和登陆记录
        self._dialog_stack = []
        self.landfall_records = []

    def _init_attributes(self) -> None:
        self.tys: List[Typhoon] = []
        self.cti = 0
        self.edit_typhoon: Optional[Typhoon] = None

        self.pl = False
        self.lst = pygame.time.get_ticks()

        self.st = "010100"
        self.ste = 0
        self.ssf = 12 * 3600
        self.tsa = self.csa = 0.0
        self.sy = self.sty = self.edy = 2000
        self.yf = False
        self.yad: Dict[int, float] = {}
        self.current_ace_year = 2000

        self.effects: List[LandfallEffect] = []

        self.info_box_slots: Dict[Typhoon, int] = {}
        self.info_box_free_slots = list(range(9))

        self.error_message = ""
        self.error_time = 0
        self.dialog_page_cache = {}
        self._config_needs_save = False

        self.dragging_point = False
        self.drag_typhoon: Optional[Typhoon] = None
        self.drag_point_index = -1
        self.drag_start_pos = (0, 0)
        self.right_button_dragging = False
        self.right_drag_start_pos = (0, 0)
        self._view_dirty = False
        self._game_ct: int = 0               # 冻结的游戏时钟，暂停时停止推进

        self._ace_timeline_cache: Dict[int, List[Tuple[datetime, float]]] = {}
        self._ace_typhoon_cache: Dict[int, List[Tuple[str, float]]] = {}

        # 不再在此调用 _sync_config_hot_fields()

    def _sync_config_hot_fields(self):
        for field in _HOT_FIELDS:
            if hasattr(self.cfg, field):
                setattr(self, field, getattr(self.cfg, field))

    def _init_resource_managers(self) -> None:
        self.res_mgr = ResourceManager()
        self.map_mgr = MapManager(self)

    def _init_season_ace(self):
        try:
            dt = datetime(self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
            self.current_ace_year = self.ace_engine.ace_year(dt)
            self.csa = self.ace_engine.cumulative_ace_up_to(dt)
        except Exception:
            pass

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
        self.mode_desc_normal = rt(f_s, "模式: 正常", TXT)
        self.mode_desc_season = rt(f_s, "模式: 台风季", TXT)
        self.mode_desc_edit = rt(f_s, "模式: 编辑", TXT)

    # ── 配置委托 ──
    def __getattr__(self, name: str):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        try:
            cfg = object.__getattribute__(self, 'cfg')
        except AttributeError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        if name in cfg.__dataclass_fields__:
            return getattr(cfg, name)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        if name == 'cfg':
            return object.__setattr__(self, name, value)
        try:
            cfg = object.__getattribute__(self, 'cfg')
        except AttributeError:
            return object.__setattr__(self, name, value)
        if name in cfg.__dataclass_fields__:
            setattr(cfg, name, value)
            object.__setattr__(self, name, value)  # 同步到实例
            return
        object.__setattr__(self, name, value)

    # ── 公开方法 ──
    def get_display_name(self, ty: Typhoon) -> str:
        if ty.cust: return ty.cust
        if ty.sname: return ty.sname
        year = ty.start_time[:4] if ty.start_time and len(ty.start_time) >= 4 else ""
        base = f"{ty.basin}{ty.n}" if ty.basin else ty.n
        return f"{base}{year}" if year else base

    def current_typhoon(self) -> Optional[Typhoon]:
        return self.tys[self.cti] if self.tys else None

    def reset_map(self) -> None:
        self.map_mgr.reset_map()
        self.map_draw_rect = self.map_mgr.get_draw_rect()
        self.update_all_screen_points()

    def update_all_screen_points(self) -> None:
        for ty in self.tys:
            ty.update_screen_points(self.latlon_to_screen)
        if self.edit_typhoon:
            self.edit_typhoon.update_screen_points(self.latlon_to_screen)

    # ── 主循环 ──
    def update(self, dt: float) -> None:
        ct = pygame.time.get_ticks()
        dt = (ct - self.lst) / 1000.0
        self.lst = ct
        self.input_handler.update(ct)

        # 视图脏标记处理：重建陆地掩码后，同步所有台风 last_on_land
        if self._view_dirty:
            self._view_dirty = False
            self.map_mgr.update_land_mask()
            self._sync_land_state()

        # 暂停时仍允许旋转、淡出、登陆特效运行
        if self.md == _MODE_SEASON:
            self.update_season_time(dt)

        self._update_all(ct, dt)
        self.effects = [e for e in self.effects if e.update(ct)]

    def _update_all(self, ct: float, dt: float):
        dialog_open = self.dialog_mgr.any_active()
        paused = not self.pl or dialog_open   # 暂停：手动暂停 或 对话框打开
        current = self.current_typhoon() if self.md == _MODE_NORMAL else None
        for ty in self.tys:
            ty.us(dt)                       # 旋转 — 暂停时也继续
            self._fade_one(ty, ct)         # 淡出
            self.check_landfall(ty, ct)    # 登陆
            if ty.act:
                if self.md == _MODE_NORMAL:
                    if ty == current:
                        self._update_normal(ty, ct, paused)
                elif self.md == _MODE_SEASON:
                    if ty.ss and not ty.sf:
                        self._update_season(ty, ct, paused)
                elif self.md == _MODE_EDIT:
                    if self.edit_typhoon == ty:
                        self._update_edit(ty, ct, paused)

    def _fade_one(self, ty, ct):
        if ty.finish_time <= 0:
            return
        elapsed = (ct - ty.finish_time) / 1000.0
        if self.fade_typhoon:
            ty.icon_alpha = max(0, int(255 * (1.0 - elapsed / 30.0)))
        else:
            ty.icon_alpha = 255
        if self.fade_path:
            ty.path_alpha = max(0, int(255 * (1.0 - elapsed / 30.0)))
        else:
            ty.path_alpha = 0

    def _update_normal(self, ty, ct, paused=False):
        if len(ty.pts) == 1:
            ty.fin = True
            ty.ft = ct
            ty.finish_time = ct
        elif ty.fin:
            if ct - ty.ft >= 500:
                ty.fin = False
                if self.ac and self.tys:
                    self.cti = (self.cti + 1) % len(self.tys)
                    self.current_typhoon().rst()
        else:
            if not ty.ipos and ty.ci < len(ty.pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.sp, paused)

    def _update_season(self, ty, ct, paused=False):
        if ty.ci == 0 and ty.last_ace_ci == -1:
            pt = ty.pts[0]
            if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == self.current_ace_year:
                if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                    self.csa += pt['pace']
            ty.last_ace_ci = 0
        if len(ty.pts) == 1:
            ty.fin = True
            ty.sf = True
            ty.finish_time = ct
        elif not ty.fin:
            if not ty.ipos and ty.ci < len(ty.pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.sp, paused)
            if not paused and ty.ci > ty.last_ace_ci:
                for i in range(ty.last_ace_ci + 1, ty.ci + 1):
                    pt = ty.pts[i]
                    if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == self.current_ace_year:
                        if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                            self.csa += pt['pace']
                ty.last_ace_ci = ty.ci
            if ty.fin:
                ty.sf = True
                ty.act = False
        ty.cace = ty.pts[ty.ci]['ace'] if ty.pts else 0.0

    def _update_edit(self, ty, ct, paused=False):
        if len(ty.pts) == 1:
            ty.fin = True
            ty.ft = ct
            ty.finish_time = ct
        elif ty.fin:
            if ct - ty.ft >= 500:
                ty.fin = False
                self.pl = False
                ty.rst()
        else:
            if not ty.ipos and ty.ci < len(ty.pts) - 1:
                ty.sm(ct)
            ty.um(ct, self.sp, paused)

    # ── 登陆检测 ──
    def check_landfall(self, ty, ct):
        if not ty.act:
            return
        pos = ty.cpos()
        if not pos:
            return
        x, y = self.latlon_to_screen(pos['la'], pos['lo'])
        if not (0 <= x < self.screen_width and 0 <= y < self.map_height):
            return
        if self.map_mgr.land_img is None:
            self.map_mgr.update_land_mask()
        if self.map_mgr.land_img is None:
            return
        is_land = self.map_mgr.is_land_at_screen(x, y)
        if is_land and not ty.last_on_land:
            cp = ty.cp()
            if cp:
                strength = self.get_strength_category(cp['w'], cp['st'])
                self.landfall_records.append({
                    'name': self.get_display_name(ty),
                    'wind': cp['w'],
                    'year': self.current_ace_year if self.md == _MODE_SEASON else datetime.now().year,
                    'basin': ty.basin,
                    'la': pos['la'],
                    'lo': pos['lo'],
                })
                img1, img2 = self.res_mgr.get_landfall_images(strength)
                if img1 and img2:
                    self.effects.append(LandfallEffect(strength, pos['lo'], pos['la'], img1, img2, ct, self))
                sound = self.res_mgr.get_sound(strength)
                if sound:
                    sound.set_volume(self.volume)
                    sound.play()
        ty.last_on_land = is_land

    def _sync_land_state(self):
        """视图变化后同步所有台风 last_on_land，防止幻影登陆特效。"""
        if self.map_mgr.land_img is None:
            self.map_mgr.update_land_mask()
        if self.map_mgr.land_img is None:
            return
        for ty in self.tys:
            pos = ty.cpos()
            if not pos:
                continue
            x, y = self.latlon_to_screen(pos['la'], pos['lo'])
            if 0 <= x < self.screen_width and 0 <= y < self.map_height:
                ty.last_on_land = self.map_mgr.is_land_at_screen(x, y)

    def _mark_view_dirty(self):
        self._view_dirty = True


class InputHandler:
    def __init__(self, sim):
        self.sim = sim
        self._down = False
        self._down_time = 0
        self._down_pos = (0, 0)
        self._triggered = False

    def handle_event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self._down = True
            self._down_time = pygame.time.get_ticks()
            self._down_pos = e.pos
            self._triggered = False
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._down = False

    def update(self, ct):
        if self.sim.dialog_mgr.any_active():
            return
        if self._down and not self._triggered and ct - self._down_time >= 200:
            mx, my = pygame.mouse.get_pos()
            if math.hypot(mx - self._down_pos[0], my - self._down_pos[1]) < 10:
                self.sim.on_long_press(mx, my)
            self._triggered = True


class DialogManager:
    def __init__(self, sim):
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

    def handle_event(self, e):
        return any(d.handle_event(e) for d in self._all() if d.active)

    def draw(self, surface):
        for d in self._all():
            d.draw(surface)

    def any_active(self):
        return any(d.active for d in self._all())

    def _all(self):
        return (self.tj, self.sd, self.tl, self.new_typhoon_dialog,
                self.point_edit_dialog, self.point_list, self.ace_chart,
                self.intensity_chart, self.path_comparison, self.heatmap,
                self.path_length_viewer, self.season_stats)


_HOT_FIELDS = [
    'md', 'pl', 'sp', 'mis', 'mas', 'ac', 'fade_typhoon', 'fade_path',
    'mlo', 'Mlo', 'mla', 'Mla', 'screen_width', 'screen_height',
    'hemisphere', 'ace_limit_mode', 'ace_limit_basin', 'point_size', 'icon_size',
]