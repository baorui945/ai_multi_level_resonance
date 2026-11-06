# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.resample —— 日线 → 周线（同一套 v2 判定用的周期重采样）
==============================================================================
周线 K 线：按 W-FRI 聚合，trade_date = 该周最后一个交易日（周期标记），
period_end = 名义周五（判「已收盘」用），week_first = 该周第一个交易日
（下一周的开仓日 D4 用它）。

只做重采样，不做任何判定；判定仍走 ma7daily.upthrow.detect_frame。
"""
import numpy as np
import pandas as pd


def to_weekly(daily, rule='W-FRI'):
    """日线长表 → 周线长表。

    Returns:
        stock_code, stock_name, trade_date（该周最后交易日）, week_first,
        period_end（名义周五）, open/high/low/close/volume/amount
    """
    daily = daily.sort_values(['stock_code', 'trade_date'])
    out = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.set_index('trade_date')
        r = g.resample(rule)
        w = pd.DataFrame({
            'open_price': r['open_price'].first(),
            'high_price': r['high_price'].max(),
            'low_price': r['low_price'].min(),
            'close_price': r['close_price'].last(),
            'volume': r['volume'].sum(min_count=1),
            'amount': r['amount'].sum(min_count=1),
        }).dropna(subset=['close_price'])
        if w.empty:
            continue
        idx = g.index.to_series().groupby(pd.Grouper(freq=rule))
        w['trade_date'] = idx.last().reindex(w.index)
        w['week_first'] = idx.min().reindex(w.index)
        w['period_end'] = pd.DatetimeIndex(w.index)
        w['stock_code'] = code
        w['stock_name'] = g['stock_name'].iloc[-1]
        out.append(w.reset_index(drop=True))
    if not out:
        return pd.DataFrame(columns=['stock_code', 'stock_name', 'trade_date',
                                     'week_first', 'period_end', 'close_price'])
    res = pd.concat(out, ignore_index=True)
    return res[['stock_code', 'stock_name', 'trade_date', 'week_first',
                'period_end', 'open_price', 'high_price', 'low_price',
                'close_price', 'volume', 'amount']]
