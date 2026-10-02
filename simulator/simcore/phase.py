# -*- coding: utf-8 -*-
"""台风**相态/阶段分类** —— 纯函数, 不依赖模拟状态。

为什么单独一个模块
------------------
`core.py` 的强度方程 (``intensity_step``) 里原来把"相态"和"强度档"混在
一起: 判据写成 ``if phase == 'TC' and vmax < 21: phase = 'LO'``, 于是

  · 强度掉档时**没有逐级**(用户: "减弱的 tc 会直接从 TS 掉到 DB, 没有 TD,
    即使还有 30 多的风速");
  · ``DB`` / ``WV`` / ``LO`` 这三个**非热带气旋**类别没有强度上限, 于是
    "扰动能有 93kt" —— ATCF/JTWC 口径里 DB/WV/LO 的定义本身就把它们限制在
    热带风暴强度以下 (见 ``CAP_KT`` 与 output/sim/_reference/PHASE_LADDER.md);
  · EX/SS/SD 的门槛写在 ``elif phase == 'SD'`` 里, 只有先进 SD 才可能到 EX/SS,
    实测一个都出不来。

本模块把这些判据抽成**纯函数** (输入标量 → 输出阶段码), 让"分类"可测、可
复现、可单独演进; 接线仍然由 ``core.py`` 负责 (见 PHASE_LADDER.md 的接线章节)。

阶段码 (ATCF/JTWC, 见 https://ftp.nhc.noaa.gov/atcf/README
                与 b-deck 格式说明 abdeck/abrdeck)
--------------------------------------------------------------------
热  带: TD(tropical depression) / TS(tropical storm) /
        TY(typhoon) / ST(super typhoon)
副热带: SD(subtropical depression) / SS(subtropical storm)
温  带: EX(extratropical)
季  风: MD(monsoon depression)          ← 北印度洋/南海的大尺度弱暖心低压
非热带气旋 (扰动类):
        DB(disturbance 扰动) / WV(tropical wave 热带波) /
        LO(low 低压/残余槽)

**"是不是热带气旋"与"强度档"是两件事**: 本模块只回答"是哪一类系统"
(phase), 强度档 (TD/TS/TY/ST 的档位) 由 vmax 自己决定。两者叠加才得到
b-deck 第三列那个 st 码。

设计约束 (与用户 2026-09-21 报的三个缺陷一一对应)
------------------------------------------------
1. **逐级减弱**: ``WEAKEN_LADDER`` 规定减弱每调用一次只允许降一档, 且
   ``TS→TD`` 与 ``TD→LO`` 用**两个不同阈值**, 所以 40→30kt 的减弱必须先出现
   TD (见 ``classify`` 的 force-down 分支)。
2. **DB/WV/LO 硬上限 < 34kt**: 见 ``CAP_KT``; 分类层直接拦住 "扰动 93kt"。
3. **EX 可达且不可逆**: 进入 EX 后不会退回 TS/TY (除非显式 ``retropical=True``),
   但可以继续减弱为 LO。

口径说明: 本仓库其余部分 (``app/utils.py`` / ``app/statistics/summary_list.py`` /
``app/ace_engine.py``) 早已把 ``MD/SS/SD/EX/LO`` 当作"非热带"、把 ``DB`` 单独
再排除一次 (``_EXCLUDED_TYPES``)。本模块的 ``TC_PHASES`` / ``NON_TROPICAL_TC`` /
``DISTURBANCE`` 与那套口径一致, 便于接线时不产生第二套定义。
"""
from __future__ import annotations

import math

__all__ = [
    'PHASE_CODES', 'PHASE_NAME_ZH', 'PHASE_NAME_EN',
    'TC_PHASES', 'NON_TROPICAL_TC', 'DISTURBANCE',
    'CAP_KT', 'WEAKEN_LADDER', 'GENESIS_PHASES', 'TD_FLOOR_KT', 'DB_KT',
    'MD_DOMAIN', 'SUBTROPIC_LAT', 'EX_LAT', 'KT_TS', 'KT_TY', 'KT_ST',
    'classify', 'classify_series', 'phase_spans', 'max_kt_for',
    'is_tropical_phase', 'grade_kt',
]

# ── 阶段码 ──────────────────────────────────────────────────────────────

#: 全部阶段码 (ATCF/JTWC 口径)
PHASE_CODES = ('DB', 'WV', 'LO', 'TD', 'TS', 'TY', 'ST', 'EX', 'SS', 'SD', 'MD')

PHASE_NAME_ZH = {
    'DB': '扰动', 'WV': '热带波', 'LO': '低压(残余槽)',
    'TD': '热带低压', 'TS': '热带风暴', 'TY': '台风', 'ST': '超强台风',
    'EX': '温带变性', 'SS': '副热带风暴', 'SD': '副热带低压',
    'MD': '季风低压',
}

PHASE_NAME_EN = {
    'DB': 'disturbance', 'WV': 'tropical wave', 'LO': 'low',
    'TD': 'tropical depression', 'TS': 'tropical storm', 'TY': 'typhoon',
    'ST': 'super typhoon', 'EX': 'extratropical', 'SS': 'subtropical storm',
    'SD': 'subtropical depression', 'MD': 'monsoon depression',
}

