# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_scan.py —— 独立日线 MA7「上抛」扫描（盘后）
==============================================================================
与 scan.py（日/周/月 MACD 共振）完全独立：只读复用日线加载/股票池，
参数来自 config/ma7_daily.json，不修改任何既有逻辑。

用法::

    python ma7_scan.py                      # 最新交易日：当日新起点 + 上抛中
    python ma7_scan.py --date 2026-10-23
    python ma7_scan.py --recent 5           # 最近 5 个交易日的新起点
    python ma7_scan.py --all-state          # 列出当日全部「上抛中」
    python ma7_scan.py --universe all       # hs300 / coal / all
    python ma7_scan.py --refresh            # 强制重拉日线缓存
"""
import argparse
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

from ma7daily import settings as S, signal as SG
from mlr import data as D, universe as U


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_scan', description='日线 MA7 上抛扫描')
    ap.add_argument('--date', default=None, help='交易日（默认最新）')
    ap.add_argument('--recent', type=int, default=0,
                    help='附最近 N 个交易日（含当日，按日线交易历）的新起点')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all（默认 hs300）')
    ap.add_argument('--all-state', action='store_true',
                    help='列出当日全部上抛中（含非起点）')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线缓存')
    ap.add_argument('--preset', default=None,
                    help='叠加 config 中的预设口径（如 strict=降噪）')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    args = ap.parse_args(argv)

    try:
        cfg = S.load_config(preset=args.preset)
    except ValueError as e:
        raise SystemExit(str(e))
    m = cfg['ma7']
    st = cfg['state']
    print('=' * 78)
    print('  日线 MA7「上抛」—— 独立扫描'
          + (f"    [preset={cfg['preset']}]" if cfg.get('preset') else ''))
    print(f"  规则: close>MA{m['window']} & MA{m['window']}首根上拐"
          f" & 更高低点"
          + (f" & 突破{m['fresh_cross_days']}日内"
             f"({m.get('fresh_mode', 'since_turn')})"
             if m.get('fresh_cross_days') else ''))
    print("  确认: 下一交易日 MA7 继续上行；信号日记为第一根上拐日"
          if m.get('confirm_next_up', True) else "  确认: 当日")
    print(f"  退出: 连续{st['exit_below_days']}日收盘<MA7 或 "
          f"连续{st['exit_slope_days']}日斜率<"
          f"{float(st.get('exit_slope_threshold', 0.0)) * 100:.2f}%/日")
    print('=' * 78)
    for w in S.validate_config(cfg):
        print(f'  [参数] {w}')

    universe = args.universe or 'hs300'
    codes = None
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'  股票池: 沪深300成分股 {len(codes)} 只 (update_date={upd})')
    elif universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'  股票池: 动力煤板块 {len(codes)} 只 (update_date={upd})')
    elif universe != 'all':
        raise SystemExit(f'未知 --universe {universe!r}（可选 hs300 / coal / all）')

    # 先用上游最新日同步缓存（无新数据则不动）
    if not args.refresh:
        D.sync_latest_daily()
    daily = D.load_daily(codes=codes, refresh=args.refresh)
    frame = SG.detect_frame(daily, cfg)

    last_day = pd.Timestamp(args.date) if args.date else SG.latest_date(frame)
    cal = pd.DatetimeIndex(pd.Series(frame['trade_date'].unique())
                           .sort_values().to_numpy())
    if last_day not in cal:
        raise SystemExit(f'{last_day.date()} 不是数据内交易日；'
                         f'最新为 {cal[-1].date()}')

    day = frame[frame['trade_date'] == last_day].copy()
    cur = day[day['up']].copy()
    if m.get('confirm_next_up', True) and 'confirm_date' in frame:
        ent = frame[(frame['confirm_date'] == last_day)
                    & frame['entry_flag']].copy()
        ent_label = '今日确认信号（起点日）'
        cnt_label = '今日确认信号'
    else:
        ent = day[day['entry_flag']].copy()
        ent_label = '今日新起点'
        cnt_label = '今日新起点'
    print(f'  交易日: {last_day.date()}   全市场: {len(day)} 只')
    print(f'  上抛中: {len(cur)} 只 | {cnt_label}: {len(ent)} 只')

    cols = ['stock_code', 'stock_name', 'date', 'close_price', 'ma7',
            'slope_pct_day', 'dev_pct', 'seg_len']
    show = (cur if args.all_state else ent).rename(
        columns={'trade_date': 'date'})
    if len(show):
        print('\n' + ('  当日上抛中' if args.all_state else f'  {ent_label}') + ':')
        print(show[cols].sort_values('stock_code').to_string(index=False))
    else:
        print(f'  （当日无{"上抛中" if args.all_state else "新起点"}股票）')

    if args.recent:
        cal_upto = cal[cal <= last_day]
        lo = cal_upto[-min(args.recent, len(cal_upto))]
        recent = frame[frame['entry_flag']
                       & (frame['trade_date'] >= lo)
                       & (frame['trade_date'] <= last_day)].copy()
        print(f'\n  最近 {min(args.recent, len(cal_upto))} 个交易日的新起点'
              f'（{lo.date()} ~ {last_day.date()}）: {len(recent)} 条')
        if len(recent):
            r = recent.rename(columns={'trade_date': 'signal_date',
                                       'close_price': 'signal_close'})
            print(r[['signal_date', 'stock_code', 'stock_name', 'signal_close',
                     'ma7', 'slope_pct_day', 'dev_pct', 'seg_no']]
                  .sort_values(['signal_date', 'stock_code'])
                  .to_string(index=False))

    S.ensure_dirs()
    suffix = '_state' if args.all_state else ''
    out = args.out or os.path.join(
        S.OUTPUT_DIR, f'ma7_scan{suffix}_{last_day.strftime("%Y%m%d")}.csv')
    show.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n[OK] 已保存: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
