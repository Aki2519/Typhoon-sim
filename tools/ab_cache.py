# -*- coding: utf-8 -*-
"""缓存上限 A/B（内存 vs 帧时间）。

每个配置**另起进程**跑（RSS 才准），每组：预热 → N 轮取中位 → 报 RSS。
真正的内存大头是 pygame Surface / cv2 的 C 内存，tracemalloc 量不到，
只能这样比。

用法：
    python tools/ab_cache.py --date 2026-08-25 --rounds 6
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (标签, 原生 MB, 表面 MB, 条数上限)
CONFIGS = [
    ('基线 24/16/240', 24, 16, 240),
    ('砍 12/8/90', 12, 8, 90),
    ('砍 8/6/60', 8, 6, 60),
    ('加大 40/32/240', 40, 32, 240),
]

RUNNER = r'''
import os, sys, time, gc, statistics, datetime, json
os.environ.setdefault('SDL_VIDEODRIVER','dummy'); os.environ.setdefault('SDL_AUDIODRIVER','dummy')
sys.path.insert(0, ROOT)
import pygame; pygame.init(); pygame.display.set_mode((1600,1000))
from app.ty_sim import TySim
import psutil; P = psutil.Process()
s = TySim(pygame.display.set_mode((1600,1000))); s.md = s.MODE_SEASON; s.dark_mode = True
s.season_ctrl.jump_to(datetime.datetime(*DATE)); s._sync_season_state()
s.playback_ctrl.update_all(0.0, 0.05, False, s.season_ctrl); s.update(0.05)
act = len([t for t in s.tys if getattr(t,'act',False)])
surf = pygame.Surface((1600,1000))
for _ in range(20): s.update(1/60); s.renderer.draw(surf)
gc.collect(); rss0 = P.memory_info().rss
rs = []
for _ in range(ROUNDS):
    a = time.perf_counter()
    for _ in range(25): s.update(1/60); s.renderer.draw(surf)
    rs.append((time.perf_counter()-a)/25*1000)
print(json.dumps({'act': act, 'median': statistics.median(rs), 'min': min(rs),
                  'rounds': rs, 'rss': P.memory_info().rss}))
'''


def run_one(date, rounds, native, surf, items):
    env = dict(os.environ)
    env.update(TSIM_NATIVE_MB=str(native), TSIM_SURF_MB=str(surf),
               TSIM_CACHE_MAX=str(items), TSIM_CONFIG_FILE=os.path.join(
                   tempfile.gettempdir(), 'tsim_ab_%d_%d_%d.json' % (native, surf, items)))
    code = ('ROOT=%r\nDATE=(%d,%d,%d)\nROUNDS=%d\n' % (
        ROOT, date.year, date.month, date.day, rounds)) + RUNNER
    r = subprocess.run([sys.executable, '-c', code], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=1800)
    for line in reversed((r.stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except Exception:
                pass
    print('  ! 失败:', (r.stderr or '')[-400:])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='2026-08-25')
    ap.add_argument('--rounds', type=int, default=6)
    args = ap.parse_args()
    from datetime import datetime
    date = datetime.strptime(args.date, '%Y-%m-%d')
    print('A/B：%s  %d 轮，另起进程' % (date.date(), args.rounds))
    print('%-18s %8s %8s %10s  %s' % ('配置', '中位ms', 'fps', 'RSS', '各轮'))
    rows = []
    for label, n, sf, it in CONFIGS:
        res = run_one(date, args.rounds, n, sf, it)
        if not res:
            continue
        rows.append((label, res))
        print('%-18s %8.1f %8.1f %7.0fMB  %s' % (
            label, res['median'], 1000.0 / res['median'], res['rss'] / 1e6,
            ' '.join('%.0f' % v for v in res['rounds'])))
    if rows:
        base = rows[0][1]['median']
        print('\n相对基线：')
        for label, res in rows[1:]:
            print('  %-18s 帧时间 %+5.1f%%   RSS %+6.0f MB' % (
                label, (res['median'] / base - 1) * 100,
                (res['rss'] - rows[0][1]['rss']) / 1e6))


if __name__ == '__main__':
    main()
