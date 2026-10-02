# -*- coding: utf-8 -*-
"""自由运动(N 模式)位同校验: 对同一 seed 跑一段, 打印全状态哈希。

用途: 轮次 120 重构 `motion_step`(抽出 `_free_motion`)之后, 必须证明
**自由运动路径逐位不变** —— 否则 A 模式的修复会顺带改动已经验收过的一切
(年尺度强度分布、阶段码占比、ERC…)。哈希覆盖每个台风的 lon/lat/vmax/pmin/phase
以及 wake/ohcWake 场, 所以"少沉积一次尾流"这种改动也逃不掉。

用法:
    python tools/free_hash.py 20 12345            # 20 天, seed 12345(数据模式, 默认)
    python tools/free_hash.py 20 12345 analytic   # 解析模式(dataMode=False)

⚠⚠ **覆盖范围(轮次 160 补)**: 本脚本原先**硬编码 `dataMode=True`**(见下面 MODE),
所以它只给**数据分支**做位级背书。而 `_env_update_impl` 是 `if DATA is not None:`
与 `else:` 两个**互斥分支**, 轮次 159 的 22 处常量预计算里**有 12+ 处只在解析分支
执行** —— 那些站点原先**没有任何位级判据**(perf-reviewer 指出, VERIFY_PERF_R159.md)。
现在两种模式各有一条基线; 解析模式的输出前缀是 `FREEHASH_ANALYTIC`。
两行**都以哈希为第 2 字段**(按 `awk '{print $2}'` 取的脚本不受影响), 末尾多一个
`mode=data|analytic` 便于人工确认跑的是哪一支。

参照值(**必须与当时的默认配置一起看**, 否则会把"改了生成率"误判成"改坏了"):
    `_GEN_TRIES=19`(轮次 120 之前):
        d3e072b412aa7475d8ae9ae5430935f686a6f4dc658fd53d8347c9e59598b328
    `_GEN_TRIES=6`(轮次 123-129):
        18f9c456f393b332aefcbba359fcfaf778df5dc2aceb6bdc0fbb114e135f866d
    `_GEN_TRIES=2` + `_GEN_RATE=0.97`(轮次 130-149):
        1c58fc5c9b71b0fdab90d4a36d3c763928406907f7f6f69c2b39b68f67f58f93
    ⚠ **轮次 150 起改成 `_GEN_RATE=0.84` + `STILLBORN_FRAC=0.60`** ⇒ 上面那条
    `1c58fc5c…` **不再是现行配置的基线**, 拿它比会误判成"运动学被改坏了";
    而且它会**阻断**下面那句"改生成率必然改哈希"的判读。
    待办已办 —— **轮次 158 实测补记**(`_GEN_RATE=0.84` + `STILLBORN_FRAC=0.60`, 现行):
        2c30144786b0553a0b9bafa2c37c196b50ace7d4d502cb64be22f2c7436dcdb3
        (days=20 seed=12345 steps=80 maxalive=9)
    **解析模式基线(轮次 160 新补; 与上面那条同日同配置, 只差 dataMode)**:
        5d51e3d901d827429de05b42f84f60df774f697ccc872570287c8490a360919c
        (days=20 seed=12345 steps=80 maxalive=3 mode=analytic)
        ⚠ 两条**必须一起验**: 解析分支上有 12+ 个只在该分支执行的预计算站点,
        只验数据分支 = 把这些站点留在**无判据**状态(perf-reviewer 的发现)。

    ⚠ **上面这条是轮次 158 各项集成工作在「数据分支(`dataMode=True`)」上的"逐位不变"判据**:
    副高形状(倾角/西端陡峭/四向伸展)、西风槽、冷空气、地形、回放 —— 这些新东西的
    **参数默认全部关闭**, 所以只要集成没写坏别的地方, 哈希必须**仍然等于 `2c301447…`**。
    集成后哈希一变 ⇒ "默认关 = 不改变现有结果"没做到, 必须回退或修好。
    唯一例外: 显式把新参数打开(那本来就该变)。
    ⚠⚠ **不要写成"全树判据"** —— 本脚本原先**硬编码 `dataMode=True`**, 解析分支上
    12+ 个只在该分支执行的站点它**一个都不覆盖**(见上面第 3 段的说明)。要做全树判据,
    必须**两条基线一起验**: 数据 `2c301447…` + 解析 `5d51e3d9…`。
统一条件: days=20 seed=12345。**改生成率/生成次数/僵胎占比必然改哈希**(预期行为),
只有"与生成无关的改动"才要求哈希逐位不变。
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V  # noqa: E402

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
# 轮次 160: 第 3 个参数选模式。默认 `data` = 与历史调用**完全一致**(向后兼容)。
MODE = (sys.argv[3].strip().lower() if len(sys.argv) > 3 else 'data')
if MODE not in ('data', 'analytic'):
    raise SystemExit('mode 只能是 data 或 analytic, 收到: %r' % (MODE,))
IS_DATA = (MODE == 'data')

sim = V.init({'start': '2025-08-01 00', 'seed': SEED,
              'params': {'dataMode': IS_DATA, 'genesis': True, 'forecast': False}})
V.env_update(sim)
h = hashlib.sha256()
nstep = int(round(DAYS * 24.0 / V.step_hours(sim)))
nmax = 0
for _ in range(nstep):
    V.sim_step(sim, V.step_hours(sim))
    for tc in sorted(sim['tcs'], key=lambda q: q['id']):
        h.update(('%.7f %.7f %.5f %.5f %s %d|' % (
            tc['lon'], tc['lat'], tc['vmax'], tc['pmin'], tc['phase'],
            tc['id'])).encode())
    h.update(('%.6f' % float(sim['wake'].sum())
              + '%.6f' % float(sim['ohcWake'].sum())
              + '%d' % len(sim['tcs'])).encode())
    nmax = max(nmax, len(sim['tcs']))
print('%s %s  days=%s seed=%s steps=%d maxalive=%d mode=%s'
      % ('FREEHASH' if IS_DATA else 'FREEHASH_ANALYTIC',
         h.hexdigest(), DAYS, SEED, nstep, nmax, MODE))
