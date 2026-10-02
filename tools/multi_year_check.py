"""多种子年尺度验收: 同一配置跑 N 年(不同种子), 报告均值 + 范围。

为什么要它: 单年 ~94 个风暴里, 130+ 只有 6-8 个, 泊松噪声约 +-2.7 个百分点。
轮次 41-44 里我拿 9% / 7% / 6% 做取舍 —— 那些差别**全在噪声内**, 是无效比较。
本脚本强制报"均值 [最小, 最大]"并打印每年原始值, 让噪声可见。

用法: python tools/multi_year_check.py --seeds 20250801,11,23,37,53 --days 365
"""
import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
ENV = dict(os.environ)
ENV['PYTHONIOENCODING'] = 'utf-8'


#: 参与验收的源码。**验收期间它们不允许被改动** —— 见下面 _src_hash 的说明。
_WATCHED = ('simulator/simcore/core.py', 'simulator/simcore/phase.py',
            'simulator/simcore/envmodel/solver.py',
            'simulator/simcore/envmodel/modes.py',
            'simulator/simcore/climate.py',
            'tools/verify_year.py')


def _src_hash():
    h = hashlib.md5()
    for f in _WATCHED:
        try:
            with open(os.path.join(ROOT, f), 'rb') as fh:
                h.update(fh.read())
        except OSError:
            h.update(b'?')
    return h.hexdigest()


def parse_rec(out, sd):
    """从 `verify_year` 的输出里抽一条种子记录。**必须与 --resume 共用**,
    否则续跑时补进来的种子会缺字段(第 18 次口径问题的形态)。"""
    rec = {'seed': sd}
    for ln in out.split('\n'):
        # 观测基准那一行也含 P50/P90/P95, 会把模型值覆盖掉(轮次 49 踩过这个坑)。
        # 轮次 122 又把基准行扩成了多行(带口径说明), 其中一行同样含 P50/P90 ——
        # 所以过滤条件从"观测基准/参考:"放宽到**任何含"基准"的行**。
        # (模型的输出行里不含"基准"二字; 已由 tests/test_multi_year_tools.py 锁住。)
        if '基准' in ln or '参考' in ln:
            continue
        if '扰动总数' in ln:
            rec['total'] = int(ln.split()[1])
        elif '年内累计' in ln:
            # **真年生成量**(轮次 115): 上面那行的 `扰动总数` 是"最近 120 天内生成、
            # 还没被剔掉的个数"(`_sim_step_core` 会剔掉死亡超过 2880h 的),
            # 在 >120 天的运行里会**饱和**。两者在 135 天实测相差 20 个(218 vs 238)。
            # ⚠ 轮次 134 修: 锚点原来是 **'年内累计生成'**(没有冒号/空格), 而
            # `verify_year` 实际打印的是
            #   `**年内累计: 生成 83 个(0.83 个/天) | 其中发展成 34kt+ 64 个(发展率 77%)**`
            # —— "累计" 与 "生成" 之间是 `: `, 所以那条分支**从来没命中过**,
            # `gen/gdev/grate` 三个字段在汇总里一直是空的(而 `dev/rate` 由下一行的
            # "发展成 34kt+" 命中, 所以外行看不出来)。改为按 `年内累计` 匹配 + 正则取数。
            _m = re.search(r'生成\s*(\d+)\s*个', ln)
            if _m:
                rec['gen'] = int(_m.group(1))
            _m = re.search(r'34kt\+\s*(\d+)\s*个', ln)
            if _m:
                rec['gdev'] = int(_m.group(1))
            _m = re.search(r'发展率\s*([\d.]+)\s*%', ln)
            if _m:
                rec['grate'] = float(_m.group(1))
        elif '发展率' in ln:
            rec['dev'] = int(ln.split()[2].rstrip(')'))
            try:
                rec['rate'] = float(ln.split('发展率')[1].split('%')[0].strip())
            except Exception:
                pass
        elif '100-129' in ln and '个' in ln:
            rec['s100'] = float(ln.split('(')[1].split('%')[0])
        elif '130+' in ln and '个' in ln:
            rec['s130'] = float(ln.split('(')[1].split('%')[0])
        elif 'P50' in ln and 'P90' in ln:
            rec['p50'] = float(ln.split('P50')[1].split()[0])
            rec['p90'] = float(ln.split('P90')[1].split()[0])
            rec['p95'] = float(ln.split('P95')[1].split()[0])
        elif '一生最高纬度' in ln and '中位' in ln:
            rec['maxlat'] = float(ln.split('中位')[1].split()[0])
            rec['p28'] = float(ln.split('>=28')[1].split('%')[0].replace('*', '').strip())
        elif '净向极位移中位' in ln:
            rec['dlat'] = float(ln.split('净向极位移中位')[1].split()[0])
            rec['life'] = float(ln.split('寿命中位')[1].split()[0])
    return rec


