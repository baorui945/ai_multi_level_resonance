# -*- coding: utf-8 -*-
"""临时探索：模板 601898 在 2025-11-11 ~ 2026-03-16 的 DIF/DEA/柱 形态。"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r'\..')

from mlr import data as D, settings as S
from mlr.macd import macd as calc_macd

cfg = S.load_config()
mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
daily = D.load_daily(codes=['601898'])
g = daily[daily['stock_code'] == '601898'].sort_values('trade_date')
close = g['close_price'].to_numpy('float64')
dates = pd.to_datetime(g['trade_date']).to_numpy()
dif, dea, hist = calc_macd(close, *mp)

m = (dates >= np.datetime64('2025-11-11')) & (dates <= np.datetime64('2026-03-16'))
idx = np.where(m)[0]
print(f'MACD params = {mp}')
print(f'模板窗口 L = {idx.size}   全局索引 {idx[0]} ~ {idx[-1]}')
print(f'close: {close[idx[0]]:.2f} -> {close[idx[-1]]:.2f}  '
      f'({(close[idx[-1]] / close[idx[0]] - 1) * 100:+.2f}%)')
print()
print(f'{"i":>4s} {"date":>11s} {"close":>8s} {"dif":>9s} {"dea":>9s} '
      f'{"hist":>9s} {"sign":>4s}')
for j, i in enumerate(idx):
    sign = '绿' if hist[i] < 0 else '红'
    print(f'{j:>4d} {np.datetime64(dates[i], "D")!s:>11s} {close[i]:8.2f} '
          f'{dif[i]:9.4f} {dea[i]:9.4f} {hist[i]:9.4f} {sign:>4s}')

# 柱的符号变化点（绿->红）
s = np.sign(hist[idx])
cross = [(j, idx[j]) for j in range(1, len(s)) if s[j] > 0 >= s[j - 1]]
print()
print('柱由绿转红的索引（模板内相对 j / 全局 i）：', cross)
print('柱<0 的天数 =', int((hist[idx] < 0).sum()),
      ' 柱>0 的天数 =', int((hist[idx] > 0).sum()))
print('DIF 最小值在相对 j =', int(np.argmin(dif[idx])),
      f' (dif={dif[idx].min():.4f})')
