# -*- coding: utf-8 -*-
"""
==============================================================================
scan.py —— 多周期共振 · 最新交易日扫描（盘后选股）
==============================================================================
回答「今天有没有日/周/月三周期共振的股票」。
开始时自动调用 `sync_latest_daily` 把日线缓存增量同步到 DB 最新（命中且无新数据
则不动）。周期口径由 `data.period_mode` 决定：默认 `closed` 只用已收盘的周/月 K
（与回测逐日一致，输出会打印状态截至日）；`asof` 用进行中周期（provisional）。

用法::

    python scan.py                     # 扫描最新交易日（三周期买点）
    python scan.py --date 2026-10-16   # 指定交易日
    python scan.py --recent 10         # 附带展示最近 10 个交易日的信号（含当日，按日线交易历）
    python scan.py --all-state         # 列出当日「处于共振中」的全部（含非首日）
    python scan.py --mode wm_first     # 改入场口径（周月共振首日，不等日线）
    python scan.py --mode dw_first     # 日周共振首日（周线确认）
    python scan.py --universe coal     # 动力煤板块（hs300 / coal / all）
    python scan.py --layer daily       # 日线上抛
    python scan.py --layer weekly      # 周线上抛
    python scan.py --layer monthly     # 月线上抛
    python scan.py --layer dw          # 日线+周线共振
    python scan.py --layer wm          # 周线+月线共振（月线已确认）
    python scan.py --layer watch       # 候选池：周线上抛、月线尚未上抛
    python scan.py --layer all         # 三层统计并各存一份 CSV
    python scan.py --out my.csv
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, resonance as R, universe as U


def _warn_partial_period(last_day, daily, period_mode='closed'):
    """说明最新周期的收盘程度，以及当前 period_mode 下状态截至哪一根周期K。"""
    last_day = pd.Timestamp(last_day).normalize()
    if str(period_mode).lower() == 'closed':
        # 只使用已收盘周期：周线截至最近一个周五，月线截至最近一个自然月末
        w_end = last_day - pd.Timedelta(days=(last_day.weekday() - 4) % 7)
        m_end = last_day + pd.offsets.MonthEnd(0)
        if m_end > last_day:
            m_end = m_end - pd.offsets.MonthEnd(1)
        print(f'  [口径] closed：忽略进行中的周/月K，周线状态截至 '
              f'{w_end:%Y-%m-%d}、月线截至 {m_end:%Y-%m-%d}（与回测一致）')
        return
    notes = []
    wk_end = pd.Period(last_day, freq='W-FRI').end_time.normalize()
    mo_end = pd.Period(last_day, freq='M').end_time.normalize()
    if wk_end > last_day:
        notes.append(f'本周未收盘（名义周止 {wk_end:%m-%d}，周线状态基于进行中K线，'
                     '周五收盘可能翻转/新增信号）')
    if mo_end > last_day:
        notes.append(f'当月未收盘（名义月止 {mo_end:%m-%d}，月线状态同理，'
                     '月末收盘后再确认）')
    if notes:
        print('  [提示] ' + '；'.join(notes) + ' -> 当日结果为 provisional'
              '（以周期收盘后重扫为准）')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='scan', description='多周期共振扫描')
    ap.add_argument('--date', default=None, help='交易日（默认最新）')
    ap.add_argument('--recent', type=int, default=0,
                    help='附最近 N 个交易日（含当日，按日线交易历）的共振首日信号')
    ap.add_argument('--mode', default=None,
                    choices=list(R.ENTRY_COLS),
                    help='入场模式（默认取配置 signal.mode）：'
                         'any_entry=三周期共振首日 / fresh_daily=日线金叉后N根内 / '
                         'slowest_turn=月线刚转多 / wm_first=周月共振首日(不等日线) / '
                         'dw_first=日周共振首日(周线确认) / dw_watch=日线先动候选')
    ap.add_argument('--layer', default=None,
                    choices=['entry', 'wm', 'watch', 'all',
                             'daily', 'weekly', 'monthly', 'dw'],
                    help='输出层：entry=三周期买点(默认) / wm=周月共振 / '
                         'watch=候选池(周线上抛、月线未上抛) / '
                         'daily|weekly|monthly=单周期上抛 / dw=日周共振 / '
                         'all=三层统计并各存一份CSV')
    ap.add_argument('--all-state', action='store_true',
                    help='列出当日处于共振中的全部（含非首日）')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all（默认取配置 universe.default）')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)

    cfg = S.load_config()
    mode = args.mode or cfg.get('signal', {}).get('mode', 'any_entry')
    entry_col = R.ENTRY_COLS.get(mode, 'entry')
    print('=' * 78)
    print('  多周期（日/周/月）MACD 共振 —— 当日扫描')
    print(f'  入场模式: {mode}  ({entry_col})')
    print('=' * 78)
    for w in S.validate_config(cfg):
        print(f'  [口径] {w}')

    universe = args.universe or cfg.get('universe', {}).get('default', 'hs300')
    codes = None
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'  股票池: 沪深300成分股 {len(codes)} 只 (update_date={upd})')
    elif universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'  股票池: 动力煤板块 {len(codes)} 只 (update_date={upd})')
    elif universe != 'all':
        raise SystemExit(f'未知 --universe {universe!r}（可选 hs300 / coal / all）')

    # 先把日线缓存增量同步到 DB 最新（否则缓存停在旧日期，扫的是过期状态）
    if not args.refresh:
        D.sync_latest_daily()
    daily = D.load_daily(codes=codes, refresh=args.refresh)
    tfs = D.build_timeframes(daily, cfg)
    res = R.build_resonance(tfs, cfg)

    last_day = pd.Timestamp(args.date) if args.date else res['trade_date'].max()
    # 周期口径：closed=只认已收盘周/月K（与回测一致）；asof=用进行中K（provisional）
    _warn_partial_period(last_day, daily,
                         cfg['data'].get('period_mode', 'closed'))
    day = res[res['trade_date'] == last_day].copy()
    print(f'  交易日: {last_day.date()}   全市场: {len(day)} 只')
    if 'monthly_ready' in day.columns:
        n_ready = int(day['monthly_ready'].sum())
        print(f'  月线暖机: {n_ready}/{len(day)} 只已可用'
              f'（phase 模式在可用前只做日+周，之后做周+月）')
    n_watch = int(day['watch'].sum())
    n_wm = int(day['wm_resonance'].sum())
    n_entry = int(day['entry'].sum())
    n_daily = int(day['up_daily'].sum())
    n_weekly = int(day['up_weekly'].sum())
    n_monthly = int(day['up_monthly'].sum())
    n_dw = int(day['dw_resonance'].sum())
    print(f'  分层: 日线 {n_daily} | 周线 {n_weekly} | 月线 {n_monthly} | '
          f'日周 {n_dw} | 候选池 {n_watch} | 周月 {n_wm} | 三周期 {n_entry}')

    label = {'any_entry': '三周期共振首日', 'fresh_daily': '金叉新鲜共振',
             'slowest_turn': '月线转多共振',
             'wm_first': '周月共振首日',
             'dw_first': '日周共振首日（周线确认）',
             'dw_watch': '日线先动候选新增',
             'phase_wm': '分段：日周→周月',
             'phase_entry': '分段：日周→三周期'}.get(mode, mode)
    layer = args.layer or 'entry'
    if layer == 'watch':
        label = '候选池（周线上抛/月线未上抛）'
    elif layer == 'wm':
        label = '周月共振（等日线）'
    elif layer == 'daily':
        label = '日线上抛'
    elif layer == 'weekly':
        label = '周线上抛'
    elif layer == 'monthly':
        label = '月线上抛'
    elif layer == 'dw':
        label = '日周共振（日线 ∧ 周线）'

    base_cols = ['stock_code', 'stock_name', 'date', 'close_price', 'dif', 'dea',
                 'hist', 'up_daily', 'up_weekly', 'up_monthly']

    if layer == 'all':
        S.ensure_dirs()
        for name, col, extra in (
                ('watch', 'watch', ['week_age_weeks']),
                ('wm', 'wm_resonance', ['week_age_weeks', 'w2m_weeks']),
                ('entry', 'entry', ['w2m_weeks'])):
            sub = day[day[col]].copy().rename(columns={'trade_date': 'date'})
            path = os.path.join(S.OUTPUT_DIR,
                                f'scan_{name}_{last_day:%Y%m%d}.csv')
            sub[base_cols + extra].to_csv(path, index=False,
                                          encoding='utf-8-sig')
            print(f'  [OK] {name}: {len(sub)} 只 → {path}')
        return 0

    if args.all_state:
        show = day[day['resonance']].copy()
        cols = base_cols + [entry_col]
    elif layer == 'watch':
        show = day[day['watch']].copy()
        cols = base_cols + ['week_age_weeks']
    elif layer == 'wm':
        show = day[day['wm_resonance']].copy()
        cols = base_cols + ['week_age_weeks', 'w2m_weeks']
    elif layer == 'daily':
        show = day[day['up_daily']].copy()
        cols = base_cols
    elif layer == 'weekly':
        show = day[day['up_weekly']].copy()
        cols = base_cols + ['week_age_weeks']
    elif layer == 'monthly':
        show = day[day['up_monthly']].copy()
        cols = base_cols + ['month_turn_date']
    elif layer == 'dw':
        show = day[day['dw_resonance']].copy()
        cols = base_cols + ['d2w_days', 'week_age_weeks']
    else:
        show = day[day[entry_col]].copy()
        cols = base_cols

    show = show.rename(columns={'trade_date': 'date'})
    show = show[cols].sort_values('stock_code')
    print(f'  {"共振中" if args.all_state else label}命中: {len(show)} 只')
    if len(show):
        print(show.to_string(index=False))

    mask_col = {'watch': 'watch', 'wm': 'wm_resonance',
                'daily': 'up_daily', 'weekly': 'up_weekly',
                'monthly': 'up_monthly', 'dw': 'dw_resonance'}.get(
        layer, 'entry' if args.all_state else entry_col)

    if args.recent:
        # 口径：最近 N 个**交易日**（含当日）。用共振面板自带的日线交易历
        # 取最近 N 个交易日作窗口起点，而不是自然日——周末/节假日不算天数。
        cal = pd.DatetimeIndex(
            pd.Series(res['trade_date'].unique()).sort_values().to_numpy())
        cal = cal[cal <= last_day]
        if len(cal) == 0:
            return 0
        lo = cal[-min(args.recent, len(cal))]
        ent = res[res[mask_col] & (res['trade_date'] >= lo)
                  & (res['trade_date'] <= last_day)]
        print(f'\n  最近 {min(args.recent, len(cal))} 个交易日的「{label}」信号'
              f'（{lo.date()} ~ {last_day.date()}）: {len(ent)} 条')
        if len(ent):
            e = ent.rename(columns={'trade_date': 'signal_date',
                                    'close_price': 'signal_close'})
            print(e[['signal_date', 'stock_code', 'stock_name', 'signal_close',
                     'dif', 'dea', 'hist']].sort_values(
                ['signal_date', 'stock_code']).to_string(index=False))

    S.ensure_dirs()
    suffix = '' if mode == 'any_entry' else f'_{mode}'
    if layer in ('watch', 'wm', 'daily', 'weekly', 'monthly', 'dw'):
        suffix = f'_{layer}'
    out = args.out or os.path.join(
        S.OUTPUT_DIR, f'scan{suffix}_{last_day.strftime("%Y%m%d")}.csv')
    day[day[mask_col]].to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n[OK] 已保存: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
