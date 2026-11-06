# -*- coding: utf-8 -*-
"""
==============================================================================
burst_validate.py —— 爆发形态评分的统计验证
==============================================================================

检验内容：

1. 逐笔净 alpha（t+1 开盘口径，扣 0.2% 成本）在 5/10/20/40 日上的表现；
2. score 与 5 个单维度的五分组单调性 —— 维度的**方向**是否与假设一致；
3. 基准对照：同日随机、同日「低分但同样通过结构约束」匹配池；
4. 权重敏感性：等权 / 默认 / 各维度加权 30% 时，头部与尾部的 alpha 差；
5. 分数门槛敏感性、样本内外、逐年；
6. 可选网格：N×M 三组重算（--grid）。

口径全部复用 `coil.evaluate`（t+1 开盘、一字板剔除、沪深300 同期基准），
只读工具函数，不读取 coil 的任何策略参数。

用法::

    python burst_validate.py --universe all
    python burst_validate.py --universe all --grid
    python burst_validate.py --universe hs300 --no-hard
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

import burst_scan as SCN
from burst import settings as BS
from coil import evaluate as EV

SCHEMES = {
    '原式v01(30/20/20/15/15)': {'breakout': .30, 'accel': .20, 'body': .20,
                            'bull': .15, 'squeeze': .15},
    '修正a-heavy(55/25/10/5/5)': {'breakout': .55, 'accel': .25, 'body': .10,
                               'bull': .05, 'squeeze': .05},
    '纯A突破(100)': {'breakout': 1.0, 'accel': 0.0, 'body': 0.0,
                  'bull': 0.0, 'squeeze': 0.0},
    '等权(20×5)': {'breakout': .20, 'accel': .20, 'body': .20,
                 'bull': .20, 'squeeze': .20},
    'A突破50%': {'breakout': .50, 'accel': .125, 'body': .125,
                'bull': .125, 'squeeze': .125},
    'B加速50%': {'breakout': .125, 'accel': .50, 'body': .125,
                'bull': .125, 'squeeze': .125},
    'C实体50%': {'breakout': .125, 'accel': .125, 'body': .50,
                'bull': .125, 'squeeze': .125},
    'E逼空50%': {'breakout': .125, 'accel': .125, 'body': .125,
                'bull': .125, 'squeeze': .50},
    '无突破项': {'breakout': 0.0, 'accel': .25, 'body': .25,
               'bull': .25, 'squeeze': .25},
}


def _parse_args():
    p = argparse.ArgumentParser(description='BURST 爆发形态评分统计验证')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--start', default='', help='默认读配置 backtest.date_start')
    p.add_argument('--end', default='', help='默认缓存最新日')
    p.add_argument('--split', default='', help='样本内外切分日')
    p.add_argument('--min-score', type=float, default=None)
    p.add_argument('--n', type=int, default=0)
    p.add_argument('--m', type=int, default=0)
    p.add_argument('--no-hard', action='store_true', help='关闭最低结构约束')
    p.add_argument('--grid', action='store_true', help='额外跑 N×M 网格（较慢）')
    p.add_argument('--out', default='')
    return p.parse_args()


def _load(a):
    ns = argparse.Namespace(universe=a.universe, codes='')
    return SCN._load_px(ns)



# ═════════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════════
def _prep(sig):
    """统一成 event_study 需要的列名，保留 score / _z_* 等附加列。"""
    d = sig.rename(columns={'代码': 'code', '信号日': 'date', '收盘': 'close'})
    cols = ['code', 'date', 'close', 'score'] + \
        [c for c in d.columns if c.startswith('_')]
    return d[cols].copy()


def _score_by(detail, w):
    """按给定权重重算总分（用已存的归一化分，避免重算特征）。"""
    wsum = sum(w.values())
    s = np.zeros(len(detail), dtype='float64')
    for k, v in w.items():
        s += detail['_z_' + k].to_numpy('float64') * float(v)
    return 100.0 * s / wsum


def _qmeans(detail, key, h, oo=True):
    """五分组均值，返回 list[float]（按组 1..5），不可分组时返回 []。"""
    if key not in detail or len(detail) < 25:
        return []
    q = EV.quintile_table(detail, [h], key=key, q=5, oo=oo)
    if not len(q):
        return []
    return [float(x) for x in q.sort_values('分组')['均值alpha'].tolist()]


def _is_monotonic(vals):
    if len(vals) < 5:
        return False, 0.0
    diffs = np.diff(vals)
    return bool((diffs > 0).all()), float(vals[-1] - vals[0])


def _verdict(t_main, mono, spread, grid_pos, grid_n, oos_mean, delta):
    rows = [
        ('主持有期净 alpha 的按日聚合 t ≥ 2',
         f't={t_main:.2f}' if np.isfinite(t_main) else 'NA',
         '达标' if np.isfinite(t_main) and t_main >= 2 else '未达标'),
        ('相对同日低分池的增量 > 0',
         f'{delta:+.2f}pp' if np.isfinite(delta) else 'NA',
         '达标' if np.isfinite(delta) and delta > 0 else '未达标'),
        ('评分五分组单调（Q5>Q1 且逐步抬升）',
         '单调' if mono else f'非单调（Q5-Q1={spread:+.2f}pp）',
         '达标' if mono else '未达标'),
        ('参数档同号（≥60% 为正）',
         f'{grid_pos}/{grid_n}' if grid_n else '未检验',
         '达标' if grid_n and grid_pos / grid_n >= 0.6 else '未达标'),
        ('样本外均值不翻负',
         f'{oos_mean:+.2f}%' if np.isfinite(oos_mean) else 'NA',
         '达标' if np.isfinite(oos_mean) and oos_mean > 0 else '未达标'),
    ]
    return pd.DataFrame(rows, columns=['检验项', '结果', '判定'])



def _t(x):
    x = np.asarray(x, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size < 3:
        return np.nan
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(x.size))) if sd > 0 else np.nan


def _add_cluster_t(detail, summ, H):
    """按交易日聚合的 t：同一天几十条信号高度相关，逐笔 t 会严重高估。

    做法：先算每个交易日的横截面均值 alpha，再对日序列求 t。持有期重叠仍会
    残留自相关，所以这个 t 是偏保守的参考值，不是严格 HAC。
    """
    out = summ.copy()
    td, nd = [], []
    for h in H:
        d = detail
        if 'untradable' in d:
            d = d[~d['untradable'].to_numpy(bool)]
        key = f'a{h}_oo' if f'a{h}_oo' in d else f'a{h}'
        g = d.groupby(pd.to_datetime(d['date']))[key].mean()
        g = g[np.isfinite(g)]
        td.append(round(_t(g.to_numpy()), 2) if len(g) else np.nan)
        nd.append(int(len(g)))
    out['交易日数'] = nd
    out['按日聚合t'] = td
    return out


def _weight_table(detail, h):
    """同一批信号，换权重后看头/尾五分位的 alpha 差（检验权重是否有信息）。"""
    keep = ~detail['untradable'].to_numpy(bool) \
        if 'untradable' in detail else np.ones(len(detail), bool)
    rows = []
    for name, w in SCHEMES.items():
        s = _score_by(detail, w)
        d = detail.assign(_w=s)[keep]
        if len(d) < 50:
            continue
        grp = pd.qcut(d['_w'].rank(method='first'), 5, labels=False)
        top = d.loc[grp == 4, f'a{h}_oo'].to_numpy('float64')
        bot = d.loc[grp == 0, f'a{h}_oo'].to_numpy('float64')
        tt = _t(top)
        rows.append({'权重方案': name,
                     '头部均值%': round(float(np.nanmean(top)) * 100, 2),
                     '尾部均值%': round(float(np.nanmean(bot)) * 100, 2),
                     '头-尾(pp)': round(float(np.nanmean(top)
                                             - np.nanmean(bot)) * 100, 2),
                     '头部t': round(tt, 2) if np.isfinite(tt) else np.nan,
                     'n头部': int(np.isfinite(top).sum())})
    return pd.DataFrame(rows)


def _grid(px, cfg, mat, index, H, main_h, end, hard, min_score):
    import copy
    combos = [(3, 20), (5, 20), (8, 20), (5, 60), (10, 20), (10, 60)]
    out = []
    for n, m in combos:
        c = copy.deepcopy(cfg)
        c['shape']['n'], c['shape']['m'] = n, m
        t0 = time.time()
        s = SCN.scan(px, c, cfg['backtest']['date_start'], end,
                     min_score, hard=hard)
        if not len(s):
            print(f'  [网格] n={n} m={m} 无信号')
            continue
        det = EV.event_study(mat, index, _prep(s), H)
        summ = EV.summarize(det, [main_h], label=f'N{n}/M{m}')
        summ['N'], summ['M'] = n, m
        summ['信号数'] = len(s)
        out.append(summ)
        print(f'  [网格] n={n} m={m} → {len(s)} 条，'
              f'{summ["均值alpha"].iloc[0]:+.2f}%，'
              f't={summ["t值"].iloc[0]:.2f}'
              f'（{time.time() - t0:.0f}s）')
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()



def _notes(cfg, a):
    return pd.DataFrame({'项目': [
        'alpha 口径', '成本', '入场价', '一字板', 't 值口径',
        '分组 n', '同日随机基准', '同日低分池基准', '权重敏感性',
        '样本内外', '信号数口径'],
        '说明': [
        '个股区间收益 − 沪深300 同期收益 − 成本；默认报「t+1 开盘→h 日后开盘」',
        f'{cfg["exit"]["cost"] * 100:.1f}%（双边合计）',
        '信号日 T 收盘后确认，T+1 开盘可执行',
        'T+1 开盘相对 T 收盘涨幅 ≥ 9.5%/19.5% 视为买不进，alpha 统计时剔除',
        '「t值」为逐笔 t（会因同日几十条信号高度相关而高估）；'
        '「按日聚合t」先按交易日取横截面均值再求 t，作为判定依据',
        '分位分组按 rank 五等分；n 是信号条数，不是独立事件数',
        '同日期、同股票池随机抽样，n 与真实信号数一致',
        '与 BURST 同日期、同样通过结构约束、但 score 处于最低 40% 的信号，'
        '1:1 匹配抽样；用来检验「评分本身」是否有增量信息',
        '用同一批信号的归一化分重算总分，只改权重，不重算特征',
        '样本内 < split ≤ 样本外；权重方案在样本内与样本外分别报告',
        '同一股票两次信号至少间隔 cooldown 个交易日',
    ]})


def main():
    a = _parse_args()
    cfg = BS.load_config()
    cfg['signal']['min_score'] = 0.0          # 验证默认跑全样本，保留分位信息
    if a.min_score is not None:
        cfg['signal']['min_score'] = float(a.min_score)
    if a.n:
        cfg['shape']['n'] = int(a.n)
    if a.m:
        cfg['shape']['m'] = int(a.m)
    BS.ensure_dirs()
    px = _load(a)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp(px['trade_date'].max())
    start = pd.Timestamp(a.start or cfg['backtest']['date_start'])
    split = pd.Timestamp(a.split or cfg['backtest']['split'])
    H = [int(x) for x in cfg['exit']['horizons']]
    main_h = int(cfg['exit']['main_horizon'])
    hard = not a.no_hard
    print(f'[池] {a.universe} | [区间] {start.date()} ~ {end.date()} | '
          f'切分 {split.date()} | N={cfg["shape"]["n"]} M={cfg["shape"]["m"]} '
          f'| hard={hard} | min_score={cfg["signal"]["min_score"]}')

    mat, index = EV.build_matrices(px), EV.load_index()
    t0 = time.time()
    sig = SCN.scan(px, cfg, start, end, cfg['signal']['min_score'],
                   hard=hard, want_conv=True)
    print(f'[信号] {len(sig)} 条 / '
          f'{sig["代码"].nunique() if len(sig) else 0} 只'
          f'（{time.time() - t0:.0f}s）')
    if not len(sig):
        raise SystemExit('区间内无信号：放宽 --min-score 或检查参数')

    detail = EV.event_study(mat, index, _prep(sig), H)
    dts = pd.to_datetime(detail['date'])
    summ = _add_cluster_t(detail, EV.summarize(detail, H, '全部'), H)

    qs = [EV.quintile_table(detail, H, key='score')]
    for k in ['breakout', 'accel', 'body', 'bull', 'squeeze']:
        qs.append(EV.quintile_table(detail, H, key='_S_' + k))
    qtab = pd.concat([x for x in qs if len(x)], ignore_index=True)

    rnd = EV.random_signals(mat, max(len(detail), 50), start, end,
                            seed=int(cfg['backtest']['seed']),
                            min_future=max(H) + 2)
    d_rand = EV.event_study(mat, index, rnd, H)
    b_rand = _add_cluster_t(d_rand, EV.summarize(d_rand, H, '同日随机'), H)
    pool = SCN.scan(px, cfg, start, end, min_score=-1e9, hard=hard)
    cut = float(pool['score'].quantile(0.4)) if len(pool) else np.nan
    low = pool[pool['score'] <= cut] if len(pool) else pool
    mch = EV.matched_price_only(
        _prep(sig), _prep(low)[['code', 'date', 'close']],
        seed=int(cfg['backtest']['seed']))
    d_low = EV.event_study(mat, index, mch, H)
    b_low = _add_cluster_t(
        d_low, EV.summarize(d_low, H, f'同日低分池(<{cut:.0f}分)'), H)
    base = pd.concat([summ.assign(组='BURST'),
                      b_rand.assign(组='同日随机'),
                      b_low.assign(组='同日低分匹配')], ignore_index=True)

    wt = pd.concat([
        _weight_table(detail, main_h).assign(样本='全样本'),
        _weight_table(detail[dts < split], main_h).assign(样本='样本内'),
        _weight_table(detail[dts >= split], main_h).assign(样本='样本外'),
    ], ignore_index=True)
    th = []
    for ms in (0, 40, 50, 60, 70, 80):
        s = EV.summarize(detail[detail['score'] >= ms], H, f'≥{ms}')
        s['门槛'] = ms
        th.append(s)
    th = pd.concat(th, ignore_index=True)

    iso = pd.concat([
        EV.summarize(detail[dts < split], H, '样本内'),
        EV.summarize(detail[dts >= split], H, '样本外')], ignore_index=True)
    yr = EV.by_year(detail, main_h)



    grid = _grid(px, cfg, mat, index, H, main_h, end, hard,
                 float(BS.load_config()['signal']['min_score'])) \
        if a.grid else pd.DataFrame()

    tm = summ.loc[summ['持有期'] == main_h, '按日聚合t']
    t_main = float(tm.iloc[0]) if len(tm) else np.nan
    vals = _qmeans(detail, 'score', main_h)
    mono, spread = _is_monotonic(vals)
    oos = iso[(iso['分组'] == '样本外') & (iso['持有期'] == main_h)]
    oos_mean = float(oos['均值alpha'].iloc[0]) if len(oos) else np.nan
    b_row = base[(base['组'] == 'BURST') & (base['持有期'] == main_h)]
    l_row = base[(base['组'] == '同日低分匹配') & (base['持有期'] == main_h)]
    delta = float(b_row['均值alpha'].iloc[0] - l_row['均值alpha'].iloc[0]) \
        if len(b_row) and len(l_row) else np.nan
    gpos = int((grid['均值alpha'] > 0).sum()) if len(grid) else 0
    gn = len(grid) if len(grid) else 0
    verdict = _verdict(t_main, mono, spread, gpos, gn, oos_mean, delta)

    out = a.out or os.path.join(
        BS.OUTPUT_DIR, f'burst_validate_{end.strftime("%Y%m%d")}.xlsx')
    books = [('结论摘要', verdict), ('持有期汇总', summ), ('基准对照', base),
             ('分组单调', qtab), ('权重敏感性', wt), ('门槛敏感性', th),
             ('样本内外', iso), ('分年', yr), ('口径说明', _notes(cfg, a))]
    if len(grid):
        books.append(('参数网格', grid))
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        for name, df in books:
            df.to_excel(xw, sheet_name=name, index=False)
        det_out = sig if len(sig) <= 50000 else sig.head(50000)
        det_out.to_excel(xw, sheet_name='信号明细', index=False)
    if len(sig) > 50000:
        print(f'[注意] 信号 {len(sig)} 条，明细表只写前 50000 条')
    print()
    print(verdict.to_string(index=False))
    print(f'[输出] {out}')


if __name__ == '__main__':
    main()

