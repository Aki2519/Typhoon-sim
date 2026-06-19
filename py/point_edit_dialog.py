# py/point_edit_dialog.py
import pygame
from datetime import datetime
from .constants import f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_DISABLED
from .input_field import InputField
from .dialog_base import DraggableDialog
from typing import Dict, Optional, Callable


class PointEditDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.labels = ['强度 (节):', '气压 (hPa):', '类型:', '纬度:', '经度:', '时间:']
        self.callback: Optional[Callable] = None
        self.fields: list[InputField] = []
        self.confirm_text = rt(f_s, "确认", (255,255,255))
        self.cancel_text = rt(f_s, "取消", (255,255,255))
        self.title = rt(f_m, "编辑报点", TXT)
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

    def activate(self, initial_values: Optional[Dict] = None, callback: Optional[Callable] = None):
        super().activate()
        self.callback = callback
        cols = 3
        field_width = 150
        field_height = 30
        spacing = 15
        total_width = cols * field_width + (cols - 1) * spacing
        start_x = (self.sim.screen_width - total_width) // 2
        start_y = (self.sim.screen_height - 200) // 2

        self.fields.clear()
        for i, label in enumerate(self.labels):
            row = i // cols
            col = i % cols
            x = start_x + col * (field_width + spacing)
            y = start_y + 40 + row * 60
            field = InputField((x, y, field_width, field_height), label=label, max_length=30)
            if initial_values and i < len(initial_values):
                key = ['wind','pressure','type','lat','lon','time'][i]
                field.set_text(initial_values.get(key, ""))
            self.fields.append(field)
        self.fields[0].activate()
        self.current_field = 0
        self._update_bg_rect()

    def deactivate(self):
        super().deactivate()
        self.fields.clear()
        self.callback = None
        self.dragging = False

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False
        # 处理拖动
        if self.handle_drag_event(e):
            return True
        # 鼠标点击切换输入框
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, field in enumerate(self.fields):
                if field.rect.collidepoint(e.pos):
                    for f in self.fields:
                        f.deactivate()
                    field.activate()
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
                self.submit()
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
            btn_y = self.bg_rect.y + self.bg_rect.height - 45
            confirm_rect = pygame.Rect(self.bg_rect.centerx - 90, btn_y, 80, 30)
            cancel_rect = pygame.Rect(self.bg_rect.centerx + 10, btn_y, 80, 30)
            if confirm_rect.collidepoint(x, y):
                self.submit()
                self.deactivate()
                return True
            if cancel_rect.collidepoint(x, y):
                self.deactivate()
                return True
        return False

    def submit(self):
        if self.callback:
            values = {}
            keys = ['wind', 'pressure', 'type', 'lat', 'lon', 'time']
            for i, key in enumerate(keys):
                values[key] = self.fields[i].get_text()
            self.callback(values)

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self.draw_background(surface, self.bg_rect)
        title_surf = self.title
        surface.blit(title_surf, (self.bg_rect.centerx - title_surf.get_width()//2, self.bg_rect.y + 10))
        for field in self.fields:
            field.draw(surface)
        btn_y = self.bg_rect.y + self.bg_rect.height - 45
        confirm_rect = pygame.Rect(self.bg_rect.centerx - 90, btn_y, 80, 30)
        cancel_rect = pygame.Rect(self.bg_rect.centerx + 10, btn_y, 80, 30)
        self.draw_button(surface, confirm_rect, self.confirm_text, BUTTON_BORDER)
        self.draw_button(surface, cancel_rect, self.cancel_text, BUTTON_DISABLED)