#: 有暖心 + 闭合环流的**热带气旋**相态 (ACE / 巅峰强度统计只认这几个)
TC_PHASES = ('TD', 'TS', 'TY', 'ST')

#: 非热带气旋相态 (与 app/utils.py 的 _NON_TROPICAL_TYPES 同口径: MD/SS/SD/EX/LO)
NON_TROPICAL_TC = ('MD', 'SS', 'SD', 'EX', 'LO')

#: 扰动类 (DB/WV; 与 app/utils.py 的 _EXCLUDED_TYPES = NON_TROPICAL_TC + DB 一起被排除)
DISTURBANCE = ('DB', 'WV')

#: 非热带气旋类别的强度上限 (kt) —— 超过就必须是热带/副热带系统
#:   DB: ATCF 扰动/热带波在 b-deck 里从不带热带风暴强度 (xrq 实测 max 30)
#:   WV: 同上 (xrq 实测 max 34, 只有 30 条)
#:   LO: 残余低压 (xrq 里没有 LO 记录, JTWC 口径同为热带风暴强度以下)
#:   MD: 季风低压可以到中等热带风暴强度 (xrq 实测 max 55), 但不会到台风级
#:   SS/SD: 副热带系统的强度顶是 50-60kt 级 (xrq 实测 SS max 65 / SD max 30)
#: **LO 的强度上限只是"业务惯例", 不是物理**(用户 2026-09-21 指出):
#: "如果不对称性过强是可以出现任意强度的 LO 的, 理论上 2024 普拉桑可以给 70kt LO
#:  (当然没人这么给), 只要风场不闭合"。LO 的物理含义是**槽/非闭合系统**, 与强度无关;
#: 33kt 是从 xrq 实测的业务惯例。所以: 默认仍按惯例 <34kt, 但当**结构明确已失**
#: (weak >= LO_UNCAPPED_WEAK)时**取消上限**, 让"高强度非闭合槽"能被表达出来。
LO_UNCAPPED_WEAK = 60.0
CAP_KT = {'DB': 30.0, 'WV': 33.0, 'LO': 33.0, 'MD': 55.0,
          'SD': 33.0, 'SS': 65.0}

#: **DB 与 WV 在本模型里怎么分**: 两者都是"无闭合环流的弱扰动",
#: 观测上靠闭合环流/组织度区分, 而我们的状态量里只有 ``weak`` (结构散乱计数) 与
#: 强度。约定: 尚能看出这是一个"环流中心"(有名字/编号, 或 ``weak`` 不大) 的弱系统
#: 记 **WV(热带波)**; 组织度已经散光 (``weak`` 很大) 或弱到风场都读不出来
#: (< ``DB_KT``) 的记 **DB(扰动)**。这只是一个约定, 接线的抽样统计不受影响。
DB_KT = 10.0

#: 强度分级阈值 (kt, 1 分钟持续风; JTWC/NHC 口径)
KT_TS = 34.0          # tropical storm 下限 (也就是"命名门槛")
KT_TY = 64.0          # typhoon 下限
KT_ST = 130.0         # super typhoon 下限 (西北太 130kt; 全球统一口径见 basin.nature_code)

#: 逐级减弱阶梯 (从强到弱): 每次调用最多往右挪一格。
#:   TS --(vmax < 34)--> TD --(vmax < 20)--> LO --> DB (组织度散光/极弱)
#: **不能跳过**: TS→DB 这种直接掉两格以上的请求会被拦成只降一格
#: (拦不动的时候 —— 目标档在该风速下根本判不出来 —— 才放行)。
WEAKEN_LADDER = ('ST', 'TY', 'TS', 'TD', 'LO', 'DB')

#: TD 的"兜底强度"(kt): vmax 掉到这里以下时, 已经掉了一档的 TD 还能**多留一报**
#: (用户: "TD 段必须真实存在(哪怕只有一两报)")。再往下掉才进 LO。
TD_FLOOR_KT = 20.0

#: "先发展后成台"的相态: 只在这些相态里才允许升格为 TD/TS/TY
GENESIS_PHASES = ('DB', 'WV', 'LO', 'MD')

#: MD(季风低压)的经纬域: 阿拉伯海/孟加拉湾/南海 (lon 可省, 只判纬度)
MD_DOMAIN = {'lat_min': 0.0, 'lat_max': 30.0, 'lon_min': 45.0, 'lon_max': 110.0}

#: 副热带系统 (SS/SD) 的纬度带 (|lat| 落在这里才允许判副热带)
SUBTROPIC_LAT = (20.0, 45.0)
#: 温带变性 (EX) 的纬度带
EX_LAT = 30.0

#: 各相态的**相态内**强度域 (kt)。给 force-down 做合法性检查:
#: 强制降档时若目标相态在该风速下非法 (例如 20kt 的 TD), 就不用强制结果。
#: 下限取"该相态还能被观测辨识"的门槛 —— 例如 12kt 的系统不该被强制叫 TD。
_PHASE_KT_DOMAIN = {
    'DB': (10.0, 30.0), 'WV': (10.0, 33.0), 'LO': (6.0, 33.0),
    'TD': (20.0, 33.0), 'TS': (KT_TS, 63.0), 'TY': (KT_TY, 129.0),
    'ST': (KT_ST, 200.0),
    'SD': (12.0, 33.0), 'SS': (KT_TS, 65.0), 'EX': (6.0, 200.0),
    'MD': (15.0, 55.0),
}


