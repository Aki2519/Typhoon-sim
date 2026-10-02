# -*- coding: utf-8 -*-
"""把 probe_sim_fps.py / _probe_sim_stepcost.py 的 JSON 结果转成 Markdown 表。

纯数据整理, 不起模拟进程, 不修改任何被监视文件。
用法: python tools/_probe_sim_tables.py /tmp/p_pacing.json /tmp/p_layers.json ...
"""
import json
import sys


def g(d, *ks, default=float('nan')):
    for k in ks:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def load(paths):
    cells, switch, meta = [], [], {}
    for p in paths:
        try:
            with open(p, encoding='utf-8') as f:
                r = json.load(f)
        except Exception as e:
            print('!! %s: %s' % (p, e))
            continue
        meta[p] = {'boot_s': r.get('boot_s'), 'warm': r.get('warm'),
                   'dt_mode': r.get('dt_mode'), 'fps_cap': r.get('fps_cap')}
        for c in r.get('cells', []):
            c['_src'] = p
            cells.append(c)
        for s in r.get('switch', []):
            s['_src'] = p
            switch.append(s)
    return cells, switch, meta


def main():
    cells, switch, meta = load(sys.argv[1:])
    print('## meta')
    for k, v in meta.items():
        print('- %s: %s' % (k, json.dumps(v, ensure_ascii=False)))

    print('\n## A. 逐档位(物理+渲染, 真实播放)')
    print('| 档位 | rep | 目标ms/6h | 面板stepCostMs | p50 | p90 | p99 | max | '
          '>16.7ms% | >33ms% | 物理块p50 | 物理块max | 块数 | 1块/帧 | '
          '物理占墙钟% | UI冻结% | 面板有效x | 真实有效x | tcs/alive |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for c in cells:
        if c.get('phase') not in ('pacing', 'combined'):
            continue
        f, b = c['frame'], c['physics_block_ms']
        blocksum = (b.get('mean', 0.0) * b.get('n', 0)) if b.get('n') else 0.0
        wall = c.get('wall_s') or float('nan')
        print('| %s | %s | %.0f | %.0f | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | '
              '%s | %.0f | %d | %.1f | %.1f | %.1f | %.2f | %.3f | %d/%d |'
              % (c['tier'], c.get('rep', 1), c['tier_target_ms'],
                 c['panel_step_cost_ms'], f['p50'], f['p90'], f['p99'], f['max'],
                 c['over16_7_pct'], c['over33_pct'],
                 ('%.0f' % b['p50']) if b.get('n') else '-',
                 c['max_block_ms'], c['n_physics_frames'], c['frames_per_block'],
                 c['pct_wall_blocked'], 100.0 * blocksum / (wall * 1000.0) if wall == wall else float('nan'),
                 c['eff_x'], c.get('wall_effective_x', float('nan')),
                 c['tc']['tcs'], c['tc']['alive']))

    print('\n## B. 逐图层(纯渲染, 物理暂停) —— 只看 DRAW')
    print('| 图层组合 | rep | UPDATE p50 | DRAW p50 | DRAW p90 | DRAW p99 | DRAW max | '
          '帧 p50 | 帧 p90 | >16.7ms% | >33ms% | 层数 | tcs/alive |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for c in cells:
        if c.get('phase') != 'render':
            continue
        f, u, d = c['frame'], c['update'], c['draw']
        print('| %s | %s | %.2f | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %d | %d/%d |'
              % (','.join(c['layers']), c.get('rep', 1), u['p50'], d['p50'], d['p90'],
                 d['p99'], d['max'], f['p50'], f['p90'], c['over16_7_pct'],
                 c['over33_pct'], len(c['layers']), c['tc']['tcs'], c['tc']['alive']))

    print('\n## C. 单图层打开开销(暂停物理)')
    print('| 图层 | set/toggle 调用本体 ms | 打开第一帧 ms | 稳态 p50 | 稳态 p90 |')
    print('|---|---|---|---|---|')
    for s in switch:
        print('| %s | %.3f | %s | %.1f | %.1f |'
              % (s['layer'], s['call_ms'],
                 ('%.1f' % s['cold_ms']) if s.get('cold_ms') is not None else '-',
                 s['warm_p50'], s['warm_p90']))


if __name__ == '__main__':
    main()
