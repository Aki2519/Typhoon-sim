# py/input_field.py
import pygame
from .constants import f_s, TXT, BUTTON_BORDER
from typing import Optional, Callable, Tuple, Union

class InputField:
    def __init__(self,
                 rect: Union[pygame.Rect, Tuple[int, int, int, int]],
                 label: str = "",
                 max_length: int = 30,
                 validator: Optional[Callable[[str], bool]] = None,
                 font=f_s):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.text = ""
        self.cursor_pos = 0
        self.active = False
        self.max_length = max_length
        self.validator = validator
        self.font = font
        self.selection_start: Optional[int] = None
        self.selection_end: Optional[int] = None
        self.dragging = False
        self.scrap_initialized = False

        # 退格加速
        self._bs_held = False
        self._bs_start = 0
        self._bs_tick = 0
        self._BS_DELAY = 500      # 500ms 后开始加速
        self._BS_INTERVAL = 250   # 4字符/秒 = 每250ms一个字符

    def _init_scrap(self):
        if not self.scrap_initialized:
            try:
                pygame.scrap.init()
                self.scrap_initialized = True
            except Exception:
                pass

    def _get_index_at_pos(self, x: int) -> int:
        rel_x = x - self.rect.x - 5
        if rel_x <= 0:
            return 0
        for i in range(1, len(self.text) + 1):
            w = self.font.size(self.text[:i])[0]
            if rel_x < w:
                return i
        return len(self.text)

    def _delete_selection(self):
        if self.selection_start is not None and self.selection_end is not None:
            start = min(self.selection_start, self.selection_end)
            end = max(self.selection_start, self.selection_end)
            self.text = self.text[:start] + self.text[end:]
            self.cursor_pos = start
            self.selection_start = self.selection_end = None

    def _copy_to_clipboard(self):
        if self.selection_start is not None and self.selection_end is not None:
            start = min(self.selection_start, self.selection_end)
            end = max(self.selection_start, self.selection_end)
            selected = self.text[start:end]
            self._init_scrap()
            if self.scrap_initialized:
                try:
                    pygame.scrap.put(pygame.SCRAP_TEXT, selected.encode('utf-8'))
                except Exception:
                    pass

    def _paste_from_clipboard(self):
        self._init_scrap()
        if self.scrap_initialized:
            try:
                raw = pygame.scrap.get(pygame.SCRAP_TEXT)
                if raw:
                    clipboard_text = raw.decode('utf-8').replace('\x00', '')
                    clipboard_text = ''.join(c for c in clipboard_text if c.isprintable())
                    self._delete_selection()
                    remaining = self.max_length - len(self.text)
                    insert_text = clipboard_text[:remaining]
                    self.text = self.text[:self.cursor_pos] + insert_text + self.text[self.cursor_pos:]
                    self.cursor_pos += len(insert_text)
            except Exception:
                pass

    def _handle_bs_accel(self) -> bool:
        """持续退格加速：返回 True 表示执行了一次删除"""
        if not self._bs_held:
            return False
        now = pygame.time.get_ticks()
        elapsed = now - self._bs_start
        if elapsed < self._BS_DELAY:
            return False
        ticks_from_start = elapsed - self._BS_DELAY
        expected_ticks = int(ticks_from_start / self._BS_INTERVAL)
        if expected_ticks > self._bs_tick:
            chars_to_delete = expected_ticks - self._bs_tick
            for _ in range(chars_to_delete):
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                    self._bs_tick = expected_ticks
                    return True
                if self.cursor_pos > 0:
                    self.text = self.text[:self.cursor_pos-1] + self.text[self.cursor_pos:]
                    self.cursor_pos -= 1
                else:
                    break
            self._bs_tick = expected_ticks
            return True
        return False

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.active:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.rect.collidepoint(event.pos):
                    self.active = True
                    idx = self._get_index_at_pos(event.pos[0])
                    self.cursor_pos = idx
                    self.selection_start = self.selection_end = None
                    return True
            return False

        # 每一帧都在 draw 中处理退格加速，这里只处理首次按下和释放
        if event.type == pygame.KEYDOWN:
            mods = pygame.key.get_mods()
            ctrl_pressed = mods & pygame.KMOD_CTRL

            if event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT,
                             pygame.K_LCTRL, pygame.K_RCTRL,
                             pygame.K_LALT, pygame.K_RALT,
                             pygame.K_LMETA, pygame.K_RMETA):
                return True

            if ctrl_pressed and event.key == pygame.K_c:
                self._copy_to_clipboard()
                return True
            if ctrl_pressed and event.key == pygame.K_v:
                self._paste_from_clipboard()
                return True
            if ctrl_pressed and event.key == pygame.K_a:
                self.selection_start = 0
                self.selection_end = len(self.text)
                self.cursor_pos = len(self.text)
                return True

            shift_pressed = mods & pygame.KMOD_SHIFT
            if event.key == pygame.K_LEFT:
                if self.cursor_pos > 0:
                    if shift_pressed:
                        if self.selection_start is None:
                            self.selection_start = self.cursor_pos
                        self.cursor_pos -= 1
                        self.selection_end = self.cursor_pos
                    else:
                        self.cursor_pos -= 1
                        self.selection_start = self.selection_end = None
                return True
            elif event.key == pygame.K_RIGHT:
                if self.cursor_pos < len(self.text):
                    if shift_pressed:
                        if self.selection_start is None:
                            self.selection_start = self.cursor_pos
                        self.cursor_pos += 1
                        self.selection_end = self.cursor_pos
                    else:
                        self.cursor_pos += 1
                        self.selection_start = self.selection_end = None
                return True
            elif event.key == pygame.K_BACKSPACE:
                # 开始退格加速计时
                self._bs_held = True
                self._bs_start = pygame.time.get_ticks()
                self._bs_tick = 0
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                elif self.cursor_pos > 0:
                    self.text = self.text[:self.cursor_pos-1] + self.text[self.cursor_pos:]
                    self.cursor_pos -= 1
                return True
            elif event.key == pygame.K_DELETE:
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                elif self.cursor_pos < len(self.text):
                    self.text = self.text[:self.cursor_pos] + self.text[self.cursor_pos+1:]
                return True
            elif event.key == pygame.K_TAB or event.key == pygame.K_KP_ENTER:
                return False
            elif event.unicode.isprintable() and len(self.text) < self.max_length:
                if self.validator and not self.validator(event.unicode):
                    return False
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                self.text = self.text[:self.cursor_pos] + event.unicode + self.text[self.cursor_pos:]
                self.cursor_pos += 1
                return True

        elif event.type == pygame.KEYUP:
            if event.key == pygame.K_BACKSPACE:
                self._bs_held = False
                return True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                idx = self._get_index_at_pos(event.pos[0])
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if self.selection_start is None:
                        self.selection_start = self.cursor_pos
                    self.cursor_pos = idx
                    self.selection_end = self.cursor_pos
                else:
                    self.cursor_pos = idx
                    self.selection_start = self.selection_end = None
                self.dragging = True
                return True
            else:
                self.active = False
                self.dragging = False
                self.selection_start = self.selection_end = None
                self._bs_held = False
                return False

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging and self.rect.collidepoint(event.pos):
                idx = self._get_index_at_pos(event.pos[0])
                if self.selection_start is None:
                    self.selection_start = self.cursor_pos
                self.cursor_pos = idx
                self.selection_end = self.cursor_pos
                return True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging:
                self.dragging = False
                return True

        return False

    def draw(self, surface: pygame.Surface):
        # 退格加速持续处理
        if self.active and self._bs_held:
            self._handle_bs_accel()

        if self.label:
            label_surf = self.font.render(self.label, True, TXT)
            surface.blit(label_surf, (self.rect.x, self.rect.y - 20))

        pygame.draw.rect(surface, (255, 255, 255), self.rect, 0, 3)
        border_color = BUTTON_BORDER if self.active else (150, 150, 150)
        pygame.draw.rect(surface, border_color, self.rect, 1, 3)

        text_x = self.rect.x + 5
        text_y = self.rect.y + 5
        if self.text:
            if self.selection_start is not None and self.selection_end is not None:
                start = min(self.selection_start, self.selection_end)
                end = max(self.selection_start, self.selection_end)
                if start < end:
                    x_start = text_x + self.font.size(self.text[:start])[0]
                    x_end = text_x + self.font.size(self.text[:end])[0]
                    highlight_rect = pygame.Rect(x_start, text_y, x_end - x_start, self.rect.height - 10)
                    s = pygame.Surface(highlight_rect.size, pygame.SRCALPHA)
                    s.fill((70, 130, 180, 100))
                    surface.blit(s, highlight_rect)

            text_surf = self.font.render(self.text, True, TXT)
            surface.blit(text_surf, (text_x, text_y))

        if self.active and (pygame.time.get_ticks() % 1000 < 500):
            cursor_x = text_x + self.font.size(self.text[:self.cursor_pos])[0]
            cursor_y_top = text_y
            cursor_y_bottom = self.rect.y + self.rect.height - 5
            pygame.draw.line(surface, TXT, (cursor_x, cursor_y_top), (cursor_x, cursor_y_bottom), 2)

    def get_text(self) -> str:
        return self.text

    def set_text(self, text: str):
        self.text = text[:self.max_length]
        self.cursor_pos = len(self.text)
        self.selection_start = self.selection_end = None

    def activate(self):
        self.active = True
        self.cursor_pos = len(self.text)
        self.selection_start = self.selection_end = None

    def deactivate(self):
        self.active = False
        self.dragging = False
        self.selection_start = self.selection_end = None
        self._bs_held = False