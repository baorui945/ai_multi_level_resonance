# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_scan_week.py —— 周线 MA7「上抛」扫描（v2 同一套规则，周期换成 W-FRI）
==============================================================================
规则与日线 v2（doc/日线扫描方案_v2.md）完全同构，只是把「日」换成「周」：

  段：MA7w 的 5 周斜率 > 0，连续 2 周确认起止，允许 1 周回踩；
      段内价格从未站上 MA7w 的斜率段跳过。
  C ：段内首次收盘 > MA7w 的那一周为 D1，连续 3 周收盘 > MA7w → D3 确认；
  B ：C 没出信号时，前一周跌破、当周重新站上，连续 3 周 → D3 确认；
      每段只取最早一个，C 成功则不再找 B。
  执行：D3 那一周收盘确认 → 下一周第一个交易日开盘（D4）。

只使用「已收盘周」（period_end <= 扫描日），盘中/未走完的当周不参与判定。

用法::

    python ma7_scan_week.py --universe hs300
    python ma7_scan_week.py --codes 000400 --recent 10
    python ma7_scan_week.py --codes 000400 --date 2026-10-30
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
except Exception:                                            # noqa: BLE001
    pass

from ma7daily import settings as S, upthrow as UP, resample as R     # noqa: E402
from mlr import data as D, universe as U                             # noqa: E402

CONFIG_NAME = 'ma7_week.json'

