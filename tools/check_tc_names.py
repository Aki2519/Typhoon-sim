#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_tc_names.py —— 校验 simulator/env/model/tc_names.json（全球各洋区热带气旋官方命名表）

校验内容：
  1. WP：每个版本 5 套、每套 28 个；整套 140 个全局无重复；套内无重复
  2. AL：每个版本 6 套、每套 21 个；套内无重复
  3. EP：每个版本 6 套、每套 24 个（NHC 东北太平洋为 A-X 共 24 名）
  4. CP / IO / SH：结构非空、套内无重复
  5. 打印人工核对样本：
       - 2026 年 WP 第 1 套前 5 个名字（按 versions 里 from<=2026 的最新版本）
       - 2026 年 AL 第 1 套前 5 个名字
       - 依据 anchor 推算的 2026 年 WP/AL 实际首发名字前 5 个

用法：  python3 tools/check_tc_names.py [json 路径]
返回码： 0 = 全部通过；1 = 有校验失败
"""

import json
import os
import sys

DEFAULT_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "simulator", "env", "model", "tc_names.json",
)

EXPECT = {
    "WP": {"n_lists": 5, "per_list": 28},
    "AL": {"n_lists": 6, "per_list": 21},
    "EP": {"n_lists": 6, "per_list": 24},
}

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def pick_version(basin, year):
    """取 max(from <= year) 的版本；找不到则取 from 最小的那个。"""
    versions = basin.get("versions") or []
    if not versions:
        return None
    cands = [v for v in versions if v.get("from", -10 ** 9) <= year]
    if not cands:
        return versions[0]
    return max(cands, key=lambda v: v.get("from", -10 ** 9))


def check_list_of_lists(tag, lists, n_lists=None, per_list=None):
    """检查 [[名字...], ...]。返回展平后的名字列表。"""
    if not isinstance(lists, list) or not lists:
        err("%s: lists 为空或不是列表" % tag)
        return []
    if n_lists is not None and len(lists) != n_lists:
        err("%s: 套数 = %d，应为 %d" % (tag, len(lists), n_lists))
    flat = []
    for i, col in enumerate(lists):
        if not isinstance(col, list):
            err("%s 第 %d 套不是列表" % (tag, i))
            continue
        if per_list is not None and len(col) != per_list:
            err("%s 第 %d 套名字数 = %d，应为 %d" % (tag, i, len(col), per_list))
        for j, nm in enumerate(col):
            if not isinstance(nm, str) or not nm.strip():
                err("%s 第 %d 套第 %d 个不是有效字符串: %r" % (tag, i, j, nm))
                continue
            if nm[0] != nm[0].upper():
                warn("%s 第 %d 套 %r 未首字母大写" % (tag, i, nm))
        seen = set()
        for nm in col:
            if isinstance(nm, str):
                if nm in seen:
                    err("%s 第 %d 套内重复: %r" % (tag, i, nm))
                seen.add(nm)
        flat.extend([n for n in col if isinstance(n, str)])
    return flat


def check_basin(code, spec, data):
    if code not in data:
        err("缺少洋区 %s" % code)
        return
    basin = data[code]
    versions = basin.get("versions") or []
    if not versions:
        err("%s: 没有 versions" % code)
        return
    for v in versions:
        frm = v.get("from")
        tag = "%s(from %s)" % (code, frm)
        flat = check_list_of_lists(tag, v.get("lists"), spec["n_lists"], spec["per_list"])
        if code == "WP":
            dup = sorted({n for n in flat if flat.count(n) > 1})
            if dup:
                err("%s: 全局重复名字 %s" % (tag, dup))
            if len(flat) != spec["n_lists"] * spec["per_list"]:
                err("%s: 总名字数 = %d，应为 %d"
                    % (tag, len(flat), spec["n_lists"] * spec["per_list"]))
        else:
            dup = sorted({n for n in flat if flat.count(n) > 1})
            if dup:
                warn("%s: 跨套重复（不同年份轮换，允许）: %s" % (tag, dup))


def check_simple(tag, lists):
    if not lists:
        err("%s: lists 为空" % tag)
        return
    check_list_of_lists(tag, lists)


def check_sh(data):
    if "SH" not in data:
        err("缺少洋区 SH")
        return
    sh = data["SH"]
    sub = sh.get("sub")
    if not isinstance(sub, dict) or not sub:
        err("SH: 缺少 sub")
        return
    for code, b in sub.items():
        if b.get("kind") == "sub_lists" and isinstance(b.get("sub"), dict):
            for k, v in b["sub"].items():
                names = v.get("names") or []
                check_list_of_lists("SH/%s/%s" % (code, k), [names])
        else:
            for v in (b.get("versions") or []):
                check_list_of_lists("SH/%s(from %s)" % (code, v.get("from")),
                                    v.get("lists"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JSON
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print("找不到文件: %s" % path)
        return 1
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print("读取: %s" % path)

    for code, spec in EXPECT.items():
        check_basin(code, spec, data)

    for code in ("CP", "IO"):
        if code not in data:
            err("缺少洋区 %s" % code)
            continue
        b = data[code]
        for v in (b.get("versions") or []):
            check_list_of_lists("%s(from %s)" % (code, v.get("from")), v.get("lists"))
    check_sh(data)

    # ---- 人工核对样本 ----
    def first_n(flat_lists, n=5):
        out = []
        for col in flat_lists:
            for nm in col:
                out.append(nm)
                if len(out) >= n:
                    return out
        return out

    print("\n=== 人工核对样本 ===")

    wp = data.get("WP")
    if wp:
        v = pick_version(wp, 2026)
        lists = v["lists"]
        print("[1] 2026 年 WP 第 1 套前 5 个名字 (版本 from=%s): %s"
              % (v.get("from"), ", ".join(lists[0][:5])))
        print("    2026 年 WP 各套首名: %s"
              % " | ".join("%s: %s" % ("I II III IV V".split()[i], c[0])
                           for i, c in enumerate(lists)))
        anc = wp.get("anchor") or {}
        if anc:
            li = anc.get("list_index", 0)
            ni = anc.get("name_index", 0)
            if 0 <= li < len(lists):
                tail = lists[li][ni:] + [n for c in lists[li + 1:] for n in c] \
                    + [n for c in lists[:li] for n in c]
                print("    按 anchor(%s, list_index=%s, name_index=%s) 推算 2026 首发前 5 个: %s"
                      % (anc.get("year"), li, ni, ", ".join(first_n([tail]))))

    al = data.get("AL")
    if al:
        v = pick_version(al, 2026)
        lists = v["lists"]
        print("[2] 2026 年 AL 第 1 套前 5 个名字 (版本 from=%s): %s"
              % (v.get("from"), ", ".join(lists[0][:5])))
        anc = al.get("anchor") or {}
        if anc:
            li = anc.get("list_index", 0)
            ni = anc.get("name_index", 0)
            if 0 <= li < len(lists):
                tail = lists[li][ni:] + [n for c in lists[li + 1:] for n in c] \
                    + [n for c in lists[:li] for n in c]
                print("    按 anchor 推算 2026 首发前 5 个: %s"
                      % ", ".join(first_n([tail])))

    ep = data.get("EP")
    if ep:
        v = pick_version(ep, 2026)
        print("[3] 2026 年 EP 第 1 套前 5 个名字 (版本 from=%s): %s"
              % (v.get("from"), ", ".join(v["lists"][0][:5])))

    # ---- 规模汇总 ----
    print("\n=== 规模汇总 ===")
    for code in ("WP", "AL", "EP", "CP", "IO"):
        b = data.get(code)
        if not b:
            continue
        vs = b.get("versions") or []
        shapes = ["%sx%s" % (len(v.get("lists", [])),
                             len(v["lists"][0]) if v.get("lists") else 0)
                  for v in vs]
        tot = sorted({len(c) * len(v.get("lists", []))
                      for v in vs for c in (v.get("lists") or [[]])[:1]})
        print("  %-3s versions=%d  shape=%s  名字总数=%s"
              % (code, len(vs), ",".join(shapes), tot))
    if "SH" in data:
        for code, b in (data["SH"].get("sub") or {}).items():
            if b.get("kind") == "sub_lists":
                for k, v in (b.get("sub") or {}).items():
                    print("  SH/%-3s/%-9s names=%d" % (code, k, len(v.get("names") or [])))
            else:
                for v in (b.get("versions") or []):
                    n = sum(len(c) for c in v.get("lists", []))
                    print("  SH/%-3s (from %s) names=%d" % (code, v.get("from"), n))

    print("\n=== 结果 ===")
    for w in warnings:
        print("  [warn] %s" % w)
    if errors:
        for e in errors:
            print("  [FAIL] %s" % e)
        print("校验未通过：%d 个错误" % len(errors))
        return 1
    print("校验通过 ✓ （0 错误，%d 警告）" % len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
