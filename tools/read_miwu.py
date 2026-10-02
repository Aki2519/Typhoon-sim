# -*- coding: utf-8 -*-
"""miwu b-deck 读取器(G:\\气象\\数据\\miwu, 只读)。

miwu 是"一年一目录 / 一洋区一子目录 / 一风暴一文件"的自制 b-deck 集,
字段形状与自评同构(12 列),但两处不同:

  * 第 5 列的作者标记是 ``MIWU``(自评是 ``chunshu``, xrq 是 ``XRQA``);
  * 第 12 列(自评里的"编号B":invest 编号或名字)**恒为空** —— 名字只出现在
    每个文件**第一行**的扩展段末列(第 28 列),普通行没有名字、也没有低压号。

文件名 ``b<洋区><号><年>.dat``(例 ``bwp012024.dat``)是唯一的身份来源;
号 01-49 = 低压号,60-99 = 未编号(invest)。

一行 12 列(正常行,逗号+空格分隔,行尾 LF)::

    WP, 01, 2024052318,   , MIWU, 0,  85N, 1294E,  20, 1008, DB,
    0    1     2        3    4    5    6     7      8     9   10  11

第 3 列是 ``YYYYMMDDHH``,第 4 列是**分钟** —— 绝大多数行为空(视为 0 分),
全库只有 24 行写了分钟(如 ``2013110723, 13`` = 23:13Z),而其中常常就是那条峰值记录。
本读取器把时间规范化成 ``YYYYMMDDHHMM``(空分钟补 ``00``),``r.dtg`` 给回 10 位的
``YYYYMMDDHH``。

文件第一行多出 ATCF 的"风圈扩展段"(第 12-27 列,实测全为 0 / 空,
只有 RAD 与 WINDCODE 会取 34 / NEQ),第 28 列是风暴名::

    WP, 01, 2024052312,   , MIWU, 0,  75N, 1305E,  20, 1008, DB, 0, , 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, , 0, 0, Ewiniar,

纬度/经度是**十分之一度 + 半球字母**(``85N`` = 8.5°N, ``1294E`` = 129.4°E),
本读取器统一换成十进制度(南纬/西经为负)。

用法::

    python tools/read_miwu.py --list                    # 列清单(逐年/洋区/风暴数)
    python tools/read_miwu.py --year 2024               # 逐年清单
    python tools/read_miwu.py --storm WP24 2025         # 打印一个风暴的逐条记录
    python tools/read_miwu.py --self-test               # 最小自检
    python tools/read_miwu.py --verify-all              # 全库体检

代码内::

    from tools.read_miwu import iter_storms, read_storm, load_all
    for st in iter_storms(years=[2025], basins=["WP"]):
        print(st.key, st.name, len(st.records))
        for r in st.records:                 # (time, lat, lon, wind, pressure, stage, name)
            print(r.time, r.lat, r.lon, r.wind, r.pressure, r.stage, r.name)

只读,不写任何东西。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, namedtuple
from datetime import datetime

# ---------------------------------------------------------------- 常量

DEFAULT_DIR = r"G:\气象\数据\miwu"

#: 目录名 → 文件里的洋区码(2018 的 bep102018.dat 落在 WPAC 目录里, 属已知错位)
DIR_TO_BASIN = {
    "WPAC": "WP",
    "EPAC": "EP",
    "CPAC": "CP",
    "ATL": "AL",
    "IO": "IO",
    "SHEM": "SH",
    "SATL": "SL",
    "ME": "ME",
}

#: ATCF 阶段码(性质)。DB/WV/LO/TD/TS/TY/ST 是热带性质,
#: EX/SS/SD/MD/PT 是变性/副热带/其他, HU 是 NHC 口径的飓风。
STAGE_CODES = (
    "DB", "WV", "LO", "TD", "TS", "TY", "ST", "HU",
    "EX", "SS", "SD", "MD", "PT",
)

FILE_RE = re.compile(r"^b([a-z]{2})([0-9A-Za-z]{2})(\d{4})\.dat$")
DTG_RE = re.compile(r"^\d{10}$")

#: 记录 = (time, lat, lon, wind, pressure, stage, name)
#: time 是 'YYYYMMDDHHMM'(按字典序排序即按时间排序): 第 4 列写了分钟就用真实分钟,
#: 没写(绝大多数行)补 '00'。带分钟的记录全库只有 24 条, 且往往是峰值那一条。
#: lat/lon 是十进制度(南纬/西经为负); wind 是 kt; pressure 是 hPa;
#: stage 是阶段码; name 只在文件首行/跨洋区行有, 其余为 None。
class Record(namedtuple("Record", "time lat lon wind pressure stage name")):
    """一条记录。``time`` 是 'YYYYMMDDHHMM', ``dtg`` 是 'YYYYMMDDHH', ``dt`` 给 datetime。"""

    __slots__ = ()

    @property
    def dtg(self):
        """ATCF 的 10 位时次标识 'YYYYMMDDHH'。"""
        return self.time[:10]

    @property
    def minute(self):
        return int(self.time[10:12])

    @property
    def dt(self):
        """datetime(UTC, naive)。"""
        return datetime.strptime(self.time, "%Y%m%d%H%M")

    @property
    def latlon(self):
        return (self.lat, self.lon)


class Storm(namedtuple("Storm", "key name path year_dir basin_dir records issues extra_names")):
    """一个风暴文件解析结果。

    key          : (洋区码, 号, 年) 例 ('WP', '01', '2024')
    name         : 首行扩展段里的名字(str)或 None
    path         : 文件绝对路径
    year_dir     : 年目录名(例 '2024')
    basin_dir    : 洋区目录名(例 'WPAC')
    records      : Record 列表(按文件原顺序)
    issues       : 数据质量提示(str 列表)
    extra_names  : 除首行外中途出现的 (time, name)(跨洋区风暴会有)
    """

    __slots__ = ()

    @property
    def basin(self):
        return self.key[0]

    @property
    def number(self):
        return self.key[1]

    @property
    def year(self):
        return self.key[2]

    @property
    def times(self):
        return [r.time for r in self.records]

    @property
    def named(self):
        return self.name is not None

    @property
    def is_invest(self):
        """号 60-99 视为未编号扰动。"""
        n = self.number
        return n.isdigit() and 50 <= int(n) < 90


# ---------------------------------------------------------------- 字段解析


def _split(line):
    """按逗号切分并去掉两头空白(保留行尾逗号造成的空列)。"""
    return [x.strip() for x in line.split(",")]


def parse_latlon(tok):
    """'1294E' → 129.4;'85S' → -8.5。无法解析返回 None。"""
    if not tok:
        return None
    hemi = tok[-1].upper()
    if hemi not in "NSEW":
        return None
    body = tok[:-1].strip()
    if not body.isdigit():
        return None
    v = int(body) / 10.0
    if hemi in ("S", "W"):
        v = -v
    return v


def parse_record(line, name=None):
    """解析一行数据行,失败(页脚/汇总行等)返回 None。

    兼容三种行:12 列(正常)、11 列(行尾少了逗号)、29 列(首行/跨洋区行,
    前 11 列同义,第 28 列是名字)。

    第 3 列是 YYYYMMDDHH,第 4 列是**分钟**(绝大多数行为空 —— 空则按 0 分处理)。
    """
    tok = _split(line)
    if len(tok) < 11:
        return None
    if not DTG_RE.match(tok[2]):
        return None
    lat = parse_latlon(tok[6])
    lon = parse_latlon(tok[7])
    if lat is None or lon is None:
        return None
    try:
        wind = int(tok[8])
        pressure = int(tok[9])
    except ValueError:
        return None
    # 第 4 列: 分钟。只在少数行有值(全库 24 条), 空则补 00
    minute = tok[3] if tok[3].isdigit() else ""
    time = tok[2] + (minute.zfill(2) if minute else "00")
    stage = tok[10] or None
    # 29 列行的名字在第 28 列; 12 列行的第 12 列(自评的"编号B")在 miwu 里恒空
    if name is None and len(tok) >= 28 and tok[27]:
        name = tok[27]
    return Record(time, lat, lon, wind, pressure, stage, name)


def read_storm(path, year_dir=None, basin_dir=None):
    """读一个 .dat,返回 Storm。路径不存在抛 FileNotFoundError。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8-sig", errors="replace")

    base = os.path.basename(path)
    m = FILE_RE.match(base)
    if m:
        key = (m.group(1).upper(), m.group(2).upper(), m.group(3))
    else:
        key = (None, None, None)

    year_dir = year_dir or os.path.basename(os.path.dirname(os.path.dirname(path)))
    basin_dir = basin_dir or os.path.basename(os.path.dirname(path))

    records = []
    extra_names = []
    issues = []
    name = None          # 首行的名字
    current = None       # 当前生效的名字(跨洋区行会换一个)
    seen = Counter()
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = parse_record(line)
        if rec is None:
            skipped += 1
            continue
        if rec.name:                      # 只有首行/跨洋区行带名字
            if name is None:
                name = rec.name
            elif rec.name != current:
                extra_names.append((rec.time, rec.name))
            current = rec.name
        elif current is not None:         # 普通行继承当前名字, 便于按风暴聚合
            rec = rec._replace(name=current)
        records.append(rec)
        seen[rec.time] += 1

    if skipped:
        issues.append("跳过 %d 行非数据行(页脚/汇总等)" % skipped)
    dups = sorted(t for t, c in seen.items() if c > 1)
    if dups:
        issues.append("同一文件内重复时次: %s" % ", ".join(dups))
    ts = [r.time for r in records]
    if any(b < a for a, b in zip(ts, ts[1:])):
        bad = [(a, b) for a, b in zip(ts, ts[1:]) if b < a]
        issues.append("时次非单调(年月疑似写错): %s" % ", ".join("%s→%s" % p for p in bad))
    if key[0] and basin_dir in DIR_TO_BASIN and DIR_TO_BASIN[basin_dir] != key[0]:
        issues.append("文件名洋区 %s 与目录 %s 不一致" % (key[0], basin_dir))
    if name is None:
        issues.append("首行无名字(扩展段缺失)")
    return Storm(key, name, path, year_dir, basin_dir, records, issues, extra_names)


