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
)
from .input_field import InputField
from .dialog_base import DraggableDialog
from typing import List, Dict, Tuple


_ARROW_SIZE = 18


def _make_sort_key(sim, basin_order: Dict[str, int]):
    def key(idx: int):
        ty = sim.tys[idx]
        basin_idx = basin_order.get(ty.basin, 9999)
        first_time = ty.pts[0]['t'] if ty.pts else "99999999"
        name = sim.get_display_name(ty).lower()
        return (basin_idx, first_time, name)
    return key


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
        self.edit_field = None

        self.search_field = InputField(pygame.Rect(0, 0, 180, 24), max_length=50, font=f_s)
        self._filtered_indices: List[int] = []
        self._basin_order: Dict[str, int] = {}
        self._area_name_map: Dict[str, str] = {}

        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self.title = rt(f_m, "台风列表", TXT)
        self.name_btn_text = rt(f_s, "编辑名称", (255, 255, 255))
        self.number_btn_text = rt(f_s, "编辑编号", (255, 255, 255))
        self.filename_btn_text = rt(f_s, "编辑文件名", (255, 255, 255))
        self.jump_text = rt(f_s, "跳页", (255, 255, 255))
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))

        self._row_cache: Dict[int, Dict[str, pygame.Surface]] = {}
        self._row_hashes: Dict[int, str] = {}

    def activate(self):
        super().activate()
        if 'ty_list' in self.sim.dialog_page_cache:
            self.current_page = self.sim.dialog_page_cache['ty_list']
        else:
            self.current_page = 0
        self.si = -1
        self.ei = -1
        self.edit_type = None
        self.hi = -1
        self.jump_active = False
        self.edit_field = None

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
        self.edit_field = None
        self.dragging = False
        self.search_field.deactivate()

    def _clear_row_cache(self):
        self._row_cache.clear()
        self._row_hashes.clear()

    def _apply_filter(self, reset_page=True):
        text = self.search_field.get_text().strip().lower()
        sort_key = _make_sort_key(self.sim, self._basin_order)
        if not text:
            self._filtered_indices = sorted(range(len(self.sim.tys)), key=sort_key)
        else:
            self._filtered_indices = sorted(
                (i for i, ty in enumerate(self.sim.tys)
                 if text in self.sim.get_display_name(ty).lower()),
                key=sort_key,
            )
        if reset_page:
            self.current_page = 0
            self.si = -1
        self._clear_row_cache()

    def _build_row_texts(self, ty) -> Tuple[str, str]:
        base = self.sim.get_display_name(ty)
        mode = self.sim.name_display_mode
        if mode == 0:
            sy = ty.pts[0]['t'][:4] if ty.pts else "????"
            disp = f"{sy} {base}"
        else:
            disp = base
        vw = [p['w'] for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
        mw = max(vw) if vw else 0
        cat = self.sim.gsc(mw, "")
        st = ty.pts[0]['t'] if ty.pts else "????"
        area_name = self._area_name_map.get(ty.basin, ty.basin) if hasattr(self, '_area_name_map') else ''
        info = f"{cat} {mw}kt  ACE:{ty.tace:.4f}  起始:{st}  {area_name}"
        return disp, info

    def _get_row_hash(self, ty) -> str:
        disp, info = self._build_row_texts(ty)
        return f"{disp}|{info}"

    def _render_row(self, ty) -> Dict[str, pygame.Surface]:
        disp, info = self._build_row_texts(ty)
        return {'name': rt(f_m, disp, TXT, 530), 'info': rt(f_s, info, TXT, 530)}

    def _update_bg_rect(self):
        lw = TY_LIST_WIDTH
        lh = TY_LIST_TOP_OFFSET + self.rows_per_page * TY_LIST_ITEM_HEIGHT + 100 + 30
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - lw) // 2,
            (self.sim.screen_height - lh) // 2, lw, lh)

    def _search_rect(self):
        return pygame.Rect(self.bg_rect.x + 20, self.bg_rect.y + 45, 180, 24)

    def _action_buttons(self):
        lx, ly, lw, lh = self.bg_rect
        bw, bh, gap = 80, 25, 10
        tw = 3 * bw + 2 * gap
        sx = lx + (lw - tw) // 2
        y = ly + lh - 45
        return {
            'name': pygame.Rect(sx, y, bw, bh),
            'number': pygame.Rect(sx + bw + gap, y, bw, bh),
            'filename': pygame.Rect(sx + 2 * (bw + gap), y, bw, bh),
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
            delta = -1 if e.y > 0 else 1
            np = self.current_page + delta
            if 0 <= np < self.get_total_pages():
                self.current_page = np
                self.si = self._filtered_indices[self.get_page_start()] if self._filtered_indices else -1
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.dragging:
                return True

            if self._page_left_btn().collidepoint(e.pos):
                if self.current_page > 0:
                    self.current_page -= 1
                    s = self.get_page_start()
                    self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
                return True
            if self._page_right_btn().collidepoint(e.pos):
                if self.current_page < self.get_total_pages() - 1:
                    self.current_page += 1
                    s = self.get_page_start()
                    self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
                return True

            if self._jump_btn().collidepoint(e.pos):
                self.jump_active = True
                self.jump_input = ""
                return True

            for action, rect in self._action_buttons().items():
                if rect.collidepoint(e.pos) and self.si != -1:
                    self.ei = self.si
                    self.edit_type = action
                    self.edit_field = None
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
                            self.edit_field = None
                        self.lct = now
                return True

        if e.type == pygame.KEYDOWN:
            return self._keydown(e)

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
            chart._needs_update = True

    def _keydown(self, e):
        if e.key == pygame.K_ESCAPE:
            if self.ei != -1:
                self.ei = -1
                self.edit_type = None
                self.edit_field = None
            else:
                self.deactivate()
            return True

        if e.key == pygame.K_RETURN:
            if self.ei != -1 and self.edit_field:
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
                self.edit_field = None
                return True
            return False

        if e.key in (pygame.K_UP, pygame.K_DOWN) and self.si != -1 and self._filtered_indices:
            try:
                cur = self._filtered_indices.index(self.si)
                delta = -1 if e.key == pygame.K_UP else 1
                nxt = (cur + delta) % len(self._filtered_indices)
                np_page = nxt // self.rows_per_page
                if np_page != self.current_page:
                    self.current_page = np_page
                self.si = self._filtered_indices[nxt]
            except ValueError:
                pass
            return True

        if e.key == pygame.K_LEFT:
            if self.current_page > 0:
                self.current_page -= 1
                s = self.get_page_start()
                self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
            return True

        if e.key == pygame.K_RIGHT:
            if self.current_page < self.get_total_pages() - 1:
                self.current_page += 1
                s = self.get_page_start()
                self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
            return True

        return False

    def _apply_edit(self):
        ty = self.sim.tys[self.ei]
        new_text = self.edit_field.get_text()
        if self.edit_type == 'name':
            ty.cust = new_text
        elif self.edit_type == 'number':
            ty.n = new_text
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
        self.edit_field = None
        self._row_cache.pop(self.ei, None)
        self._row_hashes.pop(self.ei, None)

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
                self.current_page = page - 1
                s = self.get_page_start()
                self.si = self._filtered_indices[s] if s < len(self._filtered_indices) else -1
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
        lx, ly, lw, lh = self.bg_rect
        self.draw_background(surface, self.bg_rect)
        self.draw_title(surface, self.title, self.bg_rect, y_offset=15)

        self.search_field.rect = self._search_rect()
        self.search_field.draw(surface)

        start = self.get_page_start()
        end = min(start + self.rows_per_page, len(self._filtered_indices))
        for i in range(start, end):
            row = i - start
            y = ly + TY_LIST_TOP_OFFSET + row * TY_LIST_ITEM_HEIGHT + 20
            oi = self._filtered_indices[i]
            ty = self.sim.tys[oi]

            if oi == self.si:
                hl = pygame.Surface((lw - 40, 66), pygame.SRCALPHA)
                hl.fill(LIST_HL)
                surface.blit(hl, (lx + 20, y + 2))

            if self.ei == oi and self.edit_type == 'name':
                if self.edit_field is None:
                    self.edit_field = InputField((lx + 30, y + 5, 300, 25), max_length=30)
                    self.edit_field.set_text(ty.cust or "")
                    self.edit_field.activate()
            else:
                old_hash = self._row_hashes.get(oi)
                new_hash = self._get_row_hash(ty)
                if old_hash is None or old_hash != new_hash:
                    self._row_cache[oi] = self._render_row(ty)
                    self._row_hashes[oi] = new_hash
                surface.blit(self._row_cache[oi]['name'], (lx + 30, y + 5))
                surface.blit(self._row_cache[oi]['info'], (lx + 30, y + 35))

            if self.ei == oi and self.edit_type in ('number', 'filename'):
                if self.edit_field is None:
                    r = (lx + 30, y + 35, 200, 25)
                    self.edit_field = InputField(r, max_length=20)
                    if self.edit_type == 'number':
                        self.edit_field.set_text(ty.n)
                    else:
                        fname = os.path.basename(ty.filepath).replace('.txt', '') if ty.filepath else ""
                        self.edit_field.set_text(fname)
                    self.edit_field.activate()

        if self.edit_field:
            self.edit_field.draw(surface)

        page_info = f"第 {self.current_page + 1}/{self.get_total_pages()} 页  共 {len(self._filtered_indices)} 条"
        surface.blit(rt(f_s, page_info, TXT), (lx + 20, ly + lh - 80))

        for action, rect in self._action_buttons().items():
            texts = {'name': self.name_btn_text, 'number': self.number_btn_text,
                     'filename': self.filename_btn_text}
            self.draw_button(surface, rect, texts[action], BUTTON_BG)

        pl = self._page_left_btn()
        pr = self._page_right_btn()
        pygame.draw.polygon(surface, TXT,
            [(pl.right, pl.top), (pl.right, pl.bottom), (pl.left + 4, pl.centery)])
        pygame.draw.polygon(surface, TXT,
            [(pr.left, pr.top), (pr.left, pr.bottom), (pr.right - 4, pr.centery)])

        self.draw_button(surface, self._jump_btn(), self.jump_text, BUTTON_BG)

        if self.jump_active:
            ov = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 100))
            surface.blit(ov, (0, 0))
            ir = pygame.Rect(self.sim.screen_width // 2 - 100, self.sim.screen_height // 2 - 30, 200, 40)
            pygame.draw.rect(surface, (255, 255, 255), ir)
            pygame.draw.rect(surface, BUTTON_BORDER, ir, 2)
            prompt = rt(f_s, f"输入页码 (1-{self.get_total_pages()}):", TXT)
            surface.blit(prompt, (ir.x, ir.y - 25))
            it = rt(f_s, self.jump_input + ("_" if pygame.time.get_ticks() % 1000 < 500 else ""), TXT)
            surface.blit(it, (ir.x + 5, ir.y + 10))
            cb = pygame.Rect(ir.x + 20, ir.y + 50, 60, 30)
            ca = pygame.Rect(ir.x + 120, ir.y + 50, 60, 30)
            self.draw_button(surface, cb, self.confirm_text, BUTTON_BORDER)
            self.draw_button(surface, ca, self.cancel_text, BUTTON_DISABLED)
            self.jump_confirm_btn = cb
            self.jump_cancel_btn = ca

        ct = pygame.time.get_ticks()
        if self.hi != -1 and ct - self.hst > 1500:
            self._draw_tooltip(surface)

    def _draw_tooltip(self, surface):
        if self.hi < 0 or self.hi >= len(self.sim.tys):
            return
        ty = self.sim.tys[self.hi]
        if not ty.pts:
            return
        mx, my = pygame.mouse.get_pos()
        tw, th = 280, 180
        tx = min(mx + 20, self.sim.screen_width - tw - 10)
        ty2 = min(my + 20, self.sim.screen_height - th - 10)
        ts = pygame.Surface((tw, th), pygame.SRCALPHA)
        ts.fill((255, 255, 255, 230))
        pygame.draw.rect(ts, BUTTON_BORDER, (0, 0, tw, th), 2, 8)

        dn = self.sim.get_display_name(ty)
        ts.blit(rt(f_m, f"台风: {dn}", TXT), (10, 10))
        ts.blit(rt(f_s, f"总点数: {len(ty.pts)}", TXT), (10, 40))
        fp, lp = ty.pts[0], ty.pts[-1]
        ts.blit(rt(f_s, f"起点: {fp['la']:.1f}°N, {fp['lo']:.1f}°E", TXT), (10, 90))
        ts.blit(rt(f_s, f"终点: {lp['la']:.1f}°N, {lp['lo']:.1f}°E", TXT), (10, 110))
        vw = [p['w'] for p in ty.pts if p['st'].upper() not in ('MD', 'SS', 'SD', 'EX', 'LO')]
        if vw:
            mw = max(vw)
            mc = self.sim.gsc(mw, "")
        else:
            mw, mc = 0, "N/A"
        ts.blit(rt(f_s, f"最大强度: {mc} ({mw}kt)", TXT), (10, 140))
        surface.blit(ts, (tx, ty2))