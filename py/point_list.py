# py/point_list.py
"""报点列表对话框。"""
from __future__ import annotations

import pygame
import time
from datetime import datetime
from .constants import (
    f_s, f_m, rt, TXT, LIST_HL, BUTTON_BORDER, BUTTON_DISABLED,
    POINT_LIST_ROWS_PER_PAGE, POINT_LIST_WIDTH, POINT_LIST_ROW_HEIGHT, POINT_LIST_HEADER_Y,
    DIALOG_TITLE_BAR_HEIGHT,
)
from .input_field import InputField
from .dialog_base import DraggableDialog
from .typhoon import TrackPoint
from .utils import lon_to_display, lat_to_display


class PointList(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.selected_index = -1
        self.current_page = 0
        self.rows_per_page = POINT_LIST_ROWS_PER_PAGE
        self.jump_active = False
        self.jump_field = None
        self._needs_save = False
        self.readonly = False
        self.typhoon = None
        self.last_click_time = 0
        self.last_click_index = -1

        self.headers = ["时间", "纬度", "经度", "强度", "气压", "类型", "正式报"]
        self.col_widths = [180, 80, 80, 50, 50, 60, 60]
        self.header_surfs = [rt(f_s, h, TXT) for h in self.headers]

        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._row_cache = {}
        self._row_hashes = {}

    # ── 激活/关闭 ──

    def activate(self, typhoon=None, readonly=False):
        super().activate()
        self.typhoon = typhoon or self.sim.edit_typhoon
        self.readonly = readonly if typhoon is not None else (self.sim.md != "edit")
        key = f"point_list_{id(self.typhoon)}"
        self.current_page = self.sim.dialog_page_cache.get(key, 0)
        self.selected_index = -1
        self.jump_active = False
        self.jump_field = None
        self._needs_save = False
        self._clear_row_cache()
        self._update_bg_rect()
        self.last_click_time = 0
        self.last_click_index = -1

    def deactivate(self):
        if self.typhoon:
            self.sim.dialog_page_cache[f"point_list_{id(self.typhoon)}"] = self.current_page
        if not self.readonly and self._needs_save:
            self._save(self.typhoon)
        super().deactivate()
        self.dragging = False

    # ── 行数据 ──

    def _clear_row_cache(self):
        self._row_cache.clear()
        self._row_hashes.clear()

    def _get_row_data(self, pt: TrackPoint):
        """返回 (hash_str, {header: surface})。"""
        cols = [
            pt['t'], lat_to_display(pt['la']), lon_to_display(pt['lo']),
            str(pt['w']), str(pt['p']) if pt['p'] else '', pt['st'],
            "是" if pt.get('official', True) else "否",
        ]
        return "|".join(cols), {h: rt(f_s, cols[i], TXT) for i, h in enumerate(self.headers)}

    # ── 布局 ──

    def _update_bg_rect(self):
        w = POINT_LIST_WIDTH
        h = POINT_LIST_ROW_HEIGHT * self.rows_per_page + 150
        self.bg_rect = pygame.Rect(
            (self.sim.screen_width - w) // 2, (self.sim.screen_height - h) // 2, w, h)

    def _action_buttons(self):
        bx, by, bw, _ = self.bg_rect
        btn_w, btn_h, gap = 80, 30, 10
        start = bx + (bw - (7 * btn_w + 6 * gap)) // 2
        y = by + self.bg_rect.height - 45
        labels = ['undo', 'redo', 'edit', 'delete', 'insert_before', 'insert_after', 'append']
        return {label: pygame.Rect(start + i * (btn_w + gap), y, btn_w, btn_h)
                for i, label in enumerate(labels)}

    def _jump_btn(self):
        return pygame.Rect(self.bg_rect.right - 120, self.bg_rect.bottom - 40, 80, 25)

    def get_total_pages(self):
        return max(1, (len(self.typhoon.pts) + self.rows_per_page - 1) // self.rows_per_page) \
            if self.typhoon else 1

    def get_page_start(self):
        return self.current_page * self.rows_per_page

    # ── 事件 ──

    def handle_event(self, e):
        if not self.active:
            return False
        if not self.typhoon:
            self.deactivate()
            return False
        if self.handle_drag_event(e):
            return True
        if self.jump_active:
            return self._jump_event(e)

        if e.type == pygame.MOUSEWHEEL and self.bg_rect.collidepoint(pygame.mouse.get_pos()):
            delta = -1 if e.y > 0 else 1
            new_page = self.current_page + delta
            if 0 <= new_page < self.get_total_pages():
                self.current_page = new_page
                self.selected_index = self.get_page_start()
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.dragging:
                return True
            if self._jump_btn().collidepoint(e.pos):
                self.jump_active = True
                return True
            if not self.readonly:
                for action, rect in self._action_buttons().items():
                    if rect.collidepoint(e.pos):
                        self._handle_action(action)
                        return True
            if self.bg_rect.collidepoint(e.pos):
                row = (e.pos[1] - self.bg_rect.y - 75) // POINT_LIST_ROW_HEIGHT
                idx = self.get_page_start() + row
                if 0 <= row < self.rows_per_page and idx < len(self.typhoon.pts):
                    self.selected_index = idx
                    now = time.time()
                    if idx == self.last_click_index and now - self.last_click_time < 0.3 and not self.readonly:
                        self._edit_point(idx)
                    self.last_click_time, self.last_click_index = now, idx
                return True

        if e.type == pygame.KEYDOWN:
            return self._keydown(e)
        return False

    def _keydown(self, e):
        if e.key == pygame.K_ESCAPE:
            self.deactivate()
            return True
        if e.key in (pygame.K_z, pygame.K_y) and (e.mod & pygame.KMOD_CTRL) and not self.readonly:
            return self._history_op('undo' if e.key == pygame.K_z else 'redo')
        if e.key == pygame.K_UP and self.selected_index > 0:
            self._move_cursor(-1)
            return True
        if e.key == pygame.K_DOWN and self.selected_index < len(self.typhoon.pts) - 1:
            self._move_cursor(1)
            return True
        if e.key == pygame.K_LEFT and self.current_page > 0:
            self.current_page -= 1
            self.selected_index = self.get_page_start()
            return True
        if e.key == pygame.K_RIGHT and self.current_page < self.get_total_pages() - 1:
            self.current_page += 1
            self.selected_index = self.get_page_start()
            return True
        if e.key == pygame.K_DELETE and self.selected_index >= 0 and not self.readonly:
            self._delete_point()
            return True
        if e.key == pygame.K_RETURN and self.selected_index >= 0 and not self.readonly:
            self._edit_point(self.selected_index)
            return True
        return False

    def _move_cursor(self, delta):
        new_idx = self.selected_index + delta
        if 0 <= new_idx < len(self.typhoon.pts):
            new_page = new_idx // self.rows_per_page
            if new_page != self.current_page:
                self.current_page = new_page
            self.selected_index = new_idx

    # ── 跳页 ──

    def _jump_event(self, e):
        if self.jump_field is None:
            r = pygame.Rect(self.bg_rect.centerx - 100, self.bg_rect.centery - 20, 200, 40)
            self.jump_field = InputField(r, max_length=3, validator=str.isdigit)
            self.jump_field.activate()
        elif self.jump_field.handle_event(e):
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.jump_active = False
                self.jump_field = None
            elif e.key == pygame.K_RETURN:
                self._do_jump()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            if self.jump_confirm_btn.collidepoint(x, y) if hasattr(self, 'jump_confirm_btn') else False:
                self._do_jump()
            elif self.jump_cancel_btn.collidepoint(x, y) if hasattr(self, 'jump_cancel_btn') else False:
                self.jump_active = False
                self.jump_field = None
            return True
        return True

    def _do_jump(self):
        if not self.jump_field:
            return
        try:
            page = int(self.jump_field.get_text())
            if 1 <= page <= self.get_total_pages():
                self.current_page = page - 1
                self.selected_index = self.get_page_start()
                self.jump_active = False
                self.jump_field = None
            else:
                self.jump_field.set_text("")
        except ValueError:
            self.jump_field.set_text("")
            self.jump_active = False
            self.jump_field = None

    # ── 操作 ──

    def _handle_action(self, action):
        if self.readonly:
            return
        ops = {'undo': self._history_op, 'redo': self._history_op}
        if action in ops:
            return ops[action](action)
        if action == 'edit' and self.selected_index >= 0:
            self._edit_point(self.selected_index)
        elif action == 'delete' and self.selected_index >= 0:
            self._delete_point()
        elif action == 'insert_before' and self.selected_index >= 0:
            self._insert_point(self.selected_index, before=True)
        elif action == 'insert_after' and self.selected_index >= 0:
            self._insert_point(self.selected_index, before=False)
        elif action == 'append':
            self._insert_point(len(self.typhoon.pts), before=False)

    def _history_op(self, action):
        if not self.typhoon:
            return False
        if getattr(self.typhoon, action)():
            self._clear_row_cache()
            self.typhoon.update_screen_points(self.sim.latlon_to_screen)
            self._needs_save = True
            self.sim._refresh_ace_data()
        return True

    # ── 编辑/删除/插入 ──

    def _edit_point(self, idx):
        pt = self.typhoon.pts[idx]
        init = {'wind': str(pt['w']), 'pressure': str(pt['p']) if pt['p'] else '',
                'type': pt['st'], 'lat': f"{pt['la']:.1f}", 'lon': f"{pt['lo']:.1f}",
                'time': pt['t']}
        self.sim.dialog_mgr.point_edit_dialog.activate(init, lambda v: self._update_point(idx, v))

    def _update_point(self, idx, vals):
        try:
            self._apply_point_change(idx, vals, is_new=False)
        except (ValueError, Exception) as e:
            self.typhoon.undo()
            self.sim.show_error(f"编辑点出错: {e}")

    def _delete_point(self):
        if self.selected_index < 0:
            return
        self.typhoon.push_snapshot()
        self._clear_row_cache()
        del self.typhoon.pts[self.selected_index]
        if self.selected_index >= len(self.typhoon.pts):
            self.selected_index = len(self.typhoon.pts) - 1
        self.current_page = min(self.current_page, self.get_total_pages() - 1)
        self._after_point_change()

    def _insert_point(self, idx, before):
        name = self.typhoon.pts[-1]['name'] if self.typhoon.pts else (self.typhoon.sname or "")
        init = {'wind': '', 'pressure': '', 'type': '', 'lat': '', 'lon': '',
                'time': self.sim.get_next_time_for_typhoon(self.typhoon)}
        self.sim.dialog_mgr.point_edit_dialog.activate(
            init, lambda v: self._add_point(idx, before, v, name))

    def _add_point(self, idx, before, vals, name):
        try:
            self._apply_point_change(idx, vals, is_new=True, before=before, name=name)
        except (ValueError, Exception) as e:
            self.typhoon.undo()
            self.sim.show_error(f"插入点出错: {e}")

    def _apply_point_change(self, idx, vals, is_new, before=True, name=""):
        w = int(vals['wind']) if vals['wind'] else 15
        p = int(vals['pressure']) if vals['pressure'] else 0
        st = vals['type'] if vals['type'] else self._infer_type(w, self.typhoon.basin if self.typhoon else None)
        la, lo = float(vals['lat']), float(vals['lon'])
        t = vals['time']

        ace_year = 0
        if len(t) >= 10:
            try:
                ace_year = self.sim.get_ace_year(datetime.strptime(t[:10], "%Y%m%d%H"))
            except (ValueError, Exception):
                pass

        if not is_new:
            self.typhoon.push_snapshot()
            name = self.typhoon.pts[idx].get('name', '')
            self.typhoon.pts[idx].update(
                {'w': w, 'p': p, 'st': st, 'la': la, 'lo': lo, 't': t, 'name': name, 'ace_year': ace_year})
            self.typhoon.pts[idx]['color'] = self.sim.get_point_color(w, st)
            self.typhoon.pts[idx]['color_dim'] = self.sim.darken_color(self.typhoon.pts[idx]['color'], 0.6)
        else:
            self.typhoon.push_snapshot()
            if not self.typhoon.pts and la < 0:
                self.typhoon.mirror = True
                self.typhoon.rot_dir = -1
            if not self.typhoon.pts and self.sim.res_mgr.ocean_areas.areas:
                area = self.sim.res_mgr.ocean_areas.find_area(la, lo)
                if area:
                    self.typhoon.basin = area.code
            pt = TrackPoint(t=t, la=la, lo=lo, w=w, p=p, st=st, name=name,
                            official=True, ace=0, pace=0, ace_year=ace_year,
                            color=self.sim.get_point_color(w, st))
            pt.color_dim = self.sim.darken_color(pt.color, 0.6)
            if before:
                self.typhoon.pts.insert(idx, pt)
            elif idx >= len(self.typhoon.pts):
                self.typhoon.pts.append(pt)
            else:
                self.typhoon.pts.insert(idx + 1, pt)

        self._after_point_change()

    def _after_point_change(self):
        self.typhoon.recalc_ace()
        self.typhoon.recalc_simulated_times()
        self._clear_row_cache()
        self.typhoon.update_screen_points(self.sim.latlon_to_screen)
        self._needs_save = True
        self.sim._refresh_ace_data()

    @staticmethod
    def _infer_type(wind, basin=None):
        if wind < 24: return "DB"
        if wind < 34: return "TD"
        if wind < 64: return "TS"
        if basin and basin.upper() == "AL":
            return "HU"
        if wind < 130: return "TY"
        return "ST"

    # ── 保存 ──

    def save_typhoon_to_file(self, ty):
        """保存台风到文件（公共接口）。"""
        self._save(ty)

    def _save(self, ty):
        if not ty.filepath:
            return
        self.sim.repo.ensure_simple_bdeck_copy(ty)
        lines = []
        for pt in ty.pts:
            basin = ty.basin or "WP"
            lat = abs(pt['la'])
            lat_int = int(round(lat * 10))
            lat_dir = 'N' if pt['la'] >= 0 else 'S'
            lon = pt['lo']
            if lon > 180.0:
                lon_int, lon_dir = int(round((360.0 - lon) * 10)), 'W'
            else:
                lon_int, lon_dir = int(round(lon * 10)), 'E'
            lat_field = f"{lat_int:>3d}{lat_dir}".rjust(5)
            lon_field = f"{lon_int:>4d}{lon_dir}".rjust(6)
            wind = f"{pt['w']:>4d}"
            pressure = f"{pt['p']:>5d}" if pt['p'] else "    0"
            lines.append(
                f"{basin}, {ty.n},{pt['t']},   ,chunshu,   0,"
                f"{lat_field},{lon_field},{wind},{pressure}, {pt['st']},    {pt.get('name', '')}")
        try:
            with open(ty.filepath, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))
            self._needs_save = False
        except (IOError, OSError) as e:
            self.sim.show_error(f"保存文件失败: {e}")

    # ── 绘制 ──

    def draw(self, surface):
        if not self.active or not self.typhoon:
            if not self.typhoon and self.active:
                self.deactivate()
            return

        lx, ly, lw, lh = self.bg_rect
        self.draw_background(surface, self.bg_rect)
        surface.blit(rt(f_m, f"报点列表 - {self.sim.get_display_name(self.typhoon)}", TXT), (lx + 20, ly + 15))

        hx = lx + 20
        for i, surf in enumerate(self.header_surfs):
            surface.blit(surf, (hx, ly + POINT_LIST_HEADER_Y))
            hx += self.col_widths[i]

        start = self.get_page_start()
        end = min(start + self.rows_per_page, len(self.typhoon.pts))
        for i in range(start, end):
            row = i - start
            y = ly + 75 + row * POINT_LIST_ROW_HEIGHT
            if i == self.selected_index:
                pygame.draw.rect(surface, LIST_HL, (lx + 5, y - 2, lw - 10, 28))
            pt = self.typhoon.pts[i]
            dh = self._get_row_data(pt)[0]
            h = self._row_hashes.get(i)
            if h is None or h != dh:
                self._row_cache[i] = self._get_row_data(pt)[1]
                self._row_hashes[i] = dh
            cx = lx + 20
            for j, header in enumerate(self.headers):
                surface.blit(self._row_cache[i][header], (cx, y))
                cx += self.col_widths[j]

        page_info = f"第 {self.current_page + 1}/{self.get_total_pages()} 页  共 {len(self.typhoon.pts)} 条"
        surface.blit(rt(f_s, page_info, TXT), (lx + 20, ly + lh - 30))

        self.draw_button(surface, self._jump_btn(), rt(f_s, "跳页", (255, 255, 255)), BUTTON_BORDER)
        if not self.readonly:
            btns = self._action_buttons()
            texts = {'undo': rt(f_s, "撤销", (255, 255, 255)), 'redo': rt(f_s, "重做", (255, 255, 255)),
                     'edit': rt(f_s, "编辑", (255, 255, 255)), 'delete': rt(f_s, "删除", (255, 255, 255)),
                     'insert_before': rt(f_s, "前插", (255, 255, 255)),
                     'insert_after': rt(f_s, "后插", (255, 255, 255)),
                     'append': rt(f_s, "新增", (255, 255, 255))}
            for label, rect in btns.items():
                self.draw_button(surface, rect, texts[label], BUTTON_BORDER)

        if self.jump_active and self.jump_field:
            ov = pygame.Surface((lw + 40, lh + 40), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 100))
            surface.blit(ov, (lx - 20, ly - 20))
            self.jump_field.draw(surface)
            surface.blit(rt(f_s, f"输入页码 (1-{self.get_total_pages()}):", TXT),
                         (self.jump_field.rect.x, self.jump_field.rect.y - 25))
            confirm = pygame.Rect(self.jump_field.rect.x + 20, self.jump_field.rect.y + 50, 60, 30)
            cancel = pygame.Rect(self.jump_field.rect.x + 120, self.jump_field.rect.y + 50, 60, 30)
            self.draw_button(surface, confirm, rt(f_s, "确认", (255, 255, 255)), BUTTON_BORDER)
            self.draw_button(surface, cancel, rt(f_s, "取消", (255, 255, 255)), BUTTON_DISABLED)
            self.jump_confirm_btn, self.jump_cancel_btn = confirm, cancel