# py/ty_sim_mixins/track_mixin.py
"""镜头跟踪 Mixin：跟随台风平移地图，结束后自动总览完整路径。

风季(MODE_SEASON): 跟随指定/全部活跃台风，全部结束后总览其路径。
正常(MODE_NORMAL): 跟随"当前播放台风"([ ] 切换/自动连播后自动改跟新台风)，
                   该台风播完后自动完整显示其路径；不提供键盘快捷键(仅按钮)。
"""
from __future__ import annotations

import math
import pygame

from ..config import AppConfig
from ..constants import FADE_DURATION, FADE_DURATION_QUICK

# 平滑跟随系数(60fps 每帧向目标位置插值的比例; 实际按 dt 折算, 帧率无关)
_TRACK_SMOOTH_K = 0.08
# 单台风跟随: 目标始终为台风当前位置; 全部跟随: 所有活跃台风位置 bbox 中心
# 视差死区(px): 目标与视中心偏差小于该值时不再平移(避免台风静止/镜头到位后每帧无谓重投影)
_TRACK_PAN_DEADZONE = 0.25
# 单帧实际位移小于该值(px)视为"没动": 目标在被地图边界钳制的那条轴上时,
# dx*k 会无限趋近 0, 若仍标记视图脏就会永远每帧重投影
_TRACK_PAN_MIN_STEP = 0.01
# 摇镜的三个阈值(单帧位移上限 / 目标瞬移判定 / 退出阈值)现在都是**用户可调设置**:
#   cfg.track_pan_max_step_px / track_jump_px / track_glide_exit_px(见 config.py),
#   本模块用 self._tcfg(...) 读 —— 这里不再保留第二份默认值。

# ═══════════════════ 收尾时序状态机(用户 2026-10-01 定的四步) ═══════════════════
#   idle → following → pan → settle → hl_in → hl_hold → hl_out → fade → done
#   ① 跟踪结束 → **停下来**(正常模式; 风季不暂停, 多台风并行)
#   ② **平滑移动+缩放**到"包含整条路径"的视野(逐帧插值, 不是 set_view_region 瞬跳),
#      边距 = cfg.track_view_pad_pct(默认 30%); 视野中心由**整条路径 bbox** 决定
#   ③ **高亮 2.4s**: 0.2s 渐入 → 2.0s 保持 → 0.2s 渐出(**单次, 不是闪烁**)
#   ④ 之后**让路径慢慢淡出**(从高亮渐出完起算, 时长 = cfg.fade_path_mode)
#   ⑤ done: **释放相机锁** —— 正常模式要用户显式操作才重新抓;
#      风季出现新的活跃台风则自动重新抓(见 `_track_season_next_active`)。
# 为什么要状态机而不是"每帧看 fin": playback_ctrl._update_normal 会在结束
# FADE_COOLDOWN_MS(500ms)后把 ty.fin 清成 False, 于是旧实现下一帧就以为
# "又有活跃台风了" ⇒ **总览被取消、相机在 +512ms 时又平移走**(实测路径 bbox 漂移
# 657/387 px, 半条路径被推出屏幕, 高亮和淡出都打在错误构图上)。
# 现在收尾一旦进入就**锁存**, 只有用户恢复播放 / 换台风 / 换模式 / 超时才退出。
#: 用户 2026-10-01 的**精确时序**(原话: "结束后平滑移动与缩放, 镜头到位 0.5s 后再
#: 2.4s 高亮(0.2s 渐入, 2s 高亮, 0.2s 渐出). 结束后即使仍然在跟踪也不锁定镜头"):
#:   idle → following → pan(平滑移+缩) → settle(0.5s) → hl_in(0.2s) → hl_hold(2.0s)
#:        → hl_out(0.2s) → fade(按 cfg) → done(**释放相机锁**)
#: ①"平滑移动与缩放"= 位置与 scale 都**逐帧插值**(smoothstep 缓入缓出), 不是
#:   `set_view_region` 那种瞬跳(实测原来一帧 9728px + scale×6)。
#: ②视野由**整条路径 bbox** 决定, **不锁定结束点**。
#: ③高亮是 **0→峰值→0 单次**渐变, **不是闪烁**(旧的 255/90 交替已删)。
_TRACK_FINALE_PHASES = ('pan', 'settle', 'hl_in', 'hl_hold', 'hl_out', 'fade', 'done')
#: ①②推镜时长 = **距离自适应**: `MIN + 距离(屏幕px)x PER_1K/1000`, 夹在 [MIN, MAX]。
#: 固定 1200ms 时远近目标速度差太多(实测 6500px 移动峰值 144px/帧, 而 19000px 的
#: 远景目标峰值 378px/帧 —— 那已经不像"平滑移动"了)。自适应后长距离时间更长,
#: 峰值被压下来; 短距离也不会慢吞吞。
                                   #   ⇒ 路径占较紧那根轴 1/(1+2*0.25) = 66.7%
                                   #   (用户: "范围不够大"; 原来 10% ⇒ 83%/太挤)
