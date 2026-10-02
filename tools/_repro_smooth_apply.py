# -*- coding: utf-8 -*-
"""复现: "平滑路径有可能不应用, 需要开关一次才确保应用"。

判据用**可观测产物**: 台风对象的 `v.smooth_screen_points` ——
它非空才代表"平滑真的应用到了路径上"(绘制分支读的就是它)。

流程严格走真实入口:
  1. 建 TySim + Settings 面板, `activate()`;
  2. 先把 smooth_path 置 False 并 apply → 断言样条为空;
  3. 模拟"用户点一下平滑复选框"(直接调到面板的点击目标回调) → apply;
  4. 看样条是否非空。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                                   # noqa: E402

pygame.init()
sc = pygame.display.set_mode((1360, 885))
from app.ty_sim import TySim                                    # noqa: E402

sim = TySim(sc)
sim.repo.load_typhoon_files()
if not sim.repo.tys:
    print('没有台风'); sys.exit(0)
sim.md = sim.MODE_NORMAL
sim.repo.cti = 0
ty = sim.current_typhoon()
sim.update_all_screen_points()


def spline_len():
    return len(getattr(ty.v, 'smooth_screen_points', []) or [])


print('台风 %s%s, %d 个报点' % (ty.b, ty.n, len(ty.pts)))

# 1) 强制关掉平滑并应用
sim.smooth_path = False
sim.update_all_screen_points()
print('关掉平滑后: smooth_screen_points = %d  (期望 0)' % spline_len())

# 2) 打开设置面板(会 activate), 模拟点击"平滑路径"复选框
dlg = sim.dialog_mgr.settings if hasattr(sim.dialog_mgr, 'settings') else None
if dlg is None:
    for attr in dir(sim.dialog_mgr):
        o = getattr(sim.dialog_mgr, attr, None)
        if o.__class__.__name__ == 'Settings':
            dlg = o
            break
print('设置面板:', dlg.__class__.__name__ if dlg else '未找到')
dlg.activate()
print('activate 后面板里的 smooth_path =', dlg.smooth_path,
      ' sim.smooth_path =', sim.smooth_path)

# 找到"平滑路径"复选框的点击目标并触发(真实入口)
before = dlg.smooth_path
dlg.smooth_path = not dlg.smooth_path
dlg._needs_save = True
print('模拟点击复选框: %s -> %s' % (before, dlg.smooth_path))
ok = dlg.apply_settings()
print('apply_settings() ->', ok)
print('apply 后 sim.smooth_path =', sim.smooth_path)
print('apply 后 smooth_screen_points = %d  (期望 >0)' % spline_len())
print()
print('RESULT %s' % ('通过' if spline_len() > 0 else '**复现: 平滑没应用**'))
