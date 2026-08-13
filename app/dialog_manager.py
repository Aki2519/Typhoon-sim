# py/dialog_manager.py
"""对话框管理器：集中管理所有对话框的创建、事件分发和绘制。"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .ty_sim import TySim

from .ty_list import TyList
from .settings import Settings
from .time_jump import TimeJump
from .new_typhoon_dialog import NewTyphoonDialog
from .point_edit_dialog import PointEditDialog
from .point_list import PointList
from .track_dialog import TrackDialog
from .statistics.dialog_chart import ACEChartDialog
from .statistics.intensity_chart import IntensityChartDialog
from .statistics.path_comparison import PathComparisonDialog
from .statistics.heatmap import PathHeatmapDialog
from .statistics.path_length_viewer import PathLengthViewer
from .statistics.season_stats_dialog import SeasonStatsDialog
from .statistics.intensity_comparison import IntensityComparisonDialog
from .statistics.summary_list import SummaryListDialog
from .statistics.multi_year_chart import MultiYearDialog


class DialogManager:
    def __init__(self, sim: TySim) -> None:
        self.sim = sim
        self.tl = TyList(sim)
        self.sd = Settings(sim)
        self.tj = TimeJump(sim)
        self.new_typhoon_dialog = NewTyphoonDialog(sim)
        self.point_edit_dialog = PointEditDialog(sim)
        self.point_list = PointList(sim)
        self.track_dialog = TrackDialog(sim)
        self.ace_chart = ACEChartDialog(sim)
        self.intensity_chart = IntensityChartDialog(sim)
        self.path_comparison = PathComparisonDialog(sim)
        self.heatmap = PathHeatmapDialog(sim)
        self.path_length_viewer = PathLengthViewer(sim)
        self.season_stats = SeasonStatsDialog(sim)
        self.intensity_comparison = IntensityComparisonDialog(sim)
        self.summary_list = SummaryListDialog(sim)
        self.multi_year = MultiYearDialog(sim)
        self._active_count: Optional[int] = None

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
        c = self._active_count
        if c is None:
            c = sum(1 for d in self._all() if d.active)
            self._active_count = c
        return c > 0

    def _all(self) -> tuple:
        return tuple(d for d in (
            self.tj, self.sd, self.tl, self.new_typhoon_dialog,
            self.point_edit_dialog, self.point_list, self.track_dialog,
            self.ace_chart,
            self.intensity_chart, self.path_comparison, self.heatmap,
            self.path_length_viewer, self.season_stats, self.intensity_comparison,
            self.summary_list, self.multi_year,
            getattr(self.sim, 'script_dialog', None),
        ) if d is not None)