# ── 小工具 ──────────────────────────────────────────────────────────────

def _f(v, default=None):
    """安全取浮点 (None / NaN / 非数 → default)。"""
    if v is None:
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


def _clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)


def grade_kt(v):
    """纯强度档 (不含相态): 只看 vmax 的 TD/TS/TY/ST。"""
    v = _f(v, 0.0)
    if v >= KT_ST:
        return 'ST'
    if v >= KT_TY:
        return 'TY'
    if v >= KT_TS:
        return 'TS'
    return 'TD'


def max_kt_for(code):
    """该相态的强度上限 (kt)。非热带气旋类别 (DB/WV/LO) 一律 < 34kt。"""
    return CAP_KT.get(str(code).upper())


def is_tropical_phase(code):
    """是否"暖心的热带气旋"相态 (只认 TD/TS/TY/ST)。"""
    return str(code).upper() in TC_PHASES


def _in_kt_domain(code, v):
    lo, hi = _PHASE_KT_DOMAIN.get(code, (0.0, 999.0))
    return lo - 1e-9 <= v <= hi + 1e-9


def _legal_step(code, v, warm_core):
    """某个相态在该风速 + 该暖心状态下**是否判得出来**。

    逐级减弱的"强制降一档"必须过这一关, 否则会出现
    "40°N 上的冷心残余被强制降成 TD" 这种自相矛盾的结果。
    """
    if not _in_kt_domain(code, v):
        return False
    if code in TC_PHASES and not warm_core:
        return False                        # 热带气旋必须暖心
    if code in ('TD', 'TS') and v < TD_FLOOR_KT:
        return False                        # 弱风速迟滞: 不再挂 TD 标签
    if code in ('WV', 'DB') and v > 30.0:
        return False
    return True


def _closed_circulation(v, numbered, named, weak, cold_core=False):
    """闭合环流判据 (用于把 WV 热带波 与 TD/LO 分开)。

    · 已有低压编号/已命名 → 一定闭合;
    · 强度 >= 25kt (WP 的 TD 下限) 且未被组织结构判为散乱 → 闭合;
    · ``weak`` 是 core.py 已有的"结构退化"计数 (0 起步), 它大说明中心散乱;
    · **冷心**时不算热带闭合环流 —— 否则一个 25kt 的切断低压会被判成 TD。
    """
    if numbered or named:
        return True
    if cold_core:
        return bool(v >= 30.0 and weak < 30.0)      # 冷心: 要有相当强度才认闭合
    return bool(v >= 25.0 and weak < 30.0)


#: 相态家族 (温带 EX 自成一家: 变性不可逆的守卫要用)
_FAMILY = {'TD': 'tropical', 'TS': 'tropical', 'TY': 'tropical', 'ST': 'tropical',
           'SD': 'subtropic', 'SS': 'subtropic', 'EX': 'extratropical',
           'MD': 'monsoon', 'DB': 'genesis', 'WV': 'genesis', 'LO': 'genesis'}


def _family(code):
    return _FAMILY.get(code, 'genesis')


# ── 主分类器 ────────────────────────────────────────────────────────────

