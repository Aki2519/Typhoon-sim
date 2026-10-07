# py/ty_sim_mixins/draw_info_boxes_mixin.py
"""风季台风信息框 Mixin + 合并：季节时钟、ACE、控制面板。"""
from __future__ import annotations
import math
import os
from datetime import datetime
import pygame
from ..constants import (
    rt, TXT,
    INFO_BOX_BG, INFO_BOX_BORDER,
    HEMISPHERE_NORTH,
    SEASON_INFO_BOX_WIDTH,
    SEASON_INFO_BOX_START_X, SEASON_INFO_BOX_START_Y,
    SEASON_INFO_BOXES_PER_COL, SEASON_INFO_BOX_MAX_COLS,
    SEASON_INFO_BOX_SPACING_X, SEASON_INFO_BOX_SPACING_Y,
)
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..utils import max_wind_from_points
from ..utils import peak_point, movement_speed_kt
from ..control_panel import ControlPanel

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'assets', 'font')
# 统一走 _load_font(带缓存/字体回退): pygame.font.Font 在字体缺失时直接抛异常且绕过缓存
_font_date = _load_font(FONT_FILE, 54, 54)
_font_sub = SmartFont(_load_font(FONT_FILE, 27, 27), _load_font(FONT_FILE, 27, 27))
_font_month = _load_font(FONT_FILE, 30, 30)
_font_box = SmartFont(_load_font(FONT_FILE, 18, 18), _load_font(FONT_FILE, 18, 18))
_font_ace_title = SmartFont(_load_font(FONT_FILE, 18, 18), _load_font(FONT_FILE, 18, 18))
_font_ace = SmartFont(_load_font(FONT_FILE, 34, 34), _load_font(FONT_FILE, 34, 34))
_font_ace_note = SmartFont(_load_font(FONT_FILE, 25, 25), _load_font(FONT_FILE, 25, 25))

# 法30: 季节时钟 trig LUT(4096 级,亚像素一致)
_TRIG_N = 4096
_COS_LUT = [math.cos(2 * math.pi * i / _TRIG_N) for i in range(_TRIG_N)]
_SIN_LUT = [math.sin(2 * math.pi * i / _TRIG_N) for i in range(_TRIG_N)]

# ── S4(2026-10-06): 信息框"点一下 ⇒ 镜头跳到该台风" ────────────────────────
# 命中判定**绝不另算一份矩形**: `blit_info_box` 在**真正绘制**的那一刻, 把框实际
# 落在屏幕上的矩形登记进 `self._info_box_hit`; 命中判定只读这份登记 ⇒ 绘制与命中
# 共用同一份数据, 不存在"两处各写一遍坐标然后漂移"的可能。
# 登记带时间戳: 只认**最近画过**的框(用户点的是他眼睛看到的那一个), 陈旧条目
# 会被丢弃, 因此切模式/框消失后不会留下幽灵热区。
_INFO_BOX_HIT_MAX_AGE_MS = 250
#: 悬停反馈①: 框沿高亮环。**双色(主色 + 描边)**, 理由有实测:
#:   环画在框**外侧**(4px 间隙), 压的是**地图**; 实测环带背景亮度跨
#:   0.0014(深海) ~ 0.4567(亮陆地)(n=3084, 且 `dark_mode` 对地图**无影响** ——
#:   两种模式下背景统计完全相同) ⇒ **任何单一颜色都会在中间亮度处失效**:
#:   琥珀最小 1.49:1 / 白 2.07:1 / 靛蓝 2.57:1(靛蓝正是"为浅底挑的深色",
#:   在深底上反而更差)。所以按双色设计, 判据:
#:     **对环带每个背景像素 B: max(con(主色,B), con(描边,B)) ≥ 3:1**
#:   实测 琥珀+黑描边 = **最小 4.03 / 中位 13.71**(N=3084 全覆盖达标)。
#: `dark_mode` 只调**描边** —— 真正需要按模式区分的是"描边对界面底色的对比度":
#:   浅色界面纯黑, 对浅色面板边框 (120,140,170) = **5.45:1**;
#:   深色界面近黑, 对深色面板边框 (55,85,130) = 5.44:1, 少一点视觉噪点。
#:   ⚠ 主色两模式**同色**是数据结论而非偷懒: 换任何"适合浅底的深色", 它在深色
#:   地图上的对比度都不够(候选表见 `output/exploration/s4_click_to_tc.md` §6)。
_INFO_BOX_HOVER_RING = (255, 214, 120)             # 主色(双模式同色)
_INFO_BOX_HOVER_RING_CASING_DARK = (20, 20, 24)    # 深色界面描边
_INFO_BOX_HOVER_RING_CASING_LIGHT = (0, 0, 0)      # 浅色界面描边(对浅底最强)
_INFO_BOX_HOVER_RING_W = 3
#: 悬停反馈②: 手型光标(与颜色无关的第二个通道, 满足"状态不只靠颜色传达")
_INFO_BOX_HOVER_RADIUS = 12


def _cos_lut(a: float) -> float:
    return _COS_LUT[int(a / (2 * math.pi) * _TRIG_N) % _TRIG_N]


def _sin_lut(a: float) -> float:
    return _SIN_LUT[int(a / (2 * math.pi) * _TRIG_N) % _TRIG_N]


_BOX_PAD = 4


def _fit_box_line(font, text: str, color, max_w: int) -> pygame.Surface:
    """把长文本截断为单行(超宽时二分缩短 + 省略号),避免 wrap 多行与下行重叠。"""
    full = rt(font, text, color)
    if full.get_width() <= max_w:
        return full
    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if rt(font, text[:mid] + "…", color).get_width() <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return rt(font, text[:lo] + "…", color)

_CLOCK_START_A = -math.pi / 2
_CLOCK_TAU = 2.0 * math.pi

_MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
_STROKE = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]

_ACE_NOTE_DURATION_MS = 2500
_ACE_NOTE_FADE_MS = 500

# ACE 数字合成缓存：值量化到 4 位小数，变化 <0.00005 复用上一帧（拖25）
_ace_digit_cache: dict = {}
_ACE_DIGIT_CACHE_MAX = 64

# ── 描边文字缓存：key=(字体id, 文本, 颜色) → 已合成描边 Surface ──
_stroked_cache: dict = {}
_STROKED_CACHE_MAX = 128


def _stroked(font, text: str, color) -> pygame.Surface:
    """黑色 8 向描边 + 前景色文字，整体合成并缓存（锚点 = 原文字位置 -1,-1）。"""
    key = (id(font), text, color)
    surf = _stroked_cache.get(key)
    if surf is None:
        fg = font.render(text, True, color)
        bk = font.render(text, True, (0, 0, 0))
        surf = pygame.Surface((fg.get_width() + 2, fg.get_height() + 2), pygame.SRCALPHA)
        for dx, dy in _STROKE:
            surf.blit(bk, (dx + 1, dy + 1))
        surf.blit(fg, (1, 1))
        if len(_stroked_cache) >= _STROKED_CACHE_MAX:
            _stroked_cache.pop(next(iter(_stroked_cache)))
        _stroked_cache[key] = surf
    return surf


