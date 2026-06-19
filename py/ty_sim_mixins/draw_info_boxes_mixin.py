# py/ty_sim_mixins/draw_info_boxes_mixin.py
"""风季台风信息框 Mixin。台风结束时信息框立即消失。"""
import pygame
from ..constants import (
    f_s, rt, TXT,
    INFO_BOX_BG, INFO_BOX_BORDER,
)


class TySimDrawInfoBoxesMixin:
    """台风季模式下的多台风信息框。"""

    def _render_info_box(self, ty, bw: int, bh: int) -> pygame.Surface:
        box = pygame.Surface((bw, bh), pygame.SRCALPHA)
        pygame.draw.rect(box, INFO_BOX_BG, (0, 0, bw, bh), 0, 8)
        pygame.draw.rect(box, INFO_BOX_BORDER, (0, 0, bw, bh), 2, 8)

        fp = ty.pts[0]
        lp = ty.pts[-1]
        tyy = fp['t'][:4] if len(fp['t']) >= 4 else "未知"
        tn = self.get_display_name(ty)
        l1 = rt(f_s, f"{ty.b}{ty.n} {tyy} {tn}", TXT, bw - 20)
        box.blit(l1, (10, 5))

        st = fp['t']
        et = lp['t']
        sf = f"{st[4:6]}/{st[6:8]}" if len(st) >= 8 else "未知"
        ef = f"{et[4:6]}/{et[6:8]}" if len(et) >= 8 else "未知"
        if len(st) >= 8 and len(et) >= 8:
            sm, sd = int(st[4:6]), int(st[6:8])
            em, ed = int(et[4:6]), int(et[6:8])
            td = (em - sm) * 30 + (ed - sd)
            ti = f"{sf}-{ef} ({td}天)"
        else:
            ti = f"{sf}-{ef}"
        l2 = rt(f_s, ti, TXT, bw - 20)
        box.blit(l2, (10, 25))

        valid_winds = [p['w'] for p in ty.pts if p['st'].upper() not in ['MD', 'SS', 'SD', 'EX', 'LO']]
        max_wind = max(valid_winds) if valid_winds else 0
        cp = ty.cp()
        current_wind = cp['w'] if cp else "?"
        l3 = rt(f_s, f"巅峰:{max_wind}kt 实时:{current_wind}kt", TXT, bw - 20)
        box.blit(l3, (10, 45))

        current_ace = ty.cace if cp else 0.0
        l4 = rt(f_s, f"总ACE:{ty.tace:.4f} 实时ACE:{current_ace:.4f}", TXT, bw - 20)
        box.blit(l4, (10, 65))
        return box

    def draw_season_info_boxes(self, surface: pygame.Surface) -> None:
        # 仅活跃台风显示信息框（act=True, ss=True, sf=False）
        active_typhoons = [t for t in self.tys if t.act and t.ss and not t.sf]

        # 清理已结束的台风 slot（立即释放，不淡出）
        ended = [ty for ty in list(self.info_box_slots.keys())
                 if ty not in active_typhoons]
        for ty in ended:
            slot = self.info_box_slots.pop(ty)
            self.info_box_free_slots.append(slot)
            self.info_box_free_slots.sort()

        # 分配新 slot
        for ty in active_typhoons:
            if ty not in self.info_box_slots and self.info_box_free_slots:
                slot = self.info_box_free_slots.pop(0)
                self.info_box_slots[ty] = slot

        bw, bh = 280, 90
        bpr = 3
        start_x = 180
        start_y = 10
        spacing_x, spacing_y = 10, 10

        for ty, slot in self.info_box_slots.items():
            if ty not in active_typhoons:
                continue
            r = slot // bpr
            c = slot % bpr
            x = start_x + c * (bw + spacing_x)
            y = start_y + r * (bh + spacing_y)

            box = self._render_info_box(ty, bw, bh)
            surface.blit(box, (x, y))