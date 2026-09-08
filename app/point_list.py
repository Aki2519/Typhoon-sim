# py/point_list.py
"""报点列表对话框。"""
from __future__ import annotations

import os
import pygame
import time
from datetime import datetime, timedelta
from .constants import (
    f_s, f_m, rt, TXT, LIST_HL, BUTTON_BORDER, BUTTON_DISABLED,
    POINT_LIST_ROWS_PER_PAGE, POINT_LIST_WIDTH, POINT_LIST_ROW_HEIGHT, POINT_LIST_HEADER_Y,
    DIALOG_TITLE_BAR_HEIGHT,
    SETTINGS_TEXT_LIGHT, SETTINGS_TEXT_DIM,
    MODE_EDIT,
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

        # 不显示"正式报"列(也不显示 JTWC 评);非正式报仅以整行变暗区分
        self.headers = ["#", "时间", "纬度", "经度", "强度", "气压", "类型"]
        self.col_widths = [40, 140, 80, 80, 50, 50, 60]
        tc_d = SETTINGS_TEXT_LIGHT
        tc_l = TXT
        self.header_surfs = [rt(f_s, h, tc_l) for h in self.headers]
        self.header_surfs_dark = [rt(f_s, h, tc_d) for h in self.headers]

        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT
        self._row_cache = {}
        self._row_hashes = {}
        self._idx_cache = {}

        # 按钮文字缓存（不再每帧 rt()）
        W = (255, 255, 255)

        self._key_held_timer = 0
        self._key_held_key = 0
        self._KEY_REPEAT_DELAY = 500
        self._KEY_REPEAT_INTERVAL = 120

        self._btn_texts = {
            'undo': rt(f_s, "撤销", W), 'redo': rt(f_s, "重做", W),
            'edit': rt(f_s, "编辑", W), 'delete': rt(f_s, "删除", W),
            'insert_before': rt(f_s, "前插", W),
            'insert_after': rt(f_s, "后插", W),
            'append': rt(f_s, "新增", W),
        }
        self._jump_text = rt(f_s, "跳页", W)
        self._confirm_text = rt(f_s, "确认", W)
        self._cancel_text = rt(f_s, "取消", W)

    # ── 激活/关闭 ──

    def activate(self, typhoon=None, readonly=False):
        super().activate()
        self.typhoon = typhoon or self.sim.edit_typhoon
        self.readonly = readonly if typhoon is not None else (self.sim.md != "edit")
        key = f"point_list_{id(self.typhoon)}"
        self.current_page = min(self.sim.dialog_page_cache.get(key, 0),
                                self.get_total_pages() - 1)
        self.selected_index = -1
        self.jump_active = False
        self.jump_field = None
        self._needs_save = False
        self._clear_row_cache()
        self._update_bg_rect()
        self.last_click_time = 0
        self.last_click_index = -1
        self._key_held_key = 0
        self._lr_tick = 0
        self._key_held_timer = 0

    def deactivate(self):
        if self.typhoon:
            self.sim.dialog_page_cache[f"point_list_{id(self.typhoon)}"] = self.current_page
        if not self.readonly and self._needs_save:
            self._save(self.typhoon)
        if self.jump_field is not None:
            self.jump_field.deactivate()
            self.jump_field = None
        self.jump_active = False
        self._key_held_key = 0
        self._lr_tick = 0
        self._key_held_timer = 0
        super().deactivate()
        self.dragging = False

    # ── 行数据 ──

    def _clear_row_cache(self):
        self._row_cache.clear()
        self._row_hashes.clear()
        self._row_rev = getattr(self, '_row_rev', 0) + 1   # 法28: 修订 token
        self._row_rev_last = -1

    @staticmethod
    def _fmt_time(t):
        """YYYYMMDDHH[MM] → 'MM-DD HH:MM'(含分钟时显示真实分钟)。"""
        from .utils import fmt_short_time
        return fmt_short_time(t)

    def _row_hash(self, pt: TrackPoint, dark: bool = False) -> str:
        cols = [
            self._fmt_time(pt['t']), lat_to_display(pt['la']), lon_to_display(pt['lo']),
            str(pt['w']), str(pt['p']) if pt['p'] else '', pt['st'],
        ]
        # 非正式报仅整行变暗(不显示列), official 标志仍入哈希保证变暗样式同步
        return "|".join(cols) + f"|dark={dark}|o={1 if pt.get('official', True) else 0}"

    def _row_surfs(self, pt: TrackPoint, dark: bool = False):
        cols = [
            self._fmt_time(pt['t']), lat_to_display(pt['la']), lon_to_display(pt['lo']),
            str(pt['w']), str(pt['p']) if pt['p'] else '', pt['st'],
        ]
        # 非正式报(插值点)整行变暗(仅视觉区分,不单列显示是否正式报)
        if not pt.get('official', True):
            tc = SETTINGS_TEXT_DIM if dark else (150, 150, 150)
        else:
            tc = SETTINGS_TEXT_LIGHT if dark else TXT
        # 序号列单独绘制,不在此生成
        return {h: rt(f_s, cols[i], tc) for i, h in enumerate(self.headers[1:])}

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
                    self._sync_map_selection(idx)
                    now = time.time()
                    if idx == self.last_click_index and now - self.last_click_time < 0.3 and not self.readonly:
                        self._edit_point(idx)
                    self.last_click_time, self.last_click_index = now, idx
                return True

        if e.type == pygame.KEYDOWN:
            return self._keydown(e)
        if e.type == pygame.KEYUP and e.key in (pygame.K_LEFT, pygame.K_RIGHT):
            if self._key_held_key == e.key:
                self._key_held_key = 0
            return True
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
        if e.key in (pygame.K_LEFT, pygame.K_RIGHT):
            self._key_held_timer = pygame.time.get_ticks()
            self._key_held_key = e.key
            self._lr_tick = 0
            if e.key == pygame.K_LEFT:
                self.current_page = (self.current_page - 1) % self.get_total_pages()
            else:
                self.current_page = (self.current_page + 1) % self.get_total_pages()
            self.selected_index = self.get_page_start()
            return True
        if e.key == pygame.K_HOME:
            self.selected_index = 0
            self.current_page = 0
            return True
        if e.key == pygame.K_END:
            last = len(self.typhoon.pts) - 1
            self.selected_index = last
            self.current_page = max(0, last) // self.rows_per_page
            return True
        if e.key == pygame.K_DELETE and self.selected_index >= 0 and not self.readonly:
            self._delete_point()
            return True
        if e.key == pygame.K_RETURN and self.selected_index >= 0 and not self.readonly:
            self._edit_point(self.selected_index)
            return True
        if e.key in (pygame.K_COMMA, pygame.K_PERIOD):
            # , / .: 与编辑模式地图快捷键一致,切换选中报点(循环)
            n = len(self.typhoon.pts)
            if n:
                delta = -1 if e.key == pygame.K_COMMA else 1
                if self.selected_index < 0:
                    nxt = 0
                else:
                    nxt = (self.selected_index + delta) % n
                new_page = nxt // self.rows_per_page
                if new_page != self.current_page:
                    self.current_page = new_page
                self.selected_index = nxt
                self._sync_map_selection(nxt)
                return True
        return False

    def _move_cursor(self, delta):
        new_idx = self.selected_index + delta
        if 0 <= new_idx < len(self.typhoon.pts):
            new_page = new_idx // self.rows_per_page
            if new_page != self.current_page:
                self.current_page = new_page
            self.selected_index = new_idx
            self._sync_map_selection(new_idx)

    def _sync_map_selection(self, idx):
        """C9: 列表选中 ↔ 地图选中点高亮联动。"""
        if self.sim.md == self.sim.MODE_EDIT and self.typhoon is self.sim.edit_typhoon:
            self.sim._edit_selected_point = idx
            self.sim._last_edited_point = idx

    # ── 跳页 ──

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
            for _ in range(count):
                if self._key_held_key == pygame.K_LEFT:
                    self.current_page = (self.current_page - 1) % self.get_total_pages()
                else:
                    self.current_page = (self.current_page + 1) % self.get_total_pages()
                self.selected_index = self.get_page_start()
            self._lr_tick = expected

    def _jump_event(self, e):
        if self.jump_field is None:
            r = pygame.Rect(self.bg_rect.centerx - 100, self.bg_rect.centery - 20, 200, 40)
            self.jump_field = InputField(r, max_length=3, dark=self.dark_mode)
            self.jump_field.activate()
        if self.jump_field.handle_event(e):
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.jump_active = False
                if self.jump_field is not None:
                    self.jump_field.deactivate()
                    self.jump_field = None
            elif e.key == pygame.K_RETURN:
                self._do_jump()
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            confirm, cancel = self._jump_btns()
            if confirm.collidepoint(x, y):
                self._do_jump()
            elif cancel.collidepoint(x, y):
                self.jump_active = False
                if self.jump_field is not None:
                    self.jump_field.deactivate()
                    self.jump_field = None
            return True
        return True

    def _jump_btns(self):
        """跳页确认/取消按钮矩形(由输入框位置推导,与绘制共用)。"""
        if self.jump_field is None:
            return pygame.Rect(0, 0, 0, 0), pygame.Rect(0, 0, 0, 0)
        confirm = pygame.Rect(self.jump_field.rect.x + 20, self.jump_field.rect.y + 50, 60, 30)
        cancel = pygame.Rect(self.jump_field.rect.x + 120, self.jump_field.rect.y + 50, 60, 30)
        return confirm, cancel

    def _do_jump(self):
        if not self.jump_field:
            return
        if not self.jump_field.get_text().strip():
            return
        try:
            page = int(self.jump_field.get_text())
            if 1 <= page <= self.get_total_pages():
                self.current_page = page - 1
                self.selected_index = self.get_page_start()
                self.jump_active = False
                self.jump_field.deactivate()
                self.jump_field = None
            else:
                self.jump_field.set_text("")
        except ValueError:
            self.jump_field.set_text("")
            self.sim.show_error("请输入数字页码")

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
            self.sim.refresh_typhoon_after_point_change(self.typhoon)
            self.sim._refresh_ace_data(self.typhoon)
            self.sim._last_edited_point = None
            # undo/redo 可能增删点: 页码与选中索引越界校正(K14)
            self.current_page = min(self.current_page, self.get_total_pages() - 1)
            if self.selected_index >= len(self.typhoon.pts):
                self.selected_index = self.get_page_start()
        return True

    # ── 编辑/删除/插入 ──

    def _edit_point(self, idx):
        pt = self.typhoon.pts[idx]
        # 报点列表编辑: 完整字段集(洋区/编号/名字, 参考自评工具), 与编辑模式
        # 地图上的 6 字段编辑界面区分
        init = {'basin': self.typhoon.basin or '', 'number': self.typhoon.n or '',
                'name': pt.get('name', ''),
                'wind': str(pt['w']), 'pressure': str(pt['p']) if pt['p'] else '',
                'type': pt['st'], 'lat': f"{pt['la']:.1f}", 'lon': f"{pt['lo']:.1f}",
                'time': pt['t']}
        self.sim.dialog_mgr.point_edit_dialog.activate(
            init,
            lambda v: self._update_point(idx, v),
            point_nav=self._point_edit_nav,
            point_index=idx,
            point_total=len(self.typhoon.pts),
            field_set='full',
        )

    def _point_edit_nav(self, cur, delta):
        """编辑报点对话框按 , / . 切换报点时,提供相邻报点的初始值与提交回调。"""
        pts = self.typhoon.pts
        idx = cur + delta
        if not (0 <= idx < len(pts)):
            return None
        pt = pts[idx]
        init = {'basin': self.typhoon.basin or '', 'number': self.typhoon.n or '',
                'name': pt.get('name', ''),
                'wind': str(pt['w']), 'pressure': str(pt['p']) if pt['p'] else '',
                'type': pt['st'], 'lat': f"{pt['la']:.1f}", 'lon': f"{pt['lo']:.1f}",
                'time': pt['t']}
        cb = lambda v, ni=idx: self._update_point(ni, v)
        # 同步列表选中与地图选中,切换后继续 , / . 或关闭对话框都落在新报点上
        self.selected_index = idx
        self.current_page = idx // self.rows_per_page
        self._sync_map_selection(idx)
        return init, cb, idx

    def _update_point(self, idx, vals):
        # 返回是否成功: 失败时对话框保持打开,避免用户输入被静默丢弃
        try:
            _, new_idx = self._apply_point_change(idx, vals, is_new=False)
            self.sim._last_edited_point = new_idx
            if new_idx is not None and 0 <= new_idx < len(self.typhoon.pts):
                self.selected_index = new_idx
                self.current_page = new_idx // self.rows_per_page
            return True
        except Exception as e:
            self.sim.show_error(f"编辑点出错: {e}")
            return False

    def _delete_point(self):
        if self.selected_index < 0:
            return
        # A3: 统一走 sim 级删除(共享撤销/重投影/保存逻辑,并同步地图选中)
        if self.sim._delete_edit_point(self.selected_index, self.typhoon):
            self.sim._last_edited_point = None

    def _insert_point(self, idx, before):
        name = self.typhoon.pts[-1]['name'] if self.typhoon.pts else (self.typhoon.sname or "")
        init = {'wind': '', 'pressure': '', 'type': '', 'lat': '', 'lon': '',
                'time': self._default_time_for_insert(idx, before)}
        self.sim.dialog_mgr.point_edit_dialog.activate(
            init, lambda v: self._add_point(idx, before, v, name))

    def _default_time_for_insert(self, idx, before):
        """插入点的默认时间: 取插入位置两侧报点的中间时刻,使按时间排序后
        正好落在期望位置; 边界处取相邻点 ±6h; 无法解析时退回末尾+6h。"""
        pts = self.typhoon.pts
        n = len(pts)
        if not pts:
            return "2000010100"

        def _dt(s):
            try:
                if len(s) >= 12:
                    return datetime.strptime(s[:12], "%Y%m%d%H%M")
                return datetime.strptime(s[:10], "%Y%m%d%H")
            except (ValueError, TypeError):
                return None

        if before:
            a = pts[idx - 1] if idx > 0 else None
            b = pts[idx] if idx < n else None
        else:
            a = pts[idx] if idx < n else pts[n - 1]
            b = pts[idx + 1] if idx + 1 < n else None
        da = _dt(a['t']) if a else None
        db = _dt(b['t']) if b else None
        has_min = bool((a and len(a['t']) >= 12) or (b and len(b['t']) >= 12))
        fmt = "%Y%m%d%H%M" if has_min else "%Y%m%d%H"
        if da is not None and db is not None:
            mid = da + (db - da) / 2
            return mid.strftime(fmt)
        if da is not None and db is None:
            return (da + timedelta(hours=6)).strftime(fmt)
        if db is not None and da is None:
            return (db - timedelta(hours=6)).strftime(fmt)
        return self.sim.get_next_time_for_typhoon(self.typhoon)

    def _add_point(self, idx, before, vals, name):
        # 返回是否成功: 失败时对话框保持打开,避免用户输入被静默丢弃
        try:
            _, new_idx = self._apply_point_change(idx, vals, is_new=True, before=before, name=name)
            self.sim._last_edited_point = new_idx
            if new_idx is not None and 0 <= new_idx < len(self.typhoon.pts):
                self.selected_index = new_idx
                self.current_page = new_idx // self.rows_per_page
            return True
        except Exception as e:
            self.sim.show_error(f"插入点出错: {e}")
            return False

    def _apply_point_change(self, idx, vals, is_new, before=True, name=""):
        """应用点变更。返回 (True, new_idx)：new_idx 为变更后该点的索引
        (新增/时间修改可能按时间重排)。"""
        # 先无副作用地解析/校验输入(失败时直接抛出带具体信息的 ValueError);
        # 全部通过后才快照并应用。这样校验失败不会污染撤销/重做栈
        # (不残留未消费快照→"幽灵撤销",也不把未变更状态压进重做栈→"幽灵重做")。
        try:
            w = int(vals['wind']) if vals['wind'] else 15
        except (ValueError, TypeError):
            raise ValueError("强度必须是数字")
        if w < 0:
            raise ValueError("强度不能为负数")
        try:
            p = int(vals['pressure']) if vals['pressure'] else 0
        except (ValueError, TypeError):
            raise ValueError("气压必须是数字")
        if p < 0:
            raise ValueError("气压不能为负数")
        try:
            la, lo = float(vals['lat']), float(vals['lon'])
        except (ValueError, TypeError):
            raise ValueError("经纬度必须是数字")
        if not (-90 <= la <= 90):
            raise ValueError("纬度必须在 -90 到 90 之间")
        if not (0 <= lo <= 360):
            raise ValueError("经度必须在 0 到 360 之间")
        st = (vals['type'] or '').strip() or self._infer_type(w, self.typhoon.basin if self.typhoon else None)
        t = vals['time']

        ace_year = 0
        if len(t) >= 10:
            try:
                ace_year = self.sim.get_ace_year(datetime.strptime(t[:10], "%Y%m%d%H"))
            except Exception:
                pass

        # 台风级字段(报点列表完整编辑界面, 参考自评工具):
        # 洋区/编号属于台风身份, 不进入点级撤销栈(与台风列表改名一致);
        # 名字为点级字段, 走下方 update 路径(可撤销)。
        if 'basin' in vals or 'number' in vals:
            old_key = f"{self.typhoon.b}{self.typhoon.n}"
            b = (vals.get('basin') or '').strip().upper()
            n = (vals.get('number') or '').strip()
            if b and b != self.typhoon.basin:
                self.typhoon.basin = b
            if n and n != self.typhoon.n:
                self.typhoon.n = n
                self.typhoon.name = f"{self.typhoon.b}{n}"
            if (b or n) and old_key != f"{self.typhoon.b}{self.typhoon.n}":
                # 自定义名称键随编号迁移(与台风列表编辑编号一致)
                tn = getattr(getattr(self.sim, 'cfg', None), 'tn', None)
                if tn is not None:
                    new_key = f"{self.typhoon.b}{self.typhoon.n}"
                    if old_key in tn:
                        tn[new_key] = tn.pop(old_key)
                    elif self.typhoon.cust:
                        tn[new_key] = self.typhoon.cust
                self.sim.save_config()

        # 校验通过,此时才快照(推入撤销栈并清空重做栈)
        self.typhoon.push_snapshot()
        try:
            # 与 utils_mixin 地图路(add/update_point_in_edit_typhoon)口径一致:
            # 每次变更加强度/性质都同步重算 cat,否则对话框改点后报点 cat 残留
            # 旧类别,直接用 .get('cat') 的消费方(SMCY 预开流等)显示错类别
            cat = self.sim.get_strength_category(w, st)
            if not is_new:
                if 'name' in vals and vals.get('name') is not None:
                    name = str(vals['name'])
                else:
                    name = self.typhoon.pts[idx].get('name', '')
                self.typhoon.pts[idx].update(
                    {'w': w, 'p': p, 'st': st, 'la': la, 'lo': lo, 't': t, 'name': name, 'ace_year': ace_year})
                self.typhoon.pts[idx]['color'] = self.sim.get_point_color(w, st)
                self.typhoon.pts[idx]['color_dim'] = self.sim.darken_color(self.typhoon.pts[idx]['color'], 0.6)
                self.typhoon.pts[idx]['cat'] = cat
                # 时间被修改后按时间重排,保证路径按时间连接
                new_idx = self.typhoon.resort_point_by_time(idx)
            else:
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
                pt.cat = cat
                # 统一按时间顺序插入,路径按时间连接(无论输入时间在何时)
                new_idx = self.typhoon.insert_point_by_time(pt)

            self._after_point_change()
            return True, new_idx
        except Exception:
            self.typhoon.undo()
            raise

    def _after_point_change(self):
        self.typhoon.recalc_ace()
        self.typhoon.recalc_simulated_times()
        self._clear_row_cache()
        # N7: 先清 geo 样条缓存,再重投影(就地修改点时不复用旧样条)
        from .typhoon_render import _clear_geo_spline_cache
        _clear_geo_spline_cache(self.typhoon)
        self.typhoon.update_screen_points(self.sim.latlon_to_screen)
        self.sim.refresh_typhoon_after_point_change(self.typhoon)
        self._needs_save = True
        self.sim._refresh_ace_data(self.typhoon)
        # 缓存失效链与 utils_mixin 地图路一致: 时间/性质被改后必须同步失效
        # 季节起始缓存(否则旧 pts[0]['t'] 派生起激活时刻残留)与季节信息框缓存
        self.sim._drop_season_start_cache(self.typhoon)
        for _c in ('_season_info_box_cache', '_season_info_box_last_data'):
            getattr(self.sim, _c, {}).pop(self.typhoon, None)

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

    def save_typhoon_to_file(self, ty, silent: bool = False):
        """保存台风到文件（公共接口）。

        silent=True 时静默保存(拖动报点等高频路径), 不弹"已保存"提示。"""
        self._save(ty, silent=silent)

    def _save(self, ty, silent: bool = False):
        if not ty.filepath:
            self.sim.show_error("台风无文件路径,无法保存")
            return
        if not self.sim.repo.ensure_simple_bdeck_copy(ty):
            # 转换失败时 ty.filepath 仍指向原始 JTWC 文件: 继续写会覆盖原始数据
            self.sim.show_error("保存失败: JTWC 转换失败, 已放弃保存以保护原始数据")
            return
        lines = []
        if not ty.pts:
            # 空 pts: 重写占位注释头(否则重载后台风丢失,R2-20)
            lines.append(f"# NEW {ty.basin or 'WP'} {ty.n} "
                         f"{ty.cust or ty.sname or ''}".rstrip())
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
            # 分钟字段(时间串 12 位时取 10-12 位,否则留空)
            minutes = pt['t'][10:12] if len(pt['t']) >= 12 else ''
            lines.append(
                f"{basin}, {ty.n},{pt['t']},{minutes:>3s},chunshu,   0,"
                f"{lat_field},{lon_field},{wind},{pressure}, {pt['st']},    {pt.get('name', '')}")
        tmp = ty.filepath + '.tmp'
        try:
            # 原子写: 直接 open(...,'w') 在写盘失败时会截断原文件
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))
            os.replace(tmp, ty.filepath)
            self._needs_save = False
            if not silent:
                # 编辑模式: 保存是高频自动行为(撤销/重做/拖点/改点), 不弹打扰
                if getattr(self.sim, 'md', None) != MODE_EDIT:
                    self.sim.show_toast(f"已保存 {os.path.basename(ty.filepath)}", 'success')
        except (IOError, OSError) as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            self.sim.show_error(f"保存文件失败: {e}")

    # ── 绘制 ──

    def draw(self, surface):
        if not self.active or not self.typhoon:
            if not self.typhoon and self.active:
                self.deactivate()
            return

        self._handle_key_repeat()
        dark = self.dark_mode
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        hl_color = (65, 90, 140, 130) if dark else LIST_HL
        lx, ly, lw, lh = self.bg_rect

        if dark:
            self.draw_dark_panel(surface, self.bg_rect)
        else:
            self.draw_background(surface, self.bg_rect)
        surface.blit(rt(f_m, f"报点列表 - {self.sim.get_display_name(self.typhoon)}", tc),
                     (lx + 20, ly + 15))

        headers = self.header_surfs_dark if dark else self.header_surfs
        hx = lx + 20
        for i, surf in enumerate(headers):
            surface.blit(surf, (hx, ly + POINT_LIST_HEADER_Y))
            hx += self.col_widths[i]

        start = self.get_page_start()
        end = min(start + self.rows_per_page, len(self.typhoon.pts))
        rev = getattr(self, '_row_rev', 0)
        rev_changed = rev != getattr(self, '_row_rev_last', -1)
        if rev_changed:
            self._row_rev_last = rev
        for i in range(start, end):
            row = i - start
            y = ly + 75 + row * POINT_LIST_ROW_HEIGHT
            if i == self.selected_index:
                pygame.draw.rect(surface, hl_color, (lx + 5, y - 2, lw - 10, 28))
            pt = self.typhoon.pts[i]
            # 翻页/跳页未清缓存时,新页行缺失会 KeyError: 缺失即重建
            if rev_changed or i not in self._row_cache:
                dh = self._row_hash(pt, dark)
                h = self._row_hashes.get(i)
                if h is None or h != dh or i not in self._row_cache:
                    self._row_cache[i] = self._row_surfs(pt, dark)
                    self._row_hashes[i] = dh
            cx = lx + 20
            # 序号列单独绘制(行位置,不进缓存;表面按 (序号,主题) 缓存)
            idx_key = (i + 1, dark)
            idx_surf = self._idx_cache.get(idx_key)
            if idx_surf is None:
                idx_surf = rt(f_s, str(i + 1), tc)
                if len(self._idx_cache) > 64:
                    self._idx_cache.pop(next(iter(self._idx_cache)))
                self._idx_cache[idx_key] = idx_surf
            surface.blit(idx_surf, (cx, y))
            cx += self.col_widths[0]
            for j, header in enumerate(self.headers[1:]):
                surface.blit(self._row_cache[i][header], (cx, y))
                cx += self.col_widths[j + 1]

        page_info = f"第 {self.current_page + 1}/{self.get_total_pages()} 页  共 {len(self.typhoon.pts)} 条"
        # 页脚置于操作按钮行上方,避免被第一个按钮遮挡
        btn_y = self.bg_rect.y + self.bg_rect.height - 45
        surface.blit(rt(f_s, page_info, tc), (lx + 20, btn_y - 26))

        if dark:
            self.draw_dark_button(surface, self._jump_btn(), self._jump_text)
        else:
            self.draw_button(surface, self._jump_btn(), self._jump_text, BUTTON_BORDER)
        if not self.readonly:
            btns = self._action_buttons()
            for label, rect in btns.items():
                if dark:
                    self.draw_dark_button(surface, rect, self._btn_texts[label])
                else:
                    self.draw_button(surface, rect, self._btn_texts[label], BUTTON_BORDER)

        if self.jump_active and self.jump_field:
            ov = pygame.Surface((lw + 40, lh + 40), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 120))
            surface.blit(ov, (lx - 20, ly - 20))
            self.jump_field.draw(surface)
            surface.blit(rt(f_s, f"输入页码 (1-{self.get_total_pages()}):", tc),
                         (self.jump_field.rect.x, self.jump_field.rect.y - 25))
            confirm, cancel = self._jump_btns()
            if dark:
                self.draw_dark_button(surface, confirm, self._confirm_text)
                self.draw_dark_button(surface, cancel, self._cancel_text)
            else:
                self.draw_button(surface, confirm, self._confirm_text, BUTTON_BORDER)
                self.draw_button(surface, cancel, self._cancel_text, BUTTON_DISABLED)