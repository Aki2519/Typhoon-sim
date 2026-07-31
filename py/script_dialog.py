# py/script_dialog.py
"""脚本选择与执行对话框 + 实时脚本编辑器。"""
from __future__ import annotations

import os
import pygame
from typing import List, Optional

from .constants import (
    f_s, f_m, rt, TXT, BUTTON_BORDER, BUTTON_BG, BUTTON_DISABLED,
    DIALOG_TITLE_BAR_HEIGHT,
    darken_color, lighten_color,
)
from .dialog_base import DraggableDialog
from .script_engine import scan_scripts, Script


SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'script')

# 对话框尺寸
DIALOG_W = 550
DIALOG_H = 450
ITEM_H = 36
ITEMS_PER_PAGE = 8

# 编辑器尺寸
EDIT_W = 660
EDIT_H = 560

_TEMPLATE = """#新脚本
jump 2026-08-01 00:00
target 120E 10N w=60:
    arrive 2026-08-01 00:00
    depart 2026-08-05 00:00
"""


class _TextArea:
    """简易多行文本编辑器（脚本编写用）。"""

    def __init__(self, rect: pygame.Rect, font, dark: bool = False) -> None:
        self.rect = pygame.Rect(rect)
        self.font = font
        self.dark = dark
        self.lines: List[str] = ['']
        self.row = 0
        self.col = 0
        self.scroll = 0
        self.active = False
        self._undo_stack: List[List[str]] = []
        self._undo_pos: int = -1
        self.on_change = None
        self._line_h = font.get_height() + 4
        self._gutter = 36
        self._pad = 6
        self._scrap_ok = False

    # ── 文本 ──

    def set_text(self, text: str) -> None:
        self.lines = text.split('\n') or ['']
        if not self.lines:
            self.lines = ['']
        self.row = min(self.row, len(self.lines) - 1)
        self.col = min(self.col, len(self.lines[self.row]))
        self.scroll = 0
        self._push_undo()

    def get_text(self) -> str:
        return '\n'.join(self.lines)

    def _push_undo(self):
        """显式推快照（set_text、paste 等整体操作时调用）。"""
        text = '\n'.join(self.lines)
        if self._undo_pos < 0 or self._undo_stack[self._undo_pos] != text:
            self._undo_stack[self._undo_pos + 1:] = [text]
            self._undo_pos += 1

    def _snapshot(self):
        """仅当内容变化时才推快照（每次编辑后调用）。"""
        text = '\n'.join(self.lines)
        if self._undo_pos < 0 or self._undo_stack[self._undo_pos] != text:
            self._undo_stack[self._undo_pos + 1:] = [text]
            self._undo_pos += 1
        if len(self._undo_stack) > 256:
            self._undo_stack[:] = self._undo_stack[-128:]
            self._undo_pos = len(self._undo_stack) - 1

    def _notify(self):
        self._snapshot()
        if self.on_change:
            self.on_change()

    # ── 几何 ──

    @property
    def visible_rows(self) -> int:
        return max(1, (self.rect.h - self._pad * 2) // self._line_h)

    def _ensure_visible(self):
        if self.row < self.scroll:
            self.scroll = self.row
        elif self.row >= self.scroll + self.visible_rows:
            self.scroll = self.row - self.visible_rows + 1

    def _index_at(self, pos) -> tuple:
        x, y = pos
        row = self.scroll + (y - self.rect.y - self._pad) // self._line_h
        row = max(0, min(len(self.lines) - 1, row))
        line = self.lines[row]
        rel_x = x - (self.rect.x + self._gutter + self._pad)
        col = len(line)
        prev_w = 0
        for i in range(1, len(line) + 1):
            w = self.font.size(line[:i].replace('\t', '    '))[0]
            if w >= rel_x:
                col = i - 1 if (rel_x - prev_w) < (w - rel_x) else i
                break
            prev_w = w
        return row, min(col, len(line))

    # ── 剪贴板 ──

    def _paste(self):
        if not self._scrap_ok:
            try:
                pygame.scrap.init()
                self._scrap_ok = True
            except Exception:
                return
        try:
            raw = pygame.scrap.get(pygame.SCRAP_TEXT)
        except Exception:
            return
        if not raw:
            return
        try:
            text = raw.decode('utf-8', errors='ignore').replace('\x00', '')
        except Exception:
            return
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        parts = text.split('\n')
        line = self.lines[self.row]
        before, after = line[:self.col], line[self.col:]
        if len(parts) == 1:
            self.lines[self.row] = before + parts[0] + after
            self.col += len(parts[0])
        else:
            self.lines[self.row] = before + parts[0]
            tail = parts[-1] + after
            self.lines[self.row + 1:self.row + 1] = parts[1:-1] + [tail]
            self.row += len(parts) - 1
            self.col = len(parts[-1])
        self._ensure_visible()
        self._notify()

    def _copy(self):
        if not self._scrap_ok:
            try:
                pygame.scrap.init()
                self._scrap_ok = True
            except Exception:
                return
        try:
            pygame.scrap.put(pygame.SCRAP_TEXT, self.get_text().encode('utf-8'))
        except Exception:
            pass

    def _cut(self):
        self._copy()
        self.lines.pop(self.row)
        if not self.lines:
            self.lines = ['']
        self.row = min(self.row, len(self.lines) - 1)
        self.col = 0
        self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - self.visible_rows)))
        self._ensure_visible()
        self._notify()

    def _undo(self):
        if self._undo_pos >= 0:
            text = self._undo_stack[self._undo_pos]
            if self._undo_pos > 0:
                self._undo_pos -= 1
            self.lines = text.split('\n') or ['']
            if not self.lines:
                self.lines = ['']
            self.row = min(self.row, len(self.lines) - 1)
            self.col = min(self.col, len(self.lines[self.row]))
            self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - self.visible_rows)))
            self._ensure_visible()
            if self.on_change:
                self.on_change()

    # ── 事件 ──

    def handle_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.rect.collidepoint(e.pos):
                self.active = True
                self.row, self.col = self._index_at(e.pos)
                try:
                    pygame.key.start_text_input()
                    pygame.key.set_text_input_rect(self.rect)
                except Exception:
                    pass
                return True
            self.active = False
            try:
                pygame.key.stop_text_input()
            except Exception:
                pass
            return False

        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.rect.collidepoint(mx, my):
                max_scroll = max(0, len(self.lines) - self.visible_rows)
                self.scroll = max(0, min(max_scroll, self.scroll - e.y * 3))
                return True
            return False

        if not self.active or e.type != pygame.KEYDOWN:
            return False

        mods = pygame.key.get_mods()
        if mods & pygame.KMOD_CTRL:
            if e.key == pygame.K_v:
                self._paste()
                return True
            if e.key == pygame.K_z:
                self._undo()
                return True
            if e.key == pygame.K_c:
                self._copy()
                return True
            if e.key == pygame.K_x:
                self._cut()
                return True
            if e.key == pygame.K_s:
                return True    # 保存由外部 Ctrl+S 处理，这里防止输入 's'
            return False

        line = self.lines[self.row]
        if e.key == pygame.K_RETURN:
            self.lines[self.row] = line[:self.col]
            self.lines.insert(self.row + 1, line[self.col:])
            self.row += 1
            self.col = 0
        elif e.key == pygame.K_BACKSPACE:
            if self.col > 0:
                self.lines[self.row] = line[:self.col - 1] + line[self.col:]
                self.col -= 1
            elif self.row > 0:
                self.col = len(self.lines[self.row - 1])
                self.lines[self.row - 1] += line
                self.lines.pop(self.row)
                self.row -= 1
        elif e.key == pygame.K_DELETE:
            if self.col < len(line):
                self.lines[self.row] = line[:self.col] + line[self.col + 1:]
            elif self.row + 1 < len(self.lines):
                self.lines[self.row] = line + self.lines[self.row + 1]
                self.lines.pop(self.row + 1)
        elif e.key == pygame.K_LEFT:
            if self.col > 0:
                self.col -= 1
            elif self.row > 0:
                self.row -= 1
                self.col = len(self.lines[self.row])
        elif e.key == pygame.K_RIGHT:
            if self.col < len(line):
                self.col += 1
            elif self.row + 1 < len(self.lines):
                self.row += 1
                self.col = 0
        elif e.key == pygame.K_UP:
            if self.row > 0:
                self.row -= 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif e.key == pygame.K_DOWN:
            if self.row + 1 < len(self.lines):
                self.row += 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif e.key == pygame.K_HOME:
            self.col = 0
        elif e.key == pygame.K_END:
            self.col = len(line)
        elif e.key == pygame.K_PAGEUP:
            self.row = max(0, self.row - self.visible_rows)
            self.col = min(self.col, len(self.lines[self.row]))
        elif e.key == pygame.K_PAGEDOWN:
            self.row = min(len(self.lines) - 1, self.row + self.visible_rows)
            self.col = min(self.col, len(self.lines[self.row]))
        elif e.key == pygame.K_TAB:
            if mods & pygame.KMOD_SHIFT:
                line = self.lines[self.row]
                if line.startswith('\t'*2):
                    self.lines[self.row] = line[2:]
                    self.col = max(0, self.col - 2)
                elif line.startswith('\t'):
                    self.lines[self.row] = line[1:]
                    self.col = max(0, self.col - 1)
                elif line.startswith(' '*4):
                    self.lines[self.row] = line[4:]
                    self.col = max(0, self.col - 4)
                elif line.startswith(' '*2):
                    self.lines[self.row] = line[2:]
                    self.col = max(0, self.col - 2)
                elif line.startswith(' '):
                    self.lines[self.row] = line[1:]
                    self.col = max(0, self.col - 1)
            else:
                self.lines[self.row] = line[:self.col] + '\t' + line[self.col:]
                self.col += 1
        elif e.unicode and e.unicode.isprintable():
            self.lines[self.row] = line[:self.col] + e.unicode + line[self.col:]
            self.col += len(e.unicode)
        else:
            return False

        self._ensure_visible()
        if e.key not in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
                         pygame.K_HOME, pygame.K_END, pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            self._notify()
        return True

    # ── 绘制 ──

    def draw(self, surface: pygame.Surface) -> None:
        r = self.rect
        d = self.dark
        bg = (40, 44, 55) if d else (255, 255, 255)
        gutter_bg = (35, 38, 48) if d else (238, 241, 246)
        border = (80, 110, 160) if (self.active and d) else \
                 BUTTON_BORDER if self.active else \
                 (80, 85, 100) if d else (150, 158, 172)
        ln_c = (120, 125, 140) if d else (150, 158, 175)
        tc = (220, 220, 240) if d else TXT
        scroll_c = (110, 115, 130) if d else (180, 190, 205)

        pygame.draw.rect(surface, bg, r, 0, 5)
        pygame.draw.rect(surface, gutter_bg, (r.x, r.y, self._gutter, r.h),
                         0, 5, 5, 0, 5, 0)
        pygame.draw.rect(surface, border, r, 1, 5)

        old_clip = surface.get_clip()
        surface.set_clip(r.inflate(-2, -2))
        y = r.y + self._pad
        for i in range(self.scroll, min(len(self.lines), self.scroll + self.visible_rows)):
            ln_surf = rt(f_s, str(i + 1), ln_c)
            surface.blit(ln_surf, (r.x + self._gutter - 6 - ln_surf.get_width(), y))
            text = self.lines[i].replace('\t', '    ')
            if text:
                s = rt(f_s, text, tc)
                surface.blit(s, (r.x + self._gutter + self._pad, y))
            y += self._line_h
        # 光标
        if self.active and pygame.time.get_ticks() % 1000 < 500:
            if self.scroll <= self.row < self.scroll + self.visible_rows:
                cx = r.x + self._gutter + self._pad + \
                    self.font.size(self.lines[self.row][:self.col].replace('\t', '    '))[0]
                cy = r.y + self._pad + (self.row - self.scroll) * self._line_h
                pygame.draw.line(surface, tc, (cx, cy), (cx, cy + self._line_h - 4), 2)
        surface.set_clip(old_clip)

        # 滚动条
        if len(self.lines) > self.visible_rows:
            track_h = r.h - 8
            bar_h = max(24, int(track_h * self.visible_rows / len(self.lines)))
            max_scroll = len(self.lines) - self.visible_rows
            bar_y = r.y + 4 + int((track_h - bar_h) * self.scroll / max_scroll)
            pygame.draw.rect(surface, scroll_c,
                             (r.right - 7, bar_y, 4, bar_h), 0, 2)


