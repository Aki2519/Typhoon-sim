# -*- coding: utf-8 -*-
"""跑一整年模拟: ① 每 6 小时存 sst/ohc/风切图(+ 原始场) ② 结束后把所有模拟台风导出成
简化 b-deck(ATCF 风格)到指定目录。

用户要求: **不要实时记录到 typhoon 文件夹** —— 运行期间只往数据目录写
(默认 G:/气象/数据/sim2025), b-deck 跑完之后再一次导出。

产物:
  <data>/sst|ohc|shear/<YYYYMMDDHH>.png     每 6 小时一张(全球)
  <data>/fields/<field>_<YYYYMM>.npy        原始场(float16, 每月一个)
  <bdeck>/<basin><NN><年>.dat               简化 b-deck(一个台风一个文件)

用法:
  python tools/sim_year.py --year 2025 --days 365
  python tools/sim_year.py --year 2025 --days 5 --no-maps      # 快速试跑
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import zlib

import numpy as np

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

DEFAULT_DATA = r'G:\气象\数据\sim2025'
#: **实时记录目录**: 用户要求写到 ./sim_typhoon(仓库根下), **不写 typhoon/**;
#: 由界面上的按钮手动"移动到 typhoon/<年份>"。见 app 里的 _btn_sim_move_bdeck。
DEFAULT_BDECK = os.path.join(ROOT, 'sim_typhoon')
SIG = bytes([137, 80, 78, 71, 13, 10, 26, 10])          # PNG 魔数


def png_bytes(rgb):
    """(H, W, 3) uint8 -> PNG 字节(zlib+struct 自己写, 不经过 SDL)。

    起因: pygame.image.save 在这台机器上不可靠 —— 传路径时出现过 65 字节的截断文件,
    传文件对象时干脆写成别的格式(读回来 "Unsupported image format")。
    """
    h, w, _ = rgb.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)                                   # 每行 filter type = 0
        raw += rgb[y].tobytes()

    def _chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    return (SIG
            + _chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(bytes(raw), 6))
            + _chunk(b'IEND', b''))


def save_maps(outdir, ts, sst, ohc_kj, shear, land, zoom=3):
    """三张全球图。**与视频共用同一套出图代码**(compare_fields.pad_polar + rgb_domain)。

    2026-09-21 的教训: 这条"直存 PNG"与 tools/make_video 的"从 npy 出片"原来是**两套**
    各自实现翻转/画布的实现 —— 于是我修好了 PNG、视频却还是倒的(用户: "视频里还是反的")。
    现在两条路都只做一件事: pad_polar(±60 → ±90 北在上画布, 域外 NaN) + rgb_domain(上色,
    陆地为灰, **域外画成无数据色并标出域边界**)。
    """
    import compare_fields as CF
    fields = (('sst', np.asarray(sst, np.float32)),
              ('ohc', np.asarray(ohc_kj, np.float32)),
              ('shear', np.asarray(shear, np.float32)))
    z = max(1, int(zoom))
    for fld, arr in fields:
        canvas, lm = CF.pad_polar(arr, land)          # ±90 画布, 行 0 = 89.75N
        if z > 1:
            h, w = canvas.shape
            h2, w2 = h // z, w // z
            canvas = np.nanmean(canvas[:h2 * z, :w2 * z].reshape(h2, z, w2, z),
                                axis=(1, 3))
            if lm is not None:
                lm = (lm[:h2 * z, :w2 * z].reshape(h2, z, w2, z).mean(axis=(1, 3)) > 0.6)
        rgb = CF.rgb_domain(fld, canvas, lm)
        d = os.path.join(outdir, fld)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '%s.png' % ts), 'wb') as fh:
            fh.write(png_bytes(np.ascontiguousarray(rgb)))


def bdeck_basin(basin):
    """洋区代号按用户规则归一(2026-09-20): **没有 SI** —— 南半球除南大西洋统称 SH。

    模型内部会吐 SI(南印度洋)/ SP(南太平洋)/ SA(南大西洋), 而 b-deck 里要写成
    SH / SH / SA。北半球与其余保持原样(WP / EP / AL / IO / CP…)。
    """
    b = str(basin or 'WP').upper()
    return 'SH' if b in ('SI', 'SP') else b


#: 洋区 → "编号B"(未命名时用的低压编号后缀)。WP 的 03W、SH 的 01S …
_ID_SUFFIX = {'WP': 'W', 'EP': 'E', 'CP': 'C', 'AL': 'L', 'IO': 'A', 'SH': 'S', 'SA': 'Q'}


def _sm():
    """界面侧 SimModeMixin —— 纬度/经度写法与记录目录名**只有那一处实现**, 这里复用。"""
    from app.ty_sim_mixins.sim_mode_mixin import SimModeMixin
    return SimModeMixin


def bdeck_invest_code(tc):
    """扰动码(**不带洋区后缀**): `A3` / `C6`。

    用户 2026-09-21: "扰动在第二列就写 C6, 不是 C6W" —— 扰动码本体是"字母+数字"两位
    (core.make_tc 的 A0..Z9), 老记录里可能存成 `A3W`(码+洋区后缀), 这里统一削掉后缀。
    """
    inv = str((tc or {}).get('invest') or 'A0').strip().upper()
    if len(inv) == 3 and inv[1].isdigit():
        inv = inv[:2]
    return inv or 'A0'


def bdeck_invest_num(tc) -> int:
    """invest 编号(90-99), 未命名系统的第 12 列用它(如 `93W`)。"""
    try:
        return int((tc or {}).get('investNum') or 90)
    except (TypeError, ValueError):
        return 90


def bdeck_has_number(tc) -> bool:
    """有低压编号吗(`cy` 非空; 达到 TD 才给)。第 2 列按这个判据走。"""
    return (tc or {}).get('cy') is not None


def bdeck_named(tc) -> bool:
    """已命名吗 —— core 在取名那一刻同时置 `named=True` 与 `namedAt`。"""
    return bool((tc or {}).get('named')) or (tc or {}).get('namedAt') is not None


def bdeck_is_invest(tc) -> bool:
    """**扰动**吗 —— 既没有低压号、也还没命名。

    目录分家按这个判据(用户 2026-09-21: 扰动与"**有低压号/已命名**"的系统分目录),
    所以"命名了但没有低压号"的系统(directName 通道)算**正式**, 进正式目录;
    但它的第 2 列仍然是扰动码(第 2 列只看有没有低压号, 见 bdeck_lines)。
    """
    return not bdeck_has_number(tc) and not bdeck_named(tc)


def bdeck_subdir(year, basin, invest: bool = False) -> str:
    """记录目录名: 正式 `2025wp` / 扰动 `2025wp_invest`(洋区先按用户规则归一: 没有 SI)。"""
    return _sm()._bdeck_subdir(year, bdeck_basin(basin), invest)


def bdeck_file_names(basin, tc, year):
    """→ (按低压号的名字|None, 按扰动码的名字), 用来找"同一系统在别处留下的旧文件"。

    正式: `<洋区><NN><年>.dat`(如 `wp032025.dat`)
    扰动: `<洋区><扰动码><年>.dat`(如 `wpA32025.dat`) —— 用户 2026-09-21:
    "为什么文件名是 wp932025 而不是 wpA32025" ⇒ **用扰动码, 不用 invest 数字**。
    """
    b = bdeck_basin(basin).lower()
    cy = (tc or {}).get('cy')
    formal = ('%s%02d%d.dat' % (b, int(cy), int(year))) if cy is not None else None
    return formal, '%s%s%d.dat' % (b, bdeck_invest_code(tc), int(year))


def bdeck_file_name(basin, tc, year) -> str:
    """该系统该落的文件名: 有低压号 → `wp032025.dat`; 没有 → `wpA32025.dat`。

    注意"文件名"与"放哪个目录"是**两条判据**: 命名了但没低压号的系统进正式目录,
    文件名仍然是扰动码那个(它没有编号可用)。
    """
    formal, invest = bdeck_file_names(basin, tc, year)
    return formal or invest


def bdeck_first_cols(path):
    """读文件**第一条数据行**的 12 列(去空白), 空文件返回 []。"""
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for line in f:
                if line.strip():
                    return [c.strip() for c in line.split(',')]
    except OSError:
        return []
    return []


def bdeck_file_has_number(path) -> bool:
    """文件里有**低压号**吗(某一行第 2 列是纯数字)。

    只有这种文件才参与收尾的 `01/02/03…` 编号 —— 扰动文件第 2 列是扰动码, 而
    "命名了但没有低压号"的文件第 2 列也是扰动码, 两者都**没有号可用**, 不该被编号。
    """
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            for line in f:
                cols = [c.strip() for c in line.split(',')]
                if len(cols) > 1 and cols[1].isdigit():
                    return True
    except OSError:
        return False
    return False


def bdeck_file_is_invest(path) -> bool:
    """文件里记的是**扰动**吗 —— 全程没有低压号、且全程没出现过名字。

    第 12 列形如 `03W` / `90W`(编号+洋区后缀)的**不是名字**(与 tools/clean_staged_bdeck
    同一口径), 所以"编号期"的文件仍算扰动; 一旦某一行出现 `Dikeledi` 这种名字就算正式。
    """
    import re as _re
    numbered = named = False
    try:
        with open(path, encoding='ascii', errors='replace') as f:
            rows = [[c.strip() for c in l.split(',')] for l in f if l.strip()]
    except OSError:
        return False
    for cols in rows:
        if len(cols) > 1 and cols[1].isdigit():
            numbered = True
        if len(cols) > 11 and cols[11] and not _re.fullmatch(r'\d{2}[A-Za-z]', cols[11]):
            named = True
    return not (numbered or named)


def bdeck_lines(tc, base, interval_h=6):
    """单台风的 b-deck 行 —— 用户自评格式(12 列), 并区分**低压 / 扰动**:

        WP, 03, 2025062212,   ,chunshu,   0, 138N, 1196E,  15, 1010, DB,96W
        WP, C6, 2025062212,   ,chunshu,   0,  97N, 1196E,  15, 1010, DB,93W

    规则(用户 2026-09-21):
      · 有低压编号(达到 TD 才给, tc 的 cy 非空) → 第 2 列写两位序号(如 `03`);
      · 没低压编号(仍是扰动) → 第 2 列写**扰动码本身**(如 `C6`, 白纸黑字**不带洋区后缀**;
        用户: "扰动在第二列就写 C6, 不是 C6W");
      · 第 12 列: 命名后换名字(Title case), **命名前一律是 invest 编号 + 洋区后缀**(`96W`);
        于是会出现"有台风名、但没有低压号"的系统(用户要的就是这种);
      · 纬度/经度按"度×10 + N/S / E/W"写, **不留多余前导零**(97N / 138N / 75S / 1196E)。

    第 12 列为什么是 invest 编号而不是低压号: 用户给的权威模板那一行就是
    `WP, 03, ..., DB,96W` —— 低压号已经是 03、末列却仍是 96W(自评文件
    `2025 03W wp032025.dat` 的头几行、`bal012026 Arthur.dat` 的 DB 段都是这样)。
    """
    from datetime import timedelta
    tr = tc.get('track') or []
    basin = bdeck_basin(tc.get('basin', 'WP'))
    suffix = _ID_SUFFIX.get(basin, 'W')
    cy = tc.get('cy')
    inv = bdeck_invest_code(tc)
    inv_num = bdeck_invest_num(tc)
    named_h = tc.get('namedAt')
    num_h = tc.get('numberedAt')
    name = str(tc.get('name') or '').strip()
    sm = _sm()
    lines = []
    for q in tr:
        h = int(round(float(q['t'])))
        if h % max(1, interval_h) != 0:
            continue
        dt = base + timedelta(hours=h)
        num_here = (cy is not None) and (num_h is None or float(h) >= float(num_h) - 1e-6)
        # 第 2 列: 有低压号 → 两位序号; 仍是扰动 → 只写扰动码本身(A3, 不是 A3W)
        col2 = ('%02d' % cy) if num_here else inv
        vmax = int(round(float(q['vmax'])))
        pmin = int(round(float(q.get('pmin', 1010.0))))
        # **第 11 列用模型自己的阶段码**(2026-09-21): 原来是纯 vmax 映射
        # (), 于是模型永远只会输出 DB/TS/TY/ST 四种,
        # 用户报的"TD 一次都没出现 / 减弱从 TS 直跳 DB / 没见过 EX、LO、WV、SS、SD、MD"
        # 全是这一处造成的。现在取 tc 的权威阶段码(phase.py 判定)并做上限断言。
        st = str(q.get('phase') or '').strip().upper()
        if st not in ('DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD'):
            st = 'DB' if vmax < 34 else ('TS' if vmax < 64 else ('TY' if vmax < 96 else 'ST'))
        named_here = bool(name) and named_h is not None and float(h) >= float(named_h) - 1e-6
        ident = name.title() if named_here else ('%02d%s' % (inv_num, suffix))
        lines.append((dt.strftime('%Y%m%d%H'),
                      '%s, %s, %s,   ,chunshu,   0,%5s,%6s,%4d,%5d,%3s,%s'
                      % (basin, col2, dt.strftime('%Y%m%d%H'),
                         sm._bdeck_lat(float(q['lat'])), sm._bdeck_lon(float(q['lon'])),
                         vmax, pmin, st, ident)))
    # **同一时次只保留最后一条**(2026-09-21): make_tc 以前会预塞一条"生成时刻"的记录,
    # 与主循环同一步补的"步后"记录标同一个时刻 —— 实测 b-deck 头两行同标 2025010806
    # 却是 20kt/31kt 两个值。模型侧已改(track 初始为空), 这里再加一道: 万一还有重复,
    # 保留**最后**一条(步后状态才是该时刻的真实状态)。
    dedup = []
    for lab, ln in lines:
        if dedup and dedup[-1][0] == lab:
            dedup[-1] = (lab, ln)
        else:
            dedup.append((lab, ln))
    return [ln for _, ln in dedup], basin, cy


def stage_bdeck(stage_dir, tc, base, year, interval_h=6):
    """**运行期间**把单个系统写进实时记录目录(默认 ./sim_typhoon)。

    目录按用户 2026-09-21 的要求**分家**(扰动单独一个文件夹):
      · 有低压号 / 已命名(正式系统) → `<记录根>/<年>/<年><洋区>/<洋区><NN|码><年>.dat`
      · 仍是扰动                      → `<记录根>/<年>/<年><洋区>_invest/<洋区><扰动码><年>.dat`
        (文件名用**扰动码**, 如 `wpA32025.dat`; 不是 invest 数字 `wp932025.dat`)

    同一个系统**后来才升级成正式系统**时, 这次落盘会把它以前留下的旧文件删掉
    (两个目录、两种命名都查一遍), 等于"从 invest 目录搬走", 不会出现同一系统两份。
    """
    lines, basin, cy = bdeck_lines(tc, base, interval_h)
    if not lines:
        return None
    invest = bdeck_is_invest(tc)
    formal_dir = os.path.join(stage_dir, str(year), bdeck_subdir(year, basin, False))
    invest_dir = os.path.join(stage_dir, str(year), bdeck_subdir(year, basin, True))
    tgt_dir = invest_dir if invest else formal_dir
    os.makedirs(tgt_dir, exist_ok=True)
    keep = bdeck_file_name(basin, tc, year)
    fn = os.path.join(tgt_dir, keep)
    with open(fn, 'w', encoding='ascii', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    # 搬家: 同一系统在**两个目录**下留下的旧名(升级换目录 / 换编号)一并清掉
    formal_name, inv_name = bdeck_file_names(basin, tc, year)
    for d in (formal_dir, invest_dir):
        for n in (formal_name, inv_name):
            if not n or n == keep:
                continue
            old = os.path.join(d, n)
            if os.path.isfile(old):
                os.remove(old)
    return fn


def finalize_bdeck(stage_dir, year, interval_h=6):
    """收尾: ① 扰动/正式**分流** ② 正式系统在洋区内按首现时刻重新编号。返回文件数。

    ① 分流: 目录里放错的文件搬回该去的目录(比如"先以扰动落盘、后来升级成正式系统"),
       判据看**文件内容**(全程无低压号且全程无名字 ⇒ 扰动), 目标名按内容算。
    ② 编号: 只编**正式**目录(`<年><洋区>`)里**有低压号**的文件 —— 扰动目录 `*_invest`
       不编号, 正式目录里"命名了但没有低压号"的文件也不编号(它没有号可用)。
       **只改第 2 列**(低压序号); 第 12 列是 invest 编号或名字, 与序号无关, 原样保留
       —— 自评文件 `2025 03W wp032025.dat` 里第 12 列就是 96W, 不随文件号走。

    为什么要在收尾改名: 实时落盘时按默认名写, 收尾才按全年该洋区的先后统一编号。
    """
    import glob as _glob
    import shutil as _sh
    sm = _sm()
    root = os.path.join(stage_dir, str(year))
    if not os.path.isdir(root):
        return 0
    # ① 分流: 放错目录的搬回该去的目录
    for sub in sorted(os.listdir(root)):
        sdir = os.path.join(root, sub)
        if not os.path.isdir(sdir):
            continue
        basin, invest_dir = sm._bdeck_parse_subdir(year, sub)
        if basin is None:
            continue
        for path in sorted(_glob.glob(os.path.join(sdir, '*.dat'))):
            is_inv = bdeck_file_is_invest(path)
            if is_inv == invest_dir:
                continue
            cols = bdeck_first_cols(path)
            if len(cols) < 3:
                continue
            name = ('%s%s%d.dat' % (basin, cols[1], int(year)) if is_inv
                    else '%s%02d%d.dat' % (basin, int(cols[1]), int(year)))
            dst_dir = os.path.join(root, sm._bdeck_subdir(year, basin, is_inv))
            os.makedirs(dst_dir, exist_ok=True)
            _sh.move(path, os.path.join(dst_dir, name))
    # ② 正式目录里"有低压号"的文件按首现时刻重新编号
    n_out = 0
    for sub in sorted(os.listdir(root)):
        sdir = os.path.join(root, sub)
        if not os.path.isdir(sdir):
            continue
        basin, invest_dir = sm._bdeck_parse_subdir(year, sub)
        if basin is None or invest_dir:
            continue                      # 扰动不占低压号, 不编号
        files = [p for p in _glob.glob(os.path.join(sdir, '*.dat'))
                 # 轮次 124 修: 原来只收"**有低压号**"的文件(`bdeck_file_has_number`),
                 # 于是"命名了但从未拿到低压号"的系统留在**正式目录**里、却挂着
                 # 扰动码文件名(`wpE52025.dat`)—— 实测 `output/sim/2025/2025/2025wp/`
                 # 213 个文件里有 **26 个字母命名文件**。
                 # ATCF 口径下"命名即已编号", 所以正式目录里**没有号但有名字**的系统
                 # 也应当占一个低压序号 ⇒ 判据改成 `not bdeck_file_is_invest(p)`
                 # (扰动目录不受影响: 那里全是 is_invest=True 的文件, 第 377 行已跳过)。
                 # 复验: `2025wp/` 内不应再有字母命名文件, 且文件数与摘要一致。
                 if not bdeck_file_is_invest(p)]
        files.sort(key=lambda p: ((bdeck_first_cols(p) + [''] * 3)[2],
                                  os.path.basename(p)))
        out = []
        for i, fn in enumerate(files, start=1):
            with open(fn, encoding='ascii', errors='replace') as f:
                rows = [l.rstrip('\r\n') for l in f if l.strip()]
            fixed = []
            for l in rows:
                cols = l.split(',')
                if len(cols) > 1:
                    lead = ' ' if cols[1].startswith(' ') else ''
                    cols[1] = '%s%02d' % (lead, i)          # 只换第 2 列, 保留原对齐
                fixed.append(','.join(cols))
            out.append(('%s%02d%d.dat' % (basin, i, year), fixed, fn))
        # 先把这一目录读进内存, 再统一落盘 —— 免得 "wp01→wp02" 这种改名的中途互相覆盖
        for old in files:
            os.remove(old)
        for name, fixed, _src in out:
            with open(os.path.join(sdir, name), 'w', encoding='ascii', newline='\r\n') as f:
                f.write('\n'.join(fixed) + '\n')
            n_out += 1
    return n_out



def export_bdeck(sim, outdir, year, interval_h=6):
    """收尾导出(配合 stage_bdeck; 已被内核删掉的老风暴靠实时记录补上)。"""
    os.makedirs(outdir, exist_ok=True)
    base = sim.get('start')
    written = []
    for cy, tc in enumerate(sim.get('tcs', []), start=1):
        tr = tc.get('track') or []
        if len(tr) < 2:
            continue
        cyc = int(tc.get('cy', cy))
        basin = str(tc.get('basin', 'WP')).upper()
        lines = []
        for q in tr:
            h = int(round(float(q['t'])))
            if h % max(1, interval_h) != 0:
                continue
            dt = base + timedelta(hours=h)
            vmax = int(round(float(q['vmax'])))
            pmin = int(round(float(q.get('pmin', 1010.0))))
            # **第 11 列用模型自己的阶段码**(2026-09-21): 原来是纯 vmax 映射
        # (), 于是模型永远只会输出 DB/TS/TY/ST 四种,
        # 用户报的"TD 一次都没出现 / 减弱从 TS 直跳 DB / 没见过 EX、LO、WV、SS、SD、MD"
        # 全是这一处造成的。现在取 tc 的权威阶段码(phase.py 判定)并做上限断言。
        st = str(q.get('phase') or '').strip().upper()
        if st not in ('DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD'):
            st = 'DB' if vmax < 34 else ('TS' if vmax < 64 else ('TY' if vmax < 96 else 'ST'))
            lines.append('%s, %02d,%s,   ,%s,   0, %s, %s, %3d, %4d, %s,    0'
                         % (basin, cyc, dt.strftime('%Y%m%d%H'),
                            (tc.get('name') or 'unnamed').lower(),
                            SM._bdeck_lat(float(q['lat'])), SM._bdeck_lon(float(q['lon'])),
                            vmax, pmin, st))
        if not lines:
            continue
        fn = os.path.join(outdir, '%s%02d%d.dat' % (basin.lower(), cyc, year))
        with open(fn, 'w', encoding='ascii', newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')
        written.append(fn)
    return written


def _flush_fields(d, month, buf):
    os.makedirs(d, exist_ok=True)
    for k, v in buf.items():
        if v:
            np.save(os.path.join(d, '%s_%s.npy' % (k, month)), np.stack(v))
    print('  field npy %s: %s' % (month, ', '.join('%s x %d' % (k, len(v)) for k, v in buf.items())),
          flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2025)
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--start', default=None)
    ap.add_argument('--data', default=DEFAULT_DATA)
    ap.add_argument('--bdeck', default=DEFAULT_BDECK,
                    help='实时记录目录(默认 ./sim_typhoon; 收尾不再自动写 typhoon/)')
    ap.add_argument('--export-to-typhoon', default=None,
                    help='可选: 收尾时把这些 b-deck 复制到指定 typhoon 子目录')
    ap.add_argument('--precision', default='100ms')
    ap.add_argument('--seed', type=int, default=20250801)
    ap.add_argument('--no-maps', action='store_true')
    ap.add_argument('--no-fields', action='store_true')
    ap.add_argument('--zoom', type=int, default=3)
    a = ap.parse_args(argv)
    start = a.start or ('%d-01-01 00' % a.year)
    os.makedirs(a.data, exist_ok=True)
    from simulator.simcore import core as V
    t0 = time.time()
    sim = V.init({'start': start, 'seed': a.seed, 'envStepH': 6.0,
                  'params': {'precision': a.precision, 'dataMode': True}})
    step = V.step_hours(sim)
    n = int(a.days * 24 / step)
    land = None
    try:
        import compare_fields as CF
        land = CF.cell_land()
    except Exception as exc:
        print('(land mask unavailable: %s)' % exc, flush=True)
    buf = {'sst': [], 'ohc': [], 'shear': []}
    month = None
    # b-deck 暂存目录(在数据目录里, **不是** typhoon/); 收尾再复制到 a.bdeck
    stage_dir = a.bdeck          # 实时记录目录: ./sim_typhoon(不是数据目录、更不是 typhoon/)
    staged = {}
    print('run %s for %.0f days (precision %s, maps %s, zoom %d) -> %s'
          % (start, a.days, a.precision, 'off' if a.no_maps else 'on', a.zoom, a.data),
          flush=True)
    for i in range(n):
        V.sim_step(sim, step)
        clk = V.clock(sim)
        ts = clk.strftime('%Y%m%d%H')
        sst = np.asarray(sim['env']['sst'], np.float32)
        sh = np.asarray(sim['env']['shear'], np.float32)
        ocn = sim.get('ocn') or {}
        ohc = (np.asarray(ocn['ohc_kj'], np.float32) if ocn.get('ohc_kj') is not None
               else np.zeros_like(sst))
        if not a.no_fields:
            m = clk.strftime('%Y%m')
            if month is None:
                month = m
            elif m != month:
                _flush_fields(os.path.join(a.data, 'fields'), month, buf)
                buf = {'sst': [], 'ohc': [], 'shear': []}
                month = m
            buf['sst'].append(sst.astype(np.float16))
            buf['ohc'].append(ohc.astype(np.float16))
            buf['shear'].append(sh.astype(np.float16))
        if not a.no_maps:
            save_maps(a.data, ts, sst, ohc, sh, land, zoom=a.zoom)
        # **边跑边落盘 b-deck**(写数据目录, 不写 typhoon/): 死亡台风会在 120 天后被内核
        # 剔除, 所以必须在它还活着/刚死时就写出去。
        for _t in sim.get('tcs', []):
            if _t.get('dead') and id(_t) not in staged:
                _f = stage_bdeck(stage_dir, _t, sim.get('start'), a.year)
                if _f:
                    staged[id(_t)] = _f
        if (i + 1) % int(24 * 10 / step) == 0:
            alive = sum(1 for t in sim.get('tcs', []) if not t.get('dead'))
            print('  day %.0f, alive %d, total %d, staged %d, %.0fs'
                  % ((i + 1) * step / 24.0, alive, len(sim.get('tcs', [])),
                     len(staged), time.time() - t0),
                  flush=True)
    if month is not None:
        _flush_fields(os.path.join(a.data, 'fields'), month, buf)
    # 收尾: 还活着的也写进实时记录目录。**不自动写 typhoon/** —— 由界面按钮
    # 「移入台风库」手动搬(用户 2026-09-20 的要求)。
    for _t in sim.get('tcs', []):
        if id(_t) not in staged:
            if stage_bdeck(stage_dir, _t, sim.get('start'), a.year):
                staged[id(_t)] = True
    n_fin = finalize_bdeck(stage_dir, a.year)          # 各洋区按生成时间重新编号
    n_rec = 0
    for _r, _d, _fs in os.walk(stage_dir):
        n_rec += sum(1 for f in _fs if f.endswith('.dat'))
    print('实时记录 %d 个(已按洋区编号 %d 个)→ %s' % (n_rec, n_fin, stage_dir), flush=True)
    print('  目录结构: %s' % os.path.join(stage_dir, str(a.year), '%d<洋区>' % a.year), flush=True)
    if a.export_to_typhoon:                 # 仅在**显式**要求时才写 typhoon/
        import shutil
        os.makedirs(a.export_to_typhoon, exist_ok=True)
        for fn in sorted(os.listdir(stage_dir)):
            if fn.endswith('.dat'):
                shutil.copy2(os.path.join(stage_dir, fn),
                             os.path.join(a.export_to_typhoon, fn))
        print('已复制到 %s' % a.export_to_typhoon, flush=True)
    print('DONE %.0fs' % (time.time() - t0), flush=True)


if __name__ == '__main__':
    main()
