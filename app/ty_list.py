# py/ty_list.py
"""台风列表对话框（支持搜索、洋区排序、翻页、台风季时间跳转）。"""
from __future__ import annotations

import os
import time
import re
import pygame
from datetime import datetime
from .constants import (
    f_s, f_m, rt, TXT, LIST_HL, BUTTON_BORDER, BUTTON_DISABLED, BUTTON_BG,
    TY_LIST_ROWS_PER_PAGE, TY_LIST_ITEM_HEIGHT, TY_LIST_WIDTH, TY_LIST_TOP_OFFSET,
    DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_TEXT_LIGHT,
)
from .input_field import InputField
from .dialog_base import DraggableDialog
from .utils import display_category
from typing import List, Dict, Tuple


_ARROW_SIZE = 18


def _ty_max_wind(ty) -> int:
    """台风最大风速(排除 MD/SS/SD/EX/LO 等非风暴状态)。"""
    mw, _st = _ty_peak(ty)
    return mw


def _ty_peak(ty):
    """(峰值风速, 峰值报点性质)。排除非热带性质报点。"""
    vp = [p for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
    if not vp:
        return 0, ""
    mwp = max(vp, key=lambda p: p['w'])
    return mwp['w'], mwp.get('st', '')


def _fmt_short_time(t) -> str:
    """YYYYMMDDHH[MM] → 'MM-DD HH:MM'(含分钟时显示真实分钟)。"""
    from .utils import fmt_short_time
    return fmt_short_time(t)


def _fmt_timespan(st: str, et: str) -> str:
    """'01-01 00:00~01-06 12:00 (5天)'。"""
    if len(st) >= 10 and len(et) >= 10:
        s = _fmt_short_time(st)
        e = _fmt_short_time(et)
        try:
            d0 = datetime.strptime(st[:10], "%Y%m%d%H")
            d1 = datetime.strptime(et[:10], "%Y%m%d%H")
            return f"{s}~{e} ({max(0, (d1 - d0).days)}天)"
        except ValueError:
            return f"{s}~{e}"
    return f"{st}~{et}"


class TyList(DraggableDialog):
    def __init__(self, s):
        super().__init__(s)
        self.si = -1
        self.current_page = 0
        self.rows_per_page = TY_LIST_ROWS_PER_PAGE
        self.ei = -1
        self.edit_type = None
        self.hi = -1
        self.hst = 0
        self.lct = 0
        self.jump_active = False
        self.jump_input = ""
        self._clear_edit_field()

        self.search_field = InputField(pygame.Rect(0, 0, 180, 24), max_length=50, font=f_s,
                                        dark=self.dark_mode)
        self._filtered_indices: List[int] = []
        self._basin_order: Dict[str, int] = {}
        self._area_name_map: Dict[str, str] = {}

        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self.title = rt(f_m, "台风列表", TXT)
        self.title_dark = rt(f_m, "台风列表", SETTINGS_TEXT_LIGHT)
        self.name_btn_text = rt(f_s, "编辑名称", (255, 255, 255))
        self.number_btn_text = rt(f_s, "编辑编号", (255, 255, 255))
        self.filename_btn_text = rt(f_s, "编辑文件名", (255, 255, 255))
        self.jump_text = rt(f_s, "跳页", (255, 255, 255))
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))

        self._row_cache: Dict[int, Dict[str, pygame.Surface]] = {}
        self._row_hashes: Dict[int, str] = {}
        self._row_texts_cache: dict = {}

        self._key_held_timer = 0
        self._key_held_key = 0
        self._KEY_REPEAT_DELAY = 500
        self._KEY_REPEAT_INTERVAL = 120

    def activate(self):
        super().activate()
        if 'ty_list' in self.sim.dialog_page_cache:
            self.current_page = self.sim.dialog_page_cache['ty_list']
        else:
            self.current_page = 0
        self.sort_mode = self.sim.dialog_page_cache.get('ty_list_sort', 'time')
        self.si = -1
        self.ei = -1
        self.edit_type = None
        self.hi = -1
        self.jump_active = False
        self._clear_edit_field()

        self._build_basin_order()
        self.search_field.deactivate()
        self._apply_filter(reset_page=False)
        self.current_page = min(self.current_page, self.get_total_pages() - 1)
        if self._filtered_indices:
            start = self.get_page_start()
            self.si = self._filtered_indices[start] if start < len(self._filtered_indices) else -1

        self._clear_row_cache()
        self._update_bg_rect()

    def _build_basin_order(self):
        areas = getattr(getattr(self.sim, 'res_mgr', None), 'ocean_areas', None)
        if areas and areas.areas:
            self._basin_order = {a.code: i for i, a in enumerate(areas.areas)}
            self._area_name_map = {a.code: a.name_cn for a in areas.areas}
        else:
            self._basin_order = {}
            self._area_name_map = {}

    def deactivate(self):
        self.sim.dialog_page_cache['ty_list'] = self.current_page
        super().deactivate()
        self._clear_edit_field()
        self.dragging = False
        self.search_field.deactivate()

    def _clear_edit_field(self):
        if getattr(self, 'edit_field', None) is not None:
            self.edit_field.deactivate()
        self.edit_field = None

    def _clear_row_cache(self):
        self._row_cache.clear()
        self._row_hashes.clear()
        # 行文本缓存随行缓存一起清,避免台风对象重建后残留陈旧条目
        self._row_texts_cache.clear()

    def _apply_filter(self, reset_page=True):
        text = self.search_field.get_text().strip().lower()
        sort_key = self._sort_key_fn()
        if not text:
            self._filtered_indices = sorted(range(len(self.sim.tys)), key=sort_key)
        else:
            self._filtered_indices = sorted(
                (i for i, ty in enumerate(self.sim.tys) if self._matches(ty, text)),
                key=sort_key,
            )
        # 法26: idx → 过滤后位置映射,O(1) 查页码
        self._pos_map = {idx: pos for pos, idx in enumerate(self._filtered_indices)}
        if reset_page:
            self.current_page = 0
            self.si = -1
        self._clear_row_cache()

    def _matches(self, ty, text) -> bool:
        """搜索匹配: 名称/编号/盆域编号/文件名。"""
        if text in self.sim.get_display_name(ty).lower():
            return True
        if text in str(ty.n).lower():
            return True
        if text in f"{ty.b}{ty.n}".lower():
            return True
        if text in str(ty.basin).lower():
            return True
        fn = os.path.basename(ty.filepath).lower() if ty.filepath else ""
        return text in fn

    def _sort_key_fn(self):
        mode = getattr(self, 'sort_mode', 'time')
        sim = self.sim
        basin_order = self._basin_order
        if mode == 'ace':
            return lambda idx: (-sim.tys[idx].tace,
                                sim.get_display_name(sim.tys[idx]).lower())
        if mode == 'wind':
            return lambda idx: (-_ty_max_wind(sim.tys[idx]),
                                sim.get_display_name(sim.tys[idx]).lower())
        return lambda idx: (basin_order.get(sim.tys[idx].basin, 9999),
                            sim.tys[idx].pts[0]['t'] if sim.tys[idx].pts else "99999999",
                            sim.get_display_name(sim.tys[idx]).lower())

    def _build_row_texts(self, ty) -> Tuple[str, str]:
        base = self.sim.get_display_name(ty)
        mode = self.sim.name_display_mode
        if mode == 0:
            sy = ty.pts[0]['t'][:4] if ty.pts else "????"
            disp = f"{sy} {base}"
        else:
            disp = base
        mw, peak_st = _ty_peak(ty)
        cat = self.sim.gsc(mw, peak_st) if mw else "N/A"
        st = ty.pts[0]['t'] if ty.pts else "????"
        et = ty.pts[-1]['t'] if ty.pts else "????"
        span = _fmt_timespan(st, et) if ty.pts else ""
        area_name = self._area_name_map.get(ty.basin, ty.basin)
        info = f"{display_category(cat)} {mw}kt  ACE:{ty.tace:.4f}  {span}  {len(ty.pts)}点  {area_name}"
        return disp, info

    def _row_hash(self, ty):
        # N25: 仅构建字符串哈希(纯文本,便宜),surface 仅在哈希失配时构建
        disp, info = self._build_row_texts_cached(ty)
        return f"{disp}|{info}|{self.dark_mode}"

    def _row_surfs(self, ty):
        disp, info = self._build_row_texts_cached(ty)
        tc = SETTINGS_TEXT_LIGHT if self.dark_mode else TXT
        return {'name': rt(f_m, disp, tc, 530), 'info': rt(f_s, info, tc, 530)}

    def _build_row_texts_cached(self, ty) -> Tuple[str, str]:
        """法15: O(P) 扫描结果按 (pts id/len, tace, 显示模式, 首末点时间, 盆域, cust/n) 缓存。"""
        mode = self.sim.name_display_mode
        key = (id(ty.pts), len(ty.pts), ty.tace, mode, self.dark_mode, ty.basin,
               ty.pts[0]['t'] if ty.pts else '',
               ty.pts[-1]['t'] if ty.pts else '',
               getattr(ty, 'cust', ''), getattr(ty, 'n', ''))
        cached = self._row_texts_cache
        hit = cached.get(id(ty))
        if hit is not None and hit[0] == key:
            return hit[1]
        result = self._build_row_texts(ty)
        if len(cached) > 1024:
            cached.pop(next(iter(cached)))
        cached[id(ty)] = (key, result)
        return result

    def _update_bg_rect(self):
        lw = TY_LIST_WIDTH
        lh = TY_LIST_TOP_OFFSET + self.rows_per_page * TY_LIST_ITEM_HEIGHT + 100 + 30
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - lw) // 2,
            (self.sim.screen_height - lh) // 2, lw, lh)

    def _search_rect(self):
        return pygame.Rect(self.bg_rect.x + 20, self.bg_rect.y + 45, 180, 24)

    def _sort_btn(self):
        return pygame.Rect(self.bg_rect.x + 210, self.bg_rect.y + 45, 110, 24)

    def _action_buttons(self):
        lx, ly, lw, lh = self.bg_rect
        box_w, box_h, gap = 80, 25, 10
        tw = 3 * box_w + 2 * gap
        sx = lx + (lw - tw) // 2
        y = ly + lh - 45
        return {
            'name': pygame.Rect(sx, y, box_w, box_h),
            'number': pygame.Rect(sx + box_w + gap, y, box_w, box_h),
            'filename': pygame.Rect(sx + 2 * (box_w + gap), y, box_w, box_h),
        }

    def _jump_btn(self):
        return pygame.Rect(self.bg_rect.right - 30 - 80 - 5 - _ARROW_SIZE,
                           self.bg_rect.bottom - 40, 80, 25)

    def _page_left_btn(self):
        return pygame.Rect(self._jump_btn().left - _ARROW_SIZE - 5,
                           self.bg_rect.bottom - 40, _ARROW_SIZE, _ARROW_SIZE)

    def _page_right_btn(self):
        return pygame.Rect(self._jump_btn().right + 5,
                           self.bg_rect.bottom - 40, _ARROW_SIZE, _ARROW_SIZE)

    def get_total_pages(self):
        return max(1, (len(self._filtered_indices) + self.rows_per_page - 1) // self.rows_per_page)

    def get_page_start(self):
        return self.current_page * self.rows_per_page

    # ═══════════════════════════════════════════════
    #  事件
    # ═══════════════════════════════════════════════
    def _handle_key_repeat(self):
        if not self._key_held_key:
            return
        now = pygame.time.get_ticks()
        elapsed = now - self._key_held_timer
        if elapsed < self._KEY_REPEAT_DELAY:
            return
        ticks_from_start = elapsed - self._KEY_REPEAT_DELAY
        expected = int(ticks_from_start / self._KEY_REPEAT_INTERVAL)
        if expected > getattr(self, '_lr_tick', 0):
            count = expected - getattr(self, '_lr_tick', 0)
            delta = -1 if self._key_held_key == pygame.K_LEFT else 1
            for _ in range(count):
                self._set_page(self.current_page + delta)
            self._lr_tick = expected

    def _set_page(self, page: int) -> None:
        """切页: 夹紧页码、同步选中行,并清除可能残留的编辑框状态。"""
        self.current_page = max(0, min(page, self.get_total_pages() - 1))
        s = self.get_page_start()
        self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
        self.ei = -1
        self.edit_type = None
        self._clear_edit_field()

    def _page_wheel_or_btn(self, delta: int) -> bool:
        np = self.current_page + delta
        if 0 <= np < self.get_total_pages():
            self._set_page(np)
            return True
        return False

    def handle_event(self, e):
        if not self.active:
            return False
        if self.handle_drag_event(e):
            return True

        if self.jump_active:
            return self._handle_jump(e)

        if self.edit_field and self.edit_field.handle_event(e):
            return True

        sr = self._search_rect()
        self.search_field.rect = sr
        sh = self.search_field.handle_event(e)

        if self.search_field.active:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    self.search_field.set_text("")
                    self.search_field.deactivate()
                    self._apply_filter(True)
                    return True
                if e.key == pygame.K_RETURN:
                    self.search_field.deactivate()
                    self._apply_filter(True)
                    return True
                if sh:
                    self._apply_filter(True)
                    return True
                return True
            if e.type == pygame.MOUSEBUTTONDOWN and sh:
                self._apply_filter(True)
                return True
            return True

        if e.type == pygame.MOUSEWHEEL and self.bg_rect.collidepoint(pygame.mouse.get_pos()):
            self._page_wheel_or_btn(-1 if e.y > 0 else 1)
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.dragging:
                return True

            if self._page_left_btn().collidepoint(e.pos):
                self._page_wheel_or_btn(-1)
                return True
            if self._page_right_btn().collidepoint(e.pos):
                self._page_wheel_or_btn(1)
                return True

            if self._jump_btn().collidepoint(e.pos):
                self.jump_active = True
                self.jump_input = ""
                return True

            if self._sort_btn().collidepoint(e.pos):
                order = ['time', 'ace', 'wind']
                self.sort_mode = order[(order.index(self.sort_mode) + 1) % 3]
                self.sim.dialog_page_cache['ty_list_sort'] = self.sort_mode
                self._apply_filter(True)
                return True

            for action, rect in self._action_buttons().items():
                if rect.collidepoint(e.pos) and self.si != -1:
                    self.ei = self.si
                    self.edit_type = action
                    self._clear_edit_field()
                    return True

            if self.bg_rect.collidepoint(e.pos):
                ry = e.pos[1] - self.bg_rect.y - TY_LIST_TOP_OFFSET - 20
                if ry >= 0:
                    row = ry // TY_LIST_ITEM_HEIGHT
                    idx_f = self.get_page_start() + row
                    if 0 <= row < self.rows_per_page and idx_f < len(self._filtered_indices):
                        oi = self._filtered_indices[idx_f]
                        now = time.time()
                        if now - self.lct < 0.3 and oi == self.si:
                            self._select(oi)
                        else:
                            self.si = oi
                            self.ei = -1
                            self.edit_type = None
                            self._clear_edit_field()
                        self.lct = now
                return True

        if e.type == pygame.KEYDOWN:
            return self._keydown(e)

        if e.type == pygame.KEYUP and e.key in (pygame.K_LEFT, pygame.K_RIGHT):
            if self._key_held_key == e.key:
                self._key_held_key = 0
            return True

        if e.type == pygame.MOUSEMOTION and not self.dragging:
            if self.bg_rect.collidepoint(e.pos):
                ry = e.pos[1] - self.bg_rect.y - TY_LIST_TOP_OFFSET - 20
                if ry >= 0:
                    row = ry // TY_LIST_ITEM_HEIGHT
                    idx = self.get_page_start() + row
                    if 0 <= row < self.rows_per_page and idx < len(self._filtered_indices):
                        oi = self._filtered_indices[idx]
                        if oi != self.hi:
                            self.hi = oi
                            self.hst = pygame.time.get_ticks()
                        return True
            self.hi = -1
        return False

    def _select(self, idx):
        md = self.sim.md
        if md == "normal":
            self.sim.cti = idx
            self.sim.current_typhoon().rst()
        elif md == "season":
            self._jump_to_typhoon_start(idx)
        else:
            self.sim.edit_typhoon = self.sim.tys[idx]
            # 切换编辑台风后复位选中点,避免旧索引越界
            self.sim._edit_selected_point = None
        self.deactivate()

    def _jump_to_typhoon_start(self, idx):
        ty = self.sim.tys[idx]
        if not ty.pts:
            return
        ft = ty.pts[0]['t']
        try:
            y, m, d, h = int(ft[:4]), int(ft[4:6]), int(ft[6:8]), int(ft[8:10])
            target = datetime(y, m, d, h)
        except (ValueError, IndexError):
            return

        if hasattr(self.sim, 'season_ctrl'):
            self.sim.season_ctrl.jump_to(target)
            self.sim._sync_season_state()

        chart = getattr(getattr(self.sim, 'dialog_mgr', None), 'ace_chart', None)
        if chart and chart.active:
            chart.needs_update = True

    def _keydown(self, e):
        if e.key == pygame.K_ESCAPE:
            if self.ei != -1:
                self.ei = -1
                self.edit_type = None
                self._clear_edit_field()
            else:
                self.deactivate()
            return True

        if e.key == pygame.K_RETURN:
            if self.ei != -1 and self.edit_field:
                self._apply_edit()
            elif self.ei != -1:
                try:
                    page = self._pos_map.get(self.ei, -1) // self.rows_per_page
                except (ValueError, AttributeError):
                    page = self.current_page
                self.current_page = page
                self.si = self.ei
                self._clear_edit_field()
                ty = self.sim.tys[self.ei]
                self._ensure_edit_field(ty)
                self._apply_edit()
            elif self.si != -1:
                self._select(self.si)
            return True

        if self.ei != -1:
            if e.key == pygame.K_TAB:
                actions = ['name', 'number', 'filename']
                idx = actions.index(self.edit_type)
                delta = -1 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                self.edit_type = actions[(idx + delta) % 3]
                self._clear_edit_field()
                return True
            return False

        if e.key in (pygame.K_UP, pygame.K_DOWN) and self.si != -1 and self._filtered_indices:
            cur = self._pos_map.get(self.si, -1)
            if cur < 0 or cur >= len(self._filtered_indices):
                # 选中行已不在当前过滤集合(编辑名称/编号后),回退到列表首行
                cur = 0
                self.si = self._filtered_indices[0]
            delta = -1 if e.key == pygame.K_UP else 1
            nxt = (cur + delta) % len(self._filtered_indices)
            np_page = nxt // self.rows_per_page
            if np_page != self.current_page:
                self.current_page = np_page
            self.si = self._filtered_indices[nxt]
            return True

        if e.key in (pygame.K_LEFT, pygame.K_RIGHT):
            self._key_held_timer = pygame.time.get_ticks()
            self._key_held_key = e.key
            self._lr_tick = 0
            delta = -1 if e.key == pygame.K_LEFT else 1
            self._set_page(self.current_page + delta)
            return True

        return False

    def _ensure_edit_field(self, ty):
        if self.edit_field is not None:
            return
        try:
            rel = self._pos_map.get(self.ei, -1) % self.rows_per_page
        except ValueError:
            rel = 0
        lx = self.bg_rect.x
        y = self.bg_rect.y + TY_LIST_TOP_OFFSET + rel * TY_LIST_ITEM_HEIGHT + 20
        if self.edit_type == 'name':
            self.edit_field = InputField((lx + 30, y + 5, 300, 25),
                                         max_length=30, dark=self.dark_mode)
            self.edit_field.set_text(ty.cust or "")
        else:
            self.edit_field = InputField((lx + 30, y + 35, 200, 25),
                                         max_length=20, dark=self.dark_mode)
            if self.edit_type == 'number':
                self.edit_field.set_text(ty.n)
            else:
                fname = os.path.basename(ty.filepath).replace('.txt', '') if ty.filepath else ""
                self.edit_field.set_text(fname)
        self.edit_field.activate()

    def _apply_edit(self):
        ty = self.sim.tys[self.ei]
        new_text = self.edit_field.get_text()
        if self.edit_type == 'name':
            ty.cust = new_text
            # N20: 自定义名称持久化到 config.tn
            self.sim.cfg.tn[f"{ty.b}{ty.n}"] = new_text
        elif self.edit_type == 'number':
            old_key = f"{ty.b}{ty.n}"
            ty.n = new_text
            ty.name = f"{ty.b}{new_text}"
            # N20: 编号变更迁移自定义名称键
            if old_key in self.sim.cfg.tn:
                self.sim.cfg.tn[f"{ty.b}{new_text}"] = self.sim.cfg.tn.pop(old_key)
            elif ty.cust:
                self.sim.cfg.tn[f"{ty.b}{new_text}"] = ty.cust
        elif self.edit_type == 'filename':
            if ty.filepath:
                safe = re.sub(r'[^a-zA-Z0-9_\-]', '', new_text) or "typhoon"
                new_path = os.path.join(os.path.dirname(ty.filepath), safe + ".txt")
                try:
                    os.rename(ty.filepath, new_path)
                    ty.filepath = new_path
                except (IOError, OSError) as e:
                    self.sim.show_error(f"重命名文件失败: {e}")
        self.sim.save_config()
        self.ei = -1
        self.edit_type = None
        self._clear_edit_field()
        # 名称/编号变更影响排序与搜索: 重新过滤,避免 _pos_map/排序过期错位
        self._apply_filter(False)

    def _handle_jump(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.jump_active = False
                self.jump_input = ""
                return True
            if e.key == pygame.K_RETURN:
                self._do_jump()
                return True
            if e.key == pygame.K_BACKSPACE:
                self.jump_input = self.jump_input[:-1]
                return True
            if e.unicode.isdigit() and len(self.jump_input) < 3:
                self.jump_input += e.unicode
                return True
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if hasattr(self, 'jump_confirm_btn') and self.jump_confirm_btn.collidepoint(x, y):
                self._do_jump()
                return True
            if hasattr(self, 'jump_cancel_btn') and self.jump_cancel_btn.collidepoint(x, y):
                self.jump_active = False
                self.jump_input = ""
                return True
        return True

    def _do_jump(self):
        try:
            page = int(self.jump_input)
            if 1 <= page <= self.get_total_pages():
                self._set_page(page - 1)
                self.jump_active = False
                self.jump_input = ""
            else:
                self.jump_input = ""
        except ValueError:
            self.jump_input = ""
        self.jump_active = False

    # ═══════════════════════════════════════════════
    #  绘制
    # ═══════════════════════════════════════════════
    def draw(self, surface):
        if not self.active:
            return
        self._handle_key_repeat()
        lx, ly, lw, lh = self.bg_rect
        dark = self.dark_mode
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        if dark:
            self.draw_dark_panel(surface, self.bg_rect)
            self.draw_title(surface, self.title_dark, self.bg_rect, y_offset=15)
        else:
            self.draw_background(surface, self.bg_rect)
            self.draw_title(surface, self.title, self.bg_rect, y_offset=15)

        self.search_field.rect = self._search_rect()
        self.search_field.draw(surface)

        # 排序模式切换按钮
        sort_labels = {'time': "排序:时间", 'ace': "排序:ACE", 'wind': "排序:强度"}
        sort_surf = rt(f_s, sort_labels.get(getattr(self, 'sort_mode', 'time'), "排序:时间"), tc)
        sb = self._sort_btn()
        if dark:
            self.draw_dark_button(surface, sb, sort_surf)
        else:
            self.draw_button(surface, sb, sort_surf, BUTTON_BG)

        start = self.get_page_start()
        end = min(start + self.rows_per_page, len(self._filtered_indices))
        for i in range(start, end):
            row = i - start
            y = ly + TY_LIST_TOP_OFFSET + row * TY_LIST_ITEM_HEIGHT + 20
            oi = self._filtered_indices[i]
            ty = self.sim.tys[oi]

            if oi == self.si:
                hl = pygame.Surface((lw - 40, 66), pygame.SRCALPHA)
                hl.fill((70, 105, 165, 130) if dark else LIST_HL)
                surface.blit(hl, (lx + 20, y + 2))

            if self.ei == oi and self.edit_type == 'name':
                self._ensure_edit_field(ty)
            else:
                # N25: 哈希不变时直接复用缓存 surface(仅哈希变化才重建)
                new_hash = self._row_hash(ty)
                if self._row_hashes.get(oi) != new_hash:
                    self._row_cache[oi] = self._row_surfs(ty)
                    self._row_hashes[oi] = new_hash
                surface.blit(self._row_cache[oi]['name'], (lx + 30, y + 5))
                surface.blit(self._row_cache[oi]['info'], (lx + 30, y + 35))

            if self.ei == oi and self.edit_type in ('number', 'filename'):
                self._ensure_edit_field(ty)

        if self.edit_field:
            self.edit_field.draw(surface)

        page_info = f"第 {self.current_page + 1}/{self.get_total_pages()} 页  共 {len(self._filtered_indices)} 条"
        surface.blit(rt(f_s, page_info, tc), (lx + 20, ly + lh - 80))

        for action, rect in self._action_buttons().items():
            texts = {'name': self.name_btn_text, 'number': self.number_btn_text,
                     'filename': self.filename_btn_text}
            if dark:
                self.draw_dark_button(surface, rect, texts[action])
            else:
                self.draw_button(surface, rect, texts[action], BUTTON_BG)

        pl = self._page_left_btn()
        pr = self._page_right_btn()
        pygame.draw.polygon(surface, tc,
            [(pl.right, pl.top), (pl.right, pl.bottom), (pl.left + 4, pl.centery)])
        pygame.draw.polygon(surface, tc,
            [(pr.left, pr.top), (pr.left, pr.bottom), (pr.right - 4, pr.centery)])

        if dark:
            self.draw_dark_button(surface, self._jump_btn(), self.jump_text)
        else:
            self.draw_button(surface, self._jump_btn(), self.jump_text, BUTTON_BG)

        if self.jump_active:
            ov = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 140 if dark else 100))
            surface.blit(ov, (0, 0))
            item_rect = pygame.Rect(self.sim.screen_width // 2 - 100, self.sim.screen_height // 2 - 30, 200, 40)
            if dark:
                pygame.draw.rect(surface, (35, 40, 54), item_rect, 0, 4)
                pygame.draw.rect(surface, (80, 110, 160), item_rect, 2, 4)
                pop_tc = SETTINGS_TEXT_LIGHT
            else:
                pygame.draw.rect(surface, (255, 255, 255), item_rect)
                pygame.draw.rect(surface, BUTTON_BORDER, item_rect, 2)
                pop_tc = TXT
            prompt = rt(f_s, f"输入页码 (1-{self.get_total_pages()}):", pop_tc)
            surface.blit(prompt, (item_rect.x, item_rect.y - 25))
            it = rt(f_s, self.jump_input + ("_" if pygame.time.get_ticks() % 1000 < 500 else ""), pop_tc)
            surface.blit(it, (item_rect.x + 5, item_rect.y + 10))
            cb = pygame.Rect(item_rect.x + 20, item_rect.y + 50, 60, 30)
            ca = pygame.Rect(item_rect.x + 120, item_rect.y + 50, 60, 30)
            if dark:
                self.draw_dark_button(surface, cb, self.confirm_text, accent=True)
                self.draw_dark_button(surface, ca, self.cancel_text)
            else:
                self.draw_button(surface, cb, self.confirm_text, BUTTON_BORDER)
                self.draw_button(surface, ca, self.cancel_text, BUTTON_DISABLED)
            self.jump_confirm_btn = cb
            self.jump_cancel_btn = ca

        ct = pygame.time.get_ticks()
        if self.hi != -1 and ct - self.hst > 800:
            self._draw_tooltip(surface)

    def _draw_tooltip(self, surface):
        if self.hi < 0 or self.hi >= len(self.sim.tys):
            return
        ty = self.sim.tys[self.hi]
        if not ty.pts:
            return
        dark = self.dark_mode
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        mouse_x, mouse_y = pygame.mouse.get_pos()
        tw, th = 280, 190
        tx = min(mouse_x + 20, self.sim.screen_width - tw - 10)
        ty2 = min(mouse_y + 20, self.sim.screen_height - th - 10)
        text_surface = pygame.Surface((tw, th), pygame.SRCALPHA)
        if dark:
            text_surface.fill((25, 30, 44, 235))
            pygame.draw.rect(text_surface, (70, 100, 150), (0, 0, tw, th), 2, 8)
        else:
            text_surface.fill((255, 255, 255, 230))
            pygame.draw.rect(text_surface, BUTTON_BORDER, (0, 0, tw, th), 2, 8)

        dn = self.sim.get_display_name(ty)
        text_surface.blit(rt(f_m, f"台风: {dn}", tc), (10, 10))
        fp, lp = ty.pts[0], ty.pts[-1]
        text_surface.blit(rt(f_s, f"点数: {len(ty.pts)}   时长: {_fmt_timespan(fp['t'], lp['t'])}", tc), (10, 40))
        text_surface.blit(rt(f_s, f"起点: {_fmt_short_time(fp['t'])}  {fp['la']:.1f}°N, {fp['lo']:.1f}°E", tc), (10, 90))
        text_surface.blit(rt(f_s, f"终点: {_fmt_short_time(lp['t'])}  {lp['la']:.1f}°N, {lp['lo']:.1f}°E", tc), (10, 110))
        vp = [p for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
        if vp:
            mwp = max(vp, key=lambda p: p['w'])
            mw = mwp['w']
            mc = self.sim.gsc(mw, mwp.get('st', ''))
        else:
            mw, mc = 0, "N/A"
        text_surface.blit(rt(f_s, f"最大强度: {display_category(mc)} ({mw}kt)", tc), (10, 140))
        text_surface.blit(rt(f_s, f"ACE: {ty.tace:.4f}", tc), (10, 160))
        surface.blit(text_surface, (tx, ty2))