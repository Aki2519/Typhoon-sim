# tools/probe_new_knobs_r158.py
"""轮次 158 新旋钮的**正向验证**: 默认关=逐位不变(由 free_hash 保证), 打开必须真的起作用。

为什么必须有这个:
    "默认关时结果逐位不变" 只证明了**没写坏**; 如果接线漏了 / 写成了死代码, 它照样通过。
    所以每个新旋钮都要有一条"打开真的发挥作用"的证据。

⚠ **模式依赖(实测, 不是设计意图的转述)** ---- 这是本文件最重要的信息:
    仓库的 `_env_update_impl` 有**两个互斥分支**:
      · `if DATA is not None:`  数据驱动模式(`params['dataMode']=True`, **应用默认**)
      · `else:`                 解析模式(默认 False)
    于是:
      · 3 四向伸展(`shExtN/S/W/E`)只作用于 `RIDGES` 解析式 -> **只在解析模式生效**
        (数据模式 `env['pEnv']` 直接来自真实 `DATA['mslp']`, 那条循环根本不执行);
      · 1/2 倾角/西端陡峭只作用于**脊线诊断** `sh_ridge_lat`, 而那段在
        `if DATA is not None:` 分支里(core.py:1678-1683) -> **只在数据模式生效**。
    两者都能被"打开后无效"误判, 所以本探针**按模式分别验证**, 并把这条依赖显式记下来。
    顺带发现的旧问题(未修, 见报告): 解析模式下 `env['shRidgeN']` **从不存在** ->
    `sh_pole_steer` 恒返回 (0,0), 轮次 103 标定的向极引导修正在该模式下是**死的**。
    修它会让解析模式的结果整体改变(破坏逐位不变), 所以只报告、不擅自改。

用法:
    python tools/probe_new_knobs_r158.py
"""
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')


def _fresh(data_mode: bool):
    from simulator.simcore import core as V
    sim = V.init({'seed': 20260801})
    sim['params']['dataMode'] = bool(data_mode)
    V.env_update(sim)
    return V, sim


def _env_hash(sim) -> str:
    h = hashlib.sha256()
    for k in sorted(sim['env'].keys()):
        v = sim['env'][k]
        if isinstance(v, np.ndarray):
            h.update(k.encode())
            h.update(np.ascontiguousarray(v).tobytes())
    return h.hexdigest()[:16]


def _cold_peak_t(sim, span_h: int = 24 * 40, step_h: int = 6) -> tuple:
    """扫一遍时间轴, 找冷空气场最强的时刻(准周期脉冲在 t=0 未必触发)。"""
    from simulator.simcore import synoptic as SY
    best_t, best = 0.0, 0.0
    for t in range(0, span_h, step_h):
        s = dict(sim)
        # ⚠ 必须把开关打开再扫 ---- 否则 cold_air 恒返回 None, 扫出来永远是"t=0, 场强 0"
        s['params'] = dict(sim.get('params') or {})
        s['params']['csOn'] = True
        s['t'] = float(t)
        try:
            f = SY.cold_air(s)
        except Exception:
            continue
        m = 0.0 if f is None else float(np.max(f))
        if m > best:
            best, best_t = m, float(t)
    return best_t, best


def _steer_delta(V, a, b) -> float:
    d = []
    for lo in (110.0, 130.0, 150.0, 170.0):
        for la in (20.0, 28.0, 36.0):
            try:
                d.append(abs(float(np.asarray(V.sh_pole_steer(a, lo, la)).ravel()[1])
                             - float(np.asarray(V.sh_pole_steer(b, lo, la)).ravel()[1])))
            except Exception:
                return -1.0
    return max(d) if d else 0.0


