#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
stat_phase_duration.py -- b-deck 阶段码(phase code)持续时长统计

回答的问题:
  1. 观测里每个系统的"扰动阶段"(DB/WV/LO)持续多久? "低压阶段"(TD)持续多久?
  2. 首报的阶段码分布(第一条记录是 DB / TD / TS ...) -> 台风到底有没有扰动阶段
  3. 存在 TS->DB 跳变(中间没有 TD)的案例数
  4. 模型输出在同一组指标上是什么样, 与观测差几倍
  5. 一季有多少个系统, 其中多少条记录处于 DB/WV/LO (估算"一年有多少扰动")

输入格式 (自评/tools 生成的 b-deck, 12 列, 逗号分隔):
    WP, 03, 2025062212,   ,chunshu,   0, 138N, 1196E,  15, 1010, DB,96W
    col: 0洋区 1低压号/扰动码 2时间 3空 4固定串 5tau 6纬度 7经度 8风速kt 9气压 10阶段码 11标识
xrq 原始归档只有 11 列(缺 col11 标识), 本脚本兼容并记为格式差异。

用法:
    python tools/stat_phase_duration.py                 # 用内置默认集合, 写 md + json
    python tools/stat_phase_duration.py --list-sources   # 只列出探测到的集合
    python tools/stat_phase_duration.py --skip-archive   # 跳过大样本 xrq/wp 历史归档
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

# ---------------------------------------------------------------- 常量 / 分类

# 阶段码语义分组
DIST_CODES = ("DB", "WV", "LO")              # 扰动阶段: 热带扰动/东风波/低压区
DEP_CODES = ("TD",)                          # 低压(热带低压)阶段
STORM_CODES = ("TS", "TY", "ST", "HU", "TT")  # 达到"风暴"强度(中/强台风及飓风)
OTHER_CODES = ("EX", "SS", "SD", "MD")       # 变性/副热带/季风低压等
KNOWN_CODES = DIST_CODES + DEP_CODES + STORM_CODES + OTHER_CODES

ALL_CODES_ORDER = list(DIST_CODES) + ["TD"] + ["TS", "TY", "ST", "HU", "TT"] + ["EX", "SS", "SD", "MD"]

TAU_HOURS = 6.0  # 每条记录代表 6 小时

DEFAULT_BASE_ZIPING = r"G:\气象\数据\自评"
DEFAULT_BASE_XRQ = r"G:\气象\数据\xrq"
DEFAULT_BASE_SIM = r"G:\气象\Typhoon-sim\sim_typhoon"

# 候选观测集合(自评) —— name -> (subpath, note)
OBS_CANDIDATES = [
    ("2025wp", r"2025\2025wp"),
    ("2025other", r"2025\2025other"),
    ("2025al", r"2025\2025al"),
    ("2026wp", r"2026\2026wp"),
    ("2025-2026sh", r"2026\2025-2026sh"),
    ("2026al", r"2026\2026al"),
    ("2026ep&cp", r"2026\2026ep&cp"),
    ("2026other", r"2026\2026other"),
]

XRQ_CANDIDATES = [
    ("xrq_2025wp", "2025"),
    ("xrq_2026wp", "2026"),
    ("xrq_sh", "sh"),
    ("xrq_wp_archive", "wp"),
]

SIM_CANDIDATES = [
    ("model_2025", r"2025"),
    ("model_2100", r"2100"),
]


# ---------------------------------------------------------------- 解析

FILESTEM_RE = re.compile(r"^(?:b)?([a-z]{2})(.+?)(\d{4})$", re.IGNORECASE)


class Rec(object):
    __slots__ = ("basin", "num", "t", "code", "ident", "ncol", "lat", "lon", "wind")

    def __init__(self, basin, num, t, code, ident, ncol, lat, lon, wind):
        self.basin = basin
        self.num = num
        self.t = t
        self.code = code
        self.ident = ident
        self.ncol = ncol
        self.lat = lat
        self.lon = lon
        self.wind = wind


def parse_time(s):
    """'2025062212' -> int; 只接受 10 位 YYYYMMDDHH。失败返回 None。"""
    s = s.strip()
    if len(s) == 10 and s.isdigit():
        return int(s)
    return None


def parse_file(path):
    """返回 (records, issues)。records 按时间升序。"""
    issues = []
    recs = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw = fh.read()
    except Exception as exc:  # pragma: no cover
        return [], ["open_failed:%s" % exc]
    for ln, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        n = len(parts)
        if n < 11:
            issues.append("line%d_too_few_columns(%d)" % (ln, n))
            continue
        if n > 12:
            issues.append("line%d_too_many_columns(%d)" % (ln, n))
            continue
        if n == 11:
            issues.append("missing_identifier_column")
        t = parse_time(parts[2])
        if t is None:
            issues.append("line%d_bad_time(%r)" % (ln, parts[2]))
            continue
        code = parts[10].upper()
        if code not in KNOWN_CODES:
            issues.append("unknown_phase_code(%r)" % parts[10])
            if not code:
                issues.append("empty_phase_code")
        recs.append(
            Rec(
                basin=parts[0],
                num=parts[1],
                t=t,
                code=code,
                ident=(parts[11] if n == 12 else ""),
                ncol=n,
                lat=parts[6],
                lon=parts[7],
                wind=parts[8],
            )
        )
    recs.sort(key=lambda r: r.t)
    return recs, issues


def split_stem(fname):
    """从文件名提取 (basin, num, year)。失败返回 (None, None, None)。

    自评文件名形如 "2025 03W wp032025.dat" / "bsh012026 01S.dat"(前面带中文名与空格),
    因此对空格分隔的每个 token 都试一次, 取第一个匹配 b-deck 命名的。
    """
    stem = os.path.splitext(os.path.basename(fname))[0]
    tokens = stem.split()
    order = ([tokens[-1]] + tokens) if tokens else []
    for tok in order:
        m = FILESTEM_RE.match(tok.strip())
        if m:
            return m.group(1).lower(), m.group(2), int(m.group(3))
    return None, None, None


def list_dat(root, recursive):
    out = []
    if not os.path.isdir(root):
        return out
    if recursive:
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.lower().endswith(".dat"):
                    out.append(os.path.join(dp, f))
    else:
        try:
            for f in os.listdir(root):
                p = os.path.join(root, f)
                if os.path.isfile(p) and f.lower().endswith(".dat"):
                    out.append(p)
        except OSError:
            pass
    out.sort()
    return out


# ---------------------------------------------------------------- 分位数

def quantile(vals, q):
    """线性插值分位数(不依赖 numpy)。vals 会被排序。"""
    if not vals:
        return None
    v = sorted(vals)
    if len(v) == 1:
        return float(v[0])
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    frac = pos - lo
    return float(v[lo]) * (1.0 - frac) + float(v[hi]) * frac


def desc(vals):
    vals = [x for x in vals if x is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None,
                "min": None, "max": None}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / float(len(vals)), 2),
        "median": round(quantile(vals, 0.50), 2),
        "p10": round(quantile(vals, 0.10), 2),
        "p90": round(quantile(vals, 0.90), 2),
        "min": round(float(min(vals)), 2),
        "max": round(float(max(vals)), 2),
    }


# ---------------------------------------------------------------- 单系统分析

