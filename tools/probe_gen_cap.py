# -*- coding: utf-8 -*-
"""出生门体检: 年生成量要降到 250 时, `_GEN_RATE` 能不能**线性**定点?

背景(`ROUND_LOG` 轮次 41 / 115 / 117):
  * `_GEN_RATE` 早已饱和(>1), 真正的限流点一度是 `_GEN_TRIES`;
  * 出生门里还有一道 **存活名额** `_GEN_MAX_ALIVE`(=60, 见 `core.gen_check`)。
    文档明确记过"降生成率会撞上限、于是无效"这个坑 —— 所以**降 `_GEN_RATE` 之前
    必须先证明这个窗口里没顶到名额上限**, 否则定点算出来的数字是假的。

本工具不改源码, 只在内存里覆盖常量, 逐 6h 步记录"存活数"轨迹与真生成量:
  * `max_alive` 与 `_GEN_MAX_ALIVE` 的关系 ⇒ 是否触顶;
  * `spawn_total` ⇒ 真生成量(给 `make_tc` 包计数器, 与 `verify_year` 同一口径);
  * `spawn_rate_per_day` ⇒ 直接与 250/年 比。

⚠ 与 `verify_year` 的口径关系: **100 天窗口下"扰动总数"与"年内累计生成"相等**,
因为 120 天保留窗(2880h)还没生效; 饱和只出现在 365 天尺度。本工具用计数器,
所以在两种窗口下都是真值。

用法:
    python tools/probe_gen_cap.py 100 20250801 "2025-07-01 00"
    python tools/probe_gen_cap.py 100 20250801 "2025-07-01 00" stillbornFrac=0.60,_GEN_RATE=0.77
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
from simulator.simcore import core as V  # noqa: E402

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 20250801
START = sys.argv[3] if len(sys.argv) > 3 else '2025-07-01 00'

# 第 4 个参数: 逗号分隔的覆盖项。`_` 前缀的名字(如 `_GEN_RATE`)改的是**模块常量**
# (内存覆盖, 不落盘), 其余进 sim['params'] —— 与扫参工具的习惯一致。
params, consts = {}, {}
if len(sys.argv) > 4:
    for kv in [x for x in sys.argv[4].split(',') if x.strip()]:
        k, _, v = kv.partition('=')
        k, v = k.strip(), v.strip()
        if k.startswith('_'):
            try:
                consts[k] = int(v) if k == '_GEN_TRIES' else float(v)
            except ValueError:
                raise SystemExit('常量的值必须能转成数字: %s=%s' % (k, v))
        else:
            try:
                params[k] = float(v)
            except ValueError:
                params[k] = v

_orig_vals = {k: getattr(V, k) for k in consts}
for k, v in consts.items():
    setattr(V, k, v)

params.setdefault('dataMode', True)
params.setdefault('genesis', True)
params.setdefault('forecast', False)

sim = V.init({'start': START, 'seed': SEED, 'envStepH': 6.0, 'params': params})

# ── 计数器: 真生成量(与 verify_year 同法, 给 make_tc 包一层) ──
_spawn = [0]
_orig_make = V.make_tc


def _counting_make(sim_, *args, **kw):
    _spawn[0] += 1
    return _orig_make(sim_, *args, **kw)


# ── 逐 6h 步记录存活数与模拟时刻(存活 = not dead, 与 gen_check 的名额判据一致) ──
_alive_hist = []          # (sim_t_hours, n_alive)
_orig_core = V._sim_step_core


def _traced_core(sim_, *a, **kw):
    r = _orig_core(sim_, *a, **kw)
    _alive_hist.append((float(sim_['t']),
                        len([t for t in sim_['tcs'] if not t['dead']])))
    return r


# ── 逐 6h 步记录存活数(仅作参考) ──
# ⚠ 轮次 153 修: 原来只在整步**跑完之后**统计"存活", 而 `gen_check` 的门控量是
# 它**进入时**的存活数, 同一步里之后还会发生"出生"和"死亡" ⇒ 采到的是
# (门前 + 本步出生 − 本步死亡), 与门看到的那个数不是一回事, `cap_hit` 会假阳/假阴。
# 真正的判据必须在**门控入口**采样(见下面的 `_traced_gen`)。
_alive_hist = []          # (sim_t_hours, n_alive)  —— 仅供参考
_gate_alive = []          # gen_check 入口处的存活数 —— **cap_hit 用这一列**
_orig_core = V._sim_step_core
_orig_gen = V.gen_check


def _traced_core(sim_, *a, **kw):
    r = _orig_core(sim_, *a, **kw)
    _alive_hist.append((float(sim_['t']),
                        len([t for t in sim_['tcs'] if not t['dead']])))
    return r


def _traced_gen(sim_):
    # **与 `core.gen_check` 的判据逐字一致**: `len([t for t in tcs if not t.dead])`
    _gate_alive.append(len([t for t in sim_['tcs'] if not t['dead']]))
    return _orig_gen(sim_)


V.make_tc = _counting_make
V._sim_step_core = _traced_core
V.gen_check = _traced_gen
try:
    nstep = int(round(DAYS * 24.0 / 6.0))
    for _ in range(nstep):
        V.sim_step(sim, 6.0)
finally:
    V.make_tc = _orig_make
    V._sim_step_core = _orig_core
    V.gen_check = _orig_gen
    # ⚠ 覆盖值必须在**还原之前**取下来 —— 还原后就只剩原值了。
    _eff_cap = int(getattr(V, '_GEN_MAX_ALIVE'))
    _eff_rate = float(getattr(V, '_GEN_RATE'))
    _eff_tries = int(getattr(V, '_GEN_TRIES'))
    for k, v in _orig_vals.items():
        setattr(V, k, v)

_cap = _eff_cap                       # ⚠ 用还原前采下的生效值(轮次 153 修)
_tries = _eff_tries
_rate = _eff_rate
_alive = [n for _, n in _alive_hist]
_max_alive = max(_alive) if _alive else 0
_max_gate_alive = max(_gate_alive) if _gate_alive else 0
_gen_rate_param = float(params.get('genesisRate', 1.0))

# 尾段(最后 20% 步)的存活中位: 用来判断是不是"长期贴着上限跑"
_tail = _alive[int(len(_alive) * 0.8):] if _alive else [0]
_tail_med = sorted(_tail)[len(_tail) // 2] if _tail else 0

out = {
    'days': DAYS, 'seed': SEED, 'start': START,
    'overrides': {'consts': consts, 'params': params},
    'gen_rate': _rate, 'gen_tries': _tries, 'genesisRate_param': _gen_rate_param,
    'spawn_total': _spawn[0],
    'spawn_rate_per_day': round(_spawn[0] / DAYS, 4),
    # ⚠⚠ 轮次 153: 这个字段**不是年生成量**, 只是"本窗口 x365"。
    # 本项目的年度量口径是 **1 月 + 7 月两个窗口分别跑再相加**(单窗口是单半球季,
    # 外推有系统偏差 —— 轮次 123 实测 1 月窗里 EP=AL=IO=0、WP=15/SH=38)。
    # 直接拿它跟 250/年 比是错的; 要年量请跑 Jan + Jul 两次后求和。
    'single_window_x365_NOT_annual': round(_spawn[0] / DAYS * 365.0, 1),
    'max_alive': _max_alive,
    'max_gate_alive': _max_gate_alive,     # gen_check 入口处(与名额判据同一时刻)
    'alive_cap': _cap,
    'cap_hit': bool(_max_gate_alive >= _cap),
    'tail_alive_median': _tail_med,
    'steps': len(_alive_hist),
}
print('PROBE ' + json.dumps(out, ensure_ascii=False, separators=(',', ':')))
print('# 提示: single_window_x365_NOT_annual 是**单窗口**外推, 不可当年度量; '
      '年量要用 Jan + Jul 两窗口相加。')
