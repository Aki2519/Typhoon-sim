# app/sim_settings_dialog.py
"""模拟模式专属设置面板。

**2026-09-19 按用户要求重做样式**: "设置页面做成与主设置界面相同" —— 现在与
`app/settings.py` 的 Settings 对话框同一套视觉语言:

  · 深色圆角面板 + 右上角"确认/关闭"按钮(同款 modern button);
  · 小节标题(时间与步长 / 强度显示精度 / 数据环境 / 显示与交互) + 分隔线;
  · 每行: 左侧标签(带冒号) + 右侧控件(内凹输入框样式的值 / 复选框);
  · 颜色字体全部取自 constants 的 SETTINGS_* 常量, 与主设置界面一致。

数据通路仍是"面板只调用 mixin 的唯一实现"(见 _get/_set), 每个可点的值都能
落到运行时(runtime)与 cfg(跨会话记住)。
"""
from __future__ import annotations

import pygame

from .constants import (
    f_s, f_m, rt,
    SETTINGS_TAB_BG, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    SETTINGS_TOGGLE_OFF, SETTINGS_TOGGLE_ON, settings_accent,
)
from .dialog_base import DraggableDialog

#: 与主设置界面同一套网格(见 settings.Settings.LAYOUT_*)
_ROW_H = 30
_LABEL_X = 30
_CTRL_X = 250
_CTRL_W = 150
_CTRL_H = 24
_CHECK = 18
_SECTION_GAP = 18
_PANEL_W = 750
_PANEL_H = 680
_BTN_W, _BTN_H = 60, 26


