# -*- coding: utf-8 -*-
"""诊断: 模拟模式里"扰动图标消失、路径也看不到、只剩一个点在动"。

怀疑的机制(从代码读出来的, 现在要**实测**):
  1. `_sim_render` 每帧把 `ty.pts` 截断到 `ci+1` 个点(只画已播过的部分);
  2. `draw_typhoon_info` 的"图标淡出"用 `n_pts = len(ty.pts)` 判"是否已到末点":
        if ty.ci >= n_pts - 2 and n_pts >= 2:
            progress = 1.0 if ty.ci >= n_pts - 1 else 0.0
            icon_alpha = 255 * (1 - progress)
     截断后 n_pts == ci+1 ⇒ `ci >= ci-1` 恒真、`ci >= ci` 恒真 ⇒ progress=1 ⇒ **alpha=0**。
  3. 路径 `draw_typhoon` 用同一份被截断的 `ty.pts` ⇒ 点数 = ci+1:
     ci=0 时只有 1 个点(**单点 = 那个"白点"**), ci>=1 时才是折线。

本探针驱动真实应用层(`TySim` + `_sim_render`), 逐系统打印这些量并**核对淡出算式**。
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from app.ty_sim import TySim  # noqa: E402


def fade_icon_alpha(app, ty):
    """**直接调用产品的真实实现**, 不复制算式。

    ⚠ 轮次 153 修: 原来这里逐字抄了一份 `_draw_icon_mixin.draw_typhoon_info` 的
    淡出算式。抄件会与真实实现**漂移**(真实实现改了而副本没改 ⇒ 本工具得出假结论),
    而这个项目历史上已经因此栽过。`TySim` 的 MRO 上就有 `_end_fade_alpha`, 直接调。

    返回值: `_end_fade_alpha` 返回 None 表示"不适用/不淡出", 对应绘制时的
    默认 `icon_alpha = 255`。
    """
    if not getattr(app, 'fade_typhoon', True):
        return 255
    a = app._end_fade_alpha(ty)
    return 255 if a is None else int(a)


def main():
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    app = TySim(screen)
    app.md = app.MODE_SIM
    app._sim_reset(msg=False)
    print('fade_typhoon 设置 =', getattr(app, 'fade_typhoon', None))
    print('sim_layers =', app.sim_layers)
    print()

    app.sim_playing = True
    rows = []
    for step in range(4000):
        app._sim_advance(0.05)
        if step % 400 != 0:
            continue
        sim = app.sim_v4
        disp = app._sim_display_sim() or sim
        # 走一遍真实渲染路径(会做截断)
        app._sim_render(screen)
        # ⚠ 轮次 153 修: 原来只取 `disp['tcs'][:6]`。`sim['tcs']` 是**追加序**且
        # 死亡台风会保留 120 天, 所以前 6 个恰好是**最老(多半已死)**的那批,
        # 而"存活系统图标是否可见"这个判据需要看的是**存活**的那些 ⇒ 原采样会失明。
        # 现在遍历全部, 只在打印时截断行数。
        for tc in disp['tcs']:
            ty = app.sim_path_tys.get(tc['id'])
            if ty is None:
                continue
            rows.append({
                'step': step,
                'id': tc['id'],
                'disturb': bool(tc.get('disturb')),
                'dead': bool(tc.get('dead')),
                'vmax': round(float(tc['vmax']), 1),
                'phase': tc.get('phase'),
                'track_n': len(tc['track']),
                'total_n': int(getattr(ty, 'track_total_n', 0) or 0),
                'pts_after_trunc': len(ty.pts),
                'ci': int(ty.ci),
                'ipos': ty.v.ipos is not None,
                'icon_alpha': fade_icon_alpha(app, ty),
            })

    print('%-6s %-4s %-7s %-6s %-6s %-7s %-8s %-7s %-4s %-6s %-6s %s'
          % ('step', 'id', 'disturb', 'dead', 'vmax', 'phase', 'track_n',
             'total_n', 'ci', 'pts', 'ipos', 'icon_alpha'))
    for r in rows[:60]:                      # 只截打印, 统计用全量
        print('%-6d %-4d %-7s %-6s %-6.1f %-7s %-8d %-7d %-4d %-6d %-6s %d'
              % (r['step'], r['id'], r['disturb'], r['dead'], r['vmax'],
                 r['phase'], r['track_n'], r['total_n'], r['ci'],
                 r['pts_after_trunc'], r['ipos'], r['icon_alpha']))
    if len(rows) > 60:
        print('... (共 %d 条, 上面只印前 60 条)' % len(rows))

    if rows:
        live0 = [r for r in rows if not r['dead'] and r['icon_alpha'] == 0]
        dead0 = [r for r in rows if r['dead'] and r['icon_alpha'] == 0]
        live_ok = [r for r in rows if not r['dead'] and r['icon_alpha'] > 0]
        print()
        print('样本 %d 条' % len(rows))
        print('  存活 & alpha=0  : %-3d  <-- **必须是 0**, 否则就是缺陷' % len(live0))
        print('  存活 & alpha>0  : %-3d' % len(live_ok))
        print('  已死亡 & alpha=0: %-3d  (设计行为: 图标淡出, 与 fade_typhoon 设置一致)'
              % len(dead0))
        if live0:
            print('  !! 仍有存活系统图标不可见: %s'
                  % [(r['id'], r['ci'], r['track_n']) for r in live0[:6]])

    if rows:
        zero = [r for r in rows if r['icon_alpha'] == 0]
        nz = [r for r in rows if r['icon_alpha'] > 0]
        print()
        print('采样 %d 条: icon_alpha=0 的 %d 条, >0 的 %d 条' % (len(rows), len(zero), len(nz)))
        print('alpha=0 的样本里, ci 的取值: %s' % sorted({r['ci'] for r in zero}))
        print('alpha>0 的样本里, ci 的取值: %s' % sorted({r['ci'] for r in nz}))
        print('alpha=0 的样本里, 截断后点数 pts 的取值: %s'
              % sorted({r['pts_after_trunc'] for r in zero}))


if __name__ == '__main__':
    main()
