# tools/probe_terrain_wiring_r158.py
"""⑥ 地形接线的**定向正向验证**(轮次 158)。

为什么不能只看 free_hash:
    地形只在台风经过**内陆高海拔**时才生效。如果 20 天里没有台风撞山, 那么
    "terrainOn 开/关的哈希相同" 既可能是"接线没生效", 也可能是"根本没撞山" ----
    **两者无法区分**。所以本探针同时报告"有没有台风真的经过高地形"。

做法:
    同一 seed 跑 N 步两遍(terrainOn 关 / 开), 逐步哈希比对, 报告首个分歧步;
    同时记录全程台风采样到的最大海拔与最大陆地占比, 用于判读"没分歧"是否正常。

用法:
    python tools/probe_terrain_wiring_r158.py [步数]
"""
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def _hash(sim) -> str:
    h = hashlib.sha256()
    for k in sorted(sim['env'].keys()):
        v = sim['env'][k]
        if isinstance(v, np.ndarray):
            h.update(k.encode())
            h.update(np.ascontiguousarray(v).tobytes())
    for tc in sim.get('tcs') or []:
        h.update(('%d|%.4f|%.4f|%.4f|%.4f|%s' % (
            tc['id'], tc['lon'], tc['lat'], tc['vmax'], tc['pmin'],
            tc.get('phase', ''))).encode())
    return h.hexdigest()


def _run(steps: int, terrain_on: bool):
    from simulator.simcore import core as V
    from simulator.simcore import terrain as TG
    sim = V.init({'seed': 12345})
    sim['params']['terrainOn'] = bool(terrain_on)
    dt = float(V.step_hours(sim))
    hashes, max_h, max_land, land_hits = [], 0.0, 0.0, 0
    for i in range(steps):
        V.sim_step(sim, dt, max_inner=1)
        hashes.append(_hash(sim))
        for tc in sim.get('tcs') or []:
            if tc.get('dead'):
                continue
            try:
                h = float(TG.elev_at(tc['lon'], tc['lat'], use_max=True))
                lf = float(V.land_at(sim, tc['lon'], tc['lat']))
            except Exception:
                continue
            max_h = max(max_h, h)
            max_land = max(max_land, lf)
            if h > 0.0 and lf > 0.05:
                land_hits += 1
    return hashes, max_h, max_land, land_hits, len(sim.get('tcs') or [])


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    off, h_off, l_off, n_off, t_off = _run(steps, False)
    on, h_on, l_on, n_on, t_on = _run(steps, True)
    print('步数=%d  tcs: 关=%d 开=%d' % (steps, t_off, t_on))
    print('台风采样到的最大海拔: 关=%.1f m  开=%.1f m' % (h_off, h_on))
    print('台风采样到的最大陆地占比: 关=%.2f  开=%.2f' % (l_off, l_on))
    print('"台风位于陆地且海拔>0"的采样次数: 关=%d 开=%d' % (n_off, n_on))
    first = None
    for i, (a, b) in enumerate(zip(off, on)):
        if a != b:
            first = i
            break
    if first is None:
        if max(h_on, h_off) > 0.0 and max(n_on, n_off) > 0:
            print('**FAIL**: 确实有台风经过高地形, 但 terrainOn 开/关逐位相同 '
                  '-> 接线没生效')
            raise SystemExit(1)
        print('结论: 本段没有台风经过"陆地且高海拔"' + ' -> 无差异是正常的(不构成证据)')
        print('      -> 需要用"把台风放到台湾"的定向用例才能验证接线')
        return
    print('首个分歧步 = %d (开/关状态从此不同) -> 接线生效' % first)
    # 同时确认"默认关"这一侧与无地形基线一致(由 free_hash 负责, 这里只做提示)
    print('提示: "默认关 = 逐位不变" 由 tools/free_hash.py 单独保证')


if __name__ == '__main__':
    main()
