# py/season_ctrl.py
"""风季控制器：时间推进、台风激活、ACE 累计。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional, TYPE_CHECKING

import pygame

from .constants import SEASON_SPEED_DEFAULT, HEMISPHERE_SOUTH

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
        self._pending_gen: int = 0                # 待激活缓存代次(数据重载/跳转 +1)

    def bind(self, dialog_mgr: object = None) -> None:
        self._dialog_mgr = dialog_mgr

    def invalidate_pending(self) -> None:
        """待激活缓存换代: 台风数据被重载/过滤/替换后必须调用。
        旧实现只用 id(repo.tys)+len 判版本, 而 load_typhoon_files() 原地
        clear 重填(同一 list、长度常相同) -> 缓存不失效, _pending 继续持有
        旧 Typhoon 对象并激活它们, 新对象永不激活。"""
        self._pending_gen += 1
        self._pending = None
        self._pending_src = ()
        self._start_cache.clear()

    def _ty_identity(self) -> tuple:
        """台风对象身份指纹(长度 + 首/末对象 id)。
        旧对象被 _pending/_start_cache 强引用, id 不会被复用, 指纹可靠。"""
        tys = self.repo.tys
        if not tys:
            return ()
        return (len(tys), id(tys[0]), id(tys[-1]))

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
        # 南半球回卷点: 数据最后报点之后 1 小时(不早于 7/1,不晚于 12/31),
        # 避免"最后报点在 edy 年 7-12 月"时被 7/1 提前截断(M4)
        last_abs = None
        first_abs = None
        for ty in self.repo.tys:
            if not ty.pts:
                continue
            for pt, is_first in ((ty.pts[0], True), (ty.pts[-1], False)):
                t = pt.get('t', '')
                if len(t) < 10:
                    continue
                try:
                    dt = datetime(int(t[:4]), int(t[4:6]), int(t[6:8]), int(t[8:10]))
                except ValueError:
                    continue
                if is_first:
                    if first_abs is None or dt < first_abs:
                        first_abs = dt
                else:
                    if last_abs is None or dt > last_abs:
                        last_abs = dt
        july_sec = (datetime(2000, 7, 1) - datetime(2000, 1, 1)).total_seconds()
        if last_abs is not None:
            # 绝对时间最后报点 → 其所在年内的秒数(绝对比较,不可跨年比秒数);
            # 7/1 秒数按报点年计算(闰年 182 天/非闰年 181 天,避免多 1 天空播)
            jy = last_abs.year
            july_sec = (datetime(jy, 7, 1) - datetime(jy, 1, 1)).total_seconds()
            last_sec = (last_abs - datetime(jy, 1, 1)).total_seconds()
            year_len = (366 if self._is_leap_year(jy) else 365) * 86400
            self._sh_wrap_second = min(year_len, max(july_sec, last_sec + 3600))
        else:
            self._sh_wrap_second = july_sec
        if first_abs is not None:
            self._sh_first_second = (first_abs - datetime(self.sty, 1, 1)).total_seconds()
        else:
            self._sh_first_second = None

    def update(self, dt: float) -> None:
        if not self._pl:
            return

        sp = self.cfg.sp
        time_delta = dt * sp * self.ssf
        self.ste += time_delta
        self.yf = False
        wrapped = False

        while True:
            year_seconds = (366 if self._is_leap_year(self.sy) else 365) * 86400
            if (self.cfg.hemisphere == HEMISPHERE_SOUTH and self.sy >= self.edy):
                # 南半球最后季节结束点 = 数据最后报点后 1 小时(不早于 7/1):
                # 数据播完即回卷,避免 7-12 月空转,也不截断 7-12 月的真实报点(R1/M4)。
                # 兜底按当年 7/1 动态计算(闰年 182 天/非闰年 181 天,防单年死循环)
                w = getattr(self, '_sh_wrap_second', None)
                if w is None:
                    w = (datetime(self.sy, 7, 1)
                         - datetime(self.sy, 1, 1)).total_seconds()
                year_seconds = min(year_seconds, w)
            if self.ste >= year_seconds:
                self.sy += 1
                if self.sy > self.edy:
                    self.sy = self.sty
                    wrapped = True
                if self.cfg.hemisphere == HEMISPHERE_SOUTH and wrapped:
                    # 南半球仅在"回卷"(进入下一 ACE 年/循环)时把 ste 重编码为
                    # 当年 7/1 偏移(R1)。非回卷跨年必须走 ste -= year_seconds,
                    # 让时钟落到次年 1/1 继续播放 1-6 月(南半球活跃季);
                    # 否则每季只播 7-12 月,1-6 月被永久跳过(R2 回归修复)。
                    base = (datetime(self.sy, 7, 1, 0)
                            - datetime(self.sy, 1, 1, 0)).total_seconds()
                    first = getattr(self, '_sh_first_second', None)
                    if first is not None and first < base:
                        # 首年 1-6 月已有报点:从最早报点前 1 小时起播,
                        # 与 reset_to_first_year 完全一致(M4),否则回卷后
                        # 首年 1-6 月(上一 ACE 年)数据被永久跳过(R3)。
                        self.ste = max(0.0, first - 3600.0)
                    elif self.sty == self.edy:
                        # 单年数据:精确落到当年 7/1(与 reset_to_first_year 及
                        # 多年 else 分支一致)。回卷点 _sh_wrap_second 必不小于
                        # 当年 7/1+1h(末报在 7-12 月),故 ste=base < 回卷点,
                        # 不会恒真死循环;且避免 base-3600 落地后首帧 ACE 年
                        # 瞬时误标为上一 ACE 年、与 reset 起播点错位(R4)。
                        self.ste = base
                    else:
                        # 多年数据:精确落到当年 7/1,与 reset_to_first_year 一致
                        self.ste = base
                else:
                    self.ste -= year_seconds
                if wrapped:
                    # 彻底回卷:ACE 累计无条件重置。南半球回卷落到当年 7/1,
                    # 北半球回到 1/1(尾部 July-1 翻转在 wrapped 时被跳过)。
                    if self.cfg.hemisphere == HEMISPHERE_SOUTH:
                        # 用实际起播时间反推 ACE 年(首年 1-6 月起播时即上一
                        # ACE 年),与 reset_to_first_year 的 current_ace_year 一致
                        restart_dt = self._year_start(self.sy) + timedelta(seconds=self.ste)
                        new_ace_year = self.ace_engine.ace_year(restart_dt)
                    else:
                        new_ace_year = self.ace_engine.ace_year(datetime(self.sy, 1, 1, 0))
                    self.csa = 0.0
                    self._csa_base = 0.0
                    self.current_ace_year = new_ace_year
                    if self._dialog_mgr and self._dialog_mgr.ace_chart.active:
                        self._dialog_mgr.ace_chart.needs_update = True
                elif self.cfg.hemisphere != HEMISPHERE_SOUTH:
                    # 北半球每个日历年即一季:非回卷跨年必重置累计。
                    new_ace_year = self.ace_engine.ace_year(datetime(self.sy, 1, 1, 0))
                    if new_ace_year != self.current_ace_year:
                        self.csa = 0.0
                        self._csa_base = 0.0
                        self.current_ace_year = new_ace_year
                        if self._dialog_mgr and self._dialog_mgr.ace_chart.active:
                            self._dialog_mgr.ace_chart.needs_update = True
                # (南半球非回卷:ste 现已落到次年 1/1 附近,ACE 年尚未变,
                # 由下方 "July-1 翻转" 在真实跨入 7/1 时再重置,避免本帧重复重置。)
                if self.sy == self.sty:
                    if self._dialog_mgr and hasattr(self._dialog_mgr.sim, 'playback_ctrl'):
                        pb = self._dialog_mgr.sim.playback_ctrl
                        pb.landfall_records.clear()
                        try:
                            pb._lf_last.clear()
                            pb._was_fin.clear()
                        except Exception:
                            pass
                    for ty in self.repo.tys:
                        ty.rst()
                        ty.ss = False
                        ty.sf = False
                        ty.act = False
                self.yf = True
            else:
                break

        total_hours = int(self.ste / 3600)
        days, hours = divmod(total_hours, 24)
        current_dt = self._year_start(self.sy) + timedelta(days=days, hours=hours)
        self.st = current_dt.strftime("%m%d%H")
        if not wrapped:
            # ACE 年边界（南半球 7 月 1 日）静默翻转时重置累计（R1）
            new_ay = self.ace_engine.ace_year(current_dt)
            if new_ay != self.current_ace_year:
                self.current_ace_year = new_ay
                self.csa = 0.0
                self._csa_base = 0.0
                if self._dialog_mgr and self._dialog_mgr.ace_chart.active:
                    self._dialog_mgr.ace_chart.needs_update = True

        # ── 待激活列表：只扫描未开始的台风，激活后移除 ──
        src = (self._pending_gen, self._ty_identity())
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
                        # 时间非法:标记已结束(与 jump_to 口径一致),不静默移出列表
                        ty.sf = True
                        continue
                else:
                    ty.sf = True
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

    def _reset_sim_month_tracker(self) -> None:
        """清掉 TySim 的月度总结进度。

        时间回拨/重置也会让 ste 变小, 若不清, _check_monthly_summary 会把它
        误判为 12->1 跨年, 重复弹出上一年的月度总结。"""
        sim = getattr(getattr(self, '_dialog_mgr', None), 'sim', None)
        if sim is not None:
            sim._last_month_key = None
            sim._last_month_ste = None

    def jump_to(self, dt: datetime) -> None:
        """统一时间跳转：设置时间 + 重置全部台风 + 计算ACE + 同步 TySim。"""
        self.calc_years()   # 切模式/跳转路径不调 reset: 确保回卷点数据就绪(R2-2/T3-1)
        self.set_jump(dt.year,
                      (dt - datetime(dt.year, 1, 1, 0)).total_seconds(),
                      dt.strftime("%m%d%H"))
        self.csa = self.calc_accumulated_ace_up_to(dt.year, dt.month, dt.day, dt.hour)
        self._csa_base = self.csa
        self.current_ace_year = self.get_ace_year(dt)
        self._start_cache.clear()
        self.invalidate_pending()
        self.yf = False
        self._reset_sim_month_tracker()
        # 跳转回拨后清空登陆记录/去重位置/完成提示,避免同一段路径被重复统计、
        # 或重新播完该台风时被 _was_fin=True 抑制 ACE 结束提示(R4)
        if self._dialog_mgr and hasattr(self._dialog_mgr.sim, 'playback_ctrl'):
            pb = self._dialog_mgr.sim.playback_ctrl
            pb.landfall_records.clear()
            try:
                pb._lf_last.clear()
                pb._was_fin.clear()
            except Exception:
                pass
        for ty in self.repo.tys:
            ty.rst()
            if not ty.pts:
                continue
            if len(ty.points_dt) == len(ty.pts):
                # 法6: 直接取已排序解析时间,免 2 次 strptime
                st = ty.points_dt[0]
                et = ty.points_dt[-1]
            else:
                try:
                    st = datetime.strptime(ty.pts[0]['t'][:10], "%Y%m%d%H")
                    et = datetime.strptime(ty.pts[-1]['t'][:10], "%Y%m%d%H")
                except Exception:
                    st = et = None
            if st is None or et is None:
                ty.sf = True
                ty.act = ty.ss = False
                continue
            if dt < st:
                ty.ss = ty.act = ty.sf = False
            elif dt >= et:
                # 含"恰在末报点"的情况: 数据已播完(R2-21)
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
            base = (datetime(self.sty, 7, 1, 0) - datetime(self.sty, 1, 1, 0)).total_seconds()
            first = getattr(self, '_sh_first_second', None)
            if first is not None and first < base:
                # 数据在首年 1-6 月已有报点: 从最早报点前 1 小时起播,不跳过(M4)
                self.ste = max(0.0, first - 3600.0)
            else:
                self.ste = base
            days = int(self.ste // 86400)
            hours = int((self.ste % 86400) // 3600)
            self.st = (datetime(self.sty, 1, 1) + timedelta(days=days,
                                                            hours=hours)).strftime("%m%d%H")
        else:
            self.st = "010100"
            self.ste = 0.0
        self.sy = self.sty
        month, day, hour = int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6])
        self.csa = 0.0
        self._csa_base = 0.0
        self.current_ace_year = self.ace_engine.ace_year(datetime(self.sty, month, day, hour))
        self._start_cache.clear()
        self.invalidate_pending()
        self.yf = False
        self._reset_sim_month_tracker()
        # 年循环：清空 finish note 记录让台风重新触发
        if self._dialog_mgr and hasattr(self._dialog_mgr.sim, 'playback_ctrl'):
            pb = self._dialog_mgr.sim.playback_ctrl
            pb._was_fin.clear()
            pb.landfall_records.clear()
            try:
                pb._lf_last.clear()
            except Exception:
                pass
        for ty in self.repo.tys:
            ty.rst()
            ty.ss = False
            ty.sf = False
            ty.act = False

    @staticmethod
    def _is_leap_year(y: int) -> bool:
        return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)

    _year_start_cache: Dict[int, datetime] = {}

    def _year_start(self, year: int) -> datetime:
        dt = self._year_start_cache.get(year)
        if dt is None:
            dt = datetime(year, 1, 1, 0)
            if len(self._year_start_cache) > 64:
                self._year_start_cache.clear()
            self._year_start_cache[year] = dt
        return dt
