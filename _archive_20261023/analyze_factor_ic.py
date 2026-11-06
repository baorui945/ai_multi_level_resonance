# -*- coding: utf-8 -*-
"""在「形态信号池」里做因子 IC 筛查：这些信号内部还有没有可用的横截面区分度？

只用建仓日及之前的数据计算因子（无前视），目标是 alpha = 逐笔净收益% − 沪深300 同区间%。

用法：
    python analyze_factor_ic.py --years 2019-2025
    python analyze_factor_ic.py --years 2019-2025 --min-n 5 --out output/factor_ic.xlsx

因子（全部只用 t 日及以前数据）：
    mom20/mom60      20/60 日动量
    rev5             5 日反转（取负号，越大=前期越跌）
    vol20            20 日波动率
    ma60dist         收盘/MA60 − 1
    rsi14            RSI(14)
    liq20            log(20 日平均成交额)
    vr20             当日成交量/20 日均量
    hh60dist         收盘/60 日最高 − 1
"""
import argparse

import numpy as np
import pandas as pd

from mlr import data as D
from mlr import settings as S

HYB = '10日减半+13日清仓+5%移动止盈'


def load_index():
    dates, close = pd.read_pickle(S.IDX_PKL)
    idx = pd.to_datetime(dates.astype(str), format='%Y%m%d')
    s = pd.Series(close.astype(float), index=idx)
    return s[~s.index.duplicated(keep='last')].sort_index()


def build_factors(codes, dates):
    px = D.load_daily(verbose=False)
    px = px[px['stock_code'].isin(codes)].sort_values(['stock_code', 'trade_date'])
    out = []
    for code, g in px.groupby('stock_code'):
        g = g.set_index('trade_date')
        c = g['close_price'].astype(float)
        v = g['volume'].astype(float)
        a = g['amount'].astype(float)
        f = pd.DataFrame(index=g.index)
        f['mom20'] = c / c.shift(20) - 1
        f['mom60'] = c / c.shift(60) - 1
        f['rev5'] = -(c / c.shift(5) - 1)
        ret = c.pct_change()
        f['vol20'] = ret.rolling(20).std()
        f['ma60dist'] = c / c.rolling(60).mean() - 1
        up = ret.clip(lower=0).rolling(14).mean()
        dn = (-ret.clip(upper=0)).rolling(14).mean()
        f['rsi14'] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        f['liq20'] = np.log(a.rolling(20).mean())
        f['vr20'] = v / v.rolling(20).mean()
        f['hh60dist'] = c / g['high_price'].astype(float).rolling(60).max() - 1
        f['stock_code'] = code
        out.append(f.reindex(dates, level=0).dropna(how='all'))
    f = pd.concat(out)
    return f.reset_index().rename(columns={'index': 'trade_date'})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2019-2025')
    ap.add_argument('--trades', default=r'output\multi_year_exit_mp42.xlsx')
    ap.add_argument('--min-n', type=int, default=5)
    ap.add_argument('--out', default=r'output\factor_ic.xlsx')
    args = ap.parse_args()

    d = pd.read_excel(args.trades, sheet_name='逐笔明细', dtype={'代码': str})
    d = d[d['规则'] == HYB].copy()
    d['建仓日'] = pd.to_datetime(d['建仓日']).dt.normalize()
    d['卖出日'] = pd.to_datetime(d['卖出日'])
    y0, y1 = [int(x) for x in args.years.split('-')]
    d = d[(d['建仓日'].dt.year >= y0) & (d['建仓日'].dt.year <= y1)]
    idx = load_index()
    d['指数%'] = [float(idx.asof(t) / idx.asof(e) - 1) * 100
                  for e, t in zip(d['建仓日'], d['卖出日'])]
    d['alpha%'] = d['净收益%'] - d['指数%']

    fac = build_factors(set(d['代码']), set(d['建仓日']))
    m = d.merge(fac, left_on=['代码', '建仓日'],
                right_on=['stock_code', 'trade_date'], how='left')
    print(f'成交 {len(d)} 笔，匹配到因子 {m["mom20"].notna().sum()} 笔')
    m['年月'] = m['建仓日'].dt.to_period('M')

    cols = ['mom20', 'mom60', 'rev5', 'vol20', 'ma60dist', 'rsi14', 'liq20',
            'vr20', 'hh60dist']
    rows = []
    for f in cols:
        ics = []
        for _, g in m.groupby('年月'):
            g = g[[f, 'alpha%']].dropna()
            if len(g) >= args.min_n:
                ics.append(g[f].corr(g['alpha%'], method='spearman'))
        ics = pd.Series(ics, dtype=float)
        rows.append({'因子': f, '组数': len(ics), 'IC均值': ics.mean(),
                     'IC标准差': ics.std(), 'IC>0占比%': (ics > 0).mean() * 100,
                     'ICIR': ics.mean() / ics.std() if ics.std() else np.nan,
                     't': ics.mean() / ics.std() * np.sqrt(len(ics)) if ics.std() else np.nan})
    tab = pd.DataFrame(rows).sort_values('IC均值', key=abs, ascending=False)
    print('\n== 信号池内因子月度横截面 IC（对 alpha）==')
    print(tab.round(3).to_string(index=False))
    tab.round(4).to_excel(args.out, index=False)
    print('\n[OK] 已保存', args.out)


if __name__ == '__main__':
    main()
