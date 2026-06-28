# py/app_context.py
"""应用上下文：轻量容器，传递给对话框和统计模块替代 sim。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig
    from .view_state import ViewState
    from .data_repo import DataRepository
    from .ace_engine import ACEEngine
    from .resource_manager import ResourceManager, MapManager
    from .script_engine import ScriptEngine


@dataclass
class AppContext:
    """向对话框/统计模块提供的只读上下文。"""
    cfg: AppConfig
    view: ViewState
    repo: DataRepository
    ace_engine: ACEEngine
    res_mgr: ResourceManager
    map_mgr: MapManager
    script_engine: Optional[ScriptEngine] = None
    dialog_mgr: object = None

    @property
    def screen_width(self) -> int:
        return self.view.screen_width

    @property
    def screen_height(self) -> int:
        return self.view.screen_height

    @property
    def map_height(self) -> int:
        return self.view.map_height

    @property
    def tys(self) -> list:
        return self.repo.tys
