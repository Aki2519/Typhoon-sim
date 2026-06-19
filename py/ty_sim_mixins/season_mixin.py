# py/ty_sim_mixins/season_mixin.py
from datetime import datetime, timedelta


class TySimSeasonMixin:

    def update_season_time(self, dt: float) -> None:
        if not self.pl:
            return

        time_delta = dt * self.sp * self.ssf
        self.ste += time_delta
        self.yf = False

        while True:
            year_seconds = (366 if self._is_leap_year(self.sy) else 365) * 86400
            if self.ste >= year_seconds:
                self.ste -= year_seconds
                self.sy += 1
                new_dt = datetime(self.sy, 1, 1, 0)
                new_ace_year = self.ace_engine.ace_year(new_dt)
                if new_ace_year != self.current_ace_year:
                    self.csa = 0.0
                    self.current_ace_year = new_ace_year
                    if hasattr(self, 'dialog_mgr') and self.dialog_mgr.ace_chart.active:
                        self.dialog_mgr.ace_chart._needs_update = True
                if self.sy > self.edy:
                    self.sy = self.sty
                if self.sy == self.sty:
                    for ty in self.tys:
                        ty.rst()
                        ty.ss = False
                        ty.sf = False
                        ty.act = False
                self.yf = True
            else:
                break

        total_hours = int(self.ste / 3600)
        days = total_hours // 24
        hours = total_hours % 24
        current_dt = datetime(self.sy, 1, 1, 0) + timedelta(days=days, hours=hours)
        self.st = current_dt.strftime("%m%d%H")
        self.current_ace_year = self.ace_engine.ace_year(current_dt)

        if not hasattr(self, '_start_cache'):
            self._start_cache = {}

        for ty in self.tys:
            if ty.ss or ty.sf or not ty.pts:
                continue

            start_dt = self._start_cache.get(ty)
            if start_dt is None:
                ft = ty.pts[0]['t']
                if len(ft) >= 8:
                    try:
                        year = int(ft[:4])
                        month = int(ft[4:6])
                        day = int(ft[6:8])
                        hour = int(ft[8:10]) if len(ft) >= 10 else 0
                        start_dt = datetime(year, month, day, hour)
                        self._start_cache[ty] = start_dt
                    except ValueError:
                        continue
                else:
                    continue

            if current_dt >= start_dt:
                ty.ss = True
                ty.act = True

    @staticmethod
    def _is_leap_year(y: int) -> bool:
        return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)