def main() -> None:
    bad = []

    # ── 3 四向伸展: 必须在**解析模式**下改变 pEnv ──
    V, a = _fresh(data_mode=False)
    p0 = a['env']['pEnv'].copy()
    print('3 解析模式基线  pEnv max=%.2f hPa' % float(p0.max()))
    for name, key, val in (('shExtW=40', 'shExtW', 40.0), ('shExtE=24', 'shExtE', 24.0),
                           ('shExtN=9', 'shExtN', 9.0), ('shExtS=9', 'shExtS', 9.0)):
        _, b = _fresh(data_mode=False)
        b['params'][key] = val
        V.env_update(b)
        dp = float(np.abs(b['env']['pEnv'] - p0).max())
        ok = dp > 0.0
        print('3 %-12s max|dpEnv|=%7.4f hPa  %s' % (name, dp, 'OK' if ok else '**FAIL**'))
        if not ok:
            bad.append('3 ' + name)

    # ── 1/2 倾角/西端陡峭: 必须在**数据模式**下改变脊线与引导修正 ──
    V, a = _fresh(data_mode=True)
    r0 = a['env'].get('shRidgeN')
    print('1/2 数据模式基线  shRidgeN 存在=%s' % (r0 is not None))
    if r0 is None:
        bad.append('1/2 数据模式下 shRidgeN 缺失')
    else:
        r0 = np.asarray(r0)
        for name, key, val in (('shTilt=2.0', 'shTilt', 2.0),
                               ('shWestAmp=6', 'shWestAmp', 6.0)):
            _, b = _fresh(data_mode=True)
            b['params'][key] = val
            V.env_update(b)
            r1 = np.asarray(b['env'].get('shRidgeN'))
            d_ridge = not np.array_equal(r0, r1)
            ds = _steer_delta(V, a, b)
            ok = d_ridge and ds > 0.0
            print('1 %-12s 脊线变了=%-5s 引导最大变化=%7.4f kt  %s'
                  % (name, d_ridge, ds, 'OK' if ok else '**FAIL**'))
            if not ok:
                bad.append('1 ' + name)

    # ── 4/5 西风槽 + 冷空气: 打开必须改变风场; 冷空气还要经 u200-u850 反馈进切变 ──
    V, a = _fresh(data_mode=True)
    u0 = a['env']['u850'].copy()
    _, b = _fresh(data_mode=True)
    b['params']['wtOn'] = True
    V.env_update(b)
    du = float(np.abs(b['env']['u850'] - u0).max())
    print('4 wtOn=True   max|du850|=%7.4f kt  %s'
          % (du, 'OK' if du > 0.0 else '**FAIL**'))
    if not (du > 0.0):
        bad.append('4 wtOn')

    tpk, cmax = _cold_peak_t(a)
    _, c = _fresh(data_mode=True)
    c['params']['csOn'] = True
    c['t'] = tpk
    V.env_update(c)
    _, d = _fresh(data_mode=True)
    d['t'] = tpk
    V.env_update(d)
    dcs = float(np.abs(c['env']['u850'] - d['env']['u850']).max())
    dsh = float(np.abs(c['env']['shear'] - d['env']['shear']).max())
    # ⚠ 切变**不该**变, 这是既有设计不是缺陷: 数据分支在 core.py:1644-1649 把切变的
    # **模长强行归一**到 `DATA['shear']`(标定过的日尺度口径), 所以任何风场扰动都动不了
    # 切变大小。-> 冷空气在数据模式下是**低层风/引导**效应, 不是经切变抑制强度的效应。
    ok = dcs > 0.0
    print('5 csOn=True   t=%4.0fh 场max=%.3f  max|du850|=%7.4f kt  %s'
          % (tpk, cmax, dcs, 'OK' if ok else '**FAIL**'))
    print('              (参考) 切变变化=%.4f kt ---- 数据分支把切变模长归一到 '
          'DATA[shear], 故预期为 0' % dsh)
    if not ok:
        bad.append('5 csOn')

    # ── 默认关 = 逐位不变(两个模式各查一遍) ──
    for dm in (False, True):
        V, x = _fresh(data_mode=dm)
        V.env_update(x)
        _, y = _fresh(data_mode=dm)
        same = _env_hash(x) == _env_hash(y)
        print('默认关位同(dataMode=%-5s) env哈希一致=%s  %s'
              % (dm, same, 'OK' if same else '**FAIL**'))
        if not same:
            bad.append('默认关 dataMode=%s' % dm)

    print()
    if bad:
        print('**失败**: %s' % bad)
        raise SystemExit(1)
    print('全部旋钮在其生效模式下都真的改变了场 -> 不是死代码')


if __name__ == '__main__':
    main()
