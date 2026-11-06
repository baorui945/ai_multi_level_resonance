# -*- coding: utf-8 -*-
"""MLR Web UI 数据层：读取 output/ 下的扫描 CSV 与回测 xlsx，按需计算个股明细。"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'output'
CACHE = Path(__file__).resolve().parents[1] / 'data'
CACHE.mkdir(parents=True, exist_ok=True)

# 回测表头 → 前端字段
_PERF_MAP = {
    '持有期(交易日)': 'horizon', '主口径': 'main', '信号数': 'signals',
    '去重交易笔数': 'trades', '因数据不足剔除': 'skip',
    '因涨停剔除': 'skip_lu', '因并发上限剔除': 'skip_cap',
    '持有期内去重': 'dedup', '年化收益(%)': 'annual',
    'α(年化%)': 'alpha', 'α-t': 'alpha_t', '年化波动(%)': 'vol',
    '夏普': 'sharpe', '最大回撤(%)': 'mdd', 'Calmar': 'calmar',
    '资金利用率(%)': 'util', '逐笔净均(%)': 'avg_net',
    '逐笔净t': 'net_t', '逐笔净胜率(%)': 'win',
}
_TRADE_MAP = {
    '股票代码': 'code', '买入日': 'entry', '卖出日': 'exit',
    '持有交易日': 'hold', '买入价': 'entry_px', '卖出价': 'exit_px',
    '毛收益(%)': 'gross', '净收益(%)': 'net',
    '期间最大浮盈(%)': 'mfe', '期间最大浮亏(%)': 'mae',
    'T+1开盘口径(%)': 't1', '退出原因': 'reason',
}
_SIGNAL_MAP = {
    'stock_code': 'code', 'stock_name': 'name', 'signal_date': 'date',
    'signal_close': 'close', 'dif': 'dif', 'dea': 'dea', 'hist': 'hist',
    'bars_since_cross': 'bars_since_cross',
}


def _latest(pattern: str) -> Path | None:
    files = glob.glob(str(OUTPUT / pattern))
    return Path(max(files, key=os.path.getmtime)) if files else None


def _clean(v):
    """numpy / NaN → JSON 安全值。"""
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
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def records(df: pd.DataFrame) -> list[dict]:
    if df is None or not len(df):
        return []
    out = []
    for row in df.to_dict('records'):
        out.append({k: _clean(v) for k, v in row.items()})
    return out


# ═════════════════════════════════════════════════════════════════════════
# 扫描
# ═════════════════════════════════════════════════════════════════════════
def load_scan(universe: str = 'hs300') -> dict:
    universe = str(universe or 'hs300')
    snap = CACHE / f'scan_{universe}.json'
    if not snap.exists() and universe == 'hs300':
        snap = CACHE / 'scan_full.json'
    if snap.exists():
        with open(snap, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['file'] = snap.name
        data['universe'] = data.get('universe') or universe
        data['mtime'] = datetime.fromtimestamp(
            snap.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        return data
    if universe != 'hs300':
        return {'available': False, 'universe': universe,
                'error': f'尚未生成「{universe}」扫描快照，请点击「执行扫描」'}
    path = _latest('scan_*.csv')
    if path is None:
        return {'available': False, 'error': 'output/ 下没有 scan_*.csv'}
    df = pd.read_csv(path)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    for c in df.columns:
        if df[c].dtype == bool:
            continue
        if df[c].dtype == object:
            df[c] = df[c].replace({'True': True, 'False': False,
                                   'true': True, 'false': False})
    keep = [
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
    cols = [c for c in keep if c in df.columns]
    df = df[cols].copy()
    counts = {}
    for c in ('up_daily', 'up_weekly', 'up_monthly',
              'up_daily_turn', 'up_weekly_turn', 'up_monthly_turn',
              'watch', 'watch_entry', 'wm_resonance', 'wm_entry',
              'resonance', 'entry', 'dw_resonance', 'dw_entry',
              'phase_wm', 'phase_entry'):
        counts[c] = int(df[c].sum()) if c in df.columns else 0
    counts['all'] = int(len(df))
    date = str(df['trade_date'].iloc[0]) if len(df) else None
    return {
        'available': True, 'file': path.name, 'date': date,
        'universe': universe,
        'counts': counts, 'rows': records(df),
        'mtime': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
    }


# ═════════════════════════════════════════════════════════════════════════
# 回测
# ═════════════════════════════════════════════════════════════════════════
def load_backtest() -> dict:
    path = _latest('mlr_backtest_*.xlsx')
    if path is None:
        return {'available': False, 'error': 'output/ 下没有 mlr_backtest_*.xlsx'}
    sheets = pd.read_excel(path, sheet_name=None)

    perf_df = sheets.get('绩效总览')
    perf, bench = [], None
    if perf_df is not None:
        perf_df = perf_df.rename(columns=_PERF_MAP)
        if 'horizon' in perf_df.columns:
            is_bench = perf_df['horizon'].astype(str) == '基准'
            bench = records(perf_df[is_bench])[0] if is_bench.any() else None
            perf = records(perf_df[~is_bench])

    annual = {}
    adf = sheets.get('年度收益')
    if adf is not None:
        adf = adf.rename(columns={'持有期': 'horizon'})
        for r in records(adf):
            key = str(r.pop('horizon'))
            annual[key] = r

    monthly = {}
    mdf = sheets.get('月度收益')
    if mdf is not None:
        mdf = mdf.rename(columns={'持有期': 'horizon'})
        for r in records(mdf):
            key = str(r.pop('horizon'))
            monthly[key] = r

    nav = []
    ndf = sheets.get('净值曲线')
    if ndf is not None:
        ndf = ndf.rename(columns={'trade_date': 'date'})
        ndf['date'] = pd.to_datetime(ndf['date']).dt.strftime('%Y-%m-%d')
        nav = records(ndf)

    trades = {}
    for sh in sheets:
        m = re.fullmatch(r'逐笔明细H(\d+)', str(sh))
        if m:
            d = sheets[sh].rename(columns=_TRADE_MAP)
            trades[m.group(1)] = records(d)

    signals = []
    sdf = sheets.get('共振信号')
    if sdf is not None:
        sdf = sdf.rename(columns=_SIGNAL_MAP)
        if 'date' in sdf.columns:
            sdf['date'] = pd.to_datetime(sdf['date']).dt.strftime('%Y-%m-%d')
        signals = records(sdf)

    horizons = [p['horizon'] for p in perf if p.get('horizon') is not None]
    main = next((p['horizon'] for p in perf if p.get('main') == '★'),
                horizons[0] if horizons else None)
    return {
        'available': True, 'file': path.name, 'perf': perf, 'benchmark': bench,
        'annual': annual, 'monthly': monthly, 'nav': nav,
        'trades': trades, 'signals': signals, 'horizons': horizons,
        'main': main,
        'mtime': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
    }


# ═════════════════════════════════════════════════════════════════════════
# 个股明细（按需计算，进程内缓存）
# ═════════════════════════════════════════════════════════════════════════
_STOCK_CACHE: dict[str, dict] = {}


def _read_daily(code: str) -> pd.DataFrame:
    px_path = ROOT / 'data' / '_cache' / 'daily_px.parquet'
    try:
        df = pd.read_parquet(px_path, filters=[('stock_code', '==', code)])
    except Exception:                                        # noqa: BLE001
        df = pd.read_parquet(px_path)
        df = df[df['stock_code'] == code]
    return df.sort_values('trade_date').reset_index(drop=True)


def stock_detail(code: str) -> dict:
    code = str(code).zfill(6)
    if code in _STOCK_CACHE:
        return _STOCK_CACHE[code]

    from mlr import data as D, settings as S, resonance as R

    daily = _read_daily(code)
    if daily.empty:
        return {'available': False, 'error': f'本地缓存没有 {code}'}

    cfg = S.load_config()
    tfs = D.build_timeframes(daily, cfg)
    res = R.build_resonance(tfs, cfg)

    # 日线
    d = res[['trade_date', 'open_price', 'high_price', 'low_price',
             'close_price', 'volume', 'amount', 'dif', 'dea', 'hist',
             'up_daily', 'bars_since_cross', 'd2w_days',
             'week_age_weeks', 'monthly_ready']].copy()
    d['trade_date'] = pd.to_datetime(d['trade_date']).dt.strftime('%Y-%m-%d')
    d = d.rename(columns={'trade_date': 'date', 'open_price': 'open',
                          'high_price': 'high', 'low_price': 'low',
                          'close_price': 'close', 'up_daily': 'up'})

    # 周线 / 月线（detect_frame 带 dif/dea/hist）
    w = R.detect_frame(tfs['weekly'], 'weekly', cfg)
    w = w[w.stock_code == code].sort_values('trade_date')
    w = w[['trade_date', 'period_end', 'close_price', 'dif', 'dea', 'hist',
           'upthrow']].rename(columns={
               'trade_date': 'bar_date', 'period_end': 'period_end',
               'close_price': 'close', 'upthrow': 'up'})
    w['bar_date'] = pd.to_datetime(w['bar_date']).dt.strftime('%Y-%m-%d')
    w['period_end'] = pd.to_datetime(w['period_end']).dt.strftime('%Y-%m-%d')

    m = R.detect_frame(tfs['monthly'], 'monthly', cfg)
    m = m[m.stock_code == code].sort_values('trade_date')
    m = m[['trade_date', 'period_end', 'close_price', 'dif', 'dea', 'hist',
           'upthrow']].rename(columns={
               'trade_date': 'bar_date', 'period_end': 'period_end',
               'close_price': 'close', 'upthrow': 'up'})
    m['bar_date'] = pd.to_datetime(m['bar_date']).dt.strftime('%Y-%m-%d')
    m['period_end'] = pd.to_datetime(m['period_end']).dt.strftime('%Y-%m-%d')

    # 只保留已收盘周期（closed 口径），避免把进行中的周/月K当成“最新状态”
    last_day = str(pd.Timestamp(daily['trade_date'].max()).date())
    w = w[w['period_end'] <= last_day].reset_index(drop=True)
    m = m[m['period_end'] <= last_day].reset_index(drop=True)

    # 信号
    flags = ['dw_entry', 'wm_entry', 'entry', 'phase_wm', 'phase_entry',
             'dw_process_start']
    sig = res[res[flags].any(axis=1)][
        ['trade_date', 'close_price', 'dif', 'dea', 'hist',
         'd2w_days', 'week_age_weeks', 'monthly_ready'] + flags].copy()
    sig['trade_date'] = pd.to_datetime(sig['trade_date']).dt.strftime('%Y-%m-%d')
    sig = sig.rename(columns={'trade_date': 'date', 'close_price': 'close'})

    out = {
        'available': True, 'code': code,
        'name': str(daily['stock_name'].iloc[-1]) if 'stock_name' in daily else '',
        'first': str(pd.Timestamp(daily['trade_date'].min()).date()),
        'last': str(pd.Timestamp(daily['trade_date'].max()).date()),
        'daily': records(d), 'weekly': records(w), 'monthly': records(m),
        'signals': records(sig),
    }
    _STOCK_CACHE[code] = out
    return out


# ═════════════════════════════════════════════════════════════════════════
# 运行记录
# ═════════════════════════════════════════════════════════════════════════
def list_runs() -> list[dict]:
    out = []
    for p in sorted(OUTPUT.rglob('*'), key=os.path.getmtime, reverse=True):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ('.xlsx', '.csv', '.txt', '.png', '.json'):
            continue
        out.append({
            'name': p.name,
            'rel': str(p.relative_to(OUTPUT)).replace('\\', '/'),
            'size': p.stat().st_size,
            'mtime': datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'kind': p.suffix.lower().lstrip('.'),
        })
        if len(out) >= 200:
            break
    return out


def _scan_meta(universe: str) -> dict | None:
    """读轻量 meta（不存在时回落到全量 json 的头字段）。"""
    p = CACHE / f'scan_{universe}.meta.json'
    if p.exists():
        with open(p, 'r', encoding='utf-8') as f:
            m = json.load(f)
        m['file'] = f'scan_{universe}.json'
        m['mtime'] = datetime.fromtimestamp(
            p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        return m
    full = CACHE / f'scan_{universe}.json'
    if not full.exists() and universe == 'hs300':
        full = CACHE / 'scan_full.json'
    if full.exists():
        with open(full, 'r', encoding='utf-8') as f:
            d = json.load(f)
        m = {k: d.get(k) for k in ('available', 'date', 'universe',
                                   'counts', 'mode', 'generated_at')}
        m['file'] = full.name
        m['mtime'] = datetime.fromtimestamp(
            full.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        return m
    return None


def meta() -> dict:
    universes = {}
    for u in ('hs300', 'all', 'coal'):
        m = _scan_meta(u)
        if m:
            universes[u] = m
    default = 'hs300' if 'hs300' in universes else next(iter(universes), 'hs300')
    scan = universes.get(default) or {
        'available': False, 'universe': default,
        'error': '尚未生成扫描快照，请点击「执行扫描」',
    }
    bt = load_backtest()
    return {
        'project': 'MLR · 多周期共振',
        'scan': scan,
        'scan_universes': universes,
        'backtest': {k: bt.get(k) for k in ('available', 'file', 'main',
                                            'horizons', 'mtime', 'error')},
        'as_of': scan.get('date'),
    }
