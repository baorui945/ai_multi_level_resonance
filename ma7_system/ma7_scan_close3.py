# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_scan_close3.py —— 日线 MA7「上抛」扫描（v2：C 主 + B 兜底）
==============================================================================
规则（完整说明见 doc/日线扫描方案_v2.md）：

  段：MA7 的 5 日斜率 `(MA7[t]/MA7[t-5])**(1/5)-1`
      * 起点：斜率由负转正，连续 2 天为正；
      * 持续：斜率 > 0，允许 1 天回踩不中断；
      * 终点：连续 2 天斜率不为正，段终点为最后一个斜率 > 0 的交易日。
  信号：
      * C（主）：段内**首次收盘 > MA7** 的那天为起点，从起点起
        连续 3 根收盘都 > MA7 → 第 3 根确认；
      * B（兜底）：C 没出信号时，允许“前一天跌破、当天重新站上”，
        从重新站上日起连续 3 根收盘 > MA7 → 第 3 根确认；
        每段只取最早的一次，C 成功时不再找 B。
      * D1 → 候选 1/3；D2 → 候选 2/3；D3 收盘 → 确认 3/3；
        D4 开盘为最早可执行日。

用法::

    python ma7_scan_close3.py --codes 000400 --date 2026-05-13
    python ma7_scan_close3.py --universe hs300            # 最新交易日
    python ma7_scan_close3.py --universe all --recent 5
    python ma7_scan_close3.py --codes 000400 --recent 10
