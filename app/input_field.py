# py/input_field.py
"""文本输入框。基于 pygame_textinput.TextInputManager 管理文本状态。"""
from __future__ import annotations

import pygame
import logging
from pygame_textinput import TextInputManager
from .constants import f_s, TXT, BUTTON_BORDER
from typing import Optional, Callable, Tuple, Union

logger = logging.getLogger(__name__)

# IME 引用计数: 多层对话框同时持有激活字段时,低层 deactivate 不再停掉顶层中文输入
_text_input_count: int = 0


def start_text_input_global(rect=None) -> bool:
    global _text_input_count
    if _text_input_count <= 0:
        try:
            pygame.key.start_text_input()
        except Exception:
            # IME 无法启动: 不把本字段计入计数。
            # 若仍将 _text_started 置 True,低层 deactivate 会多扣计数,
            # 误停仍激活字段的中文输入(见 _start_text_input)。
            return False
    # 计数已 >0(另一字段/上层对话框已启动 IME)时仍要更新候选窗位置:
    # 此前只在校首次启动(<="0")时 set_text_input_rect,嵌套场景下新的激活
    # 字段的 rect 从未传给 SDL,IME 候选窗停留在陈旧字段位置。
    # set_text_input_rect 失败不致命: 不影响计数,仅候选窗可能定位不准。
    if rect is not None:
        try:
            pygame.key.set_text_input_rect(rect)
        except Exception:
            pass
    _text_input_count += 1
    return True


