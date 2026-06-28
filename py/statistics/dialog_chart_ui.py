# py/statistics/dialog_chart_ui.py
"""ACE 图表对话框 UI 辅助：底部按钮、标题、跳转、悬停、保存。"""
from __future__ import annotations
import pygame
import os
from ..constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER,
    HEMISPHERE_NORTH,
)
from ..input_field import InputField


class DialogChartUI:
    """Mixin: ACE 图表 UI 辅助方法。"""
