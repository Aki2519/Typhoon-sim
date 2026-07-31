# py/ty_sim_mixins/draw_info_boxes_mixin.py
"""风季台风信息框 Mixin + 合并：季节时钟、ACE、控制面板。"""
from __future__ import annotations
import math
import os
from datetime import datetime
import pygame
from ..constants import (
    f_s, f_m, f_19, rt, TXT,
    INFO_BOX_BG, INFO_BOX_BORDER,
    SEASON_CLOCK_BG, SEASON_CLOCK_BORDER, SEASON_CLOCK_QUARTER,
    HEMISPHERE_NORTH,
    SEASON_INFO_BOX_WIDTH,
    SEASON_INFO_BOX_START_X, SEASON_INFO_BOX_START_Y,
    SEASON_INFO_BOXES_PER_COL, SEASON_INFO_BOX_MAX_COLS,
    SEASON_INFO_BOX_SPACING_X, SEASON_INFO_BOX_SPACING_Y,
)
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..utils import max_wind_from_points
from ..control_panel import ControlPanel

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'font')
_font_date = pygame.font.Font(os.path.join(_FONT_DIR, FONT_FILE), 54)
_font_sub = SmartFont(_load_font(FONT_FILE, 27, 27), _load_font(FONT_FILE, 27, 27))
_font_month = pygame.font.Font(os.path.join(_FONT_DIR, FONT_FILE), 30)
_font_box = SmartFont(_load_font(FONT_FILE, 18, 18), _load_font(FONT_FILE, 18, 18))
_font_ace_title = SmartFont(_load_font(FONT_FILE, 18, 18), _load_font(FONT_FILE, 18, 18))
_font_ace = SmartFont(_load_font(FONT_FILE, 34, 34), _load_font(FONT_FILE, 34, 34))
_font_ace_note = SmartFont(_load_font(FONT_FILE, 25, 25), _load_font(FONT_FILE, 25, 25))

_BOX_PAD = 4

_MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
_STROKE = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]

_ACE_NOTE_DURATION_MS = 2500
_ACE_NOTE_FADE_MS = 500

# ── 描边文字缓存：key=(字体id, 文本, 颜色) → 已合成描边 Surface ──
_stroked_cache: dict = {}
_STROKED_CACHE_MAX = 128


def _stroked(font, text: str, color) -> pygame.Surface:
    """黑色 8 向描边 + 前景色文字，整体合成并缓存（锚点 = 原文字位置 -1,-1）。"""
    key = (id(font), text, color)
    surf = _stroked_cache.get(key)
    if surf is None:
        fg = font.render(text, True, color)
        bk = font.render(text, True, (0, 0, 0))
        surf = pygame.Surface((fg.get_width() + 2, fg.get_height() + 2), pygame.SRCALPHA)
        for dx, dy in _STROKE:
            surf.blit(bk, (dx + 1, dy + 1))
        surf.blit(fg, (1, 1))
        if len(_stroked_cache) >= _STROKED_CACHE_MAX:
            _stroked_cache.pop(next(iter(_stroked_cache)))
        _stroked_cache[key] = surf
    return surf


# ── 淡入淡出半透明副本缓存：alpha 量化 16 档，避免每帧 copy ──
_faded_cache: dict = {}
_FADED_CACHE_MAX = 64


