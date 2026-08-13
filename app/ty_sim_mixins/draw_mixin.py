# py/ty_sim_mixins/draw_mixin.py
"""绘图协调 Mixin：聚合所有子绘图 Mixin。"""
from ._draw_path_mixin import TySimDrawPathMixin
from ._draw_icon_mixin import TySimDrawIconMixin
from .draw_info_boxes_mixin import TySimDrawInfoBoxesMixin


class TySimDrawMixin(
    TySimDrawPathMixin,
    TySimDrawIconMixin,
    TySimDrawInfoBoxesMixin,
):

    def draw(self, surface):
        self.renderer.draw(surface)

    def _draw_map(self, surface):
        """由 renderer 单独调用的地图绘制入口(本类 draw 不包含地图)。"""
        self.map_mgr.draw_map(surface)
