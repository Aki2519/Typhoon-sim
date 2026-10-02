# tools/shot_sim_r158.py
"""把模拟模式**真实渲染一帧**存成 PNG, 用于人眼检查布局(轮次 158)。

为什么需要: 轮次 158 在控制面板 Row2 新加了两个按钮(单步15m / 骰子种子)。
"文字不溢出、不重叠、不被遮挡"是硬要求, 而这一类问题**只能看图确认** ----
断言测不出来。

用法:
    python tools/shot_sim_r158.py [输出png] [秒数]
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

pygame.init()
screen = pygame.display.set_mode((1280, 800))
from app.ty_sim import TySim  # noqa: E402
from app.constants import MODE_SIM  # noqa: E402


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else 'output/sim/_reference/shot_sim_r158.png'
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    app = TySim(screen)
    app.md = MODE_SIM
    app._sim_reset(msg=False)
    app.sim_playing = True
    frames = int(secs * 30)
    dt = 1.0 / 30.0
    for _ in range(frames):
        app._sim_update(dt)
    screen.fill((0, 0, 0))
    app._sim_render(screen)
    # 控制面板是独立绘制的, 必须一起画出来才能看到按钮
    draw_panel = getattr(app, 'draw_control_panel', None) or getattr(app, 'control_panel', None)
    try:
        if hasattr(app, 'control_panel'):
            app.control_panel.build()
            app.control_panel.draw(screen)
    except Exception as exc:
        print('控制面板绘制失败(不影响地图截图):', exc)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pygame.image.save(screen, out)
    print('saved', out, screen.get_size())


if __name__ == '__main__':
    main()