# ---------------------------------------------------------------- 遍历


def iter_storm_files(root=DEFAULT_DIR, years=None, basins=None):
    """按 (年, 洋区目录, 文件) 顺序产出 .dat 路径(只认 4 位年目录)。"""
    years = set(str(y) for y in years) if years else None
    basins = set(b.upper() for b in basins) if basins else None
    for year in sorted(os.listdir(root)):
        ypath = os.path.join(root, year)
        if not re.match(r"^\d{4}$", year) or not os.path.isdir(ypath):
            continue
        if years and year not in years:
            continue
        for basin in sorted(os.listdir(ypath)):
            bpath = os.path.join(ypath, basin)
            if not os.path.isdir(bpath):
                continue
            if basins and basin.upper() not in basins and DIR_TO_BASIN.get(basin.upper(), basin.upper()) not in basins:
                continue
            for fname in sorted(os.listdir(bpath)):
                if fname.endswith(".dat"):
                    yield os.path.join(bpath, fname)


def iter_storms(root=DEFAULT_DIR, years=None, basins=None):
    """逐风暴产出 Storm。"""
    for path in iter_storm_files(root, years, basins):
        yield read_storm(path)


def load_all(root=DEFAULT_DIR, years=None, basins=None):
    """返回 {key: Storm}; 同一 key 撞车时后者覆盖并在 issues 里留痕。"""
    out = {}
    for st in iter_storms(root, years, basins):
        if st.key in out:
            st.issues.append("与 %s 同 key" % out[st.key].path)
        out[st.key] = st
    return out