def _faded_copy(surf: pygame.Surface, alpha: int) -> pygame.Surface:
    bucket = max(0, min(15, alpha // 16))
    key = (id(surf), bucket)
    out = _faded_cache.get(key)
    if out is None:
        out = surf.copy()
        out.set_alpha(bucket * 16 + 15)
        if len(_faded_cache) >= _FADED_CACHE_MAX:
            _faded_cache.pop(next(iter(_faded_cache)))
        _faded_cache[key] = out
    return out


class TySimDrawInfoBoxesMixin:
    """台风季模式下的多台风信息框 + 季节时钟 + ACE + 控制面板。"""

    _season_info_box_cache: dict = {}
    _season_info_box_last_data: dict = {}
    _season_box_line_h: int = 0
    _clock_arc_surf = None

    @classmethod
    def _season_info_box_line_height(cls) -> int:
        if not cls._season_box_line_h:
            cls._season_box_line_h = _font_box.render(
                "总ACE:0.0000 实时ACE:0.0000", True, (255, 255, 255)).get_height()
        return cls._season_box_line_h

    @classmethod
    def _season_info_box_height(cls) -> int:
        """信息框高度：上下边距相同，5 行文字。"""
        return _BOX_PAD * 2 + 5 * cls._season_info_box_line_height()

    def _render_info_box(self, ty, box_w: int, box_h: int):
        """渲染信息框主体（不含实时ACE数字）。
        返回 (box, ace_x, ace_y, tc)：数字由调用方每帧单独绘制。"""
        # ── 检查缓存 ──
        cp = ty.cp()
        dark = getattr(self, 'dark_mode', True)
        key_data = (
            ty.b, ty.n, ty.tace,
            cp['w'] if cp else 0,
            cp['st'] if cp else '',
            cp['p'] if cp else 0,
            ty.ci, dark,
        )
        if ty in self._season_info_box_cache and self._season_info_box_last_data.get(ty) == key_data:
            return self._season_info_box_cache[ty]

        box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)

        # 暗色主题样式
        if dark:
            bg = (22, 28, 44, 180)
            border = (55, 85, 130)
            tc = (215, 225, 245)
        else:
            bg = INFO_BOX_BG
            border = INFO_BOX_BORDER
            tc = TXT

        pygame.draw.rect(box, bg, (0, 0, box_w, box_h), 0, 8)

        # 左侧强度竖条（当前风速颜色）
        if cp and 'color' in cp:
            bar_c = cp['color']
            if len(bar_c) == 3:
                bar_c = (*bar_c, 220)
            pygame.draw.rect(box, bar_c, (0, 0, 8, box_h), 0, 12, 0, 0, 12)

        pygame.draw.rect(box, border, (0, 0, box_w, box_h), 2, 12)

        fp = ty.pts[0]
        lp = ty.pts[-1]
        tyy = fp['t'][:4] if len(fp['t']) >= 4 else "未知"
        if getattr(self, 'point_name_mode', False) and cp:
            tn = (cp.get('name') or '').strip()
            if not tn:
                tn = self.get_display_name(ty)
        else:
            tn = self.get_display_name(ty)

        st = fp['t']
        et = lp['t']
        sf = f"{st[4:6]}/{st[6:8]}" if len(st) >= 8 else "未知"
        ef = f"{et[4:6]}/{et[6:8]}" if len(et) >= 8 else "未知"
        if len(st) >= 10 and len(et) >= 10:
            try:
                s_dt = datetime(int(st[:4]), int(st[4:6]), int(st[6:8]), int(st[8:10]))
                e_dt = datetime(int(et[:4]), int(et[4:6]), int(et[6:8]), int(et[8:10]))
                td = (e_dt - s_dt).days
            except Exception:
                td = 0
        else:
            td = 0

        max_wind = max_wind_from_points(ty.pts)
        current_wind = cp['w'] if cp else "?"
        cur_st = (cp['st'].upper() if cp and cp['st'] else '-') if cp else '-'
        cur_pres = cp['p'] if cp else 0

        lines = [
            f"{ty.basin}{ty.n} {tyy} {tn}",
            f"{sf}-{ef} ({td}天)",
            f"巅峰:{max_wind}kt 实时:{current_wind}kt",
            f"气压:{cur_pres}hPa" if cur_pres else "气压:-",
            f"总ACE:{ty.tace:.4f} 实时ACE:",
        ]
        line_h = self._season_info_box_line_height()
        text_x = 16
        y = _BOX_PAD
        st_surf = rt(_font_box, cur_st, tc)
        ace_x = ace_y = 0
        for i, ln in enumerate(lines):
            max_w = box_w - text_x - 8
            if i == 0:
                # 第一行最右边：当前性质（首行文字避让）
                max_w -= st_surf.get_width() + 8
                box.blit(st_surf, (box_w - 10 - st_surf.get_width(), y))
            surf_ln = rt(_font_box, ln, tc, max_w)
            box.blit(surf_ln, (text_x, y))
            if i == 4:
                ace_x = text_x + surf_ln.get_width()
                ace_y = y
            y += line_h

        # ── 存入缓存 ──
        result = (box, ace_x, ace_y, tc)
        self._season_info_box_cache[ty] = result
        self._season_info_box_last_data[ty] = key_data
        return result

    _BOX_FADE_MS = 400

    def _box_alpha(self, ty, now: float) -> int:
        st = self._box_anim.get(ty)
        if st is None:
            return 255
        t = now - st['start']
        if st['state'] == 'in':
            a = st['from'] + 255.0 * t / self._BOX_FADE_MS
            if a >= 255:
                self._box_anim.pop(ty, None)
                return 255
        else:
            a = st['from'] - 255.0 * t / self._BOX_FADE_MS
        return max(0, min(255, int(a)))

    def draw_season_info_boxes(self, surface: pygame.Surface) -> None:
        now = pygame.time.get_ticks()
        if not hasattr(self, '_box_anim'):
            self._box_anim = {}
        # 仅活跃台风显示信息框（act=True, ss=True, sf=False）
        active_typhoons = [t for t in self.tys if t.act and t.ss and not t.sf]

        # 已结束的台风：先淡出，淡出完成后释放 slot
        for ty in list(self.info_box_slots.keys()):
            if ty in active_typhoons:
                continue
            st = self._box_anim.get(ty)
            if st is None or st['state'] != 'out':
                self._box_anim[ty] = {'state': 'out', 'start': now,
                                      'from': self._box_alpha(ty, now)}
            elif self._box_alpha(ty, now) <= 0:
                slot = self.info_box_slots.pop(ty)
                self.info_box_free_slots.append(slot)
                self.info_box_free_slots.sort()
                self._season_info_box_cache.pop(ty, None)
                self._season_info_box_last_data.pop(ty, None)
                self._box_anim.pop(ty, None)

        # 分配新 slot（淡入）
        for ty in active_typhoons:
            if ty not in self.info_box_slots:
                if self.info_box_free_slots:
                    slot = self.info_box_free_slots.pop(0)
                    self.info_box_slots[ty] = slot
                    self._box_anim[ty] = {'state': 'in', 'start': now, 'from': 0}
            else:
                st = self._box_anim.get(ty)
                if st and st['state'] == 'out':
                    self._box_anim[ty] = {'state': 'in', 'start': now,
                                          'from': self._box_alpha(ty, now)}

        box_w, box_h = SEASON_INFO_BOX_WIDTH, self._season_info_box_height()
        per_col = SEASON_INFO_BOXES_PER_COL
        start_x = SEASON_INFO_BOX_START_X
        start_y = SEASON_INFO_BOX_START_Y
        spacing_x, spacing_y = SEASON_INFO_BOX_SPACING_X, SEASON_INFO_BOX_SPACING_Y

        for ty, slot in self.info_box_slots.items():
            c = slot // per_col
            r = slot % per_col
            if c >= SEASON_INFO_BOX_MAX_COLS:
                continue
            alpha = self._box_alpha(ty, now)
            if alpha <= 0:
                continue
            x = start_x + c * (box_w + spacing_x)
            y = start_y + r * (box_h + spacing_y)

            box, ace_x, ace_y, tc = self._render_info_box(ty, box_w, box_h)
            # 实时ACE数字：每帧单独渲染（ASCII，用底层字体避免污染 SmartFont 缓存）
            digits = _font_box.en_font.render(f"{ty.cace:.4f}", True, tc)
            if alpha < 255:
                # 从左侧滑入/滑出（缓出）+ 淡入淡出
                t = alpha / 255.0
                ease = 1.0 - (1.0 - t) ** 3
                x = int(x - (x + box_w) * (1.0 - ease))
                faded = _faded_copy(box, alpha)
                surface.blit(faded, (x, y))
                d = digits.copy()
                d.set_alpha(alpha)
                surface.blit(d, (x + ace_x, y + ace_y))
            else:
                surface.blit(box, (x, y))
                surface.blit(digits, (x + ace_x, y + ace_y))

    def draw_season_clock(self, surface: pygame.Surface) -> None:
        if TySimDrawInfoBoxesMixin._clock_arc_surf is None:
            TySimDrawInfoBoxesMixin._clock_arc_surf = pygame.Surface((240, 240), pygame.SRCALPHA)
        tr = 80
        inner_r = 56
        cx = cy = 120
        arc_inner = inner_r + 5
        arc_outer = tr - 2

        ste = getattr(self, 'ste', 0)
        day_seconds = ste % (24 * 3600)
        progress = day_seconds / (24 * 3600)

        # 外环 + 内环（白色空心）
        pygame.draw.circle(surface, (255, 255, 255), (cx, cy), tr, 2)
        pygame.draw.circle(surface, (255, 255, 255), (cx, cy), inner_r, 2)

        # 进度弧（多边形精确填充，两环之间留缝隙）
        if progress > 0.001:
            arc_surf = self._clock_arc_surf
            arc_surf.fill((0, 0, 0, 0))
            steps = max(4, int(progress * 120))
            pts_out, pts_in = [], []
            start_a = -math.pi / 2
            step_angle = progress * 2.0 * math.pi / steps
            for i in range(steps + 1):
                a = start_a + i * step_angle
                pts_out.append((cx + arc_outer * math.cos(a), cy + arc_outer * math.sin(a)))
            for i in range(steps, -1, -1):
                a = start_a + i * step_angle
                pts_in.append((cx + arc_inner * math.cos(a), cy + arc_inner * math.sin(a)))
            pygame.draw.polygon(arc_surf, (255, 255, 255), pts_out + pts_in)
            surface.blit(arc_surf, (0, 0))

        # 年份（上方，紧贴外环）
        year_str = str(self.sy)
        year_surf = _stroked(_font_sub, year_str, (255, 255, 255))
        yx = cx - (year_surf.get_width() - 2) // 2 - 1
        yy = cy - tr - (year_surf.get_height() - 2) - 1
        surface.blit(year_surf, (yx, yy))

        # 月份 + 日期（偏下）
        month_idx = int(self.st[0:2])
        month_str = _MONTHS[month_idx] if 1 <= month_idx <= 12 else self.st[0:2]
        day_str = str(int(self.st[2:4]))
        month_surf = _stroked(_font_month, month_str, (255, 255, 255))
        day_surf = _stroked(_font_date, day_str, (255, 255, 255))
        gap_md = 2
        month_h = month_surf.get_height() - 2
        day_h = day_surf.get_height() - 2
        total_md_h = month_h + day_h + gap_md
        md_top = cy - total_md_h // 2
        mx = cx - (month_surf.get_width() - 2) // 2
        dx = cx - (day_surf.get_width() - 2) // 2
        dy_s = md_top + month_h + gap_md
        surface.blit(month_surf, (mx - 1, md_top - 1))
        surface.blit(day_surf, (dx - 1, dy_s - 1))

        # 时分（下方）
        hour = int(day_seconds / 3600)
        minute = int((day_seconds % 3600) / 60)
        time_str = f"{hour:02d}{minute:02d}Z"
        time_surf = _stroked(_font_sub, time_str, (255, 255, 255))
        text_x = cx - (time_surf.get_width() - 2) // 2 - 1
        text_y = cy + tr + 5 - 1
        surface.blit(time_surf, (text_x, text_y))

    def draw_control_panel(self, surface) -> None:
        if not hasattr(self, '_panel') or self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.build()
        self._panel.draw(surface)

    @property
    def control_panel(self):
        if not hasattr(self, '_panel') or self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.build()
        return self._panel

    def draw_ace_display(self, surface):
        if self.md == self.MODE_NORMAL or self.md == self.MODE_EDIT:
            ty = self.current_typhoon() if self.md == self.MODE_NORMAL else self.edit_typhoon
            if ty and ty.pts:
                self._draw_progress(surface, f"{self.get_display_name(ty)} ACE:",
                                    ty.cace, ty.tace, self.screen_width - 10)
            return

        ace_year = self.current_ace_year
        year_str = str(ace_year) if self.hemisphere == HEMISPHERE_NORTH else f"{ace_year}-{ace_year + 1}"

        lm = self.ace_limit_mode
        bc = self.ace_limit_basin
        if lm == 'basin' and bc:
            area = self.res_mgr.ocean_areas.get_by_code(bc)
            label = f"{year_str} {area.name_full if area else bc} ACE:"
        else:
            label = f"{year_str} ACE:"

        cya = self.yad.get(ace_year, 0.0)
        self._draw_progress(surface, label, self.csa, cya, self.screen_width - 10)

    def _draw_progress(self, surface, label, current_ace, total_ace, right):
        w, h = 495, 45
        x = right - w

        title = _stroked(_font_ace_title, "Accumulated Cyclone Energy", (200, 200, 210))
        surface.blit(title, (x - 1, 8 - 1))
        pygame.draw.rect(surface, (255, 255, 255), (x, 32, w, h), 3)
        if total_ace > 0:
            fw = int(w * min(1.0, current_ace / total_ace))
            pygame.draw.rect(surface, (255, 200, 0), (x, 32, fw, h))

        # 数值每帧变化（插值模式），直接用底层字体渲染，避免污染 SmartFont 缓存
        val = f"{current_ace:.4f}"
        vf = _font_ace.en_font
        black = vf.render(val, True, (0, 0, 0))
        white = vf.render(val, True, (255, 255, 255))
        tw, th = white.get_width(), white.get_height()
        text_x = x + w - tw - 12
        text_y = 32 + (h - th) // 2
        for dx, dy in _STROKE:
            surface.blit(black, (text_x + dx, text_y + dy))
        surface.blit(white, (text_x, text_y))

        self._draw_ace_note(surface, x, 32, h)

        if getattr(self, 'show_ace_total', True):
            ls = _stroked(_font_ace, label, (255, 255, 255))
            surface.blit(ls, (right - ls.get_width() + 1, 89 - 1))
            ys = _stroked(_font_ace, f"{total_ace:.4f}", (255, 255, 255))
            surface.blit(ys, (right - ys.get_width() + 1, 137 - 1))

    def _draw_ace_note(self, surface, bar_x, bar_y, bar_h):
        """在 ACE 进度条内左侧显示最近结束台风的 '台风名 +ACE'。"""
        note = getattr(getattr(self, 'playback_ctrl', None), 'ace_note', None)
        if not note:
            return
        elapsed = pygame.time.get_ticks() - note['time']
        if elapsed >= _ACE_NOTE_DURATION_MS:
            return
        txt = (f"{note['name']} " if note['name'] else "") + f"+{note['ace']:.4f}"
        white = rt(_font_ace_note, txt, (255, 255, 255))
        colored = rt(_font_ace_note, txt, note['color'])
        remain = _ACE_NOTE_DURATION_MS - elapsed
        if remain < _ACE_NOTE_FADE_MS:
            alpha = max(0, int(255 * remain / _ACE_NOTE_FADE_MS))
            white = white.copy(); white.set_alpha(alpha)
            colored = colored.copy(); colored.set_alpha(alpha)
        nx = bar_x + 10
        ny = bar_y + (bar_h - white.get_height()) // 2
        for dx, dy in _STROKE:
            surface.blit(colored, (nx + dx, ny + dy))
        surface.blit(white, (nx, ny))