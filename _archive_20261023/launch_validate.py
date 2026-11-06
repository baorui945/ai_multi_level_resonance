# -*- coding: utf-8 -*-
"""
==============================================================================
launch_validate.py —— 「平台 → 上抛」的统计验证
==============================================================================

与 squeeze_validate 同一套事件研究口径（coil.evaluate）：T+1 开盘买入、
h 日后开盘卖出，扣双边 0.2%，减沪深300 同期收益；一字板剔除；
t 值用按日聚合口径。

用法::

    python launch_validate.py
    python launch_validate.py --start 2017-01-01 --split 2023-01-01
"""
import argparse
import os

import numpy as np
import pandas as pd

import launch_scan as LS
from coil import evaluate as EV

HORIZONS = [5, 10, 20, 40]


def _cluster_t(det, h):
    d = det[~det['untradable'].to_numpy(bool)]
    g = d.groupby(pd.to_datetime(d['date']))[f'a{h}_oo'].mean()
    g = g[np.isfinite(g)]
    if len(g) < 3:
        return np.nan
    x = g.to_numpy()
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(x.size))) if sd > 0 else np.nan


def main():
    ap = argparse.ArgumentParser(description='LAUNCH 统计验证')
    ap.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    ap.add_argument('--start', default='2017-01-01')
    ap.add_argument('--end', default='')
    ap.add_argument('--split', default='2023-01-01')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    cfg = LS.load_config()
    px = LS.load_px(a.universe)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp(px['trade_date'].max())
    start = pd.Timestamp(a.start)
    split = pd.Timestamp(a.split)
    main_h = int(cfg['exit']['main_horizon'])
    cost = float(cfg['exit']['cost'])

    print(f'[验证] {start.date()} ~ {end.date()}')
    df = LS.scan(px, cfg, start, end, pick=None, verbose=False)
    print(f'[信号] {len(df)} 条')
    if not len(df):
        raise SystemExit('无信号')
    mat = EV.build_matrices(px)
    index = EV.load_index()
    det = EV.event_study(mat, index,
                         df.rename(columns={'代码': 'code', '信号日': 'date',
                                            '收盘': 'close'}),
                         HORIZONS, cost)
    s = EV.summarize(det, HORIZONS, label='launch')
    s['按日聚合t'] = [_cluster_t(det, h) for h in HORIZONS]
    print(s.to_string(index=False))

    d = det[~det['untradable'].to_numpy(bool)].copy()
    d['年'] = pd.to_datetime(d['date']).dt.year
    yr = d.groupby('年').agg(n=('a20_oo', 'size'),
                             a20=('a20_oo', lambda x: round(x.mean() * 100, 2)),
                             win=('a20_oo', lambda x: round((x > 0).mean() * 100, 1)))
    print('\n=== 逐年（20 日）===')
    print(yr.to_string())

    rows = []
    dt = pd.to_datetime(d['date'])
    for name, m in (('样本内', dt < split), ('样本外', dt >= split)):
        x = d.loc[m.to_numpy(), 'a20_oo'].to_numpy('float64')
        x = x[np.isfinite(x)]
        if len(x):
            rows.append({'区间': name, 'n': len(x),
                         '均值alpha': round(x.mean() * 100, 2),
                         '中位alpha': round(np.median(x) * 100, 2),
                         '胜率%': round((x > 0).mean() * 100, 1)})
    io = pd.DataFrame(rows)
    print('\n=== 样本内外（20 日）===')
    print(io.to_string(index=False))

    out = a.out or os.path.join(LS.OUTPUT_DIR, f'launch_validate_{end:%Y%m%d}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        s.to_excel(xw, sheet_name='持有期', index=False)
        io.to_excel(xw, sheet_name='样本内外', index=False)
        yr.to_excel(xw, sheet_name='逐年', index=False)
        pd.DataFrame({
            '项目': ['alpha 口径', '成本', '入场价', '一字板', 't 值口径', '注意'],
            '说明': ['个股区间收益 − 沪深300 同期收益 − 成本',
                     f'{cost * 100:.1f}%（双边合计）',
                     'T 收盘确认，T+1 开盘可执行',
                     'T+1 开盘高开 ≥9.5%/19.5% 视为买不进并剔除',
                     '「按日聚合t」：同日信号相关，逐笔 t 会高估',
                     '持有期重叠使 t 仍偏乐观；结论看符号与量级'],
        }).to_excel(xw, sheet_name='口径说明', index=False)
        df.to_excel(xw, sheet_name='信号明细', index=False)
    print(f'\n[输出] {out}')


if __name__ == '__main__':
    main()