def iter_records(root=DEFAULT_DIR, years=None, basins=None):
    """产出 (key, Record) 扁平流,便于统计。"""
    for st in iter_storms(root, years, basins):
        for rec in st.records:
            yield st.key, rec


def storm_series(path):
    """便捷函数: 一个文件 → (name, [(time, lat, lon, wind, pressure, stage, name), ...])。"""
    st = read_storm(path)
    return st.name, [tuple(r) for r in st.records]


# ---------------------------------------------------------------- CLI


def _p(out):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(out)


def cmd_list(root, years=None):
    total_files = total_recs = 0
    by_year = {}
    for st in iter_storms(root, years):
        total_files += 1
        total_recs += len(st.records)
        y = by_year.setdefault(st.year_dir, {"files": 0, "recs": 0, "basins": Counter()})
        y["files"] += 1
        y["recs"] += len(st.records)
        y["basins"][st.basin_dir] += 1
    _p("目录: %s" % root)
    _p("%-6s %6s %8s  %s" % ("年", "风暴", "记录", "洋区(风暴数)"))
    for y in sorted(by_year):
        d = by_year[y]
        bs = ", ".join("%s:%d" % kv for kv in sorted(d["basins"].items()))
        _p("%-6s %6d %8d  %s" % (y, d["files"], d["recs"], bs))
    _p("合计: %d 个年份, %d 个风暴文件, %d 条记录" % (len(by_year), total_files, total_recs))


