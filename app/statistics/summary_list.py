# py/statistics/summary_list.py
"""总结条列表：全屏白底展示本风季所有总结条 + 末页统计总结。ESC 退出。"""
from __future__ import annotations

import pygame
from datetime import datetime
from typing import List, Optional

from ..constants import rt
from ..constants.fonts import _load_font, SmartFont, FONT_FILE
from ..dialog_base import Dialog
from ..smcy_icon import get_summary_frame
from ..summary_effect import TyphoonSummary, _CAT_COLOR
from ..statistics.chart_helpers import _haversine_chain
from ..statistics.season_stats import calculate_season_stats
from ..utils import (display_category, _WIND_C2_MIN, _WIND_C4_ST_MIN,
                     get_tropical_points)
from ..ty_sim_mixins._draw_icon_mixin import _apply_purple_filter, _purple_tier

# 行内文字用较大字号渲染后按条高缩放，保证清晰
_font_small = SmartFont(_load_font(FONT_FILE, 28, 28), _load_font(FONT_FILE, 28, 28))
_font_name = SmartFont(_load_font(FONT_FILE, 36, 36), _load_font(FONT_FILE, 36, 36))
_font_info = SmartFont(_load_font(FONT_FILE, 32, 32), _load_font(FONT_FILE, 32, 32))
_font_title = SmartFont(_load_font(FONT_FILE, 64, 64), _load_font(FONT_FILE, 64, 64))

_OUTLINE = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_WHITE = (255, 255, 255)
_NON_TROPICAL = ('MD', 'SS', 'SD', 'EX', 'LO')

_outlined_cache: dict = {}

# 紫色滤镜结果缓存: (帧 id, tier) → 已滤镜帧,避免每帧 numpy 全图运算
_purple_frame_cache: dict = {}
_PURPLE_CACHE_MAX = 96

# 视频动画节流: 每行每 ~66ms 才请求新帧(~15fps),避免 3 行 × 60fps
# 的解码压力造成界面卡顿
_FRAME_INTERVAL_MS = 66


def _outlined(font, text: str, target_h: int = 0) -> pygame.Surface:
    """白字黑描边文字，可按目标高度缩放；缓存。"""
    key = (id(font), text, target_h)
    surf = _outlined_cache.get(key)
    if surf is None:
        fg = font.render(text, True, _WHITE)
        bk = font.render(text, True, (0, 0, 0))
        surf = pygame.Surface((fg.get_width() + 4, fg.get_height() + 4), pygame.SRCALPHA)
        for dx, dy in _OUTLINE:
            surf.blit(bk, (dx + 2, dy + 2))
        surf.blit(fg, (2, 2))
        if target_h > 0 and surf.get_height() != target_h:
            k = target_h / surf.get_height()
            surf = pygame.transform.smoothscale(
                surf, (max(1, int(surf.get_width() * k)), target_h))
        if len(_outlined_cache) > 256:
            _outlined_cache.pop(next(iter(_outlined_cache)))
        _outlined_cache[key] = surf
    return surf


