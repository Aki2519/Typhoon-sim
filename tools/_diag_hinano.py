# -*- coding: utf-8 -*-
"""诊断: `typhoon/bsh141989 Hinano.dat` 为什么没进台风列表。

分四步定位, 每一步都打印事实而不是猜:
  1. 文件在不在扫描目录里、格式识别成什么;
  2. **单独** parse 它, 得到几个报点(pts) —— 0 个就说明是解析/内容问题;
  3. 走完整 `load_typhoon_files()`, 看它在不在结果里;
  4. 若不在, 判断是不是被**洋区过滤**(`basin_filter_enabled` + `ace_limit_mode`)滤掉了
     —— 这是 `load_typhoon_files` 末尾那段唯一的删减逻辑。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                                   # noqa: E402

pygame.init()
screen = pygame.display.set_mode((1200, 800))
from app.ty_sim import TySim                                    # noqa: E402
from app.constants import TYPHOON_DIR                           # noqa: E402

sim = TySim(screen)
repo = sim.data_repo if hasattr(sim, 'data_repo') else getattr(sim, 'repo', None)
print('TYPHOON_DIR = %r  (绝对路径 %s)' % (TYPHOON_DIR, os.path.abspath(TYPHOON_DIR)))

fp = None
for root, _d, files in os.walk(TYPHOON_DIR):
    for fn in files:
        if 'hinano' in fn.lower():
            fp = os.path.join(root, fn)
print()
print('--- 1) 文件 ---')
print('  找到: %s' % fp)
if fp is None:
    print('  !! 扫描目录里没有 Hinano —— 那就是"不在目录里"')
    sys.exit(0)
print('  大小: %d 字节' % os.path.getsize(fp))
with open(fp, encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
print('  前 3 行: %r' % [ln.strip() for ln in lines[:3]])
print('  格式识别: %s' % repo.detect_format(fp, lines))

print()
print('--- 2) 单独 parse ---')
ty = repo.parse_typhoon_file(fp, add_to_list=False)
if ty is None:
    print('  parse 返回 None')
else:
    print('  b=%r n=%r name=%r cust=%r pts=%d' % (
        getattr(ty, 'b', None), getattr(ty, 'n', None),
        getattr(ty, 'name', None), getattr(ty, 'cust', None), len(ty.pts or [])))
    if ty.pts:
        print('  首点 %r' % (ty.pts[0],))
        print('  末点 %r' % (ty.pts[-1],))

print()
print('--- 3) 全量加载后它在不在列表 ---')
repo.load_typhoon_files()
hit = [t for t in repo.tys if 'hinano' in str(getattr(t, 'name', '')).lower()
       or (getattr(t, 'n', None) and '989' in str(getattr(t, 'n', '')))]
print('  repo.tys 共 %d 条' % len(repo.tys))
print('  备份 _all_tys_backup 共 %d 条' % len(repo._all_tys_backup))
hit_bak = [t for t in repo._all_tys_backup
           if 'hinano' in str(getattr(t, 'name', '')).lower()]
print('  在 repo.tys 里匹配到: %s' % [(t.b, t.n, t.name) for t in hit])
print('  在 _all_tys_backup 里匹配到: %s' % [(t.b, t.n, t.name) for t in hit_bak])

print()
print('--- 4) 洋区过滤参数 ---')
print('  basin_filter_enabled = %r' % getattr(sim, 'basin_filter_enabled', None))
print('  ace_limit_mode       = %r' % getattr(sim.cfg, 'ace_limit_mode', None))
print('  ace_limit_basin      = %r' % getattr(sim.cfg, 'ace_limit_basin', None))
if hit_bak and not hit:
    print('  => 结论: 被**洋区过滤**滤掉了(备份里有、过滤后没了)')
elif not hit_bak:
    print('  => 结论: **根本没解析进列表**(备份里也没有)')
