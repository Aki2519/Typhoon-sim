# app/module_dialog.py
"""「模块」界面（用户 2026-10-14，2026-10-15 按用户要求补齐版式）。

**版式逐项对齐台风列表（`app/ty_list.py`）**：
  · 尺寸：650×**610**（= `TY_LIST_TOP_OFFSET + 6×TY_LIST_ITEM_HEIGHT + 130`，与台风列表同一个公式）
  · 顶部工具条：搜索框（`+20, y+45, 180, 24`）+ 排序按钮（`+210, y+45, 110, 24`）
  · 分页：`第 i/n 页  共 m 个` + ◀ 跳页 ▶（位置取自 `_jump_btn` 那一套）
  · 行：两段式文字（模块名 / 简介）+ **淡白描边**；"当前行"常驻蓝色高亮（与台风列表同色）
  · 行缓存：按 hash 复用 surface（内容没变不重建）
  · 按钮：`draw_dark_button` / `draw_button`
  · 背景/标题：暗色 `draw_dark_panel`、亮色 `draw_background`（我第一版用了脚本界面那套
    无条件浅色背景 ⇒ 暗色下整块发白，用户："背景怎么是白色的"）。

与台风列表**故意不同**（用户明确要求）：
  · 每行**右下角**一个勾选框 = 启用/停用（台风列表没有）
  · 点行 = 选中该行**并**进入它的设置界面
"""
from __future__ import annotations

import pygame
from typing import Dict, List, Tuple

from .constants import (
    f_s, f_m, rt, TXT, LIST_HL, BUTTON_BG,
    SETTINGS_TEXT_LIGHT, TY_LIST_ITEM_HEIGHT, TY_LIST_TOP_OFFSET, TY_LIST_WIDTH,
    TY_LIST_ROWS_PER_PAGE,
)
from .dialog_base import DraggableDialog
from .input_field import InputField
from .module_registry import MODULES, is_enabled, open_module_settings, set_enabled

ROW_H = TY_LIST_ITEM_HEIGHT
TOP_OFFSET = TY_LIST_TOP_OFFSET
ROWS_PER_PAGE = TY_LIST_ROWS_PER_PAGE
_JUMP_W, _ARROW = 80, 18
CHK = 18                       # 勾选框边长（右下角）

#: 每行那圈**淡白描边**（用户 2026-10-14："加一圈白框，要比较淡，调到你满意为止"）。
#: 取值过程（同一张暗底上叠 alpha 白，逐档渲染对比）：
#:   70 → 比行内亮太多，看着像"选中"；45 → 仍抢眼；**28** → 看得见轮廓但不吵；
#:   18 → 基本看不见。取 28：暗底上约 (58,60,66)，比面板底色亮一档、比选中蓝低得多。
#: 亮色主题在**白底**上要更实才看得出 ⇒ 用 70。
ROW_BORDER_ALPHA_DARK = 28
ROW_BORDER_ALPHA_LIGHT = 70
ROW_BORDER_RADIUS = 5


class ModuleDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.active = False
        self.dragging = False
        self.title = rt(f_m, "模块", TXT)
        self.title_dark = rt(f_m, "模块", SETTINGS_TEXT_LIGHT)
        self.close_text = rt(f_s, "关闭", (255, 255, 255))
        self.all_text = rt(f_s, "全部启用", (255, 255, 255))
        self.none_text = rt(f_s, "全部停用", (255, 255, 255))
        self.status = ''
        #: "当前行"（对齐台风列表的 `si`）：-1 = 无
        self.si = -1
        self.current_page = 0
        self.rows_per_page = ROWS_PER_PAGE
        self.sort_mode = 'reg'          # reg / name / state
        self.jump_active = False
        self.jump_input = ""
        self.search_field = InputField(pygame.Rect(0, 0, 180, 24), max_length=30,
                                        font=f_s, dark=self.dark_mode)
        self._filtered: List[str] = []          # 过滤+排序后的 mid 列表
        self._row_cache: Dict[str, Tuple[str, dict]] = {}
        self._row_rects: List[pygame.Rect] = []
        self._chk_rects: List[pygame.Rect] = []

    # ── 生命周期 ──
    def refresh(self) -> None:
        self.status = ''
        self._apply_filter(reset_page=False)

    def activate(self):
        super().activate()
        self.search_field.rect = self._search_rect()
        self._apply_filter(reset_page=True)
        if self.si < 0 and self._filtered:
            self.si = 0                     # 与台风列表一致：进来就有"当前行"
        if self.si >= len(MODULES):
            self.si = -1
        self._update_bg_rect()

    def deactivate(self):
        # ⚠ 必须先走基类： 才把本对话框移出
        #    并失效  的活跃计数。
        #   绕开它会留下"栈里还挂着一个 active=False 的对话框"，
        #    遍历栈时全 False 又落到末尾 
        #   ⇒ 关掉「模块」之后所有快捷键全死（dialogs-ui 审查第 1 条）。
        super().deactivate()
        self.dragging = False
        self.jump_active = False
        try:
            self.search_field.deactivate()
        except Exception:
            pass

    def _update_bg_rect(self):
        """与台风列表同一个高度公式 ⇒ **固定 610**（6 行 × 70 + 60 + 130）。"""
        lw = TY_LIST_WIDTH
        lh = TOP_OFFSET + self.rows_per_page * ROW_H + 100 + 30
        self.bg_rect = pygame.Rect((self.sim.screen_width - lw) // 2,
                                   (self.sim.screen_height - lh) // 2, lw, lh)

    # ── 工具条 / 分页 / 按钮 几何（照台风列表）──
    def _search_rect(self):
        return pygame.Rect(self.bg_rect.x + 20, self.bg_rect.y + 45, 180, 24)

    def _sort_btn(self):
        return pygame.Rect(self.bg_rect.x + 210, self.bg_rect.y + 45, 110, 24)

    def _jump_btn(self):
        return pygame.Rect(self.bg_rect.right - 30 - _JUMP_W - 5 - _ARROW,
                           self.bg_rect.bottom - 38, _JUMP_W, 25)

    def _page_left_btn(self):
        return pygame.Rect(self._jump_btn().left - _ARROW - 5,
                           self.bg_rect.bottom - 40, _ARROW, _ARROW)

    def _page_right_btn(self):
        return pygame.Rect(self._jump_btn().right + 5,
                           self.bg_rect.bottom - 40, _ARROW, _ARROW)

    def _btn_rects(self):
        r = self.bg_rect
        y = r.y + r.height - 70
        return {
            'all': pygame.Rect(r.x + 20, y, 84, 25),
            'none': pygame.Rect(r.x + 112, y, 84, 25),
            'close': pygame.Rect(r.right - 100, y, 80, 25),
        }

    # ── 过滤 / 排序 / 分页 ──
    def _apply_filter(self, reset_page: bool = False):
        text = (self.search_field.text or "").strip().lower()
        mids = [m.mid for m in MODULES
                if not text or text in m.name.lower() or text in m.desc.lower()]
        if self.sort_mode == 'name':
            order = {m.mid: m.name for m in MODULES}
            mids.sort(key=lambda x: order.get(x, x))
        elif self.sort_mode == 'state':
            mids.sort(key=lambda x: (not is_enabled(self.sim, x), x))
        self._filtered = mids
        if reset_page:
            self.current_page = 0
        self.current_page = max(0, min(self.current_page, self.get_total_pages() - 1))

    def get_total_pages(self):
        return max(1, (len(self._filtered) + self.rows_per_page - 1) // self.rows_per_page)

    def get_page_start(self):
        return self.current_page * self.rows_per_page

    def _page_items(self) -> List[str]:
        s = self.get_page_start()
        return self._filtered[s:s + self.rows_per_page]

    @staticmethod
    def _module(mid):
        for m in MODULES:
            if m.mid == mid:
                return m
        return MODULES[0]

    # ── 行缓存 ──
    def _row_hash(self, m) -> str:
        return "%s|%s|%s|%s" % (m.mid, m.name, m.desc,
                                is_enabled(self.sim, m.mid))

    def _row_surfs(self, m, dark: bool) -> dict:
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        dim = (150, 158, 176) if dark else (110, 118, 138)
        return {'name': rt(f_s, m.name, tc if is_enabled(self.sim, m.mid) else dim, 420),
                'desc': rt(f_s, m.desc, dim, 586)}

    def _row_surfs_cached(self, m, dark: bool) -> dict:
        key = self._row_hash(m) + ("|d" if dark else "|l")
        hit = self._row_cache.get(m.mid)
        if hit is None or hit[0] != key:
            surfs = self._row_surfs(m, dark)
            self._row_cache[m.mid] = (key, surfs)
            return surfs
        return hit[1]

    def _clear_row_cache(self):
        self._row_cache.clear()

    # ── 事件 ──
    def handle_event(self, e) -> bool:
        if not self.active:
            return False
        if self.jump_active and self._handle_jump(e):
            return True
        if self.handle_drag_event(e):
            return True
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            if self.jump_active:
                self.jump_active = False
            else:
                self.deactivate()
            return True
        if self.search_field.handle_event(e):
            self._apply_filter(reset_page=True)
            return True
        if e.type != pygame.MOUSEBUTTONDOWN or e.button != 1:
            return False
        b = self._btn_rects()
        if b['all'].collidepoint(e.pos):
            for m in MODULES:
                set_enabled(self.sim, m.mid, True)
            self.status = '已全部启用'
            self._clear_row_cache()
            return True
        if b['none'].collidepoint(e.pos):
            for m in MODULES:
                set_enabled(self.sim, m.mid, False)
            self.status = '已全部停用'
            self._clear_row_cache()
            return True
        if b['close'].collidepoint(e.pos):
            self.deactivate()
            return True
        if self._sort_btn().collidepoint(e.pos):
            self.sort_mode = {'reg': 'name', 'name': 'state', 'state': 'reg'}.get(
                self.sort_mode, 'reg')
            self._apply_filter(reset_page=True)
            return True
        pl, pr = self._page_left_btn(), self._page_right_btn()
        if pl.collidepoint(e.pos) or pr.collidepoint(e.pos):
            d = -1 if pl.collidepoint(e.pos) else 1
            self.current_page = (self.current_page + d) % self.get_total_pages()
            return True
        if self._jump_btn().collidepoint(e.pos):
            self.jump_active = True
            self.jump_input = ""
            return True
        mids = self._page_items()
        for i, chk in enumerate(self._chk_rects):
            if chk.collidepoint(e.pos) and i < len(mids):
                mid = mids[i]
                on = not is_enabled(self.sim, mid)
                set_enabled(self.sim, mid, on)
                self.status = '%s：已%s' % (self._module(mid).name,
                                            '启用' if on else '停用')
                self._clear_row_cache()
                return True
        for i, rr in enumerate(self._row_rects):
            if rr.collidepoint(e.pos) and i < len(mids):
                mid = mids[i]
                self.si = MODULES.index(self._module(mid))
                self.status = open_module_settings(self.sim, mid)
                return True
        return False

    def _handle_jump(self, e) -> bool:
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                try:
                    n = int(self.jump_input)
                    self.current_page = max(0, min(self.get_total_pages() - 1, n - 1))
                except ValueError:
                    pass
                self.jump_active = False
            elif e.key == pygame.K_BACKSPACE:
                self.jump_input = self.jump_input[:-1]
            else:
                ch = getattr(e, 'unicode', '')
                if ch and ch.isdigit() and len(self.jump_input) < 3:
                    self.jump_input += ch
            return True
        if e.type == pygame.MOUSEBUTTONDOWN:
            self.jump_active = False
            return True
        return False

    # ── 绘制 ──
    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        r = self.bg_rect
        dark = self.dark_mode
        tc = SETTINGS_TEXT_LIGHT if dark else TXT
        if dark:
            self.draw_dark_panel(surface, r)
            self.draw_title(surface, self.title_dark, r, y_offset=15)
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=15)

        # 顶部工具条
        self.search_field.rect = self._search_rect()
        self.search_field.draw(surface)
        labels = {'reg': "排序:注册", 'name': "排序:名称", 'state': "排序:启用"}
        sort_surf = rt(f_s, labels.get(self.sort_mode, "排序:注册"), tc)
        if dark:
            self.draw_dark_button(surface, self._sort_btn(), sort_surf)
        else:
            self.draw_button(surface, self._sort_btn(), sort_surf, BUTTON_BG)

        mx, my = pygame.mouse.get_pos()
        mids = self._page_items()
        self._row_rects, self._chk_rects = [], []
        alpha = ROW_BORDER_ALPHA_DARK if dark else ROW_BORDER_ALPHA_LIGHT
        for i, mid in enumerate(mids):
            m = self._module(mid)
            y = r.y + TOP_OFFSET + i * ROW_H + 20
            row = pygame.Rect(r.x + 20, y, r.w - 40, ROW_H - 4)
            chk = pygame.Rect(row.right - CHK - 8, row.bottom - CHK - 8, CHK, CHK)
            # 点行 = 选中+进设置（不含右下角那块勾选框区）
            self._row_rects.append(pygame.Rect(row.x, row.y, chk.x - row.x, row.h))
            self._chk_rects.append(chk)
            if MODULES.index(m) == self.si:
                hl = pygame.Surface((row.w, row.h), pygame.SRCALPHA)
                hl.fill((70, 105, 165, 130) if dark else LIST_HL)
                surface.blit(hl, (row.x, row.y))
            elif row.collidepoint(mx, my):
                hl = pygame.Surface((row.w, row.h), pygame.SRCALPHA)
                hl.fill((70, 105, 165, 70) if dark else (200, 220, 255, 120))
                surface.blit(hl, (row.x, row.y))
            # 每行一圈淡白描边
            bd = pygame.Surface((row.w, row.h), pygame.SRCALPHA)
            pygame.draw.rect(bd, (255, 255, 255, alpha),
                             (0, 0, row.w, row.h), 1, ROW_BORDER_RADIUS)
            surface.blit(bd, (row.x, row.y))
            # 两段式文字
            surfs = self._row_surfs_cached(m, dark)
            surface.blit(surfs['name'], (row.x + 10, row.y + 5))
            surface.blit(surfs['desc'], (row.x + 10, row.y + 35))
            # 右下角勾选框
            on = is_enabled(self.sim, m.mid)
            pygame.draw.rect(surface, (30, 34, 44) if dark else (245, 247, 250), chk, 0, 4)
            pygame.draw.rect(surface, (150, 175, 210) if dark else (150, 158, 172), chk, 1, 4)
            if on:
                pygame.draw.line(surface, (90, 205, 120),
                                 (chk.x + 3, chk.centery),
                                 (chk.centerx - 1, chk.bottom - 4), 3)
                pygame.draw.line(surface, (90, 205, 120),
                                 (chk.centerx - 1, chk.bottom - 4),
                                 (chk.right - 3, chk.y + 4), 3)

        # 分页信息 + 箭头 + 跳页
        page_info = "第 {}/{} 页  共 {} 个".format(
            self.current_page + 1, self.get_total_pages(), len(self._filtered))
        surface.blit(rt(f_s, page_info, tc), (r.x + 20, r.y + r.height - 95))
        pl, pr = self._page_left_btn(), self._page_right_btn()
        pygame.draw.polygon(surface, tc, [(pl.right, pl.top), (pl.right, pl.bottom),
                                          (pl.left + 4, pl.centery)])
        pygame.draw.polygon(surface, tc, [(pr.left, pr.top), (pr.left, pr.bottom),
                                          (pr.right - 4, pr.centery)])
        jump_txt = rt(f_s, "跳页", (255, 255, 255))
        if dark:
            self.draw_dark_button(surface, self._jump_btn(), jump_txt)
        else:
            self.draw_button(surface, self._jump_btn(), jump_txt, BUTTON_BG)

        b = self._btn_rects()
        for key, txt in (('all', self.all_text), ('none', self.none_text)):
            if dark:
                self.draw_dark_button(surface, b[key], txt)
            else:
                self.draw_button(surface, b[key], txt, BUTTON_BG)
        if dark:
            self.draw_dark_button(surface, b['close'], self.close_text)
        else:
            self.draw_button(surface, b['close'], self.close_text, BUTTON_BG)
        if self.status:
            surface.blit(rt(f_s, self.status, tc),
                         (max(r.x + 210, b['close'].left - 320), b['close'].y + 6))

        if self.jump_active:
            ov = pygame.Surface((self.sim.screen_width, self.sim.screen_height), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 140 if dark else 100))
            surface.blit(ov, (0, 0))
            box = pygame.Rect(self.sim.screen_width // 2 - 100,
                              self.sim.screen_height // 2 - 30, 200, 40)
            pygame.draw.rect(surface, (35, 40, 54) if dark else (245, 247, 250), box, 0, 4)
            pygame.draw.rect(surface, (80, 110, 160), box, 2, 4)
            surface.blit(rt(f_m, self.jump_input or "_", tc), (box.x + 12, box.y + 8))
