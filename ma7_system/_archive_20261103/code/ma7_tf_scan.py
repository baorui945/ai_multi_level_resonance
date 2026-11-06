# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_tf_scan.py —— 周线 / 月线 MA7 上抛信号扫描（沪深300 等股票池）
==============================================================================
与日线 ma7_scan.py 同一套逻辑，只是把日线重采样为周线/月线：

    python ma7_tf_scan.py --tf W --recent 3
    python ma7_tf_scan.py --tf M --recent 3
    python ma7_tf_scan.py --tf W --universe coal --recent 3

输出：
    output/ma7_W_scan_<最新日期>.csv
    output/ma7_M_scan_<最新日期>.csv

状态说明：
    confirmed = 已被下一周期确认
    pending   = 最新周期尚未收盘，仅初步确认（如周五未收盘）
    forming   = 最新周期刚出现信号，还没有下一周期可确认
"""
import argparse
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from ma7daily import settings as S, signal as SG
from ma7_tf import resample_daily, is_complete_last_bar
from mlr import data as D, universe as U


def scan_one(g, tf, cfg, recent_n):
    code = str(g['stock_code'].iloc[0]).zfill(6)
    name = g['stock_name'].iloc[-1]
    bars = resample_daily(g, tf)
    if len(bars) < 8:
        return []
    complete = is_complete_last_bar(pd.Timestamp(bars['trade_date'].iloc[-1]), tf)
    done = bars if complete else bars.iloc[:-1]
    if len(done) == 0:
        return []
    last_done = pd.Timestamp(done['trade_date'].iloc[-1])
    last_bar = pd.Timestamp(bars['trade_date'].iloc[-1])
    cutoff = pd.Timestamp(done['trade_date'].iloc[-min(recent_n, len(done))])

    f = SG.detect_frame(bars, cfg)
    e = f[(f['entry_flag']) & (f['trade_date'] >= cutoff)].copy()
    out = []
    for _, r in e.iterrows():
        conf = pd.Timestamp(r['confirm_date']) if pd.notna(r['confirm_date']) else pd.NaT
        if pd.notna(conf) and conf <= last_done:
            status = 'confirmed'
        elif (not complete) and pd.notna(conf) and conf == last_bar:
            status = 'pending'
        else:
            status = 'forming'
        out.append({
            'tf': tf,
            'code': code,
            'name': name,
            'signal_date': pd.Timestamp(r['trade_date']),
            'confirm_date': conf,
            'close': round(float(r['close_price']), 2),
            'ma7': round(float(r['ma7']), 2),
            'slope_pct': round(float(r['slope_pct_day']), 3),
            'dev_pct': round(float(r['dev_pct']), 2),
            'seg_len': int(r['seg_len']),
            'status': status,
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_tf_scan',
                                 description='周线/月线 MA7 上抛信号扫描')
    ap.add_argument('--tf', default='W', choices=['W', 'M', 'w', 'm'],
                    help='W=周线，M=月线')
    ap.add_argument('--recent', type=int, default=3,
                    help='最近 N 个周期（默认 3）')
    ap.add_argument('--universe', default='hs300',
                    help='hs300 / coal / all（默认 hs300）')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线缓存')
    ap.add_argument('--preset', default=None,
                    help='叠加 config 中的预设口径（如 strict=降噪）')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    args = ap.parse_args(argv)

    tf = args.tf.upper()
    tf_name = '周线' if tf == 'W' else '月线'
    try:
        cfg = S.load_config(preset=args.preset)
    except ValueError as e:
        raise SystemExit(str(e))

    if args.universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        pool = f'沪深300成分股 {len(codes)} 只'
    elif args.universe == 'coal':
        codes, upd = U.get_coal_codes()
        pool = f'动力煤板块 {len(codes)} 只'
    elif args.universe == 'all':
        codes, upd = None, None
        pool = '全市场'
    else:
        raise SystemExit(f'未知 --universe {args.universe!r}（hs300 / coal / all）')

    if not args.refresh:
        D.sync_latest_daily()
    daily = D.load_daily(codes=codes, refresh=args.refresh, verbose=False)
    last_daily = pd.Timestamp(daily['trade_date'].max())
    print('=' * 78)
    print(f'  MA7 上抛 · {tf_name}扫描 · {pool}')
    print(f'  日线数据: {daily.trade_date.min().date()} ~ {last_daily.date()}'
          f'  ({daily.stock_code.nunique()} 只)')
    print(f'  窗口    : 最近 {args.recent} 个{tf_name}周期')
    print('=' * 78, flush=True)

    rows = []
    for code, g in daily.groupby('stock_code', sort=False):
        try:
            rows.extend(scan_one(g.sort_values('trade_date'), tf, cfg,
                                 args.recent))
        except Exception as ex:
            print(f'[跳过] {code}: {ex}')
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(['signal_date', 'code'], ascending=[False, True])
        out['signal_date'] = pd.to_datetime(out['signal_date'])
        out['confirm_date'] = pd.to_datetime(out['confirm_date'])
    else:
        out = pd.DataFrame(columns=['tf', 'code', 'name', 'signal_date',
                                    'confirm_date', 'close', 'ma7',
                                    'slope_pct', 'dev_pct', 'seg_len',
                                    'status'])

    print(f'  信号数: {len(out)}')
    if len(out):
        print('  状态  : ' + str(out['status'].value_counts().to_dict()))
        for d, g in out.groupby('signal_date', sort=False):
            print(f'\n  {pd.Timestamp(d).date()}  {len(g)} 只')
            print(g[['code', 'name', 'close', 'ma7', 'dev_pct',
                     'confirm_date', 'status']].to_string(index=False))
    else:
        print('  （窗口内无信号）')

    S.ensure_dirs()
    suffix = last_daily.strftime('%Y%m%d')
    path = args.out or os.path.join(S.OUTPUT_DIR, f'ma7_{tf}_scan_{suffix}.csv')
    out.to_csv(path, index=False, encoding='utf-8-sig')
    print(f'\n[OK] 已保存: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
