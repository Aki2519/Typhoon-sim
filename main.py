# main.py
import ctypes
import os
import pygame
import sys
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

pygame.init()


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

    screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE)
    pygame.display.set_caption("台风路径模拟系统")

    sim = TySim(screen)
    clock = pygame.time.Clock()

    if sim.window_topmost:
        sim.toggle_window_topmost()

    running = True
    while running:
        dt = clock.tick(0) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                sim.handle_resize(event.w, event.h)
            else:
                sim.handle_event(event)
        sim.update(dt)
        sim.draw(screen)
        pygame.display.flip()

    sim.save_config()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()