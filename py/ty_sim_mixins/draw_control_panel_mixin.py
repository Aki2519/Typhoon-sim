# py/ty_sim_mixins/draw_control_panel_mixin.py
"""控制面板绘制 Mixin。"""
from __future__ import annotations


class TySimDrawControlPanelMixin:
    """底部控制按钮、速度条、模式切换。"""

    def draw_control_panel(self, surface) -> None:
        from ..control_panel import ControlPanel
        if not hasattr(self, '_panel') or self._panel is None:
            self._panel = ControlPanel(self)
            self._panel.build()
        self._panel.draw(surface)

    @property
    def control_panel(self):
        if not hasattr(self, '_panel') or self._panel is None:
            from ..control_panel import ControlPanel
            self._panel = ControlPanel(self)
            self._panel.build()
        return self._panel