SHOW = {
    'stock_code': 'code',
    'stock_name': 'name',
    'seg_start_date': 'start',
    'trade_date': 'week',
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
        return [c.strip().zfill(6) for c in args.codes.split(',') if c.strip()], ''
    universe = args.universe or cfg['scan'].get('universe', 'hs300')
    if universe == 'hs300':
        return U.get_hs300_codes()
    if universe == 'coal':
        return U.get_coal_codes()
    if universe == 'all':
        return None, ''
    raise SystemExit(f'未知 --universe {universe!r}（hs300 / coal / all）')


def _fmt(df):
    if not len(df):
        return '  （无）'
    d = df[COLS].rename(columns=SHOW).copy()
    for c in ('start', 'week', 'confirm', 'entry', 'seg_end'):
        d[c] = pd.to_datetime(d[c]).dt.date
    d['close'] = d['close'].round(2)
    d['ma7'] = d['ma7'].round(2)
    d['s5'] = d['s5'].round(3)
    return d.sort_values(['start', 'code']).to_string(index=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_scan_week',
                                 description='周线 MA7 上抛扫描（v2 同构，周期 W-FRI）')
    ap.add_argument('--date', default=None, help='扫描日（默认最新交易日）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票，如 000400')
    ap.add_argument('--universe', default=None, choices=['hs300', 'coal', 'all'])
    ap.add_argument('--recent', type=int, default=0, help='附最近 N 周的确认')
    ap.add_argument('--rule', default=None, help="重采样规则（默认取 config 的 resample.rule / W-FRI）")
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线')
    ap.add_argument('--no-sync', action='store_true', help='跳过上游增量同步')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    ap.add_argument('--config', default=None, help='参数文件（默认 config/ma7_week.json）')
    args = ap.parse_args(argv)

    cfg_path = args.config or os.path.join(S.BASE_DIR, 'config', CONFIG_NAME)
    cfg = UP.load_config(cfg_path)
    rule = args.rule or cfg.get('resample', {}).get('rule') or 'W-FRI'
    seg, sig = cfg['segment'], cfg['signal']
    codes, upd = _pool(args, cfg)

    print('=' * 84)
    print('  周线 MA7「上抛」扫描（v2 规则 / W-FRI）')
    print(f"  段  : MA7w {cfg['ma7_window']} 周 / 斜率 {seg['slope_window']} 周 / "
          f"连续 {seg['start_confirm']} 周确认起止 / 允许 {seg['end_confirm'] - 1} 周回踩")
    print(f"  信号: C=段内首次站上起连续 {sig['close_above_days']} 周收盘>MA7w；"
          f"B=C未出时允许 {sig.get('fallback_min_dip', 1)} 周回踩后重新站上")
    print('  执行: D3 周收盘确认 → 下一周第一个交易日开盘（D4）')
    print('=' * 84)

    if codes is None:
        print('  股票池: 全市场')
    else:
        print(f"  股票池: {len(codes)} 只" + (f' (update_date={upd})' if upd else ''))

    if not args.no_sync:
        try:
            D.sync_latest_daily()
        except Exception as e:                               # noqa: BLE001
            print(f'  [警告] 上游同步失败，使用本地缓存：{e}')

    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if not len(daily):
        raise SystemExit('无日线数据')
    daily['trade_date'] = pd.to_datetime(daily['trade_date'])
    daily = daily.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)

    cal = pd.DatetimeIndex(sorted(daily['trade_date'].unique()))
    asof = pd.Timestamp(args.date) if args.date else pd.Timestamp(cal[-1])
    if asof not in cal:
        raise SystemExit(f'{asof.date()} 不是数据内交易日；最新为 {cal[-1].date()}')

    wk = R.to_weekly(daily[daily['trade_date'] <= asof], rule)
    wk = wk[wk['period_end'] <= asof].reset_index(drop=True)   # 只认已收盘周
    if not len(wk):
        raise SystemExit('没有已收盘周，无法扫描')

    frame = UP.detect_frame(wk, cfg)
    frame = frame.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)
    # D4 = 下一周第一个交易日开盘（不是下一周的周期标记日）
    frame['entry_date'] = (frame.groupby('stock_code')['week_first']
                           .shift(-1).where(frame['signal']))

    last_wk = pd.Timestamp(frame['trade_date'].max())
    weeks = pd.DatetimeIndex(sorted(frame['trade_date'].unique()))
    conf, cand2, cand1 = UP.scan_day(frame, last_wk)
    act = UP.active(frame, last_wk)
    day = frame[frame['trade_date'] == last_wk]
    label = '股票' if codes is not None else '全市场'

    print(f'  周线: {last_wk.date()}（该周最后交易日） | {label}: {len(day)} 只 | '
          f'段内: {len(act)} | 本周确认3/3: {len(conf)} | 候选2/3: {len(cand2)} | 候选1/3: {len(cand1)}')

    print(f'\n  【本周确认 3/3】（起点=start，确认周={last_wk.date()}，执行=entry 开盘）:')
    print(_fmt(conf))
    if len(conf) and conf['entry_date'].isna().any():
        print('  [注] 最新一周确认的信号，entry 为下一周首个交易日（数据中尚未包含）')
    print(f'\n  【候选 2/3】（已站上 2 周，下周确认）:')
    print(_fmt(cand2))
    print(f'\n  【候选 1/3】（本周刚站上）:')
    print(_fmt(cand1))

    if args.recent:
        upto = weeks[weeks <= last_wk]
        lo = upto[-min(args.recent, len(upto))]
        recent = frame[frame['signal'] & (frame['trade_date'] >= lo)
                       & (frame['trade_date'] <= last_wk)].copy()
        recent = recent.sort_values(['seg_start_date', 'stock_code'])
        print(f'\n  【最近 {min(args.recent, len(upto))} 周确认】（{lo.date()} ~ {last_wk.date()}）: '
              f'{len(recent)} 条')
        print(_fmt(recent))

    # ---------- 输出 ----------
    S.ensure_dirs()
    buckets = []
    for name, df in (('confirmed', conf), ('candidate2', cand2), ('candidate1', cand1)):
        if len(df):
            df = df.copy()
            df['bucket'] = name
            buckets.append(df)
    rows = pd.concat(buckets, ignore_index=True) if buckets else frame.iloc[0:0].copy()
    out = args.out or os.path.join(
        S.OUTPUT_DIR, f"ma7_week_scan_{last_wk.strftime('%Y%m%d')}.csv")
    keep = ['bucket'] + COLS
    if len(rows):
        rows[keep].sort_values(['bucket', 'seg_start_date', 'stock_code']).to_csv(
            out, index=False, encoding='utf-8-sig')
    else:
        pd.DataFrame(columns=keep).to_csv(out, index=False, encoding='utf-8-sig')

    meta = {
        'rule': 'ma7_week',
        'tf': 'W',
        'resample_rule': rule,
        'config': cfg,
        'last_week': str(last_wk.date()),
        'data_range': [str(cal[0].date()), str(cal[-1].date())],
        'universe_update_date': upd,
        'n_codes': int(frame['stock_code'].nunique()),
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