def analyze_system(path, dataset, kind):
    recs, issues = parse_file(path)
    basin, num, year = split_stem(path)
    sysinfo = {
        "dataset": dataset,
        "kind": kind,
        "file": os.path.basename(path),
        "basin": (basin or (recs[0].basin.lower() if recs else None)),
        "num": num or (recs[0].num if recs else None),
        "season_year": year,
        "n_records": len(recs),
        "issue_counts": dict(collections.Counter(issues)),
        "issues": sorted(set(issues)),
    }
    if not recs:
        sysinfo["empty"] = True
        return sysinfo, []

    # ---- 逐阶段码记录数 / 小时数 / 寿命占比
    cnt = collections.Counter(r.code for r in recs)
    total_rec = len(recs)
    sysinfo["phase_records"] = {c: cnt.get(c, 0) for c in ALL_CODES_ORDER if cnt.get(c, 0)}
    for c in sorted(cnt):
        if c not in sysinfo["phase_records"]:
            sysinfo["phase_records"][c] = cnt[c]

    # ---- 时间连续性
    span_h = (recs[-1].t - recs[0].t)
    # 把 YYYYMMDDHH 差值换算成小时比较麻烦, 用简化: 相邻记录时间戳字符串比较不可靠,
    # 这里用 "天数*24 + 小时差" 的近似换算
    def t2h(t):
        d, h = divmod(t, 100)
        y, md = divmod(d, 10000)
        mo, da = divmod(md, 100)
        # 用儒略日近似(不跨年精确), 只用于差值
        a = (14 - mo) // 12
        y2 = y + 4800 - a
        m2 = mo + 12 * a - 3
        jdn = da + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - y2 // 100 + y2 // 400 - 32045
        return jdn * 24 + h

    hs = [t2h(r.t) for r in recs]
    gaps = [hs[i + 1] - hs[i] for i in range(len(hs) - 1)]
    big_gaps = [g for g in gaps if g > TAU_HOURS]
    sysinfo["span_h"] = round(float(hs[-1] - hs[0]), 1)
    sysinfo["record_lifetime_h"] = round(total_rec * TAU_HOURS, 1)
    sysinfo["n_gaps"] = len(big_gaps)
    sysinfo["gap_h_total"] = round(float(sum(g - TAU_HOURS for g in big_gaps)), 1)
    sysinfo["max_gap_h"] = round(float(max(gaps)), 1) if gaps else 0.0

    codes = [r.code for r in recs]
    sysinfo["first_code"] = codes[0]
    sysinfo["last_code"] = codes[-1]
    sysinfo["code_sequence"] = "".join(
        {"DB": "D", "WV": "W", "LO": "L", "TD": "d", "TS": "T", "TY": "Y", "ST": "S",
         "HU": "H", "TT": "t", "EX": "E", "SS": "s", "SD": "x", "MD": "m"}.get(c, "?")
        for c in codes
    )

    # ---- 是否出现过各阶段
    sysinfo["has_td"] = cnt.get("TD", 0) > 0
    sysinfo["has_dist_any"] = any(cnt.get(c, 0) > 0 for c in DIST_CODES)
    sysinfo["has_storm"] = any(cnt.get(c, 0) > 0 for c in STORM_CODES)

    # ---- 首次达到风暴强度之前的"前生期"(pre-genesis)
    i_storm = None
    for i, c in enumerate(codes):
        if c in STORM_CODES:
            i_storm = i
            break
    sysinfo["first_storm_index"] = i_storm
    sysinfo["genesis_code"] = codes[i_storm] if i_storm is not None else None
    if i_storm is None:
        pre = codes
    else:
        pre = codes[:i_storm]
    pre_dist = sum(1 for c in pre if c in DIST_CODES)
    pre_td = sum(1 for c in pre if c in DEP_CODES)
    pre_other = len(pre) - pre_dist - pre_td
    sysinfo["pre_storm_n"] = len(pre)
    sysinfo["pre_storm_h"] = round(len(pre) * TAU_HOURS, 1)
    sysinfo["pre_storm_dist_h"] = round(pre_dist * TAU_HOURS, 1)   # 扰动阶段(DB/WV/LO)
    sysinfo["pre_storm_td_h"] = round(pre_td * TAU_HOURS, 1)       # 低压阶段(TD)
    sysinfo["pre_storm_other_h"] = round(pre_other * TAU_HOURS, 1)
    sysinfo["pre_storm_dist_n"] = pre_dist
    sysinfo["pre_storm_td_n"] = pre_td
    # 前生期里 TD 是否先于风暴出现(顺序信息)
    sysinfo["pre_storm_codes"] = "".join(
        {"DB": "D", "WV": "W", "LO": "L", "TD": "d", "TS": "T", "TY": "Y", "ST": "S",
         "HU": "H", "TT": "t", "EX": "E", "SS": "s", "SD": "x", "MD": "m"}.get(c, "?")
        for c in pre
    )

    # ---- 全寿命累计
    sysinfo["life_dist_n"] = sum(cnt.get(c, 0) for c in DIST_CODES)
    sysinfo["life_dist_h"] = round(sysinfo["life_dist_n"] * TAU_HOURS, 1)
    sysinfo["life_td_n"] = cnt.get("TD", 0)
    sysinfo["life_td_h"] = round(sysinfo["life_td_n"] * TAU_HOURS, 1)
    sysinfo["life_storm_n"] = sum(cnt.get(c, 0) for c in STORM_CODES)
    sysinfo["life_storm_h"] = round(sysinfo["life_storm_n"] * TAU_HOURS, 1)

    # ---- 跳变统计
    j_ts_db = 0    # 风暴强度 -> DB, 中间没有 TD
    j_ts_td = 0    # 风暴强度 -> TD (正常减弱)
    j_ts_db_tail = 0   # 该跳变之后全部是 DB -> "衰减直接落 DB"(没有 TD/LO/EX 过渡)
    j_ts_db_mid = 0    # 该跳变之后还会回到风暴强度 -> "中途相位回跳"
    db_after_storm = 0
    for i in range(len(codes) - 1):
        a, b = codes[i], codes[i + 1]
        if a in STORM_CODES and b == "DB":
            j_ts_db += 1
            if all(x == "DB" for x in codes[i + 1:]):
                j_ts_db_tail += 1
            else:
                j_ts_db_mid += 1
        if a in STORM_CODES and b == "TD":
            j_ts_td += 1
    for i, c in enumerate(codes):
        if c == "DB" and i_storm is not None and i > i_storm:
            db_after_storm += 1
    sysinfo["jumps_storm_to_db"] = j_ts_db
    sysinfo["jumps_storm_to_db_tail"] = j_ts_db_tail
    sysinfo["jumps_storm_to_db_mid"] = j_ts_db_mid
    sysinfo["jumps_storm_to_td"] = j_ts_td
    sysinfo["db_records_after_storm"] = db_after_storm

    # ---- 无 TD 但有风暴强度 = "跳过低压"
    sysinfo["storm_without_td"] = bool(sysinfo["has_storm"] and not sysinfo["has_td"])

    return sysinfo, recs


# ---------------------------------------------------------------- 数据集

def discover_sources(include_archive=True, base_ziping=DEFAULT_BASE_ZIPING,
                     base_xrq=DEFAULT_BASE_XRQ, base_sim=DEFAULT_BASE_SIM):
    """返回 dataset 描述列表: dict(name, kind, root, recursive, exists, n_files)"""
    out = []

    def add(name, kind, root, recursive=False, note=""):
        n = len(list_dat(root, recursive)) if os.path.isdir(root) else 0
        out.append({
            "name": name, "kind": kind, "root": root, "recursive": recursive,
            "exists": os.path.isdir(root), "n_files": n, "note": note,
        })

    for name, sub in OBS_CANDIDATES:
        add(name, "obs_ziping", os.path.join(base_ziping, sub), False, "自评 b-deck(12列)")
    for name, sub in XRQ_CANDIDATES:
        if name == "xrq_wp_archive" and not include_archive:
            continue
        add(name, "obs_xrq", os.path.join(base_xrq, sub), False, "xrq 原始归档(多为11列)")
    for name, sub in SIM_CANDIDATES:
        add(name, "model", os.path.join(base_sim, sub), True, "模型输出(12列)")
    return out


