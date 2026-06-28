# py/script_engine.py
"""镜头运动与时间变速脚本引擎。

状态机模型:
  DWELL  — 镜头静止在目标N，等待模拟时间推进到目标N的 == 日期
  MOVING — 镜头从目标N插值到目标N+1，等待模拟时间推进到目标N+1的 [[ 日期

语义:
  [[日期  — 到达目标N的日期（MOVING结束，DWELL开始）
  ==日期  — 离开目标N的日期（DWELL结束，开始向N+1 MOVING）
  *  (在==之前) — 到达目标N的速度（也即 DWELL 期间的速度）
  *  (在==之后) — 离开目标N的速度（也即向N+1 MOVING 的速度）
  >  (在==之后) — DWELL结束时的跳跃日期（先跳再开始MOVING）
  >  (在[[之前) — 到达目标N时的跳跃日期（先跳再开始DWELL）
  //  — 移入该目标时匀速（线性插值）
   /   — 移入该目标时变速（缓入缓出）
"""


from __future__ import annotations
import os
import re
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ── 坐标 / 日期解析 ──

def _parse_lon(s: str) -> float:
    s = s.strip().upper()
    if s.endswith('W'):
        return 360.0 - float(s[:-1])
    if s.endswith('E'):
        return float(s[:-1])
    return float(s)

def _parse_lat(s: str) -> float:
    s = s.strip().upper()
    if s.endswith('S'):
        return -float(s[:-1])
    if s.endswith('N'):
        return float(s[:-1])
    return float(s)

def _parse_date(s: str) -> Optional[datetime]:
    """解析日期字符串: 2026-04-12-00z → datetime.
    小时可选，'z'后缀可选。空字符串返回默认值 2000-01-01 00z."""
    s = s.strip()
    if not s:
        return datetime(2000, 1, 1, 0)
    s = s.rstrip('zZ')
    parts = s.split('-')
    if len(parts) < 3:
        return None
    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    h = int(parts[3]) if len(parts) >= 4 else 0
    try:
        return datetime(y, m, d, h)
    except ValueError:
        return None

def _parse_date_required(s: str) -> datetime:
    """解析日期，失败或为空返回默认值 2000-01-01."""
    d = _parse_date(s)
    return d if d is not None else datetime(2000, 1, 1, 0)

