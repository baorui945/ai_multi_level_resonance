# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_tf.py —— 同一套 MA7 上抛逻辑的周线 / 月线回测
==============================================================================
逻辑与 ma7daily 完全一致，只把日线重采样成周线/月线：

  * 周线：每周最后一个交易日为 bar 日期，open=首日开盘，high/low=周内极值，
    close=周末收盘，volume=周内合计；
  * 月线：同理，按自然月聚合。

信号：MA7 上拐 + 收盘>MA7 + 更高低点 + K线质量，下一 bar MA7 继续上行确认。
回测：信号 bar 的下一 bar 确认，再下一 bar 开盘入场；状态结束后的下一 bar
开盘出场；双边成本 0.2%。周线/月线各自按 bar 计持有期。

用法::

    python ma7_tf.py --code 000400 --tf W
    python ma7_tf.py --code 000400 --tf M
    python ma7_tf.py --code 000400 --tf W --start 2020-01-01
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

from ma7daily import settings as S, signal as SG
from mlr import data as D


def resample_daily(daily, tf):
    """日线 -> 周线(W-FRI) / 月线(ME)，trade_date 取该周期最后一个交易日。"""
    tf = tf.upper()
    freq = 'W-FRI' if tf == 'W' else 'ME'
    d = daily.sort_values('trade_date').copy()
    d['trade_date'] = pd.to_datetime(d['trade_date'])
    g = d.set_index('trade_date')
    r = g.resample(freq)
    last_dates = g.index.to_series().resample(freq).last()
    out = pd.DataFrame({
        'trade_date': last_dates.to_numpy(),
        'open_price': r['open_price'].first().to_numpy(),
        'high_price': r['high_price'].max().to_numpy(),
        'low_price': r['low_price'].min().to_numpy(),
        'close_price': r['close_price'].last().to_numpy(),
        'volume': r['volume'].sum().to_numpy(),
    }).dropna(subset=['close_price'])
    out['stock_code'] = d['stock_code'].iloc[-1]
    out['stock_name'] = d['stock_name'].iloc[-1]
    return out.reset_index(drop=True)


def is_complete_last_bar(last_date, tf):
    """判断最后一个 bar 是否是完整周期（用下一个工作日是否跨周期判断）。"""
    nxt = last_date + pd.offsets.BDay(1)
    if tf.upper() == 'W':
        return nxt.to_period('W-FRI') != last_date.to_period('W-FRI')
    return nxt.to_period('M') != last_date.to_period('M')


def backtest_bars(frame, cost=0.002, start=None):
    """按 bar 级别的状态退出回测；返回逐笔 DataFrame。"""
    f = frame.reset_index(drop=True)
    rows = []
    last_exit_i = -1
    for i, r in f[f['entry_flag']].iterrows():
        if start is not None and pd.Timestamp(r['trade_date']) < pd.Timestamp(start):
            continue
        if i + 2 >= len(f):
            continue
        entry_i = i + 2          # 信号 bar -> 确认 bar -> 下一 bar 开盘
        if entry_i <= last_exit_i:
            continue
        later = f[(f.index > entry_i) & f['exit_flag']]
        if len(later):
            exit_flag_i = int(later.index[0])
            exit_i = min(exit_flag_i + 1, len(f) - 1)
            status = 'closed' if exit_i > exit_flag_i else 'open'
        else:
            exit_i = len(f) - 1
            status = 'open'
        entry_px = float(f.loc[entry_i, 'open_price'])
        exit_px = (float(f.loc[exit_i, 'open_price'])
                   if status == 'closed' else float(f.loc[exit_i, 'close_price']))
        ret = (exit_px / entry_px) * (1.0 - cost) - 1.0
        rows.append({
            'signal_date': r['trade_date'],
            'confirm_date': f.loc[i + 1, 'trade_date'],
            'entry_date': f.loc[entry_i, 'trade_date'],
            'entry_px': round(entry_px, 3),
            'exit_date': f.loc[exit_i, 'trade_date'],
            'exit_px': round(exit_px, 3),
            'bars': int(exit_i - entry_i + 1),
            'ret_pct': round(ret * 100, 2),
            'status': status,
        })
        last_exit_i = exit_i
    return pd.DataFrame(rows)


