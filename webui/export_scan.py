# -*- coding: utf-8 -*-
"""生成 Web UI 的扫描快照（按股票池分文件）。

用法：
    python webui/export_scan.py                     # hs300，最新交易日
    python webui/export_scan.py --universe all      # 全市场（分块计算，约 3-5 分钟）
    python webui/export_scan.py --universe coal
    python webui/export_scan.py --universe all --date 2026-10-27

输出：
    webui/data/scan_<universe>.json       全量行（前端表格）
    webui/data/scan_<universe>.meta.json  轻量元信息（日期/计数/时间）
    webui/data/scan_full.json             hs300 兼容副本
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mlr import data as D, resonance as R, settings as S, universe as U  # noqa: E402

CACHE = ROOT / 'webui' / 'data'
CACHE.mkdir(parents=True, exist_ok=True)

KEEP = [
    'stock_code', 'stock_name', 'trade_date', 'close_price',
    'dif', 'dea', 'hist', 'hist_weekly', 'hist_monthly',
    'up_daily', 'up_weekly', 'up_monthly', 'monthly_ready',
    'up_daily_turn', 'up_weekly_turn', 'up_monthly_turn',
    'watch', 'wm_resonance', 'resonance', 'dw_resonance', 'dw_watch',
    'entry', 'wm_entry', 'dw_entry', 'watch_entry', 'dw_watch_entry',
    'phase_wm', 'phase_entry', 'entry_fresh', 'entry_slowest',
    'week_age_weeks', 'w2m_weeks', 'd2w_days', 'bars_since_cross',
    'week_turn_date', 'month_turn_date', 'day_turn_date',
    'wm_process_start', 'dw_process_start',
]

COUNT_COLS = (
    'up_daily', 'up_weekly', 'up_monthly',
    'up_daily_turn', 'up_weekly_turn', 'up_monthly_turn',
    'watch', 'watch_entry', 'wm_resonance', 'wm_entry',
    'resonance', 'entry', 'dw_resonance', 'dw_entry',
    'phase_wm', 'phase_entry',
)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()}
            for row in df.to_dict('records')]


def _codes_for(universe: str):
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'[股票池] hs300 {len(codes)} 只 (update={upd})')
        return codes
    if universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'[股票池] coal {len(codes)} 只 (update={upd})')
        return codes
    if universe == 'all':
        print('[股票池] 全市场（读本地 parquet 缓存）')
        return None
    raise SystemExit(f'未知 universe={universe}（hs300 / all / coal）')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe', default=None, choices=['hs300', 'all', 'coal'])
    ap.add_argument('--date', default=None)
    ap.add_argument('--chunk', type=int, default=500,
                    help='全市场分块大小（只影响峰值内存，不影响结果）')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    cfg = S.load_config()
    universe = args.universe or cfg.get('universe', {}).get('default', 'hs300')
    codes = _codes_for(universe)

    daily = D.load_daily(codes=codes)
    if daily.empty:
        raise SystemExit('日线缓存为空')
    last = pd.Timestamp(args.date) if args.date else daily['trade_date'].max()
    all_codes = sorted(daily['stock_code'].unique())
    print(f'[扫描] universe={universe}  date={last.date()}  '
          f'股票 {len(all_codes)} 只  分块 {args.chunk}')

    frames = []
    n_chunk = (len(all_codes) - 1) // args.chunk + 1
    for i in range(0, len(all_codes), args.chunk):
        ch = all_codes[i:i + args.chunk]
        sub = daily[daily['stock_code'].isin(ch)]
        if sub.empty:
            continue
        tfs = D.build_timeframes(sub, cfg)
        res = R.build_resonance(tfs, cfg)
        day = res[res['trade_date'] == last]
        if len(day):
            frames.append(day)
        print(f'  [块 {i // args.chunk + 1}/{n_chunk}] {len(ch)} 只 → '
              f'{len(day)} 行', flush=True)

    if not frames:
        raise SystemExit(f'{last.date()} 没有数据')
    day = pd.concat(frames, ignore_index=True)
    day['trade_date'] = pd.to_datetime(day['trade_date']).dt.strftime('%Y-%m-%d')
    for c in ('week_turn_date', 'month_turn_date', 'day_turn_date'):
        if c in day.columns:
            day[c] = pd.to_datetime(day[c]).dt.strftime('%Y-%m-%d')
    cols = [c for c in KEEP if c in day.columns]
    day = day[cols].sort_values('stock_code').reset_index(drop=True)

    counts = {c: int(day[c].sum()) for c in COUNT_COLS if c in day.columns}
    counts['all'] = int(len(day))
    payload = {
        'available': True,
        'source': 'export_scan.py',
        'date': str(last.date()),
        'universe': universe,
        'counts': counts,
        'mode': cfg.get('signal', {}).get('mode', 'phase_wm'),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'rows': records(day),
    }
    meta = {k: payload[k] for k in
            ('available', 'date', 'universe', 'counts', 'mode', 'generated_at')}
    meta['rows_count'] = len(day)

    out = Path(args.out) if args.out else CACHE / f'scan_{universe}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    with open(CACHE / f'scan_{universe}.meta.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    if universe == 'hs300':
        shutil.copyfile(out, CACHE / 'scan_full.json')

    print(f'[OK] {out}  {len(day)} 行  counts={counts}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
