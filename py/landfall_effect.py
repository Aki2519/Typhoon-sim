# py/landfall_effect.py
import pygame
from typing import Tuple

class LandfallEffect:
    def __init__(self,
                 strength: str,
                 lon: float,
                 lat: float,
                 img1: pygame.Surface,
                 img2: pygame.Surface,
                 start_time: int,
                 sim: 'TySim'):
        self.strength = strength
        self.lon = lon          # 存储经纬度
        self.lat = lat
        self.img1 = img1
        self.img2 = img2
        self.start_time = start_time
        self.sim = sim
        self.duration1 = 5000   # 第一个特效持续时间（毫秒）
        self.duration2 = 2000   # 第二个特效持续时间（毫秒）

    def update(self, current_time: int) -> bool:
        """返回True表示特效还未结束"""
        elapsed = current_time - self.start_time
        return elapsed <= self.duration1 or elapsed <= self.duration2

    def draw(self, surface: pygame.Surface, current_time: int):
        """根据经纬度实时计算屏幕坐标绘制"""
        elapsed = current_time - self.start_time
        # 获取当前屏幕坐标
        x, y = self.sim.latlon_to_screen(self.lat, self.lon)

        if elapsed <= self.duration1:
            # 第一个特效：渐隐的旋转圆环
            if elapsed <= 2000:
                scale = 1.0
            else:
                scale = 1.0 - (elapsed - 2000) / 3000
                scale = max(0.0, scale)
            total_scale = 0.75 * scale
            angle = (elapsed / 1000.0) * 360 % 360
            if total_scale > 0:
                orig_w, orig_h = self.img1.get_size()
                new_w = int(orig_w * total_scale)
                new_h = int(orig_h * total_scale)
                if new_w > 0 and new_h > 0:
                    scaled = pygame.transform.smoothscale(self.img1, (new_w, new_h))
                    rotated = pygame.transform.rotate(scaled, angle)
                    rect = rotated.get_rect(center=(x, y))
                    surface.blit(rotated, rect)

        if elapsed <= self.duration2:
            # 第二个特效：渐隐的闪光
            alpha = int(255 * (1.0 - elapsed / self.duration2))
            alpha = max(0, min(255, alpha))
            img2_copy = self.img2.copy()
            img2_copy.set_alpha(alpha)
            rect = img2_copy.get_rect(center=(x, y))
            surface.blit(img2_copy, rect)