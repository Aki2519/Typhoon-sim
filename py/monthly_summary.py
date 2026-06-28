# py/monthly_summary.py
"""台风季月度总结弹窗：按月统计风暴、台风、MH、C5、ACE、最强风暴。"""
import pygame

from .constants import f_s, rt, TXT, INFO_BOX_BG, INFO_BOX_BORDER

BOX_W = 240
BOX_H = 160
SLIDE_DURATION = 0.3
VISIBLE_DURATION = 5.0
BOX_Y = 115

_MONTH_NAMES = {
    1: "1月", 2: "2月", 3: "3月", 4: "4月", 5: "5月", 6: "6月",
    7: "7月", 8: "8月", 9: "9月", 10: "10月", 11: "11月", 12: "12月",
}


class MonthlySummary:
    HIDDEN = 0
    SLIDING_IN = 1
    VISIBLE = 2
    SLIDING_OUT = 3

    def __init__(self, sim):
        self.sim = sim
        self.state = self.HIDDEN
        self._anim_progress = 0.0
        self._visible_timer = 0.0
        self._data = {}
        self._cached_surf = None

    @property
    def active(self):
        return self.state != self.HIDDEN

    def _month_str(self):
        m = self._data.get('month', 1)
        return _MONTH_NAMES.get(m, f"{m}月")

    def trigger(self, year: int, month: int):
        if not self.sim.monthly_summary:
            return
        self._compute(year, month)
        self.state = self.SLIDING_IN
        self._anim_progress = 0.0
        self._cached_surf = None

    def update(self, dt: float):
        if self.state == self.HIDDEN:
            return
        if self.state == self.SLIDING_IN:
            self._anim_progress += dt / SLIDE_DURATION
            if self._anim_progress >= 1.0:
                self._anim_progress = 1.0
                self.state = self.VISIBLE
                self._visible_timer = 0.0
        elif self.state == self.VISIBLE:
            self._visible_timer += dt
            if self._visible_timer >= VISIBLE_DURATION:
                self.state = self.SLIDING_OUT
        elif self.state == self.SLIDING_OUT:
            self._anim_progress -= dt / SLIDE_DURATION
            if self._anim_progress <= 0.0:
                self._anim_progress = 0.0
                self.state = self.HIDDEN

    def draw(self, surface: pygame.Surface):
        if self.state == self.HIDDEN:
            return

        right = self.sim.screen_width - 10
        target_x = right - BOX_W

        t = self._anim_progress
        if self.state in (self.SLIDING_IN, self.VISIBLE):
            eased = 1.0 - (1.0 - t) ** 3
        else:
            eased = t ** 3

        x = int(self.sim.screen_width + 10 - (self.sim.screen_width + 10 - target_x) * eased)

        if self._cached_surf is None:
            self._cached_surf = self._build_surface()

        bg = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
        alpha = int(255 * eased)
        bg.set_alpha(alpha)
        bg.blit(self._cached_surf, (0, 0))
        surface.blit(bg, (x, BOX_Y))

    def _compute(self, year: int, month: int):
        engine = self.sim.ace_engine
        month_str = f"{month:02d}"

        storms = set()
        ts_storms = set()
        mh_storms = set()
        c5_storms = set()
        total_ace = 0.0
        strongest = (None, 0)

        for ty in self.sim.tys:
            in_month = False
            max_w = 0
            tid = id(ty)
            for p in ty.pts:
                if not engine.point_in_limit(p['la'], p['lo']):
                    continue
                t = p['t']
                if len(t) < 8:
                    continue
                if t[4:6] != month_str:
                    continue
                try:
                    py = int(t[:4])
                except ValueError:
                    continue
                if py != year:
                    continue
                in_month = True
                w = p['w']
                pace = p.get('pace', 0.0)
                total_ace += pace
                if w > max_w:
                    max_w = w
                if w >= 34:
                    ts_storms.add(tid)
                if w >= 96:
                    mh_storms.add(tid)
                if w >= 137:
                    c5_storms.add(tid)
            if in_month:
                storms.add(tid)
                name = self.sim.get_display_name(ty)
                if max_w > strongest[1]:
                    strongest = (name, max_w)

        self._data = {
            'year': year,
            'month': month,
            'storms': len(storms),
            'typhoons': len(ts_storms),
            'mh': len(mh_storms),
            'c5': len(c5_storms),
            'ace': total_ace,
            'strongest_name': strongest[0],
            'strongest_wind': strongest[1],
        }

    def _build_surface(self):
        d = self._data
        surf = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
        pygame.draw.rect(surf, INFO_BOX_BG, (0, 0, BOX_W, BOX_H), 0, 10)
        pygame.draw.rect(surf, INFO_BOX_BORDER, (0, 0, BOX_W, BOX_H), 2, 10)

        title = f"━━ {d['year']}年{self._month_str()} 总结 ━━"
        title_surf = rt(f_s, title, TXT)
        surf.blit(title_surf, ((BOX_W - title_surf.get_width()) // 2, 8))

        lines = [
            f"风暴: {d['storms']}",
            f"台风: {d['typhoons']}",
            f"MH: {d['mh']}",
            f"C5: {d['c5']}",
            f"月ACE: {d['ace']:.4f}",
        ]
        if d['strongest_name']:
            lines.append(f"最强: {d['strongest_name']} ({d['strongest_wind']}kt)")

        y = 32
        for line in lines:
            ls = rt(f_s, line, TXT)
            surf.blit(ls, (16, y))
            y += 19

        return surf
