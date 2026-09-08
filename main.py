import ctypes
import os
import time
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


def _draw_splash(screen, text="正在加载台风数据…", done=None, total=None):
    """启动加载 splash + 可选进度条。用应用字体(含中文)而非内建字体。"""
    try:
        screen.fill((16, 22, 36))
        w, h = screen.get_size()
        font = constants.f_m
        font_small = constants.f_s
        ts = font.render(text, True, (230, 235, 245))
        screen.blit(ts, ((w - ts.get_width()) // 2, h // 2 - 40))
        if done is not None and total:
            bw = min(400, w - 120)
            bx = (w - bw) // 2
            by = h // 2
            pygame.draw.rect(screen, (40, 48, 66), (bx, by, bw, 14), 0, 7)
            fw = int(bw * max(0, min(1, done / max(1, total))))
            if fw > 0:
                pygame.draw.rect(screen, (70, 150, 230), (bx, by, fw, 14), 0, 7)
            ps = font_small.render(f"{done}/{total}", True, (200, 210, 225))
            screen.blit(ps, ((w - ps.get_width()) // 2, by + 20))
    except Exception:
        pass


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

    def _on_load(done, total):
        _draw_splash(screen, "正在加载台风数据…", done, total)
        pygame.display.flip()

    _draw_splash(screen)
    pygame.display.flip()
    sim = TySim(screen, cfg=_cfg, on_load_progress=_on_load)
    clock = pygame.time.Clock()

    running = True
    perf = pygame.time.get_ticks
    last_resize_save = 0
    try:
        while running:
            cap = 60 if (perf() - getattr(sim, '_last_interact', 0) < 100) \
                else max(0, getattr(sim.cfg, 'fps_cap', 120))
            # 钳制 dt: 一次长卡顿(首次加载/GC)会让动画型状态(月度总结滑入滑出、
            # 平滑相机)整段跳过, 单帧最多按 100ms 推进
            dt = min(clock.tick(cap) / 1000.0, 0.1)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # 关窗是洋区编辑改动的最后保存机会(唯一保存入口是 exit)
                    try:
                        sim.ocean_edit.exit()
                    except Exception:
                        pass
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
            # 视觉资源惰性预热: 首屏/交互期后台执行, 不阻塞启动
            sim._tick_warmup()
            pygame.display.flip()
            if last_resize_save and perf() - last_resize_save > 1000:
                last_resize_save = 0
                sim.save_config()
    except Exception:
        import traceback
        # 尽力保存当前状态
        try:
            sim.save_config()
        except Exception:
            pass
        # 写崩溃日志
        os.makedirs("logs", exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        crash_fn = os.path.join("logs", f"crash_{ts}.log")
        ctx = f"mode={getattr(sim, 'md', '?')} cti={getattr(sim, 'cti', '?')}"
        try:
            with open(crash_fn, 'w', encoding='utf-8') as f:
                f.write(ctx + "\n\n" + traceback.format_exc())
        except Exception:
            pass
        # 全屏先恢复窗口再弹窗
        try:
            pygame.display.set_mode((_cfg.screen_width, _cfg.screen_height), pygame.RESIZABLE)
        except Exception:
            pass
        try:
            ctypes.windll.user32.MessageBoxW(
                0, f"程序发生错误,日志已保存至 {crash_fn}", "台风路径模拟系统", 0x10)
        except Exception:
            pass
        pygame.quit()
        sys.exit(1)

    sim.save_config()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