def find_done(txt, seeds):
    """从参考文件内容里找出**已完成**的种子 -> {seed: rec}。

    判定依据: 同时出现 `>>> seed X` 起跑行与 `<<< seed X 用时` 完成行。
    **只有起跑行说明那个种子中途死了, 必须重跑** —— 这正是轮次 105/110/111
    三次"整进程被杀"的形态(父进程挂起、参考文件 0 字节或停在起跑行)。

    抽成独立函数是为了能**单测**(见 tests/test_multi_year_tools.py):
    验收工具本身出错会静默地给出错的结果, 比模型出错更危险。
    """
    done = {}
    for sd in seeds:
        a = '>>> seed %s\n' % sd
        b = '<<< seed %s 用时' % sd
        if a in txt and b in txt:
            i = txt.index(a)
            j = txt.index(b, i)
            done[sd] = parse_rec(txt[i:j], sd)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', default='20250801,11,23,37,53')
    ap.add_argument('--days', type=float, default=365.0)
    ap.add_argument('--sst-anom', type=float, default=0.0)
    ap.add_argument('--tag', default='multi')
    ap.add_argument('--start', default='2025-01-01 00',
                    help='起始时刻。**15 种子验收要跑 1 月 + 7 月两个窗口**'
                         '(单窗口是单半球季, 外推年量有偏)')
    ap.add_argument('--param', default='',
                    help='透传给 verify_year 的 --param(覆盖 sim["params"], 如 hiTauScale=0.35)。'
                         '⚠ 扫参必须**每个参数一份产物**, 否则汇总会把不同参数混在一起')
    ap.add_argument('--resume', action='store_true', default=False,
                    help='续跑: 跳过参考文件里**已完成**的种子(本机内存不稳, '
                         '长验收随时可能被杀, 必须能续)')
    a = ap.parse_args()
    seeds = [s.strip() for s in a.seeds.split(',') if s.strip()]

    # **制度性防线(轮次 107)**: `multi_year_check` 每个种子都新起一个
    # `verify_year` 子进程, 所以子进程每次都**重新 import 源码** —— 如果验收期间
    # 源码被改动, 前面几个种子跑的是旧代码、后面是新的, 汇总之中的"均值"毫无意义。
    # 实测踩过: 轮次 105 的 15 种子运行到一半时我改了 `core.py`, 15 个种子里
    # 14 个报 `ArrayMemoryError` 且代码版本混杂, 整份结果作废。
    # 这里在**每个种子开跑前**核对源码指纹, 一旦变化就立即停止并把已完成的种子
    # 结果原样保留(不拿混版数据做判断)。
    _h0 = _src_hash()
    rows = []
    # **每轮即时写盘**(第 98 轮补): 之前只在最后汇总, 一旦作业被中断/客户端超时,
    # 前面已跑完的种子结果就全丢了 —— run_enso_years.py 在轮次 36 已修过同样的缺陷,
    # 这里同步补上。
    outp = os.path.join(ROOT, 'output', 'sim', '_reference', 'multi_year_%s.txt' % a.tag)
    # **断点续跑**(轮次 110 加): 本机可用内存只有 1-3 GiB(物理 15.74 GiB)