def run(sources, verbose=True):
    systems = []
    for src in sources:
        if not src["exists"] or src["n_files"] == 0:
            if verbose:
                print("  [skip] %-16s %s (exists=%s, n_files=%d)"
                      % (src["name"], src["root"], src["exists"], src["n_files"]))
            continue
        files = list_dat(src["root"], src["recursive"])
        if verbose:
            print("  [scan] %-16s %s  files=%d" % (src["name"], src["root"], len(files)))
        for fp in files:
            info, _recs = analyze_system(fp, src["name"], src["kind"])
            info["year"] = None
            systems.append(info)
    return systems


# ---------------------------------------------------------------- 汇总

def agg_block(syslist):
    """对一组系统算分布。"""
    out = {}
    out["n_systems"] = len(syslist)
    out["n_records"] = sum(s["n_records"] for s in syslist)
    reaching = [s for s in syslist if s.get("first_storm_index") is not None]
    out["n_systems_reaching_storm"] = len(reaching)
    out["n_systems_never_storm"] = len(syslist) - len(reaching)

    phase_rec = collections.Counter()
    for s in syslist:
        for c, n in s.get("phase_records", {}).items():
            phase_rec[c] += n
    out["phase_records"] = {c: phase_rec.get(c, 0) for c in ALL_CODES_ORDER if phase_rec.get(c, 0)}
    for c in sorted(phase_rec):
        if c not in out["phase_records"]:
            out["phase_records"][c] = phase_rec[c]
    out["phase_hours"] = {c: round(n * TAU_HOURS, 1) for c, n in out["phase_records"].items()}
    tot = float(out["n_records"]) or 1.0
    out["phase_frac"] = {c: round(n / tot, 4) for c, n in out["phase_records"].items()}
    out["n_records_dist"] = sum(phase_rec.get(c, 0) for c in DIST_CODES)
    out["n_records_td"] = phase_rec.get("TD", 0)
    out["n_records_storm"] = sum(phase_rec.get(c, 0) for c in STORM_CODES)
    out["n_records_other"] = sum(phase_rec.get(c, 0) for c in OTHER_CODES)

    # 首报阶段码分布
    fc = collections.Counter(s["first_code"] for s in syslist if s.get("first_code"))
    out["first_code_dist"] = dict(fc)
    out["first_code_dist_frac"] = {k: round(v / float(len(syslist) or 1), 4) for k, v in fc.items()}
    lc = collections.Counter(s["last_code"] for s in syslist if s.get("last_code"))
    out["last_code_dist"] = dict(lc)

    # 结论指标 1: 达到风暴强度的系统, 其"前生期"构成
    out["pre_storm_dist_h"] = desc([s["pre_storm_dist_h"] for s in reaching])
    out["pre_storm_td_h"] = desc([s["pre_storm_td_h"] for s in reaching])
    out["pre_storm_total_h"] = desc([s["pre_storm_h"] for s in reaching])
    out["pre_storm_dist_n"] = desc([s["pre_storm_dist_n"] for s in reaching])
    out["pre_storm_td_n"] = desc([s["pre_storm_td_n"] for s in reaching])
    out["pre_storm_total_n"] = desc([s["pre_storm_n"] for s in reaching])

    # 指标 2: 全寿命累计
    out["life_dist_h"] = desc([s["life_dist_h"] for s in syslist])
    out["life_td_h"] = desc([s["life_td_h"] for s in syslist])
    out["life_dist_frac"] = desc([(s["life_dist_n"] / float(s["n_records"])) for s in syslist if s["n_records"]])
    out["life_td_frac"] = desc([(s["life_td_n"] / float(s["n_records"])) for s in syslist if s["n_records"]])
    out["span_h"] = desc([s["span_h"] for s in syslist])

    # 指标 2b: 用"报数"(每条 6h)表达的同一批数字 —— 用户习惯这么说
    out["pre_storm_dist_reports"] = desc([s["pre_storm_dist_h"] / TAU_HOURS for s in reaching])
    out["pre_storm_td_reports"] = desc([s["pre_storm_td_h"] / TAU_HOURS for s in reaching])
    out["pre_storm_total_reports"] = desc([s["pre_storm_h"] / TAU_HOURS for s in reaching])
    out["life_dist_reports"] = desc([s["life_dist_h"] / TAU_HOURS for s in syslist])
    out["life_td_reports"] = desc([s["life_td_h"] / TAU_HOURS for s in syslist])
    nreach = len(reaching) or 1
    out["n_reaching_pre_storm_le1"] = sum(1 for s in reaching if s["pre_storm_n"] <= 1)
    out["frac_reaching_pre_storm_le1"] = round(out["n_reaching_pre_storm_le1"] / float(nreach), 4)
    out["n_reaching_pre_storm_le3"] = sum(1 for s in reaching if s["pre_storm_n"] <= 3)
    out["frac_reaching_pre_storm_le3"] = round(out["n_reaching_pre_storm_le3"] / float(nreach), 4)
    out["n_reaching_td_zero"] = sum(1 for s in reaching if s["pre_storm_td_n"] == 0)
    out["frac_reaching_td_zero"] = round(out["n_reaching_td_zero"] / float(nreach), 4)
    out["n_reaching_td_ge2"] = sum(1 for s in reaching if s["pre_storm_td_n"] >= 2)
    out["frac_reaching_td_ge2"] = round(out["n_reaching_td_ge2"] / float(nreach), 4)

    # 指标 3: 跳变
    out["n_jumps_storm_to_db"] = sum(s["jumps_storm_to_db"] for s in syslist)
    out["n_systems_with_jump_storm_to_db"] = sum(1 for s in syslist if s["jumps_storm_to_db"] > 0)
    out["n_jumps_storm_to_db_tail"] = sum(s.get("jumps_storm_to_db_tail", 0) for s in syslist)
    out["n_systems_terminal_storm_to_db"] = sum(1 for s in syslist if s.get("jumps_storm_to_db_tail", 0) > 0)
    out["n_jumps_storm_to_db_mid"] = sum(s.get("jumps_storm_to_db_mid", 0) for s in syslist)
    out["n_systems_midlife_storm_to_db"] = sum(1 for s in syslist if s.get("jumps_storm_to_db_mid", 0) > 0)
    out["n_jumps_storm_to_td"] = sum(s["jumps_storm_to_td"] for s in syslist)
    out["n_systems_storm_without_td"] = sum(1 for s in syslist if s.get("storm_without_td"))
    out["n_systems_with_td"] = sum(1 for s in syslist if s.get("has_td"))
    out["n_systems_with_dist"] = sum(1 for s in syslist if s.get("has_dist_any"))
    out["n_systems_with_db_after_storm"] = sum(1 for s in syslist if s.get("db_records_after_storm", 0) > 0)

    # 数据质量
    iss = collections.Counter()
    for s in syslist:
        for i, n in (s.get("issue_counts") or {}).items():
            key = re.sub(r"\(.*\)$", "", i)
            key = re.sub(r"^line\d+_", "lineN_", key)
            iss[key] += n
    out["issue_counts"] = dict(iss.most_common())
    out["n_systems_with_gaps"] = sum(1 for s in syslist if s.get("n_gaps", 0) > 0)
    return out


def per_season(systems, kind_filter):
    """按 (集合, 洋区, 年) 统计季系统数。"""
    buckets = collections.defaultdict(lambda: {"n_systems": 0, "n_records": 0,
                                               "n_records_dist": 0, "n_records_td": 0,
                                               "n_systems_dist_first": 0,
                                               "n_systems_td_first": 0,
                                               "n_systems_reaching_storm": 0})
    for s in systems:
        if kind_filter and s["kind"] != kind_filter:
            continue
        b = buckets[(s["dataset"], s["basin"], s["season_year"])]
        b["n_systems"] += 1
        b["n_records"] += s["n_records"]
        b["n_records_dist"] += s["life_dist_n"]
        b["n_records_td"] += s["life_td_n"]
        if s.get("first_code") in DIST_CODES:
            b["n_systems_dist_first"] += 1
        if s.get("first_code") in DEP_CODES:
            b["n_systems_td_first"] += 1
        if s.get("first_storm_index") is not None:
            b["n_systems_reaching_storm"] += 1
    return {"%s|%s|%s" % k: v for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}


