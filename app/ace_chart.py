# -*- coding: utf-8 -*-
"""右上角**实时 ACE 折线图**（用户 2026-10-19，样式照 @xrq 那张 2013 WPAC 图）。

参考图（用户给的）只有**最上面那一幅**：
    · 当年累计 ACE 粗线（深色、带线头）
    · 历史包络三条带：Min-Max / 10%-90% / 25%-75%（浅蓝，越窄越深）
    · Median（绿虚线）/ Average（紫虚线）
    · 标题行 `2013 WPAC   ACE: 335.3875 (+14.359)   Rank: 34/77`
    · 左上图例带具体数值；横轴 01-01…01-01；纵轴 100 一档
**下面的 Rank / 每日 ACE 柱 / 台风方块那几幅一律不做**；背景**透明**。

数据：`assets/ace_basin_years.json` 的 `daily`（每洋区每年 367 个按日累计值）
    西北太平洋来自 xrq，其它洋区来自官评（见 tools/ace_basin_stats.py）

四个开关（设置 → 显示 → ACE 折线图）：
    ace_chart_enabled      启用（同时关掉右上角总 ACE 条）
    ace_chart_history      历史包络 + 中位/平均虚线
    ace_chart_other        当年其它洋区（细线对比）
    ace_chart_follow       跟随：线头保持在图中心（ACE 很低时退化为从左显示）
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pygame

from .constants.fonts import _load_font, FONT_FILE   # 项目自带 MapleMono

W, H = 520, 246      # 2.11:1 —— 参考图那幅是宽扁的（约 2.15:1）
MARGIN = 10
BASIN = 'WP'
BASIN_CN = {'WP': 'WPAC', 'EP': 'EPAC', 'CP': 'CPAC', 'NA': 'NATL',
            'NI': 'NIO', 'SH': 'SHEM'}
BASIN_COLOR = {'EP': (110, 200, 255), 'CP': (150, 160, 255),
               'NA': (255, 140, 160), 'NI': (150, 235, 150), 'SH': (200, 165, 255)}

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'assets', 'ace_basin_years.json')


_mini = None


def shadow_blit(surface, surf, pos, offset=1):
    """透明底上的文字描边：先画一层黑描边再画本色。"""
    dark = surf.copy()
    dark.fill((0, 0, 0, 90), special_flags=pygame.BLEND_RGBA_MULT)
    for dx, dy in ((-offset, 0), (offset, 0), (0, -offset), (0, offset)):
        surface.blit(dark, (pos[0] + dx, pos[1] + dy))
    surface.blit(surf, pos)


_FONT = _load_font(FONT_FILE, 11, 11)      # 统一小字号（用户：字体缩小一些）
_FONT_T = _load_font(FONT_FILE, 12, 12)    # 只给标题行


def mini(text, color):
    """**全图统一**用项目自带字体（用户："字体不对，没有全部采用统一字体"）。

    之前标题用 f_s、刻度和图例用 pygame 默认字体，两套字体混在一起。
    现在标题/刻度/图例/年份标注**全部**走这一个字体对象。
    """
    return _FONT.render(text, True, color)


def flag(sim, name, default=False):
    """读开关：先 sim 属性，再退 cfg（设置面板 `_sync` 写的是 sim）。"""
    v = getattr(sim, name, None)
    if v is None:
        cfg = getattr(sim, 'cfg', None)
        v = getattr(cfg, name, default) if cfg is not None else default
    return bool(v)


def _pct(sorted_vals, q):
    """线性插值分位数"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo))


