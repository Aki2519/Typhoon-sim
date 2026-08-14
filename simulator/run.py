# simulator/run.py
"""台风架空模拟器 — 主程序入口(F5 界面骨架 + F11 端到端管线)。

运行:
  python simulator/run.py --seed 1 --years 3             # 界面模式
  python simulator/run.py --seed 1 --years 3 --headless  # 批处理(仅生成)
管线: modes → env(生成场) → gen(records) → sim(.dat) → 演示帧渲染。
"""
from __future__ import annotations
import os
import sys
import json
import time
import random
import re
import subprocess
from datetime import datetime, timedelta
from typing import Optional, Tuple
import pygame

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, ROOT)

# 惰性 UI 依赖(pygame.init 后加载,避免无头导入字体失败)
T = LAYERS = ModePanel = render_layer = set_video_context = None


def _load_ui():
    global T, LAYERS, ModePanel, render_layer, set_video_context
    if T is None:
        from simulator.ui import theme as _T
        from simulator.ui.main import (LAYERS as _L, ModePanel as _MP,
                                       render_layer as _RL, set_video_context as _SVC)
        T, LAYERS = _T, _L
        ModePanel, render_layer, set_video_context = _MP, _RL, _SVC


# 布局: 全屏显示面 + 底部功能栏(参考 typhoon_sim 控制栏, 两行控件)
BOTTOM_H = 96

MODE_NAMES = {
    'nino34': 'ENSO', 'soi': 'SOI', 'pdo': 'PDO',
    'amo': 'AMO', 'dmi': 'IOD',
}

VIDEO_LAYER_IDS = ('olr', 'video_refl', 'video_sfc', 'video_mid',
                   'video_high', 'video_track', 'video_station', 'video_center')

# 上拉框弹出动画高度(从功能栏向上展开)
MENU_ITEM_H = 30


class UpMenu:
    """上拉框(从底部功能栏向上弹出的选项列表)。

    图层选择 / 追踪台风选择共用: items=[(id, 名称, 颜色)], 点击回调。
    """

    def __init__(self, items, on_select):
        self.items = items
        self.on_select = on_select
        self.rect = pygame.Rect(0, 0, 240, 8 + MENU_ITEM_H * len(items))
        self.visible = False

    def open_above(self, anchor: pygame.Rect, screen_w: int, screen_h: int):
        self.rect.width = 240
        self.rect.height = 8 + MENU_ITEM_H * len(self.items)
        x = min(anchor.x, screen_w - self.rect.w - 8)
        self.rect.x = x
        self.rect.bottom = anchor.y - 4        # 向上弹出
        # M7: 顶部钳制, 避免项数多时超出屏幕顶部(必要时从顶部对齐并增高)
        if self.rect.y < 4:
            self.rect.y = 4
            self.rect.height = min(self.rect.height, anchor.y - 8)
        self.visible = True

    def handle_event(self, e) -> bool:
        if not self.visible:
            return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not self.rect.collidepoint(e.pos):
                self.visible = False
                return True
            i = (e.pos[1] - self.rect.y - 4) // MENU_ITEM_H
            if 0 <= i < len(self.items):
                self.on_select(self.items[i][0])
                self.visible = False
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.visible = False
            return True
        return False

    def draw(self, surface):
        if not self.visible:
            return
        T.draw_panel(surface, self.rect, radius=8)
        y = self.rect.y + 4
        for lid, name, color in self.items:
            item = pygame.Rect(self.rect.x + 6, y, self.rect.w - 12, MENU_ITEM_H - 4)
            if color is not None:
                pygame.draw.circle(surface, color, (item.x + 9, item.centery), 4)
            on = (lid == getattr(self, '_selected', None))
            nm = T.ellipsis_text(16, name, T.TEXT if on else T.TEXT_DIM,
                                 max_w=item.w - 24)
            surface.blit(nm, (item.x + 18, item.y + 4))
            if on:
                pygame.draw.rect(surface, T.ACCENT, item, 1, border_radius=4)
            y += MENU_ITEM_H
        pygame.draw.rect(surface, T.ACCENT, (self.rect.x, self.rect.y - 2,
                                             self.rect.w, 2), border_radius=1)