class ScriptDialog(DraggableDialog):
    """脚本列表 + 实时编辑对话框。"""

    def __init__(self, sim):
        super().__init__(sim)
        self.active = False
        self.dragging = False
        self.title_text = rt(f_m, "脚本列表", TXT)
        self.edit_title_text = rt(f_m, "脚本编辑器", TXT)
        self.hint_text = rt(f_s, "点击脚本开始执行，ESC 或点击外部关闭", TXT)
        self.close_text = rt(f_s, "关闭", (255, 255, 255))
        self.stop_text = rt(f_s, "停止脚本", (255, 255, 255))
        self.new_text = rt(f_s, "新建脚本", (255, 255, 255))
        self.edit_text = rt(f_s, "编辑", (255, 255, 255))
        self.run_text = rt(f_s, "运行", (255, 255, 255))
        self.save_text = rt(f_s, "保存", (255, 255, 255))
        self.back_text = rt(f_s, "返回", (255, 255, 255))

        self.scripts: List[dict] = []
        self._scroll_offset = 0
        self._hovered_idx = -1
        self._edit_btn_rects: List[tuple] = []

        # 编辑器状态（跨开关持久）
        self.mode = 'list'
        self._editor: Optional[_TextArea] = None
        self._editor_text: str = _TEMPLATE
        self._editor_name: str = ""
        self._parse_status: str = ""
        self._parse_ok: bool = True
        self._save_status: str = ""

        self._refresh_scripts()

    def _refresh_scripts(self):
        """刷新脚本列表。"""
        self.scripts = scan_scripts(SCRIPT_DIR)

    # ── 激活 / 关闭 ──

    def activate(self):
        """打开对话框。"""
        super().activate()
        self._refresh_scripts()
        self._scroll_offset = 0
        self._hovered_idx = -1
        self.mode = 'list'
        self._update_rect()

    def deactivate(self):
        """关闭对话框。"""
        if self._editor is not None:
            self._editor_text = self._editor.get_text()
        super().deactivate()
        self._hovered_idx = -1

    def _update_rect(self):
        dw, dh = (EDIT_W, EDIT_H) if self.mode == 'edit' else (DIALOG_W, DIALOG_H)
        dx = (self.sim.screen_width - dw) // 2
        dy = (self.sim.screen_height - dh) // 2
        self.bg_rect = pygame.Rect(dx, dy, dw, dh)
        if self.mode == 'edit':
            self._layout_editor()

    # ── 编辑器 ──

    def _layout_editor(self):
        r = self.bg_rect
        area = pygame.Rect(r.x + 20, r.y + 88, r.width - 40, r.height - 88 - 92)
        if self._editor is None:
            self._editor = _TextArea(area, f_s, dark=self.dark_mode)
            self._editor.set_text(self._editor_text)
            self._editor.on_change = self._live_parse
        else:
            self._editor.rect = area
            self._editor.dark = self.dark_mode
        self._live_parse()

    def _open_editor(self, text: str = None, name: str = ""):
        if text is not None:
            self._editor_text = text
            if self._editor is not None:
                self._editor.set_text(text)
                self._editor.row = self._editor.col = self._editor.scroll = 0
        self._editor_name = name
        self._save_status = ""
        self.mode = 'edit'
        self._update_rect()
        if self._editor:
            self._editor.active = True

    def _close_editor(self):
        if self._editor is not None:
            self._editor_text = self._editor.get_text()
        self.mode = 'list'
        self._refresh_scripts()
        self._update_rect()

    def _live_parse(self):
        """实时解析当前脚本内容并更新状态提示。"""
        if self._editor is None:
            return
        text = self._editor.get_text()
        try:
            script = Script.parse(text, self._editor_name or "editor")
            n = sum(1 for c in script.commands if c.target is not None)
            if n == 0:
                self._parse_ok = False
                self._parse_status = "无有效目标 (需要 target ...)"
            else:
                self._parse_ok = True
                jump = ""
                first = script.commands[0]
                if first.type == 'jump' and first.jump_date is not None:
                    jump = f"  起始跳跃 {first.jump_date.strftime('%Y-%m-%d %H:%M')}"
                self._parse_status = f"解析成功: {n} 个目标{jump}"
        except Exception as ex:
            self._parse_ok = False
            self._parse_status = f"解析失败: {ex}"

    def _run_editor_script(self):
        if self._editor is None:
            return
        text = self._editor.get_text()
        self._editor_text = text
        engine = self.sim.script_engine
        name = self._editor_name or "实时脚本"
        if engine.load_script(text, name):
            engine.start()
            self.deactivate()

    def _save_editor_script(self):
        if self._editor is None:
            return
        name = (self._editor_name or "").strip()
        if not name:
            name = "script.txt"
        if not name.endswith('.json'):
            name += '.json'
        self._editor_name = name
        path = os.path.join(SCRIPT_DIR, name)
        try:
            os.makedirs(SCRIPT_DIR, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self._editor.get_text())
            self._save_status = f"已保存: {name}"
            self._refresh_scripts()
        except Exception as ex:
            self._save_status = f"保存失败: {ex}"

    # ── 绘制 ──

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        if self.mode == 'edit':
            self._draw_editor(surface)
        else:
            self._draw_list(surface)

    def _draw_list(self, surface: pygame.Surface):
        r = self.bg_rect
        self.draw_background(surface, r)
        self.draw_title(surface, self.title_text, r, y_offset=20)

        list_x = r.x + 20
        list_y = r.y + 65
        list_w = r.width - 40
        list_h = ITEMS_PER_PAGE * ITEM_H

        # 脚本列表背景
        list_bg = pygame.Rect(list_x, list_y, list_w, list_h)
        pygame.draw.rect(surface, (255, 255, 255, 200), list_bg, 0, 5)
        pygame.draw.rect(surface, BUTTON_BORDER, list_bg, 1, 5)

        # 可见脚本
        visible_scripts = self.scripts[self._scroll_offset:self._scroll_offset + ITEMS_PER_PAGE]
        mx, my = pygame.mouse.get_pos()
        self._hovered_idx = -1
        self._edit_btn_rects = []

        for i, script_info in enumerate(visible_scripts):
            actual_idx = self._scroll_offset + i
            item_y = list_y + i * ITEM_H
            item_rect = pygame.Rect(list_x, item_y, list_w, ITEM_H)
            edit_btn = pygame.Rect(list_x + list_w - 62, item_y + 5, 52, ITEM_H - 10)
            self._edit_btn_rects.append((edit_btn, actual_idx))

            # 悬停高亮（编辑按钮区除外）
            hover = item_rect.collidepoint(mx, my) and not edit_btn.collidepoint(mx, my)
            if hover:
                self._hovered_idx = actual_idx
                pygame.draw.rect(surface, (200, 220, 255, 180), item_rect, 0, 4)

            # 分割线
            if i > 0:
                pygame.draw.line(surface, (200, 210, 230),
                                 (list_x + 5, item_y), (list_x + list_w - 5, item_y), 1)

            # 文件名
            name_surf = rt(f_s, script_info['filename'], TXT)
            surface.blit(name_surf, (list_x + 10, item_y + 4))

            # 简介（小字，宽度截断用 clip 避免 O(n²) rt() 截断循环）
            desc = script_info.get('description', '')
            if desc:
                max_desc_w = list_w - 250
                desc_surf = rt(f_s, desc, (120, 120, 150))
                if desc_surf.get_width() > max_desc_w:
                    old_clip_desc = surface.get_clip()
                    surface.set_clip(pygame.Rect(list_x + 160, item_y, max_desc_w, desc_surf.get_height()))
                    surface.blit(desc_surf, (list_x + 160, item_y + 6))
                    surface.set_clip(old_clip_desc)
                else:
                    surface.blit(desc_surf, (list_x + 160, item_y + 6))

            # 编辑按钮
            self._draw_btn(surface, edit_btn, self.edit_text, (120, 140, 180))

        # 滚动提示
        total = len(self.scripts)
        if total > ITEMS_PER_PAGE:
            scroll_info = f"{self._scroll_offset + 1}-{min(self._scroll_offset + ITEMS_PER_PAGE, total)} / {total}"
            scroll_surf = rt(f_s, scroll_info, TXT)
            surface.blit(scroll_surf, (list_x + list_w - scroll_surf.get_width() - 5, list_y + list_h + 8))

        # 提示文字
        hint_y = r.y + list_h + 80
        surface.blit(self.hint_text, (r.x + 20, hint_y))

        # 按钮
        btn_y = r.y + r.height - 40
        new_btn = pygame.Rect(r.x + 20, btn_y, 90, 28)
        self._draw_btn(surface, new_btn, self.new_text, (80, 150, 90))

        close_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
        self._draw_btn(surface, close_btn, self.close_text, BUTTON_BORDER)

        # 停止按钮（始终显示，未运行时灰色）
        engine_running = hasattr(self.sim, 'script_engine') and self.sim.script_engine.running
        stop_btn = pygame.Rect(r.x + r.width - 200, btn_y, 90, 28)
        stop_color = (200, 80, 80) if engine_running else BUTTON_DISABLED
        self._draw_btn(surface, stop_btn, self.stop_text, stop_color)

    def _draw_editor(self, surface: pygame.Surface):
        r = self.bg_rect
        self.draw_background(surface, r)
        self.draw_title(surface, self.edit_title_text, r, y_offset=20)

        # 文件名行
        name_label = rt(f_s, "文件名:", TXT)
        surface.blit(name_label, (r.x + 20, r.y + 58))
        name_rect = pygame.Rect(r.x + 20 + name_label.get_width() + 8, r.y + 54, 260, 24)
        self._name_rect = name_rect
        pygame.draw.rect(surface, (255, 255, 255), name_rect, 0, 5)
        pygame.draw.rect(surface, (150, 158, 172), name_rect, 1, 5)
        shown = self._editor_name or "(未命名，保存时自动补 .json)"
        name_color = TXT if self._editor_name else (160, 168, 182)
        surface.blit(rt(f_s, shown, name_color), (name_rect.x + 6, name_rect.y + 4))

        # 文本编辑区
        if self._editor:
            self._editor.draw(surface)

        # 实时解析状态
        status_y = r.y + r.height - 84
        color = (30, 140, 60) if self._parse_ok else (200, 60, 60)
        surface.blit(rt(f_s, self._parse_status, color, r.width - 40), (r.x + 20, status_y))
        if self._save_status:
            ss = rt(f_s, self._save_status, (100, 110, 140))
            surface.blit(ss, (r.x + 20, status_y + 20))

        # 按钮行
        btn_y = r.y + r.height - 40
        run_btn = pygame.Rect(r.x + 20, btn_y, 80, 28)
        self._draw_btn(surface, run_btn, self.run_text,
                       (80, 150, 90) if self._parse_ok else BUTTON_DISABLED)
        save_btn = pygame.Rect(r.x + 110, btn_y, 80, 28)
        self._draw_btn(surface, save_btn, self.save_text, BUTTON_BORDER)
        back_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
        self._draw_btn(surface, back_btn, self.back_text, (130, 130, 150))

    # ── 事件 ──

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if self.handle_drag_event(e):
            if self.mode == 'edit':
                self._layout_editor()
            return True

        if self.mode == 'edit':
            return self._handle_editor_event(e)
        return self._handle_list_event(e)

    def _handle_list_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_UP:
                self._scroll_offset = max(0, self._scroll_offset - 1)
                return True
            if e.key == pygame.K_DOWN:
                max_offset = max(0, len(self.scripts) - ITEMS_PER_PAGE)
                self._scroll_offset = min(max_offset, self._scroll_offset + 1)
                return True

        if e.type == pygame.MOUSEWHEEL:
            max_offset = max(0, len(self.scripts) - ITEMS_PER_PAGE)
            self._scroll_offset = max(0, min(max_offset, self._scroll_offset - e.y))
            return True

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            r = self.bg_rect

            # 编辑按钮
            for rect, idx in self._edit_btn_rects:
                if rect.collidepoint(x, y) and 0 <= idx < len(self.scripts):
                    self._edit_script(idx)
                    return True

            # 新建按钮
            btn_y = r.y + r.height - 40
            new_btn = pygame.Rect(r.x + 20, btn_y, 90, 28)
            if new_btn.collidepoint(x, y):
                self._open_editor(self._editor_text if self._editor_text else _TEMPLATE)
                return True

            # 关闭按钮
            close_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
            if close_btn.collidepoint(x, y):
                self.deactivate()
                return True

            # 停止按钮（始终可点击）
            if hasattr(self.sim, 'script_engine') and self.sim.script_engine.running:
                stop_btn = pygame.Rect(r.x + r.width - 200, btn_y, 90, 28)
                if stop_btn.collidepoint(x, y):
                    self.sim.script_engine.stop()
                    return True

            # 点击脚本项
            if self._hovered_idx >= 0 and self._hovered_idx < len(self.scripts):
                self._run_script(self._hovered_idx)
                return True

            # 点击对话框外部
            if not r.collidepoint(x, y):
                self.deactivate()
                return True

        return False

    def _handle_editor_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._close_editor()
                return True
            # Ctrl+S 快速保存
            if e.key == pygame.K_s and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self._save_editor_script()
                return True

        # 文件名输入（简易：点击后用键盘输入）
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            r = self.bg_rect
            btn_y = r.y + r.height - 40
            run_btn = pygame.Rect(r.x + 20, btn_y, 80, 28)
            save_btn = pygame.Rect(r.x + 110, btn_y, 80, 28)
            back_btn = pygame.Rect(r.x + r.width - 100, btn_y, 80, 28)
            if run_btn.collidepoint(e.pos):
                if self._parse_ok:
                    self._run_editor_script()
                return True
            if save_btn.collidepoint(e.pos):
                self._save_editor_script()
                return True
            if back_btn.collidepoint(e.pos):
                self._close_editor()
                return True
            name_rect = getattr(self, '_name_rect', None)
            if name_rect and name_rect.collidepoint(e.pos):
                self._name_editing = True
                if self._editor:
                    self._editor.active = False
                return True
            if self._editor and self._editor.rect.collidepoint(e.pos):
                self._name_editing = False

        # 文件名键入
        if getattr(self, '_name_editing', False) and e.type == pygame.KEYDOWN:
            if e.key == pygame.K_BACKSPACE:
                self._editor_name = self._editor_name[:-1]
                return True
            if e.key in (pygame.K_RETURN, pygame.K_TAB):
                self._name_editing = False
                if self._editor:
                    self._editor.active = True
                return True
            if e.unicode and e.unicode.isprintable() and len(self._editor_name) < 40:
                self._editor_name += e.unicode
                return True

        if self._editor and self._editor.handle_event(e):
            return True

        # 编辑器模式禁止事件穿透到底层地图
        consuming = {pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN, pygame.MOUSEWHEEL,
                     pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP}
        if e.type in consuming:
            return True
        return False

    # ── 运行 / 编辑入口 ──

    def _run_script(self, idx: int):
        """执行选中的脚本。"""
        if idx < 0 or idx >= len(self.scripts):
            return

        script_info = self.scripts[idx]
        try:
            with open(script_info['path'], 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            self.sim.show_error(f"无法读取脚本: {e}")
            return

        engine = self.sim.script_engine
        if engine.load_script(text, script_info['filename']):
            engine.start()
            self.deactivate()

    def _edit_script(self, idx: int):
        """在编辑器中打开选中脚本。"""
        script_info = self.scripts[idx]
        try:
            with open(script_info['path'], 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            self.sim.show_error(f"无法读取脚本: {e}")
            return
        self._open_editor(text, script_info['filename'])

    def _draw_btn(self, surface: pygame.Surface, rect: pygame.Rect,
                  text_surf: pygame.Surface, color):
        """绘制按钮。"""
        mx, my = pygame.mouse.get_pos()
        hover = rect.collidepoint(mx, my)
        pressed = hover and pygame.mouse.get_pressed()[0]

        if pressed:
            c = darken_color(color, 0.8)
        elif hover:
            c = lighten_color(color, 1.2)
        else:
            c = color

        pygame.draw.rect(surface, c, rect, 0, 5)
        surface.blit(text_surf, (
            rect.centerx - text_surf.get_width() // 2,
            rect.centery - text_surf.get_height() // 2,
        ))
