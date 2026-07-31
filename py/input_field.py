# py/input_field.py
"""文本输入框。基于 pygame_textinput.TextInputManager 管理文本状态。"""
from __future__ import annotations

import pygame
import logging
from pygame_textinput import TextInputManager
from .constants import f_s, TXT, BUTTON_BORDER
from typing import Optional, Callable, Tuple, Union

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# [KEEP] 旧 InputField 已替换为 pygame_textinput 版本。
# 如需恢复，搜索 "[KEEP]" 标记，旧代码全文保留在 git 历史中。
# ═══════════════════════════════════════════════════════════

class InputField:
    """文本输入框，基于 pygame_textinput.TextInputManager。

    dark=False (默认) → 白色底色 + 黑色文字
    dark=True → 暗色底色 + 浅色文字 + 暗色高亮 + 暗色光标
    """

    def __init__(self,
                 rect: Union[pygame.Rect, Tuple[int, int, int, int]],
                 label: str = "",
                 max_length: int = 30,
                 validator: Optional[Callable[[str], bool]] = None,
                 font=f_s,
                 dark: bool = False):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.active = False
        self.max_length = max_length
        self.validator = validator
        self.font = font
        self.dark = dark
        self.selection_start: Optional[int] = None
        self.selection_end: Optional[int] = None
        self.dragging = False
        self.scrap_initialized = False

        self._manager = TextInputManager(validator=self._validate_text)

        # 退格加速（Manager 依赖 set_repeat，但我们自己管理更可控）
        self._bs_held = False
        self._bs_start = 0
        self._bs_tick = 0
        self._BS_DELAY = 500
        self._BS_INTERVAL = 250

        # 左右方向键加速
        self._lr_held = False
        self._lr_start = 0
        self._lr_tick = 0
        self._lr_dir = 0
        self._LR_DELAY = 500
        self._LR_INTERVAL = 30

    # ── 文本状态委托 ──

    @property
    def text(self) -> str:
        return self._manager.value if hasattr(self, '_manager') else ""

    @text.setter
    def text(self, value: str):
        self._manager.value = value

    @property
    def cursor_pos(self) -> int:
        return self._manager.cursor_pos if hasattr(self, '_manager') else 0

    @cursor_pos.setter
    def cursor_pos(self, value: int):
        self._manager.cursor_pos = value

    def _validate_text(self, value: str) -> bool:
        if len(value) > self.max_length:
            return False
        if self.validator and value:
            return all(self.validator(c) for c in value)
        return True

    # ── 剪贴板 ──

    def _init_scrap(self):
        if not self.scrap_initialized:
            try:
                pygame.scrap.init()
                self.scrap_initialized = True
            except Exception:
                logger.debug("剪贴板初始化失败", exc_info=True)

    def _delete_selection(self):
        if self.selection_start is not None and self.selection_end is not None:
            start = min(self.selection_start, self.selection_end)
            end = max(self.selection_start, self.selection_end)
            self.text = self._manager.value[:start] + self._manager.value[end:]
            self.cursor_pos = start
            self.selection_start = self.selection_end = None

    def _copy_to_clipboard(self):
        if self.selection_start is not None and self.selection_end is not None:
            start = min(self.selection_start, self.selection_end)
            end = max(self.selection_start, self.selection_end)
            selected = self._manager.value[start:end]
            self._init_scrap()
            if self.scrap_initialized:
                try:
                    pygame.scrap.put(pygame.SCRAP_TEXT, selected.encode('utf-8'))
                except Exception:
                    logger.debug("复制到剪贴板失败", exc_info=True)

    def _paste_from_clipboard(self):
        self._init_scrap()
        if self.scrap_initialized:
            try:
                raw = pygame.scrap.get(pygame.SCRAP_TEXT)
                if raw:
                    clipboard_text = raw.decode('utf-8').replace('\x00', '')
                    clipboard_text = ''.join(c for c in clipboard_text if c.isprintable())
                    self._delete_selection()
                    v = self._manager.value
                    cp = self.cursor_pos
                    remaining = self.max_length - len(v)
                    insert_text = clipboard_text[:remaining]
                    new_val = v[:cp] + insert_text + v[cp:]
                    new_val = new_val[:self.max_length]
                    self.text = new_val
                    self.cursor_pos = min(cp + len(insert_text), self.max_length)
            except Exception:
                logger.debug("从剪贴板粘贴失败", exc_info=True)

    # ── 点击位置计算 ──

    def _handle_bs_accel(self) -> bool:
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
                    v = self._manager.value
                    cp = self.cursor_pos
                    self.text = v[:cp - 1] + v[cp:]
                    self.cursor_pos = cp - 1
                else:
                    break
            self._bs_tick = expected_ticks
            return True
        return False

    def _handle_lr_accel(self) -> bool:
        if not self._lr_held:
            return False
        now = pygame.time.get_ticks()
        elapsed = now - self._lr_start
        if elapsed < self._LR_DELAY:
            return False
        ticks_from_start = elapsed - self._LR_DELAY
        expected_ticks = int(ticks_from_start / self._LR_INTERVAL)
        if expected_ticks > self._lr_tick:
            chars_to_move = expected_ticks - self._lr_tick
            for _ in range(chars_to_move):
                if self._lr_dir == -1 and self.cursor_pos > 0:
                    self.cursor_pos -= 1
                elif self._lr_dir == 1 and self.cursor_pos < len(self._manager.value):
                    self.cursor_pos += 1
                else:
                    break
            self.selection_start = self.selection_end = None
            self._lr_tick = expected_ticks
            return True
        return False

    def _get_index_at_pos(self, x: int) -> int:
        rel_x = x - self.rect.x - 5
        if rel_x <= 0:
            return 0
        acc = 0
        for i, ch in enumerate(self._manager.value):
            w = self.font.size(ch)[0]
            if rel_x < acc + w // 2:
                return i
            acc += w
        return len(self._manager.value)

    # ── 小键盘按键映射 ──
    # pygame_textinput 的 _process_keydown 通过 pygame.key.name() 查找方法，
    # 但 SDL 将 K_KP1 命名为 '[1]' 等含括号字符串，无法匹配任何 _process_* 方法。
    # K_KP_ENTER 已在上层对话框统一处理，此处不转换。
    _NUMPAD_MAP = {
        pygame.K_KP0: '0', pygame.K_KP1: '1', pygame.K_KP2: '2',
        pygame.K_KP3: '3', pygame.K_KP4: '4', pygame.K_KP5: '5',
        pygame.K_KP6: '6', pygame.K_KP7: '7', pygame.K_KP8: '8',
        pygame.K_KP9: '9',
        pygame.K_KP_PERIOD: '.',
        pygame.K_KP_MINUS: '-',
        pygame.K_KP_PLUS: '+',
    }

    # ── 事件 ──

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.active:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.rect.collidepoint(event.pos):
                    self.activate_at(event.pos[0])
                    return True
            return False

        # 小键盘按键：pygame_textinput 无法识别 SDL 的 '[1]' 风格键名，在此手动插入对应字符
        if event.type == pygame.KEYDOWN:
            ch = self._NUMPAD_MAP.get(event.key)
            if ch is not None:
                self._delete_selection()
                v = self._manager.value
                cp = self.cursor_pos
                new_val = v[:cp] + ch + v[cp:]
                if self._validate_text(new_val):
                    self.text = new_val
                    self.cursor_pos = cp + 1
                return True

            mods = pygame.key.get_mods()
            ctrl_pressed = mods & pygame.KMOD_CTRL

            if event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT,
                              pygame.K_LCTRL, pygame.K_RCTRL,
                              pygame.K_LALT, pygame.K_RALT,
                              pygame.K_LMETA, pygame.K_RMETA):
                return True

            # 仅把可打印字符喂给管理器；控制键/组合键全部自行处理，避免写入 \t \r \x03 等
            if not ctrl_pressed and event.unicode and event.unicode.isprintable():
                self._manager.update([event])

            # Ctrl+Z / Ctrl+Y / Ctrl+S → 归还上级
            if ctrl_pressed and event.key in (pygame.K_z, pygame.K_y, pygame.K_s):
                return False

            # Ctrl+C / Ctrl+V / Ctrl+A —— 自行处理
            if ctrl_pressed and event.key == pygame.K_c:
                self._copy_to_clipboard()
                return True
            if ctrl_pressed and event.key == pygame.K_v:
                self._paste_from_clipboard()
                return True
            if ctrl_pressed and event.key == pygame.K_a:
                self.selection_start = 0
                self.selection_end = len(self._manager.value)
                self.cursor_pos = len(self._manager.value)
                return True

            # Tab / Enter / Escape → 归还上级
            if event.key in (pygame.K_TAB, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                return False

            # 左右方向键：先捕获选区锚点，再移动并启动加速（不经过管理器）
            if event.key == pygame.K_LEFT:
                if mods & pygame.KMOD_SHIFT:
                    if self.selection_start is None:
                        self.selection_start = self.cursor_pos
                else:
                    self.selection_start = self.selection_end = None
                if self.cursor_pos > 0:
                    self.cursor_pos -= 1
                self.selection_end = self.cursor_pos
                self._lr_held = True
                self._lr_start = pygame.time.get_ticks()
                self._lr_tick = 0
                self._lr_dir = -1
                return True
            if event.key == pygame.K_RIGHT:
                if mods & pygame.KMOD_SHIFT:
                    if self.selection_start is None:
                        self.selection_start = self.cursor_pos
                else:
                    self.selection_start = self.selection_end = None
                if self.cursor_pos < len(self._manager.value):
                    self.cursor_pos += 1
                self.selection_end = self.cursor_pos
                self._lr_held = True
                self._lr_start = pygame.time.get_ticks()
                self._lr_tick = 0
                self._lr_dir = 1
                return True

            # Home / End
            if event.key == pygame.K_HOME:
                self.selection_start = self.selection_end = None
                self.cursor_pos = 0
                return True
            if event.key == pygame.K_END:
                self.selection_start = self.selection_end = None
                self.cursor_pos = len(self._manager.value)
                return True

            # Delete：删除光标处字符（不经过管理器）
            if event.key == pygame.K_DELETE:
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                elif self.cursor_pos < len(self._manager.value):
                    v = self._manager.value
                    self.text = v[:self.cursor_pos] + v[self.cursor_pos + 1:]
                return True

            # Backspace：立即删除一次并启动加速（不经过管理器）
            if event.key == pygame.K_BACKSPACE:
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                elif self.cursor_pos > 0:
                    v = self._manager.value
                    cp = self.cursor_pos
                    self.text = v[:cp - 1] + v[cp:]
                    self.cursor_pos = cp - 1
                self._bs_held = True
                self._bs_start = pygame.time.get_ticks()
                self._bs_tick = 0
                return True

            # 其余按键——Manager 已处理（若为可打印字符），消费
            return True

        elif event.type == pygame.KEYUP:
            if event.key == pygame.K_BACKSPACE:
                self._bs_held = False
                return True
            if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                self._lr_held = False
                self._lr_dir = 0
                return True
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
                self._clear_hold_state()
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

    # ── 绘制 ──

    def draw(self, surface: pygame.Surface):
        # 退格 / 方向键加速持续处理
        if self.active and self._bs_held:
            self._handle_bs_accel()
        if self.active and self._lr_held:
            self._handle_lr_accel()

        d = self.dark
        bg = (40, 44, 55) if d else (255, 255, 255)
        border_active = (80, 110, 160) if d else BUTTON_BORDER
        border_inactive = (60, 65, 78) if d else (150, 150, 150)
        tc = (220, 220, 240) if d else TXT
        hl = (80, 120, 200, 80) if d else (70, 130, 180, 100)

        if self.label:
            label_surf = self.font.render(self.label, True, tc)
            surface.blit(label_surf, (self.rect.x, self.rect.y - 20))

        pygame.draw.rect(surface, bg, self.rect, 0, 3)
        border_color = border_active if self.active else border_inactive
        pygame.draw.rect(surface, border_color, self.rect, 1, 3)

        val = self._manager.value
        text_x = self.rect.x + 5
        text_y = self.rect.y + 5

        if val:
            # 选区高亮
            ss = self.selection_start
            se = self.selection_end
            if ss is not None and se is not None:
                start = min(ss, se)
                end = max(ss, se)
                if start < end:
                    x_start = text_x + self.font.size(val[:start])[0]
                    x_end = text_x + self.font.size(val[:end])[0]
                    highlight_rect = pygame.Rect(x_start, text_y, x_end - x_start, self.rect.height - 10)
                    s = pygame.Surface(highlight_rect.size, pygame.SRCALPHA)
                    s.fill(hl)
                    surface.blit(s, highlight_rect)

            text_surf = self.font.render(val, True, tc)
            surface.blit(text_surf, (text_x, text_y))

        if self.active and (pygame.time.get_ticks() % 1000 < 500):
            cp = self.cursor_pos
            cursor_x = text_x + self.font.size(val[:cp])[0]
            cursor_c = (220, 220, 240) if d else TXT
            pygame.draw.line(
                surface, cursor_c,
                (cursor_x, text_y),
                (cursor_x, self.rect.y + self.rect.height - 5),
                2)

    def get_text(self) -> str:
        return self._manager.value

    def set_text(self, text: str):
        self._manager.value = text[:self.max_length]
        self.cursor_pos = len(self._manager.value)
        self.selection_start = self.selection_end = None

    def _clear_hold_state(self):
        self._bs_held = False
        self._bs_start = 0
        self._bs_tick = 0
        self._lr_held = False
        self._lr_start = 0
        self._lr_tick = 0
        self._lr_dir = 0

    def _start_text_input(self):
        try:
            pygame.key.start_text_input()
            pygame.key.set_text_input_rect(self.rect)
        except Exception:
            pass

    def _stop_text_input(self):
        try:
            pygame.key.stop_text_input()
        except Exception:
            pass

    def activate(self):
        self.active = True
        self.cursor_pos = len(self._manager.value)
        self.selection_start = self.selection_end = None
        self._clear_hold_state()
        self._start_text_input()

    def activate_at(self, x: int):
        """激活并把光标定位到点击的 x 坐标处。"""
        self.active = True
        self.cursor_pos = self._get_index_at_pos(x)
        self.selection_start = self.selection_end = None
        self._clear_hold_state()
        self._start_text_input()

    def deactivate(self):
        self.active = False
        self.dragging = False
        self.selection_start = self.selection_end = None
        self._clear_hold_state()
        self._stop_text_input()
