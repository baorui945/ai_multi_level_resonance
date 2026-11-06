# -*- coding: utf-8 -*-
"""2026 年：收盘 > MA7 时，连续阳线根数的分布（全市场 + 000400）。"""
import numpy as np
import pandas as pd

px = pd.read_parquet('data/_cache/daily_px.parquet')
px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
px['trade_date'] = pd.to_datetime(px['trade_date'])
px = px[px['trade_date'] >= '2026-01-01'].sort_values(['stock_code', 'trade_date'])


def run_len(flag):
    """连续 True 的长度（当天为 0/1 起点）。"""
    out = np.zeros(len(flag), dtype='float64')
    k = 0
    for i, v in enumerate(flag):
        k = k + 1 if v else 0
        out[i] = k
    return out


all_runs, per_stock = [], {}
closed_runs = []
for code, g in px.groupby('stock_code', sort=False):
    if len(g) < 30:
        continue
    c = g['close_price'].to_numpy('float64')
    o = g['open_price'].to_numpy('float64')
    ma7 = pd.Series(c).rolling(7).mean().to_numpy()
    yang = c > o
    rl = run_len(yang)
    above = np.isfinite(ma7) & (c > ma7)
    sel = above & yang & np.isfinite(ma7)
    if sel.sum():
        all_runs.append(rl[sel])
        per_stock[code] = float(np.median(rl[sel]))
    # 完整的连阳段（段结束在 2026 内，且段内收盘都在 MA7 上）
    i = 0
    n = len(yang)
    while i < n:
        if yang[i]:
            j = i
            while j + 1 < n and yang[j + 1]:
                j += 1
            seg_ok = np.all(above[i:j + 1]) and np.isfinite(ma7[i:j + 1]).all()
            if seg_ok:
                closed_runs.append(j - i + 1)
            i = j + 1
        else:
            i += 1

runs = np.concatenate(all_runs)
print(f'2026 全市场：收盘>MA7 且为阳线的“日样本” {runs.size:,} 个')
print(f'  连续阳线根数：均值 {runs.mean():.2f} 中位 {np.median(runs):.0f} '
      f'P75 {np.percentile(runs, 75):.0f} P90 {np.percentile(runs, 90):.0f} '
      f'最大 {runs.max():.0f}')
print('  分布：')
s = pd.Series(runs).value_counts().sort_index()
for k, v in s.items():
    if k <= 12:
        print(f'   {int(k):2d} 连阳: {v:>8,} ({v / runs.size * 100:5.1f}%)')
print(f'   >12 连阳: {int((runs > 12).sum()):,}')

cr = np.array(closed_runs)
print(f'\n完整连阳段（段内收盘均在 MA7 上）{cr.size:,} 段：'
      f'均值 {cr.mean():.2f} 中位 {np.median(cr):.0f} P75 {np.percentile(cr, 75):.0f} '
      f'P90 {np.percentile(cr, 90):.0f}')

ps = pd.Series(per_stock)
print(f'\n个股中位数（{ps.size} 只）：'
      f'中位 {ps.median():.0f} 均值 {ps.mean():.2f} P25 {ps.quantile(.25):.0f} '
      f'P75 {ps.quantile(.75):.0f}')
print(f'000400 的中位数：{per_stock.get("000400")}')
