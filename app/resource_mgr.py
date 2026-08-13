from __future__ import annotations

import os
import math
import logging
from typing import Dict

import pygame

from .constants import (
    SUCAI_DIR, SOUND_DIR, f_s, f_m, f_l, f_name,
    MD_COLOR, STS, C5_L, C2_MINUS, C3_MINUS, C4_ST, WV,
    ICON_SET_DEFAULT,
    find_insensitive_path as fip
)

from .ocean_mgr import OceanAreaManager

logger = logging.getLogger(__name__)


class ResourceManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.images: Dict[str, pygame.Surface] = {}
        self.sounds: Dict[str, pygame.mixer.Sound] = {}
        self.ocean_areas = OceanAreaManager()
        self._icon_set = ICON_SET_DEFAULT
        self._load_all()

    @property
    def icon_set(self) -> str:
        return self._icon_set

    @icon_set.setter
    def icon_set(self, value: str) -> None:
        if value != self._icon_set:
            self._icon_set = value
            self._reload_icons()

    def _icon_dir(self) -> str:
        return os.path.join(SUCAI_DIR, self._icon_set)

    def _reload_icons(self) -> None:
        """切换图标集时只重载图标图片,避免音效被无谓地重复加载。"""
        self.images.clear()
        self._load_all(load_sounds=False)

    def _load_all(self, load_sounds: bool = True):
        icon_dir = self._icon_dir()
        categories = ['DB', 'EX', 'TD', 'TS', 'STS', 'C1', 'C2-', 'C2', 'C3-', 'C3', 'C4', 'C4-ST', 'C5',
                      'MD', 'SD', 'SS', 'LO', 'WV', 'C3_3', 'C4_3', 'C5_3']
        for cat in categories:
            ring_path = fip(os.path.join(icon_dir, f"{cat}_1.png"))
            center_path = fip(os.path.join(icon_dir, f"{cat}_2.png"))
            ring_img, center_img = None, None
            if ring_path:
                try: ring_img = pygame.image.load(ring_path).convert_alpha()
                except Exception as e: logger.warning(f"加载图标失败 {cat}_1: {e}")
            if center_path:
                try: center_img = pygame.image.load(center_path).convert_alpha()
                except Exception as e: logger.warning(f"加载图标失败 {cat}_2: {e}")
            self.images[f"{cat}_ring"] = ring_img or self._create_ring_icon(cat)
            self.images[f"{cat}_center"] = center_img or self._create_center_icon(cat)

        for sub, base, color in [('C2-', 'C2', C2_MINUS), ('C3-', 'C3', C3_MINUS), ('C4-ST', 'C4', C4_ST)]:
            ring_img = self.images.get(f"{base}_ring")
            if ring_img:
                self.images[f"{sub}_ring"] = self._recolor_icon(ring_img, color)
            center_img = self.images.get(f"{base}_center")
            if center_img:
                self.images[f"{sub}_center"] = center_img

        lf_map = {'C1': 'C1', 'C2-': 'C2', 'C2': 'C2', 'C3-': 'C3', 'C3': 'C3',
                  'C4': 'C4', 'C4-ST': 'C4', 'C5': 'C5',
                  'STS': 'STS', 'TS': 'TS', 'TD': 'TD', 'SD': 'SD', 'SS': 'SS',
                  'EX': 'EX', 'MD': 'MD', 'DB': 'DB', 'WV': 'WV'}
        for key, prefix in lf_map.items():
            for sfx in ('', '_2'):
                path = fip(os.path.join(icon_dir, f"landfall_{prefix}{sfx}.png"))
                if path:
                    try:
                        self.images[f"landfall_{key}{'_2' if sfx else '_1'}"] = \
                            pygame.image.load(path).convert_alpha()
                    except Exception as e: logger.warning(f"加载登陆图片失败 {key}{sfx}: {e}")

        if not load_sounds:
            return
        # 注意：C2-/C3-/C4-ST 等子级别没有独立音效文件，必须映射到主级别，
        # 否则以这些强度登陆时 get_sound 返回 None → 无声（如 100kt=C3- 登陆）。
        for strength, suffix in {'C1': 'C1', 'C2': 'C2', 'C2-': 'C2',
                                  'C3': 'C3', 'C3-': 'C3',
                                  'C4': 'C4', 'C4-ST': 'C4', 'C5': 'C5',
                                  'TS': 'TS', 'STS': 'TS', 'SS': 'SS',
                                  'TD': 'TD', 'SD': 'TD', 'DB': 'TD',
                                  'LO': 'TD', 'WV': 'TD',
                                  'EX': 'EX', 'MD': 'MD'}.items():
            path = os.path.join(SOUND_DIR, f"sound.landfall.{suffix}.ogg")
            if not os.path.exists(path):
                path = os.path.join(SUCAI_DIR, f"sound.landfall.{suffix}.ogg")
            if os.path.exists(path):
                try: self.sounds[strength] = pygame.mixer.Sound(path)
                except Exception:
                    logger.debug(f"加载音效失败: {path}", exc_info=True)

    def _create_ring_icon(self, cat):
        s = pygame.Surface((80, 80), pygame.SRCALPHA)
        colors = {'DB': (150, 150, 150), 'EX': (150, 200, 255), 'MD': MD_COLOR,
                  'LO': (150, 200, 100), 'STS': STS, 'C5': C5_L,
                  'C2-': C2_MINUS, 'C3-': C3_MINUS, 'C4-ST': C4_ST, 'WV': WV}
        c = colors.get(cat, (100, 150, 255))
        pygame.draw.circle(s, (*c, 200), (40, 40), 35, 5)
        if cat in ('C3_3', 'C4_3', 'C5_3'):
            return s
        lc = {'MD': (0, 200, 0, 220), 'LO': (100, 200, 100, 220),
              'STS': (*STS, 220)}.get(cat,
              (50, 100, 200, 220) if cat != 'C5' else (*C5_L, 220))
        for i in range(0, 360, 45):
            a = math.radians(i)
            cos_a = math.cos(a)
            sin_a = math.sin(a)
            x1, y1 = 40 + 30 * cos_a, 40 + 30 * sin_a
            x2, y2 = 40 + 25 * cos_a, 40 + 25 * sin_a
            pygame.draw.line(s, lc, (x1, y1), (x2, y2), 2)
        return s

    @staticmethod
    def _create_center_icon(cat):
        s = pygame.Surface((60, 60), pygame.SRCALPHA)
        pygame.draw.circle(s, (255, 255, 255, 240), (30, 30), 20)
        pygame.draw.circle(s, (50, 50, 50, 240), (30, 30), 4)
        return s

    @staticmethod
    def _recolor_icon(surface, target_color):
        """按亮度重着色(仅在图标集切换时执行一次,逐像素可接受)。"""
        target = target_color[:3]
        luts = [[int(t * (v / 255.0)) for v in range(256)] for t in target]
        out = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        w, h = surface.get_size()
        for y in range(h):
            for x in range(w):
                r, g, b, a = surface.get_at((x, y))
                if a == 0:
                    continue
                lum = max(r, g, b)
                if lum == 0:
                    out.set_at((x, y), (0, 0, 0, a))
                else:
                    out.set_at((x, y), (luts[0][lum], luts[1][lum], luts[2][lum], a))
        return out

    def get_image(self, name): return self.images.get(name)
    def get_sound(self, name): return self.sounds.get(name)
    def get_landfall_images(self, s):
        return self.images.get(f"landfall_{s}_1"), self.images.get(f"landfall_{s}_2")