def classify(vmax, sst=None, lat=None, shear=None, land=0.0, age=0.0,
             dt_sst200=None, t200=None, previous=None, numbered=False,
             named=False, weak=0.0, front=False, disturb=False,
             lon=None, motion=None,
             pmin=None, rh=None, u850=None, retropical=False, di_minus=None,
             td_fresh=False, phase=None):
    """把当前状态判成一个 ATCF 阶段码。**纯函数**: 同样的输入永远同样的输出。

    参数
    ----
    vmax : 最大持续风速 (kt)            必需
    sst  : 海表温度 (°C)                缺失 → 无法判暖心 (按中性处理)
    lat  : 纬度 (度, 南半球为负)         缺失 → 不判副热/温带纬度域
    shear: 200-850hPa 风切 (kt)
    land : 陆地覆盖 0-1 (0=纯海洋)
    age  : 系统年龄 (h)
    dt_sst200 : 暖心判据 **SST - 200hPa 温度差 (K)**。热带气旋 ≥ 60K 左右,
          变性/冷心后迅速缩小。优先用它; 缺省时由 ``sst`` + ``t200`` 现算。
    t200 : 200hPa 温度 (K 或 °C, <150 视为 °C) —— 没有 dt_sst200 时的兜底
    previous : 上一报的阶段码 (转移规则要用; 传 None 表示"第一报")
    numbered : 是否已有低压编号 (cy) —— 我们的模型里"编号"与"命名"是两件事
    named    : 是否已被命名
    weak     : core.py 的"结构退化计数"(>20 视为结构已散)
    front    : 是否有锋面接入/锋生 (EX 的直接证据)
    disturb  : 这个系统是不是内核认定的**独立扰动**(`tc['disturb']`, 永不发展)。
               **2026-09-23(轮次 114)新增**: ATCF/自评 的实际用法是
               **DB(扰动)是弱系统的主类** —— 实测 DB 20.6-23.7% vs WV 1.3-4.3%
               (DB 是 WV 的 5-18 倍); 而这里原来"强度一过 10kt 且结构没散光就判 WV",
               把两者判反了(模型 DB 6.9% / WV 10.3%)。内核**本来就有**扰动标记,
               直接用它: 纯扰动一路记 DB, WV 留给"非扰动、未闭合、还没热带化"的少数系统。
    lon      : 经度 (0-360) —— MD 的域判据要用
    motion   : 移速 (kt) —— 大范围风场判据的辅助量
    retropical : 显式允许 EX → 热带气旋的再热带化 (默认不可逆)
    td_fresh : 本报是否**刚从热带档落进 TD** (TS/TY → TD 的第一报)。``True`` 时
               TD 可以呆在 TD_FLOOR_KT 以下 (保证 TD 段真的出现一两报)。
               ``classify_series`` 会自己维护它; 手工调用时按上面这句话传。
    phase    : 被检查的相态 (可选)。给了就检查"这个相态在该强度下合法吗" ——
               例如 ``phase='DB', vmax=93`` 会返回 ``over_cap=True`` 并把
               ``capped_from='DB'``, 用来在**赋值前**拦住"扰动 93kt"。

    返回
    ----
    dict:
      phase      阶段码 (PHASE_CODES 之一)
      confidence 置信说明 (人类可读的一句话)
      reasons    命中的判据列表
      tropical   是否暖心热带气旋 (等价 is_tropical_phase)
      cap_kt     该相态的强度上限 (DB/WV/LO 是 < 34kt)
      over_cap   本报 vmax 是否**超出该相态上限** (分类层拒绝越界赋值)
      capped_from 因越界被换掉的原相态 (没有就是 None)
      warmth     暖心度 0-1
      cold_core / warm_core / weak_warm_core / subtropical 布尔判据
      ladder      是否被逐级减弱阶梯拦下 (便于诊断"为什么没一步掉到 DB")
    """
    v = _f(vmax, 20.0)
    sst_v = _f(sst, None)
    lat_v = _f(lat, None)
    shear_v = _f(shear, 0.0)
    age_v = max(0.0, _f(age, 0.0))
    weak_v = _f(weak, 0.0)
    lon_v = _f(lon, None)
    # pmin / rh / u850 / motion / land 目前不参与判据, 但保留在签名里 —— 接线方
    # 以后要给"大范围风场"(SS)与"季风低压"(u850 西风)加判据时不用改调用点。
    prev = str(previous).upper() if previous else None
    if prev is not None and prev not in PHASE_CODES:
        prev = None
    want = str(phase).upper() if phase else None
    if want is not None and want not in PHASE_CODES:
        want = None
    numbered = bool(numbered)
    named = bool(named)
    abslat = abs(lat_v) if lat_v is not None else None

    reasons = []

    # ── 1. 暖心度 (0=冷心, 1=典型热带暖心) ──
    dT = _f(dt_sst200, None)
    if dT is None and sst_v is not None and _f(t200, None) is not None:
        _t200 = _f(t200)
        _t200_c = (_t200 - 273.15) if _t200 > 150.0 else _t200
        dT = sst_v - _t200_c
    if dT is not None:
        # 标定: 观测里热带气旋的 SST-T200 约 45-62K (强台风 55-65K),
        # 变性/冷心后落到 20-40K。取 28K→0 / 60K→1, 于是 50K 才刚过 0.6。
        warmth = _clamp((dT - 28.0) / 32.0, 0.0, 1.0)
        reasons.append('SST-T200=%.0fK → 暖心度 %.2f' % (dT, warmth))
    else:
        warmth = 0.5                                      # 无观测: 中性
        reasons.append('无 SST-T200 → 暖心度按中性 0.50')

    # 干冷空气 / 变形场: 有锋面或极强切变时暖心度打折
    if front:
        warmth -= 0.25
        reasons.append('锋面接入 → 暖心度 -0.25')
    if shear_v > 25.0:
        warmth -= min(0.15, (shear_v - 25.0) / 100.0)
        reasons.append('强切变 %.0fkt → 暖心度打折' % shear_v)
    warmth = _clamp(warmth)

    cold_core = bool(front or warmth < 0.35)
    # **2026-09-22(轮次 104)修: 显式锋面/温带证据优先于这个会饱和的代理。**
    # `warmth` 用的是 SST − 200hPa 温度的**绝对值**: 热带气旋 45-65K, 而中纬度
    # 即使海温只有 22-25°C, 对流层顶 −55°C 也让 dT 高达 **70-80K** ⇒ 按
    # (dT−28)/32 归一后**恒等于 1.0**, 于是"高纬依旧暖心"永远成立。
    # 实测后果(轮次 104, `tools/probe_et_gate.py`, 90 天): 内核的物理变性分支
    # 已经把 **2552 步**判成 `cuClass='EX'`(另有 SS 824 步), 但权威阶段码里
    # **EX / SS / SD 一个都没有** —— 因为这里把它们全挡回热带档, 再被 LO 阶梯吃掉。
    # 这不是"变性没发生", 是判据自锁。
    # `front` 由内核的 ET 分支给出(冷海 + 强切变 + 仍强, 持续 ET_HOURS), 属于**物理证据**;
    # 代理量饱和时应该听证据, 而不是听饱和的代理。
    warm_core = bool(warmth >= 0.60 and not front)
    weak_warm_core = bool(0.35 <= warmth < 0.60) or bool(front and warmth >= 0.60)
    if cold_core and not front:
        reasons.append('暖心度 %.2f < 0.35 → 冷心' % warmth)
    if warm_core:
        reasons.append('暖心度 %.2f ≥ 0.60 → 暖心中' % warmth)

    # 副热带域
    in_subtropic = bool(abslat is not None and SUBTROPIC_LAT[0] <= abslat <= SUBTROPIC_LAT[1])
    subtropical = bool(weak_warm_core and in_subtropic)
    if subtropical:
        reasons.append('弱暖心 + |lat|=%.0f° 在 %d-%d° 副热带带 → 副热带系统'
                       % (abslat, SUBTROPIC_LAT[0], SUBTROPIC_LAT[1]))

    # ── 2. 自然相态 (只看环境/结构, 不看上一报) ──
    in_md = (lat_v is not None and MD_DOMAIN['lat_min'] <= lat_v <= MD_DOMAIN['lat_max']
             and (lon_v is None
                  or MD_DOMAIN['lon_min'] <= (lon_v % 360.0) <= MD_DOMAIN['lon_max']))
    # EX 的前提: 高纬 + 冷心 + (锋面/强切变/冷海)。**暖心系统不判 EX** ——
    # 否则一个仍暖心的系统只要被推过 30° 就会被误判成温带变性。
    ex_ok = bool(
        abslat is not None and abslat >= EX_LAT
        and (cold_core or front)
        and not warm_core
        and (front or shear_v >= 20.0 or (sst_v is not None and sst_v < 24.0)))
    if ex_ok:
        reasons.append('|lat|=%.0f° + 冷心 + 锋面/切变%.0fkt/冷海 → 温带变性条件成立'
                       % (abslat, shear_v))

    closed = _closed_circulation(v, numbered, named, weak_v, cold_core)
    if not closed:
        reasons.append('强度 %.0fkt 且未编号 → 无闭合环流' % v)

    # 判据顺序就是**相态的优先级**: 温带变性 > 季风低压 > 副热带 > 热带 > 残余/扰动。
    # 顺序错了会出现"40kt 的温带气旋被判成 TS"这类错误 (vmax 先命中热带档)。
    if ex_ok:
        natural = 'EX'
    elif in_md and 15.0 <= v <= CAP_KT['MD'] and (not warm_core) and age_v >= 12.0:
        natural = 'MD'
        reasons.append('季风域(lat=%.0f lon=%s) + 大尺度弱暖心 + age %.0fh → MD'
                       % (lat_v, ('%.0f' % lon_v) if lon_v is not None else '-', age_v))
    elif in_subtropic and not warm_core and 20.0 <= v <= CAP_KT['SS']:
        # 副热带带内的弱暖心系统: 还没到大风是 SD, 到了就是 SS (xrq 实测 SS 30-65kt)
        natural = 'SS' if v >= KT_TS else 'SD'
        reasons.append('|lat|=%.0f° + 弱暖心 + %.0fkt → %s' % (abslat, v, natural))
    elif v >= KT_TS:
        natural = grade_kt(v)
    elif warm_core and closed:
        natural = 'TD'                          # 暖心 + 闭合环流 = 热带低压
    elif not closed and not named and not numbered:
        # 无闭合环流的弱扰动: 强度还在 → WV(热带波);
        # **已经热带气旋化过的系统**或弱到风场都读不出 (< DB_KT) → DB(扰动)。
        # (这样能保证 TS 减弱时不会一步掉到 DB —— 那正是用户报的缺陷。)
        natural = 'DB' if (weak_v >= 45.0 or v < DB_KT or disturb
                           or _family(prev) == 'tropical') else 'WV'
    else:
        natural = 'LO' if (closed or named or numbered) else 'DB'

    # 已命名/已编号的系统不该被判成"未编号的波/扰动"
    if (named or numbered) and natural in ('DB', 'WV'):
        natural = 'LO'
        reasons.append('已命名/已编号 → 不判 (DB/WV), 落 LO')

    reasons.append('自然相态 = %s (vmax %.0fkt)' % (natural, v))

    # ── 3. 相态升级 (DB/WV/LO/MD 出身发展成热带气旋) ──
    phase = natural
    if prev in GENESIS_PHASES:
        if prev == 'MD':
            if v >= 40.0 or not (lat_v is not None and lat_v <= MD_DOMAIN['lat_max']):
                phase = grade_kt(v)
                reasons.append('MD 出身 + 强度/位置越出季风域 → 升格 %s' % phase)
        elif prev == 'LO':
            # 残余低压重回暖池再发展 (core.py 原本就允许 LO→TC); 逐级: LO→TD→TS。
            # 门槛取 TD 的兜底强度 —— 否则 17kt 会在 LO/TD 之间来回跳
            # (LO 的升级线与 TD 的降级线必须留出迟滞)。
            if warm_core and v >= TD_FLOOR_KT:
                phase = grade_kt(v)
                reasons.append('LO 出身 + 暖心恢复 + %.0fkt → 升格 %s' % (v, phase))
        elif prev in ('DB', 'WV') and warm_core and closed and v >= 20.0:
            phase = grade_kt(v)
            reasons.append('(DB/WV) 出身 + 暖心闭合 + %.0fkt → 升格 %s' % (v, phase))

    # ── 4. EX 不可逆 (除非显式 retropical) ──
    _retro = bool(retropical and warm_core and v >= 45.0)
    if _retro and prev == 'EX' and phase != 'EX':
        # 再热带化也要逐级: EX → TD → TS, 不许一步回到 TS/TY
        reasons.append('retropical: EX 热心恢复, 先回到 TD')
        phase = 'TD'
    elif prev == 'EX' and phase != 'EX':
        reasons.append('EX 不可逆: 拒绝 %s → 保持 EX' % phase)
        phase = 'EX'

    # ── 5. 相态家族守卫 + 逐级减弱阶梯 ──
    # 5a. 家族守卫: 副热带/温带系统的**弱暖心**状态不许"变回"热带气旋。
    #     否则一个 SD 只要 vmax 被环境推到 34kt 就会被贴成 TS, SS/SD 就成了随机标签。
    #     显式 retropical=True 时才放行 (再热带化)。逐级: SD → TD → TS。
    ladder_hit = False
    forced = False                              # 是否被"家族守卫"强制改成了 SD
    if prev in ('SS', 'SD', 'EX') and phase in TC_PHASES and not _retro:
        if prev == 'EX':
            reasons.append('家族守卫: EX 的冷心状态不直接回 %s, 保持 EX' % phase)
            phase = 'EX'
        elif _in_kt_domain('SD', v) and not warm_core:
            # 弱暖心不足以一次跨到热带气旋 → 退回副热带低压 (SD 的强度域内)
            reasons.append('家族守卫: %s 的弱暖心不足以升格 %s → 先落 SD'
                           % (prev, phase))
            phase = 'SD'
            forced = True
        elif v >= KT_TS and prev == 'SS':
            # 强度已在副热带风暴档: 结构上并没有变得更冷, 就不该降到 SD 再爬回来
            reasons.append('家族守卫: %s 保持 SS (%.0fkt 已达副热带风暴档)' % (prev, v))
            phase = 'SS'
        else:
            reasons.append('家族守卫: %s→%s 不合理 → 回到 %s' % (prev, phase, prev))
            phase = prev
    elif prev == 'TS' and phase == 'SS':
        # 热带风暴变性: 结构上先经过副热带低压, 不许一步跳到 SS
        ladder_hit = True
        forced = True
        reasons.append('家族守卫: TS 变性须先经 SD (不直接给 SS)')
        phase = 'SD'

    # 5b. 逐级减弱阶梯 (只约束**热带阶梯内部**): 一次只允许掉一档。
    if prev is not None and prev in WEAKEN_LADDER and phase in WEAKEN_LADDER:
        i_prev = WEAKEN_LADDER.index(prev)
        i_now = WEAKEN_LADDER.index(phase)
        # 只拦"往下掉": 增强方向由相态升级/自然判定负责 (例如 TY→TS 掉一档是合法的)
        if i_now - i_prev >= 2:
            step = WEAKEN_LADDER[i_prev + 1]
            if _legal_step(step, v, warm_core):
                ladder_hit = True
                reasons.append('逐级减弱: %s→%s 跨 %d 档, 本步只降到 %s'
                               % (prev, phase, i_now - i_prev, step))
                phase = step
            else:
                reasons.append('%s 在 %.0fkt 下非法 → 不做逐级拦截' % (step, v))

    # 5c. 弱风速的**迟滞**: TD 与 LO 之间要留迟滞带 (TD_FLOOR_KT), 否则系统会在
    #     "18kt 判 LO → 17kt 又判回 TD"之间反复跳。
    #       · 上一报已经是残余类 (LO/WV/DB) → 涨回 TD_FLOOR_KT 之前不再升回 TD;
    #       · 上一报已经是热带档 → 掉到 TD_FLOOR_KT 以下就让位给 LO。
    if phase == 'TD' and v < TD_FLOOR_KT:
        if td_fresh:
            pass                                  # 刚降到 TD: 允许在兜底以下多留一报
        elif prev in ('LO', 'WV', 'DB') or prev == 'TD':
            phase = 'LO' if (closed or named or numbered) else 'WV'
            ladder_hit = True
            reasons.append('弱风速迟滞: %.0fkt < TD 兜底 %.0fkt 且上一报 %s → %s'
                           % (v, TD_FLOOR_KT, prev, phase))
    # 5d. 风场下限: 15kt 以下谈不上热带低压/风暴, 一律落残余低压 (LO) / 波 (WV)。
    if v < 16.0 and phase in TC_PHASES:
        _was = phase
        phase = 'LO' if (closed or named or numbered) else 'WV'
        ladder_hit = True
        reasons.append('vmax %.0fkt < 16kt → %s(不能维持 %s)' % (v, phase, _was))

    # ── 5e. **结构失稳 → 残余低压(LO)**: 允许 TS/TY 直接落到 LO ──
    # 观测锚点(2,410 个 xrq/自评文件): 进入 LO 的前一码 = TD 15 / DB 5 / **TS 2** / SD 1,
    # LO 起始强度中位 20kt、96% <34kt。即"登陆或入冷海、暖心消失"时, 机构会在风还有
    # 30-34kt 时就把它降级成残余低压, **不等风速掉到 20kt**; 而阶梯(WEAKEN_LADDER)强制
    # TS→TD→LO, 从结构上禁止了这条路径(实测 classify(vmax=34..40, land=0.8) 一律返回 TS)。
    # 这里按"结构已失"的证据开一个显式例外。
    _lost_strong = weak_v >= LO_UNCAPPED_WEAK          # 结构明确已失(不闭合)
    if phase in TC_PHASES or phase in ('SS', 'SD'):
        _lost = ((land is not None and float(land) >= 0.6)
                 or (sst_v is not None and float(sst_v) < 24.0)
                 or _lost_strong)
        # 惯例档: v<=33; 但结构明确已失时可**任意强度**(见 LO_UNCAPPED_WEAK 的说明)
        if _lost and (v <= 33.0 or _lost_strong):
            _was5 = phase
            phase = 'LO'
            ladder_hit = True
            reasons.append('结构失稳(登陆/冷海/退化) ⇒ %s → LO(观测里 TS→LO 确有发生)' % _was5)

    # ── 6. 非热带气旋类别的强度硬上限 (DB/WV/LO < 34kt) ──
    # 分类层就把"扰动 93kt"这类越界请求拦掉: 换成同强度下的合法热带档。
    #   ① 调用方若显式声明 ``phase='DB'`` (被"分类"的相态), 先查它是否越界;
    #   ② 再查本函数自己判出来的相态是否越界 (DB/WV/LO 的判据本身已保证不越界,
    #      这一层是给"上一报传进来的非法状态"兜底)。
    over_cap = False
    capped_from = None
    _lost_strong = bool(globals().get('_NO_OP')) or ('_lost_strong' in dir() and _lost_strong)
    _check = want if want is not None else phase
    _cap = CAP_KT.get(_check)
    if (_check == 'LO' and _lost_strong):
        # 非闭合槽没有物理上限 —— 允许"70kt 的 LO"这种极端形态(用户明确要求)
        reasons.append('LO 结构已失: 不套惯例上限(可任意强度)')
    elif _cap is not None and v > _cap + 1e-9 and not forced:
        # 分类层把"扰动 93kt"这类越界请求拦掉: 换成同强度下的合法热带档。
        # 被"家族守卫"强制降成的 SD 不做这道检查 (那是结构判据的结果)。
        over_cap = True
        capped_from = _check
        alt = grade_kt(v)
        reasons.append('强度上限拦截: %s 上限 %.0fkt < %.0fkt → 改判 %s'
                       % (_check, _cap, v, alt))
        phase = alt
        ladder_hit = False
    elif want is not None and want != phase:
        # 调用方声明的相态合法, 但与本函数的自然判定不同 → 以自然判定为准
        reasons.append('声明相态 %s 与判据不符 (自然 %s) → 用 %s' % (want, phase, phase))

    # ── 7. 组装 ──
    result = {
        'phase': phase,
        'confidence': '; '.join(reasons) if reasons else '无判据',
        'reasons': reasons,
        'tropical': is_tropical_phase(phase),
        'cap_kt': CAP_KT.get(phase),
        'over_cap': over_cap,
        'capped_from': capped_from,
        'warmth': round(warmth, 4),
        'cold_core': cold_core,
        'warm_core': warm_core,
        'weak_warm_core': weak_warm_core,
        'subtropical': subtropical,
        'ladder': ladder_hit,
        'previous': prev,
    }
    return result


