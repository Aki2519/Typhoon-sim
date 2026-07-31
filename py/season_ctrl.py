# py/season_ctrl.py
"""风季控制器：时间推进、台风激活、ACE 累计。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional, TYPE_CHECKING

import pygame

from .constants import SEASON_SPEED_DEFAULT, HEMISPHERE_NORTH, HEMISPHERE_SOUTH

if TYPE_CHECKING:
    from .typhoon import Typhoon
    from .data_repo import DataRepository
    from .ace_engine import ACEEngine
    from .config import AppConfig


class SeasonController:
    """管理风季模式的时间推进和台风生命周期。"""

    def __init__(self, cfg: AppConfig, repo: DataRepository,
                 ace_engine: ACEEngine) -> None:
        self.cfg = cfg
        self.repo = repo
        self.ace_engine = ace_engine
        self._pl: bool = False
        self.st: str = "010100"
        self.ste: float = 0.0
        self.sy: int = 2000
        self.sty: int = 2000
        self.edy: int = 2000
        self.csa: float = 0.0
        self._csa_base: float = 0.0
        self.current_ace_year: int = 2000
        self.ssf: float = SEASON_SPEED_DEFAULT
        self.yf: bool = False
        self._start_cache: Dict[Typhoon, datetime] = {}
        self._dialog_mgr: object = None
        self._pending: Optional[list] = None      # 未激活台风列表（激活后移除）
        self._pending_src: tuple = ()

    def bind(self, dialog_mgr: object = None) -> None:
        self._dialog_mgr = dialog_mgr

    @property
    def csa_base(self) -> float:
        return self._csa_base

    @csa_base.setter
    def csa_base(self, value: float) -> None:
        self._csa_base = value

    def calc_years(self) -> None:
        sty, edy = self.ace_engine.season_years()
        self.sty, self.edy = sty, edy
        self.sy = sty

    def update(self, dt: float) -> None:
        if not self._pl:
            return

        time_delta = dt * self.cfg.sp * self.ssf
        self.ste += time_delta
        self.yf = False
        wrapped = False

        while True:
            year_seconds = (366 if self._is_leap_year(self.sy) else 365) * 86400
            if self.ste >= year_seconds:
                self.ste -= year_seconds
                self.sy += 1
                if self.sy > self.edy:
                    self.sy = self.sty
                    wrapped = True
                    if self.cfg.hemisphere == HEMISPHERE_SOUTH:
                        new_ace_year = self.ace_engine.ace_year(datetime(self.sy, 7, 1, 0))
                    else:
                        new_ace_year = self.ace_engine.ace_year(datetime(self.sy, 1, 1, 0))
                    self.csa = 0.0
                    self._csa_base = 0.0
                    self.current_ace_year = new_ace_year
                    if self._dialog_mgr and self._dialog_mgr.ace_chart.active:
                        self._dialog_mgr.ace_chart.needs_update = True
                else:
                    new_dt = datetime(self.sy, 1, 1, 0)
                    new_ace_year = self.ace_engine.ace_year(new_dt)
                    if new_ace_year != self.current_ace_year:
                        self.csa = 0.0
                        self._csa_base = 0.0
                        self.current_ace_year = new_ace_year
                        if self._dialog_mgr and self._dialog_mgr.ace_chart.active:
                            self._dialog_mgr.ace_chart.needs_update = True
                if self.sy == self.sty:
                    for ty in self.repo.tys:
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
        if not wrapped:
            self.current_ace_year = self.ace_engine.ace_year(current_dt)

        # ── 待激活列表：只扫描未开始的台风，激活后移除 ──
        src = (id(self.repo.tys), len(self.repo.tys))
        if self._pending is None or src != self._pending_src or self.yf:
            self._pending = [ty for ty in self.repo.tys
                             if not ty.ss and not ty.sf and ty.pts]
            self._pending_src = src

        still_pending = []
        for ty in self._pending:
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
                ty.v._spawn_time = pygame.time.get_ticks()
            else:
                still_pending.append(ty)
        self._pending = still_pending

    def calc_accumulated_ace_up_to(self, y: int, m: int, d: int, h: int) -> float:
        return self.ace_engine.cumulative_ace_up_to(datetime(y, m, d, h))

    def add_csa(self, amount: float) -> None:
        self._csa_base += amount

    def set_csa_base(self, value: float) -> None:
        self._csa_base = value

    def get_ace_year(self, dt: datetime) -> int:
        return self.ace_engine.ace_year(dt)

    def set_jump(self, y: int, simulated_seconds: float, time_str: str) -> None:
        if len(time_str) < 6:
            return
        try:
            m, d, h = int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6])
        except (ValueError, IndexError):
            return
        self.sy = y
        self.ste = simulated_seconds
        self.st = time_str
        self.current_ace_year = self.get_ace_year(datetime(y, m, d, h))

    def jump_to(self, dt: datetime) -> None:
        """统一时间跳转：设置时间 + 重置全部台风 + 计算ACE + 同步 TySim。"""
        self.set_jump(dt.year,
                      (dt - datetime(dt.year, 1, 1, 0)).total_seconds(),
                      dt.strftime("%m%d%H"))
        self.csa = self.calc_accumulated_ace_up_to(dt.year, dt.month, dt.day, dt.hour)
        self._csa_base = self.csa
        self.current_ace_year = self.get_ace_year(dt)
        self._start_cache.clear()
        self._pending = None
        self.yf = False
        for ty in self.repo.tys:
            ty.rst()
            if not ty.pts:
                continue
            try:
                st = datetime.strptime(ty.pts[0]['t'][:10], "%Y%m%d%H")
                et = datetime.strptime(ty.pts[-1]['t'][:10], "%Y%m%d%H")
            except Exception:
                ty.sf = True
                ty.act = ty.ss = False
                continue
            if dt < st:
                ty.ss = ty.act = ty.sf = False
            elif dt > et:
                ty.sf = True
                ty.act = ty.ss = False
            else:
                ty.ss = ty.act = True
                ty.sf = False
                ty.set_current_time(dt)
                ty.last_ace_ci = ty.ci
                ty.v._spawn_time = pygame.time.get_ticks()

    def reset_to_first_year(self) -> None:
        """重置风季状态到最早年份的1月1日（南半球则为7月1日），并同步所有台风状态。"""
        self.calc_years()
        if self.cfg.hemisphere == HEMISPHERE_SOUTH:
            self.st = "070100"
        else:
            self.st = "010100"
        self.ste = 0.0
        self.sy = self.sty
        month, day, hour = int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])
        self.csa = 0.0
        self.current_ace_year = self.ace_engine.ace_year(datetime(self.sty, month, day, hour))
        self._start_cache.clear()
        self._pending = None
        self.yf = False
        # 年循环：清空 finish note 记录让台风重新触发
        if self._dialog_mgr and hasattr(self._dialog_mgr.sim, 'playback_ctrl'):
            self._dialog_mgr.sim.playback_ctrl._was_fin.clear()
            self._dialog_mgr.sim.playback_ctrl._finale_until = 0.0
            self._dialog_mgr.sim.playback_ctrl._finale_triggered = False
        for ty in self.repo.tys:
            ty.rst()
            ty.ss = False
            ty.sf = False
            ty.act = False

    @staticmethod
    def _is_leap_year(y: int) -> bool:
        return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
