# py/point_edit_dialog.py
from __future__ import annotations

import pygame
from datetime import datetime
from .constants import f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED, SETTINGS_TEXT_LIGHT
from .input_field import InputField
from .dialog_base import DraggableDialog
from .utils import lon_to_display, lat_to_display, parse_lon, parse_lat
from typing import Dict, Optional, Callable


class PointEditDialog(DraggableDialog):
    # 字段集: point=编辑模式地图编辑(强度/气压/类型/纬度/经度/时间);
    # full=报点列表编辑(参考自评工具: 洋区/编号/时间/纬度/经度/强度/气压/类型/名字)
    FIELD_SETS = {
        'point': ['wind', 'pressure', 'type', 'lat', 'lon', 'time'],
        'full': ['basin', 'number', 'time', 'lat', 'lon',
                 'wind', 'pressure', 'type', 'name'],
    }
    FIELD_LABELS = {
        'wind': '强度 (节):', 'pressure': '气压 (hPa):', 'type': '类型:',
        'lat': '纬度:', 'lon': '经度:', 'time': '时间:',
        'basin': '洋区:', 'number': '编号:', 'name': '名字:',
    }

    def __init__(self, sim):
        super().__init__(sim)
        self.field_keys = list(self.FIELD_SETS['point'])
        self.labels = [self.FIELD_LABELS[k] for k in self.field_keys]
        self._latlon_idx = [i for i, k in enumerate(self.field_keys)
                            if k in ('lat', 'lon')]
        self.callback: Optional[Callable] = None
        self.fields: list[InputField] = []
        self.point_nav: Optional[Callable] = None
        self.point_index: int = 0
        self.point_total: int = 0
        self.confirm_text = rt(f_s, "确认", (255,255,255))
        self.cancel_text = rt(f_s, "取消", (255,255,255))
        self.title = rt(f_m, "编辑报点", TXT)
        self.title_dark = rt(f_m, "编辑报点", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = 30

    def _update_bg_rect(self):
        if not self.fields:
            self.bg_rect = pygame.Rect(0, 0, 600, 220)
            return
        min_x = min(f.rect.x for f in self.fields)
        max_x = max(f.rect.right for f in self.fields)
        min_y = min(f.rect.y for f in self.fields)
        max_y = max(f.rect.bottom for f in self.fields)
        padding = 20
        title_height = 30
        button_height = 40
        self.bg_rect = pygame.Rect(
            min_x - padding,
            min_y - title_height - padding,
            max_x - min_x + 2 * padding,
            max_y - min_y + title_height + button_height + 2 * padding
        )

    def activate(self, initial_values: Optional[Dict] = None, callback: Optional[Callable] = None,
                 point_nav: Optional[Callable] = None, point_index: int = 0,
                 point_total: int = 0, field_set: str = 'point'):
        """打开编辑报点对话框。

        point_nav: 可选, callable(cur_index, delta) → (init_values, callback, new_index) 或 None。
        提供后支持按 , / . 切换上一/下一个报点。
        field_set: 'point'=编辑模式地图编辑(6 字段); 'full'=报点列表编辑
        (9 字段, 含洋区/编号/名字, 参考自评工具)。"""
        super().activate()
        self.callback = callback
        self.point_nav = point_nav
        self.point_index = point_index
        self.point_total = point_total
        self.field_keys = list(self.FIELD_SETS.get(field_set, self.FIELD_SETS['point']))
        self.labels = [self.FIELD_LABELS[k] for k in self.field_keys]
        self._latlon_idx = [i for i, k in enumerate(self.field_keys)
                            if k in ('lat', 'lon')]
        self._sw = self.sim.screen_width
        self._sh = self.sim.screen_height
        cols = 3
        field_width = 150
        field_height = 30
        spacing = 15
        total_width = cols * field_width + (cols - 1) * spacing
        start_x = (self._sw - total_width) // 2
        start_y = (self._sh - 220) // 2

        self.fields.clear()
        for i, label in enumerate(self.labels):
            row = i // cols
            col = i % cols
            x = start_x + col * (field_width + spacing)
            y = start_y + 40 + row * 60
            field = InputField((x, y, field_width, field_height), label=label,
                               max_length=30, dark=self.dark_mode)
            self.fields.append(field)
        self._populate_fields(initial_values)
        self.fields[0].activate()
        self.current_field = 0
        self._update_pos_title()
        self._update_bg_rect()

    def _populate_fields(self, initial_values: Optional[Dict]) -> None:
        """把初始值写入输入框(经纬度转 NSEW 显示格式)。"""
        for i, key in enumerate(self.field_keys):
            val = ""
            if initial_values and key in initial_values:
                val = initial_values[key]
                if key == 'lat' and val != "":
                    try:
                        val = lat_to_display(float(val))
                    except (TypeError, ValueError):
                        # 数据中的纬度不是数字(空/None/脏值): 保留原文本,
                        # 由 submit() 的 parse_lat 给出"纬度格式不正确"提示
                        val = str(val)
                elif key == 'lon' and val != "":
                    try:
                        val = lon_to_display(float(val))
                    except (TypeError, ValueError):
                        val = str(val)
            self.fields[i].set_text(val)

    def _update_pos_title(self) -> None:
        if self.point_total > 0:
            t = f"编辑报点 ({self.point_index + 1}/{self.point_total})"
            self.title = rt(f_m, t, TXT)
            self.title_dark = rt(f_m, t, SETTINGS_TEXT_LIGHT)

    def _nav_point(self, delta: int) -> bool:
        """按 , / . 切换上一/下一个报点(重填字段、替换提交回调)。"""
        if not self.point_nav:
            return False
        res = self.point_nav(self.point_index, delta)
        if res is None:
            return False
        init, cb, new_idx = res
        self.point_index = new_idx
        self.callback = cb
        self._populate_fields(init)
        self._update_pos_title()
        self._update_bg_rect()
        return True

    def deactivate(self):
        # 必须调用基类复位 active，否则对话框残留绘制(只有面板无输入框/文字)
        super().deactivate()
        for f in self.fields:
            f.deactivate()
        self.fields.clear()
        self.callback = None
        self.point_nav = None
        self.point_index = 0
        self.point_total = 0
        self.field_keys = list(self.FIELD_SETS['point'])
        self.labels = [self.FIELD_LABELS[k] for k in self.field_keys]
        self._latlon_idx = [i for i, k in enumerate(self.field_keys)
                            if k in ('lat', 'lon')]
        self.dragging = False

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        # 处理拖动（记录旧位置，拖动后同步字段偏移）
        old_x, old_y = self.bg_rect.x, self.bg_rect.y
        if self.handle_drag_event(e):
            dx = self.bg_rect.x - old_x
            dy = self.bg_rect.y - old_y
            if dx != 0 or dy != 0:
                for field in self.fields:
                    field.rect.x += dx
                    field.rect.y += dy
            return True
        # , / .: 切换上一/下一个报点(须在字段消费按键前拦截,
        # 否则激活字段会把可打印的 , . 吞掉)
        # 例外: 纬度/经度字段需要输入小数点(如 15.5N),. 不拦截;
        # 未提供导航时也不拦截,一律落入下方字段处理
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_COMMA, pygame.K_PERIOD):
            active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
            if not (e.key == pygame.K_PERIOD and active_idx in self._latlon_idx):
                delta = -1 if e.key == pygame.K_COMMA else 1
                if self._nav_point(delta):
                    return True
                if self.point_nav is not None:
                    return True  # 有导航但已到边界: 消费按键,不切换
            # 未拦截: 落入下方字段处理(输入框可正常输入 , / .)
        # 鼠标点击切换输入框
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, field in enumerate(self.fields):
                if field.rect.collidepoint(e.pos):
                    for f in self.fields:
                        f.deactivate()
                    field.activate_at(e.pos[0])
                    self.current_field = i
                    return True
        # 让当前激活字段处理事件
        for i, field in enumerate(self.fields):
            if field.handle_event(e):
                if field.active:
                    self.current_field = i
                return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            elif e.key == pygame.K_RETURN:
                if self.submit():
                    self.deactivate()
                return True
            elif e.key == pygame.K_TAB or e.key == pygame.K_KP_ENTER:
                active_idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                if active_idx != -1:
                    self.fields[active_idx].deactivate()
                    shift = pygame.key.get_mods() & pygame.KMOD_SHIFT
                    next_idx = (active_idx + (-1 if shift else 1)) % len(self.fields)
                    self.fields[next_idx].activate()
                    self.current_field = next_idx
                else:
                    self.fields[0].activate()
                    self.current_field = 0
                return True
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            confirm_rect, cancel_rect = self._btns()
            if confirm_rect.collidepoint(x, y):
                if self.submit():
                    self.deactivate()
                return True
            if cancel_rect.collidepoint(x, y):
                self.deactivate()
                return True
        return False

    def _btns(self):
        btn_y = self.bg_rect.y + self.bg_rect.height - 45
        return (pygame.Rect(self.bg_rect.centerx - 90, btn_y, 80, 30),
                pygame.Rect(self.bg_rect.centerx + 10, btn_y, 80, 30))

    def submit(self) -> bool:
        values = {}
        for i, key in enumerate(self.field_keys):
            raw = self.fields[i].get_text()
            # 经纬度从 NSEW 格式解析回浮点数
            if key == 'lat':
                try:
                    values[key] = str(parse_lat(raw))
                except (TypeError, ValueError):
                    self.sim.show_error("纬度格式不正确")
                    return False
            elif key == 'lon':
                try:
                    values[key] = str(parse_lon(raw))
                except (TypeError, ValueError):
                    self.sim.show_error("经度格式不正确")
                    return False
            elif key == 'time':
                # 时间串必须精确匹配 10 位 %Y%m%d%H 或 12 位 %Y%m%d%H%M(含分钟),
                # 尾部多余字符视为非法,否则拒绝提交
                t = raw.strip()
                ok = False
                if len(t) == 12:
                    try:
                        datetime.strptime(t, "%Y%m%d%H%M")
                        ok = True
                    except ValueError:
                        ok = False
                elif len(t) == 10:
                    try:
                        datetime.strptime(t, "%Y%m%d%H")
                        ok = True
                    except ValueError:
                        ok = False
                if not ok:
                    self.sim.show_error("时间格式不正确(需 YYYYMMDDHH 或 YYYYMMDDHHMM)")
                    return False
                values[key] = t
            elif key in ('wind', 'pressure'):
                raw_s = raw.strip()
                if raw_s:
                    try:
                        num = int(raw_s)
                    except (TypeError, ValueError):
                        self.sim.show_error("强度必须是数字" if key == 'wind' else "气压必须是数字")
                        return False
                    if num < 0:
                        self.sim.show_error("强度不能为负数" if key == 'wind' else "气压不能为负数")
                        return False
                # 保留原始字符串(空=默认值),由回调统一按 _apply_point_change 语义解析
                values[key] = raw
            else:
                values[key] = raw
        # 校验独立于回调是否存在: 无回调时也不静默跳过校验
        if self.callback:
            res = self.callback(values)
            # 回调返回 False 表示应用失败(如点列表内部校验/应用异常):
            # 保持对话框打开,避免用户输入被静默丢弃
            if res is False:
                return False
        return True

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dark = self.dark_mode
        if dark:
            self.draw_dark_panel(surface, self.bg_rect)
        else:
            self.draw_background(surface, self.bg_rect)
        title_surf = self.title_dark if dark else self.title
        surface.blit(title_surf, (self.bg_rect.centerx - title_surf.get_width()//2, self.bg_rect.y + 10))
        for field in self.fields:
            field.draw(surface)
        confirm_rect, cancel_rect = self._btns()
        if dark:
            self.draw_dark_button(surface, confirm_rect, self.confirm_text)
            self.draw_dark_button(surface, cancel_rect, self.cancel_text)
        else:
            self.draw_button(surface, confirm_rect, self.confirm_text, BUTTON_BORDER)
            self.draw_button(surface, cancel_rect, self.cancel_text, BUTTON_DISABLED)