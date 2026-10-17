# app/script_timeline.py
"""脚本编辑器的**线性时间轴**（用户 2026-10-13 需求 3.6 全系列 + 3.8~3.12）。

与地图上那个"转轮/时间圈"无关：这是普通的线性时间轴，横轴 = 时间。

分工：
  · `TimelineModel`  —— 纯数据 + 纯逻辑（段、变速点、jump 点、当前时间、缩放平移、
                        吸附、**段之间不许重合**）。不依赖 pygame，可单独测。
  · `TimelineWidget` —— 画与交互（滚轮缩放 / 左键编辑 / 右键拖动 / 拖端点 / 双击变速点）。

用户给的硬性数字（写死在这里，改之前先问）：
    · 时间轴跨度：最小 12h，最大 20 年（3.12）
    · 吸附精度：默认 6h；更细的只能在编辑区手写，但在时间轴上要能正常显示；
      拖动更细的东西**之后**吸附到最近的 6h（3.11）
    · 新建 target 段默认 10 天（3.6.2）
    · 标签精度随跨度：>10 年只显示年，>1 年只显示年月，>2 个月只显示年月日，
      否则精确到小时（3.6.3）
    · 段填充：自下而上 50% 蓝 → 到**该段高度的 75%** 处透明（3.6.2）
    · 上方正好放 3 行时间文字，超过就错落（3.6.3）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

# ── 常量（用户给的数字） ─────────────────────────────────────────────────
MIN_SPAN_H = 12.0                 # 3.12 最小跨度 12 小时
MAX_SPAN_H = 20 * 365.25 * 24.0   # 3.12 最大跨度 20 年
DEFAULT_SEGMENT_H = 24.0 * 10     # 3.6.2 新建段默认 10 天
SNAP_H = 6.0                      # 3.11 默认精度
LABEL_ROWS = 3                    # 3.6.3 上方正好放 3 行

#: 刻度阶梯（小时）—— 从最细到最粗，`make_ticks` 从中挑
_STEP_LADDER = [1, 2, 3, 6, 12, 24, 48, 72, 24 * 7, 24 * 14,
                24 * 30, 24 * 91, 24 * 182, 24 * 365, 24 * 365 * 2,
                24 * 365 * 5, 24 * 365 * 10]

C_SEG = (70, 140, 235)            # 时间段蓝
C_SEG_EDGE = (120, 190, 255)
C_SPEED = (245, 205, 60)          # 变速点黄
C_JUMP = (95, 215, 120)           # jump 绿
C_NOW = (255, 255, 255)           # 当前时间白线
C_TEXT = (235, 240, 250)
C_BG = (18, 21, 28, 255)   # **不透明**：时间轴要压住功能栏，透出来会糊
C_BORDER = (240, 244, 250)


# ── 纯逻辑：吸附 / 刻度 / 标签 ──────────────────────────────────────────

def snap(t: datetime, step_h: float = SNAP_H) -> datetime:
    """吸附到最近 step_h 小时（3.11）。

    ⚠ 基准必须是**当天零点**，不能拿 `t.timestamp() % step` 去算：那样基准是
    Unix 纪元（UTC），在东八区会让 6h 网格落在 02/08/14/20 点，用户看到的是
    "明明吸附了却不对齐整点"。以当天 0 点为基准 ⇒ 网格永远是 00/06/12/18。
    """
    if step_h <= 0:
        return t
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    off_h = (t - midnight).total_seconds() / 3600.0
    k = round(off_h / step_h)
    return midnight + timedelta(hours=k * step_h)


def _calendar_candidates():
    """日历刻度阶梯：(种类, 步长)。种类 'd'=天 / 'mo'=月 / 'y'=年。

    ⚠ 为什么不能只用"固定小时数的倍数"：用户 2026-10-13 指出"刻度线明显不对"。
    按固定 30 天对齐（Unix 纪元的整数倍）会得到 07-01 / 07-31 / 08-30 这种
    **看着就不像日历**的刻度；月/年必须按**日历边界**切（每月 1 号、每年 1 月 1 日）。
    """
    return (('d', 1), ('d', 2), ('d', 3), ('d', 7), ('d', 14),
            ('mo', 1), ('mo', 2), ('mo', 3), ('mo', 6),
            ('y', 1), ('y', 2), ('y', 5), ('y', 10), ('y', 20))


def _add_months(t: datetime, n: int) -> datetime:
    y = t.year + (t.month - 1 + n) // 12
    m = (t.month - 1 + n) % 12 + 1
    return datetime(y, m, 1)


def _approx_hours(kind: str, n: int) -> float:
    if kind == 'd':
        return n * 24.0
    if kind == 'mo':
        return n * 30.44 * 24.0
    return n * 365.25 * 24.0


def _iter_ticks(t0: datetime, t1: datetime, kind: str, n: int):
    """按日历边界迭代刻度时间（含 t0..t1 之间）。"""
    if kind == 'd':
        cur = t0.replace(hour=0, minute=0, second=0, microsecond=0)
        while cur < t0:
            cur += timedelta(days=n)
        while cur <= t1:
            yield cur
            cur += timedelta(days=n)
        return
    if kind == 'mo':
        cur = datetime(t0.year, t0.month, 1)
        # 对齐到"能被 n 整除的月份"（从 1 月起算）
        while (cur.month - 1) % n:
            cur = _add_months(cur, 1)
        while cur < t0:
            cur = _add_months(cur, n)
        while cur <= t1:
            yield cur
            cur = _add_months(cur, n)
        return
    cur = datetime(t0.year, 1, 1)
    while (cur.year - 1) % n:
        cur = datetime(cur.year + 1, 1, 1)
    while cur < t0:
        cur = datetime(cur.year + n, 1, 1)
    while cur <= t1:
        yield cur
        cur = datetime(cur.year + n, 1, 1)


def pick_step(span_h: float, width_px: int, min_label_px: int = 110):
    """挑 **三级**刻度：(带标签的, 中, 短)，各是 (种类, 步长)。

    用户 2026-10-13："刻度线这么少我怎么看" ⇒ 一把尺子要有三级：
      · 带标签级：间距 >= （保证文字不挤在一起）
      · 中级    ：间距 >= 24px（不标字，只画中线）
      · 短级    ：间距 >= 6px（不标字，画短线）
    挤不下的档返回 None（3.6"挤不下的小刻度线消失"）。
    """
    width_px = max(40, int(width_px))
    span_h = max(MIN_SPAN_H, float(span_h))
    cands = _calendar_candidates()

    def _pick(min_px):
        for c in cands:
            if width_px * _approx_hours(*c) / span_h >= min_px:
                return c
        return None

    label = _pick(min_label_px) or cands[-1]
    # ⚠ 中级/短级要取"**最细的**、但间距仍够"的那一档（不是从标签档往下退一档）——
    #   退一档的话 200 天跨度只有 13 条中线，用户："刻度线这么少我怎么看"。
    li = list(cands).index(label)

    def _finest(min_px):
        # 阶梯是从**细到粗**排的 ⇒ 第一个满足间距要求的，就是最细且放得下的那一档
        for c in cands[:li]:
            if width_px * _approx_hours(*c) / span_h >= min_px:
                return c
        return None

    medium = _finest(24.0)
    short = _finest(6.0)
    # 同一档不能同时当中级和短级（阶梯最细只到 1 天时会出现）⇒ 退化成 None
    if medium == label:
        medium = None
    if short == medium:
        short = None
    return label, medium, short


def format_time(t: datetime, span_h: float) -> str:
    """时间文字精度随跨度（用户 2026-10-13 改的新阈值）。

    跨度 <= 1 个月 : 精确到小时    MM-DD HH
    跨度 >  1 个月 : 只显示年月日  YYYY-MM-DD
    跨度 >  6 个月 : 只显示年月    YYYY-MM
    跨度 >  5 年   : 只显示年      YYYY
    """
    if span_h > 5 * 365.25 * 24:
        return '%d' % t.year
    if span_h > 6 * 30.44 * 24:
        return '%d-%02d' % (t.year, t.month)
    if span_h > 30.44 * 24:
        return '%d-%02d-%02d' % (t.year, t.month, t.day)
    return '%02d-%02d %02d' % (t.month, t.day, t.hour)


def make_ticks(t0: datetime, t1: datetime, width_px: int
               ) -> List[Tuple[float, int, str]]:
    """生成三级刻度：[(相对横坐标, 级别 2=带标签/1=中/0=短, 标签)]。

    级别 2 才带标签；级别 1/0 只画线（长度不同）—— 一把尺子看起来才"有刻度"。
    对齐一律走**日历边界**（），不是"固定小时数的整数倍"。
    """
    span_h = max(MIN_SPAN_H, (t1 - t0).total_seconds() / 3600.0)
    label_c, medium_c, mid_c = pick_step(span_h, width_px)
    h_per_px = span_h / max(1, width_px)
    out: List[Tuple[float, int, str]] = []
    seen = set()

    def emit(step, level: int) -> None:
        if step is None:
            return
        kind, n = step
        for t in _iter_ticks(t0, t1, kind, n):
            x = (t - t0).total_seconds() / 3600.0 / h_per_px
            if not (-1.0 <= x <= width_px + 1.0):
                continue
            key = round(x, 1)
            if key in seen:
                continue
            seen.add(key)
            out.append((x, level, format_time(t, span_h) if level == 2 else ''))

    emit(label_c, 2)
    emit(medium_c, 1)
    emit(mid_c, 0)
    out.sort(key=lambda it: it[0])
    return out


def layout_labels(ticks: Sequence[Tuple[float, int, str]], text_w: int = 74,
                  rows: int = LABEL_ROWS) -> List[Tuple[float, int, str]]:
    """把主刻度标签错落排到 `rows` 行里（3.6.3：正好 3 行，多了尽量错开重叠）。

    贪心：从上往下找第一个"上一个占用位置离得够远"的行；都满了就选最空的
    （允许重叠，但错开行 ⇒ 视觉上不糊成一团）。
    """
    last_x = [-10 ** 9] * max(1, rows)
    out = []
    for x, level, text in ticks:
        if level and text:
            row = 0
            for r in range(len(last_x)):
                if x - last_x[r] >= text_w:
                    row = r
                    break
            else:
                row = min(range(len(last_x)), key=lambda r: last_x[r])
            last_x[row] = x + text_w * 0.5
            out.append((x, row, text))
        else:
            out.append((x, 0, text))
    return out


# ── 数据模型 ────────────────────────────────────────────────────────────

@dataclass
class Segment:
    start: datetime
    end: datetime
    #: 该段对应**文件里第几个 target 块**（0 起）。审查 R8 高#2：
    #:  返回按 start 排序的列表，而 
    #: 按文件行序写入 ⇒ 非时序脚本的段会被**互换**（实测块1/块2 时间对调）。
    #: -1 = 不是从脚本读出来的（UI 新建），写回时按位置回退。
    block_index: int = -1

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass
class SpeedPoint:
    t: datetime
    speed: float = 1.0


@dataclass
class JumpPoint:
    t: datetime
    src: Optional[datetime] = None


@dataclass
class TimelineModel:
    """时间段 / 变速点 / jump / 当前时间 + 视窗（中心 + 跨度）。"""

    now: datetime = field(default_factory=datetime.now)
    center: datetime = field(default_factory=datetime.now)
    span_h: float = 24.0 * 30
    segments: List[Segment] = field(default_factory=list)
    speeds: List[SpeedPoint] = field(default_factory=list)
    jumps: List[JumpPoint] = field(default_factory=list)
    selected: int = -1

    # ── 视窗 ──
    def set_span(self, span_h: float, anchor: Optional[datetime] = None) -> None:
        new = max(MIN_SPAN_H, min(MAX_SPAN_H, float(span_h)))
        if anchor is not None:
            # 以 anchor 为锚点缩放：anchor 的屏幕比例保持不变。
            # ⚠ `frac_of` 可能落在 [0,1] 之外（anchor 远在当前视野外，例如刚打开面板、
            #   视野还停在默认的"当前时间 ±15 天"而脚本讲的是 2028 年）——
            #   不夹住的话 `(0.5-frac)*new` 会把中心甩到几十年以外（实测甩到 1997）。
            frac = min(1.0, max(0.0, self.frac_of(anchor)))
            self.span_h = new
            self.center = anchor + timedelta(hours=(0.5 - frac) * new)
        else:
            self.span_h = new

    def zoom(self, factor: float, anchor: datetime) -> None:
        self.set_span(self.span_h * factor, anchor)

    def pan_hours(self, hours: float) -> None:
        self.center += timedelta(hours=hours)

    def t0(self) -> datetime:
        return self.center - timedelta(hours=self.span_h / 2.0)

    def t1(self) -> datetime:
        return self.center + timedelta(hours=self.span_h / 2.0)

    def frac_of(self, t: datetime) -> float:
        return ((t - self.t0()).total_seconds() / 3600.0) / self.span_h

    def time_at_frac(self, frac: float) -> datetime:
        return self.t0() + timedelta(hours=frac * self.span_h)

    # ── 时间段 ──
    def find_segment(self, t: datetime) -> int:
        for i, s in enumerate(self.segments):
            if s.start <= t <= s.end:
                return i
        return -1

    def sort_segments(self) -> None:
        self.segments.sort(key=lambda s: s.start)

    def next_start_after(self, t: datetime) -> Optional[datetime]:
        best = None
        for s in self.segments:
            if s.start > t and (best is None or s.start < best):
                best = s.start
        return best

    def add_segment(self, t: datetime, default_h: float = DEFAULT_SEGMENT_H
                    ) -> Tuple[Optional[Segment], str]:
        """3.6.2：空白处单击新建段（默认 10 天）。

        · 落在**已有段中间** ⇒ 左端 = 点击处，右端 = 下一个段的**起点**（与它重合）；
          没有下一个段就按默认长度。
        · 与已有段重合 ⇒ 拒绝（3.8 段之间不能重合）。
        返回 (新段 or None, 说明)。
        """
        t = snap(t)
        inside = self.find_segment(t)
        if inside >= 0:
            nxt = self.next_start_after(t)
            end = nxt if nxt is not None else self.segments[inside].end
            if end <= t:
                return None, '右侧没有空间（下一个时间段起点不在右边）'
            # ⚠ 内插必须先**把父段右端收到 t**：旧实现只 append 新段，
            # 父段仍是 [old_start, old_end] ⇒ 两段直接重叠，违反 3.8
            # "时间段不能重合"，且这个重叠会被  写回脚本。
            parent = self.segments[inside]
            if parent.start < t < parent.end:
                parent.end = t
            seg = Segment(t, end)
            self.segments.append(seg)
            self.sort_segments()
            return seg, '在已有时间段内插入（右端与下一段重合）'
        end = t + timedelta(hours=default_h)
        # 不许与任何已有段重合：能塞多少塞多少
        for s in self.segments:
            if s.start >= t and s.start < end:
                end = s.start
        if end <= t:
            return None, '该位置放不下（与已有时间段冲突）'
        seg = Segment(t, end)
        self.segments.append(seg)
        self.sort_segments()
        return seg, '新建时间段（默认 10 天）'

    def move_endpoint(self, idx: int, which: str, t: datetime) -> str:
        """3.6.3：拖端点。自动吸附 + 不许与相邻段重合（3.8）。"""
        if not (0 <= idx < len(self.segments)):
            return '无效段'
        seg = self.segments[idx]
        t = snap(t)
        if which == 'start':
            lo = None
            for s in self.segments:
                if s is not seg and s.end <= seg.start:
                    lo = s.end if lo is None or s.end > lo else lo
            if lo is not None and t < lo:
                t = lo
            if t >= seg.end:
                t = seg.end - timedelta(hours=SNAP_H)
            seg.start = t
        else:
            hi = None
            for s in self.segments:
                if s is not seg and s.start >= seg.end:
                    hi = s.start if hi is None or s.start < hi else hi
            if hi is not None and t > hi:
                t = hi
            if t <= seg.start:
                t = seg.start + timedelta(hours=SNAP_H)
            seg.end = t
        self.sort_segments()
        return ''

    # ── 变速点 / jump ──
    def toggle_speed(self, t: datetime, speed: float = 1.0) -> Tuple[Optional[SpeedPoint], bool]:
        """3.6.4：双击加变速点；再次双击同一个点 = 进入编辑。返回 (点, 是否已存在)。"""
        t = snap(t)
        for sp in self.speeds:
            if abs((sp.t - t).total_seconds()) <= SNAP_H * 1800:
                return sp, True
        sp = SpeedPoint(t, speed)
        self.speeds.append(sp)
        self.speeds.sort(key=lambda s: s.t)
        return sp, False

    def add_jump(self, t: datetime, src: Optional[datetime] = None) -> JumpPoint:
        jp = JumpPoint(snap(t), src)
        self.jumps.append(jp)
        self.jumps.sort(key=lambda j: j.t)
        return jp

    # ── 与脚本文本互转（3.7 实时） ──
    def to_script(self, header: str = '') -> str:
        """把时间轴上的编辑结果写回脚本文本（保留 header 注释行）。

        ⚠ 经纬度/宽度不在时间轴上编辑（那是 3.5 的地图黄框管的），所以这里**不重建**
        target 行，只负责把 jump 点写出来；段的 arrive/depart 由调用方用
        `patch_script_segments` 回写（避免把用户在编辑区手写的 lon/lat 抹掉）。
        """
        lines = []
        if header:
            lines.append(header)
        for j in self.jumps:
            lines.append('jump %s' % j.t.strftime('%Y-%m-%d %H:%M'))
        return '\n'.join(lines)


def patch_script_segments(text: str, segments: Sequence[Segment]) -> str:
    """把时间段写回脚本文本：按**块序**改写每个 target 块的 arrive/depart。

    只动这两行的**时间**，经纬度/宽度/速度行原样保留 —— 时间轴不碰它不该碰的东西。

    ⚠ 审查 R8 高#2/#3（2026-10-15）两条：
      ① `segments_from_script` 返回**按 start 排序**的列表，这里却按**文件行序**取
         `segments[idx]` ⇒ 非时序脚本（块1 晚、块2 早）两个块的时间会**互换**。
         现在按 `Segment.block_index` 对齐；没有 block_index（UI 新建）才按位置回退。
      ② 原实现无条件用 `expand_script` 的展开结果做底稿 ⇒ 用户一拖时间轴，
         整份**粗糙脚本**就被规范化重写（`*2`→`*2.0`、新增行、丢注释位置）。
         现在：**有硬错误就直接返回原文**，让面板去报错，不擅自覆盖。
    """
    from .script_complete import expand_script
    exp, errs = expand_script(text)
    if [e for e in errs if e.level == 'error']:
        return text                      # 解析有硬错误 ⇒ 不覆盖用户原文
    lines = exp.split('\n')
    # 块序 → 段：有 block_index 的优先，其余按出现顺序补位
    by_block = {}
    rest: List[Segment] = []
    for seg in segments:
        bi = getattr(seg, 'block_index', -1)
        if bi is not None and bi >= 0 and bi not in by_block:
            by_block[bi] = seg
        else:
            rest.append(seg)
    blk = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith('target ') or s.startswith('to '):
            blk += 1
            seg = by_block.get(blk)
            if seg is None:
                if not rest:
                    continue
                seg = rest.pop(0)        # 回退：UI 新建的段按顺序贴到剩余块
            # 在块内找 arrive/depart 行
            j = i + 1
            seen = set()
            while j < len(lines):
                st = lines[j].strip()
                if not st:
                    j += 1
                    continue
                if not lines[j].startswith((' ', '\t')):
                    break
                if st.startswith('arrive '):
                    lines[j] = '%sarrive %s' % (_indent_of(lines[j]),
                                                seg.start.strftime('%Y-%m-%d %H:%M'))
                    seen.add('arrive')
                elif st.startswith('depart '):
                    lines[j] = '%sdepart %s' % (_indent_of(lines[j]),
                                                seg.end.strftime('%Y-%m-%d %H:%M'))
                    seen.add('depart')
                j += 1
            # 缺哪行就补哪行（插在块首，保持缩进）
            # ⚠ 审查 R8 高#10 已知未修: 插块首会把原有  顶到 depart 之后。

            missing = [k for k in ('arrive', 'depart') if k not in seen]
            if missing:
                pad = '    '
                add = []
                if 'arrive' in missing:
                    add.append(pad + 'arrive ' + seg.start.strftime('%Y-%m-%d %H:%M'))
                if 'depart' in missing:
                    add.append(pad + 'depart ' + seg.end.strftime('%Y-%m-%d %H:%M'))
                lines[i + 1:i + 1] = add
                continue
    return '\n'.join(lines)


def _indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def segments_from_script(text: str) -> List[Segment]:
    """把脚本里的 target 块读成时间段（start = arrive, end = depart）。

    `TargetRegion` 里到点是 `move_to_date`、离点是 `move_from_date`
    （对应脚本文本里的 arrive / depart）。缺一个就按默认长度补一个占位段，
    保证时间轴上"看得见"这个块。
    """
    from .script_complete import expand_script
    from .script_engine import Script
    out: List[Segment] = []
    try:
        exp, errs = expand_script(text)
        src = exp if not [e for e in errs if e.level == 'error'] else text
        script = Script.parse(src, 'timeline')
    except Exception:
        return out
    blk = -1
    for cmd in script.commands:
        tgt = getattr(cmd, 'target', None)
        if tgt is None:
            continue
        blk += 1                      # 文件里第 blk 个 target 块（与写入顺序一致）
        arrive = getattr(tgt, 'move_to_date', None)
        depart = getattr(tgt, 'move_from_date', None)
        seg = None
        if arrive and depart and depart > arrive:
            seg = Segment(arrive, depart)
        elif arrive:
            seg = Segment(arrive, arrive + timedelta(hours=DEFAULT_SEGMENT_H))
        if seg is not None:
            seg.block_index = blk
            out.append(seg)
    out.sort(key=lambda s: s.start)
    return out
