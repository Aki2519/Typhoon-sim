# app/sim_time_dialog.py
"""F1: 模拟起始时间对话框(模拟模式)。

任意年份(1700-2300)+ 时次(00/06/12/18)+ 环境场重建步长(1/3/6/12/24h)。
与 TimeJump 同一套交互(InputField / Tab 切换 / Enter 确认 / ESC 取消),
只是目标是"重设模拟起始时刻"而不是"跳到风季某天"。
"""
from __future__ import annotations

from datetime import datetime

import pygame

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED,
    DIALOG_TITLE_BAR_HEIGHT, SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
)
from .input_field import InputField
from .dialog_base import DraggableDialog

_W, _H = 460, 400
_LABELS = ('年份:', '月份:', '日期:', '时次:')
#: 一步到位的示例年份(覆盖"任意时间"的三个典型场景)
_PRESETS = (('最新实况', 'latest'), ('今天', None), ('1950 历史', 1950),
            ('1850 历史', 1850), ('2100 情景', 2100))
#: 环境场重建步长档位 —— **必须与 core.ENV_STEPS 一致**。
#: 这里原来写死 (1,3,6,12,24), 而内核 2026-09-17 起上限压到 6 h, 于是
#: 点 12h/24h 会被 snap_env_step 静默吸附回 6h(按钮"跳"回 6h, 像是没反应)。
def _steps() -> tuple:
    """延迟导入内核档位表(app 层惯例: 不在模块导入期拉 numpy)。"""
    try:
        from simulator.simcore.core import ENV_STEPS
        return tuple(ENV_STEPS)
    except Exception:      # pragma: no cover - 内核缺席时退回已知档位
        return (1.0, 3.0, 6.0)


class SimTimeDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.fields = []
        self.title = rt(f_m, "模拟起始时间", TXT)
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))
        self._hint = rt(f_s, "Tab 切换字段 / Enter 确认 / ESC 取消", TXT)
        self._hint_dark = rt(f_s, "Tab 切换字段 / Enter 确认 / ESC 取消", SETTINGS_TEXT_DIM)
        self._preset_rects = {}
        self._step_rects = {}
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

    # ── 生命周期 ──
    def activate(self):
        super().activate()
        dx = (self.sim.screen_width - _W) // 2
        dy = (self.sim.screen_height - _H) // 2
        self.dialog_rect = pygame.Rect(dx, dy, _W, _H)
        self.bg_rect = self.dialog_rect
        st = getattr(self.sim, 'sim_start_dt', None) or datetime.now()
        self._fill_fields(st.year, st.month, st.day, st.hour)
        self.fields[0].activate()
        self.dragging = False

    def deactivate(self):
        for f in self.fields:
            f.deactivate()
        self.fields.clear()
        super().deactivate()
        self.dragging = False

    def _field_rect(self, i):
        return pygame.Rect(self.dialog_rect.x + 130,
                           self.dialog_rect.y + 70 + i * 40, 120, 24)

    def _fill_fields(self, y, m, d, h):
        vals = ('%04d' % y, '%02d' % m, '%02d' % d, '%02d' % h)
        if not self.fields:
            for i, v in enumerate(vals):
                fld = InputField(self._field_rect(i), max_length=4,
                                 dark=self.dark_mode)
                fld.set_text(v)
                self.fields.append(fld)
        else:
            for fld, v in zip(self.fields, vals):
                fld.set_text(v)
                fld.deactivate()

    # ── 绘制 ──
    def draw(self, surface):
        if not self.active:
            return
        r = self.dialog_rect
        if self.dark_mode:
            self.draw_dark_overlay(surface)
            self.draw_dark_panel(surface, r)
            self.draw_dark_title(surface, "模拟起始时间", r)
            label_color = SETTINGS_TEXT_LIGHT
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=20)
            label_color = TXT
        for i, fld in enumerate(self.fields):
            fld.rect = self._field_rect(i)
            fld.draw(surface)
            lb = rt(f_s, _LABELS[i], label_color)
            surface.blit(lb, (r.x + 30, r.y + 70 + i * 40 + 3))
        surface.blit(self._hint_dark if self.dark_mode else self._hint,
                     (r.x + 30, r.y + _H - 30))
        # 预设年份
        self._preset_rects = {}
        y0 = r.y + 240
        for k, (name, yr) in enumerate(_PRESETS):
            col, row = k % 3, k // 3
            br = pygame.Rect(r.x + 30 + col * 136, y0 + row * 32, 126, 26)
            if self.dark_mode:
                self.draw_dark_button(surface, br, name)
            else:
                self.draw_button(surface, (br.x, br.y, br.w, br.h),
                                 rt(f_s, name, TXT), BUTTON_BORDER)
            self._preset_rects[k] = br
        # 环境步长
        sy = y0 + 2 * 32 + 6
        lb = rt(f_s, "环境步长:", label_color)
        surface.blit(lb, (r.x + 30, sy + 4))
        self._step_rects = {}
        cur = float(getattr(self.sim, 'sim_env_step_h', 6.0))
        for k, h in enumerate(_steps()):
            br = pygame.Rect(r.x + 130 + k * 58, sy, 52, 26)
            armed = abs(h - cur) < 1e-6
            if self.dark_mode:
                self.draw_dark_button(surface, br, '%gh' % h, accent=armed)
            else:
                self.draw_button(surface, (br.x, br.y, br.w, br.h),
                                 rt(f_s, '%gh' % h, TXT),
                                 BUTTON_BORDER if not armed else (90, 160, 230))
            self._step_rects[k] = br
        # 确认/取消
        ok = pygame.Rect(r.x + 120, r.y + _H - 62, 84, 30)
        no = pygame.Rect(r.x + 250, r.y + _H - 62, 84, 30)
        if self.dark_mode:
            self.draw_dark_button(surface, ok, "确认", accent=True)
            self.draw_dark_button(surface, no, "取消")
        else:
            self.draw_button(surface, (ok.x, ok.y, ok.w, ok.h),
                             self.confirm_text, BUTTON_BORDER)
            self.draw_button(surface, (no.x, no.y, no.w, no.h),
                             self.cancel_text, BUTTON_DISABLED)
        self._ok_rect, self._no_rect = ok, no

    # ── 事件 ──
    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            for i, fld in enumerate(self.fields):
                fld.rect = self._field_rect(i)
            return True
        r = self.dialog_rect
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            for k, br in self._preset_rects.items():
                if br.collidepoint(x, y):
                    yr = _PRESETS[k][1]
                    if yr == 'latest':
                        # 用户 2026-09-19: 默认就该是"最后一个有实测模态的时间"
                        from simulator.simcore.core import latest_real_start
                        d = latest_real_start(6.0)
                        self._fill_fields(d.year, d.month, d.day, d.hour)
                        return True
                    if yr is None:
                        now = datetime.now()
                        self._fill_fields(now.year, now.month, now.day, now.hour)
                    else:
                        f = self.fields
                        try:
                            mm = max(1, min(12, int(f[1].get_text())))
                            dd = max(1, min(28, int(f[2].get_text())))
                        except ValueError:
                            mm, dd = 9, 1
                        self._fill_fields(yr, mm, dd, 0)
                    return True
            for k, br in self._step_rects.items():
                if br.collidepoint(x, y):
                    _st = _steps()
                    self.sim._sim_set_env_step(_st[k], reset=False)
                    return True
            if getattr(self, '_ok_rect', None) and self._ok_rect.collidepoint(x, y):
                if self._apply():
                    self.deactivate()
                return True
            if getattr(self, '_no_rect', None) and self._no_rect.collidepoint(x, y):
                self.deactivate()
                return True
            for fld in self.fields:
                if fld.rect.collidepoint(e.pos):
                    for g in self.fields:
                        g.deactivate()
                    fld.activate_at(e.pos[0])
                    return True
            if r.collidepoint(x, y):
                return True
            self.deactivate()
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_RETURN:
                if self._apply():
                    self.deactivate()
                return True
            if e.key in (pygame.K_TAB, pygame.K_KP_ENTER):
                idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                delta = -1 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                nxt = (idx + delta) % len(self.fields) if idx != -1 else 0
                if idx != -1:
                    self.fields[idx].deactivate()
                self.fields[nxt].activate()
                return True
        for fld in self.fields:
            if fld.handle_event(e):
                return True
        return False

    def _apply(self) -> bool:
        try:
            y, m, d, h = (int(fld.get_text()) for fld in self.fields)
        except ValueError:
            self.sim.show_error("请输入有效的数字")
            return False
        if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= h <= 23):
            self.sim.show_error("日期或时次超出范围")
            return False
        try:
            target = datetime(y, m, d, h)
        except ValueError:
            self.sim.show_error("无效的日期")
            return False
        self.sim._sim_set_start(target)
        return True
