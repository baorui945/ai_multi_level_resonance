# -*- coding: utf-8 -*-
"""信号横截面有效性检验（IC）：在「同月同批信号」内部，形态特征能否预测后续 alpha。

若 IC ≈ 0，说明这批信号在截面上没有区分度——换参数、换仓位上限、加止盈都救不回来，
只能换信号。

用法：
    python analyze_signal_ic.py --years 2019-2025
    python analyze_signal_ic.py --years 2025 --min-n 5

口径：
* 信号来自 `output/shape_gc_099_fwd10_wkmacd_{year}.xlsx`
* 逐笔盈亏来自 `output/multi_year_exit_mp42.xlsx`（混合规则，全信号等权，无仓位上限偏差）
* alpha = 逐笔净收益% − 沪深300 同区间收益%
* IC = 同一建仓月内，特征值与 alpha 的 Spearman 秩相关（样本 ≥ min-n 才计入）
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

from mlr import settings as S

FEATS = ['窗口score', 'corr_DIF', 'corr_DEA', 'DIF', 'DEA', '柱(DIF-DEA)',
         '窗口涨跌幅%', '周线柱(当前周)', '周线DIF(当前周)']
# 注意：'10日涨跌幅%' 是「金叉日后 10 个交易日的涨跌幅」= 未来信息，绝不能当选股特征
# （它对本样本 alpha 的 IC 高达 0.78，纯属前视，不作为可用信号）
HYB = '10日减半+13日清仓+5%移动止盈'


def load_index():
    dates, close = pd.read_pickle(S.IDX_PKL)
    idx = pd.to_datetime(dates.astype(str), format='%Y%m%d')
    s = pd.Series(close.astype(float), index=idx)
    return s[~s.index.duplicated(keep='last')].sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2019-2025')
    ap.add_argument('--min-n', type=int, default=5)
    ap.add_argument('--trades', default=r'output\multi_year_exit_mp42.xlsx')
    args = ap.parse_args()
    y0, y1 = [int(x) for x in args.years.split('-')]

    sig = []
    for y in range(y0, y1 + 1):
        p = os.path.join('output', f'shape_gc_099_fwd10_wkmacd_{y}.xlsx')
        if os.path.isfile(p):
            sig.append(pd.read_excel(p, dtype={'代码': str}))
    sig = pd.concat(sig, ignore_index=True)
    sig['建仓日'] = pd.to_datetime(sig['金叉日(=窗口终点)']).dt.normalize()
    sig = sig.drop_duplicates(['代码', '建仓日'])

    d = pd.read_excel(args.trades, sheet_name='逐笔明细', dtype={'代码': str})
    d = d[d['规则'] == HYB].copy()
    d['建仓日'] = pd.to_datetime(d['建仓日']).dt.normalize()
    d['卖出日'] = pd.to_datetime(d['卖出日'])
    idx = load_index()
    d['指数%'] = [float(idx.asof(t) / idx.asof(e) - 1) * 100
                  for e, t in zip(d['建仓日'], d['卖出日'])]
    d['alpha%'] = d['净收益%'] - d['指数%']

    m = d.merge(sig[['代码', '建仓日'] + FEATS], on=['代码', '建仓日'], how='inner')
    print(f'信号 {len(sig)} 个，成交 {len(d)} 笔，可匹配特征 {len(m)} 笔'
          f'（未匹配多为同票重复信号被跳过）')
    m['年月'] = m['建仓日'].dt.to_period('M')
    m['年'] = m['建仓日'].dt.year

    rows = []
    for f in FEATS:
        for per, name in [(m['年'], '年'), (m['年月'], '月')]:
            ics = []
            for _, g in m.groupby(per):
                g = g[[f, 'alpha%']].dropna()
                if len(g) >= args.min_n:
                    ics.append(g[f].corr(g['alpha%'], method='spearman'))
            ics = pd.Series(ics, dtype=float)
            rows.append({'特征': f, '分组': name, '组数': len(ics),
                         'IC均值': ics.mean(), 'IC标准差': ics.std(),
                         'IC>0占比%': (ics > 0).mean() * 100,
                         'ICIR': ics.mean() / ics.std() if ics.std() else np.nan,
                         't': ics.mean() / ics.std() * np.sqrt(len(ics)) if ics.std() else np.nan})
    tab = pd.DataFrame(rows)
    tab = tab[tab['分组'] == '月'].sort_values('IC均值', key=abs, ascending=False)
    print('\n== 月度横截面 IC（Spearman，按建仓月分组）==')
    print(tab.drop(columns='分组').round(3).to_string(index=False))

    best = tab.iloc[0]['特征'] if len(tab) else None
    if best:
        print(f'\n== 按「{best}」分组的 alpha（月度内排名前 1/3 vs 后 1/3）==')
        out = []
        for k, g in m.groupby('年月'):
            g = g[[best, 'alpha%']].dropna()
            if len(g) < 6:
                continue
            kk = max(1, len(g) // 3)
            g = g.sort_values(best, ascending=False)
            out.append({'年月': str(k), '前1/3均值%': g['alpha%'].head(kk).mean(),
                        '后1/3均值%': g['alpha%'].tail(kk).mean()})
        if out:
            print(pd.DataFrame(out).round(2).to_string(index=False))

    print('\n== 逐年 alpha（混合规则，全信号等权）==')
    print(m.groupby('年')['alpha%'].agg(['size', 'mean', 'median',
                                         lambda x: (x > 0).mean() * 100]).round(2).to_string())


if __name__ == '__main__':
    main()
