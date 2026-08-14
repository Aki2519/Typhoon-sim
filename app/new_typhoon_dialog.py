# py/new_typhoon_dialog.py
from __future__ import annotations

import os
import re
import pygame
from .constants import f_s, rt, TYPHOON_DIR, BUTTON_BORDER, BUTTON_DISABLED
from .typhoon import Typhoon
from .input_field import InputField
from .dialog_base import Dialog


class NewTyphoonDialog(Dialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.labels = ['台风名称:', '低压编号:', '起始时间 (YYYYMMDDHH):',
                       '生成洋区 (默认自动检测):', '文件名 (可选):']
        self.fields: list[InputField] = []
        self.confirm_text = rt(f_s, "确认", (255, 255, 255))
        self.cancel_text = rt(f_s, "取消", (255, 255, 255))

    def activate(self):
        super().activate()
        fw, fh, sp = 200, 30, 20
        dy = self.sim.screen_height - 150
        start_x = (self.sim.screen_width - (5 * fw + 4 * sp)) // 2

        self.bg_rect = pygame.Rect(0, dy - 10, self.sim.screen_width, 120)

        self.fields.clear()
        for i, label in enumerate(self.labels):
            rect = (start_x + i * (fw + sp), dy + 25, fw, fh)
            f = InputField(rect, label=label, max_length=30, dark=self.dark_mode)
            self.fields.append(f)

        self.fields[0].activate()
        self.current_field = 0

    def deactivate(self):
        for f in self.fields:
            f.deactivate()
        self.fields.clear()
        super().deactivate()

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            dy = self.sim.screen_height - 150
            if pygame.Rect(self.sim.screen_width // 2 - 90, dy + 70, 80, 30).collidepoint(x, y):
                if self._create():
                    self.deactivate()
                return True
            if pygame.Rect(self.sim.screen_width // 2 + 10, dy + 70, 80, 30).collidepoint(x, y):
                self.deactivate()
                return True
            for i, field in enumerate(self.fields):
                if field.rect.collidepoint(e.pos):
                    for f in self.fields:
                        f.deactivate()
                    field.activate_at(e.pos[0])
                    self.current_field = i
                    return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_RETURN:
                if self._create():
                    self.deactivate()
                return True
            if e.key in (pygame.K_TAB, pygame.K_KP_ENTER):
                idx = next((i for i, f in enumerate(self.fields) if f.active), -1)
                delta = -1 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                nxt = (idx + delta) % len(self.fields) if idx != -1 else 0
                if idx != -1:
                    self.fields[idx].deactivate()
                self.fields[nxt].activate()
                self.current_field = nxt
                return True

        for i, field in enumerate(self.fields):
            if field.handle_event(e):
                if field.active:
                    self.current_field = i
                return True
        return False

    def _create(self) -> bool:
        name = self.fields[0].get_text().strip()
        number = self.fields[1].get_text().strip()
        start_time = self.fields[2].get_text().strip()
        basin = self.fields[3].get_text().strip().upper() or "WP"
        filename = self.fields[4].get_text().strip()

        if not name or not number or not start_time:
            self.sim.show_error("请填写台风名称、编号和起始时间")
            return False

        if not re.fullmatch(r'\d{10}', start_time):
            self.sim.show_error("起始时间格式必须为 YYYYMMDDHH")
            return False
        # 起始时间必须是合法日期时间
        try:
            from datetime import datetime as _dt
            _dt.strptime(start_time, "%Y%m%d%H")
        except ValueError:
            self.sim.show_error("起始时间不是合法日期时间")
            return False

        if not number.isdigit():
            self.sim.show_error("低压编号必须为数字")
            return False

        year = start_time[:4]

        if not filename:
            if basin == "WP":
                filename = f"{year} {name} {basin.lower()}{number}{year}"
            else:
                filename = f"b{basin.lower()}{number}{year} {name}"
        # 保留中文(\w 含 CJK)/字母/数字/常用符号,避免中文名被静默剔除
        safe = re.sub(r'[^\w\- ]', '', filename, flags=re.UNICODE) or "typhoon"
        base = safe
        counter = 1
        while os.path.exists(os.path.join(TYPHOON_DIR, base + ".txt")):
            base = f"{safe}_{counter}"
            counter += 1
        filepath = os.path.join(TYPHOON_DIR, base + ".txt")

        try:
            # 写入占位注释头(名称/编号持久化),重载后不因无报点而消失
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"# NEW {basin} {number} {name}\n")
        except Exception as e:
            self.sim.show_error(f"创建文件失败: {e}")
            return False

        # b 恒为 "WP"(与 parse_typhoon_file 的构造一致, 见 data_repo.py:171);
        # 真实盆域存 basin, 保证 cfg.tn 键 f"{b}{n}" 跨重载一致(R4V-1 发现)。
        ty = Typhoon("WP", number)
        ty.cust = ty.sname = name
        ty.basin = basin
        ty.filepath = filepath
        ty.sim = self.sim
        ty.start_time = start_time
        self.sim.tys.append(ty)
        # _all_tys_backup 是"原始未过滤全集"备份;用 is not None 判定,避免备份为空
        # (首次新建/空工作区)时新台风被漏进备份,导致还原/重载后失衡
        if self.sim.repo._all_tys_backup is not None:
            self.sim.repo._all_tys_backup.append(ty)
        self.sim.cti = len(self.sim.tys) - 1
        self.sim.edit_typhoon = ty
        self.sim.md = "edit"
        # R3-6: 新建台风立即刷新屏幕点与盆域排序,否则拖动/切换顺序异常
        try:
            self.sim.update_all_screen_points()
            if hasattr(self.sim.repo, '_sort_by_basin'):
                self.sim.repo._sort_by_basin()
        except Exception:
            pass
        return True

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dy = self.sim.screen_height - 150
        r = pygame.Rect(0, dy - 10, self.sim.screen_width, 120)
        dark = self.dark_mode
        if dark:
            self.draw_dark_panel(surface, r)
        else:
            self.draw_background(surface, r)

        for field in self.fields:
            field.draw(surface)

        cr = pygame.Rect(self.sim.screen_width // 2 - 90, dy + 70, 80, 30)
        ca = pygame.Rect(self.sim.screen_width // 2 + 10, dy + 70, 80, 30)
        if dark:
            self.draw_dark_button(surface, cr, self.confirm_text)
            self.draw_dark_button(surface, ca, self.cancel_text)
        else:
            self.draw_button(surface, cr, self.confirm_text, BUTTON_BORDER)
            self.draw_button(surface, ca, self.cancel_text, BUTTON_DISABLED)