# + 用户训练作业, 15 种子长验收
    # 实测会被整进程杀掉(父进程一起没), 所以必须能接着跑。
    # 判定"已完成"的依据是参考文件里同时有起跑行与完成行 —— 只有起跑行说明那个种子
    # 中途死了, 要重跑。
    done = {}
    if a.resume and os.path.exists(outp):
        try:
            done = find_done(io.open(outp, encoding='utf-8',
                                     errors='replace').read(), seeds)
            if done:
                print('续跑: 已有 %d 个种子完成 -> %s'
                      % (len(done), ' '.join(sorted(done))), flush=True)
        except Exception as exc:
            print('续跑读取失败(将从头跑): %s' % exc, flush=True)
    fh = io.open(outp, 'a' if done else 'w', encoding='utf-8')
    for sd in seeds:
        if sd in done:
            rows.append(done[sd])
            continue
        _h = _src_hash()
        if _h != _h0:
            print('!!! **源码在验收期间被改动**(指纹 %s -> %s) —— 结果不可比, 停止。'
                  % (_h0[:8], _h[:8]), flush=True)
            fh.write('!!! 源码在验收期间被改动(%s -> %s), 结果不可比, 已停止\n'
                     % (_h0[:8], _h[:8]))
            fh.flush()
            break
        t0 = time.time()
        print('>>> seed %s ...' % sd, flush=True)
        r = subprocess.run(
            [PY, os.path.join(HERE, 'verify_year.py'), '--sst-anom', str(a.sst_anom),
             '--seed', sd, '--days', str(a.days), '--start', a.start,
             '--tag', sd] + (['--param', a.param] if a.param else []),
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', env=ENV)
        out = (r.stdout or '') + (r.stderr or '')
        fh.write('>>> seed %s\n' % sd)
        fh.write(out + '\n')
        fh.flush()
        print(out, flush=True)
        # 解析需要的几行。**必须用与 --resume 同一个解析函数**, 否则续跑补进来的
        # 记录与一次跑完的记录会字段不一致(第 18 次口径问题的形态)。
        # 轮次 115 修: 抽函数时把这一行的赋值漏掉了, 于是 `name 'rec' is not defined` ——
        # 它**只在跑完一个种子之后**才炸, 而前面 a16/a17/a18 全在种子中途就死了,
        # 所以一直没暴露。教训: 工具的改动要用**跑得完的短窗口**过一遍, 不是只看语法。
        rows.append(parse_rec(out, sd))
        print('<<< seed %s 用时 %.0f s' % (sd, time.time() - t0), flush=True)

    print()
    print('=' * 92)
    print('多种子汇总(%d 年, sstAnom=%+.1f)' % (len(rows), a.sst_anom))
    for k, nm in (('total', '扰动总数'), ('dev', '发展数'), ('s100', '100-129 占比%'),
                  ('s130', '130+ 占比%'), ('p50', '峰值 P50'), ('p90', '峰值 P90'),
                  ('p95', '峰值 P95'), ('gen', '年内累计生成'), ('gdev', '年内累计发展'),
                  ('grate', '年发展率%'), ('maxlat', '最高纬中位(度)'),
                  ('p28', '到过>=28 占比%'),
                  ('dlat', '净向极位移(度)'), ('life', '寿命中位(h)')):
        v = [r[k] for r in rows if k in r]
        if not v:
            continue
        lo, hi = min(v), max(v)
        mean = sum(v) / len(v)
        # ⚠ 轮次 135: **必须把 n 打出来**。这里用的是"有这个字段的种子", 若某个种子
        # 缺字段(轮次 134 的 `gen` 就是这种情况), 均值会**静默地**只对一部分种子求,
        # 而"范围"看起来仍然正常 —— 这是"测量本身出错"最隐蔽的一种形态。
        _n = len(v)
        _warn = '' if _n == len(rows) else '  <<< 只有 %d/%d 个种子有该字段!' % (_n, len(rows))
        print('  %-14s 均值 %7.1f   范围 [%.1f, %.1f]   n=%d%s   逐种子 %s'
              % (nm, mean, lo, hi, _n, _warn,
                 ' '.join('%.1f' % x for x in v)))
    print('  观测基准(口径必须看清): 扰动 ~200(用户口径 250) | 发展 ~100(用户口径 120)')
    print('    · 100-129 / 130+ 的**可比**基准是"单年多海盆": 自评2025 11.1%/13.9%,')
    print('      自评2026 5.3%/12.3%, xrq2025 9.4%/12.5%(OBS_REFERENCE.md 3b);')
    print('      17%/18% 是 xrq/wp 的 **WP-only 多年**口径, 与本工具(全球单年)**不可直接比**。')
    print('    · 峰值基准: P50 75 | P90 130(单年) / 140(WP-only) | P95 147-155')
    print('  **注意**: "扰动总数"是最近 120 天内的**保留数**(会饱和),'
          ' "年内累计生成"才是真年生成量;比较年生成量必须用后者。')
    print('  任务 A 基准: 最高纬中位 25-26 度 | 到过>=28 **40-45%%** | 净向极位移 10-13 度 | 寿命 180-222h')
    print('  **判读规则**: 若范围跨过观测值, 则该指标"与观测无显著差别", 不得再拿单点差去做取舍。')
    fh.close()
    print('-> %s' % outp)


if __name__ == '__main__':
    main()