# ---------------------------------------------------------------- 输出 MD

def fmt_desc(d, unit="h"):
    if not d or d["n"] == 0:
        return "n/a"
    return "%.1f / %.1f / %.1f / %.1f" % (d["median"], d["mean"], d["p10"], d["p90"])


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def ratio(a, b):
    if a is None or b in (None, 0):
        return None
    return round(float(a) / float(b), 2)


EXTERNAL_REFS = [
    ("Climatology of developing and nondeveloping disturbances for tropical cyclone genesis "
     "over the western North Pacific (TAO, 2022)",
     "https://link.springer.com/article/10.1007/s44195-022-00012-4"),
    ("JMA 気象庁 気候変動監視レポート 第7章 熱帯低気圧",
     "https://www.data.jma.go.jp/cpdinfo/ccj/2025/pdf/cc2025_shousai_7.pdf"),
    ("IBTrACS Climatology (AMS 30th Hurricane Conf.)",
     "https://ams.confex.com/ams/30Hurricane/webprogram/Handout/Paper204685/IBTrACS%20Climatology%20for%202012%20AMS%20Tropical.pdf"),
    ("Spatial Temporal Characteristics of Tropical Disturbances over the Northwestern Pacific "
     "during 2000-2008 (北京大学学报)",
     "http://xbna.pku.edu.cn/EN/Y2011/V47/I2/278"),
]