class AceChart:
    def __init__(self, sim):
        self.sim = sim
        self._data = None
        self._tried = False
        self._bands = {}

    # ── 数据 ──
    def data(self):
        if self._data is None and not self._tried:
            self._tried = True
            try:
                with open(DATA, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        return self._data or {}

    def daily(self, basin, year):
        return (self.data().get('daily', {}).get(basin, {}) or {}).get(str(year)) or None

    def bands(self, basin):
        """按天统计历史包络。返回 (env, finals, n_years)

        env[d] = (min, max, p10, p90, p25, p75, median, average)
        finals  = 同年末值的同一组统计（给图例显示具体数字）
        """
        if basin in self._bands:
            return self._bands[basin]
        years = self.data().get('daily', {}).get(basin, {}) or {}
        series = [v for v in years.values() if v]
        out = None
        if series:
            n = max(len(s) for s in series)
            env = []
            for d in range(n):
                col = sorted(s[d] for s in series if d < len(s))
                env.append((col[0], col[-1], _pct(col, 10), _pct(col, 90),
                            _pct(col, 25), _pct(col, 75),
                            _pct(col, 50), sum(col) / len(col)))
            finals_v = sorted(v[-1] for v in series if v)
            finals = (finals_v[0], finals_v[-1], _pct(finals_v, 10),
                      _pct(finals_v, 90), _pct(finals_v, 25),
                      _pct(finals_v, 75), _pct(finals_v, 50),
                      sum(finals_v) / len(finals_v))
            out = (env, finals, len(finals_v))
        self._bands[basin] = out
        return out

    # ── 当前状态 ──
    def now_point(self):
        sim = self.sim
        day, ace = 1.0, 0.0
        try:
            if getattr(sim, 'md', '') == getattr(sim, 'MODE_SEASON', 'season'):
                dt = sim._season_dt()
                day = dt.timetuple().tm_yday + dt.hour / 24.0
                ace = float(getattr(sim, 'csa', 0.0) or 0.0)
            else:
                ty = sim.current_typhoon() if hasattr(sim, 'current_typhoon') else None
                if ty is not None and getattr(ty, 'pts', None):
                    p = ty.pts[min(getattr(ty, 'ci', 0), len(ty.pts) - 1)]
                    t = str(p.get('t') or '')
                    if len(t) >= 8:
                        day = datetime(int(t[0:4]), int(t[4:6]),
                                       int(t[6:8])).timetuple().tm_yday
                    ace = float(getattr(ty, 'tace', 0.0) or 0.0)
        except Exception:
            pass
        return day, ace

    def live_curve(self):
        """**模拟自己的**按日累计 ACE（到当前时刻为止）。

        ⚠ 原来这里画的是 `daily` 里那一年的曲线（xrq 官方数据），而线头用的是
        模拟的 `csa` —— 两者根本不是一套数据（2025：模拟 535 vs xrq 239），
        于是线头飘在自己那条线外面，相近年份看着"根本不在两边"。
        现在当年曲线与线头都来自同一个来源：`ace_engine.cumulative_ace_up_to`。
        只在"日期变了"时重算（一天一次），避免每帧 366 次计算。
        """
        sim = self.sim
        try:
            year = self.year()
            dt = sim._season_dt()
        except Exception:
            return []
        key = (year, dt.date())
        if getattr(self, '_lc_key', None) == key:
            return self._lc
        eng = getattr(sim, 'ace_engine', None)
        cur = []
        if eng is not None:
            d0 = datetime(year, 1, 1)
            n = dt.timetuple().tm_yday
            for i in range(n):
                try:
                    cur.append(float(eng.cumulative_ace_up_to(d0 + timedelta(days=i))))
                except Exception:
                    cur.append(0.0)
        self._lc_key, self._lc = key, cur
        return cur

    def year(self):
        try:
            return self.sim._season_dt().year
        except Exception:
            return datetime.now().year

    # ── 绘制 ──
    @staticmethod
    def _dash_poly(surface, color, pts, seg=7, gap=5, width=2):
        """**真正的虚线**：沿整条折线累计长度来切段。

        ⚠ 之前是"每个小段各画一次"——相邻两点只差 1~2px，每次都从头开始画，
        结果整条线连成**实线**（用户："你这是完全的实线"）。必须累计长度。
        """
        if len(pts) < 2:
            return
        period = seg + gap
        acc = 0.0
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = max(abs(dx), abs(dy))
            if L < 1e-6:
                continue
            t = 0.0
            while t < L:
                pos = (acc + t) % period
                if pos < seg:
                    run = min(seg - pos, L - t)
                    a = t / L
                    b = (t + run) / L
                    pygame.draw.line(surface, color,
                                     (int(x1 + dx * a), int(y1 + dy * a)),
                                     (int(x1 + dx * b), int(y1 + dy * b)), width)
                    t += run
                else:
                    t += min(period - pos, L - t)
            acc += L

    @staticmethod
    def _shadow(surface, surf, pos):
        dark = surf.copy()
        dark.fill((0, 0, 0, 120), special_flags=pygame.BLEND_RGBA_MULT)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            surface.blit(dark, (pos[0] + dx, pos[1] + dy))
        surface.blit(surf, pos)

    def neighbors(self, day_idx, now_val):
        """当天实时值最接近的两个年份（返回 [(year, val), ...]）。"""
        out = []
        for y2, ser in (self.data().get('daily', {}).get(BASIN, {}) or {}).items():
            if not ser or day_idx >= len(ser):
                continue
            if str(y2) == str(self.year()):
                continue
            out.append((ser[day_idx], str(y2), ser))
        if not out:
            return []
        out.sort(key=lambda t: t[0])
        below = [t for t in out if t[0] <= now_val]
        above = [t for t in out if t[0] > now_val]
        if not below:
            picks = above[:2]
        elif not above:
            picks = below[-2:]
        else:
            picks = [below[-1], above[0]]
        return [(y, v) for v, y, _s in picks]

    def draw(self, surface):
        sim = self.sim
        w, h = W, H
        x0, y0 = sim.screen_width - w - MARGIN, MARGIN
        title_h, pad_l, pad_r, pad_b = 17, 32, 8, 18
        plot = pygame.Rect(x0 + pad_l, y0 + title_h,
                           w - pad_l - pad_r, h - title_h - pad_b)
        # 透明底：不垫任何底色（用户明确要求）

        year = self.year()
        day_now, ace_now = self.now_point()
        follow = flag(sim, 'ace_chart_follow', False)
        bd = self.bands(BASIN)
        cur = self.live_curve()          # 当年线 = 模拟实时累计（与线头同源）

        y_hi = max(50.0, ace_now * 1.15)
        if bd:
            y_hi = max(y_hi, bd[1][1] * 1.06)
            if bd[0]:
                y_hi = max(y_hi, max(e[1] for e in bd[0]) * 1.06)
        if cur:
            y_hi = max(y_hi, max(cur) * 1.05)
        _data_cur = self.daily(BASIN, year)
        if _data_cur:
            y_hi = max(y_hi, max(_data_cur) * 1.05)
        if follow and ace_now > 12:
            half_d = 30.0
            cx = min(366 - half_d, max(half_d, day_now))
            y_lo = max(0.0, ace_now - max(20.0, ace_now * 0.55))
            y_hi_f = ace_now + max(20.0, ace_now * 0.55)
        else:
            cx, half_d, y_lo, y_hi_f = 183.0, 183.0, 0.0, y_hi
        x_lo, x_hi = cx - half_d, cx + half_d

        def sx(d):
            return int(plot.x + (d - x_lo) / max(1e-6, x_hi - x_lo) * plot.w)

        def sy(a):
            return int(plot.bottom - (a - y_lo) / max(1e-6, y_hi_f - y_lo) * plot.h)

        # 网格（虚线）
        span = y_hi_f - y_lo
        step = 100.0 if span > 220 else (50.0 if span > 80 else max(5.0, round(span / 4.0)))
        a = y_lo - (y_lo % step) + step
        while a <= y_hi_f + 1e-6:
            y = sy(a)
            self._dash_poly(surface, (66, 74, 90), [(plot.x, y), (plot.right, y)],
                            4, 5, 1)
            a += step
        for m in range(1, 13):
            dnum = datetime(2001, m, 1).timetuple().tm_yday
            if x_lo <= dnum <= x_hi:
                x = sx(dnum)
                self._dash_poly(surface, (62, 70, 86), [(x, plot.top), (x, plot.bottom)],
                                4, 5, 1)

        # 三条包络带
        if flag(sim, 'ace_chart_history', True) and bd and bd[0]:
            env = bd[0]
            for lo_i, hi_i, alpha in ((0, 1, 58), (2, 3, 96), (4, 5, 140)):
                poly = [(sx(d + 1), sy(env[d][hi_i])) for d in range(len(env))
                        if x_lo <= d + 1 <= x_hi]
                poly += [(sx(d + 1), sy(env[d][lo_i])) for d in range(len(env) - 1, -1, -1)
                         if x_lo <= d + 1 <= x_hi]
                if len(poly) > 3:
                    local = [(px - plot.x, py - plot.y) for px, py in poly]
                    band = pygame.Surface(plot.size, pygame.SRCALPHA)
                    pygame.draw.polygon(band, (126, 178, 250, alpha), local)
                    if lo_i == 0:
                        for i in range(len(local)):
                            pygame.draw.line(band, (90, 150, 235, 190),
                                             local[i], local[(i + 1) % len(local)], 1)
                    surface.blit(band, plot.topleft)

        # 其它洋区（可选，细线）
        if flag(sim, 'ace_chart_other', False):
            for b, color in BASIN_COLOR.items():
                other = self.daily(b, year)
                if not other:
                    continue
                pts = [(sx(i + 1), sy(v)) for i, v in enumerate(other)
                       if x_lo <= i + 1 <= x_hi]
                if len(pts) > 1:
                    pygame.draw.lines(surface, color, False, pts, 1)

        # 最近的两个年份：**把它们的曲线画出来** + 数值标左上角
        # 历史曲线的下标 = 当天序号（1-based）；不要用 live 曲线的长度去钳，
        # 那会差一天（实测 191.1 vs 192.4）
        i_day = int(day_now)
        near = self.neighbors(i_day, ace_now)
        near_colors = [(255, 190, 120), (150, 220, 255)]
        for (ny, nval), col in zip(near, near_colors):
            ser = (self.data().get('daily', {}).get(BASIN, {}) or {}).get(ny)
            if not ser:
                continue
            pts = [(sx(i + 1), sy(v)) for i, v in enumerate(ser)
                   if x_lo <= i + 1 <= min(x_hi, day_now)]
            if len(pts) > 1:
                pygame.draw.lines(surface, col, False, pts, 2)

        # 中位（绿虚线）/ 平均（黑虚线）—— 真虚线
        if bd and bd[0]:
            env = bd[0]
            for idx, color in ((6, (60, 210, 130)), (7, (0, 0, 0))):
                pts = [(sx(d + 1), sy(env[d][idx])) for d in range(len(env))
                       if x_lo <= d + 1 <= x_hi]
                self._dash_poly(surface, color, pts, 7, 5, 2)

        # 平均 ACE：红色水平虚线（不再标字）
        if bd:
            avg = bd[1][7]
            ay = sy(avg)
            if plot.top < ay < plot.bottom:
                self._dash_poly(surface, (235, 45, 45),
                                [(plot.x, ay), (plot.right, ay)], 7, 5, 2)

        # 当年曲线：白底蓝边
        if cur:
            pts = [(sx(i + 1), sy(v)) for i, v in enumerate(cur)
                   if x_lo <= i + 1 <= min(x_hi, day_now)]
            if len(pts) > 1:
                pygame.draw.lines(surface, (60, 120, 255), False, pts, 5)
                pygame.draw.lines(surface, (255, 255, 255), False, pts, 3)
        hx, hy = sx(day_now), sy(ace_now)
        if plot.left - 4 <= hx <= plot.right + 4:
            pygame.draw.circle(surface, (60, 120, 255), (hx, hy), 5)
            pygame.draw.circle(surface, (255, 255, 255), (hx, hy), 3)

        # 轴标签
        a = y_lo - (y_lo % step)
        while a <= y_hi_f + 1e-6:
            if a >= y_lo:
                lab = mini('%.0f' % a, (150, 170, 210))
                ly = sy(a) - lab.get_height() // 2
                if ly > plot.top + 1:
                    self._shadow(surface, lab, (plot.x - lab.get_width() - 4, ly))
            a += step
        mw = mini('01-01', (0, 0, 0)).get_width()
        every = 1 if plot.w / 12.0 >= mw + 3 else 2
        for m in range(1, 13, every):
            dnum = datetime(2001, m, 1).timetuple().tm_yday
            if not (x_lo <= dnum <= x_hi):
                continue
            lab = mini('%02d-01' % m, (168, 182, 215))
            self._shadow(surface, lab, (sx(dnum) - lab.get_width() // 2, plot.bottom + 2))

        # 标题行
        title = _FONT_T.render('%d %s   ACE: %.4f' % (year, BASIN_CN.get(BASIN, BASIN),
                                                     ace_now), True, (245, 246, 250))
        self._shadow(surface, title, (x0 + 1, y0 + 1))
        if bd:
            # 名次用**同一天**的值比较（拿"当下 196"去跟别人"年末总值"比是错的，
            # 会算出 #76/78 这种离谱名次）
            same_day = []
            for _y2, ser in (self.data().get('daily', {}).get(BASIN, {}) or {}).items():
                if ser and i_day < len(ser):
                    same_day.append(ser[i_day])
            rank = 1 + sum(1 for v in same_day if v > ace_now)
            avg = bd[1][7]
            mid = mini('(%+.2f)  #%d/%d' % (ace_now - avg, rank, len(same_day)),
                       (210, 220, 235))
            self._shadow(surface, mid, (x0 + 2 + title.get_width() + 6, y0 + 2))

        # 最近年份的数值：**左上角**（无框、无"Median/Average"字样）
        ly = y0 + title.get_height() + 3
        for (ny, nval), col in zip(near, near_colors):
            txt = mini('%s  %.1f' % (ny, nval), col)
            self._shadow(surface, txt, (x0 + 2, ly))
            ly += txt.get_height() + 1