class SimulatorApp:
    def __init__(self, screen, series, seed: int):
        _load_ui()
        self.screen = screen
        self.series = series          # {mode: [(y, m, v, seg), ...]}
        self.seed = seed
        self.sw, self.sh = screen.get_size()

        # 模拟时间(6h 步进, 从接续段起始; 空序列时用默认年份)
        ys = sorted({y for m in self.series for y, _, _, _ in self.series[m]})
        self.year = ys[0] if ys else 2000
        self.month = 1
        self.day = 1
        self.hour = 0
        self.playing = False
        self.speed = 1               # 1/2/4/8
        self.speed_opts = [1, 2, 4, 8]

        self.layer_index = 0
        self.layer_opacity = 100
        self.layer_contour = False
        self.show_rings = False
        self.show_modes_overlay = False
        self.kernel = 'B'                 # 模拟内核: 'B' 合成架空 | 'A' 真实大气(IFS/ERA5)
        self.kernel_a_ready = self._check_kernel_a()
        self.wrf_proc = None              # 后台 WRF 管线进程
        self.wrf_poll_t = 0.0
        self.settings_panel = None        # 设置面板(S 键)
        self.kernel_open = False

        self.ui_hidden = False
        self.gen_state = 'ready'     # ready/generating/ok/bad
        self.gen_msg = '就绪'
        self.mode_panel = None       # ModePanel 浮层
        self.gen_panel = None        # GenPanel 生成设置浮层(需求3)
        self.typhoons = []           # 阶段5 接入
        self.station_point = None
        self.layer_menu = None       # 上拉框: 图层选择
        self.typhoon_menu = None     # 上拉框: 追踪台风选择
        self._layout()
        from simulator.ui.display import DisplayView
        self.view = DisplayView(self.view_rect)
        # 恢复持久化的区域图设置
        self._apply_saved_region()

    # ── 布局 ──

    def _check_kernel_a(self) -> bool:
        """内核 A 就绪 = 可访问真实大气数据源(Open-Meteo, 免注册)。"""
        try:
            from simulator.env.real_source import available
            return available()
        except Exception:
            return False

    def _layout(self):
        w, h = self.sw, self.sh
        self.view_rect = pygame.Rect(0, 0, w, h - BOTTOM_H)
        self.bottom_rect = pygame.Rect(0, h - BOTTOM_H, w, BOTTOM_H)

    def _rects(self):
        return (self.view_rect, self.bottom_rect)

    def _apply_saved_region(self):
        """从持久化设置恢复 D01 区域图参数(中心/跨度)。"""
        try:
            from simulator.ui.main import UI_SETTINGS
            self.view.set_region(
                lat=UI_SETTINGS.get('region_lat', 20.0),
                lon=UI_SETTINGS.get('region_lon', 125.0),
                lat_span=UI_SETTINGS.get('region_lat_span', 40.0),
                lon_span=UI_SETTINGS.get('region_lon_span', 70.0))
        except Exception:
            pass

    # ── 主循环 ──

    def run(self):
        clock = pygame.time.Clock()
        running = True
        while running:
            dt = clock.tick(60) / 1000.0
            self._poll_wrf_pipeline()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.VIDEORESIZE:
                    self.sw, self.sh = e.w, e.h
                    self._layout()
                    self.view.rect = self.view_rect
                    continue
                elif e.type == pygame.KEYDOWN:
                    def _panel_typing(p):
                        return (p is not None
                                and any(getattr(ib, 'active', False)
                                        for ib in getattr(p, 'inputs', [])))
                    typing = _panel_typing(self.gen_panel) or _panel_typing(self.settings_panel)
                    if not typing and self._key(e):
                        continue
                if self.gen_panel and self.gen_panel.handle_event(e):
                    continue
                if self.settings_panel and self.settings_panel.handle_event(e):
                    continue
                if self.mode_panel and self.mode_panel.handle_event(e):
                    continue
                if self.layer_menu and self.layer_menu.handle_event(e):
                    continue
                if self.typhoon_menu and self.typhoon_menu.handle_event(e):
                    continue
                if self.view.handle_event(e):
                    continue
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    self._click(e.pos)
            if self.playing:
                self._advance()
            self.draw()
            pygame.display.flip()
        pygame.quit()

    def _key(self, e) -> bool:
        k = e.key
        if k == pygame.K_SPACE:
            self.playing = not self.playing
            return True
        if k == pygame.K_LEFT:
            self._advance(-1)
            return True
        if k == pygame.K_RIGHT:
            self._advance(1)
            return True
        if k == pygame.K_LEFTBRACKET:
            self._advance(-4)
            return True
        if k == pygame.K_RIGHTBRACKET:
            self._advance(4)
            return True
        if k == pygame.K_h:
            self.ui_hidden = not self.ui_hidden
            return True
        if k == pygame.K_s:
            if self.settings_panel is None:
                self.settings_panel = SettingsPanel(self)
            else:
                self.settings_panel = None
            return True
        if k == pygame.K_r:
            self.view.reset()
            self.gen_msg = '视图已重置'
            return True
        if k == pygame.K_m:
            self.mode_panel = ModePanel(self.series, MODE_NAMES)
            return True
        if k == pygame.K_ESCAPE:
            self.mode_panel = None
            self.gen_panel = None
            self.settings_panel = None
            return True
        if k == pygame.K_p:
            self._screenshot()
            return True
        if k == pygame.K_F12:
            self._toggle_topmost()
            return True
        if k == pygame.K_f:
            # 画面模式: 循环切换 8 类视频画面
            vids = [i for i, (lid, *_ ) in enumerate(LAYERS) if lid in VIDEO_LAYER_IDS]
            if self.layer_index in vids:
                self.layer_index = vids[(vids.index(self.layer_index) + 1) % len(vids)]
            else:
                self.layer_index = vids[0] if vids else 0
            return True
        # 1-0 切换图层
        ch = pygame.key.name(k)
        if ch.isdigit() and 1 <= int(ch) <= 10:
            self.layer_index = (int(ch) - 1) % len(LAYERS)
            return True
        # 视频图层快捷键(q/w/e/r/t/y/u)
        vmap = {'q': 'video_refl', 'w': 'video_sfc', 'e': 'video_mid',
                'r': 'video_high', 't': 'video_track', 'y': 'video_station',
                'u': 'video_center'}
        if ch in vmap:
            for i, (lid, *_ ) in enumerate(LAYERS):
                if lid == vmap[ch]:
                    self.layer_index = i
                    return True
        return False

    def _screenshot(self):
        os.makedirs(os.path.join(ROOT, 'picture'), exist_ok=True)
        name = f"sim_{self.year}{self.month:02d}{self.day:02d}_{self.hour:02d}.png"
        pygame.image.save(self.screen, os.path.join(ROOT, 'picture', name))

    def _toggle_topmost(self):
        if os.name == 'nt':
            try:
                import win32gui, win32con
                hwnd = win32gui.FindWindow(None, "台风架空模拟器")
                if hwnd:
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            except Exception:
                pass

    def _advance(self, steps: int = 1):
        steps *= self.speed if self.playing else 1
        for _ in range(abs(steps)):
            if steps < 0:
                self._step(-6)
            else:
                self._step(6)

    @staticmethod
    def _month_len(y: int, m: int) -> int:
        if m == 2:
            return 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28
        return 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30

    def _step(self, hours: int):
        # R2-02: 按真实月长推进(固定 31 天月历会产生 2/30、2/31 等非法日期)
        self.hour += hours
        while self.hour >= 24:
            self.hour -= 24
            self.day += 1
            if self.day > self._month_len(self.year, self.month):
                self.day = 1
                self.month += 1
                if self.month > 12:
                    self.month = 1
                    self.year += 1
        while self.hour < 0:
            self.hour += 24
            self.day -= 1
            if self.day < 1:
                self.month -= 1
                if self.month < 1:
                    self.month = 12
                    self.year -= 1
                self.day = self._month_len(self.year, self.month)

    # ── 点击 ──

    def _click(self, pos):
        # H3: 内核下拉展开项绘制于功能栏上方(超出 bottom_rect), 必须先于此处理
        if getattr(self, 'kernel_open', False):
            r = self.bottom_rect
            gy = r.y + 46
            for i, (k, label) in enumerate((('B', '合成(内核B)'), ('A', '真实大气(内核A)'))):
                item = pygame.Rect(r.right - 170, gy - 24 - (2 - i) * 26, 150, 24)
                if item.collidepoint(pos):
                    self.kernel = k
                    self.gen_msg = f'模拟内核: {label}'
                    self.kernel_open = False
                    return
        if self.bottom_rect.collidepoint(pos):
            self._click_bottom(pos)
            return

    def _randomize_seed(self) -> None:
        """种子语义: 0~10^10 为固定种子, -1 表示随机(每次取新随机种子)。"""
        self.seed = int(random.random() * (10 ** 10))
        self.gen_state = 'ready'
        self.gen_msg = f'已更换随机种子 {self.seed}(重新生成以生效)'

    def _click_bottom(self, pos):
        r = self.bottom_rect
        # 行1: 时间轴按钮(从右往左: 倍率/▶▶/▶/◀/◀◀)
        bx = r.x + 20
        for label, cb in (('◀◀', lambda: self._advance(-4)),
                          ('◀', lambda: self._advance(-1)),
                          ('▶', lambda: self._advance(1)),
                          ('▶▶', lambda: self._advance(4))):
            b = pygame.Rect(bx, r.y + 6, 44, 28)
            if b.collidepoint(pos):
                cb()
                return
            bx += 50
        if pygame.Rect(bx + 4, r.y + 6, 60, 28).collidepoint(pos):
            self.speed = self.speed_opts[(self.speed_opts.index(self.speed) + 1)
                                         % len(self.speed_opts)]
            return
        if pygame.Rect(bx + 70, r.y + 6, 90, 28).collidepoint(pos):
            self.playing = not self.playing
            return
        # 行2: 功能按钮(图层上拉框 / 追踪台风上拉框 / 生成 / 设置 …)
        gy = r.y + 46
        gx = r.x + 20
        # 图层上拉框
        if pygame.Rect(gx, gy, 130, 30).collidepoint(pos):
            self._toggle_layer_menu(pygame.Rect(gx, gy, 130, 30))
            return
        # 追踪台风上拉框
        tx = gx + 140
        if pygame.Rect(tx, gy, 130, 30).collidepoint(pos):
            self._toggle_typhoon_menu(pygame.Rect(tx, gy, 130, 30))
            return
        # 生成设置
        if pygame.Rect(gx + 280, gy, 96, 30).collidepoint(pos):
            if self.gen_panel is None:
                self.gen_panel = GenPanel(self)
            else:
                self.gen_panel = None
            return
        if pygame.Rect(gx + 384, gy, 96, 30).collidepoint(pos):
            self._run_generation(None)
            return
        if pygame.Rect(gx + 488, gy, 96, 30).collidepoint(pos):
            self._randomize_seed()
            return
        if pygame.Rect(gx + 592, gy, 80, 30).collidepoint(pos):
            self.settings_panel = SettingsPanel(self)
            return
        if pygame.Rect(gx + 680, gy, 80, 30).collidepoint(pos):
            self.mode_panel = ModePanel(self.series, MODE_NAMES)
            return
        # 内核下拉(靠右, 互不重叠)
        kr = pygame.Rect(r.right - 170, gy, 150, 30)
        if kr.collidepoint(pos):
            self.kernel_open = not self.kernel_open
            return
        if self.kernel_open:
            for i, (k, label) in enumerate((('B', '合成(内核B)'), ('A', '真实大气(内核A)'))):
                item = pygame.Rect(r.right - 170, gy - 24 - (2 - i) * 26, 150, 24)
                if item.collidepoint(pos):
                    self.kernel = k
                    self.gen_msg = f'模拟内核: {label}'
                    self.kernel_open = False
                    return

    def _toggle_layer_menu(self, anchor):
        items = [(lid, name, T.LAYER_COLORS[i % len(T.LAYER_COLORS)])
                 for i, (lid, name, _key, _rdy) in enumerate(LAYERS)]
        if self.layer_menu is None:
            self.layer_menu = UpMenu(items, self._select_layer)
        self.layer_menu._selected = LAYERS[self.layer_index][0]
        self.layer_menu.open_above(anchor, self.sw, self.sh)
        self.typhoon_menu = None

    def _select_layer(self, lid):
        for i, (id_, *_ ) in enumerate(LAYERS):
            if id_ == lid:
                self.layer_index = i
                return

    def _toggle_typhoon_menu(self, anchor):
        from simulator.ui.main import _video_datetime
        dt = _video_datetime((self.year, self.month, self.day, self.hour))
        tys = self.view.active_typhoons(dt)
        if not tys:
            self.gen_msg = '暂无台风(请先开始模拟)'
            return
        items = [(t['id'], f"#{t.get('id')}  {t.get('st', '')}  {t.get('w', 0)}kt",
                  T.LAYER_COLORS[i % len(T.LAYER_COLORS)])
                 for i, t in enumerate(tys)]
        if self.typhoon_menu is None:
            self.typhoon_menu = UpMenu(items, self._select_track)
        self.typhoon_menu._selected = self.view.track_id
        self.typhoon_menu.open_above(anchor, self.sw, self.sh)
        self.layer_menu = None

    def _select_track(self, tid):
        self.view.track_id = tid
        try:
            from simulator.ui import main as UIM
            UIM.set_video_track(tid)
        except Exception:
            pass
        self.gen_msg = f'追踪台风 #{tid}'

    # ── 生成执行(需求3: 从界面参数运行完整管线)──

    def _run_generation(self, panel):
        """按界面参数(或默认模式 D)生成台风并模拟。
        panel: GenPanel 或 None(直接开始模拟, 用面板上次设置/默认)。"""
        try:
            self.gen_state = 'generating'
            self.gen_msg = '准备环境场…'
            pygame.display.flip()
            from simulator.env import build_library as BL
            from simulator.env import field_io as F
            from simulator.env import generator as EG
            from simulator.typhoons import gen as TG
            from simulator.typhoons import sim as TS

            # ── 读取参数(面板或默认)──
            name = no = None
            lat = lon = None
            t0 = None
            mode = 'D'
            seed = self.seed
            if panel is not None:
                name = panel.name.text.strip() or None
                no = panel.no.text.strip() or None
                mode = panel.mode
                if panel.lat.text.strip():
                    lat = _parse_latlon(panel.lat.text, True)
                    if lat is None or not (-90 <= lat <= 90):
                        self.gen_state = 'bad'
                        self.gen_msg = '纬度格式错误(如 7N / 7.5S / 7)'
                        return
                if panel.lon.text.strip():
                    lon = _parse_latlon(panel.lon.text, False)
                    if lon is None or not (0 <= lon <= 360):
                        self.gen_state = 'bad'
                        self.gen_msg = '经度格式错误(如 178E / 178.5W / 178)'
                        return
                if panel.time.text.strip():
                    try:
                        t0 = datetime.strptime(panel.time.text.strip(), '%Y-%m-%d %H')
                    except ValueError:
                        self.gen_state = 'bad'
                        self.gen_msg = '时间格式应为 YYYY-MM-DD HH'
                        return
                if panel.seed.text.strip():
                    try:
                        seed = int(panel.seed.text)
                    except ValueError:
                        self.gen_state = 'bad'
                        self.gen_msg = '种子应为整数'
                        return
                    # 种子语义: 0~10^10 固定, -1 或空 = 随机
                    if seed == -1 or seed == '':
                        seed = int(random.random() * (10 ** 10))
                    elif not (0 <= seed <= 10 ** 10):
                        self.gen_state = 'bad'
                        self.gen_msg = '种子应在 0~10^10, 或填 -1 表示随机'
                        return
                self.seed = seed
            if mode in ('C', 'B') and t0 is None:
                self.gen_state = 'bad'
                self.gen_msg = '模式 B/C 需要时间(YYYY-MM-DD HH)'
                return
            if mode in ('A', 'C') and (lat is None or lon is None):
                self.gen_state = 'bad'
                self.gen_msg = '模式 A/C 需要坐标(纬度/经度)'
                return

            # ── 环境场(内核B: 合成/模拟场 3 年窗口; 内核A: 真实大气 IFS/ERA5)──
            years = [y for m in self.series for y, _, _, _ in self.series[m]]
            # 全空回退默认(与 __init__ 口径一致: ys[0] if ys else 2000)
            base_year = t0.year if t0 is not None else (min(years) if years else 2000)
            months = [mo for m in self.series
                      for y, mo, _, _ in self.series[m] if y == base_year]
            base_month = t0.month if t0 is not None else (min(months) if months else 7)
            if self.kernel == 'A':
                # 内核A: 真实大气。优先 WRF 直接模拟(wrfdir, 由 WRF 管线生成),
                # 无 WRF 场时用 Open-Meteo IFS/ERA5 数据驱动(免注册, 逐日缓存)。
                from simulator.env.real_source import RealFieldAPI, available
                from simulator.env.wrf_fields import WrfFieldAPI
                wapi = WrfFieldAPI()
                track = wapi.load_track()
                track_ok = track and _track_covers(track, t0)
                if track_ok:
                    # ── WRF 直接模拟模式: 台风=涡旋追踪结果, 跳过生成/模拟 ──
                    self._apply_wrf_track(wapi, track, name, t0)
                    return
                if wapi.available() and not track:
                    # WRF 场已有但未追踪 → 立即补跑追踪(秒级), 有结果则直接用
                    print('[sim] 内核A: 补跑涡旋追踪…')
                    self.gen_msg = '内核A: 补跑涡旋追踪…'
                    pygame.display.flip()
                    self._run_track_sync(wapi, t0)
                    track2 = wapi.load_track()
                    if track2 and _track_covers(track2, t0):
                        self._apply_wrf_track(wapi, track2, name, t0)
                        return
                    print('[sim] 内核A: 追踪无结果, 改用 WRF 场数据驱动')
                    api = wapi
                elif wapi.available():
                    # WRF 场可用但既有 track 未覆盖 t0(或补跑追踪仍无覆盖)→
                    # 改用 WRF 场数据驱动生成,不误报"wrfdir 与网络均不可用"。
                    print('[sim] 内核A: WRF 场未覆盖目标时段, 改用 WRF 场数据驱动')
                    self.gen_msg = '内核A: WRF 场数据驱动…'
                    api = wapi
                elif available():
                    print('[sim] 内核A: 真实大气场(Open-Meteo IFS/ERA5, 免注册)…')
                    self.gen_msg = '内核A: 真实大气场(IFS/ERA5)…'
                    api = RealFieldAPI()
                else:
                    # ── 无任何 WRF/联网数据 → 后台启动完整 WRF 管线 ──
                    self._start_wrf_pipeline(t0, lat, lon, panel)
                    return
                pygame.display.flip()
            else:
                print(f"[sim] 生成环境场(从 {base_year}-{base_month:02d} 起 36 个月, "
                      f"seed={seed + 1000})…")
                self.gen_msg = f'生成环境场(从 {base_year}-{base_month:02d} 起)…'
                pygame.display.flip()
                BL.build_library()
                eg = EG.AnalogGenerator(seed=seed + 1000)
                eg.generate_monthly((base_year, base_month), 36)
                api = F.FieldAPI(generated_dir=eg.generated_dir)

            # ── 生成 ──
            print(f"[sim] 生成台风胚胎 (模式 {mode}, seed={seed + 2000})…")
            self.gen_msg = '生成台风胚胎…'
            pygame.display.flip()
            g = TG.Generator(api=api, seed=seed + 2000)
            if mode == 'D':
                # 模式 D: 月份窗口从 base_month 对齐到整窗。
                # GUI 环境场固定生成 36 个月(base_year 起), window_months=36
                # 覆盖整窗(跨年递增); 旧实现仅首年生成, 后续面板时间线空转。
                recs = g.generate('D', year=base_year, month=base_month,
                                  window_months=36)
            else:
                recs = g.generate(mode, coord=(lat, lon) if lat is not None and lon is not None else None,
                                  t0=t0, year=t0.year if t0 else base_year,
                                  month=t0.month if t0 else 7)
            # 编号仅对单系统生成生效;批量(模式 B/D)共用编号会互相覆盖 .dat(T3-11)
            if no and len(recs) == 1:
                recs[0]['no'] = no
            if name:
                for r in recs:
                    r['name'] = name
            # 输出统一: 与 headless 一致, records.json 与 .dat 同置于 run_{seed}/ 下
            run_dir = os.path.join(BASE, 'output', f'run_{seed}')
            os.makedirs(run_dir, exist_ok=True)
            g.save(os.path.join(run_dir, 'records.json'))

            # ── 模拟 → .dat ──
            self.gen_msg = f'模拟 {len(recs)} 个系统…'
            pygame.display.flip()
            dat_dir = os.path.join(run_dir, 'dat')
            print(f"[sim] 模拟 {len(recs)} 个系统 (seed={seed + 3000}) → {dat_dir}")
            sims = TS.simulate_records(recs, api, seed=seed + 3000, out_dir=dat_dir)

            # ── 显示: 每个系统最新状态进主视图 ──
            self.typhoons = []
            for s in sims:
                if not s.states:
                    continue
                st = s.states[-1]
                self.typhoons.append({
                    'id': s.uid, 'name': s.rec.get('name') or f"#{s.no}",
                    'lat': st['la'], 'lon': st['lo'], 'w': st['w'],
                    'stage': st['stage'], 'basin': s.basin, 'p': st['p'],
                    'r34': st.get('r34', 0), 'cat': 0,
                })
            n_dat = len([s for s in sims if s.states])
            # run.log: 记录 seed/内核/模式/时间窗口/生成与模拟台风数 (与 headless 同目录语义)
            # 日志写失败不否决已完成的生成/模拟(兜底,不影响产物落盘)
            try:
                _write_gui_run_log(
                    os.path.join(run_dir, 'run.log'), seed=seed, kernel=self.kernel,
                    mode=mode, base_year=base_year, base_month=base_month,
                    n_gen=len(recs), n_dat=n_dat)
            except Exception as ex:
                print(f"[sim] 写 run.log 失败(忽略): {ex}")
            # F10: 视频图层数据接入(界面模式此前从未设置上下文,画面显示空/合成)
            all_states = []
            for s in sims:
                for st in s.states:
                    st2 = dict(st)
                    st2['id'] = s.uid
                    all_states.append(st2)
            all_states.sort(key=lambda st: st.get('t', ''))
            from simulator.ui import main as UIM
            UIM.set_video_context(api, all_states,
                                  t0 if t0 is not None else datetime(base_year, 1, 1))
            self.gen_state = 'ok'
            self.gen_msg = (f"完成: {len(recs)} 系统(性质 "
                            f"{[r.get('nature', 'TD') for r in recs]}), "
                            f"{n_dat} 个 .dat → {dat_dir} · records/运行日志 {run_dir}")
            self.gen_panel = None
        except Exception as ex:
            self.gen_state = 'bad'
            self.gen_msg = f'生成失败: {ex}'

    # ── 内核A: WRF 直接模拟辅助 ──

    def _apply_wrf_track(self, wapi, track, name=None, t0=None):
        """把 WRF 涡旋追踪结果作为台风接入视图与视频上下文。"""
        print(f'[sim] 内核A: WRF 直接模拟(追踪 {len(track)} 个报点)…')
        self.gen_msg = '内核A: WRF 直接模拟(涡旋追踪)…'
        pygame.display.flip()
        self.typhoons = []
        for st in track:
            self.typhoons.append({
                'id': 1, 'name': name or 'WRF-TC',
                'lat': st['la'], 'lon': st['lo'], 'w': st['w'],
                'stage': st.get('stage', '增强'), 'basin': 'WP',
                'p': st['p'], 'r34': st.get('r34', 0), 'cat': 0})
        if self.typhoons:
            self.typhoons = [self.typhoons[-1]]
        all_states = []
        for st in track:
            st2 = dict(st)
            st2['id'] = 1
            all_states.append(st2)
        all_states.sort(key=lambda s: s.get('t', ''))
        from simulator.ui import main as UIM
        t0f = None
        if t0 is not None:
            t0f = t0
        elif all_states:
            t0f = datetime(int(all_states[0]['t'][:4]), int(all_states[0]['t'][4:6]),
                           int(all_states[0]['t'][6:8]))
        UIM.set_video_context(wapi, all_states, t0f)
        self.gen_state = 'ok'
        self.gen_msg = (f'内核A: WRF 直接模拟完成 — '
                        f'{len(track)} 个报点(wrfdir/track.json)')
        self.gen_panel = None

    def _run_track_sync(self, wapi, t0):
        """对已有 wrfdir 场补跑涡旋追踪(秒级)。"""
        import glob as _g
        wrfout_dir = os.path.join(BASE, 'wrf', 'case', 'wrfout')
        if not os.path.isdir(wrfout_dir):
            return
        from simulator.wrf import track_vortex as TV
        files = _g.glob(os.path.join(wrfout_dir, 'wrfout_*'))
        if not files:
            return
        t_first = TV._parse_fname(os.path.basename(files[0]))
        t_last = TV._parse_fname(os.path.basename(files[-1]))
        if t_first is None or t_last is None:
            return
        days = max(1, (t_last - t_first).days + 1)
        pts = TV.track_wrfout(wrfout_dir, t_first, days)
        if pts:
            TV.save_track(pts, os.path.join(wapi.wrf_dir, 'track.json'))

    def _start_wrf_pipeline(self, t0, lat, lon, panel):
        """后台启动完整 WRF 管线(下载 ERA5→编译检查→跑 WRF→追踪)。
        完成后 _poll_wrf_pipeline 自动载入结果。"""
        wrf_dir = os.path.join(BASE, 'wrf')
        ps1 = os.path.join(wrf_dir, 'run_wrf.ps1')
        logp = os.path.join(wrf_dir, 'pipeline.log')
        days = 10
        vmax = 30.0
        rmw = 30.0
        hist = 12
        if panel is not None:
            try:
                days = max(1, int(panel.wrf_days.text.strip() or '10'))
            except ValueError:
                pass
            try:
                vmax = max(5.0, float(panel.bogus_vmax.text.strip() or '30'))
            except ValueError:
                pass
            try:
                rmw = max(5.0, float(panel.bogus_rmw.text.strip() or '30'))
            except ValueError:
                pass
            try:
                hist = max(1, min(60, int(panel.hist_interval.text.strip() or '12')))
            except ValueError:
                pass
        lat0 = lat if lat is not None else 25.0
        lon0 = lon if lon is not None else 150.0
        cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1,
               '-Start', t0.strftime('%Y-%m-%d') if t0 else '',
               '-Days', str(days),
               '-BogusVmax', str(vmax), '-BogusLat', str(lat0),
               '-BogusLon', str(lon0), '-BogusRmw', str(rmw),
               '-HistInterval', str(hist)]
        print('[sim] 启动 WRF 管线(后台):', ' '.join(cmd))
        print('[sim] 日志实时输出到本窗口(关闭本窗口会中断管线)…')
        self.gen_state = 'generating'
        self.gen_msg = (f'WRF 管线运行中(下载 ERA5→WRF→追踪, 约 {days} 天域, '
                        f'预计数小时; 详细进度见 cmd 窗口)…')
        pygame.display.flip()
        try:
            # 继承控制台 → 子进程输出实时显示在 cmd 窗口
            self.wrf_proc = subprocess.Popen(
                cmd, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) & 0)
            self.wrf_poll_t = time.time()
        except Exception as ex:
            self.gen_state = 'bad'
            self.gen_msg = f'WRF 管线启动失败: {ex}'

    def _poll_wrf_pipeline(self):
        """主循环轮询: 管线完成且 track.json 就绪 → 自动载入。"""
        if self.wrf_proc is None:
            return
        proc = self.wrf_proc
        if proc.poll() is None:
            if time.time() - self.wrf_poll_t > 30:
                self.wrf_poll_t = time.time()
                from simulator.env.wrf_fields import WrfFieldAPI
                n = 0
                wdir = WrfFieldAPI().wrf_dir
                if os.path.isdir(wdir):
                    n = len([f for f in os.listdir(wdir) if f.endswith('.npz')])
                self.gen_msg = (f'WRF 管线运行中({n} 天场已生成; '
                                f'进度 simulator/wrf/pipeline.log)…')
            return
        self.wrf_proc = None
        from simulator.env.wrf_fields import WrfFieldAPI
        wapi = WrfFieldAPI()
        track = wapi.load_track()
        if track:
            self._apply_wrf_track(wapi, track, None)
            return
        # 管线失败或未产生追踪
        logp = os.path.join(BASE, 'wrf', 'pipeline.log')
        tail = ''
        try:
            with open(logp, encoding='utf-8', errors='replace') as f:
                tail = '…'.join(f.read().splitlines()[-3:])
        except Exception:
            pass
        self.gen_state = 'bad'
        self.gen_msg = f'WRF 管线结束但无追踪结果(exit={proc.returncode}); {tail}'

    # ── 绘制 ──

    def draw(self):
        s = self.screen
        s.fill(T.BG)
        # 先计算状态框是否显示, 同步 D01 标题右移量(避免首帧标题被遮挡)
        from simulator.ui.main import _video_datetime
        dt_sb = _video_datetime((self.year, self.month, self.day, self.hour))
        if self.ui_hidden or self.view.tracked(dt_sb) is None:
            self.view.title_inset = 0
        else:
            self.view.title_inset = max(0, 270 + 12 - (self.view_rect.x + 50))
        if self.ui_hidden:
            # 画面模式(F): 仅渲染显示界面(图层),不画 UI 面板(T3-10)
            self.view.draw(s, LAYERS[self.layer_index][0],
                           (self.year, self.month, self.day, self.hour),
                           opacity=self.layer_opacity)
            return
        self.view.draw(s, LAYERS[self.layer_index][0],
                       (self.year, self.month, self.day, self.hour),
                       opacity=self.layer_opacity)
        # 左上角台风状态信息框(显示面内唯一的信息元素)
        self._draw_status_box(s)
        self._draw_bottom(s)
        if self.mode_panel:
            self.mode_panel.draw(s)
        if self.gen_panel:
            self.gen_panel.draw(s)
        if self.settings_panel:
            self.settings_panel.draw(s)
        if self.layer_menu:
            self.layer_menu.draw(s)
        if self.typhoon_menu:
            self.typhoon_menu.draw(s)

    def _draw_status_box(self, s):
        """左上角台风目前状态信息框(参考 typhoon_sim 信息框位置)。
        M3: 状态框存在时 D01 标题右移避开(display.title_inset, draw 中先行计算)。"""
        from simulator.ui.main import _video_datetime
        dt = _video_datetime((self.year, self.month, self.day, self.hour))
        ty = self.view.tracked(dt)
        if ty is None:
            return
        box = pygame.Rect(10, 10, 260, 118)
        # 名称行
        tid = ty.get('id', '?')
        st_tag = ty.get('st', '')
        name = next((t.get('name', '') for t in self.typhoons
                     if t.get('id') == tid), '') or f"#{tid}"
        # 半透明深色底 + 圆角边框(不遮挡下层信息, 参考图左上角信息框)
        panel = pygame.Surface((box.w, box.h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (20, 26, 40, 200), (0, 0, box.w, box.h),
                         border_radius=8)
        s.blit(panel, box.topleft)
        pygame.draw.rect(s, (70, 130, 220), box, 1, border_radius=8)
        nm = T.ellipsis_text(18, f"台风 {name}  {st_tag}", T.TEXT, max_w=box.w - 16)
        s.blit(nm, (box.x + 10, box.y + 6))
        lat, lon = ty.get('la', 0), ty.get('lo', 0)
        la_s = f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}"
        lo_s = f"{abs(lon) if lon <= 180 else 360 - lon:.1f}°{'E' if lon <= 180 else 'W'}"
        f2 = T.font(15)
        rows = [
            f"位置: {la_s}  {lo_s}",
            f"风速: {ty.get('w', 0)} kt    气压: {ty.get('p', '-')} hPa",
            f"阶段: {ty.get('stage', '-')}    R34: {ty.get('r34', '-')} nm",
        ]
        y = box.y + 36
        for line in rows:
            ts = f2.render(line, True, T.TEXT_DIM)
            s.blit(ts, (box.x + 10, y))
            y += 24

    def _draw_bottom(self, s):
        r = self.bottom_rect
        T.draw_panel(s, r, radius=0)
        # 行1: 时间轴 + 速度 + 播放 + 时间显示 + 状态消息
        bx = r.x + 20
        for label, accent in (('◀◀', False), ('◀', False),
                              ('▶', True), ('▶▶', False)):
            b = pygame.Rect(bx, r.y + 6, 44, 28)
            T.draw_button(s, b, label, accent=accent, size=16)
            bx += 50
        sp = pygame.Rect(bx + 4, r.y + 6, 60, 28)
        T.draw_button(s, sp, f"x{self.speed}", accent=False, size=16)
        pb = pygame.Rect(bx + 70, r.y + 6, 90, 28)
        T.draw_button(s, pb, "暂停" if self.playing else "播放", accent=self.playing, size=16)
        # 时间显示 + 状态消息(UI 提示词: 消息宽限截断)
        s.blit(T.text(16, f"{self.year}-{self.month:02d}-{self.day:02d} {self.hour:02d}:00",
                      T.TEXT_DIM), (bx + 170, r.y + 12))
        msg_x = bx + 330
        msg_w = max(60, r.w - 470 - msg_x - 20)
        s.blit(T.ellipsis_text(14, self.gen_msg, T.TEXT_DIM, max_w=msg_w),
               (msg_x, r.y + 14))
        # 行2: 功能按钮(图层/追踪/生成/设置/模态) + 内核下拉
        gy = r.y + 46
        gx = r.x + 20
        lid, lname, lkey, _ = LAYERS[self.layer_index]
        # M8: draw_button 内置省略号截断(见 theme.py), 按钮文字不溢出
        T.draw_button(s, pygame.Rect(gx, gy, 130, 30), f"图层: {lname}",
                      accent=self.layer_menu is not None, size=14)
        track_txt = "追踪: 自动"
        if self.view.track_id is not None:
            track_txt = f"追踪: #{self.view.track_id}"
        T.draw_button(s, pygame.Rect(gx + 140, gy, 130, 30), track_txt,
                      accent=self.typhoon_menu is not None, size=14)
        T.draw_button(s, pygame.Rect(gx + 280, gy, 96, 30), "生成设置", size=15)
        T.draw_button(s, pygame.Rect(gx + 384, gy, 96, 30), "开始模拟", accent=True, size=16)
        T.draw_button(s, pygame.Rect(gx + 488, gy, 96, 30), "重新随机", size=15)
        T.draw_button(s, pygame.Rect(gx + 592, gy, 80, 30), "设置", size=15)
        T.draw_button(s, pygame.Rect(gx + 680, gy, 80, 30), "模态", size=15)
        # 内核下拉(靠右,互不重叠; 展开项向上弹出)
        kr = pygame.Rect(r.right - 170, gy, 150, 30)
        klab = "合成(内核B)" if self.kernel == 'B' else "真实大气(内核A)"
        T.draw_button(s, kr, klab, accent=False, enabled=True, size=14)
        if self.kernel_open:
            for i, (k, label) in enumerate((('B', '合成(内核B)'), ('A', '真实大气(内核A)'))):
                item = pygame.Rect(r.right - 170, gy - 24 - (2 - i) * 26, 150, 24)
                T.draw_button(s, item, label, enabled=True, size=14)