class SimSettingsDialog(DraggableDialog):
    """模拟设置(仅在模拟模式可用)。选项实时生效, 直接读写 sim 属性。"""

    #: 这三个"循环"行的权威值在 **cfg**(内核从 sim['params'] 读, 面板显示读 cfg,
    #: 循环函数两边一起写)。_get/_set 必须走同一张表, 否则面板显示与 _get() 会打架。
    _CYCLE_CFG = {'_precision': 'sim_precision',
                  '_vmax_prec': 'sim_vmax_prec',
                  '_pmin_prec': 'sim_pmin_prec',
                  # 轮次 151: 集合预报(实时)开关。默认关 —— 它占单步 89% 的墙钟,
                  # 开着时 400ms 档 UI 冻结 84-88%, 交互基本不可用。
                  '_ensemble': 'sim_ensemble'}

    #: 轮次 158: 面板键 → `sim['params']` 的键(新增环境机制)。走 mixin 的
    #: `_sim_param_value/_sim_set_param/_sim_cycle_param/_sim_toggle_param` 唯一实现,
    #: 面板不自造状态, 也不直接碰 params。
    _PARAM_KEYS = {
        '_p_shTilt': 'shTilt', '_p_shWestAmp': 'shWestAmp',
        '_p_shExtW': 'shExtW', '_p_shExtE': 'shExtE',
        '_p_shExtN': 'shExtN', '_p_shExtS': 'shExtS',
        '_p_wtOn': 'wtOn', '_p_wtAmp': 'wtAmp',
        '_p_csOn': 'csOn', '_p_csAmp': 'csAmp',
        '_p_terrainOn': 'terrainOn',
    }
    #: 这些是布尔开关(其余是数值 cycle 行)
    _PARAM_FLAGS = ('wtOn', 'csOn', 'terrainOn')

    def __init__(self, sim):
        super().__init__(sim)
        self.active = False
        self.bg_rect = pygame.Rect(0, 0, _PANEL_W, _PANEL_H)
        self._close_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._ok_btn_rect = pygame.Rect(0, 0, 0, 0)
        self._rows = []          # 本轮绘制时的行表
        self._slider_rects = {}  # 兼容旧调用(现在没有滑条)
        self._toggle_rects = {}

    # ── 属性存取 ──
    def _sim(self):
        return getattr(self.sim, 'sim_v4', None)

    def _display_keys(self) -> dict:
        return getattr(self.sim, '_SIM_DISPLAY_KEYS', {}) or {}

    def _get(self, key, default):
        # 显示/开关类键的权威值在应用属性(镜像 cfg) —— 见 SimModeMixin。
        if key in self._display_keys():
            return self.sim._sim_display(key, default)
        # 轮次 158: 新增环境机制(住在 sim['params'] + cfg 覆盖表)
        if key in self._PARAM_KEYS:
            return self.sim._sim_param_value(self._PARAM_KEYS[key], default)
        if key == '_genesis':                 # 规范属性: sim_natural_gen
            return bool(getattr(self.sim, 'sim_natural_gen', True))
        if key == '_data_mode':               # 规范属性: sim_data_mode
            return bool(getattr(self.sim, 'sim_data_mode', True))
        if key == '_click_spawn':
            return bool(getattr(self.sim.cfg, 'sim_click_spawn', False))
        if key in self._CYCLE_CFG:
            return getattr(self.sim.cfg, self._CYCLE_CFG[key], default)
        if key == '_env_step':
            return getattr(self.sim, 'sim_env_step_h', default)
        if key == '_seed':
            return getattr(self.sim, 'sim_seed', default)
        if key == '_data_year':
            return getattr(self.sim, 'sim_data_year', default)
        s = self._sim()
        if s is not None and key in s:
            return s[key]
        return default

    def _set(self, key, value):
        # 不再写 `setattr(self.sim, key.lstrip('_'), value)` 那种没人读的镜像属性
        # (粒子密度滑条就是被它坑死的, 见 tests/test_sim_settings.py)
        if key in self._display_keys():
            self.sim._sim_set_display(key, value)
            return
        if key in self._CYCLE_CFG:
            setattr(self.sim.cfg, self._CYCLE_CFG[key], value)
            self.sim._config_needs_save = True
            # 集合预报开关改变后必须**重建模拟**才生效(它写进 sim_v4['params'])
            if key == '_ensemble':
                self.sim._sim_reset(msg=False)
                self.sim._sim_notice('集合预报: ' + ('开(很慢)' if value else '关'))
            return
        if key == '_env_step':
            self.sim._sim_set_env_step(value, reset=False)
            return
        if key == '_seed':
            self.sim._sim_set_seed(value)
            return
        if key == '_data_year':
            self.sim._sim_set_data_year(value)
            return
        if key == '_click_spawn':
            self.sim.cfg.sim_click_spawn = bool(value)
            self.sim._config_needs_save = True
            self.sim._sim_notice('点击生成: ' + ('开' if value else '关'))
            return
        s = self._sim()
        if s is not None:
            s[key] = value

    # ── 生命周期 ──
    def activate(self) -> None:
        # 必须走基类: 登记 _dialog_stack 并失效 dialog_mgr._active_count,
        # 否则 any_active() 恒为 False → 点击穿透到地图、面板无法被统一关闭
        super().activate()
        w = min(_PANEL_W, max(420, self.sim.screen_width - 80))
        avail_h = max(320, self.sim.screen_height
                      - getattr(self.sim, 'control_panel_height', 100) - 70)
        rows = self._rows_spec()
        need = self._rows_height(rows) + 52 + 34
        # 单列放不下就分列(用户 2026-09-19 要求"与主设置界面相同", 主界面是
        # 可滚动单列; 这里用分列达到同样效果: 任何窗口尺寸下控件都不会被裁掉)
        usable = max(120, avail_h - 52 - 34)
        self._cols = 1
        while self._cols < 3 and need / self._cols > usable:
            self._cols += 1
        if self._cols > 1:
            w = min(int(_PANEL_W * 0.62 * self._cols),
                    max(560, self.sim.screen_width - 60))
        h = min(max(_PANEL_H, need if self._cols == 1 else usable + 86),
                avail_h)
        self.bg_rect = pygame.Rect((self.sim.screen_width - w) // 2,
                                   max(50, (self.sim.screen_height
                                            - getattr(self.sim, 'control_panel_height', 100)
                                            - h) // 2),
                                   w, h)
        self._layout_valid = False

    def deactivate(self) -> None:
        super().deactivate()

    def _split_columns(self, rows, cols) -> list:
        """把行表按**高度**均分成 cols 列(小节标题不与其后第一行分开)。"""
        if cols <= 1:
            return [rows]
        total = self._rows_height(rows)
        target = total / cols
        out, cur, h = [], [], 0
        for i, row in enumerate(rows):
            kind = row[2]
            rh = (_ROW_H + _SECTION_GAP - 8) if kind == 'section' else _ROW_H
            nxt = rows[i + 1][2] if i + 1 < len(rows) else None
            # 小节标题必须与其后第一行同列
            if (kind == 'section' and nxt is not None
                    and h + rh + _ROW_H > target and len(out) < cols - 1):
                out.append(cur)
                cur, h = [], 0
            cur.append(row)
            h += rh
            if h >= target and len(out) < cols - 1 and kind != 'section':
                out.append(cur)
                cur, h = [], 0
        if cur:
            out.append(cur)
        return out

    @staticmethod
    def _rows_height(rows) -> int:
        h = 0
        for _k, _l, kind in rows:
            h += (_ROW_H + _SECTION_GAP - 8) if kind == 'section' else _ROW_H
        return h

    # ── 行表(带小节标题) ──
    def _rows_spec(self) -> list:
        """[(key, label, kind)]; kind: section/value/cycle/check/style/info。"""
        return [
            ('_sec_time', '时间与步长', 'section'),
            ('_start_time', '模拟起始时间:', 'value'),
            ('_env_step', '环境步长:', 'cycle'),
            ('_precision', '模拟精度:', 'cycle'),
            ('_sec_prec', '强度显示精度', 'section'),
            ('_vmax_prec', '风速精度:', 'cycle'),
            ('_pmin_prec', '气压精度:', 'cycle'),
            ('_sec_data', '数据环境', 'section'),
            ('_data_mode', '数据环境:', 'check'),
            ('_data_state', '数据源:', 'info'),
            ('_data_year', '数据年份:', 'value'),
            ('_seed', '模拟种子:', 'value'),
            ('_mode_ovr', '模态覆盖:', 'value'),
            ('_sec_display', '显示与交互', 'section'),
            ('_show_barbs', '风羽:', 'check'),
            ('_ensemble', '集合预报(实时,慢):', 'check'),
            ('_show_forecast', '预报路径(需集合预报):', 'style'),
            # **上线口径(用户 2026-09-23)**: 预报的局限必须写在界面上, 不能只写在文档里。
            # 模型环境是**月气候态库 + 合成瞬变**, 没有实时分析场 ⇒ 误差只能与
            # "持续性/气候漂移"这类**同样没有实时信息**的基线比(实测技巧分 +0.06~+0.14),
            # 与官方业务误差(24h 70-100km)直接比是没有意义的。
            ('_fcst_note', '预报说明:', 'info'),
            ('_quad_rings', '四象限风圈:', 'check'),
            ('_show_rings', '风圈显示:', 'check'),
            ('_show_labels', '台风标签:', 'check'),
            ('_genesis', '自然生成:', 'check'),
            ('_particle_count', '粒子密度:', 'value'),
            ('_click_spawn', '点击生成台风:', 'check'),
            # ── 轮次 158: 新增环境机制(默认全部关闭, 所以不动它们 = 结果逐位不变) ──
            # 副高形状: ①② 作用于**脊线诊断**(⇒ 引导修正, 只在数据环境模式下);
            # ③ 四向伸展作用于**副高的气压场**(只在解析环境模式下)。见
            # tools/probe_new_knobs_r158.py 的实测记录。
            ('_sec_ridge', '副高形状 158', 'section'),
            ('_p_shTilt', '脊线倾角:', 'cycle'),
            ('_p_shWestAmp', '西端陡峭:', 'cycle'),
            ('_p_shExtW', '副高西伸:', 'cycle'),
            ('_p_shExtE', '副高东伸:', 'cycle'),
            ('_p_shExtN', '副高北伸:', 'cycle'),
            ('_p_shExtS', '副高南伸:', 'cycle'),
            ('_sec_synoptic', '西风槽/冷空气 158', 'section'),
            ('_p_wtOn', '西风槽:', 'check'),
            ('_p_wtAmp', '西风槽强度:', 'cycle'),
            ('_p_csOn', '冷空气:', 'check'),
            ('_p_csAmp', '冷空气强度:', 'cycle'),
            ('_sec_terrain', '地形 158', 'section'),
            ('_p_terrainOn', '山脉地形:', 'check'),
            ('_p_terrain_note', '地形说明:', 'info'),
        ]

    def _row_text(self, key: str) -> str:
        """'value'/'cycle' 类行的显示文本。"""
        if key == '_mode_ovr':
            return self._sim_mode_text()
        if key == '_data_year':
            try:
                return self.sim._sim_data_year_text()
            except Exception:
                return '气候态'
        if key == '_seed':
            return str(getattr(self.sim, 'sim_seed', 1))
        if key == '_start_time':
            return self._sim_start_text()
        if key == '_env_step':
            return '%g 小时' % float(self._get('_env_step', 6.0))
        if key == '_precision':
            return str(self._get('_precision', '400ms'))
        if key == '_vmax_prec':
            return {'5': '整 5 kt', '1': '整 1 kt',
                    '0.1': '0.1 kt'}.get(str(self._get('_vmax_prec', '1')), '整 1 kt')
        if key == '_pmin_prec':
            return {'0258': '尾数 0258', '1': '整 1 hPa',
                    '0.1': '0.1 hPa'}.get(str(self._get('_pmin_prec', '1')), '整 1 hPa')
        if key == '_particle_count':
            return str(self.sim._sim_particle_count())
        # 轮次 158: 新增环境机制的 cycle 行显示
        if key in self._PARAM_KEYS:
            p = self._PARAM_KEYS[key]
            v = self._get(key, None)
            if p == 'shTilt':
                return '0 (关闭)' if not v else '%+.1f 度/度' % float(v)
            if p == 'shWestAmp':
                return '0 (关闭)' if not v else '%.0f' % float(v)
            if p in ('shExtW', 'shExtE', 'shExtN', 'shExtS'):
                return '%.1f 度' % float(v)
            if p in ('wtAmp', 'csAmp'):
                return '%.0f kt' % float(v)
            return str(v)
        if key == '_p_terrain_note':
            # 让"默认关"和"数据来源"一眼可见, 免得以为是坏了
            try:
                from simulator.simcore import terrain as _TG
                _ok = _TG.available()
            except Exception:
                _ok = False
            # ⚠ 必须极短: 面板是分列布局, info 行的长文本会溢出被裁(实测过)
            return '真 DEM 默认关' if _ok else '无数据 不生效'
        if key == '_fcst_note':
            # 上线口径: 界面上必须能一眼看到"预报没有实时信息"这件事。
            return ('环境=月气候态+合成瞬变, 无实时信息; 误差仅略优于气候漂移'
                    '(24h≈300km)')
        return ''

    def _sim_start_text(self) -> str:
        """起始时刻显示(容忍 str/datetime 两条路径 —— 见旧版崩溃记录)。"""
        st = getattr(self.sim, 'sim_start_dt', None)
        if st is None:
            st = getattr(self.sim, 'cfg', None)
            st = getattr(st, 'sim_start', None) if st is not None else None
        if st is None:
            s = self._sim()
            st = (s or {}).get('start')
        if st is None:
            return '-'
        if hasattr(st, 'strftime'):
            return st.strftime('%Y-%m-%d %HZ')
        return str(st)

    def _sim_mode_text(self) -> str:
        ov = getattr(self.sim, 'sim_mode_override', {}) or {}
        return ('已覆盖 %d 项' % len(ov)) if ov else '查表/AR'

    def _sim_data_text(self) -> str:
        s = self._sim()
        st = (s or {}).get('dataState', 'off') if s else 'off'
        base = {'loaded': '气候库', 'partial': '气候库·部分',
                'fallback': '已降级解析', 'off': '解析公式'}.get(st, st)
        # 瞬时 / 瞬时·部分 / 月均 + 数据覆盖窗口(用户 2026-09-20: "一切数据要用瞬时";
        # 而单日 ERA5 只有 ±6 小时窗口, 模拟钟一走远就回落月均 —— 必须写在面板上)
        src = str((s or {}).get('dataSrc') or '')
        if src:
            base += '·' + src
        try:
            from simulator.simcore import daily as _D
            cov = _D.coverage()
            e = cov.get('era5')
            o = cov.get('ohc')
            bits = []
            if e:
                bits.append('ERA5 %s..%s(%d)' % (e[0].strftime('%m-%d %HZ'),
                                                 e[1].strftime('%m-%d %HZ'), e[2]))
            if o:
                bits.append('OHC %s..%s(%d)' % (o[0].strftime('%m-%d'),
                                                o[1].strftime('%m-%d'), o[2]))
            if bits:
                base += '  |  ' + '  '.join(bits)
        except Exception:
            pass
        return base

    # ── 绘制 ──
    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        dark = getattr(self.sim, 'dark_mode', True)
        r = self.bg_rect
        text_color = SETTINGS_TEXT_LIGHT if dark else (30, 40, 60)
        dim = SETTINGS_TEXT_DIM if dark else (110, 120, 140)
        accent = settings_accent(dark, getattr(self.sim, 'color_scheme', None))
        self._slider_rects = {}
        self._toggle_rects = {}

        if dark:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
        else:
            self.draw_background(surface, r)
        dx, dy, dw, dh = r
        surface.blit(rt(f_m, "模拟设置", text_color), (dx + 20, dy + 12))

        # 右上角按钮(与主设置界面同款 modern button)
        mx, my = pygame.mouse.get_pos()
        close_rect = pygame.Rect(dx + dw - _BTN_W - 12, dy + 8, _BTN_W, _BTN_H)
        ok_rect = pygame.Rect(dx + dw - _BTN_W * 2 - 20, dy + 8, _BTN_W, _BTN_H)
        self._close_btn_rect = close_rect
        self._ok_btn_rect = ok_rect
        self._modern_btn(surface, close_rect, "关闭", dark, accent,
                         hover=close_rect.collidepoint(mx, my))
        self._modern_btn(surface, ok_rect, "确认", dark, accent, accent_on=True,
                         hover=ok_rect.collidepoint(mx, my))

        rows = self._rows_spec()
        self._rows = rows
        cols = max(1, int(getattr(self, '_cols', 1)))
        if cols > 1:
            chunks = self._split_columns(rows, cols)
            step = (dw - 40) // cols
            for i, chunk in enumerate(chunks):
                self._draw_column(surface, chunk, dx, dy, dh,
                                  dark, text_color, dim, accent, i * step, step)
            return
        self._draw_column(surface, rows, dx, dy, dh, dark, text_color, dim,
                          accent, 0, dw - 40)

    def _draw_column(self, surface, rows, dx, dy, dh, dark, text_color, dim,
                     accent, x_off, col_w=None) -> None:
        y = dy + 52
        for key, label, kind in rows:
            if y > dy + dh - 34:
                break
            if kind == 'section':
                # 小节标题 + 下方分隔线(与主设置界面一致)
                surface.blit(rt(f_m, label, dim), (dx + _LABEL_X + x_off, y + 2))
                ly = y + 24
                pygame.draw.line(surface, SETTINGS_TAB_BG,
                                 (dx + _LABEL_X + x_off, ly),
                                 (dx + _LABEL_X + x_off + 300, ly), 1)
                y += _ROW_H + _SECTION_GAP - 8
                continue
            surface.blit(rt(f_s, label, text_color),
                         (dx + _LABEL_X + x_off, y + 5))
            cx = dx + _CTRL_X + x_off
            if kind == 'info':
                # 原来这里**写死了 `_sim_data_text()`** —— 只有一个 info 行时看不出来,
                # 加第二个 info 行就会两行都显示数据源。改成按键取值。
                _itxt = (self._sim_data_text() if key == '_data_state'
                         else self._row_text(key))
                # 轮次 158: **按列宽截断**。原来 info 行完全不受宽度约束, 长文本会
                # 直接画进**下一列**并与那里的标题/数值重叠(实测 "预报说明" 压住
                # "西风槽/冷空气 158" 标题、"数据源" 压住 "点击生成台风")。
                if col_w:
                    _lim = int(col_w) - (_CTRL_X + 8) - 10
                    if _lim > 20:
                        _itxt = self._elide(f_s, _itxt, _lim)
                surface.blit(rt(f_s, _itxt, text_color), (cx + 8, y + 5))
            elif kind in ('value', 'cycle'):
                box = pygame.Rect(cx, y + 3, _CTRL_W, _CTRL_H)
                self._field_box(surface, box, dark)
                ts = rt(f_s, self._row_text(key), text_color)
                surface.blit(ts, (box.centerx - ts.get_width() // 2,
                                  box.centery - ts.get_height() // 2))
                self._toggle_rects[key] = box
            elif kind in ('check', 'style'):
                box = pygame.Rect(cx, y + 6, _CHECK, _CHECK)
                self._checkbox(surface, box, bool(self._get(key, True)), dark, accent)
                self._toggle_rects[key] = box
                if kind == 'style':
                    style = self._get('_fcst_style', 'JTWC')
                    sbox = pygame.Rect(cx + _CHECK + 14, y + 3, 120, _CTRL_H)
                    self._field_box(surface, sbox, dark)
                    ss = rt(f_s, '样式: %s' % style, text_color)
                    surface.blit(ss, (sbox.centerx - ss.get_width() // 2,
                                      sbox.centery - ss.get_height() // 2))
                    self._toggle_rects['_style_btn'] = sbox
            y += _ROW_H

    @staticmethod
    def _elide(font, text: str, max_w: int) -> str:
        """把文本按像素宽度截断并加省略号(轮次 158: 防 info 行溢进下一列)。"""
        try:
            if rt(font, text, (255, 255, 255)).get_width() <= max_w:
                return text
        except Exception:
            return text
        s = text
        while len(s) > 2:
            s = s[:-1]
            try:
                if rt(font, s + '…', (255, 255, 255)).get_width() <= max_w:
                    return s + '…'
            except Exception:
                break
        return s

    def _modern_btn(self, surface, rect, text, dark, accent,
                    accent_on=False, hover=False):
        if accent_on:
            bg = accent
        elif dark:
            bg = SETTINGS_TOGGLE_ON if hover else SETTINGS_TOGGLE_OFF
        else:
            bg = (100, 150, 200) if hover else (180, 190, 210)
        pygame.draw.rect(surface, bg, rect, border_radius=6)
        ts = rt(f_m, text, (255, 255, 255))
        surface.blit(ts, (rect.centerx - ts.get_width() // 2,
                          rect.centery - ts.get_height() // 2 - 1))

    @staticmethod
    def _field_box(surface, box, dark):
        """内凹输入框样式(与主设置界面的数值框一致)。"""
        bg = (28, 34, 48) if dark else (232, 235, 242)
        bd = SETTINGS_TAB_BG if dark else (150, 160, 180)
        pygame.draw.rect(surface, bg, box, border_radius=4)
        pygame.draw.rect(surface, bd, box, 1, border_radius=4)

    @staticmethod
    def _checkbox(surface, box, on, dark, accent):
        """复选框(与主设置界面一致: 未选=深底描边, 选中=accent 底 + 白勾)。"""
        bg = (28, 34, 48) if dark else (232, 235, 242)
        bd = SETTINGS_TAB_BG if dark else (150, 160, 180)
        pygame.draw.rect(surface, bg, box, border_radius=4)
        pygame.draw.rect(surface, bd, box, 1, border_radius=4)
        if on:
            pygame.draw.rect(surface, accent, box.inflate(-2, -2), border_radius=3)
            c = box.center
            pygame.draw.lines(surface, (255, 255, 255), False,
                              [(c[0] - 4, c[1]), (c[0] - 1, c[1] + 3),
                               (c[0] + 4, c[1] - 3)], 2)

    # ── 事件 ──
    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # **按钮必须先判**: 它们在标题栏范围内(右上角), 而 handle_drag_event
            # 会把标题栏内的按下当成拖拽并 return True —— 于是"确认/关闭"点不动
            # (用户 2026-09-19 报的正是这个)
            if self._close_btn_rect.collidepoint(e.pos):
                self.deactivate()
                return True
            if self._ok_btn_rect.collidepoint(e.pos):
                if self.sim.save_config(force=True):
                    self.sim.show_toast("模拟设置已保存", 'success')
                else:
                    self.sim.show_error("设置保存失败(写入配置失败)")
                self.deactivate()
                return True
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self._close_btn_rect.collidepoint(x, y):
                self.deactivate()
                return True
            if self._ok_btn_rect.collidepoint(x, y):
                # 与主设置界面一致: 值即时生效, "确认" = 落盘 + 关闭
                if self.sim.save_config(force=True):
                    self.sim.show_toast("模拟设置已保存", 'success')
                else:
                    self.sim.show_error("设置保存失败(写入配置失败)")
                self.deactivate()
                return True
            for key, tr in self._toggle_rects.items():
                if tr.collidepoint(x, y):
                    self._on_control(key)
                    return True
            if self.bg_rect.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        return False

    def _on_control(self, key: str) -> None:
        """控件点击 → 统一走 mixin 的唯一实现(面板不自造状态)。"""
        dm = getattr(self.sim, 'dialog_mgr', None)
        if key == '_style_btn':
            cur = self._get('_fcst_style', 'JTWC')
            self._set('_fcst_style', 'JMA' if cur == 'JTWC' else 'JTWC')
        elif key == '_start_time':
            if dm is not None and getattr(dm, 'sim_time', None):
                dm.sim_time.activate()
        elif key == '_mode_ovr':
            if dm is not None and getattr(dm, 'sim_mode', None):
                dm.sim_mode.activate()
        elif key == '_seed':
            if dm is not None and getattr(dm, 'sim_value', None):
                dm.sim_value.activate(
                    title="模拟种子", label="种子",
                    value=str(getattr(self.sim, 'sim_seed', 1)),
                    hint="相同种子 = 相同噪声流(重置后生效)",
                    on_ok=self.sim._sim_set_seed, max_length=10)
        elif key == '_data_year':
            if dm is not None and getattr(dm, 'sim_value', None):
                _y = getattr(self.sim, 'sim_data_year', None)
                dm.sim_value.activate(
                    title="数据年份", label="年份",
                    value='' if _y is None else str(int(_y)),
                    hint="留空 = 气候态; 填年份 = 用该年气候库",
                    on_ok=self.sim._sim_set_data_year, max_length=4)
        elif key == '_particle_count':
            if dm is not None and getattr(dm, 'sim_value', None):
                dm.sim_value.activate(
                    title="粒子密度", label="粒子数",
                    value=str(self.sim._sim_particle_count()),
                    hint="500 - 6000",
                    on_ok=self.sim._sim_set_display_particle, max_length=4)
        elif key == '_env_step':
            self.sim._sim_cycle_env_step()
        elif key == '_precision':
            self.sim._sim_cycle_precision()
        elif key == '_vmax_prec':
            self.sim._sim_cycle_prec('vmax')
        elif key == '_pmin_prec':
            self.sim._sim_cycle_prec('pmin')
        elif key == '_data_mode':
            self.sim._sim_toggle_data_mode()
        elif key == '_genesis':
            self.sim._sim_toggle_natural_gen()
        # 轮次 158: 新增环境机制 —— 走 mixin 的唯一实现, 面板不自造状态
        elif key in self._PARAM_KEYS:
            _p = self._PARAM_KEYS[key]
            if _p in self._PARAM_FLAGS:
                self.sim._sim_toggle_param(_p)
            else:
                self.sim._sim_cycle_param(_p)
        else:
            self._set(key, not bool(self._get(key, True)))
