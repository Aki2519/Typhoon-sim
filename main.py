import ctypes
import os
import pygame
import sys
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)


def _apply_dpi():
    # 必须在 pygame.init() 之前调用；此阶段不能导入 py.*（constants.fonts 需要 font 已初始化）
    # 缺省 False = 程序 DPI aware(UI 1:1 不放大),与 AppConfig 默认一致
    disable = False
    if os.path.exists("config.json"):
        try:
            with open("config.json", 'r', encoding='utf-8') as f:
                disable = json.load(f).get("disable_dpi_scaling", False)
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

pygame.init()
try:
    pygame.mixer.set_num_channels(32)
except pygame.error:
    pass
try:
    pygame.key.stop_text_input()
except Exception:
    pass

from app.config import AppConfig
import app.constants as constants
from app.ty_sim import TySim

# 启动时只解析一次配置文件（窗口尺寸等全部由此派生）
_cfg = AppConfig.load(constants.CONFIG_FILE)

_WIN_TITLE = "台风路径模拟系统"


def _maximize_window(title: str = _WIN_TITLE):
    """最大化已创建的窗口并返回客户区尺寸 (w, h)；失败返回 None。"""
    try:
        import ctypes.wintypes as wt
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if not hwnd:
            return None
        # SW_MAXIMIZE = 3
        ctypes.windll.user32.ShowWindow(hwnd, 3)
        rect = wt.RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
        w, h = rect.right, rect.bottom
        if w > 0 and h > 0:
            return int(w), int(h)
    except Exception:
        return None
    return None


def main():
    sw, sh = _cfg.screen_width, _cfg.screen_height
    constants.SW, constants.SH = sw, sh
    constants.MH = sh - constants.CPH

    # 窗口图标必须在 set_mode 之前设置,否则任务栏图标不生效
    try:
        icon = pygame.image.load(os.path.join(script_dir, "assets", "icon.png"))
        pygame.display.set_icon(icon)
    except Exception:
        pass

    screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE, vsync=0)
    pygame.display.set_caption(_WIN_TITLE)

    # 启动即最大化：同步客户区尺寸到 pygame surface / 配置 / 布局常量
    if getattr(_cfg, "start_maximized", True):
        wpair = _maximize_window()
        if wpair:
            mw, mh = wpair
            if (mw, mh) != (sw, sh):
                try:
                    screen = pygame.display.set_mode((mw, mh), pygame.RESIZABLE, vsync=0)
                except Exception:
                    pass
                sw, sh = mw, mh
                constants.SW, constants.SH = sw, sh
                constants.MH = sh - constants.CPH
                _cfg.screen_width, _cfg.screen_height = sw, sh

    sim = TySim(screen, cfg=_cfg)
    clock = pygame.time.Clock()

    running = True
    perf = pygame.time.get_ticks
    last_resize_save = 0
    while running:
        cap = 60 if (perf() - getattr(sim, '_last_interact', 0) < 100) \
            else max(0, getattr(sim.cfg, 'fps_cap', 120))
        dt = clock.tick(cap) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                sim.handle_resize(event.w, event.h)
                last_resize_save = perf()
            else:
                sim.handle_event(event)
        t0 = perf()
        sim.update(dt)
        t1 = perf()
        sim.draw(screen)
        sim._t_update_ms = t1 - t0
        sim._t_draw_ms = perf() - t1
        pygame.display.flip()
        if last_resize_save and perf() - last_resize_save > 1000:
            last_resize_save = 0
            sim.save_config()

    sim.save_config()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
