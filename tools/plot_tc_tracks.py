# -*- coding: utf-8 -*-
"""路径图绘制 —— 模拟 vs 观测目视对比 + **typhoon4 风格单幅路径图**。

## 轮次 163 重写(界面 + 渲染双重升级)
原先的版本(148 行)有一批界面与渲染问题, 按 `ui-experience` 的标准逐条列在这里,
每条都写了**根因**与**修法**, 后面是实现:

| 级别 | 问题(旧版) | 根因 | 现在 |
|---|---|---|---|
| 阻塞 | `--sim` **必填** ⇒ 画不了纯观测图 | 参数定义成 required | `--sim` 可选; 不给就只画观测 |
| 阻塞 | 配色与 typhoon4 **完全不同**(TS 是绿、只有 6 档、无白/紫) | `grade_color()` 自造色阶 | 用 `ty4_render.PALETTE_2023`(像素级确证) |
| 严重 | 范围**写死** `lon 0-360 / lat -60..60` | 常量 | 默认按轨迹包围盒自动取景(typhoon4 规则), 可 `--lon/--lat` 覆写 |
| 严重 | 跨 180 度**把轨迹切断** | `np.diff(lon)>180` 分段 | `ty4_render.unwrap_lon`(平移, 不切断) |
| 严重 | 只画**起点一个白点** | 只 `circle(lon[a])` | **每个定位点都画**(按等级着色; 预报点画三角) |
| 严重 | **无图例** ⇒ 颜色无解释 | 没实现 | `--legend` 0-4(默认右下) |
| 一般 | 尺寸/区域/图例位置**不可配** | 全是常量 | `--width/--height/--space/--legend/--priority` |
| 一般 | 不能只画**单条风暴** | 无此参数 | `--storm <编号或名字>` |
| 建议 | 报错不友好 | `z['vmax_%d']` 直接 KeyError | 缺字段时跳过并计数, 末尾统一报告 |

⚠ **"完全一致"是分区域的**(实测): 底图/轨迹/圆点可逐像素; **图例文字只能给容差** ——
参考图是原版 exe(GDI)光栅化的, 与 PIL 的字形抗锯齿不可能逐位相同。
详见 `output/sim/_reference/TY4_SPEC.md` 与 `TY4_LEAD_NOTES.md`。

用法:
  # 1) 模拟 vs 观测双面板(旧行为, 但用新渲染)
  python tools/plot_tc_tracks.py --sim output/sim/1997/tcsim_1997k.npz --year 1997
  # 2) 只画观测的 typhoon4 风格单幅图
  python tools/plot_tc_tracks.py --year 2013 --obs-only --out out/wpac2013.png
  # 3) 画单条风暴
  python tools/plot_tc_tracks.py --year 1997 --storm 11 --obs-only
  # 4) 参数全覆盖
  python tools/plot_tc_tracks.py --year 1997 --sim ... --width 2560 --legend 3 --priority 3
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import numpy as np                                              # noqa: E402
import pygame                                                   # noqa: E402

pygame.init()
pygame.display.set_mode((64, 64))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import ty4_render as T4                                         # noqa: E402
from PIL import Image                                           # noqa: E402

FONT = os.path.join(ROOT, 'assets', 'font', 'MapleMono-NF-CN-Medium.ttf')


def _to_surface(img) -> 'pygame.Surface':
    """PIL -> pygame Surface(避免中间的临时文件)。"""
    return pygame.image.fromstring(img.tobytes(), img.size, 'RGB')


def draw_ty4(tracks, legend=4, width=1920, height=1080,
             width_space=360, height_space=270, priority=2,
             title=None, clamp=False) -> 'pygame.Surface':
    """用 **typhoon4 规格**画一幅图(底图 + 轨迹 + 图例)。

    与旧版的区别: 底图/取景/配色/取色规则/点/图例 全部换成实测规格。
    """
    fixes = []
    for tc in tracks:
        lon = np.asarray(tc['lon'], float)
        lat = np.asarray(tc['lat'], float)
        v = np.asarray(tc['vmax'], float)
        n = min(lon.size, lat.size, v.size)
        for i in range(n):
            fixes.append(T4.Fix(float(lon[i]) % 360.0, float(lat[i]),
                                float(v[i])))
    if not fixes:
        # **空态**: 不给一张纯底图让人猜, 明确画一句"没有数据"
        s = pygame.Surface((width, height))
        s.fill((15, 23, 31))
        f = pygame.font.Font(FONT, 28)
        msg = '没有可画的路径(该年/该风暴无定位点)'
        s.blit(f.render(msg, True, (200, 205, 215)),
               ((width - f.size(msg)[0]) // 2, height // 2))
        return s
    opt = T4.RenderOptions(width=width, height=height,
                           width_space=width_space, height_space=height_space,
                           draw_legends=legend, priority=priority)
    img = T4.render_map(fixes, opt=opt, clamp=clamp)
    # ⚠ **反馈**: typhoon4 规格里画布会为容纳轨迹而自动长大(这是正确行为, 实测
    # 4 张参考图宽度就各不相同)。但如果用户显式给了 --width/--height, 静默返回一个
    # 大得多的图会让人以为参数写错了/程序坏了 ⇒ **必须说清楚**。
    if img.size != (width, height):
        print('注意: 轨迹跨度大, 画布按 typhoon4 规格自动放大到 %dx%d '
              '(请求 %dx%d)。要强制裁到请求尺寸请加 --clamp-size。'
              % (img.size[0], img.size[1], width, height))
    s = _to_surface(img)
    if title:
        f = pygame.font.Font(FONT, 20)
        s.blit(f.render(title, True, (255, 255, 255)), (10, 6))
    return s


def load_sim(path):
    """读模拟结果 npz。**缺字段不再直接 KeyError**, 而是跳过并计数。"""
    z = np.load(path, allow_pickle=False)
    ids = sorted({int(k.split('_')[1]) for k in z.files if k.startswith('t_')})
    out, skipped = [], 0
    for i in ids:
        try:
            if z['vmax_%d' % i].size < 3:
                skipped += 1
                continue
            out.append({'id': i, 'lon': z['lon_%d' % i],
                        'lat': z['lat_%d' % i], 'vmax': z['vmax_%d' % i]})
        except KeyError:
            skipped += 1
    return out, skipped


def load_obs(year, min_vmax=34.0):
    """读观测路径(只取命名风暴, 口径与旧版一致)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tclim', os.path.join(ROOT, 'tools', 'train_tc_clim.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tracks, _ = m.collect()
    out, skipped = [], 0
    for _k, (_prio, tr) in tracks.items():
        tr = [r for r in tr if r[0] // 1000000 == year]
        if len(tr) < 3:
            skipped += 1
            continue
        v = np.array([r[3] for r in tr], float)
        if v.max() < min_vmax:
            skipped += 1
            continue
        out.append({'id': _k if isinstance(_k, int) else 0,
                    'lon': np.array([r[2] for r in tr], float),
                    'lat': np.array([r[1] for r in tr], float), 'vmax': v})
    return out, skipped


def filter_storm(tracks, storm):
    if storm is None:
        return tracks
    s = str(storm)
    return [t for t in tracks if str(t.get('id')) == s]


def main():
    ap = argparse.ArgumentParser(
        description='路径图绘制(typhoon4 规格)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('用法:')[-1])
    ap.add_argument('--sim', default=None,
                    help='模拟结果 npz; **不给就只画观测**(旧版此项必填)')
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--outdir', default=None)
    ap.add_argument('--out', default=None, help='直接指定输出文件')
    ap.add_argument('--obs-only', action='store_true',
                    help='只出观测的 typhoon4 风格单幅图')
    ap.add_argument('--sim-only', action='store_true', help='只出模拟单幅图')
    ap.add_argument('--storm', default=None, help='只画这条风暴(编号)')
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--height', type=int, default=1080)
    ap.add_argument('--width-space', type=int, default=360)
    ap.add_argument('--height-space', type=int, default=270)
    ap.add_argument('--legend', type=int, default=4, help='0不画 1左上 2左下 3右上 4右下')
    ap.add_argument('--priority', type=int, default=2,
                    help='0不画路径 1同时 2定位点置顶 3按强度排序')
    ap.add_argument('--clamp-size', action='store_true',
                    help='强制输出恰好 width x height(超出裁掉); 默认按 typhoon4 规格自动放大')
    ap.add_argument('--min-vmax', type=float, default=34.0,
                    help='观测风暴的最低峰值风速(kt), 默认 34 = 只取命名风暴')
    a = ap.parse_args()

    sim, obs = [], []
    if a.sim:
        sim, sk = load_sim(a.sim)
        sim = filter_storm(sim, a.storm)
        if sk:
            print('提示: 模拟结果里 %d 条因字段缺失/点太少被跳过' % sk)
    if not a.sim_only:
        obs, sk2 = load_obs(a.year, a.min_vmax)
        obs = filter_storm(obs, a.storm)
        if sk2:
            print('提示: 观测里 %d 条未达口径被跳过' % sk2)

    od = a.outdir or os.path.join(ROOT, 'output', 'sim', str(a.year))
    os.makedirs(od, exist_ok=True)

    def path(tag):
        return a.out if a.out else os.path.join(od, 'tctracks_%d%s.png' % (a.year, tag))

    if a.obs_only or a.sim_only:
        tracks = obs if a.obs_only else sim
        kind = '观测' if a.obs_only else '模拟'
        if not tracks:
            # **空态**: 明确告知并给下一步建议, 不产出一张空白图
            print('没有可画的路径(%s %d%s)。建议: 去掉 --storm, 或放宽 --min-vmax(现 %.0f kt)'
                  % (kind, a.year, ' 风暴 %s' % a.storm if a.storm else '', a.min_vmax))
            return 1
        s = draw_ty4(tracks, legend=a.legend, width=a.width, height=a.height,
                     width_space=a.width_space, height_space=a.height_space,
                     priority=a.priority, clamp=a.clamp_size,
                     title='%s %d   %d 条' % (kind, a.year, len(tracks)))
        p = path('_%s' % ('obs' if a.obs_only else 'sim'))
        pygame.image.save(s, p)
        print('-> %s   %s %d 条  %dx%d' % (p, kind, len(tracks), s.get_width(), s.get_height()))
        return 0

    # 双面板(旧行为; 但现在两边都用 typhoon4 规格画)
    sl = draw_ty4(sim, legend=a.legend, width=a.width, height=a.height,
                  width_space=a.width_space, height_space=a.height_space,
                  priority=a.priority,
                  title='模拟 %d   %d 条' % (a.year, len(sim)))
    ol = draw_ty4(obs, legend=a.legend, width=a.width, height=a.height,
                  width_space=a.width_space, height_space=a.height_space,
                  priority=a.priority,
                  title='观测 %d   %d 条' % (a.year, len(obs)))
    img = pygame.Surface((a.width, a.height * 2 + 34))
    img.fill((10, 10, 16))
    img.blit(sl, (0, 0))
    img.blit(ol, (0, a.height + 34))
    big = pygame.font.Font(FONT, 24)
    img.blit(big.render('TC 路径对比  %d  (上=模拟  下=观测)' % a.year,
                        True, (255, 235, 120)), (8, a.height + 6))
    p = path('')
    pygame.image.save(img, p)
    print('-> %s   模拟 %d 条 / 观测 %d 条' % (p, len(sim), len(obs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
