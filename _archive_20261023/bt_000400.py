# -*- coding: utf-8 -*-
"""v0.4 抛物线规则：000400 全部信号的逐笔回测。"""
import numpy as np
import pandas as pd

import launch_scan as LS

cfg = LS.load_config()
px = LS.load_px('all', '000400')
sig = LS.scan(px, cfg, '2017-01-01', '2026-10-19', pick=None, verbose=False)
g = px[px['stock_code'] == '000400'].sort_values('trade_date').reset_index(drop=True)
o = g['open_price'].to_numpy('float64')
c = g['close_price'].to_numpy('float64')
ts = g['trade_date'].to_numpy()

rows = []
for _, r in sig.iterrows():
    i = int(np.flatnonzero(ts == np.datetime64(r['信号日']))[0])
    e = o[i + 1]
    row = {'信号日': r['信号日'].date(), '收盘': round(c[i], 2),
           '现价': r['现价'], '涨幅%': r['涨幅%'],
           '金叉后': int(i - np.flatnonzero(ts == np.datetime64(r['信号日']))[0])
           if False else None,
           '入场(次日开)': round(e, 2)}
    for k in (5, 10, 20, 40, 60):
        row[f'{k}日%'] = round((o[i + 1 + k] / e - 1) * 100, 1) \
            if i + 1 + k < len(c) else np.nan
    row['60日内最大%'] = round((g['high_price'].to_numpy()[i + 1:i + 61].max()
                             / e - 1) * 100, 1) if i + 61 <= len(c) else np.nan
    rows.append(row)
tab = pd.DataFrame(rows).drop(columns=['金叉后'])

print(tab.to_string(index=False))
print('\n=== 汇总（T+1 开盘买入）===')
for k in (5, 10, 20, 40, 60):
    x = tab[f'{k}日%'].dropna().to_numpy()
    if len(x):
        print(f'{k:>2}日: n={len(x):2d} 均值={x.mean():+6.2f}% '
              f'中位={np.median(x):+6.2f}% 胜率={(x > 0).mean() * 100:5.1f}%')
x = tab['60日内最大%'].dropna().to_numpy()
print(f'60日内最大涨幅: 均值={x.mean():+.1f}% 中位={np.median(x):+.1f}%')

out = 'output/launch/backtest_000400.xlsx'
with pd.ExcelWriter(out, engine='openpyxl') as xw:
    tab.to_excel(xw, sheet_name='逐笔', index=False)
print('[输出]', out)