# ── 淡入淡出半透明副本缓存：alpha 量化 16 档，避免每帧 copy ──
_faded_cache: dict = {}
_FADED_CACHE_MAX = 64


def _faded_copy(surf: pygame.Surface, alpha: int) -> pygame.Surface:
    bucket = max(0, min(15, alpha // 16))
    key = (id(surf), bucket)
    entry = _faded_cache.get(key)
    if entry is None or entry[0] is not surf:
        out = surf.copy()
        out.set_alpha(bucket * 16 + 15)
        entry = (surf, out)
        if len(_faded_cache) >= _FADED_CACHE_MAX:
            _faded_cache.pop(next(iter(_faded_cache)))
        _faded_cache[key] = entry
    return entry[1]


class TySimDrawInfoBoxesMixin:
    """风季模式下的多台风信息框 + 季节时钟 + ACE + 控制面板。"""

    # _season_info_box_cache / _season_info_box_last_data 由 TySim._init_attributes
    # 创建为实例属性(类级共享会跨实例污染并泄漏台风对象)
    _season_box_line_h: int = 0
    _clock_arc_surf = None

    @classmethod
    def _season_info_box_line_height(cls) -> int:
        if not cls._season_box_line_h:
            cls._season_box_line_h = _font_box.render(
                "总ACE:0.0000 实时ACE:0.0000", True, (255, 255, 255)).get_height()
        return cls._season_box_line_h

    @classmethod
    def _season_info_box_height(cls) -> int:
        """信息框高度：上下边距相同，6 行文字。"""
        return _BOX_PAD * 2 + 6 * cls._season_info_box_line_height()

    def _render_info_box(self, ty, box_w: int, box_h: int):
        """渲染信息框主体（不含实时ACE数字）。
        返回 (box, ace_x, ace_y, tc)：数字由调用方每帧单独绘制。"""
        if not ty.pts:
            # 新建未加点台风: 画空框, 避免 ty.pts[0] IndexError
            box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            tc0 = (215, 225, 245) if getattr(self, 'dark_mode', True) else TXT
            pygame.draw.rect(box, INFO_BOX_BORDER, (0, 0, box_w, box_h), 2, 12)
            return box, 0, 0, tc0
        # ── 检查缓存 ──
        cp = ty.cp()
        dark = getattr(self, 'dark_mode', True)
        peak = peak_point(ty.pts)
        mv = movement_speed_kt(ty.pts, ty.points_time, ty.ci)
        key_data = (
            ty.b, ty.n, ty.tace,
            # 模式必须进缓存键: "巅峰@日期"只在风季模式显示(用户 2026-09-19),
            # 少了这一项, 先画风季后画正常模式会直接命中旧框(离屏渲染实测两张
            # 图字节相同才发现)
            getattr(self, 'md', None),
            ty.cust, ty.sname, ty.start_time,   # 名称字段(编辑名称后缓存失效)
            cp.get('name', '') if cp else '',   # 逐点名称模式下当前点名变化
            cp['w'] if cp else 0,
            cp['st'] if cp else '',
            cp['p'] if cp else 0,
            cp['la'] if cp else 0,
            cp['lo'] if cp else 0,
            ty.ci, dark,
            getattr(self, 'point_name_mode', False),   # N16: 逐点名称模式进 key
            getattr(self, 'name_display_mode', 0),
            len(ty.pts), peak['t'] if peak else '',
            peak['w'] if peak else 0, peak['p'] if peak else 0,  # R4: 巅峰风速/气压进 key
            round(mv or 0, 2),
            box_w, box_h,
        )
        if ty in self._season_info_box_cache and self._season_info_box_last_data.get(ty) == key_data:
            return self._season_info_box_cache[ty]

        box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)

        # 暗色主题样式
        if dark:
            bg = (22, 28, 44, 180)
            border = (55, 85, 130)
            tc = (215, 225, 245)
        else:
            bg = INFO_BOX_BG
            border = INFO_BOX_BORDER
            tc = TXT

        pygame.draw.rect(box, bg, (0, 0, box_w, box_h), 0, 8)

        # 左侧强度竖条（当前风速颜色）
        if cp and 'color' in cp:
            bar_c = cp['color']
            if len(bar_c) == 3:
                bar_c = (*bar_c, 220)
            pygame.draw.rect(box, bar_c, (0, 0, 8, box_h), 0, 12, 0, 0, 12)

        pygame.draw.rect(box, border, (0, 0, box_w, box_h), 2, 12)

        fp = ty.pts[0]
        lp = ty.pts[-1]
        tyy = fp['t'][:4] if len(fp['t']) >= 4 else "未知"
        if getattr(self, 'point_name_mode', False) and cp:
            # ⚠ 2026-10-01 审查: 这里原来空名会**回退成台风默认名**, 与图标侧
            # 今天改成的"空行不显示名字"语义相反 ⇒ 同屏会出现"图标旁没名字、
            # 信息框里却有 2025 xxW"。现在两侧统一: 空名就是空。
            # (该文件的缓存键已含 , 改语义不会引入陈旧命中。)
            tn = (cp.get('name') or '').strip()
        else:
            tn = self.get_display_name(ty)

        st = fp['t']
        et = lp['t']

        def _zz(t: str) -> str:
            """报点时间 → 'MM/DD HHZ' / 'MM/DD HHMMz'(分钟非零才带分钟)。

            用户 2026-09-19: "起止时间改成 06/21 12z-06/27 00z 这种(使用 z 而非
            XX:XX, 分钟直接显示 0645z 这样的)"。
            """
            if len(t) < 8:
                return "未知"
            mm, dd = t[4:6], t[6:8]
            if len(t) < 10:
                return f"{mm}/{dd}"
            hh = t[8:10]
            mi = t[10:12] if len(t) >= 12 else '00'
            try:
                has_min = int(mi) != 0
            except Exception:
                has_min = False
            return f"{mm}/{dd} {hh}{mi if has_min else ''}z"

        sft = _zz(st)
        eft = _zz(et)

        # 巅峰口径(用户 2026-09-19): **只看已经播过的部分**(过去最强), 不再把
        # 未来的报点算进来 —— 否则"当前巅峰"会显示还没发生的强度。
        _ci = int(getattr(ty, 'ci', 0) or 0)
        _past = ty.pts[:_ci + 1] if 0 <= _ci < len(ty.pts) else ty.pts
        peak = peak_point(_past) or peak_point(ty.pts)
        max_wind = max_wind_from_points(_past) if _past else max_wind_from_points(ty.pts)
        current_wind = cp['w'] if cp else "?"
        cur_st = (cp['st'].upper() if cp and cp['st'] else '-') if cp else '-'
        cur_pres = cp['p'] if cp else 0
        peak_txt = f"巅峰:{max_wind}kt"
        # 巅峰后面的时间只在风季模式显示(用户: "另外两个模式的状态栏巅峰后面的时间都去掉")
        if peak and getattr(self, 'md', None) == getattr(self, 'MODE_SEASON', 'season'):
            pt = peak['t']
            peak_txt += f"@{pt[4:6]}-{pt[6:8]}" if len(pt) >= 8 else ""
        mv_txt = f" 移速:{mv:.0f}kt" if mv else ""
        if cp:
            la, lo = cp['la'], cp['lo']
            lat_dir = 'N' if la >= 0 else 'S'
            if lo > 180.0:
                lon_disp, lon_dir = 360.0 - lo, 'W'
            elif lo < 0:
                lon_disp, lon_dir = -lo, 'W'
            else:
                lon_disp, lon_dir = lo, 'E'
            pos_txt = f"位置:{abs(la):.1f}°{lat_dir} {lon_disp:.1f}°{lon_dir}{mv_txt}"
        else:
            pos_txt = "位置:-"

        lines = [
            f"{ty.basin}{ty.n} {tyy} {tn}",
            # 起止时间: '06/21 12z-06/27 00z'(z 口径, 不带时长) —— 用户 2026-09-19
            f"{sft}-{eft}",
            f"{peak_txt} 实时:{current_wind}kt",
            f"气压:{cur_pres}hPa" if cur_pres else "气压:-",
            pos_txt,
            # 总 ACE 去掉(用户 2026-09-19), 只留实时 ACE 数字
            "实时ACE:",
        ]
        # 模拟模式: 多一行**预报**(巅峰/登陆强度) —— 用户 2026-09-19
        # "巅峰及登陆强度要显示(提前一报的好处就是这个)"。数据来自内核集合
        # 统计(tc['fcPeak']/['fcLand']), 由适配器挂到 ty 上。
        _fc_line = self._sim_forecast_line(ty)
        if _fc_line:
            lines.append(_fc_line)
        line_h = self._season_info_box_line_height()
        text_x = 16
        y = _BOX_PAD
        st_surf = rt(_font_box, cur_st, tc)
        ace_x = ace_y = 0
        for i, ln in enumerate(lines):
            max_w = box_w - text_x - 8
            if i == 0:
                # 第一行最右边：当前性质（首行文字避让）
                max_w -= st_surf.get_width() + 8
                box.blit(st_surf, (box_w - 10 - st_surf.get_width(), y))
            surf_ln = rt(_font_box, ln, tc, max_w)
            if (surf_ln.get_height() > line_h + 2
                    or surf_ln.get_width() > max_w + 1):
                # 长名称触发 wrap 换行(会与下一行重叠)或横向溢出(无空格单词):
                # 逐字截断为单行
                surf_ln = _fit_box_line(_font_box, ln, tc, max_w)
            box.blit(surf_ln, (text_x, y))
            if i == 5:
                ace_x = text_x + surf_ln.get_width()
                ace_y = y
            y += line_h

        # ── 存入缓存 ──
        result = (box, ace_x, ace_y, tc)
        self._season_info_box_cache[ty] = result
        self._season_info_box_last_data[ty] = key_data
        return result

    _BOX_FADE_MS = 400
    _BOX_ALPHA_RATE = 255.0 / _BOX_FADE_MS

    def _box_alpha(self, ty, now: float) -> int:
        # 轮次 156: 防御性取值 —— `_box_anim` 只在 `draw_season_info_boxes` 里懒初始化,
        # 其它调用者(如模拟模式)可能先到这里; 原来直接 `self._box_anim.get(...)`
        # 会在未初始化时抛 AttributeError, 而调用点包在 `except: logger.debug` 里
        # ⇒ **整个信息框静默消失**。
        st = (getattr(self, '_box_anim', None) or {}).get(ty)
        if st is None:
            return 255
        t = now - st['start']
        if st['state'] == 'in':
            a = st['from'] + t * self._BOX_ALPHA_RATE
            if a >= 255:
                self._box_anim.pop(ty, None)
                return 255
        else:
            a = st['from'] - t * self._BOX_ALPHA_RATE
        return max(0, min(255, int(a)))

    def _sim_forecast_line(self, ty) -> str:
        """模拟模式信息框的预报行(非模拟模式返回 '')。

        格式与其它行一致(MM/DD HHZ); 峰值带 P10~P90 不确定度; 登陆强度在
        不足 40% 集合成员登陆时不给(避免编一个假的确定值)。
        """
        if getattr(self, 'md', None) != getattr(self, 'MODE_SIM', 'sim'):
            return ''
        pk = getattr(ty, 'fcPeak', None)
        ld = getattr(ty, 'fcLand', None)
        if not pk and not ld:
            return ''

        out = []
        if pk:
            _s = '预巅峰:%dkt' % round(float(pk['kt']))
            if pk.get('kt_lo') is not None and pk.get('kt_hi') is not None:
                _s += '(%d~%d)' % (round(float(pk['kt_lo'])), round(float(pk['kt_hi'])))
            if pk.get('h') is not None:
                _s += ' +%dh' % round(float(pk['h']))
            out.append(_s)
        if ld:
            out.append('预登陆:%dkt +%dh' % (round(float(ld['kt'])),
                                           round(float(ld.get('h') or 0))))
        return '  '.join(out)

    def blit_info_box(self, surface: pygame.Surface, ty, box_w: int, box_h: int,
                      x: int, y: int, alpha: int = 255) -> None:
        """画**一个**信息框(含"实时ACE"数字)。

        轮次 156: 从 `draw_season_info_boxes` 的循环体里抽出来, 让**风季与模拟模式
        共用同一份**渲染 —— 模拟模式原先直接调 `_render_info_box` 却只 blit
        `_res[0]`, 把 `ace_x/ace_y/tc` 丢掉了, 于是框里"实时ACE:"后面**永远空白**
        (该函数的文档明确要求"数字由调用方每帧单独绘制")。

        alpha<255 时走风季那套"从左侧滑入 + 淡入淡出"。
        """
        box, ace_x, ace_y, tc = self._render_info_box(ty, box_w, box_h)
        # 实时ACE数字：按 (量化cace, tc) 缓存复用，避免每帧裸 render(法9/K27)
        digit_key = (round(ty.cace, 4), tc)
        digits = _ace_digit_cache.get(digit_key)
        if digits is None:
            digits = _font_box.en_font.render(f"{ty.cace:.4f}", True, tc)
            if len(_ace_digit_cache) >= _ACE_DIGIT_CACHE_MAX:
                _ace_digit_cache.pop(next(iter(_ace_digit_cache)))
            _ace_digit_cache[digit_key] = digits
        if alpha < 255:
            t = alpha / 255.0
            ease = 1.0 - (1.0 - t) ** 3
            x = int(x - (x + box_w) * (1.0 - ease))
            faded = _faded_copy(box, alpha)
            surface.blit(faded, (x, y))
            d = digits.copy()
            d.set_alpha(alpha)
            surface.blit(d, (x + ace_x, y + ace_y))
        else:
            surface.blit(box, (x, y))
            surface.blit(digits, (x + ace_x, y + ace_y))
        # ── S4: 登记**实际绘制**的矩形(含上面滑入动画后的 x) 供命中判定共用 ──
        _rect = pygame.Rect(x, y, box_w, box_h)
        self._info_box_hit_record(ty, _rect)
        # 悬停反馈: 环画在框**外侧**(inflate(8))而不是压在原边框上 ——
        # 原边框宽 3px + 12px 圆角时, 弧段会向框内侵入 ~5.6px, 实测**碰到了 32 个
        # 文字亮像素**(见 G:/tmp/paint/s4_layout_check2.py)。改成外扩环之后框内
        # 逐像素不变(实测 0), 且 4px 间隙正好落在 4/8px 间距基准上。
        # 双色(描边 + 主色): 描边比主色环各宽 1px, 保证在**任何**底色上都 ≥3:1
        # (浅色界面/亮陆地靠描边, 深色地图靠主色), 依据见文件头注释与报告 §6。
        if getattr(self, '_info_box_hover_ty', None) is ty:
            _core, _casing = self._info_box_hover_colors()
            _ring = _rect.inflate(8, 8)
            pygame.draw.rect(surface, _casing, _ring.inflate(2, 2),
                             _INFO_BOX_HOVER_RING_W + 2,
                             _INFO_BOX_HOVER_RADIUS + 5)
            pygame.draw.rect(surface, _core, _ring,
                             _INFO_BOX_HOVER_RING_W, _INFO_BOX_HOVER_RADIUS + 4)

    # ── S4: 信息框命中登记 / 悬停 / 点击跳转 ──────────────────────────────
    def _info_box_hit_prune(self, now: int = None) -> None:
        """丢弃过期的命中登记(陈旧帧/上一个模式留下的框)。"""
        now = pygame.time.get_ticks() if now is None else now
        hit = getattr(self, '_info_box_hit', None)
        if not hit:
            return
        for k in [k for k, (_r, t) in hit.items()
                  if now - t > _INFO_BOX_HIT_MAX_AGE_MS]:
            hit.pop(k, None)

    def _info_box_hit_record(self, ty, rect: pygame.Rect) -> None:
        """由 `blit_info_box` 在绘制时调用 —— **命中矩形的唯一来源**。"""
        if not hasattr(self, '_info_box_hit'):
            self._info_box_hit = {}
        now = pygame.time.get_ticks()
        self._info_box_hit[ty] = (pygame.Rect(rect), now)
        # 顺带做一次轻量清理(每帧 O(框数), 不新增遍历成本)
        if len(self._info_box_hit) > 1:
            self._info_box_hit_prune(now)

    def _info_box_at(self, pos):
        """点/悬停位置命中哪个信息框 → `(ty, rect)`; 未命中 `(None, None)`。

        只认**最近画过**(≤ `_INFO_BOX_HIT_MAX_AGE_MS`)且**包含该点**的框 ⇒
        热区 = 视觉区(整框, 远大于桌面端 32px 下限), 框外 1px 自然不命中。
        """
        self._info_box_hit_prune()
        hit = getattr(self, '_info_box_hit', None)
        if not hit:
            return None, None
        for ty, (rect, _t) in list(hit.items()):
            if rect.collidepoint(pos):
                return ty, rect
        return None, None

    def _info_box_hover_colors(self):
        """悬停环的 `(主色, 描边)` —— 按 `dark_mode` 取描边, 主色双模式同色。

        数据依据(实测 N=3084 个环带背景像素, 两种 `dark_mode` 统计相同):
          · 琥珀+黑描边: 逐像素 `max(con(主色,B), con(描边,B))` = 最小 **4.03**、
            中位 **13.71** ⇒ 任意底色都 ≥3:1;
          · 换"适合浅底的深色"(靛蓝+白描边) 最小只有 **2.57** ✗ —— 浅底问题不是
            靠换主色解决的, 而是靠描边把它与浅色界面/亮陆地隔开(描边对浅色面板边框
            5.45:1、对亮陆地 10.13:1)。候选表见报告 §6。
        """
        if getattr(self, 'dark_mode', True):
            return _INFO_BOX_HOVER_RING, _INFO_BOX_HOVER_RING_CASING_DARK
        return _INFO_BOX_HOVER_RING, _INFO_BOX_HOVER_RING_CASING_LIGHT

    def _info_box_set_hand_cursor(self, on: bool) -> None:
        """可点击提示: 手型光标。headless/不支持光标的驱动下静默跳过。"""
        want = bool(on)
        if getattr(self, '_info_box_hand_cursor', False) == want:
            return
        try:
            cur = (pygame.SYSTEM_CURSOR_HAND if want
                   else pygame.SYSTEM_CURSOR_ARROW)
            pygame.mouse.set_cursor(cur)
        except Exception:
            pass
        self._info_box_hand_cursor = want

    def _info_box_hover_update(self, pos) -> None:
        """鼠标移动时调用(只读命中登记, **不消费事件**)。"""
        if getattr(self, 'dialog_mgr', None) is not None \
                and self.dialog_mgr.any_active():
            self._info_box_hover_ty = None
            self._info_box_set_hand_cursor(False)
            return
        ty, _r = self._info_box_at(pos)
        self._info_box_hover_ty = ty
        self._info_box_set_hand_cursor(ty is not None)

    def _info_box_jump_to(self, ty) -> bool:
        """镜头跳到该台风当前位置(复用现有居中能力, 不另造坐标变换)。

        * 位置取自 `ty.cpos()` —— **与信息框"位置:"那一行同一个来源**
          (`_render_info_box` 用 `ty.cp()`), 所以"点它跳过去"与"框里写的坐标"
          必然一致;
        * 只调 `map_mgr.center_on_latlon()`(现成的居中 API, 自带经度环绕与
          纬度 clamp), 再走现有那套"视图变了"的失效钩子
          (`_view_dirty` / `invalidate_screen_points_lazy`, 与 `track_mixin.py:330-331`
          的同款处理), 不复制任何投影数学。
        """
        try:
            pos = ty.cpos() if hasattr(ty, 'cpos') else None
            if not pos:
                cp = ty.cp() if hasattr(ty, 'cp') else None
                pos = {'la': cp['la'], 'lo': cp['lo']} if cp else None
            if not pos:
                return False
            mv = getattr(getattr(self, 'map_mgr', None), 'map_view', None)
            if mv is None:
                return False
            # ⚠ 居中 API 在 **MapView** 上(`app/map_mgr.py:337`), 不在 MapManager 上
            #   —— 写成 `self.map_mgr.center_on_latlon(...)` 会 AttributeError,
            #   被下面的 except 吞掉 ⇒ "点了没反应"。实测踩过(见报告 §调试)。
            mv.center_on_latlon(float(pos['la']), float(pos['lo']))
            # 视图变了 ⇒ 复用现有失效钩子(下一帧按新视图重投影/重建路径缓存)
            self._view_dirty = True
            self.invalidate_screen_points_lazy()
            # 模拟模式: 顺带把它设为选中(信息框本来就只画选中那只, 这里保证语义一致)
            if getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim'):
                for _tid, _ty in (getattr(self, 'sim_path_tys', None) or {}).items():
                    if _ty is ty:
                        self.sim_sel_tc_id = _tid
                        break
            return True
        except Exception:
            if hasattr(self, 'logger'):
                self.logger.debug('信息框跳转失败', exc_info=True)
            return False

    # ── S4b(2026-10-05): 多框布局 + 滚轮滑动 ────────────────────────────────
    #: 可见视口下沿为提示条预留的高度(提示条**不与任何框重叠** —— 它落在预留带里)
    _INFO_BOX_HINT_H = 18
    #: 滚动热区横向比框宽出多少(每侧) —— 用户要求"矩形应该略宽于信息框"
    _INFO_BOX_HOTSPOT_PAD_X = 8
    #: 视口底部留白(避免贴住功能栏)
    _INFO_BOX_BOTTOM_MARGIN = 8

    def _info_box_viewport(self):
        """信息框**可见视口**(地图区内的一列): `(rect, hint_h)`。

        y 从 `START_Y`(既有约定, 上面是圆环钟) 到 `map_height - 留白 - 提示带`;
        x 只占既有那一列(350px)。单列是**刻意**的: 滚动已经让"所有台风都摸得到",
        多列只会白占地图(实测 1600x900 横向能放 4 列)。
        """
        mh = getattr(self, 'map_height', 900)
        start_x = SEASON_INFO_BOX_START_X
        start_y = SEASON_INFO_BOX_START_Y
        box_w = SEASON_INFO_BOX_WIDTH
        hint = self._INFO_BOX_HINT_H
        bottom = max(start_y + 40, mh - self._INFO_BOX_BOTTOM_MARGIN - hint)
        return pygame.Rect(start_x, start_y, box_w, bottom - start_y), hint

    def _info_box_hotspot(self):
        """滚轮热区: **不可见**(从不绘制), 略宽于框, **纵向覆盖整列**(含框间空白)。

        用户原话: "在信息框的地方做一矩形, 无可见外观, 但鼠标放到矩形里滚动不是
        缩放而是滑动信息框, **即使在信息框之间的空白区域**; 矩形应该略宽于信息框"。
        """
        vp, _h = self._info_box_viewport()
        pad = self._INFO_BOX_HOTSPOT_PAD_X
        return pygame.Rect(vp.left - pad, vp.top - 4, vp.width + 2 * pad,
                           vp.height + 8 + self._INFO_BOX_HINT_H)

    def _info_box_scroll_max(self, n_boxes: int, box_h: int) -> int:
        """可滚动的最大偏移 = 内容高度 − **可见视口高度**(下限 0) —— 用来做钳制。

        ⚠ 这里**不能**再减提示带: `_info_box_viewport()` 返回的 vp 已经把提示带
        扣掉了(它到 `map_height - 留白 - 提示带` 为止)。第一版重复扣了一次 ⇒
        滚到底时最后一个框仍然差 18px 露不出来(实测 max 2431 vs 应有 2449)。
        """
        vp, _hint = self._info_box_viewport()
        step = box_h + SEASON_INFO_BOX_SPACING_Y
        content = max(0, n_boxes * step - SEASON_INFO_BOX_SPACING_Y)
        return max(0, content - vp.height)

    def _info_box_scroll_by(self, notches: int, box_h: int, n_boxes: int) -> bool:
        """滚轮路由入口: 返回 True = 已消费(热区内且**内容确实超出一屏**)。

        ⚠ 内容不足一屏时**不消费** ⇒ 事件回落到地图缩放(用户在一个空矩形上滚
        半天没反应还缩放不了, 是最糟的体感) —— 这是刻意选择, 见报告 §7。

        步进策略: 一次滚轮 = **一个框高**(整框滚动, 观感稳定); 末尾额外允许停在
        `top`(贴底)这个"半格"位置, 保证**最后一个框一定能露全**(否则按整步走会
        永远差一格)。
        """
        mx, my = pygame.mouse.get_pos()
        if my >= getattr(self, 'map_height', 900):
            return False                       # 与功能栏/窗口底部不相交: 不串事件
        if getattr(self, 'dialog_mgr', None) is not None and self.dialog_mgr.any_active():
            return False
        if not self._info_box_hotspot().collidepoint((mx, my)):
            return False
        top = self._info_box_scroll_max(n_boxes, box_h)
        if top <= 0:
            return False                       # 一屏放得下 ⇒ 交回地图缩放
        step = box_h + SEASON_INFO_BOX_SPACING_Y
        cur = max(0, min(top, int(getattr(self, '_info_box_scroll', 0) or 0)))
        if int(notches) < 0:                   # 向下滚(看更下面的)
            nxt = (cur // step + 1) * step
            self._info_box_scroll = top if nxt >= top else nxt
        elif int(notches) > 0:                 # 向上滚
            if cur >= top:                     # 从"贴底"回到最后一个整步
                self._info_box_scroll = (top // step) * step
            else:
                self._info_box_scroll = max(0, ((cur - 1) // step) * step if cur > 0 else 0)
        return True

    def _info_box_ordered(self, items):
        """决定"哪些台风先出现在上面"的排序(理由见报告 §7)。

        ① **选中那只置顶**(正在看它);
        ② 存活优先(已消散的是历史);
        ③ 离视图中心近优先(用户正在看的区域);
        ④ 强度降序; ⑤ id 稳定兜底。
        滚动到底能看到全部, 所以这个顺序只决定"第一屏是谁"。
        """
        mv = getattr(getattr(self, 'map_mgr', None), 'map_view', None)
        clon = clat = None
        if mv is not None:
            try:
                clon = (mv.view_x + getattr(self, 'screen_width', 1600) / 2.0
                        / max(1e-6, mv.scale)) * mv._deg_per_px_x + mv.lon_min
                clat = mv.lat_max - (mv.view_y + getattr(self, 'map_height', 900)
                                     / 2.0 / max(1e-6, mv.scale)) * mv._deg_per_px_y
            except Exception:
                clon = clat = None

        def key(t):
            sel = 0 if self._info_box_is_selected(t) else 1
            try:
                p = t.cpos() or (t.cp() or {})
                la, lo = float(p.get('la', 0.0)), float(p.get('lo', 0.0))
            except Exception:
                la, lo = 0.0, 0.0
            if clon is None:
                dist = 0.0
            else:
                dist = abs(la - clat) + min(abs(lo - clon), 360.0 - abs(lo - clon))
            alive = 0 if not getattr(t, 'fin', False) else 1
            try:
                vmax = -max([float(q.get('vmax', 0) or 0)
                             for q in (t.pts or [])] or [0.0])
            except Exception:
                vmax = 0.0
            return (sel, alive, round(dist, 1), vmax, str(getattr(t, 'n', '')))

        return sorted(list(items), key=key)

    def _info_box_is_selected(self, ty) -> bool:
        """该台风是不是"当前选中"(模拟模式看 `sim_sel_tc_id`)。"""
        if getattr(self, 'md', None) != getattr(self, 'MODE_SIM', 'sim'):
            return False
        try:
            sid = getattr(self, 'sim_sel_tc_id', None)
            return sid is not None and (getattr(self, 'sim_path_tys', None) or {}).get(sid) is ty
        except Exception:
            return False

    def _info_box_height_for(self, ty) -> int:
        """**单个**框需要的高度。

        ⚠ 必修: 模拟模式的信息框会多一行"预报"(`_render_info_box` 在
        `_sim_forecast_line()` 非空时 append 第 7 行, 见 `:301-306`)。原实现按
        `_season_info_box_height()`(固定 6 行)给高度, 于是**有预报那几只的预报行
        画到了 box 面之外 ⇒ 静默丢掉**(S4b 把单框改成共用布局时引入)。现在按框
        各自算高度; 布局的步进取**最大**高度, 保证栅格仍然整齐、不会互相压。
        """
        h = self._season_info_box_height()
        if getattr(self, 'md', None) == getattr(self, 'MODE_SIM', 'sim'):
            try:
                if self._sim_forecast_line(ty):
                    h += self._season_info_box_line_height()
            except Exception:
                pass
        return h

    def draw_info_boxes_scrolling(self, surface: pygame.Surface, items,
                                  clear_registry: bool = True) -> int:
        """**两模式共用**的多框绘制: 单列 + 滚动 + 视口裁剪 + 溢出提示。

        返回实际画出的框数。命中登记就在 `blit_info_box` 里完成(带滚动后的真实 y),
        所以"点第 k 个框"天然跟着滚动偏移走, 不需要第二套坐标 —— 实测见报告 §7。
        """
        if clear_registry and hasattr(self, '_info_box_hit'):
            self._info_box_hit.clear()
        box_w = SEASON_INFO_BOX_WIDTH
        # 逐框高度(有"预报"行的框高一行); 步进取**最大**高度, 栅格才整齐
        h_of = {id(ty): self._info_box_height_for(ty) for ty in items}
        box_h = max(list(h_of.values()) or [self._season_info_box_height()])
        step = box_h + SEASON_INFO_BOX_SPACING_Y
        vp, hint_h = self._info_box_viewport()
        ordered = self._info_box_ordered(items)
        n = len(ordered)
        # 热区钳制要用**内容里的框总数**(不是"这一帧画出来的几个") —— 否则滚不动,
        # 实测第一版把 19 个框的可滚范围按 4 个算, 只滚了 76px(应有 2449px)。
        self._info_box_content_n = n
        # 记录本帧生效的高度/步进: 滚轮路由用它算钳制, 避免两处各算一遍而漂移
        self._info_box_box_h = box_h
        top = self._info_box_scroll_max(n, box_h)
        off = max(0, min(top, int(getattr(self, '_info_box_scroll', 0) or 0)))
        self._info_box_scroll = off
        now = pygame.time.get_ticks()
        prev_clip = surface.get_clip()
        surface.set_clip(vp)                     # ← 裁剪: 视口外**一个像素都不画**
        drawn = 0
        sel_rect = None
        for i, ty in enumerate(ordered):
            hi = h_of.get(id(ty), box_h)
            y = vp.top + i * step - off
            # 只画**完整落在视口内**的框: 半个框露在视口沿上会把文字裁一半 ——
            # `ui-experience` 最高优先级那条("文字不许被截断")优先于"多露一点"。
            if y < vp.top or y + hi > vp.bottom:
                continue
            alpha = self._box_alpha(ty, now)
            if alpha <= 0:
                continue
            self.blit_info_box(surface, ty, box_w, hi, vp.left, y, alpha)
            if self._info_box_is_selected(ty):
                sel_rect = pygame.Rect(vp.left, y, box_w, hi)
            drawn += 1
        # 选中标记: 框**左侧**竖条(在框外 ⇒ 不压任何文字, 也不动排版)
        if sel_rect is not None:
            pygame.draw.rect(surface, (120, 200, 255),
                             pygame.Rect(sel_rect.left - 6, sel_rect.top, 4,
                                         sel_rect.height))
        # 溢出/位置提示(落在预留带里, 不与任何框重叠): 不许静默截断
        # ⚠ 必须在**恢复裁剪之后**画 —— 提示条位于 vp 之下(vp.bottom+1), 第一版在
        #   裁剪还开着时画, 于是被 `set_clip(vp)` 整个裁掉(实测提示条像素 = 0)。
        surface.set_clip(prev_clip)
        first_vis = -(-off // step)                       # ceil: 第一个完整可见的序号
        last_vis = (off + vp.height - box_h) // step      # floor: 最后一个
        if last_vis < first_vis - 1:
            last_vis = first_vis - 1
        hidden_above = max(0, first_vis)
        hidden_below = max(0, n - 1 - last_vis)
        parts = []
        if hidden_above:
            parts.append('↑ 上方还有 %d 个' % hidden_above)
        if hidden_below:
            parts.append('↓ 还有 %d 个 (滚轮滑动)' % hidden_below)
        if parts:
            ts = rt(_font_box, '  '.join(parts), (255, 210, 110))
            surface.blit(ts, (vp.left + 2, vp.bottom + 1))
        return drawn

    def draw_season_info_boxes(self, surface: pygame.Surface) -> None:
        now = pygame.time.get_ticks()
        # ── S4: 本模式每帧都从这里开始画框 ⇒ 先清空命中登记 ──
        # 事件处理发生在本帧绘制**之前**, 读到的永远是"上一帧画完时"的登记 ——
        # 也就是用户此刻屏幕上真正看到的那批框, 语义正确。
        if hasattr(self, '_info_box_hit'):
            self._info_box_hit.clear()
        if not hasattr(self, '_box_anim'):
            self._box_anim = {}
        # 仅活跃台风显示信息框（act=True, ss=True, sf=False）
        active_typhoons = [t for t in self.tys if t.act and t.ss and not t.sf]

        # 已结束的台风：先淡出，淡出完成后释放 slot
        for ty in list(self.info_box_slots.keys()):
            if ty in active_typhoons:
                continue
            st = self._box_anim.get(ty)
            if st is None or st['state'] != 'out':
                self._box_anim[ty] = {'state': 'out', 'start': now,
                                      'from': self._box_alpha(ty, now)}
            elif self._box_alpha(ty, now) <= 0:
                slot = self.info_box_slots.pop(ty)
                self.info_box_free_slots.append(slot)
                self.info_box_free_slots.sort()
                self._season_info_box_cache.pop(ty, None)
                self._season_info_box_last_data.pop(ty, None)
                self._box_anim.pop(ty, None)

        # 分配新 slot（淡入）
        # ⚠ S4b: slot 现在只是"加入顺序/淡入动画"的簿记, **不再决定 y 坐标**
        #   (坐标由 `draw_info_boxes_scrolling` 的滚动偏移给出)。池子用完就继续
        #   往上加号 —— 用户要求"所有台风都画出来", 不能被 8 个槽位卡住。
        for ty in active_typhoons:
            if ty not in self.info_box_slots:
                if self.info_box_free_slots:
                    slot = self.info_box_free_slots.pop(0)
                else:
                    slot = max(self.info_box_slots.values(), default=-1) + 1
                self.info_box_slots[ty] = slot
                self._box_anim[ty] = {'state': 'in', 'start': now, 'from': 0}
            else:
                st = self._box_anim.get(ty)
                if st and st['state'] == 'out':
                    self._box_anim[ty] = {'state': 'in', 'start': now,
                                          'from': self._box_alpha(ty, now)}

        # 轮次 156: 单个框的渲染抽成共用方法; S4b: 位置/滚动/裁剪/提示走共用布局
        self.draw_info_boxes_scrolling(surface, active_typhoons,
                                       clear_registry=False)

        # ⚠ S4b: 原来的"8 槽溢出提示"删掉了 —— 它已经不成立:
        #   ① slot 池会用完就继续加号, 所有活跃台风都有框;
        #   ② "没显示"这件事现在由**滚动提示**表达("↓ 还有 N 个 (滚轮滑动)"),
        #      而且真的能滚到 —— 比一行"未显示"更符合用户要求, 也不静默截断。
        # S4: 悬停目标这一帧没被画出来(台风结束/换了模式) ⇒ 清高亮与手型光标
        _hv = getattr(self, '_info_box_hover_ty', None)
        if _hv is not None and _hv not in (getattr(self, '_info_box_hit', None) or {}):
            self._info_box_hover_ty = None
            self._info_box_set_hand_cursor(False)

    def _active_clock_tuple(self):
        """正常/编辑模式的圆环钟时间 (年,月,日,时,分,当日进度)。

        **2026-09-19 修**: 这两个模式的 `_clock` 原先不传 time_tuple, 钟就直接读
        `self.sy / self.st / self.ste` —— 那是**风季控制器**的字段, 正常/编辑模式
        里没人更新, 于是永远停在初始值(用户:"正常/编辑模式的时间轴坏了")。
        改成取当前台风(编辑模式取被编辑台风)的**当前报点时刻**, 按 at/points_time
        做秒级插值; 一个台风都没有时退回系统当前时间(总比显示 2025-01-01 强)。
        """
        try:
            ty = (self.edit_typhoon if self.md == self.MODE_EDIT
                  else self.current_typhoon())
        except Exception:
            ty = None
        dt = None
        try:
            pts = getattr(ty, 'pts', None) or []
            pd = getattr(ty, 'points_dt', None) or []
            pt = getattr(ty, 'points_time', None) or []
            ci = int(getattr(ty, 'ci', 0) or 0)
            if pts and len(pd) == len(pts) and 0 <= ci < len(pd):
                dt = pd[ci]
                if (ci + 1 < len(pd) and ci + 1 < len(pt)
                        and float(pt[ci + 1]) > float(pt[ci])):
                    span = float(pt[ci + 1]) - float(pt[ci])
                    frac = max(0.0, min(1.0, (float(getattr(ty, 'at', 0.0) or 0.0)
                                              - float(pt[ci])) / span))
                    dt = dt + (pd[ci + 1] - dt) * frac
        except Exception:
            dt = None
        if dt is None:
            dt = datetime.now()
        day_secs = dt.hour * 3600 + dt.minute * 60 + dt.second
        return (dt.year, dt.month, dt.day, dt.hour, dt.minute,
                day_secs / 86400.0)

    def draw_season_clock(self, surface: pygame.Surface, origin=(0, 0),
                          time_tuple=None) -> None:
        """左上角时间轴(圆环钟)。origin: 绘制位置(各模式可不同);
        time_tuple: (year, month, day, hour, minute, day_progress) 自定义时间
        (模拟模式传模拟日历); 缺省用风季模式的 self.sy/self.st/self.ste。"""
        if TySimDrawInfoBoxesMixin._clock_arc_surf is None:
            TySimDrawInfoBoxesMixin._clock_arc_surf = pygame.Surface((240, 240), pygame.SRCALPHA)
        tr = 80
        inner_r = 56
        cx = cy = 120
        arc_inner = inner_r + 5
        arc_outer = tr - 2

        if time_tuple is not None:
            sy2, st_month, st_day, hour, minute, progress = time_tuple
            year_str = str(sy2)
            month_str = _MONTHS[st_month] if 1 <= st_month <= 12 else str(st_month)
            day_str = str(st_day)
        else:
            ste = getattr(self, 'ste', 0)
            day_seconds = ste % (24 * 3600)
            progress = day_seconds / (24 * 3600)
            year_str = str(self.sy)
            month_idx = int(self.st[0:2])
            month_str = _MONTHS[month_idx] if 1 <= month_idx <= 12 else self.st[0:2]
            day_str = str(int(self.st[2:4]))
            hour = int(day_seconds / 3600)
            minute = int((day_seconds % 3600) / 60)

        tmp = pygame.Surface((240, 240), pygame.SRCALPHA)

        # 外环 + 内环（白色空心）
        pygame.draw.circle(tmp, (255, 255, 255), (cx, cy), tr, 2)
        pygame.draw.circle(tmp, (255, 255, 255), (cx, cy), inner_r, 2)

        # 进度弧（多边形精确填充，两环之间留缝隙）
        if progress > 0.001:
            arc_surf = self._clock_arc_surf
            arc_surf.fill((0, 0, 0, 0))
            steps = max(4, int(progress * 120))
            start_a = _CLOCK_START_A
            step_angle = progress * _CLOCK_TAU / steps
            cos_a = [_cos_lut(start_a + i * step_angle) for i in range(steps + 1)]
            sin_a = [_sin_lut(start_a + i * step_angle) for i in range(steps + 1)]
            pts_out = [(cx + arc_outer * c, cy + arc_outer * s) for c, s in zip(cos_a, sin_a)]
            pts_in = [(cx + arc_inner * c, cy + arc_inner * s)
                      for c, s in zip(reversed(cos_a), reversed(sin_a))]
            pygame.draw.polygon(arc_surf, (255, 255, 255), pts_out + pts_in)
            tmp.blit(arc_surf, (0, 0))

        # 年份（上方，紧贴外环）
        year_surf = _stroked(_font_sub, year_str, (255, 255, 255))
        yx = cx - (year_surf.get_width() - 2) // 2 - 1
        yy = cy - tr - (year_surf.get_height() - 2) - 1
        tmp.blit(year_surf, (yx, yy))

        # 月份 + 日期（偏下）
        month_surf = _stroked(_font_month, month_str, (255, 255, 255))
        day_surf = _stroked(_font_date, day_str, (255, 255, 255))
        gap_md = 2
        month_h = month_surf.get_height() - 2
        day_h = day_surf.get_height() - 2
        total_md_h = month_h + day_h + gap_md
        md_top = cy - total_md_h // 2
        mx = cx - (month_surf.get_width() - 2) // 2
        dx = cx - (day_surf.get_width() - 2) // 2
        dy_s = md_top + month_h + gap_md
        tmp.blit(month_surf, (mx - 1, md_top - 1))
        tmp.blit(day_surf, (dx - 1, dy_s - 1))

        # 时分（下方）
        time_str = f"{hour:02d}{minute:02d}Z"
        time_surf = _stroked(_font_sub, time_str, (255, 255, 255))
        text_x = cx - (time_surf.get_width() - 2) // 2 - 1
        text_y = cy + tr + 5 - 1
        tmp.blit(time_surf, (text_x, text_y))

        surface.blit(tmp, origin)

    def draw_control_panel(self, surface) -> None:
        if not hasattr(self, '_panel') or self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.build()
        self._panel.draw(surface)

    @property
    def control_panel(self):
        if not hasattr(self, '_panel') or self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.build()
        return self._panel

    def draw_ace_display(self, surface):
        if self.md == self.MODE_NORMAL or self.md == self.MODE_EDIT:
            ty = self.current_typhoon() if self.md == self.MODE_NORMAL else self.edit_typhoon
            if ty and ty.pts:
                self._draw_progress(surface, f"{self.get_display_name(ty)} ACE:",
                                    ty.cace, ty.tace, self.screen_width - 10)
            return

        if self.md == getattr(self, 'MODE_SIM', 'sim'):
            # 模拟模式用**同一套** ACE 条(用户 2026-09-19: "ace 条像其它模式一样显示.
            # 上限默认 1500. 突破了就停在白框最右面")。_draw_progress 内部
            # min(1.0, cur/total) 已保证封顶停在右边框。
            sim = getattr(self, 'sim_v4', None) or {}
            cur = float(sim.get('aceSum', 0.0) or 0.0)
            cap = float(getattr(self.cfg, 'sim_ace_cap', 1500.0) or 1500.0)
            try:
                yr = self._sim_calendar(sim, disp=True)[0]
            except Exception:
                yr = datetime.now().year
            # 只画进度条与条内数值: **不画"总 ACE"那一行**
            # (用户 2026-09-19: "右上角始终显示 2026: 1500.0000, 这个删掉")
            self._draw_progress(surface, f"{yr} ACE:", cur, cap,
                                self.screen_width - 10, show_total=False)
            return

        ace_year = self.current_ace_year
        year_str = str(ace_year) if self.hemisphere == HEMISPHERE_NORTH else f"{ace_year}-{ace_year + 1}"

        lm = self.ace_limit_mode
        bc = self.ace_limit_basin
        if lm == 'basin' and bc:
            area = self.res_mgr.ocean_areas.get_by_code(bc)
            label = f"{year_str} {area.name_full if area else bc} ACE:"
        else:
            label = f"{year_str} ACE:"

        cya = self.yad.get(ace_year, 0.0)
        self._draw_progress(surface, label, self.csa, cya, self.screen_width - 10)

    def _draw_progress(self, surface, label, current_ace, total_ace, right,
                       show_total: bool = None):
        w, h = 495, 45
        x = right - w

        title = _stroked(_font_ace_title, "Accumulated Cyclone Energy", (200, 200, 210))
        surface.blit(title, (x - 1, 8 - 1))
        # 先画黄条进度, 再画白框描边(白框覆盖黄条, 避免黄条压住边框)
        if total_ace > 0:
            fw = int(w * min(1.0, current_ace / total_ace))
            pygame.draw.rect(surface, (255, 200, 0), (x, 32, fw, h))
        pygame.draw.rect(surface, (255, 255, 255), (x, 32, w, h), 3)

        # 数值每帧变化（插值模式），按量化值缓存合成面
        val = f"{current_ace:.4f}"
        key = round(current_ace, 4)
        surf = _ace_digit_cache.get(key)
        if surf is None:
            vf = _font_ace.en_font
            white = vf.render(val, True, (255, 255, 255))
            black = vf.render(val, True, (0, 0, 0))
            tw, th = white.get_width(), white.get_height()
            surf = pygame.Surface((tw + 2, th + 2), pygame.SRCALPHA)
            for dx, dy in _STROKE:
                surf.blit(black, (dx + 1, dy + 1))
            surf.blit(white, (1, 1))
            if len(_ace_digit_cache) >= _ACE_DIGIT_CACHE_MAX:
                _ace_digit_cache.pop(next(iter(_ace_digit_cache)))
            _ace_digit_cache[key] = surf
        tw, th = surf.get_width(), surf.get_height()
        text_x = x + w - tw - 11
        text_y = 32 + (h - th) // 2
        surface.blit(surf, (text_x, text_y))

        self._draw_ace_note(surface, x, 32, h)

        _st = getattr(self, 'show_ace_total', True) if show_total is None else show_total
        if _st:
            ls = _stroked(_font_ace, label, (255, 255, 255))
            surface.blit(ls, (right - ls.get_width() + 1, 89 - 1))
            ys = _stroked(_font_ace, f"{total_ace:.4f}", (255, 255, 255))
            surface.blit(ys, (right - ys.get_width() + 1, 137 - 1))

    def _draw_ace_note(self, surface, bar_x, bar_y, bar_h):
        """在 ACE 进度条内左侧显示最近结束台风的 '台风名 +ACE'。"""
        note = getattr(getattr(self, 'playback_ctrl', None), 'ace_note', None)
        if not note:
            return
        elapsed = pygame.time.get_ticks() - note['time']
        if elapsed >= _ACE_NOTE_DURATION_MS:
            return
        txt = (f"{note['name']} " if note['name'] else "") + f"+{note['ace']:.4f}"
        white = rt(_font_ace_note, txt, (255, 255, 255))
        # 描边用加深的强度色(原台风色过亮, 与白字对比不足; 再深一档提高可读性)
        sc = tuple(int(c * 0.55) for c in (note['color'] or (255, 255, 255))[:3])
        colored = rt(_font_ace_note, txt, sc)
        remain = _ACE_NOTE_DURATION_MS - elapsed
        if remain < _ACE_NOTE_FADE_MS:
            alpha = max(0, int(255 * remain / _ACE_NOTE_FADE_MS))
            white = white.copy(); white.set_alpha(alpha)
            colored = colored.copy(); colored.set_alpha(alpha)
        nx = bar_x + 10
        ny = bar_y + (bar_h - white.get_height()) // 2
        for dx, dy in _STROKE:
            surface.blit(colored, (nx + dx, ny + dy))
        surface.blit(white, (nx, ny))