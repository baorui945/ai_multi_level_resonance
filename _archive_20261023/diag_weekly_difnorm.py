# -*- coding: utf-8 -*-
"""诊断 2：周线 DIF 绝对水平 vs 归一化水平 vs 变化方向。"""
import numpy as np
import pandas as pd

from mlr import data as D
from export_shape_list import _week_index, _week_last_close, _wk_vals_at

C = '10日涨跌幅%'
ev = pd.read_excel(r'output\shape_gc_099_fwd10_2026.xlsx',
                   sheet_name=0, dtype={'代码': str})
ev['金叉日'] = pd.to_datetime(ev['金叉日(=窗口终点)'])

d = D.load_daily(verbose=False).sort_values(['stock_code', 'trade_date'])
want = set(ev['代码'])
rows = []
for code, g in d.groupby('stock_code', sort=False):
    if code not in want:
        continue
    dt = g.trade_date.values.astype('datetime64[D]')
    px = g.close_price.values.astype(float)
    codes = _week_index(dt)
    wl = _week_last_close(px, codes)
    pos = {t: i for i, t in enumerate(dt)}
    for _, r in ev[ev['代码'] == code].iterrows():
        i = pos.get(np.datetime64(r['金叉日'].date()))
        if i is None:
            continue
        wv = _wk_vals_at(px, codes, wl, i, (30, 90, 9))
        if wv is None:
            wv = {k: np.nan for k in ('dif_cur', 'dif_prev', 'dea_cur',
                                      'dea_prev', 'hist_cur', 'hist_prev')}
        rows.append({'代码': code, '名称': r['名称'], '金叉日': r['金叉日'],
                     'ret': r[C], 'close': r['收盘价'],
                     'wdf': wv['dif_cur'], 'wdp': wv['dif_prev'],
                     'wh': wv['hist_cur'], 'whp': wv['hist_prev']})
w = pd.DataFrame(rows).dropna(subset=['wdf'])
w['ok'] = w.wdf >= 0
w['month'] = w['金叉日'].dt.month
w['dfn'] = w.wdf / w.close * 100          # 周线 DIF 占股价 %
w['dd'] = w.wdf - w.wdp                   # 周线 DIF 变化


def tab(s, col, bins, labels, title):
    print(f'\n=== {title} ===')
    t = s.assign(b=pd.cut(s[col], bins=bins, labels=labels))
    g = t.groupby('b', observed=True).ret.agg(
        ['count', 'mean', 'median', lambda x: (x > 0).mean() * 100])
    g.columns = ['count', '均值%', '中位%', '胜率%']
    print(g.round(2).to_string())


tab(w, 'dfn', [-1e9, -1, 0, 1, 3, 10, 1e9],
    ['<-1', '-1~0', '0~1', '1~3', '3~10', '>10'],
    '周线 DIF / 收盘价（%），全部 168 笔')
tab(w, 'dd', [-1e9, -1, -0.2, 0.2, 1, 1e9],
    ['<-1', '-1~-0.2', '-0.2~0.2', '0.2~1', '>1'],
    '周线 DIF 环比变化（当前周-前一周），全部 168 笔')

print('\n=== 通过组（130）按周线 DIF 档位 ===')
p = w[w.ok].copy()
p['b'] = pd.cut(p.wdf, [-1e9, 0.5, 1, 3, 10, 1e9],
                labels=['0~0.5', '0.5~1', '1~3', '3~10', '>10'])
g = p.groupby('b', observed=True).ret.agg(['count', 'median'])
g['9月占比%'] = p.groupby('b', observed=True).apply(
    lambda s: (s.month == 9).mean() * 100, include_groups=False)
g['7月占比%'] = p.groupby('b', observed=True).apply(
    lambda s: (s.month == 7).mean() * 100, include_groups=False)
print(g.round(2).to_string())

print('\n=== 9 月 27 笔：周线 DIF 分布 ===')
s9 = w[w.month == 9]
print(s9.wdf.describe(percentiles=[.25, .5, .75]).round(2).to_string())
print(f'9 月全部周线 DIF>0：{(s9.wdf > 0).sum()} / {len(s9)}')

print('\n=== 各月：周线 DIF 中位 / 归一化中位 ===')
print(w.groupby('month')[['wdf', 'dfn', 'ret']].median().round(2).to_string())

print('\n=== 归一化 dfn 与收益 Spearman（分月） ===')
for m in sorted(w.month.unique()):
    s = w[w.month == m]
    if len(s) >= 8:
        print(f'{m:>2}月 n={len(s):>3}  rho(wdf)={s.wdf.corr(s.ret, method="spearman"):+.3f}  '
              f'rho(dfn)={s.dfn.corr(s.ret, method="spearman"):+.3f}')
