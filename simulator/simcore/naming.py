# -*- coding: utf-8 -*-
"""热带气旋命名 —— 各洋区真实命名表的接入。

数据源(全部是抓取来的静态文件, 离线可用):
  simulator/env/model/tc_names.json         现行表 + WP 的 21 个版本快照(2000-2026)
  simulator/env/model/tc_names_history.json **2000 年以前**的历史表(含 JTWC 的 92 名名单)
  simulator/env/model/tc_names_annual.json  各洋区逐年命名数(用于推算起点指针)

## 为什么不能"一年一套"

用户 2026-09-17 提供的验收日期里有 **1997-09-15**, 而 WP 的现行 140 名表
**2000-01-01 才启用** —— 1997 年用的是 JTWC 自己维护的 92 名名单
(1996-01-01 生效、严格顺序连续使用、**跨年不重置**)。三条独立证据链已核实:
1997 首个 = Hannah, 1998 首个 = Nichole, 1999 末 = Gloria,
2000 首个新名 = Damrey。中间没有过渡期。

而且即使是现行 140 名表, 也是 **I→II→III→IV→V 连续循环、一年通常跨两套**
(2025 末 = Koto 第 3 套第 5 个 → 2026 首个 = Nokaen 第 3 套第 6 个)。
所以统一用**全局指针**而不是"年 → 套"的映射。

AL/EP 不同: NHC 明写 2026 用第 1 套、2032 复用同套 → **一年一套**成立。
"""
from __future__ import annotations

import datetime as _dt
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL = os.path.normpath(os.path.join(_HERE, '..', 'env', 'model'))

#: 洋区代码 → 生成经度判断(与 eval_tc.basin_of 一致的口径)
_BASIN_LON = (('WP', 100.0, 180.0), ('EP', 180.0, 260.0),
              ('AL', 260.0, 360.0), ('IO', 20.0, 100.0))

#: 命名门槛(JTWC/NHC 口径: 34 kt 的 1 分钟持续风速)
NAME_KT = 34.0

#: 南半球**子洋区**(WMO 责任区)与各自的命名规则。
#: 用户 2026-09-19: "南半球没有命名(BOM 要求四象限 >= 35kt 命名, 南半球其它机构
#: 自己找)"。原来 basin_of 给南半球的是 SI/SP/SA, 而命名表里只有 SH→sub
#: (AU/FJ/ID/MU), 于是 next_name('SP') 找不到表 → 只能返回 'SP01' 这种编号,
#: 南半球风暴**永远拿不到名字**。
#:   AU  BOM(澳区 90-160E): 四象限都有 >=35kt 大风才算命名(比 JTWC 严一档)
#:   FJ  RSMC Nadi(160E-120W): 达到大风(34kt)即命名
#:   MU  RSMC La Réunion(30-90E): 中等热带风暴(34kt)即命名
#:   ID  TCWC Jakarta(90-100E 附近, 印尼): 34kt
_SH_SUB = (('MU', 30.0, 90.0), ('ID', 90.0, 100.0), ('AU', 100.0, 160.0),
           ('FJ', 160.0, 250.0))


def sub_basin(lon: float, lat: float) -> str:
    """南半球按经度定 WMO 责任区(AU/FJ/MU/ID); 北半球返回 ''。"""
    if lat >= 0:
        return ''
    lo = lon % 360.0
    for nm, a, b in _SH_SUB:
        if a <= lo < b:
            return nm
    return ''


def name_threshold(lat: float, lon: float) -> float:
    """该位置命名所需的最大持续风速(kt)。

    BOM(澳区)要"四象限都 >=35kt"; 其它机构(含 JTWC/NHC)34kt。这里返回阈值,
    四象限的检查在调用方(core.maybe_name)按风场做。
    """
    return 35.0 if sub_basin(lon, lat) == 'AU' else NAME_KT

_CACHE = {'ok': False}


def basin_of(lon: float) -> str:
    """按生成经度定洋区(与评估工具同口径)。"""
    lo = lon % 360.0
    for nm, a, b in _BASIN_LON:
        if a <= lo < b:
            return nm
    return 'SH'