# ── 逐时次序列 ──────────────────────────────────────────────────────────

_T_KEYS = ('t', 'time', 'hour', 'h')


def _t_of(rec, i, dt):
    if isinstance(rec, dict):
        for k in _T_KEYS:
            if k in rec and rec[k] is not None:
                return _f(rec[k], i * dt)
        return i * dt
    return i * dt


def _fields_of(rec):
    """把一条 record 归一成 classify 的关键字参数。

    支持 dict (键名同 classify) 或序列 (按 ``_TUPLE_ORDER`` 顺序)。
    """
    if isinstance(rec, dict):
        return {
            'vmax': rec.get('vmax', rec.get('w', rec.get('wind', 0.0))),
            'sst': rec.get('sst'),
            'lat': rec.get('lat'),
            'shear': rec.get('shear'),
            'land': rec.get('land', 0.0),
            'age': rec.get('age', 0.0),
            'dt_sst200': rec.get('dt_sst200'),
            't200': rec.get('t200'),
            'numbered': rec.get('numbered', rec.get('cy') is not None),
            'named': rec.get('named', False),
            'weak': rec.get('weak', 0.0),
            'front': rec.get('front', False),
            'disturb': rec.get('disturb', False),
            'lon': rec.get('lon'),
            'motion': rec.get('motion'),
            'pmin': rec.get('pmin'),
            'rh': rec.get('rh'),
            'u850': rec.get('u850'),
            'phase': rec.get('phase'),
        }
    # 序列: vmax 必需, 其余按位置
    vals = list(rec)
    out = {'vmax': vals[0] if len(vals) > 0 else 0.0,
           'sst': vals[1] if len(vals) > 1 else None,
           'lat': vals[2] if len(vals) > 2 else None,
           'shear': vals[3] if len(vals) > 3 else None,
           'land': vals[4] if len(vals) > 4 else 0.0,
           'age': vals[5] if len(vals) > 5 else 0.0}
    return out


