# -*- coding: utf-8 -*-
"""临时诊断：为什么「周线 DIF 前一周/当前周 >=0」过滤后表现反而更差。"""
import numpy as np
import pandas as pd

from mlr import data as D
from export_shape_list import _week_index, _week_last_close, _wk_vals_at

C = '10日涨跌幅%'
ev = pd.read_excel(r'output\shape_gc_099_fwd10_2026.xlsx',
                   sheet_name=0, dtype={'代码': str})
ev['金叉日'] = pd.to_datetime(ev['金叉日(=窗口终点)'])

# ── 给 168 笔全部补上周线数值（不只通过的行）────────────────────────────
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
        t = np.datetime64(r['金叉日'].date())
        i = pos.get(t)
        if i is None:
            continue
        wv = _wk_vals_at(px, codes, wl, i, (30, 90, 9))
        if wv is None:
            wv = {k: np.nan for k in ('dif_cur', 'dif_prev', 'dea_cur',
                                      'dea_prev', 'hist_cur', 'hist_prev')}
        rows.append({'代码': code, '名称': r['名称'], '金叉日': r['金叉日'],
                     'score': r['窗口score'], 'ret': r[C],
                     'wdf': wv['dif_cur'], 'wdp': wv['dif_prev'],
                     'wdc': wv['dea_cur'], 'wh': wv['hist_cur'],
                     'whp': wv['hist_prev']})
w = pd.DataFrame(rows)
w['ok'] = (w.wdc >= 0) & (w.wdp >= 0) & (w.wh * 0 + w.wdc >= 0)
w['ok'] = (w.wdf >= 0) & (w.wdp >= 0)
w['month'] = w['金叉日'].dt.month
print(f'样本 {len(w)} 笔，通过 {int(w.ok.sum())}，剔除 {int((~w.ok).sum())}')
print('\n=== 1) 被剔除 38 笔明细（按收益降序）===')
print(w[~w.ok].sort_values('ret', ascending=False)
      [['代码', '名称', '金叉日', 'score', 'wdf', 'wdp', 'wdc', 'wh', 'ret']]
      .round(3).to_string(index=False))

print('\n=== 2) 月份 × 通过/剔除 交叉表（count | 中位收益%）===')
ct = w.pivot_table(index='month', columns='ok', values='ret',
                   aggfunc=['count', 'median', 'mean'])
print(ct.round(2).to_string())

print('\n=== 3) 同月内对比（pass 中位 / fail 中位）===')
for m in sorted(w.month.unique()):
    s = w[w.month == m]
    a, b = s[s.ok].ret, s[~s.ok].ret
    if len(a) and len(b):
        print(f'{m:>2}月  通过 n={len(a):>3} 中位 {a.median():+6.2f}%   '
              f'剔除 n={len(b):>2} 中位 {b.median():+6.2f}%   差 {a.median()-b.median():+6.2f}pp')
    else:
        print(f'{m:>2}月  通过 n={len(a):>3} 中位 {a.median():+6.2f}%   '
              f'剔除 n={len(b):>2} 中位 {"—" if b.empty else f"{b.median():+.2f}%"}')

print('\n=== 4) 按「周线 DIF 当前周」分档（全部 168 笔）===')
bins = [-1e9, -1, 0, 1, 3, 10, 1e9]
labels = ['<-1', '-1~0', '0~1', '1~3', '3~10', '>10']
w['wkbin'] = pd.cut(w.wdf, bins=bins, labels=labels)
print(w.groupby('wkbin', observed=True).ret
      .agg(['count', 'mean', 'median', lambda s: (s > 0).mean() * 100])
      .rename(columns={'<lambda_0>': 'win%'}).round(2).to_string())

print('\n=== 5) 相关性：周线 DIF 水平 vs 10 日收益（Spearman）===')
for name, s in [('全部', w), ('1月', w[w.month == 1]), ('5月', w[w.month == 5]),
                ('7月', w[w.month == 7]), ('9月', w[w.month == 9])]:
    if len(s) >= 8:
        r = s.wdf.corr(s.ret, method='spearman')
        print(f'{name:<4} n={len(s):>3}  rho={r:+.3f}')

print('\n=== 6) 剔除组内部：周线柱>0（底部刚翻红）vs 柱<=0 ===')
f = w[~w.ok]
for lab, s in [('柱>0且前周>0', f[(f.wh > 0) & (f.whp > 0)]),
               ('柱>0但前周<=0', f[(f.wh > 0) & (f.whp <= 0)]),
               ('柱<=0', f[f.wh <= 0])]:
    if len(s):
        print(f'{lab:<14} n={len(s):>2} 均值 {s.ret.mean():+6.2f}% 中位 {s.ret.median():+6.2f}% '
              f'胜率 {(s.ret > 0).mean()*100:5.1f}%')

print('\n=== 7) 通过组的周线 DIF 有多高 ===')
print(w.groupby('ok').wdf.describe(percentiles=[.25, .5, .75]).round(2).to_string())

