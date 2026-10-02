# -*- coding: utf-8 -*-
"""**能玩吗** 端到端检查(轮次 150 收口自检, 一次性工具, 无副作用)。

前面的验收全部只测"物理与数据链路"(`verify_year` / `multi_year_check` 直接驱动
`core`) —— 它们**不经过应用层**。本检查反过来: 真启动完整 `TySim`(dummy SDL 头less),
切进 MODE_SIM, 开自然生成, 用**应用自己的** `_sim_advance()` 推进模拟钟, 然后看:
  ① 台风出不出来(`len(sim['tcs'])`);
  ② 出来的能不能被应用渲染(调 `_sim_advance` 之外的绘制路径);
  ③ 图层切换 / 重置 / 导出 b-deck 这些"玩"的动作会不会抛异常。

判据(全部必须成立才算"能玩"):
  · 20 个模拟日之后 `len(tcs) > 0`, 且至少 1 个 `not dead`;
  · `_sim_advance` 返回的推进时数 > 0;
  · **每个图层**逐个切换 + **真的渲染一帧**, 不抛异常且画面非全黑;
  · `_sim_reset()` 后回到空场;
  · 导出 b-deck 不抛异常, **且产物文件确实存在、非空**。

⚠ 轮次 153 修: 原来判据 ② 只调 `_sim_set_layer`(三行赋值)从不渲染, 判据 ⑤ 的
条件写成字面量 `True`(恒过)且从不查文件 —— "只验不崩"正是本仓库反复栽跟头的地方
(它同时漏掉了"图标 icon_alpha 恒 0"那种不抛异常的缺陷)。

用法: python tools/_check_playable.py
"""
import os
import sys
import traceback

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from app.ty_sim import TySim  # noqa: E402
from app.ty_sim_mixins.sim_mode_mixin import SIM_LAYER_IDS  # noqa: E402
from simulator.simcore import core as V  # noqa: E402

FAIL = []


def check(name, cond, detail=''):
    print('%-46s %s %s' % (name, 'PASS' if cond else '**FAIL**', detail), flush=True)
    if not cond:
        FAIL.append(name)


pygame.init()
screen = pygame.display.set_mode((800, 600))
app = TySim(screen)
app.md = app.MODE_SIM

# ── ① 进入模拟模式: 状态挂在 `sim_v4`, 由 _sim_reset() 建立 ──
try:
    app._sim_reset(msg=False)
except Exception:
    check('进入模拟模式(_sim_reset)不抛异常', False, traceback.format_exc()[-600:])
    raise SystemExit(1)
check('进入模拟模式(_sim_reset)不抛异常', True)
sim = app.sim_v4
check('有 sim 状态', sim is not None)
check('自然生成默认开启', bool(app.sim_natural_gen), 'sim_natural_gen=%s' % app.sim_natural_gen)

# ── ② 用应用自己的推进函数跑 20 个模拟日 ──
app.sim_playing = True
target_days = 20.0
total_adv = 0.0
frames = 0
try:
    while total_adv < target_days * 24.0 and frames < 60000:
        total_adv += float(app._sim_advance(0.05))
        frames += 1
except Exception:
    check('_sim_advance 20 天不抛异常', False, traceback.format_exc()[-500:])
else:
    check('_sim_advance 20 天不抛异常', True, '%d 帧 / 推进 %.1f 模拟时' % (frames, total_adv))

check('模拟钟真的走了', total_adv > 0, '推进 %.1f 时' % total_adv)

tcs = list(sim['tcs']) if sim else []
alive = [t for t in tcs if not t['dead']]
check('生成了台风', len(tcs) > 0, 'tcs=%d (存活 %d)' % (len(tcs), len(alive)))
check('有存活台风', len(alive) > 0)
if tcs:
    vs = [float(t['vmax']) for t in tcs]
    check('强度值合理(0-250kt)', all(0.0 <= v <= 250.0 for v in vs),
          'vmax %s..%s' % (min(vs), max(vs)))
    lons = [float(t['lon']) for t in tcs]
    lats = [float(t['lat']) for t in tcs]
    check('经纬度在合法域内', all(-180.0 <= x <= 360.0 for x in lons)
          and all(-90.0 <= y <= 90.0 for y in lats),
          'lon %s..%s lat %s..%s' % (min(lons), max(lons), min(lats), max(lats)))

# ── ③ 逐个图层切换, 并且**真的渲染一帧**(轮次 153: 原来只调 set_layer 没渲染) ──
app.sim_playing = False
bad = []
blank = []
for lid in SIM_LAYER_IDS:
    try:
        app._sim_set_layer(lid)
        screen.fill((0, 0, 0))
        app._sim_render(screen)
        # 渲染后画面不该是纯黑(说明这一层确实画了东西); track 单独看
        arr = pygame.surfarray.array3d(screen)
        if int(arr.sum()) == 0:
            blank.append(lid)
    except Exception:
        bad.append('%s: %s' % (lid, traceback.format_exc().splitlines()[-1]))
check('逐图层切换 + 渲染一帧不抛异常',
      not bad, '; '.join(bad) if bad else '共 %d 层' % len(SIM_LAYER_IDS))
check('每层渲染后画面非全黑', not blank, '全黑层=%s' % blank if blank else '')

# ── ④ 重置 ──
try:
    app._sim_reset(msg=False)
    check('重置后回到空场', len(app.sim_v4['tcs']) == 0, 'tcs=%d' % len(app.sim_v4['tcs']))
except Exception:
    check('重置后回到空场', False, traceback.format_exc()[-300:])

# ── ⑤ 导出 b-deck(会走 import_tc / stage_bdeck 那条链) ──
app.sim_playing = True
_n = 0
while _n < 4000:
    app._sim_advance(0.05)
    _n += 1
try:
    out = app._sim_export()
    # 轮次 153: 原来判据写成字面量 True(恒过), 只把返回值打印出来, 从不查文件。
    _ok = bool(out) and os.path.exists(str(out)) and os.path.getsize(str(out)) > 0
    check('导出 b-deck 且产物非空', _ok,
          'path=%r size=%s' % (out, os.path.getsize(str(out)) if out and
                               os.path.exists(str(out)) else 'N/A'))
except Exception:
    check('导出 b-deck 且产物非空', False, traceback.format_exc()[-400:])

print()
print('=' * 60)
if FAIL:
    print('结果: **%d 项失败** -> %s' % (len(FAIL), ', '.join(FAIL)))
    sys.exit(1)
print('结果: 全部通过 —— 模拟模式可以正常玩')
sys.exit(0)
