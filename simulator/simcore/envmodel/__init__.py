# simulator/simcore/envmodel/__init__.py
"""F2/F3 离线环境场模拟器(运行时零外部数据)。

全部输入来自 simulator/env/model/*.npz —— 由 tools/train_env_base.py 与
tools/train_env_modes.py 离线训练烘焙。运行时只做:
    场 = 基态(儒略日插值) + 模态异常(查表/AR) + 增暖趋势 + 天气尺度随机场

缺任一 npz 时, solve() 返回 None, 上层回落到旧的合成库/解析场 —— 保证
"没有训练产物也能跑", 而不是崩。
"""
from .store import available, load, MODEL_DIR          # noqa: F401
from .solver import solve, clear_cache                 # noqa: F401