def _parse_seconds(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    return float(s)

def _parse_speed(s: str) -> float:
    s = s.strip()
    if not s:
        return 1.0
    return float(s)


# ── 脚本数据结构 ──

class TargetRegion:
    """脚本中定义的镜头目标区域及关联参数。"""
    def __init__(self, lon: float, lat: float, width_deg: float, index: int,
                 constant_speed: bool = False):
        self.lon = lon
        self.lat = lat
        self.width_deg = width_deg
        self.index = index
        self.constant_speed = constant_speed          # // → True (匀速), / → False (变速)

        # 由缩进行解析得到
        self.move_to_date: Optional[datetime] = None   # [[ 到达日期
        self.move_from_date: Optional[datetime] = None  # == 离开日期
        self.approach_speed: float = 1.0               # * 在 == 之前：到达/停留速度
        self.departure_speed: float = 1.0              # * 在 == 之后：离开速度
        self.jump_on_arrival: Optional[datetime] = None # > 在 [[ 之前：到达时跳跃
        self.jump_on_depart: Optional[datetime] = None  # > 在 == 之后：离开时跳跃
        self._has_departure_speed: bool = False         # 是否有显式 * after ==

    def compute_bounds(self, screen_width: int, map_height: int) -> Tuple[float, float, float, float]:
        """根据屏幕宽高比计算实际经纬度四至。
        用户指定宽度(纬距)，高度 = 宽度 / 宽高比。
        返回 (mlo, Mlo, mla, Mla)。"""
        if map_height <= 0:
            map_height = 1
        aspect = screen_width / map_height
        lon_span = self.width_deg
        lat_span = self.width_deg / aspect
        mlo = self.lon
        Mlo = self.lon + lon_span
        mla = self.lat
        Mla = self.lat + lat_span
        return mlo, Mlo, mla, Mla

    def get_move_speed(self, prev_target: Optional['TargetRegion']) -> float:
        """返回移入本目标时应使用的速度。
        == 之后的 * 优先（离开速度），否则使用 == 之前的 *（到达速度）。"""
        if prev_target is not None and prev_target._has_departure_speed:
            return prev_target.departure_speed
        return self.approach_speed

    def __repr__(self):
        return (f"<Target[{self.index}] lon={self.lon} lat={self.lat} w={self.width_deg} "
                f"arr={self.move_to_date} dep={self.move_from_date} "
                f"asp={self.approach_speed} dsp={self.departure_speed} "
                f"cs={self.constant_speed}>")


class Script:
    """解析后的脚本。"""
    def __init__(self):
        self.description: str = ""
        self.start_jump_date: Optional[datetime] = None
        self.targets: List[TargetRegion] = []
        self.filename: str = ""

    @classmethod
    def parse(cls, text: str, filename: str = "") -> "Script":
        script = cls()
        script.filename = filename

        lines = text.split('\n')
        current_target_idx = -1
        past_eq = False          # 当前目标是否已经遇到了 ==

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # 简介行
            if line.startswith('#'):
                if not script.description:
                    script.description = line[1:].strip()
                continue

            # 无缩进的 > 行：脚本启动时间跳跃（必须在第一个 / 之前）
            if line.startswith('>') and not raw_line.startswith((' ', '\t')) and current_target_idx < 0:
                date_str = line[1:].strip()
                if date_str:
                    date = _parse_date(date_str)
                    if date is not None:
                        script.start_jump_date = date
                    else:
                        logger.warning(f"无效的启动跳跃日期: {date_str}")
                continue

            # 结束标记
            if line == '%':
                break

            # 目标区域定义行 (/ 或 //)
            if line.startswith('/'):
                constant = line.startswith('//')
                content = line[2:] if constant else line[1:]
                parts = content.split(';')
                if len(parts) != 2:
                    logger.warning(f"脚本行格式错误 (需要 ; 分隔): {line}")
                    continue
                coord_part = parts[0].strip()
                width_part = parts[1].strip()

                coord_tokens = coord_part.split()
                if len(coord_tokens) < 2:
                    logger.warning(f"脚本行坐标格式错误: {line}")
                    continue
                lon = _parse_lon(coord_tokens[0])
                lat = _parse_lat(coord_tokens[1])
                width_deg = float(width_part)

                current_target_idx = len(script.targets)
                past_eq = False
                script.targets.append(TargetRegion(lon, lat, width_deg, current_target_idx,
                                                    constant_speed=constant))
                continue

            # 缩进的行
            if not raw_line.startswith((' ', '\t')):
                logger.warning(f"脚本行缺少缩进: {line}")
                continue

            if current_target_idx < 0:
                logger.warning(f"控制符号出现在目标区域之前: {line}")
                continue

            tgt = script.targets[current_target_idx]
            line_stripped = line

            if line_stripped.startswith('[['):
                date = _parse_date_required(line_stripped[2:].strip())
                tgt.move_to_date = date
                past_eq = False
            elif line_stripped.startswith('=='):
                date = _parse_date_required(line_stripped[2:].strip())
                tgt.move_from_date = date
                past_eq = True
            elif line_stripped.startswith('['):
                # [秒数 — 暂未在状态机中实现，保留兼容
                pass
            elif line_stripped.startswith('='):
                # =秒数 — 暂未在状态机中实现，保留兼容
                pass
            elif line_stripped.startswith('*'):
                speed = _parse_speed(line_stripped[1:].strip())
                if past_eq:
                    tgt.departure_speed = speed
                    tgt._has_departure_speed = True
                else:
                    tgt.approach_speed = speed
            elif line_stripped.startswith('>'):
                date_str = line_stripped[1:].strip()
                date = _parse_date(date_str) if date_str else None
                if past_eq:
                    tgt.jump_on_depart = date
                else:
                    tgt.jump_on_arrival = date
            else:
                logger.warning(f"未知控制符号: {line}")

        # ── 推断缺失的到达/离开日期 ──
        for i, target in enumerate(script.targets):
            if target.move_to_date is None:
                if i == 0:
                    target.move_to_date = script.start_jump_date or datetime(2000, 1, 1, 0)
                else:
                    prev = script.targets[i - 1]
                    target.move_to_date = prev.move_from_date or prev.move_to_date
            if target.move_from_date is None:
                if i + 1 < len(script.targets):
                    target.move_from_date = target.move_to_date
                else:
                    target.move_from_date = target.move_to_date

        return script


# ── 脚本执行引擎 (状态机) ──

class ScriptEngine:
    """执行脚本，控制镜头运动和时间变速。

    状态机:
      IDLE    — 未运行
      DWELL   — 镜头静止在 current_target，等待 sim.ste 抵达 move_from_date
      MOVING  — 镜头插值从 prev_target 到 current_target，
                 等待 sim.ste 抵达 current_target.move_to_date
    """

    STATE_IDLE = 0
    STATE_DWELL = 1
    STATE_MOVING = 2

    def __init__(self, sim):
        self.sim = sim
        self.script: Optional[Script] = None
        self.running = False
        self.paused = False

        self._state = self.STATE_IDLE
        self._current_target_idx = 0
        self._prev_target_idx = -1

        # 插值参数
        self._interp_start = [0.0, 0.0, 0.0, 0.0]   # mlo, Mlo, mla, Mla
        self._interp_end = [0.0, 0.0, 0.0, 0.0]
        self._interp_start_ste: float = 0.0
        self._interp_end_ste: float = 0.0
        self._interp_linear: bool = False

    # ── 公开 API ──

    def load_script(self, text: str, filename: str = "") -> bool:
        try:
            self.script = Script.parse(text, filename)
            if not self.script.targets:
                self.sim.show_error("脚本无有效目标")
                return False
            return True
        except Exception as e:
            logger.error(f"脚本解析失败: {e}")
            self.sim.show_error(f"脚本解析失败: {e}")
            return False

    def start(self) -> bool:
        if not self.script or not self.script.targets:
            self.sim.show_error("没有可执行的脚本")
            return False

        # 切换到台风季模式
        if self.sim.md != self.sim.MODE_SEASON:
            self.sim.switch_mode()

        # 启动时间跳跃
        if self.script.start_jump_date is not None:
            self._do_time_jump(self.script.start_jump_date)

        # 取消模拟暂停
        self.sim.pl = True
        from .constants import f_s, rt
        self.sim.play_text = rt(f_s, "暂停", (255, 255, 255))

        self.running = True
        self.paused = False
        self._current_target_idx = 0
        self._prev_target_idx = -1

        # 进入首个目标的 DWELL 状态
        t0 = self.script.targets[0]
        self._snap_to_target(t0)
        self._set_speed(t0.approach_speed)
        self._state = self.STATE_DWELL

        return True

    def stop(self):
        self.running = False
        self.paused = False
        self._state = self.STATE_IDLE
        self.script = None

    def update(self, dt: float):
        """每帧更新。"""
        if not self.running or not self.script:
            return

        sim_paused = not self.sim.pl
        if self.paused or sim_paused:
            return

        if self._state == self.STATE_DWELL:
            self._update_dwell()
        elif self._state == self.STATE_MOVING:
            self._update_moving()

    # ── DWELL 阶段 ──

    def _update_dwell(self):
        """DWELL: 镜头静止在 current_target，等待 ste 抵达 move_from_date。"""
        tgt = self.script.targets[self._current_target_idx]
        end_abs = self._dt_to_abs_ste(tgt.move_from_date)

        if self._sim_abs_ste() >= end_abs:
            # 停留结束：处理离开跳跃
            self._ensure_date(tgt.move_from_date)

            if tgt.jump_on_depart is not None:
                self._do_time_jump(tgt.jump_on_depart)

            # 进入 MOVING 到下一个目标
            next_idx = self._current_target_idx + 1
            if next_idx >= len(self.script.targets):
                self.stop()
                return

            next_tgt = self.script.targets[next_idx]
            self._prev_target_idx = self._current_target_idx
            self._current_target_idx = next_idx
            self._start_moving_to(tgt, next_tgt)

    # ── MOVING 阶段 ──

    def _start_moving_to(self, from_tgt: TargetRegion, to_tgt: TargetRegion):
        """开始从 from_tgt 向 to_tgt 移动。"""
        # 速度
        speed = to_tgt.get_move_speed(from_tgt)
        self._set_speed(speed)

        target_date = to_tgt.move_to_date

        # 记录起始 bounds
        start_b = from_tgt.compute_bounds(self.sim.screen_width, self.sim.map_height)
        end_b = to_tgt.compute_bounds(self.sim.screen_width, self.sim.map_height)
        self._interp_start = list(start_b)
        self._interp_end = list(end_b)
        self._interp_start_ste = self._sim_abs_ste()
        self._interp_end_ste = self._dt_to_abs_ste(target_date)
        self._interp_linear = to_tgt.constant_speed  # 移入目标的 // 或 /

        if self._interp_end_ste <= self._interp_start_ste:
            # 日期已到（或跳跃后已过），立即到位
            self._snap_to_target(to_tgt)
            self._set_speed(to_tgt.approach_speed)
            self._state = self.STATE_DWELL
            return

        self._state = self.STATE_MOVING

    def _update_moving(self):
        """MOVING: 插值镜头从 prev_target 到 current_target。"""
        if self._sim_abs_ste() >= self._interp_end_ste:
            # 到达
            tgt = self.script.targets[self._current_target_idx]
            self._snap_to_target(tgt)
            self._ensure_date(tgt.move_to_date)
            self._set_speed(tgt.approach_speed)
            self._state = self.STATE_DWELL

            # 处理到达跳跃（如果 > 在 [[ 之前）
            if tgt.jump_on_arrival is not None:
                self._do_time_jump(tgt.jump_on_arrival)
            return

        # 插值
        if self._interp_end_ste > self._interp_start_ste:
            t = (self._sim_abs_ste() - self._interp_start_ste) / (self._interp_end_ste - self._interp_start_ste)
        else:
            t = 1.0
        t = max(0.0, min(1.0, t))
        if not self._interp_linear:
            t = self._ease_in_out(t)

        self.sim.mlo = self._interp_start[0] + (self._interp_end[0] - self._interp_start[0]) * t
        self.sim.Mlo = self._interp_start[1] + (self._interp_end[1] - self._interp_start[1]) * t
        self.sim.mla = self._interp_start[2] + (self._interp_end[2] - self._interp_start[2]) * t
        self.sim.Mla = self._interp_start[3] + (self._interp_end[3] - self._interp_start[3]) * t
        self._update_sim_view()

    # ── 辅助 ──

    def _snap_to_target(self, tgt: TargetRegion):
        """瞬间将镜头定位到目标区域。"""
        mlo, Mlo, mla, Mla = tgt.compute_bounds(self.sim.screen_width, self.sim.map_height)
        self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla = mlo, Mlo, mla, Mla
        self._update_sim_view()

    _SECONDS_PER_YEAR = 31622400  # 366 * 86400，确保跨年比较无重叠

    def _sim_abs_ste(self) -> float:
        """返回模拟当前时间的绝对秒数（跨年安全）。"""
        return self.sim.ste + self.sim.sy * self._SECONDS_PER_YEAR

    def _dt_to_abs_ste(self, date: datetime) -> float:
        """将 datetime 转换为绝对秒数（跨年安全）。"""
        ste_in_year = (date - datetime(date.year, 1, 1, 0)).total_seconds()
        return ste_in_year + date.year * self._SECONDS_PER_YEAR

    def _date_to_ste(self, date: datetime) -> float:
        """将 datetime 转换为年内秒数。"""
        return (date - datetime(date.year, 1, 1, 0)).total_seconds()

    def _set_speed(self, speed: float):
        """设置模拟速度。"""
        self.sim.sp = speed

    def _ensure_date(self, date: datetime):
        """确保 sim 的 sy / st 与给定日期一致（不失一般性地修正跨年边界）。"""
        if self.sim.sy != date.year:
            self.sim.sy = date.year
        self.sim.st = date.strftime("%m%d%H")

    def _do_time_jump(self, target_date: datetime):
        """执行时间跳跃。"""
        y, m, d, h = target_date.year, target_date.month, target_date.day, target_date.hour
        ts = (target_date - datetime(y, 1, 1, 0)).total_seconds()
        self.sim.ste = ts
        self.sim.sy = y
        self.sim.st = target_date.strftime("%m%d%H")
        self.sim.current_ace_year = self.sim.get_ace_year(target_date)
        self.sim.csa = self.sim.calc_accumulated_ace_up_to(y, m, d, h)

        for ty in self.sim.tys:
            ty.rst()
            if not ty.pts:
                continue
            try:
                st = datetime.strptime(ty.pts[0]['t'][:10], "%Y%m%d%H")
                et = datetime.strptime(ty.pts[-1]['t'][:10], "%Y%m%d%H")
            except Exception:
                continue
            if target_date < st:
                ty.ss = ty.act = ty.sf = False
            elif target_date > et:
                ty.sf = True
                ty.act = ty.ss = False
            else:
                ty.ss = ty.act = True
                ty.sf = False
                ty.set_current_time(target_date)
                ty.last_ace_ci = ty.ci

    def _update_sim_view(self):
        """将 sim 的 bounds 同步到视图。"""
        if self.sim.map_mgr.map_view:
            self.sim.map_mgr.map_view.set_view_region(
                self.sim.mlo, self.sim.Mlo, self.sim.mla, self.sim.Mla)
        self.sim.map_mgr.update_land_mask()
        self.sim.update_all_screen_points()
        self.sim._view_dirty = True

    @staticmethod
    def _ease_in_out(t: float) -> float:
        if t < 0.5:
            return 4 * t * t * t
        else:
            return 1 - pow(-2 * t + 2, 3) / 2


# ── 脚本文件扫描 ──

def scan_scripts(script_dir: str) -> List[dict]:
    scripts = []
    if not os.path.isdir(script_dir):
        return scripts

    for fname in sorted(os.listdir(script_dir)):
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(script_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                first_line = f.readline()
        except Exception:
            first_line = ""

        description = ""
        if first_line.startswith('#'):
            description = first_line[1:].strip()

        scripts.append({
            'filename': fname,
            'path': fpath,
            'description': description,
        })

    return scripts
