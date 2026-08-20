# app/ty_sim_mixins/sim_mode_mixin.py
"""模拟模式(MODE_SIM): 完全移植 SimCore 模拟器核心(环境场/TC动力学/云场/集合)。

- 核心: simulator.simcore.core(0.5° 环境场 + 10min 积分步 + 半拉格朗日云场 + 12成员集合)
- 渲染: simulator.simcore.render 各图层叠加到现有地图上(不再覆盖地图)
- 交互: 左键点击生成/选中台风, 空格播放, 数字键图层, 速度档按钮
"""
from __future__ import annotations

import math
import os
import json
from datetime import datetime
from typing import List, Optional

import numpy as np
import pygame

from app.constants import f_s, rt

# 图层: (id, 名称, 快捷键) — 已去掉云图图层
SIM_LAYERS = [
    ('pressure', '气压',      '1'),
    ('wind',     '风场',      '2'),
    ('sst',      '海温',      '3'),
    ('ohc',      '热含量',    '4'),
    ('track',    '路径',      '5'),
    ('ridge',    '副高',      '6'),
]
SIM_LAYER_IDS = [lid for lid, _, _ in SIM_LAYERS]

# 速度档(×): 与全局速度条共用 self.sp(0.1-10), 档位按钮循环
SIM_SPEEDS = [0.5, 1.0, 2.0, 4.0, 8.0]


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


class _SimPathV:
    __slots__ = ('smooth_screen_points', '_sp_ver')

    def __init__(self):
        self.smooth_screen_points = None
        self._sp_ver = 0


class _SimPathTy:
    """模拟模式台风的轻量路径对象: 完全复用其它模式的点阵/渐变线路径渲染
    (_render_path_to_surface: 半透明未来段 + 不透明已走段 + 点阵标记)。"""

    __slots__ = ('pts', 'ci', 'v', 'screen_points',
                 '_path_cache_key', '_path_cache_full', '_path_cache_traversed',
                 '_path_cache_blit', '_last_rendered_ci')

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


