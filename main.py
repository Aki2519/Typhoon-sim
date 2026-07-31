import ctypes
import os
import pygame
import sys
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

pygame.init()
try:
    pygame.mixer.set_num_channels(32)
except pygame.error:
    pass
try:
    pygame.key.stop_text_input()
except Exception:
    pass


def _apply_dpi():
    config_file = "config.json"
    disable = True
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                disable = json.load(f).get("disable_dpi_scaling", True)
        except Exception:
            pass
    if not disable:
        for func in ('SetProcessDpiAwareness', 'SetProcessDPIAware'):
            try:
                getattr(ctypes.windll.shcore, func, getattr(ctypes.windll.user32, func, None))(1)
                break
            except Exception:
                continue


_apply_dpi()

from py.utils import load_window_size
import py.constants as constants
from py.ty_sim import TySim


def main():
    sw, sh = load_window_size()
    constants.SW, constants.SH = sw, sh
    constants.MH = sh - constants.CPH

    screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE, vsync=0)
    pygame.display.set_caption("台风路径模拟系统")

    sim = TySim(screen)
    clock = pygame.time.Clock()

    running = True
    perf = pygame.time.get_ticks
    while running:
        dt = clock.tick(max(0, getattr(sim.cfg, 'fps_cap', 120))) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                sim.handle_resize(event.w, event.h)
            else:
                sim.handle_event(event)
        t0 = perf()
        sim.update(dt)
        t1 = perf()
        sim.draw(screen)
        sim._t_update_ms = t1 - t0
        sim._t_draw_ms = perf() - t1
        pygame.display.flip()

    sim.save_config(force=True)
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()