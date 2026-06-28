# py/ty_sim_mixins/draw_mixin.py
"""绘图协调 Mixin：聚合所有子绘图 Mixin。"""
from __future__ import annotations
from .draw_typhoon_mixin import TySimDrawTyphoonMixin
from .draw_info_boxes_mixin import TySimDrawInfoBoxesMixin
from .draw_ace_mixin import TySimDrawACEMixin
from .draw_season_clock_mixin import TySimDrawSeasonClockMixin
from .draw_control_panel_mixin import TySimDrawControlPanelMixin


class TySimDrawMixin(
    TySimDrawTyphoonMixin,
    TySimDrawInfoBoxesMixin,
    TySimDrawACEMixin,
    TySimDrawSeasonClockMixin,
    TySimDrawControlPanelMixin,
):

    def draw(self, surface):
        self.renderer.draw(surface)

    def _draw_map(self, surface):
        self.map_mgr.draw_map(surface)

    def _draw_main_view(self, surface):
        """绘制主界面（不含面板和弹窗），用于截图。委托给 Renderer。"""

        self.renderer.draw_view_only(surface)