#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_tc_names_history.py -- sanity check / printer for tc_names_history.json

静态数据校验脚本(只读,不修改任何文件)。

用法:
    python tools/check_tc_names_history.py                 # 默认读 simulator/env/model/tc_names_history.json
    python tools/check_tc_names_history.py <path.json>

打印内容:
  1. 1997 年 WP 名字序列(前 15 个)+ 全年序列 + 序列长度
  2. 1999 -> 2000 命名体系切换前后对照
  3. 各洋区历史版本的起始年份
  4. 一致性断言(通过返回 0,失败返回 1)
"""

from __future__ import annotations

import json
import os
import sys

try:  # Windows 控制台默认不是 utf-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_PATH = os.path.join(ROOT, "simulator", "env", "model", "tc_names_history.json")

SEP = "-" * 78


def head(title: str) -> None:
    print()
    print(SEP)
    print(title)
    print(SEP)


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_year_names(by_year: dict, year: str):
    """Return a list of names for a year, tolerating either list or dict entries."""
    v = by_year.get(year)
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, dict):
        for key in ("names", "sequence", "list"):
            if isinstance(v.get(key), list):
                return [str(x) for x in v[key]]
    return None


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    failures: list[str] = []
    checks = 0

    if not os.path.exists(path):
        print("ERROR: file not found: %s" % path)
        return 1

    print("tc_names_history.json  ->  %s" % path)
    data = load(path)
    print("top-level keys: %s" % ", ".join(sorted(data.keys())))

    # ---------------------------------------------------------------- WP 1997
    head("[1] WP 1997 -- 2000 年之前使用的名字序列 (JTWC 轮换名单)")
    wp = data.get("WP", {})
    pre = wp.get("pre2000", {})
    by_year = pre.get("by_year", {}) if isinstance(pre, dict) else {}

    seq1997 = get_year_names(by_year, "1997")
    if not seq1997:
        failures.append("WP.pre2000.by_year['1997'] missing or empty")
        print("  !! 1997 序列缺失")
    else:
        checks += 1
        print("  序列长度: %d" % len(seq1997))
        print("  前 15 个: %s" % ", ".join(seq1997[:15]))
        print("  全年    : %s" % ", ".join(seq1997))
        jtwc_only = pre.get("by_year_jtwc_only", {}).get("1997")
        if jtwc_only:
            print("  仅 JTWC 命名(JMA 未编号): %s" % ", ".join(jtwc_only))
        cross = pre.get("crossovers", {}).get("1997")
        if cross:
            print("  他区跨界名(不计入 WP 名单): %s" % ", ".join(cross))

    # ------------------------------------------------------- 1999 -> 2000 切换
    head("[2] 1999 -> 2000 命名体系切换对照")
    tr = pre.get("transition_1999_2000", {})
    seq1999 = get_year_names(by_year, "1999") or []
    seq2000 = get_year_names(by_year, "2000") or []
    print("  旧体系 (JTWC 名单) 1999 全年: %s" % (", ".join(seq1999) if seq1999 else "<缺失>"))
    print("  新体系 (140 名表)  2000 全年: %s" % (", ".join(seq2000) if seq2000 else "<缺失>"))
    if isinstance(tr, dict):
        last_old = tr.get("last_old_name")
        first_new = tr.get("first_new_name")
        print("  最后一个旧名 : %s (%s, %s)" % (last_old.get("name"), last_old.get("year"), last_old.get("date")) if last_old else "  最后一个旧名 : <缺失>")
        print("  第一个新名   : %s (%s, %s)" % (first_new.get("name"), first_new.get("year"), first_new.get("date")) if first_new else "  第一个新名   : <缺失>")
        gap = tr.get("gap_note") or tr.get("note")
        if gap:
            print("  说明         : %s" % gap)
        if last_old and seq1999:
            checks += 1
            if seq1999[-1] != last_old.get("name"):
                failures.append(
                    "1999 序列末位 %r != transition.last_old_name %r" % (seq1999[-1], last_old.get("name"))
                )
            else:
                print("  [OK] 1999 序列末位与 last_old_name 一致")
        if first_new and seq2000:
            checks += 1
            if seq2000[0] != first_new.get("name"):
                failures.append(
                    "2000 序列首位 %r != transition.first_new_name %r" % (seq2000[0], first_new.get("name"))
                )
            else:
                print("  [OK] 2000 序列首位与 first_new_name 一致")
    else:
        print("  说明: %s" % tr)

    # ------------------------------------------------------ 各洋区起始年份
    head("[3] 各洋区历史版本起始年份")
    lines: list[str] = []

    # WP pre-2000 list
    lists = pre.get("lists", {}) if isinstance(pre, dict) else {}
    if lists:
        sets = lists.get("sets", [])
        lines.append(
            "WP  pre-2000 JTWC list : effective_from=%s, sets=%d x %s names = %s"
            % (
                lists.get("effective_from", "?"),
                len(sets),
                len(sets[0]) if sets else "?",
                lists.get("total_names", "?"),
            )
        )
    vt = wp.get("versions_from")
    if vt:
        lines.append("WP  current 140-name list : from=%s" % vt)
    lines.append("WP  by_year coverage      : %s" % _year_span(by_year))

    for basin in ("AL", "EP", "CP"):
        sec = data.get(basin, {})
        hist = sec.get("history", []) if isinstance(sec, dict) else []
        starts = [h.get("from") for h in hist if isinstance(h, dict) and h.get("from") is not None]
        lines.append(
            "%s  history milestones     : %s"
            % (basin, ", ".join(str(s) for s in sorted(starts)) if starts else "<none>")
        )
        reps = sec.get("retired_replacements") if isinstance(sec, dict) else None
        if reps is not None:
            lines.append("%s  retired_replacements   : %d entries" % (basin, len(reps)))

    io = data.get("IO", {})
    if isinstance(io, dict):
        lines.append(
            "IO  pre-2004 naming        : %s" % ("NO name list (number only)" if io.get("pre2004_no_names") else io.get("pre2004", "?"))
        )
        for h in io.get("history", []) or []:
            if isinstance(h, dict) and h.get("from"):
                lines.append("IO    from %s : %s" % (h.get("from"), h.get("note", "")))

    sh = data.get("SH", {})
    sub = sh.get("sub", sh) if isinstance(sh, dict) else {}
    for key in ("AU", "FJ", "ID", "MU"):
        block = sub.get(key, {}) if isinstance(sub, dict) else {}
        hist = block.get("history", []) if isinstance(block, dict) else []
        starts = [h.get("from") for h in hist if isinstance(h, dict) and h.get("from") is not None]
        lines.append(
            "SH/%s history milestones   : %s"
            % (key, ", ".join(str(s) for s in sorted(starts)) if starts else "<none>")
        )
        if isinstance(block, dict) and block.get("first_named"):
            lines.append("SH/%s first_named          : %s" % (key, block.get("first_named")))

    for line in lines:
        print("  " + line)

    # ------------------------------------------------------------- 断言
    head("[4] 一致性断言")
    al_rows = data.get("AL", {}).get("retired_replacements") or []
    ep_rows = data.get("EP", {}).get("retired_replacements") or []
    cp_rows = data.get("CP", {}).get("retired_replacements") or []
    checks += 1
    if len(al_rows) >= 100 and len(ep_rows) >= 20:
        print("  [OK] AL/EP 退役->替换表条数: AL=%d, EP=%d, CP=%d" % (len(al_rows), len(ep_rows), len(cp_rows)))
    else:
        failures.append("AL/EP 退役->替换表条数异常: AL=%d, EP=%d" % (len(al_rows), len(ep_rows)))
    checks += 1
    dangling = [
        r for r in list(al_rows) + list(ep_rows)
        if r.get("new") and r.get("new") != "NOT VERIFIED" and not r.get("effective_year") and r.get("basin") != "CP"
    ]
    if dangling:
        failures.append("%d 条记录了替换名但缺 effective_year: %s" % (len(dangling), dangling[:3]))
    else:
        print("  [OK] 所有有替换名的 AL/EP 条目都带 effective_year(CP 名除外)")
    checks += 1
    if len(seq1997 or []) >= 15:
        print("  [OK] 1997 序列 >= 15 个名字")
    else:
        failures.append("1997 序列少于 15 个名字")
    checks += 1
    if seq1997 and seq1997[0] == "Hannah":
        print("  [OK] 1997 首个名字 = Hannah (JTWC 分配, JMA 未编号为台风)")
    else:
        failures.append("1997 首个名字应为 Hannah, 实际 %r" % (seq1997[0] if seq1997 else None))
    checks += 1
    if seq1999 and seq2000 and seq1999[-1] == "Gloria" and seq2000[0] == "Damrey":
        print("  [OK] 切换点: 旧体系末名 Gloria(1999-11) -> 新体系首名 Damrey(2000-05)")
    else:
        failures.append(
            "切换点异常: 1999末=%r, 2000首=%r" % (seq1999[-1] if seq1999 else None, seq2000[0] if seq2000 else None)
        )
    checks += 1
    missing = [str(y) for y in range(1990, 2000) if not get_year_names(by_year, str(y))]
    if missing:
        failures.append("by_year 缺少年份: %s" % ", ".join(missing))
    else:
        print("  [OK] by_year 覆盖 1990-1999 全部 10 年")

    print()
    print("checks run: %d, failures: %d" % (checks, len(failures)))
    if failures:
        for f in failures:
            print("  FAIL: %s" % f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


def _year_span(by_year: dict) -> str:
    years = sorted(int(y) for y in by_year.keys() if str(y).isdigit())
    if not years:
        return "<none>"
    return "%d-%d (%d years)" % (years[0], years[-1], len(years))


if __name__ == "__main__":
    raise SystemExit(main())
