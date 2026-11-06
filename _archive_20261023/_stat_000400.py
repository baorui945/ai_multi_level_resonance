# -*- coding: utf-8 -*-
"""000400，2026 年：收盘>MA7 时的连续阳线根数（中位数 + 逐段明细）。"""
import numpy as np
import pandas as pd

px = pd.read_parquet('data/_cache/daily_px.parquet')
px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
g = px[px['stock_code'] == '000400'].sort_values('trade_date').reset_index(drop=True)
g = g[g['trade_date'] >= '2026-01-01'].reset_index(drop=True)
c = g['close_price'].to_numpy('float64')
o = g['open_price'].to_numpy('float64')
ma7 = pd.Series(c).rolling(7).mean().to_numpy()
ts = g['trade_date'].to_numpy()
yang = c > o

# 连续阳线根数（含当天）
rl = np.zeros(len(yang))
k = 0
for i, v in enumerate(yang):
    k = k + 1 if v else 0
    rl[i] = k

above = c > ma7
sel = above & yang
vals = rl[sel]
print(f'2026 年 000400：收盘>MA7 且阳线的交易日 {int(sel.sum())} 天')
print(f'连续阳线根数：中位 {np.median(vals):.0f}  均值 {vals.mean():.2f}  '
      f'P75 {np.percentile(vals, 75):.0f}  P90 {np.percentile(vals, 90):.0f}  '
      f'最大 {vals.max():.0f}')
s = pd.Series(vals).value_counts().sort_index()
print('分布：' + '  '.join(f'{int(k)}根:{v}' for k, v in s.items()))

# 逐段明细
print('\n=== 2026 年所有连阳段 ===')
i, n = 0, len(yang)
rows = []
while i < n:
    if yang[i]:
        j = i
        while j + 1 < n and yang[j + 1]:
            j += 1
        ok = bool(np.all(above[i:j + 1]))
        rows.append({'起': pd.Timestamp(ts[i]).date(), '止': pd.Timestamp(ts[j]).date(),
                     '根数': j - i + 1, '段内收盘全在MA7上': 'Y' if ok else ''})
        i = j + 1
    else:
        i += 1
df = pd.DataFrame(rows)
print(df.to_string(index=False))
print('\n段数', len(df), '中位', df['根数'].median(), '均值', round(df['根数'].mean(), 2),
      '| 全在MA7上的段数', int((df['段内收盘全在MA7上'] == 'Y').sum()))
