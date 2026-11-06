# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_scan_res.py —— 日线 × 周线共振扫描（日线开仓必须在周线上抛段内）
==============================================================================
两套 v2 规则（日线 doc/日线扫描方案_v2.md；周线与它同构，周期换 W-FRI）：

  日线：D3 收盘确认 → D4 开盘执行；
  周线：s5w 连续 2 周 > 0 启动，连续 2 周 <= 0 结束；
        段区间 = [段起点, 最后一个 s5w > 0 的周]；
  共振：日线【确认日 D3】必须落在周线上抛段内；开仓日 D4 已知时也要求落在段内。
        段仍在进行中 → 上界视为无穷。
        不满足 → 不开仓。

用法::

    python ma7_scan_res.py --universe hs300
    python ma7_scan_res.py --codes 600188 --date 2026-09-01 --recent 10
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

from ma7daily import settings as S, upthrow as UP, resample as R, resonance as RES  # noqa: E402
from mlr import data as D, universe as U                                              # noqa: E402

DAY_CFG = 'ma7_close3.json'
WEEK_CFG = 'ma7_week.json'
COLS = ['bucket', 'stock_code', 'stock_name', 'signal_date', 'trade_date',
        'close_price', 'ma7', 's5', 'seg_day', 'mode', 'confirm_date',
        'entry_date', 'wk_seg', 'wk_start', 'wk_end', 'wk_in', 'action', 'note']


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


def _d(x):
    if x is None or (isinstance(x, float) and not pd.notna(x)):
        return ''
    ts = pd.Timestamp(x)
    return '' if pd.isna(ts) else ts.strftime('%Y-%m-%d')