class SummaryListDialog(Dialog):
    """全屏总结条列表（ESC 退出，无按钮）。"""

    ROWS = 10
    STAGGER_MS = 100     # 每条晚 0.1s 出场
    SLIDE_MS = 300       # 单条滑入/收回时长
    PAGE_MS = 8000       # 每页展示 8 秒
    PHASE_MS = 4000      # 前 4 秒强度/ACE，后 4 秒活跃/路径/均速
    GAP = 8
    APPEAR_DELAY_MS = 1000   # 打开后延迟 1s 开始动画

    def __init__(self, sim):
        super().__init__(sim)
        self._pages: List[list] = []
        self._page = 0
        self._title = ""
        self._state = 'in'          # in / out
        self._anim_t0 = 0
        self._page_t0 = 0
        self._pending: Optional[object] = None   # 'close' 或目标页码
        self._open_t = 0
        self._bg: Optional[pygame.Surface] = None
        # 视频帧节流缓存: key=(cat, hemi, size) → (获取时间, 帧)
        self._row_frame_cache: dict = {}

    def deactivate(self):
        self._row_frame_cache.clear()
        super().deactivate()

    # ── 数据 ──

    def activate(self):
        super().activate()
        sim = self.sim

        # 截取当前地图渲染 → 高斯模糊作为背景
        self._build_blurred_bg(sim)

        self.bg_rect = pygame.Rect(0, 0, sim.screen_width, sim.screen_height)
        year = sim.current_ace_year

        lm = getattr(sim, 'ace_limit_mode', 'none')
        bc = getattr(sim, 'ace_limit_basin', '')
        basin_code = bc if (lm == 'basin' and bc) else None
        basin_area = None
        basin_cn = "全球"
        if basin_code:
            basin_area = sim.res_mgr.ocean_areas.get_by_code(basin_code)
            if basin_area is not None:
                basin_cn = getattr(basin_area, 'name_cn', basin_code)
        self._title = f"{year}年 {basin_cn} 热带气旋总结列表"
        self._stats_title = f"{year}年 {basin_cn} 风季总结"

        storm_rows = self._build_storm_rows(year)
        stat_rows = self._build_stat_rows(year, basin_code, basin_area)

        self._pages = [storm_rows[i:i + self.ROWS]
                       for i in range(0, len(storm_rows), self.ROWS)]
        if not self._pages:
            self._pages = [[]]
        self._pages.append(stat_rows)

        self._page = 0
        self._state = 'in'
        self._pending = None
        now = pygame.time.get_ticks()
        # 延迟 1s 后首次亮相
        self._anim_t0 = now + self.APPEAR_DELAY_MS
        self._page_t0 = now
        self._open_t = now

    def _build_blurred_bg(self, sim):
        """用地图渲染做基层，两次缩放模拟高斯模糊。"""
        map_img = getattr(sim.map_mgr, '_cached_map_render', None)
        if map_img is not None:
            raw = map_img.copy()
        else:
            # 兜底：从屏幕截取地图区域
            raw = sim.screen.subsurface(
                pygame.Rect(0, 0, sim.screen_width, sim.map_height)).copy()
        # 两级平滑缩放模拟高斯模糊
        f = 8
        w, h = raw.get_width(), raw.get_height()
        sw = max(1, w // f)
        sh = max(1, h // f)
        small = pygame.transform.smoothscale(raw, (sw, sh))
        blur = pygame.transform.smoothscale(small, (w, h))
        # 第二次 —— 更强模糊
        small2 = pygame.transform.smoothscale(blur, (max(1, sw // 2), max(1, sh // 2)))
        blur2 = pygame.transform.smoothscale(small2, (w, h))
        # 暗化 + 半透明
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 110))
        blur2.blit(overlay, (0, 0))
        self._bg = blur2

    def _build_storm_rows(self, year: int) -> list:
        rows = []
        storms = []
        for ty in self.sim.tys:
            if not ty.pts:
                continue
            if not any(p.get('ace_year') == year for p in ty.pts):
                continue
            if not TyphoonSummary.available_for(ty):
                continue
            storms.append(ty)
        storms.sort(key=lambda t: t.pts[-1]['t'])   # 总结条生成顺序 = 结束时间

        for ty in storms:
            cat = TyphoonSummary._find_peak(ty)
            pool = get_tropical_points(ty.pts) or ty.pts
            # 单趟遍历: 同时取峰值风速/气压/日期,避免三次全量扫描
            max_wind = 0
            peak_pres = 0
            peak_date = ""
            for p in pool:
                w = p.get('w', 0)
                if w > max_wind:
                    max_wind = w
                    peak_pres = p.get('p') or 0
                    peak_date = f"{p.get('t', '')[4:6]}/{p.get('t', '')[6:8]}" \
                        if len(p.get('t', '')) >= 8 else ""
                elif w == max_wind and w > 0:
                    pr = p.get('p') or 0
                    if pr and (not peak_pres or pr < peak_pres):
                        peak_pres = pr
                    if not peak_date and len(p.get('t', '')) >= 8:
                        peak_date = f"{p.get('t', '')[4:6]}/{p.get('t', '')[6:8]}"
            hours = 0.0
            try:
                s = datetime.strptime(ty.pts[0]['t'][:10], "%Y%m%d%H")
                e = datetime.strptime(ty.pts[-1]['t'][:10], "%Y%m%d%H")
                hours = (e - s).total_seconds() / 3600.0
            except Exception:
                pass
            km = 0.0
            st = None
            for i in range(len(ty.pts) - 1):
                p0, p1 = ty.pts[i], ty.pts[i + 1]
                seg, st = _haversine_chain(p1['la'], p1['lo'], p0['la'], p0['lo'], st)
                km += seg
            spd = km / hours if hours > 0 else 0.0

            code = f"{ty.basin}{ty.n}" if ty.basin else f"{ty.b}{ty.n}"
            intensity = f"{max_wind}kt"
            if peak_pres:
                intensity += f" {peak_pres}mb"
            if peak_date:
                intensity += f" ({peak_date})"
            d, h = int(hours // 24), int(hours % 24)
            rows.append({
                'cat': cat,
                'hemi': 'S' if ty.v.mirror else 'N',
                'line1': f"{code} {display_category(cat)}",
                'line2': ty.cust or ty.sname or ty.name,
                'right1': f"强度 {intensity}   ACE {ty.tace:.4f}",
                'right2': f"活跃 {d}d{h}h   路径 {km:.0f}km   均速 {spd:.0f}km/h",
                'wind': max_wind,
            })
        return rows

    def _build_stat_rows(self, year: int, basin_code, basin_area) -> list:
        sim = self.sim
        stats = calculate_season_stats(sim, year, basin_code)
        hemi = 'S' if getattr(sim, 'hemisphere', 'north') == 'south' else 'N'

        # C2 / C4(超强台风) 数量：按热带报点风速阈值统计
        c2 = 0
        c4st = 0
        for ty in sim.tys:
            ypts = [p for p in ty.pts if p.get('ace_year') == year]
            if basin_area is not None:
                ypts = [p for p in ypts if basin_area.contains(p['la'], p['lo'])]
            tp = [p for p in ypts if (p.get('st') or '').upper() not in _NON_TROPICAL]
            if any(p.get('w', 0) >= _WIND_C2_MIN for p in tp):
                c2 += 1
            if any(p.get('w', 0) >= _WIND_C4_ST_MIN for p in tp):
                c4st += 1

        def stat(cat, label, value):
            return {'cat': cat, 'hemi': hemi, 'line1': label, 'line2': "",
                    'right1': f"{value}", 'right2': f"{value}", 'wind': 0}

        rows = [
            stat('TD', "热带低压数量", stats['total_td']),
            stat('TS', "TS(热带风暴)数量", stats['total_ts']),
            stat('C1', "C1(台风)数量", stats['total_ty']),
            stat('C2', "C2数量", c2),
            stat('C3', "C3(MH)数量", stats['total_mh']),
            stat('C4', "C4(超强台风)数量", c4st),
            stat('C5', "C5数量", stats['total_c5']),
        ]
        wk = stats.get('wind_king')
        if wk:
            wk_wind = int(wk[1])
            # 按性质推断风王等级：找到该台风热带报点中的最大风速点
            wk_cat = sim.gsc(wk_wind, "")
            for ty in sim.tys:
                if sim.get_display_name(ty) == wk[0] and ty.pts:
                    pool = get_tropical_points(ty.pts) or ty.pts
                    if pool:
                        mwp = max(pool, key=lambda p: p.get('w', 0))
                        wk_cat = sim.gsc(mwp.get('w', 0), mwp.get('st', ''))
                        wk_wind = int(mwp.get('w', 0))
                    break
            rows.append({'cat': wk_cat, 'hemi': hemi, 'line1': "风王",
                         'line2': wk[0], 'right1': f"{wk_wind}kt",
                         'right2': f"{wk_wind}kt", 'wind': wk_wind})
        return rows

    # ── 事件 ──

    def _start_out(self, pending):
        if self._state == 'out':
            return
        self._state = 'out'
        self._pending = pending
        self._anim_t0 = pygame.time.get_ticks()

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._start_out('close')
                return True
            if e.key in (pygame.K_RIGHT, pygame.K_DOWN, pygame.K_PAGEDOWN, pygame.K_SPACE):
                if self._page + 1 < len(self._pages):
                    self._start_out(self._page + 1)
                return True
            if e.key in (pygame.K_LEFT, pygame.K_UP, pygame.K_PAGEUP):
                if self._page > 0:
                    self._start_out(self._page - 1)
                return True
            return True
        if e.type == pygame.MOUSEWHEEL:
            if e.y < 0 and self._page + 1 < len(self._pages):
                self._start_out(self._page + 1)
            elif e.y > 0 and self._page > 0:
                self._start_out(self._page - 1)
            return True
        if e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return True
        return False

    # ── 绘制 ──

    @staticmethod
    def _ease_out(t: float) -> float:
        return 1.0 - (1.0 - t) ** 3

    def _row_offset(self, i: int, now: int, bar_w: int, target_x: int) -> Optional[int]:
        """返回该行当前 x（None = 完全在屏外）。"""
        t0 = self._anim_t0 + i * self.STAGGER_MS
        p = (now - t0) / self.SLIDE_MS
        if self._state == 'in':
            if p <= 0:
                return None
            p = min(1.0, p)
            e = self._ease_out(p)
        else:
            if p >= 1:
                return None
            e = 1.0 - self._ease_out(max(0.0, min(1.0, p)))
        return int(target_x - (target_x + bar_w) * (1.0 - e))

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        sim = self.sim
        sw, sh = sim.screen_width, sim.screen_height
        self.bg_rect = pygame.Rect(0, 0, sw, sh)
        # 模糊地图背景
        if self._bg is not None:
            surface.blit(self._bg, (0, 0))
            # 背景下方延伸：控制面板区用纯色填充
            if self._bg.get_height() < sh:
                pygame.draw.rect(surface, (12, 14, 22),
                                 (0, self._bg.get_height(), sw,
                                  sh - self._bg.get_height()))
        else:
            surface.fill((16, 19, 28))

        now = pygame.time.get_ticks()
        rows = self._pages[self._page]

        # 布局：总结条严格保持视频原始比例 20:1；标题固定字号；
        # 整体垂直居中，标题与条间距收紧。
        is_stats_page = self._page == len(self._pages) - 1
        title = _outlined(_font_title,
                          self._stats_title if is_stats_page else self._title)
        title_gap = 6
        avail_h = sh - 24 - title.get_height() - title_gap - (self.ROWS - 1) * self.GAP
        bar_h = int(min(avail_h / self.ROWS, (sw - 30) / 20) * 0.85)
        bar_h = max(32, bar_h)
        bar_w = int(bar_h * 1920 / 96)          # 20:1 原始比例
        target_x = (sw - bar_w) // 2

        # 整体垂直居中
        content_h = title.get_height() + title_gap + self.ROWS * bar_h + (self.ROWS - 1) * self.GAP
        top = max(6, (sh - content_h) // 2)
        surface.blit(title, ((sw - title.get_width()) // 2, top))
        rows_y0 = top + title.get_height() + title_gap

        page_info = rt(_font_small, f"{self._page + 1}/{len(self._pages)}",
                       (140, 148, 162))
        surface.blit(page_info, (sw - page_info.get_width() - 16, 14))

        phase2 = (now - self._page_t0) >= self.PHASE_MS
        for i, row in enumerate(rows):
            x = self._row_offset(i, now, bar_w, target_x)
            if x is None:
                continue
            y = rows_y0 + i * (bar_h + self.GAP)
            self._draw_row(surface, row, pygame.Rect(x, y, bar_w, bar_h), now, phase2, i)

        # 每页展示 8 秒后自动翻页
        if self._state == 'in' and self._page + 1 < len(self._pages) \
                and now - self._page_t0 >= self.PAGE_MS:
            self._start_out(self._page + 1)

        # 收回动画结束 → 执行后续
        if self._state == 'out':
            last = self._anim_t0 + (max(len(rows), 1) - 1) * self.STAGGER_MS + self.SLIDE_MS
            if now >= last:
                if self._pending == 'close':
                    self.deactivate()
                else:
                    self._page = int(self._pending)
                    self._state = 'in'
                    self._anim_t0 = pygame.time.get_ticks()
                    self._page_t0 = self._anim_t0
                    # 翻页后重置视频帧计时,避免 idx 无限增长
                    # (非循环视频会提前结束,循环视频则动画阶段错乱)
                    self._open_t = self._page_t0
                self._pending = None

    def _draw_row(self, surface, row, rect, now, phase2, row_idx=0):
        # 深色背板（视频黑色部分为透明，白底上需要垫底）
        pygame.draw.rect(surface, (16, 19, 28), rect)
        # 视频帧按 ~15fps 节流 + 各行错峰,避免 3 行同时解码造成卡顿
        key = (row['cat'], row['hemi'], rect.size)
        entry = self._row_frame_cache.get(key)
        if entry is None or (now - entry[0]) >= _FRAME_INTERVAL_MS + (row_idx % 3) * 8:
            idx = int((now - self._open_t) * 60 / 1000)
            frame = get_summary_frame(row['cat'], row['hemi'], idx, rect.size)
            if len(self._row_frame_cache) > 32:
                self._row_frame_cache.pop(next(iter(self._row_frame_cache)))
            self._row_frame_cache[key] = (now, frame)
        else:
            frame = entry[1]
        if frame is not None:
            tier = _purple_tier(row.get('wind', 0)) if row['cat'] == 'C5' else None
            if tier is not None:
                pkey = (id(frame), tier)
                filtered = _purple_frame_cache.get(pkey)
                if filtered is None:
                    filtered = _apply_purple_filter(frame, tier[1])
                    if len(_purple_frame_cache) >= _PURPLE_CACHE_MAX:
                        _purple_frame_cache.pop(next(iter(_purple_frame_cache)))
                    _purple_frame_cache[pkey] = filtered
                frame = filtered
            surface.blit(frame, rect.topleft)
        cat_color = _CAT_COLOR.get(row['cat'], (200, 200, 220))
        pygame.draw.rect(surface, cat_color, rect, 2)

        # 字号随条高缩放
        h_small = max(10, int(rect.h * 0.30))
        h_name = max(12, int(rect.h * 0.42))
        h_info = max(12, int(rect.h * 0.40))

        # 左侧
        left_x = rect.x + rect.w // 5
        if row['line2']:
            s1 = _outlined(_font_small, row['line1'], h_small)
            s2 = _outlined(_font_name, row['line2'], h_name)
            top = rect.y + (rect.h - s1.get_height() - s2.get_height()) // 2
            surface.blit(s1, (left_x, top))
            surface.blit(s2, (left_x, top + s1.get_height()))
        else:
            s1 = _outlined(_font_name, row['line1'], h_name)
            surface.blit(s1, (left_x, rect.y + (rect.h - s1.get_height()) // 2))

        # 右侧（前 4s 强度/ACE，后 4s 活跃/路径/均速）
        right = row['right2'] if phase2 else row['right1']
        st = _outlined(_font_info, right, h_info)
        surface.blit(st, (rect.right - 15 - st.get_width(),
                          rect.y + (rect.h - st.get_height()) // 2))