_TRACK_FINALE_MAX_MS = 90000       # 兜底超时(动画+静置+高亮+淡出 + 余量)


#: 收尾时序参数的**唯一真相** = AppConfig 的默认值(见 config.py 的 track_* 字段)。
#: 模块内不再写第二份默认值; 没有 cfg 的对象(测试桩)也走这里 ⇒ 行为一致。
_TRACK_CFG_DEFAULT = AppConfig()


class _TrackCfgMixin:  # 只为把访问器集中在一处(混入本文件的类)
    def _tcfg(self, name: str):
        cfg = getattr(self, 'cfg', None)
        if cfg is not None:
            try:
                return getattr(cfg, name)
            except AttributeError:
                pass
        return getattr(_TRACK_CFG_DEFAULT, name)


def _circular_lon_center(lons: list) -> float:
    """经度环形中心(跨 0°/360° 时取正确圆心)。"""
    if not lons:
        return 180.0
    cx = sum(math.cos(math.radians(lo)) for lo in lons) / len(lons)
    cy = sum(math.sin(math.radians(lo)) for lo in lons) / len(lons)
    return math.degrees(math.atan2(cy, cx)) % 360.0


def _lon_span_wrap(lons: list) -> float:
    """经度覆盖跨度(0-360 环形,取最大缺口反面的跨度)。"""
    if not lons:
        return 360.0
    srt = sorted(lons)
    n = len(srt)
    if n == 1:
        return 0.0     # 单点无跨度; 返回 360 会让总览缩到全球
    gaps = [(srt[(i + 1) % n] - srt[i]) % 360.0 for i in range(n)]
    return 360.0 - max(gaps)