def stop_text_input_global() -> None:
    global _text_input_count
    if _text_input_count > 0:
        _text_input_count -= 1
    if _text_input_count <= 0:
        # 计数下限归零,防止 InputField 对未激活字段也 stop 导致的负数泄漏
        # (计数为负会让文本输入被反复 start/stop,Windows 下每次重启 TSF/IME
        # 都会吞掉下一个按键 —— 表现为对话框里第一次退格无效,第二次才生效)
        _text_input_count = 0
        try:
            pygame.key.stop_text_input()
        except Exception:
            pass

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
        self.on_change: Optional[Callable[[], None]] = None
        self._prefix_w_cache: Optional[Tuple[str, int, int]] = None
        self._sel_w_cache: Optional[tuple] = None
        self._sel_hl_cache: dict = {}
        self._last_key_insert: Optional[tuple] = None
        self._text_started: bool = False

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
                    # 基于删除选区后的值构造候选文本，整体过 validator
                    # （与 KEYDOWN/TEXTINPUT 插入路径一致；粘贴失败时不破坏原内容，
                    #  避免已删除的选区无法还原）。
                    v = self._manager.value
                    # 待删除选区：删除后文本收敛到选区起点，尾段从选区终点后开始
                    sel_exists = (self.selection_start is not None
                                  and self.selection_end is not None)
                    head_end = min(self.selection_start, self.selection_end) if sel_exists \
                        else self.cursor_pos
                    tail_start = max(self.selection_start, self.selection_end) if sel_exists \
                        else self.cursor_pos
                    sel_len = tail_start - head_end
                    remaining = self.max_length - (len(v) - sel_len)
                    insert_text = clipboard_text[:max(0, remaining)]
                    new_val = v[:head_end] + insert_text + v[tail_start:]
                    new_val = new_val[:self.max_length]
                    if not self._validate_text(new_val):
                        return True
                    self._delete_selection()
                    self.text = new_val
                    self.cursor_pos = min(head_end + len(insert_text), self.max_length)
                    self._notify_change()
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
            deleted_any = False
            for _ in range(chars_to_delete):
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                    deleted_any = True
                elif self.cursor_pos > 0:
                    v = self._manager.value
                    cp = self.cursor_pos
                    self.text = v[:cp - 1] + v[cp:]
                    self.cursor_pos = cp - 1
                    deleted_any = True
                else:
                    break
            self._bs_tick = expected_ticks
            if deleted_any:
                self._notify_change()   # N14: 长按退格同样触发 on_change
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
            # 按住方向键期间中途按下 Shift 开始选区：锚点必须是"移动前"的光标位置,
            # 否则用移动后的最终 cursor 当锚点会把选区塌缩成一点(见历史 N-round 选区 bug)。
            anchor = self.cursor_pos
            for _ in range(chars_to_move):
                if self._lr_dir == -1 and self.cursor_pos > 0:
                    self.cursor_pos -= 1
                elif self._lr_dir == 1 and self.cursor_pos < len(self._manager.value):
                    self.cursor_pos += 1
                else:
                    break
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                if self.selection_start is None:
                    # 移动过程中新开的 shift 选区:以移动起点为锚点
                    self.selection_start = anchor
                self.selection_end = self.cursor_pos
            else:
                self.selection_start = self.selection_end = None
            self._lr_tick = expected_ticks
            return True
        return False

    def _prefix_width(self, val: str, cp: int) -> int:
        cache = self._prefix_w_cache
        if cache is not None and cache[0] == val and cache[1] == cp:
            return cache[2]
        w = self.font.size(val[:cp])[0]
        self._prefix_w_cache = (val, cp, w)
        return w

    def _notify_change(self):
        if self.on_change:
            self.on_change()

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

        # IME 组合结果：直接插入（R9 中文输入）。
        # 普通可打印键会同时产生 KEYDOWN(unicode) 与 TEXTINPUT，
        # 这里与 KEYDOWN 插入做一次性去重，避免同一字符插入两次。
        if event.type == pygame.TEXTINPUT and event.text:
            # Windows 下退格键经 WM_CHAR 会额外产生 TEXTINPUT('\x08') 等控制字符,
            # 必须过滤,否则会被当文本插入(产生隐形字符/与 KEYDOWN 删除抵消)
            if not event.text.isprintable():
                return True
            dedup = self._last_key_insert
            if (dedup and dedup[0] == event.text and len(event.text) == 1
                    and pygame.time.get_ticks() - dedup[1] < 150):
                self._last_key_insert = None
                return True
            self._delete_selection()
            v = self._manager.value
            cp = self.cursor_pos
            new_val = v[:cp] + event.text + v[cp:]
            if self._validate_text(new_val):
                self.text = new_val
                self.cursor_pos = cp + len(event.text)
                self._notify_change()
            return True

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
                    self._last_key_insert = (ch, pygame.time.get_ticks())
                    self._notify_change()
                return True

            # 用事件自带的修饰键状态(event.mod)而非实时 get_mods():
            # 事件在缓冲等待派发时 get_mods() 可能已反映"松开"后的状态,
            # 导致 Ctrl+V/C/A 等组合键偶发失效;event.mod 是事件的真实快照。
            mods = getattr(event, 'mod', 0) or 0
            ctrl_pressed = mods & pygame.KMOD_CTRL

            if event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT,
                              pygame.K_LCTRL, pygame.K_RCTRL,
                              pygame.K_LALT, pygame.K_RALT,
                              pygame.K_LMETA, pygame.K_RMETA):
                return True

            # 可打印字符由 TEXTINPUT 事件统一插入（KEYDOWN 与其同时触发，
            # 若在这里插入会与 TEXTINPUT 分支重复，产生双字符）
            if not ctrl_pressed and event.unicode and event.unicode.isprintable():
                # 清掉可能残留的小键盘去重条目,防止后续普通键的同字符被误吞
                self._last_key_insert = None
                return True

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

            # Tab / Enter / Escape / 上下方向键 → 归还上级
            if event.key in (pygame.K_TAB, pygame.K_RETURN, pygame.K_KP_ENTER,
                             pygame.K_ESCAPE, pygame.K_UP, pygame.K_DOWN):
                return False

            # 左右方向键：先捕获选区锚点，再移动并启动加速（不经过管理器）
            if event.key == pygame.K_LEFT:
                if mods & pygame.KMOD_SHIFT:
                    if self.selection_start is None:
                        # 以"移动前"的光标为锚点,保证 Shift+← 首键即正确成选区
                        self.selection_start = self.cursor_pos
                    if self.cursor_pos > 0:
                        self.cursor_pos -= 1
                    self.selection_end = self.cursor_pos
                else:
                    # 无 Shift 时不设 selection_end: 若此前为 Shift 选区,清除二者;
                    # 若本无选区,保持二者为 None,不留悬挂的 selection_end
                    # (悬挂 end 虽被各消费方用"start 与 end 均非 None"守住,但状态不干净)。
                    self.selection_start = self.selection_end = None
                    if self.cursor_pos > 0:
                        self.cursor_pos -= 1
                self._lr_held = True
                self._lr_start = pygame.time.get_ticks()
                self._lr_tick = 0
                self._lr_dir = -1
                return True
            if event.key == pygame.K_RIGHT:
                if mods & pygame.KMOD_SHIFT:
                    if self.selection_start is None:
                        self.selection_start = self.cursor_pos
                    if self.cursor_pos < len(self._manager.value):
                        self.cursor_pos += 1
                    self.selection_end = self.cursor_pos
                else:
                    self.selection_start = self.selection_end = None
                    if self.cursor_pos < len(self._manager.value):
                        self.cursor_pos += 1
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
                    self._notify_change()
                elif self.cursor_pos < len(self._manager.value):
                    v = self._manager.value
                    self.text = v[:self.cursor_pos] + v[self.cursor_pos + 1:]
                    self._notify_change()
                return True

            # Backspace：立即删除一次并启动加速（不经过管理器）
            if event.key == pygame.K_BACKSPACE:
                deleted = False
                if self.selection_start is not None and self.selection_end is not None:
                    self._delete_selection()
                    self._notify_change()
                    deleted = True
                elif self.cursor_pos > 0:
                    v = self._manager.value
                    cp = self.cursor_pos
                    self.text = v[:cp - 1] + v[cp:]
                    self.cursor_pos = cp - 1
                    self._notify_change()
                    deleted = True
                # 仅在确实删除了内容时启动长按加速(光标在行首时按退格无操作)
                if deleted:
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
                if self.active:
                    self._stop_text_input()
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

    def draw(self, surface: pygame.Surface, y_offset: int = 0):
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

        r = self.rect.move(0, y_offset)
        if self.label:
            label_surf = self.font.render(self.label, True, tc)
            surface.blit(label_surf, (r.x, r.y - 20))

        pygame.draw.rect(surface, bg, r, 0, 3)
        border_color = border_active if self.active else border_inactive
        pygame.draw.rect(surface, border_color, r, 1, 3)

        val = self._manager.value
        text_x = r.x + 5
        text_y = r.y + 5

        if val:
            # 选区高亮(表面按 (文本,选区,尺寸) 缓存,避免每帧新建 SRCALPHA)
            ss = self.selection_start
            se = self.selection_end
            if ss is not None and se is not None:
                start = min(ss, se)
                end = max(ss, se)
                if start < end:
                    cache = self._sel_w_cache
                    if cache is not None and cache[0] == val and cache[1] == start and cache[2] == end:
                        x_start, x_end = cache[3], cache[4]
                    else:
                        x_start = text_x + self.font.size(val[:start])[0]
                        x_end = text_x + self.font.size(val[:end])[0]
                        self._sel_w_cache = (val, start, end, x_start, x_end)
                    hl_key = (val, start, end, x_end - x_start, self.rect.height - 10)
                    hl_surf = self._sel_hl_cache.get(hl_key)
                    if hl_surf is None:
                        hl_surf = pygame.Surface((x_end - x_start, self.rect.height - 10),
                                                 pygame.SRCALPHA)
                        hl_surf.fill(hl)
                        if len(self._sel_hl_cache) > 32:
                            self._sel_hl_cache.pop(next(iter(self._sel_hl_cache)))
                        self._sel_hl_cache[hl_key] = hl_surf
                    surface.blit(hl_surf, (x_start, text_y))

            text_surf = self.font.render(val, True, tc)
            surface.blit(text_surf, (text_x, text_y))

        if self.active and (pygame.time.get_ticks() % 1000 < 500):
            cp = self.cursor_pos
            cursor_x = text_x + self._prefix_width(val, cp)
            cursor_c = (220, 220, 240) if d else TXT
            pygame.draw.line(
                surface, cursor_c,
                (cursor_x, text_y),
                (cursor_x, r.y + self.rect.height - 5),
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
        # start_text_input_global 返回是否成功获取了 IME 槽位。
        # 仅当确实获取到槽位才置 _text_started,否则 deactivate 时若照常
        # 调用 stop 会把别的字段的计数扣负,误停仍激活字段的中文输入。
        if start_text_input_global(self.rect):
            self._text_started = True

    def _stop_text_input(self):
        # 仅当本字段确实启动过文本输入才停止,防止对未激活字段 stop
        # 导致全局计数变负(负数会让文本输入反复重启,Windows 下吞掉下一个按键)
        if self._text_started:
            self._text_started = False
            stop_text_input_global()

    def activate(self):
        self.active = True
        self.cursor_pos = len(self._manager.value)
        self.selection_start = self.selection_end = None
        self._clear_hold_state()
        if not self._text_started:
            self._start_text_input()

    def activate_at(self, x: int):
        """激活并把光标定位到点击的 x 坐标处。"""
        self.active = True
        self.cursor_pos = self._get_index_at_pos(x)
        self.selection_start = self.selection_end = None
        self._clear_hold_state()
        if not self._text_started:
            self._start_text_input()

    def deactivate(self):
        self.active = False
        self.dragging = False
        self.selection_start = self.selection_end = None
        self._last_key_insert = None
        self._clear_hold_state()
        self._stop_text_input()