class SimModeMixin:
    """模拟模式逻辑。挂载到 TySim。"""

    # ── 初始化 ──
    def _init_sim_mode(self) -> None:
        self.sim_layer = 'track'           # 当前图层
        self.sim_playing = False
        self.sim_layers = {'track'}
        self.sim_cloud_mode = 'IR'
        self.sim_natural_gen = True
        self.sim_seed = 20250801
        self.sim_month = 8
        # 数据驱动模式(借鉴 KWP): True=用项目气候库真实环境场, False=解析公式
        self.sim_data_mode = False
        self.sim_data_year = None      # 具体年份(如 2020); None=用气候态
        self.sim_data_clim = True      # True=气候态, False=具体年份库
        self.sim_sel_tc_id: Optional[int] = None
        self.sim_particles = None
        self.sim_v4 = None
        self.sim_icon_tys = {}
        self.sim_path_tys = {}
        self._sim_step_acc = 0.0
        self._sim_cld_acc = 0.0
        self._sim_msg = ""
        self._sim_msg_t = 0
        # 注入文本渲染(避免 simcore.render 反向依赖 app 层)
        try:
            from simulator.simcore import render as _R
            _R.set_label_fn(lambda t, c: rt(f_s, t, c))
        except Exception:
            pass

    def _sim_sync_path_ty(self, tc) -> _SimPathTy:
        """按 tc 的 track 构建/更新轻量路径对象(仅点增长时重建)。"""
        from simulator.simcore.render import grade_kt
        ty = self.sim_path_tys.get(tc['id'])
        if ty is None:
            ty = _SimPathTy()
            self.sim_path_tys[tc['id']] = ty
        n = len(tc['track'])
        if len(ty.pts) != n:
            pts = []
            for q in tc['track']:
                g = grade_kt(q['vmax'])
                col = g[3][:3]
                dim = tuple(int(c * 0.45) for c in col)
                pts.append({'t': str(q['t']), 'la': q['lat'], 'lo': q['lon'],
                            'w': q['vmax'], 'p': q['pmin'], 'st': g[1],
                            'color': col, 'color_dim': dim, 'official': True})
            ty.pts = pts
            ty.ci = max(0, n - 1)
            ty.screen_points = [self.latlon_to_screen(q['lat'], q['lon'])
                                for q in tc['track']]
        return ty

    def _sim_set_layer(self, lid: str) -> None:
        """切换图层(单选): 只显示该图层。"""
        if lid not in SIM_LAYER_IDS:
            return
        self.sim_layer = lid
        self.sim_layers = {lid}
        self._sim_notice(f"图层: {self._sim_layer_short()}")

    # ── 重置(新建模拟) ──
    def _sim_reset(self, msg: bool = True) -> None:
        from simulator.simcore import core as V
        self.sim_v4 = V.init({
            'month': self.sim_month,
            'seed': self.sim_seed,
            'params': {'shLat': 31.0, 'shLon': 146.0, 'shAmp': 10.0,
                       'random': 0.55, 'genesis': self.sim_natural_gen,
                       'dataMode': self.sim_data_mode,
                       'dataYear': self.sim_data_year,
                       'dataClim': self.sim_data_clim,
                       'dataDayCycle': 1.0},
        })
        V.env_update(self.sim_v4)
        from simulator.simcore.render import Particles
        self.sim_particles = Particles(self._sim_particle_count())
        # 渲染设置默认值(存于 sim_v4 dict, render_map 读取)
        self.sim_v4['_fcst_style'] = getattr(self, 'sim_forecast_style', 'JTWC')
        self.sim_v4['_quad_rings'] = True
        self.sim_v4['_show_rings'] = True
        self.sim_v4['_show_labels'] = True
        self.sim_v4['_show_barbs'] = True
        self.sim_v4['_show_forecast'] = True
        self.sim_v4['_particle_count'] = 3000
        self.sim_sel_tc_id = None
        self._sim_step_acc = 0.0
        self._sim_cld_acc = 0.0
        if msg:
            self._sim_notice("已重置模拟(种子 %d, %d月)" % (self.sim_seed, self.sim_month))

    def _sim_particle_count(self) -> int:
        return getattr(self, 'sim_particle_count', 3000)

    def _sim_notice(self, text: str) -> None:
        self._sim_msg = text
        self._sim_msg_t = pygame.time.get_ticks()

    # ── 手动生成 / 选中 ──
    def _sim_spawn(self, la: float, lo: float, vmax_kt: float = 48.0) -> None:
        from simulator.simcore import core as V
        if self.sim_v4 is None:
            self._sim_reset(msg=False)
        tc = V.make_tc(self.sim_v4, lo, la, vmax_kt)
        self.sim_sel_tc_id = tc['id']
        self._sim_notice(f"已生成台风 {tc['name']} [{tc['basin']}] "
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
        try:
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
        for lid, name, _ in SIM_LAYERS:
            if lid == self.sim_layer:
                return name
        return self.sim_layer

    # ── 导出(纯 b-deck 格式, ATCF 标准) ──
    @staticmethod
    def _bdeck_lat(lat: float) -> str:
        hemi = 'N' if lat >= 0 else 'S'
        return f"{int(round(abs(lat) * 10)):03d}{hemi}"

    @staticmethod
    def _bdeck_lon(lon: float) -> str:
        lo = lon % 360.0
        if lo > 180.0:
            return f"{int(round((360.0 - lo) * 10)):03d}W"
        return f"{int(round(lo * 10)):03d}E"

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
            # 模拟时间 → 真实日期(基准 2000-01-01 + t 小时)
            from datetime import timedelta as _td
            base = datetime(2000, sim['month0'], 1)
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
            from simulator.simcore import core as V
            # 与全局速度条共用 self.sp: 1× = 每秒 6 个 10min 步 = 1 模拟小时/实时秒
            self._sim_step_acc += self.sp * dt * 6.0
            steps = int(min(40, math.floor(self._sim_step_acc)))
            self._sim_step_acc -= steps
            for _ in range(steps):
                V.sim_step(self.sim_v4, V.C['DT'])
            hpf = self.sp * dt
            self._sim_cld_acc += hpf
            if self._sim_cld_acc >= 0.08:
                V.cloud_step(self.sim_v4, min(self._sim_cld_acc, 3.5))
                self._sim_cld_acc = 0.0
            if self.sim_particles is not None:
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
            self.sim_natural_gen = not self.sim_natural_gen
            if self.sim_v4 is not None:
                self.sim_v4['params']['genesis'] = self.sim_natural_gen
            self._sim_notice("自然生成: " + ("开" if self.sim_natural_gen else "关"))
            return True
        if k == pygame.K_r:
            self._sim_reset()
            return True
        if k == pygame.K_d:
            # 切换数据驱动模式(真实气候库环境场 / 解析公式)
            self.sim_data_mode = not self.sim_data_mode
            self._sim_reset(msg=False)
            st = (self.sim_v4 or {}).get('dataState', 'off')
            label = {'loaded': '开(真实数据)', 'partial': '开(真实数据·部分降级)',
                     'fallback': '开(已降级解析)', 'off': '关(解析公式)'}.get(st, st)
            self._sim_notice("数据模式: " + label)
            return True
        if k == pygame.K_e:
            fn = self._sim_export(6)
            self._sim_notice(f"已导出: {os.path.basename(fn)}")
            return True
        return False

    def _sim_handle_click(self, mx: int, my: int) -> bool:
        """地图左键点击: 命中台风→选中; 计算域内→生成台风。"""
        if self.sim_v4 is None:
            self._sim_reset(msg=False)
        tc = self._sim_find_tc(mx, my)
        if tc is not None:
            self.sim_sel_tc_id = tc['id']
            self._sim_notice(f"跟踪: {tc['name']} {tc['vmax']:.0f}kt")
            return True
        la, lo = self._sim_scr2ll(mx, my)
        if self._sim_in_domain(la, lo):
            self._sim_spawn(la, lo)
            return True
        return False

    def _sim_sync_play_text(self) -> None:
        try:
            self.play_text = rt(f_s, '暂停' if self.sim_playing else '播放', (255, 255, 255))
            self._panel = None
        except Exception:
            pass

    # ── 渲染(叠加到地图) ──
    def _sim_render(self, surface) -> None:
        """在地图 surface 上叠加 SimCore 图层(renderer 已先绘制地图底图)。"""
        if self.sim_v4 is None:
            return
        from simulator.simcore import render as R
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

        sel = None
        if self.sim_sel_tc_id is not None:
            for tc in self.sim_v4['tcs']:
                if tc['id'] == self.sim_sel_tc_id:
                    sel = tc
                    break
        try:
            R.render_map(surface, self.sim_v4, fx, fy, self.sim_layers,
                         sel_tc=sel, mode=self.sim_cloud_mode,
                         particles=self.sim_particles,
                         icon_fn=self._sim_draw_tc_icon)
        except Exception:
            pass
        # 台风路径: 完全复用其它模式的点阵/渐变线渲染(不自行绘制)
        if 'track' in self.sim_layers:
            for tc in self.sim_v4['tcs']:
                if tc['dead'] or len(tc['track']) < 2:
                    continue
                try:
                    ty = self._sim_sync_path_ty(tc)
                    full, trav, pos = self._render_path_to_surface(
                        ty, ty.screen_points, False)
                    surface.blit(full, pos)
                    surface.blit(trav, pos)
                except Exception:
                    pass
        self._sim_draw_hud(surface)
        if self._sim_msg and pygame.time.get_ticks() - self._sim_msg_t < 2500:
            txt = rt(f_s, self._sim_msg, (255, 230, 100))
            y = getattr(self, 'map_height', surface.get_height()) - 40
            surface.blit(txt, (10, max(0, y)))

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
            pass

    # ── 左上角时间轴(圆环钟) + 季节样式信息框 ──
    def _sim_calendar(self, sim) -> tuple:
        """模拟时间 → 真实日历 (月, 日, 时)。修复"9月44日"类溢出。"""
        from datetime import datetime as _dt, timedelta
        base = _dt(2000, sim['month0'], 1)
        cur = base + timedelta(hours=sim['t'])
        return cur.month, cur.day, cur.hour

    def _sim_render_info_box(self, tc) -> pygame.Surface:
        """台风信息框(样式照季节模式 _render_info_box: 暗底圆角/左侧强度色条)。"""
        from simulator.simcore.render import grade_kt
        box_w = 350
        line_h = 17
        box_h = 8 + line_h + 4 + line_h + 4 + line_h + 8
        box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        dark = getattr(self, 'dark_mode', True)
        bg = (22, 28, 44, 180) if dark else (245, 245, 250, 235)
        border = (55, 85, 130) if dark else (120, 140, 170)
        tc_c = (215, 225, 245) if dark else (30, 40, 60)
        pygame.draw.rect(box, bg, (0, 0, box_w, box_h), 0, 8)
        v_kt = tc['vmax']
        g = grade_kt(v_kt)
        bar_c = (*g[3][:3], 220)
        pygame.draw.rect(box, bar_c, (0, 0, 8, box_h), 0, 12, 0, 0, 12)
        pygame.draw.rect(box, border, (0, 0, box_w, box_h), 2, 12)
        phase = tc.get('phase', 'TC')
        ph = '' if phase == 'TC' else (' [变性]' if phase == 'SD'
                                       else (' [温带]' if phase == 'EX' else ' [副热带]'))
        name_s = rt(f_s, f"{tc['name']}{ph}", (255, 255, 255))
        box.blit(name_s, (14, 6))
        erc = ' | 眼壁置换中 眼径%.0fkm' % (2 * tc['rmw']) if tc['erc'] else ''
        inten_s = rt(f_s, f"{v_kt:.0f}kt {g[0]} {tc['pmin']:.0f}hPa{erc}", tc_c)
        box.blit(inten_s, (14, 6 + line_h + 4))
        pos_s = rt(f_s, f"{tc['lat']:+.1f}°  {tc['lon'] % 360:.1f}°  移速 "
                        f"{math.hypot(tc['mu'], tc['mv']):.0f}kt", tc_c)
        box.blit(pos_s, (14, 6 + 2 * (line_h + 4)))
        return box

    def _sim_draw_hud(self, surface) -> None:
        sim = self.sim_v4
        month, day, hr = self._sim_calendar(sim)
        # 左上角时间轴(圆环钟, 模拟时间)
        self.draw_season_clock(surface, origin=(0, 0),
                               time_tuple=(2000, month, day, hr, 0,
                                           (sim['t'] % 24) / 24.0))
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
            pass
        # 季节样式信息框(时钟下方, 与季节模式同起点 y=245)
        y = 245
        for tc in sim['tcs']:
            if tc['dead']:
                continue
            box = self._sim_render_info_box(tc)
            surface.blit(box, (15, y))
            y += box.get_height() + 5
            if y > getattr(self, 'map_height', 900) - 120:
                break
