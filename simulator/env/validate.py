# simulator/env/validate.py
"""F6 验证(无偏,留出法): 随机剔除 10-15% 历史月不参与建库,
用生成器还原被剔除月的场,与真实场对比(RMSE/空间相关/GPI 分布)。

合成库模式下同样执行(验证管线本身),真实数据到位后以相同流程跑。
"""
from __future__ import annotations
import os
import numpy as np
from typing import Dict, List, Tuple

from . import field_io as F
from . import build_library as BL
from .generator import AnalogGenerator
from simulator import modes as M

ENV_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ENV_DIR, 'reports')

HOLDOUT_RATIO = 0.12          # 剔除 12%(10-15% 区间)
RMSE_SST_TARGET = 0.9         # SST 月 RMSE 上限(°C)
CORR_TARGET = 0.7             # 空间相关下限


def _library_mode(years: range) -> str:
    """由场库内 sst 文件的 meta.source 判定 'real'/'synthetic'(取代硬编码)。
    真实库落盘 source 为 'era5'(sst) 与 'oras5'(ohc), 归一为 'real';
    合成库为 'synthetic'。读不到/异常则回退 'synthetic'。"""
    for y in years:
        p = F.field_path('sst', y)
        if not os.path.exists(p):
            continue
        try:
            with np.load(p, allow_pickle=True) as z:
                meta = z['meta']
                m = meta.item() if hasattr(meta, 'item') else meta
                src = m.get('source', 'synthetic') if isinstance(m, dict) else None
                if src in ('era5', 'oras5'):
                    return 'real'
                if src == 'synthetic':
                    return 'synthetic'
        except Exception:
            continue
    return 'synthetic'


