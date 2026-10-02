# app/ty_sim_mixins/sim_mode_mixin.py
"""模拟模式(MODE_SIM): 完全移植 SimCore 模拟器核心(环境场/TC动力学/云场/集合)。

- 核心: simulator.simcore.core(0.5° 环境场 + 10min 积分步 + 半拉格朗日云场 + 12成员集合)
- 渲染: simulator.simcore.render 各图层叠加到现有地图上(不再覆盖地图)
- 交互: 左键点击生成/选中台风, 空格播放, 数字键图层, 速度档按钮
"""
from __future__ import annotations

import logging
import math
import os
import json
import time
from datetime import datetime
from typing import List, Optional

import numpy as np
import pygame

from app.constants import f_s, rt
# 轮次 156: 信息框的宽度/位置改用风季的常量(原来是硬编码 470 与 (15,244))
from app.constants.layout import (SEASON_INFO_BOX_WIDTH,
                                  SEASON_INFO_BOX_START_X,
                                  SEASON_INFO_BOX_START_Y)

logger = logging.getLogger(__name__)

# 图层: (id, 名称, 快捷键) — 已去掉云图图层
SIM_LAYERS = [
    ('pressure', '气压',      '1'),
    ('wind',     '风场',      '2'),
    ('sst',      '海温',      '3'),
    ('ohc',      '热含量',    '4'),
    ('track',    '路径',      '5'),
    ('ridge',    '副高',      '6'),
    ('shear',    '风切',      '7'),
    ('flow',     '环流',      '8'),
    ('current',  '洋流',      '9'),
]
SIM_LAYER_IDS = [lid for lid, _, _ in SIM_LAYERS]

# 速度档(×): 与全局速度条共用 self.sp(0.1-10), 档位按钮循环
SIM_SPEEDS = [0.5, 1.0, 2.0, 4.0, 8.0]

#: 速度基准(**用户 2026-09-19 定**): 1× = 每秒推进 6 模拟小时, 也就是
#: "一个 6h 步正好 1 秒"。旧定义是"1× = 每秒 6 个积分步"(6h 步时 = 36 h/s),
#: 用户实测反馈"1 倍速下 6 个小时一瞬间就过去了, 不应该是 1s 吗" —— 已改。
#: 与步长解耦: 不管环境步长取 1/3/6h, 1× 永远是 6 模拟小时/实时秒。
SIM_HOURS_PER_SEC_AT_1X = 6.0

#: 单帧允许花在**推进模拟**上的墙钟上限(ms)。2s/5s 档一个 6h 步要 2-5 s, 靠
#: core.sim_step(max_inner=1) 把内步摊到多帧, 每帧 1-2 个内步(≈100-200 ms),
#: 这样 UI 一直有响应; 模拟钟推进的总量与顺序不变(见 _sim_advance)。
SIM_FRAME_BUDGET_MS = 200.0

#: 单帧最多跑几个内步(估算失效时的硬闸, 防"一帧几百步"把界面打死)。
SIM_MAX_UNITS_PER_FRAME = 16


def _fmt_vmax(v: float, mode: str = '1') -> str:
    """风速显示精度: 5 / 1(默认) / 0.1 kt。**内部始终是 float。**"""
    m = str(mode)
    if m == '5':
        return '%.0f' % (round(float(v) / 5.0) * 5)
    if m == '0.1':
        return '%.1f' % float(v)
    return '%.0f' % round(float(v))


def _fmt_pmin(pp: float, mode: str = '1') -> str:
    """气压显示精度: 0258(尾数只取 0/2/5/8) / 1(默认) / 0.1 hPa。"""
    m = str(mode)
    pp = float(pp)
    if m == '0.1':
        return '%.1f' % pp
    if m == '0258':
        # 找最接近的"末位为 0/2/5/8"的整数
        base = int(round(pp))
        cand = [base + d for d in (-3, -2, -1, 0, 1, 2, 3)]
        ok = [c for c in cand if abs(c) % 10 in (0, 2, 5, 8)]
        if ok:
            return '%d' % min(ok, key=lambda c: abs(c - pp))
        return '%d' % base
    return '%.0f' % round(pp)


class _SimIconTy:
    """模拟模式台风的轻量图标对象: 模拟 Typhoon 的图标接口
    (ra/sa/mirror/get_rotated_ring/_smcy_frame), 使图标渲染完全复用
    其它模式的 _draw_simple_icon / _draw_smcy_frame 管线(自旋动画/滤镜/半球)。"""

    __slots__ = ('ra', 'sa', 'sa3', 'sa4', 'sa5', 'mirror', 'v',
                 '_smcy_frame', '_smcy_last_cat', '_smcy_last_ticks', '_cache')

    def __init__(self, mirror: bool = False):
        self.ra = 0.0
        self.sa = 0.0
        self.sa3 = self.sa4 = self.sa5 = 0.0
        self.mirror = mirror
        self.v = self
        self._smcy_frame = 0
        self._smcy_last_cat = ''
        self._smcy_last_ticks = 0
        self._cache = {}

    def get_rotated_ring(self, key, base_ring, angle, mirror, tint=None):
        ck = (key[0], key[1], key[2], int(round(angle % 360)), bool(mirror), tint)
        s = self._cache.get(ck)
        if s is None:
            s = base_ring
            if angle % 360 > 0.5:
                s = pygame.transform.rotate(s, angle % 360)
            if len(self._cache) > 240:
                self._cache.pop(next(iter(self._cache)))
            self._cache[ck] = s
        return s

    def get_rotated_level3_ring(self, key, base_ring, angle, mirror, tint=None):
        return self.get_rotated_ring(key, base_ring, angle, mirror, tint)


def _no_icon(surface, tc, x, y, sel) -> None:
    """占位 icon_fn: 模拟模式的图标交给 draw_typhoon_info(其它模式那套)画,
    render_map 的 HUD 只需要画风圈/四象限环/高亮, 不要再画一个图标。"""
    return None


class _SimPathV:
    """路径渲染读取的视图字段(最小集)。

     →  会读
     / ; 缺任一个就抛异常, 而调用点
    是 except 包住的 → **路径静默不画**(用户 2026-09-19: "台风没有过去的轨迹")
    """
    __slots__ = ('smooth_screen_points', 'smooth_map_points',
                 '_smooth_arc_lengths', '_sp_ver')

    def __init__(self):
        self.smooth_screen_points = None
        self.smooth_map_points = None
        self._smooth_arc_lengths = None
        self._sp_ver = 0


class _SimPathTy:
    """模拟模式台风的轻量路径对象: 完全复用其它模式的点阵/渐变线路径渲染
    (_render_path_to_surface: 半透明未来段 + 不透明已走段 + 点阵标记)。"""

    __slots__ = ('pts', 'ci', 'v', 'screen_points',
                 '_path_cache_key', '_path_cache_full', '_path_cache_traversed',
                 '_path_cache_blit', '_last_rendered_ci',
                 '_path_cache_partial', '_path_cache_origin',
                 '_path_cache_sp0', '_path_cache_spL')

    def __init__(self):
        self.pts = []
        self.ci = 0
        self.v = _SimPathV()
        self.screen_points = []
        self._path_cache_key = None
        self._path_cache_full = None
        self._path_cache_traversed = None
        self._path_cache_blit = (0, 0)
        self._last_rendered_ci = -1
        self._path_cache_partial = False
        self._path_cache_origin = (0, 0)
        self._path_cache_sp0 = None
        self._path_cache_spL = None


