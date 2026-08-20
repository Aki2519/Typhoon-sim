# simulator.simcore/__init__.py
"""SimCore 台风数值模拟器移植: 核心物理 + pygame 渲染。
惰性: 默认只导入 core(numpy, 无 pygame 依赖); render(pygame)按需显式导入。
"""
from . import core

__all__ = ['core']