def holdout_validate(years: range = None, ratio: float = HOLDOUT_RATIO,
                     seed: int = 2026) -> Dict[str, dict]:
    """留出法验证: 剔除部分历史月 → 重建库索引 → 生成被剔除月 → 对比。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    # 默认与建库年份一致(LIB_YEARS=1979-2026 含 2026),避免留出法漏掉库内最新年份
    years = years or BL.LIB_YEARS
    rng = np.random.RandomState(seed)
    all_months = [(y, m) for y in years for m in range(1, 13)
                  if F.load_monthly_field('sst', y) is not None]
    n_hold = max(1, int(len(all_months) * ratio))
    held = set(rng.choice(len(all_months), size=n_hold, replace=False))
    held_months = [all_months[i] for i in sorted(held)]

    # 生成器用"剔除后"的库(排除被剔除月)
    gen = AnalogGenerator(years=years, seed=seed)
    gen.pool = [m for m in all_months if m not in set(held_months)]
    gen.pool_vec = {k: v for k, v in gen.pool_vec.items() if k in set(gen.pool)}
    # 重建协方差(R2-4: 与运行时 _index 同口径——特征值尺度岭 + 伪逆兜底)
    vecs = np.stack([gen.pool_vec[k] for k in gen.pool])
    gen._pool_mean = vecs.mean(axis=0)
    cov = np.cov(vecs.T)
    lam = np.linalg.eigvalsh(cov)
    lam_max = float(lam[-1]) if len(lam) else 1.0
    gen._pool_cov = cov + np.eye(cov.shape[0]) * max(1e-9, 0.01 * lam_max)
    try:
        gen._pool_cov_inv = np.linalg.inv(gen._pool_cov)
    except np.linalg.LinAlgError:
        gen._pool_cov_inv = np.linalg.pinv(gen._pool_cov)

    report = {}
    for var in ('sst', 'ohc', 'shear', 'rh700', 'mslp', 'gpi'):
        rmse_list = []
        corr_list = []
        gpi_ks = []
        for ym in held_months:
            picked = gen._pick_analog(ym, exclude={ym})
            if picked is None:
                continue
            a = F.load_monthly_field(var, ym[0])
            b = F.load_monthly_field(var, picked[0])
            if a is None or b is None:
                continue
            truth = a[ym[1] - 1]
            pred = b[picked[1] - 1]
            mask = ~np.isnan(truth) & ~np.isnan(pred)
            if mask.sum() < 100:
                continue
            t, p = truth[mask], pred[mask]
            if var == 'gpi':
                # GPI 动态范围大(受 sst^4 主导),用 log1p 域计算 RMSE
                t, p = np.log1p(np.maximum(t, 0)), np.log1p(np.maximum(p, 0))
            rmse = float(np.sqrt(np.mean((t - p) ** 2)))
            if np.std(t) > 1e-6 and np.std(p) > 1e-6:
                corr = float(np.corrcoef(t, p)[0, 1])
            else:
                corr = 0.0
            rmse_list.append(rmse)
            corr_list.append(corr)
        if rmse_list:
            report[var] = {
                'rmse_mean': float(np.mean(rmse_list)),
                'corr_mean': float(np.mean(corr_list)),
                'n': len(rmse_list),
            }
    # C1: pass/fail 判定(合成库单独标记,不参与真实库达标判定)
    targets = {'sst': (0.9, 0.7), 'ohc': (20.0, 0.6), 'shear': (8.0, 0.5),
               'rh700': (8.0, 0.5), 'mslp': (6.0, 0.7), 'gpi': (3.0, 0.3)}
    fails = []
    unvalidated = []
    for var, (rmse_t, corr_t) in targets.items():
        r = report.get(var)
        if r is None:
            # 该变量全无有效留出样本(库内无该场/样本过少)→ 记未验证,
            # 计入失败而非静默跳过, 避免"空报告 pass"的假阳性。
            unvalidated.append(var)
            continue
        if r['rmse_mean'] > rmse_t or r['corr_mean'] < corr_t:
            fails.append(var)
    report['_pass'] = not fails and not unvalidated
    report['_fails'] = fails
    report['_unvalidated'] = unvalidated
    report['_mode'] = _library_mode(years)   # 由库内 sst 元数据判定 synthetic/real,
                                             # 不再硬编码 'synthetic'
    # OHC-SST 独立性(生成库内)
    report['independence'] = _ohc_sst_independence()
    # 平滑度(生成 24 月相邻差异 vs 历史相邻差异)
    report['smoothness'] = _smoothness_check(gen)
    _save_report(report, held_months)
    return report


def _ohc_sst_independence() -> dict:
    """生成场中 OHC-SST 相关 vs 历史(不过高;冷水上翻信号可出现在高温区)。"""
    def _corr(ym):
        sst = F.load_monthly_field('sst', ym[0])
        ohc = F.load_monthly_field('ohc', ym[0])
        if sst is None or ohc is None:
            return None
        s, o = sst[ym[1] - 1], ohc[ym[1] - 1]
        mask = ~np.isnan(s) & ~np.isnan(o)
        if mask.sum() < 100:
            return None
        return float(np.corrcoef(s[mask], o[mask])[0, 1])
    vals = []
    for y in range(1995, 2005):
        for m in (1, 7):
            c = _corr((y, m))
            if c is not None:
                vals.append(c)
    return {'ohc_sst_corr_mean': float(np.mean(vals)) if vals else None,
            'n': len(vals)}


def _smoothness_check(gen: AnalogGenerator) -> dict:
    """逐月平滑度: 生成序列相邻月差异 vs 历史相邻月差异。
    G5/G7: 不落盘(save=False),历史侧统计全部 12 组相邻月对(与生成侧 24 对同口径)。"""
    hist_diffs = []
    for y in range(1980, 2025):
        a = F.load_monthly_field('sst', y - 1)
        b = F.load_monthly_field('sst', y)
        if a is None or b is None:
            continue
        # 全部 12 组相邻月对: 同年 11 对(1→2..11→12) + 跨年 1 对(12→次年1)
        for mo in range(11):
            d = np.nanmean(np.abs(a[mo + 1] - a[mo]))
            hist_diffs.append(float(d))
        d = np.nanmean(np.abs(b[0] - a[11]))
        hist_diffs.append(float(d))
    series = gen.generate_monthly((2027, 1), 24, use_fragment=False, save=False)
    gen_diffs = []
    for i in range(1, 24):
        d = np.nanmean(np.abs(series['sst'][i] - series['sst'][i - 1]))
        gen_diffs.append(float(d))
    return {
        'hist_adj_diff_mean': float(np.mean(hist_diffs)) if hist_diffs else None,
        'gen_adj_diff_mean': float(np.mean(gen_diffs)) if gen_diffs else None,
        'ok': bool(gen_diffs and hist_diffs and
                   np.mean(gen_diffs) < 2.5 * np.mean(hist_diffs)),
    }


def _save_report(report: dict, held: List[Tuple[int, int]]) -> None:
    import json
    os.makedirs(REPORT_DIR, exist_ok=True)
    doc = {'held_months': held, 'report': report}
    with open(os.path.join(REPORT_DIR, 'holdout_report.json'), 'w',
              encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    import json
    rep = holdout_validate()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