def cmd_storm(root, basin, num, year):
    path = os.path.join(root, year, _basin_dir(root, year, basin), "b%s%s%s.dat" % (basin.lower(), num, year))
    if not os.path.exists(path):
        _p("找不到 %s" % path)
        return 1
    st = read_storm(path)
    _p("# %s  name=%s  记录 %d 条  %s" % (os.path.basename(path), st.name, len(st.records), path))
    _p("%-13s %7s %8s %5s %6s %-3s %s" % ("time", "lat", "lon", "wind", "pres", "st", "name"))
    for r in st.records:
        _p("%-13s %7.1f %8.1f %5d %6d %-3s %s" % (r.time, r.lat, r.lon, r.wind, r.pressure, r.stage or "-", r.name or "-"))
    for i in st.issues:
        _p("! " + i)
    return 0


def _basin_dir(root, year, basin):
    ypath = os.path.join(root, year)
    if not os.path.isdir(ypath):
        return basin.upper() + "AC"
    for d in sorted(os.listdir(ypath)):
        if os.path.isdir(os.path.join(ypath, d)) and DIR_TO_BASIN.get(d.upper()) == basin.upper():
            return d
    return basin.upper() + "AC"


def cmd_self_test(root):
    """最小自检: 读一个文件, 打印前几行解析结果 + 断言几条不变量。"""
    rel = os.path.join("2024", "WPAC", "bwp012024.dat")
    path = os.path.join(root, rel)
    _p("== self-test: %s" % path)
    if not os.path.exists(path):
        _p("!! 文件不存在, 换 2025/WPAC/bwp012024 …")
        return 1
    with open(path, "rb") as fh:
        head = fh.read().decode("utf-8-sig", errors="replace").splitlines()[:4]
    _p("-- 前三行原文 --")
    for l in head[:3]:
        _p("   " + l)
    _p("-- 解析结果(前 3 条) --")
    st = read_storm(path)
    for r in st.records[:3]:
        _p("   %s" % (tuple(r),))
    _p("-- 带分钟的记录(全库 24 条, 常是峰值那条) --")
    for r in read_storm(os.path.join(root, "2006", "WPAC", "bwp222006.dat")).records[16:21]:
        _p("   %s" % (tuple(r),))
    _p("-- 断言 --")
    assert st.name == "Ewiniar", st.name
    assert st.key == ("WP", "01", "2024"), st.key
    assert len(st.records) >= 10, len(st.records)
    assert abs(st.records[0].lat - 7.5) < 1e-9, st.records[0]
    assert abs(st.records[0].lon - 130.5) < 1e-9, st.records[0]
    assert st.records[0].wind == 20 and st.records[0].pressure == 1008
    assert st.records[0].stage == "DB"
    assert st.records[0].time == "202405231200", st.records[0].time
    assert st.records[0].dtg == "2024052312"
    assert st.records[0].dt.isoformat() == "2024-05-23T12:00:00", st.records[0].dt
    assert st.records[0].latlon == (7.5, 130.5)
    # 带分钟的行(全库 24 条): 2023/EPAC/bep152023.dat 的 2023101100 有 00:00 与 00:10 两条
    st_min = read_storm(os.path.join(root, "2023", "EPAC", "bep152023.dat"))
    mins = [r for r in st_min.records if r.minute]
    assert mins, "bep152023.dat 应有带分钟的记录"
    assert mins[0].time == "202310102130", mins[0].time
    assert [r.time for r in st_min.records if r.dtg == "2023101100"] == [
        "202310110000", "202310110010"], [r.time for r in st_min.records]
    assert all(r.stage in STAGE_CODES for r in st.records), [r.stage for r in st.records]
    assert all(0 <= r.wind <= 300 for r in st.records)
    _p("   OK: name/key/经纬度(十分之一度→度)/风压/阶段码/时次 全部通过")
    _p("   issues: %s" % (st.issues or "无"))
    return 0