def _fmt(df, cols=('stock_code', 'stock_name', 'signal_date', 'mode', 'confirm_date',
                   'entry_date', 'wk_seg', 'wk_in', 'action')):
    if not len(df):
        return '  （无）'
    d = df[list(cols)].copy()
    d.columns = ['code', 'name', 'D1', 'mode', 'D3', 'D4', '周线段', '段内', '动作']
    for c in ('D1', 'D3', 'D4'):
        d[c] = d[c].map(_d)
    return d.to_string(index=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_scan_res',
                                 description='日线×周线共振扫描（日线开仓必须在周线上抛段内）')
    ap.add_argument('--date', default=None, help='交易日（默认最新）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票')
    ap.add_argument('--universe', default=None, choices=['hs300', 'coal', 'all'])
    ap.add_argument('--recent', type=int, default=0, help='附最近 N 个交易日确认')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--no-sync', action='store_true')
    ap.add_argument('--out', default=None)
    ap.add_argument('--config', default=None, help='日线参数（默认 config/ma7_close3.json）')
    ap.add_argument('--week-config', default=None, help='周线参数（默认 config/ma7_week.json）')
    args = ap.parse_args(argv)

    dcfg = UP.load_config(args.config or os.path.join(S.BASE_DIR, 'config', DAY_CFG))
    wcfg = UP.load_config(args.week_config or os.path.join(S.BASE_DIR, 'config', WEEK_CFG))
    codes, upd = _pool(args, dcfg)

    print('=' * 88)
    print('  日线 × 周线共振扫描（日线开仓必须在周线上抛段内）')
    print(f"  日线: MA7 {dcfg['ma7_window']} / 斜率 {dcfg['segment']['slope_window']} 日 / "
          f"连续 {dcfg['signal']['close_above_days']} 根站上 / D3 确认 D4 开盘")
    print(f"  周线: {wcfg.get('resample', {}).get('rule', 'W-FRI')} / MA7w {wcfg['ma7_window']} 周 / "
          f"斜率 {wcfg['segment']['slope_window']} 周 / 连续 2 周确认起止")
    print('  共振: D3 ∈ 周线上抛段；D4 已知时也必须在段内；进行中的段不设上界')
    print('=' * 88)

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

    hist = daily[daily['trade_date'] <= asof].reset_index(drop=True)
    up = UP.detect_frame(hist, dcfg)

    wk = R.to_weekly(hist, wcfg.get('resample', {}).get('rule', 'W-FRI'))
    wk = wk[wk['period_end'] <= asof].reset_index(drop=True)
    wf = UP.detect_frame(wk, wcfg)
    segs_by = {c: g for c, g in RES.weekly_segments(wf).groupby('stock_code', sort=False)}

    nxt = (daily.loc[daily['trade_date'] > asof, ['stock_code', 'trade_date']]
           .groupby('stock_code')['trade_date'].min().to_dict())

    conf, cand2, cand1 = UP.scan_day(up, asof)
    act = UP.active(up, asof)

    rows = []
    for b, df_ in (('confirmed', conf), ('candidate2', cand2), ('candidate1', cand1)):
        for r in df_.itertuples(index=False):
            segs = segs_by.get(r.stock_code)
            d3 = r.confirm_date if pd.notna(r.confirm_date) else r.trade_date
            s3 = RES.segment_at(segs, d3)
            entry = r.entry_date if pd.notna(r.entry_date) else nxt.get(r.stock_code)
            s4 = RES.segment_at(segs, entry) if entry is not None else None
            wk_in = (s3 is not None)
            if b != 'confirmed':
                action, note = '观察', ('周线段内' if wk_in else '周线段外')
            elif not wk_in:
                action, note = '不开仓', '周线段外'
            elif entry is not None and s4 is None:
                action, note = '不开仓', '开仓日已出段'
            elif entry is None:
                action, note = '开仓', '次一交易日开盘'
            else:
                action, note = '开仓', '次一交易日开盘'
            seg_txt = (f"{_d(s3.start)}~{'进行中' if s3.ongoing else _d(s3.end)}"
                       if s3 is not None else '')
            rows.append({
                'bucket': b, 'stock_code': r.stock_code, 'stock_name': r.stock_name,
                'signal_date': _d(r.signal_date if pd.notna(r.signal_date) else r.seg_start_date),
                'trade_date': _d(r.trade_date), 'close_price': round(float(r.close_price), 2),
                'ma7': round(float(r.ma7), 2), 's5': round(float(r.s5), 3),
                'seg_day': int(r.seg_day), 'mode': r.mode,
                'confirm_date': _d(r.confirm_date), 'entry_date': _d(entry),
                'wk_seg': str(int(s3.seg_id)) if s3 is not None else '',
                'wk_start': _d(s3.start) if s3 is not None else '',
                'wk_end': ('进行中' if s3.ongoing else _d(s3.end)) if s3 is not None else '',
                'wk_in': '是' if wk_in else '否', 'action': action, 'note': note,
            })
    out = pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame(columns=COLS)

    c = out[out['bucket'] == 'confirmed']
    n_in = int((c['wk_in'] == '是').sum()) if len(c) else 0
    n_ok = int((c['action'] == '开仓').sum()) if len(c) else 0
    print(f"\n  交易日: {asof.date()} | {up['stock_code'].nunique()} 只 | 段内 {len(act)} | "
          f"今日确认 {len(conf)} | 其中周线段内 {n_in} | 可开仓 {n_ok} | "
          f"候选2/3 {len(cand2)} | 候选1/3 {len(cand1)}")

    print('\n  【今日确认 + 周线段内（可开仓）】:')
    print(_fmt(c[c['wk_in'] == '是']) if len(c) else '  （无）')
    print('\n  【今日确认但在周线段外（不开仓）】:')
    print(_fmt(c[c['wk_in'] == '否']) if len(c) else '  （无）')
    print('\n  【候选 2/3】:')
    print(_fmt(out[out['bucket'] == 'candidate2']))
    print('\n  【候选 1/3】:')
    print(_fmt(out[out['bucket'] == 'candidate1']))

    if args.recent:
        days = cal[cal <= asof]
        lo = days[-min(args.recent, len(days))]
        sub = up[up['signal'] & (up['trade_date'] >= lo) & (up['trade_date'] <= asof)]
        print(f'\n  【最近 {min(args.recent, len(days))} 个交易日确认】（{lo.date()} ~ {asof.date()}，'
              f'{len(sub)} 条）:')
        rrows = []
        for r in sub.sort_values(['trade_date', 'stock_code']).itertuples(index=False):
            segs = segs_by.get(r.stock_code)
            s3 = RES.segment_at(segs, r.confirm_date)
            rrows.append([r.stock_code, r.stock_name, _d(r.trade_date), r.mode,
                          '是' if s3 is not None else '否',
                          _d(r.entry_date) or _d(nxt.get(r.stock_code))])
        if rrows:
            print(pd.DataFrame(rrows, columns=['code', 'name', 'D3', 'mode', '段内', 'D4'])
                  .to_string(index=False))
        else:
            print('  （无）')

    S.ensure_dirs()
    out_path = args.out or os.path.join(
        S.OUTPUT_DIR, f"ma7_res_scan_{asof.strftime('%Y%m%d')}.csv")
    out.to_csv(out_path, index=False, encoding='utf-8-sig')

    meta = {
        'rule': 'ma7_daily_x_weekly_segment',
        'day_config': dcfg, 'week_config': wcfg,
        'last_day': str(asof.date()),
        'data_range': [str(cal[0].date()), str(cal[-1].date())],
        'universe_update_date': upd,
        'n_codes': int(up['stock_code'].nunique()),
        'counts': {'active': int(len(act)), 'confirmed': int(len(conf)),
                   'confirmed_in_segment': n_in, 'actionable': n_ok,
                   'candidate2': int(len(cand2)), 'candidate1': int(len(cand1))},
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    meta_path = os.path.splitext(out_path)[0] + '.meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f'\n[OK] CSV : {out_path}')
    print(f'[OK] Meta: {meta_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
