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
    """最大化已创建的窗口并返回客户区尺寸 (w, h)；失败返回 None。

    ⚠⚠ 审查抓到: 原来直接用 `ctypes.windll.user32.XXX(...)` 而**没有声明原型**
    ⇒ ctypes 默认把返回值当 `c_int`、把参数当 `c_int` 传, 而 `HWND` 是
    **64 位指针**(`app/window_util.py` 有同样的问题, 那边已修)。
    当前机器 HWND 数值小所以只表现为"偶发查不到窗口", 但按 c_int 传句柄是错的。
    这里显式声明 argtypes/restype; 用**私有** `WinDLL` 实例, 不污染全局 `ctypes.windll`。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u.FindWindowW.restype = wt.HWND
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.ShowWindow.restype = wt.BOOL
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetClientRect.restype = wt.BOOL
        hwnd = u.FindWindowW(None, title)
        if not hwnd:
            return None
        # SW_MAXIMIZE = 3
        u.ShowWindow(hwnd, 3)
        rect = wt.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rect))
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


#: W1 修复4: 加载进度重绘的**最小间隔**(ms)。50 ms ≈ 20 FPS 的进度刷新,
#: 肉眼已看不出停顿; 而台风库解析单个文件通常只要几毫秒 ⇒ 省下绝大部分重绘。
_LOAD_PROGRESS_MIN_MS = 50


def _make_load_progress(screen, min_interval_ms: int = _LOAD_PROGRESS_MIN_MS):
    """构造 `_draw_splash` 的**节流**回调, 供 `TySim(on_load_progress=…)` 使用。

    为什么要有这个:
      `app/data_repo.py:152-158` 每解析完一个台风文件就回调一次; 本仓库有
      **207 个** `.dat/.txt` ⇒ 原来每次都 `_draw_splash` + `pygame.display.flip()`,
      即 **207 次 `font.render` + 414 次 blit + 207 次 flip**。
      dummy SDL 下 flip 近似免费, 真窗口下是实打实的呈现成本(见
      `output/exploration/13_perf_startup.md` §2.3 的漏测项 h)。

    契约:
      * 距上次重绘 < `min_interval_ms` 且**还没加载完** → 跳过;
      * **最后一次(`done >= total`)一定重绘** —— 否则进度条会停在中途;
      * `total <= 0`(异常输入)时也一定重绘(条件里 `done < total` 为假);
      * 首次调用一定重绘(初始 `last` 取极小值)。
    返回值可直接当 `on_load_progress` 传下去。
    """
    last = [-10 ** 9]

    def _on_load(done, total):
        now = pygame.time.get_ticks()
        if done < total and now - last[0] < min_interval_ms:
            return
        last[0] = now
        _draw_splash(screen, "正在加载台风数据…", done, total)
        pygame.display.flip()

    return _on_load


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
                    # ⚠ 只在 set_mode **成功**后才写回尺寸 —— 原来 try/except 之后
                    # 无条件赋值, 于是 set_mode 失败时内部按最大化尺寸布局、而
                    # surface 还是旧的, 并把这个尺寸存盘(审查 B4)。
                    sw, sh = mw, mh
                except Exception:
                    pass

    # ── 与系统对账 + 把窗口挪回屏内（**两条分支都要做**）──────────────────────
    # ⚠ 审查 B3: 原来这两段只写在 `else` 里, 而 `start_maximized` 默认 True
    #   (config.py:50) ⇒ **默认路径根本不跑**; 且 `_maximize_window()` 查不到窗口
    #   时返回 None, if 分支什么都不做也不 fallback ⇒ 症状照样复现。
    #
    # 为什么必须对账: `set_mode` 对"放不下"的尺寸**不报错**, 应用于是相信自己是
    # 请求值(实测现象: 应用以为 2560x1540, 屏幕/位置上却是另一回事)。
    # 为什么必须挪窗: `set_mode` **只改大小、不动位置** ⇒ 窗口从原位往右下长,
    # 超出屏幕的部分用户看不见(实测客户区 1540 时窗口底边到 1902, 屏只有 1600)
    # ⇒ 用户以为"改了没生效"。
    try:
        from app.window_util import actual_client_size, keep_window_on_screen
        real = actual_client_size(_WIN_TITLE)
        if real and real[0] > 0 and real[1] > 0:
            sw, sh = real
        keep_window_on_screen(_WIN_TITLE)
    except Exception:
        pass

    # ⚠ 审查 B1/B2: 统一以 **surface 的真实尺寸**为准, 并把 `_cfg` 一起同步。
    #   原来 else 分支只写了 `sw,sh` 与 `constants.*`, **漏了 `_cfg.screen_width/height`**
    #   ⇒ 那个值会被 `save_config()` 存盘, 于是每次启动重复"改了不生效", 永不收敛。
    #   `handle_resize`(ty_sim.py:527-535)本来就是 sw/cfg/view 三处同改的契约。
    try:
        sw, sh = screen.get_size()
    except Exception:
        pass
    constants.SW, constants.SH = sw, sh
    constants.MH = sh - constants.CPH
    try:
        _cfg.screen_width, _cfg.screen_height = sw, sh
    except Exception:
        pass

    _on_load = _make_load_progress(screen)

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
        # 写崩溃日志(统一进 output/logs, 精简主目录)
        _logd = os.path.join("output", "logs")
        os.makedirs(_logd, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        crash_fn = os.path.join(_logd, f"crash_{ts}.log")
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
