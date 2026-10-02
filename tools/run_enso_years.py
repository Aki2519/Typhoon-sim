"""三种 ENSO 年各跑一年: 超强厄尔尼诺 / 中性 / 超强拉尼娜。

**2026-09-21 轮次 45 重要更正**: 这里改用 `--enso-pin`(钉住模拟器**自带的** ENSO 相位),
不再用 `--sst-anom`。原因:
    · 内核里本来就有 `enso_update` —— 充放电振子(周期约 4 年)+ **空间型** SSTA
      (中东太 145W 增暖、西太冷却)+ **切变遥相关**(厄尔尼诺时西太切变增强、中东太减弱);
    · 而 `sstAnom` 只是**全海盆均匀偏移**, 根本不是 ENSO。
    ⇒ 之前用 sstAnom 跑的三次"ENSO 年"作废, 不能反映"生成点东移"这类型态。

三种相位: ensoPin = +2.0(超强厄尔尼诺) / 0.0(中性) / -2.0(超强拉尼娜)。
另附**生成经度分布**, 用来检验"厄尔尼诺时生成点东移、西太减少、东太增多"。

结果每轮即时追加写盘。
"""
import io
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
OUT = os.path.join(ROOT, 'output', 'sim', '_reference', 'enso_pin_runs.txt')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
ENV = dict(os.environ)
ENV['PYTHONIOENCODING'] = 'utf-8'

RUNS = [
    ('超强厄尔尼诺', 2.0, '20250801'),
    ('中性', 0.0, '20250801'),
    ('超强拉尼娜', -2.0, '20250801'),
]

with io.open(OUT, 'w', encoding='utf-8') as fh:
    for tag, pin, seed in RUNS:
        t0 = time.time()
        head = '>>> 开始 %s (ensoPin=%+.1f) ...' % (tag, pin)
        print(head, flush=True)
        fh.write(head + '\n')
        fh.flush()
        r = subprocess.run(
            [PY, os.path.join(HERE, 'verify_year.py'), '--enso-pin', str(pin),
             '--seed', seed, '--days', '365', '--tag', tag],
            cwd=ROOT, capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=ENV)
        out = (r.stdout or '') + (r.stderr or '')
        fh.write(out + '\n')
        fh.flush()
        try:
            print(out, flush=True)
        except Exception:
            pass
        tail = '<<< %s 完成, 用时 %.0f s' % (tag, time.time() - t0)
        print(tail, flush=True)
        fh.write(tail + '\n')
        fh.flush()

print('-> %s' % OUT)
