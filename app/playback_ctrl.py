# py/playback_ctrl.py
"""播放控制器：台风更新、淡出、登陆检测。"""
from __future__ import annotations

import pygame
from typing import List, Optional, TYPE_CHECKING

from .typhoon import Typhoon
from .landfall_effect import LandfallEffect, LandfallEffectSMCY, LandedEffect
from .particle_effect import RIEffect, TSNoteEffect, play_eri_sound, play_note_ts_sound
from .ace_engine import _ace_eligible
from .utils import get_tropical_points, play_sound
from .constants import FADE_DURATION, FADE_DURATION_QUICK, ICON_SET_SMCY, MODE_NORMAL, MODE_SEASON, MODE_EDIT

if TYPE_CHECKING:
    from .data_repo import DataRepository
    from .view_state import ViewState
    from .config import AppConfig
    from .ace_engine import ACEEngine
    from .resource_manager import ResourceManager, MapManager
    from .season_ctrl import SeasonController


RI_WIND_INCREASE_KT = 60
RI_OFFICIAL_INTERVALS = 4
FADE_COOLDOWN_MS = 500


class PlaybackController:
    """管理台风更新循环：移动、旋转、淡出、登陆检测。"""

    def __init__(self, cfg: AppConfig, repo: DataRepository,
                 view: ViewState, ace_engine: ACEEngine,
                 res_mgr: ResourceManager, map_mgr: MapManager) -> None:
        self.cfg = cfg
        self.repo = repo
        self.view = view
        self.ace_engine = ace_engine
        self.res_mgr = res_mgr
        self.map_mgr = map_mgr
        self.effects: List[LandfallEffect] = []
        self.landfall_records: list = []
        self._pl: bool = False
        self.ace_note: Optional[dict] = None
        self._was_fin: dict = {}
        self._lf_last: dict = {}

    def latlon_to_screen(self, la: float, lo: float) -> tuple:
        return self.view.latlon_to_screen(la, lo)

    def update_all(self, ct: float, dt: float, dialog_open: bool,
                    season_ctrl: Optional[SeasonController] = None) -> None:
        paused = not self._pl or dialog_open
        md = self.cfg.md
        current = self.repo.current_typhoon() if md == MODE_NORMAL else None
        edit_ty = self.repo.edit_typhoon if md == MODE_EDIT else None
        fade_ms = FADE_DURATION * 1000.0
        for typhoon in self.repo.tys:
            # ── 静止台风快速跳过：既不在运动，也不在淡出窗口 ──
            if md == MODE_NORMAL:
                moving = typhoon is current
            elif md == MODE_SEASON:
                moving = typhoon.act and typhoon.ss and not typhoon.sf
            else:
                moving = typhoon is edit_ty
            if not moving:
                ft = typhoon.finish_time
                if ft <= 0 or ct - ft >= fade_ms:
                    continue
            if typhoon.act:
                typhoon.us(dt)
            self._fade_one(typhoon, ct)
            self._check_landfall(typhoon, ct, season_ctrl)
            self._check_finish_note(typhoon, ct)
            if not typhoon.act:
                continue
            if md == MODE_NORMAL:
                if typhoon == current:
                    self._update_normal(typhoon, ct, paused)
            elif md == MODE_SEASON:
                if typhoon.ss and not typhoon.sf:
                    self._update_season(typhoon, ct, paused, season_ctrl)
            elif md == MODE_EDIT:
                if self.repo.edit_typhoon == typhoon:
                    self._update_edit(typhoon, ct, paused)
        self.effects = [e for e in self.effects if e.update(ct)]
        if md == MODE_SEASON and not paused and season_ctrl:
            if self.cfg.ace_interpolated:
                season_ctrl.csa = self._compute_interpolated_csa(season_ctrl)
            else:
                season_ctrl.csa = season_ctrl.csa_base

    def _fade_one(self, typhoon: Typhoon, ct: float) -> None:
        if typhoon.finish_time <= 0:
            return
        elapsed = (ct - typhoon.finish_time) * 0.001
        v = typhoon.v
        ratio = 1.0 - elapsed / FADE_DURATION
        if self.cfg.fade_typhoon:
            v.icon_alpha = max(0, int(255 * ratio))
        else:
            v.icon_alpha = 255
        mode = getattr(self.cfg, 'fade_path_mode', 'fade')
        if mode == 'never':
            v.path_alpha = 255
        elif mode == 'quick':
            v.path_alpha = max(0, int(255 * (1.0 - elapsed / FADE_DURATION_QUICK)))
        else:
            v.path_alpha = max(0, int(255 * ratio))

    def _update_normal(self, typhoon: Typhoon, ct: float, paused: bool) -> None:
        pts = typhoon.pts
        n_pts = len(pts)
        if n_pts == 1:
            # R3-1: 仅首次标记完成,fin 清除后不重标记,
            # 否则 500ms 后 finish_time 被重置形成"淡出永不完成"循环
            if not typhoon.fin and not getattr(typhoon, '_single_fin_done', False):
                typhoon._mark_finished(ct)
            # R2-2: 单点台风完成标记后,同样走自动推进(ac)/淡出流程
            if typhoon.fin and ct - typhoon.ft >= FADE_COOLDOWN_MS:
                typhoon.fin = False
                typhoon._single_fin_done = True
                if self.cfg.ac and self.repo.tys:
                    self.repo.cti = (self.repo.cti + 1) % len(self.repo.tys)
                    if self.repo.current_typhoon():
                        self.repo.current_typhoon().rst()
        elif typhoon.fin:
            if ct - typhoon.ft >= FADE_COOLDOWN_MS:
                typhoon.fin = False
                if self.cfg.ac and self.repo.tys:
                    self.repo.cti = (self.repo.cti + 1) % len(self.repo.tys)
                    if self.repo.current_typhoon():
                        self.repo.current_typhoon().rst()
        else:
            prev_ci = typhoon.ci
            if not typhoon.v.ipos and typhoon.ci < n_pts - 1:
                typhoon.sm(ct)
            typhoon.um(ct, self.cfg.sp, paused)
            if typhoon.ci != prev_ci:
                self._check_particle_effects(typhoon, prev_ci, ct)
        typhoon.cace = (typhoon.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[typhoon.ci]['ace'] if pts and typhoon.ci < n_pts else 0.0))

    def _update_season(self, typhoon: Typhoon, ct: float, paused: bool,
                       season_ctrl: Optional[SeasonController] = None) -> None:
        pts = typhoon.pts
        n_pts = len(pts)
        if not pts:
            typhoon.cace = 0.0
            return
        if season_ctrl and typhoon.ci == 0 and typhoon.last_ace_ci == -1:
            pt = pts[0]
            if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == season_ctrl.current_ace_year:
                if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                    season_ctrl.add_csa(pt['pace'])
            typhoon.last_ace_ci = 0
        if n_pts == 1:
            typhoon._mark_finished(ct)
            typhoon.sf = True
        elif not typhoon.fin:
            prev_ci = typhoon.ci
            if not typhoon.v.ipos and typhoon.ci < n_pts - 1:
                typhoon.sm(ct)
            typhoon.um(ct, self.cfg.sp, paused)
            if typhoon.ci != prev_ci:
                self._check_particle_effects(typhoon, prev_ci, ct)
            if not paused and season_ctrl and typhoon.ci > typhoon.last_ace_ci:
                for i in range(typhoon.last_ace_ci + 1, typhoon.ci + 1):
                    pt = pts[i]
                    if pt.get('pace', 0) > 0 and pt.get('ace_year', 0) == season_ctrl.current_ace_year:
                        if self.ace_engine.point_in_limit(pt['la'], pt['lo']):
                            season_ctrl.add_csa(pt['pace'])
            typhoon.last_ace_ci = typhoon.ci
            if typhoon.fin:
                typhoon.sf = True
                typhoon.act = False
                if self.cfg.show_summary:
                    from .summary_effect import TyphoonSummary
                    if TyphoonSummary.available_for(typhoon):
                        self.effects.append(TyphoonSummary(typhoon, ct))

        typhoon.cace = (typhoon.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[typhoon.ci]['ace']
                         if pts and 0 <= typhoon.ci < len(pts) else 0.0))

    def _compute_interpolated_csa(self,
                                    season_ctrl: SeasonController) -> float:
        """csa_base(已确认部分) + 各活跃台风向下一报点的插值部分。
        法20: 每台风缓存部分 pace·t,仅 ci 前进或 t 跨 1/256 桶时重算。"""
        cur_year = season_ctrl.current_ace_year
        total = season_ctrl.csa_base
        lm = self.ace_engine.limit_mode
        lb = self.ace_engine.limit_basin
        for typhoon in self.repo.tys:
            if (not typhoon.act or not typhoon.ss or typhoon.sf or typhoon.fin
                    or typhoon.ci < 0 or typhoon.ci >= len(typhoon.pts) - 1):
                typhoon._ipol_key = None
                continue
            pts = typhoon.pts
            # points_time 与 pts 长度不一致时(尚未重算/点被就地修改)跳过插值部分,
            # 只保留已确认的 csa_base,避免 points_time[ci+1] 越界崩溃。
            if len(typhoon.points_time) != len(pts):
                typhoon._ipol_key = None
                continue
            pt_next = pts[typhoon.ci + 1]
            if pt_next.get('pace', 0) <= 0 or pt_next.get('ace_year', 0) != cur_year:
                typhoon._ipol_key = None
                continue
            if not self.ace_engine.point_in_limit(pt_next['la'], pt_next['lo']):
                typhoon._ipol_key = None
                continue
            pt0 = typhoon.points_time[typhoon.ci]
            pt1 = typhoon.points_time[typhoon.ci + 1]
            if pt1 <= pt0:
                typhoon._ipol_key = None
                continue
            t = max(0.0, min(1.0, (typhoon.at - pt0) / (pt1 - pt0)))
            bucket = int(t * 256)
            key = (typhoon.ci, bucket, lm, lb)
            if getattr(typhoon, '_ipol_key', None) == key:
                total += typhoon._ipol_part
            else:
                part = pt_next['pace'] * t
                typhoon._ipol_part = part
                typhoon._ipol_key = key
                total += part
        return total

    def _update_edit(self, typhoon: Typhoon, ct: float, paused: bool) -> None:
        pts = typhoon.pts
        n_pts = len(pts)
        if n_pts == 1:
            # R3-1: 仅首次标记完成,fin 清除后不重标记,避免 500ms 循环
            if not typhoon.fin and not getattr(typhoon, '_single_fin_done', False):
                typhoon._mark_finished(ct)
            # R2-2: 单点台风同样走停止播放+复位流程
            if typhoon.fin and ct - typhoon.ft >= FADE_COOLDOWN_MS:
                typhoon.fin = False
                self._pl = False
                typhoon.rst()
                typhoon._single_fin_done = True
                if self.repo._sim:
                    sim = self.repo._sim
                    sim._edit_selected_point = None
                    # 同步播放按钮文案(与 _set_playing 一致)
                    try:
                        from .constants import f_s, rt
                        sim.play_text = rt(f_s, "播放", (255, 255, 255))
                    except Exception:
                        pass
        elif typhoon.fin:
            if ct - typhoon.ft >= FADE_COOLDOWN_MS:
                typhoon.fin = False
                self._pl = False
                typhoon.rst()
                if self.repo._sim:
                    sim = self.repo._sim
                    sim._edit_selected_point = None
                    # 同步播放按钮文案(与 _set_playing 一致)
                    try:
                        from .constants import f_s, rt
                        sim.play_text = rt(f_s, "播放", (255, 255, 255))
                    except Exception:
                        pass
        else:
            if not typhoon.v.ipos and typhoon.ci < n_pts - 1:
                typhoon.sm(ct)
            typhoon.um(ct, self.cfg.sp, paused)
        typhoon.cace = (typhoon.interpolated_cace() if self.cfg.ace_interpolated
                   else (pts[typhoon.ci]['ace'] if pts and typhoon.ci < n_pts else 0.0))

    def _check_landfall(self, typhoon: Typhoon, ct: float,
                        season_ctrl: Optional[SeasonController] = None) -> None:
        if not typhoon.act:
            return
        mm = self.map_mgr
        if mm._load_land_orig() is None:
            return
        ace_limit_mode = self.cfg.ace_limit_mode
        if ace_limit_mode == "basin" and self.cfg.ace_limit_basin:
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None and not self.repo._ty_in_filter_basin(typhoon, area):
                pos = typhoon.cpos()
                if pos:
                    typhoon.v.last_on_land = mm.is_land_at_geo(pos['la'], pos['lo'])
                return
        pos = typhoon.cpos()
        if not pos:
            return
        # 登陆判定完全基于地理坐标（视图无关）：拖动/缩放地图不影响结果；
        # 地理位置未变化时跳过采样（暂停/低速时避免每帧检测）
        lkey = (round(pos['la'] * 1000), round(pos['lo'] * 1000))
        if self._lf_last.get(typhoon) == lkey:
            return
        self._lf_last[typhoon] = lkey
        is_land = mm.is_land_at_geo(pos['la'], pos['lo'])
        v = typhoon.v
        if is_land and not v.last_on_land:
            current_point = typhoon.cp()
            if current_point:
                adv_on_land = mm.is_land_at_geo(current_point['la'], current_point['lo'])
                if adv_on_land and typhoon.ci > 0:
                    prev_pt = typhoon.pts[typhoon.ci - 1]
                    landfall_wind = prev_pt.get('w', current_point.get('w', 0))
                    landfall_st = prev_pt.get('st', current_point.get('st', ''))
                    landfall_pres = prev_pt.get('p', 0) or current_point.get('p', 0)
                else:
                    landfall_wind = current_point.get('w', 0)
                    landfall_st = current_point.get('st', '')
                    landfall_pres = current_point.get('p', 0)
                strength = self.repo.get_strength_category(landfall_wind, landfall_st)
                # 登陆记录年份优先取报点自身 ace_year,否则回退季节当前年
                cur_pt = typhoon.cp()
                ace_year = (cur_pt.get('ace_year', 0) if cur_pt else 0) \
                    or (season_ctrl.current_ace_year if season_ctrl else 2000)
                self.landfall_records.append({
                    'name': self.repo.get_display_name(typhoon),
                    'wind': landfall_wind,
                    'year': ace_year,
                    'basin': typhoon.basin,
                    'la': pos['la'],
                    'lo': pos['lo'],
                })
                # 特效与音效仅在登陆点位于屏幕内时播放（记录始终保留）
                sx, sy = self.view.latlon_to_screen(pos['la'], pos['lo'])
                on_screen = (0 <= sx < self.view.screen_width
                             and 0 <= sy < self.view.map_height)
                if typhoon.ci != 0 and on_screen:
                    lf_color = self.repo.get_point_color(landfall_wind, landfall_st)
                    lf_label = f"{landfall_wind}kt"
                    if landfall_pres:
                        lf_label += f" {landfall_pres}mb"
                    lf_scale = self.cfg.peak_label_size / 100.0
                    if self.cfg.icon_set == ICON_SET_SMCY:
                        from .smcy_icon import get_landfall_frames
                        icon_factor = self.cfg.icon_size / 100.0
                        lf_size = max(20, 4 * round(70 * icon_factor * 1.5 / 4))
                        frames = get_landfall_frames(strength, lf_size, lf_size)
                        if frames:
                            self.effects.append(LandfallEffectSMCY(
                                strength, pos['lo'], pos['la'], frames, ct,
                                self.view.latlon_to_screen,
                                label=lf_label, label_color=lf_color,
                                label_scale=lf_scale))
                    else:
                        img1, img2 = self.res_mgr.get_landfall_images(strength)
                        if img1 and img2:
                            self.effects.append(LandfallEffect(
                                strength, pos['lo'], pos['la'], img1, img2, ct,
                                self.view.latlon_to_screen,
                                label=lf_label, label_color=lf_color,
                                label_scale=lf_scale))
                    sound = self.res_mgr.get_sound(strength)
                    if sound:
                        play_sound(sound, self.cfg.volume)

                    # ── Landed 落地标记动画 ──
                    prf = self.cfg.point_size / 100.0
                    marker_size = max(6, int(13 * prf))
                    landed = LandedEffect(strength, pos['lo'], pos['la'], ct,
                                          self.view.latlon_to_screen,
                                          int(marker_size * 96 / 40))
                    if landed._count > 0:
                        self.effects.append(landed)
        v.last_on_land = is_land

    def _check_finish_note(self, typhoon: Typhoon, ct: float) -> None:
        """台风结束时记录 ACE 提示：进度条左侧显示 '台风名 +ACE'。"""
        fin_now = typhoon.fin or typhoon.sf
        if fin_now and not self._was_fin.get(typhoon, False):
            name = typhoon.sname or typhoon.cust or ''
            tropical = get_tropical_points(typhoon.pts)
            if tropical:
                mwp = max(tropical, key=lambda p: p['w'])
                color = tuple(mwp.get('color', (200, 200, 200)))
            else:
                color = (200, 200, 200)
            self.ace_note = {'name': name, 'ace': typhoon.tace,
                             'color': color, 'time': ct}
        self._was_fin[typhoon] = fin_now

    def _check_particle_effects(self, typhoon: Typhoon, prev_ci: int, ct: float) -> None:
        self._check_ts_note(typhoon, prev_ci, ct)
        self._check_ri_effect(typhoon, prev_ci, ct)

    def _icon_factor(self) -> float:
        sim = getattr(self.repo, '_sim', None)
        if sim is not None and hasattr(sim, '_size_factors'):
            return sim._size_factors()[1]
        f = self.cfg.icon_size / 100.0
        mv = self.map_mgr.map_view
        if self.cfg.fix_icon_point_size and mv and mv.min_scale > 0:
            f *= mv.scale / (mv.min_scale * 2.5)
        return f

    def _check_ts_note(self, typhoon: Typhoon, prev_ci: int, ct: float) -> None:
        """检测从不可计算 ACE 的类型加强为 TS，在台风位置播放 note_ts 特效。"""
        pts = typhoon.pts
        new_ci = typhoon.ci
        if new_ci <= prev_ci or len(pts) < 2:
            return
        for i in range(max(prev_ci + 1, 1), min(new_ci, len(pts) - 1) + 1):
            if pts[i]['st'].upper() == 'TS' and not _ace_eligible(pts[i - 1]):
                self.effects.append(TSNoteEffect(
                    typhoon, ct, self.view.latlon_to_screen,
                    self._icon_factor,
                    smcy=self.cfg.icon_set == ICON_SET_SMCY))
                play_note_ts_sound(self.cfg.volume)
                break

    def _check_ri_effect(self, typhoon: Typhoon, prev_ci: int, ct: float) -> None:
        """检测 RI 事件：越过的官方报相对之前 ≤4 个官方报增幅 ≥60kt 时触发。
        一个巅峰只触发一次：触发后需等风速回落（越过巅峰）才重新武装。"""
        if not self.cfg.show_ri_effect:
            return
        pts = typhoon.pts
        new_ci = typhoon.ci
        if new_ci <= prev_ci or len(pts) < 2:
            return
        v = typhoon.v
        # 微优化1: 官方报索引按台风级缓存(点变更时经 refresh_typhoon_after_point_change 清空)
        officials = getattr(typhoon, '_officials', None)
        if officials is None or officials[1] != id(pts):
            officials = ([i for i, p in enumerate(pts) if p.get('official', False)], id(pts))
            typhoon._officials = officials
        officials = officials[0]
        for j in range(1, len(officials)):
            pj = officials[j]
            if pj <= prev_ci:
                continue
            if pj > new_ci:
                break
            # 风速回落 → 已越过一个巅峰，重新武装
            if pts[pj]['w'] < pts[officials[j - 1]]['w']:
                v._ri_armed = True
                continue
            if not v._ri_armed:
                continue
            for k in range(min(j, RI_OFFICIAL_INTERVALS)):
                pi = officials[j - k - 1]
                if pts[pj]['w'] - pts[pi]['w'] >= RI_WIND_INCREASE_KT:
                    icon_factor = self.cfg.icon_size / 100.0
                    self.effects.append(RIEffect(typhoon, ct, self.view.latlon_to_screen,
                                                  icon_factor))
                    play_eri_sound(self.cfg.volume)
                    v._ri_armed = False
                    break