def perf(trades):
    if trades is None or len(trades) == 0:
        return {'n': 0}
    r = trades['ret_pct'].to_numpy() / 100.0
    total = float(np.prod(1.0 + r) - 1.0)
    years = max((pd.Timestamp(trades['exit_date'].iloc[-1])
                 - pd.Timestamp(trades['entry_date'].iloc[0])).days / 365.25, 1e-9)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    eq = np.cumprod(1.0 + r)
    mdd = float(np.min(eq / np.maximum.accumulate(eq) - 1.0))
    return {
        'n': len(trades),
        'total_pct': round(total * 100, 2),
        'cagr_pct': round(cagr * 100, 2),
        'mdd_pct': round(mdd * 100, 2),
        'win_pct': round(float((r > 0).mean() * 100), 1),
        'avg_pct': round(float(r.mean() * 100), 2),
    }


def signal_table(frame, start=None):
    rows = []
    f = frame.reset_index(drop=True)
    for i, r in f[f['entry_flag']].iterrows():
        if start is not None and r['trade_date'] < pd.Timestamp(start):
            continue
        seg = f[(f['trade_date'] >= r['trade_date']) & (f['seg_no'] == r['seg_no'])]
        ex = seg[seg['exit_flag']]
        e = ex.iloc[0] if len(ex) else None
        rows.append({
            'signal_date': r['trade_date'],
            'confirm_date': (pd.Timestamp(r['confirm_date'])
                             if pd.notna(r['confirm_date']) else pd.NaT),
            'close': round(float(r['close_price']), 2),
            'ma7': round(float(r['ma7']), 2),
            'slope_pct': round(float(r['slope_pct_day']), 3),
            'dev_pct': round(float(r['dev_pct']), 2),
            'seg_end': (pd.Timestamp(e['trade_date']) if e is not None else pd.NaT),
            'seg_bars': int(len(seg)),
            'seg_status': ('closed' if e is not None else 'open'),
        })
    t = pd.DataFrame(rows)
    if len(t):
        t['signal_date'] = pd.to_datetime(t['signal_date'])
        t['confirm_date'] = pd.to_datetime(t['confirm_date'])
        t['seg_end'] = pd.to_datetime(t['seg_end'])
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_tf', description='MA7 上抛 周线/月线回测')
    ap.add_argument('--code', default='000400', help='股票代码，如 000400')
    ap.add_argument('--tf', default='W', choices=['W', 'M', 'w', 'm'],
                    help='W=周线，M=月线')
    ap.add_argument('--start', default='2020-01-01', help='信号起点')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线缓存')
    ap.add_argument('--preset', default=None,
                    help='叠加 config 中的预设口径（如 strict=降噪）')
    ap.add_argument('--out', default=None, help='Excel 输出路径')
    args = ap.parse_args(argv)

    tf = args.tf.upper()
    tf_name = '周线' if tf == 'W' else '月线'
    try:
        cfg = S.load_config(preset=args.preset)
    except ValueError as e:
        raise SystemExit(str(e))

    if not args.refresh:
        D.sync_latest_daily()
    daily = D.load_daily(codes=[args.code], refresh=args.refresh)
    if len(daily) == 0:
        raise SystemExit(f'{args.code} 无日线数据')
    bars = resample_daily(daily, tf)
    last_dt = pd.Timestamp(bars['trade_date'].iloc[-1])
    complete = is_complete_last_bar(last_dt, tf)
    bars_done = bars if complete else bars.iloc[:-1].reset_index(drop=True)

    print('=' * 78)
    print(f'  MA7 上抛 · {tf_name}回测 · {args.code} {daily.stock_name.iloc[-1]}')
    print('=' * 78)
    print(f'  日线数据 : {daily.trade_date.min().date()} ~ {daily.trade_date.max().date()}'
          f'  ({len(daily):,} 行)')
    print(f'  {tf_name}bar : {bars.trade_date.iloc[0].date()} ~ {last_dt.date()}'
          f'  ({len(bars)} 根；最后一根{"完整" if complete else "未完成，仅用于观察"}）')
    print(f'  规则     : MA7 上拐 + 收盘>MA7 + 更高低点 + K线质量；'
          f'下一{tf_name} MA7 继续上行确认')
    print(f'  回测     : 确认后下一{tf_name}开盘入场；状态结束后的下一{tf_name}开盘出场；'
          f'双边成本 0.2%')

    frame_full = SG.detect_frame(bars, cfg)
    frame_done = SG.detect_frame(bars_done, cfg) if len(bars_done) else frame_full
    sig_done = signal_table(frame_done, start=args.start)
    sig_full = signal_table(frame_full, start=args.start)

    # 2026 已完成信号
    y26 = sig_done[sig_done['signal_date'] >= '2026-01-01'] if len(sig_done) else sig_done
    print(f'\n  2026 已完成的{tf_name}信号: {len(y26)} 个')
    if len(y26):
        print(y26[['signal_date', 'confirm_date', 'close', 'ma7',
                   'slope_pct', 'dev_pct', 'seg_end', 'seg_bars']]
              .to_string(index=False))

    # 当前未完成周期候选（仅最后一根 bar 确认的）
    if not complete and len(sig_full):
        pending = sig_full[sig_full['confirm_date'] == last_dt]
        # 排除已在完整 bar 中出现的
        if len(pending):
            done_keys = set(zip(sig_done['signal_date'], sig_done['confirm_date'])) \
                if len(sig_done) else set()
            pending = pending[[(a, b) not in done_keys
                               for a, b in zip(pending['signal_date'],
                                               pending['confirm_date'])]]
        print(f'\n  当前未完成{tf_name}（{last_dt.date()}）待确认/观察: {len(pending)} 个')
        if len(pending):
            print(pending[['signal_date', 'confirm_date', 'close', 'ma7',
                           'slope_pct', 'dev_pct']].to_string(index=False))

    trades = backtest_bars(frame_done, cost=cfg['backtest']['cost'],
                           start=args.start)
    m = perf(trades)
    print(f'\n  回测统计（{tf_name}信号，{args.start} 起）:')
    if m['n']:
        print(f'    交易 {m["n"]} 笔 | 总收益 {m["total_pct"]:.2f}% | '
              f'年化 {m["cagr_pct"]:.2f}% | 最大回撤 {m["mdd_pct"]:.2f}% | '
              f'胜率 {m["win_pct"]:.1f}% | 逐笔均值 {m["avg_pct"]:.2f}%')
    else:
        print('    无交易')

    if len(trades):
        print('\n  最近 8 笔:')
        print(trades.tail(8).to_string(index=False))

    # 输出
    S.ensure_dirs()
    stamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    xlsx = args.out or os.path.join(
        S.OUTPUT_DIR, f'ma7_{tf}_{args.code}_{stamp}.xlsx')
    with pd.ExcelWriter(xlsx, engine='openpyxl') as w:
        pd.DataFrame([{'tf': tf_name, 'code': args.code,
                       'name': daily.stock_name.iloc[-1],
                       'start': args.start, **m}]).to_excel(
            w, sheet_name='汇总', index=False)
        (sig_full if len(sig_full) else pd.DataFrame()).to_excel(
            w, sheet_name=f'{tf_name}信号', index=False)
        (trades if len(trades) else pd.DataFrame()).to_excel(
            w, sheet_name='逐笔', index=False)
    csv = os.path.join(S.OUTPUT_DIR, f'ma7_{tf}_{args.code}_signals.csv')
    (sig_full if len(sig_full) else pd.DataFrame()).to_csv(
        csv, index=False, encoding='utf-8-sig')
    print(f'\n[OK] Excel : {xlsx}')
    print(f'[OK] 信号CSV: {csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