class TySimTrackMixin(_TrackCfgMixin):
    """镜头跟踪: tracking_mode = 'off' | 'single' | 'all'。"""

    def _init_tracking(self) -> None:
        self.tracking_mode: str = 'off'
        self.tracking_typhoon = None
        self._tracking_seen: set = set()
        self._tracking_overview_done: bool = False
        # 用户 2026-10-01: 跟踪结束后的 **2 秒路径高亮**状态。
        #   `_track_hl_ty`  = 要高亮的台风(正常模式就是刚播完那个)
        #   `_track_hl_from`= 高亮开始的 tick —— 淡出**从这一刻才起算**
        #                     (所以时序是: 停下 -> 缩放整条路径 -> 高亮 2s -> 慢慢淡出)
        self._track_hl_ty = None
        self._track_hl_from: int = 0
        self._track_hl_s: float = 2.0
        # 正常模式: 当前被跟踪台风(可能已结束, 供播完后总览完整路径)
        self._tracked_ty = None
        # 相机"有界摇镜"状态: 跟踪目标瞬移(换台风)时置位, 见 `_track_pan_to`
        self._track_tgt_prev = None
        self._track_glide: bool = False
        # ── 收尾时序状态机(见文件顶部说明) ──
        self._track_phase: str = 'idle'
        self._track_phase_t0: int = 0
        self._track_finale_tys: list = []
        self._track_finale_md = None
        self._track_fade_from: int = 0
        self._track_hl_tys: list = []
        self._track_anim_dur: int = 0
        self.tracking_finale_reason: str = ''

    def set_tracking(self, mode: str, typhoon=None) -> None:
        """由跟踪选择对话框调用: mode = 'off' | 'single' | 'all'。"""
        self.tracking_mode = mode
        self.tracking_typhoon = typhoon
        self._tracking_seen.clear()
        self._tracking_overview_done = False
        self._tracked_ty = None
        # ⚠ 2026-10-01 (rev-simmix 复核): 收尾高亮三件状态也必须一起复位。
        # 之前只清 `_tracked_ty`, `_track_hl_ty` 会一直持有**上一个** Typhoon 引用
        # (台风列表重建时阻止 GC, 且语义上是"跟踪已关/已重开却仍标记着高亮目标")。
        # 现在过期判定(`_draw_path_mixin.py:1001-1003` 的 `ct < hl_from + 窗口`)
        # 正好让它不生效, 所以是潜伏状态而不是当前可见缺陷 —— 但一旦窗口判定改成
        # `>=` 或"未设置就高亮", 它会立刻复活。
        self._track_hl_ty = None
        self._track_hl_from = 0
        # 目标一变(开始/换台风)就清掉上一帧目标, 避免把"上次跟踪的位置"误判成瞬移
        self._track_tgt_prev = None
        # 收尾状态机一起复位(重新选跟踪对象 = 用户明确要重新开始)
        self._track_phase = 'idle'
        self._track_phase_t0 = 0
        self._track_finale_tys = []
        self._track_finale_md = None
        self._track_fade_from = 0
        self._track_hl_tys = []
        self._track_glide = False

    def tracking_label(self) -> str:
        """用于界面指示的小标签; 未跟踪返回空串。"""
        if self.tracking_mode == 'off':
            return ""
        if self.md == self.MODE_NORMAL:
            # 正常模式: 跟随的是当前播放台风, 名称动态显示
            ty = self.current_typhoon()
            if ty is not None:
                return f"镜头跟踪: {self.get_display_name(ty)}"
            return ""
        if self.tracking_mode == 'all':
            return "镜头跟踪: 全部活跃"
        if self.tracking_typhoon is not None:
            return f"镜头跟踪: {self.get_display_name(self.tracking_typhoon)}"
        return ""

    def _track_active_typhoons(self) -> list:
        """当前应被跟踪的台风(未出现/已结束的不跟踪)。"""
        if self.md == self.MODE_NORMAL:
            # 正常模式: 始终跟踪当前播放台风([ ] 切换/自动连播后自动改跟新台风)
            if self.tracking_mode == 'off':
                return []
            ty = self.current_typhoon()
            if ty is None or not ty.pts:
                # 当前台风无报点(或已被过滤): 清掉旧目标, 否则播完后会总览上一个台风
                self._tracked_ty = None
                return []
            self._tracked_ty = ty
            if ty.fin:
                return []
            return [ty]
        if self.tracking_mode == 'single':
            ty = self.tracking_typhoon
            if ty is not None and ty.act and not ty.sf and ty.pts:
                self._tracking_seen.add(ty)
                return [ty]
            return []
        out = []
        for ty in self.tys:
            if ty.act and not ty.sf and ty.pts:
                self._tracking_seen.add(ty)
                out.append(ty)
        return out

    # ── 收尾时序状态机 ────────────────────────────────────────────────────
    # ── 收尾时序状态机 ────────────────────────────────────────────────────
    def _track_fade_ms(self) -> int:
        """淡出时长(ms) —— 与 `_draw_path_mixin` 同一口径(跟随 `cfg.fade_path_mode`)。"""
        mode = str(getattr(getattr(self, 'cfg', None), 'fade_path_mode', 'fade'))
        dur = FADE_DURATION_QUICK if mode == 'quick' else FADE_DURATION
        return int(dur * 1000)

    def _track_finale_span(self, tys: list):
        """收尾目标(整条路径 bbox + 边距)的经纬范围; 无点时 None。**不是结束点。**"""
        lons, lats = [], []
        for ty in tys:
            for p in (ty.pts or ()):
                lons.append(p['lo'])
                lats.append(p['la'])
        if not lons:
            return None
        clon = _circular_lon_center(lons)
        span = max(_lon_span_wrap(lons), 1e-3)
        lat0, lat1 = min(lats), max(lats)
        if lat1 - lat0 < 1e-3:
            lat0 = max(-90.0, lat0 - 1e-3)
            lat1 = min(90.0, lat0 + 1e-3)
        half = span / 2.0
        pad_lon = max(span * self._tcfg("track_view_pad_pct") / 100.0, 1e-3)
        pad_lat = max((lat1 - lat0) * self._tcfg("track_view_pad_pct") / 100.0, 1e-3)
        return ((clon - half - pad_lon) % 360.0, clon + half + pad_lon,
                max(-90.0, lat0 - pad_lat), min(90.0, lat1 + pad_lat))

    def _track_path_deadline(self, ty) -> float:
        """收尾中该台风路径必须保持可见的截止时刻(ms) —— 供 `should_draw_typhoon` 用。

        没有它, 收尾的**高亮(2.4s)+淡出**会被 `finish_time + _FADE_MS` 那个可见性
        窗口提前裁掉(fade 档在 alpha≈17 处消失, quick 档更短 ⇒ 高亮没完就没了)。
        """
        if self._track_fade_from and any(t is ty for t in self._track_hl_tys):
            return float(self._track_fade_from + self._track_fade_ms())
        return 0.0

    def _track_is_highlight(self, ty) -> bool:
        """该台风是不是本轮收尾的高亮目标(风季多台风时是一组)。"""
        return any(t is ty for t in self._track_hl_tys)

    def _track_highlight_level(self, ct: int = None) -> float:
        """高亮强度 0..1: 0.2s 渐入 → 2.0s 保持 → 0.2s 渐出 (**单次, 不是闪烁**)。"""
        if ct is None:
            ct = pygame.time.get_ticks()
        ph, t0 = self._track_phase, self._track_phase_t0
        if ph == 'hl_in':
            return min(1.0, max(0.0, (ct - t0) / float(max(1, self._tcfg("track_hl_in_ms")))))
        if ph == 'hl_hold':
            return 1.0
        if ph == 'hl_out':
            return max(0.0, 1.0 - (ct - t0) / float(max(1, self._tcfg("track_hl_out_ms"))))
        return 0.0

    def _track_finale_path_alpha(self, ct: int = None) -> int:
        """收尾台风的路径**自身透明度**: 高亮全程满不透明, 高亮渐出完起按 cfg 淡出。"""
        if ct is None:
            ct = pygame.time.get_ticks()
        if not self._track_fade_from or ct < self._track_fade_from:
            return 255
        dur = max(1, self._track_fade_ms())
        return max(0, int(255 * (1.0 - (ct - self._track_fade_from) / float(dur))))

    def _track_finale_end(self, reason: str = '') -> None:
        """结束收尾, **释放相机锁**(回到 idle/following)。"""
        self._track_phase = 'idle'
        self._track_phase_t0 = 0
        self._track_finale_tys = []
        self._track_finale_md = None
        self._track_hl_tys = []
        self._track_hl_ty = None
        self._track_hl_from = 0
        self._track_fade_from = 0
        self._track_anim = None
        self._track_anim_dur = 0
        self._tracking_seen.clear()      # 下一轮收尾只框"这一轮"跟踪过的台风
        self.tracking_finale_reason = reason

    def _tracking_season_active(self) -> bool:
        """风季: 场上是否又出现了**值得重新抓镜头**的活跃台风。

        判据必须是 `act and not sf` —— 刚跑完收尾的那批台风是 `sf=True/act=False`
        (`playback_ctrl._update_season` 在结束时置位), 所以**不会**被这里当成
        "又有活跃台风" ⇒ 不可能"刚 done 就立刻抓回同一个台风"(那会变成抖动/死循环)。
        新生成的台风、或被重播/重启成活跃状态的那个台风, 才会让它返回 True。
        """
        for t in (self.tys or ()):
            if getattr(t, 'act', False) and not getattr(t, 'sf', False) and getattr(t, 'pts', None):
                return True
        return False

    def _track_switched_to_other(self) -> bool:
        """收尾期间用户是否**显式换了目标**(换台风/自动连播到下一台)。

        只看"当前台风是不是收尾集合里的", **不看 `fin`** —— `playback_ctrl` 会在
        结束 500ms 后把 `fin` 清成 False, 那**不是**用户换了目标。
        """
        finales = self._track_finale_tys
        if self.md == self.MODE_NORMAL:
            ty = self.current_typhoon()
            if ty is None:
                return False
            return (not ty.fin) and not any(ty is f for f in finales)
        for t in self.tys:
            if (t.act and not t.sf and t.pts
                    and not any(t is f for f in finales)):
                return True
        return False

    def _track_anim_step(self, ct: int) -> None:
        """①②平滑推镜: 位置与 scale 都按 smoothstep 插值到"整条路径"的目标视野。

        与 `set_view_region` 的瞬时大跳不同, 这里逐帧插值 ⇒ 没有单帧大位移。
        scale 用**几何插值**(等比), 视觉上缩放速度才均匀。
        """
        a = self._track_anim
        if not a:
            return
        mv = self.map_mgr.map_view
        if mv is None:
            self._track_anim = None
            return
        u = min(1.0, max(0.0, (ct - a['x0']) / float(max(1, int(a.get('dur') or self._tcfg('track_pan_min_ms'))))))
        e = u * u * (3.0 - 2.0 * u)                    # smoothstep 缓入缓出
        dx = (a['tx'] - a['sx'] + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        mv.view_x = (a['sx'] + dx * e) % mv.img_w
        mv.view_y = a['sy'] + (a['ty'] - a['sy']) * e
        mv._clamp_view_y()
        ss, ts = float(a['ss']), float(a['ts'])
        mv.scale = ss * (ts / ss) ** e if (ss > 0.0 and ts > 0.0) else ss + (ts - ss) * e
        mv._bottom_align = False
        self._view_dirty = True
        self.invalidate_screen_points_lazy()
        if u >= 1.0:
            self._track_anim = None

    def _track_start_finale(self, ct: int, tys: list) -> None:
        """进入收尾: ①平滑推镜到整条路径 ②记下静置/高亮的起点与目标。"""
        mv = self.map_mgr.map_view
        if mv is None:
            return
        rng = self._track_finale_span(tys)
        if rng is None:
            return
        lo0, lo1, la0, la1 = rng
        # 目标视野: 优先让 MapView 算(与 set_view_region 同一套公式, 只算不改);
        # 极简视图桩(测试用 FakeMapView)没有这个接口 ⇒ 退回原来的瞬时 set_view_region
        tgt = None
        fn = getattr(mv, 'target_view_for_region', None)
        if fn is not None:
            try:
                tgt = fn(lo0, lo1, la0, la1)
            except Exception:
                tgt = None
        if tgt is None:
            if hasattr(mv, 'set_view_region'):
                mv.set_view_region(lo0, lo1, la0, la1)
            self._view_dirty = True
            self.invalidate_screen_points_lazy()
            self._track_anim = None
            self._track_anim_dur = 0
        else:
            # 距离用**缩放后的屏幕空间**度量: 起点与目标各自乘以自己的 scale
            # (只按 map px 差会漏掉'缩放主导'的移动, 实测差 10 倍)
            _dpx = math.hypot(tgt[1] * tgt[0] - mv.view_x * mv.scale,
                             tgt[2] * tgt[0] - mv.view_y * mv.scale)
            _mn = int(self._tcfg("track_pan_min_ms"))
            _mx = int(self._tcfg("track_pan_max_ms"))
            _per = float(self._tcfg("track_pan_ms_per_1k_px"))
            anim_ms = int(min(max(_mn, _mx), max(_mn, _mn + _dpx * _per / 1000.0)))
            self._track_anim_dur = anim_ms
            self._track_anim = {'sx': mv.view_x, 'sy': mv.view_y, 'ss': mv.scale,
                                'tx': tgt[1], 'ty': tgt[2], 'ts': tgt[0], 'x0': ct,
                                'dur': anim_ms}
            self._track_anim_step(ct)
        self._tracking_overview_done = True
        self._track_phase = 'pan'
        self._track_phase_t0 = ct
        self._track_finale_md = self.md
        self._track_finale_tys = list(tys)
        # ③ 高亮的目标 = **全部**收尾台风(风季多台风一起高亮)
        self._track_hl_tys = list(tys)
        self._track_hl_ty = tys[0] if len(tys) == 1 else None
        self._track_hl_from = ct
        # 淡出从"高亮渐出完"那一刻起算
        self._track_fade_from = (ct + int(getattr(self, '_track_anim_dur', self._tcfg('track_pan_min_ms')))
                                 + int(self._tcfg('track_settle_ms'))
                                 + int(self._tcfg('track_hl_in_ms'))
                                 + int(self._tcfg('track_hl_hold_ms'))
                                 + int(self._tcfg('track_hl_out_ms')))
        # ② 推镜期间不该有摇镜状态残留(免得收尾后摇镜把相机拖回去)
        self._track_glide = False
        self._track_tgt_prev = None
        # ① 停下: 只在正常模式且正在播放时暂停(风季多台风并行, 不该被单个结束打断)
        if self.md == self.MODE_NORMAL and getattr(self, 'pl', False):
            self._set_playing(False)

    def _track_advance_phase(self, ct: int) -> None:
        """推进收尾状态机(超时保护: 任何阶段超过 _TRACK_FINALE_MAX_MS 强制 done)。"""
        ph = self._track_phase
        if ph not in _TRACK_FINALE_PHASES:
            return
        t0 = self._track_phase_t0
        if ct - t0 > _TRACK_FINALE_MAX_MS:
            self._track_phase = 'done'
            self._track_hl_tys = []      # 兜底: 不再要求保持可见
            self.tracking_finale_reason = 'timeout'
            return
        if ph == 'pan':
            if ct - t0 >= int(getattr(self, '_track_anim_dur', self._tcfg('track_pan_min_ms'))):
                self._track_anim = None
                self._track_phase = 'settle'
                self._track_phase_t0 = ct
        elif ph == 'settle':
            if ct - t0 >= int(self._tcfg('track_settle_ms')):
                self._track_phase = 'hl_in'
                self._track_phase_t0 = ct
        elif ph == 'hl_in':
            if ct - t0 >= int(self._tcfg('track_hl_in_ms')):
                self._track_phase = 'hl_hold'
                self._track_phase_t0 = ct
        elif ph == 'hl_hold':
            if ct - t0 >= int(self._tcfg('track_hl_hold_ms')):
                self._track_phase = 'hl_out'
                self._track_phase_t0 = ct
        elif ph == 'hl_out':
            if ct - t0 >= int(self._tcfg('track_hl_out_ms')):
                self._track_phase = 'fade'
                self._track_phase_t0 = ct
        elif ph == 'fade':
            if ct >= self._track_fade_from + self._track_fade_ms():
                self._track_phase = 'done'
                self._track_hl_tys = []  # 淡完了: 可见性交回 finish_time 规则
                # 这一轮收尾结束 ⇒ 清空"本轮跟踪集合", 否则下一轮总览会把
                # **历史上跟踪过的所有台风**一起框进来(区域离谱地大、路径变成小点)
                self._tracking_seen.clear()

    def _update_tracking(self, ct: int, dt: float = 0.0) -> None:
        """镜头跟踪 + 收尾时序状态机(状态/转移见文件顶部说明)。

        收尾一旦进入就**锁存**: `playback_ctrl` 会在结束 500ms 后把 `ty.fin` 清成
        False, 旧实现下一帧就以为"又有活跃台风" ⇒ 总览被取消、相机再平移走
        (实测路径 bbox 漂移 657/387px)。
        `done` 之后**释放相机锁**: 即使仍在跟踪、场上还有活跃台风, 也不再驱动相机,
        直到用户显式操作(换台风 / 恢复播放 / 换模式 / 重选跟踪)。
        """
        if self.md not in (self.MODE_SEASON, self.MODE_NORMAL) or self.tracking_mode == 'off':
            return
        mv = self.map_mgr.map_view
        if mv is None:
            return

        # ── 收尾锁定 / 相机锁 ──
        if self._track_phase in _TRACK_FINALE_PHASES:
            if (self._track_finale_md is not None
                    and self.md != self._track_finale_md):
                self._track_finale_end('mode changed')
            elif self._track_phase == 'done':
                # ④ 收尾已跑完 ⇒ 相机锁**已释放**。两种模式区别(用户 2026-10-01 两条要求):
                #   正常模式: **不自动抓回** —— 只有用户显式恢复播放 / 换台风才重新跟。
                #   风季模式: **新台风生成(或重启)就自动重新抓镜头**。
                # ⚠ 为什么不会"刚 done 就立刻抓回同一个台风": 判据 `_track_season_next_active`
                #   要求 `t.act and not t.sf`; 而刚跑完收尾的台风正是 `sf=True/act=False`
                #   (playback_ctrl._update_season 收尾时置的) ⇒ 它们不算"活跃", 不会触发重抓;
                #   只有当场上出现**新的**活跃台风(新生成, 或被重播/重启成 act 的那个)才重抓。
                if self.md == self.MODE_NORMAL and getattr(self, 'pl', False):
                    self._track_finale_end('playback resumed')
                elif self.md == self.MODE_NORMAL and self._track_switched_to_other():
                    self._track_finale_end('target switched')
                elif (self.md != self.MODE_NORMAL and self._tracking_season_active()):
                    self._track_finale_end('season next active storm')
                else:
                    return
            else:
                if self.md == self.MODE_NORMAL and getattr(self, 'pl', False):
                    self._track_finale_end('playback resumed')
                elif self._track_switched_to_other():
                    self._track_finale_end('target switched')
                else:
                    self._track_anim_step(ct)
                    self._track_advance_phase(ct)
                    return

        active = self._track_active_typhoons()
        if active:
            self._track_phase = 'following'
            self._tracking_overview_done = False
            lons, lats = [], []
            for ty in active:
                pos = ty.cpos()
                if pos:
                    lons.append(pos['lo'])
                    lats.append(pos['la'])
            if lons:
                clon = _circular_lon_center(lons)
                clat = sum(lats) / len(lats)
                self._track_pan_to(mv, clon, clat, ct, dt)
            return

        # 全部被跟踪台风已完成 → 收尾(仅触发一次; 收尾结束前不会重入)
        if self._tracking_overview_done:
            return
        if self.md == self.MODE_NORMAL:
            # 正常模式: 收尾"当前被跟踪台风"(刚播完的那个)
            tys = [self._tracked_ty] if self._tracked_ty is not None else []
        else:
            tys = list(self._tracking_seen)
        tys = [t for t in tys if t is not None and getattr(t, 'pts', None)]
        if not tys:
            return
        self._track_start_finale(ct, tys)
    def _track_pan_to(self, mv, clon: float, clat: float, ct: int = 0, dt: float = 0.0) -> None:
        """向目标中心平滑平移(带经度环绕感知; 逐帧、帧率无关, 小死区)。
        必须逐帧应用: 若按固定间隔节流, 相机与台风运动失去同步, 台风会在屏幕上抖动。"""
        px = (clon - mv.lon_min) * mv._scale_x
        py = (mv.lat_max - clat) * mv._scale_y
        # ── "目标瞬移"判定(必须在死区早退之前更新, 否则跳过的帧会累积成假瞬移) ──
        _prev = getattr(self, '_track_tgt_prev', None)
        self._track_tgt_prev = (px, py)
        if _prev is None:
            _jump = False
        else:
            _tdx = (px - _prev[0] + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
            _tdy = py - _prev[1]
            _jump = math.hypot(_tdx, _tdy) * mv.scale > float(self._tcfg('track_jump_px'))
        tx = (px - mv.screen_width / (2.0 * mv.scale)) % mv.img_w
        ty_target = py - mv.screen_height / (2.0 * mv.scale)
        # 环绕感知的最短方向增量
        dx = (tx - mv.view_x + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        dy = ty_target - mv.view_y
        # 死区: 台风静止且镜头已对准时不产生任何视图改动(避免无谓的地图重绘/重投影)
        if abs(dx) < _TRACK_PAN_DEADZONE and abs(dy) < _TRACK_PAN_DEADZONE:
            return
        # 帧率无关平滑: 以 60fps 的 _TRACK_SMOOTH_K 为基准按 dt 折算时间常数
        if dt > 0:
            k = 1.0 - (1.0 - _TRACK_SMOOTH_K) ** (dt * 60.0)
        else:
            k = _TRACK_SMOOTH_K
        step_x, step_y = dx * k, dy * k
        # ── 有界摇镜(治"时不时镜头跳变") ────────────────────────────────────
        # 实测(`G:/tmp/probe_camera_jump.py`, 2000 帧/场景, 真相机几何 30px/deg、
        # zoom=0.9067): 稳态跟随每帧 p50 = 0.05~0.38 **屏幕像素**、p99 <= 0.96,
        # **零次** >2px; 但**跟踪目标一换**(自动连播 `cfg.ac` 默认开 / 点 [ ] 上一台
        # 下一台)第一帧就是 **24.1px**, 之后 22.7/20.5/19.6/17.3…共 21 帧 >5px ——
        # 这就是用户说的"画面整幅猛地一跳"。
        # 规则: 只有**单帧目标位移 > _TRACK_JUMP_PX**(瞬移=换台风)才进入有界摇镜,
        # 每帧最多走 `_TRACK_PAN_MAX_STEP_PX` 屏幕像素, 剩余缺口小到
        # `_TRACK_GLIDE_EXIT_PX` 时交回指数平滑。⇒ 换台风变成一次 0.8s 的快速摇镜;
        # 正常跟随(含深放大+高倍速下的快速台风)**不进入该模式**, 手感与性能不变。
        if _jump:
            self._track_glide = True
        if getattr(self, '_track_glide', False):
            _gap_px = math.hypot(dx, dy) * mv.scale
            if _gap_px <= float(self._tcfg('track_glide_exit_px')):
                self._track_glide = False
            else:
                _cap = float(self._tcfg('track_pan_max_step_px')) / max(1e-6, mv.scale)   # 屏幕px -> 地图px
                _mag = math.hypot(step_x, step_y)
                if _mag > _cap:
                    _f = _cap / _mag
                    step_x *= _f
                    step_y *= _f
        old_vx, old_vy = mv.view_x, mv.view_y
        mv.view_x = (mv.view_x + step_x) % mv.img_w
        mv.view_y += step_y
        mv._clamp_view_y()
        # ── 相位锁定: 让**目标点的屏幕坐标落在整数像素**(消除"图标略微抖动") ──
        # 抖动根因(实测, 见 G:/tmp/probe_track_jitter.py): `geo_to_screen`
        # (`map_mgr.py:109-115`) 是**两段式取整** `int(px*scale) - int(view*scale)`。
        # 相机每帧连续前推时, 台风自己的 `int(px*scale)` 与相机的 `int(view*scale)`
        # 在**不同帧**分别跨过整数边界 ⇒ 两者之差(即屏幕坐标)在真值完全不动的情况下
        # 于 m / m±1 之间来回跳 —— 就是用户看到的"镜头跟踪时图标略微抖动"。
        #   · 定速同速跟随: 无取整的浮点屏幕坐标 std=0.000000, 渲染出的整数坐标
        #     240 帧里范围 [-1,+1]、方向翻转 **27** 次(见探针 A 组)。
        #   · 纯平移的路径点(`_try_shift_screen_points`, 按 int(view*scale) 整页位移)
        #     翻转 **0** 次 —— 所以抖动只在**连续运动的目标图标**上, 不是路径/锚点问题。
        # 修法: 只微调相机偏移的**亚像素相位**(调整量 < 0.5 屏幕像素), 使
        # `px*scale - view_x*scale` 成为整数 m。于是
        #   int(px*scale) - int(view_x*scale) = int(px*scale) - (int(px*scale) - m) = m
        # 恒为常数 ⇒ 目标图标在屏幕上像素级静止, 且它跨像素的时刻与相机跨像素的时刻
        # **完全一致** ⇒ 不再有两次独立取整。相机的连续轨迹只被亚像素修正
        # (相机位置 dx 只变 < 0.1 map px), 平滑跟随手感不变; 也不新增任何视图重建
        # (本函数末尾的脏标记/惰性重投影判定逻辑一个字没改)。
        _s = mv.scale
        _res_x = (px * _s - mv.view_x * _s) - round(px * _s - mv.view_x * _s)
        mv.view_x = (mv.view_x + _res_x / _s) % mv.img_w
        _res_y = (py * _s - mv.view_y * _s) - round(py * _s - mv.view_y * _s)
        mv.view_y += _res_y / _s
        mv._clamp_view_y()
        # 上下边界钳制后实际位移可能为 0(目标在屏幕外): 此时若仍标记视图脏,
        # 镜头贴边后会每帧无谓重投影。只在"实际一点都没动"时回退本帧,
        # 正常接近目标仍走上面的死区判定, 不会提前停住
        adx = (mv.view_x - old_vx + mv.img_w / 2.0) % mv.img_w - mv.img_w / 2.0
        ady = mv.view_y - old_vy
        if abs(adx) < _TRACK_PAN_MIN_STEP and abs(ady) < _TRACK_PAN_MIN_STEP:
            mv.view_x, mv.view_y = old_vx, old_vy
            return
        self._view_dirty = True
        # 视图变化 → 屏幕坐标过期,由绘制惰性重投影
        self.invalidate_screen_points_lazy()