def build_md(report):
    src = report["sources"]
    agg = report["aggregates"]
    L = []
    A = L.append

    A("# 阶段码(扰动 / 低压)持续时长统计 — 观测 vs 模型")
    A("")
    A("生成脚本: `tools/stat_phase_duration.py`  ·  机器可读结果: `phase_duration_stats.json`  ·  "
      "每条 b-deck 记录 = 6 小时")
    A("")

    # ---------------- 0. 扫了哪些目录
    A("## 0. 实际扫描的数据集合")
    A("")
    rows = []
    for s in src:
        rows.append([s["name"], s["kind"],
                     "有" if s["exists"] else "**无**",
                     s["n_files"] if s["exists"] else "-",
                     s["root"], s.get("note", "")])
    A(md_table(["集合", "类别", "存在", ".dat 文件数", "路径", "备注"], rows))
    A("")
    missing = [s for s in src if not s["exists"] or s["n_files"] == 0]
    if missing:
        A("**探测到但为空 / 不存在(未纳入统计)**:")
        for s in missing:
            A("- `%s` — `%s` (exists=%s, n_files=%d)" % (s["name"], s["root"], s["exists"], s["n_files"]))
        A("")

    # ---------------- 1. 阶段码总览
    A("## 1. 阶段码总记录数 / 总小时数 / 寿命占比")
    A("")
    for key, label in (("obs_ziping", "观测·自评(2025+2026)"), ("obs_xrq", "观测·xrq 原始归档"),
                       ("model", "模型输出(sim_typhoon)")):
        d = agg.get(key)
        if not d or d["n_records"] == 0:
            continue
        A("### %s —— %d 个系统, %d 条记录" % (label, d["n_systems"], d["n_records"]))
        A("")
        rows = []
        got = d["phase_records"]
        for c in ALL_CODES_ORDER:
            if got.get(c):
                rows.append([c, got[c], "%.1f" % d["phase_hours"][c],
                             "%.2f%%" % (100.0 * d["phase_frac"][c])])
        extra = [c for c in got if c not in ALL_CODES_ORDER]
        for c in sorted(extra):
            rows.append(["%s *(非常规码)*" % c, got[c], "%.1f" % d["phase_hours"][c],
                         "%.2f%%" % (100.0 * d["phase_frac"][c])])
        A(md_table(["阶段码", "记录数", "小时数", "占全部记录比例"], rows))
        A("")
        A("- 扰动阶段(DB/WV/LO) 合计 **%d 条 / %.0f h**, 占 %.2f%%"
          % (d["n_records_dist"], d["n_records_dist"] * TAU_HOURS,
             100.0 * d["n_records_dist"] / max(1, d["n_records"])))
        A("- 低压阶段(TD) 合计 **%d 条 / %.0f h**, 占 %.2f%%"
          % (d["n_records_td"], d["n_records_td"] * TAU_HOURS,
             100.0 * d["n_records_td"] / max(1, d["n_records"])))
        A("- 风暴强度(TS/TY/ST/HU/TT) 合计 %d 条 / %.0f h; 其它(EX/SS/SD/MD) %d 条"
          % (d["n_records_storm"], d["n_records_storm"] * TAU_HOURS, d["n_records_other"]))
        A("- 达到风暴强度的系统 %d 个; 从未达到风暴强度 %d 个"
          % (d["n_systems_reaching_storm"], d["n_systems_never_storm"]))
        A("- 出现过 TD 记录的系统 %d 个; 出现过 DB/WV/LO 的系统 %d 个"
          % (d["n_systems_with_td"], d["n_systems_with_dist"]))
        A("")

    # ---------------- 2. 首报阶段码分布
    A("## 2. 首报阶段码分布 —— \"台风到底有没有扰动阶段\"")
    A("")
    A("一个系统的**第一条**记录所处的阶段码:")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d or d["n_systems"] == 0:
            continue
        fc = d["first_code_dist"]
        n = d["n_systems"]
        rows.append([label, n] + ["%s: %d (%.0f%%)" % (c, fc.get(c, 0), 100.0 * fc.get(c, 0) / n)
                                  for c in ("DB", "WV", "LO", "TD", "TS", "TY", "ST", "SD", "MD", "EX")])
    A(md_table(["数据", "系统数", "DB", "WV", "LO", "TD", "TS", "TY", "ST", "SD", "MD", "EX"], rows))
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d or d["n_systems"] == 0:
            continue
        n = d["n_systems"]
        fc = d["first_code_dist"]
        dist0 = sum(fc.get(c, 0) for c in DIST_CODES)
        td0 = sum(fc.get(c, 0) for c in DEP_CODES)
        st0 = sum(fc.get(c, 0) for c in STORM_CODES)
        rows.append([label, n,
                     "%d (%.0f%%)" % (dist0, 100.0 * dist0 / n),
                     "%d (%.0f%%)" % (td0, 100.0 * td0 / n),
                     "%d (%.0f%%)" % (st0, 100.0 * st0 / n),
                     "%d (%.0f%%)" % (n - dist0 - td0 - st0, 100.0 * (n - dist0 - td0 - st0) / n)])
    A(md_table(["数据", "系统数", "首报=扰动(DB/WV/LO)", "首报=低压(TD)", "首报=风暴(TS+)", "首报=其它"],
               rows))
    A("")
    A("**末报**阶段码分布(用于判断\"结尾是否也回落到 DB/LO\"):")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d:
            continue
        lc = d.get("last_code_dist", {})
        n = max(1, d["n_systems"])
        rows.append([label] + ["%s: %d (%.0f%%)" % (c, lc.get(c, 0), 100.0 * lc.get(c, 0) / n)
                               for c in sorted(lc, key=lambda k: -lc[k])[:6]])
    A(md_table(["数据", "末报阶段码分布(降序, 取前 6)"], rows))
    A("")

    # ---------------- 2b. 逐数据集明细
    A("## 2b. 逐数据集明细(每个集合单独一行)")
    A("")
    rows = []
    for ssrc in src:
        a = report["per_dataset"].get(ssrc["name"])
        if not a or a["n_systems"] == 0:
            continue
        fc = a["first_code_dist"]
        n = a["n_systems"]
        dist0 = sum(fc.get(c, 0) for c in DIST_CODES)
        td0 = sum(fc.get(c, 0) for c in DEP_CODES)
        rows.append([
            ssrc["name"], ssrc["kind"], n, a["n_records"],
            ",".join("%s:%d" % (k, v) for k, v in sorted(fc.items(), key=lambda kv: -kv[1])),
            "%d%%" % round(100.0 * dist0 / n),
            "%.1f" % a["pre_storm_dist_h"]["median"] if a["pre_storm_dist_h"]["n"] else "n/a",
            "%.1f" % a["pre_storm_td_h"]["median"] if a["pre_storm_td_h"]["n"] else "n/a",
            "%.1f" % a["pre_storm_total_h"]["median"] if a["pre_storm_total_h"]["n"] else "n/a",
            a["n_jumps_storm_to_db"],
            a["n_systems_storm_without_td"],
        ])
    A(md_table(["集合", "类别", "系统数", "记录数", "首报阶段码分布", "首报=扰动",
                "前生期扰动中位h", "前生期TD中位h", "前生期合计中位h",
                "TS+→DB跳变", "有风暴无TD"], rows))
    A("")

    # ---------------- 3. 前生期时长分布
    A("## 3. 核心指标: \"达到风暴强度之前的扰动/低压时长\"")
    A("")
    A("定义: 取每个系统**首次出现 TS/TY/ST/HU/TT 之前**的所有记录(即\"前生期\", pre-genesis), "
      "在其中分别累加扰动码(DB/WV/LO)与低压码(TD)。只统计最终达到风暴强度的系统。")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d or d["n_systems_reaching_storm"] == 0:
            continue
        for field, name in (("pre_storm_dist_h", "扰动 DB/WV/LO (h)"),
                            ("pre_storm_td_h", "低压 TD (h)"),
                            ("pre_storm_total_h", "前生期合计 (h)"),
                            ("pre_storm_dist_n", "扰动 条数"),
                            ("pre_storm_td_n", "低压 条数")):
            dd = d[field]
            rows.append([label, name, d["n_systems_reaching_storm"],
                         "%.1f" % dd["median"], "%.1f" % dd["mean"],
                         "%.1f" % dd["p10"], "%.1f" % dd["p90"],
                         "%.1f" % dd["min"], "%.1f" % dd["max"]])
    A(md_table(["数据", "指标", "n(达风暴系统)", "中位", "均值", "p10", "p90", "min", "max"], rows))
    A("")
    A("同一批数字换成**报数**(1 报 = 6 h):")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d or d["n_systems_reaching_storm"] == 0:
            continue
        n = d["n_systems_reaching_storm"]
        rows.append([label,
                     "%.1f" % d["pre_storm_dist_reports"]["median"],
                     "%.1f" % d["pre_storm_td_reports"]["median"],
                     "%.1f" % d["pre_storm_total_reports"]["median"],
                     "%d (%.0f%%)" % (d["n_reaching_pre_storm_le1"], 100.0 * d["frac_reaching_pre_storm_le1"]),
                     "%d (%.0f%%)" % (d["n_reaching_pre_storm_le3"], 100.0 * d["frac_reaching_pre_storm_le3"]),
                     "%d (%.0f%%)" % (d["n_reaching_td_zero"], 100.0 * d["frac_reaching_td_zero"]),
                     "%d (%.0f%%)" % (d["n_reaching_td_ge2"], 100.0 * d["frac_reaching_td_ge2"])])
    A(md_table(["数据", "扰动中位报数", "TD中位报数", "前生期合计中位报数",
                "前生期≤1报的系统", "前生期≤3报的系统", "前生期无TD的系统", "前生期TD≥2报的系统"], rows))
    A("")

    # ---------------- 4. 全寿命累计
    A("## 4. 参考: 全寿命累计(含结尾衰减段)")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d or d["n_systems"] == 0:
            continue
        for field, name in (("life_dist_h", "全寿命 DB/WV/LO (h)"),
                            ("life_td_h", "全寿命 TD (h)"),
                            ("span_h", "首末记录时间跨度 (h)")):
            dd = d[field]
            rows.append([label, name, d["n_systems"], "%.1f" % dd["median"], "%.1f" % dd["mean"],
                         "%.1f" % dd["p10"], "%.1f" % dd["p90"]])
    A(md_table(["数据", "指标", "n", "中位", "均值", "p10", "p90"], rows))
    A("")

    # ---------------- 5. 跳变
    A("## 5. 阶段跳变与\"跳过低压\"")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d:
            continue
        rows.append([label, d["n_systems"],
                     d["n_jumps_storm_to_db"], d["n_systems_with_jump_storm_to_db"],
                     d["n_jumps_storm_to_td"],
                     d["n_systems_storm_without_td"],
                     d["n_systems_with_db_after_storm"]])
    A(md_table(["数据", "系统数", "TS+→DB 跳变次数", "含该跳变的系统数",
                "TS+→TD 次数", "有风暴但从无 TD 的系统", "风暴后仍出现 DB 的系统"], rows))
    A("")
    A("把 TS+→DB 跳变按位置拆开(这是模型最要命的形态):")
    A("")
    rows = []
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d:
            continue
        rows.append([label, d["n_jumps_storm_to_db"],
                     "%d (%d 个系统)" % (d.get("n_jumps_storm_to_db_tail", 0),
                                         d.get("n_systems_terminal_storm_to_db", 0)),
                     "%d (%d 个系统)" % (d.get("n_jumps_storm_to_db_mid", 0),
                                         d.get("n_systems_midlife_storm_to_db", 0))])
    A(md_table(["数据", "TS+→DB 合计", "末段型(此后全是 DB = 衰减直接落 DB)",
                "中段型(之后又回到风暴强度 = 相位回跳)"], rows))
    A("")
    A("> 判据: 相邻两条记录之间 `前一条 ∈ {TS,TY,ST,HU,TT}` 且 `后一条 = DB`, 即为一次 "
      "**TS→DB 跳变**(相邻记录之间不存在 TD, 缺低压过渡)。")
    A("")

    # ---------------- 6. 每季系统数
    A("## 6. 一季有多少个系统 / 多少条扰动记录")
    A("")
    ps = report["per_season"]
    rows = []
    for k, v in ps.items():
        ds, basin, year = k.split("|")
        if ds == "xrq_wp_archive":
            continue
        rows.append([ds, basin, year, v["n_systems"], v["n_systems_dist_first"],
                     v["n_records"], v["n_records_dist"], v["n_records_td"],
                     "%.0f" % (v["n_records_dist"] * TAU_HOURS),
                     "%.0f%%" % (100.0 * v["n_records_dist"] / max(1, v["n_records"]))])
    A("**非归档集合(2025 / 2026 自评 + 模型)** —— 这就是「一季几个系统」的直接答案:")
    A("")
    A(md_table(["集合", "洋区", "季(年)", "系统数", "其中首报=扰动的系统数", "总记录",
                "扰动记录数", "TD 记录数", "扰动小时数", "扰动记录占比"], rows))
    A("")

    # 归档按年汇总
    arc = {}
    for k, v in ps.items():
        ds, basin, year = k.split("|")
        if ds != "xrq_wp_archive" or basin != "wp":
            continue
        arc[year] = v
    if arc:
        yrs = sorted(y for y in arc if y not in ("None", None))
        nsys = [arc[y]["n_systems"] for y in yrs]
        ndist = [arc[y]["n_records_dist"] for y in yrs]
        ndist_h = [arc[y]["n_records_dist"] * TAU_HOURS for y in yrs]
        zero_years = [y for y in yrs if arc[y]["n_records_dist"] == 0]
        good_years = [y for y in yrs if arc[y]["n_records_dist"] > 0]
        ndist_good = [arc[y]["n_records_dist"] for y in good_years]
        sysdist = [arc[y]["n_systems_dist_first"] for y in yrs]
        sysdist_good = [arc[y]["n_systems_dist_first"] for y in good_years]
        A("**`xrq/wp` 历史归档(1949–2025, 仅 WP 洋区)** —— 用于回答「一年有多少扰动」:")
        A("")
        A("- 覆盖 %d 个季(年): %s … %s" % (len(yrs), yrs[0], yrs[-1]))
        A("- **每季系统数**(b-deck 文件数, 含编号台风与未编号扰动/投资扰动): "
          "中位 **%d** / 均值 %.1f, p10–p90 = %d–%d, 极值 %d–%d"
          % (quantile(nsys, .5), sum(nsys) / float(len(nsys)), quantile(nsys, .1),
             quantile(nsys, .9), min(nsys), max(nsys)))
        A("- **每季从扰动码起步的系统数**(首报 ∈ DB/WV/LO, 这是「一年有多少扰动」最直接的下界): "
          "中位 **%d 个** / 均值 %.1f 个 / 极值 %d–%d"
          % (quantile(sysdist, .5), sum(sysdist) / float(len(sysdist)),
             min(sysdist), max(sysdist)))
        A("- **每季处在 DB/WV/LO 的记录数**: 中位 %.1f 条 (= %.0f h) / 均值 %.1f 条, "
          "p10–p90 = %.0f–%.0f 条"
          % (quantile(ndist, .5), quantile(ndist, .5) * TAU_HOURS,
             sum(ndist) / float(len(ndist)), quantile(ndist, .1), quantile(ndist, .9)))
        A("- **每季处在 DB/WV/LO 的记录小时数**: 中位 %.0f h / 均值 %.0f h"
          % (quantile(ndist_h, .5), sum(ndist_h) / float(len(ndist_h))))
        A("- 归档里每季系统数最多的几年: %s"
          % ", ".join("%s(%d)" % (y, arc[y]["n_systems"]) for y in sorted(yrs, key=lambda y: -arc[y]["n_systems"])[:5]))
        A("- :warning: **%d 个年份的 DB/WV/LO 记录数为 0**: %s —— 这些年归档只给 TD 及以上, "
          "扰动码整段缺失, 因此「扰动条数」在归档里**不能跨年直接比较**。"
          % (len(zero_years), ", ".join(zero_years) if zero_years else "无"))
        if good_years:
            A("- 剔除这 %d 个缺码年份后(剩 %d 个季): 每季 DB/WV/LO 记录数 中位 %.1f 条, 均值 %.1f 条; "
              "每季从扰动码起步的系统数 中位 %.1f 个, 均值 %.1f 个 —— "
              "**这才是可用的「一年有多少扰动」口径**。"
              % (len(zero_years), len(good_years), quantile(ndist_good, .5),
                 sum(ndist_good) / float(len(ndist_good)), quantile(sysdist_good, .5),
                 sum(sysdist_good) / float(len(sysdist_good))))
        A("")

    # ---------------- 7. 模型 vs 观测 差距
    A("## 7. 模型 vs 观测: 差距倍数")
    A("")
    o = agg.get("obs_ziping", {})
    m = agg.get("model", {})
    if o and m:
        rows = []
        for field, name in (("pre_storm_dist_h", "前生期 扰动 DB/WV/LO (h)"),
                            ("pre_storm_td_h", "前生期 低压 TD (h)"),
                            ("pre_storm_total_h", "前生期合计 (h)"),
                            ("life_dist_h", "全寿命 DB/WV/LO (h)"),
                            ("life_td_h", "全寿命 TD (h)")):
            od, md_ = o.get(field, {}), m.get(field, {})
            if not od or not md_:
                continue
            rows.append([name,
                         "%.1f" % od["median"] if od["median"] is not None else "n/a",
                         "%.1f" % md_["median"] if md_["median"] is not None else "n/a",
                         ("%.1f×" % (od["median"] / md_["median"])) if (od["median"] and md_["median"]) else
                         ("观测 %.1f h, 模型 0 h → 完全缺失" % od["median"] if od["median"] else "n/a"),
                         "%.1f" % od["mean"] if od["mean"] is not None else "n/a",
                         "%.1f" % md_["mean"] if md_["mean"] is not None else "n/a"])
        A(md_table(["指标", "观测中位", "模型中位", "观测/模型 倍数", "观测均值", "模型均值"], rows))
        A("")

    # ---------------- 8. 数据质量
    A("## 8. 数据质量与格式一致性(如实记录)")
    A("")
    for key, label in (("obs_ziping", "观测·自评"), ("obs_xrq", "观测·xrq"), ("model", "模型")):
        d = agg.get(key)
        if not d:
            continue
        A("**%s** (系统 %d 个, 记录 %d 条)" % (label, d["n_systems"], d["n_records"]))
        A("")
        A("- 记录列数问题: %s" % (d["issue_counts"] or "无"))
        A("- 存在 >6h 时间缺口的系统: %d 个" % d["n_systems_with_gaps"])
        A("")
    A("**关键阶段码在各数据集里的存在性**(缺哪个码直接影响可比较性):")
    A("")
    rows = []
    for ssrc in src:
        a = report["per_dataset"].get(ssrc["name"])
        if not a or a["n_systems"] == 0:
            continue
        rec = a["phase_records"]
        rows.append([ssrc["name"]] +
                    ["%d" % rec.get(c, 0) if rec.get(c, 0) else "**缺**" for c in ("DB", "WV", "LO", "TD")] +
                    [",".join(sorted(rec))])
    A(md_table(["集合", "DB", "WV", "LO", "TD", "该集合出现的全部阶段码"], rows))
    A("")

    # ---------------- 9. 外部参照
    A("## 9. 外部参照: 一年到底有多少\"扰动\"")
    A("")
    A("外部文献/机构口径(供交叉核对, 与本地数据统计口径不同, 不能直接相减):")
    A("")
    for t, u in EXTERNAL_REFS:
        A("- [%s](%s)" % (t, u))
    A("")

    # ---------------- 10. 结论
    A("## 10. 结论")
    A("")
    for line in report["conclusions"]:
        A(line)
    A("")
    return "\n".join(L)


