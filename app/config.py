# py/config.py
"""应用配置 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, MISSING
from typing import Dict, Optional, Tuple
import json
import math
import os
import logging

from .constants import HEMISPHERE_NORTH, ICON_SET_DEFAULT

logger = logging.getLogger(__name__)

# 信息框自定义(S0): 有序行 key 表(顺序即默认显示顺序)
# ⚠ 用户 2026-10-01: 气压从「等级」行拆出, 成为独立行 `pres` 并**默认紧跟风速之后**
#   (行序: 名字 → 时间 → 位置 → 风速 → 气压 → 等级 → 移速 → …)。
#   `cat` 行从此只渲染「等级」; 旧配置的迁移见 `migrate_info_box_rows`。
INFO_BOX_ROW_KEYS = ('name', 'time', 'pos', 'wind', 'pres', 'cat', 'speed',
                     'peak', 'tace', 'cace')
# 各行的 (默认显示, 默认平滑) —— 与 agent.md S0 B2 表一致
_INFO_BOX_ROW_DEFAULTS = {
    'name': (True, True), 'time': (False, True), 'pos': (True, True),
    'wind': (True, True), 'pres': (True, True), 'cat': (True, True),
    'speed': (True, True),
    'peak': (False, True), 'tace': (False, True), 'cace': (False, True),
}


def _default_info_box_rows() -> list:
    return [{'key': k, 'on': _INFO_BOX_ROW_DEFAULTS[k][0],
             'smooth': _INFO_BOX_ROW_DEFAULTS[k][1]} for k in INFO_BOX_ROW_KEYS]


def migrate_info_box_rows(rows) -> list:
    """把**旧配置**的行序补齐到当前键表(缺 `pres` 时补一行「气压」)。

    迁移规则(用户 2026-10-01): 气压从 `cat` 行拆出 ⇒ 新行 `pres` 默认**紧跟 `wind`
    之后**; 若没有 `wind` 行则放到 `cat` **之前**; 两者都没有就追加到末尾。
    其余行的顺序/显隐标志**一律不动**(老用户的行序不能被搞乱); 未知 key 由
    `_coerce` 的清洗环节丢弃, 这里只处理"缺 pres"这一件事。

    幂等: 已有 `pres`(无论显/隐)时**原样返回入参** —— 调用点每帧都可能调它。
    """
    if not isinstance(rows, list):
        return rows
    if 'pres' not in INFO_BOX_ROW_KEYS:
        return rows
    for it in rows:
        if isinstance(it, dict) and it.get('key') == 'pres':
            return rows                     # 已迁移(含显式隐藏), 不重复插入
    out = [dict(it) for it in rows if isinstance(it, dict)]
    on, smooth = _INFO_BOX_ROW_DEFAULTS['pres']
    item = {'key': 'pres', 'on': on, 'smooth': smooth}
    idx = next((i for i, it in enumerate(out) if it.get('key') == 'wind'), None)
    if idx is not None:                     # 紧跟 wind 之后
        out.insert(idx + 1, item)
    else:
        j = next((i for i, it in enumerate(out) if it.get('key') == 'cat'), None)
        if j is not None:                   # 没有 wind: 放到 cat 之前
            out.insert(j, item)
        else:                               # 两行都没有: 追加
            out.append(item)
    return out


#: 镜头跟踪收尾参数的可调范围 (lo, hi)。**唯一真相**: AppConfig 的手改钳制与
#: 设置面板的输入校验都读这一份, 不会出现"面板放行、config 钳掉"这类两处漂移。
TRACK_FIELD_RANGES = {
    'track_view_pad_pct': (0, 50),          # 边距 >50% 会让路径只剩中间一小块
    'track_settle_ms': (0, 5000),           # 到位后静置; 0 = 不静置
    'track_hl_in_ms': (0, 2000),            # 渐入可为 0(硬切), 不能为负
    'track_hl_out_ms': (0, 2000),           # 渐出同上
    'track_hl_hold_ms': (100, 10000),       # 保持为 0 等于没有高亮
    'track_pan_min_ms': (100, 5000),        # 推镜最短时长
    'track_pan_ms_per_1k_px': (0, 2000),    # 每 1000 屏幕像素追加的时长
    'track_pan_max_ms': (100, 10000),       # 推镜最长时长
    'track_pan_max_step_px': (0.5, 200.0),  # 单帧上限 <0.5px 会把正常跟随限死
    'track_jump_px': (1.0, 2000.0),         # 太小会把快速台风误判成瞬移
    'track_glide_exit_px': (0.0, 500.0),    # 缺口小于它则交回指数平滑
    'track_auto_transition_s': (0.2, 30.0),  # 自动镜头的指数响应时间常数(秒)
    'track_auto_pad_pct': (0, 100),          # 自动镜头 bbox 外扩(%)
}


@dataclass
class AppConfig:
    mlo: float = 100.0
    Mlo: float = 180.0
    mla: float = 0.0
    Mla: float = 50.0
    map_corner_mode: bool = False
    cmp: Optional[str] = None

    ac: bool = True
    md: str = "normal"
    sp: float = 1.0
    mis: float = 0.1
    mas: float = 10.0

    show_info_box_normal: bool = True
    show_info_box_season: bool = True
    screen_width: int = 2560
    screen_height: int = 1540
    start_maximized: bool = True
    window_topmost: bool = False
    fullscreen: bool = False

    ace_display_mode: str = "progress_bar"
    ace_geo_limit_enabled: bool = False
    ace_limit_mode: str = "none"
    ace_limit_basin: str = ""
    ace_min_lon: float = 100.0
    ace_max_lon: float = 180.0
    ace_min_lat: float = 0.0
    ace_max_lat: float = 90.0

    land_min_lon: float = 90.0
    land_max_lon: float = 190.0
    land_min_lat: float = -10.0
    land_max_lat: float = 80.0

    main_rotation_speed: float = 1.0
    # 模拟模式(F1/F3): 起始时刻与模态手动覆盖 —— 都要跨会话记住。
    # **空串 = 自动**: 用"最后一个有实测模态数据的时刻"(用户 2026-09-19:
    # "默认是 1850 年. 应该是当前时间, 或者是最后一个能找到现实模态的时间")。
    sim_start: str = ""
    sim_env_step_h: float = 6.0
    sim_mode_override: dict = field(default_factory=dict)
    # 强度显示精度(用户 2026-09-17 定)。**内部一律 float**, 这只是显示层。
    #   风速: "5" / "1"(默认) / "0.1"       单位 kt
    #   气压: "0258"(尾数只取 0/2/5/8) / "1"(默认) / "0.1"   单位 hPa
    sim_vmax_prec: str = "1"
    sim_pmin_prec: str = "1"
    # 模拟精度档位(用户 2026-09-18 定): 用"每 6h 模拟时间的墙钟成本"定义。
    # 硬上限 1 s, 实际上限 800 ms —— 超过它 UI 提示"已达当前模拟时间步长上限"。
    sim_precision: str = "400ms"
    # 模拟模式的显示/开关类设置(2026-09-18 review 补)。这些原来只活在
    # sim_v4 字典里, 而 sim_v4 每次 _sim_reset 都重建 —— 于是"关掉风圈显示 →
    # 改一下起始时刻"就被静默打回默认, 重启更是全丢。面板上的开关必须记住。
    sim_natural_gen: bool = True
    sim_data_mode: bool = True
    # 数据环境用哪一年的库(None = 气候态; 有值 = 具体年份库)
    sim_data_year: Optional[int] = None
    sim_data_clim: bool = True
    # 随机种子(环境模态 AR 噪声/集合扰动/生成扰动的来源)
    # 注: 播放速度用的是既有的 sp 字段(全局速度条同一旋钮), 不另设 sim_sp
    sim_seed: int = 20250801
    sim_fcst_style: str = "JTWC"
    sim_particle_count: int = 3000
    sim_quad_rings: bool = True
    sim_show_rings: bool = True
    sim_show_labels: bool = True
    sim_show_barbs: bool = True
    sim_show_forecast: bool = True
    #: **集合预报(实时)**: 默认 **关**。
    #: 它是只读诊断(12 成员集合, 画预报锥), 但每签发一次要跑 13 次 env_update ——
    #: 实测单步 `forecast=True` **24.6 s** vs `False` **2.6 s**(占 89%), 400ms 档
    #: UI 冻结占比 84-88% ⇒ 开着它模拟模式基本没法交互。验收工具
    #: (`verify_year` / `multi_year_check`)显式传 `params['forecast']`, 内核默认值
    #: 未改, 所以那两条链路不受影响。
    sim_ensemble: bool = False
    #: 轮次 158: 新增环境机制(副高形状 / 西风槽 / 冷空气 / 地形)的**参数覆盖字典**。
    #: 用一个字典而不是给每个旋钮加一个字段 —— 旋钮已有十几个且还会长, 逐字段加会让
    #: config 与内核的参数表**两处维护、早晚漂开**。键名就是 `sim['params']` 的键。
    #: `_sim_reset` 会把它 merge 进 params; **空字典 = 全走内核默认 = 逐位不变**。
    sim_env_params: dict = field(default_factory=dict)
    # 模拟模式 ACE 条的上限(用户 2026-09-19: 默认 1500; 突破了就停在白框最右)
    sim_ace_cap: float = 1500.0
    # 地图左键点击是否生成台风(用户 2026-09-19: 默认关, 免得误点就出风暴)
    sim_click_spawn: bool = False
    # 中键点击生成台风(用户 2026-09-19: 默认开)
    sim_middle_spawn: bool = True
    # 风场样式: barbs(风羽) / stream(流线) / particles(粒子)
    sim_wind_mode: str = "barbs"
    # 风切样式: True=色阶填充(默认) / False=CIMSS 式等值线(带数值标注)
    sim_shear_fill: bool = True
    # 台风头顶的强度/气压标签(用户 2026-09-19)
    sim_show_inten: bool = True
    sim_label_size: int = 100        # 字号百分比(60-200)
    sim_label_offset: int = 40       # 标签离台风中心的像素距离(6-120)
    level3_rotation_speed: float = 1.5
    volume: float = 0.6
    # 登陆音效随强度增强(可开关;曲线参数均可调整)
    landfall_vol_scale: bool = True
    #: ── 登陆汇总（用户 2026-10-12 需求 2）─────────────────────────────
    #: "当登陆强度较弱时, 可以把一系列时间地点非常近且强度相同的登陆汇总成一次"
    #:           总开关
    #:      汇总判据: 'time'(按时间串) / 'place'(按地点串)
    #:        强度容差(kt), 默认 0 = 必须完全相同
    #:      时间最大差(h), 默认 3
    #:  地点最大差(经纬距), 默认 0.2
    landfall_merge: bool = False
    landfall_merge_mode: str = "time"
    landfall_merge_dw: int = 0
    landfall_merge_dt_h: float = 3.0
    landfall_merge_dist_deg: float = 0.2
    landfall_vol_base_kt: int = 65
    landfall_vol_lo_kt: int = 35
    landfall_vol_lo_db: float = -2.0
    landfall_vol_hi_kt: int = 112
    landfall_vol_hi_db: float = 5.0
    landfall_vol_cap_kt: int = 200
    landfall_vol_cap_db: float = 15.0
    name_display_mode: int = 0
    point_name_mode: bool = False
    hemisphere: str = HEMISPHERE_NORTH
    point_size: int = 100
    icon_size: int = 100
    name_size: int = 100
    #: 台风名字的**额外**像素偏移(相对"图标右上角"锚点; 单位 = icon_factor==1 时的 px,
    #: 渲染时乘 icon_factor)。`dy` 正方向 = **向下**(正数往下、负数往上), 与直觉一致。
    #: 用户 2026-10-01: "名字太右上角了。加一个可视化界面可以自行调整吧" ⇒
    #: 基准锚点已从"正对角"收到 `NAME_ANCHOR_IN`(见 `_draw_icon_mixin`),
    #: 这两项是用户自己的微调量(可视化对话框 app/name_pos_dialog.py 写入)。
    name_dx: int = 0
    name_dy: int = 0
    #: 巅峰/登陆标注的**额外**偏移 —— 与 `name_dx/name_dy` **同一套语义**:
    #: 单位 = `icon_factor==1` 时的 px(渲染时乘 icon_factor), `dy` 正 = 向下;
    #: 基准点是**名字**的左上角(标注本来就堆在名字下方, 用户 2026-10-01:
    #: "而且不能调整巅峰/登陆!")。由 app/name_pos_dialog.py 写入。
    peak_dx: int = 0
    peak_dy: int = 0
    peak_label_size: int = 100
    fix_icon_point_size: bool = False
    #: **域外风暴小窗**（用户 2026-10-12 需求 1）：在屏幕边缘用白色方框显示
    #: 指定的、不在屏幕内的风暴。哪些风暴走这套显示由「域外风暴」按钮的对话框
    #: 写入 `ty.offscreen`（见 app/offscreen_storm.py / offscreen_dialog.py）。
    show_offscreen_boxes: bool = True
    fade_typhoon: bool = True
    fade_path: bool = True
    fade_path_mode: str = "fade"
    smooth_path: bool = False
    smooth_path_segments: int = 10
    smooth_path_mode: str = "monotone"
    edit_snap_step: float = 0.1
    show_edit_point_labels: bool = False
    path_mode: str = "markers"
    show_future_path: bool = True
    ace_interpolated: bool = False
    show_fps: bool = False
    fps_cap: int = 60   # CPU 软件渲染下 120 帧预算仅 8.3ms; 60 保帧时间稳定
    monthly_summary: bool = True

    show_ri_effect: bool = True
    show_ace_bar: bool = True
    show_ace_total: bool = True

    disable_dpi_scaling: bool = False
    dark_mode: bool = True

    icon_set: str = ICON_SET_DEFAULT
    color_scheme: int = 1
    show_summary: bool = True
    summary_transparent: bool = True
    basin_filter_enabled: bool = True
    show_legend: bool = False
    show_graticule: bool = False
    show_ocean_areas: bool = False
    show_coord_hud: bool = True
    show_track_label: bool = True   # 左上角镜头跟踪指示(可关, 便于录屏)
    normal_other_display: str = "translucent"   # 正常模式其它台风: translucent/opaque/hidden

    # ── 镜头跟踪: 收尾时序(用户 2026-10-01; 默认值 = 当时的硬编码常量) ──────
    #: 收尾视野边距(%): 路径 bbox 每边外扩该比例的跨度。路径占屏 ≈ 1/(1+2p)。
    #: 25 ⇒ 66.7%, 30 ⇒ 62.5%(用户 2026-10-01 定 30)。
    track_view_pad_pct: int = 30
    #: 推镜(平滑移动+缩放)到位后的静置(ms), 之后才开始高亮。
    track_settle_ms: int = 500
    #: 高亮: 渐入 / 保持 / 渐出(ms)。**单次** 0→峰值→0, 不是闪烁。
    track_hl_in_ms: int = 200
    track_hl_hold_ms: int = 2000
    track_hl_out_ms: int = 200
    #: 推镜时长 = `min + 距离(屏幕px) * per_1k / 1000`, 夹在 [min, max]。
    track_pan_min_ms: int = 700
    track_pan_ms_per_1k_px: int = 140
    track_pan_max_ms: int = 2400
    #: 有界摇镜(换台风时不"跳"): 单帧相机位移上限(屏幕px) / 目标瞬移判定(px) /
    #: 缺口小于它则把摇镜交回指数平滑(px)。
    track_pan_max_step_px: float = 6.0
    track_jump_px: float = 30.0
    track_glide_exit_px: float = 12.0
    #: ── 自动镜头（用户 2026-10-13）────────────────────────────────────────
    #: 与"镜头跟踪"融合：位置仍走 （经度环绕/死区/有界摇镜/相位锁定），
    #: 自动镜头**只补上缩放那一半** —— 把全体活跃台风的 bbox 装进画面。
    #:  = 指数响应时间常数（秒），帧率无关：
    #:   k = 1 - exp(-4.6 * dt / T)   ⇒ 经过 T 秒走完 99%。
    track_auto_transition_s: float = 3.0
    #: bbox 每边外扩比例（%）。20 ⇒ 路径占屏约 100/140。
    track_auto_pad_pct: int = 20

    #: ── 模块系统（用户 2026-10-14）────────────────────────────────────
    #: 键 = 模块 id（见 app/module_registry.py），值 = 是否启用。
    #: **没配过的模块走各自的默认值（默认全开）** ⇒ 观感与加模块系统之前一致。
    modules: Dict[str, bool] = field(default_factory=dict)

    tn: Dict[str, str] = field(default_factory=dict)

    # 信息框自定义(S0): 行显隐/平滑/排序 + 去背景 + 文字阴影
    info_box_rows: list = field(default_factory=_default_info_box_rows)
    info_box_bg: bool = False
    info_box_text_shadow: bool = True
    info_box_scale: float = 1.2

    @classmethod
    def _serialize_fields(cls) -> Tuple[str, ...]:
        return tuple(f.name for f in fields(cls) if not f.name.startswith('_'))

    def update_from(self, other: "AppConfig") -> None:
        """用另一个配置实例覆盖当前实例的全部字段（py/→app/ 重命名期间曾丢失,已恢复）。"""
        for fld in fields(self):
            setattr(self, fld.name, getattr(other, fld.name))

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning(f"配置加载失败: {path}: {e}")
            return cls()
        if not isinstance(raw, dict):
            logger.warning(f"配置顶层不是对象: {path}")
            return cls()
        known = {f.name: f for f in fields(cls)}
        # ⚠⚠ 2026-10-01 重构(用户: "回落默认也改掉"):
        # 原来是"先把所有字段塞进 kwargs, 再 `cls(**kwargs)`, 一旦抛异常就
        # `return cls()`" —— **任何一个字段非法, 整份配置回落到默认值**,
        # 用户所有设置一次性全丢(而且只在日志里留一行 warning)。
        # 现在改成**逐字段恢复**: 合法字段照常生效, 只有解析失败的**那一个**字段
        # 用自己的默认值, 并逐个列出来。这样单点坏值不会连坐。
        cfg = cls()
        bad_fields = []
        for k, v in raw.items():
            fld = known.get(k)
            if fld is None:
                continue
            try:
                setattr(cfg, k, cls._coerce(fld, v))
            except Exception as e:
                bad_fields.append((k, v, e))
        if bad_fields:
            logger.warning(
                "配置有 %d 个字段非法, 仅这些字段使用默认值(其余照常生效): %s",
                len(bad_fields),
                "; ".join(f"{k}={v!r}({e})" for k, v, e in bad_fields[:8]))
        # 手改坏值归一化：范围必须有效，否则启动时 (Mlo-mlo) 除零（B2 残留）
        if cfg.Mlo <= cfg.mlo or cfg.Mlo - cfg.mlo < 0.1:
            cfg.mlo, cfg.Mlo = min(cfg.mlo, cfg.Mlo), max(cfg.mlo, cfg.Mlo)
            if cfg.Mlo - cfg.mlo < 0.1:
                cfg.mlo, cfg.Mlo = 100.0, 180.0
        if cfg.Mla <= cfg.mla or cfg.Mla - cfg.mla < 0.1:
            cfg.mla, cfg.Mla = min(cfg.mla, cfg.Mla), max(cfg.mla, cfg.Mla)
            if cfg.Mla - cfg.mla < 0.1:
                # 与类声明默认值保持一致
                cfg.mla, cfg.Mla = 0.0, 50.0
        if cfg.ace_max_lon < cfg.ace_min_lon:
            cfg.ace_min_lon, cfg.ace_max_lon = cfg.ace_max_lon, cfg.ace_min_lon
        if cfg.ace_max_lat < cfg.ace_min_lat:
            cfg.ace_min_lat, cfg.ace_max_lat = cfg.ace_max_lat, cfg.ace_min_lat
        # 陆地范围同样需要保持 max≥min,避免未来下游 (max-min) 除零
        if cfg.land_max_lon < cfg.land_min_lon:
            cfg.land_min_lon, cfg.land_max_lon = cfg.land_max_lon, cfg.land_min_lon
        if cfg.land_max_lat < cfg.land_min_lat:
            cfg.land_min_lat, cfg.land_max_lat = cfg.land_max_lat, cfg.land_min_lat
        return cfg

    @staticmethod
    def _default_for(fld) -> object:
        """安全默认值:优先 default,其次 default_factory,最后类型兜底(N8)。"""
        if fld.default is not MISSING:
            return fld.default
        if fld.default_factory is not MISSING:
            return fld.default_factory()
        return None

    @staticmethod
    def _coerce(fld, v):
        if v is None:
            return AppConfig._default_for(fld)
        t = fld.type
        if t == 'bool':
            return v if isinstance(v, bool) else AppConfig._default_for(fld)
        if t == 'int':
            if isinstance(v, bool):
                return AppConfig._default_for(fld)
            # int() 遇到 nan/inf(JSON 字面量或 1e999)直接抛 OverflowError,
            # 先归一化再折算,避免 load() 崩溃(与下方 float NaN/Inf 守卫一致)
            try:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    fv = None
                if fv is not None and (math.isnan(fv) or math.isinf(fv)):
                    return AppConfig._default_for(fld)
                out = int(v)
            except (TypeError, ValueError, OverflowError):
                try:
                    out = int(float(v))
                except (TypeError, ValueError, OverflowError):
                    return AppConfig._default_for(fld)
            if fld.name in ('screen_width',):
                out = max(800, min(out, 3840))
            elif fld.name in ('screen_height',):
                out = max(600, min(out, 2160))
            elif fld.name in ('point_size', 'icon_size', 'name_size', 'peak_label_size'):
                out = max(1, out)
            elif fld.name in ('name_dx', 'name_dy', 'peak_dx', 'peak_dy'):
                # 名字/标注偏移: 手改 config.json 注入 ±10^9 会把它们甩到屏幕外(且
                # "点一下方向键要按几万次"才回得来)。~1.5 倍图标尺寸已是可用上限。
                out = max(-300, min(out, 300))
            elif fld.name == 'fps_cap':
                # 0 = 不限帧(settings UI 提供 60/120/0 三档)。首轮修复误用
                # max(1,·),导致手改/重启后 0 被钳成 1 FPS;必须放行 0。
                out = max(0, min(out, 500))
            elif fld.name == 'smooth_path_segments':
                # 与 settings.py 的 200 上限对齐:避免手改 config 注入
                # 每段 1000 点的昂贵样条(全站 ~8 处调用点)。<=0 仍钳 1。
                out = max(1, min(out, 200))
            elif fld.name == 'sim_label_size':
                out = max(60, min(out, 200))
            elif fld.name == 'sim_label_offset':
                out = max(6, min(out, 120))
            elif fld.name == 'sim_particle_count':
                # 与设置面板滑条同域(500-6000), 手改 10^9 会让粒子步进卡死
                out = max(500, min(out, 6000))
            # ── 镜头跟踪收尾时序(用户可调; 手改坏值必须有界) ──
            elif fld.name in TRACK_FIELD_RANGES:
                lo, hi = TRACK_FIELD_RANGES[fld.name]
                out = max(int(lo), min(out, int(hi)))
            return out
        if t == 'float':
            try:
                out = float(v)
            except (TypeError, ValueError):
                return AppConfig._default_for(fld)
            # NaN/Infinity 不会被比较捕获,显式拒绝避免下游除零/范围校验失效
            if math.isnan(out) or math.isinf(out):
                return AppConfig._default_for(fld)
            if fld.name in ('mla', 'Mla', 'ace_min_lat', 'ace_max_lat',
                            'land_min_lat', 'land_max_lat'):
                out = max(-90.0, min(out, 90.0))
            elif fld.name in ('mlo', 'Mlo', 'ace_min_lon', 'ace_max_lon',
                              'land_min_lon', 'land_max_lon'):
                out = max(0.0, min(out, 360.0))
            elif fld.name == 'mis':
                out = max(0.1, out)
            elif fld.name == 'mas':
                out = max(0.1, out)
            elif fld.name == 'edit_snap_step':
                # 0 = 关闭吸附(合法哨兵); 负值会让洋区编辑网格 while 永不终止
                out = max(0.0, min(out, 10.0))
            elif fld.name == 'volume':
                out = max(0.0, min(out, 1.0))
            elif fld.name == 'sim_ace_cap':
                # ACE 条上限: 0/负数会让 min(1, cur/total) 除零或恒满格
                out = max(100.0, min(out, 100000.0))
            # ── 镜头跟踪: 有界摇镜(换台风不"跳")的三个阈值 ──
            elif fld.name in TRACK_FIELD_RANGES:
                lo, hi = TRACK_FIELD_RANGES[fld.name]
                out = max(float(lo), min(out, float(hi)))
            return out
        if t.startswith('Dict['):
            # N8: null/非 dict 用 default_factory 兜底,绝不返回 MISSING
            if isinstance(v, dict):
                if fld.name == 'tn':
                    # 值必须是字符串: 坏值会传给 Typhoon.cust 并在排序时 .lower() 崩溃
                    return {str(k): val for k, val in v.items()
                            if isinstance(val, str)}
                return v
            return AppConfig._default_for(fld)
        if t == 'list' or t.startswith('List['):
            # 列表字段(当前仅 info_box_rows): 归一化行条目,坏值回退默认
            if fld.name == 'info_box_rows':
                cleaned = []
                for it in v if isinstance(v, list) else []:
                    if isinstance(it, dict) and it.get('key') in INFO_BOX_ROW_KEYS:
                        cleaned.append({'key': it['key'],
                                        'on': bool(it.get('on', True)),
                                        'smooth': bool(it.get('smooth', True))})
                if cleaned:
                    # ⚠ 用户 2026-10-01: 旧配置没有 `pres` 行(气压原先并进 `cat`),
                    #   这里统一补上, 且**保持老用户其余行序不变**。
                    return migrate_info_box_rows(cleaned)
                return AppConfig._default_for(fld)
            return v if isinstance(v, list) else AppConfig._default_for(fld)
        if t == 'Optional[str]':
            return v if v is None or isinstance(v, str) else AppConfig._default_for(fld)
        if t == 'Optional[int]':
            # 留空/None = 该字段的"没有"语义(如 sim_data_year: 用气候态)。
            # 非数字的坏值一律当 None, 不能让手改 config 把 int() 带崩。
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            if isinstance(v, bool):
                return AppConfig._default_for(fld)
            try:
                return int(v)
            except (TypeError, ValueError, OverflowError):
                return None
        if fld.name == 'hemisphere':
            # 下半球开关靠严格 == 'south' 判断；手改 config 写成 'North'/'S' 会
            # 静默让南半球季节、ACE 年界全部翻转,这里做小写 + 白名单归一化(防御)。
            if isinstance(v, str):
                v = v.strip().lower()
                return v if v in (HEMISPHERE_NORTH, 'south') else AppConfig._default_for(fld)
            return AppConfig._default_for(fld)
        if fld.name == 'sim_fcst_style':            # 预报路径样式只有 JTWC/JMA 两套(render 按 == 'JMA' 分支)
            if isinstance(v, str):
                u = v.strip().upper()
                return u if u in ('JTWC', 'JMA') else AppConfig._default_for(fld)
            return AppConfig._default_for(fld)
        if fld.name == 'sim_wind_mode':             # 风场样式白名单
            if isinstance(v, str):
                w = v.strip().lower()
                return w if w in ('barbs', 'stream', 'particles') \
                    else AppConfig._default_for(fld)
            return AppConfig._default_for(fld)
        return v if isinstance(v, str) else AppConfig._default_for(fld)

    def save(self, path: str) -> bool:
        """保存配置。返回是否成功(失败仅记日志, 不中断退出/模式切换)。"""
        # W6(2026-10-02): 与 `ConfigStore` **同源**的写保护 —— 非应用进程写用户
        # `config.json` 时会被重定向到临时文件（判定见 `app/config_store.py` 模块头的
        # 5 级优先级；应用本体由 `main.py` 的 `declare_app()` 声明）。
        # `AppConfig.save()` 是本项目**唯一**绕过 `ConfigStore` 的写入旁路
        # （调用点 `app/ty_sim.py:458`），此前不受写保护，故在此同源收口。
        # 函数内 import：`config_store` 只依赖标准库，无环；且避免影响 import 顺序。
        try:
            from .config_store import resolve_config_path
            path = resolve_config_path(path)
        except Exception:
            pass
        tmp = path + ".tmp"
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({k: getattr(self, k) for k in self._serialize_fields()}, f, indent=2)
            os.replace(tmp, path)
            return True
        except (TypeError, OSError, ValueError) as e:
            logger.warning(f"配置保存失败: {path}: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False