"""
import argparse
import json
import os
import sys
import time

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from ma7daily import settings as S, upthrow as UP
from mlr import data as D, universe as U


SHOW = {
    'stock_code': 'code',
    'stock_name': 'name',
    'seg_start_date': 'start',
    'trade_date': 'date',
    'close_price': 'close',
    'ma7': 'ma7',
    's5': 's5',
    'seg_day': 'day',
    'mode': 'mode',
    'confirm_date': 'confirm',
    'entry_date': 'entry',
    'seg_end_date': 'seg_end',
}
COLS = list(SHOW.keys())


def _pool(args, cfg):
    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(',') if c.strip()]
        return codes, ''
    universe = args.universe or cfg['scan'].get('universe', 'hs300')
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        return codes, upd
    if universe == 'coal':
        codes, upd = U.get_coal_codes()
        return codes, upd
    if universe == 'all':
        return None, ''
    raise SystemExit(f'未知 --universe {universe!r}（hs300 / coal / all）')


def _fmt(df):
    if not len(df):
        return '  （无）'
    d = df[COLS].rename(columns=SHOW).copy()
    d['start'] = pd.to_datetime(d['start']).dt.date
    d['date'] = pd.to_datetime(d['date']).dt.date
    for c in ('confirm', 'entry', 'seg_end'):
        d[c] = pd.to_datetime(d[c]).dt.date
    d['close'] = d['close'].round(2)
    d['ma7'] = d['ma7'].round(2)
    d['s5'] = d['s5'].round(3)
    return d.sort_values(['start', 'code']).to_string(index=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_scan_close3',
                                 description='日线 MA7 上抛扫描 v2（价格确认）')
    ap.add_argument('--date', default=None, help='交易日（默认最新）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票，如 000400')
    ap.add_argument('--universe', default=None, choices=['hs300', 'coal', 'all'])
    ap.add_argument('--recent', type=int, default=0,
                    help='附最近 N 个交易日的确认信号')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线')
    ap.add_argument('--no-sync', action='store_true', help='跳过上游增量同步')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    args = ap.parse_args(argv)

    cfg = UP.load_config()
    seg, sig = cfg['segment'], cfg['signal']
    codes, upd = _pool(args, cfg)

    print('=' * 82)
    print('  日线 MA7「上抛」扫描（C + B 兜底）')
    print(f"  段  : MA7 {seg['slope_window']} 日斜率>0，连续{seg['start_confirm']}天确认起止，"
          f"允许{seg['end_confirm'] - 1}天回踩")
    print(f"  信号: C=首个站上日起连续{sig['close_above_days']}根收盘>MA7；"
          f"B=C未出时允许{sig.get('fallback_min_dip', 1)}日回踩后重新站上")
    print('=' * 82)

    if codes is None:
        print('  股票池: 全市场')
    else:
        print(f"  股票池: {len(codes)} 只" + (f' (update_date={upd})' if upd else ''))

    if not args.no_sync:
        try:
            D.sync_latest_daily()
        except Exception as e:                                   # noqa: BLE001
            print(f'  [警告] 上游同步失败，使用本地缓存：{e}')

    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if not len(daily):
        raise SystemExit('无日线数据')
    frame = UP.detect_frame(daily, cfg)

    cal = pd.DatetimeIndex(sorted(frame['trade_date'].unique()))
    last_day = pd.Timestamp(args.date) if args.date else pd.Timestamp(frame['trade_date'].max())
    if last_day not in cal:
        raise SystemExit(f'{last_day.date()} 不是数据内交易日；最新为 {cal[-1].date()}')

    conf, cand2, cand1 = UP.scan_day(frame, last_day)
    act = UP.active(frame, last_day)
    day = frame[frame['trade_date'] == last_day]
    label = '股票' if codes is not None else '全市场'

    print(f'  交易日: {last_day.date()} | {label}: {len(day)} 只 | '
          f'段内: {len(act)} | 今日确认3/3: {len(conf)} | '
          f'候选2/3: {len(cand2)} | 候选1/3: {len(cand1)}')

    print(f'\n  【今日确认 3/3】（起点日=start，确认日={last_day.date()}，执行日=entry 开盘）:')
    print(_fmt(conf))
    if len(conf) and conf['entry_date'].isna().any():
        print('  [注] 最新交易日确认的信号，entry 为下一交易日（数据中尚未包含）')
    print(f'\n  【候选 2/3】（已站上 2 天，{last_day.date()} 收盘后确认段起点）:')
    print(_fmt(cand2))
    print(f'\n  【候选 1/3】（今天刚站上，需明天继续站上）:')
    print(_fmt(cand1))

    if args.recent:
        cal_upto = cal[cal <= last_day]
        lo = cal_upto[-min(args.recent, len(cal_upto))]
        recent = frame[frame['signal'] & (frame['trade_date'] >= lo)
                       & (frame['trade_date'] <= last_day)].copy()
        recent = recent.sort_values(['seg_start_date', 'stock_code'])
        print(f'\n  【最近 {min(args.recent, len(cal_upto))} 个交易日确认】'
              f'（{lo.date()} ~ {last_day.date()}）: {len(recent)} 条')
        print(_fmt(recent))

    # ---------- 输出 ----------
    S.ensure_dirs()
    buckets = []
    for name, df in (('confirmed', conf), ('candidate2', cand2),
                     ('candidate1', cand1)):
        if len(df):
            df = df.copy()
            df['bucket'] = name
            buckets.append(df)
    rows = pd.concat(buckets, ignore_index=True) if buckets else frame.iloc[0:0].copy()
    if not len(rows) and codes is None:
        rows = frame.iloc[0:0].copy()

    out = args.out or os.path.join(
        S.OUTPUT_DIR, f'ma7_scan_close3_{last_day.strftime("%Y%m%d")}.csv')
    keep = ['bucket'] + COLS
    if len(rows):
        rows[keep].sort_values(['bucket', 'seg_start_date', 'stock_code']).to_csv(
            out, index=False, encoding='utf-8-sig')
    else:
        pd.DataFrame(columns=keep).to_csv(out, index=False, encoding='utf-8-sig')

    meta = {
        'rule': 'ma7_close3',
        'config': cfg,
        'last_day': str(last_day.date()),
        'data_range': [str(pd.Timestamp(daily['trade_date'].min()).date()),
                       str(pd.Timestamp(daily['trade_date'].max()).date())],
        'universe_update_date': upd,
        'n_codes': int(daily['stock_code'].nunique()),
        'counts': {'active': int(len(act)), 'confirmed': int(len(conf)),
                   'candidate2': int(len(cand2)), 'candidate1': int(len(cand1))},
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    meta_path = os.path.splitext(out)[0] + '.meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f'\n[OK] CSV : {out}')
    print(f'[OK] Meta: {meta_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
