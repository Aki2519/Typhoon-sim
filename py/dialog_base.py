# py/dialog_base.py
import pygame
from typing import Optional, Tuple, TYPE_CHECKING
from .constants import (
    LIST_BG, TXT, BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED, DIALOG_TITLE_BAR_HEIGHT,
    f_s, f_m
)

if TYPE_CHECKING:
    from .sim_view import SimView

TITLE_FONT = f_m
CONTENT_FONT = f_s

DIALOG_PADDING = 10
BTN_PADDING_V = 5
BTN_PADDING_H = 8
BTN_RADIUS = 5

BTN_PRIMARY_NORMAL = BUTTON_BORDER
BTN_PRIMARY_HOVER = (90, 150, 210)
BTN_PRIMARY_PRESSED = (60, 120, 180)
BTN_DISABLED = BUTTON_DISABLED
BTN_LIGHT_NORMAL = BUTTON_BG
BTN_LIGHT_HOVER = (130, 170, 220)
BTN_LIGHT_PRESSED = (90, 130, 190)

DIALOG_BG = LIST_BG
DIALOG_BORDER = BUTTON_BORDER
DIALOG_BORDER_WIDTH = 2
DIALOG_RADIUS = 10


class Dialog:
    def __init__(self, sim: 'SimView'):
        self.sim: 'SimView' = sim
        self.active = False
        self.current_field = 0

    def activate(self, *args, **kwargs):
        self.active = True
        # 推入焦点栈（如果 sim 有这个属性）
        if hasattr(self.sim, '_dialog_stack') and self not in self.sim._dialog_stack:
            self.sim._dialog_stack.append(self)

    def deactivate(self):
        self.active = False
        if hasattr(self.sim, '_dialog_stack') and self in self.sim._dialog_stack:
            self.sim._dialog_stack.remove(self)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
        return False

    def draw(self, surface: pygame.Surface):
        pass

    def draw_background(self, surface: pygame.Surface, rect: pygame.Rect,
                        color=DIALOG_BG, border_color=DIALOG_BORDER, alpha=True,
                        radius=DIALOG_RADIUS):
        if alpha:
            bg = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, color, (0, 0, rect.width, rect.height), 0, radius)
            pygame.draw.rect(bg, border_color, (0, 0, rect.width, rect.height),
                             DIALOG_BORDER_WIDTH, radius)
            surface.blit(bg, rect)
        else:
            pygame.draw.rect(surface, color, rect, 0, radius)
            pygame.draw.rect(surface, border_color, rect, DIALOG_BORDER_WIDTH, radius)

    def draw_title(self, surface: pygame.Surface, title: pygame.Surface,
                   rect: pygame.Rect, y_offset=15):
        x = rect.centerx - title.get_width() // 2
        y = rect.y + y_offset
        surface.blit(title, (x, y))

    def draw_button(self, surface: pygame.Surface, rect, text_surf: pygame.Surface,
                    style='primary', enabled=True, hover=False):
        if not isinstance(rect, pygame.Rect):
            rect = pygame.Rect(rect)
        if not enabled:
            color = BTN_DISABLED
        elif style == 'primary':
            color = BTN_PRIMARY_HOVER if hover else BTN_PRIMARY_NORMAL
        else:
            color = BTN_LIGHT_HOVER if hover else BTN_LIGHT_NORMAL
        pygame.draw.rect(surface, color, rect, 0, BTN_RADIUS)
        surface.blit(text_surf, (
            rect.centerx - text_surf.get_width() // 2,
            rect.centery - text_surf.get_height() // 2))

    def draw_text_button(self, surface, rect, font, text, text_color, style='primary',
                         enabled=True, hover=False):
        txt = font.render(text, True, text_color)
        self.draw_button(surface, rect, txt, style, enabled, hover)

    def draw_title_bar(self, surface: pygame.Surface, rect: pygame.Rect,
                       title_text: str, title_color=TXT, title_font=TITLE_FONT):
        if not isinstance(rect, pygame.Rect):
            rect = pygame.Rect(rect)
        bar_rect = pygame.Rect(rect.x, rect.y, rect.width, DIALOG_TITLE_BAR_HEIGHT)
        bar_surf = pygame.Surface((bar_rect.width, bar_rect.height), pygame.SRCALPHA)
        bar_surf.fill((0, 0, 0, 30))
        title = title_font.render(title_text, True, title_color)
        bar_surf.blit(title, (
            bar_surf.get_width() // 2 - title.get_width() // 2,
            bar_surf.get_height() // 2 - title.get_height() // 2))
        surface.blit(bar_surf, (bar_rect.x, bar_rect.y))
        return bar_rect


class DraggableDialog(Dialog):
    def __init__(self, sim: 'SimView'):
        super().__init__(sim)
        self.dragging = False
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.bg_rect = pygame.Rect(0, 0, 0, 0)
        self.title_bar_height = DIALOG_TITLE_BAR_HEIGHT

    def _is_title_bar(self, pos):
        return self.bg_rect.collidepoint(pos) and pos[1] - self.bg_rect.y < self.title_bar_height

    def handle_drag_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self._is_title_bar(e.pos):
                self.dragging = True
                self.drag_offset_x = e.pos[0] - self.bg_rect.x
                self.drag_offset_y = e.pos[1] - self.bg_rect.y
                # 提升到栈顶
                if hasattr(self.sim, '_dialog_stack') and self in self.sim._dialog_stack:
                    self.sim._dialog_stack.remove(self)
                    self.sim._dialog_stack.append(self)
                return True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.dragging = False
        elif e.type == pygame.MOUSEMOTION and self.dragging:
            new_x = e.pos[0] - self.drag_offset_x
            new_y = e.pos[1] - self.drag_offset_y
            new_x = max(0, min(new_x, self.sim.screen_width - self.bg_rect.width))
            new_y = max(0, min(new_y, self.sim.screen_height - self.bg_rect.height))
            self.bg_rect.x = new_x
            self.bg_rect.y = new_y
            return True
        return False