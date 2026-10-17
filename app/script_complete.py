# app/script_complete.py
"""脚本"粗糙写法"的**补全 / 识别 / 候选**引擎（用户 2026-10-13 需求 3.2–3.4）。

用户原话要点的复述：
```
3.2 编辑界面最上方有"补全"按钮, 把简写补全（给了极端例子：`t 120e 0n 40` ⇒
    `target 120E 0N w=40:` …）
3.3 也可以**直接识别**粗糙脚本而不修改脚本
3.4 输入首字母后按 tab/上下键显示候选命令与格式提示
(命令主要识别**首字母**; 首字母相同则看出现的位置等; 实在无法分辨就报错,
 并给出详细报错地址)
```
设计取舍：
* **一套规范形式 + 一个识别器**。粗糙脚本 → 先展开成规范形式 → 交给已有的
  `script_engine.Script` 解析。这样"能直接识别粗糙脚本"(3.3) 和"补全"(3.2)
  共用同一条通路，不会出现"补全完能跑、不补全跑不了"的分裂。
* 命令按**作用域**（顶层 / target 块内）过滤后再做前缀匹配 ⇒ 首字母冲突大多自然消解；
  仍冲突的（同一作用域、同一首字母、都不是彼此的前缀）报错并给出行列。
* 纯函数、不依赖 pygame / sim ⇒ 可单独测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ── 命令表 ──────────────────────────────────────────────────────────────
# scope: 'top' = 只能在顶层; 'block' = 只能在 target 块内; 'both' = 两处都能
SCOPE_TOP = 'top'
SCOPE_BLOCK = 'block'
SCOPE_BOTH = 'both'


@dataclass(frozen=True)
class CommandDef:
    name: str
    scope: str
    hint: str
    aliases: Tuple[str, ...] = ()


COMMANDS: Tuple[CommandDef, ...] = (
    CommandDef('jump', SCOPE_BOTH, 'jump <datetime>',
               aliases=('j', 'ju', 'jum', 'jump')),
    CommandDef('target', SCOPE_TOP, 'target <lon> <lat> w=<deg>:',
               aliases=('t', 'ta', 'tar', 'targ', 'targe', 'target', 'to')),
    CommandDef('arrive', SCOPE_BLOCK, 'arrive <datetime>',
               aliases=('a', 'ar', 'arr', 'arri', 'arriv', 'arrive', 'ave')),
    CommandDef('depart', SCOPE_BLOCK, 'depart <datetime>',
               # ⚠ 'dt' 是**非前缀**简写（用户 2026-10-13 明确要求："dt 需要被识别为
               # depart"）。它不符合"首字母前缀"的规律，所以必须**显式登记**在别名里，
               # 否则会被判成"未识别"。前缀匹配只看 alias.startswith(token)，
               # 这里 token='dt' 时只有 'dt' 这个别名能命中 ⇒ 不会误伤别的命令。
               aliases=('d', 'de', 'dep', 'depa', 'depar', 'depart', 'dt')),
    CommandDef('wait', SCOPE_BOTH, 'wait <时长> (5s / 2m)',
               aliases=('w', 'wa', 'wai', 'wait')),
    # ⚠ 下面三条是**补漏**（审查 R8 高#3）：引擎  本来就支持
    #   /(等用户点击)、、，
    #   但补全命令表里没有 ⇒ 手写这些行的脚本会被对话框判"解析失败"、运行按钮直接置灰。
    CommandDef('hold', SCOPE_BOTH, 'hold <时长> (5s / 2m)',
               aliases=('h', 'ho', 'hol', 'hold')),
    CommandDef('pause', SCOPE_BOTH, 'pause（等用户点击继续）',
               aliases=('p', 'pa', 'pau', 'paus', 'pause')),
    CommandDef('speed', SCOPE_BOTH, 'speed <倍数>',
               aliases=('sp', 'spe', 'spee', 'speed')),
)
#: 不属于命令、但可出现在行首的记号
SPEED_TOKEN = '*'


@dataclass
class ScriptError:
    """一处报错/警告。`line`/`col` 从 1 开始（给人看的）。"""

    line: int
    col: int
    message: str
    level: str = 'error'

    def __str__(self) -> str:
        return '第%d行 第%d列: %s' % (self.line, self.col, self.message)


@dataclass
class ExpandedLine:
    line_no: int          # 原脚本里的行号（1 起）
    indent: int
    text: str             # 规范化后的行（不含行尾注释）
    comment: str = ''
    scope: str = SCOPE_TOP


@dataclass
class _TargetBlockState:
    """展开过程中的 target 块状态（用来判断当前在哪个作用域）。"""

    open: bool = False


# ── 作用域 ──────────────────────────────────────────────────────────────

def scope_for(prev_line: Optional[ExpandedLine]) -> str:
    """当前行应处在的作用域：上一行是 target 头且没被顶掉 ⇒ 'block'。"""
    if prev_line is None:
        return SCOPE_TOP
    if prev_line.scope == SCOPE_TOP and prev_line.text.rstrip().endswith(':'):
        return SCOPE_BLOCK
    return prev_line.scope


# ── 命令解析 ────────────────────────────────────────────────────────────

def resolve_command(token: str, scope: str) -> Tuple[Optional[CommandDef], Optional[str]]:
    """把（可能是简写的）命令词解析成命令。返回 (命令, 错误信息)。

    规则（照用户要求）：
      ① 先按 scope 过滤（顶层只认 target/jump，块内只认 arrive/depart/wait/jump）；
      ② 在前缀里找"以 token 开头"的命令（token 是首字母/简写都算命中）；
      ③ 命中多条：若其中一条**严格更短**（是其它命中的前缀）则选它 —— 例如块内
         `d` 同时命中 depart（`d` 是 `depart` 的前缀）—— 这不算歧义；
      ④ 仍无法分辨 ⇒ 返回错误字符串（调用方补上行列）。
    """
    tok = token.strip().lower().rstrip(':')
    if not tok:
        return None, '命令为空'
    allowed = [c for c in COMMANDS if c.scope == scope or c.scope == SCOPE_BOTH]
    hit = [c for c in allowed if any(a.startswith(tok) for a in c.aliases)]
    if not hit:
        names = '、'.join(c.name for c in allowed)
        return None, '未识别的命令 %r（此处可用: %s）' % (token, names)
    if len(hit) == 1:
        return hit[0], None
    # ③ 取最短的别名长度作为判据：别名更短的那个是"更明确的简写"
    shortest = min(min(len(a) for a in c.aliases) for c in hit)
    best = [c for c in hit if min(len(a) for a in c.aliases) == shortest]
    if len(best) == 1:
        return best[0], None
    return None, '命令 %r 在此处无法分辨（候选: %s）' % (token, '、'.join(c.name for c in best))


def complete_candidates(prefix: str, scope: str) -> List[Tuple[str, str]]:
    """3.4 的候选表：返回 [(补全文本, 格式提示), ...]。空 prefix ⇒ 全部可用命令。"""
    tok = (prefix or '').strip().lower()
    out: List[Tuple[str, str]] = []
    for c in COMMANDS:
        if c.scope != scope and c.scope != SCOPE_BOTH:
            continue
        if not tok or c.name.startswith(tok) or any(a.startswith(tok) for a in c.aliases):
            out.append((c.name, c.hint))
    return out


# ── 各字段的规范化 ──────────────────────────────────────────────────────

_NUM = re.compile(r'^-?\d+(?:\.\d+)?$')


def _fmt_num(value: float, ndigits: int = 2) -> str:
    """去掉多余的小数零：1.00 → 1, 125.0 → 125, 1.5 → 1.5。"""
    s = ('%.*f' % (ndigits, value)).rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def normalize_coord(token: str) -> Optional[str]:
    """`120e`/`125.0E`/`0n`/`2.0N`/`110` → `120E`/`125E`/`0N`/`2N`/`110`。"""
    t = token.strip().rstrip(',')
    if not t:
        return None
    m = re.match(r'^(-?\d+(?:\.\d+)?)\s*([NnSsEeWw])?$', t)
    if not m:
        return None
    value = float(m.group(1))
    hemi = (m.group(2) or '').upper()
    if hemi in ('S', 'W') and value > 0:
        value = -value
    body = _fmt_num(abs(value) if hemi in ('S', 'W') else value, 2)
    if hemi in ('S', 'W'):
        body = _fmt_num(abs(value), 2)
    return body + hemi


def normalize_width(token: str) -> Optional[float]:
    """`w30` / `w=30` / `=30` / 裸 `40` → 30.0（返回数值）。行尾的 `:` 允许带上
    （`tar 110E 0n w30:` —— 冒号在**行尾 token** 上，用户例子就是这样）。"""
    t = token.strip().rstrip(':')
    m = re.match(r'^[Ww]?\s*=?\s*(-?\d+(?:\.\d+)?)$', t)
    if not m:
        return None
    return float(m.group(1))


def normalize_duration(token: str) -> Optional[str]:
    """时长归一：5s / 2m / 5 → 5s / 2m / 5s。

    引擎 `_parse_duration` **只认 Ns / Nm**；裸数字补单位 s
    （与 hold/wait 的日常写法一致）。
    """
    t = (token or '').strip().lower().replace(' ', '')
    if not t:
        return None
    m = re.match(r'^(\d+(?:\.\d+)?)([sm]?)$', t)
    if not m:
        return None
    num, unit = m.group(1), m.group(2)
    return num + (unit or 's')


def normalize_speed(token: str) -> Optional[str]:
    """`*3` / `*1.00` / `*4.0` → `*3.0` / `*1.0` / `*4.0`（速度保留一位小数）。"""
    t = token.strip()
    if not t.startswith(SPEED_TOKEN):
        return None
    body = t[1:].strip()
    if not _NUM.match(body):
        return None
    return '*%.1f' % float(body)


#: 日期里的"首组数字长度 → 年宽度"判据。用户给的 6 个例子全部覆盖：
_DATE_SPLIT = re.compile(r'\D+')


def normalize_date(text: str) -> Optional[str]:
    """把各种粗糙写法归一成 `YYYY-MM-DD HH:MM`。

    规则：**看第一个数字组的位数**决定年宽（4 ⇒ 四位年，2 ⇒ 两位年补 20），
    其余数字按 月 日 时 分 依次切；缺的补 00。
    覆盖用户给的例子：
        2028012300        → 2028-01-23 00:00
        2028-02-0100      → 2028-02-01 00:00
        2028-02-03 12     → 2028-02-03 12:00
        2028-0210 12:00z  → 2028-02-10 12:00
        202802-181200     → 2028-02-18 12:00
        28-02-2612:00z    → 2028-02-26 12:00
    """
    t = (text or '').strip().rstrip('zZ')
    if not t:
        return None
    groups = [g for g in _DATE_SPLIT.split(t) if g]
    if not groups:
        return None
    first = groups[0]
    if len(first) == 2:
        year = 2000 + int(first)
        rest = ''.join(groups[1:])
    elif len(first) == 4:
        year = int(first)
        rest = ''.join(groups[1:])
    elif len(first) in (6, 8, 10, 12, 14):
        # 首组里**挤了年月**（`202802-181200` / `2028012300` / `20280201`）
        year = int(first[:4])
        rest = first[4:] + ''.join(groups[1:])
    else:
        return None
    fields = [rest[i:i + 2] for i in range(0, len(rest), 2)]
    if len(fields) < 2:
        return None
    try:
        month, day = int(fields[0]), int(fields[1])
        hour = int(fields[2]) if len(fields) > 2 and fields[2] else 0
        minute = int(fields[3]) if len(fields) > 3 and fields[3] else 0
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return '%04d-%02d-%02d %02d:%02d' % (year, month, day, hour, minute)


def parse_date_value(text: str):
    """把 `YYYY-MM-DD HH:MM` 变成 datetime（失败返回 None）。"""
    from datetime import datetime
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return None


# ── 行级展开 ────────────────────────────────────────────────────────────

_INDENT_RE = re.compile(r'^[ \t]*')


def _split_comment(raw: str) -> Tuple[str, str]:
    """拆出行尾注释。`#` 在行首 = 整行注释。"""
    idx = raw.find('#')
    if idx < 0:
        return raw, ''
    return raw[:idx], raw[idx:]


def _expand_tokens(cmd: CommandDef, tokens: Sequence[str], line_no: int, base_col: int,
                   errors: List[ScriptError]) -> Optional[str]:
    """按命令类型把 token 序列规范成一行文本。"""
    # 行尾的冒号属于**行**不属于参数（用户例子 `tar 110E 0n w30:` 的冒号就贴在 w30 上）
    tokens = [t.rstrip(':') for t in tokens]
    out: List[str] = [cmd.name]
    if cmd.name == 'jump':
        if not tokens:
            errors.append(ScriptError(line_no, base_col, 'jump 需要一个时间'))
            return None
        d = normalize_date(' '.join(tokens))
        if d is None:
            errors.append(ScriptError(line_no, base_col, 'jump 的时间无法识别: %s' % ' '.join(tokens)))
            return None
        return 'jump %s' % d
    if cmd.name == 'pause':
        return 'pause'
    if cmd.name in ('hold', 'speed'):
        if not tokens:
            errors.append(ScriptError(line_no, base_col, '%s 需要一个参数' % cmd.name))
            return None
        arg = ' '.join(tokens)
        if cmd.name == 'hold':
            dur = normalize_duration(arg)
            if dur is None:
                errors.append(ScriptError(line_no, base_col,
                                          'hold 的时长无法识别: %s' % arg))
                return None
            return 'hold %s' % dur
        sp = normalize_speed(arg if arg.startswith(SPEED_TOKEN) else SPEED_TOKEN + arg)
        if sp is None:
            errors.append(ScriptError(line_no, base_col, 'speed 的倍数无法识别: %s' % arg))
            return None
        return 'speed %s' % sp[1:]          # 引擎的 speed 行不要 '*'

    if cmd.name == 'wait':
        # 引擎 ：先按时长(5s/2m)，再按日期（日期会被引擎明确拒绝）
        arg = ' '.join(tokens) if tokens else ''
        dur = normalize_duration(arg)
        if dur is not None:
            return 'wait %s' % dur
        if not tokens:
            errors.append(ScriptError(line_no, base_col, 'wait 需要一个时长（5s / 2m）'))
            return None
        d = normalize_date(arg)
        if d is not None:
            errors.append(ScriptError(line_no, base_col,
                                      'wait 不支持日期参数（引擎会拒绝）: %s' % arg))
            return None
        errors.append(ScriptError(line_no, base_col, 'wait 的时长无法识别: %s' % arg))
        return None
    if cmd.name in ('arrive', 'depart'):
        if not tokens:
            errors.append(ScriptError(line_no, base_col, '%s 需要一个时间' % cmd.name))
            return None
        d = normalize_date(' '.join(tokens))
        if d is None:
            errors.append(ScriptError(line_no, base_col,
                                      '%s 的时间无法识别: %s' % (cmd.name, ' '.join(tokens))))
            return None
        return '%s %s' % (cmd.name, d)
    if cmd.name == 'target':
        coord, width, rest = [], None, []
        for tk in tokens:
            if tk.startswith('*'):
                sp = normalize_speed(tk)
                if sp:
                    rest.append(sp)
                continue
            c = normalize_coord(tk)
            if c is not None and len(coord) < 2:
                coord.append(c)
                continue
            if re.match(r'^[Ww]?\s*=?\s*-?\d+(\.\d+)?$', tk):
                width = normalize_width(tk)
                continue
            # 裸数字：已经有两个坐标时，它就是宽度（用户例: `t 120e 0n 40`）
            if _NUM.match(tk) and len(coord) >= 2 and width is None:
                width = float(tk)
                continue
            rest.append(tk)
        if len(coord) < 2:
            errors.append(ScriptError(line_no, base_col,
                                      'target 需要经纬度（已给 %d 个）' % len(coord)))
            return None
        head = 'target %s %s' % (coord[0], coord[1])
        if width is not None:
            head += ' w=%s' % _fmt_num(width, 1)
        # 审查 R8 高#1: `*speed` 不能拼在 `target ...:` 后面 —— 引擎的 target 头
        #   正则是 `w\s*=\s*(\S+)`（不锚行尾，见 script_engine.py:273），会把
        #   冒号与 `*2.0` 一起吞进宽度 ⇒ 直接抛 "无效宽度: 40:"。
        #   其余 token（-smooth 之类）仍留在同一行，速度记号单独成行。
        speeds = [t for t in rest if t.startswith(SPEED_TOKEN)]
        others = [t for t in rest if not t.startswith(SPEED_TOKEN)]
        line = head + ':'
        if others:
            line += ' ' + ' '.join(others)
        if speeds:
            return line + "\n" + "\n".join(speeds)
        return line
    return None


def expand_script(text: str, *, indent: int = 4) -> Tuple[str, List[ScriptError]]:
    """3.2 的"补全"：把粗糙脚本展开成规范脚本。

    返回 (规范文本, 错误/警告列表)。有 error 时**仍然返回**尽力展开的结果，
    由调用方决定是否采纳（编辑区要能边写边看）。
    """
    lines = (text or '').split('\n')
    errors: List[ScriptError] = []
    out_lines: List[str] = []
    prev: Optional[ExpandedLine] = None
    for i, raw in enumerate(lines, start=1):
        body, comment = _split_comment(raw)
        stripped = body.strip()
        if not stripped:
            out_lines.append(comment.strip() if comment.strip() else '')
            continue
        indent_s = _INDENT_RE.match(body).group(0)
        toks = stripped.split()
        first = toks[0]
        # 作用域判据（三层，按用户"适用性一定要好"的要求尽量宽容）：
        #   ① 默认：上一行开了 target 块 ⇒ 块内;
        #   ② 缩进的行才算块内（跟 Python 一样）；
        #   ③ 顶格但顶层解析不了、块内能解析 ⇒ 按块内（块内命令没缩进也能用）。
        # ⚠ 这段必须在 first 定义**之后** —— 放在前面会读到上一轮循环残留的 first
        #   （Python 循环变量不销毁），表现为"偶尔某一行用错作用域"。
        in_block = scope_for(prev) == SCOPE_BLOCK
        cur_scope = SCOPE_BLOCK if (in_block and indent_s) else SCOPE_TOP
        if in_block and not indent_s and not first.startswith(SPEED_TOKEN):
            _top_ok = resolve_command(first, SCOPE_TOP)[0] is not None
            _blk_ok = resolve_command(first, SCOPE_BLOCK)[0] is not None
            if not _top_ok and _blk_ok:
                cur_scope = SCOPE_BLOCK
        # 缩进 = 由最终作用域决定（顶层行不能因为"上一行是块内的"而被缩进）
        level = 1 if cur_scope == SCOPE_BLOCK else 0
        base_col = len(indent_s) + 1
        # 行内单独的速度记号：*3
        if first.startswith(SPEED_TOKEN):
            sp = normalize_speed(first)
            if sp is None:
                errors.append(ScriptError(i, base_col, '速度记号无法识别: %s' % first))
                continue
            new_text = sp
            extra = toks[1:]
            if extra:
                new_text += ' ' + ' '.join(extra)
            out_lines.append(' ' * (indent * 1) + new_text + (('  ' + comment) if comment else ''))
            prev = ExpandedLine(i, 1, new_text, comment, SCOPE_BLOCK)
            continue
        # 块内的参数行：w=xx / -smooth
        if cur_scope == SCOPE_BLOCK and (normalize_width(first) is not None
                                         or first.startswith('-s')):
            if first.startswith('-s'):
                out_lines.append(' ' * indent + '-smooth')
            else:
                out_lines.append(' ' * indent + 'w=%s' % _fmt_num(normalize_width(first), 1))
            prev = ExpandedLine(i, 1, first, comment, SCOPE_BLOCK)
            continue
        cmd, err = resolve_command(first, cur_scope)
        if cmd is None:
            errors.append(ScriptError(i, base_col, err or '命令无法识别'))
            out_lines.append(body.rstrip())
            prev = ExpandedLine(i, level, stripped, comment, cur_scope)
            continue
        new_text = _expand_tokens(cmd, toks[1:], i, base_col + len(first) + 1, errors)
        if new_text is None:
            out_lines.append(body.rstrip())
            prev = ExpandedLine(i, level, stripped, comment, cur_scope)
            continue
        prefix = ' ' * (indent * level)
        # 展开结果可能是**多行**（target 头 + 独立成行的 `*speed`）——
        # 续行要比头行再深一级，否则 `*2.0` 顶格会脱离 target 块。
        if "\n" in new_text:
            head_line, *cont = new_text.split("\n")
            block = prefix + head_line
            for cl in cont:
                block += "\n" + (' ' * (indent * (level + 1))) + cl
            out_lines.append(block + (('  ' + comment) if comment else ''))
        else:
            out_lines.append(prefix + new_text + (('  ' + comment) if comment else ''))
        _head_for_scope = new_text.split("\n")[0]
        prev = ExpandedLine(i, level, _head_for_scope, comment,
                            SCOPE_TOP if _head_for_scope.rstrip().endswith(':') else
                            (SCOPE_BLOCK if cur_scope == SCOPE_BLOCK else SCOPE_TOP))
    return '\n'.join(out_lines), errors


def rough_errors(text: str) -> List[ScriptError]:
    """只取错误（3.3 的"直接识别"路径用它给出报错地址）。"""
    return [e for e in expand_script(text)[1] if e.level == 'error']
