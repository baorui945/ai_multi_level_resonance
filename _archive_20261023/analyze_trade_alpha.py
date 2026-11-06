# -*- coding: utf-8 -*-
"""逐笔 alpha 分解：把每笔交易的净收益，减去「同区间沪深300 收益」，看信号是否真有选股能力。

用法：
    python analyze_trade_alpha.py --trades output/multi_year_exit_mp42.xlsx
    python analyze_trade_alpha.py --trades output/exit_rules_2025.xlsx --rule 减半

输出：
* 每笔 alpha = 净收益% − 沪深300(卖出日/建仓日 − 1)
* 按年份汇总（均值/中位/胜率/t 值/相关性/Beta）
* 逐笔收益与指数同期收益的回归（beta≈1 且 alpha≈0 ⇒ 只是在吃市场 beta）

注意：建仓/卖出均为当日收盘，指数同样取这两日收盘，区间完全对齐。
"""
import argparse

import numpy as np
import pandas as pd

from mlr import settings as S


def load_index():
    dates, close = pd.read_pickle(S.IDX_PKL)
    idx = pd.to_datetime(dates.astype(str), format='%Y%m%d')
    s = pd.Series(close.astype(float), index=idx)
    return s[~s.index.duplicated(keep='last')].sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trades', default=r'output\multi_year_exit_mp42.xlsx')
    ap.add_argument('--rule', default='减半', help='规则名包含的关键字')
    ap.add_argument('--sheet', default='逐笔明细')
    args = ap.parse_args()

    d = pd.read_excel(args.trades, sheet_name=args.sheet, dtype={'代码': str})
    d = d[d['规则'].astype(str).str.contains(args.rule)].copy()
    d['建仓日'] = pd.to_datetime(d['建仓日'])
    d['卖出日'] = pd.to_datetime(d['卖出日'])
    idx = load_index()
    d['指数%'] = [float(idx.asof(t) / idx.asof(e) - 1) * 100 for e, t in zip(d['建仓日'], d['卖出日'])]
    d['alpha%'] = d['净收益%'] - d['指数%']
    d['年'] = d['建仓日'].dt.year

    def stat(g):
        a = g['alpha%']
        r = g['净收益%']
        b = g['指数%']
        n = len(g)
        t = a.mean() / a.std(ddof=1) * np.sqrt(n) if n > 2 and a.std(ddof=1) > 0 else np.nan
        beta = np.cov(r, b)[0, 1] / np.var(b, ddof=1) if n > 2 else np.nan
        corr = np.corrcoef(r, b)[0, 1] if n > 2 else np.nan
        return pd.Series({'笔数': n, '策略均值%': r.mean(), '指数同期%': b.mean(),
                          'alpha均值%': a.mean(), 'alpha中位%': a.median(),
                          'alpha胜率%': (a > 0).mean() * 100, 'alpha_t': t,
                          'Beta(逐笔)': beta, '相关系数': corr})

    print(f'\n== {args.trades} / 规则含「{args.rule}」 ==')
    by_year = d.groupby('年').apply(stat, include_groups=False).round(2)
    print(by_year.to_string())
    print('-' * 110)
    print(stat(d).round(2).to_string())

    tot = d['净收益%'].sum()
    print(f'\n逐笔净收益合计 {tot:+.1f} 个百分点；其中指数同期贡献 {d["指数%"].sum():+.1f}，'
          f'alpha 贡献 {d["alpha%"].sum():+.1f}（alpha 占比 {d["alpha%"].sum() / tot * 100:.0f}%）')
    print('\n分年 alpha 分布（alpha>0 即跑赢同期指数）：')
    print(d.assign(年=d['年']).groupby('年')['alpha%']
          .describe(percentiles=[.25, .5, .75]).round(2).to_string())


if __name__ == '__main__':
    main()