def cmd_verify_all(root):
    """全库体检: 记录数、阶段码、风压范围、异常文件。"""
    n_files = n_recs = 0
    stages = Counter()
    basins = Counter()
    winds = []
    press = []
    issue_files = []
    for st in iter_storms(root):
        n_files += 1
        n_recs += len(st.records)
        basins[st.key[0]] += 1
        for r in st.records:
            stages[r.stage] += 1
            winds.append(r.wind)
            press.append(r.pressure)
        if st.issues:
            issue_files.append((st.year_dir, st.basin_dir, os.path.basename(st.path), st.issues))
    winds.sort()
    press.sort()

    def pct(v, p):
        return v[min(len(v) - 1, int(len(v) * p / 100))]

    _p("风暴文件 %d, 记录 %d" % (n_files, n_recs))
    _p("洋区: %s" % dict(basins.most_common()))
    _p("阶段码: %s" % dict(stages.most_common()))
    _p("风速 kt  min/p50/p99/max = %s" % [pct(winds, p) for p in (0, 50, 99, 100)])
    _p("气压 hPa min/p50/p99/max = %s" % [pct(press, p) for p in (0, 50, 99, 100)])
    _p("风速>300kt 的记录: %d" % sum(1 for w in winds if w > 300))
    _p("有提示的文件 %d 个:" % len(issue_files))
    for y, b, f, iss in issue_files:
        _p("   %s/%s/%s : %s" % (y, b, f, " | ".join(iss)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="miwu b-deck 读取器(只读)")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="miwu 根目录(默认 %s)" % DEFAULT_DIR)
    ap.add_argument("--year", action="append", help="只处理这些年份(可重复)")
    ap.add_argument("--basin", action="append", help="只处理这些洋区码(WP/EP/CP/AL/IO/SH/SL/ME)")
    ap.add_argument("--list", action="store_true", help="列逐年清单")
    ap.add_argument("--storm", nargs=2, metavar=("BASINNUM", "YEAR"), help="打印一个风暴,--storm WP24 2025")
    ap.add_argument("--self-test", action="store_true", help="最小自检")
    ap.add_argument("--verify-all", action="store_true", help="全库体检")
    args = ap.parse_args(argv)

    if args.self_test:
        return cmd_self_test(args.dir)
    if args.verify_all:
        return cmd_verify_all(args.dir)
    if args.storm:
        sid, year = args.storm
        m = re.match(r"^([A-Za-z]{2})([0-9A-Za-z]{2})$", sid)
        if not m:
            _p("--storm 形如 WP24 / EP04 / AL02")
            return 2
        return cmd_storm(args.dir, m.group(1).upper(), m.group(2).upper(), year)
    if args.year or args.basin:
        n = 0
        for st in iter_storms(args.dir, args.year, args.basin):
            n += 1
            _p("%-4s %-5s %-6s %-4s name=%-14s 记录 %3d  %s ~ %s"
               % (st.year_dir, st.basin_dir, st.number, st.basin, st.name or "-",
                  len(st.records), st.records[0].time if st.records else "-",
                  st.records[-1].time if st.records else "-"))
            for i in st.issues:
                _p("      ! " + i)
        _p("共 %d 个风暴文件" % n)
        return 0
    if args.list:
        cmd_list(args.dir, args.year)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