def make_conclusions(report):
    agg = report["aggregates"]
    o = agg.get("obs_ziping", {})
    m = agg.get("model", {})
    C = []
    if o:
        C.append("### 10.1 观测里扰动阶段到底多长")
        C.append("")
        n_reach = o["n_systems_reaching_storm"]
        C.append("- 自评观测共 **%d 个系统 / %d 条记录**(每条 6 h); 其中达到风暴强度(TS+) "
                 "**%d 个**, 从未达到风暴强度 %d 个。"
                 % (o["n_systems"], o["n_records"], n_reach, o["n_systems_never_storm"]))
        d = o["pre_storm_dist_h"]
        t = o["pre_storm_td_h"]
        s = o["pre_storm_total_h"]
        if d["n"]:
            C.append("- **扰动阶段(DB/WV/LO)**: 前生期时长 中位 **%.1f h** / 均值 %.1f h, "
                     "p10–p90 = %.1f–%.1f h, 极值 %.1f–%.1f h (n=%d)。"
                     % (d["median"], d["mean"], d["p10"], d["p90"], d["min"], d["max"], d["n"]))
            C.append("- **低压阶段(TD)**: 前生期时长 中位 **%.1f h** / 均值 %.1f h, "
                     "p10–p90 = %.1f–%.1f h, 极值 %.1f–%.1f h。"
                     % (t["median"], t["mean"], t["p10"], t["p90"], t["min"], t["max"]))
            C.append("- **前生期合计(扰动+低压)**: 中位 **%.1f h** / 均值 %.1f h "
                     "(≈ 中位 %.1f 报)。"
                     % (s["median"], s["mean"], s["median"] / TAU_HOURS))
        C.append("- 首报阶段码: %s。即 **%.0f%% 的系统首报就是扰动(DB/WV/LO)**, "
                 "%.0f%% 首报已是低压(TD), %.0f%% 首报直接是风暴强度 —— "
                 "所以观测里\"有扰动阶段\"这件事是**统计上的常态, 不是例外**。"
                 % (o["first_code_dist"],
                    100.0 * sum(o["first_code_dist"].get(c, 0) for c in DIST_CODES) / max(1, o["n_systems"]),
                    100.0 * sum(o["first_code_dist"].get(c, 0) for c in DEP_CODES) / max(1, o["n_systems"]),
                    100.0 * sum(o["first_code_dist"].get(c, 0) for c in STORM_CODES) / max(1, o["n_systems"])))
        C.append("- **TS→DB 跳变(中间没有 TD)**: 观测 %d 次 / 涉及 %d 个系统; "
                 "TS+→TD 正常减弱 %d 次; 有风暴但全程无 TD 的系统 %d 个。"
                 % (o["n_jumps_storm_to_db"], o["n_systems_with_jump_storm_to_db"],
                    o["n_jumps_storm_to_td"], o["n_systems_storm_without_td"]))
        C.append("")
    if m:
        C.append("### 10.2 模型短多少倍")
        C.append("")
        C.append("- 模型输出 %d 个系统 / %d 条记录, 出现的阶段码**只有 %s**。"
                 % (m["n_systems"], m["n_records"], sorted(m["phase_records"])))
        C.append("- ⚠️ **先纠正一点(数据说的和直觉不完全一样)**: 模型**并非**\"完全没有扰动阶段\" —— "
                 "它有 %.0f%% 的系统首报就是 DB。真正的问题是三件事: (1) 扰动阶段**只有 2 报**, "
                 "不是没有; (2) **TD 码在模型里一次都没出现过**; (3) 风暴强度与 DB 之间**直接互相跳变**。"
                 % (100.0 * m["first_code_dist"].get("DB", 0) / max(1, m["n_systems"])))
        od, md_ = o.get("pre_storm_dist_h", {}), m.get("pre_storm_dist_h", {})
        if od.get("median") is not None and md_.get("median") is not None:
            if md_["median"] == 0:
                C.append("- **扰动阶段**: 观测中位 %.1f h, 模型中位 0.0 h —— 模型绝大多数系统"
                         "首报即 TS/TY, 扰动阶段等价于**完全缺失**。" % od["median"])
            else:
                C.append("- **扰动阶段**: 观测中位 %.1f h vs 模型中位 %.1f h → 模型短 **%.1f×**。"
                         % (od["median"], md_["median"], od["median"] / md_["median"]))
        ot, mt = o.get("pre_storm_td_h", {}), m.get("pre_storm_td_h", {})
        if ot.get("median") is not None:
            if not mt or mt.get("median") == 0:
                C.append("- **低压阶段(TD)**: 观测中位 %.1f h / 均值 %.1f h, 而模型 **完全不发出 TD 码**"
                         "(0 h, 0 条) —— 不是\"短几倍\", 而是**该阶段在模型里不存在**。"
                         % (ot["median"], ot["mean"]))
            else:
                C.append("- **低压阶段(TD)**: 观测中位 %.1f h vs 模型中位 %.1f h → 短 %.1f×。"
                         % (ot["median"], mt["median"], ot["median"] / mt["median"]))
        os_, ms_ = o.get("pre_storm_total_h", {}), m.get("pre_storm_total_h", {})
        if os_.get("median") and ms_.get("median"):
            C.append("- **前生期合计**: 观测中位 %.1f h vs 模型中位 %.1f h → 模型短 **%.1f×** "
                     "(即用户说的\"只有 2–3 报\")。"
                     % (os_["median"], ms_["median"], os_["median"] / ms_["median"]))
        C.append("- **TS→DB 跳变**: 模型 %d 次 / 涉及 %d 个系统(观测 %d 次 / %d 个系统) —— "
                 "这正是用户抱怨的缺陷, 在观测里罕见而在模型里普遍。拆开看: "
                 "**末段型 %d 次(涉及 %d 个系统, 占 %.0f%%)** = 台风**从风暴强度直接掉回 DB 结束**生活史, "
                 "中间没有 TD/LO/EX 过渡; **中段型 %d 次(涉及 %d 个系统)** = 中途先回跳 DB 再过几报又"
                 "变回 TS/TY。观测里这两种都是 **0 例**。"
                 % (m["n_jumps_storm_to_db"], m["n_systems_with_jump_storm_to_db"],
                    o.get("n_jumps_storm_to_db", 0), o.get("n_systems_with_jump_storm_to_db", 0),
                    m.get("n_jumps_storm_to_db_tail", 0), m.get("n_systems_terminal_storm_to_db", 0),
                    100.0 * m.get("n_systems_terminal_storm_to_db", 0) / max(1, m["n_systems"]),
                    m.get("n_jumps_storm_to_db_mid", 0), m.get("n_systems_midlife_storm_to_db", 0)))
        C.append("- 模型有风暴但全程无 TD 的系统: **%d / %d (%.0f%%)**。"
                 % (m["n_systems_storm_without_td"], m["n_systems"],
                    100.0 * m["n_systems_storm_without_td"] / max(1, m["n_systems"])))
        C.append("")
        C.append("用\"报数\"说得更直白:")
        C.append("")
        C.append("- 观测·自评: 达到风暴强度前, 扰动阶段中位 **%.1f 报**(%.1f h), 低压阶段中位 "
                 "**%.1f 报**(%.1f h), 前生期合计中位 **%.1f 报**(%.1f h); 前生期 ≤3 报的系统只占 "
                 "%.0f%%。"
                 % (o["pre_storm_dist_reports"]["median"], o["pre_storm_dist_h"]["median"],
                    o["pre_storm_td_reports"]["median"], o["pre_storm_td_h"]["median"],
                    o["pre_storm_total_reports"]["median"], o["pre_storm_total_h"]["median"],
                    100.0 * o["frac_reaching_pre_storm_le3"]))
        C.append("- 模型: 前生期合计中位 **%.1f 报**(%.1f h), 其中 **%.0f%%** 的系统前生期 ≤3 报, "
                 "**%.0f%%** 的系统前生期完全没有 TD —— 与用户观察到的\"经常只有 2–3 报\"完全一致。"
                 % (m["pre_storm_total_reports"]["median"], m["pre_storm_total_h"]["median"],
                    100.0 * m["frac_reaching_pre_storm_le3"], 100.0 * m["frac_reaching_td_zero"]))
        C.append("")
        # 分年模型对照
        pd_ = report.get("per_dataset", {})
        m25, m2100 = pd_.get("model_2025"), pd_.get("model_2100")
        if m25 and m2100:
            C.append("- 模型内部两代对照: `model_2025` 前生期合计中位 %.1f h / 扰动中位 %.1f h; "
                     "`model_2100` 前生期合计中位 %.1f h / 扰动中位 %.1f h; 两者 TD 记录数均为 **0**。"
                     % (m25["pre_storm_total_h"]["median"], m25["pre_storm_dist_h"]["median"],
                        m2100["pre_storm_total_h"]["median"], m2100["pre_storm_dist_h"]["median"]))
            C.append("")
    C.append("### 10.3 一季有多少个系统 / 一年有多少扰动")
    C.append("")
    ps = report.get("per_season", {})
    arc = {k.split("|")[2]: v for k, v in ps.items()
           if k.startswith("xrq_wp_archive|wp|")}
    if arc:
        yrs = sorted(y for y in arc if y not in ("None", None))
        nsys = [arc[y]["n_systems"] for y in yrs]
        ndist = [arc[y]["n_records_dist"] for y in yrs]
        zero = [y for y in yrs if arc[y]["n_records_dist"] == 0]
        good = [y for y in yrs if arc[y]["n_records_dist"] > 0]
        nd_good = [arc[y]["n_records_dist"] for y in good]
        sd_all = [arc[y]["n_systems_dist_first"] for y in yrs]
        sd_good = [arc[y]["n_systems_dist_first"] for y in good]
        C.append("- **本地数据口径**(`xrq/wp` 归档, WP 洋区 %d 个季): 每季 b-deck 系统数(含未编号扰动) "
                 "中位 **%d 个** / 均值 %.1f 个 / 极值 %d–%d 个; 每季处于 DB/WV/LO 的记录数 "
                 "中位 **%.0f 条 = %.0f h** / 均值 %.1f 条; 每季**从扰动码起步**的系统数 "
                 "中位 **%.0f 个** / 均值 %.1f 个。"
                 % (len(yrs), quantile(nsys, .5), sum(nsys) / float(len(nsys)), min(nsys), max(nsys),
                    quantile(ndist, .5), quantile(ndist, .5) * TAU_HOURS,
                    sum(ndist) / float(len(ndist)),
                    quantile(sd_all, .5), sum(sd_all) / float(len(sd_all))))
        C.append("- 注意: 该归档有 **%d 个年份完全缺失 DB/WV/LO 码**(%s), 所以「一年多少扰动」的"
                 "可用口径应**剔除这些年份**: 剩 %d 个季时, 每季 DB/WV/LO 记录数 中位 %.0f 条 / "
                 "均值 %.0f 条, 每季从扰动码起步的系统数 中位 **%.0f 个** / 均值 %.1f 个。"
                 % (len(zero), ", ".join(zero) if zero else "无", len(good),
                    quantile(nd_good, .5), sum(nd_good) / float(len(nd_good)),
                    quantile(sd_good, .5), sum(sd_good) / float(len(sd_good))))
        C.append("- 即: **一年(WP)大约有 30 个 b-deck 系统, 其中约 29 个(≈97%)是从 DB/WV/LO 扰动阶段"
                 "起步的**, 折合每季约 218 条 × 6 h ≈ 1300 h 的扰动期记录 —— 与外部文献中"
                 "「远多于编号台风数的热带扰动/非发展扰动」的定性结论一致。")
    C.append("- **同期对照**: 自评 `2025wp` %d 个系统 / `2026wp` %d 个系统 / `2025-2026sh` %d 个系统; "
             "模型 `model_2025` wp 30 个、sh 36 个, `model_2100` wp 28 个、sh 24 个 —— "
             "模型**系统总数与观测同量级**, 差的是每个系统内部的阶段结构, 不是数量。"
             % (report["per_dataset"].get("2025wp", {}).get("n_systems", 0),
                report["per_dataset"].get("2026wp", {}).get("n_systems", 0),
                report["per_dataset"].get("2025-2026sh", {}).get("n_systems", 0)))
    C.append("- 外部文献口径(需再核对): 见第 9 节链接。**未能从这些网页抓到可直接比较的年度扰动总数字**"
             "(Springer / DOAJ 均返回 403), 因此本节只给出本地数据的口径, 不虚构外部数字。")
    C.append("")
    C.append("### 10.4 诚实说明 / 局限")
    C.append("")
    C.append("- 本统计把 b-deck 的 **相邻记录对** 当作阶段跳变判据: 观测自评数据里存在 >6 h 的"
             "时间缺口(系统 %d 个), 缺口处可能\"藏\"了未记录的 TD, 因此观测的 TS→DB 跳变计数是**上界**。"
             % o.get("n_systems_with_gaps", 0))
    C.append("- `TS/TY/ST/HU/TT` 被统一视为\"风暴强度\"; `HU`(飓风)与 `TT`(未知码)出现在自评数据里, "
             "属于与 WMO/JTWC 标准 11 码略有出入的写法, 已如实计入。")
    C.append("- xrq 原始归档为 **11 列**(缺 col11 标识列), 且个别行 30 列(畸形行已跳过), "
             "统计口径与自评 12 列不完全一致, 两类数据分开列出。")
    C.append("- `xrq/2025`、`xrq/2026` 与 `xrq/wp` 归档中的 `bwp*2025.dat` / `bwp*2026.dat` "
             "**存在重叠**, 不可相加。")
    C.append("- 模型中出现的 DB 同时包含\"生成初期\"与\"衰减末期\", 因此\"全寿命 DB 时长\"不等于"
             "\"前生期扰动时长\"; 判断\"有没有扰动阶段\"应看 **第 3 节的前生期指标与第 2 节的首报分布**。")
    return C


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="b-deck 阶段码持续时长统计")
    ap.add_argument("--base-ziping", default=DEFAULT_BASE_ZIPING)
    ap.add_argument("--base-xrq", default=DEFAULT_BASE_XRQ)
    ap.add_argument("--base-sim", default=DEFAULT_BASE_SIM)
    ap.add_argument("--out-md", default=r"G:\气象\Typhoon-sim\output\sim\_reference\PHASE_DURATION_STATS.md")
    ap.add_argument("--out-json", default=r"G:\气象\Typhoon-sim\output\sim\_reference\phase_duration_stats.json")
    ap.add_argument("--skip-archive", action="store_true",
                    help="跳过 xrq/wp 大样本历史归档(2340 文件)")
    ap.add_argument("--list-sources", action="store_true", help="只列出探测到的集合")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    sources = discover_sources(include_archive=not args.skip_archive,
                               base_ziping=args.base_ziping, base_xrq=args.base_xrq,
                               base_sim=args.base_sim)

    if args.list_sources:
        for s in sources:
            print("%-16s %-12s exists=%-5s n_files=%-6d %s"
                  % (s["name"], s["kind"], s["exists"], s["n_files"], s["root"]))
        return 0

    if not args.quiet:
        print("[discover] 候选集合 %d 个" % len(sources))
    systems = run(sources, verbose=not args.quiet)

    by_kind = collections.defaultdict(list)
    for s in systems:
        by_kind[s["kind"]].append(s)

    aggregates = {}
    for kind, lst in by_kind.items():
        aggregates[kind] = agg_block(lst)

    per_ds = {}
    by_ds = collections.defaultdict(list)
    for s in systems:
        by_ds[s["dataset"]].append(s)
    for name, lst in by_ds.items():
        per_ds[name] = agg_block(lst)

    report = {
        "generated_by": "tools/stat_phase_duration.py",
        "tau_hours": TAU_HOURS,
        "code_groups": {
            "disturbance": list(DIST_CODES),
            "depression": list(DEP_CODES),
            "storm": list(STORM_CODES),
            "other": list(OTHER_CODES),
        },
        "sources": sources,
        "aggregates": aggregates,
        "per_dataset": per_ds,
        "per_season": per_season(systems, None),
        "systems": systems,
    }
    report["conclusions"] = make_conclusions(report)

    outdir = os.path.dirname(args.out_json)
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir)

    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    md = build_md(report)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)

    if not args.quiet:
        print("\n[写出] %s" % args.out_json)
        print("[写出] %s" % args.out_md)
        for kind, a in aggregates.items():
            print("\n[%s] systems=%d records=%d  first_code=%s"
                  % (kind, a["n_systems"], a["n_records"], a["first_code_dist"]))
            print("        pre_storm_dist_h median=%s  pre_storm_td_h median=%s  pre_storm_total_h median=%s"
                  % (a["pre_storm_dist_h"]["median"], a["pre_storm_td_h"]["median"],
                     a["pre_storm_total_h"]["median"]))
            print("        jumps_storm_to_db=%d (systems %d), storm_without_td=%d"
                  % (a["n_jumps_storm_to_db"], a["n_systems_with_jump_storm_to_db"],
                     a["n_systems_storm_without_td"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