def classify_series(records, dt_h=6.0, **fixed):
    """对逐时次记录**按时间顺序**跑 classify, 自动接续 ``previous``。

    ``records``: 每条是 dict (键名同 classify) 或序列 ``(vmax, sst, lat, shear, land, age)``。
    ``fixed``: 对全部时次生效的常量 (例如 ``numbered=True``)。
    返回 ``[{'t':…, 'phase':…, 'over_cap':…, 'ladder':…, 'confidence':…}, …]``。
    """
    out = []
    prev = fixed.pop('previous', None)
    for i, rec in enumerate(records):
        kw = _fields_of(rec)
        kw.update({k: v for k, v in fixed.items() if k not in kw or kw[k] is None})
        # 逐时次自己维护"本报是不是**刚**从热带档落进 TD 的那一报":
        # 只有 TS/TY 落到 TD 的**第一条**记录算 fresh (允许它在兜底强度以下多留一报);
        # 之后 prev 已是 TD, 或者从残余类 (LO/WV/DB) 升回 TD, 都不算。
        if kw.get('td_fresh') is None:
            kw['td_fresh'] = bool(prev is not None
                                  and _family(prev) == 'tropical' and prev != 'TD')
        r = classify(previous=prev, **kw)
        out.append({'t': _t_of(rec, i, dt_h), 'phase': r['phase'],
                    'over_cap': r['over_cap'], 'capped_from': r['capped_from'],
                    'ladder': r['ladder'],
                    'vmax': _f(kw.get('vmax'), 0.0), 'confidence': r['confidence']})
        prev = r['phase']
    return out


