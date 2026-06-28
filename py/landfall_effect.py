# py/landfall_effect.py
"""登陆效果类。"""
from __future__ import annotations

import math
import pygame
from typing import Callable, Optional


class LandfallEffect:
    def __init__(self, strength: str, lon: float, lat: float,
                 img1: pygame.Surface, img2: pygame.Surface,
                 start_time: float,
                 latlon_to_screen_func: Callable[[float, float], tuple]) -> None:
        self.strength = strength
        self.lon = lon
        self.lat = lat
        self.img1 = img1
        self.img2 = img2
        self.start_time = start_time
        self.latlon_to_screen = latlon_to_screen_func
        self._flash_alpha: int = 255
        self._ring_alpha: int = 255

    def update(self, current_time: float) -> bool:
        elapsed = (current_time - self.start_time) / 1000.0
        if elapsed > 2.0:
            return False
        self._flash_alpha = max(0, int(255 * (1.0 - elapsed / 1.0)))
        self._ring_alpha = max(0, int(255 * (1.0 - elapsed / 2.0)))
        return True

    def draw(self, surface: pygame.Surface, current_time: float) -> None:
        x, y = self.latlon_to_screen(self.lat, self.lon)
        if self._flash_alpha > 0 and self.img2:
            flash = self.img2.copy()
            flash.set_alpha(self._flash_alpha)
            r = flash.get_rect(center=(x, y))
            surface.blit(flash, r)
        if self._ring_alpha > 0 and self.img1:
            ring = self.img1.copy()
            ring.set_alpha(self._ring_alpha)
            elapsed = (current_time - self.start_time) / 1000.0
            angle = elapsed * 360 % 360
            rotated = pygame.transform.rotate(ring, angle)
            r = rotated.get_rect(center=(x, y))
            surface.blit(rotated, r)