# ════════════════════ 生成设置浮层(需求3: 指定名称/编号/坐标/时间/种子/模式)════════════════════

def _parse_latlon(text: str, is_lat: bool) -> Optional[float]:
    """解析坐标: 支持 '7N' '7.5S' '178E' '178.5W' 或纯数字。
    纬度 N/S 后缀; 经度 E/W 后缀(W 换算为 360-lon)。返回 None 表示格式错误。"""
    t = text.strip().upper().replace(' ', '').replace(',', '')
    m = re.match(r'^([+-]?\d+(?:\.\d+)?)([NSEW])?$', t)
    if not m:
        return None
    v = float(m.group(1))
    suf = m.group(2)
    if is_lat:
        if suf == 'S':
            v = -v
        return v
    if suf == 'W':
        v = 360.0 - v
    return v % 360.0


class InputBox:
    """轻量文本输入框(点击激活, TEXTINPUT/退格/方向键/IME, Enter 失活)。"""

    def __init__(self, rect, text=""):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.active = False
        self.cursor = len(text)

    def handle(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if self.rect.collidepoint(ev.pos):
                self.active = True
                self.cursor = len(self.text)
                try:
                    pygame.key.start_text_input()
                    pygame.key.set_text_input_rect(self.rect)
                except Exception:
                    pass
                return True
            self.active = False
            return False
        if not self.active:
            return False
        if ev.type == pygame.TEXTINPUT and ev.text:
            self.text = self.text[:self.cursor] + ev.text + self.text[self.cursor:]
            self.cursor += len(ev.text)
            return True
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_BACKSPACE and self.cursor > 0:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
                return True
            if ev.key == pygame.K_DELETE and self.cursor < len(self.text):
                self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
                return True
            if ev.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
                return True
            if ev.key == pygame.K_RIGHT:
                self.cursor = min(len(self.text), self.cursor + 1)
                return True
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                self.active = False
                try:
                    pygame.key.stop_text_input()
                except Exception:
                    pass
                return True
            if ev.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                try:
                    clip = pygame.scrap.get(pygame.SCRAP_TEXT)
                    if clip:
                        s = clip.decode("utf-8", errors="ignore")
                        s = "".join(c for c in s if c.isprintable())
                        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
                        self.cursor += len(s)
                except Exception:
                    pass
                return True
        return False

    def draw(self, surf):
        pygame.draw.rect(surf, (42, 46, 62), self.rect, border_radius=6)
        border = (110, 140, 190) if self.active else (66, 72, 92)
        pygame.draw.rect(surf, border, self.rect, 1, border_radius=6)
        old = surf.get_clip()
        surf.set_clip(self.rect)
        f = T.font(16)
        tx = self.rect.x + 8
        ty = self.rect.y + (self.rect.h - f.get_height()) // 2 + 1
        surf.blit(f.render(self.text, True, T.TEXT), (tx, ty))
        if self.active and pygame.time.get_ticks() % 1000 < 500:
            cw = f.size(self.text[:self.cursor])[0]
            cx = tx + cw
            if cx < self.rect.right - 4:
                pygame.draw.line(surf, T.TEXT, (cx, ty), (cx, self.rect.bottom - 6), 2)
        surf.set_clip(old)


class GenPanel:
    """生成设置浮层(独立实现, 不依赖惰性加载的 FloatPanel):
    台风名/低压编号/模式/坐标/时间/种子。"""

    MODE_DESC = {
        'A': 'A: 决定生成点(位置±3°, 时间按统计分布随机)',
        'B': 'B: 决定生成时间(时间±48h, 位置在盆地带内按 GPI 权重随机)',
        'C': 'C: 两者同时(坐标+时间都固定)',
        'D': 'D: 完全随机(整季无人值守)',
    }

    def __init__(self, app):
        self.app = app
        self.title = '生成设置'
        self.rect = pygame.Rect(0, 0, 640, 580)
        self.rect.centerx = app.sw // 2
        self.rect.y = max(40, app.sh // 2 - 290)
        self.dragging = False
        self.offset = (0, 0)
        r = self.rect
        fx = r.x + 220
        fw = r.w - 234
        self.name = InputBox((fx, r.y + 44, fw, 30), '')
        self.no = InputBox((fx, r.y + 86, fw, 30), '')
        self.lat = InputBox((fx, r.y + 128, 130, 30), '')
        self.lon = InputBox((fx + 230, r.y + 128, 130, 30), '')
        self.time = InputBox((fx, r.y + 170, fw, 30), '')
        self.seed = InputBox((fx, r.y + 212, 130, 30), '')
        self.mode = 'C'
        self.mode_btns = []
        # 内核A(WRF): 涡旋注入与模拟参数
        self.bogus_vmax = InputBox((fx, r.y + 340, 130, 30), '30')
        self.bogus_rmw = InputBox((fx + 230, r.y + 340, 130, 30), '30')
        self.wrf_days = InputBox((fx, r.y + 384, 130, 30), '10')
        self.hist_interval = InputBox((fx + 230, r.y + 384, 130, 30), '12')
        self.inputs = [self.name, self.no, self.lat, self.lon, self.time, self.seed,
                       self.bogus_vmax, self.bogus_rmw, self.wrf_days,
                       self.hist_interval]
        now = datetime(app.year, app.month, app.day, app.hour)
        self.time.text = now.strftime('%Y-%m-%d %H')
        self.time.cursor = len(self.time.text)
        # 种子默认 -1 = 随机(用户规则: 0~10^10 固定, -1 随机)
        self.seed.text = '-1'
        self.seed.cursor = len(self.seed.text)

    def handle_event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not self.rect.collidepoint(e.pos):
                return False
            # 标题栏拖拽
            if e.pos[1] < self.rect.y + 34:
                self.dragging = True
                self.offset = (e.pos[0] - self.rect.x, e.pos[1] - self.rect.y)
                return True
            for k, rect in self.mode_btns:
                if rect.collidepoint(e.pos):
                    self.mode = k
                    return True
            if self._btn_run().collidepoint(e.pos):
                self.app._run_generation(self)
                return True
            if self._btn_cancel().collidepoint(e.pos):
                self.app.gen_panel = None
                return True
            for inp in self.inputs:
                if inp.rect.collidepoint(e.pos):
                    # 焦点独占: 点击 B 时释放 A,防文本进错框(T3-12)
                    for other in self.inputs:
                        if other is not inp:
                            other.active = False
                    inp.handle(e)
                    return True
            return True
        if e.type == pygame.MOUSEMOTION and self.dragging:
            # M4: 拖拽时同步平移全部输入框(否则输入框停留在原位)
            dx = e.pos[0] - self.offset[0] - self.rect.x
            dy = e.pos[1] - self.offset[1] - self.rect.y
            self.rect.x = e.pos[0] - self.offset[0]
            self.rect.y = e.pos[1] - self.offset[1]
            for inp in self.inputs:
                inp.rect.x += dx
                inp.rect.y += dy
            return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.dragging = False
        for inp in self.inputs:
            if inp.handle(e):
                return True
        return False

    def _btn_run(self):
        r = self.rect
        return pygame.Rect(r.x + r.w // 2 - 180, r.y + r.h - 52, 150, 36)

    def _btn_cancel(self):
        r = self.rect
        return pygame.Rect(r.x + r.w // 2 + 30, r.y + r.h - 52, 150, 36)

    def draw(self, surface):
        ov = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 120))
        surface.blit(ov, (0, 0))
        T.draw_panel(surface, self.rect, radius=10)
        pygame.draw.rect(surface, (40, 44, 55),
                         (self.rect.x, self.rect.y, self.rect.w, 34), border_radius=8)
        ts = T.text(18, self.title, T.ACCENT)
        surface.blit(ts, (self.rect.x + 12, self.rect.y + 6))
        r = self.rect
        # 标签带独立 x(修正: 原实现全部画在 x+14, 纬/经标签重叠)
        label_x = {'台风名': r.x + 14, '低压编号': r.x + 14,
                   '纬度': r.x + 14,
                   '时间 (YYYY-MM-DD HH)': r.x + 14, '种子': r.x + 14}
        labels = [('台风名', self.name), ('低压编号', self.no),
                  ('纬度', self.lat), ('经度', self.lon),
                  ('时间 (YYYY-MM-DD HH)', self.time), ('种子', self.seed)]
        for lab, inp in labels:
            if lab == '经度':
                continue      # 单独绘制(紧贴经度输入框)
            surface.blit(T.text(16, lab, T.TEXT), (label_x[lab], inp.rect.y + 5))
            inp.draw(surface)
        # 经度标签紧贴经度输入框左侧(不压纬度输入框)
        lon_lab = T.text(16, '经度', T.TEXT)
        surface.blit(lon_lab, (self.lon.rect.x - 12 - lon_lab.get_width(),
                               self.lon.rect.y + 5))
        self.lon.draw(surface)
        my = r.y + 256
        surface.blit(T.text(16, '生成模式', T.TEXT), (r.x + 14, my + 5))
        self.mode_btns = []
        for i, k in enumerate(('A', 'B', 'C', 'D')):
            b = pygame.Rect(r.x + 110 + i * 90, my, 80, 30)
            self.mode_btns.append((k, b))
            T.draw_button(surface, b, k, accent=(k == self.mode), size=16)
        surface.blit(T.text(13, self.MODE_DESC.get(self.mode, ''), T.TEXT_DIM),
                     (r.x + 14, my + 38))
        tip = ('提示: 坐标 SST<25°C 时以 EX/SS 性质起始, 自然演化不强行转 TC; '
               '|纬度|≤60°(全球网格限制)')
        # M5: 提示文本与下方 "WRF 参数" 标题拉开间距(原 my+62 与 r.y+310 重叠)
        surface.blit(T.ellipsis_text(13, tip, T.TEXT_DIM, max_w=r.w - 28),
                     (r.x + 14, my + 62))
        # 内核A(WRF)参数区(M5: 标题下移到 r.y+322, 避开提示文本)
        surface.blit(T.text(16, 'WRF 参数(内核A)', T.ACCENT), (r.x + 14, r.y + 322))
        for lab, inp in (('初始强度 (kt)', self.bogus_vmax), ('RMW (km)', self.bogus_rmw)):
            surface.blit(T.text(16, lab, T.TEXT), (inp.rect.x - 12 - T.text(16, lab, T.TEXT).get_width(),
                                                   inp.rect.y + 5))
            inp.draw(surface)
        for lab, inp in (('模拟天数', self.wrf_days), ('输出步长 (分)', self.hist_interval)):
            surface.blit(T.text(16, lab, T.TEXT), (inp.rect.x - 12 - T.text(16, lab, T.TEXT).get_width(),
                                                   inp.rect.y + 5))
            inp.draw(surface)
        surface.blit(T.ellipsis_text(
            13, '内核A: 无 WRF 数据时自动跑管线(下载 ERA5→WRF→追踪, 数小时, 后台执行); '
                '默认注入 30kt 初始涡旋, 历史台风可勾 -NoBogus; 输出步长 1-60 分钟(默认 12)',
            T.TEXT_DIM, max_w=r.w - 28), (r.x + 14, r.y + 436))
        T.draw_button(surface, self._btn_run(), '开始生成', accent=True, size=16)
        T.draw_button(surface, self._btn_cancel(), '取消', size=16)


# ════════════════════ 设置面板(S 键)════════════════════

class SettingsPanel:
    """设置: 区域图(中心/跨度, 持久化) / 经纬线 / 红外色阶 / 单站坐标。"""

    CM_LABELS = {'CA': 'CA 增强', 'CC': 'CC 彩色', 'OTT': 'OTT 增强',
                 'RAMMB': 'RAMMB', 'RBTOP': 'RBTOP', 'BD': 'BD 增强',
                 'BW': 'BW 灰度'}

    def __init__(self, app):
        self.app = app
        self.title = '设置'
        self.rect = pygame.Rect(0, 0, 520, 420)
        self.rect.centerx = app.sw // 2
        self.rect.y = max(60, app.sh // 2 - 210)
        self._cm_names = None
        self.inputs = []
        # 区域图参数(纬度/经度/纬跨/经跨) + 单站坐标
        from simulator.ui import main as UIM
        reg = app.view.region
        spec = [
            ('区域中心纬度', str(reg['lat']), 24),
            ('区域中心经度', str(reg['lon']), 24),
            ('区域纬度跨度', str(reg['lat_span']), 24),
            ('区域经度跨度', str(reg['lon_span']), 24),
            ('单站纬度(空=台风中心)', str(UIM.UI_SETTINGS.get('station_lat', '')), 24),
            ('单站经度(空=台风中心)', str(UIM.UI_SETTINGS.get('station_lon', '')), 24),
        ]
        fx = self.rect.x + 220
        fw = self.rect.w - 234
        y0 = self.rect.y + 44
        for i, (lab, txt, h) in enumerate(spec):
            self.inputs.append(InputBox((fx, y0 + i * 34, fw, h), txt))
        self.station_inputs = self.inputs[4:6]

    def _colormaps(self) -> list:
        if self._cm_names is None:
            try:
                from simulator.clouds.render import colormap_names
                self._cm_names = colormap_names()
            except Exception:
                self._cm_names = ['CA']
        return self._cm_names

    def handle_event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if not self.rect.collidepoint(e.pos):
                self.app.settings_panel = None
                return True
            from simulator.ui import main as UIM
            # 经纬线
            if pygame.Rect(self.rect.x + 30, self.rect.y + 250, 220, 24).collidepoint(e.pos):
                UIM.set_setting('show_grid', not UIM.UI_SETTINGS.get('show_grid', True))
                return True
            # 色阶循环
            if pygame.Rect(self.rect.x + 30, self.rect.y + 288, 240, 26).collidepoint(e.pos):
                names = self._colormaps()
                cur = UIM.UI_SETTINGS.get('colormap', 'CA')
                nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
                UIM.set_setting('colormap', nxt)
                return True
            # 应用区域设置
            if pygame.Rect(self.rect.x + 30, self.rect.y + 344, 140, 30).collidepoint(e.pos):
                self._apply_region()
                return True
            # 输入框
            for inp in self.inputs:
                if inp.rect.collidepoint(e.pos):
                    for other in self.inputs:
                        if other is not inp:
                            other.active = False
                    inp.handle(e)
                    return True
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.settings_panel = None
            return True
        for inp in self.inputs:
            if inp.handle(e):
                return True
        return False

    def _apply_region(self):
        try:
            lat = float(self.inputs[0].text.strip())
            lon = float(self.inputs[1].text.strip())
            lat_span = float(self.inputs[2].text.strip())
            lon_span = float(self.inputs[3].text.strip())
            self.app.view.set_region(lat=lat, lon=lon,
                                     lat_span=lat_span, lon_span=lon_span)
            from simulator.ui import main as UIM
            UIM.set_setting('region_lat', lat)
            UIM.set_setting('region_lon', lon)
            UIM.set_setting('region_lat_span', lat_span)
            UIM.set_setting('region_lon_span', lon_span)
            self.app.gen_msg = (f'区域图: 中心 {lat:.1f}°/{lon:.1f}°, '
                                f'跨度 {lat_span:.0f}°×{lon_span:.0f}°')
        except ValueError:
            self.app.gen_msg = '区域设置: 数字格式错误'
        # 单站坐标
        try:
            from simulator.ui import main as UIM
            slat_t = self.station_inputs[0].text.strip()
            slon_t = self.station_inputs[1].text.strip()
            if slat_t and slon_t:
                slat = float(slat_t)
                slon = float(slon_t)
                UIM.set_video_station((slat, slon))
                UIM.set_setting('station_lat', slat)
                UIM.set_setting('station_lon', slon)
                self.app.gen_msg += ' · 单站已设'
            elif not slat_t and not slon_t:
                UIM.set_video_station(None)
                UIM.set_setting('station_lat', '')
                UIM.set_setting('station_lon', '')
        except ValueError:
            self.app.gen_msg += ' · 单站格式错误'

    def draw(self, surface):
        from simulator.ui import main as UIM
        ov = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 120))
        surface.blit(ov, (0, 0))
        T.draw_panel(surface, self.rect, radius=10)
        pygame.draw.rect(surface, (40, 44, 55),
                         (self.rect.x, self.rect.y, self.rect.w, 34), border_radius=8)
        ts = T.text(18, self.title, T.ACCENT)
        surface.blit(ts, (self.rect.x + 12, self.rect.y + 6))
        r = self.rect
        # 区域图设置(参考图: 左区域可拖动/设置)
        surface.blit(T.text(14, '区域图(左侧 D01)', T.TEXT_DIM), (r.x + 14, r.y + 40))
        for i, inp in enumerate(self.inputs[:4]):
            surface.blit(T.text(16, ['中心纬度', '中心经度', '纬度跨度', '经度跨度'][i],
                                 T.TEXT), (r.x + 14, inp.rect.y + 4))
            inp.draw(surface)
        surface.blit(T.text(13, '也可在左侧区域图按住拖动平移、滚轮缩放(即时生效)',
                            T.TEXT_DIM), (r.x + 14, r.y + 182))
        surface.blit(T.text(14, '单站坐标(单站时间序列图层)', T.TEXT_DIM),
                     (r.x + 14, r.y + 206))
        for i, inp in enumerate(self.station_inputs):
            surface.blit(T.text(16, '纬度' if i == 0 else '经度', T.TEXT),
                         (r.x + 14, inp.rect.y + 4))
            inp.draw(surface)
        show = UIM.UI_SETTINGS.get('show_grid', True)
        T.draw_checkbox(surface, r.x + 30, r.y + 250, show)
        surface.blit(T.text(16, '显示经纬线', T.TEXT), (r.x + 58, r.y + 252))
        surface.blit(T.text(14, '红外色阶', T.TEXT_DIM), (r.x + 30, r.y + 274))
        cur = UIM.UI_SETTINGS.get('colormap', 'CA')
        T.draw_button(surface, pygame.Rect(r.x + 30, r.y + 288, 240, 26),
                      f'{cur}  {self.CM_LABELS.get(cur, "")}  (点击切换)', accent=True, size=14)
        # M6: 提示文本在按钮下方(不再被按钮覆盖)
        surface.blit(T.text(13, '循环切换 CA→CC→OTT→RAMMB→RBTOP→BD→BW(云图层); ESC 关闭',
                            T.TEXT_DIM), (r.x + 30, r.y + 322))
        T.draw_button(surface, pygame.Rect(r.x + 30, r.y + 344, 140, 30),
                      '应用区域设置', accent=True, size=15)
        surface.blit(T.text(13, '设置自动保存(settings.json)', T.TEXT_DIM),
                     (r.x + 300, r.y + 352))


def _write_gui_run_log(path: str, *, seed: int, kernel: str, mode: str,
                       base_year: int, base_month: int,
                       n_gen: int, n_dat: int) -> str:
    """GUI 路径 run.log: 记录 seed/内核/模式/时间窗口/生成与模拟台风数。
    与 headless(run_pipeline 的 run.log)同目录语义, 便于对照与回放定位。"""
    lines = [
        f"=== GUI 生成运行日志 ===",
        f"seed={seed}",
        f"kernel={kernel}",
        f"mode={mode}",
        f"time_window={base_year}-{base_month:02d}",
        f"generated={n_gen}",
        f"simulated={n_dat}",
        f"finished={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


# ════════════════════ F11 端到端管线 ════════════════════

def run_pipeline(seed: int = 1, years: int = 3, out_root: Optional[str] = None,
                 render_frames: bool = True, mode: str = 'D',
                 coord: Optional[Tuple[float, float]] = None,
                 t0: Optional[datetime] = None,
                 name: Optional[str] = None, no: Optional[str] = None,
                 render_all: bool = False, kernel: str = 'B',
                 frame_size: Tuple[int, int] = (2560, 1600)) -> dict:
    """一键完整链路: 模态 → 环境场 → 生成 → 模拟 → 演示帧。
    每步产物校验(缺失报错,不静默跳过);日志写 run.log;seed 可复现。
    mode: D 全随机 / A 定坐标 / B 定时间 / C 坐标+时间。
    kernel: 'B' 合成架空(默认) / 'A' 真实大气(IFS/ERA5, 免注册, 需联网)。
    render_all: 渲染全部 17 类图层到 frames/(默认 2560x1600)。"""
    import logging
    out_root = out_root or os.path.join(BASE, 'output', f'run_{seed}')
    os.makedirs(out_root, exist_ok=True)
    logp = os.path.join(out_root, 'run.log')
    logging.basicConfig(filename=logp, level=logging.INFO,
                        format='%(asctime)s %(message)s', force=True)
    log = logging.getLogger('pipeline')
    t_start = time.perf_counter()

    def _check(cond, msg):
        if not cond:
            raise RuntimeError(f"[F11] {msg} — 上一步产物缺失/为空,不允许静默跳过")
        log.info('OK %s (%.1fs)', msg, time.perf_counter() - t_start)

    from simulator.ui.main import set_video_context, render_layer
    # a. 模态(阶段1)
    from simulator import modes as M
    mg = M.ModeGenerator(seed=seed, cont_years=2)
    series = mg.generate(total_years=years)
    M.save_output(series, mg.seed, mg.sub_seeds, mg.retry_counts,
                  mg.low_confidence, out_root)
    _check(bool(series.get('nino34')), 'modes 序列')
    log.info('  seed=%d score=%.3f low_conf=%s', mg.seed, mg.best_score,
             mg.low_confidence or '无')
    start_y = min(y for m in series for y, _, _, _ in series[m])
    start_mo = min(mo for m in series for y, mo, _, _ in series[m] if y == start_y)

    # b. 环境场(阶段2)
    from simulator.env import build_library as BL
    from simulator.env import field_io as F
    from simulator.env import generator as EG
    if kernel == 'A':
        from simulator.env.real_source import RealFieldAPI, available
        from simulator.env.wrf_fields import WrfFieldAPI
        wapi = WrfFieldAPI()
        if wapi.available():
            api = wapi
            log.info('  内核A: WRF 输出场(simulator/env/wrfdir)')
        elif available():
            api = RealFieldAPI()
            log.info('  内核A: 真实大气场(IFS/ERA5, 逐日缓存 %s)',
                     getattr(api, 'cache_dir', '?'))
        else:
            raise RuntimeError('内核A需要数据: 先跑 simulator/wrf/run_wrf.ps1, '
                               '或联网(Open-Meteo IFS/ERA5 免注册)')
    else:
        BL.build_library()
        eg = EG.AnalogGenerator(seed=seed + 1000)
        eg.generate_monthly((start_y, start_mo), years * 12)
        _check(bool(eg.log), '环境场生成(analog 日志非空)')
        log.info('  生成 %d 个月场,匹配 %d 条', years * 12, len(eg.log))
        api = F.FieldAPI(generated_dir=eg.generated_dir)

    # c. 台风生成(阶段3; 模式 C: 坐标+时间给定, 单系统)
    from simulator.typhoons import gen as TG
    g = TG.Generator(api=api, seed=seed + 2000)
    if mode == 'C':
        if coord is None or t0 is None:
            raise ValueError('模式 C 需 --lat/--lon 与 --time')
        recs = g.generate('C', coord=coord, t0=t0, year=t0.year, month=t0.month)
    elif mode == 'A':
        if coord is None:
            raise ValueError('模式 A 需 --lat/--lon')
        recs = g.generate('A', coord=coord, year=start_y, month=7)
    elif mode == 'B':
        if t0 is None:
            raise ValueError('模式 B 需 --time')
        recs = g.generate('B', t0=t0, year=t0.year, month=t0.month)
    else:
        # 模式 D: 月份窗口从接续段起始月对齐到整窗(环境场窗口与模拟月一致)。
        # window_months = years*12 从 (start_y, start_mo) 覆盖整窗, 跨年递增;
        # 旧实现仅生成首年 [start_mo..12], --years>1 时窗口后续年份完全空转
        # (多年场景台风缺失 + 越窗月用历史/无场采样 的集成 Bug)。
        recs = g.generate('D', year=start_y, month=start_mo,
                          window_months=years * 12)
    if no and len(recs) == 1:
        recs[0]['no'] = no
    if name:
        for r in recs:
            r['name'] = name
    g.save(os.path.join(out_root, 'records.json'))
    _check(len(recs) > 0, 'records.json(生成台风)')
    log.info('  生成 %d 台风,拒绝 %d 次', len(recs), len(g.rejections))

    # d. 台风模拟(阶段4) → .dat
    from simulator.typhoons import sim as TS
    dat_dir = os.path.join(out_root, 'dat')
    sims = TS.simulate_records(recs, api, seed=seed + 3000, out_dir=dat_dir)
    n_dat = len([1 for s in sims if s.states])
    _check(n_dat > 0, f'.dat({n_dat})')
    diss = [s.dissip_reason for s in sims if s.dissip_reason]
    exs = sum(1 for s in sims for st in s.states if st['st'] == 'EX')
    log.info('  模拟 %d 台风(%d 有报点),EX 报点 %d,消散原因 %s',
             len(sims), n_dat, exs, diss or '进行中')

    # e. 演示帧(阶段5 + F10 视频画面)
    frames_dir = None
    if render_frames and sims and sims[0].states:
        frames_dir = os.path.join(out_root, 'frames')
        os.makedirs(frames_dir, exist_ok=True)
        all_states = []
        for s in sims:
            for st in s.states:
                st2 = dict(st)
                st2['id'] = s.no
                all_states.append(st2)
        all_states.sort(key=lambda st: st.get('t', ''))
        dt0 = t0 if t0 is not None else datetime(start_y, 1, 1)
        set_video_context(api, all_states, dt0)
        n_saved = 0
        if render_all:
            from simulator.ui.main import LAYERS
            target = all_states[len(all_states) // 2] if all_states else None
            if target is not None:
                dt = datetime.strptime(target['t'], '%Y%m%d%H')
                for lid, _name, _key, _rdy in LAYERS:
                    surf = render_layer(lid, (dt.year, dt.month, dt.day, dt.hour),
                                        frame_size)
                    if surf is not None:
                        pygame.image.save(surf, os.path.join(frames_dir, f"{lid}.png"))
                        n_saved += 1
        else:
            for lid in VIDEO_LAYER_IDS:
                if not all_states:
                    break
                s0 = all_states[len(all_states) // 2]
                dt = datetime.strptime(s0['t'], '%Y%m%d%H')
                surf = render_layer(lid, (dt.year, dt.month, dt.day, dt.hour),
                                    frame_size)
                if surf is not None:
                    pygame.image.save(surf, os.path.join(frames_dir, f"{lid}.png"))
                    n_saved += 1
        log.info('  演示帧 %d 类保存至 %s', n_saved, frames_dir)

    log.info('PIPELINE DONE seed=%d years=%d (%.0fs)', seed, years,
             time.perf_counter() - t_start)
    return {'seed': seed, 'out': out_root, 'records': len(recs),
            'dat': n_dat, 'log': logp, 'frames': frames_dir}


def main(argv=None):
    import argparse
    pygame.init()          # 字体等需先初始化(headless 亦同)
    try:
        pygame.key.stop_text_input()
    except Exception:
        pass
    from simulator.ui import theme as T
    from simulator.ui.main import (LAYERS, ModePanel, set_video_context,
                                   render_layer)
    ap = argparse.ArgumentParser(description='台风架空模拟器')
    ap.add_argument('--seed', type=int, default=None,
                    help='固定种子;缺省时界面模式随机、批处理模式默认 1')
    ap.add_argument('--years', type=int, default=3)
    ap.add_argument('--width', type=int, default=1366)
    ap.add_argument('--height', type=int, default=768)
    ap.add_argument('--headless', action='store_true',
                    help='批处理模式: 仅生成不渲染界面')
    ap.add_argument('--out', default=None)
    ap.add_argument('--size', default=None,
                    help='headless 帧渲染尺寸 WxH(默认 2560x1600)')
    ap.add_argument('--mode', default='D', choices=('A', 'B', 'C', 'D'),
                    help='生成模式: A 定坐标 / B 定时间 / C 坐标+时间 / D 全随机')
    ap.add_argument('--kernel', default='B', choices=('A', 'B'),
                    help='模拟内核: B 合成架空(默认) / A 真实大气(IFS/ERA5, 需联网)')
    ap.add_argument('--lat', type=float, default=None, help='模式 A/C 生成纬度')
    ap.add_argument('--lon', type=float, default=None, help='模式 A/C 生成经度')
    ap.add_argument('--time', default=None, help='模式 B/C 生成时间 YYYY-MM-DD HH')
    ap.add_argument('--name', default=None, help='台风名(单系统生效)')
    ap.add_argument('--no', default=None, help='低压编号(单系统生效)')
    ap.add_argument('--render-all', action='store_true',
                    help='headless 时渲染全部 17 类图层到 frames/')
    args = ap.parse_args(argv)

    if args.headless:
        frame_size = (2560, 1600)
        if args.size:
            try:
                sw_, sh_ = args.size.lower().split('x')
                frame_size = (int(sw_), int(sh_))
            except (ValueError, AttributeError):
                print(f'--size 格式错误: {args.size}(应为 WxH,如 2560x1600)')
                return
        seed = args.seed if args.seed is not None else 1
        coord = (args.lat, args.lon) if args.lat is not None and args.lon is not None else None
        t0 = None
        if args.time:
            try:
                t0 = datetime.strptime(args.time, '%Y-%m-%d %H')
            except ValueError:
                print(f'--time 格式错误: {args.time}(应为 YYYY-MM-DD HH)')
                return
        result = run_pipeline(seed, args.years, args.out, mode=args.mode,
                              coord=coord, t0=t0, name=args.name, no=args.no,
                              render_all=args.render_all, kernel=args.kernel,
                              frame_size=frame_size)
        print(f"[F11] 完成: {result['records']} 台风, {result['dat']} 个 .dat,"
              f" 日志 {result['log']}")
        if result['frames']:
            print(f"[F11] 演示帧: {result['frames']}")
        return

    # 窗口图标必须在 set_mode 之前设置,否则任务栏图标不生效
    icon = os.path.join(ROOT, 'assets', 'icon.png')
    if os.path.exists(icon):
        try:
            pygame.display.set_icon(pygame.image.load(icon))
        except Exception:
            pass

    screen = pygame.display.set_mode((args.width, args.height), pygame.RESIZABLE)
    pygame.display.set_caption("台风架空模拟器")

    # 用户规则: 打开模拟器时种子初始随机(缺省不固定);显式 --seed 才固定
    seed = args.seed if args.seed is not None else int(random.random() * (10 ** 10))

    # 模态序列: 优先加载 output 归档,缺失则自动生成(离线可用)
    from simulator import modes as M
    series = None
    out_path = os.path.join(BASE, 'output', 'modes_series.json')
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding='utf-8') as f:
                doc = json.load(f)
            series = {m: [(y, mo, v, seg) for y, mo, v, seg in rows]
                      for m, rows in doc['modes'].items()}
        except Exception:
            series = None
    if series is None:
        gen = M.ModeGenerator(seed=seed, cont_years=2)
        series = gen.generate(total_years=args.years)
        M.save_output(series, gen.seed, gen.sub_seeds, gen.retry_counts,
                      gen.low_confidence, os.path.join(BASE, 'output'))

    app = SimulatorApp(screen, series, seed)
    app.run()


def _track_covers(track: list, t0: Optional[datetime]) -> bool:
    """track 是否覆盖 t0(±3 天)。"""
    if not track:
        return False
    try:
        ts = [datetime(int(s['t'][:4]), int(s['t'][4:6]), int(s['t'][6:8]),
                       int(s['t'][8:10])) for s in track]
    except Exception:
        return False
    if t0 is None:
        return True
    return min(ts) <= t0 + timedelta(days=3) and max(ts) >= t0 - timedelta(days=3)


if __name__ == '__main__':
    main()
