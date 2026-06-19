# py/new_typhoon_dialog.py
import os
import re
import pygame
from .constants import f_s, f_m, rt, TXT, TYPHOON_DIR, BUTTON_BORDER, BUTTON_DISABLED
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

        self.fields.clear()
        for i, label in enumerate(self.labels):
            rect = (start_x + i * (fw + sp), dy + 25, fw, fh)
            f = InputField(rect, label=label, max_length=30)
            self.fields.append(f)

        self.fields[3].set_text("")
        self.fields[0].activate()
        self.current_field = 0

    def deactivate(self):
        super().deactivate()
        self.fields.clear()

    def handle_event(self, e: pygame.event.Event) -> bool:
        if not self.active:
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            x, y = e.pos
            dy = self.sim.screen_height - 150
            if pygame.Rect(self.sim.screen_width // 2 - 90, dy + 70, 80, 30).collidepoint(x, y):
                self._create()
                self.deactivate()
                return True
            if pygame.Rect(self.sim.screen_width // 2 + 10, dy + 70, 80, 30).collidepoint(x, y):
                self.deactivate()
                return True
            for i, field in enumerate(self.fields):
                if field.rect.collidepoint(e.pos):
                    for f in self.fields:
                        f.deactivate()
                    field.activate()
                    self.current_field = i
                    return True

        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()
                return True
            if e.key == pygame.K_RETURN:
                self._create()
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

    def _create(self):
        name = self.fields[0].get_text().strip()
        number = self.fields[1].get_text().strip()
        start_time = self.fields[2].get_text().strip()
        basin = self.fields[3].get_text().strip().upper() or "WP"
        filename = self.fields[4].get_text().strip()

        if not name or not number or not start_time:
            self.sim.show_error("请填写台风名称、编号和起始时间")
            return

        year = start_time[:4] if len(start_time) >= 4 else "2000"

        if not filename:
            filename = f"{year} {name} {basin.lower()}{number}{year}"
        safe = re.sub(r'[^a-zA-Z0-9_\- ]', '', filename) or "typhoon"
        base = safe
        counter = 1
        while os.path.exists(os.path.join(TYPHOON_DIR, base + ".txt")):
            base = f"{safe}_{counter}"
            counter += 1
        filepath = os.path.join(TYPHOON_DIR, base + ".txt")

        try:
            open(filepath, 'w', encoding='utf-8').close()
        except Exception as e:
            self.sim.show_error(f"创建文件失败: {e}")
            return

        ty = Typhoon(basin, number)
        ty.cust = ty.sname = name
        ty.basin = basin
        ty.filepath = filepath
        ty.sim = self.sim
        ty.start_time = start_time
        self.sim.tys.append(ty)
        self.sim.cti = len(self.sim.tys) - 1
        self.sim.edit_typhoon = ty
        self.sim.md = "edit"

    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        dy = self.sim.screen_height - 150
        self.draw_background(surface, pygame.Rect(0, dy - 10, self.sim.screen_width, 120))

        for field in self.fields:
            field.draw(surface)

        cr = pygame.Rect(self.sim.screen_width // 2 - 90, dy + 70, 80, 30)
        ca = pygame.Rect(self.sim.screen_width // 2 + 10, dy + 70, 80, 30)
        self.draw_button(surface, cr, self.confirm_text, BUTTON_BORDER)
        self.draw_button(surface, ca, self.cancel_text, BUTTON_DISABLED)