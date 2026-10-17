# app/script_editor_panel.py
"""右侧**可视化脚本编辑面板**（用户 2026-10-13 需求 3.1 / 3.6 / 3.7 / 3.9 / 3.10）。

布局：
```
┌──────────────────────────────┬───────────────────┐
│                              │  [补全] 脚本编辑区 │  ← 面板宽 = 窗口 1/4
│            地图              │  (文本, 暗/亮)     │
│                              │                   │
│                        ┌─────┴───────────────────┤
│                        │  线性时间轴(覆盖功能栏)  │  ← 底部, 压住右 1/4 功能栏
└────────────────────────┴─────────────────────────┘
```
面板本体高度 = `screen_height - CPH`（用户：窗高 1540 ⇒ 1440，即在功能栏**上方**截止）；
时间轴贴在窗口最底部，因此会**覆盖**右 1/4 的功能栏（用户 3.6 的原话）。

交互优先级（3.10 要求"当前时间白线最高优先级"）：
    当前时间白线 > 段端点 > 变速点 > 段内 > 空白
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

import pygame

from .constants import f_s, f_m, rt
from .constants.layout import CPH
from .script_timeline import (
    C_BG, C_BORDER, C_JUMP, C_NOW, C_SEG, C_SEG_EDGE, C_SPEED, C_TEXT,
    LABEL_ROWS, Segment, TimelineModel, format_time, layout_labels, make_ticks,
)

PANEL_FRAC = 0.25          # 3.1 面板宽 ≈ 窗口 1/4
PANEL_MIN_W = 320
TIMELINE_H = 118           # 时间轴条高度（含 3 行文字 + 刻度）
HIT_PX = 9                 # 端点/白线的抓取半径（3.6.3 要抓得住）
#: 段在屏幕上的宽度小于它时**不画端点时间文字**（放不下，画了只会糊成一团）。
#: 用户 2026-10-13: "你这个示意图的时间怎么这么乱" ⇒ 宁可少画也不要糊。
LABEL_MIN_SEG_PX = 64


def _row_top(inner, row: int, fh: int) -> int:
    """第 `row` 行时间文字的**顶边**（相对 inner 顶）——端点抬到它下面。
    3.6.3："时间段端点被抬高"（填充色不抬）。"""
    return 2 + row * fh + fh


def panel_rect(screen_w: int, screen_h: int) -> pygame.Rect:
    """3.1：右侧面板（覆盖地图、到功能栏上沿为止）。"""
    w = max(PANEL_MIN_W, int(screen_w * PANEL_FRAC))
    w = min(w, max(PANEL_MIN_W, screen_w - 120))
    return pygame.Rect(screen_w - w, 0, w, max(120, screen_h - CPH))


def timeline_rect(screen_w: int, screen_h: int) -> pygame.Rect:
    """3.6：时间轴 = **地图左下角 → 编辑区左边**的一条横条，落在**功能栏上方**。

    用户 2026-10-13 的更正："时间轴是放到功能栏上面, 编辑区左侧,
    就是从地图左下角到编辑区边上"。
    ⇒ 它**不压功能栏**（之前按"覆盖功能栏"理解错了），右端止于编辑面板左缘，
      高度贴功能栏上沿。
    """
    r = panel_rect(screen_w, screen_h)
    top = max(0, screen_h - CPH - TIMELINE_H)      # 功能栏**上方**
    return pygame.Rect(0, top, max(80, r.x), TIMELINE_H)


class TimelineWidget:
    """线性时间轴：画 + 交互（3.6.1~3.6.5 / 3.9 / 3.10 / 3.11 / 3.12）。"""

    def __init__(self, model: TimelineModel, on_seek: Optional[Callable] = None,
                 on_change: Optional[Callable] = None, on_pick_segment: Optional[Callable] = None,
                 on_add_segment: Optional[Callable] = None):
        self.m = model
        self.on_seek = on_seek
        self.on_change = on_change
        self.on_pick_segment = on_pick_segment
        self.on_add_segment = on_add_segment
        self.dark = True
        self._drag = None          # ('start'|'end'|'now'|'pan'|'speed', idx, 额外)
        self._last_click = (0, (0, 0))
        self._hover = None

    # ── 坐标 ──
    def _x(self, t: datetime, r: pygame.Rect) -> float:
        return r.x + self.m.frac_of(t) * r.w

    def _t(self, x: float, r: pygame.Rect) -> datetime:
        return self.m.time_at_frac((x - r.x) / max(1, r.w))

    # ── 命中 ──
    def _hit(self, pos, r):
        """返回 (kind, idx)：now / start / end / speed / jump / seg / empty。"""
        if abs(pos[0] - self._x(self.m.now, r)) <= HIT_PX:
            return 'now', -1
        for i, s in enumerate(self.m.segments):
            for kind, t in (('start', s.start), ('end', s.end)):
                if abs(pos[0] - self._x(t, r)) <= HIT_PX:
                    return kind, i
        for i, sp in enumerate(self.m.speeds):
            if abs(pos[0] - self._x(sp.t, r)) <= HIT_PX:
                return 'speed', i
        for i, jp in enumerate(self.m.jumps):
            if abs(pos[0] - self._x(jp.t, r)) <= HIT_PX:
                return 'jump', i
        idx = self.m.find_segment(self._t(pos[0], r))
        if idx >= 0:
            return 'seg', idx
        return 'empty', -1

    # ── 事件（3.6.1~3.6.4 / 3.9 / 3.10）──
    def handle_event(self, e, r: pygame.Rect) -> bool:
        # 3.6.4：变速点数值编辑器打开时**独占键盘**（Enter 提交 / Esc 取消）
        if getattr(self, '_edit_speed', -1) >= 0:
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._commit_speed_editor()
                    return True
                if e.key == pygame.K_ESCAPE:
                    self._edit_speed = -1
                    self._edit_text = ''
                    return True
                if e.key == pygame.K_BACKSPACE:
                    self._edit_text = getattr(self, '_edit_text', '')[:-1]
                    return True
            if e.type == pygame.TEXTINPUT and e.text:
                t = getattr(self, '_edit_text', '')
                if len(t) < 8 and (e.text[0].isdigit() or e.text[0] == '.'):
                    self._edit_text = t + e.text
                return True
            return False
        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if r.collidepoint(mx, my):
                factor = 0.8 if e.y > 0 else 1.25        # 3.6.1 滚轮缩放
                self.m.zoom(factor, self._t(mx, r))
                return True
            return False
        if e.type == pygame.MOUSEMOTION and self._drag:
            self._apply_drag(e.pos, r)
            return True
        if e.type == pygame.MOUSEBUTTONUP and self._drag:
            kind = self._drag[0]
            self._drag = None
            if kind == 'now' and self.on_seek:           # 3.10 拖动结束才渲染实况
                self.on_seek(self.m.now)
            elif self.on_change:
                self.on_change()
            return True
        if e.type != pygame.MOUSEBUTTONDOWN:
            return False
        if not r.collidepoint(e.pos):
            return False
        kind, idx = self._hit(e.pos, r)
        if e.button == 3:                                # 3.6.1 右键拖动
            self._drag = ('pan', 0, e.pos[0])
            return True
        if e.button != 1:
            return True
        now = pygame.time.get_ticks()
        dbl = (now - self._last_click[0] < 320
               and abs(e.pos[0] - self._last_click[1][0]) < 6)
        self._last_click = (now, e.pos)
        if kind == 'now':                                # 3.10 最高优先级
            self._drag = ('now', -1, None)
            return True
        if kind in ('start', 'end'):
            self._drag = (kind, idx, None)
            return True
        if kind == 'speed':                              # 3.6.4 已存在 ⇒ 进入编辑
            if dbl:
                self._open_speed_editor(idx)
                return True
            self._drag = ('speed', idx, None)
            return True
        if kind == 'jump':                               # 3.9 单击 ⇒ 显示从哪里跳过来
            self._sel_jump = -1 if getattr(self, '_sel_jump', -1) == idx else idx
            return True
        if kind == 'seg':
            self.m.selected = idx
            if self.on_pick_segment:                     # 3.6.5 单击段 ⇒ 地图显示范围
                self.on_pick_segment(self.m.segments[idx])
            return True
        if dbl:                                          # 3.6.4 双击加变速点
            sp, existed = self.m.toggle_speed(self._t(e.pos[0], r))
            if sp is not None and not existed:
                idx = self.m.speeds.index(sp)
                self._open_speed_editor(idx)             # "刚创建时直接可编辑"
            if self.on_change:
                self.on_change()
            return True
        seg, _msg = self.m.add_segment(self._t(e.pos[0], r))   # 3.6.2 空白单击建段
        if seg is not None:
            self.m.selected = self.m.segments.index(seg)
            if self.on_add_segment:
                self.on_add_segment(seg)          # 面板据此**补出 target 块**
            if self.on_pick_segment:
                self.on_pick_segment(seg)
        if self.on_change:
            self.on_change()
        return True

    def _apply_drag(self, pos, r) -> None:
        kind, idx, extra = self._drag
        if kind == 'pan':                                # 右键拖动
            if extra is not None:
                dx = pos[0] - extra
                self.m.pan_hours(-dx / max(1, r.w) * self.m.span_h)
                self._drag = ('pan', 0, pos[0])
            return
        t = self._t(pos[0], r)
        if kind == 'now':
            self.m.now = t                               # 拖动中不渲染（由 on_seek 只在松手时触发）
        elif kind in ('start', 'end'):
            self.m.move_endpoint(idx, kind, t)
        elif kind == 'speed' and 0 <= idx < len(self.m.speeds):
            self.m.speeds[idx].t = t
            self.m.speeds.sort(key=lambda s: s.t)

    # ── 绘制 ──
    # ── 3.6.4 变速点数值编辑（"刚创建时直接可编辑"）──
    def _open_speed_editor(self, idx: int) -> None:
        if 0 <= idx < len(self.m.speeds):
            self._edit_speed = idx
            self._edit_text = ('%g' % self.m.speeds[idx].speed)

    def _commit_speed_editor(self) -> None:
        idx = getattr(self, '_edit_speed', -1)
        if 0 <= idx < len(self.m.speeds):
            try:
                v = float(getattr(self, '_edit_text', '') or 1.0)
                self.m.speeds[idx].speed = max(0.01, min(100.0, v))
            except ValueError:
                pass
        self._edit_speed = -1
        self._edit_text = ''
        if self.on_change:
            self.on_change()

    def _draw_speed_editor(self, surface, inner, base_y, seg_h) -> None:
        idx = getattr(self, '_edit_speed', -1)
        if not (0 <= idx < len(self.m.speeds)):
            return
        sp = self.m.speeds[idx]
        px = int(self._x(sp.t, inner))
        box = pygame.Rect(px - 34, base_y - seg_h - 46, 68, 22)
        box.clamp_ip(inner)
        pygame.draw.rect(surface, (40, 44, 54), box, 0, 4)
        pygame.draw.rect(surface, C_SPEED, box, 1, 4)
        surface.blit(rt(f_s, (getattr(self, '_edit_text', '') or '_') + '×',
                        C_SPEED), (box.x + 5, box.y + 3))

    def _blit_label(self, surface, text, left, row, fh, top):
        """画一行时间文字。left 与 row 都是排布阶段算好的，这里不再夹取。"""
        surf = rt(f_s, text, C_TEXT)
        surface.blit(surf, (left, top + (row + 1) * fh))
        return top + (row + 1) * fh

    def draw(self, surface: pygame.Surface, r: pygame.Rect) -> None:
        """时间轴绘制（3.6 / 3.6.2 / 3.6.3 / 3.6.4 / 3.9 / 3.10）。

        用户 2026-10-13 的六条更正全部体现在这里的**竖向布局**上：
          上  : 端点时间文字 x3 行（错落）  —— 3.6.3"时间轴上方的空间正好 3 个文字"
          次上: 刻度线（白，主/次长度不同）
          中  : 日期文字（标在刻度线**下方**）
          下  : 渐变色（矮） + 端点竖线（高，一直升到它那行文字下沿）
        """
        bg = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(bg, C_BG, (0, 0, r.w, r.h), 0, 6)
        surface.blit(bg, r)
        pygame.draw.rect(surface, C_BORDER, r, 2, 6)
        inner = pygame.Rect(r.x + 6, r.y + 6, r.w - 12, r.h - 12)
        fh = f_s.get_height() + 1

        # ── 竖向布局（用户 2026-10-13 三轮更正后的定稿）──
        #   框**上方** : 端点时间文字 3 行（3.6.3"时间轴上方的空间正好够 3 个文字"）
        #   框**内**   : 蓝色渐变（自下而上，底 50% 不透明，75% 高处透明）
        #   框**底部** : 刻度线（三级长度）
        #   刻度线**下方**: 日期文字（用户："日期标在刻度线下方"）
        #   ⚠ 时间段必须在刻度线**上方**（用户："你的时间段为什么在刻度线下面"）
        #   ⚠ 全部**框内**（用户 2026-10-13："时间段太高了! 你都超出背景了"）：
        #     端点文字占框内**上方** 3 行（3.6.3），渐变只占它下面的**默认高度**，
        #     刻度线在渐变下面、日期再下面。
        #   3.6.3 括号里那句是**字面意思**："时间轴上方的空间正好够显示 3 个时间文字"
        #   ⇒ 3 行文字在**框外上方**（地图区域），端点竖线从刻度线升上去够到它们；
        #   框内只放"渐变 + 刻度线 + 日期"。用户 2026-10-13 把这条原文又贴了一遍，
        #   说明我上一版把文字塞进框内是理解错了。
        # 第 3 行的下沿必须**不碰**框顶（留 2px）: rows_y[0] = r.y - 3*fh - 2
        rows_y = [r.y - 3 * fh - 2 + i * fh for i in range(LABEL_ROWS)]
        tick_label_y = inner.bottom - fh - 1                          # 日期：框内最底一行
        ruler_y = tick_label_y - 5                                    # 刻度线在日期上方
        base_y = ruler_y                                              # 渐变底 = 刻度线
        # 3.6.2"默认高度"：渐变只占刻度线以上可用高度的 55%（不是占满整框）
        _avail = max(18, base_y - (inner.y + 6))
        grad_h = int(_avail * 0.45)          # 3.6.2 的"默认高度"
        fill_top = base_y - grad_h

        def _end_top(row: int) -> int:
            # 端点竖线：只比渐变**略高**一点（约 30%）+ 按文字行微错落
            # （行 0 最高、行 2 最低 = "错落使用时间文字与端点高度"）。
            # ⚠ 实测过一版"升到框外文字下沿" ⇒ 60px 高、占框内 57%，用户：
            #   "时间段端点和线又太高了"。现在封顶在框内 55%。
            top = fill_top - int(grad_h * 0.30) + (LABEL_ROWS - 1 - row) * 4
            return max(inner.y + 2, top)

        ticks = make_ticks(self.m.t0(), self.m.t1(), inner.w)

        # ── 端点文字排布：3 行错落，**不丢弃**（3.6.3"超过 3 个就尽量错开的重叠显示"）──
        items = []
        for i, sg in enumerate(self.m.segments):
            # ⚠ **每个**端点都要有时间文字（3.6.3）。用户 2026-10-13：
            #   "好几个时间段根本没有时间文字" —— 之前我按段宽过滤，把窄段的都丢了。
            for t in (sg.start, sg.end):
                x = self._x(t, inner) - inner.x
                if -2 <= x <= inner.w + 2:
                    items.append((x, format_time(t, self.m.span_h)))
        items.sort(key=lambda it: it[0])
        self._last_rows = {i: [] for i in range(LABEL_ROWS)}
        row_x = {}
        row_end = [-10 ** 9] * LABEL_ROWS
        for x, text in items:
            w = rt(f_s, text, C_TEXT).get_width()
            # 左端在这里**一次**夹进框内（绘制时不再夹）—— 夹在排布阶段才能正确占位
            left = min(max(2.0, x - w / 2.0), max(2.0, inner.w - w - 1))
            # 错落规则（3.6.3）：优先放到**放得下**的最上一行；三行都放不下时才挑
            # "已被占到的右端最小"的那行 ⇒ 同一位置的两个文字必然分到不同行。
            # ⚠ 之前用 max(0, left-row_end) 当键，"两行都重叠"时两行都算 0 ⇒ 总选第 0 行，
            #   根本不分行（用户："右侧两个时间文字都很高并且没有避让"）。
            fits = [rr for rr in range(LABEL_ROWS) if left >= row_end[rr] + 4]
            row = fits[0] if fits else min(range(LABEL_ROWS), key=lambda rr: row_end[rr])
            row_end[row] = max(row_end[row], left + w)
            row_x[(round(x, 3), 'start' if True else '')] = row
            row_x[round(x, 3)] = row
            self._last_rows[row].append((inner.x + left, inner.x + left + w))
        # 端点 -> 行号（按时间取；起止可能同 x，取先出现的那行即可）
        seg_rows = []
        for sg in self.m.segments:
            r0 = row_x.get(round(self._x(sg.start, inner) - inner.x, 3), 2)
            r1 = row_x.get(round(self._x(sg.end, inner) - inner.x, 3), 2)
            seg_rows.append((r0, r1))

        # ── 时间段：矮渐变 + 高端点线（3.6.2 / 3.6.3）──
        for i, sg in enumerate(self.m.segments):
            x0 = self._x(sg.start, inner)
            x1 = self._x(sg.end, inner)
            if x1 < inner.x - 4 or x0 > inner.right + 4:
                continue
            x0c, x1c = max(inner.x, x0), min(inner.right, x1)
            w = max(1, int(x1c - x0c))
            col = C_SEG if i != self.m.selected else (120, 190, 255)
            strip = pygame.Surface((w, grad_h), pygame.SRCALPHA)
            # 3.6.2：**底下 50% 不透明度的蓝**，到**（默认）高度的 75%** 处渐变到透明。
            # 行号 yy=0 是 strip 顶 ⇒ 从底往上算：底部 25% 实、往上到 75% 处透明。
            solid_to, fade_to = 0.55, 0.75            # 底 50% 实心 → 75% 高处透明（3.6.2）
            for yy in range(grad_h):
                up = grad_h - 1 - yy                  # 0 = 最底
                k = up / max(1, grad_h - 1)
                if k <= solid_to:
                    a = 128
                else:
                    a = int(128 * max(0.0, 1.0 - (k - solid_to) / (fade_to - solid_to)))
                pygame.draw.line(strip, (*col, a), (0, yy), (w, yy))
            surface.blit(strip, (x0c, base_y - grad_h))
            r0, r1 = seg_rows[i]
            for px, row in ((x0, r0), (x1, r1)):
                pygame.draw.line(surface, C_SEG_EDGE, (px, base_y),
                                 (px, _end_top(row)), 2)

        # ── 刻度线（3.6 白色、主/次长度不同）+ 日期在**下方** ──
        pygame.draw.line(surface, (150, 158, 176), (inner.x, ruler_y), (inner.right, ruler_y), 1)
        for x, level, _t in ticks:
            px = int(inner.x + x)
            if px < inner.x - 1 or px > inner.right + 1:
                continue
            # 三级长度区分（2=带标签级最长, 1=中, 0=短）——
            # 用户 2026-10-13："刻度线这么少我怎么看" ⇒ 短/中线要密，长线要疏。
            ln = 13 if level == 2 else (8 if level == 1 else 4)
            col = C_TEXT if level == 2 else (170, 178, 196)
            pygame.draw.line(surface, col, (px, ruler_y), (px, ruler_y - ln), 1)
        for x, level, text in ticks:
            if level != 2 or not text:
                continue
            px = int(inner.x + x)
            surf = rt(f_s, text, C_TEXT)
            tx = min(max(inner.x + 1, px - surf.get_width() // 2),
                     inner.right - surf.get_width() - 1)
            surface.blit(surf, (tx, tick_label_y))

        # ── 变速点(黄) / jump(绿)：挂在刻度线上（3.6.4 / 3.9）──
        for sp in self.m.speeds:
            px = int(self._x(sp.t, inner))
            if not (inner.x - 8 <= px <= inner.right + 8):
                continue
            pygame.draw.polygon(surface, C_SPEED,
                                [(px, ruler_y - 26), (px + 6, ruler_y - 20),
                                 (px, ruler_y - 14), (px - 6, ruler_y - 20)])
        sel_j = getattr(self, '_sel_jump', -1)
        for i, jp in enumerate(self.m.jumps):
            px = int(self._x(jp.t, inner))
            if not (inner.x - 8 <= px <= inner.right + 8):
                continue
            pygame.draw.polygon(surface, C_JUMP,
                                [(px, ruler_y - 28), (px + 7, ruler_y - 20),
                                 (px, ruler_y - 12), (px - 7, ruler_y - 20)])
            if i == sel_j and jp.src is not None:
                sx = int(self._x(jp.src, inner))
                if not (inner.x - 8 <= sx <= inner.right + 8):
                    continue
                pygame.draw.line(surface, C_JUMP, (sx, base_y - 2), (px, base_y - 2), 2)
                txt = rt(f_s, '从 %s 跳来' % jp.src.strftime('%m-%d %H:%M'), C_JUMP)
                tx = min(max(inner.x + 2, px - txt.get_width() // 2),
                         inner.right - txt.get_width() - 2)
                surface.blit(txt, (tx, base_y - txt.get_height() - 2))

        # ── 当前时间白线（3.10，最高优先级，贯穿整条）──
        nx = int(self._x(self.m.now, inner))
        if inner.x - 2 <= nx <= inner.right + 2:
            pygame.draw.line(surface, C_NOW, (nx, inner.y + 1), (nx, base_y + 2), 2)

        # ── 端点时间文字（3 行错落，标在**上方**）──
        for x, text in items:
            row = row_x.get(round(x, 3), 0)
            w = rt(f_s, text, C_TEXT).get_width()
            left = max(2.0, x - w / 2.0)
            surface.blit(rt(f_s, text, C_TEXT), (inner.x + left, rows_y[row]))

        # 变速点数值编辑器（3.6.4 "刚创建时直接可编辑"）
        self._draw_speed_editor(surface, inner, base_y, grad_h)

    # ── 3.6.4 变速点数值编辑（"刚创建时直接可编辑"）──
    def _open_speed_editor(self, idx: int) -> None:
        if 0 <= idx < len(self.m.speeds):
            self._edit_speed = idx
            self._edit_text = ('%g' % self.m.speeds[idx].speed)

    def _commit_speed_editor(self) -> None:
        idx = getattr(self, '_edit_speed', -1)
        if 0 <= idx < len(self.m.speeds):
            try:
                v = float(getattr(self, '_edit_text', '') or 1.0)
                self.m.speeds[idx].speed = max(0.01, min(100.0, v))
            except ValueError:
                pass
        self._edit_speed = -1
        self._edit_text = ''
        if self.on_change:
            self.on_change()
class ScriptEditorPanel:
    """需求 3.1 的**右侧编辑界面**：文本编辑区 + 时间轴 + 补全按钮。

    · 右侧、宽度 = 窗口 1/4；覆盖地图；底部截止到功能栏上沿；
    · 时间轴贴在窗口最底部 ⇒ 覆盖右 1/4 的功能栏（3.6）；
    · 编辑区实时变化（3.7）：文本改动 → 解析 → 时间轴重建。
    """

    def __init__(self, sim):
        from .script_dialog import _TextArea
        self.sim = sim
        self._TextArea = _TextArea
        self.active = False
        self.text = ''
        self.name = ''
        self.model = TimelineModel()
        self.timeline = TimelineWidget(self.model, on_seek=self._on_seek,
                                       on_change=self._on_change,
                                       on_pick_segment=self._on_pick_segment,
                                       on_add_segment=self._on_add_segment)
        self.status = ''
        self.area = None
        self._rect = pygame.Rect(0, 0, 1, 1)
        self._tl_rect = pygame.Rect(0, 0, 1, 1)
        self._complete_rect = pygame.Rect(0, 0, 1, 1)
        self.dirty = True
        self.target_box = None       # 3.5：地图黄框（由 renderer 读）

    # ── 生命周期 ──
    def toggle(self, text: str = '', name: str = '') -> bool:
        if self.active:
            self.deactivate()
            return False
        self.activate(text, name)
        return True

    def activate(self, text: str = '', name: str = '') -> None:
        self.active = True
        self.text = text
        self.name = name
        self.rebuild()

    def deactivate(self) -> None:
        self.active = False
        if self.area is not None:
            self.text = self.area.get_text()
            self.area.active = False

    # ── 3.7 实时 ──
    def rebuild(self) -> None:
        """重建布局与时间轴模型（文本 → 段）。"""
        sw, sh = self.sim.screen_width, self.sim.screen_height
        self._rect = panel_rect(sw, sh)
        self._tl_rect = timeline_rect(sw, sh)
        r = self._rect
        self._complete_rect = pygame.Rect(r.x + 10, r.y + 8, 74, 26)
        area = pygame.Rect(r.x + 10, r.y + 42, r.w - 20, self._tl_rect.y - r.y - 54)
        if self.area is None or self.area.rect != area:
            self.area = self._TextArea(area, f_s, dark=True)
            self.area.set_text(self.text)
            self.area.on_change = self._on_text_change
        self.area.dark = bool(getattr(self.sim, 'dark_mode', True))
        self.timeline.dark = self.area.dark
        self.refresh_model()
        self.dirty = True

    def refresh_model(self) -> None:
        """文本 → 时间轴段（不覆盖用户已拖出来的段编辑结果由 on_change 负责）。"""
        from .script_timeline import segments_from_script
        segs = segments_from_script(self.text)
        if segs:
            self.model.segments = segs
        if not self.model.speeds and not self.model.jumps:
            self.model.center = (self.model.t0() + timedelta(hours=self.model.span_h / 2))
        if segs:
            first, last = segs[0].start, segs[-1].end
            span = max(24.0 * 3, (last - first).total_seconds() / 3600.0 * 1.6)
            self.model.set_span(max(24.0 * 5, span), anchor=(first + (last - first) / 2))
        self._sync_target_box()
        if self.sim is not None:
            # 当前时间：风季用 `_season_dt()`（sy + st=MMDDHH），正常模式用台风的报点时间
            try:
                if self.sim.md == self.sim.MODE_SEASON:
                    self.model.now = self.sim._season_dt()
                else:
                    ty = self.sim.current_typhoon() if hasattr(self.sim, 'current_typhoon') else None
                    if ty is not None and getattr(ty, 'pts', None):
                        p = ty.cpts()
                        if p and p.get('t'):
                            t = str(p['t'])
                            self.model.now = datetime(int(t[0:4]), int(t[4:6]), int(t[6:8]),
                                                      int(t[8:10]) if len(t) >= 10 else 0)
            except Exception:
                pass

    def _sync_target_box(self) -> None:
        """3.5：脚本里有 target ⇒ 地图上显示它的显示范围黄框。"""
        box = getattr(self.sim, 'script_target_box', None)
        if box is None:
            return
        try:
            from .script_complete import expand_script
            from .script_engine import Script
            exp, errs = expand_script(self.text)
            src = exp if not [e for e in errs if e.level == 'error'] else self.text
            for cmd in Script.parse(src, 'panel').commands:
                tgt = getattr(cmd, 'target', None)
                if tgt is not None:
                    box.set_from_target(tgt.lon, tgt.lat, tgt.width_deg)
                    return
        except Exception:
            return

    def _on_text_change(self) -> None:
        if self.area is not None:
            self.text = self.area.get_text()
        self.dirty = True

    def _on_change(self) -> None:
        """时间轴上的编辑 → 写回脚本文本（3.7 实时；只改 arrive/depart 的时间）。"""
        from .script_timeline import patch_script_segments
        try:
            new = patch_script_segments(self.text, self.model.segments)
        except Exception:
            new = self.text
        if new != self.text:
            self.text = new
            if self.area is not None:
                self.area.set_text(new)
        self.dirty = True

    def _on_seek(self, t: datetime) -> None:
        """3.10：白线拖动结束后才渲染实况。

        跳转走与脚本 `jump` **同一条通路**（`sim.season_ctrl.jump_to`），
        这样时间轴拖动与脚本执行的结果一致（不会出现"脚本能跳、拖动跳不动"）。
        """
        try:
            if self.sim.md == self.sim.MODE_SEASON and hasattr(self.sim, 'season_ctrl'):
                self.sim.season_ctrl.jump_to(t)
            elif hasattr(self.sim, 'set_current_time'):
                ty = self.sim.current_typhoon() if hasattr(self.sim, 'current_typhoon') else None
                if ty is not None:
                    ty.set_current_time(t)
            self.status = '已跳转到 %s' % t.strftime('%Y-%m-%d %H:%M')
        except Exception as exc:
            self.status = '跳转失败: %s' % exc
        self.dirty = True

    def _on_add_segment(self, seg: Segment) -> None:
        """3.6.2 + 3.7：时间轴上新建的段要**落回脚本文本**。

        做法：段数比脚本里的 target 块多 ⇒ 说明有一个段是"时间轴上新建的"，
        给它补一个 target 块（经纬度/宽度取地图黄框的当前值，没有就取默认）。
        只补块、不改已有块 —— 用户在编辑区手写的坐标一个字不动。
        """
        box = getattr(self.sim, 'script_target_box', None)
        if box is not None and getattr(box, 'active', False):
            lon, lat, w = box.to_target()
        else:
            lon, lat, w = 140.0, 20.0, 40.0
        blocks = sum(1 for ln in self.text.splitlines()
                     if ln.strip().startswith(('target ', 'to ')))
        if len(self.model.segments) <= blocks:
            return
        block = ('target %.0fE %.0fN w=%.0f:\n'
                 '    arrive %s\n'
                 '    depart %s\n') % (lon, lat, w,
                                       seg.start.strftime('%Y-%m-%d %H:%M'),
                                       seg.end.strftime('%Y-%m-%d %H:%M'))
        self.text = self.text.rstrip('\n') + '\n' + block
        if self.area is not None:
            self.area.set_text(self.text)
        self.status = '已补出 target 块（%s）' % seg.start.strftime('%m-%d %H:%M')
        self.dirty = True

    def _on_pick_segment(self, seg: Segment) -> None:
        """3.6.5：左键单击一个时间段 ⇒ **地图上随即显示该时间段的显示范围**。

        ⚠ 之前这里只把 (start,end) 记进 `self.target_box`、地图上什么都没发生 ——
        等于这条需求没实现（用户 2026-10-13 让我"回去读一遍要求"时发现的）。
        现在按**段的序号**去脚本里取对应 target 的 lon/lat/w，直接驱动地图黄框。
        """
        box = getattr(self.sim, 'script_target_box', None)
        idx = self.model.segments.index(seg) if seg in self.model.segments else -1
        lon = lat = w = None
        try:
            from .script_complete import expand_script
            from .script_engine import Script
            exp, errs = expand_script(self.text)
            src = exp if not [e for e in errs if e.level == 'error'] else self.text
            tgts = [c.target for c in Script.parse(src, 'panel').commands
                    if getattr(c, 'target', None) is not None]
            if 0 <= idx < len(tgts):
                lon, lat, w = tgts[idx].lon, tgts[idx].lat, tgts[idx].width_deg
        except Exception:
            pass
        if box is not None and lon is not None:
            box.set_from_target(lon, lat, w)          # 地图黄框 = 该段的显示范围
            self.status = '时段 %s → %s ｜ 地图显示 %.1fE %.1fN w=%.0f' % (
                seg.start.strftime('%m-%d %H:%M'), seg.end.strftime('%m-%d %H:%M'),
                lon, lat, w)
        else:
            self.status = '时段 %s → %s' % (seg.start.strftime('%m-%d %H:%M'),
                                           seg.end.strftime('%m-%d %H:%M'))
        self.dirty = True

    # ── 事件 ──
    def handle_event(self, e) -> bool:
        if not self.active:
            return False
        from .script_complete import expand_script
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._complete_rect.collidepoint(e.pos):     # 3.2 补全
                src = self.area.get_text()
                out, errs = expand_script(src)
                hard = [x for x in errs if x.level == 'error']
                if hard:
                    self.status = str(hard[0])[:64]
                else:
                    self.area.set_text(out)
                    self.text = out
                    self.status = '已补全 ✓'
                    self.refresh_model()
                self.dirty = True
                return True
        if self.timeline.handle_event(e, self._tl_rect):
            self.rebuild_after_tl()
            return True
        if self.area is not None and self.area.handle_event(e):
            return True
        # 面板内的其它点击不穿透到地图
        if e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and \
                self._rect.collidepoint(getattr(e, 'pos', (-1, -1))):
            return True
        return False

    def rebuild_after_tl(self) -> None:
        if self.area is not None:
            self.text = self.area.get_text()
        self.dirty = True

    # ── 绘制 ──
    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        r = self._rect
        dark = bool(getattr(self.sim, 'dark_mode', True))
        bg = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(bg, (24, 27, 35, 246) if dark else (246, 248, 252, 246),
                         (0, 0, r.w, r.h), 0, 0)
        surface.blit(bg, r)
        pygame.draw.line(surface, (92, 100, 118) if dark else (150, 158, 172),
                         (r.x, r.y), (r.x, r.bottom), 2)
        txt_col = (226, 232, 244) if dark else (34, 38, 48)
        # 标题 + 补全按钮
        title = rt(f_m, '脚本可视化编辑', txt_col)
        surface.blit(title, (r.x + self._complete_rect.w + 20, r.y + 12))
        pygame.draw.rect(surface, (70, 120, 190), self._complete_rect, 0, 5)
        surface.blit(rt(f_s, '补全', (255, 255, 255)),
                     (self._complete_rect.x + 22, self._complete_rect.y + 5))
        # 文本编辑区
        if self.area is not None:
            self.area.draw(surface)
        # 状态行
        if self.status:
            surface.blit(rt(f_s, self.status, (150, 200, 150)), (r.x + 10, r.bottom + 4))

    def draw_overlay(self, surface: pygame.Surface) -> None:
        """时间轴单独一个绘制入口（画在功能栏之后，确保不被任何 UI 盖住）。

        用户 2026-10-13 更正：时间轴 = "地图左下角 → 编辑区左边、功能栏**上方**"
        的一条横条 ⇒ **不压功能栏**；仍在功能栏绘制之后补画一次更保险
        （Renderer.draw 的顺序是 场景图层 → draw_control_panel → 对话框）。
        """
        if not self.active:
            return
        self.timeline.draw(surface, self._tl_rect)