class SimModeMixin:
    """模拟模式逻辑。挂载到 TySim。"""

    # ── 初始化 ──
    def _init_sim_mode(self) -> None:
        self.sim_layer = 'track'           # 当前图层
        self.sim_playing = False
        self.sim_layers = {'track'}
        self.sim_cloud_mode = 'IR'
        # 显示/开关类设置的**权威来源**是应用属性(镜像 cfg, 跨会话记住)。
        # 见 _SIM_DISPLAY_KEYS 与 _sim_toggle_natural_gen/_sim_toggle_data_mode。
        self.sim_natural_gen = bool(getattr(self.cfg, 'sim_natural_gen', True))
        # 种子也是 cfg 字段(面板可改) —— 这里**不要**写死 20250801,
        # 否则每次启动都会把用户存的种子覆盖掉。有旧配置缺字段时用默认值兜底。
        if getattr(self.cfg, 'sim_seed', None) in (None, ''):
            self.sim_seed = 20250801
        # F1: 绝对模拟起始时刻(可设任意年份 1700-2300)与环境场重建步长。
        # 默认 **1997-09-16 12z** —— 用户 2026-09 拍板: 1997 的模态与今年相近,
        # 而模态索引表在 1997 有完整实测(2026 最新只到 08, 只能 AR 外推)。
        # 运行时起始时刻用**独立属性**: sim_start 是 cfg 字段(描述符代理),
        # 在描述符安装前给它赋值会被实例字典里的同名值"屏蔽"(读回来还是 cfg),
        # 而 cfg 里存的是字符串/空串(空=自动)。见 _coerce_start。
        # ⚠ 修复10: 初值改为 None(惰性), 由 `_sim_ensure_kernel()` 首次进入模拟
        #   模式时填上 —— 原来这里写死 datetime(2026,3,17) 只是占位, 下面立刻被
        #   `_coerce_start()` 的结果覆盖, 而那个调用要 import 内核。
        self._sim_start_dt = None
        self.sim_env_step_h = 6.0
        self.sim_mode_override = {}
        # 从配置恢复(跨会话记住起始时刻 / 步长 / 模态覆盖)。
        # ⚠⚠ 2026-10-02(W1 修复10): **构造期不再 import SimCore 内核**。
        #   原来这里直接调 `self._coerce_start(...)`(→ `parse_start`/`latest_real_start`)
        #   与 `from simulator.simcore.core import snap_env_step`, 下面还有一处
        #   `from simulator.simcore import render` 注入 `set_label_fn`。
        #   后果: 用户**不进模拟模式**也要在启动时付内核的 import + 读模态 npz
        #   (实测 `_init_sim_mode` = 675 ms, 未绑核 1251 ms; 构造后
        #   `simcore.core`/`simcore.render` 都出现在 sys.modules 里)。
        #   现在: 构造期只做**内核无关**的状态初始化; 上面三件事推迟到
        #   `_sim_ensure_kernel()`(首次真正进入模拟模式时调用, 幂等)。
        _cfg_start = getattr(self.cfg, 'sim_start', None)
        # 一次性迁移: 用户 2026-09-19 明确说"默认是 1850 年. 应该是当前时间" ——
        # 把这个具体值当作"没设过", 回到自动(最新实测); 其它值(1997/2099 等
        # 明确选过的)照旧尊重。
        if str(_cfg_start or '').strip().startswith('1850'):
            logger.info('sim_start=%r 视为未设置, 回到自动(最新实测)', _cfg_start)
            try:
                self.cfg.sim_start = ''
                self._config_needs_save = True
            except Exception:
                pass
            _cfg_start = ''
        self._sim_start_dt = None          # 惰性: 见 sim_start_dt / _sim_ensure_kernel
        self._sim_kernel_ready = False     # 内核 import 是否已完成
        # 环境步长: 构造期**不做**核内的 snap_env_step(那要 import 内核) ——
        # 先取配置原值, 首次进模拟模式时由 _sim_ensure_kernel() 吸附到 ENV_STEPS。
        # 吸附前该值只被"模拟设置 / 模拟起始时间"两个面板读
        # (sim_settings_dialog 的 _get('_env_step') 与 sim_time_dialog 的档位高亮),
        # 而那两个面板**只能在模拟模式里打开** ⇒ 到那时必然已吸附, 行为与改动前一致。
        try:
            self.sim_env_step_h = float(
                getattr(self.cfg, 'sim_env_step_h', 6.0) or 6.0)
        except (TypeError, ValueError):
            self.sim_env_step_h = 6.0
        # 模态覆盖是纯 dict 归一化(不要内核), 保持在构造期。
        try:
            ov = getattr(self.cfg, 'sim_mode_override', None) or {}
            self.sim_mode_override = {str(k): float(v)
                                      for k, v in dict(ov).items()}
        except Exception:
            logger.debug('模态覆盖配置恢复失败', exc_info=True)
            self.sim_mode_override = {}
        # 兼容旧代码/配置: 由起始时刻派生(内核无关; 首次进模拟模式时补上)
        self.sim_month = None
        # 数据驱动模式: 默认**开**(离线环境场模型才是主路径); 用户关过就记住
        self.sim_data_mode = bool(getattr(self.cfg, 'sim_data_mode', True))
        self.sim_data_year = None      # 具体年份(如 2020); None=用气候态
        self.sim_data_clim = True      # True=气候态, False=具体年份库
        # 渲染/粒子类显示设置(同样镜像 cfg)
        for _key, _attr in self._SIM_DISPLAY_KEYS.items():
            _dflt = {'_fcst_style': 'JTWC', '_particle_count': 3000}.get(_key, True)
            setattr(self, _attr, getattr(self.cfg, _attr, _dflt))
        self.sim_sel_tc_id: Optional[int] = None
        self.sim_particles = None
        self.sim_v4 = None
        self.sim_icon_tys = {}
        self.sim_path_tys = {}
        # 欠账(单位 = **内步**, 不是旧版的"10 分钟步"): 见 _sim_advance
        self._sim_step_acc = 0.0
        self._sim_rate_hps = 0.0          # 实测推进速度(模拟小时/实时秒, 3s 窗口平滑)
        self._sim_rate_h = 0.0            # 速度窗口: 累计推进小时
        self._sim_rate_s = 0.0            # 速度窗口: 累计实时秒
        self._sim_last_budget_ms = 0.0    # 上一帧推进模拟实际花的墙钟 ms
        self._sim_disp_h = 0.0            # 显示钟(模拟小时): 连续播放用, 见 _sim_disp_target
        self._sim_disp_init = False       # 首帧是否已把显示钟对准"物理 - 一步"
        self._sim_cld_acc = 0.0
        self._sim_msg = ""
        self._sim_msg_t = 0
        # ⚠ 2026-10-02(W1 修复10): 原来这里在**构造期**就
        #   `from simulator.simcore import render as _R` + `set_label_fn(...)`,
        #   把 simcore.render(以及 numpy)拉进启动路径。已移入 `_sim_ensure_kernel()`。
        #   (另一条兜底注入在 _sim_setup_render_font(), 只在首次 _sim_render 时跑。)

    def _sim_ensure_kernel(self) -> None:
        """**首次真正进入模拟模式**时才 import SimCore 内核并完成一次性初始化。

        W1 修复10: 构造期不再 import 内核(`_init_sim_mode`), 把三件事推迟到这里:
          ① `_sim_start_dt`: 经 `_coerce_start` 归一化(`parse_start` /
             `latest_real_start`, 后者要读模态 npz);
          ② `sim_env_step_h`: 用核内 `snap_env_step` 吸附到 `ENV_STEPS`;
          ③ `set_label_fn`: 把 app 的文本渲染注入 simcore.render。
        幂等: 用 `_sim_kernel_ready` 短路; 任何一步失败都**不**置位, 下次进入会重试
        (与原来 try/except 逐块降级的语义一致)。
        调用点: `_sim_reset()` 开头(进入模拟模式 / 按 R 重置都经它),
        以及 `sim_start_dt` property(面板在进入前读起始时刻时兜底)。
        """
        if getattr(self, '_sim_kernel_ready', False):
            return
        try:
            from simulator.simcore.core import snap_env_step
        except Exception:
            # 与改动前一致: 内核不可用时**降级**而不是抛出去(构造期原来也是 try 包住的)
            logger.debug('SimCore 内核不可用, 跳过一次性初始化', exc_info=True)
            return
        try:
            self._sim_start_dt = self._coerce_start(
                getattr(self.cfg, 'sim_start', None))
            self.sim_env_step_h = snap_env_step(
                getattr(self.cfg, 'sim_env_step_h', 6.0))
            self.sim_month = self._sim_start_dt.month
        except Exception:
            logger.debug('模拟模式内核初始化失败', exc_info=True)
        try:
            from simulator.simcore import render as _R
            _R.set_label_fn(lambda t, c: rt(f_s, t, c))
        except Exception:
            logger.debug("simcore 文本渲染注入失败", exc_info=True)
        self._sim_kernel_ready = True

    def _sim_sync_path_ty(self, tc, disp_h=None):
        """把模拟台风**转换/增量更新成真正的 Typhoon 对象**(供其它模式的渲染管线用)。

        用户 2026-09-19: "你不会复用其它模式的东西? 非要从头写?" —— 对。模拟模式
        原来自己维护一个  轻量对象 + 自己的图标/标签/信息框, 三套代码
        各写一遍, 于是 ①名字样式与其它模式不一致 ②信息框里塞了"精度/实测"这种
        其它模式没有的东西 ③路径渲染还踩了 smooth_screen_points 缺失的坑。

        现在改成: **把 sim 的 track 映射成 Typhoon 数据模型**(pts/points_time/
        points_dt/ci/at/v), 之后图标、名字、路径、信息框全部走其它模式那套
        (draw_typhoon / draw_typhoon_info), 一行都不用重写。

        增量: 只在**新增报点**时 append(不整表重建), 这样路径渲染的增量缓存
        () 能命中, 不会每到一个报点重建整张路径面(卡顿来源)。
        """
        from datetime import timedelta as _td
        from app.typhoon import Typhoon

        ty = self.sim_path_tys.get(tc['id'])
        if ty is None:
            ty = Typhoon(str(tc.get('basin') or 'WP'), str(tc.get('id') % 100))
            ty.basin = str(tc.get('basin') or 'WP')
            # ⚠⚠ 2026-10-01 (F1, rev-simmix): **必须绑定 `.sim`, 与 `data_repo.py:212`
            #   的 `ty.sim = self._sim` 同一契约。**
            #   `Typhoon.sim` 默认 None(`app/typhoon.py:170`), 而 `typhoon_render.py:96`
            #   的样条构建门是 `if self.sim and self.sim.cfg.smooth_path:` ⇒ 不绑定时
            #   SIM 模式的 `smooth_screen_points` / `smooth_map_points` **恒为空**,
            #   后果①"SIM 模式平滑路径 100% 不生效";
            #   后果② `_draw_path_mixin.py:947-950` 那个"开关开着但样条为空 ⇒ 判过期"
            #   的守卫于是**每帧为真** ⇒ `_try_shift_screen_points` 的纯平移捷径每帧被否,
            #   退化成每帧全量重投影(性能回归)。
            #   注意**不要**反过来把这个守卫加上 `ty.sim` 判断来"消掉"每帧重建 ——
            #   那只是把平滑永不生效这个真 bug 藏起来。
            #   顺带修好一处同源不一致: `typhoon_render.py:161-162` 的
            #   `self.sim.main_rotation_speed / level3_rotation_speed` 在绑定后会取
            #   `TySim` 上的 cfg 代理值(默认 1.0 / 1.5, 与原来的 else 兜底**相同**),
            #   用户非默认旋转速度在 SIM 模式也能生效了。
            ty.sim = self
            # ⚠ 轮次 155: 原来这里写死 `_spawn_time = 0`, 等于**关掉生成渐入** ——
            # 风季在 `playback_ctrl`/`season_ctrl` 里都设成当前 tick, 于是图标
            # 0→255 渐显 800ms; 模拟模式原本是"直接跳出", 与风季不一致。
            ty.v._spawn_time = pygame.time.get_ticks()
            ty.v.icon_alpha = 255
            self.sim_path_tys[tc['id']] = ty
        # 名字/强度: 名字变了(达到命名门槛时)要同步, 其它模式按 ty.cust 显示
        # 表头样式与风季一致: "WP09 2026 名字" —— 编号放 ty.n, 名字放 ty.cust;
        # 未命名时 cust 留空(只显示编号)
        _nm = str(tc.get('name') or '')
        _num = _nm[2:] if _nm[:2] in ('WP', 'EP', 'AL', 'IO', 'SH', 'SP', 'SI') else ''
        if _num.isdigit():
            ty.n = _num
        # cust 保持空(不覆盖用户自定义名), sname 给"编号或真实名" —— 风季表头
        # "WP09 2026 名字" 里的"名字"就取它
        if getattr(ty, 'sname', '') != _nm:
            ty.sname = _nm
        base = self.sim_v4.get('start') if isinstance(self.sim_v4, dict) else None
        n = len(tc['track'])
        while len(ty.pts) < n:
            i = len(ty.pts)
            q = tc['track'][i]
            # ⚠⚠ 轮次 154: 报点的等级/颜色必须走**风季那套口径**, 不能用
            # simcore 的 `grade_kt`。两套表根本不是一套:
            #   simcore `grade_kt`(`simulator/simcore/render.py`): DB/TD/TS/C1..C5
            #     —— 8 档, **完全不看 phase**, 颜色是它自己的 `_CMA_KT`;
            #   风季 `get_strength_category`+`get_point_color`
            #     (`app/utils.py` / `app/data_repo.py`): 先按 `st`(EX/SS/SD/LO/MD/WV)
            #     分相态, 再按风速给 DB/TD/TS/STS/C1/C2-/C2/C3-/C3/C4-ST/C5 —— 12 档。
            # 后果(用户可见): **同一风速在两种模式画不同图标、不同颜色、不同等级串**;
            # `STS`/`C2-`/`C3-`/`C4-ST` 以及 EX/SS/LO 那些素材在模拟模式永远不被请求。
            # 附加收益: `_ace_eligible`(app/ace_engine.py) 只认 TS/TY/ST/HU, 而旧口径
            # 写的是 DB/C1/C2… ⇒ 模拟台风的 ACE 与"巅峰"标注几乎从不生效; 换成 phase
            # 口径后 TS/TY/ST 都合格, 那两处也随之变对。
            _w = int(round(float(q['vmax'])))
            _st = str(q.get('phase') or 'DB')      # 模拟的 phase 与风季的 st 同词汇
            col = self.get_point_color(_w, _st)
            dim = self.darken_color(col, 0.6)      # 与风季同一暗化系数(原为 0.45)
            _cat = self.get_strength_category(_w, _st)
            _dt = None
            try:
                _dt = base + _td(hours=float(q['t']))
            except Exception:
                _dt = None
            # ⚠ 轮次 155: 这里原来是**绕过 `Typhoon.add_point` 直接 append**,
            # 于是 add_point 的副作用全丢了(见下两处)。逐条补齐, 口径与
            # `app/typhoon_data.py` 的 add_point 一致。
            _la = float(q['lat'])
            if not ty.pts and _la < 0:
                # 与 add_point 首句一致: 南半球系统用镜像图标/反向自旋
                ty.v.mirror = True
                ty.v.rot_dir = -1
            ty.pts.append({
                't': (_dt.strftime('%Y%m%d%H') if _dt is not None else ''),
                'la': _la, 'lo': float(q['lon']),
                # 整数: 风季信息框直接 f"{w}kt" / f"{p}hPa"
                'w': _w,
                'p': int(round(float(q['pmin']))),
                'st': _st, 'cat': _cat, 'color': col, 'color_dim': dim,
                'official': True, 'ace': float(tc.get('ace', 0.0)),
            })
            # add_point 尾部会失效这三个**按整条路径**算的缓存(名字取色 / 巅峰标注 /
            # 名色)。原来模拟模式从不清它们 ⇒ 缓存冻结在首帧那批点上:
            # 名字颜色终身停在首报色、Peak 标注永远算不出。
            for _attr in ('_cached_max_wind_color', '_cached_peaks',
                          '_cached_name_colors'):
                if hasattr(ty, _attr):
                    delattr(ty, _attr)
            if _dt is not None:
                ty.points_dt.append(_dt)
            # points_time 单位与其它模式一致: 0.5 = 6 模拟小时
            ty.points_time.append(float(q['t']) / 12.0)
        # 显示位置: ci = 显示钟之前的最后一报, at 用同一单位 → 图标沿路径连续走
        _h = float(self._sim_disp_h if disp_h is None else disp_h)
        ci = 0
        for i, q in enumerate(tc['track']):
            if float(q['t']) <= _h + 1e-9:
                ci = i
        ty.ci = min(max(0, ci), max(0, len(ty.pts) - 1))
        ty.at = _h / 12.0
        ty.tace = float(tc.get('ace', 0.0))
        # 预报(集合统计)挂到对象上, 供信息框显示
        ty.fcPeak = tc.get('fcPeak')
        ty.fcLand = tc.get('fcLand')
        ty.cace = float(tc.get('ace', 0.0))
        ty.act = not tc.get('dead')
        ty.sf = bool(tc.get('dead'))
        ty.ss = ty.act
        ty.finish_time = 0
        # 实时位置(供活线段): 显示钟在相邻两报之间的插值位置
        try:
            tr = tc['track']
            lo_i = min(ty.ci, len(tr) - 1)
            if lo_i + 1 < len(tr):
                a, b = tr[lo_i], tr[lo_i + 1]
                span = max(1e-9, float(b['t']) - float(a['t']))
                w = min(1.0, max(0.0, (_h - float(a['t'])) / span))
                ty.v.ipos = {'la': float(a['lat']) + (float(b['lat']) - float(a['lat'])) * w,
                             'lo': float(a['lon']) + (float(b['lon']) - float(a['lon'])) * w}
            else:
                ty.v.ipos = {'la': float(tr[lo_i]['lat']), 'lo': float(tr[lo_i]['lon'])}
        except Exception:
            ty.v.ipos = None
        # ⚠⚠ 轮次 152: 记录**未截断的全程点数**。
        # `_sim_render` 会在画路径前把 `ty.pts` 截断到 `ci+1`(只画已播过的部分),
        # 而 `draw_typhoon_info` 的"图标淡出"拿 `len(ty.pts)` 当"是否已到末点"的
        # 依据 —— 截断后 `len(ty.pts) == ci+1`, 于是 `ci >= n_pts-2` 与
        # `ci >= n_pts-1` **恒真**, 淡出算出 progress=1.0 ⇒ **icon_alpha 恒为 0**
        # ⇒ 用户看到的就是"图标消失、只剩一个(淡色)定位点在动"。
        # 实测: 53 条采样里 52 条 alpha=0, 只有 ci=0 那条正常。
        ty.track_total_n = len(tc['track'])
        return ty

    def _sim_set_layer(self, lid: str) -> None:
        """**单选**切换图层(数字键/面板循环按钮): 只显示该图层 + 路径。

        ⚠ 轮次 153: 必须保留 `'track'`。本函数原先是**唯一**不补 `'track'` 的入口
        (另两条 `_sim_toggle_layer` / `_sim_set_env_layer` 都补, 且注释写明"路径层
        始终保留(它是台风本体)"), 而轮次 151 给图标/名字/信息框加了 `'track'` 门控
        ⇒ 按数字键切到别的图层会**整个台风消失**(路径+图标+名字+信息框一起没),
        与面板按钮的行为不一致。现在统一不变量: `sim_layers` 恒含 `'track'`。
        """
        if lid not in SIM_LAYER_IDS:
            return
        self.sim_layer = lid
        self.sim_layers = {lid} | {'track'}
        self._sim_notice(f"图层: {self._sim_layer_short()}")

    def _sim_toggle_layer(self, lid: str) -> None:
        """**叠加**切换: 同一个图层再点一次关掉, 多个图层可以同时开。

        用户要的是"sst/ohc/风切的半透明图层叠加" —— render_map 本来就支持
        (每个图层各自 blit, 带自己的 alpha), 缺的只是能让
        sim_layers 同时装多个层的入口。路径层始终保留(它是台风本体)。

        ⚠⚠ **轮次 151 修的缺陷**: 环境层(sst/ohc/shear)本应**三选一**(用户
        2026-09-19: "这三个应该在一起互斥"), 但互斥只在 `_sim_set_env_layer`
        (面板上那排蓝色按钮)里实现; 同一行的通用 `layer_*` 按钮与数字键都走
        **本函数**, 于是可以叠出 sst+ohc+shear 三层, 画面糊成一片
        (实测两层同开差 474,654 px = 63.09% 地图)。现在环境层在这里也委托给
        互斥分支, 保证**无论从哪条入口**基数都 ≤ 1。
        """
        if lid not in SIM_LAYER_IDS:
            return
        if lid in self.ENV_LAYERS:
            self._sim_set_env_layer(lid)
            return
        cur = set(self.sim_layers or set())
        if lid in cur:
            cur.discard(lid)
        else:
            cur.add(lid)
        cur.add('track')                 # 路径不参与叠加开关
        self.sim_layers = cur
        self.sim_layer = lid
        # 副高图层: 默认镜头下它常在视野外(实测地图区 0 像素) —— 打开后如果
        # 一帧内什么都没画出来, 给一句提示, 免得用户以为"这个按钮是假的"。
        if lid == 'ridge':
            self._sim_ridge_check_at = pygame.time.get_ticks() + 500
        names = [n for i, n, _ in SIM_LAYERS if i in cur and i != 'track']
        self._sim_notice('叠加图层: ' + ('、'.join(names) if names else '无'))

    @property
    def sim_start_dt(self):
        """运行时起始时刻(datetime)。UI 读这个; cfg.sim_start 只存字符串。

        ⚠ 2026-10-02(W1 修复10): 构造期不再解析(那会 import 内核), 这里**惰性**
        解析并**缓存**回 `_sim_start_dt`(所以只会 import 一次)。第一次访问时若内核
        还没就绪, 先走 `_sim_ensure_kernel()` 完成一次性初始化。
        """
        dt = getattr(self, '_sim_start_dt', None)
        if dt is None:
            if not getattr(self, '_sim_kernel_ready', False):
                self._sim_ensure_kernel()
            dt = getattr(self, '_sim_start_dt', None)
        return dt or self._coerce_start(getattr(self.cfg, 'sim_start', None))

    @staticmethod
    def _coerce_start(v):
        """任何输入 → datetime; 空值 = **自动**(最后一个有实测模态数据的时刻)。

        用户 2026-09-19: "默认是 1850 年. 应该是当前时间(或者是最后一个能找到
        现实模态的时间)"。空/坏值不再回落到写死的 1997-09-16, 而是问内核要
        latest_real_start(读不到就系统当前时间)。
        """
        from datetime import datetime as _dt
        try:
            from simulator.simcore.core import parse_start, latest_real_start
            return parse_start(v) if v else latest_real_start(6.0)
        except Exception:
            return _dt(2026, 3, 17, 0)

    # ── 显示/开关类设置: sim_v4 键 ↔ 应用属性(同时镜像到 cfg, 跨会话记住) ──
    #   2026-09-18 review: 这些开关原来只写 sim_v4, 而 sim_v4 每次 _sim_reset
    #   都重建 → "关掉风圈 → 改起始时刻" 被静默打回默认; 重启全丢。
    _SIM_DISPLAY_KEYS = {
        '_fcst_style': 'sim_fcst_style',
        '_particle_count': 'sim_particle_count',
        '_quad_rings': 'sim_quad_rings',
        '_show_rings': 'sim_show_rings',
        '_show_labels': 'sim_show_labels',
        '_show_barbs': 'sim_show_barbs',
        '_show_forecast': 'sim_show_forecast',
    }

    def _sim_display(self, key, default=None):
        """读显示设置: 应用属性优先(权威), 其次配置, 最后 sim_v4。"""
        attr = self._SIM_DISPLAY_KEYS.get(key)
        if attr is None:
            s = getattr(self, 'sim_v4', None)
            return (s or {}).get(key, default) if isinstance(s, dict) else default
        v = getattr(self, attr, None)
        if v is None:
            v = getattr(self.cfg, attr, None)
        if v is None:
            s = getattr(self, 'sim_v4', None)
            v = (s or {}).get(key, default) if isinstance(s, dict) else default
        return default if v is None else v

    def _sim_set_display(self, key, value) -> None:
        """写显示设置: 同时落到 sim_v4(渲染读它) / 应用属性 / 配置(记忆)。"""
        attr = self._SIM_DISPLAY_KEYS.get(key)
        s = getattr(self, 'sim_v4', None)
        if isinstance(s, dict):
            s[key] = value
        if attr is None:
            return
        if attr == 'sim_particle_count':
            value = max(500, min(6000, int(value)))
            s = getattr(self, 'sim_v4', None)
            if isinstance(s, dict):
                s[key] = value
        setattr(self, attr, value)
        try:
            setattr(self.cfg, attr, value)
            self._config_needs_save = True
        except Exception:
            logger.debug('记录显示设置失败', exc_info=True)
        # ⚠ 轮次 156: "预报路径"是**依赖集合预报数据**的显示项, 而集合默认关
        # (占单步 89% 墙钟, 见 app/config.py 的 sim_ensemble)。原来打开它什么都不
        # 会发生、也没有任何提示 ⇒ 看起来像个坏开关。
        # 这里**只提示不自动开启**: 偷偷把集合打开会让用户莫名卡死几秒。
        if key == '_show_forecast' and value and not bool(
                getattr(self.cfg, 'sim_ensemble', False)):
            self._sim_notice('预报路径需要先开「集合预报(实时,慢)」, 否则没有预报数据')

    #: ── 轮次 158: 新增环境机制的面板可调表 ─────────────────────────────────
    #: 值域刻意取"有意义的小集合"(面板 cycle 行逐个循环)。**每项 [0] 必须是内核默认**
    #: —— 这样循环一圈必然经过默认值, 能回到"逐位不变"的基准。
    #: 键名 = `sim['params']` 的键, 与内核 `P.get(key, 常量)` 一一对应。
    SIM_PARAM_STEPS = {
        # ① 副高脊线倾角(度纬度/度经度) —— 作用于脊线诊断 ⇒ 引导修正
        'shTilt': [0.0, 0.5, 1.0, 2.0, 3.0, -0.5, -1.0, -2.0],
        # ② 副高西端陡峭
        'shWestAmp': [0.0, 2.0, 4.0, 6.0, 10.0],
        # ③ 副高四向伸展(度) —— [0] 逐字等于原来的内联常数 22 / 12 / 5.5 / 5.5
        'shExtW': [22.0, 18.0, 26.0, 32.0, 40.0],
        'shExtE': [12.0, 8.0, 16.0, 20.0, 24.0],
        'shExtN': [5.5, 4.0, 7.0, 9.0, 12.0],
        'shExtS': [5.5, 4.0, 7.0, 9.0, 12.0],
        # ④ 西风槽强度峰值(kt)
        'wtAmp': [6.0, 3.0, 10.0, 14.0, 0.0],
        # ⑤ 冷空气低层风异常峰值(kt)
        'csAmp': [8.0, 4.0, 12.0, 16.0, 0.0],
    }
    #: 开关类(布尔)参数 —— 默认全关, 所以 [0] 概念上就是 False
    SIM_PARAM_FLAGS = ('wtOn', 'csOn', 'terrainOn')

    def _sim_param_value(self, name, default=None):
        """读新机制参数: sim['params'] 优先 → cfg 覆盖表 → 表里第一档(内核默认)。"""
        s = getattr(self, 'sim_v4', None)
        p = (s or {}).get('params') if isinstance(s, dict) else None
        if isinstance(p, dict) and name in p:
            return p[name]
        ov = getattr(self.cfg, 'sim_env_params', None) or {}
        if name in ov:
            return ov[name]
        # ⚠⚠ 开关类必须**先判**, 不能排在 `default is not None` 之后。
        # 面板画复选框用的是 `self._get(key, True)`(sim_settings_dialog.py:440),
        # 旧顺序下 `_sim_param_value('wtOn', True)` 会**直接返回 True**, 而内核默认是
        # False ⇒ 三个开关永远画成"已勾选"(假状态), 且第一次点击反而把它**打开**、
        # 框不变、还提示"wtOn: 开"。实测由 verifier 抓到(轮次 159, VERIFY_R158.md §2.1)。
        # 开关的权威默认值就是内核的"关", 调用方给的 default 不改变这一点。
        if name in self.SIM_PARAM_FLAGS:
            return False
        if default is not None:
            return default
        return (self.SIM_PARAM_STEPS.get(name) or [None])[0]

    def _sim_set_param(self, name, value) -> None:
        """写新机制参数: 同时落 `sim_v4['params']`(本帧生效)与 cfg(记忆, 重置不丢)。"""
        s = getattr(self, 'sim_v4', None)
        if isinstance(s, dict):
            s.setdefault('params', {})[name] = value
        try:
            ov = dict(getattr(self.cfg, 'sim_env_params', None) or {})
            ov[name] = value
            self.cfg.sim_env_params = ov
            self._config_needs_save = True
        except Exception:
            logger.debug('记录环境参数失败', exc_info=True)

    def _sim_cycle_param(self, name) -> None:
        """面板 cycle 行: 在 `SIM_PARAM_STEPS[name]` 里循环到下一档并立即生效。"""
        steps = self.SIM_PARAM_STEPS.get(name)
        if not steps:
            return
        cur = self._sim_param_value(name)
        i = 0
        for k, v in enumerate(steps):
            try:
                if abs(float(v) - float(cur)) < 1e-9:
                    i = k
                    break
            except (TypeError, ValueError):
                continue
        nxt = steps[(i + 1) % len(steps)]
        self._sim_set_param(name, nxt)
        self._sim_notice('%s: %g' % (name, nxt))

    def _sim_toggle_param(self, name) -> None:
        """面板 check 行: 开关类参数(默认全关)。"""
        v = not bool(self._sim_param_value(name, False))
        self._sim_set_param(name, v)
        self._sim_notice('%s: %s' % (name, '开' if v else '关'))

    #: ── 轮次 158: 时间轴回放(回溯) ──────────────────────────────────────────
    #: 机制在 `simulator/simcore/rewind.py`(纯标准库, 30 项单测); 这里只管
    #: **应用侧接线**: 建仓库 / 每步 advance / 恢复后作废全部派生缓存。
    #: ⚠ 最容易出的 bug 是"物理状态回溯了, 但画面还在显示回放点之后的东西",
    #: 所以恢复一律走 `on_restore` 回调集中失效, 而不是散在调用点。
    SIM_REWIND_INTERVAL = 2              # 每 N 步存一份(粒度 vs 内存的折中)
    SIM_REWIND_MAX_SNAPSHOTS = 240
    SIM_REWIND_MAX_BYTES = 96 * 1024 * 1024

    def _sim_rewind_init(self) -> None:
        """按预算建回放仓库。每次 `_sim_reset` 重建 —— 旧快照属于旧的那次模拟。"""
        self._sim_rewind = None
        try:
            from simulator.simcore import rewind as _RW
        except Exception:
            logger.debug('回放模块不可用', exc_info=True)
            return
        try:
            self._sim_rewind = _RW.RewindStore(
                self.sim_v4,
                interval=int(self.SIM_REWIND_INTERVAL),
                max_snapshots=int(self.SIM_REWIND_MAX_SNAPSHOTS),
                max_bytes=int(self.SIM_REWIND_MAX_BYTES),
                min_keep=4,
                on_restore=self._sim_on_rewind_restore)
        except Exception:
            logger.debug('回放仓库建立失败', exc_info=True)
            self._sim_rewind = None

    def _sim_rewind_tick(self) -> None:
        """物理真的推进了才调(内部按 interval 决定是否真存快照)。"""
        st = getattr(self, '_sim_rewind', None)
        if st is None:
            return
        try:
            st.advance()
        except Exception:
            logger.debug('回放采集失败', exc_info=True)

    def _sim_on_rewind_restore(self, snap) -> None:
        """恢复后**作废全部派生缓存**。

        不做的后果: 台风对象还是回放点之后的那一代(`sim_path_tys` 按旧 tcs 建),
        路径面缓存键(id(ty.pts))也就不会失效 ⇒ 画面继续显示"未来"。
        """
        try:
            self._sim_drop_ty_caches()          # 注销旧一代对象的缓存
            # ⚠⚠ **必须真的清空 `sim_path_tys`**: `_sim_drop_ty_caches()` 只做
            # "缓存注销"(它不碰这个 dict —— 清空是在 `_sim_reset` 里做的),
            # 而 `tc['id']` 在恢复前后是**同一个** ⇒ 留着旧对象会被"同 id 的新 tcs"
            # 直接复用, 于是路径/报点仍是回放点之后那一代 —— 物理回溯了、画面没回溯。
            self.sim_path_tys = {}
            # ⚠⚠ 轮次 162 补: **同类还有两个** —— `sim_icon_tys` 也是**按 `tc['id']`
            # 建键**的(`:1609/:1612`), 而 id 恢复前后相同 ⇒ 同理会复用旧对象
            # (图标纹理/动画帧停在未来); `sim_sel_tc_id` 则可能指向一个**在恢复点
            # 还没生成/已经消亡**的台风。两者在 `_sim_reset` 里都清了(`:845`/`:854`),
            # 回放恢复也该清 —— 这条是 verifier 审计时发现的
            # (VERIFY_R158.md §2.11, 与上面 `sim_path_tys` 同一类, 只是我一直漏了)。
            self.sim_icon_tys = {}
            self.sim_sel_tc_id = None
        except Exception:
            logger.debug('回放: 台风缓存作废失败', exc_info=True)
        try:
            from simulator.simcore import render as _R
            _R._flow_last.clear()               # 流场/环境层节流缓存(模块级 dict)
            # ⚠⚠ 轮次 159 补: `_flow_cache` 的键**只含 `_envVer`、没有校验和**, 而
            # 恢复时版本号是"快照值 +1" —— 这个号在**回退前那段被作废的未来里已经
            # 用过** ⇒ 只清节流表救不了: 命中 `_flow_cache` 会**提前 blit 返回**,
            # 根本不查节流表。同样只有版本号做键的还有内核的物理量 `_OCEAN_UV`
            # (core.py:4495, 供 `ocean_step` 做尾流平流, 键 `('usv', _envVer, shape)`)。
            # 实测由 verifier 抓到(轮次 159, VERIFY_R158.md §2.2)。
            _R._flow_cache.clear()
        except Exception:
            logger.debug('回放: 流场缓存作废失败', exc_info=True)
        try:
            from simulator.simcore import core as _C
            _C._OCEAN_UV.clear()                # 只按 _envVer 做键的物理缓存
        except Exception:
            logger.debug('回放: 内核版本缓存作废失败', exc_info=True)
        try:
            # 显示钟跟到恢复点 —— 与 `_sim_advance` 同一条不变式
            # (disp <= max(0, t_phys - step_h)); 不跟的话日历还停在"未来"
            self._sim_disp_h = max(0.0, float(self.sim_v4.get('t', 0.0))
                                   - self._sim_step_h())
            self._sim_disp_init = True
            self._sim_rate_t_prev = None
        except Exception:
            logger.debug('回放: 显示钟同步失败', exc_info=True)

    def _sim_rewind_move(self, direction: int) -> bool:
        """沿快照时间轴走一格。direction < 0 = 回溯, > 0 = 前进。"""
        st = getattr(self, '_sim_rewind', None)
        if st is None:
            self._sim_notice('回放: 未启用')
            return False
        try:
            idx = list(st.indices)
        except Exception:
            idx = []
        if len(idx) < 2:
            self._sim_notice('回放: 还没有可回溯的快照')
            return False
        cur = int(getattr(st, 'cursor', 0) or idx[-1])
        if direction < 0:
            cand = [i for i in idx if i < cur]
            tgt, edge = (cand[-1] if cand else None), '已到最早'
        else:
            cand = [i for i in idx if i > cur]
            tgt, edge = (cand[0] if cand else None), '已到最新'
        if tgt is None:
            self._sim_notice('回放: %s' % edge)
            return False
        try:
            st.restore(int(tgt))
        except Exception:
            logger.debug('回放恢复失败', exc_info=True)
            self._sim_notice('回放: 恢复失败')
            return False
        self._sim_notice('回放: 第 %d 份快照 (t=%.1fh)'
                         % (tgt, float(self.sim_v4.get('t', 0.0))))
        return True

    def _sim_rewind_key(self, k: int) -> bool:
        """按键: `,` 回溯一份快照。返回 True = 已消费(放在分派最后)。

        ⚠ **没有"前进"键, 这是有意的**: `RewindStore.restore()` 会调 `_drop_after`
        丢掉恢复点**之后**的全部快照(线性时间轴语义 —— 回溯后重放的历史与原来必然
        不同, redo 必须失效)。所以"前进"在回溯之后永远是死键; 正确的继续方式是
        **播放**(SPACE): 从恢复点重新积算下去, 并自然产生新的快照。
        """
        if k == pygame.K_COMMA:
            return self._sim_rewind_move(-1)
        return False

    def _sim_set_display_particle(self, v) -> None:
        """粒子密度(设置面板"粒子密度"行 → 单值对话框 → 这里)。

        走 _sim_set_display, 于是同时落 sim_v4(渲染读) / 应用属性 / cfg(记忆),
        粒子对象在下一帧 _sim_update 里按新数量重建。
        """
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError):
            self._sim_notice('粒子数需要整数')
            return
        self._sim_set_display('_particle_count', n)
        self._sim_notice('粒子密度: %d' % self._sim_particle_count())
        self._panel = None

    def _sim_toggle_natural_gen(self) -> bool:
        """自然生成开关(**唯一实现**)。设置面板与 N 键都走这里。

        原来两条路径各写各的: 面板写 sim_v4['params']['genesis'] 与一个没人读的
        self.genesis, N 键写 self.sim_natural_gen —— 于是面板显示与真实开关
        会互相打脸(面板按 sim_v4['_genesis'] 画, 那个键没人写过)。
        """
        self.sim_natural_gen = not bool(self.sim_natural_gen)
        if isinstance(getattr(self, 'sim_v4', None), dict):
            self.sim_v4.setdefault('params', {})['genesis'] = self.sim_natural_gen
        try:
            self.cfg.sim_natural_gen = self.sim_natural_gen
            self._config_needs_save = True
        except Exception:
            logger.debug('记录自然生成开关失败', exc_info=True)
        self._sim_notice("自然生成: " + ("开" if self.sim_natural_gen else "关"))
        return True

    def _sim_toggle_data_mode(self) -> bool:
        """数据环境开关(**唯一实现**)。切换后必须重建模拟(环境场来源变了)。"""
        self.sim_data_mode = not bool(self.sim_data_mode)
        try:
            self.cfg.sim_data_mode = self.sim_data_mode
            self._config_needs_save = True
        except Exception:
            logger.debug('记录数据模式失败', exc_info=True)
        self._sim_reset(msg=False)
        st = (self.sim_v4 or {}).get('dataState', 'off')
        label = {'loaded': '开(真实数据)', 'partial': '开(真实数据·部分降级)',
                 'fallback': '开(已降级解析)', 'off': '关(解析公式)'}.get(st, st)
        self._sim_notice("数据模式: " + label)
        return True

    # ── 重置(新建模拟) ──
    def _sim_drop_ty_caches(self) -> None:
        """把"按 Typhoon 对象做键"的缓存**注销掉**旧一代模拟台风。

        轮次 153 修: `_sim_reset` 原来只是 `self.sim_path_tys = {}` 丢掉引用,
        但 `_name_anim` / `_season_info_box_cache` / `_season_info_box_last_data` /
        `_box_anim` 这些 dict 是**以 Typhoon 对象为键**的 ⇒ 对象被键强引用、GC 收不走;
        而对象上还挂着 bbox 尺寸的 `_path_cache_full` Surface(最宽到屏宽量级)
        ⇒ 每次重置永久泄漏一代台风。

        ⚠ 只做"缓存注销", 不碰对话框/播放控制器 —— 模拟台风从不进入
        `repo.tys`, 所以 `delete_typhoon()`(它还负责信息框槽位、图表对话框、
        特效缓存)在这里既不需要、也会误伤。
        """
        stale = list(getattr(self, 'sim_path_tys', {}).values())
        for ty in stale:
            try:
                self._invalidate_path_cache_for_ty(ty)
            except Exception:
                logger.debug('注销路径缓存失败', exc_info=True)
            for _c in ('_name_anim', '_season_info_box_cache',
                       '_season_info_box_last_data', '_box_anim'):
                _d = getattr(self, _c, None)
                if _d:
                    _d.pop(ty, None)
            try:
                self._drop_season_start_cache(ty)
            except Exception:
                pass
            try:
                from ..typhoon_render import _clear_geo_spline_cache
                _clear_geo_spline_cache(ty)
            except Exception:
                pass

    def _sim_reset(self, msg: bool = True) -> None:
        # W1 修复10: 这是"进入模拟模式 / 按 R 重置"的必经入口 ⇒ 在这里完成一次性
        # 内核初始化(import simcore + 起始时刻归一化 + 环境步长吸附 + 字体注入)。
        self._sim_ensure_kernel()
        from simulator.simcore import core as V
        from simulator.simcore.core import DEFAULT_PRECISION
        # 精度档位必须**在这里**灌进 params: core.init 的 params 默认是
        # DEFAULT_PRECISION, 而设置面板/循环函数只写 cfg 与"当前" sim_v4。
        # 少了这一行, 任何一次 reset(改起始时刻/改步长/切数据环境)都会把
        # 用户选的档位静默打回 400ms, 而 UI 还显示着他选的档。
        _prec = str(getattr(self.cfg, 'sim_precision', DEFAULT_PRECISION))
        self.sim_v4 = V.init({
            # W1 修复10: 用 property(惰性解析 + 缓存), 构造期它是 None。
            'start': self.sim_start_dt,
            'envStepH': self.sim_env_step_h,
            'seed': self.sim_seed,
            'params': {'shLat': 31.0, 'shLon': 146.0, 'shAmp': 10.0,
                       'random': 0.55, 'genesis': self.sim_natural_gen,
                       'dataMode': self.sim_data_mode,
                       'dataYear': self.sim_data_year,
                       'dataClim': self.sim_data_clim,
                       'dataDayCycle': 1.0,
                       'modeOverride': dict(self.sim_mode_override or {}),
                       # 集合预报(实时): 默认关 —— 见 app/config.py 的 sim_ensemble 说明。
                       # 内核默认仍是 True, 只有交互式应用这一侧显式关闭。
                       'forecast': bool(getattr(self.cfg, 'sim_ensemble', False)),
                       'precision': _prec,
                       # 轮次 158: 把面板调过的**新增环境机制参数**合并进 params
                       # (副高形状 / 西风槽 / 冷空气 / 地形)。
                       # ⚠ 必须在**内层 'params' 字典里** —— 放到外层 opts 会被
                       # V.init 忽略, 表现为"改完一按重置就丢"(实测踩过)。
                       # **空字典 = 一个键都不加 ⇒ 内核默认 ⇒ 逐位不变**;
                       # merge 而非覆盖 ⇒ 上面那些既有键不会被陈旧值改掉。
                       **dict(getattr(self.cfg, 'sim_env_params', None) or {})},
        })
        V.env_update(self.sim_v4)
        # 轮次 158: 回放仓库必须**在 sim_v4 建好之后**建(快照直接引用这个 dict);
        # 每次重置都重建 —— 旧快照属于旧的那次模拟, 留着会让回溯到"另一次模拟"。
        self._sim_rewind_init()
        from simulator.simcore.render import Particles
        # 渲染设置(存于 sim_v4 dict, render_map 读取) —— 从**记忆**恢复, 不再写死默认
        for _k in self._SIM_DISPLAY_KEYS:
            self.sim_v4[_k] = self._sim_display(_k)
        # 风场/风切样式(2026-09-19): 走 cfg 记忆, 渲染侧读 sim_v4 的副本
        self._sim_sync_layer_styles()
        self.sim_particles = Particles(self._sim_particle_count())
        self.sim_sel_tc_id = None
        # 轮次 151: 重置必须清掉路径/图标用的 Typhoon 对象缓存。原先只在
        # `_init_sim_mode` 建一次、reset 不清 ⇒ 重置后台风的图标/名字/信息框会
        # **残留在屏幕上**(它们不受 `sim_layers` 门控, 见 _sim_render)。
        # 轮次 153: 清空前先给旧对象**注销**那些"按 Typhoon 对象做键"的缓存,
        # 否则对象被 dict 的键强引用、GC 收不走(对象上还挂着 bbox 尺寸的
        # `_path_cache_full` Surface)⇒ 每次 reset 永久泄漏一代台风。
        self._sim_drop_ty_caches()
        self.sim_path_tys = {}
        self.sim_icon_tys = {}
        # 轮次 153: 副高的"延迟检查"定时器也要清, 否则重置后可能立刻弹出
        # 上一局留下的"副高中心不在当前视野"提示。
        self._sim_ridge_check_at = 0
        self._sim_step_acc = 0.0
        self._sim_disp_h = 0.0
        self._sim_disp_init = False
        self._sim_rate_reset()
        self._sim_cld_acc = 0.0
        if msg:
            self._sim_notice("已重置模拟(%s 起, 步长 %gh, 精度 %s, 种子 %d)"
                             % (self.sim_start_dt.strftime('%Y-%m-%d %HZ'),
                                self.sim_env_step_h, _prec, self.sim_seed))

    def _sim_particle_count(self) -> int:
        """粒子数: 以 sim_v4 为运行时真值(滑条只写它), 属性/配置兜底。"""
        s = getattr(self, 'sim_v4', None)
        if isinstance(s, dict):
            v = s.get('_particle_count')
            if v:
                return max(500, min(6000, int(v)))
        return max(500, min(6000, int(getattr(self, 'sim_particle_count', 3000))))

    # ── F1: 起始时刻 / 环境步长 ──
    def _sim_set_start(self, dt, reset: bool = True) -> None:
        """设置模拟起始时刻(任意年份 1700-2300)。"""
        from simulator.simcore.core import parse_start, YEAR_MIN, YEAR_MAX
        dt = parse_start(dt)
        if not (YEAR_MIN <= dt.year <= YEAR_MAX):
            self._sim_notice("起始年份需在 %d-%d 之间" % (YEAR_MIN, YEAR_MAX))
            return
        try:
            self.cfg.sim_start = dt.strftime('%Y-%m-%d %H')
            self._config_needs_save = True
        except Exception:
            logger.debug('记录起始时刻失败', exc_info=True)
        self._sim_start_dt = self._coerce_start(dt)
        self.sim_month = self._sim_start_dt.month
        if reset:
            self._sim_reset(msg=False)
        self._sim_notice("起始时刻: %s" % dt.strftime('%Y-%m-%d %HZ'))

    def _sim_set_env_step(self, h, reset: bool = True) -> None:
        """设置环境场重建步长(档位见 core.ENV_STEPS: 1/3/6 h, 上限 6 h)。

        注意: 这是**环境场重建间隔**(物理分辨率), 不是播放速度。播放速度是
        self.sp(全局速度条 / 快捷键 +/←/→/X), 两者别混。
        """
        from simulator.simcore.core import snap_env_step
        self.sim_env_step_h = snap_env_step(h)
        if self.sim_v4 is not None:
            self.sim_v4['envStepH'] = self.sim_env_step_h
        try:
            self.cfg.sim_env_step_h = float(self.sim_env_step_h)
            self._config_needs_save = True
        except Exception:
            logger.debug('记录环境步长失败', exc_info=True)
        if reset and self.sim_v4 is not None and float(self.sim_v4['t']) > 0:
            self._sim_reset(msg=False)
        self._sim_notice("环境步长: %gh" % self.sim_env_step_h)

    def _sim_set_mode_override(self, d) -> None:
        """设置模态手动覆盖(模态名 → σ 值); 传空 dict 即恢复查表/AR。"""
        clean = {}
        for k, v in (d or {}).items():
            try:
                clean[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        self.sim_mode_override = clean
        try:
            self.cfg.sim_mode_override = dict(clean)
            self._config_needs_save = True
        except Exception:
            logger.debug('记录模态覆盖失败', exc_info=True)
        if self.sim_v4 is not None:
            self.sim_v4['params']['modeOverride'] = dict(clean)
        if clean:
            self._sim_notice("模态覆盖: " + ', '.join(
                '%s=%+.2f' % kv for kv in list(clean.items())[:4]))
        else:
            self._sim_notice("模态覆盖: 已清除")

    # ── 播放速度 / 随机种子 / 数据年份(2026-09-19 补的面板入口) ──
    def _sim_set_speed(self, x) -> None:
        """设置播放速度倍速(与全局速度条同一个 self.sp)。

        定义: 1× = 每秒 6 个**积分步**; 积分步长 = 环境步长, 所以 6h 步时
        1× ≈ 36 模拟小时/实时秒。实际吃不下的档位/速度会自动降速,
        界面显示"有效 x×"(见 _sim_rate_text)。
        """
        try:
            x = float(x)
        except (TypeError, ValueError):
            return
        self.sp = min(float(self.mas), max(float(self.mis), x))
        self._sim_rate_reset()
        self._panel = None
        self._sim_notice("速度: %s" % self._sim_rate_text())

    def _sim_cycle_speed(self) -> None:
        """按 SIM_SPEEDS 循环到下一档(面板点一下走一格; 与速度按钮同表)。"""
        cur = float(getattr(self, 'sp', 1.0))
        nxt = next((s for s in SIM_SPEEDS if s > cur + 1e-9), SIM_SPEEDS[0])
        self._sim_set_speed(nxt)

    def _sim_set_seed(self, v) -> None:
        """设置随机种子(环境模态 AR 噪声 / 集合扰动 / 生成扰动的来源)。

        只影响 init/reset 之后的新噪声流, 所以**下次重置生效** —— 提示里说明。
        """
        try:
            n = int(float(v)) & 0xFFFFFFFF
        except (TypeError, ValueError):
            self._sim_notice("种子需要整数")
            return
        self.sim_seed = n
        try:
            self.cfg.sim_seed = n
            self._config_needs_save = True
        except Exception:
            logger.debug('记录种子失败', exc_info=True)
        self._sim_notice("种子: %d(下次重置生效)" % n)

    def _sim_set_data_year(self, y) -> None:
        """设置数据环境用的年份(None/空 = 气候态; 有值 = 具体年份库)。

        环境场来源变了 → 必须重建模拟。
        """
        if y is None or str(y).strip() == '':
            self.sim_data_year = None
            self.sim_data_clim = True
            _txt = '气候态'
        else:
            try:
                n = int(float(str(y).strip()))
            except (TypeError, ValueError):
                self._sim_notice("年份需要整数(或留空 = 气候态)")
                return
            from simulator.simcore.core import YEAR_MIN, YEAR_MAX
            if not (YEAR_MIN <= n <= YEAR_MAX):
                self._sim_notice("年份需在 %d-%d 之间" % (YEAR_MIN, YEAR_MAX))
                return
            self.sim_data_year = n
            self.sim_data_clim = False
            _txt = '%d 年库' % n
        try:
            self.cfg.sim_data_year = self.sim_data_year
            self.cfg.sim_data_clim = bool(self.sim_data_clim)
            self._config_needs_save = True
        except Exception:
            logger.debug('记录数据年份失败', exc_info=True)
        self._sim_reset(msg=False)
        self._sim_notice("数据年份: %s" % _txt)

    def _sim_data_year_text(self) -> str:
        if getattr(self, 'sim_data_year', None) is None:
            return '气候态'
        return '%d 年库' % int(self.sim_data_year)

    # ── 图层与样式(2026-09-19: 图层开关搬到下方功能栏, 地图上不再有按钮) ──
    ENV_LAYERS = ('sst', 'ohc', 'shear')

    def _sim_sync_layer_styles(self) -> None:
        """把 cfg 里的图层样式写进 sim_v4(渲染侧读副本)。"""
        if not isinstance(getattr(self, 'sim_v4', None), dict):
            return
        self.sim_v4['_wind_mode'] = str(
            getattr(self.cfg, 'sim_wind_mode', 'barbs') or 'barbs')
        self.sim_v4['_shear_fill'] = bool(
            getattr(self.cfg, 'sim_shear_fill', True))

    def _sim_set_env_layer(self, lid: str) -> None:
        """海温/热含量/风切**三选一**(用户 2026-09-19: 这三个应该在一起互斥)。

        再点一次当前层 = 关掉; 点另一个 = 换成它。其它图层(气压/风场/环流/…)
        不受影响, 可以叠加。
        """
        if lid not in self.ENV_LAYERS:
            return
        cur = set(self.sim_layers or set())
        others = set(self.ENV_LAYERS) - {lid}
        if lid in cur:
            cur.discard(lid)
        else:
            cur -= others
            cur.add(lid)
        cur.add('track')
        self.sim_layers = cur
        self.sim_layer = lid
        self._sim_notice('环境层: ' + (self._sim_env_layer_text()))

    def _sim_env_layer_text(self) -> str:
        cur = set(self.sim_layers or set())
        names = [n for i, n, _k in SIM_LAYERS if i in cur and i in self.ENV_LAYERS]
        return names[0] if names else '无'

    def _sim_cycle_wind_mode(self) -> None:
        """风场样式: 风羽 → 流线 → 粒子(用户要"支持流场式的")。"""
        seq = ('barbs', 'stream', 'particles')
        cur = str(getattr(self.cfg, 'sim_wind_mode', 'barbs') or 'barbs')
        nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else 'barbs'
        self.cfg.sim_wind_mode = nxt
        self._config_needs_save = True
        self._sim_sync_layer_styles()
        self._panel = None
        self._sim_notice('风场样式: ' + {'barbs': '风羽', 'stream': '流线',
                                        'particles': '粒子'}[nxt])

    def _sim_cycle_shear_style(self) -> None:
        """风切样式: 色阶(默认) ↔ 等值线(带数值标注)。"""
        self.cfg.sim_shear_fill = not bool(getattr(self.cfg, 'sim_shear_fill', True))
        self._config_needs_save = True
        self._sim_sync_layer_styles()
        self._panel = None
        self._sim_notice('风切样式: ' + ('色阶' if self.cfg.sim_shear_fill else '等值线'))

    def _sim_cycle_env_step(self) -> None:
        """按档位表循环到下一档(设置面板点一下走一格)。"""
        from simulator.simcore.core import ENV_STEPS
        cur = float(getattr(self, 'sim_env_step_h', 6.0))
        i = min(range(len(ENV_STEPS)), key=lambda k: abs(ENV_STEPS[k] - cur))
        self._sim_set_env_step(ENV_STEPS[(i + 1) % len(ENV_STEPS)], reset=False)

    def _sim_cycle_precision(self) -> None:
        """循环模拟精度档位, 并**立刻**应用到运行中的模拟。

        档位名是"每 6h 模拟时间的墙钟成本"目标。实测成本会实时显示
        (sim['stepCostMs']) —— 档位名是目标不是承诺, 因为机器不同会漂。
        """
        from simulator.simcore.core import PRECISION_TIERS
        names = [n for n, _ms, _i, _h, _q in PRECISION_TIERS]
        cur = str(getattr(self.cfg, 'sim_precision', '400ms'))
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.cfg.sim_precision = nxt
        self._config_needs_save = True
        s = getattr(self, 'sim_v4', None)
        if isinstance(s, dict):
            s.setdefault('params', {})['precision'] = nxt
        self._sim_rate_reset()
        self._sim_notice('模拟精度 → %s' % nxt)

    def _sim_cost_warn(self) -> bool:
        """当前一步是否超出"实际上限 800 ms" —— UI 据此显示'已达当前模拟时间步长上限'。

        注: 2s/5s 两档**故意**超过 800ms(用户 2026-09-18: 这两档只能靠放慢
        模拟推进速度来达到), 所以它们常亮此提示是预期行为, 不是故障。
        """
        s = getattr(self, 'sim_v4', None)
        if not isinstance(s, dict):
            return False
        from simulator.simcore.core import PRECISION_CAP_MS
        return float(s.get('stepCostMs', 0.0) or 0.0) > PRECISION_CAP_MS

    def _sim_tier_target_ms(self) -> float:
        """当前档位的**目标** ms/6h(档位名就是目标, 实测见 sim['stepCostMs'])。"""
        s = getattr(self, 'sim_v4', None)
        name = str((s or {}).get('params', {}).get('precision')
                   or getattr(self.cfg, 'sim_precision', '400ms'))
        try:
            from simulator.simcore.core import precision_spec
            return float(precision_spec(name)[0])
        except Exception:
            return 0.0

    def _sim_cycle_prec(self, which: str) -> None:
        """循环强度显示精度档。

        风速: 5 → 1 → 0.1 kt(默认 1)
        气压: 0258 → 1 → 0.1 hPa(默认 1)
        **只影响显示**, 内核一律 float(0.1kt / 0.1hPa 精度)。
        """
        if which == 'vmax':
            seq = ('5', '1', '0.1')
            cur = str(getattr(self.cfg, 'sim_vmax_prec', '1'))
            self.cfg.sim_vmax_prec = seq[(seq.index(cur) + 1) % 3] if cur in seq else '1'
        else:
            seq = ('0258', '1', '0.1')
            cur = str(getattr(self.cfg, 'sim_pmin_prec', '1'))
            self.cfg.sim_pmin_prec = seq[(seq.index(cur) + 1) % 3] if cur in seq else '1'
        self._config_needs_save = True

    def _sim_notice(self, text: str) -> None:
        """模拟模式提示语。

        ⚠ 轮次 155: 改为走**共用的顶部 toast**(`show_toast`, 与风季同一个), 不再
        自绘在右下角。原因: 右下角与经纬度 HUD(`renderer._draw_coord_hud`, 同样
        右下角、后绘制)几何重叠, 提示会被 HUD 压住; 而共用 toast 在**顶部居中**
        堆叠(最多 3 条)、带级别配色 —— 顺带把"两套提示机制并存"也合并成一套。
        `_sim_msg`/`_sim_msg_t` 仍写入(供既有读取点/测试兼容)。
        """
        self._sim_msg = text
        self._sim_msg_t = pygame.time.get_ticks()
        try:
            self.show_toast(text, 'info')
        except Exception:
            logger.debug('模拟提示 toast 失败', exc_info=True)

    # ── 手动生成 / 选中 ──
    def _sim_spawn(self, la: float, lo: float, vmax_kt: float = 15.0) -> None:
        """手动生成一个**热带扰动**(默认 15kt = DB), 之后自己长成 TD/TS。

        用户 2026-09-19: "台风生成就是 TS, 而没有从 DB-TD-TS 的过程" ——
        手动生成也不该凭空造一个成熟台风; 名称在达到命名门槛时由内核 maybe_name
        自动赋(见 core.maybe_name)。
        """
        from simulator.simcore import core as V
        if self.sim_v4 is None:
            self._sim_reset(msg=False)
        tc = V.make_tc(self.sim_v4, lo, la, vmax_kt)
        self.sim_sel_tc_id = tc['id']
        self._sim_notice(f"已生成扰动 {tc['name']} [{tc['basin']}] "
                         f"@ {la:.1f}° {lo % 360:.1f}° {tc['vmax']:.0f}kt")

    def _sim_find_tc(self, mx: int, my: int):
        """屏幕点 → 命中的台风(容差 16px)。"""
        if self.sim_v4 is None:
            return None
        for tc in self.sim_v4['tcs']:
            if tc['dead']:
                continue
            sx, sy = self._sim_ll2scr(tc['lon'], tc['lat'])
            if math.hypot(sx - mx, sy - my) < 16:
                return tc
        return None

    def _sim_scr2ll(self, mx: int, my: int):
        """屏幕点 → 经纬度(经地图视图反算)。"""
        try:
            return self.screen_to_latlon(mx, my)
        except Exception:
            return None, None

    def _sim_ll2scr(self, lon: float, lat: float):
        # ⚠ 2026-10-01: 这是**连续移动位置**的逐帧投影(唯一调用点 `_sim_pick_tc` 用它
        # 命中 16px 内的台风)。图标本身已改用 `_icon_live_screen`(连续差值一次取整),
        # 这里同步, 保证"点得到的就是画出来的那个像素" —— 否则拾取圆与图标可差 ≤1px。
        # 不改观感: 只影响点击判定, 不参与任何绘制/缓存/地图空间。
        try:
            _fn = getattr(self, '_icon_live_screen', None)
            if _fn is not None:
                return _fn(lat, lon)
            return self.latlon_to_screen(lat, lon)
        except Exception:
            w = getattr(self, 'screen_width', 1600)
            h = getattr(self, 'map_height', 900)
            return (lon - 100.0) / 72.0 * w, (42.0 - lat) / 42.0 * h

    def _sim_in_domain(self, la, lo) -> bool:
        return (la is not None and -60 <= la <= 60 and lo is not None
                and 0 <= lo <= 360)

    # ── 图层名 ──
    def _sim_layer_short(self) -> str:
        """控制面板按钮标签(按钮宽 80px, 只容得下约 2 个汉字)。

        叠加模式下 sim_layer 只是"最后点的那个", 代表不了当前显示集合 ——
        按集合报: 1 个非路径层报名字, ≥2 个报"N层"(逐个报名字会溢出按钮)。
        """
        cur = set(self.sim_layers or set())
        names = [n for lid, n, _ in SIM_LAYERS if lid in cur and lid != 'track']
        if len(names) == 1:
            return names[0]
        if len(names) >= 2:
            return '%d层' % len(names)
        return '路径'

    # ── 导出(纯 b-deck 格式, ATCF 标准) ──
    #: 实时记录根(仓库根下, 与 tools/sim_year.DEFAULT_BDECK 同一处)
    BDECK_STAGE_ROOT = 'sim_typhoon'
    #: 「移入台风库」的目标根: typhoon/sim/<年>/<年><洋区>[_invest]/
    BDECK_LIB_ROOT = os.path.join('typhoon', 'sim')
    #: 扰动子目录后缀 —— 用户 2026-09-21: "扰动单独放在一个文件夹"
    BDECK_INVEST_TAG = '_invest'

    @staticmethod
    def _bdeck_lat(lat: float) -> str:
        """纬度 = 度×10 + N/S, **不留多余前导零**(用户 2026-09-21: "097N 就写 97N")。

        9.7N → `97N`; 13.8N → `138N`(**≥100 的照旧三位**, 不能写成 138→38);
        7.5S → `75S`。列宽靠 `%5s` 对齐, 不靠这里补零。
        """
        hemi = 'N' if float(lat) >= 0 else 'S'
        return '%d%s' % (int(round(abs(float(lat)) * 10)), hemi)

    @staticmethod
    def _bdeck_lon(lon: float) -> str:
        """经度 = 度×10 + E/W, 同样不留多余前导零: 119.6E → `1196E`, 9.04E → `904E`。"""
        lo = float(lon) % 360.0
        if lo > 180.0:
            return '%dW' % int(round((360.0 - lo) * 10))
        return '%dE' % int(round(lo * 10))

    # ── b-deck 记录目录的布局(扰动 / 正式 **分家**) ──
    # 用户 2026-09-21 的三条要求落在这里:
    #   · "扰动单独放在一个文件夹" → `<年>/<年><洋区>_invest/`;
    #   · "移入台风库按钮要把两个目录都处理掉" → _bdeck_move_plan 两个都列;
    #   · 落盘侧的目录名由 tools/sim_year 复用 _bdeck_subdir(口径只有这一处)。

    @classmethod
    def _bdeck_is_invest_dir(cls, name: str) -> bool:
        """`2025wp_invest` 这种扰动目录(与正式目录 `2025wp` 分开)。"""
        return str(name).lower().endswith(cls.BDECK_INVEST_TAG)

    @classmethod
    def _bdeck_subdir(cls, year, basin, invest: bool = False) -> str:
        """记录目录名: 正式 `2025wp`, 扰动 `2025wp_invest`。"""
        return '%d%s%s' % (int(year), str(basin).lower(),
                           cls.BDECK_INVEST_TAG if invest else '')

    @classmethod
    def _bdeck_parse_subdir(cls, year, name):
        """→ (洋区小写, 是否扰动目录); 不是本格式返回 (None, False)。

        `2025wp` → ('wp', False); `2025wp_invest` → ('wp', True);
        `2025other` / `2025-2026sh` → (None, False)(不认的不动)。
        """
        s = str(name).lower()
        y = str(int(year))
        if not s.startswith(y):
            return None, False
        rest = s[len(y):]
        invest = rest.endswith(cls.BDECK_INVEST_TAG)
        if invest:
            rest = rest[:-len(cls.BDECK_INVEST_TAG)]
        if len(rest) != 2 or not rest.isalpha():
            return None, False
        return rest, invest

    @classmethod
    def _bdeck_lib_dir(cls, year, basin, invest: bool = False) -> str:
        """该系统的**台风库**落点(相对仓库根)。"""
        return os.path.join(cls.BDECK_LIB_ROOT, str(int(year)),
                            cls._bdeck_subdir(year, basin, invest))

    @classmethod
    def _bdeck_move_plan(cls, stage_root=None):
        """「移入台风库」的搬家清单 → [(源目录, 目标目录, 是否扰动目录)]。

        两条线一起搬(用户 2026-09-21: "按钮要把两个目录都处理掉"):
          正式: `<stage>/<年>/<年><洋区>/`         → `typhoon/sim/<年>/<年><洋区>/`
          扰动: `<stage>/<年>/<年><洋区>_invest/`  → `typhoon/sim/<年>/<年><洋区>_invest/`

        纯计算(只看目录、不写盘), 所以能在测试里直接核对分流结果。
        """
        root = stage_root if stage_root is not None else cls.BDECK_STAGE_ROOT
        plan = []
        if not os.path.isdir(root):
            return plan
        for yr in sorted(os.listdir(root)):
            ydir = os.path.join(root, yr)
            if not (os.path.isdir(ydir) and len(yr) == 4 and yr.isdigit()):
                continue
            for sub in sorted(os.listdir(ydir)):
                sdir = os.path.join(ydir, sub)
                if not os.path.isdir(sdir):
                    continue
                plan.append((sdir,
                             os.path.join(cls.BDECK_LIB_ROOT, yr, sub),
                             cls._bdeck_is_invest_dir(sub)))
        return plan

    def _sim_export(self, interval_h: int = 6) -> str:
        """导出为纯 b-deck(BEST 报文)文本。行格式(ATCF):
        BASIN, CY, YYYYMMDDHH, , , BEST, TAU, LAT, LON, VMAX, MSLP, TY,
        RAD, WINDCODE, RAD1, RAD2, RAD3, RAD4, POCI, ROCI, RMW, ..."""
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'sim_output')
        os.makedirs(out_dir, exist_ok=True)
        fn = os.path.join(out_dir,
                          f"sim_bdeck_{datetime.now().strftime('%Y%m%d%H%M%S')}.bde")
        lines = []
        sim = self.sim_v4
        if sim is not None:
            # 模拟时间 → 真实日期(F1: 直接用内核的绝对起始时刻)
            from datetime import timedelta as _td
            base = sim.get('start') or datetime(2000, sim['month0'], 1)
            for cy, tc in enumerate(sim['tcs'], start=1):
                cat = tc.get('phase', 'TC')
                if cat == 'TC':
                    cat = self._smcy_cat(tc['vmax'])
                elif cat == 'SD':
                    cat = 'SS'
                # 按间隔采样(默认 6h)
                last_h = -1
                for q in tc['track']:
                    h = int(round(q['t']))
                    if h % max(1, interval_h) != 0 and h != last_h:
                        continue
                    if h == last_h:
                        continue
                    last_h = h
                    dt = base + _td(hours=h)
                    ts = dt.strftime('%Y%m%d%H')
                    lat_s = self._bdeck_lat(q['lat'])
                    lon_s = self._bdeck_lon(q['lon'])
                    vmax = int(round(q['vmax']))
                    pmin = int(round(q['pmin']))
                    # 风圈半径(34kt, 简化为整圆半径 km)
                    from simulator.simcore import core as V
                    r34 = int(round(V.radius_at_wind(tc, 34.0))) if tc['vmax'] >= 34 else 0
                    rmw = int(round(tc['rmw']))
                    line = (f"{tc['basin']}, {cy:02d}, {ts}, , , BEST,   0, "
                            f"{lat_s}, {lon_s}, {vmax:3d}, {pmin:4d}, {cat}, "
                            f"34, NEQ, {r34:3d}, {r34:3d}, {r34:3d}, {r34:3d}, "
                            f"1010, 200, {rmw:3d}, , , , , , , {tc['name'].upper()}, "
                            f", , , ,")
                    lines.append(line)
        with open(fn, 'w', encoding='ascii') as f:
            f.write('\n'.join(lines) + ('\n' if lines else ''))
        return fn

    # ── 步进(每帧) ──
    def _sim_unit_ms(self, sim, step_h: float) -> float:
        """一个**内步**的墙钟 ms 估算(用于单帧预算分配)。

        stepCostMs 是"每 6h 模拟时间"的成本(内核实测+指数平滑), 换算:
          每步 = stepCostMs × step_h/6;  每内步 = 每步 ÷ inner
        **再与档位目标取大**: 快档(50/100ms)靠 hold 省环境重建, 单步成本
        6 ms 与 190 ms 交替出现(实测 50ms 档: 均值 30、中位 7、最大 191),
        只看均值会让一帧塞进几十步, 撞上一次重建就长卡; 用"档位目标"当估算
        下限, 每帧最多干 预算÷目标 个内步 —— 帧成本就有了确定的量级。
        """
        try:
            from simulator.simcore.core import precision_spec
            _tgt, _inner, _h, _q = precision_spec(
                (sim.get('params') or {}).get('precision', '400ms'))
        except Exception:
            _tgt, _inner = 400.0, 1
        _inner = max(1, int(_inner))
        _per6 = float(sim.get('stepCostMs', 0.0) or 0.0)
        _tgt_per_unit = max(1.0, float(_tgt or 400.0) * (float(step_h) / 6.0) / _inner)
        _meas_per_unit = _per6 * (float(step_h) / 6.0) / _inner
        return max(_tgt_per_unit, _meas_per_unit, 1.0)

    def _sim_step_h(self) -> float:
        from simulator.simcore import core as V
        return float(V.step_hours(self.sim_v4))

    def _sim_inner_count(self) -> int:
        try:
            from simulator.simcore.core import precision_spec
            return max(1, int(precision_spec(
                (self.sim_v4.get('params') or {}).get('precision', '400ms'))[1]))
        except Exception:
            return 1

    def _sim_advance(self, dt: float) -> float:
        """一帧的推进: **显示钟是主时钟, 物理永远领先它一步**。

        用户 2026-09-19 的两条反馈都在这里解决:
          · "台风运动是跳跃的" —— 屏幕上的位置按显示钟在相邻两个**已算出**的
            报点之间插值, 所以必须保证显示钟 ≤ 物理钟 - 一步;
          · "时间轴一跳一跳" —— 显示钟按 sp×6 h/s 匀速走, 机器跟不上时它就
            **停在那儿等**(旧写法是冲到物理钟再被顶回来, 于是每步跳 6 小时)。

        顺序(与旧版的死锁坑):
          1. 本帧显示钟想走 Δ = sp·6·dt;
          2. 把物理推到 disp + Δ + 一步(否则显示钟会被上界卡住不动 ——
             两边互相等, 实测显示钟永远停在 0);
          3. 显示钟再前进 min(Δ, 物理 - 一步)。
        """
        from simulator.simcore import core as V
        sim = self.sim_v4
        step_h = self._sim_step_h()
        inner = self._sim_inner_count()
        t_phys = float(sim.get('t', 0.0))
        if not getattr(self, '_sim_disp_init', False):
            self._sim_disp_h = max(0.0, t_phys - step_h)
            self._sim_disp_init = True
        if not self.sim_playing:
            return 0.0
        d_h = self.sp * dt * SIM_HOURS_PER_SEC_AT_1X
        # ① 物理追到"显示钟 + Δ + 一步"
        target = self._sim_disp_h + d_h + step_h
        unit_h = step_h / inner
        done = 0
        spent0 = time.perf_counter()
        budget_units = max(1, int(SIM_FRAME_BUDGET_MS / self._sim_unit_ms(sim, step_h)))
        while t_phys < target - 1e-9 and done < SIM_MAX_UNITS_PER_FRAME:
            if done and done >= budget_units:
                break
            V.sim_step(sim, step_h, max_inner=1)
            done += 1
            t_phys = float(sim.get('t', 0.0))
        self._sim_last_budget_ms = (time.perf_counter() - spent0) * 1000.0
        # ② 显示钟前进(被"物理 - 一步"顶住就等)
        self._sim_disp_h = min(max(self._sim_disp_h + d_h, 0.0),
                               max(0.0, t_phys - step_h))
        return done * unit_h

    def _sim_track_rate(self, adv_h: float) -> None:
        """实测速度窗口(**每帧都记**, 空闲帧记 0 小时)。

        坑: 最初把这段写在 _sim_advance 里、"还没到步就 return"之后, 于是
        只有干活的帧才累计 —— 空闲帧的实时秒数没算进去, 测出来的速度虚高,
        而且稀疏档慢速时窗口很久填不满, 干脆显示不出有效倍速(实测 sp=1 时
        120 帧只累计到 0.2 s 的"干活时间")。
        窗口取 3 s: 推进是按 6h 整步量化的, 窗口太短(0.5 s)会让"有效倍速"
        在 0/12/24 h/s 之间跳 —— 实测 sp=0.5(请求 18 h/s)被显示成 0.42×。

        ⚠ 轮次 153: **不再收 `dt` 形参**。原来那个 `dt` 是本函数算错的根源
        (见下), 留着同名参数迟早被写回去; 调用方现在只传 `adv_h`。

        ⚠⚠ **轮次 151 修掉的缺陷**: 原来这里累加的是 **`dt`**, 而调用方传进来的
        `dt` 是 `main.py` 算的"上一帧真实耗时", **上限被钳到 0.1 s**
        (`dt = min(tick/1000, 0.1)`)。只要有一帧超过 100 ms(默认档下几乎每帧都
        超), 这个窗口就把 1 秒记成 0.1 秒 ⇒ 测出来的速度**系统性虚高**。
        实测: 面板显示 `1× = 6 h/s`(有效 0.999×), 而真实推进只有 **0.229×**
        —— 高估 4.4 倍, 于是"速度 8×"是个谎, 面板那条"请求× → 有效×"的
        红字告警也**永远不会出现**(偏差被钳掉的 dt 掩盖了)。

        改法: 用 **墙钟间隔**(`time.perf_counter()` 差值)代替被钳的 `dt`。
        本函数在播放时**每帧恰好调用一次**(见 `_sim_update`), 所以相邻两次
        调用的间隔就是真实帧时间, 含物理 + 渲染 + 事件处理 —— 这才是用户
        感知的"每秒走多少模拟小时"。
        ⚠ 轮次 153 补: 暂停 / 开着对话框时 `_sim_update` **不调用**本函数, 那
        条分支会主动把 `_sim_rate_t_prev` 置 None(见 `_sim_update` 的 else 分支),
        否则恢复后第一帧会把整段暂停时长算成"推进耗时", 弹出虚假红字。
        下面 `_el > 2.0` 的丢弃只兜底长空档, 不能替代那个作废动作。
        """
        _now = time.perf_counter()
        _prev = getattr(self, '_sim_rate_t_prev', None)
        self._sim_rate_t_prev = _now
        if _prev is None:
            return                       # 还没有上一个采样点, 不产生样本
        _el = _now - _prev
        # 暂停/对话框/窗口拖动造成的长空档不该算进"速度", 丢弃 >2 s 的间隔
        if _el <= 1e-6 or _el > 2.0:
            return
        self._sim_rate_h += adv_h
        self._sim_rate_s += _el
        if self._sim_rate_s >= 3.0:
            _inst = self._sim_rate_h / self._sim_rate_s
            self._sim_rate_hps = (_inst if self._sim_rate_hps <= 0.0
                                  else self._sim_rate_hps
                                  + (_inst - self._sim_rate_hps) * 0.5)
            self._sim_rate_h = 0.0
            self._sim_rate_s = 0.0

    def _sim_rate_reset(self) -> None:
        """清掉测速窗口(改速度/改档位/重置后立刻反映新设置, 不带着旧均值)。"""
        self._sim_rate_hps = 0.0
        self._sim_rate_h = 0.0
        self._sim_rate_s = 0.0
        self._sim_rate_t_prev = None     # 轮次 151: 同时清掉墙钟采样点

    def _sim_effective_x(self) -> float:
        """实测有效倍速(基准: 1× = 每秒 6 模拟小时)。

        与实际请求的 self.sp 不同时, 说明机器吃不下当前档位/速度 —— UI 要
        如实显示, 否则"速度 8×"就是个谎。**上限钳到请求值**: 调度是需求驱动
        的(欠账封顶一步), 平均推进不可能真的快过请求, 超过只能是测量尖峰。
        """
        sim = getattr(self, 'sim_v4', None)
        if not isinstance(sim, dict):
            return 0.0
        if self._sim_rate_hps <= 0.0:
            return 0.0
        base = SIM_HOURS_PER_SEC_AT_1X
        return min(self._sim_rate_hps / base, float(self.sp))

    # ── 显示钟(连续播放) ──
    def _sim_display_sim(self):
        """给渲染用的 sim 视图: 台风位置/强度按**显示钟**插值(不动物理状态)。

        返回浅拷贝 dict(台风条目是浅拷贝 + 覆盖 lat/lon/vmax/pmin/rmw), 其它
        键共享 —— render_map 只读, 不会写回物理状态。
        """
        sim = self.sim_v4
        if not isinstance(sim, dict):
            return None
        try:
            t = float(self._sim_disp_h)
            if t <= 0.0:
                return sim
            out = dict(sim)
            tcs = []
            for tc in sim.get('tcs', []):
                tr = tc.get('track') or []
                if tc.get('dead') or len(tr) < 2:
                    tcs.append(tc)
                    continue
                # 二分找 t 落在哪一段
                lo, hi = 0, len(tr) - 1
                if t <= float(tr[0]['t']):
                    q = dict(tc)
                elif t >= float(tr[-1]['t']):
                    q = dict(tc)
                else:
                    while hi - lo > 1:
                        mid = (lo + hi) // 2
                        if float(tr[mid]['t']) <= t:
                            lo = mid
                        else:
                            hi = mid
                    a, b = tr[lo], tr[hi]
                    span = max(1e-9, float(b['t']) - float(a['t']))
                    w = min(1.0, max(0.0, (t - float(a['t'])) / span))
                    q = dict(tc)
                    # 只插值**两个端点都有的键** —— 报点里没有 rmw(它有
                    # t/lon/lat/vmax/pmin/name/named/phase), 原来无条件插 rmw
                    # 会 KeyError, 被外层 except 吞掉后退回物理状态 ——
                    # 表现就是"台风运动仍然是跳跃的"(插值根本没生效)。
                    for _k in ('lat', 'lon', 'vmax', 'pmin', 'rmw'):
                        if _k in a and _k in b:
                            q[_k] = float(a[_k]) + (float(b[_k]) - float(a[_k])) * w
                tcs.append(q)
            out['tcs'] = tcs
            out['t'] = t
            return out
        except Exception:
            logger.debug('显示插值失败(退回物理状态)', exc_info=True)
            return sim

    def _sim_calendar_disp(self):
        """显示钟对应的日历(start + 显示小时数)。"""
        sim = self.sim_v4 or {}
        base = sim.get('start')
        try:
            from datetime import timedelta
            return base + timedelta(hours=float(self._sim_disp_h))
        except Exception:
            return self._sim_datetime(sim)

    def _sim_rate_text(self) -> str:
        """速度文本(红字显示在速度条上方, 见 ControlPanel.draw):

        正常 → '1× = 6 h/s'; 机器吃不下当前档位/速度 → '8× → 有效 0.58×'。
        用户 2026-09-19 定: 调速只留快捷键 + 速度条, 反馈就写成这一行红字。
        """
        _eff = self._sim_effective_x()
        if _eff > 0.0 and abs(_eff - self.sp) > max(0.05, 0.08 * self.sp):
            return '%g× → 有效 %.2f×' % (self.sp, _eff)
        return '%g× = %.0f h/s' % (self.sp, self.sp * SIM_HOURS_PER_SEC_AT_1X)

    def _sim_update(self, dt: float) -> None:
        if self.sim_v4 is None:
            return
        # 粒子密度设置变化时重建
        want = self._sim_particle_count()
        if self.sim_particles is not None and self.sim_particles.n != want:
            from simulator.simcore.render import Particles
            self.sim_particles = Particles(want)
        dialog_open = bool(getattr(self, 'dialog_mgr', None)
                           and self.dialog_mgr.any_active())
        if self.sim_playing and not dialog_open:
            # 显示钟 + 物理(物理领先一步): 见 _sim_advance 的说明
            hpf = self._sim_advance(dt)
            if hpf > 0.0:
                self._sim_rewind_tick()       # 轮次 158: 按间隔存回放快照
            self._sim_track_rate(hpf)         # 空闲帧也要计实时秒(否则速度虚高)
            # 云场已停算(模拟模式不要云图): 原 cloud_step 单次 34ms, 每 0.25 模拟
            # 小时触发一次纯属白烧; 冷尾流衰减/平流已移入 core.ocean_step
            # 只有风场图层可见时才推进粒子: 原实现无条件 step(实测 63ms/帧)
            if self.sim_particles is not None and 'wind' in self.sim_layers:
                self.sim_particles.step(self.sim_v4, min(hpf, 0.5))
            # 图标自旋动画与 SMCY 帧推进(照其它模式 update_rotation)
            mf = getattr(self, 'main_rotation_speed', 1.0)
            for tc in self.sim_v4['tcs']:
                if tc['dead']:
                    continue
                ict = self.sim_icon_tys.get(tc['id'])
                if ict is None:
                    ict = _SimIconTy(tc['lat'] < 0)
                    self.sim_icon_tys[tc['id']] = ict
                ict.sa = (ict.sa + 180.0 * dt * mf) % 360.0
        else:
            # 轮次 153: 暂停 / 开着对话框时不采样速度, 必须把采样点作废。
            # `_sim_track_rate` 只在上面那条分支里被调用, 采样点会停在暂停前那一帧;
            # 恢复后的第一帧于是把整段空档(只要 ≤2 秒, 而 2 秒是丢弃阈值)算成
            # "推进耗时", 而分子只加了恢复帧的 adv_h ⇒ 面板会弹出**虚假**的
            # "请求 1× → 有效 0.6×" 红字(约 3 秒后自愈)。
            self._sim_rate_t_prev = None

        # 副高图层打开后的一次性检查(延迟半秒, 等渲染把 RIDGE_VISIBLE_PX 更新出来)
        _rc = getattr(self, '_sim_ridge_check_at', 0)
        if _rc and pygame.time.get_ticks() >= _rc:
            self._sim_ridge_check_at = 0
            if 'ridge' in (self.sim_layers or set()):
                try:
                    from simulator.simcore.render import RIDGE_VISIBLE_PX
                    if int(RIDGE_VISIBLE_PX[0]) <= 0:
                        self._sim_notice('副高中心不在当前视野(把地图移向西太北部)')
                except Exception:
                    pass

    # ── 事件 ──
    def _sim_handle_event(self, e) -> bool:
        if e.type != pygame.KEYDOWN:
            return False
        if getattr(e, 'mod', 0) & (pygame.KMOD_CTRL | pygame.KMOD_ALT | pygame.KMOD_META):
            return False
        k = e.key
        for lid, _, key in SIM_LAYERS:
            if k == getattr(pygame, 'K_' + key):
                self._sim_set_layer(lid)
                return True
        if k == pygame.K_SPACE:
            self.sim_playing = not self.sim_playing
            self._sim_sync_play_text()
            return True
        if k == pygame.K_g:
            self._sim_spawn(17.0, 131.5)
            return True
        if k == pygame.K_n:
            return self._sim_toggle_natural_gen()
        if k == pygame.K_r:
            self._sim_reset()
            return True
        if k == pygame.K_d:
            # 切换数据驱动模式(真实气候库环境场 / 解析公式)
            return self._sim_toggle_data_mode()
        if k == pygame.K_e:
            fn = self._sim_export(6)
            self._sim_notice(f"已导出: {os.path.basename(fn)}")
            return True
        # 轮次 158: 单步(.) / 骰子种子(;) —— 实现在独立的 SimStepControlMixin 里,
        # 这里只挂一行。放在**最后**: 让它只能接手上面都没认领的按键。
        if self._sim_step_control_key(k):
            return True
        # 轮次 158: 时间轴回放 —— **只有 `,` 回溯, 没有"前进"键**(有意):
        # `RewindStore.restore()` 会 `_drop_after` 丢掉恢复点之后的全部快照(线性
        # 时间轴语义), 所以"前进"在回溯后永远是死键。继续的方式是**播放**(SPACE)。
        # ⚠ 这行原来写的是"`,` 回溯 / `/` 前进" —— 与实现不符(过期注释),
        # 已由 verifier 的文档审计查出(task-15 / DOC_AUDIT_R162.md)。
        if self._sim_rewind_key(k):
            return True
        return False

    def _sim_handle_click(self, mx: int, my: int) -> bool:
        """地图左键点击: 选台风 / (可选)生成。

        用户 2026-09-19: "不要点击能生成台风(至少做成可开关的)" —— 默认
        **关闭**点地生成(cfg.sim_click_spawn 默认 False), 点空白处只取消选中;
        想恢复点地生成: 设置面板"点击生成"开关, 或快捷键 G 直接生成。
        """
        if self.sim_v4 is None:
            self._sim_reset(msg=False)
        tc = self._sim_find_tc(mx, my)
        if tc is not None:
            self.sim_sel_tc_id = tc['id']
            self._sim_notice(f"跟踪: {tc['name']} {tc['vmax']:.0f}kt")
            return True
        la, lo = self._sim_scr2ll(mx, my)
        if not self._sim_in_domain(la, lo):
            return False
        if not bool(getattr(self.cfg, 'sim_click_spawn', False)):
            self.sim_sel_tc_id = None             # 空白处点一下 = 取消选中
            self._sim_notice("点击生成已关闭(面板可开, 或按 G 生成)")
            return True
        self._sim_spawn(la, lo)
        return True

    def _sim_spawn_at_screen(self, mx: int, my: int) -> bool:
        """屏幕坐标处生成一个扰动(中键用)。域外返回 False。"""
        try:
            la, lo = self._sim_scr2ll(mx, my)
        except Exception:
            return False
        if not self._sim_in_domain(la, lo):
            self._sim_notice('域外不能生成台风')
            return False
        self._sim_spawn(la, lo)
        return True

    def _sim_sync_play_text(self) -> None:
        try:
            self.play_text = rt(f_s, '暂停' if self.sim_playing else '播放', (255, 255, 255))
            self._panel = None
        except Exception:
            pass

    # ── 渲染(叠加到地图) ──
    def _sim_setup_render_font(self) -> None:
        """把 app 的字体渲染注入 simcore(否则它落到 SysFont 兜底, 本机会崩)。"""
        try:
            from simulator.simcore import render as R
            if getattr(self, '_sim_font_injected', False):
                return
            R.set_label_fn(lambda text, color: rt(f_s, text, color))
            self._sim_font_injected = True
        except Exception:
            logger.debug('simcore 字体注入失败', exc_info=True)

    def _sim_degrade_level(self) -> int:
        """帧预算自适应降级等级(0=全画)。用渲染耗时的指数滑动平均判定。

        16.7ms 是 60FPS 的一帧预算: 持续超过 ~1.15 倍砍副高, 超过 ~1.6 倍再砍
        风羽; 耗时回落自动逐级恢复(EMA 本身带滞后, 不会来回抖)。
        """
        ms = getattr(self, '_sim_render_ms', 0.0)
        if ms > 16.7 * 1.6:
            return 2
        if ms > 16.7 * 1.15:
            return 1
        return 0

    def _sim_grid_map_pad(self, surface):
        """色阶类图层的映射: 域扩到 ±90(数组已按 _pad_poles 补齐, 两极不再是空白)。"""
        from simulator.simcore import render as R
        return self._sim_grid_map(surface, lat0=R.LAYER_LAT0, lat1=R.LAYER_LAT1)

    def _sim_grid_map(self, surface, lat0=None, lat1=None):
        """构造格点类图层用的**仿射**映射(见 render.GridMap)。

        背景: app 的点映射 geo_to_screen 会按"离屏幕中心最近的副本"对每个点翻面,
        用于台风位置是对的, 但用于整张 0.5° 格点场就是灾难 —— fx(0) 与 fx(360)
        解到同一像素, sst/ohc 贴图宽度算成 0, 整层永远画不出来(实测平均像素差
        0.12/0.20); 风羽还会因逐点翻面在阈值附近每帧跳一个图宽("闪烁")。
        这里只取投影的**仿射部分**(不含环绕), 让 render 侧一次性选好副本。
        """
        from simulator.simcore import render as R
        from simulator.simcore.core import C as _C
        try:
            mv = self.view._map_mgr.map_view
            ox, oy = mv._draw_offset()
            vx = int(mv.view_x * mv.scale)
            vy = int(mv.view_y * mv.scale)
            k = mv.scale

            def x_aff(lon):
                return int(((lon - mv.lon_min) * mv._scale_x) * k) - vx + ox

            def y_aff(lat):
                return int(((mv.lat_max - lat) * mv._scale_y) * k) - vy + oy

            wrap = mv.wrap_px()
            clip = (0, 0, surface.get_width(), getattr(self, 'map_height',
                                                       surface.get_height()))
            _la0 = _C['LAT0'] if lat0 is None else float(lat0)
            _la1 = _C['LAT1'] if lat1 is None else float(lat1)
            return R.GridMap(_C['LON0'], _C['LON1'], _la0, _la1,
                             x_aff(_C['LON0']), x_aff(_C['LON1']),
                             y_aff(_la1), y_aff(_la0),
                             wrap=wrap, clip=clip)
        except Exception:
            logger.debug('格点映射构造失败(退回点映射)', exc_info=True)
            return None

    def _sim_render(self, surface) -> None:
        """在地图 surface 上叠加 SimCore 图层(renderer 已先绘制地图底图)。"""
        if self.sim_v4 is None:
            return
        from simulator.simcore import render as R
        self._sim_setup_render_font()
        # 全球计算域(0-360E, 60S-60N) → 地图视图映射(等距圆柱近似)
        try:
            def fx(lon):
                return self.latlon_to_screen(20.0, lon % 360.0)[0]

            def fy(lat):
                return self.latlon_to_screen(lat, 120.0)[1]
        except Exception:
            def fx(lon):
                return (lon % 360.0) / 360.0 * surface.get_width()

            def fy(lat):
                return (60.0 - lat) / 120.0 * surface.get_height()

        grid = self._sim_grid_map(surface)
        grid_pad = self._sim_grid_map_pad(surface)
        # 渲染用**显示视图**: 台风位置/强度按显示钟插值(物理已算到下一步),
        # 于是"实时播放的是上一步"且画面连续; 物理状态不被触碰。
        disp = self._sim_display_sim() or self.sim_v4

        sel = None
        if self.sim_sel_tc_id is not None:
            for tc in disp['tcs']:
                if tc['id'] == self.sim_sel_tc_id:
                    sel = tc
                    break
        # 帧预算自适应: **默认关闭**(2026-09-19 用户反馈"风场会闪烁显示")。
        # 原实现用 16.7ms(60FPS 预算)判定, 实测本机单帧渲染 20-100ms → 长期
        # 处于"砍副高/砍风羽"档, 而 EMA 一回落又把它们放回来 → 用户选中的图层
        # 忽隐忽现, 看起来就是"风羽在闪"。图层显隐只应由用户开关决定。
        layers = self.sim_layers
        degrade = (self._sim_degrade_level()
                   if getattr(self, 'sim_auto_degrade', False) else 0)
        if degrade >= 1:
            layers = layers - {'ridge'}
        prev_barbs = self.sim_v4.get('_show_barbs', True)
        if degrade >= 2:
            self.sim_v4['_show_barbs'] = False
        t0 = time.perf_counter()
        try:
            R.render_map(surface, disp, fx, fy, layers,
                         sel_tc=sel, mode=self.sim_cloud_mode,
                         particles=self.sim_particles,
                         # 图标不在这里画: 由其它模式的 draw_typhoon_info 统一画
                         # (图标集/名字样式/信息框与风季模式完全一致); 这里只要
                         # 风圈/四象限环/高亮
                         icon_fn=_no_icon,
                         grid=grid, grid_pad=grid_pad,
                         hud_labels=False)
        except Exception:
            logger.debug("模拟模式图层渲染失败", exc_info=True)
        finally:
            self.sim_v4['_show_barbs'] = prev_barbs
            dt_ms = (time.perf_counter() - t0) * 1000.0
            prev = getattr(self, '_sim_render_ms', dt_ms)
            self._sim_render_ms = prev * 0.85 + dt_ms * 0.15
            self._sim_degrade = degrade
        if degrade:
            _txt = rt(f_s, '图层降级: ' + ('风羽/副高' if degrade >= 2 else '副高'),
                      (255, 210, 120))
            surface.blit(_txt, (10, 26))
        # ── 台风本体: 完全复用其它模式的管线 ──
        # 用户 2026-09-19: "照搬其它模式的图标和路径…不会复用?" —— 这里不再自己
        # 画路径/图标/名字, 而是把 sim 台风映射成 Typhoon 对象后调用其它模式那两个
        # 入口: draw_typhoon(路径+活线段+登陆标记+高亮) 与 draw_typhoon_info
        # (图标+名字+信息框)。名字样式、图标集、路径样式、信息框从此与风季模式一致。
        if 'track' in self.sim_layers:
            for tc in disp['tcs']:
                if len(tc['track']) < 1:
                    continue
                try:
                    ty = self._sim_sync_path_ty(tc)
                    # ⚠⚠ 轮次 155: 这里**原来每帧把 `ty.pts` 截断到 ci+1**
                    # ("只画已经播过的部分")。那个做法同时打断了四个共享函数的
                    # 前置条件, 而且路径缓存键含 `id(ty.pts)` ⇒ 每帧新建 list ⇒
                    # 每帧重建整张路径面:
                    #   · 活线段  `_draw_path_mixin` 要求 `ci < n_pts-1` ⇒ 恒假, 恒不画
                    #   · 类别过渡 前半段要求 `ci+1 < len(pts)` ⇒ 不可达, 换级前 3h 无预警
                    #   · 图标预载 同一条件 ⇒ 恒不执行(切类别那一帧首次解码卡顿回到帧上)
                    #   · 信息框 `pts[-1]` ⇒ 退化成"起点–当前"而不是"起点–终点"
                    # 现在这件事交给语义正确的开关: `_show_future()` 对 MODE_SIM
                    # 一律返回 False(模拟没有"未来段", 物理领先那几步不该画) ——
                    # 路径本来就只把**已走段**画到 `cur_idx`, 所以删掉截断后画面不变,
                    # 四个前置条件一起恢复, 缓存键也恢复稳定。
                    # `highlight` = 是否被鼠标点选。原来的条件是**同一个表达式
                    # 写了两遍**(`a or a`, 恒等于 a), 已去掉重复。
                    # ⚠ 仍与风季有语义差: 风季恒有"当前台风"⇒ 永远恰好一只是高亮,
                    # 而模拟模式只有点选才高亮 ⇒ 不点选时没有高亮定位点。
                    # (待办, 见 ROUND_LOG 轮次 153 §3 #12)
                    self.draw_typhoon(surface, ty,
                                      highlight=(tc['id'] == self.sim_sel_tc_id))
                except Exception:
                    logger.debug("模拟模式路径渲染失败", exc_info=True)
        # ── 图标 + 名字 + 信息框: 全部照搬风季模式 ──
        # draw_typhoon_info = 图标 + 名字(风季那套样式/字号); 信息框用
        # _render_info_box(风季信息框本体, 含模拟模式的"预报"行)。只画**选中**
        # 的那一只, 免得十几个框铺满屏幕。
        # ⚠ 轮次 151: 整段必须**和路径一起受 'track' 门控**。原先它在这道门之外,
        # 于是按数字键切到别的图层(路径消失)时, 图标/名字/信息框仍留在屏幕上 ——
        # 实测残留 142,803 px(19.04% 地图), 用户看到"幽灵台风"。
        if 'track' in self.sim_layers:
            try:
                _sel_ty = None
                for tc in disp['tcs']:
                    if not (tc.get('track') or []):
                        continue
                    ty = self.sim_path_tys.get(tc['id'])
                    if ty is None:
                        continue
                    # "台风标签"开关(app 设置)在这里生效: 关掉就不画名字
                    self.draw_typhoon_info(
                        surface, ty,
                        with_name=bool(self._sim_display('_show_labels', True)))
                    if tc['id'] == self.sim_sel_tc_id:
                        _sel_ty = ty
                if _sel_ty is not None:
                    _h = self._season_info_box_height()
                    if getattr(_sel_ty, 'fcPeak', None) or getattr(_sel_ty, 'fcLand', None):
                        _h += self._season_info_box_line_height()   # 多一行"预报"
                    # ⚠⚠ 轮次 156: 改用风季那套**共用**的框渲染 `blit_info_box`。
                    # 原来这里直接调 `_render_info_box(_sel_ty, 470, _h)` 又只 blit
                    # `_res[0]`, 丢掉 `ace_x/ace_y/tc` ⇒ 框里"实时ACE:"后面**永远空白**;
                    # 宽度还是硬编码 470(风季常量 350), 位置也是写死的 (15,244)。
                    self.blit_info_box(surface, _sel_ty,
                                       SEASON_INFO_BOX_WIDTH, _h,
                                       SEASON_INFO_BOX_START_X,
                                       SEASON_INFO_BOX_START_Y,
                                       self._box_alpha(_sel_ty,
                                                       pygame.time.get_ticks()))
            except Exception:
                logger.debug("模拟模式图标/信息框渲染失败", exc_info=True)
        self._sim_draw_hud(surface)
        self._sim_draw_probe(surface)
        # 提示语不再自绘: `_sim_notice` 走共用顶部 toast(见该函数)。
        # 原来这里在右下角画 `_sim_msg`, 与经纬度 HUD 重叠、且是第二套提示机制。

    # ── 台风图标(复用 app 图标集: 简单图标或 SMCY) ──
    @staticmethod
    def _smcy_cat(v_kt: float) -> str:
        """kt 等级 → SMCY 类别(SMSS: TD/TS/C1-C5)。"""
        if v_kt >= 137:
            return 'C5'
        if v_kt >= 113:
            return 'C4'
        if v_kt >= 96:
            return 'C3'
        if v_kt >= 83:
            return 'C2'
        if v_kt >= 64:
            return 'C1'
        if v_kt >= 34:
            return 'TS'
        return 'TD'

    def _sim_draw_tc_icon(self, surface, tc, x, y, sel) -> None:
        """台风图标: 完全复用其它模式的图标管线(_draw_simple_icon/_draw_smcy_frame),
        含自旋动画(ra+sa)、TS 渐变/C5 紫色滤镜、EX 放大、半球选择。"""
        try:
            from app.constants import ICON_SET_SMCY
            v_kt = tc['vmax']
            cat = tc.get('phase', 'TC')
            if cat == 'TC':
                cat = self._smcy_cat(v_kt)
            elif cat == 'SD':
                cat = 'SS'
            # 轻量图标对象(自旋角由 _sim_update 推进)
            icon_ty = self.sim_icon_tys.get(tc['id'])
            if icon_ty is None:
                icon_ty = _SimIconTy(tc['lat'] < 0)
                self.sim_icon_tys[tc['id']] = icon_ty
            icon_factor = getattr(self, 'icon_size', 100) / 100.0
            cp = {'w': int(round(v_kt)), 'st': cat, 'cat': cat,
                  'la': tc['lat'], 'lo': tc['lon']}
            if self.cfg.icon_set == ICON_SET_SMCY:
                self._advance_smcy_frame(icon_ty, cat, pygame.time.get_ticks(), self.sp)
                self._draw_smcy_frame(surface, icon_ty, cat, icon_ty.v._smcy_frame,
                                      x, y, icon_factor, 255, int(round(v_kt)))
            else:
                self._draw_simple_icon(surface, icon_ty, cat, cp, x, y,
                                       icon_factor, 255)
        except Exception:
            logger.debug("模拟模式图标绘制失败", exc_info=True)

    # ── 左上角时间轴(圆环钟) + 季节样式信息框 ──
    def _sim_datetime(self, sim):
        """模拟时刻 → datetime(F1: 由内核的绝对起始时刻 + t 得出)。"""
        from simulator.simcore import core as V
        try:
            return V.clock(sim)
        except Exception:
            from datetime import datetime as _dt, timedelta
            return _dt(2000, int(sim.get('month0', 1)), 1) + \
                timedelta(hours=float(sim.get('t', 0.0)))

    def _sim_calendar(self, sim, disp: bool = False) -> tuple:
        """模拟时间 → 真实日历 (年, 月, 日, 时)。

        disp=True 用**显示钟**(连续播放, 见 _sim_disp_target): 左上角时钟与
        底部面板都要连续走, 否则只在整步落地时跳 6 小时。
        """
        cur = self._sim_calendar_disp() if disp else self._sim_datetime(sim)
        return cur.year, cur.month, cur.day, cur.hour

    def _sim_probe_value(self):
        """光标所在经纬度上的当前图层数值(悬停取值)。"""
        try:
            if getattr(self, 'md', None) != getattr(self, 'MODE_SIM', 'sim'):
                return None
            if self.sim_v4 is None:
                return None
            mx, my = pygame.mouse.get_pos()
            if my > getattr(self, 'map_height', 10 ** 9):
                return None
            la, lo = self._sim_scr2ll(mx, my)
            if not self._sim_in_domain(la, lo):
                return None
            env = self.sim_v4['env']
            layer = str(getattr(self, 'sim_layer', '') or '')
            # ⚠ 轮次 156: `sim_layer` 在**关闭**图层时也会被写(两条入口都写), 而
            # 它只是"最近碰过的那个层"; 真正的可见集合是 `sim_layers`。
            # 原来只读 `sim_layer` ⇒ 关掉海温之后悬停**仍然报海温的数值**。
            if layer not in (self.sim_layers or set()):
                return None
            _name = {'sst': 'SST', 'ohc': 'OHC', 'shear': '风切', 'pressure': '气压',
                     'rh': '湿度'}.get(layer)
            if _name is None:
                return None
            from simulator.simcore.core import env_bilinear
            if layer == 'pressure':
                arr = env.get('pDisp', env.get('pTot'))
                if arr is None:
                    return None
                v = float(env_bilinear(arr, lo, la))
                return '%s %.1f hPa' % (_name, v)
            if layer == 'ohc':
                ocn = self.sim_v4.get('ocn') or {}
                arr = ocn.get('ohc_kj', env.get('ohc'))
            else:
                arr = env.get(layer)
            if arr is None:
                return None
            v = float(env_bilinear(arr, lo, la))
            if layer == 'sst':
                return '%s %.1f °C' % (_name, v)
            if layer == 'shear':
                return '%s %.1f kt' % (_name, v)
            return '%s %.0f' % (_name, v)
        except Exception:
            logger.debug('悬停取值失败', exc_info=True)
            return None

    def _sim_draw_probe(self, surface) -> None:
        """在光标旁显示当前图层的数值(鼠标悬停)。"""
        txt = self._sim_probe_value()
        if not txt:
            return
        try:
            mx, my = pygame.mouse.get_pos()
            img = rt(f_s, txt, (255, 255, 255))
            bg = pygame.Surface((img.get_width() + 10, img.get_height() + 6),
                                pygame.SRCALPHA)
            bg.fill((0, 0, 0, 175))
            x = min(max(4, mx + 14), surface.get_width() - bg.get_width() - 4)
            y = min(max(4, my + 14), surface.get_height() - bg.get_height() - 4)
            surface.blit(bg, (x, y))
            surface.blit(img, (x + 5, y + 3))
        except Exception:
            logger.debug('模拟模式悬停取值绘制失败', exc_info=True)

    def _sim_draw_flow_legend(self, surface) -> None:
        """环流/洋流图例(用户 2026-09-19: "洋流/环流就几条, 完全不知道是什么")。

        除了把种子撒进可见窗口(线多了), 还要**说清是什么**: 标题 + 色标(kt)。
        风场流线用风速色标(0-60kt), 洋流用表层流速(0-2kt)。
        """
        try:
            layers = set(self.sim_layers or set())
            want = []
            if 'flow' in layers:
                want.append(('环流(风场流线)', 0.0, 60.0, 'kt'))
            if 'current' in layers:
                want.append(('洋流(表层流)', 0.0, 2.0, 'kt'))
            if not want:
                return
            from simulator.simcore.render import _wind_color
            dark = getattr(self, 'dark_mode', True)
            sw = getattr(self, 'screen_width', surface.get_width())
            sh = getattr(self, 'map_height', surface.get_height())
            bw, bh = 216, 26 + 22 * len(want)
            panel = pygame.Surface((bw, bh), pygame.SRCALPHA)
            bg = (16, 22, 36, 200) if dark else (245, 245, 250, 225)
            bd = (55, 85, 130) if dark else (120, 140, 170)
            tc_c = (215, 225, 245) if dark else (30, 40, 60)
            dim = (140, 155, 180) if dark else (120, 130, 150)
            pygame.draw.rect(panel, bg, panel.get_rect(), 0, 8)
            pygame.draw.rect(panel, bd, panel.get_rect(), 1, 8)
            y = 6
            for title, lo, hi, unit in want:
                panel.blit(rt(f_s, title, tc_c), (10, y))
                y += 17
                bx, bwid, bhgt = 10, bw - 20, 8
                for i in range(bwid):
                    f = i / max(1, bwid - 1)
                    c = _wind_color(lo + (hi - lo) * f)
                    pygame.draw.line(panel, c, (bx + i, y), (bx + i, y + bhgt))
                pygame.draw.rect(panel, bd, (bx, y, bwid, bhgt), 1)
                panel.blit(rt(f_s, '0', dim), (bx, y + bhgt + 1))
                lab = rt(f_s, '%g %s' % (hi, unit), dim)
                panel.blit(lab, (bx + bwid - lab.get_width(), y + bhgt + 1))
                y += bhgt + 8
            surface.blit(panel, (max(0, sw - bw - 12),
                                 max(0, sh - bh - 40)))
        except Exception:
            logger.debug('流场图例绘制失败', exc_info=True)

    def _sim_draw_stats_box(self, surface) -> None:
        """左下角统计框(用户 2026-09-19 定的内容)。

        替代原来的"时间轴 + ACE 条 + 存活/命名"面板:
          · 时间轴交给左上角圆环钟(连续显示钟), ACE 交给和其它模式同一套 ACE 条,
            所以旧面板整个删掉;
          · 新框只报三件事: **总命名数**、**目前存活数**、**本风季最强风暴**
            —— 最强按南北半球分开(用户明确要求)。
        """
        try:
            sim = self.sim_v4
            if not sim:
                return
            sh = getattr(self, 'map_height', 900)
            dark = getattr(self, 'dark_mode', True)
            # 用户 2026-09-19: "左下角的框没有统计到生成的 tc, 任何时刻都是 0,0,-,-"
            # 根因是内核把 dead 台风从列表里删掉了(现在保留), 另外**命名与否要看
            # tc['named'] 而不是当前 vmax** —— 减弱到 34kt 以下的命名风暴仍是
            # "总命名"里的一个, 原来的 vmax>=34 口径会把它们漏掉。
            tcs = [t for t in sim.get('tcs', [])]
            alive = [t for t in tcs if not t['dead']]
            named_all = [t for t in tcs if t.get('named')]
            named_alive = [t for t in alive if t.get('named')]

            # **本年最强** = 整年历史里出现过的最强(遍历每条轨迹的所有报点),
            # 不是"这一报最强的那个台风"(用户 2026-09-19 指出)
            def _strongest(hemi):
                best = None
                for t in named_all:
                    for q in (t.get('track') or []):
                        if (float(q.get('lat', 0.0)) >= 0.0) != (hemi == 'N'):
                            continue
                        v = float(q.get('vmax', 0.0))
                        if best is None or v > best[0]:
                            best = (v, t.get('name', '?'))
                return '%s %.0fkt' % (best[1], best[0]) if best else None

            north = [t for t in named_all if float(t.get('lat', 0.0)) >= 0.0]
            south = [t for t in named_all if float(t.get('lat', 0.0)) < 0.0]
            lines = [
                ('总命名', str(len(named_all)), (150, 205, 255)),
                ('存活', '%d(命名 %d)' % (len(alive), len(named_alive)),
                 (150, 230, 170)),
                ('北半球年最强', _strongest('N') or '-', (255, 160, 150)),
                ('南半球年最强', _strongest('S') or '-', (255, 160, 150)),
            ]
            lab_w = max(rt(f_s, a, (0, 0, 0)).get_width() for a, _b, _c in lines)
            val_surfs = [(a, rt(f_s, b, c), c) for a, b, c in lines]
            bw = max(lab_w + 10 + s2.get_width() for _a, s2, _c in val_surfs) + 26
            bw = max(210, min(340, bw))
            line_h = 19
            bh = 12 + len(lines) * line_h + 6
            panel = pygame.Surface((bw, bh), pygame.SRCALPHA)
            bg = (16, 22, 36, 205) if dark else (245, 245, 250, 225)
            bd = (55, 85, 130) if dark else (120, 140, 170)
            tc_c = (215, 225, 245) if dark else (30, 40, 60)
            dim = (140, 155, 180) if dark else (120, 130, 150)
            pygame.draw.rect(panel, bg, panel.get_rect(), 0, 8)
            pygame.draw.rect(panel, bd, panel.get_rect(), 1, 8)
            y = 8
            for a, s2, c in val_surfs:
                panel.blit(rt(f_s, a, dim), (12, y + 2))
                panel.blit(s2, (12 + lab_w + 10, y + 2))
                y += line_h
            surface.blit(panel, (12, max(0, sh - bh - 12)))
        except Exception:
            logger.debug('模拟统计框绘制失败', exc_info=True)

    def _sim_draw_hud(self, surface) -> None:
        sim = self.sim_v4
        # ⚠⚠ 轮次 154: 圆环钟必须拿到**分钟与秒级进度**。
        # 原来传的是 `_sim_calendar()` 的 (年,月,日,时) 再补一个硬编码 0 当分钟、
        # 用整数小时算进度 ⇒ 钟内恒 `HH00Z`, 进度弧每个模拟小时跳 15°(只有 24 个位置)。
        # 而上游 `_sim_calendar_disp()` 返回的本来就是**含分/秒的完整 datetime**
        # (`base + timedelta(hours=显示钟)`), 信息是白白丢掉的。
        # 风季那条路(`ste`)用的是秒级连续进度, 这里对齐同一口径。
        _cdt = self._sim_calendar_disp()
        _secs = (_cdt.hour * 3600 + _cdt.minute * 60 + _cdt.second
                 + _cdt.microsecond / 1e6)
        # 左上角时间轴(圆环钟, 模拟时间): 连续走(显示钟), 见 _sim_disp_target
        self.draw_season_clock(surface, origin=(0, 0),
                               time_tuple=(_cdt.year, _cdt.month, _cdt.day,
                                           _cdt.hour, _cdt.minute,
                                           _secs / 86400.0))
        # 数据模式徽标(右上角): 读真实 dataState(而非仅设置)——降级/部分时提示原因
        try:
            st = sim.get('dataState', 'off')
            if st == 'loaded':
                mode_txt, bar = '数据环境', (90, 220, 90)
            elif st == 'partial':
                mode_txt, bar = '数据环境·部分', (240, 210, 90)
            elif st == 'fallback':
                mode_txt, bar = '数据环境·已降级', (240, 140, 90)
            else:
                mode_txt, bar = '解析环境', (150, 150, 160)
            # 瞬时 / 月均 兜底 要如实标出来(用户 2026-09-20: "一切数据要用瞬时")
            _src = str(sim.get('dataSrc') or '')
            if _src in ('瞬时', '瞬时·部分', '月均'):
                mode_txt += '·' + _src
            badge = rt(f_s, "模式: " + mode_txt + " [D]", bar)
            self._sim_ctx_badge = badge
            sw = getattr(self, 'screen_width', 1600)
            surface.blit(badge, (max(0, sw - badge.get_width() - 12), 8))
            warn = sim.get('dataWarn', '')
            if warn:
                wsurf = rt(f_s, '⚠ ' + warn, (255, 190, 90))
                surface.blit(wsurf, (max(0, sw - wsurf.get_width() - 12),
                                     8 + badge.get_height() + 3))
        except Exception:
            logger.debug("模拟模式数据徽标绘制失败", exc_info=True)
        # 季节样式信息框(时钟下方, 与季节模式同起点 y=245)
        y = 245
        # 信息框不在这里画了 —— 完全照搬风季模式: 由 draw_typhoon_info
        # (选中那一只台风)画图标 + 名字 + 信息框, 见 _sim_render 末尾。
        # 用户 2026-09-19: "我让你完全照搬风季模式的信息框…不要再自作主张"
        # (原来这里塞了"精度/实测 ms"这种其它模式没有的行)
        # 图层开关搬到**下方功能栏**(用户 2026-09-19: "上方的图层显示放到下方功能栏,
        # 图层按钮也不需要了"), 地图上不再画那一排按钮; 统计框在左下。
        self._sim_draw_stats_box(surface)
        self._sim_draw_flow_legend(surface)
