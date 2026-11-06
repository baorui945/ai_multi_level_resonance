# -*- coding: utf-8 -*-
"""对比新口径（排除周线高位死叉）留下的 42 笔与被剔除的 126 笔。"""
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
            rows.append({'代码': code, '名称': r['名称'], '金叉日': r['金叉日'],
                         'ret': r[C], 'ok': False, 'why': '周线数据不足'})
            continue
        bad = {}
        for s in ('cur', 'prev'):
            if wv[f'hist_{s}'] < 0 and (wv[f'dif_{s}'] > 0 or wv[f'dea_{s}'] > 0):
                bad[s] = wv
        rows.append({'代码': code, '名称': r['名称'], '金叉日': r['金叉日'],
                     'ret': r[C], 'ok': not bad,
                     'why': '高位死叉(当前周)' if 'cur' in bad else
                            ('高位死叉(前一周)' if 'prev' in bad else ''),
                     'wdf': wv['dif_cur'], 'wdc': wv['dea_cur'], 'wh': wv['hist_cur']})
w = pd.DataFrame(rows)
f = lambda s: (len(s), s.ret.median(), s.ret.mean(), (s.ret > 0).mean() * 100,
               s.ret.min(), s.ret.max(), (s.ret < -10).mean() * 100)
print('%-10s %4s %8s %8s %7s %8s %8s %7s' %
      ('组', 'n', '中位%', '均值%', '胜率%', '最小%', '最大%', '跌>10%'))
for lab, s in [('保留', w[w.ok]), ('剔除', w[~w.ok]), ('全部', w)]:
    n, md, mn, wr, lo, hi, tail = f(s)
    print('%-10s %4d %8.2f %8.2f %7.1f %8.2f %8.2f %7.1f' %
          (lab, n, md, mn, wr, lo, hi, tail))
print('\n剔除原因分布：')
print(w[~w.ok].why.value_counts().to_string())
print('\n被剔除的收益前 8：')
print(w[~w.ok].nlargest(8, 'ret')[['代码', '名称', '金叉日', 'ret', 'why', 'wdf', 'wdc', 'wh']]
      .round(2).to_string(index=False))
print('\n按月份（保留 / 剔除 中位）：')
w['month'] = w['金叉日'].dt.month
print(w.pivot_table(index='month', columns='ok', values='ret',
                    aggfunc=['count', 'median']).round(2).to_string())