def _load(name):
    p = os.path.join(_MODEL, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


_TIMES = {'ok': False, 'data': None}


def name_times():
    """**JMA 官方 best track 抽出来的真实命名时刻表**(tools/build_name_times.py 生成)。

    结构: {'WP': {'2025': [{'cy': 1, 'name': 'WUTIP', 'named': '2025-06-11 12:00'}, ...]}}
    用户 2026-09-20: "获取每个 tc 的真实命名时间, 存起来, 而不是靠指针按日期比例推进"。
    """
    if not _TIMES['ok']:
        _TIMES['ok'] = True
        _TIMES['data'] = _load('tc_name_times.json') or {}
    return _TIMES['data']


def _tables():
    if _CACHE['ok']:
        return _CACHE
    _CACHE['ok'] = True
    _CACHE['cur'] = _load('tc_names.json') or {}
    _CACHE['hist'] = _load('tc_names_history.json') or {}
    _CACHE['annual'] = _load('tc_names_annual.json') or {}
    return _CACHE


# ── 现行表(2000 起 / AL/EP 一直适用) ──

def _flat_wp(cur, year: int):
    """WP 该年的 140 名展平序列(按版本取 from <= year 的最新一版)。"""
    wp = (cur or {}).get('WP')
    if not wp:
        return None
    ver = None
    for v in wp.get('versions', []):
        if int(v.get('from', 0)) <= year:
            if ver is None or int(v['from']) > int(ver['from']):
                ver = v
    if ver is None:
        ver = (wp.get('versions') or [None])[0]
    if not ver:
        return None
    lists = ver.get('lists') or []
    flat = []
    for col in lists:
        flat.extend([str(x) for x in col])
    return flat or None


def _wp_pointer(cur, annual, year: int, month: int, day: int):
    """WP 现行 140 名表的**全局指针**(该时刻下一个该用的名字的序号)。

    指针从 anchor(2026 年第 3 套第 6 个 = 全局 61)出发, 用逐年命名数与
    季节分布往前/往后推。
    """
    wp = (cur or {}).get('WP') or {}
    anc = wp.get('anchor') or {}
    n_per = 28
    a_year = int(anc.get('year', 2026))
    a_p = int(anc.get('list_index', 2)) * n_per + int(anc.get('name_index', 5))
    # anchor 的时点是 2026-01-16(2026 季首个命名), 用日序换算
    a_doy = 16
    cnt = (annual or {}).get('wp', {}) or {}
    doy = _dt.date(year, month, day).timetuple().tm_yday
    # 年内已命名比例: 用季节核密度太贵, 这里用"台风季主要在 5-11 月"的分段近似
    # (实测 WP 年命名数 ~30, 1-4 月合计约 2 个)
    def _frac(y, d):
        # 粗略累计比例: 1-4 月 7%, 5 月 12%, 6 月 18%, 7 月 31%, 8 月 47%,
        # 9 月 63%, 10 月 79%, 11 月 91%, 12 月 100%
        brk = (31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)
        cum = (0.07, 0.12, 0.18, 0.31, 0.47, 0.63, 0.79, 0.91, 0.97, 0.99, 1.0, 1.0)
        for i, b in enumerate(brk):
            if d <= b:
                lo = brk[i - 1] if i else 0
                f0 = cum[i - 1] if i else 0.0
                r = (d - lo) / max(1, b - lo)
                return f0 + (cum[i] - f0) * r
        return 1.0

    def _n(y):
        v = cnt.get(str(y))
        if v:
            return float(v)
        return 30.0                      # 气候平均

    if year == a_year:
        # **同年**: anchor 在 1 月 16 日, 目标可能早于它 → 增量必须可正可负。
        # 原来把它当成"年初"处理, 于是 2026-01-01 算成 61+29=90(应为 60)。
        p = a_p + int(round(_n(year) * (_frac(year, doy) - _frac(a_year, a_doy))))
    elif year > a_year:
        # 往后推
        delta = _n(a_year) * (1.0 - _frac(a_year, a_doy))       # anchor 年剩余
        for y in range(a_year + 1, year):
            delta += _n(y)
        delta += _n(year) * _frac(year, doy)
        p = a_p + int(round(delta))
    else:
        # 往前推
        delta = _n(year) * (1.0 - _frac(year, doy))             # 该年剩余
        for y in range(year + 1, a_year):
            delta += _n(y)
        delta += _n(a_year) * _frac(a_year, a_doy)
        p = a_p - int(round(delta))
    n = len(_flat_wp(cur, year) or []) or 140
    return p % n


def _wp_pre2000(hist, year: int, month: int, day: int):
    """2000 年以前的 WP 名表(JTWC)。

    优先用历史文件里**逐年实测的序列**; 没有那一年时, 用 92 名名单按
    '跨年不重置'的规则外推。
    """
    wp = (hist or {}).get('WP') or {}
    pre = wp.get('pre2000') or {}
    by = pre.get('by_year') or {}
    rec = by.get(str(year))
    if isinstance(rec, dict):
        names = rec.get('names') or []
    elif isinstance(rec, list):
        names = rec
    else:
        names = []
    if names:
        return [str(x) for x in names], None
    # 外推: 用 lists 里的 92 名, 从某个已知年份的已知指针推
    lst = pre.get('lists') or {}
    flat = []
    for col in (lst.get('columns') or lst.get('lists') or []):
        flat.extend([str(x) for x in col])
    if not flat:
        # 退路: by_year 里任意一年的序列长度作为周期
        for y2 in sorted(by):
            r = by[y2]
            nm = r.get('names') if isinstance(r, dict) else r
            if nm:
                flat = [str(x) for x in nm]
                break
    return (flat or []), None


# ── 对外接口 ──

class Namer:
    """按洋区给台风分配真实名字。一个模拟实例一个(指针随生成推进)。"""

    def __init__(self, start: str):
        t = _tables()
        self.cur, self.hist, self.annual = t['cur'], t['hist'], t['annual']
        if isinstance(start, _dt.datetime):
            self._d = start
        elif isinstance(start, _dt.date):
            self._d = _dt.datetime(start.year, start.month, start.day)
        else:
            _s = str(start).strip().replace('T', ' ')
            for _f in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H',
                       '%Y-%m-%d', '%Y-%m', '%Y'):
                try:
                    self._d = _dt.datetime.strptime(_s, _f)
                    break
                except ValueError:
                    continue
            else:
                self._d = _dt.datetime(2000, 1, 1)
        self._ptr = {}          # basin → 下一个名字的下标(懒初始化)
        self._used = {}         # (basin, year) → 已用掉的真实命名编号集合
        self._log = []

    # -- WP --
    def _wp_name(self, i: int) -> str:
        y = self._d.year
        if y >= 2000:
            flat = _flat_wp(self.cur, y)
            if flat:
                return flat[i % len(flat)]
        names, _ = _wp_pre2000(self.hist, y, self._d.month, self._d.day)
        if names:
            return names[i % len(names)]
        return 'WP%02d' % (i + 1)

    def _wp_start(self) -> int:
        y = self._d.year
        if y >= 2000:
            return _wp_pointer(self.cur, self.annual, y, self._d.month, self._d.day)
        # 2000 前: 用该年实测序列, 从 0 开始(序列本身就是那年的实际顺序)
        return 0

    # -- AL / EP: 一年一套 --
    def _rot_start(self, basin: str) -> int:
        d = self.cur.get(basin) or {}
        cyc = int(d.get('cycle_years', 6) or 6)
        lists = None
        for v in d.get('versions', []):
            if int(v.get('from', 0)) <= self._d.year:
                lists = v.get('lists')
        if not lists and d.get('versions'):
            lists = d['versions'][0].get('lists')
        # 套数
        nset = len(lists) if lists else cyc
        # anchor 年对应第 0 套
        anc = d.get('anchor') or {}
        a_year = int(anc.get('year', 2026))
        li = (self._d.year - a_year) % max(1, nset)
        # 年内起点: 用季节分布近似(北大西洋 6-11 月, 东太 5-11 月)
        doy = self._d.timetuple().tm_yday
        cnt = (self.annual or {}).get(basin.lower(), {}) or {}
        nmax = float(cnt.get(str(self._d.year)) or
                     (21 if basin == 'AL' else 24))
        if basin == 'AL':
            frac = min(1.0, max(0.0, (doy - 151) / 183.0))     # 6 月起
        else:
            frac = min(1.0, max(0.0, (doy - 121) / 213.0))     # 5 月起
        return int(li), int(round(nmax * frac))

    def name_by_time(self, basin: str, when):
        """按**真实命名时刻**取名字: 在该年该洋区里选"离 when 最近的、还没用过"的那一个。

        比"按日期比例推指针"可靠得多 —— 指针只能猜年内进度(实测 2025 会被推到 Krathon),
        而这张表是 JMA 官方 best track 里每条风暴**首次达到 TS 的时刻**。
        """
        if when is None:
            return None
        t = name_times().get(basin.upper()) or {}
        rows = t.get(str(getattr(when, 'year', '')))
        if not rows:
            return None
        try:
            import datetime as _d
            key = (basin.upper(), str(when.year))
            used = self._used.setdefault(key, set())
            best, bi = None, None
            for r in rows:
                if r['cy'] in used or not r.get('named'):
                    continue
                try:
                    d = _d.datetime.strptime(r['named'], '%Y-%m-%d %H:%M')
                except Exception:
                    continue
                gap = abs((d - when).total_seconds())
                if best is None or gap < best:
                    best, bi = gap, r
            if bi is None:
                return None
            used.add(bi['cy'])
            return bi['name']
        except Exception:
            return None

    def next_name(self, basin: str, lon: float = None,
                  lat: float = None, when=None) -> str:
        basin = (basin or 'WP').upper()
        # 南半球: basin 给 SI/SP/SA/SH 都先解析到 WMO 子洋区(AU/FJ/MU/ID)
        if basin in ('SI', 'SP', 'SA', 'SH') and lon is not None:
            sub = sub_basin(float(lon), float(lat or -15.0))
            if sub:
                basin = sub
        # **优先用真实命名时刻表**(JMA); 没有该年数据才回落到指针
        _bt = self.name_by_time(basin, when)
        if _bt:
            return _bt
        if basin == 'WP':
            if basin not in self._ptr:
                self._ptr[basin] = self._wp_start()
            nm = self._wp_name(self._ptr[basin])
            self._ptr[basin] += 1
        elif basin in ('AL', 'EP'):
            d = self.cur.get(basin) or {}
            lists = None
            for v in d.get('versions', []):
                if int(v.get('from', 0)) <= self._d.year:
                    lists = v.get('lists')
            if not lists and d.get('versions'):
                lists = d['versions'][0].get('lists')
            if not lists:
                return '%s%02d' % (basin, self._ptr.get(basin, 0) + 1)
            li, off = self._rot_start(basin)
            key = (basin, li, off)
            if key not in self._ptr:
                self._ptr[key] = off
            col = lists[li % len(lists)]
            nm = str(col[self._ptr[key] % len(col)])
            self._ptr[key] += 1
        else:
            # CP / IO / SH: 单一循环表(含子洋区)
            d = self.cur.get(basin)
            if basin == 'SH':
                d = ((self.cur.get('SH') or {}).get('sub') or {}).get('AU') or {}
            elif basin in ('AU', 'FJ', 'ID', 'MU'):
                d = ((self.cur.get('SH') or {}).get('sub') or {}).get(basin) or {}
            lists = None
            if d:
                for v in d.get('versions', []):
                    if int(v.get('from', 0)) <= self._d.year:
                        lists = v.get('lists')
                if not lists and d.get('versions'):
                    lists = d['versions'][0].get('lists')
            if not lists:
                return '%s%02d' % (basin, self._ptr.get(basin, 0) + 1)
            col = lists[0]
            if basin not in self._ptr:
                self._ptr[basin] = 0
            nm = str(col[self._ptr[basin] % len(col)])
            self._ptr[basin] += 1
        self._log.append((basin, nm))
        return nm

    # -- 诊断 --
    def peek(self, year: int, month: int, day: int, basin: str = 'WP'):
        """不消耗指针地预览该时刻的起始名字(给测试/UI 用)。"""
        old = self._d
        self._d = _dt.datetime(year, month, day)
        if basin == 'WP':
            i = self._wp_start() if year >= 2000 else 0
            nm = self._wp_name(i)
        else:
            d = self.cur.get(basin) or {}
            lists = None
            for v in d.get('versions', []):
                if int(v.get('from', 0)) <= year:
                    lists = v.get('lists')
            nm = '?'
            if lists:
                li, off = self._rot_start(basin)
                col = lists[li % len(lists)]
                nm = str(col[off % len(col)])
        self._d = old
        return nm