def phase_spans(records, dt_h=6.0, merge_gap_h=0.0, **fixed):
    """逐时次记录的**阶段持续时长**与出现顺序。

    参数
    ----
    records   : 同 ``classify_series``
    dt_h      : 没有 ``t`` 字段时相邻记录的间隔 (h)
    merge_gap_h : 同一阶段被打断后再次出现时, **两段间隔 ≤ 该值** 就合并成一段
                  (默认 0 → 不合并, 每段独立列出; 见返回值的 ``merged`` 标记)
    fixed     : 传给 classify 的常量

    返回
    ----
    dict:
      spans   : 按时间序的段列表, 每段 ``{phase, t0, t1, hours, n, idx0, idx1, merged}``
      totals  : ``{phase: 累计小时}``
      counts  : ``{phase: 段数}``
      order   : 阶段**首次出现**的顺序 (去重)
      timeline: ``[phase, …]`` 逐时次阶段码
      step_h  : 使用的步长
    """
    steps = classify_series(records, dt_h=dt_h, **fixed)
    spans = []
    for i, s in enumerate(steps):
        t0 = s['t']
        t1 = steps[i + 1]['t'] if i + 1 < len(steps) else t0 + dt_h
        hours = max(0.0, float(t1) - float(t0))
        # "同一阶段被打断后再次出现": 往回找最近一段同名阶段, 间隔 ≤ merge_gap_h
        # 就并回那一段 (只累加时长与计数, 中间被别的阶段占用的时间不算在内)。
        found = None
        if merge_gap_h > 0:
            for cand in reversed(spans):
                if cand['phase'] == s['phase']:
                    found = cand if float(t0) - float(cand['t1']) <= float(merge_gap_h) + 1e-9 else None
                    break
        elif spans and spans[-1]['phase'] == s['phase']:
            found = spans[-1]
        if found is not None:
            found['t1'] = t1
            found['hours'] += hours
            found['n'] += 1
            found['idx1'] = i
            found['merged'] = True
            continue
        spans.append({'phase': s['phase'], 't0': t0, 't1': t1, 'hours': hours,
                      'n': 1, 'idx0': i, 'idx1': i, 'merged': False})

    totals, counts, order = {}, {}, []
    for sp in spans:
        ph = sp['phase']
        totals[ph] = totals.get(ph, 0.0) + sp['hours']
        counts[ph] = counts.get(ph, 0) + 1
        if ph not in order:
            order.append(ph)
    return {'spans': spans, 'totals': totals, 'counts': counts,
            'order': order, 'timeline': [s['phase'] for s in steps],
            'step_h': dt_h}
